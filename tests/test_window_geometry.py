"""Tests de la logique de placement de fenêtre (point 24) : window_geometry.py.

Fonctions pures (sans Qt) : visibilité d'une fenêtre sur un ensemble d'écrans,
recentrage, et décision de repositionnement (moniteur disparu / hors zone
visible). Rectangles = (x, y, largeur, hauteur).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import window_geometry as wg  # noqa: E402

# Deux écrans côte à côte (config multi-écrans typique).
SCREEN_LEFT = (0, 0, 1920, 1080)
SCREEN_RIGHT = (1920, 0, 1920, 1080)
SCREENS = [SCREEN_LEFT, SCREEN_RIGHT]


def test_fully_on_screen_is_visible():
    assert wg.is_window_visible((100, 100, 800, 600), SCREENS) is True


def test_on_second_screen_is_visible():
    assert wg.is_window_visible((2000, 200, 800, 600), SCREENS) is True


def test_completely_off_screen_is_not_visible():
    # Bien au-delà des deux écrans.
    assert wg.is_window_visible((5000, 5000, 800, 600), SCREENS) is False


def test_negative_offscreen_is_not_visible():
    assert wg.is_window_visible((-2000, -2000, 800, 600), SCREENS) is False


def test_tiny_sliver_visible_is_rejected():
    # Seulement 10 px de la fenêtre dépassent sur l'écran gauche : insuffisant.
    assert wg.is_window_visible((-790, 100, 800, 600), SCREENS) is False


def test_straddling_two_screens_is_visible():
    # À cheval sur la frontière des deux écrans : visible (pas de trou entre eux).
    assert wg.is_window_visible((1620, 100, 600, 400), SCREENS) is True


def test_no_screens_is_not_visible():
    assert wg.is_window_visible((0, 0, 800, 600), []) is False


def test_zero_size_is_not_visible():
    assert wg.is_window_visible((0, 0, 0, 0), SCREENS) is False


def test_visible_fraction():
    # Moitié gauche hors écran (x=-400 sur une fenêtre de 800 -> 400 visibles).
    assert wg.visible_fraction((-400, 100, 800, 600), SCREENS) == pytest.approx(0.5)


def test_title_bar_must_be_grabbable():
    # Fenêtre dont le haut est au-dessus de l'écran (barre de titre invisible)
    # mais dont une grande surface reste visible : rejetée car non saisissable.
    window = (100, -20, 800, 600)  # 20 px du haut coupés -> titre partiellement
    # Ici le titre reste visible (coupé de 20px sur 32) -> encore saisissable.
    assert wg.is_window_visible(window, SCREENS) is True
    # Titre complètement au-dessus de l'écran :
    window2 = (100, -200, 800, 600)
    assert wg.is_window_visible(window2, SCREENS) is False


def test_centered_geometry():
    geo = wg.centered_geometry((800, 600), (0, 0, 1920, 1080))
    assert geo == ((1920 - 800) // 2, (1080 - 600) // 2, 800, 600)


def test_centered_geometry_clamps_to_screen():
    geo = wg.centered_geometry((3000, 2000), (0, 0, 1920, 1080))
    assert geo == (0, 0, 1920, 1080)


def test_centered_geometry_respects_screen_offset():
    geo = wg.centered_geometry((800, 600), (1920, 0, 1920, 1080))
    assert geo[0] >= 1920  # placé sur l'écran de droite


# --- resolve_target_geometry ------------------------------------------------

PRIMARY = (0, 0, 1920, 1080)


def test_resolve_returns_none_when_visible_and_monitor_present():
    target = wg.resolve_target_geometry(
        (100, 100, 800, 600), SCREENS, PRIMARY,
        stored_screen_name="DISPLAY1",
        available_screen_names=["DISPLAY1", "DISPLAY2"])
    assert target is None


def test_resolve_recenters_when_monitor_gone():
    # La fenêtre est physiquement visible, mais son moniteur d'origine a disparu.
    target = wg.resolve_target_geometry(
        (100, 100, 800, 600), SCREENS, PRIMARY,
        stored_screen_name="DISPLAY2",
        available_screen_names=["DISPLAY1"])
    assert target == wg.centered_geometry((800, 600), PRIMARY)


def test_resolve_recenters_when_offscreen():
    target = wg.resolve_target_geometry(
        (9000, 9000, 800, 600), SCREENS, PRIMARY,
        stored_screen_name="DISPLAY1",
        available_screen_names=["DISPLAY1"])
    assert target == wg.centered_geometry((800, 600), PRIMARY)


def test_resolve_ignores_monitor_name_when_not_provided():
    # Sans info de moniteur, seule la visibilité compte.
    assert wg.resolve_target_geometry(
        (100, 100, 800, 600), SCREENS, PRIMARY) is None
    assert wg.resolve_target_geometry(
        (9000, 9000, 800, 600), SCREENS, PRIMARY) == wg.centered_geometry(
            (800, 600), PRIMARY)


# --- Fenêtre plus grande que l'écran (petites résolutions) --------------------

def test_fit_inside_reduit_et_decale():
    assert wg.fit_inside((100, 50, 2000, 1200), (0, 0, 1366, 728)) == (0, 0, 1366, 728)
    # Taille correcte mais déborde à droite / en bas : simplement décalée.
    assert wg.fit_inside((1200, 600, 400, 300), (0, 0, 1366, 728)) == (966, 428, 400, 300)
    # Déjà dedans : inchangée.
    assert wg.fit_inside((10, 10, 400, 300), (0, 0, 1366, 728)) == (10, 10, 400, 300)
    # Écran décalé (second moniteur).
    assert wg.fit_inside((1300, -20, 400, 900), (1366, 0, 1920, 800)) == (1366, 0, 400, 800)


def test_host_screen_retient_l_ecran_qui_porte_le_plus_la_fenetre():
    left, right = (0, 0, 1366, 728), (1366, 0, 1920, 1040)
    assert wg.host_screen((1000, 0, 400, 300), [left, right]) == left
    assert wg.host_screen((1300, 0, 400, 300), [left, right]) == right
    assert wg.host_screen((9000, 9000, 10, 10), [left, right]) is None


def test_resolve_reduit_une_fenetre_plus_grande_que_son_ecran():
    """Taille mémorisée sur un grand écran, poste passé en 1366×768 : la
    fenêtre est visible mais ses bords (et ses boutons) sortaient de l'écran."""
    screen = (0, 0, 1366, 728)
    target = wg.resolve_target_geometry(
        (50, 20, 1600, 1000), [screen], screen,
        stored_screen_name="A", available_screen_names=["A"])
    assert target == (0, 0, 1366, 728)


def test_resolve_laisse_une_fenetre_qui_tient_dans_son_ecran():
    screen = (0, 0, 1366, 728)
    assert wg.resolve_target_geometry(
        (50, 20, 400, 700), [screen], screen,
        stored_screen_name="A", available_screen_names=["A"]) is None
