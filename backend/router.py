"""The router (spec/20 §Routing) — resolve a ROLE to the model that serves it, from the user's
settings. This is what finally makes the model picker BITE: until now `primary` was
written-but-unread and the orchestrator hardcoded Claude, so choosing a provider in the settings
window changed nothing at turn time.

v1: role -> the configured provider + model -> an adapter, read from the CURRENT provider-keyed
settings. Three roles today:

  assistant          the answer model            <- settings `primary`
  cleanup_dictation  dictation transform engine  <- settings `cleanup_dictation`
  cleanup_prompts    prompt-cleanup engine       <- settings `cleanup_prompts`

each naming a provider whose per-card config lives in `models[<provider>]`
({on, model, effort, thinking, endpoint?}).

Deliberately NOT here: choosing WHICH model for a request by its content ("short -> cheap"). That
is the per-task layer (Layer 2), which also brings the several-instances-per-provider redesign
(spec/70). v1 is role -> instance; the orchestrator seam (`build_for_role`) does not change when
Layer 2 lands — only the data this module reads.

Settings are read FRESH on every call, so a change in the picker takes effect on the next turn with
no restart. The orchestrator caches the adapter on `signature()` and rebuilds only when that
changes, so the HTTP client is still kept across turns (spec/20 adapter lifetime).
"""
from __future__ import annotations

from shared import settings
from backend.llm.providers import build_model

# The settings key naming the provider for each role. 'assistant' is the historical `primary`.
_ROLE_KEY = {
    "assistant": "primary",
    "cleanup_dictation": "cleanup_dictation",
    "cleanup_prompts": "cleanup_prompts",
}


