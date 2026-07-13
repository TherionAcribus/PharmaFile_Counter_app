"""Modèle Qt pour la file des patients (point 21).

Avant : à chaque évènement `new_patient`, tous les boutons patients de la vue
étaient supprimés puis recréés (clignotement, perte de la position de défilement,
coût O(n) même pour un seul changement).

Ici on expose la file via un ``QAbstractListModel`` consommé par un ``QListView``.
Les mises à jour sont *différentielles* : on identifie chaque patient par son
``id`` et on n'émet que les insertions / suppressions / changements de contenu
réellement survenus. Un seul patient qui change ne reconstruit donc pas la liste,
et la vue conserve sa position de défilement.

La logique de diff (``compute_list_diff``) et la mise en forme du texte
(``patient_display_text``) sont des fonctions pures, testables sans Qt.
"""

import logging

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont

from accessibility import (
    DEFAULT_LIST_FONT_SIZE,
    clamp_font_size,
    staff_highlight_text,
)

logger = logging.getLogger("appcomptoir.patient_list_model")

# Au-delà de ce nombre, on tronque l'affichage : un QListView virtualise déjà le
# rendu (seuls les éléments visibles sont peints), mais cette borne protège des
# cas pathologiques (file anormalement longue) côté modèle et diff.
MAX_DISPLAYED_PATIENTS = 500

# Couleur de fond pour un patient dont l'activité est assignée à l'équipier
# courant (identique à l'ancien surlignage orange des PatientButton).
_STAFF_HIGHLIGHT_BG = "#f98517"
_STAFF_HIGHLIGHT_FG = "#000000"


def patient_display_text(patient):
    """Texte affiché pour un patient (identique à l'ancien libellé de bouton)."""
    text = str(patient.get("call_number", ""))
    if patient.get("activity_is_staff"):
        text += f" -> {patient.get('activity', '')}"
    language_code = patient.get("language_code")
    if language_code and language_code != "fr":
        text += f" ({language_code})"
    return text


def patient_is_staff_highlight(patient, staff_id):
    """Vrai si l'activité du patient est assignée à l'équipier courant.

    Reproduit l'ancienne condition ``self.staff_id == patient['activity_is_staff']``.
    On exige un staff_id « vrai » pour éviter qu'une valeur fausse (None/False/0)
    ne surligne les patients sans activité staff (``activity_is_staff`` falsy).
    """
    if not staff_id:
        return False
    return staff_id == patient.get("activity_is_staff")


def compute_list_diff(old_ids, new_ids):
    """Calcule la suite minimale d'opérations transformant ``old_ids`` en
    ``new_ids`` à l'aide de deux primitives appliquées *dans l'ordre* :

        ("remove", index)        -> supprime la ligne à ``index``
        ("insert", index, id)    -> insère ``id`` à ``index``

    Les identifiants sont supposés uniques. Le consommateur applique les
    opérations dans l'ordre retourné sur la même structure ; les index restent
    donc valides au fil des mutations.

    Cas courants (le point sensible du refactor) :
      - ajout d'un patient en fin de file : 1 insertion ;
      - retrait d'un patient : 1 suppression ;
      - même file, contenu inchangé : 0 opération.
    """
    cur = list(old_ids)
    new_set = set(new_ids)
    ops = []

    # Phase 1 — suppressions : on retire (du bas vers le haut, pour garder les
    # index valides) tout id absent de la nouvelle file.
    for i in range(len(cur) - 1, -1, -1):
        if cur[i] not in new_set:
            ops.append(("remove", i))
            del cur[i]

    # Phase 2 — on aligne l'ordre. ``cur`` ne contient plus que des ids présents
    # dans new_ids ; on place chaque id cible à sa position définitive.
    i = 0
    while i < len(new_ids):
        target = new_ids[i]
        if i < len(cur) and cur[i] == target:
            i += 1
            continue
        # L'id cible est-il présent plus loin (déplacement) ou nouveau (insertion) ?
        j = None
        for k in range(i + 1, len(cur)):
            if cur[k] == target:
                j = k
                break
        if j is not None:
            ops.append(("remove", j))
            del cur[j]
        ops.append(("insert", i, target))
        cur.insert(i, target)
        i += 1

    return ops


