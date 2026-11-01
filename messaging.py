"""Interface compacte et contrôleur réseau de la messagerie App Comptoir.

``MessagingController`` porte l'état (identité, conversations, onglets, fil,
lecture) et les échanges avec le serveur. Il s'appuie sur :

- ``messaging_ui`` : widgets et construction du panneau ;
- ``messaging_render`` : rendu HTML du fil (pur, sans Qt) ;
- ``messaging_composer`` : saisie et envoi (urgent, équipe, brouillons) ;
- ``messaging_delete`` : retrait d'un message envoyé par erreur ;
- ``messaging_quick_replies`` : phrases rapides du menu « ⚡ ».

Les trois derniers sont des classes parentes (mixins) du contrôleur : leurs
méthodes lisent et modifient son état, et restent appelées sur lui.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from PySide6.QtCore import (
    QEvent, QObject, QSettings, Qt, QTimer,
)
from PySide6.QtGui import (
    QAction, QColor, QIcon, QPalette, QStandardItem, QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QMenu, QTabBar, QToolButton,
)

import notification_rules
import settings_schema
from dock_arrangement import DockArranger, dock_in_front, toggle_dock
from messaging_render import render_thread_html
from messaging_render import normalize as _normalize
import messaging_ui
from messaging_composer import ComposerMixin
from messaging_delete import DeleteMessagesMixin
from messaging_quick_replies import QuickRepliesMixin
from messaging_ui import (
    _INITIALS_ROLE, _NAME_ROLE, _SEARCH_ROLE, _presence_icon, composer_spacing,
)
from quick_replies_dialog import (
    DEFAULT_MAX_COUNT, DEFAULT_MAX_LENGTH,
)


class MessagingController(QObject, ComposerMixin, DeleteMessagesMixin, QuickRepliesMixin):
    HEARTBEAT_MS = 30_000
    # Délai avant l'envoi de l'accusé « Lu » : laisse à l'équipier le temps
    # de voir réellement les messages (cf. _schedule_read_mark).
    READ_DELAY_MS = 1_500
    #: Phrases d'équipe d'origine du menu « ⚡ » : repli tant que le serveur
    #: n'a pas transmis les siennes (ou s'il est trop ancien). Identiques à
    #: ``DEFAULT_TEAM`` de Serveur/quick_replies.py (un test le vérifie).
    #: Une phrase est insérée dans le champ, JAMAIS envoyée telle quelle.
    QUICK_REPLIES = (
        "J'arrive", "Occupé(e) 2 min", "En réserve", "Un client m'attend",
    )
    #: Intervalle minimal entre deux signaux « en train d'écrire » (s) :
    #: l'indicateur est éphémère, inutile d'en envoyer à chaque frappe.
    TYPING_EMIT_INTERVAL = 3.0
    #: Durée d'affichage de « … écrit » sans nouveau signal (s).
    TYPING_DISPLAY_MS = 4_000
    #: Fenêtre de retrait d'un de mes messages (s) : garde miroir de
    #: DELETE_WINDOW_SECONDS côté serveur, qui reste le seul juge.
    DELETE_WINDOW_SECONDS = 15 * 60
    #: Sans bouton « Envoyer », l'invite du champ rappelle le geste.
    #: Vers toute l'équipe, l'envoi se confirme par un second Entrée
    #: (cf. send) : annoncé AVANT la frappe, pas découvert après.
    INPUT_PLACEHOLDER = "Écrire un message… (Entrée pour envoyer)"
    URGENT_PLACEHOLDER = "Message URGENT… (Entrée pour envoyer)"
    TEAM_PLACEHOLDER = ("Message à toute l'équipe… "
                        "(Entrée 2 fois pour envoyer)")
    TEAM_URGENT_PLACEHOLDER = ("Message URGENT à toute l'équipe… "
                               "(Entrée 2 fois pour envoyer)")
    INPUT_TOOLTIP = "Entrée : envoyer — Maj+Entrée : aller à la ligne"
    TEAM_INPUT_TOOLTIP = ("Entrée puis Entrée à nouveau : envoyer à toute "
                          "l'équipe (confirmation) — Maj+Entrée : aller à "
                          "la ligne")
    URGENT_ACTION_TEXT = "Message urgent (prochain envoi)"
    #: Libellé de repos de l'action de retrait du menu « ⚡ » ; à
    #: l'ouverture du menu, il précise le message visé ou la raison du
    #: grisé (cf. _delete_action_state).
    DELETE_ACTION_TEXT = "Retirer mon dernier message"
    #: Longueur de l'extrait du message visé dans un libellé de menu.
    SNIPPET_LENGTH = 25
    QUICK_BUTTON_TOOLTIP = "Réponses rapides, message urgent…"
    URGENT_BUTTON_TOOLTIP = ("Prochain message envoyé en URGENT — "
                             "décocher dans ce menu pour annuler")
    #: Repère visuel de l'urgence armée, posé sur le champ lui-même.
    URGENT_INPUT_STYLE = (
        "QPlainTextEdit { border: 2px solid #dc2626; border-radius: 4px;"
        " background-color: palette(base); }")
    #: Fond rouge plutôt que couleur du texte : l'émoji « ⚡ » garde ses
    #: propres couleurs et ignorerait un simple ``color``.
    URGENT_BUTTON_STYLE = (
        "QToolButton { background-color:#dc2626; border-radius:4px; }")

    def __init__(self, window, settings_factory=QSettings):
        super().__init__()
        self.window = window
        self._settings_factory = settings_factory
        self.enabled = False
        self.staff_id = None
        self.staff_name = None
        # Mes initiales métier (état « self » du serveur) et l'ensemble des
        # initiales connues normalisées (annuaire + moi, reconstruit par
        # _rebuild_recipients) — servant au surlignage des mentions « @XX ».
        self.self_initials = None
        self._known_initials = set()
        self.conversations = []
        self.messages = []
        self._unread_marker_id = None
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
        self.search = None
        self.tabs = None
        self.thread = None
        self.input = None
        self.quick_button = None
        # Menu « ⚡ » : phrases d'équipe (admin) puis phrases personnelles,
        # toutes deux transmises dans l'état de messagerie. Tant que le
        # serveur ne les a pas envoyées : phrases d'origine, et pas de
        # gestion personnelle (rien où les enregistrer).
        self.team_replies = list(self.QUICK_REPLIES)
        self.personal_replies = []
        self.quick_replies_supported = False
        self.quick_max_count = DEFAULT_MAX_COUNT
        self.quick_max_length = DEFAULT_MAX_LENGTH
        # Liste personnelle dont l'enregistrement a échoué : reproposée à la
        # prochaine ouverture de la fenêtre plutôt que perdue.
        self._unsaved_personal = None
        self._quick_menu = None
        self._quick_phrase_actions = []
        self._quick_manage_action = None
        # « Message urgent » : action cochable du menu « ⚡ » (plus de bouton
        # dédié — la largeur revient au champ de saisie).
        self.urgent_action = None
        self._layout = None
        self._composer = None
        self._spacing = None
        self.status_label = None
        # Barre « ↓ N nouveaux messages » : messages arrivés pendant que le
        # fil était remonté (identifiants, pour ne jamais compter deux fois).
        self.new_messages_button = None
        self._unseen_ids = set()
        # Conversations remises en non lu À LA MAIN alors qu'elles étaient
        # affichées : l'accusé « Lu » automatique les épargne tant que
        # l'équipier n'y est pas revenu (changement d'onglet, réouverture du
        # panneau, envoi d'un message) — sinon il repartirait aussitôt.
        self._keep_unread_keys = set()
        self._online_only = None
        self._icon_layout = None
        self._more_menu = None
        self._recipient_model = None
        self._recipient_proxy = None
        # Brouillons par conversation, et clé du fil dont le texte est
        # actuellement dans le champ de saisie (cf. _store_draft).
        self._drafts = {}
        self._current_key = None
        self._sent_key = None
        # Onglets ouverts à la main (recherche, notification) : persistés
        # d'une session à l'autre. L'intention d'ouverture couvre le cas où
        # open() arrive avant le premier état serveur.
        self._open_tabs = self._load_open_tabs()
        self._pending_open_key = None
        # Onglets fermés à la main : clé -> last_message_id vu au moment de
        # la fermeture, persisté pareil. Une fermeture n'est pas un « vu » :
        # le fil réapparaît dès qu'une activité nouvelle change cet id.
        self._closed_tabs = self._load_closed_tabs()
        # Vrai après le premier _refresh_tabs fait sur un état serveur : tant
        # qu'aucun état n'a été vu, l'onglet affiché n'est qu'un défaut et la
        # dernière conversation mémorisée reprend la main au premier état.
        self._tabs_initialized = False
        # Un envoi en vol : Entrée et le bouton peuvent se déclencher pendant
        # la requête (Entrée n'est pas debouncé) — sans verrou, le même message
        # partait en double. _sent_body mémorise le corps envoyé pour ne pas
        # effacer un nouveau brouillon saisi pendant l'attente.
        self._send_in_flight = False
        self._sent_body = None
        # « … écrit » affiché dans le libellé d'état : texte courant et
        # expiration (le signal ne se répète pas de lui-même).
        self._typing_text = None
        self._last_typing_emit = 0.0
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
        # « Retirer mon dernier message » se confirme en deux temps : le
        # menu se referme à chaque clic sur une action — au premier, on le
        # rouvre aussitôt avec l'action renommée « confirmer » ; le second
        # clic, au même endroit, lance la requête. Jamais de modale au
        # comptoir (elle volerait le focus en pleine saisie).
        self._delete_confirm = False
        self._delete_target_id = None
        self._delete_action = None
        # Pagination « charger plus ancien » : has_more de la dernière
        # réponse messages du fil courant seulement (réinitialisé au
        # changement de fil), et verrou pendant qu'une page remonte.
        self._has_more = False
        self._older_in_flight = False
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

        # L'accusé « Lu » ne part jamais instantanément : ce timer laisse le
        # temps de voir le fil, puis _mark_visible_read re-vérifie TOUTES les
        # conditions (premier plan, ascenseur en bas) à l'échéance.
        self._read_timer = QTimer(self)
        self._read_timer.setSingleShot(True)
        self._read_timer.setInterval(self.READ_DELAY_MS)
        self._read_timer.timeout.connect(self._mark_visible_read)

        # « … écrit » s'efface tout seul : pas de signal « arrêté d'écrire »
        # dans le protocole, l'expiration le tue.
        self._typing_timer = QTimer(self)
        self._typing_timer.setSingleShot(True)
        self._typing_timer.setInterval(self.TYPING_DISPLAY_MS)
        self._typing_timer.timeout.connect(self._clear_typing)

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
        # Les alertes de l'équipier qui part (dont les persistantes : urgent,
        # mention) ne doivent pas rester à l'écran du suivant.
        self._dismiss_notifications(
            lambda origin, _data:
                origin in notification_rules.MESSAGE_ALERT_ORIGINS)
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
        # l'identité précédente devient caduque. La toute première identité
        # lue au démarrage n'a pas d'agent « précédent » : elle ne doit pas
        # effacer les préférences d'onglets restaurées depuis QSettings.
        startup = self._identity_generation == 0
        self._identity_generation += 1
        self.conversations = []
        self.messages = []
        self._unread_marker_id = None
        self._keep_unread_keys = set()
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
        self._delete_confirm = False
        self._delete_target_id = None
        self._has_more = False
        self._older_in_flight = False
        # « … écrit » n'a de sens que dans la session qui l'a affiché.
        self._typing_timer.stop()
        self._typing_text = None
        self._last_typing_emit = 0.0
        # La notification « non-lus à la connexion » pourra se rejouer pour la
        # prochaine session (nouvel agent, réactivation) : sa référence repart
        # de zéro.
        self._baseline_known = False
        # Les initiales de l'agent précédent n'ont plus cours : ni pour les
        # notifications de mention ni pour le surlignage du fil.
        self.self_initials = None
        self._known_initials = set()
        # Les phrases personnelles sont celles de l'agent précédent : retour
        # aux phrases d'origine jusqu'au premier état du nouvel agent.
        self.team_replies = list(self.QUICK_REPLIES)
        self.personal_replies = []
        self.quick_replies_supported = False
        self._unsaved_personal = None
        self._rebuild_quick_menu()
        # Tout ce qui suivait la session s'arrête avec elle : accusé « Lu » en
        # cours d'armement, brouillons par conversation, onglets ouverts ou
        # fermés à la main et intention d'ouverture en attente.
        self._read_timer.stop()
        self._drafts = {}
        self._current_key = None
        self._sent_key = None
        self._pending_open_key = None
        if not startup:
            # Onglets ouverts/fermés à la main : préférences persistées,
            # restaurées au lancement puis purgées seulement aux changements
            # de session (nouvel agent, déconnexion, désactivation).
            self._open_tabs = set()
            self._save_open_tabs()
            self._closed_tabs = {}
            self._save_closed_tabs()
            # Nouvel agent : son premier état restaurera à nouveau la
            # dernière conversation mémorisée.
            self._tabs_initialized = False

    def _clear_displayed_data(self):
        """Vide immédiatement ce qui est affiché quand l'agent change alors
        que le panneau existe déjà : le fil et le brouillon de l'agent
        précédent ne doivent pas rester visibles le temps des premières
        réponses du nouvel agent. Le dock, son placement et sa visibilité
        ne sont pas touchés."""
        if self.tabs is not None:
            # currentChanged déclencherait _tab_changed : la purge faite ici
            # suffit. QTabBar n'a pas de clear() : on retire un à un.
            self.tabs.blockSignals(True)
            while self.tabs.count():
                self.tabs.removeTab(0)
            self.tabs.blockSignals(False)
        if self._recipient_model is not None:
            self._recipient_model.clear()
        if self.search is not None:
            self.search.clear()
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

        container = messaging_ui.build_panel(self, self.dock)

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
        # Points d'armement de l'accusé « Lu » : retour au premier plan de la
        # fenêtre (ou du dock détaché), prise de focus du champ de saisie.
        self.window.installEventFilter(self)
        self.dock.installEventFilter(self)
        self.input.installEventFilter(self)
        self.dock.setVisible(settings.value("messaging_dock_visible", False, type=bool))
        # Un état peut déjà être connu si le panneau est (re)créé après le
        # premier résultat : onglets et complétion se remplissent tout de suite.
        self._refresh_tabs()
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
                        # Filtres d'événements posés à la création : retirés
                        # AVANT la destruction, sinon un événement livré
                        # après deleteLater rappellerait un objet mort.
                        for watched in (self.window, obj, self.input):
                            if watched is None:
                                continue
                            try:
                                watched.removeEventFilter(self)
                            except RuntimeError:
                                pass
                    obj.deleteLater()
                except RuntimeError:
                    pass
            setattr(self, obj_name, None)
        self.search = self.tabs = self.thread = None
        self.input = None
        self.quick_button = self.urgent_action = None
        self._layout = self._composer = self._spacing = None
        self._delete_action = None
        self._quick_menu = self._quick_manage_action = None
        self._quick_phrase_actions = []
        self.status_label = None
        self.new_messages_button = None
        self._unseen_ids = set()
        self._online_only = None
        self._recipient_model = self._recipient_proxy = None
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
        if conversation_key:
            known = next((item for item in self.conversations
                          if item.get("key") == conversation_key), None)
            if known is None:
                # État pas encore chargé (panneau jamais ouvert) : l'intention
                # est consommée par le prochain _refresh_tabs.
                self._pending_open_key = conversation_key
            else:
                if known.get("kind") == "direct":
                    self._mark_opened(conversation_key)
                self._refresh_tabs()
                index = self._tab_index_of(conversation_key)
                if index >= 0:
                    self.tabs.setCurrentIndex(index)
        # _visibility_changed/_tab_changed déclenchent déjà
        # request_state/armement « Lu » ; cet appel couvre le cas « dock déjà
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
            # Panneau rouvert : l'équipier revient à la conversation affichée,
            # la remise en non lu a rempli son office.
            self._keep_unread_keys.discard(self._current_key)
            self.request_state()
            self._schedule_read_mark()
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

    def _dismiss_notifications(self, predicate):
        dismiss = getattr(self.window, "dismiss_notifications", None)
        if callable(dismiss):
            dismiss(predicate)

    def _dismiss_read_notifications(self):
        """Ferme les alertes « nouveau message » devenues sans objet : la
        conversation désignée n'a plus de non-lus — lus dans le panneau (ici
        ou à un autre poste du même équipier), ou retirés par l'expéditeur.
        Une alerte sans conversation (rappel « N non lus » de connexion) se
        ferme quand plus rien n'est non lu. Les persistantes (urgent,
        mention) sont concernées : c'est pour elles que ça compte."""
        unread = {item.get("key"): int(item.get("unread_count") or 0)
                  for item in self.conversations if item.get("key")}
        total = self.unread_total

        def read_already(origin, data):
            if origin not in notification_rules.MESSAGE_ALERT_ORIGINS:
                return False
            key = data.get("conversation_key")
            if key is None:
                return total == 0
            return unread.get(key, 0) == 0

        self._dismiss_notifications(read_already)

    def _handle_state_result(self, result):
        try:
            if result.status == 200 and isinstance(result.data, dict):
                if result.data.get("enabled") is False:
                    self.set_enabled(False)
                    return
                previous_unread = self.unread_total
                self.conversations = result.data.get("conversations") or []
                self.unread_total = int(result.data.get("unread_total") or 0)
                self.self_initials = (
                    result.data.get("self") or {}).get("initials")
                self._apply_quick_replies(result.data.get("quick_replies"))
                self._refresh_tabs()
                self._update_badge()
                # Alertes déjà sans objet (messages lus dans le panneau, ou
                # retirés par leur expéditeur) : fermées avant toute nouvelle.
                self._dismiss_read_notifications()
                if self.unread_total > previous_unread and self._pending_event:
                    event = self._pending_event
                    sender = event.get("sender_name")
                    if self.staff_id in (
                            event.get("mentioned_staff_ids") or []):
                        # Mention « @initiales » : alerte dédiée, persistante
                        # jusqu'à fermeture explicite (origine sticky, cf.
                        # notification_rules.STICKY_ORIGINS). La mention
                        # prime sur « urgent » : elle nomme l'équipier.
                        notification = {
                            "origin": "messaging_mention",
                            "message": (
                                f"{sender} vous a mentionné." if sender
                                else "Vous avez été mentionné."),
                        }
                    elif event.get("urgent"):
                        # Message prioritaire : persistant pareil — l'alerte
                        # ne doit pas s'effacer toute seule au comptoir.
                        notification = {
                            "origin": "messaging_urgent",
                            "message": (f"Message urgent de {sender}."
                                        if sender else "Message urgent."),
                        }
                    else:
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
                    # Fenêtre en arrière-plan (l'équipier travaille ailleurs)
                    # : la notification flottante peut passer inaperçue —
                    # le clignotement de la barre des tâches insiste,
                    # jusqu'à ce que la fenêtre reprenne le premier plan.
                    active = QApplication.activeWindow()
                    if active is not self.window and active is not self.dock:
                        QApplication.alert(self.window)
                elif not self._baseline_known and self.unread_total > 0:
                    # Première réponse d'état de la session (connexion ou
                    # reconnexion) : rappeler les non-lus accumulés pendant
                    # l'absence — une seule fois. Pas de cumul avec « Nouveau
                    # message » : un même état n'affiche qu'une alerte.
                    mentions = int(result.data.get("unread_mentions") or 0)
                    urgents = int(result.data.get("unread_urgent") or 0)
                    if mentions:
                        # Des mentions non lues mènent l'alerte : plus
                        # insistante (origine sticky) qu'un décompte de
                        # non-lus — l'équipier est nommément attendu dessus.
                        # S'il y a AUSSI des urgents, ils sont cumulés dans
                        # le texte : une seule notification, la plus forte.
                        text = (
                            f"Vous avez {self.unread_total} message(s) "
                            f"non lu(s), dont {mentions} mention(s) "
                            "vous concernant")
                        if urgents:
                            text += f" et {urgents} urgent(s)"
                        self.window.show_notification({
                            "origin": "messaging_mention",
                            "message": text + ".",
                        }, internal=True)
                    elif urgents:
                        # Pas de mention mais du prioritaire : l'alerte
                        # « urgent » reste persistante pareil.
                        self.window.show_notification({
                            "origin": "messaging_urgent",
                            "message": (
                                f"Vous avez {urgents} message(s) urgent(s) "
                                "non lu(s)."),
                        }, internal=True)
                    else:
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

    # --- onglets de conversation --------------------------------------------

    def _load_open_tabs(self):
        """Onglets ouverts à la main, lus dans QSettings (liste JSON de clés).
        Tolérant : une valeur corrompue ne doit pas casser la messagerie."""
        stored = self._settings_factory().value("messaging_open_tabs", "[]")
        if isinstance(stored, str):
            try:
                stored = json.loads(stored)
            except ValueError:
                stored = []
        return {key for key in (stored or []) if isinstance(key, str)}

    def _save_open_tabs(self):
        self._settings_factory().setValue(
            "messaging_open_tabs", json.dumps(sorted(self._open_tabs)))

    def _load_closed_tabs(self):
        """Onglets fermés à la main : dict JSON clé → last_message_id vu à la
        fermeture (int ou None). Tolérant : valeur corrompue → dict vide."""
        stored = self._settings_factory().value("messaging_closed_tabs", "{}")
        if isinstance(stored, str):
            try:
                stored = json.loads(stored)
            except ValueError:
                stored = {}
        if not isinstance(stored, dict):
            return {}
        return {key: value for key, value in stored.items()
                if isinstance(key, str)
                and (value is None or isinstance(value, int))}

    def _save_closed_tabs(self):
        self._settings_factory().setValue(
            "messaging_closed_tabs", json.dumps(self._closed_tabs))

    def _mark_opened(self, key):
        """Ouverture explicite d'un fil (recherche, notification, intention
        consommée) : mémorisée dans _open_tabs, et toute fermeture enregistrée
        pour cette clé devient caduque."""
        self._open_tabs.add(key)
        self._save_open_tabs()
        if key in self._closed_tabs:
            del self._closed_tabs[key]
            self._save_closed_tabs()

    def _tab_index_of(self, key):
        for i in range(self.tabs.count()):
            if self.tabs.tabData(i) == key:
                return i
        return -1

    def _selected_key(self):
        if self.tabs is None:
            return None
        index = self.tabs.currentIndex()
        return self.tabs.tabData(index) if index >= 0 else None

    def _selected(self):
        key = self._selected_key()
        return next((item for item in self.conversations
                     if item.get("key") == key), None)

    def _update_tab(self, index, conversation):
        """Libellé court, infobulle complète et pastille d'un onglet."""
        unread = int(conversation.get("unread_count") or 0)
        if conversation.get("kind") == "broadcast":
            short = "Équipe"
            self.tabs.setTabIcon(index, QIcon())     # pas de pastille
            tooltip = conversation.get("name") or "Toute l’équipe"
        else:
            name = conversation.get("name") or "Conversation"
            short = name.split()[0]
            self.tabs.setTabIcon(
                index, _presence_icon(bool(conversation.get("online"))))
            tooltip = name
            counter = conversation.get("counter_name")
            if counter:
                tooltip += f" — Comptoir {counter}"
            if not conversation.get("online", True):
                tooltip += " (hors ligne)"
        if unread:
            short += f" ({unread})"
        self.tabs.setTabText(index, short)
        self.tabs.setTabToolTip(index, tooltip)
        self.tabs.setTabData(index, conversation.get("key"))
        # Non-lus : l'onglet se colore — un nouveau message se repère d'un
        # coup d'œil même quand son onglet n'est pas celui sélectionné.
        self.tabs.setTabTextColor(
            index, QColor("#d97706") if unread
            else self.tabs.palette().color(QPalette.ColorRole.WindowText))

    def _refresh_tabs(self):
        """Fait correspondre les onglets à l'ensemble actif : « Équipe »
        toujours, puis les fils directs avec de l'activité (non-lus ou
        historique) ou ouverts à la main — dans l'ordre du serveur. La
        sélection est préservée par clé ; le modèle de complétion de la
        recherche est reconstruit dans la foulée."""
        if self.tabs is None:
            return
        previous_key = self._selected_key()
        had_state = self._tabs_initialized
        if self.conversations:
            self._tabs_initialized = True
        known = {item.get("key"): item for item in self.conversations
                 if item.get("key")}
        # Intention d'ouverture (open() avant l'arrivée de l'état) : la
        # conversation désormais connue est marquée ouverte et sera
        # sélectionnée ci-dessous ; si l'état chargé ne la contient pas,
        # l'intention est caduque. État pas encore arrivé : on la garde.
        pending = None
        if self._pending_open_key is not None:
            if self._pending_open_key in known:
                pending = self._pending_open_key
                # « Équipe » est permanente : seuls les fils directs entrent
                # dans les onglets ouverts à la main.
                if known[pending].get("kind") == "direct":
                    self._mark_opened(pending)
                self._pending_open_key = None
            elif self.conversations:
                self._pending_open_key = None
        # Ensemble actif : un fil direct a un onglet dès qu'il porte des
        # non-lus, un historique, ou a été ouvert à la main — sauf s'il a été
        # fermé à la main et n'a pas eu d'activité depuis (last_message_id
        # inchangé) : la fermeture est mémorisée, pas un « vu ».
        ordered = ["broadcast"]
        closed_dirty = False
        for conversation in self.conversations:
            key = conversation.get("key")
            if not key or conversation.get("kind") == "broadcast":
                continue
            if key in self._open_tabs:
                # Ouverture manuelle prioritaire : une fermeture enregistrée
                # qui traînerait encore est caduque.
                if key in self._closed_tabs:
                    del self._closed_tabs[key]
                    closed_dirty = True
                ordered.append(key)
                continue
            last_id = conversation.get("last_message_id")
            if key in self._closed_tabs:
                if self._closed_tabs[key] == last_id:
                    continue  # fermé à la main, rien de neuf : reste fermé
                # last_message_id a changé = activité nouvelle (dont les
                # non-lus entrants) : l'onglet renaît.
                del self._closed_tabs[key]
                closed_dirty = True
            if (int(conversation.get("unread_count") or 0) > 0
                    or last_id is not None):
                ordered.append(key)
        # Fils avec des non-lus d'abord, juste après « Équipe » : avec
        # beaucoup d'onglets, un message non lu ne reste plus caché derrière
        # les flèches de défilement. Tri stable : l'ordre du serveur est
        # conservé à l'intérieur de chaque groupe.
        ordered = ["broadcast"] + sorted(
            ordered[1:],
            key=lambda key: int(
                (known.get(key) or {}).get("unread_count") or 0) == 0)
        if closed_dirty:
            self._save_closed_tabs()
        keep = set(ordered)
        self.tabs.blockSignals(True)
        try:
            for i in range(self.tabs.count() - 1, -1, -1):
                if self.tabs.tabData(i) not in keep:
                    self.tabs.removeTab(i)
            for i, key in enumerate(ordered):
                index = self._tab_index_of(key)
                if index < 0:
                    index = self.tabs.insertTab(i, "")
                elif index != i:
                    self.tabs.moveTab(index, i)
                self._update_tab(i, known.get(key) or {
                    "key": key, "kind": "broadcast"})
            # « Équipe » est permanente : retirer le bouton de fermeture posé
            # automatiquement par tabsClosable sur son onglet.
            self.tabs.setTabButton(
                0, QTabBar.ButtonPosition.RightSide, None)
            # Sélection : intention d'ouverture > onglet courant conservé >
            # dernière conversation consultée > « Équipe ». La conservation
            # ne vaut qu'entre états réels : avant le premier, l'onglet
            # affiché n'est qu'un défaut posé en attendant.
            target = pending
            if target is None and had_state and previous_key in keep:
                target = previous_key
            if target is None:
                saved = self._settings_factory().value(
                    "messaging_last_conversation")
                if saved in keep:
                    target = saved
            if target is None:
                target = ordered[0]
            index = self._tab_index_of(target)
            if index >= 0:
                self.tabs.setCurrentIndex(index)
        finally:
            self.tabs.blockSignals(False)
        self._rebuild_recipients()
        if self._selected_key() != previous_key:
            # Signaux bloqués pendant la mutation : si la sélection a changé
            # de fait (onglet disparu, restauration, intention honorée), la
            # logique de changement est rejouée ici.
            self._tab_changed(self.tabs.currentIndex())
        self._update_composer()

    def _rebuild_recipients(self):
        """Source de la complétion « Écrire à… » : un item par conversation
        directe. « Équipe » est volontairement absente — son onglet est
        permanent, inutile de la chercher."""
        if self._recipient_model is None:
            return
        self._recipient_model.clear()
        for conversation in self.conversations:
            if conversation.get("kind") != "direct":
                continue
            name = conversation.get("name") or "Équipier"
            # Initiales métier du serveur (« BN » pour « Benoit ») : affichées
            # dans le libellé et matchables — « Benoit » seul ne dériverait
            # que « b » des mots du nom.
            business_initials = conversation.get("initials") or ""
            label = name
            if business_initials:
                label += f" ({business_initials})"
            counter = conversation.get("counter_name")
            if counter:
                label += f" — Comptoir {counter}"
            initials = "".join(word[0] for word in name.split() if word)
            item = QStandardItem(label)
            item.setData(conversation.get("key"), Qt.ItemDataRole.UserRole)
            item.setData(name, _NAME_ROLE)
            item.setData(business_initials or None, _INITIALS_ROLE)
            # Texte cherché sous toutes ses formes pour que la passe interne
            # du completer (MatchContains sur _SEARCH_ROLE) n'écarte rien.
            item.setData(
                f"{name} {initials} {business_initials} {_normalize(name)} "
                f"{_normalize(initials)} {_normalize(business_initials)}",
                _SEARCH_ROLE)
            self._recipient_model.appendRow(item)
        # Surlignage des mentions : toutes les initiales connues, normalisées
        # comme les tokens extraits du texte — la mienne comprise (absente
        # de l'annuaire des destinataires, ajoutée depuis l'état « self »).
        self._known_initials = {
            _normalize(conversation.get("initials"))
            for conversation in self.conversations
            if conversation.get("initials")
        }
        if self.self_initials:
            self._known_initials.add(_normalize(self.self_initials))

    def _recipient_activated(self, index):
        """Choix d'un destinataire dans le popup : ouvre l'onglet de sa
        conversation et rend la main au champ de message."""
        # L'index émis par le completer peut être dans son modèle interne ou
        # dans notre proxy : les deux propagent data(), la clé se lit pareil.
        key = index.data(Qt.ItemDataRole.UserRole) if index.isValid() else None
        if not key or self.tabs is None:
            return
        self._mark_opened(key)
        self._refresh_tabs()
        tab = self._tab_index_of(key)
        if tab >= 0:
            self.tabs.setCurrentIndex(tab)
        self.search.clear()
        if self.input is not None:
            self.input.setFocus()

    def _activate_first_recipient(self):
        """Entrée dans la recherche sans popup ouvert : prend la première
        ligne filtrée — la meilleure correspondance du modèle."""
        if self._recipient_proxy is not None and self._recipient_proxy.rowCount():
            self._recipient_activated(self._recipient_proxy.index(0, 0))

    def _cycle_tab(self, step):
        """Ctrl+Tab / Ctrl+Maj+Tab : conversation suivante/précédente,
        circulaire — on suit l'ordre affiché, qui est réordonnable à la
        souris."""
        if self.tabs is None or self.tabs.count() <= 1:
            return
        count = self.tabs.count()
        self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % count)

    def _release_focus(self):
        """Échap dans le panneau : la main revient à la fenêtre principale,
        l'équipier enchaîne au clavier sur la file des patients."""
        self.window.setFocus()

    # --- phrases rapides « ⚡ » -----------------------------------------------

    def _show_transient_status(self, text, delay_ms=6000):
        """Message affiché sous le champ de saisie, effacé après quelques
        secondes s'il est toujours celui affiché."""
        self._status_message = text
        self._update_composer()
        QTimer.singleShot(delay_ms, lambda: self._clear_status_if(text))

    def _clear_status_if(self, text):
        if self._status_message != text:
            return
        self._status_message = None
        try:
            self._update_composer()
        except RuntimeError:
            # Le délai peut s'écouler après la destruction du panneau.
            pass

    def _tab_menu(self, index):
        """Menu du clic droit sur l'onglet ``index`` (None hors onglet)."""
        key = self.tabs.tabData(index) if index >= 0 else None
        conversation = next((item for item in self.conversations
                             if item.get("key") == key), None)
        if conversation is None:
            return None
        menu = QMenu(self.tabs)
        action = menu.addAction("Marquer comme non lu")
        already = int(conversation.get("unread_count") or 0) > 0
        action.setEnabled(not already and self.enabled)
        if already:
            action.setText("Marquer comme non lu (déjà non lu)")
        action.triggered.connect(
            lambda _checked=False, _key=key: self.mark_unread(_key))
        return menu

    def _show_tab_menu(self, position):
        menu = self._tab_menu(self.tabs.tabAt(position))
        if menu is not None:
            menu.exec(self.tabs.mapToGlobal(position))
            menu.deleteLater()

    def mark_unread(self, key):
        """Remet la conversation en non lu (son dernier message reçu) :
        l'onglet se recolore et le compteur repart, comme pour un message
        arrivé — rappel pour y revenir plus tard."""
        conversation = next((item for item in self.conversations
                             if item.get("key") == key), None)
        if conversation is None or not self.enabled:
            return
        if key == self._current_key:
            # Affichée en ce moment : sans ce garde, l'accusé automatique la
            # relirait dans la seconde.
            self._keep_unread_keys.add(key)
            self._read_timer.stop()
        kind = conversation.get("kind")
        peer = conversation.get("staff_id") if kind == "direct" else None
        self.window.api.messaging_mark_unread(
            kind, peer_staff_id=peer,
            on_result=self._network_callback(
                lambda result, _key=key: self._handle_unread_result(
                    result, _key)))

    def _handle_unread_result(self, result, key):
        data = result.data if isinstance(result.data, dict) else {}
        if result.status == 200 and data.get("unread"):
            self._show_transient_status(
                "Conversation marquée comme non lue.", delay_ms=4000)
            self.request_state()
            return
        self._keep_unread_keys.discard(key)
        message = data.get("message")
        self._show_transient_status(
            message if isinstance(message, str) and message
            else "La conversation n'a pas pu être marquée comme non lue.")

    def _close_tab(self, index):
        """Fermeture d'un onglet à la main : la clé sort des onglets ouverts
        manuellement, et la fermeture elle-même est mémorisée avec le
        last_message_id courant — le fil reste fermé aux états suivants
        jusqu'à une activité nouvelle (cf. _refresh_tabs)."""
        key = self.tabs.tabData(index)
        if key == "broadcast":
            return  # « Équipe » est permanente : jamais fermable.
        conversation = next((item for item in self.conversations
                             if item.get("key") == key), None)
        self._closed_tabs[key] = (conversation or {}).get("last_message_id")
        self._save_closed_tabs()
        self.tabs.removeTab(index)
        if key in self._open_tabs:
            self._open_tabs.discard(key)
            self._save_open_tabs()

    def _tab_changed(self, index):
        key = self.tabs.tabData(index) if index >= 0 else None
        # Le brouillon saisi appartient à la conversation quittée : conservé
        # par clé, puis celui du fil sélectionné est restauré (ou vide).
        self._store_draft()
        self._current_key = key
        # Revenir sur une conversation remise en non lu, c'est la lire.
        self._keep_unread_keys.discard(key)
        self._restore_draft()
        self.messages = []
        self._unread_marker_id = None
        # Consignes/erreurs affichées, confirmation « équipe » et consigne
        # « retirer » éventuellement armées concernaient la conversation
        # quittée : sans objet ici.
        self._status_message = None
        self._broadcast_confirm = False
        self._disarm_delete_confirm()
        # « Il reste des messages plus anciens » appartenait au fil quitté ;
        # la page récente du nouveau fil le recalculera. Le verrou de
        # chargement se relâche : une réponse en vol pour l'ancien fil sera
        # de toute façon ignorée au retour (clé différente).
        self._has_more = False
        self._older_in_flight = False
        if self._online_only is not None:
            # La portée choisie appartenait à la conversation quittée : on
            # repart sur le périmètre par défaut (« tous »).
            self._online_only.setChecked(False)
        # « Urgent » armé dans le fil quitté : on ne le transporte pas dans
        # la conversation suivante.
        self._disarm_urgent()
        # Les nouveaux messages signalés appartenaient au fil quitté.
        self._unseen_ids = set()
        self._update_new_messages_button()
        # « … écrit » concernait l'interlocuteur du fil quitté.
        self._typing_timer.stop()
        self._typing_text = None
        if self.thread is not None:
            self.thread.clear()
        # Dernière conversation consultée : retrouvée à la prochaine session.
        # On ne mémorise qu'une clé réellement connue : la sélection par
        # défaut (« Équipe » posée avant l'arrivée du premier état) ne doit
        # pas écraser la préférence à restaurer.
        if key is not None and any(
                item.get("key") == key for item in self.conversations):
            self._settings_factory().setValue("messaging_last_conversation", key)
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

    def _maybe_load_older(self, value):
        """Ascenseur arrivé tout en haut du fil : les messages plus anciens
        se chargent à la demande — seulement s'il en reste (has_more de la
        dernière réponse) et jamais deux pages à la fois."""
        if value != 0:
            return
        self._load_older()

    def _load_older(self):
        if (not self._has_more or self._older_in_flight
                or not self.messages):
            return
        conversation = self._selected()
        if not conversation or not self.enabled:
            return
        oldest = self.messages[0].get("id")
        if oldest is None:
            return
        self._older_in_flight = True
        kind = conversation.get("kind")
        key = conversation.get("key")
        peer_id = conversation.get("staff_id") if kind == "direct" else None
        self.window.api.messaging_messages(
            kind, peer_staff_id=peer_id, before_id=oldest,
            on_result=self._network_callback(
                lambda result, _key=key:
                    self._handle_messages_result(result, _key, prepend=True)))

    def _handle_messages_result(self, result, key=None, prepend=False):
        # Chargement d'une page plus ancienne terminé (appliquée ou non) :
        # le verrou anti-doublon se relâche.
        if prepend:
            self._older_in_flight = False
        # Réponse tardive d'une conversation quittée entretemps : on l'ignore
        # au lieu d'afficher l'ancien fil dans la conversation désormais
        # sélectionnée (les requêtes sont asynchrones, sans annulation).
        if key is not None:
            current = self._selected()
            if not current or current.get("key") != key:
                return
        if result.status == 200 and isinstance(result.data, dict):
            self._has_more = bool(result.data.get("has_more"))
            messages = result.data.get("messages") or []
            if prepend:
                if messages:
                    self.messages = messages + self.messages
                    # Insertion EN TÊTE : l'écran doit rester calé sur le
                    # même contenu (keep_position, cf. _render_messages).
                    self._render_messages(keep_position=True)
            else:
                # Rafraîchissement ordinaire : la page récente remplace le
                # fil — simple à suivre, les anciens se rechargeront pareil.
                at_bottom = self._thread_at_bottom()
                arrived = self._arrived_messages(self.messages, messages)
                self.messages = messages
                self._render_messages()
                if arrived and not at_bottom:
                    # Fil remonté : le rendu garde la position de lecture,
                    # la barre annonce ce qui est arrivé plus bas.
                    self._unseen_ids.update(arrived)
                self._update_new_messages_button()
                self._schedule_read_mark()

    def _arrived_messages(self, previous, current):
        """Identifiants des messages d'autrui apparus EN FIN de fil depuis le
        rendu précédent. Premier chargement d'un fil : rien (ce n'est pas
        une arrivée). Mes propres envois et les traces de retrait ne
        comptent jamais."""
        known = [m.get("id") for m in previous if m.get("id") is not None]
        if not known:
            return set()
        last_known = max(known)
        return {
            m.get("id") for m in current
            if m.get("id") is not None and m.get("id") > last_known
            and not m.get("deleted")
            and (m.get("sender") or {}).get("staff_id") != self.staff_id
        }

    def _thread_at_bottom(self):
        bar = self.thread.verticalScrollBar() if self.thread is not None else None
        return bar is None or bar.value() >= bar.maximum()

    def _update_new_messages_button(self):
        """Libellé et visibilité de la barre : seuls comptent les messages
        signalés encore présents (un message retiré entre-temps sort du
        compte)."""
        present = {m.get("id") for m in self.messages if not m.get("deleted")}
        self._unseen_ids &= present
        button = self.new_messages_button
        if button is None:
            return
        try:
            count = len(self._unseen_ids)
            if count:
                text = ("↓ 1 nouveau message" if count == 1
                        else f"↓ {count} nouveaux messages")
                button.setText(text)
                button.setAccessibleName(text)
            button.setVisible(bool(count))
        except RuntimeError:
            pass

    def _scroll_to_latest(self):
        """Clic sur la barre : direction le dernier message. L'arrivée en bas
        cache la barre et arme l'accusé « Lu » (_thread_reached_bottom)."""
        if self.thread is None:
            return
        bar = self.thread.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())
        self._unseen_ids = set()
        self._update_new_messages_button()

    def _receipt_targets(self):
        """(id portant « Lu », id portant « Remis ») parmi MES messages : un
        accusé répété sur chaque bulle n'apportait rien — la lecture se fait
        dans l'ordre, « Lu » sous mon dernier message lu vaut pour tous ceux
        d'avant. « Remis » ne s'affiche que sous mon tout dernier message,
        s'il est remis mais pas encore lu."""
        mine = [m for m in self.messages
                if m.get("id") is not None and not m.get("deleted")
                and (m.get("sender") or {}).get("staff_id") == self.staff_id]
        read_id = next((m.get("id") for m in reversed(mine)
                        if m.get("read_at")), None)
        last = mine[-1] if mine else None
        delivered_id = (last.get("id") if last and not last.get("read_at")
                        and last.get("delivered_at") else None)
        return read_id, delivered_id

    def _update_unread_marker(self):
        """Message devant lequel se place le repère « Nouveaux messages » :
        le premier non-lu entrant vu dans ce fil. Il est mémorisé — l'accusé
        « Lu » ne l'efface plus — et ne se déplace que pour une NOUVELLE
        vague de non-lus, une fois la précédente lue. Remis à zéro au
        changement de conversation."""
        first_unread = next(
            (m.get("id") for m in self.messages
             if m.get("is_unread") and not m.get("deleted")
             and m.get("id") is not None
             and (m.get("sender") or {}).get("staff_id") != self.staff_id),
            None)
        marker = self._unread_marker_id
        if first_unread is not None:
            current = next((m for m in self.messages
                            if m.get("id") == marker), None)
            still_unread = bool(current and current.get("is_unread"))
            if marker is None or current is None or (
                    not still_unread and first_unread > marker):
                self._unread_marker_id = first_unread
        return self._unread_marker_id

    def _render_messages(self, keep_position=False):
        if self.thread is None:
            return
        # Repères calculés ici (ils dépendent de l'état de lecture) ; la mise
        # en forme elle-même est pure (messaging_render).
        marker_id = self._update_unread_marker()
        read_id, delivered_id = self._receipt_targets()
        document = render_thread_html(
            self.messages, my_staff_id=self.staff_id,
            today=datetime.now().astimezone().date(),
            marker_id=marker_id, read_id=read_id, delivered_id=delivered_id,
            known_initials=self._known_initials, self_initials=self.self_initials)
        # Si l'utilisateur consultait l'historique (ascenseur remonté), un
        # rafraîchissement ne doit pas le ramener brutalement en bas ; sinon on
        # suit normalement le dernier message.
        bar = self.thread.verticalScrollBar()
        previous = bar.value() if bar is not None else 0
        previous_max = bar.maximum() if bar is not None else 0
        stick_to_bottom = (not keep_position
                           and (bar is None or bar.value() >= bar.maximum()))
        self.thread.setHtml(document)
        if keep_position and bar is not None:
            # Prépend d'anciens messages : le contenu a poussé vers le bas.
            # Garder la même DISTANCE au bas de l'ascenseur cale l'écran sur
            # le même message qu'avant le chargement.
            bar.setValue(bar.maximum() - (previous_max - previous))
        elif stick_to_bottom or bar is None:
            self.thread.moveCursor(QTextCursor.End)
        else:
            bar.setValue(min(previous, bar.maximum()))

    def _schedule_read_mark(self):
        """Arme la temporisation de l'accusé « Lu » si le fil est a priori
        visible. La décision finale est prise par _mark_visible_read à
        l'échéance : le délai laisse à l'équipier le temps de voir les
        messages avant qu'on le prétende lu auprès de l'expéditeur."""
        if self.dock is None:
            return
        try:
            if not dock_in_front(self.dock):
                return
            if not self.window.isVisible() or self.window.isMinimized():
                return
        except RuntimeError:
            return
        self._read_timer.start()

    def _thread_reached_bottom(self, value):
        # L'équipier a fait défiler le fil jusqu'en bas : les derniers
        # messages sont à l'écran — c'est un point d'armement du « Lu ».
        bar = self.thread.verticalScrollBar() if self.thread is not None else None
        if bar is not None and value >= bar.maximum():
            # Redescendu à la main : les nouveaux messages sont à l'écran.
            if self._unseen_ids:
                self._unseen_ids = set()
                self._update_new_messages_button()
            self._schedule_read_mark()

    def eventFilter(self, watched, event):
        # Focus sur le champ de saisie, fenêtre (ou dock détaché) activée :
        # le fil attire l'attention — ces événements ne font qu'ARMER la
        # temporisation, les gardes complètes restent dans
        # _mark_visible_read, revérifiées à l'échéance.
        try:
            kind = event.type()
            if (watched is self.input and kind == QEvent.Type.FocusIn) or (
                    watched in (self.window, self.dock)
                    and kind == QEvent.Type.WindowActivate):
                self._schedule_read_mark()
        except RuntimeError:
            pass
        return super().eventFilter(watched, event)

    def _mark_visible_read(self):
        # Conversation remise en non lu à la main : on la laisse non lue
        # tant que l'équipier n'y est pas revenu (cf. _keep_unread_keys).
        if self._current_key in self._keep_unread_keys:
            return
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
        # La fenêtre doit aussi être AU PREMIER PLAN : sans ce garde, un « Lu »
        # partait alors que l'équipier travaille dans une autre application ou
        # que l'écran est verrouillé. Le dock détaché est une fenêtre à part :
        # il peut être l'activeWindow à lui seul.
        active = QApplication.activeWindow()
        if active is not window and active is not self.dock:
            return
        # Ascenseur remonté dans l'historique : les derniers messages ne sont
        # pas à l'écran, on ne peut pas les prétendre lus.
        if self.thread is not None:
            bar = self.thread.verticalScrollBar()
            if bar is not None and bar.value() < bar.maximum():
                return
        ids = [item.get("id") for item in self.messages if item.get("is_unread")]
        ids = [value for value in ids if value is not None]
        if ids:
            self.window.api.messaging_read(
                ids,
                on_result=self._network_callback(
                    lambda _result: self.request_state()))

    def on_window_restored(self):
        """Fenêtre restaurée (dé-réduite, sortie du systray) : les non-lus
        affichés pendant l'absence deviennent réellement lisibles — après le
        délai d'attention, sous les gardes de _mark_visible_read."""
        self._schedule_read_mark()

    def _apply_spacing(self, width, height):
        """Marges resserrées quand le panneau est petit (composer_spacing) ;
        rien n'est touché tant que le palier ne change pas."""
        spacing = composer_spacing(width, height)
        if spacing == self._spacing or self._layout is None:
            return
        self._spacing = spacing
        margin, gap, text_margin = spacing
        try:
            self._layout.setContentsMargins(margin, margin, margin, margin)
            self._layout.setSpacing(gap)
            if self._composer is not None:
                self._composer.setSpacing(gap)
            if self.input is not None:
                self.input.document().setDocumentMargin(text_margin)
        except RuntimeError:
            # Redimensionnement livré pendant la destruction du panneau.
            pass

    def _update_badge(self):
        if self.button is None:
            return
        self.button.setText("💬" if not self.unread_total else f"💬 {self.unread_total}")
        # Des non-lus : le bouton se colore — le signal reste visible même
        # quand le panneau de messagerie est fermé.
        self.button.setStyleSheet(
            "color: #d97706; font-weight: bold;" if self.unread_total else "")
        self.button.setAccessibleDescription(
            f"{self.unread_total} message(s) non lu(s)" if self.unread_total
            else "Aucun message non lu"
        )
        if self.dock is not None:
            # Le titre sert d'étiquette quand le panneau est groupé en
            # onglets avec la file des patients : les non-lus y restent
            # visibles sans ouvrir la messagerie.
            self.dock.setWindowTitle(
                f"Messagerie ({self.unread_total})" if self.unread_total
                else "Messagerie")

    # --- événements Socket.IO ---------------------------------------------

    def socket_changed(self, payload):
        if not self.enabled or not self.staff_id:
            return
        self._pending_event = payload if isinstance(payload, dict) else {}
        self.request_state()

    def socket_typing(self, payload):
        """« … écrit » affiché dans le libellé d'état — seulement si le
        signal concerne la conversation affichée : un typing d'un autre
        fil (ou le mien depuis un autre poste) est ignoré sans bruit."""
        if not isinstance(payload, dict) or not self.enabled:
            return
        if payload.get("staff_id") == self.staff_id:
            return  # mon propre signal depuis un autre poste : ignoré
        conversation = self._selected()
        if not conversation:
            return
        kind = payload.get("kind")
        if kind == "broadcast":
            relevant = conversation.get("kind") == "broadcast"
        elif kind == "direct":
            relevant = (
                conversation.get("kind") == "direct"
                and conversation.get("staff_id") == payload.get("staff_id")
                and payload.get("peer_staff_id") == self.staff_id)
        else:
            relevant = False
        if not relevant:
            return
        self._typing_text = f"{payload.get('name') or 'Quelqu\u2019un'} écrit…"
        # Chaque nouveau signal réarme l'expiration (le timer est déjà
        # singleShot : start() le relance depuis zéro).
        self._typing_timer.start()
        self._update_composer()

    def _clear_typing(self):
        """Expiration du « … écrit » — pas de signal d'arrêt dans le
        protocole : l'indicateur s'éteint tout seul."""
        self._typing_text = None
        self._update_composer()

    def socket_presence_changed(self):
        self.request_state()

    def socket_config_changed(self, enabled):
        self.set_enabled(enabled)

    def socket_connected(self):
        if self.enabled and self.staff_id:
            self.heartbeat()
