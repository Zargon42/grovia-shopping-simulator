# Simulatore di spesa Grovia

Un modello modificabile della disposizione di un supermercato e un simulatore Python per confrontare strategie di percorso. Il repository include un grafo del negozio ispirato a Lidl, un editor visuale, uno showcase riproducibile e i dati e grafici esportati.

[Read this README in English](README.md)

> **Ambito:** tutti i risultati in questo repository sono simulati. Il grafo del negozio, il catalogo sintetico, le liste, i percorsi, i profili degli acquirenti e l'esposizione dei prodotti freddi sono input del modello, non osservazioni di clienti Lidl reali né misurazioni della temperatura dei prodotti.

## Contenuto

- `store_sim.py` — CLI del simulatore; le interfacce esistenti per layout JSON e riga di comando sono mantenute.
- `store_editor.py` — editor Tkinter per creare e modificare layout JSON. Se non esiste un `my_store.json` locale, l'avvio apre `lidl_store_layout.json`.
- `lidl_store_layout.json` — grafo del negozio con 104 nodi, 123 archi e tre casse.
- `configs/showcase.json` — configurazione completa usata per lo showcase.
- `outputs/showcase/Lidl_store/` — prove grezze, riepiloghi, analisi di sensibilità, report, percorsi rappresentativi e grafici PNG esportati dal simulatore.

## Installazione (Python 3.10+)

Crea un ambiente virtuale e installa le dipendenze bloccate alle versioni indicate:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Su macOS o Linux, usa `python3 -m venv .venv` e `source .venv/bin/activate`. L'editor visuale richiede Tkinter, normalmente incluso nelle distribuzioni Python per Windows e macOS. Su Linux, se necessario, installa il pacchetto Tkinter della distribuzione.

Apri il layout incluso nell'editor visuale:

```text
python store_editor.py
```

Visualizza l'aiuto del simulatore:

```text
python store_sim.py --help
```

La CLI continua a supportare le opzioni esistenti `--store`, `--catalog`, `--config`, `--export-store`, le opzioni della griglia e quelle di output. Per una piccola esecuzione Lidl:

```text
python store_sim.py --store lidl_store_layout.json --n-lists 2 --out smoke-results
```

## Showcase: configurazione e rigenerazione

Lo showcase incluso è stato generato con il layout Lidl, seed **42**, **100 liste per dimensione** e liste da **6, 12, 24 e 40 articoli**. Include entrambe le ordinazioni, `random` e `grouped`: 800 liste sintetiche distinte e 5.600 righe di prova a livello strategia per sette strategie.

La configurazione completa, inclusi velocità di camminata, tempo di prelievo, mix dei prodotti, ordine dei gruppi S2, calibrazione S5, pesi dei profili S6 e intervalli di sensibilità, si trova in [`configs/showcase.json`](configs/showcase.json). Per rigenerare i risultati dalla cartella principale:

```text
python store_sim.py --store lidl_store_layout.json --config configs/showcase.json --out outputs/showcase
```

Il comando scrive in `outputs/showcase/Lidl_store/`, sostituendo i file di output con una nuova esecuzione usando seed e configurazione salvati.

## Strategie

| ID | Strategia | Comportamento del percorso |
|---|---|---|
| S0 | Ordine della lista | Visita gli articoli nell'ordine scritto, usando i percorsi più brevi tra fermate successive. |
| S1 | Tour breve, senza categorie | Ordinamento nearest-neighbor seguito da miglioramento 2-opt. È un'euristica, non garantisce il tour globalmente più breve. |
| S2 | Tour vincolato per categorie | Usa l'ordine configurato dei gruppi, nearest-neighbor e miglioramento 2-opt all'interno di ogni gruppo. |
| S3 | Percorso fisso nel negozio | Segue il percorso a serpentina del layout e preleva gli articoli della lista man mano che li incontra. |
| S4 | Percorso con prodotti abituali e ricerca per corsia | Visita prima prodotti sintetici abituali e poi cerca nelle corsie pertinenti. |
| S5 | Escursioni calibrate sulla deviazione di percorso | Aggiunge escursioni nelle corsie a un ordine d'acquisto simile a S1 per raggiungere una quota aggregata di deviazione. |
| S6 | Acquirente stocastico | Estrae un profilo comportamentale concentrato, abituale o esplorativo e sceglie gli articoli probabilisticamente, con possibili deviazioni di esplorazione. |

Sono euristiche di percorso e modelli di scenario, non previsioni convalidate del comportamento reale. In particolare, S1 non è un risolutore esatto del problema del commesso viaggiatore.

## Risultati simulati e interpretazione

La tabella mostra le mediane per **100 liste casuali da 40 articoli**. “Freddi” indica il tempo medio nel carrello per gli articoli freschi e surgelati in ciascun viaggio, calcolato dalla velocità simulata e dal tempo di prelievo; “surgelati” è la media corrispondente per i soli surgelati. Sono risultati simulati di percorso ed esposizione, non misurazioni della temperatura né della sicurezza alimentare.

| Strategia | Distanza (m) | Tempo articoli freddi (min) | Tempo surgelati (min) |
|---|---:|---:|---:|
| S0 | 936,0 | 15,26 | 15,39 |
| S1 | 234,0 | 10,33 | 7,82 |
| S2 | 369,0 | 5,12 | 1,71 |
| S3 | 453,0 | 8,82 | 10,74 |
| S4 | 508,5 | 13,66 | 9,51 |
| S5 | 750,0 | 17,48 | 13,58 |
| S6 | 495,0 | 11,31 | 10,79 |

