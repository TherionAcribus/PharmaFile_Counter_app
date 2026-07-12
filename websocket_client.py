import socketio
import json
import logging
import random
import threading
from PySide6.QtCore import Signal, QThread

from socket_auth import build_socket_auth_headers
from counter_id_utils import coerce_counter_id

logger = logging.getLogger("appcomptoir.websocket")

# Reconnexion : backoff exponentiel plafonné + jitter. La bibliothèque
# python-socketio gère aussi une reconnexion automatique — on la DÉSACTIVE
# (reconnection=False) pour ne pas la doubler avec notre boucle (sinon deux
# mécanismes concurrents, risque de connexions simultanées).
RECONNECT_BASE_DELAY = 1.0   # délai de base (s) pour la 1re tentative
RECONNECT_MAX_DELAY = 30.0   # plafond (s)


def compute_reconnect_delay(attempt, base=RECONNECT_BASE_DELAY,
                            cap=RECONNECT_MAX_DELAY, rand=random.random):
    """Délai avant la reconnexion n° ``attempt`` (>=1) : backoff exponentiel
    plafonné avec jitter (« equal jitter »).

    exp = min(cap, base * 2**(attempt-1)) ; délai = exp/2 + jitter dans [0, exp/2].
    Garantit un minimum (exp/2) tout en dispersant les tentatives (jitter) et en
    bornant la croissance (plafond) -> nombre de tentatives maîtrisé quand le
    serveur est indisponible."""
    exp = min(cap, base * (2 ** (max(1, attempt) - 1)))
    return exp / 2 + (exp / 2) * rand()


def _safe_origin(data):
    """Extrait l'origine (catégorie non sensible) d'une notification, pour les
    logs, sans exposer le contenu (message, données patient)."""
    try:
        payload = data.get("data") if isinstance(data, dict) else None
        if isinstance(payload, str):
            payload = json.loads(payload)
        if isinstance(payload, dict):
            return payload.get("origin", "?")
    except Exception:
        pass
    return "?"


