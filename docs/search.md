# Search — guida completa ai tipi di ricerca

> Documento **specifico** sulla ricerca. Per popolare il `config.yaml` di un'entità in
> generale vedi [configuration.md](configuration.md); qui si entra nel dettaglio di
> **come** cerca smart-search, **cosa copre** ogni modalità e **come estenderla**.

smart-search espone un solo endpoint di ricerca — `GET /search` — ma con **quattro
modalità** (`search_mode`) che lavorano in modo profondamente diverso. Capire quale usare
(e cosa sa/non sa fare ognuna) è la differenza tra una ricerca utile e una frustrante.

---

## I due assi della ricerca

Ogni modalità si colloca su due assi:

- **Keyword vs Semantica.** Una ricerca *keyword* trova i documenti che contengono le
  *parole* della query (eventualmente con stemming/fuzzy). Una ricerca *semantica* trova i
  documenti che hanno lo stesso *significato*, anche senza parole in comune.
- **Richiede o no l'embedder (Ollama).** Le modalità semantiche embeddano la query a
  runtime → serve Ollama raggiungibile. Le modalità keyword no → funzionano anche a Ollama
  spento.

| Modalità | Asse | Embedder (Ollama)? |
|---|---|---|
| `hybrid` | keyword **+** semantica | **sì** |
| `vector` | semantica pura | **sì** |
| `bm25` | keyword (ranking IDF) | no |
| `fts` | keyword (coverage + frase) | no |

> Vincolo importante: l'embedder usato a **query-time** deve essere lo **stesso** usato per
> indicizzare i documenti (stesso spazio vettoriale e dimensioni). Non si può "indicizzare
> con OpenAI e cercare con Ollama". Per zero chiamate cloud a query-time, scegli un embedder
> locale per quell'entità.

---

## Le quattro modalità in dettaglio

### `hybrid` (default) — il tuttofare

Esegue **due ricerche in parallelo** e le fonde con **RRF** (Reciprocal Rank Fusion):

1. **sparse BM25** sui termini della query (keyword esatte, nomi, codici);
2. **dense kNN** sul vettore della query (concetti, parafrasi, sinonimi semantici).

Prende il meglio di entrambi: trova "Mario Rossi" (keyword) **e** "chi si occupa di
sicurezza" (concetto). È il default consigliato per la maggior parte dei casi.

### `vettoriale / vector` — semantica pura (kNN)

Embedda la query e fa **k-nearest-neighbor coseno** sui vettori densi. Nessun match di
parole: solo significato. È la modalità che gestisce **nativamente** maschile/femminile,
singolare/plurale, sinonimi e parafrasi — perché lavora sul concetto, non sulla stringa.

Punto debole: identificatori esatti, SKU, codici, nomi propri rari → il vettore "sbava".
Usala per query concettuali ("prodotti per esterni resistenti all'acqua") e cross-lingua.

### `bm25` — keyword con ranking di rilevanza (IDF/TF)

**Vero BM25** sull'indice sparse: pesa i termini per frequenza (TF) e rarità (IDF). Un
documento che contiene il termine più volte, in un campo corto, e dove il termine è raro nel
corpus, sale più in alto. Ordina per **rilevanza statistica**.

- Tolleranza ai refusi preservata: la query viene espansa con varianti
  Levenshtein-1 + morfologia italiana **prima** di entrare nel modello BM25.
