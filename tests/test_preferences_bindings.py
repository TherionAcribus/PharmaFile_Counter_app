"""Table unique réglage -> champ des préférences (_bindings).

Chargement, valeurs par défaut, profil et enregistrement dérivent de cette
table. Ces tests en font un cliquet :
- elle couvre EXACTEMENT les réglages du schéma (hors adresse du serveur,
  propre au poste) et les raccourcis : ajouter une clé au schéma sans champ
  (ou l'inverse) échoue ici au lieu de casser l'enregistrement en silence ;
- chaque valeur enregistrée est relue telle quelle par son champ ;
- les plages des champs numériques sont celles du schéma ;
- les listes déroulantes enregistrent la valeur, jamais le libellé.
"""

import os
import sys

import pytest
from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QSlider, QSpinBox, QWidget

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import preferences  # noqa: E402
import settings_schema  # noqa: E402
from accessibility import TONE_HUMOROUS, TONE_SOBER  # noqa: E402
from shortcut_config import MODE_DISABLED, MODE_FOCUSED  # noqa: E402
from shortcut_defaults import SHORTCUT_DEFAULTS  # noqa: E402

#: Réglages du schéma qui ne sont PAS dans le formulaire des préférences.
NOT_IN_FORM = {"web_url"}   # configuration du poste (StationDialog)


@pytest.fixture
def isolated_settings(_shared_qapplication):
    organization = QCoreApplication.organizationName()
    application = QCoreApplication.applicationName()
    QCoreApplication.setOrganizationName("AppComptoirTests")
    QCoreApplication.setApplicationName("test_preferences_bindings")
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


def test_table_couvre_exactement_le_schema_et_les_raccourcis(isolated_settings):
    dialog = _dialog()
    keys = [key for key, _widget, _page in dialog._bindings]
    assert len(keys) == len(set(keys)), "clé liée deux fois"
    expected = (set(settings_schema.SETTINGS) - NOT_IN_FORM) | set(SHORTCUT_DEFAULTS)
    assert set(keys) == expected
    assert set(dialog._form_settings()) == expected


def test_chaque_champ_n_est_lie_qu_une_fois(isolated_settings):
    dialog = _dialog()
    widgets = [id(widget) for _key, widget, _page in dialog._bindings]
    assert len(widgets) == len(set(widgets))


def _non_default(key):
    """Une valeur valide différente du défaut, pour chaque type de réglage."""
    spec = settings_schema.SETTINGS[key]
    default = spec.default
    if isinstance(default, bool):
        return not default
    if isinstance(default, int):
        low, high = settings_schema.bounds(key)
        return high if default != high else low
    alternatives = {
        "shortcut_mode": MODE_DISABLED if default != MODE_DISABLED else MODE_FOCUSED,
        "profile_sync_policy": "auto",
        "message_tone": TONE_HUMOROUS if default != TONE_HUMOROUS else TONE_SOBER,
        "notification_corner": "top-right",
        "patient_list_vertical_position": "right",
        "patient_list_horizontal_position": "bottom",
        "messaging_dock_area": "right",
        "selected_skin": "Darkeum",
    }
    return alternatives[key]


def test_chaque_valeur_enregistree_est_relue_par_son_champ(isolated_settings, monkeypatch):
    # Pas d'application réelle du skin pendant le test.
    monkeypatch.setattr(preferences.resources, "apply_skin", lambda *a, **k: True)
    stored = {}
    for key in set(settings_schema.SETTINGS) - NOT_IN_FORM:
        stored[key] = _non_default(key)
        isolated_settings.setValue(key, stored[key])
    for name in SHORTCUT_DEFAULTS:
        stored[name] = "Ctrl+Maj+F9" if name == "pause_shortcut" else SHORTCUT_DEFAULTS[name]
        isolated_settings.setValue(name, stored[name])
    form = _dialog()._form_settings()
    for key, value in stored.items():
        assert form[key] == value, key


def test_plages_des_champs_numeriques_issues_du_schema(isolated_settings):
    dialog = _dialog()
    for key, widget, _page in dialog._bindings:
        if isinstance(widget, (QSpinBox, QSlider)):
            assert (widget.minimum(), widget.maximum()) == settings_schema.bounds(key), key
    assert (dialog.volume_spinbox.minimum(), dialog.volume_spinbox.maximum()) \
        == settings_schema.bounds("notification_volume")


def test_taille_de_liste_alignee_sur_le_schema(isolated_settings):
    """Régression : le champ s'arrêtait à 24 pt alors que le schéma acceptait
    72 ; une valeur de 30 était affichée 24 puis réenregistrée sans prévenir."""
    isolated_settings.setValue("patient_list_font_size", 30)
    dialog = _dialog()
    assert dialog.patient_list_font_size_spinbox.value() == settings_schema.MAX_LIST_FONT_SIZE
    assert dialog._form_settings()["patient_list_font_size"] \
        == settings_schema.read(isolated_settings, "patient_list_font_size")


def test_positions_enregistrent_la_valeur_pas_le_libelle(isolated_settings):
    dialog = _dialog()
    combo = dialog.messaging_position
    combo.setCurrentIndex(combo.findData("right"))
    assert combo.currentText() == "Droite"
    assert dialog._form_settings()["messaging_dock_area"] == "right"


def test_valeur_inconnue_de_liste_retombe_sur_le_defaut(isolated_settings):
    isolated_settings.setValue("notification_corner", "au-milieu")
    dialog = _dialog()
    assert dialog._form_settings()["notification_corner"] \
        == settings_schema.default("notification_corner")


def test_profil_ne_touche_pas_aux_cles_du_poste(isolated_settings):
    """reconnect_last_user décrit le poste (PROFILE_LOCAL_KEYS) : un profil,
    même s'il en contenait une valeur, ne la modifie pas."""
    dialog = _dialog()
    before = dialog.reconnect_last_user_checkbox.isChecked()
    dialog._apply_profile_to_form({"reconnect_last_user": not before,
                                   "notification_duration": 42})
    assert dialog.reconnect_last_user_checkbox.isChecked() == before
    assert dialog.notification_duration_spinbox.value() == 42


def test_valeurs_par_defaut_de_la_page_session(isolated_settings):
    dialog = _dialog()
    dialog.reconnect_last_user_checkbox.setChecked(
        not settings_schema.default("reconnect_last_user"))
    dialog.notification_duration_spinbox.setValue(42)
    dialog.navigation_list.setCurrentItem(dialog.session_item)
    dialog._reset_to_defaults(current_page_only=True)
    assert dialog.reconnect_last_user_checkbox.isChecked() \
        == settings_schema.default("reconnect_last_user")
    assert dialog.notification_duration_spinbox.value() == 42   # autre page intacte
