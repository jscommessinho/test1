"""GTS3 original All-period stats source adapter safety regression tests."""
import gzip
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from betpilot_v3.gts3_original_adapter import (
    collect_stats, execute, json_file, verify_gts3_original, verified_season_index
)
from betpilot_v3.pitchapi_preflight import PreflightBlocked, SCHEMA


TARGET = {
    'pitchapi_event_id': 'm_ZZZ999', 'pitchapi_league_id': 'l_ABC123',
    'competition': 'Serie A', 'country': 'ITA', 'season': '2026/2027',
    'home': {'id': 't_ABC123'}, 'away': {'id': 't_DEF456'},
    'kickoff': '2026-11-03T18:45:00Z'
}
CUTOFF = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def raw(mid):
    return {'match_id': mid,
            'home_team': {'id': TARGET['home']['id']},
            'away_team': {'id': TARGET['away']['id']},
            'periods': [{'period': 'All',
                         'groups': [{'items': [
                             {'key': 'expected_goals', 'home': '1.5', 'away': '0.8'},
                             {'key': 'total_shots', 'home': '10', 'away': '8'}]}]}]}


def seed(root, *, missing_stats=False, after_cutoff=False):
    for i in range(10):
        day = f'2026-10-{15+i:02d}'
        mid = f'm_{i:06d}'
        fixture = {'id': mid, 'league_id': TARGET['pitchapi_league_id'],
                   'status': 'finished', 'season': '2026/2027',
                   'date': day, 'time_utc': day + 'T15:00:00+00:00',
                   'home_team': {'id': TARGET['home']['id']},
                   'away_team': {'id': TARGET['away']['id']},
                   'score_home': 2, 'score_away': 1}
        item = {'fixture': fixture, 'status': 'PASS_INTERNAL',
                'retrieved_at_utc': '2026-11-04T19:00:00Z' if after_cutoff and i == 0
                                    else day + 'T20:00:00Z',
                'stats_raw': None if missing_stats and i == 0 else raw(mid)}
        path = Path(root)/'daily'/'2026'/'10'/(day+'.json.gz')
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, 'wt', encoding='utf8') as gz:
            json.dump({'schema': 'BETPILOT_PITCHAPI_WORLD_DAILY_v1',
                       'date_utc': day, 'matches': {mid: item}}, gz)


