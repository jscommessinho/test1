#!/usr/bin/env python3
"""BETPILOT: deterministic CSV vs user-authorized web fixture snapshot parity.

Offline only. This code is NOT a FootyStats HTML fetcher: acquisition from a
browser snapshot is a separate, unverified step. No GA2 engine changes.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

class SnapshotError(ValueError):
    pass

SCHEMA_FIELDS = ('date','home','away','home_goals','away_goals')


def _int(x,label):
    if isinstance(x,bool) or not re.fullmatch(r'\d{1,2}',str(x)):
        raise SnapshotError('Invalid '+label)
    n=int(x)
    if n>30:raise SnapshotError('Out of bounds '+label)
    return n


def _team(x):
    if not isinstance(x,str) or not x.strip() or len(x)>120:
        raise SnapshotError('Invalid team name')
    return ' '.join(x.split())


def compare(csv_path,snapshot_path, *, expected_league='italy-serie-a', expected_season='2026/2027', day_tolerance=1):
    csv_path=Path(csv_path);snapshot_path=Path(snapshot_path)
    if day_tolerance not in (0,1):raise SnapshotError('Date tolerance must be zero or one')
    snap=json.loads(snapshot_path.read_text('utf-8'))
    if (snap.get('competition_key'),snap.get('season'))!=(expected_league,expected_season):
        raise SnapshotError('League and season mismatch')
    if snap.get('source_url')!='https://footystats.org/italy/serie-a/fixtures':
        raise SnapshotError('Unexpected source page URL')
    if snap.get('acquisition_method')!='HUMAN_REVIEW_OF_WEB_RENDERED_TEXT':
        raise SnapshotError('Snapshot provenance not explicit')
    observations=snap.get('fixtures')
    if not isinstance(observations,list) or not observations:raise SnapshotError('Snapshot has no fixtures')
    extracted={}
    for item in observations:
        if not isinstance(item,dict) or not all(x in item for x in SCHEMA_FIELDS):
            raise SnapshotError('Web snapshot row missing required field')
        home,away=_team(item['home']),_team(item['away'])
        if home==away:raise SnapshotError('Identical home/away')
        key=(home,away)
        if key in extracted:raise SnapshotError('Duplicate web result')
        try:d=date.fromisoformat(item['date'])
        except (ValueError,TypeError) as e:raise SnapshotError('Invalid web match date') from e
        h,a=_int(item['home_goals'],'home_goals'),_int(item['away_goals'],'away_goals')
        extracted[key]=(d,h,a)
    actual={};total=0;incomplete=0
    with csv_path.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        columns=set(reader.fieldnames or [])
        required={'status','home_team_name','away_team_name','home_team_goal_count','away_team_goal_count','timestamp'}
        if not required.issubset(columns):raise SnapshotError('CSV missing required columns')
        for row in reader:
            total+=1
            if row['status'].strip().lower()!='complete':
                incomplete+=1;continue
            home,away=_team(row['home_team_name']),_team(row['away_team_name'])
            key=(home,away)
            if key in actual:raise SnapshotError('Duplicate completed CSV fixture')
            try:
                stamp=datetime.fromtimestamp(int(row['timestamp']),timezone.utc).date()
            except (TypeError,ValueError,OverflowError) as e:raise SnapshotError('Invalid CSV date') from e
            actual[key]=(stamp,_int(row['home_team_goal_count'],'CSV home goals'),_int(row['away_team_goal_count'],'CSV away goals'))
    missing=sorted(actual.keys()-extracted.keys())
    surplus=sorted(extracted.keys()-actual.keys())
    scores=[];dates=[]
    for key in actual.keys() & extracted.keys():
        cd,ch,ca=actual[key];wd,wh,wa=extracted[key]
        if (ch,ca)!=(wh,wa):scores.append({'home':key[0],'away':key[1],'csv':[ch,ca],'web':[wh,wa]})
        if abs((cd-wd).days)>day_tolerance:dates.append({'home':key[0],'away':key[1],'csv':cd.isoformat(),'web':wd.isoformat()})
    ok=not (missing or surplus or scores or dates) and len(extracted)==len(actual)
    result={
        'status':'PARITY_VERIFIED' if ok else 'PARITY_FAILED',
        'research_only':True,
        'fixture_page_download_automated':False,
        'source_url':snap['source_url'],'acquisition_method':snap['acquisition_method'],
        'csv_sha256':hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        'snapshot_sha256':hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
        'league':expected_league,'season':expected_season,
        'scheduled_csv_rows':total,'complete_csv_rows':len(actual),'incomplete_csv_rows':incomplete,
        'web_result_rows':len(extracted),'matched_exact_score_and_near_date':len((actual.keys() & extracted.keys()) - {(x['home'],x['away']) for x in scores+dates}),
        'missing_in_web':[{'home':a,'away':b} for a,b in missing],
        'missing_in_csv':[{'home':a,'away':b} for a,b in surplus],
        'score_mismatches':scores,'date_mismatches':dates,
        'date_tolerance_days':day_tolerance,
        'important_note':'Offline research comparison of manually reviewed web page text versus original CSV. Does not demonstrate GitHub HTML access, page parser completeness for future seasons, or predictive validity.'
    }
    return result

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--csv',type=Path,required=True)
    p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--audit',type=Path,required=True)
    args=p.parse_args(argv)
    try:
        result=compare(args.csv,args.snapshot)
        args.audit.parent.mkdir(parents=True,exist_ok=True)
        args.audit.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
        print(result['status'],'matched',result['matched_exact_score_and_near_date'],
              'of',result['complete_csv_rows'],'(snapshot',result['web_result_rows'],')')
        return 0 if result['status']=='PARITY_VERIFIED' else 2
    except (SnapshotError,ValueError,OSError) as e:
        print('PARITY_ABSTAIN:',str(e));return 2

if __name__=='__main__':raise SystemExit(main())
