"""Taille de l'interface : zoom global de l'App Comptoir.

Problème : sur un petit écran (portable 1366×768, Windows réglé à 125 %),
l'App ne dispose que d'environ 1093×614 points utiles et déborde. Qt suit le
zoom de Windows et rien ne permettait de l'ajuster.

Solution : un réglage POSTE « Taille de l'interface » (clé ``ui_scale``) :

* ``0`` = Automatique (défaut) : si l'écran principal offre moins de place que
  ``REFERENCE_WIDTH`` × ``REFERENCE_HEIGHT`` points, le zoom est réduit juste
  assez pour que l'interface tienne — jamais en dessous de
  ``MIN_AUTO_FACTOR`` ni d'une taille finale de ``MIN_AUTO_EFFECTIVE`` (90 % de
  la taille « Windows à 100 % »), pour rester lisible. Jamais d'agrandissement ;
* ``80``…``125`` = zoom manuel, en pourcentage du zoom de Windows.

Le zoom est appliqué par la variable d'environnement ``QT_SCALE_FACTOR``, lue
par Qt À LA CRÉATION de ``QApplication`` : tout suit d'un coup (polices,
boutons, icônes, docks, dialogues), mais un changement exige un redémarrage —
d'où ``relaunch``. Une variable ``QT_SCALE_FACTOR`` déjà posée par
l'administrateur l'emporte toujours (dépannage), et le réglage est alors
signalé comme sans effet.

Le calcul (``auto_factor``, ``decide``, commandes de relance) est pur et testé
sans Qt ; seule ``probe_primary_screen`` interroge Windows (ctypes), AVANT la
création de ``QApplication`` — Qt ne peut pas encore répondre à ce moment-là.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping, MutableMapping, Optional, Sequence

logger = logging.getLogger("appcomptoir.ui_scale")

#: Clé QSettings (réglage du POSTE : dépend de l'écran, jamais envoyé dans le
#: profil de l'équipier).
SETTING_KEY = "ui_scale"
#: Valeur « Automatique ».
AUTO = 0
#: Zooms manuels proposés, en % du zoom de Windows.
PRESETS = (80, 90, 100, 110, 125)
CHOICES = (AUTO, *PRESETS)
DEFAULT = AUTO

#: Variable lue par Qt au démarrage.
ENV_VAR = "QT_SCALE_FACTOR"

#: Place utile (en points, barre des tâches déduite) en dessous de laquelle le
#: mode automatique réduit le zoom. 680 de haut : un écran 1920×1080 à 150 %
#: (≈ 688) ou 1600×900 à 125 % (≈ 688) restent intacts ; 1366×768 à 125 %
#: (≈ 582) est réduit.
REFERENCE_WIDTH = 1000
REFERENCE_HEIGHT = 680
#: Zoom minimal choisi automatiquement (par rapport au zoom de Windows).
MIN_AUTO_FACTOR = 0.75
#: Taille finale minimale (zoom Windows × zoom de l'App) : en dessous, le texte
#: devient trop petit pour un usage au comptoir.
MIN_AUTO_EFFECTIVE = 0.9
#: Pas des zooms automatiques (85 %, 90 %… plutôt que 87,3 %).
AUTO_STEP = 0.05


def normalize_ui_scale(value: Any) -> int:
    """Valeur enregistrée → l'un des ``CHOICES`` (Automatique sinon)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return AUTO
    return number if number in CHOICES else AUTO


def format_percent(factor: float) -> str:
    """0.85 → « 85 % » (espace insécable, typographie française)."""
    return f"{round(factor * 100)} %"


@dataclass(frozen=True)
class ScreenInfo:
    """Zone utile de l'écran principal en pixels PHYSIQUES + zoom Windows."""

    work_width: int
    work_height: int
    dpi_scale: float  # 1.0 = 100 %, 1.25 = 125 %…

    @property
    def logical_width(self) -> int:
        return round(self.work_width / self.dpi_scale)

    @property
    def logical_height(self) -> int:
        return round(self.work_height / self.dpi_scale)


