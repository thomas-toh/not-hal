"""B1: the Anthropic Messages API adapter (Contract B).

Streams a Claude reply as ModelEvents. The first build ran zero tools (utterance in, streamed text
out); the tool_use path is wired for the tool loop.

    python -m backend.llm.claude "what time is it in Tokyo?"   # live console round-trip
    python -m backend.llm.claude --selfcheck                   # no network: error mapping
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import AsyncIterator

from .base import (
    DEFAULT_SYSTEM,
    MAX_TOKENS,
    Done,
    Error,
    Session,
    TextDelta,
    ToolCall,
    ToolSpec,
    ssl_context,
)
from .providers import credential_for

# DEFAULT_SYSTEM and MAX_TOKENS moved to base.py when B2 arrived — they describe the app, not
# Anthropic, and a later versioned persona must have one place to replace. Imported above so this
# module's own name for them still resolves.
#
# There is deliberately NO default model here. An adapter that silently defaulted to one Claude
# model over another would carry a preference, and asymmetrically — B2 already demands the caller
# name a model. Both adapters now do: a turn with no model yields a clean Error, never a guess.
# The daemon's operational default (it is Claude-only until the router lands) lives in the
# orchestrator, where it belongs — a caller's choice, not the adapter's.

# No `thinking` param: on Opus 4.8 that means thinking is OFF, which is what a <4 s
# first-word voice reply wants (adaptive thinking delays the first token).


def _tools_for_api(tools: list[ToolSpec]) -> list[dict]:
    """Contract T registry entries -> Anthropic tool objects.

    The registry spells the JSON-schema key `parameters` (shared/schemas/tools.json) and carries a
    `tier`; Anthropic requires `input_schema` and rejects unknown fields. The list used to be
    passed through VERBATIM, which was invisible only because the first build passed an empty one — the first
    real tool would have 400'd, and `_error_kind` maps a 400 to the generic apology, so it would
    have surfaced as an unexplained "sorry". Translation lives here for the same reason error
    mapping does: it is this provider's wire format.
    """
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t.get("parameters") or {"type": "object", "properties": {}},
        }
        for t in tools
    ]


def _error_kind(exc: Exception) -> str:
    """Map an SDK exception to a shared Contract B Error kind by its TYPE and status
    code — never by matching the message prose.

    No `context` case: a context overflow and any other malformed request are BOTH
    `BadRequestError` / `type == "invalid_request_error"` (verified, anthropic 0.116.0) — the
    provider gives no distinct code to switch on, so the only in-band signal is the message
    text, and heuristics over prose mis-narrate (a 400 about a bad field said "conversation too
    long"). A 400 therefore maps to `unknown` (the generic apology), which is what the API is
    actually telling us. Detecting context overflow *properly* means counting tokens against the
    model's window BEFORE the call — a proactive check, not an error heuristic — and that only
    earns its keep once conversations persist across wakes (parked)."""
    import anthropic

    if isinstance(exc, anthropic.AuthenticationError):
        return "auth"
    if isinstance(exc, anthropic.RateLimitError):
        return "rate_limit"
    if isinstance(exc, anthropic.APIConnectionError):
        return "unavailable"
    if isinstance(exc, anthropic.APIStatusError):   # covers BadRequestError (400) -> unknown
        return "unavailable" if exc.status_code >= 500 else "unknown"
    return "unknown"


class ClaudeModel:
    """B1. `converse` is an async generator, matching the ModelAdapter Protocol."""

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model
        # Both the credential-store account name and the env-var fallback come from the
        # catalogue now (shared/schemas/settings.json -> providers.anthropic), so B1 no longer
        # hardcodes what the settings window writes. Same entry, read through the schema.
        self._api_key = api_key or credential_for("anthropic")
        self._client = None          # built on first use, then kept — see _client_once()

    def _client_once(self):
        """One client for this adapter's life, resting on Contract B's one-loop guarantee.
        It used to be rebuilt per turn, which cost two separate things on the
        end-of-speech -> first-word path: a fresh TCP+TLS handshake (unavoidable then, since
        an httpx pool belongs to the loop that made it and the loop died with the turn), and
        ~190 ms of plain CPU re-reading this machine's CA bundle. Only the second is fixed
        here; the first is fixed by there being one loop at all.

        `DefaultAsyncHttpxClient`, NOT a bare `httpx.AsyncClient`: the SDK passes a supplied
        client through verbatim, and a bare one silently swaps the SDK's 600 s read timeout
        for httpx's 5 s default — which would abort exactly the slow-first-token turns this
        is meant to make faster (this repo has already recorded a 9.1 s cold turn).
        """
        import anthropic

        if self._client is None:
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key,
                http_client=anthropic.DefaultAsyncHttpxClient(verify=ssl_context()),
            )
        return self._client

    async def converse(
        self,
        session: Session,
        utterance: str,
        tools: list[ToolSpec],
    ) -> AsyncIterator:
        if session.local_only:
            yield Error("unavailable", "B1 (Claude API) blocked: session is local_only")
            return
        if not self._api_key:
            yield Error("auth", "no API key (keyring service 'not-hal', account 'anthropic')")
            return
        if not self.model:
            yield Error("no_model", "no model chosen for 'anthropic'")
            return

        client = self._client_once()
        # An empty utterance is the tool-loop CONTINUE signal: the new input — the
        # tool_result message that record_tool_round wrote — is already in history, so add no user
        # turn. Two user messages in a row would break Anthropic's strict user/assistant alternation.
        messages = list(session.history)
        if utterance:
            messages.append({"role": "user", "content": utterance})
        kwargs = dict(
            model=self.model,
            max_tokens=session.max_tokens or MAX_TOKENS,   # transform lifts this for long text
            system=session.system or DEFAULT_SYSTEM,
            messages=messages,
        )
        if session.temperature is not None:                # transform runs deterministic
            kwargs["temperature"] = session.temperature
        if tools:
            kwargs["tools"] = _tools_for_api(tools)

        try:
            async with client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield TextDelta(text)
                final = await stream.get_final_message()
            # Tool calls: a turn either speaks or calls tools; surface them after
            # the text so the orchestrator can execute through Contract T.
            for block in final.content:
                if block.type == "tool_use":
                    yield ToolCall(block.id, block.name, block.input)
            yield Done(
                usage={
                    "input_tokens": final.usage.input_tokens,
                    "output_tokens": final.usage.output_tokens,
                }
            )
        except Exception as exc:  # noqa: BLE001 - map every provider error to Error
            yield Error(_error_kind(exc), str(getattr(exc, "message", exc)))

    @staticmethod
    def record_tool_round(session: Session, assistant_text: str, calls, results: dict) -> None:
        """Append one completed tool round to `session.history` in Anthropic's wire shape, so the
        next converse round (driven with an empty utterance) sees the model's tool_use blocks and
        their results. The orchestrator owns the loop and executes the tools through Contract T;
        this only SERIALISES, because the message shape IS this provider's wire format —
        the same reason tool translation and error mapping live in the adapter, not the caller.

        `results` maps a tool_use id -> its result string. Anthropic wants ONE assistant message
        whose content interleaves any spoken text with the tool_use blocks, then ONE user message
        carrying every tool_result block."""
        content: list[dict] = []
        if assistant_text:
            content.append({"type": "text", "text": assistant_text})
        for c in calls:
            content.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.input})
        session.history.append({"role": "assistant", "content": content})
        session.history.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": c.id, "content": results.get(c.id, "")}
                for c in calls
            ],
        })


async def _run(question: str, model: str) -> int:
    adapter = ClaudeModel(model=model)
    session = Session(id="cli")
    status = 0
    async for ev in adapter.converse(session, question, []):
        if isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
        elif isinstance(ev, ToolCall):
            print(f"\n[tool_call {ev.name} {ev.input}]", flush=True)
        elif isinstance(ev, Done):
            print(f"\n[done: {ev.usage}]")
        elif isinstance(ev, Error):
            print(f"\n[error/{ev.kind}: {ev.detail}]", file=sys.stderr)
            status = 1
    return status


def _selfcheck() -> None:
    # Error mapping is by exception TYPE and status code, never message prose. Build real
    # SDK exception instances via __new__ (isinstance passes; no httpx.Response to fake), set
    # only the status_code the ladder reads. A 400 maps to `unknown` — the generic apology —
    # because Anthropic collapses context-overflow and other bad requests into one type.
    import anthropic

    def _exc(cls, status=None):
        e = cls.__new__(cls)
        if status is not None:
            e.status_code = status
        return e

    assert _error_kind(_exc(anthropic.AuthenticationError)) == "auth"
    assert _error_kind(_exc(anthropic.RateLimitError)) == "rate_limit"
    assert _error_kind(_exc(anthropic.APIConnectionError)) == "unavailable"
    assert _error_kind(_exc(anthropic.InternalServerError, 500)) == "unavailable"
    assert _error_kind(_exc(anthropic.BadRequestError, 400)) == "unknown", \
        "a 400 must map to the generic apology — no prose-guessing at 'context'"
    assert _error_kind(RuntimeError("boom")) == "unknown"
    assert DEFAULT_SYSTEM and "voice" in DEFAULT_SYSTEM.lower()

    # Tool translation. The registry spells `parameters`; Anthropic demands `input_schema` and
    # rejects unknown fields, so passing an entry through verbatim 400s on the first real tool.
    # Exercised against the REAL registry, because that is the shape the model will be handed.
    from shared.config import load_schemas

    registry = load_schemas()["tools"]["tools"]
    assert registry, "shared/schemas/tools.json must carry the starter tools"
    wired = _tools_for_api(registry)
    assert len(wired) == len(registry)
    for w, t in zip(wired, registry):
        assert set(w) == {"name", "description", "input_schema"}, \
            f"Anthropic rejects unknown tool fields, got {sorted(w)}"
        assert w["name"] == t["name"]
        assert w["input_schema"] == t["parameters"], "`parameters` must become `input_schema`"
        assert "tier" not in w, "tier is the app's safety business and must not leave the machine"
    assert _tools_for_api([{"name": "bare"}])[0]["input_schema"] == \
        {"type": "object", "properties": {}}, "a tool with no parameters still needs a schema"

    # Tool-loop threading: a completed round serialises into history in Anthropic's wire
    # shape — one assistant message interleaving text with tool_use, then one user message of
    # tool_result blocks — so the next round (empty utterance) reads the results without stacking
    # a second user turn.
    s = Session(id="t")
    ClaudeModel.record_tool_round(
        s, "one moment", [ToolCall("tu_1", "system_status", {})], {"tu_1": "Local time: noon"})
    assert s.history[0]["role"] == "assistant"
    assert s.history[0]["content"][0] == {"type": "text", "text": "one moment"}
    assert {"type": "tool_use", "id": "tu_1", "name": "system_status", "input": {}} \
        in s.history[0]["content"]
    assert s.history[1] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "Local time: noon"}]}
    # A tool-only round (no spoken text) carries just the tool_use block.
    s2 = Session(id="t")
    ClaudeModel.record_tool_round(s2, "", [ToolCall("tu_2", "x", {})], {})
    assert s2.history[0]["content"] == [{"type": "tool_use", "id": "tu_2", "name": "x", "input": {}}]
    assert s2.history[1]["content"][0]["content"] == "", "a missing result serialises as empty"

    # Client lifetime. No network: every cost here is local CPU.
    import time

    assert ssl_context() is ssl_context(), "the CA bundle must be parsed once per process"
    model = ClaudeModel(api_key="x")
    first = model._client_once()                    # also warms `import anthropic` (~600 ms,
    assert model._client_once() is first, \
        "the client must be built once and kept"    # ...paid once at startup, not per turn)
    # Time a genuinely FRESH build, imports warm — this is the per-turn cost that used to be
    # paid on every question. ~190 ms unmemoised vs ~0.2 ms memoised (measured 2026-07-22),
    # so 50 ms sits ~4x under the failure and ~250x over the pass.
    t0 = time.perf_counter()
    ClaudeModel(api_key="x")._client_once()
    build_ms = (time.perf_counter() - t0) * 1000
    assert build_ms < 50, f"building the client took {build_ms:.0f} ms — CA bundle reloaded?"
    # A supplied http_client is used VERBATIM by the SDK, so a bare httpx.AsyncClient would
    # silently swap the SDK's 600 s read timeout for httpx's 5 s default and start killing
    # slow first tokens — the exact turns this whole change exists to speed up.
    assert first.timeout.read >= 60, f"custom client dropped the SDK read timeout: {first.timeout}"

    # No baked model preference. A turn with no model must yield a clean Error, never a guess —
    # symmetric with B2, and the whole point of the agnosticism pass.
    async def _first(model, session):
        async for ev in model.converse(session, "q", []):
            return ev

    ev = asyncio.run(_first(ClaudeModel(api_key="x"), Session(id="t")))
    assert isinstance(ev, Error) and ev.kind == "no_model", \
        f"no model must be reported as such, not defaulted nor shrugged at: {ev}"

    print("selfcheck OK: error mapping by type/status (no prose), no baked model default, client "
          "built once with the trust store memoised and the SDK's long read timeout intact")


def main() -> None:
    # A console convenience default, NOT an adapter default: this is the Claude tester, so a Claude
    # model is intrinsic here. The env var is the daemon's knob (orchestrator.DAEMON_MODEL) and is
    # honoured so `python -m backend.llm.claude "q"` matches what the daemon would run.
    cli_default = os.environ.get("NOTHAL_MODEL", "claude-opus-4-8")
    ap = argparse.ArgumentParser(description="Talk to the Anthropic API directly")
    ap.add_argument("question", nargs="?", help="prompt to send to Claude")
    ap.add_argument("--model", default=cli_default, help=f"model id (default {cli_default})")
    ap.add_argument("--selfcheck", action="store_true", help="offline logic check, no network")
    args = ap.parse_args()

    if args.selfcheck:
        _selfcheck()
        return
    if not args.question:
        ap.error("provide a question, or --selfcheck")
    sys.exit(asyncio.run(_run(args.question, args.model)))


if __name__ == "__main__":
    main()
