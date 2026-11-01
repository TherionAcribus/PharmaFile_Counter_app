"""Retrait d'un message envoyé par erreur (messagerie de l'App comptoir).

Sorti de messaging.py : choix du message retirable (mes messages récents,
dans la fenêtre de retrait du serveur), libellés et état de l'action du menu
« ⚡ » et du clic droit sur une bulle, confirmation en deux temps, requête et
traitement de la réponse.

Classe parente (mixin) de ``MessagingController`` : ces méthodes lisent le
fil, l'identité et les widgets du contrôleur. Constantes utilisées :
``DELETE_WINDOW_SECONDS``, ``DELETE_ACTION_TEXT``, ``SNIPPET_LENGTH``.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from messaging_render import MESSAGE_ANCHOR as _MESSAGE_ANCHOR


class DeleteMessagesMixin:
    """Retrait de mes messages récents (cf. docstring du module)."""

    def _delete_remaining(self, message):
        """Secondes restantes pour retirer ``message``, ou None s'il n'est
        pas retirable : pas de moi, sans identifiant ou date illisible.
        Négatif (ou nul) = délai dépassé. Le serveur reste seul juge
        (DELETE_WINDOW_SECONDS, si les horloges divergent)."""
        if not self.enabled or not isinstance(message, dict):
            return None
        if message.get("id") is None or message.get("deleted"):
            return None
        if (message.get("sender") or {}).get("staff_id") != self.staff_id:
            return None
        try:
            created = datetime.fromisoformat(
                str(message.get("created_at") or ""))
        except (TypeError, ValueError):
            return None
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - created).total_seconds()
        return self.DELETE_WINDOW_SECONDS - age

    def _is_deletable(self, message):
        remaining = self._delete_remaining(message)
        return remaining is not None and remaining > 0

    def _my_last_message(self):
        """Mon message le plus récent du fil affiché, retirable ou non —
        même si d'autres ont répondu depuis."""
        for message in reversed(self.messages):
            if message.get("deleted"):
                continue  # déjà retiré : on vise le message d'avant
            if (message.get("sender") or {}).get("staff_id") == self.staff_id:
                return message
        return None

    def _my_last_deletable(self):
        """Mon dernier message s'il est encore retirable (le serveur
        l'accepte même après une réponse : seuls l'expéditeur et le délai
        comptent)."""
        message = self._my_last_message()
        return message if self._is_deletable(message) else None

    def _can_delete_last_message(self):
        return self._my_last_deletable() is not None

    @classmethod
    def _snippet(cls, body):
        """Extrait d'un message pour un libellé de menu : une seule ligne,
        tronqué, « & » doublé (sinon pris pour un raccourci clavier)."""
        text = " ".join(str(body or "").split())
        if len(text) > cls.SNIPPET_LENGTH:
            text = text[:cls.SNIPPET_LENGTH - 1].rstrip() + "…"
        return text.replace("&", "&&")

    def _delete_action_state(self):
        """(libellé, actif, bulle d'aide) de l'action de retrait du menu ⚡ :
        le message visé et le temps restant, ou la raison du grisé."""
        if self._delete_confirm:
            target = next((m for m in self.messages
                           if m.get("id") == self._delete_target_id), None)
            body = str((target or {}).get("body") or "")
            return (f"⚠ Confirmer le retrait de « {self._snippet(body)} »",
                    True, body)
        message = self._my_last_message()
        if message is None:
            return (f"{self.DELETE_ACTION_TEXT} (aucun dans ce fil)",
                    False, "")
        body = str(message.get("body") or "")
        remaining = self._delete_remaining(message)
        if remaining is None or remaining <= 0:
            return (f"{self.DELETE_ACTION_TEXT} (impossible après 15 min)",
                    False, body)
        minutes = max(1, math.ceil(remaining / 60))
        return (f"Retirer « {self._snippet(body)} » (encore {minutes} min)",
                True, body)

    def _refresh_delete_action(self):
        if self._delete_action is None:
            return
        text, enabled, tooltip = self._delete_action_state()
        self._delete_action.setText(text)
        self._delete_action.setEnabled(enabled)
        self._delete_action.setToolTip(tooltip or text)

    def _request_delete(self):
        """« Retirer mon dernier message » (menu ⚡) : confirmation à deux
        temps dans le menu lui-même. Un menu se referme au clic sur une
        action : au premier clic on le rouvre donc aussitôt avec l'action
        renommée « ⚠ Confirmer le retrait » — le second clic, à la même place
        et visible, déclenche la requête. Le message visé est figé au premier
        clic. Passé le délai (ou changement de fil), l'intention retombe."""
        if not self._delete_confirm:
            target = self._my_last_deletable()
            if target is None:
                return
            self._delete_confirm = True
            self._delete_target_id = target.get("id")
            self._refresh_delete_action()
            QTimer.singleShot(5000, self._disarm_delete_confirm)
            # Le menu vient de se refermer sur ce premier clic : on le
            # rouvre pour que la confirmation reste sous le doigt.
            QTimer.singleShot(0, self._reopen_quick_menu)
            return
        target_id = self._delete_target_id
        self._disarm_delete_confirm()
        target = next((m for m in self.messages if m.get("id") == target_id),
                      None)
        if target is None or not self._is_deletable(target):
            # Délai écoulé pendant la confirmation, ou fil rechargé sans lui.
            self._status_message = "Délai dépassé (15 min)."
            self._update_composer()
            return
        self._delete_message(target_id)

    def _delete_message(self, message_id):
        self.window.api.messaging_delete(
            message_id,
            on_result=self._network_callback(self._handle_delete_result))

    def _message_in_block(self, block):
        """Message affiché dans un bloc du fil (une bulle = un bloc), via
        l'ancre posée au rendu ; None pour un repère ou un bloc vide."""
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            for name in fragment.charFormat().anchorNames():
                if name.startswith(_MESSAGE_ANCHOR):
                    wanted = name[len(_MESSAGE_ANCHOR):]
                    return next((m for m in self.messages
                                 if str(m.get("id")) == wanted), None)
            it += 1
        return None

    def _message_at(self, position):
        if self.thread is None:
            return None
        return self._message_in_block(
            self.thread.cursorForPosition(position).block())

    def _extend_thread_menu(self, menu, position):
        self._add_delete_entries(menu, self._message_at(position))

    def _add_delete_entries(self, menu, message):
        """Clic droit sur une de MES bulles : « Retirer ce message » en tête
        du menu, avec le temps restant. La confirmation est un sous-menu
        (survol puis clic) : deux gestes, sans boîte de dialogue. Délai
        dépassé : l'entrée reste visible mais grisée, avec la raison."""
        remaining = self._delete_remaining(message)
        if remaining is None:
            return
        actions = menu.actions()
        first = actions[0] if actions else None
        if remaining <= 0:
            action = QAction(
                "Retirer ce message (impossible après 15 min)", menu)
            action.setEnabled(False)
            menu.insertAction(first, action)
        else:
            minutes = max(1, math.ceil(remaining / 60))
            submenu = QMenu(
                f"Retirer ce message (encore {minutes} min)", menu)
            confirm = submenu.addAction("⚠ Confirmer le retrait")
            message_id = message.get("id")
            confirm.triggered.connect(
                lambda _checked=False, _id=message_id:
                    self._delete_message(_id))
            menu.insertMenu(first, submenu)
        if first is not None:
            menu.insertSeparator(first)

    def _reopen_quick_menu(self):
        try:
            if (self._delete_confirm and self.quick_button is not None
                    and self.quick_button.isEnabled()):
                self.quick_button.showMenu()
        except RuntimeError:
            # Le délai nul peut s'écouler après la destruction du panneau.
            pass

    def _disarm_delete_confirm(self):
        """Fin du délai (ou fil quitté) : l'intention « retirer » retombe
        et l'action retrouve son libellé."""
        if not self._delete_confirm:
            return
        self._delete_confirm = False
        self._delete_target_id = None
        try:
            self._refresh_delete_action()
        except RuntimeError:
            # Le timer peut s'écouler après la destruction de l'interface.
            pass

    def _handle_delete_result(self, result):
        if (result.status == 200 and isinstance(result.data, dict)
                and result.data.get("deleted")):
            self._status_message = None
            self._update_composer()
            # Le message a disparu partout : état et fil se rechargent chez
            # moi, le signal « messaging_changed » fait de même chez le
            # destinataire côté serveur.
            self.request_state()
            self.request_messages()
            return
        error = (result.data.get("error")
                 if isinstance(result.data, dict) else None)
        self._status_message = (
            "Délai dépassé (15 min)." if error == "delete_window_expired"
            else "Le message n'a pas pu être retiré.")
        self._update_composer()
