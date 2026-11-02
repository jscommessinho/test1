# BETPILOT | PREMATCH — Bookmaker Analyzer 2.0
## RC15 — Protocollo operativo consolidato

**Data:** 30 ottobre 2026  
**Tipo di rilascio:** integrazione e reporting, senza modifica ai modelli statistici RC9/RC12/RC13/RC14.  
**Stato:** operativo come analizzatore probabilistico e diagnostico; Confidence storica sperimentale, non certificazione predittiva/finanziaria.

### A. Scopo
Interpretare in modo sistematico le **quote prematch di Bet365**, preservando la gerarchia dei mercati, esplicitando la distribuzione di possibili risultati e segnalando incongruenze fra le linee. L'obiettivo principale non è identificare una quota con maggior EV, né stimare l'esposizione del bookmaker (che non è osservabile dalle quote).

### B. Input

**Sette quote obbligatorie**: `q1` (casa), `qx` (pareggio), `q2` (trasferta), `qo` (Over 2.5), `qu` (Under 2.5), `qgg` (Goal/Goal), `qng` (No Goal). Tutte **decimal odds** della medesima partita e fonte Bet365. Campionato e squadre sono obbligatori; `bookmaker=Bet365`. La data è consigliata. Le colonne `[ODDS - PICKED]` live non sono ammesse come input prematch.

**Facoltativi**: Over/Under 1.5 (`over15`, `under15`), Over/Under 3.5 (`over35`, `under35`) e coppia Asian Handicap completa (`ah_line_home`, `ah_home_odds`, `ah_away_odds`). Ogni coppia deve essere completa; una singola quota orfana comporta scarto della riga.

La provenienza Bet365/closing dell'archivio LivePick è **attestata dall'utente**. Il dato esportato non registra timestamp autonomi delle singole quote. Per le nuove analisi, segnalare esplicitamente se sono quote closing o semplicemente rilevate prima del kickoff.

### C. Flusso unico per ciascuna partita

1. **Data Quality:** controllare fonte, campionato, quote minime, overround, coppie facoltative e coerenza dei mercati. Se BISE non è identificabile, **non** forzare stime con fallback presentate come BISE.
2. **Bookmaker Probability Intelligence (RC11):** eliminare il margine in ogni mercato tramite normalizzazione proporzionale, mantenendo separate le famiglie 1X2, O/U2.5 e GG/NG. Visualizzare la gerarchia degli esiti *all'interno di ogni famiglia*. Non dedurre le responsabilità finanziarie di Bet365.
3. **RC12 Confidence sperimentale:** applicare solo i correttivi effettivamente selezionati nello storico (1X2 OTHER ove previsto), mantenendo diretti gli altri mercati secondo protocollo. Mostrare probabilità, intensità `probabilità × 10`, e consistenza del campione nella fascia storica. **Intensità ≠ attendibilità indipendente.**
4. **BISE RC9:** calcolare una sola distribuzione Poisson/Dixon–Coles di risultati coerente con i prezzi 1X2/O/U2.5 *non calibrati*; derivarne i gol attesi, il GG teorico, gli scorelines, le dodici celle esclusive 1/X/2 × GG/NG × O/U2.5 e le combinazioni sovrapposte.
5. **Scenario Intelligence RC13:** ordinare solo le **dodici celle mutuamente esclusive** quando si cercano scenari alternativi. Presentare le combinazioni sovrapposte in una tabella distinta e avvertire che non sono sommabili. I punteggi esatti non sono calibrati singolarmente.
6. **Market Stability RC14:** confrontare gli O/U1.5 e O/U3.5 diretti (se disponibili) con gli O/U ricavati dalla matrice BISE; soglie storiche p95 separate BIG5/OTHER derivate da 2020–2024. Le soglie indicano rarità dello **scostamento tra prezzi**, non probabilità di successo. Verificare O1.5 ≥ O2.5 ≥ O3.5 e GG ≤ O1.5, con la tolleranza RC14.
7. **Asian Handicap:** rappresentare vincita/push/perdita in frazioni ponderate; considerare correttamente mezza vincita e mezza perdita. La quota `1+L/W` è soltanto teorica condizionata alla matrice BISE; nessun edge certificato.
8. **Sintesi finale:** favorito Bet365 in 1X2, preferenza Over/Under e GG/NG, scenari esclusivi più probabili, risultati esatti strutturali, eventuali anomalie. **Non sommare score di Confidence e non sommare segnali correlati.**

