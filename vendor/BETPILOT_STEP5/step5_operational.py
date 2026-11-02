#!/usr/bin/env python3
"""Safe orchestration: RC15 event identity + FootyStats GA2 history -> native integration.
Requires verified cross-provider team aliases. Preserves every frozen engine.
"""
from __future__ import annotations
import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from footystats_ga2_adapter import (load_files,predict_from_history,iso_utc,DataError,sha)
from api_football_adapter import norm
from integrate_native import check_payload,run_full

def team_map_load(path):
    obj=json.loads(Path(path).read_text('utf8'))
    if not isinstance(obj,dict) or not obj.get('mapping_verified_by_operator'):
        raise DataError('Team ID cross-provider mapping must be manually verified before FULL integration')
    if not isinstance(obj.get('teams'),dict):raise DataError('Expected teams mapping')
    return obj

def lookup_id(m,team):
    matches=[]
    for fs_id, name in m['teams'].items():
        if norm(name)==norm(team):matches.append(int(fs_id))
    if len(matches)!=1:raise DataError('Team mapping not unique/absent for '+str(team))
    return matches[0]

def run(history,team_map,rc15_input,output):
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    event=json.loads(Path(rc15_input).read_text('utf8'))
    check_payload(event)
    m=event['match']
    mapping=team_map_load(team_map)
    if mapping.get('competition')!=m['competition']:
        raise DataError('Competition mismatch for FootyStats team mapping')
    hi=lookup_id(mapping,m['home']);ai=lookup_id(mapping,m['away'])
    raw,sources=load_files(history)
    ga,audit=predict_from_history(raw,m,hi,ai,m['cutoff'])
    audit['sources']=sources
    audit['cross_provider_mapping_source']=Path(team_map).name
    audit['cross_provider_mapping_provenance']='OPERATOR_VERIFIED_NOT_INDEPENDENTLY_CERTIFIED'
    full={**event,'goal_analyzer':ga}
    check_payload(full)
    (out/'INPUT_WITH_GA2.json').write_text(json.dumps(full,ensure_ascii=False,indent=2,allow_nan=False)+'\n','utf8')
    (out/'FOOTYSTATS_GA2_AUDIT.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2,allow_nan=False)+'\n','utf8')
    report=run_full(full,out/'native_full')
    if report['status']!='FULL_NATIVE_EXECUTION_RESEARCH_ONLY':
        raise DataError('Native integration did not return research-only FULL state')
    return {'mode':report['status'],'event_id':m['event_id'],'train_n':ga['training_n'],
            'frozen_version':ga['version'],'operational_eligible':False}

def main(argv=None):
    p=argparse.ArgumentParser(description='Step5 authenticated/supplied RC15 event + verified FootyStats history')
    p.add_argument('--history',nargs='+',required=True,type=Path)
    p.add_argument('--team-map',required=True,type=Path)
    p.add_argument('--rc15-input',required=True,type=Path)
    p.add_argument('--output-dir',required=True,type=Path)
    a=p.parse_args(argv)
    try:
        print(json.dumps(run(a.history,a.team_map,a.rc15_input,a.output_dir),indent=2))
        return 0
    except (DataError,ValueError,OSError,RuntimeError) as ex:
        print('STEP5_FAILED: '+str(ex));return 2

if __name__=='__main__':raise SystemExit(main())
