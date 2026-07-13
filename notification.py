from collections import deque

from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout, QApplication
from PySide6.QtCore import Qt, QTimer, Signal, QThread, QMetaObject, Slot
from PySide6.QtGui import QGuiApplication
import json
import logging

from notification_layout import (
    DEFAULT_MAX_VISIBLE,
    DEFAULT_MARGIN,
    DEFAULT_SPACING,
    NOTIFICATION_WIDTH,
    compute_stack_positions,
    normalize_corner,
    notification_signature,
    should_queue,
)

logger = logging.getLogger("appcomptoir.notification")


def _extract_origin_message(data, internal):
    """Origine + message d'une notification, quel que soit le format d'entrée
    (dict interne ou chaîne JSON venue du serveur). Sert au calcul de signature."""
    payload = data
    if not internal and isinstance(data, str):
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return (str(data), "")
    if isinstance(payload, dict):
        return (payload.get("origin", ""), payload.get("message", ""))
    return (str(payload), "")


class NotificationManager:
    """Orchestre l'affichage des notifications : écran cible = celui de la fenêtre
    principale, empilement dans un coin configurable, déduplication des messages
    identiques, limite du nombre visible et mise en file du surplus."""

    def __init__(self, main_window, max_visible=DEFAULT_MAX_VISIBLE,
                 spacing=DEFAULT_SPACING, margin=DEFAULT_MARGIN):
        self.main_window = main_window
        self.max_visible = max_visible
        self.spacing = spacing
        self.margin = margin
        self.active_notifications = []
        self.pending = deque()
        # signature -> notification visible, pour dédupliquer et raviver le timer.
        self._active_signatures = {}

    # --- API publique ---------------------------------------------------

    def notify(self, data, internal=False, font_size=None):
        """Point d'entrée unique. Déduplique, puis affiche ou met en file.
        Retourne la notification affichée, ou None (dupliquée / mise en file)."""
        origin, message = _extract_origin_message(data, internal)
        signature = notification_signature(origin, message)

        # Déjà visible : on ne l'empile pas, on prolonge simplement son affichage.
        existing = self._active_signatures.get(signature)
        if existing is not None:
            existing.restart_auto_close()
            logger.debug("Notification dupliquée ignorée (origin=%s)", origin)
            return None

        # Déjà en file : on ignore le doublon.
        if any(sig == signature for (_d, _i, _f, sig) in self.pending):
            logger.debug("Notification dupliquée déjà en file (origin=%s)", origin)
            return None

        spec = (data, internal, font_size, signature)
        if should_queue(len(self.active_notifications), self.max_visible):
            self.pending.append(spec)
            logger.debug("Notification mise en file (%s en attente)", len(self.pending))
            return None
        return self._create_and_show(spec)

    def update_positions(self):
        """Repositionne toutes les notifications visibles sur l'écran courant de
        la fenêtre principale (et non plus toujours l'écran principal)."""
        screen = self._target_screen()
        geo = screen.availableGeometry()
        rect = (geo.x(), geo.y(), geo.width(), geo.height())
        sizes = [(NOTIFICATION_WIDTH, max(1, n.sizeHint().height()))
                 for n in self.active_notifications]
        corner = normalize_corner(getattr(self.main_window, "notification_corner", None))
        positions = compute_stack_positions(rect, sizes, corner, self.spacing, self.margin)
        for notif, (x, y, w, h) in zip(self.active_notifications, positions):
            notif.setGeometry(x, y, w, h)

    # --- Interne --------------------------------------------------------

    def _create_and_show(self, spec):
        data, internal, font_size, signature = spec
        notif = CustomNotification(data=data, font_size=font_size,
                                   parent=self.main_window, internal=internal,
                                   manager=self)
        notif.signature = signature
        self.active_notifications.append(notif)
        self._active_signatures[signature] = notif
        notif.closed.connect(lambda n=notif: self._on_closed(n))
        notif.show_without_activating()
        self.update_positions()
        duration_s = getattr(self.main_window, "notification_duration", 5)
        notif.start_auto_close(duration_s * 1000)
        return notif

    def _on_closed(self, notif):
        if notif in self.active_notifications:
            self.active_notifications.remove(notif)
        self._active_signatures.pop(getattr(notif, "signature", None), None)
        self.update_positions()
        self._drain()

    def _drain(self):
        """Affiche les notifications en file tant qu'il reste de la place."""
        while self.pending and len(self.active_notifications) < self.max_visible:
            spec = self.pending.popleft()
            if spec[3] in self._active_signatures:
                continue  # devenue redondante entre-temps
            self._create_and_show(spec)

    def _target_screen(self):
        """Écran où se trouve la fenêtre principale (windowHandle().screen() puis
        screenAt du centre en repli), et non plus systématiquement l'écran
        principal."""
        win = self.main_window
        handle = win.windowHandle() if win is not None else None
        if handle is not None and handle.screen() is not None:
            return handle.screen()
        if win is not None:
            screen = QGuiApplication.screenAt(win.frameGeometry().center())
            if screen is not None:
                return screen
        return QGuiApplication.primaryScreen()


class CustomNotification(QDialog):

    closed = Signal()

    def __init__(self, data, font_size=None, parent=None, internal=False, manager=None):
        # WindowDoesNotAcceptFocus + WA_ShowWithoutActivating : la notification ne
        # vole jamais le focus au progiciel de l'utilisateur.
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)

        self.internal = internal
        self.manager = manager
        self.audio_player = self.parent().audio_player

        # Timer d'auto-fermeture réutilisable (permet de prolonger l'affichage
        # quand une notification identique est réémise).
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self.close)

        self.format_data(data)

        font_size = font_size or self.parent().notification_font_size

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
        close_button.setFocusPolicy(Qt.NoFocus)
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

        # Taille par défaut ; la position définitive est fixée par le manager
        # (sur le bon écran, dans le coin configuré).
        self.resize(NOTIFICATION_WIDTH, 120)

    def format_data(self, notification_data):
        if not self.internal:
            try:
                notification_data = json.loads(notification_data)
            except json.JSONDecodeError:
                logger.error("Données de notification illisibles (JSON invalide)")

        self.origin = notification_data["origin"]
        logger.debug("Notification affichée (origin=%s)", self.origin)
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

    def show_without_activating(self):
        """Affiche la notification sans lui donner le focus (le progiciel de
        l'utilisateur garde le focus) et joue le son associé."""
        super().show()
        if self.audio_player:
            self.audio_player.play_sound(self.sound)

    def start_auto_close(self, milliseconds):
        self._close_timer.start(max(0, int(milliseconds)))

    def restart_auto_close(self):
        """Relance le compte à rebours d'auto-fermeture (notification ravivée)."""
        if self._close_timer.interval() > 0:
            self._close_timer.start(self._close_timer.interval())

    def show(self):
        """Compatibilité : afficher sans voler le focus. Le cycle de vie normal
        (dédup, file, positionnement) passe par NotificationManager.notify()."""
        self.show_without_activating()

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
