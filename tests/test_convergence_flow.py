"""Flux critiques de convergence temps réel (point 30).

Couvre, sur les VRAIES méthodes de MainWindow (avec un faux ``self`` minimal, sans
widgets ni réseau), trois comportements essentiels quand Socket.IO n'est qu'une
notification et pas la source de vérité :

- ``new_patient`` : garde de révision (message périmé/dupliqué ignoré, trou de
  révision -> resynchronisation, évènement mal formé sans crash) ;
- ``handle_socket_connection`` : après une perte réelle de connexion, la
  reconnexion déclenche une resynchronisation (SocketIO ne rejoue pas les
  évènements manqués) ;
- ``_on_resync_ready`` : une snapshot plus ancienne que l'état connu n'est jamais
  appliquée, et une passe en attente est relancée une seule fois (coalescing).
"""

import logging
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402
from resync_coordinator import ResyncCoordinator  # noqa: E402


# --- new_patient : garde de révision + robustesse (#6, #7, #8) ---------------

def _wnp(queue_revision=None):
    w = types.SimpleNamespace(
        logger=logging.getLogger("test.convergence"),
        queue_revision=queue_revision,
        list_patients=[],
        shutting_down=False,
        calls={"refresh": 0, "resync": 0},
    )
    w.refresh_patient_lists = lambda: w.calls.__setitem__("refresh", w.calls["refresh"] + 1)
    w._request_resync = lambda: w.calls.__setitem__("resync", w.calls["resync"] + 1)
    w.new_patient = types.MethodType(main.MainWindow.new_patient, w)
    return w


def test_new_patient_first_revision_establishes_and_applies():
    w = _wnp(queue_revision=None)
    w.new_patient([{"id": 1}], revision=5)
    assert w.queue_revision == 5
    assert w.list_patients == [{"id": 1}]
    assert w.calls["refresh"] == 1
    assert w.calls["resync"] == 0


def test_new_patient_advances_revision():
    w = _wnp(queue_revision=5)
    w.new_patient([{"id": 2}], revision=6)
    assert w.queue_revision == 6
    assert w.calls["refresh"] == 1
    assert w.calls["resync"] == 0


@pytest.mark.parametrize("stale", [5, 4, 1])
def test_new_patient_ignores_stale_or_duplicate_revision(stale):
    # rev <= révision connue : message périmé ou dupliqué -> ignoré (aucune MAJ).
    w = _wnp(queue_revision=5)
    w.new_patient([{"id": 99}], revision=stale)
    assert w.queue_revision == 5          # inchangée
    assert w.list_patients == []          # pas de MAJ de la liste
    assert w.calls["refresh"] == 0
    assert w.calls["resync"] == 0


def test_new_patient_revision_gap_triggers_resync():
    # Trou de révision (au moins un évènement manqué) : on ne fait pas confiance
    # à ce seul message, on recharge l'état autoritatif.
    w = _wnp(queue_revision=5)
    w.new_patient([{"id": 3}], revision=7)   # 7 > 5 + 1
    assert w.queue_revision == 7
    assert w.calls["resync"] == 1
    assert w.calls["refresh"] == 0           # pas d'application directe


def test_new_patient_without_revision_is_applied():
    # Évènement hérité sans numéro de révision : appliqué tel quel.
    w = _wnp(queue_revision=5)
    w.new_patient([{"id": 4}], revision=None)
    assert w.list_patients == [{"id": 4}]
    assert w.calls["refresh"] == 1


@pytest.mark.parametrize("payload", [None, "pas une liste", {"id": 1}, 42])
def test_new_patient_malformed_payload_does_not_crash(payload):
    # Payload mal formé (pas une liste) : ne doit pas lever (le log de debug est
    # gardé par isinstance) ; la garde de révision fonctionne quand même.
    w = _wnp(queue_revision=None)
    w.new_patient(payload, revision=1)   # ne doit pas lever
    assert w.queue_revision == 1
    assert w.calls["refresh"] == 1


# --- handle_socket_connection : reconnexion -> resynchronisation (#7) --------

