# Triage — techniques agentiques à adopter pour renforcer l'agent (AI_for_ssh_v3)

Date : 2026-07-22
Sources : [NirDiamant/Agent_Memory_Techniques](https://github.com/NirDiamant/Agent_Memory_Techniques) · [NirDiamant/GenAI_Agents](https://github.com/NirDiamant/GenAI_Agents)
Cible (north-star) : **synthèse multi-documents EXHAUSTIVE et FIABLE** sur 5-10 docs (ex. « crée une table catégorisant tous les types d'attaquant »).
Boussole : **RAGAS** (context recall/precision, faithfulness, answer relevancy, hit@k).
Contrainte dure : **100 % OFFLINE**, petits modèles locaux, GPU limité → coût borné.

Rappel de l'existant pertinent (ne PAS re-recommander tel quel) :
- `core/planner.py` : planner-exécuteur (2-5 sous-questions → `rag_search` par étape → 1 synthèse), re-planification bornée, fallback ReAct (`core/agent.py`).
- `core/synthesize_corpus.py` : map-reduce DÉTERMINISTE (préfiltre → extract/doc → reduce) avec **dédup** et **attribution `[src: ...]` verbatim**.
- `core/self_rag.py` : **réflexion déjà présente** (LLM-as-judge : context_relevance / faithfulness / answer_relevance + retry par reformulation) — mais OFF par défaut, branchée sur le RAG simple, pas sur la synthèse.
- `core/router.py` : routage heuristique regex (RAG / Agent / verif), zéro appel LLM.
- `tools/corpus_tools.py` : `list_sources` + `extract_from_document` — **pas encore câblés** dans l'agent.
- **Aucune mémoire persistante** (pas de mémoire épisodique/sémantique/cross-session).

---

## 1. Catalogue compact des deux repos

### A. Agent_Memory_Techniques (catégorie : mémoire, sauf indication)

| # | Technique | 1 ligne |
|---|-----------|---------|
| 1 | Conversation Buffer | Historique verbatim complet en contexte. |
| 2 | Sliding Window | Ne garde que les N derniers messages. |
| 3 | Summary Memory | Résume les vieux tours, jette l'original. |
| 4 | Summary Buffer | Résumé du vieux + récent intact. |
| 5 | Token Buffer | Budget tokens strict, purge le plus ancien. |
| 6 | Vector Store Memory | Interactions passées en embeddings, rappel par similarité. |
| 7 | Entity Memory | Faits structurés sur entités (personnes, projets, préférences). |
| 8 | Knowledge Graph Memory | Connaissances en nœuds/relations. |
| 9 | Episodic Memory | Interactions complètes horodatées + contexte. |
| 10 | Semantic Memory | Faits généralisables extraits des épisodes. |
| 11 | Procedural Memory | Workflows/recettes réutilisables (« comment faire »). |
| 12 | Working Memory | Gestion dynamique de l'attention active. |
| 13 | Hierarchical Memory Layers | Stockage hot/warm/cold, promotion/démotion temporelle. |
| 14 | Memory Consolidation | Fusion + dédup + renforcement des mémoires. |
| 15 | Memory Compaction | Réduction (résumé/distillation/extraction d'entités). |
| 16 | Self-Reflection Memory (réflexion) | L'agent documente ses leçons et les réapplique. |
| 17 | Memory Routing | Aiguille lecture/écriture vers le bon store selon le contenu. |
| 18 | Temporal Memory | Horodatage + pondération de récence au ranking. |
| 19 | Forgetting & Decay | Élague le vieux/peu pertinent. |
| 20 | Memory Retrieval Patterns (retrieval-augmentation) | Compare semantic / recency / hybride / **diversité (MMR)**. |
| 21 | Cross-Session Memory | Restaure l'état pour reprendre une conversation. |
| 22 | Multi-Agent Shared Memory (multi-agent) | Store partagé + message-passing + consensus. |
| 23 | Memory with Tools (tool-use) | save/search/forget exposés comme OUTILS appelables. |
| 24 | Graphiti (Zep) | KG temporel construit depuis le dialogue. |
| 25 | Mem0 | Couche mémoire managée (extraction/stockage/retrieval par user). |
| 26 | Letta (MemGPT) | Mémoire auto-éditée, heartbeat de pression mémoire. |
| 27 | Zep | Classification dialogue + entités + KG temporel à l'échelle. |
| 28 | Memory Evaluation | Précision/rappel du retrieval, staleness, contradictions. |
| 29 | Benchmarks (LoCoMo / LongMemEval) | Bench conversations longues. |
| 30 | Production Patterns | Cache, TTL, sharding, backups, GDPR, observabilité. |

### B. GenAI_Agents (catégorie indiquée)

| Technique | Catégorie | 1 ligne |
|-----------|-----------|---------|
| **Controllable RAG Agent** | planning + retrieval | QA complexe par **graphe déterministe** de plan avec étapes vérifiées. |
| ATLAS / Project Manager / Taskifier / Chiron | planning | Décomposition de tâche, dépendances, checkpoints. |
| Self-Improving Agent | réflexion | Apprend des interactions, raffinement continu. |
| Self-Healing Codebase | réflexion | Détection d'erreur + génération de correctif en boucle. |
| LangGraph Systems Inspector | multi-agent + réflexion | Testeurs spécialisés détectant vulnérabilités. |
| Research Team (AutoGen) / Blog Writer (Swarm) / ClauseAI / Grocery (CrewAI) / Contextual Quoting | multi-agent | Rôles répartis (retriever / raisonnement / classif / rédaction / édition). |
| Memory-Enhanced Conversational Agent | mémoire | Mémoire court + long terme. |
| Memory-Enhanced Email Agent (LangMem) | mémoire | Mémoire sémantique/épisodique/procédurale pour tri. |
| EU Green Deal FAQ Bot | retrieval + réflexion | FAISS + chunking sémantique + **agent évaluateur de réponse**. |
| Systematic Review Automation | retrieval + réflexion | Multi-papiers avec **cycles de révision**. |
| Gutenberg Sage | retrieval | **RAG local (Ollama)** enrichi NER. |
| Art Tourguide (LightRAG) / DataScribe | retrieval | RAG knowledge-graph / exploration de schéma. |
| Controllable / Contextual Quoting | tool-use | Génération de citations ancrées. |
| Journalism Assistant | retrieval | Fact-checking + détection de biais par retrieval. |
| MCP tutorial / LangGraph intro | tool-use / framework | Intégration ressources externes / workflows à états. |
| ShopGenie, Car Buyer, News TL;DR, AInsight, Podcast, GIF/DALL-E, TTS, Music, Weather, Scientific Paper (CORE API), Internet Search | tool-use (WEB/CLOUD) | Dépendent d'API web/cloud/génération média. |

---

## 2. TRIAGE pour NOTRE agent

### TIER HIGH — fort levier sur exhaustivité + fiabilité

**H1. Câbler `list_sources` + `extract_from_document` comme OUTILS de l'agent** (≈ Memory with Tools #23 / Controllable RAG tool-use)
- Quoi : exposer nos deux tools corpus au planner/ReAct pour qu'il puisse énumérer les docs et forcer une extraction par-document au lieu de ne dépendre que du top-k de `rag_search`.
- Pourquoi NOUS : notre north-star EXIGE de couvrir *tous* les docs ; `rag_search` seul rate les docs hors top-k → trou d'exhaustivité. Ces outils existent déjà, non branchés.
- Offline : **oui**. Coût : moyen (1 appel/doc, borné par le nombre de docs, déjà concurrent dans le map-reduce).
- RAGAS : **context recall**, **hit@k**.

**H2. Boucle de couverture / réflexion d'exhaustivité** (≈ Self-Reflection #16 + Systematic Review revision cycles + EU FAQ evaluator)
- Quoi : après la synthèse, un pass LLM-as-judge dédié « manque-t-il un doc / une catégorie ? » qui, si oui, relance extraction ciblée sur les docs sous-représentés. Étendre `self_rag.py` (aujourd'hui centré RAG simple) à la synthèse corpus + ajouter le critère *coverage*.
- Pourquoi NOUS : notre réflexion existante ne juge pas l'EXHAUSTIVITÉ (elle juge relevance/faithfulness/answer). C'est précisément la métrique manquante pour le north-star.
- Offline : **oui**. Coût : +1 à +2 passes LLM (borné : 1 retry max, comme SELF_RAG_MAX_RETRIES).
- RAGAS : **context recall** (surtout), **answer relevancy**.

**H3. Retrieval par DIVERSITÉ (MMR) + hybride** (≈ Memory Retrieval Patterns #20)
- Quoi : dans chaque sous-question du planner et dans le préfiltre du map-reduce, remplacer le top-k pur similarité par MMR (pertinence − redondance) pour maximiser la couverture inter-documents.
- Pourquoi NOUS : le top-k similarité empile des chunks quasi-dupliqués du même doc → écrase les docs minoritaires, tue l'exhaustivité. MMR force la représentation de plus de sources.
- Offline : **oui** (calcul vectoriel local). Coût : **faible** (post-traitement des embeddings déjà calculés).
- RAGAS : **context recall**, **context precision**, **hit@k**.

**H4. Consolidation / dédup sémantique renforcée au reduce** (≈ Memory Consolidation #14)
- Quoi : au reduce, dédupliquer les éléments équivalents par similarité d'embedding (pas seulement par le LLM) avant fusion, en préservant l'union des `[src: ...]`.
- Pourquoi NOUS : `synthesize_corpus.py` déduplique déjà mais côté LLM ; une dédup vectorielle en amont réduit les doublons ratés et les catégories dupliquées → table finale plus propre et plus fidèle.
- Offline : **oui**. Coût : **faible**.
- RAGAS : **faithfulness**, **context precision**.

### TIER MEDIUM — utile, à mesurer, gain plus indirect

**M1. Entity / Knowledge-Graph Memory sur le corpus** (#7/#8, cf. Gutenberg Sage NER, LightRAG)
- Quoi : extraire une fois les entités clés (ex. « types d'attaquant ») par doc → index d'entités servant de checklist d'exhaustivité au reduce.
- Pourquoi NOUS : transforme « exhaustif » en vérifiable (chaque entité connue doit apparaître dans la table). Fort pour le north-star MAIS construction coûteuse.
- Offline : **oui** (NER/LLM local). Coût : **moyen-élevé** (pré-passe d'indexation par doc ; amortie si mise en cache).
- RAGAS : **context recall**.

**M2. Agent « auditeur de couverture » séparé** (≈ Multi-Agent Shared Memory #22 / Contextual Quoting retriever+classif)
- Quoi : un second rôle léger qui reçoit la liste des sources + la synthèse et signale les sources non citées. Variante mono-modèle de H2 si on veut un rôle explicite plutôt qu'un pass.
- Pourquoi NOUS : filet de sécurité d'exhaustivité. Redondant avec H2 → n'adopter qu'un des deux.
- Offline : **oui**. Coût : moyen (+1 appel). RAGAS : **context recall**.

**M3. Procedural Memory — recette de synthèse mise en cache** (#11)
- Quoi : mémoriser le plan/paramètres qui ont donné une bonne synthèse pour un type de requête d'agrégation, et le rejouer.
- Pourquoi NOUS : stabilité/latence sur requêtes récurrentes (« catégorise tous les X »), pas un gain de précision direct.
- Offline : **oui**. Coût : **faible**. RAGAS : indirect (**answer relevancy** via cohérence).

**M4. Summary / Hierarchical memory pour docs longs** (#3/#4/#13)
- Quoi : résumé/compaction hiérarchique par doc pour tenir des docs de centaines de pages en contexte borné.
- Pourquoi NOUS : pas pour le corpus 5-10 docs actuel, mais **prérequis du chantier RAG-scaling** (docs longs). À garder en réserve.
- Offline : **oui**. Coût : moyen. RAGAS : **context recall** (sur docs longs).

**M5. Cross-Session Memory (cache de synthèses validées)** (#21)
- Quoi : persister les syntheses validées + leur contexte pour reprise/reproductibilité.
- Pourquoi NOUS : cohérence entre sessions et éval reproductible ; gain accuracy faible.
- Offline : **oui** (Mongo déjà là). Coût : faible. RAGAS : indirect.

### TIER LOW — faible fit / redondant

- **Conversation/Sliding/Token Buffer (#1/#2/#5)** : gestion d'historique conversationnel — notre north-star est one-shot analytique, peu de valeur. Offline oui, coût nul, RAGAS ≈ nul.
- **Temporal Memory / Forgetting & Decay (#18/#19)** : la récence n'a pas de sens sur un corpus documentaire figé ; oublier nuirait à l'exhaustivité.
- **Working Memory dynamique (#12)** : déjà couvert par l'accumulation de passages du planner.
- **Memory Routing (#17)** : notre `router.py` heuristique suffit ; un routeur mémoire ajouterait de la complexité sans gain accuracy.
- **Self-Improving / Self-Healing code (GenAI)** : orienté génération de code, hors périmètre RAG.

---

## 3. EXPLICITEMENT EXCLU

| Technique | Raison |
|-----------|--------|
| ShopGenie (Tavily), Car Buyer (Playwright), News TL;DR, AInsight, Internet Search, Scientific Paper (CORE API), Journalism (fact-check web) | **Web/API cloud requis** → viole 100 % offline. |
| Podcast Generator, GIF/DALL-E, TTS Poem, Music Compositor | **Génération média + API cloud** → hors périmètre + offline KO. |
| **Mem0 (#25), Zep (#27), Graphiti (#24), Letta/MemGPT (#26)** | Plateformes mémoire managées / infra lourde ; auto-hébergement possible pour certaines mais **infra + modèles supplémentaires + latence** sur petit GPU → mauvais ratio. Réimplémenter les *patterns* (Entity/KG = M1) plutôt qu'importer la plateforme. |
| Frameworks multi-agents complets (**AutoGen / CrewAI / Swarm / LangGraph runtime**) | Orchestration lourde + latence + dépendances ; on garde les *patterns* (rôles, cycles de révision) sans le runtime. |
| Benchmarks LoCoMo / LongMemEval (#29), Memory Evaluation (#28) | Éval de mémoire conversationnelle ; **notre boussole est RAGAS**, pas ces bench. |
| Production Patterns : TTL, sharding, GDPR, decay (#30/#19) | Ops/conformité, sans effet sur accuracy/exhaustivité du north-star. |
| MCP tutorial | Intègre ressources externes → contraire à l'offline strict (sauf outil local, déjà couvert par nos tools). |

---

## 4. Roadmap de renforcement recommandée (ordonnée)

> Règle : **chaque étape mesurée par RAGAS AVANT/APRÈS** (context recall/precision, faithfulness, answer relevancy, hit@k) sur un jeu de requêtes d'agrégation multi-docs. Aucune adoption sans delta mesuré.

1. **H3 — Retrieval MMR/diversité** (coût faible, gain recall/precision immédiat, base des étapes suivantes). Cible : context recall, hit@k.
2. **H1 — Câbler `list_sources` + `extract_from_document`** dans l'agent (outils déjà écrits ; garantit la couverture par-document). Cible : context recall, hit@k.
3. **H4 — Dédup sémantique au reduce** (nettoie la table, faible coût). Cible : faithfulness, context precision.
4. **H2 — Boucle de réflexion d'EXHAUSTIVITÉ** (étendre `self_rag.py` au map-reduce + critère coverage ; retry borné). Cible : context recall, answer relevancy.
5. **M1 — Index d'entités comme checklist d'exhaustivité** (si H2 laisse des trous ; pré-passe cachée). Cible : context recall.
6. **Réserve scaling : M4 (summary/hiérarchique)** — à activer pour le chantier docs longs, pas avant.

Notes de garde-fous :
- H2 et M2 sont redondants → n'en implémenter qu'UN (préférer H2, mono-modèle, moins de latence).
- Tout ajout LLM-as-judge doit rester **borné** (1 retry max, à l'image de `SELF_RAG_MAX_RETRIES`) pour le petit GPU.
- Ne pas régresser l'attribution `[src: ...]` verbatim ni le fallback ReAct existants.
