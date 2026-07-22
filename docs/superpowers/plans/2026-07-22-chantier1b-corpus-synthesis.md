# Chantier 1b — Synthèse corpus map-reduce Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Doter le RAG d'une capacité de **synthèse agrégative balayant tout le corpus** (« catégorise toutes les attaques connues de ce corpus »), impossible aujourd'hui car tout est plafonné par un top-k qui voit < 0,1 % d'un corpus de 50 docs.

**Architecture:** Un pipeline **déterministe** map-reduce (`core/synthesize_corpus.py`), déclenché par l'intention `aggregate` (détecteur livré en 1a) : (1) pré-filtrer les documents pertinents pour l'aspect demandé, (2) MAP = extraire l'aspect par document (résumés RAPTOR + chunks scopés → LLM), concurrence bornée, (3) REDUCE hiérarchique = agréger/dédupliquer/catégoriser avec attribution par source. Streaming glass-box via SSE. Le LLM est **injectable** pour des tests offline déterministes.

**Tech Stack:** Python 3.12, MongoDB (`chunks`), Ollama via `core.model_router.build_llm`, FastAPI SSE. Réutilise `core.summarize._gather_material`, `core.ask.retrieve_only`, `retrieval.intent.classify_intent` (1a). Tests : pytest offline avec LLM factice injecté.

## Global Constraints

- **100% offline** : aucun téléchargement, aucun nouveau service ni dépendance pip. LLM local via Ollama (`build_llm("generate")`).
- **Déterminisme & fiabilité** : le balayage du corpus est un pipeline CODÉ, pas laissé au libre-arbitre du ReAct — l'exhaustivité prime.
- **LLM injectable** : `synthesize_corpus(...)` et `extract_from_document(...)` acceptent un paramètre `llm=` (callable `str -> str`) ; défaut `build_llm("generate").invoke`. Les tests injectent un faux LLM → aucun appel réseau/GPU en test.
- **Bornes obligatoires** (env, surchargeables) : `CORPUS_MAP_CONCURRENCY=2`, `CORPUS_MAX_DOCS=50`. Jamais de troncature silencieuse : si un plafond coupe, l'émettre en événement glass-box.
- **Attribution par source** : chaque item de la synthèse finale cite le(s) document(s) d'où il vient.
- **Ne déclencher le map-reduce QUE sur l'intention `aggregate`** ; toute autre question garde le chemin retrieval de 1a (coûteux : O(nb docs) appels LLM).
- **Glass-box** : émettre la progression (pré-filtrage, document i/N, agrégation) en trames SSE, via le même style que `_agent_events` (`api/rag.py`).
- Nouvel outil/agent visible dans l'onglet **Informations** (principe projet : tout nouveau skill agent y est expliqué).

---

### Task 1: Lister les sources indexées (couche donnée)

Helper réutilisable qui renvoie les noms de documents du corpus (données déjà exposées par `/api/sources`).

**Files:**
- Create: `core/corpus.py`
- Test: `tests/test_corpus_sources.py`

