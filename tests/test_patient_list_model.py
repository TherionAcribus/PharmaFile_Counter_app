"""Tests du modèle Qt de la file des patients (point 21).

Deux niveaux :
  - fonctions pures (``compute_list_diff``, ``patient_display_text``,
    ``patient_is_staff_highlight``) testées sans Qt ;
  - ``PatientListModel`` testé avec le vrai PySide6 (QGuiApplication offscreen) :
    on vérifie que les mises à jour sont DIFFÉRENTIELLES — un ajout n'émet qu'une
    insertion, un retrait qu'une suppression, un changement de contenu qu'un
    dataChanged — et surtout qu'aucune réinitialisation complète du modèle
    (modelReset) n'a lieu (preuve qu'on ne reconstruit pas toute la liste).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

# Backend graphique hors-écran : QFont/QBrush (créés par le modèle) nécessitent
# une QGuiApplication mais pas d'affichage réel.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from patient_list_model import (  # noqa: E402
    MAX_DISPLAYED_PATIENTS,
    PatientListModel,
    compute_list_diff,
    patient_display_text,
    patient_is_staff_highlight,
)


# --------------------------------------------------------------------------
# Fonctions pures (sans Qt)
# --------------------------------------------------------------------------

def _apply(old_ids, ops):
    """Applique la suite d'opérations de compute_list_diff pour vérifier
    qu'elle transforme bien old_ids en la cible."""
    cur = list(old_ids)
    for op in ops:
        if op[0] == "remove":
            del cur[op[1]]
        else:
            cur.insert(op[1], op[2])
    return cur


@pytest.mark.parametrize("old,new", [
    ([], []),
    ([1, 2, 3], [1, 2, 3]),          # inchangé
    ([1, 2, 3], [1, 2, 3, 4]),       # ajout en fin
    ([1, 2, 3], [0, 1, 2, 3]),       # ajout en tête
    ([1, 2, 3], [1, 3]),             # retrait au milieu
    ([1, 2, 3], []),                 # tout retirer
    ([], [1, 2, 3]),                 # tout ajouter
    ([1, 2, 3], [3, 2, 1]),          # inversion complète
    ([1, 2, 3, 4], [1, 3, 2, 4]),    # échange de deux voisins
    ([1, 2, 3], [4, 5, 6]),          # remplacement total
])
def test_compute_list_diff_transforms_correctly(old, new):
    ops = compute_list_diff(old, new)
    assert _apply(old, ops) == new


def test_diff_no_change_is_empty():
    assert compute_list_diff([1, 2, 3], [1, 2, 3]) == []


def test_diff_append_one_is_single_insert():
    ops = compute_list_diff([1, 2, 3], [1, 2, 3, 4])
    assert ops == [("insert", 3, 4)]


def test_diff_remove_one_is_single_remove():
    ops = compute_list_diff([1, 2, 3], [1, 3])
    assert ops == [("remove", 1)]


def test_patient_display_text_variants():
    assert patient_display_text({"call_number": "A-1", "language_code": "fr"}) == "A-1"
    assert patient_display_text(
        {"call_number": "A-1", "language_code": "en"}) == "A-1 (en)"
    assert patient_display_text(
        {"call_number": "A-1", "activity_is_staff": 7, "activity": "Ordo",
         "language_code": "fr"}) == "A-1 -> Ordo"


def test_staff_highlight():
    assert patient_is_staff_highlight({"activity_is_staff": 7}, 7) is True
    assert patient_is_staff_highlight({"activity_is_staff": 7}, 3) is False
    # staff_id faux ne doit pas surligner les patients sans activité staff.
    assert patient_is_staff_highlight({"activity_is_staff": None}, None) is False
    assert patient_is_staff_highlight({"activity_is_staff": 0}, 0) is False


# --------------------------------------------------------------------------
# Modèle Qt (PySide6 réel, offscreen)
# --------------------------------------------------------------------------

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance() or QGuiApplication([])
    yield app


