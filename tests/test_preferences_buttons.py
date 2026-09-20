"""Boutons du dialogue Préférences : Enregistrer / Annuler (QDialogButtonBox).

- « Annuler » ferme le dialogue avec Rejected sans enregistrer : auparavant
  il n'y avait qu'« Enregistrer » et il fallait deviner la croix de fermeture ;
- la barre de boutons est en bas du dialogue : le bouton unique était ajouté
  au layout horizontal, ce qui le rendait pleine hauteur, collé à droite.
"""

import os
import sys

import pytest
from PySide6.QtCore import QCoreApplication, QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout,
    QWidget)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import preferences  # noqa: E402


@pytest.fixture
def isolated_settings(_shared_qapplication):
    """QSettings propre à ces tests (jamais la configuration réelle du poste)."""
    organization = QCoreApplication.organizationName()
    application = QCoreApplication.applicationName()
    QCoreApplication.setOrganizationName("AppComptoirTests")
    QCoreApplication.setApplicationName("test_preferences_buttons")
    settings = QSettings()
    settings.clear()
    yield settings
    settings.clear()
    settings.sync()
    QCoreApplication.setOrganizationName(organization)
    QCoreApplication.setApplicationName(application)


def _dialog():
    parent = QWidget()
    dialog = preferences.PreferencesDialog(parent)
    # Référence Python sur le parent : sans elle, il est ramassé en fin de
    # fonction et Qt détruit le dialogue avec lui.
    dialog._test_parent = parent
    return dialog


def _reject_button(dialog):
    for button in dialog.button_box.buttons():
        if dialog.button_box.buttonRole(button) == QDialogButtonBox.RejectRole:
            return button
    return None


def test_annuler_rejette_le_dialogue(isolated_settings):
    dialog = _dialog()
    cancel = _reject_button(dialog)
    assert cancel is not None and cancel.text() == "Annuler"
    cancel.click()
    assert dialog.result() == QDialog.Rejected  # fermé sans enregistrer


def test_boutons_en_bas_du_dialogue(isolated_settings):
    dialog = _dialog()
    assert isinstance(dialog.main_layout, QVBoxLayout)
    last = dialog.main_layout.itemAt(dialog.main_layout.count() - 1).widget()
    assert last is dialog.button_box
    # « Enregistrer » vit dans la barre et reste le bouton d'acceptation.
    assert dialog.button_box.buttonRole(dialog.save_button) == QDialogButtonBox.AcceptRole


def test_status_label_est_un_label_enroule(isolated_settings):
    """Le statut de connexion est un QLabel à retour à la ligne (plus de
    QTextEdit à largeur fixe qui bloquait le redimensionnement), et le texte
    reste sélectionnable pour copier un message d'erreur."""
    dialog = _dialog()
    assert isinstance(dialog.status_label, QLabel)
    assert dialog.status_label.wordWrap() is True
    assert dialog.status_label.maximumWidth() == 16777215  # pas de largeur figée
    assert dialog.status_label.textInteractionFlags() & Qt.TextSelectableByMouse


def test_chaque_groupe_raccourci_est_independant(isolated_settings):
    """Les 5 groupes « modificateurs + touche » ont chacun leurs propres cases :
    modifier l'un n'affecte pas les autres (accès via findChild)."""
    dialog = _dialog()
    others = (dialog.validate_patient_shortcut_input,
              dialog.pause_shortcut_input,
              dialog.recall_shortcut_input,
              dialog.deconnect_input)
    before = [dialog.get_shortcut_text(w) for w in others]

    target = dialog.next_patient_shortcut_input
    for modifier in ("Ctrl", "Alt", "Maj", "Win"):
        target.findChild(QCheckBox, modifier).setChecked(False)
    target.findChild(QCheckBox, "Ctrl").setChecked(True)
    target.findChild(QLineEdit).setText("N")

    assert dialog.get_shortcut_text(target) == "Ctrl+N"
    assert [dialog.get_shortcut_text(w) for w in others] == before


def test_pas_d_attributs_de_raccourci_residuels(isolated_settings):
    """create_shortcut_input ne laisse pas d'attributs sur le dialogue : ils ne
    désignaient que le DERNIER groupe créé et invitaient à l'erreur."""
    dialog = _dialog()
    for attr in ("ctrl_button", "alt_button", "shift_button", "win_button",
                 "key_input"):
        assert not hasattr(dialog, attr)
