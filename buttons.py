from PySide6.QtWidgets import QPushButton, QSizePolicy
from PySide6.QtCore import QTimer, Signal

class DebounceButton(QPushButton):
    """ Bouton qui permet d'éviter de cliquer deux fois dessus (en 500ms)"""
    clicked_with_debounce = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.debounce_time = 500  # Temps de débounce en millisecondes
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.on_debounce_timeout)
        self.clicked.connect(self.on_clicked)

        # Définir la politique de taille
        #self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)
        #self.setMinimumSize(30, 30)
        #self.setMaximumSize(30, 30)  # Taille minimale en pixels (ajustez selon vos besoins)

    def on_clicked(self):
        if not self.timer.isActive():
            self.setEnabled(False)
            self.timer.start(self.debounce_time)

    def on_debounce_timeout(self):
        self.setEnabled(True)
        self.clicked_with_debounce.emit()