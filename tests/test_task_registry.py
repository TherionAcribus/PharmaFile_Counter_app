"""Tests du registre de tâches réseau actives (task_registry.TaskRegistry).

Couvre :
- conservation des références (plusieurs tâches différentes coexistent) ;
- déduplication par clé (seconde action identique refusée tant que la première
  est active), puis réautorisée après retrait ;
- clé None jamais dédupliquée (ex: workers) ;
- retrait idempotent.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from task_registry import TaskRegistry  # noqa: E402


def test_multiple_distinct_tasks_are_retained():
    reg = TaskRegistry()
    a, b, c = object(), object(), object()
    assert reg.add(a, key="validate")
    assert reg.add(b, key="pause")
    assert reg.add(c, key="delete:5")
    assert len(reg) == 3
    assert a in reg and b in reg and c in reg
    assert reg.active_keys() == {"validate", "pause", "delete:5"}


def test_duplicate_key_is_refused_until_removed():
    reg = TaskRegistry()
    t1, t2 = object(), object()
    assert reg.add(t1, key="validate") is True
    # Seconde action identique refusée tant que la première est active.
    assert reg.is_active("validate") is True
    assert reg.add(t2, key="validate") is False
    assert len(reg) == 1
    # Après la fin de la première, la clé est de nouveau disponible.
    reg.remove(t1, key="validate")
    assert reg.is_active("validate") is False
    assert reg.add(t2, key="validate") is True
    assert len(reg) == 1


def test_none_key_never_deduplicated():
    reg = TaskRegistry()
    w1, w2 = object(), object()
    assert reg.add(w1) is True
    assert reg.add(w2) is True  # pas de dédup sur None (ex: workers)
    assert len(reg) == 2
    assert reg.is_active(None) is False


def test_remove_is_idempotent():
    reg = TaskRegistry()
    t = object()
    reg.add(t, key="x")
    reg.remove(t, key="x")
    reg.remove(t, key="x")  # ne lève pas
    assert len(reg) == 0
    assert reg.active_keys() == set()


def test_different_targets_run_concurrently():
    # Deux patients différents : mêmes type d'action mais clés distinctes -> OK.
    reg = TaskRegistry()
    assert reg.add(object(), key="delete:1")
    assert reg.add(object(), key="delete:2")
    assert len(reg) == 2
