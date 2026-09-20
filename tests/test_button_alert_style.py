"""Tests du cycle d'alerte « patient à valider » (setRed / resetColor).

Avant : ``original_style`` était mémorisé à la construction du bouton — donc
souvent une chaîne vide. Toute feuille inline posée ensuite (le « border: none »
des IconeButton, un style appliqué par un skin…) était effacée par resetColor.
Désormais la feuille est capturée à l'entrée de l'alerte et restaurée telle
quelle à la sortie.

Tests réels avec PySide6 (QApplication offscreen).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from buttons import DebounceButton, IconeButton  # noqa: E402

_ASSETS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "assets", "images"))


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _button_color(btn):
    btn.ensurePolished()
    return btn.palette().color(QPalette.Button).name()


def test_reset_color_restores_post_construction_style(qapp):
    """Un style posé APRÈS la construction survit à un cycle alerte complet."""
    btn = DebounceButton("Valider")
    btn.setStyleSheet("border: none;")

    btn.setRed()
    assert btn.styleSheet() == DebounceButton.ALERT_STYLESHEET

    btn.resetColor()
    assert btn.styleSheet() == "border: none;"


def test_icone_button_style_survives_alert_cycle(qapp):
    """Cas concret : IconeButton pose « border: none » après le constructeur de
    DebounceButton — la capture à la construction le perdait définitivement."""
    btn = IconeButton(
        icon_path=os.path.join(_ASSETS, "loop_yes.ico"),
        icon_inactive_path=os.path.join(_ASSETS, "loop_no.ico"),
        flask_url="http://x/app/counter/auto_calling",
        tooltip_text="Désactiver",
        tooltip_inactive_text="Activer",
        state="inactive",
    )
    assert btn.styleSheet() == "border: none;"

    btn.setRed()
    btn.resetColor()
    assert btn.styleSheet() == "border: none;"


def test_skin_keeps_applying_after_alert_cycle(qapp):
    """Scénario rapporté : skin appliqué (feuille sur le parent, comme
    load_skin), alerte déclenchée puis levée — le skin doit revenir."""
    parent = QWidget()
    lay = QVBoxLayout(parent)
    btn = DebounceButton("Valider")
    lay.addWidget(btn)
    parent.setStyleSheet(
        "QPushButton { background-color: #112233; color: #aabbcc; }")

    assert _button_color(btn) == "#112233"

    btn.setRed()
    assert _button_color(btn) == "#c0392b"

    btn.resetColor()
    assert _button_color(btn) == "#112233"


def test_repeated_set_red_keeps_pre_alert_style(qapp):
    """Un second setRed pendant l'alerte ne doit pas capturer la feuille
    d'alerte elle-même comme « style d'origine »."""
    btn = DebounceButton("Valider")
    btn.setStyleSheet("border: none;")

    btn.setRed()
    btn.setRed()
    btn.resetColor()
    assert btn.styleSheet() == "border: none;"


def test_external_style_change_during_alert_not_clobbered(qapp):
    """Si la feuille est remplacée pendant l'alerte, resetColor n'écrase pas le
    style le plus récent."""
    btn = DebounceButton("Valider")
    btn.setStyleSheet("border: none;")

    btn.setRed()
    btn.setStyleSheet("font-weight: bold;")
    btn.resetColor()
    assert btn.styleSheet() == "font-weight: bold;"


def test_reset_color_without_alert_is_noop(qapp):
    """resetColor sans alerte en cours ne touche pas la feuille courante."""
    btn = DebounceButton("Valider")
    btn.setStyleSheet("border: none;")

    btn.resetColor()
    assert btn.styleSheet() == "border: none;"
