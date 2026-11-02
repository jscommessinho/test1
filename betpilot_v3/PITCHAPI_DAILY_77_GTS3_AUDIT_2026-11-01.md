# BETPILOT FASE 1 — Audit read-only disponibilità statistiche originali GTS3

**Prova:** [GitHub Actions job](https://github.com/jscommessinho/betpilot-prematch-analyzer/actions/runs/41291587469) eseguito sulla PR #6, lettura del branch privato `pitchapi-daily-data` mediante git fetch/show, **0 richieste PitchAPI aggiuntive**. Pin di lega da `data/pitchapi/league_pins_77.json` (schema originale, 2026-10-31).

**Ambito ristretto:** 5 partizioni daily 2026-10-28…2026-11-01, con snapshot raccolti 2026-11-01 06:44:35–06:51:26 UTC. Non include i separati artifact storici dei backfill, che non sono presenti nel branch giornaliero esaminato. I conteggi sono snapshot di quell'istante, non copertura futura.

**Totali:** 328 record, 294 record con stato PASS_INTERNAL, All expected_goals/total_shots e ID squadra raw coerenti; 328 senza stagione nella fixture daily; 64/77 leghe hanno almeno una fixture salvata, 13 non hanno fixture in queste cinque partizioni. 575 coppie lega/squadra con candidati, massimo 2 partite per squadra, **0 squadre con almeno 10**, **0 con almeno 4**. I 328 record hanno raw `/stats`; 11 non contengono simultaneamente entrambi i campi All obbligatori. PASS_INTERNAL è controllo interno, NON verifica indipendente degli xG.

**Matrice per tutte le 77 leghe:** `Max/squadra` conta soltanto i candidati GTS3 di questa finestra. `0` può significare nessuna gara terminata/acquisita nei cinque giorni, non indisponibilità definitiva presso il provider.

| Lega | Paese | ID PitchAPI | Record | PASS+All GTS3 | Senza stagione | Max/squadra |
|---|---|---|---:|---:|---:|---:|
| 1. Division | DEN | `l_1rPvVD` | 4 | 4 | 4 | 1 |
| 1. Lig | INT | `l_1BFfna` | 5 | 5 | 5 | 1 |
| 1. liga | SVK | `l_4MU9Yh` | 4 | 4 | 4 | 1 |
| 1. Liga | CZE | `l_0F4I4F` | 5 | 5 | 5 | 1 |
| 2. Bundesliga | GER | `l_3hvsne` | 6 | 6 | 6 | 1 |
| 3. Liga | GER | `l_47077Y` | 7 | 0 | 7 | 0 |
| A-League | AUS | `l_4MPd7x` | 5 | 5 | 5 | 1 |
| Allsvenskan | SWE | `l_1XsMdH` | 3 | 3 | 3 | 1 |
| Besta deild | ISL | `l_1TTHxD` | 0 | 0 | 0 | 0 |
| Bundesliga | AUT | `l_3zsMpo` | 3 | 3 | 3 | 1 |
| Bundesliga | GER | `l_1Isor4` | 7 | 6 | 7 | 1 |
| Challenge League | SUI | `l_4auCPk` | 4 | 4 | 4 | 1 |
| Challenger Pro League | BEL | `l_1pQOHK` | 5 | 5 | 5 | 1 |
| Champions League | INT | `l_0bfbkO` | 0 | 0 | 0 | 0 |
| Championship | ENG | `l_4DWyTT` | 12 | 12 | 12 | 1 |
| Conference League | INT-2 | `l_2WZ2tt` | 0 | 0 | 0 | 0 |
| Division Profesional | PAR | `l_4FPF2L` | 5 | 5 | 5 | 1 |
| Eerste Divisie | NED | `l_49KZaB` | 5 | 4 | 5 | 1 |
| Ekstraklasa | POL | `l_214sqK` | 6 | 6 | 6 | 1 |
| Eliteserien | NOR | `l_0uqFK8` | 3 | 3 | 3 | 1 |
| Eredivisie | NED | `l_4H43wr` | 4 | 4 | 4 | 1 |
| Europa League | INT-2 | `l_38d9HA` | 0 | 0 | 0 | 0 |
| First Division A | BEL | `l_2L6d1F` | 5 | 5 | 5 | 1 |
| HNL | CRO | `l_2tFk9k` | 3 | 3 | 3 | 1 |
| Indian Super League | IND | `l_1qpFVw` | 3 | 3 | 3 | 1 |
| J. League | JPN | `l_2ApGa4` | 7 | 7 | 7 | 1 |
| J3 League | JPN | `l_3fglZv` | 10 | 0 | 10 | 0 |
| K League 1 | KOR | `l_2EKrnc` | 0 | 0 | 0 | 0 |
| K League 2 | KOR | `l_271pqa` | 4 | 0 | 4 | 0 |
| LaLiga | ESP | `l_0ErfuF` | 5 | 5 | 5 | 1 |
| LaLiga2 | ESP | `l_3kyHLd` | 4 | 3 | 4 | 1 |
| League One | ENG | `l_0ahLw8` | 11 | 11 | 11 | 1 |
| League Two | ENG | `l_1W6idJ` | 12 | 12 | 12 | 1 |
| Liga 1 | PER | `l_3uNwVX` | 5 | 5 | 5 | 1 |
| Liga de Primera | CHI | `l_3qNjzC` | 3 | 3 | 3 | 1 |
| Liga I | ROU | `l_0kSJLz` | 3 | 3 | 3 | 1 |
| Liga MX | MEX | `l_3v84VE` | 8 | 7 | 8 | 2 |
| Liga Portugal | POR | `l_4QexZg` | 3 | 3 | 3 | 1 |
| Liga Profesional | ARG | `l_45VZuL` | 7 | 7 | 7 | 1 |
| Ligat ha'Al | ISR | `l_16Sq13` | 4 | 4 | 4 | 1 |
| Ligue 1 | FRA | `l_3FJFUl` | 4 | 3 | 4 | 1 |
| Ligue 2 | FRA | `l_0275VP` | 7 | 7 | 7 | 1 |
| MLS | USA | `l_0eg6C9` | 18 | 18 | 18 | 2 |
| MLS Next Pro | USA | `l_3Tn1kG` | 1 | 1 | 1 | 1 |
| Paulista A1 | BRA | `l_4V6jPs` | 0 | 0 | 0 | 0 |
| Premier Division | IRL | `l_1eJSA7` | 5 | 4 | 5 | 1 |
| Premier League | CAN | `l_3UiOcj` | 0 | 0 | 0 | 0 |
| Premier League | EGY | `l_46aZMC` | 8 | 8 | 8 | 1 |
| Premier League | ENG | `l_4WFCIZ` | 8 | 8 | 8 | 1 |
| Premier League | RUS | `l_2Mh0jT` | 3 | 3 | 3 | 1 |
| Premier Soccer League | RSA | `l_1Kknkl` | 8 | 8 | 8 | 1 |
| Premiership | SCO | `l_1LMdEO` | 3 | 3 | 3 | 1 |
| Primera A | COL | `l_1xXulJ` | 6 | 5 | 6 | 1 |
| Primera Division | BOL | `l_4dzm4e` | 4 | 4 | 4 | 1 |
| Primera Division | VEN | `l_1TVRZW` | 2 | 2 | 2 | 1 |
| Pro League | UAE | `l_0aEfof` | 0 | 0 | 0 | 0 |
| Qatar Stars League | QAT | `l_3wr44B` | 6 | 5 | 6 | 1 |
| Saudi First Division | KSA | `l_3CB6qX` | 0 | 0 | 0 | 0 |
| Saudi Pro League | KSA | `l_0Lpz1f` | 9 | 9 | 9 | 1 |
| Serie A | BRA | `l_1D8Xrl` | 7 | 6 | 7 | 1 |
| Serie A | ECU | `l_1dmt9H` | 3 | 3 | 3 | 1 |
| Serie A | ITA | `l_0ALvwF` | 5 | 5 | 5 | 1 |
| Serie B | BRA | `l_3qUkB8` | 4 | 4 | 4 | 1 |
| Serie B | ITA | `l_0jy5yE` | 5 | 4 | 5 | 1 |
| Super League | CHN | `l_20pNSc` | 0 | 0 | 0 | 0 |
| Super League | SUI | `l_4d6Bwl` | 3 | 3 | 3 | 1 |
| Super League 1 | GRE | `l_3xwVF2` | 1 | 1 | 1 | 1 |
| Super Lig | TUR | `l_0S1uaf` | 4 | 4 | 4 | 1 |
| Super Liga | SRB | `l_2xBPKk` | 1 | 1 | 1 | 1 |
| Superligaen | DEN | `l_49Zmno` | 1 | 1 | 1 | 1 |
| Thai League 1 | THA | `l_3hW1NO` | 3 | 3 | 3 | 1 |
| USL Championship | USA | `l_3yVUAO` | 5 | 3 | 5 | 1 |
| USL League One | USA | `l_3Kf7Ni` | 2 | 2 | 2 | 1 |
| Veikkausliiga | FIN | `l_3Ci5Cg` | 2 | 1 | 2 | 1 |
| Virsliga | LVA | `l_45xKXM` | 0 | 0 | 0 | 0 |
| Women's Premier League | KSA | `l_1SGwQT` | 0 | 0 | 0 | 0 |
| World Cup | INT | `l_1XLtP0` | 0 | 0 | 0 | 0 |

**Conseguenze:** una stagione daily non può essere dedotta automaticamente; il nuovo adapter cerca soltanto un record originale della **stessa partita** negli archivi storici pinned e richiede corrispondenza rigorosa ID, giorno, squadre e risultato. Non sostituisce stats team con shot xG. Anche quando la stagione si recupera, serve uno storico originale autentico di molte altre partite per ciascuna squadra, coerente col periodo di calibrazione congelato. Nessun motore o workflow modificato.
