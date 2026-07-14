"""Vérification de connexion avant enregistrement (point 8) côté préférences.

- _on_connection_checked : n'enregistre (finalize) QUE si la connexion est OK ;
  en cas d'échec, avertit et ne persiste rien (« enregistré » non affiché).
- TokenCheckWorker.run : traduit correctement les réponses serveur en (ok, message).
"""

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import preferences  # noqa: E402


# --- _on_connection_checked -------------------------------------------------

def _dialog():
    w = types.SimpleNamespace(calls={"finalize": 0})
    w.status_label = types.SimpleNamespace(setText=lambda t: None)
    w.save_button = types.SimpleNamespace(setEnabled=lambda v: None)
    w._finalize_save = lambda: w.calls.__setitem__("finalize", w.calls["finalize"] + 1)
    w._on_connection_checked = types.MethodType(
        preferences.PreferencesDialog._on_connection_checked, w)
    return w


def test_finalizes_only_on_success():
    w = _dialog()
    w._on_connection_checked(True, "")
    assert w.calls["finalize"] == 1


def test_failure_does_not_finalize_and_warns(monkeypatch):
    warned = {"n": 0}
    monkeypatch.setattr(preferences.QMessageBox, "warning",
                        lambda *a, **k: warned.__setitem__("n", warned["n"] + 1))
    w = _dialog()
    w._on_connection_checked(False, "Serveur injoignable")
    assert w.calls["finalize"] == 0     # rien n'est enregistré
    assert warned["n"] == 1             # l'utilisateur est averti


# --- TokenCheckWorker.run ---------------------------------------------------

def _resp(status, payload=None, raises=False):
    def _post(*a, **k):
        if raises:
            raise preferences.requests.exceptions.RequestException("boom")
        return types.SimpleNamespace(
            status_code=status,
            json=lambda: (payload if payload is not None else {}),
        )
    return _post


def _run_check(monkeypatch, post):
    monkeypatch.setattr(preferences.requests, "post", post)
    worker = preferences.TokenCheckWorker("http://serveur", "secret")
    captured = {}
    worker.checked.connect(lambda ok, msg: captured.update(ok=ok, msg=msg))
    worker.run()
    return captured


def test_token_check_success(monkeypatch):
    got = _run_check(monkeypatch, _resp(200, {"token": "abc"}))
    assert got["ok"] is True


def test_token_check_missing_token(monkeypatch):
    got = _run_check(monkeypatch, _resp(200, {}))
    assert got["ok"] is False and "jeton" in got["msg"].lower()


def test_token_check_bad_secret(monkeypatch):
    got = _run_check(monkeypatch, _resp(401))
    assert got["ok"] is False and "secret" in got["msg"].lower()


def test_token_check_unexpected_status(monkeypatch):
    got = _run_check(monkeypatch, _resp(500))
    assert got["ok"] is False and "500" in got["msg"]


def test_token_check_network_error(monkeypatch):
    got = _run_check(monkeypatch, _resp(0, raises=True))
    assert got["ok"] is False and "injoignable" in got["msg"].lower()
