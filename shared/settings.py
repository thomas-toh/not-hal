r"""User settings — the config file the settings window writes and the bridge reads.

One small JSON file in the per-user config dir: the settings window (teleprompter process)
writes it, the bridge reads it FRESH at each decision point, so a change takes effect on the
next turn with no restart and no file-watcher. Stdlib only — the bridge must read this
headless, without Qt.

spec/70 §2: settings travel by FILE, not over the status socket — this is that file.

The knobs themselves live in `shared/schemas/settings.json` (hard rule 3), not here: defaults,
labels, help text and which pane a row belongs to are all read from it, by this module and by
the settings window alike. Adding a setting means editing that JSON and nothing else.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from shared.config import load_schemas

log = logging.getLogger("nothal.settings")


def schema() -> dict:
    """The whole settings schema: `panes`, `settings`, `providers`."""
    return load_schemas()["settings"]


def spec(key: str) -> dict:
    """One setting's declaration (type, default, label, help, pane, built)."""
    return schema()["settings"].get(key, {})


def defaults() -> dict[str, object]:
    """Every default, derived from the schema — never restated in Python (hard rule 3)."""
    return {k: v["default"] for k, v in schema()["settings"].items()}


def settings_path() -> Path:
    r"""%APPDATA%
othalsettings.json on Windows; ~/.config/nothal/settings.json elsewhere.
    NOTHAL_SETTINGS overrides the whole path (tests + power users), matching spec/70's env-override
    pattern. Location chosen here (spec/70 §4) so every writer and reader agrees."""
    override = os.environ.get("NOTHAL_SETTINGS")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / "nothal" / "settings.json"


def _read_raw() -> dict:
    """Only the keys actually written to the file (no defaults merged in), so the file stays a
    minimal record of user overrides and the schema's defaults can evolve. Missing/broken file
    -> {}: settings must never be the reason the daemon won't start."""
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("settings unreadable (%s) — using defaults", e)
        return {}


def load() -> dict:
    """Every setting: schema defaults under whatever the file overrides."""
    return {**defaults(), **_read_raw()}


def get(key: str):
    """One setting by name, falling back to its schema default (or None if unknown)."""
    return load().get(key, defaults().get(key))


def set(key: str, value) -> None:
    """Write one setting, preserving the others already in the file. Creates the dir/file on
    first write. The settings window calls this; the bridge only reads."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_raw()
    data[key] = value
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    # A structured value is logged as its SHAPE, never verbatim: keys live in the OS credential
    # store (spec/50 rule 10), and the log is this file's shadow — dumping a whole dict at INFO is
    # one careless field away from writing a secret down. Scalars are safe and useful to see; the
    # `models` dict (the only structured value today) is logged as its provider ids alone.
    if isinstance(value, dict):
        log.info("setting %s (%d: %s)", key, len(value), ", ".join(map(str, value)))
    elif isinstance(value, list):
        log.info("setting %s (%d items)", key, len(value))
    else:
        log.info("setting %s = %r", key, value)


if __name__ == "__main__":
    # ponytail: runnable self-check for the read/write/merge and the schema derivation — points
    # at a throwaway file so it never touches the real settings.
    import tempfile

    d = defaults()
    assert d["tts"] is False and d["pings"] is True, d
    assert d["listen_for_me"] is False, "the mic must be shut by default (D23)"
    assert d["skip_permissions"] is False, "a permission bypass must never default on"
    # Every declared setting is renderable: it names a real pane, or opts out with null.
    panes = {p["id"] for p in schema()["panes"]}
    for key, s in schema()["settings"].items():
        assert s["pane"] is None or s["pane"] in panes, f"{key}: unknown pane {s['pane']!r}"
        assert "default" in s and "type" in s, f"{key}: incomplete declaration"
    # A provider offered in Manage must say where it runs and how it authenticates.
    for pid, p in schema()["providers"].items():
        assert p["where"] in ("cloud", "local"), pid
        assert p["auth"] in ("key", "endpoint"), pid

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["NOTHAL_SETTINGS"] = str(Path(tmp) / "settings.json")
        assert load() == d, "missing file must yield the schema defaults"
        assert get("tts") is False and get("pings") is True
        assert get("nope") is None, "unknown key -> None"
        set("tts", True)
        assert get("tts") is True, "set() must persist"
        assert get("pings") is True, "set() must leave other keys at their default"
        set("models", {"anthropic": {"on": True, "model": "claude-opus-4-8"}})
        assert get("models")["anthropic"]["model"] == "claude-opus-4-8", "structured values survive"
        assert get("tts") is True, "a structured write must not disturb earlier keys"
    os.environ.pop("NOTHAL_SETTINGS", None)
    print(f"settings selfcheck OK: {len(d)} settings from the schema, "
          f"{len(schema()['providers'])} providers, path -> {settings_path()}")
