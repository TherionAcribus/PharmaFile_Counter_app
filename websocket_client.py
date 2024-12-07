import socketio
import json
import time
from PySide6.QtCore import Signal, QThread


class WebSocketClient(QThread):
    new_patient = Signal(object)
    new_notification = Signal(str)
    my_patient = Signal(object)
    change_paper = Signal(object)
    change_paper_button = Signal(str)
    change_auto_calling = Signal(object)
    update_auto_calling = Signal(object)
    disconnect_user = Signal(object)
    ws_connection_status = Signal(bool, int, bool)
    connection_lost = Signal(int)
    refresh_after_clear_patient_list = Signal(bool)

    def __init__(self, parent, username="Counter App"):
        super().__init__()
        self.parent = parent
        self.username = username
        self.previously_connected = False

        if "https" in self.parent.web_url:
            self.web_url = self.parent.web_url.replace("https", "wss")
        else:
            self.web_url = self.parent.web_url.replace("http", "ws")

        self.sio = socketio.Client(logger=True, engineio_logger=True)
        self.setup_socketio_events()

    def setup_socketio_events(self):
        # Connexion aux événements WebSocket
        self.sio.on('connect', self.on_connect, namespace='/socket_app_counter')
        self.sio.on('disconnect', self.on_disconnect)
        self.sio.on('update', self.on_update, namespace='/socket_app_counter')
        self.sio.on('paper', self.on_paper, namespace='/socket_app_counter')
        self.sio.on('notification', self.on_notification, namespace='/socket_app_counter')     
        self.sio.on('change_auto_calling', self.on_change_auto_calling, namespace='/socket_app_counter')
        self.sio.on('update_auto_calling', self.on_update_auto_calling, namespace='/socket_app_counter')   
        self.sio.on('disconnect_user', self.on_disconnect_user, namespace='/socket_app_counter')
        self.sio.on('update_patient_list', self.on_update_patient_list, namespace='/socket_app_counter')
        self.sio.on('refresh_after_clear_patient_list', self.on_refresh_after_clear_patient_list, namespace='/socket_app_counter')

    def run(self):
        headers = {'username': self.username}
        reconnection_attempts = 0
        max_reconnection_delay = 30
        initial_delay = 5

        while True:
            try:
                if reconnection_attempts > 0:
                    delay = min(initial_delay * reconnection_attempts, max_reconnection_delay)
                    print(f"Waiting {delay} seconds before reconnection attempt {reconnection_attempts}")
                    time.sleep(delay)
                    
                print(f"Attempting to connect to {self.web_url}/socket_app_counter")
                self.sio.connect(f"{self.web_url}/socket_app_counter", headers=headers)
                
                print("Connection successful!")
                reconnection_attempts = 0
                self.sio.wait()

            except socketio.exceptions.ConnectionError as e:
                reconnection_attempts += 1
                print(f"Connection attempt {reconnection_attempts} failed: {str(e)}")
                self.connection_lost.emit(reconnection_attempts)  # Émet le signal de déconnexion

    def stop(self):
        self.sio.disconnect()
        self.quit()
        self.wait()

    def on_connect(self):
        print('WebSocket connected')
        self.ws_connection_status.emit(True, 0, True)

    def on_disconnect(self):
        print('WebSocket disconnected')
        self.connection_lost.emit(0) 
        
    def on_paper(self, data):
        print("Received paper:", data)
        self.change_paper.emit(data)
        
    def on_change_auto_calling(self, data):
        if self.parent.counter_id == int(data["data"]['counter_id']):
            self.change_auto_calling.emit(data)

    def on_update_auto_calling(self, data):
        if self.parent.counter_id == int(data["data"]['counter_id']):
            self.update_auto_calling.emit(data)

    def on_disconnect_user(self, data):
        print("DISCONNECT")
        print(data)
        if self.parent.counter_id == int(data["data"]['counter_id']):
            self.disconnect_user.emit(data)
    
    def on_notification(self, data):
        print("Received notification:", data)
        
        # Parser data["data"] si c'est une chaîne JSON
        if isinstance(data["data"], str):
            try:
                notification_data = json.loads(data["data"])
            except json.JSONDecodeError:
                print("Error parsing notification data")
                return
        else:
            notification_data = data["data"]

        # si on affiche à tous ou si on affiche seulement pour le counter
        if (
            not data["flag"] or  # Cas où tout le monde peut voir la notification
            data["flag"] == self.parent.counter_id or  # Cas où le counter_id correspond directement
            (isinstance(data["flag"], list) and self.parent.counter_id in data["flag"])  # Cas où flag est une liste et contient le counter_id
        ):
            self.new_notification.emit(data['data'])
        
        # si la notification concerne le papier, mettre à jour le bouton
        if notification_data["origin"] in ["no_paper", "low_paper", "paper_ok"]:
            self.change_paper_button.emit(notification_data["origin"])

    def on_update_patient_list(self, data):
        print('nouvelle liste de patients', data)
        try:
            if isinstance(data, str):
                data = json.loads(data)
            if isinstance(data["data"], str):
                data["data"] = json.loads(data["data"])
            self.new_patient.emit(data["data"])
            self.my_patient.emit(data["data"])

        except json.JSONDecodeError as e:
            print(f"Failed to decode JSON: {e}")

    def on_refresh_after_clear_patient_list(self, data):
        print('nouvelle liste de patients', data)
        self.refresh_after_clear_patient_list.emit(True)
        
    def on_update(self, data):
        print("Received update:", data)
        # Normalement cette partie peut être supprimée
        try:
            if isinstance(data, str):
                data = json.loads(data)
            if data['flag'] == 'update_patient_list':
                if isinstance(data["data"], str):
                    data["data"] = json.loads(data["data"])
                self.new_patient.emit(data["data"])
            elif data['flag'] == 'my_patient':
                self.my_patient.emit(data["data"])
        except json.JSONDecodeError as e:
            print(f"Failed to decode JSON: {e}")
            

