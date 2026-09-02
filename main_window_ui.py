"""Construction de l'interface de la fenêtre principale (point 10.9).

``MainWindow`` assemblait elle-même ses widgets : treize méthodes ``_create_*``
noyées au milieu du métier, du réseau et des raccourcis. Elles vivent ici, sous
forme de FONCTIONS prenant la fenêtre en argument — l'équivalent d'un fichier
« .ui » écrit à la main.

Convention : ces fonctions POSENT des attributs sur la fenêtre
(``window.btn_next``, ``window.label_patient``…), exactement comme avant ; le
reste de l'application continue de les lire au même endroit. Ce module est donc
un *constructeur* de l'interface, pas une couche d'abstraction : il connaît la
fenêtre, mais la fenêtre n'a plus à connaître Qt en détail.

Le comportement (ce qui se passe au clic) reste dans ``MainWindow`` : les
fonctions ci-dessous se contentent de brancher les widgets sur ses méthodes.
"""

import logging

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QAction, QColor, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView, QDockWidget, QHBoxLayout, QLabel, QListView, QMenu,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

import endpoints
from buttons import DebounceButton, IconeButton
from patient_list_model import PatientListModel
from resources import resource_path

_logger = logging.getLogger("appcomptoir.ui")

class ConnectionStatusIndicator(QWidget):
    # Couleur de teinte par état (pour ceux qui perçoivent la couleur).
    _STATUS_COLOR = {
        "connected": "#2e7d32",     # vert
        "connecting": "#e67e22",    # orange
        "disconnected": "#c0392b",  # rouge
    }
    # Libellé texte par état (nom accessible + infobulle) : l'état ne repose pas
    # que sur la couleur/la forme, il est aussi nommé pour les lecteurs d'écran.
    _STATUS_LABEL = {
        "connected": "Temps réel connecté",
        "connecting": "Reconnexion en cours",
        "disconnected": "Temps réel déconnecté",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self.status = "connected"
        self.last_connection_time = None
        self.reconnection_attempts = 0
        self.setMouseTracking(True)

        # Charger les SVG avec vos noms de fichiers
        self.renderers = {}
        status_files = {
            "connected": "connection_true.svg",
            "connecting": "connection_standing.svg",
            "disconnected": "connection_false.svg"
        }

        for status, filename in status_files.items():
            renderer = QSvgRenderer()
            svg_path = resource_path(f"assets/images/{filename}")
            if renderer.load(svg_path):
                self.renderers[status] = renderer
            else:
                _logger.warning("Erreur lors du chargement de %s", filename)

        self._refresh_accessibility()

    def set_status(self, status, reconnection_attempts=None):
        _logger.debug("Indicateur de connexion : %s", status)
        try:
            if self.isVisible():
                self.status = status
                if status == "connected":
                    self.last_connection_time = QDateTime.currentDateTime()
                    self.reconnection_attempts = 0
                elif reconnection_attempts is not None:
                    self.reconnection_attempts = reconnection_attempts
                self.update_tooltip()
                self.update()
        except RuntimeError:
            pass

    def _status_tooltip(self):
        """Texte d'état complet (base + horodatage/tentatives)."""
        base = self._STATUS_LABEL.get(self.status, self._STATUS_LABEL["disconnected"])
        if self.status == "connected":
            if self.last_connection_time:
                time_str = self.last_connection_time.toString("HH:mm:ss")
                return f"{base} depuis {time_str}"
            return base
        if self.reconnection_attempts > 0:
            return f"{base}\nNombre de tentatives de reconnexion : {self.reconnection_attempts}"
        return base

    def _refresh_accessibility(self):
        """Nom accessible = état courant, pour les lecteurs d'écran (point 28)."""
        label = self._STATUS_LABEL.get(self.status, self._STATUS_LABEL["disconnected"])
        self.setAccessibleName(f"État de la connexion temps réel : {label}")
        self.setAccessibleDescription(self._status_tooltip())

    def update_tooltip(self):
        try:
            if self.isVisible():
                self.setToolTip(self._status_tooltip())
                self._refresh_accessibility()
        except RuntimeError:
            pass

    def paintEvent(self, event):
        try:
            if self.isVisible() and self.status in self.renderers:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.Antialiasing)
                self.renderers[self.status].render(painter, self.rect())

                # Teinte l'icône selon l'état : garde la forme (anti-aliasée) mais
                # la recolore (SourceIn ne peint que là où l'icône a de l'alpha).
                color = self._STATUS_COLOR.get(self.status)
                if color is not None:
                    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
                    painter.fillRect(self.rect(), QColor(color))
                    painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

                # « connecting » utilise le même dessin SVG que « connected » : la
                # couleur seule ne suffirait pas à les distinguer en niveaux de
                # gris. On superpose trois points (badge « en cours ») pour une
                # distinction non colorée.
                if self.status == "connecting":
                    self._paint_progress_dots(painter)
        except RuntimeError:
            pass

    def _paint_progress_dots(self, painter):
        rect = self.rect()
        dot_r = max(1, rect.width() // 12)
        gap = dot_r * 3
        cy = rect.bottom() - dot_r - 1
        cx0 = rect.center().x() - gap
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#1a1a1a"))
        for i in range(3):
            painter.drawEllipse(cx0 + i * gap - dot_r, cy - dot_r, dot_r * 2, dot_r * 2)


def create_interface(window):
    # Supprime l'ancien widget central s'il existe (changement d'orientation)
    if window.centralWidget():
        window.centralWidget().deleteLater()
    window.central_widget = QWidget()
    window.setCentralWidget(window.central_widget)

    window.main_layout = QHBoxLayout(window.central_widget) if window.horizontal_mode else QVBoxLayout(window.central_widget)

    # Créer un widget conteneur pour les éléments principaux
    window.main_elements_container = QWidget() 
    main_elements_layout = QHBoxLayout(window.main_elements_container) if window.horizontal_mode else QVBoxLayout(window.main_elements_container)
    main_elements_layout.setContentsMargins(0, 0, 0, 0)
    main_elements_layout.setSpacing(5)  # Ajustez l'espacement selon vos besoins

    window.central_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    window.main_elements_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    _create_name(window)
    _create_label_patient(window)
    _create_main_button_container(window)
    _create_option_button_container(window)
    _create_icon_widget(window)
    _create_patient_list_widget(window)

    # Ajouter les widgets au conteneur principal
    main_elements_layout.addWidget(window.label_staff)
    main_elements_layout.addWidget(window.label_patient)
    main_elements_layout.addWidget(window.main_button_container)
    main_elements_layout.addWidget(window.option_button_container)

    # Configurer la politique de taille du conteneur principal
    window.main_elements_container.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)

    # Ajouter le conteneur principal et les autres widgets au layout principal
    window.main_layout.addWidget(window.main_elements_container)
    window.main_layout.addWidget(window.icone_widget)

    window.refresh_patient_lists()

    # Ajouter un stretch pour pousser les widgets vers le haut/gauche
    if window.horizontal_mode:
        window.main_layout.addStretch(1)
    else:
        window.main_layout.addStretch(1)

    # Mode panneau compact : resserre les marges/espacements et priorise
    # visuellement les éléments essentiels dans une petite zone (point 25).
    _apply_compact_styling(window)


