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
    QDialog, QDialogButtonBox, QLabel, QScrollArea, QVBoxLayout, QWidget)

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


def test_valeurs_par_defaut_page_courante(isolated_settings):
    """« Rétablir cette page » remet les champs de la page affichée aux défauts
    du schéma — sans toucher les autres pages ni persister quoi que ce soit."""
    import settings_schema
    dialog = _dialog()
    dialog.always_on_top_checkbox.setChecked(
        not settings_schema.default("always_on_top"))
    dialog.notification_duration_spinbox.setValue(42)
    dialog.navigation_list.setCurrentItem(dialog.general_item)

    dialog._reset_to_defaults(current_page_only=True)

    assert dialog.always_on_top_checkbox.isChecked() \
        == settings_schema.default("always_on_top")
    # Autre page non concernée.
    assert dialog.notification_duration_spinbox.value() == 42
    # Et rien n'a été écrit dans QSettings.
    assert isolated_settings.value("always_on_top") is None


def test_valeurs_par_defaut_toutes_les_pages(isolated_settings):
    """« Rétablir toutes les pages » remet schéma + raccourcis par défaut, sans
    toucher à la configuration du poste (URL, comptoir)."""
    import settings_schema
    import shortcut_defaults
    isolated_settings.setValue("web_url", "https://officine.example")
    isolated_settings.setValue("counter_id", 3)
    dialog = _dialog()
    dialog.notification_duration_spinbox.setValue(42)
    dialog.next_patient_shortcut_input.setShortcut("Ctrl+F9")
    dialog.reconnect_last_user_checkbox.setChecked(
        not settings_schema.default("reconnect_last_user"))

    dialog._reset_to_defaults(current_page_only=False)

    assert dialog.notification_duration_spinbox.value() \
        == settings_schema.default("notification_duration")
    assert dialog.next_patient_shortcut_input.shortcut() \
        == shortcut_defaults.default_shortcut("next_patient_shortcut")
    assert dialog.reconnect_last_user_checkbox.isChecked() \
        == settings_schema.default("reconnect_last_user")
    # Configuration du poste intacte (et rien n'a été persisté).
    assert isolated_settings.value("web_url") == "https://officine.example"
    assert int(isolated_settings.value("counter_id")) == 3
    assert "officine.example" in dialog.station_url_value.text()


def test_bouton_valeurs_par_defaut_propose_les_deux_portees(isolated_settings):
    dialog = _dialog()
    assert dialog.button_box.buttonRole(dialog.defaults_button) \
        == QDialogButtonBox.ResetRole
    actions = [a.text() for a in dialog.defaults_button.menu().actions()]
    assert len(actions) == 2


def test_bouton_appliquer_sauvegarde_sans_fermer(isolated_settings, monkeypatch):
    """« Appliquer » lance la même validation/persistance qu'« Enregistrer »
    mais sans fermeture (close_dialog=False) ; « Enregistrer » garde True —
    le paramètre booléen de clicked() ne doit pas fuiter dans l'appel."""
    dialog = _dialog()
    seen = []
    monkeypatch.setattr(dialog, "save_preferences",
                        lambda close_dialog=True: seen.append(close_dialog))
    assert dialog.button_box.buttonRole(dialog.apply_button) \
        == QDialogButtonBox.ApplyRole
    # « Appliquer » n'est actif qu'avec une modification en attente.
    dialog.notification_duration_spinbox.setValue(
        dialog.notification_duration_spinbox.value() + 1)
    dialog.apply_button.click()
    dialog.save_button.click()
    assert seen == [False, True]


def test_appliquer_persiste_emet_et_laisse_ouvert(isolated_settings):
    """_finalize_save avec ``close=False`` écrit QSettings, émet ``applied``
    et laisse le dialogue ouvert — exactement le comportement « Appliquer »."""
    dialog = _dialog()
    applied = []
    dialog.applied.connect(lambda: applied.append(True))

    dialog._finalize_save({"notification_volume": 33, "notification_muted": True,
                           "selected_skin": ""}, close=False)

    assert isolated_settings.value("notification_volume", type=int) == 33
    assert isolated_settings.value("notification_muted", type=bool) is True
    assert applied == [True]            # la fenêtre a été prévenue
    assert dialog.result() == 0         # dialogue toujours ouvert
    assert "enregistrées" in dialog.save_status_label.text()


