# Instructions du dépôt — AI_for_ssh_v3

## Sources de vérité

- `docs/PROJECT_STATE.md` est l’état canonique de reprise et décrit l’architecture effectivement déployée.
- `docs/CODEX-ROADMAP.md` est la feuille de route active.
- `docs/superpowers/` et `.superpowers/sdd/` sont des archives de conception et d’exécution provenant du workflow Claude/Superpowers. Ne pas les traiter comme une liste de tâches actuelle sans vérifier le code, les tests et Git.
- `env_config.py` et `core/model_router.py` définissent la configuration et le routage LLM effectifs.

## Règles de travail

1. Préserver les fichiers utilisateur non suivis sous `docs/PDF/` et `docs/out/`.
2. Avant une tâche, confirmer son état dans le code et dans `git log`; plusieurs plans archivés sont déjà implémentés.
3. Pour toute technique RAG, mesurer avant/après avec le harnais local RAGAS. Ne conserver une technique que si le gain net justifie son coût et sa latence.
4. Garder le système 100 % local/offline : Ollama, MongoDB, ChromaDB et modèles locaux.
5. Utiliser les rôles de `core/model_router.py`; ne pas instancier un modèle Ollama en dur dans une feature.
6. Toute nouvelle tâche LLM doit avoir un rôle explicite ou justifier la réutilisation d’un rôle existant.
7. Tests ciblés d’abord, puis suite pertinente; pour le front, exécuter `npm run lint` et `npm run build`.
8. Les changements de modèle depuis l’UI sont persistés dans `.env` et prennent effet après redémarrage, sauf l’override de génération explicitement à chaud.
9. Ne jamais activer automatiquement HyPE sur les nœuds ou résumés RAPTOR. Leur combinaison doit rester derrière des flags indépendants et faire l’objet d’une expérience factorielle.
10. Tout index multi-représentation doit conserver un type explicite (`raw`, `hype_question`, `raptor_summary`) et appliquer des quotas/une diversification afin qu’un type synthétique ne monopolise pas le top-k.
11. La matrice exhaustive rôle × modèle est reportée. Ne pas la relancer sans demande explicite du propriétaire.

## Routage LLM

- `rewrite` → `REWRITER_MODEL`
- `agent` → `AGENT_MODEL`
- `planner` → `PLANNER_MODEL`
- `extract` → `EXTRACTION_MODEL`
- `synthesize` → `SYNTHESIS_MODEL`
- `generate` → `GEN_MODEL`
- `judge` → `JUDGE_MODEL`
- `enhance` → `ENHANCEMENT_MODEL` ou repli `REWRITER_MODEL`
- embeddings → `EMBED_MODEL`

## Critère de fin

Une tâche est terminée lorsque le code, les tests, la documentation de configuration et la validation proportionnée au risque sont cohérents. Les mesures longues ou dépendantes du GPU doivent produire un résultat enregistré, comparable au baseline et accompagné d’une décision explicite adopter/rejeter.
