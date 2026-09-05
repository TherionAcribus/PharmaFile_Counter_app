"""Systray et sons extraits de MainWindow (point 10.11).

Le systray se teste réellement en « offscreen » : les QSystemTrayIcon se
construisent, leurs menus aussi. On vérifie surtout ce qui était fragile — le
menu de la file reconstruit à l'ouverture seulement, et le retrait effectif des
icônes à la fermeture (sinon elles survivent au processus).
"""

import logging
import os
import sys

import pytest
from PySide6.QtWidgets import QApplication, QWidget

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio  # noqa: E402
import main  # noqa: E402
from tray_manager import TrayManager  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeWindow(QWidget):
    """Fenêtre minimale : le systray ne lui demande que des actions.

    C'est un vrai QWidget : les icônes de la barre d'état lui sont rattachées
    (parent Qt), comme en production.
    """

    def __init__(self, patients=None):
        super().__init__()
        self.list_patients = patients if patients is not None else []
        self.calls = []
        self.selected = []

    def call_web_function_pause(self):
        self.calls.append("pause")

    def call_web_function_validate(self):
        self.calls.append("validate")

    def call_web_function_validate_and_call_next(self):
        self.calls.append("next")

    def select_patient(self, patient_id):
        self.selected.append(patient_id)


@pytest.fixture
def tray(qapp):
    window = FakeWindow()
    manager = TrayManager(window, logger=logging.getLogger("test.tray"))
    yield manager
    manager.cleanup()


# --- construction -----------------------------------------------------------

def test_trois_icones_creees(tray):
    tray.setup()
    assert len(tray.icons) == 3
    assert [icon.toolTip() for icon in tray.icons] == [
        "Pause", "Prochain patient", "Valider patient"]


def test_chaque_icone_a_un_menu(tray):
    tray.setup()
    assert all(icon.contextMenu() is not None for icon in tray.icons)


def test_setup_idempotent(tray):
    """Un second appel ne doit pas empiler six icônes dans la barre."""
    tray.setup()
    tray.setup()
    assert len(tray.icons) == 3


def test_clic_gauche_declenche_l_action(tray):
    from PySide6.QtWidgets import QSystemTrayIcon
    tray.setup()
    for icon in tray.icons:
        icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    assert tray.window.calls == ["pause", "next", "validate"]


def test_clic_droit_ne_declenche_aucune_action(tray):
    """Le clic droit ouvre le menu : il ne doit pas valider un patient."""
    from PySide6.QtWidgets import QSystemTrayIcon
    tray.setup()
    for icon in tray.icons:
        icon.activated.emit(QSystemTrayIcon.ActivationReason.Context)
    assert tray.window.calls == []


# --- menu de la file --------------------------------------------------------

def test_menu_de_file_construit_a_l_ouverture(tray):
    tray.setup()
    tray.window.list_patients = [
        {"id": 1, "call_number": "A1", "activity": "Ordonnance"},
        {"id": 2, "call_number": "B2", "activity": "Conseil"},
    ]
    # Rien tant que le menu n'est pas ouvert.
    assert tray.patient_menu.actions() == []
    tray.patient_menu.aboutToShow.emit()
    assert [a.text() for a in tray.patient_menu.actions()] == [
        "A1 - Ordonnance", "B2 - Conseil"]


def test_menu_de_file_appelle_le_patient_choisi(tray):
    tray.setup()
    tray.window.list_patients = [{"id": 7, "call_number": "A1", "activity": "Ordonnance"}]
    tray.patient_menu.aboutToShow.emit()
    tray.patient_menu.actions()[0].trigger()
    assert tray.window.selected == [7]


def test_menu_de_file_ignore_les_patients_incomplets(tray):
    tray.setup()
    tray.window.list_patients = [
        {"id": 1, "call_number": "A1"},                              # activité manquante
        None,                                                        # entrée invalide
        {"id": 2, "call_number": "B2", "activity": "Conseil"},
    ]
    tray.patient_menu.aboutToShow.emit()
    assert [a.text() for a in tray.patient_menu.actions()] == ["B2 - Conseil"]


def test_menu_de_file_vide_sans_patients(tray):
    tray.setup()
    tray.window.list_patients = None
    tray.patient_menu.aboutToShow.emit()
    assert tray.patient_menu.actions() == []


# --- fermeture --------------------------------------------------------------

def test_cleanup_retire_les_icones(tray):
    tray.setup()
    tray.cleanup()
    assert tray.icons == []


