"""Tests d'intégration réels du gestionnaire réseau (connections.NetworkManager).

Exécutés avec le vrai PySide6 (venv App_Comptoir) : worker QThread + file + signaux.
On remplace la ``requests.Session`` interne par une fausse session pour ne pas
faire d'I/O réseau, tout en exerçant la vraie mécanique (file, worker, 401/rejeu,
timeout par requête, purge à l'arrêt, obtention de jeton, NetResult).
"""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

import connections  # noqa: E402
from connections import NetworkManager, DEFAULT_TIMEOUT, _Job, _RequestSpec  # noqa: E402
from net_result import NetResult  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


class FakeResp:
    def __init__(self, status, text="", json_data=None, content_type="application/json"):
        self.status_code = status
        self.text = text
        self.headers = {"Content-Type": content_type} if content_type else {}
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("corps non JSON")
        return self._json


class FakeSession:
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


def test_request_blocking_returns_netresult(mgr_factory):
    m = mgr_factory(FakeSession(get_responses=[FakeResp(200, '{"ok": true}')]))
    res = m.request_blocking("http://srv/a", timeout_s=5)
    assert isinstance(res, NetResult)
    assert res.status == 200 and res.success is True
    assert res.data == {"ok": True}   # décodé car content-type JSON


def test_html_response_does_not_crash_and_data_is_none(mgr_factory):
    m = mgr_factory(FakeSession(get_responses=[
        FakeResp(200, "<html>oops</html>", content_type="text/html")]))
    res = m.request_blocking("http://srv/a", timeout_s=5)
    assert res.status == 200
    assert res.data is None            # pas de décodage d'un corps HTML


def test_401_triggers_single_reauth_and_one_retry(mgr_factory):
    m = mgr_factory(FakeSession(
        get_responses=[FakeResp(401, "no"), FakeResp(200, '{"ok": 1}')],
        post_responses=[FakeResp(200, json_data={"token": "newtok"})],  # /token
    ))
    res = m.request_blocking("http://srv/a", timeout_s=5)
    assert res.status == 200
    gets = [c for c in m._session.calls if c[0] == "GET"]
    posts = [c for c in m._session.calls if c[0] == "POST"]
    assert len(gets) == 2      # 1 essai + 1 rejeu
    assert len(posts) == 1     # un seul renouvellement de jeton
    assert m.current_token() == "newtok"


def test_timeout_override_reaches_session(mgr_factory):
    m = mgr_factory(FakeSession(get_responses=[FakeResp(200, "ok"), FakeResp(200, "ok")]))
    m.request_blocking("http://srv/a", timeout=(1, 2), timeout_s=5)
    assert m._session.calls[0][2] == (1, 2)
    m.request_blocking("http://srv/a", timeout_s=5)
    assert m._session.calls[1][2] == DEFAULT_TIMEOUT


def test_idempotency_key_added_as_header(mgr_factory):
    m = mgr_factory(FakeSession(post_responses=[FakeResp(200, "ok")]))
    m.request_blocking("http://srv/a", method="POST", data={"x": 1},
                       idempotency_key="abc-123", timeout_s=5)
    post = [c for c in m._session.calls if c[0] == "POST"][0]
    assert post[4].get("X-Idempotency-Key") == "abc-123"


def test_idempotency_key_preserved_across_401_retry(mgr_factory):
    # Une action POST idempotente prend un 401, déclenche le renouvellement du
    # jeton, puis est rejouée : le rejeu DOIT réutiliser exactement la même clé
    # d'idempotence (sinon le serveur exécuterait l'action deux fois).
    m = mgr_factory(FakeSession(post_responses=[
        FakeResp(401, "expired"),                       # 1er envoi de l'action
        FakeResp(200, json_data={"token": "newtok"}),   # renouvellement du jeton
        FakeResp(200, "ok"),                             # rejeu de l'action
    ]))
    m.request_blocking("http://srv/act", method="POST", data={"x": 1},
                       idempotency_key="same-key", timeout_s=5)

    target_posts = [c for c in m._session.calls
                    if c[0] == "POST" and c[1] == "http://srv/act"]
    token_posts = [c for c in m._session.calls
                   if c[0] == "POST" and c[1] == "http://srv/token"]

    assert len(target_posts) == 2          # 1 envoi + 1 rejeu après 401
    # c[4] = en-têtes de la requête POST (cf. FakeSession.post).
    keys = {(c[4] or {}).get("X-Idempotency-Key") for c in target_posts}
    assert keys == {"same-key"}            # MÊME clé sur les deux envois
    assert len(token_posts) == 1
    assert (token_posts[0][4] or {}).get("X-Idempotency-Key") is None  # jamais sur le renouvellement


