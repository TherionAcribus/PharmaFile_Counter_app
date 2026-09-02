"""Icônes de la barre d'état système (point 10.11).

Le comptoir pose TROIS icônes distinctes dans la zone de notification, chacune
étant un raccourci d'action à la souris sans passer par la fenêtre :

* « Pause » — met le patient courant en pause ;
* « Prochain patient » — valide et appelle le suivant ; son menu contextuel
  liste la file et permet d'appeler un patient précis ;
* « Valider patient » — valide le patient courant.

Le menu « Prochain patient » est reconstruit à son OUVERTURE (``aboutToShow``) et
non à chaque évènement de file : c'est ce qui évite de reconstruire un menu
invisible des dizaines de fois par heure.

La fenêtre garde les actions (``call_web_function_pause``…) ; ce module ne
s'occupe que des icônes, de leurs menus et de leur destruction propre.
"""

import logging

from PySide6.QtCore import QObject
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from resources import resource_path


class TrayManager(QObject):
    """Les trois icônes de la zone de notification et leurs menus."""

    def __init__(self, window, logger=None):
        super().__init__(window)
        self.window = window
        self.logger = logger or logging.getLogger("appcomptoir.tray")
        self.icons = []
        self.patient_menu = None

    # --- construction -------------------------------------------------------

    def setup(self):
        """Crée les trois icônes. Idempotent : un appel supplémentaire remplace
        les icônes existantes plutôt que de les empiler."""
        self.logger.info("Création du Systray...")
        self.cleanup()

        self._add_icon("assets/images/pause.ico", "Pause",
                       action=self.window.call_web_function_pause,
                       menu_label="Mettre le patient en pause")

        # Icône « Prochain patient » : menu persistant reconstruit à l'ouverture.
        self.patient_menu = QMenu()
        self.patient_menu.aboutToShow.connect(self.rebuild_patient_menu)
        self._add_icon("assets/images/next_orange.ico", "Prochain patient",
                       action=self.window.call_web_function_validate_and_call_next,
                       menu=self.patient_menu)

        self._add_icon("assets/images/check.ico", "Valider patient",
                       action=self.window.call_web_function_validate,
                       menu_label="Valider le patient")

    def _add_icon(self, icon_path, tooltip, action, menu=None, menu_label=None):
        icon = QSystemTrayIcon(QIcon(resource_path(icon_path)), self.window)
        icon.setToolTip(tooltip)
        if menu is None:
            menu = QMenu()
            menu.addAction(menu_label or tooltip).triggered.connect(action)
            # Le menu appartient à l'icône : sans cette référence, il serait
            # ramassé par le garbage collector et le clic droit n'afficherait rien.
            icon.menu_ref = menu
        icon.setContextMenu(menu)
        # Clic gauche (Trigger) = action directe ; clic droit = menu contextuel,
        # géré par Qt.
        icon.activated.connect(
            lambda reason, act=action: self._on_activated(reason, act))
        icon.setVisible(True)
        icon.show()
        self.icons.append(icon)
        return icon

    @staticmethod
    def _on_activated(reason, action):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            action()

    # --- menu de la file ----------------------------------------------------

    def rebuild_patient_menu(self):
        """Reconstruit le menu contextuel du systray « Prochain patient » (appelé
        à son ouverture via aboutToShow)."""
        menu = self.patient_menu
        if menu is None:
            return
        menu.clear()
        for patient in (self.window.list_patients or []):
            try:
                action_text = f"{patient['call_number']} - {patient['activity']}"
            except (KeyError, TypeError):
                continue
            action = menu.addAction(action_text)
            action.triggered.connect(
                lambda checked, p=patient: self.window.select_patient(p['id']))

    # --- destruction --------------------------------------------------------

    def cleanup(self):
        """Retire les icônes (fermeture de l'application). Sans cela, elles
        peuvent survivre au processus jusqu'à ce que l'utilisateur survole la
        zone de notification."""
        for icon in self.icons:
            try:
                icon.setVisible(False)
                icon.deleteLater()
            except RuntimeError:
                pass   # déjà détruite côté Qt
        self.icons = []
        self.patient_menu = None
