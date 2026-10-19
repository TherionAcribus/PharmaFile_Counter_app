"""Profil de préférences par équipier — client.

Trois couches testées :

1. noyau pur ``profile_sync`` : filtrage des clés locales (identité du poste),
   conversion DictSettings, application normalisée d'un payload, politique
   effective (celle du profil — personnelle — prime sur le réglage local) ;
2. dialogue : boutons envoyer/récupérer (état selon équipier connecté),
   envoi du payload filtré, réception versée dans le formulaire sans
   persistance, 404 distingués (profil absent vs serveur trop ancien) ;
3. fenêtre : recherche du profil à la connexion (dédupliquée par équipier),
   politique « auto » (application silencieuse), « never » (rien), « ask »
   (question avec option « ne plus proposer »).
"""

import logging
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402
import preferences  # noqa: E402
import profile_sync  # noqa: E402
import settings_schema  # noqa: E402
from shortcut_defaults import SHORTCUT_DEFAULTS  # noqa: E402


@pytest.fixture
def isolated_settings(_shared_qapplication):
    """QSettings propre à ces tests (jamais la configuration réelle du poste)."""
    from PySide6.QtCore import QCoreApplication, QSettings
    organization = QCoreApplication.organizationName()
    application = QCoreApplication.applicationName()
    QCoreApplication.setOrganizationName("AppComptoirTests")
    QCoreApplication.setApplicationName("test_profile_sync")
    settings = QSettings()
    settings.clear()
    yield settings
    settings.clear()
    settings.sync()
    QCoreApplication.setOrganizationName(organization)
    QCoreApplication.setApplicationName(application)


# ---------------------------------------------------------------------------
# 1. Noyau pur
# ---------------------------------------------------------------------------

def test_build_payload_exclut_identite_du_poste():
    form = {
        "web_url": "https://srv",
        "counter_id": 3,
        "app_secret": "secret",
        "last_staff_initials": "JJ",
        "reconnect_last_user": True,
        "notification_volume": 80,
        "always_on_top": False,
        "profile_sync_policy": "auto",
        "next_patient_shortcut": "Alt+X",
        "cle_inconnue": "n'importe quoi",
    }
    payload = profile_sync.build_payload(form)
    assert "web_url" not in payload
    assert "counter_id" not in payload
    assert "app_secret" not in payload
    assert "last_staff_initials" not in payload
    assert "reconnect_last_user" not in payload
    assert "cle_inconnue" not in payload
    # Les clés personnelles et les raccourcis passent.
    assert payload["notification_volume"] == 80
    assert payload["always_on_top"] is False
    assert payload["profile_sync_policy"] == "auto"
    assert payload["next_patient_shortcut"] == "Alt+X"


def test_build_payload_tolere_none_et_vide():
    assert profile_sync.build_payload(None) == {}
    assert profile_sync.build_payload({}) == {}


def test_dict_settings_convertit_les_types():
    shim = profile_sync.DictSettings({
        "flag": "true", "nb": "42", "txt": 7, "flag2": "oui"})
    assert shim.value("flag", False, type=bool) is True
    assert shim.value("flag2", False, type=bool) is True
    assert shim.value("nb", 0, type=int) == 42
    assert shim.value("nb_invalide", 9, type=int) == 9
    assert shim.value("txt", "", type=str) == "7"
    assert shim.value("absent", "def") == "def"


def test_apply_payload_n_ecrit_que_les_cles_presentes_et_normalisees():
    target = profile_sync.DictSettings({})
    payload = {
        "notification_volume": 5000,       # hors plage -> borné à 100
        "web_url": "https://espion",        # clé locale -> ignorée
        "pause_shortcut": "Alt+Z",          # raccourci -> écrit
        "inconnue": 1,                      # non déclarée -> ignorée
    }
    written = profile_sync.apply_payload(payload, target)
    assert written == 2
    assert target.value("notification_volume") == 100
    assert target.value("pause_shortcut") == "Alt+Z"
    assert "web_url" not in target._data
    assert "inconnue" not in target._data


def test_apply_payload_ignore_les_cles_locales_meme_presentes():
    target = profile_sync.DictSettings({})
    profile_sync.apply_payload(
        {"counter_id": 9, "reconnect_last_user": False}, target)
    assert "counter_id" not in target._data
    assert "reconnect_last_user" not in target._data


def test_effective_policy_profil_prime_sur_local():
    assert profile_sync.effective_policy(
        {"profile_sync_policy": "never"}, "auto") == "never"
    assert profile_sync.effective_policy(
        {"profile_sync_policy": "auto"}, "never") == "auto"


def test_effective_policy_repli_sur_local_puis_ask():
    assert profile_sync.effective_policy({}, "never") == "never"
    assert profile_sync.effective_policy(None, "auto") == "auto"
    # Valeur invalide locale -> « ask » (défaut).
    assert profile_sync.effective_policy({}, "bizarre") == "ask"
    # Politique du profil invalide -> ignorée, le local fait foi.
    assert profile_sync.effective_policy(
        {"profile_sync_policy": "bizarre"}, "never") == "never"


