"""Output-Kontrakt der Lauf-Enddokumente + gestylter Markdown-Renderer (B4)."""

from __future__ import annotations

from .contract import (
    BudgetCheck,
    OutputContract,
    count_tokens,
    outputs_dir,
    persist_outputs,
    recommendation_doc_path,
    report_doc_path,
)
from .renderer import (
    markdown_to_html,
    render_artifact,
    render_csv,
    render_document,
    render_json,
    render_text,
)

__all__ = [
    "BudgetCheck",
    "OutputContract",
    "count_tokens",
    "outputs_dir",
    "persist_outputs",
    "recommendation_doc_path",
    "report_doc_path",
    "markdown_to_html",
    "render_artifact",
    "render_csv",
    "render_document",
    "render_json",
    "render_text",
]
