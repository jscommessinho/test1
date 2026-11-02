"""Invoke original immutable GA3 RC2 CLI on a verified pre-cutoff PitchAPI archive.

This produces the ORIGINAL GA3 research prediction, not an MA3 native export.
No GA3 fitting or Poisson/probability implementation is copied here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from betpilot.pitchapi_history_archive import load_archive
from betpilot_v3.pitchapi_preflight import PreflightBlocked, instant, require


def _json(path):
    try:
        return json.loads(Path(path).read_text('utf8'))
    except (ValueError, OSError, UnicodeError) as exc:
        raise PreflightBlocked('SOURCE_INVALID', Path(path).name) from exc


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_ga3_original(root):
    root = Path(root).resolve()
    manifest = _json(root / 'MANIFEST_SHA256.json')
    require(isinstance(manifest.get('files'), dict) and
            len(manifest['files']) >= 5, 'GA3_MANIFEST_INVALID', 'Original release manifest missing')
    for name, digest in manifest['files'].items():
        require(isinstance(name, str) and isinstance(digest, str) and
                len(digest) == 64 and (root / name).is_file() and
                (root / name).resolve().is_relative_to(root) and
                _digest(root / name) == digest,
                'GA3_SOURCE_CHANGED', 'Frozen GA3 release integrity failure: ' + str(name))
    require((root / 'goal_analyzer3' / 'cli.py').is_file(),
            'GA3_CLI_MISSING', 'Native GA3 CLI missing')
    return root


def validate_archive(archive_path, target, as_of):
    path = Path(archive_path)
    require(path.is_file() and not path.is_symlink(), 'GA3_ARCHIVE_MISSING',
            'Existing history archive must be supplied; no new download')
    raw = _json(path)
    league = raw.get('league')
    require(isinstance(league, dict) and league.get('id') == target['pitchapi_league_id']
            and league.get('name') == target['competition']
            and league.get('country') == target['country'],
            'GA3_ARCHIVE_IDENTITY', 'Pinned archive does not match future fixture')
    require(raw.get('provider') == 'PitchAPI',
            'GA3_PROVIDER_INVALID', 'Only PitchAPI history allowed')
    require(date.fromisoformat(raw['as_of_utc_date']) <= as_of.date(),
            'GA3_ARCHIVE_AFTER_CUTOFF', 'Historical archive as-of exceeds cutoff')
    try:
        validated = load_archive(path, league, raw['as_of_utc_date'])
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise PreflightBlocked('GA3_ARCHIVE_INVALID', path.name + ': ' + str(exc)[:180]) from exc
    accepted = validated['records']
    require(len(accepted) > 0, 'GA3_HISTORY_EMPTY', 'No internally PASS archived source records')
    teams = {target['home']['id'], target['away']['id']}
    covered = set()
    for rec in accepted.values():
        retrieved = instant(rec.get('retrieved_at_utc'))
        require(retrieved <= as_of, 'GA3_HISTORY_AFTER_CUTOFF',
                'Historical observations acquired after target cutoff')
        require(date.fromisoformat(rec['date']) + timedelta(days=3) <= as_of.date(),
                'GA3_HISTORY_NOT_MATURE', 'GA3 original three-day source lag not respected')
        covered.update(teams.intersection({rec['home']['id'], rec['away']['id']}))
    require(teams == covered, 'GA3_TEAM_HISTORY_MISSING',
            'One or both teams have no original GA3 data')
    return {'file_sha256': _digest(path), 'accepted_source_records': len(accepted)}


def execute(preflight_dir, archive_path, original_root, output_dir, *, launch=subprocess.run):
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    status = {'schema': 'BETPILOT_GA3_ORIGINAL_BRIDGE_V1',
              'mode': 'RESEARCH_ONLY', 'betting_enabled': False,
              'ma3_native_export_created': False}
    try:
        root = Path(preflight_dir)
        gate = _json(root / 'PITCHAPI_PREFLIGHT_STATUS.json')
        require(gate.get('status') == 'PITCHAPI_ASOF_PREFLIGHT_PASSED_NATIVE_PENDING'
                and gate.get('full_analysis_ready') is False,
                'PREFLIGHT_NOT_PASSED', 'Source preflight required')
        target = _json(root / 'PITCHAPI_FIXTURE_CANDIDATE.json')
        history = _json(root / 'PITCHAPI_ASOF_HISTORY.json')
        for name in ('PITCHAPI_FIXTURE_CANDIDATE.json', 'PITCHAPI_ASOF_HISTORY.json'):
            require(_digest(root / name) == (gate.get('file_sha256') or {}).get(name),
                    'PREFLIGHT_HASH_MISMATCH', 'Preflight source modified: ' + name)
        require(history.get('target_event_id') == target.get('pitchapi_event_id'),
                'PREFLIGHT_IDENTITY_MISMATCH', 'History/fixture identity differs')
        as_of = instant(history.get('cutoff'))
        require(as_of < instant(target.get('kickoff')),
                'GA3_CUTOFF_INVALID', 'Prematch cutoff after kickoff')
        evidence = validate_archive(archive_path, target, as_of)
        frozen_root = verify_ga3_original(original_root)
        destination = out / 'GA3_RC2_ORIGINAL_PREDICTION_RESEARCH.json'
        env = os.environ.copy()
        env['PYTHONPATH'] = os.pathsep.join(
            [str(frozen_root), env.get('PYTHONPATH', '')])
        cmd = [sys.executable, '-m', 'goal_analyzer3.cli', 'predict',
               str(Path(archive_path).resolve()),
               '--league-id', target['pitchapi_league_id'],
               '--home', target['home']['id'], '--away', target['away']['id'],
               '--as-of', as_of.isoformat(), '--output', str(destination)]
        result = launch(cmd, cwd=frozen_root, env=env, check=False,
                        capture_output=True, text=True, timeout=180)
        require(result.returncode == 0 and destination.is_file(),
                'GA3_ORIGINAL_CLI_FAILED', 'Original GA3 rejected the verified inputs')
        prediction = _json(destination)
        require(prediction.get('status') == 'RESEARCH_ONLY_NOT_VALIDATED_FOR_PRODUCTION'
                and prediction.get('provider') == 'PitchAPI'
                and prediction.get('teams') == [target['home']['id'], target['away']['id']]
                and instant(prediction.get('as_of')) == as_of
                and prediction.get('point_in_time_original_publication_verified') is False
                and isinstance(prediction.get('prediction'), dict),
                'GA3_ORIGINAL_OUTPUT_INVALID', 'Original GA3 output identity/status mismatch')
        status.update(status='GA3_ORIGINAL_RESEARCH_EXECUTED',
                      original_ga3_cli_executed=True,
                      original_prediction_sha256=_digest(destination),
                      source_archive=evidence,
                      event_id=target['pitchapi_event_id'])
    except (PreflightBlocked, ValueError, KeyError, TypeError, OSError,
            subprocess.SubprocessError) as exc:
        status.update(status='NO_BET_GA3_ORIGINAL_BLOCKED',
                      original_ga3_cli_executed=False,
                      reason_code=getattr(exc, 'code', 'GA3_EXECUTION_FAILED'),
                      reason=str(exc)[:250])
    (out / 'GA3_ORIGINAL_BRIDGE_STATUS.json').write_text(
        json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n',
        encoding='utf8')
    return status


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--preflight-dir', type=Path, required=True)
    ap.add_argument('--archive', type=Path, required=True)
    ap.add_argument('--ga3-original-root', type=Path, required=True)
    ap.add_argument('--output-dir', type=Path, required=True)
    args = ap.parse_args(argv)
    result = execute(args.preflight_dir, args.archive,
                     args.ga3_original_root, args.output_dir)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'GA3_ORIGINAL_RESEARCH_EXECUTED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
