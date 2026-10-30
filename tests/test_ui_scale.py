"""Taille de l'interface (zoom global, ui_scale) : calcul, démarrage, relance."""

import logging
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import profile_sync  # noqa: E402
import settings_schema  # noqa: E402
import ui_scale  # noqa: E402
from profile_sync import DictSettings  # noqa: E402
from ui_scale import AUTO, ENV_VAR, ScaleDecision, ScreenInfo  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_current(monkeypatch):
    monkeypatch.setattr(ui_scale, "_current", None)


# --- Réglage ----------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (0, AUTO), ("0", AUTO), (85, AUTO), ("abc", AUTO), (None, AUTO), (-5, AUTO),
    (80, 80), ("90", 90), (100, 100), (110, 110), (125, 125), (150, AUTO),
])
def test_normalize_ui_scale(value, expected):
    assert ui_scale.normalize_ui_scale(value) == expected


def test_reglage_dans_le_schema_automatique_par_defaut():
    assert settings_schema.default("ui_scale") == AUTO
    assert settings_schema.read(DictSettings({}), "ui_scale") == AUTO
    assert settings_schema.read(DictSettings({"ui_scale": "110"}), "ui_scale") == 110
    # Valeur inconnue (édition manuelle) : retour à Automatique.
    assert settings_schema.read(DictSettings({"ui_scale": 95}), "ui_scale") == AUTO


def test_reglage_reste_sur_le_poste():
    """Le zoom dépend de l'écran : jamais envoyé dans le profil de l'équipier."""
    assert "ui_scale" in profile_sync.PROFILE_LOCAL_KEYS
    payload = profile_sync.build_payload({"ui_scale": 80, "always_on_top": True})
    assert "ui_scale" not in str(payload)


# --- Calcul automatique -------------------------------------------------------

@pytest.mark.parametrize("width, height, dpi, expected", [
    # Portable 1366×768 à 125 % : ≈ 1093×582 points → réduit.
    (1366, 728, 1.25, 0.85),
    # Même écran à 100 % : assez de place.
    (1366, 728, 1.0, 1.0),
    # 1366×768 à 150 % : limité par le plancher de 75 %.
    (1366, 728, 1.5, 0.75),
    # Écrans courants laissés intacts.
    (1920, 1032, 1.5, 1.0),
    (1600, 860, 1.25, 1.0),
    (1920, 1040, 1.25, 1.0),
    (3440, 1392, 1.0, 1.0),
    # Très petit écran à 100 % : jamais sous 90 % de la taille « Windows 100 % ».
    (1024, 560, 1.0, 0.9),
])
def test_auto_factor(width, height, dpi, expected):
    assert ui_scale.auto_factor(ScreenInfo(width, height, dpi)) == expected


def test_auto_factor_ne_grossit_jamais_et_tolere_l_inconnu():
    assert ui_scale.auto_factor(None) == 1.0
    assert ui_scale.auto_factor(ScreenInfo(0, 0, 1.0)) == 1.0
    assert ui_scale.auto_factor(ScreenInfo(1000, 600, 0)) == 1.0
    assert ui_scale.auto_factor(ScreenInfo(5000, 3000, 1.0)) == 1.0


def test_auto_factor_respecte_les_planchers():
    for dpi in (1.0, 1.25, 1.5, 1.75, 2.0):
        factor = ui_scale.auto_factor(ScreenInfo(800, 400, dpi))
        assert factor >= ui_scale.MIN_AUTO_FACTOR
        assert factor * dpi >= ui_scale.MIN_AUTO_EFFECTIVE - 1e-9


# --- Décision ---------------------------------------------------------------

def test_decide_manuel():
    d = ui_scale.decide(90, ScreenInfo(1366, 728, 1.25))
    assert (d.factor, d.source) == (0.9, "manual")


def test_decide_automatique():
    d = ui_scale.decide(AUTO, ScreenInfo(1366, 728, 1.25))
    assert (d.factor, d.source) == (0.85, "auto")
    assert "85" in d.describe() and "1093×582" in d.describe()


def test_decide_variable_d_environnement_prioritaire():
    d = ui_scale.decide(80, None, env_value="1.5")
    assert (d.factor, d.source) == (1.5, "environment")
    assert not d.applied_by_app
    assert ENV_VAR in d.describe()


def test_decide_variable_d_environnement_illisible():
    assert ui_scale.decide(AUTO, None, env_value="n'importe").factor == 1.0


def test_needs_restart():
    auto = ScaleDecision(AUTO, 1.0, "auto")
    assert ui_scale.needs_restart(auto, 90)
    assert not ui_scale.needs_restart(auto, AUTO)
    assert not ui_scale.needs_restart(None, 90)
    # Zoom imposé par l'environnement : relancer ne changerait rien.
    assert not ui_scale.needs_restart(ScaleDecision(AUTO, 1.2, "environment"), 90)


# --- Démarrage ----------------------------------------------------------------

def test_configure_pose_la_variable_si_reduction():
    env = {}
    d = ui_scale.configure(lambda: AUTO, lambda: ScreenInfo(1366, 728, 1.25), env)
    assert env[ENV_VAR] == "0.85"
    assert ui_scale.current() is d


