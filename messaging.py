"""Interface compacte et contrôleur réseau de la messagerie App Comptoir."""

from __future__ import annotations

import html
import json
import re
import time
import unicodedata
import uuid
from datetime import datetime, timezone

from PySide6.QtCore import (
    QEvent, QModelIndex, QObject, QSettings, QSortFilterProxyModel, Qt, QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QIcon, QKeySequence, QPainter, QPalette, QPixmap,
    QShortcut, QStandardItem, QStandardItemModel, QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QCompleter, QDockWidget, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QPlainTextEdit, QSizePolicy,
    QTabBar, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

import settings_schema
from dock_arrangement import DockArranger, dock_in_front, toggle_dock
from quick_replies_dialog import (
    DEFAULT_MAX_COUNT, DEFAULT_MAX_LENGTH, QuickRepliesDialog,
)


class MessageInput(QPlainTextEdit):
    sendRequested = Signal()
    #: Échap : rien à annuler dans le champ (le brouillon est conservé) —
    #: le signal rend la main à la fenêtre principale.
    escapeRequested = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
                event.modifiers() & Qt.ShiftModifier):
            self.sendRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.escapeRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


#: En dessous de ces dimensions (px), le panneau resserre marges et
#: espacements : chaque pixel rendu va au fil et au champ de saisie.
COMPACT_DOCK_WIDTH = 320
COMPACT_DOCK_HEIGHT = 260


def composer_spacing(width, height):
    """(marge, espacement, marge interne du champ) selon la taille du
    panneau de messagerie : valeurs d'origine, ou resserrées s'il est
    étroit OU bas."""
    if width < COMPACT_DOCK_WIDTH or height < COMPACT_DOCK_HEIGHT:
        return 2, 3, 2
    return 6, 6, 4


class _ResizeAwareWidget(QWidget):
    """Conteneur du panneau qui signale ses changements de taille."""

    resized = Signal(int, int)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        size = event.size()
        self.resized.emit(size.width(), size.height())


def _normalize(text):
    """Forme de comparaison tolérante : minuscules, accents retirés — un
    « hel » tapé retrouve « Hélène »."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)).lower()


def _presence_icon(online):
    """Pastille de présence ~10 px pour l'onglet d'un fil direct : verte en
    ligne, grise hors ligne."""
    pixmap = QPixmap(10, 10)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#22c55e" if online else "#9ca3af"))
    painter.drawEllipse(0, 0, 9, 9)
    painter.end()
    return QIcon(pixmap)


#: Rôle modèle portant le nom « brut » d'un destinataire (sans le suffixe
#: « — Comptoir X » du libellé) : c'est lui, pas le texte affiché, qui sert
#: au matching de _RecipientFilterModel.
_NAME_ROLE = Qt.ItemDataRole.UserRole + 1

#: Rôle modèle lu par la passe de filtrage INTERNE de QCompleter (MatchContains
#: dessus) : nom + initiales, en version affichée ET normalisée. QCompleter
#: filtre une seconde fois le modèle qu'on lui donne ; réunir ici toutes les
#: formes cherchées garantit que sa passe n'écarte jamais une ligne que
#: _RecipientFilterModel a acceptée (« ft », accents…).
_SEARCH_ROLE = Qt.ItemDataRole.UserRole + 2

#: Rôle modèle portant les initiales MÉTIER du destinataire
#: (Pharmacist.initials côté serveur — « BN » pour « Benoit »), différentes
#: des initiales dérivées des mots du nom quand le nom n'en a qu'un.
#: None/absent sur un vieux serveur : toléré.
_INITIALS_ROLE = Qt.ItemDataRole.UserRole + 3

#: Token de mention « @XX » dans un corps de message : initiales métier
#: (1 à 10 caractères de mot), match insensible à la casse — la même
#: forme que l'extraction côté serveur.
_MENTION_PATTERN = re.compile(r"@(\w{1,10})\b", re.IGNORECASE)


class _RecipientFilterModel(QSortFilterProxyModel):
    """Filtre des destinataires de « Écrire à… » : la requête normalisée est
    acceptée si elle est sous-chaîne du nom (ce qui couvre le préfixe d'un
    mot), préfixe des initiales dérivées des mots (« ft » → « Fabien Test »),
    ou préfixe des initiales métier (« bn » → « Benoit », rôle
    _INITIALS_ROLE ; absent sur les vieux serveurs)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._query = ""

    def set_query(self, text):
        self._query = _normalize(text)
        self.invalidate()

    def filterAcceptsRow(self, row, parent):
        if not self._query:
            return True
        index = self.sourceModel().index(row, 0, parent)
        name = _normalize(index.data(_NAME_ROLE) or "")
        if self._query in name:
            return True
        initials = "".join(word[0] for word in name.split())
        if initials.startswith(self._query):
            return True
        business = _normalize(index.data(_INITIALS_ROLE) or "")
        return bool(business) and business.startswith(self._query)


