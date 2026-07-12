import logging
from PySide6.QtWidgets import QPushButton, QMainWindow, QMenu
from PySide6.QtCore import QTimer, Signal, QSize, Qt
from PySide6.QtGui import QIcon

from button_state import resolve_button_state

logger = logging.getLogger("appcomptoir.buttons")

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
        self.color_changed = False  # défini si on a changé la couleur
        self._user_enabled = True  # Nouvel attribut pour suivre l'état souhaité par l'utilisateur
        # Verrou explicite tenu par l'appelant tant qu'une requête réseau
        # déclenchée par ce bouton est en cours. Contrairement au debounce
        # ci-dessus (qui ne fait que patienter un temps fixe de 500ms), ce
        # verrou reste actif jusqu'à ce que set_busy(False) soit appelé, donc
        # jusqu'à la réponse effective du serveur même si elle met plus de
        # 500ms à arriver.
        self._busy = False
        self.original_style = self.styleSheet()

    def on_clicked(self):
        if not self.timer.isActive() and self._user_enabled and not self._busy:
            super().setEnabled(False)
            self.timer.start(self.debounce_time)

    def on_debounce_timeout(self):
        if self._user_enabled and not self._busy:
            super().setEnabled(True)
            self.clicked_with_debounce.emit()

    def set_busy(self, busy):
        """ Verrouille (ou déverrouille) le bouton pour la durée d'une requête
        réseau en cours, indépendamment du debounce à durée fixe. """
        self._busy = busy
        if busy:
            self.timer.stop()
            super().setEnabled(False)
        elif self._user_enabled:
            super().setEnabled(True)

    def setEnabled(self, enabled):
        self._user_enabled = enabled
        if not enabled:
            self.timer.stop()  # Arrêter le timer si le bouton est désactivé
        if not self._busy:
            super().setEnabled(enabled)

    def isEnabled(self):
        return self._user_enabled
        # Définir la politique de taille
        #self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)
        #self.setMinimumSize(30, 30)
        #self.setMaximumSize(30, 30)  # Taille minimale en pixels (ajustez selon vos besoins)

    def setRed(self):
        self.color_changed = True
        """ Change temporairement la couleur du bouton en rouge """
        self.setStyleSheet("background-color: red; color: white;")

    def resetColor(self):
        """ Réinitialise la couleur du bouton à son style d'origine """
        if self.color_changed:
            self.setStyleSheet(self.original_style)
            self.color_changed = False


class PatientButton(DebounceButton):
    def __init__(self, text, patient_data, parent=None):
        super().__init__(text)
        self.patient_data = patient_data
        self.parent = parent
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    def show_context_menu(self, position):
        menu = QMenu()

        # Action pour valider
        action_validate = menu.addAction("Marquer comme validé")
        action_validate.triggered.connect(lambda: self.parent.on_action_validate(self.patient_data['id']))

        # Action pour supprimer
        action_delete = menu.addAction("Supprimer")
        action_delete.triggered.connect(lambda: self.parent.on_action_delete(self.patient_data['id']))

        # Sous-menu pour assigner à un membre de l'équipe
        if hasattr(self.parent, 'activities_staff') and self.parent.activities_staff:
            assign_submenu = QMenu("Assigner à...", menu)
            
            for activity in self.parent.activities_staff:
                action = assign_submenu.addAction(activity['name'])
                action.triggered.connect(lambda checked, a=activity: self.parent.on_action_wait_for(a, self.patient_data['id']))
            
            menu.addMenu(assign_submenu)

        # Afficher le menu à la position du clic droit
        menu.exec_(self.mapToGlobal(position))


class IconeButton(DebounceButton):
    def __init__(self, icon_path, icon_inactive_path, flask_url, tooltip_text, tooltip_inactive_text, state, is_always_visible=True, parent=None):
        super().__init__(parent)

        self.icon_path = icon_path
        self.icon_inactive_path = icon_inactive_path
        self.flask_url = flask_url
        self.tooltip_text = tooltip_text
        self.tooltip_inactive_text = tooltip_inactive_text
        # On garde une référence à la MainWindow pour faire passer toutes les
        # requêtes par make_request_thread : le jeton courant est ajouté par la
        # session au moment de l'appel (plus de copie périmée de app_token) et le
        # renouvellement sur 401 (avec un seul rejeu) y est intégré.
        self.main_window = parent
        self.is_always_visible = is_always_visible
        self.setFixedSize(50, 50)
        self.setIcon(QIcon(self.icon_path))
        self.setIconSize(QSize(50, 50))
        self.setStyleSheet("border: none;")
        self.state = state  # inactive, active, waiting
        # État à restaurer si la requête échoue, pour ne jamais rester bloqué en
        # "waiting" (le bouton redevient utilisable).
        self._previous_state = state

        self.clicked.connect(self.toggle_state)
        self.update_button_icon()

    def toggle_state(self):
        logger.debug("toggle_state (état=%s)", self.state)
        if self.state == "inactive":
            self._previous_state = "inactive"
            self.state = "waiting"
            self.update_button_icon()
            self.send_request("activate")
        elif self.state == "active":
            self._previous_state = "active"
            self.state = "waiting"
            self.update_button_icon()
            self.send_request("deactivate")

    def change_state(self, state):
        self.state = state
        self.update_button_icon()

    def handle_response(self, result):
        logger.debug("handle_response (statut=%s)", result.status)
        # resolve_button_state garantit que le bouton quitte "waiting" quel que
        # soit le résultat (succès, corps inattendu, ou erreur).
        self.state = resolve_button_state(result.status, result.data, self._previous_state)
        if not result.success:
            logger.warning("Réponse en erreur du bouton (statut=%s) : %s",
                           result.status, result.detail)
        logger.debug("État mis à jour : %s", self.state)
        self.update_button_icon()

        if "paper" in self.flask_url and isinstance(self.main_window, QMainWindow):
            self.main_window.update_paper_action_text(self.state)

    def send_request(self, action):
        logger.debug("Envoi de la requête bouton (action=%s)", action)
        url = f"{self.flask_url}"
        data = {'action': action,
                'counter_id': self.main_window.counter_id}

        # make_request_thread : session partagée (jeton courant ajouté au moment
        # de l'appel) + renouvellement automatique du jeton sur 401 avec un seul
        # rejeu de la requête.
        self.request_thread = self.main_window.make_request_thread(url, method='POST', data=data)
        self.request_thread.result.connect(self.handle_response)
        self.request_thread.start()

    def update_button_icon(self, state=None):
        if state:
            self.state = state
        logger.debug("update_button_icon (état=%s)", self.state)

        if self.state == "inactive":
            if self.is_always_visible:
                self.setIcon(QIcon(self.icon_inactive_path))
                self.setIconSize(QSize(50, 50))
                self.setEnabled(True)
                self.setToolTip(self.tooltip_inactive_text)
                self.show()  
            else:
                self.hide()
        elif self.state == "active":
            self.show() 
            self.setIcon(QIcon(self.icon_path))
            self.setIconSize(QSize(50, 50))
            self.setEnabled(True)
            self.setToolTip(self.tooltip_text)
        elif self.state == "waiting":
            if self.is_always_visible:
                self.show()
            self.setIcon(QIcon(self.icon_path))
            self.setIconSize(QSize(50, 50))
            self.setEnabled(False)
            self.setToolTip("En attente d'une connexion")

