#!/usr/bin/env python3
"""BETPILOT | PREMATCH Bookmaker Analyzer 2.0 — RC11 operational, descriptive.

Frozen BISE RC9 unchanged. Outputs bookmaker probability hierarchy, independent
market reconstruction, modeled score distribution and provenance-aware caveats.
No confidence certification, automatic betting recommendations or EV claims.
"""
import argparse, csv, hashlib, json, math, re
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from scipy.special import expit, logit
from scipy.stats import poisson
from rc8_bise_frozen_source import core

HERE=Path(__file__).resolve().parent
FREEZE=json.loads((HERE/'rc9_frozen_protocol.json').read_text())
REQUIRED=['league','home','away','q1','qx','q2','qo','qu','qgg','qng']
ODDS=['q1','qx','q2','qo','qu','qgg','qng']
BIG5={
    'Italy Serie A','England Premier League','Spain La Liga',
    'France Ligue 1','Germany Bundesliga I'
}
# Descriptive, not forecasting, historical absolute gap percentiles.
# Source RC8 frozen checkpoint, training through Dec 2024 only.
BENCHMARK={
  'big5':{'n':5978,'median_pp':0.686980240757562,'p90_pp':1.6822978427841515,'p95_pp':2.0129455937319576,'p99_pp':2.796901441071279},
  'other':{'n':340926,'median_pp':2.3610345955166645,'p90_pp':4.699852214420175,'p95_pp':5.441761254314645,'p99_pp':7.7351979251670535}
}
BANNED_SUBSTRINGS=('futsal','efootball','e-soccer','esoccer','beach soccer','kings league',
                    'queens league','socca','star sixes','baller league','indoor soccer')
# CSV is a header + one competition per row; ignore empty lines/whitespace.
BANNED_EXACT={s.strip().lower() for s in (HERE/'blacklist_integrale.csv').read_text(encoding='utf-8-sig').splitlines()[1:] if s.strip()}


def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def normalize(odds):
    a=np.array(odds,dtype=float)
    if not np.all(np.isfinite(a)) or np.any(a<=1.) or np.any(a>=1000):
        raise ValueError('Quote decimali mancanti o non ammissibili (1 < quota < 1000)')
    inv=1/a
    overround=float(inv.sum()-1)
    if not (-0.025 <= overround <= 0.30):
        raise ValueError(f'Overround non ammissibile: {overround:.2%}')
    return (inv/inv.sum()).tolist(),overround


def is_nonstandard(league):
    txt=league.strip().lower()
    return (txt in BANNED_EXACT or any(b in txt for b in BANNED_SUBSTRINGS)
            or re.search(r'\b\d+\s*mins?\s*play\b',txt) is not None)


def score_distribution(lh,la,pdraw):
    """Unconditional score grid with Dixon-Coles score-cell correction (additive t)."""
    if min(lh,la)<0 or not np.isfinite(lh+la):
        raise ValueError('Lambda non ammissibili')
    n=60
    hp=poisson.pmf(np.arange(n+1),lh)
    ap=poisson.pmf(np.arange(n+1),la)
    mat=np.outer(hp,ap)
    pdraw_base=float(np.sum(hp*ap))
    t=(pdraw_base-pdraw)/2
    mat[0,0]-=t
    mat[1,1]-=t
    mat[1,0]+=t
    mat[0,1]+=t
    if mat.min() < -1e-8:
        raise ValueError('Correzione Dixon-Coles produce punteggi negativi')
    if abs(float(mat.sum())-1)>2e-8:
        raise ValueError('Massa di probabilità troncata non trascurabile')
    mat=np.maximum(mat,0)
    return mat,t


def score_prob(mat,cond):
    ii,jj=np.indices(mat.shape)
    return float(mat[cond(ii,jj)].sum())


