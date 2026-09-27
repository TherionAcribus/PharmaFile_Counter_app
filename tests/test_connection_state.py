"""F3 — état de connexion : fausses alertes, minuteur périmé, état recréé.

Couvre, sur les VRAIES méthodes de MainWindow (avec un faux ``self`` minimal ou
de vrais widgets offscreen), les défauts constatés à l'audit :

- une coupure courte suivie d'une reconnexion ne doit PAS afficher plus tard
  « déconnecté » : le minuteur d'alerte est arrêté et le drapeau relâché ;
- le drapeau ``disconnect_notification_shown`` est réinitialisé à la
  reconnexion, sinon la coupure suivante n'était jamais signalée ;
- un timeout déjà planifié avant la reconnexion est ignoré ;
- l'état temps réel vit dans le contrôleur : un indicateur recréé (changement
  d'orientation, préférences) reflète « disconnected »/« connecting », pas le
  « connected » par défaut du constructeur ;
- tant que la liaison n'est pas « connected », la file est marquée durablement
  « non actualisée » (titre du panneau + bouton) ;
- la reconnexion déclenche une resync unique (drapeau consommé).
"""

import logging
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main  # noqa: E402
from main_window_ui import ConnectionStatusIndicator  # noqa: E402


class _FakeTimer:
    """Minuteur factice (isActive/start/stop) pour suivre l'armement réel."""

    def __init__(self):
        self.active = False
        self.started_with = []

    def isActive(self):
        return self.active

    def start(self, msec):
        self.active = True
        self.started_with.append(msec)

    def stop(self):
        self.active = False


def _win(socket_was_disconnected=False):
    """Faux ``self`` de MainWindow avec minuteur, indicateur et collecteurs."""
    w = types.SimpleNamespace(
        logger=logging.getLogger("test.connexion"),
        socket_was_disconnected=socket_was_disconnected,
        disconnect_notification_shown=False,
        disconnect_timer=_FakeTimer(),
        current_reconnection_attempts=0,
        notification_after_deconnection=5,
        list_patients=[{"id": 1}, {"id": 2}],
        calls={"resync": 0, "status": [], "notify": []},
    )
    w.connection_indicator = types.SimpleNamespace(
        set_status=lambda *a: w.calls["status"].append(a))
    w.show_notification = lambda data, internal=False: w.calls[
        "notify"].append(data.get("origin"))
    w._request_resync = lambda: w.calls.__setitem__(
        "resync", w.calls["resync"] + 1)
    for name in ("handle_socket_connection", "_handle_connection_lost",
                 "_handle_disconnection_timeout", "_set_rt_status",
                 "_restore_connection_indicator", "_update_list_freshness",
                 "_update_patient_count_label"):
        setattr(w, name, types.MethodType(getattr(main.MainWindow, name), w))
    return w


# --- Coupure courte -> reconnexion : pas de fausse alerte -------------------

def test_reconnect_before_delay_stops_timer():
    w = _win()
    w._handle_connection_lost(0)
    assert w.disconnect_timer.isActive()          # délai de grâce armé
    w.handle_socket_connection(True)
    assert not w.disconnect_timer.isActive()      # minuteur arrêté
    assert w.disconnect_notification_shown is False
    assert w._rt_status == "connected"


def test_stale_timeout_after_reconnect_is_ignored():
    # Le timeout expirait après la reconnexion -> affichait « déconnecté » et
    # repassait l'icône au rouge alors que la liaison était revenue.
    w = _win()
    w._handle_connection_lost(0)
    w.handle_socket_connection(True)
    w._handle_disconnection_timeout()             # timeout périmé
    assert w.calls["notify"] == []                # aucune alerte
    assert w.calls["status"][-1] == ("connected", 0)
    assert w._rt_status == "connected"


def test_no_restore_notification_when_alert_never_shown():
    # Coupure plus courte que le délai : pas d'annonce « rétablie » non plus.
    w = _win()
    w._handle_connection_lost(0)
    w.handle_socket_connection(True, display_notification=True)
    assert "socket_connection_true" not in w.calls["notify"]


