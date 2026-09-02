"""Tests du module ``endpoints`` (point 10.1) — source unique des routes serveur.

Deux natures de tests :

1. la construction des URL (chemin exact, normalisation de la base, échappement
   des segments variables) ;
2. un CLIQUET statique : aucun module du client ne doit reconstruire une URL de
   route à la main. C'est ce test qui empêche la dispersion de revenir.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import endpoints  # noqa: E402

BASE = "https://srv.example.com"

# (fonction, arguments, chemin attendu). Les chemins sont ceux déclarés côté
# serveur : toute divergence casse ici avant de casser en production.
CASES = [
    (endpoints.app_token, (), "/api/get_app_token"),
    (endpoints.counters, (), "/api/counters"),
    (endpoints.counter_state, (3,), "/api/counter/3/state"),
    (endpoints.is_staff_on_counter, (3,), "/api/counter/is_staff_on_counter/3"),
    (endpoints.is_patient_on_counter, (3,), "/api/counter/is_patient_on_counter/3"),
    (endpoints.patients_list, (), "/api/patients_list_for_pyside"),
    (endpoints.put_standing_list, (12,), "/api/counter/put_standing_list/12"),
    (endpoints.put_standing_list, (12, 4), "/api/counter/put_standing_list/12/4"),
    (endpoints.api_validate_patient, (12,), "/api/counter/validate_patient/12"),
    (endpoints.delete_patient, (12,), "/api/counter/delete_patient/12"),
    (endpoints.validate_and_call_next, (3,), "/validate_and_call_next/3"),
    (endpoints.validate_patient, (3, 12), "/validate_patient/3/12"),
    (endpoints.pause_patient, (3, 12), "/pause_patient/3/12"),
    (endpoints.call_specific_patient, (3, 12), "/call_specific_patient/3/12"),
    (endpoints.auto_calling, (), "/app/counter/auto_calling"),
    (endpoints.paper_add, (), "/app/counter/paper_add"),
    (endpoints.relaunch_patient_call, (3,), "/app/counter/relaunch_patient_call/3"),
    (endpoints.update_staff, (), "/app/counter/update_staff"),
    (endpoints.remove_staff, (), "/app/counter/remove_staff"),
    (endpoints.socket_url, (), "/socket_app_counter"),
]


@pytest.mark.parametrize("func, args, path", CASES, ids=[c[0].__name__ + str(c[1]) for c in CASES])
def test_chemin_exact(func, args, path):
    assert func(BASE, *args) == BASE + path


@pytest.mark.parametrize("func, args, path", CASES, ids=[c[0].__name__ + str(c[1]) for c in CASES])
def test_base_avec_slash_final(func, args, path):
    """Une base héritée finissant par « / » ne doit pas produire « // »."""
    assert func(BASE + "/", *args) == BASE + path
    assert func(BASE + "///", *args) == BASE + path


def test_base_vide_ou_none():
    """Serveur non configuré : on construit un chemin relatif, sans planter."""
    assert endpoints.app_token(None) == "/api/get_app_token"
    assert endpoints.app_token("") == "/api/get_app_token"


def test_segment_echappe_contre_injection_de_chemin():
    """Un identifiant inattendu ne doit pas pouvoir sortir de sa position."""
    url = endpoints.delete_patient(BASE, "../../admin/users")
    assert url == BASE + "/api/counter/delete_patient/..%2F..%2Fadmin%2Fusers"
    # ni ajouter une chaîne de requête
    url = endpoints.counter_state(BASE, "3?admin=1")
    assert url == BASE + "/api/counter/3%3Fadmin%3D1/state"


def test_identifiant_absent_ne_leve_pas():
    """Comportement historique conservé : une action déclenchée sans patient
    courant construit une URL vouée au 404, elle ne plante pas. Les appelants
    (validate_my_patient…) filtrent en amont."""
    assert endpoints.validate_patient(BASE, 3, None) == BASE + "/validate_patient/3/None"


def test_put_standing_list_sans_activite():
    """activity_id=None ne doit PAS ajouter de segment (les deux routes serveur
    existent : avec et sans activité)."""
    assert endpoints.put_standing_list(BASE, 12, None) == BASE + "/api/counter/put_standing_list/12"
    assert endpoints.put_standing_list(BASE, 12, 0) == BASE + "/api/counter/put_standing_list/12/0"


# --- Cliquet : plus aucune URL de route écrite à la main -------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modules applicatifs susceptibles de parler au serveur.
SOURCES = ["main.py", "preferences.py", "websocket_client.py", "connections.py",
           "buttons.py", "net_core.py", "socket_auth.py"]

# Une URL construite à la main = une interpolation de la base suivie d'un chemin.
HARDCODED = re.compile(r"\{\s*(?:self\.)?(?:web_url|base_url|url)\s*\}\s*/")


@pytest.mark.parametrize("name", SOURCES)
def test_aucune_url_de_route_en_dur(name):
    path = os.path.join(ROOT, name)
    if not os.path.exists(path):
        pytest.skip(f"{name} absent")
    with open(path, encoding="utf-8") as f:
        source = f.read()
    faults = [line for line in source.splitlines()
              if HARDCODED.search(line) and not line.lstrip().startswith("#")]
    assert not faults, (
        f"{name} construit une URL de route à la main : utilisez endpoints.py\n"
        + "\n".join(faults))
