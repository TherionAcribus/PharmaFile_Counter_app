"""Filtrage des notifications PAR CATÉGORIE — logique pure, testable sans Qt.

Historique du problème corrigé ici : ``show_notification()`` gardait TOUTES les
notifications derrière la seule préférence « Afficher les activités spécifiques
(Vaccins, Tests…) ». Décocher cette option — qui ne concerne que les actes
poussés par le serveur — coupait aussi l'affichage ET le son du rappel de
validation, des alertes de connexion, du papier, etc., alors même que leurs
propres cases restaient cochées. À l'inverse, certaines catégories n'étaient
filtrées qu'au point d'appel (donc pas pour les notifications venues du serveur).

Ce module définit la source unique de vérité :

  - la **catégorie** d'une notification à partir de son ``origin`` (celui posé par
    l'app ou par le serveur, cf. ``communication.send_app_notification``) ;
  - la **clé de préférence d'affichage** et la **clé de préférence de son** de
    chaque catégorie — ce qui permet de distinguer « afficher » et « jouer un
    son » (on peut vouloir voir une alerte sans qu'elle sonne, ou l'inverse).

Une catégorie est indépendante des autres : sa case ne peut plus faire taire une
alerte qui n'a rien à voir avec elle.
"""

# --- Catégories -----------------------------------------------------------

CURRENT_PATIENT = "current_patient"
AUTOCALLING = "autocalling"
SPECIFIC_ACTS = "specific_acts"
PAPER = "paper"
CONNECTION = "connection"
VALIDATION = "validation"
SYSTEM = "system"

#: Ordre d'affichage dans la fenêtre de préférences.
CATEGORIES = (
    CURRENT_PATIENT,
    AUTOCALLING,
    SPECIFIC_ACTS,
    PAPER,
    CONNECTION,
    VALIDATION,
    SYSTEM,
)

#: Libellé de chaque catégorie (fenêtre de préférences).
CATEGORY_LABELS = {
    CURRENT_PATIENT: "Patient appelé au comptoir",
    AUTOCALLING: "Appel automatique",
    SPECIFIC_ACTS: "Activités spécifiques (vaccins, tests…)",
    PAPER: "Papier de la borne",
    CONNECTION: "Connexion au serveur / temps réel",
    VALIDATION: "Rappel de validation du patient",
    SYSTEM: "Autres alertes (imprimante, déconnexion, transfert…)",
}

#: Explication courte (infobulle) de ce que couvre chaque catégorie.
CATEGORY_HINTS = {
    CURRENT_PATIENT: "Le patient que vous venez d'appeler à votre comptoir.",
    AUTOCALLING: "Un patient vous est attribué automatiquement.",
    SPECIFIC_ACTS: "Actes signalés par le serveur (voir le paramétrage du serveur).",
    PAPER: "Papier bientôt épuisé, plus de papier, papier rechargé.",
    CONNECTION: "Serveur inaccessible, connexion temps réel perdue ou rétablie.",
    VALIDATION: "Rappel après le délai « patient non validé ».",
    SYSTEM: "Erreur d'imprimante, déconnexion par un autre poste, patient déjà "
            "pris, transfert de patient, origines inconnues.",
}

# --- Origines -> catégorie ------------------------------------------------

# Toute origine non listée retombe sur SYSTEM : une notification d'origine
# inconnue (nouvelle version du serveur, par exemple) reste visible.
_ORIGIN_CATEGORY = {
    "new_patient": CURRENT_PATIENT,
    "autocalling": AUTOCALLING,
    "activity": SPECIFIC_ACTS,
    "low_paper": PAPER,
    "no_paper": PAPER,
    "paper_ok": PAPER,
    "connection": CONNECTION,
    "socket_connection_true": CONNECTION,
    "socket_connection_false": CONNECTION,
    "please_validate": VALIDATION,
}

# --- Clés de préférences --------------------------------------------------

# Affichage : on conserve les clés QSettings historiques là où elles existent
# (aucune migration nécessaire, les réglages déjà enregistrés restent valables).
DISPLAY_KEYS = {
    CURRENT_PATIENT: "notification_current_patient",
    AUTOCALLING: "notification_autocalling_new_patient",
    SPECIFIC_ACTS: "notification_specific_acts",
    PAPER: "notification_add_paper",
    CONNECTION: "notification_connection",
    VALIDATION: "notification_validation",
    SYSTEM: "notification_system",
}

#: Son : une clé par catégorie, distincte de l'affichage.
SOUND_KEYS = {
    CURRENT_PATIENT: "notification_current_patient_sound",
    AUTOCALLING: "notification_autocalling_new_patient_sound",
    SPECIFIC_ACTS: "notification_specific_acts_sound",
    PAPER: "notification_add_paper_sound",
    CONNECTION: "notification_connection_sound",
    VALIDATION: "notification_validation_sound",
    SYSTEM: "notification_system_sound",
}

#: Toutes les clés à charger depuis QSettings (affichage + son).
ALL_KEYS = tuple(DISPLAY_KEYS[c] for c in CATEGORIES) + tuple(SOUND_KEYS[c] for c in CATEGORIES)


def category_for_origin(origin):
    """Catégorie d'une notification à partir de son ``origin`` (SYSTEM par défaut)."""
    return _ORIGIN_CATEGORY.get(origin, SYSTEM)


def display_key(origin):
    """Clé de préférence « afficher » qui gouverne cette origine."""
    return DISPLAY_KEYS[category_for_origin(origin)]


def sound_key(origin):
    """Clé de préférence « jouer un son » qui gouverne cette origine."""
    return SOUND_KEYS[category_for_origin(origin)]


def _enabled(prefs, key):
    """Lecture tolérante : une clé absente vaut « activé ».

    On préfère une notification en trop à une alerte silencieusement perdue
    parce qu'une préférence manque (config partielle, test, futur réglage)."""
    if not prefs:
        return True
    value = prefs.get(key, True)
    return True if value is None else bool(value)


def should_display(origin, prefs, force=False):
    """Faut-il AFFICHER cette notification ? ``force`` ignore les préférences
    (bouton de test, retour visuel d'un raccourci : réglages dédiés ailleurs)."""
    return True if force else _enabled(prefs, display_key(origin))


def should_play_sound(origin, prefs, force=False):
    """Faut-il JOUER LE SON de cette notification ? Indépendant de l'affichage."""
    return True if force else _enabled(prefs, sound_key(origin))
