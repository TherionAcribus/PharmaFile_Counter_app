"""Tests d'intégration Qt du placement de fenêtre (point 24).

On utilise le VRAI ``WindowPlacement`` (save/restore/reset) sur une petite
sous-classe de QMainWindow, pour vérifier le round-trip Qt
(saveGeometry/restoreGeometry) et la commande de réinitialisation, sans
instancier toute l'application (réseau, workers…). QApplication est fournie par
conftest ; QSettings est isolé dans un fichier temporaire.
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QMainWindow  # noqa: E402

from window_placement import WindowPlacement  # noqa: E402


class GeoWindow(QMainWindow):
    """Fenêtre minimale équipée du vrai gestionnaire de placement."""

    GEOMETRY_KEY = WindowPlacement.GEOMETRY_KEY
    SCREEN_KEY = WindowPlacement.SCREEN_KEY
    shutting_down = False

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger("test.geo")
        self.placement = WindowPlacement(self, logger=self.logger)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path):
    """Isole QSettings dans un INI temporaire pour ne pas polluer le système."""
    QCoreApplication.setOrganizationName("PharmaFileTest")
    QCoreApplication.setApplicationName("AppComptoirTest")
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    QSettings.setDefaultFormat(QSettings.IniFormat)
    s = QSettings()
    s.clear()
    s.sync()
    yield
    s = QSettings()
    s.clear()
    s.sync()


def test_restore_without_saved_geometry_returns_false(qapp_unused=None):
    win = GeoWindow()
    assert win.placement.restore() is False


def test_save_then_restore_roundtrip():
    win = GeoWindow()
    win.resize(640, 480)
    win.move(50, 60)
    win.show()
    win.placement.save()

    # La clé de géométrie est bien enregistrée.
    assert QSettings().value(GeoWindow.GEOMETRY_KEY) is not None

    # Une nouvelle fenêtre restaure la géométrie mémorisée.
    win2 = GeoWindow()
    assert win2.placement.restore() is True
    win2.show()
    # Taille restaurée (la position exacte dépend du gestionnaire de fenêtres ;
    # on vérifie la taille, fiable en mode offscreen).
    assert win2.size().width() == 640
    assert win2.size().height() == 480


def test_reset_window_position_clears_settings():
    win = GeoWindow()
    win.show()
    win.placement.save()
    assert QSettings().value(GeoWindow.GEOMETRY_KEY) is not None

    win.placement.reset()

    assert QSettings().value(GeoWindow.GEOMETRY_KEY) is None
    assert QSettings().value(GeoWindow.SCREEN_KEY) is None
    assert not win.isMaximized()


def test_ensure_visible_moves_offscreen_window_back():
    win = GeoWindow()
    win.resize(400, 300)
    win.show()
    # Place la fenêtre très loin, hors de tout écran.
    win.move(20000, 20000)
    win.placement.ensure_visible()
    # Après correction, une part significative de la fenêtre est sur un écran.
    from window_geometry import is_window_visible
    frame = win.frameGeometry()
    window = (frame.x(), frame.y(), frame.width(), frame.height())
    assert is_window_visible(window, win.placement.screen_rects())


def test_ensure_visible_leaves_onscreen_window_untouched():
    win = GeoWindow()
    win.resize(400, 300)
    win.show()
    primary = win.placement.screen_rects()[0] if win.placement.screen_rects() else (0, 0, 800, 600)
    win.move(primary[0] + 10, primary[1] + 10)
    pos_before = (win.x(), win.y())
    win.placement.ensure_visible()
    assert (win.x(), win.y()) == pos_before
