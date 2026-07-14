"""Validation/normalisation de l'URL du serveur (point 8) : url_validation.py.

Module pur : schéma http/https, hôte présent, http interdit pour un serveur
distant (sauf mode dev), et distinction local vs distant.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import url_validation as uv  # noqa: E402


# --- normalize_url ----------------------------------------------------------

def test_normalize_trims_and_strips_trailing_slash():
    assert uv.normalize_url("  https://exemple.com/  ") == "https://exemple.com"
    assert uv.normalize_url("https://exemple.com///") == "https://exemple.com"


def test_normalize_empty():
    assert uv.normalize_url("") == ""
    assert uv.normalize_url(None) == ""
    assert uv.normalize_url("   ") == ""


# --- is_local_host ----------------------------------------------------------

def test_local_hosts():
    for h in ("localhost", "127.0.0.1", "::1", "192.168.1.10", "10.0.0.5",
              "172.16.3.4", "serveur-pharma", "nas.local", "srv.lan"):
        assert uv.is_local_host(h), h


def test_remote_hosts():
    for h in ("exemple.com", "gestionfile.onrender.com", "8.8.8.8"):
        assert not uv.is_local_host(h), h


# --- validate_server_url ----------------------------------------------------

def test_https_remote_ok():
    ok, normalized, err = uv.validate_server_url("https://gestionfile.onrender.com/")
    assert ok and normalized == "https://gestionfile.onrender.com" and err is None


def test_empty_rejected():
    ok, _, err = uv.validate_server_url("")
    assert not ok and "vide" in err.lower()


def test_missing_scheme_rejected():
    ok, _, err = uv.validate_server_url("exemple.com")
    assert not ok and "http" in err.lower()


def test_non_http_scheme_rejected():
    ok, _, err = uv.validate_server_url("ftp://exemple.com")
    assert not ok


def test_http_local_allowed():
    ok, normalized, err = uv.validate_server_url("http://localhost:5000")
    assert ok and normalized == "http://localhost:5000" and err is None
    ok2, _, _ = uv.validate_server_url("http://192.168.1.20:5000")
    assert ok2


def test_http_remote_rejected_by_default():
    ok, _, err = uv.validate_server_url("http://exemple.com")
    assert not ok and "distant" in err.lower()


def test_http_remote_allowed_in_dev_mode():
    ok, normalized, err = uv.validate_server_url(
        "http://exemple.com", allow_insecure_remote=True)
    assert ok and normalized == "http://exemple.com" and err is None


def test_scheme_without_host_rejected():
    ok, _, err = uv.validate_server_url("https://")
    assert not ok
