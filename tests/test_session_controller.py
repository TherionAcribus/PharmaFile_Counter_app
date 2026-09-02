"""Séquences de fond : démarrage et resynchronisation (point 10.13).

On vérifie les garanties qui protègent réellement l'application : aucune requête
dans le thread graphique, un seul ResyncWorker à la fois (coalescing), une seule
relance après une rafale, référence forte au thread tant qu'il tourne, et attente
BORNÉE à la fermeture.
"""

import logging
import os
import sys
import types

import pytest
from PySide6.QtCore import QCoreApplication, QThread

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from session_controller import ResyncWorker, SessionController, StartupWorker  # noqa: E402


class FakeTasks:
    def __init__(self):
        self.added = []
        self.removed = []

    def add(self, task, key=None):
        self.added.append(task)

    def remove(self, task, key=None):
        self.removed.append(task)

    def snapshot(self):
        return list(self.added)


class FakeWindow:
    """Fenêtre minimale vue par les workers : jeton + snapshot d'état."""

    def __init__(self, state=None, token_fails=False):
        self.shutting_down = False
        self.state = state if state is not None else {"revision": 3}
        self.token_fails = token_fails
        self.token_calls = 0
        self.state_calls = 0

    def get_app_token(self):
        self.token_calls += 1
        if self.token_fails:
            raise RuntimeError("serveur injoignable")

    def init_state(self):
        self.state_calls += 1
        return self.state


@pytest.fixture
def controller():
    window = FakeWindow()
    tasks = FakeTasks()
    return SessionController(window, tasks, logger=logging.getLogger("test.session"))


# --- workers ----------------------------------------------------------------

def test_startup_worker_recupere_jeton_puis_etat():
    window = FakeWindow()
    worker = StartupWorker(window)
    recus = []
    worker.finished_startup.connect(lambda c, s: recus.append((c, s)))
    worker.run()          # exécution directe : c'est le corps du thread
    assert recus == [(True, {"revision": 3})]
    assert (window.token_calls, window.state_calls) == (1, 1)


def test_startup_worker_sans_jeton_ne_demande_pas_l_etat():
    """Serveur injoignable : inutile d'enchaîner une requête vouée à l'échec."""
    window = FakeWindow(token_fails=True)
    worker = StartupWorker(window)
    recus = []
    worker.finished_startup.connect(lambda c, s: recus.append((c, s)))
    worker.run()
    assert recus == [(False, None)]
    assert window.state_calls == 0


def test_resync_worker_recupere_l_etat():
    window = FakeWindow(state={"revision": 9})
    worker = ResyncWorker(window)
    recus = []
    worker.finished_resync.connect(recus.append)
    worker.run()
    assert recus == [{"revision": 9}]
    # La resync ne redemande PAS de jeton (la session est déjà ouverte).
    assert window.token_calls == 0


# --- coalescing des resyncs -------------------------------------------------

def test_une_seule_resync_active_a_la_fois(controller, monkeypatch):
    lances = []
    monkeypatch.setattr("session_controller.ResyncWorker",
                        lambda window: _worker_factice(lances))
    assert controller.request_resync(lambda state: None) is not None
    assert controller.request_resync(lambda state: None) is None   # mémorisée
    assert len(lances) == 1


def test_relance_unique_apres_une_rafale(controller, monkeypatch):
    lances = []
    monkeypatch.setattr("session_controller.ResyncWorker",
                        lambda window: _worker_factice(lances))
    controller.request_resync(lambda state: None)
    controller.request_resync(lambda state: None)   # 1re demande pendant la passe
    controller.request_resync(lambda state: None)   # 2e demande pendant la passe
    # Une SEULE relance, pas une par demande.
    assert controller.finish_resync() is True
    assert controller.finish_resync() is False


def test_aucune_resync_pendant_l_arret(controller, monkeypatch):
    lances = []
    monkeypatch.setattr("session_controller.ResyncWorker",
                        lambda window: _worker_factice(lances))
    controller.window.shutting_down = True
    assert controller.request_resync(lambda state: None) is None
    assert lances == []


def _worker_factice(lances):
    worker = types.SimpleNamespace(
        finished_resync=types.SimpleNamespace(connect=lambda slot: None),
        finished=types.SimpleNamespace(connect=lambda slot: None),
    )
    worker.start = lambda: lances.append(worker)
    return worker


# --- suivi des threads ------------------------------------------------------

def test_track_garde_une_reference_jusqu_a_la_fin(controller):
    """Sans référence forte, le QThread peut être détruit en pleine exécution."""
    slots = []
    worker = types.SimpleNamespace(
        finished=types.SimpleNamespace(connect=slots.append))
    controller.track(worker)
    assert controller._tasks.added == [worker]
    slots[0]()   # le thread signale sa fin
    assert controller._tasks.removed == [worker]


def test_attente_des_workers_bornee():
    """Un worker qui ne rend pas la main ne doit pas bloquer la fermeture."""
    QCoreApplication.instance() or QCoreApplication([])

    class WorkerBloque(QThread):
        def __init__(self):
            super().__init__()
            self.waits = []

        def isRunning(self):
            return True

        def wait(self, ms):
            self.waits.append(ms)
            return False        # ne se termine jamais

    bloque = WorkerBloque()
    tasks = FakeTasks()
    tasks.added.append(bloque)
    controller = SessionController(FakeWindow(), tasks,
                                   logger=logging.getLogger("test.session"))
    controller.wait_active_workers(total_timeout_ms=50)
    # Une attente, bornée par le budget global.
    assert bloque.waits and bloque.waits[0] <= 50


# --- délégation depuis MainWindow ------------------------------------------

def test_main_window_delegue_les_sequences():
    for nom in ("_track_worker", "_wait_active_workers"):
        assert not hasattr(main.MainWindow, nom), nom
    source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "main.py"), encoding="utf-8").read()
    # Les classes de threads ne vivent plus dans main.py.
    assert "class StartupWorker" not in source
    assert "class ResyncWorker" not in source
    assert "self.session.start_startup(" in source
    assert "self.session.request_resync(" in source
