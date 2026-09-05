"""Sons de l'application (point 10.11).

Trois sons ponctuent l'usage du comptoir : « patient déjà pris » (le patient
appelé a été récupéré par un autre comptoir), « ding » (nouveau patient) et
« pensez à valider ». Le démarrage n'enregistre que leurs CHEMINS (et vérifie
que les fichiers existent) : le décodage a lieu à la première lecture, il n'y a
pas de préchargement en mémoire. Si une latence au premier son se révélait
gênante en usage réel, la piste serait de passer ces effets courts en WAV joués
par ``QSoundEffect`` (qui, lui, précharge vraiment) ; tant qu'aucune latence
n'est constatée, ``QMediaPlayer`` suffit et évite un second moteur audio.

Isolé de ``main.py`` : la fenêtre demande un son par son NOM, elle n'a plus à
connaître QMediaPlayer, les chemins de fichiers ni la conversion du volume.

Un seul ``QMediaPlayer`` sert tous les sons : demander un son en remplace donc
la source, ce qui coupait la lecture en cours. Les demandes rapprochées passent
maintenant par ``audio_policy.SoundScheduler`` (priorité aux alertes parlées,
« ding » jamais mis en attente, file bornée et périssable) ; ce module ne fait
qu'exécuter ses décisions.

Deux réglages distincts arrivent d'en haut : le VOLUME (0-100 %, converti en
gain perceptuel) et le MODE MUET (coupe la sortie sans toucher au volume, pour
retrouver intact le réglage préféré en rétablissant le son). Enfin, les
problèmes audio ne sont plus seulement journalisés : le dernier message est
conservé (``last_error``) et signalé (``error_changed``), ce qui permet aux
préférences de l'AFFICHER au lieu de laisser l'utilisateur devant un silence
inexpliqué.
"""

import logging
import os

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudio, QAudioOutput, QMediaPlayer

from audio_policy import DROP, PLAY, PREEMPT, QUEUE, SoundScheduler
from resources import resource_path

logger = logging.getLogger("appcomptoir.audio")

#: Sons embarqués : nom logique -> fichier dans les ressources.
SOUNDS = {
    "patient_taken": "assets/sounds/already_taken.mp3",
    "ding": "assets/sounds/ding.mp3",
    "please_validate": "assets/sounds/please_validate.mp3",
}

#: Garde-fou : durée au-delà de laquelle un son est considéré terminé même sans
#: signal de fin (média illisible, périphérique disparu…). Sans lui, une lecture
#: qui ne s'achève jamais bloquerait définitivement la file d'attente.
WATCHDOG_MS = 15000


def perceptual_volume(percent):
    """Convertit un pourcentage d'interface (0-100) en gain linéaire pour Qt.

    L'ancienne conversion ``volume / 100`` envoyait le pourcentage tel quel :
    or ``setVolume`` attend un gain LINÉAIRE alors que l'oreille, elle, est
    logarithmique. Conséquence : la moitié du curseur s'entendait presque aussi
    fort que le maximum, et tout le bas de la course ne servait quasiment à
    rien. Qt recommande de traiter un curseur d'interface comme une échelle
    logarithmique et de convertir explicitement (``QAudio::convertVolume``) :
    50 % sonne alors bien comme « à mi-volume ».

    Fonction tolérante (valeur absente ou hors bornes) : le volume vient d'un
    réglage utilisateur, il ne doit jamais faire échouer la lecture.
    """
    try:
        percent = float(percent)
    except (TypeError, ValueError):
        logger.warning("Volume illisible (%r) : son coupé par précaution", percent)
        percent = 0.0
    percent = max(0.0, min(100.0, percent))
    linear = QAudio.convertVolume(
        percent / 100,
        QAudio.VolumeScale.LogarithmicVolumeScale,
        QAudio.VolumeScale.LinearVolumeScale,
    )
    # convertVolume peut renvoyer -0.0 (ou déborder très légèrement) : on borne.
    return max(0.0, min(1.0, float(linear)))


