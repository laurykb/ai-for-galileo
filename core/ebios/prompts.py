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
Identifie les événements redoutés : atteintes portées à une valeur métier, leur impact
et leur gravité sur l'échelle 1=Mineure, 2=Significative, 3=Grave, 4=Critique.

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
