"""Placement de la fenêtre : géométrie persistée, multi-écran, panneau (10.12).

Regroupe ce qui décide OÙ et COMMENT la fenêtre s'affiche :

* mémorisation/restauration de la taille, de la position et du moniteur
  (points 24) ;
* garde-fou multi-écran : si le moniteur d'origine disparaît ou que la fenêtre
  se retrouve hors zone visible, elle est recentrée plutôt que perdue ;
* mode panneau compact : colonne étroite dockée sur un bord (mode vertical) ou
  barre fine en haut de l'écran (mode horizontal) — point 25 ;
* magnétisme aux bords après un déplacement à la souris.

Le CALCUL est fait par les modules purs ``window_geometry`` et ``panel_layout``
(déjà testés sans Qt) ; ce module en est l'intégration : lecture de la
configuration Qt des écrans, application à la fenêtre, persistance QSettings.

Le drapeau ``applying`` marque nos propres repositionnements : sans lui,
``moveEvent`` relancerait le magnétisme en boucle sur nos propres ``move()``.
"""

import logging

from PySide6.QtCore import QSettings, QTimer, Slot
from PySide6.QtGui import QGuiApplication

from panel_layout import (
    DEFAULT_SNAP_THRESHOLD, HORIZONTAL, VERTICAL, compact_panel_geometry,
    nearest_vertical_side, snap_to_edges,
)
from window_geometry import resolve_target_geometry

#: Délai avant magnétisme, après le dernier déplacement de la fenêtre (ms).
SNAP_DELAY_MS = 200


