"""Valeurs par défaut des raccourcis clavier — source unique de vérité.

Centralisées ici pour éviter toute divergence entre main.py et preferences.py
(bug historique : le défaut Pause valait « Altl+P » dans main.py alors que les
préférences utilisaient « Alt+P », rendant la Pause inopérante au 1er démarrage).

Inclut aussi une petite migration transparente qui remplace les anciennes valeurs
erronées éventuellement déjà enregistrées.
"""

SHORTCUT_DEFAULTS = {
    "next_patient_shortcut": "Alt+S",
    "validate_patient_shortcut": "Alt+V",
    "pause_shortcut": "Alt+P",
    "recall_shortcut": "Alt+R",
    "deconnect_shortcut": "Alt+D",
}

# Anciennes valeurs erronées -> valeur corrigée, par raccourci.
_LEGACY_SHORTCUT_FIXES = {
    "pause_shortcut": {"Altl+P": "Alt+P"},
}


def default_shortcut(name):
    """Valeur par défaut d'un raccourci (chaîne vide si nom inconnu)."""
    return SHORTCUT_DEFAULTS.get(name, "")


def migrate_shortcut(name, value):
    """Retourne la valeur corrigée si `value` est une ancienne valeur erronée
    connue pour `name`, sinon `value` inchangée."""
    return _LEGACY_SHORTCUT_FIXES.get(name, {}).get(value, value)
