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
