# EBIOS RM — Atelier 1 (V1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nouvel onglet `EBIOS RM` : sélection de documents ingérés, génération LLM structurée et sourcée de l'Atelier 1, édition/validation humaine, export XLSX fidèle au classeur contractuel (DOCX/PDF explicitement indisponibles en V1).

**Architecture:** JSON versionné en Mongo = source de vérité ; exporteur XLSX déterministe (openpyxl) qui copie le modèle contractuel et n'écrit que les ancres des zones fusionnées ; pipeline extraction (`build_llm("extract")`, `format="json"`) → consolidation déterministe → contrôle borné (warnings, jamais de régénération auto) ; API FastAPI + SSE ; UI Next.js avec 3 tables éditables.

**Tech Stack:** FastAPI, pymongo (sync), openpyxl 3.1.5, pydantic v2, Next.js App Router, SSE maison (`api/common.py:_sse` + `web/src/lib/sse.ts`), rôles `core/model_router.py`.

## Global Constraints

- 100 % local/offline. Aucun modèle Ollama en dur : uniquement `build_llm(role)` (`core/model_router.py:149`).
- Modèle contractuel : `/home/marsattacks/Téléchargements/EBIOS_RM_Ateliers_modele(1).xlsx`, SHA-256 `752dadf48369d0e135def3a79affccdb455d0a812925e193b4068f75a45501dc`. **Jamais écrasé** — toujours copié. Chargement `openpyxl.load_workbook(..., data_only=False)`.
- V1 remplit uniquement `Atelier 1 - Missions`, `Atelier 1 - ER`, `Atelier 1 - GAP`. Écriture **uniquement dans les ancres** des zones fusionnées. Les 6 noms définis `#REF!` ne sont pas réparés.
- Capacités (mesurées sur le classeur réel) : 5 valeurs métier, 11 biens supports, 7 ER, 3 blocs GAP avec écarts **[2, 2, 1]** (fusions réelles `D5:D6`/`E5:E6` ⇒ ancres écarts `D3,D4 | D5,D7 | D8` — le handoff disait 3 écarts au bloc 2, les fusions réelles priment). Dépassement ⇒ **refus explicite**, pas de troncature.
- Injection de formule : toute chaîne écrite est forcée en texte (`cell.data_type = "s"`), y compris si elle commence par `=`, `+`, `-`, `@`.
- Chemins d'export contrôlés côté serveur (tempfile), jamais fournis par le client. Nom : `{projet}_atelier1_v{version}_{AAAAMMJJ}.xlsx`.
- Export **uniquement après validation** (décision V1). DOCX/PDF : indisponibles en V1 (sélecteur présent, options désactivées avec raison).
- Décisions V1 : gravité entier 1–4 (Mineure/Significative/Grave/Critique) ; état GAP ∈ {Appliqué, Partiellement appliqué, Non appliqué} ; responsable par VM en JSON, cellule `B5` = valeur commune ou noms distincts joints par « / » ; référentiels texte libre ; rétention illimitée ; régénération ciblée hors V1.
- Aucune assertion sans preuve rattachée ⇒ warning visible, jamais de citation inventée. Pas de boucle de réparation automatique.
- Ne pas toucher aux changements Codex non commités ni à `docs/PDF/`, `docs/out/`. `git add` **par chemin explicite** uniquement.
- Branche de travail : `feat/ebios-rm-atelier1` (créée depuis `feat/chantier1-retrieval-multidoc`).
- Tests : `.venv/bin/python -m pytest tests/ebios/... -q` ; front : `npm run lint` + `npm run build` dans `web/`.

---

### Task 1: Schémas Atelier 1 + capacités + fixtures

**Files:**
- Create: `core/ebios/__init__.py` (vide), `core/ebios/exporters/__init__.py` (vide)
- Create: `core/ebios/schemas.py`
- Create: `tests/ebios/__init__.py` (vide), `tests/ebios/fixtures.py`
- Test: `tests/ebios/test_schemas.py`

**Interfaces:**
- Produces: `EbiosStudy` (pydantic, `.model_dump()`/`.model_validate()`), `Atelier1`, `ValeurMetier`, `BienSupport`, `EvenementRedoute`, `SocleSecurite`, `Ecart`, `Preuve`, `Responsable`, `GRAVITE_LABELS: dict[int,str]`, `CAPACITES`, `GAP_ECART_CAPACITES = [2,2,1]`, `check_references(study: EbiosStudy) -> list[str]`, `check_capacity(a1: Atelier1) -> list[str]`, `bounded_control(study: EbiosStudy) -> list[str]`
- Fixture : `tests/ebios/fixtures.py:make_study(**overrides) -> dict` (étude valide complète, 2 VM, 2 BS, 2 ER, 2 référentiels GAP)

- [ ] **Step 1: Créer la branche**

```bash
git checkout -b feat/ebios-rm-atelier1
```

- [ ] **Step 2: Écrire les tests qui échouent**

```python
# tests/ebios/test_schemas.py
import pytest
from pydantic import ValidationError
from core.ebios.schemas import (
    EbiosStudy, Atelier1, check_references, check_capacity, bounded_control,
    GRAVITE_LABELS, GAP_ECART_CAPACITES,
)
from tests.ebios.fixtures import make_study


def test_roundtrip_valide():
    study = EbiosStudy.model_validate(make_study())
    assert study.schema_version == "1.0"
    assert study.atelier_1.valeurs_metier[0].nature == "processus"
    assert study.model_dump()["atelier_1"]["evenements_redoutes"][0]["gravite"] == 3


def test_gravite_bornee_1_4():
    data = make_study()
    data["atelier_1"]["evenements_redoutes"][0]["gravite"] = 5
    with pytest.raises(ValidationError):
        EbiosStudy.model_validate(data)
    assert GRAVITE_LABELS == {1: "Mineure", 2: "Significative", 3: "Grave", 4: "Critique"}


def test_etat_application_enum():
    data = make_study()
    data["atelier_1"]["socle_securite"][0]["etat_application"] = "Inconnu"
    with pytest.raises(ValidationError):
        EbiosStudy.model_validate(data)


def test_references_internes():
    data = make_study()
    data["atelier_1"]["evenements_redoutes"][0]["valeur_metier_id"] = "vm-999"
    errs = check_references(EbiosStudy.model_validate(data))
    assert any("vm-999" in e for e in errs)
    assert check_references(EbiosStudy.model_validate(make_study())) == []


def test_capacites():
    data = make_study()
    a1 = data["atelier_1"]
    vm = a1["valeurs_metier"][0]
    a1["valeurs_metier"] = [dict(vm, id=f"vm-{i}") for i in range(1, 7)]  # 6 > 5
    errs = check_capacity(EbiosStudy.model_validate(data).atelier_1)
    assert any("valeurs" in e.lower() for e in errs)
    assert GAP_ECART_CAPACITES == [2, 2, 1]


def test_capacite_ecarts_gap():
    data = make_study()
    gap = data["atelier_1"]["socle_securite"][0]
    gap["ecarts"] = [dict(gap["ecarts"][0]) for _ in range(3)]  # 3 > bloc 1 (2)
    errs = check_capacity(EbiosStudy.model_validate(data).atelier_1)
    assert any("écart" in e.lower() or "ecart" in e.lower() for e in errs)


def test_bounded_control_signale_preuves_manquantes():
    data = make_study()
    data["atelier_1"]["valeurs_metier"][0]["preuves"] = []
    warns = bounded_control(EbiosStudy.model_validate(data))
    assert any("preuve" in w.lower() for w in warns)
```

```python
# tests/ebios/fixtures.py
import copy

_PREUVE = {"document_id": "doc-a.md", "source": "doc-a.md", "page": 3, "extrait": "extrait cité"}

_STUDY = {
    "schema_version": "1.0",
    "project_id": "p-1",
    "source_document_ids": ["doc-a.md"],
    "atelier_1": {
        "mission": "Assurer la surveillance spatiale.",
        "valeurs_metier": [
            {"id": "vm-1", "denomination": "Contrôle mission", "nature": "processus",
             "description": "Pilotage des opérations.", "responsable": {"nom": "Dupont", "type": "INTERNE"},
             "preuves": [dict(_PREUVE)]},
            {"id": "vm-2", "denomination": "Télémesures", "nature": "information",
             "description": "Données de vol.", "responsable": {"nom": "Dupont", "type": "INTERNE"},
             "preuves": [dict(_PREUVE)]},
        ],
        "biens_supports": [
            {"id": "bs-1", "denomination": "Segment sol", "description": "Stations.",
             "responsable": {"nom": "Martin", "type": "EXTERNE"},
             "valeurs_metier_associees": ["vm-1"], "preuves": [dict(_PREUVE)]},
            {"id": "bs-2", "denomination": "Liaison RF", "description": "Bande X.",
             "responsable": {"nom": "Martin", "type": "EXTERNE"},
             "valeurs_metier_associees": ["vm-1", "vm-2"], "preuves": [dict(_PREUVE)]},
        ],
        "evenements_redoutes": [
            {"id": "er-1", "valeur_metier_id": "vm-1", "evenement": "Perte du contrôle",
             "impact": "Interruption mission", "gravite": 3, "justification_gravite": "Arrêt prolongé",
             "preuves": [dict(_PREUVE)]},
            {"id": "er-2", "valeur_metier_id": "vm-2", "evenement": "Divulgation télémesures",
             "impact": "Atteinte confidentialité", "gravite": 2, "justification_gravite": "",
             "preuves": [dict(_PREUVE)]},
        ],
        "socle_securite": [
            {"id": "gap-1", "type_referentiel": "Norme", "nom_referentiel": "ISO 27001",
             "etat_application": "Partiellement appliqué",
             "ecarts": [{"description": "Pas de revue annuelle", "justification": "Effectif réduit",
                         "preuves": [dict(_PREUVE)]}]},
            {"id": "gap-2", "type_referentiel": "Réglementation", "nom_referentiel": "LPM",
             "etat_application": "Appliqué", "ecarts": []},
        ],
    },
    "warnings": [],
    "generated_at": "2026-07-27T12:00:00",
    "validated_at": None,
}


def make_study(**overrides):
    data = copy.deepcopy(_STUDY)
    data.update(overrides)
    return data
```

- [ ] **Step 3: Vérifier l'échec**

Run: `.venv/bin/python -m pytest tests/ebios/test_schemas.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.ebios'`

- [ ] **Step 4: Implémenter les schémas**

```python
# core/ebios/schemas.py
"""Schémas Atelier 1 EBIOS RM — source de vérité JSON, versionnée et migrable."""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

GRAVITE_LABELS = {1: "Mineure", 2: "Significative", 3: "Grave", 4: "Critique"}

# Capacités du classeur contractuel (fusions réelles vérifiées le 2026-07-27).
CAPACITES = {"valeurs_metier": 5, "biens_supports": 11, "evenements_redoutes": 7, "socle_securite": 3}
GAP_ECART_CAPACITES = [2, 2, 1]  # ancres D3,D4 | D5,D7 | D8


class NatureVM(str, Enum):
    processus = "processus"
    information = "information"


class TypeResponsable(str, Enum):
    interne = "INTERNE"
    externe = "EXTERNE"


class EtatApplication(str, Enum):
    applique = "Appliqué"
    partiel = "Partiellement appliqué"
    non_applique = "Non appliqué"


class Preuve(BaseModel):
    document_id: str
    source: str
    page: Optional[int] = None
    extrait: str


class Responsable(BaseModel):
    nom: str
    type: TypeResponsable


class ValeurMetier(BaseModel):
    id: str
    denomination: str
    nature: NatureVM
    description: str = ""
    responsable: Responsable
    preuves: List[Preuve] = Field(default_factory=list)


class BienSupport(BaseModel):
    id: str
    denomination: str
    description: str = ""
    responsable: Responsable
    valeurs_metier_associees: List[str] = Field(default_factory=list)
    preuves: List[Preuve] = Field(default_factory=list)


class EvenementRedoute(BaseModel):
    id: str
    valeur_metier_id: str = ""
    evenement: str
    impact: str = ""
    gravite: int = Field(ge=1, le=4)
    justification_gravite: str = ""
    preuves: List[Preuve] = Field(default_factory=list)


class Ecart(BaseModel):
    description: str
    justification: str = ""
    preuves: List[Preuve] = Field(default_factory=list)


class SocleSecurite(BaseModel):
    id: str
    type_referentiel: str
    nom_referentiel: str
    etat_application: EtatApplication
    ecarts: List[Ecart] = Field(default_factory=list)


class Atelier1(BaseModel):
    mission: str = ""
    valeurs_metier: List[ValeurMetier] = Field(default_factory=list)
    biens_supports: List[BienSupport] = Field(default_factory=list)
    evenements_redoutes: List[EvenementRedoute] = Field(default_factory=list)
    socle_securite: List[SocleSecurite] = Field(default_factory=list)


class EbiosStudy(BaseModel):
    schema_version: str = SCHEMA_VERSION
    project_id: str
    source_document_ids: List[str] = Field(default_factory=list)
    atelier_1: Atelier1 = Field(default_factory=Atelier1)
    warnings: List[str] = Field(default_factory=list)
    generated_at: str = ""
    validated_at: Optional[str] = None


def check_references(study: EbiosStudy) -> List[str]:
    """Erreurs de références internes (bloquantes pour la validation)."""
    errs: List[str] = []
    vm_ids = {vm.id for vm in study.atelier_1.valeurs_metier}
    for er in study.atelier_1.evenements_redoutes:
        if er.valeur_metier_id and er.valeur_metier_id not in vm_ids:
            errs.append(f"ER {er.id} : valeur métier inconnue « {er.valeur_metier_id} »")
    for bs in study.atelier_1.biens_supports:
        for ref in bs.valeurs_metier_associees:
            if ref not in vm_ids:
                errs.append(f"Bien support {bs.id} : valeur métier inconnue « {ref} »")
    return errs


def check_capacity(a1: Atelier1) -> List[str]:
    """Dépassements des capacités du classeur (bloquants pour l'export, jamais tronqués)."""
    errs: List[str] = []
    counts = {
        "valeurs métier": (len(a1.valeurs_metier), CAPACITES["valeurs_metier"]),
        "biens supports": (len(a1.biens_supports), CAPACITES["biens_supports"]),
        "événements redoutés": (len(a1.evenements_redoutes), CAPACITES["evenements_redoutes"]),
        "référentiels": (len(a1.socle_securite), CAPACITES["socle_securite"]),
    }
    for label, (n, cap) in counts.items():
        if n > cap:
            errs.append(f"Capacité dépassée : {n} {label} pour {cap} emplacements dans le classeur")
    for i, gap in enumerate(a1.socle_securite[: len(GAP_ECART_CAPACITES)]):
        cap = GAP_ECART_CAPACITES[i]
        if len(gap.ecarts) > cap:
            errs.append(
                f"Capacité dépassée : {len(gap.ecarts)} écarts pour le référentiel "
                f"« {gap.nom_referentiel} » (bloc {i + 1} : {cap} max)"
            )
    return errs


def bounded_control(study: EbiosStudy) -> List[str]:
    """Contrôle borné : signale, n'invente ni ne régénère jamais."""
    warns: List[str] = []
    a1 = study.atelier_1
    if not a1.mission:
        warns.append("Mission absente")
    for vm in a1.valeurs_metier:
        if not vm.preuves:
            warns.append(f"Valeur métier « {vm.denomination} » sans preuve")
    for bs in a1.biens_supports:
        if not bs.preuves:
            warns.append(f"Bien support « {bs.denomination} » sans preuve")
    for er in a1.evenements_redoutes:
        if not er.preuves:
            warns.append(f"Événement redouté « {er.evenement} » sans preuve")
        if not er.valeur_metier_id:
            warns.append(f"Événement redouté « {er.evenement} » non rattaché à une valeur métier")
    for gap in a1.socle_securite:
        for ec in gap.ecarts:
            if not ec.preuves:
                warns.append(f"Écart « {ec.description[:60]} » sans preuve")
    warns.extend(check_capacity(a1))
    return warns
```

