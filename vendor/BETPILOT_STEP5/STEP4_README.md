# BETPILOT | Step 4 — API-Football → Bookmaker RC15

**Architettura:** modulo `api_football_adapter.py` sul confine fra acquisizione dati e Bookmaker Analyzer. `original_sources/rc15/*`, `original_sources/ma2/*` e `integrate_native.py` **immutati** rispetto al rilascio Step 3. Lo Step 4 non modifica i motori probabilistici.

## Cosa è stato realizzato

- Client HTTP per `https://v3.football.api-sports.io/fixtures` e `.../odds` con `x-apisports-key` letto da variabile d'ambiente `APISPORTS_KEY` (mai salvato negli output).
- Due forme di ricerca: `--fixture-id` (2 chiamate) oppure `--home --away --date --horizon-days` (almeno 3 chiamate; 1 per giorno più 2). Il matching è per nomi esatti normalizzati; le ambiguità non sono risolte arbitrariamente.
- Solo bookmaker ID 8 **Bet365** (match *id e nome*). Estrazione stretta da mercati prematch **id 1** Match Winner, **id 5** Goals Over/Under, **id 8** Both Teams Score. `Goal Line` id 50 rimane distinta, senza mescolare prezzi.
- Quote obbligatorie RC15: `q1/qx/q2/qo/qu/qgg/qng`. Facoltativi: O/U1.5 e O/U3.5 se coppie complete. Incoerenze, quote mancanti, conflitti, status non prematch, timestamp tardivi e nomi discordanti vengono **rifiutati**.
- Archiviazione snapshot provider, hash SHA-256 e timestamp **di cattura lato client** distinto dal campo `update` **riportato da API-Football**.
- Output `RC15_EVENT_INPUT.json` già compatibile con `integrate_native.py`. Con `--run-native`, esegue i codici originali RC15 → MA2 e genera il report `ANALYSIS` in modalità `RC15_MA2_NATIVE_BOOKMAKER_ONLY`. **Nessuna GA2 nuova previsione viene generata.**
- Controllo massimo richieste HTTP per singola esecuzione (`--max-requests`, default 10); non è un limite globale giornaliero, che va controllato a livello di API/ambiente.

## Comando locale con credenziale sicura (non incollarla nella conversazione)

Per sicurezza configura `APISPORTS_KEY` mediante un secret manager dell'ambiente d'esecuzione; non scrivere la chiave nei file del progetto, nei log o nella chat. Nel terminale macOS/Linux, puoi leggerla senza echo con `read -rs APISPORTS_KEY; export APISPORTS_KEY` (vale per bash); su Windows PowerShell configura una variabile d'ambiente nel tuo contesto fidato.

Esegui da questa directory:

```bash
python api_football_adapter.py --fixture-id ID_PARTITA_FUTURA --output-dir ./live_run --run-native
```

Oppure, quando non conosci l'ID:

```bash
python api_football_adapter.py --home "Squadra Casa" --away "Squadra Ospite" --date YYYY-MM-DD --horizon-days 2 --output-dir ./live_run --run-native
```

Le date passate vengono respinte e `--date` per la ricerca è trattata in UTC. L'utente dovrà assicurarsi di utilizzare la data corretta rispetto al fuso della partita. Ricerche automatiche con omonimie o nomi non esatti sono volutamente rifiutate; per questi casi occorre un miglior matching verificato.

## Collaudo offline riproducibile: quote inviate dall'utente

I JSON in `examples/step4/RAW_*_RECONSTRUCTED.json` sono **ricostruzioni minime** delle risposte fornite in chat per **Coritiba–Vitória, fixture 1492434, 31/10/2026**, non la cattura originale della risposta HTTP e **non** un test live di autenticazione API.

```bash
python api_football_adapter.py --fixture-id 1492434 \
  --fixtures-json examples/step4/RAW_FIXTURE_RECONSTRUCTED.json \
  --odds-json examples/step4/RAW_ODDS_RECONSTRUCTED.json \
  --output-dir examples/step4/REAL_QUOTES_REPLAY --run-native
```

Il collaudo ha estratto 7/7 quote e dato esito `RC15_MA2_NATIVE_BOOKMAKER_ONLY`, senza alcuna richiesta HTTP o segreto. Il valore dell'Under 2.5 è 1.80 da `Goals Over/Under` id 5: il diverso 1.85 da `Goal Line` id 50 **non** è stato utilizzato.

## Verifiche eseguite in questa sessione

- 16/16 nuovi test data adapter.
- 25/25 test d'integrazione Step 3.
- 24/24 test RC13, 16/16 RC14, 16/16 RC15 originali.
- 29/29 test MA2 originali.
- **Totale 126/126** test passati nei casi verificati.

I test non dimostrano copertura per tutte le partite, stabilità dell'accesso API nel tempo, qualità dei prezzi Bet365 al momento esatto né accuratezza predittiva. Il **primo run live autenticato non è stato eseguito** perché la chiave privata non è configurata in questo ambiente. Il test `test_direct_api_simulated_auth_fetch` usa un client **mock**, non una connessione reale.

## Provenienza / affidabilità

Quando si scarica direttamente da API-Football, `VERIFIED_EXTERNAL_SNAPSHOT` significa soltanto *risposta del provider raccolta e archiviata dall'applicazione*, con hash e tempo locale della cattura; **non** significa che l'API abbia certificato un timestamp direttamente emesso da Bet365. Il provider rimane la fonte mediata. Gli esempi offline mantengono `USER_SUPPLIED_NOT_TIMESTAMP_VERIFIED`.

Le quote prematch dipendono dalla copertura e dalla finestra temporale del provider; se mancano, il sistema si astiene. Il servizio documenta aggiornamenti tipici ogni tre ore e mantiene uno storico molto breve: per dataset futuri servono snapshot conservati autonomamente.

Lo stato delle analisi rimane `RESEARCH_ONLY`; nessuna promozione della validazione GA2 o MA2 viene implicata da questa integrazione.

## Prossimo step

Configurare una chiave **fuori dalla chat**, in un esecutore che supporta credenziali private e uscite HTTPS; poi testare in modo mirato una partita futura e controllare i log (esclusa la chiave). Integrare successivamente FootyStats mediante un secondo adapter verso la pipeline GA2, dopo verifica della copertura e delle definizioni statistiche.