def _apply_compact_styling(window):
    """Adapte l'interface au mode panneau compact.

    On resserre marges et espacements pour tenir dans une petite zone, et on
    garantit une hauteur minimale confortable aux boutons essentiels (Suivant/
    Valider/Pause) pour qu'aucun ne soit tronqué et qu'ils restent lisibles.
    En mode normal, on rétablit des valeurs standard (au cas où on quitte le
    mode compact sans reconstruire depuis zéro)."""
    compact = getattr(window, "compact_mode", False)
    margin = 4 if compact else 9
    spacing = 4 if compact else 6
    window.main_layout.setContentsMargins(margin, margin, margin, margin)
    window.main_layout.setSpacing(spacing)
    if hasattr(window, "main_button_layout"):
        window.main_button_layout.setSpacing(spacing)
    # Hauteur minimale des boutons essentiels : lisibles et cliquables même
    # dans un panneau étroit, sans être tronqués.
    min_h = 44 if compact else 0
    for name in ("btn_next", "btn_validate", "btn_pause"):
        button = getattr(window, name, None)
        if button is not None:
            button.setMinimumHeight(min_h)


def _create_name(window):
    window.label_staff = QLabel("")
    window.label_staff.setAlignment(Qt.AlignCenter)


def _create_label_patient(window):
    # Remplacer QLabel par QPushButton
    window.label_patient = QPushButton("Pas de connexion !")
    window.label_patient.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
    window.label_patient.setMinimumWidth(0)
    window.label_patient.setStyleSheet("text-align: left;")
    window.label_patient.setCheckable(False)  # Le bouton n'est pas "toggle"
    window.label_patient.setFlat(True)  # Le bouton ressemble davantage à un label

    # Créer un menu d'actions
    window.patient_menu = QMenu(window.label_patient)  # Stocké comme attribut de classe
    window.action_wait = window.patient_menu.addAction("Remettre en attente")
    
    # on ne crée le sous-menu que si on a défini des "activités Staff"
    if hasattr(window, 'activities_staff') and window.activities_staff:
        # Créer un sous-menu pour "Remettre en attente pour..."
        window.wait_for_submenu = QMenu("Remettre en attente pour...", window.patient_menu)
        
        # Ajouter chaque activité staff comme une action dans le sous-menu
        for activity in window.activities_staff:
            action = window.wait_for_submenu.addAction(activity['name'])
            action.triggered.connect(lambda checked, a=activity: window.on_action_wait_for(a))
        
        # Ajouter le sous-menu au menu principal
        window.patient_menu.addMenu(window.wait_for_submenu)

    window.action_delete = window.patient_menu.addAction("Supprimer")

    # Connecter les actions à des méthodes
    window.action_wait.triggered.connect(window.on_action_wait)
    window.action_delete.triggered.connect(window.on_action_delete)

    # Associer le menu au bouton
    window.label_patient.setMenu(window.patient_menu)

    # Désactiver les actions par défaut
    window._update_menu_actions(False)


