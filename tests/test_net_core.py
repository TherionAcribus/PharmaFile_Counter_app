"""Tests de la logique réseau pure (net_core.perform_with_reauth).

Garantit le contrat central du gestionnaire réseau :
- succès direct sans renouvellement ;
- sur 401, renouvellement du jeton puis UN SEUL rejeu ;
- si le renouvellement échoue, pas de rejeu ;
- si le rejeu renvoie encore 401, on n'insiste pas (une seule répétition).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from net_core import perform_with_reauth  # noqa: E402


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class Sender:
    """Renvoie successivement les réponses fournies et compte les appels."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self._responses.pop(0)


def test_success_no_reauth():
    send = Sender([FakeResponse(200, "ok")])
    reauth_calls = []
    text, status = perform_with_reauth(send, lambda: reauth_calls.append(1) or True)
    assert (text, status) == ("ok", 200)
    assert send.calls == 1
    assert reauth_calls == []  # jamais appelé si pas de 401


def test_401_then_reauth_then_retry_success():
    send = Sender([FakeResponse(401, "no"), FakeResponse(200, "ok")])
    reauth = lambda: True
    text, status = perform_with_reauth(send, reauth)
    assert (text, status) == ("ok", 200)
    assert send.calls == 2  # exactement un rejeu


def test_401_reauth_fails_no_retry():
    send = Sender([FakeResponse(401, "no")])
    text, status = perform_with_reauth(send, lambda: False)
    assert status == 401
    assert send.calls == 1  # pas de rejeu si le renouvellement échoue


def test_401_twice_only_one_retry():
    # Le rejeu renvoie encore 401 : on ne répète qu'une seule fois.
    send = Sender([FakeResponse(401, "a"), FakeResponse(401, "b")])
    reauth_count = {"n": 0}

    def reauth():
        reauth_count["n"] += 1
        return True

    text, status = perform_with_reauth(send, reauth)
    assert status == 401
    assert send.calls == 2         # 1 essai + 1 rejeu, pas plus
    assert reauth_count["n"] == 1  # un seul renouvellement


def test_non_401_error_not_retried():
    send = Sender([FakeResponse(500, "boom")])
    text, status = perform_with_reauth(send, lambda: True)
    assert (text, status) == ("boom", 500)
    assert send.calls == 1
