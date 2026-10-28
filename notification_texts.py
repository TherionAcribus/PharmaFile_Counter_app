"""Textes des notifications fabriquées par l'App — logique pure, sans Qt.

Les textes sont réglables dans l'administration du serveur (Application ›
Notifications du comptoir) et arrivent dans l'état du comptoir
(``notification_texts`` : {id: modèle}). Les textes d'origine ci-dessous
servent de repli : serveur ancien qui ne les envoie pas, champ vidé, valeur
illisible. Ils doivent rester identiques à ceux du catalogue serveur
(``Serveur/app_notification_texts.py``, un test le vérifie).

Balises : {N} numéro du patient, {A} activité, {M} membre de l'équipe,
{S} secondes restantes.
"""

import re

DEFAULTS = {
    "new_patient": "Nouveau patient : {N} pour '{A}'",
    "autocalling": "Appel automatique du patient {N} pour '{A}'",
    "patient_taken_queue": "Patient déjà pris en charge par un autre comptoir.",
    "please_validate": "Pensez à valider votre patient afin de vider l'écran d'affichage.",
    "auto_release_warning": (
        "Le patient sera retiré automatiquement de l'écran d'appel dans {S} "
        "secondes. Validez-le ou prolongez l'appel."),
    "server_unreachable": "Le serveur est inaccessible.",
    "realtime_lost": (
        "La connexion temps réel a été perdue. Tentative de reconnexion... La "
        "liste des patients ne s'affichera plus en temps réel, mais les boutons "
        "fonctionnent toujours."),
    "realtime_restored": "La connexion temps réel est (r)établie !",
    "disconnected_by_user": "Vous avez été déconnecté par {M}.",
    "low_paper": "On est quasiment au bout du rouleau",
    "paper_ok": "Une gentille personne a remis du papier",
    "not_identified": "Identifiez-vous sur un comptoir pour agir sur la file.",
    "action_busy": "Une action est déjà en cours…",
}

_MARKER = re.compile(r"\{([A-Za-z])\}")


def template_for(texts, message_id):
    """Modèle reçu du serveur s'il est exploitable, sinon le texte d'origine."""
    value = texts.get(message_id) if isinstance(texts, dict) else None
    if isinstance(value, str) and value.strip():
        return value
    return DEFAULTS[message_id]


def render(texts, message_id, **values):
    """Texte final : balises fournies remplacées, les autres laissées telles
    quelles (le serveur refuse déjà les balises non prévues)."""
    def _sub(match):
        letter = match.group(1).upper()
        if letter in values:
            value = values[letter]
            return "" if value is None else str(value)
        return match.group(0)
    return _MARKER.sub(_sub, template_for(texts, message_id))
