"""Structure de résultat réseau commune + traitement uniforme des réponses.

Sans dépendance PySide (testable seul). Utilisée par le gestionnaire réseau pour
livrer aux appelants un objet homogène plutôt qu'un triplet brut :

- ``success`` / ``status`` : issue HTTP (status 0 = erreur réseau/timeout) ;
- ``data`` : JSON décodé UNIQUEMENT si le corps est du JSON (content-type
  compatible), sinon None -> une réponse HTML/vide/malformée ne fait pas planter ;
- ``message`` : message utilisateur court et distinct selon le statut ;
- ``detail`` : détail technique à journaliser (jamais montré à l'utilisateur).
"""

import json


def user_message_for_status(status):
    """Message utilisateur court, distinct par catégorie de statut."""
    if status == 0:
        return "Serveur injoignable. Vérifiez la connexion."
    if status == 401:
        return "Session expirée. Reconnexion en cours…"
    if status == 403:
        return "Action non autorisée."
    if status in (409, 423):
        return "Patient déjà pris en charge par un autre comptoir."
    if 500 <= status < 600:
        return "Erreur du serveur. Réessayez dans un instant."
    if 400 <= status < 500:
        return "Requête refusée par le serveur."
    return ""


def parse_json_if_possible(text, content_type):
    """Décode le JSON seulement si c'est pertinent : corps non vide ET content-type
    JSON (ou inconnu). Une réponse HTML ou un JSON malformé renvoie None au lieu de
    lever une exception."""
    if not text:
        return None
    if content_type and "json" not in content_type.lower():
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


class NetResult:
    """Résultat homogène d'une requête réseau."""

    __slots__ = ("status", "data", "text", "message", "detail", "content_type")

    def __init__(self, status, data=None, text="", message="", detail="", content_type=None):
        self.status = status
        self.data = data
        self.text = text
        self.message = message
        self.detail = detail
        self.content_type = content_type

    @property
    def success(self):
        return 200 <= self.status < 300

    @property
    def is_timeout(self):
        return self.status == 0

    @classmethod
    def from_response(cls, status, text, content_type=None, detail=""):
        data = parse_json_if_possible(text, content_type)
        message = "" if 200 <= status < 300 else user_message_for_status(status)
        if not detail and not (200 <= status < 300):
            detail = f"HTTP {status}: {(text or '')[:200]}"
        return cls(status=status, data=data, text=text or "", message=message,
                   detail=detail, content_type=content_type)

    @classmethod
    def network_error(cls, detail=""):
        return cls(status=0, data=None, text="", message=user_message_for_status(0),
                   detail=detail or "erreur réseau")

    def __repr__(self):  # pragma: no cover - confort de debug
        return f"NetResult(status={self.status}, success={self.success})"