- Collezioni multi-campo: i sparse per-campo (`sparse_{campo}`) vengono fusi con RRF.
- Le query di **negazione** ("chi *non* lavora su X") ricadono sul path scroll di `fts`
  (BM25 non può classificare l'assenza di un termine).

Usala per codici, SKU, identificatori dove conta *quanto* e *quanto raro*.

### `fts` — full-text booleano + frase

Filtro **booleano di presenza** (`scroll` + `MatchText` per-termine) con:

- **Snowball stemming** sull'indice testuale (`_fts_text`) → singolare/plurale;
- **fuzzy Levenshtein-1** + **morfologia italiana** (numero **e** genere) per-termine;
- **scoring per priorità di campo** (l'ordine in `text_fields` = peso);
- **bonus frase esatta**: se la query multi-parola compare **verbatim** in un campo, quel
  documento riceve un bonus → onora la "ricerca di frase".
- `match_mode: and|or` — come combinare più termini (AND = tutti presenti, OR = almeno uno).

Usala per **recall** (trovare tutto ciò che contiene i termini) e per **frasi esatte**.

---

## Cosa copre ogni modalità — matrice reale

| Caso | `hybrid` | `vector` | `bm25` | `fts` |
|---|:---:|:---:|:---:|:---:|
| **Refusi** (es. `refsuo`→`refuso`) | ✅¹ | ◐² | ✅¹ | ✅¹ |
| **Singolare/plurale** (libro/libri) | ✅ | ✅ | ✅³ | ✅³ |
| **Maschile/femminile** (collaboratore/collaboratrice) | ✅⁴ | ✅ | ✅⁵ | ✅⁵ |
| **Sinonimi — dizionario custom** (`synonyms.yaml`) | ✅ | ✅ | ✅ | ✅ |
| **Sinonimi — dizionario esterno** (OMW/WordNet) | ✅⁶ | ✅⁶ | ✅⁶ | ✅⁶ |
| **Fonetico** (suona-uguale) | ❌ | ❌ | ❌ | ❌ |

¹ Fuzzy Levenshtein-1 (max 1 edit), solo query 1–2 termini, richiede `python-Levenshtein`.
² Il denso *tollera* i refusi se l'errore non stravolge il token, ma non è garantito.
³ Via Snowball stemming + regole morfologiche italiane.
⁴ Lato denso (gli embedding capiscono il genere).
⁵ Via le regole morfologiche di genere (`-tore↔-trice`, `-o↔-a`, `-e→-essa`, `-essa→-e`).
   Over-genera su nomi non-persona (es. `libro`→`libra`), ma gli extra non matchano nulla.
⁶ Solo se `fts.use_omw: true` nel config dell'entità (lingue: `it`, `en`).

### Note sui limiti

- **La morfologia italiana (numero + genere) richiede `fts.language: it`** nel config
  dell'entità. Senza, la lingua è `en` e le regole italiane non si attivano (il fuzzy fa solo
  Levenshtein). Esempio: per far sì che `coordinatrice` agganci `Coordinatore`, l'entità deve
  avere:
  ```yaml
  vector_store:
    fts:
      language: it
      match_mode: and
  ```
- **Fuzzy/morfologia** sono attivi solo su query da **1–2 termini** (guard anti-DoS): su
  query lunghe l'espansione diluirebbe lo score e viene saltata.
- **Tolleranza ai refusi (Levenshtein)** usa un vocabolario costruito al **full-sync in modalità
  fts/bm25**. Un'entità indicizzata in `hybrid` non ha quel vocabolario → in fts/bm25 i refusi
  non vengono corretti (la morfologia italiana funziona comunque, non dipende dal vocab).
- **Sinonimi in modalità keyword**: l'espansione sinonimi/OMW è OR-semantica. In `fts`/`bm25`
  con `match_mode: and` il sistema **forza automaticamente OR** quando l'espansione scatta
  (altrimenti pretenderebbe TUTTI i sinonimi insieme → 0 risultati).
- **`python-Levenshtein`** serve solo per i refusi; la morfologia italiana (numero+genere)
  funziona comunque senza la libreria.
- **Fonetico** (Soundex/Metaphone) **non è implementato**. Per l'italiano vale poco e
  *non* risolve il maschile/femminile (`collaboratore` e `collaboratrice` non sono
  foneticamente uguali) — per il genere usa le regole morfologiche (già attive) o i sinonimi.

---

## Dizionari di sinonimi

Due fonti, indipendenti e cumulabili. Entrambe espandono la query **prima** di mandarla al
motore (per `vector`/`hybrid` l'espansione avviene prima dell'embed).

### 1. `synonyms.yaml` — dizionario custom per-entità (consigliato)

File **opzionale** per ogni entità: `configuration/{Entità}/synonyms.yaml`. Formato: lista di
**gruppi di equivalenza bidirezionali**.

```yaml
# configuration/CollaboratoriDB/synonyms.yaml
- [auto, automobile, macchina, vettura]
- [sviluppatore, developer, programmatore]
- [CV, curriculum, resume]
```

Se in query compare **un qualsiasi** termine di un gruppo, vengono aggiunti **tutti** gli
altri del gruppo. Funziona in **tutte** le modalità Qdrant. Se il file non esiste → no-op.
È la leva più potente e precisa nelle modalità keyword: controlli tu il dominio.

> Dopo aver creato/modificato `synonyms.yaml` **non serve re-indicizzare**: l'espansione è a
> query-time. Basta che il file sia sul volume montato in `configuration/`.

### 2. OMW (Open Multilingual Wordnet) — dizionario esterno generico

Dizionario WordNet multilingua, **opt-in** per entità:

```yaml
vector_store:
  search_mode: fts
  fts:
    language: it        # it | en (whitelist)
    use_omw: true       # scarica OMW a runtime e lo usa per espandere
```

Quando attivo, OMW aggiunge i lemmi sinonimi alla query. Richiede la libreria `wn` (degrada a
no-op se assente) e scarica il pacchetto della lingua al primo uso. Copertura ampia ma
**rumorosa** (sinonimi spesso troppo larghi) — preferisci `synonyms.yaml` per il controllo.

---

## Come lanciare una ricerca

### Da API — `GET /search`

| Parametro | Esempio | Descrizione |
|---|---|---|
| `q` | `q=cavo di rete` | query (obbligatorio) |
| `collection` | `collection=ProdottiFake` | entità su cui cercare |
| `search_mode` | `search_mode=fts` | forza la modalità per questa richiesta (override del config) |
| `match_mode` | `match_mode=or` | `and` (default) \| `or` — solo `fts` (e `bm25` in negazione) |
| `filter` | `filter=categoria:Reti` | filtri esatti su `metadata_fields` (`Campo:Valore[,...]`) |
| `min_score` | `min_score=0.6` | esclude risultati sotto soglia (0.0–1.0) |
| `fields` | `fields=nome,categoria` | campi nel JSON di risposta |
| `limit` | `limit=20` | numero risultati |

```bash
# FTS, ricerca di frase esatta, niente Ollama necessario
curl "http://localhost:8000/search?q=cavo+di+rete&collection=ProdottiFake&search_mode=fts"

# BM25 puro su un codice prodotto
curl "http://localhost:8000/search?q=RJ45&collection=ProdottiFake&search_mode=bm25"

# Semantica pura
curl "http://localhost:8000/search?q=connettori+per+esterni+impermeabili&collection=ProdottiFake&search_mode=vector"
```

> `search_mode` è un **override per-richiesta**. Se omesso, vale la `search_mode` del config
> dell'entità. Un'entità indicizzata in `hybrid` può servire tutte e quattro le modalità
> (ha sia i vettori densi sia l'indice sparse); un'entità indicizzata in `fts`/`bm25` non ha
> i vettori densi, quindi `vector`/`hybrid` non sono disponibili per essa.

### Dal frontend (`/search`)

Il selettore **Modalità** appare quando l'entità è su Qdrant ed è stata indicizzata in
`hybrid` (le altre indicizzazioni espongono una sola modalità). Le modalità keyword
(`bm25`/`fts`) restano selezionabili **anche a Ollama spento**.

---

## Casi d'uso — quale scegliere

| Obiettivo | Modalità consigliata |
|---|---|
| "Non so bene cosa cerco, voglio risultati sensati" | `hybrid` |
| Codice/SKU/identificatore esatto, ordinato per rilevanza | `bm25` |
| Frase esatta o massimo recall sui termini, Ollama offline | `fts` |
| Concetto/parafrasi, cross-lingua, m/f e sinonimi automatici | `vector` |
| Ricerca con negazione ("chi **non** fa X") | `fts` (o `bm25`, ricade su scroll) |

---

## Sotto il cofano (riferimenti al codice)

- Modalità e fusione: `sync-service/vector_stores/qdrant_store.py` → `search()`,
  `_bm25_search()`, `_score_by_field()`.
- Fuzzy + morfologia (numero + genere): `sync-service/vector_stores/fuzzy.py`.
- Sinonimi custom + OMW: `sync-service/vector_stores/synonyms.py`.
- Wiring query-time (espansione sinonimi/OMW/fuzzy, negazione): `sync-service/api/search.py`.
