"""Only Bookmaker RC15 + Verdetto C. Standalone CLI (no Step5)."""
import argparse,json,os,sys
from datetime import datetime,timezone
from pathlib import Path
from .standalone import BookmakerError,from_payload,from_csv,digest

def main(argv=None):
 p=argparse.ArgumentParser(description="BETPILOT Bookmaker RC15 + Verdetto C standalone")
 g=p.add_mutually_exclusive_group(required=True)
 g.add_argument("--status-only",action="store_true")
 g.add_argument("--input-json",type=Path)
 g.add_argument("--input-csv",type=Path)
 g.add_argument("--fixture-id",type=int)
 g.add_argument("--search",action="store_true")
 p.add_argument("--home");p.add_argument("--away");p.add_argument("--date")
 p.add_argument("--league-id",type=int);p.add_argument("--reserve",type=int,default=20)
 p.add_argument("--max-api-calls",type=int,default=4)
 p.add_argument("--output-dir",type=Path,default=Path("output/bookmaker"))
 a=p.parse_args(argv);o=a.output_dir
 try:
  if a.input_json:r=from_payload(json.loads(a.input_json.read_text("utf8")),o)
  elif a.input_csv:r=from_csv(a.input_csv,o)
  else:
   from .quota_client import QuotaClient
   from .api_football import acquire
   client=QuotaClient(os.environ.get("APISPORTS_KEY",""),reserve=a.reserve,max_calls=a.max_api_calls)
   status=client.preflight()
   if a.status_only:
    o.mkdir(parents=True,exist_ok=True)
    d={"status":"API_FOOTBALL_AUTHENTICATED","quota_limit":status["quota_limit"],
       "quota_remaining":status["quota_remaining_at_start"],"reserved_calls":status["reserve"],
       "secret_disclosed":False,"other_analyzers_executed":[]}
    (o/"BOOKMAKER_STATUS.json").write_text(json.dumps(d,indent=2)+"\n")
    print(json.dumps(d));return 0
   if a.search and (not a.home or not a.away or not a.date):
    raise BookmakerError("--search requires home, away and date")
   day=datetime.strptime(a.date,"%Y-%m-%d").date() if a.date else None
   payload,raw=acquire(fixture_id=a.fixture_id,home=a.home,away=a.away,date=day,
        horizon_days=1,league_id=a.league_id,client=client,now=datetime.now(timezone.utc))
   r=from_payload(payload,o)
   cap=o/"PROVIDER_CAPTURE.json"
   cap.write_text(json.dumps(raw,indent=2,ensure_ascii=False,allow_nan=False)+"\n")
   mp=o/"RUN_MANIFEST.json";m=json.loads(mp.read_text())
   m["sha256"]["PROVIDER_CAPTURE.json"]=digest(cap)
   mp.write_text(json.dumps(m,indent=2,ensure_ascii=False)+"\n")
  print(json.dumps({"status":r["status"],"independent":True,
     "other_analyzers_executed":[],"output":str(o/"BOOKMAKER_ANALYSIS.json")}))
  return 0
 except (BookmakerError,ValueError,KeyError,OSError,RuntimeError,AssertionError,ImportError) as e:
  o.mkdir(parents=True,exist_ok=True)
  d={"status":"BOOKMAKER_ABSTAIN","reason":str(e),"quotes_invented":False,"other_analyzers_executed":[]}
  (o/"BOOKMAKER_ABSTAIN.json").write_text(json.dumps(d,indent=2,ensure_ascii=False)+"\n")
  print("BOOKMAKER ABSTAIN: "+str(e),file=sys.stderr)
  return 2

if __name__=="__main__":
 raise SystemExit(main())
