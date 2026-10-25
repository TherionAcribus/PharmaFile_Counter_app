# -*- mode: python ; coding: utf-8 -*-

# Variante « onefile » de FlowelisComptoir.spec : produit un exécutable unique
# autonome (dist/FlowelisComptoir.exe) au lieu du dossier
# dist/FlowelisComptoir/.
# Utilisée par le workflow de release (.github/workflows/release.yml) pour les
# utilisateurs qui veulent un seul fichier à copier ; le build « dossier » reste
# recommandé en usage courant (démarrage plus rapide, pas d'extraction des
# ressources dans %TEMP% à chaque lancement).
#
# Les listes ci-dessous DOIVENT rester identiques à celles de
# FlowelisComptoir.spec :
# tests/test_build_spec.py compare les deux fichiers et échoue en cas de
# divergence (risque historique : un main.spec obsolète produisait un binaire
# silencieusement dégradé).

SERVER_AND_DEV_EXCLUDES = [
    'flask', 'werkzeug', 'jinja2', 'itsdangerous', 'redis', 'gunicorn',
    'sqlalchemy', 'flask_sqlalchemy', 'flask_login', 'flask_socketio',
    'celery', 'eventlet', 'gevent', 'pymysql', 'mysql', 'alembic',
    'pytest', 'line_profiler', 'pyinstaller',
]

KEYRING_HIDDENIMPORTS = [
    'keyring.backends.Windows',
    'keyring.backends.macOS',
    'keyring.backends.SecretService',
    'keyring.backends.chainer',
    'keyring.backends.fail',
    'win32ctypes.core',            # dépendance du backend Windows (pywin32-ctypes)
    'win32ctypes.core.cffi',
    'win32ctypes.core.ctypes',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets'), ('skins', 'skins'), ('templates', 'templates')],
    hiddenimports=KEYRING_HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=SERVER_AND_DEV_EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='FlowelisComptoir',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico',
)
