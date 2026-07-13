"""Garde-fou : tous les skins doivent respecter le contraste AA sur le texte
*actif* (point 28).

On réutilise l'auditeur ``tools/check_contrast`` (qui exclut les composants
désactivés — exemptés par WCAG — et les pseudo-éléments décoratifs sans texte).
Ce test échoue si une retouche future réintroduit un couple texte/fond en dessous
du seuil AA (4.5:1).
"""

import glob
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from tools.check_contrast import audit_file, audit_text  # noqa: E402

SKINS = sorted(glob.glob(os.path.join(ROOT, "skins", "*.qss")))


def test_skins_are_present():
    assert SKINS, "Aucun skin trouvé"


@pytest.mark.parametrize("path", SKINS, ids=lambda p: os.path.basename(p))
def test_skin_active_text_passes_aa(path):
    failures = audit_file(path)
    assert not failures, "Couples texte/fond insuffisants : " + "; ".join(
        f"{ratio:.2f}:1 {fg}/{bg} ({sel})" for sel, fg, bg, ratio in failures)


def test_auditor_flags_low_contrast_pair():
    # Contrôle négatif : l'auditeur détecte bien un texte gris sur fond gris.
    qss = "QLabel { color: #777777; background-color: #808080; }"
    assert audit_text(qss), "L'auditeur aurait dû signaler un contraste insuffisant"


def test_auditor_ignores_disabled_state():
    # Un composant désactivé (grisé volontaire) est exempté : pas un échec.
    qss = "QPushButton:disabled { color: #656565; background-color: #404040; }"
    assert audit_text(qss) == []


def test_auditor_ignores_gradient_stops_worst_case():
    # Sur un dégradé, le pire arrêt doit être évalué : ici blanc sur un arrêt
    # clair -> échec attendu.
    qss = ("QWidget { color: #ffffff; background-color: "
           "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #101010, stop:1 #dddddd); }")
    assert audit_text(qss)
