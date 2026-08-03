# Roadmap active Codex

Dernière consolidation : 2026-08-03.

Ce document adapte les plans historiques de `docs/superpowers/` à un workflow agentique indépendant de Claude. Les anciens briefs et rapports restent des preuves et archives; cette roadmap est le point d’entrée opérationnel.

État détaillé et consignes de reprise : `docs/PROJECT_STATE.md`.

## État consolidé

### Terminé — ne pas réimplémenter

- Chantier 1a : qualité du retrieval multi-document.
- Chantier 1b : synthèse corpus map-reduce, streaming et isolation des erreurs.
- Chantier 2 : périmètre non collant, routeur, UI Informations et agent.
- Vague 0 : harnais RAGAS local et baseline 30 questions.
- Vague 1, code : HyPE, en-têtes contextuels et correction parent-child.
- Routage LLM atomique : extraction et synthèse séparées de la rédaction finale; sélection de tous les rôles dans l’UI.

Baseline historique Vague 0 : recall 0.529, precision 0.809, faithfulness 0.495, answer relevancy 0.599, keyword hit rate 0.656, environ 40 s/question. Elle sert de repère historique; chaque expérimentation doit aussi établir un baseline contemporain avec le même code et les mêmes modèles.

## Phase historique — validation Vague 1

### Cap directeur — optimisation adaptative par requête

La finalité n’est pas une configuration RAG universelle. Pour chaque requête, le système sélectionne automatiquement le mode, les techniques, les paramètres et les rôles LLM qui maximisent la qualité sous un budget de latence explicite.

- Hors ligne : mesurer chaque technique par catégorie et convertir les résultats en règles de politique versionnées.
- En ligne : classifier la requête, appliquer la meilleure stratégie connue, exposer la décision, mesurer la réponse et autoriser une réparation bornée.
- Mode Expert : permettre uniquement des overrides intentionnels à trois états (`Auto`, `Forcé activé`, `Forcé désactivé`).

Taxonomie initiale : question pointue, exploratoire, agrégation exhaustive, comparaison/multi-hop et multi-document. Un mode RAG ou Agent forcé reste une contrainte de haut niveau ; ses paramètres internes restent automatiques sauf override Expert explicite.

Une technique expérimentale ne peut être activée automatiquement qu’après un gain mesuré pour la catégorie concernée. Chaque décision expose sa version, son profil, ses leviers, leur origine (`automatic` ou `expert_override`) et sa justification.


Objectif : décider HyPE, headers et parent-child sur 30 questions, jamais sur les seules mesures indicatives à 8 questions.

1. Redémarrer l’API et reconstruire un index flags-off.
2. Mesurer un baseline contemporain 30 questions.
3. Mesurer séparément HyPE, HyPE+headers, HyPE+parent-child, puis le meilleur combo.
4. Enregistrer pour chaque run : commit, rôles/modèles, flags, taille index, métriques RAGAS et latence.
5. Décision explicite par technique : adopter ou rejeter. Restaurer ensuite l’index dans l’état retenu.

Important : fixer les modèles durant toute la matrice. Proposition initiale : `JUDGE_MODEL` stable pour toutes les évaluations; `GEN_MODEL` stable pour éviter de confondre amélioration retrieval et changement de rédacteur.

## Garde-fou transversal — interaction HyPE × RAPTOR

Le gain de HyPE et RAPTOR n’est pas supposé additif. Leur combinaison peut saturer localement l’espace vectoriel : plusieurs résumés abstraits, chacun décliné en questions hypothétiques proches, peuvent évincer les chunks bruts exacts du top-k. Le symptôme attendu est un recall stable ou supérieur, mais une chute de context precision et de faithfulness, avec un coût d’ingestion fortement accru.

Contraintes de conception :

- HyPE reste désactivé par défaut sur les vecteurs `raptor_summary`; il cible d’abord les chunks bruts.
- Chaque unité indexée porte `representation_type=raw|hype_question|raptor_summary` et un identifiant de parent canonique.
- Après recherche, collapse vers le parent puis déduplication avant rerank.
- Le top-k final réserve une place minimale aux chunks bruts et plafonne les représentations synthétiques par parent et par type.
- Préférer une fusion par branches ou un reranking diversifié à un unique k-NN sur un index sans quotas.
- Surveiller le ratio vecteurs/chunks, les doublons de voisinage, la part du top-k par type et le taux de parents uniques.

Protocole obligatoire avant une combinaison : matrice factorielle `standard`, `HyPE seul`, `RAPTOR seul`, `HyPE+RAPTOR`, avec mêmes modèles, dataset et budget top-k. Décision fondée sur context recall, context precision, faithfulness, answer relevancy, hit@k, latence requête, temps d’ingestion, taille d’index et coût mémoire. Le combo est rejeté si son gain net ne dépasse pas clairement la meilleure technique seule.

## Vague 2 — précision, fidélité et robustesse

Traiter une technique à la fois, avec test offline puis mesure RAGAS :

1. Compression contextuelle extractive, sans appel LLM supplémentaire.
2. CRAG local borné : réécriture et retrieval élargi lorsque le score CE est faible.
3. Step-back prompting pour les questions agentiques complexes.
4. RAPTOR récursif, avec `ENHANCEMENT_MODEL` pour le précalcul et `SYNTHESIS_MODEL` pour les réductions globales. Son expérimentation commence sans HyPE sur les nœuds RAPTOR; le combo constitue une expérience séparée.

