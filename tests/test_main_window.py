"""Tests de MainWindow (point 29) — remplace l'ancien ``test.py`` obsolète.

L'ancien fichier (racine du projet, nommé ``test.py``) n'était pas découvert par
la convention pytest (``test_*.py``) et testait des attributs/méthodes qui
n'existent plus : ``vertical_mode`` (renommé ``horizontal_mode``),
``update_control_buttons_layout`` et ``resize_to_fit_buttons`` (le basculement
d'orientation reconstruit désormais toute l'interface via ``create_interface`` et
redocke le panneau via ``apply_panel_mode``).

On teste les noms actuels :
  - la logique de branchement de ``toggle_orientation`` (fait pivoter
    ``horizontal_mode``, reconstruit l'interface, ne redocke le panneau que si le
    mode compact est actif ET la fenêtre visible) — avec un faux ``self`` où
    ``create_interface`` / ``apply_panel_mode`` sont mockés ;
  - la vraie construction de l'interface par ``create_interface`` sur une vraie
    QMainWindow, en mockant réseau / WebSocket / audio et sans jamais toucher au
    réseau réel (les séquences réseau de démarrage — StartupWorker, jeton, liste
    des patients — ne sont pas déclenchées).

Aucune connexion réseau réelle : le ``NetworkManager`` est un MagicMock et la
séquence de démarrage n'est pas lancée (on n'appelle jamais ``__init__``, qui
ouvrirait un StartupWorker). ``create_interface`` ne fait de toute façon aucune
requête réseau (point 2) — vérifié par ``test_create_interface_does_not_touch_network``.
Fonctionne en Qt « offscreen » (cf. conftest.py).
"""

import logging
import os
import sys
import types
from unittest import mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QVBoxLayout  # noqa: E402

import main  # noqa: E402
from buttons import DebounceButton, IconeButton  # noqa: E402
from main_window_ui import ConnectionStatusIndicator  # noqa: E402
from window_placement import WindowPlacement  # noqa: E402


# --- toggle_orientation : logique de branchement (faux self, mocks) ---------
#
# On appelle la vraie méthode avec un objet minimal : elle ne fait qu'accéder à
# des attributs, donc pas besoin d'instancier une fenêtre. On vérifie ce que
# l'ancien test vérifiait, mais avec les noms actuels.


def _toggle_stub(horizontal_mode=False, compact_mode=False, visible=False):
    return types.SimpleNamespace(
        horizontal_mode=horizontal_mode,
        compact_mode=compact_mode,
        create_interface=mock.MagicMock(),
        # Le dockage du panneau appartient au gestionnaire de placement (10.12).
        placement=mock.MagicMock(),
        isVisible=mock.MagicMock(return_value=visible),
    )


def test_toggle_orientation_flips_horizontal_mode():
    stub = _toggle_stub(horizontal_mode=False)
    main.MainWindow.toggle_orientation(stub)
    assert stub.horizontal_mode is True
    main.MainWindow.toggle_orientation(stub)
    assert stub.horizontal_mode is False


def test_toggle_orientation_rebuilds_interface():
    stub = _toggle_stub()
    main.MainWindow.toggle_orientation(stub)
    stub.create_interface.assert_called_once()


def test_toggle_orientation_redocks_panel_when_compact_and_visible():
    stub = _toggle_stub(compact_mode=True, visible=True)
    main.MainWindow.toggle_orientation(stub)
    stub.placement.apply_panel_mode.assert_called_once()


def test_toggle_orientation_skips_panel_when_not_compact():
    stub = _toggle_stub(compact_mode=False, visible=True)
    main.MainWindow.toggle_orientation(stub)
    stub.placement.apply_panel_mode.assert_not_called()


def test_toggle_orientation_skips_panel_when_not_visible():
    # Fenêtre non affichée (démarrage) : la géométrie de cadre n'est pas fiable,
    # on ne redocke pas encore le panneau même en mode compact.
    stub = _toggle_stub(compact_mode=True, visible=False)
    main.MainWindow.toggle_orientation(stub)
    stub.placement.apply_panel_mode.assert_not_called()


# --- create_interface : vraie construction sur une vraie QMainWindow --------


def _make_main_window(horizontal_mode=False, compact_mode=False):
    """Construit une vraie MainWindow SANS passer par ``__init__`` (qui lancerait
    la séquence réseau de démarrage). On initialise uniquement la base
    QMainWindow (pour que setCentralWidget/addDockWidget fonctionnent) puis on
    renseigne à la main les attributs dont ``create_interface`` a besoin, avec un
    réseau/audio/WebSocket mockés."""
    win = main.MainWindow.__new__(main.MainWindow)
    QMainWindow.__init__(win)

    win.logger = logging.getLogger("test.main_window")
    # Réseau / audio / WebSocket : mockés, jamais sollicités par create_interface.
    win.network_manager = mock.MagicMock()
    # Couche d'accès au serveur (point 10.8) : mockée, aucune requête ne doit
    # partir pendant la construction de l'interface.
    win.api = mock.MagicMock()
    win.audio_player = mock.MagicMock()
    win.socket_io_client = mock.MagicMock()
    win.notification_manager = None
    win.activities_staff = None
    win.staff_id = False
    win.my_patient = None
    win.patient_id = None
    win.list_patients = [{
        "id": 1,
        "call_number": "A001",
        "activity": "Ordonnance",
        "language_code": "fr",
        "counter_id": 1,
    }]

    win.web_url = "http://localhost:5000"
    win.counter_id = 1
    win.counter_name = "Comptoir 1"

    win.horizontal_mode = horizontal_mode
    win.compact_mode = compact_mode
    win.panel_snap = True
    win.panel_thickness = 300
    win.shutting_down = False
    # Placement réel (point 10.12) : le menu « Réinitialiser la position » s'y
    # branche à la construction de l'interface.
    win.placement = WindowPlacement(win, logger=win.logger)

    win.display_patient_list = False
    win.patient_list_position_vertical = "bottom"
    win.patient_list_position_horizontal = "right"
    win.patient_list_font_size = 12

    win.autocalling = "inactive"
    win.add_paper = "inactive"

    win.next_patient_shortcut = "Alt+S"
    win.validate_patient_shortcut = "Alt+V"
    win.pause_shortcut = "Alt+P"
    win.recall_shortcut = "Alt+R"
    win.deconnect_shortcut = "Alt+D"

    win.create_interface()
    return win


