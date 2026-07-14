"""Gestion des workers de test des Préférences (point 9).

Couvre :
- le registre _start_worker : anti-doublon par « kind », conservation de la
  référence, remplacement d'un worker terminé, refus pendant la fermeture ;
- _shutdown_workers : interruption + attente bornée des workers actifs, puis vidage ;
- CountersWorker.run : réponses JSON invalides / jeton manquant / format
  inattendu émettent un échec explicite (le thread ne meurt pas silencieusement) ;
- update_counters : ignore les comptoirs sans les champs attendus ;
- boutons de test : désactivés pendant un test, réactivés à la fin / en cas d'échec.

Tests « faux self » : on appelle les vraies méthodes de PreferencesDialog sur un
objet léger, sans construire tout le dialogue ni ouvrir de réseau réel.
"""

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import preferences  # noqa: E402


# --- Faux worker (interface minimale utilisée par le registre) --------------

class FakeWorker:
    def __init__(self, running=False, wait_returns=True):
        self._running = running
        self._wait_returns = wait_returns
        self.started = 0
        self.interrupted = 0
        self.waited = None

    def isRunning(self):
        return self._running

    def start(self):
        self.started += 1
        self._running = True

    def requestInterruption(self):
        self.interrupted += 1

    def wait(self, ms):
        self.waited = ms
        if self._wait_returns:
            self._running = False
        return self._wait_returns


def _registry_dialog():
    w = types.SimpleNamespace(_workers={}, _closing=False)
    w._start_worker = types.MethodType(preferences.PreferencesDialog._start_worker, w)
    w._shutdown_workers = types.MethodType(preferences.PreferencesDialog._shutdown_workers, w)
    return w


# --- _start_worker ----------------------------------------------------------

def test_start_worker_keeps_reference_and_starts():
    w = _registry_dialog()
    worker = FakeWorker()
    assert w._start_worker("k", worker) is True
    assert worker.started == 1
    assert w._workers["k"] is worker  # référence forte conservée


def test_start_worker_refuses_duplicate_same_kind():
    w = _registry_dialog()
    first = FakeWorker()
    assert w._start_worker("k", first) is True  # first est maintenant running
    second = FakeWorker()
    assert w._start_worker("k", second) is False
    assert second.started == 0
    assert w._workers["k"] is first  # l'ancien n'est pas remplacé


def test_start_worker_replaces_finished_worker():
    w = _registry_dialog()
    first = FakeWorker()
    w._start_worker("k", first)
    first._running = False  # terminé
    second = FakeWorker()
    assert w._start_worker("k", second) is True
    assert w._workers["k"] is second


def test_start_worker_refused_while_closing():
    w = _registry_dialog()
    w._closing = True
    worker = FakeWorker()
    assert w._start_worker("k", worker) is False
    assert worker.started == 0


def test_start_worker_different_kinds_coexist():
    w = _registry_dialog()
    a = FakeWorker()
    b = FakeWorker()
    assert w._start_worker("a", a) is True
    assert w._start_worker("b", b) is True
    assert w._workers == {"a": a, "b": b}


# --- _shutdown_workers ------------------------------------------------------

def test_shutdown_interrupts_and_waits_running_only():
    w = _registry_dialog()
    running = FakeWorker(running=True)
    finished = FakeWorker(running=False)
    w._workers = {"a": running, "b": finished}
    w._shutdown_workers()
    assert w._closing is True
    assert running.interrupted == 1
    assert running.waited == preferences.WORKER_SHUTDOWN_TIMEOUT_MS
    assert finished.interrupted == 0  # pas d'attente inutile sur un worker fini
    assert w._workers == {}


def test_shutdown_bounded_even_if_worker_hangs():
    w = _registry_dialog()
    stuck = FakeWorker(running=True, wait_returns=False)  # ne se termine pas
    w._workers = {"a": stuck}
    w._shutdown_workers()  # ne doit pas boucler indéfiniment
    assert stuck.waited == preferences.WORKER_SHUTDOWN_TIMEOUT_MS
    assert w._workers == {}


# --- CountersWorker.run : réponses invalides --------------------------------

def _resp(status, payload=None, raises=False, bad_json=False):
    def _call(*a, **k):
        if raises:
            raise preferences.requests.exceptions.RequestException("boom")

        def _json():
            if bad_json:
                raise ValueError("pas du JSON")
            return payload if payload is not None else {}

        return types.SimpleNamespace(status_code=status, json=_json)
    return _call


def _run_counters(monkeypatch, post, get=None):
    monkeypatch.setattr(preferences.requests, "post", post)
    if get is not None:
        monkeypatch.setattr(preferences.requests, "get", get)
    worker = preferences.CountersWorker("http://serveur", "secret")
    captured = {}
    worker.result.connect(lambda ok, data: captured.update(ok=ok, data=data))
    worker.run()
    return captured


def test_counters_bad_secret(monkeypatch):
    got = _run_counters(monkeypatch, _resp(401))
    assert got["ok"] is False and "secret" in got["data"].lower()


def test_counters_missing_token(monkeypatch):
    got = _run_counters(monkeypatch, _resp(200, {}))
    assert got["ok"] is False and "jeton" in got["data"].lower()


def test_counters_token_bad_json(monkeypatch):
    got = _run_counters(monkeypatch, _resp(200, bad_json=True))
    assert got["ok"] is False and "jeton" in got["data"].lower()