def analyze(row):
    vals={k:str(row.get(k,'')).strip() for k in REQUIRED}
    if any(not vals[k] for k in ('league','home','away')):
        raise ValueError('Necessari campionato, casa, trasferta')
    bookmaker=str(row.get('bookmaker','Bet365')).strip().lower().replace(' ','')
    if bookmaker not in ('bet365','bet365.com','bet365.it'):
        raise ValueError('Bookmaker deve essere Bet365')
    if is_nonstandard(vals['league']):
        raise ValueError('Competizione non standard, esclusa dalla ricerca')
    num={k:float(vals[k]) for k in ODDS}
    p1,v1=normalize([num[x] for x in ['q1','qx','q2']])
    po,vo=normalize([num[x] for x in ['qo','qu']])
    pb,vb=normalize([num[x] for x in ['qgg','qng']])
    btts,flag,lt,lh,la=core(float(p1[0]),float(p1[1]),float(po[0]))
    if flag or not np.isfinite(btts):
        raise ValueError(f'BISE non identificabile con queste quote (flag={flag}). Nessuna previsione forzata')
    mat,t=score_distribution(float(lh),float(la),float(p1[1]))
    pmat=score_prob(mat,lambda i,j:(i>0)&(j>0))
    ph=score_prob(mat,lambda i,j:i>j)
    px=score_prob(mat,lambda i,j:i==j)
    pa=score_prob(mat,lambda i,j:i<j)
    pov=score_prob(mat,lambda i,j:i+j>=3)
    if max(abs(pmat-btts),abs(ph-p1[0]),abs(px-p1[1]),abs(pa-p1[2]),abs(pov-po[0])) > 1e-7:
        raise ValueError('Controllo di ricostruzione DC non superato')
    top=[]
    # Scorelines are model-conditioned, not calibrated exact-score forecasts.
    for i,j in sorted(np.ndindex(mat.shape),key=lambda ij:mat[ij],reverse=True)[:8]:
        top.append({'score':f'{i}-{j}','prob':round(float(mat[i,j]),6)})
    gap=float(btts-pb[0]);agap=abs(gap)*100
    grp='big5' if vals['league'] in BIG5 else 'other';bench=BENCHMARK[grp]
    if agap>=bench['p99_pp']:rarity='≥99° percentile storico'
    elif agap>=bench['p95_pp']:rarity='95°–99° percentile storico'
    elif agap>=bench['p90_pp']:rarity='90°–95° percentile storico'
    else:rarity='entro il 90° percentile storico'
    implied={'1':p1[0],'X':p1[1],'2':p1[2]}
    leader=max(implied,key=implied.get)
    # Diagnostic calibration models: neither output is a certified fair price.
    lm=float(logit(np.clip(pb[0],0.00001,0.99999)))
    cal_market=FREEZE['frozen_model']['market']
    cal_gap=FREEZE['frozen_model']['market_gap']
    pmarketcal=float(expit(cal_market['intercept']+cal_market['coefficients'][0]*lm))
    pgapcal=float(expit(cal_gap['intercept']+cal_gap['coefficients'][0]*lm+cal_gap['coefficients'][1]*10*gap))
    scenario={
        '1X':ph+px,'X2':px+pa,'12':ph+pa,
        'Over_1_5':score_prob(mat,lambda i,j:i+j>=2),
        'Over_2_5':pov,
        'Over_3_5':score_prob(mat,lambda i,j:i+j>=4),
        'GG_and_Over_2_5':score_prob(mat,lambda i,j:(i>0)&(j>0)&(i+j>=3)),
        'home_win_and_GG':score_prob(mat,lambda i,j:(i>j)&(j>0)),
        'away_win_and_GG':score_prob(mat,lambda i,j:(i<j)&(i>0)),
        'home_clean_sheet':score_prob(mat,lambda i,j:j==0),
        'away_clean_sheet':score_prob(mat,lambda i,j:i==0)
    }
    return {
      'partita':{'league':vals['league'],'home':vals['home'],'away':vals['away'],'source':'Bet365 — attestato dall’utente / dataset LivePick',
                 'date':str(row.get('match_day','')).strip(), 'analysis_kind':str(row.get('analysis_kind','non_specificato')).strip()},
      'input_odds':num,
      'market_probs':{'1X2':implied,'OU2_5':{'over':po[0],'under':po[1]},'BTTS':{'GG':pb[0],'NG':pb[1]}},
      'vig':{'1X2':v1,'OU2_5':vo,'BTTS':vb},
      'market_hierarchy':{'1X2_leader':leader,'1X2_leader_prob':implied[leader],
                          'OU2_5_leader':'Over' if po[0]>po[1] else ('Under' if po[0]<po[1] else 'Pari'),
                          'BTTS_leader':'GG' if pb[0]>pb[1] else ('NG' if pb[0]<pb[1] else 'Pari')},
      'bise':{'lambda_home':float(lh),'lambda_away':float(la),'lambda_total':float(lt),'dc_t':float(t),
              'p_GG_reconstructed':float(btts),'p_GG_market':float(pb[0]),'gap_pp':gap*100,
              'abs_gap_pp':agap, 'gap_benchmark_group':grp,
              'gap_benchmark_ref_years':'2020–2024','gap_reference_n':bench['n'],
              'gap_percentile_band':rarity,'gap_reference_p95_pp':bench['p95_pp'],
              'interpretation':'Scostamento fra mercati; NON è un vantaggio predittivo o value bet dimostrato'},
      'scenarios_from_BISE':scenario,
      'exact_scores_from_BISE':top,
      'calibration_research_only':{'p_GG_market_calibrated_global':pmarketcal,'p_GG_market_plus_BISE_global':pgapcal,
        'note':'Solo confronto sperimentale. Su BIG5 il correttore BISE globale peggiorava il Brier nel test 2025–26; non usarlo come raccomandazione.'},
      'status':{'descriptive_ready':True,'predictive_confidence_certified':False,'value_bet_detected':False,
                'forward_validated':False,'model_version':FREEZE['version']}
    }


