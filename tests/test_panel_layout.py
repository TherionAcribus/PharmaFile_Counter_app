"""Tests de la logique du mode panneau compact (point 25) : panel_layout.py.

Fonctions pures (sans Qt) : bornage de l'épaisseur, géométrie d'un panneau docké
(colonne verticale / barre horizontale), choix du bord le plus proche et
magnétisme aux bords de l'écran. Rectangles = (x, y, largeur, hauteur).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import panel_layout as pl  # noqa: E402

# Écran typique (zone utile hors barre des tâches).
SCREEN = (0, 0, 1920, 1040)


# --- clamp_thickness --------------------------------------------------------

def test_clamp_thickness_in_range():
    assert pl.clamp_thickness(300) == 300


def test_clamp_thickness_below_min():
    assert pl.clamp_thickness(50) == pl.MIN_PANEL_THICKNESS


def test_clamp_thickness_above_max():
    assert pl.clamp_thickness(9999) == pl.MAX_PANEL_THICKNESS


def test_clamp_thickness_invalid_falls_back_to_default():
    assert pl.clamp_thickness(None) == pl.DEFAULT_PANEL_THICKNESS
    assert pl.clamp_thickness("abc") == pl.DEFAULT_PANEL_THICKNESS


def test_clamp_thickness_string_number():
    # QSettings peut renvoyer une chaîne.
    assert pl.clamp_thickness("320") == 320


# --- compact_panel_geometry : vertical --------------------------------------

def test_vertical_panel_right_full_height():
    rect = pl.compact_panel_geometry(pl.VERTICAL, SCREEN, 300, "right")
    assert rect == (1920 - 300, 0, 300, 1040)


def test_vertical_panel_left_docks_to_left_edge():
    rect = pl.compact_panel_geometry(pl.VERTICAL, SCREEN, 300, "left")
    assert rect == (0, 0, 300, 1040)


def test_vertical_panel_respects_screen_offset():
    screen = (1920, 0, 1920, 1040)  # écran de droite
    rect = pl.compact_panel_geometry(pl.VERTICAL, screen, 300, "right")
    assert rect == (1920 + 1920 - 300, 0, 300, 1040)


def test_vertical_thickness_clamped_to_screen_width():
    narrow = (0, 0, 200, 1040)
    rect = pl.compact_panel_geometry(pl.VERTICAL, narrow, 300, "left")
    # 300 borné à la largeur d'écran (200).
    assert rect == (0, 0, 200, 1040)


# --- compact_panel_geometry : horizontal ------------------------------------

def test_horizontal_panel_top_full_width():
    rect = pl.compact_panel_geometry(pl.HORIZONTAL, SCREEN, 300, "top")
    assert rect == (0, 0, 1920, 300)


def test_horizontal_panel_bottom_docks_to_bottom_edge():
    rect = pl.compact_panel_geometry(pl.HORIZONTAL, SCREEN, 300, "bottom")
    assert rect == (0, 1040 - 300, 1920, 300)


# --- nearest_vertical_side --------------------------------------------------

def test_nearest_side_left():
    assert pl.nearest_vertical_side((100, 0, 300, 1040), SCREEN) == "left"


def test_nearest_side_right():
    assert pl.nearest_vertical_side((1500, 0, 300, 1040), SCREEN) == "right"


def test_nearest_side_uses_window_center():
    # Fenêtre dont le centre est juste à gauche du milieu (960).
    assert pl.nearest_vertical_side((600, 0, 300, 1040), SCREEN) == "left"
    assert pl.nearest_vertical_side((820, 0, 300, 1040), SCREEN) == "right"


# --- snap_to_edges ----------------------------------------------------------

def test_snap_left_edge():
    rect = pl.snap_to_edges((10, 300, 300, 500), SCREEN, threshold=24)
    assert rect == (0, 300, 300, 500)


def test_snap_right_edge():
    # Bord droit de la fenêtre à 1910 (écran 1920) -> aimanté.
    rect = pl.snap_to_edges((1610, 300, 300, 500), SCREEN, threshold=24)
    assert rect == (1920 - 300, 300, 300, 500)


def test_snap_top_edge():
    rect = pl.snap_to_edges((500, 8, 300, 500), SCREEN, threshold=24)
    assert rect == (500, 0, 300, 500)


def test_snap_bottom_edge():
    rect = pl.snap_to_edges((500, 550, 300, 480), SCREEN, threshold=24)
    assert rect == (500, 1040 - 480, 300, 480)


def test_snap_two_corners_at_once():
    # Proche du coin haut-gauche : les deux axes sont aimantés.
    rect = pl.snap_to_edges((5, 6, 300, 500), SCREEN, threshold=24)
    assert rect == (0, 0, 300, 500)


def test_no_snap_when_far_from_edges():
    window = (500, 400, 300, 300)
    assert pl.snap_to_edges(window, SCREEN, threshold=24) == window


def test_snap_size_is_preserved():
    rect = pl.snap_to_edges((10, 8, 333, 444), SCREEN, threshold=24)
    assert rect[2:] == (333, 444)


def test_left_edge_wins_over_right_when_window_wider_than_screen():
    # Fenêtre plus large que l'écran, collée à gauche : on garde le bord gauche.
    wide = (5, 300, 2000, 500)
    rect = pl.snap_to_edges(wide, SCREEN, threshold=24)
    assert rect[0] == 0