- [ ] **Step 5: Vérifier le succès**

Run: `.venv/bin/python -m pytest tests/ebios/test_schemas.py -q`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add core/ebios/__init__.py core/ebios/exporters/__init__.py core/ebios/schemas.py tests/ebios/__init__.py tests/ebios/fixtures.py tests/ebios/test_schemas.py
git commit -m "feat(ebios): schémas Atelier 1 v1.0 — capacités classeur réelles, contrôle borné"
```

---

### Task 2: Exporteur XLSX déterministe

**Files:**
- Create: `core/ebios/exporters/xlsx.py`
- Modify: `env_config.py` (clé `EBIOS_TEMPLATE_PATH` dans `get_config()` + ré-export fin de fichier)
- Modify: `.env.example` (ligne `EBIOS_TEMPLATE_PATH=`)
- Test: `tests/ebios/test_xlsx_export.py`

**Interfaces:**
- Consumes: `make_study()`, `check_capacity`, `EbiosStudy`
- Produces: `export_xlsx(study: dict, out_path, template_path=None) -> pathlib.Path` (lève `ExportCapacityError(ValueError)` si dépassement, `TemplateIntegrityError(RuntimeError)` si SHA différent), `common_responsable(a1: Atelier1) -> str`, `TEMPLATE_SHA256`

- [ ] **Step 1: Écrire les tests qui échouent**

```python
# tests/ebios/test_xlsx_export.py
import hashlib
import shutil
import pytest

openpyxl = pytest.importorskip("openpyxl")

from core.ebios.exporters.xlsx import (
    export_xlsx, ExportCapacityError, TemplateIntegrityError, TEMPLATE_SHA256, common_responsable,
)
from core.ebios.schemas import EbiosStudy
from tests.ebios.fixtures import make_study

TEMPLATE = "/home/marsattacks/Téléchargements/EBIOS_RM_Ateliers_modele(1).xlsx"


@pytest.fixture()
def out(tmp_path):
    return tmp_path / "export.xlsx"


def _load(path):
    return openpyxl.load_workbook(path, data_only=False)


def test_export_remplit_les_trois_feuilles(out):
    export_xlsx(make_study(), out, template_path=TEMPLATE)
    wb = _load(out)
    mi = wb["Atelier 1 - Missions"]
    assert mi["B1"].value == "Assurer la surveillance spatiale."
    assert mi["B2"].value == "Contrôle mission"
    assert mi["B3"].value == "processus"
    assert mi["G2"].value == "Télémesures"
    assert mi["B5"].value == "Dupont (INTERNE)"          # responsable commun identique
    assert mi["B6"].value == "Segment sol"               # bien support 1
    assert mi["C6"].value == "Liaison RF"
    er = wb["Atelier 1 - ER"]
    assert er["A3"].value == "Contrôle mission"          # dénomination résolue depuis vm-1
    assert er["B3"].value == "Perte du contrôle"
    assert er["D3"].value == 3
    gap = wb["Atelier 1 - GAP"]
    assert gap["A3"].value == "Norme"
    assert gap["C3"].value == "Partiellement appliqué"
    assert gap["D3"].value == "Pas de revue annuelle"
    assert gap["A5"].value == "Réglementation"           # bloc 2, ancre A5


def test_modele_source_intact_et_structure_preservee(out):
    before = hashlib.sha256(open(TEMPLATE, "rb").read()).hexdigest()
    export_xlsx(make_study(), out, template_path=TEMPLATE)
    assert hashlib.sha256(open(TEMPLATE, "rb").read()).hexdigest() == before == TEMPLATE_SHA256
    wb_t, wb_o = _load(TEMPLATE), _load(out)
    assert wb_o.sheetnames == wb_t.sheetnames                      # 15 feuilles, même ordre
    for name in wb_t.sheetnames:
        assert {str(m) for m in wb_o[name].merged_cells.ranges} == \
               {str(m) for m in wb_t[name].merged_cells.ranges}
    assert set(wb_o.defined_names) == set(wb_t.defined_names)      # #REF! non réparés
    # Formules hors Atelier 1 conservées (Atelier 3 - PP, datacartoPP)
    for name in ("Atelier 3 - PP", "datacartoPP"):
        f_t = [(c.coordinate, c.value) for row in wb_t[name].iter_rows() for c in row
               if c.data_type == "f"]
        f_o = [(c.coordinate, c.value) for row in wb_o[name].iter_rows() for c in row
               if c.data_type == "f"]
        assert f_o == f_t


def test_injection_formule_forcee_en_texte(out):
    data = make_study()
    data["atelier_1"]["valeurs_metier"][0]["denomination"] = "=HYPERLINK(\"x\")"
    data["atelier_1"]["evenements_redoutes"][0]["evenement"] = "+DANGER"
    export_xlsx(data, out, template_path=TEMPLATE)
    wb = _load(out)
    b2 = wb["Atelier 1 - Missions"]["B2"]
    assert b2.value == "=HYPERLINK(\"x\")" and b2.data_type == "s"
    b3 = wb["Atelier 1 - ER"]["B3"]
    assert b3.value == "+DANGER" and b3.data_type == "s"


def test_depassement_refuse_sans_tronquer(out):
    data = make_study()
    vm = data["atelier_1"]["valeurs_metier"][0]
    data["atelier_1"]["valeurs_metier"] = [dict(vm, id=f"vm-{i}") for i in range(1, 7)]
    with pytest.raises(ExportCapacityError):
        export_xlsx(data, out, template_path=TEMPLATE)
    assert not out.exists()


def test_sha_template_verifie(tmp_path, out):
    bad = tmp_path / "bad.xlsx"
    shutil.copy(TEMPLATE, bad)
    with open(bad, "ab") as f:
        f.write(b"x")
    with pytest.raises(TemplateIntegrityError):
        export_xlsx(make_study(), out, template_path=bad)


def test_responsable_commun_heterogene(out):
    data = make_study()
    data["atelier_1"]["valeurs_metier"][1]["responsable"]["nom"] = "Durand"
    a1 = EbiosStudy.model_validate(data).atelier_1
    assert common_responsable(a1) == "Dupont (INTERNE) / Durand (INTERNE)"


def test_cellule_sans_valeur_non_remplie(out):
    data = make_study()
    data["atelier_1"]["socle_securite"][1]["ecarts"] = []   # gap-2 sans écart
    export_xlsx(data, out, template_path=TEMPLATE)
    wb = _load(out)
    assert wb["Atelier 1 - GAP"]["D5"].value is None