@pytest.fixture
def window():
    win = _make_main_window()
    yield win
    # Pas de win.close() : closeEvent déclenche la séquence d'arrêt complète
    # (attente des workers, libération réseau du comptoir) qui suppose un objet
    # construit via __init__. La fenêtre n'a jamais été affichée : deleteLater
    # suffit à la libérer.
    win.deleteLater()


def test_create_interface_builds_action_buttons(window):
    # Les trois boutons d'action sont créés avec les noms actuels et sont bien
    # des DebounceButton, avec le bon nom accessible (point 28).
    for attr, label in (("btn_next", "Suivant"),
                        ("btn_validate", "Valider"),
                        ("btn_pause", "Pause")):
        button = getattr(window, attr, None)
        assert isinstance(button, DebounceButton), attr
        assert button.accessibleName() == label


def test_create_interface_vertical_uses_vertical_layouts(window):
    # Orientation verticale (défaut) -> QVBoxLayout.
    assert isinstance(window.main_layout, QVBoxLayout)
    assert isinstance(window.main_button_layout, QVBoxLayout)


def test_create_interface_horizontal_uses_horizontal_layouts():
    win = _make_main_window(horizontal_mode=True)
    try:
        assert isinstance(win.main_layout, QHBoxLayout)
        assert isinstance(win.main_button_layout, QHBoxLayout)
    finally:
        win.deleteLater()


def test_create_interface_does_not_touch_network(window):
    # Aucun appel réseau réel pendant la construction de l'interface.
    window.network_manager.request_blocking.assert_not_called()
    window.network_manager.make_handle.assert_not_called()
    window.api.make_handle.assert_not_called()
    window.api.fetch_state.assert_not_called()


def test_toggle_orientation_rebuilds_layout_end_to_end(window):
    # Bout-en-bout avec la vraie create_interface : le basculement change
    # réellement le type de layout. La fenêtre n'est pas affichée et le mode
    # compact est off, donc apply_panel_mode ne se déclenche pas.
    assert isinstance(window.main_layout, QVBoxLayout)

    window.toggle_orientation()
    assert window.horizontal_mode is True
    assert isinstance(window.main_layout, QHBoxLayout)

    window.toggle_orientation()
    assert window.horizontal_mode is False
    assert isinstance(window.main_layout, QVBoxLayout)

    # Toujours pas de réseau réel après reconstruction.
    window.network_manager.request_blocking.assert_not_called()
    window.api.make_handle.assert_not_called()


# --- label_staff : nom de l'équipier ----------------------------------------

def test_label_staff_visible_en_vertical(window):
    # Vertical : le nom de l'équipier tient sa place dans le corps de la fenêtre.
    assert not window.label_staff.isHidden()


def test_label_staff_masque_en_horizontal():
    # Horizontal : le label est MASQUÉ — auparavant il restait vide mais
    # occupait sa place dans la barre (le nom reste dans la barre de titre).
    win = _make_main_window(horizontal_mode=True)
    try:
        assert win.label_staff.isHidden()
    finally:
        win.deleteLater()


def test_update_staff_label_alimente_meme_masque():
    win = _make_main_window(horizontal_mode=True)
    try:
        win.update_staff_label("Alice")
        assert win.staff_name == "Alice"
        assert win.label_staff.text() == "-= Alice =-"  # prêt si on rebascule
        assert win.label_staff.isHidden()
    finally:
        win.deleteLater()


def test_label_staff_retrouve_le_nom_apres_reconstruction(window):
    # Le label est recréé vide à chaque create_interface (orientation,
    # préférences) : le nom courant doit être restauré.
    window.update_staff_label("Alice")
    window.create_interface()
    assert window.label_staff.text() == "-= Alice =-"
    assert not window.label_staff.isHidden()


def test_deconnexion_efface_le_nom_staff(window):
    window.update_staff_label("Alice")
    window.deconnexion_interface()
    assert window.staff_name is None


# --- boutons-icônes : taille nominale vs mode compact -------------------------

def test_boutons_icones_taille_nominale(window):
    assert window.btn_auto_calling.width() == IconeButton.BASE_PX
    assert window.btn_paper.width() == IconeButton.BASE_PX
    assert window.connection_indicator.width() == ConnectionStatusIndicator.BASE_PX


def test_mode_compact_resserre_les_boutons_icones():
    # En mode panneau compact, les boutons-icônes et l'indicateur suivent le
    # resserrement du reste de l'interface (ils étaient figés à 50/30 px).
    win = _make_main_window(compact_mode=True)
    try:
        assert win.btn_auto_calling.width() == IconeButton.COMPACT_PX
        assert win.btn_paper.width() == IconeButton.COMPACT_PX
        assert win.connection_indicator.width() == ConnectionStatusIndicator.COMPACT_PX
    finally:
        win.deleteLater()
