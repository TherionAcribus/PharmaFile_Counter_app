"""Régression : les actions patient modificatrices sont envoyées en POST (pt 14).

Invariant vérifié statiquement sur main.py : tout appel ``self._submit(...)`` dont
le résultat est traité par ``handle_result`` (validation, pause, suppression,
remise en attente, appel patient/suivant) porte ``method='POST'`` — plus aucun GET
modificateur.
"""

import os
import re

_MAIN = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "main.py"))

_SUBMIT_CALL = re.compile(r"self\._submit\((.*?)\)", re.DOTALL)


def _submit_calls():
    with open(_MAIN, encoding="utf-8") as fh:
        source = fh.read()
    return [m.group(1) for m in _SUBMIT_CALL.finditer(source)]


def test_all_patient_actions_use_post():
    calls = _submit_calls()
    assert calls, "aucun appel self._submit trouvé (parsing cassé ?)"
    modifying = [args for args in calls if "handle_result" in args]
    assert modifying, "aucune action patient (handle_result) trouvée"
    for args in modifying:
        assert "method='POST'" in args or 'method="POST"' in args, (
            f"action modificatrice sans POST : self._submit({args.strip()[:80]}...)"
        )


def test_no_modifying_action_relies_on_default_get():
    # Le défaut de _submit est GET : aucune action patient ne doit s'y fier.
    for args in _submit_calls():
        if "handle_result" in args:
            assert "method=" in args, (
                "une action patient utilise le GET par défaut de _submit"
            )