def preflight(root):
    p = Path(root)/'gate'
    p.mkdir()
    files = {'PITCHAPI_FIXTURE_CANDIDATE.json': TARGET,
             'PITCHAPI_ASOF_HISTORY.json': {
                 'target_event_id': TARGET['pitchapi_event_id'],
                 'cutoff': CUTOFF.isoformat()}}
    import hashlib
    hashes = {}
    for name, doc in files.items():
        data = json.dumps(doc).encode()
        (p/name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    (p/'PITCHAPI_PREFLIGHT_STATUS.json').write_text(json.dumps({
        'schema': SCHEMA, 'status': 'PITCHAPI_ASOF_PREFLIGHT_PASSED_NATIVE_PENDING',
        'file_sha256': hashes, 'full_analysis_ready': False}))
    return p


class FrozenGTS3AdapterTest(unittest.TestCase):
    def test_exact_raw_team_stat_processing(self):
        with tempfile.TemporaryDirectory() as td:
            seed(td)
            def strict_original(payload, mid, home, away):
                self.assertEqual(payload['match_id'], mid)
                self.assertEqual(payload['home_team']['id'], home)
                self.assertEqual(payload['away_team']['id'], away)
                self.assertEqual(payload['periods'][0]['period'], 'All')
                return {'expected_goals': 1.5}, {'expected_goals': .8}, 0
            result, qa = collect_stats(td, TARGET, CUTOFF, stats_validator=strict_original)
            self.assertEqual(len(result['records']), 10)
            self.assertEqual(qa['coverage']['home_at_home'], 10)
            self.assertEqual(qa['coverage']['away_at_away'], 10)
            self.assertIn('statistics', result['records']['m_000000'])
            self.assertNotIn('shots_raw', result['records']['m_000000'])

    def test_late_source_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            seed(td, after_cutoff=True)
            with self.assertRaises(PreflightBlocked) as err:
                collect_stats(td, TARGET, CUTOFF, stats_validator=lambda *a: None)
            self.assertEqual(err.exception.code, 'GTS3_SOURCE_INSUFFICIENT')

    def test_missing_all_stats_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            seed(td, missing_stats=True)
            with self.assertRaises(PreflightBlocked) as err:
                collect_stats(td, TARGET, CUTOFF, stats_validator=lambda *a: None)
            self.assertEqual(err.exception.code, 'GTS3_SOURCE_INSUFFICIENT')

    def test_original_historical_match_can_supply_missing_season_only_on_exact_identity(self):
        from betpilot.pitchapi_history_archive import sha256
        with tempfile.TemporaryDirectory() as td:
            seed(td)
            recs={}
            for i in range(10):
                mid=f'm_{i:06d}'
                day=f'2026-10-{15+i:02d}'
                item={'match_id':mid,'league_id':TARGET['pitchapi_league_id'],
                      'season':'2026/2027','date':day,
                      'home':{'id':TARGET['home']['id'],'name':'Home','goals':2,'xg':1.5,'shots':10},
                      'away':{'id':TARGET['away']['id'],'name':'Away','goals':1,'xg':.8,'shots':8},
                      'qa_internal':'PASS','qa_external':'OFFICIAL_REFERENCE_NOT_VERIFIED',
                      'source':'PitchAPI'}
                item['source_digest']=sha256(item)
                item['retrieved_at_utc']=day+'T20:00:00Z'
                recs[mid]=item
                filename=Path(td)/'daily'/'2026'/'10'/(day+'.json.gz')
                with gzip.open(filename,'rt',encoding='utf8') as z: doc=json.load(z)
                doc['matches'][mid]['fixture'].pop('season')
                with gzip.open(filename,'wt',encoding='utf8') as z:json.dump(doc,z)
            hist=Path(td)/'historic.json'
            hist.write_text(json.dumps({
                'schema':'BETPILOT-PITCHAPI-HISTORY-ARCHIVE-RESEARCH-v1',
                'provider':'PitchAPI',
                'league':{'id':TARGET['pitchapi_league_id'],
                          'name':TARGET['competition'],'country':TARGET['country']},
                'as_of_utc_date':'2026-11-02',
                'records':recs,'warnings':{},'rejected':{}}))
            index=verified_season_index(hist,TARGET,CUTOFF)
            self.assertEqual(len(index),10)
            source,qa=collect_stats(td,TARGET,CUTOFF,stats_validator=lambda *a:None,
                                    season_index=index)
            self.assertEqual(len(source['records']),10)
            self.assertTrue(all(x['season']=='2026/2027'
                                for x in source['records'].values()))
            # A wrong result in the original season mapping is not a valid join.
            index['m_000000']['home_goals']=3
            with self.assertRaises(PreflightBlocked):
                collect_stats(td,TARGET,CUTOFF,stats_validator=lambda *a:None,
                              season_index=index)

    def test_native_frozen_source_and_subprocess_interface(self):
        work = os.environ.get('BETPILOT_UPSTREAM_WORKDIR')
        if not work:
            self.skipTest('Frozen original source only in V3 CI')
        original = verify_gts3_original(
            Path(work) / 'BETPILOT_GTS3_RC1')
        params = json_file(original/'reports'/'selected_model_parameters.json')
        periods = [key for key, value in params.items()
                   if isinstance(value, dict) and
                   str(value.get('training_max_date', '9999-99-99')) < '2026-11-03']
        self.assertTrue(periods)
        with tempfile.TemporaryDirectory() as td:
            seed(td)
            gate = preflight(td)
            def original_cli(argv, **kwargs):
                self.assertIn('gts3.score', argv)
                self.assertIn('--model', argv)
                self.assertIn('--archive', argv)
                self.assertNotIn('GA2', argv)
                return SimpleNamespace(returncode=0, stdout=json.dumps({
                    'status': 'RESEARCH_ONLY_NO_BETTING',
                    'fixture_date': '2026-11-03',
                    'season': TARGET['season'], 'lambda_adjustment_allowed': False,
                    'snapshot_is_true_prematch': False,
                    'predictions': [
                        {'team_id': TARGET['home']['id']},
                        {'team_id': TARGET['away']['id']}]
                }))
            output = execute(gate, td, original, Path(td)/'out', periods[0],
                             launch=original_cli)
            self.assertEqual(output['status'], 'GTS3_ORIGINAL_RESEARCH_EXECUTED', output)
            self.assertFalse(output['ma3_native_export_created'])
            self.assertFalse(output['ga3_probability_modified'])


if __name__ == '__main__':
    unittest.main()
