import socketio
import json
import logging
import time
from PySide6.QtCore import Signal, QThread

from socket_auth import build_socket_auth_headers

logger = logging.getLogger("appcomptoir.websocket")


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

        # On garde l'URL HTTP/HTTPS d'origine et on laisse python-socketio
        # négocier le transport (polling puis montée en WebSocket). Forcer
        # ws://wss:// manuellement est inutile et fragile.
        self.web_url = self.parent.web_url

        # Logs verbeux (chaque ping/pong compris) réservés au mode debug déjà
        # exposé dans les Préférences ("Garder ouverte la fenêtre de log après
        # le démarrage"), pas systématiques en prod.
        debug = getattr(self.parent, "debug_window", False)
        self.sio = socketio.Client(logger=debug, engineio_logger=debug)
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
        max_reconnection_delay = 30
        initial_delay = 5

        while True:
            try:
                if reconnection_attempts > 0:
                    delay = min(initial_delay * reconnection_attempts, max_reconnection_delay)
                    logger.info("Nouvelle tentative de connexion %d dans %ds", reconnection_attempts, delay)
                    time.sleep(delay)

                # Jeton relu à CHAQUE tentative : une reconnexion après
                # renouvellement utilise automatiquement le nouveau jeton.
                headers = build_socket_auth_headers(self.username, self._current_token())
                logger.info("Connexion à %s/socket_app_counter", self.web_url)
                self.sio.connect(f"{self.web_url}/socket_app_counter", headers=headers)

                logger.info("Connexion WebSocket établie")
                reconnection_attempts = 0
                self.sio.wait()

            except socketio.exceptions.ConnectionError as e:
                reconnection_attempts += 1
                logger.warning("Échec de connexion (tentative %d) : %s", reconnection_attempts, e)
                self.connection_lost.emit(reconnection_attempts)  # Émet le signal de déconnexion
                # La connexion a pu être refusée pour cause de jeton expiré :
                # on le renouvelle pour que la prochaine tentative soit valide.
                self._refresh_token_if_possible()

    def stop(self):
        self.sio.disconnect()
        self.quit()
        self.wait()

    def on_connect(self):
        logger.info("WebSocket connecté")
        self.ws_connection_status.emit(True, 0, True)

    def on_disconnect(self):
        logger.info("WebSocket déconnecté")
        self.connection_lost.emit(0)

    def on_paper(self, data):
        logger.debug("Événement 'paper' reçu")
        self.change_paper.emit(data)
        
    def on_change_auto_calling(self, data):
        if self.parent.counter_id == int(data["data"]['counter_id']):
            self.change_auto_calling.emit(data)

    def on_update_auto_calling(self, data):
        if self.parent.counter_id == int(data["data"]['counter_id']):
            self.update_auto_calling.emit(data)

    def on_disconnect_user(self, data):
        logger.debug("Événement 'disconnect_user' reçu")
        if self.parent.counter_id == int(data["data"]['counter_id']):
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
        if (
            not data["flag"] or  # Cas où tout le monde peut voir la notification
            data["flag"] == self.parent.counter_id or  # Cas où le counter_id correspond directement
            (isinstance(data["flag"], list) and self.parent.counter_id in data["flag"])  # Cas où flag est une liste et contient le counter_id
        ):
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
            

