"""Enregistrement / désenregistrement des raccourcis (point 30, mode d'install.).

Complète test_shortcut_dispatch.py (qui teste l'enregistrement des hotkeys et la
collecte des échecs) en couvrant l'ORCHESTRATION _install_shortcuts /
_remove_all_shortcuts : un seul mécanisme actif à la fois, et surtout le
DÉSENREGISTREMENT systématique (unhook des hooks keyboard + désactivation/
suppression des QShortcut) avant toute (ré)installation — sinon les hooks
s'empileraient et une pression déclencherait l'action plusieurs fois.

On appelle les vraies méthodes avec un faux ``self`` ; ``main.keyboard`` est
monkeypatché (aucun hook système réel) et les installateurs concrets
(_install_focused_shortcuts / _install_global_shortcuts) sont stubbés pour ne
tester ici que l'aiguillage.
"""

import logging
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402


class FakeKeyboard:
    def __init__(self):
        self.unhook_calls = 0

    def unhook_all_hotkeys(self):
        self.unhook_calls += 1


class FakeQShortcut:
    def __init__(self):
        self.enabled = True
        self.deleted = False

    def setEnabled(self, value):
        self.enabled = value

    def deleteLater(self):
        self.deleted = True


@pytest.fixture
def fake_keyboard(monkeypatch):
    kb = FakeKeyboard()
    monkeypatch.setattr(main, "keyboard", kb)
    return kb


def _win(mode, existing_shortcuts=None):
    w = types.SimpleNamespace(
        logger=logging.getLogger("test.shortcut_install"),
        shortcut_mode=mode,
        _qshortcuts=list(existing_shortcuts or []),
        calls={"focused": 0, "global": 0},
    )
    w._install_focused_shortcuts = lambda: w.calls.__setitem__("focused", w.calls["focused"] + 1)
    w._install_global_shortcuts = lambda: w.calls.__setitem__("global", w.calls["global"] + 1)
    w._remove_all_shortcuts = types.MethodType(main.MainWindow._remove_all_shortcuts, w)
    w._install_shortcuts = types.MethodType(main.MainWindow._install_shortcuts, w)
    return w


# --- _remove_all_shortcuts (désenregistrement) ------------------------------

def test_remove_unhooks_keyboard_and_clears_qshortcuts(fake_keyboard):
    sc1, sc2 = FakeQShortcut(), FakeQShortcut()
    w = _win(main.MODE_GLOBAL, existing_shortcuts=[sc1, sc2])
    w._remove_all_shortcuts()
    assert fake_keyboard.unhook_calls == 1       # hooks système retirés
    assert sc1.enabled is False and sc1.deleted is True
    assert sc2.enabled is False and sc2.deleted is True
    assert w._qshortcuts == []                   # liste vidée


def test_remove_is_safe_without_existing_shortcuts(fake_keyboard):
    w = _win(main.MODE_DISABLED)
    w._remove_all_shortcuts()                    # ne doit pas lever
    assert fake_keyboard.unhook_calls == 1
    assert w._qshortcuts == []


# --- _install_shortcuts (aiguillage selon le mode) --------------------------

def test_install_disabled_removes_and_installs_nothing(fake_keyboard):
    w = _win(main.MODE_DISABLED, existing_shortcuts=[FakeQShortcut()])
    w._install_shortcuts()
    assert fake_keyboard.unhook_calls == 1        # désenregistrement quand même
    assert w.calls["focused"] == 0
    assert w.calls["global"] == 0
    assert w._qshortcuts == []


def test_install_focused_removes_then_installs_focused(fake_keyboard):
    w = _win(main.MODE_FOCUSED, existing_shortcuts=[FakeQShortcut()])
    w._install_shortcuts()
    assert fake_keyboard.unhook_calls == 1        # ancien mécanisme retiré d'abord
    assert w.calls["focused"] == 1
    assert w.calls["global"] == 0


def test_install_global_removes_then_installs_global(fake_keyboard):
    w = _win(main.MODE_GLOBAL, existing_shortcuts=[FakeQShortcut()])
    w._install_shortcuts()
    assert fake_keyboard.unhook_calls == 1
    assert w.calls["global"] == 1
    assert w.calls["focused"] == 0


def test_reinstall_clears_previous_qshortcuts_before_installing(fake_keyboard):
    # Ré-enregistrement : les anciens QShortcut sont désactivés/supprimés avant
    # que le nouveau mécanisme ne soit installé (pas d'empilement).
    old = FakeQShortcut()
    w = _win(main.MODE_GLOBAL, existing_shortcuts=[old])
    w._install_shortcuts()
    assert old.deleted is True
    assert w._qshortcuts == []
    assert w.calls["global"] == 1