```

- [ ] **Step 2: Vérifier l'échec**

Run: `.venv/bin/python -m pytest tests/ebios/test_xlsx_export.py -q`
Expected: FAIL — `ModuleNotFoundError` sur `core.ebios.exporters.xlsx`

- [ ] **Step 3: Implémenter l'exporteur**

```python
# core/ebios/exporters/xlsx.py
"""Export XLSX déterministe de l'Atelier 1 vers le classeur contractuel EBIOS RM.

Copie le modèle (jamais écrasé, intégrité SHA-256 vérifiée), n'écrit que les
cellules d'ancrage des zones fusionnées, force chaque chaîne en texte
(anti-injection de formule) et refuse tout dépassement de capacité.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import openpyxl

from core.ebios.schemas import Atelier1, EbiosStudy, check_capacity

TEMPLATE_SHA256 = "752dadf48369d0e135def3a79affccdb455d0a812925e193b4068f75a45501dc"

_SHEET_MISSIONS = "Atelier 1 - Missions"
_SHEET_ER = "Atelier 1 - ER"
_SHEET_GAP = "Atelier 1 - GAP"

# Ancres mesurées sur le classeur réel (fusions vérifiées le 2026-07-27).
_VM_COLS = ("B", "G", "H", "J", "M")                     # lignes 2/3/4, resp. commun B5
_BS_COLS = ("B", "C", "D", "G", "H", "I", "J", "K", "L", "M", "N")  # lignes 6/7/8
_ER_FIRST_ROW, _ER_LAST_ROW = 3, 9
_GAP_BLOCKS = (
    {"anchor_row": 3, "ecart_rows": (3, 4)},
    {"anchor_row": 5, "ecart_rows": (5, 7)},   # D5:D6/E5:E6 fusionnées -> 2 ancres
    {"anchor_row": 8, "ecart_rows": (8,)},
)


class ExportCapacityError(ValueError):
    """Le contenu dépasse les capacités du classeur : refus, pas de troncature."""


class TemplateIntegrityError(RuntimeError):
    """Le modèle contractuel ne correspond pas au SHA-256 attendu."""


def _default_template_path() -> str:
    from env_config import EBIOS_TEMPLATE_PATH
    return EBIOS_TEMPLATE_PATH


def _set(ws, coord: str, value) -> None:
    """Écrit une cellule ; les chaînes sont forcées en texte (jamais interprétées)."""
    if value is None or value == "":
        return
    cell = ws[coord]
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"


def _fmt_resp(resp) -> str:
    return f"{resp.nom} ({resp.type.value})"


def common_responsable(a1: Atelier1) -> str:
    """Valeur de B5:N5 : commune si identique, sinon noms distincts joints."""
    seen: list[str] = []
    for vm in a1.valeurs_metier:
        label = _fmt_resp(vm.responsable)
        if label not in seen:
            seen.append(label)
    return " / ".join(seen)


def export_xlsx(study: dict, out_path, template_path=None) -> Path:
    model = EbiosStudy.model_validate(study)
    a1 = model.atelier_1
    errs = check_capacity(a1)
    if errs:
        raise ExportCapacityError(" ; ".join(errs))

    template = Path(template_path or _default_template_path())
    digest = hashlib.sha256(template.read_bytes()).hexdigest()
    if digest != TEMPLATE_SHA256:
        raise TemplateIntegrityError(
            f"Modèle {template} : SHA-256 {digest} != attendu {TEMPLATE_SHA256}"
        )

    out = Path(out_path)
    shutil.copy(template, out)
    wb = openpyxl.load_workbook(out, data_only=False)

    ws = wb[_SHEET_MISSIONS]
    _set(ws, "B1", a1.mission)
    for col, vm in zip(_VM_COLS, a1.valeurs_metier):
        _set(ws, f"{col}2", vm.denomination)
        _set(ws, f"{col}3", vm.nature.value)
        _set(ws, f"{col}4", vm.description)
    _set(ws, "B5", common_responsable(a1))
    for col, bs in zip(_BS_COLS, a1.biens_supports):
        _set(ws, f"{col}6", bs.denomination)
        _set(ws, f"{col}7", bs.description)
        _set(ws, f"{col}8", _fmt_resp(bs.responsable))

    ws = wb[_SHEET_ER]
    vm_names = {vm.id: vm.denomination for vm in a1.valeurs_metier}
    for row, er in zip(range(_ER_FIRST_ROW, _ER_LAST_ROW + 1), a1.evenements_redoutes):
        _set(ws, f"A{row}", vm_names.get(er.valeur_metier_id, ""))
        _set(ws, f"B{row}", er.evenement)
        _set(ws, f"C{row}", er.impact)
        _set(ws, f"D{row}", er.gravite)

    ws = wb[_SHEET_GAP]
    for block, gap in zip(_GAP_BLOCKS, a1.socle_securite):
        row = block["anchor_row"]
        _set(ws, f"A{row}", gap.type_referentiel)
        _set(ws, f"B{row}", gap.nom_referentiel)
        _set(ws, f"C{row}", gap.etat_application.value)
        for erow, ecart in zip(block["ecart_rows"], gap.ecarts):
            _set(ws, f"D{erow}", ecart.description)
            _set(ws, f"E{erow}", ecart.justification)

    wb.save(out)
    return out
```

Dans `env_config.py`, dans `get_config()` (près des autres chemins/flags) :

```python
"EBIOS_TEMPLATE_PATH": os.environ.get(
    "EBIOS_TEMPLATE_PATH",
    "/home/marsattacks/Téléchargements/EBIOS_RM_Ateliers_modele(1).xlsx",
),
```

et en fin de fichier, avec les autres ré-exports :

```python
EBIOS_TEMPLATE_PATH = CONFIG["EBIOS_TEMPLATE_PATH"]
```

Dans `.env.example`, section dédiée :

```bash
# --- EBIOS RM ---
# Classeur contractuel (jamais modifié, copié à chaque export)
EBIOS_TEMPLATE_PATH=/home/marsattacks/Téléchargements/EBIOS_RM_Ateliers_modele(1).xlsx
```

Note : `test_depassement_refuse_sans_tronquer` exige que la capacité soit vérifiée **avant** la copie (`assert not out.exists()`), l'ordre du code ci-dessus le garantit.

- [ ] **Step 4: Vérifier le succès**

Run: `.venv/bin/python -m pytest tests/ebios/test_xlsx_export.py -q`
Expected: 7 passed

- [ ] **Step 5: Non-régression schémas**

Run: `.venv/bin/python -m pytest tests/ebios -q`
Expected: 14 passed

- [ ] **Step 6: Commit**

```bash
git add core/ebios/exporters/xlsx.py tests/ebios/test_xlsx_export.py env_config.py .env.example
git commit -m "feat(ebios): export XLSX déterministe — ancres réelles, SHA-256, anti-injection, refus capacité"
```

---

### Task 3: Repository Mongo + versionnement optimiste

**Files:**
- Create: `core/ebios/repository.py`
- Create: `tests/ebios/conftest.py` (fake Mongo en mémoire)
- Test: `tests/ebios/test_repository.py`

**Interfaces:**
- Consumes: `utils/mongo.py:get_db()` (singleton pymongo sync)
- Produces: classe `EbiosRepository(db=None)` :
  - `create_project(name: str, document_ids: list[str]) -> dict` (`project_id` uuid4 hex, `status="created"`)
  - `get_project(project_id) -> dict | None`, `list_projects() -> list[dict]`
  - `update_documents(project_id, document_ids) -> dict`
  - `save_version(project_id, study: dict, origin: str, author: str, expected_version: int | None) -> dict` — version = dernière+1 ; lève `VersionConflictError` si `expected_version` ≠ dernière ; met `status` à `draft_generated` (origin="generation") ou `draft_reviewed` (origin="manual_edit")
  - `latest_version(project_id) -> dict | None`
  - `validate(project_id, expected_version, validated_by) -> dict` — lève `ValidationBlockedError` si `check_references` non vide ; fige `validated_at`, `status="validated"`, origin `"validation"`
  - `mark_exported(project_id, fmt) -> None` (`status="exported"`, trace `{format, at}` dans `exports`)
  - Collections : `ebios_projects`, `ebios_workshop_versions`

- [ ] **Step 1: Écrire le fake Mongo et les tests qui échouent**

```python
# tests/ebios/conftest.py
"""Fake Mongo minimal : uniquement les opérations utilisées par EbiosRepository."""
import copy

import pytest


class FakeCollection:
    def __init__(self):
        self.docs = []

    @staticmethod
    def _match(doc, flt):
        return all(doc.get(k) == v for k, v in flt.items())

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    def find_one(self, flt, sort=None):
        found = [d for d in self.docs if self._match(d, flt)]
        if sort:
            key, direction = sort[0]
            found.sort(key=lambda d: d.get(key, 0), reverse=direction < 0)
        return copy.deepcopy(found[0]) if found else None

    def find(self, flt=None):
        return [copy.deepcopy(d) for d in self.docs if self._match(d, flt or {})]

    def update_one(self, flt, update):
        for d in self.docs:
            if self._match(d, flt):
                for k, v in update.get("$set", {}).items():
                    d[k] = copy.deepcopy(v)
                for k, v in update.get("$push", {}).items():
                    d.setdefault(k, []).append(copy.deepcopy(v))
                return


class FakeDB(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = FakeCollection()
        return dict.__getitem__(self, name)


@pytest.fixture()
def fake_db():
    return FakeDB()
```

```python
# tests/ebios/test_repository.py
import pytest

from core.ebios.repository import EbiosRepository, VersionConflictError, ValidationBlockedError
from tests.ebios.fixtures import make_study


@pytest.fixture()
def repo(fake_db):
    return EbiosRepository(db=fake_db)


def test_create_et_get_projet(repo):
    p = repo.create_project("Étude spatiale", ["doc-a.md"])
    assert p["project_id"] and p["status"] == "created"
    assert repo.get_project(p["project_id"])["name"] == "Étude spatiale"
    assert [x["name"] for x in repo.list_projects()] == ["Étude spatiale"]


def test_versionnement_incremental_et_statuts(repo):
    p = repo.create_project("E", ["doc-a.md"])
    v1 = repo.save_version(p["project_id"], make_study(), origin="generation",
                           author="pipeline", expected_version=None)
    assert v1["version"] == 1
    assert repo.get_project(p["project_id"])["status"] == "draft_generated"
    v2 = repo.save_version(p["project_id"], make_study(), origin="manual_edit",
                           author="ingénieur", expected_version=1)
    assert v2["version"] == 2 and v2["parent_version"] == 1
    assert repo.get_project(p["project_id"])["status"] == "draft_reviewed"
    assert repo.latest_version(p["project_id"])["version"] == 2


def test_conflit_optimiste(repo):
    p = repo.create_project("E", [])
    repo.save_version(p["project_id"], make_study(), "generation", "pipeline", None)
    with pytest.raises(VersionConflictError):
        repo.save_version(p["project_id"], make_study(), "manual_edit", "ingénieur",
                          expected_version=0)  # quelqu'un a déjà écrit v1


def test_validation_fige_et_trace(repo):
    p = repo.create_project("E", [])
    repo.save_version(p["project_id"], make_study(), "generation", "pipeline", None)
    v = repo.validate(p["project_id"], expected_version=1, validated_by="Laury")
    assert v["study"]["validated_at"] is not None
    assert v["origin"] == "validation" and v["version"] == 2
    assert repo.get_project(p["project_id"])["status"] == "validated"


def test_validation_bloquee_si_references_cassees(repo):
    p = repo.create_project("E", [])
    bad = make_study()
    bad["atelier_1"]["evenements_redoutes"][0]["valeur_metier_id"] = "vm-999"
    repo.save_version(p["project_id"], bad, "generation", "pipeline", None)
    with pytest.raises(ValidationBlockedError):
        repo.validate(p["project_id"], expected_version=1, validated_by="Laury")


def test_mark_exported(repo):
    p = repo.create_project("E", [])
    repo.save_version(p["project_id"], make_study(), "generation", "pipeline", None)
    repo.validate(p["project_id"], 1, "Laury")
    repo.mark_exported(p["project_id"], "xlsx")
    proj = repo.get_project(p["project_id"])
    assert proj["status"] == "exported" and proj["exports"][0]["format"] == "xlsx"
```

- [ ] **Step 2: Vérifier l'échec**

Run: `.venv/bin/python -m pytest tests/ebios/test_repository.py -q`
Expected: FAIL — `ModuleNotFoundError` sur `core.ebios.repository`

- [ ] **Step 3: Implémenter le repository**

```python
# core/ebios/repository.py
"""Persistance Mongo des dossiers EBIOS : snapshots immuables + versionnement optimiste."""
from __future__ import annotations

import time
import uuid
from typing import Optional

from core.ebios.schemas import EbiosStudy, check_references

_PROJECTS = "ebios_projects"
_VERSIONS = "ebios_workshop_versions"
_ATELIER = "atelier_1"


class VersionConflictError(RuntimeError):
    """La version attendue n'est plus la dernière : ne pas écraser une correction."""


