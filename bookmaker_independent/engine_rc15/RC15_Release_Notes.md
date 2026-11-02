# RC15 — Release Notes

| Component | State | Implementation |
|---|---|---|
| RC9 frozen BISE | Unchanged, verified SHA256 | `rc8_bise_frozen_source.py` |
| RC11 Bet365 de-vig | Reused | `rc11_operational.py` |
| RC11.2 GG/NG historical references | Reused, experimental | `rc11_2_confidence.py` |
| RC12 1X2, double chance and O/U calibrations | Reused | `rc12_multimarket.py` |
| RC13 exact scores, 12 exclusive cells, combos, AH | Reused | `rc13_scenarios.py` |
| RC14 optional markets / gap percentile | Reused | `rc14_operational.py` |
| RC15 integrated JSON/MD/HTML outputs | **New** | `rc15_intelligence.py` |
| RC15 reports for four historical fixtures | **New** | `demo_reports_rc15/` |
| Input quality, immutability, old model regression | **56/56 tests passed** | `test_rc15.py`, `test_rc14.py`, `test_rc13_scenarios.py` |

**Change policy:** No model refits, no new Confidence scores, no use of prospective matches, no claims of bookmaker exposure or profitability.
