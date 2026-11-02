#!/usr/bin/env python3
"""Bookmaker Analyzer 2.0 / RC11.2: GG/NG predictive probability + honest empirical confidence.

Frozen RC9 BISE is not refitted. Confidence 0-10 is 10 * event probability,
NOT a second independent certainty estimate. The historical context is grouped
reliability on 2025-26 retrospective test and not forward certification.
"""
from pathlib import Path
import argparse, csv, hashlib, json, math
import numpy as np
from scipy.special import expit, logit
from rc11_operational import analyze as rc11_analyze, FREEZE, BIG5

HERE=Path(__file__).resolve().parent
BENCH=HERE/'rc11_2_confidence_holdout_bins.json'

def _digest(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def _p_from_market(p_market, group):
    if group=='BIG5':
        # BIG5 group-level out-of-time calibration improvement is too small.
        # Keep original de-vig Bet365 probability as primary estimate.
        return p_market, 'Bet365 de-vig, senza correzione aggiuntiva BIG5'
    m=FREEZE['frozen_model']['market']
    z=float(logit(np.clip(p_market,1e-6,1-1e-6)))
    return float(expit(m['intercept']+m['coefficients'][0]*z)), 'Bet365 de-vig calibrato con modello globale RC9 congelato (2020–24)'

def _wilson(y, n):
    if not n: return [None,None]
    z=1.959963984540054
    center=(y+z*z/(2*n))/(1+z*z/n)
    half=z*math.sqrt(y*(1-y)/n+z*z/(4*n*n))/(1+z*z/n)
    return [round(max(0,center-half),6),round(min(1,center+half),6)]

def make_reference(checkpoint, output=BENCH):
    """Generate diagnostics on 2025–26 only; no fitting or coefficient selection."""
    import pandas as pd
    d=pd.read_csv(checkpoint,usecols=['picked_date','league','gg_real','p_btts'])
    d['year']=pd.to_datetime(d['picked_date'],utc=True).dt.year
    d=d[d['year']>=2025].copy()
    d['group']=np.where(d['league'].isin(BIG5),'BIG5','OTHER')
    d['p_ref']=d.apply(lambda r:_p_from_market(float(r.p_btts),r['group'])[0],axis=1)
    d['bucket']=np.clip((d['p_ref']*10).astype(int),0,9)
    out={'schema':'BETPILOT_RC11_2_RELIABILITY_BINS_V1',
         'source_sha256':_digest(checkpoint),
         'frozen_protocol_sha256':_digest(HERE/'rc9_frozen_protocol.json'),
         'source_period':'2025-2026 through 2026-10-21 (retrospective; not new forward matches)',
         'note':'Group-level reference bins: CI is interval of observed GG frequency, not CI of individual fixture risk; evaluation bins are retrospective exploratory summaries.',
         'by_group':{}}
    for group in ['BIG5','OTHER']:
        g=d[d.group==group]; rows={}
        for b in range(10):
            x=g[g.bucket==b]
            n=len(x)
            if n:
                obs=float(x.gg_real.mean());pred=float(x.p_ref.mean()); ci=_wilson(obs,n)
            else:obs=pred=None;ci=[None,None]
            rows[str(b)]={'n':n,'mean_pred':pred,'observed_GG':obs,'observed_wilson95':ci,
                'bias_pp':None if n==0 else round(100*(obs-pred),3)}
        out['by_group'][group]={'n':len(g),'bins':rows}
    Path(output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    return out

def confidence(row, ref=None):
    r=rc11_analyze(row)
    grp='BIG5' if r['partita']['league'] in BIG5 else 'OTHER'
    pm=float(r['market_probs']['BTTS']['GG'])
    pg, method=_p_from_market(pm,grp)
    main='GG' if pg>=0.5 else 'NG'
    pmax=pg if main=='GG' else 1-pg
    ref=ref or json.loads(BENCH.read_text(encoding='utf-8'))
    if ref.get('frozen_protocol_sha256') != _digest(HERE/'rc9_frozen_protocol.json'):
        raise ValueError('Reference bins incompatible with RC9 freeze')
    bin_id=str(min(9,max(0,int(pg*10))))
    diag=ref['by_group'][grp]['bins'][bin_id]
    n=diag['n'];err=diag['bias_pp']
    # Evidenza per-fascia, non classe di affidabilita individuale.
    if n<100:status='SUPPORTO_STORICO_INSUFFICIENTE'
    elif n<1000:status='SUPPORTO_STORICO_LIMITATO'
    elif abs(err)>3:status='SCOSTAMENTO_CALIBRAZIONE_MATERIALE'
    else:status='SUPPORTO_STORICO_DISPONIBILE'
    r['confidence_predittiva']={
      'mercato':'GG/NG','modalita':'SPERIMENTALE_STORICA', 'probabilita_GG':pg,
      'probabilita_NG':1-pg, 'esito_piu_probabile':main,
      'probabilita_esito_piu_probabile':pmax,
      'intensita_0_10':round(10*pmax,1),
      'nota_intensita':'La scala 0–10 e una trasposizione della probabilita, NON un punteggio di attendibilita distinto.',
      'metodo':method,
      'supporto_storico':{'gruppo':grp,'fascia_GG':f'{int(bin_id)*10}–{(int(bin_id)+1)*10}%',
          'n':n,'frequenza_GG_osservata':diag['observed_GG'],
          'GG_prevista_media':diag['mean_pred'],
          'intervallo_95_frequenza_GG_osservata':diag['observed_wilson95'],
          'scostamento_medio_pp':err, 'stato':status},
      'convergenza_BISE':'stessa preferenza GG/NG' if (float(r['bise']['p_GG_reconstructed'])>=.5)==(pg>=.5) else 'preferenze discordanti',
      'convergenza_aumenta_confidence':False,
      'limiti':'Solo GG/NG; quote e previsioni storiche non certificano un vantaggio o la correttezza di una singola previsione. Non applicare a 1X2, risultato esatto, combinazioni o selezione puntate.',
    }
    r['status']['predictive_confidence_certified']=False
    r['status']['predictive_confidence_experimental_GG_NG_enabled']=True
    return r

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',required=True,help='CSV RC11 con sette quote Bet365')
    p.add_argument('--output',required=True,help='JSON risultati con Confidence GG/NG')
    p.add_argument('--reference',default=str(BENCH))
    a=p.parse_args()
    ref=json.loads(Path(a.reference).read_text(encoding='utf-8'))
    with Path(a.input).open(newline='',encoding='utf-8-sig') as f:
        rows=list(csv.DictReader(f))
    if not rows:raise ValueError('File vuoto')
    results=[];errors=[]
    for i,row in enumerate(rows,start=2):
        try:results.append(confidence(row,ref))
        except (TypeError,ValueError) as exc:errors.append({'line':i,'reason':str(exc)})
    out={'protocol':'RC11_2_EXPERIMENTAL_GG_NG_CONFIDENCE','model_version':FREEZE['version'],
         'input_sha256':_digest(a.input),'reference_sha256':_digest(a.reference),
         'results':results,'errors':errors}
    Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'processed':len(results),'excluded':len(errors),'output':a.output}))
if __name__=='__main__':main()
