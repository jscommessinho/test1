"""Offline preflight tests: no secrets, no paid requests, no frozen models."""
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys

# V3's existing replay test runner sets PYTHONPATH to betpilot_v3/, not repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from betpilot_v3.pitchapi_preflight import (PreflightBlocked, discover, history, publish)

UTC = timezone.utc
H = {'id': 't_ABC123', 'name': 'Inter'}
A = {'id': 't_DEF456', 'name': 'Juventus'}
LEAGUE = {'id': 'l_ABC123', 'name': 'Serie A', 'country_code': 'ITA', 'seasons': ['2026/2027']}
FIXTURE = {'id': 'm_ZZZ999', 'home_team': H, 'away_team': A,
           'league_id': LEAGUE['id'], 'time_utc': '2026-11-03T19:45:00Z',
           'status': 'not_started'}
NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)


class FakeClient:
    def __init__(self, *, fixtures=None, season='2026/2027'):
        self.calls = []
        self.fixtures = [FIXTURE] if fixtures is None else fixtures
        self.season = season

    def get(self, path, params=None):
        self.calls.append(path)
        if path == '/v1/date/2026-11-03':
            return {'matches': self.fixtures}
        if path == '/v1/matches/m_ZZZ999':
            return {**FIXTURE, 'league': {'id': LEAGUE['id']}, 'season': self.season}
        if path == '/v1/leagues':
            return {'leagues': [LEAGUE]}
        raise AssertionError('Unexpected provider request ' + path)


def seed(root, *, bad_capture=False, bad_quality=False):
    for i in range(10):
        day = f'2026-10-{20+i:02d}'
        mid = f'm_{i:06d}'
        row = {**FIXTURE, 'id': mid, 'status': 'finished', 'date': day,
               'time_utc': day + 'T15:00:00+00:00',
               'score_home': 2, 'score_away': 1}
        item = {'fixture': row, 'retrieved_at_utc':
                '2026-11-04T00:00:00Z' if bad_capture and i == 0 else day + 'T19:00:00Z',
                'status': 'STRUCTURAL_ANOMALY' if bad_quality and i == 0 else 'PASS_INTERNAL',
                'home_shots': {'xg': 1.5, 'shots': 10},
                'away_shots': {'xg': 0.8, 'shots': 8}}
        p = Path(root) / 'daily' / '2026' / '10' / (day + '.json.gz')
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, 'wt', encoding='utf8') as f:
            json.dump({'schema': 'BETPILOT_PITCHAPI_WORLD_DAILY_v1',
                       'date_utc': day, 'matches': {mid: item}}, f)