def test_enregistrer_ne_fait_aucun_appel_reseau(isolated_settings, monkeypatch):
    """« Enregistrer » est synchrone : plus de vérification serveur (elle ne
    concernait que la configuration du poste, désormais à part)."""
    import station_dialog
    monkeypatch.setattr(station_dialog.requests, "post",
                        lambda *a, **k: pytest.fail("appel réseau inattendu"))
    monkeypatch.setattr(station_dialog.requests, "get",
                        lambda *a, **k: pytest.fail("appel réseau inattendu"))
    dialog = _dialog()
    dialog.notification_duration_spinbox.setValue(17)
    dialog.save_preferences()
    assert dialog.result() == QDialog.Accepted
    assert isolated_settings.value("notification_duration", type=int) == 17
    assert isolated_settings.value("web_url") is None   # poste non touché


def test_enregistrer_persiste_et_ferme_sans_emit_applied(isolated_settings):
    """« Enregistrer » (close=True) : même persistance, puis Accepted — pas
    d'émission ``applied`` (Accepted est déjà le déclencheur unique)."""
    dialog = _dialog()
    applied = []
    dialog.applied.connect(lambda: applied.append(True))

    dialog._finalize_save({"notification_volume": 40, "notification_muted": False,
                           "selected_skin": ""}, close=True)

    assert isolated_settings.value("notification_volume", type=int) == 40
    assert dialog.result() == QDialog.Accepted
    assert applied == []


def test_ouvrir_dossier_journaux(isolated_settings, monkeypatch, tmp_path):
    """Le bouton Journal ouvre l'explorateur sur le dossier des logs réel
    (créé au besoin) via QDesktopServices — pas un chemin en dur."""
    log_dir = tmp_path / "logs_dir"   # volontairement absent : doit être créé
    monkeypatch.setattr(preferences, "default_log_dir", lambda: log_dir)
    opened = []
    monkeypatch.setattr(preferences.QDesktopServices, "openUrl",
                        lambda url: opened.append(url) or True)
    dialog = _dialog()
    dialog.open_logs_button.click()
    assert log_dir.is_dir()
    assert len(opened) == 1
    from pathlib import Path
    assert Path(opened[0].toLocalFile()) == log_dir


def test_ouvrir_dossier_journaux_echec_signale(isolated_settings, monkeypatch, tmp_path):
    """Si l'explorateur refuse, l'échec est signalé — pas un clic muet."""
    monkeypatch.setattr(preferences, "default_log_dir",
                        lambda: tmp_path / "logs")
    monkeypatch.setattr(preferences.QDesktopServices, "openUrl",
                        lambda url: False)
    warned = []
    monkeypatch.setattr(preferences.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a))
    dialog = _dialog()
    dialog.open_logs_button.click()
    assert warned


def test_etat_connexion_en_direct_suit_le_parent(isolated_settings):
    """La page Connexion affiche l'état temps réel de l'application (socket),
    lu depuis la fenêtre principale et rafraîchi tant que le dialogue vit."""
    parent = QWidget()
    parent._rt_status = "connected"
    parent._rt_attempts = 0
    dialog = preferences.PreferencesDialog(parent)
    dialog._test_parent = parent
    assert "connecté" in dialog.live_status_label.text()

    parent._rt_status = "disconnected"
    parent._rt_attempts = 3
    dialog._refresh_live_connection_status()
    assert "déconnecté" in dialog.live_status_label.text()
    assert "tentative 3" in dialog.live_status_label.text()

    parent._rt_status = "connecting"
    dialog._refresh_live_connection_status()
    assert "en cours" in dialog.live_status_label.text()


def test_etat_connexion_inconnu_sans_etat_parent(isolated_settings):
    """Parent sans état temps réel (avant la première connexion, test) :
    affichage honnête « inconnu » plutôt qu'un faux « connecté »."""
    dialog = _dialog()
    assert "inconnu" in dialog.live_status_label.text()