class RecipientSearchEdit(QLineEdit):
    """Champ de recherche d'un destinataire : Échap vide d'abord le champ ;
    un second Échap — champ déjà vide — émet escapeRequested (la main
    revient à la fenêtre principale). Quand le popup du completer est
    ouvert, c'est lui qui consomme Échap (fermeture) avant que la touche
    n'arrive ici."""

    escapeRequested = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.text():
                self.clear()
            else:
                self.escapeRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MessagingController(QObject):
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

        container = _ResizeAwareWidget(self.dock)
        layout = QVBoxLayout(container)
        self._layout = layout
        self._spacing = None
        # Marges et espacements ajustés à la taille réelle du panneau
        # (cf. composer_spacing) ; valeurs d'origine posées d'emblée.
        container.resized.connect(self._apply_spacing)

        # Recherche d'un destinataire : complétion par nom ou initiales ;
        # l'activation ouvre l'onglet de la conversation. Pas de combo
        # éditable : Entrée y insérait un item fantôme (InsertAtBottom) quand
        # le texte ne correspondait à rien.
        self.search = RecipientSearchEdit(container)
        self.search.setPlaceholderText("Écrire à… (prénom ou initiales)")
        self.search.setAccessibleName("Nouvelle conversation")
        self.search.setToolTip(
            "Écrire à un équipier : nom ou initiales, Entrée ouvre le fil")
        self.search.setMinimumWidth(0)
        self.search.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._recipient_model = QStandardItemModel(self.search)
        self._recipient_proxy = _RecipientFilterModel(self.search)
        self._recipient_proxy.setSourceModel(self._recipient_model)
        self._recipient_proxy.setFilterKeyColumn(0)
        completer = QCompleter(self._recipient_proxy, self.search)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        # Le completer filtre lui-même par-dessus le proxy : _SEARCH_ROLE
        # réunit toutes les formes cherchées pour que sa passe n'écarte rien.
        completer.setCompletionRole(_SEARCH_ROLE)
        self.search.setCompleter(completer)
        self.search.textChanged.connect(self._recipient_proxy.set_query)
        self.search.returnPressed.connect(self._activate_first_recipient)
        self.search.escapeRequested.connect(self._release_focus)
        completer.activated[QModelIndex].connect(self._recipient_activated)
        layout.addWidget(self.search)

        # Conversations en cours : « Équipe » permanente, un onglet par fil
        # direct actif ou choisi — plusieurs échanges restent visibles d'un
        # coup d'œil, dans un encombrement vertical minimal.
        self.tabs = QTabBar(container)
        self.tabs.setExpanding(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.ElideRight)
        self.tabs.setDrawBase(False)
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setAccessibleName("Conversations en cours")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        layout.addWidget(self.tabs)

        # Navigation clavier entre conversations, même geste que les
        # navigateurs : Ctrl+Tab / Ctrl+Maj+Tab, circulaire. Enfants du
        # dock : détruits et recréés avec lui, jamais en double.
        for sequence, step in (("Ctrl+Tab", 1), ("Ctrl+Shift+Tab", -1)):
            shortcut = QShortcut(QKeySequence(sequence), self.dock)
            shortcut.setContext(
                Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(
                lambda _step=step: self._cycle_tab(_step))

        self.status_label = QLabel("", container)
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status_label.setAccessibleName("État de la conversation")
        # Vide par défaut : sans texte, le libellé ne réserve pas de ligne.
        self.status_label.hide()
        layout.addWidget(self.status_label)

        self.thread = QTextBrowser(container)
        self.thread.setOpenExternalLinks(False)
        self.thread.setAccessibleName("Messages de la conversation")
        self.thread.setMinimumWidth(0)
        self.thread.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        layout.addWidget(self.thread, 1)
        # Ascenseur arrivé en bas (défilement manuel ou suivi du dernier
        # message) : point d'armement de l'accusé « Lu ».
        self.thread.verticalScrollBar().valueChanged.connect(
            self._thread_reached_bottom)
        # Arrivé tout en haut : les messages plus anciens se chargent à la
        # demande (« charger plus ancien » sans bouton).
        self.thread.verticalScrollBar().valueChanged.connect(
            self._maybe_load_older)

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

        # Ligne de saisie : le champ et un seul petit bouton « ⚡ ». Pas de
        # bouton « Envoyer » (Entrée envoie, le texte d'invite le rappelle)
        # ni de bouton « urgent » (option du menu « ⚡ ») : sur un petit
        # écran, la largeur revient au texte en cours d'écriture.
        composer = QHBoxLayout()
        self._composer = composer
        self.input = MessageInput(container)
        self.input.setPlaceholderText(self.INPUT_PLACEHOLDER)
        self.input.setAccessibleName("Texte du message")
        self.input.setToolTip(self.INPUT_TOOLTIP)
        self.input.setMinimumWidth(0)
        self.input.setMaximumHeight(76)
        self.input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.input.setTabChangesFocus(True)
        self.input.textChanged.connect(self._update_composer)
        self.input.textChanged.connect(self._maybe_emit_typing)
        self.input.sendRequested.connect(self.send)
        self.input.escapeRequested.connect(self._release_focus)
        composer.addWidget(self.input, 1)
        # Réponses rapides : le menu insère la phrase dans le champ (elle
        # reste modifiable, Entrée envoie) — pas d'envoi direct : un envoi
        # immédiat sous le doigt au comptoir serait une fausse manip.
        self.quick_button = QToolButton(container)
        self.quick_button.setText("⚡")
        self.quick_button.setToolTip(self.QUICK_BUTTON_TOOLTIP)
        self.quick_button.setAccessibleName("Réponses rapides")
        self.quick_button.setAutoRaise(True)
        self.quick_button.setFixedSize(32, 32)
        self.quick_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        quick_menu = QMenu(self.quick_button)
        self._quick_menu = quick_menu
        # Les phrases (équipe puis personnelles) sont insérées en tête par
        # _rebuild_quick_menu ; viennent ensuite, fixes, la gestion des
        # phrases personnelles puis le retrait du dernier message.
        self._quick_manage_action = quick_menu.addAction(
            "Gérer mes phrases rapides…")
        self._quick_manage_action.triggered.connect(
            self.open_quick_replies_dialog)
        # « Message urgent » : arme le PROCHAIN envoi en urgent —
        # notification persistante chez le destinataire. Un seul message
        # concerné : l'option se décoche toute seule après l'envoi réussi
        # (et repart décochée à chaque changement de conversation). Armée,
        # elle se voit sur le champ lui-même (cf. _apply_urgent_state).
        quick_menu.addSeparator()
        self.urgent_action = quick_menu.addAction(self.URGENT_ACTION_TEXT)
        self.urgent_action.setCheckable(True)
        self.urgent_action.setToolTip(
            "Le prochain message s'affichera en notification persistante "
            "chez le destinataire")
        self.urgent_action.toggled.connect(self._apply_urgent_state)
        # Retrait du dernier message envoyé par erreur : séparé des
        # insertions de texte — c'est une autre nature d'action, confirmée
        # en deux temps en ligne (même logique que l'envoi « équipe »).
        quick_menu.addSeparator()
        self._delete_action = quick_menu.addAction(
            "Retirer mon dernier message")
        self._delete_action.triggered.connect(self._request_delete)
        # Son état « activé » dépend du dernier message chargé : réévalué à
        # l'ouverture du menu plutôt qu'à chaque rendu du fil.
        quick_menu.aboutToShow.connect(self._refresh_delete_action)
        self.quick_button.setMenu(quick_menu)
        self._rebuild_quick_menu()
        composer.addWidget(self.quick_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(composer)
        self._apply_spacing(container.width(), container.height())

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

    def _insert_quick_reply(self, text):
        """Réponse rapide : insérée à la position du curseur (insertion, pas
        de remplacement), jamais envoyée directement — l'équipier relit,
        modifie si besoin puis valide par Entrée."""
        if self.input is None:
            return
        self.input.insertPlainText(text)
        self.input.setFocus()

    def _can_delete_last_message(self):
        """L'action « Retirer mon dernier message » ne vise que le dernier
        message du fil affiché : il doit être de moi et dans la fenêtre de
        retrait — la fenêtre exacte est aussi contrôlée côté serveur
        (DELETE_WINDOW_SECONDS, seul juge si les horloges divergent)."""
        if not self.enabled or not self.messages:
            return False
        last = self.messages[-1]
        if (last.get("sender") or {}).get("staff_id") != self.staff_id:
            return False
        try:
            created = datetime.fromisoformat(
                str(last.get("created_at") or ""))
        except (TypeError, ValueError):
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - created).total_seconds()
        return age < self.DELETE_WINDOW_SECONDS

    def _refresh_delete_action(self):
        if self._delete_action is not None:
            self._delete_action.setEnabled(self._can_delete_last_message())

    def _request_delete(self):
        """Retrait du dernier message du fil : confirmation à deux temps
        dans le menu lui-même. Un menu se referme au clic sur une action :
        au premier clic on le rouvre donc aussitôt avec l'action renommée
        « ⚠ Confirmer le retrait » — le second clic, à la même place et
        visible, déclenche la requête. Passé le délai (ou changement de
        fil), l'intention retombe."""
        if not self._can_delete_last_message():
            return
        if not self._delete_confirm:
            self._delete_confirm = True
            self._delete_action.setText("⚠ Confirmer le retrait")
            QTimer.singleShot(5000, self._disarm_delete_confirm)
            # Le menu vient de se refermer sur ce premier clic : on le
            # rouvre pour que la confirmation reste sous le doigt.
            QTimer.singleShot(0, self._reopen_quick_menu)
            return
        self._disarm_delete_confirm()
        self.window.api.messaging_delete(
            self.messages[-1].get("id"),
            on_result=self._network_callback(self._handle_delete_result))

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
        try:
            if self._delete_action is not None:
                self._delete_action.setText("Retirer mon dernier message")
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

    def _tab_changed(self, index):
        key = self.tabs.tabData(index) if index >= 0 else None
        # Le brouillon saisi appartient à la conversation quittée : conservé
        # par clé, puis celui du fil sélectionné est restauré (ou vide).
        self._store_draft()
        self._current_key = key
        self._restore_draft()
        self.messages = []
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
                self.messages = messages
                self._render_messages()
                self._schedule_read_mark()

    def _render_messages(self, keep_position=False):
        if self.thread is None:
            return
        blocks = []
        separator_done = False
        # Expéditeur de la bulle précédente (staff_id + kind) : les messages
        # consécutifs du même expéditeur se regroupent — le nom n'est répété
        # que sur la première et les suivantes se rapprochent (marge 2 px).
        previous_bubble = None
        for message in self.messages:
            sender = message.get("sender") or {}
            mine = sender.get("staff_id") == self.staff_id
            align = "right" if mine else "left"
            color = "#dbeafe" if mine else "#f1f3f5"
            if message.get("kind") == "broadcast":
                color = "#fff3cd"
            urgent = bool(message.get("urgent"))
            if urgent:
                # Prioritaire : fond rouge pâle et « ❗ » devant le nom —
                # repérable quelle que soit la bulle (direct, équipe,
                # sortante).
                color = "#fee2e2"
            name = "Vous" if mine else html.escape(str(sender.get("name") or "Inconnu"))
            if urgent:
                name = "❗ " + name
            timestamp = self._format_time(message.get("created_at"))
            body = html.escape(str(message.get("body") or "")).replace("\n", "<br>")
            # Mentions « @initiales » sur le corps DÉJÀ échappé (@ et
            # lettres ne sont jamais réécrits par html.escape) : seules les
            # initiales connues sont mises en valeur, un « @xx » quelconque
            # reste du texte ordinaire.
            body = _MENTION_PATTERN.sub(self._highlight_mention, body)
            # Accusés en gris discret : « Lu » (consultation réelle) prime,
            # sinon « Remis » (l'app du destinataire a récupéré le message).
            receipt = ""
            if mine and message.get("read_at"):
                receipt = ' · <span style="color:#6b7280">Lu</span>'
            elif mine and message.get("delivered_at"):
                receipt = ' · <span style="color:#6b7280">Remis</span>'
            # Repère visuel posé juste avant le PREMIER message entrant non
            # lu — uniquement si des messages le précèdent : un fil 100 %
            # non lu se suffit (le repère n'apparaît pas entre les non-lus
            # non plus — l'occasion est consommée dès le premier rencontré).
            # Il disparaît naturellement au prochain rendu après l'accusé.
            if (not mine and message.get("is_unread")
                    and not separator_done):
                separator_done = True
                if blocks:
                    blocks.append(
                        '<div style="text-align:center; color:#6b7280; '
                        'font-size:small; margin:6px 0">'
                        '—— Nouveaux messages ——</div>')
                    # Le repère coupe le regroupement : la bulle qui le
                    # suit réaffiche le nom de l'expéditeur.
                    previous_bubble = None
            staff = sender.get("staff_id")
            bubble_key = ((staff, message.get("kind"))
                          if staff is not None else None)
            grouped = bubble_key is not None and bubble_key == previous_bubble
            margin = "2px 0 5px" if grouped else "5px 0"
            header = "" if grouped else f"<b>{name}</b> "
            blocks.append(
                f'<div style="text-align:{align}; margin:{margin}">'
                f'<span style="background:{color}; padding:6px">'
                f'{header}<small>{timestamp}{receipt}</small><br>{body}'
                f'</span></div>'
            )
            previous_bubble = bubble_key
        # Si l'utilisateur consultait l'historique (ascenseur remonté), un
        # rafraîchissement ne doit pas le ramener brutalement en bas ; sinon on
        # suit normalement le dernier message.
        bar = self.thread.verticalScrollBar()
        previous = bar.value() if bar is not None else 0
        previous_max = bar.maximum() if bar is not None else 0
        stick_to_bottom = (not keep_position
                           and (bar is None or bar.value() >= bar.maximum()))
        self.thread.setHtml("".join(blocks) or "<p>Aucun message aujourd’hui.</p>")
        if keep_position and bar is not None:
            # Prépend d'anciens messages : le contenu a poussé vers le bas.
            # Garder la même DISTANCE au bas de l'ascenseur cale l'écran sur
            # le même message qu'avant le chargement.
            bar.setValue(bar.maximum() - (previous_max - previous))
        elif stick_to_bottom or bar is None:
            self.thread.moveCursor(QTextCursor.End)
        else:
            bar.setValue(min(previous, bar.maximum()))

    def _highlight_mention(self, match):
        """Mise en valeur d'un token « @XX » si c'est une initiale connue :
        fond orange quand il s'agit de la mienne — elle doit sauter aux
        yeux dans un fil d'équipe — simple gras orange pour les autres."""
        token = _normalize(match.group(1))
        if token not in self._known_initials:
            return match.group(0)
        if token == _normalize(self.self_initials or ""):
            return (f'<b style="color:#fff;background:#b45309">'
                    f'{match.group(0)}</b>')
        return f'<b style="color:#b45309">{match.group(0)}</b>'

    @staticmethod
    def _format_time(value):
        if not value:
            return ""
        try:
            parsed = datetime.fromisoformat(value)
            return parsed.astimezone().strftime("%H:%M") if parsed.tzinfo else parsed.strftime("%H:%M")
        except (TypeError, ValueError):
            return ""

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

    def socket_presence_changed(self):
        self.request_state()

    def socket_config_changed(self, enabled):
        self.set_enabled(enabled)

    def socket_connected(self):
        if self.enabled and self.staff_id:
            self.heartbeat()