def _create_main_button_container(window):
    window.main_button_container = QWidget()
    window.main_button_layout = QHBoxLayout() if window.horizontal_mode else QVBoxLayout()

    buttons_config = [
        ("btn_next", "Suivant", window.next_patient_shortcut, window.call_web_function_validate_and_call_next),
        ("btn_validate", "Valider", window.validate_patient_shortcut, window.call_web_function_validate),
        ("btn_pause", "Pause", window.pause_shortcut, window.call_web_function_pause)
    ]

    for attr_name, text, shortcut, callback in buttons_config:
        base_label = f"{text}\n{shortcut}"
        button = DebounceButton(base_label)
        # Nom accessible = action (sans le raccourci), infobulle explicite :
        # utile pour les lecteurs d'écran et au survol (point 28).
        button.setAccessibleName(text)
        button.setToolTip(f"{text} (raccourci : {shortcut})")
        # Libellé de base mémorisé : permet de restaurer le texte après un
        # marquage d'alerte (bouton Valider) sans reconstruire le bouton.
        button._base_label = base_label
        button.clicked.connect(callback)
        setattr(window, attr_name, button)  # Stocke le bouton comme attribut de la classe
        window.main_button_layout.addWidget(button)

    window.main_button_container.setLayout(window.main_button_layout)


def _create_option_button_container(window):
    
    window.option_button_container = QWidget()
    window.option_button_layout = QHBoxLayout() if window.horizontal_mode else QVBoxLayout()

    _create_choose_patient_button(window)
    _create_more_button(window)

    window.option_button_layout.addWidget(window.btn_choose_patient)
    window.option_button_layout.addWidget(window.btn_more)

    window.option_button_container.setLayout(window.option_button_layout)


def _create_icon_widget(window):
    window.icone_widget = QWidget()
    window.icone_layout = QHBoxLayout()

    window.connection_indicator = ConnectionStatusIndicator()
    window.icone_layout.addWidget(window.connection_indicator)
    
    _create_auto_calling_button(window)
    _create_paper_button(window)

    window.icone_layout.addWidget(window.btn_auto_calling)
    window.icone_layout.addWidget(window.btn_paper)

    window.icone_widget.setLayout(window.icone_layout)       


