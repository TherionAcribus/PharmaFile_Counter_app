import requests
import os
from datetime import datetime
from PySide6.QtWidgets import QDialog, QHBoxLayout, QListWidget, QListWidgetItem, QStackedWidget, QWidget, QVBoxLayout, QCheckBox, QLineEdit, QTextEdit, QPushButton, QLabel, QMessageBox, QComboBox
from PySide6.QtCore import Signal, Slot, QSettings, Qt, QThread


class TestConnectionWorker(QThread):
    connection_tested = Signal(bool, str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            response = requests.get(self.url)
            if response.status_code == 200:
                self.connection_tested.emit(True, f"Connexion réussie à {current_time}")
            else:
                self.connection_tested.emit(False, f"Erreur de connexion: {response.status_code} à {current_time}")
        except requests.exceptions.RequestException as e:
            self.connection_tested.emit(False, f"Erreur: {e} à {current_time}")
            

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
        
        self.start_with_reduce_mode = QCheckBox("Démarrer en mode réduit", self.general_page)
        self.general_layout.addWidget(self.start_with_reduce_mode)
        
        self.horizontal_mode = QCheckBox("Orientation verticale", self.general_page)
        self.general_layout.addWidget(self.horizontal_mode)

        self.display_patient_list = QCheckBox("Liste des patients", self.general_page)
        self.general_layout.addWidget(self.display_patient_list)

        self.patient_list_position_combo = QComboBox(self.general_page)
        self.patient_list_position_combo.addItems(["Bas", "Droite"])
        self.general_layout.addWidget(self.patient_list_position_combo)

        self.patient_list_position_label = QLabel("Position de la liste des patients:", self.general_page)
        self.general_layout.addWidget(self.patient_list_position_label)
        
        self.debug_window = QCheckBox("Garder ouverte la fenêtre de log après le démarrage", self.general_page)
        self.general_layout.addWidget(self.debug_window)

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
        
        self.username_label = QLabel("Nom d'utilisateur:", self.connexion_page)
        self.connexion_layout.addWidget(self.username_label)
        
        self.username_input = QLineEdit()
        self.connexion_layout.addWidget(self.username_input)
        
        self.password_label = QLabel("Mot de passe:", self.connexion_page)
        self.connexion_layout.addWidget(self.password_label)
        
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.connexion_layout.addWidget(self.password_input)
        
        self.counter_label = QLabel("Sélectionner le comptoir:", self.connexion_page)
        self.connexion_layout.addWidget(self.counter_label)
        
        self.counter_combobox = QComboBox(self.connexion_page)
        self.connexion_layout.addWidget(self.counter_combobox)
        
        self.connexion_layout.addStretch()
        
        self.stacked_widget.addWidget(self.connexion_page)
        
        self.raccourcis_page = QWidget()
        self.raccourcis_layout = QVBoxLayout()
        self.raccourcis_page.setLayout(self.raccourcis_layout)
        
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
        
        self.notification_specific_acts_checkbox = QCheckBox("Afficher les actes spécifiques", self.notifications_page)
        self.notifications_layout.addWidget(self.notification_specific_acts_checkbox)
        
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
        self.username_input.setText(settings.value("username", "admin"))
        self.password_input.setText(settings.value("password", "admin"))
        self.counter_id = settings.value("counter_id", None)
        self.counter_combobox.addItem(str(self.counter_id) + " - Chargement en cours...", self.counter_id)
        
        self.load_shortcut(settings, "next_patient_shortcut", self.next_patient_shortcut_input, "Alt+S")
        self.load_shortcut(settings, "validate_patient_shortcut", self.validate_patient_shortcut_input, "Alt+V")
        self.load_shortcut(settings, "pause_shortcut", self.pause_shortcut_input, "Alt+P")
        self.load_shortcut(settings, "recall_shortcut", self.recall_shortcut_input, "Alt+R")
        self.load_shortcut(settings, "deconnect_shortcut", self.deconnect_input, "Alt+D")

        self.show_current_patient_checkbox.setChecked(settings.value("show_current_patient", True, type=bool))
        self.notification_specific_acts_checkbox.setChecked(settings.value("notification_specific_acts", True, type=bool))

        self.start_with_reduce_mode.setChecked(settings.value("start_with_reduce_mode", False, type=bool))
        self.always_on_top_checkbox.setChecked(settings.value("always_on_top", False, type=bool))
        self.horizontal_mode.setChecked(settings.value("vertical_mode", False, type=bool))
        self.display_patient_list.setChecked(settings.value("display_patient_list", False, type=bool))
        self.patient_list_position_combo.setCurrentText(settings.value("patient_list_position", "Bas"))
        self.debug_window.setChecked(settings.value("debug_window", False, type=bool))
        
        # pour les skins
        selected_skin = settings.value("selected_skin", "")
        index = self.skin_combo.findText(selected_skin)
        if index >= 0:
            self.skin_combo.setCurrentIndex(index)
        self.current_skin = selected_skin

    def load_shortcut(self, settings, name, widget, default_shortcut):
        shortcut = settings.value(name, default_shortcut)
        keys = shortcut.split("+")
        widget.findChild(QCheckBox, "Ctrl").setChecked("Ctrl" in keys)
        widget.findChild(QCheckBox, "Alt").setChecked("Alt" in keys)
        widget.findChild(QCheckBox, "Maj").setChecked("Maj" in keys)
        widget.findChild(QCheckBox, "Win").setChecked("Win" in keys)
        widget.findChild(QLineEdit).setText(keys[-1] if keys and keys[-1] not in ["Ctrl", "Alt", "Maj", "Win"] else "")

    def save_preferences(self):
        url = self.url_input.text()
        username = self.username_input.text()
        password = self.password_input.text()
        counter_id = self.counter_combobox.currentData()
        next_patient_shortcut = self.get_shortcut_text(self.next_patient_shortcut_input)
        validate_patient_shortcut = self.get_shortcut_text(self.validate_patient_shortcut_input)
        recall_shortcut = self.get_shortcut_text(self.recall_shortcut_input)
        deconnect_shortcut = self.get_shortcut_text(self.deconnect_input)
        pause_shortcut = self.get_shortcut_text(self.pause_shortcut_input)

        if not url:
            QMessageBox.warning(self, "Erreur", "L'URL ne peut pas être vide")
            return
        if not counter_id:
            QMessageBox.warning(self, "Erreur", "Vous devez sélectionner un comptoir")
            return
        if not username or not password:
            QMessageBox.warning(self, "Erreur", "Le nom d'utilisateur et le mot de passe ne peuvent pas être vides")
            return
        
        settings = QSettings()
        old_url = settings.value("web_url")
        settings.setValue("web_url", url)
        settings.setValue("username", username)
        settings.setValue("password", password)
        settings.setValue("counter_id", counter_id)
        settings.setValue("next_patient_shortcut", next_patient_shortcut)
        settings.setValue("validate_patient_shortcut", validate_patient_shortcut)
        settings.setValue("pause_shortcut", pause_shortcut)
        settings.setValue('recall_shortcut', recall_shortcut)
        settings.setValue("deconnect_shortcut", deconnect_shortcut)
        
        settings.setValue("show_current_patient", self.show_current_patient_checkbox.isChecked())
        settings.setValue("notification_specific_acts", self.notification_specific_acts_checkbox.isChecked())

        settings.setValue("always_on_top", self.always_on_top_checkbox.isChecked())
        settings.setValue("start_with_reduce_mode", self.start_with_reduce_mode.isChecked())
        settings.setValue("vertical_mode", self.horizontal_mode.isChecked())
        settings.setValue("display_patient_list", self.display_patient_list.isChecked())
        settings.setValue("patient_list_position", self.patient_list_position_combo.currentText())
        settings.setValue("debug_window", self.debug_window.isChecked())
        # skins
        settings.setValue("selected_skin", self.skin_combo.currentText())
        self.current_skin = self.skin_combo.currentText()
        
        self.parent().setWindowFlag(Qt.WindowStaysOnTopHint, self.always_on_top_checkbox.isChecked())
        self.parent().show() 

        if url != old_url:
            print("Redémarrage du client SSE")
            #2self.parent().start_sse_client(url)
            # TODO idem avec websocket
        
        self.accept()
        self.parent().load_preferences()
        self.preferences_updated.emit()

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
        url = self.url_input.text() + '/api/counters'
        try:
            response = requests.get(url)
            if response.status_code == 200:
                counters = response.json()
                self.counters_loaded.emit(counters)
            else:
                self.status_label.setText(f"Erreur de chargement des comptoirs: {response.status_code}")
        except requests.exceptions.RequestException as e:
            self.status_label.setText(f"Erreur: {e}")

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
        skins_dir = "skins"  # Assurez-vous que ce chemin est correct
        if not os.path.exists(skins_dir):
            os.makedirs(skins_dir)
        for file in os.listdir(skins_dir):
            if file.endswith(".qss"):
                self.skin_combo.addItem(os.path.splitext(file)[0])

    def preview_skin(self, skin_name):
        if skin_name:
            qss_file = os.path.join("skins", f"{skin_name}.qss")
            with open(qss_file, "r") as f:
                self.parent().setStyleSheet(f.read())

    def reject(self):
        # Réapplique le skin enregistré si l'utilisateur ferme sans sauvegarder
        self.preview_skin(self.current_skin)
        super().reject()


