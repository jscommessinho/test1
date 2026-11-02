#!/usr/bin/env python3
"""BETPILOT: read-only, key-authenticated PitchAPI xG+shots research pilot.

No changes to frozen GA2/GTS/RC15/MA2. Does not estimate GTS scores.
Source: https://pitchapi.dev/ (read-only REST API v1, observed docs 2026-10-31).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from betpilot.gts_pitchapi_quality import assess_batch, QualityError, MAX_ABS_XG_DELTA

HOST = 'https://api.pitchapi.dev'
ID = re.compile(r'^[mlt]_[A-Za-z0-9]{6}$')
MAX_BODY = 3_000_000

class ProbeError(ValueError):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProbeError('Redirect blocked to protect API credential')

class Client:
    def __init__(self, key, *, budget=100, pause=.20, transport=None):
        if not isinstance(key, str) or not key.strip() or '\r' in key or '\n' in key:
            raise ProbeError('PITCHAPI_API_KEY is required')
        if not 1 <= budget <= 400 or not 0 <= pause <= 10:
            raise ProbeError('Invalid safe request budget or pause')
        self.key = key.strip()
        self.budget = budget
        self.pause = pause
        self.used = 0
        self.transport = transport
        self.opener = build_opener(NoRedirect())

    def get(self, endpoint, params=None):
        if not (endpoint == '/v1/leagues' or
                re.fullmatch(r'/v1/leagues/l_[A-Za-z0-9]{6}/matches', endpoint) or
                re.fullmatch(r'/v1/date/\d{4}-\d{2}-\d{2}', endpoint) or
                re.fullmatch(r'/v1/matches/m_[A-Za-z0-9]{6}(?:/(?:shots|stats))?', endpoint)):
            raise ProbeError('Endpoint not allowlisted')
        if self.used >= self.budget:
            raise ProbeError('Local API request budget exhausted; abstaining')
        self.used += 1
        if self.pause and self.used > 1:
            time.sleep(self.pause)
        url = HOST + endpoint + ('?' + urlencode(params) if params else '')
        try:
            if self.transport is not None:
                obj = self.transport(endpoint, params)
            else:
                req = Request(url, headers={'X-API-KEY': self.key, 'Accept': 'application/json',
                                             'User-Agent': 'BETPILOT-prematch-gts-research/1.0'})
                with self.opener.open(req, timeout=20) as response:
                    raw = response.read(MAX_BODY + 1)
                    if len(raw) > MAX_BODY:
                        raise ProbeError('Provider response larger than 3 MB')
                    obj = json.loads(raw)
        except HTTPError as exc:
            raise ProbeError('PitchAPI HTTP status ' + str(exc.code) +
                             (' (key rejected)' if exc.code == 401 else '')) from None
        except (URLError, TimeoutError, UnicodeError, ValueError) as exc:
            if isinstance(exc, ProbeError):
                raise
            raise ProbeError('PitchAPI transport or JSON error (credentials never logged)') from None
        if not isinstance(obj, dict) or not isinstance(obj.get('data'), dict):
            error = obj.get('error', {}) if isinstance(obj, dict) else {}
            code = error.get('code', 'INVALID_RESPONSE') if isinstance(error, dict) else 'INVALID_RESPONSE'
            raise ProbeError('PitchAPI returned ' + str(code)[:60])
        return obj['data']

def ident(x, prefix):
    if not isinstance(x, str) or not ID.fullmatch(x) or not x.startswith(prefix+'_'):
        raise ProbeError('Invalid provider ' + prefix + ' ID')
    return x

def norm(v):
    return ' '.join(str(v).lower().replace(' fc','').replace(' football club','').split())

def _fixture_on_utc_day(m, day):
    """PitchAPI date listings omit date for upcoming fixtures; time_utc is authoritative.

    Reject contradictory date/time fields and timezone-naive timestamps rather
    than silently shifting a fixture to the wrong day.
    """
    recorded=m.get('date')
    if recorded not in (None,'') and recorded!=day:
        return False
    stamped=m.get('time_utc')
    if stamped:
        try:
            kt=datetime.fromisoformat(str(stamped).replace('Z','+00:00'))
            if kt.tzinfo is None or kt.utcoffset() is None:
                return False
            return kt.astimezone(timezone.utc).date().isoformat()==day
        except (TypeError,ValueError,OverflowError):
            return False
    return recorded==day

def find_fixture(matches, home, away, day):
    found=[m for m in matches if isinstance(m,dict) and
           _fixture_on_utc_day(m,day) and
           norm((m.get('home_team') or {}).get('name','')) == norm(home) and
           norm((m.get('away_team') or {}).get('name','')) == norm(away)]
    if len(found) != 1:
        raise ProbeError('Fixture name/date not found exactly once in PitchAPI')
    m=found[0]
    ident(m['id'],'m')
    for side in ('home_team','away_team'):
        ident(m[side]['id'],'t')
    if m['home_team']['id']==m['away_team']['id']:
        raise ProbeError('Invalid equal team IDs')
    return m

def date_eligible(match_day, target_date, lag_days=2):
    return date.fromisoformat(match_day)+timedelta(days=lag_days)<date.fromisoformat(target_date)

def build_histories(lists, target, target_date, venue_count=10):
    # Stable provider IDs ensure no fuzzy cross-provider team guesses.
    unique={}
    for source in lists:
        for m in source:
            if not isinstance(m,dict):
                continue
            mid=ident(m['id'],'m')
            if mid in unique:
                old=unique[mid]
                if old.get('date')!=m.get('date') or old.get('home_team',{}).get('id')!=m.get('home_team',{}).get('id'):
                    raise ProbeError('Conflicting duplicated match ID')
                continue
            unique[mid]=m
    eligible=[]
    for m in unique.values():
        if m.get('status') != 'finished' or not date_eligible(m['date'],target_date):
            continue
        ident(m['home_team']['id'],'t');ident(m['away_team']['id'],'t')
        if m['home_team']['id']==m['away_team']['id']:
            raise ProbeError('Equal team IDs')
        eligible.append(m)
    eligible.sort(key=lambda m:(m['date'],m['id']),reverse=True)
    h_id=target['home_team']['id']; a_id=target['away_team']['id']
    h_all=[m for m in eligible if h_id in (m['home_team']['id'],m['away_team']['id'])]
    a_all=[m for m in eligible if a_id in (m['home_team']['id'],m['away_team']['id'])]
    h_venue=[m for m in h_all if m['home_team']['id']==h_id]
    a_venue=[m for m in a_all if m['away_team']['id']==a_id]
    if len(h_all)<10 or len(a_all)<10 or len(h_venue)<4 or len(a_venue)<4:
        raise ProbeError('GTS history insufficient: need >=10 each and >=4 relevant venue')
    chosen={m['id']:m for m in h_all[:10]+a_all[:10]+h_venue[:venue_count]+a_venue[:venue_count]}
    return {'home_all':h_all,'away_all':a_all,'home_venue':h_venue,'away_venue':a_venue,'chosen':list(chosen.values()),'eligible_league':len(eligible)}

def nonneg(value, label):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
        raise ProbeError('Missing or invalid '+label)
    return float(value)

def parse_shots(payload, match):
    if payload.get('match_id') != match['id'] or not isinstance(payload.get('periods'),list):
        raise ProbeError('Shot response identity/schema mismatch')
    h=match['home_team']['id'];a=match['away_team']['id']
    records={h: {'xg':0.0,'shots':0},a:{'xg':0.0,'shots':0}}
    seen=set()
    for per in payload['periods']:
        if per.get('period') not in ('FirstHalf','SecondHalf','ExtraFirstHalf','ExtraSecondHalf'):
            raise ProbeError('Unexpected shot period: would risk double count')
        if not isinstance(per.get('shots'),list):
            raise ProbeError('Missing shot list')
        for s in per['shots']:
            sid=s.get('id');team=s.get('team_id');xg=nonneg(s.get('expected_goals'),'shot xG')
            if not isinstance(sid,str) or not sid.startswith('s_') or team not in records:
                raise ProbeError('Unrecognized shot or team ID')
            if sid in seen:
                raise ProbeError('Duplicate shot ID')
            if xg>1:
                raise ProbeError('Shot xG greater than 1')
            seen.add(sid)
            records[team]['shots']+=1;records[team]['xg']+=xg
    for v in records.values():
        if v['shots'] and v['xg']==0:
            raise ProbeError('All shot xGs zero despite attempts; requires manual review')
    return records

def parse_match_stats(payload, match):
    if payload.get('match_id') != match['id']:
        raise ProbeError('Stats response match ID mismatch')
    periods=[p for p in payload.get('periods',[]) if p.get('period')=='All']
    if len(periods)!=1:
        return None
    found=[]
    for group in periods[0].get('groups',[]):
        for item in group.get('items',[]):
            if item.get('key')=='expected_goals':
                found.append((item.get('home'),item.get('away')))
    if not found:
        return None
    try:
        values=[tuple(float(z) for z in pair) for pair in found]
    except (TypeError,ValueError):
        return None
    if not all(math.isfinite(v) and v>=0 for pair in values for v in pair):
        return None
    # The real provider duplicates the same expected_goals key in two
    # groups of the All-period stats. Treat identical rounded values once;
    # contradictory duplicates are a hard data-quality failure.
    initial=values[0]
    if any(abs(pair[0]-initial[0])>0.011 or abs(pair[1]-initial[1])>0.011
           for pair in values[1:]):
        raise ProbeError('Conflicting duplicate official team xG fields')
    return initial

def pilot(client, *, day, home, away, league_name='Premier League', country='ENG', seasons=('2025/2026','2026/2027'), official_reference=None):
    catalogue=client.get('/v1/leagues').get('leagues')
    if not isinstance(catalogue,list):raise ProbeError('PitchAPI league list missing')
    candidates=[x for x in catalogue if norm(x.get('name',''))==norm(league_name) and x.get('country_code')==country]
    if len(candidates)!=1:raise ProbeError('Target league missing or ambiguous in PitchAPI')
    league=candidates[0];lid=ident(league['id'],'l')
    documented_seasons=league.get('seasons')
    if not isinstance(documented_seasons,list) or not all(s in documented_seasons for s in seasons):
        raise ProbeError('PitchAPI does not expose both requested seasons for this league')
    fixture_list=client.get('/v1/date/'+day, {'status':'all'}).get('matches',[])
    if not isinstance(fixture_list,list):raise ProbeError('Date response missing matches')
    target=find_fixture(fixture_list,home,away,day)
    if target.get('status')=='finished':
        raise ProbeError('Target already finished; cannot claim live prematch execution')
    kickoff=target.get('time_utc')
    try:
        kt=datetime.fromisoformat(kickoff.replace('Z','+00:00'))
        if kt.utcoffset() is None or datetime.now(timezone.utc)>=kt:
            raise ProbeError('Target kickoff already passed; abstaining from prematch')
    except (AttributeError,ValueError,TypeError) as exc:
        if isinstance(exc,ProbeError):raise
        raise ProbeError('Invalid target kickoff timestamp') from None
    fixture_detail=client.get('/v1/matches/'+target['id'])
    if fixture_detail.get('id')!=target['id'] or fixture_detail.get('league',{}).get('id')!=lid or \
       fixture_detail.get('home_team',{}).get('id')!=target['home_team']['id'] or \
       fixture_detail.get('away_team',{}).get('id')!=target['away_team']['id']:
        raise ProbeError('League or team identity mismatch in verified match detail')
    season_lists=[]
    for season in seasons:
        listed=client.get('/v1/leagues/'+lid+'/matches',{'season':season,'status':'played'})
        if listed.get('league',{}).get('id')!=lid or not isinstance(listed.get('matches'),list):
            raise ProbeError('League response identity/schema mismatch')
        season_lists.append(listed['matches'])
    hist=build_histories(season_lists,target,day)
    output=[]
    for m in hist['chosen']:
        sh=client.get('/v1/matches/'+m['id']+'/shots')
        counts=parse_shots(sh,m)
        st=client.get('/v1/matches/'+m['id']+'/stats')
        stated=parse_match_stats(st,m)
        derived=[counts[m['home_team']['id']]['xg'],counts[m['away_team']['id']]['xg']]
        # Validation of 0.50 inclusive is centralised in the GLOBAL policy;
        # PitchAPI's own team stats are NOT league-official external xG.
        output.append({'match_id':m['id'],'date':m['date'],
                       'home':{'id':m['home_team']['id'],'name':m['home_team']['name'],**counts[m['home_team']['id']]},
                       'away':{'id':m['away_team']['id'],'name':m['away_team']['name'],**counts[m['away_team']['id']]},
                       'team_stats_xg':stated,'source':'PitchAPI shots+stats'})
    ids={r['match_id'] for r in output}
    if ids!={m['id'] for m in hist['chosen']}:
        raise ProbeError('Missing historical match metrics')
    league_ref={'id':lid,'name':league['name'],'country':country}
    quality=assess_batch(output,official_reference,league_ref)
    own_compared=sum(m['team_stats_xg'] is not None for m in output)
    if quality['hard_discrepancy']:
        status='PITCHAPI_SOURCE_PILOT_REVIEW_REQUIRED'
    elif quality['all_matches_officially_crosschecked']:
        status='PITCHAPI_SOURCE_PILOT_FULL_OFFICIAL_QUALITY_PASS_NOT_GTS_SCORE'
    else:
        status='PITCHAPI_SOURCE_PILOT_INTERNAL_PASS_OFFICIAL_REFERENCE_PENDING'
    quality_flags=[
        {'match_id':r['match_id'],'date':r['date'],
         'pitchapi_internal_status':r['internal_pitchapi_status'],
         'internal_deltas':r['internal_absolute_deltas'],
         'league_official_status':r['league_official_status'],
         'official_deltas':r['league_official_absolute_deltas']}
        for r in quality['matches']
        if r['internal_pitchapi_status']!='PASS' or r['league_official_status']=='REJECTED_OFFICIAL_ABOVE_0_50'
    ]
    return {'status':status,
            'fixture':{'date':day,'match_id':target['id'],'home':target['home_team'],'away':target['away_team']},
            'provider':'PitchAPI','league':league_ref,
            'seasons':list(seasons),'availability_lag_days':2,'prematch_point_in_time_proven':False,
            'history_coverage':{k:len(hist[k]) for k in ('home_all','away_all','home_venue','away_venue')},
            'eligible_league_listed':hist['eligible_league'],'matches_with_verified_shots_and_xg':len(output),
            'api_requests':client.used,'matches':sorted(output,key=lambda x:(x['date'],x['match_id'])),
            'pitchapi_team_xg_internal_compared':own_compared,
            'pitchapi_team_xg_internal_unavailable':len(output)-own_compared,
            'quality_audit':quality,'quarantined_xg_discrepancies':quality_flags,
            'internal_pitchapi_quality_passed':quality['internal_quality_passed'],
            'gts_input_quality_passed':quality['all_matches_officially_crosschecked'],
            'GTS_2_0_score':None,'GTS_score_status':'NOT_CALCULATED: no provider-specific frozen ECDF and league priors',
            'model_sources_modified':False}

def main(argv=None):
    p=argparse.ArgumentParser(description='Read-only PitchAPI GTS xG+shots pilot, no model changes')
    p.add_argument('--date',default='2026-10-31');p.add_argument('--home',default='Chelsea');p.add_argument('--away',default='Manchester United')
    p.add_argument('--max-requests',type=int,default=100);p.add_argument('--pause',type=float,default=.2)
    p.add_argument('--league-name',default='Premier League')
    p.add_argument('--country-code',default='ENG')
    p.add_argument('--seasons',nargs=2,default=['2025/2026','2026/2027'])
    p.add_argument('--official-reference',type=Path,default=None,
                   help='Only an operator-attested LEAGUE official FT xG JSON; omit when unavailable')
    p.add_argument('--output',type=Path,default=Path('output/pitchapi_gts/CHELSEA_MANUTD_SOURCE_AUDIT.json'))
    a=p.parse_args(argv)
    key=os.environ.get('PITCHAPI_API_KEY','')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    if not key:
        a.output.write_text(json.dumps({'status':'PITCHAPI_KEY_REQUIRED',
              'no_requests_performed':True,'no_real_api_data_verified':True,
              'setup':'Add PITCHAPI_API_KEY to private repository Actions secrets, then rerun.'},indent=2)+'\n')
        print('PITCHAPI_KEY_REQUIRED: no authenticated request or real data extraction was performed')
        return 0
    try:
        client=Client(key,budget=a.max_requests,pause=a.pause)
        official_ref=json.loads(a.official_reference.read_text('utf-8')) if a.official_reference else None
        out=pilot(client,day=a.date,home=a.home,away=a.away,
                  league_name=a.league_name,country=a.country_code,
                  seasons=tuple(a.seasons),official_reference=official_ref)
        a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
        print(out['status']+': matches='+str(out['matches_with_verified_shots_and_xg'])+
              ' pitchapi_team_xg_internal_compared='+str(out['pitchapi_team_xg_internal_compared'])+
              ' external_official_pending='+str(out['quality_audit']['official_pending_count'])+
              ' quarantined='+str(len(out['quarantined_xg_discrepancies']))+
              ' requests='+str(out['api_requests'])+' NO GTS SCORE / MODEL MODIFICATION')
        return 2 if out['quality_audit']['hard_discrepancy'] else 0
    except (ProbeError,OSError,ValueError) as exc:
        a.output.write_text(json.dumps({'status':'PITCHAPI_SOURCE_PILOT_ABSTAIN',
              'reason':str(exc),'no_gts_score_produced':True},indent=2)+'\n')
        print('PITCHAPI_SOURCE_PILOT_ABSTAIN: '+str(exc),file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
