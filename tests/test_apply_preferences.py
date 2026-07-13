"""Câblage réel d'apply_preferences (point 19) : reconnexion vs cosmétique.

On appelle la vraie méthode main.MainWindow.apply_preferences avec un faux self :
load_preferences est simulée pour changer (ou non) une valeur « service », et on
vérifie que _reconnect_services n'est appelé QUE lorsqu'une valeur service change.
"""

import logging
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402


def _win(url="http://a", secret="s", counter=1):
    w = types.SimpleNamespace(
        web_url=url, app_secret=secret, counter_id=counter,
        logger=logging.getLogger("test.apply_prefs"),
        calls={"reconnect": 0, "shortcut": 0},
    )
    w.setup_global_shortcut = lambda: w.calls.__setitem__("shortcut", w.calls["shortcut"] + 1)
    w._reconnect_services = lambda: w.calls.__setitem__("reconnect", w.calls["reconnect"] + 1)
    w.apply_preferences = types.MethodType(main.MainWindow.apply_preferences, w)
    return w


def test_url_change_reconnects():
    w = _win(url="http://a")
    w.load_preferences = lambda: setattr(w, "web_url", "http://b")
    w.apply_preferences()
    assert w.calls["reconnect"] == 1
    assert w.calls["shortcut"] == 1   # cosmétique toujours appliqué


def test_secret_change_reconnects():
    w = _win(secret="s1")
    w.load_preferences = lambda: setattr(w, "app_secret", "s2")
    w.apply_preferences()
    assert w.calls["reconnect"] == 1


def test_counter_change_reconnects():
    w = _win(counter=1)
    w.load_preferences = lambda: setattr(w, "counter_id", 2)
    w.apply_preferences()
    assert w.calls["reconnect"] == 1


def test_no_service_change_does_not_reconnect():
    w = _win()
    w.load_preferences = lambda: None   # rien ne change côté service
    w.apply_preferences()
    assert w.calls["reconnect"] == 0
    assert w.calls["shortcut"] == 1     # cosmétique quand même appliqué
