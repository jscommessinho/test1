"""General, bounded fixture reconciliation for PitchAPI's pinned 77 leagues.

A fixture is recognized by independent exact team, date, kickoff and country
checks; never make up a provider league ID from another provider's catalogue.
No frozen analyzer or collection workflow is changed.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

from betpilot_v3.pitchapi_preflight import PreflightBlocked, instant, require

COUNTRIES = {
    "ARG":"Argentina","AUS":"Australia","AUT":"Austria","BEL":"Belgium",
    "BOL":"Bolivia","BRA":"Brazil","CAN":"Canada","CHI":"Chile",
    "CHN":"China","COL":"Colombia","CRO":"Croatia","CZE":"Czech Republic",
    "DEN":"Denmark","ECU":"Ecuador","EGY":"Egypt","ENG":"England",
    "ESP":"Spain","FIN":"Finland","FRA":"France","GER":"Germany",
    "GRE":"Greece","IND":"India","INT":"World","INT-2":"World",
    "IRL":"Ireland","ISL":"Iceland","ISR":"Israel","ITA":"Italy",
    "JPN":"Japan","KOR":"South Korea","KSA":"Saudi Arabia","LVA":"Latvia",
    "MEX":"Mexico","NED":"Netherlands","NOR":"Norway","PAR":"Paraguay",
    "PER":"Peru","POL":"Poland","POR":"Portugal","QAT":"Qatar",
    "ROU":"Romania","RSA":"South Africa","RUS":"Russia","SCO":"Scotland",
    "SRB":"Serbia","SUI":"Switzerland","SVK":"Slovakia","SWE":"Sweden",
    "THA":"Thailand","TUR":"Turkey","UAE":"United Arab Emirates",
    "USA":"USA","VEN":"Venezuela",
}
# Only presentation-language equivalents, no automatic fuzzy team substitution.
TEAM_ALIASES = {
    "siviglia":"sevilla", "bayern monaco":"bayern munich",
    "inter milan":"inter", "internazionale":"inter",
    "internazionale milano":"inter", "milan":"ac milan",
    "manchester united":"manchester united",
}
COUNTRY_ALIASES = {
    "korea republic":"south korea", "korea south":"south korea",
    "united states":"usa","united states of america":"usa",
    "czechia":"czech republic", "saudi arabia":"saudi arabia",
    "world":"world", "europe":"world",
}


def normalized(value):
    if not isinstance(value, str):
        return ""
    s = unicodedata.normalize("NFKD", value.casefold())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", s))


def team(value):
    label = normalized(value)
    label = TEAM_ALIASES.get(label, label)
    # Remove only clearly generic *edge* club designators. Keep meaningful
    # inner tokens and reserve explicit aliases for translated team names.
    tokens = label.split()
    generic = {"fc", "cf", "afc", "sc", "fk"}
    while len(tokens) > 1 and tokens[0] in generic:
        tokens.pop(0)
    while len(tokens) > 1 and tokens[-1] in generic:
        tokens.pop()
    return " ".join(tokens)


def countries_equal(code, api_name):
    require(code in COUNTRIES, "COUNTRY_NOT_PINNED", str(code))
    actual = normalized(api_name)
    actual = COUNTRY_ALIASES.get(actual, actual)
    return actual == normalized(COUNTRIES[code])


def load_registry(path):
    p = Path(path)
    require(p.is_file() and not p.is_symlink(),
            "PINNED_CATALOGUE_MISSING", "77-league registry unavailable")
    doc = json.loads(p.read_text("utf8"))
    require(doc.get("schema") == "BETPILOT_PITCHAPI_LEAGUE_PINNED_77_v1"
            and isinstance(doc.get("leagues"), dict)
            and len(doc["leagues"]) == 77,
            "PINNED_CATALOGUE_INVALID", "Expected original exact 77-league registry")
    registered = {}
    for key, item in doc["leagues"].items():
        signature = (item["league_id"], item["league_name"], item["country_code"])
        require(signature not in registered, "PINNED_CATALOGUE_AMBIGUOUS",
                "Duplicate original provider league")
        registered[signature] = dict(item, registry_key=key)
    return registered


def pinned(source_id, competition, country, registry):
    match = registry.get((source_id, competition, country))
    require(match is not None, "PITCHAPI_LEAGUE_NOT_SUPPORTED",
            "Target league is not one of the 77 original provider pins")
    return match


def fixture_from_api(client, request, day, *, league_id=None, season=None,
                     fixture_id=None, now=None):
    """Discover a *single* future fixture: one budgeted /fixtures?date call.

    API-Football fixture discovery is independent of PitchAPI's three-call
    detail/catalogue lookup. Never guess an API fixture or season ID.
    """
    from betpilot_v3.full_analysis import parse_request
    from betpilot import quota_smart  # existing safe vendor-path registration
    from api_football_adapter import identify_fixture
    home, away = parse_request(request)
    date.fromisoformat(day)
    now = now or datetime.now(timezone.utc)
    data = client.get("/fixtures", date=day, timezone="UTC")
    require(data.get("paging", {}).get("total", 1) == 1,
            "API_FIXTURES_PAGINATED", "Incomplete date fixture list")
    choices = []
    for row in data.get("response", []):
        try:
            f, clubs, league = row["fixture"], row["teams"], row["league"]
            if team(clubs["home"]["name"]) != team(home) or team(clubs["away"]["name"]) != team(away):
                continue
            if instant(f["date"]).date().isoformat() != day:
                continue
            if league_id is not None and int(league["id"]) != int(league_id):
                continue
            if season is not None and int(league["season"]) != int(season):
                continue
            if fixture_id is not None and int(f["id"]) != int(fixture_id):
                continue
            identified = identify_fixture({"response":[row]}, int(f["id"]), now)
            choices.append((identified, row))
        except (KeyError, ValueError, TypeError, PreflightBlocked):
            continue
    require(len(choices) == 1, "API_FIXTURE_MISSING_OR_AMBIGUOUS",
            "No single exact, future, authorized API-Football fixture for requested teams/day")
    return choices[0]


def acquire_original_quote(client, fixture, row, *, now=None):
    """Exactly one capped authenticated /odds call; retain source identity."""
    from betpilot import quota_smart  # existing safe vendor-path registration
    from api_football_adapter import extract_odds, build_input
    from integrate_native import check_payload
    now = now or datetime.now(timezone.utc)
    raw = client.get("/odds", fixture=fixture["id"], bookmaker=8)
    updated, quotes = extract_odds(raw, fixture["id"], now)
    input_ = build_input(fixture, quotes, updated, now,
                         {"response":[row]}, raw, live=True)
    check_payload(input_)
    league = row["league"]
    evidence = {
        "api_fixture_id": fixture["id"],
        "api_league_id": int(league["id"]),
        "api_season": int(league["season"]),
        "api_country": league["country"],
        "api_league_name": league["name"],
        "home": row["teams"]["home"]["name"],
        "away": row["teams"]["away"]["name"],
        "kickoff": fixture["kickoff"],
        "provider": "API-Football",
    }
    return input_, evidence


def reconcile(match, ids, pitch_target, quote, api_evidence, registry):
    """Compare authenticated independent provider observations before rebind."""
    require(pitch_target["pitchapi_event_id"] == match["event_id"]
            and pitch_target["pitchapi_league_id"] == ids["league_id"],
            "PITCHAPI_EVENT_CONFLICT", "PitchAPI event/league changed")
    key = pinned(ids["league_id"], match["competition"],
                 pitch_target["country"], registry)
    require(countries_equal(pitch_target["country"], api_evidence["api_country"]),
            "PROVIDER_COUNTRY_CONFLICT", "Country differs between providers")
    season = ids["season"]
    year = int(api_evidence["api_season"])
    require(season in (str(year), f"{year}/{year+1}"),
            "PROVIDER_SEASON_CONFLICT", "Provider seasons differ")
    qmatch = quote["match"]
    require(qmatch["event_id"] == f"API-FOOTBALL-fixture-{api_evidence['api_fixture_id']}",
            "PROVIDER_FIXTURE_ID_CONFLICT", "Bookmaker fixture ID changed")
    require(qmatch["competition"] ==
            f"{api_evidence['api_country']} {api_evidence['api_league_name']} {year}",
            "PROVIDER_LEAGUE_CONFLICT", "Quote competition differs from authenticated fixture")
    for side in ("home","away"):
        require(team(match[side]) == team(api_evidence[side])
                and team(qmatch[side]) == team(api_evidence[side]),
                "PROVIDER_TEAM_CONFLICT", side + " name changed across source reports")
    kickoff = instant(match["kickoff"])
    require(instant(api_evidence["kickoff"]) == kickoff
            and instant(qmatch["kickoff"]) == kickoff,
            "PROVIDER_KICKOFF_CONFLICT", "Fixture kickoff differs across providers")
    quote_cut = instant(qmatch["cutoff"])
    cutoff = instant(match["cutoff"])
    snap = quote["odds_snapshot"]
    require(snap.get("bookmaker") == "Bet365"
            and snap.get("provenance") == "VERIFIED_EXTERNAL_SNAPSHOT"
            and isinstance(snap.get("external_capture_reference"),str)
            and snap.get("external_capture_reference"),
            "PROVIDER_QUOTE_UNVERIFIED", "Authenticated quote snapshot is required")
    require(instant(snap["timestamp"]) <= quote_cut <= cutoff < kickoff,
            "PROVIDER_ASOF_CONFLICT", "Quote not obtained before original PitchAPI cutoff")
    # Name equivalence is advisory for cross-provider labelling; independent
    # match identity is grounded in teams, exact kickoff, season and country.
    league_labels_same = normalized(match["competition"]) == normalized(
        api_evidence["api_league_name"])
    derived = json.loads(json.dumps(quote))
    derived["match"] = dict(match)
    evidence = {
        "schema": "BETPILOT_77_PINNED_CROSS_PROVIDER_EVENT_V1",
        "mode": "RESEARCH_ONLY", "betting_enabled": False,
        "pitchapi_event_id": match["event_id"],
        "api_fixture_id": api_evidence["api_fixture_id"],
        "pitchapi_league_id": ids["league_id"],
        "api_league_id": api_evidence["api_league_id"],
        "registry_key": key["registry_key"],
        "season": season, "kickoff": match["kickoff"],
        "cross_provider_label_same": league_labels_same,
        "source_match_proof": "exact venue/team pair, UTC kickoff, pinned country, season and API fixture identity",
        "direct_bet365_capture_independently_verified": False,
        "original_quote_cutoff": qmatch["cutoff"],
        "original_quote_snapshot_id": snap["snapshot_id"],
    }
    return derived, evidence
