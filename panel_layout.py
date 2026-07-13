"""Logique pure du mode panneau compact (point 25), testable sans Qt.

L'App_Comptoir est utilisée principalement comme un panneau latéral occupant une
petite zone à côté du progiciel, pas comme une fenêtre générique. Ce module
regroupe les calculs géométriques du mode compact, isolés de Qt pour être
testables :

  - BORNAGE de l'épaisseur du panneau (largeur en mode vertical, hauteur en mode
    horizontal) dans une plage raisonnable ;
  - GÉOMÉTRIE d'un panneau docké : colonne verticale étroite sur un bord gauche/
    droit, ou barre horizontale fine en haut/bas, occupant toute la dimension
    perpendiculaire de l'écran ;
  - MAGNÉTISME aux bords : aligne une fenêtre sur un bord d'écran proche.
  - choix du BORD le plus proche pour docker le panneau vertical.

L'intégration Qt (résolution de l'écran courant, resize/move, moveEvent) vit dans
main.py et appelle ces fonctions. Les rectangles sont des tuples
``(x, y, largeur, hauteur)`` ; les zones d'écran passées sont les zones utiles
(``availableGeometry``, hors barre des tâches).
"""

from __future__ import annotations

# Orientations du panneau. "vertical" = colonne étroite (largeur = épaisseur) ;
# "horizontal" = barre fine (hauteur = épaisseur).
VERTICAL = "vertical"
HORIZONTAL = "horizontal"

# Épaisseur du panneau (px). Le point 25 suggère 280–340 px pour le panneau
# vertical ; on autorise une plage un peu plus large pour laisser le choix tout
# en gardant un vrai « panneau » (jamais une fenêtre pleine).
MIN_PANEL_THICKNESS = 240
MAX_PANEL_THICKNESS = 480
DEFAULT_PANEL_THICKNESS = 300

# Distance (px) sous laquelle un bord de fenêtre est aimanté au bord d'écran.
DEFAULT_SNAP_THRESHOLD = 24


def clamp_thickness(value, default=DEFAULT_PANEL_THICKNESS):
    """Borne l'épaisseur du panneau dans [MIN, MAX]. Une valeur illisible
    (None, non convertible) retombe sur ``default``."""
    try:
        t = int(value)
    except (TypeError, ValueError):
        t = default
    return max(MIN_PANEL_THICKNESS, min(MAX_PANEL_THICKNESS, t))


def compact_panel_geometry(orientation, screen_avail, thickness, side):
    """Rectangle d'un panneau compact docké sur un bord de ``screen_avail``.

    - orientation VERTICAL : colonne de largeur ``thickness`` (bornée à l'écran),
      hauteur = toute la zone utile, dockée à gauche (``side='left'``) ou à
      droite (``'right'``) ;
    - orientation HORIZONTAL : barre de hauteur ``thickness``, largeur = toute la
      zone utile, dockée en haut (``side='top'``) ou en bas (``'bottom'``).

    L'épaisseur est bornée à la dimension de l'écran pour ne jamais dépasser.
    """
    sx, sy, sw, sh = screen_avail
    t = clamp_thickness(thickness)
    if orientation == VERTICAL:
        t = max(1, min(t, sw))
        x = sx if side == "left" else sx + sw - t
        return (x, sy, t, sh)
    # horizontal
    t = max(1, min(t, sh))
    y = sy if side == "top" else sy + sh - t
    return (sx, y, sw, t)


def nearest_vertical_side(window, screen_avail):
    """Bord gauche/droit de l'écran le plus proche du centre de la fenêtre.

    Sert à docker le panneau vertical du côté vers lequel l'utilisateur l'a
    amené, sans imposer un côté fixe."""
    wx, _wy, ww, _wh = window
    sx, _sy, sw, _sh = screen_avail
    center_x = wx + ww / 2
    return "left" if center_x < sx + sw / 2 else "right"


def snap_to_edges(window, screen_avail, threshold=DEFAULT_SNAP_THRESHOLD):
    """Aimante la fenêtre aux bords de ``screen_avail`` (magnétisme).

    Si un bord de la fenêtre se trouve à moins de ``threshold`` px du bord
    correspondant de l'écran, la fenêtre est alignée exactement sur ce bord (sans
    changer sa taille). Le bord GAUCHE est prioritaire sur le DROIT et le HAUT sur
    le BAS quand la fenêtre est plus large/haute que l'écran. Retourne le nouveau
    rectangle ``(x, y, w, h)`` (mêmes w/h)."""
    wx, wy, ww, wh = window
    sx, sy, sw, sh = screen_avail

    new_x = wx
    if abs(wx - sx) <= threshold:
        new_x = sx
    elif abs((wx + ww) - (sx + sw)) <= threshold:
        new_x = sx + sw - ww

    new_y = wy
    if abs(wy - sy) <= threshold:
        new_y = sy
    elif abs((wy + wh) - (sy + sh)) <= threshold:
        new_y = sy + sh - wh

    return (new_x, new_y, ww, wh)