def pct(x):return f'{x*100:.2f}%'


def md_report(reports,errors,input_name):
    out=['# BETPILOT | BOOKMAKER ANALYZER 2.0 — RC11',
         '', '**Modalità**: lettura operativa e descrittiva delle quote Bet365; BISE congelato in RC9. Nessuna probabilità di profitto certificata.',
         '',f'**Sorgente elaborata:** `{input_name}`; **partite valide**: {len(reports)}; **scartate**: {len(errors)}.',
         '']
    for r in reports:
        m=r['partita'];b=r['bise'];q=r['market_probs'];s=r['scenarios_from_BISE']
        out.extend([f"## {m['home']} – {m['away']} ({m['league']})",'',
          f"**Mercato 1X2 (de-vig):** 1 {pct(q['1X2']['1'])} · X {pct(q['1X2']['X'])} · 2 {pct(q['1X2']['2'])} — preferito: **{r['market_hierarchy']['1X2_leader']}**.",
          f"**O/U 2.5:** Over {pct(q['OU2_5']['over'])} · Under {pct(q['OU2_5']['under'])}.",
          f"**GG/NG:** GG {pct(q['BTTS']['GG'])} · NG {pct(q['BTTS']['NG'])}.",
          f"**BISE GG ricostruito:** {pct(b['p_GG_reconstructed'])}; **Δ BISE − quota GG:** {b['gap_pp']:+.2f} punti percentuali.",
          f"**Rarità storica della discrepanza:** {b['gap_percentile_band']} ({b['gap_benchmark_group']}, n={b['gap_reference_n']:,}, benchmark 2020–24).",'',
          '| Scenario condizionato a BISE | Probabilità |','|---|---:|',
          f"| 1X | {pct(s['1X'])} |",f"| X2 | {pct(s['X2'])} |",f"| Over 1.5 | {pct(s['Over_1_5'])} |",
          f"| Over 3.5 | {pct(s['Over_3_5'])} |",f"| GG + Over 2.5 | {pct(s['GG_and_Over_2_5'])} |",'',
          '**Risultati esatti più probabili (modello strutturale, non calibrato):** '+', '.join(f"{x['score']} ({pct(x['prob'])})" for x in r['exact_scores_from_BISE'][:5]),
          '', '*Lettura statistica, non consiglio di puntata. Quote di apertura, esposizione e intenzioni del bookmaker non sono inferibili dalle sole closing odds.*',''])
    if errors:
        out+=['## Righe escluse','', '| Riga | Motivo |','|---:|---|']
        for e in errors[:30]:out.append(f"| {e['row']} | {e['reason'].replace('|','/')} |")
        if len(errors)>30:out.append(f'... e altre {len(errors)-30} righe escluse.')
    out+=['','## Stato scientifico','',
          '- BISE: ricostruzione strutturale verificata storicamente, non certificazione di edge.',
          '- Segnale incrementale globale storico molto piccolo; non generalizzabile automaticamente alle BIG5.',
          '- Bookmaker Confidence predittiva: NON attiva.',
          '- Fonte quote Bet365 closing: attestazione del proprietario dell’archivio LivePick; non timestamp verificati singolarmente.',
          '- Modello congelato: '+FREEZE['version']+'.','']
    return '\n'.join(out)


