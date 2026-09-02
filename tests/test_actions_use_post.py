"""Régression : les actions patient modificatrices sont envoyées en POST (pt 14).

L'invariant portait sur les appels ``self._submit(..., method='POST')`` dispersés
dans main.py. Depuis le point 10.8, toutes les requêtes vivent dans
``counter_api`` : l'invariant se vérifie donc là, et de façon structurelle
(analyse de l'AST) plutôt que par expression régulière.

Deux garde-fous :

1. chaque action MODIFICATRICE passe par ``self._post`` (jamais ``_get``, jamais
   ``_submit`` avec une méthode choisie sur place) ;
2. ``_submit`` n'a PAS de méthode HTTP par défaut — une nouvelle action ne peut
   donc pas hériter d'un GET par inadvertance, ce qui était le risque d'origine.
"""

import ast
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import counter_api  # noqa: E402
from counter_api import CounterApi  # noqa: E402

_SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "counter_api.py")

# Actions qui CHANGENT l'état côté serveur : toutes doivent partir en POST.
MODIFYING_ACTIONS = [
    "validate_and_call_next",
    "validate_current_patient",
    "validate_queued_patient",
    "pause_current_patient",
    "relaunch_call",
    "call_specific_patient",
    "put_standing",
    "delete_patient",
    "login_staff",
    "logout_staff",
]

# Lectures seules : GET.
READ_ACTIONS = ["fetch_staff"]


def _method_body(name):
    tree = ast.parse(open(_SOURCE, encoding="utf-8").read(), filename="counter_api.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"méthode {name} introuvable dans counter_api.py")


def _called_helpers(node):
    """Noms des helpers ``self._xxx`` appelés dans le corps d'une méthode."""
    names = set()
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and isinstance(sub.func.value, ast.Name) and sub.func.value.id == "self"):
            names.add(sub.func.attr)
    return names


@pytest.mark.parametrize("name", MODIFYING_ACTIONS)
def test_action_modificatrice_en_post(name):
    helpers = _called_helpers(_method_body(name))
    assert "_post" in helpers, f"{name} n'utilise pas self._post"
    assert "_get" not in helpers, f"{name} est modificatrice : pas de GET"


@pytest.mark.parametrize("name", READ_ACTIONS)
def test_lecture_en_get(name):
    helpers = _called_helpers(_method_body(name))
    assert "_get" in helpers and "_post" not in helpers


def test_toutes_les_actions_sont_couvertes():
    """Une nouvelle action publique doit être classée ici (POST ou GET), sinon ce
    test échoue : impossible d'en ajouter une sans décider de sa méthode."""
    publiques = {name for name, _ in inspect.getmembers(CounterApi, inspect.isfunction)
                 if not name.startswith("_")}
    infra = {"make_handle", "fetch_token", "clear_token", "stop",
             "fetch_state", "fetch_current_patient", "fetch_patients_list",
             "release_counter_blocking"}
    classees = set(MODIFYING_ACTIONS) | set(READ_ACTIONS) | infra
    assert publiques - classees == set(), (
        "action non classée POST/GET dans ce test : " + ", ".join(sorted(publiques - classees)))


def test_submit_sans_methode_par_defaut():
    """Le défaut GET historique de _submit est supprimé : la méthode HTTP est un
    paramètre obligatoire, on ne peut plus l'oublier."""
    signature = inspect.signature(CounterApi._submit)
    assert signature.parameters["method"].default is inspect.Parameter.empty


def test_idempotence_reservee_a_l_action_qui_fait_avancer_la_file():
    """Seul « valider et appeler le suivant » porte une clé d'idempotence : c'est
    la seule action dont un rejeu ferait avancer la file deux fois."""
    tree = ast.parse(open(_SOURCE, encoding="utf-8").read(), filename="counter_api.py")
    idempotentes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for sub in ast.walk(node):
                if isinstance(sub, ast.keyword) and sub.arg == "idempotent":
                    if isinstance(sub.value, ast.Constant) and sub.value.value is True:
                        idempotentes.append(node.name)
    assert idempotentes == ["validate_and_call_next"]


def test_plus_aucune_requete_construite_dans_main():
    """main.py ne doit plus appeler directement le gestionnaire réseau."""
    main_source = open(
        os.path.join(os.path.dirname(_SOURCE), "main.py"), encoding="utf-8").read()
    for interdit in ("self.network_manager.request_blocking",
                     "self.network_manager.make_handle",
                     "self.network_manager.fetch_token_blocking"):
        assert interdit not in main_source, interdit


def test_module_expose_bien_la_classe():
    assert counter_api.CounterApi is CounterApi
