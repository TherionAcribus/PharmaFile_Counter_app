"""Widgets et construction du panneau de messagerie.

Sortis de messaging.py : les petites classes de widgets du panneau (champ de
saisie, fil, recherche de destinataire et son filtre) et ``build_panel``,
qui crée le contenu du dock et branche ses signaux sur le contrôleur — même
démarche que main_window_ui.py pour la fenêtre principale. Le comportement
reste dans ``MessagingController``.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import (
    QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut, QStandardItemModel,
)
from PySide6.QtWidgets import (
    QCheckBox, QCompleter, QHBoxLayout, QLabel, QLineEdit, QMenu, QPlainTextEdit,
    QPushButton, QSizePolicy, QTabBar, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from messaging_render import normalize as _normalize


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


class MessageThread(QTextBrowser):
    """Fil des messages. Le menu du clic droit garde les actions standard
    (copier, tout sélectionner) ; le contrôleur y ajoute les siennes via
    ``contextMenuAboutToShow(menu, position)`` — position dans le viewport,
    celle qu'attend cursorForPosition."""

    contextMenuAboutToShow = Signal(object, object)

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu(event.pos())
        self.contextMenuAboutToShow.emit(menu, event.pos())
        menu.exec(event.globalPos())
        menu.deleteLater()


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


