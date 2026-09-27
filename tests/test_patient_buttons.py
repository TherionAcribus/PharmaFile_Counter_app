"""État des boutons d'action patient (point 10.3).

Ces tests figent le comportement de l'ancien ``update_my_buttons`` — y compris
ses cas « on ne touche à rien » — désormais explicite au lieu d'être obtenu par
un ``except:`` nu.
"""

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from button_state import (  # noqa: E402
    CALLING_BUTTONS, IDLE_BUTTONS, MALFORMED, ONGOING_BUTTONS, OTHER_COUNTER,
    UNKNOWN_STATUS, resolve_patient_buttons,
)

COUNTER = 3


def _state(patient, counter_id=COUNTER):
    return resolve_patient_buttons(patient, counter_id)[0]


# --- décisions appliquées ---------------------------------------------------

@pytest.mark.parametrize("patient", [None, {}, [], "", 0, False])
def test_aucun_patient_desactive_tout(patient):
    """Suppression quotidienne de la file : plus aucune action, minuteur arrêté."""
    assert _state(patient) is IDLE_BUTTONS
    assert IDLE_BUTTONS.pause_enabled is False
    assert IDLE_BUTTONS.validate_enabled is False
    assert IDLE_BUTTONS.validate_alert is False
    assert IDLE_BUTTONS.start_call_timer is False


def test_comptoir_sans_patient_courant():
    assert _state({"counter_id": COUNTER, "id": None}) is IDLE_BUTTONS


def test_patient_en_appel():
    state = _state({"counter_id": COUNTER, "id": 7, "status": "calling"})
    assert state is CALLING_BUTTONS
    assert state.validate_enabled is True
    assert state.pause_enabled is False
    assert state.start_call_timer is True
    # L'alerte visuelle n'est PAS touchée pendant l'appel.
    assert state.validate_alert is None


def test_patient_pris_en_charge():
    state = _state({"counter_id": COUNTER, "id": 7, "status": "ongoing"})
    assert state is ONGOING_BUTTONS
    assert state.pause_enabled is True
    assert state.validate_enabled is False
    assert state.validate_alert is False
    assert state.start_call_timer is False


# --- cas « ne rien changer » (l'ancien except: nu) --------------------------

def test_autre_comptoir_ne_change_rien():
    decision, reason = resolve_patient_buttons(
        {"counter_id": COUNTER + 1, "id": 7, "status": "calling"}, COUNTER)
    assert decision is None and reason == OTHER_COUNTER


def test_comptoir_absent_ne_change_rien():
    decision, reason = resolve_patient_buttons({"id": 7, "status": "calling"}, COUNTER)
    assert decision is None and reason == OTHER_COUNTER


@pytest.mark.parametrize("status", ["standing", "done", None, "", 42])
def test_statut_inattendu_ne_change_rien(status):
    decision, reason = resolve_patient_buttons(
        {"counter_id": COUNTER, "id": 7, "status": status}, COUNTER)
    assert decision is None and reason == UNKNOWN_STATUS


def test_statut_manquant_ne_change_rien():
    decision, reason = resolve_patient_buttons({"counter_id": COUNTER, "id": 7}, COUNTER)
    assert decision is None and reason == UNKNOWN_STATUS


def test_identifiant_manquant_signale_une_donnee_malformee():
    decision, reason = resolve_patient_buttons({"counter_id": COUNTER, "status": "calling"}, COUNTER)
    assert decision is None and reason == MALFORMED


@pytest.mark.parametrize("patient", ["patient", 42, ["a"], object()])
def test_type_inattendu_signale_une_donnee_malformee(patient):
    decision, reason = resolve_patient_buttons(patient, COUNTER)
    assert decision is None and reason == MALFORMED


def test_aucun_motif_ne_contient_de_donnee_patient():
    """Les motifs partent dans les journaux : ils doivent rester génériques."""
    patient = {"counter_id": COUNTER, "id": 7, "status": "calling", "name": "Dupont"}
    _, reason = resolve_patient_buttons(patient, COUNTER)
    assert "Dupont" not in reason and "7" not in reason


# --- cliquet : plus aucun except: nu dans le code applicatif ---------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app_modules():
    for name in sorted(os.listdir(ROOT)):
        if name.endswith(".py"):
            yield name


def test_aucun_except_nu_dans_le_client():
    """Un ``except:`` nu attrape aussi KeyboardInterrupt/SystemExit et masque les
    fautes de programmation : il ne doit plus en exister dans le client."""
    faults = []
    for name in _app_modules():
        with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                faults.append(f"{name}:{node.lineno}")
    assert not faults, "except: nu (attrape tout, masque les bugs) : " + ", ".join(faults)


# --- application par MainWindow.update_my_buttons --------------------------
# Même approche que test_update_my_patient : on lie la vraie méthode à un faux
# ``self`` minimal (elle n'instancie aucun widget).

import logging  # noqa: E402
import types  # noqa: E402

import main  # noqa: E402


class FakeButton:
    def __init__(self):
        self.enabled = "sentinelle"
        self.tooltip = ""
        self.description = ""
        self.color_changed = False
        self._base_tooltip = "Action de base"

    def setEnabled(self, value):
        self.enabled = value

    def setToolTip(self, text):
        self.tooltip = text

    def toolTip(self):
        return self.tooltip

    def setAccessibleDescription(self, text):
        self.description = text


class FakeTimer:
    def __init__(self):
        self.calls = []

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")


