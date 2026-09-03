import sys
import time
from functools import partial
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QMessageBox, QDialog
from PySide6.QtCore import Signal, Slot, QSettings, QTimer, Qt
from PySide6.QtGui import QIcon, QAction

from websocket_client import WebSocketClient
from preferences import PreferencesDialog
from app_identity import apply_identity, legacy_sources, migrate_legacy_settings
from button_state import MALFORMED, resolve_patient_buttons
from patient_list_model import PatientListModel
from notification import CustomNotification, NotificationManager
from connections import NetworkManager
from counter_api import CounterApi
import main_window_ui
from login_view import create_login_widget
from shortcut_manager import ShortcutManager
from tray_manager import TrayManager
from window_placement import WindowPlacement
from session_controller import SessionController
from audio import build_audio_player
from loading_screen import LoadingScreen
from my_logger import AppLogger, register_secret
from secret_store import load_secret
from task_registry import TaskRegistry
from resync_coordinator import snapshot_is_fresh
from counter_id_utils import coerce_counter_id
from shortcut_defaults import read_shortcut
from preferences_diff import needs_service_reconnect
import endpoints
import resources
import settings_schema
from accessibility import (
    validate_alert_text,
)

import logging
# Logger de module : propage vers les handlers configurés par AppLogger
# (fichier tournant + masquage + fenêtre UI). À utiliser dans les classes qui
# n'ont pas de self.logger (ex. AudioPlayer).
logger = logging.getLogger("appcomptoir.main")


# Réexporté pour les appelants historiques : l'implémentation vit désormais dans
# resources.py (source unique, testée, correcte en build onefile).
resource_path = resources.resource_path