class ValidationBlockedError(ValueError):
    """Références internes invalides : la validation est refusée."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class EbiosRepository:
    def __init__(self, db=None):
        if db is None:
            from utils.mongo import get_db
            db = get_db()
        self._projects = db[_PROJECTS]
        self._versions = db[_VERSIONS]

    # -- projets -----------------------------------------------------------
    def create_project(self, name: str, document_ids: list[str]) -> dict:
        doc = {
            "project_id": uuid.uuid4().hex,
            "name": name,
            "document_ids": list(document_ids),
            "status": "created",
            "created_at": _now(),
            "exports": [],
        }
        self._projects.insert_one(dict(doc))
        return doc

    def get_project(self, project_id: str) -> Optional[dict]:
        doc = self._projects.find_one({"project_id": project_id})
        if doc:
            doc.pop("_id", None)
        return doc

    def list_projects(self) -> list[dict]:
        docs = list(self._projects.find({}))
        for d in docs:
            d.pop("_id", None)
        return sorted(docs, key=lambda d: d.get("created_at", ""), reverse=True)

    def update_documents(self, project_id: str, document_ids: list[str]) -> dict:
        self._projects.update_one({"project_id": project_id},
                                  {"$set": {"document_ids": list(document_ids)}})
        return self.get_project(project_id)

    # -- versions ----------------------------------------------------------
    def latest_version(self, project_id: str) -> Optional[dict]:
        doc = self._versions.find_one({"project_id": project_id, "atelier": _ATELIER},
                                      sort=[("version", -1)])
        if doc:
            doc.pop("_id", None)
        return doc

    def save_version(self, project_id: str, study: dict, origin: str, author: str,
                     expected_version: Optional[int], status: Optional[str] = None) -> dict:
        EbiosStudy.model_validate(study)  # snapshot toujours conforme au schéma
        latest = self.latest_version(project_id)
        current = latest["version"] if latest else 0
        if expected_version is not None and expected_version != current:
            raise VersionConflictError(
                f"Version attendue {expected_version}, dernière en base {current}"
            )
        doc = {
            "project_id": project_id,
            "atelier": _ATELIER,
            "version": current + 1,
            "parent_version": current or None,
            "origin": origin,
            "author": author,
            "created_at": _now(),
            "study": study,
        }
        self._versions.insert_one(dict(doc))
        new_status = status or {"generation": "draft_generated",
                                "manual_edit": "draft_reviewed"}.get(origin)
        if new_status:
            self._projects.update_one({"project_id": project_id},
                                      {"$set": {"status": new_status}})
        return doc

    def validate(self, project_id: str, expected_version: int, validated_by: str) -> dict:
        latest = self.latest_version(project_id)
        if not latest or latest["version"] != expected_version:
            raise VersionConflictError(
                f"Version attendue {expected_version}, dernière en base "
                f"{latest['version'] if latest else 0}"
            )
        study = dict(latest["study"])
        errs = check_references(EbiosStudy.model_validate(study))
        if errs:
            raise ValidationBlockedError(" ; ".join(errs))
        study["validated_at"] = _now()
        return self.save_version(project_id, study, origin="validation",
                                 author=validated_by, expected_version=expected_version,
                                 status="validated")

    def mark_exported(self, project_id: str, fmt: str) -> None:
        self._projects.update_one(
            {"project_id": project_id},
            {"$set": {"status": "exported"},
             "$push": {"exports": {"format": fmt, "at": _now()}}},
        )
```

- [ ] **Step 4: Vérifier le succès**

Run: `.venv/bin/python -m pytest tests/ebios -q`
Expected: 20 passed

- [ ] **Step 5: Commit**

```bash
git add core/ebios/repository.py tests/ebios/conftest.py tests/ebios/test_repository.py
git commit -m "feat(ebios): repository Mongo — snapshots immuables, versionnement optimiste, cycle de statuts"
```

---

### Task 4: API REST (projets, édition, validation, export)

**Files:**
- Create: `api/ebios.py`
- Modify: `api/main.py` (import + `app.include_router(ebios_router)`, même motif que les routeurs existants `api/main.py:36-46`)
- Test: `tests/ebios/test_api_ebios.py`

**Interfaces:**
- Consumes: `EbiosRepository`, `export_xlsx`, `bounded_control`, exceptions Task 2/3
- Produces (préfixe `/api/ebios`, conventions `api/documents.py` : chemins complets, imports core paresseux, `HTTPException`) :
  - `POST /api/ebios/projects` `{name, document_ids}` → projet
  - `GET /api/ebios/projects` → `{projects: [...]}`
  - `GET /api/ebios/projects/{pid}` → `{project, latest: version|null, warnings: [...]}`
  - `PUT /api/ebios/projects/{pid}/atelier-1` `{study, expected_version, author}` → version (409 si conflit)
  - `POST /api/ebios/projects/{pid}/atelier-1/validate` `{expected_version, validated_by}` → version (409 conflit, 422 références)
  - `POST /api/ebios/projects/{pid}/atelier-1/exports` `{format}` → fichier XLSX (`FileResponse`, nom contrôlé) ; 409 si non validé ou format docx/pdf (« indisponible : template non validé ») ; 422 si capacité dépassée
  - Helper module : `_repo()` (indirection monkeypatchable, import paresseux)

- [ ] **Step 1: Écrire les tests qui échouent**

```python
# tests/ebios/test_api_ebios.py
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.ebios as ebios_api
from core.ebios.repository import EbiosRepository
from tests.ebios.fixtures import make_study


@pytest.fixture()
def client(fake_db, monkeypatch):
    repo = EbiosRepository(db=fake_db)
    monkeypatch.setattr(ebios_api, "_repo", lambda: repo)
    app = FastAPI()
    app.include_router(ebios_api.router)
    return TestClient(app)


def _project(client, docs=("doc-a.md",)):
    r = client.post("/api/ebios/projects", json={"name": "Étude", "document_ids": list(docs)})
    assert r.status_code == 200
    return r.json()


def test_creation_et_listing(client):
    p = _project(client)
    assert client.get("/api/ebios/projects").json()["projects"][0]["project_id"] == p["project_id"]
    detail = client.get(f"/api/ebios/projects/{p['project_id']}").json()
    assert detail["latest"] is None and detail["project"]["status"] == "created"


def test_put_atelier_et_conflit(client):
    p = _project(client)
    r = client.put(f"/api/ebios/projects/{p['project_id']}/atelier-1",
                   json={"study": make_study(), "expected_version": 0, "author": "ingénieur"})
    assert r.status_code == 200 and r.json()["version"] == 1
    r2 = client.put(f"/api/ebios/projects/{p['project_id']}/atelier-1",
                    json={"study": make_study(), "expected_version": 0, "author": "ingénieur"})
    assert r2.status_code == 409


def test_get_expose_warnings_du_controle_borne(client):
    p = _project(client)
    data = make_study()
    data["atelier_1"]["valeurs_metier"][0]["preuves"] = []
    client.put(f"/api/ebios/projects/{p['project_id']}/atelier-1",
               json={"study": data, "expected_version": 0, "author": "ingénieur"})
    detail = client.get(f"/api/ebios/projects/{p['project_id']}").json()
    assert any("preuve" in w.lower() for w in detail["warnings"])


def test_validation_puis_export_xlsx(client, tmp_path):
    p = _project(client)
    client.put(f"/api/ebios/projects/{p['project_id']}/atelier-1",
               json={"study": make_study(), "expected_version": 0, "author": "ingénieur"})
    r = client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/validate",
                    json={"expected_version": 1, "validated_by": "Laury"})
    assert r.status_code == 200 and r.json()["version"] == 2
    r = client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/exports",
                    json={"format": "xlsx"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert "atelier1_v2" in r.headers["content-disposition"]
    assert r.content[:2] == b"PK"


def test_export_refuse_avant_validation(client):
    p = _project(client)
    client.put(f"/api/ebios/projects/{p['project_id']}/atelier-1",
               json={"study": make_study(), "expected_version": 0, "author": "ingénieur"})
    r = client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/exports",
                    json={"format": "xlsx"})
    assert r.status_code == 409


def test_docx_pdf_explicitement_indisponibles(client):
    p = _project(client)
    client.put(f"/api/ebios/projects/{p['project_id']}/atelier-1",
               json={"study": make_study(), "expected_version": 0, "author": "ingénieur"})
    client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/validate",
                json={"expected_version": 1, "validated_by": "Laury"})
    for fmt in ("docx", "pdf"):
        r = client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/exports",
                        json={"format": fmt})
        assert r.status_code == 409 and "indisponible" in r.json()["detail"].lower()


def test_projet_inconnu_404(client):
    assert client.get("/api/ebios/projects/nope").status_code == 404
```

- [ ] **Step 2: Vérifier l'échec**

Run: `.venv/bin/python -m pytest tests/ebios/test_api_ebios.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.ebios'`

- [ ] **Step 3: Implémenter le routeur**

```python
# api/ebios.py
"""Endpoints EBIOS RM — Atelier 1 : dossiers, brouillons versionnés, validation, exports."""
from __future__ import annotations

import re
import tempfile
import time
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

router = APIRouter()

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _repo():
    from core.ebios.repository import EbiosRepository
    return EbiosRepository()


class CreateProjectBody(BaseModel):
    name: str
    document_ids: List[str] = []


class PutAtelierBody(BaseModel):
    study: dict
    expected_version: int
    author: str = "ingénieur"


class ValidateBody(BaseModel):
    expected_version: int
    validated_by: str = "ingénieur"


class ExportBody(BaseModel):
    format: str  # xlsx | docx | pdf


def _get_project_or_404(repo, project_id: str) -> dict:
    proj = repo.get_project(project_id)
    if not proj:
        raise HTTPException(404, f"Dossier EBIOS inconnu : {project_id}")
    return proj


@router.post("/api/ebios/projects")
def create_project(body: CreateProjectBody):
    if not body.name.strip():
        raise HTTPException(400, "Nom de dossier requis")
    return _repo().create_project(body.name.strip(), body.document_ids)


@router.get("/api/ebios/projects")
def list_projects():
    return {"projects": _repo().list_projects()}


@router.get("/api/ebios/projects/{project_id}")
def get_project(project_id: str):
    from core.ebios.schemas import EbiosStudy, bounded_control
    repo = _repo()
    proj = _get_project_or_404(repo, project_id)
    latest = repo.latest_version(project_id)
    warnings: List[str] = []
    if latest:
        warnings = bounded_control(EbiosStudy.model_validate(latest["study"]))
    return {"project": proj, "latest": latest, "warnings": warnings}


@router.put("/api/ebios/projects/{project_id}/atelier-1")
def put_atelier(project_id: str, body: PutAtelierBody):
    from core.ebios.repository import VersionConflictError
    repo = _repo()
    _get_project_or_404(repo, project_id)
    try:
        return repo.save_version(project_id, body.study, origin="manual_edit",
                                 author=body.author, expected_version=body.expected_version)
    except VersionConflictError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:  # pydantic ValidationError incluse
        raise HTTPException(422, f"Étude invalide : {e}")


@router.post("/api/ebios/projects/{project_id}/atelier-1/validate")
def validate_atelier(project_id: str, body: ValidateBody):
    from core.ebios.repository import ValidationBlockedError, VersionConflictError
    repo = _repo()
    _get_project_or_404(repo, project_id)
    try:
        return repo.validate(project_id, body.expected_version, body.validated_by)
    except VersionConflictError as e:
        raise HTTPException(409, str(e))
    except ValidationBlockedError as e:
        raise HTTPException(422, f"Validation refusée : {e}")


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "ebios"


@router.post("/api/ebios/projects/{project_id}/atelier-1/exports")
def export_atelier(project_id: str, body: ExportBody):
    from core.ebios.exporters.xlsx import ExportCapacityError, TemplateIntegrityError, export_xlsx
    repo = _repo()
    proj = _get_project_or_404(repo, project_id)
    if body.format in ("docx", "pdf"):
        raise HTTPException(409, f"Export {body.format.upper()} indisponible en V1 : "
                                 "template non validé")
    if body.format != "xlsx":
        raise HTTPException(400, f"Format inconnu : {body.format}")
    latest = repo.latest_version(project_id)
    if not latest or not latest["study"].get("validated_at"):
        raise HTTPException(409, "Export refusé : l'Atelier 1 doit d'abord être validé")

    tmp_dir = Path(tempfile.mkdtemp(prefix="ebios_export_"))
    filename = (f"{_safe_name(proj['name'])}_atelier1_v{latest['version']}"
                f"_{time.strftime('%Y%m%d')}.xlsx")
    out = tmp_dir / filename
    try:
        export_xlsx(latest["study"], out)
    except ExportCapacityError as e:
        raise HTTPException(422, f"Capacité du classeur dépassée : {e}")
    except TemplateIntegrityError as e:
        raise HTTPException(500, str(e))
    repo.mark_exported(project_id, body.format)

    def _cleanup():
        try:
            out.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass

    return FileResponse(out, media_type=_XLSX_MIME, filename=filename,
                        background=BackgroundTask(_cleanup))
```

Dans `api/main.py`, à côté des imports de routeurs existants :

```python
from api.ebios import router as ebios_router
```

et à côté des `app.include_router(...)` :

```python
app.include_router(ebios_router)
```

- [ ] **Step 4: Vérifier le succès**

Run: `.venv/bin/python -m pytest tests/ebios -q`
Expected: 28 passed

- [ ] **Step 5: Vérifier que l'app se monte toujours**

Run: `.venv/bin/python -c "from api.main import app; print(len(app.routes))"`
Expected: un nombre, sans traceback

- [ ] **Step 6: Commit**

```bash
git add api/ebios.py api/main.py tests/ebios/test_api_ebios.py
git commit -m "feat(ebios): API dossiers/édition/validation/export — XLSX servi, DOCX/PDF explicitement indisponibles"
```

---

### Task 5: Pipeline LLM — prompts, extraction, preuves, consolidation

**Files:**
- Create: `core/ebios/prompts.py`, `core/ebios/evidence.py`, `core/ebios/atelier1.py`
- Test: `tests/ebios/test_atelier1_pipeline.py`

**Interfaces:**
- Consumes: `build_llm("extract")` / `build_llm("synthesize")` (`core/model_router.py:149`), `OllamaClient.invoke(prompt, format="json")` (`core/llm_client.py:66`), registre `core/prompt_registry.py:get_prompt(key, default)`, chunks Mongo `{"source": name}` (champs `content`, `page_number`)
- Produces:
  - `prompts.py` : `EXTRACT_VM_PROMPT`, `EXTRACT_ER_PROMPT`, `EXTRACT_GAP_PROMPT` (défauts, enregistrés sous `ebios.extract.vm|er|gap` via `get_prompt`)
  - `evidence.py` : `resolve_preuves(extraits: list[str], document_id: str, chunks: list[dict]) -> tuple[list[dict], list[str]]` — page par recherche du verbatim dans les chunks ; introuvable ⇒ preuve `page=None` + warning « preuve non localisée » ; **jamais d'invention**
  - `atelier1.py` : `generate_atelier1(project_id: str, document_ids: list[str], llm=None, gather=None, progress=None) -> dict` (étude conforme `EbiosStudy`) ; `llm(prompt, format)` injectable (défaut `build_llm("extract").invoke`), `gather(doc_id) -> list[chunk]` injectable (défaut Mongo), `progress(stage: str, detail: dict)` rappelé à chaque étape ; consolidation déterministe : ids `vm-N`/`bs-N`/`er-N`/`gap-N`, dédup par dénomination normalisée (casefold/strip, fusion des preuves), résolution `valeur_metier` par dénomination (échec ⇒ `valeur_metier_id=""` + warning), gravité hors 1–4 ⇒ clamp **refusé** : entrée conservée avec gravité par défaut 1 + warning explicite ; warnings finaux = consolidation + `bounded_control`

- [ ] **Step 1: Écrire les tests qui échouent**

```python
# tests/ebios/test_atelier1_pipeline.py
import json

from core.ebios.atelier1 import generate_atelier1
from core.ebios.evidence import resolve_preuves
from core.ebios.schemas import EbiosStudy

_CHUNKS = [
    {"content": "Le contrôle mission assure le pilotage.", "page_number": 4},
    {"content": "La perte du contrôle interrompt la mission.", "page_number": 9},
]

_VM_JSON = {
    "mission": "Surveillance spatiale",
    "valeurs_metier": [{"denomination": "Contrôle mission", "nature": "processus",
                        "description": "Pilotage.", "responsable_nom": "Dupont",
                        "responsable_type": "INTERNE",
                        "extraits": ["Le contrôle mission assure le pilotage."]}],
    "biens_supports": [{"denomination": "Segment sol", "description": "Stations.",
                        "responsable_nom": "Martin", "responsable_type": "EXTERNE",
                        "valeurs_metier": ["Contrôle mission"],
                        "extraits": ["Le contrôle mission assure le pilotage."]}],
}
_ER_JSON = {"evenements_redoutes": [
    {"valeur_metier": "Contrôle mission", "evenement": "Perte du contrôle",
     "impact": "Interruption", "gravite": 3, "justification_gravite": "Arrêt",
     "extraits": ["La perte du contrôle interrompt la mission."]},
    {"valeur_metier": "Inconnue", "evenement": "Événement orphelin",
     "impact": "x", "gravite": 9, "justification_gravite": "", "extraits": []},
]}
_GAP_JSON = {"socle_securite": [
    {"type_referentiel": "Norme", "nom_referentiel": "ISO 27001",
     "etat_application": "Appliqué",
     "ecarts": [{"description": "Pas de revue", "justification": "", "extraits": []}]},
]}


def fake_llm(prompt, format=None):
    assert format == "json"
    if "valeurs métier" in prompt.lower():
        return json.dumps(_VM_JSON, ensure_ascii=False)
    if "redout" in prompt.lower():
        return json.dumps(_ER_JSON, ensure_ascii=False)
    return json.dumps(_GAP_JSON, ensure_ascii=False)


def test_resolve_preuves_localise_la_page():
    preuves, warns = resolve_preuves(
        ["Le contrôle mission assure le pilotage.", "Texte jamais vu"], "doc-a.md", _CHUNKS)
    assert preuves[0]["page"] == 4 and preuves[0]["document_id"] == "doc-a.md"
    assert preuves[1]["page"] is None
    assert any("non localisée" in w for w in warns)


def test_generation_complete_et_sourcee():
    stages = []
    study = generate_atelier1("p-1", ["doc-a.md"], llm=fake_llm,
                              gather=lambda doc: _CHUNKS,
                              progress=lambda s, d: stages.append(s))
    model = EbiosStudy.model_validate(study)          # conforme au schéma
    assert model.project_id == "p-1"
    a1 = model.atelier_1
    assert a1.mission == "Surveillance spatiale"
    assert a1.valeurs_metier[0].id == "vm-1"
    assert a1.valeurs_metier[0].preuves[0].page == 4
    assert a1.biens_supports[0].valeurs_metier_associees == ["vm-1"]
    assert a1.evenements_redoutes[0].valeur_metier_id == "vm-1"
    assert {"extraction", "consolidation", "validation"} <= set(stages)


def test_entrees_douteuses_signalees_jamais_inventees():
    study = generate_atelier1("p-1", ["doc-a.md"], llm=fake_llm, gather=lambda d: _CHUNKS)
    er2 = study["atelier_1"]["evenements_redoutes"][1]
    assert er2["valeur_metier_id"] == ""              # VM inconnue -> non rattaché
    assert er2["gravite"] == 1                        # 9 hors échelle -> défaut + warning
    warns = " ".join(study["warnings"]).lower()
    assert "gravité" in warns or "gravite" in warns
    assert "sans preuve" in warns


def test_dedup_multi_documents():
    study = generate_atelier1("p-1", ["doc-a.md", "doc-b.md"],
                              llm=fake_llm, gather=lambda d: _CHUNKS)
    denoms = [v["denomination"] for v in study["atelier_1"]["valeurs_metier"]]
    assert denoms == ["Contrôle mission"]             # dédupliqué entre les 2 docs
    assert len(study["atelier_1"]["valeurs_metier"][0]["preuves"]) >= 1


def test_json_illisible_isole_pas_de_crash():
    def broken_llm(prompt, format=None):
        if "redout" in prompt.lower():
            return "pas du json"
        return fake_llm(prompt, format=format)
    study = generate_atelier1("p-1", ["doc-a.md"], llm=broken_llm, gather=lambda d: _CHUNKS)
    assert study["atelier_1"]["evenements_redoutes"] == []
    assert any("doc-a.md" in w and "redout" in w.lower() for w in study["warnings"])
```

- [ ] **Step 2: Vérifier l'échec**

Run: `.venv/bin/python -m pytest tests/ebios/test_atelier1_pipeline.py -q`
Expected: FAIL — imports manquants

- [ ] **Step 3: Implémenter prompts, preuves, pipeline**

```python
# core/ebios/prompts.py
"""Prompts d'extraction EBIOS RM Atelier 1 (défauts, surchargés via le registre versionné)."""

