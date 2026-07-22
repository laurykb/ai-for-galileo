# Vague 1 — Précalcul haut rendement (HyPE + en-têtes contextuels + parent-child)

Date : 2026-07-22
Statut : design (autonome — à relire). **À exécuter APRÈS la baseline RAGAS (Vague 0)**.
Boussole : chaque technique est **mesurée avant/après** avec `--mode ragas`
(faithfulness, context recall/precision, answer relevancy, hit@k). Adoptée
seulement si gain net sans coût de latence de requête inacceptable.

## Contexte

Le plan d'amélioration classe en Vague 1 les trois techniques au meilleur rapport
gain/latence, car elles **précalculent à l'ingestion** (coût payé une fois) et
réutilisent des briques existantes. Contrainte 100 % offline.

## Techniques

### 1. HyPE — embarquer les questions hypothétiques

**État actuel** : à l'ingestion on génère `AUTO_QUESTIONS` questions par chunk
(`nlp/chunk_enhancer.py`), stockées en métadonnée `questions_str` et envoyées
**uniquement à BM25** (`indexing/keyword_index._build_enriched_text` les répète
dans le texte enrichi). Le sémantique ne les voit pas.

**Changement** : à l'ingestion, **embarquer** chaque question hypothétique et
l'indexer dans Chroma comme un vecteur supplémentaire **pointant vers le chunk
parent** (même `id` de chunk en métadonnée, un `chunk_type="hype_question"` ou un
id dérivé). À la requête, la recherche sémantique matche la question utilisateur
contre ces embeddings-questions ; on **remonte le chunk parent** (dédup par id).
**Aucun appel LLM à la requête** — tout est précalculé.

**Points de design** :
- Réutiliser `build_embeddings` (`indexing/embedding.py`) : ajouter les questions
  comme documents à embarquer, avec métadonnée liant au chunk parent.
- À la récupération (`retrieval/semantic_search` / `vector_store`), quand un hit
  est un `hype_question`, **résoudre vers le chunk parent** avant fusion/rerank
  (dédup : un chunk atteint via plusieurs questions ne compte qu'une fois, garder
  le meilleur score).
- Flag `HYPE_ENABLED` (`.env`, défaut à décider par l'éval) pour activer/mesurer.
- Coût ingestion : +N embeddings/chunk (N=`AUTO_QUESTIONS`) et +espace Chroma.
  Requête : inchangée.

**Cible RAGAS** : ↑ context recall, ↑ hit@k (surtout questions paraphrasées /
acronymes vs vocabulaire du document — notre point faible connu « IDS » ≠
« détecteur d'intrusion »).

### 2. En-têtes contextuels de chunk (embarqués)

**État actuel** : chunks avec métadonnées `heading`/`breadcrumb` (fil d'Ariane),
mais le **texte embarqué** est le seul contenu du chunk — le contexte
hiérarchique n'est pas dans le vecteur.

**Changement** : à l'ingestion, **préfixer** le texte de chaque chunk de son fil
d'Ariane (titre document + hiérarchie de section) **avant embedding** (et
idéalement avant enrichissement BM25 aussi). Un chunk « 6.2.1 : AES-256… » gagne
« Cible de sécurité X > Fonctions cryptographiques > 6.2.1 … ».

**Points de design** :
- Point d'application : `core/ingest.py` / `indexing/embedding.py` — construire le
  texte à embarquer = `f"{breadcrumb}\n\n{page_content}"` quand `breadcrumb` existe.
- Ne PAS modifier le `doc`/contenu affiché à l'utilisateur (le préfixe sert à
  l'embedding, pas au contexte de génération — ou l'inclure aussi, à mesurer).
- Flag `CONTEXT_HEADERS_ENABLED` pour activer/mesurer.
- Coût : ingestion négligeable ; requête inchangée.

**Cible RAGAS** : ↑ context precision (moins de chunks hors-contexte qui matchent
sur des termes génériques).

### 3. Activer + calibrer Parent-Child

**État actuel** : `retrieval/parent_child.py` existe (`expand_to_parent`,
reconstruit le contexte de section autour d'un chunk enfant matché) mais est
**désactivé** (`PARENT_CHILD_ENABLED=false`).

**Changement** : **activer** (le passer à `true` après mesure), en calibrant
`PARENT_CHILD_MAX_CHARS` et le top-k pour ne pas exploser le contexte de
génération. Le générateur voit un **segment parent cohérent** plutôt que des
fragments.

**Points de design** :
- Déjà branché dans `hybrid_retrieve` (paramètre `parent_child_on`). Il s'agit
  surtout d'activer par défaut + calibrer, en mesurant l'impact latence (contexte
  plus long → génération un peu plus lente) vs le gain faithfulness/completude.
- Interaction avec le plancher de couverture (1a) et `NUM_CHUNKS_PARENT_CHILD`
  (réduit le top-k quand actif) : vérifier la cohérence.

**Cible RAGAS** : ↑ context recall + faithfulness (contexte cohérent → moins
d'hallucination sur fragments tronqués).

## Protocole de mesure (obligatoire — boussole)

Pour CHAQUE technique, indépendamment :
1. Baseline RAGAS déjà archivée (Vague 0).
2. Activer la technique (flag), **ré-ingérer** le corpus si la technique touche
   l'ingestion (HyPE, en-têtes), relancer `--mode ragas`.
3. Comparer les 4 métriques + hit@k + latence vs baseline.
4. **Garder** seulement si gain net (surtout context recall/precision,
   faithfulness) sans dégradation de latence inacceptable. Sinon, laisser le flag
   à `false` et documenter le résultat.
5. Une fois les trois évaluées, choisir la **combinaison** qui maximise la qualité
   (ré-éval de la combinaison, pas seulement des techniques isolées).

## Vérification

- Tests unitaires offline (résolution HyPE→parent avec dédup ; préfixe d'en-tête
  construit correctement ; parent-child actif respecte le plafond).
- Ré-ingestion du corpus + run `--mode ragas` avant/après chaque flag.
- Suite `pytest -q` verte ; modes d'éval intacts.
- Le use case nord (« crée une table catégorisant tous les types d'attaquant » sur
  5-10 docs) sert de sonde qualitative : la couverture/exhaustivité doit monter.

## Risques

- HyPE gonfle l'index Chroma (×`AUTO_QUESTIONS`) — surveiller la mémoire/latence
  de recherche à l'échelle 50 docs ; borner si besoin (moins de questions/chunk).
- La résolution HyPE→parent doit dédupliquer proprement (un chunk atteint par
  plusieurs questions = un seul chunk, meilleur score) sinon on fausse le top-k.
- En-têtes : ne pas polluer l'embedding si les breadcrumbs sont trop verbeux
  (tronquer).
- Parent-child : surveiller la latence de génération (contexte plus long).
- **Rien n'est adopté sans preuve RAGAS** — c'est la règle.
