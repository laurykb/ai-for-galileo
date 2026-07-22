# Chantier 1 — Socle retrieval, synthèse corpus & ingestion multi-document

Date : 2026-07-22
Statut : design validé (approche A + abstention recalibrée/intention + synthèse corpus map-reduce)
Périmètre : RAG direct **et** mode Agent (même socle de retrieval)

## Contexte

AI_for_ssh_v3 est un RAG documentaire 100 % local (offline). Cible réelle
confirmée : **jusqu'à 50 documents de 100–200 pages chacun** (soit
≈ 5 000–10 000 pages, **≈ 15 000–22 000 chunks** à 1000 caractères/chunk).

Deux besoins distincts coexistent, et **aucun** n'est correctement servi
aujourd'hui :

1. **Q&A pointue** — « C'est quoi un IDS ? », « Quelles sont les exigences de
   chiffrement ? ». Aujourd'hui : les petits documents sont noyés, les
   questions génériques répondent « je ne trouve pas ».
2. **Synthèse agrégative corpus-large** — « Catégorise toutes les attaques
   connues de ce corpus ». Aujourd'hui : **structurellement impossible**. Tout
   le pipeline est plafonné par un top-k de 15 chunks, qui voit **< 0,1 %** d'un
   corpus de 50 documents. Il n'existe aucune brique qui balaie tout le corpus.

Ce chantier corrige (1) et **construit** (2), plus fiabilise l'ingestion à
l'échelle visée. La fondation de données est saine (Chroma + BM25 Mongo
contiennent bien tous les documents) : le problème est le scoring/recall et
l'absence de mode agrégatif, pas le stockage ni le routage.

Hors périmètre (autres chantiers) :
- **Chantier 2** — périmètre & contexte UI : sélecteur qui ne revient pas sur
  « Tous », Agent collant au dernier document, restauration de session, audit
  du routeur `auto` (regex trop étroite → l'agent ne se déclenche presque
  jamais). *Note : le détecteur d'intention défini ici est partagé avec cet
  audit.*
- **Chantier 3** — génération PDF/DOCX à la demande.

## Diagnostic confirmé (mesuré sur le corpus courant)

Corpus de test : un document de 770 chunks + un de 4 chunks.

1. **Petits documents noyés.** Toute requête (même « compare les deux
   documents ») renvoie **15/15 chunks du gros document, 0 du petit**. Top-k
   fixe (`NUM_CHUNKS=15`) sur corpus déséquilibré → le petit ne gagne jamais.
2. **Décalage de vocabulaire.** `WEIGHT_BM25=0.7` / `WEIGHT_SEMANTIC=0.3`. BM25
   ne matche que les tokens exacts : « IDS » ne matche pas « détecteur
   d'intrusion intelligent ». Le sémantique, qui ferait le pont, est
   sous-pondéré.
3. **Seuil d'abstention sur le plancher de bruit.** Le cross-encoder sort ~0.50
   quand aucun passage ne « répond » ; `CE_RELEVANCE_THRESHOLD=0.505` est pile
   dessus (« de quoi parle » = 0.505 passe, « c'est quoi un IDS » = 0.501
   échoue). Abstention brutale sur les questions génériques.
