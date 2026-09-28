"""Disposition des panneaux secondaires : file des patients + messagerie.

Deux docks partagent la fenêtre sous (ou à droite de) la partie comptoir. Quand
ils sont dans la même zone, ils sont soit EMPILÉS (l'un au-dessus de l'autre,
dans un ordre choisi), soit REGROUPÉS EN ONGLETS.

Principe : l'utilisateur décide, l'application retient. Un glisser-déposer
n'est JAMAIS corrigé de force — on se contente d'observer le résultat une fois
le dépôt terminé (animation Qt comprise) et de le mémoriser. Auparavant, chaque
dépôt déclenchait un réarrangement programmatique (ré-onglettage en compact,
ré-empilement « messagerie en haut » sinon) pendant que Qt posait encore le
panneau : la disposition était annulée et les contenus finissaient
chevauchés dans une même zone.

La disposition mémorisée n'est appliquée qu'aux moments où c'est l'application
qui place les panneaux : création de la messagerie, reconstruction de
l'interface, changement de préférence ou choix explicite dans le menu
« Disposition des panneaux ».

D'un démarrage à l'autre, l'état complet des panneaux (``saveState`` de Qt :
onglet au premier plan, panneau détaché et sa position, côte à côte…) est
aussi conservé, séparément pour chaque mode d'affichage (compact/étendu ×
vertical/horizontal) puisque la fenêtre n'y a pas la même forme.

Le calcul (quelle disposition, quel ordre) est pur et testé sans Qt ;
``DockArranger`` en est l'intégration QMainWindow.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication
from PySide6.QtWidgets import QMainWindow, QMenu, QTabWidget

#: Clé QSettings de la disposition choisie.
LAYOUT_KEY = "secondary_docks_layout"

AUTO = "auto"
TABS = "tabs"
STACKED_MESSAGING_FIRST = "stacked_messaging_first"
STACKED_PATIENTS_FIRST = "stacked_patients_first"
LAYOUTS = (AUTO, TABS, STACKED_MESSAGING_FIRST, STACKED_PATIENTS_FIRST)

#: Libellés du menu « Disposition des panneaux », dans l'ordre d'affichage.
LAYOUT_LABELS = (
    (AUTO, "Automatique (onglets en mode compact)"),
    (STACKED_MESSAGING_FIRST, "Empilés — messagerie en haut"),
    (STACKED_PATIENTS_FIRST, "Empilés — liste des patients en haut"),
    (TABS, "Regroupés en onglets"),
)

#: Délai entre la fin d'un glisser-déposer et la lecture de la disposition :
#: laisse l'animation de dépôt de Qt se terminer (ms).
CAPTURE_DELAY_MS = 350

#: Hauteurs de départ quand les panneaux sont empilés (px).
STACK_HEIGHTS = {"messaging": 180, "patients": 110}

#: Préfixe QSettings de l'état Qt des panneaux, suivi du mode d'affichage.
STATE_KEY_PREFIX = "secondary_docks_state/"
#: Version passée à saveState/restoreState : l'incrémenter invalide les états
#: enregistrés si la structure des panneaux change.
STATE_VERSION = 1


# --- cœur pur -----------------------------------------------------------------

def normalize_layout(value) -> str:
    """Valeur mémorisée → disposition connue (repli : automatique)."""
    return value if value in LAYOUTS else AUTO


def effective_layout(stored, compact: bool) -> str:
    """Disposition à appliquer. « Automatique » garde le comportement
    historique : onglets en mode compact (encombrement vertical borné),
    empilés messagerie en haut sinon."""
    stored = normalize_layout(stored)
    if stored != AUTO:
        return stored
    return TABS if compact else STACKED_MESSAGING_FIRST


def observed_layout(tabbed: bool, messaging_pos, patients_pos) -> str:
    """Disposition constatée après un dépôt de l'utilisateur.

    ``*_pos`` sont les coins haut-gauche ``(x, y)`` des deux panneaux ; le
    premier dans l'ordre de lecture (haut puis gauche) est considéré en tête —
    côte à côte, celui de gauche."""
    if tabbed:
        return TABS
    m_x, m_y = messaging_pos
    p_x, p_y = patients_pos
    if (m_y, m_x) <= (p_y, p_x):
        return STACKED_MESSAGING_FIRST
    return STACKED_PATIENTS_FIRST


def mode_key(compact: bool, horizontal: bool) -> str:
    """Mode d'affichage → suffixe de clé de l'état mémorisé."""
    return ("compact" if compact else "extended") + "-" + (
        "horizontal" if horizontal else "vertical")


def area_name(area) -> str | None:
    """Zone Qt → valeur de préférence (« bottom »/« right »), None sinon."""
    if area == Qt.DockWidgetArea.BottomDockWidgetArea:
        return "bottom"
    if area == Qt.DockWidgetArea.RightDockWidgetArea:
        return "right"
    return None


# --- intégration Qt -----------------------------------------------------------

def configure_main_window(window) -> None:
    """Options de docks de la fenêtre : empilement libre (y compris dans une
    zone déjà occupée), onglets, animation ; barre d'onglets en haut du
    groupe pour qu'elle reste lisible sous la partie comptoir."""
    window.setDockOptions(
        QMainWindow.DockOption.AnimatedDocks
        | QMainWindow.DockOption.AllowNestedDocks
        | QMainWindow.DockOption.AllowTabbedDocks
    )
    window.setTabPosition(Qt.DockWidgetArea.AllDockWidgetAreas, QTabWidget.TabPosition.North)