# ---------------------------------------------------------------------------
# 2. Dialogue — boutons et versement du profil dans le formulaire
# ---------------------------------------------------------------------------

class FakeApi:
    """API réseau simulée : capture les appels profil."""

    def __init__(self):
        self.uploads = []
        self.fetches = []

    def upload_staff_preferences(self, staff_id, payload, on_result=None):
        self.uploads.append((staff_id, payload))

    def fetch_staff_preferences(self, staff_id, on_result=None):
        self.fetches.append(staff_id)
        self.on_result = on_result


def _dialog(isolated_settings, staff_id=None):
    from PySide6.QtWidgets import QWidget
    parent = QWidget()
    parent.staff_id = staff_id
    parent.api = FakeApi()
    dialog = preferences.PreferencesDialog(parent)
    dialog._parent_ref = parent  # survie du parent (cf. fixture existante)
    return dialog


def test_boutons_profil_grises_sans_equipier(isolated_settings):
    dialog = _dialog(isolated_settings, staff_id=None)
    assert not dialog.profile_send_button.isEnabled()
    assert not dialog.profile_fetch_button.isEnabled()
    assert "initiales" in dialog.profile_hint_label.text()


def test_boutons_profil_actifs_avec_equipier(isolated_settings):
    dialog = _dialog(isolated_settings, staff_id=7)
    assert dialog.profile_send_button.isEnabled()
    assert dialog.profile_fetch_button.isEnabled()
    assert dialog.profile_hint_label.text() == ""


def test_envoi_du_payload_filtre_au_bon_equipier(isolated_settings):
    dialog = _dialog(isolated_settings, staff_id=7)
    dialog.url_input.setText("https://serveur.example")
    dialog._send_profile()
    api = dialog.parent().api
    assert len(api.uploads) == 1
    staff_id, payload = api.uploads[0]
    assert staff_id == 7
    # Jamais l'identité du poste.
    assert "web_url" not in payload
    assert "counter_id" not in payload
    assert "app_secret" not in payload
    # Les réglages personnels du formulaire, oui.
    assert "profile_sync_policy" in payload
    assert "notification_volume" in payload
    for name in SHORTCUT_DEFAULTS:
        assert name in payload


def test_reception_verse_le_profil_dans_le_formulaire(isolated_settings):
    dialog = _dialog(isolated_settings, staff_id=7)
    dialog._fetch_profile()
    assert dialog.parent().api.fetches == [7]
    result = types.SimpleNamespace(
        status=200,
        data={"payload": {"notification_volume": 88,
                          "profile_sync_policy": "auto"}})
    dialog._on_profile_fetched(result)
    assert dialog.volume_slider.value() == 88
    assert dialog.profile_policy_combo.currentData() == "auto"
    # Rien de persisté : la confirmation reste à l'utilisateur.
    assert isolated_settings.value("notification_volume") is None
    assert "Enregistrer" in dialog.profile_status_label.text()


def test_404_profil_absent_vs_serveur_ancien(isolated_settings):
    dialog = _dialog(isolated_settings, staff_id=7)
    dialog._on_profile_fetched(types.SimpleNamespace(
        status=404, data={"error": "no_preferences"}))
    assert "Aucun profil" in dialog.profile_status_label.text()
    dialog._on_profile_fetched(types.SimpleNamespace(
        status=404, data=None))
    assert "indisponible" in dialog.profile_status_label.text()


def test_envoi_404_serveur_ancien_signale(isolated_settings):
    dialog = _dialog(isolated_settings, staff_id=7)
    dialog._on_profile_sent(types.SimpleNamespace(status=404, data=None))
    assert "indisponible" in dialog.profile_status_label.text()
    dialog._on_profile_sent(types.SimpleNamespace(
        status=200, data={"staff_id": 7}))
    assert "enregistrées" in dialog.profile_status_label.text()


# ---------------------------------------------------------------------------
# 3. Fenêtre — recherche à la connexion et politique
# ---------------------------------------------------------------------------

class _FakeMainApi:
    def __init__(self):
        self.fetches = []

    def fetch_staff_preferences(self, staff_id, on_result=None):
        self.fetches.append(staff_id)
        self.on_result = on_result


def _win(staff_id):
    """Fausse MainWindow avec les vraies méthodes de profil."""
    w = types.SimpleNamespace(
        staff_id=staff_id,
        _profile_checked_staff=set(),
        _profile_pending_staff=None,
        api=_FakeMainApi(),
        logger=logging.getLogger("test.profile_sync"),
        applied=[],
    )
    w._maybe_fetch_staff_profile = types.MethodType(
        main.MainWindow._maybe_fetch_staff_profile, w)
    w._on_staff_profile_result = types.MethodType(
        main.MainWindow._on_staff_profile_result, w)
    w._apply_staff_profile = types.MethodType(
        main.MainWindow._apply_staff_profile, w)
    w._show_inline_notice = lambda msg: w.applied.append(("notice", msg))
    w.apply_preferences = lambda: w.applied.append(("apply",))
    return w


