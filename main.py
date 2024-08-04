import sys
import os
import time
import json
import requests
import threading
import socketio
from requests.exceptions import RequestException
import keyboard
from PySide6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon, QMenu, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox, QComboBox, QTextEdit, QGroupBox, QListWidget, QListWidgetItem, QStackedWidget, QWidget, QCheckBox, QSizePolicy
from PySide6.QtCore import QUrl, Signal, Slot, QSettings, QThread, QTimer, Qt, QSize, QMetaObject
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtGui import QIcon, QAction, QKeySequence, QKeyEvent
from datetime import datetime

from websocket_client import WebSocketClient


class SSEClient(QThread):
    new_patient = Signal(object)
    new_notification = Signal(str)

    def __init__(self, web_url):
        super().__init__()
        self.web_url = web_url

    def run(self):
        while True:
            try:
                url = f'{self.web_url}/events/update_patient_pyside'
                response = requests.get(url, stream=True)
                client = response.iter_lines()
                for line in client:
                    if line:
                        decoded_line = line.decode('utf-8')
                        if decoded_line.startswith('data:'):
                            json_data = decoded_line[5:].strip()
                            data = json.loads(json_data)
                            if data['type'] == 'notification_new_patient':
                                self.new_notification.emit(data['message'])
                            elif data['type'] == 'patient':
                                self.new_patient.emit(data["list"])
            except RequestException as e:
                print(f"Connection lost: {e}")
                time.sleep(5)
                print("Attempting to reconnect...")

    def stop(self):
        self._running = False
        self.wait()


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


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


class RequestThread(QThread):
    result = Signal(float, str, int)

    def __init__(self, url, session, method='GET', data=None, headers=None):
        super().__init__()
        self.url = url
        self.session = session
        self.method = method
        self.data = data
        self.headers = headers

    def run(self):
        start_time = time.time()
        try:
            if self.method == 'GET':
                response = self.session.get(self.url)
            elif self.method == 'POST':
                response = self.session.post(self.url, data=self.data, headers=self.headers)
            else:
                raise ValueError(f"Méthode HTTP non supportée: {self.method}")
            
            end_time = time.time()
            elapsed_time = end_time - start_time
            self.result.emit(elapsed_time, response.text, response.status_code)
        except RequestException as e:
            end_time = time.time()
            elapsed_time = end_time - start_time
            self.result.emit(elapsed_time, str(e), 0)


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
        
        self.vertical_mode = QCheckBox("Orientation verticale", self.general_page)
        self.general_layout.addWidget(self.vertical_mode)
        
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
        
        self.deconnect_label = QLabel("Raccourci - Déconnexion:", self.raccourcis_page)
        self.raccourcis_layout.addWidget(self.deconnect_label)
        
        self.deconnect_input = self.create_shortcut_input()
        self.raccourcis_layout.addWidget(self.deconnect_input)
        
        self.stacked_widget.addWidget(self.raccourcis_page)
        
        self.main_layout.addWidget(self.stacked_widget)

        self.notifications_page = QWidget()
        self.notifications_layout = QVBoxLayout()
        self.notifications_page.setLayout(self.notifications_layout)
        
        self.show_current_patient_checkbox = QCheckBox("Afficher le patient en cours", self.notifications_page)
        self.notifications_layout.addWidget(self.show_current_patient_checkbox)
        
        self.notification_specific_acts_checkbox = QCheckBox("Afficher les actes spécifiques", self.notifications_page)
        self.notifications_layout.addWidget(self.notification_specific_acts_checkbox)
        
        self.stacked_widget.addWidget(self.notifications_page)
        
        self.main_layout.addWidget(self.stacked_widget)
        
        self.save_button = QPushButton("Enregistrer", self)
        self.save_button.clicked.connect(self.save_preferences)
        self.main_layout.addWidget(self.save_button)
        
        self.load_preferences()
        
        self.counters_loaded.connect(self.update_counters)
        
        QTimer.singleShot(0, self.test_url)

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
        self.load_shortcut(settings, "deconnect_shortcut", self.deconnect_input, "Alt+D")

        self.show_current_patient_checkbox.setChecked(settings.value("show_current_patient", True, type=bool))
        self.notification_specific_acts_checkbox.setChecked(settings.value("notification_specific_acts", True, type=bool))

        self.start_with_reduce_mode.setChecked(settings.value("start_with_reduce_mode", False, type=bool))
        self.always_on_top_checkbox.setChecked(settings.value("always_on_top", False, type=bool))
        self.vertical_mode.setChecked(settings.value("vertical_mode", False, type=bool))

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
        settings.setValue("deconnect_shortcut", deconnect_shortcut)
        
        settings.setValue("show_current_patient", self.show_current_patient_checkbox.isChecked())
        settings.setValue("notification_specific_acts", self.notification_specific_acts_checkbox.isChecked())

        settings.setValue("always_on_top", self.always_on_top_checkbox.isChecked())
        settings.setValue("start_with_reduce_mode", self.start_with_reduce_mode.isChecked())
        settings.setValue("vertical_mode", self.vertical_mode.isChecked())
        self.parent().setWindowFlag(Qt.WindowStaysOnTopHint, self.always_on_top_checkbox.isChecked())
        self.parent().show() 

        if url != old_url:
            print("Redémarrage du client SSE")
            self.parent().start_sse_client(url)
        
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


