#!/usr/bin/env python3
"""BETPILOT Step 4 — API-FOOTBALL Bet365 prematch data adapter.

The adapter is OUTSIDE the frozen RC15/MA2 engines. No API secrets saved.
Modes:
  live: GET /fixtures?id=... and GET /odds?fixture=...&bookmaker=8;
        optionally date/teams discovery with limited date polling.
  offline: validate provided raw API responses, label unverified capture.

A native BetAnalyzer run can be requested after writing the RC15-compatible JSON.
Not a GA2 training/prediction pipeline and not a validated betting system.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from integrate_native import check_payload, run_full

BASE_URL = 'https://v3.football.api-sports.io'
BOOKMAKER_ID = 8
REQUIRED_BET_IDS = (1, 5, 8)
FIELD_SPECS = {
    1: {'Home': 'q1', 'Draw': 'qx', 'Away': 'q2'},
    5: {'Over 2.5': 'qo', 'Under 2.5': 'qu',
        'Over 1.5': 'over15', 'Under 1.5': 'under15',
        'Over 3.5': 'over35', 'Under 3.5': 'under35'},
    8: {'Yes': 'qgg', 'No': 'qng'},
}
REQUIRED_FIELDS = ('q1','qx','q2','qo','qu','qgg','qng')


class AcquisitionError(ValueError):
    pass


def aware_utc(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError('Timezone mandatory')
        return parsed.astimezone(timezone.utc)
    except ValueError as ex:
        raise AcquisitionError(f'Invalid timezone-aware date/time: {value!r}') from ex


def z(t):
    return aware_utc(t.isoformat()).isoformat(timespec='seconds')


def norm(s):
    value = unicodedata.normalize('NFKD', str(s).casefold())
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+', value))


def hash_object(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class FootballClient:
    def __init__(self, api_key, max_requests=10, timeout=15):
        if not api_key or not api_key.strip():
            raise AcquisitionError('APISPORTS_KEY environment variable missing')
        self.key = api_key.strip()
        self.max_requests = max_requests
        self.count = 0
        self.timeout = timeout

    def get(self, path, **query):
        if path not in ('/fixtures','/odds'):
            raise AcquisitionError('Blocked endpoint')
        if self.count >= self.max_requests:
            raise AcquisitionError('Per-run request cap reached')
        req = Request(BASE_URL+path+'?'+urlencode(query), headers={
            'x-apisports-key': self.key,
            'Accept':'application/json',
            'User-Agent':'BETPILOT-Research-Adapter/0.1'
        })
        self.count += 1
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw=resp.read(12_000_000)
                if resp.status != 200:
                    raise AcquisitionError(f'Provider status HTTP {resp.status}')
                obj=json.loads(raw)
        except HTTPError as ex:
            raise AcquisitionError(f'Provider returned HTTP {ex.code}; check quota/plan (no API key logged)') from ex
        except (URLError, TimeoutError, ValueError) as ex:
            raise AcquisitionError(f'Provider request failed ({type(ex).__name__}); no key logged') from ex
        if obj.get('errors'):
            raise AcquisitionError('Provider errors: '+json.dumps(obj['errors'], ensure_ascii=False)[:700])
        return obj


def identify_fixture(response, fixture_id, now):
    candidates = response.get('response', [])
    if len(candidates) != 1 or int(candidates[0].get('fixture',{}).get('id',-1)) != int(fixture_id):
        raise AcquisitionError('Fixture ID not found uniquely')
    fixture = candidates[0]
    f=fixture['fixture']
    kickoff=aware_utc(f['date'])
    if kickoff <= now:
        raise AcquisitionError('Fixture already kicked off; live prematch analysis not allowed')
    if f.get('status',{}).get('short') not in ('NS','TBD'):
        raise AcquisitionError('Fixture no longer in prematch status')
    home=fixture['teams']['home']['name']
    away=fixture['teams']['away']['name']
    if norm(home)==norm(away):
        raise AcquisitionError('Home and away teams equal')
    league=fixture['league']
    competition=f"{league.get('country','')} {league.get('name','')} {league.get('season','')}".strip()
    return {
        'id':int(fixture_id), 'home':home,'away':away,
        'competition':competition, 'kickoff':z(kickoff),
        'league_id':league.get('id'), 'team_home_id':fixture['teams']['home'].get('id'),
        'team_away_id':fixture['teams']['away'].get('id')
    }


def discover_fixture(client, home, away, start_date, num_days, now, league_id=None):
    if num_days < 1 or num_days > 7:
        raise AcquisitionError('Date search window 1..7 days')
    candidates=[]
    queries=[]
    for day in range(num_days):
        date=(start_date+timedelta(days=day)).isoformat()
        obj=client.get('/fixtures', date=date)
        queries.append(obj)
        for item in obj.get('response',[]):
            try:
                fi=item['fixture']; ho=item['teams']['home']['name']; aw=item['teams']['away']['name']
                kick=aware_utc(fi['date'])
                if norm(ho)!=norm(home) or norm(aw)!=norm(away):continue
                if league_id is not None and int(item['league']['id'])!=int(league_id):continue
                if kick<=now or fi.get('status',{}).get('short') not in ('NS','TBD'):continue
                candidates.append((kick,int(fi['id'])))
            except (KeyError, ValueError, TypeError):
                continue
    if len(candidates)!=1:
        raise AcquisitionError('Fixture identity not unique: '+str(len(candidates))+' matches. Use --fixture-id.')
    return candidates[0][1], queries


def extract_odds(response, fixture_id, now):
    items=[]
    for match in response.get('response',[]):
        if int(match.get('fixture',{}).get('id',-1)) != int(fixture_id): continue
        for book in match.get('bookmakers',[]):
            if int(book.get('id',-1)) != BOOKMAKER_ID or norm(book.get('name',''))!='bet365': continue
            time=aware_utc(match['update'])
            if time>now:
                raise AcquisitionError('Provider quote-update is in the future relative to capture')
            values={}
            for bet in book.get('bets',[]):
                try: bet_id=int(bet.get('id',-1))
                except (ValueError, TypeError):continue
                if bet_id not in FIELD_SPECS:continue
                for v in bet.get('values',[]):
                    name=v.get('value')
                    if name not in FIELD_SPECS[bet_id]:continue
                    field=FIELD_SPECS[bet_id][name]
                    try: price=float(v['odd'])
                    except (ValueError, TypeError, KeyError):raise AcquisitionError('Unparseable odds: '+field)
                    if not 1<price<100000:raise AcquisitionError('Invalid odds: '+field)
                    if field in values and values[field]!=price:
                        raise AcquisitionError('Conflicting prices in same market: '+field)
                    values[field]=price
            if not all(k in values for k in REQUIRED_FIELDS):
                raise AcquisitionError('Incomplete RC15 seven-price set; missing: '+','.join(k for k in REQUIRED_FIELDS if k not in values))
            items.append((time, values))
    if not items:
        raise AcquisitionError('No Bet365 odds found for fixture ID')
    unique={(a.isoformat(),json.dumps(v,sort_keys=True)) for a,v in items}
    if len(unique)>1:
        raise AcquisitionError('Multiple discordant Bet365 snapshots; select a snapshot explicitly')
    return items[0]


def build_input(fixture, odds, update, capture, fixture_json, odds_json, *, live=False):
    if aware_utc(fixture['kickoff'])<=capture:
        raise AcquisitionError('Kickoff already passed: cannot claim live prematch input')
    if update>capture or update>=aware_utc(fixture['kickoff']):
        raise AcquisitionError('Quote update after capture or kickoff')
    fid=fixture['id']
    f_hash=hash_object(fixture_json);o_hash=hash_object(odds_json)
    provenance = 'VERIFIED_EXTERNAL_SNAPSHOT' if live else 'USER_SUPPLIED_NOT_TIMESTAMP_VERIFIED'
    return {
        'match':{
            'event_id':f'API-FOOTBALL-fixture-{fid}',
            'home':fixture['home'], 'away':fixture['away'],
            'competition':fixture['competition'], 'kickoff':fixture['kickoff'],
            'cutoff':z(capture if live else update),
        },
        'odds_snapshot':{
            'bookmaker':'Bet365', 'timestamp':z(update),
            'provenance':provenance,
            'snapshot_id':f'APIFOOTBALL-{fid}-{update.strftime("%Y%m%dT%H%M%SZ")}',
            'external_capture_reference':(
                f'Authenticated API-FOOTBALL via x-apisports-key; capture={z(capture)}; '
                f'fixtures_sha256={f_hash}; odds_sha256={o_hash}; NOT direct Bet365 timestamp verification'
                if live else 'User-provided API-Football response; original capture timing unverified'
            )
        }, 'odds':odds
    }


def acquire(fixture_id=None, home=None, away=None, date=None, horizon_days=1,
            league_id=None, *, client=None, fixture_response=None, odds_response=None, now=None):
    now=now or datetime.now(timezone.utc)
    now=aware_utc(now.isoformat())
    offline=fixture_response is not None and odds_response is not None
    if not offline and client is None:
        raise AcquisitionError('Live client required without raw fixtures/odds JSON')
    if fixture_id is None:
        if not home or not away or not date: raise AcquisitionError('Use --fixture-id or --home, --away, --date')
        if offline: raise AcquisitionError('Offline fixture resolution requires --fixture-id')
        fixture_id, _ = discover_fixture(client, home, away, date, horizon_days, now, league_id)
    fixture_response=fixture_response if offline else client.get('/fixtures', id=int(fixture_id))
    fixture=identify_fixture(fixture_response, fixture_id, now)
    if home and norm(fixture['home'])!=norm(home):raise AcquisitionError('Requested home does not match fixture')
    if away and norm(fixture['away'])!=norm(away):raise AcquisitionError('Requested away does not match fixture')
    if league_id is not None and fixture['league_id']!=int(league_id):raise AcquisitionError('League mismatch')
    odds_response=odds_response if offline else client.get('/odds', fixture=int(fixture_id), bookmaker=BOOKMAKER_ID)
    update,odds=extract_odds(odds_response, fixture_id, now)
    payload=build_input(fixture, odds, update, now, fixture_response, odds_response, live=not offline)
    check_payload(payload)
    return payload,{'fixture_response':fixture_response,'odds_response':odds_response,
                    'capture_time_utc':z(now),'provider_update_utc':z(update),
                    'quotes_directly_from_bet365_verified':False,
                    'capture_provenance': 'AUTHENTICATED_PROVIDER_FETCH' if not offline else 'OFFLINE_USER_PROVIDED',
                    'api_requests':(getattr(client, 'used', getattr(client, 'count', 0)) if client is not None else 0)}


def cli(argv=None):
    p=argparse.ArgumentParser(description='API-FOOTBALL Bet365 adapter → unchanged RC15/MA2 BetAnalyzer')
    p.add_argument('--fixture-id',type=int)
    p.add_argument('--home')
    p.add_argument('--away')
    p.add_argument('--date',help='YYYY-MM-DD date to search fixtures, UTC by default')
    p.add_argument('--horizon-days',type=int,default=1,help='Maximum 7; costs one /fixtures call per day')
    p.add_argument('--league-id',type=int)
    p.add_argument('--fixtures-json',type=Path,help='Offline raw GET /fixtures response')
    p.add_argument('--odds-json',type=Path,help='Offline raw GET /odds response')
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--run-native',action='store_true',help='Run original RC15 → MA2 using input')
    p.add_argument('--max-requests',type=int,default=10)
    args=p.parse_args(argv)
    offline=args.fixtures_json is not None or args.odds_json is not None
    if offline and not (args.fixtures_json and args.odds_json): p.error('Both --fixtures-json and --odds-json required offline')
    try:
        day=datetime.strptime(args.date,'%Y-%m-%d').date() if args.date else None
        client=None if offline else FootballClient(os.environ.get('APISPORTS_KEY',''),max_requests=args.max_requests)
        payload,raw=acquire(fixture_id=args.fixture_id,home=args.home,away=args.away,date=day,
                            horizon_days=args.horizon_days,league_id=args.league_id,client=client,
                            fixture_response=json.loads(args.fixtures_json.read_text()) if offline else None,
                            odds_response=json.loads(args.odds_json.read_text()) if offline else None)
        out=args.output_dir
        out.mkdir(parents=True,exist_ok=True)
        (out/'RC15_EVENT_INPUT.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
        (out/'PROVIDER_CAPTURE.json').write_text(json.dumps(raw,indent=2,ensure_ascii=False)+'\n')
        result={'fixture_id':payload['match']['event_id'],'home':payload['match']['home'],
                'away':payload['match']['away'],'seven_odds_complete':True,
                'capture':raw['capture_provenance'],'requests':raw['api_requests'],'native_run':'SKIPPED'}
        if args.run_native:
            run=run_full(payload,out/'native_run')
            result['native_run']=run['status']
        print(json.dumps(result,indent=2,ensure_ascii=False))
    except (AcquisitionError, OSError, ValueError, KeyError) as e:
        print('ACQUISITION FAILED: '+str(e),file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':sys.exit(cli())
