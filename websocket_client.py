import socketio
import json
import time
from PySide6.QtCore import Signal, QThread


class WebSocketClient(QThread):
    new_patient = Signal(object)
    new_notification = Signal(str)
    my_patient = Signal(object)
    change_paper = Signal(object)
    change_auto_calling = Signal(object)
    update_auto_calling = Signal(object)

    def __init__(self, parent, username="Counter App"):
        super().__init__()
        self.parent = parent
        self.username = username

        if "https" in self.parent.web_url:
            self.web_url = self.parent.web_url.replace("https", "wss")
        else:
            self.web_url = self.parent.web_url.replace("http", "ws")

        self.sio = socketio.Client(logger=True, engineio_logger=True)

        # Connexion aux événements WebSocket
        self.sio.on('connect', self.on_connect, namespace='/socket_app_counter')
        self.sio.on('disconnect', self.on_disconnect)
        self.sio.on('update', self.on_update, namespace='/socket_app_counter')
        self.sio.on('paper', self.on_paper, namespace='/socket_app_counter')
        self.sio.on('notification', self.on_notification, namespace='/socket_app_counter')     
        self.sio.on('change_auto_calling', self.on_change_auto_calling, namespace='/socket_app_counter')
        self.sio.on('update_auto_calling', self.on_update_auto_calling, namespace='/socket_app_counter')   


    def run(self):
        headers = {'username': self.username}

        while True:
            try:
                self.sio.connect(f"{self.web_url}/socket_app_counter", headers=headers)
                self.sio.wait()  # Maintenir la connexion ouverte
            except socketio.exceptions.ConnectionError as e:
                print(f"Connection lost: {e}")
                time.sleep(5)  # Attendre 5 secondes avant de tenter une reconnexion
                print("Attempting to reconnect...")

    def stop(self):
        self.sio.disconnect()
        self.quit()
        self.wait()

    def on_connect(self):
        print('WebSocket connected et c cool')

    def on_disconnect(self):
        print('WebSocket disconnected')
        
    def on_paper(self, data):
        print("Received paper:", data)
        self.change_paper.emit(data)
        
    def on_change_auto_calling(self, data):
        if self.parent.counter_id == int(data["data"]['counter_id']):
            self.change_auto_calling.emit(data)

    def on_update_auto_calling(self, data):
        if self.parent.counter_id == int(data["data"]['counter_id']):
            self.update_auto_calling.emit(data)
    
    def on_notification(self, data):
        print("Received notification:", data)
        # si on affiche à tous ou si on affiche seulement pour le counter
        if not data["flag"] or data["flag"] == self.parent.counter_id:
            self.new_notification.emit(data['data'])

    def on_update(self, data):
        print("Received update:", data)
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
            

