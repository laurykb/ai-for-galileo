# Vague 0 — Harnais RAGAS local Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Mesurer la qualité des réponses avec de vraies métriques RAGAS locales (faithfulness, context recall, context precision, answer relevancy) par décomposition en affirmations — prérequis pour évaluer toute technique des vagues suivantes.

**Architecture:** Nouvelles fonctions RAGAS dans `core/evaluation.py`, juge LLM (`build_llm("judge")`) et embeddings (`OllamaEmbedding`) **injectables** pour des tests offline déterministes ; nouveau `--mode ragas` dans `evals/run_eval.py`. Les métriques heuristiques existantes sont renommées `*_lexical` et conservées pour le mode `retrieval` léger.

**Tech Stack:** Python 3.12, Ollama (juge + embeddings), pytest offline (fakes injectés). 100% offline — PAS la librairie `ragas`.

## Global Constraints

- **100% offline** : aucune dépendance pip nouvelle (pas `ragas`/`datasets`). Réutiliser `core.model_router.build_llm("judge")` et `nlp.ollama_embedding.OllamaEmbedding`.
- **Injectabilité** : chaque fonction RAGAS accepte `llm=` (callable ou objet avec `.invoke`) et, pour l'answer relevancy, `embed=` (callable `str -> list[float]`). Défauts = juge/embeddings réels. Les tests injectent des fakes → zéro appel réseau.
- **Ne pas casser** les modes `retrieval`/`full` existants : on RENOMME les heuristiques (`context_recall`→`context_recall_lexical`, `context_precision`→`context_precision_lexical`) et on met à jour leurs SEULS appelants (`evals/run_eval.py:_evaluate_item` mode retrieval).
- **Parsing robuste** des affirmations (une par ligne, tolérant aux puces) — style `core/corpus_extract._parse_items`.
- **Scores indicatifs** : juge = petit modèle local ; ce qui compte est la comparaison avant/après à juge+jeu constants.

---

### Task 1: `decompose_claims` — décomposition en affirmations atomiques

**Files:** Modify `core/evaluation.py` ; Test `tests/test_evaluation_ragas.py`

**Interfaces:**
- Produces: `decompose_claims(text: str, llm=None) -> list[str]` — demande au LLM une liste d'affirmations atomiques, parse les lignes non vides (retire puces `-*•`). `llm` injectable (callable `str->str` ou objet `.invoke`). Texte vide → `[]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evaluation_ragas.py
"""Métriques RAGAS locales — juge/embeddings injectés, 100% hors-ligne."""
from core.evaluation import decompose_claims


def test_decompose_parses_atomic_claims():
    fake = lambda prompt: "- Le chiffrement est AES-256\n- La clé fait 256 bits\n\n* Le mode est GCM\n"
    out = decompose_claims("…", llm=fake)
    assert out == ["Le chiffrement est AES-256", "La clé fait 256 bits", "Le mode est GCM"]


def test_decompose_empty_text():
    assert decompose_claims("", llm=lambda p: "IGNORED") == []
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_evaluation_ragas.py -v` → FAIL (ImportError).

- [ ] **Step 3: Implement** dans `core/evaluation.py` :

```python
def _call_llm(llm, prompt: str) -> str:
    """Appelle un juge injecté : callable(prompt)->str OU objet avec .invoke."""
    if llm is None:
        llm = _get_judge_llm()
    fn = llm if callable(llm) else llm.invoke
    return fn(prompt) or ""


_CLAIMS_PROMPT = """[TÂCHE] Décompose le TEXTE en affirmations ATOMIQUES (une idée vérifiable par ligne).
- Une affirmation par ligne, préfixée « - ». Rien d'autre.
- Fidèle au texte, sans invention, sans reformulation excessive.

[TEXTE]
{text}

[AFFIRMATIONS]"""


def decompose_claims(text: str, llm=None) -> list[str]:
    """Extrait des affirmations atomiques d'un texte (LLM injectable)."""
    if not (text or "").strip():
        return []
    raw = _call_llm(llm, _CLAIMS_PROMPT.format(text=text[:2000]))
    items = []
    for line in raw.splitlines():
        s = line.strip().lstrip("-*•").strip()
        if s:
            items.append(s)
    return items
