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
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QLineEdit, QScrollArea,
    QVBoxLayout, QWidget)

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


def test_navigation_par_selection_change_de_page(isolated_settings):
    """La navigation suit la SÉLECTION (currentItemChanged) : clics souris mais
    aussi flèches du clavier et Tab — auparavant seul itemClicked réagissait."""
    dialog = _dialog()
    dialog.navigation_list.setCurrentItem(dialog.notifications_item)
    assert dialog.stacked_widget.currentIndex() == 3
    dialog.navigation_list.setCurrentItem(dialog.general_item)
    assert dialog.stacked_widget.currentIndex() == 0
    dialog.change_page(None)   # sélection vidée : tolérée sans erreur


def test_pages_sont_scrollables(isolated_settings):
    """Les 4 pages sont des QScrollArea redimensionnables : le contenu reste
    accessible sur petit écran / grande police au lieu d'être tronqué."""
    dialog = _dialog()
    for page in (dialog.general_page, dialog.connexion_page,
                 dialog.raccourcis_page, dialog.notifications_page):
        assert isinstance(page, QScrollArea)
        assert page.widgetResizable() is True


def test_url_invalide_bascule_sur_la_page_connexion(isolated_settings, monkeypatch):
    """Une erreur de validation renvoie sur la page fautive au lieu de laisser
    l'utilisateur chercher où corriger."""
    dialog = _dialog()
    dialog.url_input.setText("ftp://serveur")   # schéma non http(s)
    monkeypatch.setattr(preferences.QMessageBox, "warning",
                        lambda *a, **k: None)
    dialog.save_preferences()
    assert dialog.stacked_widget.currentIndex() == 1   # page « Connexion »


def test_raccourci_invalide_bascule_sur_la_page_raccourcis(isolated_settings, monkeypatch):
    isolated_settings.setValue("web_url", "https://exemple.test")
    dialog = _dialog()
    dialog.counter_combobox.addItem("1", 1)
    dialog.counter_combobox.setCurrentIndex(dialog.counter_combobox.count() - 1)
    # « Ctrl » seul = modificateur sans touche : raccourci invalide.
    group = dialog.next_patient_shortcut_input
    group.findChild(QCheckBox, "Ctrl").setChecked(True)
    group.findChild(QLineEdit).setText("")
    monkeypatch.setattr(preferences.QMessageBox, "warning",
                        lambda *a, **k: None)
    dialog.save_preferences()
    assert dialog.stacked_widget.currentIndex() == 2   # page « Raccourcis »


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
