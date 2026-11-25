"""Extractors turn one PDF into an Invoice.

To add a provider, add a module here and one line to EXTRACTORS (and its price to
pricing.py, or its cost is shown as "not priced").
"""

from __future__ import annotations

from importlib import import_module

from .base import ExtractionError, ExtractionResult, Extractor, Usage

__all__ = ["EXTRACTORS", "ExtractionError", "ExtractionResult", "Extractor", "Usage", "get_extractor"]

# name -> (module in this package, class). Modules are imported on demand, so the rules
# run without any model SDK installed.
EXTRACTORS: dict[str, tuple[str, str]] = {
    "rules": ("rules", "RulesExtractor"),
    "gemini": ("gemini", "GeminiExtractor"),
}


def get_extractor(name: str) -> Extractor:
    key = name.strip().lower()
    if key not in EXTRACTORS:
        raise ExtractionError(f"Unknown extractor {name!r}. Choose one of: {', '.join(EXTRACTORS)}.")
    module, cls = EXTRACTORS[key]
    return getattr(import_module(f"{__name__}.{module}"), cls)()
