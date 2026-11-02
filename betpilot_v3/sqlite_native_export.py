"""Verified source-only SQLite -> original GA3 and GTS3 input layouts.

This never modifies private SQLite. It reads original gzipped PitchAPI `/stats`
responses, verifies the saved SHA256, and validates every observation with
*the pinned, unmodified GTS3 original parser*. GA3 source xG is explicitly
TEAM /stats All expected_goals (NOT per-shot reconstructed xG). No invented
shot feed, timestamps, matches, lineups, odds, or model coefficients.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import json
import math
import sqlite3
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from betpilot.pitchapi_history_archive import VERSION, canonical, sha256, save, load_archive
from betpilot_v3.pitchapi_preflight import instant
from betpilot_v3.sqlite_source import _files

SCHEMA='BETPILOT_SQLITE_PITCHAPI_NATIVE_SOURCE_V1'
ALLOWED={'eng-premier','ita-seriea','fra-ligue1','esp-laliga','ger-bundesliga'}
PIN_NAMES={'eng-premier':('Premier League','ENG',('Premier League',)),
           'ita-seriea':('Serie A','ITA',('Serie A',)),
           'fra-ligue1':('Ligue 1','FRA',('Ligue 1',)),
           'esp-laliga':('LaLiga','ESP',('La Liga','LaLiga')),
           'ger-bundesliga':('Bundesliga','GER',('Bundesliga',))}

class SourceError(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__(code)


def verified_original_validator(repo_root=None):
    root=Path(repo_root or Path(__file__).resolve().parents[1])
    manifest=json.loads((root/'betpilot_v3/SOURCE_ZIPS_SHA256.json').read_text('utf8'))['original_zip_sha256']
    name='BETPILOT_GTS3_RC1_2026-11-01(1).zip'
    path=root/'betpilot_v3/frozen'/name
    if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest[name]:
        raise SourceError('GTS3_FROZEN_SOURCE_HASH_MISMATCH')
    with zipfile.ZipFile(path) as z:
        if 'BETPILOT_GTS3_RC1/src/gts3/source.py' not in z.namelist():
            raise SourceError('GTS3_FROZEN_VALIDATOR_NOT_FOUND')
    original_pkg=str(path)+'/BETPILOT_GTS3_RC1/src'
    if original_pkg not in sys.path:sys.path.insert(0,original_pkg)
    return importlib.import_module('gts3.source').extract_stats


def _readonly(path):
    p=Path(path).resolve()
    uri='file:'+quote(str(p),safe='/')+'?mode=ro'
    conn=sqlite3.connect(uri,uri=True,timeout=5)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


def convert(sqlite_root,history_root,team_stats_root,*,cutoff=None,repo_root=None,
            validator=None, league=None):
    """Build audited, ephemeral *source adapters* for supported original leagues.

    These are not official pre-match historical release snapshots; run-time
    evaluation must still verify cutoff, enough fixture samples, bookmaker
    provenance, and the unchanged original model gates.
    """
    now=instant(cutoff) if isinstance(cutoff,str) else cutoff or datetime.now(timezone.utc)
    if now.tzinfo is None:raise SourceError('CUTOFF_NOT_UTC_AWARE')
    now=now.astimezone(timezone.utc)
    validate=validator or verified_original_validator(repo_root)
    root=Path(repo_root or Path(__file__).resolve().parents[1])
    pins=json.loads((root/'data/pitchapi/league_pins_77.json').read_text('utf8'))
    if pins.get('schema')!='BETPILOT_PITCHAPI_LEAGUE_PINNED_77_v1':
        raise SourceError('PINNED_PITCHAPI_REGISTRY_INVALID')
    trusted={}
    for slug,(name,country,allowed_aliases) in PIN_NAMES.items():
        ids={v.get('league_id') for v in pins['leagues'].values()
             if v.get('league_name')==name and v.get('country_code')==country}
        if len(ids)!=1 or not next(iter(ids)).startswith('l_'):
            raise SourceError('PINNED_PITCHAPI_LEAGUE_NOT_UNIQUE')
        trusted[slug]={'id':next(iter(ids)),'name':name,'country':country,
                       'allowed_source_aliases':allowed_aliases}
    cutoff_day=now.date()
    histories=defaultdict(dict);stats=defaultdict(dict);leagues={}
    counters=Counter();digests=defaultdict(set)
    chosen=_files(Path(sqlite_root))
    for path in chosen:
        if path.name=='catalogue.sqlite':continue
        parts=path.relative_to(Path(sqlite_root).resolve()).parts
        if len(parts)!=2 or parts[0] not in ALLOWED or (league and parts[0]!=league):
            continue
        slug=parts[0]
        with _readonly(path) as c:
            tables={x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'source_responses','fixtures','teams','leagues'} <= tables:
                raise SourceError('ORIGINAL_SOURCE_TABLES_MISSING')
            info=c.execute('SELECT pitchapi_league_id,name,country_code FROM leagues WHERE slug=?',(slug,)).fetchone()
            if not info:
                raise SourceError('ORIGINAL_LEAGUE_METADATA_MISSING')
            pin=trusted[slug]
            # Older v4 seasonal SQLite files may leave league ID NULL and keep
            # the provider ID only in the separately verified PitchAPI catalogue.
            # The independent PitchAPI 77-league registry resolves this *only*
            # under an exact country and explicit published source-name alias.
            if info['country_code']!=pin['country'] or info['name'] not in pin['allowed_source_aliases']:
                raise SourceError('ORIGINAL_LEAGUE_REGISTRY_CONFLICT')
            if info['pitchapi_league_id'] not in (None,'',pin['id']):
                raise SourceError('ORIGINAL_LEAGUE_PROVIDER_ID_CONFLICT')
            meta={'id':pin['id'],'name':pin['name'],'country':pin['country']}
            if slug in leagues and leagues[slug]!=meta:
                raise SourceError('CONFLICTING_ORIGINAL_LEAGUE_IDENTITIES')
            leagues[slug]=meta
            q='''SELECT f.pitchapi_fixture_id id,f.season_label season,
                    f.kickoff_utc kickoff,f.fixture_status status,
                    f.home_goals hg,f.away_goals ag,f.raw_json fixture_raw,
                    h.team_name hn,a.team_name an,f.home_team_id hid,f.away_team_id aid
                 FROM fixtures f
                 JOIN teams h ON h.pitchapi_team_id=f.home_team_id
                 JOIN teams a ON a.pitchapi_team_id=f.away_team_id
                 WHERE f.league_slug=?'''
            for f in c.execute(q,(slug,)):
                counters['listed']+=1
                try:
                    if str(f['status']).lower() not in ('finished','ft','played','completed'):
                        counters['not_finished']+=1;continue
                    kickoff=instant(f['kickoff'])
                    if kickoff>=now or kickoff.date()+timedelta(days=3)>cutoff_day:
                        counters['not_mature']+=1;continue
                    if any(not isinstance(f[k],int) or isinstance(f[k],bool) or not 0<=f[k]<=30 for k in ('hg','ag')):
                        counters['no_result']+=1;continue
                    mid=f['id']
                    if not(isinstance(mid,str) and mid.startswith('m_') and
                           isinstance(f['hid'],str) and f['hid'].startswith('t_') and
                           isinstance(f['aid'],str) and f['aid'].startswith('t_') and f['hid']!=f['aid']):
                        counters['invalid_identity']+=1;continue
                    season=f['season']
                    if not isinstance(season,str) or not season or len(season)>24:
                        counters['invalid_season']+=1;continue
                    source=c.execute('''SELECT body_gzip,sha256_hex,original_bytes,fetched_at_utc
                        FROM source_responses WHERE dataset='stats' AND match_id=?
                        ORDER BY fetched_at_utc DESC,response_id DESC''',(mid,)).fetchall()
                    if not source:
                        counters['missing_stats']+=1;continue
                    item=None;observed=None;raw_digest=None
                    for response in source:
                        try:
                            at=instant(response['fetched_at_utc'])
                            if not kickoff<=at<=now:continue
                            raw=gzip.decompress(response['body_gzip'])
                            hx=hashlib.sha256(raw).hexdigest()
                            if hx!=response['sha256_hex'] or len(raw)!=response['original_bytes']:
                                raise SourceError('ORIGINAL_PROVIDER_SHA256_MISMATCH')
                            envelope=json.loads(raw)
                            if not isinstance(envelope,dict) or not isinstance(envelope.get('data'),dict):
                                raise SourceError('ORIGINAL_STATS_ENVELOPE_INVALID')
                            parsed=envelope['data']
                            if(parsed.get('match_id')!=mid or
                               parsed.get('home_team',{}).get('id')!=f['hid'] or
                               parsed.get('away_team',{}).get('id')!=f['aid']):
                                raise SourceError('ORIGINAL_MATCH_STATS_IDENTITY_MISMATCH')
                            home,away,_duplicate=validate(parsed,mid,f['hid'],f['aid'])
                            for side in (home,away):
                                xg,shots=side['expected_goals'],side['total_shots']
                                if (isinstance(xg,bool) or isinstance(shots,bool) or
                                    not isinstance(xg,(int,float)) or not math.isfinite(xg) or xg<0 or
                                    not isinstance(shots,(int,float)) or not float(shots).is_integer() or shots<0):
                                    raise SourceError('ORIGINAL_TEAM_XG_SHOTS_INVALID')
                            item=(home,away,parsed);observed=at;raw_digest=hx
                            break
                        except SourceError:
                            raise
                        except (ValueError,TypeError,KeyError,OSError,OverflowError,gzip.BadGzipFile):
                            continue
                    if item is None:
                        counters['unusable_or_late_stats']+=1;continue
                    home,away,raw_stats=item
                    raw_fixture=json.loads(f['fixture_raw'] or '{}')
                    if raw_fixture and (raw_fixture.get('id')!=mid or
                                       (raw_fixture.get('home_team') or {}).get('id')!=f['hid'] or
                                       (raw_fixture.get('away_team') or {}).get('id')!=f['aid']):
                        raise SourceError('ORIGINAL_FIXTURE_RAW_CONFLICT')
                    d=kickoff.date().isoformat()
                    original={'match_id':mid,'league_id':meta['id'],'season':season,
                        'date':d,
                        'home':{'id':f['hid'],'name':f['hn'],'goals':f['hg'],
                                'xg':float(home['expected_goals']),'shots':int(home['total_shots'])},
                        'away':{'id':f['aid'],'name':f['an'],'goals':f['ag'],
                                'xg':float(away['expected_goals']),'shots':int(away['total_shots'])},
                        'internal_absolute_xg_deltas':{},
                        'qa_internal':'PASS','qa_external':'OFFICIAL_REFERENCE_NOT_VERIFIED',
                        'source':'PitchAPI original /stats All team expected_goals; frozen GTS3 native parser verified; not reconstructed per-shot xG',
                        'original_response_sha256':raw_digest,
                        'xg_measurement':'TEAM_STATS_ALL_NOT_SHOT_RECONSTRUCTED'}
                    original['source_digest']=sha256(original)
                    original['retrieved_at_utc']=observed.isoformat()
                    row={'fixture':{'match_id':mid,'date':d,'season':season,
                        'home':{'id':f['hid']},'away':{'id':f['aid']},
                        'home_goals':f['hg'],'away_goals':f['ag']},
                        'retrieved_at_utc':observed.isoformat(),
                        'statistics':raw_stats,'source_response_sha256':raw_digest}
                    if mid in histories[slug] and histories[slug][mid]!=original:
                        raise SourceError('ORIGINAL_DUPLICATE_MATCH_CONFLICT')
                    histories[slug][mid]=original
                    stats[slug][mid]=row
                    digests[slug].add(raw_digest)
                    counters['accepted']+=1
                except SourceError:
                    raise
                except (OSError,TypeError,ValueError,KeyError,json.JSONDecodeError):
                    counters['unusable_fixture']+=1
    hroot=Path(history_root).resolve();sroot=Path(team_stats_root).resolve()
    if hroot == sroot:raise SourceError('HISTORY_STATS_PATH_COLLISION')
    for slug,meta in leagues.items():
        history={'schema':VERSION,'provider':'PitchAPI','league':meta,
            'as_of_utc_date':cutoff_day.isoformat(),
            'measurement':'FT expected_goals from original PitchAPI /stats All team aggregates (not per-shot reconstruction)',
            'source_qa':'ORIGINAL_GTS3_TEAM_STATS_PARSER','external_official_xg_verification':'NOT_PERFORMED',
            'records':histories[slug],'rejected':{},'warnings':{},'runs':[]}
        hpath=hroot/slug/'archive.json'
        if hpath.exists():raise SourceError('REFUSE_OVERWRITE_HISTORY')
        save(hpath,history)
        load_archive(hpath,meta,cutoff_day.isoformat())
        team={'schema':'BETPILOT_PITCHAPI_TEAM_STATS_v1','provider':'PitchAPI',
            'league':meta,'as_of':cutoff_day.isoformat(),'records':stats[slug],
            'source':'ORIGINAL_PRIVATE_SQLITE_VALIDATED_RAW_STATS_ONLY',
            'not_prematch_collected':True}
        spath=sroot/slug/'archive.json'
        if spath.exists():raise SourceError('REFUSE_OVERWRITE_TEAM_STATS')
        save(spath,team)
    return {'schema':SCHEMA,'status':'PITCHAPI_SQLITE_NATIVE_INPUTS_MATERIALIZED',
            'source':'ORIGINAL_PRIVATE_SQLITE_READ_ONLY',
            'model_formulas_changed':False,'xg_from_team_stats_not_shot_reconstruction':True,
            'league_id_source':'VERIFIED_ORIGINAL_PITCHAPI_77_LEAGUE_REGISTRY',
            'not_historically_captured_prematch':True,'leagues':sorted(leagues),
            'history_records':{k:len(v) for k,v in histories.items()},
            'raw_response_hashes_verified':sum(len(x) for x in digests.values()),
            'counts':dict(counters),'cutoff_utc':now.isoformat()}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sqlite-root',type=Path,required=True)
    p.add_argument('--history-root',type=Path,required=True)
    p.add_argument('--team-stats-root',type=Path,required=True)
    p.add_argument('--cutoff-utc',help='Strict UTC as-of timestamp; defaults current UTC')
    p.add_argument('--league',choices=sorted(ALLOWED))
    p.add_argument('--output',type=Path)
    a=p.parse_args(argv)
    result=convert(a.sqlite_root,a.history_root,a.team_stats_root,
                   cutoff=a.cutoff_utc,league=a.league)
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