### D. Ordine di presentazione del report

1. Identità evento, fonte e integrità dell'input.
2. Quote depurate dal margine e probabilità sperimentali dei mercati singoli (con supporto storico per fascia).
3. Ricostruzione BISE: gol attesi, dodici scenari mutuamente esclusivi (primi sei), combinazioni sovrapposte e punteggi esatti (primi cinque).
4. Discrepanze, copertura mercati O/U supplementari, vincoli matematici e handicap asiatici opzionali.
5. Interpretazione descrittiva, limiti, informazioni mancanti e **nessuna promozione automatica a una puntata**.

### E. Confidence: cosa è / cosa non è

- La **probabilità** di un esito non coincide con la **fiducia nella calibrazione di quella probabilità**.
- L'**intensità 0–10** è semplicemente `10 × p`. Non può essere chiamata un punteggio indipendente di sicurezza.
- La fascia storica è un riferimento aggregato, non una garanzia per il singolo match. BIG5 hanno spesso campioni molto più piccoli delle leghe OTHER.
- Le stime singolo-mercato RC12 possono essere calibrate mentre BISE resta una matrice strutturale derivata dalle quote originali: è errato presentarle come una distribuzione congiunta già calibrata.
- Le quote supplementari provengono dallo **stesso bookmaker**; la loro concordanza non costituisce una conferma statistica indipendente.
- Risultati esatti, combo e handicap: solo probabilità strutturali, senza certificazione predittiva empirica specifica.

### F. Evidenza disponibile e limiti

Il database storico checkpoint RC8 contiene **512.420 previsioni ricostruite** da record di LivePick, quindi selezionate tra incontri presenti al minuto di osservazione della piattaforma (non un campione di tutte le partite). Gli studi retrospettivi hanno rilevato una differenza molto piccola fra BISE e il bookmaker calibrato, senza una prova robusta trasferibile alle BIG5 e senza dimostrazione di ROI. I dati 2025–2026 sono già stati esaminati ripetutamente: continuare ad ottimizzare sul medesimo periodo può produrre sovradattamento. La **validazione prospettica resta sospesa su richiesta dell'utente** e non deve essere dichiarata conclusa.

### G. Integrità e riproducibilità

- SHA256 del BISE RC9 congelato: `666af5396bc760dd7b7296ea028f5aeaef8ecd4eb123fd56277f4764232cd8df`.
- RC15 è un **reporting layer** che chiama RC14→RC13→RC12→RC11→BISE, senza cambiare i coefficienti.
- Report prodotti: JSON strutturato, Markdown per lettura, HTML statico offline. Il manifest registra SHA256 dell'input e del modello.
- Test: 16 nuovi test RC15, 16 regressioni RC14 e 24 regressioni RC13, **56 su 56 superati** il 30 ottobre 2026.

### H. Convenzione in chat

Esempio input testuale:

```
Bookmaker Analysis: Squadra Casa – Squadra Ospite
Competizione: Italy Serie A
Bet365 prematch:
1 2.10 | X 3.40 | 2 3.60
Over 2.5 1.85 | Under 2.5 1.95
GG 1.75 | NG 2.05
Opzionali: Over 1.5, Over 3.5, handicap asiatico
```

Le quote sopra sono **puramente illustrative**, non collegate a una partita reale. Nella chat dedicata alle singole partite, il protocollo RC15 può essere richiesto con `Bookmaker Analysis` o come componente del futuro `Full Analysis`; non deve sovrapporsi a Goal Analyzer o Market Analyzer.

**Versioning:** `RC15_FINAL_REPORT_V1`. Modifiche a formule, nuovi mercati strutturali o nuovi coefficienti richiedono una versione esplicita e test di regressione.
