"""Filtrage des notifications par catégorie (notification_rules + show_notification).

Régression corrigée : la case « Afficher les activités spécifiques (Vaccins,
Tests…) » servait de filtre GÉNÉRAL dans ``show_notification()``. La décocher
coupait aussi l'affichage et le son du rappel de validation, des alertes de
connexion, du papier… alors que leurs propres réglages les autorisaient.

On vérifie donc ici :
  - le classement origine -> catégorie (y compris les origines inconnues) ;
  - l'INDÉPENDANCE des catégories (aucune ne peut faire taire les autres) ;
  - la séparation « afficher » / « jouer un son » ;
  - que toutes les clés de préférences existent bien dans le schéma.
"""

import logging
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main  # noqa: E402
import notification_rules as rules  # noqa: E402
from notification import extract_origin_message  # noqa: E402
import settings_schema  # noqa: E402


# Une origine représentative (au moins) par catégorie.
ORIGINS_BY_CATEGORY = {
    rules.CURRENT_PATIENT: ("new_patient",),
    rules.AUTOCALLING: ("autocalling",),
    rules.SPECIFIC_ACTS: ("activity",),
    rules.PAPER: ("low_paper", "no_paper", "paper_ok"),
    rules.CONNECTION: ("connection", "socket_connection_true", "socket_connection_false"),
    rules.VALIDATION: ("please_validate",),
    rules.SYSTEM: ("printer_error", "disconnect_by_user"),
    rules.MESSAGING: ("messaging",),
}


# --- Classement des origines ---------------------------------------------

@pytest.mark.parametrize("origin,category", [
    ("new_patient", rules.CURRENT_PATIENT),
    ("autocalling", rules.AUTOCALLING),
    ("activity", rules.SPECIFIC_ACTS),          # acte « spécifique » du serveur
    ("low_paper", rules.PAPER),
    ("no_paper", rules.PAPER),
    ("paper_ok", rules.PAPER),
    ("connection", rules.CONNECTION),
    ("socket_connection_true", rules.CONNECTION),
    ("socket_connection_false", rules.CONNECTION),
    ("please_validate", rules.VALIDATION),
    ("printer_error", rules.SYSTEM),
    ("disconnect_by_user", rules.SYSTEM),
    ("patient_taken", rules.SYSTEM),
    ("patient_for_staff_from_app", rules.SYSTEM),
    ("messaging", rules.MESSAGING),
])
def test_category_for_known_origins(origin, category):
    assert rules.category_for_origin(origin) == category


def test_unknown_origin_is_never_silently_dropped():
    # Une origine inconnue (nouvelle version du serveur) reste affichable.
    assert rules.category_for_origin("origine_du_futur") == rules.SYSTEM
    assert rules.should_display("origine_du_futur", {}) is True


def test_missing_preference_defaults_to_enabled():
    # Préférences vides / partielles : on affiche plutôt que de perdre l'alerte.
    assert rules.should_display("please_validate", None) is True
    assert rules.should_play_sound("please_validate", {}) is True


# --- Indépendance des catégories -----------------------------------------

def _prefs(**overrides):
    prefs = {key: True for key in rules.ALL_KEYS}
    prefs.update(overrides)
    return prefs


def test_specific_acts_off_does_not_mute_other_categories():
    """Le cœur du bug : « activités spécifiques » décochée ne coupe qu'elle-même."""
    prefs = _prefs(notification_specific_acts=False)
    assert rules.should_display("activity", prefs) is False
    for origin in ("please_validate", "connection", "socket_connection_false",
                   "low_paper", "new_patient", "autocalling", "printer_error"):
        assert rules.should_display(origin, prefs) is True, origin
        assert rules.should_play_sound(origin, prefs) is True, origin


