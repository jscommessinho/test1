#!/usr/bin/env python3
"""Experimental provider-homogeneous ECDF reference for frozen GTS-AVAILABLE weights.

This builds research reference *diagnostics* from PitchAPI-only historical
aggregate observations. It does not replace StatsBomb frozen reference or
claim a calibrated probability. Train/test split strictly by season; no
future matches enter the training ECDF or any historical feature window.
"""
from __future__ import annotations
import argparse
import bisect
import json
import math
from collections import Counter
from datetime import date,timedelta
from pathlib import Path
from betpilot.pitchapi_history_archive import load_archive, save, sha256

VERSION='GTS-PITCHAPI-AVAILABLE-ECDF-RESEARCH-v0'
WEIGHTS=(25/85,25/85,15/85,10/85,10/85)
COMPONENTS=('O','D','H','Q','F')
MIN_REFERENCE_ROWS=200
MIN_HOLDOUT_ROWS=30

class ReferenceError(ValueError):pass

def match_rows(archive,include_warnings_research=False):
    rows=[]
    originals=list(archive['records'].values())
    warnings=list(archive.get('warnings',{}).values()) if include_warnings_research else []
    for rec in originals+warnings:
        if rec.get('qa_internal') not in (('PASS','WARNING_GT_0_50') if include_warnings_research else ('PASS',)):
            continue
        a=rec['home'];b=rec['away']
        for team,opp,venue in ((a,b,'home'),(b,a,'away')):
            rows.append(dict(match_id=rec['match_id'], date=rec['date'],
                season=rec['season'],venue=venue,team=team['name'],
                team_id=team['id'],opponent_id=opp['id'],goals=team['goals'],
                xg=team['xg'],shots=team['shots']))
    return sorted(rows,key=lambda r:(r['date'],r['match_id'],r['venue']))

def raw_features(rows):
    # Matches original GTS_AVAILABLE core.features raw(O,D,H,Q,F) equations.
    # All history must be >=2 days older than the evaluated fixture.
    rows=sorted(rows,key=lambda r:(r['date'],r['match_id'],r['venue']))
    by={(r['match_id'],r['team_id']):r for r in rows}
    if len(by)!=len(rows):raise ReferenceError('Duplicate team-match evidence')
    out=[]
    indexed_dates=[date.fromisoformat(r['date']) for r in rows]
    for i,r in enumerate(rows):
        target_day=indexed_dates[i]
        hist=[a for a,day in zip(rows,indexed_dates) if day+timedelta(days=2)<target_day]
        if not hist:continue
        own=[a for a in hist if a['team_id']==r['team_id']]
        opp=[a for a in hist if a['team_id']==r['opponent_id']]
        vown=[a for a in own if a['venue']==r['venue']]
        vopp=[a for a in opp if a['venue']!=r['venue']]
        if min(len(own),len(opp))<10 or min(len(vown),len(vopp))<4:continue
        mu=sum(a['xg'] for a in hist)/len(hist)
        total_shots=sum(a['shots'] for a in hist)
        if total_shots<=0:continue
        q0=sum(a['xg'] for a in hist)/total_shots
        def rate(rr,conceded=False,prior=mu):
            if conceded:
                values=[by[(a['match_id'],a['opponent_id'])]['xg'] for a in rr]
            else:
                values=[a['xg'] for a in rr]
            return (sum(values)+5*prior)/(len(rr)+5)
        ownw=own[-10:];oppw=opp[-10:]
        O=rate(ownw)
        D=rate(oppw,conceded=True)
        H=(rate(vown[-10:],prior=O)+rate(vopp[-10:],conceded=True,prior=D))/2
        Q=(sum(a['xg'] for a in ownw)+20*q0)/(sum(a['shots'] for a in ownw)+20)
        F=rate(own[-5:])-O
        values=[O,D,H,Q,F]
        if any(not math.isfinite(v) for v in values):raise ReferenceError('Nonfinite component')
        out.append({'match_id':r['match_id'],'date':r['date'],'season':r['season'],
                    'team_id':r['team_id'],'venue':r['venue'],
                    'raw':values,'goals_observed':r['goals'],'xg_observed':r['xg'],
                    'max_input_date':max(a['date'] for a in hist),
                    'n_team':len(own),'n_opponent':len(opp),
                    'n_team_venue':len(vown),'n_opponent_venue':len(vopp)})
    return out

