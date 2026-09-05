"""Le test de notification n'est qu'un APERÇU du volume (son, point 3).

« Tester la notification » joue le son au volume en cours d'édition : il modifie
donc le volume du lecteur principal AVANT tout enregistrement. Fermer les
préférences sans enregistrer restaurait le thème mais pas le volume (volume 50,
test à 90, annulation → le lecteur restait à 90). Le volume enregistré est
désormais rétabli à l'annulation, comme le skin.

L'aperçu porte aussi sur le MODE MUET (point 4) : décocher « couper tous les
sons » puis tester doit s'entendre, et l'annulation doit rendre le mode muet
enregistré comme elle rend le volume.

Tests « faux self » (comme test_preferences_workers) pour la logique, plus un
vrai QDialog pour vérifier le chemin complet de reject().
"""

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from PySide6.QtWidgets import QDialog, QWidget  # noqa: E402

import preferences  # noqa: E402


class FakePlayer:
    def __init__(self, volume=50, muted=False):
        self.volume = volume
        self.muted = muted

    def set_volume(self, volume):
        self.volume = volume

    def set_muted(self, muted):
        self.muted = muted


class FakeParent:
    def __init__(self, player):
        self.audio_player = player
        self.notifications = []

    def show_notification(self, data, **kwargs):
        self.notifications.append((data, kwargs))


#: Méthodes du vrai dialogue greffées sur le « faux self ».
_METHODS = ("_player", "_set_player_volume", "apply_audio_preview",
            "restore_volume", "test_notification")


def _dialog(player, edited_volume, saved_volume=50,
            edited_muted=False, saved_muted=False):
    parent = FakeParent(player) if player is not None else types.SimpleNamespace()
    w = types.SimpleNamespace(
        current_volume=saved_volume,
        current_muted=saved_muted,
        _volume_previewed=False,
        volume_spinbox=types.SimpleNamespace(value=lambda: edited_volume),
        mute_checkbox=types.SimpleNamespace(isChecked=lambda: edited_muted),
        notification_font_size_spinbox=types.SimpleNamespace(value=lambda: 12),
    )
    w.parent = lambda: parent
    for name in _METHODS:
        setattr(w, name, types.MethodType(getattr(preferences.PreferencesDialog, name), w))
    return w


# --- Aperçu -----------------------------------------------------------------

def test_test_notification_applies_edited_volume_and_flags_preview():
    player = FakePlayer(volume=50)
    w = _dialog(player, edited_volume=90)
    w.test_notification()
    assert player.volume == 90            # le test s'entend au volume en cours d'édition
    assert w._volume_previewed is True    # ...mais c'est un aperçu, à annuler si besoin
    assert w.parent().notifications       # la notification est bien affichée


def test_test_notification_without_player_does_not_flag_preview():
    w = _dialog(None, edited_volume=90)
    w.parent().show_notification = lambda data, **kwargs: None
    w.test_notification()
    assert w._volume_previewed is False   # rien à restaurer si aucun lecteur


def test_apercu_applique_le_mode_muet_en_cours_d_edition():
    """Décocher « couper tous les sons » puis tester doit s'entendre : le mode
    muet enregistré ne doit pas survivre à l'aperçu."""
    player = FakePlayer(volume=50, muted=True)
    w = _dialog(player, edited_volume=60, edited_muted=False, saved_muted=True)
    w.test_notification()
    assert player.muted is False
    assert player.volume == 60


def test_apercu_coupe_le_son_si_la_case_vient_d_etre_cochee():
    player = FakePlayer(volume=50, muted=False)
    w = _dialog(player, edited_volume=60, edited_muted=True, saved_muted=False)
    w.test_notification()
    assert player.muted is True


# --- Restauration -----------------------------------------------------------

def test_restore_volume_restores_saved_volume_after_preview():
    player = FakePlayer(volume=50)
    w = _dialog(player, edited_volume=90, saved_volume=50)
    w.test_notification()
    w.restore_volume()
    assert player.volume == 50            # le scénario du rapport : 50 → test 90 → annulation
    assert w._volume_previewed is False


def test_restore_volume_rend_aussi_le_mode_muet_enregistre():
    player = FakePlayer(volume=50, muted=True)
    w = _dialog(player, edited_volume=90, saved_volume=50,
                edited_muted=False, saved_muted=True)
    w.test_notification()
    assert player.muted is False          # aperçu : on entend le réglage édité
    w.restore_volume()
    assert player.muted is True           # annulation : on rend le réglage enregistré
    assert player.volume == 50


def test_restore_volume_is_a_noop_without_preview():
    player = FakePlayer(volume=70)        # volume réglé ailleurs entre-temps
    w = _dialog(player, edited_volume=90, saved_volume=50)
    w.restore_volume()
    assert player.volume == 70            # aucun aperçu : on n'écrase rien


def test_restore_volume_is_a_noop_when_volume_unknown():
    player = FakePlayer(volume=90)
    w = _dialog(player, edited_volume=90, saved_volume=50)
    w.test_notification()
    w.current_volume = None               # préférences jamais chargées
    w.restore_volume()
    assert player.volume == 90


# --- reject() complet (vrai QDialog) ----------------------------------------

def test_reject_restores_skin_and_volume(_shared_qapplication):
    parent = QWidget()
    player = FakePlayer(volume=50, muted=True)
    parent.audio_player = player

    dialog = preferences.PreferencesDialog.__new__(preferences.PreferencesDialog)
    QDialog.__init__(dialog, parent)
    dialog._workers, dialog._closing = {}, False
    dialog.current_skin = "Pas de skin"
    dialog.current_volume = 50
    dialog.current_muted = True
    dialog._volume_previewed = False
    dialog.volume_spinbox = types.SimpleNamespace(value=lambda: 90)
    dialog.mute_checkbox = types.SimpleNamespace(isChecked=lambda: False)
    dialog.notification_font_size_spinbox = types.SimpleNamespace(value=lambda: 12)
    parent.show_notification = lambda data, **kwargs: None

    dialog.test_notification()
    assert player.volume == 90
    assert player.muted is False

    dialog.reject()
    assert dialog.result() == QDialog.Rejected
    assert player.volume == 50            # fermeture sans enregistrer : volume rendu
    assert player.muted is True           # ...et mode muet rendu
