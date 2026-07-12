"""Stockage sécurisé du secret applicatif (app_secret).

Historique
----------
Le secret partagé était enregistré en clair dans QSettings, donc dans le
registre Windows (HKCU), lisible par n'importe quel programme s'exécutant sous
la session de l'utilisateur. On le stocke désormais via ``keyring``, qui utilise
le Gestionnaire d'identifiants Windows (Credential Manager, protégé par DPAPI)
sous Windows, le Trousseau sous macOS et Secret Service sous Linux.

Robustesse
----------
Si aucun backend ``keyring`` n'est disponible (import manquant, backend absent),
on retombe sur QSettings afin de ne jamais empêcher l'application de démarrer,
en journalisant un avertissement. Le stockage sécurisé est donc une amélioration
« best effort » : présent quand la plateforme le permet, transparent sinon.

Migration
---------
``load_secret`` migre automatiquement une éventuelle valeur héritée stockée en
clair dans QSettings : elle est déplacée vers keyring puis effacée de QSettings.
"""

import logging

logger = logging.getLogger(__name__)

# Namespace de l'entrée dans le magasin d'identifiants.
SERVICE_NAME = "GestionFile-AppComptoir"
SECRET_ENTRY = "app_secret"

# Clé QSettings historique (secret en clair) : lue pour migration, puis effacée.
_LEGACY_QSETTINGS_KEY = "app_secret"

try:  # keyring est optionnel : l'app doit fonctionner même sans lui.
    import keyring
    from keyring.errors import KeyringError, NoKeyringError
    # Un backend "null"/"fail" peut être présent sans réellement stocker :
    # on vérifiera à l'usage via les exceptions.
    _KEYRING_AVAILABLE = True
except Exception as exc:  # pragma: no cover - dépend de l'environnement
    keyring = None
    KeyringError = NoKeyringError = Exception
    _KEYRING_AVAILABLE = False
    logger.warning("keyring indisponible (%s) : repli sur QSettings pour le secret.", exc)


def _keyring_get():
    if not _KEYRING_AVAILABLE:
        return None
    try:
        return keyring.get_password(SERVICE_NAME, SECRET_ENTRY)
    except (KeyringError, Exception) as exc:  # pragma: no cover
        logger.warning("Lecture keyring impossible (%s) : repli sur QSettings.", exc)
        return None


def _keyring_set(value) -> bool:
    if not _KEYRING_AVAILABLE:
        return False
    try:
        keyring.set_password(SERVICE_NAME, SECRET_ENTRY, value)
        return True
    except (KeyringError, Exception) as exc:  # pragma: no cover
        logger.warning("Écriture keyring impossible (%s) : repli sur QSettings.", exc)
        return False


def load_secret(settings) -> str:
    """Retourne le secret applicatif, en le migrant depuis QSettings si besoin.

    ``settings`` est un QSettings (ou tout objet exposant ``value``/``setValue``/
    ``remove``). Priorité : keyring, puis valeur héritée en clair (migrée), sinon
    chaîne vide.
    """
    secret = _keyring_get()
    if secret:
        return secret

    # Migration éventuelle depuis l'ancien stockage en clair.
    legacy = settings.value(_LEGACY_QSETTINGS_KEY, "")
    legacy = legacy or ""
    if legacy:
        if _keyring_set(legacy):
            # Migration réussie : on efface la copie en clair.
            settings.remove(_LEGACY_QSETTINGS_KEY)
            logger.info("Secret applicatif migré de QSettings vers le magasin sécurisé.")
        return legacy

    return ""


def save_secret(settings, value) -> None:
    """Enregistre le secret dans le magasin sécurisé.

    En cas de succès keyring, on s'assure qu'aucune copie en clair ne subsiste
    dans QSettings. Si keyring échoue/est indisponible, on retombe sur QSettings
    (comportement historique) en journalisant un avertissement.
    """
    value = value or ""
    if _keyring_set(value):
        settings.remove(_LEGACY_QSETTINGS_KEY)
        return
    logger.warning("Secret applicatif stocké en clair dans QSettings (keyring indisponible).")
    settings.setValue(_LEGACY_QSETTINGS_KEY, value)
