"""Shared paths for local-only Copilot data."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def private_root() -> Path:
    return Path(os.environ.get(
        "COPILOT_PRIVATE_DIR", "~/.copilot-private")).expanduser()


def load_private_json(relative_path: str) -> dict[str, Any]:
    path = private_root() / relative_path
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in private config: {path}")
    return value
