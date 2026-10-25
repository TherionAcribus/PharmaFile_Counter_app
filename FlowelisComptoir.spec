# -*- mode: python ; coding: utf-8 -*-

# Modules exclus du build : jamais importés par le client (dépendances serveur ou
# outils de dev). Les exclure garantit qu'ils ne gonflent pas la distribution même
# s'ils se trouvent dans l'environnement de compilation (cf. point 23).
SERVER_AND_DEV_EXCLUDES = [
    'flask', 'werkzeug', 'jinja2', 'itsdangerous', 'redis', 'gunicorn',
    'sqlalchemy', 'flask_sqlalchemy', 'flask_login', 'flask_socketio',
    'celery', 'eventlet', 'gevent', 'pymysql', 'mysql', 'alembic',
    'pytest', 'line_profiler', 'pyinstaller',
]

# keyring charge ses backends dynamiquement (entry points / import différé) :
# PyInstaller ne les détecte pas par analyse statique. Sans ces imports cachés,
# le binaire gelé ne trouve AUCUN backend et retombe sur un stockage en clair
# (secret_store le signalerait, mais on veut le stockage sécurisé). On force donc
# l'inclusion du backend Windows (Credential Manager, via win32ctypes) et des
# backends de secours multiplateformes.
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
    [],
    exclude_binaries=True,
    name='PharmaFile',
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
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PharmaFile_v3',
)
