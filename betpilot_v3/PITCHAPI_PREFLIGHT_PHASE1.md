# PREMATCH V3 — Fase 1: preflight + publisher nativo esterno (RESEARCH_ONLY)

## Risultato tecnico verificabile

Il repository aveva già il collector `betpilot/pitchapi_daily_sync.py` e il branch privato `pitchapi-daily-data`, oltre alla catena `betpilot_v3/full_analysis.py` per **sei JSON nativi già pronti**.

Il controllo preliminare aggiunge `betpilot_v3/pitchapi_preflight.py`:
- riutilizza l'API Client PitchAPI già autorizzata, con **massimo 3 GET** per una partita (data, dettaglio, catalogo);
- richiede identità univoca casa/trasferta, stagione verificata dal provider, stessa lega/ID squadre, kickoff con timezone e target prematch;
- legge gli archivi locali `warehouse/daily/YYYY/MM/*.json.gz` **senza modificarli**, e può includere con `--league-archive` gli archivi storici **già prodotti** dai workflow per competizione, verificandone identità e digest tramite il validatore originale;
- accetta soltanto partite storiche terminate con QA `PASS_INTERNAL`, xG e tiri validi, acquisizione documentata prima del cutoff e kickoff precedente a tale acquisizione;
- blocca l'analisi quando il campione GTS non arriva a 10 gare per squadra e 4 gare nella sede pertinente, quando la partita è ambigua, i dati sono posteriori al cutoff, o l'identità è insufficiente;
- crea `PITCHAPI_FIXTURE_CANDIDATE.json`, `PITCHAPI_ASOF_HISTORY.json`, `PITCHAPI_PREFLIGHT_STATUS.json`, con digest SHA-256 degli output positivi.

L'output positivo porta **sempre** `full_analysis_ready=false`, `ga3_native_executed=false` e `gts3_native_executed=false`. **Non** crea `match.json`, `pitchapi_identity.json`, `ga3_native_output.json`, `gts3_native_output.json` né `GTS3_FROZEN_MODEL_PARAMS.json`: questi devono rispettare i contratti effettivi originali, e non si possono imitare con proxy sintetici.

## Uso controllato

Preparare una copia **di sola lettura** della cartella `warehouse` dal branch privato `pitchapi-daily-data`; NON avviare un secondo collector. Sul runner autorizzato, con `PITCHAPI_API_KEY` già configurata:

```bash
python -m betpilot_v3.pitchapi_preflight \
  --request "FULL ANALYSIS: Inter - Juventus" \
  --day-utc 2026-11-03 \
  --country-code ITA \
  --competition "Serie A" \
  --warehouse-root /secure/read_only_warehouse \
  --league-archive /secure/existing_archives/seriea.json \
  --output-dir /secure/preflight/inter_juventus
```

Data e squadre nell'esempio sono **soltanto illustrative**, non una dichiarazione che la partita si giochi davvero. L'opzione `--cutoff` è facoltativa; se omessa, usa l'istante di completamento dell'acquisizione dell'identità. Un cutoff storico antecedente alla verifica corrente è bloccato: questo componente non può ricreare una cattura prematch già passata.

Senza `PITCHAPI_API_KEY` non effettua richieste e produce `NO_BET_SOURCE_PREFLIGHT_BLOCKED`. Senza storia sufficiente nel branch quotidiano e negli eventuali archivi storici già acquisiti resta `HISTORY_INSUFFICIENT`. Non scarica dati storici, non utilizza FootyStats e non cambia i workflow preesistenti.

## Esecuzione degli Analyzer originali (senza alterare i motori)

L'audit del codice congelato ha accertato che Goal Analyzer 3.0 RC2 dispone già di un CLI originale (goal_analyzer3.cli predict) che legge archivi JSON per lega con league pin, data, risultati, ID squadra, digest e QA. Il suo loader richiede un lag storico prudenziale di **3 giorni**; gli xG sperimentali restano disabilitati.

Il nuovo adapter esterno **betpilot_v3/ga3_original_adapter.py** invoca direttamente quel CLI dopo controllo della manifest SHA256 del GA3, digest degli input, archivio originale e timestamp antecedenti al cutoff. Produce GA3_RC2_ORIGINAL_PREDICTION_RESEARCH.json e uno status. Non reinventa calcoli e **non** trasforma un output di ricerca nel falso ga3_native_output.json richiesto da MA3.

