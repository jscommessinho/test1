#!/usr/bin/env python3
"""BETPILOT: authenticated PitchAPI catalogue + shot xG smoke tests against fixed open-matchday snapshot.

Research coverage, not proof of all historical xG coverage or GTS accuracy.
Never expose API keys or identify public-repo match rows as xG.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import unicodedata
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from betpilot.pitchapi_gts_probe import Client, ProbeError, parse_shots, parse_match_stats
from betpilot.gts_pitchapi_quality import assess_pitchapi_match

OPEN_REPO = "oladeeayo/open-matchday"
SEASON_SUFFIX = "2627"
MAX_PUBLIC_BODY = 3_000_000
COUNTRY_MAP = {"NED":"NED","SCO":"SCO","WAL":"WAL","GER":"GER","ENG":"ENG",
               "ESP":"ESP","ITA":"ITA","FRA":"FRA","POR":"POR","BRA":"BRA",
               "ARG":"ARG","USA":"USA","POL":"POL","JPN":"JPN","CHN":"CHN",
               "AUT":"AUT","SUI":"SUI","GRE":"GRE","FIN":"FIN","TUR":"TUR",
               "NOR":"NOR","SWE":"SWE","BEL":"BEL","CRO":"CRO","CZE":"CZE",
               "IRE":"IRE","RUS":"RUS","ROU":"ROU","UKR":"UKR","CYP":"CYP",
               "SVK":"SVK","SVN":"SVN","KAZ":"KAZ","DEN":"DEN","MEX":"MEX",
               "AUS":"AUS","UEFA":"UEFA"}
def normalize(s):
    n=unicodedata.normalize("NFKD",str(s or "")).casefold()
    n="".join(c for c in n if not unicodedata.combining(c))
    return " ".join(re.sub("[^a-z0-9]+"," ",n).split())

def raw_fetch(sha,path):
    if not re.fullmatch("[a-f0-9]{40}",sha):raise ValueError("Open-matchday commit must be SHA40")
    url=f"https://raw.githubusercontent.com/{OPEN_REPO}/{sha}/{path}"
    req=Request(url,headers={"User-Agent":"BETPILOT-Source-Catalogue-Audit/1.0"})
    with urlopen(req,timeout=25) as res:
        body=res.read(MAX_PUBLIC_BODY+1)
    if len(body)>MAX_PUBLIC_BODY or not body:
        raise ValueError("Public repository response size invalid")
    return body.decode("utf-8-sig")

def parse_index(text):
    rows=[]
    for line in text.splitlines():
        cells=[c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells)<6 or not cells[0].startswith("`") or not cells[0].endswith("`"):
            continue
        folder=cells[0].strip("`")
        if not re.fullmatch(r"[A-Z0-9]+_[A-Za-z0-9-]+(?:_[A-Za-z0-9-]+)*",folder):
            continue
        rows.append({"folder":folder,"country":folder.split("_")[0],
                     "name":cells[1],"providers":cells[3],
                     "fixture_spine":cells[5]})
    if len(rows)<65:
        raise ValueError(f"Unexpected public repository index coverage ({len(rows)})")
    return rows

def pin_public_folder(league_id, pins, registry):
    found=[k for k,v in pins.items() if v.get("league_id")==league_id]
    if len(found)>1:raise ValueError("Ambiguous pinned PitchAPI source ID")
    if not found:return None,None
    key=found[0]
    lookup={x["id"]:str(Path(x["current"]["path"]).parent) for x in registry}
    lookup["pol-ekstraklasa"]="POL_1_ekstraklasa"
    return lookup.get(key),key

def link_public(league,index,pins,registry):
    p,key=pin_public_folder(league.get("id"),pins,registry)
    if p is not None:
        selected=[x for x in index if x["folder"]==p]
        if len(selected)!=1:return "PINNED_PUBLIC_FOLDER_NOT_FOUND",None,key
        return "EXACT_PINNED_MAPPING",selected[0],key
    # Other leagues: strictly unique SAME country and normalized name; no fuzzy-tier guesses.
    possible=[r for r in index if r["country"]==league.get("country_code")
              and normalize(r["name"])==normalize(league.get("name"))]
    if len(possible)==1:return "EXACT_COUNTRY_AND_NAME",possible[0],None
    if len(possible)>1:return "AMBIGUOUS_REVIEW_REQUIRED",None,None
    return "NO_EXACT_PUBLIC_MATCH_REVIEW_REQUIRED",None,None

def season_order(s):
    if not isinstance(s,list):return []
    return sorted(set(str(x) for x in s if re.fullmatch(r"20\d{2}(?:/20\d{2})?",str(x))),reverse=True)

def test_one_match(client,league,as_of):
    lid=league["id"]
    seasons=season_order(league.get("seasons"))
    cutoff=date.fromisoformat(as_of)-timedelta(days=2)
    selected=None; chosen_season=None; latest_count=0; errs=[]
    for season in seasons[:3]:
        try:
            data=client.get(f"/v1/leagues/{lid}/matches",{"season":season,"status":"played"})
            if (data.get("league") or {}).get("id")!=lid:raise ValueError("League-ID mismatch")
            rows=data.get("matches")
            if not isinstance(rows,list):raise ValueError("Match-list missing")
            eligible=[]
            for m in rows:
                try:
                    if m.get("status")=="finished" and date.fromisoformat(m.get("date",""))<cutoff:
                        eligible.append(m)
                except (TypeError,ValueError):
                    continue
            if not latest_count:latest_count=len(eligible)
            if eligible:
                selected=max(eligible,key=lambda x:(x["date"],x["id"]))
                chosen_season=season
                break
        except (ProbeError,ValueError,KeyError,TypeError) as exc:
            errs.append(str(exc)[:100])
            if "HTTP status 429" in str(exc) or "budget exhausted" in str(exc):
                return {"status":"RATE_LIMIT_OR_BUDGET_STOP","error":str(exc)[:150]}
            continue
    if selected is None:
        return {"status":"NO_ELIGIBLE_FINISHED_SAMPLE","season_candidates":seasons[:3],
                "last_season_eligible_count":latest_count,"errors":errs}
    try:
        shots=parse_shots(client.get(f"/v1/matches/{selected['id']}/shots"),selected)
        official=parse_match_stats(client.get(f"/v1/matches/{selected['id']}/stats"),selected)
        h=selected["home_team"]["id"];a=selected["away_team"]["id"]
        entry={"match_id":selected["id"],"date":selected["date"],
               "home":{"id":h,**shots[h]},"away":{"id":a,**shots[a]},
               "team_stats_xg":official}
        quality=assess_pitchapi_match(entry)
        state=quality["internal_pitchapi_status"]
        return {"status":"XG_SHOTS_PASS" if state=="PASS" else
                         "XG_SHOTS_PRESENT_WARNING" if state=="REJECTED_INTERNAL_ABOVE_0_50" else
                         "XG_SHOTS_PRESENT_NO_INTERNAL_REFERENCE",
                "season":chosen_season,"last_season_eligible_count":latest_count,
                "sample_match_id":selected["id"],"date":selected["date"],
                "sample_shots":shots[h]["shots"]+shots[a]["shots"],
                "team_shot_xg":[round(shots[h]["xg"],4),round(shots[a]["xg"],4)],
                "internal_xg_absolute_deltas":quality["internal_absolute_deltas"]}
    except (ProbeError,ValueError,KeyError,TypeError) as exc:
        return {"status":"XG_SAMPLE_NOT_VERIFIED","season":chosen_season,
                "sample_match_id":selected["id"],"error":str(exc)[:160],
                "last_season_eligible_count":latest_count}

def public_csv_metrics(sha,folder):
    try:
        csvtext=raw_fetch(sha,f"data/{folder}/results_{SEASON_SUFFIX}.csv")
        rows=list(csv.DictReader(io.StringIO(csvtext)))
        stats={}
        for key in ["home_shots","away_shots","home_target","away_target",
                    "home_corners","away_corners","home_corners_1h","away_corners_1h",
                    "ht_home","ht_away","home_yellow","away_yellow"]:
            stats[key]=sum(bool(str(r.get(key,"")).strip()) for r in rows)
        return {"csv_results_rows":len(rows),"nonnull_columns_counts":stats,
                "all_result_rows_have_shots":len(rows)>0 and stats["home_shots"]==len(rows) and stats["away_shots"]==len(rows)}
    except (HTTPError,URLError,TimeoutError,UnicodeDecodeError,ValueError,csv.Error) as exc:
        return {"public_csv_status":"UNVERIFIED","public_csv_error":type(exc).__name__}

def run(client,*,sha,as_of,registry,pins):
    index=parse_index(raw_fetch(sha,"LEAGUE_INDEX.md"))
    catalogue=client.get("/v1/leagues").get("leagues")
    if not isinstance(catalogue,list) or len(catalogue)<25:raise ValueError("PitchAPI leagues list missing or implausibly small")
    output=[]
    cache={}
    for j,league in enumerate(catalogue,1):
        status,public,key=link_public(league,index,pins,registry)
        sample=test_one_match(client,league,as_of)
        if public is not None and public["folder"] not in cache:
            cache[public["folder"]]=public_csv_metrics(sha,public["folder"])
        r={"pitchapi_id":league.get("id"),"pitchapi_name":league.get("name"),
           "country_code":league.get("country_code"),"pitchapi_seasons":season_order(league.get("seasons")),
           "pitchapi_season_count":len(season_order(league.get("seasons"))),
           "pitchapi_sample":sample,"public_mapping_status":status,"betpilot_existing_key":key,
           "open_matchday":({**public,**cache.get(public["folder"],{})} if public else None)}
        output.append(r)
        print(f"SOURCE_CATALOGUE_PROGRESS {j}/{len(catalogue)} {league.get('country_code')} {league.get('name')}: {sample.get('status')} | {status}",flush=True)
        if sample.get("status")=="RATE_LIMIT_OR_BUDGET_STOP":break
    matched_folders={r["open_matchday"]["folder"] for r in output if r["open_matchday"]}
    public_only=[x for x in index if x["folder"] not in matched_folders]
    return {"report_version":"BETPILOT_PITCHAPI_XG_OPEN_MATCHDAY_CROSS_COVERAGE_V1",
            "retrieved_as_of_date":as_of,"provider":"PitchAPI free API",
            "public_provider":"oladeeayo/open-matchday","open_matchday_commit_sha":sha,
            "source_catalogue_count":len(catalogue),"provider_leagues_audited":len(output),
            "public_index_competitions":len(index),"public_name_mapped":len(matched_folders),
            "pitchapi_xg_shot_pass_samples":sum(x["pitchapi_sample"]["status"]=="XG_SHOTS_PASS" for x in output),
            "pitchapi_xg_shot_warning_samples":sum(x["pitchapi_sample"]["status"]=="XG_SHOTS_PRESENT_WARNING" for x in output),
            "pitchapi_xg_sample_unverified":sum(x["pitchapi_sample"]["status"] not in
                 ("XG_SHOTS_PASS","XG_SHOTS_PRESENT_WARNING") for x in output),
            "pitchapi_only_pending_match_verification":sum(x["open_matchday"] is None for x in output),
            "public_only_competitions":public_only,"pitchapi_leagues":output,
            "xg_is_not_in_open_matchday":True,
            "public_historic_three_full_seasons_proven":False,
            "all_matches_xg_coverage_proven":False,
            "all_matches_public_stats_completeness_proven":False,
            "gts_prediction_validated":False,
            "api_requests_used":client.used}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--open-commit",required=True)
    p.add_argument("--as-of",default="2026-10-31")
    p.add_argument("--out",type=Path,default=Path("output/pitchapi_77_cross_coverage/report.json"))
    p.add_argument("--registry",type=Path,default=Path("data/coverage_25/registry.json"))
    p.add_argument("--pins",type=Path,default=Path("data/pitchapi/league_pins_26.json"))
    a=p.parse_args()
    a.out.parent.mkdir(parents=True,exist_ok=True)
    if not os.environ.get("PITCHAPI_API_KEY"):
        raise SystemExit("PITCHAPI_KEY_REQUIRED")
    try:
        registry=json.loads(a.registry.read_text("utf-8"))
        pins=json.loads(a.pins.read_text("utf-8"))["leagues"]
        client=Client(os.environ["PITCHAPI_API_KEY"],budget=330,pause=.24)
        result=run(client,sha=a.open_commit,as_of=a.as_of,registry=registry,pins=pins)
        a.out.write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+"\n")
        print("BETPILOT_FULL_SOURCE_COVERAGE "+json.dumps({
              "catalogue":result["source_catalogue_count"],
              "audited":result["provider_leagues_audited"],
              "public_index":result["public_index_competitions"],
              "mapped":result["public_name_mapped"],
              "samples_xg_pass":result["pitchapi_xg_shot_pass_samples"],
              "samples_xg_warning":result["pitchapi_xg_shot_warning_samples"],
              "samples_unverified":result["pitchapi_xg_sample_unverified"],
              "api_requests":result["api_requests_used"]},sort_keys=True))
    except Exception as exc:
        a.out.write_text(json.dumps({"status":"AUDIT_FAILED","reason":str(exc)[:180]})+"\n")
        raise

if __name__=="__main__":
    main()
