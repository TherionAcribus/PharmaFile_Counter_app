"""Décision de reconnexion après modification des préférences (module pur).

Distingue les changements « services » (serveur, secret, comptoir) — qui exigent
d'arrêter/reconnecter le WebSocket, renouveler le jeton, recharger le snapshot et
reconstruire l'interface — des changements purement cosmétiques (thème, volume,
notifications…) qui ne doivent PAS déclencher de reconnexion.
"""

# Clés dont un changement impose une reconnexion complète des services.
SERVICE_KEYS = ("web_url", "app_secret", "counter_id")


def needs_service_reconnect(old, new):
    """True si au moins une valeur « service » (URL, secret, comptoir) a changé
    entre ``old`` et ``new`` (deux mappings)."""
    return any(old.get(k) != new.get(k) for k in SERVICE_KEYS)
