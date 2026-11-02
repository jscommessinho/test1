"""RC15 presentation invariants, error handling and frozen-core compatibility."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from rc15_intelligence import run,verify_inputs,summarise,render_html,render_match_md,VERSION
from rc14_operational import diagnose

HERE=Path(__file__).resolve().parent

class RC15Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model,cls.ref,cls.stats=verify_inputs()
        with (HERE/'RC14_Real_Matches_Demo.csv').open(newline='') as f:cls.rows=list(csv.DictReader(f));cls.columns=list(cls.rows[0])
        cls.raw=diagnose(cls.rows[0],cls.model,cls.ref,cls.stats)
        cls.report=summarise(cls.raw,cls.ref)

    def make_csv(self,rows,cols=None):
        t=tempfile.TemporaryDirectory();p=Path(t.name)
        f=p/'prices.csv'
        with f.open('w',newline='',encoding='utf8') as out:
            w=csv.DictWriter(out,fieldnames=cols or self.columns,extrasaction='ignore');w.writeheader();w.writerows(rows)
        self.addCleanup(t.cleanup)
        return f,p/'out'

    def test_01_frozen_hash(self):
        self.assertEqual(self.report['data_quality']['frozen_BISE_verified'],True)
        self.assertEqual(self.report['schema'],VERSION)
    def test_02_12_states_sum_one(self):
        self.assertAlmostEqual(sum(self.report['bise_structural']['all_12_atomic_states'].values()),1,places=8)
    def test_03_1x2_sum_one(self):
        self.assertAlmostEqual(sum(self.report['single_market_probabilities_RC12_experimental']['1X2']['p'][x] for x in ('1','X','2')),1,places=8)
    def test_04_1x2_double_chance(self):
        p=self.report['single_market_probabilities_RC12_experimental']['1X2']['p'];self.assertAlmostEqual(p['1X'],p['1']+p['X'],places=8)
    def test_05_btts_complement(self):
        p=self.report['single_market_probabilities_RC12_experimental']['BTTS']['p'];self.assertAlmostEqual(p['GG']+p['NG'],1,places=8)
    def test_06_no_additional_independent_signal(self):
        self.assertEqual(self.report['optional_market_diagnostics']['independent_signals_added'],0)
    def test_07_status_no_certified_edge(self):
        self.assertIs(self.report['status']['value_bet_or_ROI_certified'],False)
        self.assertIs(self.report['status']['joint_scenarios_empirically_calibrated'],False)
        self.assertEqual(self.report['status']['forward_validation'],'PAUSED_AS_REQUESTED')
    def test_08_all_four_examples_pass(self):
        f,out=self.make_csv(self.rows);manifest=run(f,out)
        self.assertEqual((manifest['analyzed'],manifest['rejected']),(4,0))
        self.assertIn('Lorient U19',(out/'RC15_Intelligence_Report.md').read_text())
        self.assertIn('<!doctype html>',(out/'RC15_Intelligence_Report.html').read_text())
    def test_09_seven_quotes_only(self):
        f,out=self.make_csv([self.rows[0]],cols=['bookmaker','match_day','analysis_kind','league','home','away','q1','qx','q2','qo','qu','qgg','qng'])
        m=run(f,out);self.assertEqual((m['analyzed'],m['rejected']),(1,0))
        payload=json.loads((out/'RC15_Analysis.json').read_text())['reports'][0]
        self.assertEqual(payload['data_quality']['diagnostic'],'NO_OPTIONAL_GOAL_MARKETS')
    def test_10_duplicate_rejected(self):
        f,out=self.make_csv([self.rows[0],self.rows[0]])
        m=run(f,out);self.assertEqual((m['analyzed'],m['rejected']),(1,1))
    def test_11_incomplete_required_odds_rejected(self):
        r=self.rows[0].copy();r['qo']=''
        f,out=self.make_csv([r]);m=run(f,out);self.assertEqual(m['rejected'],1)
    def test_12_wrong_bookmaker_rejected(self):
        r=self.rows[0].copy();r['bookmaker']='OtherBook'
        f,out=self.make_csv([r]);m=run(f,out);self.assertEqual(m['rejected'],1)
    def test_13_markup_escape_html(self):
        r=self.report.copy();r['identity']=dict(self.report['identity']);r['identity']['home']='<script>alert(1)</script>'
        html=render_html([r],[],{})
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn('&lt;script&gt;',html)
    def test_14_previous_RC13_same_joint(self):
        a=self.report['bise_structural']['top_6_mutually_exclusive_scenarios'][0]
        raw=self.raw['scenario_intelligence_RC13']['structural_joint_atomic_12_states']
        self.assertAlmostEqual(a['p'],raw[a['event']],places=10)
    def test_15_description_not_certified(self):
        md=render_match_md(self.report)
        self.assertIn('non calibrati',md)
        self.assertIn('nessun vantaggio economico',md)
    def test_16_missing_columns_fails_fast(self):
        f,out=self.make_csv([{'home':'A','away':'B'}],cols=['home','away'])
        with self.assertRaisesRegex(ValueError,'MISSING_COLUMNS'):run(f,out)

if __name__=='__main__':unittest.main()
