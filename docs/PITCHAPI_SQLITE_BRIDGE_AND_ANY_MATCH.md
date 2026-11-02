# BETPILOT — Database PitchAPI originale + comando per qualsiasi partita

## Che cosa è stato verificato

- Lo ZIP `BET-PILOT-Prematch-Analyzer-main.zip` è **solo il codice**, non contiene file `.sqlite`.
- Lo storico effettivo si trova sul ramo **`pitchapi-warehouse`** della repository PRIVATA `jscommessinho/BET-PILOT-Prematch-Analyzer` e include `data/catalogue.sqlite`, più archivi stagionali per **eng-premier, ita-seriea, fra-ligue1, esp-laliga, ger-bundesliga** (fino a 2026/27). Le vecchie sorgenti non sono state modificate.
- I database espongono il modello `fixtures` + `teams` + `leagues`, con ID e dati di origine PitchAPI; il nuovo lettore `betpilot_v3.sqlite_source` li usa esclusivamente in modalità **SQLite `mode=ro`**.
- I database SQLite storici non sono il formato di input completo della pipeline V3: **non possono sostituire automaticamente** gli archivi nativi GA3/GTS3, né giustificare probabilità costruite senza i relativi dati.

## Comando unico, valido per qualsiasi coppia di squadre

```sh
python -m betpilot_v3.command "BETPILOT: Inter - Milan" \
  --sqlite-root /private/pitchapi/data \
  --warehouse-root /private/pitchapi/native_daily \
  --history-root /private/pitchapi/native_history
```

Funziona anche con `BETPILOT: Juventus - Napoli`, `BETPILOT: Roma - Lazio`, `BETPILOT: Deportivo La Coruña - Osasuna` o qualsiasi altra coppia, senza ID hardcoded. **Funziona** significa che il comando interpreta le squadre ed esegue i controlli; il report completo esiste **solo** se le fonti autentiche e le quote disponibili coprono davvero l'incontro.

Se esiste un incontro futuro negli SQLite, viene verificato il nome casa/trasferta e selezionata la data UTC più vicina. Se manca, la pipeline API-Sports tenta una ricerca autenticata, limitata a un team search e ai prossimi 30 eventi di quella squadra; non usa date inventate, Google, SofaScore o altri siti. L'originale Goal Analyzer/GTS e il Bookmaker RC15 non cambiano formule.

L'API può non contenere una partita, avere una squadra con denominazione diversa o non fornire quote Bet365 autentiche. In tali casi la risposta deve restare `FIXTURE_NOT_FOUND`, `DATA_UNAVAILABLE`, `ODDS_UNAVAILABLE` o altra motivazione tecnica verificata, non un pronostico simulato.

## Accesso ai DB dalla nuova repository pubblica test1, senza alterare quella vecchia

La nuova repository è PUBBLICA, quella storica è PRIVATA. **Non copiare gli archivi SQLite in `test1`**.

Per consentire a un runner `test1` di leggere la sorgente privata, usare un *fine-grained GitHub token* con **Contents: Read** esclusivamente su `jscommessinho/BET-PILOT-Prematch-Analyzer`, registrato come segreto GitHub Actions `PITCHAPI_SOURCE_READ_TOKEN` di `test1` (non nel codice). Il job `prematch-generic-fixture-sqlite.yml` usa `betpilot_v3.private_sqlite_mount` per eseguire un *partial sparse checkout* in una directory temporanea e verifica gli SQLite in **read-only**, senza commit, push o operazioni sull'originale. Se il segreto non esiste, registra esplicitamente `PRIVATE_SQLITE_MOUNT_UNAVAILABLE`.

**Attenzione**: un runner GitHub Actions è temporaneo, quindi questo import non rappresenta un archivio durevole né completa la pipeline V3. Per risposte normalmente inferiori a 60 secondi, predisporre il database originale su un **volume persistente privato** e fornire separatamente gli archivi nativi storici. Gli archivi dei cinque principali campionati **non coprono** ogni partita del mondo. Per nuove competizioni serve la stessa fonte PitchAPI, con raccolta autorizzata e corretta provenienza.

La pipeline originale prevede in alcuni punti minimi/cutoff che restano nel modello congelato, e deve essere validata sulla reale disponibilità di dati delle altre competizioni. Nessun test su SQLite sintetico equivale a una previsione autentica. Il target reale `BETPILOT: Deportivo La Coruña - Osasuna` **non è dichiarato riuscito**.
