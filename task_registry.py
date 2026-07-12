"""Registre des tâches réseau actives (sans dépendance PySide, testable seul).

Rôle : conserver une référence forte à chaque tâche (RequestHandle ou QThread)
tant qu'elle n'est pas terminée — pour ne plus écraser un attribut partagé
(``self.thread``) et perdre le suivi / voir la tâche détruite prématurément — et
interdire une seconde action identique (même ``key``) tant que la première est en
cours.
"""


class TaskRegistry:
    def __init__(self):
        self._tasks = set()   # références fortes aux tâches actives
        self._keys = set()    # clés d'actions en cours (déduplication)

    def is_active(self, key):
        """True si une action portant cette clé est déjà en cours."""
        return key is not None and key in self._keys

    def add(self, task, key=None):
        """Enregistre une tâche. Retourne False (sans rien enregistrer) si
        ``key`` est déjà active — doublon refusé ; True sinon."""
        if self.is_active(key):
            return False
        self._tasks.add(task)
        if key is not None:
            self._keys.add(key)
        return True

    def remove(self, task, key=None):
        """Retire une tâche terminée (idempotent)."""
        self._tasks.discard(task)
        if key is not None:
            self._keys.discard(key)

    def active_keys(self):
        return set(self._keys)

    def __len__(self):
        return len(self._tasks)

    def __contains__(self, task):
        return task in self._tasks
