import os
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime

class AppLogger:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        if AppLogger._instance is not None:
            raise Exception("Cette classe est un singleton!")
            
        self.logger = None
        self.setup_logger()
    
    def setup_logger(self):
        """Configure le système de logging"""
        # Créer le dossier logs s'il n'existe pas
        log_dir = 'logs'
        log_file = os.path.join(log_dir, 'application.log')
        os.makedirs(log_dir, exist_ok=True)

        # Configurer le logger principal
        self.logger = logging.getLogger("AppLogger")
        self.logger.setLevel(logging.DEBUG)
        
        # Supprimer tous les handlers existants pour éviter les doublons
        self.logger.handlers.clear()

        # Handler pour la rotation des fichiers
        file_handler = TimedRotatingFileHandler(
            filename=log_file,
            when='midnight',
            interval=1,
            backupCount=7,
            encoding='utf-8'
        )
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(file_handler)

        # Nettoyer les vieux logs au démarrage
        self._cleanup_old_logs(log_dir)

    def _cleanup_old_logs(self, log_dir, max_days=7):
        """Supprime les fichiers de log plus vieux que max_days jours"""
        current_time = datetime.now()
        for filename in os.listdir(log_dir):
            if filename.endswith('.log') and filename != 'application.log':
                file_path = os.path.join(log_dir, filename)
                file_time = datetime.fromtimestamp(os.path.getctime(file_path))
                if (current_time - file_time).days > max_days:
                    try:
                        os.remove(file_path)
                        print(f"Suppression de l'ancien fichier de log : {filename}")
                    except Exception as e:
                        print(f"Erreur lors de la suppression de {filename}: {e}")

    def add_ui_handler(self, callback):
        """Ajoute un handler pour l'interface utilisateur"""
        ui_handler = LogHandler(callback)
        ui_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(ui_handler)
        return ui_handler

    def get_logger(self):
        """Retourne l'instance du logger"""
        return self.logger

    def cleanup(self):
        """Nettoie les handlers"""
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)

class LogHandler(logging.Handler):
    def __init__(self, update_callback):
        super().__init__()
        self.update_callback = update_callback

    def emit(self, record):
        log_entry = self.format(record)
        self.update_callback(log_entry)