def dock_in_front(dock) -> bool:
    """Vrai si le panneau est réellement lisible : affiché ET, s'il est dans
    un groupe d'onglets, sur l'onglet courant."""
    return dock.isVisible() and not dock.visibleRegion().isEmpty()


def toggle_dock(dock) -> bool:
    """Bascule d'affichage d'un panneau, cohérente avec les onglets.

    Masqué → affiché au premier plan ; affiché mais derrière un autre onglet →
    amené devant (le masquer surprendrait : on ne le voyait pas) ; au premier
    plan → masqué. Retourne l'état affiché final."""
    if dock.isHidden():
        dock.show()
        dock.raise_()
        return True
    if dock.isVisible() and not dock_in_front(dock):
        dock.raise_()
        return True
    if not dock.isVisible():
        # Fenêtre principale masquée (zone de notification) : on ne masque
        # pas un panneau qu'on ne pouvait pas voir.
        dock.raise_()
        return True
    dock.hide()
    return False


class DockArranger(QObject):
    """Applique et mémorise la disposition de la file des patients et de la
    messagerie. ``busy`` est vrai pendant NOS déplacements : les signaux Qt
    émis alors (visibilité, zone) ne sont pas des choix de l'utilisateur."""

    def __init__(self, window, settings_factory):
        super().__init__(window)
        self.window = window
        self._settings_factory = settings_factory
        self.busy = False
        self._tracked = []
        self._menu = None
        self._actions = {}
        self._capture_timer = QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.setInterval(CAPTURE_DELAY_MS)
        self._capture_timer.timeout.connect(self.capture)
        # Mode d'affichage dont l'état Qt est actuellement en place (None :
        # rien restauré pour les panneaux courants).
        self._mode_key = None

    # --- accès ------------------------------------------------------------

    def _docks(self):
        """(file des patients, messagerie) ; None pour un panneau absent ou
        déjà détruit par une reconstruction."""
        patients = getattr(self.window, "patient_list_dock", None)
        messaging = getattr(getattr(self.window, "messaging", None), "dock", None)
        return (patients if patients is not None and _alive(patients) else None,
                messaging if messaging is not None and _alive(messaging) else None)

    def stored_layout(self) -> str:
        return normalize_layout(
            self._settings_factory().value(LAYOUT_KEY, AUTO, type=str))

    def current_layout(self) -> str:
        return effective_layout(
            self.stored_layout(), getattr(self.window, "compact_mode", False))

    def _current_mode_key(self) -> str:
        return mode_key(getattr(self.window, "compact_mode", False),
                        getattr(self.window, "horizontal_mode", False))

    # --- état conservé d'un démarrage à l'autre ------------------------------

    def save_state(self) -> None:
        """Enregistre l'état Qt des deux panneaux pour le mode en place.
        Sans les deux panneaux (écran de connexion, messagerie désactivée),
        rien n'est écrit : on garderait un état amputé de la messagerie."""
        patients, messaging = self._docks()
        if self.busy or patients is None or messaging is None:
            return
        key = self._mode_key or self._current_mode_key()
        self._settings_factory().setValue(
            STATE_KEY_PREFIX + key, self.window.saveState(STATE_VERSION))

    def forget_docks(self) -> None:
        """La messagerie va être détruite (déconnexion, désactivation) :
        enregistre son état, puis prépare une restauration pour le prochain
        panneau créé."""
        self.save_state()
        self._mode_key = None

    def _restore_state(self, patients, messaging) -> bool:
        data = self._settings_factory().value(
            STATE_KEY_PREFIX + self._current_mode_key())
        if not data:
            return False
        try:
            restored = self.window.restoreState(QByteArray(data), STATE_VERSION)
        except (TypeError, ValueError):
            restored = False
        if not restored:
            return False
        # L'état Qt contient aussi l'affichage des panneaux ; c'est pourtant
        # aux réglages dédiés d'en décider (la file a pu être masquée par
        # l'écran de connexion au moment de l'enregistrement).
        patients.setVisible(bool(getattr(self.window, "display_patient_list", True)))
        messaging.setVisible(bool(self._settings_factory().value(
            "messaging_dock_visible", False, type=bool)))
        # Un panneau détaché resté sur un écran débranché depuis : on le
        # re-docke plutôt que de le perdre hors de vue.
        for dock in (patients, messaging):
            if dock.isFloating() and not _on_a_screen(dock):
                dock.setFloating(False)
        return True

    # --- suivi des dépôts utilisateur --------------------------------------

    def track(self, dock) -> None:
        """Observe les dépôts de ``dock`` (idempotent)."""
        if dock is None or any(d is dock for d in self._tracked):
            return
        self._tracked = [d for d in self._tracked if _alive(d)] + [dock]
        dock.dockLocationChanged.connect(self._schedule_capture)
        dock.topLevelChanged.connect(self._schedule_capture)
        # Onglet passé au premier plan, ouverture/fermeture : l'état
        # enregistré suit.
        dock.visibilityChanged.connect(self._schedule_capture)
        dock.destroyed.connect(self._forget_destroyed)

    def _forget_destroyed(self, *_args):
        self._tracked = [d for d in self._tracked if _alive(d)]

    def _schedule_capture(self, *_args):
        if not self.busy:
            self._capture_timer.start()

    def capture(self) -> None:
        """Mémorise la disposition laissée par l'utilisateur, SANS rien
        déplacer : zones de chaque panneau et, s'ils partagent une zone,
        onglets ou ordre d'empilement."""
        if self.busy:
            return
        window = self.window
        patients, messaging = self._docks()
        settings = self._settings_factory()
        if messaging is not None and not messaging.isFloating():
            name = area_name(window.dockWidgetArea(messaging))
            if name:
                settings.setValue("messaging_dock_area", name)
        if patients is not None and not patients.isFloating():
            name = area_name(window.dockWidgetArea(patients))
            if name:
                horizontal = getattr(window, "horizontal_mode", False)
                key = ("patient_list_horizontal_position" if horizontal
                       else "patient_list_vertical_position")
                attr = ("patient_list_position_horizontal" if horizontal
                        else "patient_list_position_vertical")
                settings.setValue(key, name)
                # Une reconstruction d'interface ne doit pas ramener la file
                # à son ancienne place.
                setattr(window, attr, name)
        if patients is None or messaging is None:
            return
        area = window.dockWidgetArea(messaging)
        if not (patients.isFloating() or messaging.isFloating()
                or patients.isHidden() or messaging.isHidden()
                or area == Qt.DockWidgetArea.NoDockWidgetArea
                or area != window.dockWidgetArea(patients)):
            tabbed = patients in window.tabifiedDockWidgets(messaging)
            observed = observed_layout(
                tabbed,
                messaging.geometry().topLeft().toTuple(),
                patients.geometry().topLeft().toTuple(),
            )
            # Si l'observation correspond déjà à la disposition
            # « automatique » du mode courant, on laisse le réglage
            # automatique en place.
            if observed != self.current_layout():
                settings.setValue(LAYOUT_KEY, observed)
            self._sync_menu()
        self.save_state()

    # --- application ------------------------------------------------------

    def set_layout(self, layout) -> None:
        """Choix explicite (menu) : mémorise puis applique."""
        self._settings_factory().setValue(LAYOUT_KEY, normalize_layout(layout))
        self.apply()
        self._sync_menu()
        self.save_state()
        fit =getattr(self.window, "fit_window_to_content", None)
        if callable(fit):
            QTimer.singleShot(0, fit)

    def apply(self) -> None:
        """Place les deux panneaux selon la disposition mémorisée, s'ils sont
        dans la même zone. Des panneaux dans des zones différentes ou flottants
        sont laissés tels quels : c'est un choix de l'utilisateur."""
        patients, messaging = self._docks()
        self.track(patients)
        self.track(messaging)
        if patients is None or messaging is None:
            return
        window = self.window
        mode = self._current_mode_key()
        if mode != self._mode_key:
            # Premier placement de ces panneaux, ou changement de mode
            # d'affichage : l'état du mode quitté est enregistré, celui du
            # mode courant (s'il existe) restauré tel que laissé.
            if self._mode_key is not None:
                self.save_state()
            self._mode_key = mode
            self.busy = True
            try:
                restored = self._restore_state(patients, messaging)
            finally:
                self.busy = False
            if restored:
                return
        if patients.isFloating() or messaging.isFloating():
            return
        area = window.dockWidgetArea(messaging)
        if area == Qt.DockWidgetArea.NoDockWidgetArea or area != window.dockWidgetArea(patients):
            return
        layout = self.current_layout()
        tabbed = patients in window.tabifiedDockWidgets(messaging)
        self.busy = True
        # tabifyDockWidget/splitDockWidget ignorent les panneaux explicitement
        # masqués : on les affiche le temps du placement, puis on restaure
        # l'état choisi par l'utilisateur.
        hidden = [d for d in (patients, messaging) if d.isHidden()]
        try:
            for dock in hidden:
                dock.show()
            if layout == TABS:
                if not tabbed:
                    front = messaging if dock_in_front(messaging) else patients
                    window.tabifyDockWidget(patients, messaging)
                    front.raise_()
            else:
                first, second = ((messaging, patients)
                                 if layout == STACKED_MESSAGING_FIRST
                                 else (patients, messaging))
                # Les positions ne sont fiables qu'une fois tout affiché : avant
                # le premier affichage, les deux panneaux sont en (0, 0) et
                # sembleraient déjà « dans l'ordre ». Un côte à côte laissé par
                # l'utilisateur (tête à gauche) est respecté.
                laid_out = (window.isVisible() and not hidden
                            and patients.isVisible() and messaging.isVisible())
                in_order = (not tabbed and laid_out and observed_layout(
                    False, messaging.geometry().topLeft().toTuple(),
                    patients.geometry().topLeft().toTuple()) == layout)
                if not in_order:
                    # Ré-ajouter le second le sort d'un éventuel groupe
                    # d'onglets ; le découpage le pose sous le premier.
                    window.addDockWidget(area, second)
                    window.splitDockWidget(first, second, Qt.Orientation.Vertical)
                    heights = [STACK_HEIGHTS["messaging" if d is messaging
                                             else "patients"]
                               for d in (first, second)]
                    window.resizeDocks([first, second], heights, Qt.Orientation.Vertical)
            for dock in hidden:
                dock.hide()
        finally:
            self.busy = False

    # --- menu « Disposition des panneaux » ----------------------------------

    def install_menu(self, parent_menu) -> QMenu:
        """Ajoute le sous-menu de disposition à ``parent_menu``."""
        menu = QMenu("Disposition des panneaux", parent_menu)
        group = QActionGroup(menu)
        group.setExclusive(True)
        self._actions = {}
        for value, label in LAYOUT_LABELS:
            action = QAction(label, menu)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, v=value: self.set_layout(v))
            group.addAction(action)
            menu.addAction(action)
            self._actions[value] = action
        menu.aboutToShow.connect(self._sync_menu)
        parent_menu.addMenu(menu)
        self._menu = menu
        self._sync_menu()
        return menu

    def remove_menu(self) -> None:
        if self._menu is not None:
            try:
                self._menu.menuAction().deleteLater()
                self._menu.deleteLater()
            except RuntimeError:
                pass
        self._menu = None
        self._actions = {}

    def _sync_menu(self) -> None:
        stored = self.stored_layout()
        for value, action in list(self._actions.items()):
            try:
                action.setChecked(value == stored)
            except RuntimeError:
                self._actions = {}
                return


def _on_a_screen(dock) -> bool:
    frame = dock.frameGeometry()
    return any(screen.availableGeometry().intersects(frame)
               for screen in QGuiApplication.screens())


def _alive(obj) -> bool:
    try:
        obj.objectName()
        return True
    except RuntimeError:
        return False
