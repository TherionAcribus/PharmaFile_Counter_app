import sys
import os
import json
import uuid
import requests
import threading
from requests.exceptions import RequestException
import keyboard
from PySide6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon, QMenu, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox, QWidget, QCheckBox, QSizePolicy, QPlainTextEdit, QScrollArea, QDockWidget, QBoxLayout, QFrame
from PySide6.QtCore import QUrl, Signal, Slot, QSettings, QTimer, QThread, Qt, QMetaObject, QCoreApplication, QFile, QTextStream, QObject, QDateTime
from PySide6.QtGui import QIcon, QAction, QPainter
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtSvg import QSvgRenderer

from websocket_client import WebSocketClient
from preferences import PreferencesDialog
from buttons import DebounceButton, IconeButton, PatientButton
from notification import CustomNotification
from connections import RequestThread, DEFAULT_TIMEOUT
from my_logger import AppLogger, register_secret
from secret_store import load_secret

import logging
# Logger de module : propage vers les handlers configurés par AppLogger
# (fichier tournant + masquage + fenêtre UI). À utiliser dans les classes qui
# n'ont pas de self.logger (ex. AudioPlayer).
logger = logging.getLogger("appcomptoir.main")

# from line_profiler import profile
def profile(func):
    return func

class AudioPlayer(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.sounds = {}

        # Ajout des callbacks
        self.player.errorOccurred.connect(self.handle_error)

    def add_sound(self, name, file_path):
        self.sounds[name] = QUrl.fromLocalFile(file_path)
        logger.debug("Son ajouté : %s", name)

    def play_sound(self, name):
        if name in self.sounds:
            self.player.setSource(self.sounds[name])
            self.player.play()
        else:
            logger.warning("Son non trouvé : %s", name)

    def set_volume(self, volume):
        self.audio_output.setVolume(volume / 100.0)
        logger.debug("Volume réglé à : %s%%", volume)

    @Slot(QMediaPlayer.Error, str)
    def handle_error(self, error, error_string):
        logger.error("Erreur de lecture audio : %s - %s", error, error_string)


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

def load_stylesheet(filename):
    file = QFile(filename)
    if file.open(QFile.ReadOnly | QFile.Text):
        stream = QTextStream(file)
        return stream.readAll()
    return ""


class LoadingScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PharmaFile")
        self.setFixedSize(400, 200)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)

        # Configuration de l'interface utilisateur
        self._setup_ui()
        
        # Obtention de l'instance du logger et ajout du handler UI
        self.app_logger = AppLogger.get_instance()
        self.ui_handler = self.app_logger.add_ui_handler(self.update_progress)
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

class StartupWorker(QThread):
    """ Exécute en arrière-plan la séquence réseau de démarrage (token + état
    initial) pour ne pas geler le thread GUI pendant que le serveur répond. """
    finished_startup = Signal(bool, object)  # connected, state (dict ou None)

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

    def run(self):
        mw = self.main_window
        connected = False
        state = None

        try:
            mw.get_app_token()
            # si on a un token, on se considère comme connecté
            connected = True
        except Exception as e:
            logger.error("Erreur lors de l'obtention du token : %s", e)
            connected = False

        if connected:
            # Une seule snapshot atomique (patient en cours + liste + réglages +
            # révision) au lieu de deux requêtes séparées qui pouvaient se
            # chevaucher avant l'ouverture de Socket.IO (course de démarrage).
            state = mw.init_state()

        self.finished_startup.emit(connected, state)


class ResyncWorker(QThread):
    """ Récupère en arrière-plan l'état courant (patient en cours + liste des
    patients) après une reconnexion WebSocket.

    SocketIO ne rejoue pas les évènements manqués pendant une coupure : sans
    ça, un comptoir qui perd la connexion quelques secondes/minutes reste
    figé sur son dernier état connu jusqu'au prochain évènement poussé, qui
    peut ne jamais arriver si rien d'autre ne change côté serveur entretemps.
    """
    finished_resync = Signal(object)  # state (dict ou None)

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

    def run(self):
        mw = self.main_window
        # Même snapshot atomique qu'au démarrage : on récupère l'état autoritatif
        # complet (dont la révision) en une requête.
        state = mw.init_state()
        self.finished_resync.emit(state)