def test_configure_ne_pose_rien_a_100_pourcent():
    env = {}
    ui_scale.configure(lambda: 100, lambda: None, env)
    assert ENV_VAR not in env
    ui_scale.configure(lambda: AUTO, lambda: ScreenInfo(3440, 1392, 1.0), env)
    assert ENV_VAR not in env


def test_configure_manuel_ne_mesure_pas_l_ecran():
    def probe():
        raise AssertionError("mesure inutile en mode manuel")
    env = {}
    ui_scale.configure(lambda: "110", probe, env)
    assert env[ENV_VAR] == "1.1"


def test_configure_respecte_la_variable_existante():
    env = {ENV_VAR: "1.3"}
    d = ui_scale.configure(lambda: 80, lambda: ScreenInfo(1366, 728, 1.25), env)
    assert env[ENV_VAR] == "1.3"
    assert d.source == "environment"


def test_configure_ne_bloque_jamais_le_demarrage():
    def broken():
        raise OSError("registre illisible")
    env = {}
    d = ui_scale.configure(broken, broken, env)
    assert d.factor == 1.0 and ENV_VAR not in env


# --- Relance ------------------------------------------------------------------

def test_relaunch_command():
    assert ui_scale.relaunch_command(["app.exe", "--x"], "C:/App/app.exe", True) \
        == ["C:/App/app.exe", "--x"]
    cmd = ui_scale.relaunch_command(["main.py", "--x"], "python.exe", False)
    assert cmd[0] == "python.exe" and os.path.isabs(cmd[1]) and cmd[2] == "--x"


def test_relaunch_environment_retire_notre_variable():
    ours = ScaleDecision(AUTO, 0.85, "auto")
    env = ui_scale.relaunch_environment({ENV_VAR: "0.85", "PATH": "x"}, ours, frozen=True)
    assert ENV_VAR not in env and env["PATH"] == "x"
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_relaunch_environment_garde_celle_de_l_administrateur():
    admin = ScaleDecision(AUTO, 1.3, "environment")
    env = ui_scale.relaunch_environment({ENV_VAR: "1.3"}, admin, frozen=False)
    assert env[ENV_VAR] == "1.3"
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in env


# --- Proposition de redémarrage (MainWindow.apply_preferences) ----------------

def _window(main, old_scale, new_scale):
    w = types.SimpleNamespace(
        web_url="http://a", app_secret="s", counter_id=1,
        logger=logging.getLogger("test.ui_scale"),
        horizontal_mode=False, compact_mode=False, panel_thickness=300,
        display_patient_list=False, patient_list_position_vertical="bottom",
        patient_list_position_horizontal="right", staff_id=None,
        show_auto_call_button=False, always_on_top=False, ui_scale=old_scale,
    )
    w.setup_global_shortcut = lambda: None
    w._reconnect_services = lambda *a, **k: None
    w.isVisible = lambda: False
    w.load_preferences = lambda: setattr(w, "ui_scale", new_scale)
    w._offer_restart_for_ui_scale = lambda: None
    w.apply_preferences = types.MethodType(main.MainWindow.apply_preferences, w)
    return w


@pytest.fixture
def scheduled(monkeypatch):
    import main
    calls = []
    monkeypatch.setattr(main, "QTimer", types.SimpleNamespace(
        singleShot=lambda _ms, fn: calls.append(fn)))
    return main, calls


def test_changement_de_taille_propose_un_redemarrage(scheduled):
    main, calls = scheduled
    ui_scale._current = ScaleDecision(AUTO, 1.0, "auto")
    w = _window(main, AUTO, 90)
    w.apply_preferences()
    assert w._offer_restart_for_ui_scale in calls


def test_sans_changement_pas_de_redemarrage(scheduled):
    main, calls = scheduled
    ui_scale._current = ScaleDecision(AUTO, 1.0, "auto")
    w = _window(main, AUTO, AUTO)
    w.apply_preferences()
    assert w._offer_restart_for_ui_scale not in calls


def test_retour_a_la_valeur_de_demarrage_pas_de_redemarrage(scheduled):
    main, calls = scheduled
    ui_scale._current = ScaleDecision(AUTO, 1.0, "auto")
    w = _window(main, 90, AUTO)   # « Plus tard » puis retour à Automatique
    w.apply_preferences()
    assert w._offer_restart_for_ui_scale not in calls


def test_request_restart_ferme_puis_marque_la_relance(monkeypatch):
    import main
    quits = []
    monkeypatch.setattr(main, "QApplication", types.SimpleNamespace(
        instance=lambda: types.SimpleNamespace(quit=lambda: quits.append(1))))
    w = types.SimpleNamespace(logger=logging.getLogger("t"), restart_requested=False)
    w.close = lambda: True
    main.MainWindow.request_restart(w)
    assert w.restart_requested and quits == [1]

    w.restart_requested = False
    w.close = lambda: False   # fermeture refusée : pas de relance
    main.MainWindow.request_restart(w)
    assert not w.restart_requested
