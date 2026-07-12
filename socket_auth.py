"""En-têtes d'authentification de la connexion Socket.IO.

Isolé de ``websocket_client`` (qui dépend de PySide6/socketio) pour être testable
seul et pour exprimer clairement le contrat d'authentification :

- la connexion Socket.IO présente le MÊME jeton applicatif (``X-App-Token``) que
  les requêtes REST : c'est la seule véritable preuve d'identité ;
- le ``username`` historique est conservé mais n'est qu'un libellé d'affichage
  (il ne prouve rien), d'où l'ajout du jeton.

Le serveur (namespace ``/socket_app_counter``) lit ``X-App-Token`` à la poignée
de main et refuse la connexion sans jeton valide lorsque ``SECURITY_LOGIN_COUNTER``
est actif.
"""


def build_socket_auth_headers(username, token):
    """Construit les en-têtes de connexion Socket.IO.

    Inclut ``X-App-Token`` uniquement si un jeton est disponible : la connexion
    est ainsi authentifiée dès qu'un jeton existe, et le renouvellement du jeton
    (relu à chaque reconnexion) est pris en compte automatiquement.
    """
    headers = {}
    if username:
        headers["username"] = username
    if token:
        headers["X-App-Token"] = token
    return headers
