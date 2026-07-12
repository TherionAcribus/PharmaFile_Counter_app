"""Normalisation de l'identifiant de comptoir (counter_id).

counter_id doit être un entier strictement positif et du MÊME type partout
(préférences, comparaisons WebSocket et HTTP). Or QSettings peut renvoyer une
chaîne ("1") au premier démarrage tandis que le serveur utilise des entiers : sans
normalisation, ``"1" == 1`` est faux et des comparaisons échouent silencieusement
(le patient courant ne s'affiche pas, les évènements ciblés sont ignorés…).
"""


def coerce_counter_id(value):
    """Retourne un counter_id entier strictement positif, ou None si la valeur est
    invalide (None, non numérique, <= 0, ou booléen).

    Le booléen est explicitement rejeté (``isinstance(True, int)`` est vrai en
    Python) pour ne pas transformer True en comptoir 1.
    """
    if isinstance(value, bool):
        return None
    try:
        cid = int(value)
    except (TypeError, ValueError):
        return None
    return cid if cid > 0 else None