@pytest.mark.parametrize("category", rules.CATEGORIES)
def test_each_category_only_filters_itself(category):
    prefs = _prefs(**{rules.DISPLAY_KEYS[category]: False})
    for other, origins in ORIGINS_BY_CATEGORY.items():
        for origin in origins:
            assert rules.should_display(origin, prefs) is (other != category), origin
            # Le son d'une catégorie ne dépend jamais de l'affichage d'une autre.
            assert rules.should_play_sound(origin, prefs) is True, origin


# --- « Afficher » et « son » sont deux réglages distincts -----------------

def test_sound_can_be_muted_while_still_displayed():
    prefs = _prefs(notification_validation_sound=False)
    assert rules.should_display("please_validate", prefs) is True
    assert rules.should_play_sound("please_validate", prefs) is False


def test_display_off_leaves_the_sound_key_untouched():
    prefs = _prefs(notification_add_paper=False)
    assert rules.should_display("low_paper", prefs) is False
    assert rules.should_play_sound("low_paper", prefs) is True


def test_force_bypasses_every_preference():
    prefs = {key: False for key in rules.ALL_KEYS}
    assert rules.should_display("test_notification", prefs, force=True) is True
    assert rules.should_play_sound("test_notification", prefs, force=True) is True


# --- Cohérence avec le schéma de configuration ---------------------------

@pytest.mark.parametrize("key", rules.ALL_KEYS)
def test_every_preference_key_is_declared_in_schema(key):
    assert key in settings_schema.SETTINGS
    expected = False if key == "notification_messaging_sound" else True
    assert settings_schema.SETTINGS[key].default is expected


def test_one_display_and_one_sound_key_per_category():
    assert set(rules.DISPLAY_KEYS) == set(rules.CATEGORIES)
    assert set(rules.SOUND_KEYS) == set(rules.CATEGORIES)
    assert len(set(rules.ALL_KEYS)) == 2 * len(rules.CATEGORIES)
    assert set(rules.CATEGORY_LABELS) == set(rules.CATEGORIES)
    assert set(rules.CATEGORY_HINTS) == set(rules.CATEGORIES)
    assert set(ORIGINS_BY_CATEGORY) == set(rules.CATEGORIES)


# --- Intégration : MainWindow.show_notification ---------------------------

def _window(**overrides):
    """Fenêtre minimale portant show_notification et un gestionnaire factice."""
    w = types.SimpleNamespace(
        logger=logging.getLogger("test.notification.rules"),
        notification_prefs=_prefs(**overrides),
        shown=[],
        played=[],
        notified_patients=[],
        sticky_flags=[],
    )
    # Fenêtre masquée par défaut : les confirmations « inline » retombent donc
    # sur la fenêtre flottante, sauf test contraire explicite.
    w.isVisible = lambda: False

    def _notify_spy(data, internal=False, font_size=None, play_sound=True,
                    patient_id=None, sticky=False):
        origin, _message = extract_origin_message(data, internal)
        w.shown.append((origin, play_sound))
        w.sticky_flags.append(sticky)
        w.notified_patients.append(patient_id)

    manager = types.SimpleNamespace(notify=_notify_spy)
    w._ensure_notification_manager = lambda: manager
    w.audio_player = types.SimpleNamespace(play_sound=w.played.append)
    w.show_notification = types.MethodType(main.MainWindow.show_notification, w)
    w.play_notification_sound = types.MethodType(main.MainWindow.play_notification_sound, w)
    return w


def _notify(w, origin, **kwargs):
    w.show_notification({"origin": origin, "message": "m"}, internal=True, **kwargs)


def test_show_notification_filters_only_the_matching_category():
    w = _window(notification_specific_acts=False)
    _notify(w, "activity")
    _notify(w, "please_validate")
    _notify(w, "connection")
    assert [origin for origin, _sound in w.shown] == ["please_validate", "connection"]


def test_show_notification_passes_sound_preference_to_the_manager():
    w = _window(notification_connection_sound=False)
    _notify(w, "connection")
    _notify(w, "please_validate")
    assert w.shown == [("connection", False), ("please_validate", True)]


