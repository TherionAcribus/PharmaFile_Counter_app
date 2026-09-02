"""Défauts de raccourcis centralisés + migration Altl+P (point 18) et lecture
unifiée main.py / preferences.py (point 10.2)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402
from shortcut_defaults import (  # noqa: E402
    SHORTCUT_DEFAULTS,
    UI_MODIFIERS,
    default_shortcut,
    join_shortcut,
    migrate_shortcut,
    read_shortcut,
    split_shortcut,
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


# --- lecture unifiée (read_shortcut) ---------------------------------------

class FakeSettings:
    def __init__(self, data=None):
        self._data = dict(data or {})
    def value(self, key, default=None):
        return self._data.get(key, default)
    def setValue(self, key, val):
        self._data[key] = val


def test_read_shortcut_uses_default_when_unset():
    assert read_shortcut(FakeSettings(), "pause_shortcut") == "Alt+P"


def test_read_shortcut_migrates_and_persists_legacy_value():
    settings = FakeSettings({"pause_shortcut": "Altl+P"})
    assert read_shortcut(settings, "pause_shortcut") == "Alt+P"
    # La correction est persistée (plus de "Altl+P" au prochain démarrage).
    assert settings.value("pause_shortcut") == "Alt+P"


def test_read_shortcut_persiste_meme_depuis_les_preferences():
    """Régression du point 10.2 : la fenêtre de préférences migrait sans
    persister, elle pouvait donc réécrire l'ancienne valeur erronée."""
    settings = FakeSettings({"pause_shortcut": "Altl+P"})
    dialog_value = read_shortcut(settings, "pause_shortcut")
    startup_value = read_shortcut(settings, "pause_shortcut")
    assert dialog_value == startup_value == "Alt+P"
    assert settings.value("pause_shortcut") == "Alt+P"


def test_read_shortcut_journalise_la_migration():
    messages = []

    class FakeLogger:
        def info(self, msg, *args):
            messages.append(msg % args)

    read_shortcut(FakeSettings({"pause_shortcut": "Altl+P"}), "pause_shortcut", FakeLogger())
    assert messages and "pause_shortcut" in messages[0]
    # Aucun journal quand rien ne change.
    messages.clear()
    read_shortcut(FakeSettings({"pause_shortcut": "Alt+P"}), "pause_shortcut", FakeLogger())
    assert messages == []


# --- décomposition / recomposition pour l'interface ------------------------

@pytest.mark.parametrize("value, modifiers, key", [
    ("Alt+P", ["Alt"], "P"),
    ("Ctrl+Maj+K", ["Ctrl", "Maj"], "K"),
    ("F5", [], "F5"),
    ("Alt", ["Alt"], ""),          # modificateur seul : pas de touche finale
    ("Alt+", ["Alt"], ""),
    ("", [], ""),
    (None, [], ""),
])
def test_split_shortcut(value, modifiers, key):
    assert split_shortcut(value) == (modifiers, key)


@pytest.mark.parametrize("value", ["Alt+P", "Ctrl+Maj+K", "F5", "Ctrl+Alt+Maj+Win+X"])
def test_split_join_aller_retour(value):
    modifiers, key = split_shortcut(value)
    assert join_shortcut(modifiers, key) == value


def test_join_shortcut_ordre_canonique():
    """L'ordre des modificateurs ne dépend pas de l'ordre de saisie."""
    assert join_shortcut(["Maj", "Ctrl"], "K") == "Ctrl+Maj+K"
    assert join_shortcut(UI_MODIFIERS, "") == "Ctrl+Alt+Maj+Win"


# --- cliquet : une seule implémentation de lecture -------------------------

def test_aucune_relecture_locale_des_raccourcis():
    """main.py et preferences.py ne doivent plus lire/migrer un raccourci
    eux-mêmes : read_shortcut est la seule porte d'entrée."""
    for name in ("main.py", "preferences.py"):
        source = _read(name)
        assert "migrate_shortcut(" not in source, name
        assert "default_shortcut(" not in source, name
        assert "read_shortcut(" in source, name


def test_main_ne_definit_plus_son_propre_loader():
    assert not hasattr(main.MainWindow, "_load_shortcut")