class MainWindow(QMainWindow):
    patient_data_received = Signal(object)
    patient_id = None
    staff_id = None
    
    def __init__(self):
        super().__init__()
        self.session = requests.Session()  # Session HTTP persistante
        
        self.load_preferences()
        
        self.app_token = None
        self.get_app_token()
        
        self.setup_ui()
        
        self.setup_user()
        
        self.is_reduced_mode = False
        if self.start_with_reduce_mode:
            self.toggle_mode()     

        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.always_on_top)
        self.show() 
        
    def get_app_token(self):
        url = f'{self.web_url}/api/get_app_token'
        data = {'app_secret': 'votre_secret_app'}
        response = self.session.post(url, data=data)
        if response.status_code == 200:
            self.app_token = response.json()['token']
            print("Token obtenu :", self.app_token)
        else:
            print("Échec de l'obtention du token")

        
    def load_preferences(self):
        settings = QSettings()
        self.web_url = settings.value("web_url", "http://localhost:5000")
        self.username = settings.value("username", "admin")
        self.password = settings.value("password", "admin")
        self.counter_id = settings.value("counter_id", "1")
        self.next_patient_shortcut = settings.value("next_patient_shortcut", "Alt+S")
        self.validate_patient_shortcut = settings.value("validate_patient_shortcut", "Alt+V")
        self.pause_shortcut = settings.value("pause_shortcut", "Altl+P")
        self.deconnect_shortcut = settings.value("deconnect_shortcut", "Alt+D")
        self.notification_specific_acts = settings.value("notification_specific_acts", True, type=bool)
        self.always_on_top = settings.value("always_on_top", False, type=bool)
        self.start_with_reduce_mode = settings.value("start_with_reduce_mode", False, type=bool)
        self.vertical_mode = settings.value("vertical_mode", False, type=bool)
    
        
    def setup_ui(self):
        icon_path = os.path.join(os.path.dirname(__file__), 'assets/images', 'next.ico')
        self.setWindowIcon(QIcon(icon_path))
        self.setWindowTitle("PharmaFile")
        self.resize(800, 600)         

        self.create_menu()
        self.start_socket_io_client(self.web_url)

        icon_path = resource_path("assets/images/pause.ico")
        self.trayIcon1 = QSystemTrayIcon(QIcon(icon_path), self)
        self.trayIcon1.setToolTip("Pause")
        tray_menu1 = QMenu()
        open_action1 = tray_menu1.addAction("Open Main Window")
        open_action1.triggered.connect(self.call_web_function_pause)
        self.trayIcon1.setContextMenu(tray_menu1)
        self.trayIcon1.activated.connect(self.on_tray_icon_pause_activated)
        self.trayIcon1.show()

        icon_path = resource_path("assets/images/next_orange.ico")
        self.trayIcon2 = QSystemTrayIcon(QIcon(icon_path), self)
        self.trayIcon2.setToolTip("Prochain patient")
        tray_menu2 = QMenu()
        open_action2 = tray_menu2.addAction("Call Web Function")
        open_action2.triggered.connect(self.call_web_function_validate_and_call_next)
        self.trayIcon2.setContextMenu(tray_menu2)
        self.trayIcon2.activated.connect(self.on_tray_icon_call_next_activated)
        self.trayIcon2.show()

        icon_path = resource_path("assets/images/check.ico")
        self.trayIcon3 = QSystemTrayIcon(QIcon(icon_path), self)
        self.trayIcon3.setToolTip("Valider patient")
        tray_menu3 = QMenu()
        open_action3 = tray_menu3.addAction("Call Web Function")
        open_action3.triggered.connect(self.call_web_function_validate)
        self.trayIcon3.setContextMenu(tray_menu3)
        self.trayIcon3.activated.connect(self.on_tray_icon_validation_activated)
        self.trayIcon3.show()

        self.browser = QWebEngineView()
        # Connect to the URL changed signal. On recherche la page login pour la remplir
        self.browser.urlChanged.connect(self.on_url_changed)
        
        self.web_channel = QWebChannel()
        self.web_channel.registerObject("pyqt", self)
        self.browser.page().setWebChannel(self.web_channel)
        self.browser.loadFinished.connect(self.on_load_finished)
        self.load_url()

        self.stacked_widget = QStackedWidget()
        self.create_control_buttons()

        self.stacked_widget.addWidget(self.browser)
        self.stacked_widget.addWidget(self.button_widget)
        
        self.setCentralWidget(self.stacked_widget)        

        self.init_patient()
        
        list_patients = self.init_list_patients()
        self.update_patient_menu(list_patients)

        self.setup_global_shortcut()
        
    def setup_user(self):
        """ Va chercher le staff sur le comptoir """
        url = f'{self.web_url}/api/counter/is_staff_on_counter/{self.counter_id}'
        self.user_thread = RequestThread(url, self.session, method='GET')
        self.user_thread.result.connect(self.handle_user_result)
        self.user_thread.start()
        
    @Slot(float, str, int)
    def handle_user_result(self, elapsed_time, response_text, status_code):
        # si staff au comptoir 
        if status_code == 200:
            try:
                print("Success:", response_text)
                response_data = json.loads(response_text)
                self.staff_id = response_data["staff"]['id']
                staff_name = response_data["staff"]['name']
                # on modifie le titre
                self.update_window_title(staff_name)              
                
            except json.JSONDecodeError as e:
                print("Failed to decode JSON:", e)
        # si personne au comptoir
        elif status_code == 204:
            print("Success:", response_text)
            print("No staff on counter")
            self.staff_id = False
            # on modifie le titre
            self.update_window_title("Connectez-vous !")
            # on affiche l'interface de connexion
            self.deconnexion_interface()
        else:
            print("Failed to retrieve data:", status_code)
        print("Elapsed time:", elapsed_time)
        
        
    def update_window_title(self, staff_name):
        """ Met a jour le titre de la fenetre """
        print(f"Staff name: {staff_name}")
        self.setWindowTitle(f"PharmaFile - {self.counter_id} - {staff_name}")
        

    def start_sse_client(self, url):
        print(f"Starting SSE client with URL: {url}")
        self.sse_client = SSEClient(url)
        self.sse_client.new_patient.connect(self.update_patient_menu)
        self.sse_client.new_notification.connect(self.show_notification)
        self.sse_client.start()
        
    def start_socket_io_client(self, url):
        print(f"Starting Socket.IO client with URL: {url}")
        self.socket_io_client = WebSocketClient(url)
        self.socket_io_client.new_patient.connect(self.new_patient)
        self.socket_io_client.new_notification.connect(self.show_notification)
        self.socket_io_client.start()       

        
    def create_menu(self):
        self.menu = self.menuBar().addMenu("Fichier")
        self.preferences_action = QAction("Préférences", self)
        self.preferences_action.triggered.connect(self.show_preferences_dialog)
        self.menu.addAction(self.preferences_action)
        
        self.toggle_mode_action = QAction("Mode réduit", self)
        self.toggle_mode_action.triggered.connect(self.toggle_mode)
        self.menu.addAction(self.toggle_mode_action)  


    def create_control_buttons(self):
        if hasattr(self, 'button_widget'):
            self.button_widget.deleteLater()  # Supprimez l'ancien widget des boutons
        self.button_widget = QWidget()
        self.main_layout = QVBoxLayout() if self.vertical_mode else QHBoxLayout()

        self.label_bar = QLabel("Status: Ready")
        self.label_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.label_bar.setAlignment(Qt.AlignCenter)

        self.button_container = QWidget()
        self.button_layout = QVBoxLayout() if self.vertical_mode else QHBoxLayout()

        self.btn_next = QPushButton("Suivant\n" + self.next_patient_shortcut)
        self.btn_validate = QPushButton("Valider\n" + self.validate_patient_shortcut)
        self.btn_pause = QPushButton("Pause\n" + self.pause_shortcut)
        
        self.btn_next.clicked.connect(self.call_web_function_validate_and_call_next)
        self.btn_validate.clicked.connect(self.call_web_function_validate)
        self.btn_pause.clicked.connect(self.call_web_function_pause)

        for button in [self.btn_next, self.btn_validate, self.btn_pause]:
            button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
            self.button_layout.addWidget(button)
            
        # Create button for list of patients and and its menu
        self.btn_choose_patient = QPushButton(">>")
        self.choose_patient_menu = QMenu()

        self.btn_choose_patient.setMenu(self.choose_patient_menu)

        self.btn_choose_patient.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self.button_layout.addWidget(self.btn_choose_patient)
        
        # on recherche et rafraichit le patient en cours
        self.init_patient()
        
        # on recherche et rafraichit la liste des patient pour le Dropdown
        list_patients = self.init_list_patients()
        self.update_list_patient(list_patients)
        
            

        # Create the dropdown button and its menu
        self.btn_more = QPushButton("+")
        self.more_menu = QMenu()
        self.action_toggle_mode = QAction("Agrandir", self)
        self.action_deconnexion = QAction(f"Deconnexion {self.deconnect_shortcut}", self)
        self.action_toggle_orientation = QAction("Orientation", self)

        self.action_toggle_mode.triggered.connect(self.toggle_mode)
        self.action_deconnexion.triggered.connect(self.deconnexion_interface)
        self.action_toggle_orientation.triggered.connect(self.toggle_orientation)

        self.more_menu.addAction(self.action_toggle_mode)
        self.more_menu.addAction(self.action_deconnexion)
        self.more_menu.addAction(self.action_toggle_orientation)
        self.btn_more.setMenu(self.more_menu)

        self.btn_more.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self.button_layout.addWidget(self.btn_more)

        self.button_container.setLayout(self.button_layout)

        self.main_layout.addWidget(self.label_bar)
        self.main_layout.addWidget(self.button_container)

        self.button_widget.setLayout(self.main_layout)
        self.stacked_widget.addWidget(self.button_widget)
        self.button_widget.hide()


    def update_control_buttons_layout(self):
        self.create_control_buttons()
        if self.is_reduced_mode:
            self.stacked_widget.setCurrentWidget(self.button_widget)
            
    def toggle_mode(self):
        if self.is_reduced_mode:
            self.setMinimumSize(QSize(0, 0))
            self.setMaximumSize(QSize(16777215, 16777215))
            self.resize(800, 600)
            self.menuBar().show()
            self.stacked_widget.setCurrentWidget(self.browser)
            self.toggle_mode_action.setText("Mode réduit")

        else:
            self.resize_to_fit_buttons()
            self.menuBar().hide()
            self.stacked_widget.setCurrentWidget(self.button_widget)
            self.toggle_mode_action.setText("Mode normal")

        self.is_reduced_mode = not self.is_reduced_mode
        
    def resize_to_fit_buttons(self):
        self.button_widget.adjustSize()
        size_hint = self.button_widget.sizeHint()
        self.setMinimumSize(size_hint)
        self.setMaximumSize(size_hint)
        self.resize(size_hint)
        
    def toggle_orientation(self):
        self.vertical_mode = not self.vertical_mode
        self.update_control_buttons_layout()
        self.resize_to_fit_buttons()
        
    def show_preferences_dialog(self):
        dialog = PreferencesDialog(self)
        dialog.preferences_updated.connect(self.apply_preferences)
        if dialog.exec():
            self.load_preferences()
            self.load_url()
            self.setup_global_shortcut()
    
    def deconnexion_interface(self):
        print("deconnexion_interface")
        # Créer un nouveau widget pour l'interface de connexion
        login_widget = QWidget()
        login_layout = QVBoxLayout() if self.vertical_mode else QHBoxLayout()

        # Ajouter un label
        self.label_connexion = QLabel("Connectez-vous")
        login_layout.addWidget(self.label_connexion)

        # Ajouter un champ pour les initiales
        self.initials_input = QLineEdit()
        self.initials_input.setPlaceholderText("Entrez vos initiales")
        login_layout.addWidget(self.initials_input)
        # désactivation du champ à l'initialisation sinon le raccourci clavier est entré dans le champ
        self.initials_input.setDisabled(True)
        # réactivation après 100ms
        QTimer.singleShot(100, self.enable_initials_input)
        
        # Checkbox pour la deconnexion sur tous les autres postes
        self.checkbox_on_all = QCheckBox("Deconnexion sur tous les autres postes")
        login_layout.addWidget(self.checkbox_on_all)

        # Ajouter un bouton de validation
        validate_button = QPushButton("Valider")
        validate_button.clicked.connect(self.validate_login)
        login_layout.addWidget(validate_button)

        login_widget.setLayout(login_layout)

        # Remplacer le widget actuel par le widget de connexion
        self.stacked_widget.addWidget(login_widget)
        self.stacked_widget.setCurrentWidget(login_widget)

        # Connecter la touche Enter à la fonction de validation
        self.initials_input.returnPressed.connect(self.validate_login)
        
        print("deconnexion_interface 2")
        
        # Deconnexion sur le serveur
        url = f'{self.web_url}/app/counter/remove_staff'
        data = {'counter_id': self.counter_id}     
        headers = {'X-App-Token': self.app_token}
        self.disconnect_thread = RequestThread(url, self.session, method='POST', data=data, headers=headers)
        self.disconnect_thread.result.connect(self.handle_disconnect_result)
        self.disconnect_thread.start()
        
    def enable_initials_input(self):
        """ Permet d'activer le champ des initiales lors de l'initialisation + focus
        Obligé de le désactiver pour éviter entrée du raccourci clavier dans le champ """
        self.initials_input.setDisabled(False)
        # Donner le focus au champ des initiales
        self.initials_input.setFocus()
        
    @Slot(float, str, int)
    def handle_disconnect_result(self, elapsed_time, response_text, status_code):
        print("OK")
        print(status_code)
        print(response_text)
        if status_code == 200:
            # Remise à jour de la barre de titre
            self.update_window_title("Déconnecté")
            # Mise à jour de l'id staff
            self.staff_id = None
        else:
            # Afficher un message d'erreur
            QMessageBox.warning(self, "Erreur de connexion", "Impossible de se connecter. Veuillez réessayer.")
            

    def validate_login(self):
        if not self.app_token:
            print("Pas de token valide")
            return
        
        initials = self.initials_input.text()
        cb_deconnexion_on_all = self.checkbox_on_all.isChecked()
        if initials:
            url = f'{self.web_url}/app/counter/update_staff'
            data = {'initials': initials, 'counter_id': self.counter_id, "deconnect": True, "app": True}
            headers = {'X-App-Token': self.app_token}
            
            self.login_thread = RequestThread(url, self.session, method='POST', data=data, headers=headers)
            self.login_thread.result.connect(self.handle_login_result)
            self.login_thread.start()

    @Slot(float, str, int)
    def handle_login_result(self, elapsed_time, response_text, status_code):
        print("OK")
        print(status_code)
        print(response_text)
        if status_code == 200:
            response_data = json.loads(response_text)
            # Remise à jour de la barre de titre
            self.update_window_title(response_data["staff"]["name"])
            # Mise à jour de l'id staff
            self.staff_id = response_data["staff"]["id"]
            # Revenir à l'interface d'origine
            self.stacked_widget.setCurrentWidget(self.button_widget)
            # Mettre à jour l'interface si nécessaire
            self.init_patient()
        elif status_code == 204:
            print("Success:", response_text)
            print("Staff unknown")
            self.staff_id = False
            # Revenir à l'interface d'origine
            self.label_connexion.setText("Initiales incorrectes ! ")            
        else:
            # Afficher un message d'erreur
            QMessageBox.warning(self, "Erreur de connexion", "Impossible de se connecter. Veuillez réessayer.")
            
    
    def apply_preferences(self):
        self.load_preferences()
        self.setup_global_shortcut()
        self.update_control_buttons_layout()
            
    def load_url(self):
        counter_web_url = f'{self.web_url}/counter/{self.counter_id}'
        self.browser.setUrl(QUrl(counter_web_url))

    def on_load_finished(self, success):
        if not success:
            error_html = """
            <html>
            <head>
                <title>Erreur de connexion</title>
                <script type="text/javascript" src="qrc:///qtwebchannel/qwebchannel.js"></script>
                <script type="text/javascript">
                    function connectToPyQt() {{
                        new QWebChannel(qt.webChannelTransport, function(channel) {{
                            window.pyqt = channel.objects.pyqt;
                        }});
                    }}
                    window.onload = connectToPyQt;
                </script>
            </head>
            <body>
                <h1>Erreur de connexion au serveur</h1>
                <p>Impossible de se connecter au serveur à l'adresse suivante :</p>
                <p><strong>{web_url}</strong></p>
                <p>Veuillez vérifier que le serveur soit actif et que l'adresse du serveur soit bien configurée dans les préférences.</p>
                <p><button onclick="window.pyqt.pyqt_call_preferences()">Ouvrir les Préférences</button></p>
            </body>
            </html>
            """
            self.browser.setHtml(error_html.format(web_url=self.web_url))
            
    def on_url_changed(self, url):
        # Check if 'login' appears in the URL
        if "login" in url.toString():
            self.inject_login_script()            

    def inject_login_script(self):
        # Inject JavaScript to fill and submit the login form automatically
        script = f"""
        document.addEventListener('DOMContentLoaded', function() {{
        var usernameInput = document.querySelector('input[name="username"]');
        var passwordInput = document.querySelector('input[name="password"]');
        var rememberCheckbox = document.querySelector('input[name="remember"]');
        if (usernameInput && passwordInput) {{
            usernameInput.value = "{self.username}";
            passwordInput.value = "{self.password}";
            if (rememberCheckbox) {{
                rememberCheckbox.checked = true;
            }}
            var form = usernameInput.closest('form');
            if (form) {{
                form.submit();
                    }}
                }}
            }})();
            """
        self.browser.page().runJavaScript(script)
            
    def init_patient(self):
        url = f'{self.web_url}/api/counter/is_patient_on_counter/{self.counter_id}'
        response = requests.get(url)
        print(response)
        if response.status_code == 200:
            print("Success:", response)
            self.update_my_patient(response.json())
            self.update_my_buttons(response.json())
        else:
            print("Failed to retrieve data:", response.status_code)
            
            
    def init_list_patients(self):
        url = f'{self.web_url}/api/patients_list_for_pyside'
        response = requests.get(url)
        print(response.json())
        if response.status_code == 200:
            print("Success:", response)
            return response.json()
        else:
            print("Failed to retrieve data:", response.status_code)
            
    def call_web_function_validate_and_call_next(self):
        print("Call Web Function NEXT")
        url = f'{self.web_url}/validate_and_call_next/{self.counter_id}'
        self.thread = RequestThread(url, self.session)
        self.thread.result.connect(self.handle_result)
        self.thread.start()

    @Slot(float, str, int)
    def handle_result(self, elapsed_time, response_text, status_code):
        if status_code == 200:
            print("CODE 200!!!!")
            try:
                print("Success:", response_text)
                response_data = json.loads(response_text)
                self.update_my_patient(response_data)
                self.update_my_buttons(response_data)
            except json.JSONDecodeError as e:
                print("Failed to decode JSON:", e)
        else:
            print("Failed to retrieve data:", status_code)
        print("Elapsed time:", elapsed_time)

    
    def call_web_function_validate_and_call_specifique(self, patient_select_id):
            print("Call Web Function Specifique")
            url = f'{self.web_url}/call_specific_patient/{self.counter_id}/{patient_select_id}'
            self.thread = RequestThread(url, self.session)
            self.thread.result.connect(self.handle_result)
            self.thread.start()
            

   # Fonction pour valider un patient
    def call_web_function_validate(self):
        print("Call Web Function Validate")
        url = f'{self.web_url}/validate_patient/{self.counter_id}/{self.patient_id}'
        self.thread = RequestThread(url, self.session)
        self.thread.result.connect(self.handle_result)
        self.thread.start()

                    
    # Fonction pour mettre en pause un patient
    def call_web_function_pause(self):
        print("Call Web Function Pause")
        url = f'{self.web_url}/pause_patient/{self.counter_id}/{self.patient_id}'
        self.thread = RequestThread(url, self.session)
        self.thread.result.connect(self.handle_result)
        self.thread.start()


    def update_my_patient(self, patient):
        print("Update My Patient new", patient, type(patient))
        if patient["counter_id"] == self.counter_id:
            print(patient["id"], type(patient["id"]))
            if patient["id"] is None:
                self.patient_id = None
                self.label_bar.setText("Pas de patient en cours")
            else:
                self.patient_id = patient["id"]
                status = patient["status"]
                if status == "calling":
                    status_text = "En appel"
                elif status == "ongoing":
                    status_text = "Au comptoir"
                else:
                    status_text = "????"
                self.label_bar.setText(f"{patient['call_number']} {status_text} ({patient['activity']})")

    def update_my_buttons(self, patient):
        if patient["counter_id"] == self.counter_id:
            if patient["id"] is None:
                self.btn_pause.setEnabled(False)
                self.btn_validate.setEnabled(False)
            else:
                if patient["status"] == "calling":
                    self.btn_pause.setEnabled(False)
                    self.btn_validate.setEnabled(True)
                elif patient["status"] == "ongoing":
                    self.btn_pause.setEnabled(True)
                    self.btn_validate.setEnabled(False)


    def setup_global_shortcut(self):
        self.shortcut_thread = threading.Thread(target=self.setup_shortcuts, daemon=True)
        self.shortcut_thread.start()

    def setup_shortcuts(self):
        keyboard.add_hotkey(self.next_patient_shortcut, self.handle_next_patient_shortcut)
        keyboard.add_hotkey(self.validate_patient_shortcut, self.handle_validate_shortcut)
        keyboard.add_hotkey(self.pause_shortcut, self.handle_pause_shortcut)
        keyboard.add_hotkey(self.deconnect_shortcut, self.handle_deconnect_shortcut)

    def handle_next_patient_shortcut(self):
        self.btn_next.animateClick()
        self.call_web_function_validate_and_call_next()

    def handle_validate_shortcut(self):
        self.btn_validate.animateClick()
        self.call_web_function_validate()

    def handle_pause_shortcut(self):
        self.btn_pause.animateClick()
        self.call_web_function_pause()
        
    def handle_deconnect_shortcut(self):
        print("handle_deconnect_shortcut")
        QMetaObject.invokeMethod(self, 'deconnexion_interface', Qt.QueuedConnection)
        

    def new_patient(self, patient):
        print("new_patient", patient)
        #self.init_patient()
        self.update_patient_menu(patient)
        if self.is_reduced_mode:
            self.update_list_patient(patient)

    def update_patient_menu(self, patients):
        """ Mise a jour de la liste des patients le trayIcon """
        menu = QMenu()
        for patient in patients:
            action_text = f"{patient['call_number']} - {patient['activity']}"
            action = menu.addAction(action_text)
            action.triggered.connect(lambda checked, p=patient: self.select_patient(p['id']))
        self.trayIcon2.setContextMenu(menu)
        
        
    def update_list_patient(self, patients):
        """ Mise à jour de la liste des patients pour le bouton 'Choix' """
        self.choose_patient_menu.clear()  # Clear the menu before updating
        for patient in patients:
            print("patient entrée", patient)
            action_select_patient = QAction(f"{patient['call_number']} - {patient['activity']}", self)
            action_select_patient.triggered.connect(lambda checked, p=patient: self.select_patient(p['id']))
            self.choose_patient_menu.addAction(action_select_patient)
        self.btn_choose_patient.setMenu(self.choose_patient_menu) 
        

    def show_notification(self, data):
        if self.notification_specific_acts:
            self.trayIcon1.showMessage("Patient Update", data, QSystemTrayIcon.Information, 5000)

    @Slot()
    def pyqt_call_preferences(self):
        self.show_preferences_dialog()

    def select_patient(self, patient_select_id):
        self.call_web_function_validate_and_call_specifique(patient_select_id)


    def on_tray_icon_validation_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.call_web_function_validate()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            pass


    def on_tray_icon_call_next_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.call_web_function_validate_and_call_next()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            pass
        
    def on_tray_icon_pause_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.call_web_function_pause()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            pass


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    app.setApplicationName("PySide6 Web Browser Example")
    app.setOrganizationName("MyCompany")
    app.setOrganizationDomain("mycompany.com")
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
