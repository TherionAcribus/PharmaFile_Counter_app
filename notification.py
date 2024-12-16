from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout, QApplication
from PySide6.QtCore import Qt, QTimer, Signal, QThread, QMetaObject, Slot
import json


class NotificationManager:
    def __init__(self):
        self.active_notifications = []
        self.spacing = 10  # Espacement vertical entre les notifications

    def add_notification(self, notification):
        self.active_notifications.append(notification)
        self.update_positions()
        
        # Connecter la fermeture de la notification
        notification.closed.connect(lambda: self.remove_notification(notification))
    
    def remove_notification(self, notification):
        if notification in self.active_notifications:
            self.active_notifications.remove(notification)
            self.update_positions()
    
    def update_positions(self):
        screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry()
        base_y = screen_geometry.bottomLeft().y()
        
        # Positionner les notifications de bas en haut
        current_y = base_y
        for notif in reversed(self.active_notifications):
            height = notif.sizeHint().height()
            notif.setGeometry(
                screen_geometry.bottomLeft().x() + 20,
                current_y - height - 20,
                300,
                height
            )
            current_y -= (height + self.spacing)


class CustomNotification(QDialog):

    closed = Signal()

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

        self.origin = notification_data["origin"]
        self.message = notification_data["message"]

        # Définir des couleurs par défaut
        self.background_color = "white"
        self.font_color = "black"

        self.sound = "ding"
        if self.origin == "activity":
            self.title = "Une nouvelle mission arrive !"
        elif self.origin == "printer_error":
            self.title = "Je crois qu'on a un problème..."
        elif self.origin == "low_paper":
            self.title = "Fin du rouleau !"
            self.background_color = "orange"
        elif self.origin == "no_paper":
            self.title = "Il n'y a plus de papier !"
            self.background_color = "red"
        elif self.origin == "paper_ok":
            self.title = "Vous faites bonne impression !"
            self.background_color = "light_green"
        elif self.origin == "patient_taken":
            self.title = "A une seconde près !"
        elif self.origin == "autocalling":
            self.title = "Ils arrivent !"
        elif self.origin == "new_patient":
            self.title = "Nouveau patient !"
        elif self.origin == "connection":
            self.title = "Problème de connexion"
        elif self.origin == "please_validate":
            self.title = "Sauvez un bébé phoque : validez votre patient !"
            self.sound = "please_validate"
            self.background_color = "red"
        elif self.origin == "disconnect_by_user":
            self.title = "Pousse toi de là !"
        elif self.origin == "test_notification":
            self.title = "Test micro, 1, 2, 3, Test..."
        elif self.origin == "socket_connection_true":
            self.title = "Tout va bien, on est branché !"
        elif self.origin == "socket_connection_false":
            self.title = "Quelqu'un s'est pris les pieds dans les cables !"
            self.background_color = "red"
        elif self.origin == "patient_for_staff_from_app":
            self.title = "Transfert de patient"
        else:
            self.title = self.origin

    def show(self):
        if not hasattr(self.parent(), 'notification_manager'):
            self.parent().notification_manager = NotificationManager()
        
        super().show()
        if self.audio_player:
            self.audio_player.play_sound(self.sound)
            
        # Ajouter cette notification au manager
        self.parent().notification_manager.add_notification(self)
        
        # Configurer le timer pour la fermeture automatique
        QTimer.singleShot(self.parent().notification_duration*1000, self.close)

    def close(self):
        # Ensure we close the notification in the main thread
        if QThread.currentThread() is QApplication.instance().thread():
            super().close()
        else:
            # If we're in a different thread, use invokeMethod to close in the main thread
            QMetaObject.invokeMethod(self, "close_from_main_thread", Qt.QueuedConnection)

    @Slot()
    def close_from_main_thread(self):
        super().close()

    def closeEvent(self, event):
        # Émettre le signal de fermeture avant de fermer
        self.closed.emit()
        super().closeEvent(event)

    def mousePressEvent(self, event):
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape: 
            self.close()
        else:
            super().keyPressEvent(event)
