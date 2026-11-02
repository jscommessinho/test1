#!/usr/bin/env python3
"""BETPILOT Bookmaker Analyzer RC12: multicategory 1X2 and binary O/U 2.5
calibration with strict chronological fit (<=2023), selection (2024), audit (2025-26).
RC9 BISE and RC11.2 GG/NG confidence remain UNCHANGED.

Two market calibrators:
  1X2: one-param temperature  p_i^gamma / sum_j p_j^gamma,
       preserving each match's within-market ranking.
  OU:  logistic(intercept + slope*logit(p_over)), complement under.
Selection: on 2024 only. In each scope and market, choose calibrated model
iff validation Brier AND logloss both beat raw by > 1e-9.  Otherwise use raw.
Retrospective test metrics are never used in selection.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, math
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.special import expit, logit, logsumexp
from rc11_2_confidence import confidence as rc11_confidence, BIG5

HERE=Path(__file__).resolve().parent
SOURCE=HERE/'BETPILOT_RC8_BISE_Closing_Odds_Checkpoint.csv.gz'
MODEL=HERE/'RC12_Fitted_Calibrators.json'
REFERENCE=HERE/'RC12_Reliability_Reference.json'


def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def normalize(a):
    odds=np.asarray(a,dtype=float)
    inv=1/odds
    return inv/inv.sum(axis=1,keepdims=True)


def prep(path):
    use=['picked_date','league','score','q1','qx','q2','qo','qu','qgg','qng','gg_real']
    d=pd.read_csv(path,usecols=use)
    parts=d['score'].str.extract(r'^\s*(\d+)\s*-\s*(\d+)\s*$')
    if parts.isna().any().any():raise ValueError('Score a 90 minuti non valido')
    gh=parts[0].astype(int).to_numpy();ga=parts[1].astype(int).to_numpy()
    d['year']=d.picked_date.str[:4].astype(int)
    d['month']=d.picked_date.str[:7]
    d['scope']=np.where(d.league.isin(BIG5),'BIG5','OTHER')
    for x in ['q1','qx','q2','qo','qu','qgg','qng']:
        if (d[x]<=1).any() or (not np.isfinite(d[x].to_numpy()).all()):
            raise ValueError('Quota non valida in '+x)
    o=normalize(d[['q1','qx','q2']].to_numpy())
    u=normalize(d[['qo','qu']].to_numpy())[:,0]
    y=np.where(gh>ga,0,np.where(gh==ga,1,2))
    yo=(gh+ga>=3).astype(int)
    return d,o,u,y,yo


def multiclass_brier(y,p):
    return np.sum((p-np.eye(3)[y])**2,axis=1)


def multiclass_ll(y,p):
    return -np.log(np.clip(p[np.arange(len(y)),y],1e-14,1))


def binary_brier(y,p):return (y-p)**2

def binary_ll(y,p):
    y=np.asarray(y,dtype=float)
    p=np.clip(p,1e-12,1-1e-12)
    return -y*np.log(p)-(1-y)*np.log1p(-p)


def temp_1x2(p, gamma):
    logs=np.log(np.clip(p,1e-12,1))*gamma
    return np.exp(logs-logsumexp(logs,axis=1,keepdims=True))


def fit_temp_1x2(p,y):
    logs=np.log(p)
    def loss(g):
        z=logs*g
        return float(np.mean(logsumexp(z,axis=1)-z[np.arange(len(y)),y]))
    r=minimize_scalar(loss,bounds=(0.5,1.8),method='bounded',options={'xatol':1e-8})
    if not r.success: raise RuntimeError(r.message)
    return float(r.x)


def binary_cal(p,coef):
    return expit(coef[0]+coef[1]*logit(np.clip(p,1e-6,1-1e-6)))


def fit_binary_cal(p,y):
    z=logit(np.clip(p,1e-6,1-1e-6))
    def loss(b):
        zz=b[0]+b[1]*z
        return float(np.mean(np.logaddexp(0,zz)-y*zz))
    r=minimize(loss,[0.,1.],method='L-BFGS-B',bounds=[(-2,2),(0.25,2.0)],options={'maxiter':300})
    if not r.success:raise RuntimeError(r.message)
    return r.x.tolist()


def summary(y,p,market):
    loss = (multiclass_brier(y,p),multiclass_ll(y,p)) if market=='1X2' else (binary_brier(y,p),binary_ll(y,p))
    return {'n':int(len(y)), 'brier':float(np.mean(loss[0])), 'logloss':float(np.mean(loss[1]))}


def reliability(y,p,market,scope,yearstr):
    """Fixed bins by actual selection. 1X2, 1X, X2 and 12 are each
    evaluated as distinct binary events; no threefold pseudoreplication.
    """
    if market=='1X2':
        events={
            '1':(p[:,0],(y==0).astype(int)),
            'X':(p[:,1],(y==1).astype(int)),
            '2':(p[:,2],(y==2).astype(int)),
            '1X':(p[:,0]+p[:,1],(y!=2).astype(int)),
            'X2':(p[:,1]+p[:,2],(y!=0).astype(int)),
            '12':(p[:,0]+p[:,2],(y!=1).astype(int)),
        }
    else:
        events={'Over_2_5':(p,y), 'Under_2_5':(1-p,1-y)}
    out={'scope':scope,'market':market,'period':yearstr,
         'note':'Bin counts represent distinct fixtures per event. Descriptive 2025–2026 frequencies, not individual-event confidence or forward validation.',
         'by_event':{}}
    for name,(pred,actual) in events.items():
        bins=[]
        for b in range(10):
            lo=b/10;hi=(b+1)/10
            ix=(pred>=lo)&((pred<hi) if b<9 else (pred<=hi))
            n=int(ix.sum())
            bins.append({'bucket':f'{b*10}-{(b+1)*10}%', 'matches':n,
                         'mean_prediction':None if not n else float(pred[ix].mean()),
                         'observed_frequency':None if not n else float(actual[ix].mean()),
                         'mean_bias_pp':None if not n else float(100*(actual[ix].mean()-pred[ix].mean()))})
        out['by_event'][name]={'n':int(len(y)),'bins':bins}
    return out


def bootstrap_months(d,y,raw,cal,market,n=600,seed=20261030):
    raw_b=(multiclass_brier(y,raw) if market=='1X2' else binary_brier(y,raw))
    cal_b=(multiclass_brier(y,cal) if market=='1X2' else binary_brier(y,cal))
    diff=cal_b-raw_b
    frame=pd.DataFrame({'month':d.month.to_numpy(),'diff':diff}).groupby('month')['diff'].agg(['sum','count']).to_numpy()
    rng=np.random.default_rng(seed);m=len(frame);boot=np.empty(n)
    for k in range(n):
        f=frame[rng.integers(m,size=m)]
        boot[k]=f[:,0].sum()/f[:,1].sum()
    return {'difference_brier_cal_minus_raw':float(diff.mean()),'monthly_block_bootstrap_CI95':np.quantile(boot,[.025,.975]).tolist(),
            'n_months':int(m),'note':'Retrospective uncertainty; not independent prospective proof'}


def build(checkpoint,out_model=MODEL,out_ref=REFERENCE):
    d,p1,po,y1,yo=prep(checkpoint)
    model={'schema':'BETPILOT_RC12_MultiMarket_Calibration_v1','created':'2026-10-30',
           'training':'2020-2023', 'validation':'2024','retrospective_test':'2025-2026 through 2026-10-21',
           'source_sha256':sha256(checkpoint),
           'freeze_rc9_sha256':sha256(HERE/'rc9_frozen_protocol.json'),
           'markets':{}, 'note':'Training only through 2023; choose a calibrator only on 2024. Historical test already explored in RC8/9; NOT blind.'}
    ref={'schema':'BETPILOT_RC12_Bins_v1','source_sha256':model['source_sha256'], 'markets':{},
         'note':'Descriptive bin frequencies on retrospective 2025-2026 test, not certified model confidence.'}
    for scope in ['BIG5','OTHER']:
        group=d.scope.eq(scope).to_numpy()
        mtr=group&(d.year<=2023).to_numpy(); mva=group&(d.year==2024).to_numpy();mte=group&(d.year>=2025).to_numpy()
        model['markets'][scope]={};ref['markets'][scope]={}
        for market,probs,ys in [('1X2',p1,y1),('OU2_5',po,yo)]:
            if market=='1X2':
                coef=fit_temp_1x2(probs[mtr],ys[mtr]); transform=lambda x:temp_1x2(x,coef)
            else:
                coef=fit_binary_cal(probs[mtr],ys[mtr]); transform=lambda x:binary_cal(x,coef)
            va_r=summary(ys[mva],probs[mva],market);va_c=summary(ys[mva],transform(probs[mva]),market)
            selected='calibrated' if va_c['brier']<va_r['brier']-1e-9 and va_c['logloss']<va_r['logloss']-1e-9 else 'direct'
            tr_r=summary(ys[mtr],probs[mtr],market);tr_c=summary(ys[mtr],transform(probs[mtr]),market)
            te_r=summary(ys[mte],probs[mte],market);te_c=summary(ys[mte],transform(probs[mte]),market)
            selected_probs=transform(probs[mte]) if selected=='calibrated' else probs[mte]
            te_sel=summary(ys[mte],selected_probs,market)
            uncert=bootstrap_months(d.loc[mte],ys[mte],probs[mte],selected_probs,market)
            model['markets'][scope][market]={
                'parameters':coef,'calibrator':'temperature' if market=='1X2' else 'logistic_intercept_slope',
                'selected_on_2024':selected, 'operational_method':'calibrated' if selected=='calibrated' and uncert['monthly_block_bootstrap_CI95'][1]<0 else 'direct', 'operational_policy':'Post-hoc conservative guardrail using 2025-26 stability; do NOT treat this as an independent unselected test or future proof.', 'train':{'direct':tr_r,'calibrated':tr_c},
                'validation':{'direct':va_r,'calibrated':va_c},
                'retrospective_test':{'direct':te_r,'calibrated':te_c,'selected':te_sel,'diff':uncert},
            }
            ref['markets'][scope][market]=reliability(ys[mte],selected_probs,market,scope,'2025-2026')
    Path(out_model).write_text(json.dumps(model,ensure_ascii=False,indent=2),encoding='utf-8')
    Path(out_ref).write_text(json.dumps(ref,ensure_ascii=False,indent=2),encoding='utf-8')
    return model


def analyze(row, model, reference=None):
    # Uses *unmodified* RC11.2 for GG/NG, BISE and structural scenario probabilities.
    r=rc11_confidence(row)
    scope='BIG5' if r['partita']['league'] in BIG5 else 'OTHER'
    raw_1=np.array([[r['market_probs']['1X2'][k] for k in ('1','X','2')]])
    raw_o=float(r['market_probs']['OU2_5']['over'])
    c1=model['markets'][scope]['1X2']; co=model['markets'][scope]['OU2_5']
    pp=temp_1x2(raw_1,c1['parameters'])[0] if c1['operational_method']=='calibrated' else raw_1[0]
    ov=float(binary_cal(np.array([raw_o]),co['parameters'])[0]) if co['operational_method']=='calibrated' else raw_o
    out={
        'modalita':'RETROSPECTIVE_CALIBRATION_EXPERIMENTAL','scope':scope,
        'partita':r['partita'],
        '1X2':{'1':float(pp[0]),'X':float(pp[1]),'2':float(pp[2]),'method':c1['operational_method'],
               '1X':float(pp[0]+pp[1]),'X2':float(pp[1]+pp[2]),'12':float(pp[0]+pp[2])},
        'OU2_5':{'Over':ov,'Under':1-ov,'method':co['operational_method']},
        'BTTS':r['confidence_predittiva'],
        'scenario_BISE':r['scenarios_from_BISE'],
        'intensita_principale_0_10':{'1X2':round(10*max(pp),1),'OU2_5':round(10*max(ov,1-ov),1)},
        'probabilita_e_intensita_0_10':{**{k:{'p':float(v),'intensita':round(10*v,1)} for k,v in [('1',pp[0]),('X',pp[1]),('2',pp[2]),('1X',pp[0]+pp[1]),('X2',pp[1]+pp[2]),('12',pp[0]+pp[2]),('Over_2_5',ov),('Under_2_5',1-ov)]},
                                          'GG':{'p':float(r['confidence_predittiva']['probabilita_GG']),'intensita':round(10*float(r['confidence_predittiva']['probabilita_GG']),1)},
                                          'NG':{'p':float(r['confidence_predittiva']['probabilita_NG']),'intensita':round(10*float(r['confidence_predittiva']['probabilita_NG']),1)}},
        'convergenza_non_indipendente':True,
        'stato_confidence':'SPERIMENTALE. Supporto storico per evento; probabilita*10 NON e un voto di affidabilita.',
        'avvertenza':'Scenari BISE strutturali non ricalibrati. NON combinare le probabilita calibrate 1X2 e Over in un nuovo score model senza verifica di coerenza. Intensita 0-10 = probabilita x10, non affidabilita autonoma.'}
    if reference:
        def matching_bin(m,event,p):
            idx=int(min(9,max(0,math.floor(p*10))))
            return reference['markets'][scope][m]['by_event'][event]['bins'][idx]
        out['supporto_storico']={'Over_2_5':matching_bin('OU2_5','Over_2_5',ov),
                                'Under_2_5':matching_bin('OU2_5','Under_2_5',1-ov),
                                '1X':matching_bin('1X2','1X',float(pp[0]+pp[1])),
                                'X2':matching_bin('1X2','X2',float(pp[1]+pp[2])),
                                '12':matching_bin('1X2','12',float(pp[0]+pp[2])),
                                '1':matching_bin('1X2','1',float(pp[0])),
                                'X':matching_bin('1X2','X',float(pp[1])),
                                '2':matching_bin('1X2','2',float(pp[2]))}
    return out


def cli():
    a=argparse.ArgumentParser(description=__doc__)
    sp=a.add_subparsers(dest='cmd',required=True)
    b=sp.add_parser('build');b.add_argument('--checkpoint',default=str(SOURCE))
    b.add_argument('--model',default=str(MODEL));b.add_argument('--reference',default=str(REFERENCE))
    q=sp.add_parser('analyze');q.add_argument('--input',required=True);q.add_argument('--output',required=True)
    q.add_argument('--model',default=str(MODEL));q.add_argument('--reference',default=str(REFERENCE))
    args=a.parse_args()
    if args.cmd=='build':
        m=build(args.checkpoint,args.model,args.reference)
        for scope in m['markets']:
            for market,v in m['markets'][scope].items():
                t=v['retrospective_test'];print(scope,market,v['selected_on_2024'],t['direct']['brier'],t['selected']['brier'], t['diff']['monthly_block_bootstrap_CI95'])
    else:
        m=json.loads(Path(args.model).read_text());ref=json.loads(Path(args.reference).read_text())
        if ref.get('source_sha256') != m.get('source_sha256'):
            raise ValueError('RC12 reference and model dataset mismatch')
        if sha256(HERE/'rc9_frozen_protocol.json')!=m['freeze_rc9_sha256']:
            raise ValueError('RC9 model freeze SHA256 mismatch')
        with open(args.input,encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
        res=[]; errors=[]
        for i,row in enumerate(rows,start=2):
            try:res.append(analyze(row,m,ref))
            except Exception as e:errors.append({'row':i,'error':str(e)})
        Path(args.output).write_text(json.dumps({'schema':'RC12_OPERATIVE_V1','results':res,'errors':errors},ensure_ascii=False,indent=2))
        print('processed',len(res),'excluded',len(errors))

if __name__=='__main__':cli()
