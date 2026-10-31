"""Phrases rapides « ⚡ » de la messagerie : équipe (admin) + personnelles.

1. concordance des phrases d'origine avec le serveur ;
2. fenêtre « Mes phrases rapides » (sans réseau) ;
3. menu « ⚡ » construit depuis l'état de messagerie, repli serveur ancien ;
4. enregistrement : appel PUT, succès, refus lisible, brouillon conservé.
"""

import ast
from pathlib import Path

import endpoints
from net_result import NetResult
from quick_replies_dialog import QuickRepliesDialog, clean_phrases
from tests.test_messaging import FakeApi, _controller, _select, _state

SERVEUR_QUICK_REPLIES = (Path(__file__).resolve().parents[2]
                         / "Serveur" / "quick_replies.py")


class QuickApi(FakeApi):
    def __init__(self):
        super().__init__()
        self.save_handle = object()

    def messaging_save_quick_replies(self, phrases, on_result=None):
        self.calls.append(("save_quick_replies", list(phrases), on_result))
        return self.save_handle


def _quick_controller():
    window, controller = _controller()
    window.api = QuickApi()
    controller.set_enabled(True)
    return window, controller


def _state_with(team=("Au labo",), personal=("Pause café",), **extra):
    state = _state()
    state["quick_replies"] = {"team": list(team), "personal": list(personal),
                              "max_personal": 12, "max_length": 120, **extra}
    return state


def _menu_labels(controller):
    return [a.text() for a in controller.quick_button.menu().actions()
            if not a.isSeparator() and a.isVisible()]


def _saves(api):
    return [call for call in api.calls if call[0] == "save_quick_replies"]


# --- concordance ----------------------------------------------------------------

