"""Propriétés Windows de l'exe (version, nom du produit), pour les .spec.

Rangé dans tools/ : seul le build dépend de PyInstaller, jamais l'App
elle-même (cf. tools/check_dependencies.py). La version vient de la source
unique app_version.py.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from app_version import APP_VERSION, version_tuple  # noqa: E402


def windows_version_info(version: str = APP_VERSION):
    """Propriétés Windows de l'exe (clic droit › Propriétés › Détails),
    construites pour les .spec."""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo,
    )
    numbers = (*version_tuple(version), 0)
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            # 040C = français, 04B0 = Unicode.
            StringFileInfo([StringTable("040C04B0", [
                StringStruct("CompanyName", "Flowelis"),
                StringStruct("FileDescription", "Flowelis Comptoir"),
                StringStruct("FileVersion", version),
                StringStruct("InternalName", "FlowelisComptoir"),
                StringStruct("OriginalFilename", "FlowelisComptoir.exe"),
                StringStruct("ProductName", "Flowelis Comptoir"),
                StringStruct("ProductVersion", version),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x040C, 1200])]),
        ],
    )
