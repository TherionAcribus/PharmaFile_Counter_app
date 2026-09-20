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
    # État « liste des comptoirs » : l'entrée d'attente initiale (libellé +
    # counter_id enregistré) et les couples (URL, secret) suivis par le vrai
    # dialogue.
    w.counter_id = 3
    w.counter_items = [("3 - Chargement en cours...", 3)]
    w.counter_combobox = types.SimpleNamespace(
        setItemText=lambda i, t: w.counter_items.__setitem__(
            i, (t, w.counter_items[i][1])))
    w._counters_loaded_for = None
    w._set_counter_placeholder = types.MethodType(
        preferences.PreferencesDialog._set_counter_placeholder, w)
    w._start_counters_worker = types.MethodType(
        preferences.PreferencesDialog._start_counters_worker, w)
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
    w._on_counters_result(True, [{"id": 1, "name": "c"}], ("http://serveur", "secret"))
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


def test_load_counters_passes_request_pair_to_handler(monkeypatch):
    """Le couple (url, secret) de la requête est rattaché au résultat : le
    handler reçoit les valeurs interrogées, pas celles des champs."""
    w = _button_dialog()
    captured = {}
    monkeypatch.setattr(
        preferences, "CountersWorker",
        lambda url, secret: types.SimpleNamespace(
            result=types.SimpleNamespace(
                connect=lambda f: captured.__setitem__("emit_to", f))))
    w._start_worker = lambda kind, worker: True
    w._on_counters_result = lambda ok, data, pair: captured.__setitem__(
        "got", (ok, data, pair))
    w.load_counters = types.MethodType(preferences.PreferencesDialog.load_counters, w)
    w.load_counters()
    captured["emit_to"](True, [{"id": 1}])  # le worker émet son résultat
    assert captured["got"] == (True, [{"id": 1}], ("http://serveur", "secret"))


# --- Chargement à l'ouverture de la page « Connexion » ----------------------

def _connexion_dialog(url="http://serveur", secret="secret"):
    w = _button_dialog()
    w.url_input = types.SimpleNamespace(text=lambda: url)
    w.app_secret_input = types.SimpleNamespace(text=lambda: secret)
    w._loads = []
    w.load_counters = lambda: w._loads.append(True)
    w._maybe_load_counters = types.MethodType(
        preferences.PreferencesDialog._maybe_load_counters, w)
    return w


def test_maybe_load_counters_when_configured():
    w = _connexion_dialog()
    w._maybe_load_counters()
    assert w._loads == [True]


def test_maybe_load_counters_skipped_without_url():
    w = _connexion_dialog(url="")
    w._maybe_load_counters()
    assert w._loads == []
    assert w.counter_items[0][0] == "3 - complétez l'adresse et le secret"


def test_maybe_load_counters_skipped_without_secret():
    w = _connexion_dialog(secret="")
    w._maybe_load_counters()
    assert w._loads == []
    assert w.counter_items[0][0] == "3 - complétez l'adresse et le secret"


def test_maybe_load_counters_skipped_when_list_current():
    w = _connexion_dialog()
    w._counters_loaded_for = ("http://serveur", "secret")
    w._maybe_load_counters()
    assert w._loads == []  # liste déjà à jour pour ce couple


def test_maybe_load_counters_reloads_after_field_change():
    w = _connexion_dialog()
    w._counters_loaded_for = ("http://autre", "secret")
    w._maybe_load_counters()
    assert w._loads == [True]  # champs modifiés : liste affichée périmée


def test_on_counters_result_success_remembers_pair():
    w = _button_dialog()
    w.counters_loaded = types.SimpleNamespace(emit=lambda data: None)
    w._on_counters_result = types.MethodType(
        preferences.PreferencesDialog._on_counters_result, w)
    w._on_counters_result(True, [{"id": 1, "name": "c"}], ("http://serveur", "secret"))
    assert w._counters_loaded_for == ("http://serveur", "secret")


