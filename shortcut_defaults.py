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


# --- Lecture depuis les préférences (source unique) -------------------------

#: Modificateurs tels qu'affichés dans l'interface, dans l'ordre d'affichage ET
#: de sérialisation (« Ctrl+Alt+Maj+Win+K »). Les cases à cocher des préférences
#: portent ces mêmes noms comme ``objectName``.
UI_MODIFIERS = ("Ctrl", "Alt", "Maj", "Win")


def read_shortcut(settings, name, logger=None):
    """Lit un raccourci depuis les préférences, migre et PERSISTE la correction.

    Fonction partagée par ``main.py`` (application du raccourci au démarrage) et
    ``preferences.py`` (affichage dans l'écran de configuration) : avant le point
    10.2, les deux avaient leur propre version et seule celle de main.py
    persistait la migration — la fenêtre de préférences pouvait donc réécrire
    l'ancienne valeur erronée à l'enregistrement.

    ``settings`` n'a besoin que de ``value(clé, défaut)`` et ``setValue`` : un
    QSettings, ou n'importe quel objet équivalent en test.
    """
    stored = settings.value(name, default_shortcut(name))
    migrated = migrate_shortcut(name, stored)
    if migrated != stored:
        settings.setValue(name, migrated)
        if logger is not None:
            logger.info("Raccourci '%s' migré vers %s", name, migrated)
    return migrated


def split_shortcut(value):
    """Décompose « Alt+Maj+P » en ``(["Alt", "Maj"], "P")`` pour l'interface.

    Un raccourci réduit à des modificateurs (« Alt+ », « Ctrl ») donne une touche
    vide : l'interface affiche alors un champ vide plutôt qu'un modificateur
    présenté comme touche finale.
    """
    keys = str(value or "").split("+")
    modifiers = [m for m in UI_MODIFIERS if m in keys]
    key = keys[-1] if keys and keys[-1] not in UI_MODIFIERS else ""
    return modifiers, key


def join_shortcut(modifiers, key):
    """Recompose un raccourci depuis l'interface (ordre canonique des
    modificateurs, puis la touche). Inverse de :func:`split_shortcut`."""
    parts = [m for m in UI_MODIFIERS if m in set(modifiers)]
    if key:
        parts.append(key)
    return "+".join(parts)
