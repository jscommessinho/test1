# BETPILOT | BetAnalyzer 2.0 — Step 3

**Integrazione nativa RC15 + MA2 con output GA2 conservato.** Stato `RESEARCH_ONLY`.

## Risultato

- `integrate_native.py`: esegue i moduli RC15 e MA2 **originali** e l'adapter tra i due; GA2 usa λ provenienti da output forniti o archiviati, senza nuovo fit.
- `original_sources/`: copie immutate dei due ZIP forniti dall'utente (con manifest originali).
- `examples/SYNTHETIC_FULL_INTEGRATION.json`: esempio con GA2 archivio Genoa–Lecce e **quote sintetiche**, senza fonte Bet365 reale né timestamp verificato. Orario kickoff nell'esempio assunto per il test dei controlli, non certificato storicamente.
- `examples/FULL_RESULT/`: input/output nativo dei tre livelli e risultato BetAnalyzer, inclusi manifest.
- `examples/BOOKMAKER_ONLY_RESULT/`: percorso RC15 → MA2 senza GA2.
- `docs/STEP3_INTEGRATION_REPORT.md`: risultati dei test, limiti e prossimo passo.

## Esecuzione

```bash
python integrate_native.py examples/SYNTHETIC_FULL_INTEGRATION.json --output-dir my_results
```

## Test

```bash
python tests/test_native_integration.py
python original_sources/rc15/test_rc15.py
python original_sources/rc15/test_rc14.py
python original_sources/rc15/test_rc13_scenarios.py
python original_sources/ma2/BETPILOT_MARKET_ANALYZER_2_BETA/test_market_analyzer.py
```

Dipendenze RC15: Python 3.10+, `numpy`, `scipy`. Nessun accesso internet richiesto. Non modificare i file congelati nei moduli.

### Importante

Lo stato `FULL_NATIVE_EXECUTION_RESEARCH_ONLY` **non** significa FULL predittivo o finanziariamente validato. Le quote nei test sono deliberatamente sintetiche. Una previsione GA2 archiviata viene riutilizzata solo per la verifica matematica e di interfaccia; non è un fit GA2 ex novo.
