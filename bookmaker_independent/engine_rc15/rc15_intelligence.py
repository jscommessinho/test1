#!/usr/bin/env python3
"""BETPILOT | Bookmaker Analyzer 2.0 RC15 — final reporting layer.

Reads Bet365 odds; delegates calculations to RC9/RC12/RC13/RC14 frozen modules.
No additional statistical model, no recalibration, no independently validated edge.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
from html import escape as H
import hashlib
import json
import math
from pathlib import Path

from rc14_operational import diagnose, sha256 as old_sha256, FREEZE_SHA256
from rc11_operational import BIG5

ROOT=Path(__file__).resolve().parent
VERSION='BETPILOT_BOOKMAKER_ANALYZER_2.0_RC15_FINAL_REPORT_V1'
REQUIRED=['bookmaker','league','home','away','q1','qx','q2','qo','qu','qgg','qng']
OPTIONAL=['match_day','analysis_kind','over15','under15','over35','under35','ah_line_home','ah_home_odds','ah_away_odds']


def format_pct(n): return f'{n*100:.2f}%'
def pp(n): return f'{n:+.2f} pp'
def comma(s): return str(s).replace('.',',')
def hash_file(p):return old_sha256(p)


def verify_inputs():
    frozen=ROOT/'rc8_bise_frozen_source.py'
    if hash_file(frozen)!=FREEZE_SHA256:
        raise ValueError('FROZEN_BISE_SHA_MISMATCH')
    model=json.loads((ROOT/'RC12_Fitted_Calibrators.json').read_text(encoding='utf8'))
    reference=json.loads((ROOT/'RC12_Reliability_Reference.json').read_text(encoding='utf8'))
    stats=json.loads((ROOT/'RC14_Statistics.json').read_text(encoding='utf8'))
    if model['source_sha256']!=reference['source_sha256']:
        raise ValueError('INCOMPATIBLE_RC12_REFERENCE')
    if hash_file(ROOT/'rc9_frozen_protocol.json')!=model['freeze_rc9_sha256']:
        raise ValueError('RC9_PROTOCOL_CHANGED')
    return model,reference,stats


def historical_support(ref,scope,market,event,p):
    idx=max(0,min(9,int(p*10)))
    d=ref['markets'][scope][market]['by_event'][event]['bins'][idx]
    n=int(d['matches'])
    if n < 100: state='INSUFFICIENT'
    elif n < 1000:state='LIMITED'
    else:state='AVAILABLE'
    return {'scope':scope,'range':d['bucket'],'n':n,'mean_prediction':d['mean_prediction'],
            'observed_frequency':d['observed_frequency'],'bias_pp':d['mean_bias_pp'],
            'support_level':state,'not_confidence_score':True,
            'reference':'Exploratory retrospective bins; 2025-2026 previously inspected; not future-certification'}


def summarise(raw,ref):
    r=raw['scenario_intelligence_RC13']; dg=raw['market_diagnostic']
    direct=r['market_direct']; scope=dg['scope']; market_cal=r['rc12_confidence_unmodified']
    # RC12 *operational* marginal estimates must be kept separate from RC9 BISE structural joint grid.
    p1={k:market_cal[k]['p'] for k in ('1','X','2','1X','X2','12')}
    o={k:market_cal[k]['p'] for k in ('Over_2_5','Under_2_5')}
    bt={k:market_cal[k]['p'] for k in ('GG','NG')}
    def top(x):return max(x,key=x.get)
    core={'1X2':{'p':p1,'favourite':top({k:p1[k] for k in ('1','X','2')}),
                    'method':r['rc12_scope_and_methods']['1X2']},
          'OU2_5':{'p':o,'favourite':top(o),'method':r['rc12_scope_and_methods']['OU2_5']},
          'BTTS':{'p':bt,'favourite':top(bt),'method':'RC11.2 (reference calibrated by scope where applicable)'}}
    supports={}
    for k,p in p1.items():supports[k]=historical_support(ref,scope,'1X2',k,p)
    for k,p in o.items():supports[k]=historical_support(ref,scope,'OU2_5',k,p)
    # GG/NG historic support derives from separate RC11.2 reference as implemented in RC12.
    bt_ref=json.loads((ROOT/'rc11_2_confidence_holdout_bins.json').read_text(encoding='utf8'))
    bin_id=str(max(0,min(9,int(bt['GG']*10))))
    b=bt_ref['by_group'][scope]['bins'][bin_id]
    supports['GG_NG']={'scope':scope,'GG_probability_band':f'{int(bin_id)*10}–{(int(bin_id)+1)*10}%',
                       'n':b['n'],'observed_GG':b['observed_GG'],'mean_predicted_GG':b['mean_pred'],
                       'bias_pp':b['bias_pp'],'support_level':('INSUFFICIENT' if b['n']<100 else 'LIMITED' if b['n']<1000 else 'AVAILABLE'),
                       'not_confidence_score':True}
    m=r['partita'];top_atom=list(r['structural_joint_atomic_12_states'].items())[:6]
    topscore=r['structural_exact_scores'][:5]
    pqq={k:direct['1X2'][k] for k in ('1','X','2')}
    # Statistical independence intentionally NOT assumed; market data comes from the same bookmaker.
    obj={
        'schema':VERSION,
        'identity':{'home':m['home'],'away':m['away'],'league':m['league'],
                    'match_day':m['date'],'analysis_kind':m['analysis_kind'],'bookmaker':'Bet365',
                    'prematch_closing_status':'user_attested_not_independently_timestamp_verified'},
        'data_quality':{'primary_quote_families':3,'required_odds_count':7,
                        'optional_over_families':dg['coverage_goal_markets'],
                        'diagnostic':dg['score'],'logical_constraints':dg['logical_constraints'],
                        'scope':scope,'frozen_BISE_verified':True},
        'bookmaker_prices':{'1X2_de_vig':pqq,
                            'OU2_5_de_vig':direct['OU2_5'],
                            'GG_NG_de_vig':direct['BTTS']},
        'single_market_probabilities_RC12_experimental':core,
        'historical_support':supports,
        'bise_structural':{'lambda_home':r['bise_unmodified']['lambda_home'],
                           'lambda_away':r['bise_unmodified']['lambda_away'],
                           'GG':r['structural_marginals']['GG'],
                           'GG_direct_gap_pp':r['bise_unmodified']['gap_pp'],
                           'GG_gap_historical_band':r['bise_unmodified']['gap_percentile_band'],
                           'top_6_mutually_exclusive_scenarios':[{'event':k,'p':v} for k,v in top_atom],
                           'top_8_overlapping_scenarios':[{'event':k,'p':v} for k,v in list(r['structural_joint_scenarios'].items())[:8]],
                           'top_5_exact_scores':topscore,
                           'all_12_atomic_states':r['structural_joint_atomic_12_states']},
        'optional_market_diagnostics':{'over_under_gaps':dg['gaps'],'asian_handicap':dg['Asian_Handicap_direct_comparisons'],
                                       'independent_signals_added':0},
        'status':{'operational':True,'predictive_confidence':'EXPERIMENTAL_HISTORICAL_SINGLE_MARKET_ONLY',
                  'joint_scenarios_empirically_calibrated':False,
                  'bookmaker_exposure_inferable_from_prices':False,
                  'forward_validation':'PAUSED_AS_REQUESTED',
                  'value_bet_or_ROI_certified':False},
    }
    # Validations of the presentation layer, not prediction quality.
    assert math.isclose(sum(p1[k] for k in ('1','X','2')),1,abs_tol=1e-9)
    assert math.isclose(o['Over_2_5']+o['Under_2_5'],1,abs_tol=1e-9)
    assert math.isclose(bt['GG']+bt['NG'],1,abs_tol=1e-9)
    assert math.isclose(sum(r['structural_joint_atomic_12_states'].values()),1,abs_tol=1e-8)
    return obj


def describe_diagnostic(s):
    return {'HISTORICALLY_TYPICAL_PRICE_GAPS':'Scostamenti dei mercati aggiuntivi storicamente tipici',
            'UNUSUAL_CROSS_MARKET_GAP':'Uno o più scostamenti superano la soglia storica di riferimento',
            'MARKET_PROBABILITY_INCONSISTENCY':'Incoerenza probabilistica tra mercati: controllare i prezzi',
            'NO_OPTIONAL_GOAL_MARKETS':'Solo sette quote standard: controllo dei mercati aggiuntivi non disponibile'}.get(s,s)


def render_match_md(x):
    m=x['identity'];q=x['single_market_probabilities_RC12_experimental'];b=x['bise_structural'];diag=x['data_quality']
    A=['## '+m['home']+' – '+m['away']+' ('+m['league']+')','',
       f"**Data:** {m['match_day'] or 'non indicata'} · **Fonte:** Bet365 (attestata dall’utente) · **Gruppo:** {diag['scope']}",'',
       '### 1. Gerarchia Bet365 e probabilità operative','',
       '| Mercato | Probabilità | Indicatore 0–10 | Supporto storico |','|---|---:|---:|---|']
    for k in ('1','X','2','1X','X2','12'):
        p=q['1X2']['p'][k];v=x['historical_support'][k]
        A.append(f"| {k} | {format_pct(p)} | {p*10:.1f} | {v['n']:,} in fascia {v['range']} ({v['support_level']}) |")
    for k,label in [('Over_2_5','Over 2.5'),('Under_2_5','Under 2.5')]:
        p=q['OU2_5']['p'][k];v=x['historical_support'][k]
        A.append(f"| {label} | {format_pct(p)} | {p*10:.1f} | {v['n']:,} in fascia {v['range']} ({v['support_level']}) |")
    for k in ('GG','NG'):
        p=q['BTTS']['p'][k];v=x['historical_support']['GG_NG']
        A.append(f"| {k} | {format_pct(p)} | {p*10:.1f} | {v['n']:,} in fascia GG {v['GG_probability_band']} ({v['support_level']}) |")
    A+=['',f"**Favoriti per mercato:** 1X2 → **{q['1X2']['favourite']}**; gol → **{q['OU2_5']['favourite']}**; GG/NG → **{q['BTTS']['favourite']}**.",'',
        f"**Metodi:** 1X2 `{q['1X2']['method']}`; Over 2.5 `{q['OU2_5']['method']}`. I valori 0–10 sono probabilità × 10, non voti di affidabilità.",'',
        '### 2. Scenario Intelligence BISE (distribuzione strutturale separata)','',
        f"**Gol attesi modello:** casa {b['lambda_home']:.2f}, trasferta {b['lambda_away']:.2f}. **GG BISE:** {format_pct(b['GG'])}; scostamento dal GG Bet365: **{pp(b['GG_direct_gap_pp'])}** ({b['GG_gap_historical_band']}).",'',
        '| Scenari esclusivi 1X2 × GG/NG × O/U 2.5 | Probabilità BISE |','|---|---:|']
    for sc in b['top_6_mutually_exclusive_scenarios']: A.append(f"| {sc['event']} | {format_pct(sc['p'])} |")
    A+=['', '**Combinazioni sovrapposte (NON sommarle):** '+', '.join(f"{s['event']} {format_pct(s['p'])}" for s in b['top_8_overlapping_scenarios'][:5])+'.','',
        '**Risultati esatti (non calibrati singolarmente):** '+', '.join(f"{s['score']} {format_pct(s['prob'])}" for s in b['top_5_exact_scores'])+'.','',
        '### 3. Stabilità e coerenza delle quote','',
        f"**Diagnosi:** {describe_diagnostic(diag['diagnostic'])}.",'']
    gaps=x['optional_market_diagnostics']['over_under_gaps']
    if gaps:
        A+=['| Mercato | Bet365 de-vig | BISE | Differenza | Soglia p95 pre-2025 |','|---|---:|---:|---:|---:|']
        for g in gaps:
            A.append(f"| {g['market']} | {format_pct(g['p_bet365'])} | {format_pct(g['p_bise_structural'])} | {pp(g['gap_pp'])} | {g['train_abs_gap_p95_pp']:.2f} pp |")
    else:A+=['**Mercati O/U aggiuntivi assenti:** nessun giudizio di copertura estesa.','']
    h=x['optional_market_diagnostics']['asian_handicap']
    if h:
        A+=['','**Handicap asiatico quotato (payout in frazioni ponderate):**','',
           '| Lato / linea | Quota Bet365 | Vincita | Rimborso | Perdita | Quota strutturale |','|---|---:|---:|---:|---:|---:|']
        for a in h:
            A.append(f"| {a['side']} / {a['home_handicap_line']:+.2f} | {a['offered_bookmaker_decimal']:.3f} | {format_pct(a['win_fraction'])} | {format_pct(a['push_fraction'])} | {format_pct(a['loss_fraction'])} | {a['fair_decimal_structural']:.3f} |")
    A+=['','### 4. Lettura e limiti','',
        '**Lettura:** le quote descrivono la gerarchia degli scenari prezzati da Bet365; gli scenari congiunti provengono da un unico modello BISE. Una discrepanza fra mercati non dimostra che Bet365 stia sbagliando.','',
        '**Confidence:** solo probabilità e supporto storico esplorativo per mercati singoli; i risultati esatti e gli scenari congiunti non sono calibrati separatamente.','',
        '**Rischi:** nessuna esposizione interna del bookmaker è osservabile; nessun vantaggio economico o ROI certificato. La provenienza closing Bet365 è confermata dall’utente, non da timestamp indipendenti.','',
        '---','']
    return '\n'.join(A)


def render_md(results,errors, manifest):
    a=['# BETPILOT | BOOKMAKER ANALYZER 2.0','## RC15 — Final Protocol & Intelligence Report','',
       '**Scopo:** lettura descrittiva delle quote Bet365, probabilità sperimentali calibrate per mercati singoli, scenari BISE e diagnostica della coerenza.','',
       f"**Partite analizzate:** {len(results)} · **Record respinti:** {len(errors)} · **BISE congelato:** `{FREEZE_SHA256[:16]}…`.",'',
       '> Le stime 1X2/O/U/GG calibrate e la distribuzione BISE strutturale sono rappresentazioni distinte: non combinarle in una singola distribuzione senza nuova calibrazione.','']
    for x in results:a.append(render_match_md(x))
    if errors:
        a+=['## Record esclusi','']
        for e in errors:a.append(f"- Riga {e['line']}: {e['error']}")
    a+=['','**Stato:** RC15 operativo. Validazione prospettica sospesa su richiesta dell’utente; vantaggio predittivo addizionale BISE molto piccolo sullo storico e non dimostrato nelle BIG5; nessuna raccomandazione automatica di puntata.','']
    return '\n'.join(a)


def render_html(results,errors,manifest):
    style='''
    :root{--ink:#16263a;--muted:#657487;--line:#dce5ed;--bg:#f3f6fa;--card:#fff;--navy:#16324a;--teal:#147d78}
    *{box-sizing:border-box}body{background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0}
    .wrap{max-width:1080px;margin:0 auto;padding:32px 22px 60px}h1{font-size:28px;margin:8px 0 4px;letter-spacing:-.7px}h2{font-size:22px;margin:0}h3{font-size:16px;margin:26px 0 12px}
    .eyebrow{letter-spacing:2px;text-transform:uppercase;color:var(--teal);font-size:11px;font-weight:800}.sub{color:var(--muted);margin:0 0 24px}
    .panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;margin:17px 0;box-shadow:0 4px 14px #233e5310}
    .grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.tile{border:1px solid var(--line);border-radius:10px;padding:14px;background:#fafcfd}
    .lab{color:var(--muted);font-size:12px}.num{font-size:24px;font-weight:750;font-variant-numeric:tabular-nums}.small{color:var(--muted);font-size:12px}
    table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}th,td{border-bottom:1px solid var(--line);padding:9px;text-align:left}th{font-size:12px;color:var(--muted);font-weight:650;background:#fafcfd}td:last-child,th:last-child{text-align:right}
    .split{display:grid;grid-template-columns:1fr 1fr;gap:20px}.pill{display:inline-block;border-radius:99px;padding:4px 10px;color:var(--teal);background:#e6f4f1;font-size:12px;font-weight:700}
    .warn{border-left:3px solid #c38d3c;padding-left:14px;color:#6b5328}.note{font-size:12px;color:var(--muted)}.bar{height:8px;border-radius:4px;background:#e5edf1;overflow:hidden}.fill{height:100%;background:var(--teal)}
    @media(max-width:700px){.grid,.split{grid-template-columns:1fr}body{font-size:14px}.wrap{padding:17px 12px}h1{font-size:23px}}
    @media print{body{background:white}.wrap{max-width:none}.panel{box-shadow:none;break-inside:avoid}}
    '''
    body=['<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
          '<title>BETPILOT | Bookmaker Analyzer 2.0 · RC15</title><style>'+style+'</style></head><body><main class="wrap">',
          '<div class="eyebrow">BETPILOT / PREMATCH / RC15</div><h1>Bookmaker Analyzer 2.0</h1>',
          '<p class="sub">Final Intelligence Report · quote Bet365 e scenari strutturali · '+str(len(results))+' partite elaborate</p>',
          '<div class="panel"><b>Protocollo</b><p>Le probabilità dei mercati singoli sono riportate separatamente dalla matrice BISE dei punteggi. La scala 0–10 equivale a probabilità × 10: non è una misura indipendente dell’affidabilità.</p><p class="note">Provenienza Bet365 closing attestata dall’utente; timestamp prezzo non verificati autonomamente. Nessun edge, ROI o vantaggio predittivo certificato.</p></div>']
    for x in results:
        idt=x['identity'];q=x['single_market_probabilities_RC12_experimental'];b=x['bise_structural'];dg=x['data_quality']
        def card(label,key,probs):
            return '<div class="tile"><div class="lab">'+H(label)+'</div><div class="num">'+H(key)+' · '+format_pct(probs[key])+'</div><div class="small">Intensità '+f'{10*probs[key]:.1f}'+'/10 (probabilità)</div></div>'
        body+=['<section class="panel"><div class="eyebrow">'+H(idt['league'])+' · '+H(idt['match_day'] or 'data non specificata')+'</div>',
               '<h2>'+H(idt['home'])+' – '+H(idt['away'])+'</h2>',
               '<p class="sub">Gruppo '+H(dg['scope'])+' · '+H(describe_diagnostic(dg['diagnostic']))+'</p>',
               '<div class="grid">'+card('1X2',q['1X2']['favourite'],q['1X2']['p'])+card('Over/Under 2.5',q['OU2_5']['favourite'],q['OU2_5']['p'])+card('GG/NG',q['BTTS']['favourite'],q['BTTS']['p'])+'</div>',
               '<h3>Gerarchia dei prezzi e probabilità sperimentali</h3><table><tr><th>Esito</th><th>Probabilità</th><th>Supporto fascia (n)</th></tr>']
        for k in ('1','X','2','1X','X2','12','Over_2_5','Under_2_5','GG','NG'):
            typ='1X2' if k in q['1X2']['p'] else ('OU2_5' if k in q['OU2_5']['p'] else 'BTTS')
            p=q[typ]['p'][k]
            sup=x['historical_support']['GG_NG' if k in ('GG','NG') else k]
            body.append('<tr><td>'+H(k.replace('_',' '))+'</td><td>'+format_pct(p)+'</td><td>'+f"{sup['n']:,}"+'</td></tr>')
        body+=['</table><div class="split"><div><h3>Scenari BISE esclusivi — primi sei</h3><table><tr><th>Scenario</th><th>Prob.</th></tr>']
        for s in b['top_6_mutually_exclusive_scenarios']:
            body.append('<tr><td>'+H(s['event'])+'</td><td>'+format_pct(s['p'])+'</td></tr>')
        body.append('</table></div><div><h3>Punteggi esatti BISE — top 5</h3><table><tr><th>Risultato</th><th>Prob.</th></tr>')
        for s in b['top_5_exact_scores']:
            body.append('<tr><td>'+H(s['score'])+'</td><td>'+format_pct(s['prob'])+'</td></tr>')
        body+=['</table><p class="small">Punteggi e combinazioni non calibrati individualmente.</p></div></div>',
               '<h3>Scenari sovrapposti (non sommabili)</h3><div class="grid">']
        for sc in b['top_8_overlapping_scenarios'][:6]:
            body.append('<div class="tile"><div class="lab">'+H(sc['event'])+'</div><div class="num">'+format_pct(sc['p'])+'</div><div class="bar"><div class="fill" style="width:'+str(round(sc['p']*100,2))+'%"></div></div></div>')
        body+=['</div><h3>Confronto mercati supplementari</h3>']
        gaps=x['optional_market_diagnostics']['over_under_gaps']
        if gaps:
            body+=['<table><tr><th>Mercato</th><th>Bet365</th><th>BISE</th><th>Gap</th><th>p95 pre-2025</th></tr>']
            for g in gaps:body.append('<tr><td>'+H(g['market'])+'</td><td>'+format_pct(g['p_bet365'])+'</td><td>'+format_pct(g['p_bise_structural'])+'</td><td>'+pp(g['gap_pp'])+'</td><td>'+str(round(g['train_abs_gap_p95_pp'],2))+' pp</td></tr>')
            body.append('</table>')
        else:body.append('<p class="small">Non forniti: il controllo esteso non è disponibile.</p>')
        body.append('<p class="note">BISE GG '+format_pct(b['GG'])+' · differenza da Bet365 '+pp(b['GG_direct_gap_pp'])+' · gol modello casa '+f"{b['lambda_home']:.2f}"+' / ospite '+f"{b['lambda_away']:.2f}"+'</p>')
        ah=x['optional_market_diagnostics']['asian_handicap']
        if ah:
            body.append('<h3>Handicap asiatico · payout ponderati</h3><table><tr><th>Lato</th><th>Quota</th><th>Vincita</th><th>Rimborso</th><th>Perdita</th></tr>')
            for a in ah:body.append('<tr><td>'+H(a['side'])+' '+f"{a['home_handicap_line']:+.2f}"+'</td><td>'+f"{a['offered_bookmaker_decimal']:.3f}"+'</td><td>'+format_pct(a['win_fraction'])+'</td><td>'+format_pct(a['push_fraction'])+'</td><td>'+format_pct(a['loss_fraction'])+'</td></tr>')
            body.append('</table>')
        body.append('<p class="warn">Convergenza e discrepanze fra quote Bet365 non forniscono segnali indipendenti. Le probabilità non rivelano le esposizioni interne del bookmaker.</p></section>')
    if errors:
        body+=['<section class="panel"><h3>Record scartati</h3>']
        for e in errors:body.append('<p>Riga '+str(e['line'])+': '+H(e['error'])+'</p>')
        body.append('</section>')
    body+=['<p class="note">RC15 — BETPILOT | PREMATCH. Modello BISE congelato. Validazione prospettica sospesa. Dati storici selezionati da LivePick: non rappresentativi automaticamente di tutte le partite.</p></main></body></html>']
    return ''.join(body)


def run(input_file, output_dir):
    input_file=Path(input_file).resolve(); output_dir=Path(output_dir).resolve();output_dir.mkdir(parents=True,exist_ok=True)
    model,reference,stats=verify_inputs()
    with input_file.open(newline='',encoding='utf-8-sig') as stream:
        reader=csv.DictReader(stream)
        missing=[k for k in REQUIRED if k not in (reader.fieldnames or [])]
        if missing:raise ValueError('MISSING_COLUMNS: '+', '.join(missing))
        rows=list(reader)
    if not rows:raise ValueError('EMPTY_INPUT')
    results=[];errors=[];seen=set()
    for n,row in enumerate(rows,start=2):
        try:
            key=(row['league'].strip().casefold(),row['home'].strip().casefold(),row['away'].strip().casefold(),(row.get('match_day') or '').strip())
            if key in seen:raise ValueError('DUPLICATE_MATCH_IN_BATCH')
            seen.add(key)
            raw=diagnose(row,model,reference,stats)
            results.append(summarise(raw,reference))
        except Exception as e:
            errors.append({'line':n,'error':str(e)})
    manifest={'version':VERSION,'input_file':input_file.name,
              'input_sha256':hash_file(input_file),
              'frozen_bise_sha256':hash_file(ROOT/'rc8_bise_frozen_source.py'),
              'model_sha256':hash_file(ROOT/'RC12_Fitted_Calibrators.json'),
              'statistics_sha256':hash_file(ROOT/'RC14_Statistics.json'),
              'analyzed':len(results),'rejected':len(errors),'total_input':len(rows),
              'fitted_models_modified':False,'forward_validated':False}
    (output_dir/'RC15_Analysis.json').write_text(json.dumps({'schema':VERSION,'reports':results,'errors':errors},ensure_ascii=False,indent=2),encoding='utf8')
    (output_dir/'RC15_Intelligence_Report.md').write_text(render_md(results,errors,manifest),encoding='utf8')
    (output_dir/'RC15_Intelligence_Report.html').write_text(render_html(results,errors,manifest),encoding='utf8')
    (output_dir/'RC15_Run_Manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf8')
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,help='CSV con sette quote Bet365 e campionato/nomi')
    parser.add_argument('--output-dir',required=True,help='Cartella report')
    args=parser.parse_args();print(json.dumps(run(args.input,args.output_dir),indent=2))
