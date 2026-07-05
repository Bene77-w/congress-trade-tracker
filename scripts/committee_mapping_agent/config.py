"""Zentrale Konfiguration des Committee-Mapping-Agents.

Die WICHTIGSTEN Stellschrauben (Modell + Thinking-Effort je Rolle) stehen ganz
oben im MODELS-Dict, damit sie ohne Suchen anpassbar sind. Alles weitere
(Budgets, Pfade, max_tokens) folgt darunter.
"""
import os

# ---------------------------------------------------------------------------
# MODELLE JE ROLLE  ->  hier anpassen.  Format:  rolle -> (modell, effort)
#   effort = None                -> Thinking-Mode aus (extra_body weglassen)
#   effort = "low"/"medium"/"high" -> Thinking-Mode an mit reasoning_effort
# Verifizierte DeepSeek-V4-Modelle (Juni 2026): deepseek-v4-pro, deepseek-v4-flash.
# ---------------------------------------------------------------------------
MODELS = {
    "research":  ("deepseek-v4-flash", 'high'),   # geordnete Infos sammeln
    "mapping":   ("deepseek-v4-pro",   "high"),  # eigentliche Klassifikation
    "review":    ("deepseek-v4-pro",   "max"),  # Plausibilitaets-Kontrolle
    "finalize":  ("deepseek-v4-pro",   "max"),  # User-Kommentare einarbeiten
}

# Review-LLM-Instanz an/aus. False = 'review' schreibt Pass-Through-Caches
# (Mapping-Konfidenz unveraendert, review_flag 'not_reviewed', keine API-Calls);
# der Pipeline-Rest (export-review/finalize/promote) laeuft identisch weiter.
# Sinnvoll, wenn Mapping + Review dasselbe Modell mit gleichem Effort nutzen —
# dann bringt die Zweitmeinung wenig ausser Kosten.
REVIEW_ENABLED = False

# max_tokens (Output-Limit) je Rolle. DeepSeek akzeptiert NUR max_tokens.
# ACHTUNG: Reasoning-Tokens (Thinking-Mode) zaehlen GEGEN dieses Budget —
# bei effort="max" muss es gross genug sein, sonst kommt leerer Content zurueck.
MAX_TOKENS = {
    "research":  16384,  # finales JSON grosser Committees (bis ~13 Einheiten
                         # mit Text+Zitat+URL) + Reasoning muss hineinpassen
    "mapping":   16384,
    "review":    16384,
    "finalize":  16384,
}

# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------
# Harte Obergrenze Tool-Turns je Research-Committee — reiner Runaway-/Kosten-
# Schutz (verhindert Endlos-Suchschleifen + Website-Hammering), KEIN Sparziel:
# der Prompt verlangt ohnehin minimale Tool-Nutzung. 16 gibt grossen Committees
# (z. B. 12 Subcommittees auf eigenen Unterseiten) genug Luft; nach Erschoepfung
# erzwingt llm_client einen Abschluss mit dem bereits Gesammelten (fehlende
# Einheiten -> grounding_quality='none', sichtbar im Review-xlsx).
MAX_SEARCH_TURNS = 16
MAX_REPAIR_RETRIES = 2     # Repair-Reprompts bei JSON/Schema-Fehler je Call
REQUEST_TIMEOUT = 120      # Sekunden je LLM-HTTP-Call

# Leeres-Mapping-Retry: bleibt eine Einheit nach dem ersten Versuch ohne valide
# Zeile, wird das Mapping mit frischem API-Call wiederholt (die Lauf-Varianz des
# Modells gibt so eine zweite Chance). Backoff = Abkuehlzeit (Sek.) VOR Retry i.
# Nur wenn MAP_RETRY_ONLY_GROUNDED den Versuch zulaesst (Default: nur bei
# grounding_quality == "direct" — bei "none"/"fallback" ist leer oft korrekt,
# da lohnt kein Backoff). Bleibt es nach allen Versuchen leer -> no_mapping.
MAP_EMPTY_RETRIES = 5                  # Re-Versuche bei leerem Mapping
MAP_EMPTY_BACKOFF = (2, 4, 8, 16, 32)  # Sek. vor Retry 1..5 (exponentiell)
MAP_RETRY_ONLY_GROUNDED = True         # nur retryen, wenn grounding == "direct"
FETCH_TIMEOUT = 20         # Sekunden je fetch_page-HTTP-Call
FETCH_MAX_CHARS = 20000    # Fenstergroesse je fetch_page-Antwort (Token-Schutz);
                           # lange Seiten gehen NICHT verloren: der Kappungs-
                           # Marker nennt start_char fuer die Fortsetzung

# Parallelisierung (Threads — die Calls sind I/O-bound, der OpenAI-Client ist
# thread-safe). DeepSeek drosselt unter Last serverseitig dynamisch: werden
# Calls auffaellig langsam, Werte senken. research bleibt bewusst sequentiell
# (kein Hammering der Committee-Websites).
MAP_CONCURRENCY = 6        # parallele Mapping-Einheiten
REVIEW_CONCURRENCY = 3     # parallele Review-Committees

