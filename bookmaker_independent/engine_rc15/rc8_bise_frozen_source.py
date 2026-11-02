#!/usr/bin/env python3
"""BETPILOT RC8: mathematical and historical validation; user-provenance Bet365 closing LivePick."""
import numpy as np,pandas as pd,json,os,math
from numba import njit,prange,set_num_threads
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss,log_loss
set_num_threads(4)
ROOT=os.environ.get('RC8_ROOT',os.path.dirname(os.path.abspath(__file__)))
@njit
def probs(x,y):
    hp=math.exp(-x);ap=math.exp(-y); cpa=ap
    win=0.0; draw=hp*ap
    for h in range(1,28):
        hp=hp*x/h
        win+=hp*cpa
        ap=ap*y/h
        cpa+=ap
        draw+=hp*ap
    return win,draw
@njit
def core(pH,pD,pOver):
    lo=0.;hi=22.
    for j in range(41):
        L=(lo+hi)/2
        pv=1-math.exp(-L)*(1+L+L*L/2)
        if pv<pOver:lo=L
        else:hi=L
    L=(lo+hi)/2
    # Enforce consistency with calibrated DC correction and exact market constraints.
    # f(x) = P(H win | poisson) + correction to 1-0 - market H.
    lo=0.;hi=L
    w0,d0=probs(0.,L); wh,dh=probs(L,0.)
    if w0+(d0-pD)/2 > pH+1e-7 or wh+(dh-pD)/2 < pH-1e-7:
        return np.nan,1,L,np.nan,np.nan
    for j in range(34):
        x=(lo+hi)/2; w,dr=probs(x,L-x)
        f=w+(dr-pD)/2-pH
        if f<0: lo=x
        else: hi=x
    x=(lo+hi)/2;y=L-x;win,dr=probs(x,y);t=(dr-pD)/2
    e=math.exp(-L)
    p00=e;p11=e*x*y;p01=e*y;p10=e*x
    if t>min(p00,p11)+1e-9 or t < -min(p01,p10)-1e-9:
        return np.nan,2,L,x,y
    btts=(1-math.exp(-x))*(1-math.exp(-y))-t
    if btts < 0 or btts > 1:
        return np.nan,3,L,x,y
    return btts,0,L,x,y
@njit(parallel=True)
def fit(pH,pD,pO):
    n=len(pH)
    preds=np.empty(n);flags=np.empty(n,dtype=np.int8);lams=np.empty(n);h=np.empty(n);a=np.empty(n)
    for i in prange(n):
        preds[i],flags[i],lams[i],h[i],a[i]=core(pH[i],pD[i],pO[i])
    return preds,flags,lams,h,a

def scores(obs,p):
    p=np.clip(np.asarray(p),1e-7,1-1e-7)
    return {'n':len(obs),'brier':float(np.mean((obs-p)**2)),'log_loss':float(-np.mean(obs*np.log(p)+(1-obs)*np.log(1-p))),'mean_pred':float(np.mean(p)),'realized':float(np.mean(obs))}

def compare(d,label):
    if len(d)<15:return None
    y=d['gg_real'].to_numpy();pb=d['p_btts'].to_numpy();pm=d['p_bise'].to_numpy()
    return {'name':label,'n':int(len(d)),'market':scores(y,pb),'bise':scores(y,pm), 'brier_difference_bise_minus_market':float(np.mean((y-pm)**2-(y-pb)**2)), 'logloss_difference_bise_minus_market':float(np.mean(-y*np.log(pm)-(1-y)*np.log(1-pm)+y*np.log(pb)+(1-y)*np.log(1-pb))), 'mean_abs_gap_pp':float(np.mean(abs(pm-pb))*100)}

