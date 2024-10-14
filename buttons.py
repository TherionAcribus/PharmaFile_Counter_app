from PySide6.QtWidgets import QPushButton, QSizePolicy
from PySide6.QtCore import QTimer, Signal

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
