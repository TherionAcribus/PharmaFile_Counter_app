import logging
import time
import uuid
from PySide6.QtCore import QThread, Signal
from requests.exceptions import RequestException

logger = logging.getLogger("appcomptoir.connections")

# (connect_timeout, read_timeout) en secondes. Evite qu'une requête reste
# bloquée indéfiniment quand le serveur ou le réseau ne répond plus
# (proxy/box qui coupe silencieusement, serveur qui ne répond plus, etc.)
DEFAULT_TIMEOUT = (5, 10)


class RequestThread(QThread):
    result = Signal(float, str, int)

    def __init__(self, url, session, method='GET', data=None, headers=None, timeout=DEFAULT_TIMEOUT, reauth_callback=None):
        super().__init__()
        self.url = url
        self.session = session
        self.method = method
        self.data = data
        self.headers = headers
        self.timeout = timeout
        # Appelé (sans argument, retourne un bool) si le serveur répond 401.
        # Doit renouveler le token sur la session partagée. La requête est
        # alors rejouée une fois avec le nouveau token.
        self.reauth_callback = reauth_callback

    def _send(self):
        if self.method == 'GET':
            return self.session.get(self.url, headers=self.headers, timeout=self.timeout)
        elif self.method == 'POST':
            return self.session.post(self.url, data=self.data, headers=self.headers, timeout=self.timeout)
        else:
            raise ValueError(f"Méthode HTTP non supportée: {self.method}")

    def run(self):
        # Identifiant de corrélation : permet de relier, dans les logs, le début
        # de la requête, l'éventuelle ré-authentification et l'erreur finale,
        # sans exposer d'URL/donnée sensible. La réponse (corps) n'est jamais
        # journalisée ici (peut contenir des données patient).
        cid = uuid.uuid4().hex[:8]
        logger.debug("[cid=%s] %s %s", cid, self.method, self.url)
        start_time = time.time()
        try:
            response = self._send()
            if response.status_code == 401 and self.reauth_callback and self.reauth_callback():
                logger.info("[cid=%s] 401 reçu, ré-authentification puis nouvel essai", cid)
                response = self._send()

            elapsed_time = time.time() - start_time
            logger.debug("[cid=%s] réponse %s en %.3fs", cid, response.status_code, elapsed_time)
            self.result.emit(elapsed_time, response.text, response.status_code)
        except RequestException as e:
            elapsed_time = time.time() - start_time
            logger.warning("[cid=%s] échec réseau après %.3fs : %s", cid, elapsed_time, e)
            self.result.emit(elapsed_time, str(e), 0)
