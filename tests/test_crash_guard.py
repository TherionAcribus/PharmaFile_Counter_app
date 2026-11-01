"""Tests de la capture des erreurs non prévues (crash_guard).

L'exécutable n'a pas de console : sans ces points de capture, une exception
levée dans un bouton, une minuterie ou un thread ne laissait aucune trace.
"""

import logging
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import crash_guard
from crash_guard import (
    FULL, SILENT, SUMMARY, CrashGuard, RepeatTracker, exception_signature,
    report_previous_crash,
)
from my_logger import RedactingFilter


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def capture():
    logger = logging.getLogger("test.crash_guard")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _ListHandler()
    logger.addHandler(handler)
    yield logger, handler.records
    logger.removeHandler(handler)


@pytest.fixture
def guard(capture, tmp_path):
    logger, _records = capture
    g = CrashGuard(logger=logger, log_dir=tmp_path, enable_faulthandler=False).install()
    yield g
    g.uninstall()


def _process_events():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def _raise_at_fixed_place():
    raise ValueError("boum")


def _exc_info():
    try:
        _raise_at_fixed_place()
    except ValueError:
        return sys.exc_info()


# --- noyau pur ---------------------------------------------------------------

def test_signature_identifies_type_and_innermost_line():
    t, _v, tb = _exc_info()
    sig = exception_signature(t, tb)
    assert sig.startswith("ValueError@test_crash_guard.py:")
    # Même endroit = même signature, quel que soit le message.
    t2, _v2, tb2 = _exc_info()
    assert exception_signature(t2, tb2) == sig


def test_signature_without_traceback():
    assert exception_signature(KeyError, None) == "KeyError"


def test_repeat_tracker_full_then_silent_then_summary():
    tracker = RepeatTracker(full_reports=3, summary_every=10)
    decisions = [tracker.record("x")[0] for _ in range(20)]
    assert decisions[:3] == [FULL, FULL, FULL]
    assert decisions[9] == SUMMARY and decisions[19] == SUMMARY
    assert decisions[3:9] == [SILENT] * 6
    # Une autre erreur a son propre compteur.
    assert tracker.record("y") == (FULL, 1)


def test_previous_crash_is_reported_then_cleared(capture, tmp_path):
    logger, records = capture
    path = tmp_path / crash_guard.CRASH_FILENAME
    path.write_text("Windows fatal exception: access violation\n", encoding="utf-8")
    assert report_previous_crash(path, logger) is True
    assert "access violation" in records[-1].getMessage()
    assert records[-1].levelno == logging.ERROR
    assert path.read_text(encoding="utf-8") == ""
    # Fichier vide ou absent : rien à signaler.
    assert report_previous_crash(path, logger) is False
    assert report_previous_crash(tmp_path / "absent.log", logger) is False


# --- points de capture --------------------------------------------------------

def test_main_thread_exception_is_logged_with_traceback(guard, capture):
    _logger, records = capture
    sys.excepthook(*_exc_info())
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info[0] is ValueError


def test_qt_slot_exception_is_logged(guard, capture):
    """Cas réel visé : une exception dans un slot/minuterie Qt passe par
    sys.excepthook (PySide6) — elle doit arriver dans le journal."""
    from PySide6.QtCore import QEventLoop, QTimer
    _logger, records = capture
    # Vraie boucle d'évènements (comme app.exec()) : un processEvents() appelé
    # depuis Python ferait remonter l'exception jusqu'à l'appelant.
    loop = QEventLoop()
    QTimer.singleShot(0, _raise_at_fixed_place)
    QTimer.singleShot(50, loop.quit)
    loop.exec()
    assert any(r.exc_info and r.exc_info[0] is ValueError for r in records)


def test_python_thread_exception_is_logged(guard, capture):
    _logger, records = capture
    thread = threading.Thread(target=_raise_at_fixed_place, name="raccourcis")
    thread.start()
    thread.join(timeout=5)
    assert len(records) == 1
    assert "raccourcis" in records[0].getMessage()
    assert records[0].exc_info[0] is ValueError


def test_keyboard_interrupt_is_not_reported_as_error(guard, capture, monkeypatch):
    _logger, records = capture
    delegated = []
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: delegated.append(a[0]))
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert records == []
    assert delegated == [KeyboardInterrupt]


