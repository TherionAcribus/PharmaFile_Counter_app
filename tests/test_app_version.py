"""Tests du numéro de version de l'App (app_version : source unique)."""

import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, ROOT)

from app_version import (
    APP_VERSION, reported_version, tag_matches, version_label, version_tuple,
)


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def test_version_is_x_y_z():
    major, minor, patch = version_tuple()
    assert f"{major}.{minor}.{patch}" == APP_VERSION


@pytest.mark.parametrize("bad", ["1.2", "v1.2.0", "1.2.0-dev", "", "1.x.0"])
def test_invalid_versions_are_refused(bad):
    with pytest.raises(ValueError):
        version_tuple(bad)


def test_reported_version_distinguishes_published_and_source_runs():
    # L'exe publié annonce la version exacte ; depuis les sources, le serveur
    # ne doit pas la confondre avec une version publiée.
    assert reported_version(frozen=True) == APP_VERSION
    assert reported_version(frozen=False) == APP_VERSION + "-dev"
    assert version_label(frozen=True) == f"Version {APP_VERSION}"
    assert "sources" in version_label(frozen=False)


def test_reported_version_fits_the_server_column():
    assert len(reported_version(frozen=False)) <= 40  # String(40) côté serveur


def test_tag_must_match_version():
    assert tag_matches(f"v{APP_VERSION}")
    assert not tag_matches(APP_VERSION)
    assert not tag_matches("v0.0.1")


@pytest.mark.parametrize("tag,code", [(f"v{APP_VERSION}", 0), ("v0.0.1", 1)])
def test_release_check_command(tag, code):
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "app_version.py"), tag],
                          capture_output=True)
    assert proc.returncode == code


def test_release_workflow_checks_the_tag_before_building():
    workflow = _read(os.path.join(".github", "workflows", "release.yml"))
    check = workflow.index('python app_version.py "${{ github.ref_name }}"')
    assert check < workflow.index("pyinstaller")


def test_both_specs_embed_the_version_in_the_exe():
    for name in ("FlowelisComptoir.spec", "FlowelisComptoir_onefile.spec"):
        spec = _read(name)
        assert "VERSION_INFO = exe_version_info.windows_version_info()" in spec, name
        assert "version=VERSION_INFO," in spec, name


def test_windows_version_info_carries_the_version():
    pytest.importorskip("PyInstaller")
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import exe_version_info
    text = str(exe_version_info.windows_version_info())
    major, minor, patch = version_tuple()
    assert f"filevers=({major}, {minor}, {patch}, 0)" in text
    assert f"StringStruct('ProductVersion', '{APP_VERSION}')" in text


def test_qt_application_knows_the_version():
    # Faux objet : la vraie QApplication est partagée par tous les tests et
    # son identité situe les réglages Qt.
    from unittest import mock

    import app_identity
    app = mock.MagicMock()
    app_identity.apply_identity(app)
    app.setApplicationVersion.assert_called_once_with(APP_VERSION)


def test_version_is_shown_in_preferences():
    source = _read("preferences.py")
    assert "app_version.version_label()" in source
