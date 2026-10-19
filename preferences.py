import logging

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QListWidget, QListWidgetItem, QStackedWidget, QWidget, QVBoxLayout, QCheckBox, QPushButton, QLabel, QMessageBox, QComboBox, QSpinBox, QSlider, QGridLayout, QScrollArea, QGroupBox, QMenu
from PySide6.QtCore import Signal, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
import resources
from secret_store import secret_storage
from shortcut_defaults import (
    default_shortcut, join_shortcut, read_shortcut, split_shortcut,
)
from shortcut_edit import ShortcutEdit
import notification_rules
import profile_sync
import settings_schema
from counter_id_utils import coerce_counter_id
from panel_layout import (
    MIN_PANEL_THICKNESS, MAX_PANEL_THICKNESS, DEFAULT_PANEL_THICKNESS,
)
from shortcut_config import (
    MODE_DISABLED, MODE_FOCUSED, MODE_GLOBAL,
    ACTION_LABELS, find_duplicate_shortcuts, find_invalid_shortcuts,
    INVALID_EMPTY, INVALID_LONE_MODIFIER, INVALID_UNKNOWN_KEY,
)
from station_dialog import StationDialog, secret_status_text, sync_and_verify
from accessibility import (
    MIN_FONT_POINT_SIZE, TONE_HUMOROUS, TONE_SOBER,
)
from audio import SOUNDS
from my_logger import default_log_dir

logger = logging.getLogger("appcomptoir.preferences")

#: Libellés affichés pour chaque son dans le sélecteur « Tester un son ».
#: Les clés viennent de ``audio.SOUNDS`` : si un son est ajouté côté lecteur,
#: il apparaît automatiquement ici (avec son nom technique si aucun libellé
#: convivial n'est défini).
SOUND_LABELS = {
    "ding": "Ding (nouveau patient)",
    "patient_taken": "Patient déjà pris",
    "please_validate": "Pensez à valider",
}


# Constants for UI texts and corresponding values
BOTTOM_TEXT = "Bas"
RIGHT_TEXT = "Droite"
POSITION_MAPPING = {
    BOTTOM_TEXT: "bottom",
    RIGHT_TEXT: "right"
}
REVERSE_POSITION_MAPPING = {v: k for k, v in POSITION_MAPPING.items()}


