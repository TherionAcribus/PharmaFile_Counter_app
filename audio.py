"""Sons de l'application (point 10.11).

Trois sons ponctuent l'usage du comptoir : « patient déjà pris » (le patient
appelé a été récupéré par un autre comptoir), « ding » (nouveau patient) et
« pensez à valider ». Ils sont chargés une fois au démarrage puis rejoués à la
demande.

Isolé de ``main.py`` : la fenêtre demande un son par son NOM, elle n'a plus à
connaître QMediaPlayer, les chemins de fichiers ni la conversion du volume.
"""

import logging

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from resources import resource_path

logger = logging.getLogger("appcomptoir.audio")

#: Sons embarqués : nom logique -> fichier dans les ressources.
SOUNDS = {
    "patient_taken": "assets/sounds/already_taken.mp3",
    "ding": "assets/sounds/ding.mp3",
    "please_validate": "assets/sounds/please_validate.mp3",
}


class AudioPlayer(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.sounds = {}

        # Ajout des callbacks
        self.player.errorOccurred.connect(self.handle_error)

    def add_sound(self, name, file_path):
        self.sounds[name] = QUrl.fromLocalFile(file_path)

    def play_sound(self, name):
        if name in self.sounds:
            self.player.setSource(self.sounds[name])
            self.player.play()
        else:
            logger.warning("Son inconnu : %s", name)

    def set_volume(self, volume):
        """Volume attendu en pourcentage (0-100) ; Qt le veut entre 0 et 1."""
        self.audio_output.setVolume(volume / 100)

    def handle_error(self, error, error_string):
        logger.error("Erreur du lecteur audio (%s) : %s", error, error_string)


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
