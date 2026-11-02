"""Original frozen MA3 contract verification of EXACT five generated V3 files.

This uses the original MA3 synthetic source fixtures already in the signed ZIP.
It NEVER claims a real provider execution or operational betting eligibility.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from betpilot_v3.pitchapi_native_phase1 import (
    assemble_native, digest, select_original_gts_period, PreflightBlocked, execute
)


def make_gate(tmp, sample):
    event=json.loads((sample/'match.json').read_text())
    ids=json.loads((sample/'pitchapi_identity.json').read_text())
    target={'provider':'PitchAPI',
            'pitchapi_event_id':'m_ABC123',
            'pitchapi_league_id': ids['league_id'],
            'competition':event['competition'],
            'country':'ENG',
            'season':ids['season'],
            'home':{'id':ids['home_team_id'],'name':event['home']},
            'away':{'id':ids['away_team_id'],'name':event['away']},
            'kickoff':event['kickoff'],
            'fixture_captured_at_utc':'2026-11-14T08:00:00+00:00'}
    hist={'target_event_id':'m_ABC123','cutoff':event['cutoff']}
    gate=tmp/'preflight';gate.mkdir()
    files={'PITCHAPI_FIXTURE_CANDIDATE.json':target,
           'PITCHAPI_ASOF_HISTORY.json':hist}
    digests={}
    for name,obj in files.items():
        text=json.dumps(obj).encode()
        (gate/name).write_bytes(text)
        digests[name]=hashlib.sha256(text).hexdigest()
    (gate/'PITCHAPI_PREFLIGHT_STATUS.json').write_text(json.dumps({
        'status':'PITCHAPI_ASOF_PREFLIGHT_PASSED_NATIVE_PENDING',
        'full_analysis_ready':False,'file_sha256':digests}))
    ga=tmp/'ga';ga.mkdir();gts=tmp/'gts';gts.mkdir()
    gp=ga/'GA3_RC2_ORIGINAL_PREDICTION_RESEARCH.json'
    tp=gts/'GTS3_RC1_ORIGINAL_SCORE_RESEARCH.json'
    shutil.copy2(sample/'ga3_native_output.json',gp)
    shutil.copy2(sample/'gts3_native_output.json',tp)
    (ga/'GA3_ORIGINAL_BRIDGE_STATUS.json').write_text(json.dumps({
        'status':'GA3_ORIGINAL_RESEARCH_EXECUTED',
        'original_ga3_cli_executed':True,
        'ma3_native_export_created':False,
        'original_prediction_sha256':digest(gp)}))
    (gts/'GTS3_ORIGINAL_BRIDGE_STATUS.json').write_text(json.dumps({
        'status':'GTS3_ORIGINAL_RESEARCH_EXECUTED',
        'original_gts3_cli_executed':True,
        'ma3_native_export_created':False,
        'ga3_probability_modified':False,
        'score_sha256':digest(tp)}))
    return gate,ga,gts


class MA3NativeFiveFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        w=os.environ.get('BETPILOT_UPSTREAM_WORKDIR')
        if not w:raise unittest.SkipTest('Signed frozen ZIPs only present in V3 CI')
        cls.ma=Path(w)/'BETPILOT_MARKET_ANALYZER_3_0_NATIVE_RC2'
        cls.gtsroot=Path(w)/'BETPILOT_GTS3_RC1'
        cls.sample=cls.ma/'examples'/'native_synthetic'
        sys.path.insert(0,str(cls.ma))
        from ma3.native_exports import export_ga3,export_gts3
        cls.ga_parser=staticmethod(export_ga3)
        cls.gts_parser=staticmethod(export_gts3)

    def test_exact_original_ga3_gts3_contracts_from_five_file_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            temp=Path(td)
            gate,ga,gts=make_gate(temp,self.sample)
            dest=temp/'native'/'fixture_m_ABC123'
            manifest=assemble_native(gate,ga,gts,self.ma,self.gtsroot,dest)
            self.assertEqual(manifest['status'],'ORIGINAL_MA3_PARSERS_ACCEPTED_RESEARCH_ONLY')
            self.assertEqual(len(manifest['five_file_sha256']),5)
            self.assertEqual(manifest['ma3_parser_ga3_markets'],47)
            self.assertFalse(manifest['operational_eligible'])
            for name,checksum in manifest['five_file_sha256'].items():
                self.assertEqual(checksum,digest(dest/name))
            m=json.loads((dest/'match.json').read_text())
            ids=json.loads((dest/'pitchapi_identity.json').read_text())
            a=self.ga_parser(dest/'ga3_native_output.json',m,ids)
            g=self.gts_parser(dest/'gts3_native_output.json',m,ids,
                              dest/'GTS3_FROZEN_MODEL_PARAMS.json')
            self.assertTrue(a['native_schema_parity_verified'])
            self.assertTrue(g['native_schema_parity_verified'])
            self.assertEqual(digest(dest/'GTS3_FROZEN_MODEL_PARAMS.json'),
                             digest(self.gtsroot/'reports'/'selected_model_parameters.json'))

    def test_mutated_original_ga3_rejected_and_no_native_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            temp=Path(td)
            gate,ga,gts=make_gate(temp,self.sample)
            gp=ga/'GA3_RC2_ORIGINAL_PREDICTION_RESEARCH.json'
            gp.write_bytes(gp.read_bytes()+b' ')
            destination=temp/'native'/'fixture_m_ABC123'
            with self.assertRaises(PreflightBlocked) as err:
                assemble_native(gate,ga,gts,self.ma,self.gtsroot,destination)
            self.assertEqual(err.exception.code,'ORIGINAL_OUTPUT_HASH_MISMATCH')
            self.assertFalse(destination.exists())

    def test_original_gts_period_strict_only_season_2026_27(self):
        model=self.gtsroot/'reports'/'selected_model_parameters.json'
        self.assertEqual(select_original_gts_period(
            model,'2026/2027','2026-11-15T17:00:00Z'),
            '2026_27_HISTORICALLY_OBSERVED')
        with self.assertRaises(PreflightBlocked):
            select_original_gts_period(model,'2027/2028','2027-11-15T17:00:00Z')
        with self.assertRaises(PreflightBlocked):
            select_original_gts_period(model,'2026/2027','2026-03-01T17:00:00Z')

    def test_duplicate_native_destination_refused(self):
        with tempfile.TemporaryDirectory() as td:
            temp=Path(td)
            gate,ga,gts=make_gate(temp,self.sample)
            dest=temp/'native';dest.mkdir()
            with self.assertRaises(PreflightBlocked) as err:
                assemble_native(gate,ga,gts,self.ma,self.gtsroot,dest)
            self.assertEqual(err.exception.code,'NATIVE_DESTINATION_EXISTS')


class NoProviderNetworkFailClosed(unittest.TestCase):
    def test_without_pitchapi_key_no_external_calls_and_no_partial_native(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'out'
            args=SimpleNamespace(output_dir=out,request='FULL ANALYSIS: Inter - Juventus',
                day_utc='2026-11-03',country_code='ITA',competition='Serie A',
                warehouse_root=Path(td)/'world',history_root=Path(td)/'history')
            with patch.dict(os.environ, {'PITCHAPI_API_KEY': ''}):
                result=execute(args)
            self.assertEqual(result['reason_code'],'PITCHAPI_KEY_UNAVAILABLE')
            self.assertEqual(result['status'],'NO_BET_NATIVE_INPUTS_BLOCKED')
            self.assertFalse(result['full_analysis_completed'])
            self.assertFalse((out/'native').exists())

if __name__=='__main__':
    unittest.main()
