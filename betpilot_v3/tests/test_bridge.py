"""Integration regression coverage using original, immutable source trees."""
from __future__ import annotations
import copy, hashlib, json, os, shutil, sys, tempfile, unittest
from pathlib import Path

HOME=Path(__file__).resolve().parents[1]
WORK=Path(os.environ.get('BETPILOT_UPSTREAM_WORKDIR', str(HOME.parent/'betpilot_integration_work')))
MA=WORK/'BETPILOT_MARKET_ANALYZER_3_0_NATIVE_RC2'
BA=WORK/'BETPILOT_BETANALYZER_3_0_RC1'
NATIVE=MA/'examples'/'native_synthetic'
OUTPUT=NATIVE/'full_native_replay'
BRIDGE=NATIVE/'rc15_native_verdict_c_bridge.json'
sys.path.insert(0,str(HOME))
sys.path.insert(0,str(MA))
sys.path.insert(0,str(BA))
from bridge.native_handoff import (IntegrationError,audit_ma3,audit_native_sources,
   check_sha_manifest_ba3,check_sha_manifest_ma3,convert,run,sha,ensure)
from betanalyzer3.contracts import validate, ContractError
from betanalyzer3.decision import analyze, ev
from ma3.core import market_from_matrix, load_matrix


def fixtures():
    norm=json.loads((OUTPUT/'MA3_INPUT_NORMALIZED.json').read_text())
    ma=json.loads((OUTPUT/'MA3_ANALYSIS.json').read_text())
    ho=json.loads((OUTPUT/'BETANALYZER_HANDOFF.json').read_text())
    return norm,ma,ho


