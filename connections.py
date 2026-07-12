"""Gestionnaire réseau centralisé de l'App comptoir.

Un unique worker (thread dédié) possède la SEULE ``requests.Session`` et traite
une file de requêtes en série. Cela élimine tout accès concurrent à la session
(et à ses en-têtes, dont le jeton) et centralise :

- l'ajout du jeton applicatif (porté par la session, posé sous verrou) ;
- le timeout de chaque requête ;
- un résultat homogène ``NetResult`` (statut, données JSON éventuelles, message
  utilisateur, détail technique) ; ``status == 0`` pour une erreur réseau/timeout ;
- le renouvellement du jeton sur 401 avec un seul rejeu ;
- l'idempotence (en-tête ``X-Idempotency-Key`` par requête).

Deux modes d'utilisation :
- asynchrone (thread GUI) : ``make_handle(...)`` -> ``RequestHandle`` dont le
  signal ``result(NetResult)`` est émis vers le thread appelant ; ``.start()``
  met la requête en file ;
- bloquant (threads de fond : démarrage, resync) : ``request_blocking(...)`` (rend
  un ``NetResult``) et ``fetch_token_blocking(...)`` attendent le worker.
  Ne JAMAIS les appeler depuis le worker lui-même (interblocage) ni, en pratique,
  depuis le thread GUI (il bloquerait).
"""

import logging
import queue
import threading
import time
import uuid

import requests
from requests.exceptions import RequestException
from PySide6.QtCore import QObject, QThread, Signal

from net_core import perform_with_reauth
from net_result import NetResult

logger = logging.getLogger("appcomptoir.connections")

# (connect_timeout, read_timeout) en secondes. Evite qu'une requête reste
# bloquée indéfiniment quand le serveur ou le réseau ne répond plus.
DEFAULT_TIMEOUT = (5, 10)

# Sentinelle d'arrêt du worker.
_STOP = object()


class RequestHandle(QObject):
    """Résultat asynchrone d'une requête.

    ``result`` porte un ``NetResult`` (issue homogène : statut, données JSON
    éventuelles, message utilisateur, détail technique). ``finished`` est émis
    juste après (pour toujours libérer l'état occupé d'un bouton, y compris en
    cas d'erreur).
    """
    result = Signal(object)   # NetResult
    finished = Signal()

    def __init__(self, manager, spec):
        super().__init__()
        self._manager = manager
        self._spec = spec
        self._started = False

    def start(self):
        """Met la requête en file (idempotent). Compatible avec l'ancien usage
        ``handle.result.connect(...); handle.start()``."""
        if not self._started:
            self._started = True
            self._manager._enqueue(self, self._spec)


class _RequestSpec:
    __slots__ = ("url", "method", "data", "headers", "idempotency_key", "timeout")

    def __init__(self, url, method, data, headers, idempotency_key, timeout=None):
        self.url = url
        self.method = method
        self.data = data
        self.headers = headers
        self.idempotency_key = idempotency_key
        self.timeout = timeout  # surcharge le timeout par défaut si fourni


class _Job:
    """Élément de file : soit une requête (handle async ou event bloquant), soit
    un renouvellement de jeton."""
    __slots__ = ("kind", "spec", "handle", "event", "result_box")

    def __init__(self, kind, spec=None, handle=None, event=None):
        self.kind = kind            # "request" | "token"
        self.spec = spec
        self.handle = handle        # RequestHandle si async, sinon None
        self.event = event          # threading.Event si bloquant
        self.result_box = {}        # rempli pour les jobs bloquants


class _NetworkWorker(QThread):
    def __init__(self, manager):
        super().__init__()
        self._manager = manager

    def run(self):
        self._manager._run_loop()


