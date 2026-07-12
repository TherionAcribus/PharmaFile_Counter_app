"""Logique d'état des boutons d'icône (papier / appel automatique).

Isolée de ``buttons.py`` (qui dépend de PySide6) pour être testable seule et pour
garantir un invariant clé : après une réponse serveur, le bouton ne doit JAMAIS
rester bloqué dans l'état transitoire "waiting".
"""

import json


def resolve_button_state(status_code, response_text, previous_state):
    """Détermine le nouvel état d'un IconeButton d'après la réponse serveur.

    - 200 avec un corps JSON ``{"status": bool}`` -> "active" / "inactive" ;
    - 200 mais corps inattendu, OU toute autre réponse (401 après échec de
      renouvellement du jeton, 5xx, erreur réseau ``status=0``...) -> on restaure
      ``previous_state`` afin que le bouton quitte "waiting" et redevienne
      utilisable, même en cas d'erreur.
    """
    if status_code == 200:
        try:
            data = json.loads(response_text)
            return "active" if data["status"] else "inactive"
        except (ValueError, KeyError, TypeError):
            return previous_state
    return previous_state