```

- [ ] **Step 4: Run** the test → PASS. **Step 5: Commit** `git add core/evaluation.py tests/test_evaluation_ragas.py && git commit -m "feat(eval): decompose_claims (base des métriques RAGAS)"`

---

### Task 2: `faithfulness_ragas` + `context_recall_ragas`

Deux métriques par affirmations : fidélité (affirmations de la réponse soutenues par le contexte) et rappel de contexte (affirmations de la référence attribuables au contexte).

**Files:** Modify `core/evaluation.py` ; extend `tests/test_evaluation_ragas.py`

**Interfaces:**
- Consumes: `decompose_claims` (Task 1).
- Produces:
  - `faithfulness_ragas(generated, chunks, llm=None) -> float` = soutenues/total.
  - `context_recall_ragas(reference, chunks, llm=None) -> float` = attribuables/total.
  - Helper `_judge_supported(claim, context, llm) -> bool` (juge oui/non via `_ask_judge` ≥ 0.5).

- [ ] **Step 1: Write the failing test**

```python
def test_faithfulness_supported_ratio():
    # 2 affirmations ; le juge en soutient 1 (score 1.0) et pas l'autre (0.0).
    from core import evaluation as E
    claims = ["A", "B"]
    fake_llm = object()
    E_decompose = lambda text, llm=None: claims
    # juge : "A" soutenue, "B" non
    def fake_judge(_llm, prompt):
        return 1.0 if "A" in prompt.split("AFFIRMATION")[-1] else 0.0
    import types
    E.decompose_claims = E_decompose            # monkeypatch simple
    E._ask_judge = fake_judge
    chunks = [{"doc": "contexte"}]
    assert E.faithfulness_ragas("réponse", chunks, llm=fake_llm) == 0.5


def test_context_recall_attributable_ratio(monkeypatch):
    from core import evaluation as E
    monkeypatch.setattr(E, "decompose_claims", lambda text, llm=None: ["x", "y", "z"])
    monkeypatch.setattr(E, "_ask_judge", lambda _llm, prompt: 1.0 if "x" in prompt or "y" in prompt else 0.0)
    val = E.context_recall_ragas("ref", [{"doc": "c"}], llm=object())
    assert abs(val - 2/3) < 1e-6
```

> Note : préférer `monkeypatch.setattr` (comme le 2e test) ; réécrire le 1er test au même style. Ne pas laisser de monkeypatch global non nettoyé.

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** :

```python
def _judge_supported(claim: str, context: str, llm) -> bool:
    prompt = f"""[TÂCHE] Le CONTEXTE soutient-il l'AFFIRMATION ? Réponds par un nombre : 1.0 = oui (directement justifiable), 0.0 = non.

[CONTEXTE]
{context[:2000]}

[AFFIRMATION]
{claim}

Score :"""
    return _ask_judge(llm if not callable(llm) else llm, prompt) >= 0.5


def _context_text(chunks, topk=5, per=500) -> str:
    return "\n\n".join((c.get("doc", "") or "")[:per] for c in (chunks or [])[:topk])


def faithfulness_ragas(generated: str, chunks: list, llm=None) -> float:
    """Affirmations de la réponse soutenues par le contexte / total."""
    claims = decompose_claims(generated, llm=llm)
    if not claims or not chunks:
        return 0.0
    ctx = _context_text(chunks)
    supported = sum(1 for c in claims if _judge_supported(c, ctx, llm))
    return supported / len(claims)


def context_recall_ragas(reference: str, chunks: list, llm=None) -> float:
    """Affirmations de la référence attribuables au contexte récupéré / total."""
    claims = decompose_claims(reference, llm=llm)
    if not claims or not chunks:
        return 0.0
    ctx = _context_text(chunks)
    attrib = sum(1 for c in claims if _judge_supported(c, ctx, llm))
    return attrib / len(claims)
```

> `_ask_judge(llm, prompt)` existe déjà et attend un objet `.invoke` ; adapter : si `llm` est callable, l'envelopper. Simplifier en faisant passer le prompt par `_call_llm` puis en parsant le nombre — l'implémenteur harmonise avec `_ask_judge` existant, sans dupliquer le parsing.

- [ ] **Step 4: Run** tests → PASS. **Step 5: Commit** `git commit -am "feat(eval): faithfulness_ragas + context_recall_ragas (par affirmations)"`

---

### Task 3: `context_precision_ragas` (rank-aware)

**Files:** Modify `core/evaluation.py` ; extend test.

**Interfaces:**
- Produces: `context_precision_ragas(question, reference, chunks, topk=5, llm=None) -> float` — juge la pertinence de chaque chunk (question+référence), puis calcule la **mean average precision@k** : récompense les chunks pertinents haut dans le classement.

- [ ] **Step 1: Test**

```python
def test_context_precision_rank_aware(monkeypatch):
    from core import evaluation as E
    # 3 chunks ; pertinents = positions 1 et 3 (0-indexé 0 et 2).
    monkeypatch.setattr(E, "_judge_chunk_relevant",
                        lambda q, ref, doc, llm: doc in ("c0", "c2"))
    chunks = [{"doc": "c0"}, {"doc": "c1"}, {"doc": "c2"}]
    val = E.context_precision_ragas("q", "ref", chunks, topk=3, llm=object())
    # AP@k = mean(precision@rank aux positions pertinentes) = mean(1/1, 2/3) = 0.8333
    assert abs(val - (1.0 + 2/3) / 2) < 1e-6


