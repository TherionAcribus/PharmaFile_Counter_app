"""Interface et cycle de vie de la messagerie PySide."""

import uuid

import pytest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QHBoxLayout, QMainWindow, QMenu, QTabBar, QWidget,
)

from messaging import (
    MessagingController, _NAME_ROLE, _RecipientFilterModel,
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
        self.calls.append(("messages", kind, peer_staff_id, on_result))

    def messaging_send(self, client_id, kind, body, recipient_id=None,
                       scope=None, on_result=None):
        # on_result reste en position 4 (les tests existants appellent
        # call[4](NetResult(...))) ; la portée broadcast est en position 5.
        self.calls.append(("send", kind, body, recipient_id, on_result, scope))

    def messaging_read(self, ids, on_result=None):
        self.calls.append(("read", list(ids), on_result))


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
