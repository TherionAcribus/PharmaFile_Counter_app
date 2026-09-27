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


def test_group_excludes_incompatible_actions():
    """Le groupe refuse une AUTRE action du même groupe (exclusion mutuelle),
    contrairement à la clé qui ne refuse que les doublons."""
    reg = TaskRegistry()
    a, b = object(), object()
    assert reg.add(a, key="pause", group="patient") is True
    assert reg.is_group_active("patient") is True
    assert reg.add(b, key="validate_and_call_next", group="patient") is False
    assert len(reg) == 1
    reg.remove(a, key="pause")
    assert reg.is_group_active("patient") is False
    assert reg.add(b, key="validate_and_call_next", group="patient") is True


def test_group_does_not_block_ungrouped_tasks():
    reg = TaskRegistry()
    assert reg.add(object(), key="pause", group="patient")
    # Une tâche sans groupe n'est pas gênée par le groupe occupé…
    assert reg.add(object(), key="login") is True
    # …et un autre groupe reste indépendant.
    assert reg.add(object(), key="x", group="other") is True


def test_active_groups_reports_current_ones():
    reg = TaskRegistry()
    t = object()
    assert reg.active_groups() == set()
    reg.add(t, key="pause", group="patient")
    assert reg.active_groups() == {"patient"}
    reg.remove(t, key="pause")
    assert reg.active_groups() == set()


def test_snapshot_is_a_stable_copy():
    # snapshot() sert à itérer les tâches actives à l'arrêt sans être gêné par
    # les retraits concurrents (finished).
    reg = TaskRegistry()
    a, b = object(), object()
    reg.add(a)
    reg.add(b, key="k")
    snap = reg.snapshot()
    assert set(snap) == {a, b}
    # Retirer après le snapshot ne modifie pas la copie déjà prise.
    reg.remove(a)
    assert set(snap) == {a, b}
    assert len(reg) == 1
