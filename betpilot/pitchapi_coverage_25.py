#!/usr/bin/env python3
"""BETPILOT: free PitchAPI coverage audit for 25 league registry + optional Poland.

This is SOURCE DISCOVERY ONLY, not a model evaluation or GTS computation.
No original GA2/GTS/RC15/MA2 files are modified. No credentials in output.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import unicodedata
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from betpilot.pitchapi_gts_probe import Client, ProbeError, ident, parse_match_stats, parse_shots
from betpilot.gts_pitchapi_quality import assess_pitchapi_match, POLICY_VERSION

# Country codes are candidate catalogue filters, not claims of verified league mapping.
COUNTRIES = {
    "eng": "ENG", "ger": "GER", "esp": "ESP", "ita": "ITA",
    "fra": "FRA", "por": "POR", "ned": "NED", "tur": "TUR",
    "nor": "NOR", "swe": "SWE", "gre": "GRE", "aut": "AUT",
    "sco": "SCO", "fin": "FIN", "bra": "BRA", "arg": "ARG",
    "jpn": "JPN", "chn": "CHN", "usa": "USA", "pol": "POL",
}
# These are search candidates, NOT automatically approved name aliases.
CANDIDATES = {
    "eng-premier": ("Premier League",),
    "eng-championship": ("EFL Championship", "Championship"),
    "ger-bundesliga": ("Bundesliga",),
    "ger-bundesliga2": ("2. Bundesliga",),
    "esp-laliga": ("La Liga", "LaLiga"),
    "esp-segunda": ("Segunda División", "LaLiga 2", "Segunda Division"),
    "ita-seriea": ("Serie A",),
    "ita-serieb": ("Serie B",),
    "fra-ligue1": ("Ligue 1",),
    "fra-ligue2": ("Ligue 2",),
    "por-primeira": ("Primeira Liga",),
    "ned-eredivisie": ("Eredivisie",),
    "tur-superlig": ("Süper Lig", "Super Lig"),
    "nor-eliteserien": ("Eliteserien",),
    "swe-allsvenskan": ("Allsvenskan",),
    "gre-superleague": ("Super League Greece", "Super League 1"),
    "aut-bundesliga": ("Austrian Bundesliga", "Bundesliga"),
    "sco-premiership": ("Scottish Premiership", "Premiership"),
    "fin-veikkausliiga": ("Veikkausliiga",),
    "bra-seriea": ("Brasileirão Série A", "Serie A"),
    "bra-serieb": ("Brasileirão Série B", "Serie B"),
    "arg-primera": ("Liga Profesional Argentina", "Liga Profesional"),
    "jpn-j1": ("J1 League",),
    "chn-superleague": ("Chinese Super League", "Super League"),
    "usa-mls": ("Major League Soccer", "MLS"),
    "pol-ekstraklasa": ("Ekstraklasa",),
}

class CoverageError(ValueError):
    pass

def norm(v):
    if not isinstance(v, str):
        return ""
    t = unicodedata.normalize("NFKD", v).casefold()
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())

def roster(registry, include_poland=True):
    if not isinstance(registry, list):
        raise CoverageError("Registry must be list")
    result=[]
    seen=set()
    for x in registry:
        key=x.get("id")
        if key in seen or key not in CANDIDATES:
            raise CoverageError("Unexpected/duplicate league key in 25-league registry")
        seen.add(key)
        result.append({"id":key,"name":x["name"],"country_code":COUNTRIES[key.split("-")[0]],"target_25":True})
    if len(result)!=25:
        raise CoverageError("25-league registry count mismatch")
    if include_poland:
        result.append({"id":"pol-ekstraklasa","name":"Ekstraklasa","country_code":"POL","target_25":False})
    return result

def match_league(target, catalogue, reviewed_aliases=None):
    """Resolve by exact name or strict pinned provider-ID/name/country catalogue review.

    A pinned alias is ONLY a catalogue identity, not proof of federation xG data.
    Changes to provider ID, name or country fail closed for explicit re-review.
    """
    country=target["country_code"]
    candidates=[x for x in catalogue if x.get("country_code")==country and
                norm(x.get("name")) in {norm(n) for n in CANDIDATES[target["id"]]}]
    candidates={x.get("id"):x for x in candidates if isinstance(x,dict) and x.get("id")}
    source=next(iter(candidates.values())) if len(candidates)==1 else None
    if source is not None:
        ident(source["id"],"l")
        if norm(source["name"])==norm(target["name"]):
            return "EXACT_NAME_AND_COUNTRY_CANDIDATE",source,[]
    pinned=(reviewed_aliases or {}).get(target["id"])
    if isinstance(pinned,dict):
        matches=[x for x in catalogue if x.get("id")==pinned.get("pitchapi_league_id") and
                 x.get("name")==pinned.get("pitchapi_league_name") and
                 x.get("country_code")==pinned.get("country_code")==country]
        if len(matches)==1:
            ident(matches[0]["id"],"l")
            return "REVIEWED_CATALOGUE_ALIAS_PINNED",matches[0],[]
        # A previously reviewed ID may have changed: never guess.
        return "MAPPING_REVIEW_REQUIRED",None,[
            {"reason":"PINNED_PROVIDER_ID_NAME_OR_COUNTRY_CHANGED","id":pinned.get("pitchapi_league_id")}]
    if source is not None:
        return "ALIAS_CANDIDATE_REVIEW_REQUIRED",source,[]
    return ("MAPPING_REVIEW_REQUIRED",None,
            [{"id":x.get("id"),"name":x.get("name"),"country_code":x.get("country_code")}
             for x in catalogue if x.get("country_code")==country][:15])

def iso_seasons(value):
    if not isinstance(value,list):
        return []
    return sorted(set(s for s in value if isinstance(s,str) and re.fullmatch(r"20\d\d(?:/20\d\d)?",s)),
                  key=lambda s:(int(s[:4]),s),reverse=True)

def completed_before(matches,as_of):
    boundary=date.fromisoformat(as_of)-timedelta(days=2)
    found={}
    for m in matches:
        if not isinstance(m,dict) or m.get("status")!="finished":
            continue
        d=m.get("date")
        if not isinstance(d,str):
            continue
        try:
            if date.fromisoformat(d)>=boundary:continue
        except ValueError:
            continue
        if not isinstance(m.get("id"),str):
            continue
        ident(m["id"],"m")
        found[m["id"]]=m
    return sorted(found.values(),key=lambda z:(z["date"],z["id"]),reverse=True)

def sample_metrics(client, match):
    shots=client.get("/v1/matches/"+match["id"]+"/shots")
    metrics=parse_shots(shots,match)
    stats=client.get("/v1/matches/"+match["id"]+"/stats")
    own=parse_match_stats(stats,match)
    hid=match["home_team"]["id"];aid=match["away_team"]["id"]
    entry={"match_id":match["id"],"date":match["date"],
           "home":{"id":hid,**metrics[hid]},
           "away":{"id":aid,**metrics[aid]},
           "team_stats_xg":own}
    qa=assess_pitchapi_match(entry)
    return {"match_id":match["id"],"date":match["date"],
            "shots_home":entry["home"]["shots"],"shots_away":entry["away"]["shots"],
            "shot_xg_home":entry["home"]["xg"],"shot_xg_away":entry["away"]["xg"],
            "internal_pitchapi_status":qa["internal_pitchapi_status"],
            "internal_absolute_deltas":qa["internal_absolute_deltas"],
            "external_official_quality":"NOT_TESTED_NO_LEAGUE_REFERENCE"}

def audit(client,registered,as_of="2026-10-31",reviewed_aliases=None):
    catalogue=client.get("/v1/leagues").get("leagues")
    if not isinstance(catalogue,list):
        raise CoverageError("PitchAPI league catalogue missing")
    summary=[]
    for target in registered:
        row={**target,"mapping":"NOT_FOUND","league_id":None,"pitchapi_league_name":None,
             "seasons":[],"season_sampled":None,"played_eligible_in_season":None,
             "xg_shots_sample":None,"status":"NOT_CHECKED","error":None}
        status,league,suggestions=match_league(target,catalogue,reviewed_aliases)
        row["mapping"]=status
        if league is None:
            row["status"]="MAPPING_REVIEW_REQUIRED"
            row["country_candidates"]=suggestions
            summary.append(row)
            continue
        row["league_id"]=league["id"]
        row["pitchapi_league_name"]=league.get("name")
        row["seasons"]=iso_seasons(league.get("seasons"))
        if not row["seasons"]:
            row["status"]="NO_SEASONS_ADVERTISED"
            summary.append(row)
            continue
        if status=="ALIAS_CANDIDATE_REVIEW_REQUIRED":
            row["status"]="MAPPING_REVIEW_REQUIRED"
            summary.append(row)
            continue
        # One recent season; fallback to previous only if no eligible results.
        # No whole-league historical xG claim is made from this single-match probe.
        for season in row["seasons"][:2]:
            try:
                obj=client.get("/v1/leagues/"+league["id"]+"/matches",{"season":season,"status":"played"})
                if obj.get("league",{}).get("id")!=league["id"]:
                    raise CoverageError("League identity mismatch in match list")
                matches=obj.get("matches")
                if not isinstance(matches,list):
                    raise CoverageError("Season fixture list missing")
                eligible=completed_before(matches,as_of)
                row["season_sampled"]=season
                row["played_eligible_in_season"]=len(eligible)
                if eligible:
                    row["xg_shots_sample"]=sample_metrics(client,eligible[0])
                    row["status"]="SAMPLE_INTERNAL_PASS" if row["xg_shots_sample"]["internal_pitchapi_status"]=="PASS" else "SAMPLE_INTERNAL_REVIEW_REQUIRED"
                    break
                row["status"]="NO_ELIGIBLE_FINISHED_FIXTURES_IN_TWO_SEASONS"
            except (ProbeError,CoverageError,KeyError,TypeError,ValueError) as exc:
                row["status"]="API_OR_SCHEMA_ERROR"
                row["error"]=str(exc)[:180]
                break
        summary.append(row)
    counted=Counter(r["status"] for r in summary)
    return {"status":"SOURCE_COVERAGE_AUDIT_ONLY_NO_GTS_MODEL",
            "source":"PitchAPI authenticated read-only REST API",
            "as_of_utc_date":as_of,
            "registry_scope":"BETPILOT 25 target leagues + Poland discovery extension",
            "requested_leagues":len(registered),"target25":sum(x["target_25"] for x in registered),
            "pitchapi_catalogue_leagues":len(catalogue),"requests_used":client.used,
            "source_quality_rule":POLICY_VERSION,
            "external_official_xg_comparison_performed":False,
            "benchmark_built":False,"gts_scores_calculated":False,
            "status_counts":dict(counted),
            "league_results":summary}

def main(argv=None):
    p=argparse.ArgumentParser(description="PitchAPI coverage mapping audit for target 25 football leagues")
    p.add_argument("--registry",type=Path,default=Path("data/coverage_25/registry.json"))
    p.add_argument("--reviewed-aliases",type=Path,default=Path("data/pitchapi/league_aliases_2026-10-31.json"))
    p.add_argument("--output",type=Path,default=Path("output/pitchapi_coverage_25/report.json"))
    p.add_argument("--as-of-date",default="2026-10-31")
    p.add_argument("--max-requests",type=int,default=100)
    p.add_argument("--include-polish-ekstraklasa",action="store_true")
    a=p.parse_args(argv)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    if not os.environ.get("PITCHAPI_API_KEY"):
        a.output.write_text(json.dumps({"status":"PITCHAPI_KEY_REQUIRED","requests_performed":0},indent=2)+"\n")
        print("PITCHAPI_KEY_REQUIRED: no authenticated coverage check")
        return 2
    try:
        reg=json.loads(a.registry.read_text("utf-8"))
        rows=roster(reg,include_poland=a.include_polish_ekstraklasa)
        reviewed=json.loads(a.reviewed_aliases.read_text("utf-8"))
        if reviewed.get("schema")!="BETPILOT_PITCHAPI_PROVIDER_CATALOGUE_ALIASES_V1" or not isinstance(reviewed.get("aliases"),dict):
            raise CoverageError("Invalid reviewed provider catalogue alias file")
        cl=Client(os.environ["PITCHAPI_API_KEY"],budget=a.max_requests,pause=0.16)
        report=audit(cl,rows,as_of=a.as_of_date,reviewed_aliases=reviewed["aliases"])
        report["catalogue_alias_review"]={"source_run_id":reviewed.get("source_run_id"),
                                         "external_federation_attested":False,
                                         "verified_as_provider_identity_only":True}
        a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
        print("PITCHAPI_COVERAGE_25_AUDIT "+json.dumps({
            "status_counts":report["status_counts"],"requests_used":report["requests_used"],
            "league_results":[{"id":r["id"],"status":r["status"],"mapping":r["mapping"],
                "season":r["season_sampled"],"matches":r["played_eligible_in_season"],
                "sample_shots":(r["xg_shots_sample"]["shots_home"]+r["xg_shots_sample"]["shots_away"]) if r["xg_shots_sample"] else None,
                "error":r["error"]} for r in report["league_results"]]
        },ensure_ascii=False))
        return 0
    except (CoverageError,ProbeError,ValueError,OSError) as e:
        a.output.write_text(json.dumps({"status":"SOURCE_COVERAGE_AUDIT_FAILED","reason":str(e)[:200]},indent=2)+"\n")
        print("PITCHAPI_COVERAGE_25_FAILED "+str(e)[:180])
        return 2

if __name__=="__main__":
    raise SystemExit(main())
