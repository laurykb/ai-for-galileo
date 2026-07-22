# Plan d'amélioration RAG — techniques à adopter (source : NirDiamant/RAG_Techniques)

Date : 2026-07-22
Nature : plan de recommandations priorisées (pas d'implémentation). Optique
**latence / meilleure précision**, contrainte **100 % offline** (Ollama + Chroma
+ Mongo, petit GPU), mesure façon **RAGAS**.

## TL;DR

1. **D'abord la mesure.** On ne dispose aujourd'hui que d'un `hit@k` retrieval
   (`evals/run_eval --mode retrieval`). Impossible de dire qu'une technique
   « améliore significativement » sans **context precision / context recall /
   faithfulness / answer relevancy**. → **Priorité 0 : harnais RAGAS local**
   (juge LLM via Ollama), sinon on optimise à l'aveugle.
2. **Meilleur rapport gain/latence** : les techniques qui **précalculent à
   l'ingestion** (coût payé une fois) plutôt qu'un **appel LLM par requête**.
   Trois gagnantes réutilisent des briques qu'on a déjà : **HyPE** (emb
   des questions synthétiques qu'on génère déjà mais qu'on n'envoie qu'à BM25),
   **en-têtes contextuels de chunk**, et **activer/améliorer le parent-child**.
3. **Robustesse ciblée** : un **CRAG adapté offline** (au lieu d'abstenir sur un
   score bas, retenter localement plus large) transforme nos abstentions en
   secondes chances.

## Ce qu'on a déjà (audit rapide)

