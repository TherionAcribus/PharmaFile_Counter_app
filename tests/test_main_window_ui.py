"""Séparation interface / fenêtre principale (point 10.9).

La construction des widgets a quitté ``MainWindow`` pour ``main_window_ui``,
``login_view`` et ``loading_screen``. Ces tests vérifient que la séparation tient
— et surtout qu'elle n'a rien changé au résultat : l'écran de connexion et
l'indicateur de connexion se construisent réellement, avec les mêmes attributs
posés sur la fenêtre.
"""

import ast
import inspect
import os
import sys

import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QWidget

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import login_view  # noqa: E402
import main  # noqa: E402
import main_window_ui  # noqa: E402

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# --- écran de connexion -----------------------------------------------------

class FakeWindow:
    """Fenêtre minimale : l'écran de connexion ne lui demande que deux actions."""

    def __init__(self):
        self.logins = 0
        self.preferences = 0

    def validate_login(self):
        self.logins += 1

    def show_preferences_dialog(self):
        self.preferences += 1


def test_login_view_construit_l_ecran(qapp):
    window = FakeWindow()
    widget = login_view.create_login_widget(window)
    try:
        assert isinstance(widget, QWidget)
        # Les champs restent posés sur la fenêtre (validate_login les relit).
        assert isinstance(window.initials_input, QLineEdit)
        assert isinstance(window.checkbox_on_all, QCheckBox)
        assert window.checkbox_on_all.isChecked() is True
        assert window.label_connexion.text() == "Connectez-vous"
    finally:
        widget.deleteLater()


def test_login_view_branche_la_validation(qapp):
    window = FakeWindow()
    widget = login_view.create_login_widget(window)
    try:
        window.initials_input.returnPressed.emit()
        assert window.logins == 1
    finally:
        widget.deleteLater()


# --- indicateur de connexion ------------------------------------------------

def test_indicateur_de_connexion_construit(qapp):
    indicateur = main_window_ui.ConnectionStatusIndicator()
    try:
        assert indicateur.status == "connected"
        # Les trois SVG d'état sont chargés depuis les ressources embarquées.
        assert set(indicateur.renderers) == {"connected", "connecting", "disconnected"}
        # État nommé (pas seulement coloré) pour les lecteurs d'écran.
        assert indicateur.accessibleName()
    finally:
        indicateur.deleteLater()


# --- structure : la fenêtre ne construit plus ses widgets ------------------

MOVED_TO_UI = [
    "create_interface", "_apply_compact_styling", "_create_name",
    "_create_label_patient", "_create_main_button_container",
    "_create_option_button_container", "_create_icon_widget", "_create_icon_button",
    "_create_auto_calling_button", "_create_paper_button",
    "_create_choose_patient_button", "_create_more_button",
    "_create_patient_list_widget",
]


@pytest.mark.parametrize("name", MOVED_TO_UI)
def test_constructeur_disponible_dans_le_module_ui(name):
    fonction = getattr(main_window_ui, name)
    # Chaque constructeur prend la fenêtre en premier argument.
    assert list(inspect.signature(fonction).parameters)[0] == "window"


@pytest.mark.parametrize("name", [n for n in MOVED_TO_UI if n != "create_interface"])
def test_constructeur_retire_de_main_window(name):
    """Seule ``create_interface`` subsiste sur la fenêtre, en délégation."""
    assert not hasattr(main.MainWindow, name), name


def test_create_interface_delegue():
    """La méthode restante ne fait que déléguer (elle décide du QUAND, pas du QUOI)."""
    source = inspect.getsource(main.MainWindow.create_interface)
    assert "main_window_ui.create_interface(self)" in source
    corps = [n for n in ast.parse(source.lstrip()).body[0].body
             if not isinstance(n, ast.Expr) or not isinstance(n.value, ast.Constant)]
    assert len(corps) == 1, "create_interface doit se limiter à la délégation"


def test_main_ne_construit_plus_de_mise_en_page():
    """Cliquet : aucune construction de layout/widget d'interface dans main.py.

    Les boîtes de dialogue (QMessageBox) et le menu du systray restent légitimes.
    """
    with open(os.path.join(APP_DIR, "main.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename="main.py")
    interdits = {"QVBoxLayout", "QHBoxLayout", "QLabel", "QPushButton", "QListView",
                 "QDockWidget", "QSizePolicy", "QLineEdit", "QCheckBox",
                 "QPlainTextEdit", "QAbstractItemView"}
    trouves = sorted({node.func.id for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                      and node.func.id in interdits})
    assert trouves == [], f"construction de widgets restée dans main.py : {trouves}"


def test_modules_ui_sans_dependance_a_main():
    """Les modules d'interface ne doivent PAS importer main (sinon import
    circulaire, et la séparation ne vaut rien)."""
    for name in ("main_window_ui.py", "login_view.py", "loading_screen.py"):
        with open(os.path.join(APP_DIR, name), encoding="utf-8") as fh:
            source = fh.read()
        assert "import main" not in source, name
