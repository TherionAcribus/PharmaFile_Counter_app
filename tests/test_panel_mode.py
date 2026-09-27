"""Câblage réel du mode panneau compact (point 25), porté par ``WindowPlacement``
depuis le point 10.12.

On appelle les vraies méthodes apply_panel_mode / apply_edge_snap avec un faux
gestionnaire et une fausse fenêtre (aucun widget instancié) : on vérifie que
l'orientation choisit la bonne géométrie de panneau (colonne verticale dockée vs
barre horizontale en haut), que le magnétisme n'agit qu'en cas de déplacement
réel, et que le drapeau ``applying`` est bien posé pendant nos repositionnements
pour ne pas reboucler via moveEvent.
"""

import logging
import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402
from window_placement import WindowPlacement  # noqa: E402


class FakeTimer:
    """QTimer factice : mémorise les start() sans boucle d'événements."""

    def __init__(self):
        self.started = []

    def start(self, ms=None):
        self.started.append(ms)


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


class FakeSize:
    def __init__(self, height):
        self._height = height

    def height(self):
        return self._height


class FakePanelWindow:
    def __init__(self, horizontal_mode=False, frame=(600, 300, 400, 500),
                 avail=(0, 0, 1920, 1040), content_height=420):
        self.horizontal_mode = horizontal_mode
        self.compact_mode = True
        self.panel_snap = True
        self.panel_thickness = 300
        self.shutting_down = False
        self.applying = False
        self._avail = avail
        self._frame = FakeRect(*frame)
        self._content_height = content_height
        self.logger = logging.getLogger("test.panel_mode")
        self.resizes = []
        self.moves = []
        self.applying_during_move = []
        # Vraies méthodes liées à ce faux self.
        # Le gestionnaire de placement voit CETTE instance comme sa fenêtre.
        self.window = self
        self.applying = False
        self.apply_panel_mode = types.MethodType(WindowPlacement.apply_panel_mode, self)
        self.fit_to_content_height = types.MethodType(
            WindowPlacement.fit_to_content_height, self,
        )
        self.preferred_content_height = types.MethodType(
            WindowPlacement.preferred_content_height, self,
        )
        self._apply_edge_snap = types.MethodType(WindowPlacement.apply_edge_snap, self)
        self._window_frame = types.MethodType(WindowPlacement._window_frame, self)
        # self.placement = self : MainWindow.fit_window_to_content délègue à
        # l'objet placement, qui est simulé par cette même instance.
        self.placement = self
        self.fit_window_to_content = types.MethodType(
            main.MainWindow.fit_window_to_content, self,
        )
        self.on_screen_geometry_changed = types.MethodType(
            WindowPlacement.on_screen_geometry_changed, self,
        )
        self._handle_screen_change = types.MethodType(
            WindowPlacement._handle_screen_change, self,
        )
        self._screen_timer = FakeTimer()

    # --- API Qt minimale simulée ---
    def current_screen_avail(self):
        return self._avail

    def frameGeometry(self):
        return self._frame

    def isMaximized(self):
        return False

    def isFullScreen(self):
        return False

    def isVisible(self):
        return True

    def width(self):
        return self._frame.width()

    def minimumHeight(self):
        return 0

    def minimumSizeHint(self):
        return FakeSize(self._content_height)

    def showNormal(self):
        pass

    def resize(self, w, h):
        self.resizes.append((w, h))

    def move(self, x, y):
        # Enregistre l'état du drapeau au moment du move pour vérifier la garde.
        self.applying_during_move.append(self.applying)
        self.moves.append((x, y))


# --- apply_panel_mode -------------------------------------------------------

def test_vertical_panel_docks_to_nearest_side_right():
    # Fenêtre côté droit de l'écran -> colonne dockée à droite.
    w = FakePanelWindow(horizontal_mode=False, frame=(1500, 300, 400, 500))
    w.apply_panel_mode()
    assert w.resizes == [(300, 420)]          # largeur fine, hauteur utile seulement
    # Dockée au bord droit, position verticale CONSERVÉE (y=300).
    assert w.moves == [(1920 - 300, 300)]


