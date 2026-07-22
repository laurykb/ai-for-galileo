# Vague 1 — Précalcul haut rendement (HyPE + en-têtes contextuels + parent-child) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve RAGAS retrieval quality (context recall/precision, faithfulness, answer relevancy, hit@k) by precomputing three ingestion/retrieval levers, each adopted only on proven net RAGAS gain.

**Architecture:** Three flag-gated techniques, measured independently against the frozen Vague 0 baseline. (1) **HyPE** — replace the current crude "embed questions *instead of* content" hack with proper per-question vectors that point back to their parent chunk, while restoring a real content vector. (2) **Contextual headers** — ensure the breadcrumb path is embedded (not just stored in content). (3) **Parent-child** — fix an existing top-k divergence, then enable + calibrate. Techniques 1 and 2 are coupled: both live in `indexing/embedding.py:build_embeddings`.

**Tech Stack:** Python 3.12, Ollama (`bge-m3` embeddings, `mistral-small3.2` gen), Chroma vector store, MongoDB, pytest. 100% offline.

## Global Constraints

- **RAGAS is the compass.** No technique is adopted without a net RAGAS gain (recall/precision/faithfulness/answer_relevancy/hit@k) at acceptable query latency. Measure before/after each technique with `PYTHONUTF8=1 RAGAS_JUDGE_CONCURRENCY=4 CORPUS_MAP_CONCURRENCY=4 python -m evals.run_eval --mode ragas`.
- **Frozen baseline (ragas-baseline-v0-final, 30 q):** context_recall=0.529 · context_precision=0.809 · faithfulness=0.495 · answer_relevancy=0.599 · keyword_hit_rate=0.656 · latency≈40 s/q. Stored in Mongo `eval_runs` id=6a60d0d645ad8dc54ca9c29d and `evals/last_eval.json`.
- **100% offline** — no external API, no query-time LLM added by these techniques.
- **Feature-flag idiom (verbatim):** `os.environ.get("NAME", "false").lower() in ("true", "1", "yes")` inside a `*_config` dict in `env_config.get_config()`, spread into `full_config`, then re-exported as a module constant near line 473-479.
- **Every technique behind a default-`false` flag.** A flag is flipped to `true` in `.env` only after its RAGAS run proves net gain; a losing technique stays `false` and the result is documented.
- **Re-ingest when the technique touches ingestion** (HyPE, headers): re-run the corpus ingest so Chroma/BM25/Mongo reflect the change before measuring.
- **TDD**: failing unit test first (offline, no Ollama/Mongo), watch it fail, minimal code, watch it pass, commit. RAGAS runs are separate measurement tasks.

---

## File Structure

- `env_config.py` — add `HYPE_ENABLED`, `HYPE_MAX_QUESTIONS`, `CONTEXT_HEADERS_ENABLED` flags (three-touch pattern). One responsibility: config.
- `indexing/embedding.py` — `build_embeddings` gains: (a) HyPE expansion into extra parent-pointing vectors, (b) optional breadcrumb prefix on embed-text. Extract a pure helper `build_embedding_units(docs)` returning the list of (id, document, embed_text, metadata) so the logic is unit-testable without Ollama.
- `retrieval/hype.py` — **new**: pure `resolve_hype_hits(ranked_ids, lookup)` that collapses `hype_question` hits onto their parent id with dedup (keep best rank). One responsibility: query-time HyPE resolution.
- `retrieval/retrieve.py` — call `resolve_hype_hits` right after the parallel retrievers, before RRF fusion; fix nothing else here for HyPE.
- `core/ask.py` — fix the parent-child top-k divergence (`_pc_active` must mirror the real gate).
- `tests/test_hype_embedding.py` — **new**: unit tests for `build_embedding_units`.
- `tests/test_hype_resolve.py` — **new**: unit tests for `resolve_hype_hits`.
- `tests/test_context_headers.py` — **new**: unit tests for header prefixing in `build_embedding_units`.
- `tests/test_ask_parent_child_topk.py` — **new**: unit test for the `_pc_active`/top-k fix.

---

## Task 1: Config flags for Vague 1

