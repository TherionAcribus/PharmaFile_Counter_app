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
    assert settings_schema.SETTINGS[key].default is True   # tout activé par défaut


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
    )
    def _notify_spy(data, internal=False, font_size=None, play_sound=True):
        origin, _message = extract_origin_message(data, internal)
        w.shown.append((origin, play_sound))

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
