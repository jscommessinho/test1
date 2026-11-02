# BETPILOT | PREMATCH V3 — FASE 1, verifica tecnica conclusiva

**Verdetto sul branch:** esportazione native V3 completa a livello software e verificata tramite i **parser originali MA3 su artefatti sintetici originali**; esecuzione su una partita futura reale non dimostrata. RESEARCH_ONLY / NO BET. Nessun merge, nessuna schedulazione.

## Sequenza operativa

1. Il singolo comando `betpilot_v3/pitchapi_native_phase1.py` verifica gli ZIP frozen e i manifest SHA-256 originali di MA3, BA3, GA3 RC2 e GTS3 RC1.
2. Attraverso il Client PitchAPI originale esegue **al massimo tre GET** autenticati per data, dettaglio partita e catalogo. Verifica identità univoca, stagione, venue, kickoff UTC e provenienza; non scarica storico né quote.
3. Scopre l'archivio storico originale della lega da `--history-root`, controlla il `as_of`, digest record, QA e disponibilità osservata prima del cutoff. Legge il warehouse daily esistente, mai lo modifica.
4. Seleziona automaticamente esattamente un periodo GTS3 frozen ODH associato alla stagione e con data finale di training precedente al target. Nessuna ricalibrazione o selezione in base al risultato.
5. Esegue i comandi originali, **immutati**, `goal_analyzer3.cli predict` e `gts3.score`. GTS usa solo `/stats` periodo `All`, mai xG da singoli tiri. Se la stagione daily manca, può usare solo un altro record originale con identità perfettamente uguale (match/date/club/score); altrimenti fallisce.
6. Copia byte-per-byte gli output originali GA3/GTS3 e i parametri GTS3 frozen nel bundle; ricava `match.json` e `pitchapi_identity.json` dal fixture PitchAPI autenticato. Non inventa matrice, probabilità o valori quantitativi.
7. Esegue *realmente* `ma3.native_exports.export_ga3`, `ma3.native_exports.export_gts3` e `ma3.runtime.verify_source_file` dalle sorgenti originali per verificare la parity dei 47 mercati, il ricalcolo esatto del GTS con ODH, gli ID, il cutoff e il SHA pinned dei parametri. Solo dopo entrambi i PASS pubblica in modo atomico cinque JSON e un manifest SHA256.
8. In ogni altro caso produce `PITCHAPI_PHASE1_STATUS.json` con `NO_BET_NATIVE_INPUTS_BLOCKED` e causa; non pubblica i cinque JSON come se fossero verificati.

## Invocazione unica, con archivi autentici preesistenti

```bash
python -m betpilot_v3.pitchapi_native_phase1 \
  --request "FULL ANALYSIS: Inter - Juventus" \
  --day-utc 2026-11-03 \
  --country-code ITA \
  --competition "Serie A" \
  --warehouse-root /secure/read_only_worldwide_warehouse \
  --history-root /secure/existing_per_league_JSON_archives \
  --output-dir /secure/output/native_phase1
```

La data e le squadre sono solo sintassi illustrativa. `PITCHAPI_API_KEY` deve essere già autorizzata dal runner; il codice non legge altri provider né crea richieste a pagamento fuori dal budget client. Nessun passo manuale intermedio, salvo disponibilità dei due archivi locali già acquisiti.

## Cinque file e provenienza

| File | Origine | Parser originale |
|---|---|---|
| `match.json` | Partita futura PitchAPI verificata; kickoff, nomi, league, cutoff | `ma3.native_exports._binding` |
| `pitchapi_identity.json` | Stessa partita, league/team/season ID PitchAPI, riferimento SHA256 e provenance provider | `ma3.native_exports._binding` |
| `ga3_native_output.json` | Copia immutata dell'output **autentico** del CLI congelato GA3 RC2 | `ma3.native_exports.export_ga3` (matrice score esatti + 47 mercati) |
| `gts3_native_output.json` | Copia immutata dello score del CLI congelato GTS3 RC1 | `ma3.native_exports.export_gts3` (ODH, intensità e score, solo diagnostico) |
| `GTS3_FROZEN_MODEL_PARAMS.json` | Copia byte-identica di `reports/selected_model_parameters.json` del pacchetto congelato GTS3 | SHA256 fisso MA3 `7a87f0a372e895aa4cf8916f01575471937ae577c6719194cd4c0b0ac9515de6` |

**Nota sull'identità:** `EXTERNAL_VERIFIED` denota controllo attraverso endpoint PitchAPI autenticati, NON una riconciliazione bookmaker indipendente. Il manifest esplicita `independent_bookmaker_identity_reconciled=false`. Non è un certificato point-in-time di pubblicazione xG.

## Evidenze realmente disponibili

- CI V3 [run 41291874477](https://github.com/jscommessinho/betpilot-prematch-analyzer/actions/runs/41291874477): test di export a cinque file con parser MA3 originale sulla fixture **sintetica** del pacchetto originale; test SHA, presenza dei parametri, identità e fail-closed. Non esegue l'intero flusso reale.
- CI generale [run 41291874466](https://github.com/jscommessinho/betpilot-prematch-analyzer/actions/runs/41291874466) e verifica quote [run 41291874469](https://github.com/jscommessinho/betpilot-prematch-analyzer/actions/runs/41291874469).
- Audit PitchAPI reale e read-only [run 41291587469](https://github.com/jscommessinho/betpilot-prematch-analyzer/actions/runs/41291587469), per-leghe e contatori riportati in `PITCHAPI_DAILY_77_GTS3_AUDIT_2026-11-01.md`.

## Blocchi oggettivi

- Su cinque partizioni giornaliere (2026-10-28…2026-11-01) sono salvati 328 record di 64/77 leghe, 294 candidati GTS3 stats All + PASS. **Tutti e 328 mancano della stagione nel fixture daily.** Tra 575 coppie lega/squadra con candidati lo storico massimo è due gare, sotto 10 match per squadra e quattro nel venue; nessuna squadra soddisfa i minimi. Le altre **13 leghe** non compaiono in questo ristretto intervallo, senza che ciò dimostri assenza dal provider. Per il dettaglio completo vedere l'audit.
- Gli archivi GA3 per-league disponibili attraverso altri workflow possono avere più stagioni, ma i loro aggregate shot-xG **non sostituiscono** gli originali raw team stats GTS3. Gli artefatti storici Actions separati non sono stati inventariati da questa esecuzione.
- Al momento non è verificabile una partita futura dotata di entrambe le fonti pre-cutoff originali, stagione idonea, GTS3 frozen period, e osservazioni sufficienti. Non è quindi stata eseguita una vera previsione end-to-end usando questo nuovo orchestratore.
- Gli input bookmaker `RC15_EVENT_INPUT.json`, l'esecuzione MA3+BA3 completa con quote genuine, e la validazione predittiva OOS sono gate separati. NON trattarli come completati.

**Verdetto operativo per l'utente:** VERDETTO B — integrazione software degli export nativi completata e testata attraverso i parser originali, test reale pendente perché i dati daily esaminati non bastano. Nessuna autorizzazione a puntate.
