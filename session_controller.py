"""Séquences de fond : démarrage et resynchronisation (point 10.13).

Deux séquences réseau ne doivent JAMAIS s'exécuter dans le thread graphique —
sinon l'application se fige tant que le serveur n'a pas répondu :

* le DÉMARRAGE (jeton applicatif puis état initial du comptoir) ;
* la RESYNCHRONISATION après une reconnexion WebSocket — Socket.IO ne rejoue pas
  les évènements manqués pendant une coupure, un comptoir déconnecté quelques
  minutes resterait donc figé sur son dernier état connu jusqu'au prochain
  évènement poussé, qui peut ne jamais arriver.

Ce module possède les deux QThread, leur cycle de vie (référence forte jusqu'à
la fin, attente bornée à la fermeture) et le COALESCING des resyncs : une rafale
d'évènements ou de reconnexions ne doit pas créer une rafale de requêtes.

La décision de QUOI faire du résultat reste dans la fenêtre (``_on_startup_ready``
/ ``_on_resync_ready``) : appliquer un état, c'est toucher aux widgets.
"""

import logging
import time

from PySide6.QtCore import QThread, Signal

from resync_coordinator import ResyncCoordinator

logger = logging.getLogger("appcomptoir.session")


class StartupWorker(QThread):
    """ Exécute en arrière-plan la séquence réseau de démarrage (token + état
    initial) pour ne pas geler le thread GUI pendant que le serveur répond. """
    finished_startup = Signal(bool, object)  # connected, state (dict ou None)

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

    def run(self):
        mw = self.main_window
        connected = False
        state = None

        try:
            mw.get_app_token()
            # si on a un token, on se considère comme connecté
            connected = True
        except Exception as e:
            logger.error("Erreur lors de l'obtention du token : %s", e)
            connected = False

        if connected:
            # Une seule snapshot atomique (patient en cours + liste + réglages +
            # révision) au lieu de deux requêtes séparées qui pouvaient se
            # chevaucher avant l'ouverture de Socket.IO (course de démarrage).
            state = mw.init_state()

        self.finished_startup.emit(connected, state)


class ResyncWorker(QThread):
    """ Récupère en arrière-plan l'état courant (patient en cours + liste des
    patients) après une reconnexion WebSocket. """
    finished_resync = Signal(object)  # state (dict ou None)

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

    def run(self):
        mw = self.main_window
        # Même snapshot atomique qu'au démarrage : on récupère l'état autoritatif
        # complet (dont la révision) en une requête.
        state = mw.init_state()
        self.finished_resync.emit(state)


class SessionController:
    """Cycle de vie des séquences de fond de la fenêtre principale.

    ``tasks`` est le registre des tâches actives (partagé avec la couche réseau) :
    il conserve une référence forte à chaque worker jusqu'à sa fin, sinon un
    QThread peut être détruit en pleine exécution (« QThread: Destroyed while
    thread is still running »).
    """

    def __init__(self, window, tasks, logger=None):
        self.window = window
        self._tasks = tasks
        self.logger = logger or logging.getLogger("appcomptoir.session")
        # Coalescing des resynchronisations : une seule passe réseau active à la
        # fois, les demandes reçues pendant une passe sont fusionnées en une.
        self.resync = ResyncCoordinator()

    # --- suivi des threads --------------------------------------------------

    def track(self, worker):
        """ Garde une référence à un QThread jusqu'à sa fin, pour ne pas le
        détruire prématurément s'il est encore en cours. """
        self._tasks.add(worker)
        worker.finished.connect(lambda: self._tasks.remove(worker))
        return worker

    def wait_active_workers(self, total_timeout_ms=2000):
        """ Attend (borné) la fin des QThread encore actifs avant destruction, en
        partageant un budget de temps global. """
        deadline = time.monotonic() + total_timeout_ms / 1000.0
        for task in self._tasks.snapshot():
            if isinstance(task, QThread) and task.isRunning():
                remaining = int(max(0.0, deadline - time.monotonic()) * 1000)
                if not task.wait(remaining or 1):
                    self.logger.warning("Un worker n'a pas terminé dans le délai d'arrêt")

    # --- démarrage ----------------------------------------------------------

    def start_startup(self, on_ready):
        """ (Re)lance la séquence réseau de démarrage en arrière-plan. Rappelée
        après (re)configuration d'un comptoir valide. """
        worker = StartupWorker(self.window)
        worker.finished_startup.connect(on_ready)
        self.track(worker)
        worker.start()
        return worker

    # --- resynchronisation --------------------------------------------------

    def request_resync(self, on_ready):
        """ Déclenche une resynchronisation de l'état autoritatif en garantissant
        qu'UNE SEULE resync réseau est active à la fois.

        Si une resync est déjà en cours, on mémorise seulement qu'une nouvelle
        passe est demandée : une rafale d'évènements ou de reconnexions ne crée
        donc pas une rafale de ResyncWorker. La passe en attente est relancée une
        seule fois à la fin (cf. ``finish_resync``). Retourne le worker créé, ou
        None si la demande a été mémorisée / l'application s'arrête. """
        if self.window.shutting_down:
            return None
        if not self.resync.request():
            return None  # une resync est déjà active : demande mémorisée
        worker = ResyncWorker(self.window)
        worker.finished_resync.connect(on_ready)
        self.track(worker)
        worker.start()
        return worker

    def finish_resync(self):
        """ Libère le verrou de resync. Retourne True si une passe supplémentaire
        a été demandée pendant celle qui vient de finir (l'appelant la relance
        une seule fois, pour converger vers l'état le plus récent). """
        return self.resync.finish()
