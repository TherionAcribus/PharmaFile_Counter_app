"""Sons de l'application (point 10.11).

Trois sons ponctuent l'usage du comptoir : « patient déjà pris » (le patient
appelé a été récupéré par un autre comptoir), « ding » (nouveau patient) et
« pensez à valider ». Ils sont chargés une fois au démarrage puis rejoués à la
demande.

Isolé de ``main.py`` : la fenêtre demande un son par son NOM, elle n'a plus à
connaître QMediaPlayer, les chemins de fichiers ni la conversion du volume.

Un seul ``QMediaPlayer`` sert tous les sons : demander un son en remplace donc
la source, ce qui coupait la lecture en cours. Les demandes rapprochées passent
maintenant par ``audio_policy.SoundScheduler`` (priorité aux alertes parlées,
« ding » jamais mis en attente, file bornée et périssable) ; ce module ne fait
qu'exécuter ses décisions.
"""

import logging

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

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


class AudioPlayer(QObject):
    def __init__(self, parent=None, scheduler=None):
        super().__init__(parent)
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.sounds = {}
        self.scheduler = scheduler or SoundScheduler()

        # Vrai pendant ``_start`` : Qt peut signaler un média invalide DANS
        # setSource/play, il ne faut pas enchaîner la file par réentrance.
        self._starting = False
        self._finish_requested = False

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
        """Volume attendu en pourcentage (0-100) ; Qt le veut entre 0 et 1."""
        self.audio_output.setVolume(volume / 100)

    def handle_error(self, error, error_string):
        logger.error("Erreur du lecteur audio (%s) : %s", error, error_string)
        self._finish("erreur du lecteur")

    # --- exécution des décisions -------------------------------------------

    def _start(self, name):
        self._starting = True
        self._finish_requested = False
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
        if status in (QMediaPlayer.MediaStatus.EndOfMedia,
                      QMediaPlayer.MediaStatus.InvalidMedia):
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


def build_audio_player(parent, volume):
    """Crée le lecteur, charge les sons embarqués et applique le volume.

    Les chemins passent par ``resources`` : les sons fonctionnent donc aussi dans
    un exécutable PyInstaller onefile.
    """
    player = AudioPlayer(parent)
    for name, relative_path in SOUNDS.items():
        player.add_sound(name, resource_path(relative_path))
    player.set_volume(volume)
    return player