class MainWindow(QMainWindow):

    patient_data_received = Signal(object)
    patient_id = None
    staff_id = None
    activities_staff = None  # les activités "Staff" pour renvoyer un patient vers quelqu'un
    connected = False  # permet de savoir si on a réussi à se connecter
    add_paper = "waiting"
    autocalling = "waiting"
    list_patients = None  # liste des patient qui sera chargée au démarrage puis mise à jour via SocketIO
    my_patient =  None
    counter_name = None
    # Révision de l'état de la file connue localement. Toute diffusion Socket.IO
    # de la liste porte une révision croissante : on écarte les messages dont la
    # révision est <= à celle-ci (périmés/dupliqués) et on recharge l'état
    # autoritatif si on détecte un trou. -1 = aucun état chargé pour l'instant.
    queue_revision = -1

    def __init__(self):
        super().__init__()

        # pour gérer le délai avant d'indiquer une erreur de connexion
        self.disconnect_timer = QTimer(self)  # Timer créé dans le thread principal
        self.disconnect_timer.setSingleShot(True)
        self.disconnect_timer.timeout.connect(self._handle_disconnection_timeout)
        self.current_reconnection_attempts = 0
        self.disconnect_notification_shown = False
        # Distinct de disconnect_notification_shown (qui dépend du réglage
        # "notification_connection") : sert uniquement à savoir si on a
        # réellement perdu la connexion, pour déclencher un rattrapage d'état
        # à la reconnexion (SocketIO ne rejoue pas les évènements manqués).
        self.socket_was_disconnected = False

        self.loading_screen = LoadingScreen()
        self.loading_screen.show()

        self.app_logger = AppLogger.get_instance()
        self.logger = self.app_logger.get_logger()
        self.logger.info("Initialisation de la session...")
        self.session = requests.Session()  # Session HTTP persistante

        self.activities_staff = None  # pour être en global

        # LOAD PREFERENCES
        self.load_preferences()

        # on créé un timer qui permet d'alerter si le patient reste en Calling
        self.create_call_timer()

        # quand App se ferme, on ferme aussi le systray
        app = QApplication.instance()
        app.aboutToQuit.connect(self.cleanup_systray)

        self.logger.info("Test de la connexion...")
        self.app_token = None
        self.connected = False

        # La séquence réseau de démarrage (token + patient courant + liste des
        # patients) se fait en arrière-plan pour ne pas geler l'UI si le
        # serveur est lent/injoignable. La suite de l'initialisation continue
        # dans _on_startup_ready() une fois le résultat disponible.
        self.startup_worker = StartupWorker(self)
        self.startup_worker.finished_startup.connect(self._on_startup_ready)
        self.startup_worker.start()

    def _on_startup_ready(self, connected, state):
        """ Suite de l'initialisation une fois la séquence réseau de démarrage terminée """
        self.connected = connected

        if connected and state:
            self._apply_state(state)
        else:
            self.my_patient = None
            self.list_patients = []

        self.setup_ui()

        self.init_audio()

        self.setup_user()

        self.start_socket_io_client(self.web_url)

        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.always_on_top)
        self.show()

        self.alert_if_not_connected()

        if not self.debug_window:
            self.loading_screen.close()

    def load_preferences(self):
        self.logger.info("Initialisation des préférences...")
        
        settings = QSettings()
        self.web_url = settings.value("web_url", "https://gestionfile.onrender.com")
        # Le secret applicatif est lu depuis le magasin sécurisé (keyring /
        # Gestionnaire d'identifiants Windows), avec migration automatique de
        # l'ancienne valeur en clair éventuellement présente dans QSettings.
        self.app_secret = load_secret(settings)
        # Masquage du secret dans tous les logs (défense en profondeur).
        register_secret(self.app_secret)
        self.counter_id = settings.value("counter_id", "1")
        self.next_patient_shortcut = settings.value("next_patient_shortcut", "Alt+S")
        self.validate_patient_shortcut = settings.value("validate_patient_shortcut", "Alt+V")
        self.pause_shortcut = settings.value("pause_shortcut", "Altl+P")
        self.recall_shortcut = settings.value("recall_shortcut", "Alt+R")
        self.deconnect_shortcut = settings.value("deconnect_shortcut", "Alt+D")
        self.notification_current_patient = settings.value("notification_current_patient", True, type=bool)
        self.notification_autocalling_new_patient = settings.value("notification_autocalling_new_patient", True, type=bool)
        self.notification_specific_acts = settings.value("notification_specific_acts", True, type=bool)
        self.notification_add_paper = settings.value("notification_add_paper", True, type=bool)
        self.notification_connection = settings.value("notification_connection", True, type=bool)
        self.notification_after_deconnection = settings.value("notification_after_deconnection", 10, type=int)
        self.timer_after_calling = settings.value("notification_after_calling", 60, type=int)
        self.notification_duration = settings.value("notification_duration", 5, type=int)
        self.notification_font_size = settings.value("notification_font_size", 12, type=int)
        self.sound_volume = settings.value("notification_volume", 50, type=int)

        self.always_on_top = settings.value("always_on_top", False, type=bool)
        self.horizontal_mode = settings.value("vertical_mode", False, type=bool)
        self.display_patient_list = settings.value("display_patient_list", False, type=bool)
        self.patient_list_position_vertical = settings.value("patient_list_vertical_position", "bottom")
        self.patient_list_position_horizontal = settings.value("patient_list_horizontal_position", "right")
        self.debug_window = settings.value("debug_window", False, type=bool)
        # Journalisation détaillée (DEBUG) seulement si la fenêtre de log est
        # demandée ; sinon INFO (production). Les logs DEBUG ne sont donc pas
        # actifs en usage normal.
        if hasattr(self, "app_logger"):
            self.app_logger.enable_debug(self.debug_window)
        self.selected_skin = settings.value("selected_skin", "")

    def setup_ui(self):
        self.logger.info("Initialisation de l'interface...")

        icon_path = os.path.join(os.path.dirname(__file__), 'assets/images', 'next.ico')
        self.setWindowIcon(QIcon(icon_path))
        self.setWindowTitle("PharmaFile")

        self.setup_systray()

        # self.list_patients a déjà été renseigné par _on_startup_ready()
        # (récupéré en arrière-plan par StartupWorker) avant l'appel à setup_ui().
        self.logger.debug("Liste patients chargée (%s patients)",
                          len(self.list_patients) if self.list_patients else 0)

        self.create_interface()

        self.load_skin()

        self.setup_global_shortcut()
        

    def create_interface(self):
        # Supprime l'ancien widget central s'il existe (changement d'orientation)
        if self.centralWidget():
            self.centralWidget().deleteLater()
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self.main_layout = QHBoxLayout(self.central_widget) if self.horizontal_mode else QVBoxLayout(self.central_widget)

        # Créer un widget conteneur pour les éléments principaux
        self.main_elements_container = QWidget() 
        main_elements_layout = QHBoxLayout(self.main_elements_container) if self.horizontal_mode else QVBoxLayout(self.main_elements_container)
        main_elements_layout.setContentsMargins(0, 0, 0, 0)
        main_elements_layout.setSpacing(5)  # Ajustez l'espacement selon vos besoins

        self.central_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.main_elements_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._create_name()
        self._create_label_patient()
        self._create_main_button_container()
        self._create_option_button_container()
        self._create_icon_widget()
        self._create_patient_list_widget()

        # Ajouter les widgets au conteneur principal
        main_elements_layout.addWidget(self.label_staff)
        main_elements_layout.addWidget(self.label_patient)
        main_elements_layout.addWidget(self.main_button_container)
        main_elements_layout.addWidget(self.option_button_container)

        # Configurer la politique de taille du conteneur principal
        self.main_elements_container.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)

        # Ajouter le conteneur principal et les autres widgets au layout principal
        self.main_layout.addWidget(self.main_elements_container)
        self.main_layout.addWidget(self.icone_widget)

        self.update_patient_widget()
        self.update_patient_menu(self.list_patients)

        # Ajouter un stretch pour pousser les widgets vers le haut/gauche
        if self.horizontal_mode:
            self.main_layout.addStretch(1)
        else:
            self.main_layout.addStretch(1)

    def _create_name(self):
        self.label_staff = QLabel("")
        self.label_staff.setAlignment(Qt.AlignCenter)

    def _create_label_patient(self):
        # Remplacer QLabel par QPushButton
        self.label_patient = QPushButton("Pas de connexion !")
        self.label_patient.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        self.label_patient.setMinimumWidth(0)
        self.label_patient.setStyleSheet("text-align: left;")
        self.label_patient.setCheckable(False)  # Le bouton n'est pas "toggle"
        self.label_patient.setFlat(True)  # Le bouton ressemble davantage à un label

        # Créer un menu d'actions
        self.patient_menu = QMenu(self.label_patient)  # Stocké comme attribut de classe
        self.action_wait = self.patient_menu.addAction("Remettre en attente")
        
        # on ne crée le sous-menu que si on a défini des "activités Staff"
        if hasattr(self, 'activities_staff') and self.activities_staff:
            # Créer un sous-menu pour "Remettre en attente pour..."
            self.wait_for_submenu = QMenu("Remettre en attente pour...", self.patient_menu)
            
            # Ajouter chaque activité staff comme une action dans le sous-menu
            for activity in self.activities_staff:
                action = self.wait_for_submenu.addAction(activity['name'])
                action.triggered.connect(lambda checked, a=activity: self.on_action_wait_for(a))
            
            # Ajouter le sous-menu au menu principal
            self.patient_menu.addMenu(self.wait_for_submenu)

        self.action_delete = self.patient_menu.addAction("Supprimer")

        # Connecter les actions à des méthodes
        self.action_wait.triggered.connect(self.on_action_wait)
        self.action_delete.triggered.connect(self.on_action_delete)

        # Associer le menu au bouton
        self.label_patient.setMenu(self.patient_menu)

        # Désactiver les actions par défaut
        self._update_menu_actions(False)

    def _update_menu_actions(self, enable):
        """Active ou désactive les actions du menu"""
        self.action_wait.setEnabled(enable)
        if hasattr(self, 'wait_for_submenu'):
            self.wait_for_submenu.setEnabled(enable)
        self.action_delete.setEnabled(enable)

    def on_action_wait(self):
        # Logique pour remettre le patient en attente
        self.logger.debug("Patient remis en attente")
        url = f'{self.web_url}/api/counter/put_standing_list/{self.patient_id}'
        self.thread = self.make_request_thread(url)
        self.thread.result.connect(self.handle_result)
        self.thread.start()

    def on_action_wait_for(self, activity, patient_id=None):
        """
        patient_id: si non fourni, utilise self.patient_id (patient en cours)
        """
        target_id = patient_id if patient_id is not None else self.patient_id
        self.logger.debug("Patient remis en attente pour l'activité id=%s", activity['id'])
        url = f'{self.web_url}/api/counter/put_standing_list/{target_id}/{activity["id"]}'
        self.thread = self.make_request_thread(url)
        self.thread.result.connect(self.handle_result)
        self.thread.start()

    def on_action_validate(self, patient_id):
        url = f'{self.web_url}/api/counter/validate_patient/{patient_id}'
        self.validate_my_patient(url)

    def on_action_delete(self, patient_id=None):
        """
        patient_id: si non fourni, utilise self.patient_id (patient en cours)
        """
        target_id = patient_id if patient_id is not None else self.patient_id
        
        msg_box = QMessageBox()
        msg_box.setWindowFlags(msg_box.windowFlags() | Qt.WindowStaysOnTopHint)
        msg_box.setIcon(QMessageBox.Warning)
        msg_box.setWindowTitle("Confirmation de suppression")
        msg_box.setText("Êtes-vous sûr de vouloir supprimer ce patient ?")
        
        # Création des boutons personnalisés
        bouton_oui = msg_box.addButton("Oui", QMessageBox.YesRole)
        bouton_non = msg_box.addButton("Non", QMessageBox.NoRole)
        msg_box.setDefaultButton(bouton_non)
        
        msg_box.exec()
        
        # Si l'utilisateur clique sur "Oui"
        if msg_box.clickedButton() == bouton_oui:
            self.logger.debug("Suppression du patient demandée")
            url = f'{self.web_url}/api/counter/delete_patient/{target_id}'
            self.thread = self.make_request_thread(url)
            self.thread.result.connect(self.handle_result)
            self.thread.start()

    def _create_main_button_container(self):
        self.main_button_container = QWidget()
        self.main_button_layout = QHBoxLayout() if self.horizontal_mode else QVBoxLayout()

        buttons_config = [
            ("btn_next", "Suivant", self.next_patient_shortcut, self.call_web_function_validate_and_call_next),
            ("btn_validate", "Valider", self.validate_patient_shortcut, self.call_web_function_validate),
            ("btn_pause", "Pause", self.pause_shortcut, self.call_web_function_pause)
        ]

        for attr_name, text, shortcut, callback in buttons_config:
            button = DebounceButton(f"{text}\n{shortcut}")
            button.clicked.connect(callback)
            setattr(self, attr_name, button)  # Stocke le bouton comme attribut de la classe
            self.main_button_layout.addWidget(button)

        self.main_button_container.setLayout(self.main_button_layout)


    def _create_option_button_container(self):
        
        self.option_button_container = QWidget()
        self.option_button_layout = QHBoxLayout() if self.horizontal_mode else QVBoxLayout()

        self._create_choose_patient_button()
        self._create_more_button()

        self.option_button_layout.addWidget(self.btn_choose_patient)
        self.option_button_layout.addWidget(self.btn_more)

        self.option_button_container.setLayout(self.option_button_layout)

    def _create_icon_widget(self):
        self.icone_widget = QWidget()
        self.icone_layout = QHBoxLayout()

        self.connection_indicator = ConnectionStatusIndicator()
        self.icone_layout.addWidget(self.connection_indicator)
        
        self._create_auto_calling_button()
        self._create_paper_button()

        self.icone_layout.addWidget(self.btn_auto_calling)
        self.icone_layout.addWidget(self.btn_paper)

        self.icone_widget.setLayout(self.icone_layout)       


    def _create_icon_button(self, icon_path, icon_inactive_path, flask_url, tooltip_text, tooltip_inactive_text, state, is_always_visible=True):
        return IconeButton(
            icon_path=resource_path(icon_path),
            icon_inactive_path=resource_path(icon_inactive_path),
            flask_url=flask_url,
            tooltip_text=tooltip_text,
            tooltip_inactive_text=tooltip_inactive_text,
            state=state,
            parent=self,
            is_always_visible=is_always_visible
        )

    def _create_auto_calling_button(self):
        self.logger.info("Connexion pour charger le bouton d'appel automatique...")
        self.btn_auto_calling = self._create_icon_button(
            "assets/images/loop_yes.ico",
            "assets/images/loop_no.ico",
            f'{self.web_url}/app/counter/auto_calling',
            "Desactiver l'appel automatique",
            "Activer l'appel automatique",
            self.autocalling
        )

    def _create_paper_button(self):
        self.logger.info("Connexion pour charger l'icone de changement de papier...")
        self.btn_paper = self._create_icon_button(
            "assets/images/paper_add.ico",
            "assets/images/paper.ico",
            f'{self.web_url}/app/counter/paper_add',
            "Indiquer que vous avez changé le papier",
            "Indiquer qu'il faut changer le papier",
            self.add_paper,
            is_always_visible=False)
        
    def trigger_paper_button(self):
        if hasattr(self, 'btn_paper'):
            self.logger.debug("trigger_paper_button (état=%s)", self.btn_paper.state)
            self.btn_paper.toggle_state()

    def update_paper_action_text(self, state):
        if hasattr(self, 'btn_paper'):
            self.logger.debug("Mise à jour texte action papier (état=%s)", state)
            if state == "active":
                self.paper_action.setText("J'ai changé le papier")
            else:
                self.paper_action.setText("Changement papier nécessaire")

    def call_web_function_validate_and_call_next(self):
        url = f'{self.web_url}/validate_and_call_next/{self.counter_id}'
        # Clé d'idempotence : une nouvelle par action utilisateur. Si la requête
        # est renvoyée (relance réseau, ou relance automatique après un 401),
        # le serveur reconnaît la même clé et ne fait pas avancer la file deux
        # fois. La clé est portée par le RequestThread, donc la relance interne
        # après 401 réutilise bien la même valeur.
        headers = {'X-Idempotency-Key': str(uuid.uuid4())}
        self.btn_next.set_busy(True)
        self.thread = self.make_request_thread(url, headers=headers)
        self.thread.result.connect(self.handle_result)
        self.thread.finished.connect(lambda: self.btn_next.set_busy(False))
        self.thread.start()
        self.update_my_buttons(self.my_patient)
        self.close_please_validate_notification()


    def call_web_function_validate(self):
        self.logger.debug("Validation du patient (call_web_function_validate)")
        self.close_please_validate_notification()
        url = f'{self.web_url}/validate_patient/{self.counter_id}/{self.patient_id}'
        self.validate_my_patient(url)                    


    def validate_my_patient(self, url):
        self.logger.debug("Validation du patient en cours")
        self.close_please_validate_notification()
        if self.my_patient:
            self.btn_validate.set_busy(True)
            self.thread = self.make_request_thread(url)
            self.thread.result.connect(self.handle_result)
            self.thread.finished.connect(lambda: self.btn_validate.set_busy(False))
            self.thread.start()
        # permet de supprimer le Validate en rouge et l'alerte en si le bouton "Valider" est resté enclenché mais qu'il n'y a plus de patient
        else:
            self.update_my_buttons(self.my_patient)

    def close_please_validate_notification(self):
        # Fermeture des notification qui appele à valider le patient si il y a en a ouverte et que l'on clique sur le bouton "Valider"
        if hasattr(self, 'notification_manager'):
            for notification in self.notification_manager.active_notifications[:]:  # Create a copy of the list to avoid modification during iteration
                if isinstance(notification, CustomNotification) and getattr(notification, 'origin', None) == "please_validate":
                    notification.close()

    def call_web_function_pause(self):
        self.logger.debug("Mise en pause du patient")
        url = f'{self.web_url}/pause_patient/{self.counter_id}/{self.patient_id}'
        self.btn_pause.set_busy(True)
        self.thread = self.make_request_thread(url)
        self.thread.result.connect(self.handle_result)
        self.thread.finished.connect(lambda: self.btn_pause.set_busy(False))
        self.thread.start()

    @profile
    def _create_choose_patient_button(self):
        self.btn_choose_patient = DebounceButton("Patients")
        self.choose_patient_menu = QMenu()
        self.btn_choose_patient.setMenu(self.choose_patient_menu)

        # self.my_patient/self.list_patients sont normalement déjà remplis par
        # _on_startup_ready() (StartupWorker) avant le premier appel à cette
        # méthode. Ce qui suit est un filet de sécurité (ex: reconstruction de
        # l'interface après un changement d'orientation) au cas où ils seraient
        # encore vides, pas le chemin normal de démarrage.
        if not self.my_patient:
            self.logger.info("__ Connexion pour charger le patient en cours...")
            self.my_patient = self.init_patient()
        # uniquement si chargement des patients réussi (pas de connexion)
        if self.my_patient:
            self.update_my_patient(self.my_patient)
            self.update_my_buttons(self.my_patient)

        if not self.list_patients:
            self.logger.info("__ Connexion pour charger la liste des patients...")
            self.list_patients = self.init_list_patients()
        if self.list_patients:
            self.update_list_patient(self.list_patients)

    def _create_more_button(self):
        self.btn_more = DebounceButton("Menu")
        self.more_menu = QMenu()

        # Créer l'action pour le papier séparément pour pouvoir la mettre à jour
        self.paper_action = QAction("Changement papier nécessaire", self)
        self.paper_action.triggered.connect(self.trigger_paper_button)
        self.update_paper_action_text(self.add_paper)  # Mettre à jour le texte initial

        actions = [
            ("Relancer l'appel ", self.recall_shortcut, self.recall),
            (None, None, self.paper_action), 
            ("Changer l'orientation", None, self.toggle_orientation),
            ("Deconnexion ", self.deconnect_shortcut, self.deconnection),
            ("Préférences", None, self.show_preferences_dialog),
            ("Afficher/Masquer Liste Patients", None, self.toggle_patient_list),
        ]

        for text, shortcut, callback in actions:
            if isinstance(callback, QAction):  # Si c'est déjà une action
                self.more_menu.addAction(callback)
            else:
                action = QAction(f"{text}{shortcut if shortcut else ''}", self)
                action.triggered.connect(callback)
                self.more_menu.addAction(action)

        self.btn_more.setMenu(self.more_menu)

    def _create_patient_list_widget(self):
        # Create the dock widget if it doesn't exist
        if not hasattr(self, 'patient_list_dock'):
            # Create the dock widget
            self.patient_list_dock = QDockWidget("Liste des patients", self)
            self.patient_list_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea | Qt.BottomDockWidgetArea)
            
            # Create main container widget
            container_widget = QWidget()
            container_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            
            # Utiliser un QVBoxLayout pour le conteneur principal
            container_layout = QVBoxLayout(container_widget)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(0)
            
            # Create and configure scroll area
            self.scroll_area = QScrollArea()
            self.scroll_area.setWidgetResizable(True)
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            
            # Create scroll content with a QWidget
            self.scroll_content = QWidget()
            self.scroll_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)  # Important: Maximum ici
            
            # Créer le layout pour le contenu
            self.scroll_layout = QVBoxLayout(self.scroll_content)
            self.scroll_layout.setContentsMargins(0, 0, 0, 0)
            self.scroll_layout.setSpacing(0)
            self.scroll_layout.setAlignment(Qt.AlignTop)
            
            # Configurer la hiérarchie
            self.scroll_area.setWidget(self.scroll_content)
            container_layout.addWidget(self.scroll_area, 1)  # Le 1 donne la priorité d'expansion
            
            # Set the container as the dock widget's content
            self.patient_list_dock.setWidget(container_widget)
            
            # Add dock widget to main window
            self.addDockWidget(Qt.RightDockWidgetArea, self.patient_list_dock)
            
            # Adjust minimum size
            self.patient_list_dock.setMinimumHeight(100)
            
            # Remove borders and make it look cleaner
            self.patient_list_dock.setStyleSheet("""
                QDockWidget {
                    border: none;
                    padding: 0;
                }
                QScrollArea {
                    border: none;
                }
            """)
        
        # Update visibility based on preferences
        self.patient_list_dock.setVisible(self.display_patient_list)
        
        # Adjust dock widget position based on preferences
        if (self.horizontal_mode and self.patient_list_position_horizontal == "bottom") or \
            (not self.horizontal_mode and self.patient_list_position_vertical == "bottom"):
            self.addDockWidget(Qt.BottomDockWidgetArea, self.patient_list_dock)
        elif (self.horizontal_mode and self.patient_list_position_horizontal == "right") or \
            (not self.horizontal_mode and self.patient_list_position_vertical == "right"):
            self.addDockWidget(Qt.RightDockWidgetArea, self.patient_list_dock)

    def toggle_patient_list(self):
        if self.patient_list_dock.isVisible():
            self.patient_list_dock.hide()
        else:
            self.patient_list_dock.show()
    
    def hide_patient_list(self):
        self.patient_list_dock.hide()

    def toggle_orientation(self):
        self.horizontal_mode = not self.horizontal_mode
        self.create_interface()

    def _update_layout(self):
        # Créer un nouveau layout avec la nouvelle orientation
        new_layout = QHBoxLayout() if self.horizontal_mode else QVBoxLayout()
        
        # Transférer tous les widgets de l'ancien layout vers le nouveau
        while self.main_layout.count():
            item = self.main_layout.takeAt(0)
            new_layout.addWidget(item.widget())

        # Remplacer l'ancien layout par le nouveau
        self.centralWidget().setLayout(new_layout)
        self.main_layout = new_layout

        # Mettre à jour la position du dock widget
        if self.horizontal_mode:
            self.addDockWidget(Qt.RightDockWidgetArea, self.patient_list_dock)
        else:
            self.addDockWidget(Qt.BottomDockWidgetArea, self.patient_list_dock)

        # Forcer le recalcul du layout
        self.centralWidget().updateGeometry()
        self.adjustSize()

    def init_list_patients(self):
        url = f'{self.web_url}/api/patients_list_for_pyside'
        try:
            response = self.session.get(url, timeout=DEFAULT_TIMEOUT)
            if response.status_code == 200:
                self.logger.debug("Liste des patients récupérée")
                return response.json()
            else:
                self.logger.warning("Échec de récupération de la liste (statut=%s)", response.status_code)
        except RequestException as e:
            self.logger.warning("Connexion perdue lors de la récupération de la liste : %s", e)
            return []

    def recall(self):
        url = f"{self.web_url}/app/counter/relaunch_patient_call/{self.counter_id}"
        self.request_thread = self.make_request_thread(url, method='POST')
        self.request_thread.start()

    def setup_user(self):
        """ Va chercher le staff sur le comptoir """
        self.logger.info("Paramétrage de l'utilisateur...")
        url = f'{self.web_url}/api/counter/is_staff_on_counter/{self.counter_id}'
        self.user_thread = self.make_request_thread(url, method='GET')
        self.user_thread.result.connect(self.handle_user_result)
        self.user_thread.start()

    @Slot(float, str, int)
    def handle_result(self, elapsed_time, response_text, status_code):
        self.logger.debug("Réponse action patient (statut=%s, %.3fs)", status_code, elapsed_time)
        if status_code == 200:
            try:
                response_data = json.loads(response_text)
                self.update_my_patient(response_data)
                self.update_my_buttons(response_data)
                if self.notification_current_patient:
                    message = f"Nouveau patient : {response_data['call_number']} pour '{response_data['activity']}'"
                    self.show_notification({"origin": "new_patient", "message": message}, internal=True)

            except json.JSONDecodeError as e:
                self.logger.warning("Réponse illisible (JSON invalide) : %s", e)
        # plus de patient. Attention 204 ne permet pas de passer une info car 204 =pas de données
        elif status_code == 204:
            self.update_my_patient(None)
        # utiliser pour supprimer ou remettre un patient en attente
        elif status_code == 201:
            self.update_my_patient(False)
            patient = {"counter_id": self.counter_id, "id": None}
            self.update_my_buttons(patient)
        # 423 = patient déjà pris par un autre comptoir
        elif status_code == 423:
            self.patient_already_taken()
        else:
            self.logger.warning("Échec de l'action patient (statut=%s)", status_code)

    @Slot(float, str, int)
    def handle_user_result(self, elapsed_time, response_text, status_code):
        # si staff au comptoir
        if status_code == 200:
            try:
                response_data = json.loads(response_text)
                self.staff_id = response_data["staff"]['id']
                staff_name = response_data["staff"]['name']
                # on modifie le titre
                self.update_window_title(staff_name)
                self.update_staff_label(staff_name)

            except json.JSONDecodeError as e:
                self.logger.warning("Réponse staff illisible (JSON invalide) : %s", e)
        # si personne au comptoir
        elif status_code == 204:
            self.logger.debug("Aucun staff sur le comptoir")
            # deconnexion
            self.disconnect_from_counter()
            self.staff_id = False
            # on modifie le titre
            self.update_window_title("Connectez-vous !")
            # on affiche l'interface de connexion
            self.deconnexion_interface()
        else:
            self.logger.warning("Échec de récupération du staff (statut=%s)", status_code)
        
        
    def update_window_title(self, staff_name):
        """ Met a jour le titre de la fenetre """
        self.setWindowTitle(f"PharmaFile - {self.counter_name} - {staff_name}")

    def update_staff_label(self, staff_name):
        """ Met à jour le nom de l'équipier """
        try:
            if not self.horizontal_mode:
                name = f'-= {staff_name} =-'
                self.label_staff.setText(name)
        except RuntimeError:
            pass

    def start_socket_io_client(self, url):
        self.logger.info("Création de la connexion Socket.IO...")
        self.socket_io_client = WebSocketClient(self, username=f"Counter {self.counter_id} App")
        self.socket_io_client.new_patient.connect(self.new_patient)
        self.socket_io_client.new_notification.connect(self.show_notification)
        self.socket_io_client.change_paper.connect(self.change_paper)
        self.socket_io_client.change_paper_button.connect(self.change_paper_button)
        self.socket_io_client.change_auto_calling.connect(self.change_auto_calling)
        self.socket_io_client.update_auto_calling.connect(self.update_auto_calling)
        self.socket_io_client.disconnect_user.connect(self.disconnect_user)
        self.socket_io_client.ws_connection_status.connect(self.handle_socket_connection)
        self.socket_io_client.connection_lost.connect(self._handle_connection_lost)
        self.socket_io_client.refresh_after_clear_patient_list.connect(self.refresh_after_clear_patient_list)
        self.socket_io_client.start()

    def init_state(self):
        """ Récupère l'état autoritatif complet du comptoir en une seule requête
        (patient en cours + liste + réglages + révision). Utilisé au démarrage et
        à chaque resynchronisation pour garantir un état cohérent, plutôt que
        d'agréger plusieurs snapshots susceptibles de se contredire. """
        url = f'{self.web_url}/api/counter/{self.counter_id}/state'
        try:
            response = self.session.get(url, timeout=DEFAULT_TIMEOUT)
            if response.status_code == 200:
                return response.json()
            self.logger.warning("Échec de récupération de l'état (statut=%s)", response.status_code)
        except RequestException as e:
            self.logger.warning("Connexion perdue lors de la récupération de l'état : %s", e)
        return None

    def _apply_state(self, state):
        """ Applique une snapshot d'état autoritative aux champs de données (sans
        toucher aux widgets : les appelants rafraîchissent l'UI selon le contexte
        démarrage/resync). """
        self.queue_revision = state.get("revision", self.queue_revision)
        self.my_patient = state.get("current_patient")
        self.list_patients = state.get("standing_list") or []
        self.autocalling = "active" if state.get("autocalling") else "inactive"
        self.add_paper = "active" if state.get("add_paper") else "inactive"
        if state.get("counter_name"):
            self.counter_name = state.get("counter_name")
        if state.get("activities_staff"):
            self.activities_staff = state["activities_staff"]

    def _reload_state_async(self):
        """ Recharge l'état autoritatif en arrière-plan (déclenché sur détection
        d'un trou de révision). Réutilise ResyncWorker et son applicateur. """
        self.resync_worker = ResyncWorker(self)
        self.resync_worker.finished_resync.connect(self._on_resync_ready)
        self.resync_worker.start()

    def init_patient(self):
        url = f'{self.web_url}/api/counter/is_patient_on_counter/{self.counter_id}'
        try:
            response = self.session.get(url, timeout=DEFAULT_TIMEOUT)
            if response.status_code == 200:
                self.logger.debug("Patient courant récupéré")
                return response.json()
            else:
                self.logger.warning("Échec de récupération du patient (statut=%s)", response.status_code)
                return None
        except RequestException as e:
            self.logger.warning("Connexion perdue lors de la récupération du patient : %s", e)
            return None

    def patient_already_taken(self):
        self.logger.debug("Patient déjà attribué à un autre comptoir")
        self.label_patient.setText("Patient déjà attribué")
        self.audio_player.play_sound("patient_taken")


    def handle_socket_connection(self, status, reconnection_attempts=0, display_notification=True):
        if status is None:  # Connecting
            self.connection_indicator.set_status("connecting", reconnection_attempts)
        elif status:  # Connected
            should_notify = self.disconnect_notification_shown and display_notification and self.notification_connection
            if should_notify:
                self.show_notification({
                    "origin": "socket_connection_true",
                    "message": "La connexion temps réel est (r)établie !"
                }, internal=True)
            if self.socket_was_disconnected:
                # On a réellement perdu la connexion à un moment : rattrape
                # l'état courant au lieu de compter sur le prochain évènement
                # poussé par le serveur.
                self.socket_was_disconnected = False
                self.resync_worker = ResyncWorker(self)
                self.resync_worker.finished_resync.connect(self._on_resync_ready)
                self.resync_worker.start()
            self.connection_indicator.set_status("connected")
        else:  # Disconnected
            self.socket_was_disconnected = True
            if display_notification and self.notification_connection:
                self.disconnect_notification_shown = True
                self.show_notification({
                    "origin": "socket_connection_false",
                    "message": "La connexion temps réel a été perdue. Tentative de reconnexion... La liste des patients ne s'affichera plus en temps réél, mais les boutons fonctionnent toujours."
                }, internal=True)
            self.connection_indicator.set_status("disconnected", reconnection_attempts)

    def _on_resync_ready(self, state):
        """ Applique l'état autoritatif rattrapé (reconnexion ou trou de révision)
        et rafraîchit l'UI : patient courant, liste, papier, autocalling ET staff. """
        if not state:
            return
        self._apply_state(state)

        # Staff en premier : peut faire basculer entre l'écran de connexion et
        # l'interface principale (donc reconstruire les widgets patient).
        self._resync_staff(state.get("staff"))

        # Si plus personne au comptoir, on est sur l'écran de connexion : il n'y
        # a pas d'UI patient à rafraîchir.
        if not (isinstance(self.staff_id, int) and self.staff_id):
            return

        # patient en cours + boutons associés
        self.update_my_patient(self.my_patient)
        self.update_my_buttons(self.my_patient)

        # liste des patients (menus + widget)
        self.update_patient_menu(self.list_patients)
        self.update_list_patient(self.list_patients)
        self.update_patient_widget()

        # réglages (icônes autocalling / papier)
        if hasattr(self, 'btn_auto_calling'):
            self.btn_auto_calling.update_button_icon(self.autocalling)
        if hasattr(self, 'btn_paper'):
            self.btn_paper.update_button_icon(self.add_paper)

    def _resync_staff(self, staff):
        """ Réaligne l'affichage du staff sur l'état autoritatif (/state) lors
        d'une resynchronisation, en n'agissant qu'en cas de changement réel.

        Contrairement au flux de démarrage (handle_user_result), on ne fait ici
        AUCUN appel serveur : la resync ne fait que refléter l'état, elle ne le
        mutila pas. On évite aussi de reconstruire l'écran de connexion tant que
        rien ne change, car deconnexion_interface() remplace le widget central.

        staff : dict {id, name, ...} si quelqu'un est au comptoir, sinon None. """
        # staff_id vaut un int > 0 si connecté, False/None sinon. isinstance
        # + test de vérité écarte False/None/0 (bool est un int en Python).
        current_id = self.staff_id if (isinstance(self.staff_id, int) and self.staff_id) else None
        new_id = staff['id'] if staff else None

        if new_id == current_id:
            # Pas de changement de personne : on rafraîchit juste le libellé
            # (utile si le nom du comptoir a changé), sans reconstruire l'UI.
            if staff:
                self.update_window_title(staff['name'])
                self.update_staff_label(staff['name'])
            return

        if staff:
            # Un (autre) staff est désormais au comptoir : on repasse sur
            # l'interface principale si on était sur l'écran de connexion.
            self.staff_id = staff['id']
            self.update_window_title(staff['name'])
            self.recreate_main_interface()
            self.update_staff_label(staff['name'])
        else:
            # Le staff a été déconnecté à distance pendant la coupure : on
            # reflète la déconnexion côté UI, SANS refaire l'appel serveur
            # remove_staff (le serveur n'a déjà plus de staff).
            self.staff_id = False
            self.update_window_title("Connectez-vous !")
            self.deconnexion_interface()


    def update_my_patient(self, patient):
        try:
            self.logger.debug("Mise à jour du patient en cours")
            if patient is None:
                self.patient_id = None
                self.label_patient.setText("Plus de patient")
                self._update_menu_actions(False)
            elif patient is False:
                self.patient_id = None
                self.label_patient.setText("Pas de patient")
                self._update_menu_actions(False)
            else:
                if patient["counter_id"] == self.counter_id:
                    if patient["id"] is None:
                        self.patient_id = None
                        self.label_patient.setText("Pas de patient en cours")
                        self._update_menu_actions(False)
                    else:
                        self.patient_id = patient["id"]
                        status = patient["status"]
                        if status == "calling":
                            status_text = "En appel"
                        elif status == "ongoing":
                            status_text = "Au comptoir"
                        else:
                            status_text = "????"
                        language = f" ({patient['language_code']}) ".upper() if patient["language_code"] != "fr" else ""
                        self.label_patient.setText(f"{patient['call_number']}{language} {status_text} ({patient['activity']})")
                        self._update_menu_actions(True)  # Active les actions car il y a un patient
        except:
            self._update_

    def update_my_buttons(self, patient):
        #TEMPORAIRE
        try:
            # cas de la suppression quotidienne de la liste des patients
            if not patient:
                    self.btn_pause.setEnabled(False)
                    self.btn_validate.setEnabled(False)
                    self.btn_validate.resetColor()
                    self.call_timer.stop()  # bloque le timer "calling" si plus personne
            else:
                if patient["counter_id"] == self.counter_id:
                    if patient["id"] is None:
                        self.btn_pause.setEnabled(False)
                        self.btn_validate.setEnabled(False)
                        self.btn_validate.resetColor()
                        self.call_timer.stop()  # bloque le timer "calling" si plus personne
                    else:
                        if patient["status"] == "calling":
                            self.btn_pause.setEnabled(False)
                            self.btn_validate.setEnabled(True)
                            self.call_timer.start()  # démarre le timer "calling" si le patient en appel
                        elif patient["status"] == "ongoing":
                            self.btn_pause.setEnabled(True)
                            self.btn_validate.setEnabled(False)
                            self.btn_validate.resetColor()
                            self.call_timer.stop()  # bloque le timer "calling" si patient pris en charge
        except:
            pass

    def create_login_widget(self):
        login_widget = QWidget()
        login_layout = QVBoxLayout()

        # Ajouter un label
        self.label_connexion = QLabel("Connectez-vous")
        self.label_connexion.setAlignment(Qt.AlignCenter)  # Centre le texte
        font = self.label_connexion.font()
        font.setPointSize(16)  # Augmente la taille de la police (ajustez selon vos besoins)
        font.setBold(True)  # Met le texte en gras
        self.label_connexion.setFont(font)
        login_layout.addWidget(self.label_connexion)

        # Ajouter un champ pour les initiales
        self.initials_input = QLineEdit()
        self.initials_input.setPlaceholderText("Entrez vos initiales")
        login_layout.addWidget(self.initials_input)

        # Checkbox pour la deconnexion sur tous les autres postes
        self.checkbox_on_all = QCheckBox("Déconnexion sur tous les autres postes")
        self.checkbox_on_all.setChecked(True)
        login_layout.addWidget(self.checkbox_on_all)

        # Ajouter un bouton de validation
        validate_button = DebounceButton("Valider")
        validate_button.clicked.connect(self.validate_login)
        login_layout.addWidget(validate_button)

        # Ajouter un bouton de préférences
        preferences_button = QPushButton("Préférences")
        preferences_button.clicked.connect(self.show_preferences_dialog)
        login_layout.addWidget(preferences_button)

        login_widget.setLayout(login_layout)

        # Connecter la touche Enter à la fonction de validation
        self.initials_input.returnPressed.connect(self.validate_login)

        return login_widget
    
    def deconnection(self):
        self.disconnect_from_counter()
        self.deconnexion_interface()

    def deconnexion_interface(self):
        self.logger.debug("Affichage de l'interface de connexion")
        # Créer et définir le widget de connexion
        login_widget = self.create_login_widget()
        self.setCentralWidget(login_widget)

        self.hide_patient_list()
        
        # désactivation du champ à l'initialisation sinon le raccourci clavier est entré dans le champ
        self.initials_input.setDisabled(True)
        # réactivation après 100ms
        QTimer.singleShot(100, self.enable_initials_input)

    def disconnect_from_counter(self):
        # Deconnexion sur le serveur
        url = f'{self.web_url}/app/counter/remove_staff'
        data = {'counter_id': self.counter_id}
        self.disconnect_thread = self.make_request_thread(url, method='POST', data=data)
        self.disconnect_thread.result.connect(self.handle_disconnect_result)
        self.disconnect_thread.start()

    def enable_initials_input(self):
        """ Permet d'activer le champ des initiales lors de l'initialisation + focus
        Obligé de le désactiver pour éviter entrée du raccourci clavier dans le champ """
        self.initials_input.setDisabled(False)
        # Donner le focus au champ des initiales
        self.initials_input.setFocus()

    @Slot(float, str, int)
    def handle_disconnect_result(self, elapsed_time, response_text, status_code):
        self.logger.debug("Réponse déconnexion (statut=%s)", status_code)
        if status_code == 200:
            # Remise à jour de la barre de titre
            self.update_window_title("Déconnecté")
            # Mise à jour de l'id staff
            self.staff_id = None
        else:
            # Afficher un message d'erreur
            QMessageBox.warning(self, "Erreur de connexion", "Impossible de se connecter. Veuillez réessayer.")

    def validate_login(self):
        if not self.app_token:
            self.logger.warning("Connexion impossible : pas de token valide")
            return
        
        initials = self.initials_input.text()
        cb_deconnexion_on_all = self.checkbox_on_all.isChecked()

        if initials:
            url = f'{self.web_url}/app/counter/update_staff'
            data = {'initials': initials, 'counter_id': self.counter_id, "deconnect": cb_deconnexion_on_all, "app": True}

            self.login_thread = self.make_request_thread(url, method='POST', data=data)
            self.login_thread.result.connect(self.handle_login_result)
            self.login_thread.start()

    @Slot(float, str, int)
    def handle_login_result(self, elapsed_time, response_text, status_code):
        self.logger.debug("Réponse connexion staff (statut=%s)", status_code)
        if status_code == 200:
            response_data = json.loads(response_text)
            staff_name = response_data["staff"]["name"]
            # Mise à jour de la barre de titre
            self.update_window_title(staff_name)
            # Mise à jour de l'id staff
            self.staff_id = response_data["staff"]["id"]
            # Recréer l'interface principale1
            self.recreate_main_interface()
            self.update_staff_label(staff_name)
            # Mettre à jour l'interface si nécessaire
            self.init_patient()
        elif status_code == 204:
            self.logger.debug("Initiales inconnues")
            self.staff_id = False
            # Mettre à jour le label de connexion
            if hasattr(self, 'label_connexion'):
                self.label_connexion.setText("Initiales incorrectes ! ")            
        else:
            # Afficher un message d'erreur
            QMessageBox.warning(self, "Erreur de connexion", "Impossible de se connecter. Veuillez réessayer.")
    
    def recreate_main_interface(self):
        # Supprime l'ancien widget central (widget de login)
        if self.centralWidget():
            self.centralWidget().deleteLater()
        
        # Recrée l'interface principale
        self.create_interface()
    
    def show_preferences_dialog(self):
        dialog = PreferencesDialog(self)
        dialog.preferences_updated.connect(self.apply_preferences)
        if dialog.exec():
            # a la fermeture on recharge les preferences
            self.load_preferences()
            # on ajuste le volume
            self.audio_player.set_volume(self.sound_volume)
            # on recharge les raccourcis
            self.setup_global_shortcut()

    def setup_global_shortcut(self):
        self.shortcut_thread = threading.Thread(target=self.setup_shortcuts, daemon=True)
        self.shortcut_thread.start()

    def setup_shortcuts(self):
        # Retire les raccourcis précédemment enregistrés avant d'en ajouter de
        # nouveaux : sans ça, chaque changement de préférences empilait un
        # nouveau hook sur les anciens et une pression déclenchait l'action
        # autant de fois que de hooks accumulés.
        keyboard.unhook_all_hotkeys()
        keyboard.add_hotkey(self.next_patient_shortcut, self.handle_next_patient_shortcut)
        keyboard.add_hotkey(self.validate_patient_shortcut, self.handle_validate_shortcut)
        keyboard.add_hotkey(self.pause_shortcut, self.handle_pause_shortcut)
        keyboard.add_hotkey(self.recall_shortcut, self.recall)
        keyboard.add_hotkey(self.deconnect_shortcut, self.handle_deconnect_shortcut)

    def handle_next_patient_shortcut(self):
        # Ne fait que simuler le clic : le bouton est déjà connecté à
        # call_web_function_validate_and_call_next() (cf. _create_main_button_container).
        # Appeler la fonction ici en plus déclenchait l'action deux fois par
        # pression, ce qui pouvait faire avancer la file de deux patients.
        self.btn_next.animateClick()

    def handle_validate_shortcut(self):
        self.btn_validate.animateClick()

    def handle_pause_shortcut(self):
        self.btn_pause.animateClick()
        
    def handle_deconnect_shortcut(self):
        self.logger.debug("Raccourci de déconnexion déclenché")
        QMetaObject.invokeMethod(self, 'deconnection', Qt.QueuedConnection)
        
    def call_web_function_validate_and_call_specifique(self, patient_select_id):
            url = f'{self.web_url}/call_specific_patient/{self.counter_id}/{patient_select_id}'
            self.thread = self.make_request_thread(url)
            self.thread.result.connect(self.handle_result)
            self.thread.start()


    def get_app_token(self):
        """ Récupère un token applicatif et l'installe sur la session HTTP
        partagée, pour que toutes les requêtes (GET et POST) l'envoient
        automatiquement. Lève une exception si l'authentification échoue,
        pour que l'appelant (démarrage, renouvellement) le sache clairement
        plutôt que de continuer comme si de rien n'était. """
        url = f'{self.web_url}/api/get_app_token'
        data = {'app_secret': self.app_secret}
        response = self.session.post(url, data=data, timeout=DEFAULT_TIMEOUT)
        if response.status_code == 200:
            self.app_token = response.json()['token']
            self.session.headers['X-App-Token'] = self.app_token
            # Le jeton n'est JAMAIS journalisé. On l'enregistre auprès du filtre
            # de masquage pour qu'il soit caviardé s'il apparaissait par mégarde
            # dans un log (défense en profondeur).
            register_secret(self.app_token)
            self.logger.debug("Token applicatif obtenu et installé")
        else:
            self.app_token = None
            self.session.headers.pop('X-App-Token', None)
            raise RuntimeError(f"Échec de l'obtention du token (statut {response.status_code})")

    def try_refresh_app_token(self):
        """ Variante de get_app_token() qui ne lève pas d'exception : à utiliser
        comme reauth_callback par RequestThread quand une requête reçoit un 401. """
        try:
            self.get_app_token()
            return True
        except Exception as e:
            self.logger.warning("Échec du renouvellement du token : %s", e)
            return False

    def make_request_thread(self, url, method='GET', data=None, headers=None):
        """ Fabrique de RequestThread partagée : garantit que le token
        applicatif (porté par self.session) est renouvelé automatiquement
        si le serveur répond 401. """
        return RequestThread(url, self.session, method=method, data=data, headers=headers,
                              reauth_callback=self.try_refresh_app_token)

    def apply_preferences(self):
        self.load_preferences()
        self.setup_global_shortcut()    

    def init_audio(self):
        self.audio_player = AudioPlayer(self)
        sound_path = resource_path("assets/sounds/already_taken.mp3")
        self.audio_player.add_sound("patient_taken", sound_path)
        sound_path = resource_path("assets/sounds/ding.mp3")
        self.audio_player.add_sound("ding", sound_path)
        sound_path = resource_path("assets/sounds/please_validate.mp3")
        self.audio_player.add_sound("please_validate", sound_path)
        self.audio_player.set_volume(self.sound_volume) 

    def closeEvent(self, event):
        self.logger.info("Fermeture de l'App")

        # déconnection du comptoir
        self.logger.info("Déconnexion du comptoir suite à la fermeture de l'App")
        self.disconnect_from_counter()
        
        # Fermeture de la fenêtre secondaire quand la fenêtre principale est fermée
        if self.loading_screen:
            self.loading_screen.close()
        super().closeEvent(event)

    # Note : l'ancien couple connexion_for_app_init()/handle_init_app() (requête
    # /app/counter/init_app pour autocalling + papier + activités staff +
    # nom du comptoir) a été supprimé : ces informations sont désormais fournies
    # de façon atomique par /api/counter/<id>/state via _apply_state().

    def update_list_patient(self, patients):
        """ Mise à jour de la liste des patients pour le bouton 'Choix' """
        self.choose_patient_menu.clear()  # Clear the menu before updating
        try:
            for patient in patients:
                language = f" ({patient['language_code']}) ".upper() if patient["language_code"] != "fr" else ""
                action_select_patient = QAction(f"{patient['call_number']} {language}- {patient['activity']}", self)
                action_select_patient.triggered.connect(lambda checked, p=patient: self.select_patient(p['id']))
                self.choose_patient_menu.addAction(action_select_patient)
            self.btn_choose_patient.setMenu(self.choose_patient_menu)
        except TypeError:
            self.logger.warning("Liste de patients invalide (TypeError)")

    def new_patient(self, patient, revision=None):
        self.logger.debug("new_patient reçu (revision=%s, %s patients)",
                          revision, len(patient) if isinstance(patient, list) else "?")

        # Convergence via révision : Socket.IO est une notification, pas la
        # source de vérité. On compare la révision reçue à celle connue.
        if revision is not None:
            if self.queue_revision is not None and self.queue_revision >= 0:
                if revision <= self.queue_revision:
                    # Message périmé ou dupliqué (ex. réordonnancement réseau) :
                    # on a déjà un état au moins aussi récent, on l'ignore.
                    self.logger.debug("new_patient ignoré (rev %s <= %s)", revision, self.queue_revision)
                    return
                if revision > self.queue_revision + 1:
                    # Trou : au moins un évènement a été manqué. On ne fait pas
                    # confiance à ce seul message et on recharge l'état autoritatif.
                    self.logger.info("Trou de révision (%s -> %s), rechargement de l'état",
                                     self.queue_revision, revision)
                    self.queue_revision = revision
                    self._reload_state_async()
                    return
            # Établit (si pas encore de référence) ou avance la révision connue.
            self.queue_revision = revision

        # mise à jour de self.patient
        self.list_patients = patient
        self.update_patient_menu(patient)
        self.update_list_patient(patient)
        self.update_patient_widget()

    def update_patient_menu(self, patients):
        """ Mise a jour de la liste des patients le trayIcon """
        menu = QMenu()       

        # Mise à jour du bouton 'Choix' selon qu'il y ait ou non des patients
        label_text = f"Patient{'s' if len(patients) > 1 else ''} ({len(patients)})"
        self.btn_choose_patient.setText(label_text)

        # Ajout des patients dans le menu
        for patient in patients:
            action_text = f"{patient['call_number']} - {patient['activity']}"
            label = QLabel(action_text)
            if self.staff_id == patient["activity_is_staff"]:
                label.setStyleSheet("background-color: #f98517; color: #000000;")

            action = menu.addAction(action_text)
            action.triggered.connect(lambda checked, p=patient: self.select_patient(p['id']))
            
        self.trayIcon2.setContextMenu(menu)

    def update_patient_widget(self):
        # Clear existing buttons
        for i in reversed(range(self.scroll_layout.count())):
            widget = self.scroll_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

        # Add new buttons for each patient
        for patient in self.list_patients:
            button_text = patient['call_number']
            if patient['activity_is_staff']:
                button_text += f" -> {patient['activity']}"
            if patient["language_code"] != "fr":
                button_text += f" ({patient['language_code']})"
            button = PatientButton(button_text, patient, self)  # Utilisation d'une classe personnalisée
            
            font = button.font()
            font.setPointSize(8)
            button.setFont(font)

            if self.staff_id == patient["activity_is_staff"]:
                button.setStyleSheet("background-color: #f98517; color: #000000;")

            button.clicked.connect(lambda checked, id=patient["id"]: self.call_web_function_validate_and_call_specifique(id))
            self.scroll_layout.addWidget(button)

        # Add a spacer at the end
        self.scroll_layout.addStretch(1)

        # Force layout update
        self.scroll_content.updateGeometry()
        self.scroll_area.updateGeometry()

    def show_notification(self, data, internal=False):
        if self.notification_specific_acts:
            notification = CustomNotification(data=data, parent=self, internal=internal)
            notification.show()

    def _handle_connection_lost(self, reconnection_attempts):
        """Gère la perte de connexion"""
        self.current_reconnection_attempts = reconnection_attempts
        # Met à jour immédiatement l'indicateur visuel sans notification
        self.handle_socket_connection(False, reconnection_attempts, False)
        
        # Démarre le timer si pas déjà actif
        if not self.disconnect_timer.isActive() and not self.disconnect_notification_shown:
            self.disconnect_timer.start(self.notification_after_deconnection*1000)  # délai avant notification
    
    def _handle_disconnection_timeout(self):
        """Appelé après le délai de 5 secondes"""
        if not self.disconnect_notification_shown:
        # Affiche la notification de déconnexion
            self.disconnect_notification_shown = True
            self.handle_socket_connection(False, self.current_reconnection_attempts, True)

    def change_paper(self, data):
        self.add_paper = "active" if data["data"]["add_paper"] else "inactive"
        self.btn_paper.update_button_icon(self.add_paper)
        if self.notification_add_paper:
            message = "On est quasiment au bout du rouleau" if self.add_paper == "active" else "Une gentille personne a remis du papier"
            self.show_notification({"origin": "low_paper", "message": message}, internal=True)
        
    def change_paper_button(self, origin):
        """ Appelé lors d'une notification venant de l'imprimante via le serveur. Le but est de ne pas redéclencher une seconde notification """
        self.logger.debug("Mise à jour du bouton papier (origin=%s)", origin)
        add_paper = "active" if origin in ["low_paper", "no_paper"] else "inactive"
        self.btn_paper.update_button_icon(add_paper)

    def refresh_after_clear_patient_list(self):
        self.logger.debug("Rafraîchissement après purge de la liste des patients")
        self.update_my_patient(None)
        self.update_my_buttons(None)

    def change_auto_calling(self, data):
        self.autocalling = "active" if data["data"]["autocalling"] else "inactive"
        self.logger.debug("Auto-calling : %s", self.autocalling)
        self.btn_auto_calling.update_button_icon(self.autocalling)

    def update_auto_calling(self, data):
        """ Mise à jour de l'interface lors de l'autocalling (arrivé d'un patient)"""
        self.logger.debug("Mise à jour auto-calling (arrivée d'un patient)")
        patient = data["data"]["patient"]
        #patient["counter_id"] = self.counter_id
        self.update_my_patient(patient)
        self.update_my_buttons(patient)
        if self.notification_autocalling_new_patient:
            message = f"Appel automatique du patient {patient['call_number']} pour '{patient['activity']}'"
            self.show_notification({"origin": "autocalling", "message": message}, internal=True)

    def disconnect_user(self, data):
        self.logger.info("Déconnexion du comptoir demandée par un autre poste")
        message = f'Vous avez déconnecté par {data["data"]["staff"]}'
        self.show_notification({"origin": "disconnect_by_user", "message": message}, internal=True)
        self.deconnexion_interface()

    @Slot()
    def pyqt_call_preferences(self):
        self.show_preferences_dialog()

    def select_patient(self, patient_select_id):
        self.call_web_function_validate_and_call_specifique(patient_select_id)


    def on_tray_icon_validation_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.call_web_function_validate()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            pass


    def on_tray_icon_call_next_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.call_web_function_validate_and_call_next()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            pass
        
    def on_tray_icon_pause_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.call_web_function_pause()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            pass

    def load_skin(self):
        if self.selected_skin:
            qss_file = os.path.join("skins", f"{self.selected_skin}.qss")
            if os.path.exists(qss_file):
                with open(qss_file, "r") as f:
                    qss = f.read()
                    self.setStyleSheet(qss)
                    # Appliquer le style à toute l'application
                    QApplication.instance().setStyleSheet(qss)

    def setup_systray(self):
        """ Création du Systray"""        
        self.logger.info("Création du Systray...")
        icon_path = resource_path("assets/images/pause.ico")
        self.trayIcon1 = QSystemTrayIcon(QIcon(icon_path), self)
        self.trayIcon1.setToolTip("Pause")
        tray_menu1 = QMenu()
        open_action1 = tray_menu1.addAction("Open Main Window")
        open_action1.triggered.connect(self.call_web_function_pause)
        self.trayIcon1.setContextMenu(tray_menu1)
        self.trayIcon1.activated.connect(self.on_tray_icon_pause_activated)
        self.trayIcon1.setVisible(True)
        self.trayIcon1.show()


        icon_path = resource_path("assets/images/next_orange.ico")
        self.trayIcon2 = QSystemTrayIcon(QIcon(icon_path), self)
        self.trayIcon2.setToolTip("Prochain patient")
        tray_menu2 = QMenu()
        open_action2 = tray_menu2.addAction("Call Web Function")
        open_action2.triggered.connect(self.call_web_function_validate_and_call_next)
        self.trayIcon2.setContextMenu(tray_menu2)
        self.trayIcon2.activated.connect(self.on_tray_icon_call_next_activated)
        self.trayIcon2.setVisible(True)
        self.trayIcon2.show()


        icon_path = resource_path("assets/images/check.ico")
        self.trayIcon3 = QSystemTrayIcon(QIcon(icon_path), self)
        self.trayIcon3.setToolTip("Valider patient")
        tray_menu3 = QMenu()
        open_action3 = tray_menu3.addAction("Call Web Function")
        open_action3.triggered.connect(self.call_web_function_validate)
        self.trayIcon3.setContextMenu(tray_menu3)
        self.trayIcon3.activated.connect(self.on_tray_icon_validation_activated)
        self.trayIcon3.setVisible(True)
        self.trayIcon3.show()

    def cleanup_systray(self):
        # Supprime les icônes de la barre d'état système (fermeture de l'App)
        if hasattr(self, 'trayIcon1'):
            self.trayIcon1.setVisible(False)
            self.trayIcon1.deleteLater()
        if hasattr(self, 'trayIcon2'):
            self.trayIcon2.setVisible(False)
            self.trayIcon2.deleteLater()
        if hasattr(self, 'trayIcon3'):
            self.trayIcon3.setVisible(False)
            self.trayIcon3.deleteLater()

    def alert_if_not_connected(self):
        """ Affiche une alerte si le serveur n'est pas accessible"""
        if not self.connected:
            self.show_notification({"origin": "connection", "message": "Le serveur est inaccessible."}, internal=True)

    def call_timer_delay_expired(self):
        self.btn_validate.setRed()
        self.show_notification({"origin": "please_validate", "message": "Pensez à valider votre patient afin de vider l'écran d'affichage."}, internal=True)

    def create_call_timer(self):
        """ Permet de définir un timer qui envoye une alerte si le patient n'est pas validé """
        self.call_timer = QTimer(self)
        self.call_timer.setInterval(self.timer_after_calling * 1000)
        self.call_timer.timeout.connect(self.call_timer_delay_expired)


class ConnectionStatusIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self.status = "connected"
        self.last_connection_time = None
        self.reconnection_attempts = 0
        self.setMouseTracking(True)

        # Charger les SVG avec vos noms de fichiers
        self.renderers = {}
        status_files = {
            "connected": "connection_true.svg",
            "connecting": "connection_standing.svg",
            "disconnected": "connection_false.svg"
        }

        for status, filename in status_files.items():
            renderer = QSvgRenderer()
            svg_path = resource_path(f"assets/images/{filename}")
            if renderer.load(svg_path):
                self.renderers[status] = renderer
            else:
                logger.warning("Erreur lors du chargement de %s", filename)

    def set_status(self, status, reconnection_attempts=None):
        logger.debug("Indicateur de connexion : %s", status)
        try:
            if self.isVisible():
                self.status = status
                if status == "connected":
                    self.last_connection_time = QDateTime.currentDateTime()
                    self.reconnection_attempts = 0
                elif reconnection_attempts is not None:
                    self.reconnection_attempts = reconnection_attempts
                self.update_tooltip()
                self.update()
        except RuntimeError:
            pass

    def update_tooltip(self):
        try:
            if self.isVisible():
                if self.status == "connected":
                    if self.last_connection_time:
                        time_str = self.last_connection_time.toString("HH:mm:ss")
                        tooltip = f"Connecté depuis {time_str}"
                    else:
                        tooltip = "Temps réel Connecté"
                else:
                    tooltip = "Temps réel déconnecté"
                    if self.reconnection_attempts > 0:
                        tooltip += f"\nNombre de tentatives de reconnexion : {self.reconnection_attempts}"
                
                self.setToolTip(tooltip)
        except RuntimeError:
            pass

    def paintEvent(self, event):
        try:
            if self.isVisible() and self.status in self.renderers:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.Antialiasing)
                self.renderers[self.status].render(painter, self.rect())
        except RuntimeError:
            pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    app.setApplicationName("PySide6 Web Browser Example2")
    app.setOrganizationName("MyCompany2")
    app.setOrganizationDomain("mycompany.com")

    #stylesheet = load_stylesheet("Incrypt.qss")
    #app.setStyleSheet(stylesheet)
    
    # MainWindow.show() est appelé en interne une fois l'initialisation
    # asynchrone terminée (_on_startup_ready), pas ici : l'appeler tout de
    # suite afficherait une fenêtre encore vide pendant le chargement.
    window = MainWindow()
    sys.exit(app.exec())