class SignalCounter:
    """Compte les émissions des signaux de mutation d'un modèle."""

    def __init__(self, model):
        self.inserted = 0
        self.removed = 0
        self.data_changed = 0
        self.reset = 0
        model.rowsInserted.connect(lambda *a: self._inc("inserted"))
        model.rowsRemoved.connect(lambda *a: self._inc("removed"))
        model.dataChanged.connect(lambda *a: self._inc("data_changed"))
        model.modelReset.connect(lambda *a: self._inc("reset"))

    def _inc(self, name):
        setattr(self, name, getattr(self, name) + 1)


def _patient(pid, call="A", activity="Ordo", staff=None, lang="fr"):
    return {
        "id": pid, "call_number": call, "activity": activity,
        "activity_is_staff": staff, "language_code": lang,
    }


def test_set_patients_populates_rows(qapp):
    m = PatientListModel()
    m.set_patients([_patient(1), _patient(2), _patient(3)])
    assert m.rowCount() == 3
    assert m.id_at(0) == 1
    assert m.data(m.index(2, 0), PatientListModel.IdRole) == 3


def test_append_emits_single_insert_no_reset(qapp):
    m = PatientListModel()
    m.set_patients([_patient(1), _patient(2)])
    c = SignalCounter(m)
    m.set_patients([_patient(1), _patient(2), _patient(3)])
    assert m.rowCount() == 3
    assert c.inserted == 1
    assert c.removed == 0
    assert c.reset == 0            # PAS de reconstruction complète
    assert c.data_changed == 0


def test_remove_emits_single_remove_no_reset(qapp):
    m = PatientListModel()
    m.set_patients([_patient(1), _patient(2), _patient(3)])
    c = SignalCounter(m)
    m.set_patients([_patient(1), _patient(3)])  # retire le 2
    assert m.rowCount() == 2
    assert c.removed == 1
    assert c.inserted == 0
    assert c.reset == 0


def test_content_change_emits_only_datachanged(qapp):
    m = PatientListModel()
    m.set_patients([_patient(1, call="A"), _patient(2, call="B")])
    c = SignalCounter(m)
    # Seul le patient 2 change d'activité : un seul dataChanged, aucune structure.
    m.set_patients([_patient(1, call="A"), _patient(2, call="B", activity="Autre")])
    assert c.data_changed == 1
    assert c.inserted == 0
    assert c.removed == 0
    assert c.reset == 0
    assert m.data(m.index(1, 0), Qt.DisplayRole) == "B"


def test_identical_update_emits_nothing(qapp):
    m = PatientListModel()
    patients = [_patient(1), _patient(2), _patient(3)]
    m.set_patients(patients)
    c = SignalCounter(m)
    m.set_patients([_patient(1), _patient(2), _patient(3)])  # contenu identique
    assert c.inserted == 0
    assert c.removed == 0
    assert c.data_changed == 0
    assert c.reset == 0


def test_reorder_preserves_ids(qapp):
    m = PatientListModel()
    m.set_patients([_patient(1), _patient(2), _patient(3)])
    c = SignalCounter(m)
    m.set_patients([_patient(3), _patient(2), _patient(1)])
    assert [m.id_at(i) for i in range(3)] == [3, 2, 1]
    assert c.reset == 0  # réordonnancement sans reconstruction complète


def test_display_role_and_staff_background(qapp):
    from accessibility import STAFF_HIGHLIGHT_MARKER
    m = PatientListModel()
    m.set_staff_id(7)
    m.set_patients([_patient(1, call="A-1", activity="Ordo", staff=7, lang="en")])
    idx = m.index(0, 0)
    # Accessibilité (point 28) : le libellé d'un patient assigné à l'équipier
    # courant porte un marqueur texte en plus du fond orange.
    assert m.data(idx, Qt.DisplayRole) == f"{STAFF_HIGHLIGHT_MARKER}A-1 -> Ordo (en)"
    assert m.data(idx, PatientListModel.PatientRole)["id"] == 1
    # Fond orange car l'activité est assignée à l'équipier courant.
    assert m.data(idx, Qt.BackgroundRole) is not None
    assert m.data(idx, Qt.FontRole) is not None


