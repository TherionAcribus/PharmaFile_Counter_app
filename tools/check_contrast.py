"""Audit de contraste des skins (.qss) — point 28.

Pour chaque bloc de règle QSS qui définit *à la fois* une couleur de texte
(``color``) et un fond (``background-color`` ou ``background``), on calcule le
ratio de contraste WCAG entre le texte et le fond. Pour un fond en dégradé
(``qlineargradient`` / ``qradialgradient``), on évalue le contraste contre
*chaque* arrêt de couleur et on retient le pire cas (le plus faible ratio) :
le texte doit rester lisible sur toute l'étendue du dégradé.

Les couleurs non interprétables (variables, motifs, ``palette(...)``) sont
ignorées : l'outil est un garde-fou, pas un validateur exhaustif du langage QSS.

Usage :
    python tools/check_contrast.py [--threshold 4.5] [skins/Xxx.qss ...]

Sans argument, audite tous les ``skins/*.qss``. Code de sortie non nul si au
moins un couple texte/fond échoue au seuil (par défaut AA normal, 4.5:1).
"""

import argparse
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from accessibility import AA_NORMAL, contrast_ratio, parse_color  # noqa: E402

# Un bloc = "selecteur { ... }". On ignore les commentaires /* ... */ au préalable.
_BLOCK_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# Arrêts de couleur d'un dégradé : "stop:0 rgba(...)", "stop:1 #rrggbb", etc.
_STOP_RE = re.compile(r"stop\s*:\s*[0-9.]+\s+([^,)]+(?:\([^)]*\))?)")

# On restreint l'audit au texte réellement affiché et actif :
#  - les composants désactivés sont exemptés du contraste par WCAG 2.1 (§1.4.3,
#    « inactive user interface component ») ; leur grisé volontaire est légitime ;
#  - les pseudo-éléments décoratifs ne portent pas de texte (séparateurs,
#    indicateurs, poignées, flèches, barres de progression...) : y mesurer un
#    « contraste texte/fond » n'a pas de sens.
_SKIP_SELECTOR_MARKERS = (
    "disabled",
    "::separator", "::indicator", "::chunk", "::handle", "::groove",
    "::add-line", "::sub-line", "::up-arrow", "::down-arrow",
    "::up-button", "::down-button", "::branch", "::corner",
    "::scroller", "::menu-indicator", "::drop-down", "::tab-bar",
)


def _is_text_selector(selector):
    lowered = selector.lower()
    return not any(marker in lowered for marker in _SKIP_SELECTOR_MARKERS)


def _declarations(block_body):
    """Dict propriété -> valeur (dernière gagnante) pour un corps de bloc."""
    decls = {}
    for piece in block_body.split(";"):
        if ":" not in piece:
            continue
        prop, _, value = piece.partition(":")
        decls[prop.strip().lower()] = value.strip()
    return decls


def _background_colors(value):
    """Liste de couleurs d'un fond : la couleur simple, ou tous les arrêts d'un
    dégradé. Retourne les valeurs brutes (chaînes) à faire parser ensuite."""
    lowered = value.lower()
    if "gradient" in lowered:
        return _STOP_RE.findall(value)
    return [value]


def audit_text(qss_text, threshold=AA_NORMAL):
    """Retourne la liste des échecs ``(selecteur, texte, fond, ratio)`` d'un QSS."""
    stripped = _COMMENT_RE.sub("", qss_text)
    failures = []
    for selector, body in _BLOCK_RE.findall(stripped):
        if not _is_text_selector(selector):
            continue
        decls = _declarations(body)
        fg_raw = decls.get("color")
        bg_raw = decls.get("background-color") or decls.get("background")
        if not fg_raw or not bg_raw:
            continue
        fg = parse_color(fg_raw)
        if fg is None:
            continue
        for bg_candidate in _background_colors(bg_raw):
            bg = parse_color(bg_candidate)
            if bg is None:
                continue
            ratio = contrast_ratio(fg, bg)
            if ratio < threshold:
                failures.append((selector.strip(), fg_raw, bg_candidate.strip(), ratio))
    return failures


def audit_file(path, threshold=AA_NORMAL):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return audit_text(fh.read(), threshold)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit de contraste des skins QSS")
    parser.add_argument("--threshold", type=float, default=AA_NORMAL,
                        help="Seuil de contraste (défaut : 4.5, AA normal)")
    parser.add_argument("files", nargs="*", help="Fichiers .qss (défaut : skins/*.qss)")
    args = parser.parse_args(argv)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = args.files or sorted(glob.glob(os.path.join(root, "skins", "*.qss")))

    total_failures = 0
    for path in files:
        failures = audit_file(path, args.threshold)
        name = os.path.basename(path)
        if failures:
            total_failures += len(failures)
            print(f"[ÉCHEC] {name} — {len(failures)} couple(s) sous {args.threshold}:1")
            for selector, fg, bg, ratio in failures:
                print(f"    {ratio:4.2f}:1  texte {fg} / fond {bg}  ({selector})")
        else:
            print(f"[OK]    {name}")

    if total_failures:
        print(f"\n{total_failures} couple(s) texte/fond insuffisant(s).")
        return 1
    print("\nTous les couples texte/fond audités respectent le seuil.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