def test_default_phrases_match_the_server():
    # Le dépôt GitHub de l'App est publié SEUL (sans le dossier Serveur
    # voisin) : la concordance n'est vérifiable que dans la copie complète.
    if not SERVEUR_QUICK_REPLIES.exists():
        import pytest
        pytest.skip("fichier serveur quick_replies.py absent de cette copie")
    from messaging import MessagingController
    tree =ast.parse(SERVEUR_QUICK_REPLIES.read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", None) == "DEFAULT_TEAM"):
            server_defaults = ast.literal_eval(node.value)
            break
    else:
        raise AssertionError("DEFAULT_TEAM introuvable côté serveur")
    assert tuple(MessagingController.QUICK_REPLIES) == tuple(server_defaults)


def test_endpoint_is_centralized():
    assert endpoints.messaging_quick_replies("https://srv/") == \
        "https://srv/api/messaging/quick_replies"


# --- fenêtre --------------------------------------------------------------------

def test_clean_phrases():
    assert clean_phrases(["  a  b ", "", "A B", "c\nd"]) == ["a b", "c d"]
    assert clean_phrases(["x" * 10], max_length=4) == ["xxxx"]


def test_dialog_add_dedupe_remove_and_move():
    dialog = QuickRepliesDialog(["Un", "Deux"], team_phrases=["J'arrive"],
                                max_count=3, max_length=20)
    assert dialog.phrases() == ["Un", "Deux"]
    assert "J'arrive" in dialog.team_label.text()

    dialog.new_phrase.setText("  deux ")          # doublon (casse ignorée)
    dialog._add_phrase()
    assert dialog.phrases() == ["Un", "Deux"]
    assert dialog.list.currentRow() == 1

    dialog.new_phrase.setText("Trois")
    dialog._add_phrase()
    assert dialog.phrases() == ["Un", "Deux", "Trois"]
    # Plafond atteint : plus d'ajout possible, et c'est annoncé.
    assert not dialog.new_phrase.isEnabled()
    assert "3 / 3" in dialog.count_label.text()

    dialog.list.setCurrentRow(2)
    dialog._move_current(-1)
    assert dialog.phrases() == ["Un", "Trois", "Deux"]
    assert dialog.list.currentRow() == 1
    dialog._remove_current()
    assert dialog.phrases() == ["Un", "Deux"]
    assert dialog.new_phrase.isEnabled()


def test_dialog_respects_server_limits():
    dialog = QuickRepliesDialog(["a", "b", "c"], max_count=2, max_length=5)
    assert dialog.phrases() == ["a", "b"]
    assert dialog.new_phrase.maxLength() == 5


# --- menu « ⚡ » ------------------------------------------------------------------

def test_menu_shows_team_then_personal_phrases_from_state():
    _window, controller = _quick_controller()
    controller._handle_state_result(NetResult(200, data=_state_with(
        team=["Au labo", "En réserve"], personal=["Pause café"])))
    assert _menu_labels(controller) == [
        "Au labo", "En réserve", "Pause café",
        "Gérer mes phrases rapides…", controller.URGENT_ACTION_TEXT,
        "Retirer mon dernier message"]


def test_menu_offers_to_add_when_no_personal_phrase():
    _window, controller = _quick_controller()
    controller._handle_state_result(NetResult(200, data=_state_with(personal=[])))
    assert _menu_labels(controller) == [
        "Au labo", "Ajouter mes phrases rapides…",
        controller.URGENT_ACTION_TEXT, "Retirer mon dernier message"]


def test_old_server_keeps_default_phrases_without_management():
    _window, controller = _quick_controller()
    controller._handle_state_result(NetResult(200, data=_state()))
    assert _menu_labels(controller) == list(controller.QUICK_REPLIES) + [
        controller.URGENT_ACTION_TEXT, "Retirer mon dernier message"]
    assert not controller.quick_replies_supported


def test_menu_phrase_is_inserted_not_sent():
    window, controller = _quick_controller()
    controller._handle_state_result(NetResult(200, data=_state_with()))
    _select(controller, "direct:2")
    action = next(a for a in controller.quick_button.menu().actions()
                  if a.text() == "Pause café")
    action.trigger()
    assert controller.input.toPlainText() == "Pause café"
    assert not [c for c in window.api.calls if c[0] == "send"]


def test_identity_change_drops_previous_personal_phrases():
    _window, controller = _quick_controller()
    controller._handle_state_result(NetResult(200, data=_state_with()))
    controller.set_identity(2, "Bob")
    assert controller.personal_replies == []
    assert "Pause café" not in _menu_labels(controller)


def test_delete_action_survives_menu_rebuilds():
    # La confirmation en deux temps du retrait vit sur l'action elle-même :
    # reconstruire les phrases ne doit pas la recréer.
    _window, controller = _quick_controller()
    delete_action = controller._delete_action
    controller._handle_state_result(NetResult(200, data=_state_with()))
    controller._handle_state_result(NetResult(200, data=_state_with(team=["Autre"])))
    assert controller._delete_action is delete_action
    assert delete_action in controller.quick_button.menu().actions()


# --- enregistrement -------------------------------------------------------------

def test_save_success_updates_menu_and_status():
    window, controller = _quick_controller()
    controller._handle_state_result(NetResult(200, data=_state_with()))
    _select(controller, "direct:2")
    controller.save_personal_quick_replies(["Je reviens", "Pause café"])
    call = _saves(window.api)[-1]
    assert call[1] == ["Je reviens", "Pause café"]
    call[2](NetResult(200, data={"team": ["Au labo"],
                                 "personal": ["Je reviens", "Pause café"]}))
    assert controller.personal_replies == ["Je reviens", "Pause café"]
    assert "Je reviens" in _menu_labels(controller)
    assert controller.status_label.text() == "Vos phrases rapides sont enregistrées."
    assert controller._unsaved_personal is None


def test_save_refusal_shows_server_message_and_keeps_draft(monkeypatch):
    window, controller = _quick_controller()
    controller._handle_state_result(NetResult(200, data=_state_with()))
    _select(controller, "direct:2")
    controller.save_personal_quick_replies(["Trop longue"])
    _saves(window.api)[-1][2](NetResult(400, data={
        "error": "invalid_quick_replies", "message": "12 phrases au maximum."}))
    assert controller.status_label.text() == "12 phrases au maximum."
    assert controller.personal_replies == ["Pause café"]
    assert controller._unsaved_personal == ["Trop longue"]

    # À la réouverture, la fenêtre repropose la liste non enregistrée.
    seen = {}

    def fake_exec(dialog):
        seen["phrases"] = dialog.phrases()
        return QuickRepliesDialog.DialogCode.Rejected

    monkeypatch.setattr(QuickRepliesDialog, "exec", fake_exec)
    controller.open_quick_replies_dialog()
    assert seen["phrases"] == ["Trop longue"]


def test_dialog_validation_triggers_save(monkeypatch):
    window, controller = _quick_controller()
    controller._handle_state_result(NetResult(200, data=_state_with()))

    def fake_exec(dialog):
        dialog.new_phrase.setText("Au téléphone")
        dialog._add_phrase()
        return QuickRepliesDialog.DialogCode.Accepted

    monkeypatch.setattr(QuickRepliesDialog, "exec", fake_exec)
    controller.open_quick_replies_dialog()
    assert _saves(window.api)[-1][1] == ["Pause café", "Au téléphone"]


def test_dialog_unavailable_with_old_server(monkeypatch):
    window, controller = _quick_controller()
    controller._handle_state_result(NetResult(200, data=_state()))
    opened = []
    monkeypatch.setattr(QuickRepliesDialog, "exec",
                        lambda dialog: opened.append(True))
    controller.open_quick_replies_dialog()
    assert not opened
    assert not _saves(window.api)
