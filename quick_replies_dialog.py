"""Fenêtre « Mes phrases rapides » de la messagerie.

L'équipier y gère SES phrases du menu « ⚡ » : elles s'ajoutent aux phrases
d'équipe (réglées dans l'administration) et sont rangées sur le serveur, sur
sa fiche — il les retrouve sur tous les comptoirs. La fenêtre ne fait aucun
appel réseau : elle rend la liste, le contrôleur de messagerie l'enregistre.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QStyledItemDelegate,
    QVBoxLayout,
)

from screen_fit import fit_on_show

#: Limites de repli (serveur ancien) — les vraies viennent de l'état de
#: messagerie (``quick_replies.max_personal`` / ``max_length``).
DEFAULT_MAX_COUNT = 12
DEFAULT_MAX_LENGTH = 120

_SPACES = re.compile(r"\s+")


def normalize_phrase(text) -> str:
    """Même règle que le serveur : espaces et retours ramenés à une espace."""
    return _SPACES.sub(" ", text or "").strip()


def clean_phrases(values, max_length=DEFAULT_MAX_LENGTH) -> list[str]:
    """Phrases normalisées, coupées à ``max_length``, sans vide ni doublon
    (casse ignorée), dans l'ordre donné."""
    seen = set()
    result = []
    for value in values:
        phrase = normalize_phrase(value)[:max_length].strip()
        folded = phrase.casefold()
        if phrase and folded not in seen:
            seen.add(folded)
            result.append(phrase)
    return result


class _LimitedEditDelegate(QStyledItemDelegate):
    """Édition en place bornée à la longueur maximale d'une phrase."""

    def __init__(self, max_length, parent=None):
        super().__init__(parent)
        self._max_length = max_length

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            editor.setMaxLength(self._max_length)
        return editor


