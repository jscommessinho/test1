#!/usr/bin/env python3
"""Frozen GA2 on Chelsea-Manchester United 2026-10-31; research only.

Never modifies model, no odds, no lineups, no GTS, no live scores.
Uses 3 historic complete EPL seasons plus the current open-matchday snapshot.
"""
import csv
import hashlib
import io
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"vendor"/"BETPILOT_STEP5"))
from footystats_ga2_adapter import load_frozen_model

CUTOFF="2026-10-01"
KICKOFF="2026-10-31T12:30:00Z"
FIXTURE={"home":"Chelsea FC","away":"Manchester United FC"}
CURRENT_MAP={
 "Arsenal":"Arsenal FC","Aston Villa":"Aston Villa FC",
 "Bournemouth":"AFC Bournemouth","Brentford":"Brentford FC",
 "Brighton":"Brighton & Hove Albion FC","Chelsea":"Chelsea FC",
 "Coventry":"Coventry City FC","Crystal Palace":"Crystal Palace FC",
 "Everton":"Everton FC","Fulham":"Fulham FC","Hull":"Hull City FC",
 "Ipswich":"Ipswich Town FC","Leeds":"Leeds United FC",
 "Liverpool":"Liverpool FC","Man City":"Manchester City FC",
 "Man Utd":"Manchester United FC","Newcastle":"Newcastle United FC",
 "Nott'm Forest":"Nottingham Forest FC","Spurs":"Tottenham Hotspur FC",
 "Sunderland":"Sunderland AFC",
}

def get_source(url):
    req=Request(url,headers={"User-Agent":"BETPILOT-Research-Audit/1.0","Accept":"application/json,text/csv"})
    with urlopen(req,timeout=25) as r:
        data=r.read(1_500_000)
    if len(data)>=1_500_000:
        raise RuntimeError("source exceeds download size limit")
    return data,hashlib.sha256(data).hexdigest()

def scorepair(score):
    if isinstance(score,dict):
        score=score.get("ft")
    if not isinstance(score,list) or len(score)!=2 or any(type(x)!=int or x<0 or x>30 for x in score):
        raise ValueError("Missing or invalid FT score")
    return score

def record(season,day,home,away,goals,identity):
    d=date.fromisoformat(day)
    hg,ag=scorepair(goals)
    return {"id":identity,"season":season,"date":day,"forecast_date":day,
            "completion_date":day,"available_date":(d+timedelta(days=2)).isoformat(),
            "home":home,"away":away,"hg":hg,"ag":ag,"hh":None,"ha":None}

