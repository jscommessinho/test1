"""One-off authentic GA3 RC2.post2 evaluation on verified historical PitchAPI goals.

Used with the original user-supplied, byte-verified goal_analyzer3 source
embedded as a ZIP; no reimplementation or recalibration. No DB writes.
"""
from __future__ import annotations
import base64, gzip, hashlib, io, json, subprocess, sys, zipfile
from datetime import datetime, timezone
from pathlib import Path

SOURCES={
    "goal_analyzer3/__init__.py":"f4ff752422aa8d0bce31d0dc432c13183d4b393b702a1310c6feaada28559f80",
    "goal_analyzer3/data.py":"69cccff5838179a1d10300d268ee2efaba9bf399d53b4a0f2751ae50982d217a",
    "goal_analyzer3/model.py":"aec65a210bb3ef91e8297db90b1794d47231747a88ddf71d96a1e9c93f12614f",
    "goal_analyzer3/probability.py":"dee2455e25782076c773d95896541665714230210e8cb4d3dfeb4f16ae937f20",
}
archive=base64.b64decode((Path(__file__).parent/"authentic_ga3_core.b64").read_text().strip(),validate=True)
expected_zip="ec449d8305a341f36c29cc1710f3285dcddb94522718de99d50e2fd360281382"
assert hashlib.sha256(archive).hexdigest()==expected_zip, "Original GA3 code archive changed!"
with zipfile.ZipFile(io.BytesIO(archive)) as z:
    assert set(z.namelist())==set(SOURCES),"Unexpected files in engine bundle"
    for path, sha in SOURCES.items():
        content=z.read(path)
        assert hashlib.sha256(content).hexdigest()==sha, "Engine source hash mismatch: "+path
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp:
        z.extractall(tmp)
        sys.path.insert(0,tmp)
        from goal_analyzer3.data import Match,require_pitchapi
        from goal_analyzer3.model import ModelConfig,RidgePoisson
        from goal_analyzer3.probability import predict_markets
        generated=subprocess.run(
            [sys.executable,"integration/ga3_export_prematch_juventus_napoli.py"],
            check=True,capture_output=True,text=True).stdout
        compact_b64=generated.split("GZ_BASE64_EXPORT_START\n")[1].split("\nGZ_BASE64_EXPORT_END")[0]
        data=json.loads(gzip.decompress(base64.b64decode("".join(compact_b64.splitlines()))))
        cutoff=datetime.fromisoformat(data["prematch_cutoff"]).astimezone(timezone.utc)
        ids=data["team_ids"]
        names=data["team_names"]
        seasons=data["seasons"]
        home_candidates=[i for i,name in enumerate(names) if name=="Juventus"]
        away_candidates=[i for i,name in enumerate(names) if name=="Napoli"]
        if len(home_candidates)!=1 or len(away_candidates)!=1:
            raise RuntimeError("Cannot resolve Juventus and Napoli uniquely in PitchAPI")
        hidx,aidx=home_candidates[0],away_candidates[0]
        rows=[Match(datetime.fromtimestamp(ts,tz=timezone.utc),
                    ids[h],ids[a],hg,ag,seasons[s],"PitchAPI:"+data["league_pin"],"PitchAPI")
              for ts,h,a,hg,ag,s in data["matches"]]
        rows=require_pitchapi(rows)
        assert len(rows)==len(data["matches"]), "GA3 source duplicate detected"
        assert all(r.kickoff<cutoff for r in rows)
        assert all(r.hxg is None and r.axg is None for r in rows), "xG must be absent"
        config=ModelConfig()
        assert config.min_train==1 and config.xg_blend==0, "Wrong GA3 model release"
        model=RidgePoisson(config).fit(rows,cutoff)
        lh,la=model.predict(ids[hidx],ids[aidx],as_of=cutoff)
        market=predict_markets(lh,la)
        scores=[(i,j,market["exact_scores"][i][j]) for i in range(len(market["exact_scores"]))
                for j in range(len(market["exact_scores"][i]))]
        scores.sort(key=lambda r:r[2],reverse=True)
        team_counts=model.metadata()["team_matches"]
        summary={
            "fixture":"Juventus - Napoli",
            "league":"Serie A",
            "match_datetime_utc":cutoff.isoformat(),
            "note":"Retrospective prematch: no target match outcome was used",
            "source":"PitchAPI private SQLite warehouse; goals only",
            "engine":"Unmodified, SHA256-verified Goal Analyzer 3.0 RC2.post2 uploaded source",
            "model_config":model.metadata()["config"],
            "seasons":seasons,
            "training_matches":model.metadata()["train_n"],
            "train_ess":model.metadata()["train_ess"],
            "training_last_kickoff":model.metadata()["training_last_kickoff"],
            "home_team":{"name":names[hidx],"id":ids[hidx],"historical_matches":team_counts.get(ids[hidx],0)},
            "away_team":{"name":names[aidx],"id":ids[aidx],"historical_matches":team_counts.get(ids[aidx],0)},
            "lambda_home":lh,"lambda_away":la,
            "home_win":market["markets"]["home_win"],
            "draw":market["markets"]["draw"],
            "away_win":market["markets"]["away_win"],
            "btts_yes":market["markets"]["btts_yes"],
            "over_1_5":market["markets"]["over_1.5"],
            "over_2_5":market["markets"]["over_2.5"],
            "over_3_5":market["markets"]["over_3.5"],
            "under_2_5":market["markets"]["under_2.5"],
            "top_exact_scores":[{"score":f"{h}-{a}","probability":p}
                                for h,a,p in scores[:8]],
            "coverage_skipped":data["skipped"],
            "validation_status":"RESEARCH_ONLY_NOT_PREDICTIVELY_VALIDATED",
            "point_in_time_source_publication_verified":False,
        }
        print("GA3_AUTHENTIC_PITCHAPI_PREMATCH_RESULT_START")
        print(json.dumps(summary,ensure_ascii=False,sort_keys=True,indent=2))
        print("GA3_AUTHENTIC_PITCHAPI_PREMATCH_RESULT_END")
