"""Actions de l'équipier sur les patients (MainWindow).

Sorties de main.py : appeler le suivant, valider, mettre en pause, prolonger
l'appel, rappeler, remettre en attente (pour une activité ou un équipier),
supprimer — pour le patient du comptoir comme pour un patient désigné dans la
file — ainsi que la garde commune, les verrous « occupé » et le traitement des
réponses du serveur.

C'est une classe parente de ``MainWindow`` (mixin) et non un objet séparé :
ces actions lisent et modifient l'état de la fenêtre à chaque ligne (patient
courant, équipier, boutons, notifications, resynchronisation). Les boutons,
menus, raccourcis et l'icône de notification continuent d'appeler les mêmes
méthodes sur la fenêtre.
"""

import time
from functools import partial

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QMenu, QMessageBox

import notification_texts
from counter_api import PATIENT_ACTION_GROUP
from patient_list_model import PatientListModel

_notification_text = notification_texts.render_for


class _BusyButtonRef:
    """Référence « occupé » donnée à CounterApi à la place d'un widget concret.

    La requête tient un verrou LOGIQUE mémorisé par la fenêtre
    (``_busy_widgets``) : les boutons réels peuvent être détruits et recréés
    pendant le vol (changement d'orientation, mode compact, reconnexion) sans
    que le ``set_busy(False)`` de fin ne s'adresse à un objet Qt mort — cause
    du « RuntimeError: Internal C++ object already deleted » constaté en audit.
    """

    __slots__ = ("_window", "_attrs")

    def __init__(self, window, attrs):
        self._window = window
        self._attrs = attrs

    def set_busy(self, busy):
        self._window._set_busy_widgets(self._attrs, busy)


