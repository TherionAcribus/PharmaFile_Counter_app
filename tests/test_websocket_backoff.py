"""Tests du backoff de reconnexion WebSocket (point 12).

compute_reconnect_delay est déterministe si on injecte ``rand`` : on vérifie le
backoff exponentiel, le plafond, et les bornes du jitter (equal jitter).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from websocket_client import (  # noqa: E402
    compute_reconnect_delay,
    RECONNECT_BASE_DELAY,
    RECONNECT_MAX_DELAY,
)


def _delay(attempt, r):
    return compute_reconnect_delay(attempt, rand=lambda: r)


def test_exponential_growth_without_jitter():
    # rand=0 -> délai = exp/2 (borne basse), croissance exponentielle.
    assert _delay(1, 0.0) == RECONNECT_BASE_DELAY * 1 / 2      # exp=1  -> 0.5
    assert _delay(2, 0.0) == RECONNECT_BASE_DELAY * 2 / 2      # exp=2  -> 1.0
    assert _delay(3, 0.0) == RECONNECT_BASE_DELAY * 4 / 2      # exp=4  -> 2.0
    assert _delay(4, 0.0) == RECONNECT_BASE_DELAY * 8 / 2      # exp=8  -> 4.0


def test_capped_at_max():
    # Pour un grand nombre de tentatives, exp est plafonné à RECONNECT_MAX_DELAY.
    low = _delay(50, 0.0)
    high = _delay(50, 1.0)
    assert low == RECONNECT_MAX_DELAY / 2
    assert high == RECONNECT_MAX_DELAY
    # jamais au-dessus du plafond
    assert high <= RECONNECT_MAX_DELAY


def test_jitter_bounds_equal_jitter():
    # délai ∈ [exp/2, exp] : minimum garanti + dispersion.
    for attempt in (1, 2, 3, 6, 20):
        lo = _delay(attempt, 0.0)
        hi = _delay(attempt, 1.0)
        mid = _delay(attempt, 0.5)
        assert lo <= mid <= hi
        assert hi <= 2 * lo + 1e-9          # exp = 2 * (exp/2)
        assert hi <= RECONNECT_MAX_DELAY + 1e-9


def test_delay_never_zero_minimum_guaranteed():
    # Même avec jitter nul, un délai minimal existe (pas de martèlement).
    assert _delay(1, 0.0) > 0.0


def test_attempt_below_one_treated_as_one():
    assert _delay(0, 0.0) == _delay(1, 0.0)
