"""Configuration du poste séparée des préférences personnelles.

Serveur, secret et comptoir décrivent la MACHINE (réglés une fois à
l'installation) : ils ont quitté la page « Connexion » des préférences pour une
fenêtre dédiée (StationDialog). Couvre :
- le secret n'est jamais réaffiché (champ vide, simple état « enregistré ») et
  un champ laissé vide conserve le secret enregistré ;
- l'enregistrement ne vérifie le serveur que si l'URL ou le secret changent, et
  ne réécrit le secret que s'il a changé ;
- la page « Poste » des préférences : résumé en lecture seule, modification
  après avertissement, signal ``station_changed`` ;
- main.py ouvre la configuration du poste au premier démarrage et après un
  échec de reconnexion.

Le magasin de secrets réel (keyring) n'est jamais touché : load_secret,
save_secret et secret_storage sont remplacés.
"""

import os
import sys
import types

import pytest
from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QDialog, QLineEdit, QMessageBox, QWidget

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import preferences  # noqa: E402
import station_dialog  # noqa: E402
from secret_store import STORAGE_KEYRING, STORAGE_NONE  # noqa: E402

STORED = "secret-enregistre"


@pytest.fixture
def isolated_settings(_shared_qapplication):
    organization = QCoreApplication.organizationName()
    application = QCoreApplication.applicationName()
    QCoreApplication.setOrganizationName("AppComptoirTests")
    QCoreApplication.setApplicationName("test_station_dialog")
    settings = QSettings()
    settings.clear()
    yield settings
    settings.clear()
    settings.sync()
    QCoreApplication.setOrganizationName(organization)
    QCoreApplication.setApplicationName(application)


@pytest.fixture
def secrets(monkeypatch):
    """Magasin de secrets simulé : {"value": secret enregistré, "saved": [...]}."""
    store = {"value": STORED, "saved": []}
    monkeypatch.setattr(station_dialog, "load_secret", lambda s: store["value"])
    monkeypatch.setattr(
        station_dialog, "secret_storage",
        lambda s: STORAGE_KEYRING if store["value"] else STORAGE_NONE)
    monkeypatch.setattr(preferences, "secret_storage",
                        lambda s: STORAGE_KEYRING if store["value"] else STORAGE_NONE)

    def _save(settings, value):
        store["saved"].append(value)
        store["value"] = value
        return True
    monkeypatch.setattr(station_dialog, "save_secret", _save)
    return store


@pytest.fixture
def no_modal(monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: warned.append(a[1:3]))
    return warned


def _configured(settings, url="https://serveur.example", counter=3):
    settings.setValue("web_url", url)
    settings.setValue("counter_id", counter)


def _station(monkeypatch=None):
    parent = QWidget()
    if monkeypatch is not None:
        # Pas de chargement réseau de la liste des comptoirs à l'ouverture.
        monkeypatch.setattr(station_dialog.StationDialog, "load_counters",
                            lambda self: None)
    dialog = station_dialog.StationDialog(parent)
    dialog._test_parent = parent
    return dialog


def _select_counter(dialog, counter_id=3):
    dialog.counter_combobox.clear()
    dialog.counter_combobox.addItem(f"Comptoir {counter_id}", counter_id)
    dialog.counter_combobox.setCurrentIndex(0)


# --- Secret : jamais réaffiché ----------------------------------------------

def test_secret_enregistre_jamais_affiche(isolated_settings, secrets, monkeypatch):
    _configured(isolated_settings)
    dialog = _station(monkeypatch)
    assert dialog.app_secret_input.text() == ""
    assert dialog.app_secret_input.echoMode() == QLineEdit.Password
    assert "conserver" in dialog.app_secret_input.placeholderText()
    assert "enregistré" in dialog.secret_status_label.text()
    assert STORED not in dialog.secret_status_label.text()
    # « Afficher la saisie » ne peut rien révéler : le champ est vide.
    dialog.show_secret_checkbox.setChecked(True)
    assert dialog.app_secret_input.text() == ""


def test_champ_vide_utilise_le_secret_enregistre(isolated_settings, secrets, monkeypatch):
    dialog = _station(monkeypatch)
    assert dialog._app_secret() == STORED
    dialog.app_secret_input.setText("  nouveau  ")
    assert dialog._app_secret() == "nouveau"


