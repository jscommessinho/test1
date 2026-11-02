"""Materialize read-only PitchAPI All-period stats for unchanged GTS3 RC1.

Uses only authenticated previously archived worldwide responses, never shot xG.
Outputs the original GTS3 CLI research score, NOT an MA3 native export.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path

from betpilot_v3.pitchapi_preflight import (
    PreflightBlocked, instant, read_archive, require
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_file(path):
    try:
        return json.loads(Path(path).read_text('utf8'))
    except (OSError, ValueError, UnicodeError) as exc:
        raise PreflightBlocked('SOURCE_INVALID', Path(path).name) from exc


def verify_gts3_original(root):
    root = Path(root).resolve()
    manifest = json_file(root / 'MANIFEST.json')
    require(isinstance(manifest.get('files'), dict) and len(manifest['files']) >= 5,
            'GTS3_MANIFEST_MISSING', 'Original frozen GTS3 manifest missing')
    for name, expected in manifest['files'].items():
        p = root / name
        require(isinstance(name, str) and isinstance(expected, str)
                and len(expected) == 64 and p.is_file()
                and p.resolve().is_relative_to(root) and sha(p) == expected,
                'GTS3_SOURCE_CHANGED', 'GTS3 frozen original SHA mismatch: ' + str(name))
    require((root / 'src' / 'gts3' / 'score.py').is_file()
            and (root / 'reports' / 'selected_model_parameters.json').is_file(),
            'GTS3_MISSING', 'Original GTS3 score or model parameters missing')
    return root


def verified_season_index(league_archive, target, cutoff):
    """Use same-match historical season ONLY with exact ID/date/team/result parity."""
    from betpilot.pitchapi_history_archive import load_archive
    path = Path(league_archive)
    require(path.is_file() and not path.is_symlink(), 'GTS3_SEASON_SOURCE_MISSING',
            'Existing pinned history must be supplied read-only')
    source = json_file(path)
    league = source.get('league') or {}
    require(source.get('provider') == 'PitchAPI'
            and league.get('id') == target['pitchapi_league_id']
            and league.get('name') == target['competition']
            and league.get('country') == target['country'],
            'GTS3_SEASON_SOURCE_WRONG_LEAGUE', 'Existing season source league mismatch')
    require(date.fromisoformat(source['as_of_utc_date']) <= cutoff.date(),
            'GTS3_SEASON_SOURCE_LATE', 'Original history archive after cutoff')
    try:
        checked = load_archive(path, league, source['as_of_utc_date'])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PreflightBlocked('GTS3_SEASON_SOURCE_INVALID', str(exc)[:180]) from exc
    index = {}
    for mid, r in checked['records'].items():
        if instant(r['retrieved_at_utc']) > cutoff:
            continue
        index[mid] = {'season': r['season'], 'date': r['date'],
                      'home_team_id': r['home']['id'],
                      'away_team_id': r['away']['id'],
                      'home_goals': r['home']['goals'],
                      'away_goals': r['away']['goals']}
    return index


def collect_original_team_stats_archive(path, target, cutoff, stats_validator):
    """Use existing PitchAPI 26-league full team-statistics collector, read-only.

    Different from historical shot-xG aggregate archives: each record retains
    the provider's actual /stats All payload and provider fixture season.
    """
    p = Path(path)
    require(p.is_file() and not p.is_symlink(),
            'GTS3_TEAM_STATS_ARCHIVE_MISSING',
            'Existing original team-stat archive must be a local safe file')
    raw = json_file(p)
    league = raw.get('league') or {}
    require(raw.get('schema') == 'BETPILOT_PITCHAPI_TEAM_STATS_v1'
            and league.get('id') == target['pitchapi_league_id']
            and league.get('name') == target['competition']
            and league.get('country') == target['country'],
            'GTS3_TEAM_STATS_SOURCE_INVALID',
            'Existing original team-stat archive identity/schema mismatch')
    require(date.fromisoformat(raw['as_of']) <= cutoff.date(),
            'GTS3_TEAM_STATS_AFTER_CUTOFF',
            'Existing collection as-of date later than actual cutoff')
    require(isinstance(raw.get('records'), dict),
            'GTS3_TEAM_STATS_SOURCE_INVALID',
            'Original team-stat archive records missing')
    records = {}
    excluded = Counter()
    for mid, row in raw['records'].items():
        try:
            f = row['fixture']
            observed = instant(row['retrieved_at_utc'])
            match_date = date.fromisoformat(f['date'])
            if observed > cutoff or match_date >= cutoff.date():
                excluded['after_cutoff'] += 1
                continue
            if (date.fromisoformat(target['kickoff'][:10]) - match_date).days <= 2:
                excluded['too_recent'] += 1
                continue
            require(f['match_id'] == mid,
                    'GTS3_TEAM_STATS_ID_CONFLICT', 'Original history event mismatch')
            ht, at = f['home']['id'], f['away']['id']
            require(ht != at and
                    all(isinstance(x, str) and x.startswith('t_') for x in (ht, at)),
                    'GTS3_TEAM_STATS_ID_CONFLICT', 'Invalid source team IDs')
            hg, ag = f['home_goals'], f['away_goals']
            require(all(isinstance(x, int) and not isinstance(x, bool)
                        and 0 <= x <= 30 for x in (hg, ag)),
                    'GTS3_TEAM_STATS_SCORE_INVALID', 'Original final score invalid')
            require(isinstance(f.get('season'), str) and f['season'],
                    'GTS3_TEAM_STATS_SEASON_MISSING', 'Original provider season unavailable')
            stats = row['statistics']
            stats_validator(stats, mid, ht, at)
            records[mid] = {
                'match_id': mid, 'date': f['date'], 'season': f['season'],
                'home': {'id': ht, 'goals': hg},
                'away': {'id': at, 'goals': ag},
                'statistics': stats
            }
        except (KeyError, ValueError, TypeError, AssertionError, PreflightBlocked):
            excluded['unusable_original_stat_record'] += 1
    return records, {'included': len(records),
                     'excluded': dict(excluded),
                     'source_sha256': sha(p),
                     'source_schema': 'BETPILOT_PITCHAPI_TEAM_STATS_v1'}


def collect_stats(warehouse, target, cutoff, *, stats_validator, season_index=None,
                  team_stats_archive=None):
    """Strict original All-period team-stats validation; retain every league match."""
    root = Path(warehouse)
    require(root.is_dir(), 'WAREHOUSE_MISSING', 'Read-only warehouse not available')
    matches = {}
    seen = set()
    exclusions = Counter()
    # Retrieve the existing original /stats archive first, so the current
    # provider season can be anchored before consuming fresh daily data.
    original_team_stats_qa = None
    original_records = {}
    if team_stats_archive is not None:
        original_records, original_team_stats_qa = collect_original_team_stats_archive(
            team_stats_archive, target, cutoff, stats_validator)
    current_season = target.get('season')
    known_current = [
        date.fromisoformat(rec['date'])
        for rec in (season_index or {}).values()
        if current_season and rec.get('season') == current_season
    ]
    known_current.extend(date.fromisoformat(v['date'])
                         for v in original_records.values()
                         if current_season and v['season'] == current_season)
    latest_current = max(known_current) if known_current else None
    inferred_current = 0
    for archive in sorted(root.glob('daily/*/*/*.json.gz')):
        require(not archive.is_symlink(), 'UNSAFE_ARCHIVE', 'Symlinks forbidden')
        day = read_archive(archive)
        for mid, item in day['matches'].items():
            require(mid not in seen, 'DUPLICATE_ARCHIVE_ID', mid)
            seen.add(mid)
            if mid == target['pitchapi_event_id']:
                continue
            if not isinstance(item, dict) or item.get('status') != 'PASS_INTERNAL':
                exclusions['quality_or_structure'] += 1
                continue
            f = item.get('fixture') or {}
            lid = f.get('league_id') or (f.get('league') or {}).get('id')
            if lid != target['pitchapi_league_id']:
                exclusions['foreign_league'] += 1
                continue
            try:
                observed = instant(item.get('retrieved_at_utc'))
                event_time = instant(f.get('time_utc'))
                if observed > cutoff or event_time >= cutoff or observed < event_time:
                    exclusions['out_of_cutoff'] += 1
                    continue
                # GTS3 has a two-day historical availability lag.
                if (date.fromisoformat(f['date']) - date.fromisoformat(
                        target['kickoff'][:10])).days >= -2:
                    exclusions['too_recent'] += 1
                    continue
                require(f.get('status') == 'finished' and f.get('id') == mid,
                        'GTS3_FIXTURE_INVALID', 'Finished fixture identity invalid')
                verified = (season_index or {}).get(mid)
                if verified is not None:
                    require(verified['date'] == f.get('date')
                            and verified['home_team_id'] == f['home_team']['id']
                            and verified['away_team_id'] == f['away_team']['id']
                            and verified['home_goals'] == f.get('score_home')
                            and verified['away_goals'] == f.get('score_away'),
                            'GTS3_SEASON_IDENTITY_CONFLICT',
                            'Existing historical season has conflicting fixture identity')
                season = f.get('season')
                if season is None or season == '':
                    season = verified.get('season') if verified else None
                    if not season and latest_current is not None:
                        observed_day = date.fromisoformat(f['date'])
                        if (latest_current < observed_day <= cutoff.date()
                                and (observed_day-latest_current).days <= 120):
                            # Source league and original past-season anchor
                            # have already been verified. Never infer a new
                            # season or a different competition by calendar.
                            season = current_season
                            inferred_current += 1
                elif verified is not None:
                    require(season == verified['season'], 'GTS3_SEASON_IDENTITY_CONFLICT',
                            'Date-feed season contradicts original archived season')
                require(isinstance(season, str) and season.strip(),
                        'GTS3_SEASON_MISSING', 'No original matching provider season')
                ht = f['home_team']['id']
                at = f['away_team']['id']
                hg, ag = f['score_home'], f['score_away']
                require(ht != at and isinstance(hg, int) and not isinstance(hg, bool)
                        and isinstance(ag, int) and not isinstance(ag, bool)
                        and 0 <= hg <= 30 and 0 <= ag <= 30,
                        'GTS3_FIXTURE_INVALID', 'Invalid teams/final score')
                raw = item.get('stats_raw')
                require(isinstance(raw, dict), 'GTS3_STATS_MISSING',
                        'Original raw provider team stats not present')
                # Native frozen GTS3 parser rejects wrong teams, absent xG, shots
                # and contradictory duplicate expected_goals values.
                stats_validator(raw, mid, ht, at)
                matches[mid] = {
                    'match_id': mid, 'date': f['date'], 'season': season,
                    'home': {'id': ht, 'goals': hg},
                    'away': {'id': at, 'goals': ag},
                    'statistics': raw
                }
            except (PreflightBlocked, ValueError, KeyError, TypeError):
                exclusions['unusable_stats_or_metadata'] += 1
    if original_records:
        for mid, row in original_records.items():
            if mid in matches:
                older = matches[mid]
                require(older['date'] == row['date']
                        and older['season'] == row['season']
                        and older['home'] == row['home']
                        and older['away'] == row['away'],
                        'GTS3_DUPLICATE_SOURCE_CONFLICT',
                        'Daily vs original team stats identity mismatch: ' + mid)
                # Identical match identity: retain original validated team stats.
            matches[mid] = row
    h, a = target['home']['id'], target['away']['id']
    counts = {
        'home_total': sum(h in (x['home']['id'], x['away']['id']) for x in matches.values()),
        'away_total': sum(a in (x['home']['id'], x['away']['id']) for x in matches.values()),
        'home_at_home': sum(x['home']['id'] == h for x in matches.values()),
        'away_at_away': sum(x['away']['id'] == a for x in matches.values())
    }
    require(min(counts['home_total'], counts['away_total']) >= 10
            and min(counts['home_at_home'], counts['away_at_away']) >= 4,
            'GTS3_SOURCE_INSUFFICIENT',
            'Need original stats All-period 10 team/4 venue: '
            + json.dumps({'coverage': counts, 'excluded': dict(exclusions)}))
    return {'league': {'id': target['pitchapi_league_id'],
                       'name': target['competition']},
            'records': matches}, {'coverage': counts, 'excluded': dict(exclusions),
                                 'original_team_stats_archive': original_team_stats_qa,
                                 'current_season_rows_anchored_to_original': inferred_current}


def execute(preflight_dir, warehouse_root, gts3_original_root, output_dir, period,
            *, league_archive=None, team_stats_archive=None,
            launch=subprocess.run):
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    status = {'schema': 'BETPILOT_GTS3_ORIGINAL_BRIDGE_V1',
              'mode': 'RESEARCH_ONLY', 'betting_enabled': False,
              'ma3_native_export_created': False, 'ga3_probability_modified': False}
    try:
        original = verify_gts3_original(gts3_original_root)
        gate_dir = Path(preflight_dir)
        gate = json_file(gate_dir / 'PITCHAPI_PREFLIGHT_STATUS.json')
        require(gate.get('status') == 'PITCHAPI_ASOF_PREFLIGHT_PASSED_NATIVE_PENDING',
                'PREFLIGHT_NOT_PASSED', 'Source preflight must succeed')
        for filename in ('PITCHAPI_FIXTURE_CANDIDATE.json', 'PITCHAPI_ASOF_HISTORY.json'):
            require(sha(gate_dir / filename) == (gate.get('file_sha256') or {}).get(filename),
                    'PREFLIGHT_HASH_MISMATCH', 'Source integrity mismatch')
        target = json_file(gate_dir / 'PITCHAPI_FIXTURE_CANDIDATE.json')
        hist = json_file(gate_dir / 'PITCHAPI_ASOF_HISTORY.json')
        require(hist.get('target_event_id') == target.get('pitchapi_event_id'),
                'PREFLIGHT_IDENTITY_MISMATCH', 'Fixture and history mismatch')
        cutoff = instant(hist.get('cutoff'))
        require(cutoff < instant(target['kickoff']), 'GTS3_CUTOFF_INVALID',
                'Prematch cutoff must precede kickoff')
        params_path = original / 'reports' / 'selected_model_parameters.json'
        params = json_file(params_path)
        require(isinstance(period, str) and period in params
                and isinstance(params[period], dict),
                'GTS3_PERIOD_INVALID', 'Explicit original frozen period required')
        require(params[period]['training_max_date'] < target['kickoff'][:10],
                'GTS3_PERIOD_LEAKAGE', 'Model trained on target day or later')
        sys.path.insert(0, str(original / 'src'))
        from gts3.source import extract_stats, load_zip
        season_index = (verified_season_index(league_archive,target,cutoff)
                        if league_archive is not None else {})
        source, qa = collect_stats(warehouse_root, target, cutoff,
                                   stats_validator=extract_stats,
                                   season_index=season_index,
                                   team_stats_archive=team_stats_archive)
        material = out / 'GTS3_ORIGINAL_STATS_SOURCE_RESEARCH.zip'
        with zipfile.ZipFile(material, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('archive.json', json.dumps(source, ensure_ascii=False,
                                                  sort_keys=True, allow_nan=False))
        # Prove the *original* frozen parser accepts the materialized data.
        rows, parsed = load_zip(material)
        require(parsed['accepted'] == len(source['records']) and len(rows) == parsed['accepted'],
                'GTS3_NATIVE_SOURCE_INVALID', 'Original GTS3 archive parser rejected source')
        env = os.environ.copy()
        env['PYTHONPATH'] = os.pathsep.join([str(original / 'src'),
                                           env.get('PYTHONPATH', '')])
        cmd = [sys.executable, '-m', 'gts3.score', '--archive', str(material),
               '--model', str(params_path), '--period', period,
               '--date', target['kickoff'][:10], '--season', target['season'],
               '--home-id', target['home']['id'], '--away-id', target['away']['id']]
        result = launch(cmd, cwd=original, env=env, capture_output=True,
                        text=True, timeout=180, check=False)
        require(result.returncode == 0, 'GTS3_ORIGINAL_SCORE_FAILED',
                'Original GTS3 score rejected the verified inputs')
        try:
            score = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise PreflightBlocked('GTS3_SCORE_INVALID', 'Original CLI did not output JSON')
        require(score.get('status') == 'RESEARCH_ONLY_NO_BETTING'
                and score.get('fixture_date') == target['kickoff'][:10]
                and score.get('season') == target['season']
                and score.get('lambda_adjustment_allowed') is False
                and score.get('snapshot_is_true_prematch') is False
                and len(score.get('predictions') or []) == 2
                and {v['team_id'] for v in score['predictions']}
                == {target['home']['id'], target['away']['id']},
                'GTS3_SCORE_INVALID', 'Original GTS3 output identity/role mismatch')
        destination = out / 'GTS3_RC1_ORIGINAL_SCORE_RESEARCH.json'
        destination.write_text(json.dumps(score, ensure_ascii=False,
                                          indent=2, allow_nan=False) + '\n', 'utf8')
        status.update(status='GTS3_ORIGINAL_RESEARCH_EXECUTED',
                      original_gts3_cli_executed=True, selected_period=period,
                      stats_archive_sha256=sha(material),
                      score_sha256=sha(destination),
                      source_coverage=qa, original_parser=parsed,
                      exact_historical_season_matches=len(season_index))
    except (PreflightBlocked, ValueError, KeyError, TypeError, OSError,
            subprocess.SubprocessError, ImportError) as exc:
        status.update(status='NO_BET_GTS3_ORIGINAL_BLOCKED',
                      original_gts3_cli_executed=False,
                      reason_code=getattr(exc, 'code', 'GTS3_EXECUTION_FAILED'),
                      reason=str(exc)[:500])
    (out / 'GTS3_ORIGINAL_BRIDGE_STATUS.json').write_text(
        json.dumps(status, ensure_ascii=False, sort_keys=True,
                   indent=2, allow_nan=False) + '\n', encoding='utf8')
    return status


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--preflight-dir', type=Path, required=True)
    p.add_argument('--warehouse-root', type=Path, required=True)
    p.add_argument('--gts3-original-root', type=Path, required=True)
    p.add_argument('--period', required=True)
    p.add_argument('--league-archive', type=Path,
                   help='Original same-league historical archive to verify missing season by exact match ID')
    p.add_argument('--team-stats-archive', type=Path,
                   help='Existing original PitchAPI team stats archive, read-only')
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args(argv)
    result = execute(args.preflight_dir, args.warehouse_root, args.gts3_original_root,
                     args.output_dir, args.period, league_archive=args.league_archive,
                     team_stats_archive=args.team_stats_archive)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'GTS3_ORIGINAL_RESEARCH_EXECUTED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