class PreflightTests(unittest.TestCase):
    def test_unique_future_fixture_and_bounded_api(self):
        client = FakeClient()
        obj = discover(client, 'FULL ANALYSIS: Inter - Juventus', '2026-11-03',
                       'ITA', captured_at=NOW)
        self.assertEqual(obj['pitchapi_event_id'], 'm_ZZZ999')
        self.assertEqual(obj['season'], '2026/2027')
        self.assertEqual(len(client.calls), 3)

    def test_duplicate_refused_before_more_requests(self):
        client = FakeClient(fixtures=[FIXTURE, FIXTURE])
        with self.assertRaisesRegex(PreflightBlocked, 'exactly one'):
            discover(client, 'FULL ANALYSIS: Inter - Juventus', '2026-11-03',
                     'ITA', captured_at=NOW)
        self.assertEqual(len(client.calls), 1)

    def test_unknown_season_blocks(self):
        with self.assertRaises(PreflightBlocked) as e:
            discover(FakeClient(season=None), 'FULL ANALYSIS: Inter - Juventus',
                     '2026-11-03', 'ITA', captured_at=NOW)
        self.assertEqual(e.exception.code, 'SEASON_UNVERIFIED')

    def test_historical_capture_is_asof(self):
        with tempfile.TemporaryDirectory() as d:
            seed(d)
            target = discover(FakeClient(), 'FULL ANALYSIS: Inter - Juventus',
                              '2026-11-03', 'ITA', captured_at=NOW)
            evidence = history(d, target, NOW)
            self.assertEqual(evidence['coverage']['home_all'], 10)
            self.assertEqual(evidence['coverage']['away_away'], 10)
            self.assertFalse(evidence['publication_timestamps_independently_verified'])

    def test_late_retrieval_refused_not_backfilled(self):
        with tempfile.TemporaryDirectory() as d:
            seed(d, bad_capture=True)
            target = discover(FakeClient(), 'FULL ANALYSIS: Inter - Juventus',
                              '2026-11-03', 'ITA', captured_at=NOW)
            with self.assertRaises(PreflightBlocked) as e:
                history(d, target, NOW)
            self.assertEqual(e.exception.code, 'HISTORY_INSUFFICIENT')

    def test_bad_qa_not_used(self):
        with tempfile.TemporaryDirectory() as d:
            seed(d, bad_quality=True)
            target = discover(FakeClient(), 'FULL ANALYSIS: Inter - Juventus',
                              '2026-11-03', 'ITA', captured_at=NOW)
            with self.assertRaises(PreflightBlocked) as e:
                history(d, target, NOW)
            self.assertEqual(e.exception.code, 'HISTORY_INSUFFICIENT')

    def test_research_only_publication_not_native(self):
        with tempfile.TemporaryDirectory() as d:
            seed(d)
            result = publish(FakeClient(), 'FULL ANALYSIS: Inter - Juventus',
                             '2026-11-03', 'ITA', d, Path(d)/'output', captured_at=NOW)
            self.assertEqual(result['status'], 'PITCHAPI_ASOF_PREFLIGHT_PASSED_NATIVE_PENDING')
            self.assertFalse(result['full_analysis_ready'])
            self.assertFalse(result['ga3_native_executed'])
            self.assertFalse((Path(d)/'output'/'ga3_native_output.json').exists())
            self.assertTrue((Path(d)/'output'/'PITCHAPI_ASOF_HISTORY.json').exists())

    def test_expired_cutoff_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            seed(d)
            result = publish(FakeClient(), 'FULL ANALYSIS: Inter - Juventus',
                             '2026-11-03', 'ITA', d, Path(d)/'output',
                             captured_at=NOW, cutoff='2026-11-01T12:00:00Z')
            self.assertEqual(result['reason_code'], 'INVALID_CUTOFF')
            self.assertEqual(result['status'], 'NO_BET_SOURCE_PREFLIGHT_BLOCKED')




    def test_existing_per_league_historical_artifact(self):
        from betpilot.pitchapi_history_archive import sha256
        with tempfile.TemporaryDirectory() as d:
            records = {}
            for i in range(10):
                day = f'2026-10-{20+i:02d}'
                mid = f'm_{i:06d}'
                rec = {'match_id': mid, 'league_id': LEAGUE['id'],
                       'season': '2026/2027', 'date': day,
                       'home': {'id': H['id'], 'name': H['name'],
                                'goals': 2, 'xg': 1.5, 'shots': 10},
                       'away': {'id': A['id'], 'name': A['name'],
                                'goals': 1, 'xg': 0.8, 'shots': 8},
                       'qa_internal': 'PASS',
                       'qa_external': 'OFFICIAL_REFERENCE_NOT_VERIFIED',
                       'source': 'PitchAPI'}
                rec['source_digest'] = sha256(rec)
                rec['retrieved_at_utc'] = day + 'T19:00:00+00:00'
                records[mid] = rec
            archive = Path(d) / 'league.json'
            archive.write_text(json.dumps({
                'schema': 'BETPILOT-PITCHAPI-HISTORY-ARCHIVE-RESEARCH-v1',
                'provider': 'PitchAPI',
                'league': {'id': LEAGUE['id'], 'name': LEAGUE['name'],
                           'country': LEAGUE['country_code']},
                'as_of_utc_date': '2026-11-02',
                'records': records, 'warnings': {}, 'rejected': {}
            }))
            target = discover(FakeClient(), 'FULL ANALYSIS: Inter - Juventus',
                              '2026-11-03', 'ITA', captured_at=NOW)
            result = history(d, target, NOW, league_archives=(archive,))
            self.assertEqual(result['coverage']['home_all'], 10)
            self.assertEqual(result['matches'][0]['source_kind'], 'PITCHAPI_PINNED_LEAGUE_HISTORY')
            records['m_000000']['retrieved_at_utc'] = '2026-11-04T12:00:00Z'
            archive.write_text(json.dumps({
                'schema': 'BETPILOT-PITCHAPI-HISTORY-ARCHIVE-RESEARCH-v1',
                'provider': 'PitchAPI',
                'league': {'id': LEAGUE['id'], 'name': LEAGUE['name'],
                           'country': LEAGUE['country_code']},
                'as_of_utc_date': '2026-11-02',
                'records': records, 'warnings': {}, 'rejected': {}
            }))
            with self.assertRaises(PreflightBlocked) as err:
                history(d, target, NOW, league_archives=(archive,))
            self.assertEqual(err.exception.code, 'HISTORY_INSUFFICIENT')


class FrozenInterfaceDiscovery(unittest.TestCase):
    """Check source entrypoints in existing SHA-verified V3 CI environment."""
    def test_discover_original_entrypoints(self):
        import os
        root = os.environ.get('BETPILOT_UPSTREAM_WORKDIR')
        if not root:
            self.skipTest('Frozen ZIPs are extracted only by existing V3 native CI')
        expected = {
            'GA3_PITCHAPI_ONLY_RC2_2026-11-01':
                ('MANIFEST_SHA256.json', 'docs/INTEGRATION_GUIDE.md',
                 'goal_analyzer3/cli.py', 'goal_analyzer3/pitchapi.py'),
            'BETPILOT_GTS3_RC1':
                ('MANIFEST.json', 'docs/INTEGRATION_GUIDE.md',
                 'src/gts3/score.py', 'src/gts3/source.py',
                 'reports/selected_model_parameters.json')
        }
        for folder, files in expected.items():
            original = Path(root) / folder
            for name in files:
                self.assertTrue((original / name).is_file(), folder + '/' + name)


if __name__ == '__main__':
    unittest.main()
