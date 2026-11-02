"""BETPILOT | Prioritize five flagship football competitions in PitchAPI historical backfill.

The Premier League has its own unchanged daily GitHub workflow. This planner
selects the other four from the existing 25-league matrix; all other 21 retain
their original independent checkpoints on a weekly, lower-volume schedule.
Neither model weights nor historical QA definitions are changed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

FOCUS = ("ita-seriea", "fra-ligue1", "esp-laliga", "ger-bundesliga")
PREMIER = "eng-premier"
DAILY_CRON = "11 2 * * *"
WEEKLY_CRON = "27 6 * * 0"

def plan(manifest, *, event_name, cron="", scope="priority", manual_max=""):
    if manifest.get("schema") != "BETPILOT_PITCHAPI_LEAGUE_PINNED_26_v1":
        raise ValueError("Unexpected league catalogue schema")
    leagues = manifest.get("leagues")
    if not isinstance(leagues, dict) or len(leagues) != 26 or PREMIER not in leagues:
        raise ValueError("Expected 26 unique pinned competitions, including Premier League")
    if len(set(FOCUS)) != 4 or any(k not in leagues or k == PREMIER for k in FOCUS):
        raise ValueError("One or more flagship league IDs are missing")
    others = [k for k in leagues if k != PREMIER and k not in FOCUS]
    if len(others) != 21:
        raise ValueError("Expected 21 non-priority leagues")
    if event_name == "schedule":
        if cron == DAILY_CRON:
            selected, level, batch = list(FOCUS), "priority", 80
        elif cron == WEEKLY_CRON:
            selected, level, batch = others, "other", 6
        else:
            raise ValueError("Unrecognized schedule; refuse unintended full acquisition")
    elif event_name in ("workflow_dispatch", "push"):
        if event_name == "push":
            scope = "priority"
        if scope not in ("priority", "other", "all"):
            raise ValueError("Invalid manual league scope")
        selected = (list(FOCUS) if scope == "priority" else
                    others if scope == "other" else list(FOCUS) + others)
        level = scope
        batch = {"priority":80,"other":6,"all":12}[scope]
        if event_name == "workflow_dispatch" and manual_max:
            try:
                batch = int(manual_max)
            except ValueError as e:
                raise ValueError("Non-numeric manual batch") from e
    else:
        raise ValueError("Unrecognized event")
    if not 1 <= batch <= 120:
        raise ValueError("Per-league batch must be 1..120")
    if not selected or PREMIER in selected or len(set(selected)) != len(selected):
        raise ValueError("Invalid or overlapping selected leagues")
    return {"keys":selected, "limit":batch, "scope":level,
            "focus_five_including_separate_premier": [PREMIER, *FOCUS],
            "selected_count":len(selected)}

def main(argv=None):
    p=argparse.ArgumentParser(description="Plan fixed PITCHAPI flagship-vs-other GH Actions matrix")
    p.add_argument("--pins",type=Path,default=Path("data/pitchapi/league_pins_26.json"))
    p.add_argument("--event",required=True)
    p.add_argument("--cron",default="")
    p.add_argument("--scope",default="priority")
    p.add_argument("--manual-max",default="")
    a=p.parse_args(argv)
    cfg=plan(json.loads(a.pins.read_text("utf-8")),event_name=a.event,
             cron=a.cron,scope=a.scope,manual_max=a.manual_max)
    if not os.environ.get("GITHUB_OUTPUT"):
        raise SystemExit("GITHUB_OUTPUT missing")
    with open(os.environ["GITHUB_OUTPUT"],"a",encoding="utf-8") as f:
        f.write("keys="+json.dumps(cfg["keys"],separators=(",",":"))+"\n")
        f.write("limit="+str(cfg["limit"])+"\n")
        f.write("scope="+cfg["scope"]+"\n")
    print("PITCHAPI_FOCUS_PRIORITY_PLAN "+json.dumps(cfg,sort_keys=True))

if __name__=="__main__":
    main()
