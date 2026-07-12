"""Tests du coordinateur de resynchronisation (point 13).

Vérifie le coalescing (une seule resync active à la fois, demandes fusionnées) et
la garde de révision (un snapshot ancien n'écrase jamais un état plus récent).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from resync_coordinator import ResyncCoordinator, snapshot_is_fresh  # noqa: E402


# --- Coalescing -------------------------------------------------------------

def test_first_request_starts():
    c = ResyncCoordinator()
    assert c.request() is True
    assert c.in_progress is True


def test_second_request_while_active_does_not_start():
    c = ResyncCoordinator()
    assert c.request() is True     # démarre
    assert c.request() is False    # déjà en cours -> mémorisé
    assert c.request() is False    # rafale : toujours mémorisé, pas de nouveau départ


def test_finish_relaunches_once_if_pending():
    c = ResyncCoordinator()
    c.request()                    # active
    c.request()                    # une passe demandée entretemps
    c.request()                    # plusieurs demandes -> une seule relance
    assert c.finish() is True      # relancer une fois
    assert c.in_progress is False


def test_finish_without_pending_does_not_relaunch():
    c = ResyncCoordinator()
    c.request()
    assert c.finish() is False
    assert c.in_progress is False


def test_burst_of_events_creates_at_most_one_relaunch():
    # 1 départ + rafale de 10 demandes -> à la fin, UNE seule relance.
    c = ResyncCoordinator()
    assert c.request() is True                          # départ (thread 1)
    starts = sum(1 for _ in range(10) if c.request())   # aucune ne redémarre
    assert starts == 0
    assert c.finish() is True                           # -> le caller relance
    assert c.request() is True                          # la relance démarre (thread 2, unique)
    assert c.finish() is False                          # plus rien en attente


def test_cycle_returns_to_idle():
    c = ResyncCoordinator()
    c.request()
    assert c.finish() is False
    # De nouveau au repos : une nouvelle demande redémarre normalement.
    assert c.request() is True


# --- Garde de révision ------------------------------------------------------

def test_fresh_when_no_known_reference():
    assert snapshot_is_fresh(5, None) is True
    assert snapshot_is_fresh(5, -1) is True
    assert snapshot_is_fresh(None, 10) is True


def test_snapshot_applied_when_newer_or_equal():
    assert snapshot_is_fresh(10, 10) is True
    assert snapshot_is_fresh(11, 10) is True


def test_stale_snapshot_rejected():
    assert snapshot_is_fresh(9, 10) is False
