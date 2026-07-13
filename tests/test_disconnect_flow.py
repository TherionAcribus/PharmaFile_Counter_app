"""Fiabilité de la déconnexion du personnel (point 20).

On teste la logique (vraies méthodes, faux self) : la déconnexion utilisateur ne
bascule sur l'écran de connexion qu'APRÈS confirmation serveur ; en cas d'échec,
l'état précédent est restauré et on propose de réessayer (pas de faux état local).
"""

import logging
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402
from net_result import NetResult  # noqa: E402


def _win():
    w = types.SimpleNamespace(
        _disconnect_in_progress=False,
        staff_id=7,
        logger=logging.getLogger("test.disconnect"),
        _title="PharmaFile - Comptoir 1 - JD",
        calls={"disconnect_from_counter": 0, "deconnexion_interface": 0,
               "offer_retry": 0, "update_title": []},
        _dc_on_result="unset",
    )
    w.windowTitle = lambda: w._title
    w.setWindowTitle = lambda t: setattr(w, "_title", t)

    def _dc(on_result=None):
        w.calls["disconnect_from_counter"] += 1
        w._dc_on_result = on_result
    w.disconnect_from_counter = _dc
    w.deconnexion_interface = lambda: w.calls.__setitem__("deconnexion_interface",
                                                          w.calls["deconnexion_interface"] + 1)
    w.update_window_title = lambda name: w.calls["update_title"].append(name)
    w._offer_retry_disconnect = lambda result: w.calls.__setitem__("offer_retry",
                                                                    w.calls["offer_retry"] + 1)
    # Vraies méthodes liées.
    w.deconnection = types.MethodType(main.MainWindow.deconnection, w)
    w.handle_disconnect_result = types.MethodType(main.MainWindow.handle_disconnect_result, w)
    return w


def test_deconnection_shows_progress_and_waits_for_server():
    w = _win()
    w.deconnection()
    assert w._disconnect_in_progress is True
    assert "en cours" in w._title.lower()
    # La requête est envoyée AVEC le handler de finalisation...
    assert w.calls["disconnect_from_counter"] == 1
    assert w._dc_on_result is w.handle_disconnect_result
    # ...mais on N'a PAS encore basculé sur l'écran de connexion.
    assert w.calls["deconnexion_interface"] == 0


def test_deconnection_is_reentrancy_safe():
    w = _win()
    w.deconnection()
    w.deconnection()  # 2e appel ignoré tant qu'une déconnexion est en cours
    assert w.calls["disconnect_from_counter"] == 1


def test_success_finalizes_login_screen():
    w = _win()
    w._disconnect_in_progress = True
    w.handle_disconnect_result(NetResult(status=200, text="", content_type=None))
    assert w._disconnect_in_progress is False
    assert w.staff_id is None
    assert w.calls["deconnexion_interface"] == 1   # bascule confirmée
    assert w.calls["offer_retry"] == 0


@pytest.mark.parametrize("status", [0, 500, 401, 403])
def test_failure_keeps_state_and_offers_retry(status):
    w = _win()
    w._disconnect_in_progress = True
    w.staff_id = 7
    w.handle_disconnect_result(NetResult.from_response(status, "boom"))
    assert w._disconnect_in_progress is False
    # État local NON modifié : toujours connecté, pas de bascule.
    assert w.staff_id == 7
    assert w.calls["deconnexion_interface"] == 0
    # On propose de réessayer.
    assert w.calls["offer_retry"] == 1