def test_cleanup_sans_setup_est_sans_effet(tray):
    tray.cleanup()   # ne doit pas lever


def test_main_window_delegue_le_systray():
    """La fenêtre ne construit plus d'icône elle-même."""
    assert not hasattr(main.MainWindow, "setup_systray")
    assert not hasattr(main.MainWindow, "_rebuild_tray_patient_menu")
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "main.py"), encoding="utf-8") as fh:
        source = fh.read()
    assert "QSystemTrayIcon" not in source


# --- sons -------------------------------------------------------------------

def test_les_trois_sons_sont_charges(qapp):
    player = audio.build_audio_player(None, volume=50)
    assert set(player.sounds) == {"patient_taken", "ding", "please_validate"}


def test_les_fichiers_sons_existent():
    """Chemins résolus par resources : valables aussi en build onefile."""
    from resources import resource_path
    for relative in audio.SOUNDS.values():
        assert os.path.exists(resource_path(relative)), relative


def test_volume_converti_en_gain_perceptuel(qapp):
    """Le pourcentage du curseur n'est plus envoyé tel quel : Qt attend un gain
    LINÉAIRE alors que le curseur est perçu logarithmiquement (50 % sonnait
    presque comme 100 %). Bornes exactes, milieu nettement sous la moitié."""
    assert audio.perceptual_volume(0) == 0.0
    assert audio.perceptual_volume(100) == pytest.approx(1.0)
    assert audio.perceptual_volume(50) < 0.5

    player = audio.build_audio_player(None, volume=70)
    assert player.volume == 70                       # réglage conservé tel quel
    assert player.audio_output.volume() == pytest.approx(audio.perceptual_volume(70))


def test_volume_perceptuel_monotone_et_borne(qapp):
    """Croissant, et tolérant aux valeurs aberrantes (réglage utilisateur)."""
    gains = [audio.perceptual_volume(p) for p in range(0, 101, 5)]
    assert gains == sorted(gains)
    assert audio.perceptual_volume(-10) == 0.0
    assert audio.perceptual_volume(400) == pytest.approx(1.0)
    assert audio.perceptual_volume(None) == 0.0      # valeur illisible : silence


# --- mode muet (indépendant du volume) --------------------------------------

def test_mode_muet_conserve_le_volume(qapp):
    """Se taire ne doit plus passer par « curseur à 0 » : le réglage préféré est
    intact au rétablissement."""
    player = audio.build_audio_player(None, volume=70)
    player.set_muted(True)
    assert player.muted is True
    assert player.audio_output.isMuted() is True
    assert player.volume == 70
    assert player.audio_output.volume() == pytest.approx(audio.perceptual_volume(70))

    player.set_muted(False)
    assert player.audio_output.isMuted() is False
    assert player.audio_output.volume() == pytest.approx(audio.perceptual_volume(70))


def test_lecteur_construit_muet_si_demande(qapp):
    player = audio.build_audio_player(None, volume=40, muted=True)
    assert player.audio_output.isMuted() is True
    assert player.volume == 40


# --- diagnostic : les erreurs audio ne restent plus dans le journal ----------

def test_erreur_audio_memorisee_et_signalee(qapp):
    """Un échec de lecture était seulement journalisé : l'utilisateur n'avait
    qu'un silence. Le lecteur retient le dernier problème et le signale."""
    from PySide6.QtMultimedia import QMediaPlayer

    player = audio.build_audio_player(None, volume=50)
    recus = []
    player.error_changed.connect(recus.append)

    player.handle_error(QMediaPlayer.Error.ResourceError, "Périphérique audio absent")
    assert "Périphérique audio absent" in player.last_error
    assert recus == [player.last_error]

    player.clear_error()
    assert player.last_error == ""
    assert recus[-1] == ""


def test_erreur_audio_nomme_le_son_fautif(qapp, fake_player):
    from PySide6.QtMultimedia import QMediaPlayer

    fake_player.play_sound("ding")
    fake_player.handle_error(QMediaPlayer.Error.FormatError, "Format non pris en charge")
    assert "ding" in fake_player.last_error
    assert "Format non pris en charge" in fake_player.last_error


def test_media_illisible_signale_sans_erreur_detaillee(qapp, fake_player):
    """Qt signale parfois InvalidMedia SANS errorOccurred : on prévient quand même."""
    from PySide6.QtMultimedia import QMediaPlayer

    fake_player.play_sound("ding")
    fake_player._handle_media_status(QMediaPlayer.MediaStatus.InvalidMedia)
    assert "ding" in fake_player.last_error
    assert "illisible" in fake_player.last_error


