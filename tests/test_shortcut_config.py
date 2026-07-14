"""Tests de la logique des raccourcis (point 27) : shortcut_config.py.

Fonctions pures (sans Qt ni keyboard) : normalisation indépendante de l'ordre/la
casse, détection de doublons entre actions, traduction vers la syntaxe keyboard
(mode global) et QKeySequence (mode premier plan), validation du mode.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import shortcut_config as sc  # noqa: E402


# --- normalize_shortcut -----------------------------------------------------

def test_normalize_is_order_insensitive():
    assert sc.normalize_shortcut("Alt+Ctrl+P") == sc.normalize_shortcut("Ctrl+Alt+P")


def test_normalize_is_case_insensitive():
    assert sc.normalize_shortcut("ctrl+alt+p") == sc.normalize_shortcut("Ctrl+Alt+P")


def test_normalize_maps_maj_to_shift():
    # « Maj » (UI française) et « Shift » sont le même modificateur.
    assert sc.normalize_shortcut("Maj+P") == sc.normalize_shortcut("Shift+P")


def test_normalize_maps_win_and_meta():
    assert sc.normalize_shortcut("Win+P") == sc.normalize_shortcut("Meta+P")


def test_normalize_empty_is_blank():
    assert sc.normalize_shortcut("") == ""
    assert sc.normalize_shortcut(None) == ""


def test_normalize_modifiers_only_is_blank():
    # Sans touche « réelle », non assignable -> vide.
    assert sc.normalize_shortcut("Ctrl+Alt") == ""


def test_normalize_dedupes_repeated_modifier():
    assert sc.normalize_shortcut("Ctrl+Ctrl+P") == "ctrl+p"


# --- find_duplicate_shortcuts -----------------------------------------------

def test_duplicates_detected_regardless_of_order():
    mapping = {"next": "Ctrl+Alt+P", "pause": "Alt+Ctrl+P"}
    dups = sc.find_duplicate_shortcuts(mapping)
    assert list(dups.values()) == [["next", "pause"]] or list(dups.values()) == [["pause", "next"]]
    assert len(dups) == 1


def test_no_duplicates_when_distinct():
    mapping = {"next": "Alt+S", "pause": "Alt+P", "validate": "Alt+V"}
    assert sc.find_duplicate_shortcuts(mapping) == {}


def test_empty_shortcuts_are_not_duplicates():
    # Deux actions non assignées ne constituent pas un conflit.
    mapping = {"next": "", "pause": "", "validate": "Alt+V"}
    assert sc.find_duplicate_shortcuts(mapping) == {}


def test_duplicate_maj_vs_shift():
    mapping = {"a": "Maj+X", "b": "Shift+X"}
    assert len(sc.find_duplicate_shortcuts(mapping)) == 1


# --- to_keyboard_hotkey -----------------------------------------------------

def test_to_keyboard_basic():
    assert sc.to_keyboard_hotkey("Alt+P") == "alt+p"


def test_to_keyboard_translates_maj_and_win():
    assert sc.to_keyboard_hotkey("Ctrl+Maj+P") == "ctrl+shift+p"
    assert sc.to_keyboard_hotkey("Win+P") == "windows+p"


def test_to_keyboard_order_insensitive_output():
    # Modificateurs triés -> sortie stable quel que soit l'ordre de saisie.
    assert sc.to_keyboard_hotkey("Alt+Ctrl+P") == sc.to_keyboard_hotkey("Ctrl+Alt+P")


def test_to_keyboard_none_without_key():
    assert sc.to_keyboard_hotkey("Ctrl+Alt") is None
    assert sc.to_keyboard_hotkey("") is None


# --- to_qt_key_sequence -----------------------------------------------------

def test_to_qt_basic():
    assert sc.to_qt_key_sequence("Alt+P") == "Alt+P"


def test_to_qt_translates_maj_and_win():
    assert sc.to_qt_key_sequence("Maj+P") == "Shift+P"
    assert sc.to_qt_key_sequence("Win+P") == "Meta+P"


def test_to_qt_fixed_modifier_order():
    # Ordre Ctrl, Alt, Shift, Meta quel que soit l'ordre de saisie.
    assert sc.to_qt_key_sequence("Win+Alt+Ctrl+Maj+P") == "Ctrl+Alt+Shift+Meta+P"


def test_to_qt_function_key():
    assert sc.to_qt_key_sequence("Ctrl+f1") == "Ctrl+F1"


def test_to_qt_none_without_key():
    assert sc.to_qt_key_sequence("Alt") is None


# --- is_recognized_key ------------------------------------------------------

def test_recognized_single_char_keys():
    assert sc.is_recognized_key("p")
    assert sc.is_recognized_key("A")
    assert sc.is_recognized_key("7")


def test_recognized_named_keys_case_insensitive():
    assert sc.is_recognized_key("F5")
    assert sc.is_recognized_key("space")
    assert sc.is_recognized_key("Enter")


def test_unrecognized_keys():
    assert not sc.is_recognized_key("")
    assert not sc.is_recognized_key(None)
    assert not sc.is_recognized_key("abc")      # multi-caractères non nommé


# --- find_invalid_shortcuts (validation point 7) ----------------------------

def test_valid_shortcuts_have_no_errors():
    mapping = {"next": "Alt+S", "validate": "Ctrl+F1", "pause": "P"}
    assert sc.find_invalid_shortcuts(mapping) == {}


def test_empty_field_is_invalid():
    assert sc.find_invalid_shortcuts({"next": ""}) == {"next": sc.INVALID_EMPTY}
    assert sc.find_invalid_shortcuts({"next": None}) == {"next": sc.INVALID_EMPTY}


def test_lone_modifier_is_invalid():
    assert sc.find_invalid_shortcuts({"next": "Ctrl+Alt"}) == {"next": sc.INVALID_LONE_MODIFIER}
    assert sc.find_invalid_shortcuts({"next": "Maj"}) == {"next": sc.INVALID_LONE_MODIFIER}


def test_unknown_key_is_invalid():
    assert sc.find_invalid_shortcuts({"next": "Ctrl+abc"}) == {"next": sc.INVALID_UNKNOWN_KEY}


def test_invalid_only_reports_offending_actions():
    mapping = {"next": "Alt+S", "pause": "Ctrl", "recall": "", "validate": "Ctrl+zz"}
    invalid = sc.find_invalid_shortcuts(mapping)
    assert invalid == {
        "pause": sc.INVALID_LONE_MODIFIER,
        "recall": sc.INVALID_EMPTY,
        "validate": sc.INVALID_UNKNOWN_KEY,
    }


# --- normalize_mode ---------------------------------------------------------

def test_normalize_mode_valid():
    for m in sc.MODES:
        assert sc.normalize_mode(m) == m


def test_normalize_mode_invalid_falls_back():
    assert sc.normalize_mode("bogus") == sc.DEFAULT_MODE
    assert sc.normalize_mode(None) == sc.DEFAULT_MODE
