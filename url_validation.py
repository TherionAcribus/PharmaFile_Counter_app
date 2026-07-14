"""Validation et normalisation de l'URL du serveur (point 8).

Module pur (sans Qt ni réseau), testable isolément. Objectifs :

  - normaliser une URL saisie (espaces, slash final superflu) ;
  - n'accepter qu'un schéma http/https avec un hôte présent ;
  - INTERDIRE http:// (non chiffré) vers un serveur DISTANT, sauf mode
    développement explicite — un poste d'officine qui parle à un serveur public
    en clair exposerait le secret applicatif et le trafic patient.

Un serveur « local » (boucle locale, réseau privé, nom d'hôte sans point ou en
.local/.lan) reste autorisé en http : c'est le cas légitime d'un serveur installé
sur le réseau de l'officine.
"""

import ipaddress
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")


def normalize_url(raw):
    """Nettoie une URL saisie : retire espaces de début/fin et slash(s) final(aux).
    Retourne une chaîne vide si l'entrée est vide/blanche."""
    if not raw:
        return ""
    url = str(raw).strip()
    if not url:
        return ""
    # On retire les slashs de fin (l'app concatène ses propres chemins « /api/... »).
    return url.rstrip("/")


def is_local_host(host):
    """True si ``host`` désigne un serveur du réseau local (boucle locale, plage
    privée, lien-local, nom .local/.lan, ou nom d'hôte à label unique sans point).
    Pour ces hôtes, http:// en clair reste acceptable."""
    if not host:
        return False
    h = str(host).strip().lower()
    if not h:
        return False
    if h == "localhost" or h.endswith(".local") or h.endswith(".lan"):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        # Pas une IP : nom d'hôte. Sans point (« serveur-pharma »), c'est un nom
        # de réseau local ; avec un point (« exemple.com »), c'est distant.
        return "." not in h


def validate_server_url(raw, allow_insecure_remote=False):
    """Valide et normalise l'URL du serveur.

    Retourne ``(ok, normalized, error)`` :
      - ``ok`` : bool ;
      - ``normalized`` : URL nettoyée (à enregistrer si ok) ;
      - ``error`` : message utilisateur si ``ok`` est False, sinon None.

    ``allow_insecure_remote`` (mode développement explicite) lève l'interdiction
    de http:// vers un hôte distant."""
    normalized = normalize_url(raw)
    if not normalized:
        return False, "", "L'URL ne peut pas être vide."
    parsed = urlparse(normalized)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return False, normalized, "L'URL doit commencer par http:// ou https://."
    # hostname est None si aucune autorité valide n'est présente.
    if not parsed.hostname:
        return False, normalized, "L'URL ne contient pas de nom de serveur valide."
    if (parsed.scheme == "http"
            and not is_local_host(parsed.hostname)
            and not allow_insecure_remote):
        return False, normalized, (
            "HTTP non chiffré est interdit pour un serveur distant. "
            "Utilisez https:// (ou activez le mode développement).")
    return True, normalized, None