def auto_factor(screen: Optional[ScreenInfo]) -> float:
    """Zoom de l'App choisi automatiquement pour ``screen`` (1.0 = inchangé)."""
    if screen is None or screen.dpi_scale <= 0:
        return 1.0
    if screen.logical_width <= 0 or screen.logical_height <= 0:
        return 1.0
    needed = min(1.0,
                 screen.logical_width / REFERENCE_WIDTH,
                 screen.logical_height / REFERENCE_HEIGHT)
    if needed >= 1.0:
        return 1.0
    # Arrondi vers le BAS (au pas près) : l'interface doit tenir.
    stepped = math.floor(needed / AUTO_STEP + 1e-9) * AUTO_STEP
    floor = max(MIN_AUTO_FACTOR, MIN_AUTO_EFFECTIVE / screen.dpi_scale)
    return round(min(1.0, max(stepped, floor)), 2)


@dataclass(frozen=True)
class ScaleDecision:
    """Zoom retenu au démarrage, et pourquoi.

    ``source`` : « auto », « manual » ou « environment » (variable posée hors de
    l'App, prioritaire)."""

    setting: int
    factor: float
    source: str
    screen: Optional[ScreenInfo] = None

    @property
    def applied_by_app(self) -> bool:
        """Vrai si c'est l'App qui a posé ``QT_SCALE_FACTOR``."""
        return self.source != "environment" and self.factor != 1.0

    def describe(self) -> str:
        """Phrase affichée dans les préférences."""
        if self.source == "environment":
            return (f"Actuellement : {format_percent(self.factor)}, imposé par la "
                    f"variable d'environnement {ENV_VAR} de ce poste "
                    "(le réglage ci-dessus est sans effet tant qu'elle existe).")
        if self.source == "manual":
            return f"Actuellement : {format_percent(self.factor)} (choisi manuellement)."
        screen = self.screen
        if screen is None:
            return ("Actuellement : 100 % (taille de l'écran inconnue, "
                    "aucun ajustement automatique).")
        detail = (f"écran utile {screen.logical_width}×{screen.logical_height} "
                  f"points, zoom Windows {format_percent(screen.dpi_scale)}")
        if self.factor == 1.0:
            return f"Actuellement : 100 %, l'écran est assez grand ({detail})."
        return f"Actuellement : {format_percent(self.factor)}, réduit automatiquement ({detail})."


def decide(setting: Any, screen: Optional[ScreenInfo],
           env_value: Optional[str] = None) -> ScaleDecision:
    """Zoom à appliquer d'après le réglage, l'écran et l'environnement."""
    setting = normalize_ui_scale(setting)
    if env_value is not None and env_value.strip():
        try:
            factor = float(env_value)
        except ValueError:
            factor = 1.0
        return ScaleDecision(setting, factor if factor > 0 else 1.0,
                             "environment", screen)
    if setting == AUTO:
        return ScaleDecision(setting, auto_factor(screen), "auto", screen)
    return ScaleDecision(setting, setting / 100, "manual", screen)


def needs_restart(decision: Optional[ScaleDecision], new_setting: Any) -> bool:
    """Le réglage enregistré diffère-t-il de celui appliqué au démarrage ?

    Faux si une variable d'environnement impose le zoom (relancer ne
    changerait rien)."""
    if decision is None or decision.source == "environment":
        return False
    return normalize_ui_scale(new_setting) != decision.setting


# --- Application au démarrage ------------------------------------------------

_current: Optional[ScaleDecision] = None


def current() -> Optional[ScaleDecision]:
    """Décision prise au démarrage (None si ``configure`` n'a pas été appelé)."""
    return _current


def configure(read_setting: Callable[[], Any],
              probe: Callable[[], Optional[ScreenInfo]],
              environ: MutableMapping[str, str]) -> ScaleDecision:
    """Décide du zoom et pose ``QT_SCALE_FACTOR`` dans ``environ``.

    À appeler AVANT ``QApplication(...)``. Ne lève jamais : en cas de souci de
    lecture, l'App démarre sans zoom plutôt que de ne pas démarrer."""
    global _current
    env_value = environ.get(ENV_VAR)
    try:
        setting = read_setting()
    except Exception:  # noqa: BLE001 — démarrer coûte que coûte
        setting = DEFAULT
    screen = None
    if not (env_value and env_value.strip()) and normalize_ui_scale(setting) == AUTO:
        try:
            screen = probe()
        except Exception:  # noqa: BLE001
            screen = None
    decision = decide(setting, screen, env_value)
    if decision.applied_by_app:
        environ[ENV_VAR] = f"{decision.factor:g}"
    _current = decision
    return decision