def test_pages_sont_scrollables(isolated_settings):
    """Les 5 pages sont des QScrollArea redimensionnables : le contenu reste
    accessible sur petit écran / grande police au lieu d'être tronqué."""
    dialog = _dialog()
    for page in (dialog.general_page, dialog.session_page,
                 dialog.raccourcis_page, dialog.notifications_page,
                 dialog.station_page):
        assert isinstance(page, QScrollArea)
        assert page.widgetResizable() is True


def test_raccourci_invalide_bascule_sur_la_page_raccourcis(isolated_settings, monkeypatch):
    dialog = _dialog()
    # « Ctrl » seul = modificateur sans touche : raccourci invalide.
    dialog.next_patient_shortcut_input.setShortcut("Ctrl")
    monkeypatch.setattr(preferences.QMessageBox, "warning",
                        lambda *a, **k: None)
    dialog.save_preferences()
    assert dialog.stacked_widget.currentIndex() == 2   # page « Raccourcis »


def test_status_label_est_un_label_enroule(isolated_settings, monkeypatch):
    """Le statut de connexion (configuration du poste) est un QLabel à retour
    à la ligne (plus de QTextEdit à largeur fixe qui bloquait le
    redimensionnement), et le texte reste sélectionnable pour copier un
    message d'erreur."""
    import station_dialog
    monkeypatch.setattr(station_dialog, "load_secret", lambda s: "")
    monkeypatch.setattr(station_dialog, "secret_storage", lambda s: "")
    parent = QWidget()
    dialog = station_dialog.StationDialog(parent)
    dialog._test_parent = parent
    assert isinstance(dialog.status_label, QLabel)
    assert dialog.status_label.wordWrap() is True
    assert dialog.status_label.maximumWidth() == 16777215  # pas de largeur figée
    assert dialog.status_label.textInteractionFlags() & Qt.TextSelectableByMouse


def test_chaque_groupe_raccourci_est_independant(isolated_settings):
    """Les 5 champs de capture ont chacun leur propre valeur : modifier l'un
    n'affecte pas les autres."""
    dialog = _dialog()
    others = (dialog.validate_patient_shortcut_input,
              dialog.pause_shortcut_input,
              dialog.recall_shortcut_input,
              dialog.deconnect_input)
    before = [w.shortcut() for w in others]

    target = dialog.next_patient_shortcut_input
    target.setShortcut("Ctrl+N")

    assert target.shortcut() == "Ctrl+N"
    assert [w.shortcut() for w in others] == before


def test_capture_frappe_produit_un_raccourci_canonique(isolated_settings):
    """ShortcutEdit traduit une frappe simulée en forme canonique persistée
    (« Alt+Maj+P »), y compris pour les touches de fonction."""
    from PySide6.QtTest import QTest

    dialog = _dialog()
    edit = dialog.next_patient_shortcut_input
    edit.setShortcut("")

    QTest.keyClick(edit, Qt.Key_P, Qt.AltModifier | Qt.ShiftModifier)
    assert edit.shortcut() == "Alt+Maj+P"

    QTest.keyClick(edit, Qt.Key_F5, Qt.ControlModifier)
    assert edit.shortcut() == "Ctrl+F5"


def test_retour_arriere_efface_le_raccourci(isolated_settings):
    """Sans modificateur, Retour arrière vide le champ (geste « effacer ») ;
    la valeur enregistrée repasse à une chaîne vide détectée comme invalide."""
    from PySide6.QtTest import QTest

    dialog = _dialog()
    edit = dialog.pause_shortcut_input
    assert edit.shortcut() == "Alt+P"   # défaut chargé

    QTest.keyClick(edit, Qt.Key_Backspace)
    assert edit.shortcut() == ""


def test_pas_d_attributs_de_raccourci_residuels(isolated_settings):
    """create_shortcut_input ne laisse pas d'attributs sur le dialogue : ils ne
    désignaient que le DERNIER groupe créé et invitaient à l'erreur."""
    dialog = _dialog()
    for attr in ("ctrl_button", "alt_button", "shift_button", "win_button",
                 "key_input"):
        assert not hasattr(dialog, attr)
