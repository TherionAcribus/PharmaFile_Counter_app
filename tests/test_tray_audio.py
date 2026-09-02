"""Systray et sons extraits de MainWindow (point 10.11).

Le systray se teste réellement en « offscreen » : les QSystemTrayIcon se
construisent, leurs menus aussi. On vérifie surtout ce qui était fragile — le
menu de la file reconstruit à l'ouverture seulement, et le retrait effectif des
icônes à la fermeture (sinon elles survivent au processus).
"""

import logging
import os
import sys

import pytest
from PySide6.QtWidgets import QApplication, QWidget

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio  # noqa: E402
import main  # noqa: E402
from tray_manager import TrayManager  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeWindow(QWidget):
    """Fenêtre minimale : le systray ne lui demande que des actions.

    C'est un vrai QWidget : les icônes de la barre d'état lui sont rattachées
    (parent Qt), comme en production.
    """

    def __init__(self, patients=None):
        super().__init__()
        self.list_patients = patients if patients is not None else []
        self.calls = []
        self.selected = []

    def call_web_function_pause(self):
        self.calls.append("pause")

    def call_web_function_validate(self):
        self.calls.append("validate")

    def call_web_function_validate_and_call_next(self):
        self.calls.append("next")

    def select_patient(self, patient_id):
        self.selected.append(patient_id)


@pytest.fixture
def tray(qapp):
    window = FakeWindow()
    manager = TrayManager(window, logger=logging.getLogger("test.tray"))
    yield manager
    manager.cleanup()


# --- construction -----------------------------------------------------------

def test_trois_icones_creees(tray):
    tray.setup()
    assert len(tray.icons) == 3
    assert [icon.toolTip() for icon in tray.icons] == [
        "Pause", "Prochain patient", "Valider patient"]


def test_chaque_icone_a_un_menu(tray):
    tray.setup()
    assert all(icon.contextMenu() is not None for icon in tray.icons)


def test_setup_idempotent(tray):
    """Un second appel ne doit pas empiler six icônes dans la barre."""
    tray.setup()
    tray.setup()
    assert len(tray.icons) == 3


def test_clic_gauche_declenche_l_action(tray):
    from PySide6.QtWidgets import QSystemTrayIcon
    tray.setup()
    for icon in tray.icons:
        icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    assert tray.window.calls == ["pause", "next", "validate"]


def test_clic_droit_ne_declenche_aucune_action(tray):
    """Le clic droit ouvre le menu : il ne doit pas valider un patient."""
    from PySide6.QtWidgets import QSystemTrayIcon
    tray.setup()
    for icon in tray.icons:
        icon.activated.emit(QSystemTrayIcon.ActivationReason.Context)
    assert tray.window.calls == []


# --- menu de la file --------------------------------------------------------

def test_menu_de_file_construit_a_l_ouverture(tray):
    tray.setup()
    tray.window.list_patients = [
        {"id": 1, "call_number": "A1", "activity": "Ordonnance"},
        {"id": 2, "call_number": "B2", "activity": "Conseil"},
    ]
    # Rien tant que le menu n'est pas ouvert.
    assert tray.patient_menu.actions() == []
    tray.patient_menu.aboutToShow.emit()
    assert [a.text() for a in tray.patient_menu.actions()] == [
        "A1 - Ordonnance", "B2 - Conseil"]


def test_menu_de_file_appelle_le_patient_choisi(tray):
    tray.setup()
    tray.window.list_patients = [{"id": 7, "call_number": "A1", "activity": "Ordonnance"}]
    tray.patient_menu.aboutToShow.emit()
    tray.patient_menu.actions()[0].trigger()
    assert tray.window.selected == [7]


def test_menu_de_file_ignore_les_patients_incomplets(tray):
    tray.setup()
    tray.window.list_patients = [
        {"id": 1, "call_number": "A1"},                              # activité manquante
        None,                                                        # entrée invalide
        {"id": 2, "call_number": "B2", "activity": "Conseil"},
    ]
    tray.patient_menu.aboutToShow.emit()
    assert [a.text() for a in tray.patient_menu.actions()] == ["B2 - Conseil"]


def test_menu_de_file_vide_sans_patients(tray):
    tray.setup()
    tray.window.list_patients = None
    tray.patient_menu.aboutToShow.emit()
    assert tray.patient_menu.actions() == []


# --- fermeture --------------------------------------------------------------

def test_cleanup_retire_les_icones(tray):
    tray.setup()
    tray.cleanup()
    assert tray.icons == []


def test_cleanup_sans_setup_est_sans_effet(tray):
    tray.cleanup()   # ne doit pas lever


def test_main_window_delegue_le_systray():
    """La fenêtre ne construit plus d'icône elle-même."""
    assert not hasattr(main.MainWindow, "setup_systray")
    assert not hasattr(main.MainWindow, "_rebuild_tray_patient_menu")
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "main.py"), encoding="utf-8") as fh:
        source = fh.read()
    assert "QSystemTrayIcon" not in source


# --- sons -------------------------------------------------------------------

def test_les_trois_sons_sont_charges(qapp):
    player = audio.build_audio_player(None, volume=50)
    assert set(player.sounds) == {"patient_taken", "ding", "please_validate"}


def test_les_fichiers_sons_existent():
    """Chemins résolus par resources : valables aussi en build onefile."""
    from resources import resource_path
    for relative in audio.SOUNDS.values():
        assert os.path.exists(resource_path(relative)), relative


def test_volume_converti_en_fraction(qapp):
    player = audio.build_audio_player(None, volume=70)
    assert player.audio_output.volume() == pytest.approx(0.7, abs=0.01)


def test_son_inconnu_ne_leve_pas(qapp, caplog):
    player = audio.build_audio_player(None, volume=10)
    with caplog.at_level(logging.WARNING, logger="appcomptoir.audio"):
        player.play_sound("inexistant")
    assert any("inexistant" in r.getMessage() for r in caplog.records)
