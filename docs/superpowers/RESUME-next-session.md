# Reprise — état du projet & tâches restantes (2026-07-22)

> **Archive Claude/Superpowers.** Pour les travaux actuels avec Codex, utiliser `docs/CODEX-ROADMAP.md` et les règles de `AGENTS.md`. En cas de divergence, le code et les tests actuels prévalent.

> **Cette reprise est obsolète.** L’état opérationnel consolidé du 2026-07-24, l’architecture effective, les validations et le prochain ordre de travail sont dans `docs/PROJECT_STATE.md`. Ne pas exécuter directement la liste ci-dessous : plusieurs éléments sont désormais implémentés.

> ## ⏸️ REPRISE 2026-07-23 — Vague 1 : code FAIT, mesures 30-q à confirmer
>
> **Fait aujourd'hui (2026-07-22, tout commité, branche `feat/chantier1-retrieval-multidoc`) :**
> - ✅ **Correctif agrégat RAGAS** (commit `7edd7a9`) : `answer_relevancy`+`keyword_hit_rate` remontés à l'agrégat ; `run_eval` écrit `evals/last_eval.json`. Baseline backfillé : **aa-rel réel = 0.599**, kw_hit = 0.656 (recall 0.529 / prec 0.809 / faith 0.495).
> - ✅ **Plan Vague 1** : `docs/superpowers/plans/2026-07-22-vague1-precompute.md`.
> - ✅ **Code Vague 1 (TDD + review clean, suite 240 verte)** :
>   - Task 1 `aeaab14` — flags `HYPE_ENABLED`/`HYPE_MAX_QUESTIONS`/`CONTEXT_HEADERS_ENABLED` (défaut off).
>   - Task 2 `2ccf184` — `indexing/embedding.build_embedding_units` : vrai HyPE (1 vecteur/question → parent) + préfixe en-tête embed-only ; restaure le vecteur CONTENU (l'ancien code embarquait le blob questions à la place).
>   - Task 3 `14afbc0` — `retrieval/hype.resolve_hype_hits` (collapse question→parent + dédup) câblé dans `hybrid_retrieve` (gate `HYPE_ENABLED`).
>   - Task 6 `845f675` — fix divergence top-k parent-child (`core/ask._effective_topk` suit le vrai gate `PARENT_CHILD_ENABLED`).
>   - Plomberie `18872ac` — `rebuild_vectors._Doc` préserve `questions` en liste (chemin rapide HyPE : re-embed depuis Mongo, ~30s vs full re-ingest).
>
> **Mesures directionnelles (⚠️ `--limit 8`, INDICATIVES — pas encore les 30-q) — mêmes 8 questions :**
>
> | Config | recall | prec | faith | aa-rel | kw_hit | lat |
> |---|---|---|---|---|---|---|
> | B1 (flags off, corrigé) | 0.163 | 0.615 | 0.246 | 0.557 | 0.167 | 38.0s |
> | **HyPE** | **0.269** | 0.547 | 0.253 | 0.526 | 0.167 | **22.7s** |
> | HyPE+headers | 0.175 | 0.619 | 0.324 | 0.553 | 0.167 | 13.0s |
> | HyPE+parent-child | 0.170 | 0.710 | 0.341 | 0.497 | 0.208 | 23.8s |
>
> **Lecture :** HyPE = **↑recall +0.106 (sa cible n°1) + ↓latence** → signal positif fort. Headers & parent-child = ↑precision/faithfulness mais annulent le gain recall (arbitrage précision↔rappel à trancher). Ingestion HyPE **linéaire** (×3.7 vecteurs → ×3.3 temps ; zéro LLM en plus ; requête coût nul). Vigilance échelle → borner `HYPE_MAX_QUESTIONS` **élastiquement** (cf mémoire `rag-elasticity-principle`).
>
> **À FAIRE demain (dans l'ordre) :**
> 1. **Redémarrer `serve.py`** (l'app en mémoire a l'ancien code retrieve, sans résolution HyPE).
> 2. **Confirmer sur 30-q** (la décision d'adoption ne se prend PAS sur 8-q) : pour chaque flag, `HYPE_ENABLED=true .venv/bin/python scripts/rebuild_vectors.py` puis `PYTHONUTF8=1 HYPE_ENABLED=true RAGAS_JUDGE_CONCURRENCY=4 python -m evals.run_eval --mode ragas` (sans `--limit`, sans `--no-save`). Mesurer : B1 → HyPE → +headers → +parent-child → combo. Comparer à B1 (même code), pas à l'ancien baseline figé.
> 3. **Décider** chaque technique sur gain net RAGAS ; figer les flags gagnants dans `.env` ; index reconstruit dans l'état adopté.
> 4. **Task 8** : combo + sonde use case nord + **revue Opus finale** (merge-ready) du diff Vague 1.
> 5. Puis **Vague 2** (spec à écrire) avec la même méthode.
>
> **État index CE SOIR :** restauré content-only (773 vecteurs), cohérent avec `.env` (tous flags Vague 1 = off). Aucune modif `.env` (mesures pilotées par env-var process). Ledger détaillé : `.superpowers/sdd/progress.md`.

---

Point de départ de la prochaine session. Branche : `feat/chantier1-retrieval-multidoc`.
Ledger d'exécution : `.superpowers/sdd/progress.md`. Mémoire projet :
`~/.claude/projects/-home-marsattacks/memory/chantier1-retrieval-multidoc.md`.

## Règle directrice (utilisateur)

**RAGAS = la boussole.** Toute technique, où qu'elle soit dans la pipeline
(Vagues 1-2, futures, ou piochées sur github.com/NirDiamant/RAG_Techniques), est
**mesurée avant/après** avec `python -m evals.run_eval --mode ragas` et **gardée
seulement sur gain net** (context recall/precision, faithfulness, answer
relevancy, hit@k) sans coût de latence inacceptable. **Use case nord** : synthèse
multi-document exhaustive et fiable sur 5-10 docs
(« crée une table catégorisant tous les types d'attaquant »).

## Livré (tout commité, revu Opus « merge-ready »)

- **Chantier 1a** — qualité retrieval : intention, pool élastique, plancher de
  couverture par document, poids 0.5/0.5, abstention recalibrée, rejet chunks
  vides, invalidation BM25, verrou multi-format .pdf/.docx/.xlsx. (recall 0.53→0.64)
- **Chantier 1b** — synthèse corpus map-reduce déterministe (prefilter large →
  MAP par doc, isolation d'erreur → REDUCE par lots avec attribution `[src:]`),
  routage intention `aggregate`→synth, SSE glass-box.
- **Chantier 2** — périmètre non-collant (upload ne restreint plus), badge
  réversible, routeur élargi (Agent en auto), **onglet Informations**, agent
  non-collant (`scope_arguments`).
- **Vague 0 — harnais RAGAS local (la boussole)** : `core/evaluation.py`
  (decompose_claims + faithfulness/context_recall/context_precision(rank-aware)/
  answer_relevancy_ragas ; juge+embeddings injectables ; heuristiques `*_lexical`),
  `evals/run_eval.py --mode ragas`, **jugements parallélisés 2-GPU**
  (`RAGAS_JUDGE_CONCURRENCY=4`, résultats identiques), `CORPUS_MAP_CONCURRENCY=4`.

## État opérationnel

- **App EN COURS** (`serve.py` : API :8000 + front :3000, Mongo :27017, Ollama
  :11434). ⚠️ **Redémarrer `serve.py`** pour charger le parallélisme
  `CORPUS_MAP_CONCURRENCY=4` (le process actuel a l'ancien code).
- **Calibration 1a active** dans `.env` (poids 0.5/0.5, seuil 0.505, pool 120,
  plancher 1) — appliquée via `scripts/apply_env_calibration.sh`.
- **GPU** : Ollama déjà réglé (systemd : `NUM_PARALLEL=4`, `MAX_LOADED=4`,
  `KEEP_ALIVE=-1`, 2× RTX 6000 Ada 48 GB). Jugements RAGAS parallélisés. **Reste**
  pour saturer les 2 GPU : parallélisme **au niveau des questions** dans
  `evals/run_eval.py` (générer sur GPU 1 pendant qu'on juge sur GPU 0) — attention
  thread-safety Chroma/cross-encoder. Optimisation notée.

## BASELINE RAGAS (référence des Vagues 1-2)

Run `ragas-baseline-v0-final` (30 questions, code parallèle) — lire l'agrégat dans
`evals/last_eval.json` ou Mongo `eval_runs`. Valeurs à reporter ici :
<!-- BASELINE_METRICS -->
**BASELINE (ragas-baseline-v0-final, 30 q, Mongo eval_runs id=6a60d0d645ad8dc54ca9c29d) :**
- context_recall = **0.529**
- context_precision = **0.809** (rank-aware)
- faithfulness = **0.495**
- answer_relevancy = **0.599** (agrégat complet 30 q, recalculé après correctif ;
  l'estimation ~0.53 était un sanity limit-2)
- keyword_hit_rate = **0.656**
- latence = **40 s/question** (série entre questions, jugements parallèles ; full
  2-GPU = optim question-level notée).

✅ **Correctif agrégat RAGAS — FAIT (2026-07-22)** : `aggregate_metrics`
(`core/evaluation.py`) remonte désormais `answer_relevancy` et `keyword_hit_rate` ;
`run_eval` écrit `evals/last_eval.json` (helper `_write_last_eval`, tous modes dont
ragas). Régression couverte : `tests/test_evaluation_ragas.py` +
`tests/test_run_eval_ragas.py`. L'agrégat baseline en Mongo (id=6a60d0d6…) a été
backfillé et `evals/last_eval.json` reflète le vrai baseline 30 q. La boussole est
complète : mesure Vague 1 possible.

## Tâches restantes (dans l'ordre)

### Vague 1 — précalcul haut rendement (spec écrit)
`docs/superpowers/specs/2026-07-22-vague1-precompute.md`. À faire : plan puis
exécution subagent-driven. Trois techniques, **chacune mesurée avant/après RAGAS**
(ré-ingérer le corpus quand la technique touche l'ingestion) :
1. **HyPE** — embarquer les questions hypothétiques (déjà générées, aujourd'hui
   BM25 seulement) et les indexer pointant vers le chunk parent ; résoudre→parent
   + dédup à la récupération. Cible : ↑ context recall / hit@k.
2. **En-têtes contextuels** — préfixer chaque chunk de son fil d'Ariane avant
   embedding. Cible : ↑ context precision.
3. **Activer parent-child** (`PARENT_CHILD_ENABLED=false` aujourd'hui) + calibrer.
   Cible : ↑ context recall / faithfulness.

### Vague 2 — précision/fidélité & robustesse (spec à écrire)
Détaillé dans `docs/superpowers/research/2026-07-22-rag-techniques-improvement-plan.md` :
4. **Compression contextuelle extractive** (réutilise les scores cross-encoder,
   zéro LLM/requête) → ↑ faithfulness, ↓ latence.
5. **CRAG adapté offline** (sur score CE bas : reformuler + `retrieve_wide` +
   élargir, au lieu d'abstenir).
6. **Step-back prompting** pour l'agent (questions complexes).
7. **RAPTOR récursif complet** (aujourd'hui résumés plats par section) — nourrit
   aussi la synthèse corpus 1b.

### Vague 3 — renforcement de l'agent (exhaustivité multi-doc) — brainstorm fait
Source : `docs/superpowers/research/2026-07-22-agent-techniques-triage.md` (repos
NirDiamant Agent_Memory_Techniques + GenAI_Agents, triés offline). Tâches, chacune
mesurée RAGAS avant/après, dans l'ordre de valeur :
1. **Boucle de réflexion d'exhaustivité** — étendre `core/self_rag.py` au
   map-reduce (critère de couverture « tous les docs/catégories balayés ? »,
   retry borné). *Levier n°1 du use case nord.* → context recall + answer relevancy.
2. **Câbler `list_sources` + `extract_from_document`** (déjà écrits dans
   `tools/corpus_tools.py`, pas branchés) dans l'agent/planner. → context recall + hit@k.
3. **Retrieval MMR / diversité** — casse le top-k redondant, force la
   représentation inter-documents. → context recall + hit@k.
4. **Dédup sémantique au REDUCE** (avant fusion LLM, union des `[src:]`).
   → faithfulness + context precision.
5. **Index d'entités = checklist d'exhaustivité** (pré-passe cachée). → context recall.
Exclus (documentés) : plateformes mémoire (Mem0/Zep/Graphiti/Letta), agents
web/cloud (offline), frameworks multi-agents lourds, benchmarks mémoire.

### Ensuite
- **Chantier 3** — génération PDF/DOCX à la demande (python-docx présent ; aucune
  lib PDF → embarquer un wheel offline).
- **Optim 2-GPU** : parallélisme au niveau des questions dans le runner d'éval.

## Méthode de reprise

Même workflow qu'ici : pour chaque vague/technique → (spec si absent) → plan
(`writing-plans`) → exécution `subagent-driven-development` (ledger
`.superpowers/sdd/progress.md`), **avec mesure RAGAS avant/après** et revue finale
Opus. Ne rien adopter sans preuve RAGAS.
