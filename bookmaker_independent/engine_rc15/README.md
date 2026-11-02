# BETPILOT — Bookmaker Analyzer 2.0 / RC15

RC15 consolidates RC11–RC14 into an easy-to-use single-match or batch report. Its source models and historical reference files are included locally, **without a change to fitted coefficients**.

## Requirements

Python 3.10+; `numpy`, `scipy`, `pandas`, `pytest` for tests (install using your Python environment package manager).

## Run

```bash
python rc15_intelligence.py --input RC14_Real_Matches_Demo.csv --output-dir demo_reports_rc15
```

For minimum seven quotes:

```bash
python rc15_intelligence.py --input RC15_Input_7_Quote_Example.csv --output-dir out_seven_odds
```

Outputs:

- `RC15_Intelligence_Report.html` — standalone styled HTML report, opens in browser offline.
- `RC15_Intelligence_Report.md` — Markdown report to paste into ChatGPT or an internal workspace.
- `RC15_Analysis.json` — machine-readable report with probability definitions and source methods.
- `RC15_Run_Manifest.json` — SHA256 of input, frozen engine and statistics; number of accepted/rejected rows.

CSV: one fixture per row, `bookmaker,match_day,analysis_kind,league,home,away,q1,qx,q2,qo,qu,qgg,qng` plus optional `over15,under15,over35,under35,ah_line_home,ah_home_odds,ah_away_odds`. Decimal odds; Bet365 only; accepted valid fixture classes per original blacklist. Example inputs and blank template included. `match_day` and `analysis_kind` are recommended; use `analysis_kind=prematch` for current pre-kickoff quotes only, `storico` for historic reconstructions. This script does not independently verify capture timestamps.

## Quality and regression checks

```bash
python -m pytest -q test_rc15.py test_rc14.py test_rc13_scenarios.py
```

All 56 tests passed. Passing software tests does not validate predictive accuracy.

## Critical methodological limits

RC12 marginal calibrations and the RC9 BISE joint matrix are not the same probability distribution: **do not mix** RC12 and BISE marginals to build a new joint probability. The 0–10 intensity is `10 × probability`, NOT an independently measured reliability. The BISE matrix derives from normalized 1X2 and Over/Under2.5 (not direct GG); additional Bet365 prices are correlated diagnostic inputs, not independent evidence. Exact scores and combined outcomes are structural/un-calibrated individually. Claims of bookmaker exposure, positive ROI, and certified EV are explicitly unsupported. Prospective validation was postponed on the user's instruction.

Source Bet365/closing provenance for LivePick historic export is user-attested, quote-specific timestamps are not independently archived. Historical selection is matches sampled by LivePick during play.
