"""Profil de préférences par équipier — construction et application.

Le profil suit la PERSONNE d'un poste à l'autre : raccourcis, notifications,
disposition, volume, politique de récupération… Jamais l'identité du poste —
adresse du serveur, comptoir, secret applicatif, reconnexion du dernier
occupant — qui décrit la machine et non l'équipier.

Module PUR : pas de Qt, pas de réseau, pas d'état. Il sait
- construire un payload depuis un instantané de réglages (build_payload) ;
- appliquer un payload à un objet « settings » (apply_payload), chaque valeur
  repassant par la normalisation du schéma : un profil entrant ne peut pas
  imposer une valeur hors plage ou d'un type inattendu ;
- décider la politique applicable (effective_policy) : celle transportée par
  le profil — personnelle — prime sur le réglage local du poste, qui ne sert
  que de valeur par défaut tant qu'aucun profil n'existe.
"""

from typing import Any, Dict, Optional

from settings_schema import SETTINGS, normalize_sync_policy, read as schema_read
from shortcut_defaults import SHORTCUT_DEFAULTS, read_shortcut

#: Version du format de payload échangé avec le serveur (incrémenter si la
#: forme change — le serveur la stocke telle quelle dans staff_preference).
PROFILE_PAYLOAD_VERSION = 1

#: Clés qui ne quittent JAMAIS le poste : elles décrivent la machine ou la
#: liaison serveur, pas la préférence de la personne. ``app_secret`` ne figure
#: dans aucun snapshot (magasin sécurisé), il est listé par défense en
#: profondeur si un futur code l'y ajoutait par mégarde.
PROFILE_LOCAL_KEYS = frozenset({
    "web_url",
    "counter_id",
    "app_secret",
    "last_staff_initials",
    "reconnect_last_user",
    # Zoom de l'interface : dépend de l'écran du poste, pas de la personne.
    "ui_scale",
})

#: Politiques de récupération du profil. « ask » = proposer au moment de la
#: connexion (défaut) ; « auto » = appliquer sans rien demander ; « never » =
#: ne jamais proposer (les boutons manuels des préférences restent utilisables).
POLICY_NEVER = "never"
POLICY_ASK = "ask"
POLICY_AUTO = "auto"
POLICY_VALUES = frozenset({POLICY_NEVER, POLICY_ASK, POLICY_AUTO})


def build_payload(values: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extrait d'un instantané de réglages les clés transportables.

    ``values`` : dict clé -> valeur (le snapshot du dialogue de préférences).
    Ne sont retenues que les clés connues — du schéma (hors identité du poste)
    ou des raccourcis déclarés : une clé inconnue ou locale n'embarque jamais.
    """
    payload = {}
    for key, value in (values or {}).items():
        if key in PROFILE_LOCAL_KEYS:
            continue
        if key in SETTINGS or key in SHORTCUT_DEFAULTS:
            payload[key] = value
    return payload


class DictSettings:
    """Adaptateur dict -> interface ``value``/``setValue`` attendue par
    ``settings_schema.read`` et ``shortcut_defaults.read_shortcut``.

    Permet de rejouer la normalisation du schéma sur un profil JSON
    téléchargé : les valeurs y passent par les mêmes conversions, bornes et
    normalisateurs que celles lues depuis QSettings.
    """

    def __init__(self, data: Optional[Dict[str, Any]]):
        self._data = dict(data or {})

    def value(self, key, default=None, type=None):
        if key not in self._data:
            return default
        raw = self._data[key]
        if raw is None:
            return default
        if type is bool:
            if isinstance(raw, str):
                return raw.strip().lower() in ("1", "true", "vrai", "oui", "yes")
            return bool(raw)
        if type is int:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default
        if type is str:
            return raw if isinstance(raw, str) else str(raw)
        return raw

    def setValue(self, key, value):
        self._data[key] = value


def apply_payload(payload: Optional[Dict[str, Any]], target) -> int:
    """Écrit dans ``target`` les réglages PRÉSENTS dans le profil, normalisés.

    ``target`` expose ``setValue`` (QSettings, DictSettings en test).
    Seules les clés transportables réellement présentes sont écrites : un
    profil partiel n'écrase pas les réglages locaux qu'il ne mentionne pas,
    et les clés du poste sont ignorées même si un client les avait envoyées.
    Retourne le nombre de clés écrites."""
    shim = DictSettings(payload)
    written = 0
    for key in (payload or {}):
        if key in PROFILE_LOCAL_KEYS:
            continue
        if key in SETTINGS:
            target.setValue(key, schema_read(shim, key))
            written += 1
        elif key in SHORTCUT_DEFAULTS:
            target.setValue(key, read_shortcut(shim, key))
            written += 1
    return written


def effective_policy(payload: Optional[Dict[str, Any]],
                     local_policy: Any) -> str:
    """Politique applicable : celle du profil si elle y est définie (elle suit
    la personne), sinon le réglage local du poste. Retourne toujours l'une des
    valeurs de ``POLICY_VALUES``."""
    remote = (payload or {}).get("profile_sync_policy")
    if remote in POLICY_VALUES:
        return remote
    return normalize_sync_policy(local_policy)
