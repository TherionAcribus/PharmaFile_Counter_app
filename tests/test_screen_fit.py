"""Fenêtres secondaires gardées dans l'écran et panneau qui peut rétrécir."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QLabel, QPushButton, QVBoxLayout, QWidget,
)

import resources  # noqa: E402
import screen_fit  # noqa: E402


def _avail():
    return QGuiApplication.primaryScreen().availableGeometry()


def test_fit_to_screen_ramene_un_dialogue_trop_grand():
    avail = _avail()
    dialog = QDialog()
    dialog.setMinimumWidth(avail.width() + 300)
    dialog.show()
    dialog.move(avail.x() + 50, avail.y() + 50)
    dialog.resize(avail.width() + 300, avail.height() + 200)
    assert screen_fit.fit_to_screen(dialog) is True
    frame = dialog.frameGeometry()
    assert avail.contains(frame), (frame, avail)
    dialog.close()


def test_fit_to_screen_ne_touche_pas_un_dialogue_qui_tient():
    avail = _avail()
    dialog = QDialog()
    dialog.show()
    dialog.resize(300, 200)
    dialog.move(avail.x() + 10, avail.y() + 10)
    assert screen_fit.fit_to_screen(dialog) is False
    dialog.close()


def test_scrollable_body_demande_la_taille_du_contenu():
    """Sur un écran assez grand, aucun défilement : la zone demande la hauteur
    entière du contenu (QScrollArea la plafonnait à ~24 lignes)."""
    dialog = QDialog()
    outer, body = screen_fit.scrollable_body(dialog)
    for i in range(40):
        body.addWidget(QLabel(f"Ligne {i}"))
    scroll = outer.itemAt(0).widget()
    assert scroll.sizeHint().height() >= scroll.widget().sizeHint().height()


def _min_width_in_panel(skin, inside_panel):
    app = QApplication.instance()
    app.setStyleSheet(resources.skin_stylesheet(skin))
    holder = QWidget()
    if inside_panel:
        holder.setObjectName(resources.COUNTER_PANEL_OBJECT_NAME)
    layout = QVBoxLayout(holder)
    button = QPushButton("A")
    layout.addWidget(button)
    holder.ensurePolished()
    button.ensurePolished()
    width = button.minimumSizeHint().width()
    app.setStyleSheet("")
    return width


def test_panneau_du_comptoir_peut_retrecir_malgre_le_skin():
    """Diffnes/Fibers/Integrid imposent « QPushButton { min-width: 80px } » :
    dans le panneau du comptoir, la règle est neutralisée ; ailleurs (dialogues)
    le skin reste intact."""
    for skin in ("Diffnes", "Fibers", "Integrid"):
        assert _min_width_in_panel(skin, inside_panel=True) < 60, skin
        assert _min_width_in_panel(skin, inside_panel=False) >= 80, skin


def test_skin_stylesheet():
    assert resources.skin_stylesheet("") == ""
    assert resources.skin_stylesheet("SkinInexistant") is None
    qss = resources.skin_stylesheet("Darkeum")
    assert qss.startswith(resources.read_skin("Darkeum"))
    assert qss.endswith(resources.PANEL_OVERRIDES_QSS)
