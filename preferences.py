import requests
import os
import logging
from datetime import datetime

logger = logging.getLogger("appcomptoir.preferences")
from PySide6.QtWidgets import QDialog, QHBoxLayout, QListWidget, QListWidgetItem, QStackedWidget, QWidget, QVBoxLayout, QCheckBox, QLineEdit, QTextEdit, QPushButton, QLabel, QMessageBox, QComboBox, QSpinBox, QSlider
from PySide6.QtCore import Signal, Slot, QSettings, Qt, QThread
from connections import DEFAULT_TIMEOUT
from secret_store import load_secret, save_secret
from counter_id_utils import coerce_counter_id
from shortcut_defaults import default_shortcut, migrate_shortcut
from panel_layout import (
    MIN_PANEL_THICKNESS, MAX_PANEL_THICKNESS, DEFAULT_PANEL_THICKNESS,
    clamp_thickness,
)
from shortcut_config import (
    MODE_DISABLED, MODE_FOCUSED, MODE_GLOBAL, DEFAULT_MODE,
    ACTION_LABELS, normalize_mode, find_duplicate_shortcuts,
)

class TestConnectionWorker(QThread):
    connection_tested = Signal(bool, str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            response = requests.get(self.url, timeout=DEFAULT_TIMEOUT)
            if response.status_code == 200:
                self.connection_tested.emit(True, f"Connexion réussie à {current_time}")
            else:
                self.connection_tested.emit(False, f"Erreur de connexion: {response.status_code} à {current_time}")
        except requests.exceptions.RequestException as e:
            self.connection_tested.emit(False, f"Erreur: {e} à {current_time}")


class CountersWorker(QThread):
    """ Récupère la liste des comptoirs en arrière-plan pour ne pas geler la
    boîte de dialogue Préférences pendant l'appel réseau.

    /api/counters est protégée côté serveur (require_app_token_or_login) : on
    récupère d'abord un token applicatif avec le secret saisi dans les
    préférences avant d'appeler la route. """
    result = Signal(bool, object)  # success, counters (list) ou message d'erreur (str)

    def __init__(self, web_url, app_secret):
        super().__init__()
        self.web_url = web_url
        self.app_secret = app_secret

    def run(self):
        try:
            token_response = requests.post(f"{self.web_url}/api/get_app_token",
                                            data={'app_secret': self.app_secret},
                                            timeout=DEFAULT_TIMEOUT)
            if token_response.status_code != 200:
                self.result.emit(False, "Secret applicatif invalide : impossible de récupérer la liste des comptoirs")
                return
            token = token_response.json()['token']

            response = requests.get(f"{self.web_url}/api/counters",
                                     headers={'X-App-Token': token}, timeout=DEFAULT_TIMEOUT)
            if response.status_code == 200:
                self.result.emit(True, response.json())
            else:
                self.result.emit(False, f"Erreur de chargement des comptoirs: {response.status_code}")
        except requests.exceptions.RequestException as e:
            self.result.emit(False, f"Erreur: {e}")


# Constants for UI texts and corresponding values
BOTTOM_TEXT = "Bas"
RIGHT_TEXT = "Droite"
POSITION_MAPPING = {
    BOTTOM_TEXT: "bottom",
    RIGHT_TEXT: "right"
}
REVERSE_POSITION_MAPPING = {v: k for k, v in POSITION_MAPPING.items()}


class PreferencesDialog(QDialog):
    counters_loaded = Signal(list)
    preferences_updated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Préférences")
        
        self.main_layout = QHBoxLayout(self)
        
        self.navigation_list = QListWidget()
        self.navigation_list.setFixedWidth(150)
        self.navigation_list.itemClicked.connect(self.change_page)
        
        self.general_item = QListWidgetItem("Général")
        self.connexion_item = QListWidgetItem("Connexion")
        self.raccourcis_item = QListWidgetItem("Raccourcis")
        self.notifications_item = QListWidgetItem("Notifications")
        self.navigation_list.addItem(self.general_item)
        self.navigation_list.addItem(self.connexion_item)
        self.navigation_list.addItem(self.raccourcis_item)
        self.navigation_list.addItem(self.notifications_item)        
        
        self.main_layout.addWidget(self.navigation_list)
        
        self.stacked_widget = QStackedWidget()

        self.general_page = QWidget()
        self.general_layout = QVBoxLayout()
        self.general_page.setLayout(self.general_layout)
        
        self.always_on_top_checkbox = QCheckBox("Always on top", self.general_page)
        self.general_layout.addWidget(self.always_on_top_checkbox)

        self.horizontal_mode = QCheckBox("Orientation verticale", self.general_page)
        self.general_layout.addWidget(self.horizontal_mode)

        # --- Mode panneau compact (point 25) ---
        # Panneau étroit docké sur un bord (colonne verticale ou barre
        # horizontale) plutôt qu'une fenêtre générique.
        self.compact_mode_checkbox = QCheckBox("Mode panneau compact (docké sur un bord)", self.general_page)
        self.general_layout.addWidget(self.compact_mode_checkbox)

        self.panel_snap_checkbox = QCheckBox("Magnétisme aux bords de l'écran", self.general_page)
        self.general_layout.addWidget(self.panel_snap_checkbox)

        self.panel_thickness_layout = QHBoxLayout()
        self.panel_thickness_label = QLabel("Épaisseur du panneau (px):", self.general_page)
        self.panel_thickness_spinbox = QSpinBox(self.general_page)
        self.panel_thickness_spinbox.setRange(MIN_PANEL_THICKNESS, MAX_PANEL_THICKNESS)
        self.panel_thickness_spinbox.setSingleStep(10)
        self.panel_thickness_layout.addWidget(self.panel_thickness_label)
        self.panel_thickness_layout.addWidget(self.panel_thickness_spinbox)
        self.general_layout.addLayout(self.panel_thickness_layout)

        self.display_patient_list = QCheckBox("Liste des patients", self.general_page)
        self.general_layout.addWidget(self.display_patient_list)

        self.patient_list_position_vertical_label = QLabel("Position de la liste des patients en mode verticale:", self.general_page)
        self.general_layout.addWidget(self.patient_list_position_vertical_label)

        self.patient_list_position_vertical = QComboBox(self.general_page)
        self.patient_list_position_vertical.addItems([BOTTOM_TEXT, RIGHT_TEXT])
        self.general_layout.addWidget(self.patient_list_position_vertical)

        self.patient_list_position_horizontal_label = QLabel("Position de la liste des patients en mode horizontal:", self.general_page)
        self.general_layout.addWidget(self.patient_list_position_horizontal_label)

        self.patient_list_position_horizontal = QComboBox(self.general_page)
        self.patient_list_position_horizontal.addItems([BOTTOM_TEXT, RIGHT_TEXT])
        self.general_layout.addWidget(self.patient_list_position_horizontal)
        
        self.debug_window = QCheckBox("Garder ouverte la fenêtre de log après le démarrage", self.general_page)
        self.general_layout.addWidget(self.debug_window)

        # Réinitialise taille/position de la fenêtre (utile si elle est perdue
        # hors écran après un changement de moniteur). Voir point 24.
        self.reset_position_button = QPushButton("Réinitialiser la position de la fenêtre", self.general_page)
        self.reset_position_button.clicked.connect(self._reset_window_position)
        self.general_layout.addWidget(self.reset_position_button)

        # Ajout de la sélection de skins
        self.skin_label = QLabel("Sélectionner un skin:", self.general_page)
        self.general_layout.addWidget(self.skin_label)
        
        self.skin_combo = QComboBox(self.general_page)
        self.skin_combo.currentTextChanged.connect(self.preview_skin)
        self.general_layout.addWidget(self.skin_combo)
        
        self.general_layout.addStretch()
        
        self.stacked_widget.addWidget(self.general_page)
        
        self.main_layout.addWidget(self.stacked_widget)
        
        self.connexion_page = QWidget()
        self.connexion_layout = QVBoxLayout()
        self.connexion_page.setLayout(self.connexion_layout)

        self.url_label = QLabel("Adresse du site web:", self.connexion_page)
        self.connexion_layout.addWidget(self.url_label)
        
        self.url_layout = QHBoxLayout()
        self.url_input = QLineEdit(self.connexion_page)
        self.url_layout.addWidget(self.url_input)
        
        self.test_button = QPushButton("Tester l'adresse", self.connexion_page)
        self.test_button.clicked.connect(self.test_url)
        self.url_layout.addWidget(self.test_button)
        
        self.connexion_layout.addLayout(self.url_layout)
        
        self.status_label = QTextEdit(self.connexion_page)
        self.status_label.setReadOnly(True)
        self.status_label.setFixedWidth(400)
        self.connexion_layout.addWidget(self.status_label)
        
        self.app_secret_label = QLabel("Secret applicatif (doit correspondre à APP_SECRET côté serveur):", self.connexion_page)
        self.connexion_layout.addWidget(self.app_secret_label)

        self.app_secret_input = QLineEdit()
        self.app_secret_input.setEchoMode(QLineEdit.Password)
        self.connexion_layout.addWidget(self.app_secret_input)

        self.counter_label = QLabel("Sélectionner le comptoir:", self.connexion_page)
        self.connexion_layout.addWidget(self.counter_label)
        
        self.counter_combobox = QComboBox(self.connexion_page)
        self.connexion_layout.addWidget(self.counter_combobox)
        
        self.connexion_layout.addStretch()
        
        self.stacked_widget.addWidget(self.connexion_page)
        
        self.raccourcis_page = QWidget()
        self.raccourcis_layout = QVBoxLayout()
        self.raccourcis_page.setLayout(self.raccourcis_layout)

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
        
        self.next_patient_shortcut_input = self.create_shortcut_input()
        self.raccourcis_layout.addWidget(self.next_patient_shortcut_input)
        
        self.validate_patient_shortcut_label = QLabel("Raccourci - Valider patient:", self.raccourcis_page)
        self.raccourcis_layout.addWidget(self.validate_patient_shortcut_label)

        self.validate_patient_shortcut_input = self.create_shortcut_input()
        self.raccourcis_layout.addWidget(self.validate_patient_shortcut_input)
        
        self.pause_shortcut_label = QLabel("Raccourci - Pause:", self.raccourcis_page)
        self.raccourcis_layout.addWidget(self.pause_shortcut_label)
        
        self.pause_shortcut_input = self.create_shortcut_input()
        self.raccourcis_layout.addWidget(self.pause_shortcut_input)
        
        self.recall_label = QLabel("Raccourci - Rappel patient:", self.raccourcis_page)
        self.raccourcis_layout.addWidget(self.recall_label)
        
        self.recall_shortcut_input = self.create_shortcut_input()
        self.raccourcis_layout.addWidget(self.recall_shortcut_input)
        
        self.deconnect_label = QLabel("Raccourci - Déconnexion:", self.raccourcis_page)
        self.raccourcis_layout.addWidget(self.deconnect_label)
        
        self.deconnect_input = self.create_shortcut_input()
        self.raccourcis_layout.addWidget(self.deconnect_input)
        
        self.raccourcis_layout.addStretch()
        
        self.stacked_widget.addWidget(self.raccourcis_page)
        
        self.main_layout.addWidget(self.stacked_widget)

        self.notifications_page = QWidget()
        self.notifications_layout = QVBoxLayout()
        self.notifications_page.setLayout(self.notifications_layout)
        
        self.show_current_patient_checkbox = QCheckBox("Afficher le patient en cours", self.notifications_page)
        self.notifications_layout.addWidget(self.show_current_patient_checkbox)

        self.notification_autocalling_new_patient_checkbox = QCheckBox("Afficher si un nouveau patient est appelé via l'autocalling", self.notifications_page)
        self.notifications_layout.addWidget(self.notification_autocalling_new_patient_checkbox)
        
        self.notification_specific_acts_checkbox = QCheckBox("Afficher les activités spécifiques (Vaccins, Tests... voir le paramètrage du serveur)", self.notifications_page)
        self.notifications_layout.addWidget(self.notification_specific_acts_checkbox)

        self.notification_add_paper_checkbox = QCheckBox("Afficher les alertes pour remplacer le papier", self.notifications_page)
        self.notifications_layout.addWidget(self.notification_add_paper_checkbox)

        self.notification_connection_checkbox = QCheckBox("Afficher en cas de problème de connexion", self.notifications_page)
        self.notifications_layout.addWidget(self.notification_connection_checkbox)

        # Ajout de l'option pour le temps pour une notification après déconnexion
        self.notification_after_deconnection_layout = QHBoxLayout()
        self.notification_after_deconnection_label = QLabel("Temps (s) avant une notification si la connexion est perdue", self.notifications_page)
        self.notification_after_deconnection_spinbox = QSpinBox(self.notifications_page)
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

        # Bouton de test des notifications
        self.test_notification_button = QPushButton("Tester la notification", self.notifications_page)
        self.test_notification_button.clicked.connect(self.test_notification)
        self.notifications_layout.addWidget(self.test_notification_button)
        
        self.notifications_layout.addStretch()
        
        self.stacked_widget.addWidget(self.notifications_page)
        
        self.main_layout.addWidget(self.stacked_widget)
        
        self.save_button = QPushButton("Enregistrer", self)
        self.save_button.clicked.connect(self.save_preferences)
        self.main_layout.addWidget(self.save_button)
        
        self.load_skins()
        self.load_preferences()
        
        self.counters_loaded.connect(self.update_counters)
        

    def create_shortcut_input(self):
        widget = QWidget()
        layout = QHBoxLayout()
        widget.setLayout(layout)
        
        self.ctrl_button = QCheckBox("Ctrl")
        self.ctrl_button.setObjectName("Ctrl")
        self.alt_button = QCheckBox("Alt")
        self.alt_button.setObjectName("Alt")
        self.shift_button = QCheckBox("Maj")
        self.shift_button.setObjectName("Maj")
        self.win_button = QCheckBox("Win")
        self.win_button.setObjectName("Win")
        self.key_input = QLineEdit()
        self.key_input.setObjectName("Key")
        
        layout.addWidget(self.ctrl_button)
        layout.addWidget(self.alt_button)
        layout.addWidget(self.shift_button)
        layout.addWidget(self.win_button)
        layout.addWidget(self.key_input)
        
        return widget

    def change_page(self, item):
        if item == self.general_item:
            self.stacked_widget.setCurrentIndex(0)
        elif item == self.connexion_item:
            self.stacked_widget.setCurrentIndex(1)
        elif item == self.raccourcis_item:
            self.stacked_widget.setCurrentIndex(2)
        elif item == self.notifications_item:
            self.stacked_widget.setCurrentIndex(3)            
        
    def load_preferences(self):
        settings = QSettings()
        self.url_input.setText(settings.value("web_url", "http://localhost:5000"))
        self.app_secret_input.setText(load_secret(settings))
        self.counter_id = coerce_counter_id(settings.value("counter_id", None))
        label = f"{self.counter_id} - Chargement en cours..." if self.counter_id else "Sélectionnez un comptoir..."
        self.counter_combobox.addItem(label, self.counter_id)
        vertical_position = settings.value("patient_list_vertical_position", "bottom")
        horizontal_position = settings.value("patient_list_horizontal_position", "right")
        
        # Défauts centralisés dans shortcut_defaults (identiques à main.py).
        self.load_shortcut(settings, "next_patient_shortcut", self.next_patient_shortcut_input)
        self.load_shortcut(settings, "validate_patient_shortcut", self.validate_patient_shortcut_input)
        self.load_shortcut(settings, "pause_shortcut", self.pause_shortcut_input)
        self.load_shortcut(settings, "recall_shortcut", self.recall_shortcut_input)
        self.load_shortcut(settings, "deconnect_shortcut", self.deconnect_input)

        # Mode des raccourcis + options (point 27).
        mode = normalize_mode(settings.value("shortcut_mode", DEFAULT_MODE))
        mode_index = self.shortcut_mode_combo.findData(mode)
        self.shortcut_mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self.confirm_sensitive_checkbox.setChecked(
            settings.value("confirm_sensitive_shortcuts", False, type=bool))
        self.shortcut_feedback_checkbox.setChecked(
            settings.value("shortcut_feedback", True, type=bool))

        self.show_current_patient_checkbox.setChecked(settings.value("notification_current_patient", False, type=bool))
        self.notification_autocalling_new_patient_checkbox.setChecked(settings.value("notification_autocalling_new_patient", True, type=bool))
        self.notification_specific_acts_checkbox.setChecked(settings.value("notification_specific_acts", True, type=bool))
        self.notification_add_paper_checkbox.setChecked(settings.value("notification_add_paper", True, type=bool))
        self.notification_connection_checkbox.setChecked(settings.value("notification_connection", True, type=bool))
        self.notification_after_deconnection_spinbox.setValue(settings.value("notification_after_deconnection", 10, type=int))
        self.notification_after_calling_spinbox.setValue(settings.value("notification_after_calling", 30, type=int))
        self.notification_duration_spinbox.setValue(settings.value("notification_duration", 5, type=int))
        self.notification_font_size_spinbox.setValue(settings.value("notification_font_size", 12, type=int))
        corner_value = settings.value("notification_corner", "bottom-left")
        corner_index = self.notification_corner_combo.findData(corner_value)
        self.notification_corner_combo.setCurrentIndex(corner_index if corner_index >= 0 else 0)
        self.volume_slider.setValue(settings.value("notification_volume", 50, type=int))

        self.always_on_top_checkbox.setChecked(settings.value("always_on_top", False, type=bool))
        self.horizontal_mode.setChecked(settings.value("vertical_mode", False, type=bool))
        self.compact_mode_checkbox.setChecked(settings.value("compact_mode", False, type=bool))
        self.panel_snap_checkbox.setChecked(settings.value("panel_snap", True, type=bool))
        self.panel_thickness_spinbox.setValue(
            clamp_thickness(settings.value("panel_thickness", DEFAULT_PANEL_THICKNESS)))
        self.display_patient_list.setChecked(settings.value("display_patient_list", False, type=bool))
        self.patient_list_position_vertical.setCurrentText(REVERSE_POSITION_MAPPING.get(vertical_position, BOTTOM_TEXT))
        self.patient_list_position_horizontal.setCurrentText(REVERSE_POSITION_MAPPING.get(horizontal_position, RIGHT_TEXT))
        self.debug_window.setChecked(settings.value("debug_window", False, type=bool))
        
        # pour les skins
        selected_skin = settings.value("selected_skin", "")
        index = self.skin_combo.findText(selected_skin)
        if index >= 0:
            self.skin_combo.setCurrentIndex(index)
        self.current_skin = selected_skin

    def load_shortcut(self, settings, name, widget):
        shortcut = migrate_shortcut(name, settings.value(name, default_shortcut(name)))
        keys = shortcut.split("+")
        widget.findChild(QCheckBox, "Ctrl").setChecked("Ctrl" in keys)
        widget.findChild(QCheckBox, "Alt").setChecked("Alt" in keys)
        widget.findChild(QCheckBox, "Maj").setChecked("Maj" in keys)
        widget.findChild(QCheckBox, "Win").setChecked("Win" in keys)
        widget.findChild(QLineEdit).setText(keys[-1] if keys and keys[-1] not in ["Ctrl", "Alt", "Maj", "Win"] else "")

    def save_preferences(self):
        url = self.url_input.text()
        app_secret = self.app_secret_input.text()
        # counter_id normalisé en entier strictement positif (cohérent avec le
        # serveur et avec les comparaisons de l'app).
        counter_id = coerce_counter_id(self.counter_combobox.currentData())
        next_patient_shortcut = self.get_shortcut_text(self.next_patient_shortcut_input)
        validate_patient_shortcut = self.get_shortcut_text(self.validate_patient_shortcut_input)
        recall_shortcut = self.get_shortcut_text(self.recall_shortcut_input)
        deconnect_shortcut = self.get_shortcut_text(self.deconnect_input)
        pause_shortcut = self.get_shortcut_text(self.pause_shortcut_input)

        if not url:
            QMessageBox.warning(self, "Erreur", "L'URL ne peut pas être vide")
            return
        if counter_id is None:
            QMessageBox.warning(self, "Erreur", "Vous devez sélectionner un comptoir valide")
            return

        # Détection des doublons de raccourcis (point 27) : deux actions ne peuvent
        # pas utiliser la même combinaison (comparaison indépendante de l'ordre et
        # de la casse des modificateurs). On refuse d'enregistrer si conflit.
        shortcut_map = {
            "next": next_patient_shortcut,
            "validate": validate_patient_shortcut,
            "pause": pause_shortcut,
            "recall": recall_shortcut,
            "deconnect": deconnect_shortcut,
        }
        duplicates = find_duplicate_shortcuts(shortcut_map)
        if duplicates:
            conflicts = "\n".join(
                "• " + " / ".join(ACTION_LABELS.get(a, a) for a in actions)
                for actions in duplicates.values())
            QMessageBox.warning(
                self, "Raccourcis en conflit",
                "Plusieurs actions utilisent la même combinaison :\n\n"
                f"{conflicts}\n\nAttribuez une combinaison distincte à chacune.")
            return

        settings = QSettings()
        settings.setValue("web_url", url)
        # Le secret est stocké dans le magasin sécurisé (keyring), pas en clair
        # dans QSettings. save_secret efface aussi toute copie en clair héritée.
        save_secret(settings, app_secret)
        settings.setValue("counter_id", counter_id)
        settings.setValue("next_patient_shortcut", next_patient_shortcut)
        settings.setValue("validate_patient_shortcut", validate_patient_shortcut)
        settings.setValue("pause_shortcut", pause_shortcut)
        settings.setValue('recall_shortcut', recall_shortcut)
        settings.setValue("deconnect_shortcut", deconnect_shortcut)
        settings.setValue("shortcut_mode", self.shortcut_mode_combo.currentData())
        settings.setValue("confirm_sensitive_shortcuts", self.confirm_sensitive_checkbox.isChecked())
        settings.setValue("shortcut_feedback", self.shortcut_feedback_checkbox.isChecked())
        
        # notifications
        settings.setValue("notification_current_patient", self.show_current_patient_checkbox.isChecked())
        settings.setValue("notification_autocalling_new_patient", self.notification_autocalling_new_patient_checkbox.isChecked())
        settings.setValue("notification_specific_acts", self.notification_specific_acts_checkbox.isChecked())
        settings.setValue("notification_add_paper", self.notification_add_paper_checkbox.isChecked())
        settings.setValue("notification_connection", self.notification_connection_checkbox.isChecked())
        settings.setValue("notification_after_deconnection", self.notification_after_deconnection_spinbox.value())
        settings.setValue("notification_duration", self.notification_duration_spinbox.value())
        settings.setValue("notification_after_calling", self.notification_after_calling_spinbox.value())
        settings.setValue("notification_font_size", self.notification_font_size_spinbox.value())
        settings.setValue("notification_corner", self.notification_corner_combo.currentData())
        settings.setValue("notification_volume", self.volume_slider.value())

        settings.setValue("always_on_top", self.always_on_top_checkbox.isChecked())
        settings.setValue("vertical_mode", self.horizontal_mode.isChecked())
        settings.setValue("compact_mode", self.compact_mode_checkbox.isChecked())
        settings.setValue("panel_snap", self.panel_snap_checkbox.isChecked())
        settings.setValue("panel_thickness", self.panel_thickness_spinbox.value())
        settings.setValue("display_patient_list", self.display_patient_list.isChecked())
        settings.setValue("patient_list_vertical_position", POSITION_MAPPING[self.patient_list_position_vertical.currentText()])
        settings.setValue("patient_list_horizontal_position", POSITION_MAPPING[self.patient_list_position_horizontal.currentText()])
        settings.setValue("debug_window", self.debug_window.isChecked())

        # skins
        settings.setValue("selected_skin", self.skin_combo.currentText())
        self.current_skin = self.skin_combo.currentText()
        
        self.parent().setWindowFlag(Qt.WindowStaysOnTopHint, self.always_on_top_checkbox.isChecked())
        self.parent().show()

        self.accept()
        # Le rechargement des préférences ET la reconnexion éventuelle des services
        # (si serveur/secret/comptoir ont changé) sont gérés par apply_preferences,
        # branché sur ce signal. On NE recharge PAS ici : apply_preferences a besoin
        # des anciennes valeurs pour détecter le changement.
        self.preferences_updated.emit()

    def _reset_window_position(self):
        """Délègue à la fenêtre principale la réinitialisation de sa géométrie."""
        parent = self.parent()
        if parent is not None and hasattr(parent, "reset_window_position"):
            parent.reset_window_position()

    def get_shortcut_text(self, widget):
        keys = []
        if widget.findChild(QCheckBox, "Ctrl").isChecked():
            keys.append("Ctrl")
        if widget.findChild(QCheckBox, "Alt").isChecked():
            keys.append("Alt")
        if widget.findChild(QCheckBox, "Maj").isChecked():
            keys.append("Maj")
        if widget.findChild(QCheckBox, "Win").isChecked():
            keys.append("Win")
        key_input = widget.findChild(QLineEdit).text()
        if key_input:
            keys.append(key_input)
        return "+".join(keys)

    def test_url(self):
        self.status_label.setText("Test de connexion en cours...")
        url = self.url_input.text()
        if not url:
            QMessageBox.warning(self, "Erreur", "L'URL ne peut pas être vide")
            self.status_label.setText("L'URL ne peut pas être vide")
            return
        
        self.worker = TestConnectionWorker(url)
        self.worker.connection_tested.connect(self.on_connection_tested)
        self.worker.start()

    @Slot(bool, str)
    def on_connection_tested(self, success, message):
        self.status_label.setText(message)
        if success:
            self.load_counters()

    def load_counters(self):
        self.counters_worker = CountersWorker(self.url_input.text(), self.app_secret_input.text())
        self.counters_worker.result.connect(self._on_counters_result)
        self.counters_worker.start()

    @Slot(bool, object)
    def _on_counters_result(self, success, data):
        if success:
            self.counters_loaded.emit(data)
        else:
            self.status_label.setText(data)

    @Slot(list)
    def update_counters(self, counters):
        self.counter_combobox.clear()
        for counter in counters:
            self.counter_combobox.addItem(counter['name'], counter['id'])
        
        if self.counter_id:
            index = self.counter_combobox.findData(int(self.counter_id))
            if index != -1:
                self.counter_combobox.setCurrentIndex(index)
    
    def load_skins(self):
        skins_dir = "skins"
        self.skin_combo.addItem("Pas de skin")  
        if not os.path.exists(skins_dir):
            os.makedirs(skins_dir)
        for file in os.listdir(skins_dir):
            if file.endswith(".qss"):
                self.skin_combo.addItem(os.path.splitext(file)[0])

    def preview_skin(self, skin_name):
        logger.debug("Aperçu du skin : %s", skin_name)
        if skin_name == "Pas de skin":
            # Supprime le skin en désactivant tous les styles QSS
            self.parent().setStyleSheet("")
        elif skin_name:
            qss_file = os.path.join("skins", f"{skin_name}.qss")
            if os.path.exists(qss_file):
                with open(qss_file, "r") as f:
                    self.parent().setStyleSheet(f.read())

    def reject(self):
        # Réapplique le skin enregistré si l'utilisateur ferme sans sauvegarder
        self.preview_skin(self.current_skin)
        super().reject()

    def test_notification(self):
        data = {"origin": "test_notification", "message": "Test de notification"}
        font_size = self.notification_font_size_spinbox.value()
        self.parent().audio_player.set_volume(self.volume_spinbox.value())
        # Passe par le gestionnaire (écran de l'app, coin configuré, sans focus) ;
        # force=True car le test doit s'afficher même notifications désactivées.
        self.parent().show_notification(data, internal=True, font_size=font_size, force=True)

