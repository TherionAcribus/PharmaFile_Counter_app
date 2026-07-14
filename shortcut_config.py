"""Logique pure des raccourcis clavier (point 27), testable sans Qt ni keyboard.

Les raccourcis globaux de l'App comptoir peuvent entrer en conflit avec ceux du
progiciel : une action patient risque d'être déclenchée alors que l'utilisateur
travaille dans une autre application. Ce module regroupe, isolés de Qt et de la
bibliothèque ``keyboard`` pour être testables :

  - les trois MODES (désactivés / actifs au premier plan / globaux) ;
  - la NORMALISATION d'un raccourci (indépendante de l'ordre et de la casse des
    modificateurs) et la DÉTECTION DE DOUBLONS entre actions ;
  - la TRADUCTION de la représentation d'interface (Ctrl/Alt/Maj/Win + touche)
    vers la syntaxe attendue par ``keyboard`` (mode global) et par
    ``QKeySequence`` (mode premier plan) ;
  - les LIBELLÉS d'actions (pour le retour visuel) et la liste des actions
    SENSIBLES (confirmation facultative).

L'intégration (installation des hooks keyboard / QShortcut, notifications,
QMessageBox de confirmation) vit dans main.py ; l'UI de configuration dans
preferences.py.
"""

from __future__ import annotations

# --- Modes de raccourci -----------------------------------------------------

MODE_DISABLED = "disabled"   # aucun raccourci
MODE_FOCUSED = "focused"     # actifs seulement quand PharmaFile est au premier plan
MODE_GLOBAL = "global"       # actifs partout dans le système
MODES = (MODE_DISABLED, MODE_FOCUSED, MODE_GLOBAL)
# Par défaut : global, pour préserver le comportement historique des postes
# existants. L'utilisateur peut neutraliser (« désactivés ») ou restreindre au
# premier plan (« focused ») depuis les préférences.
DEFAULT_MODE = MODE_GLOBAL

# --- Actions ----------------------------------------------------------------

# Ordre d'affichage / d'itération stable des actions raccourcissables.
ACTIONS = ("next", "validate", "pause", "recall", "deconnect")

ACTION_LABELS = {
    "next": "Patient suivant",
    "validate": "Valider le patient",
    "pause": "Pause",
    "recall": "Relancer l'appel",
    "deconnect": "Déconnexion",
}

# Actions « sensibles » : une confirmation facultative peut être demandée avant
# de les exécuter via un raccourci (la déconnexion sort le staff du comptoir).
SENSITIVE_ACTIONS = frozenset({"deconnect"})

# --- Traduction des modificateurs -------------------------------------------

# Jetons d'interface (et synonymes tolérés) -> nom canonique interne.
_MOD_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl", "ctl": "ctrl",
    "alt": "alt", "altgr": "alt",
    "maj": "shift", "shift": "shift",
    "win": "win", "windows": "win", "meta": "win", "cmd": "win", "super": "win",
}
# Nom canonique -> nom attendu par la bibliothèque ``keyboard``.
_KEYBOARD_MOD = {"ctrl": "ctrl", "alt": "alt", "shift": "shift", "win": "windows"}
# Nom canonique -> nom attendu par ``QKeySequence``.
_QT_MOD = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Meta"}
# Ordre conventionnel des modificateurs pour QKeySequence.
_QT_MOD_ORDER = ("ctrl", "alt", "shift", "win")


def _split(text):
    """Décompose un raccourci d'interface en (modificateurs canoniques, touche).

    ``text`` : chaîne « Ctrl+Alt+P » (jetons séparés par « + »). Retourne la liste
    des modificateurs canoniques (sans doublon, dans l'ordre de saisie) et la
    dernière touche non-modificateur (en minuscules), ou None s'il n'y en a pas.
    """
    mods = []
    key = None
    if text:
        for raw in str(text).split("+"):
            tok = raw.strip().lower()
            if not tok:
                continue
            if tok in _MOD_ALIASES:
                canon = _MOD_ALIASES[tok]
                if canon not in mods:
                    mods.append(canon)
            else:
                key = tok  # dernière touche « réelle » l'emporte
    return mods, key


def normalize_shortcut(text):
    """Forme canonique d'un raccourci, indépendante de l'ordre et de la casse des
    modificateurs (« Alt+Ctrl+P » et « ctrl+alt+p » -> « alt+ctrl+p »).

    Un raccourci vide ou réduit à des modificateurs (non assignable) donne « »."""
    mods, key = _split(text)
    if not key:
        return ""
    return "+".join(sorted(mods) + [key])


