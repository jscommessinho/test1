#!/usr/bin/env python3
"""BETPILOT BetAnalyzer 2.0 beta — verified RC15 -> MA2 native integration.

A *research* orchestration tool. Retains original source files unmodified.
Supports synthetic or user-supplied seven-odds Bet365 input and optional GA2 output.
Never presumes historical quote timestamps were independently verified.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, math, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
RC=HERE/'original_sources'/'rc15'
MA=HERE/'original_sources'/'ma2'/'BETPILOT_MARKET_ANALYZER_2_BETA'
sys.path.insert(0,str(RC));sys.path.insert(0,str(MA))
from rc15_intelligence import run as rc15_run, VERSION as RC15_VERSION  # noqa
from market_analyzer import run_analysis as ma2_run, validate_input as ma2_validate, VERSION as MA2_VERSION  # noqa

MARKET_NAMES={
 'q1':'FT.1X2.H','qx':'FT.1X2.D','q2':'FT.1X2.A',
 'qo':'FT.TOTAL.OVER.2.5','qu':'FT.TOTAL.UNDER.2.5',
 'qgg':'FT.BTTS.YES','qng':'FT.BTTS.NO',
 'over15':'FT.TOTAL.OVER.1.5','under15':'FT.TOTAL.UNDER.1.5',
 'over35':'FT.TOTAL.OVER.3.5','under35':'FT.TOTAL.UNDER.3.5'
}
SEVEN=['q1','qx','q2','qo','qu','qgg','qng']
OPTIONAL=['over15','under15','over35','under35','ah_line_home','ah_home_odds','ah_away_odds']
RC15_CSV_HEADERS=['bookmaker','match_day','analysis_kind','league','home','away']+SEVEN+OPTIONAL

class IntegrationError(ValueError): pass

def dt(s):
 try:
  t=datetime.fromisoformat(s.replace('Z','+00:00'))
  if t.tzinfo is None or t.utcoffset() is None: raise ValueError('timezone required')
  return t.astimezone(timezone.utc)
 except (TypeError,AttributeError,ValueError) as e:raise IntegrationError(f'invalid aware ISO date/time: {s!r}') from e

def sha256(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def name_eq(a,b):return str(a).strip().casefold()==str(b).strip().casefold()

def check_payload(x):
 m=x['match'];o=x['odds'];s=x['odds_snapshot'];
 for k in ('event_id','home','away','competition','kickoff','cutoff'):
  if not m.get(k):raise IntegrationError('Missing match.'+k)
 if not dt(m['cutoff'])<dt(m['kickoff']):raise IntegrationError('Cutoff must precede kickoff')
 for k in ('timestamp','provenance'):
  if not s.get(k):raise IntegrationError('Missing odds_snapshot.'+k)
 if s.get('bookmaker')!='Bet365':raise IntegrationError('Only Bet365 input permitted')
 if s['provenance'] not in ('SYNTHETIC','USER_SUPPLIED_NOT_TIMESTAMP_VERIFIED','VERIFIED_EXTERNAL_SNAPSHOT'):
  raise IntegrationError('Provenance must be explicit')
 if dt(s['timestamp'])>dt(m['cutoff']):raise IntegrationError('Odds after cutoff')
 if s['provenance']=='VERIFIED_EXTERNAL_SNAPSHOT' and not s.get('external_capture_reference'):
  raise IntegrationError('Verified snapshot requires external capture reference')
 if not all(k in o for k in SEVEN):raise IntegrationError('Seven RC15 required prices missing')
 for k,v in o.items():
  if k not in SEVEN+OPTIONAL:raise IntegrationError('Unknown odds field '+k)
  if v is None:continue
  if not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v):raise IntegrationError('Invalid '+k)
  if k!='ah_line_home' and v<=1:raise IntegrationError('Odds > 1 required: '+k)
 for group in [('over15','under15'),('over35','under35'),('ah_line_home','ah_home_odds','ah_away_odds')]:
  present=[k for k in group if o.get(k) is not None]
  if present and len(present)!=len(group):raise IntegrationError('Incomplete optional pair/set '+str(group))
 g=x.get('goal_analyzer')
 if g:
  if g.get('version')!='GA2-RESEARCH-20261029-v1':raise IntegrationError('Goal Analyzer version mismatch')
  if g.get('operational_eligible',False):raise IntegrationError('GA2 may not be operational_eligible')
  if not g.get('cutoff') or dt(g['cutoff'])>dt(m['cutoff']):raise IntegrationError('GA2 after cutoff or cutoff missing')
  for v in ('lambda_home','lambda_away'):
   if not isinstance(g.get(v),(int,float)) or not 0<g[v]<30:raise IntegrationError('Invalid GA2 '+v)
  if g.get('event_id')!=m['event_id'] or not name_eq(g.get('home'),m['home']) or not name_eq(g.get('away'),m['away']):
   raise IntegrationError('GA event identity mismatch')
 return x

def native_rc15(row,odds,out):
 out.mkdir(parents=True,exist_ok=True)
 csv_path=out/'RC15_native_input.csv'
 csv_data={'bookmaker':'Bet365','match_day':row['kickoff'][:10],'analysis_kind':'synthetic_integration_test' if row.get('synthetic') else 'prematch_user_supplied',
  'league':row['competition'],'home':row['home'],'away':row['away']}
 for k in SEVEN+OPTIONAL:csv_data[k]=str(odds[k]) if k in odds and odds[k] is not None else ''
 with csv_path.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=RC15_CSV_HEADERS);w.writeheader();w.writerow(csv_data)
 manifest=rc15_run(csv_path,out/'rc15_native')
 result=json.loads((out/'rc15_native'/'RC15_Analysis.json').read_text(encoding='utf8'))
 if manifest['analyzed']!=1 or manifest['rejected'] or result['errors'] or len(result['reports'])!=1:
  raise IntegrationError('RC15 failed/rejected fixture: '+str(result['errors']))
 report=result['reports'][0];i=report['identity']
 if not all((name_eq(i['home'],row['home']),name_eq(i['away'],row['away']),i['league']==row['competition'])):
  raise IntegrationError('RC15 identity mismatch')
 return report,manifest,csv_path

def check_market_mapping(rc):
 p=rc['single_market_probabilities_RC12_experimental']
 mapping={'FT.1X2.H':p['1X2']['p']['1'],'FT.1X2.D':p['1X2']['p']['X'],'FT.1X2.A':p['1X2']['p']['2'],
 'FT.DC.1X':p['1X2']['p']['1X'],'FT.DC.X2':p['1X2']['p']['X2'],'FT.DC.12':p['1X2']['p']['12'],
 'FT.TOTAL.OVER.2.5':p['OU2_5']['p']['Over_2_5'],'FT.TOTAL.UNDER.2.5':p['OU2_5']['p']['Under_2_5'],
 'FT.BTTS.YES':p['BTTS']['p']['GG'],'FT.BTTS.NO':p['BTTS']['p']['NG']}
 for k,v in mapping.items():
  if not isinstance(v,(int,float)) or not 0<=v<=1:raise IntegrationError('RC15 malformed probability '+k)
 assert abs(sum(mapping[k] for k in ('FT.1X2.H','FT.1X2.D','FT.1X2.A'))-1)<1e-8
 assert abs(mapping['FT.BTTS.YES']+mapping['FT.BTTS.NO']-1)<1e-8
 assert abs(mapping['FT.TOTAL.OVER.2.5']+mapping['FT.TOTAL.UNDER.2.5']-1)<1e-8
 atoms=rc['bise_structural']['all_12_atomic_states'];assert abs(sum(atoms.values())-1)<1e-8
 return mapping

def verify_archived_ga_probabilities(g, ma_output):
 """Only when original archived GA probabilities are supplied, verify their parity."""
 expected=g.get('probabilities') or {}
 if not expected: return {}
 ids={'1':'FT.1X2.H','X':'FT.1X2.D','2':'FT.1X2.A',
      'GG':'FT.BTTS.YES','NG':'FT.BTTS.NO',
      'O2.5':'FT.TOTAL.OVER.2.5','U2.5':'FT.TOTAL.UNDER.2.5'}
 present={row['market_id']:row['p_event'] for row in ma_output['markets']}
 checked={}
 for old,new in ids.items():
  if old not in expected or new not in present: continue
  delta=abs(float(expected[old])-float(present[new]))
  if delta>1e-8:raise IntegrationError('Archived GA2 probability/lambda discrepancy at '+new)
  checked[new]=delta
 return checked

def make_ma_input(x,rc,manifest,csv_path):
 m=x['match'];s=x['odds_snapshot'];g=x.get('goal_analyzer');prob=check_market_mapping(rc)
 stamp=s['timestamp'];snapshot=s.get('snapshot_id') or 'SINGLE_CAPTURE'
 quote=[{'market_id':MARKET_NAMES[k],'decimal_odds':v,'timestamp':stamp,'source':'Bet365','snapshot_id':snapshot} for k,v in x['odds'].items() if k in MARKET_NAMES and v is not None]
 ba={'protocol':'RC15','source':'Bet365','timestamp':stamp,'validation_state':'EXPERIMENTAL_HISTORICAL_SINGLE_MARKET_ONLY',
     'market_probabilities':prob,
     'confidence':{'historical_support':rc['historical_support'],'interpretation':'NOT_A_PREDICTIVE_CONFIDENCE_SCORE'},
     'scenario_intelligence':rc['bise_structural'],
     'market_stability':rc['optional_market_diagnostics'],
     'native_schema':rc['schema'],'native_manifest':manifest,
     'provenance':'RC15 NATIVE OUTPUT, not synthetic RC12 probabilities',
     'bise_is_not_independent_source':True}
 output={'match':{k:m[k] for k in ('event_id','home','away','competition','kickoff','cutoff')},
  'quotes':quote,'bookmaker_analyzer':ba,
  'requested_markets':x.get('requested_markets') or [MARKET_NAMES[k] for k in SEVEN],
  'dependence_pairs':x.get('dependence_pairs') or [['FT.BTTS.YES','FT.TOTAL.OVER.2.5']],}
 if g:
  output['goal_analyzer']={k:v for k,v in g.items() if k not in ('event_id','home','away')}
 ma2_validate(output)
 return output

def run_full(x,out):
 check_payload(x);m=x['match'];o=x['odds'];s=x['odds_snapshot'];out=Path(out);out.mkdir(parents=True,exist_ok=True)
 rc,rc_manifest,inp=native_rc15({**m,'synthetic':s['provenance']=='SYNTHETIC'},o,out)
 mainput=make_ma_input(x,rc,rc_manifest,inp)
 (out/'MA2_native_input.json').write_text(json.dumps(mainput,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf8')
 ma=ma2_run(mainput)
 if ma['protocol']!='MA2-BETA-20261030':raise IntegrationError('MA2 returned unexpected protocol')
 (out/'MA2_native_output.json').write_text(json.dumps(ma,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf8')
 if ma.get('bookmaker_rc15',{}).get('scenario_intelligence') != rc['bise_structural']:
  raise IntegrationError('Native BISE scenarios not preserved')
 for k,v in mainput['bookmaker_analyzer']['market_probabilities'].items():
  if abs(ma['bookmaker_rc15']['market_probabilities'][k]-v)>1e-12:
   raise IntegrationError('Native RC12 probability transfer mismatch '+k)
 for k,v in [('home',m['home']),('away',m['away']),('competition',m['competition'])]:
  if ma['match'][k]!=v:raise IntegrationError('MA2 identity mismatch')
 checked_ga = verify_archived_ga_probabilities(x['goal_analyzer'],ma) if x.get('goal_analyzer') else {}
 if ma['operational_eligible'] or ma['betting_recommendation'] is not None:raise IntegrationError('MA2 operational safety invariant violated')
 status='FULL_NATIVE_EXECUTION_RESEARCH_ONLY' if x.get('goal_analyzer') else 'RC15_MA2_NATIVE_BOOKMAKER_ONLY'
 result={'protocol':'BETPILOT-BETANALYZER-2.0-STEP3','status':status,'event':m,
  'quote_origin':{'provenance':s['provenance'],'time':s['timestamp'],'source':'Bet365 claimed; user-supplied or synthetic',
                  'capture_verified':s['provenance']=='VERIFIED_EXTERNAL_SNAPSHOT','synthetic':s['provenance']=='SYNTHETIC'},
  'components':{'GA2':'ARCHIVED_OR_PROVIDED_REFERENCE_RESEARCH_ONLY' if x.get('goal_analyzer') else 'NOT_AVAILABLE',
   'RC15':rc['schema'],'MA2':ma['protocol']},
  'ga2_output':x.get('goal_analyzer'),'archived_ga_probability_checks':checked_ga,
  'rc15_native_report':'rc15_native/RC15_Analysis.json', 'ma2_native_report':'MA2_native_output.json',
  'bookmaker_structure':{'no_vig':rc['bookmaker_prices'],
   'rc12_single_market_experimental':rc['single_market_probabilities_RC12_experimental'],
   'bise_structural':rc['bise_structural'],
   'historical_support':rc['historical_support'], 'market_stability':rc['optional_market_diagnostics']},
  'model_market_comparison':[{k:r.get(k) for k in ('market_id','p_event','bookmaker_p_no_vig','bookmaker_rc15_p','p_model_minus_bookmaker','p_model_minus_rc15','sensitivity_10pct_lambda','status')} for r in ma['markets']],
  'market_scenarios':ma.get('scenario_hierarchy'), 'analytical_candidates':ma.get('analytical_candidates'),
  'confidence':ma['confidence'],'selection_status':ma['selection_status'], 'betting_recommendation':None,
  'operational_eligible':False,
  'limitations':['Validation is retrospective, not prospective.','The GA2 motor is research-only.',
   'Bookmaker RC12 marginal calibration and BISE joint grid are different layers.',
   'MA2 ranking is descriptive, not a validated betting selector.',
   'RC15 sees odds but does not independently attest historical timestamps.',
   'SYNTHETIC quotes must never be represented as real Bet365 prices.' if s['provenance']=='SYNTHETIC' else
   'User-supplied market price timestamps are not independently authenticated without external provenance.']}
 (out/'BETANALYZER_ANALYSIS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
 (out/'ANALYSIS_REPORT.md').write_text(render_report(result,ma),'utf8')
 (out/'RUN_MANIFEST.json').write_text(json.dumps({'sha256':{str(p.relative_to(out)):sha256(p) for p in out.rglob('*') if p.is_file() and p.name!='RUN_MANIFEST.json'},
 'native_rc15_source_manifest_verified_externally':True,'native_ma2_source_manifest_verified_externally':True,
 'originals_modified':False,'future_accuracy_claims':False},ensure_ascii=False,indent=2)+'\n','utf8')
 return result

def render_report(r,ma):
 m=r['event'];is_synth=r['quote_origin']['synthetic'];ga=r.get('ga2_output');b=r['bookmaker_structure'];p=b['no_vig']; lines=[
 '# ANALYSIS: '+m['home']+' – '+m['away'],'',
 '**Competizione:** '+m['competition']+' · **Kickoff:** '+m['kickoff']+' · **Cutoff:** '+m['cutoff'],
 '**Modalità:** '+r['status']+' · **Stato:** RESEARCH_ONLY',
 '**Quote:** '+('SINTETICHE, non Bet365 reali' if is_synth else 'fornite esternamente, origine temporale non certificata dal modulo')+'',
 '', '## A. Match Overview','', 'Identificazione, provenienza e cutoff verificati nel file di input.','',
 '## B. Goal Analyzer','']
 if ga:lines += [f"GA2 ({ga['version']}) · λ casa {ga['lambda_home']:.4f}, λ ospite {ga['lambda_away']:.4f} · **NON VALIDATO PER PUNTATE**.",'']
 else:lines += ['Non fornito: solo analisi della struttura delle quote.','']
 lines+=['## C. Bookmaker Analyzer RC15 nativo','',
  'RC15 è stato realmente eseguito, incluse la ricostruzione BISE, le probabilità marginali RC12 e gli scenari RC13.',
  f"- 1X2 no-vig: casa {p['1X2_de_vig']['1']:.2%}, pareggio {p['1X2_de_vig']['X']:.2%}, ospite {p['1X2_de_vig']['2']:.2%}.",
  f"- O2.5 no-vig: {p['OU2_5_de_vig']['over']:.2%}; GG no-vig: {p['GG_NG_de_vig']['GG']:.2%}.",
  f"- BISE strutturale: λ casa {b['bise_structural']['lambda_home']:.4f}, λ ospite {b['bise_structural']['lambda_away']:.4f}.",
  'Le probabilità RC12 per mercato e la matrice BISE non sono intercambiabili.','',
  '## D. Market Analyzer MA2 nativo','',
  f"MA2 eseguito: modalità `{ma['mode']}`, mercati valutati {len(ma['markets'])}, pricing groups {len(ma['pricing_groups'])}.",
  'Analytical candidates descrittivi: '+(', '.join(x['market_id'] for x in (ma.get('analytical_candidates') or [])) or 'nessuno')+'.',
  '', '## E. Market Ranking','',
  'Nessuna classifica di affidabilità empiricamente calibrata; i seguenti confronti sono solo descrittivi.',
  '| Mercato | GA2 p | Bet365 no-vig | RC12 p |','|---|---:|---:|---:|']
 for row in r['model_market_comparison']:
  if row['market_id'] in ('FT.1X2.H','FT.1X2.D','FT.1X2.A','FT.TOTAL.OVER.2.5','FT.BTTS.YES'):
   f=lambda v: f'{v:.2%}' if isinstance(v,(int,float)) else 'n.d.'
   lines.append(f"| {row['market_id']} | {f(row.get('p_event'))} | {f(row.get('bookmaker_p_no_vig'))} | {f(row.get('bookmaker_rc15_p'))} |")
 lines += ['', '## F. Risk Assessment','']+['- '+v for v in r['limitations']]+['', '## G. Final Verdict','',
 '**ASTENSIONE OPERATIVA — ricerca soltanto.** L’integrazione tecnica dei moduli è stata eseguita; non è una verifica della capacità di pronosticare o di generare profitto.','']
 return '\n'.join(lines)

def main():
 parser=argparse.ArgumentParser()
 parser.add_argument('input',help='Full-event JSON with match, odds_snapshot, odds, optional goal_analyzer')
 parser.add_argument('--output-dir',required=True)
 a=parser.parse_args();r=run_full(json.loads(Path(a.input).read_text()),Path(a.output_dir))
 print(json.dumps({'status':r['status'],'output_dir':a.output_dir},indent=2))
if __name__=='__main__':main()
