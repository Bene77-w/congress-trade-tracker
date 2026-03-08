"""
Congress Trade Tracker - Policy Area to Yahoo Finance Sector Mapping

Maps Congress.gov CRS Policy Areas to Yahoo Finance sectors.
Only "sichere" (high-confidence) mappings are included.
Unsichere/kontextabhaengige Policy Areas bleiben unmapped
und werden spaeter durch einen AI-Agenten klassifiziert.

Referenz: organisation/modul6_mapping.md
"""

# Die 11 gültigen Yahoo Finance Sektoren
VALID_SECTORS = [
    'Technology',
    'Healthcare',
    'Financial Services',
    'Energy',
    'Industrials',
    'Consumer Cyclical',
    'Consumer Defensive',
    'Real Estate',
    'Utilities',
    'Basic Materials',
    'Communication Services',
]

# Sichere 1:N Mappings (12 Policy Areas → YF-Sektoren)
# Maximal 3 Sektoren pro Policy Area fuer hohe Signalqualitaet
POLICY_AREA_TO_SECTORS = {
    'Agriculture and Food':
        ['Consumer Defensive'],
    'Armed Forces and National Security':
        ['Industrials', 'Technology'],
    'Energy':
        ['Energy', 'Utilities'],
    'Environmental Protection':
        ['Energy', 'Utilities', 'Basic Materials'],
    'Finance and Financial Sector':
        ['Financial Services'],
    'Health':
        ['Healthcare'],
    'Housing and Community Development':
        ['Real Estate', 'Financial Services'],
    'Public Lands and Natural Resources':
        ['Energy', 'Basic Materials'],
    'Science, Technology, Communications':
        ['Technology', 'Communication Services'],
    'Transportation and Public Works':
        ['Industrials'],
    'Water Resources Development':
        ['Utilities', 'Industrials'],
    'Emergency Management':
        ['Industrials'],
}

# Policy Areas die spaeter vom AI-Agent analysiert werden
# (zu breit oder kontextabhaengig fuer statisches Mapping)
AGENT_REQUIRED_POLICY_AREAS = [
    'Commerce',
    'Crime and Law Enforcement',
    'Government Operations and Politics',
]

# Policy Areas ohne direkten Einzelaktien-Bezug (systemisch)
SYSTEMIC_POLICY_AREAS = [
    'Taxation',
    'Economics and Public Finance',
    'Foreign Trade and International Finance',
    'International Affairs',
    'Labor and Employment',
    'Immigration',
]

# Policy Areas ohne wirtschaftliche Relevanz
IRRELEVANT_POLICY_AREAS = [
    'Education',
    'Families',
    'Social Welfare',
    'Civil Rights and Liberties, Minority Issues',
    'Arts, Culture, Religion',
    'Sports and Recreation',
    'Congress',
    'Law',
    'Native Americans',
    'Animals',
]


def get_relevant_sectors(policy_area: str) -> list[str]:
    """
    Gibt die gemappten YF-Sektoren fuer eine Policy Area zurueck.

    Args:
        policy_area: Congress.gov Policy Area (exakter String)

    Returns:
        Liste von YF-Sektoren, oder leere Liste falls kein Mapping
    """
    return POLICY_AREA_TO_SECTORS.get(policy_area, [])


def is_sector_relevant(policy_area: str, trade_sector: str) -> bool:
    """
    Prueft ob eine Policy Area zum Sektor eines Trades passt.

    Args:
        policy_area: Congress.gov Policy Area
        trade_sector: YF-Sektor des Trades (aus Relevant_Sectors)

    Returns:
        True wenn Match, False sonst
    """
    mapped_sectors = get_relevant_sectors(policy_area)
    return trade_sector in mapped_sectors


def get_unmapped_policy_areas() -> list[str]:
    """
    Gibt alle Policy Areas zurueck die keinem Sektor zugeordnet sind.
    Fuer spaetere AI-Agent-Integration.

    Returns:
        Liste von Policy Areas ohne statisches Mapping
    """
    return AGENT_REQUIRED_POLICY_AREAS + SYSTEMIC_POLICY_AREAS + IRRELEVANT_POLICY_AREAS


def get_mapping_coverage() -> dict:
    """
    Berechnet die Mapping-Abdeckung.

    Returns:
        Dict mit Statistiken
    """
    total = (
        len(POLICY_AREA_TO_SECTORS)
        + len(AGENT_REQUIRED_POLICY_AREAS)
        + len(SYSTEMIC_POLICY_AREAS)
        + len(IRRELEVANT_POLICY_AREAS)
    )
    mapped = len(POLICY_AREA_TO_SECTORS)
    return {
        'mapped': mapped,
        'total_known': total,
        'coverage_pct': round(mapped / total * 100, 1) if total else 0,
        'agent_required': len(AGENT_REQUIRED_POLICY_AREAS),
        'systemic': len(SYSTEMIC_POLICY_AREAS),
        'irrelevant': len(IRRELEVANT_POLICY_AREAS),
    }
