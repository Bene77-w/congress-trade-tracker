"""Gemeinsame Basis fuer die Suspicious-Trades-Workflow-Varianten."""

# Muss vor jedem litellm-Import laufen: litellm laedt beim Import selbst die
# Root-.env aus dem CWD (load_dotenv im DEV-Modus). Laedt die Package-.env
# nicht zuerst, gewinnen die Root-Keys via setdefault und gelten faelschlich
# als process-env.
from . import config as _config  # noqa: F401