def _wsc(socket_was_disconnected=False, notify=True):
    w = types.SimpleNamespace(
        logger=logging.getLogger("test.convergence.socket"),
        socket_was_disconnected=socket_was_disconnected,
        disconnect_notification_shown=False,
        notification_connection=notify,
        calls={"resync": 0, "status": [], "notify": []},
    )
    w.connection_indicator = types.SimpleNamespace(
        set_status=lambda *a: w.calls["status"].append(a))
    w.show_notification = lambda data, internal=False: w.calls["notify"].append(data.get("origin"))
    w._request_resync = lambda: w.calls.__setitem__("resync", w.calls["resync"] + 1)
    w.handle_socket_connection = types.MethodType(
        main.MainWindow.handle_socket_connection, w)
    return w


def test_disconnect_marks_flag_and_updates_indicator():
    w = _wsc(socket_was_disconnected=False)
    w.handle_socket_connection(False)
    assert w.socket_was_disconnected is True
    assert w.calls["status"][-1] == ("disconnected", 0)
    assert "socket_connection_false" in w.calls["notify"]


def test_reconnect_after_disconnect_triggers_resync():
    w = _wsc(socket_was_disconnected=True)
    w.handle_socket_connection(True)
    assert w.calls["resync"] == 1                 # rattrapage de l'état
    assert w.socket_was_disconnected is False     # drapeau consommé
    assert w.calls["status"][-1] == ("connected",)


def test_first_connect_without_prior_loss_does_not_resync():
    # Connexion initiale (jamais perdue) : pas de resync inutile.
    w = _wsc(socket_was_disconnected=False)
    w.handle_socket_connection(True)
    assert w.calls["resync"] == 0
    assert w.calls["status"][-1] == ("connected",)


def test_connecting_status_sets_indicator_only():
    w = _wsc()
    w.handle_socket_connection(None, reconnection_attempts=3)
    assert w.calls["status"][-1] == ("connecting", 3)
    assert w.calls["resync"] == 0


# --- _on_resync_ready : rejet d'un snapshot plus ancien (#8) -----------------

def _wrr(queue_revision=10, pending=False):
    coord = ResyncCoordinator()
    coord.request()            # une resync est active
    if pending:
        coord.request()        # une passe supplémentaire demandée entretemps
    w = types.SimpleNamespace(
        logger=logging.getLogger("test.convergence.resync"),
        queue_revision=queue_revision,
        shutting_down=False,
        _resync=coord,
        calls={"apply": 0, "resync": 0},
    )
    w._apply_resync_state = lambda state: w.calls.__setitem__("apply", w.calls["apply"] + 1)
    w._request_resync = lambda: w.calls.__setitem__("resync", w.calls["resync"] + 1)
    w._on_resync_ready = types.MethodType(main.MainWindow._on_resync_ready, w)
    return w


@pytest.mark.parametrize("revision", [12, 10])
def test_fresh_or_equal_snapshot_is_applied(revision):
    w = _wrr(queue_revision=10)
    w._on_resync_ready({"revision": revision})
    assert w.calls["apply"] == 1


def test_stale_snapshot_is_rejected():
    # Snapshot plus ancien que l'état connu : jamais appliqué.
    w = _wrr(queue_revision=10)
    w._on_resync_ready({"revision": 8})
    assert w.calls["apply"] == 0


def test_none_state_is_safe():
    w = _wrr(queue_revision=10)
    w._on_resync_ready(None)          # ne doit pas lever
    assert w.calls["apply"] == 0


def test_pending_pass_relaunches_once():
    # Une passe demandée pendant la resync -> relance unique à la fin (coalescing).
    w = _wrr(queue_revision=10, pending=True)
    w._on_resync_ready({"revision": 11})
    assert w.calls["apply"] == 1
    assert w.calls["resync"] == 1


def test_no_pending_pass_does_not_relaunch():
    w = _wrr(queue_revision=10, pending=False)
    w._on_resync_ready({"revision": 11})
    assert w.calls["resync"] == 0
