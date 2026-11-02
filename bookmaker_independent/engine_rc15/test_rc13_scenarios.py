import csv, json, math, tempfile, unittest
from pathlib import Path
import numpy as np
from rc13_scenarios import ROOT, line_split, ah_settlement, analyze, run, sha256

class ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model=json.loads((ROOT/'RC12_Fitted_Calibrators.json').read_text())
        cls.ref=json.loads((ROOT/'RC12_Reliability_Reference.json').read_text())
        with (ROOT/'RC13_Input_Esempio.csv').open() as f:cls.rows=list(csv.DictReader(f))
        cls.result=analyze(cls.rows[1],cls.model,cls.ref)

    def test_atomic_mass(self):
        self.assertAlmostEqual(sum(self.result['structural_joint_atomic_12_states'].values()),1,places=9)
    def test_9_coherence_constraints(self):
        self.assertEqual(len(self.result['cross_market_checks']),9)
        self.assertTrue(all(self.result['cross_market_checks'].values()))
    def test_score_distribution_exact_prob(self):
        m=self.result['structural_marginals']
        self.assertAlmostEqual(m['GG'],self.result['bise_unmodified']['p_GG_reconstructed'],places=8)
    def test_double_chance_complement(self):
        m=self.result['structural_marginals']
        self.assertAlmostEqual(m['1X'],1-m['2'],places=9)
    def test_nested_over(self):
        m=self.result['structural_marginals']
        self.assertTrue(m['Over1.5']>=m['Over2.5']>=m['Over3.5'])
    def test_gg_over25_identity(self):
        z=self.result['structural_joint_scenarios']['GG & Over2.5']
        self.assertLess(z,self.result['structural_marginals']['GG'])
        self.assertLess(z,self.result['structural_marginals']['Over2.5'])
    def test_do_not_multiply_dependent_markets(self):
        d=self.result['independence_diagnostic']
        self.assertGreater(abs(d['difference_pp']),0.05)
    def test_1x2_model_unchanged(self):
        from rc11_operational import analyze as old
        a=old(self.rows[1]); m=self.result['structural_marginals']
        for name in ('1','X','2'):self.assertAlmostEqual(m[name],a['market_probs']['1X2'][name],places=7)
    def test_gg_rc12_unchanged(self):
        from rc12_multimarket import analyze as previous
        a=previous(self.rows[1],self.model,self.ref)
        self.assertEqual(self.result['rc12_confidence_unmodified'],a['probabilita_e_intensita_0_10'])
    def test_line_split_quarter(self):
        self.assertEqual(line_split(-.25),(-.5,0))
        self.assertEqual(line_split(.75),(.5,1.))
        self.assertEqual(line_split(-1.),(-1.,))
        self.assertEqual(line_split(.5),(.5,))
    def test_invalid_line(self):
        with self.assertRaises(ValueError):line_split(.3)
    def test_AH_half_win_half_push(self):
        mat=np.zeros((4,4));mat[1,0]=1
        v=ah_settlement(mat,-.75,'home')
        self.assertAlmostEqual(v['win_fraction'],.5)
        self.assertAlmostEqual(v['push_fraction'],.5)
        self.assertAlmostEqual(v['loss_fraction'],0)
    def test_AH_half_loss_half_push(self):
        mat=np.zeros((4,4));mat[1,0]=1
        v=ah_settlement(mat,-1.25,'home')
        self.assertAlmostEqual(v['win_fraction'],0)
        self.assertAlmostEqual(v['push_fraction'],.5)
        self.assertAlmostEqual(v['loss_fraction'],.5)
    def test_AH_full_push(self):
        mat=np.zeros((4,4));mat[1,0]=1
        self.assertAlmostEqual(ah_settlement(mat,-1.,'home')['push_fraction'],1)
        self.assertAlmostEqual(ah_settlement(mat,-1.,'away')['push_fraction'],1)
    def test_AH_refund_opposite_sides(self):
        mat=np.zeros((3,3));mat[0,0]=1
        a=ah_settlement(mat,-.25,'home');b=ah_settlement(mat,-.25,'away')
        self.assertEqual([a['loss_fraction'],a['push_fraction']],[.5,.5])
        self.assertEqual([b['win_fraction'],b['push_fraction']],[.5,.5])
    def test_AH_fair_quote_unit_value(self):
        mat=np.zeros((3,3));mat[0,0]=.5;mat[2,1]=.5
        a=ah_settlement(mat,-.75,'home')
        q=a['fair_decimal_structural']
        self.assertAlmostEqual((q-1)*a['win_fraction']-a['loss_fraction'],0,places=10)
    def test_optional_AH_complete(self):
        row=dict(self.rows[0],ah_line_home='-0.25',ah_home_odds='1.95',ah_away_odds='1.93')
        a=analyze(row,self.model,self.ref)
        self.assertEqual(len(a['optional_AH_comparison']),2)
    def test_optional_AH_incomplete_rejected(self):
        row=dict(self.rows[0],ah_line_home='-0.25',ah_home_odds='1.95')
        with self.assertRaises(ValueError):analyze(row,self.model,self.ref)
    def test_optional_over_complete(self):
        row=dict(self.rows[0],over15='1.4',under15='3.00',over35='2.8',under35='1.4')
        a=analyze(row,self.model,self.ref)
        self.assertEqual(len(a['optional_direct_ou_diagnostics']),2)
    def test_optional_over_incomplete_rejected(self):
        row=dict(self.rows[0],over15='1.3')
        with self.assertRaises(ValueError):analyze(row,self.model,self.ref)
    def test_bad_bookmaker_rejected(self):
        row=dict(self.rows[0],bookmaker='Pinnacle')
        with self.assertRaises(ValueError):analyze(row,self.model,self.ref)
    def test_bad_quote_rejected(self):
        row=dict(self.rows[0],q1='0.75')
        with self.assertRaises(ValueError):analyze(row,self.model,self.ref)
    def test_four_examples(self):
        with tempfile.TemporaryDirectory() as d:
            m=run(ROOT/'RC13_Input_Esempio.csv',Path(d));self.assertEqual(m['n_processed'],4)
            self.assertEqual(m['n_rejected'],0)
            self.assertEqual(len(json.loads((Path(d)/'RC13_Analysis.json').read_text())['results']),4)
    def test_frozen_code_hash(self):
        self.assertEqual(sha256(ROOT/'rc8_bise_frozen_source.py'),'666af5396bc760dd7b7296ea028f5aeaef8ecd4eb123fd56277f4764232cd8df')

if __name__=='__main__':unittest.main()
