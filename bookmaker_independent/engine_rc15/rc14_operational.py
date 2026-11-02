#!/usr/bin/env python3
"""BETPILOT Bookmaker Analyzer RC14 operational addon.

Independent *price inputs* O/U1.5,3.5, AH are NOT independent statistical signals;
Bet365 odds are mutually correlated. Frozen RC9 BISE and RC12 calibration unchanged.
"""
from pathlib import Path
import csv, hashlib, json, argparse
from rc13_scenarios import analyze as scenario_analyze, write_report as rc13_report
from rc11_operational import normalize, BIG5

ROOT=Path(__file__).resolve().parent
STATS=ROOT/'RC14_Statistics.json'
FREEZE_SHA256='666af5396bc760dd7b7296ea028f5aeaef8ecd4eb123fd56277f4764232cd8df'

def sha256(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()

def diagnose(row,model,ref,stats):
 if sha256(ROOT/'rc8_bise_frozen_source.py')!=FREEZE_SHA256:raise RuntimeError('Frozen BISE changed')
 rc=scenario_analyze(row,model,ref)
 probs=rc['market_direct']
 p25=probs['OU2_5']['over'];pGG=probs['BTTS']['GG']
 scope='BIG5' if rc['partita']['league'] in BIG5 else 'OTHER'
 gaps=[]
 p15=None;p35=None
 for x in rc['optional_direct_ou_diagnostics']:
  market='15' if x['market']=='Over1.5' else '35'
  hist=stats['market_metrics'][f'{scope}_{market}_train_2020_2024']
  threshold=hist['train_gap_p95_pp'];q=x['p_bookmaker_de_vig']
  if market=='15':p15=q
  else:p35=q
  gaps.append({'market':x['market'],'p_bet365':q,'p_bise_structural':x['p_bise_structural'],
               'gap_pp':x['gap_pp'],'abs_gap_pp':abs(x['gap_pp']),
               'train_abs_gap_p95_pp':threshold,'train_n':hist['n'],
               'gap_outside_train_p95':abs(x['gap_pp'])>threshold,
               'label':'Prezzo diretto vs struttura BISE, non prova di vantaggio predittivo'})
 tol=.005
 rules={'O1.5>=O2.5':None if p15 is None else p15+tol>=p25,
        'O2.5>=O3.5':None if p35 is None else p25+tol>=p35,
        'GG<=O1.5':None if p15 is None else pGG<=p15+tol}
 if any(x is False for x in rules.values()):quality='MARKET_PROBABILITY_INCONSISTENCY'
 elif not gaps:quality='NO_OPTIONAL_GOAL_MARKETS'
 elif any(x['gap_outside_train_p95'] for x in gaps):quality='UNUSUAL_CROSS_MARKET_GAP'
 else:quality='HISTORICALLY_TYPICAL_PRICE_GAPS'
 return {'schema':'BETPILOT_RC14_MARKET_COVERAGE_V1','match':rc['partita'],
    'scenario_intelligence_RC13':rc,
    'market_diagnostic':{'scope':scope,'coverage_goal_markets':len(gaps),'quoted_over15_present':p15 is not None,
       'quoted_over35_present':p35 is not None,'score':quality,'gaps':gaps,'logical_constraints':rules,
       'Asian_Handicap_direct_comparisons':rc['optional_AH_comparison'],
       'independent_signal_count_added':0,
       'meaning':'Stabilità dei PREZZI relativamente a RC9, NON Confidence di riuscita pronostico.'},
    'confidence_status':'RC12 single-market confidence only; no uplift from consensus; joint scenarios uncalibrated',
    'predictive_edge_certified':False}

def format_report(records,errors):
 out=['# BETPILOT | Bookmaker Analyzer 2.0 — RC14 Market Coverage & Scenario Stability','',
      '**Modalità:** scenari strutturali RC13 e confronto descrittivo prezzi Bet365. BISE RC9 congelato. Nessun vantaggio economico certificato.','',
      f'**Partite analizzate:** {len(records)} · **escluse:** {len(errors)}','']
 for x in records:
  m=x['match'];diag=x['market_diagnostic'];s=x['scenario_intelligence_RC13']['structural_joint_scenarios']
  out.extend([f"## {m['home']} – {m['away']} ({m['league']})",'',
   f"**Stabilità dei mercati:** `{diag['score']}` · {diag['coverage_goal_markets']} famiglie O/U aggiuntive · gruppo {diag['scope']}",'',
   '| Mercato aggiuntivo | Bet365 de-vig | BISE strutturale | Gap (pp) | Soglia 95° storica |','|---|---:|---:|---:|---:|'])
  for g in diag['gaps']:out.append(f"| {g['market']} | {g['p_bet365']*100:.2f}% | {g['p_bise_structural']*100:.2f}% | {g['gap_pp']:+.2f} | {g['train_abs_gap_p95_pp']:.2f} |")
  out+=['','**Vincoli:** '+', '.join(f'{k}: {v}' for k,v in diag['logical_constraints'].items()),
        '', '**Scenari congiunti (distribuzione BISE, probabilità sovrapposte):** '+', '.join(f'{k} {v*100:.1f}%' for k,v in list(s.items())[:5]),'']
  for a in diag['Asian_Handicap_direct_comparisons']:
   out.append(f"- AH {a['side']} / linea {a['home_handicap_line']:+.2f}: quota {a['offered_bookmaker_decimal']:.3f}; vincita ponderata {a['win_fraction']*100:.2f}%; rimborso {a['push_fraction']*100:.2f}%; perdita {a['loss_fraction']*100:.2f}% (solo strutturale)")
  out+=['','*Le quote aggiuntive sono correlate con le altre quote del bookmaker; concordanza ≠ maggiore Confidence e gap ≠ value bet dimostrata.*','']
 for e in errors:out.append(f"- Record {e['line']}: {e['error']}")
 return '\n'.join(out)+'\n'

def run(input_file,output_dir):
 inpath=Path(input_file); out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
 model=json.loads((ROOT/'RC12_Fitted_Calibrators.json').read_text());ref=json.loads((ROOT/'RC12_Reliability_Reference.json').read_text())
 if model['source_sha256']!=ref['source_sha256']:raise ValueError('RC12 model/reference SHA mismatch')
 if sha256(ROOT/'rc9_frozen_protocol.json')!=model['freeze_rc9_sha256']:raise ValueError('RC9 freeze protocol altered')
 stats=json.loads(STATS.read_text())
 with inpath.open(encoding='utf-8-sig',newline='') as f:
  rows=list(csv.DictReader(f))
 records=[];errors=[]
 for ix,r in enumerate(rows,2):
  try:records.append(diagnose(r,model,ref,stats))
  except Exception as exc:errors.append({'line':ix,'error':str(exc)})
 (out/'RC14_Analysis.json').write_text(json.dumps({'results':records,'errors':errors},indent=2,ensure_ascii=False))
 (out/'RC14_Report.md').write_text(format_report(records,errors))
 manifest={'release':'RC14_OPERATIONAL','source_input_sha256':sha256(inpath),
  'code_sha256':sha256(ROOT/'rc14_operational.py'),'frozen_bise_sha256':sha256(ROOT/'rc8_bise_frozen_source.py'),
  'stats_sha256':sha256(STATS),'n_accepted':len(records),'n_rejected':len(errors)}
 (out/'RC14_Manifest.json').write_text(json.dumps(manifest,indent=2))
 return manifest

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output-dir',required=True)
 a=p.parse_args();print(json.dumps(run(a.input,a.output_dir),indent=2))