def test_erreur_detaillee_conservee_apres_invalid_media(qapp, fake_player):
    """Le message précis de Qt ne doit pas être écrasé par le repli générique."""
    from PySide6.QtMultimedia import QMediaPlayer

    fake_player.play_sound("ding")
    fake_player.handle_error(QMediaPlayer.Error.FormatError, "Codec MP3 manquant")
    fake_player._handle_media_status(QMediaPlayer.MediaStatus.InvalidMedia)
    assert "Codec MP3 manquant" in fake_player.last_error


def test_fichier_son_manquant_signale_au_demarrage(qapp, monkeypatch):
    """Seule vérification faite « au chargement » : l'existence des fichiers.
    Un son absent est annoncé tout de suite, pas au premier appel de patient."""
    monkeypatch.setattr(audio.os.path, "exists", lambda path: False)
    player = audio.build_audio_player(None, volume=50)
    assert "introuvables" in player.last_error
    assert "ding" in player.last_error


def test_son_inconnu_ne_leve_pas(qapp, caplog):
    player = audio.build_audio_player(None, volume=10)
    with caplog.at_level(logging.WARNING, logger="appcomptoir.audio"):
        player.play_sound("inexistant")
    assert any("inexistant" in r.getMessage() for r in caplog.records)


# --- test direct d'un son dans les préférences ------------------------------
# Le bouton « Tester un son » joue via audio_player.play_sound, SANS passer par
# le gestionnaire de notifications : deux clics rapprochés rejouent le son
# (pas de déduplication, contrairement à « Tester la notification »). D'où
# force=True, qui court-circuite aussi la politique des sons rapprochés.

def _fake_dialog(parent, volume=50):
    """« Faux self » du dialogue : seules les méthodes du bouton « Jouer » et de
    l'aperçu audio sont greffées (comme test_preferences_volume_preview)."""
    import types

    from PySide6.QtWidgets import QCheckBox, QComboBox, QSpinBox
    import preferences as prefs

    dialog = types.SimpleNamespace(
        _volume_previewed=False,
        current_volume=volume,
        current_muted=False,
        volume_spinbox=QSpinBox(),
        mute_checkbox=QCheckBox(),
        sound_test_combo=QComboBox(),
        parent=lambda: parent,
    )
    for name in ("_player", "_set_player_volume", "apply_audio_preview", "test_sound"):
        setattr(dialog, name,
                types.MethodType(getattr(prefs.PreferencesDialog, name), dialog))
    return dialog


def test_test_sound_joue_directement_sans_deduplication(qapp):
    from unittest.mock import Mock, call
    import types

    import preferences as prefs

    audio_player = Mock()
    parent = types.SimpleNamespace(audio_player=audio_player)

    dialog = _fake_dialog(parent)
    dialog.volume_spinbox.setValue(70)
    for name in audio.SOUNDS:
        dialog.sound_test_combo.addItem(prefs.SOUND_LABELS.get(name, name), name)

    # Premier clic : joue le son par défaut (« ding », premier de SOUNDS).
    dialog.test_sound()
    assert audio_player.play_sound.call_count == 1

    # Second clic rapproché : REJOUE le son (pas de déduplication).
    dialog.test_sound()
    assert audio_player.play_sound.call_count == 2

    # Changement de son : joue le son sélectionné.
    dialog.sound_test_combo.setCurrentIndex(1)
    dialog.test_sound()
    assert audio_player.play_sound.call_args == call(
        dialog.sound_test_combo.currentData(), force=True)


def test_test_sound_applique_le_volume_d_aperçu(qapp):
    """Le volume du spinbox est appliqué au lecteur avant de jouer le son."""
    from unittest.mock import Mock
    import types

    import preferences as prefs

    audio_player = Mock()
    parent = types.SimpleNamespace(audio_player=audio_player)

    dialog = _fake_dialog(parent)
    dialog.volume_spinbox.setValue(80)
    for name in audio.SOUNDS:
        dialog.sound_test_combo.addItem(prefs.SOUND_LABELS.get(name, name), name)

    dialog.test_sound()
    audio_player.set_volume.assert_called_once_with(80)
    assert dialog._volume_previewed is True


