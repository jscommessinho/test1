#!/usr/bin/env python3
"""BETPILOT PitchAPI GTS historical feature warehouse, conservative incremental pilot.

Source data are match-level *aggregate* observations (not per-shot events),
private, never presented as league-official verified, and not a GTS score.
Each batch has a strict request budget and provider-ID checks. Frozen engines
are unmodified. In production, the ZIP artifact contains an archived snapshot;
GitHub Actions cache can restore prior checkpoints but is NOT durable storage.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from betpilot.pitchapi_gts_probe import Client, ProbeError, parse_shots, parse_match_stats, ident
from betpilot.gts_pitchapi_quality import assess_pitchapi_match, POLICY_VERSION

VERSION = 'BETPILOT-PITCHAPI-HISTORY-ARCHIVE-RESEARCH-v1'
MAX_BATCH = 120
MAX_REQUESTS = 350
LEAGUE_RE = re.compile(r'^[a-z]{3}-[a-z0-9-]+$')
SEASON_RE = re.compile(r'^20\d\d(?:/20\d\d)?$')

class HistoryError(ValueError): pass

def canonical(obj):
    return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def sha256(obj):
    return hashlib.sha256(canonical(obj).encode('utf-8')).hexdigest()

def save(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False,prefix='.gts-',suffix='.json') as f:
        json.dump(obj,f,indent=2,ensure_ascii=False,allow_nan=False)
        f.write('\n');tmp=Path(f.name)
    tmp.replace(path)

def load_archive(path,league,as_of):
    path=Path(path)
    if not path.is_file():
        return {'schema':VERSION,'provider':'PitchAPI','league':league,'as_of_utc_date':as_of,
                'measurement':'FT xG from shot expected_goals, all periods, penalties included',
                'source_qa':POLICY_VERSION,'external_official_xg_verification':'NOT_PERFORMED',
                'records':{},'rejected':{},'warnings':{},'runs':[]}
    o=json.loads(path.read_text('utf-8'))
    if o.get('schema')!=VERSION or o.get('provider')!='PitchAPI' or o.get('league')!=league or o.get('as_of_utc_date')!=as_of:
        raise HistoryError('Archive identity/schema/as-of mismatch; refusing silent cross-league or time mixing')
    if not isinstance(o.get('records'),dict) or not isinstance(o.get('rejected'),dict):
        raise HistoryError('Archive records not a mapping')
    o.setdefault('warnings',{})
    if not isinstance(o['warnings'],dict):raise HistoryError('Archive warnings invalid')
    if set(o['records']) & set(o['warnings']) or set(o['warnings']) & set(o['rejected']):
        raise HistoryError('Overlapping QA partition records')
    for mid,rec in o['warnings'].items():
        ident(mid,'m')
        if rec.get('match_id')!=mid or rec.get('qa_internal')!='WARNING_GT_0_50' or rec.get('source_digest')!=sha256({k:v for k,v in rec.items() if k not in ('source_digest','retrieved_at_utc')}):
            raise HistoryError('Warning aggregate integrity or QA marker invalid')
        if rec.get('league_id')!=league['id']:
            raise HistoryError('Warning contains foreign-league fixture')
        if not date.fromisoformat(rec['date'])+timedelta(days=2)<date.fromisoformat(as_of):
            raise HistoryError('Warning contains future match')
    for mid,rec in o['records'].items():
        ident(mid,'m')
        if rec.get('match_id')!=mid or rec.get('source_digest')!=sha256({k:v for k,v in rec.items() if k not in ('source_digest','retrieved_at_utc')}):
            raise HistoryError('Archive integrity digest mismatch at '+mid)
        if rec.get('league_id')!=league['id']:
            raise HistoryError('Archive has foreign-league fixture')
        if not date.fromisoformat(rec['date'])+timedelta(days=2)<date.fromisoformat(as_of):
            raise HistoryError('Archive contains future or not-yet-eligible match')
    return o

def choose_fixture_rows(lists,league,as_of):
    limit=date.fromisoformat(as_of)
    found={}
    for season,matches in lists:
        for m in matches:
            if m.get('status')!='finished':continue
            mid=ident(m['id'],'m')
            d=m.get('date')
            if not isinstance(d,str):raise HistoryError('Finished match missing date')
            if date.fromisoformat(d)+timedelta(days=2)>=limit:continue
            ht=m.get('home_team') or {};at=m.get('away_team') or {}
            ident(ht.get('id'),'t');ident(at.get('id'),'t')
            if ht['id']==at['id']:raise HistoryError('Invalid same-team fixture')
            gh=m.get('score_home');ga=m.get('score_away')
            if any(isinstance(x,bool) or not isinstance(x,int) or not 0<=x<=30 for x in (gh,ga)):
                raise HistoryError('Finished fixture lacks valid final score')
            row={'match_id':mid,'date':d,'season':season,'home':ht,'away':at,'home_goals':gh,'away_goals':ga}
            if mid in found and found[mid]!=row:
                raise HistoryError('Conflicting same-ID historical fixture across seasons')
            found[mid]=row
    return sorted(found.values(),key=lambda m:(m['date'],m['match_id']),reverse=True)

def validate_source_league(catalogue,league):
    options=[x for x in catalogue if x.get('id')==league['id'] and x.get('name')==league['name'] and x.get('country_code')==league['country']]
    if len(options)!=1:raise HistoryError('Pinned league id/name/country not found exactly once; review mapping')
    available=options[0].get('seasons')
    if not isinstance(available,list):raise HistoryError('League seasons list missing')
    return available

def make_record(client,match,league,retain_warning=False):
    original={'id':match['match_id'],'date':match['date'], 'home_team':match['home'],'away_team':match['away']}
    shot_payload=client.get('/v1/matches/'+match['match_id']+'/shots')
    counts=parse_shots(shot_payload,original)
    stats_payload=client.get('/v1/matches/'+match['match_id']+'/stats')
    own=parse_match_stats(stats_payload,original)
    ht=match['home']['id'];at=match['away']['id']
    metric={'match_id':match['match_id'],'date':match['date'],
            'home':{'id':ht,**counts[ht]},'away':{'id':at,**counts[at]},'team_stats_xg':own}
    qa=assess_pitchapi_match(metric)
    qa_label=qa['internal_pitchapi_status']
    if qa_label!='PASS' and not (retain_warning and qa_label=='REJECTED_INTERNAL_ABOVE_0_50'):
        raise HistoryError('Internal 0.50 xG quality gate failed: '+qa_label)
    # The provider's /stats response is NOT a league-official independent reference.
    out={'match_id':match['match_id'],'league_id':league['id'],'season':match['season'], 'date':match['date'],
         'home':{'id':ht,'name':match['home']['name'],'goals':match['home_goals'],
                 'xg':round(counts[ht]['xg'],6),'shots':counts[ht]['shots']},
         'away':{'id':at,'name':match['away']['name'],'goals':match['away_goals'],
                 'xg':round(counts[at]['xg'],6),'shots':counts[at]['shots']},
         'internal_absolute_xg_deltas':qa['internal_absolute_deltas'],
         'qa_internal':('PASS' if qa_label=='PASS' else 'WARNING_GT_0_50'),
         'qa_external':'OFFICIAL_REFERENCE_NOT_VERIFIED',
         'source':'PitchAPI shot-level xG and team stats; provider-internal validated only'}
    out['source_digest']=sha256(out)
    out['retrieved_at_utc']=datetime.now(timezone.utc).isoformat()
    return out

def readiness(records,as_of):
    # This checks data-availability only, not ECDF construction or predictive validity.
    teamrows=defaultdict(list)
    for r in records.values():
        teamrows[r['home']['id']].append((r,True))
        teamrows[r['away']['id']].append((r,False))
    ready=0
    for team,vals in teamrows.items():
        all_rows=sorted(vals,key=lambda x:(x[0]['date'],x[0]['match_id']),reverse=True)
        # Per team at least 10 total and at least 4 at home or away; actual matchup venue checked later.
        home=sum(h for _,h in vals);away=len(vals)-home
        if len(all_rows)>=10 and min(home,away)>=4:ready+=1
    return {'archived_records':len(records),'distinct_teams':len(teamrows),
            'teams_with_at_least_10_and_4_at_each_venue':ready,
            'benchmark_ready':False,'gts_score_ready':False,
            'why_not_ready':'Provider-homogeneous ECDF and forward validation not built; independently official league xG not certified'}

def is_retryable_provider_error(exc):
    msg=str(exc)
    return (msg.startswith('PitchAPI HTTP status 429') or
            any(msg.startswith('PitchAPI HTTP status '+n) for n in ('500','502','503','504')) or
            msg.startswith('PitchAPI transport or JSON error'))

def backfill(client,league,seasons,as_of,archive,max_new=25,checkpoint=None,retain_warning=False):
    if not 1<=max_new<=MAX_BATCH:raise HistoryError('Invalid max_new')
    if len(seasons)<1 or len(seasons)>7 or any(not SEASON_RE.fullmatch(s) for s in seasons):
        raise HistoryError('Invalid seasonal list')
    date.fromisoformat(as_of)
    available=validate_source_league(client.get('/v1/leagues').get('leagues',[]),league)
    if any(season not in available for season in seasons):raise HistoryError('Requested season missing in provider league')
    lists=[]
    for season in seasons:
        result=client.get('/v1/leagues/'+league['id']+'/matches',{'season':season,'status':'played'})
        if (result.get('league') or {}).get('id')!=league['id'] or not isinstance(result.get('matches'),list):
            raise HistoryError('Season identity/list mismatch')
        lists.append((season,result['matches']))
    allrows=choose_fixture_rows(lists,league,as_of)
    archive.setdefault('warnings',{})
    # Backward-compatible repair: old hard threshold exclusions can be retried
    # once in warn-retention mode. Never retry missing shot xG or schema failures.
    # Preserve original rejection details in a separate provenance ledger.
    recovered_ids=set()
    if retain_warning:
        archived_rows={m['match_id']:m for m in allrows}
        for mid,entry in list(archive['rejected'].items()):
            if (mid in archived_rows and isinstance(entry,dict)
                    and str(entry.get('reason','')).startswith('Internal 0.50 xG quality gate failed:')):
                archive.setdefault('reclassified_prior_qa_rejections',{})[mid]=entry
                del archive['rejected'][mid]
                recovered_ids.add(mid)
    pending=[m for m in allrows if m['match_id'] not in archive['records'] and m['match_id'] not in archive['rejected'] and m['match_id'] not in archive['warnings']]
    # Revalidate old strict-filter exclusions immediately, before new fixtures.
    pending.sort(key=lambda m:(m['match_id'] not in recovered_ids,m['date'],m['match_id']))

    taken=0
    stopped_on_retryable_error=None
    for m in pending[:max_new]:
        if client.used+2>client.budget:break
        try:
            rec=make_record(client,m,league,retain_warning=retain_warning)
            if rec['qa_internal']=='PASS':
                archive['records'][m['match_id']]=rec
            else:
                archive['warnings'][m['match_id']]=rec
        except ProbeError as err:
            if is_retryable_provider_error(err):
                stopped_on_retryable_error=str(err)[:130]
                break
            archive['rejected'][m['match_id']]={'date':m['date'],'season':m['season'],
                                                'reason':str(err)[:170],'review_required':True}
        except (HistoryError,KeyError,TypeError,ValueError) as err:
            archive['rejected'][m['match_id']]={'date':m['date'],'season':m['season'],
                                                'reason':str(err)[:170],'review_required':True}
        taken+=1
        if checkpoint is not None and taken%5==0:save(checkpoint,archive)
    archive['runs'].append({'retrieved_at_utc':datetime.now(timezone.utc).isoformat(),
        'requests_used':client.used,'new_attempts':taken,'new_accepted':sum(m['match_id'] in archive['records'] for m in pending[:taken]),
        'remaining_before_run':len(pending),'as_of_date':as_of,'maximum_attempts':max_new})
    if len(archive['runs'])>120:archive['runs']=archive['runs'][-120:]
    if checkpoint is not None:save(checkpoint,archive)
    return {'status':('PITCHAPI_HISTORY_INTERRUPTED_RETRYABLE' if stopped_on_retryable_error else 'PITCHAPI_HISTORY_INCREMENTAL_INTERNAL_QA_ONLY'),
            'retryable_provider_interruption':stopped_on_retryable_error,
            'league':league,'seasons':seasons,'eligible_listed':len(allrows),
            'new_attempts':taken,'remaining_not_attempted':len(pending)-taken,
            'warning_retained_cumulative':len(archive['warnings']),
            'prior_threshold_exclusions_retried':len(recovered_ids),
            'accepted_cumulative':len(archive['records']),'quarantined_cumulative':len(archive['rejected']),
            'requests_used':client.used,'source_quality_policy':POLICY_VERSION,
            'independent_league_official_xg_validated':False,
            'source_archive_digest':sha256({k:v for k,v in archive.items() if k!='runs'}),
            'diagnostic':readiness(archive['records'],as_of),
            'frozen_engines_modified':False,'gts_score_calculated':False}

def main(argv=None):
    p=argparse.ArgumentParser(description='Incremental PitchAPI historical xG aggregates, RESEARCH ONLY')
    p.add_argument('--league-id',default='l_4WFCIZ')
    p.add_argument('--league-name',default='Premier League')
    p.add_argument('--country',default='ENG')
    p.add_argument('--seasons',nargs='+',default=['2025/2026','2026/2027'])
    p.add_argument('--as-of',default='2026-10-31')
    p.add_argument('--max-new',type=int,default=25)
    p.add_argument('--max-requests',type=int,default=85)
    p.add_argument('--pause',type=float,default=0.2)
    p.add_argument('--retain-xg-warnings',action='store_true',help='Keep >0.50 xG source records in separate warnings partition; do NOT count as GTS quality PASS')
    p.add_argument('--archive',type=Path,default=Path('output/pitchapi_history/eng-premier_archive.json'))
    p.add_argument('--audit',type=Path,default=Path('output/pitchapi_history/audit.json'))
    args=p.parse_args(argv)
    league={'id':ident(args.league_id,'l'),'name':args.league_name,'country':args.country}
    if not (3<=args.max_requests<=MAX_REQUESTS and 1<=args.max_new<=MAX_BATCH):
        raise SystemExit('Invalid local request/sample budget')
    args.audit.parent.mkdir(parents=True,exist_ok=True)
    try:
        archive=load_archive(args.archive,league,args.as_of)
        key=os.environ.get('PITCHAPI_API_KEY','')
        if not key:raise HistoryError('PITCHAPI_API_KEY missing')
        client=Client(key,budget=args.max_requests,pause=args.pause)
        result=backfill(client,league,args.seasons,args.as_of,archive,args.max_new,checkpoint=args.archive,retain_warning=args.retain_xg_warnings)
        save(args.audit,result)
        print('BETPILOT_PITCHAPI_HISTORY '+canonical({k:v for k,v in result.items() if k not in ('diagnostic','source_archive_digest')}))
        return 0
    except (HistoryError,ProbeError,ValueError,OSError) as e:
        save(args.audit,{'status':'HISTORY_BACKFILL_ABSTAIN','reason':str(e)[:200],'gts_score_calculated':False})
        print('BETPILOT_PITCHAPI_HISTORY_ABSTAIN '+str(e)[:180])
        return 2

if __name__=='__main__':
    raise SystemExit(main())