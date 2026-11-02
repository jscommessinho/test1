# BET PILOT | Goal Analyzer 3.0 — Qualsiasi partita

## Disponibile ora

L'interfaccia standard è **integration/ga3_fast.py**. Una sola implementazione,
senza creare nuovi file o workflow per ogni incontro.

- Legge il database locale persistente PitchAPI (SQLite, **sola lettura**).
- Scopre automaticamente i campionati che possiedono archivi in
  `data/<slug>/*.sqlite`, anche se non erano presenti quando il codice è stato
  creato. Nessuna lista fissa di cinque campionati.
- Riconosce campionati per *slug*, nome o ID ufficiale PitchAPI, solo con
  corrispondenza univoca. Risolve le squadre con ID provider o nome esatto.
- Calcola 1X2, GG/NG, Over/Under, risultati esatti.
- Utilizza **solo gol segnati/subiti**, mai xG/xGA. Usa esattamente il
  pacchetto matematico Goal Analyzer 3.0 RC2.post2 approvato, con hash SHA-256
  verificati. Nessun minimo di 150/25 partite, attesa +3 giorni o cutoff
  mensile. Non modifica i parametri matematici approvati.
- Se la competizione non ha ancora partite registrate, restituisce
  `WAITING_FOR_DATABASE`, senza pronostici inventati. Verrà identificata
  automaticamente quando i suoi archivi saranno presenti. Il caricamento
  di nuovi campionati è responsabilità del **collector**, non del modello.

## Modalità 1 — GitHub Actions (non richiede installazioni personali)

Apri il workflow:

https://github.com/jscommessinho/BET-PILOT-Prematch-Analyzer/actions/workflows/ga3-any-match.yml

Scegli **Run workflow** e compila gli input:
- `league`: slug esatto della competizione nel database, ad esempio `ita-seriea`
- `home`: squadra casa, ad esempio `Juventus`
- `away`: squadra ospite, ad esempio `Napoli`
- `kickoff`: **opzionale**, normalmente lascia vuoto. Si usa soltanto
  se serve ricostruire una previsione riferita al calcio d'inizio ufficiale.
- `as_of`: **lascia vuoto** per un pronostico prematch attuale. Inserisci
  una data precedente alla partita solo per un test prematch retrospettivo.

Il workflow recupera dal ramo privato `pitchapi-warehouse` **solo la competizione
richiesta**, non tutte le altre; esegue il motore, pubblica il riepilogo nella
pagina del job e allega `ga3-result.json`. Non effettua chiamate PitchAPI,
non utilizza chiavi segrete e non modifica il database.

## Modalità 2 — Locale, più veloce dopo la prima sincronizzazione

Prerequisiti: Python 3.11+, NumPy e SciPy.

Scarica il repository del codice, e il ramo **pitchapi-warehouse** in una
directory persistente accessibile in lettura. Con Git:

```bash
git clone https://github.com/jscommessinho/BET-PILOT-Prematch-Analyzer.git betpilot-engine
git clone --branch pitchapi-warehouse --single-branch \
  https://github.com/jscommessinho/BET-PILOT-Prematch-Analyzer.git pitchapi-warehouse
python -m pip install numpy scipy
cd betpilot-engine
python -m integration.ga3_fast --warehouse ../pitchapi-warehouse --list-leagues
python -m integration.ga3_fast --warehouse ../pitchapi-warehouse --league ita-seriea --list-teams
python -m integration.ga3_fast --warehouse ../pitchapi-warehouse \
  --league ita-seriea --home Juventus --away Napoli
```

Queste due directory vengono mantenute e riutilizzate. Quando il download
PitchAPI aggiunge nuove partite, aggiorna **solo** il checkout del warehouse,
per esempio usando `git -C ../pitchapi-warehouse pull --ff-only`; poi il
pronostico legge immediatamente i nuovi dati. Non occorre creare procedure
specifiche per singole partite o reinstallare il motore.

Il repository privato richiede ovviamente accesso GitHub autorizzato. Le
dimensioni dei file SQLite sono rilevanti: la prima clonazione può richiedere
tempo; dopo conviene usare una copia locale stabile. In alternativa GitHub
Actions usa sparse checkout di una sola lega.

## Disciplina temporale

Nel comando veloce `--kickoff` non è necessario: `as_of` è il momento
dell'analisi. Il sistema restituisce `CURRENT_DATA_SNAPSHOT`, senza asserire
che la partita sia ancora da giocare. Se si specifica invece un kickoff, il
sistema effettua i controlli temporali prematch.

Per una partita futura con kickoff specificato, `as_of` è il momento in cui viene lanciata
l'analisi, non la data futura del calcio d'inizio. Sono usate solo partite
con data precedente a `as_of`. Per una partita già iniziata o conclusa, il
sistema **richiede esplicitamente** `--as-of` precedente al kickoff e segnala
la modalità `RETROSPECTIVE_PREMATCH`. Non etichetta prematch un risultato
elaborato dopo il calcio d'inizio.

Una previsione retrospettiva è una *simulazione*, non prova di effettiva
disponibilità di ogni statistica all'epoca della partita. I risultati vanno
trattati come ricerca fino a una validazione predittiva appropriata.

## Stato dell'espansione campionati

Il collector automatico PitchAPI configurato precedentemente acquisisce
**solo** le cinque leghe richieste inizialmente:
Premier League, Serie A, La Liga, Bundesliga, Ligue 1. **Questo aggiornamento
non lo amplia né avvia nuovi scaricamenti.**

Quando una nuova competizione viene realmente aggiunta al warehouse,
Goal Analyzer la riconosce automaticamente. Non deve essere aggiunta
manualmente all'elenco del motore.

## Test

- Pacchetto originale verificato con SHA-256, senza alterazioni.
- Test d'integrazione con 75 incontri sintetici in una nuova competizione
  non preconfigurata: esecuzione matematica reale superata.
- Test per partita già iniziata (niente falso prematch): superato.
- Test generale senza dipendenze in CI repository: superato (test matematici
  eseguiti nel job che installa SciPy).

**Nessuna modifica** a BUCO NERO, LIVEPICK, altri Analyzer o al collector.

## Prestazioni reali misurate

Su database PitchAPI originale della Serie A (1.997 risultati validi e sei
stagioni), esecuzione Goal Analyzer 3.0 invariato con letture SQLite ottimizzate:
**933,72 ms** per caricamento, fitting, calcolo probabilità e serializzazione
sul runner GitHub. Il job GitHub Actions completo ha richiesto circa **22 secondi**
fra avvio, checkout e librerie. Questi tempi non garantiscono identica durata
per ogni ambiente e non includono il tempo della conversazione ChatGPT.

GTS3, BUCO NERO, LIVEPICK e il collector PitchAPI non sono stati modificati.