def test_context_precision_no_relevant(monkeypatch):
    from core import evaluation as E
    monkeypatch.setattr(E, "_judge_chunk_relevant", lambda q, ref, doc, llm: False)
    assert E.context_precision_ragas("q", "ref", [{"doc": "c"}], llm=object()) == 0.0
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** :

```python
def _judge_chunk_relevant(question: str, reference: str, doc: str, llm) -> bool:
    prompt = f"""[TÂCHE] Ce PASSAGE est-il pertinent pour répondre à la QUESTION (au vu de la RÉPONSE attendue) ? 1.0 = pertinent, 0.0 = non.

[QUESTION] {question}
[RÉPONSE ATTENDUE] {reference[:500]}
[PASSAGE] {doc[:800]}

Score :"""
    return _ask_judge(llm if not callable(llm) else llm, prompt) >= 0.5


def context_precision_ragas(question, reference, chunks, topk=5, llm=None) -> float:
    """Mean average precision@k : pertinence jugée, pondérée par le rang."""
    kept = (chunks or [])[:topk]
    if not kept:
        return 0.0
    hits = 0
    precisions = []
    for i, c in enumerate(kept, 1):
        if _judge_chunk_relevant(question, reference, c.get("doc", "") or "", llm):
            hits += 1
            precisions.append(hits / i)
    if not precisions:
        return 0.0
    return sum(precisions) / len(precisions)
```

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `git commit -am "feat(eval): context_precision_ragas (rank-aware, pertinence jugée)"`

---

### Task 4: `answer_relevancy_ragas` (questions générées + embeddings)

**Files:** Modify `core/evaluation.py` ; extend test.

**Interfaces:**
- Produces: `answer_relevancy_ragas(generated, question, llm=None, embed=None, n=3) -> float` — génère `n` questions depuis la réponse, embarque chacune + la question d'origine, renvoie le **cosinus moyen**. `embed` injectable (callable `str -> list[float]`), défaut `OllamaEmbedding().embed_query`.

- [ ] **Step 1: Test**

```python
def test_answer_relevancy_mean_cosine(monkeypatch):
    from core import evaluation as E
    # génère 2 questions ; embeddings factices : q0 identique à l'orig, q1 orthogonal.
    monkeypatch.setattr(E, "_generate_questions", lambda gen, n, llm: ["q0", "q1"])
    vecs = {"orig": [1.0, 0.0], "q0": [1.0, 0.0], "q1": [0.0, 1.0]}
    val = E.answer_relevancy_ragas("rép", "orig", llm=object(),
                                   embed=lambda s: vecs[s], n=2)
    assert abs(val - 0.5) < 1e-6  # mean(cos=1, cos=0)


def test_answer_relevancy_empty():
    from core import evaluation as E
    assert E.answer_relevancy_ragas("", "q", llm=lambda p: "", embed=lambda s: [1.0]) == 0.0
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (réutiliser numpy déjà présent) :

```python
def _generate_questions(generated: str, n: int, llm) -> list[str]:
    prompt = f"""[TÂCHE] À partir de la RÉPONSE, génère {n} question(s) auxquelles elle répondrait. Une par ligne, préfixée « - ». Rien d'autre.

[RÉPONSE]
{generated[:1500]}

[QUESTIONS]"""
    raw = _call_llm(llm, prompt)
    qs = [l.strip().lstrip("-*•").strip() for l in raw.splitlines() if l.strip()]
    return qs[:n]


def _cosine(a, b) -> float:
    import numpy as np
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(va @ vb / (na * nb))


def answer_relevancy_ragas(generated, question, llm=None, embed=None, n=3) -> float:
    """Cosinus moyen entre la question d'origine et des questions générées depuis la réponse."""
    if not (generated or "").strip():
        return 0.0
    if embed is None:
        from nlp.ollama_embedding import OllamaEmbedding
        embed = OllamaEmbedding().embed_query
    gen_qs = _generate_questions(generated, n, llm)
    if not gen_qs:
        return 0.0
    q_vec = embed(question)
    sims = [_cosine(q_vec, embed(gq)) for gq in gen_qs]
    return sum(sims) / len(sims)
