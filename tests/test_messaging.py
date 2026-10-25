"""Interface et cycle de vie de la messagerie PySide."""

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import (
    QColor, QKeyEvent, QKeySequence, QPalette, QShortcut, QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QHBoxLayout, QMainWindow, QMenu, QTabBar, QWidget,
)

from messaging import (
    MessagingController, _INITIALS_ROLE, _NAME_ROLE, _RecipientFilterModel,
)
from net_result import NetResult


class FakeApi:
    def __init__(self):
        self.calls = []
        # Handle renvoyé par les requêtes « acceptées » (un objet truthy,
        # comme le vrai RequestHandle) ; None simule le refus anti-doublon
        # de la couche réseau quand une requête identique est déjà en vol.
        self.state_handle = object()
        self.presence_handle = object()

    def messaging_presence(self, instance, on_result=None):
        self.calls.append(("presence", instance, on_result))
        return self.presence_handle

    def messaging_leave(self, instance, on_result=None):
        self.calls.append(("leave", instance, on_result))

    def messaging_leave_blocking(self, instance):
        self.calls.append(("leave_blocking", instance))
        return True

    def messaging_state(self, on_result=None):
        self.calls.append(("state", on_result))
        return self.state_handle

    def messaging_messages(self, kind, peer_staff_id=None, before_id=None,
                           after_id=None, on_result=None):
        # on_result reste en position 3 (les tests existants appellent
        # call[3](NetResult(...))) ; before_id rejoint le tuple en
        # position 4 pour les tests de pagination.
        self.calls.append(
            ("messages", kind, peer_staff_id, on_result, before_id))

    def messaging_send(self, client_id, kind, body, recipient_id=None,
                       scope=None, urgent=False, on_result=None):
        # on_result reste en position 4 (les tests existants appellent
        # call[4](NetResult(...))) ; la portée broadcast est en position 5,
        # « urgent » en position 6.
        self.calls.append(
            ("send", kind, body, recipient_id, on_result, scope, urgent))

    def messaging_read(self, ids, on_result=None):
        self.calls.append(("read", list(ids), on_result))

    def messaging_delete(self, message_id, on_result=None):
        self.calls.append(("delete", message_id, on_result))


class FakeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.api = FakeApi()
        self.notifications = []
        central = QWidget(self)
        self.setCentralWidget(central)
        self.icone_layout = QHBoxLayout(central)
        self.more_menu = QMenu(self)

    def show_notification(self, data, **kwargs):
        self.notifications.append((data, kwargs))


class FakeSettings:
    values = {}

    def value(self, key, default=None, type=None):
        value = self.values.get(key, default)
        return type(value) if type is not None else value

    def setValue(self, key, value):
        self.values[key] = value


@pytest.fixture(autouse=True)
def _clean_fake_settings():
    """FakeSettings.values est un attribut de CLASSE partagé par toute la
    session : sans isolation, les clés persistées par la messagerie (onglets
    ouverts, dernière conversation, zone du dock) fuiteraient d'un test à
    l'autre."""
    previous = dict(FakeSettings.values)
    FakeSettings.values.clear()
    yield
    FakeSettings.values.clear()
    FakeSettings.values.update(previous)


def _controller():
    window = FakeWindow()
    controller = MessagingController(window, settings_factory=FakeSettings)
    window.messaging = controller
    controller.set_identity(1, "Alice")
    return window, controller


def _state(unread=0, online=True):
    return {
        "enabled": True,
        "unread_total": unread,
        "online_count": 1 if online else 0,
        "conversations": [
            {"key": "broadcast", "kind": "broadcast", "name": "Toute l’équipe",
             "online": True, "unread_count": 0},
            {"key": "direct:2", "kind": "direct", "staff_id": 2, "name": "Bob",
             "counter_id": 2, "counter_name": "2", "online": online,
             "unread_count": unread, "last_message_id": 1},
        ],
    }


def _index(controller, key):
    """Position de l'onglet portant ``key``, -1 s'il n'existe pas."""
    for i in range(controller.tabs.count()):
        if controller.tabs.tabData(i) == key:
            return i
    return -1


def _select(controller, key):
    """Sélectionne l'onglet d'une conversation (équivalent du clic)."""
    index = _index(controller, key)
    assert index >= 0, f"onglet absent : {key}"
    controller.tabs.setCurrentIndex(index)


def _activate_window(monkeypatch, window):
    """Simule la fenêtre au premier plan pour le garde « Lu »."""
    monkeypatch.setattr(QApplication, "activeWindow", lambda: window)


def test_feature_is_absent_until_enabled_and_destroyed_when_disabled():
    _window, controller = _controller()
    assert controller.button is None
    assert controller.dock is None
    controller.set_enabled(True)
    assert controller.button is not None
    assert controller.dock is not None
    assert any(call[0] == "presence" for call in controller.window.api.calls)
    controller.set_enabled(False)
    assert controller.button is None
    assert controller.dock is None
    assert not controller.heartbeat_timer.isActive()


def test_compact_composer_does_not_impose_the_app_width():
    _window, controller = _controller()
    controller.set_enabled(True)

    assert controller.dock.minimumWidth() == 0
    assert controller.send_button.text() == ""
    assert controller.send_button.width() == 36
    assert not controller.send_button.icon().isNull()
    assert controller.send_button.accessibleName() == "Envoyer le message"
    assert "Entrée" in controller.send_button.toolTip()


def test_dock_is_below_main_interface_by_default():
    previous = dict(FakeSettings.values)
    FakeSettings.values.pop("messaging_dock_area", None)
    try:
        window, controller = _controller()
        controller.set_enabled(True)
        assert window.dockWidgetArea(controller.dock) == Qt.BottomDockWidgetArea
    finally:
        FakeSettings.values.clear()
        FakeSettings.values.update(previous)


def test_saved_right_position_is_still_respected():
    previous = dict(FakeSettings.values)
    FakeSettings.values["messaging_dock_area"] = "right"
    try:
        window, controller = _controller()
        controller.set_enabled(True)
        assert window.dockWidgetArea(controller.dock) == Qt.RightDockWidgetArea
    finally:
        FakeSettings.values.clear()
        FakeSettings.values.update(previous)


def test_messaging_is_stacked_above_patient_list(_shared_qapplication):
    previous = dict(FakeSettings.values)
    FakeSettings.values.pop("messaging_dock_area", None)
    try:
        window, controller = _controller()
        patient_dock = QDockWidget("Liste des patients", window)
        patient_dock.setWidget(QWidget(patient_dock))
        patient_dock.setMinimumHeight(100)
        window.patient_list_dock = patient_dock
        window.addDockWidget(Qt.BottomDockWidgetArea, patient_dock)
        controller.set_enabled(True)
        window.show()
        controller.dock.show()
        patient_dock.show()
        controller.arrange_docks()
        _shared_qapplication.processEvents()

        assert controller.dock.geometry().top() < patient_dock.geometry().top()
    finally:
        FakeSettings.values.clear()
        FakeSettings.values.update(previous)


def test_repeated_identity_refresh_does_not_duplicate_ui():
    window, controller = _controller()
    controller.set_enabled(True)
    button = controller.button
    action = controller.menu_action

    controller.set_identity(1, "Alice")
    controller.attach_to_interface()

    assert controller.button is button
    assert controller.menu_action is action
    assert window.icone_layout.count() == 1


def test_interface_rebuild_moves_button_without_recreating_dock():
    window, controller = _controller()
    controller.set_enabled(True)
    old_button = controller.button
    dock = controller.dock

    central = QWidget(window)
    window.setCentralWidget(central)
    window.icone_layout = QHBoxLayout(central)
    window.more_menu = QMenu(window)
    controller.attach_to_interface()

    assert controller.button is not old_button
    assert controller.dock is dock
    assert window.icone_layout.count() == 1
    # Une action « Afficher/Masquer » + le sous-menu de disposition, sans
    # doublon après reconstruction.
    actions = window.more_menu.actions()
    assert len(actions) == 2
    assert sum(1 for a in actions if a.menu() is not None) == 1


def test_user_change_clears_every_message_before_next_identity():
    _window, controller = _controller()
    controller.set_enabled(True)
    controller.messages = [{"id": 1, "body": "confidentiel"}]
    controller.conversations = [{"key": "direct:2"}]
    controller.unread_total = 1

    controller.clear_identity(notify_server=False)
    controller.set_identity(2, "Bob")

    assert controller.messages == []
    assert controller.conversations == []
    assert controller.unread_total == 0
    assert controller.button is not None


def test_state_updates_badge_and_notifies_without_exposing_body():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._pending_event = {"sender_name": "Bob", "body": "secret"}
    controller._handle_state_result(NetResult(200, data=_state(unread=1)))
    assert "1" in controller.button.text()
    assert window.notifications[0][0] == {
        "origin": "messaging", "message": "Nouveau message de Bob.",
    }
    assert "secret" not in str(window.notifications)