def normalize_mode(mode):
    """Mode valide, ou le mode par défaut si la valeur est inconnue/illisible."""
    return mode if mode in MODES else DEFAULT_MODE


def find_duplicate_shortcuts(mapping):
    """Détecte les combinaisons partagées par plusieurs actions.

    ``mapping`` : ``{action: texte_raccourci}``. Retourne
    ``{forme_normalisée: [actions...]}`` pour les combinaisons NON vides utilisées
    par au moins deux actions (les raccourcis vides sont ignorés : « non
    assigné » n'est pas un conflit)."""
    groups = {}
    for action, text in mapping.items():
        norm = normalize_shortcut(text)
        if not norm:
            continue
        groups.setdefault(norm, []).append(action)
    return {norm: acts for norm, acts in groups.items() if len(acts) > 1}


# --- Validation d'un raccourci saisi ----------------------------------------

# Touches « réelles » nommées reconnues au-delà d'un caractère simple. Sert de
# garde-fou précoce (au moment d'enregistrer les préférences) contre les fautes
# de frappe évidentes ; la bibliothèque ``keyboard`` reste seule juge à
# l'installation (ses erreurs sont interceptées et signalées, cf. main.py).
_NAMED_KEYS = frozenset({
    "space", "enter", "return", "tab", "esc", "escape", "backspace",
    "delete", "del", "insert", "ins", "home", "end", "pageup", "pagedown",
    "up", "down", "left", "right", "printscreen", "pause", "menu",
    "capslock", "numlock", "scrolllock",
    "plus", "minus", "add", "subtract", "multiply", "divide", "decimal",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
})


def is_recognized_key(key):
    """Vrai si ``key`` est une touche « réelle » plausible : un caractère simple
    (lettre, chiffre, ponctuation) ou une touche nommée connue (F1-F12, espace,
    entrée, flèches…). Insensible à la casse."""
    if not key:
        return False
    k = str(key).strip().lower()
    if len(k) == 1:
        return True
    return k in _NAMED_KEYS


# Codes de problème renvoyés par find_invalid_shortcuts (l'UI les traduit).
INVALID_EMPTY = "empty"                 # aucune touche saisie
INVALID_LONE_MODIFIER = "lone_modifier"  # modificateur(s) sans touche réelle
INVALID_UNKNOWN_KEY = "unknown_key"      # touche non reconnue


def find_invalid_shortcuts(mapping):
    """Valide chaque raccourci d'un ``{action: texte}``.

    Retourne ``{action: code}`` pour les raccourcis invalides, où ``code`` vaut :
      - ``INVALID_EMPTY`` : champ vide (aucune touche) ;
      - ``INVALID_LONE_MODIFIER`` : uniquement des modificateurs (ex. « Ctrl+Alt »
        sans touche) — n'enregistrerait aucun raccourci exploitable ;
      - ``INVALID_UNKNOWN_KEY`` : la touche réelle n'est pas reconnue.

    Les doublons entre actions sont détectés séparément (find_duplicate_shortcuts)."""
    invalid = {}
    for action, text in mapping.items():
        mods, key = _split(text)
        if key is None:
            invalid[action] = INVALID_LONE_MODIFIER if mods else INVALID_EMPTY
        elif not is_recognized_key(key):
            invalid[action] = INVALID_UNKNOWN_KEY
    return invalid


def to_keyboard_hotkey(text):
    """Traduit un raccourci d'interface vers la syntaxe de la bibliothèque
    ``keyboard`` (mode global), ex. « Ctrl+Maj+P » -> « ctrl+shift+p ».

    Retourne None si aucune touche « réelle » n'est présente (rien à enregistrer)."""
    mods, key = _split(text)
    if not key:
        return None
    kb_mods = [_KEYBOARD_MOD[m] for m in sorted(mods)]
    return "+".join(kb_mods + [key])


def to_qt_key_sequence(text):
    """Traduit un raccourci d'interface vers la syntaxe ``QKeySequence`` (mode
    premier plan), ex. « Alt+Maj+P » -> « Alt+Shift+P ».

    Retourne None si aucune touche « réelle » n'est présente."""
    mods, key = _split(text)
    if not key:
        return None
    ordered = [_QT_MOD[m] for m in _QT_MOD_ORDER if m in mods]
    key_text = key.upper() if len(key) == 1 else key.title()
    return "+".join(ordered + [key_text])
