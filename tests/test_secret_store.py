"""Tests du stockage sécurisé du secret applicatif (secret_store).

Vérifie :
- migration automatique d'un secret hérité en clair (QSettings) vers keyring,
  avec effacement de la copie en clair ;
- reprise d'un secret enregistré sous un ancien namespace de service
  (renommage de marque) vers le namespace courant ;
- priorité au magasin sécurisé ;
- repli sur QSettings quand keyring est indisponible (l'app ne doit jamais
  casser) ;
- absence de secret => chaîne vide.

secret_store n'importe pas PySide : on utilise un faux QSettings minimal et on
simule keyring en monkeypatchant la couche interne
_keyring_get_service/_keyring_set.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import secret_store  # noqa: E402
from secret_store import load_secret, save_secret  # noqa: E402


class FakeSettings:
    """Imite l'API QSettings utilisée par secret_store : value/setValue/remove."""

    def __init__(self, initial=None):
        self._data = dict(initial or {})

    def value(self, key, default=None):
        return self._data.get(key, default)

    def setValue(self, key, val):
        self._data[key] = val

    def remove(self, key):
        self._data.pop(key, None)


@pytest.fixture
def fake_keyring(monkeypatch):
    """Keyring en mémoire (disponible), indexé par namespace de service."""
    store = {}
    monkeypatch.setattr(secret_store, "_keyring_get_service",
                        lambda service: store.get(service))

    def _set(value):
        store[secret_store.SERVICE_NAME] = value
        return True

    monkeypatch.setattr(secret_store, "_keyring_set", _set)
    return store


@pytest.fixture
def broken_keyring(monkeypatch):
    """Keyring indisponible : get renvoie None, set échoue toujours."""
    monkeypatch.setattr(secret_store, "_keyring_get_service", lambda service: None)
    monkeypatch.setattr(secret_store, "_keyring_set", lambda value: False)


def test_save_then_load_uses_keyring(fake_keyring):
    settings = FakeSettings()
    # save_secret renvoie True quand le stockage sécurisé a réussi.
    assert save_secret(settings, "mon-secret") is True
    # Rien en clair dans QSettings.
    assert settings.value("app_secret") is None
    assert fake_keyring[secret_store.SERVICE_NAME] == "mon-secret"
    assert load_secret(settings) == "mon-secret"


def test_legacy_plaintext_is_migrated_and_erased(fake_keyring):
    # Ancienne installation : secret en clair dans QSettings, rien dans keyring.
    settings = FakeSettings({"app_secret": "secret-hérité"})
    result = load_secret(settings)
    assert result == "secret-hérité"
    # Migré vers keyring...
    assert fake_keyring[secret_store.SERVICE_NAME] == "secret-hérité"
    # ...et effacé de QSettings.
    assert settings.value("app_secret") is None


def test_legacy_service_name_is_migrated(fake_keyring):
    """Un secret enregistré sous l'ancien namespace (« GestionFile-AppComptoir »,
    avant le renommage Flowelis) est recopié sous le namespace courant ; la
    copie d'origine est conservée (retour à une version antérieure possible)."""
    fake_keyring["GestionFile-AppComptoir"] = "secret-ancien-nom"
    assert load_secret(FakeSettings()) == "secret-ancien-nom"
    assert fake_keyring[secret_store.SERVICE_NAME] == "secret-ancien-nom"
    assert fake_keyring["GestionFile-AppComptoir"] == "secret-ancien-nom"


def test_keyring_value_takes_priority_over_legacy(fake_keyring):
    fake_keyring[secret_store.SERVICE_NAME] = "depuis-keyring"
    settings = FakeSettings({"app_secret": "en-clair-obsolete"})
    assert load_secret(settings) == "depuis-keyring"


def test_empty_when_nothing_stored(fake_keyring):
    assert load_secret(FakeSettings()) == ""


def test_fallback_to_qsettings_when_keyring_broken(broken_keyring):
    settings = FakeSettings()
    # keyring HS => repli sur QSettings, mais save_secret le SIGNALE (False) au
    # lieu d'accepter silencieusement le stockage en clair.
    assert save_secret(settings, "repli") is False
    assert settings.value("app_secret") == "repli"
    assert load_secret(settings) == "repli"
