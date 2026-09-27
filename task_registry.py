"""Registre des tâches réseau actives (sans dépendance PySide, testable seul).

Rôle : conserver une référence forte à chaque tâche (RequestHandle ou QThread)
tant qu'elle n'est pas terminée — pour ne plus écraser un attribut partagé
(``self.thread``) et perdre le suivi / voir la tâche détruite prématurément — et
interdire une seconde action identique (même ``key``) tant que la première est en
cours — et, via ``group``, d'interdire aussi les actions *incompatibles* (une
seule action du groupe à la fois : « Pause » pendant « Suivant » serait construite
sur un état déjà dépassé).
"""


class TaskRegistry:
    def __init__(self):
        self._tasks = set()   # références fortes aux tâches actives
        self._keys = set()    # clés d'actions en cours (déduplication)
        self._groups = {}     # tâche -> groupe d'exclusion mutuelle

    def is_active(self, key):
        """True si une action portant cette clé est déjà en cours."""
        return key is not None and key in self._keys

    def is_group_active(self, group):
        """True si une tâche du groupe d'exclusion est en cours. Permet de
        refuser des actions INCOMPATIBLES (pas seulement identiques) : par
        exemple « Pause » pendant que « Suivant » est encore en vol."""
        return group is not None and group in self._groups.values()

    def add(self, task, key=None, group=None):
        """Enregistre une tâche. Retourne False (sans rien enregistrer) si
        ``key`` est déjà active — doublon refusé — ou si ``group`` est déjà
        occupé par une autre tâche — action incompatible refusée ; True sinon."""
        if self.is_active(key) or self.is_group_active(group):
            return False
        self._tasks.add(task)
        if key is not None:
            self._keys.add(key)
        if group is not None:
            self._groups[task] = group
        return True

    def remove(self, task, key=None):
        """Retire une tâche terminée (idempotent)."""
        self._tasks.discard(task)
        if key is not None:
            self._keys.discard(key)
        self._groups.pop(task, None)

    def active_keys(self):
        return set(self._keys)

    def active_groups(self):
        return set(self._groups.values())

    def snapshot(self):
        """Copie des tâches actives (pour itérer sans risque, ex: attente à
        l'arrêt de l'application)."""
        return list(self._tasks)

    def __len__(self):
        return len(self._tasks)

    def __contains__(self, task):
        return task in self._tasks