class NetworkManager(QObject):
    """Gestionnaire réseau centralisé (voir docstring du module)."""

    # Émis (vers le thread GUI) après un renouvellement de jeton réussi, pour que
    # la fenêtre principale mette à jour son app_token (WebSocket, login...).
    token_refreshed = Signal(str)
    token_failed = Signal()

    def __init__(self, token_url_provider, secret_provider,
                 timeout=DEFAULT_TIMEOUT, parent=None):
        super().__init__(parent)
        self._token_url_provider = token_url_provider
        self._secret_provider = secret_provider
        self._timeout = timeout

        self._session = requests.Session()
        self._session_lock = threading.Lock()  # protège l'écriture des en-têtes (jeton)
        self._queue = queue.Queue()
        self._stopping = False
        self._worker = _NetworkWorker(self)
        self._worker.start()

    # ------------------------------------------------------------------ #
    # API publique (thread GUI et threads de fond)
    # ------------------------------------------------------------------ #
    def make_handle(self, url, method="GET", data=None, headers=None,
                    idempotency_key=None, timeout=None):
        """Crée un RequestHandle non démarré (compatibilité make_request_thread)."""
        spec = _RequestSpec(url, method, data, headers, idempotency_key, timeout)
        return RequestHandle(self, spec)

    def request_blocking(self, url, method="GET", data=None, headers=None,
                         idempotency_key=None, timeout=None, timeout_s=30):
        """Exécute une requête et attend le résultat (threads de fond seulement).

        ``timeout`` surcharge le timeout HTTP ; ``timeout_s`` borne l'attente du
        résultat côté appelant. Retourne un ``NetResult``."""
        spec = _RequestSpec(url, method, data, headers, idempotency_key, timeout)
        job = _Job("request", spec=spec, event=threading.Event())
        self._queue.put(job)
        if not job.event.wait(timeout_s):
            return NetResult.network_error("timeout interne du gestionnaire réseau")
        return job.result_box.get("result", NetResult.network_error("résultat indisponible"))

    def fetch_token_blocking(self, timeout_s=30):
        """Renouvelle le jeton et attend (threads de fond). Retourne le jeton ou
        None."""
        job = _Job("token", event=threading.Event())
        self._queue.put(job)
        if not job.event.wait(timeout_s):
            return None
        return job.result_box.get("token")

    def current_token(self):
        with self._session_lock:
            return self._session.headers.get("X-App-Token")

    def stop(self, timeout_ms=3000):
        """Arrête le worker (idempotent) et attend au plus ``timeout_ms``.

        Les jobs encore en file sont purgés et leurs attentes débloquées par le
        worker (voir _drain_pending), pour ne jamais laisser un appelant bloquant
        (StartupWorker/ResyncWorker) suspendu. Retourne True si le worker s'est
        terminé dans le délai."""
        if self._stopping:
            return self._worker.wait(timeout_ms)
        self._stopping = True
        self._queue.put(_STOP)
        return self._worker.wait(timeout_ms)

    # ------------------------------------------------------------------ #
    # Interne — appelé depuis le thread appelant
    # ------------------------------------------------------------------ #
    def _enqueue(self, handle, spec):
        self._queue.put(_Job("request", spec=spec, handle=handle))

    # ------------------------------------------------------------------ #
    # Interne — TOUT ce qui suit s'exécute DANS le worker
    # ------------------------------------------------------------------ #
    def _run_loop(self):
        while True:
            job = self._queue.get()
            if job is _STOP:
                self._drain_pending()
                break
            try:
                if job.kind == "token":
                    self._handle_token_job(job)
                else:
                    self._handle_request_job(job)
            except Exception:  # garde-fou : le worker ne doit jamais mourir
                logger.exception("Erreur inattendue dans le worker réseau")
                if job.event is not None:
                    job.event.set()

    def _drain_pending(self):
        """À l'arrêt : vide la file et débloque immédiatement les jobs restants
        (résultat d'échec), pour qu'aucun appelant bloquant ne reste suspendu et
        qu'aucun handle async n'attende indéfiniment son 'finished'."""
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                break
            if job is _STOP:
                continue
            aborted = NetResult.network_error("arrêt en cours")
            if job.handle is not None:
                job.handle.result.emit(aborted)
                job.handle.finished.emit()
            if job.event is not None:
                job.result_box["result"] = aborted
                job.result_box["token"] = None
                job.event.set()

    def _handle_request_job(self, job):
        result = self._execute(job.spec)
        if job.handle is not None:
            job.handle.result.emit(result)
            job.handle.finished.emit()
        if job.event is not None:
            job.result_box["result"] = result
            job.event.set()

    def _handle_token_job(self, job):
        token = self._do_token_fetch()
        if job.event is not None:
            job.result_box["token"] = token
            job.event.set()

    def _execute(self, spec):
        """Exécute la requête et renvoie TOUJOURS un NetResult (jamais d'exception
        propagée) : le handle async émet donc toujours, et un bouton quitte
        toujours l'état « attente »."""
        cid = uuid.uuid4().hex[:8]
        start = time.time()
        try:
            resp = perform_with_reauth(
                send=lambda: self._send(spec),
                reauth=self._reauth,
            )
            elapsed = time.time() - start
            content_type = resp.headers.get("Content-Type") if getattr(resp, "headers", None) else None
            logger.debug("[cid=%s] %s %s -> %s en %.3fs", cid, spec.method, spec.url,
                         resp.status_code, elapsed)
            # Le JSON n'est décodé que si le content-type est compatible ; sinon
            # data reste None (réponse HTML/vide/malformée -> pas de crash).
            return NetResult.from_response(resp.status_code, resp.text, content_type)
        except RequestException as e:
            elapsed = time.time() - start
            logger.warning("[cid=%s] échec réseau après %.3fs : %s", cid, elapsed, e)
            return NetResult.network_error(str(e))
        except Exception as e:
            elapsed = time.time() - start
            logger.exception("[cid=%s] erreur inattendue de requête", cid)
            return NetResult.network_error(str(e))

    def _send(self, spec):
        headers = dict(spec.headers) if spec.headers else {}
        if spec.idempotency_key:
            headers["X-Idempotency-Key"] = spec.idempotency_key
        timeout = spec.timeout or self._timeout
        # Le jeton courant est porté par la session (posé sous verrou). La session
        # n'est lue/écrite que dans ce worker -> aucun accès concurrent.
        if spec.method == "GET":
            return self._session.get(spec.url, headers=headers or None, timeout=timeout)
        elif spec.method == "POST":
            return self._session.post(spec.url, data=spec.data, headers=headers or None, timeout=timeout)
        raise ValueError(f"Méthode HTTP non supportée: {spec.method}")

    def _reauth(self):
        """Renouvellement du jeton déclenché par un 401 (dans le worker)."""
        logger.info("401 reçu -> renouvellement du jeton puis rejeu unique")
        return self._do_token_fetch() is not None

    def _do_token_fetch(self):
        """POST le secret applicatif pour obtenir un jeton, met à jour la session
        et notifie le thread GUI. Retourne le jeton (str) ou None. Exécuté dans le
        worker uniquement."""
        url = self._token_url_provider()
        secret = self._secret_provider()
        try:
            resp = self._session.post(url, data={"app_secret": secret}, timeout=self._timeout)
        except RequestException as e:
            logger.warning("Échec réseau lors de l'obtention du jeton : %s", e)
            self.token_failed.emit()
            return None

        if resp.status_code == 200:
            try:
                token = resp.json().get("token")
            except ValueError:
                token = None
            with self._session_lock:
                if token:
                    self._session.headers["X-App-Token"] = token
                else:
                    self._session.headers.pop("X-App-Token", None)
            if token:
                self.token_refreshed.emit(token)
                logger.debug("Jeton applicatif obtenu et installé")
                return token

        with self._session_lock:
            self._session.headers.pop("X-App-Token", None)
        logger.warning("Obtention du jeton refusée (statut %s)", resp.status_code)
        self.token_failed.emit()
        return None
