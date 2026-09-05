"""Réglages de son des préférences : mode muet et diagnostic (point 4).

Deux manques traités ici, sur un VRAI dialogue de préférences :

- se taire passait par « curseur de volume à 0 », ce qui faisait perdre le
  réglage préféré. Le mode muet est désormais une case distincte ; le volume
  reste affiché (grisé) et intact ;
- un problème audio (périphérique absent, codec manquant, fichier son
  introuvable) n'allait QUE dans le journal : l'utilisateur n'avait qu'un
  silence. Le dernier problème signalé par le lecteur est maintenant affiché
  dans la page « Notifications », et suivi en direct.
"""

import os
import sys

import pytest
from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QWidget

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import audio  # noqa: E402
import preferences  # noqa: E402
import settings_schema  # noqa: E402


@pytest.fixture
def isolated_settings(_shared_qapplication):
    """QSettings propre à ces tests (jamais la configuration réelle du poste)."""
    organization = QCoreApplication.organizationName()
    application = QCoreApplication.applicationName()
    QCoreApplication.setOrganizationName("AppComptoirTests")
    QCoreApplication.setApplicationName("test_preferences_audio_controls")
    settings = QSettings()
    settings.clear()
    yield settings
    settings.clear()
    settings.sync()
    QCoreApplication.setOrganizationName(organization)
    QCoreApplication.setApplicationName(application)


class FakePlayer:
    """Lecteur sans Qt : le dialogue n'a besoin que de ces quatre points."""

    error_changed = None   # pas de signal : le dialogue doit le tolérer

    def __init__(self, volume=50, muted=False):
        self.volume = volume
        self.muted = muted
        self.last_error = ""
        self.cleared = 0
        self.played = []

    def set_volume(self, volume):
        self.volume = volume

    def set_muted(self, muted):
        self.muted = muted

    def clear_error(self):
        self.cleared += 1
        self.last_error = ""

    def play_sound(self, name, force=False):
        self.played.append((name, force))


def _dialog(isolated_settings, player=None):
    parent = QWidget()
    if player is not None:
        parent.audio_player = player
    dialog = preferences.PreferencesDialog(parent)
    # Référence Python sur le parent : sans elle, il est ramassé en fin de
    # fonction et Qt détruit le dialogue avec lui.
    dialog._test_parent = parent
    return dialog


# --- Mode muet ---------------------------------------------------------------

def test_case_muet_grise_les_reglages_de_son_sans_les_effacer(isolated_settings):
    """Le volume reste VISIBLE (c'est le réglage conservé), simplement grisé."""
    isolated_settings.setValue("notification_volume", 70)
    dialog = _dialog(isolated_settings)

    assert dialog.mute_checkbox.isChecked() is False
    assert dialog.volume_slider.isEnabled() is True

    dialog.mute_checkbox.setChecked(True)
    assert dialog.volume_slider.isEnabled() is False
    assert dialog.volume_spinbox.isEnabled() is False
    assert dialog.sound_test_button.isEnabled() is False
    assert dialog.volume_spinbox.value() == 70      # réglage conservé, pas remis à 0

    dialog.mute_checkbox.setChecked(False)
    assert dialog.volume_slider.isEnabled() is True
    assert dialog.volume_spinbox.value() == 70


def test_mode_muet_enregistre_est_relu_au_chargement(isolated_settings):
    isolated_settings.setValue("notification_muted", True)
    isolated_settings.setValue("notification_volume", 80)
    dialog = _dialog(isolated_settings)

    assert dialog.mute_checkbox.isChecked() is True
    assert dialog.current_muted is True
    assert dialog.volume_spinbox.value() == 80      # volume préféré retrouvé intact
    assert dialog.sound_test_button.isEnabled() is False


def test_mode_muet_est_enregistre(isolated_settings, monkeypatch):
    """Le mode muet suit le même chemin d'enregistrement que le volume."""
    isolated_settings.setValue("web_url", "https://exemple.test")
    dialog = _dialog(isolated_settings)
    dialog.counter_combobox.addItem("1", 1)
    dialog.counter_combobox.setCurrentIndex(dialog.counter_combobox.count() - 1)
    dialog.mute_checkbox.setChecked(True)
    dialog.volume_slider.setValue(65)
    # Aucun dialogue modal ne doit s'ouvrir (il bloquerait le test), et on
    # s'arrête juste après l'écriture des réglages non secrets : le reste de
    # save_preferences (magasin de secrets, fermeture) ne concerne pas ce test.
    monkeypatch.setattr(preferences.QMessageBox, "warning",
                        lambda *a, **k: pytest.fail(f"dialogue modal inattendu : {a[1:3]}"))
    monkeypatch.setattr(preferences.PreferencesDialog, "_sync_and_verify",
                        lambda self, settings: bool(settings.sync()))

    dialog.save_preferences()

    assert settings_schema.read(QSettings(), "notification_muted") is True
    assert settings_schema.read(QSettings(), "notification_volume") == 65


# --- Diagnostic : problème audio visible -------------------------------------

def test_probleme_audio_deja_connu_affiche_a_l_ouverture(isolated_settings):
    player = FakePlayer()
    player.last_error = "Fichiers son introuvables : ding"
    dialog = _dialog(isolated_settings, player)

    assert dialog.audio_error_label.isVisible() is False   # dialogue non affiché
    assert "ding" in dialog.audio_error_label.text()
    assert dialog.audio_error_label.text().startswith("Problème audio")


def test_probleme_audio_survenu_pendant_les_preferences_est_affiche(isolated_settings):
    """Le lecteur réel signale ses erreurs : le dialogue les suit en direct."""
    parent = QWidget()
    parent.audio_player = audio.build_audio_player(parent, volume=50)
    dialog = preferences.PreferencesDialog(parent)
    dialog._test_parent = parent
    assert dialog.audio_error_label.text() == ""

    parent.audio_player.report_error("Périphérique audio absent")
    assert "Périphérique audio absent" in dialog.audio_error_label.text()

    parent.audio_player.clear_error()
    assert dialog.audio_error_label.text() == ""


def test_essai_de_son_efface_le_probleme_precedent(isolated_settings):
    """Le message affiché après un clic doit concerner CE clic."""
    player = FakePlayer()
    player.last_error = "Erreur précédente"
    dialog = _dialog(isolated_settings, player)

    dialog.test_sound()

    assert player.cleared == 1
    assert player.played and player.played[0][1] is True   # force=True
