# Chantier 1a — Qualité du retrieval (Q&A pointue) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Réparer le retrieval multi-document pointu — les petits documents remontent, les questions génériques répondent au lieu d'abstenir, et l'ingestion reste fiable — sans casser la Q&A existante.

**Architecture:** On garde le pipeline hybride existant (sémantique + BM25 → RRF → cross-encoder) mais on (1) élargit le pool de candidats avant rerank, (2) applique un *plancher de couverture par document* après rerank quand le périmètre est « Tous », (3) rééquilibre les poids, (4) recalibre l'abstention et la court-circuite sur les questions exploratoires via un détecteur d'intention sans LLM, (5) fiabilise l'ingestion (rejet des chunks vides + invalidation BM25).

**Tech Stack:** Python 3.12, ChromaDB (HNSW), MongoDB (`bm25_indexes`), `rank_bm25`, cross-encoder bge-reranker-v2-m3 local, Ollama. Tests : pytest (offline, déterministe).

## Global Constraints

- **100 % offline** : aucun téléchargement, aucun nouveau service ni dépendance pip. Tout tourne sur Ollama + Chroma + Mongo + Docling déjà installés (`openpyxl`/`python-docx`/`pandas` présents en secours).
- **Élasticité** : les réglages doivent être bons du petit corpus (2-5 docs) au gros (jusqu'au plafond **50 docs × 100-200 pages ≈ 15-22k chunks**). Le pool de candidats est **dérivé de la taille réelle du corpus** (nb de docs + nb de chunks, lus gratuitement dans le `bm25_tuple`), borné. Pas de constante figée bonne pour 2 docs et mauvaise pour 50.
- **Tous les nouveaux réglages passent par `env_config.py`** et sont surchargeables via `.env` (pattern existant : dict de config → export global).
- **Ne jamais désaligner `ids/texts/metadatas`** dans BM25 (cf. `tests/test_bm25.py`).
- **Le plancher par document ne s'applique QUE si `source_filter is None`** (périmètre « Tous ») et **ne doit jamais injecter un document non pertinent** (garde `floor_min_ce`).
- **Valeurs de réglage = points de départ** à affiner via `python -m evals.run_eval --mode retrieval` ; le plan fige la mécanique, pas les chiffres.
- Style de test : offline, déterministe, dataclass `_Doc(page_content, metadata)` comme dans `tests/test_bm25.py`.

---

### Task 1: Détecteur d'intention (aiguillage sans LLM)

Classe une question en `aggregate` (synthèse corpus-large — servira le Chantier 1b), `exploratory` (générique/définitionnel — ne pas abstenir) ou `pointed` (Q&A factuelle). Réutilise le style `_norm` de `core/router.py`.

**Files:**
- Create: `retrieval/intent.py`
- Test: `tests/test_intent.py`

**Interfaces:**
- Consumes: rien (module autonome).
- Produces:
  - `classify_intent(question: str) -> str` — renvoie `"aggregate"` | `"exploratory"` | `"pointed"`.
  - `is_exploratory(question: str) -> bool`
  - `is_aggregate(question: str) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intent.py
"""Tests du détecteur d'intention — 100 % hors-ligne, déterministe."""
from retrieval.intent import classify_intent, is_exploratory, is_aggregate


def test_aggregate_intent():
    assert classify_intent("Catégorise toutes les attaques connues de ce corpus") == "aggregate"
    assert classify_intent("Liste-moi l'ensemble des exigences du corpus") == "aggregate"
    assert is_aggregate("Recense toutes les menaces dans tous les documents") is True


def test_exploratory_intent():
    assert classify_intent("C'est quoi un IDS ?") == "exploratory"
    assert classify_intent("De quoi parle ce document ?") == "exploratory"
    assert is_exploratory("Résume la cible de sécurité") is True


def test_pointed_intent():
    assert classify_intent("Quelles sont les exigences de chiffrement ?") == "pointed"
    assert classify_intent("Quel est le niveau EAL de la TOE ?") == "pointed"
    assert is_exploratory("Quel est le niveau EAL de la TOE ?") is False
    assert is_aggregate("Quelles sont les exigences de chiffrement ?") is False


def test_aggregate_wins_over_exploratory():
    # "résume TOUTES les attaques" est agrégatif, pas juste exploratoire
    assert classify_intent("Résume toutes les attaques du corpus") == "aggregate"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_intent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'retrieval.intent'`

- [ ] **Step 3: Write minimal implementation**

```python
# retrieval/intent.py
"""Détecteur d'intention de requête — aiguille RAG pointu / exploratoire / synthèse
corpus, SANS appel LLM (regex sur texte normalisé). Source unique d'aiguillage,
partagée avec l'audit du routeur (Chantier 2)."""
from __future__ import annotations

import re
import unicodedata


def _norm(text: str) -> str:
    """Minuscule + sans accents (comme core.router._norm)."""
    t = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


# Agrégation corpus-large : « toutes les X », « catégorise », « du corpus »...
_AGGREGATE = re.compile(
    r"\b(tous les|toutes les|l'ensemble des|chaque (document|attaque|menace|exigence|"
    r"categorie)|recense|inventaire|categorise|categoriser|enumere|enumerer|"
    r"classe (les|toutes|tous)|liste (moi|nous|les|toutes|tous|l')|"
    r"dans tout le corpus|sur tout le corpus|(de|du|dans le) corpus|"
    r"a travers (les|tous|toutes))")

# Générique / définitionnel : on ne doit PAS abstenir dessus.
_EXPLORATORY = re.compile(
    r"\b(de quoi (parle|traite|s'agit)|c'est quoi|qu'est ce que|qu'est-ce que|"
    r"quel est le sujet|que dit (le|ce) document|resume|resumer|resumé|synthese|"
    r"presente|presentation|explique|explication|vue d'ensemble|apercu|generalites)")


def is_aggregate(question: str) -> bool:
    return bool(_AGGREGATE.search(_norm(question)))


def is_exploratory(question: str) -> bool:
    return bool(_EXPLORATORY.search(_norm(question)))


def classify_intent(question: str) -> str:
    """Renvoie 'aggregate' | 'exploratory' | 'pointed'. L'agrégation prime sur
    l'exploratoire (« résume toutes les X » = agrégatif)."""
    if is_aggregate(question):
        return "aggregate"
    if is_exploratory(question):
        return "exploratory"
    return "pointed"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_intent.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add retrieval/intent.py tests/test_intent.py
git commit -m "feat(retrieval): détecteur d'intention aggregate/exploratory/pointed"
```

---

### Task 2: Nouveaux réglages de retrieval (`env_config.py`)

Ajoute les knobs du plancher/pool et recalibre poids + seuil.

**Files:**
- Modify: `env_config.py` (bloc `retrieval_config` ~L251-258, bloc `ce_config` ~L247, exports ~L419-424)
- Test: `tests/test_config_retrieval.py`

**Interfaces:**
- Produces (globals importables depuis `env_config`) : `CANDIDATE_POOL_MIN: int`, `CANDIDATE_POOL_MAX: int`, `CANDIDATE_POOL_PER_DOC: int`, `PER_DOC_FLOOR: int`, `MAX_CHUNKS: int` ; valeurs ajustées `WEIGHT_SEMANTIC=0.5`, `WEIGHT_BM25=0.5`, `CE_RELEVANCE_THRESHOLD=0.48`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_retrieval.py
"""Vérifie que les nouveaux réglages de retrieval (élastiques) sont exportés et typés."""
import env_config as ec


def test_new_retrieval_knobs_exist():
    # Pool de candidats ÉLASTIQUE : bornes + incrément par document.
    assert isinstance(ec.CANDIDATE_POOL_MIN, int) and ec.CANDIDATE_POOL_MIN >= ec.NUM_CHUNKS
    assert isinstance(ec.CANDIDATE_POOL_MAX, int) and ec.CANDIDATE_POOL_MAX >= ec.CANDIDATE_POOL_MIN
    assert isinstance(ec.CANDIDATE_POOL_PER_DOC, int) and ec.CANDIDATE_POOL_PER_DOC >= 0
    assert isinstance(ec.PER_DOC_FLOOR, int) and ec.PER_DOC_FLOOR >= 1
    assert isinstance(ec.MAX_CHUNKS, int) and ec.MAX_CHUNKS >= ec.NUM_CHUNKS


def test_rebalanced_defaults():
    # Poids rééquilibrés (départ 0.5/0.5) et somme cohérente.
    assert abs((ec.WEIGHT_SEMANTIC + ec.WEIGHT_BM25) - 1.0) < 1e-6
    assert ec.WEIGHT_SEMANTIC >= 0.4
    # Seuil recalibré sous 0.5 (marge au-dessus du plancher de bruit gérée par l'intention).
    assert ec.CE_RELEVANCE_THRESHOLD < 0.505
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_retrieval.py -v`
Expected: FAIL — `AttributeError: module 'env_config' has no attribute 'CANDIDATE_POOL'`

- [ ] **Step 3: Write minimal implementation**

Dans `env_config.py`, bloc `retrieval_config` — ajouter les trois knobs et changer les deux poids :

```python
    retrieval_config = {
        "NUM_CHUNKS": int(os.environ.get("NUM_CHUNKS", "15")),
        "RRF_K": int(os.environ.get("RRF_K", "60")),
        "WEIGHT_SEMANTIC": float(os.environ.get("WEIGHT_SEMANTIC", "0.5")),
        "WEIGHT_BM25": float(os.environ.get("WEIGHT_BM25", "0.5")),
        "MAX_CHUNK_LENGTH": int(os.environ.get("MAX_CHUNK_LENGTH", "25000")),
        "MAX_QUERY_CHARS": int(os.environ.get("MAX_QUERY_CHARS", "512")),
        # Pool de candidats ÉLASTIQUE remonté par branche AVANT rerank : borne basse
        # (petit corpus, rapide), incrément par document, borne haute (protège le
        # coût du rerank sur gros corpus). pool = clamp(MIN, MIN + PER_DOC*n_docs, MAX).
        "CANDIDATE_POOL_MIN": int(os.environ.get("CANDIDATE_POOL_MIN", "40")),
        "CANDIDATE_POOL_MAX": int(os.environ.get("CANDIDATE_POOL_MAX", "120")),
        "CANDIDATE_POOL_PER_DOC": int(os.environ.get("CANDIDATE_POOL_PER_DOC", "3")),
        # Plancher de couverture : chunks min garantis par document pertinent (mode « Tous »).
        "PER_DOC_FLOOR": int(os.environ.get("PER_DOC_FLOOR", "1")),
        # Plafond du top-k final après plancher (protège le contexte de génération).
        "MAX_CHUNKS": int(os.environ.get("MAX_CHUNKS", "24")),
    }
```

Dans `ce_config`, recalibrer le seuil :

```python
        "CE_RELEVANCE_THRESHOLD": float(os.environ.get("CE_RELEVANCE_THRESHOLD", "0.48")),
```

Dans la section EXPORTS (après `MAX_QUERY_CHARS = CONFIG["MAX_QUERY_CHARS"]`, ~L424) :

```python
CANDIDATE_POOL_MIN = CONFIG["CANDIDATE_POOL_MIN"]
CANDIDATE_POOL_MAX = CONFIG["CANDIDATE_POOL_MAX"]
CANDIDATE_POOL_PER_DOC = CONFIG["CANDIDATE_POOL_PER_DOC"]
PER_DOC_FLOOR = CONFIG["PER_DOC_FLOOR"]
MAX_CHUNKS = CONFIG["MAX_CHUNKS"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config_retrieval.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add env_config.py tests/test_config_retrieval.py
git commit -m "feat(config): knobs pool/plancher + rééquilibrage poids et seuil abstention"
```

---

### Task 3: Plancher de couverture par document + dimensionnement élastique

Deux fonctions pures, testables offline : (a) le plancher de couverture, (b) le calcul élastique du pool de candidats en fonction de la taille du corpus.

**Files:**
- Create: `retrieval/coverage.py`
- Test: `tests/test_coverage.py`

**Interfaces:**
- Consumes: rien (fonctions pures).
- Produces:
  - `apply_coverage_floor(items: list[dict], base_k: int, per_doc_floor: int = 1, max_chunks: int = 24, floor_min_ce: float | None = None) -> list[dict]` — chaque `item` est un chunk du pipeline : dict avec `item["meta"]["source"]` et `item["ce_score"]` ; `items` supposé **trié par `ce_score` décroissant**.
  - `elastic_candidate_pool(n_docs: int, pool_min: int, pool_max: int, per_doc: int) -> int` — `clamp(pool_min, pool_min + per_doc * n_docs, pool_max)`. Petit corpus → proche de `pool_min` ; gros corpus → plafonné à `pool_max`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_coverage.py
"""Tests du plancher de couverture par document — offline, déterministe."""
from retrieval.coverage import apply_coverage_floor


def _mk(src, ce, i):
    return {"doc": f"{src}-{i}", "meta": {"source": src}, "ce_score": ce}


def _big(n=15):
    return [_mk("BIG", 0.90 - i * 0.01, i) for i in range(n)]


def test_small_doc_floored_in():
    items = _big(15) + [_mk("SMALL", 0.55, 99)]  # SMALL dernier, hors top-15
    out = apply_coverage_floor(items, base_k=15, per_doc_floor=1, max_chunks=24, floor_min_ce=0.5)
    assert "SMALL" in {it["meta"]["source"] for it in out}
    assert len(out) <= 24


def test_irrelevant_doc_not_floored():
    items = _big(15) + [_mk("SMALL", 0.30, 99)]  # sous le seuil floor_min_ce
    out = apply_coverage_floor(items, base_k=15, per_doc_floor=1, max_chunks=24, floor_min_ce=0.5)
    assert "SMALL" not in {it["meta"]["source"] for it in out}


def test_no_change_when_all_present():
    items = [_mk("A", 0.9, 0), _mk("B", 0.8, 1), _mk("A", 0.7, 2)]
    out = apply_coverage_floor(items, base_k=3, per_doc_floor=1, max_chunks=24, floor_min_ce=None)
    assert len(out) == 3


def test_cap_respected_and_floored_survive():
    # base_k=3 -> top3 tous BIG ; SMALL doit entrer malgré le plafond serré.
    items = _big(5) + [_mk("SMALL", 0.40, 99)]
    out = apply_coverage_floor(items, base_k=3, per_doc_floor=1, max_chunks=3, floor_min_ce=0.3)
    assert len(out) <= 3
    assert "SMALL" in {it["meta"]["source"] for it in out}


def test_empty_input():
    assert apply_coverage_floor([], base_k=15) == []


def test_elastic_pool_scales_with_corpus():
    from retrieval.coverage import elastic_candidate_pool
    # Petit corpus -> proche de la borne basse.
    assert elastic_candidate_pool(2, pool_min=40, pool_max=120, per_doc=3) == 46
    # Gros corpus -> plafonné à la borne haute.
    assert elastic_candidate_pool(50, pool_min=40, pool_max=120, per_doc=3) == 120
    # Corpus vide/inconnu -> borne basse.
    assert elastic_candidate_pool(0, pool_min=40, pool_max=120, per_doc=3) == 40
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_coverage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'retrieval.coverage'`

- [ ] **Step 3: Write minimal implementation**

```python
# retrieval/coverage.py
"""Plancher de couverture par document : garantit qu'un document pertinent, même
petit, remonte dans le contexte final quand le périmètre est « Tous ». Appliqué
APRÈS rerank, sur des chunks triés par ce_score décroissant."""
from __future__ import annotations


def _src(item: dict):
    return (item.get("meta") or {}).get("source")


def _ce(item: dict) -> float:
    return item.get("ce_score", 0.0)


def elastic_candidate_pool(n_docs, pool_min, pool_max, per_doc):
    """Taille élastique du pool de candidats : petit corpus -> rapide (proche de
    pool_min), gros corpus -> plafonné (pool_max). Dérivée gratuitement du nombre
    de documents du corpus (aucune requête supplémentaire)."""
    return max(pool_min, min(pool_min + per_doc * max(0, n_docs), pool_max))


def apply_coverage_floor(items, base_k, per_doc_floor=1, max_chunks=24, floor_min_ce=None):
    """Complète le top `base_k` global pour garantir `per_doc_floor` chunks par
    source éligible (ce_score >= floor_min_ce si fourni), sans jamais dépasser
    `max_chunks` ni écarter un chunk planché. `items` supposé trié ce_score desc."""
    if not items:
        return items

    selected = list(items[:base_k])
    picked = {id(x) for x in selected}
    per_source = {}
    for it in selected:
        per_source[_src(it)] = per_source.get(_src(it), 0) + 1

    floored = []
    for it in items:
        s = _src(it)
        if per_source.get(s, 0) >= per_doc_floor:
            continue
        if floor_min_ce is not None and _ce(it) < floor_min_ce:
            continue
        if id(it) in picked:
            continue
        floored.append(it)
        picked.add(id(it))
        per_source[s] = per_source.get(s, 0) + 1

    result = selected + floored
    if len(result) > max_chunks:
        floored_ids = {id(x) for x in floored}
        protected = [x for x in result if id(x) in floored_ids]
        others = [x for x in result if id(x) not in floored_ids]
        others.sort(key=_ce, reverse=True)
        keep_others = others[: max(0, max_chunks - len(protected))]
        result = keep_others + protected

    result.sort(key=_ce, reverse=True)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_coverage.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add retrieval/coverage.py tests/test_coverage.py
git commit -m "feat(retrieval): plancher de couverture par document"
```

---

### Task 4: Câbler pool élastique + plancher dans `hybrid_retrieve`

Sépare le *pool de candidats* (retrievers + RRF) du *top-k final*. Le pool est **élastique** : dérivé du nombre de documents présents dans le `bm25_tuple`. Le plancher s'applique après rerank quand `source_filter is None`.

**Files:**
- Modify: `retrieval/retrieve.py` (signature `hybrid_retrieve` ~L71-83, corps ~L91-152)
- Test: `tests/test_hybrid_coverage.py`

**Interfaces:**
- Consumes: `retrieval.coverage.apply_coverage_floor` + `elastic_candidate_pool` (Task 3) ; `env_config.CANDIDATE_POOL_MIN/MAX/PER_DOC`, `PER_DOC_FLOOR`, `MAX_CHUNKS`, `CE_RELEVANCE_THRESHOLD` (Task 2).
- Produces: `hybrid_retrieve(..., candidate_pool: int | None = None, ...)` — si `None` (défaut), le pool est calculé élastiquement depuis le corpus ; un entier explicite force la valeur (tests). Retour toujours `(fused_chunks, max_ce_score)`. Ajoute un helper `_count_sources(bm25_tuple) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hybrid_coverage.py
"""hybrid_retrieve applique le plancher de couverture après rerank (mode « Tous »).
On patch les étages coûteux (retrievers + cross-encoder) pour rester offline."""
import retrieval.retrieve as R


def _mk(src, ce, i):
    return {"doc": f"{src}-{i}", "meta": {"source": src}, "ce_score": ce}


def test_small_doc_surfaces_via_floor(monkeypatch):
    # 15 chunks BIG + 1 SMALL en dernier (hors top-15) après "rerank".
    canned = [_mk("BIG", 0.90 - i * 0.01, i) for i in range(15)] + [_mk("SMALL", 0.55, 99)]

    monkeypatch.setattr(R, "_run_parallel_retrievers",
                        lambda **kw: (["s0"], {"s0": {"doc": "x", "meta": {"source": "BIG"}, "distance": 0.1}}, [], {}))
    monkeypatch.setattr(R, "_maybe_apply_cross_encoder", lambda **kw: (canned, 0.90))

    out, max_ce = R.hybrid_retrieve(
        collection=None, query="peu importe", bm25_tuple=None,
        topk_chunks=15, source_filter=None, parent_child_on=False, debug=False,
    )
    assert "SMALL" in {c["meta"]["source"] for c in out}
    assert max_ce == 0.90
    assert len(out) <= R.MAX_CHUNKS


def test_candidate_pool_is_elastic(monkeypatch):
    # Capture le pool réellement demandé aux retrievers ; il doit croître avec le
    # nombre de documents du bm25_tuple (ici 2 sources -> proche de la borne basse).
    captured = {}

    def _fake_retrievers(**kw):
        captured["pool"] = kw["topk_chunks"]
        return ["s0"], {"s0": {"doc": "x", "meta": {"source": "A"}, "distance": 0.1}}, [], {}

    monkeypatch.setattr(R, "_run_parallel_retrievers", _fake_retrievers)
    monkeypatch.setattr(R, "_maybe_apply_cross_encoder", lambda **kw: ([_mk("A", 0.7, 0)], 0.7))

    # bm25_tuple = (bm25, ids, texts, metas) avec 2 sources distinctes.
    bm25_tuple = (None, ["a", "b"], ["t1", "t2"],
                  [{"source": "A.md"}, {"source": "B.md"}])
    R.hybrid_retrieve(collection=None, query="q", bm25_tuple=bm25_tuple,
                      topk_chunks=15, source_filter=None, parent_child_on=False, debug=False)
    # clamp(40, 40 + 3*2, 120) = 46
    assert captured["pool"] == 46


def test_no_floor_when_source_scoped(monkeypatch):
    # Périmètre = un seul document -> pas de plancher (comportement inchangé).
    canned = [_mk("BIG", 0.90 - i * 0.01, i) for i in range(15)] + [_mk("SMALL", 0.55, 99)]
    monkeypatch.setattr(R, "_run_parallel_retrievers",
                        lambda **kw: (["s0"], {"s0": {"doc": "x", "meta": {"source": "BIG"}, "distance": 0.1}}, [], {}))
    monkeypatch.setattr(R, "_maybe_apply_cross_encoder", lambda **kw: (canned, 0.90))
    out, _ = R.hybrid_retrieve(
        collection=None, query="q", bm25_tuple=None,
        topk_chunks=15, source_filter="BIG", parent_child_on=False, debug=False,
    )
    assert len(out) == 15  # top-15 brut, pas de SMALL forcé
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hybrid_coverage.py -v`
Expected: FAIL — `AttributeError: module 'retrieval.retrieve' has no attribute 'MAX_CHUNKS'` (et plancher non appliqué)

- [ ] **Step 3: Write minimal implementation**

Dans `retrieval/retrieve.py`, étendre les imports `env_config` :

```python
from env_config import (
    USE_CROSS_ENCODER,
    CROSS_ENCODER_LOCAL_PATH,
    CE_DEVICE,
    NUM_CHUNKS,
    RRF_K,
    WEIGHT_SEMANTIC,
    WEIGHT_BM25,
    PARENT_CHILD_ENABLED,
    CE_RELEVANCE_THRESHOLD,
    CANDIDATE_POOL_MIN,
    CANDIDATE_POOL_MAX,
    CANDIDATE_POOL_PER_DOC,
    PER_DOC_FLOOR,
    MAX_CHUNKS,
)
from retrieval.coverage import apply_coverage_floor, elastic_candidate_pool
```

Ajouter un helper pour compter les documents du corpus chargé (gratuit depuis le `bm25_tuple`) :

```python
def _count_sources(bm25_tuple) -> int:
    """Nombre de documents distincts dans l'index BM25 fusionné (metas = 4e élément)."""
    if not bm25_tuple:
        return 0
    try:
        metas = bm25_tuple[3]
        return len({(m or {}).get("source") for m in metas})
    except (IndexError, TypeError):
        return 0
```

Changer la signature de `hybrid_retrieve` pour ajouter `candidate_pool` (défaut `None` = élastique) :

```python
def hybrid_retrieve(
    collection,
    query,
    bm25_tuple,
    topk_chunks=NUM_CHUNKS,
    rrf_k=RRF_K,
    rerank_on=True,
    debug=True,
    weight_semantic=WEIGHT_SEMANTIC,
    weight_bm25=WEIGHT_BM25,
    source_filter: str = None,
    parent_child_on: bool = None,
    candidate_pool: int = None,
):
```

Au tout début du corps de `hybrid_retrieve` (après `_t_start = time.perf_counter()`), résoudre le pool élastique :

```python
    if candidate_pool is None:
        candidate_pool = elastic_candidate_pool(
            _count_sources(bm25_tuple),
            pool_min=CANDIDATE_POOL_MIN,
            pool_max=CANDIDATE_POOL_MAX,
            per_doc=CANDIDATE_POOL_PER_DOC,
        )
```

Dans le corps : les retrievers et la fusion RRF utilisent `candidate_pool` (au lieu de `topk_chunks`) ; le rerank tourne sur ce pool ; le plancher réduit ensuite au top-k final. Remplacer les appels :

```python
    with span("parallel_search") as _sp:
        sem_ids, sem_lookup, bm_ids, bm_lookup = _run_parallel_retrievers(
            collection=collection,
            query=query,
            bm25_tuple=bm25_tuple,
            topk_chunks=candidate_pool,     # <-- pool élargi
            source_filter=source_filter,
        )
```

```python
    # 3) Fusion RRF (sémantique + BM25) sur le pool élargi
    fused = fuse_with_rrf(
        lists_a=[sem_ids], lookups_a=[sem_lookup],
        lists_b=[bm_ids] if bm_ids else None, lookups_b=[bm_lookup] if bm_lookup else None,
        rrf_k=rrf_k, topk_final=candidate_pool,      # <-- garde le pool
        weight_semantic=weight_semantic, weight_bm25=weight_bm25
    )
```

```python
    # 5) Rerank Cross-Encoder sur le pool
    with span("rerank") as _rr:
        fused, max_ce_score = _maybe_apply_cross_encoder(
            query=query,
            fused=fused,
            rerank_on=rerank_on,
            topk_chunks=candidate_pool,
            debug=debug,
        )
    if _rr is not None and max_ce_score is not None:
        _rr.set("max_ce", round(max_ce_score, 3))

    # 5b) Plancher de couverture par document (mode « Tous » uniquement),
    # puis réduction au top-k final borné.
    if fused:
        _floor_min = CE_RELEVANCE_THRESHOLD if USE_CROSS_ENCODER else None
        fused = apply_coverage_floor(
            fused,
            base_k=topk_chunks,
            per_doc_floor=(PER_DOC_FLOOR if source_filter is None else 0),
            max_chunks=MAX_CHUNKS,
            floor_min_ce=(_floor_min if source_filter is None else None),
        )
```

> Note : avec `per_doc_floor=0` (périmètre scopé) le plancher n'ajoute rien ; `apply_coverage_floor` réduit alors simplement au top `base_k`. Placer ce bloc **avant** l'expansion parent-child (étape 6).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hybrid_coverage.py tests/test_rrf.py -v`
Expected: PASS (2 nouveaux + non-régression RRF)

- [ ] **Step 5: Commit**

```bash
git add retrieval/retrieve.py tests/test_hybrid_coverage.py
git commit -m "feat(retrieval): pool de candidats élargi + plancher de couverture dans hybrid_retrieve"
```

---

### Task 5: Abstention recalibrée + intention exploratoire

Factorise la garde d'abstention (deux occurrences) en un helper unique qui **ne s'abstient pas** sur les questions exploratoires.

**Files:**
- Modify: `core/ask.py` (garde ~L147 dans `process_query`, garde ~L205 dans `_prepare_retrieval`)
- Test: `tests/test_abstention.py`

**Interfaces:**
- Consumes: `retrieval.intent.is_exploratory` (Task 1) ; `env_config.CE_RELEVANCE_THRESHOLD` (Task 2).
- Produces: `core.ask._should_abstain(max_ce_score, question) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_abstention.py
"""La garde d'abstention respecte le seuil MAIS ne s'abstient pas sur les
questions exploratoires (« c'est quoi », « de quoi parle »)."""
import core.ask as ask


def test_abstains_on_pointed_below_threshold(monkeypatch):
    monkeypatch.setattr(ask, "USE_CROSS_ENCODER", True)
    monkeypatch.setattr(ask, "CE_RELEVANCE_THRESHOLD", 0.48)
    assert ask._should_abstain(0.40, "Quelle est la valeur exacte de la clé RSA ?") is True


def test_does_not_abstain_on_exploratory(monkeypatch):
    monkeypatch.setattr(ask, "USE_CROSS_ENCODER", True)
    monkeypatch.setattr(ask, "CE_RELEVANCE_THRESHOLD", 0.48)
    assert ask._should_abstain(0.40, "C'est quoi un IDS ?") is False
    assert ask._should_abstain(0.40, "De quoi parle ce document ?") is False


def test_no_abstention_above_threshold(monkeypatch):
    monkeypatch.setattr(ask, "USE_CROSS_ENCODER", True)
    monkeypatch.setattr(ask, "CE_RELEVANCE_THRESHOLD", 0.48)
    assert ask._should_abstain(0.60, "Quelle est la valeur exacte ?") is False


def test_no_abstention_when_ce_off(monkeypatch):
    monkeypatch.setattr(ask, "USE_CROSS_ENCODER", False)
    assert ask._should_abstain(None, "Quelle valeur ?") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_abstention.py -v`
Expected: FAIL — `AttributeError: module 'core.ask' has no attribute '_should_abstain'`

- [ ] **Step 3: Write minimal implementation**

Dans `core/ask.py`, ajouter le helper (près des autres utilitaires de retrieval, ex. après `_load_bm25`) :

```python
def _should_abstain(max_ce_score, question: str) -> bool:
    """Décide de l'abstention hors-scope. On s'abstient si le meilleur score CE
    est sous le seuil — SAUF sur les questions exploratoires (génériques/
    définitionnelles) où l'on veut retourner le meilleur contexte disponible."""
    if not USE_CROSS_ENCODER or max_ce_score is None:
        return False
    from retrieval.intent import is_exploratory
    if is_exploratory(question):
        return False
    return max_ce_score < CE_RELEVANCE_THRESHOLD
```

Remplacer la garde dans `process_query` (~L147) :

```python
        # -- Détection hors-scope ----------------------------------------------
        if _should_abstain(max_ce_score, user_q):
            logger.debug("[scope] Hors-scope détecté (max CE=%.3f)", max_ce_score)
            _tr.set("hors_scope", True)
            return OUT_OF_SCOPE_MESSAGE, [], []
```

Remplacer la garde dans `_prepare_retrieval` (~L205). `_prepare_retrieval` reçoit `user_q` — l'utiliser :

```python
    # -- Détection hors-scope --------------------------------------------------
    if _should_abstain(max_ce_score, user_q):
        logger.debug("[scope] Hors-scope détecté (max CE=%.3f)", max_ce_score)
        return q_main, [], max_ce_score  # final_chunks vide + score pour l'appelant
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_abstention.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add core/ask.py tests/test_abstention.py
git commit -m "feat(ask): abstention recalibrée + non-abstention sur questions exploratoires"
```

---

### Task 6: Rejet des chunks vides à l'ingestion

Écarte les chunks au contenu vide/blanc avant indexation (défensif aussi dans BM25).

**Files:**
- Modify: `core/ingest.py` (après `docs.extend(summary_docs)` / le `else` RAPTOR, ~L206, avant « Construction du vocabulaire »)
- Modify: `indexing/keyword_index.py` (`build_bm25_index` ~L53-61, filtre défensif)
- Test: `tests/test_empty_chunks.py`

**Interfaces:**
- Consumes: rien de nouveau.
- Produces: `build_bm25_index(docs)` ignore les docs au `page_content` vide/blanc (contrat renforcé, même signature `(bm25, ids, texts, metadatas)`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_empty_chunks.py
"""build_bm25_index ignore les chunks vides (page_content vide/blanc)."""
from dataclasses import dataclass
from indexing.keyword_index import build_bm25_index


@dataclass
class _Doc:
    page_content: str
    metadata: dict


def test_build_bm25_skips_empty_chunks():
    docs = [
        _Doc("Le chiffrement protege les donnees", {"id": "a1", "source": "A.md"}),
        _Doc("", {"id": "empty", "source": "A.md"}),
        _Doc("   ", {"id": "blank", "source": "A.md"}),
        _Doc("Le parefeu filtre le trafic", {"id": "b1", "source": "B.md"}),
    ]
    _, ids, texts, metas = build_bm25_index(docs)
    assert "empty" not in ids and "blank" not in ids
    assert set(ids) == {"a1", "b1"}
    assert all(t.strip() for t in texts)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_empty_chunks.py -v`
Expected: FAIL — `assert 'empty' not in ids` (les chunks vides sont indexés)

- [ ] **Step 3: Write minimal implementation**

Dans `indexing/keyword_index.py`, filtrer en tête de `build_bm25_index` :

```python
def build_bm25_index(docs):
    """Construit l'index BM25 et les structures associées.
    Les chunks au contenu vide/blanc sont ignorés (ils pollueraient les scores)."""
    docs = [d for d in docs if (getattr(d, "page_content", "") or "").strip()]
    texts = [getattr(d, "page_content", "") or "" for d in docs]
    ids = [getattr(d, "metadata", {}).get("id", f"doc_{i:04d}") for i, d in enumerate(docs)]
    metadatas = [getattr(d, "metadata", {}) for d in docs]
    enriched_texts = [_build_enriched_text(d) for d in docs]
    corpus_tokens = [_tokenize(t) for t in enriched_texts]
    bm25 = BM25Okapi(corpus_tokens)
    return bm25, ids, texts, metadatas
```

Dans `core/ingest.py`, filtrer la liste `docs` avant indexation (juste avant « Construction du vocabulaire », ~L206) :

```python
        # Filtrer les chunks vides : ils polluent BM25 (token vide) et le pool de
        # candidats sémantique sans jamais rien apporter à une réponse.
        _before = len(docs)
        docs = [d for d in docs if (getattr(d, "page_content", "") or "").strip()]
        _removed = _before - len(docs)
        if _removed:
            logger.info("Chunks vides écartés avant indexation : %d", _removed)
        stats["empty_chunks_removed"] = _removed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_empty_chunks.py tests/test_bm25.py -v`
Expected: PASS (nouveau test + non-régression BM25)

- [ ] **Step 5: Commit**

```bash
git add core/ingest.py indexing/keyword_index.py tests/test_empty_chunks.py
git commit -m "fix(ingest): écarter les chunks vides avant indexation (Chroma + BM25)"
```

---

### Task 7: Invalidation fiable du cache BM25 à l'ingestion

`clear_retrieval_caches()` (déjà appelé après ingestion) doit aussi vider le cache `"__all__"` de `indexing/keyword_index`, sinon le BM25 fusionné reste périmé jusqu'au redémarrage.

**Files:**
- Modify: `core/ask.py` (`clear_retrieval_caches` ~L34-43)
- Test: `tests/test_cache_invalidation.py`

**Interfaces:**
- Consumes: `indexing.keyword_index.invalidate_bm25_cache` (existe déjà, jamais appelée) ; `indexing.keyword_index._bm25_cache`.
- Produces: `core.ask.clear_retrieval_caches()` vide AUSSI le cache BM25 de `keyword_index`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cache_invalidation.py
"""clear_retrieval_caches vide aussi le cache BM25 fusionné de keyword_index,
sinon un nouveau document reste invisible jusqu'au redémarrage du process."""
import indexing.keyword_index as ki
import core.ask as ask


def test_clear_retrieval_caches_invalidates_keyword_index_cache():
    # Simule un cache BM25 fusionné chaud.
    ki._bm25_cache["__all__"] = ("fake_bm25", ["id1"], ["texte"], [{"source": "A.md"}])
    assert "__all__" in ki._bm25_cache

    ask.clear_retrieval_caches()

    assert "__all__" not in ki._bm25_cache
    assert ki._bm25_cache == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cache_invalidation.py -v`
Expected: FAIL — `assert '__all__' not in ki._bm25_cache` (cache non vidé)

- [ ] **Step 3: Write minimal implementation**

Dans `core/ask.py`, `clear_retrieval_caches` :

```python
def clear_retrieval_caches() -> None:
    """Vide les caches process-level (vector store + BM25, y compris le cache
    BM25 fusionné de keyword_index). À appeler après une (ré)ingestion pour que
    les requêtes suivantes rechargent les index fraîchement écrits."""
    global _vector_store
    _vector_store = None
    _bm25_cache.clear()
    from indexing.keyword_index import invalidate_bm25_cache
    invalidate_bm25_cache()  # vide le cache "__all__" de keyword_index
    logger.info("Caches de retrieval vidés (vector store/BM25)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cache_invalidation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/ask.py tests/test_cache_invalidation.py
git commit -m "fix(ask): clear_retrieval_caches invalide aussi le cache BM25 de keyword_index"
```

---

### Task 8: Ingestion multi-format .pdf / .docx / .xlsx (vérifier + durcir)

Les documents réels arrivent en `.pdf`, `.docx`, `.xlsx`. Le pipeline route déjà tout non-`.md` vers Docling (`UPLOAD_TYPES` inclut ces formats ; Docling installé supporte `DOCX`/`XLSX`). On **verrouille** le support (formats autorisés explicites) et on **prouve** la conversion offline sur des fichiers minimaux générés.

**Files:**
- Modify: `preprocessing/pdf_to_markdown.py` (`_get_converter` ~L57-63 : autoriser explicitement DOCX/XLSX/PPTX/HTML/MD en plus de PDF)
- Test: `tests/test_multiformat_ingest.py`

**Interfaces:**
- Consumes: `preprocessing.pdf_to_markdown.convert_and_clean(src, out_dir)` (existant).
- Produces: `_get_converter` accepte explicitement les formats bureautiques (pas de restriction implicite au PDF).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_multiformat_ingest.py
"""Conversion offline .docx et .xlsx -> Markdown non vide via le pipeline Docling.
Fichiers minimaux générés à la volée (python-docx / openpyxl, déjà installés)."""
from pathlib import Path
import pytest
from preprocessing.pdf_to_markdown import convert_and_clean


def _make_docx(path):
    import docx
    d = docx.Document()
    d.add_heading("Menaces identifiees", level=1)
    d.add_paragraph("Le systeme doit resister a l'attaque par rejeu et a l'injection.")
    d.save(path)


def _make_xlsx(path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Categorie", "Attaque"])
    ws.append(["Reseau", "Deni de service"])
    ws.append(["Applicatif", "Injection SQL"])
    wb.save(path)


@pytest.mark.parametrize("maker,ext", [(_make_docx, "docx"), (_make_xlsx, "xlsx")])
def test_office_formats_convert_to_markdown(tmp_path, maker, ext):
    src = tmp_path / f"echantillon.{ext}"
    maker(str(src))
    md_path = convert_and_clean(str(src), out_dir=str(tmp_path / "out"))
    text = Path(md_path).read_text(encoding="utf-8")
    assert text.strip(), f"markdown vide pour .{ext}"
    # Un token du contenu source doit survivre à la conversion.
    needle = "rejeu" if ext == "docx" else "Injection"
    assert needle.lower() in text.lower()
```

- [ ] **Step 2: Run test to verify it fails (ou révèle une restriction de format)**

Run: `python -m pytest tests/test_multiformat_ingest.py -v`
Expected: si `_get_converter` restreint implicitement au PDF, échec « format non supporté » ; sinon le test peut déjà passer (support Docling natif) — dans ce cas, verrouiller quand même le format à l'étape 3 pour éviter une régression silencieuse.

- [ ] **Step 3: Write minimal implementation**

Dans `preprocessing/pdf_to_markdown.py`, `_get_converter`, déclarer explicitement les formats autorisés :

```python
def _get_converter(do_ocr: bool = False):
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    return DocumentConverter(
        allowed_formats=[
            InputFormat.PDF, InputFormat.DOCX, InputFormat.XLSX,
            InputFormat.PPTX, InputFormat.HTML, InputFormat.MD,
        ],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=_pdf_pipeline_options(do_ocr))},
    )
```

> Ne toucher qu'à la construction du converter (ajout `allowed_formats` + import `InputFormat`). Le reste de `convert_and_clean` gère déjà le non-PDF (branche `converter.convert(str(src))`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_multiformat_ingest.py -v`
Expected: PASS (docx + xlsx). (Test réseau-libre : Docling ne charge les modèles layout/OCR que pour le PDF ; DOCX/XLSX sont convertis sans modèle.)

- [ ] **Step 5: Commit**

```bash
git add preprocessing/pdf_to_markdown.py tests/test_multiformat_ingest.py
git commit -m "feat(ingest): verrouiller la conversion multi-format .docx/.xlsx (Docling)"
```

---

### Task 9: Validation bout-en-bout (sondes réelles + éval + non-régression)

Vérifie l'effet réel sur le corpus courant et l'absence de régression. Pas de nouveau code produit — un script de sonde read-only + les suites existantes.

**Files:**
- Create: `scripts/probe_retrieval.py` (sonde manuelle, read-only)

**Interfaces:**
- Consumes: `core.ask._get_vector_store`, `core.ask._load_bm25`, `retrieval.retrieve.hybrid_retrieve`, `retrieval.intent.classify_intent`.

- [ ] **Step 1: Écrire la sonde read-only**

```python
# scripts/probe_retrieval.py
"""Sonde read-only : vérifie couverture multi-document + non-abstention exploratoire.
Usage : .venv/bin/python -m scripts.probe_retrieval"""
import os
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
from collections import Counter
from core.ask import _get_vector_store, _load_bm25, _should_abstain
from retrieval.retrieve import hybrid_retrieve
from retrieval.intent import classify_intent

QUERIES = [
    "c'est quoi un IDS ?",
    "Que dit le document sur les IDS intelligents ?",
    "Compare les menaces et les exigences entre les deux documents",
    "Quelles sont les exigences de chiffrement ?",
]


def main():
    col = _get_vector_store()
    bm = _load_bm25(None)
    for q in QUERIES:
        chunks, max_ce = hybrid_retrieve(collection=col, query=q, bm25_tuple=bm,
                                         source_filter=None, parent_child_on=False, debug=False)
        srcs = Counter((c.get("meta") or {}).get("source") for c in chunks)
        abstain = _should_abstain(max_ce, q)
        print(f"[{classify_intent(q):11}] abstain={abstain}  max_ce={max_ce}  "
              f"n={len(chunks)}  sources={dict(srcs)}\n   Q={q}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Lancer la sonde et lire la sortie**

Run: `.venv/bin/python -m scripts.probe_retrieval`
Expected :
- « c'est quoi un IDS ? » → `abstain=False` (intention exploratoire) et ≥1 chunk du document IDS (`Intégrateur…`).
- « Compare … entre les deux documents » → `sources` contient **les deux** documents.
- « exigences de chiffrement » → répond, sans injection forcée du petit document non pertinent.

- [ ] **Step 3: Éval de non-régression retrieval**

Run: `.venv/bin/python -m evals.run_eval --mode retrieval`
Expected : recall global ≥ baseline archivée (`evals/last_eval.json`). Noter les scores avant/après pour calibrer poids et seuil si besoin.

- [ ] **Step 4: Suite complète**

Run: `python -m pytest -q`
Expected : PASS (toutes suites, y compris les nouvelles).

- [ ] **Step 5: Commit**

```bash
git add scripts/probe_retrieval.py
git commit -m "test(retrieval): sonde read-only couverture multi-doc + non-abstention"
```

---

## Self-Review

**Spec coverage :**
- Détecteur d'intention (spec §A) → Task 1. ✅
- Pool ÉLASTIQUE + plancher par document + top-k adaptatif borné (spec §B.1-B.3 + exigence élasticité) → Tasks 2, 3, 4. ✅ (pool = `clamp(MIN, MIN+PER_DOC·n_docs, MAX)` dérivé du corpus ; top-k = base `NUM_CHUNKS` + planchers, plafonné `MAX_CHUNKS`.)
- Rééquilibrage poids (spec §B.4) → Task 2. ✅
- Abstention recalibrée + intention (spec §B.3) → Tasks 2, 5. ✅
- Rejet chunks vides (spec §D.1) → Task 6. ✅
- Invalidation BM25 (spec §D.2) → Task 7. ✅
- Ingestion multi-format .pdf/.docx/.xlsx (exigence entrées) → Task 8. ✅
- Vérification bout-en-bout (spec §Vérification) → Task 9. ✅
- **Hors périmètre de CE plan (1a)** : synthèse corpus map-reduce (spec §C) et outils agent (spec §C.1) → **Plan 1b**, à écrire après exécution de 1a. Coût BM25 fusionné à 20k chunks à mesurer (spec §D.3) → observé pendant Task 9 (éval), traité en 1b si bloquant.

**Placeholder scan :** aucun TBD/TODO ; chaque étape porte son code réel.

**Type consistency :** `apply_coverage_floor(items, base_k, per_doc_floor, max_chunks, floor_min_ce)` et `elastic_candidate_pool(n_docs, pool_min, pool_max, per_doc)` — mêmes signatures en Task 3 (déf) et Task 4 (appel). `_count_sources(bm25_tuple)` défini et utilisé en Task 4. `_should_abstain(max_ce_score, question)` — cohérent Task 5. `classify_intent/is_exploratory/is_aggregate` — cohérents Tasks 1/5. `build_bm25_index` conserve son tuple `(bm25, ids, texts, metadatas)`.

## Notes d'exécution

- Ordre des tasks = ordre de dépendance (1→9). Tasks 1, 2, 3 sont indépendantes et peuvent être parallélisées ; Task 4 dépend de 2+3 ; Task 5 dépend de 1+2 ; Tasks 6, 7, 8 indépendantes ; Task 9 en dernier.
- **Élasticité** : le pool s'adapte au corpus (2 docs → 46 ; 50 docs → plafonné 120). Mesurer le coût du cross-encoder sur 120 candidats au plafond pendant Task 9 ; si trop lent sur GPU local, baisser `CANDIDATE_POOL_MAX` via `.env` (aucune modif de code).
- Après ce plan, écrire **Plan 1b — synthèse corpus map-reduce** (`core/synthesize_corpus.py`, outils `list_sources`/`extract_from_document`, routage `aggregate`, streaming glass-box), qui consomme `retrieval.intent` livré ici.
