"""Identité applicative et reprise des réglages hérités (point 10.5).

L'ancienne identité d'exemple (« MyCompany2 » / « PySide6 Web Browser Example2 »)
déterminait l'emplacement de QSettings : la renommer sans reprise ferait
redémarrer chaque poste avec une configuration vierge (URL du serveur et
comptoir perdus). Ces tests vérifient les deux moitiés du renommage.
"""

import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_identity import (  # noqa: E402
    APPLICATION_NAME, LEGACY_IDENTITIES, MIGRATION_MARKER_KEY, ORGANIZATION_NAME,
    apply_identity, legacy_sources, migrate_legacy_settings,
)

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeSettings:
    """Double de QSettings limité aux méthodes utilisées par la migration."""

    def __init__(self, data=None):
        self._data = dict(data or {})

    def value(self, key, default=None):
        return self._data.get(key, default)

    def setValue(self, key, value):
        self._data[key] = value

    def allKeys(self):
        return list(self._data)

    def contains(self, key):
        return key in self._data


LEGACY_CONFIG = {
    "web_url": "https://pharma.example.com",
    "counter_id": 2,
    "pause_shortcut": "Alt+P",
    "notification_duration": 8,
}


# --- identité ---------------------------------------------------------------

def test_identite_sans_residu_d_exemple():
    for value in (ORGANIZATION_NAME, APPLICATION_NAME):
        assert "Example" not in value
        assert "MyCompany" not in value
    assert ORGANIZATION_NAME == "PharmaFile"


def test_apply_identity_pose_les_deux_noms():
    class FakeApp:
        def __init__(self):
            self.org = self.name = self.domain = None
        def setOrganizationName(self, v):
            self.org = v
        def setApplicationName(self, v):
            self.name = v
        def setOrganizationDomain(self, v):  # ne doit pas être appelé
            self.domain = v

    app = FakeApp()
    apply_identity(app)
    assert (app.org, app.name) == (ORGANIZATION_NAME, APPLICATION_NAME)
    # Le domaine fictif « mycompany.com » n'est plus déclaré (il primerait sur le
    # nom d'organisation sous macOS).
    assert app.domain is None


def test_ancienne_identite_conservee_comme_source_de_reprise():
    assert ("MyCompany2", "PySide6 Web Browser Example2") in LEGACY_IDENTITIES


def test_plus_aucune_identite_d_exemple_dans_main():
    with open(os.path.join(APP_DIR, "main.py"), encoding="utf-8") as fh:
        source = fh.read()
    assert "PySide6 Web Browser Example" not in source
    assert "MyCompany2" not in source
    assert "mycompany.com" not in source


# --- reprise des réglages ---------------------------------------------------

def test_reprise_des_reglages_herites():
    target = FakeSettings()
    label = migrate_legacy_settings(target, [("ancienne", FakeSettings(LEGACY_CONFIG))])
    assert label == "ancienne"
    for key, value in LEGACY_CONFIG.items():
        assert target.value(key) == value


def test_reprise_idempotente():
    """Un deuxième démarrage ne doit plus rien copier."""
    source = FakeSettings(LEGACY_CONFIG)
    target = FakeSettings()
    migrate_legacy_settings(target, [("ancienne", source)])
    target.setValue("web_url", "https://nouveau.example.com")
    assert migrate_legacy_settings(target, [("ancienne", source)]) is None
    assert target.value("web_url") == "https://nouveau.example.com"


def test_reglage_supprime_ne_ressuscite_pas():
    """Après reprise, une clé effacée par l'utilisateur ne doit pas revenir."""
    source = FakeSettings(LEGACY_CONFIG)
    target = FakeSettings()
    migrate_legacy_settings(target, [("ancienne", source)])
    del target._data["counter_id"]
    migrate_legacy_settings(target, [("ancienne", source)])
    assert not target.contains("counter_id")


def test_valeur_deja_presente_jamais_ecrasee():
    target = FakeSettings({"web_url": "https://deja-configure.example.com"})
    migrate_legacy_settings(target, [("ancienne", FakeSettings(LEGACY_CONFIG))])
    assert target.value("web_url") == "https://deja-configure.example.com"
    assert target.value("counter_id") == 2   # les autres clés sont bien reprises


def test_premiere_installation_pose_le_temoin():
    target = FakeSettings()
    assert migrate_legacy_settings(target, [("ancienne", FakeSettings())]) is None
    assert target.contains(MIGRATION_MARKER_KEY)


def test_source_vide_ignoree_au_profit_de_la_suivante():
    target = FakeSettings()
    label = migrate_legacy_settings(target, [
        ("recente", FakeSettings()),
        ("plus_ancienne", FakeSettings(LEGACY_CONFIG)),
    ])
    assert label == "plus_ancienne"
    assert target.value("web_url") == LEGACY_CONFIG["web_url"]


def test_le_temoin_n_est_pas_recopie():
    source = FakeSettings(dict(LEGACY_CONFIG, **{MIGRATION_MARKER_KEY: "autre"}))
    target = FakeSettings()
    migrate_legacy_settings(target, [("ancienne", source)])
    assert target.value(MIGRATION_MARKER_KEY) == "ancienne"


def test_legacy_sources_construit_un_settings_par_identite():
    built = []

    def factory(org, app):
        built.append((org, app))
        return FakeSettings()

    sources = legacy_sources(factory)
    assert built == list(LEGACY_IDENTITIES)
    assert [label for label, _ in sources] == [f"{o}/{a}" for o, a in LEGACY_IDENTITIES]


# --- intégration avec un vrai QSettings ------------------------------------

def test_reprise_sur_de_vrais_qsettings(tmp_path):
    """Bout en bout avec QSettings (format INI, sans toucher au registre)."""
    from PySide6.QtCore import QSettings

    legacy_file = str(tmp_path / "legacy.ini")
    target_file = str(tmp_path / "target.ini")

    legacy = QSettings(legacy_file, QSettings.IniFormat)
    for key, value in LEGACY_CONFIG.items():
        legacy.setValue(key, value)
    legacy.sync()

    target = QSettings(target_file, QSettings.IniFormat)
    label = migrate_legacy_settings(target, [("ancienne", legacy)])
    target.sync()
    assert label == "ancienne"

    relu = QSettings(target_file, QSettings.IniFormat)
    assert relu.value("web_url") == LEGACY_CONFIG["web_url"]
    # Les types restent exploitables par settings_schema.read (lecture typée).
    assert relu.value("counter_id", type=int) == 2
    assert relu.value("notification_duration", type=int) == 8
    # L'ancien emplacement est laissé intact (retour arrière possible).
    assert QSettings(legacy_file, QSettings.IniFormat).value("web_url") == LEGACY_CONFIG["web_url"]


def test_setup_application_applique_identite_et_reprise(monkeypatch):
    """main.setup_application enchaîne bien identité PUIS reprise (l'ordre
    importe : QSettings() ne pointe au bon endroit qu'après l'identité)."""
    import main

    ordre = []

    class FakeApp:
        def setOrganizationName(self, v):
            ordre.append(("org", v))
        def setApplicationName(self, v):
            ordre.append(("app", v))

    monkeypatch.setattr(main, "migrate_legacy_settings",
                        lambda target, sources: ordre.append(("migration", None)))
    monkeypatch.setattr(main, "legacy_sources", lambda factory: [])
    main.setup_application(FakeApp())
    assert [step for step, _ in ordre] == ["org", "app", "migration"]
