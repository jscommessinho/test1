"""Supplemental markets use untouched original MA3 core settlement."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from betpilot_v3.derived_markets import market_ids, compute

ROOT=Path(__file__).resolve().parents[1]

class ExtraMarketTests(unittest.TestCase):
    def test_market_count(self):
        self.assertEqual(len(market_ids()), 42)
        self.assertEqual(len(set(market_ids())),42)
    def test_real_original_ma3_on_synthetic_matrix(self):
        # The *source* fixture is synthetic. This proves mathematical semantics only.
        import tempfile
        from betpilot_v3.run_replay import unpack_frozen
        with tempfile.TemporaryDirectory() as td:
            ma,_=unpack_frozen(Path(td))
            original=json.loads((ma/'examples/native_synthetic/full_native_replay/MA3_INPUT_NORMALIZED.json').read_text())
            source=original['ga3_rc2']
            with patch.object(sys,'path',[str(ma),*sys.path]):
                result=compute(source)
            self.assertEqual(result['additional_market_count'],46)
            self.assertTrue(all(r['quote_status']=='ODDS_UNAVAILABLE' for r in result['market_rows']))
            dc={r['market_id']:r['model_probability'] for r in result['market_rows']}
            self.assertAlmostEqual(dc['FT.DC.1X'] + dc['FT.DC.X2'] + dc['FT.DC.12'],2.0,places=8)
            self.assertLessEqual(dc['FT.COMBO.FT.DC.1X&FT.TOTAL.OVER.2.5'],dc['FT.DC.1X']+1e-9)
            self.assertFalse(result['original_model_modified'])
    def test_bad_unverified_ga3_rejected(self):
        with self.assertRaises(ValueError):compute({'source':'Other','native_schema_parity_verified':False})
