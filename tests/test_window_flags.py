"""F4 — « toujours au premier plan » ne doit pas faire disparaître la fenêtre.

``QWidget.setWindowFlag`` masque la fenêtre (les drapeaux sont appliqués en
recréant le widget natif). Le code d'``apply_preferences`` testait
``isVisible()`` APRÈS le changement de drapeau : il obtenait déjà ``False`` et
n'appelait jamais ``show()`` — la fenêtre disparaissait purement dès qu'on
bascule l'option dans les préférences.

La correction mémorise la visibilité AVANT ``setWindowFlag`` et ne réaffiche
que si la fenêtre l'était.
"""

import logging
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QMainWindow  # noqa: E402

import main  # noqa: E402


def _pref_window(on_top=True):
    """Vraie QMainWindow/MainWindow minimale : apply_preferences n'a besoin que
    des attributs de réglages lus AVANT load_preferences — load_preferences
    elle-même est simulée (elle bascule juste always_on_top)."""
    win = main.MainWindow.__new__(main.MainWindow)
    QMainWindow.__init__(win)
    win.logger = logging.getLogger("test.fenetre")
    win.web_url = "http://localhost:5000"
    win.app_secret = "s"
    win.counter_id = 1
    win.staff_id = 1
    win.horizontal_mode = False
    win.compact_mode = False
    win.panel_thickness = 300
    win.display_patient_list = False
    win.patient_list_position_vertical = "bottom"
    win.patient_list_position_horizontal = "right"
    win.always_on_top = on_top
    # moveEvent/resizeEvent délèguent au placement : mocké (pas de magnétisme).
    win.placement = mock.MagicMock()
    # Effets cosmétiques / disposition : sans objet ici.
    win.setup_global_shortcut = lambda: None
    win.fit_window_to_content = lambda dock=False: None
    win.create_interface = lambda: None
    win.load_skin = lambda: None
    return win


def test_on_top_toggle_keeps_window_visible():
    # Régression : une fenêtre visible disparaissait dès le basculement de
    # l'option, setWindowFlag la masquant et isVisible() renvoyant déjà False.
    win = _pref_window(on_top=True)
    try:
        win.show()
        assert win.isVisible()
        win.load_preferences = lambda: setattr(win, "always_on_top", False)
        main.MainWindow.apply_preferences(win)
        assert win.always_on_top is False
        assert win.isVisible()
        assert not (win.windowFlags() & Qt.WindowStaysOnTopHint)
    finally:
        win.deleteLater()


def test_on_top_enable_keeps_window_visible():
    # Même défaut dans l'autre sens (réglage désactivé -> activé).
    win = _pref_window(on_top=False)
    try:
        win.show()
        assert win.isVisible()
        win.load_preferences = lambda: setattr(win, "always_on_top", True)
        main.MainWindow.apply_preferences(win)
        assert win.always_on_top is True
        assert win.isVisible()
        assert win.windowFlags() & Qt.WindowStaysOnTopHint
    finally:
        win.deleteLater()


def test_on_top_toggle_does_not_show_hidden_window():
    # Une fenêtre masquée ne doit PAS être affichée par le changement de
    # drapeau : on restaure l'état d'avant, pas « visible » en dur.
    win = _pref_window(on_top=True)
    try:
        win.load_preferences = lambda: setattr(win, "always_on_top", False)
        main.MainWindow.apply_preferences(win)
        assert not win.isVisible()
    finally:
        win.deleteLater()


def test_on_top_unchanged_does_not_touch_window():
    # Réglage inchangé : aucun changement de drapeau, aucune réapparition.
    win = _pref_window(on_top=True)
    try:
        win.show()
        win.load_preferences = lambda: setattr(win, "always_on_top", True)
        main.MainWindow.apply_preferences(win)
        assert win.isVisible()
    finally:
        win.deleteLater()