class QuickRepliesDialog(QDialog):
    def __init__(self, phrases, team_phrases=(), max_count=DEFAULT_MAX_COUNT,
                 max_length=DEFAULT_MAX_LENGTH, parent=None):
        super().__init__(parent)
        self.max_count = max(1, int(max_count or DEFAULT_MAX_COUNT))
        self.max_length = max(1, int(max_length or DEFAULT_MAX_LENGTH))
        self.setWindowTitle("Mes phrases rapides")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Vos phrases s'ajoutent à celles de l'équipe dans le menu « ⚡ » "
            "de la messagerie. Elles vous suivent sur tous les comptoirs.", self)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        team = [p for p in team_phrases if isinstance(p, str) and p]
        if team:
            # Rappel des phrases d'équipe : évite d'ajouter un doublon.
            self.team_label = QLabel(
                "Phrases de l'équipe (réglées dans l'administration) : "
                + " · ".join(team), self)
            self.team_label.setWordWrap(True)
            self.team_label.setEnabled(False)
            layout.addWidget(self.team_label)
        else:
            self.team_label = None

        self.list = QListWidget(self)
        self.list.setAccessibleName("Mes phrases rapides")
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.list.setItemDelegate(_LimitedEditDelegate(self.max_length, self.list))
        for phrase in clean_phrases(phrases, self.max_length)[:self.max_count]:
            self._append_item(phrase)

        body = QHBoxLayout()
        body.addWidget(self.list, 1)
        side = QVBoxLayout()
        self.edit_button = QPushButton("&Modifier", self)
        self.edit_button.setToolTip("Modifier la phrase sélectionnée (F2)")
        self.edit_button.clicked.connect(self._edit_current)
        self.remove_button = QPushButton("&Supprimer", self)
        self.remove_button.setToolTip("Supprimer la phrase sélectionnée (Suppr)")
        self.remove_button.clicked.connect(self._remove_current)
        self.up_button = QPushButton("M&onter", self)
        self.up_button.setToolTip("Monter dans le menu (Ctrl+Haut)")
        self.up_button.clicked.connect(lambda: self._move_current(-1))
        self.down_button = QPushButton("&Descendre", self)
        self.down_button.setToolTip("Descendre dans le menu (Ctrl+Bas)")
        self.down_button.clicked.connect(lambda: self._move_current(1))
        for button in (self.edit_button, self.remove_button,
                       self.up_button, self.down_button):
            side.addWidget(button)
        side.addStretch(1)
        body.addLayout(side)
        layout.addLayout(body)

        add_row = QHBoxLayout()
        self.new_phrase = QLineEdit(self)
        self.new_phrase.setPlaceholderText("Nouvelle phrase…")
        self.new_phrase.setAccessibleName("Nouvelle phrase")
        self.new_phrase.setMaxLength(self.max_length)
        self.new_phrase.returnPressed.connect(self._add_phrase)
        self.new_phrase.textChanged.connect(self._update_buttons)
        self.add_button = QPushButton("&Ajouter", self)
        self.add_button.clicked.connect(self._add_phrase)
        add_row.addWidget(self.new_phrase, 1)
        add_row.addWidget(self.add_button)
        layout.addLayout(add_row)

        self.count_label = QLabel(self)
        self.count_label.setEnabled(False)
        layout.addWidget(self.count_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Enregistrer")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.list,
                  self._remove_current,
                  context=Qt.ShortcutContext.WidgetShortcut)
        QShortcut(QKeySequence("Ctrl+Up"), self.list,
                  lambda: self._move_current(-1),
                  context=Qt.ShortcutContext.WidgetShortcut)
        QShortcut(QKeySequence("Ctrl+Down"), self.list,
                  lambda: self._move_current(1),
                  context=Qt.ShortcutContext.WidgetShortcut)

        self.list.currentRowChanged.connect(self._update_buttons)
        self.list.model().rowsMoved.connect(self._update_buttons)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.new_phrase.setFocus()
        self._update_buttons()

    # --- liste ------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        # Petit écran : la fenêtre est ramenée dans la zone utile (screen_fit).
        fit_on_show(self)

    def _append_item(self, phrase):
        item = QListWidgetItem(phrase)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.list.addItem(item)
        return item

    def phrases(self) -> list[str]:
        """Liste à enregistrer, nettoyée, dans l'ordre affiché."""
        texts = [self.list.item(row).text() for row in range(self.list.count())]
        return clean_phrases(texts, self.max_length)[:self.max_count]

    def _existing(self):
        return {normalize_phrase(self.list.item(row).text()).casefold()
                for row in range(self.list.count())}

    def _add_phrase(self):
        phrase = normalize_phrase(self.new_phrase.text())[:self.max_length].strip()
        if not phrase or self.list.count() >= self.max_count:
            return
        if phrase.casefold() in self._existing():
            # Déjà présente : on la sélectionne plutôt que de la doubler.
            for row in range(self.list.count()):
                if normalize_phrase(self.list.item(row).text()).casefold() == phrase.casefold():
                    self.list.setCurrentRow(row)
                    break
            self.new_phrase.clear()
            return
        item = self._append_item(phrase)
        self.list.setCurrentItem(item)
        self.new_phrase.clear()
        self._update_buttons()

    def _edit_current(self):
        item = self.list.currentItem()
        if item is not None:
            self.list.editItem(item)

    def _remove_current(self):
        row = self.list.currentRow()
        if row < 0:
            return
        self.list.takeItem(row)
        if self.list.count():
            self.list.setCurrentRow(min(row, self.list.count() - 1))
        self._update_buttons()

    def _move_current(self, step):
        row = self.list.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)
        self._update_buttons()

    def _update_buttons(self, *_args):
        row = self.list.currentRow()
        count = self.list.count()
        has_item = row >= 0
        self.edit_button.setEnabled(has_item)
        self.remove_button.setEnabled(has_item)
        self.up_button.setEnabled(has_item and row > 0)
        self.down_button.setEnabled(has_item and row < count - 1)
        full = count >= self.max_count
        self.new_phrase.setEnabled(not full)
        self.add_button.setEnabled(
            not full and bool(normalize_phrase(self.new_phrase.text())))
        text = f"{count} / {self.max_count} phrases"
        if full:
            text += " — supprimez-en une pour en ajouter une autre."
        self.count_label.setText(text)
