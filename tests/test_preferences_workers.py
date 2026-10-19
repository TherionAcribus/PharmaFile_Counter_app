"""Gestion des workers de test de la configuration du poste (point 9).

Couvre :
- le registre _start_worker : anti-doublon par « kind », conservation de la
  référence, remplacement d'un worker terminé, refus pendant la fermeture ;
- _shutdown_workers : interruption + attente bornée des workers actifs, puis vidage ;
- CountersWorker.run : réponses JSON invalides / jeton manquant / format
  inattendu émettent un échec explicite (le thread ne meurt pas silencieusement) ;
- update_counters : ignore les comptoirs sans les champs attendus ;
- boutons de test : désactivés pendant un test, réactivés à la fin / en cas d'échec.

Tests « faux self » : on appelle les vraies méthodes de StationDialog sur un
objet léger, sans construire tout le dialogue ni ouvrir de réseau réel.
"""

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import station_dialog  # noqa: E402


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
    w._start_worker = types.MethodType(station_dialog.StationDialog._start_worker, w)
    w._shutdown_workers = types.MethodType(station_dialog.StationDialog._shutdown_workers, w)
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
    assert running.waited == station_dialog.WORKER_SHUTDOWN_TIMEOUT_MS
    assert finished.interrupted == 0  # pas d'attente inutile sur un worker fini
    assert w._workers == {}


def test_shutdown_bounded_even_if_worker_hangs():
    w = _registry_dialog()
    stuck = FakeWorker(running=True, wait_returns=False)  # ne se termine pas
    w._workers = {"a": stuck}
    w._shutdown_workers()  # ne doit pas boucler indéfiniment
    assert stuck.waited == station_dialog.WORKER_SHUTDOWN_TIMEOUT_MS
    assert w._workers == {}


# --- CountersWorker.run : réponses invalides --------------------------------

def _resp(status, payload=None, raises=False, bad_json=False):
    def _call(*a, **k):
        if raises:
            raise station_dialog.requests.exceptions.RequestException("boom")

        def _json():
            if bad_json:
                raise ValueError("pas du JSON")
            return payload if payload is not None else {}

        return types.SimpleNamespace(status_code=status, json=_json)
    return _call


def _run_counters(monkeypatch, post, get=None):
    monkeypatch.setattr(station_dialog.requests, "post", post)
    if get is not None:
        monkeypatch.setattr(station_dialog.requests, "get", get)
    worker = station_dialog.CountersWorker("http://serveur", "secret")
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


# --- update_counters : champs manquants, ids et resélection -----------------

class FakeCombo:
    """Combo minimal : liste d'items (libellé, donnée) + index courant, avec la
    même sémantique que QComboBox (le premier item ajouté est sélectionné)."""

    def __init__(self):
        self.items = []
        self.index = -1

    def clear(self):
        self.items.clear()
        self.index = -1

    def addItem(self, label, data):
        self.items.append((label, data))
        if self.index == -1:
            self.index = 0

    def insertItem(self, i, label, data):
        self.items.insert(i, (label, data))

    def currentData(self):
        return self.items[self.index][1] if 0 <= self.index < len(self.items) else None

    def findData(self, data):
        for i, (_, d) in enumerate(self.items):
            if d == data:
                return i
        return -1

    def setCurrentIndex(self, i):
        self.index = i


def _counter_dialog(saved_id=None):
    w = types.SimpleNamespace(counter_id=saved_id)
    w.counter_combobox = FakeCombo()
    w.update_counters = types.MethodType(
        station_dialog.StationDialog.update_counters, w)
    return w


def test_update_counters_skips_entries_missing_fields():
    w = _counter_dialog()
    w.update_counters([{"name": "A", "id": 1}, {"id": 2}, {"name": "B", "id": 3}])
    # L'entrée sans « name » est ignorée ; sans comptoir enregistré, on affiche
    # une entrée d'attente (donnée None) plutôt qu'une présélection arbitraire.
    assert w.counter_combobox.items == [
        ("Sélectionnez un comptoir...", None), ("A", 1), ("B", 3)]


