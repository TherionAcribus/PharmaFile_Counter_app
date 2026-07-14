"""Tests du schéma de configuration (source unique des défauts/plages/version).

Vérifie notamment que les défauts historiquement divergents entre main.py et
preferences.py sont désormais uniques et canoniques (point 6).
"""

import settings_schema as ss
from shortcut_config import normalize_mode
from accessibility import normalize_tone, clamp_font_size
from panel_layout import clamp_thickness


class FakeSettings:
    """QSettings minimal : dict + coercion de type comme QSettings.value()."""

    def __init__(self, data=None):
        self.data = dict(data or {})

    def value(self, key, default=None, type=None):
        if key not in self.data:
            return default
        raw = self.data[key]
        if type is bool:
            return bool(raw)
        if type is int:
            return int(raw)
        if type is str:
            return str(raw)
        return raw

    def setValue(self, key, value):
        self.data[key] = value


# --- Défauts canoniques (fin des divergences) --------------------------------

def test_canonical_defaults_resolve_divergences():
    # URL : aucune adresse gravée (ni Render ni localhost) -> vide = non configuré.
    assert ss.default("web_url") == ""
    # Notification « patient courant » : activée (comportement runtime historique).
    assert ss.default("notification_current_patient") is True
    # Délai « après appel » : 30 s (valeur des préférences), pas 60.
    assert ss.default("notification_after_calling") == 30


def test_read_returns_defaults_on_empty_settings():
    s = FakeSettings()
    assert ss.read(s, "web_url") == ""
    assert ss.read(s, "notification_current_patient") is True
    assert ss.read(s, "notification_after_calling") == 30
    assert ss.read(s, "notification_volume") == 50


def test_every_default_survives_its_own_read():
    """Chaque défaut doit être valide au regard de sa propre plage/normalisation
    (sinon read() renverrait autre chose que le défaut sur une config vierge)."""
    s = FakeSettings()
    for key in ss.SETTINGS:
        assert ss.read(s, key) == ss.default(key), key


# --- Plages et normalisation -------------------------------------------------

def test_bounds_clamp_out_of_range_values():
    s = FakeSettings({
        "notification_after_calling": 999,
        "notification_volume": -5,
        "notification_duration": 0,
        "notification_font_size": 500,
    })
    assert ss.read(s, "notification_after_calling") == 120
    assert ss.read(s, "notification_volume") == 0
    assert ss.read(s, "notification_duration") == 1
    assert ss.read(s, "notification_font_size") == 36


def test_coerce_delegates_to_specialized_normalizers():
    s = FakeSettings({
        "shortcut_mode": "pas-un-mode",
        "message_tone": "pas-un-ton",
        "patient_list_font_size": 1,
        "panel_thickness": 10,
    })
    assert ss.read(s, "shortcut_mode") == normalize_mode("pas-un-mode")
    assert ss.read(s, "message_tone") == normalize_tone("pas-un-ton")
    assert ss.read(s, "patient_list_font_size") == clamp_font_size(1)
    assert ss.read(s, "panel_thickness") == clamp_thickness(10)


def test_stored_value_is_read_back():
    s = FakeSettings({
        "web_url": "https://exemple.test",
        "notification_current_patient": False,
        "notification_after_calling": 45,
    })
    assert ss.read(s, "web_url") == "https://exemple.test"
    assert ss.read(s, "notification_current_patient") is False
    assert ss.read(s, "notification_after_calling") == 45


# --- Versionnage / migrations ------------------------------------------------

def test_migrate_stamps_current_version_on_fresh_config():
    s = FakeSettings()
    version = ss.migrate_settings(s)
    assert version == ss.SCHEMA_VERSION
    assert s.value(ss.SCHEMA_VERSION_KEY, 0, type=int) == ss.SCHEMA_VERSION


def test_migrate_is_idempotent():
    s = FakeSettings()
    ss.migrate_settings(s)
    ss.migrate_settings(s)
    assert s.value(ss.SCHEMA_VERSION_KEY, 0, type=int) == ss.SCHEMA_VERSION


def test_migrate_does_not_downgrade_newer_config():
    future = ss.SCHEMA_VERSION + 5
    s = FakeSettings({ss.SCHEMA_VERSION_KEY: future})
    version = ss.migrate_settings(s)
    assert version == future
    assert s.value(ss.SCHEMA_VERSION_KEY, 0, type=int) == future
