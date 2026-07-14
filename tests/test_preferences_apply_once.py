"""Application des préférences une seule fois (point 7).

show_preferences_dialog ne doit utiliser QU'UN mécanisme : le résultat du
dialogue. apply_preferences est appelé EXACTEMENT une fois quand l'utilisateur
enregistre (Accepted) et JAMAIS s'il annule (Rejected). Plus de signal
preferences_updated concurrent (supprimé).
"""

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from PySide6.QtWidgets import QDialog  # noqa: E402

import main  # noqa: E402
import preferences  # noqa: E402


def _make_fake_dialog(code):
    class FakeDialog:
        def __init__(self, parent):
            self.parent = parent

        def exec(self):
            return code

    return FakeDialog


def _win():
    w = types.SimpleNamespace(calls={"apply": 0})
    w.apply_preferences = lambda: w.calls.__setitem__("apply", w.calls["apply"] + 1)
    w.show_preferences_dialog = types.MethodType(main.MainWindow.show_preferences_dialog, w)
    return w


def test_apply_called_once_on_accepted(monkeypatch):
    monkeypatch.setattr(main, "PreferencesDialog", _make_fake_dialog(QDialog.Accepted))
    w = _win()
    w.show_preferences_dialog()
    assert w.calls["apply"] == 1


def test_apply_not_called_on_rejected(monkeypatch):
    monkeypatch.setattr(main, "PreferencesDialog", _make_fake_dialog(QDialog.Rejected))
    w = _win()
    w.show_preferences_dialog()
    assert w.calls["apply"] == 0


def test_dialog_no_longer_exposes_update_signal():
    # Le signal concurrent a été retiré : un seul mécanisme (résultat du dialogue).
    assert not hasattr(preferences.PreferencesDialog, "preferences_updated")