4. **Chunks vides indexés** (`''`), qui polluent BM25 et le pool de candidats.
5. **Cache BM25 périmé à l'ingestion.** Deux caches `"__all__"` existent
   (`core/ask._bm25_cache` et `indexing/keyword_index._bm25_cache`).
   `clear_retrieval_caches()` (appelé à l'ingestion) ne vide que le premier ;
   `keyword_index.invalidate_bm25_cache()` est défini mais **jamais appelé** →
   nouveau document absent du BM25 jusqu'au redémarrage.
6. **Aucune capacité agrégative.** RAPTOR = résumés **plats par section**
   (`nlp/chunk_enhancer.py:501`, « RAPTOR simplifié », pas d'arbre), qui
   concourent au top-k sans traitement spécial. Le planner
   (`core/planner.py`) décompose par **thème** (2–5 sous-questions, borne
   ~30 passages), **jamais par document**. L'agent (`core/agent.py`) n'a
   qu'**un** outil (`rag_search`) et **ne peut ni lister les documents ni
   boucler dessus**. `entity_graph` est un placeholder vide.

## Design

Deux chemins de réponse, aiguillés par un **détecteur d'intention** partagé ;
un socle de retrieval commun ; les correctifs d'ingestion/scaling.

### A. Détecteur d'intention (aiguillage)

Un helper sans LLM (réutilise le style `_norm` + regex de `core/router.py`),
p. ex. `retrieval/intent.py`, classe la question en :
- **`aggregate`** — agrégation corpus-large : « toutes/tous les », « catégorise »,
  « liste … du corpus », « l'ensemble des », « recense », « inventaire »,
  « compare tous les », « dans tout le corpus », « chaque document ». → chemin
  **Synthèse corpus** (section C), surtout si périmètre = Tous.
- **`exploratory`** — générique/définitionnel : « de quoi parle », « c'est
  quoi », « résume », « présente », « explique ». → **ne pas abstenir** (section B.3).
- **`pointed`** — sinon, Q&A factuelle → **retrieval pointu** (section B).

Ce classifieur est la **source unique** d'aiguillage, partagée avec l'audit du
routeur (Chantier 2).

### B. Retrieval pointu (Q&A) — approche A « plancher par document »

1. **Pool de candidats élargi avant rerank.** Sémantique et BM25 remontent
   chacun `CANDIDATE_POOL` (~60) candidats au lieu du top-k final, pour que les
   chunks des petits documents entrent dans le set reranké.
   Fichiers : `retrieval/vector_store.py` (n candidats sémantiques),
   `indexing/keyword_index.py` (`bm25_search` topn), `retrieval/retrieve.py`.
2. **Plancher par document.** Si `source_filter=None`, garantir ≥ `PER_DOC_FLOOR`
   (1) chunk par document **présent dans les candidats ET au-dessus d'un seuil
   de pertinence minimal**, avant de compléter par le classement global. Ne
   jamais injecter un document non pertinent. Appliqué **après** rerank.
3. **Top-k global adaptatif borné.** Remplacer `NUM_CHUNKS` fixe par un top-k
   qui s'adapte au nombre de documents pertinents, plafonné (`MAX_CHUNKS` ~24)
   pour ne pas noyer la génération.
4. **Rééquilibrage poids** : `WEIGHT_SEMANTIC=0.5` / `WEIGHT_BM25=0.5` (départ,
   à affiner par l'éval) — couvre le décalage de vocabulaire sans perdre la
   précision BM25 sur les identifiants techniques exacts.

### B.3 Abstention recalibrée + intention

- Recalibrer `CE_RELEVANCE_THRESHOLD` pour exiger une **marge nette** au-dessus
  de ~0.50 (départ ~0.48, à fixer par l'éval), au lieu de trancher à 0.001 près.
- Si l'intention est **`exploratory`**, **ne pas déclencher** l'abstention :
  retourner le meilleur contexte disponible (la génération sourcée reste
  honnête). Factoriser la garde d'abstention de `core/ask.py` (occurrences
  ~L147 et ~L205) en **un seul helper** partagé pour éviter la divergence
  streaming/non-streaming.

### C. Synthèse corpus map-reduce (capacité nouvelle)

Déclenchée par l'intention **`aggregate`**. Pipeline **déterministe**
(`core/synthesize_corpus.py`) — pas laissé au libre-arbitre du ReAct, pour
garantir l'exhaustivité et la reproductibilité.

1. **Pré-filtrage des documents pertinents.** Une passe de retrieval large
   (BM25 + sémantique sur tout le corpus) → ensemble des documents ayant des
   hits au-dessus d'un seuil pour l'aspect demandé. Évite de MAP-er les 50 docs
   quand 8 sont concernés. Si presque tout le corpus est pertinent, MAP-er tout
   (avec bornes ci-dessous).
2. **MAP — extraction par document.** Pour chaque document retenu, extraction
   ciblée de l'aspect demandé (les attaques, les menaces, …) à partir : des
   **résumés RAPTOR de section** du doc + des chunks scopés sur ce doc
   (`source_filter=<doc>`, k élevé). Réutilise la machinerie de
   `core/summarize.py` (`summarize_document`, `_MAX_MATERIAL_CHARS`). Sortie :
   liste structurée d'items **avec citation de source**. Concurrence **bornée**
   (`CORPUS_MAP_CONCURRENCY`, ~2–3 sur GPU local).
3. **REDUCE hiérarchique.** Fusionner les listes par lots (éviter le
   dépassement de contexte à 50 docs), dédupliquer sémantiquement, catégoriser,
   produire la **synthèse finale** avec attribution par item (quel document).
   Réduction en arbre si nécessaire (réduire les réductions).
4. **Glass-box streaming.** Émettre la progression via le pattern SSE existant
   (`api/rag.py`) : `stage` (« pré-filtrage »), un frame par document MAP-é
   (« document 3/50 : … »), puis « agrégation », puis la réponse. Respecte le
   principe glass-box scannable de l'app.
5. **Bornes / garde-fous.** Plafond de documents MAP-és par requête
   (`CORPUS_MAX_DOCS`, défaut = corpus entier borné à 50) ; si dépassé, le
   signaler explicitement (pas de troncature silencieuse). Coût = O(nb docs
   pertinents) appels LLM : réservé aux requêtes `aggregate`, rares et lourdes.

### C.1 Nouveaux outils agent (fan-out)

Pour que le mode Agent bénéficie aussi du corpus (et rende le pipeline C
observable), exposer dans `tools/` :
- **`list_sources()`** — noms des documents (donnée existante :
  `api/documents.py` agrégation Mongo `$group` sur `source`).
- **`extract_from_document(document, aspect)`** — l'étape MAP réutilisable.

Enregistrés dans `tools/rag_tool.py` (`_TOOLS`) et déclarés dans l'onglet
Informations (principe : tout nouveau skill agent doit y être expliqué).

### D. Ingestion & scaling

1. **Rejet des chunks vides** (`page_content.strip()==""`) à la
   création/indexation, pour Chroma **et** BM25 (`core/ingest.py` en amont
   commun ; `indexing/embedding.py`, `indexing/keyword_index.build_bm25_index`).
2. **Invalidation BM25 fiable** : appeler
   `indexing/keyword_index.invalidate_bm25_cache()` là où
   `clear_retrieval_caches()` est déjà appelé (`core/ingest_queue.py` ~L65-66).
3. **Coût BM25 fusionné en mémoire** à 50 docs / ~20k chunks : reconstruction
   `BM25Okapi` au premier appel — à **mesurer** (temps de cold-start, mémoire) ;
   si problématique, sérialiser les tokens ou passer à un index persistant
   incrémental (renvoyé au-delà de la cible si besoin, hors périmètre).

## Nouveaux paramètres (`env_config.py`, tous surchargeables via `.env`)

| Variable | Défaut proposé | Rôle |
|---|---|---|
| `CANDIDATE_POOL` | 60 | Pool de candidats par branche avant rerank |
| `PER_DOC_FLOOR` | 1 | Chunks minimum par document pertinent (mode « Tous ») |
| `MAX_CHUNKS` | 24 | Plafond du top-k adaptatif (Q&A) |
| `WEIGHT_SEMANTIC` | 0.5 | (depuis 0.3) |
| `WEIGHT_BM25` | 0.5 | (depuis 0.7) |
| `CE_RELEVANCE_THRESHOLD` | ~0.48 | (recalibré depuis 0.505, + intention) |
| `CORPUS_MAP_CONCURRENCY` | 2 | Concurrence des extractions MAP |
| `CORPUS_MAX_DOCS` | 50 | Plafond de documents MAP-és par requête |

Contrainte offline respectée : aucun téléchargement, aucun nouveau service ;
tout tourne sur Ollama + Chroma + Mongo déjà en place.

## Flux de données (après changements)

```
api/rag.py  ──►  détecteur d'intention (retrieval/intent.py)
   ├─ pointed / exploratory ─► core/ask.process_query[_stream]
   │      └─ _load_bm25(None) → hybrid_retrieve (pool large + rerank)
   │         → PLANCHER par document → top-k adaptatif
   │         → garde d'abstention (skip si exploratory) → génération sourcée
   └─ aggregate ─► core/synthesize_corpus.py
          → pré-filtrage docs → MAP par document (borné) → REDUCE hiérarchique
          → synthèse catégorisée + attribution  (SSE glass-box)
```

## Vérification (bout en bout)

- **Sondes retrieval** (read-only, sans LLM) sur le corpus courant :
  - « c'est quoi un IDS ? » → **répond** (plus d'abstention), ≥1 chunk du doc IDS.
  - « compare … entre les deux documents » → chunks **des deux** sources.
  - « exigences de chiffrement » → **pas** d'injection forcée du petit doc.
- **Synthèse corpus** : « catégorise toutes les attaques connues du corpus » →
  la réponse cite **plusieurs documents**, la progression SSE montre le MAP par
  document ; comparer l'exhaustivité vs une extraction manuelle sur un
  sous-ensemble connu.
- **Ingestion** : ajouter un 3ᵉ document puis, **sans redémarrer**, poser une
  question qui le cible → trouvé (valide l'invalidation BM25) ; vérifier
  qu'aucun chunk vide n'est indexé.
- **Éval de non-régression** : `python -m evals.run_eval --mode retrieval`
  avant/après ; calibrer poids + seuil.
- **UI** (`:3000`, Auto, périmètre « Tous ») : question multi-doc, question
  générique, et question d'agrégation → réponses sourcées multi-documents,
  plus de « je ne trouve pas » injustifié.
- **Tests** : `python -m pytest` + tests ciblés (plancher par document, helper
  d'intention, invalidation cache, REDUCE hiérarchique de la synthèse).

## Risques / points d'attention

- Poids et seuils sont des **valeurs à calibrer** par l'éval ; le design fige la
  *mécanique*, pas les chiffres.
- Le plancher par document ne doit **jamais** injecter un document non pertinent.
- La synthèse corpus est **coûteuse** (O(docs) appels LLM) : réservée à
  l'intention `aggregate`, bornée, jamais déclenchée sur une Q&A pointue.
- REDUCE hiérarchique obligatoire à 50 docs (le contexte LLM ne tient pas 50
  listes d'items d'un coup).
- Latence sur GPU local : la synthèse corpus peut prendre des dizaines de
  secondes → le streaming glass-box est essentiel pour la lisibilité.
- Le pré-filtrage des documents doit être **rappelant** (ne pas rater un
  document pertinent) — préférer trop de docs MAP-és que trop peu.
