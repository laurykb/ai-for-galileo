# Chantier 2 — Périmètre, Agent, Routeur & onglet Informations

Date : 2026-07-22
Statut : design (décisions prises en autonomie — à relire)
Périmètre : front Next.js (`web/src`) + routeur (`core/router.py`) + doc UI

## Contexte

Après les Chantiers 1a (qualité retrieval) et 1b (synthèse corpus), il reste les
frictions d'usage quotidien que l'utilisateur a signalées :

1. **Le périmètre reste « collé » au dernier document.** Après un upload, le
   sélecteur bascule automatiquement sur le document ingéré
   (`web/src/components/chat/index.tsx:402`) et y reste ; l'utilisateur croit
   interroger « Tous les documents » alors que le front envoie encore l'ancien
   document comme `source`. Le backend honore correctement ce `source` — donc le
   RAG **et** l'Agent paraissent bloqués sur le dernier document. C'est un bug
   d'**état UI**, pas de retrieval.
2. **Le routeur `auto` ne part quasiment jamais en Agent.** La regex
   `_AGENT_SIGNALS.multi_documents` (`core/router.py:47`) est trop étroite
   (`les deux`, `dans tous les`, `chaque document`…) : les questions naturelles
   de comparaison/multi-saut ne matchent pas → `auto` reste en RAG (constat
   utilisateur confirmé). L'Agent, pourtant utile, est sous-déclenché.
3. **Pas d'onglet Informations.** L'UI (Chat / Documents / [Observabilité] /
   Paramètres) n'explique nulle part les compétences en jeu (RAG hybride,
   reranker, Self-RAG, attribution, agent planificateur, et le nouveau mode
   **Synthèse corpus** livré en 1b). Principe projet : tout nouveau skill doit
   être visible et expliqué dans un onglet Informations.

Hors périmètre : la synthèse corpus (1b, livrée) ; la génération PDF/DOCX
(Chantier 3).

## Décisions de design (autonomes)

### A. Périmètre documentaire non-collant

- **Ne plus auto-restreindre après upload.** Supprimer l'auto-sélection
  (`index.tsx:402`) : après ingestion, le périmètre **reste « Tous les
  documents »**. Un retour visuel clair confirme l'ingestion (le doc apparaît
  dans la liste + un état « ingéré »), sans changer le périmètre à la place de
  l'utilisateur.
- **« Nouvelle conversation » remet « Tous »** — déjà le cas (`index.tsx:123`),
  à conserver et couvrir par un test.
- **Restauration de session conservée** (`index.tsx:132`) : rouvrir une
  conversation restaure son périmètre — comportement légitime (la conversation
  portait sur un périmètre donné). On l'explicite dans l'UI (badge « périmètre :
  <doc> » quand ≠ Tous) pour que ce ne soit jamais silencieux.
- **Indicateur de périmètre visible.** Quand `selected ≠ ""`, afficher un badge
  discret « Périmètre : <doc> · ✕ » cliquable pour revenir à « Tous » en un clic.
  Rend l'état non-ambigu et réversible — cœur de la correction.

### B. Agent non-collant

Aucune modification backend nécessaire : `_scoped_runner`
(`api/rag.py:114-118`) n'injecte le document que si `source` est truthy, et le
front envoie `source: selected || null`. Corriger l'état UI (§A) suffit à ce que
l'Agent reçoive `null` en périmètre « Tous » et balaie tout le corpus. On ajoute
un **test de non-régression** vérifiant qu'en périmètre « Tous », l'agent n'est
pas scopé.

### C. Routeur `auto` plus juste

- **Élargir les signaux Agent** de `core/router.py` (comparaison, relationnel,
  multi-questions, multi-documents) avec des motifs plus naturels, et
  **intégrer le détecteur d'intention** de 1a (`retrieval.intent`) déjà utilisé
  pour `aggregate` : `aggregate` → synthèse corpus (déjà câblé) ; sinon signaux
  de complexité → Agent ; sinon RAG. Objectif : que « compare X et Y »,
  « quelle différence entre… », « quels liens entre… », « pour chaque cible… »
  partent en Agent en `auto`.
- **Rester conservateur** : ne pas sur-déclencher l'Agent (latence sur GPU
  local). Les valeurs/motifs sont à affiner par l'éval ; le design fige la
  mécanique. Les modes forcés RAG/Agent/Synth restent la main de l'utilisateur.
- Test : un jeu de questions représentatives → mode attendu (rag/agent/synth),
  et non-régression des cas RAG simples.

### D. Onglet Informations

- Nouvelle route `web/src/app/informations/page.tsx` + entrée de nav
  (`header-nav.tsx`, après « Documents »).
- Contenu : une carte par compétence, avec **ce qu'elle fait** et **quand elle
  se déclenche** :
  - Recherche hybride (sémantique + BM25 + fusion RRF)
  - Reranking cross-encoder + plancher de couverture par document (1a)
  - Détection d'intention & routage auto (RAG / Agent / Synthèse corpus)
  - Agent planificateur (multi-saut) — quand une question est complexe
  - **Synthèse corpus** (1b) — déclenchée par « catégorise/liste toutes les X du
    corpus » ; balaie chaque document (MAP) puis agrège (REDUCE) avec sources
  - Self-RAG (auto-correction) & Attribution par affirmation
  - Abstention hors-scope (avec non-abstention sur questions exploratoires)
- Statique (pas d'appel réseau requis) ; réutilise le thème Thales existant.

## Flux (après changements)

Upload → doc listé, **périmètre inchangé (Tous)** · Sélecteur → badge réversible
quand un doc est choisi · `auto` → intention (`aggregate`→synth) sinon
complexité (→agent) sinon RAG · onglet Informations décrit tout.

## Vérification

- **Front** : après upload, le sélecteur affiche toujours « Tous les documents »
  (test composant/état). Choisir un doc → badge « Périmètre : <doc> · ✕ » ;
  clic ✕ → retour « Tous ». « Nouvelle conversation » → « Tous ».
- **Routeur** (offline, déterministe, `tests/test_router*`) : « compare les
  menaces des deux cibles » → agent ; « catégorise toutes les attaques du
  corpus » → synth/aggregate ; « quel est le niveau EAL ? » → rag.
- **Agent non-collant** : périmètre « Tous » → `_agent_events` reçoit
  `source=None`, l'outil n'est pas scopé (test).
- **UI** : `cd web && npm run build` (ou lint) passe ; l'onglet Informations
  s'affiche et liste les compétences dont la Synthèse corpus.
- Suite complète `.venv/bin/python -m pytest -q` verte.

## Risques

- Élargir les signaux du routeur peut sur-déclencher l'Agent (latence) —
  calibrer par l'éval, garder conservateur.
- Le front migre vers Next.js (état en cours) : suivre les patterns existants
  (`web/src/components/chat`), ne pas introduire de dépendance externe (offline).
- Modifs UI à vérifier visuellement (build + éventuelle capture).
