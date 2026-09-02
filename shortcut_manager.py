"""Installation et déclenchement des raccourcis clavier (point 10.10).

Douze méthodes de ``MainWindow`` étaient consacrées aux raccourcis : verrou
d'installation, thread d'enregistrement, hooks système ``keyboard``, QShortcut du
mode « premier plan », signaux, confirmation des actions sensibles, retour visuel
et avertissement en cas de refus de Windows. Tout cela vit ici.

La logique PURE (modes, normalisation, doublons, traduction vers ``keyboard`` et
``QKeySequence``) reste dans ``shortcut_config`` ; ce module en est l'intégration
Qt/système. La fenêtre, elle, ne garde que les préférences (lues à la volée) et
les actions à exécuter.

Invariants conservés du point 27 :

* UN SEUL mécanisme actif à la fois (hooks globaux OU QShortcut), donc jamais de
  double déclenchement ;
* désenregistrement systématique AVANT toute réinstallation (sinon les hooks
  s'empilent et une pression déclenche l'action plusieurs fois) ;
* installation SÉRIALISÉE : la bibliothèque ``keyboard`` n'est pas thread-safe,
  le thread d'enregistrement précédent est attendu avant d'y toucher ;
* les callbacks ``keyboard`` s'exécutent hors du thread graphique : ils se
  contentent d'émettre un signal, jamais de manipuler un widget.
"""

import logging
import threading

import keyboard
from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QMessageBox

from shortcut_config import (
    ACTION_LABELS, ACTIONS, DEFAULT_MODE, MODE_DISABLED, MODE_FOCUSED,
    SENSITIVE_ACTIONS, to_keyboard_hotkey, to_qt_key_sequence,
)

# Attribut de la fenêtre portant le texte de chaque raccourci.
_SHORTCUT_ATTRIBUTES = {
    "next": "next_patient_shortcut",
    "validate": "validate_patient_shortcut",
    "pause": "pause_shortcut",
    "recall": "recall_shortcut",
    "deconnect": "deconnect_shortcut",
}


