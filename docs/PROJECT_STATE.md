# État courant du projet

Dernière consolidation : 2026-08-03.

Ce document est le point de reprise opérationnel. En cas de divergence, vérifier dans cet ordre : code et tests actuels, ce document, `docs/CODEX-ROADMAP.md`, puis les archives `docs/superpowers/`.

## Cap produit

AI for SSH est un système RAG entièrement local, orienté analyses structurées, exhaustives et multi-documentaires. En mode Auto, le système classe la requête, sélectionne une stratégie et ajuste les paramètres. Le mode Expert expose des overrides intentionnels. RAGAS reste la boussole : aucune technique expérimentale ne devient un défaut automatique sans gain mesuré.

Le cas d’usage nord est l’analyse de grands corpus de sécurité : identifier toutes les catégories de sources de risque, sources de risque, catégories d’objectifs, objectifs visés et types d’attaquants, avec tableau, couverture documentaire et citations.

## Architecture effective

```text
Next.js (Chat, Informations, Agents, Réglages)
  -> FastAPI (SSE, sessions, documents, prompts, métriques, système)
    -> contrôleur adaptatif / routeur
      -> RAG standard | synthèse MAP/REDUCE | Agent | Analyse profonde
    -> retrieval hybride (Chroma + BM25 -> RRF -> cross-encoder)
    -> routage LLM par rôle
    -> métriques diffusées à l’UI; couverture conservée côté backend

MongoDB : documents, chunks, sessions, prompts versionnés, runs d’évaluation
ChromaDB : vecteurs
Ollama : embeddings et LLM locaux
```

Rôles LLM : `rewrite`, `planner`, `extract`, `synthesize`, `generate`, `agent`, `judge`, `enhance`. Le modèle choisi dans la barre Chat correspond uniquement à la génération finale. Les autres rôles sont automatiquement routés, ou modifiables dans les réglages experts.

| Rôle | Modèle courant |
|---|---|
| Embedding | `bge-m3:latest` |
| Réécriture | `llama3.1:latest` |
| Planification | `qwen3.5:latest` |
| Extraction structurée | `qwen3.5:latest` |
| Synthèse standard | `mistral-small3.2:latest` |
| Génération finale | `mistral-small3.2:latest` |
| Agent | `gemma4:31b` |
| Juge | `qwen3.5:latest` |
| Analyse profonde | `deepseek-r1:32b` |

Ce routage est pragmatique, pas le résultat d’une matrice exhaustive. La matrice rôle × modèle est reportée à la demande du propriétaire car son coût est trop élevé.

## Fonctionnalités livrées

- Retrieval hybride multi-document, reranking, parent-child et contrôleur adaptatif par type de requête.
- Synthèse corpus MAP/REDUCE avec isolation des erreurs, couverture des documents, citations et extraction structurée.
- Modes Chat `Auto`, `RAG`, `Agent` et `Analyse profonde`. Auto ne déclenche jamais silencieusement le mode profond.
- Routage atomique des modèles par rôle et réglages UI experts.
- Métriques de qualité dans l’UI. Les mesures de couverture restent dans le backend/SSE pour la traçabilité, mais leur rendu Chat a été retiré après la mesure défavorable de la réparation automatique.
- Onglet Agents : inspection et édition versionnée des prompts, restauration et évaluation A/B sur golden set.
- Harnais local RAGAS, ventilation par type/stratégie et sauvegarde des runs dans MongoDB.
- Profil matériel automatique et endpoint `GET /api/perf`.
- Support bi-GPU : Ollama réparti sur deux GPU, concurrence bornée, Chroma préchauffé et singleton cross-encoder protégé.
- Accueil produit : présentation synthétique des espaces Chat et SRA, de la souveraineté locale et accès direct au Chat. Le tutoriel est volontairement reporté tant que les parcours métier évoluent.
- Navigation organisée en espaces : la marque ouvre une page d’accueil avec accès direct au Chat; le Chat reste accessible par un onglet explicite; `Aide`, `Réglages`, `Prompts métier` et `Suivi technique` sont contextuels au Chat; `Méthode SRA` reste le cœur métier et `Documents` le corpus transversal.
- Onglet `Méthode SRA` : verticale métier intégrée au moteur corpus du Chat (balayage MAP exhaustif, routage par rôle, preuves et sources), avec génération multi-document d une table Risk Objectives à deux colonnes — catégorie d attaquant et objectifs visés / justification — éditable et exportable en CSV ou XLSX. Pour l étape RO, le balayage exhaustif produit des candidats, puis une adjudication métier rejette les activités non hostiles, capacités, techniques, vulnérabilités et acteurs non intentionnels, normalise les catégories et atomise les objectifs avant consolidation. Les rejets et les citations exactes des lignes retenues restent auditables dans la trace.

## État matériel et runtime

Machine validée : 2 × RTX 6000 Ada 48 Go. Profil détecté : `dual_high_vram`, environ 98 Go de VRAM cumulée. Recommandations actives : `OLLAMA_SCHED_SPREAD=1`, quatre slots, quatre modèles chargés maximum, `CE_DEVICE=cuda:0`.

État validé après redémarrage le 2026-07-27 :

