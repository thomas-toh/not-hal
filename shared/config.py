"""Locate the repo and load the executable schemas in shared/schemas/*.json.

Hard rule 3 (CLAUDE.md): the schemas are the single source of truth. Code LOADS
them here and references the result — it never copies schema values into Python.
Adding a tool/earcon/message type means editing the JSON, not this file.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


# The OS credential-store service every API key is filed under. One home, because the daemon,
# the settings window and the smoke test all reach the same entries — three copies of a literal
# is three chances for a rename to orphan a user's stored keys.
KEY_SERVICE = "not-hal"


def repo_root() -> Path:
    # shared/config.py -> parent is shared/, its parent is the repo root.
    return Path(__file__).resolve().parent.parent


def schema_dir() -> Path:
    return repo_root() / "shared" / "schemas"


@lru_cache(maxsize=1)
def load_schemas() -> dict[str, dict]:
    """Every shared/schemas/*.json, keyed by the filename up to the first dot
    (tools.json -> "tools", audio.json -> "audio")."""
    d = schema_dir()
    schemas = {
        p.name.split(".", 1)[0]: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(d.glob("*.json"))
    }
    if not schemas:
        raise FileNotFoundError(f"no schemas found in {d}")
    return schemas


if __name__ == "__main__":
    # ponytail: runnable self-check — proves the loader finds and parses the schemas.
    s = load_schemas()
    assert {"tools", "earcons", "audio"} <= set(s), sorted(s)
    assert all(isinstance(v, dict) for v in s.values()), "each schema must parse to an object"
    print(f"loaded {len(s)} schemas from {schema_dir()}: {sorted(s)}")
