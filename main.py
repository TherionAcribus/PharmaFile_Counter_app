import sys
import os
import time
import json
import requests
import threading
import logging
from requests.exceptions import RequestException
import keyboard
from PySide6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon, QMenu, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox, QComboBox, QTextEdit, QGroupBox,  QStackedWidget, QWidget, QCheckBox, QSizePolicy, QSpacerItem, QPlainTextEdit, QScrollArea
from PySide6.QtCore import QUrl, Signal, Slot, QSettings, QThread, QTimer, Qt, QSize, QMetaObject, QCoreApplication, QFile, QTextStream
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtGui import QIcon, QAction, QTextCursor

from websocket_client import WebSocketClient
from preferences import PreferencesDialog

class LogHandler(logging.Handler):
    def __init__(self, update_callback):
        super().__init__()
        self.update_callback = update_callback

    def emit(self, record):
        log_entry = self.format(record)
        self.update_callback(log_entry)

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

def load_stylesheet(filename):
    file = QFile(filename)
    if file.open(QFile.ReadOnly | QFile.Text):
        stream = QTextStream(file)
        return stream.readAll()
    return ""


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
        print("Requesting URL:", self.url)
        start_time = time.time()
        try:
            if self.method == 'GET':
                response = self.session.get(self.url)
                print(response)
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


class IconeButton(QPushButton):
    def __init__(self, icon_path, icon_inactive_path, flask_url, tooltip_text, tooltip_inactive_text, state, parent=None):
        super().__init__(parent)
        
        self.icon_path = icon_path
        self.icon_inactive_path = icon_inactive_path
        self.flask_url = flask_url
        self.tooltip_text = tooltip_text
        self.tooltip_inactive_text = tooltip_inactive_text
        self.app_token = parent.app_token
        self.session = parent.session
        self.counter_id = parent.counter_id
        self.setFixedSize(50, 50)
        self.setIcon(QIcon(self.icon_path))
        self.setIconSize(QSize(50, 50))
        self.setStyleSheet("border: none;")
        self.state = state  # inactive, active, waiting        
        
        self.clicked.connect(self.toggle_state)
        self.update_button_icon()

    def toggle_state(self):
        if self.state == "inactive":
            self.state = "waiting"
            self.update_button_icon()
            self.send_request("activate")
        elif self.state == "active":
            self.state = "waiting"
            self.update_button_icon()
            self.send_request("deactivate")
            
    def change_state(self, state):
        self.state = state
        self.update_button_icon()
            
    def handle_response(self, elapsed_time, response_text, status_code):
        response_data = json.loads(response_text)
        print(response_data["status"], type(response_data["status"]))
        if status_code == 200:
            if response_data["status"] == True:
                self.state = "active"
            else:
                self.state = "inactive"
            #self.state == "active" if response_data["status"] else "inactive"
            print(f"Etat mis à jour : {self.state}")
            self.update_button_icon()
        else:
            self.state = "waiting"
            self.update_button_icon()
            print(f"Erreur {status_code}: {response_text}")

    def send_request(self, action):
        print(f"Envoi de la requête {action}", self.flask_url)
        url = f"{self.flask_url}"
        data = {'action': action,
                'counter_id': self.counter_id}
        headers = {'X-App-Token': self.app_token}

        self.request_thread = RequestThread(url, self.session, method='POST', data=data, headers=headers)
        self.request_thread.result.connect(self.handle_response)
        self.request_thread.start()

    def update_button_icon(self, state=None):
        if state:
            self.state = state
        print("update_button_icon", self.state)
        if self.state == "inactive":
            self.setIcon(QIcon(self.icon_inactive_path))
            self.setIconSize(QSize(50, 50))
            self.setEnabled(True)
            self.setToolTip(self.tooltip_inactive_text)
        elif self.state == "active":
            self.setIcon(QIcon(self.icon_path))
            self.setIconSize(QSize(50, 50))
            self.setEnabled(True)
            self.setToolTip(self.tooltip_text)
        elif self.state == "waiting":
            self.setIcon(QIcon(self.icon_path))
            self.setIconSize(QSize(50, 50))
            self.setEnabled(False)
            self.setToolTip("En attente d'une connexion")


class LoadingScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chargement")
        self.setFixedSize(400, 200)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)

        layout = QVBoxLayout()
        self.label = QLabel("Démarrage de l'application...")
        self.progress = QPlainTextEdit()
        self.progress.setReadOnly(True)

        layout.addWidget(self.label)
        layout.addWidget(self.progress)
        self.setLayout(layout)

        # Configurez le logger pour utiliser notre LogHandler
        self.logger = logging.getLogger("LoadingScreenLogger")
        self.logger.setLevel(logging.DEBUG)

        log_handler = LogHandler(self.update_progress)
        log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(log_handler)

        # Vous pouvez également ajouter un handler pour écrire dans un fichier ou la console
        file_handler = logging.FileHandler("application.log")
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(file_handler)

    def update_progress(self, message):
        self.progress.appendPlainText(message)
        self.progress.ensureCursorVisible()
        QCoreApplication.processEvents()


class MainWindow(QMainWindow):   

    patient_data_received = Signal(object)
    patient_id = None
    staff_id = None
    connected = False  # permet de savoir si on a réussi à se connecter
    add_paper = "waiting"
    autocalling = "waiting"
    list_patients = []  # liste des patient qui sera chargée au démarrage puis mise à jour via SocketIO

    def __init__(self):
        super().__init__()

        self.loading_screen = LoadingScreen()
        self.loading_screen.show()

        self.loading_screen.logger.info("Initialisation de la session...")
        self.session = requests.Session()  # Session HTTP persistante
        ##self.loading_screen.validate_last_line()

        self.load_preferences()

        self.loading_screen.logger.info("Test de la connexion...")
        self.app_token = None
        try:
            self.get_app_token()
            # si on a un token, on se considère comme connecté
            self.connected = True
            #self.loading_screen.update_last_line(" - OK ! Token obtenu")
        except Exception as e:
            print("Erreur lors de l'obtention du token :", e)
            self.connected = False
            #self.loading_screen.update_last_line(f"- Erreur : {e}")
            
        if self.connected:
            self.connexion_for_app_init()

        self.setup_ui()
        
        self.setup_user()
        
        self.start_socket_io_client(self.web_url)
        
        self.is_reduced_mode = False
        if self.start_with_reduce_mode:
            self.toggle_mode()

        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.always_on_top)
        self.show() 
        
        if not self.debug_window:
            self.loading_screen.close()

    def closeEvent(self, event):
        # Fermeture de la fenêtre secondaire quand la fenêtre principale est fermée
        if self.loading_screen:
            self.loading_screen.close()
        super().closeEvent(event)
        
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
        self.loading_screen.logger.info("Initialisation des préférences...")
        
        settings = QSettings()
        self.web_url = settings.value("web_url", "http://localhost:5000")
        self.username = settings.value("username", "admin")
        self.password = settings.value("password", "admin")
        self.counter_id = settings.value("counter_id", "1")
        self.next_patient_shortcut = settings.value("next_patient_shortcut", "Alt+S")
        self.validate_patient_shortcut = settings.value("validate_patient_shortcut", "Alt+V")
        self.pause_shortcut = settings.value("pause_shortcut", "Altl+P")
        self.recall_shortcut = settings.value("recall_shortcut", "Alt+R")
        self.deconnect_shortcut = settings.value("deconnect_shortcut", "Alt+D")
        self.notification_specific_acts = settings.value("notification_specific_acts", True, type=bool)
        self.always_on_top = settings.value("always_on_top", False, type=bool)
        self.start_with_reduce_mode = settings.value("start_with_reduce_mode", False, type=bool)
        self.horizontal_mode = settings.value("vertical_mode", False, type=bool)
        self.display_patient_list = settings.value("display_patient_list", False, type=bool)
        self.debug_window = settings.value("debug_window", False, type=bool)
        self.selected_skin = settings.value("selected_skin", "")

        #self.loading_screen.validate_last_line()
        
    def setup_ui(self):
        self.loading_screen.logger.info("Initialisation de l'interface...")
        icon_path = os.path.join(os.path.dirname(__file__), 'assets/images', 'next.ico')
        self.setWindowIcon(QIcon(icon_path))
        self.setWindowTitle("PharmaFile")
        self.resize(800, 600)         

        self.create_menu()      
        
        self.setup_systray()

        self.loading_screen.logger.info("Création et connexion du navigateur...")
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

        if self.connected:
            self.init_patient()        
            self.list_patients = self.init_list_patients()
            print(self.list_patients)
            self.update_patient_widget()
            self.update_patient_menu(self.list_patients)

        self.load_skin()

        self.setup_global_shortcut()
        
    def setup_systray(self):
        """ Création du Systray"""        
        self.loading_screen.logger.info("Création du Systray...")
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
        
        #self.loading_screen.validate_last_line()

    def load_skin(self):
        if self.selected_skin:
            qss_file = os.path.join("skins", f"{self.selected_skin}.qss")
            if os.path.exists(qss_file):
                with open(qss_file, "r") as f:
                    qss = f.read()
                    self.setStyleSheet(qss)
                    # Appliquer le style à toute l'application
                    QApplication.instance().setStyleSheet(qss)
        
    def setup_user(self):
        """ Va chercher le staff sur le comptoir """
        self.loading_screen.logger.info("Paramétrage de l'utilisateur...")
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
        self.loading_screen.logger.info("Création de la connexion Socket.IO...")
        print(f"Starting Socket.IO client with URL: {url}")
        self.socket_io_client = WebSocketClient(self)
        self.socket_io_client.new_patient.connect(self.new_patient)
        self.socket_io_client.new_notification.connect(self.show_notification)
        # refaire les deux fonctions en recupérant directement les valeurs plutôt que de renvoyer une requete
        self.socket_io_client.change_paper.connect(self.change_paper)
        self.socket_io_client.change_auto_calling.connect(self.change_auto_calling)
        self.socket_io_client.start()
        #self.loading_screen.validate_last_line()


    def create_menu(self):
        self.loading_screen.logger.info("Création du menu...")
        
        self.menu = self.menuBar().addMenu("Fichier")
        self.preferences_action = QAction("Préférences", self)
        self.preferences_action.triggered.connect(self.show_preferences_dialog)
        self.menu.addAction(self.preferences_action)
        
        self.toggle_mode_action = QAction("Mode réduit", self)
        self.toggle_mode_action.triggered.connect(self.toggle_mode)
        self.menu.addAction(self.toggle_mode_action)

        #self.loading_screen.validate_last_line()

    def create_control_buttons(self):
        self.loading_screen.logger.info("Création de l'interface réduite...")
        
        self._create_main_widget()
        self._create_label_bar()
        self._create_button_container()
        self._create_icon_widget()
        self._create_patient_list_widget()
        self._create_layouts()
        self._setup_stacked_widget()

    def _create_main_widget(self):
        if hasattr(self, 'button_widget'):
            self.button_widget.deleteLater()
        self.button_widget = QWidget()

    def _create_label_bar(self):
        self.label_bar = QLabel("Status: Ready")
        self.label_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.label_bar.setAlignment(Qt.AlignCenter)

    def _create_button_container(self):
        self.button_container = QWidget()
        self.button_layout = QVBoxLayout() if self.horizontal_mode else QHBoxLayout()
        
        self._create_main_buttons()
        self._create_choose_patient_button()
        self._create_more_button()

        self.button_container.setLayout(self.button_layout)

    def _create_main_buttons(self):
        buttons_config = [
            ("btn_next", "Suivant", self.next_patient_shortcut, self.call_web_function_validate_and_call_next),
            ("btn_validate", "Valider", self.validate_patient_shortcut, self.call_web_function_validate),
            ("btn_pause", "Pause", self.pause_shortcut, self.call_web_function_pause)
        ]

        for attr_name, text, shortcut, callback in buttons_config:
            button = QPushButton(f"{text}\n{shortcut}")
            button.clicked.connect(callback)
            button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
            setattr(self, attr_name, button)  # Stocke le bouton comme attribut de la classe
            self.button_layout.addWidget(button)

    def _create_choose_patient_button(self):
        self.btn_choose_patient = QPushButton(">>")
        self.choose_patient_menu = QMenu()
        self.btn_choose_patient.setMenu(self.choose_patient_menu)
        self.btn_choose_patient.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self.button_layout.addWidget(self.btn_choose_patient)

        self.loading_screen.logger.info("__ Connexion pour charger le patient en cours...")
        self.init_patient()

        self.loading_screen.logger.info("__ Connexion pour charger la liste des patients...")
        list_patients = self.init_list_patients()
        self.update_list_patient(list_patients)

    def _create_more_button(self):
        self.btn_more = QPushButton("+")
        self.more_menu = QMenu()

        actions = [
            ("Relancer l'appel", self.recall_shortcut, self.recall),
            ("Orientation", None, self.toggle_orientation),
            ("Deconnexion", self.deconnect_shortcut, self.deconnexion_interface),
            ("Agrandir", None, self.toggle_mode),
            ("Afficher/Masquer Liste Patients", None, self.toggle_patient_list)
        ]

        for text, shortcut, callback in actions:
            action = QAction(f"{text}{shortcut if shortcut else ''}", self)
            action.triggered.connect(callback)
            self.more_menu.addAction(action)

        self.btn_more.setMenu(self.more_menu)
        self.btn_more.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self.button_layout.addWidget(self.btn_more)

    def _create_icon_widget(self):
        self.icone_widget = QWidget()
        self.icone_layout = QHBoxLayout()
        
        self._create_auto_calling_button()
        self._create_paper_button()

        self.icone_widget.setLayout(self.icone_layout)

    def _create_auto_calling_button(self):
        self.loading_screen.logger.info("__ Connexion pour charger le bouton d'appel automatique...")
        self.btn_auto_calling = self._create_icon_button(
            "assets/images/loop_yes.ico",
            "assets/images/loop_no.ico",
            f'{self.web_url}/app/counter/auto_calling',
            "Desactiver l'appel automatique",
            "Activer l'appel automatique",
            self.autocalling
        )
        self.icone_layout.addWidget(self.btn_auto_calling)

    def _create_paper_button(self):
        self.loading_screen.logger.info("__ Connexion pour charger l'icone de changement de papier...")
        self.btn_paper = self._create_icon_button(
            "assets/images/paper_add.ico",
            "assets/images/paper.ico",
            f'{self.web_url}/app/counter/paper_add',
            "Indiquer que vous avez changé le papier",
            "Indiquer qu'il faut changer le papier",
            self.add_paper
        )
        self.icone_layout.addWidget(self.btn_paper)

    def _create_icon_button(self, icon_path, icon_inactive_path, flask_url, tooltip_text, tooltip_inactive_text, state):
        return IconeButton(
            icon_path=resource_path(icon_path),
            icon_inactive_path=resource_path(icon_inactive_path),
            flask_url=flask_url,
            tooltip_text=tooltip_text,
            tooltip_inactive_text=tooltip_inactive_text,
            state=state,
            parent=self
        )
    def _create_patient_list_widget(self):
        self.patient_list_widget = QWidget()
        self.patient_list_layout = QVBoxLayout()  # Changé en QVBoxLayout pour plus de flexibilité
        self.patient_list_layout.setContentsMargins(0, 0, 0, 0)
        self.patient_list_layout.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)

        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content) if not self.horizontal_mode else QHBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(0)

        self.scroll_area.setWidget(self.scroll_content)
        self.patient_list_layout.addWidget(self.scroll_area)

        self.patient_list_widget.setLayout(self.patient_list_layout)
        self.patient_list_widget.setStyleSheet("background-color: lightgray;")

        if self.horizontal_mode:
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.patient_list_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            self.patient_list_widget.setFixedWidth(70)  # Ajustez cette valeur selon vos besoins      

        else:
            self.scroll_area.setFixedHeight(50)
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.patient_list_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.patient_list_widget.setFixedHeight(50)


        self.patient_list_widget.setVisible(self.display_patient_list)

    def _create_layouts(self):
        self.main_layout = QVBoxLayout() if self.horizontal_mode else QHBoxLayout()
        self.main_layout.addWidget(self.label_bar)
        self.main_layout.addWidget(self.button_container)
        self.main_layout.addWidget(self.icone_widget)

        self.full_layout = QHBoxLayout() if self.horizontal_mode else QVBoxLayout()
        self.full_layout.addLayout(self.main_layout)
        self.full_layout.addWidget(self.patient_list_widget)

        self.button_widget.setLayout(self.full_layout)

    def _setup_stacked_widget(self):
        self.stacked_widget.addWidget(self.button_widget)
        self.button_widget.show()


    def recall(self):
        url = f"{self.web_url}/app/counter/relaunch_patient_call/{self.counter_id}"
        headers = {'X-App-Token': self.app_token}

        self.request_thread = RequestThread(url, self.session, method='POST', headers=headers)
        self.request_thread.start()


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
            # refresh browser
            self.load_url()
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
        self.loading_screen.logger.info("Changement de l'orientation...")
        self.horizontal_mode = not self.horizontal_mode
        self.update_control_buttons_layout()
        self.update_patient_widget()
        self.resize_to_fit_buttons()


    def toggle_patient_list(self):
        self.display_patient_list = not self.display_patient_list
        self.patient_list_widget.setVisible(self.display_patient_list)
        self.resize_to_fit_buttons()

    def _adjust_window_size(self):
        if self.display_patient_list:
            # Augmenter la taille pour afficher la liste des patients
            size_change = 200 if self.horizontal_mode else 100
            if self.horizontal_mode:
                new_width = self.width() + size_change
                self.setFixedWidth(new_width)
            else:
                new_height = self.height() + size_change
                self.setFixedHeight(new_height)
        else:
            # Réduire la taille à la plus petite possible
            self.adjustSize()
            
        # Forcer la mise à jour de l'interface
        self.update()
        QApplication.processEvents()

        
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
        login_layout = QVBoxLayout() if self.horizontal_mode else QHBoxLayout()

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
        if self.connected:
            counter_web_url = f'{self.web_url}/counter/{self.counter_id}'
            self.browser.setUrl(QUrl(counter_web_url))
        else:
            self.show_error_page()
            
    def show_error_page(self):
        error_page_path = os.path.join('templates', 'error_page.html')
        with open(error_page_path, 'r', encoding='utf-8') as file:
            error_html = file.read()
        
        error_html = error_html.replace('{web_url}', self.web_url)
        self.browser.setHtml(error_html, QUrl('file://'))

    def on_load_finished(self, success):
        if success and self.connected:
            current_url = self.browser.url().toString()
            if "login" in current_url:
                self.inject_login_script()
        elif not self.connected:
            self.show_error_page()
            
    def on_url_changed(self, url):
        # Check if 'login' appears in the URL
        print('url changed')
        if "login" in url.toString():
            self.inject_login_script()            

    def inject_login_script(self):
        print('injecting login script')
        script = f"""
        (function() {{
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
        try:
            response = requests.get(url)
            print(response)
            if response.status_code == 200:
                print("Success:", response)
                self.update_my_patient(response.json())
                self.update_my_buttons(response.json())
            else:
                print("Failed to retrieve data:", response.status_code)
        except RequestException as e:
            print(f"Connection lost: {e}")
            
            
    def init_list_patients(self):
        url = f'{self.web_url}/api/patients_list_for_pyside'
        try:
            response = requests.get(url)
            print(response.json())
            if response.status_code == 200:
                print("Success:", response)
                return response.json()
            else:
                print("Failed to retrieve data:", response.status_code)
        except RequestException as e:
            print(f"Connection lost: {e}")
            return []
            
    def call_web_function_validate_and_call_next(self):
        url = f'{self.web_url}/validate_and_call_next/{self.counter_id}'
        self.thread = RequestThread(url, self.session)
        self.thread.result.connect(self.handle_result)
        self.thread.start()

    @Slot(float, str, int)
    def handle_result(self, elapsed_time, response_text, status_code):
        if status_code == 200:
            try:
                print("Success:", response_text)
                response_data = json.loads(response_text)
                self.update_my_patient(response_data)
                self.update_my_buttons(response_data)
            except json.JSONDecodeError as e:
                print("Failed to decode JSON:", e)
        # plus de patient. Attention 204 ne permet pas de passer une info car 204 =pas de données
        elif status_code == 204:
            self.update_my_patient(None)
        else:
            print("Failed to retrieve data:", status_code)
        print("Elapsed time:", elapsed_time)

    
    def call_web_function_validate_and_call_specifique(self, patient_select_id):
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
        if patient is None:
            self.patient_id = None
            self.label_bar.setText("Plus de patients")
        else:
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
        keyboard.add_hotkey(self.recall_shortcut, self.recall)
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
        # mise à jour de self.patient
        self.list_patients = patient
        self.update_patient_menu(patient)
        if self.is_reduced_mode:
            self.update_list_patient(patient)
            self.update_patient_widget()

    def update_patient_menu(self, patients):
        """ Mise a jour de la liste des patients le trayIcon """
        menu = QMenu()       

        # Mise à jour du bouton 'Choix' selon qu'il y ait ou non des patients
        if patients:
            if len(patients) == 0:
                self.btn_choose_patient.setText("X")
            else:
                self.btn_choose_patient.setText(">>")
        else:
            self.btn_choose_patient.setText("X")

        # Ajout des patients dans le menu
        for patient in patients:
            action_text = f"{patient['call_number']} - {patient['activity']}"
            action = menu.addAction(action_text)
            action.triggered.connect(lambda checked, p=patient: self.select_patient(p['id']))
            
        self.trayIcon2.setContextMenu(menu)


    def update_patient_widget(self):
        """ Mise à jour de la liste des patients dans le scrollable layout """
        self.loading_screen.logger.info("Mise à jour de la liste des patients dans le scrollable layout")

        # Supprimer tous les widgets existants du scroll_content
        for i in reversed(range(self.scroll_content.layout().count())):
            widget = self.scroll_content.layout().itemAt(i).widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

        # Créer un nouveau layout
        new_layout = QVBoxLayout() if self.horizontal_mode else QHBoxLayout()
        
        # Remplacer l'ancien layout par le nouveau
        QWidget().setLayout(self.scroll_content.layout())
        self.scroll_content.setLayout(new_layout)
        self.scroll_layout = new_layout

        print("self.list_patients", self.list_patients)

        # Créer de nouveaux boutons pour chaque patient
        for patient in self.list_patients:
            button_text = patient['call_number']
            button = QPushButton(button_text)
            button.setFixedSize(60, 30)  # Taille fixe pour tous les boutons
            
            font = button.font()
            font.setPointSize(8)
            button.setFont(font)

            button.clicked.connect(lambda checked, id=patient["id"]: self.call_web_function_validate_and_call_specifique(id))
            self.scroll_layout.addWidget(button)

        # Ajouter un spacer
        self.scroll_layout.addStretch(0)

        # Forcer la mise à jour visuelle
        self.scroll_content.update()
        self.scroll_area.update()
        self.patient_list_widget.update()

        # Assurez-vous que le scroll_area affiche correctement le contenu
        self.scroll_area.setWidget(self.scroll_content)
        self.scroll_area.setWidgetResizable(True)

        # Forcer le recalcul de la géométrie
        QApplication.processEvents()
        self.scroll_content.updateGeometry()
        self.scroll_area.updateGeometry()
        self.patient_list_widget.updateGeometry()

        
    def update_list_patient(self, patients):
        """ Mise à jour de la liste des patients pour le bouton 'Choix' """
        self.choose_patient_menu.clear()  # Clear the menu before updating
        try:
            for patient in patients:
                print("patient entrée", patient)
                action_select_patient = QAction(f"{patient['call_number']} - {patient['activity']}", self)
                action_select_patient.triggered.connect(lambda checked, p=patient: self.select_patient(p['id']))
                self.choose_patient_menu.addAction(action_select_patient)
            self.btn_choose_patient.setMenu(self.choose_patient_menu) 
        except TypeError:
            print("Type error")

    def show_notification(self, data):
        if self.notification_specific_acts:
            self.trayIcon1.showMessage("Patient Update", data, QSystemTrayIcon.Information, 5000)

    def change_paper(self, data):
        self.add_paper = "active" if data["data"]["add_paper"] else "inactive"
        self.btn_paper.update_button_icon(self.add_paper)
        
    def change_auto_calling(self, data):
        self.autocalling = "active" if data["data"]["autocalling"] else "inactive"
        print(self.autocalling)
        self.btn_auto_calling.update_button_icon(self.autocalling)

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
        
    def connexion_for_app_init(self):
        self.loading_screen.logger.info("Initialisation du bouton d'appel automatique...")
        url = f'{self.web_url}/app/counter/init_app'
        data = {'counter_id': self.counter_id}
        headers = {'X-App-Token': self.app_token}
        self.init_thread = RequestThread(url, self.session, method='POST', data=data, headers=headers)
        self.init_thread.result.connect(self.handle_init_app)
        self.init_thread.start()
        
    def handle_init_app(self, elapsed_time, response_text, status_code):
        response_data = json.loads(response_text)
        if status_code == 200:
            self.autocalling = "active" if response_data['autocalling'] else "inactive"
            self.add_paper = "active" if response_data['add_paper'] else "inactive"


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    app.setApplicationName("PySide6 Web Browser Example")
    app.setOrganizationName("MyCompany")
    app.setOrganizationDomain("mycompany.com")

    stylesheet = load_stylesheet("Incrypt.qss")
    app.setStyleSheet(stylesheet)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