def test_offline_contact_remains_visible_and_can_still_receive_message():
    # Le serveur livre à la prochaine connexion : écrire à un équipier hors
    # ligne n'est plus bloqué, le statut l'annonce simplement.
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state(online=False)))
    _select(controller, "direct:2")
    assert "hors ligne" in controller.tabs.tabToolTip(_index(controller, "direct:2"))
    assert controller.input.isEnabled()
    assert "prochaine connexion" in controller.status_label.text()
    controller.input.setPlainText("On se voit demain")
    assert controller.send_button.isEnabled()
    controller.send()
    send = _sends(window.api)[-1]
    assert send[1:4] == ("direct", "On se voit demain", 2)
    assert send[5] is None                        # pas de scope sur un direct


def _sends(api):
    """Appels messaging_send déjà partis vers le (faux) serveur."""
    return [call for call in api.calls if call[0] == "send"]


def test_second_send_during_flight_does_not_duplicate():
    # Régression : Entrée n'est pas debouncé — un second send() pendant la
    # requête repartait avec un nouveau client_message_id (message en double).
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.input.setPlainText("Bonjour Bob")
    controller.send()
    controller.send()          # second Entrée / clic pendant le vol
    assert len(_sends(window.api)) == 1
    assert not controller.send_button.isEnabled()


def test_send_button_stays_disabled_while_typing_during_flight():
    # Même si l'utilisateur retape du texte pendant le vol, le bouton ne doit
    # pas se réactiver avant la réponse.
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.input.setPlainText("Bonjour Bob")
    controller.send()
    controller.input.setPlainText("Brouillon suivant")
    assert not controller.send_button.isEnabled()
    _sends(window.api)[-1][4](NetResult(201, data={"id": 1}))
    assert controller.send_button.isEnabled()


def test_draft_typed_during_send_survives_the_response():
    # Régression : input.clear() inconditionnel effaçait le nouveau brouillon
    # saisi pendant l'attente de la réponse.
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.input.setPlainText("Bonjour Bob")
    controller.send()
    controller.input.setPlainText("Autre message en préparation")
    _sends(window.api)[-1][4](NetResult(201, data={"id": 1}))
    assert controller.input.toPlainText() == "Autre message en préparation"


def test_unchanged_text_is_cleared_after_success():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.input.setPlainText("Bonjour Bob")
    controller.send()
    _sends(window.api)[-1][4](NetResult(201, data={"id": 1}))
    assert controller.input.toPlainText() == ""


def test_logout_during_send_releases_the_lock():
    # Un envoi encore en vol à la déconnexion ne doit pas verrouiller la
    # prochaine session (la réponse peut ne jamais arriver).
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.input.setPlainText("Bonjour Bob")
    controller.send()
    controller.clear_identity(notify_server=False)
    assert not controller._send_in_flight
    assert controller._sent_body is None


def test_messages_response_for_old_conversation_is_dropped():
    # Réponse tardive du fil précédent : elle ne doit pas se rendre dans la
    # conversation désormais sélectionnée.
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    old_cb = [c for c in window.api.calls if c[0] == "messages"][-1][3]
    _select(controller, "broadcast")
    stale = NetResult(200, data={
        "messages": [{"id": 9, "body": "fil précédent", "sender": {"name": "Bob"}}]})
    old_cb(stale)
    assert controller.messages == []
    assert "fil précédent" not in controller.thread.toPlainText()


def test_messages_response_for_current_conversation_is_applied():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    cb = [c for c in window.api.calls if c[0] == "messages"][-1][3]
    cb(NetResult(200, data={
        "messages": [{"id": 9, "body": "Salut", "sender": {"name": "Bob"}}]}))
    assert controller.messages[0]["body"] == "Salut"
    assert "Salut" in controller.thread.toPlainText()


def test_direct_send_uses_selected_staff_and_clears_after_success():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.input.setPlainText("Bonjour Bob")
    controller.send()
    call = next(call for call in window.api.calls if call[0] == "send")
    assert call[1:4] == ("direct", "Bonjour Bob", 2)
    call[4](NetResult(201, data={"id": 1}))
    assert controller.input.toPlainText() == ""


def test_mark_read_only_when_dock_is_visible(monkeypatch):
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    _activate_window(monkeypatch, window)
    controller.messages = [{"id": 4, "is_unread": True}]
    controller.dock.hide()
    controller._mark_visible_read()
    assert not any(call[0] == "read" for call in window.api.calls)
    controller.dock.show()
    controller._mark_visible_read()
    assert any(call[:2] == ("read", [4]) for call in window.api.calls)


def test_mark_read_skipped_while_window_is_minimized(monkeypatch):
    # Fenêtre réduite : le dock reste « visible » pour Qt, mais personne ne
    # peut lire le fil — aucun accusé « Lu » ne doit partir.
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    controller.dock.show()
    controller.messages = [{"id": 4, "is_unread": True}]
    monkeypatch.setattr(window, "isMinimized", lambda: True)
    controller._mark_visible_read()
    assert not any(call[0] == "read" for call in window.api.calls)


def test_window_restored_marks_pending_unread_messages(monkeypatch):
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    controller.dock.show()
    controller.messages = [{"id": 4, "is_unread": True},
                           {"id": 5, "is_unread": True}]
    # Réduite pendant l'arrivée des messages : rien n'est accusé.
    monkeypatch.setattr(window, "isMinimized", lambda: True)
    controller._mark_visible_read()
    assert not any(call[0] == "read" for call in window.api.calls)
    # Restauration : les non-lus affichés pendant l'absence deviennent
    # lisibles — via la temporisation (le temps de voir le fil), pas en
    # instantané.
    monkeypatch.setattr(window, "isMinimized", lambda: False)
    _activate_window(monkeypatch, window)
    controller.on_window_restored()
    assert not any(call[0] == "read" for call in window.api.calls)
    assert controller._read_timer.isActive()
    controller._read_timer.timeout.emit()
    assert any(call[:2] == ("read", [4, 5]) for call in window.api.calls)


def test_mark_read_skipped_while_window_is_hidden(monkeypatch):
    # Fenêtre masquée (systray) : même constat — pas de « Lu » mensonger.
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    controller.dock.show()
    controller.messages = [{"id": 4, "is_unread": True}]
    monkeypatch.setattr(window, "isVisible", lambda: False)
    controller._mark_visible_read()
    assert not any(call[0] == "read" for call in window.api.calls)


def test_socket_disable_removes_every_entry_point():
    _window, controller = _controller()
    controller.set_enabled(True)
    controller.socket_config_changed(False)
    assert not controller.enabled
    assert controller.button is None
    assert controller.dock is None


def _last_callback(api, name, index):
    return [call for call in api.calls if call[0] == name][-1][index]


def test_direct_identity_switch_wipes_previous_agent_immediately():
    # Régression : set_identity(Alice→Bob) direct — agent relu différent en
    # resynchronisation — laissait le fil, le badge et le brouillon d'Alice
    # affichés jusqu'à la prochaine réponse réseau.
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state(unread=1)))
    _select(controller, "direct:2")
    _last_callback(window.api, "messages", 3)(NetResult(200, data={
        "messages": [{"id": 9, "body": "Secret d'Alice",
                      "sender": {"name": "Bob"}}]}))
    controller.input.setPlainText("Brouillon d'Alice")
    assert "1" in controller.button.text()
    assert "Secret" in controller.thread.toPlainText()

    dock = controller.dock
    controller.set_identity(2, "Bob")

    assert controller.staff_id == 2
    assert controller.messages == []
    assert controller.conversations == []
    assert controller.unread_total == 0
    assert controller._pending_event is None
    assert not controller._send_in_flight
    assert controller.tabs.count() == 0
    assert "Secret" not in controller.thread.toPlainText()
    assert controller.input.toPlainText() == ""
    assert controller.status_label.text() == ""
    assert controller.button.text() == "💬"
    # Le panneau n'est ni détruit ni déplacé pendant le changement.
    assert controller.dock is dock
    assert any(call[0] == "presence" for call in window.api.calls)


def test_identity_switch_preserves_dock_placement_and_visibility():
    previous = dict(FakeSettings.values)
    FakeSettings.values["messaging_dock_area"] = "right"
    FakeSettings.values["messaging_dock_visible"] = True
    try:
        window, controller = _controller()
        controller.set_enabled(True)
        dock = controller.dock
        controller.set_identity(2, "Bob")
        assert controller.dock is dock
        assert window.dockWidgetArea(dock) == Qt.RightDockWidgetArea
        assert not dock.isHidden()
    finally:
        FakeSettings.values.clear()
        FakeSettings.values.update(previous)


def test_same_identity_resync_keeps_displayed_data():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.input.setPlainText("Brouillon")

    controller.set_identity(1, "Alice")  # même agent relu en resync

    assert controller.input.toPlainText() == "Brouillon"
    assert controller.tabs.count() == 2