- UI : `http://localhost:3000`
- API : `http://127.0.0.1:8000`
- Ollama bi-GPU persistant : `http://127.0.0.1:11434`
- `.env` pointe sur le service système `11434`.
- Pilote NVIDIA : `595.84` (module noyau open), deux RTX 6000 Ada détectées.

Le service système Ollama est activé au démarrage. Son override applique `OLLAMA_SCHED_SPREAD=1`, `OLLAMA_NUM_PARALLEL=4`, `OLLAMA_MAX_LOADED_MODELS=4` et `OLLAMA_KEEP_ALIVE=-1`. Un chargement réel de `deepseek-r1:32b` après redémarrage a confirmé l’utilisation des deux GPU. Avec le contexte 131k par défaut, ce runner de 161 Go conserve toutefois une part sur CPU (`41%/59% CPU/GPU`) malgré environ 45–46 Gio occupés sur chaque GPU.

## Validation connue

- Validation de consolidation du 2026-08-03 : 283 tests backend réussis, lint et build Next.js réussis, `git diff --check` propre.
- Dernière génération SRA réelle (`sixième test`, 16 documents) : 602 candidats extraits, 407 occurrences consolidées en 10 RO Types, 89 candidats ambigus conservés pour audit et quatre documents explicitement signalés sans RO suffisamment étayé.
- Le flux opérationnel validé est : corpus documentaire → génération des RO Types → revue/export CSV ou XLSX → génération optionnelle de la table RO × Target Objectives.


- Backend : 265 tests réussis.
- Frontend : lint et build Next.js réussis.
- `git diff --check` propre.
- `/api/perf` retourne `dual_high_vram`.
- `/api/models` expose huit modèles et le routage attendu.
- Les routes `/rag` et `/agents` répondent; `/rag` contient `Analyse profonde`.
- Smoke test Playwright réussi avec Chrome for Testing : Chat, modes Auto/RAG/Agent/Analyse profonde, tooltip du modèle final, Informations, Agents, Paramètres, sources, passages et métriques.
- Benchmark spatial provisoire : 11 réponses validées par le propriétaire le 2026-07-27; extension à 30 questions encore requise avant statut golden officiel.

Derniers runs comparables sur le corpus spatial :

| Mode | Hit | Recall | Precision | Faithfulness | Answer relevancy | Latence |
|---|---:|---:|---:|---:|---:|---:|
| RAG | 0.5364 | 0.3939 | 0.8600 | 0.4697 | 0.5105 | 42.32 s |
| Agent | 0.6955 | 0.4697 | 0.8473 | 0.4848 | 0.5264 | 33.97 s |
| Auto provisoire (11 q) | 0.8500 | 0.3485 | 0.8054 | 0.4545 | 0.4863 | 390.09 s |

Le run Auto `ragas-space-auto-provisional-v1-20260727` est archivé dans MongoDB (`6a672b34ff58c359fb573218`) et dans `evals/last_eval.json`; couverture structurée moyenne : 0.6028. Il établit la référence provisoire sur 11 questions. Analyse profonde est explicitement reportée car trop coûteuse.

## Points d’attention

- Ne jamais activer HyPE sur les résumés RAPTOR par défaut. Mesurer `standard`, `HyPE`, `RAPTOR`, puis `HyPE+RAPTOR`, avec quotas par représentation et collapse vers le parent.
- Préserver les documents utilisateur non suivis sous `docs/PDF/` et `docs/out/`.
- Le dépôt contient de nombreux changements suivis et non suivis; ne pas nettoyer ou réinitialiser sans accord explicite.
- Le mode profond utilise DeepSeek pour la réduction raisonnée, puis décharge le modèle dans un `finally`, y compris après erreur ou annulation.
- Comparer uniquement des runs ayant le même dataset, les mêmes modèles et le même nombre de questions.

## Reprise recommandée

La phase RAG générique est gelée. Le propriétaire a décidé de ne pas poursuivre le branchement des outils corpus dans l’agent ni MMR/diversité : leur gain produit n’est pas démontré face au pipeline MAP/REDUCE actuel.

1. Faire valider métier les 10 RO Types du dernier run réel et examiner les 89 candidats ambigus.
2. Évaluer ensuite la génération RO × Target Objectives sur les documents d architecture dédiés.
3. Conserver le flux simple : documents → RO Types → revue humaine → CSV/XLSX → Target Objectives optionnels.
4. Ne pas réintroduire d ateliers ou de gabarit contractuel sans décision produit.
4. Ne pas réactiver la réparation de couverture, lancer Analyse profonde ou la matrice exhaustive rôle × modèle sans nouvelle décision.


## Évolutions UX et métier — août 2026

- La page d accueil sépare clairement le Chat documentaire et la Méthode SRA ; le tutoriel a été volontairement retiré tant que le produit évolue rapidement.
- Les tables RO et TO disposent d une revue humaine explicite : statut, commentaire, édition et preuves consultables.
- Chaque génération et chaque sauvegarde de revue crée une révision immuable ; le dossier conserve un pointeur vers la version active.
- Les dossiers peuvent être dupliqués ou archivés et leur historique est visible dans l espace SRA.
- Le Chat peut recevoir une ou plusieurs révisions SRA comme contexte traçable, notamment pour interroger ou comparer des outputs.
- Les API exposent la liste, la lecture et la comparaison déterministe des révisions ainsi que les artefacts actifs.
