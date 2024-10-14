import time
from PySide6.QtCore import QThread, Signal
from requests.exceptions import RequestException


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
