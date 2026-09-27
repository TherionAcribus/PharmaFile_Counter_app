"""Unification de l'état du patient courant (F1).

Avant la correction, ``handle_result`` nourrissait ``update_my_patient`` (qui
met à jour ``patient_id`` et le libellé) mais jamais ``self.my_patient`` — la
source relue par ``validate_my_patient`` et par ``create_interface``. Résultat
reproduit : après un appel réussi (200), le bouton « Valider » n'envoyait rien
car ``my_patient`` restait None, et une reconstruction d'interface perdait le
patient affiché.

On vérifie aussi que les actions portant sur un patient de la FILE (menu
contextuel : valider, supprimer, réassigner) ne vident pas l'affichage du
patient courant quand le serveur répond 201 — le statut 201 ne concerne le
patient du comptoir que lorsque l'action le visait réellement.

Comme les autres tests de la fenêtre : les vraies méthodes sont liées à un faux
``self`` minimal, le réseau est une fausse API — rien ne part vers un serveur.
"""

import logging
import os
import sys
import types
from unittest import mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402
from net_result import NetResult  # noqa: E402


class FakeLabel:
    def __init__(self):
        self.text = None
        self.tooltip = None

    def setText(self, t):
        self.text = t

    def setToolTip(self, t):
        self.tooltip = t


class FakeApi:
    """Enregistre les appels et conserve le dernier handler de résultat."""

    def __init__(self):
        self.calls = []
        self.last_result_handler = None

    def _record(self, name, on_result, *args):
        self.calls.append((name, *args))
        self.last_result_handler = on_result

    def validate_current_patient(self, patient_id, on_result=None, busy_button=None):
        self._record("validate_current", on_result, patient_id)

    def validate_queued_patient(self, patient_id, on_result=None, busy_button=None):
        self._record("validate_queued", on_result, patient_id)

    def put_standing(self, patient_id, activity_id=None, on_result=None):
        self._record("put_standing", on_result, patient_id, activity_id)

    def delete_patient(self, patient_id, on_result=None):
        self._record("delete", on_result, patient_id)

    def respond(self, result):
        """Simule la réponse du serveur pour la dernière requête émise."""
        self.last_result_handler(result)


class FakeWindow:
    """Faux ``self`` portant les vraies méthodes de MainWindow sous test."""

    def __init__(self, counter_id=3):
        self.counter_id = counter_id
        self.patient_id = None
        self.my_patient = None
        self.label_patient = FakeLabel()
        self.menu_calls = []
        self.logger = logging.getLogger("test.current_patient")
        self.api = FakeApi()
        self.btn_validate = None

        # Effets de bord isolés en mocks (boutons, notifications, sons).
        self.update_my_buttons = mock.MagicMock()
        self.close_please_validate_notification = mock.MagicMock()
        self.show_notification = mock.MagicMock()
        self.play_notification_sound = mock.MagicMock()

        for name in (
            "update_my_patient", "_on_invalid_patient", "handle_result",
            "handle_queue_result", "_patient_result_handler",
            "validate_my_patient", "call_web_function_validate",
            "on_action_validate", "on_action_wait_for", "on_action_delete",
            "_notify_network_error",
        ):
            setattr(self, name, types.MethodType(getattr(main.MainWindow, name), self))

    def _update_menu_actions(self, enable):
        self.menu_calls.append(enable)


def _patient(counter_id=3, patient_id=42):
    return {
        "counter_id": counter_id, "id": patient_id, "status": "calling",
        "language_code": "fr", "call_number": "A-42", "activity": "Ordonnance",
    }


# --- my_patient suit l'état affiché ------------------------------------------


def test_patient_valide_alimente_my_patient():
    w = FakeWindow()
    patient = _patient()
    w.update_my_patient(patient)
    assert w.patient_id == 42
    assert w.my_patient is patient


def test_plus_de_patient_vide_my_patient():
    w = FakeWindow()
    w.my_patient = _patient()
    w.update_my_patient(None)
    assert w.my_patient is None
    assert w.patient_id is None


def test_patient_autre_comptoir_preserve_my_patient():
    w = FakeWindow()
    w.my_patient = _patient()
    w.update_my_patient(_patient(counter_id=99))
    assert w.my_patient["id"] == 42


def test_patient_invalide_vide_my_patient(caplog):
    w = FakeWindow()
    w.my_patient = _patient()
    with caplog.at_level(logging.ERROR, logger="test.current_patient"):
        w.update_my_patient({"counter_id": 3, "id": 7})  # champs manquants
    assert w.my_patient is None
    assert w.patient_id is None


# --- flux complet : appel réussi puis validation ------------------------------


def test_appel_reussi_puis_validation_enchainent():
    """Régression F1 : après un 200 d'appel, « Valider » devait envoyer la
    requête — impossible tant que my_patient restait None."""
    w = FakeWindow()
    w.handle_result(NetResult(200, _patient()))
    assert w.my_patient["id"] == 42
    assert w.patient_id == 42

    w.call_web_function_validate()
    assert ("validate_current", 42) in w.api.calls


def test_204_reinitialise_aussi_les_boutons():
    """Un 204 (« plus de patient ») doit désactiver Valider/Pause et arrêter le
    minuteur, pas seulement vider le libellé."""
    w = FakeWindow()
    w.my_patient = _patient()
    w.patient_id = 42
    w.handle_result(NetResult(204))
    w.update_my_patient  # appelé via handle_result
    assert w.my_patient is None
    w.update_my_buttons.assert_called_with(None)


# --- actions sur un patient de la file : le patient courant survit ------------


def test_valider_patient_de_la_file_sans_patient_courant():
    """La validation d'un patient désigné dans la file ne doit pas être
    conditionnée à l'existence d'un patient courant au comptoir."""
    w = FakeWindow()  # my_patient = None, patient_id = None
    w.on_action_validate(7)
    assert ("validate_queued", 7) in w.api.calls


def test_201_dune_action_sur_la_file_preserve_le_patient_courant():
    """Répondre 201 à « remettre en attente » un patient de la file ne doit pas
    vider l'affichage du patient en cours au comptoir."""
    w = FakeWindow()
    w.update_my_patient(_patient())
    w.on_action_wait_for({"id": 9, "name": "Accueil"}, patient_id=7)
    assert ("put_standing", 7, 9) in w.api.calls

    w.api.respond(NetResult(201))
    assert w.patient_id == 42
    assert w.my_patient["id"] == 42
    assert w.label_patient.text.startswith("A-42")


def test_201_sur_le_patient_courant_vide_laffichage():
    """Remettre le patient COURANT en attente : le 201 doit vider le comptoir
    (comportement conservé, seul le routage du handler change)."""
    w = FakeWindow()
    w.update_my_patient(_patient())
    w.on_action_wait_for({"id": 9, "name": "Accueil"})  # patient_id=None -> courant
    assert w.api.calls[-1] == ("put_standing", 42, 9)

    w.api.respond(NetResult(201))
    assert w.patient_id is None
    assert w.my_patient is None
    assert w.label_patient.text == "Pas de patient"


def test_423_sur_la_file_ne_touche_pas_le_libelle_courant():
    """« Patient déjà pris » pour un patient de la file : sonnerie + message,
    mais le libellé du patient courant n'est pas écrasé."""
    w = FakeWindow()
    w.update_my_patient(_patient())
    w.on_action_wait_for({"id": 9, "name": "Accueil"}, patient_id=7)

    w.api.respond(NetResult(423))
    assert w.patient_id == 42
    assert w.label_patient.text.startswith("A-42")
    w.play_notification_sound.assert_called_once()
    w.show_notification.assert_called_once()
