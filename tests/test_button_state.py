"""Tests de la logique d'état des boutons d'icône (button_state).

Invariant central : le bouton ne reste JAMAIS en "waiting" après une réponse ;
en cas d'erreur (jeton expiré non renouvelé, 5xx, réseau) il revient à l'état
précédent. On vérifie aussi le décodage du succès.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from button_state import resolve_button_state  # noqa: E402


def test_success_active():
    assert resolve_button_state(200, '{"status": true}', "inactive") == "active"


def test_success_inactive():
    assert resolve_button_state(200, '{"status": false}', "active") == "inactive"


@pytest.mark.parametrize("status,body", [
    (401, "Unauthorized"),
    (500, "Internal Server Error"),
    (0, "Connection error: timeout"),  # erreur réseau (RequestThread émet status=0)
    (423, "Locked"),
])
def test_error_reverts_to_previous_state(status, body):
    # Jamais "waiting" : on restaure l'état précédent, quel qu'il soit.
    assert resolve_button_state(status, body, "inactive") == "inactive"
    assert resolve_button_state(status, body, "active") == "active"


@pytest.mark.parametrize("body", ["", "not-json", "{}", '{"other": 1}', "null"])
def test_200_with_unexpected_body_reverts(body):
    # 200 mais corps inattendu : on ne reste pas bloqué en "waiting".
    assert resolve_button_state(200, body, "active") == "active"
    assert resolve_button_state(200, body, "inactive") == "inactive"


def test_never_returns_waiting():
    for status in (200, 401, 500, 0):
        for body in ('{"status": true}', "oops", ""):
            assert resolve_button_state(status, body, "inactive") != "waiting"