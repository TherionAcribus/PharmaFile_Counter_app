"""Tests de la normalisation de counter_id (point 17).

- ``coerce_counter_id`` (module pur) : entier strictement positif ou None.
- Cohérence des comparaisons WebSocket : parent.counter_id entier vs counter_id
  reçu du serveur en int OU en chaîne -> doivent correspondre (c'est le bug que
  la normalisation corrige : "1" == 1 était faux).
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from counter_id_utils import coerce_counter_id  # noqa: E402


# --- helper pur -------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (1, 1),
    ("1", 1),
    (" 3 ", 3),
    (42, 42),
    ("42", 42),
])
def test_valid_values_coerced_to_int(value, expected):
    assert coerce_counter_id(value) == expected
    assert isinstance(coerce_counter_id(value), int)


@pytest.mark.parametrize("value", [None, "", "abc", "1.5", 0, "0", -1, "-2", True, False, [], {}])
def test_invalid_values_return_none(value):
    assert coerce_counter_id(value) is None


# --- cohérence des comparaisons WebSocket ----------------------------------

from PySide6.QtCore import QCoreApplication  # noqa: E402
from websocket_client import WebSocketClient  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QCoreApplication.instance() or QCoreApplication([])


def _ws(counter_id):
    parent = types.SimpleNamespace(web_url="http://x", app_token="t", debug_window=False)
    parent.counter_id = counter_id  # entier (normalisé par load_preferences)
    return WebSocketClient(parent)


def test_event_matches_when_server_sends_int(qapp):
    ws = _ws(1)
    assert ws._event_targets_this_counter({"data": {"counter_id": 1}}) is True


def test_event_matches_when_server_sends_string(qapp):
    # Cœur du bug : counter_id local entier 1, serveur envoie "1" (chaîne).
    ws = _ws(1)
    assert ws._event_targets_this_counter({"data": {"counter_id": "1"}}) is True


def test_event_does_not_match_other_counter(qapp):
    ws = _ws(1)
    assert ws._event_targets_this_counter({"data": {"counter_id": 2}}) is False
    assert ws._event_targets_this_counter({"data": {"counter_id": "2"}}) is False


def test_event_malformed_payload_is_safe(qapp):
    ws = _ws(1)
    assert ws._event_targets_this_counter({"data": "pas un dict"}) is False
    assert ws._event_targets_this_counter({}) is False
    assert ws._event_targets_this_counter({"data": {"counter_id": None}}) is False
