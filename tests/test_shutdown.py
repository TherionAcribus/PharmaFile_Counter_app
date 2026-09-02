"""Fermeture avec requêtes actives (point 30).

Deux garanties de l'arrêt propre (closeEvent, point 11) sont testables comme
logique pure :

- pendant l'arrêt (``shutting_down``), la couche d'accès refuse toute NOUVELLE
  requête (aucun worker créé) — les requêtes déjà en vol, elles, sont débloquées
  par NetworkManager.stop/_drain_pending (cf. test_network_manager) ;
- ``release_counter_blocking`` libère le comptoir côté serveur avec un POST
  ``remove_staff`` borné (timeout court) et n'échoue jamais la fermeture.

``CounterApi._submit`` sert aussi de garde anti-doublon (clé déjà active) : on
vérifie les deux refus (arrêt en cours / action identique déjà en cours).

Depuis le point 10.8, ces garanties vivent dans ``counter_api`` et non plus dans
``MainWindow`` ; les derniers tests du fichier vérifient que MainWindow délègue
bien (et n'a pas gardé de primitive réseau en propre).
"""

import logging
import os
import sys
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402
from counter_api import CounterApi  # noqa: E402
from net_result import NetResult  # noqa: E402


# --- refus des nouvelles requêtes pendant l'arrêt / doublons ----------------

def _api(shutting_down=False, active=False, network_manager=None):
    made = []
    nm = network_manager or MagicMock()
    if network_manager is None:
        nm.make_handle.side_effect = lambda *a, **k: (made.append(1), MagicMock())[1]
    api = CounterApi(
        nm,
        tasks=types.SimpleNamespace(is_active=lambda key: active, add=lambda *a: None),
        url_provider=lambda: "http://srv",
        counter_id_provider=lambda: 3,
        logger=logging.getLogger("test.shutdown.submit"),
        is_shutting_down=lambda: shutting_down,
    )
    api.made = made
    return api


def test_submit_refused_while_shutting_down():
    api = _api(shutting_down=True)
    assert api.pause_current_patient(7) is None
    assert api.made == []          # aucune requête créée pendant l'arrêt


def test_submit_refused_when_same_action_already_active():
    api = _api(active=True)
    assert api.pause_current_patient(7) is None
    assert api.made == []          # pas de doublon d'une action identique en cours


def test_submit_creates_request_when_idle():
    api = _api()
    assert api.pause_current_patient(7) is not None
    assert len(api.made) == 1


def test_toutes_les_actions_sont_refusees_pendant_l_arret():
    """Aucune action ne doit contourner la garde d'arrêt."""
    api = _api(shutting_down=True)
    appels = [
        lambda: api.validate_and_call_next(),
        lambda: api.validate_current_patient(7),
        lambda: api.validate_queued_patient(7),
        lambda: api.pause_current_patient(7),
        lambda: api.relaunch_call(),
        lambda: api.call_specific_patient(7),
        lambda: api.put_standing(7),
        lambda: api.delete_patient(7),
        lambda: api.login_staff("AB", False),
        lambda: api.logout_staff(),
        lambda: api.fetch_staff(),
    ]
    assert [appel() for appel in appels] == [None] * len(appels)
    assert api.made == []


# --- release_counter_blocking : libération bornée du comptoir ---------------

def _rel(result=None, raises=None):
    nm = MagicMock()
    if raises is not None:
        nm.request_blocking.side_effect = raises
    else:
        nm.request_blocking.return_value = (
            result or NetResult(status=200, text="", content_type=None))
    return CounterApi(nm, tasks=MagicMock(),
                      url_provider=lambda: "http://srv",
                      counter_id_provider=lambda: 3,
                      logger=logging.getLogger("test.shutdown.release"))


def test_release_counter_posts_remove_staff_bounded():
    api = _rel()
    assert api.release_counter_blocking() is True
    call = api.network_manager.request_blocking.call_args
    assert call.args[0] == "http://srv/app/counter/remove_staff"
    assert call.kwargs["method"] == "POST"
    assert call.kwargs["data"] == {"counter_id": 3}
    # Borné : un timeout court est fourni (la fermeture ne peut pas rester bloquée).
    assert call.kwargs.get("timeout") is not None


def test_release_counter_cible_un_ancien_comptoir():
    """Changement de connexion : on libère l'ANCIEN serveur/comptoir."""
    api = _rel()
    api.release_counter_blocking(url="http://ancien", counter_id=9)
    call = api.network_manager.request_blocking.call_args
    assert call.args[0] == "http://ancien/app/counter/remove_staff"
    assert call.kwargs["data"] == {"counter_id": 9}


def test_release_counter_survives_server_error():
    api = _rel(result=NetResult.from_response(500, "boom"))
    assert api.release_counter_blocking() is False   # ne doit pas lever


def test_release_counter_survives_network_exception():
    api = _rel(raises=RuntimeError("connexion perdue"))
    assert api.release_counter_blocking() is False   # exception avalée


def test_release_counter_noop_without_api():
    """MainWindow appelée avant la construction de la couche d'accès."""
    w = types.SimpleNamespace(logger=logging.getLogger("test.shutdown.release"))
    w._release_counter_blocking = types.MethodType(
        main.MainWindow._release_counter_blocking, w)
    w._release_counter_blocking()   # garde hasattr -> no-op


def test_main_window_delegue_la_liberation():
    api = MagicMock()
    w = types.SimpleNamespace(logger=logging.getLogger("test.shutdown.release"), api=api)
    w._release_counter_blocking = types.MethodType(
        main.MainWindow._release_counter_blocking, w)
    w._release_counter_blocking(url="http://ancien", counter_id=9)
    api.release_counter_blocking.assert_called_once_with(url="http://ancien", counter_id=9)


def test_main_window_na_plus_de_primitive_reseau():
    """Le point 10.8 sort les primitives HTTP de MainWindow : elles ne doivent
    pas y revenir (sinon la garde d'arrêt et l'anti-doublon se dédoublent)."""
    for nom in ("_submit", "make_request_thread"):
        assert not hasattr(main.MainWindow, nom), nom