def ecdf_midrank(x,sorted_reference):
    n=len(sorted_reference)
    if not n:raise ReferenceError('Empty ECDF reference')
    return (bisect.bisect_left(sorted_reference,x)+bisect.bisect_right(sorted_reference,x))/(2*n)

def _ranks(values):
    pairs=sorted((float(v),i) for i,v in enumerate(values))
    out=[0.0]*len(values)
    pos=0
    while pos<len(pairs):
        stop=pos+1
        while stop<len(pairs) and pairs[stop][0]==pairs[pos][0]:stop+=1
        mid=(pos+1+stop)/2
        for k in range(pos,stop):out[pairs[k][1]]=mid
        pos=stop
    return out

def _pearson(a,b):
    if len(a)!=len(b) or len(a)<3:return None
    mx=sum(a)/len(a);my=sum(b)/len(b)
    cov=sum((x-mx)*(y-my) for x,y in zip(a,b))
    dx=sum((x-mx)**2 for x in a);dy=sum((y-my)**2 for y in b)
    if dx<=0 or dy<=0:return None
    return cov/(dx*dy)**.5

def spearman(a,b):
    return _pearson(_ranks(a),_ranks(b))

def holdout_diagnostics(training_scores,test_scores,test_rows,baseline_scores):
    # Purely descriptive; not odds, not calibrated scoring probabilities.
    cutoffs=[]
    t=sorted(training_scores)
    for fraction in (.2,.4,.6,.8):
        cutoffs.append(t[min(len(t)-1,int(len(t)*fraction))])
    bins=[]
    for i in range(5):
        idx=[k for k,z in enumerate(test_scores) if bisect.bisect_right(cutoffs,z)==i]
        bins.append({'quintile':i+1,'n':len(idx),
            'mean_goals':sum(test_rows[k]['goals_observed'] for k in idx)/len(idx) if idx else None,
            'mean_xg':sum(test_rows[k]['xg_observed'] for k in idx)/len(idx) if idx else None})
    return {'holdout_sample':len(test_rows),'quintiles_defined_from':'TRAIN_ONLY',
        'holdout_score_vs_goals_spearman':spearman(test_scores,[r['goals_observed'] for r in test_rows]),
        'holdout_score_vs_xg_spearman':spearman(test_scores,[r['xg_observed'] for r in test_rows]),
        'holdout_single_O_vs_goals_spearman':spearman(baseline_scores,[r['goals_observed'] for r in test_rows]),
        'goal_xg_by_train_score_quintile':bins,
        'warning':'Exploratory correlation, not confidence interval, accuracy certification or proven incremental lift'}

