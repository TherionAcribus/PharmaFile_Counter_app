"""Échéance de validation automatique : comportement du client à l'échéance.

Le compte à rebours « Prolonger » est purement local ; la bascule
``calling`` -> ``ongoing`` est faite par la tâche planifiée serveur (toutes les
5 s) puis apprise via ``counter_state_changed`` + resync. Ces tests figent :

1. la convergence : quand la resync applique « ongoing », le clignotement,
   l'avertissement et le minuteur d'échéance disparaissent ;
2. l'auto-guérison : si l'évènement temps réel est perdu (coupure socket,
   conteneur « scheduler » sans file de messages), un compteur resté à 0
   redemande l'état autoritatif — d'abord après un court délai, puis
   périodiquement.
"""

import logging
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import call_alerts  # noqa: E402
import main  # noqa: E402
from call_alerts import CallAlertController  # noqa: E402


class _FakeSignal:
    def connect(self, slot):
        self.slot = slot


class FakeTimer:
    def __init__(self):
        self.calls = []
        self.active = False
        self.timeout = _FakeSignal()

    def setInterval(self, ms):
        self.interval = ms

    def start(self):
        self.calls.append("start")
        self.active = True

    def stop(self):
        self.calls.append("stop")
        self.active = False

    def isActive(self):
        return self.active


class FakeButton:
    def __init__(self):
        self.enabled = "sentinelle"
        self.text = "Pause"
        self.tooltip = ""
        self.color_changed = False
        self._base_label = "Pause\nAlt+P"
        self._base_tooltip = "Pause (raccourci : Alt+P)"
        self.pulses = []

    def setEnabled(self, value):
        self.enabled = value

    def setText(self, text):
        self.text = text

    def setToolTip(self, text):
        self.tooltip = text

    def toolTip(self):
        return self.tooltip

    def setAccessibleName(self, text):
        pass

    def setAccessibleDescription(self, text):
        pass

    def setAlertPulse(self, highlighted):
        self.pulses.append(highlighted)


COUNTER = 3


def _window():
    """Fenêtre minimale + vrai contrôleur d'alertes (minuteries simulées)."""
    w = types.SimpleNamespace(
        counter_id=COUNTER,
        patient_id=7,
        call_auto_validation={"enabled": True, "grace_seconds": 30},
        btn_pause=FakeButton(),
        btn_validate=FakeButton(),
        logger=logging.getLogger("test.call_deadline_expiry"),
        alerts=[],
        notifications=[],
        resync_requests=0,
    )
    for name in ("update_my_buttons", "_apply_patient_buttons", "_explain_patient_buttons"):
        setattr(w, name, types.MethodType(getattr(main.MainWindow, name), w))
    w._PATIENT_BUTTON_REASON_HINT = main.MainWindow._PATIENT_BUTTON_REASON_HINT
    w._safe_widget = main.MainWindow._safe_widget
    w.close_please_validate_notification = lambda: None
    w.show_notification = lambda data, **kw: w.notifications.append(data)
    w._request_resync = lambda: setattr(
        w, "resync_requests", w.resync_requests + 1)
    w.call_alerts = CallAlertController(w, reminder_seconds=60, make_timer=FakeTimer)
    # set_validate_alert touche les vrais boutons -> on instrumente :
    alerts = w.call_alerts

    def _alert(active):
        w.alerts.append(active)
        if active:
            w.btn_validate.color_changed = True
        else:
            alerts._stop_blink()
            w.btn_validate.color_changed = False
    alerts.set_validate_alert = _alert
    return w


def _patient_appele(echeance):
    return {
        "counter_id": COUNTER, "id": 7, "status": "calling",
        "call_auto_validate_at": echeance,
    }


def test_clignote_a_zero_puis_se_reinitialise_sur_resync():
    import time
    w = _window()
    # Patient en appel, échéance déjà dépassée (le serveur n'a pas encore
    # tranché) : le rendu doit afficher « Prolonger 0 s » et clignoter.
    w.my_patient = _patient_appele(time.time() - 1)
    w.call_alerts.deadline_tick()
    assert "Prolonger" in w.btn_pause.text
    assert w.call_alerts.blink_timer.active is True   # clignotement en cours

    # Le serveur a validé : la resync applique le statut ongoing.
    w.my_patient = {
        "counter_id": COUNTER, "id": 7, "status": "ongoing",
        "call_auto_validate_at": None,
    }
    w.update_my_buttons(w.my_patient)

    assert w.call_alerts.blink_timer.active is False, "clignotement toujours actif"
    assert w.btn_pause.text == "Pause\nAlt+P"
    assert w.btn_validate.color_changed is False
    assert w.call_alerts.deadline_timer.active is False


def test_echance_depassee_demande_une_resync_espacee(monkeypatch):
    """Compteur à 0 sans bascule serveur : l'évènement temps réel a pu être
    perdu. On re-vérifie l'état autoritatif après un délai (le job serveur
    tourne toutes les 5 s), puis périodiquement."""
    import time
    w = _window()
    now = [1000.0]
    monkeypatch.setattr(call_alerts.time, "monotonic", lambda: now[0])
    w.my_patient = _patient_appele(time.time() - 1)

    w.call_alerts.deadline_tick()          # premier passage à 0 : on arme le délai
    assert w.resync_requests == 0

    now[0] += call_alerts.EXPIRED_CALL_RECHECK_DELAY_S - 1
    w.call_alerts.deadline_tick()          # le job serveur a encore le temps
    assert w.resync_requests == 0

    now[0] += 2                        # délai écoulé -> vérification
    w.call_alerts.deadline_tick()
    assert w.resync_requests == 1

    w.call_alerts.deadline_tick()          # pas de rafale : la suivante attend
    assert w.resync_requests == 1

    now[0] += call_alerts.EXPIRED_CALL_RECHECK_PERIOD_S
    w.call_alerts.deadline_tick()          # toujours bloqué à 0 -> nouvelle passe
    assert w.resync_requests == 2


def test_pas_de_resync_avant_l_echeance():
    import time
    w = _window()
    w.my_patient = _patient_appele(time.time() + 60)
    w.call_alerts.deadline_tick()
    assert w.resync_requests == 0
    assert w.call_alerts._expired_resync_at is None
