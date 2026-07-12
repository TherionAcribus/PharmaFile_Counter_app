"""Tests d'intégration réels du gestionnaire réseau (connections.NetworkManager).

Exécutés avec le vrai PySide6 (venv App_Comptoir) : worker QThread + file + signaux.
On remplace la ``requests.Session`` interne par une fausse session pour ne pas
faire d'I/O réseau, tout en exerçant la vraie mécanique (file, worker, 401/rejeu,
timeout par requête, purge à l'arrêt, obtention de jeton).
"""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

import connections  # noqa: E402
from connections import NetworkManager, DEFAULT_TIMEOUT, _Job, _RequestSpec  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


class FakeResp:
    def __init__(self, status, text="", json_data=None):
        self.status_code = status
        self.text = text
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("corps non JSON")
        return self._json


class FakeSession:
    """Imite requests.Session : enregistre les appels et rend des réponses
    programmées. ``get_responses``/``post_responses`` sont des listes consommées."""

    def __init__(self, get_responses=None, post_responses=None):
        self.headers = {}
        self.calls = []
        self._get = list(get_responses or [FakeResp(200, "ok")])
        self._post = list(post_responses or [FakeResp(200, "ok")])

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, timeout, headers))
        return self._get.pop(0)

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, timeout, data, headers))
        return self._post.pop(0)


@pytest.fixture
def mgr_factory(qapp):
    created = []

    def _make(session=None, token_url="http://srv/token", secret="s3cret"):
        m = NetworkManager(lambda: token_url, lambda: secret)
        if session is not None:
            m._session = session
        created.append(m)
        return m

    yield _make
    for m in created:
        m.stop()


def test_request_blocking_success(mgr_factory):
    m = mgr_factory(FakeSession(get_responses=[FakeResp(200, "ok")]))
    elapsed, text, status = m.request_blocking("http://srv/a", timeout_s=5)
    assert (text, status) == ("ok", 200)
    assert elapsed >= 0.0


def test_401_triggers_single_reauth_and_one_retry(mgr_factory):
    # 1er envoi 401 -> renouvellement du jeton -> rejeu unique 200.
    m = mgr_factory(FakeSession(
        get_responses=[FakeResp(401, "no"), FakeResp(200, "ok")],
        post_responses=[FakeResp(200, json_data={"token": "newtok"})],  # /token
    ))
    _e, text, status = m.request_blocking("http://srv/a", timeout_s=5)
    assert (text, status) == ("ok", 200)
    gets = [c for c in m._session.calls if c[0] == "GET"]
    posts = [c for c in m._session.calls if c[0] == "POST"]
    assert len(gets) == 2      # 1 essai + 1 rejeu
    assert len(posts) == 1     # un seul renouvellement de jeton
    assert m.current_token() == "newtok"


def test_timeout_override_reaches_session(mgr_factory):
    m = mgr_factory(FakeSession(get_responses=[FakeResp(200, "ok"), FakeResp(200, "ok")]))
    m.request_blocking("http://srv/a", timeout=(1, 2), timeout_s=5)
    assert m._session.calls[0][2] == (1, 2)
    # Sans surcharge -> timeout par défaut.
    m.request_blocking("http://srv/a", timeout_s=5)
    assert m._session.calls[1][2] == DEFAULT_TIMEOUT


def test_idempotency_key_added_as_header(mgr_factory):
    m = mgr_factory(FakeSession(post_responses=[FakeResp(200, "ok")]))
    m.request_blocking("http://srv/a", method="POST", data={"x": 1},
                       idempotency_key="abc-123", timeout_s=5)
    post = [c for c in m._session.calls if c[0] == "POST"][0]
    headers = post[4]
    assert headers.get("X-Idempotency-Key") == "abc-123"


def test_network_error_uniform_format(mgr_factory):
    class BoomSession(FakeSession):
        def get(self, url, headers=None, timeout=None):
            raise connections.RequestException("boom")
    m = mgr_factory(BoomSession())
    elapsed, text, status = m.request_blocking("http://srv/a", timeout_s=5)
    assert status == 0
    assert "boom" in text


def test_fetch_token_blocking_sets_session_header_and_returns_token(mgr_factory):
    m = mgr_factory(FakeSession(post_responses=[FakeResp(200, json_data={"token": "TOK42"})]))
    tok = m.fetch_token_blocking(timeout_s=5)
    assert tok == "TOK42"
    assert m.current_token() == "TOK42"


def test_fetch_token_blocking_failure_clears_token(mgr_factory):
    m = mgr_factory(FakeSession(post_responses=[FakeResp(401, "denied")]))
    m._session.headers["X-App-Token"] = "old"
    tok = m.fetch_token_blocking(timeout_s=5)
    assert tok is None
    assert m.current_token() is None


def test_async_handle_emits_result_then_finished(mgr_factory):
    m = mgr_factory(FakeSession(get_responses=[FakeResp(200, "ok")]))
    events = []
    h = m.make_handle("http://srv/a", method="GET")
    loop = QEventLoop()
    h.result.connect(lambda e, t, s: events.append(("result", t, s)))
    h.finished.connect(lambda: (events.append(("finished",)), loop.quit()))
    h.start()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    assert ("result", "ok", 200) in events
    assert ("finished",) in events


def test_drain_pending_unblocks_waiting_jobs(mgr_factory):
    m = mgr_factory(FakeSession())
    m.stop()  # arrête le worker : on contrôle la file à la main
    job = _Job("request", spec=_RequestSpec("u", "GET", None, None, None),
               event=threading.Event())
    m._queue.put(job)
    m._drain_pending()  # simule la purge faite par le worker sur _STOP
    assert job.event.is_set()
    assert job.result_box["result"][2] == 0  # status 0 = échec "arrêt en cours"


def test_stop_is_idempotent(mgr_factory):
    m = mgr_factory(FakeSession())
    assert m.stop() in (True, False)   # 1er arrêt
    assert m.stop() in (True, False)   # 2e arrêt : ne lève pas, ne bloque pas
