#!/usr/bin/env python3
"""Audit automatisé des dépendances du client App_Comptoir (point 23).

Objectifs (vérifiés en CI) :
  1. Toute bibliothèque tierce RÉELLEMENT importée par le code runtime est
     déclarée dans ``requirements.txt`` (sinon : échec).
  2. Aucune dépendance SERVEUR (Flask, Werkzeug, Jinja2, Redis, …) n'apparaît
     dans les fichiers de dépendances du client (sinon : échec).
  3. Avertissements (non bloquants) : dépendance déclarée mais jamais importée.

Le script n'utilise que la bibliothèque standard. Il doit être exécuté dans un
environnement où les dépendances runtime sont installées (la correspondance
« nom d'import → distribution » repose sur les métadonnées installées), ce que
fait la CI avant de l'appeler.

Les imports optionnels (entourés d'un ``try/except ImportError``, ex. ``keyring``)
ne sont PAS exigés comme installés : l'application fonctionne sans eux.

Usage :
    python tools/check_dependencies.py
Sortie : code 0 si tout est cohérent, 1 sinon.
"""

from __future__ import annotations

import ast
import importlib.metadata as metadata
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Fichiers/dossiers exclus du périmètre RUNTIME (tests, outillage, build).
EXCLUDED_NAMES = {"conftest.py"}
EXCLUDED_DIRS = {"tests", "tools", ".venv", "build", "dist", "__pycache__"}

# Distributions serveur qui ne doivent jamais figurer dans les deps du client.
SERVER_DENYLIST = {
    "flask", "werkzeug", "jinja2", "itsdangerous", "click", "redis",
    "gunicorn", "sqlalchemy", "flask-sqlalchemy", "flask-login",
    "flask-socketio", "celery", "eventlet", "gevent", "pymysql",
    "mysqlclient", "mysql-connector-python", "alembic",
}


def normalize(name: str) -> str:
    """Normalisation PEP 503 d'un nom de distribution/paquet."""
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------------------
# 1. Découverte des imports du code runtime
# ---------------------------------------------------------------------------

def runtime_files() -> list[Path]:
    files = []
    for path in ROOT.glob("*.py"):
        if path.name in EXCLUDED_NAMES:
            continue
        # test.py (racine) et tout test_*.py sont des tests, pas du runtime.
        if path.name == "test.py" or path.name.startswith("test_"):
            continue
        files.append(path)
    return sorted(files)


def local_module_names() -> set[str]:
    """Modules locaux (fichiers .py du dossier) : à exclure des tierces parties."""
    names = {p.stem for p in ROOT.glob("*.py")}
    # Paquets locaux éventuels (dossiers avec __init__.py).
    for d in ROOT.iterdir():
        if d.is_dir() and (d / "__init__.py").exists():
            names.add(d.name)
    return names


def _handler_catches_import_error(handlers) -> bool:
    for h in handlers:
        exc = h.type
        if exc is None:  # except: nu
            return True
        targets = exc.elts if isinstance(exc, ast.Tuple) else [exc]
        for t in targets:
            name = getattr(t, "id", None) or getattr(t, "attr", None)
            if name in {"ImportError", "ModuleNotFoundError", "Exception"}:
                return True
    return False


def collect_imports(tree: ast.AST):
    """Retourne (requis, optionnels) : ensembles de noms de module de 1er niveau.

    Un import est « optionnel » s'il est situé dans le corps d'un ``try`` dont un
    handler intercepte ImportError/ModuleNotFoundError/Exception.
    """
    required, optional = set(), set()

    def top_level(node):
        names = set()
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:  # ignore les imports relatifs
                names.add(node.module.split(".")[0])
        return names

    def visit(node, in_optional):
        if isinstance(node, ast.Try):
            optional_body = in_optional or _handler_catches_import_error(node.handlers)
            for child in node.body:
                visit(child, optional_body)
            for child in node.orelse + node.finalbody:
                visit(child, in_optional)
            for h in node.handlers:
                for child in h.body:
                    visit(child, in_optional)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            (optional if in_optional else required).update(top_level(node))
        for child in ast.iter_child_nodes(node):
            visit(child, in_optional)

    visit(tree, False)
    # Un nom importé de façon requise quelque part reste requis.
    optional -= required
    return required, optional


# ---------------------------------------------------------------------------
# 2. Lecture des fichiers de dépendances déclarées
# ---------------------------------------------------------------------------