# --- Délai expiré : alerte, puis « rétablie » à la reconnexion ---------------

def test_timeout_emits_alert_once():
    w = _win()
    w._handle_connection_lost(0)
    w._handle_disconnection_timeout()
    assert w.calls["notify"].count("socket_connection_false") == 1
    w._handle_disconnection_timeout()             # re-tirage sans effet
    assert w.calls["notify"].count("socket_connection_false") == 1
    assert w._rt_status == "disconnected"


def test_reconnect_after_alert_announces_restore():
    w = _win()
    w._handle_connection_lost(0)
    w._handle_disconnection_timeout()
    w.handle_socket_connection(True)
    assert "socket_connection_true" in w.calls["notify"]
    assert w.disconnect_notification_shown is False


def test_flag_reset_lets_next_outage_notify():
    # Le drapeau n'était jamais relâché : une seconde coupure n'armait plus le
    # minuteur et restait invisible.
    w = _win()
    w._handle_connection_lost(0)
    w._handle_disconnection_timeout()
    w.handle_socket_connection(True)

    w._handle_connection_lost(0)                  # deuxième coupure
    assert w.disconnect_timer.isActive()
    w._handle_disconnection_timeout()
    assert w.calls["notify"].count("socket_connection_false") == 2


# --- Resynchronisation : une seule par cycle de coupure ---------------------

def test_reconnect_resyncs_once():
    w = _win()
    w.handle_socket_connection(False, 0, False)
    w.handle_socket_connection(True)
    w.handle_socket_connection(True)              # évènement dupliqué
    assert w.calls["resync"] == 1


# --- État conservé dans le contrôleur, restauré à la reconstruction ---------

def test_status_kept_in_controller_not_widget():
    w = _win()
    w.handle_socket_connection(False, 4, False)
    assert w._rt_status == "disconnected"
    assert w._rt_attempts == 4
    # Nouvel indicateur (reconstruction) : l'état mémorisé est réimposé.
    new_indicator = types.SimpleNamespace(
        set_status=lambda *a: w.calls["status"].append(a))
    w.connection_indicator = new_indicator
    w._restore_connection_indicator()
    assert w.calls["status"][-1] == ("disconnected", 4)


def test_real_indicator_restores_disconnect_state():
    # Avec le VRAI widget : après reconstruction hors ligne, l'indicateur doit
    # être rouge (disconnected) et non vert (défaut « connected »).
    indicator = ConnectionStatusIndicator()
    assert indicator.status == "connected"        # défaut du constructeur
    w = _win()
    w.handle_socket_connection(False, 2, False)
    w.connection_indicator = indicator
    w._restore_connection_indicator()
    assert indicator.status == "disconnected"
    assert indicator.reconnection_attempts == 2
    assert "connexion" in indicator.accessibleName().lower()
    indicator.deleteLater()


def test_indicator_connecting_before_first_confirmation():
    # Nouvelle fenêtre avant la première confirmation du socket : « connecting »
    # (orange), pas « connected » (vert mensonger).
    assert main.MainWindow._rt_status == "connecting"


# --- Marqueur durable « non actualisée » -------------------------------------

def test_list_marked_stale_while_disconnected():
    w = _win()
    titles = []
    w.patient_list_dock = types.SimpleNamespace(
        setWindowTitle=lambda t: titles.append(t))
    texts = []
    w.btn_choose_patient = types.SimpleNamespace(
        setText=lambda t: texts.append(t),
        setToolTip=lambda _t: None)

    w.handle_socket_connection(False, 0, False)
    assert titles[-1] == "Liste des patients — non actualisée"
    assert texts[-1] == "Patients (2) — non actualisée"

    w.handle_socket_connection(True)
    assert titles[-1] == "Liste des patients"
    assert texts[-1] == "Patients (2)"


def test_list_marked_stale_while_connecting():
    w = _win()
    titles = []
    w.patient_list_dock = types.SimpleNamespace(
        setWindowTitle=lambda t: titles.append(t))
    w.handle_socket_connection(None, 3)
    assert titles[-1] == "Liste des patients — non actualisée"
