"""Ergonomie de la fenêtre Préférences.

- réglages dépendants grisés/masqués quand ils n'auraient aucun effet
  (épaisseur hors mode compact, position de liste de l'autre orientation,
  liste masquée, raccourcis désactivés) ;
- modifications non enregistrées : « Appliquer » actif seulement s'il y a
  quelque chose à appliquer, et fermer sans enregistrer demande confirmation
  (enregistrer / ne pas enregistrer / continuer) au lieu de tout jeter ;
- raccourcis désactivés : les combinaisons (grisées) ne bloquent plus
  l'enregistrement ;
- libellés : suffixes d'unité, « immédiatement » pour un délai nul, thème
  « Aucun ».
"""

import os
import sys

import pytest
from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QDialog, QWidget

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import preferences  # noqa: E402
from shortcut_config import MODE_DISABLED, MODE_FOCUSED  # noqa: E402


@pytest.fixture
def isolated_settings(_shared_qapplication):
    organization = QCoreApplication.organizationName()
    application = QCoreApplication.applicationName()
    QCoreApplication.setOrganizationName("AppComptoirTests")
    QCoreApplication.setApplicationName("test_preferences_ergonomie")
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
    dialog._test_parent = parent
    return dialog


def _set_mode(dialog, mode):
    dialog.shortcut_mode_combo.setCurrentIndex(
        dialog.shortcut_mode_combo.findData(mode))


# --- Réglages dépendants -----------------------------------------------------

def test_epaisseur_grisee_hors_mode_compact(isolated_settings):
    dialog = _dialog()
    dialog.compact_mode_checkbox.setChecked(False)
    assert not dialog.panel_thickness_spinbox.isEnabled()
    dialog.compact_mode_checkbox.setChecked(True)
    assert dialog.panel_thickness_spinbox.isEnabled()


def test_magnetisme_reste_actif_hors_mode_compact(isolated_settings):
    """Le magnétisme agit à chaque déplacement, compact ou non."""
    dialog = _dialog()
    dialog.compact_mode_checkbox.setChecked(False)
    assert dialog.panel_snap_checkbox.isEnabled()


def test_une_seule_position_de_liste_affichee(isolated_settings):
    dialog = _dialog()
    dialog.horizontal_mode_checkbox.setChecked(False)
    assert not dialog.patient_list_position_vertical.isHidden()
    assert dialog.patient_list_position_horizontal.isHidden()
    dialog.horizontal_mode_checkbox.setChecked(True)
    assert dialog.patient_list_position_vertical.isHidden()
    assert not dialog.patient_list_position_horizontal.isHidden()


def test_position_et_taille_grisees_si_liste_masquee(isolated_settings):
    dialog = _dialog()
    dialog.display_patient_list.setChecked(False)
    assert not dialog.patient_list_position_vertical.isEnabled()
    assert not dialog.patient_list_font_size_spinbox.isEnabled()
    # La messagerie, indépendante de la liste, reste réglable.
    assert dialog.messaging_position.isEnabled()
    dialog.display_patient_list.setChecked(True)
    assert dialog.patient_list_font_size_spinbox.isEnabled()


def test_raccourcis_desactives_grisent_les_combinaisons(isolated_settings):
    dialog = _dialog()
    _set_mode(dialog, MODE_DISABLED)
    assert not dialog.next_patient_shortcut_input.isEnabled()
    assert not dialog.confirm_sensitive_checkbox.isEnabled()
    _set_mode(dialog, MODE_FOCUSED)
    assert dialog.next_patient_shortcut_input.isEnabled()
    assert dialog.confirm_sensitive_checkbox.isEnabled()


def test_raccourcis_desactives_ne_bloquent_pas_l_enregistrement(isolated_settings, monkeypatch):
    """Champs grisés donc non corrigeables : un raccourci invalide ne doit pas
    empêcher d'enregistrer tant que les raccourcis sont désactivés."""
    monkeypatch.setattr(preferences.QMessageBox, "warning",
                        lambda *a, **k: pytest.fail("enregistrement bloqué"))
    dialog = _dialog()
    dialog.next_patient_shortcut_input.setShortcut("Ctrl")   # invalide
    _set_mode(dialog, MODE_DISABLED)
    dialog.save_preferences()
    assert dialog.result() == QDialog.Accepted


