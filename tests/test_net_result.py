"""Tests de la structure de résultat réseau commune (net_result)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from net_result import (  # noqa: E402
    NetResult,
    user_message_for_status,
    parse_json_if_possible,
)


# --- messages utilisateur distincts ----------------------------------------

def test_distinct_messages_for_error_categories():
    m = {s: user_message_for_status(s) for s in (0, 401, 403, 409, 423, 500)}
    # 401, 403, timeout(0), 5xx distincts entre eux ; 409 et 423 identiques.
    assert len({m[401], m[403], m[0], m[500]}) == 4
    assert m[409] == m[423]
    for s in (0, 401, 403, 423, 500):
        assert m[s]  # non vide


def test_success_has_no_user_message():
    assert user_message_for_status(200) == ""
    assert user_message_for_status(204) == ""


# --- décodage JSON conditionnel --------------------------------------------

def test_json_parsed_when_content_type_json():
    assert parse_json_if_possible('{"a": 1}', "application/json") == {"a": 1}


def test_html_not_parsed():
    assert parse_json_if_possible("<html></html>", "text/html") is None


def test_malformed_json_returns_none():
    assert parse_json_if_possible("{oops", "application/json") is None


def test_empty_body_returns_none():
    assert parse_json_if_possible("", "application/json") is None
    assert parse_json_if_possible(None, None) is None


def test_unknown_content_type_attempts_parse():
    # content-type absent -> on tente quand même (ex: serveur qui n'en met pas).
    assert parse_json_if_possible('{"a": 1}', None) == {"a": 1}


# --- NetResult --------------------------------------------------------------

def test_from_response_success():
    r = NetResult.from_response(200, '{"x": 1}', "application/json")
    assert r.success is True and r.status == 200
    assert r.data == {"x": 1}
    assert r.message == ""       # pas de message d'erreur sur un succès


def test_from_response_error_has_message_and_detail():
    r = NetResult.from_response(500, "Internal Error", "text/plain")
    assert r.success is False
    assert r.data is None
    assert r.message                     # message utilisateur court
    assert "500" in r.detail             # détail technique


def test_from_response_html_body_does_not_crash():
    r = NetResult.from_response(200, "<html>ok</html>", "text/html")
    assert r.status == 200 and r.data is None


def test_network_error_result():
    r = NetResult.network_error("connexion perdue")
    assert r.status == 0 and r.is_timeout is True
    assert r.success is False
    assert r.message and "connexion perdue" in r.detail