def read_setting_from_qsettings() -> Any:
    """Lit le réglage sans QApplication (identité passée explicitement)."""
    from PySide6.QtCore import QSettings

    from app_identity import APPLICATION_NAME, ORGANIZATION_NAME
    return QSettings(ORGANIZATION_NAME, APPLICATION_NAME).value(SETTING_KEY, DEFAULT)


def probe_primary_screen() -> Optional[ScreenInfo]:
    """Zone utile de l'écran principal (pixels physiques) et zoom Windows.

    Windows uniquement (None ailleurs ou en cas d'échec). Le fil d'exécution
    passe un instant en mode « DPI par écran » pour obtenir les vraies valeurs,
    puis revient à son mode d'origine : c'est Qt qui fixera ensuite celui du
    processus. Sans cette API (Windows < 10 1607), Windows renvoie des valeurs
    déjà divisées par le zoom et un DPI de 96 : le résultat reste juste."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    set_context = getattr(user32, "SetThreadDpiAwarenessContext", None)
    previous = None
    if set_context is not None:
        set_context.restype = ctypes.c_void_p
        set_context.argtypes = [ctypes.c_void_p]
        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        previous = set_context(ctypes.c_void_p(-4))
    try:
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        monitor = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)  # PRIMARY
        if not monitor:
            return None
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        work = info.rcWork
        dpi = 96
        try:
            dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
            shcore = ctypes.windll.shcore
            shcore.GetDpiForMonitor.argtypes = [
                ctypes.c_void_p, ctypes.c_int,
                ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
            # 0 = MDT_EFFECTIVE_DPI ; 0 renvoyé = S_OK
            if shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x),
                                       ctypes.byref(dpi_y)) == 0 and dpi_x.value:
                dpi = dpi_x.value
        except (AttributeError, OSError):
            pass
        return ScreenInfo(work.right - work.left, work.bottom - work.top, dpi / 96)
    finally:
        if previous:
            set_context(ctypes.c_void_p(previous))


def configure_before_app() -> ScaleDecision:
    """Point d'entrée de main.py, juste avant ``QApplication(sys.argv)``."""
    return configure(read_setting_from_qsettings, probe_primary_screen, os.environ)


# --- Redémarrage ---------------------------------------------------------------

def relaunch_command(argv: Sequence[str], executable: str, frozen: bool) -> list:
    """Ligne de commande qui relance l'App avec les mêmes arguments."""
    if frozen:
        return [executable, *argv[1:]]
    return [executable, os.path.abspath(argv[0]), *argv[1:]]


def relaunch_environment(environ: Mapping[str, str],
                         decision: Optional[ScaleDecision],
                         frozen: bool) -> dict:
    """Environnement de la nouvelle instance.

    On retire le ``QT_SCALE_FACTOR`` posé par NOUS : sinon la nouvelle instance
    le prendrait pour une consigne de l'administrateur et ignorerait le nouveau
    réglage. Exécutable PyInstaller : ``PYINSTALLER_RESET_ENVIRONMENT`` fait
    repartir la nouvelle instance de zéro (le dossier temporaire de l'instance
    actuelle disparaît à sa fermeture)."""
    env = dict(environ)
    if decision is not None and decision.applied_by_app:
        env.pop(ENV_VAR, None)
    if frozen:
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def relaunch() -> bool:
    """Démarre une nouvelle instance de l'App, détachée de celle-ci."""
    frozen = bool(getattr(sys, "frozen", False))
    command = relaunch_command(sys.argv, sys.executable, frozen)
    env = relaunch_environment(os.environ, _current, frozen)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(command, env=env, close_fds=True, creationflags=flags)  # noqa: S603
    except OSError:
        logger.exception("Redémarrage de l'App impossible")
        return False
    logger.info("Nouvelle instance de l'App lancée (changement de taille d'interface)")
    return True
