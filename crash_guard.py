"""Capture des erreurs non prévues de l'App comptoir.

L'exécutable est construit sans console (``console=False``) : une exception non
rattrapée — dans un bouton, une minuterie, un thread — partait vers une sortie
d'erreur inexistante et ne laissait AUCUNE trace. Ce module branche tous les
chemins par lesquels une erreur peut s'échapper sur le journal applicatif :

- ``sys.excepthook`` : thread principal ET slots Qt (PySide6 y fait passer les
  exceptions levées dans un slot, une minuterie ou ``QThread.run``) ;
- ``threading.excepthook`` : threads Python (ex. raccourcis clavier globaux) ;
- ``sys.unraisablehook`` : erreurs dans les destructeurs (``__del__``) ;
- gestionnaire de messages Qt : avertissements/erreurs émis par Qt lui-même ;
- ``faulthandler`` : plantage brutal (erreur native) écrit dans ``crash.log``
  et recopié dans le journal au démarrage suivant.

Une même erreur qui se répète (ex. dans une minuterie d'une seconde) n'est
écrite en entier que quelques fois, puis résumée, pour ne pas noyer le journal.
L'utilisateur peut être prévenu discrètement (une fois par erreur distincte)
via ``set_notifier``.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from PySide6.QtCore import QObject, QtMsgType, Signal, Slot, qInstallMessageHandler

CRASH_FILENAME = "crash.log"
LOGGER_NAME = "appcomptoir.crash"

#: Nombre d'occurrences d'une même erreur écrites avec leur trace complète.
FULL_REPORTS_PER_SIGNATURE = 3
#: Au-delà, une ligne de rappel toutes les N occurrences.
REPEAT_SUMMARY_EVERY = 50
#: Taille maximale recopiée depuis le crash.log de la session précédente.
PREVIOUS_CRASH_MAX_CHARS = 20_000

#: Message montré à l'utilisateur (bandeau discret), une fois par erreur distincte.
USER_NOTICE = "Une erreur interne s'est produite ; elle a été enregistrée dans le journal."

FULL = "full"
SUMMARY = "summary"
SILENT = "silent"


def exception_signature(exc_type, tb) -> str:
    """Identité d'une erreur : son type et l'endroit le plus profond où elle a
    été levée. Deux occurrences au même endroit sont « la même erreur »."""
    name = getattr(exc_type, "__qualname__", None) or str(exc_type)
    last = None
    while tb is not None:
        last = tb
        tb = tb.tb_next
    if last is None:
        return name
    code = last.tb_frame.f_code
    return f"{name}@{os.path.basename(code.co_filename)}:{last.tb_lineno}"


class RepeatTracker:
    """Compte les occurrences par signature et décide comment les journaliser
    (pur, sans Qt ni journal : testé isolément)."""

    def __init__(self, full_reports=FULL_REPORTS_PER_SIGNATURE,
                 summary_every=REPEAT_SUMMARY_EVERY):
        self.full_reports = full_reports
        self.summary_every = summary_every
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def record(self, signature: str) -> tuple[str, int]:
        """Enregistre une occurrence ; renvoie (décision, nombre d'occurrences)."""
        with self._lock:
            count = self._counts.get(signature, 0) + 1
            self._counts[signature] = count
        if count <= self.full_reports:
            return FULL, count
        if count % self.summary_every == 0:
            return SUMMARY, count
        return SILENT, count


def report_previous_crash(path: Path, logger: logging.Logger) -> bool:
    """Recopie dans le journal la trace native laissée par la session
    précédente (fichier non vide), puis vide le fichier. Renvoie True si une
    trace a été trouvée."""
    try:
        if not path.exists() or path.stat().st_size == 0:
            return False
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as e:
        logger.warning("Lecture de %s impossible : %s", path.name, e)
        return False
    if not text:
        return False
    if len(text) > PREVIOUS_CRASH_MAX_CHARS:
        text = text[:PREVIOUS_CRASH_MAX_CHARS] + "\n[… tronqué]"
    logger.error("Plantage brutal lors de la session précédente (trace native) :\n%s", text)
    try:
        path.write_text("", encoding="utf-8")
    except OSError:
        pass
    return True


class _NoticeBridge(QObject):
    """Ramène la notification utilisateur dans le thread graphique : une erreur
    survenue dans un thread de fond émet le signal, la livraison est différée
    au thread principal (connexion en file Qt)."""

    notice = Signal(str)

    def __init__(self):
        super().__init__()
        self.callback: Callable[[str], None] | None = None
        self.notice.connect(self._deliver)

    @Slot(str)
    def _deliver(self, message):
        callback = self.callback
        if callback is None:
            return
        try:
            callback(message)
        except Exception:  # prévenir ne doit jamais relancer d'erreur
            logging.getLogger(LOGGER_NAME).debug("Notification d'erreur impossible", exc_info=True)


_QT_LEVELS = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}


