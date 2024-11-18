from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout, QApplication
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
import json

class CustomNotification(QDialog):
    def __init__(self, data, font_size=None, parent=None, internal=False):        
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)

        self.internal = internal
        self.audio_player = self.parent().audio_player

        self.format_data(data)

        font_size = font_size or self.parent().notification_font_size
        print(font_size)

        self.setStyleSheet(f"""
            QDialog {{
                border: 1px solid black;
                background-color: {self.background_color};  /* Couleur de fond dynamique */
                border-radius: 5px;
            }}
            QLabel#titleLabel {{
                font-size: {font_size + 4}pt; /* Taille spécifique pour le titre */
                font-weight: bold;
                color: {self.font_color};  /* Couleur de texte dynamique */
            }}
            QLabel#messageLabel {{
                font-size: {font_size}pt; /* Taille spécifique pour le message */
                color: {self.font_color};  /* Couleur de texte dynamique */
            }}
        """)
        
        layout = QVBoxLayout()
        
        # Ajout d'un layout horizontal pour le titre et le bouton de fermeture
        top_layout = QHBoxLayout()
        
        # Ajout du titre
        title_label = QLabel(self.title)
        title_label.setObjectName("titleLabel")  # Pour que le style s'applique au titre
        top_layout.addWidget(title_label)
        
        top_layout.addStretch()
        
        close_button = QPushButton("×")
        close_button.setFixedSize(20, 20)
        close_button.clicked.connect(self.close)
        top_layout.addWidget(close_button)
        
        layout.addLayout(top_layout)
        
        # Ajout d'une ligne de séparation
        separator = QLabel()
        separator.setStyleSheet("background-color: rgba(255, 255, 255, 0.2); min-height: 1px; max-height: 1px;")
        layout.addWidget(separator)
        
        message_label = QLabel(self.message)
        message_label.setObjectName("messageLabel")  # Pour que le style s'applique au message
        message_label.setAlignment(Qt.AlignCenter)
        message_label.setWordWrap(True)
        layout.addWidget(message_label)
        
        self.setLayout(layout)

        # Positionner la notification en bas à gauche
        screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry()
        self.setGeometry(
            screen_geometry.bottomLeft().x() + 20,
            screen_geometry.bottomLeft().y() - self.sizeHint().height() - 20,
            300,
            120  # Augmenté légèrement pour accommoder le titre
        )
    
    def format_data(self, notification_data):
        print("Notification recue :", notification_data)
        print(type(notification_data))
        if not self.internal:
            try:
                notification_data = json.loads(notification_data)
                print("Notification data :", notification_data)
            except json.JSONDecodeError:
                self.parent().logger.error("Failed to decode JSON data")

        origin = notification_data["origin"]
        self.message = notification_data["message"]

        # Définir des couleurs par défaut
        self.background_color = "white"
        self.font_color = "black"

        self.sound = "ding"
        if origin == "activity":
            self.title = "Une nouvelle mission arrive !"
        elif origin == "printer_error":
            self.title = "Je crois qu'on a un problème..."
        elif origin == "printer_paper":
            self.title = "Papier, s'il vous plait !"
            self.background_color = "orange"
        elif origin == "patient_taken":
            self.title = "A une seconde près !"
        elif origin == "autocalling":
            self.title = "Ils arrivent !"
        elif origin == "new_patient":
            self.title = "Nouveau patient !"
        elif origin == "connection":
            self.title = "Problème de connexion"
        elif origin == "please_validate":
            self.title = "Sauvez un bébé phoque : validez votre patient !"
            self.sound = "please_validate"
            self.background_color = "red"
        elif origin == "disconnect_by_user":
            self.title = "Pousse toi de là !"
        elif origin == "test_notification":
            self.title = "Test micro, 1, 2, 3, Test..."
        else:
            self.title = origin

    def show(self):
        super().show()
        if self.audio_player:
            self.audio_player.play_sound(self.sound)
        QTimer.singleShot(self.parent().notification_duration*1000, self.close)

    def mousePressEvent(self, event):
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape: 
            self.close()
        else:
            super().keyPressEvent(event)

