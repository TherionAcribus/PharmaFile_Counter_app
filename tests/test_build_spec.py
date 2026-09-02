"""Configuration de build PyInstaller (point 10.6).

Un seul fichier .spec doit exister : un `main.spec` obsolète cohabitait avec
`PharmaFile.spec`, sans exclusions serveur, sans les backends keyring et sans
les dossiers `skins/`/`templates/` — compiler avec le mauvais fichier produisait
un binaire silencieusement dégradé (aucun thème, secret stocké en clair).
"""

import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(APP_DIR, "PharmaFile.spec")


def _spec_source():
    with open(SPEC, encoding="utf-8") as fh:
        return fh.read()


def test_un_seul_fichier_spec():
    specs = sorted(n for n in os.listdir(APP_DIR) if n.endswith(".spec"))
    assert specs == ["PharmaFile.spec"], f"fichiers .spec inattendus : {specs}"


def test_spec_est_du_python_valide():
    ast.parse(_spec_source(), filename="PharmaFile.spec")


def test_toutes_les_ressources_embarquees():
    """Les dossiers lus par resources.py doivent être embarqués."""
    source = _spec_source()
    for folder in ("assets", "skins", "templates"):
        assert f"('{folder}', '{folder}')" in source, folder


def test_backends_keyring_forces():
    """Sans ces imports cachés, le binaire gelé ne trouve aucun backend et le
    secret applicatif retombe sur un stockage en clair."""
    source = _spec_source()
    assert "keyring.backends.Windows" in source
    assert "win32ctypes.core" in source


def test_dependances_serveur_exclues():
    source = _spec_source()
    for module in ("flask", "sqlalchemy", "pytest", "line_profiler"):
        assert f"'{module}'" in source, module
