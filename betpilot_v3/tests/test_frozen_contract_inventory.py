"""Quiet, read-only original ZIP interface contract regression, no source dumping."""
import hashlib
import json
import os
import unittest
from pathlib import Path

class FrozenNativeContractAudit(unittest.TestCase):
    def test_immutable_original_contract_inventory(self):
        work = os.environ.get('BETPILOT_UPSTREAM_WORKDIR')
        if not work:
            self.skipTest('Frozen releases extracted by existing native CI only')
        home = Path(work)
        ma = home / 'BETPILOT_MARKET_ANALYZER_3_0_NATIVE_RC2'
        ga = home / 'GA3_PITCHAPI_ONLY_RC2_2026-11-01'
        gts = home / 'BETPILOT_GTS3_RC1'
        for root, names in (
            (ma, ('ma3/native_exports.py','ma3/cli_native.py',
                  'examples/native_synthetic/ga3_native_output.json',
                  'examples/native_synthetic/gts3_native_output.json',
                  'examples/native_synthetic/match.json',
                  'examples/native_synthetic/pitchapi_identity.json')),
            (ga, ('goal_analyzer3/cli.py','goal_analyzer3/probability.py',
                  'goal_analyzer3/pitchapi.py')),
            (gts, ('src/gts3/score.py','src/gts3/source.py',
                   'reports/selected_model_parameters.json'))):
            for name in names:
                self.assertTrue((root/name).is_file(),str(root/name))
        ga_example=json.loads((ma/'examples/native_synthetic/ga3_native_output.json').read_text())
        gts_example=json.loads((ma/'examples/native_synthetic/gts3_native_output.json').read_text())
        self.assertIn('exact_scores',ga_example['prediction'])
        self.assertIn('markets',ga_example['prediction'])
        self.assertEqual(len(gts_example['predictions']),2)
        expected='7a87f0a372e895aa4cf8916f01575471937ae577c6719194cd4c0b0ac9515de6'
        digest=hashlib.sha256((gts/'reports/selected_model_parameters.json').read_bytes()).hexdigest()
        self.assertEqual(digest,expected)
