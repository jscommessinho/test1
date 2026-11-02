"""Read-only, fail-closed PitchAPI fixture + as-of historical evidence preflight.

This is NOT a native GA3/GTS3 publisher. It deliberately never creates an
MA3-compatible native JSON or claims a prediction has been computed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

from betpilot.pitchapi_gts_probe import Client, ident
from betpilot_v3.full_analysis import parse_request

SCHEMA = 'BETPILOT_PITCHAPI_PREFLIGHT_V1'
WAREHOUSE_SCHEMA = 'BETPILOT_PITCHAPI_WORLD_DAILY_v1'


class PreflightBlocked(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def instant(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.utcoffset() is None:
            raise ValueError('timezone required')
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        raise PreflightBlocked('INVALID_TIMESTAMP', str(value)[:100]) from exc


def exact(value):
    return ' '.join(value.split()).casefold() if isinstance(value, str) else ''


def require(condition, code, message):
    if not condition:
        raise PreflightBlocked(code, message)


def discover(client, request, day, country, competition=None, *, captured_at=None):
    """At most three allowlisted GETs; no fuzzy name/ID reconciliation."""
    home, away = parse_request(request)
    from betpilot_v3.general_fixture import team
    require(re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) is not None,
            'INVALID_DATE', 'UTC date required')
    matches = client.get('/v1/date/' + day, {'status': 'all'}).get('matches')
    require(isinstance(matches, list), 'FIXTURE_FEED_INVALID', 'Missing match list')
    found = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        if team((item.get('home_team') or {}).get('name')) != team(home):
            continue
        if team((item.get('away_team') or {}).get('name')) != team(away):
            continue
        if instant(item.get('time_utc')).date().isoformat() != day:
            continue
        found.append(item)
    require(len(found) == 1, 'AMBIGUOUS_OR_MISSING_FIXTURE',
            'Expected exactly one fixture with exact teams and UTC date')
    f = found[0]
    mid = ident(f.get('id'), 'm')
    ht = ident((f.get('home_team') or {}).get('id'), 't')
    at = ident((f.get('away_team') or {}).get('id'), 't')
    require(ht != at, 'TEAM_ID_CONFLICT', 'Same team ID')
    kickoff = instant(f.get('time_utc'))
    require(f.get('status') not in ('finished', 'live', 'in_progress'),
            'TARGET_NOT_PREMATCH', 'Target has started or finished')
    detail = client.get('/v1/matches/' + mid)
    require(detail.get('id') == mid and
            (detail.get('home_team') or {}).get('id') == ht and
            (detail.get('away_team') or {}).get('id') == at,
            'FIXTURE_DETAIL_MISMATCH', 'PitchAPI fixture/detail disagree')
    lid = ident((detail.get('league') or {}).get('id'), 'l')
    listing_lid = f.get('league_id') or (f.get('league') or {}).get('id')
    if listing_lid is not None:
        require(listing_lid == lid, 'LEAGUE_ID_CONFLICT', 'Fixture/detail league mismatch')
    leagues = client.get('/v1/leagues').get('leagues')
    require(isinstance(leagues, list), 'LEAGUE_CATALOGUE_INVALID', 'Missing league catalogue')
    verified = [x for x in leagues if isinstance(x, dict) and x.get('id') == lid
                and (country is None or x.get('country_code') == country)]
    require(len(verified) == 1, 'LEAGUE_UNVERIFIED', 'League/country mapping not unique')
    league = verified[0]
    if competition is not None:
        require(exact(league.get('name')) == exact(competition), 'LEAGUE_UNVERIFIED',
                'Competition name does not match provider identity')
    capture = captured_at or datetime.now(timezone.utc)
    require(capture.tzinfo is not None and capture < kickoff, 'TARGET_NOT_PREMATCH',
            'Fixture is no longer future prematch')
    season = detail.get('season')
    require(isinstance(season, str) and season in (league.get('seasons') or []),
            'SEASON_UNVERIFIED', 'Provider fixture season missing or unverified')
    return {'provider': 'PitchAPI', 'pitchapi_event_id': mid,
            'pitchapi_league_id': lid, 'competition': league['name'],
            'country': league['country_code'], 'season': season, 'home': f['home_team'],
            'away': f['away_team'], 'kickoff': kickoff.isoformat(),
            'fixture_captured_at_utc': capture.astimezone(timezone.utc).isoformat()}


def read_archive(path):
    try:
        with gzip.open(path, 'rt', encoding='utf8') as stream:
            doc = json.load(stream)
    except (OSError, ValueError, UnicodeError, EOFError) as exc:
        raise PreflightBlocked('ARCHIVE_INVALID', path.name) from exc
    require(doc.get('schema') == WAREHOUSE_SCHEMA and
            isinstance(doc.get('matches'), dict) and
            doc.get('date_utc') == path.name.removesuffix('.json.gz'),
            'ARCHIVE_INVALID', 'Partition/schema mismatch: ' + path.name)
    return doc


def history(warehouse, target, cutoff, *, league_archives=()):
    """Keep only records actually captured pre-cutoff; no retrospective guessing."""
    root = Path(warehouse)
    require(root.is_dir(), 'WAREHOUSE_MISSING', 'Read-only local archive root unavailable')
    chosen = {}
    diagnostics = {'after_cutoff': 0, 'qa_unusable': 0, 'wrong_league': 0,
                   'incomplete': 0, 'other': 0}
    seen = set()
    for path in sorted(root.glob('daily/*/*/*.json.gz')):
        if path.is_symlink():
            raise PreflightBlocked('UNSAFE_ARCHIVE', 'Symlink not allowed')
        doc = read_archive(path)
        for mid, record in doc['matches'].items():
            require(mid not in seen, 'DUPLICATE_ARCHIVE_ID',
                    'Duplicate source ID across day partitions: ' + mid)
            seen.add(mid)
            if mid == target['pitchapi_event_id']:
                continue
            if not isinstance(record, dict) or record.get('status') != 'PASS_INTERNAL':
                diagnostics['qa_unusable'] += 1
                continue
            f = record.get('fixture') or {}
            lid = f.get('league_id') or (f.get('league') or {}).get('id')
            if lid != target['pitchapi_league_id']:
                diagnostics['wrong_league'] += 1
                continue
            try:
                retrieved = instant(record.get('retrieved_at_utc'))
                kickoff = instant(f.get('time_utc'))
            except PreflightBlocked:
                diagnostics['incomplete'] += 1
                continue
            if retrieved > cutoff or kickoff >= cutoff or kickoff > retrieved:
                diagnostics['after_cutoff'] += 1
                continue
            home = f.get('home_team') or {}
            away = f.get('away_team') or {}
            if f.get('id') != mid or f.get('status') != 'finished':
                diagnostics['incomplete'] += 1
                continue
            if home.get('id') == away.get('id') or not all(
                    isinstance(t.get('id'), str) and t['id'].startswith('t_')
                    for t in (home, away)):
                diagnostics['incomplete'] += 1
                continue
            if not any(x['id'] in (home['id'], away['id'])
                       for x in (target['home'], target['away'])):
                diagnostics['other'] += 1
                continue
            stats = (record.get('home_shots'), record.get('away_shots'))
            if any(not isinstance(s, dict) or
                   isinstance(s.get('xg'), bool) or
                   not isinstance(s.get('xg'), (int, float)) or
                   not math.isfinite(s['xg']) or s['xg'] < 0 or
                   isinstance(s.get('shots'), bool) or
                   not isinstance(s.get('shots'), int) or s['shots'] < 0
                   for s in stats):
                diagnostics['incomplete'] += 1
                continue
            chosen[mid] = {'match_id': mid, 'kickoff': kickoff.isoformat(),
                           'retrieved_at_utc': retrieved.isoformat(),
                           'league_id': lid, 'home_team_id': home['id'],
                           'away_team_id': away['id'],
                           'home': {'xg': stats[0]['xg'], 'shots': stats[0]['shots'],
                                    'goals': f.get('score_home')},
                           'away': {'xg': stats[1]['xg'], 'shots': stats[1]['shots'],
                                    'goals': f.get('score_away')},
                           'qa': 'PASS_INTERNAL',
                           'archive_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    # Per-league historical snapshots are produced by the existing 77-league
    # workflows. These are optional, separately mounted read-only artifacts,
    # NOT reconstructed or downloaded again by this preflight.
    from betpilot.pitchapi_history_archive import load_archive
    for apath in league_archives:
        archive_path = Path(apath)
        require(archive_path.is_file() and not archive_path.is_symlink(),
                'HISTORY_ARCHIVE_MISSING', 'Missing or unsafe per-league source')
        try:
            raw = json.loads(archive_path.read_text('utf8'))
            league = raw['league']
            require(league['id'] == target['pitchapi_league_id'] and
                    league['name'] == target['competition'] and
                    league['country'] == target['country'],
                    'HISTORY_ARCHIVE_IDENTITY', 'League archive ID/name/country mismatch')
            validated = load_archive(archive_path, league, raw['as_of_utc_date'])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, PreflightBlocked):
                raise
            raise PreflightBlocked('HISTORY_ARCHIVE_INVALID', archive_path.name) from exc
        archive_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        for mid, rec in validated['records'].items():
            if mid == target['pitchapi_event_id']:
                continue
            retrieved = instant(rec.get('retrieved_at_utc'))
            record_day = date.fromisoformat(rec['date'])
            if retrieved > cutoff or record_day >= cutoff.date() or retrieved.date() < record_day:
                diagnostics['after_cutoff'] += 1
                continue
            h = rec['home']
            a = rec['away']
            if not any(x['id'] in (h['id'], a['id'])
                       for x in (target['home'], target['away'])):
                diagnostics['other'] += 1
                continue
            if any(not isinstance(t['goals'], int) or isinstance(t['goals'], bool)
                   or not 0 <= t['goals'] <= 30 or
                   not isinstance(t['shots'], int) or isinstance(t['shots'], bool)
                   or t['shots'] < 0 or
                   not isinstance(t['xg'], (int, float)) or isinstance(t['xg'], bool)
                   or not math.isfinite(t['xg']) or t['xg'] < 0
                   for t in (h, a)):
                diagnostics['incomplete'] += 1
                continue
            candidate = {'match_id': mid, 'date': rec['date'],
                         'retrieved_at_utc': retrieved.isoformat(),
                         'league_id': target['pitchapi_league_id'],
                         'season': rec['season'], 'home_team_id': h['id'],
                         'away_team_id': a['id'],
                         'home': {'goals': h['goals'], 'xg': h['xg'], 'shots': h['shots']},
                         'away': {'goals': a['goals'], 'xg': a['xg'], 'shots': a['shots']},
                         'qa': 'PASS_INTERNAL', 'archive_sha256': archive_digest,
                         'record_digest': rec['source_digest'],
                         'source_kind': 'PITCHAPI_PINNED_LEAGUE_HISTORY'}
            if mid in chosen:
                old = chosen[mid]
                require(old['home_team_id'] == h['id'] and
                        old['away_team_id'] == a['id'] and
                        all(old[side]['goals'] == candidate[side]['goals'] and
                            old[side]['shots'] == candidate[side]['shots'] and
                            abs(old[side]['xg'] - candidate[side]['xg']) < 0.00001
                            for side in ('home', 'away')),
                        'HISTORY_SOURCE_CONFLICT',
                        'Conflicting per-day/per-league records for ' + mid)
                if instant(old['retrieved_at_utc']) <= retrieved:
                    continue
            chosen[mid] = candidate
    h, a = target['home']['id'], target['away']['id']
    home_all = [r for r in chosen.values() if h in (r['home_team_id'], r['away_team_id'])]
    away_all = [r for r in chosen.values() if a in (r['home_team_id'], r['away_team_id'])]
    home_venue = [r for r in home_all if r['home_team_id'] == h]
    away_venue = [r for r in away_all if r['away_team_id'] == a]
    counts = {'home_all': len(home_all), 'away_all': len(away_all),
              'home_at_home': len(home_venue), 'away_away': len(away_venue)}
    require(min(counts['home_all'], counts['away_all']) >= 10 and
            min(counts['home_at_home'], counts['away_away']) >= 4,
            'HISTORY_INSUFFICIENT', 'GTS research minimum 10 team, 4 venue not reached: '
            + json.dumps({'counts': counts, 'rejected': diagnostics}, sort_keys=True))
    return {'schema': SCHEMA + '_ASOF', 'target_event_id': target['pitchapi_event_id'],
            'cutoff': cutoff.isoformat(), 'matches': sorted(chosen.values(),
            key=lambda v: (v.get('date', v.get('kickoff', '')), v['match_id'])), 'coverage': counts,
            'excluded': diagnostics, 'publication_timestamps_independently_verified': False,
            'proof_type': 'collector response captured pre-cutoff (no independent publication timestamp)'}



def find_existing_league_archives(history_root, target, cutoff):
    """Discover ONLY already-existing original pinned PitchAPI history archives."""
    root = Path(history_root)
    require(root.is_dir() and not root.is_symlink(),
            'HISTORY_ROOT_MISSING', 'Existing local league archives directory unavailable')
    eligible = []
    # Query archive *filenames*, not all .json files: the real historical
    # source checkout can contain thousands of raw /stats JSON payloads.
    # Inspect only exact archive checkpoint paths used by the collectors.
    files = sorted({*root.rglob('archive.json'), *root.rglob('*_archive.json')})
    require(len(files) <= 1000, 'HISTORY_ARCHIVE_INDEX_TOO_LARGE',
            'Too many historical archive checkpoint candidates')
    for path in files:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            if path.stat().st_size > 80_000_000:
                continue
            record = json.loads(path.read_text('utf8'))
            if record.get('schema') != 'BETPILOT-PITCHAPI-HISTORY-ARCHIVE-RESEARCH-v1':
                continue
            league = record.get('league') or {}
            if not (record.get('provider') == 'PitchAPI'
                    and league.get('id') == target['pitchapi_league_id']
                    and league.get('name') == target['competition']
                    and league.get('country') == target['country']):
                continue
            asof = date.fromisoformat(record['as_of_utc_date'])
            if asof <= cutoff.date():
                eligible.append((asof, path))
        except (OSError, TypeError, ValueError, KeyError, UnicodeError):
            continue
    if not eligible:
        raise PreflightBlocked('HISTORY_ARCHIVE_NOT_FOUND',
                               'No exact pinned pre-cutoff original PitchAPI archive')
    best = max(d for d, _ in eligible)
    selected = [path for day, path in eligible if day == best]
    require(len(selected) == 1, 'HISTORY_ARCHIVE_AMBIGUOUS',
            'Conflicting original archives with identical as-of cutoff')
    return selected[0]


def publish(client, request, day, country, archive_root, output_root,
            *, competition=None, cutoff=None, captured_at=None, league_archives=(), history_root=None):
    out = Path(output_root)
    out.mkdir(parents=True, exist_ok=True)
    status = {'schema': SCHEMA, 'mode': 'RESEARCH_ONLY', 'betting_enabled': False,
              'ga3_native_executed': False, 'gts3_native_executed': False,
              'full_analysis_ready': False}
    try:
        target = discover(client, request, day, country, competition,
                          captured_at=captured_at)
        captured = instant(target['fixture_captured_at_utc'])
        cut = instant(cutoff) if cutoff is not None else captured
        require(captured <= cut < instant(target['kickoff']), 'INVALID_CUTOFF',
                'As-of must follow observation and precede kickoff')
        if history_root is not None:
            require(not league_archives, 'HISTORY_SOURCE_AMBIGUOUS',
                    'Choose discovery or explicit history paths, not both')
            league_archives=(find_existing_league_archives(history_root,target,cut),)
        selected = history(archive_root, target, cut, league_archives=league_archives)
        files = {'PITCHAPI_FIXTURE_CANDIDATE.json': target,
                 'PITCHAPI_ASOF_HISTORY.json': selected}
        digests = {}
        for filename, obj in files.items():
            data = (json.dumps(obj, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
            (out / filename).write_bytes(data)
            digests[filename] = hashlib.sha256(data).hexdigest()
        status.update(status='PITCHAPI_ASOF_PREFLIGHT_PASSED_NATIVE_PENDING',
                      file_sha256=digests, historical_coverage=selected['coverage'])
    except (PreflightBlocked, ValueError, KeyError, TypeError) as exc:
        status.update(status='NO_BET_SOURCE_PREFLIGHT_BLOCKED',
                      reason_code=getattr(exc, 'code', 'SOURCE_INVALID'),
                      reason=str(exc)[:900])
    (out / 'PITCHAPI_PREFLIGHT_STATUS.json').write_text(
        json.dumps(status, indent=2, sort_keys=True, allow_nan=False) + '\n', 'utf8')
    return status


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request', required=True)
    p.add_argument('--day-utc', required=True)
    p.add_argument('--country-code', required=True)
    p.add_argument('--competition')
    p.add_argument('--cutoff')
    p.add_argument('--warehouse-root', type=Path, required=True)
    p.add_argument('--league-archive', type=Path, action='append', default=[],
                   help='Read-only existing per-league historical artifact (repeatable)')
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args(argv)
    if not os.environ.get('PITCHAPI_API_KEY'):
        args.output_dir.mkdir(parents=True, exist_ok=True)
        status = {'schema': SCHEMA, 'status': 'NO_BET_SOURCE_PREFLIGHT_BLOCKED',
                  'reason_code': 'PITCHAPI_KEY_UNAVAILABLE', 'mode': 'RESEARCH_ONLY',
                  'full_analysis_ready': False, 'requests_performed': 0}
        (args.output_dir / 'PITCHAPI_PREFLIGHT_STATUS.json').write_text(
            json.dumps(status, indent=2) + '\n', 'utf8')
        print(json.dumps(status))
        return 2
    client = Client(os.environ['PITCHAPI_API_KEY'], budget=3, pause=.2)
    result = publish(client, args.request, args.day_utc, args.country_code,
                     args.warehouse_root, args.output_dir,
                     competition=args.competition, cutoff=args.cutoff,
                     league_archives=args.league_archive)
    print(json.dumps(result, indent=2))
    return 0 if result['status'].endswith('NATIVE_PENDING') else 2


if __name__ == '__main__':
    raise SystemExit(main())
