"""Accessibilité visuelle (point 28) — logique pure, testable sans Qt.

Objectif : ne jamais transmettre un état *uniquement* par la couleur. Ce module
centralise, sous forme de fonctions pures :

  - la **sévérité** d'une notification (info / succès / attention / alerte) et le
    **pictogramme + libellé** texte associés, pour qu'un état reste compréhensible
    en niveaux de gris (le glyphe et le mot distinguent l'état, pas seulement le
    fond coloré) ;
  - les **couleurs** (fond / texte) de chaque sévérité, choisies pour un contraste
    AA et exprimées avec des noms/hex valides (l'ancien ``light_green`` n'était pas
    une couleur Qt valide et retombait sur blanc) ;
  - les **titres** de notification dans deux **tons** configurables :
    ``sobre`` (explicite, défaut) et ``humoristique`` (l'ancien ton) ;
  - des **marqueurs texte** réutilisables pour les états signalés ailleurs par la
    seule couleur (patient « à valider » en rouge, patient de la file assigné à
    l'équipier surligné en orange) ;
  - le calcul du **contraste WCAG** (``contrast_ratio`` / ``passes_aa``), utilisé
    aussi bien pour valider nos couleurs que pour auditer les skins ;
  - le **bornage de la taille de police** (``clamp_font_size``) pour une police
    minimale configurable.

Aucune dépendance à Qt : tout est testable en isolation.
"""

# --- Police ---------------------------------------------------------------

# Plancher de lisibilité : on n'autorise pas une police plus petite que ceci.
# L'ancienne liste patients était figée à 8 pt ; on garde 8 comme minimum
# absolu mais la valeur devient configurable au-dessus.
MIN_FONT_POINT_SIZE = 8
MAX_FONT_POINT_SIZE = 72
DEFAULT_LIST_FONT_SIZE = 11


def clamp_font_size(size, minimum=MIN_FONT_POINT_SIZE, maximum=MAX_FONT_POINT_SIZE):
    """Ramène une taille de police (en points) dans [minimum, maximum].

    Les valeurs non numériques ou absurdes retombent sur ``minimum`` : on ne
    veut jamais afficher un texte illisible parce qu'une préférence est corrompue.
    """
    try:
        value = int(round(float(size)))
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, value))


# --- Sévérité des notifications ------------------------------------------

INFO = "info"
SUCCESS = "success"
WARNING = "warning"
CRITICAL = "critical"

# origin (côté serveur / interne) -> sévérité. Tout ce qui n'est pas listé est
# traité comme une information neutre.
_ORIGIN_SEVERITY = {
    "no_paper": CRITICAL,
    "please_validate": CRITICAL,
    "connection": CRITICAL,
    "socket_connection_false": CRITICAL,
    "printer_error": CRITICAL,
    "low_paper": WARNING,
    "patient_taken": WARNING,
    "paper_ok": SUCCESS,
    "socket_connection_true": SUCCESS,
}

# Pictogramme + libellé texte par sévérité. Le glyphe *et* le mot rendent l'état
# lisible en niveaux de gris (critère : compréhensible sans la couleur).
_SEVERITY_GLYPH = {
    CRITICAL: "⛔",   # ⛔
    WARNING: "⚠",    # ⚠
    SUCCESS: "✔",    # ✔
    INFO: "ℹ",       # ℹ
}
_SEVERITY_LABEL = {
    CRITICAL: "Alerte",
    WARNING: "Attention",
    SUCCESS: "OK",
    INFO: "Info",
}

# Couleurs (fond, texte) par sévérité. Choisies pour un contraste texte/fond
# conforme WCAG AA (>= 4.5:1) et avec des valeurs hex valides (Qt/CSS).
_SEVERITY_COLORS = {
    CRITICAL: ("#c0392b", "#ffffff"),   # rouge / blanc
    WARNING: ("#e67e22", "#1a1a1a"),    # orange / quasi-noir
    SUCCESS: ("#2e7d32", "#ffffff"),    # vert / blanc
    INFO: ("#ffffff", "#1a1a1a"),       # blanc / quasi-noir
}


def notification_severity(origin):
    """Sévérité d'une notification à partir de son ``origin``."""
    return _ORIGIN_SEVERITY.get(origin, INFO)


def severity_glyph(severity):
    """Pictogramme (glyphe unicode) associé à une sévérité."""
    return _SEVERITY_GLYPH.get(severity, _SEVERITY_GLYPH[INFO])