```

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `git commit -am "feat(eval): answer_relevancy_ragas (questions générées + cosinus embeddings)"`

---

### Task 5: Mode `--mode ragas` dans le runner

**Files:** Modify `evals/run_eval.py` ; Modify `core/evaluation.py` (rename heuristiques).

**Interfaces:**
- `run_eval` accepte `--mode ragas`. `_evaluate_item` mode ragas : génère la réponse (`process_query`) puis calcule faithfulness_ragas, context_recall_ragas, context_precision_ragas, answer_relevancy_ragas + keyword_hit_rate + latence. Rapport/agrégat/régression incluent ces clés.
- Renommer `context_recall`→`context_recall_lexical`, `context_precision`→`context_precision_lexical` ; mettre à jour le mode `retrieval` (`_evaluate_item`) pour appeler les `*_lexical`. Aucune autre régression.

- [ ] **Step 1: Test** (offline, juge/embeddings injectés au niveau evaluation)

```python
# tests/test_run_eval_ragas.py
def test_ragas_mode_item(monkeypatch):
    import evals.run_eval as R
    from core import evaluation as E
    # stub retrieval+génération et métriques pour rester offline
    monkeypatch.setattr("core.ask.process_query",
                        lambda q, source_filter=None: ("réponse", [{"doc": "ctx"}], []))
    monkeypatch.setattr(E, "faithfulness_ragas", lambda g, c, llm=None: 0.9)
    monkeypatch.setattr(E, "context_recall_ragas", lambda r, c, llm=None: 0.8)
    monkeypatch.setattr(E, "context_precision_ragas", lambda q, r, c, topk=5, llm=None: 0.7)
    monkeypatch.setattr(E, "answer_relevancy_ragas", lambda g, q, llm=None, embed=None, n=3: 0.85)
    item = {"question": "Q ?", "answer": "ref", "expected_keywords": []}
    m = R._evaluate_item(item, mode="ragas", source_filter=None, use_judge=True, judge=None)
    assert m["status"] == "ok"
    assert m["faithfulness"] == 0.9 and m["context_recall"] == 0.8
    assert m["context_precision"] == 0.7 and m["answer_relevancy"] == 0.85
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** : ajouter `"ragas"` aux `choices` du `--mode` ; brancher un `elif mode == "ragas":` dans `_evaluate_item` calculant les 4 métriques RAGAS ; renommer les heuristiques et corriger l'import + les appels du mode `retrieval`. Étendre `_METRIC_KEYS`/agrégation pour inclure `answer_relevancy`.

- [ ] **Step 4: Run** `tests/test_run_eval_ragas.py` + suite complète → PASS. **Step 5: Commit** `git commit -am "feat(eval): mode --mode ragas (4 métriques par affirmations) + heuristiques *_lexical"`

---

### Task 6: Baseline live + validation

**Files:** (aucun code) — run réel + archivage baseline.

- [ ] **Step 1: Run réel** `.venv/bin/python -m evals.run_eval --mode ragas --limit 3` (Mongo+Ollama requis). Capturer la sortie : les 4 métriques par question + agrégat. Vérifier la cohérence (faithfulness élevée sur réponse fidèle).
- [ ] **Step 2: Contrôle de validité** — vérifier qu'une réponse volontairement inventée obtient une faithfulness basse (test de bon sens du juge), en notant le résultat dans le rapport.
- [ ] **Step 3: Suite** `.venv/bin/python -m pytest -q` → vert.
- [ ] **Step 4: Archiver la baseline** — lancer le run complet `--mode ragas` (sans limit si le temps le permet, sinon limit raisonnable), le sauvegarder (Mongo `eval_runs` + `evals/last_eval.json`) comme **baseline de référence** pour les Vagues 1-2. Commit d'un éventuel fichier de baseline versionné.

---

## Self-Review

**Spec coverage :** decompose_claims → T1 ; faithfulness + context_recall RAGAS → T2 ; context_precision rank-aware → T3 ; answer_relevancy → T4 ; mode `ragas` + rename heuristiques → T5 ; baseline + validation → T6. ✅

**Placeholder scan :** les notes « l'implémenteur harmonise avec `_ask_judge` » sont des consignes d'intégration (le code est fourni), pas des TBD. Le 1er test de T2 est à réécrire au style `monkeypatch` (indiqué).

**Type consistency :** `decompose_claims(text, llm)`, `faithfulness_ragas(generated, chunks, llm)`, `context_recall_ragas(reference, chunks, llm)`, `context_precision_ragas(question, reference, chunks, topk, llm)`, `answer_relevancy_ragas(generated, question, llm, embed, n)` — cohérents T1-T5. Helpers `_judge_supported`, `_judge_chunk_relevant`, `_generate_questions`, `_call_llm`, `_cosine`, `_context_text` définis avant usage.

## Notes d'exécution

- Ordre : T1 → T2 → T3 → T4 (indépendantes après T1) → T5 (dépend de T2-4) → T6.
- Après la Vague 0 : **Vague 1** (HyPE + en-têtes contextuels + activer parent-child), chaque technique mesurée avec `--mode ragas` avant/après. Puis **Vague 2**.
