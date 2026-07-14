"""Réapplication des paramètres de connexion (point 8) : _reconnect_services /
_on_reconnect_ready, avec un faux ``self`` et un StartupWorker factice.

On vérifie l'ordre/les effets clés du changement de serveur/secret/comptoir :
libération de l'ANCIEN comptoir (avec l'ancienne URL/numéro) quand un staff y était,
invalidation du jeton et remise à zéro du staff/état, lancement d'un nouveau jeton,
et l'avertissement explicite quand la nouvelle connexion échoue.
"""

import logging
import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402


class FakeSignal:
    def __init__(self):
        self.slot = None

    def connect(self, fn):
        self.slot = fn


class FakeWorker:
    last = None

    def __init__(self, mw):
        self.mw = mw
        self.finished_startup = FakeSignal()
        self.started = False
        FakeWorker.last = self

    def start(self):
        self.started = True


class FakeSocket:
    def __init__(self):
        self.stopped = False

    def stop(self, timeout_ms=3000):
        self.stopped = True
        return True


class FakeNM:
    def __init__(self):
        self.cleared = False

    def clear_token(self):
        self.cleared = True


def _win(monkeypatch, staff_id=7):
    monkeypatch.setattr(main, "StartupWorker", FakeWorker)
    w = types.SimpleNamespace(
        logger=logging.getLogger("test.reconnect"),
        socket_io_client=FakeSocket(),
        network_manager=FakeNM(),
        staff_id=staff_id,
        app_token="ancien-jeton",
        queue_revision=42,
        my_patient={"id": 1},
        list_patients=[{"id": 1}],
        socket_was_disconnected=True,
        web_url="http://nouveau:5000",
        counter_id=2,
        released=[],
    )
    w._release_counter_blocking = lambda url=None, counter_id=None: w.released.append((url, counter_id))
    w._track_worker = lambda worker: worker
    w._on_reconnect_ready = lambda connected, state: None
    w._reconnect_services = types.MethodType(main.MainWindow._reconnect_services, w)
    return w


OLD = {"web_url": "http://ancien:5000", "app_secret": "s", "counter_id": 1}


def test_reconnect_releases_old_counter_when_staff_present(monkeypatch):
    w = _win(monkeypatch, staff_id=7)
    w._reconnect_services(OLD, old_staff_present=True)
    # Ancien comptoir libéré avec l'ANCIENNE URL et l'ANCIEN numéro (pas les nouveaux).
    assert w.released == [("http://ancien:5000", 1)]


def test_reconnect_skips_release_without_staff(monkeypatch):
    w = _win(monkeypatch, staff_id=None)
    w._reconnect_services(OLD, old_staff_present=False)
    assert w.released == []


def test_reconnect_invalidates_token_and_state(monkeypatch):
    w = _win(monkeypatch, staff_id=7)
    w._reconnect_services(OLD, old_staff_present=True)
    assert w.socket_io_client is None          # ancien WebSocket fermé
    assert w.app_token is None                  # jeton invalidé
    assert w.staff_id is None                   # staff de l'ancien comptoir oublié
    assert w.network_manager.cleared is True    # session réseau purgée
    assert w.queue_revision == -1
    assert w.my_patient is None
    assert w.list_patients == []
    assert w.socket_was_disconnected is False


def test_reconnect_starts_new_token_worker(monkeypatch):
    w = _win(monkeypatch, staff_id=7)
    w._reconnect_services(OLD, old_staff_present=True)
    assert FakeWorker.last is not None
    assert FakeWorker.last.started is True
    assert FakeWorker.last.finished_startup.slot is not None


# --- _on_reconnect_ready : succès vs échec ----------------------------------

def _ready_win(monkeypatch):
    w = types.SimpleNamespace(
        logger=logging.getLogger("test.reconnect.ready"),
        web_url="http://nouveau:5000",
        compact_mode=False,
        calls={"warn": 0, "apply_state": 0, "ws": 0},
        my_patient=None,
        list_patients=[],
    )
    w._apply_state = lambda state: w.calls.__setitem__("apply_state", w.calls["apply_state"] + 1)
    w.create_interface = lambda: None
    w.load_skin = lambda: None
    w.apply_panel_mode = lambda: None
    w.isVisible = lambda: False
    w.setup_user = lambda: None
    w.start_socket_io_client = lambda url: w.calls.__setitem__("ws", w.calls["ws"] + 1)
    w.alert_if_not_connected = lambda: None
    w._warn_reconnect_failed = lambda: w.calls.__setitem__("warn", w.calls["warn"] + 1)
    w._on_reconnect_ready = types.MethodType(main.MainWindow._on_reconnect_ready, w)
    return w


def test_on_reconnect_ready_success_no_warning(monkeypatch):
    w = _ready_win(monkeypatch)
    w._on_reconnect_ready(True, {"patients": []})
    assert w.connected is True
    assert w.calls["apply_state"] == 1
    assert w.calls["ws"] == 1        # WebSocket relancé sur le nouveau serveur
    assert w.calls["warn"] == 0


def test_on_reconnect_ready_failure_warns(monkeypatch):
    w = _ready_win(monkeypatch)
    w._on_reconnect_ready(False, None)
    assert w.connected is False
    assert w.calls["warn"] == 1      # avertissement explicite d'échec
    assert w.calls["ws"] == 1        # WS relancé quand même (rattrapage)
