"""Offline regression for original GA3 research CLI adapter (no provider calls)."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from betpilot.pitchapi_history_archive import sha256
from betpilot_v3.ga3_original_adapter import (
    PreflightBlocked, execute, validate_archive, verify_ga3_original)
from betpilot_v3.pitchapi_preflight import SCHEMA

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]


def setup(d):
    directory = Path(d)
    src = directory / 'preflight'
    src.mkdir()
    target = {
        'pitchapi_event_id': 'm_ZZZ999',
        'pitchapi_league_id': 'l_ABC123',
        'competition': 'Serie A', 'country': 'ITA',
        'home': {'id': 't_ABC123'}, 'away': {'id': 't_DEF456'},
        'kickoff': '2026-11-03T18:45:00Z'
    }
    asof = '2026-11-02T12:00:00Z'
    history = {'target_event_id': 'm_ZZZ999', 'cutoff': asof}
    names = {'PITCHAPI_FIXTURE_CANDIDATE.json': target,
             'PITCHAPI_ASOF_HISTORY.json': history}
    digests = {}
    import hashlib
    for filename, payload in names.items():
        raw = json.dumps(payload).encode()
        (src / filename).write_bytes(raw)
        digests[filename] = hashlib.sha256(raw).hexdigest()
    (src / 'PITCHAPI_PREFLIGHT_STATUS.json').write_text(json.dumps({
        'schema': SCHEMA, 'status': 'PITCHAPI_ASOF_PREFLIGHT_PASSED_NATIVE_PENDING',
        'full_analysis_ready': False, 'file_sha256': digests}))
    records = {}
    for i in range(10):
        day = f'2026-10-{15+i:02d}'
        rec = {'match_id': f'm_{i:06d}', 'league_id': target['pitchapi_league_id'],
               'season': '2026/2027', 'date': day,
               'home': {'id': 't_ABC123', 'name': 'Inter',
                        'goals': 2, 'xg': 1.25, 'shots': 9},
               'away': {'id': 't_DEF456', 'name': 'Juventus',
                        'goals': 1, 'xg': 0.7, 'shots': 8},
               'qa_internal': 'PASS', 'qa_external': 'OFFICIAL_REFERENCE_NOT_VERIFIED',
               'source': 'PitchAPI'}
        rec['source_digest'] = sha256(rec)
        rec['retrieved_at_utc'] = day + 'T20:00:00Z'
        records[rec['match_id']] = rec
    arch = directory / 'historic.json'
    arch.write_text(json.dumps({
        'schema': 'BETPILOT-PITCHAPI-HISTORY-ARCHIVE-RESEARCH-v1',
        'provider': 'PitchAPI',
        'league': {'id': 'l_ABC123', 'name': 'Serie A', 'country': 'ITA'},
        'as_of_utc_date': '2026-11-02',
        'records': records, 'warnings': {}, 'rejected': {}}))
    return src, arch, target, records


class NativeGA3AdapterTests(unittest.TestCase):
    def test_valid_archived_source_and_capture(self):
        with tempfile.TemporaryDirectory() as td:
            _, archive, target, _ = setup(td)
            evidence = validate_archive(archive, target,
                                        datetime(2026, 11, 2, 12, tzinfo=UTC))
            self.assertEqual(evidence['accepted_source_records'], 10)

    def test_after_cutoff_source_capture_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            _, archive, target, records = setup(td)
            records['m_000000']['retrieved_at_utc'] = '2026-11-04T12:00:00Z'
            obj = json.loads(archive.read_text())
            obj['records'] = records
            archive.write_text(json.dumps(obj))
            with self.assertRaises(PreflightBlocked) as err:
                validate_archive(archive, target,
                                 datetime(2026, 11, 2, 12, tzinfo=UTC))
            self.assertEqual(err.exception.code, 'GA3_HISTORY_AFTER_CUTOFF')

    def test_preflight_tamper_refused_before_native_command(self):
        with tempfile.TemporaryDirectory() as td:
            src, archive, _, _ = setup(td)
            (src / 'PITCHAPI_ASOF_HISTORY.json').write_text('{"invalid":true}')
            status = execute(src, archive, td, Path(td)/'out')
            self.assertEqual(status['reason_code'], 'PREFLIGHT_HASH_MISMATCH')
            self.assertFalse(status['original_ga3_cli_executed'])

    def test_original_sha_guard_and_invocation(self):
        frozen = os.environ.get('BETPILOT_UPSTREAM_WORKDIR')
        if not frozen:
            self.skipTest('Original SHA verified in existing V3 frozen CI')
        original = Path(frozen) / 'GA3_PITCHAPI_ONLY_RC2_2026-11-01'
        self.assertEqual(verify_ga3_original(original), original.resolve())
        with tempfile.TemporaryDirectory() as td:
            src, archive, target, _ = setup(td)
            def fake_original_cli(argv, **kwargs):
                self.assertIn('goal_analyzer3.cli', argv)
                self.assertIn('predict', argv)
                self.assertIn(target['pitchapi_league_id'], argv)
                self.assertIn(target['home']['id'], argv)
                self.assertIn(target['away']['id'], argv)
                self.assertNotIn('--allow-experimental-pitchapi-xg', argv)
                file = Path(argv[argv.index('--output')+1])
                file.write_text(json.dumps({
                    'status': 'RESEARCH_ONLY_NOT_VALIDATED_FOR_PRODUCTION',
                    'provider': 'PitchAPI', 'as_of': '2026-11-02T12:00:00+00:00',
                    'teams': [target['home']['id'], target['away']['id']],
                    'point_in_time_original_publication_verified': False,
                    'prediction': {'research': 1}}))
                return SimpleNamespace(returncode=0)
            out = execute(src, archive, original, Path(td)/'out',
                          launch=fake_original_cli)
            self.assertEqual(out['status'], 'GA3_ORIGINAL_RESEARCH_EXECUTED')
            self.assertFalse(out['ma3_native_export_created'])


if __name__ == '__main__':
    unittest.main()