def test_show_notification_force_shows_and_sounds_despite_preferences():
    w = _window(**{key: False for key in rules.ALL_KEYS})
    _notify(w, "test_notification", force=True)
    assert w.shown == [("test_notification", True)]


def test_show_notification_reads_server_payload_origin():
    # Notification poussée par le serveur : chaîne JSON, pas un dict interne.
    w = _window(notification_specific_acts=False)
    w.show_notification('{"origin": "activity", "message": "Vaccin : A012"}')
    w.show_notification('{"origin": "printer_error", "message": "bourrage"}')
    assert [origin for origin, _sound in w.shown] == ["printer_error"]


def test_standalone_sound_follows_its_category_preference():
    # « Patient déjà pris » : son sans notification (catégorie « autres alertes »).
    w = _window()
    w.play_notification_sound("patient_taken", "patient_taken")
    assert w.played == ["patient_taken"]

    muted = _window(notification_system_sound=False)
    muted.play_notification_sound("patient_taken", "patient_taken")
    assert muted.played == []


# --- Rappel « pensez à valider » : annulation et obsolescence -------------

def _validate_window(patient_id=None):
    """Fenêtre minimale portant les méthodes liées au rappel de validation."""
    w = _window()
    w.patient_id = patient_id
    w.dismissed = []
    w.notification_manager = types.SimpleNamespace(
        dismiss=lambda origin, **kw: w.dismissed.append((origin, kw)))
    w.close_please_validate_notification = types.MethodType(
        main.MainWindow.close_please_validate_notification, w)
    w.is_notification_obsolete = types.MethodType(
        main.MainWindow.is_notification_obsolete, w)
    return w


def test_validation_dismisses_reminders_visible_and_queued():
    w = _validate_window(patient_id=42)
    w.close_please_validate_notification()
    # Le gestionnaire est chargé d'annuler les deux (visibles ET en file).
    assert w.dismissed == [("please_validate", {})]


def test_validation_without_notification_manager_is_harmless():
    w = _validate_window(patient_id=42)
    w.notification_manager = None
    w.close_please_validate_notification()   # ne doit pas lever


def test_reminder_is_obsolete_once_the_patient_has_changed():
    w = _validate_window(patient_id=8)
    assert w.is_notification_obsolete("please_validate", 7) is True   # patient précédent
    assert w.is_notification_obsolete("please_validate", 8) is False  # patient courant


def test_reminder_is_obsolete_when_there_is_no_patient_left():
    w = _validate_window(patient_id=None)
    assert w.is_notification_obsolete("please_validate", 7) is True


def test_other_notifications_never_become_obsolete():
    w = _validate_window(patient_id=8)
    assert w.is_notification_obsolete("connection", 7) is False
    assert w.is_notification_obsolete("please_validate", None) is False


def test_reminder_carries_the_patient_it_concerns():
    w = _validate_window(patient_id=42)
    w.call_timer_delay_expired = types.MethodType(main.MainWindow.call_timer_delay_expired, w)
    w._set_validate_alert = lambda alert: None
    w.call_timer_delay_expired()
    assert [origin for origin, _sound in w.shown] == ["please_validate"]
    assert w.notified_patients == [42]


# --- E5 : échecs d'action et notifications moins envahissantes --------------

def test_action_error_is_system_not_connection():
    """Régression : les échecs d'action étaient étiquetés « connection » —
    décocher les alertes de connexion les masquait aussi, alors qu'ils
    exigent une attention immédiate (catégorie SYSTÈME)."""
    prefs = _prefs(notification_connection=False)
    assert rules.category_for_origin("action_error") == rules.SYSTEM
    assert rules.should_display("action_error", prefs) is True
    assert rules.should_display("connection", prefs) is False


