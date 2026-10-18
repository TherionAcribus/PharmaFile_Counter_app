"""Mesure de la livraison temps réel vue par ce comptoir (sans PySide, testable).

Chaque diffusion de la file porte une révision (``main.new_patient``). Un saut
de plus d'un cran signifie qu'au moins un évènement a été manqué : l'App se
resynchronise déjà. Ce module se contente de COMPTER ce qu'elle observe, pour
que l'administration voie si des comptoirs perdent des évènements en service
(Serveur/counter_realtime_health.py) :

* ``gaps`` / ``missed_events`` : trous alors que la connexion était établie
  et l'état à jour — évènements réellement perdus en route ;
* ``reconnect_gaps`` : trous constatés après une reconnexion, AVANT que le
  rattrapage ne soit appliqué — attendus, la coupure les explique ;
* ``stale_events`` : évènements reçus en double ou dans le désordre ;
* ``disconnects`` et ``connected_seconds`` : coupures et temps connecté.

Les compteurs sont envoyés toutes les quelques minutes puis remis à zéro
(``take_report``). Si l'envoi échoue, ``restore`` les réintègre : rien n'est
perdu, le rapport suivant couvre simplement une fenêtre plus longue.
"""

import time

REPORT_INTERVAL_SECONDS = 300
COUNTER_FIELDS = ("events", "gaps", "missed_events", "reconnect_gaps",
                  "stale_events", "disconnects", "connected_seconds")


class RealtimeHealth:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._counts = dict.fromkeys(COUNTER_FIELDS, 0)
        self._window_start = clock()
        self._connected_since = None
        # Vrai entre une reconnexion et l'application du rattrapage : un trou
        # constaté pendant ce délai est expliqué par la coupure.
        self._awaiting_resync = False

    # --- évènements observés ----------------------------------------------

    def record_event(self):
        self._counts["events"] += 1

    def record_gap(self, missed):
        missed = max(1, int(missed))
        if self._awaiting_resync:
            self._counts["reconnect_gaps"] += 1
        else:
            self._counts["gaps"] += 1
            self._counts["missed_events"] += missed

    def record_stale(self):
        self._counts["stale_events"] += 1

    def record_connected(self, was_disconnected):
        """Connexion temps réel établie. ``was_disconnected`` : une coupure
        réelle a précédé (et non la première connexion)."""
        if self._connected_since is None:
            self._connected_since = self._clock()
        if was_disconnected:
            self._awaiting_resync = True

    def record_disconnected(self):
        if self._connected_since is not None:
            self._counts["connected_seconds"] += self._clock() - self._connected_since
            self._connected_since = None
            self._counts["disconnects"] += 1

    def record_resync_applied(self):
        self._awaiting_resync = False

    # --- envoi -------------------------------------------------------------

    def _flush_connected(self):
        if self._connected_since is not None:
            now = self._clock()
            self._counts["connected_seconds"] += now - self._connected_since
            self._connected_since = now

    def take_report(self, app_version=None):
        """Rapport de la fenêtre écoulée, compteurs remis à zéro. ``None`` si
        rien n'a été observé (App jamais connectée) : inutile de l'envoyer."""
        self._flush_connected()
        now = self._clock()
        report = {key: int(round(value)) for key, value in self._counts.items()}
        report["window_seconds"] = int(round(now - self._window_start))
        self._counts = dict.fromkeys(COUNTER_FIELDS, 0)
        self._window_start = now
        if not any(report[key] for key in COUNTER_FIELDS):
            return None
        if app_version:
            report["app_version"] = str(app_version)
        return report

    def restore(self, report):
        """Réintègre un rapport non transmis (échec réseau)."""
        if not report:
            return
        for key in COUNTER_FIELDS:
            self._counts[key] += int(report.get(key) or 0)
        self._window_start -= int(report.get("window_seconds") or 0)

    def counts(self):
        """Copie des compteurs courants (diagnostic, tests)."""
        return dict(self._counts)