EXTRACT_VM_PROMPT = """Tu analyses un document pour l'Atelier 1 d'EBIOS Risk Manager.
Identifie la mission de l'organisation, ses valeurs métier (processus ou informations
essentiels) et les biens supports associés.

Règles strictes :
- N'invente rien : chaque élément doit être appuyé par des extraits VERBATIM du texte.
- "extraits" : liste de citations exactes copiées du texte (30 à 200 caractères).
- "nature" vaut exactement "processus" ou "information".
- "responsable_type" vaut exactement "INTERNE" ou "EXTERNE" ; si inconnu, mets
  responsable_nom "" et responsable_type "INTERNE".
- Si le texte ne contient rien de pertinent, renvoie des listes vides.

Réponds UNIQUEMENT avec ce JSON :
{{"mission": "...", "valeurs_metier": [{{"denomination": "...", "nature": "processus",
"description": "...", "responsable_nom": "...", "responsable_type": "INTERNE",
"extraits": ["..."]}}], "biens_supports": [{{"denomination": "...", "description": "...",
"responsable_nom": "...", "responsable_type": "EXTERNE",
"valeurs_metier": ["dénomination"], "extraits": ["..."]}}]}}

TEXTE :
{text}
"""

EXTRACT_ER_PROMPT = """Tu analyses un document pour l'Atelier 1 d'EBIOS Risk Manager.
Identifie les événements redoutés : atteintes aux valeurs métier, leur impact et leur
gravité sur l'échelle 1=Mineure, 2=Significative, 3=Grave, 4=Critique.

Règles strictes :
- N'invente rien : "extraits" contient des citations exactes du texte.
- "valeur_metier" reprend la dénomination de la valeur métier concernée.
- "gravite" est un entier de 1 à 4, justifié dans "justification_gravite".
- Si rien de pertinent, renvoie une liste vide.

Réponds UNIQUEMENT avec ce JSON :
{{"evenements_redoutes": [{{"valeur_metier": "...", "evenement": "...", "impact": "...",
"gravite": 3, "justification_gravite": "...", "extraits": ["..."]}}]}}

TEXTE :
{text}
"""

EXTRACT_GAP_PROMPT = """Tu analyses un document pour l'Atelier 1 d'EBIOS Risk Manager
(socle de sécurité). Identifie les référentiels de sécurité applicables, leur état
d'application et les écarts constatés.

Règles strictes :
- N'invente rien : "extraits" contient des citations exactes du texte.
- "etat_application" vaut exactement "Appliqué", "Partiellement appliqué" ou "Non appliqué".
- Un référentiel peut porter plusieurs écarts, chacun justifié.
- Si rien de pertinent, renvoie une liste vide.

Réponds UNIQUEMENT avec ce JSON :
{{"socle_securite": [{{"type_referentiel": "...", "nom_referentiel": "...",
"etat_application": "Appliqué", "ecarts": [{{"description": "...",
"justification": "...", "extraits": ["..."]}}]}}]}}

TEXTE :
{text}
"""
```

```python
# core/ebios/evidence.py
"""Rattachement des extraits verbatim aux documents/pages. Aucune invention."""
from __future__ import annotations


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def resolve_preuves(extraits: list[str], document_id: str,
                    chunks: list[dict]) -> tuple[list[dict], list[str]]:
    """Localise chaque extrait dans les chunks du document (page réelle ou None)."""
    preuves: list[dict] = []
    warnings: list[str] = []
    for extrait in extraits:
        ext = (extrait or "").strip()
        if not ext:
            continue
        page = None
        needle = _norm(ext)
        for chunk in chunks:
            if needle and needle in _norm(chunk.get("content", "")):
                page = chunk.get("page_number")
                break
        if page is None:
            warnings.append(f"Preuve non localisée dans {document_id} : « {ext[:60]} »")
        preuves.append({"document_id": document_id, "source": document_id,
                        "page": page, "extrait": ext})
    return preuves, warnings
```

```python
# core/ebios/atelier1.py
"""Génération du brouillon Atelier 1 : extraction par document, consolidation
déterministe, contrôle borné. Jamais de régénération automatique."""
from __future__ import annotations

import json
import time
from typing import Callable, Optional

from core.ebios.evidence import resolve_preuves
from core.ebios.prompts import EXTRACT_ER_PROMPT, EXTRACT_GAP_PROMPT, EXTRACT_VM_PROMPT
from core.ebios.schemas import EbiosStudy, GRAVITE_LABELS, bounded_control

_MAX_CHARS = 24000  # matériau max par appel d'extraction

_DOMAINS = (
    ("vm", "ebios.extract.vm", EXTRACT_VM_PROMPT),
    ("er", "ebios.extract.er", EXTRACT_ER_PROMPT),
    ("gap", "ebios.extract.gap", EXTRACT_GAP_PROMPT),
)


def _default_llm(prompt: str, format: Optional[str] = None) -> str:
    from core.model_router import build_llm
    return build_llm("extract").invoke(prompt, format=format)


def _default_gather(document_id: str) -> list[dict]:
    from utils.mongo import get_db
    cur = get_db()["chunks"].find(
        {"source": document_id},
        {"content": 1, "page_number": 1, "section_idx": 1, "chunk_idx": 1},
    ).sort([("section_idx", 1), ("chunk_idx", 1)])
    return list(cur)


def _parse_json(raw: str) -> Optional[dict]:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    start, end = raw.find("{"), raw.rfind("}") + 1
    if 0 <= start < end:
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            return None
    return None


def _norm(s: str) -> str:
    return " ".join((s or "").split()).casefold()


def generate_atelier1(project_id: str, document_ids: list[str],
                      llm: Optional[Callable] = None,
                      gather: Optional[Callable] = None,
                      progress: Optional[Callable] = None) -> dict:
    from core.prompt_registry import get_prompt

    llm = llm or _default_llm
    gather = gather or _default_gather
    notify = progress or (lambda stage, detail=None: None)

    raw_results: dict[str, list[tuple[str, dict, list[dict]]]] = {"vm": [], "er": [], "gap": []}
    warnings: list[str] = []

    for doc in document_ids:
        chunks = gather(doc)
        text = "\n\n".join(c.get("content", "") for c in chunks)[:_MAX_CHARS]
        if not text.strip():
            warnings.append(f"Document sans contenu exploitable : {doc}")
            continue
        for key, registry_key, default_prompt in _DOMAINS:
            notify("extraction", {"document": doc, "domaine": key})
            prompt = get_prompt(registry_key, default_prompt).format(text=text)
            parsed = _parse_json(llm(prompt, format="json"))
            if parsed is None:
                label = {"vm": "valeurs métier", "er": "événements redoutés",
                         "gap": "socle de sécurité"}[key]
                warnings.append(f"Extraction illisible ({label}) pour {doc} — ignorée")
                continue
            raw_results[key].append((doc, parsed, chunks))

    notify("consolidation", {})
    a1, cons_warnings = _consolidate(raw_results)
    warnings.extend(cons_warnings)

    study = {
        "schema_version": "1.0",
        "project_id": project_id,
        "source_document_ids": list(document_ids),
        "atelier_1": a1,
        "warnings": [],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "validated_at": None,
    }
    notify("validation", {})
    model = EbiosStudy.model_validate(study)
    study["warnings"] = warnings + bounded_control(model)
    return study


def _mk_resp(item: dict) -> dict:
    rtype = item.get("responsable_type", "INTERNE")
    if rtype not in ("INTERNE", "EXTERNE"):
        rtype = "INTERNE"
    return {"nom": item.get("responsable_nom", "") or "", "type": rtype}


def _merge(bucket: dict, key: str, entry: dict) -> dict:
    """Dédup déterministe par clé normalisée ; fusion des preuves sans doublon."""
    if key in bucket:
        seen = {(p["document_id"], p["extrait"]) for p in bucket[key]["preuves"]}
        for p in entry["preuves"]:
            if (p["document_id"], p["extrait"]) not in seen:
                bucket[key]["preuves"].append(p)
        return bucket[key]
    bucket[key] = entry
    return entry


