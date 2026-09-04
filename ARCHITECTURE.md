# Architecture — AI for Galileo

Carte de lecture du dépôt. Objectif : qu'un ingénieur situe **où vit quoi** en
une page, sans lire tous les docstrings. Application **100 % locale, mono-poste**
(Ollama + MongoDB + Chroma) : un **RAG documentaire adaptatif** conçu pour les analyses structurées multi-documentaires.

## Les trois couches

```text
        ┌────────────────── UI ──────────────────┐
        │  web/  (Next.js)  ──► api/  (FastAPI)   │   python serve.py
        └───────────────┬────────────────────────┘
                        │
        ┌───────────────┴───────────────────────┐
        │  MOTEUR                                │
        │  core/ retrieval/ indexing/            │
        │  nlp/ preprocessing/ utils/            │
        │  tools/ env_config.py                  │
        └───────────────┬───────────────────────┘
                        │
        ┌───────────────┴───────────────────────┐
        │  DONNÉES / RUNTIME                     │
        │  MongoDB (chunks, index BM25, sessions)│
        │  Chroma (vecteurs)   models/ (reranker)│
        │  Ollama (LLM)                          │
        └────────────────────────────────────────┘
```

**Principe** : l'UI est un adaptateur mince. Toute la logique RAG vit dans le
moteur ; `api/` ne fait que le présenter — il ne le duplique pas.

## Points d'entrée

| Entrée | Rôle |
|---|---|
| `serve.py` | Démarre Mongo + Ollama, puis l'API FastAPI + le front Next.js |
| `api/main.py` | Assemble les routeurs FastAPI (rag, sessions, documents, system) |
| `rag_mcp_server.py` | Serveur MCP (stdio) exposant le RAG comme outil |
| `core/agent.py` | Agent ReAct en CLI (`python -m core.agent "…"`) |
| `evals/` | Harnais d'évaluation chiffrée |
| `diagnostic.py` | État des services + routage + vector store |

## Flux RAG (de la question à la réponse sourcée)

1. **Ingestion** — `preprocessing/pdf_to_markdown.py` → `indexing/chunking.py`
   (chunking sémantique) → `nlp/chunk_enhancer.py` (enrichissement + RAPTOR) →
   `indexing/embedding.py` (Chroma) + `indexing/keyword_index.py` (BM25 → Mongo).
   Orchestré par `core/ingest.py`.
2. **Retrieval** — `core/ask.py` → `retrieval/retrieve.py::hybrid_retrieve` :
   sémantique (Chroma) + BM25 en parallèle → fusion `retrieval/rrf.py` → rerank
   `retrieval/cross_encoder.py` (bge-reranker, `models/`) → `parent_child` →
   `context_refine`.
3. **Décision** — `core/router.py` classe la requête et sélectionne RAG, synthèse corpus ou Agent. `Analyse profonde` reste un choix explicite.
4. **Génération** — `core/llm_answer.py` sert le RAG standard; `core/corpus_extract.py` et `core/synthesize_corpus.py` servent le MAP/REDUCE; `core/agent.py` sert l’agent. `core/model_router.py` route chaque rôle LLM.
5. **Qualité** — couverture, métriques et stratégie sont diffusées en SSE. `core/evaluation.py` et `evals/run_eval.py` assurent les évaluations RAGAS locales.

## Surfaces UI

- **Chat** : Auto, RAG/Agent en mode Expert et Analyse profonde; le modèle de la barre désigne uniquement la rédaction finale.
- **Informations** : sources, couverture et métriques.
- **Agents** : prompts effectifs, versions, restauration et tests A/B.
- **Réglages** : routage par rôle et paramètres experts.
- **Méthode SRA** : génération d une table Risk Objectives éditable et exportable en CSV/XLSX.

État détaillé de reprise : `docs/PROJECT_STATE.md`.

## Module SRA

SRA est une verticale métier native du moteur partagé, pas un pipeline parallèle. `core/sra/` fournit le contrat RO et le MAP spécialisé ; l orchestration réutilise la phase MAP concurrente de `core/synthesize_corpus.py`, le routage `extract`, les preuves, les sources et l observabilité. `api/sra.py` expose les dossiers, la génération SSE, l édition et les exports CSV/XLSX ; l interface réside sous `web/src/app/sra/`. Le Chat reste la surface généraliste du même socle et SRA sa surface métier spécialisée. Pour les étapes RO et TO, SRA utilise exclusivement une Analyse profonde par balayage MAP exhaustif, suivie d une consolidation déterministe sans résumé LLM destructif. Chaque génération persiste une trace métier : documents, fenêtres, candidats RO, consolidation et couverture.

## Stockage

- **MongoDB** — chunks, index BM25 sérialisé, sessions de conversation, dossiers SRA (`sra_projects`).
- **Chroma** — vecteurs (embeddings bge-m3).
- **`models/`** — poids du cross-encoder (gitignoré, local).
- **Ollama** — génération (`mistral-small3.2`) + embeddings (`bge-m3`).

> `data/`, `models/`, `web/.next`, `web/node_modules`, `.venv` sont
> **gitignorés** : ce sont des états runtime, pas des sources à relire.

### SRA — étape TO

La table TO v2 regroupe les scénarios d’attaque par RO Type canonique : une ligne par catégorie d’attaquant, contenant des attaques, leurs objectifs, les cibles architecturales concernées, une justification propre au scénario et les citations prouvant les cibles. Les caractérisations et preuves de la table RO fondent le profil de l’attaquant; le corpus d’architecture dédié `docs/PDF_TO` fonde l’existence et le rôle des cibles. L’adjudication refuse les associations génériques sans attaque et objectif explicites, puis une finalisation optionnelle fusionne les synonymes sans perte de cibles ou de preuves. Les anciennes tables TO v1 restent lisibles. La génération refuse de démarrer si la table RO est absente ou si un document TO demandé n’est pas indexé.