**Interfaces:**
- Produces: `list_indexed_sources(db=None) -> list[str]` — noms de documents distincts (triés), depuis la collection Mongo `chunks` (`$group` sur `source`, hors `null`). `db` injectable pour les tests (défaut : `utils.mongo.get_db()`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_corpus_sources.py
"""list_indexed_sources agrège les noms de documents distincts. Mongo factice injecté."""
from core.corpus import list_indexed_sources


class _FakeCol:
    def aggregate(self, pipeline):
        return iter([{"_id": "b.md"}, {"_id": "a.md"}])


class _FakeDB:
    def __getitem__(self, name):
        assert name == "chunks"
        return _FakeCol()


def test_list_indexed_sources_sorted_distinct():
    out = list_indexed_sources(db=_FakeDB())
    assert out == ["a.md", "b.md"]  # triés


def test_list_indexed_sources_empty():
    class _EmptyCol:
        def aggregate(self, pipeline):
            return iter([])
    class _EmptyDB:
        def __getitem__(self, n):
            return _EmptyCol()
    assert list_indexed_sources(db=_EmptyDB()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_corpus_sources.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.corpus'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/corpus.py
"""Utilitaires corpus-large : liste des documents indexés (données Mongo `chunks`)."""
from __future__ import annotations


def list_indexed_sources(db=None) -> list[str]:
    """Noms de documents distincts du corpus, triés. `db` injectable (tests)."""
    if db is None:
        from utils.mongo import get_db
        db = get_db()
    rows = db["chunks"].aggregate([
        {"$match": {"source": {"$ne": None}}},
        {"$group": {"_id": "$source"}},
    ])
    return sorted(r["_id"] for r in rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_corpus_sources.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add core/corpus.py tests/test_corpus_sources.py
git commit -m "feat(corpus): list_indexed_sources (couche donnée map-reduce)"
```

---

### Task 2: Réglages du pipeline (`env_config.py`)

**Files:**
- Modify: `env_config.py` (nouveau bloc `corpus_config`, merge dans `CONFIG`, exports)
- Test: `tests/test_config_corpus.py`

**Interfaces:**
- Produces globals: `CORPUS_MAP_CONCURRENCY: int` (2), `CORPUS_MAX_DOCS: int` (50), `CORPUS_PREFILTER_TOPN: int` (200) — nb de chunks remontés par la passe de pré-filtrage pour décider quels docs sont pertinents.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_corpus.py
import env_config as ec


def test_corpus_knobs():
    assert isinstance(ec.CORPUS_MAP_CONCURRENCY, int) and ec.CORPUS_MAP_CONCURRENCY >= 1
    assert isinstance(ec.CORPUS_MAX_DOCS, int) and ec.CORPUS_MAX_DOCS >= 1
    assert isinstance(ec.CORPUS_PREFILTER_TOPN, int) and ec.CORPUS_PREFILTER_TOPN >= ec.NUM_CHUNKS
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_config_corpus.py -v` → FAIL (AttributeError CORPUS_MAP_CONCURRENCY).

- [ ] **Step 3: Implement** — ajouter dans `env_config.py` un bloc puis le merger et l'exporter (même pattern que `retrieval_config`) :

```python
    corpus_config = {
        "CORPUS_MAP_CONCURRENCY": int(os.environ.get("CORPUS_MAP_CONCURRENCY", "2")),
        "CORPUS_MAX_DOCS": int(os.environ.get("CORPUS_MAX_DOCS", "50")),
        "CORPUS_PREFILTER_TOPN": int(os.environ.get("CORPUS_PREFILTER_TOPN", "200")),
    }
```

Merger `**corpus_config,` dans le dict `full_config` (à côté de `**retrieval_config,`), puis exporter :

```python
CORPUS_MAP_CONCURRENCY = CONFIG["CORPUS_MAP_CONCURRENCY"]
CORPUS_MAX_DOCS = CONFIG["CORPUS_MAX_DOCS"]
CORPUS_PREFILTER_TOPN = CONFIG["CORPUS_PREFILTER_TOPN"]
```

Ajouter les 3 clés à `.env.example`.

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_config_corpus.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add env_config.py .env.example tests/test_config_corpus.py
git commit -m "feat(config): réglages du pipeline de synthèse corpus"
```

---

### Task 3: Pré-filtrage des documents pertinents

Sélectionne les documents à MAP-er, pour éviter de balayer 50 docs quand 8 concernent l'aspect. Rappelant (ne rate pas un doc pertinent), borné.

**Files:**
- Modify: `core/corpus.py` (ajout `prefilter_documents`)
- Test: `tests/test_corpus_prefilter.py`

**Interfaces:**
- Consumes: `core.ask.retrieve_only` (déjà présent) ; `core.corpus.list_indexed_sources` (Task 1).
- Produces: `prefilter_documents(aspect: str, retrieve=None, all_sources=None, topn=None, max_docs=None) -> list[str]` — renvoie les documents ayant ≥1 chunk dans le top-`topn` du retrieval sur `aspect` (périmètre = tous), plafonné à `max_docs` en préservant l'ordre de pertinence. `retrieve`/`all_sources` injectables (tests). Si le pré-filtre ne remonte rien (aspect trop générique), **repli** sur toutes les sources (bornées à `max_docs`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_corpus_prefilter.py
from core.corpus import prefilter_documents


def _fake_retrieve(chunks):
    def _r(query, source_filter=None):
        return query, chunks
    return _r


def test_prefilter_keeps_relevant_docs_in_relevance_order():
    # retrieval renvoie des chunks de A puis B (A plus pertinent), C absent.
    chunks = [
        {"meta": {"source": "A.md"}}, {"meta": {"source": "A.md"}},
        {"meta": {"source": "B.md"}},
    ]
    out = prefilter_documents("attaques", retrieve=_fake_retrieve(chunks),
                              all_sources=["A.md", "B.md", "C.md"], topn=10, max_docs=50)
    assert out == ["A.md", "B.md"]  # C exclu, ordre de 1re apparition (pertinence)


def test_prefilter_caps_max_docs():
    chunks = [{"meta": {"source": f"{c}.md"}} for c in "ABCDE"]
    out = prefilter_documents("x", retrieve=_fake_retrieve(chunks),
                              all_sources=[f"{c}.md" for c in "ABCDE"], topn=50, max_docs=3)
    assert out == ["A.md", "B.md", "C.md"]


def test_prefilter_falls_back_to_all_when_empty():
    out = prefilter_documents("x", retrieve=_fake_retrieve([]),
                              all_sources=["A.md", "B.md"], topn=10, max_docs=50)
    assert out == ["A.md", "B.md"]  # repli : tout le corpus (borné)
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_corpus_prefilter.py -v` → FAIL (ImportError prefilter_documents).

- [ ] **Step 3: Implement** dans `core/corpus.py` :

```python
def prefilter_documents(aspect, retrieve=None, all_sources=None, topn=None, max_docs=None):
    """Documents pertinents pour `aspect` : ceux ayant un chunk dans le top-`topn`
    du retrieval (périmètre = tous), en ordre de 1re apparition (pertinence),
    plafonné à `max_docs`. Repli sur tout le corpus si le pré-filtre est vide."""
    from env_config import CORPUS_PREFILTER_TOPN, CORPUS_MAX_DOCS
    topn = topn or CORPUS_PREFILTER_TOPN
    max_docs = max_docs or CORPUS_MAX_DOCS
    if retrieve is None:
        from core.ask import retrieve_only as retrieve
    if all_sources is None:
        all_sources = list_indexed_sources()

    _q, chunks = retrieve(aspect, source_filter=None)
    ordered = []
    seen = set()
    for c in (chunks or [])[:topn]:
        src = (c.get("meta") or {}).get("source")
        if src and src not in seen:
            seen.add(src)
            ordered.append(src)
    if not ordered:                       # aspect trop générique -> tout le corpus
        ordered = list(all_sources)
    return ordered[:max_docs]
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_corpus_prefilter.py -v` → PASS (3).

- [ ] **Step 5: Commit**

```bash
git add core/corpus.py tests/test_corpus_prefilter.py
git commit -m "feat(corpus): pré-filtrage rappelant des documents pertinents"
```

---

### Task 4: MAP — extraction par document

Extrait l'aspect demandé d'UN document, à partir de son matériau (résumés RAPTOR + chunks scopés). LLM injectable.

**Files:**
- Create: `core/corpus_extract.py`
- Test: `tests/test_corpus_extract.py`

**Interfaces:**
- Consumes: `core.summarize._gather_material` (matériau par doc, résumés RAPTOR d'abord) ; un `llm` callable.
- Produces: `extract_from_document(document: str, aspect: str, llm=None, gather=None) -> dict` — `{"document": str, "items": list[str], "raw": str}`. Le LLM reçoit un prompt d'extraction (liste factuelle d'items de l'aspect, un par ligne, zéro invention) ; `items` = lignes non vides parsées de la sortie. `gather`/`llm` injectables (tests offline).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_corpus_extract.py
from core.corpus_extract import extract_from_document


def test_extract_parses_items_from_llm(monkeypatch):
    fake_material = ("résumé", 3, "résumés de section RAPTOR")
    fake_llm = lambda prompt: "- Déni de service\n- Injection SQL\n\n- Rejeu\n"
    out = extract_from_document("doc.md", "attaques",
                                llm=fake_llm, gather=lambda src: fake_material)
    assert out["document"] == "doc.md"
    assert out["items"] == ["Déni de service", "Injection SQL", "Rejeu"]


def test_extract_empty_material_returns_no_items():
    out = extract_from_document("doc.md", "attaques",
                                llm=lambda p: "IGNORED",
                                gather=lambda src: ("", 0, "échantillon de chunks"))
    assert out["items"] == []
    assert out["document"] == "doc.md"
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_corpus_extract.py -v` → FAIL (ImportError).

- [ ] **Step 3: Implement** `core/corpus_extract.py` :

```python
"""MAP du pipeline de synthèse corpus : extraction d'un aspect depuis UN document,
à partir de son matériau (résumés RAPTOR d'abord). LLM injectable (tests offline)."""
from __future__ import annotations

_EXTRACT_PROMPT = """[RÔLE] Tu EXTRAIS d'un document technique tous les éléments correspondant à : {aspect}.

[CONSIGNES]
- Appuie-toi UNIQUEMENT sur le MATÉRIAU fourni. Zéro invention.
- Renvoie une LISTE : un élément par ligne, préfixé « - ». Rien d'autre (pas d'intro, pas de conclusion).
- Chaque élément est factuel, concis, dans la LANGUE du document. Conserve les identifiants/valeurs exacts.
- Si le document ne contient AUCUN élément pour « {aspect} », renvoie une liste vide (aucune ligne).

[MATÉRIAU - document « {document} »]
{material}

[ÉLÉMENTS EXTRAITS pour « {aspect} »]"""


def _parse_items(text: str) -> list[str]:
    items = []
    for line in (text or "").splitlines():
        s = line.strip().lstrip("-*•").strip()
        if s:
            items.append(s)
    return items


def extract_from_document(document, aspect, llm=None, gather=None):
    """Extrait `aspect` du document. Retourne {document, items, raw}."""
    if gather is None:
        from core.summarize import _gather_material as gather
    if llm is None:
        from core.model_router import build_llm
        llm = build_llm("generate").invoke

    material, _n, _basis = gather(document)
    if not (material or "").strip():
        return {"document": document, "items": [], "raw": ""}
    from core.summarize import _MAX_MATERIAL_CHARS
    prompt = _EXTRACT_PROMPT.format(aspect=aspect, document=document,
                                    material=material[:_MAX_MATERIAL_CHARS])
    raw = llm(prompt)
    return {"document": document, "items": _parse_items(raw), "raw": raw}
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_corpus_extract.py -v` → PASS (2).

- [ ] **Step 5: Commit**

```bash
git add core/corpus_extract.py tests/test_corpus_extract.py
git commit -m "feat(corpus): MAP — extraction d'un aspect par document (LLM injectable)"
```

---

### Task 5: Orchestrateur map-reduce en streaming (`core/synthesize_corpus.py`)

Pré-filtre → MAP (concurrence bornée) → REDUCE hiérarchique, en émettant des événements glass-box (générateur, comme `PlannerAgent.run_stream`). LLM injectable.

**Files:**
- Create: `core/synthesize_corpus.py`
- Test: `tests/test_synthesize_corpus.py`

**Interfaces:**
- Consumes: `core.corpus.prefilter_documents`, `core.corpus_extract.extract_from_document`, `env_config.CORPUS_MAP_CONCURRENCY`.
- Produces: `synthesize_corpus(question, aspect=None, llm=None, prefilter=None, extract=None) -> Iterator[dict]` — générateur d'événements :
  - `{"type":"stage","stage":"prefilter","documents":[...]}`
  - `{"type":"map","index":i,"total":N,"document":d,"n_items":k}` (par document MAP-é)
  - `{"type":"stage","stage":"reduce"}`
  - `{"type":"token","text":...}` (synthèse finale streamée, ou un seul token si le LLM renvoie une string)
  - `{"type":"done","result":{"answer":str,"documents":[...],"n_items":int}}`
  - `aspect` défaut = `question` ; `llm`/`prefilter`/`extract` injectables (tests offline, aucune concurrence réelle requise — bornée à 1 possible via env).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_synthesize_corpus.py
from core.synthesize_corpus import synthesize_corpus


def test_pipeline_emits_glassbox_events_and_reduces(monkeypatch):
    monkeypatch.setenv("CORPUS_MAP_CONCURRENCY", "1")
    # 2 documents pré-filtrés ; extraction factice par doc ; reduce factice.
    prefilter = lambda aspect: ["a.md", "b.md"]
    def extract(document, aspect):
        data = {"a.md": ["Déni de service", "Rejeu"], "b.md": ["Injection SQL", "Rejeu"]}
        return {"document": document, "items": data[document], "raw": ""}
    reduce_llm = lambda prompt: "**Réseau**\n- Déni de service (a.md)\n- Rejeu (a.md, b.md)\n**Applicatif**\n- Injection SQL (b.md)"

    events = list(synthesize_corpus("catégorise les attaques", aspect="attaques",
                                    llm=reduce_llm, prefilter=prefilter, extract=extract))
    types = [e["type"] for e in events]
    assert types[0] == "stage" and events[0]["stage"] == "prefilter"
    assert events[0]["documents"] == ["a.md", "b.md"]
    map_events = [e for e in events if e["type"] == "map"]
    assert len(map_events) == 2 and map_events[0]["total"] == 2
    assert any(e["type"] == "stage" and e["stage"] == "reduce" for e in events)
    done = events[-1]
    assert done["type"] == "done"
    assert set(done["result"]["documents"]) == {"a.md", "b.md"}
    assert "Injection SQL" in done["result"]["answer"]


def test_pipeline_no_relevant_docs(monkeypatch):
    events = list(synthesize_corpus("q", aspect="x", llm=lambda p: "",
                                    prefilter=lambda a: [], extract=lambda d, a: None))
    done = events[-1]
    assert done["type"] == "done"
    assert done["result"]["documents"] == []
    assert done["result"]["n_items"] == 0
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_synthesize_corpus.py -v` → FAIL (ImportError).

- [ ] **Step 3: Implement** `core/synthesize_corpus.py` :

```python
"""Pipeline DÉTERMINISTE de synthèse corpus-large (map-reduce), en streaming.
Pré-filtre les documents pertinents, extrait l'aspect par document (concurrence
bornée), puis réduit en une synthèse catégorisée avec attribution par source.
LLM et étapes injectables pour des tests offline déterministes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

_REDUCE_PROMPT = """[RÔLE] Tu SYNTHÉTISES en une réponse unique les éléments « {aspect} » extraits de plusieurs documents.

[CONSIGNES]
- Appuie-toi UNIQUEMENT sur les ÉLÉMENTS fournis (chacun étiqueté par son document). Zéro invention.
- DÉDUPLIQUE les éléments équivalents et REGROUPE-les par CATÉGORIE pertinente.
- Pour chaque élément, cite entre parenthèses le(s) document(s) source(s).
- Rédige dans la langue des documents. Factuel, structuré, exhaustif sur le matériau fourni.

[ÉLÉMENTS EXTRAITS - aspect « {aspect} »]
{material}

[SYNTHÈSE CATÉGORISÉE de « {aspect} »]"""


def _format_material(extractions) -> str:
    blocks = []
    for ex in extractions:
        if ex and ex.get("items"):
            lines = "\n".join(f"- {it}" for it in ex["items"])
            blocks.append(f"### {ex['document']}\n{lines}")
    return "\n\n".join(blocks)


def synthesize_corpus(question, aspect=None, llm=None, prefilter=None, extract=None):
    """Générateur d'événements glass-box (voir docstring du plan)."""
    aspect = aspect or question
    if prefilter is None:
        from core.corpus import prefilter_documents
        prefilter = lambda a: prefilter_documents(a)
    if extract is None:
        from core.corpus_extract import extract_from_document
        extract = lambda d, a: extract_from_document(d, a)
    if llm is None:
        from core.model_router import build_llm
        llm = build_llm("generate").invoke
    from env_config import CORPUS_MAP_CONCURRENCY

    docs = prefilter(aspect)
    yield {"type": "stage", "stage": "prefilter", "documents": list(docs)}

    if not docs:
        yield {"type": "done", "result": {"answer": "", "documents": [], "n_items": 0}}
        return

    # MAP (concurrence bornée) — pool.map préserve l'ordre des documents.
    workers = max(1, int(CORPUS_MAP_CONCURRENCY))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        extractions = list(pool.map(lambda d: extract(d, aspect), docs))
    for i, (d, res) in enumerate(zip(docs, extractions), 1):
        n_items = len(res["items"]) if res and res.get("items") else 0
        yield {"type": "map", "index": i, "total": len(docs), "document": d, "n_items": n_items}

    # REDUCE
    yield {"type": "stage", "stage": "reduce"}
    material = _format_material(extractions)
    total_items = sum(len(e["items"]) for e in extractions if e and e.get("items"))
    if not material.strip():
        yield {"type": "done", "result": {"answer": "", "documents": list(docs), "n_items": 0}}
        return
    answer = llm(_REDUCE_PROMPT.format(aspect=aspect, material=material))
    yield {"type": "token", "text": answer}
    yield {"type": "done", "result": {"answer": answer, "documents": list(docs),
                                       "n_items": total_items}}
```

> Note : les événements `map` sont émis APRÈS la collecte `pool.map` (l'ordre déterministe prime ; un vrai streaming par-document, au fil des complétions, est une amélioration ultérieure — YAGNI tant que la sonde ne montre pas d'attente gênante). `_MAX_MATERIAL_CHARS` et `_gather_material` sont réutilisés via `core.summarize`.

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_synthesize_corpus.py -v` → PASS (2). Vérifier qu'aucun appel réseau (tout injecté).

- [ ] **Step 5: Commit**

```bash
git add core/synthesize_corpus.py tests/test_synthesize_corpus.py
git commit -m "feat(corpus): orchestrateur map-reduce en streaming (LLM injectable)"
```

---

### Task 6: Routage `aggregate` + SSE dans l'API

Brancher le pipeline sur `/api/ask` : quand l'intention est `aggregate` (mode auto) ou `mode=="synth"` forcé, driver `synthesize_corpus` et streamer les trames glass-box.

**Files:**
- Modify: `api/rag.py` (dans `ask.gen()`, avant/à côté du bloc de routage ~L182-227)
- Test: `tests/test_ask_aggregate_route.py`

**Interfaces:**
- Consumes: `retrieval.intent.classify_intent` (1a), `core.synthesize_corpus.synthesize_corpus`, `api.common._sse`.
- Produces: le routage émet `{"type":"route","mode":"synth","reason":...}` puis relaie les événements du pipeline en SSE (`stage`/`map`/`token`/`sources`/`done`), et persiste l'échange.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ask_aggregate_route.py
"""Le routeur envoie une question d'agrégation vers le pipeline de synthèse corpus.
On patche synthesize_corpus pour rester offline."""
import json
import api.rag as rag
from fastapi.testclient import TestClient
from fastapi import FastAPI


def _events(resp_text):
    out = []
    for line in resp_text.splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[len("data:"):].strip()))
    return out


def test_aggregate_question_routes_to_synth(monkeypatch):
    def fake_pipeline(question, aspect=None, **kw):
        yield {"type": "stage", "stage": "prefilter", "documents": ["a.md", "b.md"]}
        yield {"type": "map", "index": 1, "total": 2, "document": "a.md", "n_items": 2}
        yield {"type": "map", "index": 2, "total": 2, "document": "b.md", "n_items": 1}
        yield {"type": "stage", "stage": "reduce"}
        yield {"type": "token", "text": "Synthèse..."}
        yield {"type": "done", "result": {"answer": "Synthèse...",
                                          "documents": ["a.md", "b.md"], "n_items": 3}}
    monkeypatch.setattr(rag, "_synthesize_corpus", fake_pipeline, raising=False)
    # neutralise la persistance Mongo
    monkeypatch.setattr(rag, "_persist_exchange", lambda *a, **k: None)

    app = FastAPI(); app.include_router(rag.router)
    client = TestClient(app)
    r = client.post("/api/ask", json={"question": "Catégorise toutes les attaques du corpus",
                                      "mode": "auto"})
    evs = _events(r.text)
    assert any(e.get("type") == "route" and e.get("mode") == "synth" for e in evs)
    assert any(e.get("type") == "map" for e in evs)
    assert any(e.get("type") == "done" and e.get("found") for e in evs)
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_ask_aggregate_route.py -v` → FAIL (pas de mode synth).

- [ ] **Step 3: Implement** dans `api/rag.py` :

- En tête (imports paresseux existants), ajouter un indirection patchable pour les tests :

```python
def _synthesize_corpus(question, **kwargs):
    from core.synthesize_corpus import synthesize_corpus
    return synthesize_corpus(question, **kwargs)
```

- Dans `gen()`, au moment du routage (remplacer le bloc `if body.mode in ("rag","agent")...` par une version à trois voies) :

```python
            # Routage : Auto -> intention ; sinon forcé (rag/agent/synth).
            if body.mode in ("rag", "agent", "synth"):
                effective, reason = body.mode, f"mode {body.mode.upper()} forcé"
            else:
                from retrieval.intent import classify_intent
                intent = classify_intent(body.question)
                if intent == "aggregate":
                    effective, reason = "synth", "synthèse corpus (agrégation détectée)"
                else:
                    from core.router import route_query
                    d = route_query(body.question)
                    effective, reason = d["mode"], d["reason"]
            yield _sse({"type": "route", "mode": effective, "reason": reason})

            if effective == "synth":
                answer_txt = ""
                documents: list = []
                for ev in _synthesize_corpus(body.question):
                    t = ev.get("type")
                    if t == "stage":
                        yield _sse(ev)
                    elif t == "map":
                        yield _sse(ev)
                    elif t == "token":
                        answer_txt += ev["text"]
                        yield _sse({"type": "token", "text": ev["text"]})
                    elif t == "done":
                        documents = ev["result"].get("documents", [])
                        citations = [{"source": d} for d in documents]
                        yield _sse({"type": "sources", "citations": citations})
                        _persist_exchange(session_id, body.source, body.question,
                                          answer_txt, citations, None, [])
                        yield _sse({"type": "done", "found": bool(answer_txt)})
                return
```

(Le bloc `if effective == "agent":` et le RAG direct restent inchangés en dessous.)

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_ask_aggregate_route.py -v` puis la suite complète → PASS.

- [ ] **Step 5: Commit**

```bash
git add api/rag.py tests/test_ask_aggregate_route.py
git commit -m "feat(api): routage intention aggregate -> pipeline de synthèse corpus (SSE)"
```

---

### Task 7: Outils agent + onglet Informations

Exposer `list_sources` et `extract_from_document` comme outils (le mode Agent peut fan-out), et documenter le nouveau mode « Synthèse corpus » dans l'onglet Informations du front.

**Files:**
- Create: `tools/corpus_tools.py`
- Modify: `tools/rag_tool.py` (`_TOOLS` registre) OU exposer via un registre dédié importé par l'agent
- Modify: le composant de l'onglet Informations du front (chercher `web/src` le composant listant les agents/outils)
- Test: `tests/test_corpus_tools.py`

**Interfaces:**
- Produces: `tools.corpus_tools.list_sources() -> dict` (`{ok, sources:[...]}`) et `tools.corpus_tools.extract_from_document_tool(document, aspect) -> dict` (`{ok, document, items}`), enregistrés dans le registre d'outils. Descriptions en langage naturel (contrat tool calling).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_corpus_tools.py
from tools.corpus_tools import list_sources, extract_from_document_tool


def test_list_sources_tool(monkeypatch):
    import core.corpus as corpus
    monkeypatch.setattr(corpus, "list_indexed_sources", lambda db=None: ["a.md", "b.md"])
    out = list_sources()
    assert out["ok"] is True and out["sources"] == ["a.md", "b.md"]


def test_extract_tool(monkeypatch):
    import core.corpus_extract as ce
    monkeypatch.setattr(ce, "extract_from_document",
                        lambda document, aspect: {"document": document, "items": ["X"], "raw": ""})
    out = extract_from_document_tool("a.md", "attaques")
    assert out["ok"] is True and out["items"] == ["X"] and out["document"] == "a.md"
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_corpus_tools.py -v` → FAIL (ImportError).

- [ ] **Step 3: Implement** `tools/corpus_tools.py` :

```python
"""Outils agent pour le corpus-large : lister les documents, extraire un aspect
d'un document. Contrat tool calling (nom + description NL + exécuteur)."""
from __future__ import annotations

LIST_SOURCES_NAME = "list_sources"
EXTRACT_NAME = "extract_from_document"


def list_sources() -> dict:
    """Liste les documents indexés du corpus (pour un balayage/agrégation)."""
    try:
        from core.corpus import list_indexed_sources
        return {"ok": True, "sources": list_indexed_sources()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def extract_from_document_tool(document: str, aspect: str) -> dict:
    """Extrait d'UN document tous les éléments d'un aspect (MAP réutilisable)."""
    if not document or not aspect:
        return {"ok": False, "error": "document et aspect requis."}
    try:
        from core.corpus_extract import extract_from_document
        res = extract_from_document(document, aspect)
        return {"ok": True, "document": res["document"], "items": res["items"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

Enregistrer ces outils dans le registre exposé à l'agent (suivre le pattern de `tools/rag_tool._TOOLS` ; si `run_tool` impose `query`, ajouter ces outils via un registre étendu ou assouplir la validation pour les outils sans `query`). L'implémenteur doit inspecter `tools/rag_tool.run_tool` et choisir l'intégration la moins invasive, en gardant `rag_search` intact.

Documenter le mode « Synthèse corpus » dans l'onglet **Informations** du front (localiser le composant qui décrit les agents/skills ; ajouter une entrée : quand il se déclenche — questions d'agrégation « toutes les X du corpus » — et ce qu'il fait — balaye chaque document, extrait, puis agrège avec attribution).

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_corpus_tools.py -v` puis la suite complète → PASS. Vérifier le build front si le composant Informations est modifié (`cd web && npm run build` ou lint).

- [ ] **Step 5: Commit**

```bash
git add tools/corpus_tools.py tests/test_corpus_tools.py web/src
git commit -m "feat(corpus): outils agent list_sources/extract + doc onglet Informations"
```

---

### Task 8: Validation bout-en-bout (sonde live)

**Files:**
- Create: `scripts/probe_corpus_synthesis.py`

- [ ] **Step 1: Écrire la sonde** — driver `synthesize_corpus` sur une vraie question d'agrégation, avec le LLM réel, en imprimant les événements glass-box et la synthèse finale :

```python
# scripts/probe_corpus_synthesis.py
"""Sonde live : synthèse corpus map-reduce. .venv/bin/python -m scripts.probe_corpus_synthesis"""
import os
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
from core.synthesize_corpus import synthesize_corpus


def main():
    q = "Catégorise toutes les attaques et menaces connues de ce corpus."
    answer = ""
    for ev in synthesize_corpus(q, aspect="attaques et menaces"):
        t = ev["type"]
        if t == "stage":
            print(f"[stage] {ev['stage']}" + (f" docs={ev.get('documents')}" if ev.get("documents") else ""))
        elif t == "map":
            print(f"[map] {ev['index']}/{ev['total']} {ev['document']} -> {ev['n_items']} items")
        elif t == "token":
            answer += ev["text"]
        elif t == "done":
            print(f"[done] documents={ev['result']['documents']} n_items={ev['result']['n_items']}")
    print("\n=== SYNTHÈSE ===\n" + answer)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Lancer** `.venv/bin/python -m scripts.probe_corpus_synthesis` (MongoDB + Ollama requis). Vérifier : le pré-filtre remonte les documents pertinents, chaque document est MAP-é (items > 0 pour les docs concernés), la synthèse finale est **catégorisée** et **cite plusieurs documents**. Capturer la sortie dans le rapport.

- [ ] **Step 3: Suite complète** `.venv/bin/python -m pytest -q` → PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/probe_corpus_synthesis.py
git commit -m "test(corpus): sonde live de la synthèse corpus map-reduce"
```

---

## Self-Review

**Spec coverage (spec §C) :**
- Détection intention `aggregate` → routage (spec §C, §A) → Task 6 (réutilise `classify_intent` de 1a). ✅
- Pré-filtrage documents pertinents (spec §C.1) → Task 3. ✅
- MAP extraction par document, résumés RAPTOR + chunks scopés (spec §C.2) → Task 4. ✅
- REDUCE hiérarchique, dédup, catégorisation, attribution (spec §C.3) → Task 5 (reduce) + prompt. ⚠️ *Réduction « hiérarchique » par lots non implémentée en v1 : à 50 docs × items, le matériau REDUCE peut dépasser la fenêtre — voir Risque ci-dessous.*
- Glass-box streaming (spec §C.4) → Tasks 5 (événements) + 6 (SSE). ✅
- Bornes/garde-fous (spec §C.5) → Task 2 (knobs) + Task 3 (max_docs) + Task 5 (concurrence). ✅
- Outils agent list_sources/extract (spec §C.1) + onglet Informations → Task 7. ✅
- Validation (spec §Vérification) → Task 8. ✅

**Placeholder scan :** le bloc mort du ThreadPoolExecutor en Task 5 Step 3 est EXPLICITEMENT signalé comme à nettoyer (note d'implémentation) — l'implémenteur doit le remplacer par un seul `pool.map`. Aucun autre TBD.

**Type consistency :** `extract_from_document(document, aspect, llm, gather) -> {document, items, raw}` cohérent Tasks 4/5/7. `synthesize_corpus(question, aspect, llm, prefilter, extract) -> Iterator[dict]` cohérent Tasks 5/6/8. `prefilter_documents(aspect, ...)` cohérent Tasks 3/5. `list_indexed_sources(db)` cohérent Tasks 1/3/7.

## Risques / à traiter

- **REDUCE non hiérarchique (v1).** À 50 documents produisant chacun beaucoup d'items, le prompt REDUCE peut dépasser `num_ctx`. Task 5 fait un REDUCE en un seul appel. Si la sonde (Task 8) montre un dépassement, ajouter un REDUCE par lots (réduire par groupes de N docs puis réduire les réductions) — amélioration ciblée, à mesurer d'abord (ne pas sur-ingénierer avant preuve).
- **Coût LLM.** O(nb docs pré-filtrés) appels pour le MAP + 1 (ou +lots) pour le REDUCE. Réservé à l'intention `aggregate` ; borné par `CORPUS_MAX_DOCS`. Le glass-box rend l'attente lisible.
- **Concurrence GPU.** `CORPUS_MAP_CONCURRENCY=2` par défaut ; à ajuster selon la charge Ollama (aligné sur `OLLAMA_NUM_PARALLEL`).
- **Qualité extraction** dépend des résumés RAPTOR par document ; documents ingérés sans RAPTOR retombent sur un échantillon de chunks (`_gather_material`).

## Notes d'exécution

- Ordre = dépendances (1→8). Tasks 1, 2 indépendantes ; 3 dépend de 1 ; 4 indépendante ; 5 dépend de 3+4+2 ; 6 dépend de 5 (+ intent 1a) ; 7 dépend de 1+4 ; 8 en dernier.
- Après 1b, poursuivre en autonomie : **Chantier 2** (périmètre & contexte : sélecteur UI « Tous », agent non collant, audit routeur ; réutilise `retrieval.intent`), puis **Chantier 3** (génération PDF/DOCX).
