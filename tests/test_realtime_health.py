"""Mesure de la livraison temps réel par le comptoir (trous, coupures, envoi)."""

import logging
import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import endpoints  # noqa: E402
import main  # noqa: E402
from realtime_health import RealtimeHealth  # noqa: E402


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# --- Compteurs purs ------------------------------------------------------------

def test_gap_while_connected_counts_missed_events():
    health = RealtimeHealth()
    health.record_connected(was_disconnected=False)
    health.record_event()
    health.record_gap(3)
    counts = health.counts()
    assert (counts["events"], counts["gaps"], counts["missed_events"]) == (1, 1, 3)
    assert counts["reconnect_gaps"] == 0


def test_gap_after_reconnection_is_explained_until_resync_applied():
    health = RealtimeHealth()
    health.record_connected(was_disconnected=False)
    health.record_disconnected()
    health.record_connected(was_disconnected=True)
    health.record_gap(5)  # évènements émis pendant la coupure
    assert (health.counts()["reconnect_gaps"], health.counts()["gaps"]) == (1, 0)
    health.record_resync_applied()
    health.record_gap(1)  # état rattrapé : un nouveau trou est une vraie perte
    assert (health.counts()["gaps"], health.counts()["missed_events"]) == (1, 1)


def test_first_connection_does_not_wait_for_a_resync():
    health = RealtimeHealth()
    health.record_connected(was_disconnected=False)
    health.record_gap(1)
    assert health.counts()["gaps"] == 1


def test_connected_time_and_disconnects():
    clock = _Clock()
    health = RealtimeHealth(clock=clock)
    health.record_connected(was_disconnected=False)
    clock.now += 120
    health.record_disconnected()
    health.record_disconnected()  # déjà déconnecté : ne compte pas deux fois
    clock.now += 60
    health.record_connected(was_disconnected=True)
    clock.now += 30
    report = health.take_report()
    assert report["connected_seconds"] == 150
    assert report["disconnects"] == 1
    assert report["window_seconds"] == 210


def test_take_report_resets_and_skips_empty_windows():
    clock = _Clock()
    health = RealtimeHealth(clock=clock)
    assert health.take_report() is None  # jamais connecté, rien observé
    health.record_stale()
    report = health.take_report()
    assert report["stale_events"] == 1
    assert health.take_report() is None


def test_restore_merges_a_failed_report_into_the_next_one():
    clock = _Clock()
    health = RealtimeHealth(clock=clock)
    health.record_event()
    clock.now += 300
    failed = health.take_report()
    health.restore(failed)
    health.record_event()
    clock.now += 300
    report = health.take_report()
    assert report["events"] == 2
    assert report["window_seconds"] == 600
    health.restore(None)  # sans effet


def test_endpoint_is_scoped_to_the_counter():
    assert endpoints.counter_realtime_health("https://srv/", "3") == \
        "https://srv/api/counter/3/realtime_health"
    assert "/api/counter/..%2F/" in endpoints.counter_realtime_health("https://srv", "../")


# --- Câblage dans MainWindow ------------------------------------------------------

def _wnp(queue_revision):
    w = types.SimpleNamespace(
        logger=logging.getLogger("test.realtime_health"),
        queue_revision=queue_revision, list_patients=[], shutting_down=False,
        realtime_health=RealtimeHealth())
    w.refresh_patient_lists = lambda: None
    w._request_resync = lambda: None
    w.new_patient = types.MethodType(main.MainWindow.new_patient, w)
    return w


def test_new_patient_feeds_the_health_counters():
    w = _wnp(queue_revision=10)
    w.realtime_health.record_connected(was_disconnected=False)
    w.new_patient([], revision=11)   # attendu
    w.new_patient([], revision=11)   # doublon
    w.new_patient([], revision=15)   # trou : 12, 13, 14 manqués
    counts = w.realtime_health.counts()
    assert counts["events"] == 1
    assert counts["stale_events"] == 1
    assert (counts["gaps"], counts["missed_events"]) == (1, 3)


class _Result:
    def __init__(self, status):
        self.status = status


def _reporter(handle=object()):
    sent = []
    w = types.SimpleNamespace(
        logger=logging.getLogger("test.realtime_health.report"),
        shutting_down=False, counter_id=4, _realtime_health_supported=True,
        realtime_health=RealtimeHealth())
    w.api = types.SimpleNamespace(
        report_realtime_health=lambda report, on_result=None: (
            sent.append((report, on_result)) or handle))
    w._on_realtime_health_reported = types.MethodType(
        main.MainWindow._on_realtime_health_reported, w)
    w._report_realtime_health = types.MethodType(
        main.MainWindow._report_realtime_health, w)
    return w, sent


def test_report_is_sent_and_kept_on_network_failure():
    w, sent = _reporter()
    w.realtime_health.record_event()
    w._report_realtime_health()
    report, on_result = sent[0]
    assert report["events"] == 1
    on_result(_Result(0))  # serveur injoignable : rien n'est perdu
    assert w.realtime_health.counts()["events"] == 1
    w._report_realtime_health()
    sent[1][1](_Result(200))
    assert w.realtime_health.counts()["events"] == 0


def test_unsupported_server_disables_reporting():
    w, sent = _reporter()
    w.realtime_health.record_event()
    w._report_realtime_health()
    sent[0][1](_Result(404))
    assert w._realtime_health_supported is False
    w.realtime_health.record_event()
    w._report_realtime_health()
    assert len(sent) == 1


def test_nothing_sent_when_idle_or_refused_locally():
    w, sent = _reporter(handle=None)
    w._report_realtime_health()
    assert sent == []  # rien observé
    w.realtime_health.record_event()
    w._report_realtime_health()  # refusé localement (envoi déjà en vol)
    assert w.realtime_health.counts()["events"] == 1
    w.counter_id = None
    w._report_realtime_health()
    assert len(sent) == 1
