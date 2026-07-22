# Reprise — état du projet & tâches restantes (2026-07-22)

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
(en cours de calcul — à compléter à la fin du run)

Repère (1er run, code antérieur, answer_relevancy manquant) : context_recall
0.536, context_precision 0.817, faithfulness 0.548.

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

### Piste « renforcement agent » (brainstorm fait, à trier/exécuter)
`docs/superpowers/research/2026-07-22-agent-techniques-triage.md` (issu des repos
NirDiamant Agent_Memory_Techniques + GenAI_Agents, offline). Top candidats pour
l'exhaustivité multi-doc : (1) retrieval MMR/diversité ; (2) **câbler les tools
`list_sources`/`extract_from_document` déjà écrits** dans l'agent ; (3) dédup
sémantique au REDUCE ; (4) **boucle de réflexion d'exhaustivité** (étendre
`self_rag` au map-reduce, critère de couverture, retry borné) — comble le trou
d'exhaustivité du use case nord ; (5) index d'entités = checklist. Exclus :
plateformes mémoire (Mem0/Zep/Letta), agents web/cloud, multi-agent lourd.

### Ensuite
- **Chantier 3** — génération PDF/DOCX à la demande (python-docx présent ; aucune
  lib PDF → embarquer un wheel offline).
- **Optim 2-GPU** : parallélisme au niveau des questions dans le runner d'éval.

## Méthode de reprise

Même workflow qu'ici : pour chaque vague/technique → (spec si absent) → plan
(`writing-plans`) → exécution `subagent-driven-development` (ledger
`.superpowers/sdd/progress.md`), **avec mesure RAGAS avant/après** et revue finale
Opus. Ne rien adopter sans preuve RAGAS.
