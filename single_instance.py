"""Une seule App comptoir à la fois par utilisateur Windows.

Deux instances ouvertes (démarrage automatique + double-clic, par exemple)
posaient chacune leurs raccourcis globaux : un seul appui sur « Prochain
patient » appelait DEUX patients, et les deux fenêtres se disputaient le même
comptoir. Désormais la seconde instance ne démarre pas : elle demande à la
première de passer au premier plan, puis se ferme.

Deux mécanismes complémentaires :

- un fichier verrou (``QLockFile``) dans le dossier temporaire de
  l'utilisateur. C'est lui qui décide qui est « la première ». Un verrou laissé
  par une App plantée est reconnu (processus disparu) et repris
  automatiquement ;
- un canal local (``QLocalServer``) ouvert par la première instance : la
  seconde s'y connecte pour lui demander de se montrer, et attend son accusé.

Le verrou est par utilisateur : sur un serveur de sessions (plusieurs
utilisateurs sur une même machine), chacun garde son App. Si le verrou ne peut
pas être créé (dossier inaccessible), l'App démarre quand même : mieux vaut un
risque de doublon qu'un comptoir qui ne s'ouvre plus.
"""

from __future__ import annotations

import getpass
import hashlib
import logging
import os
import sys
import time

from PySide6.QtCore import QLockFile, QObject, QStandardPaths, QThread, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from app_identity import APPLICATION_NAME, ORGANIZATION_NAME

logger = logging.getLogger("appcomptoir.single_instance")

ACTIVATE_REQUEST = b"activate\n"
ACTIVATE_ACK = b"ok\n"
#: Délai total accordé à la première instance pour accuser réception (ms).
NOTIFY_TIMEOUT_MS = 2000
#: Pause entre deux tentatives de connexion au canal (ms) : la première
#: instance peut avoir pris le verrou sans avoir encore ouvert son canal.
_CONNECT_RETRY_MS = 100


def _current_user() -> str:
    try:
        return getpass.getuser()
    except (OSError, KeyError, ImportError):
        return os.environ.get("USERNAME") or os.environ.get("USER") or "inconnu"


def instance_key(user: str | None = None) -> str:
    """Nom du verrou et du canal, propre à l'application ET à l'utilisateur.
    L'identifiant est haché : un nom d'utilisateur peut contenir des
    caractères refusés dans un nom de canal."""
    user = _current_user() if user is None else user
    digest = hashlib.sha256(user.lower().encode("utf-8")).hexdigest()[:16]
    return f"{ORGANIZATION_NAME}{APPLICATION_NAME}-{digest}"


def allow_foreground_switch() -> None:
    """Windows ne laisse un processus passer au premier plan que s'il y est
    autorisé par celui qui l'est déjà. La seconde instance vient d'être lancée
    par l'utilisateur (elle a la main) : elle cède ce droit à la première."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        asfw_any = -1
        ctypes.windll.user32.AllowSetForegroundWindow(asfw_any)
    except (OSError, AttributeError):
        pass


class SingleInstance(QObject):
    """Verrou d'instance unique + canal de réveil de la première instance."""

    #: Émis (dans le thread graphique) quand une autre instance demande à
    #: la première de se montrer.
    activation_requested = Signal()

    def __init__(self, key: str | None = None, lock_dir: str | None = None):
        super().__init__()
        self.key = key or instance_key()
        lock_dir = lock_dir or QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.TempLocation)
        self.lock_path = os.path.join(lock_dir, self.key + ".lock")
        self._lock = QLockFile(self.lock_path)
        # Verrou périmé uniquement si son processus a disparu (jamais par
        # ancienneté : une App peut tourner des jours).
        self._lock.setStaleLockTime(0)
        self._server: QLocalServer | None = None
        self.is_primary = False

    # --- première instance ---------------------------------------------------

    def acquire(self) -> bool:
        """Tente de devenir l'instance principale. Renvoie False si une autre
        instance tourne déjà pour cet utilisateur."""
        if self._lock.tryLock(0):
            self.is_primary = True
            self._listen()
            return True
        if self._lock.error() == QLockFile.LockError.LockFailedError:
            return False
        # Verrou impossible à créer (droits, disque) : on démarre quand même.
        logger.warning("Verrou d'instance unique indisponible (%s) : démarrage sans protection.",
                       self._lock.error())
        self.is_primary = True
        self._listen()
        return True

    def _listen(self) -> None:
        server = QLocalServer(self)
        # Canal réservé à l'utilisateur courant.
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        # Unix : retire un canal laissé par une instance plantée (sans effet
        # sous Windows). Sans risque : nous détenons le verrou.
        QLocalServer.removeServer(self.key)
        if not server.listen(self.key):
            logger.warning("Canal d'instance unique indisponible : %s", server.errorString())
            return
        server.newConnection.connect(self._on_new_connection)
        self._server = server

    def _on_new_connection(self) -> None:
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if socket is None:
                break
            socket.readyRead.connect(lambda s=socket: self._on_ready_read(s))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self._on_ready_read(socket)

    def _on_ready_read(self, socket: QLocalSocket) -> None:
        if not socket.canReadLine():
            return
        request = bytes(socket.readLine().data())
        if request != ACTIVATE_REQUEST:
            socket.disconnectFromServer()
            return
        logger.info("Seconde instance de l'App lancée : la fenêtre existante est ramenée au premier plan.")
        self.activation_requested.emit()
        socket.write(ACTIVATE_ACK)
        socket.flush()
        socket.disconnectFromServer()

    def release(self) -> None:
        """Libère le verrou et ferme le canal (avant un redémarrage de l'App :
        la nouvelle instance doit pouvoir les prendre)."""
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._lock.isLocked():
            self._lock.unlock()
        self.is_primary = False

    # --- seconde instance ------------------------------------------------------

    def notify_primary(self, timeout_ms: int = NOTIFY_TIMEOUT_MS) -> bool:
        """Demande à l'instance principale de se montrer. Renvoie True si elle
        a répondu (False : elle ne répond pas, ou pas de canal)."""
        allow_foreground_switch()
        deadline = time.monotonic() + timeout_ms / 1000

        def left_ms() -> int:
            return max(1, int((deadline - time.monotonic()) * 1000))

        socket = QLocalSocket()
        try:
            while True:
                socket.connectToServer(self.key)
                if socket.waitForConnected(min(left_ms(), 500)):
                    break
                socket.abort()
                if time.monotonic() >= deadline:
                    return False
                QThread.msleep(_CONNECT_RETRY_MS)
            socket.write(ACTIVATE_REQUEST)
            if not socket.waitForBytesWritten(left_ms()):
                return False
            while not socket.canReadLine():
                if time.monotonic() >= deadline or not socket.waitForReadyRead(left_ms()):
                    return False
            return bytes(socket.readLine().data()) == ACTIVATE_ACK
        finally:
            socket.abort()
