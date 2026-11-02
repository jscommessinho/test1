#!/usr/bin/env python3
"""BETPILOT | PREMATCH | Bookmaker Analyzer RC13 Scenario Intelligence.

Structural joint probabilities from the UNMODIFIED RC9 Poisson/DC score grid.
DO NOT combine separately-calibrated RC12 marginals as if independent.
Asian handicap treats quarter-lines as split bets with half wins/losses.
No ROI, predictive edge, exposure or profit claims are certified.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from rc11_operational import analyze as frozen_analyze, score_distribution, BIG5
from rc12_multimarket import analyze as rc12_analyze

ROOT=Path(__file__).resolve().parent
FROZEN_BISE_SHA256='666af5396bc760dd7b7296ea028f5aeaef8ecd4eb123fd56277f4764232cd8df'
REQUIRED={'bookmaker','league','home','away','q1','qx','q2','qo','qu','qgg','qng'}
OPTIONAL_AH=('ah_line_home','ah_home_odds','ah_away_odds')
OPTIONAL_OU=('over15','under15','over35','under35')


def sha256(p:Path):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''):h.update(chunk)
    return h.hexdigest()


def line_split(line:float):
    """Return full, half or quarter Asian handicap components."""
    if not math.isfinite(line) or abs(line)>5 or abs(line*4-round(line*4))>1e-8:
        raise ValueError('Handicap in quarti di gol, intervallo da -5 a +5')
    q=int(round(line*4))
    if abs(q)%2==0: return (line,)   # .0 or .5
    return ((q-1)/4,(q+1)/4)         # e.g -0.25 -> -0.5 and 0


def ah_settlement(matrix:np.ndarray, home_line:float, side:str):
    """Per-unit stake expected fractions W/P/L for the supplied home handicap line.

    side='home': score home-away+home_line.
    side='away': score away-home-home_line.
    """
    if side not in ('home','away'):raise ValueError('side must be home or away')
    i,j=np.indices(matrix.shape)
    delta=(i-j) if side=='home' else (j-i)
    line=home_line if side=='home' else -home_line
    comps=line_split(line)
    w=np.zeros_like(matrix,dtype=float); p=np.zeros_like(matrix,dtype=float); l=np.zeros_like(matrix,dtype=float)
    for part in comps:
        result=delta+part
        w+=(result>1e-10)/len(comps)
        p+=(np.abs(result)<=1e-10)/len(comps)
        l+=(result<-1e-10)/len(comps)
    W=float(np.sum(matrix*w));P=float(np.sum(matrix*p));L=float(np.sum(matrix*l))
    if not abs(W+P+L-1)<1e-8:raise AssertionError('Settlement non conserva la massa')
    # A push refunds stake: at price q net payoff is (q-1)*W-L.
    fair=None if W<=1e-14 else 1+L/W
    return {'home_handicap_line':home_line,'side':side,'win_fraction':W,'push_fraction':P,
            'loss_fraction':L,'fair_decimal_structural':fair,
            'note':'Fractional W/P/L includes quarter-line half settlements; fair odds are model-conditional, not a value bet.'}


def _optional_number(row,name):
    v=str(row.get(name,'')).strip()
    return None if not v else float(v)


def analyze(row,model,reference):
    if str(row.get('bookmaker','')).lower().replace(' ','') not in ('bet365','bet365.it','bet365.com'):
        raise ValueError('Bookmaker non Bet365')
    old=frozen_analyze(row)
    cal=rc12_analyze(row,model,reference)
    b=old['bise']
    mat,t=score_distribution(b['lambda_home'],b['lambda_away'],old['market_probs']['1X2']['X'])
    I,J=np.indices(mat.shape)
    tot=I+J
    outcome=np.where(I>J,'1',np.where(I==J,'X','2'))
    gg=(I>0)&(J>0)
    over=tot>=3
    atomic={}
    for result in ('1','X','2'):
        for bt in ('GG','NG'):
            for goals in ('Over2.5','Under2.5'):
                mask=(outcome==result)&(gg if bt=='GG' else ~gg)&(over if goals=='Over2.5' else ~over)
                atomic[result+' & '+bt+' & '+goals]=float(mat[mask].sum())
    s=sum(atomic.values())
    if abs(s-1)>1e-8:raise AssertionError('12 atomic states must sum 100%')
    marginal={
        '1':float(mat[(outcome=='1')].sum()),'X':float(mat[(outcome=='X')].sum()),'2':float(mat[(outcome=='2')].sum()),
        'GG':float(mat[gg].sum()),'NG':float(mat[~gg].sum()),
        'Over2.5':float(mat[over].sum()),'Under2.5':float(mat[~over].sum()),
        '1X':float(mat[(outcome!='2')].sum()),'X2':float(mat[(outcome!='1')].sum()),
        '12':float(mat[(outcome!='X')].sum()),
        'Over1.5':float(mat[tot>=2].sum()),'Under1.5':float(mat[tot<2].sum()),
        'Over3.5':float(mat[tot>=4].sum()),'Under3.5':float(mat[tot<4].sum()),
    }
    joint={
        '1 & GG':float(mat[(outcome=='1')&gg].sum()),
        'X & GG':float(mat[(outcome=='X')&gg].sum()),
        '2 & GG':float(mat[(outcome=='2')&gg].sum()),
        '1 & NG':float(mat[(outcome=='1')&~gg].sum()),
        'X & Under2.5':float(mat[(outcome=='X')&~over].sum()),
        'X & Over2.5':float(mat[(outcome=='X')&over].sum()),
        'X2 & Over2.5':float(mat[(outcome!='1')&over].sum()),
        '1X & GG':float(mat[(outcome!='2')&gg].sum()),
        'GG & Over2.5':float(mat[gg&over].sum()),
        'NG & Under2.5':float(mat[~gg&~over].sum()),
        '1 & Over2.5':float(mat[(outcome=='1')&over].sum()),
        '2 & Under2.5':float(mat[(outcome=='2')&~over].sum()),
    }
    # A benchmark illustrating why one must NOT multiply dependent markets.
    dependence={
        'event':'GG & Over2.5', 'joint_model':joint['GG & Over2.5'],
        'product_if_independent_WRONG':marginal['GG']*marginal['Over2.5'],
        'difference_pp':100*(joint['GG & Over2.5']-marginal['GG']*marginal['Over2.5'])
    }
    # Internal checks for mutually exclusive / nested events and direct de-vig constraints.
    checks={
        'atomic_partition':abs(s-1)<1e-8,
        'complement_1x2':abs(marginal['1']+marginal['X']+marginal['2']-1)<1e-8,
        'double_chance':abs(marginal['1X']-marginal['1']-marginal['X'])<1e-8,
        'over_monotonic':marginal['Over1.5']+1e-9>=marginal['Over2.5']>=marginal['Over3.5']-1e-9,
        'GG_implies_over15':marginal['GG']<=marginal['Over1.5']+1e-9,
        'GG_over25_exact_identity':abs(joint['GG & Over2.5']-(marginal['GG']-float(mat[1,1])))<1e-8,
        'GG_reconstruct':abs(marginal['GG']-b['p_GG_reconstructed'])<1e-8,
        '1X2_quoted':max(abs(marginal[k]-old['market_probs']['1X2'][k]) for k in ('1','X','2'))<1e-7,
        'OU25_quoted':abs(marginal['Over2.5']-old['market_probs']['OU2_5']['over'])<1e-7,
    }
    if not all(checks.values()):raise AssertionError('Scenari incoerenti: '+str(checks))
    # Optional independently-priced Over1.5/3.5: compare like with like, NEVER treat its odds as BISE input.
    optional=[]
    for label,over_name,under_name in [('Over1.5','over15','under15'),('Over3.5','over35','under35')]:
        a=_optional_number(row,over_name); c=_optional_number(row,under_name)
        if (a is None)!=(c is None):raise ValueError('Coppia di quote '+label+' incompleta')
        if a is not None:
            from rc11_operational import normalize
            ps,vig=normalize([a,c])
            optional.append({'market':label,'p_bookmaker_de_vig':ps[0],'p_bise_structural':marginal[label],
                             'gap_pp':100*(marginal[label]-ps[0]),'vig':vig,'note':'Diagnostic gap; not an independent prediction edge.'})
    # Optional AH; model without the optional market quotes, prices used ONLY for diagnostic comparison.
    ah=[]
    v=[_optional_number(row,k) for k in OPTIONAL_AH]
    if any(x is not None for x in v) and not all(x is not None for x in v):
        raise ValueError('Handicap asiatico: servono linea casa e entrambe le quote')
    if all(x is not None for x in v):
        line,qh,qa=v
        if min(qh,qa)<=1 or max(qh,qa)>=1000:raise ValueError('Quote AH non valide')
        for side,price in [('home',qh),('away',qa)]:
            profile=ah_settlement(mat,line,side)
            profile['offered_bookmaker_decimal']=price
            profile['structural_net_at_quoted_price']=(price-1)*profile['win_fraction']-profile['loss_fraction']
            profile['use_for_betting_recommendation']=False
            ah.append(profile)
    # Reference handicap profiles do not require independently quoted AH prices.
    examples=[{side:ah_settlement(mat,ln,side) for side in ('home','away')} for ln in (-1,-.75,-.5,-.25,0,.25,.5,.75,1)]
    # Exclude optional sources from the word 'independent': quote patterns are correlated through same bookmaker.
    result={
        'schema':'RC13_SCENARIO_INTELLIGENCE_V1', 'partita':old['partita'], 'source':'Bet365 closing; user-attested provenance',
        'bise_unmodified':b,'rc12_confidence_unmodified':cal['probabilita_e_intensita_0_10'],
        'rc12_scope_and_methods':{'scope':cal['scope'],'1X2':cal['1X2']['method'],'OU2_5':cal['OU2_5']['method']},
        'market_direct':old['market_probs'],
        'structural_joint_atomic_12_states':dict(sorted(atomic.items(),key=lambda kv:-kv[1])),
        'structural_marginals':marginal, 'structural_joint_scenarios':dict(sorted(joint.items(),key=lambda kv:-kv[1])),
        'structural_exact_scores':old['exact_scores_from_BISE'],
        'independence_diagnostic':dependence,'cross_market_checks':checks,
        'optional_direct_ou_diagnostics':optional,'optional_AH_comparison':ah,
        'AH_reference_profiles':examples,
        'interpretation':{
            'bookmaker_leader_1X2':old['market_hierarchy']['1X2_leader'],
            'gg_discrepancy_pp':old['bise']['gap_pp'],
            'gg_gap_percentile':old['bise']['gap_percentile_band'],
            'distinct_information':'Quoted 1X2, O/U and GG/NG are related; BISE conditional scenarios are derived, not new independent signals.',
            'confidence':'Only RC12 calibrated/direct single-market frequencies. Joint scenarios and correct scores NOT empirically calibrated.',
            'asian_handicap':'Full and half settlements are explicit. AH price comparison is structural and not a proven fair edge.'
        }
    }
    return result


def write_report(results,errors):
    lines=['# BETPILOT | Bookmaker Analyzer 2.0 — RC13 Scenario Intelligence','',
           '**Stato:** operativo descrittivo. BISE RC9 e RC12 non modificati; nessuna nuova Confidence certificata.','',
           f'Partite analizzate: **{len(results)}** · escluse: **{len(errors)}**.','']
    for r in results:
        g=r['partita'];s=r['structural_joint_scenarios'];c=r['cross_market_checks'];d=r['independence_diagnostic']
        lines +=[f"## {g['home']} – {g['away']} ({g['league']})",'',
                 f"**Gerarchia 1X2:** favorito {r['interpretation']['bookmaker_leader_1X2']}. **Gap GG BISE−Bet365:** {r['interpretation']['gg_discrepancy_pp']:+.2f} pp.",
                 '', '### I sei scenari atomici più probabili (mutuamente esclusivi)','',
                 '| Scenario 1X2 + GG/NG + O/U2.5 | Probabilità |','|---|---:|']
        for name,p in list(r['structural_joint_atomic_12_states'].items())[:6]:lines.append(f'| {name} | {p*100:.2f}% |')
        lines +=['','### Scenari congiunti sovrapposti (NON sommare le percentuali)','',
                 '| Scenario | Probabilità |','|---|---:|']
        for name,p in list(s.items())[:10]:lines.append(f'| {name} | {p*100:.2f}% |')
        lines +=['', '**Risultati esatti più probabili:** '+', '.join(f"{a['score']}: {a['prob']*100:.2f}%" for a in r['structural_exact_scores'][:5]),
                '',f"**Dipendenza fra mercati:** {d['event']} = {d['joint_model']*100:.2f}%; moltiplicazione indipendente errata = {d['product_if_independent_WRONG']*100:.2f}%; errore {d['difference_pp']:+.2f} pp.",
                '', f"**Controlli matematici:** {sum(c.values())}/{len(c)} superati."]
        if r['optional_direct_ou_diagnostics']:
            lines+=['','**Prezzi alternativi O/U (diagnosi):** '+', '.join(f"{x['market']} {x['gap_pp']:+.2f} pp" for x in r['optional_direct_ou_diagnostics'])]
        if r['optional_AH_comparison']:
            lines+=['','### Handicap asiatico quotato', '', '| Lato / Linea casa | % vincita ponderata | % rimborso ponderato | % perdita ponderata | Quota Bet365 | Quota strutturale |','|---|---:|---:|---:|---:|---:|']
            for a in r['optional_AH_comparison']:
                lines +=[f"| {a['side']} / {a['home_handicap_line']:+.2f} | {a['win_fraction']*100:.2f}% | {a['push_fraction']*100:.2f}% | {a['loss_fraction']*100:.2f}% | {a['offered_bookmaker_decimal']:.3f} | {a['fair_decimal_structural']:.3f} |"]
        lines+=['','*Scenari derivati dalla stessa distribuzione dei risultati: non sono segnali indipendenti. La Confidence dei mercati singoli NON si trasferisce ai congiunti. Nessuna stima delle esposizioni del bookmaker dalle sole quote.*','']
    if errors:
        lines+=['## Record scartati', '']
        for er in errors:lines.append(f"- Riga {er['row']}: {er['reason']}")
    return '\n'.join(lines)+'\n'


def run(input_path,output_dir):
    input_path=Path(input_path);out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
    model=json.loads((ROOT/'RC12_Fitted_Calibrators.json').read_text())
    ref=json.loads((ROOT/'RC12_Reliability_Reference.json').read_text())
    if model['source_sha256']!=ref['source_sha256']:raise RuntimeError('Hash RC12 model/reference discordanti')
    if sha256(ROOT/'rc8_bise_frozen_source.py') != FROZEN_BISE_SHA256:
        raise RuntimeError('Codice BISE RC9 congelato risulta modificato')
    if sha256(ROOT/'rc9_frozen_protocol.json')!=model['freeze_rc9_sha256']:
        raise RuntimeError('RC9 freeze protocol alterato')
    with input_path.open(newline='',encoding='utf-8-sig') as f:
        rd=csv.DictReader(f)
        if not REQUIRED.issubset(rd.fieldnames or []):raise ValueError('Colonne mancanti: '+str(REQUIRED-set(rd.fieldnames or [])))
        rows=list(rd)
    reports=[];errors=[]
    for idx,row in enumerate(rows,start=2):
        try:reports.append(analyze(row,model,ref))
        except Exception as exc:errors.append({'row':idx,'reason':str(exc)})
    (out/'RC13_Analysis.json').write_text(json.dumps({'results':reports,'errors':errors},ensure_ascii=False,indent=2))
    (out/'RC13_Report.md').write_text(write_report(reports,errors),encoding='utf-8')
    manifest={'schema':'RC13_REPORT_V1','input_sha256':sha256(input_path),
              'frozen_bise_sha256':sha256(ROOT/'rc8_bise_frozen_source.py'),
              'frozen_rc9_sha256':sha256(ROOT/'rc9_frozen_protocol.json'),
              'rc12_parameters_sha256':sha256(ROOT/'RC12_Fitted_Calibrators.json'),
              'n_processed':len(reports),'n_rejected':len(errors),
              'report_sha256':sha256(out/'RC13_Report.md'),'analysis_sha256':sha256(out/'RC13_Analysis.json')}
    (out/'RC13_Manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False))
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output-dir',required=True)
    a=p.parse_args();print(json.dumps(run(a.input,a.output_dir),indent=2))