**Files:**
- Modify: `env_config.py` (config block near `:346`, exports near `:477`)
- Test: `tests/test_config_retrieval.py` (append)

**Interfaces:**
- Produces: module constants `HYPE_ENABLED: bool` (default False), `HYPE_MAX_QUESTIONS: int` (default 3), `CONTEXT_HEADERS_ENABLED: bool` (default False).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_config_retrieval.py
def test_vague1_flags_exist_and_default_off():
    import env_config as C
    assert C.HYPE_ENABLED is False
    assert C.CONTEXT_HEADERS_ENABLED is False
    assert isinstance(C.HYPE_MAX_QUESTIONS, int) and C.HYPE_MAX_QUESTIONS >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config_retrieval.py::test_vague1_flags_exist_and_default_off -v`
Expected: FAIL with `AttributeError: module 'env_config' has no attribute 'HYPE_ENABLED'`

- [ ] **Step 3: Write minimal implementation**

Add a config block next to `parent_child_config` (env_config.py ~:346):

```python
    vague1_config = {
        "HYPE_ENABLED": os.environ.get("HYPE_ENABLED", "false").lower() in ("true", "1", "yes"),
        "HYPE_MAX_QUESTIONS": int(os.environ.get("HYPE_MAX_QUESTIONS", "3")),
        "CONTEXT_HEADERS_ENABLED": os.environ.get("CONTEXT_HEADERS_ENABLED", "false").lower() in ("true", "1", "yes"),
    }
```

Spread it into `full_config` alongside `**parent_child_config` (~:394):

```python
        **vague1_config,