# ---------------------------------------------------------------------------
# DeepSeek-API
# ---------------------------------------------------------------------------
API_BASE_URL = "https://api.deepseek.com"
API_KEY_ENV = "DEEPSEEK_API_KEY"

# ---------------------------------------------------------------------------
# Pfade  (BASE_DIR = Projekt-Root, 3 Ebenen ueber dieser Datei)
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_MAPPINGS = os.path.join(BASE_DIR, "data", "mappings")

# Inputs
TAXONOMY_SOURCE = os.path.join(DATA_MAPPINGS, "yf_sectors_with_industries.json")
TAXONOMY_ENRICHED = os.path.join(DATA_MAPPINGS, "yf_sectors_with_industries_enriched.json")
HOUSE_COMMITTEES_CSV = os.path.join(DATA_MAPPINGS, "us_house_all_committees.csv")
SENATE_COMMITTEES_CSV = os.path.join(DATA_MAPPINGS, "us_senate_all_committees.csv")

# Master (Promote-Ziel) + Staging
MASTER_CSV = os.path.join(DATA_MAPPINGS, "subcommittee_sector_industry_mapping.csv")
STAGING_CSV = os.path.join(DATA_MAPPINGS, "subcommittee_sector_industry_mapping_generated.csv")

# Caches (gitignored) + Review-xlsx + Report
CACHE_DIR = os.path.join(AGENT_DIR, "cache")
CACHE_RESEARCH = os.path.join(CACHE_DIR, "research")
CACHE_MAPPING = os.path.join(CACHE_DIR, "mapping")
CACHE_REVIEW = os.path.join(CACHE_DIR, "review")
REVIEW_DIR = os.path.join(AGENT_DIR, "review_out")
REPORT_PATH = os.path.join(AGENT_DIR, "run_report.json")

# ---------------------------------------------------------------------------
# CSV-Schema  (exakt das vom CommitteeMapper erwartete 7-Spalten-Format)
# ---------------------------------------------------------------------------
MASTER_COLUMNS = [
    "Chamber", "Committee", "Subcommittee",
    "Sector", "Industry", "Sector_Rank", "Konfidenz",
]

# Konfidenz-Uebersetzung: intern (Agent/Review) <-> finale CSV (Pipeline).
# Der CommitteeMapper erwartet die deutschen Strings (committee_mapping.py).
CONFIDENCE_TO_CSV = {"low": "Niedrig", "medium": "Mittel", "high": "Hoch"}
CONFIDENCE_FROM_CSV = {v: k for k, v in CONFIDENCE_TO_CSV.items()}
CONFIDENCE_LEVELS = ("low", "medium", "high")

# Kalibrierungs-Rubrik fuer die Konfidenz — identisch im Mapping- UND
# Review-Prompt, damit beide Agenten dieselbe Skala sprechen. Verlangt die
# Stufen ausdruecklich als Strings (DeepSeek neigt sonst zu Zahlen ~0.7, die
# beim Bucketing alle in 'medium' landen).
CONFIDENCE_RUBRIC = (
    "\nKONFIDENZ-KALIBRIERUNG (Pflicht, exakt einer der Strings low|medium|high"
    " — KEINE Zahlen):\n"
    "- high   = das woertliche Zitat nennt die Domaene der Industry explizit "
    "(z. B. Zitat nennt 'banks' -> Banks-Industries).\n"
    "- medium = klarer thematischer Bezug der Jurisdiktion, aber ohne "
    "woertliche Nennung im Zitat.\n"
    "- low    = Bezug nur indirekt ableitbar oder spekulativ.\n"
    "Differenziere ehrlich zwischen den Zeilen — nicht pauschal dieselbe "
    "Stufe vergeben."
)

# Grounding-Qualitaet -> maximal erlaubte Konfidenz (Deckel gegen Halluzination).
# Ohne echtes Zitat von der Primaerquelle darf nichts "high" sein.
GROUNDING_CONFIDENCE_CAP = {
    "direct":   "high",     # woertliches Zitat von offizieller Committee-Site
    "fallback": "medium",   # Zitat aus Fallback-Quelle (GovInfo/Rules-PDF)
    "none":     "low",      # kein verwertbares Zitat gefunden
}


def normalize_confidence(val) -> str:
    """Bringt eine Modell-Konfidenz auf low/medium/high.

    DeepSeek liefert haeufig numerische Konfidenz (0.95 oder 95) statt der
    gewuenschten Stufen-Strings. Statt dagegen anzukaempfen, akzeptieren wir
    Zahlen und bucketen sie. Unparsebares -> 'low'.
    """
    if isinstance(val, bool):
        return "low"
    if isinstance(val, (int, float)):
        f = float(val)
    else:
        s = str(val).strip().lower()
        if s in CONFIDENCE_LEVELS:
            return s
        try:
            f = float(s)
        except ValueError:
            return "low"
    if f > 1:           # 0-100-Skala -> 0-1
        f = f / 100.0
    if f >= 0.8:
        return "high"
    if f >= 0.5:
        return "medium"
    return "low"


def model_for(role: str):
    """(modell, effort) fuer eine Rolle; KeyError bei unbekannter Rolle."""
    return MODELS[role]


def max_tokens_for(role: str) -> int:
    return MAX_TOKENS.get(role, 4096)