class WebSocketClient(QThread):
    # (liste_patients, revision) : la révision permet au thread principal
    # d'écarter les messages périmés/dupliqués et de détecter un trou.
    new_patient = Signal(object, object)
    new_notification = Signal(str)
    my_patient = Signal(object)
    change_paper = Signal(object)
    change_paper_button = Signal(str)
    change_auto_calling = Signal(object)
    update_auto_calling = Signal(object)
    disconnect_user = Signal(object)
    ws_connection_status = Signal(bool, int, bool)
    connection_lost = Signal(int)
    refresh_after_clear_patient_list = Signal(bool)

    def __init__(self, parent, username="Counter App"):
        super().__init__()
        self.parent = parent
        self.username = username
        self.previously_connected = False
        # Drapeau d'arrêt : la boucle de (re)connexion s'y réfère pour se
        # terminer proprement au lieu de se reconnecter indéfiniment.
        self._stop = threading.Event()

        # On garde l'URL HTTP/HTTPS d'origine et on laisse python-socketio
        # négocier le transport (polling puis montée en WebSocket). Forcer
        # ws://wss:// manuellement est inutile et fragile.
        self.web_url = self.parent.web_url

        # Logs verbeux (chaque ping/pong compris) réservés au mode debug déjà
        # exposé dans les Préférences ("Garder ouverte la fenêtre de log après
        # le démarrage"), pas systématiques en prod.
        debug = getattr(self.parent, "debug_window", False)
        # reconnection=False : notre boucle run() est le SEUL mécanisme de
        # reconnexion (backoff/jitter/plafond, relecture du jeton, drapeau
        # d'arrêt). Laisser la reconnexion interne active la doublerait.
        self.sio = socketio.Client(reconnection=False, logger=debug, engineio_logger=debug)
        self.setup_socketio_events()

    def setup_socketio_events(self):
        # Connexion aux événements WebSocket
        self.sio.on('connect', self.on_connect, namespace='/socket_app_counter')
        self.sio.on('disconnect', self.on_disconnect)
        self.sio.on('update', self.on_update, namespace='/socket_app_counter')
        self.sio.on('paper', self.on_paper, namespace='/socket_app_counter')
        self.sio.on('notification', self.on_notification, namespace='/socket_app_counter')     
        self.sio.on('change_auto_calling', self.on_change_auto_calling, namespace='/socket_app_counter')
        self.sio.on('update_auto_calling', self.on_update_auto_calling, namespace='/socket_app_counter')   
        self.sio.on('disconnect_user', self.on_disconnect_user, namespace='/socket_app_counter')
        self.sio.on('update_patient_list', self.on_update_patient_list, namespace='/socket_app_counter')
        self.sio.on('refresh_after_clear_patient_list', self.on_refresh_after_clear_patient_list, namespace='/socket_app_counter')

    def _current_token(self):
        return getattr(self.parent, "app_token", None)

    def _refresh_token_if_possible(self):
        """Tente de renouveler le jeton applicatif avant une reconnexion.

        Utile quand la connexion a été refusée parce que le jeton avait expiré :
        la tentative suivante présentera alors un jeton frais."""
        refresh = getattr(self.parent, "try_refresh_app_token", None)
        if callable(refresh):
            try:
                refresh()
            except Exception as e:
                logger.warning("Renouvellement du jeton avant reconnexion échoué : %s", e)

    def run(self):
        reconnection_attempts = 0

        while not self._stop.is_set():
            try:
                if reconnection_attempts > 0:
                    delay = compute_reconnect_delay(reconnection_attempts)
                    logger.info("Nouvelle tentative de connexion %d dans %.1fs",
                                reconnection_attempts, delay)
                    # Attente interruptible : stop() la débloque immédiatement.
                    if self._stop.wait(delay):
                        break

                # Drapeau revérifié juste avant de (re)connecter.
                if self._stop.is_set():
                    break

                # Garde anti double-connexion : jamais deux connexions simultanées.
                # (Avec reconnection=False, sio est déconnecté après wait(), mais on
                # s'en assure explicitement avant chaque nouvelle connexion.)
                if self.sio.connected:
                    self.sio.disconnect()

                # Jeton relu à CHAQUE tentative : une reconnexion après
                # renouvellement utilise automatiquement le nouveau jeton.
                headers = build_socket_auth_headers(self.username, self._current_token())
                logger.info("Connexion à %s/socket_app_counter", self.web_url)
                self.sio.connect(f"{self.web_url}/socket_app_counter", headers=headers)

                logger.info("Connexion WebSocket établie")
                reconnection_attempts = 0
                self.sio.wait()  # rend la main sur déconnexion (dont stop())

            except socketio.exceptions.ConnectionError as e:
                if self._stop.is_set():
                    break
                reconnection_attempts += 1
                logger.warning("Échec de connexion (tentative %d) : %s", reconnection_attempts, e)
                self.connection_lost.emit(reconnection_attempts)  # Émet le signal de déconnexion
                # La connexion a pu être refusée pour cause de jeton expiré :
                # on le renouvelle pour que la prochaine tentative soit valide.
                self._refresh_token_if_possible()

        logger.info("Boucle WebSocket terminée")

    def stop(self, timeout_ms=3000):
        """Arrêt propre et borné : lève le drapeau, déconnecte Socket.IO (ce qui
        débloque sio.wait()), puis attend la fin du thread au plus timeout_ms.
        Retourne True si le thread s'est bien terminé dans le délai."""
        self._stop.set()
        try:
            self.sio.disconnect()
        except Exception as e:
            logger.debug("Déconnexion Socket.IO à l'arrêt : %s", e)
        self.quit()
        return self.wait(timeout_ms)

    def on_connect(self):
        logger.info("WebSocket connecté")
        self.ws_connection_status.emit(True, 0, True)

    def on_disconnect(self):
        logger.info("WebSocket déconnecté")
        self.connection_lost.emit(0)

    def on_paper(self, data):
        logger.debug("Événement 'paper' reçu")
        self.change_paper.emit(data)
        
    def _event_targets_this_counter(self, data):
        """ True si l'évènement cible ce comptoir. Comparaison entière robuste :
        le serveur peut envoyer counter_id en int ou en chaîne ; parent.counter_id
        est déjà normalisé en entier. """
        payload = data.get("data") if isinstance(data, dict) else None
        cid = payload.get("counter_id") if isinstance(payload, dict) else None
        return coerce_counter_id(cid) == self.parent.counter_id

    def on_change_auto_calling(self, data):
        if self._event_targets_this_counter(data):
            self.change_auto_calling.emit(data)

    def on_update_auto_calling(self, data):
        if self._event_targets_this_counter(data):
            self.update_auto_calling.emit(data)

    def on_disconnect_user(self, data):
        logger.debug("Événement 'disconnect_user' reçu")
        if self._event_targets_this_counter(data):
            self.disconnect_user.emit(data)

    def on_notification(self, data):
        logger.debug("Notification reçue (origin=%s)", _safe_origin(data))

        # Parser data["data"] si c'est une chaîne JSON
        if isinstance(data["data"], str):
            try:
                notification_data = json.loads(data["data"])
            except json.JSONDecodeError:
                logger.warning("Notification illisible (JSON invalide)")
                return
        else:
            notification_data = data["data"]

        # si on affiche à tous ou si on affiche seulement pour le counter
        # (comparaison entière robuste : flag peut être un id, une liste d'ids,
        # en int ou en chaîne selon le serveur)
        flag = data["flag"]
        targets_all = not flag
        targets_this = coerce_counter_id(flag) == self.parent.counter_id
        targets_in_list = isinstance(flag, list) and self.parent.counter_id in [coerce_counter_id(f) for f in flag]
        if targets_all or targets_this or targets_in_list:
            self.new_notification.emit(data['data'])
        
        # si la notification concerne le papier, mettre à jour le bouton
        if notification_data["origin"] in ["no_paper", "low_paper", "paper_ok"]:
            self.change_paper_button.emit(notification_data["origin"])

    def on_update_patient_list(self, data):
        try:
            if isinstance(data, str):
                data = json.loads(data)
            if isinstance(data["data"], str):
                data["data"] = json.loads(data["data"])
            revision = data.get("revision") if isinstance(data, dict) else None
            payload = data["data"]
            logger.debug("Liste de patients reçue (%s patients, revision=%s)",
                         len(payload) if isinstance(payload, list) else "?", revision)
            self.new_patient.emit(payload, revision)
            self.my_patient.emit(payload)

        except json.JSONDecodeError as e:
            logger.warning("Liste de patients illisible (JSON invalide) : %s", e)

    def on_refresh_after_clear_patient_list(self, data):
        logger.debug("Rafraîchissement après purge de la liste des patients")
        self.refresh_after_clear_patient_list.emit(True)

    def on_update(self, data):
        logger.debug("Événement 'update' reçu")
        # Normalement cette partie peut être supprimée
        try:
            if isinstance(data, str):
                data = json.loads(data)
            if data['flag'] == 'update_patient_list':
                if isinstance(data["data"], str):
                    data["data"] = json.loads(data["data"])
                self.new_patient.emit(data["data"], data.get("revision"))
            elif data['flag'] == 'my_patient':
                self.my_patient.emit(data["data"])
        except json.JSONDecodeError as e:
            logger.warning("Événement 'update' illisible (JSON invalide) : %s", e)
            

