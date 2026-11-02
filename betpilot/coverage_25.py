#!/usr/bin/env python3
"""BETPILOT coverage audit of 25 selected leagues, entirely outside frozen GA2.

Reads THREE real historical result files and an independent current-season CSV
for each league. Reports counts, missing scores, date quality and potential
training eligibility. DOES NOT create production GA2 history or team mappings.
"""
from __future__ import annotations
import argparse, csv, io, json, re, hashlib, concurrent.futures
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

UPSTREAM='https://raw.githubusercontent.com/'
MAX_BYTES=1_500_000
EXPECTED_SEASON_KEYS=('2023-24','2024-25','2025-26')

class CoverageError(ValueError):pass


def github_raw(repo, path):
    if not re.fullmatch(r'[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+',repo):raise CoverageError('Invalid GitHub repo')
    if not re.fullmatch(r'[a-zA-Z0-9_./-]+',path) or '..' in path.split('/'):
        raise CoverageError('Invalid upstream file path')
    url=f'{UPSTREAM}{repo}/master/{path}'
    req=Request(url,headers={'Accept':'text/plain,application/json,text/csv',
                            'User-Agent':'BETPILOT-Coverage-Audit/1.0'})
    try:
        with urlopen(req,timeout=24) as resp:
            if resp.status!=200 or resp.geturl()!=url:raise CoverageError('Non-200 or redirect')
            body=resp.read(MAX_BYTES+1)
    except (URLError,HTTPError,TimeoutError) as exc:
        raise CoverageError(f'Source unavailable ({type(exc).__name__})') from None
    if not body or len(body)>MAX_BYTES:raise CoverageError('Source size invalid')
    return body


def json_games(body):
    try:data=json.loads(body)
    except (UnicodeDecodeError,json.JSONDecodeError) as exc:raise CoverageError('Invalid JSON') from exc
    matches=data.get('matches') if isinstance(data,dict) else None
    if not isinstance(matches,list):raise CoverageError('Missing matches[]')
    done=[];unscored=0;invalid=0;keys=set();duplicates=0
    for r in matches:
        if not isinstance(r,dict):invalid+=1;continue
        score=r.get('score')
        pair=score if isinstance(score,list) else score.get('ft') if isinstance(score,dict) else None
        if not isinstance(pair,list) or len(pair)!=2:
            unscored+=1;continue
        h,a=r.get('team1'),r.get('team2');d=r.get('date')
        if not isinstance(h,str) or not isinstance(a,str) or not h.strip() or not a.strip() or h==a or not isinstance(d,str):invalid+=1;continue
        try:
            day=date.fromisoformat(d)
            x,y=int(pair[0]),int(pair[1])
            if not (0<=x<=30 and 0<=y<=30) or str(x)!=str(pair[0]) or str(y)!=str(pair[1]):raise ValueError()
        except (TypeError,ValueError):invalid+=1;continue
        k=(d,h.casefold().strip(),a.casefold().strip())
        if k in keys:duplicates+=1;continue
        keys.add(k);done.append((day,h,a,x,y))
    return dict(valid=len(done),listed=len(matches),unscored=unscored,invalid=invalid,duplicates=duplicates,
                distinct_teams=len(set(z for r in done for z in (r[1],r[2]))),dates_available=len(done),
                earliest=str(min((r[0] for r in done),default='')),latest=str(max((r[0] for r in done),default='')))

# OpenFootball TXT historical exports have two score orientations.
# (1) Team A v Team B 2-1 and (2) Team A 2-1 (1-1) Team B.
DAY_RE=re.compile(r'^\s*(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+)?(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+)?'
                  r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})(?:\s+(20\d\d))?\s*$')
V_RE=re.compile(r'^(.+?)\s+v\s+(.+?)\s{2,}(\d{1,2})-(\d{1,2})(?:\s|$)')
MIDDLE_RE=re.compile(r'^(.+?)\s{2,}(\d{1,2})-(\d{1,2})(?:\s*\(\d{1,2}-\d{1,2}\))?\s{2,}(.+?)\s*$')
TIME_RE=re.compile(r'^\s*(?:\d{1,2}:\d{2}\s+)?')
SCORE_TOKEN=re.compile(r'\b\d{1,2}-\d{1,2}\b')