def severity_label(severity):
    """Libellé texte court associé à une sévérité (« Alerte », « Attention »...)."""
    return _SEVERITY_LABEL.get(severity, _SEVERITY_LABEL[INFO])


def severity_colors(severity):
    """Couple ``(fond, texte)`` contrasté pour une sévérité."""
    return _SEVERITY_COLORS.get(severity, _SEVERITY_COLORS[INFO])


def decorate_title(title, severity):
    """Préfixe un titre de notification par le pictogramme de sévérité.

    Le pictogramme distingue l'état même sans couleur ; on ne le duplique pas si
    le titre commence déjà par lui (idempotent)."""
    glyph = severity_glyph(severity)
    if not title:
        return glyph
    if title.startswith(glyph):
        return title
    return f"{glyph} {title}"


# --- Ton des messages (titres) -------------------------------------------

TONE_SOBER = "sobre"
TONE_HUMOROUS = "humoristique"
DEFAULT_TONE = TONE_SOBER
VALID_TONES = (TONE_SOBER, TONE_HUMOROUS)

# origin -> {ton: titre}. Le ton « humoristique » reprend les anciens libellés ;
# le ton « sobre » (défaut) donne un intitulé explicite et neutre.
_TITLES = {
    "activity": {
        TONE_SOBER: "Nouvelle mission",
        TONE_HUMOROUS: "Une nouvelle mission arrive !",
    },
    "printer_error": {
        TONE_SOBER: "Erreur d'imprimante",
        TONE_HUMOROUS: "Je crois qu'on a un problème...",
    },
    "low_paper": {
        TONE_SOBER: "Papier bientôt épuisé",
        TONE_HUMOROUS: "Fin du rouleau !",
    },
    "no_paper": {
        TONE_SOBER: "Plus de papier",
        TONE_HUMOROUS: "Il n'y a plus de papier !",
    },
    "paper_ok": {
        TONE_SOBER: "Papier rechargé",
        TONE_HUMOROUS: "Vous faites bonne impression !",
    },
    "patient_taken": {
        TONE_SOBER: "Patient déjà pris en charge",
        TONE_HUMOROUS: "A une seconde près !",
    },
    "autocalling": {
        TONE_SOBER: "Appel automatique",
        TONE_HUMOROUS: "Ils arrivent !",
    },
    "new_patient": {
        TONE_SOBER: "Nouveau patient",
        TONE_HUMOROUS: "Nouveau patient !",
    },
    "connection": {
        TONE_SOBER: "Problème de connexion",
        TONE_HUMOROUS: "Problème de connexion",
    },
    "please_validate": {
        TONE_SOBER: "Patient à valider",
        TONE_HUMOROUS: "Sauvez un bébé phoque : validez votre patient !",
    },
    "disconnect_by_user": {
        TONE_SOBER: "Déconnecté par un autre poste",
        TONE_HUMOROUS: "Pousse toi de là !",
    },
    "test_notification": {
        TONE_SOBER: "Notification de test",
        TONE_HUMOROUS: "Test micro, 1, 2, 3, Test...",
    },
    "socket_connection_true": {
        TONE_SOBER: "Temps réel connecté",
        TONE_HUMOROUS: "Tout va bien, on est branché !",
    },
    "socket_connection_false": {
        TONE_SOBER: "Temps réel déconnecté",
        TONE_HUMOROUS: "Quelqu'un s'est pris les pieds dans les câbles !",
    },
    "patient_for_staff_from_app": {
        TONE_SOBER: "Transfert de patient",
        TONE_HUMOROUS: "Transfert de patient",
    },
}


def normalize_tone(tone):
    """Ramène une valeur de préférence quelconque à un ton valide (défaut sobre)."""
    if isinstance(tone, str) and tone.lower() in VALID_TONES:
        return tone.lower()
    return DEFAULT_TONE


def notification_title(origin, tone=DEFAULT_TONE, fallback=None):
    """Titre d'une notification pour un ``origin`` et un ``tone`` donnés.

    ``origin`` inconnu -> ``fallback`` (par défaut l'origin lui-même), pour
    reproduire l'ancien comportement (``self.title = self.origin``)."""
    tone = normalize_tone(tone)
    entry = _TITLES.get(origin)
    if entry is None:
        return origin if fallback is None else fallback
    return entry.get(tone, entry[DEFAULT_TONE])


# --- Marqueurs d'états signalés ailleurs par la seule couleur -------------