def _create_icon_button(window, icon_path, icon_inactive_path, flask_url, tooltip_text, tooltip_inactive_text, state, is_always_visible=True, accessible_name=None):
    return IconeButton(
        icon_path=resource_path(icon_path),
        icon_inactive_path=resource_path(icon_inactive_path),
        flask_url=flask_url,
        tooltip_text=tooltip_text,
        tooltip_inactive_text=tooltip_inactive_text,
        state=state,
        parent=window,
        is_always_visible=is_always_visible,
        accessible_name=accessible_name,
    )


def _create_auto_calling_button(window):
    window.logger.info("Connexion pour charger le bouton d'appel automatique...")
    window.btn_auto_calling = _create_icon_button(window, 
        "assets/images/loop_yes.ico",
        "assets/images/loop_no.ico",
        endpoints.auto_calling(window.web_url),
        "Desactiver l'appel automatique",
        "Activer l'appel automatique",
        window.autocalling,
        accessible_name="Appel automatique des patients"
    )


def _create_paper_button(window):
    window.logger.info("Connexion pour charger l'icone de changement de papier...")
    window.btn_paper = _create_icon_button(window, 
        "assets/images/paper_add.ico",
        "assets/images/paper.ico",
        endpoints.paper_add(window.web_url),
        "Indiquer que vous avez changé le papier",
        "Indiquer qu'il faut changer le papier",
        window.add_paper,
        is_always_visible=False,
        accessible_name="État du papier de l'imprimante")


def _create_choose_patient_button(window):
    window.btn_choose_patient = DebounceButton("Patients")
    window.choose_patient_menu = QMenu()
    # Menu reconstruit paresseusement à l'ouverture (plus de reconstruction
    # à chaque évènement de file) : il lit window.list_patients courant.
    window.choose_patient_menu.aboutToShow.connect(window._rebuild_choose_patient_menu)
    window.btn_choose_patient.setMenu(window.choose_patient_menu)

    # window.my_patient/window.list_patients sont normalement déjà remplis par
    # _on_startup_ready() (StartupWorker) avant le premier appel à cette
    # méthode. Ce qui suit est un filet de sécurité (ex: reconstruction de
    # l'interface après un changement d'orientation) au cas où ils seraient
    # encore vides, pas le chemin normal de démarrage.
    if not window.my_patient:
        window.logger.info("__ Connexion pour charger le patient en cours...")
        window.my_patient = window.init_patient()
    # uniquement si chargement des patients réussi (pas de connexion)
    if window.my_patient:
        window.update_my_patient(window.my_patient)
        window.update_my_buttons(window.my_patient)

    if not window.list_patients:
        window.logger.info("__ Connexion pour charger la liste des patients...")
        window.list_patients = window.init_list_patients()
    # Le menu est reconstruit à son ouverture ; ici on ne fait qu'actualiser
    # le compteur visible du bouton. La vue (modèle) est mise à jour plus tard
    # dans create_interface, une fois _create_patient_list_widget appelé.
    window._update_patient_count_label()


def _create_more_button(window):
    window.btn_more = DebounceButton("Menu")
    window.more_menu = QMenu()

    # Créer l'action pour le papier séparément pour pouvoir la mettre à jour
    window.paper_action = QAction("Changement papier nécessaire", window)
    window.paper_action.triggered.connect(window.trigger_paper_button)
    window.update_paper_action_text(window.add_paper)  # Mettre à jour le texte initial

    actions = [
        ("Relancer l'appel ", window.recall_shortcut, window.recall),
        (None, None, window.paper_action),
        ("Changer l'orientation", None, window.toggle_orientation),
        ("Basculer le mode compact", None, window.toggle_compact_mode),
        ("Deconnexion ", window.deconnect_shortcut, window.deconnection),
        ("Préférences", None, window.show_preferences_dialog),
        ("Afficher/Masquer Liste Patients", None, window.toggle_patient_list),
        ("Réinitialiser la position", None, window.placement.reset),
    ]

    for text, shortcut, callback in actions:
        if isinstance(callback, QAction):  # Si c'est déjà une action
            window.more_menu.addAction(callback)
        else:
            action = QAction(f"{text}{shortcut if shortcut else ''}", window)
            action.triggered.connect(callback)
            window.more_menu.addAction(action)

    window.btn_more.setMenu(window.more_menu)