GTS3 RC1 ha già un parser nativo di statistiche team (/v1/matches/{id}/stats, periodo All) e il CLI originale gts3.score; richiede expected_goals e total_shots delle statistiche di squadra, **non** xG sommati dei singoli tiri. Il nuovo **betpilot_v3/gts3_original_adapter.py** legge soltanto i raw stats già presenti negli archivi giornalieri prima del cutoff, verifica con il parser frozen, prepara un ZIP input distinto, richiede un periodo originale dei parametri congelati (senza ricalibrare) e invoca l'esatto score originale. Produce GTS3_RC1_ORIGINAL_SCORE_RESEARCH.json soltanto dopo i controlli; rimane diagnostico e non altera le probabilità GA3.

Sintassi su macchina autorizzata con pacchetti frozen originali già estratti e verificati (esempio non validato come partita reale):

~~~bash
python -m betpilot_v3.ga3_original_adapter \
  --preflight-dir /secure/preflight/inter_juventus \
  --archive /secure/existing_archives/seriea.json \
  --ga3-original-root /secure/frozen/GA3_PITCHAPI_ONLY_RC2_2026-11-01 \
  --output-dir /secure/ga3/inter_juventus

python -m betpilot_v3.gts3_original_adapter \
  --preflight-dir /secure/preflight/inter_juventus \
  --warehouse-root /secure/read_only_warehouse \
  --gts3-original-root /secure/frozen/BETPILOT_GTS3_RC1 \
  --period PERIODO_ORIGINALE_VERIFICATO \
  --output-dir /secure/gts3/inter_juventus
~~~

Le ultime giornate del warehouse mondiale **non sono lo storico completo**, e i backfill storici aggregati di xG dei tiri **non** conservano i raw team stats richiesti da GTS3. L'esecuzione su dati veri resta non validata finché non sono verificabili fixture future, storico per le squadre, periodo model e timestamp prima del cutoff. Nessun secondo collector o workflow è stato creato.

**Le previsioni originali GA3 e gli score originali GTS3 non sono ancora esportazioni native MA3 V3**: non costruire o rinominare artificiosamente ga3_native_output.json, gts3_native_output.json o GTS3_FROZEN_MODEL_PARAMS.json.

## Limiti ancora aperti per la FASE 1

1. **Risolto nel nuovo orchestratore**: gli adapter e la pubblicazione dei cinque file sono implementati e verificati con i parser MA3 originali su output sintetici originali. Vedere `PHASE1_NATIVE_FINAL_REPORT_2026-11-01.md` per comando unico, SHA, risultati e limiti.
2. Verificare la disponibilità per fixture reali future di storico completo prima del cutoff; **GTS3 richiede raw team stats** e non accetta shot-xG come sostituto.
3. Verificare stagione, league pin, IDs e periodo congelato di calibrazione GTS3, senza ipotizzare che il catalogo 77 assicuri copertura.
4. Eseguire end-to-end su una partita vera con dati effettivamente acquisiti prima del cutoff, SHA nativi e controlli MA3/BA3; OOS predittiva resta distinta.
5. Il file bookmaker RC15_EVENT_INPUT.json è fase separata e non viene fabbricato.

L'integrazione degli export è implementata; la FASE 1 **non è validata su partita reale** per insufficienza dello storico GTS raw disponibile. Stato: `RESEARCH_ONLY / NO BET`. Nessuna pianificazione nuova e nessuna modifica ai modelli congelati, segreti, branch dati o automazioni.

## Test

```bash
python -m unittest betpilot_v3.tests.test_pitchapi_preflight -v
python -m unittest betpilot_v3.tests.test_ga3_original_adapter -v
python -m unittest betpilot_v3.tests.test_gts3_original_adapter -v
python -m unittest betpilot_v3.tests.test_pitchapi_native_phase1 -v
python betpilot_v3/run_replay.py --test
```

I nuovi test usano fixture/statistiche sintetiche e invocazioni subprocess simulate; nel V3 CI sono verificati manifest e parser originali, ma non costituiscono esecuzione predittiva su dati reali. La seconda suite verifica l'integrazione congelata precedente ma non prova alimentazione predittiva su dati reali.
