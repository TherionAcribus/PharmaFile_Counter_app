"""Numéro de version de l'App comptoir — source UNIQUE.

Lu par l'App (affichage, journal, rapports envoyés au serveur), par les
fichiers de build (.spec, via tools/exe_version_info.py : propriétés Windows
de l'exe) et par la publication GitHub, qui refuse une étiquette ``vX.Y.Z``
différente de ``APP_VERSION``.

Avant de publier : augmenter ``APP_VERSION`` ici, puis poser l'étiquette
correspondante (``git tag v1.2.0``).

Module sans Qt ni dépendance : le .spec PyInstaller et la CI l'importent.
"""

from __future__ import annotations

import re
import sys

APP_VERSION = "1.2.0"

#: Suffixe des versions lancées depuis les sources (poste de développement) :
#: le serveur ne les confond pas avec une version publiée.
DEV_SUFFIX = "-dev"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def is_frozen() -> bool:
    """Vrai pour l'exe construit par PyInstaller."""
    return bool(getattr(sys, "frozen", False))


def version_tuple(version: str = APP_VERSION) -> tuple[int, int, int]:
    """``"1.2.0"`` → ``(1, 2, 0)``. Lève ValueError si le format est invalide."""
    match = _VERSION_RE.match(version)
    if not match:
        raise ValueError(f"Version invalide : {version!r} (attendu X.Y.Z)")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def reported_version(frozen: bool | None = None) -> str:
    """Version envoyée au serveur : ``1.2.0`` pour l'exe publié,
    ``1.2.0-dev`` lancée depuis les sources."""
    frozen = is_frozen() if frozen is None else frozen
    return APP_VERSION if frozen else APP_VERSION + DEV_SUFFIX


def version_label(frozen: bool | None = None) -> str:
    """Libellé affiché à l'utilisateur."""
    frozen = is_frozen() if frozen is None else frozen
    return f"Version {APP_VERSION}" + ("" if frozen else " (sources)")


def tag_matches(tag: str, version: str = APP_VERSION) -> bool:
    """Vrai si l'étiquette git de publication (``v1.2.0``) correspond."""
    return tag == f"v{version}"


if __name__ == "__main__":
    # Utilisé par la publication GitHub : ``python app_version.py v1.2.0``
    # échoue si l'étiquette ne correspond pas à APP_VERSION.
    if len(sys.argv) != 2:
        print(APP_VERSION)
        sys.exit(0)
    if not tag_matches(sys.argv[1]):
        print(f"L'étiquette {sys.argv[1]} ne correspond pas à APP_VERSION = {APP_VERSION} "
              "(app_version.py) : augmentez APP_VERSION ou corrigez l'étiquette.")
        sys.exit(1)
    print(f"Étiquette {sys.argv[1]} conforme.")
