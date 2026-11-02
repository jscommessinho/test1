"""Read-only credential connectivity preflight for PitchAPI + API-Sports.

No web search, fixture/odds request, history download or stored credential.
This module makes *only* PitchAPI /v1/leagues (1 capped request) and API-Sports
/status (quota check) when both credentials are supplied in environment.
A diagnostic is not a match analysis and is not proof of model functionality.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def check(*, pitchapi_factory=None, apisports_factory=None):
    result = {"schema": "BETPILOT_PROVIDER_PREFLIGHT_V1", "status": "NOT_STARTED",
              "pitchapi": "NOT_CHECKED", "api_sports": "NOT_CHECKED",
              "real_match_analysis_completed": False,
              "web_search_used": False, "api_calls": {"pitchapi_max": 1, "api_sports_status_max": 1}}
    # Credentials are never concatenated with messages or written to disk.
    pitch_key = os.environ.get("PITCHAPI_API_KEY", "")
    sports_key = os.environ.get("APISPORTS_KEY", "")
    if not pitch_key or not sports_key:
        result.update(status="AUTHENTICATION_ERROR",
                      pitchapi="CREDENTIAL_PRESENT" if pitch_key else "MISSING_CREDENTIAL",
                      api_sports="CREDENTIAL_PRESENT" if sports_key else "MISSING_CREDENTIAL")
        return result
    try:
        if pitchapi_factory is None:
            from betpilot.pitchapi_gts_probe import Client as PitchClient
            pitchapi_factory = PitchClient
        client = pitchapi_factory(pitch_key, budget=1, pause=0)
        data = client.get("/v1/leagues")
        if not isinstance(data, dict):
            raise ValueError("Invalid PitchAPI response")
        result["pitchapi"] = "AUTHENTICATED_RESPONSE"
    except Exception as e:
        result.update(pitchapi="CONNECTION_OR_AUTH_FAILED", pitchapi_error_type=type(e).__name__)
    try:
        if apisports_factory is None:
            from betpilot.quota_smart import QuotaClient as SportsClient
            apisports_factory = SportsClient
        client = apisports_factory(sports_key, reserve=20, max_calls=1)
        quota = client.preflight()
        if not isinstance(quota, dict) or "quota_remaining_at_start" not in quota:
            raise ValueError("Invalid API-Sports quota response")
        result["api_sports"] = "AUTHENTICATED_STATUS_AND_QUOTA"
        result["api_sports_quota_above_reserve"] = True
    except Exception as e:
        result.update(api_sports="CONNECTION_OR_AUTH_FAILED", api_sports_error_type=type(e).__name__)
    result["status"] = ("PROVIDER_CONNECTIVITY_VERIFIED" if
                        result["pitchapi"] == "AUTHENTICATED_RESPONSE" and
                        result["api_sports"] == "AUTHENTICATED_STATUS_AND_QUOTA"
                        else "PROVIDER_PREFLIGHT_BLOCKED")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/betpilot/provider_preflight.json"))
    args = parser.parse_args(argv)
    result = check()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf8")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PROVIDER_CONNECTIVITY_VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