def txt_games(body,season_label,calendar_year=False):
    try:text=body.decode('utf-8-sig')
    except UnicodeDecodeError as exc:raise CoverageError('Invalid UTF-8 TXT') from exc
    year=int(season_label[:4]);current=None;done=[];seen=set();dups=0;bad=0;unscored=0
    for line in text.splitlines():
        dm=DAY_RE.match(line.strip())
        if dm:
            mon,dd,yyyy=dm.groups()
            nmonth=datetime.strptime(mon,'%b').month
            y=int(yyyy) if yyyy else (year+1 if not calendar_year and '-' in season_label and nmonth<=6 else year)
            try:current=date(y,nmonth,int(dd))
            except ValueError:current=None
            continue
        if not line.startswith(('  ','\t')) or line.lstrip().startswith(('#','(', '▪')):continue
        s=TIME_RE.sub('',line).rstrip();v=V_RE.match(s)
        m=MIDDLE_RE.match(s) if v is None else None
        if v:
            h,a,x,y=v.group(1).strip(),v.group(2).strip(),v.group(3),v.group(4)
        elif m:
            h,a,x,y=m.group(1).strip(),m.group(4).strip(),m.group(2),m.group(3)
        else:
            # TXT can list future or rescheduled fixtures without a score.
            if re.search(r'\s+v\s+',s):unscored+=1
            continue
        if not h or not a or h.casefold()==a.casefold() or current is None or not (0<=int(x)<=30 and 0<=int(y)<=30):
            bad+=1;continue
        k=(current,h.casefold(),a.casefold())
        if k in seen:dups+=1;continue
        seen.add(k);done.append((current,h,a,int(x),int(y)))
    header=re.search(r'^#\s*Matches\s+(\d+)',text,re.M)
    expected=int(header.group(1)) if header else None
    return dict(valid=len(done),listed=expected,unscored=unscored,invalid=bad,duplicates=dups,
                distinct_teams=len(set(z for r in done for z in (r[1],r[2]))),dates_available=len(done),
                earliest=str(min((r[0] for r in done),default='')),latest=str(max((r[0] for r in done),default='')))