def build_panel(controller, dock):
    """Crée le contenu du panneau dans ``dock`` et le renvoie. Les widgets
    sont posés comme attributs du contrôleur (search, tabs, thread, input…)
    et leurs signaux branchés sur ses méthodes."""
    container = _ResizeAwareWidget(dock)
    layout = QVBoxLayout(container)
    controller._layout = layout
    controller._spacing = None
    # Marges et espacements ajustés à la taille réelle du panneau
    # (cf. composer_spacing) ; valeurs d'origine posées d'emblée.
    container.resized.connect(controller._apply_spacing)

    # Recherche d'un destinataire : complétion par nom ou initiales ;
    # l'activation ouvre l'onglet de la conversation. Pas de combo
    # éditable : Entrée y insérait un item fantôme (InsertAtBottom) quand
    # le texte ne correspondait à rien.
    controller.search = RecipientSearchEdit(container)
    controller.search.setPlaceholderText("Écrire à… (prénom ou initiales)")
    controller.search.setAccessibleName("Nouvelle conversation")
    controller.search.setToolTip(
        "Écrire à un équipier : nom ou initiales, Entrée ouvre le fil")
    controller.search.setMinimumWidth(0)
    controller.search.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    controller._recipient_model = QStandardItemModel(controller.search)
    controller._recipient_proxy = _RecipientFilterModel(controller.search)
    controller._recipient_proxy.setSourceModel(controller._recipient_model)
    controller._recipient_proxy.setFilterKeyColumn(0)
    completer = QCompleter(controller._recipient_proxy, controller.search)
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    # Le completer filtre lui-même par-dessus le proxy : _SEARCH_ROLE
    # réunit toutes les formes cherchées pour que sa passe n'écarte rien.
    completer.setCompletionRole(_SEARCH_ROLE)
    controller.search.setCompleter(completer)
    controller.search.textChanged.connect(controller._recipient_proxy.set_query)
    controller.search.returnPressed.connect(controller._activate_first_recipient)
    controller.search.escapeRequested.connect(controller._release_focus)
    completer.activated[QModelIndex].connect(controller._recipient_activated)
    layout.addWidget(controller.search)

    # Conversations en cours : « Équipe » permanente, un onglet par fil
    # direct actif ou choisi — plusieurs échanges restent visibles d'un
    # coup d'œil, dans un encombrement vertical minimal.
    controller.tabs = QTabBar(container)
    controller.tabs.setExpanding(False)
    controller.tabs.setUsesScrollButtons(True)
    controller.tabs.setElideMode(Qt.ElideRight)
    controller.tabs.setDrawBase(False)
    controller.tabs.setDocumentMode(True)
    controller.tabs.setTabsClosable(True)
    controller.tabs.setMovable(True)
    controller.tabs.setAccessibleName("Conversations en cours")
    controller.tabs.currentChanged.connect(controller._tab_changed)
    controller.tabs.tabCloseRequested.connect(controller._close_tab)
    # Clic droit sur un onglet : « Marquer comme non lu ».
    controller.tabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    controller.tabs.customContextMenuRequested.connect(controller._show_tab_menu)
    layout.addWidget(controller.tabs)

    # Navigation clavier entre conversations, même geste que les
    # navigateurs : Ctrl+Tab / Ctrl+Maj+Tab, circulaire. Enfants du
    # dock : détruits et recréés avec lui, jamais en double.
    for sequence, step in (("Ctrl+Tab", 1), ("Ctrl+Shift+Tab", -1)):
        shortcut = QShortcut(QKeySequence(sequence), dock)
        shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(
            lambda _step=step: controller._cycle_tab(_step))

    controller.status_label = QLabel("", container)
    controller.status_label.setWordWrap(True)
    controller.status_label.setMinimumWidth(0)
    controller.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    controller.status_label.setAccessibleName("État de la conversation")
    # Vide par défaut : sans texte, le libellé ne réserve pas de ligne.
    controller.status_label.hide()
    layout.addWidget(controller.status_label)

    controller.thread = MessageThread(container)
    controller.thread.setOpenExternalLinks(False)
    # Clic droit sur une de mes bulles récentes : « Retirer ce message ».
    controller.thread.contextMenuAboutToShow.connect(controller._extend_thread_menu)
    controller.thread.setAccessibleName("Messages de la conversation")
    controller.thread.setMinimumWidth(0)
    controller.thread.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
    layout.addWidget(controller.thread, 1)
    # Messages arrivés pendant que l'équipier lisait plus haut dans le
    # fil : on ne le ramène pas de force en bas (il perdrait sa lecture),
    # une barre cliquable le prévient et l'y emmène. Cachée sinon.
    controller.new_messages_button = QPushButton(container)
    controller.new_messages_button.setFlat(True)
    controller.new_messages_button.setCursor(Qt.CursorShape.PointingHandCursor)
    controller.new_messages_button.setMinimumWidth(0)
    controller.new_messages_button.setSizePolicy(
        QSizePolicy.Ignored, QSizePolicy.Fixed)
    controller.new_messages_button.setStyleSheet(
        "QPushButton { background-color:#d97706; color:white; "
        "border-radius:4px; padding:3px; font-weight:bold; }")
    controller.new_messages_button.setToolTip("Aller aux derniers messages")
    controller.new_messages_button.clicked.connect(controller._scroll_to_latest)
    controller.new_messages_button.hide()
    layout.addWidget(controller.new_messages_button)
    # Ascenseur arrivé en bas (défilement manuel ou suivi du dernier
    # message) : point d'armement de l'accusé « Lu ».
    controller.thread.verticalScrollBar().valueChanged.connect(
        controller._thread_reached_bottom)
    # Arrivé tout en haut : les messages plus anciens se chargent à la
    # demande (« charger plus ancien » sans bouton).
    controller.thread.verticalScrollBar().valueChanged.connect(
        controller._maybe_load_older)

    # Portée d'un message « équipe » : tous les équipiers actifs (défaut)
    # ou les connectés seulement. Cachée hors conversation broadcast —
    # sans intérêt pour un message direct — pour garder le dock compact ;
    # sa visibilité suit la sélection (cf. _update_composer).
    controller._online_only = QCheckBox("Connectés seulement", container)
    controller._online_only.setMinimumWidth(0)
    controller._online_only.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    controller._online_only.setToolTip(
        "N'envoyer qu'aux équipiers actuellement connectés")
    controller._online_only.setAccessibleName("Portée du message à l'équipe")
    controller._online_only.hide()
    layout.addWidget(controller._online_only)

    # Ligne de saisie : le champ et un seul petit bouton « ⚡ ». Pas de
    # bouton « Envoyer » (Entrée envoie, le texte d'invite le rappelle)
    # ni de bouton « urgent » (option du menu « ⚡ ») : sur un petit
    # écran, la largeur revient au texte en cours d'écriture.
    composer = QHBoxLayout()
    controller._composer = composer
    controller.input = MessageInput(container)
    controller.input.setPlaceholderText(controller.INPUT_PLACEHOLDER)
    controller.input.setAccessibleName("Texte du message")
    controller.input.setToolTip(controller.INPUT_TOOLTIP)
    controller.input.setMinimumWidth(0)
    controller.input.setMaximumHeight(76)
    controller.input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    controller.input.setTabChangesFocus(True)
    controller.input.textChanged.connect(controller._update_composer)
    controller.input.textChanged.connect(controller._maybe_emit_typing)
    controller.input.sendRequested.connect(controller.send)
    controller.input.escapeRequested.connect(controller._release_focus)
    composer.addWidget(controller.input, 1)
    # Réponses rapides : le menu insère la phrase dans le champ (elle
    # reste modifiable, Entrée envoie) — pas d'envoi direct : un envoi
    # immédiat sous le doigt au comptoir serait une fausse manip.
    controller.quick_button = QToolButton(container)
    controller.quick_button.setText("⚡")
    controller.quick_button.setToolTip(controller.QUICK_BUTTON_TOOLTIP)
    controller.quick_button.setAccessibleName("Réponses rapides")
    controller.quick_button.setAutoRaise(True)
    controller.quick_button.setFixedSize(32, 32)
    controller.quick_button.setPopupMode(
        QToolButton.ToolButtonPopupMode.InstantPopup)
    quick_menu = QMenu(controller.quick_button)
    # Bulles d'aide visibles : l'action de retrait y montre le message
    # visé en entier (le libellé n'en garde qu'un extrait).
    quick_menu.setToolTipsVisible(True)
    controller._quick_menu = quick_menu
    # Les phrases (équipe puis personnelles) sont insérées en tête par
    # _rebuild_quick_menu ; viennent ensuite, fixes, la gestion des
    # phrases personnelles puis le retrait du dernier message.
    controller._quick_manage_action = quick_menu.addAction(
        "Gérer mes phrases rapides…")
    controller._quick_manage_action.triggered.connect(
        controller.open_quick_replies_dialog)
    # « Message urgent » : arme le PROCHAIN envoi en urgent —
    # notification persistante chez le destinataire. Un seul message
    # concerné : l'option se décoche toute seule après l'envoi réussi
    # (et repart décochée à chaque changement de conversation). Armée,
    # elle se voit sur le champ lui-même (cf. _apply_urgent_state).
    quick_menu.addSeparator()
    controller.urgent_action = quick_menu.addAction(controller.URGENT_ACTION_TEXT)
    controller.urgent_action.setCheckable(True)
    controller.urgent_action.setToolTip(
        "Le prochain message s'affichera en notification persistante "
        "chez le destinataire")
    controller.urgent_action.toggled.connect(controller._apply_urgent_state)
    # Retrait du dernier message envoyé par erreur : séparé des
    # insertions de texte — c'est une autre nature d'action, confirmée
    # en deux temps en ligne (même logique que l'envoi « équipe »).
    quick_menu.addSeparator()
    controller._delete_action = quick_menu.addAction(controller.DELETE_ACTION_TEXT)
    controller._delete_action.triggered.connect(controller._request_delete)
    # Libellé (message visé, temps restant ou raison du grisé) et état
    # dépendent du dernier message chargé : réévalués à l'ouverture du
    # menu plutôt qu'à chaque rendu du fil.
    quick_menu.aboutToShow.connect(controller._refresh_delete_action)
    controller.quick_button.setMenu(quick_menu)
    controller._rebuild_quick_menu()
    composer.addWidget(controller.quick_button, 0, Qt.AlignmentFlag.AlignTop)
    layout.addLayout(composer)
    controller._apply_spacing(container.width(), container.height())
    return container
