"""Configuration centralisée de la journalisation de l'App comptoir.

Objectifs (remplace les ``print()`` disséminés) :
- niveaux DEBUG/INFO/WARNING/ERROR, DEBUG désactivé en production ;
- fichier tournant dans un dossier utilisateur (%LOCALAPPDATA%\\PharmaFile\\logs
  sous Windows) ;
- masquage (redaction) des jetons, secrets, mots de passe, initiales et données
  patient, en défense en profondeur ;
- handler optionnel vers la fenêtre de log de l'UI.

Le logger racine est configuré une seule fois ; tous les modules récupèrent un
logger enfant :

    import logging
    logger = logging.getLogger("appcomptoir.buttons")

et héritent ainsi du fichier, de la console et du masquage. La classe
``AppLogger`` (singleton) est conservée pour compatibilité : elle déclenche la
configuration, expose le logger applicatif et gère le handler UI.
"""

import logging
import os
import platform
import re
import sys
import threading
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

APP_NAME = "PharmaFile"
LOG_FILENAME = "application.log"
APP_LOGGER_NAME = "appcomptoir"

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Longueur minimale d'une valeur enregistrée pour être masquée telle quelle
# (évite de masquer des fragments courts et fréquents).
_MIN_SECRET_LEN = 4


def default_log_dir() -> Path:
    """Répertoire de logs dans l'espace utilisateur.

    Windows : %LOCALAPPDATA%\\PharmaFile\\logs. Linux/macOS : repli sous le home.
    """
    system = platform.system()
    if system == "Windows":
        base = os.path.join(os.environ.get("LOCALAPPDATA", str(Path.home())), APP_NAME)
    elif system == "Linux":
        base = os.path.join(str(Path.home()), ".config", APP_NAME)
    else:
        base = os.path.join(str(Path.home()), f".{APP_NAME}")
    return Path(base) / "logs"


class RedactingFilter(logging.Filter):
    """Masque jetons/secrets/mots de passe/initiales et données patient.

    Deux niveaux :
    - valeurs exactes enregistrées à l'exécution (``register_secret``) : jeton
      applicatif et secret (à réenregistrer à chaque renouvellement de jeton) ;
    - motifs génériques ``champ: valeur`` pour les champs sensibles, y compris
      quand un dict/JSON patient est logué par mégarde.
    """

    _MASK = "***"

    # Champs sensibles à masquer dans un texte type JSON/dict/clé=valeur.
    _SENSITIVE_FIELDS = [
        "x-app-token", "app_token", "app-token", "token",
        "app_secret", "app-secret", "secret", "password",
        "initials", "call_number", "firstname", "lastname",
        "name", "patient_name", "phone", "phone_number", "birthdate",
    ]

    def __init__(self):
        super().__init__()
        self._secrets = set()
        self._lock = threading.Lock()
        # Motifs pour "champ": "valeur"  /  champ=valeur  /  champ: valeur
        fields = "|".join(re.escape(f) for f in self._SENSITIVE_FIELDS)
        self._field_pattern = re.compile(
            r"(['\"]?(?:" + fields + r")['\"]?\s*[:=]\s*)"
            r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}\)]+)",
            re.IGNORECASE,
        )
        self._bearer_pattern = re.compile(r"(bearer\s+)\S+", re.IGNORECASE)

    def register_secret(self, value):
        """Enregistre une valeur exacte (jeton/secret) à masquer partout."""
        if isinstance(value, str) and len(value) >= _MIN_SECRET_LEN:
            with self._lock:
                self._secrets.add(value)

    def _redact(self, text):
        with self._lock:
            secrets = list(self._secrets)
        # Valeurs exactes connues d'abord (les plus longues en premier).
        for secret in sorted(secrets, key=len, reverse=True):
            if secret and secret in text:
                text = text.replace(secret, self._MASK)
        text = self._field_pattern.sub(lambda m: m.group(1) + self._MASK, text)
        text = self._bearer_pattern.sub(lambda m: m.group(1) + self._MASK, text)
        return text

    def filter(self, record):
        try:
            message = record.getMessage()
            redacted = self._redact(message)
            if redacted != message:
                record.msg = redacted
                record.args = ()
        except Exception:
            # La journalisation ne doit jamais casser le flux applicatif.
            pass
        return True


