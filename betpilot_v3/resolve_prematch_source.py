"""Read-only authenticated PitchAPI league discovery for a generic GH Actions cache restore.

No history download and no odds request. Max three existing PitchAPI GETs.
This is a separately auditable selection step; the main runner independently
checks provider identity a second time before executing a frozen model.
"""
import argparse
import json
import os
from pathlib import Path

from betpilot.pitchapi_gts_probe import Client
from betpilot_v3.general_fixture import load_registry, pinned
from betpilot_v3.pitchapi_preflight import discover, PreflightBlocked


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--request", required=True)
    p.add_argument("--day-utc", required=True)
    p.add_argument("--country-code")
    p.add_argument("--competition")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--registry", type=Path,
                   default=Path(__file__).resolve().parents[1] /
                   "data/pitchapi/league_pins_77.json")
    args = p.parse_args(argv)
    summary = {"mode": "RESEARCH_ONLY", "betting_enabled": False,
               "status": "NO_BET_SOURCE_UNVERIFIED"}
    try:
        if not os.environ.get("PITCHAPI_API_KEY"):
            raise PreflightBlocked("PITCHAPI_KEY_UNAVAILABLE", "No authorized key")
        catalogue = load_registry(args.registry)
        target = discover(Client(os.environ["PITCHAPI_API_KEY"], budget=3, pause=.2),
                          args.request, args.day_utc, args.country_code,
                          args.competition)
        pin = pinned(target["pitchapi_league_id"], target["competition"],
                     target["country"], catalogue)
        key = pin["registry_key"]
        if not key.replace("-", "").isalnum():
            raise PreflightBlocked("UNSAFE_PINNED_KEY", key)
        summary.update(status="PINNED_MATCH_VERIFIED",
                       league_key=key, league_id=target["pitchapi_league_id"],
                       country=target["country"], competition=target["competition"],
                       season=target["season"], kickoff=target["kickoff"],
                       pitchapi_event_id=target["pitchapi_event_id"])
        if "GITHUB_OUTPUT" in os.environ:
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf8") as h:
                h.write("league_key=" + key + "\n")
    except (PreflightBlocked, KeyError, ValueError, TypeError, OSError) as e:
        summary.update(reason_code=getattr(e,"code",type(e).__name__),
                       reason=str(e)[:250])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n",
                           encoding="utf8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"]=="PINNED_MATCH_VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