class CrashGuard:
    """Installe (et retire) les points de capture des erreurs non prévues."""

    def __init__(self, logger: logging.Logger | None = None, log_dir: Path | None = None,
                 enable_faulthandler: bool = True):
        self.logger = logger or logging.getLogger(LOGGER_NAME)
        self.log_dir = log_dir
        self.enable_faulthandler = enable_faulthandler
        self.tracker = RepeatTracker()
        self._qt_tracker = RepeatTracker()
        self._notified: set[str] = set()
        self._bridge: _NoticeBridge | None = None
        self._crash_file: TextIO | None = None
        self._installed = False
        self._previous: dict[str, object] = {}

    # --- installation -----------------------------------------------------

    def install(self) -> CrashGuard:
        if self._installed:
            return self
        self._previous = {
            "excepthook": sys.excepthook,
            "threading": threading.excepthook,
            "unraisable": sys.unraisablehook,
        }
        self._bridge = _NoticeBridge()
        sys.excepthook = self._excepthook
        threading.excepthook = self._threading_excepthook
        sys.unraisablehook = self._unraisablehook
        self._previous["qt"] = qInstallMessageHandler(self._qt_message_handler)
        if self.enable_faulthandler:
            self._start_faulthandler()
        self._installed = True
        return self

    def uninstall(self) -> None:
        if not self._installed:
            return
        sys.excepthook = self._previous["excepthook"]  # type: ignore[assignment]
        threading.excepthook = self._previous["threading"]  # type: ignore[assignment]
        sys.unraisablehook = self._previous["unraisable"]  # type: ignore[assignment]
        qInstallMessageHandler(self._previous.get("qt"))
        if self._crash_file is not None:
            faulthandler.disable()
            try:
                self._crash_file.close()
            except OSError:
                pass
            self._crash_file = None
        self._installed = False

    def set_notifier(self, callback: Callable[[str], None] | None) -> None:
        """Fonction appelée (dans le thread graphique) pour prévenir
        l'utilisateur, au plus une fois par erreur distincte."""
        if self._bridge is not None:
            self._bridge.callback = callback

    def _start_faulthandler(self) -> None:
        if self.log_dir is None:
            return
        path = Path(self.log_dir) / CRASH_FILENAME
        report_previous_crash(path, self.logger)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Fichier gardé ouvert : faulthandler écrit sur son descripteur au
            # moment du plantage, sans pouvoir rien ouvrir.
            self._crash_file = open(path, "w", encoding="utf-8")
            faulthandler.enable(file=self._crash_file, all_threads=True)
        except OSError as e:
            self._crash_file = None
            self.logger.warning("Capture des plantages natifs indisponible : %s", e)

    # --- points de capture -------------------------------------------------

    def _excepthook(self, exc_type, exc_value, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, tb)
            return
        self._report(exc_type, exc_value, tb, "Erreur non prévue")

    def _threading_excepthook(self, args):
        if args.exc_type is SystemExit:
            return
        name = getattr(args.thread, "name", "?")
        self._report(args.exc_type, args.exc_value, args.exc_traceback,
                     f"Erreur non prévue dans le thread « {name} »")

    def _unraisablehook(self, unraisable):
        try:
            where = unraisable.err_msg or "Exception ignorée"
            exc_type = unraisable.exc_type
            signature = exception_signature(exc_type, unraisable.exc_traceback)
            decision, count = self.tracker.record("unraisable:" + signature)
            if decision == FULL:
                self.logger.warning(
                    "%s (%r)", where, unraisable.object,
                    exc_info=(exc_type, unraisable.exc_value, unraisable.exc_traceback))
            elif decision == SUMMARY:
                self.logger.warning("%s : déjà survenue %d fois (%s)", where, count, signature)
        except Exception:  # le point de capture ne doit jamais lever
            pass

    def _report(self, exc_type, exc_value, tb, title):
        try:
            signature = exception_signature(exc_type, tb)
            decision, count = self.tracker.record(signature)
            if decision == FULL:
                suffix = f" (occurrence {count})" if count > 1 else ""
                self.logger.error("%s%s", title, suffix, exc_info=(exc_type, exc_value, tb))
            elif decision == SUMMARY:
                self.logger.error("%s : la même erreur est survenue %d fois (%s)",
                                  title, count, signature)
            if signature not in self._notified:
                self._notified.add(signature)
                if self._bridge is not None:
                    self._bridge.notice.emit(USER_NOTICE)
        except Exception:  # dernier recours : ne jamais lever ici
            if sys.stderr is not None:
                sys.__excepthook__(exc_type, exc_value, tb)

    def _qt_message_handler(self, msg_type, context, message):
        try:
            level = _QT_LEVELS.get(msg_type, logging.WARNING)
            if not self.logger.isEnabledFor(level):
                return
            decision, count = self._qt_tracker.record(message)
            if decision == FULL:
                self.logger.log(level, "Qt : %s", message)
            elif decision == SUMMARY:
                self.logger.log(level, "Qt : %s (répété %d fois)", message, count)
        except Exception:  # appelé par Qt : ne jamais lever
            pass


_current: CrashGuard | None = None


def install(log_dir: Path | None = None, logger: logging.Logger | None = None) -> CrashGuard:
    """Installe la capture pour tout le processus (une seule fois)."""
    global _current
    if _current is None:
        _current = CrashGuard(logger=logger, log_dir=log_dir).install()
    return _current


def current() -> CrashGuard | None:
    return _current