# Filtre de masquage partagé (permet register_secret depuis l'extérieur).
_redacting_filter = RedactingFilter()


def register_secret(value):
    """Enregistre une valeur sensible (jeton, secret) à masquer dans TOUS les
    logs. À rappeler à chaque renouvellement de jeton."""
    _redacting_filter.register_secret(value)


class LogHandler(logging.Handler):
    """Handler qui pousse chaque ligne formatée vers un callback (fenêtre UI)."""

    def __init__(self, update_callback):
        super().__init__()
        self.update_callback = update_callback

    def emit(self, record):
        try:
            log_entry = self.format(record)
            self.update_callback(log_entry)
        except Exception:
            self.handleError(record)


class AppLogger:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        if AppLogger._instance is not None:
            raise Exception("Cette classe est un singleton!")
        self.logger = None
        self._formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
        self.setup_logger()

    def setup_logger(self, debug=False):
        """Configure la journalisation du processus (idempotent).

        Le niveau par défaut est INFO (production) ; DEBUG uniquement si demandé
        (préférence « garder la fenêtre de log »). Si l'écriture du fichier
        échoue, on continue avec la console : la journalisation ne doit pas
        empêcher l'app de démarrer."""
        level = logging.DEBUG if debug else logging.INFO

        root = logging.getLogger()
        root.setLevel(logging.DEBUG)  # les handlers filtrent le niveau effectif

        # Le logger applicatif exposé par get_logger() ; les modules utilisent
        # des enfants "appcomptoir.<module>" qui propagent jusqu'ici.
        self.logger = logging.getLogger(APP_LOGGER_NAME)

        # Idempotence : ne configure qu'une fois les handlers racine.
        if getattr(root, "_appcomptoir_configured", False):
            self.set_level(level)
            return

        handlers = []

        # Console (utile en développement / fenêtre console). En application
        # packagée fenêtrée (PyInstaller), sys.stderr peut être None : on
        # n'ajoute alors pas de handler console pour éviter une erreur d'émission.
        if sys.stderr is not None:
            handlers.append(logging.StreamHandler())

        # Fichier tournant dans le dossier utilisateur.
        try:
            log_directory = default_log_dir()
            log_directory.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(log_directory, 0o700)
            except OSError:
                pass
            file_handler = TimedRotatingFileHandler(
                filename=str(log_directory / LOG_FILENAME),
                when="midnight",
                interval=1,
                backupCount=7,
                encoding="utf-8",
            )
            handlers.append(file_handler)
        except Exception as e:  # pragma: no cover - dépend de l'environnement
            logging.getLogger(APP_LOGGER_NAME).warning(
                "Journalisation fichier indisponible (%s), sortie console seule.", e)

        for handler in handlers:
            handler.setLevel(level)
            handler.setFormatter(self._formatter)
            handler.addFilter(_redacting_filter)
            root.addHandler(handler)

        root._appcomptoir_configured = True
        self.set_level(level)

    def set_level(self, level):
        """Ajuste le niveau effectif de tous les handlers racine."""
        for handler in logging.getLogger().handlers:
            handler.setLevel(level)

    def enable_debug(self, enabled=True):
        self.set_level(logging.DEBUG if enabled else logging.INFO)

    def add_ui_handler(self, callback):
        """Ajoute un handler vers la fenêtre de log de l'UI (avec masquage)."""
        ui_handler = LogHandler(callback)
        ui_handler.setFormatter(self._formatter)
        ui_handler.addFilter(_redacting_filter)
        self.logger.addHandler(ui_handler)
        return ui_handler

    def get_logger(self):
        return self.logger

    def cleanup(self):
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)
