#!/usr/bin/env python3
"""Quota-aware PREMATCH identity + odds adapter for frozen RC15 / MA2.

No API-Football football-results collection. One /fixtures?date=UTC request
shared by all matches from that date, then one /odds?fixture=ID&bookmaker=8
per uncached match. /status is not billed by API-SPORTS as of 2026-10-31.

Snapshot caches contain public provider data but NEVER credential headers.
This module refuses a fixture whose competition/season or teams mismatch.
It calls the original frozen Step5 adapter without modifying its algorithms.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE='https://v3.football.api-sports.io'
VENDOR=Path(__file__).resolve().parents[1]/'vendor'/'BETPILOT_STEP5'
if VENDOR.exists() and str(VENDOR) not in sys.path:
    sys.path.insert(0,str(VENDOR))

class BudgetError(ValueError): pass


def utc(dt):
    if dt.tzinfo is None or dt.utcoffset() is None:raise BudgetError('timezone required')
    return dt.astimezone(timezone.utc)


def timestamp(s):
    try:return utc(datetime.fromisoformat(str(s).replace('Z','+00:00')))
    except (TypeError,ValueError,AttributeError) as e:raise BudgetError('malformed provider timestamp') from e


def read_cache(path, now, max_age_seconds):
    p=Path(path)
    if not p.is_file():return None
    try:
        item=json.loads(p.read_text('utf-8'))
        captured=timestamp(item['capture_utc'])
        if captured>now or (now-captured).total_seconds()>max_age_seconds:return None
        data=item['data']
        if not isinstance(data,dict) or not isinstance(data.get('response'),list):return None
        return data
    except (ValueError, KeyError, OSError, TypeError):return None


def save_cache(path, now, data):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    temp=p.with_suffix('.part')
    temp.write_text(json.dumps({'capture_utc':utc(now).isoformat(),'data':data},ensure_ascii=False),encoding='utf8')
    temp.replace(p)


class QuotaClient:
    """Fail-closed per-job quota guard; serialize jobs at GH Actions level.

    Other tools using same API key can consume credits concurrently. Recheck
    daily remaining via response header and /status for each job, stop early.
    """
    def __init__(self,key,*,reserve=20,max_calls=30,timeout=20,opener=None):
        if not key or not key.strip():raise BudgetError('APISPORTS_KEY not set')
        if not 10<=reserve<=90 or not 1<=max_calls<=80:raise BudgetError('unsafe quota configuration')
        self._key=key.strip();self.reserve=reserve;self.max_calls=max_calls
        self.timeout=timeout;self._open=opener or urlopen
        self.used=0;self.remaining=None;self.limit=None

    def _request(self,path,params):
        req=Request(API_BASE+path+('?' + urlencode(params) if params else ''),headers={
            'x-apisports-key':self._key,'Accept':'application/json',
            'User-Agent':'BETPILOT-PREMATCH-research/1.0'})
        try:
            with self._open(req,timeout=self.timeout) as response:
                raw=response.read(15_000_001)
                if len(raw)>15_000_000:raise BudgetError('provider response too large')
                # 'getheader' optional on mocks; do not leak provider headers to output.
                hdr=response.headers
                hdrremain=hdr.get('x-ratelimit-requests-remaining') if hdr else None
                obj=json.loads(raw)
                if not isinstance(obj,dict) or obj.get('errors'):
                    raise BudgetError('provider error, quota or parameters rejected')
                return obj,hdrremain
        except HTTPError as e:raise BudgetError('provider HTTP '+str(e.code)) from None
        except (URLError,TimeoutError,UnicodeError,ValueError) as e:
            if isinstance(e,BudgetError):raise
            raise BudgetError('provider connection/data error') from None

    def preflight(self):
        obj,_=self._request('/status',{})  # API-SPORTS docs: not counted.
        try:
            requests=obj['response']['requests']
            limit=int(requests['limit_day']);used=int(requests['current'])
            if limit<=0 or used<0 or used>limit:raise ValueError()
        except (KeyError,ValueError,TypeError) as e:
            raise BudgetError('cannot verify API daily quota: refusing billable request') from e
        self.limit=limit;self.remaining=limit-used
        if self.remaining<=self.reserve:
            raise BudgetError('daily reserved API quota reached; abstaining')
        return {'quota_limit':limit,'quota_remaining_at_start':self.remaining,'reserve':self.reserve}

    def get(self,path,**params):
        if path not in ('/fixtures','/odds','/teams'):raise BudgetError('endpoint not allowlisted')
        if self.remaining is None:raise BudgetError('/status quota check required')
        if self.used>=self.max_calls or self.remaining<=self.reserve:
            raise BudgetError('daily quota reserve or per-run cap reached')
        # No automatic retry (avoids accidental duplicate consumption).
        self.used+=1
        try:obj,header_remaining=self._request(path,params)
        finally:self.remaining-=1  # pessimistic even on network failure
        if header_remaining is not None:
            try:
                reported=int(header_remaining)
                if reported<0:raise ValueError()
                self.remaining=min(self.remaining,reported)
            except ValueError:raise BudgetError('invalid provider daily remaining header')
        return obj


def _normalize(s):
    from api_football_adapter import norm
    return norm(s)


def select_match(fixtures,home,away,league_id,season,now):
    from api_football_adapter import identify_fixture
    matches=[]
    for row in fixtures.get('response',[]):
        try:
            if int(row['league']['id'])!=league_id or int(row['league']['season'])!=season:continue
            if _normalize(row['teams']['home']['name'])!=_normalize(home):continue
            if _normalize(row['teams']['away']['name'])!=_normalize(away):continue
            item=identify_fixture({'response':[row]},int(row['fixture']['id']),now)
            matches.append((item,row))
        except (KeyError,ValueError,TypeError):continue
    if len(matches)!=1:raise BudgetError('fixture absent or ambiguous for requested teams+league+season+date')
    return matches[0]


def run(client,*,home,away,match_date,league_id,season,cache_dir,now=None,run_native=False,output_dir=None):
    """One date fixture query per cache expiry, one odds query per fixture / 3 h.
    Caller supplies exact league ID, never rely on 'Serie A' league name alone.
    """
    from api_football_adapter import (extract_odds,build_input,hash_object)
    from integrate_native import check_payload
    # Verdict C is applied outside the untouched Step5 engines.
    if __package__:
        from .bookmaker_verdict_c import run_full_fixed as run_full
    else:
        from bookmaker_verdict_c import run_full_fixed as run_full
    now=utc(now or datetime.now(timezone.utc))
    try:date.fromisoformat(match_date)
    except ValueError:raise BudgetError('match_date must be YYYY-MM-DD UTC')
    if not 1<=int(league_id)<=999999 or not 2020<=int(season)<=2100:
        raise BudgetError('invalid league or season')
    if not home or not away or _normalize(home)==_normalize(away):raise BudgetError('two different teams required')
    quota=client.preflight()
    directory=Path(cache_dir)
    fixture_file=directory/('fixtures_utc_'+match_date+'.json')
    fdata=read_cache(fixture_file,now,6*3600)
    sources={'fixture':'CACHE' if fdata is not None else 'API'}
    if fdata is None:
        fdata=client.get('/fixtures',date=match_date,timezone='UTC')
        if fdata.get('paging',{}).get('total',1)!=1:
            raise BudgetError('fixtures unexpectedly paginated; refuse incomplete discovery')
        save_cache(fixture_file,now,fdata)
    fixture,row=select_match(fdata,home,away,int(league_id),int(season),now)
    fid=fixture['id']
    # Future fixture date and source date must agree (UTC).
    if timestamp(fixture['kickoff']).date().isoformat()!=match_date:
        raise BudgetError('kickoff not on requested UTC day')
    # Near kickoff force fresh fixture status; no silently stale NS information.
    if sources['fixture']=='CACHE' and timestamp(fixture['kickoff'])-now<timedelta(hours=2):
        fdata=client.get('/fixtures',date=match_date,timezone='UTC')
        fixture,row=select_match(fdata,home,away,int(league_id),int(season),now)
        save_cache(fixture_file,now,fdata)
        sources['fixture']='API_REFRESH'
    odds_file=directory/('odds_bet365_'+str(fid)+'.json')
    odata=read_cache(odds_file,now,2*3600)
    sources['odds']='CACHE' if odata is not None else 'API'
    if odata is None:
        odata=client.get('/odds',fixture=fid,bookmaker=8)
        save_cache(odds_file,now,odata)
    updated,quotes=extract_odds(odata,fid,now)
    # Reject cached snapshots updated after kickoff and provider future timestamps.
    payload=build_input(fixture,quotes,updated,now,{'response':[row]},odata,live=True)
    check_payload(payload)
    result={'status':'BOOKMAKER_ONLY_RESEARCH_ONLY','match':payload['match'],
            'rc15_prices':quotes,'odds_updated_utc':updated.isoformat(),
            'source_paths':sources,'requests_this_run':client.used,
            'quota':{**quota,'remaining_estimate_after_run':client.remaining},
            'provider_fixture_hash':hash_object({'response':[row]}),
            'note':'No new results/history fetched from API-Football. Frozen GA2 unchanged.'}
    if output_dir:
        out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
        (out/'RC15_EVENT_INPUT.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n','utf8')
        if run_native:
            result['native']=run_full(payload,out/'native_run')['status']
        (out/'REQUEST_AUDIT.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n','utf8')
    elif run_native:raise BudgetError('native execution requires output directory')
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description='Budget-protected API-Football bookmaker-only prematch')
    p.add_argument('--home',required=True);p.add_argument('--away',required=True)
    p.add_argument('--date',required=True,help='UTC kickoff YYYY-MM-DD')
    p.add_argument('--league-id',required=True,type=int)
    p.add_argument('--season',required=True,type=int,help='API-Football season start year')
    p.add_argument('--cache-dir',default='.cache/betpilot_api_football')
    p.add_argument('--output-dir',default='output/api_football_smart')
    p.add_argument('--reserve',type=int,default=20)
    p.add_argument('--max-calls',type=int,default=5)
    p.add_argument('--run-native',action='store_true')
    args=p.parse_args(argv)
    try:
        result=run(QuotaClient(os.environ.get('APISPORTS_KEY',''),reserve=args.reserve,max_calls=args.max_calls),
                   home=args.home,away=args.away,match_date=args.date,league_id=args.league_id,
                   season=args.season,cache_dir=args.cache_dir,output_dir=args.output_dir,run_native=args.run_native)
        print(json.dumps({'status':result['status'],'fixture':result['match']['event_id'],
                          'calls':result['requests_this_run'],
                          'remaining_estimate':result['quota']['remaining_estimate_after_run']},ensure_ascii=False))
        return 0
    except (BudgetError,ValueError,OSError,KeyError) as e:
        print('QUOTA_SAFE_ABSTAIN:',str(e),file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
