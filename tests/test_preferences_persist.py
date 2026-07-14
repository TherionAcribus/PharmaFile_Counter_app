"""Persistance vérifiée des préférences (point 10).

_sync_and_verify force l'écriture QSettings (sync) et vérifie qu'elle a abouti
(status). Il renvoie True si tout va bien, sinon affiche une erreur et renvoie
False — c'est ce qui permet à _finalize_save de ne PAS confirmer (accept) un
enregistrement qui a en réalité échoué, et d'éviter un état mixte entre QSettings
et le magasin de secrets.
"""

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import preferences  # noqa: E402


def _dialog():
    w = types.SimpleNamespace()
    w._sync_and_verify = types.MethodType(preferences.PreferencesDialog._sync_and_verify, w)
    return w


class FakeSettings:
    def __init__(self, status):
        self._status = status
        self.synced = 0

    def sync(self):
        self.synced += 1

    def status(self):
        return self._status


def test_sync_and_verify_returns_true_when_no_error():
    w = _dialog()
    settings = FakeSettings(preferences.QSettings.NoError)
    assert w._sync_and_verify(settings) is True
    assert settings.synced == 1  # sync() a bien été appelé


def test_sync_and_verify_reports_error_and_returns_false(monkeypatch):
    shown = {"n": 0}
    monkeypatch.setattr(preferences.QMessageBox, "critical",
                        lambda *a, **k: shown.__setitem__("n", shown["n"] + 1))
    w = _dialog()
    settings = FakeSettings(preferences.QSettings.AccessError)
    assert w._sync_and_verify(settings) is False
    assert shown["n"] == 1  # l'utilisateur est averti de l'échec de persistance
