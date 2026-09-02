"""Couche d'accès au serveur du comptoir (point 10.8).

On vérifie ici ce que chaque appelant n'a plus à refaire : la bonne URL, la
bonne méthode, la clé anti-doublon, le bouton remis en état, la lecture typée,
et la lecture À LA VOLÉE de la configuration (un changement de serveur ou de
comptoir doit être pris en compte sans reconstruire l'objet).
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from counter_api import CounterApi  # noqa: E402
from net_result import NetResult  # noqa: E402

BASE = "http://srv:5000"
COUNTER = 3


class FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class FakeHandle:
    """Double de RequestHandle : enregistre l'appel et permet de simuler la fin."""

    def __init__(self, **spec):
        self.spec = spec
        self.result = FakeSignal()
        self.finished = FakeSignal()
        self.started = False

    def start(self):
        self.started = True

    def complete(self, result=None):
        self.result.emit(result or NetResult(status=200, text="", content_type=None))
        self.finished.emit()


class FakeNetworkManager:
    def __init__(self, blocking_result=None):
        self.handles = []
        self.blocking_calls = []
        self.blocking_result = blocking_result
        self.token = "jeton"
        self.cleared = False
        self.stopped = None

    def make_handle(self, url, method='GET', data=None, headers=None, idempotency_key=None):
        handle = FakeHandle(url=url, method=method, data=data, headers=headers,
                            idempotency_key=idempotency_key)
        self.handles.append(handle)
        return handle

    def request_blocking(self, url, method='GET', **kwargs):
        self.blocking_calls.append((url, method, kwargs))
        return self.blocking_result

    def fetch_token_blocking(self):
        return self.token

    def clear_token(self):
        self.cleared = True

    def stop(self, timeout_ms=None):
        self.stopped = timeout_ms


class FakeTasks:
    def __init__(self, active_keys=()):
        self.added = []
        self.removed = []
        self.active_keys = set(active_keys)

    def is_active(self, key):
        return key in self.active_keys

    def add(self, task, key=None):
        self.added.append(key)
        if key:
            self.active_keys.add(key)

    def remove(self, task, key=None):
        self.removed.append(key)
        self.active_keys.discard(key)


class FakeButton:
    def __init__(self):
        self.busy_states = []

    def set_busy(self, busy):
        self.busy_states.append(busy)


@pytest.fixture
def api():
    nm = FakeNetworkManager()
    tasks = FakeTasks()
    obj = CounterApi(nm, tasks,
                     url_provider=lambda: obj.url,
                     counter_id_provider=lambda: obj.counter_id,
                     logger=logging.getLogger("test.counter_api"))
    obj.url = BASE
    obj.counter_id = COUNTER
    obj.tasks = tasks
    return obj


def _last(api):
    return api.network_manager.handles[-1]


# --- URL, méthode et clé de chaque action ----------------------------------

CAS = [
    ("validate_and_call_next", (), f"{BASE}/validate_and_call_next/{COUNTER}",
     "POST", "validate_and_call_next"),
    ("validate_current_patient", (7,), f"{BASE}/validate_patient/{COUNTER}/7", "POST", "validate"),
    ("validate_queued_patient", (7,), f"{BASE}/api/counter/validate_patient/7", "POST", "validate"),
    ("pause_current_patient", (7,), f"{BASE}/pause_patient/{COUNTER}/7", "POST", "pause"),
    ("relaunch_call", (), f"{BASE}/app/counter/relaunch_patient_call/{COUNTER}", "POST", "recall"),
    ("call_specific_patient", (7,), f"{BASE}/call_specific_patient/{COUNTER}/7",
     "POST", "call_specific:7"),
    ("put_standing", (7,), f"{BASE}/api/counter/put_standing_list/7", "POST", "put_standing:7"),
    ("delete_patient", (7,), f"{BASE}/api/counter/delete_patient/7", "POST", "delete:7"),
    ("logout_staff", (), f"{BASE}/app/counter/remove_staff", "POST", "disconnect"),
    ("fetch_staff", (), f"{BASE}/api/counter/is_staff_on_counter/{COUNTER}", "GET", "setup_user"),
]


@pytest.mark.parametrize("nom, args, url, methode, cle", CAS, ids=[c[0] for c in CAS])
def test_url_methode_et_cle(api, nom, args, url, methode, cle):
    getattr(api, nom)(*args)
    handle = _last(api)
    assert handle.spec["url"] == url
    assert handle.spec["method"] == methode
    assert handle.started is True
    assert api.tasks.added == [cle]


def test_put_standing_avec_activite(api):
    api.put_standing(7, 4)
    assert _last(api).spec["url"] == f"{BASE}/api/counter/put_standing_list/7/4"
    # La clé reste celle du patient : deux remises en attente du même patient ne
    # doivent pas pouvoir partir en parallèle.
    assert api.tasks.added == ["put_standing:7"]