class FakeWindow:
    _PATIENT_BUTTON_REASON_HINT = main.MainWindow._PATIENT_BUTTON_REASON_HINT

    def __init__(self, counter_id=COUNTER, with_buttons=True):
        self.counter_id = counter_id
        self.call_timer = FakeTimer()
        self.alerts = []
        self.logger = logging.getLogger("test.patient_buttons")
        if with_buttons:
            self.btn_pause = FakeButton()
            self.btn_validate = FakeButton()
        self.update_my_buttons = types.MethodType(main.MainWindow.update_my_buttons, self)
        self._apply_patient_buttons = types.MethodType(
            main.MainWindow._apply_patient_buttons, self)
        self._explain_patient_buttons = types.MethodType(
            main.MainWindow._explain_patient_buttons, self)
        # staticmethod : résolution directe sur la classe.
        self._safe_widget = main.MainWindow._safe_widget

    def _set_validate_alert(self, active):
        self.alerts.append(active)


def test_application_patient_en_appel():
    w = FakeWindow()
    w.update_my_buttons({"counter_id": COUNTER, "id": 7, "status": "calling"})
    assert w.btn_validate.enabled is True
    assert w.btn_pause.enabled is False
    assert w.call_timer.calls == ["start"]
    assert w.alerts == []          # alerte volontairement non touchée


def test_application_sans_patient():
    w = FakeWindow()
    w.update_my_buttons(None)
    assert w.btn_validate.enabled is False
    assert w.btn_pause.enabled is False
    assert w.call_timer.calls == ["stop"]
    assert w.alerts == [False]


def test_application_patient_pris_en_charge():
    w = FakeWindow()
    w.update_my_buttons({"counter_id": COUNTER, "id": 7, "status": "ongoing"})
    assert w.btn_pause.enabled is True
    assert w.btn_validate.enabled is False
    assert w.call_timer.calls == ["stop"]
    assert w.alerts == [False]


def test_autre_comptoir_laisse_les_boutons_intacts():
    w = FakeWindow()
    w.update_my_buttons({"counter_id": COUNTER + 1, "id": 7, "status": "calling"})
    assert w.btn_pause.enabled == "sentinelle"
    assert w.btn_validate.enabled == "sentinelle"
    assert w.call_timer.calls == []


def test_donnee_malformee_ne_crashe_pas_et_journalise(caplog):
    w = FakeWindow()
    with caplog.at_level(logging.WARNING, logger="test.patient_buttons"):
        w.update_my_buttons("pas un patient")
    assert w.call_timer.calls == []
    assert any(MALFORMED in r.getMessage() for r in caplog.records)


def test_journal_ne_contient_aucune_donnee_patient(caplog):
    w = FakeWindow()
    with caplog.at_level(logging.DEBUG, logger="test.patient_buttons"):
        w.update_my_buttons({"counter_id": COUNTER, "id": 7, "name": "Dupont"})
    assert not any("Dupont" in r.getMessage() for r in caplog.records)


def test_ecran_de_connexion_sans_boutons_ne_crashe_pas():
    """Après déconnexion, les boutons n'existent plus : l'ancien except: nu
    masquait l'AttributeError, on le gère désormais explicitement."""
    w = FakeWindow(with_buttons=False)
    w.update_my_buttons({"counter_id": COUNTER, "id": 7, "status": "calling"})
    assert w.call_timer.calls == []


# --- infobulles d'état (E4) : un bouton grisé explique pourquoi --------------

def test_infobulles_expliquent_l_indisponibilite_sans_patient():
    w = FakeWindow()
    w.update_my_buttons(None)
    assert "aucun patient au comptoir" in w.btn_validate.tooltip
    assert "aucun patient au comptoir" in w.btn_pause.tooltip


def test_infobulles_patient_en_appel():
    w = FakeWindow()
    w.update_my_buttons({"counter_id": COUNTER, "id": 7, "status": "calling"})
    assert w.btn_validate.tooltip == "Action de base"   # actif : infobulle normale
    assert "encore en cours d'appel" in w.btn_pause.tooltip


def test_infobulles_patient_pris_en_charge():
    w = FakeWindow()
    w.update_my_buttons({"counter_id": COUNTER, "id": 7, "status": "ongoing"})
    assert w.btn_pause.tooltip == "Action de base"
    assert "déjà pris en charge" in w.btn_validate.tooltip


def test_infobulle_alerte_valider_n_est_pas_ecrasee():
    # L'alerte « patient à valider » (fond rouge) porte sa propre infobulle :
    # les infobulles d'état ne doivent pas la remplacer tant qu'elle est posée.
    w = FakeWindow()
    w.btn_validate.color_changed = True
    w.btn_validate.tooltip = "Patient à valider — cliquez pour valider"
    w.update_my_buttons({"counter_id": COUNTER, "id": 7, "status": "calling"})
    assert w.btn_validate.tooltip == "Patient à valider — cliquez pour valider"


# --- indice « action en cours » sur le vrai bouton (E4) -----------------------

def test_busy_indique_action_en_cours_puis_restaure_infobulle():
    from buttons import DebounceButton
    b = DebounceButton("Suivant\nN")
    b.setToolTip("Suivant (raccourci : N)")
    b.set_busy(True)
    assert b.toolTip() == DebounceButton.BUSY_HINT
    assert b.accessibleDescription() == DebounceButton.BUSY_HINT
    b.set_busy(False)
    assert b.toolTip() == "Suivant (raccourci : N)"
    assert b.accessibleDescription() == ""


def test_busy_n_ecrase_pas_une_infobulle_remplacee_pendant_le_vol():
    # Un autre état (alerte, motif…) a posé son infobulle pendant la requête :
    # le déverrouillage ne doit pas la remplacer par l'ancienne.
    from buttons import DebounceButton
    b = DebounceButton("Valider\nV")
    b.setToolTip("Valider (raccourci : V)")
    b.set_busy(True)
    b.setToolTip("Patient à valider — cliquez pour valider")
    b.set_busy(False)
    assert b.toolTip() == "Patient à valider — cliquez pour valider"
