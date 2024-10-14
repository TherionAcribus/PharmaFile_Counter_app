import json
from PySide6.QtWidgets import QPushButton, QSizePolicy
from PySide6.QtCore import QTimer, Signal, QSize
from PySide6.QtGui import QIcon, QAction
from connections import RequestThread

class DebounceButton(QPushButton):
    """ Bouton qui permet d'éviter de cliquer deux fois dessus (en 500ms) """
    clicked_with_debounce = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.debounce_time = 500  # Temps de débounce en millisecondes
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.on_debounce_timeout)
        self.clicked.connect(self.on_clicked)
        self._user_enabled = True  # Nouvel attribut pour suivre l'état souhaité par l'utilisateur

    def on_clicked(self):
        if not self.timer.isActive() and self._user_enabled:
            super().setEnabled(False)
            self.timer.start(self.debounce_time)

    def on_debounce_timeout(self):
        if self._user_enabled:
            super().setEnabled(True)
            self.clicked_with_debounce.emit()

    def setEnabled(self, enabled):
        self._user_enabled = enabled
        if not enabled:
            self.timer.stop()  # Arrêter le timer si le bouton est désactivé
        super().setEnabled(enabled)

    def isEnabled(self):
        return self._user_enabled
        # Définir la politique de taille
        #self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)
        #self.setMinimumSize(30, 30)
        #self.setMaximumSize(30, 30)  # Taille minimale en pixels (ajustez selon vos besoins)

class IconeButton(DebounceButton):
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
        print("handle_response", elapsed_time, response_text, status_code, type(response_text))
        response_data = json.loads(response_text)
        if status_code == 200:
            if response_data["status"]:
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