class AudioPlayer(QObject):
    #: Dernier problème audio connu (chaîne vide = aucun), émis à chaque
    #: changement pour que les préférences l'affichent en direct.
    error_changed = Signal(str)

    def __init__(self, parent=None, scheduler=None):
        super().__init__(parent)
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.sounds = {}
        self.scheduler = scheduler or SoundScheduler()

        #: Volume DEMANDÉ, en pourcentage : conservé tel quel pour que le mode
        #: muet n'écrase pas le réglage de l'utilisateur.
        self.volume = 0
        self.muted = False
        self.last_error = ""

        # Vrai pendant ``_start`` : Qt peut signaler un média invalide DANS
        # setSource/play, il ne faut pas enchaîner la file par réentrance.
        self._starting = False
        self._finish_requested = False
        # Son en cours (pour nommer le son fautif dans le message d'erreur) et
        # « une erreur détaillée a déjà été signalée pour cette lecture ».
        self._current_sound = None
        self._error_reported = False

        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(lambda: self._advance("délai de garde"))

        # Ajout des callbacks
        self.player.errorOccurred.connect(self.handle_error)
        self.player.mediaStatusChanged.connect(self._handle_media_status)

    def add_sound(self, name, file_path):
        self.sounds[name] = QUrl.fromLocalFile(file_path)

    def play_sound(self, name, force=False):
        """Demande un son par son nom.

        ``force`` court-circuite la politique des sons rapprochés : le son passe
        devant tout et coupe la lecture en cours (bouton « Tester un son » des
        préférences, où chaque clic doit s'entendre).
        """
        if name not in self.sounds:
            logger.warning("Son inconnu : %s", name)
            return
        decision = self.scheduler.request(name, force=force)
        if decision.action in (PLAY, PREEMPT):
            if decision.action == PREEMPT:
                logger.debug("Son « %s » interrompu par « %s » (%s)",
                             decision.replaced, name, decision.reason)
            self._start(name)
        elif decision.action == QUEUE:
            logger.debug("Son « %s » mis en attente (%s en cours)",
                         name, self.scheduler.current)
        elif decision.action == DROP:
            logger.debug("Son « %s » ignoré : %s", name, decision.reason)

    def set_volume(self, volume):
        """Volume attendu en pourcentage (0-100), converti en gain perceptuel.

        Le pourcentage est mémorisé tel quel : couper puis rétablir le son
        retrouve exactement le réglage choisi.
        """
        self.volume = volume
        self.audio_output.setVolume(perceptual_volume(volume))

    def set_muted(self, muted):
        """Coupe (ou rétablit) la sortie SANS toucher au volume réglé.

        Mettre le curseur à 0 « marchait » pour se taire, mais faisait perdre le
        volume préféré : le mode muet est donc un réglage à part.
        """
        self.muted = bool(muted)
        self.audio_output.setMuted(self.muted)
        logger.debug("Sons %s (volume conservé à %s%%)",
                     "coupés" if self.muted else "rétablis", self.volume)

    # --- diagnostic ---------------------------------------------------------

    def report_error(self, message):
        """Enregistre le dernier problème audio et le signale (préférences).

        Chaîne vide = plus d'erreur connue. Le signal n'est émis que sur un vrai
        changement, pour ne pas faire clignoter l'affichage.
        """
        message = message or ""
        if message == self.last_error:
            return
        self.last_error = message
        self.error_changed.emit(message)

    def clear_error(self):
        """Oublie le dernier problème signalé (avant un essai volontaire :
        l'utilisateur doit pouvoir rattacher le message affiché à SON clic)."""
        self.report_error("")

    def handle_error(self, error, error_string):
        logger.error("Erreur du lecteur audio (%s) : %s", error, error_string)
        self._error_reported = True
        detail = error_string or str(error)
        if self._current_sound:
            self.report_error(f"Son « {self._current_sound} » : {detail}")
        else:
            self.report_error(f"Lecteur audio : {detail}")
        self._finish("erreur du lecteur")

    # --- exécution des décisions -------------------------------------------

    def _start(self, name):
        self._starting = True
        self._finish_requested = False
        self._current_sound = name
        self._error_reported = False
        try:
            self.player.setSource(self.sounds[name])
            self.player.play()
        finally:
            self._starting = False
        self._watchdog.start(WATCHDOG_MS)
        if self._finish_requested:
            # Le média s'est révélé illisible pendant le démarrage : on enchaîne
            # maintenant que ``_start`` est terminé.
            self._advance("média illisible")

    def _handle_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            # Repli : Qt signale parfois un média invalide SANS errorOccurred.
            # Si une erreur détaillée est déjà arrivée, on la conserve.
            if not self._error_reported:
                self._error_reported = True
                self.report_error(
                    f"Son « {self._current_sound} » illisible : fichier absent "
                    "ou format non pris en charge par le système.")
            self._finish("fin de lecture")
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._finish("fin de lecture")

    def _finish(self, reason):
        """Fin de lecture, différée si elle survient pendant ``_start``."""
        if self._starting:
            self._finish_requested = True
            return
        self._advance(reason)

    def _advance(self, reason):
        """Enchaîne la file d'attente après la fin du son en cours."""
        self._watchdog.stop()
        following = self.scheduler.finished()
        for name in following.expired:
            logger.debug("Alerte « %s » abandonnée : attente trop longue", name)
        if following.sound:
            logger.debug("Enchaînement du son « %s » (%s)", following.sound, reason)
            self._start(following.sound)


def build_audio_player(parent, volume, muted=False):
    """Crée le lecteur, référence les sons embarqués et applique volume + muet.

    Les chemins passent par ``resources`` : les sons fonctionnent donc aussi dans
    un exécutable PyInstaller onefile. L'EXISTENCE des fichiers est vérifiée ici
    (seule vérification « au chargement » : rien n'est décodé d'avance) — un son
    manquant est signalé tout de suite dans les préférences, au lieu d'un silence
    inexpliqué au premier appel de patient.
    """
    player = AudioPlayer(parent)
    missing = []
    for name, relative_path in SOUNDS.items():
        path = resource_path(relative_path)
        if not os.path.exists(path):
            missing.append(name)
        player.add_sound(name, path)
    player.set_volume(volume)
    player.set_muted(muted)
    if missing:
        logger.error("Fichiers son introuvables : %s", ", ".join(missing))
        player.report_error(
            "Fichiers son introuvables : " + ", ".join(missing)
            + ". Réinstallez l'application pour rétablir les sons.")
    return player
