"""Champ de saisie d'un raccourci par capture de frappe.

L'utilisateur clique dans le champ puis appuie sur la combinaison voulue :
les modificateurs s'affichent au fur et à mesure qu'ils sont enfoncés, la
touche « réelle » valide la saisie. La valeur est stockée dans le format
d'interface canonique (« Ctrl+Alt+Maj+P », jetons de
``shortcut_defaults.UI_MODIFIERS``) — le même que celui persisté dans
QSettings et relu par main.py.

  - Retour arrière / Suppr sans modificateur : efface le raccourci.
  - Échap : annule la prévisualisation en cours (et ne ferme pas le
    dialogue, la touche étant consommée quand ce champ a le focus).
  - Tab / Maj+Tab : conservés par Qt pour la navigation clavier (ils
    n'atteignent jamais keyPressEvent) — ils ne sont donc pas
    capturables comme raccourci.
"""

import logging
from PySide6.QtWidgets import QLineEdit
from PySide6 import QtCore
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

logger = logging.getLogger("appcomptoir.shortcut")

# Touches modificateurs : enfoncées seules, elles ne valident rien — elles
# préparent la combinaison (prévisualisation).
_MODIFIER_KEYS = frozenset({
    Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
    Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
})

# Modificateur Qt -> jeton d'interface (ordre canonique d'affichage et de
# sérialisation, identique à shortcut_defaults.UI_MODIFIERS).
_MOD_TOKENS = (
    (Qt.KeyboardModifier.ControlModifier, "Ctrl"),
    (Qt.KeyboardModifier.AltModifier, "Alt"),
    (Qt.KeyboardModifier.ShiftModifier, "Maj"),
    (Qt.KeyboardModifier.MetaModifier, "Win"),
)

# Touches nommées -> libellé stocké. La validation (shortcut_config) et la
# traduction ``keyboard`` insensibles à la casse retrouvent le même nom
# (« PageUp » -> « pageup », « Enter » -> « enter »…). Les touches à un
# caractère (lettres, chiffres, ponctuation) et les touches de fonction
# passent par QKeySequence dans keyPressEvent.
_NAMED_KEYS = {
    Qt.Key.Key_Space: "Space",
    Qt.Key.Key_Enter: "Enter",
    Qt.Key.Key_Return: "Enter",
    Qt.Key.Key_Escape: "Escape",
    Qt.Key.Key_Backspace: "Backspace",
    Qt.Key.Key_Delete: "Delete",
    Qt.Key.Key_Insert: "Insert",
    Qt.Key.Key_Home: "Home",
    Qt.Key.Key_End: "End",
    Qt.Key.Key_PageUp: "PageUp",
    Qt.Key.Key_PageDown: "PageDown",
    Qt.Key.Key_Up: "Up",
    Qt.Key.Key_Down: "Down",
    Qt.Key.Key_Left: "Left",
    Qt.Key.Key_Right: "Right",
    Qt.Key.Key_Pause: "Pause",
    Qt.Key.Key_Menu: "Menu",
    Qt.Key.Key_CapsLock: "CapsLock",
    Qt.Key.Key_NumLock: "NumLock",
    Qt.Key.Key_ScrollLock: "ScrollLock",
    Qt.Key.Key_Print: "PrintScreen",
}

# Sans modificateur, ces touches servent à effacer la saisie plutôt qu'à
# être capturées (convention des éditeurs de raccourci ; avec un
# modificateur — ex. « Alt+Suppr » — elles restent assignables).
_CLEAR_KEYS = frozenset({Qt.Key.Key_Backspace, Qt.Key.Key_Delete})


class ShortcutEdit(QLineEdit):
    """QLineEdit en lecture seule qui capture une combinaison de touches.

    ``shortcut()`` retourne la forme canonique persistée (« Alt+Maj+P ») ;
    ``setShortcut`` affiche et mémorise une telle forme. Le signal
    ``shortcutChanged`` transporte cette même chaîne.
    """

    shortcutChanged = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Cliquez puis tapez la combinaison")
        self._shortcut = ""

    def shortcut(self):
        """Raccourci validé, en forme canonique (« Ctrl+Alt+Maj+P »)."""
        return self._shortcut

    def setShortcut(self, text):
        self._shortcut = text or ""
        self.setText(self._shortcut)
        self.shortcutChanged.emit(self._shortcut)

    def _modifier_tokens(self, modifiers):
        return [name for flag, name in _MOD_TOKENS if modifiers & flag]

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        if key in _MODIFIER_KEYS or key == Qt.Key.Key_unknown:
            # Prévisualisation des modificateurs enfoncés (sans validation).
            self.setText("+".join(self._modifier_tokens(modifiers)))
            return

        if not modifiers:
            if key in _CLEAR_KEYS:
                self.setShortcut("")
                return
            if key == Qt.Key.Key_Escape:
                # Abandonne la prévisualisation : on réaffiche la valeur
                # validée. Échap est consommé ici et ne ferme pas le dialogue.
                self.setText(self._shortcut)
                return

        name = _NAMED_KEYS.get(key)
        if name is None:
            # « P », « F5 », « 5 », « + »… — casse Qt conservée pour rester
            # cohérent avec la forme canonique historique (« Alt+P »).
            name = QKeySequence(key).toString()
        if not name:
            logger.debug("Touche non traduisible ignorée : %s", key)
            return

        self.setShortcut("+".join(self._modifier_tokens(modifiers) + [name]))

    def focusOutEvent(self, event):
        # Une prévisualisation incomplète (modificateurs seuls) est abandonnée
        # en quittant le champ : on réaffiche la dernière valeur validée.
        if self.text() != self._shortcut:
            self.setText(self._shortcut)
        super().focusOutEvent(event)
