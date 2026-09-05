"""Politique des sons rapprochés — logique pure, testable sans Qt (point 3).

Historique du problème corrigé ici : tous les sons partageaient un unique
``QMediaPlayer`` et chaque demande remplaçait sa source. Deux conséquences :

  - un simple « ding » (nouveau patient) coupait net une alerte parlée en cours
    (« patient déjà pris », « pensez à valider ») ;
  - rien ne bornait les demandes rapprochées : une rafale de patients faisait
    autant de « ding » qui se hachaient les uns les autres.

Ce module décide, pour chaque demande, s'il faut jouer tout de suite, couper le
son en cours, mettre en attente ou abandonner. Il ne connaît ni Qt ni les
fichiers : ``audio.AudioPlayer`` exécute la décision.

Politique retenue (volontairement simple) :

  1. **Priorité aux alertes parlées.** Une alerte parlée coupe un « ding » en
     cours ; l'inverse n'arrive jamais.
  2. **Les « ding » ne s'accumulent pas.** Un « ding » demandé pendant qu'un son
     joue est abandonné (et non mis en attente) : arrivé trop tard il n'informe
     plus de rien. Deux « ding » très rapprochés sont regroupés en un seul.
  3. **File d'attente bornée et périssable.** Seules les alertes parlées
     attendent leur tour, au plus ``MAX_PENDING``, et une alerte trop vieille
     (``MAX_PENDING_AGE``) est jetée plutôt que jouée avec du retard.

Un son inconnu du barème est traité comme une alerte parlée : mieux vaut le
laisser attendre son tour que le sacrifier.
"""

import time
from collections import namedtuple

#: Niveaux de priorité (plus grand = plus important).
BEEP = 1
VOICE = 2

DEFAULT_PRIORITY = VOICE

#: Priorité de chaque son embarqué (cf. ``audio.SOUNDS``).
PRIORITIES = {
    "ding": BEEP,
    "patient_taken": VOICE,
    "please_validate": VOICE,
}

#: Nombre maximum d'alertes en attente (les « ding » n'attendent jamais).
MAX_PENDING = 2

#: Âge au-delà duquel une alerte en attente est jetée (secondes).
MAX_PENDING_AGE = 8.0

#: Délai minimum entre deux lectures d'un même son (secondes) : regroupe les
#: demandes rapprochées même quand la précédente est déjà terminée.
MIN_REPEAT_INTERVAL = {
    "ding": 0.5,
}

# --- Décisions --------------------------------------------------------------

PLAY = "play"          # rien en cours : jouer maintenant
PREEMPT = "preempt"    # couper le son en cours et jouer à sa place
QUEUE = "queue"        # mettre en attente
DROP = "drop"          # abandonner

#: ``action`` : une des constantes ci-dessus. ``sound`` : le son concerné.
#: ``reason`` : motif lisible, journalisé par l'appelant. ``replaced`` : le son
#: coupé (uniquement pour PREEMPT).
Decision = namedtuple("Decision", "action sound reason replaced")

#: Résultat de ``finished()`` : le son à enchaîner (ou ``None``) et les alertes
#: jetées parce que trop vieilles.
Next = namedtuple("Next", "sound expired")


def priority(name, priorities=None):
    """Priorité d'un son ; un son inconnu vaut une alerte parlée."""
    table = PRIORITIES if priorities is None else priorities
    return table.get(name, DEFAULT_PRIORITY)


class SoundScheduler:
    """Décide du sort de chaque demande de son. Ne joue rien lui-même.

    L'appelant tient le contrat suivant : il appelle ``request()`` pour chaque
    demande, démarre effectivement le son quand la décision est PLAY ou PREEMPT,
    et appelle ``finished()`` dès que la lecture s'achève (fin du média, erreur,
    ou garde-fou) pour enchaîner la file.
    """

    def __init__(self, priorities=None, max_pending=MAX_PENDING,
                 max_age=MAX_PENDING_AGE, min_repeat=None, clock=time.monotonic):
        self.priorities = dict(PRIORITIES if priorities is None else priorities)
        self.max_pending = max_pending
        self.max_age = max_age
        self.min_repeat = dict(MIN_REPEAT_INTERVAL if min_repeat is None else min_repeat)
        self.clock = clock
        self.current = None
        self._pending = []      # [(nom, instant de la demande)]
        self._last_started = {}  # nom -> instant du dernier démarrage

    # -- consultation --------------------------------------------------------

    @property
    def pending(self):
        """Noms des sons en attente, dans l'ordre."""
        return tuple(name for name, _requested_at in self._pending)

    def priority_of(self, name):
        return priority(name, self.priorities)

    # -- décisions -----------------------------------------------------------

    def request(self, name, force=False):
        """Décide du sort d'une demande de son.

        ``force`` court-circuite toute la politique : le son passe devant tout
        (bouton « Tester un son » des préférences, où chaque clic doit
        s'entendre, même rapproché).
        """
        now = self.clock()
        if force:
            replaced = self.current
            self._pending.clear()
            self._start(name, now)
            return Decision(PREEMPT if replaced else PLAY, name,
                            "demande explicite", replaced)

        wanted = self.priority_of(name)

        if self.current is None:
            too_soon = self._too_soon(name, now)
            if too_soon is not None:
                return Decision(DROP, name, too_soon, None)
            self._start(name, now)
            return Decision(PLAY, name, "aucun son en cours", None)

        if wanted > self.priority_of(self.current):
            replaced = self.current
            self._start(name, now)
            return Decision(PREEMPT, name, "alerte prioritaire", replaced)

        if wanted <= BEEP:
            # Un « ding » n'attend pas : joué plus tard il n'annoncerait plus
            # rien, et il ne doit surtout pas couper une alerte parlée.
            return Decision(DROP, name, "son secondaire pendant une lecture", None)

        if name == self.current or name in self.pending:
            return Decision(DROP, name, "déjà en cours ou en attente", None)

        if len(self._pending) >= self.max_pending:
            return Decision(DROP, name, "file d'attente pleine", None)

        self._pending.append((name, now))
        return Decision(QUEUE, name, "alerte mise en attente", None)

    def finished(self):
        """Signale la fin de la lecture en cours et renvoie la suite.

        Les alertes attendues depuis trop longtemps sont jetées : mieux vaut
        aucun son qu'un son qui commente une situation périmée.
        """
        now = self.clock()
        self.current = None
        expired = []
        while self._pending:
            name, requested_at = self._pending.pop(0)
            if now - requested_at > self.max_age:
                expired.append(name)
                continue
            self._start(name, now)
            return Next(name, tuple(expired))
        return Next(None, tuple(expired))

    def reset(self):
        """Oublie tout (arrêt du lecteur, changement de périphérique…)."""
        self.current = None
        self._pending.clear()

    # -- interne -------------------------------------------------------------

    def _start(self, name, now):
        self.current = name
        self._last_started[name] = now

    def _too_soon(self, name, now):
        """Motif d'abandon si le son a déjà été joué il y a un instant."""
        interval = self.min_repeat.get(name)
        if not interval:
            return None
        last = self._last_started.get(name)
        if last is not None and now - last < interval:
            return "regroupé avec la demande précédente"
        return None
