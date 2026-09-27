"""E1 — zone secondaire commune en mode compact : onglets natifs de docks.

En mode panneau compact, la file des patients et la messagerie partagent une
SEULE zone à onglets (``QMainWindow.tabifyDockWidget``) : un seul panneau
grandit sous le comptoir, l'autre reste à un onglet de distance — la fenêtre
ne s'allonge plus quand les deux sections sont « ouvertes ». Hors mode compact,
le comportement historique est conservé (deux panneaux simultanés).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QDockWidget, QHBoxLayout, QVBoxLayout, QWidget  # noqa: E402

import main  # noqa: E402
from test_main_window import _make_main_window  # noqa: E402
from test_messaging import FakeSettings, _controller  # noqa: E402


def _patient_dock(window):
    """Dock « file des patients » minimal, rattaché comme dans l'app réelle."""
    dock = QDockWidget("Liste des patients", window)
    dock.setWidget(QWidget(dock))
    dock.setMinimumHeight(100)
    window.patient_list_dock = dock
    window.addDockWidget(Qt.BottomDockWidgetArea, dock)
    return dock


def test_compact_mode_groups_docks_into_one_tabbed_zone():
    window, controller = _controller()
    window.compact_mode = True
    patient_dock = _patient_dock(window)
    controller.set_enabled(True)
    # Les deux docks partagent la même zone à onglets.
    assert controller.dock in window.tabifiedDockWidgets(patient_dock)
    assert patient_dock in window.tabifiedDockWidgets(controller.dock)


def test_extended_mode_keeps_two_independent_docks():
    window, controller = _controller()
    window.compact_mode = False
    patient_dock = _patient_dock(window)
    controller.set_enabled(True)
    assert controller.dock not in window.tabifiedDockWidgets(patient_dock)


def test_messaging_toggle_raises_its_tab_then_hides():
    window, controller = _controller()
    window.compact_mode = True
    _patient_dock(window)
    controller.set_enabled(True)
    window.show()

    controller.toggle()      # ouvre / amène l'onglet « Messages » devant
    assert controller.dock.isVisible()
    controller.toggle()      # onglet courant -> masque
    assert not controller.dock.isVisible()
    window.hide()


def test_patient_list_toggle_raises_its_tab():
    window, controller = _controller()
    window.compact_mode = True
    patient_dock = _patient_dock(window)
    controller.set_enabled(True)
    window.fit_window_to_content = lambda: None
    window.show()
    patient_dock.hide()   # zone secondaire fermée au départ

    main.MainWindow.toggle_patient_list(window)   # affiche + lève son onglet
    assert patient_dock.isVisible()
    main.MainWindow.toggle_patient_list(window)   # onglet courant -> masque
    assert not patient_dock.isVisible()
    window.hide()


def test_tabify_does_not_overwrite_visibility_settings():
    # La réorganisation émet visibilityChanged : ce n'est pas un choix de
    # l'utilisateur, le réglage ne doit pas être réécrit pendant l'arrangement.
    previous = dict(FakeSettings.values)
    try:
        _window, controller = _controller()
        before = FakeSettings.values.get("messaging_dock_visible")
        controller._arranging = True
        controller._visibility_changed(not before)   # signal parasite ignoré
        assert FakeSettings.values.get("messaging_dock_visible") == before
        controller._arranging = False
        controller._visibility_changed(True)
        assert FakeSettings.values.get("messaging_dock_visible") is True
    finally:
        FakeSettings.values.clear()
        FakeSettings.values.update(previous)


def test_user_drag_retabifies_without_recursion():
    # dockLocationChanged émis pendant tabifyDockWidget ré-entrait dans
    # _arrange_with_patient_list : RecursionError en exploitation.
    window, controller = _controller()
    window.compact_mode = True
    patient_dock = _patient_dock(window)
    controller.set_enabled(True)
    assert controller.dock in window.tabifiedDockWidgets(patient_dock)

    # L'utilisateur tire l'onglet « Messages » à droite…
    window.addDockWidget(Qt.RightDockWidgetArea, controller.dock)
    assert controller.dock not in window.tabifiedDockWidgets(patient_dock)
    assert FakeSettings.values.get("messaging_dock_area") == "right"

    # …puis le redépose sur la zone de la file : il redevient un onglet,
    # sans récursion sur dockLocationChanged (ré-arrangement différé au
    # tour de boucle suivant — dockWidgetArea n'est pas encore à jour
    # quand le signal part).
    from PySide6.QtWidgets import QApplication
    window.addDockWidget(Qt.BottomDockWidgetArea, controller.dock)
    QApplication.processEvents()
    assert controller.dock in window.tabifiedDockWidgets(patient_dock)
    assert FakeSettings.values.get("messaging_dock_area") == "bottom"


def test_option_buttons_share_one_row_in_compact_vertical():
    # Compact + vertical : « Patients » et « Menu » tiennent sur UNE ligne,
    # la colonne reste courte.
    win = _make_main_window(compact_mode=True)
    try:
        assert isinstance(win.option_button_layout, QHBoxLayout)
    finally:
        win.deleteLater()


def test_option_buttons_stay_stacked_in_extended_vertical():
    win = _make_main_window(compact_mode=False)
    try:
        assert isinstance(win.option_button_layout, QVBoxLayout)
    finally:
        win.deleteLater()