def test_raccourcis_actifs_restent_valides_a_l_enregistrement(isolated_settings, monkeypatch):
    warned = []
    monkeypatch.setattr(preferences.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a[1]))
    dialog = _dialog()
    _set_mode(dialog, MODE_FOCUSED)
    dialog.next_patient_shortcut_input.setShortcut("Ctrl")
    dialog.save_preferences()
    assert warned == ["Raccourcis invalides"]


# --- Modifications non enregistrées ------------------------------------------

def test_appliquer_inactif_sans_modification(isolated_settings):
    dialog = _dialog()
    assert not dialog.apply_button.isEnabled()
    assert dialog.save_status_label.text() == ""


def test_modification_signalee_puis_annulee_par_retour_a_la_valeur(isolated_settings):
    dialog = _dialog()
    spin = dialog.notification_duration_spinbox
    initial = spin.value()
    spin.setValue(initial + 3)
    assert dialog.apply_button.isEnabled()
    assert "non enregistrées" in dialog.save_status_label.text()
    spin.setValue(initial)
    assert not dialog.apply_button.isEnabled()
    assert dialog.save_status_label.text() == ""


def test_champ_hors_reglage_ne_compte_pas(isolated_settings):
    """Le sélecteur « Tester un son » ne porte aucun réglage."""
    dialog = _dialog()
    dialog.sound_test_combo.setCurrentIndex(dialog.sound_test_combo.count() - 1)
    assert not dialog.apply_button.isEnabled()


def test_raccourci_modifie_compte(isolated_settings):
    dialog = _dialog()
    dialog.pause_shortcut_input.setShortcut("Ctrl+F9")
    assert dialog.apply_button.isEnabled()


def test_appliquer_remet_a_zero_l_etat_modifie(isolated_settings):
    dialog = _dialog()
    dialog.notification_duration_spinbox.setValue(17)
    dialog.save_preferences(close_dialog=False)
    assert not dialog.apply_button.isEnabled()
    assert "enregistrées et appliquées" in dialog.save_status_label.text()
    assert not dialog._has_unsaved_changes()


def test_fermer_sans_modification_ne_demande_rien(isolated_settings, monkeypatch):
    monkeypatch.setattr(preferences.PreferencesDialog, "_confirm_discard",
                        lambda self: pytest.fail("confirmation inattendue"))
    dialog = _dialog()
    dialog.reject()
    assert dialog.result() == QDialog.Rejected


def test_fermer_puis_continuer_a_modifier(isolated_settings, monkeypatch):
    monkeypatch.setattr(preferences.PreferencesDialog, "_confirm_discard",
                        lambda self: "cancel")
    dialog = _dialog()
    dialog.notification_duration_spinbox.setValue(17)
    dialog.setResult(-1)
    dialog.reject()
    assert dialog.result() == -1                       # toujours ouvert
    assert dialog.notification_duration_spinbox.value() == 17


def test_fermer_sans_enregistrer(isolated_settings, monkeypatch):
    monkeypatch.setattr(preferences.PreferencesDialog, "_confirm_discard",
                        lambda self: "discard")
    dialog = _dialog()
    dialog.notification_duration_spinbox.setValue(17)
    dialog.reject()
    assert dialog.result() == QDialog.Rejected
    assert isolated_settings.value("notification_duration") is None


def test_fermer_en_enregistrant(isolated_settings, monkeypatch):
    monkeypatch.setattr(preferences.PreferencesDialog, "_confirm_discard",
                        lambda self: "save")
    dialog = _dialog()
    dialog.notification_duration_spinbox.setValue(17)
    dialog.reject()
    assert dialog.result() == QDialog.Accepted
    assert isolated_settings.value("notification_duration", type=int) == 17


# --- Libellés ----------------------------------------------------------------

def test_unites_et_valeur_nulle_en_toutes_lettres(isolated_settings):
    dialog = _dialog()
    assert dialog.notification_duration_spinbox.suffix() == " s"
    assert dialog.notification_font_size_spinbox.suffix() == " pt"
    assert dialog.panel_thickness_spinbox.suffix() == " px"
    spin = dialog.notification_after_deconnection_spinbox
    spin.setValue(0)
    assert spin.text() == "immédiatement"


def test_theme_aucun(isolated_settings):
    dialog = _dialog()
    assert dialog.skin_combo.itemText(0) == preferences.NO_SKIN_LABEL
    assert dialog.skin_combo.itemData(0) == ""