```

Re-export near the parent-child exports (~:479):

```python
HYPE_ENABLED = CONFIG["HYPE_ENABLED"]
HYPE_MAX_QUESTIONS = CONFIG["HYPE_MAX_QUESTIONS"]
CONTEXT_HEADERS_ENABLED = CONFIG["CONTEXT_HEADERS_ENABLED"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config_retrieval.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add env_config.py tests/test_config_retrieval.py
git commit -m "feat(vague1): flags HYPE_ENABLED / HYPE_MAX_QUESTIONS / CONTEXT_HEADERS_ENABLED (default off)"
```

---

## Task 2: HyPE + headers embedding units (pure helper)

Extract the embed-text decision into a pure, testable helper, and implement both HyPE expansion and the header prefix there. This is the heart of techniques 1 and 2.

**Files:**
- Modify: `indexing/embedding.py` (`build_embeddings`, currently reads `questions_str` at `:45-52`)
- Test: `tests/test_hype_embedding.py` (create), `tests/test_context_headers.py` (create)

**Interfaces:**
- Produces: `build_embedding_units(docs) -> list[dict]`, each unit `{"id": str, "document": str, "embed_text": str, "metadata": dict}`. Base content unit per chunk always present; HyPE question units added only when `HYPE_ENABLED`. Consumed by `build_embeddings` (embeds `embed_text`, stores `document`).
- Consumes: chunk `Document` objects with `.page_content` and `.metadata` (`id`, `questions` list, `questions_str`, `breadcrumb`, `source`, `section_idx`, `chunk_idx`, `chunk_type`).

**Design (current → new):**
- Today (embedding.py:45-52): if `questions_str` >20 chars, embed the questions blob *instead of* content — one vector, content never embedded. This both hides the breadcrumb-in-content and gives no content vector.
- New base unit: `embed_text = page_content` (which already carries `[breadcrumb]` in technical mode) — restores a real content vector. If `CONTEXT_HEADERS_ENABLED` and a `breadcrumb` exists but is absent from `page_content`, prepend `[{breadcrumb}]\n` to `embed_text` only (never to `document`, to avoid changing generation/citation text).
- New HyPE units (only if `HYPE_ENABLED`): for each of the first `HYPE_MAX_QUESTIONS` items in `metadata["questions"]`, emit a unit with `id=f"{chunk_id}::hype::{i}"`, `document=<parent page_content>` (so a hit returns usable parent text with zero query-time lookup), `embed_text=<the question>` (optionally header-prefixed when `CONTEXT_HEADERS_ENABLED`), `metadata=<copy of parent meta>` plus `chunk_type="hype_question"`, `parent_id=chunk_id`, and `id` kept as `chunk_id` in metadata so downstream `meta["id"]` resolves to the parent.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hype_embedding.py
from types import SimpleNamespace
import indexing.embedding as E


def _doc(cid, content, questions, breadcrumb=""):
    meta = {"id": cid, "questions": list(questions),
            "questions_str": " | ".join(questions), "source": "d.pdf",
            "section_idx": 1, "chunk_idx": 0, "chunk_type": "chunk",
            "breadcrumb": breadcrumb}
    return SimpleNamespace(page_content=content, metadata=meta)


def test_base_unit_embeds_content_not_questions(monkeypatch):
    monkeypatch.setattr(E, "HYPE_ENABLED", False)
    monkeypatch.setattr(E, "CONTEXT_HEADERS_ENABLED", False)
    units = E.build_embedding_units([_doc("c1", "le contenu du chunk", ["Q1 ?", "Q2 ?"])])
    assert len(units) == 1
    assert units[0]["id"] == "c1"
    assert units[0]["embed_text"] == "le contenu du chunk"   # NOT the questions blob
    assert units[0]["document"] == "le contenu du chunk"


def test_hype_adds_parent_pointing_question_units(monkeypatch):
    monkeypatch.setattr(E, "HYPE_ENABLED", True)
    monkeypatch.setattr(E, "HYPE_MAX_QUESTIONS", 3)
    monkeypatch.setattr(E, "CONTEXT_HEADERS_ENABLED", False)
    units = E.build_embedding_units([_doc("c1", "contenu", ["Q1 ?", "Q2 ?"])])
    base = [u for u in units if u["metadata"].get("chunk_type") != "hype_question"]
    hype = [u for u in units if u["metadata"].get("chunk_type") == "hype_question"]
    assert len(base) == 1 and len(hype) == 2
    assert all(u["metadata"]["parent_id"] == "c1" for u in hype)
    assert all(u["metadata"]["id"] == "c1" for u in hype)          # resolves to parent
    assert {u["embed_text"] for u in hype} == {"Q1 ?", "Q2 ?"}     # embed the question
    assert all(u["document"] == "contenu" for u in hype)           # return parent text
    assert all(u["id"].startswith("c1::hype::") for u in hype)     # unique chroma ids


def test_hype_respects_max_questions(monkeypatch):
    monkeypatch.setattr(E, "HYPE_ENABLED", True)
    monkeypatch.setattr(E, "HYPE_MAX_QUESTIONS", 1)
    monkeypatch.setattr(E, "CONTEXT_HEADERS_ENABLED", False)
    units = E.build_embedding_units([_doc("c1", "contenu", ["Q1 ?", "Q2 ?", "Q3 ?"])])
    assert sum(1 for u in units if u["metadata"].get("chunk_type") == "hype_question") == 1
```

```python
# tests/test_context_headers.py
from types import SimpleNamespace
import indexing.embedding as E


def _doc(cid, content, breadcrumb):
    return SimpleNamespace(page_content=content, metadata={
        "id": cid, "questions": [], "questions_str": "", "source": "d.pdf",
        "section_idx": 1, "chunk_idx": 0, "chunk_type": "chunk",
        "breadcrumb": breadcrumb})


def test_header_prefixes_embed_text_only(monkeypatch):
    monkeypatch.setattr(E, "HYPE_ENABLED", False)
    monkeypatch.setattr(E, "CONTEXT_HEADERS_ENABLED", True)
    units = E.build_embedding_units([_doc("c1", "AES-256 en 6.2.1", "Crypto > 6.2.1")])
    assert units[0]["embed_text"].startswith("[Crypto > 6.2.1]")
    assert units[0]["document"] == "AES-256 en 6.2.1"    # generation text untouched


def test_header_not_duplicated_when_already_in_content(monkeypatch):
    monkeypatch.setattr(E, "HYPE_ENABLED", False)
    monkeypatch.setattr(E, "CONTEXT_HEADERS_ENABLED", True)
    units = E.build_embedding_units([_doc("c1", "[Crypto > 6.2.1]\nAES-256", "Crypto > 6.2.1")])
    assert units[0]["embed_text"].count("[Crypto > 6.2.1]") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_hype_embedding.py tests/test_context_headers.py -v`
Expected: FAIL with `AttributeError: module 'indexing.embedding' has no attribute 'build_embedding_units'`

- [ ] **Step 3: Write minimal implementation**

In `indexing/embedding.py`, import the flags at top (`from env_config import HYPE_ENABLED, HYPE_MAX_QUESTIONS, CONTEXT_HEADERS_ENABLED`) and add:

```python
def _prefix_header(text, breadcrumb):
    """Préfixe [breadcrumb] au texte à embarquer si absent (idempotent)."""
    if not (CONTEXT_HEADERS_ENABLED and breadcrumb):
        return text
    tag = f"[{breadcrumb}]"
    return text if tag in text else f"{tag}\n{text}"


def build_embedding_units(docs):
    """Unités à indexer : 1 vecteur contenu par chunk (+ vecteurs HyPE par question
    pointant vers le parent si HYPE_ENABLED). Pur : aucune dépendance Ollama/Mongo."""
    units = []
    for d in docs:
        content = d.page_content.strip()
        if not content:
            continue
        meta = dict(d.metadata)
        cid = meta["id"]
        breadcrumb = meta.get("breadcrumb", "")
        units.append({
            "id": cid,
            "document": content,
            "embed_text": _prefix_header(content, breadcrumb),
            "metadata": meta,
        })
        if HYPE_ENABLED:
            questions = [q for q in (meta.get("questions") or []) if q and q.strip()]
            for i, q in enumerate(questions[:HYPE_MAX_QUESTIONS]):
                hmeta = dict(meta)
                hmeta["chunk_type"] = "hype_question"
                hmeta["parent_id"] = cid
                hmeta["id"] = cid  # résout vers le parent en aval
                units.append({
                    "id": f"{cid}::hype::{i}",
                    "document": content,
                    "embed_text": _prefix_header(q.strip(), breadcrumb),
                    "metadata": hmeta,
                })
    return units
```

Then refactor `build_embeddings` to consume the units (replace the `texts`/`texts_to_embed` block at ~:42-54). Keep the existing metadata-sanitizing loop, but drive it from `units`:

```python
    docs = [d for d in docs if d.page_content.strip()]
    units = build_embedding_units(docs)
    texts = [u["document"] for u in units]
    texts_to_embed = [u["embed_text"] for u in units]
    ids = [u["id"] for u in units]
    raw_metadatas = [u["metadata"] for u in units]
    vecs = model.embed_documents(texts_to_embed)
    # ... existing sanitize loop, but iterate raw_metadatas instead of docs,
    #     and DELETE the old `ids = [d.metadata["id"] for d in docs]` line.
```

Adjust the sanitize loop to iterate `raw_metadatas` (each already a dict) rather than `docs`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_hype_embedding.py tests/test_context_headers.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add indexing/embedding.py tests/test_hype_embedding.py tests/test_context_headers.py
git commit -m "feat(vague1): build_embedding_units — HyPE parent-pointing vectors + optional header prefix (embed-only)"
```

---

## Task 3: Query-time HyPE resolution (collapse to parent + dedup)

**Files:**
- Create: `retrieval/hype.py`
- Modify: `retrieval/retrieve.py` (after `_run_parallel_retrievers`, ~:117-124, before `fuse_with_rrf` ~:147)
- Test: `tests/test_hype_resolve.py` (create)

**Interfaces:**
- Produces: `resolve_hype_hits(ranked_ids: list[str], lookup: dict) -> tuple[list[str], dict]`. Collapses any hit whose `lookup[id]["meta"]["chunk_type"] == "hype_question"` onto `meta["parent_id"]`, dedups (best rank wins), preserves order. Non-HyPE hits pass through unchanged.
- Consumes: the `(ranked_ids, lookup)` produced by `run_semantic_for_query` where `lookup[id] = {"doc", "meta", "distance"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hype_resolve.py
from retrieval.hype import resolve_hype_hits


def _lk(doc, meta, dist):
    return {"doc": doc, "meta": meta, "distance": dist}


def test_hype_hit_collapses_to_parent():
    ranked = ["p1::hype::0", "p2"]
    lookup = {
        "p1::hype::0": _lk("contenu p1", {"id": "p1", "parent_id": "p1", "chunk_type": "hype_question"}, 0.1),
        "p2": _lk("contenu p2", {"id": "p2", "chunk_type": "chunk"}, 0.3),
    }
    ids, lk = resolve_hype_hits(ranked, lookup)
    assert ids == ["p1", "p2"]
    assert lk["p1"]["doc"] == "contenu p1"
    assert lk["p1"]["meta"]["chunk_type"] == "chunk"  # normalised away from hype marker


def test_dedup_keeps_best_ranked_parent():
    ranked = ["p1::hype::0", "p1", "p1::hype::1"]
    lookup = {
        "p1::hype::0": _lk("c", {"id": "p1", "parent_id": "p1", "chunk_type": "hype_question"}, 0.1),
        "p1": _lk("c", {"id": "p1", "chunk_type": "chunk"}, 0.2),
        "p1::hype::1": _lk("c", {"id": "p1", "parent_id": "p1", "chunk_type": "hype_question"}, 0.4),
    }
    ids, lk = resolve_hype_hits(ranked, lookup)
    assert ids == ["p1"]                 # one entry, first (best) occurrence wins order
    assert lk["p1"]["distance"] == 0.1   # best distance retained
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_hype_resolve.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'retrieval.hype'`

- [ ] **Step 3: Write minimal implementation**

```python
# retrieval/hype.py
"""Résolution HyPE à la requête : un hit sur un vecteur-question est remonté vers
son chunk parent, avec déduplication (meilleur rang/score conservé). Pur, offline."""


def resolve_hype_hits(ranked_ids, lookup):
    new_ranked = []
    new_lookup = {}
    for cid in ranked_ids:
        entry = lookup.get(cid) or {}
        meta = dict(entry.get("meta") or {})
        if meta.get("chunk_type") == "hype_question":
            pid = meta.get("parent_id") or meta.get("id") or cid
            meta["chunk_type"] = "chunk"          # normalise pour l'aval (parent-child, etc.)
            meta.pop("parent_id", None)
        else:
            pid = cid
        cand = {"doc": entry.get("doc", ""), "meta": meta, "distance": entry.get("distance")}
        if pid not in new_lookup:
            new_ranked.append(pid)
            new_lookup[pid] = cand
        else:
            # dedup : garde la meilleure distance (plus petite) ; l'ordre reste au 1er hit.
            prev = new_lookup[pid].get("distance")
            cur = cand.get("distance")
            if cur is not None and (prev is None or cur < prev):
                new_lookup[pid]["distance"] = cur
    return new_ranked, new_lookup
```

Wire into `retrieval/retrieve.py`. After the parallel retrievers produce `sem_ids, sem_lookup` (~:117-124) and before fusion, add:

```python
from retrieval.hype import resolve_hype_hits   # top of file
from env_config import HYPE_ENABLED             # top of file
...
    if HYPE_ENABLED:
        sem_ids, sem_lookup = resolve_hype_hits(sem_ids, sem_lookup)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_hype_resolve.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite (no regression)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add retrieval/hype.py retrieval/retrieve.py tests/test_hype_resolve.py
git commit -m "feat(vague1): resolve_hype_hits — collapse question vectors to parent + dedup, wired in hybrid_retrieve"
```

---

## Task 4: MEASURE HyPE (RAGAS before/after)

Integration/measurement task — no unit test; the "before" is the frozen baseline.

- [ ] **Step 1: Re-ingest the corpus with HyPE on** (touches ingestion → mandatory re-ingest)

```bash
# In .env set HYPE_ENABLED=true (leave CONTEXT_HEADERS_ENABLED=false for isolation),
# then re-ingest the demo corpus and rebuild indexes.
# Use the project's ingest entrypoint (scripts/ or core.ingest). Confirm Chroma now
# holds parent + hype_question vectors:
.venv/bin/python diagnostic.py
```

- [ ] **Step 2: Run RAGAS on the full golden set**

```bash
PYTHONUTF8=1 RAGAS_JUDGE_CONCURRENCY=4 CORPUS_MAP_CONCURRENCY=4 \
  .venv/bin/python -m evals.run_eval --mode ragas --name ragas-vague1-hype
```

- [ ] **Step 3: Compare to baseline** — read the printed non-regression block + `evals/last_eval.json`. Record context_recall / context_precision / faithfulness / answer_relevancy / keyword_hit_rate / latency deltas vs baseline in the ledger.

- [ ] **Step 4: Decision + ledger** — keep `HYPE_ENABLED=true` only on net gain (esp. recall / hit@k) without unacceptable latency; otherwise set it back to `false`. Append the numbers and the verdict to `.superpowers/sdd/progress.md`. Commit the doc/decision (not `.env`, which is gitignored/local).

---

## Task 5: MEASURE contextual headers (RAGAS before/after)

Only the embed-text changes (query path unchanged), but embeddings change → re-ingest.

- [ ] **Step 1: Re-ingest with headers on** — set `CONTEXT_HEADERS_ENABLED=true` in `.env` (keep HyPE at whatever Task 4 concluded), re-ingest, `diagnostic.py`.

- [ ] **Step 2: Run RAGAS**

```bash
PYTHONUTF8=1 RAGAS_JUDGE_CONCURRENCY=4 CORPUS_MAP_CONCURRENCY=4 \
  .venv/bin/python -m evals.run_eval --mode ragas --name ragas-vague1-headers
```

- [ ] **Step 3: Compare + decide + ledger** — target ↑ context_precision. Keep the flag only on net gain; document either way.

---

## Task 6: Fix parent-child top-k divergence

Prereq for a fair parent-child measurement: today `core/ask.py:194-195` sets `_topk = NUM_CHUNKS_PARENT_CHILD` (8) whenever the caller passes `parent_child_on=None`, but the actual expansion in `retrieve.py:181` gates on `PARENT_CHILD_ENABLED` (default false). So with PC off, top-k is silently shrunk to 8 with no expansion. Make `_pc_active` mirror the real gate.

**Files:**
- Modify: `core/ask.py` (`_prepare_retrieval`, ~:194-195)
- Test: `tests/test_ask_parent_child_topk.py` (create)

**Interfaces:**
- Consumes: `env_config.PARENT_CHILD_ENABLED`, `NUM_CHUNKS`, `NUM_CHUNKS_PARENT_CHILD`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ask_parent_child_topk.py
import core.ask as A
import env_config as C


def test_topk_follows_real_pc_gate_when_caller_neutral(monkeypatch):
    """Quand le caller ne force rien (None), le top-k doit suivre PARENT_CHILD_ENABLED,
    pas supposer PC actif."""
    monkeypatch.setattr(A, "PARENT_CHILD_ENABLED", False, raising=False)
    monkeypatch.setattr(C, "PARENT_CHILD_ENABLED", False, raising=False)
    assert A._effective_topk(parent_child_on=None) == C.NUM_CHUNKS

    monkeypatch.setattr(A, "PARENT_CHILD_ENABLED", True, raising=False)
    monkeypatch.setattr(C, "PARENT_CHILD_ENABLED", True, raising=False)
    assert A._effective_topk(parent_child_on=None) == C.NUM_CHUNKS_PARENT_CHILD


def test_explicit_override_wins():
    assert A._effective_topk(parent_child_on=True) == C.NUM_CHUNKS_PARENT_CHILD
    assert A._effective_topk(parent_child_on=False) == C.NUM_CHUNKS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ask_parent_child_topk.py -v`
Expected: FAIL with `AttributeError: module 'core.ask' has no attribute '_effective_topk'`

- [ ] **Step 3: Write minimal implementation**

Extract the top-k decision into a helper and use it in `_prepare_retrieval`. Ensure `PARENT_CHILD_ENABLED` is imported in `core/ask.py`.

```python
def _effective_topk(parent_child_on):
    """Top-k cohérent avec le vrai gate PC. None => suit PARENT_CHILD_ENABLED."""
    active = PARENT_CHILD_ENABLED if parent_child_on is None else parent_child_on
    return NUM_CHUNKS_PARENT_CHILD if active else NUM_CHUNKS
```

Replace the `ask.py:194-195` block:

```python
    _topk = _effective_topk(parent_child_on)
```

- [ ] **Step 4: Run test + full suite**

Run: `.venv/bin/python -m pytest tests/test_ask_parent_child_topk.py -q && .venv/bin/python -m pytest -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add core/ask.py tests/test_ask_parent_child_topk.py
git commit -m "fix(vague1): parent-child top-k follows real PARENT_CHILD_ENABLED gate (no silent shrink when off)"
```

---

## Task 7: MEASURE parent-child (enable + calibrate, RAGAS)

No re-ingest needed (retrieval-only). Calibrate `NUM_CHUNKS_PARENT_CHILD` and `PARENT_CHILD_MAX_CHARS` for the latency/faithfulness trade.

- [ ] **Step 1: Enable** — set `PARENT_CHILD_ENABLED=true` in `.env`.

- [ ] **Step 2: Run RAGAS**

```bash
PYTHONUTF8=1 RAGAS_JUDGE_CONCURRENCY=4 CORPUS_MAP_CONCURRENCY=4 \
  .venv/bin/python -m evals.run_eval --mode ragas --name ragas-vague1-parentchild
```

- [ ] **Step 3: Compare (esp. faithfulness ↑ vs latency ↑)**. If latency spikes, lower `PARENT_CHILD_MAX_CHARS` (e.g. 4000→2500) and/or `NUM_CHUNKS_PARENT_CHILD`, re-run. Record each calibration point in the ledger.

- [ ] **Step 4: Decide + ledger** — keep enabled only on net gain at acceptable latency; document either way.

---

## Task 8: Combination sweep + final review

- [ ] **Step 1: Best-combo RAGAS** — with the winning flags from Tasks 4/5/7 all set, re-ingest if any ingestion-touching flag changed, run `--mode ragas --name ragas-vague1-combo`. The combination can differ from the sum of parts — this run is authoritative.

- [ ] **Step 2: Qualitative north-star probe** — run the use case "crée une table catégorisant tous les types d'attaquant" against the corpus; confirm coverage/exhaustiveness did not regress.

- [ ] **Step 3: Full suite green** — `.venv/bin/python -m pytest -q`.

- [ ] **Step 4: Update RESUME + ledger** — record the adopted Vague 1 configuration and the new post-Vague-1 RAGAS numbers as the reference for Vague 2. Mark Vague 1 done.

- [ ] **Step 5: Final Opus review** — request a merge-ready review of the Vague 1 diff (correctness of HyPE parent resolution + dedup, no generation-text leakage from headers, parent-child calibration bounds). Address Critical/Important before adopting.

---

## Self-Review (spec coverage)

- HyPE (spec §1) → Tasks 2, 3, 4. ✅ (embed questions as parent-pointing vectors; resolve+dedup at query; measure)
- Contextual headers (spec §2) → Tasks 2 (embed-only prefix, no generation leakage), 5 (measure). ✅ Reconciles with the discovered fact that breadcrumb is already in `page_content`.
- Parent-child (spec §3) → Tasks 6 (fix divergence), 7 (enable+calibrate+measure). ✅
- Measurement protocol (spec §"Protocole de mesure") → Tasks 4/5/7 before/after + Task 8 combination re-eval. ✅
- Verification (spec §"Vérification") → offline unit tests (Tasks 1-3, 6), re-ingest + RAGAS (4/5/7/8), full suite green (3/6/8), north-star probe (8). ✅
- Risks (spec §"Risques") → HyPE index bloat bounded by `HYPE_MAX_QUESTIONS` (Task 1); dedup correctness (Task 3 tests); header verbosity kept embed-only (Task 2); parent-child latency calibration (Task 7). ✅
