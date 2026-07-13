"""Tests du NotificationManager (point 26), PySide6 réel (offscreen).

Vérifie : déduplication des messages identiques (coupure réseau), limite du
nombre visible + file d'attente et vidage, absence de vol de focus, et choix d'un
écran valide. On utilise une fausse fenêtre principale (QWidget portant les
attributs lus par les notifications) pour créer de vraies CustomNotification sans
instancier toute l'application.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from notification import NotificationManager  # noqa: E402


class FakeMainWindow(QWidget):
    """Fenêtre principale minimale : porte les attributs lus par les notifications."""

    def __init__(self):
        super().__init__()
        self.audio_player = None            # pas de son en test
        self.notification_font_size = 12
        self.notification_duration = 5
        self.notification_corner = "bottom-left"


@pytest.fixture
def main_window(qapp_shared):
    w = FakeMainWindow()
    w.resize(400, 300)
    w.show()
    yield w
    w.close()


@pytest.fixture(scope="module")
def qapp_shared():
    # conftest fournit déjà une QApplication de session ; ce fixture n'existe que
    # pour exprimer la dépendance dans main_window.
    yield True


def _data(origin="connection", message="Le serveur est inaccessible."):
    return {"origin": origin, "message": message}


def test_duplicate_is_deduplicated(main_window):
    mgr = NotificationManager(main_window)
    first = mgr.notify(_data(), internal=True)
    second = mgr.notify(_data(), internal=True)  # identique
    assert first is not None
    assert second is None
    assert len(mgr.active_notifications) == 1


def test_distinct_notifications_both_shown(main_window):
    mgr = NotificationManager(main_window)
    mgr.notify(_data(origin="connection", message="a"), internal=True)
    mgr.notify(_data(origin="new_patient", message="b"), internal=True)
    assert len(mgr.active_notifications) == 2


def test_queue_beyond_max_visible(main_window):
    mgr = NotificationManager(main_window, max_visible=2)
    mgr.notify(_data(message="1"), internal=True)
    mgr.notify(_data(message="2"), internal=True)
    third = mgr.notify(_data(message="3"), internal=True)
    assert third is None
    assert len(mgr.active_notifications) == 2
    assert len(mgr.pending) == 1


def test_closing_active_drains_queue(main_window):
    mgr = NotificationManager(main_window, max_visible=2)
    mgr.notify(_data(message="1"), internal=True)
    mgr.notify(_data(message="2"), internal=True)
    mgr.notify(_data(message="3"), internal=True)
    assert len(mgr.pending) == 1

    mgr.active_notifications[0].close()  # libère une place

    assert len(mgr.active_notifications) == 2
    assert len(mgr.pending) == 0


def test_duplicate_in_queue_not_added_twice(main_window):
    mgr = NotificationManager(main_window, max_visible=1)
    mgr.notify(_data(message="1"), internal=True)      # visible
    mgr.notify(_data(message="2"), internal=True)      # en file
    mgr.notify(_data(message="2"), internal=True)      # doublon en file -> ignoré
    assert len(mgr.pending) == 1


def test_notification_does_not_steal_focus(main_window):
    mgr = NotificationManager(main_window)
    notif = mgr.notify(_data(), internal=True)
    assert notif.testAttribute(Qt.WA_ShowWithoutActivating) is True
    assert bool(notif.windowFlags() & Qt.WindowDoesNotAcceptFocus)


def test_target_screen_is_valid(main_window):
    mgr = NotificationManager(main_window)
    screen = mgr._target_screen()
    assert screen is not None


def test_positions_applied_on_target_screen(main_window):
    """Les notifications sont placées dans la zone de l'écran cible (pas hors champ)."""
    mgr = NotificationManager(main_window)
    mgr.notify(_data(message="x"), internal=True)
    geo = mgr._target_screen().availableGeometry()
    notif = mgr.active_notifications[0]
    # Le coin haut-gauche de la notification est dans les limites de l'écran cible.
    assert geo.x() <= notif.x() <= geo.x() + geo.width()
    assert geo.y() <= notif.y() <= geo.y() + geo.height()


def test_reads_configured_corner(main_window):
    main_window.notification_corner = "top-right"
    mgr = NotificationManager(main_window)
    mgr.notify(_data(message="x"), internal=True)
    geo = mgr._target_screen().availableGeometry()
    notif = mgr.active_notifications[0]
    # Coin haut-droite : proche du haut de l'écran.
    assert notif.y() < geo.y() + geo.height() // 2
