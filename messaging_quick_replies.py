"""Phrases rapides de la messagerie (menu « ⚡ »).

Sorti de messaging.py : phrases d'équipe (réglées dans l'administration) et
phrases personnelles de l'équipier, construction du menu, fenêtre « Mes
phrases rapides » et enregistrement sur le serveur, insertion d'une phrase
dans le champ de saisie (jamais d'envoi direct).

Classe parente (mixin) de ``MessagingController``.
"""

from __future__ import annotations

from PySide6.QtGui import QAction

from quick_replies_dialog import DEFAULT_MAX_COUNT, DEFAULT_MAX_LENGTH, QuickRepliesDialog


class QuickRepliesMixin:
    """Phrases rapides (cf. docstring du module)."""

    @staticmethod
    def _phrase_list(value):
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str) and item.strip()]

    def _apply_quick_replies(self, payload):
        """Bloc ``quick_replies`` de l'état serveur. Absent (serveur
        ancien) : phrases d'origine, gestion personnelle masquée."""
        if isinstance(payload, dict):
            team = self._phrase_list(payload.get("team"))
            personal = self._phrase_list(payload.get("personal"))
            supported = True
            try:
                max_count = int(payload.get("max_personal") or DEFAULT_MAX_COUNT)
                max_length = int(payload.get("max_length") or DEFAULT_MAX_LENGTH)
            except (TypeError, ValueError):
                max_count, max_length = DEFAULT_MAX_COUNT, DEFAULT_MAX_LENGTH
        else:
            team, personal, supported = list(self.QUICK_REPLIES), [], False
            max_count, max_length = DEFAULT_MAX_COUNT, DEFAULT_MAX_LENGTH
        changed = (team != self.team_replies
                   or personal != self.personal_replies
                   or supported != self.quick_replies_supported)
        self.team_replies = team
        self.personal_replies = personal
        self.quick_replies_supported = supported
        self.quick_max_count = max(1, max_count)
        self.quick_max_length = max(1, max_length)
        if changed:
            self._rebuild_quick_menu()

    def _rebuild_quick_menu(self):
        """Remplace les phrases en tête du menu « ⚡ » ; les actions fixes
        (gestion, retrait du dernier message) restent en place — l'action de
        retrait porte l'état de sa confirmation en deux temps."""
        menu = self._quick_menu
        if menu is None:
            return
        for action in self._quick_phrase_actions:
            menu.removeAction(action)
            action.deleteLater()
        self._quick_phrase_actions = []
        anchor = self._quick_manage_action
        for group in (self.team_replies, self.personal_replies):
            if not group:
                continue
            for phrase in group:
                action = QAction(phrase, menu)
                action.triggered.connect(
                    lambda _checked=False, _text=phrase:
                        self._insert_quick_reply(_text))
                menu.insertAction(anchor, action)
                self._quick_phrase_actions.append(action)
            # Séparateur après chaque groupe : équipe | personnelles | gestion.
            self._quick_phrase_actions.append(menu.insertSeparator(anchor))
        if anchor is not None:
            anchor.setVisible(self.quick_replies_supported)
            anchor.setText("Gérer mes phrases rapides…" if self.personal_replies
                           else "Ajouter mes phrases rapides…")

    def open_quick_replies_dialog(self):
        """Fenêtre « Mes phrases rapides » ; l'enregistrement part au
        serveur à la validation (la fenêtre ne fait aucun appel réseau)."""
        if not (self.enabled and self.staff_id and self.quick_replies_supported):
            return
        phrases = (self._unsaved_personal if self._unsaved_personal is not None
                   else self.personal_replies)
        dialog = QuickRepliesDialog(
            phrases, team_phrases=self.team_replies,
            max_count=self.quick_max_count, max_length=self.quick_max_length,
            parent=self.dock if self.dock is not None else self.window)
        if dialog.exec() != QuickRepliesDialog.DialogCode.Accepted:
            return
        self.save_personal_quick_replies(dialog.phrases())

    def save_personal_quick_replies(self, phrases):
        phrases = list(phrases)
        self._unsaved_personal = phrases
        handle = self.window.api.messaging_save_quick_replies(
            phrases,
            on_result=self._network_callback(self._handle_quick_replies_saved))
        if handle is None:
            self._show_transient_status(
                "Enregistrement des phrases déjà en cours…")

    def _handle_quick_replies_saved(self, result):
        data = result.data if isinstance(result.data, dict) else {}
        if result.status == 200 and "personal" in data:
            self._unsaved_personal = None
            self.personal_replies = self._phrase_list(data.get("personal"))
            if "team" in data:
                self.team_replies = self._phrase_list(data.get("team"))
            self._rebuild_quick_menu()
            self._show_transient_status("Vos phrases rapides sont enregistrées.")
            return
        message = data.get("message")
        self._show_transient_status(
            message if isinstance(message, str) and message
            else "Vos phrases rapides n'ont pas pu être enregistrées. "
                 "Rouvrez « ⚡ » pour réessayer.")

    def _insert_quick_reply(self, text):
        """Réponse rapide : insérée à la position du curseur (insertion, pas
        de remplacement), jamais envoyée directement — l'équipier relit,
        modifie si besoin puis valide par Entrée."""
        if self.input is None:
            return
        self.input.insertPlainText(text)
        self.input.setFocus()
