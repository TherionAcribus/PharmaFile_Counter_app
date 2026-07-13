"""Tests du module pur ``accessibility`` (point 28).

Aucune dépendance Qt : marqueurs d'état, sévérité/couleurs des notifications,
titres selon le ton, bornage de police et contraste WCAG.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import accessibility as a  # noqa: E402


# --- Bornage de la taille de police --------------------------------------

def test_clamp_font_size_applies_floor():
    assert a.clamp_font_size(4) == a.MIN_FONT_POINT_SIZE
    assert a.clamp_font_size(a.MIN_FONT_POINT_SIZE - 3) == a.MIN_FONT_POINT_SIZE


def test_clamp_font_size_applies_ceiling():
    assert a.clamp_font_size(999) == a.MAX_FONT_POINT_SIZE


def test_clamp_font_size_passes_valid_value():
    assert a.clamp_font_size(11) == 11


def test_clamp_font_size_handles_garbage():
    assert a.clamp_font_size(None) == a.MIN_FONT_POINT_SIZE
    assert a.clamp_font_size("abc") == a.MIN_FONT_POINT_SIZE
    assert a.clamp_font_size("14") == 14  # chaîne numérique acceptée


# --- Sévérité et pictogrammes --------------------------------------------

def test_notification_severity_mapping():
    assert a.notification_severity("no_paper") == a.CRITICAL
    assert a.notification_severity("low_paper") == a.WARNING
    assert a.notification_severity("paper_ok") == a.SUCCESS
    assert a.notification_severity("new_patient") == a.INFO
    # origin inconnu -> info par défaut
    assert a.notification_severity("origine_bidon") == a.INFO


def test_each_severity_has_distinct_glyph():
    glyphs = {a.severity_glyph(s) for s in (a.INFO, a.SUCCESS, a.WARNING, a.CRITICAL)}
    # Le glyphe distingue l'état en niveaux de gris : ils doivent tous différer.
    assert len(glyphs) == 4


def test_decorate_title_prefixes_glyph_once():
    decorated = a.decorate_title("Plus de papier", a.CRITICAL)
    assert decorated.startswith(a.severity_glyph(a.CRITICAL))
    assert "Plus de papier" in decorated
    # Idempotent : re-décorer ne double pas le pictogramme.
    assert a.decorate_title(decorated, a.CRITICAL) == decorated


# --- Couleurs de sévérité : valides et contrastées AA --------------------

@pytest.mark.parametrize("severity", [a.INFO, a.SUCCESS, a.WARNING, a.CRITICAL])
def test_severity_colors_are_parseable_and_pass_aa(severity):
    bg, fg = a.severity_colors(severity)
    # Couleurs interprétables (l'ancien « light_green » ne l'était pas).
    assert a.parse_color(bg) is not None
    assert a.parse_color(fg) is not None
    # Texte/fond conformes AA.
    assert a.passes_aa(fg, bg)


# --- Titres selon le ton --------------------------------------------------

def test_notification_title_sober_vs_humorous():
    sober = a.notification_title("please_validate", a.TONE_SOBER)
    humorous = a.notification_title("please_validate", a.TONE_HUMOROUS)
    assert sober == "Patient à valider"
    assert "phoque" in humorous  # l'ancien ton est préservé
    assert sober != humorous


def test_notification_title_defaults_to_sober():
    # Ton invalide -> sobre (défaut).
    assert a.notification_title("please_validate", "n'importe quoi") == \
        a.notification_title("please_validate", a.TONE_SOBER)
    assert a.DEFAULT_TONE == a.TONE_SOBER


def test_notification_title_unknown_origin_falls_back():
    assert a.notification_title("truc_inconnu", a.TONE_SOBER) == "truc_inconnu"
    assert a.notification_title("truc_inconnu", a.TONE_SOBER, fallback="X") == "X"


def test_every_known_origin_has_both_tones():
    for origin, entry in a._TITLES.items():
        assert a.TONE_SOBER in entry and entry[a.TONE_SOBER]
        assert a.TONE_HUMOROUS in entry and entry[a.TONE_HUMOROUS]


def test_normalize_tone():
    assert a.normalize_tone("HUMORISTIQUE") == a.TONE_HUMOROUS
    assert a.normalize_tone(None) == a.TONE_SOBER
    assert a.normalize_tone("sobre") == a.TONE_SOBER


# --- Marqueurs d'états signalés ailleurs par la couleur -------------------

def test_staff_highlight_text_adds_marker_once():
    marked = a.staff_highlight_text("A12")
    assert marked.startswith(a.STAFF_HIGHLIGHT_MARKER)
    assert "A12" in marked
    assert a.staff_highlight_text(marked) == marked  # pas de double marqueur


def test_validate_alert_text_adds_marker_once():
    marked = a.validate_alert_text("Valider\nCtrl+V")
    assert marked.startswith(a.VALIDATE_ALERT_MARKER)
    assert "Valider" in marked
    assert a.validate_alert_text(marked) == marked


# --- Contraste WCAG -------------------------------------------------------

def test_contrast_ratio_extremes():
    # Noir/blanc = 21:1 (contraste maximal).
    assert round(a.contrast_ratio("#000000", "#ffffff"), 1) == 21.0
    # Identiques = 1:1.
    assert a.contrast_ratio("#777777", "#777777") == 1.0


def test_contrast_ratio_is_symmetric():
    assert a.contrast_ratio("#123456", "#abcdef") == a.contrast_ratio("#abcdef", "#123456")


def test_parse_color_formats():
    assert a.parse_color("#fff") == (255, 255, 255)
    assert a.parse_color("#ff8800") == (255, 136, 0)
    assert a.parse_color("black") == (0, 0, 0)
    assert a.parse_color("rgb(255, 165, 0)") == (255, 165, 0)
    assert a.parse_color("rgba(16, 32, 48, 128)") == (16, 32, 48)
    assert a.parse_color("light_green") is None  # ancien nom invalide
    assert a.parse_color("palette(window)") is None


def test_passes_aa_threshold():
    assert a.passes_aa("#ffffff", "#c0392b")       # blanc/rouge sombre : OK
    assert not a.passes_aa("#ffffff", "#e67e22")   # blanc/orange : insuffisant en AA normal
    assert a.passes_aa("#1a1a1a", "#e67e22")       # texte foncé/orange : OK


def test_contrast_ratio_raises_on_unparseable():
    with pytest.raises(ValueError):
        a.contrast_ratio("light_green", "#ffffff")
