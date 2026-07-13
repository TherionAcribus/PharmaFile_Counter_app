"""Logique pure des notifications (point 26), testable sans Qt.

Regroupe :
  - la SIGNATURE d'une notification (origine + message) pour dédupliquer les
    messages identiques (ex. une coupure réseau ne doit pas empiler 20 fois le
    même « Problème de connexion ») ;
  - le calcul des POSITIONS empilées depuis un coin configurable de l'écran ;
  - la décision ADMETTRE / METTRE EN FILE selon le nombre de notifications déjà
    visibles.

L'intégration Qt (choix de l'écran de la fenêtre principale, création des
widgets, focus) vit dans notification.py. Rectangles = (x, y, largeur, hauteur).
"""

from __future__ import annotations

CORNERS = ("bottom-left", "bottom-right", "top-left", "top-right")
DEFAULT_CORNER = "bottom-left"

DEFAULT_SPACING = 10
DEFAULT_MARGIN = 20
NOTIFICATION_WIDTH = 300
# Nombre maximum de notifications visibles simultanément ; les suivantes sont
# mises en file d'attente.
DEFAULT_MAX_VISIBLE = 3

# Séparateur improbable dans un message, pour composer une clé sans collision.
_SEP = "\x1f"


def notification_signature(origin, message) -> str:
    """Clé d'identité d'une notification : deux notifications de même origine et
    même message sont considérées identiques (candidates à la déduplication)."""
    return f"{origin}{_SEP}{message}"


def normalize_corner(corner) -> str:
    return corner if corner in CORNERS else DEFAULT_CORNER


def compute_stack_positions(screen_rect, sizes, corner=DEFAULT_CORNER,
                            spacing=DEFAULT_SPACING, margin=DEFAULT_MARGIN):
    """Empile les notifications depuis un coin de ``screen_rect``.

    ``sizes`` : liste de (largeur, hauteur), dans l'ordre d'affichage (l'indice 0
    est le plus proche du coin). Retourne une liste de (x, y, w, h).
    """
    sx, sy, sw, sh = screen_rect
    corner = normalize_corner(corner)
    to_right = "right" in corner
    to_top = "top" in corner

    positions = []
    if to_top:
        cursor = sy + margin
        for (w, h) in sizes:
            x = (sx + sw - margin - w) if to_right else (sx + margin)
            positions.append((x, cursor, w, h))
            cursor += h + spacing
    else:
        cursor = sy + sh - margin
        for (w, h) in sizes:
            y = cursor - h
            x = (sx + sw - margin - w) if to_right else (sx + margin)
            positions.append((x, y, w, h))
            cursor -= h + spacing
    return positions


def should_queue(active_count, max_visible=DEFAULT_MAX_VISIBLE) -> bool:
    """Vrai si une nouvelle notification doit être mise en file plutôt qu'affichée
    (le nombre maximum de notifications visibles est atteint)."""
    return active_count >= max_visible
