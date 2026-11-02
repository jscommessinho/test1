"""Additional bet markets evaluated on an *original* verified GA3 score grid.

Uses the unchanged MA3 settlement engine for every market syntax it supports.
The two team-specific multigoal ranges are direct matrix events. Quote prices
are never inferred; these unquoted supplements are not betting suggestions.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def market_ids():
    ids = [f"FT.DC.{choice}" for choice in ("1X", "X2", "12")]
    for choice in ("1X", "X2", "12"):
        for line in ("0.5", "1.5", "2.5", "3.5", "4.5"):
            ids.append(f"FT.COMBO.FT.DC.{choice}&FT.TOTAL.OVER.{line}")
        for lower, upper in ((1,2), (1,3), (1,4), (2,3), (2,4),
                             (2,5), (3,4), (3,5)):
            ids.append(f"FT.COMBO.FT.DC.{choice}&FT.MULTIGOAL.{lower}.{upper}")
    return ids


def compute(ga_normalized: dict) -> dict:
    if ga_normalized.get("source") != "PitchAPI" or ga_normalized.get("native_schema_parity_verified") is not True:
        raise ValueError("Authenticated original GA3 normalized source required")
    from ma3.core import load_matrix, market_from_matrix
    matrix = load_matrix(ga_normalized["scorelines"])
    rows = []
    for market_id in market_ids():
        settled = market_from_matrix(market_id, matrix)
        rows.append({"market_id": market_id,
                     "model_probability": settled["p_event"],
                     "settlement_probabilities": {k: settled[k] for k in
                         ("full_win", "half_win", "push", "half_loss", "full_loss")},
                     "quote_status": "ODDS_UNAVAILABLE",
                     "selection_eligible": False,
                     "source": "ORIGINAL_GA3_MATRIX_WITH_ORIGINAL_MA3_SETTLEMENT"})
    for side in ("H", "A"):
        for lo, hi in ((1,2), (1,3)):
            prob = math.fsum(p for (h,a),p in matrix.items()
                             if lo <= (h if side == "H" else a) <= hi)
            rows.append({"market_id": f"FT.TEAM.{side}.MULTIGOAL.{lo}.{hi}",
                         "model_probability": prob,
                         "quote_status": "ODDS_UNAVAILABLE",
                         "selection_eligible": False,
                         "source": "ORIGINAL_GA3_JOINT_MATRIX"})
    return {
        "schema": "BETPILOT_GA3_MA3_ADDITIONAL_MARKETS_V1",
        "source": "PitchAPI",
        "event_id": ga_normalized["event_id"],
        "original_ga3_file_sha256": ga_normalized["source_artifact_sha256"],
        "original_model_modified": False,
        "original_ma3_modified": False,
        "supplemental_quoted_markets": 0,
        "additional_market_count": len(rows),
        "market_rows": rows,
        "status": "UNQUOTED_PROBABILITIES_ONLY_RESEARCH",
    }


def write_verified(ga_file: Path, output: Path) -> dict:
    ga_file = Path(ga_file)
    data = json.loads(ga_file.read_text("utf-8"))
    result = compute(data)
    result["normalized_ga3_sha256"] = hashlib.sha256(ga_file.read_bytes()).hexdigest()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n", "utf-8")
    return result
