"""Sécurité des actions patient pendant les requêtes et les reconstructions (F2).

Deux défauts reproduits en audit :

* le verrou anti-doublon ne bloquait que la MÊME action : « Suivant » puis
  « Pause » partaient en parallèle, la seconde requête étant construite sur un
  état déjà dépassé ;
* le « busy » était porté par le widget : reconstruire l'interface pendant une
  requête laissait le ``set_busy(False)`` de fin s'adresser à un bouton détruit
  (« RuntimeError: Internal C++ object already deleted »), et le NOUVEAU bouton
  restait cliquable pendant le reste du vol.

On couvre ici la chaîne complète : garde métier commune (boutons, menus,
raccourcis, systray passent tous par les mêmes ``call_web_function_*`` /
``on_action_*``), groupe d'exclusion côté CounterApi, verrou logique survivant
aux reconstructions, et resynchronisation après un résultat incertain.

La VRAIE CounterApi et le VRAI TaskRegistry sont utilisés ; seul le transport
réseau est simulé.
"""

import logging
import os
import sys
import types
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402
from counter_api import CounterApi, PATIENT_ACTION_GROUP  # noqa: E402
from net_result import NetResult  # noqa: E402
from task_registry import TaskRegistry  # noqa: E402

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
    def __init__(self, **spec):
        self.spec = spec
        self.result = FakeSignal()
        self.finished = FakeSignal()
        self.started = False

    def start(self):
        self.started = True

    def complete(self, result=None):
        self.result.emit(result or NetResult(status=204))
        self.finished.emit()


class FakeNetworkManager:
    def __init__(self):
        self.handles = []

    def make_handle(self, url, method='GET', data=None, headers=None,
                    idempotency_key=None):
        handle = FakeHandle(url=url, method=method, data=data, headers=headers,
                            idempotency_key=idempotency_key)
        self.handles.append(handle)
        return handle


class FakeButton:
    """Widget-bouton double : mémorise les bascules de verrou « occupé »."""

    def __init__(self):
        self.busy_states = []

    def set_busy(self, busy):
        self.busy_states.append(busy)

    @property
    def busy(self):
        return self.busy_states[-1] if self.busy_states else False


class DeadButton:
    """Bouton détruit pendant le vol : toute méthode lève RuntimeError,
    comme le wrapper shiboken d'un objet C++ supprimé."""

    def set_busy(self, busy):
        raise RuntimeError("Internal C++ object (DebounceButton) already deleted")


class FakeLabel:
    def __init__(self):
        self.text = None

    def setText(self, t):
        self.text = t

    def setToolTip(self, t):
        pass


class FakeSession:
    """SessionController double : compte les demandes de resynchronisation."""

    def __init__(self):
        self.requested = 0

    def request_resync(self, on_ready):
        self.requested += 1


class FakeWindow:
    """Faux ``self`` portant les vraies méthodes de MainWindow sous test, avec
    la vraie CounterApi branchée sur un faux transport."""

    _PATIENT_BUTTONS = main.MainWindow._PATIENT_BUTTONS

    def __init__(self, staff_id=5, patient_id=42):
        self.counter_id = COUNTER
        self.staff_id = staff_id
        self.patient_id = patient_id
        self.my_patient = ({"counter_id": COUNTER, "id": patient_id,
                            "status": "calling", "language_code": "fr",
                            "call_number": "A-42", "activity": "Ordonnance"}
                           if patient_id else None)
        self.label_patient = FakeLabel()
        self.logger = logging.getLogger("test.patient_action_guard")
        self._tasks = TaskRegistry()
        self.session = FakeSession()

        self.btn_next = FakeButton()
        self.btn_validate = FakeButton()
        self.btn_pause = FakeButton()

        # Effets de bord isolés en mocks.
        self.update_my_buttons = mock.MagicMock()
        self.close_please_validate_notification = mock.MagicMock()
        self.show_notification = mock.MagicMock()
        self.play_notification_sound = mock.MagicMock()

        # staticmethod : résolution directe sur la classe, sans MethodType.
        self._safe_widget = main.MainWindow._safe_widget

        for name in (
            "update_my_patient", "_on_invalid_patient", "handle_result",
            "handle_queue_result", "_patient_result_handler", "_patient_action_group",
            "validate_my_patient", "call_web_function_validate",
            "call_web_function_validate_and_call_next", "call_web_function_pause",
            "call_web_function_validate_and_call_specifique", "recall",
            "on_action_wait", "on_action_validate", "on_action_wait_for",
            "on_action_delete", "_notify_network_error", "_patient_action_ready",
            "_busy_ref", "_set_busy_widgets", "_apply_busy_widgets",
            "_on_patient_action_refused", "_resync_if_uncertain",
            "_request_resync", "_on_resync_ready", "create_interface",
            "_set_patient_label",
        ):
            setattr(self, name, types.MethodType(getattr(main.MainWindow, name), self))

        # Vraie couche d'accès, faux transport : on teste le chemin complet.
        self.api = CounterApi(
            FakeNetworkManager(), self._tasks,
            url_provider=lambda: BASE,
            counter_id_provider=lambda: COUNTER,
            logger=self.logger,
            on_refused=lambda reason: self._on_patient_action_refused(reason),
        )

    def _update_menu_actions(self, enable):
        pass

    @property
    def handles(self):
        return self.api.network_manager.handles


