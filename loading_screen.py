"""Écran de chargement / fenêtre de journalisation (point 10.9).

Affiché pendant la séquence de démarrage (connexion au serveur, récupération de
l'état) : la fenêtre principale ne s'affiche qu'une fois l'initialisation
terminée. Il sert aussi de fenêtre de logs quand l'option « fenêtre de débogage »
est active — il branche pour cela un handler sur ``AppLogger``.
"""

from PySide6.QtCore import QCoreApplication, Qt, Signal
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from my_logger import AppLogger


class LoadingScreen(QWidget):
    # Relais thread-safe des lignes de log : le handler de journalisation
    # émet depuis le thread qui a produit le message (workers réseau,
    # WebSocket…) ; le signal est distribué dans le thread GUI, qui seul
    # peut toucher aux widgets (sinon crash natif silencieux).
    log_received = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PharmaFile")
        self.setFixedSize(400, 200)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)

        # Configuration de l'interface utilisateur
        self._setup_ui()

        # Obtention de l'instance du logger et ajout du handler UI. Le callback
        # est le Signal (émission thread-safe), PAS update_progress : la méthode
        # qui manipule les widgets n'est ainsi jamais appelée hors du thread GUI.
        self.app_logger = AppLogger.get_instance()
        self.log_received.connect(self.update_progress)
        self.ui_handler = self.app_logger.add_ui_handler(self.log_received.emit)
        self.logger = self.app_logger.get_logger()

    def _setup_ui(self):
        """Configure l'interface utilisateur"""
        layout = QVBoxLayout()
        self.label = QLabel("Logging de l'application...")
        self.progress = QPlainTextEdit()
        self.progress.setReadOnly(True)

        layout.addWidget(self.label)
        layout.addWidget(self.progress)
        self.setLayout(layout)

    def update_progress(self, message):
        """Met à jour l'affichage des logs dans l'interface"""
        self.progress.appendPlainText(message)
        self.progress.ensureCursorVisible()
        QCoreApplication.processEvents()

    def closeEvent(self, event):
        """Gestionnaire d'événement de fermeture"""
        if hasattr(self, 'ui_handler'):
            self.logger.removeHandler(self.ui_handler)
        super().closeEvent(event)
