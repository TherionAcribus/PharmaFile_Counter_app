"""Câblage réel du traitement des raccourcis (point 27), désormais porté par
``ShortcutManager`` (point 10.10).

On appelle les vraies méthodes _dispatch / perform / _register_global_hotkeys
avec un faux ``self`` minimal (et une fausse fenêtre) : on vérifie la
confirmation des actions sensibles, le retour visuel, le mapping action -> clic
unique (jamais deux déclenchements), et la collecte des échecs d'enregistrement
global (avertissement quand keyboard/Windows refuse).
"""

import logging
import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import shortcut_manager  # noqa: E402
from shortcut_manager import ShortcutManager  # noqa: E402


class FakeButton:
    def __init__(self):
        self.clicks = 0

    def animateClick(self):
        self.clicks += 1


# --- _dispatch_shortcut : confirmation + retour visuel ----------------------

class FakeDispatch:
    def __init__(self, confirm_pref=False, feedback=False, confirm_result=True):
        # Les préférences vivent sur la fenêtre, le manager les lit à la volée.
        self.window = types.SimpleNamespace(
            confirm_sensitive_shortcuts=confirm_pref, shortcut_feedback=feedback)
        self.confirm_result = confirm_result
        self.logger = logging.getLogger("test.shortcut_dispatch")
        self.performed = []
        self.feedbacks = []
        self.confirm_calls = []
        self._dispatch_shortcut = types.MethodType(ShortcutManager._dispatch, self)

    # Helpers stubbés (les vrais ouvrent QMessageBox / notifications).
    def _confirm(self, label):
        self.confirm_calls.append(label)
        return self.confirm_result

    def _show_feedback(self, label):
        self.feedbacks.append(label)

    def perform(self, action):
        self.performed.append(action)


def test_non_sensitive_action_runs_without_confirmation():
    w = FakeDispatch(confirm_pref=True)  # pref ON mais action non sensible
    w._dispatch_shortcut("next")
    assert w.performed == ["next"]
    assert w.confirm_calls == []


def test_feedback_shown_when_enabled():
    w = FakeDispatch(feedback=True)
    w._dispatch_shortcut("pause")
    assert w.feedbacks == ["Pause"]
    assert w.performed == ["pause"]


def test_feedback_absent_when_disabled():
    w = FakeDispatch(feedback=False)
    w._dispatch_shortcut("pause")
    assert w.feedbacks == []


def test_sensitive_action_confirmed_runs():
    w = FakeDispatch(confirm_pref=True, confirm_result=True)
    w._dispatch_shortcut("deconnect")
    assert w.confirm_calls == ["Déconnexion"]
    assert w.performed == ["deconnect"]


def test_sensitive_action_declined_does_not_run():
    w = FakeDispatch(confirm_pref=True, confirm_result=False)
    w._dispatch_shortcut("deconnect")
    assert w.confirm_calls == ["Déconnexion"]
    assert w.performed == []   # action bloquée


def test_sensitive_action_without_confirm_pref_runs_directly():
    w = FakeDispatch(confirm_pref=False)
    w._dispatch_shortcut("deconnect")
    assert w.confirm_calls == []
    assert w.performed == ["deconnect"]


# --- _perform_shortcut_action : mapping + clic unique -----------------------

class FakeWindow:
    def __init__(self):
        self.btn_next = FakeButton()
        self.btn_validate = FakeButton()
        self.btn_pause = FakeButton()
        self.recall_calls = 0
        self.deconnect_calls = 0

    def recall(self):
        self.recall_calls += 1

    def deconnection(self):
        self.deconnect_calls += 1


class FakePerform:
    """Faux manager : la fenêtre porte les boutons, le manager exécute."""

    def __init__(self):
        self.window = FakeWindow()
        self.logger = logging.getLogger("test.shortcut_perform")
        self._perform_shortcut_action = types.MethodType(ShortcutManager.perform, self)

    def __getattr__(self, name):
        # Accès direct aux attributs de la fenêtre pour garder les assertions
        # des tests lisibles (w.btn_next.clicks, w.recall_calls…).
        return getattr(self.window, name)


