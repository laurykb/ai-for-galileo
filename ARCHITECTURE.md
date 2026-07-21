# Architecture — AI for SSH

Carte de lecture du dépôt. Objectif : qu'un ingénieur situe **où vit quoi** en
une page, sans lire tous les docstrings. Application **100 % locale, mono-poste**
(Ollama + MongoDB + Chroma) : un **RAG documentaire** sur cibles de sécurité.

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
3. **Génération** — `core/llm_answer.py` (Ollama, streaming, citations). Couches
   optionnelles : `core/self_rag.py`, `core/planner.py` + `core/agent.py` (ReAct),
   `core/router.py` + `core/model_router.py`, `core/attribution.py`.

## Stockage

- **MongoDB** — chunks, index BM25 sérialisé, sessions de conversation.
- **Chroma** — vecteurs (embeddings bge-m3).
- **`models/`** — poids du cross-encoder (gitignoré, local).
- **Ollama** — génération (`mistral-small3.2`) + embeddings (`bge-m3`).

> `data/`, `models/`, `web/.next`, `web/node_modules`, `.venv` sont
> **gitignorés** : ce sont des états runtime, pas des sources à relire.
