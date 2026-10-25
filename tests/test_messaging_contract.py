"""Contrat URL/API et événements temps réel de la messagerie."""

import types

import endpoints
from websocket_client import WebSocketClient


def test_messaging_endpoints_are_centralized_and_escaped():
    base = "https://srv/"
    assert endpoints.messaging_presence(base) == "https://srv/api/messaging/presence"
    assert endpoints.messaging_state(base, 3).endswith("?counter_id=3")
    url = endpoints.messaging_messages(base, 3, "direct", peer_staff_id=7, after_id=9)
    assert "kind=direct" in url
    assert "peer_staff_id=7" in url
    assert "after_id=9" in url


def test_websocket_messaging_events_emit_safe_payload():
    parent = types.SimpleNamespace(
        web_url="http://srv", app_token="token", debug_window=False,
        counter_id=1, try_refresh_app_token=lambda: True,
    )
    client = WebSocketClient(parent)
    changed = []
    presence = []
    enabled = []
    client.messaging_changed.connect(changed.append)
    client.messaging_presence_changed.connect(lambda: presence.append(True))
    client.messaging_config_changed.connect(enabled.append)
    client.on_messaging_changed({"data": {"message_id": 5, "sender_name": "Bob"}})
    client.on_messaging_presence_changed()
    client.on_messaging_config_changed({"data": {"enabled": False}})
    assert changed == [{"message_id": 5, "sender_name": "Bob"}]
    assert presence == [True]
    assert enabled == [False]


# --- join_messaging_room : déclaration du comptoir à chaque connexion --------
#
# Le serveur place le sid dans la room ``messaging_counter_<id>`` ; la room
# étant perdue à chaque reconnexion, le client la redemande dans on_connect.

class _RecordingSio:
    """Faux client socketio minimal : enregistre les appels à emit()."""

    def __init__(self):
        self.emitted = []

    def emit(self, event, payload=None, namespace=None):
        self.emitted.append((event, payload, namespace))


def _parent(counter_id=1):
    return types.SimpleNamespace(
        web_url="http://srv", app_token="token", debug_window=False,
        counter_id=counter_id, try_refresh_app_token=lambda: True,
    )


def test_on_connect_declare_le_comptoir_a_la_room_messaging():
    client = WebSocketClient(_parent(counter_id=3))
    sio = _RecordingSio()
    client.sio = sio
    client.on_connect()
    assert ("join_messaging_room", {"counter_id": 3},
            "/socket_app_counter") in sio.emitted


def test_on_connect_sans_counter_id_n_emet_pas():
    # Poste non configuré : counter_id absent puis None -> pas d'émission.
    for parent in (_parent(counter_id=None), _parent()):
        if parent.counter_id is not None:
            del parent.counter_id
        client = WebSocketClient(parent)
        sio = _RecordingSio()
        client.sio = sio
        client.on_connect()
        assert sio.emitted == []


def test_on_connect_sio_sans_emit_ne_casse_pas():
    # Un faux sio dépourvu de emit() (tests de la boucle de reconnexion) ne
    # doit pas faire échouer on_connect.
    client = WebSocketClient(_parent(counter_id=1))
    client.sio = types.SimpleNamespace()
    client.on_connect()  # ne lève pas
