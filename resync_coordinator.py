"""Coordination des resynchronisations d'état (sans dépendance PySide, testable).

Deux mécanismes séparés du reste (widgets, réseau) pour être vérifiables seuls :

- ``ResyncCoordinator`` : coalescing. Une seule resync « active » à la fois ; les
  demandes reçues pendant une resync sont fusionnées en UNE seule relance. Ainsi,
  une rafale d'évènements/reconnexions ne produit pas une rafale de threads.
- ``snapshot_is_fresh`` : garde de révision. Un snapshot dont la révision est plus
  ancienne que l'état déjà connu ne doit jamais l'écraser.
"""


class ResyncCoordinator:
    def __init__(self):
        self._in_progress = False
        self._pending = False

    @property
    def in_progress(self):
        return self._in_progress

    def request(self):
        """Demande une resync. Retourne True s'il faut la DÉMARRER maintenant,
        False si une resync est déjà en cours (la demande est mémorisée pour une
        relance unique ultérieure)."""
        if self._in_progress:
            self._pending = True
            return False
        self._in_progress = True
        self._pending = False
        return True

    def finish(self):
        """À appeler quand la resync en cours se termine. Retourne True s'il faut
        en relancer une (au moins une demande a été reçue entretemps)."""
        self._in_progress = False
        if self._pending:
            self._pending = False
            return True
        return False


def snapshot_is_fresh(snapshot_revision, known_revision):
    """True si le snapshot est au moins aussi récent que l'état connu.

    Renvoie True quand il n'y a pas de référence fiable (révision inconnue, ou
    aucun état encore chargé -> known_revision None ou < 0). Sinon, le snapshot
    n'est appliqué que si sa révision est >= à celle déjà connue."""
    if snapshot_revision is None or known_revision is None or known_revision < 0:
        return True
    return snapshot_revision >= known_revision