# Patient de la file dont l'activité est assignée à l'équipier courant : jusqu'ici
# uniquement un fond orange. On préfixe aussi le texte d'un pictogramme.
STAFF_HIGHLIGHT_MARKER = "★ "  # étoile pleine « ★ »

# Patient « à valider » : jusqu'ici uniquement le bouton Valider en rouge.
VALIDATE_ALERT_MARKER = "⚠"    # ⚠


def staff_highlight_text(text):
    """Ajoute le marqueur « équipier » devant un libellé de patient de la file."""
    if text.startswith(STAFF_HIGHLIGHT_MARKER.strip()):
        return text
    return f"{STAFF_HIGHLIGHT_MARKER}{text}"


def validate_alert_text(base_label):
    """Libellé du bouton Valider en état d'alerte (« à valider »).

    On préfixe le libellé de base (qui peut contenir un raccourci sur une 2e
    ligne) par le pictogramme d'alerte, sans le dupliquer."""
    if base_label.startswith(VALIDATE_ALERT_MARKER):
        return base_label
    return f"{VALIDATE_ALERT_MARKER} {base_label}"


# --- Contraste WCAG -------------------------------------------------------

# Noms de couleurs utilisés dans le code app / que l'on veut pouvoir auditer.
# (sous-ensemble des couleurs nommées CSS/SVG, en minuscules).
_NAMED_COLORS = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "orange": (255, 165, 0),
    "lightgreen": (144, 238, 144),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "silver": (192, 192, 192),
    "yellow": (255, 255, 0),
    "transparent": None,
}


def parse_color(value):
    """Convertit une couleur (hex ``#rgb``/``#rrggbb`` ou nom CSS connu) en
    ``(r, g, b)``. Retourne ``None`` si la couleur est inconnue, transparente ou
    non gérée (l'appelant décide alors de l'ignorer)."""
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) == 3:
        try:
            return tuple(max(0, min(255, int(c))) for c in value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text in _NAMED_COLORS:
        return _NAMED_COLORS[text]
    if text.startswith("rgb"):
        # rgb(r, g, b) / rgba(r, g, b, a). L'alpha est ignoré (approximation :
        # on évalue le contraste comme si la couleur était opaque).
        inside = text[text.find("(") + 1:text.rfind(")")]
        parts = [p.strip() for p in inside.split(",") if p.strip() != ""]
        if len(parts) >= 3:
            try:
                return tuple(max(0, min(255, int(round(float(p))))) for p in parts[:3])
            except (TypeError, ValueError):
                return None
        return None
    if text.startswith("#"):
        hex_part = text[1:]
        if len(hex_part) == 3 and all(c in "0123456789abcdef" for c in hex_part):
            return tuple(int(c * 2, 16) for c in hex_part)
        if len(hex_part) == 6 and all(c in "0123456789abcdef" for c in hex_part):
            return (int(hex_part[0:2], 16),
                    int(hex_part[2:4], 16),
                    int(hex_part[4:6], 16))
    return None


def _channel_luminance(channel_8bit):
    cs = channel_8bit / 255.0
    if cs <= 0.03928:
        return cs / 12.92
    return ((cs + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb):
    """Luminance relative WCAG d'une couleur ``(r, g, b)`` (0..1)."""
    r, g, b = rgb
    return (0.2126 * _channel_luminance(r)
            + 0.7152 * _channel_luminance(g)
            + 0.0722 * _channel_luminance(b))


def contrast_ratio(color_a, color_b):
    """Ratio de contraste WCAG entre deux couleurs (1..21).

    Accepte hex, noms connus ou tuples. Lève ``ValueError`` si une couleur ne
    peut pas être interprétée : le calcul de contraste n'a alors aucun sens."""
    a = parse_color(color_a)
    b = parse_color(color_b)
    if a is None or b is None:
        raise ValueError(f"Couleur illisible pour le contraste : {color_a!r} / {color_b!r}")
    la = relative_luminance(a)
    lb = relative_luminance(b)
    lighter, darker = (la, lb) if la >= lb else (lb, la)
    return (lighter + 0.05) / (darker + 0.05)


# Seuils WCAG 2.1.
AA_NORMAL = 4.5
AA_LARGE = 3.0


def passes_aa(foreground, background, large_text=False):
    """Vrai si le couple texte/fond respecte le contraste AA (4.5:1, ou 3:1 pour
    du grand texte)."""
    threshold = AA_LARGE if large_text else AA_NORMAL
    return contrast_ratio(foreground, background) >= threshold
