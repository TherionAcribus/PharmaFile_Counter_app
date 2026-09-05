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


# --------------------------------------------------------------------------
# Accessibilité (point 28) : ton configurable + sévérité (titre + couleurs)
# --------------------------------------------------------------------------

from notification import CustomNotification  # noqa: E402
from accessibility import (  # noqa: E402
    TONE_HUMOROUS, TONE_SOBER, severity_colors, severity_glyph,
    notification_severity, passes_aa,
)


def _make_notif(main_window, origin, tone=TONE_SOBER):
    main_window.message_tone = tone
    return CustomNotification(data={"origin": origin, "message": "m"},
                              parent=main_window, internal=True)


def test_title_uses_sober_tone_by_default(main_window):
    notif = _make_notif(main_window, "please_validate", TONE_SOBER)
    assert "Patient à valider" in notif.title
    assert "phoque" not in notif.title


def test_title_uses_humorous_tone_when_configured(main_window):
    notif = _make_notif(main_window, "please_validate", TONE_HUMOROUS)
    assert "phoque" in notif.title


def test_title_carries_severity_glyph(main_window):
    notif = _make_notif(main_window, "no_paper")
    assert notif.title.startswith(severity_glyph(notification_severity("no_paper")))


def test_colors_are_valid_and_contrasted(main_window):
    # L'ancien « light_green » (invalide) est remplacé par une couleur contrastée.
    notif = _make_notif(main_window, "paper_ok")
    bg, fg = severity_colors(notification_severity("paper_ok"))
    assert notif.background_color == bg
    assert notif.font_color == fg
    assert passes_aa(notif.font_color, notif.background_color)


def test_unknown_origin_falls_back_to_origin_text(main_window):
    notif = _make_notif(main_window, "origine_inconnue")
    assert "origine_inconnue" in notif.title


# --- Son : réglage distinct de l'affichage --------------------------------

class FakeAudioPlayer:
    def __init__(self):
        self.played = []

    def play_sound(self, name):
        self.played.append(name)


def test_sound_is_played_when_allowed(main_window):
    main_window.audio_player = FakeAudioPlayer()
    mgr = NotificationManager(main_window)
    mgr.notify(_data("please_validate", "valider"), internal=True, play_sound=True)
    assert main_window.audio_player.played == ["please_validate"]


def test_notification_can_be_shown_without_sound(main_window):
    # « Afficher » et « jouer un son » sont deux réglages indépendants : une
    # catégorie dont le son est coupé s'affiche quand même, en silence.
    main_window.audio_player = FakeAudioPlayer()
    mgr = NotificationManager(main_window)
    notif = mgr.notify(_data("please_validate", "valider"), internal=True, play_sound=False)
    assert notif is not None and notif.isVisible()
    assert main_window.audio_player.played == []


def test_queued_notification_keeps_its_own_sound_setting(main_window):
    # Une notification mise en file garde le réglage de son décidé à l'émission.
    main_window.audio_player = FakeAudioPlayer()
    mgr = NotificationManager(main_window, max_visible=1)
    first = mgr.notify(_data("connection", "1"), internal=True, play_sound=True)
    mgr.notify(_data("connection", "2"), internal=True, play_sound=False)
    assert len(mgr.pending) == 1
    first.close()                      # libère la place : la file se vide
    assert main_window.audio_player.played == ["ding"]   # la 2e est restée muette


# --- Annulation d'un rappel devenu sans objet (point 2) -------------------

def test_dismiss_drops_the_reminder_still_queued(main_window):
    """Un rappel « pensez à valider » encore EN FILE au moment de la validation
    ne doit jamais ressortir : il sonnait quand une place se libérait."""
    main_window.audio_player = FakeAudioPlayer()
    mgr = NotificationManager(main_window, max_visible=1)
    visible = mgr.notify(_data("connection", "serveur"), internal=True)
    mgr.notify(_data("please_validate", "valider"), internal=True, patient_id=42)
    assert len(mgr.pending) == 1

    assert mgr.dismiss("please_validate") == 1     # le patient vient d'être validé
    assert len(mgr.pending) == 0

    visible.close()                                # une place se libère
    assert mgr.active_notifications == []
    assert "please_validate" not in main_window.audio_player.played


def test_dismiss_closes_visible_and_queued_together(main_window):
    main_window.audio_player = FakeAudioPlayer()
    mgr = NotificationManager(main_window, max_visible=1)
    mgr.notify(_data("please_validate", "valider"), internal=True, patient_id=42)
    mgr.notify(_data("please_validate", "valider (2e message)"), internal=True, patient_id=42)
    assert len(mgr.active_notifications) == 1 and len(mgr.pending) == 1

    assert mgr.dismiss("please_validate") == 2
    assert mgr.active_notifications == [] and len(mgr.pending) == 0


def test_dismiss_leaves_other_origins_alone(main_window):
    mgr = NotificationManager(main_window, max_visible=1)
    mgr.notify(_data("connection", "serveur"), internal=True)
    mgr.notify(_data("new_patient", "arrivée"), internal=True)   # en file

    assert mgr.dismiss("please_validate") == 0
    assert len(mgr.active_notifications) == 1 and len(mgr.pending) == 1


def test_dismiss_can_target_a_single_patient(main_window):
    mgr = NotificationManager(main_window, max_visible=1)
    mgr.notify(_data("please_validate", "patient A"), internal=True, patient_id=1)
    mgr.notify(_data("please_validate", "patient B"), internal=True, patient_id=2)

    assert mgr.dismiss("please_validate", patient_id=2) == 1   # seul B est annulé
    assert len(mgr.pending) == 0
    assert len(mgr.active_notifications) == 1
    assert mgr.active_notifications[0].patient_id == 1


def test_queued_reminder_is_dropped_when_its_patient_is_gone(main_window):
    """Filet de sécurité : même sans annulation explicite, un rappel rattaché à
    un patient qui n'est plus celui du comptoir ne sort pas de la file."""
    main_window.audio_player = FakeAudioPlayer()
    main_window.patient_id = 7
    main_window.is_notification_obsolete = (
        lambda origin, patient_id: origin == "please_validate"
        and patient_id is not None and patient_id != main_window.patient_id)

    mgr = NotificationManager(main_window, max_visible=1)
    visible = mgr.notify(_data("connection", "serveur"), internal=True)
    mgr.notify(_data("please_validate", "valider"), internal=True, patient_id=7)
    main_window.patient_id = 8            # patient suivant : le rappel est caduc

    visible.close()
    assert mgr.active_notifications == []
    assert "please_validate" not in main_window.audio_player.played


def test_queued_reminder_of_the_current_patient_is_still_shown(main_window):
    main_window.audio_player = FakeAudioPlayer()
    main_window.patient_id = 7
    main_window.is_notification_obsolete = (
        lambda origin, patient_id: origin == "please_validate"
        and patient_id is not None and patient_id != main_window.patient_id)

    mgr = NotificationManager(main_window, max_visible=1)
    visible = mgr.notify(_data("connection", "serveur"), internal=True)
    mgr.notify(_data("please_validate", "valider"), internal=True, patient_id=7)

    visible.close()
    assert len(mgr.active_notifications) == 1
    assert main_window.audio_player.played == ["ding", "please_validate"]
