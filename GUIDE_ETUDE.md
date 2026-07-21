# Guide d'étude — comment ce projet est construit

Porte d'entrée pour un **relecteur** (étudiant, pair) qui veut comprendre *comment*
le projet a été réalisé et donner un retour utile. Ce guide donne un **ordre de
lecture**, une **carte des modules** et les **décisions de conception** à critiquer.
Pour le positionnement et les résultats, voir [README.md](README.md).

> **L'Outil RAG documentaire.** Pile locale : **Ollama** (`mistral-small3.2` +
> `bge-m3`) et **MongoDB**. 100 % local, pas de clé API.

## Comment lire ce dépôt

Ordre conseillé (du plus parlant au plus technique) :

1. **`README.md`** puis **ce guide** — le quoi et le pourquoi.
2. **Un flux de bout en bout**, en suivant les pointeurs ci-dessous : lis les
   4-5 fichiers du chemin principal, ignore le reste au début.
3. **Les tests** (`tests/`) — ils montrent le comportement attendu en quelques
   lignes, sans dépendre d'Ollama.
4. **Les décisions de conception** (dernière section) — c'est là que le retour a le
   plus de valeur.

Principe transverse du projet : **chaque technique est un levier activable et
mesurable** (rien n'est « magique »), et on **mesure avant d'optimiser** (les évals).

---

## L'Outil RAG

**But** : répondre à une question sur des documents techniques (ANSSI / Critères
Communs) avec des réponses *ancrées* et *citées*, en local.

### Flux d'une question (chemin principal)

```
question ─▶ core/router.py      (route Auto/RAG/Agent, SANS appel LLM)
         ─▶ core/ask.py         (orchestre le retrieval hybride)
              ├─ retrieval/semantic_search.py   (vecteurs, Chroma)
              ├─ retrieval/keyword_bm25.py      (BM25, Mongo)
              ├─ retrieval/rrf.py               (fusion des deux classements)
              ├─ retrieval/cross_encoder.py     (rerank fin)
              └─ retrieval/context_refine.py    (dédup + anti lost-in-the-middle)
         ─▶ core/llm_answer.py   (génère la réponse ancrée + citations)
         ─▶ core/evaluation.py   (vérifie la fidélité de la réponse)
         ─▶ core/self_rag.py     (auto-correction si la vérif échoue)
```

Variante **agent** : `core/agent.py` (boucle ReAct) appelle l'outil
`tools/rag_tool.py`, exposé aussi en **MCP** via `rag_mcp_server.py`.

### Carte des modules

| Rôle | Fichiers |
|---|---|
| Entrée / config | `serve.py`, `api/main.py` (+ `web/`), `env_config.py` |
| Ingestion | `core/ingest.py`, `indexing/{chunking,embedding,keyword_index,store_mongo}.py`, `nlp/chunk_enhancer.py`, `preprocessing/pdf_to_markdown.py` |
| Retrieval hybride | `retrieval/{retrieve,semantic_search,keyword_bm25,rrf,cross_encoder,parent_child,context_refine,vector_store}.py` |
| Génération | `core/{llm_answer,llm_client,summarize}.py` |
| Orchestration | `core/{router,ask,self_rag,evaluation,model_router}.py` |
| Agent / MCP | `core/agent.py`, `tools/rag_tool.py`, `rag_mcp_server.py` |
| Transverse | `utils/{security,tracing,sources,text_utils,logging_config}.py` |
| Évaluation | `evals/run_eval.py`, `evals/README.md` |

---

## Lancer & tester

```bash
# Application complète : Mongo + Ollama + front Next.js + API FastAPI
python serve.py

# Tests (hors-ligne, sans Ollama)
python -m pytest tests

# Évaluation chiffrée (avec LLM)
python -m evals.run_eval --mode retrieval   # hit@k / recall
```

---

## Où porter ton regard (décisions à critiquer)

Les choix les plus intéressants à challenger — c'est là qu'un retour aide vraiment :

- **RAG — mesurer avant d'optimiser.** Des leviers ont été *retirés* car la donnée ne
  les justifiait pas (GraphRAG = 0 effet, réécriture de requête négative). Question :
  la méthode d'A/B (`evals/`) est-elle assez robuste ?
- **RAG — routeur sans LLM** (`core/router.py`) : router Auto/RAG/Agent par heuristique
  plutôt que par un appel LLM. Bon compromis latence/robustesse, ou trop rigide ?
- **RAG — sortie de LangChain** au profit d'un client HTTP direct (`core/llm_client.py`)
  pour garder la main sur `num_ctx`/`keep_alive` (VRAM). Simplicité vs portabilité.
- **Transverse — cadre PoC mono-poste assumé** (verrou fichier, pas d'auth).
