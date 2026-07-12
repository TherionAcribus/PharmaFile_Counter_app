"""Tests d'intégration réels de l'arrêt du client WebSocket (point 11).

Vérifie que la boucle de (re)connexion se termine proprement et rapidement quand
``stop()`` est appelé — au lieu de se reconnecter indéfiniment — sans I/O réseau
réelle (on remplace le client socketio interne par un faux).
"""

import os
import sys
import threading
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from PySide6.QtCore import QCoreApplication  # noqa: E402
import socketio  # noqa: E402

from websocket_client import WebSocketClient  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def _make_parent():
    return types.SimpleNamespace(
        web_url="http://serveur-test",
        app_token="tok",
        debug_window=False,
        try_refresh_app_token=lambda: True,
    )


class FailingSio:
    """Faux client socketio : la connexion échoue toujours (serveur injoignable),
    ce qui exerce la boucle de reconnexion."""

    def __init__(self):
        self.disconnected = threading.Event()

    def on(self, *a, **k):
        pass

    def connect(self, url, headers=None):
        raise socketio.exceptions.ConnectionError("pas de serveur")

    def wait(self):
        pass

    def disconnect(self):
        self.disconnected.set()


class ConnectedSio(FailingSio):
    """Se connecte puis bloque dans wait() jusqu'à disconnect() (cas nominal)."""

    def __init__(self):
        super().__init__()
        self._release = threading.Event()

    def connect(self, url, headers=None):
        return True

    def wait(self):
        self._release.wait()

    def disconnect(self):
        self.disconnected.set()
        self._release.set()


def test_stop_terminates_reconnect_loop(qapp):
    ws = WebSocketClient(_make_parent())
    ws.sio = FailingSio()
    ws.start()
    # Laisse la boucle enchaîner un échec de connexion (puis attente de reco).
    threading.Event().wait(0.2)
    finished = ws.stop(timeout_ms=3000)
    assert finished is True
    assert not ws.isRunning()


def test_stop_when_connected_returns_quickly(qapp):
    ws = WebSocketClient(_make_parent())
    sio = ConnectedSio()
    ws.sio = sio
    ws.start()
    threading.Event().wait(0.2)  # laisse le temps de "se connecter" et d'entrer dans wait()
    finished = ws.stop(timeout_ms=3000)
    assert finished is True
    assert sio.disconnected.is_set()
    assert not ws.isRunning()


def test_stop_before_any_connection_is_safe(qapp):
    ws = WebSocketClient(_make_parent())
    ws.sio = FailingSio()
    # stop() sans avoir démarré le thread : ne doit pas lever ni bloquer.
    assert ws.stop(timeout_ms=1000) is True
    assert not ws.isRunning()
