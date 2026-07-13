"""Tests des défauts de raccourcis centralisés + migration Altl+P (point 18)."""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402
from shortcut_defaults import (  # noqa: E402
    SHORTCUT_DEFAULTS,
    default_shortcut,
    migrate_shortcut,
)

_HERE = os.path.dirname(__file__)
_APP = os.path.abspath(os.path.join(_HERE, os.pardir))


def _read(name):
    with open(os.path.join(_APP, name), encoding="utf-8") as fh:
        return fh.read()


# --- défauts ----------------------------------------------------------------

def test_pause_default_is_alt_p():
    assert default_shortcut("pause_shortcut") == "Alt+P"


def test_all_defaults_present():
    assert SHORTCUT_DEFAULTS == {
        "next_patient_shortcut": "Alt+S",
        "validate_patient_shortcut": "Alt+V",
        "pause_shortcut": "Alt+P",
        "recall_shortcut": "Alt+R",
        "deconnect_shortcut": "Alt+D",
    }


# --- migration --------------------------------------------------------------

def test_migrate_fixes_altl_p():
    assert migrate_shortcut("pause_shortcut", "Altl+P") == "Alt+P"


def test_migrate_leaves_valid_value_unchanged():
    assert migrate_shortcut("pause_shortcut", "Ctrl+P") == "Ctrl+P"
    assert migrate_shortcut("pause_shortcut", "Alt+P") == "Alt+P"


def test_migrate_only_applies_to_known_field():
    # "Altl+P" n'est corrigé que pour pause_shortcut, pas ailleurs.
    assert migrate_shortcut("recall_shortcut", "Altl+P") == "Altl+P"


# --- plus de "Altl+P" en dur ni de défauts divergents -----------------------

def test_no_buggy_default_pattern_in_source():
    # L'ancien défaut bogué (passé à value()/load_shortcut) ne doit plus exister.
    # (Une mention "Altl+P" dans un commentaire de migration reste tolérée.)
    for f in ("main.py", "preferences.py"):
        assert '", "Altl+P")' not in _read(f)


# --- migration de bout en bout via main._load_shortcut ----------------------

class FakeSettings:
    def __init__(self, data=None):
        self._data = dict(data or {})
    def value(self, key, default=None):
        return self._data.get(key, default)
    def setValue(self, key, val):
        self._data[key] = val


def _fake_window():
    w = types.SimpleNamespace(logger=__import__("logging").getLogger("test.shortcut"))
    w._load_shortcut = types.MethodType(main.MainWindow._load_shortcut, w)
    return w


def test_load_shortcut_uses_default_when_unset():
    w = _fake_window()
    assert w._load_shortcut(FakeSettings(), "pause_shortcut") == "Alt+P"


def test_load_shortcut_migrates_and_persists_legacy_value():
    settings = FakeSettings({"pause_shortcut": "Altl+P"})
    w = _fake_window()
    assert w._load_shortcut(settings, "pause_shortcut") == "Alt+P"
    # La correction est persistée (plus de "Altl+P" au prochain démarrage).
    assert settings.value("pause_shortcut") == "Alt+P"
