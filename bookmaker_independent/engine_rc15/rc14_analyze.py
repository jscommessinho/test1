#!/usr/bin/env python3
"""RC14 empirical audit — extra Bet365 closing markets vs frozen BISE, no refitting.
Historical data previously explored, not a fresh prospective experiment. ALL provenance
of Bet365/closing for LivePick is user-attested, without quote-specific timestamps.
"""
from __future__ import annotations
import csv, gzip, json, math, hashlib, argparse
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.special import i0e, iv
from scipy.stats import skellam
from rc13_scenarios import line_split, ah_settlement
from rc11_operational import BIG5, score_distribution
ROOT=Path(__file__).resolve().parent
CHECK=Path('/mnt/data/BETPILOT_RC8_BISE_Closing_Odds_Checkpoint.csv.gz')
EXTRA=ROOT/'RC14_Extra_Markets_Joined.csv.gz'

def de_vig_two(o,u):
    inv1=1/o;inv2=1/u;vig=inv1+inv2-1
    ok=(np.isfinite(o)&np.isfinite(u)&(o>1)&(u>1)&(o<1000)&(u<1000)&(vig>=-.025)&(vig<=.3))
    pr=inv1/(inv1+inv2)
    return np.where(ok,pr,np.nan),vig,ok

def compute_structural_ou(lh,la,p_draw):
    total=lh+la
    # I_0(2 sqrt(lh la))*exp(-total); i0e prevents overflow.
    bessel=2*np.sqrt(lh*la)
    base_draw=np.exp(-total+bessel)*i0e(bessel)
    t=(base_draw-p_draw)/2
    base0=np.exp(-total)
    p15=1-base0*(1+total)-t
    p35=1-base0*(1+total+total*total/2+total*total*total/6)
    return p15,p35,t

def parse_handicap(name):
    prefix='[ODDS - PRE-MATCH] Asian Handicap - '
    if not name.startswith(prefix):return None
    x=name[len(prefix):]
    if x.startswith('Home Team '):side='home';q=x[len('Home Team '):]
    elif x.startswith('Away Team '):side='away';q=x[len('Away Team '):]
    else:return None
    try: vals=[float(v.strip()) for v in q.split(',')]
    except:return None
    if not 1<=len(vals)<=2:return None
    line=sum(vals)/len(vals)
    if abs(line*4-round(line*4))>1e-6:return None
    return side,float(round(line,2))

def settle_bins_home(pminus2,pminus1,pzero,pplus1,pplus2,line):
    # h in [-1.25,+1.25]. These 5 differences are sufficient for payout fractions.
    a=np.array([pminus2,pminus1,pzero,pplus1,pplus2])
    comps=line_split(line)
    outcomes=np.array([-2.,-1.,0.,1.,2.]);w=np.zeros(5);p=np.zeros(5);l=np.zeros(5)
    for c in comps:
        v=outcomes+c
        w+=(v>0)/len(comps);p+=(np.abs(v)<=1e-9)/len(comps);l+=(v<0)/len(comps)
    return np.dot(w,a),np.dot(p,a),np.dot(l,a)

def bootstrap_month_ci(group,delta,seed=20261030,reps=1300):
    # group monthly label array, delta paired per-match loss differences; reweight monthly clusters.
    z=pd.DataFrame({'month':group,'d':delta}).groupby('month',sort=True)['d'].agg(['sum','count'])
    if len(z)<8:return {'months':len(z),'ci95':None,'note':'<8 monthly clusters'}
    rng=np.random.default_rng(seed)
    s=z['sum'].to_numpy();n=z['count'].to_numpy();r=rng.integers(0,len(s),size=(reps,len(s)))
    boots=s[r].sum(axis=1)/n[r].sum(axis=1)
    return {'months':len(z),'ci95':[float(q) for q in np.quantile(boots,[.025,.975])],
            'month_fraction_model_better':float((z['sum']<0).mean())}

def summarize_binary(d,market):
    v=d[d[f'{market}_valid']].copy()
    if not len(v):return {'n':0}
    pm=v[f'p_{market}_mkt'].to_numpy(); pb=v[f'p_{market}_bise'].to_numpy();y=v[f'y_{market}'].to_numpy()
    b_m=np.square(pm-y);b_b=np.square(pb-y);delta=b_b-b_m
    return {'n':int(len(v)), 'actual_rate':float(y.mean()), 'avg_bet365':float(pm.mean()), 'avg_bise':float(pb.mean()),
            'brier_bet365':float(b_m.mean()),'brier_bise':float(b_b.mean()),'brier_delta_model_minus_book':float(delta.mean()),
            'mean_abs_probability_gap_pp':float(np.abs(pb-pm).mean()*100),
            'mean_signed_probability_gap_pp':float((pb-pm).mean()*100),
            'gap_p95_pp':float(np.quantile(np.abs(pb-pm),.95)*100),
            'months_test':bootstrap_month_ci(v['month'],delta)}

