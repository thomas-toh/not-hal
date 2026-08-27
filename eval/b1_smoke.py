"""
B1 smoke test.

Proves the three things the B1 model adapter will rely on:
  1. AUTH      — the API key works and a model responds.
  2. STREAMING — text arrives as a token stream, and we measure time-to-first-token.
  3. TOOL USE  — the model emits a well-formed tool call for a registry-style tool,
                 accepts a tool_result, and produces a final answer.

HOW TO RUN (Windows PC first; repeat on the Mac whenever convenient)
--------------------------------------------------------------------
1. console.anthropic.com → create a DEDICATED key for this project (name it "not-hal")
   and set a monthly spend cap (£10–20) under Limits. Fund with a few pounds.
2. Store the key in the OS credential store — never setx, never a repo file. Same
   command on Windows (Credential Manager) and macOS (Keychain):
     pip install keyring
     python -c "import keyring; keyring.set_password('not-hal','anthropic', input('key: ').strip())"
   (input() keeps the key out of your shell history.)
3. Install the SDK:  pip install anthropic
4. Run:              python eval/b1_smoke.py
5. Expect three PASS lines and exit code 0. Total cost: well under £0.01.

A session-scoped ANTHROPIC_API_KEY env var works as a fallback (e.g. CI), but the
credential store is the project standard.

If the model id has aged out (error mentions the model), set NOTHAL_SMOKE_MODEL to a
current id from the console and re-run.

DONE-WHEN:
  [ ] exits 0 on the Windows PC (all three PASS)
  [ ] time-to-first-token noted (expect roughly 300–900 ms; it feeds the latency budget)
  [ ] key lives in Credential Manager under service "not-hal" (not setx, not any file)
      and has a spend cap set in the console
"""

import os
import sys
import time

MODEL = os.environ.get("NOTHAL_SMOKE_MODEL", "claude-sonnet-4-5")

# A tool in ANTHROPIC'S wire shape (`input_schema`, no `tier`), which is NOT the shape of an entry
# in shared/schemas/tools.json (`parameters`, plus `tier`). Treating them as the same shape is how
# B1's missing translation went unnoticed for a fortnight: this script passed, because it
# hand-wrote the shape the API wants, while the adapter forwarded the registry verbatim and would
# have 400'd on the first real tool. Fixed 2026-07-24 — adapters now translate. Kept in the wire
# shape deliberately: this script tests the API, not the registry.
ECHO_TOOL = {
    "name": "echo_test",
    "description": "Echo the given payload back to the assistant. A test tool.",
    "input_schema": {
        "type": "object",
        "properties": {"payload": {"type": "string"}},
        "required": ["payload"],
        "additionalProperties": False,
    },
}

MAGIC = "nothal-loop-42"


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")
    sys.exit(1)


def get_key() -> str:
    """Credential store first; session env var as fallback."""
    try:
        import keyring
        key = keyring.get_password(KEY_SERVICE, "anthropic")
        if key:
            print("  key source: OS credential store (service 'not-hal')")
            return key
    except ImportError:
        pass
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        print("  key source: environment variable (fallback — credential store "
              "is the project standard, see header step 2)")
        return key
    fail("No key found. Store it per header step 2 (keyring), or set "
         "ANTHROPIC_API_KEY in this session.")


def main() -> None:
    print(f"B1 smoke test · model={MODEL}\n")

    api_key = get_key()

    try:
        import anthropic
    except ImportError:
        fail("SDK missing — run: pip install anthropic")

    client = anthropic.Anthropic(api_key=api_key)

    # ---- 1. AUTH ---------------------------------------------------------
    try:
        r = client.messages.create(
            model=MODEL, max_tokens=32,
            messages=[{"role": "user", "content": "Reply with exactly: pong"}],
        )
    except anthropic.AuthenticationError:
        fail("Key rejected — check it was copied whole and the account is funded.")
    except anthropic.NotFoundError:
        fail(f"Model '{MODEL}' not found — set NOTHAL_SMOKE_MODEL to a current id.")
    text = "".join(b.text for b in r.content if b.type == "text")
    if "pong" not in text.lower():
        fail(f"Unexpected reply: {text!r}")
    print(f"  PASS  auth       reply={text.strip()!r}  "
          f"tokens in/out={r.usage.input_tokens}/{r.usage.output_tokens}")

    # ---- 2. STREAMING (measure time-to-first-token) ----------------------
    t0 = time.perf_counter()
    ttft = None
    chunks: list[str] = []
    with client.messages.stream(
        model=MODEL, max_tokens=100,
        messages=[{"role": "user", "content": "Count from 1 to 10, digits only."}],
    ) as stream:
        for piece in stream.text_stream:
            if ttft is None:
                ttft = time.perf_counter() - t0
            chunks.append(piece)
    total = time.perf_counter() - t0
    if ttft is None or not chunks:
        fail("Stream produced no text.")
    print(f"  PASS  streaming  first-token={ttft*1000:.0f} ms  total={total*1000:.0f} ms  "
          f"chunks={len(chunks)}")

    # ---- 3. TOOL USE (request → tool_result → final answer) --------------
    messages = [{"role": "user", "content":
                 f"Call the echo_test tool with payload '{MAGIC}', then tell me "
                 f"what it returned."}]
    r1 = client.messages.create(model=MODEL, max_tokens=200,
                                tools=[ECHO_TOOL], messages=messages)
    calls = [b for b in r1.content if b.type == "tool_use"]
    if not calls:
        fail("Model did not emit a tool_use block.")
    call = calls[0]
    if call.name != "echo_test" or call.input.get("payload") != MAGIC:
        fail(f"Malformed tool call: name={call.name} input={call.input}")
    print(f"  PASS  tool-call  name={call.name}  payload={call.input['payload']!r}  "
          f"id={call.id[:14]}…")

    messages += [
        {"role": "assistant", "content": r1.content},
        {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": call.id,
            "content": f"echoed: {MAGIC}",
        }]},
    ]
    r2 = client.messages.create(model=MODEL, max_tokens=200,
                                tools=[ECHO_TOOL], messages=messages)
    final = "".join(b.text for b in r2.content if b.type == "text")
    if MAGIC not in final:
        fail(f"Final answer lost the payload: {final!r}")
    print(f"  PASS  tool-loop  final answer contains payload")

    print("\nAll green — B1 is ready. Record the first-token number.")


if __name__ == "__main__":
    main()
