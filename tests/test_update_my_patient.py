"""Tests de update_my_patient (point 16) : robustesse aux données patient invalides.

On appelle la vraie méthode avec un faux ``self`` minimal (elle n'instancie aucun
widget : elle n'utilise que label_patient.setText, _update_menu_actions, counter_id,
patient_id, logger). But : une donnée incomplète ne doit pas crasher, l'UI doit
revenir dans un état sûr (actions désactivées), et l'erreur originale doit rester
visible dans les logs.
"""

import logging
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import main  # noqa: E402


class FakeLabel:
    def __init__(self):
        self.text = None

    def setText(self, t):
        self.text = t


class FakeWindow:
    """Faux self : porte les attributs utilisés par update_my_patient et les vraies
    méthodes update_my_patient/_on_invalid_patient liées à cette instance."""

    def __init__(self, counter_id=3):
        self.counter_id = counter_id
        self.patient_id = "sentinelle"
        self.label_patient = FakeLabel()
        self.menu_calls = []
        self.logger = logging.getLogger("test.update_my_patient")
        # Lie les vraies méthodes de MainWindow à ce faux self.
        self.update_my_patient = types.MethodType(main.MainWindow.update_my_patient, self)
        self._on_invalid_patient = types.MethodType(main.MainWindow._on_invalid_patient, self)

    def _update_menu_actions(self, enable):
        self.menu_calls.append(enable)


def _valid_patient(counter_id=3):
    return {
        "counter_id": counter_id, "id": 42, "status": "ongoing",
        "language_code": "fr", "call_number": "A-12", "activity": "Ordonnance",
    }


def test_none_patient_safe_state():
    w = FakeWindow()
    w.update_my_patient(None)
    assert w.patient_id is None
    assert w.label_patient.text == "Plus de patient"
    assert w.menu_calls == [False]


def test_false_patient_safe_state():
    w = FakeWindow()
    w.update_my_patient(False)
    assert w.patient_id is None
    assert w.menu_calls == [False]


def test_valid_patient_enables_actions():
    w = FakeWindow()
    w.update_my_patient(_valid_patient())
    assert w.patient_id == 42
    assert "A-12" in w.label_patient.text
    assert w.menu_calls == [True]


def test_patient_without_id_disables_actions():
    p = _valid_patient()
    p["id"] = None
    w = FakeWindow()
    w.update_my_patient(p)
    assert w.patient_id is None
    assert w.label_patient.text == "Pas de patient en cours"
    assert w.menu_calls == [False]


def test_other_counter_patient_leaves_state_unchanged():
    w = FakeWindow(counter_id=3)
    w.update_my_patient(_valid_patient(counter_id=99))
    assert w.patient_id == "sentinelle"   # inchangé
    assert w.menu_calls == []             # aucune action touchée


@pytest.mark.parametrize("missing", ["id", "status", "language_code", "call_number", "activity"])
def test_incomplete_patient_does_not_crash_and_disables_actions(missing, caplog):
    p = _valid_patient()
    del p[missing]
    w = FakeWindow()
    with caplog.at_level(logging.ERROR, logger="test.update_my_patient"):
        w.update_my_patient(p)  # ne doit pas lever
    # État sûr : actions désactivées, patient_id remis à None, label neutre.
    assert w.menu_calls[-1] is False
    assert w.patient_id is None
    assert w.label_patient.text == "Données patient indisponibles"
    # L'erreur originale (KeyError) est visible dans les logs techniques.
    assert any(rec.levelno >= logging.ERROR for rec in caplog.records)
    assert any(rec.exc_info for rec in caplog.records)  # trace attachée


def test_non_dict_patient_is_handled_safely(caplog):
    w = FakeWindow()
    with caplog.at_level(logging.ERROR, logger="test.update_my_patient"):
        w.update_my_patient("pas un dict")
    assert w.menu_calls[-1] is False
    assert w.patient_id is None
    assert w.label_patient.text == "Données patient indisponibles"