def test_repeated_error_does_not_flood_the_log(guard, capture):
    _logger, records = capture
    for _ in range(crash_guard.REPEAT_SUMMARY_EVERY):
        sys.excepthook(*_exc_info())
    full = [r for r in records if r.exc_info]
    summaries = [r for r in records if not r.exc_info]
    assert len(full) == crash_guard.FULL_REPORTS_PER_SIGNATURE
    assert len(summaries) == 1
    assert str(crash_guard.REPEAT_SUMMARY_EVERY) in summaries[0].getMessage()


def test_user_is_notified_once_per_distinct_error(guard):
    notices = []
    guard.set_notifier(notices.append)
    sys.excepthook(*_exc_info())
    sys.excepthook(*_exc_info())
    _process_events()
    assert notices == [crash_guard.USER_NOTICE]
    try:
        raise KeyError("autre")
    except KeyError:
        sys.excepthook(*sys.exc_info())
    _process_events()
    assert len(notices) == 2


def test_notice_from_a_thread_is_delivered_in_the_gui_thread(guard):
    delivered_in = []
    guard.set_notifier(lambda _m: delivered_in.append(threading.current_thread()))
    thread = threading.Thread(target=_raise_at_fixed_place)
    thread.start()
    thread.join(timeout=5)
    assert delivered_in == []  # pas d'appel direct depuis le thread de fond
    _process_events()
    assert delivered_in == [threading.main_thread()]


def test_failing_notifier_never_raises(guard, capture):
    def broken(_message):
        raise RuntimeError("fenêtre détruite")
    guard.set_notifier(broken)
    sys.excepthook(*_exc_info())
    _process_events()  # ne doit pas lever


def test_qt_warning_is_logged(guard, capture):
    from PySide6.QtCore import qWarning
    _logger, records = capture
    qWarning("avertissement de test Qt")
    messages = [r.getMessage() for r in records]
    assert "Qt : avertissement de test Qt" in messages
    assert records[-1].levelno == logging.WARNING


def test_unraisable_exception_is_logged(guard, capture):
    _logger, records = capture

    class Broken:
        def __del__(self):
            raise RuntimeError("dans le destructeur")

    obj = Broken()
    del obj
    assert any(r.exc_info and r.exc_info[0] is RuntimeError for r in records)


def test_uninstall_restores_previous_hooks(capture, tmp_path):
    logger, _records = capture
    before = (sys.excepthook, threading.excepthook, sys.unraisablehook)
    g = CrashGuard(logger=logger, log_dir=tmp_path, enable_faulthandler=False).install()
    assert sys.excepthook != before[0]
    g.uninstall()
    assert (sys.excepthook, threading.excepthook, sys.unraisablehook) == before


def test_faulthandler_writes_to_crash_file(capture, tmp_path, monkeypatch):
    """faulthandler est remplacé par un double : l'activer vraiment couperait
    celui de pytest pour le reste de la session."""
    enabled = []

    class FakeFaulthandler:
        @staticmethod
        def enable(file, all_threads):
            enabled.append((file.name, all_threads))

        @staticmethod
        def disable():
            enabled.append("disabled")

    monkeypatch.setattr(crash_guard, "faulthandler", FakeFaulthandler)
    logger, records = capture
    (tmp_path / crash_guard.CRASH_FILENAME).write_text("trace native", encoding="utf-8")
    g = CrashGuard(logger=logger, log_dir=tmp_path).install()
    try:
        assert enabled == [(str(tmp_path / crash_guard.CRASH_FILENAME), True)]
        assert any("trace native" in r.getMessage() for r in records)
    finally:
        g.uninstall()
    assert enabled[-1] == "disabled"


# --- masquage des traces ---------------------------------------------------------

def test_traceback_text_is_redacted():
    """Le formateur ajoute la trace APRÈS les filtres : sans traitement dédié,
    un secret dans le message d'une exception partait en clair."""
    filt = RedactingFilter()
    filt.register_secret("jeton-tres-secret-123")
    try:
        raise ValueError("refus pour jeton-tres-secret-123 token=abcdef")
    except ValueError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "échec", None, sys.exc_info())
    filt.filter(record)
    text = logging.Formatter().format(record)
    assert "jeton-tres-secret-123" not in text
    assert "abcdef" not in text
    assert "ValueError" in text
