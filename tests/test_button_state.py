"""Tests de la logique d'état des boutons d'icône (button_state).

Invariant central : le bouton ne reste JAMAIS en "waiting" après une réponse ;
en cas d'erreur (jeton expiré non renouvelé, 5xx, réseau) il revient à l'état
précédent. ``data`` est le JSON déjà décodé par le gestionnaire réseau (ou None).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from button_state import resolve_button_state  # noqa: E402


def test_success_active():
    assert resolve_button_state(200, {"status": True}, "inactive") == "active"


def test_success_inactive():
    assert resolve_button_state(200, {"status": False}, "active") == "inactive"


@pytest.mark.parametrize("status", [401, 500, 0, 423])
def test_error_reverts_to_previous_state(status):
    # Jamais "waiting" : on restaure l'état précédent, quel qu'il soit.
    # (data est None quand la réponse n'est pas un JSON exploitable.)
    assert resolve_button_state(status, None, "inactive") == "inactive"
    assert resolve_button_state(status, None, "active") == "active"


@pytest.mark.parametrize("data", [None, {}, {"other": 1}, [1, 2], "oops"])
def test_200_with_unexpected_data_reverts(data):
    # 200 mais data sans "status" exploitable : on ne reste pas bloqué en "waiting".
    assert resolve_button_state(200, data, "active") == "active"
    assert resolve_button_state(200, data, "inactive") == "inactive"


def test_never_returns_waiting():
    for status in (200, 401, 500, 0):
        for data in ({"status": True}, None, {}):
            assert resolve_button_state(status, data, "inactive") != "waiting"
