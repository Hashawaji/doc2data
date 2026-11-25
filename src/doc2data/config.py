"""Runtime settings, read from environment variables and an optional local .env file."""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATABASE_URL = "sqlite:///doc2data.db"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
ENV_FILE = ".env"  # looked up in the working directory

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_COMMENT = re.compile(r"(?:^|\s)#")


@dataclass(frozen=True)
class Settings:
    database_url: str
    extractor: str
    gemini_model: str


def load_env_file(
    path: str | os.PathLike[str] | None = None, environ: MutableMapping[str, str] | None = None
) -> None:
    """Copy KEY=VALUE lines from a .env file into the environment.

    A variable that is already set wins, so the real environment overrides the file.
    Within the file a later line replaces an earlier one, as it would in a shell. A
    missing or unreadable file is not an error. A value may be quoted; in an unquoted
    value a "#" at the start or after a space begins a comment.
    """
    target = os.environ if environ is None else environ
    try:
        text = Path(ENV_FILE if path is None else path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return
    pairs: dict[str, str] = {}
    for line in text.splitlines():
        match = _ENV_LINE.match(line)
        if match is None:  # a blank line, a comment, or anything else that is not KEY=VALUE
            continue
        key, value = match.groups()
        if value[:1] in ("'", '"') and (end := value.find(value[0], 1)) != -1:
            value = value[1:end]
        else:
            value = _COMMENT.split(value, maxsplit=1)[0].rstrip()
        pairs[key] = value
    for key, value in pairs.items():
        target.setdefault(key, value)


def load() -> Settings:
    return Settings(
        database_url=os.environ.get("DOC2DATA_DATABASE_URL", DEFAULT_DATABASE_URL),
        extractor=os.environ.get("DOC2DATA_EXTRACTOR", "rules"),
        gemini_model=os.environ.get("DOC2DATA_GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
    )