def test_display_role_no_marker_when_not_staff(qapp):
    from accessibility import STAFF_HIGHLIGHT_MARKER
    m = PatientListModel()
    m.set_staff_id(3)
    m.set_patients([_patient(1, call="A-1", activity="Ordo", staff=7, lang="fr")])
    text = m.data(m.index(0, 0), Qt.DisplayRole)
    assert not text.startswith(STAFF_HIGHLIGHT_MARKER)
    assert text == "A-1 -> Ordo"


def test_staff_marker_appears_and_disappears_with_staff_id(qapp):
    from accessibility import STAFF_HIGHLIGHT_MARKER
    m = PatientListModel()
    m.set_patients([_patient(1, call="A-1", staff=7, lang="fr")])
    idx = m.index(0, 0)
    assert not m.data(idx, Qt.DisplayRole).startswith(STAFF_HIGHLIGHT_MARKER)
    m.set_staff_id(7)
    assert m.data(idx, Qt.DisplayRole).startswith(STAFF_HIGHLIGHT_MARKER)


def test_font_size_is_configurable_and_floored(qapp):
    from accessibility import MIN_FONT_POINT_SIZE
    m = PatientListModel(font_size=16)
    m.set_patients([_patient(1)])
    assert m.data(m.index(0, 0), Qt.FontRole).pointSize() == 16
    # Sous le plancher : borné au minimum lisible.
    m.set_font_size(2)
    assert m.data(m.index(0, 0), Qt.FontRole).pointSize() == MIN_FONT_POINT_SIZE


def test_set_font_size_emits_only_when_changed(qapp):
    m = PatientListModel(font_size=12)
    m.set_patients([_patient(1), _patient(2)])
    c = SignalCounter(m)
    m.set_font_size(14)
    assert c.data_changed == 1
    assert c.reset == 0
    c2 = SignalCounter(m)
    m.set_font_size(14)  # inchangé -> aucun signal
    assert c2.data_changed == 0


def test_no_background_when_not_staff_match(qapp):
    m = PatientListModel()
    m.set_staff_id(3)
    m.set_patients([_patient(1, staff=7)])
    assert m.data(m.index(0, 0), Qt.BackgroundRole) is None


def test_set_staff_id_refreshes_highlight(qapp):
    m = PatientListModel()
    m.set_patients([_patient(1, staff=7)])
    c = SignalCounter(m)
    m.set_staff_id(7)  # change -> dataChanged sur les lignes
    assert c.data_changed == 1
    assert c.reset == 0
    # Même valeur : aucun signal.
    c2 = SignalCounter(m)
    m.set_staff_id(7)
    assert c2.data_changed == 0


def test_ignores_missing_and_duplicate_ids(qapp):
    m = PatientListModel()
    m.set_patients([
        _patient(1),
        {"call_number": "no-id", "language_code": "fr"},  # sans id -> ignoré
        _patient(1, call="dup"),                          # id dupliqué -> ignoré
        _patient(2),
    ])
    assert m.rowCount() == 2
    assert [m.id_at(i) for i in range(2)] == [1, 2]


def test_truncates_beyond_max(qapp):
    m = PatientListModel()
    m.set_patients([_patient(i) for i in range(MAX_DISPLAYED_PATIENTS + 50)])
    assert m.rowCount() == MAX_DISPLAYED_PATIENTS


def test_empty_then_clears(qapp):
    m = PatientListModel()
    m.set_patients([_patient(1), _patient(2)])
    c = SignalCounter(m)
    m.set_patients([])
    assert m.rowCount() == 0
    assert c.removed >= 1
    assert c.reset == 0
