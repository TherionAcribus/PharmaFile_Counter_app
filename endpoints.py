"""Adresses des routes serveur utilisées par le client comptoir (point 10.1).

Source UNIQUE des chemins d'URL : aucune route ne doit plus être écrite en dur
ailleurs dans le client. Avant ce module, une vingtaine de chaînes
``f'{self.web_url}/api/...'`` étaient dispersées dans ``main.py`` et
``preferences.py`` : renommer une route côté serveur imposait une chasse au
grep, et rien ne garantissait que le client et le serveur parlaient des mêmes
chemins.

Module PUR : pas de Qt, pas de réseau, pas d'état. Chaque fonction prend l'URL de
base du serveur (``web_url``) et retourne l'URL absolue. Testable isolément.

Conventions
-----------
* ``base`` est nettoyée de ses slashs finaux : ``https://srv/`` et
  ``https://srv`` donnent le même résultat (``url_validation.normalize_url``
  fait déjà ce travail à l'enregistrement, on se protège des valeurs héritées).
* Les segments variables sont échappés (``quote``) : une valeur inattendue ne
  peut pas injecter de segment de chemin (« ../ ») ni de requête (« ?x=1 »).
* Les identifiants ne sont volontairement PAS validés ici. Côté serveur les
  routes utilisent le convertisseur ``<int:...>`` : un identifiant absent donne
  un 404 propre, déjà géré par les appelants. Lever une exception à la
  construction changerait ce comportement (une action déclenchée sans patient
  courant planterait au lieu d'être ignorée).

Le chemin de chaque fonction est celui déclaré côté serveur ; la référence est
indiquée en commentaire pour que les deux côtés restent traçables.
"""

from urllib.parse import quote

#: Namespace Socket.IO du client comptoir (Serveur/sockets.py).
SOCKET_NAMESPACE = "/socket_app_counter"


def _base(web_url):
    """URL de base normalisée (sans slash final). ``None`` devient ''."""
    return str(web_url or "").rstrip("/")


def _seg(value):
    """Segment de chemin échappé (aucun « / » ni « ? » ne peut s'y glisser)."""
    return quote(str(value), safe="")


# --- Authentification / configuration -------------------------------------

def app_token(web_url):
    """POST — jeton applicatif (routes/api_system.py:/api/get_app_token)."""
    return f"{_base(web_url)}/api/get_app_token"


def counters(web_url):
    """GET — liste des comptoirs (routes/api_system.py:/api/counters)."""
    return f"{_base(web_url)}/api/counters"


# --- État du comptoir (lecture) -------------------------------------------

def counter_state(web_url, counter_id):
    """GET — état autoritatif complet (routes/counter.py:/api/counter/<id>/state)."""
    return f"{_base(web_url)}/api/counter/{_seg(counter_id)}/state"


def is_staff_on_counter(web_url, counter_id):
    """GET — agent présent sur le comptoir (routes/counter.py)."""
    return f"{_base(web_url)}/api/counter/is_staff_on_counter/{_seg(counter_id)}"


def is_patient_on_counter(web_url, counter_id):
    """GET — patient en cours sur le comptoir (routes/counter.py)."""
    return f"{_base(web_url)}/api/counter/is_patient_on_counter/{_seg(counter_id)}"


def patients_list(web_url):
    """GET — file d'attente (routes/pyside.py:/api/patients_list_for_pyside)."""
    return f"{_base(web_url)}/api/patients_list_for_pyside"


# --- Actions sur un patient de la file ------------------------------------

def put_standing_list(web_url, patient_id, activity_id=None):
    """POST — remise en attente, éventuellement vers une autre activité
    (routes/counter.py:/api/counter/put_standing_list/<pid>[/<aid>])."""
    url = f"{_base(web_url)}/api/counter/put_standing_list/{_seg(patient_id)}"
    if activity_id is not None:
        url = f"{url}/{_seg(activity_id)}"
    return url


def api_validate_patient(web_url, patient_id):
    """POST — validation d'un patient désigné (routes/counter.py)."""
    return f"{_base(web_url)}/api/counter/validate_patient/{_seg(patient_id)}"


def delete_patient(web_url, patient_id):
    """POST — suppression d'un patient de la file (routes/counter.py)."""
    return f"{_base(web_url)}/api/counter/delete_patient/{_seg(patient_id)}"


# --- Actions du comptoir courant (routes/calling.py) -----------------------

def validate_and_call_next(web_url, counter_id):
    """POST — valider le patient courant et appeler le suivant."""
    return f"{_base(web_url)}/validate_and_call_next/{_seg(counter_id)}"


def validate_patient(web_url, counter_id, patient_id):
    """POST — valider le patient du comptoir."""
    return f"{_base(web_url)}/validate_patient/{_seg(counter_id)}/{_seg(patient_id)}"


def pause_patient(web_url, counter_id, patient_id):
    """POST — mettre le patient courant en pause."""
    return f"{_base(web_url)}/pause_patient/{_seg(counter_id)}/{_seg(patient_id)}"


def call_specific_patient(web_url, counter_id, patient_id):
    """POST — appeler un patient précis choisi dans la file."""
    return f"{_base(web_url)}/call_specific_patient/{_seg(counter_id)}/{_seg(patient_id)}"


# --- Routes applicatives protégées par jeton (routes/counter.py, /app/…) ---

def auto_calling(web_url):
    """POST — bascule de l'appel automatique."""
    return f"{_base(web_url)}/app/counter/auto_calling"


def paper_add(web_url):
    """POST — bascule du signalement « papier à changer »."""
    return f"{_base(web_url)}/app/counter/paper_add"


def relaunch_patient_call(web_url, counter_id):
    """POST — relance de l'appel du patient courant (rappel)."""
    return f"{_base(web_url)}/app/counter/relaunch_patient_call/{_seg(counter_id)}"


def update_staff(web_url):
    """POST — prise de poste d'un agent sur le comptoir."""
    return f"{_base(web_url)}/app/counter/update_staff"


def remove_staff(web_url):
    """POST — libération du comptoir par l'agent."""
    return f"{_base(web_url)}/app/counter/remove_staff"


# --- Temps réel ------------------------------------------------------------

def socket_url(web_url):
    """URL de connexion Socket.IO du client comptoir."""
    return f"{_base(web_url)}{SOCKET_NAMESPACE}"
