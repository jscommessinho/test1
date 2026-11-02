#!/usr/bin/env python3
"""Small, audited, reusable PitchAPI GTS source-backfill batch for 26 pinned leagues.

Research only. Original GA2/GTS/RC15/MA2 frozen engines untouched.
No claims of independent official xG checks or validated GTS scoring.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from betpilot.pitchapi_gts_probe import Client, ident
from betpilot.pitchapi_history_archive import HistoryError, load_archive, backfill, save, MAX_BATCH

PIN_SCHEMA='BETPILOT_PITCHAPI_LEAGUE_PINNED_26_v1'
KEY_RE=re.compile(r'^[a-z]{3}-[a-z0-9-]+$')


def pinned_league(manifest, key):
    if not isinstance(manifest,dict) or manifest.get('schema')!=PIN_SCHEMA:
        raise HistoryError('Unrecognized league pins manifest')
    leagues=manifest.get('leagues')
    if not isinstance(leagues,dict) or not KEY_RE.fullmatch(key) or key not in leagues:
        raise HistoryError('League not in reviewed 26-competition manifest')
    p=leagues[key]
    if not isinstance(p,dict) or p.get('tested_one_match') is not True:
        raise HistoryError('Pinned league not previously verified on sample')
    v={'id':ident(p['league_id'],'l'),'name':p['league_name'],'country':p['country_code']}
    if not all(isinstance(v[k],str) and v[k] for k in ('id','name','country')):
        raise HistoryError('Incomplete league pin')
    return v


def latest_two_seasons(seasons, as_of):
    """Select the latest 2 published seasons starting before the cutoff year.

    Calendar 2026/2025 and split 2026/2027 + 2025/2026 both supported;
    never select 2027/2028 when cutoff is in 2026.
    """
    try: y=int(as_of[:4])
    except (ValueError,TypeError): raise HistoryError('Bad as-of date') from None
    if not isinstance(seasons,list):raise HistoryError('Provider seasons must be list')
    eligible=[]
    for s in seasons:
        if not isinstance(s,str) or not re.fullmatch(r'20\d\d(?:/20\d\d)?',s):continue
        if int(s[:4])<=y:
            eligible.append(s)
    eligible=sorted(set(eligible),key=lambda s:(int(s[:4]),s),reverse=True)
    if len(eligible)<2:
        raise HistoryError('Insufficient source seasons for historical baseline')
    return eligible[:2]



def all_published_seasons(seasons, as_of):
    """Preserve original two-season helper while extending historical acquisition.

    Only announced provider seasons, before or beginning in as-of year. An
    advertised season is not proof of individual match-level xG completeness.
    """
    try: year=int(as_of[:4])
    except (TypeError,ValueError):raise HistoryError('Invalid as-of year') from None
    if not isinstance(seasons,list):raise HistoryError('Invalid seasons catalogue')
    matched=[x for x in seasons if isinstance(x,str) and
             re.fullmatch(r'20\d\d(?:/20\d\d)?',x) and int(x[:4])<=year]
    matched=sorted(set(matched),key=lambda x:(int(x[:4]),x),reverse=True)
    if not matched or len(matched)>7:
        raise HistoryError('No eligible seasons or more than seven; review catalogue')
    return matched

def run_batch(key, manifest, archive_path, audit_path, as_of, max_new, max_requests, pause, api_key):
    league=pinned_league(manifest,key)
    if not api_key or len(api_key)<6:raise HistoryError('Key missing; must use GitHub secret')
    if not 1<=max_new<=MAX_BATCH or not (3+2*max_new)<=max_requests<=350:
        raise HistoryError('Unsafe batch or request budget')
    client=Client(api_key,budget=max_requests,pause=pause)
    catalogue=client.get('/v1/leagues').get('leagues')
    from betpilot.pitchapi_history_archive import validate_source_league
    seasons=all_published_seasons(validate_source_league(catalogue,league),as_of)
    if max_requests < 2+len(seasons)+2*max_new:
        raise HistoryError('Request budget insufficient for every advertised historical season')
    archive=load_archive(archive_path,league,as_of)
    result=backfill(client,league,seasons,as_of,archive,max_new=max_new,checkpoint=archive_path,retain_warning=True)
    result['registry_key']=key
    result['provider_league_pin_verified']=True
    result['source_artifact_storage']='PRIVATE_GITHUB_ACTIONS_ARTIFACT_AND_CACHE_EPHEMERAL'
    result['gts_currently_operational']=False
    result['gt_0_50_matches_kept_as_research_warnings']=True
    save(audit_path,result)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description='26-league PitchAPI incremental private historical GTS warehouse (research)')
    p.add_argument('--key',required=True)
    p.add_argument('--pins',type=Path,default=Path('data/pitchapi/league_pins_26.json'))
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--audit',type=Path,required=True)
    p.add_argument('--as-of',default='2026-10-31')
    p.add_argument('--max-new',type=int,default=12)
    p.add_argument('--max-requests',type=int,default=31)
    p.add_argument('--pause',type=float,default=.45)
    a=p.parse_args(argv)
    try:
        manifest=json.loads(a.pins.read_text('utf-8'))
        report=run_batch(a.key,manifest,a.archive,a.audit,a.as_of,a.max_new,a.max_requests,a.pause,os.environ.get('PITCHAPI_API_KEY',''))
        print('BETPILOT_PITCHAPI_26_BATCH '+json.dumps({k:report.get(k) for k in ('registry_key','status','eligible_listed','new_attempts','remaining_not_attempted','accepted_cumulative','quarantined_cumulative','warning_retained_cumulative','requests_used')},sort_keys=True))
        return 0
    except (HistoryError,ValueError,OSError,KeyError) as e:
        save(a.audit,{'status':'PITCHAPI_26_BATCH_ABSTAIN','key':a.key,'reason':str(e)[:150]})
        print('BETPILOT_PITCHAPI_26_BATCH_ABSTAIN '+a.key+' '+str(e)[:150])
        return 2

if __name__=='__main__': raise SystemExit(main())