def _create_patient_list_widget(window):
    # Create the dock widget if it doesn't exist
    if not hasattr(window, 'patient_list_dock'):
        # Create the dock widget
        window.patient_list_dock = QDockWidget("Liste des patients", window)
        window.patient_list_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea | Qt.BottomDockWidgetArea)
        
        # Create main container widget
        container_widget = QWidget()
        container_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # Utiliser un QVBoxLayout pour le conteneur principal
        container_layout = QVBoxLayout(container_widget)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Vue de la file : QListView adossé à un QAbstractListModel. La vue
        # virtualise le rendu (seuls les éléments visibles sont peints) et les
        # mises à jour du modèle sont différentielles — pas de reconstruction
        # complète ni de perte de la position de défilement (cf. point 21).
        window.patient_model = PatientListModel(window, font_size=window.patient_list_font_size)
        window.patient_list_view = QListView()
        window.patient_list_view.setModel(window.patient_model)
        window.patient_list_view.setUniformItemSizes(True)  # perf avec beaucoup d'éléments
        # Navigation clavier (point 28) : la liste devient focusable et
        # sélectionnable au clavier (Tab pour l'atteindre, flèches pour se
        # déplacer, Entrée pour appeler le patient, touche Menu pour le menu
        # contextuel). L'ancien NoSelection empêchait toute navigation clavier.
        window.patient_list_view.setSelectionMode(QAbstractItemView.SingleSelection)
        window.patient_list_view.setFocusPolicy(Qt.StrongFocus)
        window.patient_list_view.setTabKeyNavigation(True)
        window.patient_list_view.setAccessibleName("Liste des patients en attente")
        window.patient_list_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        window.patient_list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        window.patient_list_view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        window.patient_list_view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        window.patient_list_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Clic (souris) sur un patient : même action que l'ancien PatientButton.
        window.patient_list_view.clicked.connect(window._on_patient_list_clicked)
        # `activated` couvre l'équivalent clavier (Entrée sur la ligne courante).
        window.patient_list_view.activated.connect(window._on_patient_list_activated)
        # Menu contextuel par patient conservé, désormais porté par la vue.
        # Avec CustomContextMenu, la touche « Menu » du clavier déclenche aussi
        # ce signal sur la ligne sélectionnée.
        window.patient_list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        window.patient_list_view.customContextMenuRequested.connect(
            window._on_patient_list_context_menu)

        container_layout.addWidget(window.patient_list_view, 1)  # priorité d'expansion
        
        # Set the container as the dock widget's content
        window.patient_list_dock.setWidget(container_widget)
        
        # Add dock widget to main window
        window.addDockWidget(Qt.RightDockWidgetArea, window.patient_list_dock)
        
        # Adjust minimum size
        window.patient_list_dock.setMinimumHeight(100)
        
        # Remove borders and make it look cleaner
        window.patient_list_dock.setStyleSheet("""
            QDockWidget {
                border: none;
                padding: 0;
            }
            QListView {
                border: none;
            }
        """)
    
    # Update visibility based on preferences
    window.patient_list_dock.setVisible(window.display_patient_list)
    
    # Adjust dock widget position based on preferences
    if (window.horizontal_mode and window.patient_list_position_horizontal == "bottom") or \
        (not window.horizontal_mode and window.patient_list_position_vertical == "bottom"):
        window.addDockWidget(Qt.BottomDockWidgetArea, window.patient_list_dock)
    elif (window.horizontal_mode and window.patient_list_position_horizontal == "right") or \
        (not window.horizontal_mode and window.patient_list_position_vertical == "right"):
        window.addDockWidget(Qt.RightDockWidgetArea, window.patient_list_dock)