def read_text_any_encoding(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_requirements(path: Path) -> set[str]:
    """Noms de distributions déclarés (normalisés), en suivant les ``-r``."""
    declared: set[str] = set()
    if not path.exists():
        return declared
    for raw in read_text_any_encoding(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement ")):
            declared |= parse_requirements(path.parent / line.split(maxsplit=1)[1].strip())
            continue
        if line.startswith("-"):  # autres options pip
            continue
        # Retire specifiers de version/marqueurs/extras.
        name = re.split(r"[<>=!~;\[\s]", line, maxsplit=1)[0]
        if name:
            declared.add(normalize(name))
    return declared


# ---------------------------------------------------------------------------
# 3. Audit
# ---------------------------------------------------------------------------

def main() -> int:
    # Sortie UTF-8 même sur une console Windows en encodage hérité.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

    errors: list[str] = []
    warnings: list[str] = []

    local = local_module_names()
    stdlib = set(sys.stdlib_module_names)

    required_imports: set[str] = set()
    optional_imports: set[str] = set()
    for path in runtime_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:  # pragma: no cover
            errors.append(f"Analyse impossible de {path.name} : {exc}")
            continue
        req, opt = collect_imports(tree)
        required_imports |= req
        optional_imports |= opt

    def is_third_party(name: str) -> bool:
        return name not in stdlib and name not in local

    required_third = {n for n in required_imports if is_third_party(n)}
    optional_third = {n for n in optional_imports if is_third_party(n)} - required_third

    # Correspondance nom d'import -> distributions installées.
    pkg_to_dist = {
        normalize(k): {normalize(d) for d in v}
        for k, v in metadata.packages_distributions().items()
    }

    declared_runtime = parse_requirements(ROOT / "requirements.txt")
    declared_build = parse_requirements(ROOT / "requirements-build.txt")
    declared_dev = parse_requirements(ROOT / "requirements-dev.txt")
    declared_all = declared_runtime | declared_build | declared_dev

    # (2) Denylist serveur dans TOUS les fichiers de deps.
    for offender in sorted(declared_all & SERVER_DENYLIST):
        errors.append(
            f"Dépendance serveur interdite déclarée pour le client : '{offender}'")

    # (1) Chaque import tiers requis doit être installé ET déclaré en runtime.
    used_dists: set[str] = set()
    for name in sorted(required_third):
        dists = pkg_to_dist.get(normalize(name))
        if not dists:
            errors.append(
                f"'{name}' est importé mais aucune distribution installée ne le "
                f"fournit (exécuter l'audit dans un env avec les deps runtime).")
            continue
        used_dists |= dists
        if not (dists & declared_runtime):
            errors.append(
                f"'{name}' (distribution {sorted(dists)}) est importé par le "
                f"client mais absent de requirements.txt.")

    # Imports optionnels : tolérés si non installés ; sinon comptés comme utilisés.
    for name in sorted(optional_third):
        dists = pkg_to_dist.get(normalize(name))
        if dists:
            used_dists |= dists
            if not (dists & declared_all):
                warnings.append(
                    f"Import optionnel '{name}' ({sorted(dists)}) installé mais "
                    f"non déclaré : envisager de l'ajouter à requirements.txt.")
        else:
            # Non installé : on considère quand même le nom comme « utilisé » pour
            # ne pas signaler une déclaration optionnelle comme inutilisée.
            used_dists.add(normalize(name))

    # (3) Déclaré en runtime mais jamais importé (avertissement).
    used_names = {normalize(n) for n in required_third | optional_third}
    for dist in sorted(declared_runtime):
        if dist not in used_dists and dist not in used_names:
            warnings.append(
                f"'{dist}' est déclaré dans requirements.txt mais ne semble jamais "
                f"importé par le client.")

    # ----- Rapport -----
    print(f"Fichiers runtime analysés : {len(runtime_files())}")
    print(f"Imports tiers requis      : {sorted(required_third)}")
    print(f"Imports tiers optionnels  : {sorted(optional_third)}")
    print(f"Déclarés (runtime)        : {sorted(declared_runtime)}")
    print()

    for w in warnings:
        print(f"[AVERTISSEMENT] {w}")
    for e in errors:
        print(f"[ERREUR] {e}")

    if errors:
        print(f"\nAudit ÉCHOUÉ : {len(errors)} erreur(s), {len(warnings)} avertissement(s).")
        return 1
    print(f"\nAudit OK : dépendances cohérentes ({len(warnings)} avertissement(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