def test_update_counters_normalizes_string_ids():
    """Le serveur peut renvoyer des ids en chaîne : normalisés en entier, le
    comptoir enregistré est bien resélectionné (avant, findData(3) ne trouvait
    jamais « 3 »)."""
    w = _counter_dialog(saved_id=3)
    w.update_counters([{"name": "A", "id": "3"}, {"name": "B", "id": "4"}])
    assert w.counter_combobox.items == [("A", 3), ("B", 4)]
    assert w.counter_combobox.currentData() == 3


def test_update_counters_unknown_saved_counter_shows_placeholder():
    """Comptoir enregistré absent du serveur : PAS de présélection silencieuse
    du premier comptoir — une entrée d'attente sans donnée force un choix
    explicite à l'enregistrement."""
    w = _counter_dialog(saved_id=9)
    w.update_counters([{"name": "A", "id": 1}, {"name": "B", "id": 2}])
    assert w.counter_combobox.items[0][1] is None
    assert "introuvable" in w.counter_combobox.items[0][0]
    assert w.counter_combobox.currentData() is None


def test_update_counters_preserves_unsaved_selection():
    """L'utilisateur a choisi un autre comptoir sans enregistrer : un
    rechargement de la liste conserve SON choix, pas celui enregistré."""
    w = _counter_dialog(saved_id=1)
    w.counter_combobox.addItem("A", 1)
    w.counter_combobox.addItem("B", 2)
    w.counter_combobox.setCurrentIndex(1)   # choix utilisateur : comptoir 2
    w.update_counters([{"name": "A", "id": 1}, {"name": "B", "id": 2}])
    assert w.counter_combobox.currentData() == 2


def test_update_counters_skips_invalid_ids():
    w = _counter_dialog()
    w.update_counters([{"name": "A", "id": "abc"}, {"name": "B", "id": 0},
                       {"name": "C", "id": 2}])
    assert w.counter_combobox.items[1:] == [("C", 2)]  # ids invalides ignorés


# --- Boutons de test : activation/désactivation -----------------------------

def _button_dialog():
    w = types.SimpleNamespace(_workers={}, _closing=False)
    w.status_label = types.SimpleNamespace(_t="")
    w.status_label.setText = lambda t: setattr(w.status_label, "_t", t)
    w.test_button = types.SimpleNamespace(_e=True)
    w.test_button.setEnabled = lambda v: setattr(w.test_button, "_e", v)
    # Champ URL avec setText : test_url y réécrit la forme normalisée.
    w._url_text = "http://serveur"
    w.url_input = types.SimpleNamespace(
        text=lambda: w._url_text,
        setText=lambda t: setattr(w, "_url_text", t))
    w._secret_text = "secret"
    w.app_secret_input = types.SimpleNamespace(
        text=lambda: w._secret_text,
        setText=lambda t: setattr(w, "_secret_text", t))
    # Vraies méthodes : le secret lu partout débarrassé des espaces de bordure
    # (et, champ vide, le secret enregistré — jamais réaffiché).
    w._stored_secret = ""
    w._app_secret = types.MethodType(station_dialog.StationDialog._app_secret, w)
    w._normalize_secret_field = types.MethodType(
        station_dialog.StationDialog._normalize_secret_field, w)
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
        station_dialog.StationDialog._set_counter_placeholder, w)
    w._start_counters_worker = types.MethodType(
        station_dialog.StationDialog._start_counters_worker, w)
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
    monkeypatch.setattr(station_dialog, "TestConnectionWorker",
                        lambda url: types.SimpleNamespace(
                            connection_tested=types.SimpleNamespace(connect=lambda f: None)))
    w.test_url = types.MethodType(station_dialog.StationDialog.test_url, w)
    w.test_url()
    assert started == ["test_connection"]
    assert w.test_button._e is False


