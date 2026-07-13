"""Tests de la logique pure des notifications (point 26) : notification_layout.py.

Signature (déduplication), positions empilées par coin, décision de mise en file.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import notification_layout as nl  # noqa: E402

SCREEN = (0, 0, 1920, 1080)
# Écran secondaire décalé (multi-écrans) pour vérifier la prise en compte du offset.
SCREEN_RIGHT = (1920, 0, 1920, 1080)


def test_signature_same_for_identical():
    a = nl.notification_signature("connection", "Le serveur est inaccessible.")
    b = nl.notification_signature("connection", "Le serveur est inaccessible.")
    assert a == b


def test_signature_differs_on_origin_or_message():
    assert nl.notification_signature("connection", "x") != nl.notification_signature("new_patient", "x")
    assert nl.notification_signature("connection", "x") != nl.notification_signature("connection", "y")


def test_normalize_corner():
    assert nl.normalize_corner("top-right") == "top-right"
    assert nl.normalize_corner("nimporte") == nl.DEFAULT_CORNER
    assert nl.normalize_corner(None) == nl.DEFAULT_CORNER


def test_positions_bottom_left_stack_upward():
    sizes = [(300, 100), (300, 120)]
    pos = nl.compute_stack_positions(SCREEN, sizes, "bottom-left", spacing=10, margin=20)
    # Indice 0 le plus proche du coin (en bas), à gauche.
    assert pos[0][0] == 20  # x = marge gauche
    assert pos[0][1] == 1080 - 20 - 100  # y : au-dessus de la marge basse
    # La suivante est empilée au-dessus (y plus petit).
    assert pos[1][1] < pos[0][1]
    assert pos[1][1] == pos[0][1] - 10 - 120


def test_positions_bottom_right_aligned_right():
    sizes = [(300, 100)]
    pos = nl.compute_stack_positions(SCREEN, sizes, "bottom-right", margin=20)
    assert pos[0][0] == 1920 - 20 - 300


def test_positions_top_left_stack_downward():
    sizes = [(300, 100), (300, 100)]
    pos = nl.compute_stack_positions(SCREEN, sizes, "top-left", spacing=10, margin=20)
    assert pos[0][1] == 20  # première en haut
    assert pos[1][1] == 20 + 100 + 10  # la suivante en dessous


def test_positions_respect_screen_offset():
    sizes = [(300, 100)]
    pos = nl.compute_stack_positions(SCREEN_RIGHT, sizes, "bottom-left", margin=20)
    assert pos[0][0] == 1920 + 20  # sur l'écran de droite


def test_should_queue():
    assert nl.should_queue(0, max_visible=3) is False
    assert nl.should_queue(2, max_visible=3) is False
    assert nl.should_queue(3, max_visible=3) is True
    assert nl.should_queue(5, max_visible=3) is True