def main():
    df=pd.read_csv(ROOT+'/rc8_seven_odds_eligible.csv.gz');raw=len(df)
    df['pick_dt']=pd.to_datetime(df.picked_date,utc=True,errors='coerce')
    df['match_day']=(df.pick_dt-pd.to_timedelta(df.minute,unit='min')).dt.strftime('%Y-%m-%d')
    df['date_year']=df.match_day.str[:4]
    key=['match_day','league','home','away']
    gr=df.groupby(key,sort=False).score.nunique()
    bad_keys=gr[gr>1]
    df['key']=df[key].fillna('').astype(str).agg('|'.join,axis=1)
    badset={'|'.join(map(str,k)) for k in bad_keys.index}
    conflicts=df.key.isin(badset)
    ct_conflicts=int(conflicts.sum())
    df=df.loc[~conflicts].copy()
    duplicate=int(df.duplicated(subset=key).sum())
    df.sort_values('rownum',inplace=True)
    df=df.drop_duplicates(subset=key,keep='first').reset_index(drop=True)
    # Decimal odds, normalized inverse odds; all three quoted market families independent only as separate quoted pairs.
    one=np.column_stack([1/df.q1,1/df.qx,1/df.q2]); one/=one.sum(axis=1)[:,None]
    ov=np.column_stack([1/df.qo,1/df.qu]);ov/=ov.sum(axis=1)[:,None]
    gg=np.column_stack([1/df.qgg,1/df.qng]);gg/=gg.sum(axis=1)[:,None]
    df['p_home']=one[:,0];df['p_draw']=one[:,1];df['p_over']=ov[:,0];df['p_btts']=gg[:,0]
    ss=df.score.str.extract(r'(\d+)\s*[-–:]\s*(\d+)').astype(int)
    df['gg_real']=((ss[0]>0)&(ss[1]>0)).astype(np.int8)
    predicted,flags,lam,lamh,lama=fit(df.p_home.to_numpy(),df.p_draw.to_numpy(),df.p_over.to_numpy())
    df['flag']=flags;df['p_bise']=predicted;df['lambda_total']=lam;df['lambda_home']=lamh;df['lambda_away']=lama
    fl=pd.Series(flags).value_counts().sort_index().to_dict();coverage={'seven_valid_rows_pre_dedup':raw,'conflicting_score_rows_removed':ct_conflicts,'other_duplicate_rows_removed':duplicate,'unique_rows_for_fit':len(df),'bise_flags':{str(k):int(v) for k,v in fl.items()}}
    df_fit=df.loc[df.flag==0].copy()
    print('CORE',coverage,flush=True)
    def grouping(x):
        if x=='Italy Serie A':return 'Serie A'
        if x=='England Premier League':return 'Premier League'
        if x=='Spain La Liga':return 'La Liga'
        if x=='France Ligue 1':return 'Ligue 1'
        if x=='Germany Bundesliga I':return 'Bundesliga'
        return 'Other'
    df_fit['major']=df_fit.league.map(grouping)
    out={'method':'user-verified LivePick Bet365 prematch closing odds (per-event timestamp not independently recorded)','quality':coverage,'full':compare(df_fit,'all leagues'),'by_year':[], 'big5':compare(df_fit.loc[df_fit.major!='Other'],'big five leagues'),'by_major':[], 'by_split':{}, 'gap_buckets':[]}
    for yr,x in df_fit.groupby('date_year'):out['by_year'].append(compare(x,str(yr)))
    for lg,x in df_fit.groupby('major'):out['by_major'].append(compare(x,lg))
    for split,a in [('train_2020_2024',df_fit.date_year.astype(int)<=2024),('test_2025_2026',df_fit.date_year.astype(int)>=2025)]:out['by_split'][split]=compare(df_fit[a],split)
    # Discrepancy bins fixed prior to looking at results.
    df_fit['gap']=df_fit.p_bise-df_fit.p_btts
    for lo,hi in [(-1.,-.05),(-.05,-.02),(-.02,0),(0,.02),(.02,.05),(.05,1.)]:
        q=df_fit[(df_fit.gap>=lo)&(df_fit.gap<hi)]
        if len(q):out['gap_buckets'].append({'range_pp':[round(lo*100,2),round(hi*100,2)],'n':len(q),'mean_gap_pp':float(q.gap.mean()*100),'actual_btts_rate':float(q.gg_real.mean()),'market_btts_rate':float(q.p_btts.mean()),'bise_btts_rate':float(q.p_bise.mean()),'residual_vs_market_pp':float((q.gg_real-q.p_btts).mean()*100)})
    # Cluster block bootstrap on match-day, 95% CI for out-of-time paired Brier and Log-loss difference.
    from numpy.random import default_rng
    rng=default_rng(20261029)
    for name,part in [('all',df_fit),('test_2025_2026',df_fit[df_fit.date_year.astype(int)>=2025]),('big5',df_fit[df_fit.major!='Other']),('big5_test',df_fit[(df_fit.major!='Other')&(df_fit.date_year.astype(int)>=2025)])]:
        y=part.gg_real.to_numpy();p=part.p_btts.to_numpy();m=part.p_bise.to_numpy()
        dd=(y-m)**2-(y-p)**2
        z=-y*np.log(m)-(1-y)*np.log1p(-m)+y*np.log(p)+(1-y)*np.log1p(-p)
        res=part[['match_day']].copy();res['db']=dd;res['dl']=z
        dgroup=res.groupby('match_day',sort=False).agg({'db':['sum','count'],'dl':'sum'})
        vals=dgroup.to_numpy();n=len(vals);boots=[];ll=[]
        for j in range(400):
            sample=vals[rng.integers(0,n,size=n)]
            boots.append(sample[:,0].sum()/sample[:,1].sum());ll.append(sample[:,2].sum()/sample[:,1].sum())
        out.setdefault('bootstrap_CI',{})[name]={'n':len(part),'days':n,'brier_difference_point':float(np.mean(dd)),'brier_CI_95':np.quantile(boots,[.025,.975]).tolist(),'logloss_difference_point':float(np.mean(z)),'logloss_CI_95':np.quantile(ll,[.025,.975]).tolist()}
    # Reject any implied predictive improvement without out-of-time benchmarks. Market-only recalibration control.
    train=df_fit[df_fit.date_year.astype(int)<=2024];test=df_fit[df_fit.date_year.astype(int)>=2025]
    if len(train)>500 and len(test)>500:
        def features(d,with_gap=False):
            lm=logit(d.p_btts.clip(.001,.999).to_numpy())
            return np.column_stack((lm, 10*d.gap.to_numpy())) if with_gap else lm.reshape(-1,1)
        ytrain=train.gg_real.to_numpy();ytest=test.gg_real.to_numpy()
        models={}
        for label,flag in [('market_recalibrated',False),('market_plus_BISE_gap',True)]:
            m=LogisticRegression(C=10,max_iter=250,solver='lbfgs')
            m.fit(features(train,flag),ytrain)
            p=m.predict_proba(features(test,flag))[:,1]
            models[label]={'test':scores(ytest,p),'coefficients':[float(x) for x in m.coef_[0]],'intercept':float(m.intercept_[0])}
        out['logistic_models']=models
    # Produce only small results and reproducible summaries; no copyrighted megadatabase redistributed.
    with open(ROOT+'/rc8_bise_results.json','w') as f:json.dump(out,f,indent=2,ensure_ascii=False)
    pd.DataFrame(out['by_major']).to_json(ROOT+'/rc8_major_metrics.json',orient='records',indent=2)
    df_fit[['rownum','match_day','league','home','away','gg_real','p_btts','p_bise','gap','lambda_home','lambda_away']].to_csv(ROOT+'/rc8_model_predictions_internal.csv.gz',index=False,compression='gzip')
    print('FINISHED full summary',out['full'],'big5',out['big5'],'test',out['by_split']['test_2025_2026'],'bootstrap',out['bootstrap_CI']['test_2025_2026'],'models',out.get('logistic_models'),flush=True)
if __name__=='__main__':main()
