"""Interface et cycle de vie de la messagerie PySide."""

from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QMenu, QWidget

from messaging import MessagingController
from net_result import NetResult


class FakeApi:
    def __init__(self):
        self.calls = []

    def messaging_presence(self, instance, on_result=None):
        self.calls.append(("presence", instance, on_result))

    def messaging_leave(self, instance, on_result=None):
        self.calls.append(("leave", instance, on_result))

    def messaging_leave_blocking(self, instance):
        self.calls.append(("leave_blocking", instance))
        return True

    def messaging_state(self, on_result=None):
        self.calls.append(("state", on_result))

    def messaging_messages(self, kind, peer_staff_id=None, before_id=None,
                           after_id=None, on_result=None):
        self.calls.append(("messages", kind, peer_staff_id, on_result))

    def messaging_send(self, client_id, kind, body, recipient_id=None, on_result=None):
        self.calls.append(("send", kind, body, recipient_id, on_result))

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
             "unread_count": unread},
        ],
    }


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
    assert len(window.more_menu.actions()) == 1


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


def test_offline_contact_remains_visible_but_cannot_receive_message():
    _window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state(online=False)))
    controller.selector.setCurrentIndex(controller.selector.findData("direct:2"))
    assert "hors ligne" in controller.selector.currentText()
    assert not controller.input.isEnabled()
    assert "hors ligne" in controller.status_label.text()


def test_direct_send_uses_selected_staff_and_clears_after_success():
    window, controller = _controller()
    controller.set_enabled(True)
    controller._handle_state_result(NetResult(200, data=_state()))
    controller.selector.setCurrentIndex(controller.selector.findData("direct:2"))
    controller.input.setPlainText("Bonjour Bob")
    controller.send()
    call = next(call for call in window.api.calls if call[0] == "send")
    assert call[1:4] == ("direct", "Bonjour Bob", 2)
    call[4](NetResult(201, data={"id": 1}))
    assert controller.input.toPlainText() == ""


def test_mark_read_only_when_dock_is_visible():
    window, controller = _controller()
    controller.set_enabled(True)
    window.show()
    controller.messages = [{"id": 4, "is_unread": True}]
    controller.dock.hide()
    controller._mark_visible_read()
    assert not any(call[0] == "read" for call in window.api.calls)
    controller.dock.show()
    controller._mark_visible_read()
    assert any(call[:2] == ("read", [4]) for call in window.api.calls)


def test_socket_disable_removes_every_entry_point():
    _window, controller = _controller()
    controller.set_enabled(True)
    controller.socket_config_changed(False)
    assert not controller.enabled
    assert controller.button is None
    assert controller.dock is None