def test_test_url_leaves_button_when_duplicate(monkeypatch):
    w = _button_dialog()
    w._start_worker = lambda kind, worker: False
    monkeypatch.setattr(station_dialog, "TestConnectionWorker",
                        lambda url: types.SimpleNamespace(
                            connection_tested=types.SimpleNamespace(connect=lambda f: None)))
    w.test_url = types.MethodType(station_dialog.StationDialog.test_url, w)
    w.test_url()
    assert w.test_button._e is True  # inchangé : aucun test lancé


def test_test_url_normalizes_url_before_testing(monkeypatch):
    """La même normalisation qu'à l'enregistrement : espaces/slash final
    retirés, forme propre réécrite dans le champ et testée."""
    seen = {}
    monkeypatch.setattr(
        station_dialog, "TestConnectionWorker",
        lambda url: (seen.__setitem__("url", url), types.SimpleNamespace(
            connection_tested=types.SimpleNamespace(connect=lambda f: None)))[1])
    w = _button_dialog()
    w._url_text = "  http://serveur/  "
    w._start_worker = lambda kind, worker: True
    w.test_url = types.MethodType(station_dialog.StationDialog.test_url, w)
    w.test_url()
    assert seen["url"] == "http://serveur"
    assert w._url_text == "http://serveur"


def test_test_url_refuses_invalid_url(monkeypatch):
    """Pas de worker sur une URL que l'enregistrement refuserait (schéma non
    http(s), http distant, vide) : avertissement + message de statut."""
    built = []
    warned = []
    monkeypatch.setattr(station_dialog, "TestConnectionWorker",
                        lambda url: built.append(url))
    monkeypatch.setattr(station_dialog.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a))
    w = _button_dialog()
    w._url_text = "http://serveur-distant.example.com"   # http distant interdit
    w._start_worker = lambda kind, worker: built.append("started")
    w.test_url = types.MethodType(station_dialog.StationDialog.test_url, w)
    w.test_url()
    assert built == []
    assert warned
    assert "https" in w.status_label._t or "http" in w.status_label._t


def test_on_connection_tested_failure_reenables_button():
    w = _button_dialog()
    w.test_button._e = False
    w.on_connection_tested = types.MethodType(station_dialog.StationDialog.on_connection_tested, w)
    w.on_connection_tested(False, "Erreur de connexion")
    assert w.test_button._e is True
    assert w.status_label._t == "Erreur de connexion"


def test_on_connection_tested_success_loads_counters():
    w = _button_dialog()
    called = []
    w.load_counters = lambda: called.append(True)
    w.on_connection_tested = types.MethodType(station_dialog.StationDialog.on_connection_tested, w)
    w.on_connection_tested(True, "ok")
    assert called == [True]  # bouton laissé désactivé jusqu'à la fin du chargement


def test_on_counters_result_reenables_button():
    w = _button_dialog()
    w.test_button._e = False
    w.counters_loaded = types.SimpleNamespace(emit=lambda data: None)
    w._on_counters_result = types.MethodType(station_dialog.StationDialog._on_counters_result, w)
    w._on_counters_result(True, [{"id": 1, "name": "c"}], ("http://serveur", "secret"))
    assert w.test_button._e is True


def test_load_counters_reenables_button_if_not_started(monkeypatch):
    w = _button_dialog()
    w.test_button._e = False
    w._start_worker = lambda kind, worker: False
    monkeypatch.setattr(station_dialog, "CountersWorker",
                        lambda url, secret: types.SimpleNamespace(
                            result=types.SimpleNamespace(connect=lambda f: None)))
    w.load_counters = types.MethodType(station_dialog.StationDialog.load_counters, w)
    w.load_counters()
    assert w.test_button._e is True


def test_load_counters_passes_request_pair_to_handler(monkeypatch):
    """Le couple (url, secret) de la requête est rattaché au résultat : le
    handler reçoit les valeurs interrogées, pas celles des champs."""
    w = _button_dialog()
    captured = {}
    monkeypatch.setattr(
        station_dialog, "CountersWorker",
        lambda url, secret: types.SimpleNamespace(
            result=types.SimpleNamespace(
                connect=lambda f: captured.__setitem__("emit_to", f))))
    w._start_worker = lambda kind, worker: True
    w._on_counters_result = lambda ok, data, pair: captured.__setitem__(
        "got", (ok, data, pair))
    w.load_counters = types.MethodType(station_dialog.StationDialog.load_counters, w)
    w.load_counters()
    captured["emit_to"](True, [{"id": 1}])  # le worker émet son résultat
    assert captured["got"] == (True, [{"id": 1}], ("http://serveur", "secret"))