def _as_float(v) -> float | None:
    """A temperature the adapter can use: a real number, or None. Tolerates the legacy STRING form
    ('0.7') older profiles hold, and never lets a junk value ride onto the wire as a string —
    CompatModel expects float | None (compat.py)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def resolve(role: str) -> dict | None:
    """The provider + dials the user configured for `role`, or None if unconfigured — no provider
    named, the named provider never added, its card switched off, or no model chosen. The caller
    applies its own default on None. Shape: {provider, model, effort, thinking, endpoint, temperature}.

    A role may also name its OWN model (schema `modelKey`), which wins over the provider card's —
    so one provider and one key can serve the assistant a large model and cleanup a small one.
    """
    key = _ROLE_KEY.get(role)
    if key is None:
        return None
    pid = settings.get(key)
    if not pid:
        return None
    m = (settings.get("models") or {}).get(pid)
    if not isinstance(m, dict) or not m.get("on") or not m.get("model"):
        return None
    # A role MAY name its own model, overriding the provider card's. Model otherwise hangs off the
    # CARD, so two roles naming one provider are forced to share it — which silently put dictation
    # cleanup on the assistant's 70B. Which setting holds the override is declared in the schema
    # (`modelKey`), not hardcoded here, so giving another role one is a schema edit. Empty or absent
    # = the card's model, i.e. exactly the previous behaviour.
    model_key = (settings.spec(key) or {}).get("modelKey")
    override = str(settings.get(model_key) or "").strip() if model_key else ""
    return {
        "provider": pid,
        "model": override or m["model"],
        "effort": m.get("effort"),
        "thinking": m.get("thinking"),
        "endpoint": m.get("endpoint"),
        # Coerced to a float (or None): the sheet writes a number, but a profile written before the
        # temperature control existed holds the string "0.7".
        "temperature": _as_float(m.get("temperature")),
        # How long a LOCAL server keeps the model in VRAM. Carried here because it can only be
        # applied when not-hal starts the server — Ollama ignores `keep_alive` on the /v1 wire
        # (tested 2026-08-02), so it rides in the environment at spawn, not on the request.
        "keep_alive": m.get("keep_alive"),
    }


def signature(role: str):
    """A hashable snapshot of what `build_for_role` would construct — the orchestrator caches its
    adapter on this and rebuilds only when it changes, so the client is kept across turns (spec/20)
    yet a picker change still lands next turn. None when the role is unconfigured."""
    cfg = resolve(role)
    if cfg is None:
        return None
    return (cfg["provider"], cfg["model"], cfg.get("endpoint"), cfg.get("effort"),
            cfg.get("temperature"))


def build_for_role(role: str):
    """The adapter serving `role`, or None if unconfigured (the caller defaults). Via
    providers.build_model, so B1 for the anthropic wire and B2 for the openai wire — the router
    chooses WHICH provider; the factory builds it."""
    cfg = resolve(role)
    if cfg is None:
        return None
    return build_model(cfg["provider"], cfg["model"], cfg.get("endpoint"),
                       effort=cfg.get("effort"), temperature=cfg.get("temperature"))


def _selfcheck() -> None:
    # ponytail: runnable check of the resolution logic — no network, no adapter built (build_model
    # is exercised by providers' own selfcheck). Points at a throwaway settings file.
    import os
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["NOTHAL_SETTINGS"] = str(Path(tmp) / "s.json")

        # Nothing configured -> every role is None, so the caller keeps its default (the whole
        # point that the daemon still answers before the user has picked anything).
        assert resolve("assistant") is None and signature("assistant") is None
        assert resolve("cleanup_dictation") is None
        assert resolve("bogus_role") is None, "an unknown role is None, never a crash"

        # A primary + its card -> the assistant routes to it, dials and all.
        settings.set("models", {"groq": {"on": True, "model": "llama-3.3-70b-versatile",
                                          "effort": None, "thinking": False}})
        settings.set("primary", "groq")
        cfg = resolve("assistant")
        assert cfg and cfg["provider"] == "groq" and cfg["model"] == "llama-3.3-70b-versatile", cfg
        assert signature("assistant") == ("groq", "llama-3.3-70b-versatile", None, None, None)

        # A card switched OFF, or with no model chosen, reads as unconfigured — the daemon must not
        # be pointed at a provider the user just disabled or never finished setting up.
        settings.set("models", {"groq": {"on": False, "model": "llama-3.3-70b-versatile"}})
        assert resolve("assistant") is None, "an off card must not be routed to"
        settings.set("models", {"groq": {"on": True, "model": ""}})
        assert resolve("assistant") is None, "no model chosen -> unconfigured"

        # A primary naming a provider that was never added -> None, not a KeyError.
        settings.set("models", {"groq": {"on": True, "model": "m"}})
        settings.set("primary", "nosuch")
        assert resolve("assistant") is None

        # Cleanup roles read their own key, independently of `primary`.
        settings.set("primary", "")
        settings.set("cleanup_dictation", "groq")
        assert resolve("cleanup_dictation")["provider"] == "groq"
        assert resolve("assistant") is None, "roles do not bleed into each other"

        # A role may name its OWN model, beating the provider card's — the point being one provider
        # and ONE key serving the assistant a large model and cleanup a small one, which the
        # card-holds-the-model shape could not express (it silently put cleanup on the 70B).
        settings.set("models", {"groq": {"on": True, "model": "llama-3.3-70b-versatile"}})
        settings.set("primary", "groq")
        assert resolve("cleanup_dictation")["model"] == "llama-3.3-70b-versatile", \
            "no override -> the card's model, i.e. the old behaviour"
        settings.set("cleanup_dictation_model", "llama-3.1-8b-instant")
        assert resolve("cleanup_dictation")["model"] == "llama-3.1-8b-instant", "the role's own model wins"
        assert resolve("assistant")["model"] == "llama-3.3-70b-versatile", \
            "one role's override must NOT leak to another on the same provider"
        assert signature("cleanup_dictation") != signature("assistant"), \
            "one provider, two models -> two signatures, so both llm get built"
        settings.set("cleanup_dictation_model", "   ")
        assert resolve("cleanup_dictation")["model"] == "llama-3.3-70b-versatile", \
            "a blank override is not a model — fall back to the card"
        settings.set("cleanup_dictation_model", "")

        # Temperature (local providers only): coerced to a float the adapter can use — a profile
        # written before the control existed holds the STRING "0.7" — threaded through
        # build_for_role to the B2 adapter, and part of the signature so a change rebuilds.
        settings.set("models", {"ollama": {"on": True, "model": "qwen3:8b",
                                           "endpoint": "127.0.0.1:11434", "temperature": "0.4"}})
        settings.set("primary", "ollama")
        assert resolve("assistant")["temperature"] == 0.4, "the legacy string must coerce to a float"
        assert isinstance(resolve("assistant")["temperature"], float)
        model = build_for_role("assistant")
        assert model is not None and model.temperature == 0.4, "temperature must reach the adapter"
        _sig_warm = signature("assistant")
        settings.set("models", {"ollama": {"on": True, "model": "qwen3:8b",
                                           "endpoint": "127.0.0.1:11434", "temperature": 0.9}})
        assert signature("assistant") != _sig_warm, "a temperature change must rebuild the adapter"
        settings.set("models", {"ollama": {"on": True, "model": "m"}})   # absent -> None
        assert resolve("assistant")["temperature"] is None, "no temperature set -> None, not a crash"
        assert build_for_role("assistant").temperature is None

        # A changed pick changes the signature (so the orchestrator rebuilds), an identical one
        # does not (so it keeps the client).
        settings.set("primary", "groq")
        settings.set("models", {"groq": {"on": True, "model": "m"}})
        sig1 = signature("assistant")
        assert signature("assistant") == sig1, "an unchanged config keeps its signature"
        settings.set("models", {"groq": {"on": True, "model": "other-model"}})
        assert signature("assistant") != sig1, "a changed model must change the signature"
    os.environ.pop("NOTHAL_SETTINGS", None)
    print("router selfcheck OK: role -> configured provider/model (or None to default); "
          "off/modelless/missing cards resolve to None; signature tracks the pickable config")


if __name__ == "__main__":
    _selfcheck()
