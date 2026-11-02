"""Derived prematch markets from the ORIGINAL Goal Analyzer joint score grid.

This module does not estimate lambdas, refit the model or invent bookmaker odds.
Dependent events are evaluated cell-by-cell (never multiplied as independent).
If the grid omits material probability mass, publish conservative probability
bounds rather than pretend that its finite matrix is a full distribution.
"""
from __future__ import annotations

import math
from collections.abc import Sequence


DOUBLE_CHANCE = {
    "1X": lambda home, away: home >= away,
    "X2": lambda home, away: home <= away,
    "12": lambda home, away: home != away,
}
TOTAL_MULTIGOL = ((1, 2), (1, 3), (1, 4), (2, 3), (2, 4),
                   (2, 5), (3, 4), (3, 5))
OVER_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)


def from_joint_score_grid(grid: Sequence[Sequence[float]],
                          *, mass_tolerance: float = 1e-6) -> dict:
    """Compute requested probabilities from a rectangular joint score matrix.

    For each event, a complete grid yields an exact probability. For truncated
    grids, the interval [captured_event_mass, captured_event_mass+omitted_mass]
    is valid irrespective of how the unrepresented tails are distributed.
    """
    if not grid or not all(isinstance(row, (list, tuple)) and row for row in grid):
        raise ValueError("Expected a non-empty two-dimensional score grid")
    width = len(grid[0])
    if any(len(row) != width for row in grid):
        raise ValueError("Score grid must be rectangular")
    cells = []
    for home, row in enumerate(grid):
        for away, value in enumerate(row):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError("Score-grid probabilities must be finite nonnegative numbers")
            cells.append((home, away, float(value)))
    captured_mass = math.fsum(prob for _, _, prob in cells)
    if captured_mass <= 0 or captured_mass > 1 + mass_tolerance:
        raise ValueError("Joint score-grid probability mass out of bounds")

    def event_mass(predicate):
        return math.fsum(prob for home, away, prob in cells if predicate(home, away))

    values = {}
    for label, check in DOUBLE_CHANCE.items():
        values["double_chance_" + label.lower()] = event_mass(check)
    for side in ("home", "away"):
        get_goals = (lambda home, away: home) if side == "home" else (
            lambda home, away: away)
        values[side + "_scores"] = event_mass(
            lambda home, away: get_goals(home, away) >= 1)
        for lower, upper in ((1, 2), (1, 3)):
            values[f"{side}_multigol_{lower}_{upper}"] = event_mass(
                lambda home, away, lo=lower, hi=upper:
                lo <= get_goals(home, away) <= hi)
        for threshold in (1.5, 2.5):
            values[f"{side}_over_{str(threshold).replace('.', '_')}"] = event_mass(
                lambda home, away, limit=threshold:
                get_goals(home, away) > limit)

    for label, check in DOUBLE_CHANCE.items():
        for threshold in OVER_LINES:
            key = "double_chance_" + label.lower() + "_and_over_" + str(threshold).replace(".", "_")
            values[key] = event_mass(
                lambda home, away, condition=check, line=threshold:
                condition(home, away) and home + away > line)
        for lower, upper in TOTAL_MULTIGOL:
            key = f"double_chance_{label.lower()}_and_multigol_{lower}_{upper}"
            values[key] = event_mass(
                lambda home, away, condition=check, lo=lower, hi=upper:
                condition(home, away) and lo <= home + away <= hi)

    unresolved_mass = max(0.0, 1.0 - captured_mass)
    complete = unresolved_mass <= mass_tolerance
    bounds = {
        market: {"lower": max(0.0, p), "upper": min(1.0, p + unresolved_mass)}
        for market, p in values.items()
    }
    return {
        "status": "OK" if complete else "PARTIAL_GRID",
        "source": "original_ga3_joint_score_grid",
        "captured_mass": captured_mass,
        "unresolved_mass": unresolved_mass,
        "markets": values if complete else {},
        "probability_bounds": bounds,
        "bookmaker_odds_generated": False,
        "ga3_lambda_formula_changed": False,
    }
