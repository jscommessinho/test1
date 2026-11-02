#!/usr/bin/env python3
"""Step 5: FootyStats match history -> frozen GA2 research model.

Separately provisioned FootyStats API key (FOOTYSTATS_API_KEY) is OPTIONAL.
Offline JSON/CSV exports are supported. This does not alter the frozen GA2
engine and does not validate cross-league predictions. RESEARCH_ONLY.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import sys
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

BASE = Path(__file__).resolve().parent
GA_DIR = BASE / 'original_sources' / 'ga2'
API_BASE = 'https://api.football-data-api.com/league-matches'
FROZEN_VERSION = 'GA2-RESEARCH-20261029-v1'

class DataError(ValueError): pass

def sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str, separators=(',', ':')).encode()).hexdigest()

def iso_utc(value):
    try:
        t=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if t.tzinfo is None or t.utcoffset() is None: raise ValueError('timezone required')
        return t.astimezone(timezone.utc)
    except (TypeError,ValueError) as exc:
        raise DataError('Timestamp must be timezone aware') from exc

def _int(v,label):
    if isinstance(v,bool) or (isinstance(v,float) and not v.is_integer()): raise DataError('Invalid '+label)
    try:
        n=int(v)
        if str(v).strip() not in (str(n),str(n)+'.0') and not isinstance(v,(int,float)):
            raise ValueError()
    except (ValueError,TypeError) as exc: raise DataError('Invalid '+label) from exc
    return n

def parse_data(data):
    if isinstance(data,list): return data
    if isinstance(data,dict) and data.get('success') is False: raise DataError('FootyStats API reported failure')
    if isinstance(data,dict) and isinstance(data.get('data'),list): return data['data']
    raise DataError('Expected FootyStats league-matches JSON with data[] or list')

def _rows_from_csv(path):
    with Path(path).open(newline='',encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

def load_files(paths):
    all_records=[];sources=[]
    for path in paths:
        p=Path(path)
        if not p.is_file(): raise DataError('Missing history file '+str(p))
        if p.suffix.lower()=='.csv': rows=_rows_from_csv(p)
        elif p.suffix.lower()=='.json': rows=parse_data(json.loads(p.read_text('utf-8')))
        else: raise DataError('History input must be .csv or .json')
        sources.append({'basename':p.name,'records':len(rows),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
        all_records.extend(rows)
    return all_records,sources

def normalize_history(raw_records,cutoff,availability_lag_days=2,expected_season_ids=None):
    """Normalize with a conservative availability proxy, *not* historical proof.

    Enforces complete fixture and date+lag < prediction cutoff. Never reads
    postgame stats into predictor features. Only final full-time goal counts.
    """
    cutoff=iso_utc(cutoff)
    if availability_lag_days < 0: raise DataError('Availability lag must be nonnegative')
    seen=set();rows=[];skipped={};ids=set()
    def skip(why): skipped[why]=skipped.get(why,0)+1
    for r in raw_records:
        if not isinstance(r,dict): raise DataError('Invalid history row object')
        status=str(r.get('status','')).strip().lower()
        if status!='complete':skip('not_complete');continue
        if r.get('no_home_away') in (1,'1',True):skip('neutral_no_home_away');continue
        try:
            matchid=_int(r['id'],'id');hi=_int(r['homeID'],'homeID');ai=_int(r['awayID'],'awayID')
            hg=_int(r['homeGoalCount'],'homeGoalCount');ag=_int(r['awayGoalCount'],'awayGoalCount')
            stamp=_int(r['date_unix'],'date_unix')
            stamp=datetime.fromtimestamp(stamp,timezone.utc)
        except (KeyError, OverflowError,OSError) as ex:raise DataError('Missing/invalid required match field') from ex
        if hi<=0 or ai<=0 or hi==ai or hg<0 or ag<0 or hg>30 or ag>30:raise DataError('Invalid team or goal values')
        if matchid in seen:raise DataError('Duplicate match ID '+str(matchid))
        seen.add(matchid)
        season=r.get('competition_id')
        if expected_season_ids:
            if season not in (None,'') and _int(season,'competition_id') not in expected_season_ids:
                raise DataError('Competition season ID inconsistent with explicitly requested history')
        if season not in (None,''):ids.add(str(season))
        # Match kickoff date is a proxy for completion: excludes late-resumed games unless verified separately.
        completion=stamp.date()
        available=completion+timedelta(days=availability_lag_days)
        if datetime.combine(available,datetime.min.time(),timezone.utc)>=cutoff:
            skip('not_available_at_cutoff');continue
        # Note: completed/suspended date not proven by `date_unix`; exclude known suspensions if separately marked.
        home=f'fs-team-{hi}'; away=f'fs-team-{ai}'
        rows.append({'id':f'fs-{matchid}','season':str(r.get('season') or season or 'unknown'),
                     'date':completion.isoformat(),'forecast_date':completion.isoformat(),
                     'completion_date':completion.isoformat(),'available_date':available.isoformat(),
                     'home':home,'away':away,'hg':hg,'ag':ag,
                     'hh':None,'ha':None,'source':'FootyStats league-matches export',
                     'availability_is_proxy':True,'league_season_id':season})
    rows.sort(key=lambda r:(r['date'],r['id']))
    return rows,{'complete_eligible':len(rows),'discarded':skipped,'reported_season_ids':sorted(ids),
                 'availability_lag_days':availability_lag_days,'proven_point_in_time':False}

def load_frozen_model():
    lock=json.loads((GA_DIR/'locked_model.json').read_text('utf-8'))
    if lock['config']!={'half_life':365,'ridge':10,'venue':True} or lock['distribution']!='poisson' or lock['scale']!=1:
        raise DataError('Frozen GA2 configuration changed unexpectedly')
    # Import original source without rewriting its math or training method.
    spec=importlib.util.spec_from_file_location('betpilot_frozen_ga2_research',GA_DIR/'research.py')
    mod=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=mod
    spec.loader.exec_module(mod)
    return lock,mod

def predict_from_history(raw_records,fixture,home_fs_id,away_fs_id,cutoff,*,min_history=500,allow_unseen=False):
    """Only FT GA2, never GTS or certified HT/2H. Strict league history provenance needed."""
    cutoff=iso_utc(cutoff)
    kickoff=iso_utc(fixture['kickoff'])
    if cutoff>=kickoff:raise DataError('Predictor cutoff must precede kickoff')
    training_date=cutoff.date().replace(day=1)  # GA2 frozen protocol: monthly refit on first day
    training_cutoff=datetime.combine(training_date,datetime.min.time(),timezone.utc)
    rows,audit=normalize_history(raw_records,training_cutoff)
    if len(rows)<min_history: raise DataError(f'INSUFFICIENT_DATA: {len(rows)} available matches, minimum {min_history}')
    home=f'fs-team-{_int(home_fs_id,"home_fs_id")}'
    away=f'fs-team-{_int(away_fs_id,"away_fs_id")}'
    if home==away:raise DataError('Same team ID on both sides')
    present={r['home'] for r in rows}|{r['away'] for r in rows}
    unseen=[team for team in (home,away) if team not in present]
    if unseen and not allow_unseen:raise DataError('INSUFFICIENT_DATA: unseen FootyStats team IDs '+','.join(unseen))
    locked,original=load_frozen_model()
    cfg=original.Config(**locked['config'])
    model=original.Model(cfg).fit(rows,training_date.isoformat())
    if len(model.rows)<min_history:raise DataError('Insufficient available rows for frozen model')
    lh,la=model.predict(home,away)
    if not all(math.isfinite(x) and 0<x<30 for x in (lh,la)):raise DataError('Invalid GA2 intensities')
    probs,grid,fall=original.probs(lh,la,locked['distribution'],locked['parameter'])
    if fall:raise DataError('Unexpected distribution fallback')
    common={k:probs[k] for k in ('1','X','2','O2.5','U2.5','GG','NG')}
    ga={'event_id':fixture['event_id'],'home':fixture['home'],'away':fixture['away'],
        'version':FROZEN_VERSION,'operational_eligible':False,'cutoff':cutoff.isoformat(timespec='seconds'),
        'lambda_home':lh,'lambda_away':la,'probabilities':common,
        'training_n':len(model.rows),'training_ess':model.ess,
        'training_refit_date':training_date.isoformat(),
        'unseen_team_ids':unseen,'gts':None,
        'source':'FootyStats match results / GA2 frozen engine',
        'research_only':True}
    audit.update({'train_n':len(model.rows),'training_ess':model.ess,
                  'engine_version':FROZEN_VERSION,'model_sha256':hashlib.sha256((GA_DIR/'research.py').read_bytes()).hexdigest(),
                  'note':'Cross-provider team identity must be externally verified. Historical result availability is a two-day proxy, not a point-in-time guarantee.',
                  'validation':'RESEARCH_ONLY; not validated for this league'})
    return ga,audit

def download_footystats_seasons(season_ids,api_key,output_dir,max_requests=6,timeout=15):
    """Optional AUTHENTICATED FootyStats API; premium website access alone does NOT supply this key."""
    if not api_key or not api_key.strip():raise DataError('FOOTYSTATS_API_KEY missing (Premium website != API subscription)')
    if len(season_ids)>max_requests:raise DataError('Request budget exceeded')
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=True)
    results=[]
    for season_id in season_ids:
        sid=_int(season_id,'season_id')
        # Never include URLs or credentials in logs/errors; provider may echo key in parameters.
        url=API_BASE+'?'+urlencode({'key':api_key.strip(),'season_id':sid,'max_per_page':1000,'page':1})
        req=Request(url,headers={'Accept':'application/json'})
        try:
            with urlopen(req,timeout=timeout) as response:
                obj=json.loads(response.read(20_000_000))
        except (HTTPError,URLError,TimeoutError,ValueError) as ex:
            raise DataError('FootyStats HTTP acquisition failed: '+type(ex).__name__) from None
        records=parse_data(obj)
        pager=obj.get('pager') or {} if isinstance(obj,dict) else {}
        # Do not silently accept incomplete multipage season.
        if int(pager.get('max_page',pager.get('total_pages',1)) or 1)>1:
            raise DataError('FootyStats season is paginated beyond one page: update max_requests and collector')
        for r in records:
            if r.get('competition_id') not in (None,'') and _int(r['competition_id'],'competition_id')!=sid:
                raise DataError('FootyStats season mismatch')
        # We persist only the data array, not `parameters` that may echo the API key.
        target=output/f'footystats_season_{sid}.json'
        target.write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n','utf-8')
        results.append({'path':str(target),'count':len(records),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    return results

def main(argv=None):
    p=argparse.ArgumentParser(description='FootyStats history → original GA2 (RESEARCH_ONLY)')
    p.add_argument('--history',type=Path,nargs='*',default=[])
    p.add_argument('--fetch-season-ids',type=int,nargs='*',default=[])
    p.add_argument('--home-fs-id',type=int)
    p.add_argument('--away-fs-id',type=int)
    p.add_argument('--home',help='Name verified against the external fixture by the operator')
    p.add_argument('--away')
    p.add_argument('--event-id')
    p.add_argument('--kickoff',help='ISO8601 with timezone')
    p.add_argument('--cutoff',help='ISO8601 with timezone')
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--allow-unseen',action='store_true')
    args=p.parse_args(argv)
    try:
        args.output_dir.mkdir(parents=True,exist_ok=True)
        files=list(args.history)
        if args.fetch_season_ids:
            got=download_footystats_seasons(args.fetch_season_ids,os.environ.get('FOOTYSTATS_API_KEY',''),args.output_dir/'source_capture')
            files += [Path(r['path']) for r in got]
        if not files:raise DataError('No FootyStats history JSON/CSV provided')
        if not all(x is not None for x in (args.home_fs_id,args.away_fs_id,args.home,args.away,args.event_id,args.kickoff,args.cutoff)):
            raise DataError('Fixture identity, verified FS team IDs, kickoff, cutoff all required')
        raw,sources=load_files(files)
        fixture={'event_id':args.event_id,'home':args.home,'away':args.away,'kickoff':args.kickoff}
        ga,audit=predict_from_history(raw,fixture,args.home_fs_id,args.away_fs_id,args.cutoff,allow_unseen=args.allow_unseen)
        audit['source_files']=sources
        (args.output_dir/'GA2_RESEARCH_OUTPUT.json').write_text(json.dumps(ga,ensure_ascii=False,indent=2,allow_nan=False)+'\n','utf8')
        (args.output_dir/'GA2_INPUT_AUDIT.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2,allow_nan=False)+'\n','utf8')
        print(json.dumps({'status':'RESEARCH_ONLY','train_n':ga['training_n'],'lambda_home':ga['lambda_home'],'lambda_away':ga['lambda_away']},ensure_ascii=False))
        return 0
    except (DataError,OSError,ValueError,RuntimeError) as ex:
        print('GA2_STEP5_FAILED: '+str(ex),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