| Technique du repo | Chez nous | Statut |
|---|---|---|
| Fusion Retrieval (BM25+vecteur+RRF) | `retrieval/retrieve.py`, `rrf.py` | ✅ |
| Intelligent Reranking (cross-encoder) | bge-reranker, `retrieval/cross_encoder.py` | ✅ |
| Document Augmentation (questions/mots-clés synthétiques) | `AUTO_QUESTIONS/AUTO_KEYWORDS` à l'ingestion | ⚠️ **partiel** : questions envoyées **à BM25 seulement**, pas embarquées (cf. HyPE) |
| RAPTOR (arbre récursif) | `nlp/chunk_enhancer.py` | ⚠️ **partiel** : résumés **plats par section**, pas d'arbre récursif |
| Context Window / Parent-Child | `retrieval/parent_child.py` | ⚠️ **présent mais DÉSACTIVÉ** (`PARENT_CHILD_ENABLED=false`) |
| Self-RAG | `core/self_rag.py` | ✅ (optionnel) |
| Reliable RAG / grounding | attribution + abstention + `core/evaluation.py` | ⚠️ partiel |
| Adaptive Retrieval | détecteur d'intention + routeur (RAG/Agent/Synthèse) | ✅ (Chantiers 1-2) |
| Hierarchical / global (GraphRAG-like) | synthèse corpus map-reduce (Chantier 1b) | ✅ (pour l'agrégation) |
| Multi-faceted filtering / diversité | plancher par document (1a), `CONTEXT_DEDUP` | ⚠️ partiel (pas de MMR) |
| Explainable Retrieval | boîte de verre (chunks + ce_score affichés) | ✅ |
| Query Transformations | `trim_only_rewrite` (nettoyage léger) | ⚠️ **quasi absent** (pas de décomposition/step-back/expansion) |
| Contextual Compression | — | ❌ |
| HyDE / HyPE | — | ❌ |
| Semantic / Proposition chunking | mode `technical` (semantic_text_splitter) | ⚠️ partiel |
| Corrective RAG (CRAG) | — | ❌ |
| Graph RAG (entités/relations) | `entity_graph` **vide** ; NER → BM25 seulement | ❌ |
| Multi-modal (tables/images) | Docling extrait les tables en markdown | ⚠️ partiel |
| Éval RAGAS (precision/recall/faithfulness) | `hit@k` retrieval seulement | ❌ **manque** |

## Priorité 0 — Harnais d'évaluation RAGAS local (prérequis)

**Quoi** : ajouter un mode d'éval qui calcule, sur un jeu de référence
(questions + contextes attendus + réponses de référence), les métriques :
**context precision**, **context recall**, **faithfulness**, **answer
relevancy**, en plus du `hit@k` existant. RAGAS sait tourner avec un **LLM juge
local** (Ollama) et des embeddings locaux — donc **offline**.

**Pourquoi** : c'est la « meilleure mesure de qualité des réponses » demandée, et
le garde-fou de tout le reste : chaque technique ci-dessous n'est **adoptée que
si l'éval montre un gain net sans coût de latence inacceptable**. Sans ça, on
devine.

**Coût / latence** : hors ligne de production (batch d'éval). Effort modéré :
étendre `evals/run_eval` (mode `ragas`), construire un petit jeu de référence
(30-50 Q/A sur le corpus réel).

**Cible RAGAS** : établit la **baseline** de toutes les métriques.

---

## Tier 1 — Fort gain, faible/modéré coût, excellente adéquation

### 1. HyPE — embarquer les questions hypothétiques (réutilise l'existant)

**Quoi** : on génère déjà `AUTO_QUESTIONS` questions par chunk à l'ingestion,
mais on ne les envoie **qu'à BM25** (`indexing/keyword_index._build_enriched_text`).
HyPE = **embarquer** ces questions et les indexer comme vecteurs pointant vers
leur chunk : à la requête, on matche **question-utilisateur ↔ questions du
chunk** (aligné sémantiquement), sans **aucun appel LLM à la requête**.

**Pourquoi pour nous** : gain recall/précision important, **coût requête nul**
(tout est précalculé), et ça capitalise sur une brique déjà en place. C'est la
version offline « intelligente » de HyDE (qui, lui, coûte un appel LLM par
requête → écarté).

**Coût** : ingestion un peu plus lourde (embeddings supplémentaires) + espace
Chroma. Requête : **inchangée**. → excellent rapport latence/précision.

**Cible RAGAS** : ↑ context recall, ↑ hit@k.

### 2. En-têtes contextuels de chunk (embarqués)

**Quoi** : préfixer chaque chunk de son **fil d'Ariane** (titre du document +
hiérarchie de section) **avant embedding**. On a déjà `heading`/`breadcrumb` en
métadonnées ; il s'agit de les **inclure dans le texte embarqué**.

**Pourquoi pour nous** : nos documents (cibles de sécurité, exigences numérotées)
sont très hiérarchiques ; un chunk isolé perd son contexte (« 6.2.1 » sans le
titre). Grosse amélioration de précision pour un coût d'ingestion quasi nul.

**Coût** : ingestion négligeable. Requête inchangée.

**Cible RAGAS** : ↑ context precision.

### 3. Activer + affiner le Parent-Child / Relevant Segment Extraction (RSE)

**Quoi** : `parent_child.py` existe mais est **désactivé**. On matche des chunks
fins puis on **restitue le segment parent cohérent** (section) au générateur.
RSE va plus loin : **assembler dynamiquement les chunks adjacents pertinents** en
segments continus.

**Pourquoi pour nous** : améliore la complétude et la fidélité (le LLM voit un
contexte cohérent, pas des fragments). On a déjà l'infra ; il faut l'activer,
mesurer, et borner (`PARENT_CHILD_MAX_CHARS`) pour ne pas exploser le contexte.

**Coût** : requête légèrement plus lourde (contexte plus long → génération un peu
plus lente). À calibrer vs le plafond de tokens.

**Cible RAGAS** : ↑ context recall + faithfulness (moins d'hallucination sur
contexte tronqué).

### 4. Compression contextuelle EXTRACTIVE (pas LLM)

**Quoi** : au lieu d'une compression LLM par requête (coûteuse), **réutiliser les
scores cross-encoder qu'on calcule déjà** pour ne garder, dans chaque chunk
retenu, que les phrases les plus pertinentes (compression extractive, zéro appel
LLM supplémentaire).

**Pourquoi pour nous** : réduit le bruit dans le contexte → **↑ faithfulness** et
**↓ tokens → ↓ latence de génération**. Double gain, sans nouveau modèle.

**Coût** : requête ~inchangée (réutilise le CE). Implémentation modérée.

**Cible RAGAS** : ↑ faithfulness, ↑ context precision, latence génération ↓.

---

## Tier 2 — Fort gain, coût plus élevé, stratégique

### 5. Corrective RAG (CRAG) adapté offline

**Quoi** : CRAG note la qualité du retrieval et, si faible, corrige. La version
du repo utilise une **recherche web** — **impossible offline**. Adaptation : on a
déjà `max_ce_score` (confiance) et l'abstention. Au lieu d'**abstenir**, sur un
score bas on **retente localement** : reformulation de requête + `retrieve_wide`
+ élargissement du périmètre, puis re-note. L'abstention ne survient qu'après
échec de la seconde chance.

**Pourquoi pour nous** : transforme les « je ne trouve pas » en réponses quand
c'est récupérable ; ne coûte que sur les requêtes à faible confiance.

**Coût** : modéré (ne se déclenche que sur score bas). 1 appel LLM de
reformulation dans ce cas.

**Cible RAGAS** : ↑ context recall, ↑ answer relevancy (moins d'abstentions
injustifiées), robustesse.

### 6. Transformation de requête pour l'Agent (décomposition + step-back)

**Quoi** : renforcer le planificateur avec (a) une **décomposition en
sous-requêtes** plus robuste et (b) le **step-back prompting** (généraliser la
question avant de récupérer, pour les questions pointues qui ratent le contexte).

**Pourquoi pour nous** : notre planificateur décompose par thème mais reste
basique ; le multi-saut (« liens entre menaces et exigences ») en profiterait.
Coût LLM **uniquement en mode Agent** (déjà lent, assumé).

**Coût** : appels LLM à la requête, mais bornés au mode Agent.

**Cible RAGAS** : ↑ context recall sur questions complexes, answer completeness.

### 7. RAPTOR récursif complet (améliore aussi la synthèse corpus 1b)

**Quoi** : passer des **résumés plats par section** à un **arbre récursif**
(section → document → corpus). Une question globale tape le bon niveau ; ça
**nourrit directement le pré-filtre et le REDUCE de la synthèse corpus** (1b).

**Pourquoi pour nous** : améliore les documents longs (100-200 pages, notre
cible) et la synthèse corpus. Query-time bon marché (les résumés sont
précalculés).

**Coût** : **ingestion lourde** (appels LLM récursifs de résumé). À réserver si
l'éval montre un manque sur les questions globales/longues.

**Cible RAGAS** : ↑ context recall / answer completeness sur questions globales.

---

## Tier 3 — Conditionnel (selon besoins réels, coût élevé)

- **8. Graphe d'entités/relations (GraphRAG-lite).** `entity_graph` est un
  placeholder vide ; le NER tourne déjà mais ne nourrit que BM25. Construire des
  **relations** aiderait le multi-saut (« liens entre X et Y »). **Mais** :
  ingestion lourde et complexité élevée. À n'engager que si l'éval montre un
  déficit multi-saut que le Tier 2 ne comble pas.
- **9. Gestion des tableaux (.xlsx / tables des cibles de sécurité).** Les
  entrées incluent `.xlsx` (tabulaire) et nos documents ont beaucoup de tables.
  Docling les extrait déjà en markdown ; un **chunking conscient des tables**
  (garder une table entière, l'en-tête avec les lignes) éviterait de couper les
  tableaux. Gain ciblé sur le contenu tabulaire.
- **10. Proposition / semantic chunking affiné.** Peut monter la précision mais
  ingestion coûteuse et risque de fragmenter le contexte. Basse priorité tant que
  le chunking `technical` tient.

## Explicitement écarté (mauvais rapport latence/fit ou incompatible offline)

- **HyDE** — appel LLM **à chaque requête** ; HyPE (Tier 1) donne le même effet
  en précalcul. ❌ latence.
- **CRAG avec recherche web / multimodal Colpali / MemoRAG / Contextual-AI
  agent** — nécessitent internet ou de gros modèles supplémentaires. ❌ offline /
  latence.

## Feuille de route proposée (par vagues, chaque vague mesurée RAGAS)

1. **Vague 0 — Mesure** : harnais RAGAS local + jeu de référence + baseline.
2. **Vague 1 — Précalcul haut rendement** : HyPE (1) + en-têtes contextuels (2)
   + activer parent-child (3). Faible latence, réutilise l'existant. Mesurer.
3. **Vague 2 — Précision/fidélité & robustesse** : compression extractive (4) +
   CRAG offline (5). Mesurer faithfulness & recall.
4. **Vague 3 — Complexe/global** : step-back agent (6) + RAPTOR récursif (7) si
   l'éval le justifie.
5. **Vague 4 — Conditionnel** : graphe d'entités (8), tables (9) selon signaux.

**Règle d'or** : chaque technique n'est **gardée** que si RAGAS montre un gain net
(precision/recall/faithfulness) **sans** dégrader la latence au-delà du seuil
acceptable (rapport latence/précision). Le design fige la mécanique ; l'éval
tranche les valeurs et l'adoption.

## Prochaine étape

Si ce plan te convient, la première brique à construire est la **Vague 0
(harnais RAGAS local)** — je peux la passer en spec → plan → implémentation
(subagent-driven, comme les chantiers 1-2). C'est le prérequis qui rend tout le
reste mesurable.