def test_sans_secret_enregistre_le_champ_le_demande(isolated_settings, secrets, monkeypatch):
    secrets["value"] = ""
    dialog = _station(monkeypatch)
    assert "administrateur" in dialog.app_secret_input.placeholderText()
    assert "Aucun secret" in dialog.secret_status_label.text()


# --- Enregistrement ----------------------------------------------------------

def test_changer_seulement_le_comptoir_n_appelle_pas_le_serveur(
        isolated_settings, secrets, monkeypatch, no_modal):
    """URL et secret inchangés (champ secret vide) : enregistrement direct,
    secret non réécrit."""
    _configured(isolated_settings, counter=3)
    dialog = _station(monkeypatch)
    monkeypatch.setattr(dialog, "_start_worker",
                        lambda *a: pytest.fail("vérification réseau inattendue"))
    _select_counter(dialog, 5)
    dialog.save_preferences()
    assert dialog.result() == QDialog.Accepted
    assert int(QSettings().value("counter_id")) == 5
    assert secrets["saved"] == []          # secret inchangé : pas réécrit
    assert no_modal == []


def test_nouveau_secret_verifie_la_connexion_avant_d_enregistrer(
        isolated_settings, secrets, monkeypatch, no_modal):
    _configured(isolated_settings)
    dialog = _station(monkeypatch)
    started = []
    monkeypatch.setattr(dialog, "_start_worker",
                        lambda kind, worker: started.append(kind) or True)
    _select_counter(dialog, 3)
    dialog.app_secret_input.setText(" autre-secret ")
    dialog.save_preferences()
    assert started == ["token_check"]
    assert dialog._pending_save["app_secret"] == "autre-secret"
    assert dialog.app_secret_input.text() == "autre-secret"   # saisie nettoyée
    assert secrets["saved"] == []          # rien tant que la vérification n'a pas abouti


def test_finalize_enregistre_le_nouveau_secret(isolated_settings, secrets, monkeypatch):
    dialog = _station(monkeypatch)
    dialog._pending_save = {"settings": {"web_url": "https://s.example", "counter_id": 4},
                            "app_secret": "autre-secret"}
    dialog._finalize_save()
    assert secrets["saved"] == ["autre-secret"]
    assert QSettings().value("web_url") == "https://s.example"
    assert dialog.result() == QDialog.Accepted


def test_aucun_secret_du_tout_est_refuse(isolated_settings, secrets, monkeypatch, no_modal):
    secrets["value"] = ""
    _configured(isolated_settings)
    dialog = _station(monkeypatch)
    _select_counter(dialog, 3)
    dialog.save_preferences()
    assert no_modal and no_modal[0][0] == "Secret manquant"
    assert dialog.result() != QDialog.Accepted


def test_url_invalide_refusee(isolated_settings, secrets, monkeypatch, no_modal):
    dialog = _station(monkeypatch)
    dialog.url_input.setText("ftp://serveur")
    _select_counter(dialog, 3)
    dialog.save_preferences()
    assert no_modal and no_modal[0][0] == "URL invalide"
    assert dialog._pending_save is None


# --- Préférences : page « Poste » -------------------------------------------

def _prefs(parent=None):
    parent = parent or QWidget()
    dialog = preferences.PreferencesDialog(parent)
    dialog._test_parent = parent
    return dialog


def test_preferences_ne_contiennent_plus_la_configuration_du_poste(isolated_settings, secrets):
    dialog = _prefs()
    for attr in ("url_input", "app_secret_input", "counter_combobox", "test_button"):
        assert not hasattr(dialog, attr)
    assert dialog.session_item.text() == "Session"
    assert dialog.station_item.text() == "Poste"
    # La page « Session » garde les choix personnels.
    assert dialog.reconnect_last_user_checkbox.parent() is not None


def test_page_poste_resume_en_lecture_seule(isolated_settings, secrets):
    _configured(isolated_settings, counter=3)
    parent = QWidget()
    parent.counter_id, parent.counter_name = 3, "Comptoir Nord"
    dialog = _prefs(parent)
    assert dialog.station_url_value.text() == "https://serveur.example"
    assert dialog.station_counter_value.text() == "Comptoir Nord (n° 3)"
    assert "enregistré" in dialog.station_secret_value.text()
    assert STORED not in dialog.station_secret_value.text()


