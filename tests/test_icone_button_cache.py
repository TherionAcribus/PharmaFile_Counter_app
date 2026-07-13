"""Tests de la mise en cache des icônes d'IconeButton (point 22).

Avant : update_button_icon() recréait un QIcon depuis le fichier à chaque
changement d'état (accès disque répété). Désormais les QIcon active/inactive sont
chargées une seule fois dans le constructeur et réutilisées.

On vérifie les deux critères :
  - aucun nouveau QIcon (donc aucun accès disque) lors d'un simple changement
    d'état ;
  - le rendu reste identique : chaque état pose la bonne icône (active/inactive),
    le bon tooltip et le bon état activé.

Tests réels avec PySide6 (QApplication offscreen) : IconeButton est un QWidget.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import buttons  # noqa: E402
from buttons import IconeButton  # noqa: E402

_ASSETS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "assets", "images"))
ACTIVE_ICON = os.path.join(_ASSETS, "loop_yes.ico")
INACTIVE_ICON = os.path.join(_ASSETS, "loop_no.ico")


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make(state="inactive", is_always_visible=True):
    return IconeButton(
        icon_path=ACTIVE_ICON,
        icon_inactive_path=INACTIVE_ICON,
        flask_url="http://x/app/counter/auto_calling",
        tooltip_text="Désactiver",
        tooltip_inactive_text="Activer",
        state=state,
        is_always_visible=is_always_visible,
        parent=None,
    )


def test_icons_loaded_once_in_constructor(qapp, monkeypatch):
    """Le constructeur ne charge QUE deux QIcon (active + inactive)."""
    calls = {"n": 0}
    real_qicon = buttons.QIcon

    class CountingQIcon(real_qicon):
        def __init__(self, *args, **kwargs):
            calls["n"] += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(buttons, "QIcon", CountingQIcon)
    _make()
    assert calls["n"] == 2


def test_no_qicon_creation_on_state_change(qapp, monkeypatch):
    """Critère : aucun accès disque (aucun QIcon recréé) sur changement d'état."""
    btn = _make(state="inactive")

    calls = {"n": 0}
    real_qicon = buttons.QIcon

    class CountingQIcon(real_qicon):
        def __init__(self, *args, **kwargs):
            calls["n"] += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(buttons, "QIcon", CountingQIcon)

    for state in ["active", "inactive", "waiting", "active", "inactive", "waiting"]:
        btn.change_state(state)

    assert calls["n"] == 0


def test_reuses_same_cached_qicon_objects(qapp, monkeypatch):
    """Chaque état réutilise l'objet QIcon mis en cache (identité)."""
    btn = _make(state="inactive")

    passed = []
    monkeypatch.setattr(btn, "setIcon", lambda icon: passed.append(icon))

    btn.change_state("active")
    btn.change_state("inactive")
    btn.change_state("waiting")

    assert passed[0] is btn._icon_active
    assert passed[1] is btn._icon_inactive
    assert passed[2] is btn._icon_active  # "waiting" garde l'icône active


def test_rendering_unchanged_per_state(qapp):
    """Le rendu reste identique : icône, tooltip et état activé par état."""
    btn = _make(state="inactive")

    btn.change_state("active")
    assert btn.icon().cacheKey() == btn._icon_active.cacheKey()
    assert btn.toolTip() == "Désactiver"
    assert btn.isEnabled() is True

    btn.change_state("inactive")
    assert btn.icon().cacheKey() == btn._icon_inactive.cacheKey()
    assert btn.toolTip() == "Activer"
    assert btn.isEnabled() is True

    btn.change_state("waiting")
    assert btn.icon().cacheKey() == btn._icon_active.cacheKey()
    assert btn.toolTip() == "En attente d'une connexion"
    assert btn.isEnabled() is False


def test_active_and_inactive_icons_differ(qapp):
    """Les deux icônes chargées sont bien distinctes (rendu correct)."""
    btn = _make()
    assert btn._icon_active.cacheKey() != btn._icon_inactive.cacheKey()


def test_hidden_when_inactive_and_not_always_visible(qapp):
    """Comportement inchangé : bouton masqué en 'inactive' si non toujours visible."""
    btn = _make(state="active", is_always_visible=False)
    btn.change_state("inactive")
    assert btn.isVisible() is False
