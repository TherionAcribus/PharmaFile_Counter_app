"""Configuration du poste : adresse du serveur, secret applicatif, comptoir.

Ces trois réglages décrivent la MACHINE, pas l'équipier : ils sont fixés une
fois à l'installation (le PC ne quitte pas son comptoir) et les modifier
reconnecte toute l'application et libère le comptoir occupé. Ils vivaient dans
la page « Connexion » des préférences, mélangés aux choix personnels
(reconnexion automatique, profil serveur) : ils ont désormais leur propre
fenêtre, ouverte au premier démarrage, depuis la page « Poste » des préférences
(après avertissement) ou après un échec de reconnexion.

Le secret n'est jamais réaffiché : le champ reste vide, un libellé indique
seulement qu'un secret est enregistré (et où). Laisser le champ vide conserve le
secret enregistré ; le saisir le remplace.
"""

import logging
import os
from datetime import datetime

import requests
from PySide6.QtCore import QSettings, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

import endpoints
import settings_schema
from connections import DEFAULT_TIMEOUT
from counter_id_utils import coerce_counter_id
from secret_store import (
    STORAGE_KEYRING, STORAGE_SETTINGS, load_secret, save_secret, secret_storage,
)
from url_validation import normalize_url, validate_server_url

logger = logging.getLogger("appcomptoir.station")


def dev_insecure_allowed():
    """Mode développement explicite : autorise http:// vers un serveur distant.
    Activé uniquement par la variable d'environnement APPCOMPTOIR_DEV_INSECURE
    (pas d'option d'interface, pour éviter un déblocage accidentel en production)."""
    return bool(os.environ.get("APPCOMPTOIR_DEV_INSECURE"))


def secret_status_text(storage):
    """Libellé de l'état du secret enregistré — jamais sa valeur."""
    if storage == STORAGE_KEYRING:
        return "Secret enregistré dans le gestionnaire d'identifiants du système."
    if storage == STORAGE_SETTINGS:
        return ("Secret enregistré EN CLAIR dans la configuration locale "
                "(gestionnaire d'identifiants indisponible).")
    return "Aucun secret enregistré."


def sync_and_verify(parent, settings):
    """ Force l'écriture QSettings (``sync``) et vérifie qu'elle a abouti
    (``status``). Renvoie True si la persistance a réussi ; sinon affiche une
    erreur et renvoie False (point 10 : ne jamais laisser croire à un
    enregistrement réussi quand l'écriture a échoué). """
    settings.sync()
    if settings.status() != QSettings.NoError:
        QMessageBox.critical(
            parent, "Échec d'enregistrement",
            "Les réglages n'ont pas pu être enregistrés sur ce poste "
            "(accès au stockage local refusé ou format invalide). Les "
            "modifications ne sont PAS garanties.\n\nVérifiez les droits "
            "d'accès à la configuration, puis réessayez.")
        return False
    return True


