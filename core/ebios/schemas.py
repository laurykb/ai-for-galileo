"""Schémas Atelier 1 EBIOS RM — source de vérité JSON, versionnée et migrable."""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class _StrictModel(BaseModel):
    """Modèle de base strict : rejette les champs inconnus (extra="forbid")."""
    model_config = ConfigDict(extra="forbid")

GRAVITE_LABELS = {1: "Mineure", 2: "Significative", 3: "Grave", 4: "Critique"}


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


class Preuve(_StrictModel):
    document_id: str
    source: str
    page: Optional[int] = None
    extrait: str


class Responsable(_StrictModel):
    nom: str
    type: TypeResponsable


class ValeurMetier(_StrictModel):
    id: str
    denomination: str
    nature: NatureVM
    description: str = ""
    responsable: Responsable
    preuves: List[Preuve] = Field(default_factory=list)


class BienSupport(_StrictModel):
    id: str
    denomination: str
    description: str = ""
    responsable: Responsable
    valeurs_metier_associees: List[str] = Field(default_factory=list)
    preuves: List[Preuve] = Field(default_factory=list)


class EvenementRedoute(_StrictModel):
    id: str
    valeur_metier_id: str = ""
    evenement: str
    impact: str = ""
    gravite: int = Field(ge=1, le=4)
    justification_gravite: str = ""
    preuves: List[Preuve] = Field(default_factory=list)


class Ecart(_StrictModel):
    description: str
    justification: str = ""
    preuves: List[Preuve] = Field(default_factory=list)


class SocleSecurite(_StrictModel):
    id: str
    type_referentiel: str
    nom_referentiel: str
    etat_application: EtatApplication
    ecarts: List[Ecart] = Field(default_factory=list)


class Atelier1(_StrictModel):
    mission: str = ""
    valeurs_metier: List[ValeurMetier] = Field(default_factory=list)
    biens_supports: List[BienSupport] = Field(default_factory=list)
    evenements_redoutes: List[EvenementRedoute] = Field(default_factory=list)
    socle_securite: List[SocleSecurite] = Field(default_factory=list)


class EbiosStudy(_StrictModel):
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
    return warns
