"""Préférences lues par la fenêtre principale : table attribut ← réglage.

``MainWindow.load_preferences`` lisait une à une une quarantaine de valeurs,
et ``apply_preferences`` recopiait deux fois la liste des réglages de
disposition. La correspondance est désormais déclarée ici, une seule fois.
Les clés, types, défauts et plages restent définis par ``settings_schema``
(source commune avec la fenêtre des préférences).

Restent lus à part, dans ``MainWindow.load_preferences`` : l'adresse du
serveur, le secret (magasin sécurisé) et le numéro de comptoir (défaut
contextuel, hors schéma).
"""

from __future__ import annotations

import notification_rules
import settings_schema
from shortcut_defaults import read_shortcut

#: (attribut de MainWindow, clé de settings_schema). L'attribut garde parfois
#: un nom historique différent de la clé.
SCHEMA_PREFERENCES = (
    # Reconnexion automatique au démarrage avec le dernier équipier validé
    # (l'écran de connexion reste le repli en cas d'échec).
    ("reconnect_last_user", "reconnect_last_user"),
    # Raccourcis : désactivés / au premier plan / globaux, confirmation des
    # actions sensibles, retour visuel de l'action.
    ("shortcut_mode", "shortcut_mode"),
    ("confirm_sensitive_shortcuts", "confirm_sensitive_shortcuts"),
    ("shortcut_feedback", "shortcut_feedback"),
    # Notifications. Les cases « afficher » / « son » par catégorie sont
    # regroupées à part (notification_prefs, cf. read_preferences).
    ("notification_after_deconnection", "notification_after_deconnection"),
    # Délai du rappel « pensez à valider » (s).
    ("timer_after_calling", "notification_after_calling"),
    ("notification_duration", "notification_duration"),
    ("notification_font_size", "notification_font_size"),
    # Ton des messages : « sobre » (défaut) ou « humoristique ».
    ("message_tone", "message_tone"),
    # Coin de l'écran où s'empilent les notifications.
    ("notification_corner", "notification_corner"),
    # Volume et mode muet, indépendants : couper le son ne fait pas perdre le
    # volume préféré.
    ("sound_volume", "notification_volume"),
    ("sound_muted", "notification_muted"),
    # Taille de police de la file, bornée au plancher de lisibilité.
    ("patient_list_font_size", "patient_list_font_size"),
    # Fenêtre et panneau.
    ("always_on_top", "always_on_top"),
    # Nom historique inversé : la clé « vertical_mode » porte l'orientation
    # HORIZONTALE.
    ("horizontal_mode", "vertical_mode"),
    # Panneau étroit docké sur un bord (colonne en vertical, barre fine en
    # horizontal) plutôt qu'une fenêtre ordinaire.
    ("compact_mode", "compact_mode"),
    # Bouton « appel automatique » toujours affiché (sinon seulement tant
    # que l'appel automatique est actif ; l'action reste dans le menu).
    ("show_auto_call_button", "show_auto_call_button"),
    # Magnétisme aux bords lors d'un déplacement manuel.
    ("panel_snap", "panel_snap"),
    # Épaisseur du panneau (largeur en vertical, hauteur en horizontal).
    ("panel_thickness", "panel_thickness"),
    ("display_patient_list", "display_patient_list"),
    ("patient_list_position_vertical", "patient_list_vertical_position"),
    ("patient_list_position_horizontal", "patient_list_horizontal_position"),
    # Une seule icône regroupée dans la zone de notification.
    ("tray_single_icon", "tray_single_icon"),
    # Mode diagnostic : garde la fenêtre de journal ET active le niveau DEBUG.
    ("debug_window", "debug_window"),
    ("selected_skin", "selected_skin"),
    # Taille de l'interface (appliquée au démarrage suivant).
    ("ui_scale", "ui_scale"),
)

#: Raccourcis clavier : défauts et migration des anciennes valeurs erronées
#: (ex. « Altl+P ») dans shortcut_defaults.read_shortcut — même lecture que
#: la fenêtre des préférences. L'attribut porte le nom de la clé.
SHORTCUT_PREFERENCES = (
    "next_patient_shortcut",
    "validate_patient_shortcut",
    "pause_shortcut",
    "recall_shortcut",
    "deconnect_shortcut",
)

#: Réglages de FORME du panneau : un changement le redocke.
SHAPE_ATTRIBUTES = ("horizontal_mode", "compact_mode", "panel_thickness")
#: Réglages de disposition : un changement reconstruit l'interface comptoir.
LAYOUT_ATTRIBUTES = SHAPE_ATTRIBUTES + (
    "display_patient_list",
    "patient_list_position_vertical",
    "patient_list_position_horizontal",
    "show_auto_call_button",
)


def read_preferences(settings, logger=None) -> dict:
    """Valeurs à poser sur la fenêtre, par nom d'attribut."""
    values = {attr: settings_schema.read(settings, key) for attr, key in SCHEMA_PREFERENCES}
    for name in SHORTCUT_PREFERENCES:
        values[name] = read_shortcut(settings, name, logger)
    # Un réglage « afficher » ET un réglage « son » par catégorie de
    # notification (notification_rules) : le filtrage a lieu à un seul endroit.
    values["notification_prefs"] = {key: settings_schema.read(settings, key)
                                    for key in notification_rules.ALL_KEYS}
    return values


def snapshot(window, attributes) -> tuple:
    """Valeurs actuelles de ``attributes`` sur la fenêtre (pour comparer
    avant/après un enregistrement des préférences)."""
    return tuple(getattr(window, attr, None) for attr in attributes)
