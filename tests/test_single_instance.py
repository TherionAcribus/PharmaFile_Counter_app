"""Tests de l'instance unique de l'App (single_instance).

Deux instances ouvertes doublaient les raccourcis globaux : un appui sur
« Prochain patient » appelait deux patients. La seconde instance doit donc
renoncer et ramener la première au premier plan.
"""

import os
import re
import subprocess
import sys
import textwrap
import time
import types
import uuid
from unittest import mock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main  # noqa: E402
import single_instance  # noqa: E402
from single_instance import SingleInstance, instance_key  # noqa: E402


@pytest.fixture
def key():
    # Clé unique par test : jamais de collision avec une vraie App ouverte
    # sur le poste, ni entre tests.
    return f"FlowelisTest-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def instances(key, tmp_path):
    created = []

    def make():
        inst = SingleInstance(key=key, lock_dir=str(tmp_path))
        created.append(inst)
        return inst

    yield make
    for inst in created:
        inst.release()


def _spin(predicate, timeout_s=10.0):
    """Fait tourner une vraie boucle d'évènements jusqu'à ce que `predicate`
    soit vrai (la première instance répond depuis sa boucle)."""
    deadline = time.monotonic() + timeout_s
    while not predicate() and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return predicate()


def _run_second_instance(key, lock_dir):
    """Lance une seconde instance dans un AUTRE processus, comme en vrai."""
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {ROOT!r})
        from PySide6.QtCore import QCoreApplication
        app = QCoreApplication([])
        from single_instance import SingleInstance
        inst = SingleInstance(key={key!r}, lock_dir={lock_dir!r})
        acquired = inst.acquire()
        notified = False if acquired else inst.notify_primary(timeout_ms=5000)
        print("acquired=%s notified=%s" % (acquired, notified))
    """)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    return subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env)


# --- clé -------------------------------------------------------------------

def test_key_is_per_user_and_safe_for_a_pipe_name():
    a = instance_key("Pharmacien")
    assert a == instance_key("pharmacien")  # Windows : noms insensibles à la casse
    assert a != instance_key("Préparateur")
    assert re.fullmatch(r"[A-Za-z0-9-]+", instance_key("DOMAINE\\Jean Dupont"))


# --- verrou ----------------------------------------------------------------

def test_second_instance_is_refused_while_first_runs(instances):
    first, second = instances(), instances()
    assert first.acquire() is True
    assert second.acquire() is False


def test_lock_is_free_again_after_release(instances):
    first = instances()
    assert first.acquire()
    first.release()
    assert instances().acquire() is True


def test_lock_left_by_a_crashed_app_is_taken_over(instances, key, tmp_path):
    """Une App plantée laisse son fichier verrou : il ne doit pas empêcher
    l'App de redémarrer (processus disparu ⇒ verrou périmé)."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (tmp_path / f"{key}.lock").write_text(f"{dead.pid}\npython\n{os.environ.get('COMPUTERNAME', '')}\n")
    assert instances().acquire() is True


def test_unusable_lock_directory_does_not_block_startup(key, tmp_path):
    missing = str(tmp_path / "absent" / "encore")
    inst = SingleInstance(key=key, lock_dir=missing)
    try:
        assert inst.acquire() is True
    finally:
        inst.release()


# --- réveil de la première instance ------------------------------------------------

def test_second_process_wakes_up_the_first_and_exits(instances, key, tmp_path):
    first = instances()
    assert first.acquire()
    activations = []
    first.activation_requested.connect(lambda: activations.append(True))

    proc = _run_second_instance(key, str(tmp_path))
    assert _spin(lambda: proc.poll() is not None), "la seconde instance ne s'est pas terminée"
    out, err = proc.communicate()
    assert "acquired=False notified=True" in out, err
    assert activations == [True]


def test_notify_fails_when_nobody_answers(key, tmp_path):
    inst = SingleInstance(key=key, lock_dir=str(tmp_path))
    started = time.monotonic()
    assert inst.notify_primary(timeout_ms=300) is False
    assert time.monotonic() - started < 3


def test_unknown_request_is_ignored(instances):
    first = instances()
    assert first.acquire()
    activations = []
    first.activation_requested.connect(lambda: activations.append(True))
    socket = mock.MagicMock()
    socket.canReadLine.return_value = True
    socket.readLine.return_value = types.SimpleNamespace(data=lambda: b"autre chose\n")
    first._on_ready_read(socket)
    assert activations == []
    socket.write.assert_not_called()


# --- MainWindow.bring_to_front ---------------------------------------------------

def _window_stub(visible=True, minimized=False, loading_visible=False):
    def widget(is_visible, is_minimized=False):
        return types.SimpleNamespace(
            isVisible=mock.MagicMock(return_value=is_visible),
            isMinimized=mock.MagicMock(return_value=is_minimized),
            show=mock.MagicMock(), showNormal=mock.MagicMock(),
            raise_=mock.MagicMock(), activateWindow=mock.MagicMock())
    stub = widget(visible, minimized)
    stub.shutting_down = False
    stub.loading_screen = widget(loading_visible)
    return stub


@pytest.fixture
def no_modal(monkeypatch):
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: None))


def test_bring_to_front_restores_a_minimized_panel(no_modal):
    stub = _window_stub(visible=True, minimized=True)
    main.MainWindow.bring_to_front(stub)
    stub.showNormal.assert_called_once()
    stub.raise_.assert_called_once()
    stub.activateWindow.assert_called_once()


def test_bring_to_front_never_shows_an_empty_window_while_loading(no_modal):
    stub = _window_stub(visible=False, loading_visible=True)
    main.MainWindow.bring_to_front(stub)
    stub.show.assert_not_called()
    stub.loading_screen.raise_.assert_called_once()


def test_bring_to_front_prefers_an_open_dialog(monkeypatch):
    stub = _window_stub(visible=True)
    dialog = _window_stub(visible=True)
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: dialog))
    main.MainWindow.bring_to_front(stub)
    dialog.activateWindow.assert_called_once()
    stub.activateWindow.assert_not_called()


def test_bring_to_front_does_nothing_while_closing(no_modal):
    stub = _window_stub(visible=True)
    stub.shutting_down = True
    main.MainWindow.bring_to_front(stub)
    stub.activateWindow.assert_not_called()


# --- ordre au démarrage (point d'entrée de main.py) ---------------------------

def test_entrypoint_checks_instance_before_anything_else():
    with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
        source = f.read()
    entry = source[source.index('if __name__ == "__main__":'):]
    acquire = entry.index("instance.acquire()")
    # Avant la reprise des réglages et avant toute connexion au serveur.
    assert acquire < entry.index("setup_application(app)")
    assert acquire < entry.index("MainWindow()")
    # Redémarrage volontaire : verrou libéré AVANT de lancer la nouvelle App.
    assert entry.index("instance.release()") < entry.index("ui_scale.relaunch()")


def test_allow_foreground_switch_never_raises():
    single_instance.allow_foreground_switch()