class MainWindow(QMainWindow):

    patient_data_received = Signal(object)

    # Passe à True au tout début de la fermeture : bloque toute nouvelle action
    # réseau et évite la réentrance de closeEvent.
    shutting_down = False

    # Vrai pendant une déconnexion utilisateur en attente de confirmation serveur
    # (évite les doubles déclenchements ; l'UI n'est finalisée qu'après la réponse).
    _disconnect_in_progress = False

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

        # Placement de la fenêtre (point 10.12) : géométrie persistée,
        # garde-fou multi-écran, mode panneau compact et magnétisme aux bords.
        self.placement = WindowPlacement(self, logger=self.logger)
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

        self.activities_staff = None  # pour être en global
        # Gestionnaire de notifications (créé paresseusement au premier affichage).
        self.notification_manager = None

        # LOAD PREFERENCES
        self.load_preferences()

        # on créé un timer qui permet d'alerter si le patient reste en Calling
        self.create_call_timer()

        # quand App se ferme, on ferme aussi le systray
        app = QApplication.instance()
        app.aboutToQuit.connect(self.cleanup_systray)

        # Si un moniteur est débranché/ajouté ou la géométrie d'un écran change,
        # on revérifie que la fenêtre reste dans une zone visible (point 24).
        app.screenRemoved.connect(lambda _screen: self.placement.ensure_visible())
        app.screenAdded.connect(lambda _screen: self.placement.ensure_visible())

        self.logger.info("Test de la connexion...")
        self.app_token = None
        self.connected = False

        # Gestionnaire réseau centralisé : un unique worker possède la seule
        # requests.Session (plus d'accès concurrent depuis plusieurs threads),
        # et centralise jeton, timeout, format d'erreur, renouvellement sur 401
        # et idempotence. Les providers lisent web_url/app_secret à la volée
        # (rechargés dans load_preferences).
        self.network_manager = NetworkManager(
            token_url_provider=lambda: endpoints.app_token(self.web_url),
            secret_provider=lambda: self.app_secret,
        )
        self.network_manager.token_refreshed.connect(self._on_token_refreshed)
        self.network_manager.token_failed.connect(self._on_token_failed)

        # Registre des tâches réseau actives. Conserve une référence forte à
        # chaque RequestHandle/worker tant qu'il n'est pas terminé, pour ne plus
        # écraser un self.thread encore actif (perte de suivi, signaux 'result'/
        # 'finished' perdus, "QThread: Destroyed while thread is still running").
        # Empêche aussi une seconde action identique (même clé) tant que la
        # première est en cours.
        self._tasks = TaskRegistry()

        # Couche d'accès au serveur (point 10.8) : toutes les requêtes REST du
        # comptoir passent par elle. MainWindow ne connaît plus ni URL, ni méthode
        # HTTP, ni clé d'idempotence — seulement des actions métier. Les providers
        # évitent toute copie périmée de la configuration (rechargée par
        # load_preferences et par un changement de connexion).
        self.api = CounterApi(
            self.network_manager, self._tasks,
            url_provider=lambda: self.web_url,
            counter_id_provider=lambda: self.counter_id,
            logger=self.logger,
            is_shutting_down=lambda: self.shutting_down,
        )

        # Séquences de fond (point 10.13) : démarrage et resynchronisation, hors
        # thread graphique, avec coalescing des resyncs et suivi des threads.
        self.session = SessionController(self, self._tasks, logger=self.logger)

        # Raccourcis clavier (point 10.10) : installation sérialisée, hooks
        # système ou QShortcut selon le mode, confirmation des actions sensibles.
        # Le manager connecte ses signaux une seule fois, indépendamment des
        # ré-enregistrements faits à chaque changement de préférences.
        self.shortcuts = ShortcutManager(self, logger=self.logger)

        # Icônes de la zone de notification (point 10.11) : créées plus tard, à
        # l'initialisation de l'interface (setup_ui), mais le gestionnaire existe
        # dès maintenant pour que la fermeture puisse toujours les retirer.
        self.tray = TrayManager(self, logger=self.logger)

        # La séquence réseau de démarrage (token + patient courant + liste des
        # patients) se fait en arrière-plan pour ne pas geler l'UI si le
        # serveur est lent/injoignable. La suite de l'initialisation continue
        # dans _on_startup_ready() une fois le résultat disponible.
        self._start_startup_sequence()

    def _start_startup_sequence(self):
        """ (Re)lance la séquence réseau de démarrage en arrière-plan. Rappelée
        après (re)configuration d'un comptoir valide. """
        self.session.start_startup(self._on_startup_ready)

    def _on_startup_ready(self, connected, state):
        """ Suite de l'initialisation une fois la séquence réseau de démarrage terminée """
        self.connected = connected

        # Configuration incomplète (1er démarrage, valeur corrompue…) : on
        # N'entre PAS en mode comptoir et on ouvre l'écran de configuration.
        # - URL serveur vide = « non configuré » : aucune adresse par défaut
        #   n'est gravée dans le code (chaque officine renseigne la sienne).
        # - counter_id : sans entier valide, toutes les comparaisons/URL de
        #   comptoir seraient incohérentes.
        if not self.web_url or self.counter_id is None:
            self.logger.error(
                "Configuration incomplète (url=%r, counter_id=%r) : ouverture de "
                "l'écran de configuration.", self.web_url, self.counter_id)
            self._require_valid_counter_id()
            return

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
        # Restaure la géométrie mémorisée AVANT show(), puis vérifie la visibilité
        # APRÈS show() (la géométrie de cadre n'est fiable qu'une fois affichée).
        self.placement.restore()
        self.show()
        self.placement.ensure_visible()

        # Mode panneau compact : docke la fenêtre après show() (la géométrie de
        # cadre n'est fiable qu'une fois affichée), après la restauration/visibilité.
        if self.compact_mode:
            self.placement.apply_panel_mode()

        self.alert_if_not_connected()

        if not self.debug_window:
            self.loading_screen.close()

    def _require_valid_counter_id(self):
        """ Ouvre l'écran de configuration tant qu'aucun comptoir valide n'est
        défini. Une fois un counter_id entier valide enregistré, on relance la
        séquence de démarrage ; si l'utilisateur annule, on ne peut pas démarrer
        le mode comptoir et on quitte proprement. """
        if self.loading_screen:
            self.loading_screen.close()
        try:
            dialog = PreferencesDialog(self)
            accepted = dialog.exec()
        except Exception as e:
            self.logger.error("Écran de configuration indisponible : %s", e)
            accepted = False

        if accepted:
            self.load_preferences()
            if self.web_url and self.counter_id is not None:
                self.logger.info(
                    "Configuration valide (url renseignée, comptoir id=%s), démarrage.",
                    self.counter_id)
                self._start_startup_sequence()
                return

        self.logger.error("Configuration incomplète (URL ou comptoir manquant) : arrêt de l'application.")
        QApplication.instance().quit()

    def load_preferences(self):
        self.logger.info("Initialisation des préférences...")

        settings = QSettings()
        # Migration + estampille de version du schéma de configuration. Toutes
        # les valeurs ci-dessous sont lues via settings_schema (source unique des
        # clés, types, défauts et plages) : main.py et preferences.py partagent
        # ainsi exactement les mêmes défauts (fin des divergences historiques
        # URL / notification patient courant / délai après appel).
        settings_schema.migrate_settings(settings)
        self.web_url = settings_schema.read(settings, "web_url")
        # Le secret applicatif est lu depuis le magasin sécurisé (keyring /
        # Gestionnaire d'identifiants Windows), avec migration automatique de
        # l'ancienne valeur en clair éventuellement présente dans QSettings.
        self.app_secret = load_secret(settings)
        # Masquage du secret dans tous les logs (défense en profondeur).
        register_secret(self.app_secret)
        # counter_id normalisé en entier strictement positif (ou None si invalide).
        # QSettings peut renvoyer une chaîne ("1") ; le serveur utilise des
        # entiers. On garantit un seul type dans toute l'app pour que les
        # comparaisons (WebSocket, patient["counter_id"]...) soient cohérentes.
        # Défaut contextuel (runtime = 1) : géré hors schéma, cf. settings_schema.
        self.counter_id = coerce_counter_id(settings.value("counter_id", 1))
        # Raccourcis : défauts centralisés dans shortcut_defaults (source unique)
        # + migration transparente des anciennes valeurs erronées (ex: "Altl+P").
        # Lecture via read_shortcut (shortcut_defaults) : MÊME fonction que celle
        # utilisée par la fenêtre de préférences — plus de double logique.
        self.next_patient_shortcut = read_shortcut(settings, "next_patient_shortcut", self.logger)
        self.validate_patient_shortcut = read_shortcut(settings, "validate_patient_shortcut", self.logger)
        self.pause_shortcut = read_shortcut(settings, "pause_shortcut", self.logger)
        self.recall_shortcut = read_shortcut(settings, "recall_shortcut", self.logger)
        self.deconnect_shortcut = read_shortcut(settings, "deconnect_shortcut", self.logger)
        # Mode des raccourcis (point 27) : désactivés / actifs au premier plan /
        # globaux. Défaut = global (comportement historique). + confirmation
        # facultative des actions sensibles et retour visuel de l'action.
        self.shortcut_mode = settings_schema.read(settings, "shortcut_mode")
        self.confirm_sensitive_shortcuts = settings_schema.read(settings, "confirm_sensitive_shortcuts")
        self.shortcut_feedback = settings_schema.read(settings, "shortcut_feedback")
        self.notification_current_patient = settings_schema.read(settings, "notification_current_patient")
        self.notification_autocalling_new_patient = settings_schema.read(settings, "notification_autocalling_new_patient")
        self.notification_specific_acts = settings_schema.read(settings, "notification_specific_acts")
        self.notification_add_paper = settings_schema.read(settings, "notification_add_paper")
        self.notification_connection = settings_schema.read(settings, "notification_connection")
        self.notification_after_deconnection = settings_schema.read(settings, "notification_after_deconnection")
        self.timer_after_calling = settings_schema.read(settings, "notification_after_calling")
        self.notification_duration = settings_schema.read(settings, "notification_duration")
        self.notification_font_size = settings_schema.read(settings, "notification_font_size")
        # Ton des messages de notification (point 28) : « sobre » (explicite,
        # défaut) ou « humoristique » (ancien ton). Titres calculés dans
        # accessibility.notification_title.
        self.message_tone = settings_schema.read(settings, "message_tone")
        # Taille de police de la file des patients (point 28), bornée au plancher
        # de lisibilité. Remplace l'ancienne valeur figée de 8 pt.
        self.patient_list_font_size = settings_schema.read(settings, "patient_list_font_size")
        # Coin de l'écran où empiler les notifications (configurable).
        self.notification_corner = settings_schema.read(settings, "notification_corner")
        self.sound_volume = settings_schema.read(settings, "notification_volume")

        self.always_on_top = settings_schema.read(settings, "always_on_top")
        self.horizontal_mode = settings_schema.read(settings, "vertical_mode")
        # Mode panneau compact (point 25) : panneau étroit docké sur un bord
        # plutôt qu'une fenêtre générique. En mode vertical, colonne étroite ; en
        # mode horizontal, barre fine au-dessus du progiciel.
        self.compact_mode = settings_schema.read(settings, "compact_mode")
        # Magnétisme aux bords de l'écran lors d'un déplacement manuel.
        self.panel_snap = settings_schema.read(settings, "panel_snap")
        # Épaisseur du panneau (largeur en vertical, hauteur en horizontal), bornée.
        self.panel_thickness = settings_schema.read(settings, "panel_thickness")
        self.display_patient_list = settings_schema.read(settings, "display_patient_list")
        self.patient_list_position_vertical = settings_schema.read(settings, "patient_list_vertical_position")
        self.patient_list_position_horizontal = settings_schema.read(settings, "patient_list_horizontal_position")
        self.debug_window = settings_schema.read(settings, "debug_window")
        # Journalisation détaillée (DEBUG) seulement si la fenêtre de log est
        # demandée ; sinon INFO (production). Les logs DEBUG ne sont donc pas
        # actifs en usage normal.
        if hasattr(self, "app_logger"):
            self.app_logger.enable_debug(self.debug_window)
        self.selected_skin = settings_schema.read(settings, "selected_skin")

    def setup_ui(self):
        self.logger.info("Initialisation de l'interface...")

        icon_path = resource_path('assets/images/next.ico')
        self.setWindowIcon(QIcon(icon_path))
        self.setWindowTitle("PharmaFile")

        self.tray.setup()

        # self.list_patients a déjà été renseigné par _on_startup_ready()
        # (récupéré en arrière-plan par StartupWorker) avant l'appel à setup_ui().
        self.logger.debug("Liste patients chargée (%s patients)",
                          len(self.list_patients) if self.list_patients else 0)

        self.create_interface()

        self.load_skin()

        self.setup_global_shortcut()

    def create_interface(self):
        """ (Re)construit l'interface principale. Le détail des widgets vit dans
        ``main_window_ui`` ; la fenêtre garde la décision de QUAND reconstruire
        (démarrage, changement d'orientation, retour de l'écran de connexion). """
        main_window_ui.create_interface(self)

    def _update_menu_actions(self, enable):
        """Active ou désactive les actions du menu"""
        self.action_wait.setEnabled(enable)
        if hasattr(self, 'wait_for_submenu'):
            self.wait_for_submenu.setEnabled(enable)
        self.action_delete.setEnabled(enable)

    def on_action_wait(self):
        # Logique pour remettre le patient en attente
        self.logger.debug("Patient remis en attente")
        self.api.put_standing(self.patient_id, on_result=self.handle_result)

    def on_action_wait_for(self, activity, patient_id=None):
        """
        patient_id: si non fourni, utilise self.patient_id (patient en cours)
        """
        target_id = patient_id if patient_id is not None else self.patient_id
        self.logger.debug("Patient remis en attente pour l'activité id=%s", activity['id'])
        self.api.put_standing(target_id, activity["id"], on_result=self.handle_result)

    def on_action_validate(self, patient_id):
        # Patient désigné dans la file (menu contextuel) : route « file », pas
        # celle du comptoir.
        self.validate_my_patient(partial(self.api.validate_queued_patient, patient_id))

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
            self.api.delete_patient(target_id, on_result=self.handle_result)

    def _set_validate_alert(self, active):
        """Marque (ou démarque) le bouton Valider comme « patient à valider ».

        Accessibilité (point 28) : l'état ne repose plus uniquement sur le fond
        rouge. On enrichit aussi le libellé d'un pictogramme d'alerte, le nom
        accessible et l'infobulle, pour rester compréhensible en niveaux de gris
        et pour les lecteurs d'écran."""
        button = getattr(self, "btn_validate", None)
        if button is None:
            return
        base_label = getattr(button, "_base_label", button.text())
        if active:
            button.setRed()
            button.setText(validate_alert_text(base_label))
            button.setAccessibleName("Patient à valider")
            button.setToolTip("Patient à valider — cliquez pour valider")
        else:
            button.resetColor()
            button.setText(base_label)
            button.setAccessibleName("Valider")
            button.setToolTip("Valider")



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
        # L'idempotence de cette action (ne pas faire avancer la file deux fois
        # si la requête est rejouée) est garantie par la couche d'accès.
        self.api.validate_and_call_next(on_result=self.handle_result,
                                        busy_button=self.btn_next)
        self.update_my_buttons(self.my_patient)
        self.close_please_validate_notification()


    def call_web_function_validate(self):
        self.logger.debug("Validation du patient (call_web_function_validate)")
        self.close_please_validate_notification()
        self.validate_my_patient(partial(self.api.validate_current_patient, self.patient_id))


    def validate_my_patient(self, send):
        """ Valide le patient s'il y en a un. ``send`` est la requête à envoyer,
        déjà liée au patient concerné (celui du comptoir, ou un patient désigné
        dans la file) : cette méthode ne décide que du « faut-il envoyer ». """
        self.logger.debug("Validation du patient en cours")
        self.close_please_validate_notification()
        if self.my_patient:
            send(on_result=self.handle_result, busy_button=self.btn_validate)
        # permet de supprimer le Validate en rouge et l'alerte en si le bouton "Valider" est resté enclenché mais qu'il n'y a plus de patient
        else:
            self.update_my_buttons(self.my_patient)

    def close_please_validate_notification(self):
        # Fermeture des notification qui appele à valider le patient si il y a en a ouverte et que l'on clique sur le bouton "Valider"
        if getattr(self, 'notification_manager', None) is not None:
            for notification in self.notification_manager.active_notifications[:]:  # Create a copy of the list to avoid modification during iteration
                if isinstance(notification, CustomNotification) and getattr(notification, 'origin', None) == "please_validate":
                    notification.close()

    def call_web_function_pause(self):
        self.logger.debug("Mise en pause du patient")
        self.api.pause_current_patient(self.patient_id, on_result=self.handle_result,
                                       busy_button=self.btn_pause)



    def toggle_compact_mode(self):
        """Bascule rapide du mode panneau compact (menu « Menu »). Persiste le
        choix et reconstruit l'interface pour appliquer le style compact ; en
        l'activant, docke immédiatement le panneau."""
        self.compact_mode = not self.compact_mode
        QSettings().setValue("compact_mode", self.compact_mode)
        self.logger.info("Mode panneau compact : %s", self.compact_mode)
        self.create_interface()
        if self.compact_mode:
            self.placement.apply_panel_mode()

    def moveEvent(self, event):
        """Déplacement de la fenêtre (surcharge Qt) : le magnétisme aux bords est
        décidé par ``WindowPlacement``."""
        super().moveEvent(event)
        self.placement.on_window_moved()


    def toggle_patient_list(self):
        if self.patient_list_dock.isVisible():
            self.patient_list_dock.hide()
        else:
            self.patient_list_dock.show()
    
    def hide_patient_list(self):
        self.patient_list_dock.hide()

    def _on_patient_list_clicked(self, index):
        """Clic sur une ligne de la vue : appelle le patient correspondant
        (équivalent de l'ancien clic sur un PatientButton)."""
        self._last_patient_list_trigger = time.monotonic()
        patient_id = index.data(PatientListModel.IdRole)
        if patient_id is not None:
            self.call_web_function_validate_and_call_specifique(patient_id)

    def _on_patient_list_activated(self, index):
        """Activation clavier (Entrée) d'une ligne : même effet qu'un clic.

        Un double-clic souris émet à la fois ``clicked`` et ``activated`` ; on
        ignore l'``activated`` s'il suit immédiatement un ``clicked`` pour ne pas
        appeler deux fois le même patient."""
        last = getattr(self, "_last_patient_list_trigger", 0)
        if time.monotonic() - last < 0.3:
            return
        self._on_patient_list_clicked(index)

    def _on_patient_list_context_menu(self, position):
        """Menu contextuel d'un patient de la vue (valider / supprimer /
        assigner). Reprend à l'identique l'ancien menu de PatientButton, mais
        construit à la demande pour le patient sous le curseur."""
        index = self.patient_list_view.indexAt(position)
        if not index.isValid():
            return
        patient = index.data(PatientListModel.PatientRole)
        if not isinstance(patient, dict):
            return
        patient_id = patient.get("id")
        if patient_id is None:
            return

        menu = QMenu(self.patient_list_view)

        action_validate = menu.addAction("Marquer comme validé")
        action_validate.triggered.connect(
            lambda checked=False, pid=patient_id: self.on_action_validate(pid))

        action_delete = menu.addAction("Supprimer")
        action_delete.triggered.connect(
            lambda checked=False, pid=patient_id: self.on_action_delete(pid))

        if getattr(self, 'activities_staff', None):
            assign_submenu = QMenu("Assigner à...", menu)
            for activity in self.activities_staff:
                action = assign_submenu.addAction(activity['name'])
                action.triggered.connect(
                    lambda checked=False, a=activity, pid=patient_id:
                    self.on_action_wait_for(a, pid))
            menu.addMenu(assign_submenu)

        menu.exec(self.patient_list_view.viewport().mapToGlobal(position))

    def toggle_orientation(self):
        self.horizontal_mode = not self.horizontal_mode
        self.create_interface()
        # Le passage vertical/horizontal redocke le panneau dans la bonne
        # dimension (colonne <-> barre) sans perdre l'état fonctionnel.
        if self.compact_mode and self.isVisible():
            self.placement.apply_panel_mode()


    def init_list_patients(self):
        return self.api.fetch_patients_list()

    def recall(self):
        self.api.relaunch_call()

    def setup_user(self):
        """ Va chercher le staff sur le comptoir """
        self.logger.info("Paramétrage de l'utilisateur...")
        self.api.fetch_staff(on_result=self.handle_user_result)

    def _notify_network_error(self, result):
        """ Affiche un message utilisateur court (distinct selon le statut :
        401/403/409-423/5xx/timeout) et journalise le détail technique. Le détail
        n'est jamais montré à l'utilisateur. """
        if result.message and getattr(self, "notification_connection", True):
            self.show_notification({"origin": "connection", "message": result.message}, internal=True)
        if result.detail:
            self.logger.warning("Erreur réseau (statut=%s) : %s", result.status, result.detail)

    @Slot(object)
    def handle_result(self, result):
        self.logger.debug("Réponse action patient (statut=%s)", result.status)
        status = result.status
        if status == 200:
            data = result.data
            if isinstance(data, dict):
                self.update_my_patient(data)
                self.update_my_buttons(data)
                if self.notification_current_patient and data.get("call_number"):
                    message = f"Nouveau patient : {data['call_number']} pour '{data.get('activity', '')}'"
                    self.show_notification({"origin": "new_patient", "message": message}, internal=True)
            else:
                self.logger.warning("Réponse 200 sans JSON exploitable")
        # plus de patient. Attention 204 ne permet pas de passer une info car 204 =pas de données
        elif status == 204:
            self.update_my_patient(None)
        # utiliser pour supprimer ou remettre un patient en attente
        elif status == 201:
            self.update_my_patient(False)
            patient = {"counter_id": self.counter_id, "id": None}
            self.update_my_buttons(patient)
        # 423 = patient déjà pris par un autre comptoir (message dédié via l'UI)
        elif status == 423:
            self.patient_already_taken()
        else:
            self._notify_network_error(result)

    @Slot(object)
    def handle_user_result(self, result):
        # si staff au comptoir
        if result.status == 200:
            data = result.data
            try:
                self.staff_id = data["staff"]["id"]
                staff_name = data["staff"]["name"]
                self.update_window_title(staff_name)
                self.update_staff_label(staff_name)
            except (TypeError, KeyError):
                self.logger.warning("Réponse staff inexploitable")
        # si personne au comptoir
        elif result.status == 204:
            self.logger.debug("Aucun staff sur le comptoir")
            # deconnexion
            self.disconnect_from_counter()
            self.staff_id = False
            # on modifie le titre
            self.update_window_title("Connectez-vous !")
            # on affiche l'interface de connexion
            self.deconnexion_interface()
        else:
            self._notify_network_error(result)
        
        
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
        return self.api.fetch_state()

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

    def _request_resync(self):
        """ Demande une resynchronisation de l'état autoritatif. Le contrôleur de
        session garantit qu'une seule passe réseau est active à la fois et fusionne
        les demandes reçues entretemps. """
        self.session.request_resync(self._on_resync_ready)

    def init_patient(self):
        return self.api.fetch_current_patient()

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
                # poussé par le serveur. Coalescing : une seule resync à la fois.
                self.socket_was_disconnected = False
                self._request_resync()
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
        et rafraîchit l'UI. Libère le verrou de resync et relance UNE passe si une
        a été demandée entretemps. Un snapshot périmé (révision plus ancienne que
        l'état connu) n'est jamais appliqué. """
        relaunch = self.session.finish_resync()
        try:
            if state and snapshot_is_fresh(state.get("revision"), self.queue_revision):
                self._apply_resync_state(state)
            elif state:
                self.logger.debug("Snapshot resync périmé ignoré (rev %s < %s)",
                                  state.get("revision"), self.queue_revision)
        finally:
            # Coalescing : relance unique si des passes ont été demandées pendant
            # la resync, pour converger vers l'état le plus récent.
            if relaunch and not self.shutting_down:
                self._request_resync()

    def _apply_resync_state(self, state):
        """ Applique effectivement la snapshot et rafraîchit l'UI (patient courant,
        liste, papier, autocalling ET staff). """
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

        # liste des patients (vue différentielle + compteur ; menus paresseux)
        self.refresh_patient_lists()

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
        self.logger.debug("Mise à jour du patient en cours")

        # Cas « pas de patient » explicites (None / False) : état sûr, sans action.
        if patient is None:
            self.patient_id = None
            self.label_patient.setText("Plus de patient")
            self._update_menu_actions(False)
            return
        if patient is False:
            self.patient_id = None
            self.label_patient.setText("Pas de patient")
            self._update_menu_actions(False)
            return

        # À partir d'ici on attend un dict patient. On valide explicitement la
        # structure et on ne capture QUE les exceptions attendues (clé manquante,
        # mauvais type), au lieu d'un except générique qui masquait l'erreur.
        if not isinstance(patient, dict):
            self._on_invalid_patient(patient)
            return

        try:
            # Patient d'un autre comptoir : rien à afficher ici (état inchangé).
            if patient["counter_id"] != self.counter_id:
                return

            if patient["id"] is None:
                self.patient_id = None
                self.label_patient.setText("Pas de patient en cours")
                self._update_menu_actions(False)
                return

            self.patient_id = patient["id"]
            status_text = {"calling": "En appel", "ongoing": "Au comptoir"}.get(patient["status"], "????")
            language_code = patient["language_code"]
            language = f" ({language_code}) ".upper() if language_code != "fr" else ""
            patient_text = f"{patient['call_number']}{language} {status_text} ({patient['activity']})"
            self.label_patient.setText(patient_text)
            # Texte complet en infobulle : reste lisible même tronqué dans un
            # panneau compact étroit (point 25).
            self.label_patient.setToolTip(patient_text)
            self._update_menu_actions(True)  # Active les actions car il y a un patient
        except (KeyError, TypeError) as e:
            self._on_invalid_patient(patient, error=e)

    def _on_invalid_patient(self, patient, error=None):
        """ Données patient incomplètes/invalides : on remet l'interface dans un
        état sûr (aucune action patient possible) et on journalise le détail
        technique — l'erreur originale reste visible dans les logs — sans crasher
        ni exposer le détail à l'utilisateur. """
        self.patient_id = None
        self._update_menu_actions(False)
        self.label_patient.setText("Données patient indisponibles")
        if error is not None:
            # Appelé depuis un except : journalise la trace de l'erreur originale.
            self.logger.exception("Donnée patient invalide : %s", error)
        else:
            self.logger.error("Donnée patient invalide (type %s)", type(patient).__name__)

    def update_my_buttons(self, patient):
        """ Applique l'état des boutons Valider/Pause et du minuteur d'appel pour
        ``patient``.

        La DÉCISION est prise par ``button_state.resolve_patient_buttons`` (pure,
        testée) ; cette méthode ne fait que l'appliquer. Elle remplace l'ancien
        ``except:`` nu « #TEMPORAIRE » qui masquait aussi bien une donnée serveur
        malformée qu'une faute de frappe dans le code.

        Deux cas laissent volontairement l'interface inchangée : une mise à jour
        concernant un autre comptoir, et une donnée inexploitable (journalisée).
        Les widgets peuvent aussi ne pas exister (écran de connexion) : on le
        vérifie explicitement au lieu de compter sur une exception avalée.
        """
        decision, reason = resolve_patient_buttons(patient, self.counter_id)
        if decision is None:
            if reason == MALFORMED:
                # Aucune valeur patient dans le journal (données de santé) :
                # seulement le type et, pour un dict, les noms de champs reçus.
                shape = sorted(patient) if isinstance(patient, dict) else type(patient).__name__
                self.logger.warning(
                    "Boutons patient non recalculés : %s (reçu : %s)", reason, shape)
            else:
                self.logger.debug("Boutons patient inchangés : %s", reason)
            return
        if not hasattr(self, "btn_pause") or not hasattr(self, "btn_validate"):
            # Interface de connexion affichée : les boutons n'existent pas encore.
            self.logger.debug("Boutons patient absents (écran de connexion) : %s", reason)
            return
        self.logger.debug("Boutons patient : %s", reason)
        self.btn_pause.setEnabled(decision.pause_enabled)
        self.btn_validate.setEnabled(decision.validate_enabled)
        if decision.validate_alert is not None:
            self._set_validate_alert(decision.validate_alert)
        if decision.start_call_timer:
            self.call_timer.start()   # patient en appel : minuteur de relance
        else:
            self.call_timer.stop()    # plus personne à valider : minuteur arrêté

    def deconnection(self):
        """ Déconnexion demandée par l'utilisateur. On affiche « Déconnexion en
        cours » et on NE bascule PAS immédiatement sur l'écran de connexion : la
        bascule n'a lieu qu'APRÈS confirmation du serveur (handle_disconnect_result).
        En cas d'échec, l'état précédent est conservé et on propose de réessayer,
        pour éviter toute divergence entre l'état local et l'état serveur. """
        if self._disconnect_in_progress:
            return
        self._disconnect_in_progress = True
        # Mémorise le titre courant (nom du staff) pour le restaurer en cas d'échec.
        self._title_before_disconnect = self.windowTitle()
        self.setWindowTitle("PharmaFile - Déconnexion en cours…")
        self.disconnect_from_counter(on_result=self.handle_disconnect_result)

    def deconnexion_interface(self):
        self.logger.debug("Affichage de l'interface de connexion")
        # Créer et définir le widget de connexion
        login_widget = create_login_widget(self)
        self.setCentralWidget(login_widget)

        self.hide_patient_list()
        
        # désactivation du champ à l'initialisation sinon le raccourci clavier est entré dans le champ
        self.initials_input.setDisabled(True)
        # réactivation après 100ms
        QTimer.singleShot(100, self.enable_initials_input)

    def disconnect_from_counter(self, on_result=None):
        """ Envoie la déconnexion (remove_staff) au serveur.

        ``on_result`` : handler de résultat. La déconnexion utilisateur passe
        handle_disconnect_result (finalise l'UI après confirmation). Le chemin
        automatique (staff déjà absent côté serveur, 204) l'appelle sans handler
        (fire-and-forget), l'UI ayant déjà été mise à jour selon l'état serveur. """
        self.api.logout_staff(on_result=on_result)

    def enable_initials_input(self):
        """ Permet d'activer le champ des initiales lors de l'initialisation + focus
        Obligé de le désactiver pour éviter entrée du raccourci clavier dans le champ """
        self.initials_input.setDisabled(False)
        # Donner le focus au champ des initiales
        self.initials_input.setFocus()

    @Slot(object)
    def handle_disconnect_result(self, result):
        self.logger.debug("Réponse déconnexion (statut=%s)", result.status)
        self._disconnect_in_progress = False
        if result.status == 200:
            # Confirmé par le serveur : on finalise SEULEMENT maintenant l'écran
            # de connexion (pas avant, pour éviter un faux état local).
            self.staff_id = None
            self.update_window_title("Déconnecté")
            self.deconnexion_interface()
        else:
            # Échec (réseau, 5xx…) : l'état précédent est CONSERVÉ (l'utilisateur
            # reste connecté au comptoir). On rétablit le titre et on propose de
            # réessayer, pour ne pas diverger de l'état serveur.
            self.logger.warning("Échec de la déconnexion : %s", result.detail)
            self._offer_retry_disconnect(result)

    def _offer_retry_disconnect(self, result):
        """ Rétablit l'état connecté et propose « Réessayer » après un échec de
        déconnexion. """
        if getattr(self, "_title_before_disconnect", None):
            self.setWindowTitle(self._title_before_disconnect)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Déconnexion impossible")
        box.setText((result.message or "La déconnexion a échoué.")
                    + "\nVous êtes toujours connecté au comptoir.")
        retry_btn = box.addButton("Réessayer", QMessageBox.AcceptRole)
        box.addButton("Annuler", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is retry_btn:
            self.deconnection()

    def validate_login(self):
        if not self.app_token:
            self.logger.warning("Connexion impossible : pas de token valide")
            return
        
        initials = self.initials_input.text()
        cb_deconnexion_on_all = self.checkbox_on_all.isChecked()

        if initials:
            self.api.login_staff(initials, cb_deconnexion_on_all,
                                 on_result=self.handle_login_result)

    @Slot(object)
    def handle_login_result(self, result):
        self.logger.debug("Réponse connexion staff (statut=%s)", result.status)
        if result.status == 200:
            data = result.data
            try:
                staff_name = data["staff"]["name"]
                self.staff_id = data["staff"]["id"]
            except (TypeError, KeyError):
                self.logger.warning("Réponse de connexion inexploitable")
                QMessageBox.warning(self, "Erreur de connexion", "Réponse inattendue du serveur.")
                return
            # Mise à jour de la barre de titre
            self.update_window_title(staff_name)
            # Recréer l'interface principale1
            self.recreate_main_interface()
            self.update_staff_label(staff_name)
            # Mettre à jour l'interface si nécessaire
            self.init_patient()
        elif result.status == 204:
            self.logger.debug("Initiales inconnues")
            self.staff_id = False
            # Mettre à jour le label de connexion
            if hasattr(self, 'label_connexion'):
                self.label_connexion.setText("Initiales incorrectes ! ")
        else:
            self.logger.warning("Échec de la connexion staff : %s", result.detail)
            QMessageBox.warning(self, "Erreur de connexion", "Impossible de se connecter. Veuillez réessayer.")
    
    def recreate_main_interface(self):
        # Supprime l'ancien widget central (widget de login)
        if self.centralWidget():
            self.centralWidget().deleteLater()

        # Recrée l'interface principale
        self.create_interface()

        # Après une (re)construction de l'interface comptoir alors que la fenêtre
        # est déjà affichée (connexion staff, resync), on rétablit le panneau
        # compact docké si le mode est actif.
        if self.compact_mode and self.isVisible():
            self.placement.apply_panel_mode()
    
    def show_preferences_dialog(self):
        # Un SEUL mécanisme d'application (point 7) : le dialogue se contente de
        # renvoyer Accepted lorsqu'il a enregistré les préférences ; c'est ICI,
        # et seulement ici, qu'on déclenche l'UNIQUE apply_preferences (recharge +
        # cosmétique + reconnexion éventuelle). Plus de signal preferences_updated
        # concurrent, plus de rechargement après exec() : un seul propriétaire.
        dialog = PreferencesDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.apply_preferences()


    def setup_global_shortcut(self):
        """ (Ré)installe les raccourcis selon le mode courant. Nom conservé car
        appelé au démarrage (setup_ui) et après changement de préférences ; le
        mécanisme lui-même vit dans ``ShortcutManager``. """
        self.shortcuts.install()


    def call_web_function_validate_and_call_specifique(self, patient_select_id):
        self.api.call_specific_patient(patient_select_id, on_result=self.handle_result)


    def _on_token_refreshed(self, token):
        """ Synchronise self.app_token quand le gestionnaire réseau renouvelle le
        jeton (utilisé par le WebSocket et la connexion staff). Le jeton n'est
        jamais journalisé ; on l'enregistre pour masquage (défense en profondeur). """
        self.app_token = token
        register_secret(token)

    def _on_token_failed(self):
        self.app_token = None

    def get_app_token(self):
        """ Demande un jeton applicatif à la couche d'accès (bloquant, à appeler
        depuis un thread de fond : StartupWorker) et met à jour le miroir local.
        Lève une exception si l'authentification échoue, pour que l'appelant
        (démarrage, renouvellement) le sache clairement. """
        try:
            token = self.api.fetch_token()
        except Exception:
            self.app_token = None
            raise
        # _on_token_refreshed a déjà (ou va) mettre self.app_token à jour via le
        # signal ; on le pose aussi ici pour ne pas dépendre de l'ordonnancement.
        self.app_token = token
        register_secret(token)

    def try_refresh_app_token(self):
        """ Variante de get_app_token() qui ne lève pas d'exception (à utiliser
        avant une reconnexion WebSocket). """
        try:
            self.get_app_token()
            return True
        except Exception as e:
            self.logger.warning("Échec du renouvellement du token : %s", e)
            return False


    def apply_preferences(self):
        """ Appelé après enregistrement des préférences. Compare les anciennes et
        nouvelles valeurs : si le serveur, le secret ou le comptoir changent, on
        reconnecte entièrement les services ; sinon on n'applique que les réglages
        cosmétiques (raccourcis, volume) SANS reconnexion. """
        old = {"web_url": self.web_url, "app_secret": self.app_secret,
               "counter_id": self.counter_id}
        # Le staff était-il connecté sur l'ANCIEN comptoir ? Si oui, un changement
        # de serveur/comptoir doit d'abord libérer cet ancien comptoir (sinon il
        # reste marqué occupé côté serveur).
        old_staff_present = isinstance(self.staff_id, int) and bool(self.staff_id)
        # Réglages affectant la disposition : un changement impose de reconstruire
        # l'interface comptoir pour prendre effet immédiatement (orientation, mode
        # compact, épaisseur, liste des patients).
        old_layout = (self.horizontal_mode, self.compact_mode, self.panel_thickness,
                      self.display_patient_list, self.patient_list_position_vertical,
                      self.patient_list_position_horizontal)
        old_on_top = getattr(self, "always_on_top", False)
        self.load_preferences()

        # « Toujours au premier plan » : appliqué ICI (plus dans le dialogue, qui ne
        # touche plus la fenêtre parente — point 7). setWindowFlag peut masquer la
        # fenêtre, on ne la manipule donc que si le réglage a changé.
        if self.always_on_top != old_on_top:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, self.always_on_top)
            if self.isVisible():
                self.show()
        new = {"web_url": self.web_url, "app_secret": self.app_secret,
               "counter_id": self.counter_id}
        new_layout = (self.horizontal_mode, self.compact_mode, self.panel_thickness,
                      self.display_patient_list, self.patient_list_position_vertical,
                      self.patient_list_position_horizontal)

        # Réglages cosmétiques : toujours appliqués, aucune reconnexion requise.
        self.setup_global_shortcut()
        if hasattr(self, "audio_player"):
            self.audio_player.set_volume(self.sound_volume)
        # Taille de police de la file : le modèle persiste entre deux
        # reconstructions d'interface, on l'applique donc explicitement (point 28).
        if hasattr(self, "patient_model"):
            self.patient_model.set_font_size(self.patient_list_font_size)

        if needs_service_reconnect(old, new):
            self.logger.info("Serveur/secret/comptoir modifiés : reconnexion des services.")
            self._reconnect_services(old, old_staff_present)
            return

        # Pas de reconnexion : on applique à chaud les changements de disposition.
        staff_present = isinstance(self.staff_id, int) and bool(self.staff_id)
        if old_layout != new_layout and staff_present:
            self.create_interface()
            self.load_skin()
        # Applique (ou retire) la forme de panneau compact. Utile aussi sur l'écran
        # de connexion : la fenêtre prend/quitte la forme d'un panneau docké.
        if self.compact_mode and self.isVisible():
            self.placement.apply_panel_mode()

    def _reconnect_services(self, old_config, old_staff_present):
        """ Reconnexion complète après changement de serveur/secret/comptoir, dans
        un ordre strict pour ne jamais mélanger ancien et nouveau serveur :

          1. arrêt propre de l'ancien WebSocket ;
          2. libération de l'ANCIEN comptoir (avec l'ancien jeton, encore valide) ;
          3. invalidation du jeton + de la session réseau + de l'état local ;
          4. (les préférences sont déjà enregistrées) ;
          5. nouveau jeton + snapshot du nouveau comptoir (en arrière-plan) ;
          6/7. reconnexion HTTP/WebSocket + reconstruction UI -> _on_reconnect_ready.

        ``old_config`` porte l'ancien web_url/counter_id ; ``old_staff_present``
        indique si un staff occupait l'ancien comptoir (à libérer). """
        # 1. Fermer l'ancien WebSocket : plus aucun évènement de l'ancien comptoir.
        if getattr(self, "socket_io_client", None):
            self.socket_io_client.stop(timeout_ms=3000)
            self.socket_io_client = None

        # 2. Libérer l'ancien comptoir AVANT d'invalider le jeton (le jeton courant
        #    vaut pour l'ancien serveur). Best-effort borné : on n'empêche pas la
        #    reconnexion si l'ancien serveur ne répond pas.
        if old_staff_present:
            self._release_counter_blocking(
                url=old_config.get("web_url"), counter_id=old_config.get("counter_id"))

        # 3. Nettoyer jeton + session (le nouveau serveur/secret est déjà chargé).
        #    Le staff de l'ancien comptoir ne vaut pas sur le nouveau : on repart
        #    déconnecté (l'utilisateur se ré-identifiera sur le nouveau comptoir).
        self.app_token = None
        self.staff_id = None
        if hasattr(self, "api"):
            self.api.clear_token()

        # Repartir d'un état local vierge (rien de l'ancien comptoir).
        self.queue_revision = -1
        self.my_patient = None
        self.list_patients = []
        self.socket_was_disconnected = False

        # 5. Nouveau jeton + snapshot en arrière-plan (même séquence qu'au
        #    démarrage, mais la suite reprend la connexion plutôt que l'init).
        self.session.start_startup(self._on_reconnect_ready)

    def _on_reconnect_ready(self, connected, state):
        """ Fin de la reconnexion : applique le snapshot du nouveau comptoir,
        reconstruit l'interface comptoir et rouvre le WebSocket (sans re-créer
        systray/audio, déjà en place). Si le nouveau serveur est inexploitable
        (jeton non obtenu), on en informe explicitement l'utilisateur et on
        propose un redémarrage plutôt que de laisser croire à un succès. """
        self.connected = connected
        if connected and state:
            self._apply_state(state)
        else:
            self.my_patient = None
            self.list_patients = []

        # create_interface() supprime l'ancien widget central -> reconstruction propre.
        self.create_interface()
        self.load_skin()
        if self.compact_mode and self.isVisible():
            self.placement.apply_panel_mode()
        self.setup_user()
        # Le WebSocket est relancé sur le NOUVEau serveur (nouvelle URL) dans tous
        # les cas : s'il est momentanément injoignable, la boucle de reconnexion
        # rattrapera dès qu'il répond.
        self.start_socket_io_client(self.web_url)
        self.alert_if_not_connected()

        if not connected:
            self._warn_reconnect_failed()

    def _warn_reconnect_failed(self):
        """ Avertit que la nouvelle connexion a échoué (jeton non obtenu) et propose
        un redémarrage. On ne prétend pas que le changement est « appliqué » alors
        que le serveur est inexploitable (point 8). """
        self.logger.error(
            "Reconnexion échouée après changement de configuration (serveur %s injoignable ou secret refusé).",
            self.web_url)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Connexion au serveur impossible")
        box.setText(
            "Les nouveaux paramètres ont été enregistrés mais la connexion au "
            "serveur a échoué (serveur injoignable ou secret refusé).\n\n"
            "Vérifiez l'adresse et le secret dans les préférences, puis redémarrez "
            "l'application.")
        open_prefs = box.addButton("Ouvrir les préférences", QMessageBox.AcceptRole)
        box.addButton("Fermer", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_prefs:
            self.show_preferences_dialog()

    def init_audio(self):
        """ Prépare les sons de l'application (chargés une fois, rejoués ensuite
        par leur nom : « ding », « patient_taken », « please_validate »). """
        self.audio_player = build_audio_player(self, self.sound_volume)

    def closeEvent(self, event):
        # Arrêt propre, ordonné et BORNÉ dans le temps. Chaque étape a un délai
        # maximal : la fermeture est toujours acceptée in fine (l'app ne reste
        # jamais bloquée), tout en libérant le comptoir côté serveur et en
        # arrêtant explicitement WebSocket, workers, réseau, timers et raccourcis.
        if self.shutting_down:
            event.accept()
            return
        # Mémorise taille/position/moniteur AVANT de marquer l'arrêt (la fenêtre
        # est encore valide et affichée).
        try:
            self.placement.save()
        except Exception as e:
            self.logger.debug("Sauvegarde de la géométrie à la fermeture : %s", e)

        self.shutting_down = True
        self.logger.info("Fermeture de l'App : arrêt propre en cours")

        # 1. Plus aucune nouvelle action déclenchée par les raccourcis clavier
        #    (attente bornée de l'enregistrement en cours puis retrait des hooks).
        if hasattr(self, "shortcuts"):
            self.shortcuts.shutdown()

        # 2. Arrêt des timers.
        if hasattr(self, 'call_timer'):
            self.call_timer.stop()

        # 3. Arrêt du WebSocket (drapeau + disconnect + attente bornée). Empêche
        #    aussi le déclenchement de nouveaux ResyncWorker.
        if getattr(self, 'socket_io_client', None):
            self.socket_io_client.stop(timeout_ms=3000)

        # 4. Libération du comptoir côté serveur : déconnexion HTTP bornée.
        self._release_counter_blocking()

        # 5. Arrêt du gestionnaire réseau (worker unique) : purge la file et
        #    débloque les appels bloquants éventuels des workers.
        if hasattr(self, 'api'):
            self.api.stop(timeout_ms=3000)

        # 6. Attente bornée des workers encore actifs (StartupWorker/ResyncWorker),
        #    désormais débloqués, avant destruction -> pas de "QThread: Destroyed".
        self.session.wait_active_workers(total_timeout_ms=2000)

        # 7. Fenêtre de chargement.
        if self.loading_screen:
            self.loading_screen.close()

        event.accept()
        super().closeEvent(event)

    def _release_counter_blocking(self, url=None, counter_id=None):
        """ Envoie la déconnexion du comptoir (remove_staff) et attend au plus
        quelques secondes (timeout HTTP court + attente courte). Bornée : si le
        serveur ne répond pas, on continue.

        ``url``/``counter_id`` permettent de libérer un ANCIEN comptoir (autre
        serveur/numéro) lors d'un changement de connexion — sinon on utilise le
        serveur/comptoir courants (fermeture de l'application). La libération doit
        se faire AVANT d'invalider le jeton (le jeton courant vaut pour l'ancien
        serveur). """
        if not hasattr(self, 'api'):
            return
        self.api.release_counter_blocking(url=url, counter_id=counter_id)


    # Note : l'ancien couple connexion_for_app_init()/handle_init_app() (requête
    # /app/counter/init_app pour autocalling + papier + activités staff +
    # nom du comptoir) a été supprimé : ces informations sont désormais fournies
    # de façon atomique par /api/counter/<id>/state via _apply_state().

    def refresh_patient_lists(self):
        """Rafraîchit l'affichage de la file après un changement.

        La vue (modèle) est mise à jour de façon différentielle et le compteur du
        bouton « Patients » est réactualisé. Les menus déroulants (bouton
        « Patients » et systray) NE sont PAS reconstruits ici : ils le sont
        paresseusement à leur ouverture (``aboutToShow``) et lisent
        ``self.list_patients``. Inutile donc de les reconstruire à chaque
        évènement de file alors qu'ils sont fermés la plupart du temps."""
        self._update_patient_count_label()
        self.update_patient_widget()

    def _update_patient_count_label(self):
        """Met à jour le libellé du bouton « Patients (N) » (toujours visible)."""
        if not hasattr(self, 'btn_choose_patient'):
            return
        count = len(self.list_patients or [])
        self.btn_choose_patient.setText(f"Patient{'s' if count > 1 else ''} ({count})")

    def _rebuild_choose_patient_menu(self):
        """Reconstruit le menu du bouton « Patients » (appelé à son ouverture)."""
        self.choose_patient_menu.clear()
        try:
            for patient in (self.list_patients or []):
                language = f" ({patient['language_code']}) ".upper() if patient["language_code"] != "fr" else ""
                action_select_patient = QAction(f"{patient['call_number']} {language}- {patient['activity']}", self)
                action_select_patient.triggered.connect(lambda checked, p=patient: self.select_patient(p['id']))
                self.choose_patient_menu.addAction(action_select_patient)
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
                    self._request_resync()
                    return
            # Établit (si pas encore de référence) ou avance la révision connue.
            self.queue_revision = revision

        # mise à jour de self.patient
        self.list_patients = patient
        self.refresh_patient_lists()


    def update_patient_widget(self):
        """Met la vue de la file à jour via son modèle, de façon différentielle :
        seuls les patients ajoutés/retirés/modifiés provoquent un changement (plus
        de suppression/recréation de tous les boutons, plus de clignotement ni de
        perte de la position de défilement)."""
        if not hasattr(self, 'patient_model'):
            return
        self.patient_model.set_staff_id(self.staff_id)
        self.patient_model.set_patients(self.list_patients or [])

    def _ensure_notification_manager(self):
        """Crée (au besoin) et retourne le gestionnaire de notifications, qui
        centralise écran cible, coin, déduplication et file d'attente."""
        if getattr(self, "notification_manager", None) is None:
            self.notification_manager = NotificationManager(self)
        return self.notification_manager

    def show_notification(self, data, internal=False, font_size=None, force=False):
        # `force` = afficher même si les notifications sont désactivées (bouton de
        # test des préférences).
        if force or self.notification_specific_acts:
            self._ensure_notification_manager().notify(
                data, internal=internal, font_size=font_size)

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



    def load_skin(self):
        """ Applique le skin choisi. Le fichier est localisé par ``resources``
        (chemin absolu) : le skin s'applique donc aussi dans un build PyInstaller
        onefile et quel que soit le répertoire de lancement. """
        if not self.selected_skin:
            return
        qss = resources.read_skin(self.selected_skin)
        if qss is None:
            self.logger.warning("Skin '%s' introuvable ou illisible : style par défaut conservé",
                                self.selected_skin)
            return
        self.setStyleSheet(qss)
        # Appliquer le style à toute l'application
        QApplication.instance().setStyleSheet(qss)


    def cleanup_systray(self):
        """ Fermeture de l'application : arrêt du worker réseau puis retrait des
        icônes de la zone de notification (sinon elles peuvent survivre au
        processus jusqu'au prochain survol de la souris). """
        if hasattr(self, 'api'):
            self.api.stop()
        self.tray.cleanup()

    def alert_if_not_connected(self):
        """ Affiche une alerte si le serveur n'est pas accessible"""
        if not self.connected:
            self.show_notification({"origin": "connection", "message": "Le serveur est inaccessible."}, internal=True)

    def call_timer_delay_expired(self):
        self._set_validate_alert(True)
        self.show_notification({"origin": "please_validate", "message": "Pensez à valider votre patient afin de vider l'écran d'affichage."}, internal=True)

    def create_call_timer(self):
        """ Permet de définir un timer qui envoye une alerte si le patient n'est pas validé """
        self.call_timer = QTimer(self)
        self.call_timer.setInterval(self.timer_after_calling * 1000)
        self.call_timer.timeout.connect(self.call_timer_delay_expired)
def setup_application(app):
    """ Prépare l'application Qt AVANT toute lecture de configuration : identité
    (dont dépend l'emplacement de QSettings) puis reprise des réglages laissés
    par les versions qui s'annonçaient encore sous l'identité d'exemple. """
    apply_identity(app)
    migrate_legacy_settings(QSettings(), legacy_sources(QSettings))
    return app


if __name__ == "__main__":
    app = setup_application(QApplication(sys.argv))

    # MainWindow.show() est appelé en interne une fois l'initialisation
    # asynchrone terminée (_on_startup_ready), pas ici : l'appeler tout de
    # suite afficherait une fenêtre encore vide pendant le chargement.
    window = MainWindow()
    sys.exit(app.exec())