def _activity():
    return {"id": 9, "name": "Accueil"}


# --- exclusion mutuelle : une seule action patient à la fois ------------------


def test_pause_pendant_suivant_refusee():
    """Régression F2 : « Pause » pendant « Suivant » partait en parallèle."""
    w = FakeWindow()
    w.call_web_function_validate_and_call_next()
    assert len(w.handles) == 1
    w.call_web_function_pause()
    assert len(w.handles) == 1          # refusée, pas de requête
    w.show_notification.assert_called()  # « Une action est déjà en cours… »


def test_toutes_les_entrees_partagent_la_garde():
    """Boutons, menus, systray et raccourcis aboutissent tous aux mêmes
    ``call_web_function_*`` / ``on_action_*`` : une seule garde suffit."""
    w = FakeWindow()
    w.call_web_function_pause()          # 1re action en vol
    assert len(w.handles) == 1
    for declencheur in (
        w.call_web_function_validate_and_call_next,
        w.call_web_function_validate,
        w.call_web_function_pause,
        w.recall,
        w.on_action_wait,
        lambda: w.on_action_validate(7),
        lambda: w.on_action_wait_for(_activity(), 7),
        lambda: w.call_web_function_validate_and_call_specifique(7),
    ):
        declencheur()
    assert len(w.handles) == 1


def test_action_debloquee_apres_la_reponse():
    w = FakeWindow()
    w.call_web_function_validate_and_call_next()
    # Réponse 200 avec le patient appelé : le groupe est libéré ET le patient
    # courant persiste (un 204 l'aurait vidé, rendant « Pause » sans objet).
    w.handles[0].complete(NetResult(200, data={
        "counter_id": COUNTER, "id": 43, "status": "calling",
        "language_code": "fr", "call_number": "A-43", "activity": "Ordonnance"}))
    w.call_web_function_pause()
    assert len(w.handles) == 2


# --- gardes de session et de patient ------------------------------------------


def test_aucune_action_sans_agent_connecte():
    """Les icônes systray existent avant l'identification : sans staff, aucune
    action patient ne doit partir."""
    w = FakeWindow(staff_id=None)
    w.call_web_function_validate_and_call_next()
    w.call_web_function_pause()
    assert w.handles == []


def test_refus_sans_agent_explique_a_l_utilisateur():
    """Le refus « aucun agent au comptoir » n'est plus silencieux : sans
    retour visible, l'action semble ignorée — une notification l'explique."""
    w = FakeWindow(staff_id=None)
    w.call_web_function_validate_and_call_next()
    assert w.handles == []
    w.show_notification.assert_called_once()
    notification = w.show_notification.call_args[0][0]
    assert notification["origin"] == "action_refused"
    assert "Identifiez-vous" in notification["message"]


def test_echec_action_notifie_en_action_error():
    """Régression E5 : ``_notify_network_error`` étiquetait tout en
    « connection » — la case « connexion » des préférences masquait alors
    aussi les échecs d'action. Origine dédiée « action_error » (SYSTÈME)."""
    w = FakeWindow()
    w.call_web_function_validate_and_call_next()
    assert len(w.handles) == 1
    w.handles[0].complete(NetResult.network_error("timeout"))
    notification = w.show_notification.call_args[0][0]
    assert notification["origin"] == "action_error"


def test_pause_sans_patient_refusee():
    w = FakeWindow(patient_id=None)
    w.call_web_function_pause()
    assert w.handles == []


