import socketio
import json
import time
from PySide6.QtCore import Signal, QThread


class WebSocketClient(QThread):
    new_patient = Signal(object)
    new_notification = Signal(str)
    my_patient = Signal(object)

    def __init__(self, web_url):
        super().__init__()
        print(f"Web URL 1: {web_url}")
        if "https" in web_url:
            self.web_url = web_url.replace("https", "wss")
        else:
            self.web_url = web_url.replace("http", "ws")
        print(f"Web URL 2: {self.web_url}")

        self.sio = socketio.Client(logger=True, engineio_logger=True)
        print(f"Web URL: {self.web_url}")

        # Connexion aux événements WebSocket
        self.sio.on('connect', self.on_connect, namespace='/socket_app_counter')
        self.sio.on('disconnect', self.on_disconnect)
        self.sio.on('update', self.on_update, namespace='/socket_app_counter') 
        self.sio.on('*', self.on_update, namespace='/socket_app_counter')         


    def run(self):
        while True:
            try:
                self.sio.connect(self.web_url, namespaces=['/socket_app_counter'])
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


    def on_update(self, data):
        print("Received update:", data)
        try:
            if isinstance(data, str):
                data = json.loads(data)
            print(data)
            if data['type'] == 'notification_new_patient':
                self.new_notification.emit(data['message'])
            elif data['type'] == 'patient':
                if isinstance(data["list"], str):
                    data["list"] = json.loads(data["list"])
                print('liste', data["list"])
                print(type(data["list"]))
                self.new_patient.emit(data["list"])
            elif data['type'] == 'my_patient':
                print("my_patient", data["data"])
                self.my_patient.emit(data["data"])
        except json.JSONDecodeError as e:
            print(f"Failed to decode JSON: {e}")
            