class ShortcutManager(QObject):
    """Raccourcis clavier de la fenêtre principale.

    ``window`` fournit les préférences (textes des raccourcis, mode, options) et
    les actions à exécuter ; le manager possède tout le reste (hooks, threads,
    QShortcut, signaux).
    """

    # Émis avec le nom de l'action. Les hooks `keyboard` (mode global) émettent
    # depuis leur propre thread — émission thread-safe ; les QShortcut (mode
    # premier plan) émettent depuis le thread GUI. L'unique slot _dispatch,
    # connecté en QueuedConnection, exécute l'action dans le thread graphique.
    triggered = Signal(str)
    # Émis (depuis le thread d'enregistrement) si Windows/keyboard refuse un ou
    # plusieurs raccourcis globaux, pour en avertir l'utilisateur côté GUI.
    registration_failed = Signal(object)

    def __init__(self, window, logger=None):
        super().__init__(window)
        self.window = window
        self.logger = logger or logging.getLogger("appcomptoir.shortcuts")
        # Sérialisation de l'(ré)installation (point 7) : le verrou garantit
        # qu'une installation ne chevauche jamais une autre, et _thread référence
        # l'éventuel enregistrement global en cours (joint avant réinstallation).
        self._lock = threading.Lock()
        self._thread = None
        self._qshortcuts = []
        self._connect_signals()

    # --- signaux ------------------------------------------------------------

    def _connect_signals(self):
        """ Connecte (UNE seule fois, à la construction) le signal de raccourci à
        son unique slot de traitement et le signal d'échec à l'avertissement. La
        QueuedConnection garantit que le traitement (manipulation de widgets)
        s'exécute dans le thread graphique, jamais dans le thread keyboard. """
        self.triggered.connect(self._dispatch, Qt.QueuedConnection)
        self.registration_failed.connect(self._warn_failures, Qt.QueuedConnection)

    # --- lecture des préférences -------------------------------------------

    def items(self):
        """ Couples (action, texte du raccourci) dans un ordre stable. """
        return [(action, getattr(self.window, _SHORTCUT_ATTRIBUTES[action], ""))
                for action in ACTIONS]

    # --- installation -------------------------------------------------------

    def install(self):
        """ Retire tous les raccourcis existants (hooks keyboard + QShortcut) puis
        installe le mécanisme correspondant au mode : aucun (désactivés), QShortcut
        actifs au premier plan, ou hooks globaux. Un SEUL mécanisme est actif à la
        fois -> pas de double déclenchement.

        SÉRIALISÉ (point 7) : un verrou empêche deux installations de se chevaucher,
        et on attend la fin de l'éventuel thread d'enregistrement global précédent
        AVANT de retirer/réinstaller — sinon unhook_all_hotkeys et add_hotkey
        pourraient s'exécuter en concurrence (la bibliothèque keyboard n'est pas
        thread-safe), laissant des hooks orphelins ou dupliqués. """
        # Verrou créé paresseusement : robustesse si l'objet n'est pas passé par
        # __init__ (faux self de tests).
        lock = getattr(self, "_lock", None)
        if lock is None:
            lock = self._lock = threading.Lock()
        with lock:
            prev = getattr(self, "_thread", None)
            if prev is not None and prev.is_alive():
                # Attente bornée : l'enregistrement global précédent doit être
                # terminé avant de toucher aux hooks.
                prev.join(timeout=2.0)
            self._thread = None
            self.remove_all()
            mode = getattr(self.window, "shortcut_mode", DEFAULT_MODE)
            if mode == MODE_DISABLED:
                self.logger.info("Raccourcis clavier désactivés.")
                return
            if mode == MODE_FOCUSED:
                self._install_focused()
            else:
                self._install_global()

    def remove_all(self):
        """ Désinstalle hooks keyboard globaux ET QShortcut premier plan. Retirer
        les anciens avant d'ajouter évite l'empilement de hooks (une pression
        déclenchait l'action autant de fois que de hooks accumulés). """
        try:
            keyboard.unhook_all_hotkeys()
        except Exception as e:
            self.logger.debug("unhook_all_hotkeys : %s", e)
        for sc in getattr(self, "_qshortcuts", []):
            try:
                sc.setEnabled(False)
                sc.deleteLater()
            except RuntimeError:
                pass
        self._qshortcuts = []

    def _install_focused(self):
        """ Mode « premier plan » : QShortcut avec contexte ApplicationShortcut ->
        n'agissent que lorsqu'une fenêtre de PharmaFile est active. Aucun hook
        système : pas de conflit avec le progiciel quand l'utilisateur travaille
        ailleurs. """
        self._qshortcuts = []
        for action, text in self.items():
            seq = to_qt_key_sequence(text)
            if not seq:
                continue
            shortcut = QShortcut(QKeySequence(seq), self.window)
            shortcut.setContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(lambda a=action: self.triggered.emit(a))
            self._qshortcuts.append(shortcut)
        self.logger.info("Raccourcis actifs au premier plan (%s installés).",
                         len(self._qshortcuts))

    def _install_global(self):
        """ Mode « global » : hooks système via la bibliothèque keyboard. Comme
        l'installation peut être un peu lente, elle se fait en arrière-plan ; les
        échecs (touche invalide, refus de Windows) sont collectés et signalés au
        thread GUI. """
        self._thread = threading.Thread(target=self._register_global_hotkeys, daemon=True)
        self._thread.start()

    def _register_global_hotkeys(self):
        """ Enregistre chaque raccourci global (hors thread GUI). Chaque callback
        ne fait QU'ÉMETTRE le signal (thread-safe) ; aucune manipulation de widget
        ici. Les échecs sont remontés via registration_failed. """
        failures = []
        installed = 0
        for action, text in self.items():
            hotkey = to_keyboard_hotkey(text)
            if not hotkey:
                continue
            try:
                keyboard.add_hotkey(hotkey, self._emit, args=(action,))
                installed += 1
            except Exception as e:
                # Touche inconnue ou refus de l'OS : on n'interrompt pas les
                # autres, on collecte pour avertir l'utilisateur.
                self.logger.warning("Raccourci '%s' (%s) refusé : %s", action, text, e)
                failures.append((ACTION_LABELS.get(action, action), text, str(e)))
        self.logger.info("Raccourcis globaux installés (%s).", installed)
        if failures:
            self.registration_failed.emit(failures)

    def _emit(self, action):
        """ Callback keyboard (hors thread GUI) : émission thread-safe uniquement. """
        self.triggered.emit(action)

    def shutdown(self):
        """ Fermeture de l'application : plus aucune action déclenchable au
        clavier. On attend d'abord (de façon bornée) la fin d'un enregistrement
        global en cours, sinon il rajouterait des hooks APRÈS le retrait
        (point 7) et ceux-ci survivraient à la fenêtre. """
        prev = getattr(self, "_thread", None)
        if prev is not None and prev.is_alive():
            prev.join(timeout=2.0)
        self._thread = None
        try:
            keyboard.unhook_all_hotkeys()
        except Exception as e:
            self.logger.debug("unhook_all_hotkeys à l'arrêt : %s", e)

    # --- déclenchement ------------------------------------------------------

    @Slot(str)
    def _dispatch(self, action):
        """ Point d'entrée unique de toute action déclenchée par raccourci (mode
        global ou premier plan), exécuté dans le thread GUI : confirmation
        éventuelle des actions sensibles, retour visuel bref, puis exécution. """
        label = ACTION_LABELS.get(action, action)
        # Confirmation facultative des actions sensibles (ex. déconnexion).
        if action in SENSITIVE_ACTIONS and getattr(self.window, "confirm_sensitive_shortcuts", False):
            if not self._confirm(label):
                self.logger.debug("Action sensible '%s' annulée par l'utilisateur", action)
                return
        # Affiche brièvement quelle action a été déclenchée.
        if getattr(self.window, "shortcut_feedback", False):
            self._show_feedback(label)
        self.perform(action)

    def perform(self, action):
        """ Exécute l'action. Pour next/validate/pause, on simule le CLIC du bouton
        (déjà connecté à la bonne fonction + anti-rebond) : appeler la fonction en
        plus déclencherait l'action deux fois. Gardes hasattr : sans effet sur
        l'écran de connexion (boutons absents). """
        window = self.window
        if action == "next":
            if hasattr(window, 'btn_next'):
                window.btn_next.animateClick()
        elif action == "validate":
            if hasattr(window, 'btn_validate'):
                window.btn_validate.animateClick()
        elif action == "pause":
            if hasattr(window, 'btn_pause'):
                window.btn_pause.animateClick()
        elif action == "recall":
            window.recall()
        elif action == "deconnect":
            self.logger.debug("Raccourci de déconnexion déclenché")
            window.deconnection()

    def _confirm(self, label):
        """ Demande confirmation avant une action sensible déclenchée par raccourci.
        Retourne True si l'utilisateur confirme. """
        box = QMessageBox(self.window)
        box.setWindowFlags(box.windowFlags() | Qt.WindowStaysOnTopHint)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Confirmer l'action")
        box.setText(f"Confirmer l'action « {label} » déclenchée par raccourci ?")
        yes = box.addButton("Oui", QMessageBox.YesRole)
        box.addButton("Non", QMessageBox.NoRole)
        box.setDefaultButton(yes)
        box.exec()
        return box.clickedButton() is yes

    def _show_feedback(self, label):
        """ Notification brève indiquant l'action déclenchée. force=True : ce retour
        a sa propre préférence (shortcut_feedback), indépendante du filtre général
        des notifications. """
        self.window.show_notification(
            {"origin": "shortcut_feedback", "message": f"Raccourci : {label}"},
            internal=True, force=True)

    @Slot(object)
    def _warn_failures(self, failures):
        """ Avertit l'utilisateur quand Windows/keyboard a refusé un ou plusieurs
        raccourcis globaux (touche invalide, combinaison réservée…). """
        lines = "\n".join(f"• {label} ({text})" for label, text, _err in failures)
        QMessageBox.warning(
            self.window, "Raccourcis non enregistrés",
            "Windows a refusé l'enregistrement de ces raccourcis globaux :\n\n"
            f"{lines}\n\nModifiez-les dans les préférences ou passez en mode "
            "« actifs au premier plan ».")