class TestHandoff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.norm,cls.ma,cls.ho=fixtures()

    def test_ba3_source_manifest(self):
        self.assertEqual(check_sha_manifest_ba3(BA)['files'],17)
    def test_ma3_source_manifest(self):
        self.assertGreaterEqual(check_sha_manifest_ma3(MA)['files'],50)
    def test_native_files_parity(self):
        self.assertTrue(audit_native_sources(MA,NATIVE,self.norm['match'],self.norm)['native_schema_parity'])
    def test_native_ma3_output_audit(self):
        self.assertEqual(audit_ma3(OUTPUT,NATIVE,BRIDGE,MA)[3]['markets'],47)
    def test_47_markets_converted_without_probabilistic_fusion(self):
        result=convert(self.norm,self.ma,self.ho)
        self.assertEqual(len(result['markets']),47)
        self.assertEqual(len([x for x in result['markets'] if x['quote']]),7)
        self.assertFalse(result['engines']['gts']['probability_adjustment'])
        self.assertTrue(all(x['uncertainty_bounds'] is None for x in result['markets']))
        self.assertTrue(all(x['ga3_calibration']=='UNVALIDATED' for x in result['markets']))
        self.assertTrue(all(x['dependence_reviewed'] is False for x in result['markets']))
        self.assertTrue(all(x['quote']['valid_at_cutoff'] is False for x in result['markets'] if x['quote']))
    def test_ba3_original_validator_accepts(self):
        self.assertEqual(validate(convert(self.norm,self.ma,self.ho))['schema'],'BETPILOT_BA3_HANDOFF_V1')
    def test_ba3_original_decision_correct_no_bet(self):
        result=analyze(convert(self.norm,self.ma,self.ho))
        self.assertEqual(result['decision'],'NO_BET_INSUFFICIENT_MARKET_EVIDENCE')
        self.assertIsNone(result['primary_research_candidate'])
        self.assertIsNone(result['secondary_research_candidate'])
        self.assertFalse(result['operational_eligible'])
    def test_model_ev_matches_47_original_markets(self):
        got={x['market_id']:x for x in convert(self.norm,self.ma,self.ho)['markets']}
        for original in self.ma['markets']:
            x=got[original['market_id']]
            if x['quote']:
                self.assertAlmostEqual(ev(x['outcomes'],x['quote']['decimal_odds']),original['expected_profit_per_unit'],places=8)
    def test_one_probability_is_not_swapped(self):
        d=copy.deepcopy(self.ma)
        d['markets'][0]['model_probability']+=0.1
        with self.assertRaisesRegex(IntegrationError,'model_probability mismatch'):
            convert(self.norm,d,self.ho)
    def test_quote_provenance_spoof_rejected(self):
        n=copy.deepcopy(self.norm)
        n['bookmaker_rc15_verdict_c']['quotes'][0]['provenance']='VERIFIED_EXTERNAL_SNAPSHOT'
        with self.assertRaisesRegex(IntegrationError,'provenance mismatch'):
            convert(n,self.ma,self.ho)
    def test_ga2_version_no_fallback(self):
        result=convert(self.norm,self.ma,self.ho)
        result['engines']['goal_analyzer']['version']='GA2-RESEARCH'
        with self.assertRaisesRegex(ContractError,'version mismatch'):
            validate(result)
    def test_ma2_version_no_fallback(self):
        result=convert(self.norm,self.ma,self.ho)
        result['engines']['market_analyzer']['version']='MA2-BETA'
        with self.assertRaisesRegex(ContractError,'version mismatch'):
            validate(result)
    def test_gts_probability_adjustment_no_fallback(self):
        result=convert(self.norm,self.ma,self.ho)
        result['engines']['gts']['probability_adjustment']=True
        with self.assertRaisesRegex(ContractError,'GTS must not change probabilities'):
            validate(result)
    def test_verdict_c_without_audit_rejected(self):
        n=copy.deepcopy(self.norm)
        n['bookmaker_rc15_verdict_c']['verdict_c_audit']['schema']='UNCORRECTED_RC15'
        with self.assertRaisesRegex(IntegrationError,'Wrong native Verdict C'):
            convert(n,self.ma,self.ho)
    def test_ga3_after_cutoff_not_allowed(self):
        result=convert(self.norm,self.ma,self.ho)
        result['engines']['goal_analyzer']['data_cutoff']='2026-11-15T19:00:00+00:00'
        with self.assertRaisesRegex(ContractError,'data leakage'):
            validate(result)
    def test_quote_after_cutoff_rejected(self):
        result=convert(self.norm,self.ma,self.ho)
        next(x for x in result['markets'] if x['quote'])['quote']['timestamp']='2026-11-15T19:00:00+00:00'
        with self.assertRaisesRegex(ContractError,'after cutoff'):
            validate(result)
    def test_ga3_mutated_source_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)
            for name in ('pitchapi_identity.json','ga3_native_output.json','gts3_native_output.json','GTS3_FROZEN_MODEL_PARAMS.json'):
                shutil.copy2(NATIVE/name,p/name)
            (p/'ga3_native_output.json').write_bytes((p/'ga3_native_output.json').read_bytes()+b' ')
            with self.assertRaisesRegex(Exception,'GA3 normalized output disagrees|source artifact|SHA256'):
                audit_native_sources(MA,p,self.norm['match'],self.norm)
    def test_ma3_file_mutation_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)
            for name in ('MA3_RUN_MANIFEST.json','MA3_INPUT_NORMALIZED.json','MA3_ANALYSIS.json','BETANALYZER_HANDOFF.json'):
                shutil.copy2(OUTPUT/name,p/name)
            (p/'MA3_ANALYSIS.json').write_bytes((p/'MA3_ANALYSIS.json').read_bytes()+b' ')
            with self.assertRaisesRegex(IntegrationError,'output integrity mismatch'):
                audit_ma3(p,NATIVE,BRIDGE,MA)
    def test_bridge_file_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'rc15.json'
            x=json.loads(BRIDGE.read_text())
            x['quotes'][0]['decimal_odds']=500
            p.write_text(json.dumps(x))
            with self.assertRaisesRegex(IntegrationError,'Bookmaker bridge does not match'):
                audit_ma3(OUTPUT,NATIVE,p,MA)
    def test_full_chain_source_hash_unchanged(self):
        before={k:sha(p) for k,p in {'ba3':BA/'betanalyzer3'/'decision.py','ma3':MA/'ma3'/'core.py',
                                   'ga3':NATIVE/'ga3_native_output.json','gts3':NATIVE/'gts3_native_output.json',
                                   'rc15':BRIDGE}.items()}
        with tempfile.TemporaryDirectory() as t:
            d=run(MA,BA,OUTPUT,NATIVE,BRIDGE,t)
            self.assertEqual(d['status'],'NATIVE_MA3_TO_BA3_DECISION_PASS_RESEARCH_ONLY')
            self.assertTrue((Path(t)/'BETANALYZER_3_ANALYSIS.json').exists())
            self.assertTrue((Path(t)/'INTEGRATION_MANIFEST.json').exists())
        after={k:sha(p) for k,p in {'ba3':BA/'betanalyzer3'/'decision.py','ma3':MA/'ma3'/'core.py',
                                   'ga3':NATIVE/'ga3_native_output.json','gts3':NATIVE/'gts3_native_output.json',
                                   'rc15':BRIDGE}.items()}
        self.assertEqual(before,after)

if __name__=='__main__': unittest.main()
