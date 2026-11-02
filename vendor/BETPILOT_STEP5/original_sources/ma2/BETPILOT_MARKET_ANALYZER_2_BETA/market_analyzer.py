#!/usr/bin/env python3
"""BETPILOT Market Analyzer 2.0 — research-only reference implementation.

Python standard library only. A comparison/settlement engine, NOT a trained
prediction model or a certified betting recommendation system. All odds are
synthetic in the bundled example; never silently manufacture quotes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import argparse
import json
import math
import re
from typing import Any

VERSION = "MA2-BETA-20261030"
P_TAIL_MAX = 1e-10
STATES = ("full_win", "half_win", "push", "half_loss", "full_loss")


class MarketError(ValueError):
    pass


def require_probability(p: float, label: str = "p") -> float:
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
        raise MarketError(f"{label}: invalid probability {p}")
    return float(p)


def parse_stamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise MarketError("Timestamp required, with timezone offset")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketError("Invalid timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise MarketError("Timestamp must include timezone offset")
    return dt


def poisson_pmf(lam: float, tail_max: float = 5e-13) -> list[float]:
    if not isinstance(lam, (int, float)) or isinstance(lam, bool) or not math.isfinite(lam) or not 0 < lam <= 30:
        raise MarketError("lambda must be finite and in (0,30] for reference implementation")
    # Start from mode for numeric stability, then normalize by exp(-lambda)*sum recursively.
    # This version is limited to lambda<=30 so exp(-lambda) is well behaved.
    vals = [math.exp(-lam)]
    total = vals[0]
    k = 0
    while 1 - total > tail_max:
        k += 1
        if k > 500:
            raise MarketError("Poisson tail convergence failure")
        vals.append(vals[-1] * lam / k)
        total += vals[-1]
    return vals  # deliberately NOT renormalized


def joint_from_lambda(lh: float, la: float) -> dict[str, Any]:
    h, a = poisson_pmf(lh), poisson_pmf(la)
    cells = {(i, j): hi * aj for i, hi in enumerate(h) for j, aj in enumerate(a)}
    covered = sum(cells.values())
    tail = max(0.0, 1.0 - covered)
    if tail > P_TAIL_MAX:
        raise MarketError("Mass omitted exceeds tolerance")
    return {"matrix": cells, "omitted_mass": tail, "lambda_home": lh, "lambda_away": la}


def score_side(h: int, a: int, team: str) -> int:
    return h if team == "H" else a


def ah_component(value: int | float) -> str:
    if value > 1e-9:
        return "W"
    if value < -1e-9:
        return "L"
    return "P"


def quarter_components(line: float) -> tuple[float, ...]:
    # 0.25 line = 0 and 0.5, -0.75 line = -1 and -0.5.
    x4 = round(line * 4)
    if abs(line * 4 - x4) > 1e-8:
        raise MarketError("Asian line must be a multiple of 0.25")
    if x4 % 2:
        return (line - 0.25, line + 0.25)
    return (line,)


def outcomes(market_id: str, h: int, a: int) -> tuple[str, ...]:
    """Returns one or two equal-size stake legs: W/P/L. FT/HT/SH caller picks matrix."""
    x = market_id.split(".")
    if len(x) < 3 or x[0] not in {"FT", "HT", "SH"}:
        raise MarketError("Bad market identifier")
    period = x[0]
    typ = x[1]
    rest = x[2:]
    if typ == "1X2" and len(rest) == 1 and rest[0] in {"H", "D", "A"}:
        outcome = "H" if h > a else "D" if h == a else "A"
        return ("W" if outcome == rest[0] else "L",)
    if typ == "DC" and len(rest) == 1 and rest[0] in {"1X", "X2", "12"}:
        outcome = "1" if h > a else "X" if h == a else "2"
        return ("W" if outcome in rest[0] else "L",)
    if typ in {"AH", "DNB"}:
        if typ == "DNB" and len(rest) == 1 and rest[0] in {"H", "A"}:
            team, line = rest[0], 0.0
        elif typ == "AH" and len(rest) >= 2 and rest[0] in {"H", "A"}:
            team, line = rest[0], float(".".join(rest[1:]))
        else:
            raise MarketError("Invalid AH/DNB")
        diff = h - a if team == "H" else a - h
        return tuple(ah_component(diff + ln) for ln in quarter_components(line))
    if typ in {"TOTAL", "TOTAL_AH"} and len(rest) >= 2 and rest[0] in {"OVER", "UNDER"}:
        direction, line = rest[0], float(".".join(rest[1:]))
        vals = []
        for ln in quarter_components(line):
            margin = (h + a - ln) * (1 if direction == "OVER" else -1)
            vals.append(ah_component(margin))
        return tuple(vals)
    if typ == "TEAM" and len(rest) >= 3 and rest[0] in {"H", "A"} and rest[1] in {"OVER", "UNDER"}:
        team, direction, line = rest[0], rest[1], float(".".join(rest[2:]))
        return tuple(ah_component((score_side(h, a, team) - ln) * (1 if direction == "OVER" else -1))
                     for ln in quarter_components(line))
    if typ == "BTTS" and len(rest) == 1 and rest[0] in {"YES", "NO"}:
        ans = h >= 1 and a >= 1
        return ("W" if ans == (rest[0] == "YES") else "L",)
    if typ == "MULTIGOAL" and len(rest) == 2:
        lo, hi = map(int, rest)
        if lo < 0 or hi < lo:
            raise MarketError("Invalid multigoal range")
        return ("W" if lo <= h + a <= hi else "L",)
    if typ == "WIN_TO_NIL" and len(rest) == 1 and rest[0] in {"H", "A"}:
        win_to_nil = (h > a and a == 0) if rest[0] == "H" else (a > h and h == 0)
        return ("W" if win_to_nil else "L",)
    if typ == "CS" and len(rest) == 2:
        gh, ga = map(int, rest)
        if gh < 0 or ga < 0:
            raise MarketError("Invalid score")
        return ("W" if (h, a) == (gh, ga) else "L",)
    if typ == "EURO_AH" and len(rest) == 2 and rest[0] in {"H", "D", "A"}:
        ln = float(rest[1])
        if not ln.is_integer():
            raise MarketError("European handicap must be integral")
        adjusted = h + int(ln) - a
        return ("W" if ("H" if adjusted > 0 else "D" if adjusted == 0 else "A") == rest[0] else "L",)
    if typ == "COMBO" and len(rest) >= 1:
        # Format FT.COMBO.FT.1X2.H&FT.TOTAL.OVER.1.5
        raw = market_id[len(period + ".COMBO."):]
        parts = raw.split("&")
        if len(parts) != 2 or any(not p.startswith(period + ".") for p in parts):
            raise MarketError("COMBO requires two same-period markets")
        leg_a, leg_b = outcomes(parts[0], h, a), outcomes(parts[1], h, a)
        if len(leg_a) != 1 or len(leg_b) != 1 or not is_binary(parts[0]) or not is_binary(parts[1]):
            raise MarketError("COMBO supports binary unambiguous events only")
        return ("W" if leg_a[0] == leg_b[0] == "W" else "L",)
    raise MarketError(f"Unsupported market {market_id}")


def settle_category(legs: tuple[str, ...]) -> str:
    if len(legs) not in (1, 2):
        raise MarketError("Invalid stake division")
    val = sum({"W": 1, "P": 0, "L": -1}[x] for x in legs) / len(legs)
    mapping = {1: "full_win", 0.5: "half_win", 0: "push", -0.5: "half_loss", -1: "full_loss"}
    return mapping[val]


def is_binary(market_id: str) -> bool:
    """Return whether every possible score produces a win/loss without stake return."""
    x = market_id.split(".")
    if len(x) < 3 or x[0] not in {"FT", "HT", "SH"}:
        raise MarketError("Bad market identifier")
    t = x[1]
    if t == "DNB":
        return False
    if t in {"AH", "TOTAL", "TOTAL_AH", "TEAM"}:
        offset = 3 if t in {"AH", "TOTAL", "TOTAL_AH"} else 4
        try:
            line = float(".".join(x[offset:]))
        except ValueError as exc:
            raise MarketError("Malformed Asian/total line") from exc
        quarter_components(line)
        return abs((line % 1) - .5) < 1e-8
    if t == "COMBO":
        raw = market_id[len(x[0] + ".COMBO."):]
        parts = raw.split("&")
        if len(parts) != 2 or any(".COMBO." in p for p in parts):
            raise MarketError("Malformed or nested combo")
        return all(is_binary(p) for p in parts)
    return True


def evaluate_market(matrix: dict[tuple[int, int], float], market_id: str, odds: float | None = None) -> dict[str, Any]:
    if odds is not None and (not isinstance(odds, (float, int)) or not math.isfinite(odds) or odds <= 1):
        raise MarketError("Decimal odds must be > 1")
    buckets: dict[str, float] = defaultdict(float)
    for (h, a), mass in matrix.items():
        cat = settle_category(outcomes(market_id, h, a))
        buckets[cat] += mass
    result = {k: buckets[k] for k in STATES}
    result.update({"market_id": market_id, "binary": is_binary(market_id),
                   "p_positive_return": buckets["full_win"] + buckets["half_win"],
                   "p_non_negative_return": buckets["full_win"] + buckets["half_win"] + buckets["push"],
                   "p_loss": buckets["full_loss"] + buckets["half_loss"]})
    result["p_event"] = buckets["full_win"] if result["binary"] else None
    if odds is not None:
        result["decimal_odds"] = float(odds)
        result["expected_profit_per_unit"] = (odds - 1) * (buckets["full_win"] + .5 * buckets["half_win"]) - (
            buckets["full_loss"] + .5 * buckets["half_loss"])
    else:
        result["decimal_odds"] = None
        result["expected_profit_per_unit"] = None
    return result


def devig_exhaustive(odds: dict[str, float]) -> dict[str, Any]:
    if len(odds) < 2 or any(not math.isfinite(x) or x <= 1 for x in odds.values()):
        raise MarketError("A complete exhaustive group of decimal odds (>1) is required")
    inverses = {k: 1.0 / v for k, v in odds.items()}
    book_sum = sum(inverses.values())
    return {"proportional_no_vig": {k: v / book_sum for k, v in inverses.items()},
            "raw_implied": inverses, "booksum": book_sum, "overround": book_sum - 1,
            "method": "proportional (descriptive estimate, not a verified fair probability)"}


def exclusive_group(market_id: str) -> tuple[str, set[str]] | None:
    """Group membership; normalized only for exhaustive, disjoint, all-win/loss partitions."""
    p = market_id.split(".")
    if len(p) == 3 and p[1] == "1X2":
        return (p[0] + ".1X2", {f"{p[0]}.1X2.{x}" for x in "HDA"})
    if len(p) == 3 and p[1] == "BTTS":
        return (p[0] + ".BTTS", {f"{p[0]}.BTTS.{x}" for x in ("YES", "NO")})
    if len(p) >= 4 and p[1] == "TOTAL" and p[2] in ("OVER", "UNDER") and float(".".join(p[3:])) % 1 == .5:
        line = ".".join(p[3:])
        return (p[0] + ".TOTAL." + line, {f"{p[0]}.TOTAL.{x}.{line}" for x in ("OVER", "UNDER")})
    if len(p) >= 5 and p[1] == "TEAM" and p[3] in ("OVER", "UNDER") and float(".".join(p[4:])) % 1 == .5:
        line = ".".join(p[4:])
        return (p[0] + ".TEAM." + p[2] + "." + line, {f"{p[0]}.TEAM.{p[2]}.{x}.{line}" for x in ("OVER", "UNDER")})
    if len(p) == 4 and p[1] == "EURO_AH" and float(p[-1]).is_integer():
        return (p[0] + ".EURO_AH." + p[-1], {f"{p[0]}.EURO_AH.{x}.{p[-1]}" for x in "HDA"})
    return None


def group_book_quotes(quotes: list[dict[str, Any]]) -> dict[str, Any]:
    lookup = {q["market_id"]: q for q in quotes}
    result = {}
    for item in quotes:
        spec = exclusive_group(item["market_id"])
        if spec is None:
            continue
        key, group = spec
        if key not in result:
            present = group & lookup.keys()
            if present != group:
                result[key] = {"status": "incomplete", "missing": sorted(group - present), "markets": sorted(group)}
                continue
            stamps = {lookup[mid]["timestamp"] for mid in group}
            snapshot_ids = {lookup[mid].get("snapshot_id") for mid in group}
            stamped = [parse_stamp(t) for t in stamps]
            spread_seconds = (max(stamped) - min(stamped)).total_seconds()
            # 60s is an explicit *collection synchrony tolerance*, not a confidence threshold.
            common_snapshot = len(snapshot_ids) == 1 and None not in snapshot_ids
            if spread_seconds > 0 and (not common_snapshot or spread_seconds > 60):
                result[key] = {"status": "asynchronous", "markets": sorted(group),
                               "timestamp_spread_seconds": spread_seconds,
                               "reason": "Require exact shared timestamp or common snapshot_id with <=60 second observed spread"}
                continue
            result[key] = {"status": "complete", "markets": sorted(group),
                           "timestamp_spread_seconds": spread_seconds,
                           "reference_timestamp": min(stamped).isoformat(),
                           "source_snapshot_id": next(iter(snapshot_ids)) if common_snapshot else None,
                           **devig_exhaustive({mid: lookup[mid]["decimal_odds"] for mid in sorted(group)})}
    return result


def cross_market_consistency(groups: dict[str, Any]) -> dict[str, Any]:
    """Inspect probability monotonicity only for synchronized, completed Bet365 de-vig groups.

    Descriptive cross-group no-vig inconsistency is NOT a proven arbitrage, since
    each line has independently removed bookmaker margin by approximation.
    """
    p, origin = {}, {}
    for name, group in groups.items():
        if group["status"] != "complete":
            continue
        for mid, v in group["proportional_no_vig"].items():
            p[mid] = v
            origin[mid] = (group.get("reference_timestamp"), group.get("source_snapshot_id"))

    def same_capture(x: str, y: str) -> bool:
        tx, sx = origin[x]
        ty, sy = origin[y]
        return tx == ty or bool(sx and sx == sy and abs((parse_stamp(tx)-parse_stamp(ty)).total_seconds())<=60)

    checks = []
    def compare(less: str, more: str, rule: str) -> None:
        if less not in p or more not in p:
            return
        if not same_capture(less, more):
            checks.append({"rule": rule, "left": less, "right": more,
                           "status": "NOT_COMPARABLE_DIFFERENT_SNAPSHOT"})
            return
        delta = p[less] - p[more]
        checks.append({"rule": rule, "left": less, "right": more,
                       "p_left_minus_right": delta,
                       "status": "DESCRIPTIVE_VIOLATION" if delta > 1e-9 else "ORDER_CONSISTENT",
                       "interpretation": "Cross-market proportional-no-vig estimate; a violation can reflect margin allocation, price timing or genuine model/pricing disagreement"})

    totals = {}
    for mid in p:
        m = re.fullmatch(r"FT\.TOTAL\.OVER\.(\d+\.5)", mid)
        if m:
            totals[float(m.group(1))] = mid
    for l, hi in zip(sorted(totals)[:-1], sorted(totals)[1:]):
        compare(totals[hi], totals[l], "HIGHER_TOTAL_LINE_CANNOT_BE_MORE_LIKELY")
    if "FT.BTTS.YES" in p:
        if 1.5 in totals:
            compare("FT.BTTS.YES", totals[1.5], "BTTS_IMPLIES_OVER_1.5")
        for team in ("H", "A"):
            mid = f"FT.TEAM.{team}.OVER.0.5"
            compare("FT.BTTS.YES", mid, "BTTS_IMPLIES_TEAM_GOAL")
    violations = sum(x["status"] == "DESCRIPTIVE_VIOLATION" for x in checks)
    return {"checks": checks, "descriptive_violations": violations,
            "warning": "No-vig cross-line constraints do not prove sure-bet or independent accuracy; quote snapshots and margin treatment matter"}


def perturbation_sensitivity(lh: float, la: float, market_id: str, frac: float = .10) -> dict[str, Any]:
    """Diagnostic stress test, NOT a predictive interval or data-estimated parameter CI."""
    if not 0 < frac < 1:
        raise MarketError("frac must be in (0,1)")
    rows = []
    for mh in (1 - frac, 1.0, 1 + frac):
        for ma in (1 - frac, 1.0, 1 + frac):
            matrix = joint_from_lambda(lh * mh, la * ma)["matrix"]
            y = evaluate_market(matrix, market_id)
            rows.append(y["p_event"] if y["binary"] else y["p_positive_return"])
    return {"stress_fraction_each_lambda": frac, "metric": "p_event" if is_binary(market_id) else "p_positive_return",
            "min": min(rows), "max": max(rows), "center": rows[4], "scenarios": len(rows),
            "warning": "Hypothetical stress grid, not a confidence/credible interval"}


def dependence(matrix: dict[tuple[int, int], float], market_a: str, market_b: str) -> dict[str, Any]:
    if not is_binary(market_a) or not is_binary(market_b):
        raise MarketError("Dependence comparison requires two binary markets")
    pa = pb = joint = 0.0
    for (h, a), mass in matrix.items():
        a_w = outcomes(market_a, h, a) == ("W",)
        b_w = outcomes(market_b, h, a) == ("W",)
        pa += mass * a_w
        pb += mass * b_w
        joint += mass * (a_w and b_w)
    union = pa + pb - joint
    return {"a": market_a, "b": market_b, "p_a": pa, "p_b": pb, "p_joint": joint,
            "p_b_given_a": joint / pa if pa > 0 else None,
            "p_a_given_b": joint / pb if pb > 0 else None,
            "covariance": joint - pa * pb,
            "jaccard": joint / union if union > 0 else None,
            "subset_a_of_b": abs(joint - pa) < 1e-9,
            "subset_b_of_a": abs(joint - pb) < 1e-9,
            "independent_signals": False}


def validate_input(data: dict[str, Any]) -> dict[str, Any]:
    match = data.get("match", {})
    for field in ("event_id", "home", "away", "competition", "kickoff", "cutoff"):
        if not match.get(field):
            raise MarketError(f"Missing match.{field}")
    kick, cut = parse_stamp(match["kickoff"]), parse_stamp(match["cutoff"])
    if cut >= kick:
        raise MarketError("cutoff must be strictly before kickoff")
    if match.get("duration_minutes", 90) != 90 or match.get("format", "association_football") != "association_football":
        raise MarketError("Only regular 90-minute association football")
    quotes = data.get("quotes", [])
    seen = set()
    for q in quotes:
        for field in ("market_id", "decimal_odds", "timestamp", "source"):
            if field not in q:
                raise MarketError(f"Missing quote.{field}")
        if q["market_id"] in seen:
            raise MarketError("Duplicate quote per market; resolve snapshot first")
        seen.add(q["market_id"])
        if not isinstance(q["decimal_odds"], (int, float)) or not math.isfinite(q["decimal_odds"]) or q["decimal_odds"] <= 1:
            raise MarketError("Invalid decimal odds")
        timestamp = parse_stamp(q["timestamp"])
        if timestamp > cut:
            raise MarketError("Quote timestamp later than cutoff: future leakage")
        if q["source"] != "Bet365":
            raise MarketError("This Bet365 workflow requires source=Bet365; don't combine providers")
        if not q["market_id"].startswith(("FT.", "HT.", "SH.")):
            raise MarketError("Malformed quote market_id")
        # Fail closed on malformed ids even when no model is provided.
        outcomes(q["market_id"], 0, 0)
    ga = data.get("goal_analyzer")
    if ga is not None:
        if not isinstance(ga, dict) or ga.get("version") != "GA2-RESEARCH-20261029-v1":
            raise MarketError("GA version mismatch: explicit adapter needed")
        if ga.get("source") is None or ga.get("cutoff") is None:
            raise MarketError("GA provenance and cutoff required")
        if parse_stamp(ga["cutoff"]) > cut:
            raise MarketError("GA output after cutoff: future leakage")
        if bool(ga.get("operational_eligible", False)):
            # Fail closed: frozen reference is not operationally validated.
            raise MarketError("Frozen GA reference cannot claim operational_eligible=true")
        for k in ("lambda_home", "lambda_away"):
            if k not in ga:
                raise MarketError("GA missing " + k)
        joint_from_lambda(ga["lambda_home"], ga["lambda_away"])
        for period, d in ga.get("periods", {}).items():
            if period not in ("HT", "SH"):
                raise MarketError("Only HT and SH period adapters available")
            if not isinstance(d, dict) or not d.get("source") or not d.get("method") or not d.get("validation_state"):
                raise MarketError("Period adapter requires source/method/validation_state")
            joint_from_lambda(d["lambda_home"], d["lambda_away"])
    ba = data.get("bookmaker_analyzer")
    if ba is not None:
        if not isinstance(ba, dict) or ba.get("protocol") != "RC15":
            raise MarketError("BA adapter expects protocol RC15")
        if ba.get("source") != "Bet365":
            raise MarketError("BA source must be Bet365")
        if not ba.get("timestamp") or parse_stamp(ba["timestamp"]) > cut:
            raise MarketError("BA data after cutoff or missing timestamp")
        for mid, prob in ba.get("market_probabilities", {}).items():
            outcomes(mid, 0, 0)
            require_probability(prob, "BA probability")
    return {"cutoff": cut, "kickoff": kick}


def run_analysis(data: dict[str, Any]) -> dict[str, Any]:
    ts = validate_input(data)
    ga = data.get("goal_analyzer")
    ba = data.get("bookmaker_analyzer")
    quotes = data.get("quotes", [])
    quoted = {q["market_id"]: q for q in quotes}
    groups = group_book_quotes(quotes)
    bookmaker_p = {}
    for g in groups.values():
        if g["status"] == "complete":
            bookmaker_p.update(g["proportional_no_vig"])
    ba_p = (ba or {}).get("market_probabilities", {})
    # RC15 probabilities, if supplied, remain a separate labelled estimator,
    # not another statistically independent bookmaker signal.
    out = {"protocol": VERSION, "match": data["match"],
           "mode": "MODEL_AND_BOOKMAKER" if ga and (quotes or ba) else "MODEL_ONLY" if ga else "BOOKMAKER_ONLY" if (quotes or ba) else "NO_INPUT",
           "operational_eligible": False, "betting_recommendation": None,
           "selection_status": "ABSTAIN_OPERATIONAL_NOT_VALIDATED" if ga else "ABSTAIN_INSUFFICIENT_INDEPENDENT_EVIDENCE",
           "pricing_groups": groups, "cross_market_consistency": cross_market_consistency(groups), "markets": [], "dependence": [], "notes": [
              "Bet365-derived BA signals and Bet365 quote groups are the SAME information source.",
              "GA model probabilities are research-only and not calibrated for operational use.",
              "Bookmaker-only mode describes market price structure; it cannot independently validate selections.",
              "Proportional no-vig is a descriptive approximation, not a validated true-probability estimator.",
              "A low quote is not evidence of bookmaker liabilities or exposure."]}
    matrix = None
    period_matrices = {}
    if ga:
        j = joint_from_lambda(ga["lambda_home"], ga["lambda_away"])
        matrix = j["matrix"]
        out["model_metadata"] = {"source_version": ga["version"], "source": ga["source"],
              "lambda_home": ga["lambda_home"], "lambda_away": ga["lambda_away"],
              "omitted_mass": j["omitted_mass"], "validation_state": ga.get("validation_state", "RESEARCH_ONLY"),
              "source_cutoff": ga["cutoff"],
              "source_hash": ga.get("source_hash"), "training_n": ga.get("training_n"),
              "ESS": ga.get("ESS"), "unseen_teams": ga.get("unseen_teams"),
              "gts_descriptive_only": ga.get("gts"), "gts_reason": ga.get("gts_reason")}
        for period, source in ga.get("periods", {}).items():
            period_matrices[period] = joint_from_lambda(source["lambda_home"], source["lambda_away"])["matrix"]
        if period_matrices:
            out["model_metadata"]["periods"] = {p: {"method": d["method"], "source": d["source"],
                "validation_state": d["validation_state"]} for p,d in ga["periods"].items()}
            out["notes"].append("HT/SH independent-segment predictions do NOT model HT-to-SH game-state dependence.")
    if ba:
        out["bookmaker_rc15"] = {"protocol": ba["protocol"], "source": ba["source"],
                                   "timestamp": ba["timestamp"], "validation_state": ba.get("validation_state", "experimental"),
                                   "market_probabilities": ba_p,
                                   "confidence": ba.get("confidence"), "scenario_intelligence": ba.get("scenario_intelligence"),
                                   "market_stability": ba.get("market_stability"),
                                   "note": "RC15 fields are pass-through metadata; no arbitrary blending or recalibration."}
    requested = data.get("requested_markets") or sorted(quoted)
    quote_ages = {}
    for mid, q in quoted.items():
        quote_ages[mid] = round((ts["cutoff"] - parse_stamp(q["timestamp"])).total_seconds()/60, 2)
    for mid in requested:
        period = mid.split(".", 1)[0]
        mtx = matrix if period == "FT" else period_matrices.get(period)
        if period not in ("FT", "HT", "SH"):
            raise MarketError(f"Invalid period in {mid}")
        q = quoted.get(mid)
        row: dict[str, Any] = {"market_id": mid, "market_odds": q["decimal_odds"] if q else None,
            "bookmaker_p_no_vig": bookmaker_p.get(mid), "bookmaker_rc15_p": ba_p.get(mid),
            "odds_age_minutes_at_cutoff": quote_ages.get(mid),
            "quote_source": q["source"] if q else None}
        if mtx is not None:
            ev = evaluate_market(mtx, mid, q["decimal_odds"] if q else None)
            row.update(ev)
            if period == "FT":
                lh, la = ga["lambda_home"], ga["lambda_away"]
            else:
                lh, la = ga["periods"][period]["lambda_home"], ga["periods"][period]["lambda_away"]
            row["sensitivity_10pct_lambda"] = perturbation_sensitivity(lh, la, mid)
            row["comparison"] = "MODEL_MINUS_BOOK_PROBABILITY" if ev["binary"] and mid in bookmaker_p else "NOT_COMPARABLE"
            row["p_model_minus_bookmaker"] = ev["p_event"] - bookmaker_p[mid] if ev["binary"] and mid in bookmaker_p else None
            row["p_model_minus_rc15"] = ev["p_event"] - ba_p[mid] if ev["binary"] and mid in ba_p else None
            if row["p_model_minus_bookmaker"] is not None:
                spec = exclusive_group(mid)
                if spec and len(spec[1]) == 2:
                    row["directional_concordance_binary"] = (ev["p_event"] - .5) * (bookmaker_p[mid] - .5) > 0
                else:
                    row["directional_concordance_binary"] = None  # multiway 1X2: use leader, not >50% test
        else:
            row["status"] = "PRICING_ONLY_NOT_AN_INDEPENDENT_FORECAST" if q or (mid in ba_p) else "NOT_EVALUATED_NO_PERIOD_MODEL"
            if period != "FT":
                row["reason"] = "Period model not independently supplied; no FT substitution"
        out["markets"].append(row)
    if matrix:
        for a, b in data.get("dependence_pairs", []):
            if not (a.startswith("FT.") and b.startswith("FT.")):
                raise MarketError("Dependence pairs currently support FT only")
            out["dependence"].append(dependence(matrix, a, b))
    group_hierarchy = {}
    if matrix:
        for group_id, group in groups.items():
            if group["status"] == "complete" and all(x.split(".")[0] == "FT" for x in group["markets"]):
                mdl = {mid: evaluate_market(matrix, mid)["p_event"] for mid in group["markets"]}
                if any(p is None for p in mdl.values()):
                    continue
                group_hierarchy[group_id] = {
                    "model_leader": max(mdl, key=mdl.get),
                    "bookmaker_leader": max(group["proportional_no_vig"], key=group["proportional_no_vig"].get),
                    "same_leader": max(mdl, key=mdl.get) == max(group["proportional_no_vig"], key=group["proportional_no_vig"].get),
                    "independent_confirmations": 1 if ga else 0,
                    "note": "Same scenario preference is descriptive agreement, not predictive validation."
                }
    out["scenario_hierarchy"] = group_hierarchy
    # Descriptive shortlist only: no automatic cross-market recommendation or numeric confidence.
    out["analytical_candidates"] = [{"market_id": g["model_leader"], "group": key,
                                      "reason": "Same group leader in independent GA research forecast and Bet365 no-vig pricing",
                                      "validation_status": "DESCRIPTIVE_NOT_CERTIFIED"}
                                     for key, g in sorted(group_hierarchy.items()) if g["same_leader"]]
    out["analytical_primary_selection"] = None  # no validated common utility/risk criterion
    if matrix:
        out["model_scenarios"] = {
            "top_exact_scores": [{"home_goals": h, "away_goals": a, "p": p} for (h,a),p in sorted(matrix.items(), key=lambda x:-x[1])[:6]],
            "total_goals_0_to_4": {str(i): sum(v for (h,a),v in matrix.items() if h+a==i) for i in range(5)},
            "note": "Conditional on GA experimental independent-Poisson distribution; not a bookmaker reconstruction"
        }
    out["confidence"] = {"classification": "NOT_CALIBRATED", "numeric_score": None,
                         "evidence_quality": "RESEARCH_MODEL_UNVALIDATED" if ga else "BOOKMAKER_ONLY",
                         "robustness": "DIAGNOSTIC_STRESS_ONLY" if ga else "NOT_ASSESSED",
                         "economic_value": "SECONDARY_INFORMATION_ONLY"}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="Input JSON path")
    ap.add_argument("-o", "--output", help="Output JSON path")
    args = ap.parse_args()
    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    res = run_analysis(data)
    encoded = json.dumps(res, indent=2, ensure_ascii=False, allow_nan=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(encoded + "\n")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
