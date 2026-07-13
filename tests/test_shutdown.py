"""Fermeture avec requêtes actives (point 30).

Deux garanties de l'arrêt propre (closeEvent, point 11) sont testables comme
logique pure (vraies méthodes, faux ``self``) :

- pendant l'arrêt (``shutting_down``), ``_submit`` refuse toute NOUVELLE action
  réseau (aucun worker créé) — les requêtes déjà en vol, elles, sont débloquées
  par NetworkManager.stop/_drain_pending (cf. test_network_manager) ;
- ``_release_counter_blocking`` libère le comptoir côté serveur avec un POST
  ``remove_staff`` borné (timeout court) et n'échoue jamais la fermeture.

``_submit`` sert aussi de garde anti-doublon (clé déjà active) : on vérifie les
deux refus (arrêt en cours / action identique déjà en cours).
"""

import logging
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402
from net_result import NetResult  # noqa: E402


# --- _submit : refus des nouvelles actions pendant l'arrêt / doublons --------

def _wsub(shutting_down=False, active=False):
    w = types.SimpleNamespace(
        logger=logging.getLogger("test.shutdown.submit"),
        shutting_down=shutting_down,
        made=0,
    )

    def _make(*a, **k):
        w.made += 1
        return MagicMock()   # faux RequestHandle (result/finished/start mockés)

    w.make_request_thread = _make
    w._tasks = types.SimpleNamespace(is_active=lambda key: active, add=lambda *a: None)
    w._submit = types.MethodType(main.MainWindow._submit, w)
    return w


def test_submit_refused_while_shutting_down():
    w = _wsub(shutting_down=True)
    handle = w._submit("http://srv/a", key="next")
    assert handle is None
    assert w.made == 0          # aucune requête créée pendant l'arrêt


def test_submit_refused_when_same_action_already_active():
    w = _wsub(active=True)
    handle = w._submit("http://srv/a", key="next")
    assert handle is None
    assert w.made == 0          # pas de doublon d'une action identique en cours


def test_submit_creates_request_when_idle():
    w = _wsub(shutting_down=False, active=False)
    handle = w._submit("http://srv/a", key="next")
    assert handle is not None
    assert w.made == 1


# --- _release_counter_blocking : libération bornée du comptoir ---------------

def _wrel(result=None, raises=None):
    w = types.SimpleNamespace(
        logger=logging.getLogger("test.shutdown.release"),
        web_url="http://srv",
        counter_id=3,
        network_manager=MagicMock(),
    )
    if raises is not None:
        w.network_manager.request_blocking.side_effect = raises
    else:
        w.network_manager.request_blocking.return_value = (
            result or NetResult(status=200, text="", content_type=None))
    w._release_counter_blocking = types.MethodType(
        main.MainWindow._release_counter_blocking, w)
    return w


def test_release_counter_posts_remove_staff_bounded():
    w = _wrel()
    w._release_counter_blocking()
    call = w.network_manager.request_blocking.call_args
    assert call.args[0] == "http://srv/app/counter/remove_staff"
    assert call.kwargs["method"] == "POST"
    assert call.kwargs["data"] == {"counter_id": 3}
    # Borné : un timeout court est fourni (la fermeture ne peut pas rester bloquée).
    assert call.kwargs.get("timeout") is not None


def test_release_counter_survives_server_error():
    w = _wrel(result=NetResult.from_response(500, "boom"))
    w._release_counter_blocking()   # ne doit pas lever (fermeture toujours possible)


def test_release_counter_survives_network_exception():
    w = _wrel(raises=RuntimeError("connexion perdue"))
    w._release_counter_blocking()   # exception avalée -> fermeture non bloquée


def test_release_counter_noop_without_network_manager():
    w = types.SimpleNamespace(
        logger=logging.getLogger("test.shutdown.release"),
        web_url="http://srv", counter_id=3)
    w._release_counter_blocking = types.MethodType(
        main.MainWindow._release_counter_blocking, w)
    w._release_counter_blocking()   # pas de network_manager : garde hasattr -> no-op
