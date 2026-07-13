"""Câblage réel du mode panneau compact (point 25) dans MainWindow.

On appelle les vraies méthodes apply_panel_mode / _apply_edge_snap avec un faux
``self`` minimal (aucun widget instancié) : on vérifie que l'orientation choisit
la bonne géométrie de panneau (colonne verticale dockée vs barre horizontale en
haut), que le magnétisme n'agit qu'en cas de déplacement réel, et que le drapeau
``_applying_panel`` est bien posé pendant nos repositionnements pour ne pas
reboucler via moveEvent.
"""

import logging
import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402


class FakeRect:
    def __init__(self, x, y, w, h):
        self._x, self._y, self._w, self._h = x, y, w, h

    def x(self):
        return self._x

    def y(self):
        return self._y

    def width(self):
        return self._w

    def height(self):
        return self._h


class FakePanelWindow:
    def __init__(self, horizontal_mode=False, frame=(600, 300, 400, 500),
                 avail=(0, 0, 1920, 1040)):
        self.horizontal_mode = horizontal_mode
        self.compact_mode = True
        self.panel_snap = True
        self.panel_thickness = 300
        self.shutting_down = False
        self._applying_panel = False
        self._avail = avail
        self._frame = FakeRect(*frame)
        self.logger = logging.getLogger("test.panel_mode")
        self.resizes = []
        self.moves = []
        self.applying_during_move = []
        # Vraies méthodes liées à ce faux self.
        self.apply_panel_mode = types.MethodType(main.MainWindow.apply_panel_mode, self)
        self._apply_edge_snap = types.MethodType(main.MainWindow._apply_edge_snap, self)

    # --- API Qt minimale simulée ---
    def _current_screen_avail(self):
        return self._avail

    def frameGeometry(self):
        return self._frame

    def isMaximized(self):
        return False

    def isFullScreen(self):
        return False

    def isVisible(self):
        return True

    def showNormal(self):
        pass

    def resize(self, w, h):
        self.resizes.append((w, h))

    def move(self, x, y):
        # Enregistre l'état du drapeau au moment du move pour vérifier la garde.
        self.applying_during_move.append(self._applying_panel)
        self.moves.append((x, y))


# --- apply_panel_mode -------------------------------------------------------

def test_vertical_panel_docks_to_nearest_side_right():
    # Fenêtre côté droit de l'écran -> colonne dockée à droite.
    w = FakePanelWindow(horizontal_mode=False, frame=(1500, 300, 400, 500))
    w.apply_panel_mode()
    assert w.resizes == [(300, 1040)]      # largeur = épaisseur, hauteur pleine
    assert w.moves == [(1920 - 300, 0)]    # dockée au bord droit


def test_vertical_panel_docks_to_nearest_side_left():
    w = FakePanelWindow(horizontal_mode=False, frame=(100, 300, 400, 500))
    w.apply_panel_mode()
    assert w.resizes == [(300, 1040)]
    assert w.moves == [(0, 0)]


def test_horizontal_panel_docks_to_top():
    w = FakePanelWindow(horizontal_mode=True, frame=(600, 400, 400, 500))
    w.apply_panel_mode()
    assert w.resizes == [(1920, 300)]      # largeur pleine, hauteur = épaisseur
    assert w.moves == [(0, 0)]             # barre en haut


def test_apply_panel_sets_guard_during_move():
    w = FakePanelWindow()
    w.apply_panel_mode()
    # Pendant le repositionnement, _applying_panel doit être True (évite la boucle
    # de magnétisme via moveEvent) puis rétabli à False après.
    assert w.applying_during_move == [True]
    assert w._applying_panel is False


def test_apply_panel_noop_when_compact_disabled():
    w = FakePanelWindow()
    w.compact_mode = False
    w.apply_panel_mode()
    assert w.resizes == []
    assert w.moves == []


def test_apply_panel_noop_without_screen():
    w = FakePanelWindow()
    w._current_screen_avail = lambda: None
    w.apply_panel_mode()
    assert w.moves == []


# --- _apply_edge_snap -------------------------------------------------------

def test_snap_moves_window_near_edge():
    # Fenêtre proche du bord gauche (x=10) -> aimantée à x=0.
    w = FakePanelWindow(frame=(10, 300, 300, 500))
    w._apply_edge_snap()
    assert w.moves == [(0, 300)]
    assert w._applying_panel is False


def test_snap_does_nothing_when_far_from_edges():
    w = FakePanelWindow(frame=(600, 400, 300, 300))
    w._apply_edge_snap()
    assert w.moves == []


def test_snap_skipped_when_disabled():
    w = FakePanelWindow(frame=(10, 300, 300, 500))
    w.panel_snap = False
    w._apply_edge_snap()
    assert w.moves == []


def test_snap_skipped_when_shutting_down():
    w = FakePanelWindow(frame=(10, 300, 300, 500))
    w.shutting_down = True
    w._apply_edge_snap()
    assert w.moves == []
