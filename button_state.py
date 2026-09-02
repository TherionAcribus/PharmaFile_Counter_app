"""Logique d'état des boutons, isolée de Qt (donc testable seule).

Deux responsabilités :

* ``resolve_button_state`` — boutons d'icône (papier / appel automatique) :
  garantit qu'après une réponse serveur le bouton ne reste JAMAIS bloqué dans
  l'état transitoire "waiting" ;
* ``resolve_patient_buttons`` — boutons d'action patient (Valider / Pause) et
  minuteur d'appel, en fonction du patient courant (point 10.3). Cette logique
  vivait dans ``MainWindow.update_my_buttons``, enfermée dans un ``except:`` nu
  marqué « #TEMPORAIRE » qui avalait aussi bien une donnée serveur malformée
  qu'une vraie erreur de programmation.
"""

from typing import NamedTuple, Optional


def resolve_button_state(status_code, data, previous_state):
    """Détermine le nouvel état d'un IconeButton d'après la réponse serveur.

    ``data`` est le JSON déjà décodé par le gestionnaire réseau (ou None si la
    réponse n'était pas du JSON exploitable).

    - 200 avec ``data == {"status": bool, ...}`` -> "active" / "inactive" ;
    - 200 mais ``data`` absent/inattendu, OU toute autre réponse (401 après échec
      de renouvellement, 5xx, erreur réseau ``status=0``...) -> on restaure
      ``previous_state`` afin que le bouton quitte "waiting" et redevienne
      utilisable, même en cas d'erreur.
    """
    if status_code == 200 and isinstance(data, dict) and "status" in data:
        return "active" if data["status"] else "inactive"
    return previous_state


# --- Boutons d'action patient (Valider / Pause) et minuteur d'appel ---------

class PatientButtons(NamedTuple):
    """État à appliquer aux boutons d'action du patient courant.

    ``validate_alert`` vaut ``None`` quand l'alerte visuelle ne doit PAS être
    touchée (cas de l'appel en cours : c'est justement le moment où l'alerte
    « pensez à valider » peut être allumée par ailleurs).
    """
    pause_enabled: bool
    validate_enabled: bool
    validate_alert: Optional[bool]
    start_call_timer: bool


#: Aucun patient exploitable : plus aucune action possible, minuteur arrêté.
IDLE_BUTTONS = PatientButtons(pause_enabled=False, validate_enabled=False,
                              validate_alert=False, start_call_timer=False)
#: Patient en cours d'appel : on peut valider, pas mettre en pause.
CALLING_BUTTONS = PatientButtons(pause_enabled=False, validate_enabled=True,
                                 validate_alert=None, start_call_timer=True)
#: Patient pris en charge : on peut le mettre en pause, plus le valider.
ONGOING_BUTTONS = PatientButtons(pause_enabled=True, validate_enabled=False,
                                 validate_alert=False, start_call_timer=False)

# Motifs retournés avec la décision, pour journalisation. Les trois derniers
# accompagnent une décision ``None`` (« ne rien changer »).
NO_PATIENT = "aucun patient"
CALLING = "patient en appel"
ONGOING = "patient pris en charge"
NO_CURRENT_PATIENT = "aucun patient sur le comptoir"
OTHER_COUNTER = "patient d'un autre comptoir"
UNKNOWN_STATUS = "statut inattendu"
MALFORMED = "donnée patient inexploitable"

_MISSING = object()


def resolve_patient_buttons(patient, counter_id):
    """Décide de l'état des boutons patient. Retourne ``(décision, motif)``.

    ``décision`` vaut ``None`` quand il ne faut RIEN changer à l'interface — soit
    parce que la mise à jour concerne un autre comptoir, soit parce que la donnée
    reçue est inexploitable. Ce « ne rien changer » reproduit exactement l'ancien
    comportement (l'``except:`` nu laissait l'interface en l'état), mais il est
    désormais explicite, journalisable et testable : une erreur de programmation
    dans l'application de la décision n'est plus avalée en silence.

    ``motif`` est une chaîne courte destinée aux journaux (jamais à l'utilisateur)
    et ne contient aucune donnée patient.
    """
    if not patient:
        return IDLE_BUTTONS, NO_PATIENT
    if not isinstance(patient, dict):
        return None, MALFORMED
    # Mise à jour concernant un autre comptoir (ou sans comptoir déclaré) :
    # elle ne dit rien de NOTRE patient, on n'y touche pas.
    if patient.get("counter_id", _MISSING) != counter_id:
        return None, OTHER_COUNTER
    if "id" not in patient:
        return None, MALFORMED
    if patient["id"] is None:
        return IDLE_BUTTONS, NO_CURRENT_PATIENT
    status = patient.get("status", _MISSING)
    if status == "calling":
        return CALLING_BUTTONS, CALLING
    if status == "ongoing":
        return ONGOING_BUTTONS, ONGOING
    # "standing", "done", statut absent ou inconnu : l'ancien code ne modifiait
    # rien non plus (aucune branche ne correspondait).
    return None, UNKNOWN_STATUS