def test_load_counters_uses_normalized_pair(monkeypatch):
    """Le couple des requêtes est celui qui sera enregistré : URL normalisée et
    secret débarrassé des espaces de copier-coller en bordure."""
    captured = {}
    monkeypatch.setattr(
        station_dialog, "CountersWorker",
        lambda url, secret: (captured.__setitem__("pair", (url, secret)),
                             types.SimpleNamespace(
                                 result=types.SimpleNamespace(
                                     connect=lambda f: None)))[1])
    w = _button_dialog()
    w._url_text = "  http://serveur/  "
    w._secret_text = "  secret  "
    w._start_worker = lambda kind, worker: True
    w.load_counters = types.MethodType(station_dialog.StationDialog.load_counters, w)
    w.load_counters()
    assert captured["pair"] == ("http://serveur", "secret")


def test_on_counters_result_ignores_stale_response():
    """Réponse d'un chargement lancé avec d'anciennes valeurs : ignorée plutôt
    que d'afficher la liste d'un serveur périmé."""
    w = _button_dialog()
    emitted = []
    w.counters_loaded = types.SimpleNamespace(emit=lambda d: emitted.append(d))
    w._on_counters_result = types.MethodType(
        station_dialog.StationDialog._on_counters_result, w)
    w._url_text = "http://autre"   # champs modifiés pendant le worker
    w._on_counters_result(
        True, [{"id": 1, "name": "c"}], ("http://serveur", "secret"))
    assert emitted == []
    assert w._counters_loaded_for is None   # pas marqué « à jour » pour autant


def test_test_url_strips_secret_field(monkeypatch):
    """Le secret collé avec espaces est nettoyé : la forme propre est réécrite
    dans le champ, comme pour l'URL."""
    monkeypatch.setattr(
        station_dialog, "TestConnectionWorker",
        lambda url: types.SimpleNamespace(
            connection_tested=types.SimpleNamespace(connect=lambda f: None)))
    w = _button_dialog()
    w._secret_text = "  secret  "
    w._start_worker = lambda kind, worker: True
    w.test_url = types.MethodType(station_dialog.StationDialog.test_url, w)
    w.test_url()
    assert w._secret_text == "secret"


# --- Retouche des champs connexion : liste périmée --------------------------

def _stale_dialog():
    """Dialogue avec une liste de comptoirs déjà chargée, dont on suit le
    contenu pour vérifier qu'elle est marquée périmée à l'édition."""
    w = _button_dialog()
    w._counters_loaded_for = ("http://serveur", "secret")
    w.counter_items = [("Comptoir A", 3), ("Comptoir B", 4)]
    w.counter_combobox = types.SimpleNamespace(
        clear=lambda: w.counter_items.clear(),
        addItem=lambda label, data: w.counter_items.append((label, data)))
    w._on_connection_fields_changed = types.MethodType(
        station_dialog.StationDialog._on_connection_fields_changed, w)
    return w


def test_editing_url_marks_loaded_list_stale():
    w = _stale_dialog()
    w._url_text = "http://autre"
    w._on_connection_fields_changed()
    assert w._counters_loaded_for is None
    # Entrée d'attente avec le comptoir enregistré, pas de liste trompeuse.
    assert w.counter_items == [("3 - liste périmée, relancez le test", 3)]


def test_equivalent_edit_keeps_loaded_list():
    """Retaper la même URL normalisée (espaces, slash final) ne vide pas une
    liste pourtant valable pour le même couple."""
    w = _stale_dialog()
    w._url_text = "  http://serveur/ "
    w._on_connection_fields_changed()
    assert w._counters_loaded_for == ("http://serveur", "secret")
    assert w.counter_items == [("Comptoir A", 3), ("Comptoir B", 4)]


