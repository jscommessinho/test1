# BETPILOT V3 — FULL ANALYSIS research-only checkpoint

## Integrazione GitHub verificata
- PR #3 è stata incorporata in `main` il 2026-11-01 (merge `b11f6392dfa0de62c78b2343230a8320515d73a6`).
- Il percorso `betpilot_v3/run_verified_inputs.py` esegue, su **file già preparati**, RC15+Verdetto C → MA3 Native RC2 → BA3 RC1, riutilizzando output originali GA3/GTS3.
- Il nuovo `full_analysis.py` è solo uno **strato di discovery e orchestrazione**: non cambia nessuno dei cinque motori, non installa pianificazioni né avvia scommesse.

## Comando unico

Da root del repository:

```bash
python betpilot_v3/full_analysis.py \\
  --request "FULL ANALYSIS: Inter - Juventus" \\
  --sources-root /secure/native_fixture_bundles \\
  --output-dir /secure/output/inter_juventus
```

Facoltativi per disambiguare: `--event-id`, `--kickoff` (ISO-8601 con timezone), `--competition`.

Ogni sottocartella di `--sources-root` deve contenere **sei file generati da pipeline legittime**:
`match.json`, `pitchapi_identity.json`, `ga3_native_output.json`,
`gts3_native_output.json`, `GTS3_FROZEN_MODEL_PARAMS.json`,
`RC15_EVENT_INPUT.json`.

Non devono essere preparati manualmente ad ogni analisi: devono arrivare da un publisher automatico di fixture e output nativi, che oggi **non è ancora installato e validato**. I JSON incompleti, ambigui, non conformi, senza evidenza temporale o non allineati sono rifiutati. Il modulo nativo V3 esegue la validazione profonda dei contratti.

## Output
- `FULL_ANALYSIS_STATUS.json` sempre, anche in caso di blocco.
- Se la catena supera i controlli, `NATIVE_CHAIN/MA3/` e `NATIVE_CHAIN/BA3/` con output originali e il report del decision engine.
- `RESEARCH_REPORT_AVAILABLE` NON significa `PRODUCTION_READY` o `ECONOMIC_VALIDATION_COMPLETE`.
- In caso di input mancanti: `NO_BET_DATA_OR_INTEGRATION_BLOCKED` più codice e causa espliciti.

## Dati e vincoli
Il ramo `pitchapi-daily-data` raccoglie **partite terminate** (snapshot post-partita); questi dati sono potenzialmente utili per storico e fitting cronologico, ma non sono prediction output prematch della partita target. Non usarli come caratteristiche disponibili pre-kickoff della stessa partita. Lo stato più recente osservato all'audit (2026-11-01T06:51:27Z) segnala 141 PASS_INTERNAL, 5 WARNING_INTERNAL_DELTA, 5 INCOMPLETE_SOURCE, 21 STRUCTURAL_ANOMALY, stop `LOCAL_REQUEST_BUDGET`, 350 richieste.

Per attivare la FULL ANALYSIS su partita reale servono ancora:
1. Fixture discovery PitchAPI/quote-source documentato, autenticato, con quota budget;
2. data engineering per costruire input nativi di GA3 e GTS3 **con dati precedenti al cutoff**;
3. snapshot quote ante partita autentico e traccia di acquisizione verificabile;
4. esecuzione end-to-end da dati reali e backtesting cronologico fuori campione;
5. revisione separata dell'incertezza, del valore economico e del leakage.

Senza questi elementi la modalità resta **RESEARCH_ONLY**; MA3/BA3 non possono stimare in modo validato l'incertezza e non possono raccomandare puntate.

## Test
La suite `tests/test_full_analysis.py` copre parsing, corrispondenza esatta, ambiguità, leakage, file mancanti, JSON duplicato, symlink, mancato runner e fallimento nativo. In CI viene eseguita tramite `run_replay.py --test` nel workflow già esistente, senza modificare workflow o database di produzione.
