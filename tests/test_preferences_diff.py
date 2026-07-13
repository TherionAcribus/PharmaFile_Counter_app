"""Tests de la décision de reconnexion après préférences (point 19)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from preferences_diff import needs_service_reconnect, SERVICE_KEYS  # noqa: E402


def _prefs(url="http://a", secret="s", counter=1):
    return {"web_url": url, "app_secret": secret, "counter_id": counter}


def test_no_change_no_reconnect():
    assert needs_service_reconnect(_prefs(), _prefs()) is False


def test_url_change_triggers_reconnect():
    assert needs_service_reconnect(_prefs(), _prefs(url="http://b")) is True


def test_secret_change_triggers_reconnect():
    assert needs_service_reconnect(_prefs(), _prefs(secret="autre")) is True


def test_counter_change_triggers_reconnect():
    assert needs_service_reconnect(_prefs(counter=1), _prefs(counter=2)) is True


def test_cosmetic_change_does_not_trigger_reconnect():
    old = _prefs()
    new = _prefs()
    # Un changement hors clés « service » (volume, thème…) n'impacte pas.
    old["notification_volume"] = 10
    new["notification_volume"] = 80
    old["selected_skin"] = "a"
    new["selected_skin"] = "b"
    assert needs_service_reconnect(old, new) is False


def test_service_keys_are_url_secret_counter():
    assert set(SERVICE_KEYS) == {"web_url", "app_secret", "counter_id"}