def test_stale_state_response_of_previous_agent_is_ignored():
    window, controller = _controller()
    controller.set_enabled(True)
    controller.request_state()
    stale_cb = _last_callback(window.api, "state", 1)
    controller.set_identity(2, "Bob")
    stale_cb(NetResult(200, data=_state(unread=5)))
    assert controller.conversations == []
    assert controller.unread_total == 0
    assert controller.tabs.count() == 0
    assert controller.button.text() == "💬"


def test_stale_presence_response_of_previous_agent_is_ignored():
    window, controller = _controller()
    controller.set_enabled(True)  # heartbeat : présence d'Alice en vol
    stale_cb = _last_callback(window.api, "presence", 2)
    controller.set_identity(2, "Bob")
    stale_cb(NetResult(200, data=_state(unread=5)))
    assert controller.conversations == []
    assert controller.unread_total == 0


def test_stale_messages_response_of_previous_agent_is_ignored():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    # « broadcast » est la conversation sélectionnée par défaut.
    assert controller._selected()["key"] == "broadcast"
    controller.request_messages()
    stale_cb = _last_callback(window.api, "messages", 3)
    controller.set_identity(2, "Bob")
    # « broadcast » est aussi une conversation de Bob : la clé de fil ne
    # suffit pas, seule la génération écarte la réponse d'Alice.
    stale_cb(NetResult(200, data={
        "messages": [{"id": 9, "body": "fil d'Alice",
                      "sender": {"name": "Carol"}}]}))
    assert controller.messages == []
    assert "fil d'Alice" not in controller.thread.toPlainText()


def test_stale_send_response_of_previous_agent_is_ignored():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.input.setPlainText("Message d'Alice")
    controller.send()
    stale_cb = _sends(window.api)[-1][4]
    controller.set_identity(2, "Bob")
    controller.input.setPlainText("Brouillon de Bob")
    calls_before = len(window.api.calls)
    stale_cb(NetResult(201, data={"id": 1}))
    # Ni effacement du brouillon de Bob, ni rafraîchissement pour Alice.
    assert controller.input.toPlainText() == "Brouillon de Bob"
    assert len(window.api.calls) == calls_before


def test_stale_read_response_of_previous_agent_is_ignored(monkeypatch):
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    _activate_window(monkeypatch, window)
    controller._handle_state_result(NetResult(200, data=_state()))
    controller.dock.show()
    controller.messages = [{"id": 4, "is_unread": True}]
    controller._mark_visible_read()
    stale_cb = _last_callback(window.api, "read", 2)
    controller.set_identity(2, "Bob")
    calls_before = len(window.api.calls)
    stale_cb(NetResult(200, data={}))
    # L'accusé d'Alice ne doit pas relancer une lecture d'état pour Bob.
    assert len(window.api.calls) == calls_before


def test_response_arriving_after_disable_is_ignored():
    window, controller = _controller()
    controller.set_enabled(True)
    controller.request_state()
    stale_cb = _last_callback(window.api, "state", 1)
    controller.set_enabled(False)
    stale_cb(NetResult(200, data=_state(unread=5)))
    assert controller.conversations == []
    assert controller.unread_total == 0
    assert controller.button is None
    assert controller.dock is None


# --- Jamais de fenêtre modale : tout passe par le panneau / les notifications

def test_messaging_module_does_not_import_qmessagebox():
    # Régression : au comptoir, un QMessageBox volait le focus en pleine
    # saisie. La messagerie ne doit plus en importer aucun — toute alerte
    # passe par le libellé d'état du panneau et les notifications flottantes.
    import messaging
    assert not hasattr(messaging, "QMessageBox")


def test_send_error_notifies_without_modal_and_keeps_draft():
    # « recipient_offline » ne survient plus que pour un broadcast
    # « connectés seulement » arrivé alors que personne n'est en ligne.
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "broadcast")
    controller._online_only.setChecked(True)
    controller.input.setPlainText("Réunion dans 5 minutes")
    controller.send()
    controller.send()                     # confirmation à deux temps
    _sends(window.api)[-1][4](NetResult(409, data={"error": "recipient_offline"}))
    assert window.notifications[-1][0] == {
        "origin": "messaging_error",
        "message": "Personne n’est connecté pour recevoir ce message.",
    }
    assert window.notifications[-1][1].get("internal") is True
    # Brouillon conservé, et le détail reste affiché dans le panneau.
    assert controller.input.toPlainText() == "Réunion dans 5 minutes"
    assert "Personne n’est connecté" in controller.status_label.text()


def test_send_error_messaging_disabled_disables_feature():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.input.setPlainText("Bonjour Bob")
    controller.send()
    _sends(window.api)[-1][4](NetResult(403, data={"error": "messaging_disabled"}))
    assert window.notifications[-1][0]["origin"] == "messaging_error"
    assert not controller.enabled
    assert controller.dock is None


def test_too_long_message_is_refused_inline_and_draft_kept():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.input.setPlainText("x" * 1001)
    controller.send()
    assert not _sends(window.api)                       # rien ne part
    assert "1 000" in controller.status_label.text()
    assert controller.input.toPlainText() == "x" * 1001  # brouillon conservé


def test_broadcast_requires_a_second_send_as_inline_confirmation():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "broadcast")
    controller.input.setPlainText("Réunion dans 5 minutes")
    controller.send()                                  # 1er Entrée : consigne
    assert not _sends(window.api)
    assert controller._broadcast_confirm
    assert "Appuyez de nouveau" in controller.status_label.text()
    controller.send()                                  # 2e Entrée : envoi réel
    assert len(_sends(window.api)) == 1
    assert _sends(window.api)[-1][1:4] == (
        "broadcast", "Réunion dans 5 minutes", None)
    assert not controller._broadcast_confirm
    # L'envoi confirmé se comporte ensuite comme un envoi normal.
    _sends(window.api)[-1][4](NetResult(201, data={"id": 1}))
    assert controller.input.toPlainText() == ""


def test_broadcast_confirmation_disarmed_asks_again():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "broadcast")
    controller.input.setPlainText("Réunion dans 5 minutes")
    controller.send()
    assert controller._broadcast_confirm
    controller._disarm_broadcast_confirm()             # expiration du délai
    assert not controller._broadcast_confirm
    assert "Appuyez de nouveau" not in controller.status_label.text()
    controller.send()                                  # redemande confirmation
    assert not _sends(window.api)
    assert controller._broadcast_confirm


def test_switching_conversation_cancels_broadcast_confirmation():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "broadcast")
    controller.input.setPlainText("Réunion dans 5 minutes")
    controller.send()
    assert controller._broadcast_confirm
    _select(controller, "direct:2")
    assert not controller._broadcast_confirm
    assert controller.status_label.text() == ""


# --- Coalescing des lectures d'état ----------------------------------------

def test_state_request_refused_as_duplicate_is_retried_after_reply():
    window, controller = _controller()
    controller.set_enabled(True)
    window.api.state_handle = None                     # refus anti-doublon simulé
    controller.request_state()
    assert controller._state_pending
    state_calls_before = len(_last_callback_calls(window.api, "state"))
    # La requête « en vol » répond : la lecture en attente est relancée aussitôt.
    window.api.state_handle = object()                 # le prochain appel part
    controller._handle_state_result(NetResult(200, data=_state()))
    assert len(_last_callback_calls(window.api, "state")) == state_calls_before + 1
    assert not controller._state_pending


def test_presence_result_also_satisfies_a_pending_state_request():
    # heartbeat et request_state partagent le même handler : une réponse de
    # présence arrivée entre-temps compte comme un état frais — mais la
    # demande en attente est tout de même relancée (état encore plus frais).
    window, controller = _controller()
    controller.set_enabled(True)
    window.api.state_handle = None
    controller.request_state()
    assert controller._state_pending
    window.api.state_handle = object()
    _last_callback(window.api, "presence", 2)(NetResult(200, data=_state()))
    assert len(_last_callback_calls(window.api, "state")) >= 2
    assert not controller._state_pending


def _last_callback_calls(api, name):
    return [call for call in api.calls if call[0] == name]


# --- client_instance_id : un UUID par processus, jamais persisté ----------

def test_client_instance_id_differs_between_instances():
    # Deux contrôleurs (deux processus / deux postes) ne partagent plus
    # d'identifiant : chacun a sa propre ligne de présence côté serveur.
    c1 = MessagingController(FakeWindow(), settings_factory=FakeSettings)
    c2 = MessagingController(FakeWindow(), settings_factory=FakeSettings)
    assert c1.client_instance_id != c2.client_instance_id
    uuid.UUID(c1.client_instance_id)              # toujours un UUID valide
    uuid.UUID(c2.client_instance_id)


