# Vague 0 — Harnais d'évaluation RAGAS local

Date : 2026-07-22
Statut : design (décisions autonomes — à relire)
Périmètre : `core/evaluation.py` + `evals/run_eval.py` + jeu de référence

## Contexte

Le plan d'amélioration RAG (`docs/superpowers/research/2026-07-22-rag-techniques-improvement-plan.md`)
pose la Vague 0 comme prérequis : **mesurer** avant d'optimiser. Le harnais
actuel a les bons **noms** de métriques mais des **implémentations
heuristiques**, non fidèles à RAGAS :

- `context_recall` = recouvrement de tokens réponse-de-référence ↔ contexte
  (`core/evaluation.py:67`) — pas d'attribution par affirmation.
- `context_precision` = F1 de tokens > 0.1 sur top-k (`:79`) — pas de pertinence
  jugée ni rank-aware.
- `faithfulness_llm` = un score juge unique et grossier 0/0.5/1 (`:131`) — pas de
  décomposition de la réponse en affirmations vérifiées une à une.
- `answer_relevance_llm` = score juge direct (`:159`) — pas la méthode RAGAS
  (générer des questions depuis la réponse, comparer les embeddings).

On a en revanche déjà : `keyword_hit_rate` (hit@k, correct), un LLM-juge local
(`build_llm("judge")`, `_ask_judge`), des jeux dorés
(`evals/golden_qa_anssi_v2.json`, `golden_multihop_candidates.json`), un runner
à modes (`--mode retrieval|full`) avec agrégation, régression et sauvegarde
Mongo, et des embeddings locaux (`nlp/ollama_embedding.OllamaEmbedding`).

## Décisions de design (autonomes)

- **Implémenter les métriques RAGAS nous-mêmes, en local** — PAS la librairie
  `ragas` (absente, lourde, tire `datasets`/langchain). On réutilise
  `build_llm("judge")` (Ollama) et `OllamaEmbedding`. 100 % offline.
- **Décomposition par affirmations** (le cœur de RAGAS) : un helper LLM
  `decompose_claims(text)` extrait des affirmations atomiques, réutilisé par
  faithfulness et context_recall. Cohérent avec l'attribution par affirmation
  déjà présente dans l'app (`core/attribution.py`).
- **Juge et embeddings injectables** : toutes les fonctions acceptent `llm=` /
  `embed=` pour des **tests offline déterministes** (faux juge/embeddings), sans
  Ollama.
- **Nouveau mode `--mode ragas`** dans `run_eval` : calcule les 4 métriques
  RAGAS + hit@k, par item + agrégat + régression vs dernier run, sauvegarde
  Mongo. `retrieval`/`full` restent pour compat.
- **Garder les anciennes fonctions** heuristiques (rapides, sans LLM) sous des
  noms explicites (`context_recall_lexical`…) pour le mode `retrieval` léger ;
  les versions RAGAS sont les nouvelles par défaut du mode `ragas`.
- **Coût assumé** : le mode `ragas` fait plusieurs appels LLM par question
  (décomposition + jugements) — c'est une éval batch hors-ligne de production,
  pas un chemin de requête. `--limit` pour itérer vite.

## Métriques (définitions RAGAS retenues)

1. **Faithfulness** = (affirmations de la RÉPONSE soutenues par le contexte) /
   (total d'affirmations de la réponse). Décomposer la réponse → juger chaque
   affirmation vs contexte (soutenue oui/non) → ratio.
2. **Context recall** = (affirmations de la RÉPONSE DE RÉFÉRENCE attribuables au
   contexte récupéré) / (total d'affirmations de la référence). Décomposer la
   référence → juger l'attribution de chacune au contexte → ratio.
3. **Context precision** (rank-aware) = pour chaque chunk du top-k, juger sa
   pertinence (question+référence) → `precision@k` moyen aux positions
   pertinentes (mean average precision@k). Récompense les chunks pertinents
   **haut** dans le classement.
4. **Answer relevancy** = générer N (≈3) questions depuis la RÉPONSE (LLM),
   embarquer chacune (`OllamaEmbedding`), cosinus moyen avec l'embedding de la
   question d'origine. Mesure « la réponse répond-elle bien à la question ? ».
5. **hit@k** (`keyword_hit_rate`) — conservé tel quel.

## Composants

- `core/evaluation.py` :
  - `decompose_claims(text, llm=None) -> list[str]` (helper partagé).
  - `faithfulness_ragas(generated, chunks, llm=None) -> float`.
  - `context_recall_ragas(reference, chunks, llm=None) -> float`.
  - `context_precision_ragas(question, reference, chunks, topk, llm=None) -> float`.
  - `answer_relevancy_ragas(generated, question, llm=None, embed=None) -> float`.
  - Renommer les heuristiques existantes en `*_lexical` (compat mode retrieval).
- `evals/run_eval.py` : ajouter `--mode ragas` ; `_evaluate_item` branche les
  fonctions RAGAS ; rapport + agrégat + régression incluent les 4 métriques.
- Jeu de référence : réutiliser `golden_qa_anssi_v2.json` (questions + `answer`
  de référence + `expected_keywords`) ; le compléter si nécessaire pour couvrir
  les cas (factuel, multi-doc, générique). Documenter le format attendu.

## Flux

`python -m evals.run_eval --mode ragas [--limit N]` → pour chaque item : RAG →
retrieval + génération → décomposition d'affirmations (réponse & référence) →
jugements LLM → 4 métriques + hit@k → agrégat + régression vs `last_eval.json` /
Mongo `eval_runs`.

## Vérification

- **Tests offline** (`tests/test_evaluation_ragas.py`) : chaque métrique avec un
  **faux juge** injecté (retours déterministes) et un **faux embed** ; vérifier :
  faithfulness = soutenues/total ; context_recall = attribuables/total ;
  context_precision rank-aware (chunk pertinent en tête > en queue) ;
  answer_relevancy = cosinus moyen sur embeddings factices ; décomposition
  parse correctement une liste.
- **Run réel** (sonde) : `python -m evals.run_eval --mode ragas --limit 3` sur
  le corpus courant → imprime les 4 métriques, cohérentes (faithfulness élevée
  sur réponse fidèle, basse sur réponse inventée de contrôle).
- **Non-régression** : `python -m pytest -q` vert ; les modes `retrieval`/`full`
  existants inchangés.
- **Baseline** : archiver un run `ragas` de référence (nouvelle baseline pour
  toutes les vagues suivantes).

## Risques

- Coût/latence de l'éval `ragas` (plusieurs appels LLM/question) — c'est du
  batch hors-ligne ; `--limit` pour itérer.
- Qualité du juge = petit modèle local : les scores absolus sont indicatifs ;
  ce qui compte est la **comparaison avant/après** une technique (mêmes juge et
  jeu). Documenter cette réserve.
- Décomposition en affirmations dépend du LLM : garder des prompts stricts
  (liste, une affirmation par ligne) et un parsing robuste (réutiliser le style
  de `core/corpus_extract._parse_items`).
