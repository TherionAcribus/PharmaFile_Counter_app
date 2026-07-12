"""Tests de la construction des en-têtes d'auth Socket.IO côté client.

Vérifie que la connexion Socket.IO présente le jeton applicatif (X-App-Token)
en plus du username, et n'inclut le jeton que lorsqu'il est disponible.

socket_auth est volontairement sans dépendance PySide/socketio pour être
testable isolément.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from socket_auth import build_socket_auth_headers  # noqa: E402


def test_headers_include_token_when_present():
    headers = build_socket_auth_headers("Counter 3 App", "un-jeton-valide")
    assert headers["X-App-Token"] == "un-jeton-valide"
    assert headers["username"] == "Counter 3 App"


def test_headers_omit_token_when_absent():
    for token in (None, ""):
        headers = build_socket_auth_headers("Counter 3 App", token)
        assert "X-App-Token" not in headers
        assert headers["username"] == "Counter 3 App"


def test_username_omitted_when_empty():
    headers = build_socket_auth_headers("", "jeton")
    assert "username" not in headers
    assert headers["X-App-Token"] == "jeton"