def test_vertical_panel_docks_to_nearest_side_left():
    w = FakePanelWindow(horizontal_mode=False, frame=(100, 300, 400, 500))
    w.apply_panel_mode()
    assert w.resizes == [(300, 420)]
    assert w.moves == [(0, 300)]             # x docké, y conservé


def test_vertical_panel_vertical_position_clamped_to_screen():
    # Fenêtre plus basse que la zone utile : la position verticale est bornée
    # pour que le panneau reste entièrement visible après réduction d'écran.
    w = FakePanelWindow(frame=(1500, 2000, 400, 500), avail=(0, 0, 1920, 1040))
    w.apply_panel_mode()
    # y borné à sy + sh - h = 0 + 1040 - 420 = 620
    assert w.moves == [(1920 - 300, 620)]


def test_fit_window_to_content_keeps_position_in_compact_vertical():
    # Changement de contenu (file, messagerie, reconstruction) : hauteur
    # ajustée, position conservée — pas de redockage sur le bord.
    w = FakePanelWindow(frame=(900, 300, 400, 500))
    w.fit_window_to_content()                # dock=False par défaut
    assert w.resizes == [(400, 420)]         # largeur inchangée, hauteur utile
    assert w.moves == [(900, 300)]           # position intacte


def test_fit_window_to_content_docks_when_requested():
    # Activation du mode / changement de forme : dockage explicite.
    w = FakePanelWindow(frame=(900, 300, 400, 500))
    w.fit_window_to_content(dock=True)
    assert w.moves == [(1920 - 300, 300)]    # bord droit, y conservé


def test_fit_window_to_content_horizontal_always_redocks():
    # La barre horizontale est pleine largeur par nature : repositionnée même
    # sur un simple changement de contenu.
    w = FakePanelWindow(horizontal_mode=True, frame=(600, 400, 400, 500))
    w.fit_window_to_content()
    assert w.resizes == [(1920, 300)]
    assert w.moves == [(0, 0)]


def test_screen_change_debounced_then_redocks_compact_panel():
    w = FakePanelWindow(frame=(900, 300, 400, 500))
    w.on_screen_geometry_changed()           # rafale de signaux regroupée
    w.on_screen_geometry_changed()
    assert w._screen_timer.started == [300, 300]
    # À l'expiration : recentrage si hors écran + redockage du panneau.
    w.ensure_visible = lambda: None
    w._handle_screen_change()
    assert w.moves == [(1920 - 300, 300)]


def test_screen_change_does_not_dock_extended_window():
    w = FakePanelWindow(frame=(900, 300, 400, 500))
    w.compact_mode = False
    calls = []
    w.ensure_visible = lambda: calls.append("ensure")
    w._handle_screen_change()
    assert calls == ["ensure"]
    assert w.moves == []                     # fenêtre étendue jamais redockée


def test_horizontal_panel_docks_to_top():
    w = FakePanelWindow(horizontal_mode=True, frame=(600, 400, 400, 500))
    w.apply_panel_mode()
    assert w.resizes == [(1920, 300)]      # largeur pleine, hauteur = épaisseur
    assert w.moves == [(0, 0)]             # barre en haut


def test_apply_panel_sets_guard_during_move():
    w = FakePanelWindow()
    w.apply_panel_mode()
    # Pendant le repositionnement, applying doit être True (évite la boucle
    # de magnétisme via moveEvent) puis rétabli à False après.
    assert w.applying_during_move == [True]
    assert w.applying is False


def test_apply_panel_noop_when_compact_disabled():
    w = FakePanelWindow()
    w.compact_mode = False
    w.apply_panel_mode()
    assert w.resizes == []
    assert w.moves == []


def test_apply_panel_noop_without_screen():
    w = FakePanelWindow()
    w.current_screen_avail = lambda: None
    w.apply_panel_mode()
    assert w.moves == []


# --- _apply_edge_snap -------------------------------------------------------

def test_snap_moves_window_near_edge():
    # Fenêtre proche du bord gauche (x=10) -> aimantée à x=0.
    w = FakePanelWindow(frame=(10, 300, 300, 500))
    w._apply_edge_snap()
    assert w.moves == [(0, 300)]
    assert w.applying is False


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