def run(input_path,output_dir):
    p=Path(input_path);dest=Path(output_dir)
    if not p.exists():raise FileNotFoundError(p)
    if dest.exists() and list(dest.iterdir()):raise FileExistsError(f'Output non vuoto, rifiuto sovrascrittura: {dest}')
    dest.mkdir(parents=True,exist_ok=True)
    with p.open(newline='',encoding='utf-8-sig') as fh:
        rd=csv.DictReader(fh)
        if not set(REQUIRED).issubset(rd.fieldnames or []):
            raise ValueError('Mancano colonne: '+', '.join(sorted(set(REQUIRED)-set(rd.fieldnames or []))))
        data=list(rd)
    if not data:raise ValueError('CSV senza partite')
    reports=[];errors=[]
    for index,row in enumerate(data,start=2):
        try:reports.append(analyze(row))
        except (ValueError,TypeError,OverflowError) as e:errors.append({'row':index,'reason':str(e),'home':row.get('home'),'away':row.get('away')})
    (dest/'RC11_Report.md').write_text(md_report(reports,errors,p.name),encoding='utf-8')
    (dest/'RC11_Analysis.json').write_text(json.dumps({'model':FREEZE['version'],'input_sha256':digest(p),'results':reports,'errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    with (dest/'RC11_Summary.csv').open('w',newline='',encoding='utf-8') as fh:
        cols=['league','home','away','market_1','market_X','market_2','market_GG','bise_GG','gap_pp','gap_reference_group','gap_reference_band','model_status']
        wr=csv.DictWriter(fh,fieldnames=cols);wr.writeheader()
        for r in reports:
            m=r['partita'];q=r['market_probs'];b=r['bise'];wr.writerow({'league':m['league'],'home':m['home'],'away':m['away'],
              'market_1':q['1X2']['1'],'market_X':q['1X2']['X'],'market_2':q['1X2']['2'],
              'market_GG':q['BTTS']['GG'],'bise_GG':b['p_GG_reconstructed'],'gap_pp':b['gap_pp'],
              'gap_reference_group':b['gap_benchmark_group'],'gap_reference_band':b['gap_percentile_band'],
              'model_status':'DESCRIPTIVE_EXPERIMENTAL'})
    man={'release':'RC11_OPERATIONAL_V1','rc9_frozen_model_sha256':digest(HERE/'rc9_frozen_protocol.json'),
         'rc9_frozen_core_sha256':digest(HERE/'rc8_bise_frozen_source.py'),
         'input_sha256':digest(p),'matches_processed':len(reports),'matches_excluded':len(errors),
         'created_at_utc':datetime.now(timezone.utc).isoformat(),
         'output_sha256':{k:digest(dest/k) for k in ['RC11_Report.md','RC11_Analysis.json','RC11_Summary.csv']}}
    (dest/'RC11_Manifest.json').write_text(json.dumps(man,indent=2,ensure_ascii=False),encoding='utf-8')
    return man


def main():
    a=argparse.ArgumentParser(description='Analisi descrittiva Bet365/BISE da 7 quote prematch')
    a.add_argument('--input',required=True,help='CSV con 7 quote decimali')
    a.add_argument('--output-dir',required=True)
    z=a.parse_args()
    print(json.dumps(run(z.input,z.output_dir),indent=2,ensure_ascii=False))

if __name__=='__main__':main()
