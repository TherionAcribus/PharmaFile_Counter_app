"""Logique d'état des boutons d'icône (papier / appel automatique).

Isolée de ``buttons.py`` (qui dépend de PySide6) pour être testable seule et pour
garantir un invariant clé : après une réponse serveur, le bouton ne doit JAMAIS
rester bloqué dans l'état transitoire "waiting".
"""


def resolve_button_state(status_code, data, previous_state):
    """Détermine le nouvel état d'un IconeButton d'après la réponse serveur.

    ``data`` est le JSON déjà décodé par le gestionnaire réseau (ou None si la
    réponse n'était pas du JSON exploitable).

    - 200 avec ``data == {"status": bool, ...}`` -> "active" / "inactive" ;
    - 200 mais ``data`` absent/inattendu, OU toute autre réponse (401 après échec
      de renouvellement, 5xx, erreur réseau ``status=0``...) -> on restaure
      ``previous_state`` afin que le bouton quitte "waiting" et redevienne
      utilisable, même en cas d'erreur.
    """
    if status_code == 200 and isinstance(data, dict) and "status" in data:
        return "active" if data["status"] else "inactive"
    return previous_state