def test_sticky_and_inline_classification():
    # Une erreur d'action RESTE affichée jusqu'à fermeture explicite, et n'est
    # jamais ravalée en bandeau (elle doit être vue, pas discrète).
    assert rules.is_sticky("action_error") is True
    assert rules.is_inline_candidate("action_error") is False
    # Confirmations courantes : candidates au bandeau intégré, auto-fermantes.
    for origin in ("action_busy", "action_refused"):
        assert rules.is_inline_candidate(origin) is True
        assert rules.is_sticky(origin) is False
    # Rien d'autre n'est « inline » ni « sticky ».
    assert rules.is_inline_candidate("new_patient") is False
    assert rules.is_sticky("connection") is False


def test_show_notification_marks_action_errors_sticky():
    w = _window()
    _notify(w, "action_error")
    _notify(w, "connection")
    assert [o for o, _ in w.shown] == ["action_error", "connection"]
    assert w.sticky_flags == [True, False]


class _FakeHint:
    """Bandeau d'état factice (le vrai est un QLabel du panneau)."""

    def __init__(self):
        self.text = ""
        self.tooltip = ""
        self.visible = False

    def setText(self, t):
        self.text = t

    def setToolTip(self, t):
        self.tooltip = t

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


class _FakeTimer:
    def __init__(self):
        self.started_with = None

    def start(self, ms):
        self.started_with = ms


def _panel_window():
    """Fenêtre visible avec le bandeau d'état intégré (interface construite)."""
    w = _window()
    w.isVisible = lambda: True
    w.status_hint = _FakeHint()
    w._hint_timer = _FakeTimer()
    w._INLINE_NOTICE_MS = main.MainWindow._INLINE_NOTICE_MS
    w._safe_widget = main.MainWindow._safe_widget
    w._show_inline_notice = types.MethodType(
        main.MainWindow._show_inline_notice, w)
    w._clear_inline_notice = types.MethodType(
        main.MainWindow._clear_inline_notice, w)
    w._write_inline_notice = types.MethodType(
        main.MainWindow._write_inline_notice, w)
    w._hide_inline_notice = types.MethodType(
        main.MainWindow._hide_inline_notice, w)
    return w


def test_routine_confirmation_uses_panel_band_when_visible():
    """« Une action est déjà en cours » va dans le bandeau du panneau plutôt
    qu'en fenêtre flottante (moins envahissant pour un usage latéral)."""
    w = _panel_window()
    _notify(w, "action_busy")
    assert w.shown == []                     # pas de fenêtre flottante
    assert w.status_hint.text == "m"         # message affiché dans le bandeau
    assert w.status_hint.visible is True
    assert w._hint_timer.started_with == main.MainWindow._INLINE_NOTICE_MS
    assert w.played == ["ding"]              # la préférence de son reste honorée


def test_routine_confirmation_floats_when_panel_hidden():
    """Panneau masqué : repli sur la fenêtre flottante — le message n'est
    jamais perdu parce que le bandeau est invisible."""
    w = _window()                            # isVisible() -> False
    w.status_hint = _FakeHint()
    w._hint_timer = _FakeTimer()
    w._safe_widget = main.MainWindow._safe_widget
    w._show_inline_notice = types.MethodType(
        main.MainWindow._show_inline_notice, w)
    _notify(w, "action_busy")
    assert w.shown == [("action_busy", True)]


def test_routine_confirmation_floats_without_hint_widget():
    """Écran d'identification / reconstruction : pas de bandeau -> repli."""
    w = _panel_window()
    w.status_hint = None
    _notify(w, "action_refused")
    assert w.shown == [("action_refused", True)]


def test_important_notifications_never_go_inline():
    """Un nouveau patient ou une alerte connexion reste une fenêtre flottante
    même quand le panneau est visible : elles ne sont pas « courantes »."""
    w = _panel_window()
    _notify(w, "new_patient")
    _notify(w, "connection")
    assert [o for o, _ in w.shown] == ["new_patient", "connection"]
    assert w.status_hint.visible is False