def test_page_poste_non_configuree(isolated_settings, secrets):
    secrets["value"] = ""
    dialog = _prefs()
    assert dialog.station_url_value.text() == "non configuré"
    assert dialog.station_counter_value.text() == "non configuré"
    assert "Aucun secret" in dialog.station_secret_value.text()


def test_modifier_le_poste_demande_confirmation(isolated_settings, secrets, monkeypatch):
    opened = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    monkeypatch.setattr(preferences, "StationDialog",
                        lambda parent: opened.append(parent))
    dialog = _prefs()
    emitted = []
    dialog.station_changed.connect(lambda: emitted.append(True))
    dialog.edit_station_button.click()
    assert opened == [] and emitted == []


def test_modifier_le_poste_confirme_et_enregistre_emet_station_changed(
        isolated_settings, secrets, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    class FakeStation:
        def __init__(self, parent):
            isolated_settings.setValue("web_url", "https://nouveau.example")

        def exec(self):
            return QDialog.Accepted
    monkeypatch.setattr(preferences, "StationDialog", FakeStation)
    dialog = _prefs()
    emitted = []
    dialog.station_changed.connect(lambda: emitted.append(True))
    dialog.edit_station_button.click()
    assert emitted == [True]
    assert dialog.station_url_value.text() == "https://nouveau.example"


def test_modifier_le_poste_annule_n_emet_rien(isolated_settings, secrets, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(preferences, "StationDialog", lambda parent: types.SimpleNamespace(
        exec=lambda: QDialog.Rejected))
    dialog = _prefs()
    emitted = []
    dialog.station_changed.connect(lambda: emitted.append(True))
    dialog.edit_station_button.click()
    assert emitted == []


# --- main.py ------------------------------------------------------------------

def _fake_station(code, opened):
    class FakeStation:
        def __init__(self, parent):
            opened.append(parent)

        def exec(self):
            return code
    return FakeStation


def test_show_station_dialog_applique_si_enregistre(monkeypatch):
    import main
    opened = []
    monkeypatch.setattr(main, "StationDialog", _fake_station(QDialog.Accepted, opened))
    w = types.SimpleNamespace(applied=0)
    w.apply_preferences = lambda: setattr(w, "applied", w.applied + 1)
    main.MainWindow.show_station_dialog(w)
    assert opened == [w] and w.applied == 1

    monkeypatch.setattr(main, "StationDialog", _fake_station(QDialog.Rejected, opened))
    main.MainWindow.show_station_dialog(w)
    assert w.applied == 1


def test_premier_demarrage_ouvre_la_configuration_du_poste(monkeypatch):
    """Configuration incomplète au démarrage : c'est la fenêtre du poste qui
    s'ouvre, plus toute la fenêtre de préférences."""
    import main
    opened = []
    monkeypatch.setattr(main, "StationDialog", _fake_station(QDialog.Accepted, opened))
    monkeypatch.setattr(main, "PreferencesDialog",
                        lambda *a: pytest.fail("préférences ouvertes au lieu du poste"))
    w = types.SimpleNamespace(loading_screen=None, logger=main.logging.getLogger("t"),
                              started=0)

    def _load():
        w.web_url, w.counter_id = "https://s.example", 2
    w.load_preferences = _load
    w._start_startup_sequence = lambda: setattr(w, "started", w.started + 1)
    main.MainWindow._require_valid_counter_id(w)
    assert opened == [w] and w.started == 1


# --- Petits écrans : corps défilant, boutons toujours visibles ----------------

def test_corps_defilant_et_boutons_hors_defilement(isolated_settings, secrets, monkeypatch):
    from PySide6.QtWidgets import QScrollArea
    dialog = _station(monkeypatch)
    scroll = dialog.findChild(QScrollArea)
    assert scroll is not None
    # Les champs défilent…
    assert scroll.widget().isAncestorOf(dialog.url_input)
    assert scroll.widget().isAncestorOf(dialog.counter_combobox)
    # … mais « Enregistrer / Annuler » reste toujours visible, hors défilement.
    assert not scroll.isAncestorOf(dialog.button_box)
    assert dialog.layout().indexOf(dialog.button_box) == dialog.layout().count() - 1