def _consolidate(raw: dict) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    mission = ""
    vms: dict[str, dict] = {}
    bss: dict[str, dict] = {}
    ers: list[dict] = []
    gaps: dict[str, dict] = {}

    for doc, parsed, chunks in raw["vm"]:
        if not mission and parsed.get("mission"):
            mission = str(parsed["mission"]).strip()
        for item in parsed.get("valeurs_metier", []) or []:
            denom = str(item.get("denomination", "")).strip()
            if not denom:
                continue
            preuves, w = resolve_preuves(item.get("extraits", []) or [], doc, chunks)
            warnings.extend(w)
            nature = item.get("nature", "processus")
            if nature not in ("processus", "information"):
                warnings.append(f"Nature invalide « {nature} » pour « {denom} » — "
                                "remplacée par processus")
                nature = "processus"
            _merge(vms, _norm(denom), {
                "id": "", "denomination": denom, "nature": nature,
                "description": str(item.get("description", "")),
                "responsable": _mk_resp(item), "preuves": preuves,
            })
        for item in parsed.get("biens_supports", []) or []:
            denom = str(item.get("denomination", "")).strip()
            if not denom:
                continue
            preuves, w = resolve_preuves(item.get("extraits", []) or [], doc, chunks)
            warnings.extend(w)
            entry = _merge(bss, _norm(denom), {
                "id": "", "denomination": denom,
                "description": str(item.get("description", "")),
                "responsable": _mk_resp(item),
                "valeurs_metier_associees": [], "preuves": preuves,
                "_vm_refs": [],
            })
            entry["_vm_refs"].extend(str(v) for v in item.get("valeurs_metier", []) or [])

    for i, vm in enumerate(vms.values(), 1):
        vm["id"] = f"vm-{i}"
    vm_by_name = {_norm(v["denomination"]): v["id"] for v in vms.values()}

    for i, bs in enumerate(bss.values(), 1):
        bs["id"] = f"bs-{i}"
        refs = []
        for name in bs.pop("_vm_refs"):
            vm_id = vm_by_name.get(_norm(name))
            if vm_id and vm_id not in refs:
                refs.append(vm_id)
            elif not vm_id:
                warnings.append(f"Bien support « {bs['denomination']} » : valeur métier "
                                f"« {name} » non reconnue — association ignorée")
        bs["valeurs_metier_associees"] = refs

    seen_er: set = set()
    for doc, parsed, chunks in raw["er"]:
        for item in parsed.get("evenements_redoutes", []) or []:
            evenement = str(item.get("evenement", "")).strip()
            if not evenement:
                continue
            key = (_norm(item.get("valeur_metier", "")), _norm(evenement))
            if key in seen_er:
                continue
            seen_er.add(key)
            preuves, w = resolve_preuves(item.get("extraits", []) or [], doc, chunks)
            warnings.extend(w)
            vm_id = vm_by_name.get(_norm(str(item.get("valeur_metier", ""))), "")
            if not vm_id:
                warnings.append(f"ER « {evenement} » : valeur métier "
                                f"« {item.get('valeur_metier', '')} » non reconnue")
            gravite = item.get("gravite")
            if not isinstance(gravite, int) or not 1 <= gravite <= 4:
                warnings.append(f"ER « {evenement} » : gravité invalide « {gravite} » — "
                                f"ramenée à 1 ({GRAVITE_LABELS[1]}), à corriger")
                gravite = 1
            ers.append({
                "id": f"er-{len(ers) + 1}", "valeur_metier_id": vm_id,
                "evenement": evenement, "impact": str(item.get("impact", "")),
                "gravite": gravite,
                "justification_gravite": str(item.get("justification_gravite", "")),
                "preuves": preuves,
            })

    for doc, parsed, chunks in raw["gap"]:
        for item in parsed.get("socle_securite", []) or []:
            nom = str(item.get("nom_referentiel", "")).strip()
            if not nom:
                continue
            etat = item.get("etat_application", "Non appliqué")
            if etat not in ("Appliqué", "Partiellement appliqué", "Non appliqué"):
                warnings.append(f"Référentiel « {nom} » : état invalide « {etat} » — "
                                "remplacé par Non appliqué")
                etat = "Non appliqué"
            ecarts = []
            for ec in item.get("ecarts", []) or []:
                desc = str(ec.get("description", "")).strip()
                if not desc:
                    continue
                preuves, w = resolve_preuves(ec.get("extraits", []) or [], doc, chunks)
                warnings.extend(w)
                ecarts.append({"description": desc,
                               "justification": str(ec.get("justification", "")),
                               "preuves": preuves})
            existing = gaps.get(_norm(nom))
            if existing:
                seen_desc = {_norm(e["description"]) for e in existing["ecarts"]}
                existing["ecarts"].extend(e for e in ecarts
                                          if _norm(e["description"]) not in seen_desc)
            else:
                gaps[_norm(nom)] = {"id": "", "type_referentiel":
                                    str(item.get("type_referentiel", "")),
                                    "nom_referentiel": nom, "etat_application": etat,
                                    "ecarts": ecarts}
    for i, gap in enumerate(gaps.values(), 1):
        gap["id"] = f"gap-{i}"

    a1 = {"mission": mission, "valeurs_metier": list(vms.values()),
          "biens_supports": list(bss.values()), "evenements_redoutes": ers,
          "socle_securite": list(gaps.values())}
    return a1, warnings
```

- [ ] **Step 4: Vérifier le succès**

Run: `.venv/bin/python -m pytest tests/ebios -q`
Expected: 33 passed

- [ ] **Step 5: Commit**

```bash
git add core/ebios/prompts.py core/ebios/evidence.py core/ebios/atelier1.py tests/ebios/test_atelier1_pipeline.py
git commit -m "feat(ebios): pipeline Atelier 1 — extraction JSON sourcée, consolidation déterministe, warnings sans invention"
```

---

### Task 6: Endpoint de génération SSE

**Files:**
- Modify: `api/ebios.py` (ajout endpoint)
- Test: ajout dans `tests/ebios/test_api_ebios.py`

**Interfaces:**
- Consumes: `generate_atelier1` (indirection `_generate` monkeypatchable), `_sse` (`api/common.py:30`), pattern `StreamingResponse` de `api/rag.py:402-403`
- Produces: `POST /api/ebios/projects/{pid}/atelier-1/generate` — SSE `text/event-stream`, événements `{"type": "stage", "stage": "extraction"|"consolidation"|"validation", ...}` puis `{"type": "draft", "version": n, "study": {...}, "warnings": [...]}` puis `{"type": "done"}` ; erreur ⇒ `{"type": "error", "message": ...}` ; persiste la version `origin="generation"`, `author="pipeline"`

- [ ] **Step 1: Ajouter les tests qui échouent** (dans `tests/ebios/test_api_ebios.py`)

```python
def _events(text):
    import json as _json
    out = []
    for line in text.splitlines():
        if line.startswith("data: "):
            out.append(_json.loads(line[len("data: "):]))
    return out


def test_generate_sse_stream_et_persistance(client, monkeypatch):
    from tests.ebios.fixtures import make_study as _mk

    def fake_generate(project_id, document_ids, progress=None, **kw):
        progress("extraction", {"document": document_ids[0], "domaine": "vm"})
        progress("consolidation", {})
        progress("validation", {})
        return _mk(project_id=project_id)

    monkeypatch.setattr(ebios_api, "_generate", lambda: fake_generate)
    p = _project(client)
    r = client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/generate")
    assert r.status_code == 200
    events = _events(r.text)
    types = [e["type"] for e in events]
    assert types.count("stage") == 3 and "draft" in types and types[-1] == "done"
    draft = next(e for e in events if e["type"] == "draft")
    assert draft["version"] == 1
    detail = client.get(f"/api/ebios/projects/{p['project_id']}").json()
    assert detail["project"]["status"] == "draft_generated"
    assert detail["latest"]["origin"] == "generation"


def test_generate_sans_document_erreur(client, monkeypatch):
    monkeypatch.setattr(ebios_api, "_generate", lambda: (lambda *a, **k: {}))
    p = _project(client, docs=())
    r = client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/generate")
    events = _events(r.text)
    assert events[-1]["type"] == "error"
```

- [ ] **Step 2: Vérifier l'échec**

Run: `.venv/bin/python -m pytest tests/ebios/test_api_ebios.py -q`
Expected: FAIL — `AttributeError: ... '_generate'`

- [ ] **Step 3: Implémenter l'endpoint** (dans `api/ebios.py`)

```python
# imports additionnels en tête de fichier :
import json
import queue
import threading

from fastapi.responses import StreamingResponse

from api.common import _sse


def _generate():
    from core.ebios.atelier1 import generate_atelier1
    return generate_atelier1


@router.post("/api/ebios/projects/{project_id}/atelier-1/generate")
def generate(project_id: str):
    repo = _repo()
    proj = _get_project_or_404(repo, project_id)
    generate_fn = _generate()

    def gen():
        docs = proj.get("document_ids") or []
        if not docs:
            yield _sse({"type": "error", "message": "Aucun document sélectionné"})
            return
        events: "queue.Queue" = queue.Queue()
        result: dict = {}

        def worker():
            try:
                result["study"] = generate_fn(
                    project_id, docs,
                    progress=lambda stage, detail=None: events.put(
                        {"type": "stage", "stage": stage, **(detail or {})}),
                )
            except Exception as e:  # noqa: BLE001 — remonté au flux SSE
                result["error"] = str(e)
            finally:
                events.put(None)

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        while True:
            ev = events.get()
            if ev is None:
                break
            yield _sse(ev)
        t.join()
        if "error" in result:
            yield _sse({"type": "error", "message": result["error"]})
            return
        try:
            version = repo.save_version(project_id, result["study"], origin="generation",
                                        author="pipeline", expected_version=None)
        except ValueError as e:
            yield _sse({"type": "error", "message": f"Brouillon invalide : {e}"})
            return
        yield _sse({"type": "draft", "version": version["version"],
                    "study": result["study"],
                    "warnings": result["study"].get("warnings", [])})
        yield _sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})
```

- [ ] **Step 4: Vérifier le succès**

Run: `.venv/bin/python -m pytest tests/ebios -q`
Expected: 35 passed

- [ ] **Step 5: Commit**

```bash
git add api/ebios.py tests/ebios/test_api_ebios.py
git commit -m "feat(ebios): génération Atelier 1 en SSE — progression extraction/consolidation/validation, brouillon persisté"
```

---

### Task 6b: Endpoint de mise à jour de la sélection de documents (exigence scale, ajoutée 2026-07-27)

> Exigence utilisateur mi-session : pouvoir sélectionner tous les documents ingérés, ajouter un
> document ingéré plus tard à un dossier existant, puis re-générer l'Atelier 1 (les versions
> s'empilent, rien n'est perdu). Le repository a déjà `update_documents` ; il manque l'exposition API.

**Files:**
- Modify: `api/ebios.py`
- Test: ajout dans `tests/ebios/test_api_ebios.py`

**Interfaces:**
- Produces: `PUT /api/ebios/projects/{project_id}/documents` body `{"document_ids": [...]}` → projet mis à jour ; 404 si projet inconnu.

```python
class PutDocumentsBody(BaseModel):
    document_ids: List[str]


@router.put("/api/ebios/projects/{project_id}/documents")
def put_documents(project_id: str, body: PutDocumentsBody):
    repo = _repo()
    _get_project_or_404(repo, project_id)
    return repo.update_documents(project_id, body.document_ids)
```

Tests : `test_put_documents_puis_regeneration` — créer un projet avec 1 doc, PUT 2 docs,
vérifier que GET reflète la nouvelle liste et qu'un `generate` (fake `_generate`) reçoit
bien les 2 documents ; `test_put_documents_projet_inconnu_404`.
Commit : `feat(ebios): sélection de documents modifiable — re-génération multi-docs (exigence scale)`

---

### Task 7: UI — onglet EBIOS RM, sélection documents, génération, tables éditables, validation, export

> Amendements scale (2026-07-27) : bouton « Tout sélectionner / Tout désélectionner » sur la liste
> des documents ; `web/src/lib/ebios.ts` expose `updateDocuments(id, document_ids)` (PUT
> `/documents`) ; `onGenerate` persiste la sélection courante via `updateDocuments` AVANT d'appeler
> `streamGenerate`, pour que la re-génération porte toujours sur la sélection affichée.

**Files:**
- Create: `web/src/lib/ebios.ts` (types + appels API + flux SSE)
- Create: `web/src/components/ebios/ebios-view.tsx` (orchestration)
- Create: `web/src/components/ebios/atelier1-tables.tsx` (3 tables éditables + preuves + warnings)
- Create: `web/src/app/ebios-rm/page.tsx`
- Modify: `web/src/components/header-nav.tsx:13-17` (ajout dans `RAG_LINKS`)

**Interfaces:**
- Consumes: `getJSON`/`API_BASE` (`web/src/lib/api.ts`), kit `Panel/PanelTitle/Pill/Banner/Hint/Spinner` (`web/src/components/ui.tsx`), patron SSE `web/src/lib/sse.ts`, `GET /api/sources`, endpoints Task 4/6
- Produces: route `/ebios-rm` ; navigation prête pour les Ateliers 2–5 (pills désactivées « à venir ») ; sélecteur d'export XLSX actif, DOCX/PDF désactivés avec raison

Principes UX (mémoire projet) : glass box scannable, épuré, pas d'emoji décoratif, bulles d'aide `Hint`. Chaque entrée affiche ses sources (document, page, extrait) et les warnings sont visibles en permanence.

- [ ] **Step 1: Écrire `web/src/lib/ebios.ts`**

```typescript
// web/src/lib/ebios.ts — types et accès API du module EBIOS RM
import { API_BASE, getJSON } from "@/lib/api";

export type Preuve = { document_id: string; source: string; page: number | null; extrait: string };
export type Responsable = { nom: string; type: "INTERNE" | "EXTERNE" };
export type ValeurMetier = {
  id: string; denomination: string; nature: "processus" | "information";
  description: string; responsable: Responsable; preuves: Preuve[];
};
export type BienSupport = {
  id: string; denomination: string; description: string; responsable: Responsable;
  valeurs_metier_associees: string[]; preuves: Preuve[];
};
export type EvenementRedoute = {
  id: string; valeur_metier_id: string; evenement: string; impact: string;
  gravite: number; justification_gravite: string; preuves: Preuve[];
};
export type Ecart = { description: string; justification: string; preuves: Preuve[] };
export type SocleSecurite = {
  id: string; type_referentiel: string; nom_referentiel: string;
  etat_application: string; ecarts: Ecart[];
};
export type Atelier1 = {
  mission: string; valeurs_metier: ValeurMetier[]; biens_supports: BienSupport[];
  evenements_redoutes: EvenementRedoute[]; socle_securite: SocleSecurite[];
};
export type Study = {
  schema_version: string; project_id: string; source_document_ids: string[];
  atelier_1: Atelier1; warnings: string[]; generated_at: string; validated_at: string | null;
};
export type EbiosProject = {
  project_id: string; name: string; document_ids: string[]; status: string;
  created_at: string; exports: { format: string; at: string }[];
};
export type VersionDoc = {
  version: number; origin: string; author: string; created_at: string; study: Study;
};
export type GenerateEvent =
  | { type: "stage"; stage: string; document?: string; domaine?: string }
  | { type: "draft"; version: number; study: Study; warnings: string[] }
  | { type: "done" } | { type: "error"; message: string };