class WindowPlacement:
    """Placement de la fenêtre principale. ``window`` est la QMainWindow."""

    GEOMETRY_KEY = "window_geometry"
    SCREEN_KEY = "window_screen_name"
    DEFAULT_WINDOW_SIZE = (400, 300)  # taille par défaut après réinitialisation

    def __init__(self, window, logger=None):
        self.window = window
        self.logger = logger or logging.getLogger("appcomptoir.placement")
        # Vrai pendant NOS repositionnements (panneau, magnétisme) : moveEvent
        # les ignore, sinon le magnétisme se redéclencherait sur son propre move.
        self.applying = False
        # Magnétisme différé : on aimante peu APRÈS la fin du déplacement, pas à
        # chaque pixel parcouru pendant le glissement.
        self._snap_timer = QTimer(window)
        self._snap_timer.setSingleShot(True)
        self._snap_timer.timeout.connect(self.apply_edge_snap)

    # --- persistance --------------------------------------------------------

    def save(self):
        """Mémorise taille, position (état maximisé inclus) et nom du moniteur.
        Appelé à la fermeture. saveGeometry() encode aussi l'écran/DPI."""
        settings = QSettings()
        settings.setValue(self.GEOMETRY_KEY, self.window.saveGeometry())
        screen = self.window.screen()
        settings.setValue(self.SCREEN_KEY, screen.name() if screen else "")

    def restore(self):
        """Restaure la géométrie mémorisée. La vérification de visibilité est faite
        séparément (ensure_visible) APRÈS show(), quand la géométrie de cadre est
        fiable. Retourne True si une géométrie a été restaurée."""
        settings = QSettings()
        geometry = settings.value(self.GEOMETRY_KEY)
        if not geometry:
            return False  # premier lancement : on laisse Qt placer la fenêtre
        try:
            return bool(self.window.restoreGeometry(geometry))
        except (TypeError, ValueError) as e:
            self.logger.warning("Géométrie enregistrée illisible : %s", e)
            return False

    # --- écrans -------------------------------------------------------------

    def screen_rects(self):
        """Zones utiles (hors barre des tâches) de tous les écrans, en tuples."""
        rects = []
        for screen in QGuiApplication.screens():
            g = screen.availableGeometry()
            rects.append((g.x(), g.y(), g.width(), g.height()))
        return rects

    def current_screen_avail(self):
        """Zone utile de l'écran courant de la fenêtre (ou principal en secours),
        en tuple ``(x, y, w, h)`` ; None si aucun écran."""
        screen = self.window.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return None
        g = screen.availableGeometry()
        return (g.x(), g.y(), g.width(), g.height())

    def _window_frame(self):
        frame = self.window.frameGeometry()
        return (frame.x(), frame.y(), frame.width(), frame.height())

    @Slot()
    def ensure_visible(self):
        """Vérifie que la fenêtre reste dans une zone visible. Si son moniteur
        d'origine a disparu ou qu'elle n'est plus assez visible (déconnexion,
        changement d'écran, résolution réduite), on la recentre sur l'écran
        principal."""
        if self.window.shutting_down or not self.window.isVisible():
            return
        primary = QGuiApplication.primaryScreen()
        if primary is None:
            return
        pa = primary.availableGeometry()
        stored_name = QSettings().value(self.SCREEN_KEY) or None
        target = resolve_target_geometry(
            self._window_frame(),
            self.screen_rects(),
            (pa.x(), pa.y(), pa.width(), pa.height()),
            stored_screen_name=stored_name,
            available_screen_names=[s.name() for s in QGuiApplication.screens()],
        )
        if target is not None:
            self.logger.info("Fenêtre hors zone visible : recentrage sur l'écran principal")
            x, y, w, h = target
            self.window.resize(w, h)
            self.window.move(x, y)

    def reset(self):
        """Commande « Réinitialiser la position » : oublie la géométrie mémorisée
        et recentre la fenêtre (taille par défaut) sur l'écran principal."""
        settings = QSettings()
        settings.remove(self.GEOMETRY_KEY)
        settings.remove(self.SCREEN_KEY)
        if self.window.isMaximized() or self.window.isFullScreen():
            self.window.showNormal()
        primary = QGuiApplication.primaryScreen()
        if primary is None:
            return
        pa = primary.availableGeometry()
        # Taille par défaut raisonnable, bornée à l'écran.
        default_w = min(self.DEFAULT_WINDOW_SIZE[0], pa.width())
        default_h = min(self.DEFAULT_WINDOW_SIZE[1], pa.height())
        x = pa.x() + (pa.width() - default_w) // 2
        y = pa.y() + (pa.height() - default_h) // 2
        self.window.resize(default_w, default_h)
        self.window.move(x, y)
        self.logger.info("Position de la fenêtre réinitialisée (écran principal)")

    # --- mode panneau compact (point 25) ------------------------------------

    def apply_panel_mode(self):
        """Redimensionne/positionne la fenêtre en panneau compact docké.

        En mode vertical, colonne étroite (largeur = épaisseur configurée) sur le
        bord gauche/droit le plus proche de la position actuelle ; en mode
        horizontal, barre fine en haut de l'écran (« au-dessus du progiciel »). La
        fenêtre reste ensuite librement redimensionnable à la souris.

        Sans effet si le mode compact est désactivé."""
        window = self.window
        if not getattr(window, "compact_mode", False):
            return
        avail = self.current_screen_avail()
        if avail is None:
            return
        if window.horizontal_mode:
            # Barre horizontale dockée en haut (zone au-dessus du progiciel).
            target = compact_panel_geometry(HORIZONTAL, avail, window.panel_thickness, "top")
        else:
            # Colonne verticale dockée du côté le plus proche.
            side = nearest_vertical_side(self._window_frame(), avail)
            target = compact_panel_geometry(VERTICAL, avail, window.panel_thickness, side)
        x, y, w, h = target
        self.applying = True
        try:
            if window.isMaximized() or window.isFullScreen():
                window.showNormal()
            window.resize(w, h)
            window.move(x, y)
        finally:
            self.applying = False
        self.logger.debug("Mode panneau appliqué : %s", target)

    # --- magnétisme aux bords -----------------------------------------------

    def on_window_moved(self):
        """Appelé par ``moveEvent`` : (re)démarre le timer de magnétisme si le
        magnétisme aux bords est actif. Ignoré pendant nos propres
        repositionnements pour ne pas boucler."""
        window = self.window
        if (getattr(window, "panel_snap", False)
                and not self.applying
                and not window.shutting_down
                and window.isVisible()):
            self._snap_timer.start(SNAP_DELAY_MS)

    @Slot()
    def apply_edge_snap(self):
        """Aimante la fenêtre au bord d'écran le plus proche (déclenché peu après
        la fin d'un déplacement manuel)."""
        window = self.window
        if not window.panel_snap or window.shutting_down or not window.isVisible():
            return
        avail = self.current_screen_avail()
        if avail is None:
            return
        frame = self._window_frame()
        x, y, _w, _h = snap_to_edges(frame, avail, DEFAULT_SNAP_THRESHOLD)
        if (x, y) != (frame[0], frame[1]):
            self.applying = True
            try:
                window.move(x, y)
            finally:
                self.applying = False
            self.logger.debug("Fenêtre aimantée au bord (%s, %s)", x, y)
