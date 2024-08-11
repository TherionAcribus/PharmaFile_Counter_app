import sys
import os
import time
import json
import requests
import threading
import socketio
from requests.exceptions import RequestException
import keyboard
from PySide6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon, QMenu, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox, QComboBox, QTextEdit, QGroupBox,  QStackedWidget, QWidget, QCheckBox, QSizePolicy, QSpacerItem
from PySide6.QtCore import QUrl, Signal, Slot, QSettings, QThread, QTimer, Qt, QSize, QMetaObject, QCoreApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtGui import QIcon, QAction, QKeySequence, QKeyEvent, QTextCursor
from datetime import datetime

from websocket_client import WebSocketClient
from preferences import PreferencesDialog

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


class IconeButton(QPushButton):
    def __init__(self, icon_path, icon_inactive_path, flask_url, tooltip_text, tooltip_inactive_text, parent=None):
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
        self.state = "waiting"  # inactive, active, waiting
        
        self.send_request(action=None)
        
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
        print("REPONSE", response_text, status_code)
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

    def update_button_icon(self):
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
        self.progress = QTextEdit()
        self.progress.setReadOnly(True)

        layout.addWidget(self.label)
        layout.addWidget(self.progress)
        self.setLayout(layout)

    def update_progress(self, message):
        self.progress.append(message)
        self.progress.repaint()
        QCoreApplication.processEvents()
        
    def validate_last_line(self):
        self.update_last_line(" - OK !")

    def update_last_line(self, additional_info):
        cursor = self.progress.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.movePosition(QTextCursor.StartOfLine, QTextCursor.KeepAnchor)
        cursor.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor)
        last_line = cursor.selectedText()
        
        new_line = f"{last_line} {additional_info}"
        cursor.removeSelectedText()
        cursor.insertText(new_line)
        
        self.progress.setTextCursor(cursor)
        self.progress.ensureCursorVisible()
        self.progress.repaint()
        QCoreApplication.processEvents()
        

class MainWindow(QMainWindow):   
    
    patient_data_received = Signal(object)
    patient_id = None
    staff_id = None
    connected = False  # permet de savoir si on a réussi à se connecter
    
    def __init__(self):
        super().__init__()
        
        self.loading_screen = LoadingScreen()
        self.loading_screen.show()
        
        self.loading_screen.update_progress("Initialisation de la session...")
        self.session = requests.Session()  # Session HTTP persistante
        self.loading_screen.validate_last_line()

        self.load_preferences()

        self.loading_screen.update_progress("Test de la connexion...")
        self.app_token = None
        try:
            self.get_app_token()
            # si on a un token, on se considère comme connecté
            self.connected = True
            self.loading_screen.update_last_line(" - OK ! Token obtenu")
        except Exception as e:
            print("Erreur lors de l'obtention du token :", e)
            self.connected = False
            self.loading_screen.update_last_line(f"- Erreur : {e}")
            
        self.setup_ui()
        
        self.setup_user()
        
        self.start_socket_io_client(self.web_url)
        
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
        self.loading_screen.update_progress("Initialisation des préférences...")
        
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
        
        self.loading_screen.validate_last_line()
        
    def setup_ui(self):
        self.loading_screen.update_progress("Initialisation de l'interface...")
        icon_path = os.path.join(os.path.dirname(__file__), 'assets/images', 'next.ico')
        self.setWindowIcon(QIcon(icon_path))
        self.setWindowTitle("PharmaFile")
        self.resize(800, 600)         

        self.create_menu()      
        
        self.setup_systray()

        self.loading_screen.update_progress("Création et connexion du navigateur...")
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
            list_patients = self.init_list_patients()
            self.update_patient_menu(list_patients)

        self.setup_global_shortcut()
        
    def setup_systray(self):
        """ Création du Systray"""        
        self.loading_screen.update_progress("Création du Systray...")
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
        
    def setup_user(self):
        """ Va chercher le staff sur le comptoir """
        self.loading_screen.update_progress("Paramétrage de l'utilisateur...")
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
        self.loading_screen.update_progress("Création de la connexion Socket.IO...")
        print(f"Starting Socket.IO client with URL: {url}")
        self.socket_io_client = WebSocketClient(self)
        self.socket_io_client.new_patient.connect(self.new_patient)
        self.socket_io_client.new_notification.connect(self.show_notification)
        self.socket_io_client.change_paper.connect(self.change_paper)
        self.socket_io_client.change_auto_calling.connect(self.change_auto_calling)
        self.socket_io_client.start()
        self.loading_screen.validate_last_line()


    def create_menu(self):
        self.loading_screen.update_progress("Création du menu...")
        
        self.menu = self.menuBar().addMenu("Fichier")
        self.preferences_action = QAction("Préférences", self)
        self.preferences_action.triggered.connect(self.show_preferences_dialog)
        self.menu.addAction(self.preferences_action)
        
        self.toggle_mode_action = QAction("Mode réduit", self)
        self.toggle_mode_action.triggered.connect(self.toggle_mode)
        self.menu.addAction(self.toggle_mode_action)

        self.loading_screen.validate_last_line()

    def create_control_buttons(self):
        self.loading_screen.update_progress("Création de l'interface réduite...")
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
        
        autocalling_icon_path = resource_path("assets/images/loop_yes.ico")
        autocalling_icon_inactive_path = resource_path("assets/images/loop_no.ico")
        autocalling_url = f'{self.web_url}/app/counter/auto_calling'
        self.btn_auto_calling = IconeButton(
            icon_path = autocalling_icon_path,
            icon_inactive_path = autocalling_icon_inactive_path,
            flask_url = autocalling_url,
            tooltip_inactive_text = "Activer l'appel automatique",
            tooltip_text="Desactiver l'appel automatique",
            parent = self
        )
        self.button_layout.addWidget(self.btn_auto_calling)
        
        paper_icon_path = resource_path("assets/images/paper_add.ico")
        paper_icon_inactive_path = resource_path("assets/images/paper.ico")
        paper_url = f'{self.web_url}/app/counter/paper_add'
        self.btn_paper = IconeButton(
            icon_inactive_path = paper_icon_path,
            icon_path = paper_icon_inactive_path,
            flask_url = paper_url,
            tooltip_text = "Indiquer qu'il faut changer le papier",
            tooltip_inactive_text = "Indiquer que vous avez changé le papier",
            parent = self
        )
        self.button_layout.addWidget(self.btn_paper)

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
        if not success or not self.connected:
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
        print("Call Web Function NEXT")
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
        self.btn_paper.send_request(None)
        
    def change_auto_calling(self, data):
        self.btn_auto_calling.send_request(None)

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
