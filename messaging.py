"""Interface compacte et contrôleur réseau de la messagerie App Comptoir."""

from __future__ import annotations

import html
import uuid
from datetime import datetime

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDockWidget, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QSizePolicy, QStyle, QTextBrowser, QToolButton, QVBoxLayout,
    QWidget,
)

import settings_schema
from dock_arrangement import DockArranger, dock_in_front, toggle_dock


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
        # L'état de référence des non-lus n'est connu qu'après la première
        # réponse d'état de la session : c'est là (et là seulement) qu'on
        # annonce « vous avez N message(s) non lu(s) », jamais à chaque
        # heartbeat. Réarmé par _clear_runtime_data (nouvel agent / reprise).
        self._baseline_known = False
        # Génération d'identité : incrémentée à chaque purge d'agent. Les
        # requêtes réseau en vol ne sont pas annulables : chaque on_result
        # capture la génération à l'émission et n'applique sa réponse que si
        # elle est encore courante (cf. _network_callback).
        self._identity_generation = 0
        self.button = None
        self.menu_action = None
        self.dock = None
        self.selector = None
        self.thread = None
        self.input = None
        self.send_button = None
        self.status_label = None
        self._online_only = None
        self._icon_layout = None
        self._more_menu = None
        # Un envoi en vol : Entrée et le bouton peuvent se déclencher pendant
        # la requête (Entrée n'est pas debouncé) — sans verrou, le même message
        # partait en double. _sent_body mémorise le corps envoyé pour ne pas
        # effacer un nouveau brouillon saisi pendant l'attente.
        self._send_in_flight = False
        self._sent_body = None
        # Rafraîchissement d'état demandé pendant qu'une lecture identique
        # était déjà en vol (refusée comme doublon par la couche réseau) : la
        # réponse de la requête en cours déclenchera la relance — cf.
        # request_state/_handle_state_result.
        self._state_pending = False
        # Message transitoire affiché dans le libellé d'état du compositeur
        # (limite de longueur, consigne de confirmation « équipe », erreur
        # d'envoi) : remplace le statut calculé tant qu'il n'est pas effacé.
        # Jamais de QMessageBox ici : au comptoir, une modale volerait le
        # focus en pleine saisie.
        self._status_message = None
        # Confirmation « envoyer à toute l'équipe » EN LIGNE, à deux temps :
        # premier Entrée/clic arme ce drapeau (et affiche la consigne), le
        # second — dans le délai — envoie réellement.
        self._broadcast_confirm = False
        # Pendant nos propres déplacements/suppressions du dock : les signaux
        # de visibilité émis alors ne sont pas des choix utilisateur à
        # persister (cf. aussi arranger.busy).
        self._arranging = False
        # Disposition file des patients + messagerie (empilés/onglets) :
        # appliquée à la création, mémorisée après un glisser-déposer.
        self.arranger = DockArranger(window, settings_factory)

        # Identité de présence de CE processus : un UUID neuf à chaque
        # démarrage, volontairement NON persisté. Le stocker dans QSettings
        # (clé « messaging_client_instance_id ») faisait que plusieurs postes
        # partageant le même profil Windows héritaient du MÊME identifiant et
        # s'écrasaient mutuellement leur ligne de présence (même clé primaire
        # côté serveur) — les équipiers « disparaissaient » de la liste. Le
        # TTL serveur (90 s) purge les restes après un crash ; leave/shutdown
        # suppriment la ligne du processus proprement.
        self.client_instance_id = str(uuid.uuid4())

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
        new_id = int(staff_id) if staff_id else None
        if new_id != self.staff_id:
            # Changement d'agent sans déconnexion préalable (agent relu
            # différent en resynchronisation) : les données de l'agent
            # précédent — fil, conversations, badge, brouillon, envoi en
            # vol — sont purgées immédiatement, AVANT toute nouvelle
            # requête, et ses réponses encore en vol seront ignorées
            # (génération incrémentée dans _clear_runtime_data).
            self._clear_runtime_data()
            self._clear_displayed_data()
        self.staff_id = new_id
        self.staff_name = staff_name or None
        if self.enabled and self.staff_id:
            self.attach_to_interface()
            # update_staff_label est appelé à chaque reconstruction
            # d'interface : si le heartbeat périodique tourne déjà, il
            # suffit — pas besoin d'une requête de présence supplémentaire.
            if not self.heartbeat_timer.isActive():
                self.heartbeat()
        else:
            self.heartbeat_timer.stop()

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
        # Fermeture de l'App : la disposition des panneaux (onglet devant,
        # panneau détaché…) est retrouvée au prochain démarrage.
        self.arranger.save_state()
        if self.enabled and self.staff_id:
            try:
                self.window.api.messaging_leave_blocking(self.client_instance_id)
            except Exception:
                pass

    def _clear_runtime_data(self):
        # Nouvelle génération : toute réponse d'une requête partie sous
        # l'identité précédente devient caduque.
        self._identity_generation += 1
        self.conversations = []
        self.messages = []
        self.unread_total = 0
        self._pending_event = None
        # Si un envoi était encore en vol à la déconnexion, sa réponse ne doit
        # pas verrouiller la session suivante : le verrou est logique, pas lié
        # au cycle de vie de la requête.
        self._send_in_flight = False
        self._sent_body = None
        # Consignes/erreurs et confirmations en cours appartiennent à la
        # session précédente ; une relance d'état en attente n'a plus de sens
        # pour le nouvel agent (son heartbeat rafraîchit déjà l'état).
        self._state_pending = False
        self._status_message = None
        self._broadcast_confirm = False
        # La notification « non-lus à la connexion » pourra se rejouer pour la
        # prochaine session (nouvel agent, réactivation) : sa référence repart
        # de zéro.
        self._baseline_known = False

    def _clear_displayed_data(self):
        """Vide immédiatement ce qui est affiché quand l'agent change alors
        que le panneau existe déjà : le fil et le brouillon de l'agent
        précédent ne doivent pas rester visibles le temps des premières
        réponses du nouvel agent. Le dock, son placement et sa visibilité
        ne sont pas touchés."""
        if self.selector is not None:
            # currentIndexChanged déclencherait _conversation_changed : la
            # purge faite ici suffit.
            self.selector.blockSignals(True)
            self.selector.clear()
            self.selector.blockSignals(False)
        if self.thread is not None:
            self.thread.clear()
        if self.input is not None:
            self.input.clear()
        if self.status_label is not None:
            self.status_label.clear()
        self._update_composer()
        self._update_badge()

    def _network_callback(self, callback):
        """``on_result`` protégé par génération : une réponse émise pour un
        agent n'est jamais appliquée à l'agent suivant."""
        generation = self._identity_generation
        def _guarded(result):
            if generation == self._identity_generation:
                callback(result)
        return _guarded

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
        # La disposition n'a de sens qu'avec deux panneaux : le sous-menu vit
        # et meurt avec la messagerie.
        self.arranger.remove_menu()
        self.arranger.install_menu(more_menu)
        self._ensure_dock()

    def _ensure_dock(self):
        if self.dock is not None:
            try:
                self.dock.objectName()
                self.arranger.apply()
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

        # Portée d'un message « équipe » : tous les équipiers actifs (défaut)
        # ou les connectés seulement. Cachée hors conversation broadcast —
        # sans intérêt pour un message direct — pour garder le dock compact ;
        # sa visibilité suit la sélection (cf. _update_composer).
        self._online_only = QCheckBox("Connectés seulement", container)
        self._online_only.setMinimumWidth(0)
        self._online_only.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._online_only.setToolTip(
            "N'envoyer qu'aux équipiers actuellement connectés")
        self._online_only.setAccessibleName("Portée du message à l'équipe")
        self._online_only.hide()
        layout.addWidget(self._online_only)

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
        area_name = settings_schema.read(settings, "messaging_dock_area")
        area = Qt.BottomDockWidgetArea if area_name == "bottom" else Qt.RightDockWidgetArea
        self._arranging = True
        try:
            self.window.addDockWidget(area, self.dock)
        finally:
            self._arranging = False
        self.arranger.apply()
        self.dock.visibilityChanged.connect(self._visibility_changed)
        self.dock.setVisible(settings.value("messaging_dock_visible", False, type=bool))
        self._update_composer()

    def _destroy_ui(self):
        # Dernier état des panneaux enregistré avant la destruction : il sera
        # restauré à la prochaine ouverture de session.
        self.arranger.forget_docks()
        self.arranger.remove_menu()
        for obj_name in ("button", "menu_action", "dock"):
            obj = getattr(self, obj_name, None)
            if obj is not None:
                try:
                    if obj_name == "dock":
                        # removeDockWidget MASQUE le dock : sans ce garde, la
                        # déconnexion enregistrait « messagerie fermée » et la
                        # session suivante la rouvrait masquée.
                        self._arranging = True
                        try:
                            self.window.removeDockWidget(obj)
                        finally:
                            self._arranging = False
                    obj.deleteLater()
                except RuntimeError:
                    pass
            setattr(self, obj_name, None)
        self.selector = self.thread = self.input = self.send_button = None
        self.status_label = None
        self._online_only = None
        self._icon_layout = None
        self._more_menu = None

    def toggle(self):
        if not self.enabled or not self.staff_id:
            return
        self._ensure_dock()
        # Onglet en arrière-plan : on l'amène devant au lieu de le masquer.
        if toggle_dock(self.dock):
            self.request_state()

    def open(self, conversation_key=None):
        """Ouvre le panneau au premier plan et sélectionne la conversation
        demandée — utilisé au clic sur une notification de messagerie."""
        if not self.enabled or not self.staff_id:
            return
        self._ensure_dock()
        # Fenêtre réduite quand on clique la notification : la restaurer,
        # sinon l'ouverture du panneau reste invisible.
        if self.window.isMinimized():
            self.window.showNormal()
        # show()/raise_(), jamais toggle()/toggle_dock : elles masqueraient
        # un panneau déjà au premier plan.
        self.dock.show()
        self.dock.raise_()
        if conversation_key and self.selector is not None:
            index = self.selector.findData(conversation_key)
            if index >= 0:
                self.selector.setCurrentIndex(index)
        # _visibility_changed/_conversation_changed déclenchent déjà
        # request_state/mark_read ; cet appel couvre le cas « dock déjà
        # visible et conversation déjà sélectionnée ».
        self.request_state()

    def open_from_notification(self, data):
        self.open((data or {}).get("conversation_key"))

    def _visibility_changed(self, visible):
        if self.dock is None or self._arranging or self.arranger.busy:
            # Déplacement programmatique en cours : ce changement n'est pas un
            # choix de l'utilisateur, on ne le persiste pas.
            return
        # visibilityChanged est aussi émis quand l'onglet change dans un
        # groupe : on persiste l'état OUVERT/FERMÉ du panneau, pas le fait
        # que son onglet soit au premier plan (sinon consulter la file
        # « fermait » la messagerie pour la session suivante).
        self._settings_factory().setValue(
            "messaging_dock_visible", not self.dock.isHidden())
        if visible:
            self.request_state()
            self._mark_visible_read()
        self._schedule_window_fit()

    def arrange_docks(self):
        """Applique la disposition mémorisée de la file et de la messagerie
        (empilés dans l'ordre choisi, ou en onglets)."""
        self.arranger.apply()

    def _schedule_window_fit(self):
        callback = getattr(self.window, "fit_window_to_content", None)
        if callable(callback):
            QTimer.singleShot(0, callback)

    def apply_dock_area(self):
        """ Applique la position enregistrée (glisser-déposer ou préférence
        « Position de la messagerie »). Dans la zone de la file, la
        disposition choisie (empilés/onglets) s'applique ; ailleurs, les deux
        panneaux sont indépendants. """
        if self.dock is None:
            return
        area_name = settings_schema.read(
            self._settings_factory(), "messaging_dock_area")
        area = (Qt.BottomDockWidgetArea if area_name == "bottom"
                else Qt.RightDockWidgetArea)
        self._arranging = True
        try:
            if self.window.dockWidgetArea(self.dock) != area:
                self.window.addDockWidget(area, self.dock)
        finally:
            self._arranging = False
        self.arranger.apply()

    def heartbeat(self):
        if not self.enabled or not self.staff_id:
            return
        # Si une présence est déjà en vol, cet appel est refusé comme doublon
        # (retour None) : rien à faire, la requête en cours la rafraîchit —
        # mais le timer doit tout de même être armé pour la périodicité.
        self.window.api.messaging_presence(
            self.client_instance_id,
            on_result=self._network_callback(self._handle_state_result))
        if not self.heartbeat_timer.isActive():
            self.heartbeat_timer.start()

    def request_state(self):
        if self.enabled and self.staff_id:
            handle = self.window.api.messaging_state(
                on_result=self._network_callback(self._handle_state_result))
            if handle is None:
                # Refusée comme doublon : une lecture d'état est déjà en vol.
                # On mémorise la demande — la réponse de la requête en cours
                # relancera une lecture (cf. _handle_state_result), pour ne
                # pas perdre le rafraîchissement qui motivait cet appel.
                self._state_pending = True

    def _handle_state_result(self, result):
        try:
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
                    event = self._pending_event
                    sender = event.get("sender_name")
                    notification = {
                        "origin": "messaging",
                        # Événement sans expéditeur (accusé de lecture, par
                        # exemple un payload {"read": True}) : pas de nom à
                        # afficher, texte de repli neutre.
                        "message": (f"Nouveau message de {sender}." if sender
                                    else "Nouveau message."),
                    }
                    # Un clic sur la notification ouvre le panneau sur la
                    # conversation concernée : on transporte la clé du fil
                    # (cf. open_from_notification).
                    if event.get("kind") == "broadcast":
                        conversation_key = "broadcast"
                    elif event.get("sender_staff_id"):
                        conversation_key = f"direct:{event.get('sender_staff_id')}"
                    else:
                        conversation_key = None
                    if conversation_key is not None:
                        notification["conversation_key"] = conversation_key
                    self.window.show_notification(notification, internal=True)
                elif not self._baseline_known and self.unread_total > 0:
                    # Première réponse d'état de la session (connexion ou
                    # reconnexion) : rappeler les non-lus accumulés pendant
                    # l'absence — une seule fois. Pas de cumul avec « Nouveau
                    # message » : un même état n'affiche qu'une alerte.
                    self.window.show_notification({
                        "origin": "messaging",
                        "message": f"Vous avez {self.unread_total} message(s) non lu(s).",
                    }, internal=True)
                self._pending_event = None
                # Qu'une notification soit partie ou non, l'état de
                # référence est désormais connu pour cette session.
                self._baseline_known = True
                if self.dock is not None and self.dock.isVisible():
                    self.request_messages()
                return
            if isinstance(result.data, dict) and result.data.get("error") == "messaging_disabled":
                self.set_enabled(False)
            else:
                # Un refus persistant (staff_not_connected : l'agent n'est
                # plus rattaché au comptoir côté serveur, ou counter_id
                # erroné) était jusqu'ici invisible : les heartbeats
                # échouaient en silence et l'équipier restait « hors ligne »
                # pour tous. Le journal est la seule trace de ce diagnostic.
                logger = getattr(self.window, "logger", None)
                if logger is not None:
                    logger.warning(
                        "Réponse messagerie inattendue (statut=%s, data=%s)",
                        result.status, result.data)
        finally:
            # Une demande d'état reçue pendant le vol de cette requête
            # (refusée comme doublon, _state_pending) est honorée maintenant
            # que la réponse est là — quel que soit le statut de la réponse.
            # Ce handler sert aussi les réponses de présence : elles
            # satisfont pareillement la demande en attente.
            if self._state_pending:
                self._state_pending = False
                self.request_state()

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
        # Consignes/erreurs affichées et confirmation « équipe » éventuellement
        # armée concernaient la conversation quittée : sans objet ici.
        self._status_message = None
        self._broadcast_confirm = False
        if self._online_only is not None:
            # La portée choisie appartenait à la conversation quittée : on
            # repart sur le périmètre par défaut (« tous »).
            self._online_only.setChecked(False)
        if self.thread is not None:
            self.thread.clear()
        self._update_composer()
        self.request_messages()

    def request_messages(self):
        conversation = self._selected()
        if not conversation or not self.enabled:
            return
        kind = conversation.get("kind")
        key = conversation.get("key")
        peer_id = conversation.get("staff_id") if kind == "direct" else None
        self.window.api.messaging_messages(
            kind, peer_staff_id=peer_id,
            on_result=self._network_callback(
                lambda result, _key=key:
                    self._handle_messages_result(result, _key)))

    def _handle_messages_result(self, result, key=None):
        # Réponse tardive d'une conversation quittée entretemps : on l'ignore
        # au lieu d'afficher l'ancien fil dans la conversation désormais
        # sélectionnée (les requêtes sont asynchrones, sans annulation).
        if key is not None:
            current = self._selected()
            if not current or current.get("key") != key:
                return
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
        # Si l'utilisateur consultait l'historique (ascenseur remonté), un
        # rafraîchissement ne doit pas le ramener brutalement en bas ; sinon on
        # suit normalement le dernier message.
        bar = self.thread.verticalScrollBar()
        previous = bar.value() if bar is not None else 0
        stick_to_bottom = bar is None or bar.value() >= bar.maximum()
        self.thread.setHtml("".join(blocks) or "<p>Aucun message aujourd’hui.</p>")
        if stick_to_bottom or bar is None:
            self.thread.moveCursor(QTextCursor.End)
        else:
            bar.setValue(min(previous, bar.maximum()))

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
        # Seulement si le fil est réellement lisible : un onglet « Messagerie »
        # resté derrière la liste des patients est « visible » pour Qt, mais
        # l'expéditeur recevrait un accusé « Lu » à tort.
        if not self.dock or not dock_in_front(self.dock):
            return
        # La fenêtre réduite ou masquée (systray) laisse le dock « visible »
        # pour Qt : sans ce garde, l'expéditeur recevrait un « Lu » mensonger
        # alors que personne ne peut lire le fil.
        window = self.window
        try:
            if not window.isVisible() or window.isMinimized():
                return
        except RuntimeError:
            return  # fenêtre détruite : rien à marquer
        ids = [item.get("id") for item in self.messages if item.get("is_unread")]
        ids = [value for value in ids if value is not None]
        if ids:
            self.window.api.messaging_read(
                ids,
                on_result=self._network_callback(
                    lambda _result: self.request_state()))

    def on_window_restored(self):
        """Fenêtre restaurée (dé-réduite, sortie du systray) : les non-lus
        affichés pendant l'absence deviennent réellement lisibles."""
        self._mark_visible_read()

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
            # Un message transitoire (limite de longueur, consigne de
            # confirmation, erreur d'envoi) prime sur le statut calculé.
            self.status_label.setText(self._status_message or status)
        if self.input is not None:
            self.input.setEnabled(can_send)
        if self.send_button is not None:
            text = self.input.toPlainText().strip() if self.input is not None else ""
            self.send_button.setEnabled(
                can_send and bool(text) and not self._send_in_flight)

    def send(self):
        conversation = self._selected()
        if not conversation or self.input is None:
            return
        # Un envoi à la fois : Entrée (sendRequested) ou le bouton peuvent se
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
        self._update_composer()  # bouton grisé tant que la requête est en vol
        self.window.api.messaging_send(
            client_message_id, kind, body, recipient_id, scope=scope,
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
            self._sent_body = None
            self._status_message = None
            self._broadcast_confirm = False
            self._update_composer()
            self.request_state()
            self.request_messages()
            return
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