class TestConnectionWorker(QThread):
    connection_tested = Signal(bool, str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            response = requests.get(self.url, timeout=DEFAULT_TIMEOUT)
            if response.status_code == 200:
                self.connection_tested.emit(True, f"Connexion réussie à {current_time}")
            else:
                self.connection_tested.emit(False, f"Erreur de connexion: {response.status_code} à {current_time}")
        except requests.exceptions.RequestException as e:
            self.connection_tested.emit(False, f"Erreur: {e} à {current_time}")


class CountersWorker(QThread):
    """ Récupère la liste des comptoirs en arrière-plan pour ne pas geler la
    fenêtre pendant l'appel réseau.

    /api/counters est protégée côté serveur (require_app_token_or_login) : on
    récupère d'abord un token applicatif avec le secret saisi avant d'appeler
    la route. """
    result = Signal(bool, object)  # success, counters (list) ou message d'erreur (str)

    def __init__(self, web_url, app_secret):
        super().__init__()
        self.web_url = web_url
        self.app_secret = app_secret

    def run(self):
        try:
            token_response = requests.post(endpoints.app_token(self.web_url),
                                            data={'app_secret': self.app_secret},
                                            timeout=DEFAULT_TIMEOUT)
            if token_response.status_code != 200:
                self.result.emit(False, "Secret applicatif invalide : impossible de récupérer la liste des comptoirs")
                return
            # Réponse JSON invalide ou jeton manquant : on émet un échec explicite
            # au lieu de laisser une exception tuer silencieusement le thread (le
            # signal ne serait jamais émis et l'interface resterait figée).
            try:
                token = token_response.json().get("token")
            except ValueError:
                token = None
            if not token:
                self.result.emit(False, "Réponse du serveur invalide (jeton manquant).")
                return

            response = requests.get(endpoints.counters(self.web_url),
                                     headers={'X-App-Token': token}, timeout=DEFAULT_TIMEOUT)
            if response.status_code != 200:
                self.result.emit(False, f"Erreur de chargement des comptoirs: {response.status_code}")
                return
            try:
                counters = response.json()
            except ValueError:
                self.result.emit(False, "Réponse du serveur invalide (liste des comptoirs illisible).")
                return
            if not isinstance(counters, list):
                self.result.emit(False, "Réponse du serveur invalide (format de liste inattendu).")
                return
            self.result.emit(True, counters)
        except requests.exceptions.RequestException as e:
            self.result.emit(False, f"Erreur: {e}")


class TokenCheckWorker(QThread):
    """ Vérifie, en arrière-plan, qu'une URL + un secret permettent réellement
    d'obtenir un jeton applicatif (connexion « exploitable »). Utilisé avant
    d'enregistrer un changement de serveur/secret : on ne confirme l'enregistrement
    que si la nouvelle connexion fonctionne (point 8). """
    checked = Signal(bool, str)  # ok, message d'erreur (vide si ok)

    def __init__(self, web_url, app_secret):
        super().__init__()
        self.web_url = web_url
        self.app_secret = app_secret

    def run(self):
        try:
            resp = requests.post(endpoints.app_token(self.web_url),
                                 data={"app_secret": self.app_secret},
                                 timeout=DEFAULT_TIMEOUT)
        except requests.exceptions.RequestException as e:
            self.checked.emit(False, f"Serveur injoignable : {e}")
            return
        if resp.status_code == 200:
            try:
                token = resp.json().get("token")
            except ValueError:
                token = None
            if token:
                self.checked.emit(True, "")
            else:
                self.checked.emit(False, "Réponse du serveur invalide (jeton manquant).")
            return
        if resp.status_code in (401, 403):
            self.checked.emit(False, "Secret applicatif refusé par le serveur.")
            return
        self.checked.emit(False, f"Réponse inattendue du serveur (statut {resp.status_code}).")


# Délai maximal (ms) d'attente d'un worker de test à la fermeture du dialogue
# (point 9). Borné pour ne jamais figer l'interface : les appels réseau ont déjà
# leur propre timeout (DEFAULT_TIMEOUT), ce délai n'est qu'un garde-fou.
WORKER_SHUTDOWN_TIMEOUT_MS = 3000


class StationDialog(QDialog):
    """Fenêtre « Configuration du poste » (serveur, secret, comptoir).

    Accepted = configuration validée (connexion vérifiée si elle a changé,
    comptoir existant sur le serveur) ET persistée ; l'appelant applique alors
    les préférences (reconnexion des services si besoin)."""

    counters_loaded = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuration du poste")
        self.setMinimumWidth(460)

        # Registre des workers de test en cours (point 9), indexés par « kind »
        # (une entrée par type de test). On conserve une référence forte tant
        # que le thread tourne — sinon le ramasse-miettes peut détruire le
        # QThread en cours d'exécution (« QThread: Destroyed while thread is
        # still running »). Le kind sert aussi à interdire deux tests identiques
        # simultanés. _closing coupe tout nouveau lancement pendant la fermeture.
        self._workers = {}
        self._closing = False

        # Couple (URL, secret) dont provient la liste de comptoirs affichée
        # (None tant qu'aucun chargement n'a abouti). Permet de ne recharger la
        # liste que si nécessaire (_maybe_load_counters) et de détecter une
        # liste périmée à l'enregistrement (save_preferences).
        self._counters_loaded_for = None

        # Instantané des valeurs à enregistrer : rempli par save_preferences
        # APRÈS validation, lu par _finalize_save et les vérifications
        # asynchrones. Les champs restent éditables pendant les workers réseau
        # (le bouton Enregistrer est désactivé, pas les saisies) : sans ce
        # snapshot, un texte modifié entre le clic et la fin de la vérification
        # serait persisté sans avoir été vérifié.
        self._pending_save = None

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Réglages propres à ce poste, fixés à l'installation. Les modifier "
            "reconnecte l'application au serveur et libère le comptoir occupé.",
            self)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.url_label = QLabel("Adresse du serveur :", self)
        layout.addWidget(self.url_label)
        self.url_input = QLineEdit(self)
        # Aucune adresse par défaut n'est gravée dans le code : le texte indicatif
        # guide la saisie de l'adresse propre à l'officine.
        self.url_input.setPlaceholderText("https://mon-serveur.example.com")
        self.url_label.setBuddy(self.url_input)
        layout.addWidget(self.url_input)

        self.app_secret_label = QLabel(
            "Secret applicatif (fourni par l'administrateur du serveur) :", self)
        layout.addWidget(self.app_secret_label)

        # Champ TOUJOURS vide à l'ouverture : le secret enregistré n'est jamais
        # réaffiché. Saisie masquée, avec une case « Afficher » pour vérifier
        # une NOUVELLE saisie sans retaper à l'aveugle.
        self.app_secret_input = QLineEdit(self)
        self.app_secret_input.setEchoMode(QLineEdit.Password)
        self.app_secret_label.setBuddy(self.app_secret_input)
        self.app_secret_row = QHBoxLayout()
        self.app_secret_row.addWidget(self.app_secret_input)
        self.show_secret_checkbox = QCheckBox("Afficher la saisie", self)
        self.show_secret_checkbox.setToolTip(
            "Affiche en clair le secret en cours de saisie (jamais le secret "
            "déjà enregistré).")
        self.show_secret_checkbox.toggled.connect(
            lambda checked: self.app_secret_input.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password))
        self.app_secret_row.addWidget(self.show_secret_checkbox)
        layout.addLayout(self.app_secret_row)

        self.secret_status_label = QLabel("", self)
        self.secret_status_label.setWordWrap(True)
        layout.addWidget(self.secret_status_label)

        # URL ou secret retouché : la liste de comptoirs affichée devient
        # suspecte (elle vient peut-être de l'ancien couple) — on la marque
        # périmée au lieu de laisser des comptoirs d'un autre serveur.
        self.url_input.textChanged.connect(self._on_connection_fields_changed)
        self.app_secret_input.textChanged.connect(self._on_connection_fields_changed)

        # Le test exploite l'adresse ET le secret (chargement des comptoirs) :
        # le bouton est placé sous les deux champs qu'il consomme.
        self.test_button = QPushButton("Tester la connexion", self)
        self.test_button.clicked.connect(self.test_url)
        layout.addWidget(self.test_button)

        self.status_label = QLabel(self)
        # wordWrap plutôt qu'une largeur fixe : le texte long (message d'erreur)
        # passe à la ligne et la fenêtre reste redimensionnable. Texte
        # sélectionnable : un message d'erreur reste copiable.
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status_label)

        self.counter_label = QLabel("Comptoir de ce poste :", self)
        layout.addWidget(self.counter_label)
        self.counter_combobox = QComboBox(self)
        self.counter_label.setBuddy(self.counter_combobox)
        layout.addWidget(self.counter_combobox)

        layout.addStretch()

        self.button_box = QDialogButtonBox(self)
        self.save_button = self.button_box.addButton(
            "Enregistrer", QDialogButtonBox.AcceptRole)
        self.button_box.addButton("Annuler", QDialogButtonBox.RejectRole)
        # clicked(bool checked) transmettrait « False » : handler dédié.
        self.save_button.clicked.connect(lambda _checked=False: self.save_preferences())
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self.load_preferences()
        self.counters_loaded.connect(self.update_counters)
        # Liste des comptoirs chargée d'emblée si la connexion est renseignée.
        self._maybe_load_counters()

    # --- Registre des workers ------------------------------------------------

    def _start_worker(self, kind, worker):
        """ Démarre un worker de test en le suivant dans le registre (point 9).

        - Conserve une référence forte jusqu'à sa fin (empêche la destruction du
          QThread pendant qu'il tourne).
        - Empêche deux tests identiques simultanés : si un worker du même `kind`
          est déjà en cours, refuse le nouveau et renvoie False.
        - Refuse aussi tout lancement pendant la fermeture du dialogue.

        Renvoie True si le worker a été démarré, False sinon. """
        if self._closing:
            return False
        existing = self._workers.get(kind)
        if existing is not None and existing.isRunning():
            return False
        # Remplace un éventuel worker précédent (déjà terminé) : la référence est
        # gardée tant qu'il tourne, puis libérée au prochain lancement du même
        # kind ou à la fermeture (_shutdown_workers).
        self._workers[kind] = worker
        worker.start()
        return True

    def _shutdown_workers(self):
        """ Arrête proprement les workers de test encore actifs à la fermeture
        du dialogue (point 9) : on demande l'interruption puis on attend leur fin
        avec un délai borné. Sans cette attente, le QThread pourrait être détruit
        alors qu'il tourne encore (crash) lorsque le dialogue est libéré. Le délai
        est borné pour ne jamais figer l'interface indéfiniment. """
        self._closing = True
        for worker in list(self._workers.values()):
            if worker is None or not worker.isRunning():
                continue
            worker.requestInterruption()
            if not worker.wait(WORKER_SHUTDOWN_TIMEOUT_MS):
                logger.warning(
                    "Un worker de test est toujours actif après %d ms à la "
                    "fermeture de la configuration du poste ; il est abandonné.",
                    WORKER_SHUTDOWN_TIMEOUT_MS)
        self._workers.clear()

    def done(self, result):
        # Point de sortie unique du dialogue : accept() ET reject() (et donc la
        # croix de fermeture, qui passe par reject) y aboutissent. On y arrête les
        # workers de test avant que le dialogue ne soit refermé/libéré.
        self._shutdown_workers()
        super().done(result)

    # --- Chargement / secret -------------------------------------------------

    def load_preferences(self):
        settings = QSettings()
        settings_schema.migrate_settings(settings)
        # URL serveur : aucun défaut gravé (chaque officine renseigne la sienne) ;
        # champ vide = non configuré. Un texte indicatif guide la saisie.
        self.url_input.setText(settings_schema.read(settings, "web_url"))
        # Secret enregistré gardé en mémoire (tests de connexion, liste des
        # comptoirs) mais JAMAIS placé dans le champ.
        self._stored_secret = load_secret(settings)
        self._refresh_secret_status(secret_storage(settings))
        self.counter_id = coerce_counter_id(settings.value("counter_id", None))
        label = f"{self.counter_id} - Chargement en cours..." if self.counter_id else "Sélectionnez un comptoir..."
        self.counter_combobox.addItem(label, self.counter_id)

    def _refresh_secret_status(self, storage):
        """Libellé d'état + texte indicatif du champ selon qu'un secret est
        déjà enregistré ou non."""
        self.secret_status_label.setText(secret_status_text(storage))
        self.app_secret_input.setPlaceholderText(
            "Laisser vide pour conserver le secret enregistré"
            if self._stored_secret else "Secret fourni par l'administrateur")

    def _app_secret(self):
        """Secret EFFECTIF : la saisie (débarrassée des espaces de copier-coller
        en bordure) si le champ est rempli, sinon le secret enregistré.

        Toutes les lectures passent ici : sans strip, un espace en fin de
        collage faisait échouer le jeton avec un « secret refusé » trompeur."""
        typed = self.app_secret_input.text().strip()
        return typed or self._stored_secret

    def _normalize_secret_field(self):
        """Réécrit la saisie sans les espaces de bordure — uniquement si une
        saisie existe : le secret enregistré n'est jamais placé dans le champ."""
        typed = self.app_secret_input.text()
        if typed and typed != typed.strip():
            self.app_secret_input.setText(typed.strip())

    def _focus(self, widget):
        """Focalise le champ fautif signalé par un message d'erreur."""
        if widget is not None:
            widget.setFocus()

    # --- Enregistrement ------------------------------------------------------

    def save_preferences(self):
        """Valide puis enregistre la configuration du poste."""
        app_secret = self._app_secret()
        # counter_id normalisé en entier strictement positif (cohérent avec le
        # serveur et avec les comparaisons de l'app).
        counter_id = coerce_counter_id(self.counter_combobox.currentData())

        # Validation + normalisation de l'URL (point 8) : schéma http/https, hôte
        # présent, et http interdit pour un serveur distant (sauf mode dev). On
        # écrit la forme normalisée dans le champ (feedback + valeur enregistrée).
        ok, url, error = validate_server_url(
            self.url_input.text(), allow_insecure_remote=dev_insecure_allowed())
        if not ok:
            self._focus(self.url_input)
            QMessageBox.warning(self, "URL invalide", error)
            return
        self.url_input.setText(url)
        self._normalize_secret_field()
        if not app_secret:
            self._focus(self.app_secret_input)
            QMessageBox.warning(self, "Secret manquant",
                                "Saisissez le secret applicatif fourni par "
                                "l'administrateur du serveur.")
            return
        if counter_id is None:
            self._focus(self.counter_combobox)
            QMessageBox.warning(self, "Erreur", "Vous devez sélectionner un comptoir valide")
            return

        # Ne pas confirmer l'enregistrement tant que la nouvelle connexion n'est
        # pas exploitable (point 8) : si l'URL ou le secret changent, on vérifie
        # d'abord qu'ils permettent d'obtenir un jeton. Sinon (seul le comptoir a
        # changé), on enregistre directement.
        settings = QSettings()
        stored_url = settings_schema.read(settings, "web_url")
        connection_changed = (url != stored_url) or (app_secret != self._stored_secret)
        # La liste affichée n'est fiable que si elle provient du couple courant :
        # après un aller-retour sur les champs, elle peut montrer les comptoirs
        # d'un autre serveur alors que la connexion enregistrée est inchangée.
        list_is_stale = (self._counters_loaded_for is not None
                         and self._counters_loaded_for != (url, app_secret))

        # Instantané des valeurs validées. La suite est potentiellement
        # asynchrone (vérifications réseau en arrière-plan) et les champs
        # restent éditables entre-temps : _finalize_save écrira ce snapshot et
        # jamais les widgets.
        self._pending_save = {"settings": {"web_url": url, "counter_id": counter_id},
                              "app_secret": app_secret}

        if connection_changed:
            self._validate_connection_then_save()
        elif list_is_stale:
            self._validate_counter_then_save()
        else:
            self._finalize_save()

    def _pending_pair(self):
        """Couple (URL, secret) du snapshot en attente d'enregistrement — ce qui
        a été validé (et vérifié, le cas échéant), indépendamment du texte actuel
        des champs, restés éditables pendant les workers réseau."""
        pending = self._pending_save or {}
        return (pending.get("settings", {}).get("web_url", ""),
                pending.get("app_secret", ""))

    def _validate_connection_then_save(self):
        """ Vérifie en arrière-plan que le couple (URL, secret) du snapshot
        donne un jeton, PUIS enregistre seulement en cas de succès. Le bouton
        Enregistrer est désactivé pendant la vérification pour éviter les
        doubles soumissions. """
        url, app_secret = self._pending_pair()
        worker = TokenCheckWorker(url, app_secret)
        worker.checked.connect(self._on_connection_checked)
        # Suivi + anti-doublon via le registre (point 9) : si une vérification est
        # déjà en cours, on ne relance rien (le bouton est déjà désactivé).
        if not self._start_worker("token_check", worker):
            return
        self.save_button.setEnabled(False)
        self.status_label.setText("Vérification de la connexion au serveur…")

    @Slot(bool, str)
    def _on_connection_checked(self, ok, message):
        if not ok:
            self.save_button.setEnabled(True)
            # « Enregistré » n'est PAS affiché : le dialogue reste ouvert et rien
            # n'est persisté tant que la connexion n'est pas exploitable.
            self.status_label.setText("Non enregistré : " + message)
            self._focus(self.url_input)
            QMessageBox.warning(
                self, "Connexion impossible",
                message + "\n\nLa configuration n'a PAS été enregistrée. "
                "Vérifiez l'adresse du serveur et le secret applicatif.")
            return
        # Le couple VÉRIFIÉ est celui du snapshot, pas celui des champs (qui ont
        # pu être modifiés pendant le worker réseau).
        pair = self._pending_pair()
        if self._counters_loaded_for == pair:
            # La liste affichée provient déjà de la nouvelle connexion : le
            # comptoir choisi existe forcément sur ce serveur.
            self.save_button.setEnabled(True)
            self.status_label.setText("Connexion vérifiée — enregistrement…")
            self._finalize_save()
        else:
            # Connexion OK mais liste possiblement périmée (celle de l'ancien
            # serveur, ou simple entrée d'attente) : on vérifie que le comptoir
            # choisi existe bien sur CE serveur avant d'enregistrer.
            self._validate_counter_then_save()

    def _validate_counter_then_save(self):
        """ Vérifie en arrière-plan que le comptoir sélectionné existe sur le
        serveur (URL, secret) courant, PUIS enregistre seulement dans ce cas.

        Sans cette étape, un counter_id issu de la liste d'un ANCIEN serveur
        (connexion modifiée sans retest, ou aller-retour sur les champs) était
        persisté contre le nouveau serveur : le comptoir enregistré aurait pu
        ne pas y exister. """
        url, app_secret = self._pending_pair()
        if not self._start_counters_worker(
                "counters_save", url, app_secret, self._on_save_counters_result):
            self.save_button.setEnabled(True)
            return
        self.save_button.setEnabled(False)
        self.status_label.setText("Vérification du comptoir sur le serveur…")

    def _on_save_counters_result(self, success, data, pair):
        self.save_button.setEnabled(True)
        if not success:
            # La liste est illisible : impossible de garantir le comptoir
            # choisi — on n'enregistre rien plutôt que de persister un id
            # incertain.
            self.status_label.setText("Non enregistré : " + data)
            QMessageBox.warning(
                self, "Comptoirs introuvables",
                data + "\n\nImpossible de vérifier le comptoir sélectionné : "
                "la configuration n'a PAS été enregistrée.")
            return
        # La liste récupérée devient la liste affichée (combo rechargé).
        self._counters_loaded_for = pair
        # Le comptoir à vérifier est celui du snapshot (choisi au clic
        # « Enregistrer »), pas la sélection courante du combo — modifiable
        # pendant le worker.
        selected = (self._pending_save or {}).get("settings", {}).get("counter_id")
        self.counters_loaded.emit(data)
        ids = {coerce_counter_id(counter["id"]) for counter in data
               if isinstance(counter, dict) and "id" in counter}
        if selected is None or selected not in ids:
            # Le comptoir choisi (liste périmée) n'existe pas sur ce serveur :
            # l'utilisateur repart d'une liste à jour pour en choisir un valide.
            self.status_label.setText(
                "Non enregistré : le comptoir choisi n'existe pas sur ce serveur")
            self._focus(self.counter_combobox)
            QMessageBox.warning(
                self, "Comptoir introuvable",
                "Le comptoir sélectionné n'existe pas sur ce serveur. La liste "
                "des comptoirs a été rechargée : choisissez un comptoir, puis "
                "enregistrez à nouveau.\n\nLa configuration n'a PAS été "
                "enregistrée.")
            return
        # update_counters a pu resélectionner le comptoir ENREGISTRÉ : on remet
        # le choix de l'utilisateur (il existe sur ce serveur) et on enregistre.
        index = self.counter_combobox.findData(selected)
        if index != -1:
            self.counter_combobox.setCurrentIndex(index)
        self.status_label.setText("Connexion vérifiée — enregistrement…")
        self._finalize_save()

    def _sync_and_verify(self, settings):
        return sync_and_verify(self, settings)

    def _finalize_save(self):
        """ Persiste le snapshot validé par save_preferences (URL normalisée et,
        si la connexion changeait, vérifiée) puis ferme le dialogue (Accepted).

        On n'écrit QUE le snapshot : les vérifications réseau sont asynchrones
        et les champs sont restés éditables entre-temps.

        Ordre voulu (point 10) pour éviter une configuration à moitié enregistrée
        entre QSettings et le magasin de secrets : on écrit d'abord l'adresse et
        le comptoir, on force et on vérifie leur persistance ; seulement alors on
        enregistre le secret (s'il a changé), puis on re-synchronise et
        re-vérifie. Toute défaillance affiche une erreur et laisse le dialogue
        ouvert (pas d'``accept``). """
        pending = self._pending_save
        if pending is None:
            # Ne devrait pas arriver (appelé uniquement depuis save_preferences,
            # qui pose le snapshot avant tout enchaînement asynchrone). On refuse
            # de relire les champs : ce serait persister un état non validé.
            logger.error("_finalize_save appelé sans snapshot de configuration.")
            return
        app_secret = pending["app_secret"]
        settings = QSettings()
        for key, value in pending["settings"].items():
            settings.setValue(key, value)
        if not self._sync_and_verify(settings):
            return

        if app_secret != self._stored_secret:
            # Le secret est stocké dans le magasin sécurisé (keyring), pas en
            # clair dans QSettings. Si le magasin est indisponible, save_secret
            # retombe sur un stockage en clair mais le signale (False) : on ne
            # l'accepte PAS silencieusement, on prévient explicitement.
            fell_back_to_cleartext = not save_secret(settings, app_secret)
            if not self._sync_and_verify(settings):
                return
            self._stored_secret = app_secret
            if fell_back_to_cleartext:
                QMessageBox.warning(
                    self, "Stockage non sécurisé du secret",
                    "Le gestionnaire de secrets du système est indisponible sur ce "
                    "poste. Le secret d'application a été enregistré en clair dans la "
                    "configuration locale.\n\nInstallez/activez un magasin de secrets "
                    "(Gestionnaire d'identifiants Windows, Trousseau, Secret Service) "
                    "pour un stockage sécurisé.")

        self._pending_save = None
        self.accept()

    # --- Test de connexion et liste des comptoirs ----------------------------

    def _on_connection_fields_changed(self, *_args):
        """URL ou secret retouché : si la liste affichée provient d'un couple
        différent, on la remplace par une entrée d'attente plutôt que de laisser
        les comptoirs d'un autre serveur. Le rechargement reste à la charge de
        « Tester la connexion » ; l'entrée conserve le comptoir enregistré en
        donnée, comme à l'ouverture du dialogue."""
        if self._counters_loaded_for is None:
            return
        pair = (normalize_url(self.url_input.text()), self._app_secret())
        if pair == self._counters_loaded_for:
            return
        self._counters_loaded_for = None
        self.counter_combobox.clear()
        label = (f"{self.counter_id} - liste périmée, relancez le test"
                 if self.counter_id else "Liste périmée — relancez le test")
        self.counter_combobox.addItem(label, self.counter_id)

    def test_url(self):
        # Même validation qu'à l'enregistrement (point 8) : on ne lance pas de
        # test sur une adresse que save_preferences refuserait. L'URL normalisée
        # est réécrite dans le champ — comme à l'enregistrement — et c'est elle
        # qui est testée.
        ok, url, error = validate_server_url(
            self.url_input.text(), allow_insecure_remote=dev_insecure_allowed())
        if not ok:
            QMessageBox.warning(self, "URL invalide", error)
            self.status_label.setText(error)
            return
        self.url_input.setText(url)
        if not self._app_secret():
            QMessageBox.warning(self, "Erreur", "Le secret applicatif ne peut pas être vide")
            self.status_label.setText("Le secret applicatif ne peut pas être vide")
            return
        self._normalize_secret_field()

        worker = TestConnectionWorker(url)
        worker.connection_tested.connect(self.on_connection_tested)
        # Anti-doublon (point 9) : un seul test de connexion à la fois. On ne
        # désactive le bouton et n'affiche « en cours » qu'après un démarrage
        # effectif. Le bouton reste désactivé jusqu'à la fin du chargement des
        # comptoirs qui suit un test réussi (réactivé dans _on_counters_result).
        if not self._start_worker("test_connection", worker):
            return
        self.status_label.setText("Test de connexion en cours...")
        self.test_button.setEnabled(False)

    @Slot(bool, str)
    def on_connection_tested(self, success, message):
        self.status_label.setText(message)
        if success:
            self.load_counters()  # le bouton est réactivé à la fin du chargement
        else:
            self.test_button.setEnabled(True)

    def _maybe_load_counters(self):
        """ Charge la liste des comptoirs à l'ouverture si possible.

        - URL ou secret manquant : rien à charger, le libellé l'indique au
          lieu de promettre un chargement qui ne viendra jamais.
        - Liste déjà à jour pour ce couple (URL, secret) : pas d'appel réseau.
        - Sinon : chargement lancé (doublons déjà exclus par _start_worker). """
        url = normalize_url(self.url_input.text())
        secret = self._app_secret()
        if not url or not secret:
            self._set_counter_placeholder("complétez l'adresse et le secret")
            return
        if self._counters_loaded_for == (url, secret):
            return
        self.load_counters()

    def _set_counter_placeholder(self, suffix):
        """ Met à jour le libellé de l'entrée d'attente du combo « comptoir ».

        N'agit que tant qu'aucune vraie liste n'a été chargée : le combo ne
        contient alors que l'entrée initiale, dont la donnée (counter_id
        enregistré) est préservée — seul le texte affiché change. """
        if self._counters_loaded_for is not None:
            return
        label = f"{self.counter_id} - {suffix}" if self.counter_id else suffix
        self.counter_combobox.setItemText(0, label)

    def _start_counters_worker(self, kind, url, secret, handler):
        """ Démarre un CountersWorker pour le couple (url, secret) donné.

        Le couple de la REQUÊTE est rattaché au résultat via le lambda :
        `handler` reçoit (success, data, pair) — le pair reste correct même si
        les champs ont été modifiés pendant l'appel ou si un autre chargement
        (d'un kind différent) tourne en parallèle.
        Renvoie True si le worker a démarré. """
        worker = CountersWorker(url, secret)
        worker.result.connect(
            lambda ok, data, pair=(url, secret): handler(ok, data, pair))
        return self._start_worker(kind, worker)

    def load_counters(self):
        # Si un chargement des comptoirs est déjà en cours (ou fermeture), on
        # réactive le bouton pour ne pas le laisser bloqué désactivé.
        if not self._start_counters_worker(
                "counters", normalize_url(self.url_input.text()),
                self._app_secret(), self._on_counters_result):
            self.test_button.setEnabled(True)

    def _on_counters_result(self, success, data, pair):
        self.test_button.setEnabled(True)
        current = (normalize_url(self.url_input.text()), self._app_secret())
        if pair != current:
            # Réponse d'un chargement lancé avec d'anciennes valeurs : elle
            # concerne un autre couple (URL, secret) — on l'ignore plutôt que
            # d'afficher la liste d'un serveur périmé.
            logger.info("Liste de comptoirs ignorée : chargée pour %s, "
                        "champs désormais sur %s.", pair[0], current[0])
            return
        if success:
            # La liste affichée provient désormais du couple de la requête.
            self._counters_loaded_for = pair
            self.counters_loaded.emit(data)
        else:
            self.status_label.setText(data)
            self._set_counter_placeholder("échec du chargement")

    @Slot(list)
    def update_counters(self, counters):
        # Comptoir à resélectionner : d'abord la sélection COURANTE du combo —
        # l'utilisateur a pu choisir un autre comptoir sans encore enregistrer,
        # un rechargement de la liste ne doit pas reprendre son choix. À défaut
        # (entrée d'attente sans donnée), le comptoir enregistré.
        wanted = coerce_counter_id(self.counter_combobox.currentData())
        if wanted is None:
            wanted = self.counter_id
        self.counter_combobox.clear()
        for counter in counters:
            # Champs manquants ou identifiant invalide : on ignore l'entrée
            # plutôt que d'interrompre la mise à jour de la liste. Les ids sont
            # normalisés en entier (coerce_counter_id) pour que findData compare
            # des types identiques — un id « 3 » (chaîne) ne correspondrait
            # jamais à int(3).
            try:
                name, cid = counter['name'], coerce_counter_id(counter['id'])
            except (KeyError, TypeError):
                logger.warning("Comptoir ignoré (champ manquant) : %r", counter)
                continue
            if cid is None:
                logger.warning("Comptoir ignoré (identifiant invalide) : %r", counter)
                continue
            self.counter_combobox.addItem(name, cid)

        index = self.counter_combobox.findData(wanted) if wanted else -1
        if index != -1:
            self.counter_combobox.setCurrentIndex(index)
        else:
            # Le comptoir voulu n'existe pas (ou plus) sur ce serveur : entrée
            # d'attente SANS donnée plutôt que présélectionner le premier
            # comptoir — sinon un simple « Enregistrer » changerait de comptoir
            # silencieusement. L'enregistrement exigera un choix explicite.
            label = (f"{wanted} - comptoir introuvable sur ce serveur"
                     if wanted else "Sélectionnez un comptoir...")
            self.counter_combobox.insertItem(0, label, None)
            self.counter_combobox.setCurrentIndex(0)