def test_client_instance_id_is_never_read_nor_written_in_settings():
    # Régression : l'identifiant persisté dans QSettings était PARTAGÉ par
    # les postes d'un même profil Windows, qui s'écrasaient mutuellement leur
    # ligne de présence (même clé primaire serveur) — les équipiers
    # « disparaissaient ». La clé ne doit plus être lue ni écrite.
    touched = []

    class RecordingSettings(FakeSettings):
        def value(self, key, default=None, type=None):
            touched.append(("read", key))
            return super().value(key, default, type=type)

        def setValue(self, key, value):
            touched.append(("write", key))
            super().setValue(key, value)

    MessagingController(FakeWindow(), settings_factory=RecordingSettings)
    assert "messaging_client_instance_id" not in {key for _op, key in touched}


# --- Annonce des non-lus à la (re)connexion --------------------------------

def test_first_state_with_unread_notifies_once_per_session():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state(unread=3)))
    assert window.notifications[-1][0] == {
        "origin": "messaging",
        "message": "Vous avez 3 message(s) non lu(s).",
    }
    # Le même état relu (heartbeat suivant) ne re-notifie pas.
    count = len(window.notifications)
    controller._handle_state_result(NetResult(200, data=_state(unread=3)))
    assert len(window.notifications) == count


def test_unread_baseline_notification_resets_for_a_new_identity():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state(unread=2)))
    controller.set_identity(2, "Bob")             # nouvelle session
    controller._handle_state_result(NetResult(200, data=_state(unread=2)))
    assert window.notifications[-1][0] == {
        "origin": "messaging",
        "message": "Vous avez 2 message(s) non lu(s).",
    }


def test_no_baseline_notification_when_nothing_is_unread():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    assert window.notifications == []


def test_new_message_notification_takes_priority_over_unread_baseline():
    # Premier état de la session + événement socket déjà reçu : une seule
    # alerte — « Nouveau message » prime sur le rappel des non-lus.
    window, controller = _controller()
    controller.set_enabled(True)
    controller._pending_event = {"sender_name": "Bob"}
    controller._handle_state_result(NetResult(200, data=_state(unread=1)))
    assert [n[0]["message"] for n in window.notifications] == [
        "Nouveau message de Bob."]


# --- Portée du broadcast : « tous » ou « connectés seulement » -------------

def test_online_only_checkbox_follows_the_selected_conversation():
    _window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    # Direct : pas de portée à choisir, la case reste cachée.
    _select(controller, "direct:2")
    assert controller._online_only.isHidden()
    # Broadcast : la case apparaît…
    _select(controller, "broadcast")
    assert not controller._online_only.isHidden()
    # …et disparaît au retour sur un direct.
    _select(controller, "direct:2")
    assert controller._online_only.isHidden()


def test_online_only_checkbox_resets_on_conversation_change():
    _window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "broadcast")
    controller._online_only.setChecked(True)
    _select(controller, "direct:2")
    assert not controller._online_only.isChecked()


def test_broadcast_unchecked_checkbox_sends_scope_all_with_team_count():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "broadcast")
    controller.input.setPlainText("Réunion dans 5 minutes")
    controller.send()
    # « all » vise tous les équipiers listés, connectés ou non.
    assert "1 équipier(s)" in controller.status_label.text()
    controller.send()
    assert _sends(window.api)[-1][5] == "all"


def test_broadcast_checked_checkbox_sends_scope_online_with_online_count():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state(online=False)))
    _select(controller, "broadcast")
    controller._online_only.setChecked(True)
    controller.input.setPlainText("Réunion dans 5 minutes")
    controller.send()
    # « online » : la consigne annonce le nombre de connectés seulement.
    assert "0 personne(s) connectée(s)" in controller.status_label.text()
    controller.send()
    assert _sends(window.api)[-1][5] == "online"


# --- Clic sur une notification « Nouveau message » : ouverture du bon fil ---

def test_new_message_notification_carries_direct_conversation_key():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._pending_event = {
        "message_id": 9, "kind": "direct", "sender_staff_id": 2,
        "sender_name": "Bob",
    }
    controller._handle_state_result(NetResult(200, data=_state(unread=1)))
    assert window.notifications[0][0] == {
        "origin": "messaging",
        "message": "Nouveau message de Bob.",
        "conversation_key": "direct:2",
    }


def test_new_message_notification_carries_broadcast_conversation_key():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._pending_event = {
        "message_id": 9, "kind": "broadcast", "sender_staff_id": 2,
        "sender_name": "Bob",
    }
    controller._handle_state_result(NetResult(200, data=_state(unread=1)))
    assert window.notifications[0][0]["conversation_key"] == "broadcast"


def test_event_without_sender_uses_neutral_text_and_no_key():
    # Payload {"read": True} (accusé de lecture) : pas d'expéditeur — le
    # texte est neutre et la notification ne cible aucune conversation.
    window, controller = _controller()
    controller.set_enabled(True)
    controller._pending_event = {"read": True}
    controller._handle_state_result(NetResult(200, data=_state(unread=1)))
    assert window.notifications[0][0] == {
        "origin": "messaging", "message": "Nouveau message.",
    }


def test_open_from_notification_selects_direct_conversation():
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    controller._handle_state_result(NetResult(200, data=_state(unread=1)))
    controller.dock.hide()

    controller.open_from_notification({"conversation_key": "direct:2"})

    assert not controller.dock.isHidden()
    assert controller._selected_key() == "direct:2"


def test_open_from_notification_selects_broadcast():
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")

    controller.open_from_notification({"conversation_key": "broadcast"})

    assert not controller.dock.isHidden()
    assert controller._selected_key() == "broadcast"


def test_open_from_notification_without_key_keeps_selection():
    # Notification « Vous avez N message(s) non lu(s). » : pas de clé — le
    # panneau s'ouvre sur la conversation déjà sélectionnée.
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.dock.hide()

    controller.open_from_notification({})

    assert not controller.dock.isHidden()
    assert controller._selected_key() == "direct:2"


def test_open_restores_minimized_window(monkeypatch):
    # Fenêtre réduite au moment du clic : elle est restaurée, sinon
    # l'ouverture du panneau resterait invisible.
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    controller._handle_state_result(NetResult(200, data=_state()))
    monkeypatch.setattr(window, "isMinimized", lambda: True)
    restored = []
    monkeypatch.setattr(window, "showNormal", lambda: restored.append(True))

    controller.open_from_notification({"conversation_key": "direct:2"})

    assert restored == [True]
    assert controller._selected_key() == "direct:2"


def test_open_does_nothing_when_feature_is_disabled():
    _window, controller = _controller()      # jamais activée
    controller.open_from_notification({"conversation_key": "direct:2"})
    assert controller.dock is None


# --- Onglets de conversation ------------------------------------------------

def test_tabs_follow_active_conversations_broadcast_first():
    _window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state(unread=1)))
    assert [controller.tabs.tabData(i) for i in range(controller.tabs.count())] == [
        "broadcast", "direct:2"]
    assert controller.tabs.tabText(0) == "Équipe"
    assert controller.tabs.tabText(1) == "Bob (1)"
    # Pastille de présence sur les fils directs, aucune sur « Équipe ».
    assert controller.tabs.tabIcon(0).isNull()
    assert not controller.tabs.tabIcon(1).isNull()
    assert "Bob — Comptoir 2" in controller.tabs.tabToolTip(1)


def test_broadcast_tab_is_not_closable():
    _window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    # Le bouton de fermeture (posé par tabsClosable) est retiré sur « Équipe ».
    assert controller.tabs.tabButton(
        0, QTabBar.ButtonPosition.RightSide) is None
    direct = _index(controller, "direct:2")
    assert controller.tabs.tabButton(
        direct, QTabBar.ButtonPosition.RightSide) is not None


def test_incoming_unread_opens_tab_without_stealing_selection():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    state = _state()
    # Carol écrit sans historique connu : son onglet apparaît grâce aux
    # non-lus, mais la sélection reste sur le fil suivi.
    state["conversations"].append(
        {"key": "direct:3", "kind": "direct", "staff_id": 3,
         "name": "Carol", "online": True, "unread_count": 1})
    controller._handle_state_result(NetResult(200, data=state))
    index = _index(controller, "direct:3")
    assert index >= 0
    assert "(1)" in controller.tabs.tabText(index)
    assert controller._selected_key() == "direct:2"


def test_closing_a_manually_opened_tab_forgets_it():
    window, controller = _controller()
    controller.set_enabled(True)
    state = _state()
    # Conversation sans activité : onglet uniquement si ouvert à la main.
    state["conversations"].append(
        {"key": "direct:9", "kind": "direct", "staff_id": 9,
         "name": "Zoé Exemple", "online": True, "unread_count": 0})
    controller._handle_state_result(NetResult(200, data=state))
    assert _index(controller, "direct:9") == -1
    controller.open("direct:9")
    assert "direct:9" in controller._open_tabs
    index = _index(controller, "direct:9")
    assert index >= 0
    controller._close_tab(index)
    assert _index(controller, "direct:9") == -1
    assert "direct:9" not in controller._open_tabs