def current_csv(body):
    try:rows=list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
    except (UnicodeDecodeError,csv.Error) as exc:raise CoverageError('Invalid current CSV') from exc
    if not rows:raise CoverageError('Empty current CSV')
    fields=set(rows[0]);need={'home','away','date','ft_home','ft_away'}
    if not need.issubset(fields):raise CoverageError('Current CSV schema mismatch')
    valid=0;bad=0;duplicates=0;teams=set();keys=set();dates=[]
    for r in rows:
        try:
            day=date.fromisoformat(r['date']);h=r['home'].strip();a=r['away'].strip()
            hg,ag=int(r['ft_home']),int(r['ft_away'])
            if not h or not a or h==a or str(hg)!=r['ft_home'] or str(ag)!=r['ft_away'] or not (0<=hg<=30 and 0<=ag<=30):raise ValueError()
        except (KeyError,ValueError,TypeError):bad+=1;continue
        k=(str(day),h.casefold(),a.casefold())
        if k in keys:duplicates+=1;continue
        keys.add(k);teams.update([h,a]);dates.append(day);valid+=1
    return dict(valid=valid,listed=len(rows),invalid=bad,duplicates=duplicates,distinct_teams=len(teams),
                earliest=str(min(dates,default='')),latest=str(max(dates,default='')))


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--registry',type=Path,required=True)
    p.add_argument('--output-json',type=Path,required=True)
    p.add_argument('--output-markdown',type=Path,required=True)
    p.add_argument('--workers',type=int,default=3)
    p.add_argument('--now',help='UTC timestamp override for reproducible tests')
    opts=p.parse_args(argv)
    registry=json.loads(opts.registry.read_text('utf-8'))
    if len(registry)!=25 or len({r['id'] for r in registry})!=25:raise CoverageError('Registry must contain 25 unique leagues')
    targets={}
    for league in registry:
        hist=league['history'];cur=league['current']
        if len(hist)!=3 or list(hist)!=list(EXPECTED_SEASON_KEYS):raise CoverageError('Exactly 3 ordered history sources required')
        for year,entry in hist.items():targets[(entry['repo'],entry['path'])]=None
        targets[(cur['repo'],cur['path'])]=None
    def fetch(k):
        try:body=github_raw(*k);return (k,body,None)
        except CoverageError as e:return (k,None,str(e))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(3,opts.workers))) as pool:
        for key,body,error in pool.map(fetch,targets):targets[key]=(body,error)
    now=datetime.fromisoformat(opts.now.replace('Z','+00:00')) if opts.now else datetime.now(timezone.utc)
    report=[];success=0;source_read=0;incomplete=[]
    for league in registry:
        audits=[];n=0;errors=[]
        for label,src in league['history'].items():
            body,error=targets[(src['repo'],src['path'])]
            if error:
                audits.append({'season':label,'error':error});errors.append(label+': '+error);continue
            source_read+=1
            try:
                stat=json_games(body) if src['path'].endswith('.json') else txt_games(body,label,calendar_year=bool(re.search(r'/20\d{2}_[^/]+\.txt$',src['path'])))
                stat.update({'season':label,'repo':src['repo'],'path':src['path'],
                             'sha256':hashlib.sha256(body).hexdigest()})
                if not stat['valid'] or stat['invalid'] or stat['duplicates'] or not stat['dates_available']:
                    errors.append(label+': invalid or duplicate records')
                if stat['listed'] is not None and stat['valid']!=stat['listed']:
                    incomplete.append((league['id'],label,stat['valid'],stat['listed']))
                n+=stat['valid']
                audits.append(stat)
            except (CoverageError,ValueError) as e:
                audits.append({'season':label,'error':type(e).__name__});errors.append(label+': '+type(e).__name__)
        cur=league['current'];body,error=targets[(cur['repo'],cur['path'])]
        if error:curstat={'error':error};errors.append('current: '+error)
        else:
            source_read+=1
            try:
                curstat=current_csv(body)
                curstat['sha256']=hashlib.sha256(body).hexdigest();curstat['repo']=cur['repo'];curstat['path']=cur['path']
                if curstat['invalid'] or curstat['duplicates'] or curstat['valid']<1:errors.append('current: invalid or duplicate records')
                if curstat['latest'] and date.fromisoformat(curstat['latest'])>now.date():errors.append('current: future result date')
            except CoverageError as e:curstat={'error':str(e)};errors.append('current: invalid CSV')
        source_ok=len(audits)==3 and all('valid' in a for a in audits) and 'valid' in curstat
        # A basic source check demands all reported scheduled games to be scored;
        # counting >500 from PARTIAL seasons must never become an approval.
        complete=source_ok and all(x.get('listed') is not None and x['valid']==x['listed'] for x in audits)
        potential=complete and n>=500 and not errors
        if potential:success+=1
        report.append({'id':league['id'],'name':league['name'],'region':league['region'],
            'historical_total_valid':n,'historical_seasons':audits,'current':curstat,
            'minimum_500':n>=500,'historical_file_completeness':complete,
            'candidate_source_quality_pass':potential,
            'ga2_ready':False,'ga2_model_unchanged':True,'errors':errors,
            'needs':['independent result completeness validation','cross-season team identity reconciliation',
                     'current source team mapping','GA2 monthly cutoff audit','individual competition format audit']})
    numeric_met=sum(x['minimum_500'] for x in report)
    complete_seasons=sum(x['historical_file_completeness'] for x in report)
    final={'status':'COVERAGE_RESEARCH_ONLY','generated_at_utc':now.isoformat(),
        'requested_leagues':25,'source_files':len(targets),'source_files_read':source_read,
        'historical_numeric_minimum_500':numeric_met,
        'all_three_season_files_complete':complete_seasons,
        'basic_source_checks_passed':success,'ga2_ready':0,
        'full_historical_count_incomplete':incomplete,
        'warning':'Presence and parser tests are NOT 3-season ground-truth parity, team identity mapping, or predictive validation.',
        'license':'Honor original repositories/source rights. Noncommercial open-matchday with attribution.',
        'leagues':report}
    opts.output_json.parent.mkdir(parents=True,exist_ok=True)
    opts.output_markdown.parent.mkdir(parents=True,exist_ok=True)
    opts.output_json.write_text(json.dumps(final,indent=2,ensure_ascii=False)+'\n','utf-8')
    table=['# BETPILOT | 25-league coverage audit','',f"Generated UTC: {now.isoformat()}",
         f"Historical files inspected: {source_read} / {len(targets)} requested source file(s).",
         f"Historic >=500: {numeric_met}/25; all three historic files internally complete: {complete_seasons}/25; basic candidates: {success}/25. **GA2 certified: 0/25**.",'',
         '| League | Historical valid FT | Current FT | 2023/24 | 2024/25 | 2025/26 | Basic check |',
         '|---|---:|---:|---:|---:|---:|---|']
    for a in report:
        s=[f"{x.get('valid','ERR')}/{x.get('listed','?')}" for x in a['historical_seasons']]
        c=a['current'].get('valid','ERR')
        table.append(f"| {a['name']} | {a['historical_total_valid']} | {c} | {' | '.join(s)} | {'COMPLETE*' if a['candidate_source_quality_pass'] else 'REVIEW'} |")
    table+=['','**CAUTION:** COMPLETE* means only the file-reported count is present, not independent season coverage or GA2 readiness. Argentina 2025 claims 240 matches (only the opening stage), EFL Championship includes playoffs; treat formats as review-required. Never auto-promote to live predictions.']
    opts.output_markdown.write_text('\n'.join(table)+'\n','utf-8')
    print('COVERAGE_AUDIT_COMPLETED:',len(report),'leagues,',source_read,'downloaded files; basic candidates',success)
    print('SCORE_FILE_INCOMPLETE:',len(incomplete),'NUMERIC_500:',numeric_met,'ALL_3_FILE_COMPLETE:',complete_seasons)
    for a in report:print('LEAGUE_AUDIT',a['id'],'historical',a['historical_total_valid'],'current',a['current'].get('valid'),'basic',a['candidate_source_quality_pass'],'errors',len(a['errors']))
    return 0

if __name__=='__main__':raise SystemExit(main())
