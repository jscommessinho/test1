import csv,json,math,tempfile,unittest
from pathlib import Path
import numpy as np
from scipy.stats import skellam
from rc14_analyze import compute_structural_ou,parse_handicap,settle_bins_home,de_vig_two
from rc14_operational import diagnose,format_report,run,ROOT
from rc11_operational import analyze as frozen,score_distribution
from rc13_scenarios import analyze as previous,ah_settlement

class RC14Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.model=json.loads((ROOT/'RC12_Fitted_Calibrators.json').read_text())
  cls.ref=json.loads((ROOT/'RC12_Reliability_Reference.json').read_text())
  cls.stats=json.loads((ROOT/'RC14_Statistics.json').read_text())
  with (ROOT/'RC13_Input_Esempio.csv').open() as f:cls.rows=list(csv.DictReader(f))
  cls.default=dict(cls.rows[0]);cls.default.update(over15='1.45',under15='2.80',over35='3.20',under35='1.32')

 def test_ou_formula_exact_grids(self):
  for row in self.rows:
   a=frozen(row);b=a['bise'];m,t=score_distribution(b['lambda_home'],b['lambda_away'],a['market_probs']['1X2']['X'])
   p15,p35,tf=compute_structural_ou(np.array([b['lambda_home']]),np.array([b['lambda_away']]),np.array([a['market_probs']['1X2']['X']]))
   i,j=np.indices(m.shape)
   self.assertAlmostEqual(p15[0],m[i+j>=2].sum(),places=9)
   self.assertAlmostEqual(p35[0],m[i+j>=4].sum(),places=9)
   self.assertAlmostEqual(tf[0],t,places=9)
 def test_numerical_freeze_gg(self):
  row=self.rows[1];a=frozen(row);b=a['bise']
  _,_,t=compute_structural_ou(np.array([b['lambda_home']]),np.array([b['lambda_away']]),np.array([a['market_probs']['1X2']['X']]))
  calc=(1-math.exp(-b['lambda_home']))*(1-math.exp(-b['lambda_away']))-t[0]
  self.assertAlmostEqual(calc,b['p_GG_reconstructed'],places=9)
 def test_de_vig_two(self):
  p,vig,valid=de_vig_two(np.array([1.91]),np.array([1.91]))
  self.assertTrue(valid[0]);self.assertAlmostEqual(p[0],.5,places=9)
 def test_ah_column_parsing(self):
  self.assertEqual(parse_handicap('[ODDS - PRE-MATCH] Asian Handicap - Home Team 0.0,-0.5'),('home',-.25))
  self.assertEqual(parse_handicap('[ODDS - PRE-MATCH] Asian Handicap - Away Team 0.0,+0.5'),('away',.25))
  self.assertEqual(parse_handicap('[ODDS - PRE-MATCH] Asian Handicap - Home Team -1.0,-1.5'),('home',-1.25))
  self.assertIsNone(parse_handicap('random'))
 def test_ah_bins_vs_grid(self):
  row=self.rows[1];a=frozen(row);b=a['bise'];px=a['market_probs']['1X2']['X'];ph=a['market_probs']['1X2']['1'];pa=a['market_probs']['1X2']['2']
  m,t=score_distribution(b['lambda_home'],b['lambda_away'],px)
  dp=skellam.pmf(1,b['lambda_home'],b['lambda_away'])+t
  dm=skellam.pmf(-1,b['lambda_home'],b['lambda_away'])+t
  for line in [-1.25,-1.,-.75,-.5,-.25,0.,.25,.5,.75,1.,1.25]:
   W,P,L=settle_bins_home(pa-dm,dm,px,dp,ph-dp,line)
   v=ah_settlement(m,line,'home')
   self.assertAlmostEqual(W,v['win_fraction'],places=8)
   self.assertAlmostEqual(P,v['push_fraction'],places=8)
   self.assertAlmostEqual(L,v['loss_fraction'],places=8)
 def test_frozen_rc13_scenarios_unchanged(self):
  v=diagnose(self.default,self.model,self.ref,self.stats)
  old=previous(self.default,self.model,self.ref)
  self.assertEqual(v['scenario_intelligence_RC13']['structural_joint_atomic_12_states'],old['structural_joint_atomic_12_states'])
 def test_typical_stability_score_or_outlier_not_confidence(self):
  v=diagnose(self.default,self.model,self.ref,self.stats)
  self.assertIn(v['market_diagnostic']['score'],('HISTORICALLY_TYPICAL_PRICE_GAPS','UNUSUAL_CROSS_MARKET_GAP','MARKET_PROBABILITY_INCONSISTENCY'))
  self.assertEqual(v['market_diagnostic']['independent_signal_count_added'],0)
  self.assertFalse(v['predictive_edge_certified'])
 def test_reject_inconsistent_prices(self):
  row=dict(self.rows[0],over15='4',under15='1.25')
  v=diagnose(row,self.model,self.ref,self.stats)
  self.assertEqual(v['market_diagnostic']['score'],'MARKET_PROBABILITY_INCONSISTENCY')
 def test_missing_ou_never_claim_confidence(self):
  v=diagnose(self.rows[0],self.model,self.ref,self.stats)
  self.assertEqual(v['market_diagnostic']['score'],'NO_OPTIONAL_GOAL_MARKETS')
  self.assertFalse(v['predictive_edge_certified'])
 def test_one_optional_pair(self):
  v=diagnose(dict(self.rows[0],over35='3.1',under35='1.34'),self.model,self.ref,self.stats)
  self.assertEqual(v['market_diagnostic']['coverage_goal_markets'],1)
 def test_optional_AH_quarter(self):
  row=dict(self.rows[0],ah_line_home='-0.25',ah_home_odds='1.92',ah_away_odds='1.93')
  v=diagnose(row,self.model,self.ref,self.stats)
  self.assertEqual(len(v['market_diagnostic']['Asian_Handicap_direct_comparisons']),2)
 def test_no_bookmaker_substitution(self):
  with self.assertRaises(ValueError):diagnose(dict(self.rows[0],bookmaker='Pinnacle'),self.model,self.ref,self.stats)
 def test_incomplete_optional_pair_rejected(self):
  with self.assertRaises(ValueError):diagnose(dict(self.rows[0],over15='1.3'),self.model,self.ref,self.stats)
 def test_packaging_and_cli_reports(self):
  with tempfile.TemporaryDirectory() as temp:
   mf=run(ROOT/'RC13_Input_Esempio.csv',temp)
   self.assertEqual(mf['n_accepted'],4);self.assertEqual(mf['n_rejected'],0)
   self.assertTrue((Path(temp)/'RC14_Report.md').exists())
 def test_reference_dimensions(self):
  self.assertEqual(self.stats['rows_checkpoint'],512420)
  self.assertEqual(self.stats['coherence']['all_3_markets_present'],512301)
 def test_inputs_keep_market_families_distinct(self):
  v=diagnose(self.default,self.model,self.ref,self.stats)
  self.assertEqual(len(v['market_diagnostic']['gaps']),2)
  self.assertEqual(sum(v['scenario_intelligence_RC13']['structural_joint_atomic_12_states'].values()),1)

if __name__=='__main__':unittest.main()