Su queste liste casuali da 40 articoli, S1 ha la distanza mediana più bassa (234 m). L'ordine per categorie di S2 porta la distanza mediana a 369 m, riducendo il tempo mediano degli articoli freddi da 10,33 a 5,12 minuti e quello dei surgelati da 7,82 a 1,71 minuti. È il principale compromesso tra distanza ed esposizione al freddo in questo scenario: ritardare le categorie fresche e surgelate può ridurne il tempo nel carrello, ma non minimizza la distanza totale. In questa esecuzione le escursioni aggregate di S5 sono più lunghe (mediana 750 m) e hanno l'esposizione mediana più alta (17,48 minuti). Sono risultati specifici dello scenario; i file completi comprendono tutte e quattro le dimensioni e i due tipi di lista.

### Distanza per strategia

[![Liste raggruppate e casuali: confronto tra distanza mediana e intervallo interquartile delle strategie S0–S6 per liste di 6, 12, 24 e 40 articoli.](outputs/showcase/Lidl_store/distance_by_strategy.png)](outputs/showcase/Lidl_store/distance_by_strategy.png)

*Figura 1. Distanza simulata per strategia e dimensione della lista, separata per liste raggruppate e casuali. Impostazioni: layout Lidl, seed 42, 100 liste per dimensione e tipo, dimensioni 6/12/24/40; le barre mostrano mediana e intervallo interquartile. S1 è il più breve nei confronti mostrati; S2 scambia parte della distanza con un percorso ordinato per categorie.*

[Apri il grafico della distanza a dimensione piena](outputs/showcase/Lidl_store/distance_by_strategy.png).

### Distanza ed esposizione dei prodotti freddi

[![Per liste casuali, grafici a dispersione confrontano distanza mediana e tempo medio mediano nel carrello per prodotti freschi e surgelati nelle strategie S0–S6, a quattro dimensioni di lista, con intervalli interquartili.](outputs/showcase/Lidl_store/tradeoff_distance_vs_cold.png)](outputs/showcase/Lidl_store/tradeoff_distance_vs_cold.png)

*Figura 2. Distanza simulata e tempo medio degli articoli freddi nel carrello per liste casuali. Impostazioni: stesso layout Lidl, seed, numero di liste e dimensioni della Figura 1; punti e barre indicano mediane e intervalli interquartili. Ogni pannello ha una scala propria. Il vincolo per categorie di S2 riduce l'esposizione al freddo rispetto a S1, al costo di un percorso più lungo.*

[Apri il grafico del compromesso a dimensione piena](outputs/showcase/Lidl_store/tradeoff_distance_vs_cold.png).

### Percorsi rappresentativi sulla mappa

[![Sette percorsi simulati per la stessa lista casuale rappresentativa da 24 articoli, disegnati sulla mappa Lidl in un pannello per ciascuna strategia S0–S6.](outputs/showcase/Lidl_store/representative_trips.png)](outputs/showcase/Lidl_store/representative_trips.png)

*Figura 3. Percorsi fisici esportati dal simulatore per una lista casuale rappresentativa da 24 articoli (seed 42, selezione 0), tracciati sul grafo del negozio incluso. I sette pannelli usano la stessa lista; lunghezza del percorso e tempo totale stimato sono indicati in ciascun pannello. L'esempio illustra le differenze tra percorsi: non è una mediana né un'affermazione di tipicità.*

[Apri il grafico dei percorsi a dimensione piena](outputs/showcase/Lidl_store/representative_trips.png) · [Dati JSON del percorso](outputs/showcase/Lidl_store/representative_trip.json) · [Percorso in formato testo](outputs/showcase/Lidl_store/representative_trip.txt).

### Download dei risultati

- [CSV delle prove (5.600 righe)](outputs/showcase/Lidl_store/trials.csv)
- [CSV riepilogativo (mediane e intervalli interquartili)](outputs/showcase/Lidl_store/summary.csv)
- [CSV dell'analisi di sensibilità](outputs/showcase/Lidl_store/sensitivity.csv)
- [Report del simulatore](outputs/showcase/Lidl_store/report.txt)
- [Grafico del layout](outputs/showcase/Lidl_store/layout.png) · [Mappe di calore delle visite ai nodi](outputs/showcase/Lidl_store/heatmaps.png)

## Ipotesi e limiti del modello

- Il layout Lidl è un'astrazione a grafo modificabile: nodi e archi rappresentano posizioni e segmenti percorribili modellati, non una planimetria ufficiale o rilevata sul campo.
- Catalogo, mix delle categorie, prodotti abituali e liste sono sintetici. Una lista `grouped` è ordinata per categoria; una lista `random` è mescolata.
- La distanza è misurata lungo gli archi pesati. S1/S2 usano euristiche nearest-neighbor e 2-opt: “tour breve” è il nome di una strategia, non una prova di ottimalità.
- L'esposizione al freddo è una stima del tempo nel carrello derivata dall'avanzamento sul percorso, dalla velocità e dal tempo di prelievo. La simulazione non modella temperature dei prodotti, refrigerazione, condizioni ambientali o sicurezza alimentare.
- **Calibrazione aggregata S5:** la configurazione mira a una quota di deviazione dal percorso del 69%, usando una quota media aggregata di decomposizione dei percorsi riportata in [SSRN 942570](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=942570). La quota viene tradotta in escursioni simulate nelle corsie; non è una stima di singoli viaggi né è adattata a questo layout Lidl.
- **Profili comportamentali ipotizzati in S6:** i pesi dei profili concentrato/abituale/esplorativo (55%/30%/15%) e le intensità comportamentali sono ipotesi di scenario, non stime della ricerca citata. I profili influenzano la scelta degli articoli e le possibili deviazioni; non sono modellati acquisti incidentali o valutazioni a scaffale.

## Licenza

Non è inclusa una licenza di riutilizzo. Tutti i risultati e le immagini sono generati dal simulatore con la configurazione salvata.