class PatientListModel(QAbstractListModel):
    """File des patients pour un ``QListView``, mise à jour de façon
    différentielle et identifiée par ``id``."""

    IdRole = Qt.UserRole + 1
    PatientRole = Qt.UserRole + 2

    def __init__(self, parent=None, font_size=DEFAULT_LIST_FONT_SIZE):
        super().__init__(parent)
        self._patients = []   # liste ordonnée de dicts patient
        self._staff_id = None
        self._font = QFont()
        # Police configurable, jamais en dessous du plancher de lisibilité
        # (accessibility.clamp_font_size). L'ancienne valeur figée de 8 pt était
        # trop petite ; la taille est désormais un réglage borné.
        self._font.setPointSize(clamp_font_size(font_size))
        self._highlight_brush = QBrush(QColor(_STAFF_HIGHLIGHT_BG))
        self._highlight_fg = QBrush(QColor(_STAFF_HIGHLIGHT_FG))

    def set_font_size(self, size):
        """Change la taille de police de la liste (bornée au plancher de
        lisibilité) et rafraîchit l'affichage si elle a changé."""
        new_size = clamp_font_size(size)
        if new_size == self._font.pointSize():
            return
        self._font.setPointSize(new_size)
        if self._patients:
            top = self.index(0, 0)
            bottom = self.index(len(self._patients) - 1, 0)
            self.dataChanged.emit(top, bottom, [Qt.FontRole, Qt.SizeHintRole])

    # --- API Qt ---------------------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._patients)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._patients)):
            return None
        patient = self._patients[index.row()]
        if role == Qt.DisplayRole:
            text = patient_display_text(patient)
            # Accessibilité (point 28) : le surlignage « équipier courant » ne
            # repose plus sur la seule couleur de fond orange ; on préfixe aussi
            # le libellé d'un pictogramme, lisible en niveaux de gris.
            if patient_is_staff_highlight(patient, self._staff_id):
                text = staff_highlight_text(text)
            return text
        if role == self.IdRole:
            return patient.get("id")
        if role == self.PatientRole:
            return patient
        if role == Qt.FontRole:
            return self._font
        if patient_is_staff_highlight(patient, self._staff_id):
            if role == Qt.BackgroundRole:
                return self._highlight_brush
            if role == Qt.ForegroundRole:
                return self._highlight_fg
        return None

    # --- API application ------------------------------------------------

    def patient_at(self, row):
        if 0 <= row < len(self._patients):
            return self._patients[row]
        return None

    def id_at(self, row):
        patient = self.patient_at(row)
        return patient.get("id") if patient else None

    def set_staff_id(self, staff_id):
        """Change l'équipier courant. Le surlignage des lignes dépend de lui :
        on ne rafraîchit l'affichage que s'il a réellement changé."""
        if staff_id == self._staff_id:
            return
        self._staff_id = staff_id
        if self._patients:
            top = self.index(0, 0)
            bottom = self.index(len(self._patients) - 1, 0)
            # DisplayRole en plus du fond/texte : le marqueur « ★ » d'un patient
            # assigné à l'équipier dépend aussi de staff_id.
            self.dataChanged.emit(top, bottom,
                                  [Qt.DisplayRole, Qt.BackgroundRole, Qt.ForegroundRole])

    def set_patients(self, patients):
        """Met la file à jour de façon différentielle.

        On ne recrée jamais tout le modèle : on n'émet que les insertions,
        suppressions et ``dataChanged`` correspondant aux changements réels.
        """
        # Normalisation : on ignore les entrées sans id ou en double (un id doit
        # identifier une ligne de façon unique pour le diff).
        ordered = []
        new_by_id = {}
        for patient in (patients or []):
            if not isinstance(patient, dict):
                continue
            pid = patient.get("id")
            if pid is None or pid in new_by_id:
                continue
            new_by_id[pid] = patient
            ordered.append(patient)
            if len(ordered) >= MAX_DISPLAYED_PATIENTS:
                break

        old_ids = [p["id"] for p in self._patients]
        new_ids = [p["id"] for p in ordered]

        # 1) Ajustements structurels (insertions / suppressions / déplacements).
        for op in compute_list_diff(old_ids, new_ids):
            if op[0] == "remove":
                idx = op[1]
                self.beginRemoveRows(QModelIndex(), idx, idx)
                del self._patients[idx]
                self.endRemoveRows()
            else:  # insert
                _, idx, pid = op
                self.beginInsertRows(QModelIndex(), idx, idx)
                self._patients.insert(idx, new_by_id[pid])
                self.endInsertRows()

        # 2) Mises à jour de contenu : à ce stade l'ordre correspond à new_ids ;
        # on remplace les lignes conservées dont le contenu a changé (ex. activité
        # réassignée) et on n'émet dataChanged que pour celles-là.
        for row, patient in enumerate(ordered):
            if self._patients[row] is not patient and self._patients[row] != patient:
                self._patients[row] = patient
                index = self.index(row, 0)
                self.dataChanged.emit(index, index)
