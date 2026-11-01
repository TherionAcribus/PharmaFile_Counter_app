"""Table des préférences lues par la fenêtre principale (window_preferences)."""

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import notification_rules
import settings_schema
import window_preferences as wp
from shortcut_defaults import default_shortcut


class FakeSettings:
    """QSettings en mémoire : seules value/setValue sont utilisées."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, default=None, type=None):  # même signature que QSettings.value
        value = self.values.get(key, default)
        return type(value) if type is not None and value is not None else value

    def setValue(self, key, value):
        self.values[key] = value


#: Attributs que MainWindow lisait avant la table : aucun ne doit disparaître.
EXPECTED_ATTRIBUTES = {
    "reconnect_last_user", "next_patient_shortcut", "validate_patient_shortcut",
    "pause_shortcut", "recall_shortcut", "deconnect_shortcut", "shortcut_mode",
    "confirm_sensitive_shortcuts", "shortcut_feedback", "notification_prefs",
    "notification_after_deconnection", "timer_after_calling", "notification_duration",
    "notification_font_size", "message_tone", "patient_list_font_size",
    "notification_corner", "sound_volume", "sound_muted", "always_on_top",
    "horizontal_mode", "compact_mode", "show_auto_call_button", "panel_snap",
    "panel_thickness", "display_patient_list", "patient_list_position_vertical",
    "patient_list_position_horizontal", "tray_single_icon", "debug_window",
    "selected_skin", "ui_scale",
}


def test_every_key_exists_in_the_schema():
    for _attr, key in wp.SCHEMA_PREFERENCES:
        assert key in settings_schema.SETTINGS, key


def test_each_attribute_is_set_once():
    attrs = [attr for attr, _key in wp.SCHEMA_PREFERENCES] + list(wp.SHORTCUT_PREFERENCES)
    assert len(attrs) == len(set(attrs))


def test_all_window_attributes_are_read():
    values = wp.read_preferences(FakeSettings())
    assert set(values) == EXPECTED_ATTRIBUTES


def test_defaults_come_from_the_schema():
    values = wp.read_preferences(FakeSettings())
    for attr, key in wp.SCHEMA_PREFERENCES:
        assert values[attr] == settings_schema.default(key), attr
    assert values["next_patient_shortcut"] == default_shortcut("next_patient_shortcut")
    assert set(values["notification_prefs"]) == set(notification_rules.ALL_KEYS)


def test_stored_values_are_used_including_historical_names():
    settings = FakeSettings({"vertical_mode": True, "notification_after_calling": 45,
                             "notification_volume": 30})
    values = wp.read_preferences(settings)
    # La clé « vertical_mode » porte l'orientation HORIZONTALE (nom historique).
    assert values["horizontal_mode"] is True
    assert values["timer_after_calling"] == 45
    assert values["sound_volume"] == 30


def test_layout_attributes_are_read_preferences():
    read = {attr for attr, _key in wp.SCHEMA_PREFERENCES}
    assert set(wp.LAYOUT_ATTRIBUTES) <= read
    assert wp.LAYOUT_ATTRIBUTES[:len(wp.SHAPE_ATTRIBUTES)] == wp.SHAPE_ATTRIBUTES


def test_snapshot_reads_current_values():
    window = types.SimpleNamespace(compact_mode=True, panel_thickness=280)
    assert wp.snapshot(window, ("compact_mode", "panel_thickness", "absent")) == (True, 280, None)