def test_editing_secret_marks_loaded_list_stale():
    w = _stale_dialog()
    w._secret_text = "nouveau-secret"
    w._on_connection_fields_changed()
    assert w._counters_loaded_for is None
    assert w.counter_items[0][1] == 3   # comptoir enregistré conservé en donnée


# --- Chargement de la liste des comptoirs à l'ouverture -----------------------

def _connexion_dialog(url="http://serveur", secret="secret"):
    w = _button_dialog()
    w.url_input = types.SimpleNamespace(text=lambda: url)
    w.app_secret_input = types.SimpleNamespace(text=lambda: secret)
    w._loads = []
    w.load_counters = lambda: w._loads.append(True)
    w._maybe_load_counters = types.MethodType(
        station_dialog.StationDialog._maybe_load_counters, w)
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
        station_dialog.StationDialog._on_counters_result, w)
    w._on_counters_result(True, [{"id": 1, "name": "c"}], ("http://serveur", "secret"))
    assert w._counters_loaded_for == ("http://serveur", "secret")


def test_on_counters_result_failure_marks_placeholder():
    w = _button_dialog()
    w._on_counters_result = types.MethodType(
        station_dialog.StationDialog._on_counters_result, w)
    w._on_counters_result(False, "Erreur: réseau", ("http://serveur", "secret"))
    assert w.status_label._t == "Erreur: réseau"
    assert w.counter_items[0][0] == "3 - échec du chargement"


def test_on_counters_result_failure_keeps_loaded_list():
    w = _button_dialog()
    w._counters_loaded_for = ("http://serveur", "secret")
    w.counter_items = [("Comptoir A", 3)]
    w._on_counters_result = types.MethodType(
        station_dialog.StationDialog._on_counters_result, w)
    w._on_counters_result(False, "Erreur: réseau", ("http://autre", "secret"))
    assert w.counter_items[0][0] == "Comptoir A"  # liste réelle non écrasée


# --- Vérification du comptoir avant enregistrement ---------------------------

def _save_check_dialog(selected=3):
    """Faux dialogue pour _validate_counter_then_save / _on_save_counters_result."""
    w = _button_dialog()
    w.save_button = types.SimpleNamespace(_e=True)
    w.save_button.setEnabled = lambda v: setattr(w.save_button, "_e", v)
    w.apply_button = types.SimpleNamespace(_e=True)
    w.apply_button.setEnabled = lambda v: setattr(w.apply_button, "_e", v)
    w._finalized = []
    w._finalize_save = lambda: w._finalized.append(True)
    w._loaded = []
    w.counters_loaded = types.SimpleNamespace(emit=lambda data: w._loaded.append(data))
    # Le comptoir à vérifier et le couple (URL, secret) viennent du snapshot
    # posé au clic « Enregistrer », pas des widgets (point 1).
    w._pending_save = {"settings": {"web_url": "http://serveur",
                                    "counter_id": selected},
                       "app_secret": "secret"}
    w._pending_pair = types.MethodType(
        station_dialog.StationDialog._pending_pair, w)
    w.counter_combobox.findData = lambda d: 0
    w.counter_combobox.setCurrentIndex = lambda i: None
    w._focus = lambda *a, **k: None
    w._on_save_counters_result = types.MethodType(
        station_dialog.StationDialog._on_save_counters_result, w)
    return w


def test_save_counters_failure_does_not_finalize(monkeypatch):
    warned = []
    monkeypatch.setattr(station_dialog.QMessageBox, "warning",
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
    monkeypatch.setattr(station_dialog.QMessageBox, "warning",
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
    monkeypatch.setattr(station_dialog.QMessageBox, "warning",
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
        station_dialog.StationDialog._validate_counter_then_save, w)
    w._validate_counter_then_save()
    assert started == ["counters_save"]
    assert w.save_button._e is False  # doubles soumissions bloquées
