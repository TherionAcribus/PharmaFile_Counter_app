"""Schéma de configuration du comptoir — source unique de vérité.

Centralise, pour chaque clé de configuration (QSettings) :
  - le type attendu,
  - la valeur par défaut,
  - la plage / normalisation autorisée,
et fournit un numéro de version de schéma + un point d'entrée de migration.

Motivation (point 6) : les valeurs par défaut divergeaient entre main.py et
preferences.py — URL locale (localhost:5000) vs Render, notification « patient
courant » True vs False, délai « après appel » 60 vs 30 s. Le comportement
dépendait donc de la présence ou non d'une valeur enregistrée, c.-à-d. du fait
que l'utilisateur ait ou non déjà ouvert la fenêtre de préférences. En lisant
TOUTES ces valeurs via ce module, l'application (main.py) et la fenêtre de
préférences (preferences.py) partagent exactement les mêmes défauts et plages.

Clés délibérément gérées ailleurs (déjà à source unique, hors de ce module) :
  - raccourcis clavier         -> shortcut_defaults.py (défaut + migration typo)
  - counter_id                 -> counter_id_utils.coerce_counter_id
                                  (défaut contextuel : placeholder de la combo
                                   dans les préférences vs valeur runtime)
  - secret applicatif          -> secret_store.py (magasin sécurisé, jamais un
                                   défaut « en clair » ici)
  - géométrie / écran fenêtre  -> QSettings binaire (saveGeometry), non typé ici

Pour ajouter une clé : ajouter une entrée dans SETTINGS. Pour changer la forme
d'une valeur déjà stockée chez des utilisateurs : incrémenter SCHEMA_VERSION et
ajouter une fonction dans _MIGRATIONS (voir migrate_settings).
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from accessibility import (
    DEFAULT_LIST_FONT_SIZE, DEFAULT_TONE, clamp_font_size, normalize_tone,
)
from panel_layout import DEFAULT_PANEL_THICKNESS, clamp_thickness
from shortcut_config import DEFAULT_MODE, normalize_mode


@dataclass(frozen=True)
class Setting:
    """Définition d'une clé de configuration.

    - ``default`` : valeur par défaut (absente de QSettings).
    - ``kind``    : type demandé à QSettings (``bool``/``int``/``str``) ; ``None``
      pour lire la valeur brute (utile quand ``coerce`` fait la conversion).
    - ``bounds``  : (min, max) inclusifs appliqués aux valeurs numériques.
    - ``coerce``  : normalisateur final (prioritaire sur ``bounds``), p. ex.
      ``normalize_mode`` ou un clamp spécialisé. Reçoit la valeur déjà typée.
    """

    default: Any
    kind: Optional[type] = None
    bounds: Optional[Tuple[int, int]] = None
    coerce: Optional[Callable[[Any], Any]] = None


# La lecture réelle est fournie par read() au niveau module (plus bas), ce qui
# évite de porter la clé QSettings dans chaque objet Setting.


SCHEMA_VERSION = 1
SCHEMA_VERSION_KEY = "config_schema_version"


SETTINGS = {
    # --- Connexion -----------------------------------------------------------
    # URL du serveur : PAS de défaut « en dur » (ni Render ni localhost). Chaque
    # officine renseigne sa propre adresse ; une valeur vide signifie « non
    # configuré » et déclenche l'écran de configuration au démarrage. C'est le
    # choix le plus universel (aucune adresse spécifique gravée dans le code) et
    # le plus simple à maintenir (l'adresse Render/officine peut changer sans
    # nouvelle version de l'application).
    "web_url": Setting(default="", kind=str),

    # --- Raccourcis (mode + options ; combinaisons : voir shortcut_defaults) --
    "shortcut_mode": Setting(default=DEFAULT_MODE, kind=str, coerce=normalize_mode),
    "confirm_sensitive_shortcuts": Setting(default=False, kind=bool),
    "shortcut_feedback": Setting(default=True, kind=bool),

    # --- Notifications (activation) ------------------------------------------
    # Défaut canonique : activée (comportement runtime historique de main.py).
    "notification_current_patient": Setting(default=True, kind=bool),
    "notification_autocalling_new_patient": Setting(default=True, kind=bool),
    "notification_specific_acts": Setting(default=True, kind=bool),
    "notification_add_paper": Setting(default=True, kind=bool),
    "notification_connection": Setting(default=True, kind=bool),

    # --- Notifications (délais / tailles) ------------------------------------
    "notification_after_deconnection": Setting(default=10, kind=int, bounds=(0, 99)),
    # Défaut canonique : 30 s (valeur affichée par les préférences ; le minimum
    # de la case correspondante est de 10 s).
    "notification_after_calling": Setting(default=30, kind=int, bounds=(10, 120)),
    "notification_duration": Setting(default=5, kind=int, bounds=(1, 60)),
    "notification_font_size": Setting(default=12, kind=int, bounds=(8, 36)),
    "notification_volume": Setting(default=50, kind=int, bounds=(0, 100)),
    "notification_corner": Setting(default="bottom-left", kind=str),

    # --- Ton / accessibilité -------------------------------------------------
    "message_tone": Setting(default=DEFAULT_TONE, kind=str, coerce=normalize_tone),
    "patient_list_font_size": Setting(
        default=DEFAULT_LIST_FONT_SIZE, kind=int, coerce=clamp_font_size),

    # --- Fenêtre / panneau ---------------------------------------------------
    "always_on_top": Setting(default=False, kind=bool),
    # Clé QSettings historique « vertical_mode » = mode horizontal dans l'app.
    "vertical_mode": Setting(default=False, kind=bool),
    "compact_mode": Setting(default=False, kind=bool),
    "panel_snap": Setting(default=True, kind=bool),
    "panel_thickness": Setting(default=DEFAULT_PANEL_THICKNESS, coerce=clamp_thickness),

    # --- File des patients ---------------------------------------------------
    "display_patient_list": Setting(default=False, kind=bool),
    "patient_list_vertical_position": Setting(default="bottom", kind=str),
    "patient_list_horizontal_position": Setting(default="right", kind=str),

    # --- Divers --------------------------------------------------------------
    "debug_window": Setting(default=False, kind=bool),
    "selected_skin": Setting(default="", kind=str),
}


def default(key: str) -> Any:
    """Valeur par défaut d'une clé connue."""
    return SETTINGS[key].default