def test_on_counters_result_failure_marks_placeholder():
    w = _button_dialog()
    w._on_counters_result = types.MethodType(
        preferences.PreferencesDialog._on_counters_result, w)
    w._on_counters_result(False, "Erreur: réseau", ("http://serveur", "secret"))
    assert w.status_label._t == "Erreur: réseau"
    assert w.counter_items[0][0] == "3 - échec du chargement"


def test_on_counters_result_failure_keeps_loaded_list():
    w = _button_dialog()
    w._counters_loaded_for = ("http://serveur", "secret")
    w.counter_items = [("Comptoir A", 3)]
    w._on_counters_result = types.MethodType(
        preferences.PreferencesDialog._on_counters_result, w)
    w._on_counters_result(False, "Erreur: réseau", ("http://autre", "secret"))
    assert w.counter_items[0][0] == "Comptoir A"  # liste réelle non écrasée


# --- Vérification du comptoir avant enregistrement ---------------------------

def _save_check_dialog(selected=3):
    """Faux dialogue pour _validate_counter_then_save / _on_save_counters_result."""
    w = _button_dialog()
    w.save_button = types.SimpleNamespace(_e=True)
    w.save_button.setEnabled = lambda v: setattr(w.save_button, "_e", v)
    w._finalized = []
    w._finalize_save = lambda: w._finalized.append(True)
    w._loaded = []
    w.counters_loaded = types.SimpleNamespace(emit=lambda data: w._loaded.append(data))
    w._selected = selected
    w.counter_combobox.currentData = lambda: w._selected
    w.counter_combobox.findData = lambda d: 0
    w.counter_combobox.setCurrentIndex = lambda i: None
    w._on_save_counters_result = types.MethodType(
        preferences.PreferencesDialog._on_save_counters_result, w)
    return w


def test_save_counters_failure_does_not_finalize(monkeypatch):
    warned = []
    monkeypatch.setattr(preferences.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a[1:3]))
    w = _save_check_dialog()
    w._on_save_counters_result(False, "Erreur: réseau", ("http://serveur", "secret"))
    assert w._finalized == []          # rien n'est enregistré
    assert warned                      # l'utilisateur est averti
    assert w.save_button._e is True    # bouton réactivé


def test_save_counters_unknown_counter_does_not_finalize(monkeypatch):
    """Le comptoir choisi venait de l'ancien serveur : pas d'enregistrement,
    la liste à jour est affichée pour un nouveau choix."""
    warned = []
    monkeypatch.setattr(preferences.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a[1:3]))
    w = _save_check_dialog(selected=9)
    w._on_save_counters_result(
        True, [{"id": 3, "name": "A"}, {"id": 4, "name": "B"}],
        ("http://serveur", "secret"))
    assert w._finalized == []
    assert warned and "n'existe pas" in warned[0][1]
    assert w._loaded == [[{"id": 3, "name": "A"}, {"id": 4, "name": "B"}]]
    assert w._counters_loaded_for == ("http://serveur", "secret")


def test_save_counters_known_counter_finalizes(monkeypatch):
    warned = []
    monkeypatch.setattr(preferences.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a))
    w = _save_check_dialog(selected=4)
    w._on_save_counters_result(
        True, [{"id": 3, "name": "A"}, {"id": 4, "name": "B"}],
        ("http://serveur", "secret"))
    assert w._finalized == [True]
    assert warned == []
    assert w._counters_loaded_for == ("http://serveur", "secret")


def test_validate_counter_then_save_starts_check():
    w = _save_check_dialog()
    started = []
    w._start_counters_worker = (
        lambda kind, url, secret, handler: (started.append(kind), True)[1])
    w._validate_counter_then_save = types.MethodType(
        preferences.PreferencesDialog._validate_counter_then_save, w)
    w._validate_counter_then_save()
    assert started == ["counters_save"]
    assert w.save_button._e is False  # doubles soumissions bloquées
