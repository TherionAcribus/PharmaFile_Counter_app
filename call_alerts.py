"""Alertes liées au patient en cours d'appel.

Deux mécanismes, sortis de main.py (MainWindow) :

- le **rappel « pensez à valider »** : si le patient reste « en appel » plus
  longtemps que le délai réglé (préférence ``notification_after_calling``), le
  bouton Valider passe en alerte et une notification est affichée ;
- la **validation automatique** : quand le serveur l'a activée, un compte à
  rebours remplace le bouton Pause par « Prolonger » et fait clignoter Valider
  à l'approche de l'échéance. La bascule « calling » → « ongoing » est faite
  par le serveur ; si l'évènement temps réel se perd, le contrôleur redemande
  l'état autoritatif (auto-guérison d'un compteur bloqué à 0).

Le contrôleur possède les minuteries et l'état du compte à rebours ; il lit
le patient courant, la configuration serveur (``call_auto_validation``) et les
boutons sur la fenêtre, qui les reconstruit au gré des changements
d'interface.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer

import notification_texts
from accessibility import validate_alert_text
from button_state import resolve_call_auto_validation

#: Délai avant la première re-vérification d'état quand le compte à rebours
#: « Prolonger » affiche 0 : le job serveur de validation automatique tourne
#: toutes les 5 s — on lui laisse le temps de trancher avant de resynchroniser.
EXPIRED_CALL_RECHECK_DELAY_S = 6
#: Périodicité des re-vérifications tant que le serveur n'a pas confirmé la
#: validation automatique : couvre la perte d'un évènement temps réel
#: (coupure socket, conteneur « scheduler » sans file de messages partagée).
EXPIRED_CALL_RECHECK_PERIOD_S = 15

DEADLINE_TICK_MS = 1000
BLINK_INTERVAL_MS = 450


class CallAlertController:
    """Rappel de validation et compte à rebours de la validation automatique."""

    def __init__(self, window, reminder_seconds, make_timer=None):
        self.window = window
        if make_timer is None:
            parent = window if isinstance(window, QObject) else None
            def make_timer():
                return QTimer(parent)
        #: Rappel « pensez à valider » (patient resté en appel).
        self.reminder_timer = make_timer()
        self.reminder_timer.setInterval(reminder_seconds * 1000)
        self.reminder_timer.timeout.connect(self.reminder_expired)
        #: Tic d'une seconde du compte à rebours de validation automatique.
        self.deadline_timer = make_timer()
        self.deadline_timer.setInterval(DEADLINE_TICK_MS)
        self.deadline_timer.timeout.connect(self.deadline_tick)
        self.blink_timer = make_timer()
        self.blink_timer.setInterval(BLINK_INTERVAL_MS)
        self.blink_timer.timeout.connect(self._toggle_blink)

        #: Vrai tant que « Prolonger » remplace « Pause » (le bouton Pause
        #: prolonge alors l'appel au lieu de mettre en pause).
        self.warning_active = False
        self._warning_key = None
        self._expired_resync_at = None
        self._blink_highlighted = False

    # --- pilotage par la fenêtre ----------------------------------------------

    def set_reminder_delay(self, seconds):
        self.reminder_timer.setInterval(seconds * 1000)

    def sync(self):
        """Patient en appel : démarre le rappel, ou le compte à rebours si la
        validation automatique est armée pour ce patient."""
        state = self._state()
        if state.armed:
            self.reminder_timer.stop()
            if not self.deadline_timer.isActive():
                self.deadline_timer.start()
            self._render(state)
            return
        self.deadline_timer.stop()
        self.clear_extension_warning()
        if not self.reminder_timer.isActive():
            self.reminder_timer.start()

    def stop_call(self):
        """Plus de patient en appel : plus de rappel ni de compte à rebours."""
        self.reminder_timer.stop()
        self.deadline_timer.stop()
        self.clear_extension_warning()

    def shutdown(self):
        self.reminder_timer.stop()
        self.deadline_timer.stop()
        self.blink_timer.stop()

    # --- rappel « pensez à valider » ----------------------------------------------

    def reminder_expired(self):
        window = self.window
        if hasattr(window, "my_patient"):
            state = self._state()
            if state.armed or not isinstance(window.my_patient, dict):
                return
            if window.my_patient.get("status") != "calling":
                return
        self.set_validate_alert(True)
        window.show_notification(
            {"origin": "please_validate",
             "message": notification_texts.render_for(
                 window, "please_validate",
                 N=notification_texts.current_call_number(window))},
            internal=True, patient_id=window.patient_id)

    def set_validate_alert(self, active):
        """Marque (ou démarque) le bouton Valider comme « patient à valider ».

        Accessibilité (point 28) : l'état ne repose pas uniquement sur le fond
        rouge. Le libellé reçoit aussi un pictogramme d'alerte, et le nom
        accessible et l'infobulle changent, pour rester compréhensible en
        niveaux de gris et pour les lecteurs d'écran."""
        button = getattr(self.window, "btn_validate", None)
        if button is None:
            return
        base_label = getattr(button, "_base_label", button.text())
        if active:
            button.setRed()
            button.setText(validate_alert_text(base_label))
            button.setAccessibleName("Patient à valider")
            button.setToolTip("Patient à valider — cliquez pour valider")
        else:
            self._stop_blink()
            button.resetColor()
            button.setText(base_label)
            button.setAccessibleName("Valider")
            button.setToolTip(getattr(button, "_base_tooltip", None) or "Valider")

    # --- validation automatique ---------------------------------------------------

    def _state(self):
        return resolve_call_auto_validation(
            getattr(self.window, "my_patient", None),
            getattr(self.window, "call_auto_validation", {"enabled": False}))

    def deadline_tick(self):
        state = self._state()
        if not state.armed:
            self._expired_resync_at = None
            self.deadline_timer.stop()
            self.clear_extension_warning()
            return
        self._render(state)
        self._maybe_resync_expired(state)

    def _maybe_resync_expired(self, state):
        """Auto-guérison : l'échéance affiche 0 mais le serveur n'a pas encore
        confirmé la validation — l'évènement temps réel a pu être perdu. On
        redemande alors l'état autoritatif, d'abord après un court délai (le
        job serveur tourne toutes les 5 s) puis périodiquement tant que le
        compteur reste bloqué à 0."""
        if state.remaining_seconds > 0:
            self._expired_resync_at = None
            return
        now = time.monotonic()
        if self._expired_resync_at is None:
            self._expired_resync_at = now + EXPIRED_CALL_RECHECK_DELAY_S
            return
        if now < self._expired_resync_at:
            return
        self._expired_resync_at = now + EXPIRED_CALL_RECHECK_PERIOD_S
        self.window._request_resync()

    def _render(self, state):
        if not state.warning_active:
            self.clear_extension_warning()
            return
        window = self.window
        self.warning_active = True
        self.set_validate_alert(True)
        self._start_blink()
        self._set_extend_button(state.remaining_seconds)
        patient = window.my_patient
        warning_key = (
            window.patient_id,
            patient.get("call_auto_validate_at") if isinstance(patient, dict) else None,
        )
        if warning_key != self._warning_key:
            self._warning_key = warning_key
            window.show_notification({
                "origin": "please_validate",
                "message": notification_texts.render_for(
                    window, "auto_release_warning", S=state.remaining_seconds,
                    N=notification_texts.current_call_number(window)),
            }, internal=True, patient_id=window.patient_id)

    def _set_extend_button(self, remaining_seconds):
        button = getattr(self.window, "btn_pause", None)
        if button is None:
            return
        button.setText(f"↻ Prolonger\n{remaining_seconds} s")
        button.setAccessibleName("Prolonger l'appel")
        hint = (
            "Conserver le patient sur l'écran d'appel et relancer le délai "
            f"({remaining_seconds} s restantes)"
        )
        button.setToolTip(hint)
        button.setAccessibleDescription(hint)
        button.setEnabled(True)

    def clear_extension_warning(self):
        """Rend au bouton Pause son rôle et retire l'alerte de validation."""
        was_active = self.warning_active
        self.warning_active = False
        self._warning_key = None
        self._stop_blink()
        button = getattr(self.window, "btn_pause", None)
        if button is not None:
            button.setText(getattr(button, "_base_label", "Pause"))
            button.setAccessibleName("Pause")
            tooltip = getattr(button, "_base_tooltip", None) or "Pause"
            button.setToolTip(tooltip)
            button.setAccessibleDescription(tooltip)
        if was_active:
            self.set_validate_alert(False)
            self.window.close_please_validate_notification()

    # --- clignotement du bouton Valider -----------------------------------------------

    def _start_blink(self):
        if not self.blink_timer.isActive():
            self.blink_timer.start()

    def _toggle_blink(self):
        self._blink_highlighted = not self._blink_highlighted
        button = getattr(self.window, "btn_validate", None)
        if button is not None:
            button.setAlertPulse(self._blink_highlighted)

    def _stop_blink(self):
        self.blink_timer.stop()
        self._blink_highlighted = False
        button = getattr(self.window, "btn_validate", None)
        if button is not None:
            button.setAlertPulse(False)
