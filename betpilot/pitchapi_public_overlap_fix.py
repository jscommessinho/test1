#!/usr/bin/env python3
"""Correct and enrich existing authenticated 77-PitchAPI snapshot WITHOUT repeat API requests.

Source JSON is archived GitHub Actions evidence from run 41198322135.
Public CSVs are pinned to exact open-matchday SHA for reproducibility.
"""
from __future__ import annotations
import argparse,csv,io,json,re,unicodedata
from pathlib import Path
from collections import Counter
from betpilot.pitchapi_full_catalogue_compare import normalize,raw_fetch

CURATED={
    ("ENG","League One"):"ENG_3_league-one",
    ("ENG","League Two"):"ENG_4_league-two",
    ("BEL","First Division A"):"BEL_1_pro-league",
    ("AUS","A-League"):"AUS_1_a-league",
    ("RUS","Premier League"):"RUS_1_premier-liga",
    ("CZE","1. Liga"):"CZE_1_chance-liga",
    ("SVK","1. liga"):"SVK_1_nike-liga",
    ("SUI","Super League"):"SUI_1_super-league",
    ("DEN","Superligaen"):"DEN_1_superliga",
    ("INT","Champions League"):"UEFA_1_champions-league",
    ("INT-2","Europa League"):"UEFA_1_europa-league",
    ("INT-2","Conference League"):"UEFA_1_conference-league",
}
def run(source,registry,sha):
    if source.get("source_catalogue_count")!=77 or len(source["pitchapi_leagues"])!=77:
        raise ValueError("77-league source snapshot identity not complete")
    index={x["folder"]:x for x in source["public_only_competitions"]}
    for row in source["pitchapi_leagues"]:
        if row.get("open_matchday"):
            x=row["open_matchday"];index[x["folder"]]={
                "folder":x["folder"],"name":x["name"],"country":x["country"],
                "providers":x["providers"],"fixture_spine":x["fixture_spine"]}
    if len(index)!=75:
        raise ValueError(f"Public repository exact 75 not recovered: {len(index)}")
    pinfo={x["id"]:Path(x["current"]["path"]).parent.name for x in registry}
    pinfo["pol-ekstraklasa"]="POL_1_ekstraklasa"
    seenfolders=set();out=[]
    public_cache={}
    for x in source["pitchapi_leagues"]:
        key=x.get("betpilot_existing_key")
        folder=pinfo.get(key)
        mode="PRIORITY_PIN_26" if folder else None
        if not folder:
            exact=[i["folder"] for i in index.values() if i["country"]==x["country_code"] and normalize(i["name"])==normalize(x["pitchapi_name"])]
            if len(exact)==1:
                folder=exact[0];mode="EXACT_NAME_COUNTRY"
            elif len(exact)>1:
                mode="AMBIGUOUS_NEEDS_REVIEW"
        if not folder:
            folder=CURATED.get((x["country_code"],x["pitchapi_name"]))
            if folder:mode="CURATED_ALIAS_REVIEW"
        pub=None
        if folder:
            if folder not in index:raise ValueError("Matched nonexistent public folder: "+folder)
            if folder in seenfolders:raise ValueError("2 source leagues map to same public folder: "+folder)
            seenfolders.add(folder)
            pub=dict(index[folder])
            if folder not in public_cache:
                try:
                    text=raw_fetch(sha,"data/"+folder+"/results_2627.csv")
                    rows=list(csv.DictReader(io.StringIO(text)))
                    count=len(rows)
                    pair=lambda a,b:sum(bool(str(r.get(a,"")).strip()) and bool(str(r.get(b,"")).strip()) for r in rows)
                    pubstats={"csv_results_rows":count,
                        "shots_pair_filled":pair("home_shots","away_shots"),
                        "on_target_pair_filled":pair("home_target","away_target"),
                        "corners_pair_filled":pair("home_corners","away_corners"),
                        "halftime_pair_filled":pair("ht_home","ht_away"),
                        "goals_full_time_pair_filled":pair("ft_home","ft_away")}
                    pubstats["complete_all_shot_rows"]=count>0 and pubstats["shots_pair_filled"]==count
                    pubstats["complete_all_target_rows"]=count>0 and pubstats["on_target_pair_filled"]==count
                    pubstats["complete_all_ft_rows"]=count>0 and pubstats["goals_full_time_pair_filled"]==count
                    public_cache[folder]=pubstats
                except Exception as e:
                    public_cache[folder]={"public_csv_status":"SOURCE_FETCH_UNVERIFIED","reason":type(e).__name__}
            pub.update(public_cache[folder])
        seasons=x["pitchapi_seasons"]
        annual=any(s=="2026" for s in seasons)
        prev=["2023","2024","2025"] if annual else ["2023/2024","2024/2025","2025/2026"]
        avail=[s for s in prev if s in seasons]
        sample=x["pitchapi_sample"]["status"]
        if sample=="XG_SHOTS_PASS":status="XG_SHOTS_TESTED_PASS"
        elif sample=="XG_SHOTS_PRESENT_WARNING":status="XG_SHOTS_TESTED_WARNING"
        elif sample=="XG_SHOTS_PRESENT_NO_INTERNAL_REFERENCE":status="XG_SHOTS_PRESENT_UNCROSSCHECKED"
        else:status="XG_SAMPLE_UNVERIFIED"
        if status=="XG_SAMPLE_UNVERIFIED":footy="PRIORITA_ALTA: verificare CSV xG/tiri"
        elif len(avail)<3:footy="PRIORITA_ALTA: stagioni storiche non elencate da PitchAPI"
        elif pub is None:footy="UTILE_PER_CROSSCHECK: non presente in open-matchday"
        else:footy="OPZIONALE: fonti complementari gia individuate"
        out.append({"pitchapi_id":x["pitchapi_id"],"pitchapi_name":x["pitchapi_name"],
                    "country_code":x["country_code"],"pitchapi_seasons":seasons,
                    "pitchapi_sample":x["pitchapi_sample"],"xg_status":status,
                    "historical_prior_three":prev,"historical_prior_three_listed":avail,
                    "historical_all_three_listed":len(avail)==3,
                    "historical_all_three_xg_coverage_proven":False,
                    "public_overlap_status":mode or "PITCHAPI_ONLY_OR_UNMATCHED",
                    "public_match":pub,"public_has_xg":False,"footystats_csv_guidance":footy,
                    "registered_priority_key":key})
    public_only=[x for k,x in index.items() if k not in seenfolders]
    return {"status":"OFFLINE_PUBLIC_CROSS_MATCH_CORRECTED",
      "source_pitchapi_run":41198322135,
      "pitchapi_catalogue_count":len(out),"pitchapi_shot_xg_one_match_tested_pass":sum(x["xg_status"]=="XG_SHOTS_TESTED_PASS" for x in out),
      "pitchapi_shot_xg_warning":sum(x["xg_status"]=="XG_SHOTS_TESTED_WARNING" for x in out),
      "pitchapi_with_shot_xg_but_no_internal_total":sum(x["xg_status"]=="XG_SHOTS_PRESENT_UNCROSSCHECKED" for x in out),
      "pitchapi_sample_unverified":sum(x["xg_status"]=="XG_SAMPLE_UNVERIFIED" for x in out),
      "open_matchday_snapshot":sha,"public_total":len(index),
      "public_and_pitchapi_mapped":len(seenfolders),"pitchapi_unmatched_or_only":len(out)-len(seenfolders),
      "public_without_pitchapi_matched":len(public_only),
      "public_match_is_competition_mapping_not_fixture_completeness_proof":True,
      "historical_three_seasons_listed_not_xg_verified":True,
      "full_coverage_100_percent_proven":False,
      "public_only":public_only,"leagues":out}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",type=Path,required=True)
    p.add_argument("--registry",type=Path,default=Path("data/coverage_25/registry.json"))
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    src=json.loads(a.input.read_text("utf8"));reg=json.loads(a.registry.read_text("utf8"))
    obj=run(src,reg,src["open_matchday_commit_sha"])
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+"\n",encoding="utf8")
    print("BETPILOT_SOURCE_CROSS_FINAL "+json.dumps({k:v for k,v in obj.items() if k in
        ("status","pitchapi_catalogue_count","pitchapi_shot_xg_one_match_tested_pass","pitchapi_shot_xg_warning",
        "pitchapi_with_shot_xg_but_no_internal_total","pitchapi_sample_unverified","public_total",
        "public_and_pitchapi_mapped","pitchapi_unmatched_or_only","public_without_pitchapi_matched")}))
if __name__=="__main__":
    main()