def test_manually_closed_tab_stays_closed_on_next_state():
    # Fermer un fil « avec historique mais rien de neuf » n'est pas un « vu »
    # forcé : l'onglet ne renaît pas à chaque état (heartbeat, socket…).
    _window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    index = _index(controller, "direct:2")
    assert index >= 0                       # historique → onglet présent
    controller._close_tab(index)
    assert _index(controller, "direct:2") == -1
    assert controller._closed_tabs["direct:2"] == 1   # last_message_id mémorisé
    controller._handle_state_result(NetResult(200, data=_state()))
    assert _index(controller, "direct:2") == -1       # reste fermé


def test_closed_tab_reappears_on_new_activity():
    _window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    controller._close_tab(_index(controller, "direct:2"))
    state = _state()
    # Nouveau message dans le fil : last_message_id a changé, l'onglet renaît.
    state["conversations"][1]["last_message_id"] = 2
    state["conversations"][1]["unread_count"] = 1
    controller._handle_state_result(NetResult(200, data=state))
    assert _index(controller, "direct:2") >= 0
    assert "direct:2" not in controller._closed_tabs


def test_closed_tabs_are_remembered_across_sessions():
    _window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    controller._close_tab(_index(controller, "direct:2"))
    # Nouvelle session (autre fenêtre/contrôleur, mêmes réglages) : la
    # fermeture est retrouvée dans QSettings.
    _window2, controller2 = _controller()
    controller2.set_enabled(True)
    controller2._handle_state_result(NetResult(200, data=_state()))
    assert _index(controller2, "direct:2") == -1


def test_search_reopens_a_manually_closed_tab():
    _window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    controller._close_tab(_index(controller, "direct:2"))
    controller.search.setText("bo")
    controller._activate_first_recipient()
    assert _index(controller, "direct:2") >= 0
    assert "direct:2" not in controller._closed_tabs
    assert controller._selected_key() == "direct:2"


def test_notification_reopens_a_manually_closed_tab():
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    controller._handle_state_result(NetResult(200, data=_state()))
    controller._close_tab(_index(controller, "direct:2"))
    controller.open_from_notification({"conversation_key": "direct:2"})
    assert _index(controller, "direct:2") >= 0
    assert "direct:2" not in controller._closed_tabs
    assert controller._selected_key() == "direct:2"


def test_open_before_state_arrival_is_consumed_by_next_refresh():
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    # La conversation n'est pas encore connue : l'intention est mémorisée.
    controller.open("direct:2")
    assert controller._pending_open_key == "direct:2"
    controller._handle_state_result(NetResult(200, data=_state()))
    assert controller._pending_open_key is None
    assert controller._selected_key() == "direct:2"


def test_last_selected_conversation_is_restored():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    # Nouvelle session (autre fenêtre/contrôleur, mêmes réglages) : la
    # dernière conversation consultée est retrouvée à l'état suivant.
    window2, controller2 = _controller()
    controller2.set_enabled(True)
    controller2._handle_state_result(NetResult(200, data=_state()))
    assert controller2._selected_key() == "direct:2"


# --- Recherche de destinataire ----------------------------------------------

def test_recipient_filter_matches_name_fragments_and_initials():
    model = QStandardItemModel()
    for name, key in (("Fabien Test", "direct:2"), ("Bob Martin", "direct:3")):
        item = QStandardItem(f"{name} — Comptoir 1")
        item.setData(key, Qt.ItemDataRole.UserRole)
        item.setData(name, _NAME_ROLE)
        model.appendRow(item)
    proxy = _RecipientFilterModel()
    proxy.setSourceModel(model)
    proxy.set_query("ft")        # préfixe des initiales
    assert proxy.rowCount() == 1
    assert proxy.index(0, 0).data() == "Fabien Test — Comptoir 1"
    proxy.set_query("mar")       # sous-chaîne du nom
    assert proxy.index(0, 0).data() == "Bob Martin — Comptoir 1"
    proxy.set_query("bobi")      # ni sous-chaîne, ni initiales
    assert proxy.rowCount() == 0


def _recipient_item(name, key, initials=None, counter="1"):
    """Item du modèle de recherche, comme le construit _rebuild_recipients."""
    label = f"{name} ({initials})" if initials else name
    item = QStandardItem(f"{label} — Comptoir {counter}")
    item.setData(key, Qt.ItemDataRole.UserRole)
    item.setData(name, _NAME_ROLE)
    item.setData(initials, _INITIALS_ROLE)
    return item


def test_recipient_filter_matches_business_initials():
    # « Benoit » seul ne dériverait que « b » des mots du nom : c'est la
    # colonne initials du serveur (« BN », celle de la connexion) qui est
    # matchée — affichée dans le libellé au passage.
    model = QStandardItemModel()
    model.appendRow(_recipient_item("Benoit", "direct:4", initials="BN"))
    model.appendRow(_recipient_item("Bob Martin", "direct:3"))
    proxy = _RecipientFilterModel()
    proxy.setSourceModel(model)
    proxy.set_query("bn")
    assert proxy.rowCount() == 1
    assert proxy.index(0, 0).data() == "Benoit (BN) — Comptoir 1"
    # Rôle absent (vieux serveur, initials=None) : les critères classiques
    # continuent de fonctionner, rien ne casse.
    proxy.set_query("bob")
    assert proxy.rowCount() == 1
    assert proxy.index(0, 0).data() == "Bob Martin — Comptoir 1"
    proxy.set_query("bnz")
    assert proxy.rowCount() == 0


def test_search_opens_and_selects_the_conversation():
    _window, controller = _controller()
    controller.set_enabled(True)
    state = _state()
    state["conversations"].append(
        {"key": "direct:9", "kind": "direct", "staff_id": 9,
         "name": "Zoé Exemple", "counter_name": "3",
         "online": True, "unread_count": 0})
    controller._handle_state_result(NetResult(200, data=state))
    assert _index(controller, "direct:9") == -1
    controller.search.setText("zé")
    assert controller._recipient_proxy.rowCount() == 1
    controller._activate_first_recipient()
    assert controller._selected_key() == "direct:9"
    assert "direct:9" in controller._open_tabs
    assert controller.search.text() == ""


def test_search_enter_without_match_does_not_create_anything():
    # Régression : l'ancien combo éditable (InsertAtBottom) ajoutait un item
    # fantôme quand Entrée ne correspondait à rien.
    _window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    controller.search.setText("personne")
    assert controller._recipient_proxy.rowCount() == 0
    controller._activate_first_recipient()
    assert controller.tabs.count() == 2
    assert controller.search.text() == "personne"


def test_escape_clears_the_search_field():
    _window, controller = _controller()
    controller.set_enabled(True)
    controller.search.setText("bob")
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.NoModifier)
    controller.search.keyPressEvent(event)
    assert controller.search.text() == ""


# --- Brouillons par conversation ---------------------------------------------

def test_draft_is_kept_per_conversation():
    _window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "broadcast")
    controller.input.setPlainText("Note pour l'équipe")
    _select(controller, "direct:2")
    assert controller.input.toPlainText() == ""
    controller.input.setPlainText("Note pour Bob")
    _select(controller, "broadcast")
    assert controller.input.toPlainText() == "Note pour l'équipe"
    _select(controller, "direct:2")
    assert controller.input.toPlainText() == "Note pour Bob"


def test_successful_send_purges_the_conversation_draft():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.input.setPlainText("Bonjour Bob")
    controller.send()
    # Changement d'onglet pendant le vol : le brouillon part quand même.
    _select(controller, "broadcast")
    _sends(window.api)[-1][4](NetResult(201, data={"id": 1}))
    _select(controller, "direct:2")
    assert controller.input.toPlainText() == ""


# --- Accusé « Lu » temporisé -------------------------------------------------

def test_mark_read_skipped_while_window_is_inactive(monkeypatch):
    # Fenêtre affichée mais SANS le focus (autre application, écran
    # verrouillé) : le garde « premier plan » empêche le « Lu » mensonger —
    # le dock détaché compte comme fenêtre active à lui seul.
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    controller.dock.show()
    controller.messages = [{"id": 4, "is_unread": True}]
    monkeypatch.setattr(QApplication, "activeWindow", lambda: None)
    controller._mark_visible_read()
    assert not any(call[0] == "read" for call in window.api.calls)
    controller.dock.setFloating(True)
    monkeypatch.setattr(QApplication, "activeWindow", lambda: controller.dock)
    controller._mark_visible_read()
    assert any(call[:2] == ("read", [4]) for call in window.api.calls)


