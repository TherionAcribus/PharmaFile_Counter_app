"""Accès au serveur pour le comptoir — couche REST du client (point 10.8).

``MainWindow`` construisait ses requêtes lui-même : URL, méthode HTTP, clé
d'idempotence, clé anti-doublon, bouton à passer en « occupé », suivi du thread,
refus pendant l'arrêt. Ces détails de protocole sont maintenant ici, derrière des
méthodes qui portent le NOM DE L'ACTION MÉTIER (``pause_current_patient``,
``validate_and_call_next``…). L'interface décide QUOI faire ; ce module sait
COMMENT le demander au serveur.

Ce que la classe garantit, et que chaque appelant n'a plus à refaire :

* toute action modificatrice part en POST (les lectures seules en GET) ;
* pendant l'arrêt de l'application, aucune NOUVELLE requête n'est lancée ;
* une action déjà en cours n'est pas relancée en double (clé d'action) ;
* le bouton déclencheur est remis en état à la fin, même en cas d'erreur ;
* la référence au handle est conservée jusqu'à ``finished`` (sinon le thread
  peut être détruit en pleine requête).

Les URL viennent toutes de ``endpoints`` ; l'adresse du serveur et le numéro de
comptoir sont lus par des *providers* (fonctions sans argument) : ils changent
quand l'utilisateur modifie sa configuration, et cette classe ne peut donc pas
en garder une copie périmée.
"""

import logging
import uuid

import endpoints

_DEFAULT_LOGGER = logging.getLogger("appcomptoir.counter_api")