def test_perform_next_clicks_once():
    w = FakePerform()
    w._perform_shortcut_action("next")
    assert (w.btn_next.clicks, w.btn_validate.clicks, w.btn_pause.clicks) == (1, 0, 0)


def test_perform_validate_and_pause():
    w = FakePerform()
    w._perform_shortcut_action("validate")
    w._perform_shortcut_action("pause")
    assert w.btn_validate.clicks == 1
    assert w.btn_pause.clicks == 1


def test_perform_recall_and_deconnect_once():
    w = FakePerform()
    w._perform_shortcut_action("recall")
    w._perform_shortcut_action("deconnect")
    assert w.recall_calls == 1
    assert w.deconnect_calls == 1


def test_perform_missing_button_is_safe():
    # Sur l'écran de connexion, les boutons n'existent pas : pas de crash.
    w = FakePerform()
    del w.window.btn_next
    w._perform_shortcut_action("next")  # ne doit pas lever


# --- _register_global_hotkeys : traduction + collecte des échecs ------------

class FakeKeyboard:
    def __init__(self, fail_on=None):
        self.registered = []
        self.fail_on = fail_on  # hotkey (traduit) qui doit lever

    def add_hotkey(self, hotkey, callback, args=()):
        if hotkey == self.fail_on:
            raise ValueError("touche invalide")
        self.registered.append((hotkey, args))


class FakeSignal:
    def __init__(self):
        self.emitted = []

    def emit(self, payload):
        self.emitted.append(payload)


class FakeRegister:
    def __init__(self, texts):
        (next_text, validate_text, pause_text, recall_text, deconnect_text) = texts
        self.window = types.SimpleNamespace(
            next_patient_shortcut=next_text,
            validate_patient_shortcut=validate_text,
            pause_shortcut=pause_text,
            recall_shortcut=recall_text,
            deconnect_shortcut=deconnect_text)
        self.logger = logging.getLogger("test.shortcut_register")
        self.registration_failed = FakeSignal()
        self.shortcut_registration_failed = self.registration_failed
        self.items = types.MethodType(ShortcutManager.items, self)
        self._emit = types.MethodType(ShortcutManager._emit, self)
        self._register_global_hotkeys = types.MethodType(
            ShortcutManager._register_global_hotkeys, self)


def test_register_translates_and_skips_empty(monkeypatch):
    fake_kb = FakeKeyboard()
    monkeypatch.setattr(shortcut_manager, "keyboard", fake_kb)
    # deconnect vide -> ignoré (rien à enregistrer).
    w = FakeRegister(["Alt+S", "Alt+V", "Ctrl+Maj+P", "Alt+R", ""])
    w._register_global_hotkeys()
    registered = dict(fake_kb.registered)
    assert "alt+s" in registered
    assert "ctrl+shift+p" in registered      # Maj -> shift, ordre trié
    assert len(fake_kb.registered) == 4       # le vide n'est pas enregistré
    assert w.shortcut_registration_failed.emitted == []  # aucun échec


def test_register_collects_failures(monkeypatch):
    # keyboard refuse « alt+v » (validate) -> collecté et signalé.
    fake_kb = FakeKeyboard(fail_on="alt+v")
    monkeypatch.setattr(shortcut_manager, "keyboard", fake_kb)
    w = FakeRegister(["Alt+S", "Alt+V", "Alt+P", "Alt+R", "Alt+D"])
    w._register_global_hotkeys()
    assert len(w.shortcut_registration_failed.emitted) == 1
    failures = w.shortcut_registration_failed.emitted[0]
    # Un seul échec, portant le libellé de l'action « validate ».
    assert len(failures) == 1
    label, text, _err = failures[0]
    assert label == "Valider le patient"
    assert text == "Alt+V"
    # Les autres ont bien été enregistrés.
    assert len(fake_kb.registered) == 4


def test_register_carries_action_args(monkeypatch):
    fake_kb = FakeKeyboard()
    monkeypatch.setattr(shortcut_manager, "keyboard", fake_kb)
    w = FakeRegister(["Alt+S", "", "", "", ""])
    w._register_global_hotkeys()
    # Le callback reçoit le nom de l'action en argument (pour émettre le signal).
    assert fake_kb.registered == [("alt+s", ("next",))]