def test_messages_result_arms_read_timer_instead_of_marking(monkeypatch):
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    controller.dock.show()
    _activate_window(monkeypatch, window)
    controller._handle_state_result(NetResult(200, data=_state()))
    cb = _last_callback(window.api, "messages", 3)
    cb(NetResult(200, data={"messages": [
        {"id": 4, "body": "Salut", "is_unread": True,
         "sender": {"name": "Bob"}}]}))
    # L'accusé n'est pas immédiat : la temporisation laisse le temps de lire.
    assert not any(call[0] == "read" for call in window.api.calls)
    assert controller._read_timer.isActive()
    controller._read_timer.timeout.emit()
    assert any(call[:2] == ("read", [4]) for call in window.api.calls)


def test_read_mark_skipped_while_thread_is_scrolled_up(monkeypatch):
    # Ascenseur remonté dans l'historique : les derniers messages ne sont
    # pas à l'écran, on ne peut pas les prétendre lus.
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    controller.dock.show()
    _activate_window(monkeypatch, window)
    controller.messages = [{"id": 4, "is_unread": True}]
    bar = controller.thread.verticalScrollBar()
    bar.setRange(0, 100)
    bar.setValue(20)
    controller._mark_visible_read()
    assert not any(call[0] == "read" for call in window.api.calls)


def test_unread_colors_tab_text_and_badge_button():
    _window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state(unread=1)))
    direct = _index(controller, "direct:2")
    # Onglet coloré + bouton 💬 coloré : le signal est visible même si le
    # fil concerné n'est pas celui affiché.
    assert controller.tabs.tabTextColor(direct) == QColor("#d97706")
    assert controller.button.styleSheet()
    # Plus de non-lu : retour au neutre.
    controller._handle_state_result(NetResult(200, data=_state(unread=0)))
    default = controller.tabs.palette().color(QPalette.ColorRole.WindowText)
    assert controller.tabs.tabTextColor(direct) == default
    assert controller.button.styleSheet() == ""


# --- Réponses rapides « ⚡ » ---------------------------------------------------

def test_quick_reply_inserts_text_without_sending():
    # Le menu insère la phrase au curseur : jamais d'envoi direct — un envoi
    # immédiat sous le doigt au comptoir serait une fausse manip.
    window, controller = _controller()
    controller.set_enabled(True)
    assert controller.quick_button.accessibleName() == "Réponses rapides"
    # Les phrases restent les premières entrées du menu — l'action
    # « Retirer mon dernier message » vient après un séparateur.
    labels = [a.text() for a in controller.quick_button.menu().actions()
              if not a.isSeparator()]
    assert labels[:len(controller.QUICK_REPLIES)] == \
        list(controller.QUICK_REPLIES)
    assert labels[-1] == "Retirer mon dernier message"
    # Pas de conversation tant que l'état n'est pas arrivé : grisé.
    assert not controller.quick_button.isEnabled()
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    assert controller.quick_button.isEnabled()
    controller._insert_quick_reply("J'arrive")
    assert controller.input.toPlainText() == "J'arrive"
    assert not _sends(window.api)
    # Insertion au curseur, pas de remplacement du brouillon.
    controller._insert_quick_reply("En réserve")
    assert controller.input.toPlainText() == "J'arriveEn réserve"


# --- Rendu du fil : repère de non-lus, regroupement, « Lu » -------------------

def _feed(controller, api, messages):
    """Répond au dernier messaging_messages avec le fil donné."""
    _last_callback(api, "messages", 3)(
        NetResult(200, data={"messages": messages}))


def test_unread_separator_marks_first_incoming_unread():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed(controller, window.api, [
        {"id": 1, "body": "déjà lu",
         "sender": {"name": "Bob", "staff_id": 2}},
        {"id": 2, "body": "le nouveau", "is_unread": True,
         "sender": {"name": "Bob", "staff_id": 2}},
        {"id": 3, "body": "et celui-ci", "is_unread": True,
         "sender": {"name": "Bob", "staff_id": 2}},
    ])
    html_out = controller.thread.toHtml()
    # Une seule fois, juste avant le premier non-lu entrant.
    assert html_out.count("Nouveaux messages") == 1
    assert html_out.index("déjà lu") < html_out.index("Nouveaux messages")
    assert html_out.index("Nouveaux messages") < html_out.index("le nouveau")


def test_no_unread_separator_when_the_whole_thread_is_new():
    # Fil 100 % non lu : le contexte est évident, pas de repère en tête.
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed(controller, window.api, [
        {"id": 1, "body": "un", "is_unread": True,
         "sender": {"name": "Bob", "staff_id": 2}},
        {"id": 2, "body": "deux", "is_unread": True,
         "sender": {"name": "Bob", "staff_id": 2}},
    ])
    assert "Nouveaux messages" not in controller.thread.toHtml()


def test_consecutive_same_sender_messages_share_one_header():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed(controller, window.api, [
        {"id": 1, "body": "premier",
         "sender": {"name": "Bob", "staff_id": 2}},
        {"id": 2, "body": "deuxième du même",
         "sender": {"name": "Bob", "staff_id": 2}},
        # Un autre expéditeur entre-temps : nouveau nom affiché.
        {"id": 3, "body": "réponse", "read_at": "2025-01-01T10:00:00",
         "sender": {"name": "Alice", "staff_id": 1}},
    ])
    html_out = controller.thread.toHtml()
    # Nom affiché une seule fois sur les deux messages consécutifs de Bob
    # (Qt resérialise <b> en span font-weight, on compte le texte).
    assert html_out.count("Bob") == 1
    # Bulle regroupée : marge supérieure réduite, heure conservée.
    assert "margin-top:2px" in html_out


def test_read_receipt_is_discreet_grey():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed(controller, window.api, [
        {"id": 1, "body": "vu ?", "read_at": "2025-01-01T10:00:00",
         "sender": {"name": "Alice", "staff_id": 1}},
    ])
    assert "color:#6b7280" in controller.thread.toHtml()


def test_delivered_receipt_shows_remis_until_read():
    # « Remis » (l'app du destinataire a récupéré le message) se distingue
    # de « Lu » (consultation réelle) : Lu prime quand les deux existent.
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed(controller, window.api, [
        {"id": 1, "body": "remis ?",
         "delivered_at": "2025-01-01T10:00:00",
         "sender": {"name": "Alice", "staff_id": 1}},
    ])
    html_out = controller.thread.toHtml()
    assert "Remis" in html_out and "Lu" not in html_out
    _feed(controller, window.api, [
        {"id": 1, "body": "vu ?", "delivered_at": "2025-01-01T10:00:00",
         "read_at": "2025-01-01T10:05:00",
         "sender": {"name": "Alice", "staff_id": 1}},
    ])
    html_out = controller.thread.toHtml()
    assert "Lu" in html_out and "Remis" not in html_out


# --- Alerte barre des tâches ---------------------------------------------------

def test_new_message_flashes_taskbar_when_window_inactive(monkeypatch):
    window, controller = _controller()
    controller.set_enabled(True)
    alerts = []
    monkeypatch.setattr(
        QApplication, "alert", lambda widget, msec=0: alerts.append(widget))
    monkeypatch.setattr(QApplication, "activeWindow", lambda: None)
    controller._pending_event = {"sender_name": "Bob"}
    controller._handle_state_result(NetResult(200, data=_state(unread=1)))
    assert alerts == [window]
    assert window.notifications          # la notification reste envoyée
    # Fenêtre déjà au premier plan : inutile de la faire clignoter.
    alerts.clear()
    _activate_window(monkeypatch, window)
    controller._pending_event = {"sender_name": "Bob"}
    controller._handle_state_result(NetResult(200, data=_state(unread=2)))
    assert alerts == []


# --- Clavier : Ctrl+Tab, Échap -------------------------------------------------

def test_ctrl_tab_cycles_conversation_tabs():
    _window, controller = _controller()
    controller.set_enabled(True)
    state = _state()
    state["conversations"].append({
        "key": "direct:3", "kind": "direct", "staff_id": 3, "name": "Chloé",
        "counter_id": 3, "counter_name": "3", "online": True,
        "unread_count": 0, "last_message_id": 5})
    controller._handle_state_result(NetResult(200, data=state))
    _select(controller, "broadcast")
    next_shortcut = next(
        s for s in controller.dock.findChildren(QShortcut)
        if s.key() == QKeySequence("Ctrl+Tab"))
    next_shortcut.activated.emit()
    assert controller._selected_key() == "direct:2"
    next_shortcut.activated.emit()
    assert controller._selected_key() == "direct:3"
    # Circulaire : au-delà du dernier, retour au premier.
    next_shortcut.activated.emit()
    assert controller._selected_key() == "broadcast"
    # Ctrl+Maj+Tab remonte.
    previous_shortcut = next(
        s for s in controller.dock.findChildren(QShortcut)
        if s.key() == QKeySequence("Ctrl+Shift+Tab"))
    previous_shortcut.activated.emit()
    assert controller._selected_key() == "direct:3"