class CounterApi:
    """Requêtes du comptoir vers le serveur.

    ``network_manager`` : le gestionnaire réseau centralisé (worker unique) ;
    ``tasks`` : le registre des tâches actives (références fortes + anti-doublon) ;
    ``url_provider`` / ``counter_id_provider`` : lecture à la volée de la
    configuration courante ;
    ``is_shutting_down`` : prédicat consulté avant CHAQUE nouvelle requête.
    """

    def __init__(self, network_manager, tasks, url_provider, counter_id_provider,
                 logger=None, is_shutting_down=None):
        self.network_manager = network_manager
        self._tasks = tasks
        self._url = url_provider
        self._counter_id = counter_id_provider
        self.logger = logger or _DEFAULT_LOGGER
        self._is_shutting_down = is_shutting_down or (lambda: False)

    # --- primitives ---------------------------------------------------------

    def make_handle(self, url, method='GET', data=None, headers=None, idempotency_key=None):
        """ Crée un RequestHandle sans le démarrer (l'appelant connecte
        ``result``/``finished`` puis appelle ``start()``). Utilisé par les boutons
        d'icône, qui gèrent eux-mêmes leur cycle de vie. """
        return self.network_manager.make_handle(
            url, method=method, data=data, headers=headers,
            idempotency_key=idempotency_key)

    def _submit(self, url, method, data=None, on_result=None, key=None,
                busy_button=None, idempotent=False):
        """ Crée, suit et démarre une requête. Retourne le handle, ou None si
        l'action a été refusée (arrêt en cours, ou action identique déjà active).

        ``method`` est OBLIGATOIRE : il n'existe pas de valeur par défaut dont une
        action modificatrice pourrait hériter par accident (cf. ``_post``/``_get``).
        """
        if self._is_shutting_down():
            self.logger.debug("Action ignorée (arrêt en cours) : %s", key)
            return None
        if self._tasks.is_active(key):
            self.logger.debug("Action ignorée (déjà en cours) : %s", key)
            return None

        # Clé d'idempotence : une nouvelle par action utilisateur. Si la requête
        # est renvoyée (relance réseau, ou rejeu automatique après un 401), le
        # serveur reconnaît la même clé et ne fait pas avancer la file deux fois.
        idempotency_key = str(uuid.uuid4()) if idempotent else None
        handle = self.make_handle(url, method=method, data=data,
                                  idempotency_key=idempotency_key)
        self._tasks.add(handle, key)
        if busy_button is not None:
            busy_button.set_busy(True)
        if on_result is not None:
            handle.result.connect(on_result)

        def _cleanup():
            self._tasks.remove(handle, key)
            if busy_button is not None:
                busy_button.set_busy(False)

        # Branché avant start() : même si le worker répond très vite, le nettoyage
        # (et le rétablissement du bouton) ne peut pas être manqué.
        handle.finished.connect(_cleanup)
        handle.start()
        return handle

    def _post(self, url, **kwargs):
        """Action modificatrice."""
        return self._submit(url, 'POST', **kwargs)

    def _get(self, url, **kwargs):
        """Lecture seule."""
        return self._submit(url, 'GET', **kwargs)

    def _read_blocking(self, url, expected_type, description):
        """ Lecture bloquante (appelée depuis un thread de fond : démarrage,
        resynchronisation). Retourne la donnée décodée, ou None si le serveur n'a
        pas répondu ce qui était attendu — l'appelant n'a pas à revérifier. """
        result = self.network_manager.request_blocking(url, method='GET')
        if result.status == 200 and isinstance(result.data, expected_type):
            self.logger.debug("%s : récupéré", description)
            return result.data
        self.logger.warning("Échec de récupération : %s (statut=%s)", description, result.status)
        return None

    # --- jeton applicatif ---------------------------------------------------

    def fetch_token(self):
        """ Récupère un jeton applicatif (bloquant, à appeler depuis un thread de
        fond). Le gestionnaire réseau l'installe sur sa session : toutes les
        requêtes l'enverront ensuite automatiquement. Lève ``RuntimeError`` si
        l'authentification échoue, pour que l'appelant le sache clairement. """
        token = self.network_manager.fetch_token_blocking()
        if not token:
            raise RuntimeError("Échec de l'obtention du token")
        return token

    def clear_token(self):
        """Invalide le jeton courant (changement de serveur, déconnexion)."""
        self.network_manager.clear_token()

    def stop(self, timeout_ms=3000):
        """Arrête le worker réseau (fermeture de l'application)."""
        self.network_manager.stop(timeout_ms=timeout_ms)

    # --- lectures d'état ----------------------------------------------------

    def fetch_state(self):
        """ État autoritatif complet du comptoir en UNE requête (patient courant +
        file + réglages + révision) : évite d'agréger plusieurs instantanés
        susceptibles de se contredire. """
        return self._read_blocking(
            endpoints.counter_state(self._url(), self._counter_id()),
            dict, "état du comptoir")

    def fetch_current_patient(self):
        return self._read_blocking(
            endpoints.is_patient_on_counter(self._url(), self._counter_id()),
            dict, "patient courant")

    def fetch_patients_list(self):
        return self._read_blocking(
            endpoints.patients_list(self._url()), list, "liste des patients") or []

    def fetch_staff(self, on_result=None):
        """Agent présent sur le comptoir (asynchrone, au démarrage)."""
        return self._get(endpoints.is_staff_on_counter(self._url(), self._counter_id()),
                         on_result=on_result, key="setup_user")

    # --- actions sur le patient courant ------------------------------------

    def validate_and_call_next(self, on_result=None, busy_button=None):
        """Valide le patient courant et appelle le suivant (action idempotente :
        un rejeu ne doit pas faire avancer la file deux fois)."""
        return self._post(endpoints.validate_and_call_next(self._url(), self._counter_id()),
                          on_result=on_result, key="validate_and_call_next",
                          busy_button=busy_button, idempotent=True)

    def validate_current_patient(self, patient_id, on_result=None, busy_button=None):
        return self._post(endpoints.validate_patient(self._url(), self._counter_id(), patient_id),
                          on_result=on_result, key="validate", busy_button=busy_button)

    def pause_current_patient(self, patient_id, on_result=None, busy_button=None):
        return self._post(endpoints.pause_patient(self._url(), self._counter_id(), patient_id),
                          on_result=on_result, key="pause", busy_button=busy_button)

    def relaunch_call(self, on_result=None):
        """Relance l'appel du patient courant (« rappel »)."""
        return self._post(endpoints.relaunch_patient_call(self._url(), self._counter_id()),
                          on_result=on_result, key="recall")

    # --- actions sur un patient de la file ---------------------------------

    def call_specific_patient(self, patient_id, on_result=None):
        return self._post(
            endpoints.call_specific_patient(self._url(), self._counter_id(), patient_id),
            on_result=on_result, key=f"call_specific:{patient_id}")

    def validate_queued_patient(self, patient_id, on_result=None, busy_button=None):
        """Valide un patient désigné (menu contextuel de la file), qui n'est pas
        forcément celui du comptoir."""
        return self._post(endpoints.api_validate_patient(self._url(), patient_id),
                          on_result=on_result, key="validate", busy_button=busy_button)

    def put_standing(self, patient_id, activity_id=None, on_result=None):
        """Remet un patient en attente, éventuellement vers une autre activité."""
        return self._post(endpoints.put_standing_list(self._url(), patient_id, activity_id),
                          on_result=on_result, key=f"put_standing:{patient_id}")

    def delete_patient(self, patient_id, on_result=None):
        return self._post(endpoints.delete_patient(self._url(), patient_id),
                          on_result=on_result, key=f"delete:{patient_id}")

    # --- présence de l'agent sur le comptoir --------------------------------

    def login_staff(self, initials, disconnect_others, on_result=None):
        data = {'initials': initials, 'counter_id': self._counter_id(),
                "deconnect": disconnect_others, "app": True}
        return self._post(endpoints.update_staff(self._url()), data=data,
                          on_result=on_result, key="login")

    def logout_staff(self, on_result=None):
        """ Déconnexion de l'agent. ``on_result`` est absent sur le chemin
        automatique (l'agent était déjà absent côté serveur) : l'interface a déjà
        été mise à jour d'après l'état serveur. """
        data = {'counter_id': self._counter_id()}
        return self._post(endpoints.remove_staff(self._url()), data=data,
                          on_result=on_result, key="disconnect")

    def release_counter_blocking(self, url=None, counter_id=None):
        """ Libère le comptoir et attend (au plus quelques secondes).

        ``url``/``counter_id`` permettent de libérer un ANCIEN comptoir (autre
        serveur ou autre numéro) lors d'un changement de connexion ; sinon on
        utilise la configuration courante (fermeture de l'application). À appeler
        AVANT d'invalider le jeton : celui-ci vaut pour l'ancien serveur.

        Bornée par construction : si le serveur ne répond pas, on continue.
        Retourne True si le serveur a confirmé.
        """
        base_url = url if url is not None else self._url()
        target = counter_id if counter_id is not None else self._counter_id()
        try:
            result = self.network_manager.request_blocking(
                endpoints.remove_staff(base_url), method='POST',
                data={'counter_id': target}, timeout=(2, 3), timeout_s=4)
        except Exception as exc:
            self.logger.warning("Libération du comptoir échouée : %s", exc)
            return False
        if result.status == 200:
            self.logger.info("Comptoir libéré côté serveur")
            return True
        self.logger.warning("Libération du comptoir : statut %s", result.status)
        return False
