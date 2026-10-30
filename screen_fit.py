"""Fenêtres secondaires gardées dans l'écran (petites résolutions).

Un dialogue dont la taille minimale (ou la taille mémorisée par Qt) dépasse
l'écran déborde : ses boutons « Enregistrer / Annuler » deviennent
inaccessibles. ``fit_to_screen`` le ramène entièrement dans la zone utile de
son écran (barre des tâches exclue) ; ``scrollable_body`` fournit un corps de
dialogue qui défile quand la place manque, les boutons restant visibles.

Le calcul géométrique est celui, testé, de ``window_geometry.fit_inside``.
"""

from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from window_geometry import fit_inside


def _rect(qrect):
    return (qrect.x(), qrect.y(), qrect.width(), qrect.height())


def fit_to_screen(widget):
    """Ramène ``widget`` (fenêtre de premier niveau) dans son écran.

    Plafonne d'abord sa taille minimale à la zone utile (sinon resize()
    n'aurait aucun effet), puis le réduit / décale si besoin. Renvoie True si
    la fenêtre a été modifiée."""
    try:
        screen = widget.screen() or QGuiApplication.primaryScreen()
    except RuntimeError:  # widget détruit entre-temps
        return False
    if screen is None:
        return False
    avail = screen.availableGeometry()
    frame = widget.frameGeometry()
    inner = widget.geometry()
    dw = max(0, frame.width() - inner.width())
    dh = max(0, frame.height() - inner.height())
    if widget.minimumWidth() + dw > avail.width():
        widget.setMinimumWidth(max(1, avail.width() - dw))
    if widget.minimumHeight() + dh > avail.height():
        widget.setMinimumHeight(max(1, avail.height() - dh))
    current = _rect(frame)
    target = fit_inside(current, _rect(avail))
    if target == current:
        return False
    x, y, w, h = target
    widget.resize(max(1, w - dw), max(1, h - dh))
    widget.move(x, y)
    return True


def fit_on_show(widget):
    """À appeler depuis ``showEvent`` : l'ajustement attend le tour de boucle
    suivant, quand la fenêtre est placée et son cadre connu."""
    def _fit():
        try:
            if widget.isVisible():
                fit_to_screen(widget)
        except RuntimeError:
            pass
    QTimer.singleShot(0, _fit)


class _FittingScrollArea(QScrollArea):
    """Zone de défilement qui demande la taille ENTIÈRE de son contenu.

    QScrollArea plafonne sa taille idéale (environ 36×24 lignes de texte) : un
    dialogue un peu long s'ouvrait avec une barre de défilement même sur un
    grand écran. Ici, la taille idéale est celle du contenu — le défilement ne
    sert que quand l'écran est réellement trop petit (``fit_to_screen``)."""

    def sizeHint(self):
        widget = self.widget()
        if widget is None:
            return super().sizeHint()
        frame = 2 * self.frameWidth()
        hint = widget.sizeHint()
        return QSize(hint.width() + frame, hint.height() + frame)


def scrollable_body(dialog):
    """Corps défilant d'un dialogue.

    Renvoie ``(outer, body)`` : ``outer`` est le layout du dialogue (y placer la
    barre de boutons, qui reste toujours visible) ; ``body`` celui du contenu,
    dans une zone qui défile si l'écran est trop petit pour l'afficher entier."""
    outer = QVBoxLayout(dialog)
    scroll = _FittingScrollArea(dialog)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    inner = QWidget(scroll)
    scroll.setWidget(inner)
    outer.addWidget(scroll, 1)
    body = QVBoxLayout(inner)
    body.setContentsMargins(0, 0, 0, 0)
    return outer, body