Chaque technique exige une spec courte : problème mesuré, hypothèse, rôle LLM touché, garde-fous, métriques cibles, budget de latence et stratégie de rollback.

## Vague 3 — exhaustivité agentique multi-document

Ordre recommandé :

1. Boucle de contrôle de couverture sur la synthèse, via `JUDGE_MODEL`, avec un seul retry maximum.
2. Brancher `list_sources` et `extract_from_document` dans l’agent/planner. L’extraction doit employer `EXTRACTION_MODEL`.
3. Retrieval MMR/diversité pour réduire la redondance inter-documents.
4. Déduplication sémantique déterministe avant REDUCE; la fusion finale reste sur `SYNTHESIS_MODEL`.
5. Index d’entités servant de checklist de couverture.

Ne pas introduire AutoGen, CrewAI, Swarm ou LangGraph runtime sans nouvelle décision d’architecture. Le système conserve les patterns multi-agents mais une orchestration locale légère et explicite.

## Travaux indépendants

- Onglet **Agents** : livré. Il affiche les prompts effectifs, permet leur édition versionnée, leur restauration et l’évaluation A/B sur golden set.

- Chantier 3 : export DOCX/PDF à la demande.
- Optimisation deux GPU du runner d’évaluation, après vérification de la thread-safety Chroma/cross-encoder.
- Attribution déterministe post-hoc des lignes de synthèse pour dépasser la précision historique approximative des citations REDUCE.

## Contrat d’expérimentation

Chaque expérimentation crée un enregistrement lisible contenant : identifiant du run, commit, dataset, variables `.env` pertinentes, table de routage LLM, métriques avant/après, latence, consommation approximative, conclusion et rollback. Une amélioration non mesurée reste expérimentale et désactivée par défaut.

## Cartographie des archives

- Reprise historique : `docs/superpowers/RESUME-next-session.md`
- Plans exécutés : `docs/superpowers/plans/`
- Spécifications : `docs/superpowers/specs/`
- Recherche Vagues 2-3 : `docs/superpowers/research/`
- Rapports détaillés : `.superpowers/sdd/`

En cas de divergence, vérifier dans cet ordre : code actuel, tests, Git, roadmap active, puis archives Superpowers.


## Jalon matériel et Analyse profonde (2026-07-24)

- Profil validé : 2 x RTX 6000 Ada 48 Go, Ollama `SCHED_SPREAD=1`, quatre slots, cross-encoder sur `cuda:0`.
- Le runner préchauffe Chroma avant toute concurrence inter-questions et protège le singleton cross-encoder.
- Le chat expose `Analyse profonde` : MAP multi-document qwen3.5, REDUCE raisonné `DEEP_RESEARCH_MODEL`, budget 4096 tokens, puis déchargement garanti du modèle profond. Auto ne sélectionne jamais silencieusement ce profil coûteux.
- Le routage courant reste un candidat pragmatique. La matrice exhaustive rôle x modèle est explicitement reportée à un chantier ultérieur, à la demande du propriétaire.
- RAGAS Auto provisoire 11 questions est terminé et archivé ; Analyse profonde reste reportée. Les runs RAG et Agent comparables sont également archivés dans Mongo.

## Prochaine session — ordre recommandé

1. Conserver `COVERAGE_REPAIR_ENABLED=false`; réparation rejetée comme défaut et rendu retiré de l’interface Chat.
2. Clore la phase RAG générique. Le branchement des outils corpus dans l’agent et MMR/diversité sortent du jalon faute de valeur produit démontrée.
3. Faire valider métier les 10 RO Types consolidés sur le corpus réel et examiner les 89 candidats ambigus.
4. Évaluer la seconde étape RO × Target Objectives sur les documents d architecture dédiés; ne pas réintroduire les anciens ateliers EBIOS.

Analyse profonde et matrice exhaustive rôle × modèle sont hors du prochain jalon.

La persistance du serveur Ollama bi-GPU est validée après redémarrage le 2026-07-27 : service système `11434`, `OLLAMA_SCHED_SPREAD=1`, quatre slots, pilote NVIDIA 595.84 et utilisation effective des deux RTX 6000 Ada.

## Décision contrôle de couverture (2026-07-27)

Comparatif sur 6 requêtes structurées, mêmes modèles et dataset : réparation activée versus désactivée. Désactivée : faithfulness 0.3056 contre 0.2500, answer relevancy 0.4350 contre 0.4127, couverture structurée 0.7000 contre 0.6028 et latence 563.53 s contre 677.96 s. Retrieval identique. Décision : rejeter la réparation comme défaut et conserver le mécanisme expérimental derrière `COVERAGE_REPAIR_ENABLED=false`. Runs MongoDB : activé `6a672b34ff58c359fb573218` (sous-ensemble structuré), désactivé `6a6752c7d5196a551755b4d6`.

## Décision SRA (2026-07-28)

L ancienne méthodologie, ses ateliers et son classeur contractuel sont abandonnés. Le produit suit désormais la méthode SRA. Sa première étape produit une table Risk Objectives à deux colonnes : catégorie d attaquant et objectifs visés / justification. La table est générée depuis les documents disponibles, revue dans l interface puis exportée en CSV ou XLSX.
