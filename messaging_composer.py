"""Saisie et envoi d'un message (messagerie de l'App comptoir).

Sorti de messaging.py : état du champ de saisie et de son texte d'invite,
option « message urgent » (un seul envoi), brouillon conservé par
conversation, signal « en train d'écrire », envoi (avec confirmation en deux
temps pour l'équipe entière) et traitement de la réponse du serveur.

Classe parente (mixin) de ``MessagingController`` ; les évènements reçus du
temps réel (``socket_*``) restent sur le contrôleur.
"""

from __future__ import annotations

import time
import uuid

from PySide6.QtCore import QTimer


class ComposerMixin:
    """Saisie et envoi (cf. docstring du module)."""

    def _update_composer(self):
        conversation = self._selected()
        can_send = bool(conversation and self.enabled and self.staff_id)
        status = ""
        if conversation and conversation.get("kind") == "direct" and not conversation.get("online"):
            # Le serveur accepte le message et le livrera à la prochaine
            # connexion du destinataire : l'envoi reste possible, on
            # l'annonce simplement.
            status = "Hors ligne — le message sera affiché à sa prochaine connexion."
        if self._online_only is not None:
            # La portée « connectés seulement » ne concerne que les messages
            # à toute l'équipe.
            self._online_only.setVisible(
                bool(conversation and conversation.get("kind") == "broadcast"))
        if self.status_label is not None:
            # Priorité : message transitoire (limite, consigne, erreur) >
            # « … écrit » en cours > statut calculé. Sans texte, le libellé
            # se replie : pas de ligne vide permanente.
            text = self._status_message or self._typing_text or status
            self.status_label.setText(text)
            self.status_label.setVisible(bool(text))
        if self.input is not None:
            self.input.setEnabled(can_send)
            self._refresh_input_hint()
        if self.quick_button is not None:
            # Pas de conversation : insérer une phrase n'aurait pas de sens.
            self.quick_button.setEnabled(can_send)
        if self.urgent_action is not None:
            # Même règle : pas de conversation, rien à envoyer.
            self.urgent_action.setEnabled(can_send)

    def _is_urgent(self):
        return bool(self.urgent_action is not None
                    and self.urgent_action.isChecked())

    def _apply_urgent_state(self, armed=None):
        """« Urgent » armé : bordure rouge sur le champ, invite dédiée et
        bouton « ⚡ » sur fond rouge — visible sans ouvrir le menu."""
        if armed is None:
            armed = self._is_urgent()
        try:
            if self.input is not None:
                self.input.setStyleSheet(
                    self.URGENT_INPUT_STYLE if armed else "")
                self.input.setAccessibleDescription(
                    "Le prochain message partira en urgent" if armed else "")
                self._refresh_input_hint()
            if self.quick_button is not None:
                self.quick_button.setStyleSheet(
                    self.URGENT_BUTTON_STYLE if armed else "")
                self.quick_button.setToolTip(
                    self.URGENT_BUTTON_TOOLTIP if armed
                    else self.QUICK_BUTTON_TOOLTIP)
        except RuntimeError:
            pass

    def _refresh_input_hint(self):
        """Invite et bulle d'aide du champ selon le fil (équipe : Entrée
        deux fois) et l'urgence armée."""
        if self.input is None:
            return
        conversation = self._selected()
        team = bool(conversation and conversation.get("kind") == "broadcast")
        urgent = self._is_urgent()
        if team:
            placeholder = (self.TEAM_URGENT_PLACEHOLDER if urgent
                           else self.TEAM_PLACEHOLDER)
        else:
            placeholder = (self.URGENT_PLACEHOLDER if urgent
                           else self.INPUT_PLACEHOLDER)
        self.input.setPlaceholderText(placeholder)
        self.input.setToolTip(
            self.TEAM_INPUT_TOOLTIP if team else self.INPUT_TOOLTIP)

    def _disarm_urgent(self):
        if self.urgent_action is not None:
            self.urgent_action.setChecked(False)

    def _store_draft(self):
        """Mémorise le texte saisi sous la clé de la conversation quittée —
        ou l'oublie si le brouillon était vide."""
        if self.input is None or self._current_key is None:
            return
        text = self.input.toPlainText()
        if text.strip():
            self._drafts[self._current_key] = text
        else:
            self._drafts.pop(self._current_key, None)

    def _restore_draft(self):
        if self.input is None:
            return
        self.input.setPlainText(self._drafts.get(self._current_key, ""))

    def _maybe_emit_typing(self):
        """Signale « en train d'écrire » au destinataire courant — au plus
        une fois par TYPING_EMIT_INTERVAL, et seulement s'il y a du texte :
        un champ vidé n'a plus rien à signaler."""
        if not self.enabled or not self.staff_id or self.input is None:
            return
        conversation = self._selected()
        if not conversation or not self.input.toPlainText().strip():
            return
        now = time.monotonic()
        if now - self._last_typing_emit < self.TYPING_EMIT_INTERVAL:
            return
        client = getattr(self.window, "socket_io_client", None)
        send = getattr(client, "send_typing", None)
        if not callable(send):
            return
        self._last_typing_emit = now
        payload = {
            "kind": conversation.get("kind"),
            "staff_id": self.staff_id,
            "name": self.staff_name,
            "counter_id": getattr(self.window, "counter_id", None),
            "peer_counter_id": conversation.get("counter_id"),
        }
        if conversation.get("kind") == "direct":
            payload["peer_staff_id"] = conversation.get("staff_id")
        send(payload)

    def send(self):
        conversation = self._selected()
        if not conversation or self.input is None:
            return
        # Un envoi à la fois : Entrée (sendRequested) peut se
        # déclencher une seconde fois pendant le vol — sans ce verrou le même
        # texte repartait avec un nouveau client_message_id = doublon serveur.
        if self._send_in_flight or not self.enabled or not self.staff_id:
            return
        body = self.input.toPlainText().strip()
        if not body:
            return
        if len(body) > 1000:
            # Pas de modale : le brouillon reste saisi, l'avertissement passe
            # par le libellé d'état du panneau.
            self._status_message = "Le message est limité à 1 000 caractères."
            self._update_composer()
            return
        kind = conversation.get("kind")
        # Portée serveur du broadcast : « all » (tous les équipiers actifs,
        # connectés ou non) ou « online » (case cochée). None pour un
        # message direct — le champ n'a alors pas de sens.
        scope = None
        if kind == "broadcast":
            scope = ("online" if self._online_only is not None
                     and self._online_only.isChecked() else "all")
            if not self._broadcast_confirm:
                if scope == "online":
                    count = sum(
                        1 for item in self.conversations
                        if item.get("kind") == "direct" and item.get("online")
                    )
                    target = f"{count} personne(s) connectée(s)"
                else:
                    count = sum(
                        1 for item in self.conversations
                        if item.get("kind") == "direct"
                    )
                    target = f"{count} équipier(s)"
                # Confirmation à deux temps, en ligne : un envoi à toute
                # l'équipe se confirme par un second Entrée/clic dans le
                # délai — pas par une boîte de dialogue qui vole le focus.
                self._broadcast_confirm = True
                QTimer.singleShot(5000, self._disarm_broadcast_confirm)
                self._status_message = (
                    "Appuyez de nouveau sur Entrée pour envoyer aux "
                    f"{target}.")
                self._update_composer()
                return
            # Second Entrée dans le délai : la confirmation est acquise.
            self._broadcast_confirm = False
            self._status_message = None
        recipient_id = conversation.get("staff_id") if kind == "direct" else None
        client_message_id = str(uuid.uuid4())
        self._send_in_flight = True
        self._sent_body = body
        # Conversation de l'envoi mémorisée : si l'utilisateur change d'onglet
        # pendant le vol, le succès saura quel brouillon purger.
        self._sent_key = conversation.get("key")
        self._update_composer()
        # « Urgent » armé : le message part urgent (notification persistante
        # chez le destinataire) ; l'option se décoche au succès.
        urgent = self._is_urgent()
        self.window.api.messaging_send(
            client_message_id, kind, body, recipient_id, scope=scope,
            urgent=urgent,
            on_result=self._network_callback(self._handle_send_result),
        )

    def _disarm_broadcast_confirm(self):
        """Fin du délai de confirmation « équipe » : l'envoi suivant
        redemandera accord, et la consigne affichée redevient un statut
        neutre."""
        if not self._broadcast_confirm:
            return
        self._broadcast_confirm = False
        self._status_message = None
        try:
            self._update_composer()
        except RuntimeError:
            # Le timer peut s'écouler après la destruction de l'interface
            # (fermeture de la fenêtre, panneau détruit) : rien à rafraîchir.
            pass

    def _handle_send_result(self, result):
        self._send_in_flight = False
        if result.status in (200, 201):
            # N'effacer que si le texte est toujours celui envoyé : un nouveau
            # brouillon saisi pendant l'attente de la réponse serait perdu.
            if (self.input is not None
                    and self.input.toPlainText().strip() == self._sent_body):
                self.input.clear()
            # Le brouillon de la conversation expédiée part avec elle, même
            # si l'utilisateur a changé d'onglet pendant le vol.
            self._drafts.pop(self._sent_key, None)
            # Répondre, c'est avoir lu : la remise en non lu tombe.
            self._keep_unread_keys.discard(self._sent_key)
            self._sent_key = None
            self._sent_body = None
            self._status_message = None
            self._broadcast_confirm = False
            # Un seul message urgent par armement : désarmé au succès.
            self._disarm_urgent()
            self._update_composer()
            self.request_state()
            self.request_messages()
            return
        self._sent_key = None
        self._sent_body = None
        error = result.data.get("error") if isinstance(result.data, dict) else None
        messages = {
            # Uniquement un broadcast « connectés seulement » sans personne
            # en ligne : les directs vers un équipier hors ligne passent.
            "recipient_offline": "Personne n’est connecté pour recevoir ce message.",
            "messaging_disabled": "La messagerie vient d’être désactivée.",
            "staff_not_connected": "Votre session n’est plus active sur ce comptoir.",
            "invalid_message": "Le message est vide ou invalide.",
        }
        text = messages.get(error, "Le message n’a pas pu être envoyé.")
        # Pas de QMessageBox : le détail reste dans le panneau, et la
        # notification flottante — non modale — alerte sans voler le focus.
        self._status_message = text
        self._update_composer()
        self.window.show_notification(
            {"origin": "messaging_error", "message": text}, internal=True)
        if error == "messaging_disabled":
            self.set_enabled(False)
