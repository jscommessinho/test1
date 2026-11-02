"""Incremental GA3 read-only source overlay: historical seasons + completed daily.

Only the frozen Goal Analyzer CLI is permitted to calculate probabilities.
This module materializes the existing PitchAPI history *format* from verified
original archive records and sufficiently mature daily source snapshots.
Never refetches paid data and never overrides original historical records.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from betpilot.pitchapi_history_archive import load_archive, save, sha256
from betpilot_v3.pitchapi_preflight import (
    PreflightBlocked, instant, read_archive, require
)


def merged_original_and_daily(history_path, warehouse, target, cutoff, destination):
    source = Path(history_path)
    base = json.loads(source.read_text("utf8"))
    league = base.get("league") or {}
    require(base.get("provider") == "PitchAPI" and
            (league.get("id"), league.get("name"), league.get("country")) ==
            (target["pitchapi_league_id"], target["competition"], target["country"]),
            "GA3_ROLLING_LEAGUE_CONFLICT", "Pinned source and target league differ")
    start_day = date.fromisoformat(base["as_of_utc_date"])
    as_of = (cutoff.astimezone(timezone.utc)
             if isinstance(cutoff, datetime) and cutoff.tzinfo is not None
             else instant(cutoff))
    require(start_day <= as_of.date(), "GA3_ROLLING_ASOF_CONFLICT",
            "Cannot roll historical archive into the past")
    original = load_archive(source, league, base["as_of_utc_date"])
    current_season = target["season"]
    original_current_days = [
        date.fromisoformat(row["date"])
        for row in original["records"].values()
        if row["season"] == current_season
    ]
    latest_known = max(original_current_days) if original_current_days else None
    updated = json.loads(json.dumps(original))
    updated["as_of_utc_date"] = as_of.date().isoformat()
    root = Path(warehouse)
    require(root.is_dir(), "GA3_ROLLING_WAREHOUSE_MISSING",
            "Original daily source checkout unavailable")
    merged, rejected, duplicate = 0, {}, 0
    seen = set()
    for path in sorted(root.glob("daily/*/*/*.json.gz")):
        require(not path.is_symlink(), "UNSAFE_ARCHIVE", "Daily archive symlink")
        day = read_archive(path)
        for mid, row in day["matches"].items():
            require(mid not in seen, "GA3_ROLLING_DUPLICATE_DAILY",
                    "Daily match appears twice: " + mid)
            seen.add(mid)
            fixture = row.get("fixture") or {}
            lid = fixture.get("league_id") or (fixture.get("league") or {}).get("id")
            if lid != league["id"] or mid == target["pitchapi_event_id"]:
                continue
            if mid in updated["records"]:
                old = updated["records"][mid]
                require(fixture.get("home_team",{}).get("id") == old["home"]["id"]
                        and fixture.get("away_team",{}).get("id") == old["away"]["id"]
                        and fixture.get("score_home") == old["home"]["goals"]
                        and fixture.get("score_away") == old["away"]["goals"],
                        "GA3_ROLLING_OVERLAP_CONFLICT",
                        "Daily and historical source disagree on " + mid)
                duplicate += 1
                continue
            if mid in updated["warnings"] or mid in updated["rejected"]:
                rejected["historical_quality_partition"] = rejected.get("historical_quality_partition",0)+1
                continue
            if row.get("status") != "PASS_INTERNAL":
                rejected["daily_quality"] = rejected.get("daily_quality",0)+1
                continue
            try:
                event_day = date.fromisoformat(fixture["date"])
                observed = instant(row["retrieved_at_utc"])
                event_time = instant(fixture["time_utc"])
                require(fixture["id"] == mid and fixture["status"] == "finished"
                        and observed <= as_of and event_time <= observed
                        and event_day + timedelta(days=3) <= as_of.date(),
                        "GA3_DAILY_NOT_MATURE", "Daily match not available before cutoff+lag")
                ht = fixture["home_team"]
                at = fixture["away_team"]
                require(ht["id"] != at["id"], "GA3_TEAM_CONFLICT",
                        "Identical source team IDs")
                goals = (fixture["score_home"], fixture["score_away"])
                require(all(isinstance(n,int) and not isinstance(n,bool)
                            and 0 <= n <= 30 for n in goals),
                        "GA3_GOALS_INVALID", "No valid final score")
                metrics = [row["home_shots"], row["away_shots"]]
                require(all(isinstance(x,dict)
                            and isinstance(x.get("shots"),int)
                            and not isinstance(x["shots"],bool)
                            and x["shots"] >= 0
                            and isinstance(x.get("xg"),(int,float))
                            and not isinstance(x["xg"],bool)
                            and math.isfinite(x["xg"]) and x["xg"] >= 0
                            for x in metrics),
                        "GA3_DAILY_METRICS_INVALID", "No verified xG/shots")
                # Daily snapshots often omit season. Infer only within the
                # already observed current season, not from calendar year alone.
                season = fixture.get("season")
                if not season:
                    require(latest_known is not None and
                            latest_known < event_day <= as_of.date()
                            and (event_day-latest_known).days <= 120,
                            "GA3_DAILY_SEASON_UNRESOLVED",
                            "Daily season cannot be anchored to already verified current-season history")
                    season = current_season
                require(season == current_season or
                        any(v["season"] == season for v in original["records"].values()),
                        "GA3_DAILY_SEASON_UNKNOWN", "Unverified provider season")
                rec = {
                    "match_id": mid, "league_id": league["id"],
                    "season": season, "date": fixture["date"],
                    "home": {"id":ht["id"], "name":ht["name"],
                             "goals":goals[0], "xg":round(metrics[0]["xg"],6),
                             "shots":metrics[0]["shots"]},
                    "away": {"id":at["id"], "name":at["name"],
                             "goals":goals[1], "xg":round(metrics[1]["xg"],6),
                             "shots":metrics[1]["shots"]},
                    "internal_absolute_xg_deltas":
                        row.get("internal_quality",{}).get("internal_absolute_deltas",{}),
                    "qa_internal":"PASS",
                    "qa_external":"OFFICIAL_REFERENCE_NOT_VERIFIED",
                    "source": "PitchAPI original worldwide daily /shots and /stats; provider-internal PASS",
                    "season_provenance": ("PITCHAPI_DAILY_FIXTURE" if fixture.get("season")
                                          else "CURRENT_SEASON_ANCHORED_TO_PRIOR_PINNED_HISTORY"),
                }
                rec["source_digest"] = sha256(rec)
                rec["retrieved_at_utc"] = observed.isoformat()
                updated["records"][mid] = rec
                merged += 1
            except (PreflightBlocked, KeyError, ValueError, TypeError) as ex:
                code = getattr(ex,"code",type(ex).__name__)
                rejected[code] = rejected.get(code,0)+1
    # The frozen GA3 archive reader independently rechecks IDs, digests, QA,
    # league and temporal lag; we check the newly written material as well.
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    save(dest, updated)
    load_archive(dest, league, updated["as_of_utc_date"])
    return {
        "status":"GA3_ROLLING_HISTORY_VERIFIED",
        "historical_records":len(original["records"]),
        "added_daily_records":merged,
        "duplicate_records":duplicate,
        "total_records":len(updated["records"]),
        "current_season":current_season,
        "daily_exclusions":rejected,
        "historical_as_of":base["as_of_utc_date"],
        "rolling_as_of":updated["as_of_utc_date"],
        "original_history_unchanged": True,
    }