def test_counters_list_bad_json(monkeypatch):
    got = _run_counters(monkeypatch, _resp(200, {"token": "abc"}),
                        get=_resp(200, bad_json=True))
    assert got["ok"] is False and "illisible" in got["data"].lower()


def test_counters_not_a_list(monkeypatch):
    got = _run_counters(monkeypatch, _resp(200, {"token": "abc"}),
                        get=_resp(200, {"oops": 1}))
    assert got["ok"] is False and "format" in got["data"].lower()


def test_counters_http_error(monkeypatch):
    got = _run_counters(monkeypatch, _resp(200, {"token": "abc"}),
                        get=_resp(500))
    assert got["ok"] is False and "500" in got["data"]


def test_counters_network_error(monkeypatch):
    got = _run_counters(monkeypatch, _resp(0, raises=True))
    assert got["ok"] is False and "Erreur" in got["data"]


def test_counters_success(monkeypatch):
    counters = [{"id": 1, "name": "Comptoir 1"}]
    got = _run_counters(monkeypatch, _resp(200, {"token": "abc"}),
                        get=_resp(200, counters))
    assert got["ok"] is True and got["data"] == counters


# --- update_counters : champs manquants -------------------------------------

def test_update_counters_skips_entries_missing_fields():
    items = []
    w = types.SimpleNamespace(counter_id=None)
    w.counter_combobox = types.SimpleNamespace(
        clear=lambda: items.clear(),
        addItem=lambda name, cid: items.append((name, cid)),
        findData=lambda d: -1,
        setCurrentIndex=lambda i: None,
    )
    w.update_counters = types.MethodType(preferences.PreferencesDialog.update_counters, w)
    w.update_counters([{"name": "A", "id": 1}, {"id": 2}, {"name": "B", "id": 3}])
    assert items == [("A", 1), ("B", 3)]  # l'entrée sans « name » est ignorée


# --- Boutons de test : activation/désactivation -----------------------------

def _button_dialog():
    w = types.SimpleNamespace(_workers={}, _closing=False)
    w.status_label = types.SimpleNamespace(_t="")
    w.status_label.setText = lambda t: setattr(w.status_label, "_t", t)
    w.test_button = types.SimpleNamespace(_e=True)
    w.test_button.setEnabled = lambda v: setattr(w.test_button, "_e", v)
    w.url_input = types.SimpleNamespace(text=lambda: "http://serveur")
    w.app_secret_input = types.SimpleNamespace(text=lambda: "secret")
    # Slots référencés par les vraies méthodes au moment du .connect(...) : ils
    # doivent exister comme attributs (leur contenu importe peu ici, chaque test
    # remplace la vraie méthode qu'il exerce).
    w.on_connection_tested = lambda *a: None
    w._on_counters_result = lambda *a: None
    return w


def test_test_url_disables_button_on_start(monkeypatch):
    w = _button_dialog()
    started = []
    w._start_worker = lambda kind, worker: (started.append(kind), True)[1]
    monkeypatch.setattr(preferences, "TestConnectionWorker",
                        lambda url: types.SimpleNamespace(
                            connection_tested=types.SimpleNamespace(connect=lambda f: None)))
    w.test_url = types.MethodType(preferences.PreferencesDialog.test_url, w)
    w.test_url()
    assert started == ["test_connection"]
    assert w.test_button._e is False


def test_test_url_leaves_button_when_duplicate(monkeypatch):
    w = _button_dialog()
    w._start_worker = lambda kind, worker: False
    monkeypatch.setattr(preferences, "TestConnectionWorker",
                        lambda url: types.SimpleNamespace(
                            connection_tested=types.SimpleNamespace(connect=lambda f: None)))
    w.test_url = types.MethodType(preferences.PreferencesDialog.test_url, w)
    w.test_url()
    assert w.test_button._e is True  # inchangé : aucun test lancé


def test_on_connection_tested_failure_reenables_button():
    w = _button_dialog()
    w.test_button._e = False
    w.on_connection_tested = types.MethodType(preferences.PreferencesDialog.on_connection_tested, w)
    w.on_connection_tested(False, "Erreur de connexion")
    assert w.test_button._e is True
    assert w.status_label._t == "Erreur de connexion"


def test_on_connection_tested_success_loads_counters():
    w = _button_dialog()
    called = []
    w.load_counters = lambda: called.append(True)
    w.on_connection_tested = types.MethodType(preferences.PreferencesDialog.on_connection_tested, w)
    w.on_connection_tested(True, "ok")
    assert called == [True]  # bouton laissé désactivé jusqu'à la fin du chargement


def test_on_counters_result_reenables_button():
    w = _button_dialog()
    w.test_button._e = False
    w.counters_loaded = types.SimpleNamespace(emit=lambda data: None)
    w._on_counters_result = types.MethodType(preferences.PreferencesDialog._on_counters_result, w)
    w._on_counters_result(True, [{"id": 1, "name": "c"}])
    assert w.test_button._e is True


def test_load_counters_reenables_button_if_not_started(monkeypatch):
    w = _button_dialog()
    w.test_button._e = False
    w._start_worker = lambda kind, worker: False
    monkeypatch.setattr(preferences, "CountersWorker",
                        lambda url, secret: types.SimpleNamespace(
                            result=types.SimpleNamespace(connect=lambda f: None)))
    w.load_counters = types.MethodType(preferences.PreferencesDialog.load_counters, w)
    w.load_counters()
    assert w.test_button._e is True