def test_test_sound_sans_lecteur_ne_leve_pas(qapp):
    """Pas de lecteur audio (tests, ou avant init_audio) : ne lève pas."""
    import types

    import preferences as prefs

    parent = types.SimpleNamespace(audio_player=None)
    dialog = _fake_dialog(parent)
    for name in audio.SOUNDS:
        dialog.sound_test_combo.addItem(prefs.SOUND_LABELS.get(name, name), name)

    # Ne doit pas lever.
    dialog.test_sound()


def test_options_test_son_couvrivent_les_sons_embarques():
    """Le sélecteur « Tester un son » propose exactement les sons de audio.SOUNDS."""
    import preferences as prefs
    assert set(prefs.SOUND_LABELS) == set(audio.SOUNDS)


# --- sons rapprochés (point 3) ----------------------------------------------
# La politique elle-même est testée à part (test_audio_policy.py, sans Qt) ;
# ici on vérifie le CÂBLAGE : ce que le lecteur Qt fait vraiment des décisions.

@pytest.fixture
def fake_player(qapp):
    """Lecteur réel dont le QMediaPlayer est remplacé par un espion.

    Rien n'est joué (pas de périphérique audio en CI) : on observe les appels à
    setSource/play et on simule les fins de lecture.
    """
    from unittest.mock import Mock

    player = audio.build_audio_player(None, volume=50)
    player.player = Mock()
    return player


def _sources(fake_player):
    """Noms logiques des sons réellement envoyés au QMediaPlayer, dans l'ordre."""
    by_url = {url: name for name, url in fake_player.sounds.items()}
    return [by_url[call.args[0]] for call in fake_player.player.setSource.call_args_list]


def _end_of_media(player):
    from PySide6.QtMultimedia import QMediaPlayer
    player._handle_media_status(QMediaPlayer.MediaStatus.EndOfMedia)


def test_un_ding_ne_coupe_plus_une_alerte_parlee(fake_player):
    """Le défaut d'origine : la source était remplacée à chaque demande."""
    fake_player.play_sound("please_validate")
    fake_player.play_sound("ding")
    assert _sources(fake_player) == ["please_validate"]


def test_une_alerte_coupe_le_ding_en_cours(fake_player):
    fake_player.play_sound("ding")
    fake_player.play_sound("patient_taken")
    assert _sources(fake_player) == ["ding", "patient_taken"]


def test_une_alerte_en_attente_part_a_la_fin_de_la_precedente(fake_player):
    fake_player.play_sound("patient_taken")
    fake_player.play_sound("please_validate")
    assert _sources(fake_player) == ["patient_taken"]
    _end_of_media(fake_player)
    assert _sources(fake_player) == ["patient_taken", "please_validate"]
    assert fake_player.player.play.call_count == 2


def test_le_bouton_de_test_rejoue_toujours(fake_player):
    """« Tester un son » (force=True) : chaque clic s'entend, même rapproché."""
    fake_player.play_sound("ding", force=True)
    fake_player.play_sound("ding", force=True)
    assert _sources(fake_player) == ["ding", "ding"]


def test_une_erreur_du_lecteur_debloque_la_file(fake_player):
    """Sans cela, un son qui ne s'achève jamais gèlerait tous les suivants."""
    fake_player.play_sound("patient_taken")
    fake_player.play_sound("please_validate")
    fake_player.handle_error("erreur", "périphérique disparu")
    assert _sources(fake_player) == ["patient_taken", "please_validate"]


def test_le_delai_de_garde_debloque_la_file(fake_player):
    """Aucun signal de fin ne vient : le garde-fou enchaîne quand même."""
    fake_player.play_sound("patient_taken")
    fake_player.play_sound("please_validate")
    assert fake_player._watchdog.isActive()
    fake_player._watchdog.timeout.emit()
    assert _sources(fake_player) == ["patient_taken", "please_validate"]


def test_un_media_illisible_pendant_le_demarrage_ne_boucle_pas(fake_player):
    """Qt peut signaler l'échec DANS setSource : pas de réentrance dans _start."""
    from PySide6.QtMultimedia import QMediaPlayer

    fake_player.player.setSource.side_effect = lambda url: fake_player._handle_media_status(
        QMediaPlayer.MediaStatus.InvalidMedia)
    fake_player.play_sound("patient_taken")
    assert _sources(fake_player) == ["patient_taken"]
    assert fake_player.scheduler.current is None   # la file est libre


def test_un_son_inconnu_ne_touche_pas_a_la_lecture_en_cours(fake_player):
    fake_player.play_sound("please_validate")
    fake_player.play_sound("inexistant")
    assert _sources(fake_player) == ["please_validate"]
    assert fake_player.scheduler.current == "please_validate"
