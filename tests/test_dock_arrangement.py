"""Disposition file des patients + messagerie (dock_arrangement).

Problème d'origine : mettre la messagerie et la liste des patients l'une
au-dessus de l'autre par glisser-déposer était aussitôt « corrigé » par
l'application (ré-onglettage en compact, ré-empilement forcé sinon), pendant
que Qt posait encore le panneau — les contenus finissaient chevauchés dans une
même zone. Désormais le choix de l'utilisateur est respecté et mémorisé.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import dock_arrangement as da  # noqa: E402
import main  # noqa: E402
import messaging  # noqa: E402
from test_main_window import _make_main_window  # noqa: E402
from test_messaging import FakeSettings  # noqa: E402


# --- cœur pur -----------------------------------------------------------------

def test_normalize_layout_falls_back_to_auto():
    assert da.normalize_layout("tabs") == da.TABS
    assert da.normalize_layout("n'importe quoi") == da.AUTO
    assert da.normalize_layout(None) == da.AUTO


def test_auto_layout_keeps_historical_defaults():
    assert da.effective_layout(da.AUTO, compact=True) == da.TABS
    assert da.effective_layout(da.AUTO, compact=False) == da.STACKED_MESSAGING_FIRST
    # Un choix explicite prime sur le mode.
    assert da.effective_layout(da.STACKED_PATIENTS_FIRST, compact=True) == \
        da.STACKED_PATIENTS_FIRST
    assert da.effective_layout(da.TABS, compact=False) == da.TABS


def test_observed_layout_reads_order_top_then_left():
    assert da.observed_layout(True, (0, 0), (0, 0)) == da.TABS
    assert da.observed_layout(False, (0, 10), (0, 200)) == da.STACKED_MESSAGING_FIRST
    assert da.observed_layout(False, (0, 200), (0, 10)) == da.STACKED_PATIENTS_FIRST
    # Côte à côte : celui de gauche est en tête.
    assert da.observed_layout(False, (300, 10), (0, 10)) == da.STACKED_PATIENTS_FIRST


def test_area_name():
    assert da.area_name(Qt.BottomDockWidgetArea) == "bottom"
    assert da.area_name(Qt.RightDockWidgetArea) == "right"
    assert da.area_name(Qt.LeftDockWidgetArea) is None


# --- intégration fenêtre principale ---------------------------------------------

@pytest.fixture
def settings():
    previous = dict(FakeSettings.values)
    FakeSettings.values.clear()
    FakeSettings.values["messaging_dock_visible"] = True
    yield FakeSettings.values
    FakeSettings.values.clear()
    FakeSettings.values.update(previous)


def _settle():
    for _ in range(5):
        QApplication.processEvents()


def _window(compact=False):
    win = _make_main_window(compact_mode=compact)
    win.display_patient_list = True
    win.create_interface()
    win.messaging = messaging.MessagingController(win, settings_factory=FakeSettings)
    win.messaging.set_identity(1, "Alice")
    win.messaging.set_enabled(True)
    win.show()
    _settle()
    return win, win.messaging.dock, win.patient_list_dock


def _tabbed(win, a, b):
    return b in win.tabifiedDockWidgets(a)


def _no_overlap(a, b):
    return not a.geometry().intersects(b.geometry())


def test_patient_dock_has_stable_object_name(settings):
    win, _msg, pat = _window()
    try:
        assert pat.objectName() == "patientListDock"
        assert win.dockOptions() & win.DockOption.AllowNestedDocks
    finally:
        win.hide()
        win.deleteLater()


@pytest.mark.parametrize("compact", [False, True])
def test_user_stacking_is_kept_and_remembered(settings, compact):
    # L'utilisateur pose la liste des patients AU-DESSUS de la messagerie.
    win, msg, pat = _window(compact)
    try:
        win.addDockWidget(Qt.BottomDockWidgetArea, msg)
        win.splitDockWidget(pat, msg, Qt.Vertical)
        _settle()
        win.messaging.arranger.capture()   # fin du délai après le dépôt
        _settle()

        # Rien n'a été « corrigé » : pas d'onglets, pas de chevauchement.
        assert not _tabbed(win, msg, pat)
        assert _no_overlap(msg, pat)
        assert pat.geometry().top() < msg.geometry().top()
        assert settings[da.LAYOUT_KEY] == da.STACKED_PATIENTS_FIRST

        # Une reconstruction de l'interface (orientation, préférences…)
        # garde la disposition.
        win.create_interface()
        _settle()
        assert not _tabbed(win, msg, pat)
        assert _no_overlap(msg, pat)
        assert pat.geometry().top() < msg.geometry().top()
    finally:
        win.hide()
        win.deleteLater()


def test_user_tabbing_in_extended_mode_is_kept(settings):
    win, msg, pat = _window(compact=False)
    try:
        assert not _tabbed(win, msg, pat)
        win.tabifyDockWidget(pat, msg)
        _settle()
        win.messaging.arranger.capture()
        _settle()
        assert _tabbed(win, msg, pat)
        assert settings[da.LAYOUT_KEY] == da.TABS
        win.create_interface()
        _settle()
        assert _tabbed(win, msg, pat)
    finally:
        win.hide()
        win.deleteLater()


@pytest.mark.parametrize("layout", [
    da.STACKED_MESSAGING_FIRST, da.STACKED_PATIENTS_FIRST, da.TABS, da.AUTO])
@pytest.mark.parametrize("compact", [False, True])
def test_menu_layout_choice_never_overlaps(settings, layout, compact):
    win, msg, pat = _window(compact)
    try:
        # En partant d'onglets comme d'un empilement.
        for start in (da.TABS, da.STACKED_MESSAGING_FIRST):
            win.messaging.arranger.set_layout(start)
            _settle()
            win.messaging.arranger.set_layout(layout)
            _settle()
            expected = da.effective_layout(layout, compact)
            assert _tabbed(win, msg, pat) == (expected == da.TABS)
            assert not msg.isHidden() and not pat.isHidden()
            if expected != da.TABS:
                assert _no_overlap(msg, pat)
                first, second = ((msg, pat) if expected == da.STACKED_MESSAGING_FIRST
                                 else (pat, msg))
                assert first.geometry().top() < second.geometry().top()
    finally:
        win.hide()
        win.deleteLater()


def test_menu_lists_layouts_and_checks_the_stored_one(settings):
    win, _msg, _pat = _window()
    try:
        submenus = [a.menu() for a in win.more_menu.actions() if a.menu()]
        assert len(submenus) == 1
        labels = [a.text() for a in submenus[0].actions()]
        assert labels == [label for _v, label in da.LAYOUT_LABELS]
        win.messaging.arranger.set_layout(da.TABS)
        checked = [a.text() for a in submenus[0].actions() if a.isChecked()]
        assert checked == [dict(da.LAYOUT_LABELS)[da.TABS]]
    finally:
        win.hide()
        win.deleteLater()


def test_toggle_brings_background_tab_to_front_instead_of_hiding(settings):
    win, msg, pat = _window(compact=True)
    try:
        pat.raise_()
        _settle()
        assert not da.dock_in_front(msg)

        win.messaging.toggle()            # derrière → devant
        _settle()
        assert not msg.isHidden() and da.dock_in_front(msg)

        main.MainWindow.toggle_patient_list(win)   # idem pour la file
        _settle()
        assert not pat.isHidden() and da.dock_in_front(pat)

        main.MainWindow.toggle_patient_list(win)   # au premier plan → masque
        _settle()
        assert pat.isHidden()
    finally:
        win.hide()
        win.deleteLater()


def test_switching_tabs_does_not_close_messaging_for_next_session(settings):
    win, msg, pat = _window(compact=True)
    try:
        msg.raise_()
        _settle()
        pat.raise_()                      # l'utilisateur consulte la file
        _settle()
        assert settings["messaging_dock_visible"] is True
    finally:
        win.hide()
        win.deleteLater()


def test_background_tab_does_not_acknowledge_messages(settings):
    win, msg, pat = _window(compact=True)
    try:
        pat.raise_()
        _settle()
        win.api.reset_mock()
        win.messaging.messages = [{"id": 7, "is_unread": True}]
        win.messaging._mark_visible_read()
        win.api.messaging_read.assert_not_called()

        msg.raise_()                      # l'onglet passe devant : acquitté
        _settle()
        win.api.messaging_read.assert_called()
        assert win.api.messaging_read.call_args.args[0] == [7]
    finally:
        win.hide()
        win.deleteLater()


def test_logout_does_not_persist_messaging_as_closed(settings):
    win, msg, _pat = _window()
    try:
        assert msg.isVisible()
        win.messaging.clear_identity(notify_server=False)
        _settle()
        assert settings["messaging_dock_visible"] is True
    finally:
        win.hide()
        win.deleteLater()


def test_dragging_patient_list_updates_its_position_preference(settings):
    win, _msg, pat = _window(compact=False)
    try:
        win.addDockWidget(Qt.RightDockWidgetArea, pat)
        _settle()
        win.messaging.arranger.capture()
        assert settings["patient_list_vertical_position"] == "right"
        assert win.patient_list_position_vertical == "right"
        # La reconstruction ne la ramène pas en bas.
        win.create_interface()
        _settle()
        assert win.dockWidgetArea(pat) == Qt.RightDockWidgetArea
    finally:
        win.hide()
        win.deleteLater()
