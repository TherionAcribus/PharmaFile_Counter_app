"""Skin dans les préférences : aperçu, annulation, valeur enregistrée.

Régressions visées :
- « Pas de skin » était enregistré tel quel (libellé affiché) : au chargement,
  ``load_skin`` cherchait un fichier « Pas de skin.qss », avertissait, et
  gardait le skin précédent jusqu'au redémarrage ;
- l'aperçu ne touchait que la fenêtre principale alors que ``load_skin``
  applique le skin à toute l'application ;
- annuler sur une installation neuve (skin enregistré = "") laissait le skin
  essayé ;
- ouvrir le dialogue retirait puis remettait le skin (aperçu déclenché par le
  remplissage de la liste).
"""

import os
import sys

import pytest
from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication, QWidget

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import preferences  # noqa: E402
import resources  # noqa: E402

SENTINEL = "QWidget { color: #123456; }"


@pytest.fixture
def isolated_settings(_shared_qapplication):
    """QSettings propre à ces tests, et feuille de style de l'application
    rendue telle quelle à la fin (le QApplication est partagé)."""
    organization = QCoreApplication.organizationName()
    application = QCoreApplication.applicationName()
    QCoreApplication.setOrganizationName("AppComptoirTests")
    QCoreApplication.setApplicationName("test_preferences_skin")
    settings = QSettings()
    settings.clear()
    app_style = QApplication.instance().styleSheet()
    yield settings
    QApplication.instance().setStyleSheet(app_style)
    settings.clear()
    settings.sync()
    QCoreApplication.setOrganizationName(organization)
    QCoreApplication.setApplicationName(application)


def _dialog():
    parent = QWidget()
    dialog = preferences.PreferencesDialog(parent)
    dialog._test_parent = parent
    return dialog, parent


def _select(dialog, skin):
    index = dialog.skin_combo.findData(skin)
    assert index >= 0, skin
    dialog.skin_combo.setCurrentIndex(index)


def test_ouvrir_le_dialogue_n_applique_aucun_skin(isolated_settings):
    isolated_settings.setValue("selected_skin", "Darkeum")
    QApplication.instance().setStyleSheet(SENTINEL)
    dialog, parent = _dialog()
    assert dialog.skin_combo.currentData() == "Darkeum"
    assert QApplication.instance().styleSheet() == SENTINEL
    assert parent.styleSheet() == ""


def test_apercu_applique_le_skin_a_toute_l_application(isolated_settings):
    dialog, parent = _dialog()
    _select(dialog, "Darkeum")
    qss = resources.read_skin("Darkeum")
    assert QApplication.instance().styleSheet() == qss
    assert parent.styleSheet() == qss


def test_annuler_sur_installation_neuve_retire_le_skin_essaye(isolated_settings):
    """Skin enregistré = "" (défaut) : l'annulation doit retirer l'aperçu."""
    dialog, parent = _dialog()
    _select(dialog, "Darkeum")
    dialog.reject()
    assert QApplication.instance().styleSheet() == ""
    assert parent.styleSheet() == ""


def test_annuler_rend_le_skin_enregistre(isolated_settings):
    isolated_settings.setValue("selected_skin", "Darkeum")
    dialog, parent = _dialog()
    _select(dialog, resources.NO_SKIN)
    assert QApplication.instance().styleSheet() == ""
    dialog.reject()
    assert QApplication.instance().styleSheet() == resources.read_skin("Darkeum")


def test_annuler_sans_apercu_ne_touche_pas_au_style(isolated_settings):
    QApplication.instance().setStyleSheet(SENTINEL)
    dialog, _parent = _dialog()
    dialog.reject()
    assert QApplication.instance().styleSheet() == SENTINEL


def test_annuler_skin_enregistre_disparu_revient_au_style_standard(isolated_settings):
    """Skin enregistré dont le fichier a disparu : au démarrage rien n'était
    appliqué — l'annulation revient donc au style standard, pas à l'aperçu."""
    isolated_settings.setValue("selected_skin", "SkinDisparu")
    dialog, _parent = _dialog()
    _select(dialog, "Darkeum")
    dialog.reject()
    assert QApplication.instance().styleSheet() == ""


def test_aucun_skin_est_enregistre_comme_chaine_vide(isolated_settings):
    dialog, _parent = _dialog()
    _select(dialog, resources.NO_SKIN)
    assert dialog._form_settings()["selected_skin"] == ""
    _select(dialog, "Darkeum")
    assert dialog._form_settings()["selected_skin"] == "Darkeum"


def test_ancien_libelle_enregistre_est_relu_comme_aucun_skin(isolated_settings):
    isolated_settings.setValue("selected_skin", "Pas de skin")
    dialog, _parent = _dialog()
    assert dialog.skin_combo.currentData() == resources.NO_SKIN
    assert dialog.current_skin == ""
    assert dialog._form_settings()["selected_skin"] == ""


def test_enregistrer_consomme_l_apercu(isolated_settings):
    """Après enregistrement, le skin choisi devient la référence : une
    annulation ultérieure (« Appliquer » puis « Annuler ») ne le défait pas."""
    dialog, _parent = _dialog()
    _select(dialog, "Darkeum")
    dialog._pending_save = {
        "settings": {"notification_volume": 50, "notification_muted": False,
                     "selected_skin": "Darkeum", "web_url": "https://exemple.test"},
        "app_secret": "",
        "close": False,
    }
    dialog._finalize_save()
    dialog.reject()
    assert QApplication.instance().styleSheet() == resources.read_skin("Darkeum")
