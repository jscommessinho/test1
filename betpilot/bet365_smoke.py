#!/usr/bin/env python3
"""BETPILOT PREMATCH: minimal authenticated API-Football → Bet365 RC15 input.
Standalone deployment smoke test. NOT a replacement for frozen RC15/MA2/GA2.
Requires APISPORTS_KEY only through environment variables.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HOST = "https://v3.football.api-sports.io"
FIELDS = {
    1: {"Home": "q1", "Draw": "qx", "Away": "q2"},
    5: {"Over 2.5": "qo", "Under 2.5": "qu"},
    8: {"Yes": "qgg", "No": "qng"},
}
REQUIRED = ("q1", "qx", "q2", "qo", "qu", "qgg", "qng")


class AcquisitionError(Exception):
    pass


def utc_stamp(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Timezone missing")
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError) as error:
        raise AcquisitionError("Invalid provider timestamp") from error


def extract_prices(odds_data, fixture_id, now):
    matches = [
        row for row in odds_data.get("response", [])
        if row.get("fixture", {}).get("id") == fixture_id
    ]
    if not matches:
        raise AcquisitionError("No odds available for target fixture")
    extracted = []
    for match in matches:
        update = utc_stamp(match.get("update"))
        if update > now:
            raise AcquisitionError("Provider update timestamp is in the future")
        for bookmaker in match.get("bookmakers", []):
            if bookmaker.get("id") != 8 or bookmaker.get("name", "").casefold() != "bet365":
                continue
            prices = {}
            for market in bookmaker.get("bets", []):
                mapping = FIELDS.get(market.get("id"))
                if mapping is None:
                    continue
                for entry in market.get("values", []):
                    field = mapping.get(entry.get("value"))
                    if field is None:
                        continue
                    try:
                        price = float(entry["odd"])
                    except (ValueError, TypeError, KeyError) as error:
                        raise AcquisitionError("Unparseable quote: " + field) from error
                    if not 1 < price < 100000:
                        raise AcquisitionError("Quote out of range: " + field)
                    if field in prices and prices[field] != price:
                        raise AcquisitionError("Conflicting duplicate quote: " + field)
                    prices[field] = price
            if all(field in prices for field in REQUIRED):
                extracted.append((update, prices))
    if len(extracted) != 1:
        raise AcquisitionError("Missing or ambiguous seven-price Bet365 snapshot")
    return extracted[0]


def valid_fixture(data, fixture_id, now):
    rows = data.get("response", [])
    if len(rows) != 1 or rows[0].get("fixture", {}).get("id") != fixture_id:
        raise AcquisitionError("Fixture identity not unique")
    row = rows[0]
    match = row["fixture"]
    kickoff = utc_stamp(match["date"])
    if kickoff <= now or match.get("status", {}).get("short") not in ("NS", "TBD"):
        raise AcquisitionError("Match is not strictly prematch")
    teams = row["teams"]
    if teams["home"]["id"] == teams["away"]["id"]:
        raise AcquisitionError("Home and away teams have the same ID")
    return {
        "fixture_id": fixture_id,
        "kickoff_utc": kickoff.isoformat(),
        "home": teams["home"]["name"],
        "home_id": teams["home"]["id"],
        "away": teams["away"]["name"],
        "away_id": teams["away"]["id"],
        "league": row.get("league", {}).get("name"),
        "league_id": row.get("league", {}).get("id"),
    }


class Client:
    def __init__(self, key, max_calls=2):
        if not key or not key.strip():
            raise AcquisitionError("APISPORTS_KEY secret missing")
        self.key = key.strip()
        self.count = 0
        self.max_calls = max_calls

    def get(self, endpoint, **params):
        if endpoint not in ("odds", "fixtures") or self.count >= self.max_calls:
            raise AcquisitionError("Endpoint blocked or two-call safety cap reached")
        url = HOST + "/" + endpoint + "?" + urlencode(params)
        req = Request(url, headers={
            "x-apisports-key": self.key,
            "Accept": "application/json",
            "User-Agent": "BETPILOT-PREMATCH-smoke/1.0",
        })
        self.count += 1
        try:
            with urlopen(req, timeout=20) as response:
                data = json.load(response)
        except HTTPError as error:
            raise AcquisitionError("Provider HTTP error: " + str(error.code)) from error
        except (URLError, TimeoutError, ValueError) as error:
            raise AcquisitionError("Provider request failed: " + type(error).__name__) from error
        if data.get("errors"):
            raise AcquisitionError("API-Football reported an error; verify plan/quota")
        return data


def run(client, fixture_id=None, date=None, now=None):
    now = now or datetime.now(timezone.utc)
    if fixture_id is None:
        query_date = date or (now + timedelta(days=1)).date().isoformat()
        data = client.get("odds", date=query_date, bookmaker=8, page=1)
        # Only the first page, with no exhaustive coverage claim.
        candidates = []
        for entry in data.get("response", []):
            try:
                kick = utc_stamp(entry["fixture"]["date"])
                if kick <= now:
                    continue
                fid = int(entry["fixture"]["id"])
                update, prices = extract_prices(data, fid, now)
                if update >= kick:
                    continue
                candidates.append((kick, fid, update, prices))
            except (KeyError, ValueError, AcquisitionError):
                continue
        if not candidates:
            raise AcquisitionError("No complete future Bet365 7-price snapshot in first page")
        kickoff, fixture_id, update, prices = min(candidates, key=lambda row: row[0])
        fixture_data = client.get("fixtures", id=fixture_id)
    else:
        if not isinstance(fixture_id, int) or fixture_id <= 0:
            raise AcquisitionError("Fixture ID must be positive numeric")
        fixture_data = client.get("fixtures", id=fixture_id)
        data = client.get("odds", fixture=fixture_id, bookmaker=8)
        update, prices = extract_prices(data, fixture_id, now)
    fixture = valid_fixture(fixture_data, fixture_id, now)
    if update >= utc_stamp(fixture["kickoff_utc"]):
        raise AcquisitionError("Odds updated after kickoff")
    return {
        "status": "BOOKMAKER_INPUT_READY_RESEARCH_ONLY",
        "source": "API-Football",
        "bookmaker": "Bet365",
        "bookmaker_id": 8,
        "capture_utc": now.isoformat(),
        "provider_update_utc": update.isoformat(),
        "fixture": fixture,
        "rc15_prices": {field: prices[field] for field in REQUIRED},
        "requests_used": client.count,
        "note": "Input verified. Original RC15 and MA2 engines NOT executed by this smoke test."
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-id", type=int)
    parser.add_argument("--date", help="UTC YYYY-MM-DD, default tomorrow")
    parser.add_argument("--output", default="output/bet365_rc15_input.json")
    args = parser.parse_args()
    if args.date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        parser.error("--date must be YYYY-MM-DD")
    try:
        result = run(Client(os.environ.get("APISPORTS_KEY", "")),
                     fixture_id=args.fixture_id, date=args.date)
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("SUCCESS:", result["fixture"]["home"], "vs", result["fixture"]["away"])
        print("Bet365: 7/7 quotes | provider update:", result["provider_update_utc"])
        print("Outcome: BOOKMAKER_INPUT_READY_RESEARCH_ONLY (not full native run)")
    except AcquisitionError as error:
        print("ABSTAIN:", error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