def test_recall_sans_patient_refuse():
    w = FakeWindow(patient_id=None)
    w.recall()
    assert w.handles == []


# --- actions de file ----------------------------------------------------------


def test_action_file_autorisee_quand_rien_n_est_en_cours():
    w = FakeWindow()
    w.on_action_validate(7)
    assert len(w.handles) == 1
    assert w.handles[0].spec["url"].endswith("/api/counter/validate_patient/7")


def test_action_file_refusee_pendant_action_comptoir():
    """Pendant que le comptoir bascule (appel en vol), on refuse de modifier la
    file : la composition vue par l'utilisateur est déjà dépassée."""
    w = FakeWindow()
    w.call_web_function_validate_and_call_next()
    w.on_action_validate(7)
    assert len(w.handles) == 1


def test_action_sur_patient_courant_rejoint_le_groupe():
    """Remettre en attente LE patient courant entre dans le groupe exclusif :
    un « Suivant » lancé pendant ce vol doit être refusé."""
    w = FakeWindow()
    w.on_action_wait_for(_activity())          # patient_id=None -> courant (42)
    assert w._tasks.is_group_active(PATIENT_ACTION_GROUP)
    w.call_web_function_validate_and_call_next()
    assert len(w.handles) == 1


def test_action_sur_autre_patient_hors_groupe():
    """La même action sur un AUTRE patient de la file ne prend pas le verrou
    du groupe (mais la garde UI la refuse tout de même si le comptoir bouge)."""
    w = FakeWindow()
    w.on_action_wait_for(_activity(), patient_id=7)  # autre patient
    assert len(w.handles) == 1
    assert not w._tasks.is_group_active(PATIENT_ACTION_GROUP)


# --- verrou « occupé » indépendant des widgets ---------------------------------


def test_busy_verrouille_les_trois_boutons_pendant_le_vol():
    w = FakeWindow()
    w.call_web_function_validate_and_call_next()
    for bouton in (w.btn_next, w.btn_validate, w.btn_pause):
        assert bouton.busy is True
    w.handles[0].complete()
    for bouton in (w.btn_next, w.btn_validate, w.btn_pause):
        assert bouton.busy_states == [True, False]


def test_busy_survit_a_la_reconstruction_de_l_interface():
    """Régression F2 : l'interface est reconstruite PENDANT la requête — les
    nouveaux boutons doivent hériter du verrou, et le déverrouillage de fin ne
    doit toucher aucun objet détruit."""
    w = FakeWindow()
    w.call_web_function_validate_and_call_next()
    assert w._busy_widgets                     # verrous logiques enregistrés

    # Reconstruction : les anciens boutons sont détruits, de nouveaux arrivent.
    w.btn_next = FakeButton()
    w.btn_validate = FakeButton()
    w.btn_pause = FakeButton()
    w._apply_busy_widgets()
    assert w.btn_next.busy and w.btn_validate.busy and w.btn_pause.busy

    # Fin de requête : ce sont les NOUVEAUX boutons qui sont relâchés.
    w.handles[0].complete()
    assert w.btn_next.busy_states == [True, False]
    assert w._busy_widgets == {}


def test_cleanup_ne_touche_pas_un_widget_detruit():
    """Le bouton n'existe plus côté Qt quand la requête finit : aucun
    RuntimeError ne doit fuiter du nettoyage."""
    w = FakeWindow()
    w.call_web_function_validate_and_call_next()
    w.btn_next = DeadButton()
    w.btn_validate = DeadButton()
    w.btn_pause = DeadButton()
    w.handles[0].complete()                    # ne doit pas lever
    assert w._busy_widgets == {}


# --- résultat incertain -> resynchronisation -----------------------------------


def test_resultat_incertains_declenche_une_resync():
    """Transport rompu (0) ou 5xx : le serveur a pu appliquer l'action — on
    resynchronise plutôt que de réutiliser un état local potentiellement faux."""
    w = FakeWindow()
    w.handle_result(NetResult.network_error("timeout"))
    assert w.session.requested == 1
    w.handle_result(NetResult.from_response(500, "boom"))
    assert w.session.requested == 2


def test_resultat_refuse_ne_resynchronise_pas():
    """Une erreur 4xx est une réponse explicite du serveur : l'action n'a PAS
    été appliquée, l'état local reste fiable."""
    w = FakeWindow()
    w.handle_result(NetResult.from_response(404, "introuvable"))
    assert w.session.requested == 0