def main():
    out=ROOT/"output"/"ga2_chelsea_manutd"
    out.mkdir(parents=True,exist_ok=True)
    rows=[]
    sources=[]
    counts={}
    known=set()
    for season in ("2023-24","2024-25","2025-26"):
        url=f"https://raw.githubusercontent.com/openfootball/football.json/master/{season}/en.1.json"
        data,sha=get_source(url)
        obj=json.loads(data)
        matches=obj["matches"]
        if len(matches)!=380:
            raise ValueError("not 380 fixtures in "+season)
        found=set()
        for i,m in enumerate(matches):
            home,away=m["team1"],m["team2"]
            day=m["date"]
            goals=scorepair(m["score"])
            identity=(season,day,home,away)
            if identity in known: raise ValueError("duplicate historic fixture")
            known.add(identity)
            found.update((home,away))
            rows.append(record(season,day,home,away,goals,"of:"+season+":"+str(i)))
        if len(found)!=20:raise ValueError("historical club count !=20")
        counts[season]=len(matches)
        sources.append({"kind":"historic_season","season":season,"source_url":url,"sha256":sha,
                        "completed_matches":len(matches),"clubs":len(found)})
    url="https://raw.githubusercontent.com/oladeeayo/open-matchday/master/data/ENG_1_premier-league/results_2627.csv"
    data,sha=get_source(url)
    matches=list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    seen=set()
    current_clubs=set()
    if len(matches)!=80:raise ValueError("Current-season snapshot no longer matches validated 80 result audit")
    for i,m in enumerate(matches):
        home,away=CURRENT_MAP[m["home"]],CURRENT_MAP[m["away"]]
        current_clubs.update((m["home"],m["away"]))
        day=m["date"]
        if not ("2026-08-01"<=day<"2026-10-31"):
            raise ValueError("Future/unexpected record in live source")
        identity=("2026-27",day,home,away)
        if identity in known or identity in seen:
            raise ValueError("duplicate current fixture")
        seen.add(identity)
        goals=[int(m["ft_home"]),int(m["ft_away"])]
        rows.append(record("2026-27",day,home,away,goals,"om:"+str(i)))
    if current_clubs!=set(CURRENT_MAP):
        raise ValueError("Current club name mapping was not exhaustively verified")
    sources.append({"kind":"current_completed","season":"2026-27","source_url":url,"sha256":sha,
                    "completed_matches":len(matches),"clubs":len(current_clubs),
                    "last_match_date":max(m["date"] for m in matches),
                    "aliases":CURRENT_MAP})
    counts["2026-27"]=len(matches)
    # Point-in-time proxy: a finished match becomes eligible two calendar days later.
    eligible=[r for r in rows if r["available_date"] < CUTOFF]
    if len(eligible)<500:raise ValueError("not enough pre-cutoff history")
    if any(r["date"]>=CUTOFF for r in eligible):
        raise ValueError("temporal leakage")
    by_season=Counter(r["season"] for r in eligible)
    lock,ga2=load_frozen_model()
    assert lock["distribution"]=="poisson" and lock["scale"]==1
    cfg=ga2.Config(**lock["config"])
    model=ga2.Model(cfg).fit(eligible,CUTOFF)
    if set(FIXTURE.values()) - set(model.teams):
        raise ValueError("target team absent in training")
    lh,la=model.predict(FIXTURE["home"],FIXTURE["away"])
    ps,g,fb=ga2.probs(lh,la,lock["distribution"],lock["parameter"])
    if fb or abs(ps["1"]+ps["X"]+ps["2"]-1)>1e-8 or abs(ps["GG"]+ps["NG"]-1)>1e-8:
        raise ValueError("probability grid invalid")
    baseline=ga2.league_means(eligible,CUTOFF)
    p0,_,_=ga2.probs(*baseline)
    top=[{"result":f"{i}-{j}","probability":float(g[i,j])}
         for i,j in sorted(__import__("numpy").ndindex(g.shape),
                           key=lambda pair:g[pair],reverse=True)[:8]]
    target_team_stats={}
    for team in FIXTURE.values():
        all_matches=[r for r in eligible if team in (r["home"],r["away"])]
        if not all_matches:raise ValueError("team missing")
        target_team_stats[team]={"historical_games_at_cutoff":len(all_matches),
               "most_recent_eligible":max(r["date"] for r in all_matches),
               "current_season_games_at_cutoff":sum(r["season"]=="2026-27" for r in all_matches)}
    report={
        "status":"RESEARCH_ONLY_NOT_VALIDATED_FOR_PREMIER_LEAGUE",
        "fixture":"Chelsea vs Manchester United","competition":"English Premier League",
        "kickoff_utc":KICKOFF,
        "frozen_engine":"GA2-RESEARCH-20261029-v1",
        "frozen_config":lock["config"],"frozen_distribution":lock["distribution"],
        "frozen_scale":lock["scale"],
        "source_manifest":sources,"total_completed_source_matches":len(rows),
        "available_at_monthly_cutoff":len(eligible),
        "train_n":len(model.rows),"train_ess":model.ess,
        "training_seasons":dict(by_season),"monthly_refit":CUTOFF,
        "historical_availability":"two calendar day proxy, not certified as-known-at-time",
        "team_stats":target_team_stats,
        "lambda_home":lh,"lambda_away":la,"lambda_total":lh+la,
        "p_model":{k:ps[k] for k in ("1","X","2","GG","NG","O0.5","U0.5","O1.5","U1.5","O2.5","U2.5","O3.5","U3.5","O4.5","U4.5","H1+","A1+","H2+","A2+")},
        "top8_exact_scores":top,
        "baseline_lambda":baseline,
        "baseline_probabilities":{k:p0[k] for k in ("1","X","2","GG","O2.5")},
        "GTS":"NOT_CALCULABLE_FROM_GOAL_ONLY_DATA",
        "confidence":"NO_PREMIER_LEAGUE_PREDICTIVE_CALIBRATION_VERIFIED",
        "notes":["No bookmaker odds or lineups are inputs",
                 "Live scores and matches after monthly refit never enter training",
                 "Public current result snapshot is not independent point-in-time proof",
                 "Other leagues including Italian Serie A never enter this fit",
                 "Do not convert these probabilities into a certified bet recommendation"]
    }
    (out/"GA2_CHELSEA_MANCHESTER_UNITED_RESEARCH.json").write_text(
        json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    print("GA2_CHELSEA_MANUTD_REPORT_BEGIN")
    print(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False))
    print("GA2_CHELSEA_MANUTD_REPORT_END")
    print("GA2_CHELSEA_MANUTD_SUCCESS")
if __name__=="__main__":
    main()
