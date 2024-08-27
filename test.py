import pytest
from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication
import sys
from main import MainWindow 

@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    yield app
    app.quit()

@pytest.fixture
def main_window(qapp):
    window = MainWindow()
    window.update_control_buttons_layout = MagicMock()
    window.resize_to_fit_buttons = MagicMock()
    return window

def test_toggle_orientation_changes_vertical_mode(main_window):
    initial_vertical_mode = main_window.vertical_mode
    main_window.toggle_orientation()
    assert initial_vertical_mode != main_window.vertical_mode

def test_toggle_orientation_calls_update_methods(main_window):
    main_window.toggle_orientation()
    main_window.update_control_buttons_layout.assert_called_once()
    main_window.resize_to_fit_buttons.assert_called_once()

def test_toggle_orientation_twice_returns_to_original_state(main_window):
    initial_vertical_mode = main_window.vertical_mode
    main_window.toggle_orientation()
    main_window.toggle_orientation()
    assert initial_vertical_mode == main_window.vertical_mode
