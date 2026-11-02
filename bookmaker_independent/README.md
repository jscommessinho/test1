# BETPILOT | Bookmaker Analyzer 2.0 RC15 + Verdetto C — indipendente

Questo pacchetto usa **solo** i file in `bookmaker_independent/`.
Il motore RC15 originale è sotto `engine_rc15/`, senza alterazioni.
Non chiama Step5, GA3, GTS3, Market Analyzer, BetAnalyzer o BUCO NERO.

### Comandi

```bash
cd bookmaker_independent
python -m pip install -r requirements.txt
python -m bookmaker --input-csv engine_rc15/RC15_Input_7_Quote_Example.csv --output-dir output/demo
python -m bookmaker --status-only --output-dir output/status
python -m bookmaker --search --home Sassuolo --away Fiorentina --date 2026-11-02 --league-id 135 --output-dir output/sassuolo
```

Per le quote API è obbligatorio il secret `APISPORTS_KEY` nel repository BETPILOT PREMATCH; mai salvarlo nei file. Prima di ogni richiesta billable, il programma controlla il budget tramite `/status`; massimo 4 chiamate e riserva minima 20. Se Bet365 non fornisce tutte e sette le quote (1,X,2,O/U2.5,GG/NG), restituisce `BOOKMAKER_ABSTAIN`, senza inventare prezzi.

Il workflow dedicato è `.github/workflows/bookmaker-rc15-independent.yml`. Per analisi chat, aggiornare `integration/bookmaker-request.json`; per avvio manuale selezionare Actions → Bookmaker independent.

RC15 usa il suo calibratore RC12 originale, incluso. Verdetto C nasconde i riferimenti storici non coerenti; non modifica le probabilità del modello. Il sistema è RESEARCH_ONLY, senza ROI o validazione predittiva certificata.
