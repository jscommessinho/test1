#!/usr/bin/env python3
"""BETPILOT | three Premium historical seasons + public current-season FT rows.

Research-only. Raw Premium CSVs never changed. No API-Football requests.
Upstream open-matchday: https://github.com/oladeeayo/open-matchday
For informational, personal, non-commercial use with attribution. Provider
rights and source quality remain separate; use with audit and validation.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from betpilot.footystats_premium_csv import convert, read_csv, stable_id, parse_manifest, ImportErrorFS

ROOT_URL='https://raw.githubusercontent.com/oladeeayo/open-matchday/master/data/ITA_1_serie-a/results_2627.csv'
LEAGUE_KEY='italy-serie-a'
SEASON='2026/2027'
ALIASES={'Milan':'AC Milan','Internazionale':'Inter Milan'}
REQUIRED={'date','home','away','ft_home','ft_away'}

class CurrentFeedError(ValueError): pass


def parse_csv(content):
    try: text=content.decode('utf-8-sig')
    except UnicodeError as e: raise CurrentFeedError('UTF-8 required') from e
    reader=csv.DictReader(io.StringIO(text,newline=''))
    if not REQUIRED.issubset(reader.fieldnames or []):
        raise CurrentFeedError('Missing required current-season columns')
    rows=[]
    for n,row in enumerate(reader,2):
        if None in row: raise CurrentFeedError(f'Malformed CSV row {n}')
        rows.append(row)
    if len(rows)>380: raise CurrentFeedError('More completed rows than schedule permits')
    return rows


def get_current_bytes():
    req=Request(ROOT_URL,headers={'Accept':'text/csv','User-Agent':'BETPILOT-PREMATCH/1.0'})
    try:
        with urlopen(req,timeout=18) as resp:
            if resp.status!=200 or resp.geturl()!=ROOT_URL:raise CurrentFeedError('Redirect or bad HTTP status')
            data=resp.read(350001)
    except (HTTPError,URLError,TimeoutError) as error:
        raise CurrentFeedError('Current result source unavailable: '+type(error).__name__) from None
    if not (100<len(data)<350001):raise CurrentFeedError('Invalid source CSV bytes')
    return data


def n(s):return ' '.join(str(s).strip().casefold().split())

def to_goal(s):
    if not isinstance(s,str) or not s.isdecimal() or not 0<=int(s)<=30:
        raise CurrentFeedError('Invalid final score')
    return int(s)


def current_records(source_bytes,now):
    if now.tzinfo is None:raise CurrentFeedError('Current time must be timezone-aware')
    now=now.astimezone(timezone.utc)
    records={}
    for r in parse_csv(source_bytes):
        home=ALIASES.get(r['home'],r['home']).strip()
        away=ALIASES.get(r['away'],r['away']).strip()
        if not home or not away or n(home)==n(away):raise CurrentFeedError('Invalid team names')
        key=(n(home),n(away))
        if key in records:raise CurrentFeedError('Duplicate home/away fixture')
        try: day=datetime.strptime(r['date'],'%Y-%m-%d').date()
        except ValueError as error:raise CurrentFeedError('Invalid fixture date') from error
        # Date-only provider; wait for following UTC date before accepting result.
        if day>=now.date():raise CurrentFeedError('Future or same-day result: postpone import')
        # Use UTC end-of-day proxy, not a falsely precise kickoff timestamp.
        timestamp=int(datetime.combine(day,datetime.max.time().replace(microsecond=0),timezone.utc).timestamp())
        home_id=stable_id(LEAGUE_KEY,home.casefold())
        away_id=stable_id(LEAGUE_KEY,away.casefold())
        mid=stable_id('match','|'.join([LEAGUE_KEY,SEASON,str(timestamp),str(home_id),str(away_id)]))
        records[key]={ 'id':mid, 'homeID':home_id, 'awayID':away_id,
                      'homeGoalCount':to_goal(r['ft_home']), 'awayGoalCount':to_goal(r['ft_away']),
                      'date_unix':timestamp,'status':'complete','season':SEASON,
                      'competition_id':stable_id('season',LEAGUE_KEY+'|'+SEASON),
                      'no_home_away':0 }
    if not records:raise CurrentFeedError('Empty current-season result source')
    return records


def validate_reference(reference_csv, current):
    reference=list(read_csv(reference_csv))
    if len(reference)!=380:raise CurrentFeedError('Reference Premium current season needs 380 scheduled fixtures')
    seen=set();verified=0
    for _,row in reference:
        h=n(row.get('home_team_name'));a=n(row.get('away_team_name'))
        k=(h,a)
        if k in seen:raise CurrentFeedError('Duplicate fixture in FootyStats reference')
        seen.add(k)
        if row['status'].strip().lower()!='complete':continue
        match=current.get(k)
        if not match:raise CurrentFeedError('Source missing a reference finished match')
        expected=(to_goal(row['home_team_goal_count']),to_goal(row['away_team_goal_count']))
        actual=(match['homeGoalCount'],match['awayGoalCount'])
        if actual!=expected:raise CurrentFeedError('Completed score discrepancy')
        reference_day=datetime.fromtimestamp(int(row['timestamp']),timezone.utc).date()
        actual_day=datetime.fromtimestamp(match['date_unix'],timezone.utc).date()
        if abs((reference_day-actual_day).days)>1:raise CurrentFeedError('Completed match date discrepancy')
        verified+=1
    for (h,a) in current:
        if (h,a) not in seen:raise CurrentFeedError('Source result absent from reference schedule')
    return verified


def validate_previous(previous_path,new):
    if not previous_path: return 0
    p=Path(previous_path)
    if not p.is_file():return 0
    old=json.loads(p.read_text('utf-8'))
    if not isinstance(old,list):raise CurrentFeedError('Invalid previous snapshot structure')
    new_index={(x['homeID'],x['awayID']):x for x in new.values()}
    for oldmatch in old:
        k=(oldmatch['homeID'],oldmatch['awayID'])
        curr=new_index.get(k)
        if curr is None or any(oldmatch[field]!=curr[field] for field in ('homeGoalCount','awayGoalCount','date_unix')):
            raise CurrentFeedError('Current source lost or changed previously accepted results')
    return len(old)


def merge(historical_manifest,current_bytes,now=None,previous_path=None,reference_csv=None):
    now=now or datetime.now(timezone.utc)
    config=parse_manifest(historical_manifest)
    if config['league']!=LEAGUE_KEY or len(config['sources'])!=3 or any(s==SEASON for s,_ in config['sources']):
        raise CurrentFeedError('Require exactly three historic Serie A seasons, excluding 2026/27')
    old,mapping,audit=convert(historical_manifest)
    cur=current_records(current_bytes,now)
    previous_n=validate_previous(previous_path,cur)
    verified=validate_reference(reference_csv,cur) if reference_csv else None
    old_ids={r['id'] for r in old}
    new=sorted(cur.values(),key=lambda r:(r['date_unix'],r['id']))
    if any(r['id'] in old_ids for r in new):raise CurrentFeedError('Historical record ID collision')
    combined=sorted(old+new,key=lambda r:(r['date_unix'],r['id']))
    if len({r['id'] for r in combined})!=len(combined):raise CurrentFeedError('Duplicated record ID')
    unique_teams={r['homeID'] for r in combined}|{r['awayID'] for r in combined}
    report={'status':'CURRENT_RESULTS_VERIFIED_RESEARCH_ONLY','competition':'Italy Serie A',
            'season':SEASON,'historical_footystats_n':len(old),
            'current_open_matchday_n':len(new),'ga2_history_n':len(combined),
            'unique_teams_all_seasons':len(unique_teams),
            'reference_footystats_matches_verified':verified,
            'unchanged_prior_results_verified':previous_n,
            'upstream_sha256':hashlib.sha256(current_bytes).hexdigest(),
            'source':'open-matchday https://github.com/oladeeayo/open-matchday',
            'footystats_snapshots_sha256':[s['sha256'] for s in audit['sources']],
            'source_data_usage':'Private personal, informational, non-commercial with attribution',
            'date_precision':'DATE_ONLY_UTC_END_OF_DAY_PROXY',
            'score_change_policy':'FAIL_CLOSED',
            'point_in_time_verification':False,
            'ga2_retrained':False,
            'team_aliases_for_source':ALIASES,
            'disclaimer':'GA2 independent model is unchanged. Current result source may lag or disagree. Only final goals (not odds or xG) are passed to GA2.'}
    return combined,new,report


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--historical-manifest',type=Path,required=True)
    p.add_argument('--source-csv',type=Path)
    p.add_argument('--previous-current',type=Path)
    p.add_argument('--reference-current-csv',type=Path)
    p.add_argument('--output-history',type=Path,required=True)
    p.add_argument('--output-current',type=Path,required=True)
    p.add_argument('--output-audit',type=Path,required=True)
    a=p.parse_args(argv)
    try:
        source=a.source_csv.read_bytes() if a.source_csv else get_current_bytes()
        combined,current,audit=merge(a.historical_manifest,source,previous_path=a.previous_current,reference_csv=a.reference_current_csv)
        for path,data in ((a.output_history,combined),(a.output_current,current),(a.output_audit,audit)):
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True)+'\n','utf-8')
        print('CURRENT_RESULTS_OK:',audit['historical_footystats_n'],'historic +',audit['current_open_matchday_n'],
              'current =',audit['ga2_history_n'],'| reference',audit['reference_footystats_matches_verified'])
        return 0
    except (CurrentFeedError,ImportErrorFS,OSError,ValueError) as error:
        print('ABSTAIN_CURRENT_RESULTS:',error,file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