def build_reference(archive,train_seasons=('2025/2026',),test_seasons=('2026/2027',),include_warnings_research=False):
    rows=raw_features(match_rows(archive,include_warnings_research=include_warnings_research))
    train=[r for r in rows if r['season'] in train_seasons]
    test=[r for r in rows if r['season'] in test_seasons]
    if not train:return {'status':'PITCHAPI_ECDF_REFERENCE_NOT_READY','why':'No eligible training observations',
                         'train_rows':0,'holdout_rows':len(test),'archive_matches':len(archive['records'])}
    train_last=max(r['date'] for r in train)
    if test and min(r['date'] for r in test)<=train_last:
        raise ReferenceError('Train/holdout seasons overlap chronologically: leakage risk')
    if len(train)<MIN_REFERENCE_ROWS or len(test)<MIN_HOLDOUT_ROWS:
        return {'status':'PITCHAPI_ECDF_REFERENCE_NOT_READY',
                'why':f'Need {MIN_REFERENCE_ROWS} training team-match rows and {MIN_HOLDOUT_ROWS} chronological holdout rows',
                'train_rows':len(train),'holdout_rows':len(test),
                'archive_matches':len(archive['records']),'train_last_date':train_last,
                'train_seasons':list(train_seasons),'holdout_seasons':list(test_seasons)}
    distributions=[sorted(r['raw'][i] for r in train) for i in range(5)]
    def score(r):
        z=[ecdf_midrank(r['raw'][i],distributions[i]) for i in range(5)]
        return sum(a*b for a,b in zip(z,WEIGHTS))
    train_scores=[score(r) for r in train]
    test_scores=[score(r) for r in test]
    diag=holdout_diagnostics(train_scores,test_scores,test,
        [ecdf_midrank(r['raw'][0],distributions[0]) for r in test])
    audit={ 'source':'PitchAPI match-level shot xG/shot counts, internal quality gate 0.50',
        'source_provider':'PitchAPI','score_version':VERSION,
        'weight_source':'Frozen GTS-AVAILABLE-ECDF-v1 25:25:15:10:10, NO RETRAINING',
        'qa_discrepancy_scenario':('RESEARCH_INCLUDE_WARN_GT_0_50' if include_warnings_research else 'STRICT_INTERNAL_PASS_ONLY'),
        'warning_records_in_archive':len(archive.get('warnings',{})),
        'weights':list(WEIGHTS),'components':list(COMPONENTS),
        'ecdf_built_on':'TRAIN_ONLY','train_seasons':list(train_seasons),
        'holdout_seasons':list(test_seasons),'train_rows':len(train),
        'holdout_rows':len(test),'archive_matches':len(archive['records']),
        'train_last_date':train_last,'holdout_first_date':min(r['date'] for r in test),
        'training_component_ecdfs':distributions,
        'archive_identity':archive['league'],
        'archive_records_sha256':sha256(archive['records']),
        'point_in_time_original_publication_verified':False,
        'external_official_league_xg_verified':False,
        'gts_2_0_frozen_modified':False,
        'operational_eligible':False,
        'scores_are_betting_probabilities':False,
        'forecast_accuracies_proven':False,
        'holdout_evaluation_descriptive':diag,
        'test_score_summary':{'n':len(test_scores),'min':min(test_scores),
            'max':max(test_scores),'mean':sum(test_scores)/len(test_scores)},
        'train_score_summary':{'n':len(train_scores),'min':min(train_scores),
            'max':max(train_scores),'mean':sum(train_scores)/len(train_scores)},
        'holdout_rows_scored':[{'match_id':r['match_id'],'team_id':r['team_id'],
            'date':r['date'],'venue':r['venue'],'score_0_100_research_only':100*score(r),
            'goals_observed':r['goals_observed'],'xg_observed':r['xg_observed'],
            'max_input_date':r['max_input_date']} for r in test]
    }
    return {'status':'PITCHAPI_ECDF_REFERENCE_BUILT_RESEARCH_UNVALIDATED',**audit}

def main(argv=None):
    p=argparse.ArgumentParser(description='Time-separated PitchAPI source-homogeneous GTS ECDF reference diagnostics')
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--train-seasons',nargs='+',default=['2025/2026'])
    p.add_argument('--test-seasons',nargs='+',default=['2026/2027'])
    p.add_argument('--include-warnings-research',action='store_true',help='Comparative research only: may include flagged source records without certifying their QA')
    a=p.parse_args(argv)
    doc=json.loads(a.archive.read_text('utf-8'))
    if doc.get('schema')!='BETPILOT-PITCHAPI-HISTORY-ARCHIVE-RESEARCH-v1':
        raise SystemExit('Invalid GTS source archive schema')
    doc=load_archive(a.archive,doc['league'],doc['as_of_utc_date'])
    out=build_reference(doc,tuple(a.train_seasons),tuple(a.test_seasons),include_warnings_research=a.include_warnings_research)
    save(a.output,out)
    print('PITCHAPI_GTS_REFERENCE '+json.dumps({k:v for k,v in out.items() if k in ('status','train_rows','holdout_rows','archive_matches','why','train_last_date','holdout_first_date')},ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