export const GRAVITE_LABELS: Record<number, string> = {
  1: "Mineure", 2: "Significative", 3: "Grave", 4: "Critique",
};
export const ETATS_APPLICATION = ["Appliqué", "Partiellement appliqué", "Non appliqué"];

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export const listProjects = () =>
  getJSON<{ projects: EbiosProject[] }>("/api/ebios/projects");
export const createProject = (name: string, document_ids: string[]) =>
  send<EbiosProject>("/api/ebios/projects", "POST", { name, document_ids });
export const getProject = (id: string) =>
  getJSON<{ project: EbiosProject; latest: VersionDoc | null; warnings: string[] }>(
    `/api/ebios/projects/${id}`);
export const putAtelier = (id: string, study: Study, expected_version: number) =>
  send<VersionDoc>(`/api/ebios/projects/${id}/atelier-1`, "PUT",
    { study, expected_version, author: "ingénieur" });
export const validateAtelier = (id: string, expected_version: number, validated_by: string) =>
  send<VersionDoc>(`/api/ebios/projects/${id}/atelier-1/validate`, "POST",
    { expected_version, validated_by });

export async function exportAtelier(id: string, format: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/ebios/projects/${id}/atelier-1/exports`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const disposition = res.headers.get("content-disposition") ?? "";
  const name = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? `ebios_atelier1.${format}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

// Même patron que web/src/lib/sse.ts (POST + ReadableStream, EventSource ne fait pas de POST)
export async function streamGenerate(
  id: string, onEvent: (ev: GenerateEvent) => void, signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/ebios/projects/${id}/atelier-1/generate`, {
    method: "POST", signal,
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      for (const line of part.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        try { onEvent(JSON.parse(line.slice(6)) as GenerateEvent); } catch { /* ignore */ }
      }
    }
  }
}
```

- [ ] **Step 2: Écrire `web/src/components/ebios/atelier1-tables.tsx`**

Tables contrôlées : chaque cellule est un `<input>`/`<textarea>`/`<select>` liés au brouillon ; `onChange` remonte une copie immuable via `onChange(next: Atelier1)`. Sous chaque ligne, les preuves (`source p.X « extrait »`) en `text-fg-muted`, et `Pill tone="warn"` « sans preuve » si `preuves.length === 0`.

```tsx
// web/src/components/ebios/atelier1-tables.tsx
"use client";

import { Hint, Panel, PanelTitle, Pill } from "@/components/ui";
import {
  Atelier1, ETATS_APPLICATION, GRAVITE_LABELS, Preuve,
} from "@/lib/ebios";

const cell = "w-full bg-transparent border border-edge rounded px-2 py-1 text-sm";

function Preuves({ preuves }: { preuves: Preuve[] }) {
  if (!preuves.length) return <Pill tone="warn">sans preuve</Pill>;
  return (
    <div className="mt-1 space-y-0.5">
      {preuves.map((p, i) => (
        <p key={i} className="text-xs text-fg-muted">
          {p.source}{p.page != null ? ` p.${p.page}` : " (page non localisée)"} — « {p.extrait} »
        </p>
      ))}
    </div>
  );
}

type Props = { a1: Atelier1; onChange: (next: Atelier1) => void; disabled: boolean };

export function Atelier1Tables({ a1, onChange, disabled }: Props) {
  const patch = (partial: Partial<Atelier1>) => onChange({ ...a1, ...partial });
  const vmName = (id: string) =>
    a1.valeurs_metier.find((v) => v.id === id)?.denomination ?? id;

  return (
    <div className="space-y-6">
      <Panel>
        <PanelTitle kicker="Atelier 1" title="Missions et valeurs métier"
          hint="Mission, valeurs métier (5 max) et biens supports (11 max) du classeur contractuel." />
        <label className="mt-2 block text-xs text-fg-muted">Mission</label>
        <textarea className={cell} rows={2} value={a1.mission} disabled={disabled}
          onChange={(e) => patch({ mission: e.target.value })} />
        <div className="mt-3 space-y-3">
          {a1.valeurs_metier.map((vm, i) => (
            <div key={vm.id} className="rounded border border-edge p-2">
              <div className="grid gap-2 md:grid-cols-4">
                <input className={cell} value={vm.denomination} disabled={disabled}
                  placeholder="Dénomination"
                  onChange={(e) => patch({ valeurs_metier: a1.valeurs_metier.map(
                    (v, j) => j === i ? { ...v, denomination: e.target.value } : v) })} />
                <select className={cell} value={vm.nature} disabled={disabled}
                  onChange={(e) => patch({ valeurs_metier: a1.valeurs_metier.map(
                    (v, j) => j === i
                      ? { ...v, nature: e.target.value as "processus" | "information" } : v) })}>
                  <option value="processus">processus</option>
                  <option value="information">information</option>
                </select>
                <input className={cell} value={vm.description} disabled={disabled}
                  placeholder="Description"
                  onChange={(e) => patch({ valeurs_metier: a1.valeurs_metier.map(
                    (v, j) => j === i ? { ...v, description: e.target.value } : v) })} />
                <div className="flex gap-1">
                  <input className={cell} value={vm.responsable.nom} disabled={disabled}
                    placeholder="Responsable"
                    onChange={(e) => patch({ valeurs_metier: a1.valeurs_metier.map(
                      (v, j) => j === i
                        ? { ...v, responsable: { ...v.responsable, nom: e.target.value } } : v) })} />
                  <select className={cell} value={vm.responsable.type} disabled={disabled}
                    onChange={(e) => patch({ valeurs_metier: a1.valeurs_metier.map(
                      (v, j) => j === i
                        ? { ...v, responsable: { ...v.responsable,
                            type: e.target.value as "INTERNE" | "EXTERNE" } } : v) })}>
                    <option value="INTERNE">INTERNE</option>
                    <option value="EXTERNE">EXTERNE</option>
                  </select>
                </div>
              </div>
              <Preuves preuves={vm.preuves} />
            </div>
          ))}
        </div>
        <Hint>Biens supports associés</Hint>
        <div className="mt-1 space-y-3">
          {a1.biens_supports.map((bs, i) => (
            <div key={bs.id} className="rounded border border-edge p-2">
              <div className="grid gap-2 md:grid-cols-4">
                <input className={cell} value={bs.denomination} disabled={disabled}
                  placeholder="Dénomination"
                  onChange={(e) => patch({ biens_supports: a1.biens_supports.map(
                    (b, j) => j === i ? { ...b, denomination: e.target.value } : b) })} />
                <input className={cell} value={bs.description} disabled={disabled}
                  placeholder="Description"
                  onChange={(e) => patch({ biens_supports: a1.biens_supports.map(
                    (b, j) => j === i ? { ...b, description: e.target.value } : b) })} />
                <div className="flex gap-1">
                  <input className={cell} value={bs.responsable.nom} disabled={disabled}
                    placeholder="Responsable"
                    onChange={(e) => patch({ biens_supports: a1.biens_supports.map(
                      (b, j) => j === i
                        ? { ...b, responsable: { ...b.responsable, nom: e.target.value } } : b) })} />
                  <select className={cell} value={bs.responsable.type} disabled={disabled}
                    onChange={(e) => patch({ biens_supports: a1.biens_supports.map(
                      (b, j) => j === i
                        ? { ...b, responsable: { ...b.responsable,
                            type: e.target.value as "INTERNE" | "EXTERNE" } } : b) })}>
                    <option value="INTERNE">INTERNE</option>
                    <option value="EXTERNE">EXTERNE</option>
                  </select>
                </div>
                <p className="self-center text-xs text-fg-muted">
                  VM : {bs.valeurs_metier_associees.map(vmName).join(", ") || "aucune"}
                </p>
              </div>
              <Preuves preuves={bs.preuves} />
            </div>
          ))}
        </div>
      </Panel>

      <Panel>
        <PanelTitle kicker="Atelier 1" title="Événements redoutés"
          hint="7 lignes maximum ; gravité sur l'échelle 1 Mineure → 4 Critique." />
        <div className="mt-2 space-y-3">
          {a1.evenements_redoutes.map((er, i) => (
            <div key={er.id} className="rounded border border-edge p-2">
              <div className="grid gap-2 md:grid-cols-4">
                <select className={cell} value={er.valeur_metier_id} disabled={disabled}
                  onChange={(e) => patch({ evenements_redoutes: a1.evenements_redoutes.map(
                    (x, j) => j === i ? { ...x, valeur_metier_id: e.target.value } : x) })}>
                  <option value="">— valeur métier —</option>
                  {a1.valeurs_metier.map((vm) => (
                    <option key={vm.id} value={vm.id}>{vm.denomination}</option>
                  ))}
                </select>
                <input className={cell} value={er.evenement} disabled={disabled}
                  placeholder="Événement redouté"
                  onChange={(e) => patch({ evenements_redoutes: a1.evenements_redoutes.map(
                    (x, j) => j === i ? { ...x, evenement: e.target.value } : x) })} />
                <input className={cell} value={er.impact} disabled={disabled}
                  placeholder="Impact"
                  onChange={(e) => patch({ evenements_redoutes: a1.evenements_redoutes.map(
                    (x, j) => j === i ? { ...x, impact: e.target.value } : x) })} />
                <select className={cell} value={er.gravite} disabled={disabled}
                  onChange={(e) => patch({ evenements_redoutes: a1.evenements_redoutes.map(
                    (x, j) => j === i ? { ...x, gravite: Number(e.target.value) } : x) })}>
                  {[1, 2, 3, 4].map((g) => (
                    <option key={g} value={g}>{g} — {GRAVITE_LABELS[g]}</option>
                  ))}
                </select>
              </div>
              <Preuves preuves={er.preuves} />
            </div>
          ))}
        </div>
      </Panel>

      <Panel>
        <PanelTitle kicker="Atelier 1" title="Socle de sécurité (GAP)"
          hint="3 référentiels maximum ; écarts par bloc : 2, 2 puis 1 (structure du classeur)." />
        <div className="mt-2 space-y-3">
          {a1.socle_securite.map((gap, i) => (
            <div key={gap.id} className="rounded border border-edge p-2">
              <div className="grid gap-2 md:grid-cols-3">
                <input className={cell} value={gap.type_referentiel} disabled={disabled}
                  placeholder="Type de référentiel"
                  onChange={(e) => patch({ socle_securite: a1.socle_securite.map(
                    (g, j) => j === i ? { ...g, type_referentiel: e.target.value } : g) })} />
                <input className={cell} value={gap.nom_referentiel} disabled={disabled}
                  placeholder="Nom du référentiel"
                  onChange={(e) => patch({ socle_securite: a1.socle_securite.map(
                    (g, j) => j === i ? { ...g, nom_referentiel: e.target.value } : g) })} />
                <select className={cell} value={gap.etat_application} disabled={disabled}
                  onChange={(e) => patch({ socle_securite: a1.socle_securite.map(
                    (g, j) => j === i ? { ...g, etat_application: e.target.value } : g) })}>
                  {ETATS_APPLICATION.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              {gap.ecarts.map((ec, k) => (
                <div key={k} className="mt-2 grid gap-2 md:grid-cols-2">
                  <input className={cell} value={ec.description} disabled={disabled}
                    placeholder="Écart"
                    onChange={(e) => patch({ socle_securite: a1.socle_securite.map(
                      (g, j) => j === i ? { ...g, ecarts: g.ecarts.map(
                        (x, m) => m === k ? { ...x, description: e.target.value } : x) } : g) })} />
                  <input className={cell} value={ec.justification} disabled={disabled}
                    placeholder="Justification de l'écart"
                    onChange={(e) => patch({ socle_securite: a1.socle_securite.map(
                      (g, j) => j === i ? { ...g, ecarts: g.ecarts.map(
                        (x, m) => m === k ? { ...x, justification: e.target.value } : x) } : g) })} />
                  <Preuves preuves={ec.preuves} />
                </div>
              ))}
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
```

- [ ] **Step 3: Écrire `web/src/components/ebios/ebios-view.tsx`**

```tsx
// web/src/components/ebios/ebios-view.tsx
"use client";

import { useCallback, useEffect, useState } from "react";

import { Atelier1Tables } from "@/components/ebios/atelier1-tables";
import { Banner, Hint, Panel, PanelTitle, Pill, Spinner } from "@/components/ui";
import { getJSON } from "@/lib/api";
import {
  Atelier1, EbiosProject, Study, VersionDoc,
  createProject, exportAtelier, getProject, listProjects, putAtelier,
  streamGenerate, validateAtelier,
} from "@/lib/ebios";

const STATUS_LABELS: Record<string, string> = {
  created: "créé", draft_generated: "brouillon généré", draft_reviewed: "brouillon relu",
  validated: "validé", exported: "exporté",
};
const ATELIERS_FUTURS = ["Atelier 2", "Atelier 3", "Atelier 4", "Atelier 5"];

export function EbiosView() {
  const [projects, setProjects] = useState<EbiosProject[]>([]);
  const [sources, setSources] = useState<string[]>([]);
  const [current, setCurrent] = useState<EbiosProject | null>(null);
  const [latest, setLatest] = useState<VersionDoc | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [draft, setDraft] = useState<Study | null>(null);
  const [dirty, setDirty] = useState(false);
  const [newName, setNewName] = useState("");
  const [selectedDocs, setSelectedDocs] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [stage, setStage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exportFmt, setExportFmt] = useState("xlsx");

  const refresh = useCallback(async (projectId?: string) => {
    const [pl, src] = await Promise.all([
      listProjects(),
      getJSON<{ sources: { name: string }[] }>("/api/sources").catch(() => ({ sources: [] })),
    ]);
    setProjects(pl.projects);
    setSources(src.sources.map((s) => s.name));
    if (projectId) {
      const detail = await getProject(projectId);
      setCurrent(detail.project);
      setLatest(detail.latest);
      setWarnings(detail.warnings);
      setDraft(detail.latest ? detail.latest.study : null);
      setSelectedDocs(detail.project.document_ids);
      setDirty(false);
    }
  }, []);

  useEffect(() => { refresh().catch((e) => setError(String(e))); }, [refresh]);

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setError(null);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); setStage(null); }
  };

  const onCreate = () => run("create", async () => {
    const p = await createProject(newName, selectedDocs);
    setNewName("");
    await refresh(p.project_id);
  });

  const onGenerate = () => run("generate", async () => {
    if (!current) return;
    await streamGenerate(current.project_id, (ev) => {
      if (ev.type === "stage") setStage(
        ev.stage + (ev.document ? ` — ${ev.document} (${ev.domaine})` : ""));
      if (ev.type === "draft") { setDraft(ev.study); setWarnings(ev.warnings); }
      if (ev.type === "error") setError(ev.message);
    });
    await refresh(current.project_id);
  });

  const onSave = () => run("save", async () => {
    if (!current || !draft) return;
    await putAtelier(current.project_id, draft, latest?.version ?? 0);
    await refresh(current.project_id);
  });

  const onValidate = () => run("validate", async () => {
    if (!current || !latest) return;
    await validateAtelier(current.project_id, latest.version, "ingénieur");
    await refresh(current.project_id);
  });

  const onExport = () => run("export", async () => {
    if (!current) return;
    await exportAtelier(current.project_id, exportFmt);
    await refresh(current.project_id);
  });

  const setA1 = (a1: Atelier1) => {
    if (!draft) return;
    setDraft({ ...draft, atelier_1: a1 });
    setDirty(true);
  };

  const validated = Boolean(draft?.validated_at);

  return (
    <div className="space-y-6">
      <Panel>
        <PanelTitle kicker="EBIOS RM" title="Dossiers"
          hint="Un dossier regroupe des documents ingérés et les versions successives de l'Atelier 1." />
        <div className="mt-2 flex flex-wrap gap-2">
          {projects.map((p) => (
            <button key={p.project_id}
              className={`rounded border px-3 py-1 text-sm ${
                current?.project_id === p.project_id
                  ? "border-accent-bright" : "border-edge"}`}
              onClick={() => refresh(p.project_id).catch((e) => setError(String(e)))}>
              {p.name} <Pill tone="neutral">{STATUS_LABELS[p.status] ?? p.status}</Pill>
            </button>
          ))}
        </div>
        <div className="mt-3 flex gap-2">
          <input className="flex-1 rounded border border-edge bg-transparent px-2 py-1 text-sm"
            placeholder="Nom du nouveau dossier" value={newName}
            onChange={(e) => setNewName(e.target.value)} />
          <button className="rounded border border-edge px-3 py-1 text-sm"
            disabled={!newName.trim() || busy !== null} onClick={onCreate}>
            Créer
          </button>
        </div>
      </Panel>

      {current && (
        <>
          <Panel>
            <PanelTitle kicker={current.name} title="Documents et génération"
              hint="Sélectionner les documents déjà ingérés puis générer le brouillon sourcé." />
            <div className="mt-2 grid gap-1 md:grid-cols-2">
              {sources.map((s) => (
                <label key={s} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={selectedDocs.includes(s)}
                    disabled={busy !== null}
                    onChange={(e) => setSelectedDocs(e.target.checked
                      ? [...selectedDocs, s] : selectedDocs.filter((x) => x !== s))} />
                  {s}
                </label>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-3">
              <button className="rounded border border-edge px-3 py-1 text-sm"
                disabled={busy !== null || selectedDocs.length === 0} onClick={onGenerate}>
                Générer l&apos;Atelier 1
              </button>
              {busy === "generate" && (
                <span className="flex items-center gap-2 text-sm text-fg-muted">
                  <Spinner /> {stage ?? "démarrage"}
                </span>
              )}
              <span className="ml-auto flex gap-1">
                <Pill tone="accent">Atelier 1</Pill>
                {ATELIERS_FUTURS.map((a) => (
                  <Pill key={a} tone="neutral">{a} — à venir</Pill>
                ))}
              </span>
            </div>
          </Panel>

          {error && <Banner tone="bad">{error}</Banner>}
          {warnings.length > 0 && (
            <Banner tone="warn">
              <p className="font-medium">Avertissements ({warnings.length})</p>
              <ul className="mt-1 list-disc pl-5 text-sm">
                {warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            </Banner>
          )}

          {draft && (
            <>
              <Atelier1Tables a1={draft.atelier_1} onChange={setA1} disabled={busy !== null} />
              <Panel>
                <PanelTitle kicker="Cycle" title="Sauvegarde, validation, export"
                  hint="La validation fige la version ; l'export n'est permis qu'après validation et ne déclenche jamais de génération." />
                <div className="mt-2 flex flex-wrap items-center gap-3">
                  <span className="text-sm text-fg-muted">
                    Version {latest?.version ?? 0} ({latest?.origin ?? "aucune"})
                    {dirty && <Pill tone="warn">modifications non sauvegardées</Pill>}
                    {validated && <Pill tone="good">validé</Pill>}
                  </span>
                  <button className="rounded border border-edge px-3 py-1 text-sm"
                    disabled={busy !== null || !dirty} onClick={onSave}>
                    Sauvegarder le brouillon
                  </button>
                  <button className="rounded border border-edge px-3 py-1 text-sm"
                    disabled={busy !== null || dirty || !latest || validated}
                    onClick={onValidate}>
                    Valider l&apos;Atelier 1
                  </button>
                  <span className="ml-auto flex items-center gap-2">
                    <select className="rounded border border-edge bg-transparent px-2 py-1 text-sm"
                      value={exportFmt} onChange={(e) => setExportFmt(e.target.value)}>
                      <option value="xlsx">XLSX</option>
                      <option value="docx" disabled>DOCX — indisponible (template non validé)</option>
                      <option value="pdf" disabled>PDF — indisponible (template non validé)</option>
                    </select>
                    <button className="rounded border border-edge px-3 py-1 text-sm"
                      disabled={busy !== null || !validated} onClick={onExport}>
                      Exporter
                    </button>
                  </span>
                </div>
                {!validated && (
                  <Hint>L&apos;export est activé après validation explicite de l&apos;ingénieur.</Hint>
                )}
              </Panel>
            </>
          )}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Créer la page et brancher la navigation**

```tsx
// web/src/app/ebios-rm/page.tsx
import { EbiosView } from "@/components/ebios/ebios-view";
import { PanelTitle } from "@/components/ui";

export default function EbiosPage() {
  return (
    <div className="rise-in py-4">
      <div className="mx-auto max-w-6xl">
        <PanelTitle kicker="Analyse de risques" title="EBIOS RM"
          hint="Atelier 1 : cadrage et socle de sécurité, généré depuis vos documents puis validé par vos soins." />
        <EbiosView />
      </div>
    </div>
  );
}
```

Dans `web/src/components/header-nav.tsx`, ajouter à `RAG_LINKS` (l.13-17), après `Informations` :

```typescript
  { href: "/ebios-rm", label: "EBIOS RM" },
```

Vérifier lors de l'exécution la signature exacte de `PanelTitle`/`Pill`/`Banner`/`Hint`/`Spinner` dans `web/src/components/ui.tsx` (props `children` vs `hint`) et ajuster les appels ci-dessus sans en changer l'intention.

- [ ] **Step 5: Lint et build**

Run: `cd web && npm run lint && npm run build`
Expected: lint sans erreur, build réussi (nouvelle route `/ebios-rm` listée)

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/ebios.ts web/src/components/ebios/ web/src/app/ebios-rm/ web/src/components/header-nav.tsx
git commit -m "feat(ebios): onglet EBIOS RM — dossiers, génération SSE, tables éditables sourcées, validation et export"
```

---

### Task 8: Validation de bout en bout + documentation

**Files:**
- Modify: `docs/PROJECT_STATE.md` (section EBIOS RM dans « Fonctionnalités livrées » + validation)
- Modify: `docs/HANDOFF-CLAUDE-EBIOS-RM.md` (encart « État d'implémentation » en tête + décisions V1 actées)
- Modify: `ARCHITECTURE.md` (module `core/ebios/`, collections, endpoints)

- [ ] **Step 1: Suite backend complète**

Run: `.venv/bin/python -m pytest -q`
Expected: aucune régression (≥ 265 + ~35 nouveaux tests, tous verts). Toute régression hors périmètre EBIOS : STOP, diagnostiquer avec superpowers:systematic-debugging avant de continuer.

- [ ] **Step 2: Démarrage réel et smoke test Playwright**

Utiliser le serveur API + web déjà en marche (`http://localhost:3000`, `http://127.0.0.1:8000`) ou les relancer selon `serve.py`/`web`. Avec le MCP Playwright :
1. Ouvrir `http://localhost:3000/ebios-rm` — l'onglet `EBIOS RM` est visible dans la nav principale.
2. Créer un dossier, cocher 2 documents réels ingérés, cliquer `Générer l'Atelier 1`.
3. Vérifier : progression visible, 3 tables remplies, preuves affichées (source + page + extrait), warnings visibles.
4. Éditer une cellule → `Sauvegarder` → version incrémentée.
5. `Valider` puis exporter XLSX ; ouvrir le fichier téléchargé avec openpyxl et vérifier : 15 feuilles, feuilles Atelier 1 remplies, modèle source intact (SHA-256).
6. Vérifier `Chat` : conversation courte fonctionnelle (aucune régression des parcours RAG).

- [ ] **Step 3: Mettre à jour la documentation**

Dans `docs/PROJECT_STATE.md`, ajouter aux fonctionnalités livrées :

```markdown
- Onglet `EBIOS RM` (Atelier 1) : dossiers multi-documents, génération structurée et
  sourcée (rôle `extract`, JSON strict), consolidation déterministe, trois tables
  éditables avec preuves et avertissements, versionnement optimiste
  (`ebios_projects`, `ebios_workshop_versions`), validation explicite puis export
  XLSX fidèle au classeur contractuel (SHA-256 vérifié, anti-injection, refus de
  dépassement). DOCX/PDF affichés indisponibles (templates non validés).
```

En tête de `docs/HANDOFF-CLAUDE-EBIOS-RM.md`, ajouter un encart d'état listant : tranche V1 livrée, décisions métier actées en autonomie (tableau des 9 décisions du plan), divergence GAP bloc 2 (2 écarts, fusions réelles), et ce qui reste ouvert pour V1.1 (régénération ciblée, templates DOCX/PDF, Ateliers 2–5).

- [ ] **Step 4: Commit final**

```bash
git add docs/PROJECT_STATE.md docs/HANDOFF-CLAUDE-EBIOS-RM.md ARCHITECTURE.md
git commit -m "docs(ebios): état V1 Atelier 1 — décisions actées, validation e2e, parcours RAG non régressés"
```

---

## Self-Review (effectuée)

- **Couverture spec** : onglet principal ✅ (T7), sélection multi-doc ✅ (T7), génération sourcée ✅ (T5/T6), 3 tables éditables persistées ✅ (T3/T4/T7), champs non prouvés visibles ✅ (T1 `bounded_control` + T7), validation/export distincts et traçables ✅ (T3/T4), sélecteur de format avec DOCX/PDF indisponibles ✅ (T4/T7), 3 feuilles exactes + modèle intact + styles/formules préservés + capacités + injection ✅ (T2), non-régression Chat/RAG ✅ (T8). Régénération ciblée : explicitement hors V1 (décision consignée).
- **Placeholders** : aucun TBD ; chaque step porte code ou commande exacte. Seule adaptation autorisée à l'exécution : signatures réelles du kit `ui.tsx` (consigne explicite en T7 Step 4).
- **Cohérence des types** : `expected_version` (int, 0 = aucune version) uniforme T3→T7 ; `origin` ∈ {generation, manual_edit, validation} ; événements SSE `stage|draft|done|error` identiques T6/T7 ; `GAP_ECART_CAPACITES=[2,2,1]` unique (schemas) et consommé par l'exporteur via `check_capacity`.
