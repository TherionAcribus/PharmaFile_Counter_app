"""Vérification de connexion avant enregistrement (point 8), configuration du poste.

- _on_connection_checked : n'enregistre (finalize) QUE si la connexion est OK ;
  en cas d'échec, avertit et ne persiste rien (« enregistré » non affiché).
- TokenCheckWorker.run : traduit correctement les réponses serveur en (ok, message).
"""

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import station_dialog  # noqa: E402


# --- _on_connection_checked -------------------------------------------------

def _dialog(loaded_for=("http://serveur", "secret"),
            pending_url="http://serveur", pending_secret="secret"):
    w = types.SimpleNamespace(calls={"finalize": 0, "counter_check": 0})
    w.status_label = types.SimpleNamespace(setText=lambda t: None)
    w.save_button = types.SimpleNamespace(setEnabled=lambda v: None)
    w.apply_button = types.SimpleNamespace(setEnabled=lambda v: None)
    # Le couple vérifié vient du snapshot (_pending_save), pas des champs —
    # restés éditables pendant le worker réseau (point 1).
    w._pending_save = {"settings": {"web_url": pending_url},
                       "app_secret": pending_secret}
    w._pending_pair = types.MethodType(
        station_dialog.StationDialog._pending_pair, w)
    w._counters_loaded_for = loaded_for
    # Attribut référencé par le chemin d'erreur (_focus) : la valeur importe
    # peu, l'objet est simplement passé au stub.
    w.url_input = object()
    w._focus = lambda *a, **k: None
    w._finalize_save = lambda: w.calls.__setitem__("finalize", w.calls["finalize"] + 1)
    w._validate_counter_then_save = (
        lambda: w.calls.__setitem__("counter_check", w.calls["counter_check"] + 1))
    w._on_connection_checked = types.MethodType(
        station_dialog.StationDialog._on_connection_checked, w)
    return w


def test_finalizes_when_connection_ok_and_list_fresh():
    w = _dialog(loaded_for=("http://serveur", "secret"))
    w._on_connection_checked(True, "")
    assert w.calls["finalize"] == 1
    assert w.calls["counter_check"] == 0


def test_connection_ok_but_list_never_loaded_checks_counter_first():
    w = _dialog(loaded_for=None)
    w._on_connection_checked(True, "")
    assert w.calls["counter_check"] == 1  # le comptoir est vérifié d'abord
    assert w.calls["finalize"] == 0       # rien n'est encore enregistré


def test_connection_ok_but_stale_list_checks_counter_first():
    w = _dialog(loaded_for=("http://autre", "secret"))
    w._on_connection_checked(True, "")
    assert w.calls["counter_check"] == 1
    assert w.calls["finalize"] == 0


def test_decision_uses_pending_snapshot_not_current_fields():
    """Régression : les champs pouvaient être modifiés pendant le worker, la
    comparaison avec la liste affichée doit porter sur le couple VÉRIFIÉ
    (snapshot), pas sur le texte courant."""
    w = _dialog(loaded_for=("http://serveur", "secret"))
    # L'utilisateur a édité l'URL pendant la vérification : le snapshot porte
    # une autre valeur que la liste affichée -> le comptoir est re-vérifié.
    w._pending_save["settings"]["web_url"] = "http://edite-pendant-le-test"
    w._on_connection_checked(True, "")
    assert w.calls["counter_check"] == 1
    assert w.calls["finalize"] == 0


def test_failure_does_not_finalize_and_warns(monkeypatch):
    warned = {"n": 0}
    monkeypatch.setattr(station_dialog.QMessageBox, "warning",
                        lambda *a, **k: warned.__setitem__("n", warned["n"] + 1))
    w = _dialog()
    w._on_connection_checked(False, "Serveur injoignable")
    assert w.calls["finalize"] == 0     # rien n'est enregistré
    assert warned["n"] == 1             # l'utilisateur est averti


# --- TokenCheckWorker.run ---------------------------------------------------

def _resp(status, payload=None, raises=False):
    def _post(*a, **k):
        if raises:
            raise station_dialog.requests.exceptions.RequestException("boom")
        return types.SimpleNamespace(
            status_code=status,
            json=lambda: (payload if payload is not None else {}),
        )
    return _post


def _run_check(monkeypatch, post):
    monkeypatch.setattr(station_dialog.requests, "post", post)
    worker = station_dialog.TokenCheckWorker("http://serveur", "secret")
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