def test_escape_gives_focus_back_to_the_main_window(monkeypatch):
    # Champ de recherche : premier Échap vide, second rend la main.
    window, controller = _controller()
    controller.set_enabled(True)
    focused = []
    monkeypatch.setattr(window, "setFocus", lambda: focused.append(True))
    escape = lambda: QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.NoModifier)
    controller.search.setText("bob")
    controller.search.keyPressEvent(escape())
    assert controller.search.text() == "" and not focused
    controller.search.keyPressEvent(escape())
    assert focused == [True]
    # Champ de saisie : Échap rend la main directement (rien à vider).
    focused.clear()
    controller.input.keyPressEvent(escape())
    assert focused == [True]
    # Le brouillon saisi n'est pas perdu.
    controller.input.setPlainText("brouillon")
    controller.input.keyPressEvent(escape())
    assert controller.input.toPlainText() == "brouillon"


# --- Titre du dock -------------------------------------------------------------

def test_dock_title_counts_unread_messages():
    # Groupé en onglets avec la file des patients, le titre est la seule
    # étiquette visible : il porte le nombre de non-lus.
    _window, controller = _controller()
    controller.set_enabled(True)
    assert controller.dock.windowTitle() == "Messagerie"
    controller._handle_state_result(NetResult(200, data=_state(unread=2)))
    assert controller.dock.windowTitle() == "Messagerie (2)"
    controller._handle_state_result(NetResult(200, data=_state(unread=0)))
    assert controller.dock.windowTitle() == "Messagerie"


# --- Mentions « @initiales » ---------------------------------------------------

def test_mentioned_broadcast_notifies_with_dedicated_origin():
    # Un broadcast qui cite mes initiales déclenche l'alerte « mention »
    # (origine dédiée, persistante) plutôt que l'alerte générique.
    import notification_rules
    window, controller = _controller()
    controller.set_enabled(True)
    assert notification_rules.is_sticky("messaging_mention") is True
    controller._pending_event = {
        "sender_name": "Bob", "kind": "broadcast",
        "mentioned_staff_ids": [1],   # agent courant (set_identity(1, ...))
    }
    controller._handle_state_result(NetResult(200, data=_state(unread=1)))
    data, _kwargs = window.notifications[0]
    assert data["origin"] == "messaging_mention"
    assert data["conversation_key"] == "broadcast"
    assert "mentionné" in data["message"]


def test_unmentioned_broadcast_keeps_generic_origin():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._pending_event = {
        "sender_name": "Bob", "kind": "broadcast",
        "mentioned_staff_ids": [2],   # quelqu'un d'autre est cité
    }
    controller._handle_state_result(NetResult(200, data=_state(unread=1)))
    assert window.notifications[0][0]["origin"] == "messaging"


def test_baseline_notification_leads_with_unread_mentions():
    # Non-lus à la connexion dont des mentions : l'alerte les met en avant.
    window, controller = _controller()
    controller.set_enabled(True)
    state = _state(unread=2)
    state["unread_mentions"] = 1
    controller._handle_state_result(NetResult(200, data=state))
    data, _kwargs = window.notifications[0]
    assert data["origin"] == "messaging_mention"
    assert "1 mention(s)" in data["message"]
    # Sans mention : l'alerte reste l'origine générique.
    window.notifications.clear()
    controller._baseline_known = False
    controller._handle_state_result(NetResult(200, data=_state(unread=3)))
    assert window.notifications[0][0]["origin"] == "messaging"


def test_known_initials_are_highlighted_in_the_thread():
    # « @XX » n'est surligné que si c'est une initiale connue : fond orange
    # pour la mienne, gras orange pour les autres, texte ordinaire sinon.
    window, controller = _controller()
    controller.set_enabled(True)
    state = _state()
    state["self"] = {"staff_id": 1, "initials": "AL"}
    state["conversations"][1]["initials"] = "BN"
    controller._handle_state_result(NetResult(200, data=state))
    # « Équipe » est déjà l'onglet sélectionné par défaut : l'aller-retour
    # force la requête du fil broadcast (request_messages).
    _select(controller, "direct:2")
    _select(controller, "broadcast")
    _feed(controller, window.api, [
        {"id": 1, "body": "@AL et @BN, réunion à @ZZ",
         "sender": {"name": "Bob", "staff_id": 2}},
    ])
    html_out = controller.thread.toHtml()
    assert "background-color:#b45309" in html_out          # @AL (moi)
    assert re.search(r"color:#b45309;[^>]*>@BN", html_out)  # @BN (connu)
    assert not re.search(r"font-weight:\d+[^>]*>@ZZ", html_out)  # inconnu


# --- Messages « urgent » -------------------------------------------------------

def test_urgent_button_arms_next_send_and_disarms_on_success():
    # « ❗ » arme UN envoi : propagé au serveur, désarmé au succès — le
    # message suivant repart en normal.
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    assert controller.urgent_button.accessibleName() == "Message urgent"
    controller.input.setPlainText("Panne au comptoir 3 !")
    controller.urgent_button.setChecked(True)
    controller.send()
    assert _sends(window.api)[-1][6] is True          # urgent propagé
    _sends(window.api)[-1][4](NetResult(201, data={"id": 1}))
    assert not controller.urgent_button.isChecked()
    controller.input.setPlainText("C'est réparé")
    controller.send()
    assert _sends(window.api)[-1][6] is False


def test_urgent_button_resets_on_conversation_change():
    # Armé dans un fil, désarmé en changeant d'onglet : l'urgence ne se
    # transporte pas dans la conversation suivante.
    _window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.urgent_button.setChecked(True)
    _select(controller, "broadcast")
    assert not controller.urgent_button.isChecked()


def test_urgent_notification_uses_sticky_origin():
    import notification_rules
    window, controller = _controller()
    controller.set_enabled(True)
    assert notification_rules.is_sticky("messaging_urgent") is True
    controller._pending_event = {"sender_name": "Bob", "urgent": True}
    controller._handle_state_result(NetResult(200, data=_state(unread=1)))
    data, _kwargs = window.notifications[0]
    assert data["origin"] == "messaging_urgent"
    assert "urgent" in data["message"]


def test_mention_takes_priority_over_urgent():
    # Mentionné ET urgent : la mention nomme l'équipier — elle prime.
    window, controller = _controller()
    controller.set_enabled(True)
    controller._pending_event = {
        "sender_name": "Bob", "kind": "broadcast",
        "mentioned_staff_ids": [1], "urgent": True}
    controller._handle_state_result(NetResult(200, data=_state(unread=1)))
    assert window.notifications[0][0]["origin"] == "messaging_mention"


def test_baseline_unread_urgent_notifies_with_urgent_origin():
    window, controller = _controller()
    controller.set_enabled(True)
    state = _state(unread=2)
    state["unread_urgent"] = 2
    controller._handle_state_result(NetResult(200, data=state))
    data, _kwargs = window.notifications[0]
    assert data["origin"] == "messaging_urgent"
    assert "2 message(s) urgent(s)" in data["message"]
    # Mentions + urgents : l'origine mention prime, les urgents sont cumulés.
    window.notifications.clear()
    controller._baseline_known = False
    state["unread_mentions"] = 1
    controller._handle_state_result(NetResult(200, data=state))
    data, _kwargs = window.notifications[0]
    assert data["origin"] == "messaging_mention"
    assert "2 urgent(s)" in data["message"]


def test_urgent_message_renders_with_red_bubble_and_marker():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed(controller, window.api, [
        {"id": 1, "body": "Panne !", "urgent": True,
         "sender": {"name": "Bob", "staff_id": 2}},
    ])
    html_out = controller.thread.toHtml()
    assert "#fee2e2" in html_out
    assert "❗" in html_out


# --- Indicateur « … écrit » ----------------------------------------------------

class _FakeWs:
    """Client websocket factice : mémorise les émissions send_typing."""
    def __init__(self):
        self.typing = []

    def send_typing(self, payload):
        self.typing.append(payload)


def test_typing_signal_is_emitted_throttled():
    window, controller = _controller()
    ws = _FakeWs()
    window.socket_io_client = ws
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.input.setPlainText("B")
    controller.input.setPlainText("Bo")   # < 3 s après : pas de second envoi
    assert len(ws.typing) == 1
    payload = ws.typing[0]
    assert payload["kind"] == "direct"
    assert payload["staff_id"] == 1
    assert payload["peer_staff_id"] == 2
    assert payload["peer_counter_id"] == 2
    assert payload["counter_id"] is not None or "counter_id" in payload
    # Intervalle écoulé : un nouveau signal part.
    controller._last_typing_emit -= controller.TYPING_EMIT_INTERVAL
    controller.input.setPlainText("Bon")
    assert len(ws.typing) == 2


