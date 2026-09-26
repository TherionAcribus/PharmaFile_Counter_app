"""Interface compacte et contrôleur réseau de la messagerie App Comptoir."""

from __future__ import annotations

import html
import uuid
from datetime import datetime

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import (
    QComboBox, QDockWidget, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QSizePolicy, QStyle, QTextBrowser, QToolButton, QVBoxLayout,
    QWidget,
)


class MessageInput(QPlainTextEdit):
    sendRequested = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
                event.modifiers() & Qt.ShiftModifier):
            self.sendRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MessagingController:
    HEARTBEAT_MS = 30_000

    def __init__(self, window, settings_factory=QSettings):
        self.window = window
        self._settings_factory = settings_factory
        self.enabled = False
        self.staff_id = None
        self.staff_name = None
        self.conversations = []
        self.messages = []
        self.unread_total = 0
        self._pending_event = None
        self.button = None
        self.menu_action = None
        self.dock = None
        self.selector = None
        self.thread = None
        self.input = None
        self.send_button = None
        self.status_label = None
        self._icon_layout = None
        self._more_menu = None

        settings = self._settings_factory()
        value = settings.value("messaging_client_instance_id", "", type=str)
        try:
            self.client_instance_id = str(uuid.UUID(value))
        except (ValueError, TypeError, AttributeError):
            self.client_instance_id = str(uuid.uuid4())
            settings.setValue("messaging_client_instance_id", self.client_instance_id)

        self.heartbeat_timer = QTimer(window)
        self.heartbeat_timer.setInterval(self.HEARTBEAT_MS)
        self.heartbeat_timer.timeout.connect(self.heartbeat)

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if self.enabled == enabled:
            if enabled and self.staff_id:
                self.attach_to_interface()
            return
        self.enabled = enabled
        if not enabled:
            self.heartbeat_timer.stop()
            self._clear_runtime_data()
            self._destroy_ui()
            return
        if self.staff_id:
            self.attach_to_interface()
            self.heartbeat()

    def set_identity(self, staff_id, staff_name):
        self.staff_id = int(staff_id) if staff_id else None
        self.staff_name = staff_name or None
        if self.enabled and self.staff_id:
            self.attach_to_interface()
            self.heartbeat()

    def clear_identity(self, notify_server=True):
        if notify_server and self.enabled and self.staff_id:
            self.window.api.messaging_leave(self.client_instance_id)
        self.staff_id = None
        self.staff_name = None
        self.heartbeat_timer.stop()
        self._clear_runtime_data()
        self._destroy_ui()

    def leave_presence(self):
        """Retire la présence sans modifier l'UI avant confirmation du logout."""
        if self.enabled and self.staff_id:
            self.window.api.messaging_leave(self.client_instance_id)

    def shutdown(self):
        self.heartbeat_timer.stop()
        if self.enabled and self.staff_id:
            try:
                self.window.api.messaging_leave_blocking(self.client_instance_id)
            except Exception:
                pass

    def _clear_runtime_data(self):
        self.conversations = []
        self.messages = []
        self.unread_total = 0
        self._pending_event = None

    def attach_to_interface(self):
        if not self.enabled or not self.staff_id:
            return
        icon_layout = getattr(self.window, "icone_layout", None)
        more_menu = getattr(self.window, "more_menu", None)
        if icon_layout is None or more_menu is None:
            return

        # L'identité est reçue à chaque resynchronisation. Ne pas recréer
        # le bouton et l'action tant que l'interface courante n'a pas changé.
        # Un changement d'orientation reconstruit en revanche ces conteneurs.
        if self._icon_layout is icon_layout and self._more_menu is more_menu:
            try:
                if self.button is not None and self.menu_action is not None:
                    self.button.objectName()
                    self._ensure_dock()
                    return
            except RuntimeError:
                pass
        for obj_name in ("button", "menu_action"):
            obj = getattr(self, obj_name, None)
            if obj is not None:
                try:
                    obj.deleteLater()
                except RuntimeError:
                    pass
            setattr(self, obj_name, None)
        self._icon_layout = icon_layout
        self._more_menu = more_menu

        button = QToolButton(self.window)
        button.setText("💬")
        button.setToolTip("Ouvrir la messagerie")
        button.setAccessibleName("Messagerie interne")
        button.setMinimumSize(32, 32)
        button.clicked.connect(self.toggle)
        icon_layout.addWidget(button)
        self.button = button
        self._update_badge()

        action = QAction("Afficher/Masquer la messagerie", self.window)
        action.triggered.connect(self.toggle)
        more_menu.addAction(action)
        self.menu_action = action
        self._ensure_dock()

    def _ensure_dock(self):
        if self.dock is not None:
            try:
                self.dock.objectName()
                self._arrange_with_patient_list()
                return
            except RuntimeError:
                self.dock = None
        self.dock = QDockWidget("Messagerie", self.window)
        self.dock.setObjectName("messagingDock")
        self.dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.BottomDockWidgetArea)
        self.dock.setFeatures(
            QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable |
            QDockWidget.DockWidgetFloatable
        )
        # Ne pas imposer la largeur historique de 300 px à toute l'App. Le
        # contenu ci-dessous est compressible et laisse le panneau principal
        # décider de sa largeur minimale.
        self.dock.setMinimumWidth(0)
        self.dock.setMinimumHeight(180)
        self.dock.setAccessibleName("Panneau de messagerie interne")

        container = QWidget(self.dock)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.selector = QComboBox(container)
        self.selector.setEditable(True)
        self.selector.setMinimumContentsLength(8)
        self.selector.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon,
        )
        self.selector.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.selector.setAccessibleName("Conversation")
        self.selector.setToolTip("Rechercher ou choisir une conversation")
        self.selector.lineEdit().setPlaceholderText("Rechercher une personne…")
        completer = self.selector.completer()
        if completer is not None:
            completer.setFilterMode(Qt.MatchContains)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.selector.currentIndexChanged.connect(self._conversation_changed)
        layout.addWidget(self.selector)

        self.status_label = QLabel("", container)
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status_label.setAccessibleName("État de la conversation")
        layout.addWidget(self.status_label)

        self.thread = QTextBrowser(container)
        self.thread.setOpenExternalLinks(False)
        self.thread.setAccessibleName("Messages de la conversation")
        self.thread.setMinimumWidth(0)
        self.thread.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        layout.addWidget(self.thread, 1)

        composer = QHBoxLayout()
        self.input = MessageInput(container)
        self.input.setPlaceholderText("Écrire un message…")
        self.input.setAccessibleName("Texte du message")
        self.input.setMinimumWidth(0)
        self.input.setMaximumHeight(76)
        self.input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.input.setTabChangesFocus(True)
        self.input.textChanged.connect(self._update_composer)
        self.input.sendRequested.connect(self.send)
        composer.addWidget(self.input, 1)
        self.send_button = QPushButton(container)
        self.send_button.setIcon(
            self.window.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight),
        )
        self.send_button.setFixedSize(36, 36)
        self.send_button.setToolTip("Envoyer le message (Entrée)")
        self.send_button.setAccessibleName("Envoyer le message")
        self.send_button.clicked.connect(self.send)
        composer.addWidget(self.send_button)
        layout.addLayout(composer)

        self.dock.setWidget(container)
        settings = self._settings_factory()
        # Comme la liste des patients, la messagerie s'ouvre naturellement sous
        # la partie principale. L'utilisateur peut toujours la déplacer à
        # droite ; ce choix est ensuite conservé dans QSettings.
        area_name = settings.value("messaging_dock_area", "bottom", type=str)
        area = Qt.BottomDockWidgetArea if area_name == "bottom" else Qt.RightDockWidgetArea
        self.window.addDockWidget(area, self.dock)
        self._arrange_with_patient_list()
        self.dock.visibilityChanged.connect(self._visibility_changed)
        self.dock.dockLocationChanged.connect(self._dock_location_changed)
        self.dock.setVisible(settings.value("messaging_dock_visible", False, type=bool))
        self._update_composer()

    def _destroy_ui(self):
        for obj_name in ("button", "menu_action", "dock"):
            obj = getattr(self, obj_name, None)
            if obj is not None:
                try:
                    if obj_name == "dock":
                        self.window.removeDockWidget(obj)
                    obj.deleteLater()
                except RuntimeError:
                    pass
            setattr(self, obj_name, None)
        self.selector = self.thread = self.input = self.send_button = None
        self.status_label = None
        self._icon_layout = None
        self._more_menu = None

    def toggle(self):
        if not self.enabled or not self.staff_id:
            return
        self._ensure_dock()
        self.dock.setVisible(not self.dock.isVisible())
        if self.dock.isVisible():
            self.dock.raise_()
            self.request_state()

    def _visibility_changed(self, visible):
        self._settings_factory().setValue("messaging_dock_visible", bool(visible))
        if visible:
            self._arrange_with_patient_list()
            self.request_state()
            self._mark_visible_read()
        self._schedule_window_fit()

    def _dock_location_changed(self, area):
        self._settings_factory().setValue(
            "messaging_dock_area",
            "bottom" if area == Qt.BottomDockWidgetArea else "right",
        )

    def _arrange_with_patient_list(self):
        """Place la messagerie au-dessus de la liste lorsque les deux sont en bas."""
        patient_dock = getattr(self.window, "patient_list_dock", None)
        if self.dock is None or patient_dock is None:
            return
        if (self.window.dockWidgetArea(self.dock) == Qt.BottomDockWidgetArea
                and self.window.dockWidgetArea(patient_dock) == Qt.BottomDockWidgetArea):
            self.window.splitDockWidget(self.dock, patient_dock, Qt.Vertical)
            self.window.resizeDocks(
                [self.dock, patient_dock], [180, 110], Qt.Vertical,
            )

    def _schedule_window_fit(self):
        callback = getattr(self.window, "fit_window_to_content", None)
        if callable(callback):
            QTimer.singleShot(0, callback)

    def heartbeat(self):
        if not self.enabled or not self.staff_id:
            return
        self.window.api.messaging_presence(
            self.client_instance_id, on_result=self._handle_state_result)
        if not self.heartbeat_timer.isActive():
            self.heartbeat_timer.start()

    def request_state(self):
        if self.enabled and self.staff_id:
            self.window.api.messaging_state(on_result=self._handle_state_result)

    def _handle_state_result(self, result):
        if result.status == 200 and isinstance(result.data, dict):
            if result.data.get("enabled") is False:
                self.set_enabled(False)
                return
            previous_unread = self.unread_total
            self.conversations = result.data.get("conversations") or []
            self.unread_total = int(result.data.get("unread_total") or 0)
            self._populate_conversations()
            self._update_badge()
            if self.unread_total > previous_unread and self._pending_event:
                sender = self._pending_event.get("sender_name") or "un professionnel"
                self.window.show_notification({
                    "origin": "messaging",
                    "message": f"Nouveau message de {sender}.",
                }, internal=True)
            self._pending_event = None
            if self.dock is not None and self.dock.isVisible():
                self.request_messages()
            return
        if isinstance(result.data, dict) and result.data.get("error") == "messaging_disabled":
            self.set_enabled(False)

    def _populate_conversations(self):
        if self.selector is None:
            return
        selected = self.selector.currentData()
        self.selector.blockSignals(True)
        self.selector.clear()
        for conversation in self.conversations:
            name = conversation.get("name") or "Conversation"
            counter = conversation.get("counter_name")
            if conversation.get("kind") == "direct" and counter:
                name = f"{name} — Comptoir {counter}"
            if not conversation.get("online", True):
                name += " (hors ligne)"
            unread = int(conversation.get("unread_count") or 0)
            if unread:
                name += f"  [{unread}]"
            self.selector.addItem(name, conversation.get("key"))
        index = self.selector.findData(selected)
        self.selector.setCurrentIndex(index if index >= 0 else 0)
        self.selector.blockSignals(False)
        self._update_composer()

    def _selected(self):
        if self.selector is None:
            return None
        key = self.selector.currentData()
        return next((item for item in self.conversations if item.get("key") == key), None)

    def _conversation_changed(self, _index):
        self.messages = []
        if self.thread is not None:
            self.thread.clear()
        self._update_composer()
        self.request_messages()

    def request_messages(self):
        conversation = self._selected()
        if not conversation or not self.enabled:
            return
        kind = conversation.get("kind")
        peer_id = conversation.get("staff_id") if kind == "direct" else None
        self.window.api.messaging_messages(
            kind, peer_staff_id=peer_id, on_result=self._handle_messages_result)

    def _handle_messages_result(self, result):
        if result.status == 200 and isinstance(result.data, dict):
            self.messages = result.data.get("messages") or []
            self._render_messages()
            self._mark_visible_read()

    def _render_messages(self):
        if self.thread is None:
            return
        blocks = []
        for message in self.messages:
            sender = message.get("sender") or {}
            mine = sender.get("staff_id") == self.staff_id
            align = "right" if mine else "left"
            color = "#dbeafe" if mine else "#f1f3f5"
            if message.get("kind") == "broadcast":
                color = "#fff3cd"
            name = "Vous" if mine else html.escape(str(sender.get("name") or "Inconnu"))
            timestamp = self._format_time(message.get("created_at"))
            body = html.escape(str(message.get("body") or "")).replace("\n", "<br>")
            receipt = " · Lu" if mine and message.get("read_at") else ""
            blocks.append(
                f'<div style="text-align:{align}; margin:5px 0">'
                f'<span style="background:{color}; padding:6px">'
                f'<b>{name}</b> <small>{timestamp}{receipt}</small><br>{body}'
                f'</span></div>'
            )
        self.thread.setHtml("".join(blocks) or "<p>Aucun message aujourd’hui.</p>")
        self.thread.moveCursor(QTextCursor.End)

    @staticmethod
    def _format_time(value):
        if not value:
            return ""
        try:
            parsed = datetime.fromisoformat(value)
            return parsed.astimezone().strftime("%H:%M") if parsed.tzinfo else parsed.strftime("%H:%M")
        except (TypeError, ValueError):
            return ""

    def _mark_visible_read(self):
        if not self.dock or not self.dock.isVisible():
            return
        ids = [item.get("id") for item in self.messages if item.get("is_unread")]
        ids = [value for value in ids if value is not None]
        if ids:
            self.window.api.messaging_read(ids, on_result=lambda _result: self.request_state())

    def _update_composer(self):
        conversation = self._selected()
        can_send = bool(conversation and self.enabled and self.staff_id)
        status = ""
        if conversation and conversation.get("kind") == "direct" and not conversation.get("online"):
            can_send = False
            status = "Cette personne est actuellement hors ligne. L’historique reste consultable."
        if self.status_label is not None:
            self.status_label.setText(status)
        if self.input is not None:
            self.input.setEnabled(can_send)
        if self.send_button is not None:
            text = self.input.toPlainText().strip() if self.input is not None else ""
            self.send_button.setEnabled(can_send and bool(text))

    def send(self):
        conversation = self._selected()
        if not conversation or self.input is None:
            return
        body = self.input.toPlainText().strip()
        if not body:
            return
        if len(body) > 1000:
            QMessageBox.warning(self.window, "Message trop long", "Le message est limité à 1 000 caractères.")
            return
        kind = conversation.get("kind")
        if kind == "broadcast":
            online_count = sum(
                1 for item in self.conversations
                if item.get("kind") == "direct" and item.get("online")
            )
            answer = QMessageBox.question(
                self.window, "Message à toute l’équipe",
                f"Envoyer ce message aux {online_count} personne(s) actuellement connectée(s) ?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        recipient_id = conversation.get("staff_id") if kind == "direct" else None
        client_message_id = str(uuid.uuid4())
        self.send_button.setEnabled(False)
        self.window.api.messaging_send(
            client_message_id, kind, body, recipient_id,
            on_result=self._handle_send_result,
        )

    def _handle_send_result(self, result):
        if result.status in (200, 201):
            self.input.clear()
            self.request_state()
            self.request_messages()
            return
        error = result.data.get("error") if isinstance(result.data, dict) else None
        messages = {
            "recipient_offline": "Le destinataire n’est plus connecté.",
            "messaging_disabled": "La messagerie vient d’être désactivée.",
            "staff_not_connected": "Votre session n’est plus active sur ce comptoir.",
            "invalid_message": "Le message est vide ou invalide.",
        }
        QMessageBox.warning(
            self.window, "Message non envoyé",
            messages.get(error, "Le message n’a pas pu être envoyé."),
        )
        if error == "messaging_disabled":
            self.set_enabled(False)
        self._update_composer()

    def _update_badge(self):
        if self.button is None:
            return
        self.button.setText("💬" if not self.unread_total else f"💬 {self.unread_total}")
        self.button.setAccessibleDescription(
            f"{self.unread_total} message(s) non lu(s)" if self.unread_total
            else "Aucun message non lu"
        )

    # --- événements Socket.IO ---------------------------------------------

    def socket_changed(self, payload):
        if not self.enabled or not self.staff_id:
            return
        self._pending_event = payload if isinstance(payload, dict) else {}
        self.request_state()

    def socket_presence_changed(self):
        self.request_state()

    def socket_config_changed(self, enabled):
        self.set_enabled(enabled)

    def socket_connected(self):
        if self.enabled and self.staff_id:
            self.heartbeat()
