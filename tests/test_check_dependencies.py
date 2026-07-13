"""Tests de l'audit des dépendances (point 23) : tools/check_dependencies.py.

Logique pure (stdlib) : normalisation des noms, lecture des fichiers de
dépendances (specifiers, commentaires, `-r`, encodage), et détection des imports
requis vs optionnels (try/except ImportError).
"""

import ast
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(_ROOT, "tools"))

import check_dependencies as cd  # noqa: E402


def test_normalize_pep503():
    assert cd.normalize("python_Socket.IO") == "python-socket-io"
    assert cd.normalize("PySide6") == "pyside6"
    assert cd.normalize("flask--login") == "flask-login"


def test_parse_requirements_basic(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text(
        "# commentaire\n"
        "PySide6==6.11.1  # inline\n"
        "requests>=2.0\n"
        "python-socketio==5.11.2\n"
        "keyring==25.2.1 ; sys_platform == 'win32'\n"
        "\n",
        encoding="utf-8",
    )
    declared = cd.parse_requirements(req)
    assert declared == {"pyside6", "requests", "python-socketio", "keyring"}


def test_parse_requirements_follows_dash_r(tmp_path):
    base = tmp_path / "requirements.txt"
    base.write_text("requests==2.34.2\n", encoding="utf-8")
    dev = tmp_path / "requirements-dev.txt"
    dev.write_text("-r requirements.txt\npytest==9.1.1\n", encoding="utf-8")
    assert cd.parse_requirements(dev) == {"requests", "pytest"}


def test_parse_requirements_utf16(tmp_path):
    """Ancien requirements.txt encodé en UTF-16 : doit rester lisible."""
    req = tmp_path / "requirements.txt"
    req.write_bytes("keyboard==0.13.5\nrequests==2.34.2\n".encode("utf-16"))
    assert cd.parse_requirements(req) == {"keyboard", "requests"}


def test_parse_requirements_ignores_pip_options(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("--index-url https://x\n-e .\nrequests\n", encoding="utf-8")
    assert cd.parse_requirements(req) == {"requests"}


def _imports(code):
    return cd.collect_imports(ast.parse(code))


def test_collect_imports_required():
    required, optional = _imports("import requests\nfrom socketio import Client\n")
    assert required == {"requests", "socketio"}
    assert optional == set()


def test_collect_imports_optional_try_except():
    code = (
        "try:\n"
        "    import keyring\n"
        "    from keyring.errors import KeyringError\n"
        "except ImportError:\n"
        "    keyring = None\n"
    )
    required, optional = _imports(code)
    assert "keyring" in optional
    assert "keyring" not in required


def test_collect_imports_required_wins_over_optional():
    """Un même nom importé en dur ailleurs reste requis."""
    code = (
        "import requests\n"
        "try:\n"
        "    import requests\n"
        "except ImportError:\n"
        "    pass\n"
    )
    required, optional = _imports(code)
    assert "requests" in required
    assert "requests" not in optional


def test_collect_imports_ignores_relative():
    required, optional = _imports("from . import buttons\nfrom .net_core import x\n")
    assert required == set()
    assert optional == set()


def test_server_denylist_contains_common_server_packages():
    for pkg in ("flask", "werkzeug", "jinja2", "redis"):
        assert pkg in cd.SERVER_DENYLIST


def test_real_requirements_have_no_server_packages():
    """Le vrai requirements.txt du client ne contient aucun paquet serveur."""
    from pathlib import Path
    declared = cd.parse_requirements(Path(_ROOT) / "requirements.txt")
    assert declared and not (declared & cd.SERVER_DENYLIST)


def test_audit_main_passes_on_real_project():
    """L'audit complet réussit sur le projet réel (deps runtime installées)."""
    assert cd.main() == 0


def test_audit_main_fails_on_undeclared_import(tmp_path, monkeypatch):
    """Un module runtime important un paquet NON déclaré fait échouer l'audit."""
    (tmp_path / "app.py").write_text("import requests\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("# vide\n", encoding="utf-8")
    monkeypatch.setattr(cd, "ROOT", tmp_path)
    assert cd.main() == 1  # requests importé mais absent de requirements.txt


def test_audit_main_fails_on_server_package(tmp_path, monkeypatch):
    """Déclarer un paquet serveur fait échouer l'audit."""
    (tmp_path / "app.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")
    monkeypatch.setattr(cd, "ROOT", tmp_path)
    assert cd.main() == 1
