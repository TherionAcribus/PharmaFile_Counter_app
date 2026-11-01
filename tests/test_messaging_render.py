"""Rendu HTML du fil de messagerie (messaging_render, pur : sans Qt)."""

import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import messaging_render as mr  # noqa: E402

# Dates fixes et heures locales sans fuseau : aucun test ne dépend de
# l'heure à laquelle il tourne (minuit inclus).
TODAY = date(2026, 11, 1)
NOW = datetime(2026, 11, 1, 10, 30)


def _msg(message_id, staff_id, body="texte", name="Bob", minutes_ago=0, **extra):
    message = {"id": message_id, "sender": {"staff_id": staff_id, "name": name},
               "created_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
               "kind": "direct", "body": body}
    message.update(extra)
    return message


def _render(messages, **kw):
    kw.setdefault("my_staff_id", 1)
    kw.setdefault("today", TODAY)
    return mr.render_thread_html(messages, **kw)


def test_empty_thread_has_a_placeholder():
    assert _render([]) == mr.EMPTY_THREAD_HTML


def test_body_and_name_are_escaped():
    out = _render([_msg(1, 2, body="<script>x</script>", name="<b>Bob</b>")])
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&lt;b&gt;Bob&lt;/b&gt;" in out


def test_my_messages_are_on_the_right_with_vous():
    out = _render([_msg(1, 1)])
    assert "text-align:right" in out
    assert "<b>Vous</b>" in out


def test_consecutive_messages_of_the_same_sender_are_grouped():
    out = _render([_msg(1, 2, minutes_ago=2), _msg(2, 2, minutes_ago=1)])
    assert out.count("<b>Bob</b>") == 1
    assert "margin:2px 0 5px" in out


def test_new_messages_marker_only_when_messages_precede_it():
    two = [_msg(1, 2, minutes_ago=2), _msg(2, 3, name="Zoé", minutes_ago=1)]
    assert "Nouveaux messages" in _render(two, marker_id=2)
    assert "Nouveaux messages" not in _render(two, marker_id=1)


def test_read_and_delivered_receipts_are_shown_once():
    read_at = NOW.isoformat()
    out = _render([_msg(1, 1, minutes_ago=2, read_at=read_at), _msg(2, 1, minutes_ago=1)],
                  read_id=1, delivered_id=2)
    assert out.count("Lu à ") == 1
    assert out.count("Remis") == 1


def test_deleted_message_leaves_a_trace_without_content():
    out = _render([_msg(1, 2, body="secret", deleted=True)])
    assert "Message retiré par Bob" in out
    assert "secret" not in out


def test_urgent_message_is_marked():
    out = _render([_msg(1, 2, urgent=True)])
    assert "❗ Bob" in out
    assert "#fee2e2" in out


def test_only_known_mentions_are_highlighted_mine_strongest():
    out = _render([_msg(1, 2, body="@AB et @CD et @ZZ")],
                  known_initials={"ab", "cd"}, self_initials="AB")
    assert 'background:#b45309">@AB' in out
    assert '<b style="color:#b45309">@CD</b>' in out
    assert "@ZZ" in out and "#b45309\">@ZZ" not in out


def test_day_separator_before_older_messages():
    yesterday = _msg(1, 2, minutes_ago=24 * 60)
    out = _render([yesterday, _msg(2, 2)])
    assert "Hier" in out
    assert "Aujourd&#x27;hui" in out  # apostrophe échappée par html.escape


def test_anchor_lets_the_controller_find_a_bubble():
    out = _render([_msg(42, 2)])
    assert f'<a name="{mr.MESSAGE_ANCHOR}42">' in out


def test_day_label_and_time_helpers():
    today = date(2026, 11, 1)
    assert mr.day_label(today, today) == "Aujourd'hui"
    assert mr.day_label(date(2026, 10, 31), today) == "Hier"
    assert mr.day_label(date(2026, 10, 29), today) == "Jeudi 29 octobre"
    assert mr.day_label(date(2025, 10, 29), today) == "29 octobre 2025"
    assert mr.format_time("illisible") == ""
    assert mr.format_time("2026-11-01T09:05:00") == "09:05"
    assert mr.message_day(None) is None
    assert mr.normalize("Hélène") == "helene"
