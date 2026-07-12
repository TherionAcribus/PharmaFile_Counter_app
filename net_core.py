"""Logique réseau pure (sans PySide/requests) du gestionnaire centralisé.

Isolée pour être testable seule. Ne fait pas d'I/O : on lui injecte un ``send``
(qui exécute la requête et renvoie un objet avec ``.status_code`` et ``.text``) et
un ``reauth`` (qui renouvelle le jeton et renvoie un booléen de succès).
"""


def perform_with_reauth(send, reauth, max_retries_on_401=1):
    """Exécute une requête avec renouvellement du jeton sur 401 et UN SEUL rejeu.

    - ``send()`` -> réponse (``.status_code``, ``.text``, ``.headers``) ; appelé
      une fois, puis au plus ``max_retries_on_401`` fois de plus si le serveur
      répond 401 et que ``reauth()`` réussit.
    - Retourne l'objet réponse final (le caller en extrait statut/corps/en-têtes).

    Garantit qu'une requête n'est répétée qu'une fois (avec la valeur par défaut) :
    le renouvellement du jeton n'est tenté que sur un 401 et le rejeu est unique.
    """
    response = send()
    retries = 0
    while response.status_code == 401 and retries < max_retries_on_401:
        if not reauth():
            break
        response = send()
        retries += 1
    return response