def read(settings, key: str) -> Any:
    """Lit une valeur depuis ``settings`` (QSettings) en appliquant type, plage
    et normalisation définis par le schéma. ``key`` doit exister dans SETTINGS.
    """
    spec = SETTINGS[key]
    if spec.kind is None:
        value = settings.value(key, spec.default)
    else:
        value = settings.value(key, spec.default, type=spec.kind)
    if spec.coerce is not None:
        return spec.coerce(value)
    if spec.bounds is not None:
        low, high = spec.bounds
        return max(low, min(high, value))
    return value


# ---------------------------------------------------------------------------
# Versionnage et migrations du schéma de configuration
#
# _MIGRATIONS : liste ordonnée de (version_cible, fonction(settings)). Chaque
# fonction fait passer une configuration de (version_cible - 1) à version_cible.
# Pour l'instant (schéma v1), aucune transformation de données stockée n'est
# nécessaire : les corrections de divergence portent sur des *défauts* (jamais
# persistés). Le cadre est en place pour de futures évolutions.
# ---------------------------------------------------------------------------
_MIGRATIONS = []  # type: list[Tuple[int, Callable]]


def migrate_settings(settings) -> int:
    """Applique les migrations de schéma nécessaires puis estampille la version
    courante. Idempotent : sans travail à faire, met simplement à jour la clé de
    version. Retourne la version effective après migration."""
    stored = settings.value(SCHEMA_VERSION_KEY, 0, type=int)
    if stored >= SCHEMA_VERSION:
        # Déjà à jour (ou config plus récente : on n'écrase pas une version
        # supérieure, cas d'un retour à une version antérieure de l'app).
        return stored
    version = stored
    for target, migration in _MIGRATIONS:
        if version < target <= SCHEMA_VERSION:
            migration(settings)
            version = target
    settings.setValue(SCHEMA_VERSION_KEY, SCHEMA_VERSION)
    return SCHEMA_VERSION