class PatientActionsMixin:
    """Actions patient de MainWindow (cf. docstring du module)."""

    # Boutons verrouillés pendant une action patient en vol : le verrou logique
    # (_busy_widgets) est réappliqué à chaque reconstruction de l'interface.
    _PATIENT_BUTTONS = ("btn_next", "btn_validate", "btn_pause")

    # --- Garde commune et verrous « occupé » -----------------------------------

    def _patient_action_ready(self, action):
        """ Garde métier partagée par TOUS les points d'entrée d'une action qui
        modifie le patient ou la file. Refuse si aucun agent n'est identifié sur
        le comptoir (session invalide) ou si une action du groupe exclusif est
        déjà en vol — sinon la seconde requête partirait d'un état dépassé. """
        if not (isinstance(self.staff_id, int) and self.staff_id):
            self.logger.info("Action '%s' ignorée : aucun agent au comptoir", action)
            # Refus jusque-là silencieux (icônes systray accessibles avant
            # l'identification) : l'utilisateur doit savoir pourquoi rien ne
            # se passe — sinon l'action semble ignorée.
            self.show_notification(
                {"origin": "action_refused",
                 "message": _notification_text(self, "not_identified")},
                internal=True)
            return False
        if self._tasks.is_group_active(PATIENT_ACTION_GROUP):
            self.logger.info("Action '%s' refusée : une action patient est déjà en cours", action)
            self.show_notification(
                {"origin": "action_busy", "message": _notification_text(self, "action_busy")},
                internal=True)
            return False
        return True

    def _patient_action_group(self, target_id):
        """ Une action de file ne rejoint le groupe exclusif QUE si sa cible est
        le patient courant (elle modifierait alors l'état du comptoir). """
        return PATIENT_ACTION_GROUP if target_id == self.patient_id else None

    def _on_patient_action_refused(self, reason):
        """ Appelé par CounterApi quand une requête n'a pas pu partir — filet de
        sécurité derrière _patient_action_ready (concurrence rare, ex. un menu
        validé pendant qu'une autre action vient de démarrer). """
        if reason == "shutdown":
            return
        self.logger.info("Action refusée par la couche d'accès : %s", reason)
        self.show_notification(
            {"origin": "action_busy", "message": _notification_text(self, "action_busy")},
            internal=True)

    def _resync_if_uncertain(self, result):
        """ Après un échec dont l'effet côté serveur est douteux (transport rompu
        : statut 0 — ou erreur 5xx où l'opération a pu être appliquée), demande
        une resynchronisation plutôt que de réutiliser un état local faussé. """
        if result.status == 0 or result.status >= 500:
            if hasattr(self, "session"):
                self._request_resync()

    def _busy_ref(self):
        """ Référence « occupé » couvrant tous les boutons d'action patient :
        une action du groupe exclusif les verrouille tous (les autres entrées
        seraient incompatibles de toute façon). """
        return _BusyButtonRef(self, self._PATIENT_BUTTONS)

    def _set_busy_widgets(self, attrs, busy):
        """ (Dé)verrouille les widgets nommés ET mémorise le verrou (compteur
        par widget) pour le réappliquer après une reconstruction d'interface. """
        busy_state = getattr(self, "_busy_widgets", None)
        if busy_state is None:
            busy_state = self._busy_widgets = {}
        for attr in attrs:
            count = busy_state.get(attr, 0) + (1 if busy else -1)
            if count > 0:
                busy_state[attr] = count
            else:
                busy_state.pop(attr, None)
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    widget.set_busy(count > 0)
                except RuntimeError:
                    pass  # Widget déjà détruit : le verrou logique reste juste.

    def _apply_busy_widgets(self):
        """ Réimpose les verrous « occupé » mémorisés sur les widgets actuels —
        appelé après chaque create_interface : les boutons tout neufs reprennent
        le verrou de la requête encore en vol. """
        for attr in getattr(self, "_busy_widgets", {}):
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    widget.set_busy(True)
                except RuntimeError:
                    pass

    # --- Patient du comptoir : suivant, valider, pause, prolonger, rappel ------

    def call_web_function_validate_and_call_next(self):
        # L'idempotence de cette action (ne pas faire avancer la file deux fois
        # si la requête est rejouée) est garantie par la couche d'accès.
        if not self._patient_action_ready("next"):
            return
        self.api.validate_and_call_next(on_result=self.handle_result,
                                        busy_button=self._busy_ref())
        self.update_my_buttons(self.my_patient)
        self.close_please_validate_notification()

    def call_web_function_validate(self):
        self.logger.debug("Validation du patient (call_web_function_validate)")
        if not self._patient_action_ready("validate"):
            return
        self.close_please_validate_notification()
        self.validate_my_patient(partial(self.api.validate_current_patient, self.patient_id))

    def validate_my_patient(self, send):
        """ Valide le patient s'il y en a un. ``send`` est la requête à envoyer,
        déjà liée au patient concerné (celui du comptoir, ou un patient désigné
        dans la file) : cette méthode ne décide que du « faut-il envoyer ». """
        self.logger.debug("Validation du patient en cours")
        self.close_please_validate_notification()
        if self.my_patient:
            send(on_result=self.handle_result, busy_button=self._busy_ref())
        # permet de supprimer le Validate en rouge et l'alerte en si le bouton "Valider" est resté enclenché mais qu'il n'y a plus de patient
        else:
            self.update_my_buttons(self.my_patient)

    def call_web_function_pause(self):
        alerts = getattr(self, "call_alerts", None)
        if alerts is not None and alerts.warning_active:
            # Compte à rebours affiché : le bouton Pause est devenu « Prolonger ».
            self.call_web_function_extend_call()
            return
        if not self._patient_action_ready("pause") or not self.patient_id:
            return
        self.logger.debug("Mise en pause du patient")
        self.api.pause_current_patient(self.patient_id, on_result=self.handle_result,
                                       busy_button=self._busy_ref())

    def call_web_function_extend_call(self):
        if not self._patient_action_ready("extend_call") or not self.patient_id:
            return
        self.api.extend_current_call(
            self.patient_id,
            on_result=self.handle_extend_call_result,
            busy_button=self._busy_ref())

    @Slot(object)
    def handle_extend_call_result(self, result):
        if result.status == 200 and isinstance(result.data, dict):
            self.close_please_validate_notification()
            self.update_my_patient(result.data)
            self.update_my_buttons(result.data)
            self._show_inline_notice("Appel prolongé.")
            return
        self._notify_network_error(result)
        self._resync_if_uncertain(result)

    def recall(self):
        if not self._patient_action_ready("recall") or not self.patient_id:
            return
        self.api.relaunch_call(busy_button=self._busy_ref())

    def call_web_function_validate_and_call_specifique(self, patient_select_id):
        if not self._patient_action_ready("call_specific"):
            return
        self.api.call_specific_patient(patient_select_id, on_result=self.handle_result,
                                       busy_button=self._busy_ref())

    def select_patient(self, patient_select_id):
        self.call_web_function_validate_and_call_specifique(patient_select_id)

    # --- Remettre en attente, valider ou supprimer (patient courant ou de la file) ----

    def on_action_wait(self):
        # Logique pour remettre le patient en attente
        if not self._patient_action_ready("wait") or not self.patient_id:
            return
        self.logger.debug("Patient remis en attente")
        self.api.put_standing(self.patient_id, on_result=self.handle_result,
                              busy_button=self._busy_ref(),
                              group=PATIENT_ACTION_GROUP)

    def _wait_for_targets(self, patient_id=None):
        """Destinataires de « Remettre en attente pour... » sous la forme de
        couples ``(libellé, slot)``.

        Les ÉQUIPIERS (``staff_targets``) sont proposés quand le serveur les
        fournit — l'envoi ne dépend alors d'aucune demande nominative
        préconfigurée (le serveur résout/crée la nominative à la volée).
        Repli sur les demandes nominatives (``activities_staff``) pour les
        serveurs qui n'envoient pas encore ``staff_targets``. ``patient_id``
        vise un patient de la file (menu contextuel) ; ``None`` = patient
        courant du comptoir."""
        if getattr(self, "staff_targets", None):
            return [
                (target["name"],
                 (lambda checked=False, t=target, p=patient_id:
                  self.on_action_wait_for_staff(t["id"], patient_id=p)))
                for target in self.staff_targets
            ]
        if getattr(self, "activities_staff", None):
            return [
                (activity["name"],
                 (lambda checked=False, a=activity, p=patient_id:
                  self.on_action_wait_for(a, patient_id=p)))
                for activity in self.activities_staff
            ]
        return []

    def refresh_wait_for_submenu(self):
        """(Re)peuple le sous-menu « Remettre en attente pour... » du patient
        courant et le masque quand il n'y a aucun destinataire. Le sous-menu
        existe toujours (créé à chaque create_interface) : cette méthode est
        donc appelable à chaque application d'état, y compris quand les
        destinataires arrivent après la construction de l'interface ou
        disparaissent du serveur."""
        submenu = getattr(self, "wait_for_submenu", None)
        if submenu is None:
            return
        submenu.clear()
        targets = self._wait_for_targets()
        for label, slot in targets:
            action = submenu.addAction(label)
            action.triggered.connect(slot)
        submenu.menuAction().setVisible(bool(targets))

    def on_action_wait_for_staff(self, staff_id, patient_id=None):
        """Variante « équipier » de on_action_wait_for : le serveur résout (ou
        crée à la volée) la demande nominative du destinataire."""
        if not self._patient_action_ready("wait_for"):
            return
        target_id = patient_id if patient_id is not None else self.patient_id
        if target_id is None:
            return
        group = self._patient_action_group(target_id)
        self.logger.debug("Patient remis en attente pour l'équipier id=%s", staff_id)
        self.api.put_standing_for_staff(
            target_id, staff_id,
            on_result=self._patient_result_handler(target_id),
            busy_button=self._busy_ref() if group else None,
            group=group)

    def on_action_wait_for(self, activity, patient_id=None):
        """
        patient_id: si non fourni, utilise self.patient_id (patient en cours)
        """
        if not self._patient_action_ready("wait_for"):
            return
        target_id = patient_id if patient_id is not None else self.patient_id
        if target_id is None:
            return
        group = self._patient_action_group(target_id)
        self.logger.debug("Patient remis en attente pour l'activité id=%s", activity['id'])
        self.api.put_standing(target_id, activity["id"],
                              on_result=self._patient_result_handler(target_id),
                              busy_button=self._busy_ref() if group else None,
                              group=group)

    def on_action_validate(self, patient_id):
        # Patient désigné dans la file (menu contextuel) : route « file », pas
        # celle du comptoir — et NON conditionnée à l'existence d'un patient
        # courant (la file peut en contenir alors que le comptoir est vide).
        if not self._patient_action_ready("validate_queued"):
            return
        group = self._patient_action_group(patient_id)
        self.api.validate_queued_patient(
            patient_id, on_result=self.handle_queue_result,
            busy_button=self._busy_ref() if group else None,
            group=group)

    def on_action_delete(self, patient_id=None):
        """
        patient_id: si non fourni, utilise self.patient_id (patient en cours)
        """
        if not self._patient_action_ready("delete"):
            return
        target_id = patient_id if patient_id is not None else self.patient_id
        if target_id is None:
            return
        
        msg_box = QMessageBox()
        msg_box.setWindowFlags(msg_box.windowFlags() | Qt.WindowStaysOnTopHint)
        msg_box.setIcon(QMessageBox.Warning)
        msg_box.setWindowTitle("Confirmation de suppression")
        msg_box.setText("Êtes-vous sûr de vouloir supprimer ce patient ?")
        
        # Création des boutons personnalisés
        bouton_oui = msg_box.addButton("Oui", QMessageBox.YesRole)
        bouton_non = msg_box.addButton("Non", QMessageBox.NoRole)
        msg_box.setDefaultButton(bouton_non)
        
        msg_box.exec()
        
        # Si l'utilisateur clique sur "Oui"
        if msg_box.clickedButton() == bouton_oui:
            self.logger.debug("Suppression du patient demandée")
            group = self._patient_action_group(target_id)
            self.api.delete_patient(target_id,
                                    on_result=self._patient_result_handler(target_id),
                                    busy_button=self._busy_ref() if group else None,
                                    group=group)

    # --- File des patients : clic, Entrée, menu contextuel ---------------------

    def _on_patient_list_clicked(self, index):
        """Clic sur une ligne de la vue : appelle le patient correspondant
        (équivalent de l'ancien clic sur un PatientButton)."""
        self._last_patient_list_trigger = time.monotonic()
        patient_id = index.data(PatientListModel.IdRole)
        if patient_id is not None:
            self.call_web_function_validate_and_call_specifique(patient_id)

    def _on_patient_list_activated(self, index):
        """Activation clavier (Entrée) d'une ligne : même effet qu'un clic.

        Un double-clic souris émet à la fois ``clicked`` et ``activated`` ; on
        ignore l'``activated`` s'il suit immédiatement un ``clicked`` pour ne pas
        appeler deux fois le même patient."""
        last = getattr(self, "_last_patient_list_trigger", 0)
        if time.monotonic() - last < 0.3:
            return
        self._on_patient_list_clicked(index)

    def _on_patient_list_context_menu(self, position):
        """Menu contextuel d'un patient de la vue (valider / supprimer /
        assigner). Reprend à l'identique l'ancien menu de PatientButton, mais
        construit à la demande pour le patient sous le curseur."""
        index = self.patient_list_view.indexAt(position)
        if not index.isValid():
            return
        patient = index.data(PatientListModel.PatientRole)
        if not isinstance(patient, dict):
            return
        patient_id = patient.get("id")
        if patient_id is None:
            return

        menu = QMenu(self.patient_list_view)

        action_validate = menu.addAction("Marquer comme validé")
        action_validate.triggered.connect(
            lambda checked=False, pid=patient_id: self.on_action_validate(pid))

        action_delete = menu.addAction("Supprimer")
        action_delete.triggered.connect(
            lambda checked=False, pid=patient_id: self.on_action_delete(pid))

        # Mêmes destinataires que le sous-menu du patient courant : équipiers
        # si le serveur les fournit, demandes nominatives sinon.
        targets = self._wait_for_targets(patient_id)
        if targets:
            assign_submenu = QMenu("Assigner à...", menu)
            for label, slot in targets:
                action = assign_submenu.addAction(label)
                action.triggered.connect(slot)
            menu.addMenu(assign_submenu)

        menu.exec(self.patient_list_view.viewport().mapToGlobal(position))

    # --- Résultats des actions -------------------------------------------------

    def _notify_network_error(self, result):
        """ Affiche un message utilisateur court (distinct selon le statut :
        401/403/409-423/5xx/timeout) et journalise le détail technique. Le détail
        n'est jamais montré à l'utilisateur. """
        if result.message:
            # Origine « action_error » (catégorie SYSTÈME), pas « connection » :
            # un échec d'action n'est pas un évènement d'état de connexion —
            # décocher les alertes de connexion ne doit pas le masquer, et la
            # notification reste affichée tant que l'utilisateur ne l'a pas
            # fermée (origine « sticky », cf. notification_rules).
            self.show_notification({"origin": "action_error", "message": result.message}, internal=True)
        if result.detail:
            self.logger.warning("Erreur réseau (statut=%s) : %s", result.status, result.detail)

    @Slot(object)
    def handle_result(self, result):
        self.logger.debug("Réponse action patient (statut=%s)", result.status)
        status = result.status
        if status == 200:
            data = result.data
            if isinstance(data, dict):
                self.update_my_patient(data)
                self.update_my_buttons(data)
                if data.get("call_number"):
                    message = _notification_text(self,
                        "new_patient", N=data["call_number"], A=data.get("activity", ""))
                    self.show_notification({"origin": "new_patient", "message": message}, internal=True)
            else:
                self.logger.warning("Réponse 200 sans JSON exploitable")
        # plus de patient. Attention 204 ne permet pas de passer une info car 204 =pas de données
        elif status == 204:
            self.update_my_patient(None)
            # Les boutons doivent suivre : sans cet appel ils restaient
            # actifs/minuteur lancé sur un patient qui n'existe plus.
            self.update_my_buttons(None)
        # utiliser pour supprimer ou remettre un patient en attente
        elif status == 201:
            self.update_my_patient(False)
            patient = {"counter_id": self.counter_id, "id": None}
            self.update_my_buttons(patient)
        # 423 = patient déjà pris par un autre comptoir (message dédié via l'UI)
        elif status == 423:
            self.patient_already_taken()
        else:
            self._notify_network_error(result)
            self._resync_if_uncertain(result)

    def _patient_result_handler(self, target_id):
        """Choisit le handler de résultat selon la CIBLE de l'action :
        ``handle_result`` pour le patient courant du comptoir,
        ``handle_queue_result`` pour un patient désigné dans la file — sinon un
        201 (put_standing/delete/validate) effacerait l'affichage du patient en
        cours alors qu'il concernait un autre patient."""
        if target_id is not None and target_id == self.patient_id:
            return self.handle_result
        return self.handle_queue_result

    @Slot(object)
    def handle_queue_result(self, result):
        """Résultat d'une action sur un patient de la FILE (pas le patient
        courant). Succès : la liste est rafraîchie par les évènements temps
        réel, on ne touche ni au libellé ni aux boutons du patient courant."""
        self.logger.debug("Réponse action sur la file (statut=%s)", result.status)
        if 200 <= result.status < 300:
            return
        if result.status == 423:
            self.play_notification_sound("patient_taken", "patient_taken")
            self.show_notification(
                {"origin": "patient_taken",
                 "message": _notification_text(self, "patient_taken_queue")},
                internal=True)
            return
        self._notify_network_error(result)
        self._resync_if_uncertain(result)