class PreferencesDialog(QDialog):
    # Émis après chaque persistance réussie déclenchée par « Appliquer » (le
    # dialogue reste ouvert). « Enregistrer » n'émet rien : son Accepted sert
    # déjà de déclencheur unique d'apply_preferences côté fenêtre.
    applied = Signal()
    # Émis quand la configuration du poste (serveur, secret, comptoir) vient
    # d'être enregistrée depuis la page « Poste » : la fenêtre l'applique tout
    # de suite (reconnexion des services), indépendamment du formulaire des
    # préférences, qui reste ouvert et non enregistré.
    station_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Préférences")

        # Volume enregistré (celui du lecteur principal à l'ouverture) et drapeau
        # « un aperçu a modifié le volume du lecteur ». Le bouton « Tester la
        # notification » joue le son au volume EN COURS D'ÉDITION : sans ce
        # rappel, fermer les préférences sans enregistrer laissait le lecteur au
        # volume d'essai (le thème, lui, était bien restauré).
        self.current_volume = None
        # Mode muet enregistré : restauré lui aussi si l'utilisateur ferme sans
        # enregistrer (l'aperçu applique le volume ET le mode muet en cours
        # d'édition, sinon « Tester » resterait muet après avoir décoché).
        self.current_muted = None
        self._volume_previewed = False
        # Même principe pour le skin : « un aperçu a changé le skin affiché ».
        # Sans aperçu, l'annulation ne réapplique rien (inutile de recalculer
        # le style de toute l'application).
        self._skin_previewed = False

        # Layout vertical : le contenu (navigation + page courante) au-dessus,
        # la barre de boutons Enregistrer/Annuler en bas — auparavant le seul
        # bouton « Enregistrer » était pleine hauteur, collé à droite.
        self.main_layout = QVBoxLayout(self)
        self.content_layout = QHBoxLayout()
        self.main_layout.addLayout(self.content_layout)

        self.navigation_list = QListWidget()
        self.navigation_list.setFixedWidth(150)
        # currentItemChanged (et non itemClicked) : les flèches du clavier et le
        # Tab changent aussi de page, pas seulement un clic souris.
        self.navigation_list.currentItemChanged.connect(self.change_page)
        
        # « Session » : choix personnels liés à la connexion de l'équipier.
        # « Poste » (en dernier, rarement utile) : configuration de la machine
        # (serveur, secret, comptoir), en lecture seule ici — sa modification
        # passe par une fenêtre dédiée, après avertissement.
        self.general_item = QListWidgetItem("Général")
        self.session_item = QListWidgetItem("Session")
        self.raccourcis_item = QListWidgetItem("Raccourcis")
        self.notifications_item = QListWidgetItem("Notifications")
        self.station_item = QListWidgetItem("Poste")
        # Ordre = index des pages dans le QStackedWidget (cf. change_page).
        self._page_items = [self.general_item, self.session_item,
                            self.raccourcis_item, self.notifications_item,
                            self.station_item]
        for item in self._page_items:
            self.navigation_list.addItem(item)

        self.content_layout.addWidget(self.navigation_list)
        
        self.stacked_widget = QStackedWidget()

        self.general_page, self.general_layout = self._scrollable_page()

        # --- Fenêtre ---
        window_box = QGroupBox("Fenêtre", self.general_page)
        window_box_layout = QVBoxLayout(window_box)
        self.general_layout.addWidget(window_box)

        self.always_on_top_checkbox = QCheckBox("Toujours au premier plan", window_box)
        window_box_layout.addWidget(self.always_on_top_checkbox)

        # « vertical_mode » est une clé QSettings historique : elle porte en
        # réalité le mode HORIZONTAL de l'app (QHBoxLayout = barre large).
        # La case dit donc « horizontale » — l'ancien libellé « verticale »
        # produisait l'effet inverse de ce qu'il annonçait.
        self.horizontal_mode_checkbox = QCheckBox("Orientation horizontale", window_box)
        window_box_layout.addWidget(self.horizontal_mode_checkbox)

        # Réinitialise taille/position de la fenêtre (utile si elle est perdue
        # hors écran après un changement de moniteur). Voir point 24.
        self.reset_position_button = QPushButton("Réinitialiser la position de la fenêtre", window_box)
        self.reset_position_button.clicked.connect(self._reset_window_position)
        window_box_layout.addWidget(self.reset_position_button)

        # --- Panneau compact (point 25) ---
        panel_box = QGroupBox("Panneau compact", self.general_page)
        panel_box_layout = QVBoxLayout(panel_box)
        self.general_layout.addWidget(panel_box)

        # Panneau étroit docké sur un bord (colonne verticale ou barre
        # horizontale) plutôt qu'une fenêtre générique.
        self.compact_mode_checkbox = QCheckBox("Mode panneau compact (docké sur un bord)", panel_box)
        panel_box_layout.addWidget(self.compact_mode_checkbox)

        self.panel_snap_checkbox = QCheckBox("Magnétisme aux bords de l'écran", panel_box)
        panel_box_layout.addWidget(self.panel_snap_checkbox)

        self.panel_thickness_layout = QHBoxLayout()
        self.panel_thickness_label = QLabel("Épaisseur du panneau (px):", panel_box)
        self.panel_thickness_spinbox = QSpinBox(panel_box)
        self.panel_thickness_spinbox.setRange(MIN_PANEL_THICKNESS, MAX_PANEL_THICKNESS)
        self.panel_thickness_spinbox.setSingleStep(10)
        self.panel_thickness_layout.addWidget(self.panel_thickness_label)
        self.panel_thickness_layout.addWidget(self.panel_thickness_spinbox)
        panel_box_layout.addLayout(self.panel_thickness_layout)

        # Préréglage « Panneau latéral » : pré-remplit les réglages
        # recommandés pour un usage à côté du logiciel métier. Simple
        # pré-remplissage des cases — rien n'est écrit sans « Enregistrer ».
        self.side_panel_preset_button = QPushButton(
            "Préréglage « Panneau latéral » (étroit, docké, toujours visible)",
            panel_box)
        self.side_panel_preset_button.setToolTip(
            "Coche : mode panneau compact, magnétisme, épaisseur "
            f"{DEFAULT_PANEL_THICKNESS} px, toujours au premier plan, "
            "liste sous le panneau. Appliqué seulement à l'enregistrement.")
        self.side_panel_preset_button.setAccessibleName(
            "Appliquer le préréglage panneau latéral")
        self.side_panel_preset_button.clicked.connect(self._apply_side_panel_preset)
        panel_box_layout.addWidget(self.side_panel_preset_button)

        # --- Icônes ---
        icons_box = QGroupBox("Icônes", self.general_page)
        icons_box_layout = QVBoxLayout(icons_box)
        self.general_layout.addWidget(icons_box)

        # Une seule icône regroupée dans la zone de notification au lieu des
        # trois icônes d'action — moins d'encombrement pour un panneau latéral
        # déjà discret (les actions restent accessibles par clic droit).
        self.tray_single_icon_checkbox = QCheckBox(
            "Une seule icône dans la zone de notification", icons_box)
        self.tray_single_icon_checkbox.setToolTip(
            "Regroupe Pause / Prochain patient / Valider sous une seule icône "
            "avec menu contextuel (clic gauche = afficher le panneau).")
        icons_box_layout.addWidget(self.tray_single_icon_checkbox)

        # Bouton-icône « appel automatique » : masqué par défaut pour gagner de
        # la place — l'action reste dans le menu « Menu », et le bouton
        # réapparaît de lui-même tant que l'appel automatique est actif.
        self.show_auto_call_button_checkbox = QCheckBox(
            "Afficher le bouton d'appel automatique", icons_box)
        self.show_auto_call_button_checkbox.setToolTip(
            "Épingle l'icône d'appel automatique dans le panneau. Décoché : "
            "l'action reste dans le menu « Menu » et l'icône n'apparaît que "
            "tant que l'appel automatique est actif.")
        icons_box_layout.addWidget(self.show_auto_call_button_checkbox)

        # --- Liste des patients et messagerie ---
        lists_box = QGroupBox("Liste des patients et messagerie", self.general_page)
        lists_box_layout = QVBoxLayout(lists_box)
        self.general_layout.addWidget(lists_box)

        self.display_patient_list = QCheckBox("Liste des patients", lists_box)
        lists_box_layout.addWidget(self.display_patient_list)

        self.patient_list_position_vertical_label = QLabel("Position de la liste des patients en mode verticale:", lists_box)
        lists_box_layout.addWidget(self.patient_list_position_vertical_label)

        self.patient_list_position_vertical = QComboBox(lists_box)
        self.patient_list_position_vertical.addItems([BOTTOM_TEXT, RIGHT_TEXT])
        lists_box_layout.addWidget(self.patient_list_position_vertical)

        self.patient_list_position_horizontal_label = QLabel("Position de la liste des patients en mode horizontal:", lists_box)
        lists_box_layout.addWidget(self.patient_list_position_horizontal_label)

        self.patient_list_position_horizontal = QComboBox(lists_box)
        self.patient_list_position_horizontal.addItems([BOTTOM_TEXT, RIGHT_TEXT])
        lists_box_layout.addWidget(self.patient_list_position_horizontal)

        # Position de la messagerie : même mécanisme que la liste des patients.
        # Du même côté que la file, les deux panneaux suivent la disposition
        # choisie dans « Menu › Disposition des panneaux » (empilés ou en
        # onglets) ; choisir un autre côté la détache.
        self.messaging_position_label = QLabel(
            "Position de la messagerie :", lists_box)
        lists_box_layout.addWidget(self.messaging_position_label)
        self.messaging_position = QComboBox(lists_box)
        self.messaging_position.addItems([BOTTOM_TEXT, RIGHT_TEXT])
        self.messaging_position.setToolTip(
            "Du même côté que la liste des patients, les deux panneaux sont "
            "empilés ou regroupés en onglets (Menu › Disposition des panneaux) ; "
            "de l'autre côté, la messagerie a sa propre zone.")
        lists_box_layout.addWidget(self.messaging_position)

        # Taille de police de la file des patients (point 28) : configurable, avec
        # un plancher de lisibilité (l'ancienne valeur figée de 8 pt était petite).
        self.patient_list_font_size_layout = QHBoxLayout()
        self.patient_list_font_size_label = QLabel("Taille de police de la liste:", lists_box)
        self.patient_list_font_size_spinbox = QSpinBox(lists_box)
        self.patient_list_font_size_spinbox.setRange(MIN_FONT_POINT_SIZE, 24)
        self.patient_list_font_size_layout.addWidget(self.patient_list_font_size_label)
        self.patient_list_font_size_layout.addWidget(self.patient_list_font_size_spinbox)
        lists_box_layout.addLayout(self.patient_list_font_size_layout)

        # --- Apparence ---
        appearance_box = QGroupBox("Apparence", self.general_page)
        appearance_box_layout = QVBoxLayout(appearance_box)
        self.general_layout.addWidget(appearance_box)

        self.skin_label = QLabel("Sélectionner un skin:", appearance_box)
        appearance_box_layout.addWidget(self.skin_label)

        # L'aperçu n'est branché qu'APRÈS le chargement des préférences (fin de
        # __init__) : remplir la liste ou y sélectionner le skin enregistré ne
        # doit rien appliquer — auparavant le skin était retiré puis remis à
        # chaque ouverture du dialogue.
        self.skin_combo = QComboBox(appearance_box)
        appearance_box_layout.addWidget(self.skin_combo)

        # --- Journal ---
        log_box = QGroupBox("Journal", self.general_page)
        log_box_layout = QVBoxLayout(log_box)
        self.general_layout.addWidget(log_box)

        self.debug_window = QCheckBox("Garder ouverte la fenêtre de log après le démarrage", log_box)
        log_box_layout.addWidget(self.debug_window)
        self.open_logs_button = QPushButton("Ouvrir le dossier des journaux", log_box)
        self.open_logs_button.setToolTip(
            "Ouvre l'explorateur sur les fichiers de log de l'application.")
        self.open_logs_button.clicked.connect(self._open_log_folder)
        log_box_layout.addWidget(self.open_logs_button)

        self.general_layout.addStretch()

        self.stacked_widget.addWidget(self.general_page)

        self.content_layout.addWidget(self.stacked_widget)

        # --- Session : choix personnels liés à la connexion de l'équipier ---
        # (la configuration du POSTE — serveur, secret, comptoir — est dans la
        # page « Poste », en lecture seule).
        self.session_page, self.session_layout = self._scrollable_page()

        # Reconnexion automatique au démarrage : les postes sont le plus
        # souvent occupés par le même équipier, on évite la ressaisie du code.
        self.reconnect_last_user_checkbox = QCheckBox(
            "Au prochain démarrage, ouvrir la session avec le même utilisateur",
            self.session_page)
        self.reconnect_last_user_checkbox.setToolTip(
            "Mémorise les initiales du dernier équipier connecté et tente une "
            "connexion automatique au lancement de l'application.\n"
            "En cas d'échec, l'écran de connexion s'affiche normalement.")
        self.session_layout.addWidget(self.reconnect_last_user_checkbox)

        # --- Profil équipier sur le serveur ---
        # Les réglages personnels (raccourcis, notifications, disposition,
        # volume…) peuvent être enregistrés côté serveur et récupérés depuis
        # n'importe quel poste — jamais l'identité du poste (URL, comptoir,
        # secret). La politique de récupération voyage DANS le profil : elle
        # suit la personne, pas la machine.
        self.profile_box = QGroupBox(
            "Mon profil sur le serveur", self.session_page)
        profile_box_layout = QVBoxLayout(self.profile_box)
        self.session_layout.addWidget(self.profile_box)

        self.profile_policy_label = QLabel(
            "Récupération de mes préférences :", self.profile_box)
        profile_box_layout.addWidget(self.profile_policy_label)

        self.profile_policy_combo = QComboBox(self.profile_box)
        # (libellé affiché, valeur enregistrée) ; « ask » est le défaut schéma.
        for label, value in (
            ("Automatique à chaque connexion", "auto"),
            ("Sur demande (me le proposer)", "ask"),
            ("Jamais", "never"),
        ):
            self.profile_policy_combo.addItem(label, value)
        self.profile_policy_combo.setToolTip(
            "À votre connexion, si un profil vous est enregistré sur le "
            "serveur :\n"
            "• Automatique : vos réglages sont récupérés sans question ;\n"
            "• Sur demande : on vous propose de les récupérer ;\n"
            "• Jamais : rien n'est proposé (les boutons ci-dessous restent "
            "disponibles).\n"
            "La politique enregistrée dans votre profil suit la personne "
            "et prime sur ce choix local.")
        profile_box_layout.addWidget(self.profile_policy_combo)

        self.profile_buttons_layout = QHBoxLayout()
        self.profile_send_button = QPushButton(
            "Envoyer mes préférences au serveur", self.profile_box)
        self.profile_send_button.setToolTip(
            "Enregistre les réglages PERSONNELS affichés dans cette fenêtre "
            "sur votre profil serveur.\nNi l'adresse du serveur, ni le "
            "comptoir, ni le secret ne sont envoyés.")
        self.profile_send_button.clicked.connect(self._send_profile)
        self.profile_fetch_button = QPushButton(
            "Récupérer mes préférences du serveur", self.profile_box)
        self.profile_fetch_button.setToolTip(
            "Charge votre profil dans le formulaire, sans l'enregistrer : "
            "validez ensuite par « Enregistrer » ou « Appliquer ».")
        self.profile_fetch_button.clicked.connect(self._fetch_profile)
        self.profile_buttons_layout.addWidget(self.profile_send_button)
        self.profile_buttons_layout.addWidget(self.profile_fetch_button)
        profile_box_layout.addLayout(self.profile_buttons_layout)

        # Résultat des opérations profil (envoi/récupération).
        self.profile_status_label = QLabel("", self.profile_box)
        self.profile_status_label.setWordWrap(True)
        self.profile_status_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse)
        profile_box_layout.addWidget(self.profile_status_label)

        self.session_layout.addStretch()
        
        self.stacked_widget.addWidget(self.session_page)
        
        self.raccourcis_page, self.raccourcis_layout = self._scrollable_page()

        # --- Mode des raccourcis (point 27) ---
        self.shortcut_mode_label = QLabel("Mode des raccourcis:", self.raccourcis_page)
        self.raccourcis_layout.addWidget(self.shortcut_mode_label)
        self.shortcut_mode_combo = QComboBox(self.raccourcis_page)
        # (libellé affiché, valeur enregistrée)
        self.shortcut_mode_options = [
            ("Désactivés", MODE_DISABLED),
            ("Actifs seulement si PharmaFile est au premier plan", MODE_FOCUSED),
            ("Globaux (tout le système)", MODE_GLOBAL),
        ]
        for label, value in self.shortcut_mode_options:
            self.shortcut_mode_combo.addItem(label, value)
        self.raccourcis_layout.addWidget(self.shortcut_mode_combo)

        self.confirm_sensitive_checkbox = QCheckBox(
            "Confirmer les actions sensibles (déconnexion) déclenchées par raccourci",
            self.raccourcis_page)
        self.raccourcis_layout.addWidget(self.confirm_sensitive_checkbox)

        self.shortcut_feedback_checkbox = QCheckBox(
            "Afficher brièvement l'action déclenchée par raccourci", self.raccourcis_page)
        self.raccourcis_layout.addWidget(self.shortcut_feedback_checkbox)

        self.next_patient_shortcut_label = QLabel("Raccourci - Patient suivant:", self.raccourcis_page)
        self.raccourcis_layout.addWidget(self.next_patient_shortcut_label)
        
        self.next_patient_shortcut_input = self.create_shortcut_input(
            "Raccourci patient suivant")
        self.raccourcis_layout.addWidget(self.next_patient_shortcut_input)
        
        self.validate_patient_shortcut_label = QLabel("Raccourci - Valider patient:", self.raccourcis_page)
        self.raccourcis_layout.addWidget(self.validate_patient_shortcut_label)

        self.validate_patient_shortcut_input = self.create_shortcut_input(
            "Raccourci valider patient")
        self.raccourcis_layout.addWidget(self.validate_patient_shortcut_input)
        
        self.pause_shortcut_label = QLabel("Raccourci - Pause:", self.raccourcis_page)
        self.raccourcis_layout.addWidget(self.pause_shortcut_label)
        
        self.pause_shortcut_input = self.create_shortcut_input(
            "Raccourci pause")
        self.raccourcis_layout.addWidget(self.pause_shortcut_input)
        
        self.recall_label = QLabel("Raccourci - Rappel patient:", self.raccourcis_page)
        self.raccourcis_layout.addWidget(self.recall_label)
        
        self.recall_shortcut_input = self.create_shortcut_input(
            "Raccourci rappel patient")
        self.raccourcis_layout.addWidget(self.recall_shortcut_input)
        
        self.deconnect_label = QLabel("Raccourci - Déconnexion:", self.raccourcis_page)
        self.raccourcis_layout.addWidget(self.deconnect_label)
        
        self.deconnect_input = self.create_shortcut_input(
            "Raccourci déconnexion")
        self.raccourcis_layout.addWidget(self.deconnect_input)
        
        self.raccourcis_layout.addStretch()
        
        self.stacked_widget.addWidget(self.raccourcis_page)

        self.notifications_page, self.notifications_layout = self._scrollable_page()
        
        # Une ligne PAR CATÉGORIE de notification, avec deux réglages distincts :
        # « Afficher » et « Son ». Chaque catégorie est indépendante : décocher
        # l'une ne fait plus taire les autres (auparavant « activités
        # spécifiques » servait de filtre général à toutes les notifications).
        self.notification_categories_label = QLabel(
            "Notifications par catégorie :", self.notifications_page)
        self.notifications_layout.addWidget(self.notification_categories_label)

        self.notification_categories_grid = QGridLayout()
        self.notification_categories_grid.setColumnStretch(0, 1)
        self.notification_categories_grid.addWidget(
            QLabel("Afficher", self.notifications_page), 0, 1, Qt.AlignHCenter)
        self.notification_categories_grid.addWidget(
            QLabel("Son", self.notifications_page), 0, 2, Qt.AlignHCenter)
        self.notification_categories_grid.addWidget(
            QLabel("Écouter", self.notifications_page), 0, 3, Qt.AlignHCenter)

        # catégorie -> case à cocher (remplies/relues par load/save_preferences).
        self.notification_display_checkboxes = {}
        self.notification_sound_checkboxes = {}
        self.notification_listen_buttons = {}
        for row, category in enumerate(notification_rules.CATEGORIES, start=1):
            label_text = notification_rules.CATEGORY_LABELS[category]
            hint = notification_rules.CATEGORY_HINTS[category]
            label = QLabel(label_text, self.notifications_page)
            label.setWordWrap(True)
            label.setToolTip(hint)
            self.notification_categories_grid.addWidget(label, row, 0)
            for column, (boxes, action) in enumerate(
                    ((self.notification_display_checkboxes, "Afficher"),
                     (self.notification_sound_checkboxes, "Jouer un son pour")), start=1):
                # Case sans texte (le libellé est dans la colonne 0) : on lui donne
                # un nom accessible explicite pour les lecteurs d'écran.
                checkbox = QCheckBox(self.notifications_page)
                checkbox.setAccessibleName(f"{action} : {label_text}")
                checkbox.setToolTip(f"{action} — {hint}")
                self.notification_categories_grid.addWidget(checkbox, row, column, Qt.AlignHCenter)
                boxes[category] = checkbox
            # « Écouter » par ligne : joue le son représentatif de la catégorie,
            # au volume en cours d'édition — sans chercher le bon son dans le
            # sélecteur « Tester un son ».
            listen = QPushButton("Jouer", self.notifications_page)
            listen.setAccessibleName(f"Écouter le son : {label_text}")
            listen.setToolTip(f"Joue le son de « {label_text} » — {hint}")
            listen.clicked.connect(
                lambda _checked=False, c=category: self._test_category_sound(c))
            self.notification_categories_grid.addWidget(
                listen, row, 3, Qt.AlignHCenter)
            self.notification_listen_buttons[category] = listen
        self.notifications_layout.addLayout(self.notification_categories_grid)

        # Ajout de l'option pour le temps pour une notification après déconnexion
        self.notification_after_deconnection_layout = QHBoxLayout()
        self.notification_after_deconnection_label = QLabel("Temps (s) avant une notification si la connexion est perdue", self.notifications_page)
        self.notification_after_deconnection_spinbox = QSpinBox(self.notifications_page)
        self.notification_after_deconnection_spinbox.setRange(0, 99)
        self.notification_after_deconnection_layout.addWidget(self.notification_after_deconnection_label)
        self.notification_after_deconnection_layout.addWidget(self.notification_after_deconnection_spinbox)
        self.notifications_layout.addLayout(self.notification_after_deconnection_layout)

        # Ajout de l'option pour le temps avant une notification pour valider un patient
        self.notification_after_calling_layout = QHBoxLayout()
        self.notification_after_calling_label = QLabel("Temps (s) avant une notification si le patient n'est pas validé", self.notifications_page)
        self.notification_after_calling_spinbox = QSpinBox(self.notifications_page)
        self.notification_after_calling_spinbox.setRange(10, 120)
        self.notification_after_calling_layout.addWidget(self.notification_after_calling_label)
        self.notification_after_calling_layout.addWidget(self.notification_after_calling_spinbox)
        self.notifications_layout.addLayout(self.notification_after_calling_layout)

        # Ajout de l'option pour la durée d'affichage
        self.notification_duration_layout = QHBoxLayout()
        self.notification_duration_label = QLabel("Durée d'affichage (s):", self.notifications_page)
        self.notification_duration_spinbox = QSpinBox(self.notifications_page)
        self.notification_duration_spinbox.setRange(1, 60)
        self.notification_duration_layout.addWidget(self.notification_duration_label)
        self.notification_duration_layout.addWidget(self.notification_duration_spinbox)
        self.notifications_layout.addLayout(self.notification_duration_layout)
        
        # Ajout de l'option pour la taille de la police
        self.notification_font_size_layout = QHBoxLayout()
        self.notification_font_size_label = QLabel("Taille de la police:", self.notifications_page)
        self.notification_font_size_spinbox = QSpinBox(self.notifications_page)
        self.notification_font_size_spinbox.setRange(8, 36)
        self.notification_font_size_layout.addWidget(self.notification_font_size_label)
        self.notification_font_size_layout.addWidget(self.notification_font_size_spinbox)
        self.notifications_layout.addLayout(self.notification_font_size_layout)

        # Ton des messages (point 28) : sobre (explicite, défaut) ou humoristique.
        self.message_tone_layout = QHBoxLayout()
        self.message_tone_label = QLabel("Ton des messages:", self.notifications_page)
        self.message_tone_combo = QComboBox(self.notifications_page)
        # (libellé affiché, valeur enregistrée)
        self.message_tone_options = [
            ("Sobre (explicite)", TONE_SOBER),
            ("Humoristique", TONE_HUMOROUS),
        ]
        for label, value in self.message_tone_options:
            self.message_tone_combo.addItem(label, value)
        self.message_tone_layout.addWidget(self.message_tone_label)
        self.message_tone_layout.addWidget(self.message_tone_combo)
        self.notifications_layout.addLayout(self.message_tone_layout)

        # Coin de l'écran où afficher les notifications (point 26).
        self.notification_corner_layout = QHBoxLayout()
        self.notification_corner_label = QLabel("Coin d'affichage des notifications:", self.notifications_page)
        self.notification_corner_combo = QComboBox(self.notifications_page)
        # (libellé affiché, valeur enregistrée)
        self.notification_corner_options = [
            ("En bas à gauche", "bottom-left"),
            ("En bas à droite", "bottom-right"),
            ("En haut à gauche", "top-left"),
            ("En haut à droite", "top-right"),
        ]
        for label, value in self.notification_corner_options:
            self.notification_corner_combo.addItem(label, value)
        self.notification_corner_layout.addWidget(self.notification_corner_label)
        self.notification_corner_layout.addWidget(self.notification_corner_combo)
        self.notifications_layout.addLayout(self.notification_corner_layout)

        # Mode muet : réglage DISTINCT du volume. Couper le son en descendant le
        # curseur à 0 faisait perdre le réglage préféré ; ici le volume reste
        # affiché (mais grisé) et sera retrouvé tel quel en rétablissant le son.
        self.mute_checkbox = QCheckBox(
            "Couper tous les sons (le volume réglé est conservé)",
            self.notifications_page)
        self.mute_checkbox.setToolTip(
            "Aucun son n'est joué tant que la case est cochée ; les "
            "notifications restent affichées.")
        self.mute_checkbox.toggled.connect(self.update_sound_controls_enabled)
        self.notifications_layout.addWidget(self.mute_checkbox)

        # Ajout du contrôle du volume avec affichage numérique
        self.volume_layout = QHBoxLayout()
        self.volume_label = QLabel("Volume des notifications:", self.notifications_page)
        
        # Création du slider
        self.volume_slider = QSlider(Qt.Horizontal, self.notifications_page)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setTickPosition(QSlider.TicksBelow)
        self.volume_slider.setTickInterval(10)
        
        # Création du spinbox
        self.volume_spinbox = QSpinBox(self.notifications_page)
        self.volume_spinbox.setRange(0, 100)
        self.volume_spinbox.setSuffix("%")
        
        # Connexion des signaux pour la synchronisation
        self.volume_slider.valueChanged.connect(self.volume_spinbox.setValue)
        self.volume_spinbox.valueChanged.connect(self.volume_slider.setValue)
        
        # Ajout des widgets au layout
        self.volume_layout.addWidget(self.volume_label)
        self.volume_layout.addWidget(self.volume_slider)
        self.volume_layout.addWidget(self.volume_spinbox)
        self.notifications_layout.addLayout(self.volume_layout)

        # Test direct d'un son (indépendant du gestionnaire de notifications :
        # chaque clic rejoue le son, sans déduplication). Sert au réglage du
        # volume — l'utilisateur peut écouter chacun des trois sons.
        self.sound_test_layout = QHBoxLayout()
        self.sound_test_label = QLabel("Tester un son:", self.notifications_page)
        self.sound_test_combo = QComboBox(self.notifications_page)
        for name in SOUNDS:
            self.sound_test_combo.addItem(SOUND_LABELS.get(name, name), name)
        self.sound_test_button = QPushButton("Jouer", self.notifications_page)
        self.sound_test_button.clicked.connect(self.test_sound)
        self.sound_test_layout.addWidget(self.sound_test_label)
        self.sound_test_layout.addWidget(self.sound_test_combo)
        self.sound_test_layout.addWidget(self.sound_test_button)
        self.notifications_layout.addLayout(self.sound_test_layout)

        # Diagnostic : jusqu'ici un échec audio (périphérique absent, codec
        # manquant, fichier son introuvable…) n'allait QUE dans le journal —
        # l'utilisateur ne voyait qu'un silence. Le lecteur signale désormais son
        # dernier problème, affiché ici et rafraîchi en direct.
        self.audio_error_label = QLabel("", self.notifications_page)
        self.audio_error_label.setWordWrap(True)
        self.audio_error_label.setStyleSheet("color: #b00020;")
        self.audio_error_label.setAccessibleName("Problème audio")
        self.audio_error_label.setVisible(False)
        self.notifications_layout.addWidget(self.audio_error_label)

        # Bouton de test des notifications
        self.test_notification_button = QPushButton("Tester la notification", self.notifications_page)
        self.test_notification_button.clicked.connect(self.test_notification)
        self.notifications_layout.addWidget(self.test_notification_button)
        
        self.notifications_layout.addStretch()
        
        self.stacked_widget.addWidget(self.notifications_page)

        # --- Poste : configuration de la machine, en lecture seule ---
        # Serveur, secret et comptoir sont fixés à l'installation (le PC ne
        # quitte pas son comptoir) et les modifier reconnecte toute
        # l'application : ils ne sont plus éditables ici, seulement résumés.
        # La modification passe par une fenêtre dédiée, après avertissement.
        self.station_page, self.station_layout = self._scrollable_page()

        station_box = QGroupBox("Configuration du poste", self.station_page)
        station_grid = QGridLayout(station_box)
        station_grid.setColumnStretch(1, 1)
        self.station_url_value = QLabel("", station_box)
        self.station_counter_value = QLabel("", station_box)
        self.station_secret_value = QLabel("", station_box)
        for row, (caption, value) in enumerate((
                ("Serveur :", self.station_url_value),
                ("Comptoir :", self.station_counter_value),
                ("Secret applicatif :", self.station_secret_value))):
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            station_grid.addWidget(QLabel(caption, station_box), row, 0,
                                   Qt.AlignTop)
            station_grid.addWidget(value, row, 1)
        self.station_layout.addWidget(station_box)

        # État temps réel ACTUEL de l'application (socket). On lit l'état
        # mémorisé par la fenêtre (_rt_status) et on le rafraîchit tant que le
        # dialogue est ouvert — on voit tout de suite si le poste est en ligne.
        self.live_status_label = QLabel("", self.station_page)
        self.live_status_label.setWordWrap(True)
        self.station_layout.addWidget(self.live_status_label)

        station_hint = QLabel(
            "Ces réglages sont fixés à l'installation du poste. Les modifier "
            "reconnecte l'application au serveur et libère le comptoir occupé.",
            self.station_page)
        station_hint.setWordWrap(True)
        self.station_layout.addWidget(station_hint)

        self.edit_station_button = QPushButton(
            "Modifier la configuration du poste…", self.station_page)
        self.edit_station_button.clicked.connect(self._edit_station)
        self.station_layout.addWidget(self.edit_station_button)
        self.station_layout.addStretch()

        self.stacked_widget.addWidget(self.station_page)

        self._refresh_station_summary()
        self._refresh_live_connection_status()
        self._live_status_timer = QTimer(self)
        self._live_status_timer.setInterval(1500)
        self._live_status_timer.timeout.connect(
            self._refresh_live_connection_status)
        self._live_status_timer.start()

        # Résultat d'« Appliquer » (le dialogue reste ouvert) : sans ce retour,
        # rien n'indiquait que l'enregistrement avait eu lieu.
        self.save_status_label = QLabel("", self)
        self.save_status_label.setWordWrap(True)
        self.main_layout.addWidget(self.save_status_label)

        # Barre de boutons en bas : « Annuler » rend explicite la fermeture
        # sans enregistrer (auparavant il fallait deviner la croix). reject()
        # restaure l'aperçu skin/volume.
        self.button_box = QDialogButtonBox(self)
        self.save_button = self.button_box.addButton(
            "Enregistrer", QDialogButtonBox.AcceptRole)
        self.button_box.addButton("Annuler", QDialogButtonBox.RejectRole)
        # clicked(bool checked) transmettrait « False » à save_preferences :
        # handlers dédiés plutôt que la connexion directe.
        self.save_button.clicked.connect(self._on_save_clicked)
        self.apply_button = self.button_box.addButton(
            "Appliquer", QDialogButtonBox.ApplyRole)
        self.apply_button.setToolTip(
            "Enregistre et applique les préférences sans fermer la fenêtre.")
        self.apply_button.clicked.connect(self._on_apply_clicked)
        self.button_box.rejected.connect(self.reject)
        # « Valeurs par défaut » : remet les champs aux défauts du schéma sans
        # rien persister (l'utilisateur valide ensuite par « Enregistrer » ou
        # abandonne par « Annuler »). Menu à deux portées : page ou tout.
        self.defaults_button = self.button_box.addButton(
            "Valeurs par défaut", QDialogButtonBox.ResetRole)
        self.defaults_button.setToolTip(
            "Rétablit les valeurs par défaut.\n"
            "La configuration du poste (serveur, secret, comptoir) n'est "
            "jamais modifiée.")
        defaults_menu = QMenu(self.defaults_button)
        defaults_menu.addAction(
            "Rétablir cette page",
            lambda: self._reset_to_defaults(current_page_only=True))
        defaults_menu.addAction(
            "Rétablir toutes les pages",
            lambda: self._reset_to_defaults(current_page_only=False))
        self.defaults_button.setMenu(defaults_menu)
        self.main_layout.addWidget(self.button_box)

        self.load_skins()
        self.load_preferences()
        self.skin_combo.currentIndexChanged.connect(self._on_skin_selected)

        # setCurrentItem coche l'entrée de la liste ; change_page rejoue le clic.
        self.navigation_list.setCurrentItem(self.general_item)
        self.change_page(self.general_item)

    def _scrollable_page(self):
        """Page des préférences enveloppée dans une QScrollArea : le contenu
        reste accessible sur petit écran ou grande police au lieu d'être
        tronqué en bas (la page « Général » est longue).

        Renvoie ``(zone, layout)`` : la zone de défilement à empiler dans le
        QStackedWidget, et le QVBoxLayout du contenu à remplir."""
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        scroll.setWidget(inner)
        return scroll, QVBoxLayout(inner)

    def _show_page(self, item, focus_widget=None):
        """Bascule sur la page `item` puis focalise le champ fautif : un message
        d'erreur renvoie l'utilisateur là où il doit corriger, au lieu de le
        laisser chercher la page concernée."""
        self.navigation_list.setCurrentItem(item)   # déclenche change_page
        if focus_widget is not None:
            focus_widget.setFocus()

    def create_shortcut_input(self, accessible_name=""):
        """Champ de capture de frappe d'un raccourci (point 12).

        L'utilisateur clique puis tape la combinaison ; la valeur est la
        forme canonique « Ctrl+Alt+Maj+P » persistée dans QSettings."""
        edit = ShortcutEdit()
        if accessible_name:
            edit.setAccessibleName(accessible_name)
        return edit

    def change_page(self, item, _previous=None):
        # currentItemChanged émet (courant, précédent) ; None si la liste est
        # vidée. L'appel initial (page d'ouverture) n'a qu'un argument.
        # QListWidgetItem n'est pas hachable : recherche par égalité.
        for index, page_item in enumerate(self._page_items):
            if item == page_item:
                self.stacked_widget.setCurrentIndex(index)
                return

    # --- Page « Poste » ------------------------------------------------------

    def _refresh_station_summary(self):
        """Résumé en lecture seule de la configuration du poste. Le secret
        n'est jamais affiché : seulement s'il est enregistré, et où."""
        settings = QSettings()
        url = settings_schema.read(settings, "web_url")
        self.station_url_value.setText(url or "non configuré")
        counter_id = coerce_counter_id(settings.value("counter_id", None))
        parent = self.parent()
        counter_name = getattr(parent, "counter_name", None)
        if counter_id is None:
            counter_text = "non configuré"
        elif counter_name and getattr(parent, "counter_id", None) == counter_id:
            counter_text = f"{counter_name} (n° {counter_id})"
        else:
            counter_text = f"n° {counter_id}"
        self.station_counter_value.setText(counter_text)
        self.station_secret_value.setText(
            secret_status_text(secret_storage(settings)))

    def _edit_station(self):
        """Ouvre la configuration du poste, après avertissement : ces réglages
        se fixent à l'installation et leur modification reconnecte tout."""
        answer = QMessageBox.question(
            self, "Modifier la configuration du poste",
            "L'adresse du serveur, le secret applicatif et le comptoir sont "
            "réglés une fois à l'installation du poste.\n\n"
            "Les modifier reconnecte l'application au serveur et libère le "
            "comptoir actuellement occupé.\n\nContinuer ?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        dialog = StationDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self._refresh_station_summary()
            self.station_changed.emit()

    def load_preferences(self):
        # Toutes les valeurs sont lues via settings_schema (même source unique de
        # défauts/plages que main.py) : la fenêtre de préférences affiche donc
        # exactement les mêmes défauts que ceux appliqués au démarrage.
        # La configuration du poste (serveur, secret, comptoir) n'est pas un
        # réglage de ce formulaire : voir la page « Poste » / StationDialog.
        settings = QSettings()
        settings_schema.migrate_settings(settings)

        def read(key):
            return settings_schema.read(settings, key)

        self._apply_session_values(read)
        self._apply_raccourcis_values(
            read, lambda name: read_shortcut(settings, name, logger))
        self._apply_notifications_values(read)
        self._apply_general_values(read)

        # Valeurs ENREGISTRÉES (servent à restaurer l'aperçu skin/volume si
        # l'utilisateur ferme sans enregistrer).
        self.current_volume = read("notification_volume")
        self.current_muted = read("notification_muted")
        self.current_skin = read("selected_skin")

    def _apply_session_values(self, read):
        """Affecte les réglages de la page Session lus via ``read``."""
        self.reconnect_last_user_checkbox.setChecked(read("reconnect_last_user"))
        self._set_policy_combo(read("profile_sync_policy"))
        self._update_profile_buttons_state()

    def _set_policy_combo(self, value):
        """Positionne le combo de politique de récupération sur ``value``
        (« never »/« ask »/« auto », rabattu sur « ask » si inconnu)."""
        index = self.profile_policy_combo.findData(value)
        if index < 0:
            index = self.profile_policy_combo.findData("ask")
        self.profile_policy_combo.setCurrentIndex(index if index >= 0 else 0)

    def _staff_id(self):
        """Identifiant de l'équipier actuellement connecté (fenêtre parente),
        ou None — les opérations de profil serveur l'exigent."""
        parent = self.parent()
        staff_id = getattr(parent, "staff_id", None) if parent else None
        return staff_id if isinstance(staff_id, int) and staff_id else None

    def _update_profile_buttons_state(self):
        """N'affiche le groupe « Mon profil sur le serveur » que si un équipier
        est connecté : le profil est personnel — sans identité, montrer ces
        réglages n'a pas de sens. Rafraîchie toutes les 1,5 s via
        _refresh_live_connection_status, donc elle suit une connexion ou une
        déconnexion pendant que le dialogue est ouvert. Appelée aussi pendant
        __init__ avant la création des widgets : sans effet dans ce cas."""
        if not hasattr(self, "profile_send_button"):
            return
        enabled = self._staff_id() is not None
        self.profile_box.setVisible(enabled)
        self.profile_send_button.setEnabled(enabled)
        self.profile_fetch_button.setEnabled(enabled)

    def _send_profile(self):
        """Pousse les réglages personnels du formulaire vers le profil serveur
        de l'équipier connecté. L'identité du poste est exclue par
        ``profile_sync.build_payload`` (ni URL, ni comptoir, ni secret)."""
        staff_id = self._staff_id()
        api = getattr(self.parent(), "api", None)
        if staff_id is None or api is None:
            self._update_profile_buttons_state()
            return
        payload = profile_sync.build_payload(self._form_settings())
        self.profile_status_label.setText("Envoi du profil au serveur…")
        self.profile_send_button.setEnabled(False)
        self.profile_fetch_button.setEnabled(False)
        api.upload_staff_preferences(
            staff_id, payload, on_result=self._on_profile_sent)

    def _fetch_profile(self):
        """Télécharge le profil serveur de l'équipier connecté et le verse dans
        le formulaire — sans persister : « Enregistrer »/« Appliquer » gardent
        la main, « Annuler » laisse tout intact."""
        staff_id = self._staff_id()
        api = getattr(self.parent(), "api", None)
        if staff_id is None or api is None:
            self._update_profile_buttons_state()
            return
        self.profile_status_label.setText("Lecture du profil sur le serveur…")
        self.profile_send_button.setEnabled(False)
        self.profile_fetch_button.setEnabled(False)
        api.fetch_staff_preferences(
            staff_id, on_result=self._on_profile_fetched)

    def _profile_error_text(self, result, fallback):
        """Message pour un échec profil. Un 404 a deux sens possibles : erreur
        JSON du serveur (« no_preferences », « staff_unknown ») ou serveur trop
        ancien qui ne connaît pas la route — distingués via le corps."""
        if result.status != 404:
            detail = getattr(result, "message", None) or "serveur injoignable"
            return f"{fallback} : {detail}"
        error = None
        if isinstance(getattr(result, "data", None), dict):
            error = result.data.get("error")
        if error == "no_preferences":
            return "Aucun profil enregistré pour vous sur ce serveur."
        if error == "staff_unknown":
            return "Équipier inconnu du serveur — profil impossible."
        if error:
            return f"{fallback} : refus du serveur ({error})."
        return ("Fonction indisponible : ce serveur ne connaît pas encore "
                "les profils de préférences (mise à jour requise).")

    def _on_profile_sent(self, result):
        self._update_profile_buttons_state()
        if result.status == 200:
            self.profile_status_label.setText(
                "Préférences enregistrées sur le serveur.")
        else:
            self.profile_status_label.setText(
                self._profile_error_text(result, "Envoi impossible"))

    def _on_profile_fetched(self, result):
        self._update_profile_buttons_state()
        data = getattr(result, "data", None)
        payload = data.get("payload") if isinstance(data, dict) else None
        if result.status == 200 and isinstance(payload, dict) and payload:
            self._apply_profile_to_form(payload)
            self.profile_status_label.setText(
                "Préférences chargées dans le formulaire — validez par "
                "« Enregistrer » ou « Appliquer » pour les garder.")
        else:
            self.profile_status_label.setText(
                self._profile_error_text(result, "Récupération impossible"))

    def _apply_profile_to_form(self, payload):
        """Verse un profil téléchargé dans le formulaire (jamais persisté ici).

        Chaque valeur repasse par la normalisation du schéma via DictSettings
        (un profil hors-plage est rabattu proprement). La politique transportée
        par le profil — personnelle — remplace le choix local du combo.
        L'identité du poste (URL, secret, comptoir, reconnexion du dernier
        équipier) n'est pas touchée : elle n'est pas dans le profil."""
        shim = profile_sync.DictSettings(payload)

        def read(key):
            return settings_schema.read(shim, key)

        self._apply_general_values(read)
        self._apply_raccourcis_values(
            read, lambda name: read_shortcut(shim, name, logger))
        self._apply_notifications_values(read)
        self._set_policy_combo(read("profile_sync_policy"))

    def _apply_raccourcis_values(self, read, read_shortcut_text):
        """Affecte les réglages de la page Raccourcis.

        ``read_shortcut_text`` fournit le texte canonique d'un raccourci :
        ``read_shortcut`` au chargement (défaut + migration + persistance de la
        correction, partagé avec main.py), ``default_shortcut`` à la remise à
        zéro."""
        for name, widget in (
            ("next_patient_shortcut", self.next_patient_shortcut_input),
            ("validate_patient_shortcut", self.validate_patient_shortcut_input),
            ("pause_shortcut", self.pause_shortcut_input),
            ("recall_shortcut", self.recall_shortcut_input),
            ("deconnect_shortcut", self.deconnect_input),
        ):
            self._set_shortcut_value(widget, read_shortcut_text(name))
        mode_index = self.shortcut_mode_combo.findData(read("shortcut_mode"))
        self.shortcut_mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self.confirm_sensitive_checkbox.setChecked(read("confirm_sensitive_shortcuts"))
        self.shortcut_feedback_checkbox.setChecked(read("shortcut_feedback"))

    def _set_shortcut_value(self, widget, text):
        """Affiche un raccourci canonique (« Alt+Maj+P ») dans un ShortcutEdit."""
        modifiers, key = split_shortcut(text)
        widget.setShortcut(join_shortcut(modifiers, key))

    def _apply_notifications_values(self, read):
        """Affecte les réglages de la page Notifications lus via ``read``."""
        for category, checkbox in self.notification_display_checkboxes.items():
            checkbox.setChecked(read(notification_rules.DISPLAY_KEYS[category]))
        for category, checkbox in self.notification_sound_checkboxes.items():
            checkbox.setChecked(read(notification_rules.SOUND_KEYS[category]))
        self.notification_after_deconnection_spinbox.setValue(
            read("notification_after_deconnection"))
        self.notification_after_calling_spinbox.setValue(
            read("notification_after_calling"))
        self.notification_duration_spinbox.setValue(
            read("notification_duration"))
        self.notification_font_size_spinbox.setValue(
            read("notification_font_size"))
        tone_index = self.message_tone_combo.findData(read("message_tone"))
        self.message_tone_combo.setCurrentIndex(tone_index if tone_index >= 0 else 0)
        corner_index = self.notification_corner_combo.findData(read("notification_corner"))
        self.notification_corner_combo.setCurrentIndex(
            corner_index if corner_index >= 0 else 0)
        self.volume_slider.setValue(read("notification_volume"))
        self.mute_checkbox.setChecked(read("notification_muted"))
        self.update_sound_controls_enabled()
        self.watch_audio_errors()

    def _apply_general_values(self, read):
        """Affecte les réglages de la page Général lus via ``read``."""
        self.always_on_top_checkbox.setChecked(read("always_on_top"))
        self.horizontal_mode_checkbox.setChecked(read("vertical_mode"))
        self.compact_mode_checkbox.setChecked(read("compact_mode"))
        self.panel_snap_checkbox.setChecked(read("panel_snap"))
        self.panel_thickness_spinbox.setValue(read("panel_thickness"))
        self.tray_single_icon_checkbox.setChecked(read("tray_single_icon"))
        self.show_auto_call_button_checkbox.setChecked(
            read("show_auto_call_button"))
        self.display_patient_list.setChecked(read("display_patient_list"))
        self.patient_list_font_size_spinbox.setValue(
            read("patient_list_font_size"))
        self.patient_list_position_vertical.setCurrentText(
            REVERSE_POSITION_MAPPING.get(
                read("patient_list_vertical_position"), BOTTOM_TEXT))
        self.patient_list_position_horizontal.setCurrentText(
            REVERSE_POSITION_MAPPING.get(
                read("patient_list_horizontal_position"), RIGHT_TEXT))
        self.messaging_position.setCurrentText(REVERSE_POSITION_MAPPING.get(
            read("messaging_dock_area"), BOTTOM_TEXT))
        self.debug_window.setChecked(read("debug_window"))
        index = self.skin_combo.findData(read("selected_skin"))
        if index >= 0:
            self.skin_combo.setCurrentIndex(index)

    def _reset_to_defaults(self, current_page_only):
        """Rétablit les valeurs par défaut du schéma dans les champs du dialogue.

        Simple édition du formulaire : rien n'est persisté tant que
        l'utilisateur n'enregistre pas, et « Annuler » laisse tout intact.
        La configuration du poste (page « Poste ») n'est pas concernée."""
        read = settings_schema.default
        targets = [self.navigation_list.currentItem()] if current_page_only \
            else [self.general_item, self.session_item,
                  self.raccourcis_item, self.notifications_item]
        # QListWidgetItem n'est pas hachable : comparaisons (comme change_page)
        # plutôt qu'un dict.
        for item in targets:
            if item == self.general_item:
                self._apply_general_values(read)
            elif item == self.session_item:
                self._apply_session_values(read)
            elif item == self.raccourcis_item:
                self._apply_raccourcis_values(read, default_shortcut)
            elif item == self.notifications_item:
                self._apply_notifications_values(read)
        logger.info("Valeurs par défaut rétablies (%s).",
                    "page courante" if current_page_only else "toutes les pages")

    def save_preferences(self, close_dialog=True):
        """Valide puis enregistre les préférences.

        ``close_dialog`` : « Enregistrer » (True, fermeture Accepted après
        succès) ou « Appliquer » (False, le dialogue reste ouvert et le signal
        ``applied`` est émis à la place). Aucun appel réseau : la configuration
        du poste, seule à exiger une vérification serveur, a sa propre fenêtre
        (StationDialog)."""
        next_patient_shortcut = self.next_patient_shortcut_input.shortcut()
        validate_patient_shortcut = self.validate_patient_shortcut_input.shortcut()
        recall_shortcut = self.recall_shortcut_input.shortcut()
        deconnect_shortcut = self.deconnect_input.shortcut()
        pause_shortcut = self.pause_shortcut_input.shortcut()

        shortcut_map = {
            "next": next_patient_shortcut,
            "validate": validate_patient_shortcut,
            "pause": pause_shortcut,
            "recall": recall_shortcut,
            "deconnect": deconnect_shortcut,
        }
        # Action -> groupe de saisie : permet de focaliser le raccourci fautif
        # quand la validation échoue.
        shortcut_widgets = {
            "next": self.next_patient_shortcut_input,
            "validate": self.validate_patient_shortcut_input,
            "pause": self.pause_shortcut_input,
            "recall": self.recall_shortcut_input,
            "deconnect": self.deconnect_input,
        }
        # Validation des raccourcis (point 7) : chaque action doit avoir une touche
        # réelle reconnue (ni champ vide, ni modificateur seul, ni touche inconnue).
        # Sans ce contrôle, un « Ctrl » seul ou une touche invalide était accepté
        # puis silencieusement ignoré à l'installation (action inopérante).
        invalid = find_invalid_shortcuts(shortcut_map)
        if invalid:
            reasons = {
                INVALID_EMPTY: "aucune touche définie",
                INVALID_LONE_MODIFIER: "un modificateur seul (ajoutez une touche)",
                INVALID_UNKNOWN_KEY: "une touche non reconnue",
            }
            lines = "\n".join(
                f"• {ACTION_LABELS.get(a, a)} : {reasons[code]}"
                for a, code in invalid.items())
            first = next(iter(invalid))
            self._show_page(
                self.raccourcis_item,
                shortcut_widgets[first])
            QMessageBox.warning(
                self, "Raccourcis invalides",
                "Certains raccourcis ne sont pas valides :\n\n"
                f"{lines}\n\nCorrigez-les avant d'enregistrer.")
            return

        # Détection des doublons de raccourcis (point 27) : deux actions ne peuvent
        # pas utiliser la même combinaison (comparaison indépendante de l'ordre et
        # de la casse des modificateurs). On refuse d'enregistrer si conflit.
        duplicates = find_duplicate_shortcuts(shortcut_map)
        if duplicates:
            conflicts = "\n".join(
                "• " + " / ".join(ACTION_LABELS.get(a, a) for a in actions)
                for actions in duplicates.values())
            first_actions = next(iter(duplicates.values()))
            self._show_page(
                self.raccourcis_item,
                shortcut_widgets[first_actions[0]])
            QMessageBox.warning(
                self, "Raccourcis en conflit",
                "Plusieurs actions utilisent la même combinaison :\n\n"
                f"{conflicts}\n\nAttribuez une combinaison distincte à chacune.")
            return

        self._finalize_save(self._form_settings(), close=close_dialog)

    def _form_settings(self):
        """Instantané des réglages du formulaire — jamais l'identité du poste
        (URL, comptoir, secret : StationDialog).

        Sert à la fois à l'enregistrement local (save_preferences) et à
        _send_profile (envoi au serveur via build_payload) : un seul mapping
        widget -> clé, impossible de diverger."""
        pending_settings = {
            "profile_sync_policy": self.profile_policy_combo.currentData(),
            "reconnect_last_user": self.reconnect_last_user_checkbox.isChecked(),
            "next_patient_shortcut": self.next_patient_shortcut_input.shortcut(),
            "validate_patient_shortcut": self.validate_patient_shortcut_input.shortcut(),
            "pause_shortcut": self.pause_shortcut_input.shortcut(),
            "recall_shortcut": self.recall_shortcut_input.shortcut(),
            "deconnect_shortcut": self.deconnect_input.shortcut(),
            "shortcut_mode": self.shortcut_mode_combo.currentData(),
            "confirm_sensitive_shortcuts": self.confirm_sensitive_checkbox.isChecked(),
            "shortcut_feedback": self.shortcut_feedback_checkbox.isChecked(),
            "notification_after_deconnection": self.notification_after_deconnection_spinbox.value(),
            "notification_duration": self.notification_duration_spinbox.value(),
            "notification_after_calling": self.notification_after_calling_spinbox.value(),
            "notification_font_size": self.notification_font_size_spinbox.value(),
            "message_tone": self.message_tone_combo.currentData(),
            "notification_corner": self.notification_corner_combo.currentData(),
            "notification_volume": self.volume_slider.value(),
            "notification_muted": self.mute_checkbox.isChecked(),
            "always_on_top": self.always_on_top_checkbox.isChecked(),
            "vertical_mode": self.horizontal_mode_checkbox.isChecked(),
            "compact_mode": self.compact_mode_checkbox.isChecked(),
            "panel_snap": self.panel_snap_checkbox.isChecked(),
            "panel_thickness": self.panel_thickness_spinbox.value(),
            "tray_single_icon": self.tray_single_icon_checkbox.isChecked(),
            "show_auto_call_button": self.show_auto_call_button_checkbox.isChecked(),
            "display_patient_list": self.display_patient_list.isChecked(),
            "patient_list_font_size": self.patient_list_font_size_spinbox.value(),
            "patient_list_vertical_position": POSITION_MAPPING[self.patient_list_position_vertical.currentText()],
            "patient_list_horizontal_position": POSITION_MAPPING[self.patient_list_position_horizontal.currentText()],
            "messaging_dock_area": POSITION_MAPPING[self.messaging_position.currentText()],
            "debug_window": self.debug_window.isChecked(),
            "selected_skin": resources.normalize_skin_name(
                self.skin_combo.currentData()),
        }
        for category, checkbox in self.notification_display_checkboxes.items():
            pending_settings[notification_rules.DISPLAY_KEYS[category]] = checkbox.isChecked()
        for category, checkbox in self.notification_sound_checkboxes.items():
            pending_settings[notification_rules.SOUND_KEYS[category]] = checkbox.isChecked()
        return pending_settings

    def _on_save_clicked(self):
        self.save_preferences(close_dialog=True)

    def _on_apply_clicked(self):
        """« Appliquer » : même validation + persistance qu'« Enregistrer »,
        mais le dialogue reste ouvert et le signal ``applied`` demande à la
        fenêtre de recharger ses réglages immédiatement."""
        self.save_preferences(close_dialog=False)

    def _refresh_live_connection_status(self):
        """Affiche l'état temps réel actuel de la fenêtre principale.

        ``_rt_status``/``_rt_attempts`` sont mémorisés par la fenêtre à chaque
        évènement socket (``_set_rt_status``) : lire ces valeurs plutôt que
        l'indicateur graphique fonctionne aussi quand l'interface a été
        reconstruite — et en test, où le parent est un faux."""
        parent = self.parent()
        status = getattr(parent, "_rt_status", None)
        attempts = getattr(parent, "_rt_attempts", 0) or 0
        if status == "connected":
            text, color = ("État actuel : connecté au serveur "
                           "(temps réel actif).", "#1a7f37")
        elif status == "connecting":
            detail = f" (tentative {attempts})" if attempts else ""
            text, color = ("État actuel : connexion en cours…" + detail,
                           "#b06000")
        elif status == "disconnected":
            detail = f" (tentative {attempts})" if attempts else ""
            text, color = ("État actuel : déconnecté — reconnexion en cours"
                           + detail + ".", "#b00020")
        else:
            text, color = ("État actuel : inconnu — aucune connexion temps "
                           "réel n'a encore été établie.", "")
        # Ne re-rendre que sur changement : le label est rafraîchi par timer.
        # L'équipier connecté peut changer pendant que le dialogue est ouvert
        # : les boutons de profil suivent cet état (ils exigent une identité).
        self._update_profile_buttons_state()
        if text != self.live_status_label.text():
            self.live_status_label.setText(text)
            self.live_status_label.setStyleSheet(
                f"color: {color};" if color else "")

    def _sync_and_verify(self, settings):
        """Persistance forcée et vérifiée (point 10), cf. station_dialog."""
        return sync_and_verify(self, settings)

    def _finalize_save(self, values, close=True):
        """ Persiste les réglages validés par save_preferences, puis ferme le
        dialogue (Accepted, ``close``) ou le laisse ouvert en émettant
        ``applied`` (« Appliquer »).

        Persistance forcée et VÉRIFIÉE (``sync``/``status``) : en cas d'échec,
        une erreur est affichée et rien n'est confirmé (pas d'``accept``). """
        settings = QSettings()
        for key, value in values.items():
            settings.setValue(key, value)
        if not self._sync_and_verify(settings):
            return

        # Enregistrement effectif : le volume édité devient le volume de
        # référence, un aperçu antérieur n'a donc plus rien à restaurer (c'est
        # apply_preferences, côté fenêtre, qui l'applique au lecteur).
        self.current_volume = values["notification_volume"]
        self.current_muted = values["notification_muted"]
        self.current_skin = values["selected_skin"]
        self._volume_previewed = False
        self._skin_previewed = False

        # Un SEUL mécanisme d'application (point 7) :
        #  - « Appliquer » : le dialogue reste ouvert, le signal ``applied``
        #    déclenche l'UNIQUE apply_preferences côté fenêtre — qui recharge
        #    les valeurs, applique le cosmétique et reconnecte au besoin ;
        #  - « Enregistrer » : Accepted fait la même chose via l'appelant
        #    (MainWindow.show_preferences_dialog).
        # Le dialogue ne recharge rien et ne touche jamais la fenêtre parente.
        if close:
            self.accept()
        else:
            self.save_status_label.setText("Préférences enregistrées et appliquées.")
            self.applied.emit()

    def _apply_side_panel_preset(self):
        """Pré-remplit les réglages recommandés pour un usage en panneau latéral
        compact à côté du logiciel métier. Les autres choix de l'utilisateur
        (raccourcis, notifications, sons…) ne sont PAS touchés, et rien n'est
        écrit tant que les préférences ne sont pas enregistrées."""
        self.compact_mode_checkbox.setChecked(True)
        self.panel_snap_checkbox.setChecked(True)
        self.always_on_top_checkbox.setChecked(True)
        # Colonne verticale étroite : « Orientation horizontale » décochée.
        self.horizontal_mode_checkbox.setChecked(False)
        self.panel_thickness_spinbox.setValue(DEFAULT_PANEL_THICKNESS)
        # Liste sous le panneau en mode vertical (onglets partagés avec la
        # messagerie en mode compact) plutôt qu'à droite, qui élargirait.
        self.patient_list_position_vertical.setCurrentText(BOTTOM_TEXT)

    def _open_log_folder(self):
        """Ouvre l'explorateur sur le dossier des journaux (créé au besoin).
        Toute défaillance est signalée — jamais un clic sans effet visible."""
        log_dir = default_log_dir()
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(
                self, "Journaux inaccessibles",
                f"Impossible de préparer le dossier des journaux :\n{e}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir))):
            QMessageBox.warning(
                self, "Journaux inaccessibles",
                "Impossible d'ouvrir le dossier des journaux :\n"
                f"{log_dir}")

    def _reset_window_position(self):
        """Délègue à la fenêtre principale la réinitialisation de sa géométrie."""
        parent = self.parent()
        if parent is not None and hasattr(parent, "reset_window_position"):
            parent.reset_window_position()
        else:
            # Pas de silencieux : un clic sans effet doit laisser une trace.
            logger.warning(
                "Réinitialisation de la position demandée, mais le parent (%r) "
                "n'expose pas reset_window_position.", parent)

    def load_skins(self):
        """Remplit la liste des skins depuis les ressources embarquées.

        Ne crée plus le dossier « skins » s'il manque : l'ancienne version en
        fabriquait un (vide) dans le répertoire de lancement de l'application.
        """
        # Donnée de chaque entrée = valeur enregistrée (NO_SKIN pour « aucun ») :
        # le libellé affiché n'est plus jamais persisté.
        self.skin_combo.addItem(resources.LEGACY_NO_SKIN_LABEL, resources.NO_SKIN)
        skins = resources.available_skins()
        if not skins:
            logger.warning("Aucun skin trouvé dans %s", resources.skins_dir())
        for name in skins:
            self.skin_combo.addItem(name, name)

    def _on_skin_selected(self, _index):
        """Sélection d'un skin dans la liste : aperçu immédiat (annulable)."""
        self.preview_skin(self.skin_combo.currentData())

    def preview_skin(self, skin_name):
        """Applique ``skin_name`` en APERÇU à toute l'application — même portée
        que l'application réelle (``MainWindow.load_skin``), pour voir ce qui
        sera enregistré. Annulé par restore_skin si le dialogue est fermé sans
        enregistrer."""
        logger.debug("Aperçu du skin : %r", skin_name)
        if not resources.apply_skin(skin_name, window=self.parent(),
                                    app=QApplication.instance()):
            logger.warning("Skin '%s' introuvable ou illisible", skin_name)
            return
        self._skin_previewed = True

    def restore_skin(self):
        """Annule l'aperçu de skin : réapplique le skin ENREGISTRÉ (y compris
        « aucun »). Sans aperçu, ne touche à rien."""
        if not self._skin_previewed:
            return
        window, app = self.parent(), QApplication.instance()
        if not resources.apply_skin(self.current_skin, window=window, app=app):
            # Skin enregistré disparu : au démarrage, load_skin n'aurait rien
            # appliqué — on revient donc au style standard plutôt que de garder
            # l'aperçu.
            logger.warning("Skin enregistré '%s' introuvable : style standard rétabli",
                           self.current_skin)
            resources.apply_skin(resources.NO_SKIN, window=window, app=app)
        self._skin_previewed = False

    def reject(self):
        # Réapplique le skin ET le volume enregistrés si l'utilisateur ferme sans
        # sauvegarder : le test de notification est un APERÇU, il ne doit rien
        # laisser derrière lui.
        self.restore_skin()
        self.restore_volume()
        super().reject()

    def _player(self):
        """Lecteur audio de la fenêtre principale, ou None (en test, ou avant sa
        création au démarrage)."""
        return getattr(self.parent(), "audio_player", None)

    def _set_player_volume(self, volume):
        """Applique un volume au lecteur de la fenêtre principale.

        Renvoie True si le lecteur existait (absent en test, ou avant sa
        création au démarrage)."""
        player = self._player()
        if player is None:
            return False
        player.set_volume(volume)
        return True

    def apply_audio_preview(self):
        """Applique au lecteur le volume ET le mode muet EN COURS D'ÉDITION.

        Un bouton « Tester » doit faire entendre le réglage qu'on est en train
        de régler, pas celui enregistré : sans le mode muet, décocher « couper
        tous les sons » puis tester serait resté silencieux. L'aperçu est annulé
        (restore_volume) si le dialogue est fermé sans enregistrer."""
        if not self._set_player_volume(self.volume_spinbox.value()):
            return False
        self._player().set_muted(self.mute_checkbox.isChecked())
        self._volume_previewed = True
        return True

    def restore_volume(self):
        """Annule l'aperçu (volume ET mode muet) posé par les boutons « Tester ».

        Ne touche au lecteur que si un aperçu a réellement eu lieu, pour ne pas
        écraser un réglage posé ailleurs entre-temps."""
        if not self._volume_previewed or self.current_volume is None:
            return
        player = self._player()
        if player is not None:
            player.set_volume(self.current_volume)
            if self.current_muted is not None:
                player.set_muted(self.current_muted)
            logger.debug("Aperçu audio annulé, retour à %s%% (muet : %s)",
                         self.current_volume, self.current_muted)
        self._volume_previewed = False

    def update_sound_controls_enabled(self):
        """Grise les réglages de son quand tout est coupé.

        Le volume reste VISIBLE (valeur conservée, pas perdue) : c'est tout
        l'intérêt d'un mode muet séparé du curseur."""
        enabled = not self.mute_checkbox.isChecked()
        for widget in (self.volume_label, self.volume_slider, self.volume_spinbox,
                       self.sound_test_label, self.sound_test_combo,
                       self.sound_test_button,
                       *self.notification_listen_buttons.values()):
            widget.setEnabled(enabled)
        self.sound_test_button.setToolTip(
            "" if enabled else
            "Les sons sont coupés : décochez « Couper tous les sons » pour tester.")

    def watch_audio_errors(self):
        """Affiche le dernier problème audio connu, puis suit les suivants.

        Sans cela, une panne de son (périphérique absent, codec manquant,
        fichier son introuvable) n'existait que dans le journal."""
        player = self._player()
        if player is None:
            return
        # Le suivi en direct est un bonus : un lecteur sans signal (tests) voit
        # quand même son dernier problème affiché à l'ouverture.
        signal = getattr(player, "error_changed", None)
        if signal is not None:
            signal.connect(self.show_audio_error)
        self.show_audio_error(getattr(player, "last_error", ""))

    def show_audio_error(self, message):
        """Affiche (ou efface) le message de problème audio."""
        message = message or ""
        self.audio_error_label.setText(
            f"Problème audio : {message}" if message else "")
        self.audio_error_label.setVisible(bool(message))

    def test_notification(self):
        data = {"origin": "test_notification", "message": "Test de notification"}
        font_size = self.notification_font_size_spinbox.value()
        # Aperçu : on joue avec le volume et le mode muet en cours d'édition, en
        # mémorisant qu'il faudra revenir aux valeurs enregistrées si
        # l'utilisateur annule.
        self.apply_audio_preview()
        # Passe par le gestionnaire (écran de l'app, coin configuré, sans focus) ;
        # force=True car le test doit s'afficher même notifications désactivées.
        self.parent().show_notification(data, internal=True, font_size=font_size, force=True)

    def test_sound(self):
        """Joue directement le son sélectionné via le lecteur audio, SANS passer
        par le gestionnaire de notifications : chaque clic rejoue le son, même
        rapproché (pas de déduplication) et en coupant le son en cours
        (``force``, qui court-circuite la politique des sons rapprochés). Sert au
        réglage du volume.

        Le volume (et le mode muet) d'aperçu sont posés et seront restaurés à
        l'annulation, comme pour « Tester la notification ». Un éventuel
        problème audio affiché est effacé avant l'essai : le message qui
        s'affichera ensuite concerne bien CE clic."""
        self._preview_play(self.sound_test_combo.currentData())

    def _test_category_sound(self, category):
        """Joue le son représentatif d'une catégorie (bouton « Jouer » de la
        ligne), pour entendre immédiatement ce qu'on vient de cocher."""
        self._preview_play(
            notification_rules.CATEGORY_SOUNDS.get(category, "ding"))

    def _preview_play(self, sound_name):
        """Joue un son en APERÇU (volume et mode muet en cours d'édition,
        ``force`` pour ignorer la déduplication), et efface le message de
        problème audio affiché : celui qui apparaîtra ensuite concerne bien
        CE clic. Aperçu restauré à l'annulation (restore_volume)."""
        if not sound_name:
            return
        player = self._player()
        if player is None:
            return
        if hasattr(player, "clear_error"):
            player.clear_error()
        self.apply_audio_preview()
        player.play_sound(sound_name, force=True)