def test_typing_indicator_shows_and_expires_for_current_conversation():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    controller.socket_typing({"kind": "direct", "staff_id": 2,
                              "peer_staff_id": 1, "name": "Bob"})
    assert controller.status_label.text() == "Bob écrit…"
    # Pas de signal « arrêté d'écrire » : l'expiration l'efface toute seule.
    controller._typing_timer.timeout.emit()
    assert controller.status_label.text() == ""
    # Un nouveau signal réarme puis expire de nouveau.
    controller.socket_typing({"kind": "direct", "staff_id": 2,
                              "peer_staff_id": 1, "name": "Bob"})
    assert "écrit" in controller.status_label.text()
    controller.socket_typing({"kind": "direct", "staff_id": 2,
                              "peer_staff_id": 1, "name": "Bob"})
    controller._typing_timer.timeout.emit()
    assert controller.status_label.text() == ""


def test_typing_indicator_ignores_irrelevant_signals():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    # Moi-même (autre poste du même agent) : ignoré.
    controller.socket_typing({"kind": "direct", "staff_id": 1,
                              "peer_staff_id": 1, "name": "Alice"})
    assert controller.status_label.text() == ""
    # Un autre interlocuteur que celui du fil affiché : ignoré.
    controller.socket_typing({"kind": "direct", "staff_id": 3,
                              "peer_staff_id": 1, "name": "Chloé"})
    assert controller.status_label.text() == ""
    # Typing « équipe » dans le fil direct : ignoré.
    controller.socket_typing({"kind": "broadcast", "staff_id": 2,
                              "name": "Bob"})
    assert controller.status_label.text() == ""
    # … mais affiché dans le fil « Équipe ».
    _select(controller, "broadcast")
    controller.socket_typing({"kind": "broadcast", "staff_id": 2,
                              "name": "Bob"})
    assert controller.status_label.text() == "Bob écrit…"


# --- Retrait d'un message (« Retirer mon dernier message ») -----------------

def _my_message(message_id=30, created_at=None):
    """Un message envoyé par moi (staff_id=1), récent par défaut."""
    return {
        "id": message_id, "kind": "direct", "body": "envoyé par erreur",
        "sender": {"name": "Alice", "staff_id": 1},
        "created_at": (
            created_at or datetime.now(timezone.utc)).isoformat(),
    }


def _feed_with(controller, api, messages, has_more=None):
    """Répond au dernier messaging_messages avec un contrôle de has_more."""
    data = {"messages": messages}
    if has_more is not None:
        data["has_more"] = has_more
    _last_callback(api, "messages", 3)(NetResult(200, data=data))


def test_delete_action_is_only_enabled_on_my_recent_last_message():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")

    # Dernier message de Bob, pas de moi : action grisée.
    _feed_with(controller, window.api, [
        {"id": 10, "body": "de Bob",
         "sender": {"name": "Bob", "staff_id": 2}},
    ])
    controller._refresh_delete_action()
    assert not controller._delete_action.isEnabled()

    # Mon message mais hors de la fenêtre de 15 min : grisé pareil.
    _feed_with(controller, window.api, [
        {"id": 10, "body": "de Bob",
         "sender": {"name": "Bob", "staff_id": 2}},
        _my_message(message_id=11, created_at=(
            datetime.now(timezone.utc) - timedelta(minutes=20))),
    ])
    controller._refresh_delete_action()
    assert not controller._delete_action.isEnabled()

    # Mon message récent en fin de fil : activé.
    _feed_with(controller, window.api, [
        {"id": 10, "body": "de Bob",
         "sender": {"name": "Bob", "staff_id": 2}},
        _my_message(message_id=11),
    ])
    controller._refresh_delete_action()
    assert controller._delete_action.isEnabled()


def test_delete_requires_a_second_click_then_calls_the_api():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed_with(controller, window.api, [_my_message()])

    # Premier clic : consigne affichée, aucune requête.
    controller._request_delete()
    assert controller._delete_confirm
    assert controller._status_message == (
        "Cliquez encore pour retirer le message")
    assert not [c for c in window.api.calls if c[0] == "delete"]

    # Second clic dans le délai : la suppression part.
    controller._request_delete()
    delete_calls = [c for c in window.api.calls if c[0] == "delete"]
    assert len(delete_calls) == 1 and delete_calls[0][1] == 30
    assert not controller._delete_confirm
    assert controller._status_message is None


def test_delete_success_reloads_state_and_thread():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed_with(controller, window.api, [_my_message()])
    window.api.calls.clear()

    controller._request_delete()
    controller._request_delete()
    _last_callback(window.api, "delete", 2)(
        NetResult(200, data={"deleted": True, "message_id": 30}))
    names = [c[0] for c in window.api.calls]
    assert "state" in names and "messages" in names
    assert controller._status_message is None


def test_delete_expired_window_is_reported_inline():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed_with(controller, window.api, [_my_message()])
    controller._request_delete()
    controller._request_delete()
    _last_callback(window.api, "delete", 2)(
        NetResult(409, data={"error": "delete_window_expired"}))
    assert controller._status_message == "Délai dépassé (15 min)."


def test_delete_other_errors_are_reported_inline():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed_with(controller, window.api, [_my_message()])
    controller._request_delete()
    controller._request_delete()
    _last_callback(window.api, "delete", 2)(
        NetResult(400, data={"error": "invalid_message"}))
    assert controller._status_message == (
        "Le message n'a pas pu être retiré.")


def test_delete_intent_is_disarmed_on_tab_change():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed_with(controller, window.api, [_my_message()])
    controller._request_delete()
    assert controller._delete_confirm
    _select(controller, "broadcast")
    assert not controller._delete_confirm
    assert controller._status_message is None


# --- Pagination « charger plus ancien » -------------------------------------

def test_scrolling_to_top_requests_older_messages_with_before_id():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed_with(controller, window.api, [
        {"id": 10, "body": "dix",
         "sender": {"name": "Bob", "staff_id": 2}},
        {"id": 11, "body": "onze",
         "sender": {"name": "Alice", "staff_id": 1}},
    ], has_more=True)
    calls_before = len([c for c in window.api.calls if c[0] == "messages"])

    controller._maybe_load_older(0)
    calls = [c for c in window.api.calls if c[0] == "messages"]
    assert len(calls) == calls_before + 1
    # before_id = id du plus ancien message chargé.
    assert calls[-1][4] == 10

    # Descente de l'ascenseur : pas de chargement.
    controller._older_in_flight = False
    controller._maybe_load_older(50)
    assert len([c for c in window.api.calls if c[0] == "messages"]) == \
        calls_before + 1


def test_older_response_prepends_and_preserves_bottom_distance():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed_with(controller, window.api, [
        {"id": 10, "body": "dix",
         "sender": {"name": "Bob", "staff_id": 2}},
    ], has_more=True)
    controller._load_older()

    # Position de lecture mémorisée : 20 px au-dessus du bas (l'ascenseur
    # hors-écran n'a pas de plage réelle, on simule sa géométrie).
    bar = controller.thread.verticalScrollBar()
    set_values = []
    original = bar.setValue
    def _spy(value):
        set_values.append(value)
        original(value)
    bar.setValue = _spy
    bar.value = lambda: 20
    bar.maximum = lambda: 100

    _last_callback(window.api, "messages", 3)(NetResult(200, data={
        "has_more": False,
        "messages": [{"id": 5, "body": "cinq",
                      "sender": {"name": "Bob", "staff_id": 2}}]}))
    bar.setValue = original

    assert [m["id"] for m in controller.messages] == [5, 10]
    assert not controller._has_more
    # Distance au bas conservée : 100 - (100 - 20) = 20… la formule rend
    # la même valeur quand le maximum simulé ne bouge pas.
    assert set_values[-1] == bar.maximum() - (100 - 20)
    # Le contenu plus ancien est rendu AVANT le message déjà affiché.
    html_out = controller.thread.toHtml()
    assert html_out.index("cinq") < html_out.index("dix")


def test_no_older_request_when_has_more_is_false():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed_with(controller, window.api, [
        {"id": 10, "body": "dix",
         "sender": {"name": "Bob", "staff_id": 2}},
    ], has_more=False)
    calls_before = len([c for c in window.api.calls if c[0] == "messages"])
    controller._maybe_load_older(0)
    assert len([c for c in window.api.calls if c[0] == "messages"]) == \
        calls_before


def test_older_loading_is_locked_while_in_flight():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed_with(controller, window.api, [
        {"id": 10, "body": "dix",
         "sender": {"name": "Bob", "staff_id": 2}},
    ], has_more=True)
    calls_before = len([c for c in window.api.calls if c[0] == "messages"])
    controller._load_older()
    controller._load_older()  # doublon : la page est déjà en vol
    assert len([c for c in window.api.calls if c[0] == "messages"]) == \
        calls_before + 1


def test_older_state_resets_on_tab_change():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    _select(controller, "direct:2")
    _feed_with(controller, window.api, [
        {"id": 10, "body": "dix",
         "sender": {"name": "Bob", "staff_id": 2}},
    ], has_more=True)
    assert controller._has_more
    _select(controller, "broadcast")
    assert not controller._has_more
    assert not controller._older_in_flight
