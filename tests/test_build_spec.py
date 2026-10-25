"""Configuration de build PyInstaller (point 10.6).

Deux fichiers .spec, et seulement eux, doivent exister :
- ``FlowelisComptoir.spec``         : build « dossier » (dist/FlowelisComptoir/)
- ``FlowelisComptoir_onefile.spec`` : build « exe unique »
  (dist/FlowelisComptoir.exe), utilisé par le workflow de release

Un `main.spec` obsolète cohabitait autrefois avec le spec de production, sans
exclusions serveur, sans les backends keyring et sans les dossiers
`skins/`/`templates/` — compiler avec le mauvais fichier produisait un binaire
silencieusement dégradé (aucun thème, secret stocké en clair). La même
vigilance s'applique aux deux specs : leurs listes critiques doivent rester
identiques.
"""

import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPECS = ["FlowelisComptoir.spec", "FlowelisComptoir_onefile.spec"]
SPEC = os.path.join(APP_DIR, "FlowelisComptoir.spec")


def _spec_source(path=SPEC):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _critical_lists(path):
    """Extrait les listes SERVER_AND_DEV_EXCLUDES, KEYRING_HIDDENIMPORTS et
    datas d'un fichier .spec, pour comparaison entre les deux specs."""
    tree = ast.parse(_spec_source(path), filename=os.path.basename(path))
    constants = {}
    datas = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                    "SERVER_AND_DEV_EXCLUDES",
                    "KEYRING_HIDDENIMPORTS",
                ):
                    constants[target.id] = ast.literal_eval(node.value)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Analysis":
            for kw in node.keywords:
                if kw.arg == "datas":
                    datas = ast.literal_eval(kw.value)
    return constants, datas


def test_fichiers_spec():
    specs = sorted(n for n in os.listdir(APP_DIR) if n.endswith(".spec"))
    assert specs == SPECS, f"fichiers .spec inattendus : {specs}"


def test_specs_sont_du_python_valide():
    for name in SPECS:
        ast.parse(
            _spec_source(os.path.join(APP_DIR, name)),
            filename=name,
        )


def test_toutes_les_ressources_embarquees():
    """Les dossiers lus par resources.py doivent être embarqués."""
    for name in SPECS:
        source = _spec_source(os.path.join(APP_DIR, name))
        for folder in ("assets", "skins", "templates"):
            assert f"('{folder}', '{folder}')" in source, f"{name}: {folder}"


def test_backends_keyring_forces():
    """Sans ces imports cachés, le binaire gelé ne trouve aucun backend et le
    secret applicatif retombe sur un stockage en clair."""
    for name in SPECS:
        source = _spec_source(os.path.join(APP_DIR, name))
        assert "keyring.backends.Windows" in source, name
        assert "win32ctypes.core" in source, name


def test_dependances_serveur_exclues():
    for name in SPECS:
        source = _spec_source(os.path.join(APP_DIR, name))
        for module in ("flask", "sqlalchemy", "pytest", "line_profiler"):
            assert f"'{module}'" in source, f"{name}: {module}"


def test_listes_critiques_synchronisees():
    """Les deux specs doivent embarquer les mêmes exclusions, hiddenimports et
    dossiers de ressources — sinon l'un des deux binaires serait dégradé."""
    reference = _critical_lists(SPEC)
    onefile = _critical_lists(os.path.join(APP_DIR, "FlowelisComptoir_onefile.spec"))
    assert onefile == reference
