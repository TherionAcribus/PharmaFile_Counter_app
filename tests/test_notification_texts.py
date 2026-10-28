"""Textes des notifications réglables dans l'administration du serveur.

Couvre :
1. le module pur ``notification_texts`` (repli sur le texte d'origine,
   remplacement des balises) ;
2. l'application de l'état du comptoir (``notification_texts`` reçu ou
   absent pour un serveur ancien) ;
3. quelques points d'appel réels de ``MainWindow`` (vraies méthodes, faux
   self) : le texte réglé est bien celui affiché ;
4. la concordance des textes d'origine avec le catalogue du serveur.
"""

import ast
import logging
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import notification_texts as nt  # noqa: E402

SERVEUR_CATALOGUE = (Path(__file__).resolve().parents[2]
                     / "Serveur" / "app_notification_texts.py")


# --- Module pur -------------------------------------------------------------------

def test_repli_sur_le_texte_d_origine():
    assert nt.render(None, "server_unreachable") == "Le serveur est inaccessible."
    assert nt.render({}, "server_unreachable") == "Le serveur est inaccessible."
    # Vide, blanc ou illisible : jamais de notification vide.
    for bad in ("", "   ", None, 12, ["x"]):
        assert nt.render({"server_unreachable": bad}, "server_unreachable") == \
            "Le serveur est inaccessible."


def test_texte_regle_et_balises():
    texts = {"new_patient": "Au comptoir : {N} ({A}) {X}"}
    assert nt.render(texts, "new_patient", N="O-12", A="Ordonnance") == \
        "Au comptoir : O-12 (Ordonnance) {X}"
    # Balise en minuscules acceptée, valeur absente laissée telle quelle.
    assert nt.render({"please_validate": "Valider {n} !"}, "please_validate", N=3) == "Valider 3 !"
    assert nt.render({"please_validate": "Valider {N}"}, "please_validate") == "Valider {N}"
    assert nt.render({}, "auto_release_warning", S=42).startswith(
        "Le patient sera retiré automatiquement de l'écran d'appel dans 42 secondes")


def test_deconnexion_par_un_autre_poste_corrigee():
    # Ancien texte : « Vous avez déconnecté par … ».
    assert nt.render({}, "disconnected_by_user", M="Admin") == "Vous avez été déconnecté par Admin."


# --- Application de l'état --------------------------------------------------------

def _state_window():
    return types.SimpleNamespace(queue_revision=0, notification_texts={"x": "y"})


def test_etat_applique_les_textes_recus():
    w = _state_window()
    main.MainWindow._apply_state(w, {"notification_texts": {"action_busy": "Patience…"}})
    assert w.notification_texts == {"action_busy": "Patience…"}
    assert main._notification_text(w, "action_busy") == "Patience…"


def test_serveur_ancien_textes_d_origine():
    w = _state_window()
    main.MainWindow._apply_state(w, {})
    assert w.notification_texts == {}
    main.MainWindow._apply_state(w, {"notification_texts": "pas un dict"})
    assert w.notification_texts == {}
    assert main._notification_text(w, "action_busy") == "Une action est déjà en cours…"


# --- Points d'appel réels -----------------------------------------------------------

def _notif_window(**texts):
    w = types.SimpleNamespace(
        notification_texts=texts,
        logger=logging.getLogger("test.notification_texts"),
        shown=[],
    )
    w.show_notification = lambda data, internal=False, **kw: w.shown.append(data)
    return w


def test_deconnexion_par_un_autre_poste_utilise_le_texte_regle():
    w = _notif_window(disconnected_by_user="{M} vous a retiré du comptoir")
    w.deconnexion_interface = lambda: None
    main.MainWindow.disconnect_user(w, {"data": {"staff": "Marie"}})
    assert w.shown == [{"origin": "disconnect_by_user", "message": "Marie vous a retiré du comptoir"}]


def test_serveur_inaccessible_utilise_le_texte_regle():
    w = _notif_window(server_unreachable="Pas de serveur, prévenez l'informatique.")
    w.connected = False
    main.MainWindow.alert_if_not_connected(w)
    assert w.shown[0]["message"] == "Pas de serveur, prévenez l'informatique."


def test_rappel_de_validation_avec_numero():
    w = _notif_window(please_validate="Validez {N} !")
    w.my_patient = {"id": 5, "call_number": "C-4", "status": "calling"}
    w.patient_id = 5
    w.call_auto_validation = {"enabled": False}
    w._set_validate_alert = lambda value: None
    main.MainWindow.call_timer_delay_expired(w)
    assert w.shown[0] == {"origin": "please_validate", "message": "Validez C-4 !"}


def test_papier_selon_l_etat():
    w = _notif_window(low_paper="Rouleau presque vide", paper_ok="Papier OK")
    w.btn_paper = types.SimpleNamespace(update_button_icon=lambda state: None)
    main.MainWindow.change_paper(w, {"data": {"add_paper": True}})
    main.MainWindow.change_paper(w, {"data": {"add_paper": False}})
    assert [d["message"] for d in w.shown] == ["Rouleau presque vide", "Papier OK"]


# --- Concordance avec le serveur ------------------------------------------------------

def _server_defaults():
    """Textes d'origine du catalogue serveur, lus sans l'importer (dépôt
    voisin, environnement Python distinct)."""
    tree = ast.parse(SERVEUR_CATALOGUE.read_text(encoding="utf-8"))
    defaults = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "NotificationText"):
            message_id = ast.literal_eval(node.args[0])
            default = ast.literal_eval(node.args[4])
            # 7e argument : constante nommée APP / SERVER / BOTH (APP si absent).
            rendered_by = node.args[6].id if len(node.args) > 6 else "APP"
            defaults[message_id] = (default, rendered_by)
    return defaults


def test_textes_d_origine_identiques_au_serveur():
    if not SERVEUR_CATALOGUE.exists():
        import pytest
        pytest.skip("catalogue serveur absent de cette copie")
    server = _server_defaults()
    assert server, "catalogue serveur illisible"
    for message_id, text in nt.DEFAULTS.items():
        assert message_id in server, message_id
        assert server[message_id][0] == text, message_id
    # Tout message que le serveur attend de l'App existe ici, et inversement.
    app_side = {m for m, (_d, by) in server.items() if by in ("APP", "BOTH")}
    assert app_side == set(nt.DEFAULTS)
