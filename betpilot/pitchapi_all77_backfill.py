#!/usr/bin/env python3
"""Independently pinned 77-league PitchAPI incremental xG source acquisition.

Catalog snapshot dated 2026-10-31. Original GTS/GA2/RC15/MA2 remain frozen.
A single league per run; no provider key in logs/artifacts.
"""
from __future__ import annotations
import argparse
import json
import os
import re
from pathlib import Path
from betpilot.pitchapi_gts_probe import Client, ident, ProbeError
from betpilot.pitchapi_history_archive import (
    HistoryError, MAX_BATCH, load_archive, backfill, save, validate_source_league)

SCHEMA = 'BETPILOT_PITCHAPI_LEAGUE_PINNED_77_v1'
KEY_RE = re.compile(r'^(?:cat-\d{3}|[a-z]{3}-[a-z0-9-]+)$')
SEASON_RE = re.compile(r'^20\d\d(?:/20\d\d)?$')

def manifest_league(manifest, key):
    if not isinstance(manifest,dict) or manifest.get('schema')!=SCHEMA:
        raise HistoryError('Unknown PitchAPI 77 league manifest')
    if not isinstance(key,str) or not KEY_RE.fullmatch(key):
        raise HistoryError('Invalid provider league key')
    entries=manifest.get('leagues')
    if not isinstance(entries,dict) or len(entries)!=77 or key not in entries:
        raise HistoryError('Missing or incomplete 77-league pinned catalogue')
    p=entries[key]
    if not isinstance(p,dict): raise HistoryError('Invalid provider league entry')
    if not isinstance(p.get('league_name'),str) or not p['league_name']:
        raise HistoryError('Missing source league name')
    if not isinstance(p.get('country_code'),str) or not p['country_code']:
        raise HistoryError('Missing source league country')
    if not isinstance(p.get('prior26'),bool):
        raise HistoryError('Missing explicit prior registry status')
    return {'id':ident(p['league_id'],'l'),'name':p['league_name'],'country':p['country_code']}

def all_seasons(source,as_of):
    """All provider-supported seasons starting by as-of year, max 7.

    Listing seasons is not proof of actual xG coverage in those seasons.
    """
    y=int(as_of[:4]);result=[]
    if not isinstance(source,list): raise HistoryError('Seasons list unavailable')
    for s in source:
        if isinstance(s,str) and SEASON_RE.fullmatch(s) and int(s[:4])<=y:
            result.append(s)
    result=sorted(set(result),key=lambda s:(int(s[:4]),s),reverse=True)
    if len(result)>7:
        raise HistoryError('More than seven historical seasons; expand vetted max-season handling')
    return result

def run_batch(*,key,manifest,api_key,archive_path,audit_path,as_of,max_new,max_requests,pause):
    league=manifest_league(manifest,key)
    if not isinstance(api_key,str) or len(api_key)<6:
        raise HistoryError('PITCHAPI_API_KEY missing; configure in GitHub secrets')
    if not 1<=max_new<=MAX_BATCH or not 10<=max_requests<=350:
        raise HistoryError('Batch request budget invalid')
    client=Client(api_key,budget=max_requests,pause=pause)
    catalogue=client.get('/v1/leagues').get('leagues',[])
    allowed=validate_source_league(catalogue,league)
    seasons=all_seasons(allowed,as_of)
    if not seasons:
        result={'status':'NO_PUBLISHED_SEASONS_BEFORE_CUTOFF','registry_key':key,'league':league,
                'accepted_cumulative':0,'warning_retained_cumulative':0,'quarantined_cumulative':0,
                'eligible_listed':0,'new_attempts':0,'remaining_not_attempted':None,
                'requests_used':client.used,'gts_score_calculated':False}
        save(audit_path,result)
        return result
    # Catalogue is fetched once by the wrapper and again by the frozen source archive;
    # remaining budget must cover each season list and the configured new attempts.
    minimum=2+len(seasons)+2*max_new
    if max_requests<minimum:
        raise HistoryError('Request budget too small for selected seasons and batch')
    archive=load_archive(archive_path,league,as_of)
    report=backfill(client,league,seasons,as_of,archive,max_new=max_new,
                    checkpoint=archive_path,retain_warning=True)
    report['registry_key']=key
    report['catalogue_pin_verified']=True
    report['all_published_seasons_requested']=seasons
    report['external_league_official_xg_verified']=False
    report['not_operational_for_betting']=True
    save(audit_path,report)
    return report

def main(argv=None):
    p=argparse.ArgumentParser(description='Private incremental PitchAPI source warehouse: 77 competitions')
    p.add_argument('--key',required=True)
    p.add_argument('--pins',type=Path,default=Path('data/pitchapi/league_pins_77.json'))
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--audit',type=Path,required=True)
    p.add_argument('--as-of',default='2026-10-31')
    p.add_argument('--max-new',type=int,default=16)
    p.add_argument('--max-requests',type=int,default=48)
    p.add_argument('--pause',type=float,default=.6)
    a=p.parse_args(argv)
    try:
        manifest=json.loads(a.pins.read_text('utf-8'))
        result=run_batch(key=a.key,manifest=manifest,
          api_key=os.environ.get('PITCHAPI_API_KEY',''),
          archive_path=a.archive,audit_path=a.audit,as_of=a.as_of,
          max_new=a.max_new,max_requests=a.max_requests,pause=a.pause)
        print('BETPILOT_PITCHAPI_ALL77 '+json.dumps({k:result.get(k) for k in (
            'registry_key','status','eligible_listed','new_attempts',
            'remaining_not_attempted','accepted_cumulative','quarantined_cumulative',
            'warning_retained_cumulative','requests_used','retryable_provider_interruption')},sort_keys=True))
        return 0
    except (HistoryError,ProbeError,ValueError,OSError,KeyError) as e:
        a.audit.parent.mkdir(parents=True,exist_ok=True)
        save(a.audit,{'status':'ALL77_SOURCE_ABSTAIN','league_key':a.key,
                      'reason':str(e)[:170], 'gts_score_calculated':False})
        print('BETPILOT_PITCHAPI_ALL77_ABSTAIN '+a.key+': '+str(e)[:170])
        return 2
if __name__=='__main__':
    raise SystemExit(main())