def test_network_error_uniform_netresult(mgr_factory):
    class BoomSession(FakeSession):
        def get(self, url, headers=None, timeout=None):
            raise connections.RequestException("boom")
    m = mgr_factory(BoomSession())
    res = m.request_blocking("http://srv/a", timeout_s=5)
    assert res.status == 0 and res.is_timeout is True
    assert "boom" in res.detail
    assert res.message  # message utilisateur non vide


def test_distinct_user_messages_per_status(mgr_factory):
    cases = {401: "http://srv/401", 403: "http://srv/403", 423: "http://srv/423",
             500: "http://srv/500"}
    m = mgr_factory(FakeSession(get_responses=[
        FakeResp(401, "a"), FakeResp(200, "{}"),  # 401 -> reauth -> re-send (200)
    ], post_responses=[FakeResp(200, json_data={"token": "t"})]))
    # 401 aboutit à un rejeu ; on teste plutôt le mapping directement :
    from net_result import user_message_for_status
    msgs = {s: user_message_for_status(s) for s in (0, 401, 403, 409, 423, 500)}
    assert len({msgs[401], msgs[403], msgs[423], msgs[500], msgs[0]}) == 5  # tous distincts
    assert msgs[409] == msgs[423]  # 409 et 423 partagent le même message


def test_fetch_token_blocking_sets_session_header_and_returns_token(mgr_factory):
    m = mgr_factory(FakeSession(post_responses=[FakeResp(200, json_data={"token": "TOK42"})]))
    assert m.fetch_token_blocking(timeout_s=5) == "TOK42"
    assert m.current_token() == "TOK42"


def test_fetch_token_blocking_failure_clears_token(mgr_factory):
    m = mgr_factory(FakeSession(post_responses=[FakeResp(401, "denied")]))
    m._session.headers["X-App-Token"] = "old"
    assert m.fetch_token_blocking(timeout_s=5) is None
    assert m.current_token() is None


def test_async_handle_emits_netresult_then_finished(mgr_factory):
    m = mgr_factory(FakeSession(get_responses=[FakeResp(200, '{"v": 1}')]))
    events = []
    h = m.make_handle("http://srv/a", method="GET")
    loop = QEventLoop()
    h.result.connect(lambda r: events.append(("result", r.status, r.data)))
    h.finished.connect(lambda: (events.append(("finished",)), loop.quit()))
    h.start()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    assert ("result", 200, {"v": 1}) in events
    assert ("finished",) in events


def test_drain_pending_unblocks_waiting_jobs(mgr_factory):
    m = mgr_factory(FakeSession())
    m.stop()
    job = _Job("request", spec=_RequestSpec("u", "GET", None, None, None),
               event=threading.Event())
    m._queue.put(job)
    m._drain_pending()
    assert job.event.is_set()
    res = job.result_box["result"]
    assert isinstance(res, NetResult) and res.status == 0


def test_stop_is_idempotent(mgr_factory):
    m = mgr_factory(FakeSession())
    assert m.stop() in (True, False)
    assert m.stop() in (True, False)


def test_clear_token_removes_session_header(mgr_factory):
    m = mgr_factory(FakeSession(post_responses=[FakeResp(200, json_data={"token": "TOK"})]))
    m.fetch_token_blocking(timeout_s=5)
    assert m.current_token() == "TOK"
    m.clear_token()   # ex: changement de serveur/secret
    assert m.current_token() is None
