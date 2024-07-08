import sys
import requests
import keyboard
import socketio
from PySide6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon, QMenu
from PySide6.QtCore import QUrl, Signal, Slot
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtGui import QIcon

sio = socketio.Client(logger=True, engineio_logger=True)

@sio.event
def connect():
    print("I'm connected!")

@sio.event
def connect_error(data):
    print("The connection failed!")

@sio.event
def disconnect():
    print("I'm disconnected!")
    
counter_number = 1

adresse="http://localhost:5000"
#adresse="http://gestionfile.onrender.com"

class MainWindow(QMainWindow):
    patient_data_received = Signal(object)  # Signal passant les données des patients
    patient_id = None
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Web Browser in PySide6")
        self.resize(800, 600)
        self.patient_number = 1
        
        # Connectez le signal au slot qui mettra à jour l'interface utilisateur
        #self.patient_data_received.connect(self.update_patient_menu)
        # Connect to the WebSocket server
        #sio.connect('https://gestionfile.onrender.com:443')

        sio.connect("http://localhost:5000/socket_update_patient")
        print('my sid is', sio.sid)


        # Première icône de tray
        self.trayIcon1 = QSystemTrayIcon(QIcon("assets/images/next.ico"), self)
        self.trayIcon1.setToolTip("Tray Icon 1")
        tray_menu1 = QMenu()
        open_action1 = tray_menu1.addAction("Open Main Window")
        open_action1.triggered.connect(self.show)
        self.trayIcon1.setContextMenu(tray_menu1)
        self.trayIcon1.show()

        # Deuxième icône de tray
        self.trayIcon2 = QSystemTrayIcon(QIcon("assets/images/next.ico"), self)
        self.trayIcon2.setToolTip("Prochain patient")
        tray_menu2 = QMenu()
        open_action2 = tray_menu2.addAction("Call Web Function")
        open_action2.triggered.connect(self.call_web_function_validate_and_call_next)
        self.trayIcon2.setContextMenu(tray_menu2)
        self.trayIcon2.activated.connect(self.on_tray_icon_call_next_activated)  # Connecter le signal activated
        self.trayIcon2.show()

        # Deuxième icône de tray
        self.trayIcon3 = QSystemTrayIcon(QIcon("assets/images/next.ico"), self)
        self.trayIcon3.setToolTip("Valider patient")
        tray_menu3 = QMenu()
        open_action3 = tray_menu2.addAction("Call Web Function")
        open_action3.triggered.connect(self.call_web_function_validate)
        self.trayIcon3.setContextMenu(tray_menu3)
        self.trayIcon3.activated.connect(self.on_tray_icon_validation_activated)  # Connecter le signal activated
        self.trayIcon3.show()

        # Création de la vue du navigateur web
        #self.browser = QWebEngineView()
        #self.setCentralWidget(self.browser)
        #self.browser.setUrl(QUrl(adresse + "/counter/1"))

    def call_web_function_validate_and_call_next(self):
        print("Call Web Function NEXT")
        url = f'{adresse}/validate_and_call_next/{counter_number}'
        response = requests.get(url)  # Envoie une requête GET à l'URL
        if response.status_code == 200:
            print("Success:", response.text)
        else:
            print("Failed to retrieve data:", response.status_code)

    
    def call_web_function_validate_and_call_specifique(self, patient_select_id):
        print("Call Web Function Specifique")
        url = f'{adresse}/call_specific_patient/{counter_number}/{patient_select_id}'
        response = requests.get(url)  # Envoie une requête GET à l'URL
        if response.status_code == 200:
            print("Success:", response.text)
        else:
            print("Failed to retrieve data:", response.status_code)

    def call_web_function_validate(self):
        print("Call Web Function Validate")
        url =f'{adresse}/validate_patient/{counter_number}/{self.patient_id}' 
        response = requests.get(url)  # Envoie une requête GET à l'URL
        if response.status_code == 200:
            print("Success:", response.text)
        else:
            print("Failed to retrieve data:", response.status_code)


    def setup_global_shortcut(self):
        # Configurer un raccourci clavier global
        keyboard.add_hotkey('ctrl+<', self.call_web_function_validate_and_call_next)  # Vous pouvez changer la combinaison




    @Slot(object)
    def update_patient_menu(self, patients):
        menu = QMenu()
        for patient in patients:
            action_text = f"{patient['call_number']} - {patient['visit_reason']}"
            action = menu.addAction(action_text)
            # Ajouter un argument supplémentaire dans la lambda pour capturer le booléen
            action.triggered.connect(lambda checked, p=patient: self.select_patient(p['id']))
        self.trayIcon2.setContextMenu(menu)

    def select_patient(self, patient_select_id):
        print(f"Patient {patient_select_id} selected")
        self.call_web_function_validate_and_call_specifique(patient_select_id)


    def on_tray_icon_validation_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # Left click
            self.call_web_function_validate()
        elif reason == QSystemTrayIcon.ActivationReason.Context:  # Right click, handled by Qt automatically
            pass


    def on_tray_icon_call_next_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # Left click
            self.call_web_function_validate_and_call_next()
        elif reason == QSystemTrayIcon.ActivationReason.Context:  # Right click, handled by Qt automatically
            #self.call_web_function_validate_and_call_specifique()
            pass



    def closeEvent(self, event):
        sio.disconnect()

if __name__ == "__main__":
    app = QApplication(sys.argv)

   
    # Nécessaire pour les applications QtWebEngine
    app.setApplicationName("PySide6 Web Browser Example")
    app.setOrganizationName("MyCompany")
    app.setOrganizationDomain("mycompany.com")

    window = MainWindow()
    window.setup_global_shortcut()
    window.show()
    sys.exit(app.exec())