def test_login_staff_envoie_les_champs_attendus(api):
    api.login_staff("AB", True)
    handle = _last(api)
    assert handle.spec["url"] == f"{BASE}/app/counter/update_staff"
    assert handle.spec["data"] == {"initials": "AB", "counter_id": COUNTER,
                                   "deconnect": True, "app": True}


def test_logout_staff_envoie_le_comptoir(api):
    api.logout_staff()
    assert _last(api).spec["data"] == {"counter_id": COUNTER}


# --- idempotence ------------------------------------------------------------

def test_idempotence_de_l_appel_suivant(api):
    api.validate_and_call_next()
    cle1 = _last(api).spec["idempotency_key"]
    assert cle1
    api.tasks.active_keys.clear()
    api.validate_and_call_next()
    # Une clé NEUVE par action utilisateur (sinon la deuxième demande volontaire
    # serait ignorée par le serveur comme un rejeu).
    assert _last(api).spec["idempotency_key"] != cle1


def test_pas_de_cle_d_idempotence_sur_les_autres_actions(api):
    api.pause_current_patient(7)
    assert _last(api).spec["idempotency_key"] is None


# --- anti-doublon, arrêt, bouton occupé ------------------------------------

def test_action_identique_refusee_tant_que_la_premiere_court(api):
    assert api.pause_current_patient(7) is not None
    assert api.pause_current_patient(7) is None        # même clé -> refusée
    assert len(api.network_manager.handles) == 1


def test_action_relancable_apres_la_fin_de_la_precedente(api):
    handle = api.pause_current_patient(7)
    handle.complete()
    assert api.tasks.removed == ["pause"]
    assert api.pause_current_patient(7) is not None


def test_actions_de_patients_differents_en_parallele(api):
    assert api.delete_patient(1) is not None
    assert api.delete_patient(2) is not None           # clés distinctes


def test_bouton_occupe_puis_retabli(api):
    bouton = FakeButton()
    handle = api.validate_current_patient(7, busy_button=bouton)
    assert bouton.busy_states == [True]
    handle.complete()
    assert bouton.busy_states == [True, False]


def test_bouton_retabli_meme_en_erreur(api):
    bouton = FakeButton()
    handle = api.validate_current_patient(7, busy_button=bouton)
    handle.complete(NetResult.from_response(500, "boom"))
    assert bouton.busy_states == [True, False]


def test_on_result_branche_avant_le_demarrage(api):
    recus = []
    handle = api.validate_current_patient(7, on_result=recus.append)
    handle.complete()
    assert len(recus) == 1


# --- configuration lue à la volée ------------------------------------------

def test_changement_de_serveur_pris_en_compte(api):
    api.pause_current_patient(7)
    api.url = "https://autre:443"
    api.counter_id = 9
    api.tasks.active_keys.clear()
    api.pause_current_patient(7)
    assert _last(api).spec["url"] == "https://autre:443/pause_patient/9/7"


# --- lectures ---------------------------------------------------------------

def _api_lecture(payload, status=200):
    nm = FakeNetworkManager(blocking_result=NetResult.from_response(status, payload))
    return CounterApi(nm, FakeTasks(), lambda: BASE, lambda: COUNTER,
                      logger=logging.getLogger("test.counter_api"))


def test_fetch_state_retourne_le_dictionnaire():
    api = _api_lecture('{"revision": 4}')
    assert api.fetch_state() == {"revision": 4}
    url, method, _ = api.network_manager.blocking_calls[0]
    assert url == f"{BASE}/api/counter/{COUNTER}/state"
    assert method == "GET"


def test_fetch_state_none_si_type_inattendu():
    """Le serveur répond 200 mais avec une liste : on ne propage pas la surprise."""
    assert _api_lecture('[1, 2]').fetch_state() is None


def test_fetch_state_none_si_erreur():
    assert _api_lecture('{}', status=500).fetch_state() is None


def test_fetch_patients_list_retourne_une_liste_vide_en_erreur():
    """Les appelants itèrent dessus : jamais None."""
    assert _api_lecture('{}', status=500).fetch_patients_list() == []
    assert _api_lecture('[{"id": 1}]').fetch_patients_list() == [{"id": 1}]


def test_fetch_current_patient():
    api = _api_lecture('{"id": 7}')
    assert api.fetch_current_patient() == {"id": 7}
    assert api.network_manager.blocking_calls[0][0] == (
        f"{BASE}/api/counter/is_patient_on_counter/{COUNTER}")


# --- jeton et arrêt ---------------------------------------------------------

def test_fetch_token(api):
    assert api.fetch_token() == "jeton"


def test_fetch_token_leve_si_refus(api):
    api.network_manager.token = None
    with pytest.raises(RuntimeError):
        api.fetch_token()


def test_clear_token_et_stop(api):
    api.clear_token()
    api.stop(timeout_ms=1234)
    assert api.network_manager.cleared is True
    assert api.network_manager.stopped == 1234
