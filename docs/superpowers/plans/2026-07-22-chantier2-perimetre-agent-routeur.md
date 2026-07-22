# Chantier 2 — Périmètre / Agent / Routeur / Informations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Supprimer les frictions d'usage : périmètre documentaire non-collant, routeur `auto` qui déclenche l'Agent quand il faut, et un onglet Informations expliquant les compétences (dont la Synthèse corpus).

**Architecture:** (1) Routeur `core/router.py` élargi et adossé au détecteur d'intention de 1a (Python, testable offline). (2) Front Next.js `web/src` : ne plus auto-restreindre le périmètre après upload, badge de périmètre réversible, nouvel onglet Informations. Front vérifié par `npm run build` + `npm run lint` (pas de runner de test unitaire front) + capture visuelle.

**Tech Stack:** Python 3.12 (routeur, pytest offline) ; Next.js 16 / React 19 (front, build+lint). 100% offline, thème Thales existant.

## Global Constraints

- **100% offline** : aucune dépendance externe ni appel réseau ; réutiliser les composants et le thème existants (`web/src/components/chat`, tokens Thales nuit #242B75 + aqua).
- **Ne pas modifier les branches agent/RAG/synth de `api/rag.py`** (livrées en 1a/1b) — ce chantier ne touche que le routeur, le front, et un test agent.
- **Routeur conservateur** : élargir sans sur-déclencher l'Agent (latence GPU local) ; les modes forcés restent la main de l'utilisateur ; valeurs/motifs à affiner par l'éval — figer la mécanique.
- **Front sans runner de test** : chaque tâche front se vérifie par `cd web && npm run build && npm run lint` réussis ; les tâches visuelles par une capture d'écran relue.
- **L'intention `aggregate` est déjà routée vers `synth` en amont** de `route_query` (1b, `api/rag.py`) — `route_query` ne décide que rag vs agent.

---

### Task 1: Routeur `auto` élargi (RAG vs Agent)

Élargir les signaux de complexité pour que les questions de comparaison / relationnelles / multi-documents naturelles partent en Agent en mode `auto`, sans sur-déclencher.

**Files:**
- Modify: `core/router.py` (`_AGENT_SIGNALS` ~L44-51, éventuellement `route_query`)
- Test: `tests/test_router.py` (étendre)

**Interfaces:**
- Produces: `route_query(question) -> {"mode":"agent"|"rag","reason":str,"signals":[...]}` inchangé de forme ; comportement élargi.

- [ ] **Step 1: Write the failing tests** (ajouter à `tests/test_router.py`)

```python
def test_natural_comparison_routes_to_agent():
    for q in ["Compare les menaces des deux cibles.",
              "Quelle est la différence entre les exigences de A et de B ?",
              "En quoi la cible X diffère-t-elle de la cible Y ?"]:
        assert route_query(q)["mode"] == "agent", q


def test_relational_multi_hop_routes_to_agent():
    for q in ["Quels liens entre les hypothèses et les objectifs de sécurité ?",
              "Quelles conséquences des menaces sur les exigences ?"]:
        assert route_query(q)["mode"] == "agent", q


def test_multi_document_natural_phrasings_route_to_agent():
    for q in ["Pour chaque cible, donne le niveau EAL.",
              "Recense les exigences à travers les documents."]:
        assert route_query(q)["mode"] == "agent", q


def test_simple_factual_still_rag():
    for q in ["Quel est le niveau EAL de la TOE ?",
              "Donne la définition de la TOE.",
              "C'est quoi un IDS ?"]:
        assert route_query(q)["mode"] == "rag", q
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_router.py -v` → certains nouveaux FAIL (phrasings non captés).

- [ ] **Step 3: Implement** — élargir les motifs dans `core/router.py` (`_AGENT_SIGNALS`) :

```python
_AGENT_SIGNALS = {
    "comparaison": re.compile(
        r"\b(compar|differen|diffère|versus|\bvs\b|par rapport a|contrairement|"
        r"plutot que|distingu|oppos|difference entre|ecart entre)"),
    "relationnel": re.compile(
        r"\b(relation entre|lien entre|liens entre|depend|entrain|consequence|"
        r"consequences? (de|des|sur)|impact de|influen|en quoi .* (diff|affect)|"
        r"comment .* affect)"),
    "multi_documents": re.compile(
        r"\b(les deux|chaque (document|cible|target)|pour chaque|entre les "
        r"(cibles|documents|targets)|dans tous les|a travers (les|tous|toutes)|"
        r"d'un document a l'autre|sur l'ensemble des)"),
}
```

> Garder `multi_questions` (≥2 `?`). Ne pas capter les intentions `aggregate`
> (« toutes les X du corpus ») — elles sont routées vers `synth` en amont, mais
> par sécurité, `route_query` ne doit pas les revendiquer : c'est déjà le cas
> (ces motifs ne matchent pas les signaux Agent ci-dessus).

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_router.py -v` → PASS (anciens + nouveaux). Puis suite complète.

- [ ] **Step 5: Commit**

```bash
git add core/router.py tests/test_router.py
git commit -m "feat(router): élargir les signaux Agent (comparaison/relationnel/multi-doc naturels)"
```

---

### Task 2: Périmètre non-collant après upload

Supprimer l'auto-sélection du document après ingestion : le périmètre reste « Tous les documents ».

**Files:**
- Modify: `web/src/components/chat/index.tsx` (upload handler, ligne ~402 `setSelected(ok[0].source_name ...)`)

**Interfaces:**
- L'upload ajoute le doc à la liste (`docs`) et confirme l'ingestion, mais **ne change pas** `selected` (reste `""` = Tous).

- [ ] **Step 1: Locate & change** — dans le handler d'upload de `index.tsx`, SUPPRIMER la ligne qui force `setSelected(ok[0].source_name ?? ok[0].name)`. Conserver le rafraîchissement de la liste des documents et le message de succès. Ne pas toucher `newConversation` (`setSelected("")`) ni la restauration de session (`setSelected(d.source_filter ?? "")`).

- [ ] **Step 2: Vérifier build + lint**

Run: `cd web && npm run build && npm run lint`
Expected: build OK, lint OK (aucune variable inutilisée introduite — nettoyer si `ok[0]` devient inutilisé).

- [ ] **Step 3: Commit**

```bash
git add web/src/components/chat/index.tsx
git commit -m "fix(ui): l'upload ne restreint plus le périmètre (reste « Tous les documents »)"
```

---

### Task 3: Badge de périmètre réversible

Quand un document précis est sélectionné, afficher un badge clair « Périmètre : <doc> · ✕ » ; le ✕ ramène à « Tous » en un clic. Rend l'état non-ambigu (le cœur de la plainte « collé au dernier document »).

**Files:**
- Modify: `web/src/components/chat/composer.tsx` (près du `<select>` du périmètre, ~L113-122) ou l'en-tête du chat
- (réutiliser `selected` / `setSelected` déjà passés au composer)

**Interfaces:**
- Consomme `selected: string` et `setSelected: (s:string)=>void` (déjà en props). Affiche le badge uniquement si `selected !== ""`.

- [ ] **Step 1: Implement** — ajouter, à côté du sélecteur, un badge conditionnel (thème Thales, style discret) :

```tsx
{selected !== "" && (
  <button
    type="button"
    onClick={() => setSelected("")}
    className="inline-flex items-center gap-1 rounded-full border border-accent/40
               px-2 py-0.5 text-[11px] text-fg-muted hover:text-fg"
    title="Revenir à tous les documents"
  >
    Périmètre : {selected} <span aria-hidden>✕</span>
  </button>
)}
```

(Adapter les classes aux tokens réellement disponibles ; garder cohérent avec les autres pilules de l'UI.)

- [ ] **Step 2: Build + lint** `cd web && npm run build && npm run lint` → OK.

- [ ] **Step 3: Vérification visuelle** — lancer l'app (ou une capture) : sélectionner un document → le badge apparaît ; cliquer ✕ → périmètre « Tous », badge disparaît. Joindre la capture au rapport.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/chat/composer.tsx
git commit -m "feat(ui): badge de périmètre réversible (retour « Tous » en un clic)"
```

---

### Task 4: Onglet Informations

Nouvel onglet listant les compétences (dont la Synthèse corpus livrée en 1b), avec ce que fait chacune et quand elle se déclenche.

**Files:**
- Create: `web/src/app/informations/page.tsx`
- Modify: `web/src/components/header-nav.tsx` (ajouter `{ href: "/informations", label: "Informations" }` après Documents)

**Interfaces:**
- Page statique (aucun appel réseau). Réutilise le layout/thème existants (voir `web/src/app/rag/page.tsx` pour le pattern de page + en-tête).

- [ ] **Step 1: Créer la page** — une page listant des cartes « compétence » : titre + « Ce que ça fait » + « Quand ça se déclenche ». Couvrir : Recherche hybride (sémantique+BM25+RRF), Reranking cross-encoder + plancher par document, Détection d'intention & routage (RAG/Agent/Synthèse corpus), Agent planificateur, **Synthèse corpus** (« catégorise/liste toutes les X du corpus » → MAP par document puis REDUCE agrégé/sourcé), Self-RAG, Attribution par affirmation, Abstention hors-scope (avec non-abstention exploratoire). Suivre la structure d'une page existante (`app/rag/page.tsx`) pour l'en-tête/titre.

- [ ] **Step 2: Ajouter l'entrée de nav** dans `header-nav.tsx` (liste `RAG_LINKS`), après « Documents ».

- [ ] **Step 3: Build + lint** `cd web && npm run build && npm run lint` → OK.

- [ ] **Step 4: Vérification visuelle** — l'onglet « Informations » apparaît dans la nav, la page s'affiche avec les cartes (dont Synthèse corpus). Capture jointe.

- [ ] **Step 5: Commit**

```bash
git add web/src/app/informations/page.tsx web/src/components/header-nav.tsx
git commit -m "feat(ui): onglet Informations documentant les compétences (dont Synthèse corpus)"
```

---

### Task 5: Non-régression agent non-collant + validation UI de bout en bout

**Files:**
- Test: `tests/test_agent_scope.py` (nouveau, Python offline)

**Interfaces:**
- Vérifie que le wrapper de périmètre de l'agent n'injecte PAS de document quand `source` est None/vide.

- [ ] **Step 1: Write the test** — reproduire la logique de `_scoped_runner` (`api/rag.py:114-118`) via un test qui appelle le vrai wrapper avec `source=None` et vérifie qu'aucun `document` n'est injecté dans les arguments de l'outil ; et avec `source="a.md"` qu'il l'est. Si `_scoped_runner` est une closure interne, exposer une petite fonction pure `scope_arguments(source, arguments)` dans `api/rag.py` (refactor minimal) et l'utiliser dans `_agent_events`, puis tester cette fonction.

```python
# tests/test_agent_scope.py
from api.rag import scope_arguments


def test_no_scope_when_source_none():
    assert scope_arguments(None, {"query": "x"}) == {"query": "x"}
    assert "document" not in scope_arguments("", {"query": "x"})


def test_scope_injected_when_source_set():
    out = scope_arguments("a.md", {"query": "x"})
    assert out["document"] == "a.md"


def test_explicit_document_not_overridden():
    out = scope_arguments("a.md", {"query": "x", "document": "b.md"})
    assert out["document"] == "b.md"
```

- [ ] **Step 2: Run** → FAIL (scope_arguments absent). **Implement** : extraire la logique de `_scoped_runner` en `scope_arguments(source, arguments) -> dict` au niveau module dans `api/rag.py`, et l'utiliser dans `_scoped_runner`. Ne change pas le comportement (refactor).

- [ ] **Step 3: Run** `.venv/bin/python -m pytest tests/test_agent_scope.py -v` → PASS. Suite complète verte.

- [ ] **Step 4: Validation UI de bout en bout** — app lancée : upload d'un document → périmètre reste « Tous » ; poser « compare les deux documents » en `auto` → part en **Agent** (frame `route` = agent) et balaie les deux ; onglet Informations décrit la Synthèse corpus. Capture(s) jointe(s).

- [ ] **Step 5: Commit**

```bash
git add api/rag.py tests/test_agent_scope.py
git commit -m "test(agent): périmètre non injecté quand source vide (+ refactor scope_arguments)"
```

---

## Self-Review

**Spec coverage :**
- §A périmètre non-collant → Tasks 2 (upload) + 3 (badge réversible) ; new-conversation déjà OK. ✅
- §B agent non-collant → Task 5 (test + refactor) ; corrigé transitivement par §A. ✅
- §C routeur élargi → Task 1. ✅
- §D onglet Informations → Task 4. ✅
- Vérification (build/lint + visuel + pytest) → intégrée à chaque tâche + Task 5. ✅

**Placeholder scan :** aucun TBD ; les classes CSS du badge sont « à adapter aux tokens réels » (indication, pas placeholder — l'implémenteur vérifie au build).

**Type consistency :** `scope_arguments(source, arguments) -> dict` cohérent Task 5. `route_query` forme inchangée. `selected/setSelected` déjà typés dans le composer.

## Notes d'exécution

- Ordre : Task 1 (Python, isolée) → 2 → 3 → 4 (front, séquentielles, même zone UI) → 5 (test + validation finale).
- Les tâches front (2,3,4) n'ont pas de test unitaire : gate = `npm run build && npm run lint` + capture visuelle relue. L'implémenteur DOIT joindre la capture pour 3 et 4.
- Après Chantier 2 : **Chantier 3** (génération PDF/DOCX à la demande).
