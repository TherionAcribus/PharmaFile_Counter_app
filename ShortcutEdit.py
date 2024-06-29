from PySide6.QtWidgets import QLineEdit
from PySide6 import QtCore
from PySide6.QtCore import Qt

# POUR l'instant ne fonctionne pas....
# https://gist.github.com/Sl-Alex/20bace0271a59c8b6db446c3faacefb0

""" Extension of QLineEdit with a possibility to catch shortcuts.
Standard QKeySequenceEdit is too slow and does not make any difference between numpad and normal keys.
"""
class ShortcutEdit(QLineEdit):

    """This signal is emitted whenever a new key or modifier is pressed
    First parameter is the key (can be zero), second is a list of modifiers
    """
    shortcutChanged = QtCore.Signal(int, list)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.keymap = {}
        self.modmap = {
            Qt.ControlModifier: 'Ctrl',
            Qt.AltModifier: 'Alt',
            Qt.ShiftModifier: 'Shift',
            Qt.MetaModifier: 'Meta',
            Qt.GroupSwitchModifier: 'AltGr',
            Qt.KeypadModifier: 'Num',
        }
        
        self.modkeyslist = [
            Qt.Key_Control,
            Qt.Key_Alt,
            Qt.Key_Shift,
            Qt.Key_Meta,
            Qt.Key_AltGr,
            Qt.Key_NumLock,
        ]
        
        for key, value in vars(Qt).items():
            if isinstance(value, Qt.Key):
                self.keymap[value] = key.partition('_')[2]

        self.current_modifiers = []
        self.current_key = 0

        self.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.KeyPress or event.type() == QtCore.QEvent.KeyRelease:
            key = event.key()
            modifiers = event.modifiers()

            print(f"Event: {event.type()}, Key: {key}, Modifiers: {modifiers}")

            if event.type() == QtCore.QEvent.KeyPress:
                if modifiers == Qt.NoModifier and key not in self.modkeyslist:
                    # Simple key press without modifiers
                    self.current_modifiers = []
                    self.current_key = key
                else:
                    # Key press with modifiers
                    self.current_modifiers = []
                    for modifier, name in self.modmap.items():
                        if modifiers & modifier:
                            self.current_modifiers.append(modifier)
                    
                    # Set the key if it's not a pure modifier key
                    if key not in self.modkeyslist:
                        self.current_key = key

                # Prepare the text representation
                text = ''
                for modifier in self.current_modifiers:
                    if text:
                        text += '+'
                    text += self.modmap[modifier]
                if self.current_key and self.current_key in self.keymap:
                    if text:
                        text += '+'
                    text += self.keymap[self.current_key]

                print(f"Text: {text}")

                # Update the text and emit a signal if a key was pressed
                self.setText(text)
                self.shortcutChanged.emit(self.current_key, self.current_modifiers)

            return True

        return False