def run():
    names=['rownum','picked_date','league','score','q1','qx','q2','qo','qu','qgg','qng','lambda_home','lambda_away','p_bise']
    d=pd.read_csv(CHECK,usecols=names)
    d['rownum']=d.rownum.astype('int64')
    with gzip.open(EXTRA,'rt') as f:
        names_extra=next(csv.reader(f))
    ahcols={parse_handicap(x):x for x in names_extra if parse_handicap(x)}
    desired_lines=[-1.25,-1.,-.75,-.5,-.25,0.,.25,.5,.75,1.,1.25]
    pairs={line:(ahcols.get(('home',line)),ahcols.get(('away',-line))) for line in desired_lines}
    selected={'rownum','[ODDS - PRE-MATCH] Match Goals - over 1.5','[ODDS - PRE-MATCH] Match Goals - under 1.5',
              '[ODDS - PRE-MATCH] Match Goals - over 3.5','[ODDS - PRE-MATCH] Match Goals - under 3.5'}
    for a,b in pairs.values():
        if a and b:selected.update([a,b])
    e=pd.read_csv(EXTRA,usecols=lambda c:c in selected,low_memory=False,na_values=['-','—'])
    assert d.rownum.is_unique and e.rownum.is_unique
    d=d.merge(e,on='rownum',how='inner',validate='one_to_one',sort=False)
    assert len(d)==512420,len(d)
    d['month']=d.picked_date.str.slice(0,7)
    d['year']=d.picked_date.str.slice(0,4).astype(int)
    d['scope']=np.where(d.league.isin(BIG5),'BIG5','OTHER')
    score=d.score.str.extract(r'^\s*(\d+)\s*-\s*(\d+)\s*$').astype(int)
    gh=score[0].to_numpy();ga=score[1].to_numpy()
    total=d.lambda_home.to_numpy()+d.lambda_away.to_numpy()
    odds=d[['q1','qx','q2']].to_numpy(float); p_1x2=(1/odds);p_1x2=p_1x2/p_1x2.sum(axis=1,keepdims=True)
    p15,p35,t=compute_structural_ou(d.lambda_home.to_numpy(),d.lambda_away.to_numpy(),p_1x2[:,1])
    # Freeze preservation: BISE GG must match the checkpoint reconstruction.
    calc_gg=(1-np.exp(-d.lambda_home.to_numpy()))*(1-np.exp(-d.lambda_away.to_numpy()))-t
    max_gg_err=float(np.max(np.abs(calc_gg-d.p_bise.to_numpy())))
    if max_gg_err>1e-9:raise AssertionError('BISE frozen mismatch: '+str(max_gg_err))
    if np.min(p15)<0 or np.max(p15)>1 or np.min(p35)<0 or np.max(p35)>1:raise AssertionError('OU structural out of range')
    fmap={'15':('[ODDS - PRE-MATCH] Match Goals - over 1.5','[ODDS - PRE-MATCH] Match Goals - under 1.5',p15,(gh+ga>=2).astype(int)),
          '35':('[ODDS - PRE-MATCH] Match Goals - over 3.5','[ODDS - PRE-MATCH] Match Goals - under 3.5',p35,(gh+ga>=4).astype(int))}
    checkvig={}
    for m,(c1,c2,pm,real) in fmap.items():
        p,vig,ok=de_vig_two(d[c1].to_numpy(float),d[c2].to_numpy(float))
        d[f'{m}_valid']=ok;d[f'p_{m}_mkt']=p;d[f'p_{m}_bise']=pm;d[f'y_{m}']=real
        checkvig[m]={'complete_market_pairs':int(np.isfinite(d[c1].to_numpy(float)).sum() if False else np.sum(np.isfinite(d[c1]) & np.isfinite(d[c2]))),
                     'valid_market_pairs':int(ok.sum()), 'vig_failed_or_invalid':int(np.sum(np.isfinite(d[c1])&np.isfinite(d[c2])&~ok))}
    # Nested events impose constraints; do not threshold normal rounding as arbitrage.
    po25=1/d.qo.to_numpy();pu25=1/d.qu.to_numpy();p25=po25/(po25+pu25)
    pbgg=1/d.qgg.to_numpy();pbng=1/d.qng.to_numpy();pgg=pbgg/(pbgg+pbng)
    valid15=d['15_valid'].to_numpy();valid35=d['35_valid'].to_numpy()
    dd15=d['p_15_mkt'].to_numpy();dd35=d['p_35_mkt'].to_numpy()
    tol=.005 # 0.5 pp quality diagnostic
    coherence={
      'O1_5_below_O2_5_more_than_half_pp':int(np.sum(valid15 &(dd15+tol<p25))),
      'O2_5_below_O3_5_more_than_half_pp':int(np.sum(valid35 &(p25+tol<dd35))),
      'GG_above_O1_5_more_than_half_pp':int(np.sum(valid15 &(pgg>dd15+tol))),
      'all_3_markets_present':int(np.sum(valid15&valid35)),
      'all_3_nested_constraints_pass':int(np.sum(valid15&valid35&(dd15+tol>=p25)&(p25+tol>=dd35)&(pgg<=dd15+tol)))
    }
    # Descriptive freeze-period gap benchmarks, 2020–2024 vs 2025–2026.
    out={'schema':'BETPILOT_RC14_MARKET_COVERAGE_V1','description':'Historical audit, prospective validation suspended by user',
         'source':'User-attested Bet365 last prematch, no odds-level timestamps', 'rows_checkpoint':int(len(d)),
         'frozen_BISE_max_GG_reconstruction_error':max_gg_err,'quote_quality':checkvig,'coherence':coherence,
         'market_metrics':{},'AH_coverage_and_stability':{},'big5_leagues':sorted(BIG5)}
    gap_tables=[]
    for scope in ('BIG5','OTHER'):
        for market in ('15','35'):
            one=d[(d.scope==scope)&(d.year<=2024)&d[f'{market}_valid']]
            gap=abs(one[f'p_{market}_bise']-one[f'p_{market}_mkt'])
            p95=float(gap.quantile(.95)) if len(one) else None
            p99=float(gap.quantile(.99)) if len(one) else None
            for period,cond in [('train_2020_2024',d.year<=2024),('test_2025_2026',d.year>=2025),('all',d.year>=2020)]:
                k=f'{scope}_{market}_{period}'
                sub=d[(d.scope==scope)&cond]
                r=summarize_binary(sub,market)
                if p95 is not None and r['n']>0:
                    ss=sub[sub[f'{market}_valid']]
                    diff=(ss[f'p_{market}_bise']-ss[f'p_{market}_mkt']).abs()
                    r.update({'train_gap_p95_pp':p95*100,'train_gap_p99_pp':p99*100,
                              'fraction_gap_above_train_p95':float((diff>p95).mean()),
                              'fraction_gap_above_5pp':float((diff>.05).mean())})
                out['market_metrics'][k]=r
            # yearly (no new model fitting)
            for y,sub in d[d.scope==scope].groupby('year'):
                r=summarize_binary(sub,market)
                if r['n']>0:out['market_metrics'][f'{scope}_{market}_{y}']=r
    # Asian handicap expected payoff is derived from same 1X2 input at low lines.
    # Including diff 1 +/- allows line +/-1.25 to depend on score structure.
    lh=d.lambda_home.to_numpy(float);la=d.lambda_away.to_numpy(float)
    dplus1=skellam.pmf(1,lh,la)+t
    dminus1=skellam.pmf(-1,lh,la)+t
    ph=p_1x2[:,0];px=p_1x2[:,1];pa=p_1x2[:,2]
    if np.min(ph-dplus1)<-1e-7 or np.min(pa-dminus1)<-1e-7:raise AssertionError('AH score difference masses negative')
    for line,(hcol,acol) in pairs.items():
        if not hcol or not acol:continue
        qh=d[hcol].to_numpy(float);qa=d[acol].to_numpy(float)
        ok=np.isfinite(qh)&np.isfinite(qa)&(qh>1)&(qa>1)&(qh<1000)&(qa<1000)
        # Exclude only strong quote irrationalities; no naive de-vig for AH.
        qh=np.where(ok,qh,np.nan);qa=np.where(ok,qa,np.nan)
        if not ok.any():continue
        comps=line_split(line)
        W=np.zeros(len(d));P=np.zeros(len(d));L=np.zeros(len(d))
        probs=[pa-dminus1,dminus1,px,dplus1,ph-dplus1]
        for delta,prob in zip([-2,-1,0,1,2],probs):
            for component in comps:
                u=delta+component
                if u>1e-10:W+=prob/len(comps)
                elif u< -1e-10:L+=prob/len(comps)
                else:P+=prob/len(comps)
        assert np.max(np.abs(W+P+L-1))<1e-7
        # Comparison is structural, no profit evidence. The result is a book-priced return under a model anchored to same bookmaker.
        net_home=(qh-1)*W-L;net_away=(qa-1)*L-W
        good=ok&(W>1e-6)&(L>1e-6)&np.isfinite(net_home)&np.isfinite(net_away)
        n=int(good.sum())
        if not n:continue
        # Historical fractional settlement for the same quoted handicap: this is
        # outcome calibration, NOT an executable-betting ROI or an independent edge.
        Wact=np.zeros(len(d)); Pact=np.zeros(len(d)); LAct=np.zeros(len(d))
        dif=gh-ga
        for component in comps:
            val=dif+component
            Wact+=(val>0)/len(comps)
            Pact+=(val==0)/len(comps)
            LAct+=(val<0)/len(comps)
        assert np.max(np.abs(Wact+Pact+LAct-1))<1e-7
        gtest=good&(d.year>=2025)
        x={ 'market':f'AH home {line:+.2f} / away {-line:+.2f}', 'n_full_sample':n, 'n_test':int((good & (d.year>=2025)).sum()),
           'n_big5':int((good & (d.scope=='BIG5')).sum()),
           'mean_structural_net_home':float(net_home[good].mean()),'mean_structural_net_away':float(net_away[good].mean()),
           'fraction_structural_net_home_positive':float(np.mean(net_home[good]>0)),
           'mean_home_quoted_odds':float(np.mean(qh[good])), 'mean_away_quoted_odds':float(np.mean(qa[good])),
           'mean_model_home_fair':float(np.mean(1+L[good]/W[good])),
           'test_weighted_win_predicted':float(W[gtest].mean()) if np.any(gtest) else None,
           'test_weighted_win_realized':float(Wact[gtest].mean()) if np.any(gtest) else None,
           'test_weighted_push_predicted':float(P[gtest].mean()) if np.any(gtest) else None,
           'test_weighted_push_realized':float(Pact[gtest].mean()) if np.any(gtest) else None,
           'test_weighted_loss_predicted':float(L[gtest].mean()) if np.any(gtest) else None,
           'test_weighted_loss_realized':float(LAct[gtest].mean()) if np.any(gtest) else None,
           'net_home_abs_p95':float(np.quantile(abs(net_home[good]),.95)),
           'note':'Model-conditional net payoff from two quoted sides, NOT independently calibrated value/ROI.'}
        out['AH_coverage_and_stability'][f'{line:+.2f}']=x
    # Deep-dive audit: group-specific prediction summaries per league for 2025+ >=100 matches.
    out['league_test']={}
    for league,sub in d[d.year>=2025].groupby('league'):
        z={m:summarize_binary(sub,m) for m in ('15','35')}
        if max(z['15'].get('n',0),z['35'].get('n',0))>=100:
            out['league_test'][league]={m:{k:v for k,v in z[m].items() if k!='months_test'} for m in ('15','35')}
    out['league_test']=dict(sorted(out['league_test'].items(),key=lambda kv: -max(kv[1]['15'].get('n',0),kv[1]['35'].get('n',0))))
    # An auditable per-match diagnostic sample (no huge original data in package).
    cols=['rownum','picked_date','league','score','q1','qx','q2','qo','qu','qgg','qng',
          '15_valid','p_15_mkt','p_15_bise','y_15','35_valid','p_35_mkt','p_35_bise','y_35']
    d[cols].sample(n=min(len(d),120),random_state=20261030).sort_values('rownum').to_csv(ROOT/'RC14_Audit_Sample_120.csv',index=False)
    (ROOT/'RC14_Statistics.json').write_text(json.dumps(out,indent=2,ensure_ascii=False))
    return out

if __name__=='__main__':
 parser=argparse.ArgumentParser(description='RC14: audit of existing user-owned prematch closing odds only')
 parser.add_argument('--checkpoint',default=str(CHECK),help='RC8 frozen checkpoint CSV.gz')
 parser.add_argument('--extra',default=str(EXTRA),help='RC14 extracted extra markets CSV.gz')
 args=parser.parse_args()
 CHECK=Path(args.checkpoint);EXTRA=Path(args.extra)
 result=run()
 for scope in ('BIG5','OTHER'):
  for market in ('15','35'):
   x=result['market_metrics'][f'{scope}_{market}_test_2025_2026']
   print(scope,market,{k:v for k,v in x.items() if k in ('n','brier_bet365','brier_bise','brier_delta_model_minus_book','mean_abs_probability_gap_pp','fraction_gap_above_train_p95')})
 print('COHERENCE',result['coherence']);print('AH lines',{k:v['n_full_sample'] for k,v in result['AH_coverage_and_stability'].items()})