def test_recherche_profil_une_seule_fois_par_equipier(isolated_settings):
    w = _win(staff_id=7)
    w._maybe_fetch_staff_profile()
    w._maybe_fetch_staff_profile()
    assert w.api.fetches == [7]


def test_recherche_profil_sans_equipier_ne_fait_rien(isolated_settings):
    w = _win(staff_id=None)
    w._maybe_fetch_staff_profile()
    assert w.api.fetches == []


def test_politique_auto_applique_silencieusement(isolated_settings):
    w = _win(staff_id=7)
    w._maybe_fetch_staff_profile()
    result = types.SimpleNamespace(
        status=200,
        data={"payload": {"notification_volume": 33,
                          "profile_sync_policy": "auto"}})
    w._on_staff_profile_result(result)
    # Écrit dans QSettings (normalisé) puis appliqué à chaud.
    assert settings_schema.read(
        isolated_settings, "notification_volume") == 33
    assert ("apply",) in w.applied
    assert any(kind == "notice" for kind, *_ in w.applied)


def test_politique_never_ne_fait_rien(isolated_settings):
    w = _win(staff_id=7)
    w._maybe_fetch_staff_profile()
    result = types.SimpleNamespace(
        status=200,
        data={"payload": {"notification_volume": 33,
                          "profile_sync_policy": "never"}})
    w._on_staff_profile_result(result)
    assert w.applied == []
    assert isolated_settings.value("notification_volume") is None


def test_profil_absent_ne_fait_rien(isolated_settings):
    w = _win(staff_id=7)
    w._maybe_fetch_staff_profile()
    w._on_staff_profile_result(types.SimpleNamespace(
        status=404, data={"error": "no_preferences"}))
    assert w.applied == []


def _fake_question_box(choice_label):
    """QMessageBox de test : simule le clic sur le bouton ``choice_label``."""
    class FakeBox:
        # Constantes consultées par le code appelant (rôles Qt factices).
        Question = "question"
        YesRole = "yes"
        NoRole = "no"
        RejectRole = "reject"

        def __init__(self, parent=None):
            self._buttons = []

        def setIcon(self, icon):
            pass

        def setWindowTitle(self, title):
            pass

        def setText(self, text):
            pass

        def setInformativeText(self, text):
            pass

        def addButton(self, label, role):
            btn = types.SimpleNamespace(label=label)
            self._buttons.append(btn)
            return btn

        def exec(self):
            self._clicked = next(
                b for b in self._buttons if b.label == choice_label)

        def clickedButton(self):
            return self._clicked

    return FakeBox


def test_ask_propose_et_applique_si_accepte(isolated_settings, monkeypatch):
    w = _win(staff_id=7)
    w._maybe_fetch_staff_profile()
    monkeypatch.setattr(main, "QMessageBox", _fake_question_box("Appliquer"))
    result = types.SimpleNamespace(
        status=200,
        data={"payload": {"notification_volume": 41,
                          "profile_sync_policy": "ask"}})
    w._on_staff_profile_result(result)
    assert settings_schema.read(
        isolated_settings, "notification_volume") == 41
    assert ("apply",) in w.applied


def test_ask_ne_plus_proposer_desactive(isolated_settings, monkeypatch):
    w = _win(staff_id=7)
    w._maybe_fetch_staff_profile()
    monkeypatch.setattr(
        main, "QMessageBox", _fake_question_box("Ne plus me proposer"))
    result = types.SimpleNamespace(
        status=200,
        data={"payload": {"notification_volume": 41,
                          "profile_sync_policy": "ask"}})
    w._on_staff_profile_result(result)
    assert w.applied == []
    assert isolated_settings.value(
        "profile_prompt_opt_out", False, type=bool) is True


def test_ask_refuse_n_applique_pas(isolated_settings, monkeypatch):
    w = _win(staff_id=7)
    w._maybe_fetch_staff_profile()
    monkeypatch.setattr(main, "QMessageBox", _fake_question_box("Non"))
    result = types.SimpleNamespace(
        status=200,
        data={"payload": {"notification_volume": 41,
                          "profile_sync_policy": "ask"}})
    w._on_staff_profile_result(result)
    assert w.applied == []
    assert isolated_settings.value("notification_volume") is None


def test_reponse_d_un_autre_equipier_ignoree(isolated_settings):
    """Un changement d'équipier entre la requête et la réponse invalide le
    résultat : on n'applique pas le profil de l'un à la session de l'autre."""
    w = _win(staff_id=7)
    w._maybe_fetch_staff_profile()
    w.staff_id = 8  # changement entre-temps
    result = types.SimpleNamespace(
        status=200,
        data={"payload": {"notification_volume": 33,
                          "profile_sync_policy": "auto"}})
    w._on_staff_profile_result(result)
    assert w.applied == []
    assert isolated_settings.value("notification_volume") is None
