#!/usr/bin/env python3
"""Premier League xG threshold sensitivity, strict vs inclusive research-only.

Re-reads the seven quarantined matches from PitchAPI using free authenticated API,
then compares GTS ECDF holdout metrics. No changes to frozen GTS or production QA.
"""
from __future__ import annotations
import argparse,copy,json,os,statistics
from pathlib import Path
from betpilot.pitchapi_gts_probe import Client,parse_shots,parse_match_stats
from betpilot.pitchapi_gts_reference import build_reference
from betpilot.pitchapi_history_archive import load_archive,choose_fixture_rows,save
from betpilot.gts_pitchapi_quality import assess_pitchapi_match

L={'id':'l_4WFCIZ','name':'Premier League','country':'ENG'}

def compare(archive,client,as_of='2026-10-31'):
    rejected=archive['rejected']
    if not rejected:raise ValueError('No quarantined fixtures to assess')
    seasons=sorted(set(x['season'] for x in rejected.values()),reverse=True)
    lists=[]
    for season in seasons:
        obj=client.get('/v1/leagues/'+L['id']+'/matches',{'season':season,'status':'played'})
        if obj.get('league',{}).get('id')!=L['id']:
            raise ValueError('Provider league mismatch')
        lists.append((season,obj['matches']))
    fixture_map={r['match_id']:r for r in choose_fixture_rows(lists,L,as_of)}
    findings=[]
    research_extra={}
    for mid,previous in sorted(rejected.items()):
        m=fixture_map.get(mid)
        if not m or m['date']!=previous['date'] or m['season']!=previous['season']:
            raise ValueError('Match identity changed across source snapshots: '+mid)
        source={'id':mid,'home_team':m['home'],'away_team':m['away']}
        shots=client.get('/v1/matches/'+mid+'/shots')
        totals=parse_shots(shots,source)
        stats=client.get('/v1/matches/'+mid+'/stats')
        own=parse_match_stats(stats,source)
        ht=m['home']['id'];at=m['away']['id']
        payload={'match_id':mid,'date':m['date'],'home':{'id':ht,**totals[ht]},'away':{'id':at,**totals[at]},'team_stats_xg':own}
        qa=assess_pitchapi_match(payload)
        if qa['internal_pitchapi_status']!='REJECTED_INTERNAL_ABOVE_0_50':
            raise ValueError('Prior rejection no longer reproducible: provider revision? '+mid)
        findings.append({'match_id':mid,'date':m['date'],'season':m['season'],
                         'home_team':m['home']['name'],'away_team':m['away']['name'],
                         'pitchapi_shot_xg':qa['pitchapi_shot_xg'],
                         'pitchapi_team_stat_xg':list(own) if own else None,
                         'absolute_deltas':qa['internal_absolute_deltas'],
                         'max_abs_delta':max(qa['internal_absolute_deltas']),
                         'internal_quality':'WARNING_ABOVE_0_50','official_league_reference_checked':False})
        # EPHEMERAL research scenario only: include these records for sensitivity analysis.
        # Do NOT output any record labelled as production QA-PASS or alter stored archives.
        research_extra[mid]={'match_id':mid,'league_id':L['id'],'season':m['season'],'date':m['date'],
                             'home':{'id':ht,'name':m['home']['name'],'goals':m['home_goals'],
                                     'xg':round(totals[ht]['xg'],6),'shots':totals[ht]['shots']},
                             'away':{'id':at,'name':m['away']['name'],'goals':m['away_goals'],
                                     'xg':round(totals[at]['xg'],6),'shots':totals[at]['shots']},
                             'qa_internal':'PASS'}
    strict=build_reference(archive)
    virtual=copy.deepcopy(archive)
    virtual['records'].update(research_extra)
    # The override is never exported: numerical ablation only.
    inclusive=build_reference(virtual)
    if not strict.get('holdout_evaluation_descriptive') or not inclusive.get('holdout_evaluation_descriptive'):
        raise ValueError('ECDF scenarios could not be evaluated independently')
    sr={(x['match_id'],x['team_id']):x['score_0_100_research_only'] for x in strict['holdout_rows_scored']}
    ir={(x['match_id'],x['team_id']):x['score_0_100_research_only'] for x in inclusive['holdout_rows_scored']}
    common=sorted(sr.keys() & ir.keys());deltas=[abs(ir[k]-sr[k]) for k in common]
    def diag(r):
        x=r['holdout_evaluation_descriptive']
        return {'train_rows':r['train_rows'],'holdout_rows':r['holdout_rows'],
                'gts_goal_spearman':x['holdout_score_vs_goals_spearman'],
                'gts_xg_spearman':x['holdout_score_vs_xg_spearman'],
                'offense_goal_spearman':x['holdout_single_O_vs_goals_spearman']}
    return {'status':'RESEARCH_THRESHOLD_ABLATION_NOT_OPERATIONAL',
            'policy':'Original <=0.50 retained in certified data; seven flagged games allowed ONLY in simulation',
            'as_of':as_of,'source':'PitchAPI xG per shot and PitchAPI own totals; no independent league xG',
            'strict':diag(strict),'warn_inclusive':diag(inclusive),
            'excluded_games_checked':len(findings),'exclusions':findings,
            'count_original_records':len(archive['records']),
            'count_inclusive_virtual_records':len(virtual['records']),
            'score_comparison_common_holdout_rows':len(common),
            'score_change_points_mean_abs':statistics.mean(deltas) if deltas else None,
            'score_change_points_max_abs':max(deltas) if deltas else None,
            'change_gts_goal_spearman':diag(inclusive)['gts_goal_spearman']-diag(strict)['gts_goal_spearman'],
            'requests_used':client.used,'original_gts_code_modified':False,
            'official_xg_certified':False,'operational_eligible':False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--as-of',default='2026-10-31')
    args=p.parse_args()
    doc=load_archive(args.archive,L,args.as_of)
    key=os.environ.get('PITCHAPI_API_KEY','')
    if not key:raise SystemExit('PITCHAPI_API_KEY_REQUIRED')
    client=Client(key,budget=25,pause=.25)
    results=compare(doc,client,args.as_of)
    save(args.output,results)
    print('PITCHAPI_QA_SENSITIVITY '+json.dumps({k:v for k,v in results.items() if k in ('status','count_original_records','count_inclusive_virtual_records','excluded_games_checked','score_comparison_common_holdout_rows','score_change_points_mean_abs','score_change_points_max_abs','change_gts_goal_spearman','requests_used','strict','warn_inclusive')},sort_keys=True))

if __name__=='__main__':main()
