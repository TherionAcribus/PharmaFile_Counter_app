"""Logique pure de placement de la fenêtre principale (point 24).

L'application doit restaurer sa taille/position ET rester visible même si la
configuration d'écrans change (moniteur débranché, résolution modifiée). Les
calculs géométriques (une fenêtre est-elle suffisamment visible sur les écrans
disponibles ? où la recentrer ?) sont isolés ici, sans dépendance à Qt, pour être
testables. Les rectangles sont des tuples ``(x, y, largeur, hauteur)``.

L'intégration Qt (saveGeometry/restoreGeometry, énumération des QScreen, nom du
moniteur) vit dans main.py et appelle ces fonctions.
"""

from __future__ import annotations

# Hauteur approximative d'une barre de titre : zone par laquelle l'utilisateur
# saisit la fenêtre à la souris. On exige qu'une portion en soit visible.
DEFAULT_TITLEBAR_HEIGHT = 32
# Largeur minimale de barre de titre visible pour pouvoir attraper la fenêtre.
DEFAULT_MIN_GRAB_WIDTH = 120
# Fraction minimale de la surface de la fenêtre devant se trouver sur un écran.
DEFAULT_MIN_VISIBLE_FRACTION = 0.25


def _intersection_area(a, b) -> int:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(ax, bx)
    iy = max(ay, by)
    ix2 = min(ax + aw, bx + bw)
    iy2 = min(ay + ah, by + bh)
    if ix2 <= ix or iy2 <= iy:
        return 0
    return (ix2 - ix) * (iy2 - iy)


def visible_area(window, screens) -> int:
    """Surface de ``window`` couverte par l'union des ``screens``.

    On somme les intersections écran par écran puis on plafonne à la surface de
    la fenêtre (les écrans réels ne se chevauchent pas : la somme n'est pas
    surestimée, mais le plafond protège des configurations exotiques)."""
    _, _, ww, wh = window
    area = ww * wh
    if area <= 0:
        return 0
    total = sum(_intersection_area(window, s) for s in screens)
    return min(total, area)


def visible_fraction(window, screens) -> float:
    _, _, ww, wh = window
    area = ww * wh
    if area <= 0:
        return 0.0
    return visible_area(window, screens) / area


def visible_grab_width(window, screens, titlebar_height=DEFAULT_TITLEBAR_HEIGHT) -> float:
    """Largeur (px) de barre de titre visible : partie haute de la fenêtre
    recouverte par un écran. Sert à vérifier qu'on peut attraper la fenêtre."""
    wx, wy, ww, wh = window
    strip_h = max(1, min(titlebar_height, wh))
    strip = (wx, wy, ww, strip_h)
    return visible_area(strip, screens) / strip_h


def is_window_visible(window, screens,
                      min_fraction=DEFAULT_MIN_VISIBLE_FRACTION,
                      titlebar_height=DEFAULT_TITLEBAR_HEIGHT,
                      min_grab_width=DEFAULT_MIN_GRAB_WIDTH) -> bool:
    """Vrai si la fenêtre est « suffisamment » visible : assez de surface ET une
    portion de barre de titre saisissable sur au moins un écran."""
    wx, wy, ww, wh = window
    if ww <= 0 or wh <= 0 or not screens:
        return False
    if visible_fraction(window, screens) < min_fraction:
        return False
    grab = visible_grab_width(window, screens, titlebar_height)
    return grab >= min(min_grab_width, ww)


def centered_geometry(window_size, screen_avail):
    """Rectangle de fenêtre centré dans la zone d'écran ``screen_avail``, la
    taille étant bornée à celle de l'écran."""
    ww, wh = window_size
    sx, sy, sw, sh = screen_avail
    ww = max(1, min(ww, sw))
    wh = max(1, min(wh, sh))
    x = sx + (sw - ww) // 2
    y = sy + (sh - wh) // 2
    return (x, y, ww, wh)


def resolve_target_geometry(window, screens, primary_avail,
                            stored_screen_name=None, available_screen_names=None,
                            **visible_kwargs):
    """Décide où placer la fenêtre au démarrage / après un changement d'écran.

    Retourne ``None`` si la position actuelle convient (fenêtre visible et son
    moniteur d'origine toujours présent), sinon un rectangle recentré sur l'écran
    principal.
    """
    monitor_gone = (
        stored_screen_name is not None
        and available_screen_names is not None
        and stored_screen_name not in available_screen_names
    )
    if not monitor_gone and is_window_visible(window, screens, **visible_kwargs):
        return None
    return centered_geometry((window[2], window[3]), primary_avail)
