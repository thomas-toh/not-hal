"""Contract T tool-choice eval (was backend.orchestrator --check-tools). — Run:  python -m eval.tool_check   |   python -m eval.tool_check --selfcheck"""
from backend.orchestrator import (
    tool_specs, _start_local_servers, router, build_model, DAEMON_MODEL,
    DEFAULT_SYSTEM, disabled_note, Session, _one_round,
)

# Contract T, LIVE: does the MODEL pick the right tool for a plain request? That is the one thing
# no offline check can reach — `backend.tools --selfcheck` proves the executor, and the backends were
# driven by hand, but "a model decided to call this" is the empty column in STATE's tool ledger.
#
# `want_arg` is (parameter, substring), matched case-insensitively, because a model may reasonably
# say "Spotify" or "spotify" and either is right. `want_tool = ""` means NO tool should be called —
# over-eager tool use is a real failure mode and needs a case, not an assumption.
_TOOL_CASES = [
    ("open Spotify",                  "open_app",      ("app", "spotify"),      "the plain case"),
    ("launch Notepad for me",         "open_app",      ("app", "notepad"),      "a different verb"),
    ("bring File Explorer to the front", "focus_window", ("title_query", "explorer"),
     "switching to something already open is NOT open_app"),
    ("pause the music",               "media_control", ("action", "play_pause"), "a media key"),
    ("turn the volume up",            "media_control", ("action", "volume_up"),  "...and a volume key"),
    ("mute the sound",                "media_control", ("action", "mute_toggle"), "...and mute"),
    ("what time is it",               "system_status", None,
     "a READ must stay a read — asking the time must never open or move anything"),
    ("what did I just copy",          "read_clipboard", None,   "the other read"),
    ("how are you today",             "",              None,
     "no tool fits, so none should be called (the over-eager failure)"),
    ("next track",                    "media_control", ("action", "next"),      "a bare command"),
    ("find the document about the lease", "find_document", ("query", "lease"),
     "a query the model must compose from the sentence, not a word it can lift"),

    # NEGATIVES. Precision over recall is the binding constraint on the router, and firing on one of
    # these is a worse failure than being slow: it acts against what the speaker meant. Each one
    # names a tool's subject WITHOUT asking for it, which is exactly the case a phrase matcher gets
    # wrong. One negative (`how are you today`) names nothing at all and so tests much less.
    ("I was going to open Spotify but I ran out of time", "", None,
     "reported intent, not a request — the case the whole precision rule exists for"),
    ("my sister works at Spotify",     "",              None,   "an app name inside ordinary talk"),
    ("I hate it when Notepad crashes", "",              None,   "a complaint that names an app"),
    ("tell me a joke about volume knobs", "",           None,   "a media word inside a request for prose"),
    ("what is the difference between RAM and a hard drive", "", None,
     "a general question that sounds technical but needs nothing from this machine"),
    ("remind me how daylight saving works", "",         None,
     "'remind me' looks like a command and asks for an explanation"),
]


def _verdict(want_tool, want_arg, got: str, args: dict, text: str, malformed: bool):
    """Score one round. Extracted so the single run and the sweep cannot drift apart — a suite
    scored two ways is how the format scoreboard came to be measured with a bent yardstick."""
    if malformed:
        return "FAIL", "the model produced a malformed tool call"
    if got != want_tool:
        detail = f"called {got or '(nothing)'}, wanted {want_tool or '(nothing)'}"
        if not got:
            # What it said INSTEAD is the whole diagnosis. "I can't know that" is an honest miss;
            # inventing a time is a lie; saying nothing at all is a broken turn. Three very
            # different faults that all print as "called nothing" without this.
            detail += f" — it said {(text.strip() or '(nothing at all)')[:140]!r}"
        return "FAIL", detail
    if want_arg and want_arg[1] not in str(args.get(want_arg[0], "")).lower():
        return "FAIL", f"{want_tool}({args}) — {want_arg[0]} should contain {want_arg[1]!r}"
    return "ok", (f"{got}({args})" if got else "answered without a tool")


def _check_tools() -> None:
    """Contract T, LIVE: put each `_TOOL_CASES` utterance to the real assistant model with the
    real tool list and check WHICH tool it asks for.

    Nothing is executed. `_one_round` surfaces the tool calls and never runs them, which is what
    makes this safe to run repeatedly — it will not open nine apps or change your volume.

    Connectors are read, never written: a case whose tool the user has switched off is SKIPPED and
    said so. Consent is the user's, and a test that flips it to make itself pass is worse than a
    test that admits it could not run.
    """
    import asyncio

    offered = {t["name"] for t in tool_specs()}
    print(f"tools offered to the model: {', '.join(sorted(offered)) or '(none)'}")
    missing = {c[1] for c in _TOOL_CASES if c[1]} - offered
    if missing:
        print(f"NOT offered, so their cases will skip: {', '.join(sorted(missing))}\n"
              f"  (turn the matching connector on in Settings > Connectors — Apps & media is off "
              f"by default)")

    _start_local_servers()          # so this runs without the daemon up, if the role is local
    model = router.build_for_role("assistant") or build_model(None, DAEMON_MODEL)
    system = DEFAULT_SYSTEM + disabled_note()
    rows, failures, skipped = [], 0, 0

    # Printed as each case LANDS, not batched at the end: every case is a full model round, so a
    # silent run looks hung for a minute or two on a local model (seen 2026-08-03).
    for n, (said, want_tool, want_arg, why) in enumerate(_TOOL_CASES, start=1):
        head = f"[{n}/{len(_TOOL_CASES)}] {said!r}"
        if want_tool and want_tool not in offered:
            print(f"skip {head}\n       {want_tool} is not switched on", flush=True)
            rows.append(("skip", said, f"{want_tool} is not switched on", why))
            skipped += 1
            continue
        print(f"...  {head}", end="\r", flush=True)      # overwritten by the verdict below
        session = Session(id="toolcheck", system=system,
                          history=[{"role": "user", "content": said}])
        text, calls, err, malformed, _usage = asyncio.run(_one_round(model, session, tool_specs()))
        if err and not malformed:
            print(f"SKIPPED — the assistant model is unavailable ({err})")
            return
        got = calls[0].name if calls else ""
        args = dict(calls[0].input or {}) if calls else {}

        verdict, detail = _verdict(want_tool, want_arg, got, args, text, malformed)
        failures += verdict == "FAIL"
        rows.append((verdict, said, detail, why))
        print(f"{verdict:4} {head}\n       {detail}\n       ({why})", flush=True)

    ran = len(_TOOL_CASES) - skipped
    print(f"\ntool-call check: {ran - failures}/{ran} run, {skipped} skipped, "
          f"model {getattr(model, 'model', '?')}")
    if failures:
        raise SystemExit(f"{failures} case(s) FAILED")

# The routing prompt under test. The assistant persona asks a model to BE not-hal holding tools, and
# a model in that seat reaches for them — measured 2026-08-04, the best of five still fired on one
# ordinary sentence in eight. This asks a narrower question, and applies four levers that are
# standard for intent classification:
#   1. frame the task as a decision about the utterance, not as a turn to take;
#   2. state the trigger conditions, since a description of what a tool DOES leaves when-to-call
#      to inference (the same lever that lifts under-triggering, run in reverse);
#   3. name the failure directly — mentioning a thing is not requesting it — with negatives drawn
#      from the calls the sweep actually got wrong, not invented ones;
#   4. make answering the explicit safe default, so "no tool" is a choice rather than an absence.
# Lives in eval/ because it is under test. It moves to backend/llm/base.py beside DEFAULT_SYSTEM if
# it is adopted.
ROUTER_SYSTEM = """You are a router. Your only job is to decide whether the user's words are a \
direct instruction to perform one of the actions available to you right now.

Call a tool only when all three are true:
- the speaker is telling you to do it, now;
- the action is one of the tools you have been given;
- you are confident enough to act without asking first.

Mentioning what a tool touches is not a request. Naming an application, a file, the volume, or \
the time does not ask you to act. None of these are instructions:
- "my sister works at Spotify" — names an application, asks for nothing
- "I was going to open Spotify but I ran out of time" — reports an intention, not a request
- "I hate it when Notepad crashes" — a complaint
- "tell me a joke about volume knobs" — asks for words, not an action

When no tool applies, reply in one short sentence and call nothing. Answering when you could have \
acted is a small cost. Acting when you were only being talked to is not."""


# Exemplars for the embedding router: an utterance is classified by which of these it sits nearest.
# DELIBERATELY DISJOINT from _TOOL_CASES — an exemplar set containing the test sentences would
# measure memory rather than generalisation, and would score 100% while proving nothing. No phrasing
# here appears in the suite.
#
# The "" class carries its own exemplars rather than being "far from everything". A negative like
# "my sister works at Spotify" sits lexically close to the open_app examples, so distance alone
# cannot separate it; what separates it is being nearer to other ordinary speech.
_INTENT_EXEMPLARS = {
    "open_app": ["start Chrome", "run the calculator", "get Word going", "boot up Slack",
                 "I need Excel open", "fire up the terminal"],
    "focus_window": ["switch to the browser window", "put Word in front",
                     "show me the Terminal I already have open", "go back to the window from before"],
    "media_control": ["skip this song", "turn it down a bit", "play something", "silence it",
                      "louder please", "go back a track", "stop the audio"],
    "system_status": ["what's the date", "how much battery is left", "which window am I in",
                      "what is the time right now", "am I plugged in"],
    "read_clipboard": ["what is on the clipboard", "show me what I copied",
                       "read back the thing I cut"],
    "find_document": ["look for the invoice from March", "search my files for the contract",
                      "where is the spreadsheet about budgets", "dig out the notes from the meeting"],
    "": ["what is the capital of France", "explain how compilers work",
         "I have been using Chrome all day", "she told me to try Notepad",
         "the volume on this laptop is terrible", "write me a poem about music",
         "I should probably close some windows", "my clipboard history is a mess",
         "do you think the battery on this thing is any good", "that document was a nightmare"],
}


def _cosine(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _embed_sweep(models: list[str]) -> None:
    """Score embedding models on the same cases as the generative sweep.

    One run per model: an embedding is a pure function of its input, so repeats measure nothing.
    What is scored here is ROUTING ALONE — which tool, or none. An embedding cannot extract
    `app="Spotify"`, so the generative `commands` column is a stricter bar that also required the
    arguments to be right.
    """
    import time as _t

    cfg = router.resolve("assistant") or {}
    endpoint = cfg.get("endpoint") or "127.0.0.1:11434"
    offered = {t["name"] for t in tool_specs()}
    cases = [c for c in _TOOL_CASES if not c[1] or c[1] in offered]
    labels, texts = [], []
    for intent, examples in _INTENT_EXEMPLARS.items():
        if intent and intent not in offered:
            continue
        for ex in examples:
            labels.append(intent)
            texts.append(ex)
    print(f"embedding sweep: {len(cases)} cases against {len(texts)} exemplars "
          f"over {len({l for l in labels})} classes\n")

    summaries = []
    for model_id in models:
        _clear_vram(endpoint)
        t0 = _t.perf_counter()
        try:
            ex_vecs = _ollama(endpoint, "/api/embed",
                              {"model": model_id, "input": texts}, timeout=180)["embeddings"]
            case_vecs = _ollama(endpoint, "/api/embed",
                                {"model": model_id, "input": [c[0] for c in cases]},
                                timeout=180)["embeddings"]
        except Exception as exc:
            print(f"=== {model_id} ===\n  unavailable: {type(exc).__name__} {exc}\n")
            continue
        took = _t.perf_counter() - t0
        gb, pct = _resident(endpoint, model_id)
        print(f"=== {model_id} ===")
        print(f"  {gb:.1f} GB, {pct:.0f}% in VRAM · {took:.1f} s · "
              f"{took / (len(texts) + len(cases)) * 1000:.0f} ms/embed")

        scores, wrong = {}, []
        for group, want_set in (("commands", True), ("negatives", False)):
            rows = [c for c in cases if bool(c[1]) is want_set]
            good = 0
            for said, want_tool, _wa, _why in rows:
                v = case_vecs[[c[0] for c in cases].index(said)]
                sims = sorted(((_cosine(v, e), lab) for e, lab in zip(ex_vecs, labels)),
                              reverse=True)
                got, best = sims[0][1], sims[0][0]
                # margin over the best exemplar of any OTHER class — the number a confidence
                # threshold would be set against.
                other = next((s for s, lab in sims if lab != got), 0.0)
                if got == want_tool:
                    good += 1
                else:
                    wrong.append(f"    {said!r}\n              -> {got or '(nothing)'}, "
                                 f"wanted {want_tool or '(nothing)'} (margin {best - other:+.3f})")
            scores[group] = 100 * good / len(rows)
            print(f"  {group:9} {good:4}/{len(rows):<4} ({scores[group]:.1f}%)")
        for w in wrong:
            print(w)
        summaries.append((model_id, gb, scores["commands"], scores["negatives"],
                          took / (len(texts) + len(cases))))
        _clear_vram(endpoint)
        print()
    _print_table(summaries)


# ---- the router-model sweep -------------------------------------------------------------------
# Which small local model should make the router's tool-call-or-prompt judgement? One run of nine
# cases cannot answer that: tool choice is sampled, so a miscall that shows up once in twenty is
# invisible at n=1 and is exactly the fault that matters. This repeats every case many times per
# model and reports the rate.

def _ollama(endpoint: str, path: str, body: dict | None = None, timeout: int = 60):
    """One call to Ollama's NATIVE api. The daemon speaks `/v1` and nothing else; this harness takes
    the exception because its subject is the runner itself — `/v1` ignores `keep_alive`, so an
    eviction cannot be asked for on the wire not-hal normally uses (tested 2026-08-02)."""
    import json as _json
    import urllib.request
    url = f"http://{endpoint.replace('localhost', '127.0.0.1')}{path}"
    req = urllib.request.Request(
        url, data=_json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _json.loads(r.read() or b"{}")


def _clear_vram(endpoint: str) -> None:
    """Evict every resident model and prove the runner holds nothing.

    Thomas's constraint, and it is not fussiness: a combined run cycling five models over 16 GB
    scored one model 7/9 where three isolated runs scored it 8/9 every time. Partial CPU offload
    under memory pressure changes the numerics, so a sweep that skipped this would measure memory
    pressure and report it as model quality.
    """
    for m in _ollama(endpoint, "/api/ps").get("models", []):
        _ollama(endpoint, "/api/generate", {"model": m["name"], "keep_alive": 0})
    left = [m["name"] for m in _ollama(endpoint, "/api/ps").get("models", [])]
    if left:
        raise SystemExit(f"could not evict {left} — refusing to measure through a partial offload")


def _resident(endpoint: str, model: str) -> tuple[float, float]:
    """(GB resident, % of it in VRAM). `size_vram` against total `size`: equal means wholly in VRAM,
    less means the runner spilled part of the model to CPU, which is the condition that scores the
    same model two ways. Size is half the verdict — a model that matches a larger one while holding
    less VRAM is the better router, so it is reported beside the accuracy, never separately."""
    for m in _ollama(endpoint, "/api/ps").get("models", []):
        if m["name"] == model or m["name"].startswith(model.split(":")[0]):
            size = m.get("size", 1)
            return size / 1e9, 100 * m.get("size_vram", 0) / size
    return 0.0, 0.0


def _sweep(models: list[str], runs: int, router_prompt: bool = False,
           temp_override: float | None = None) -> None:
    import asyncio
    import time as _t

    cfg = router.resolve("assistant") or {}
    endpoint = cfg.get("endpoint") or "127.0.0.1:11434"
    effort = cfg.get("effort")
    temp = cfg.get("temperature") if temp_override is None else temp_override
    offered = {t["name"] for t in tool_specs()}
    cases = [c for c in _TOOL_CASES if not c[1] or c[1] in offered]
    system = ROUTER_SYSTEM if router_prompt else DEFAULT_SYSTEM + disabled_note()
    print(f"sweep: {len(cases)} cases x {runs} runs = {len(cases) * runs} calls per model\n"
          f"       endpoint {endpoint}, effort {effort!r}, temperature {temp!r}, "
          f"prompt {'ROUTER' if router_prompt else 'assistant'}\n"
          f"       tools offered: {', '.join(sorted(offered))}\n")

    summaries: list[tuple] = []
    for model_id in models:
        _clear_vram(endpoint)
        model = build_model("ollama", model_id, endpoint, effort=effort, temperature=temp)
        print(f"=== {model_id} ===", flush=True)
        t0 = _t.perf_counter()
        # (fails, samples) per case; `samples` keeps the distinct wrong answers, which are the
        # diagnosis — a rate says a model misfires, only the wording says how.
        tally = {c[0]: [0, set()] for c in cases}
        for r in range(runs):
            for said, want_tool, want_arg, _why in cases:
                session = Session(id="sweep", system=system,
                                  history=[{"role": "user", "content": said}])
                try:
                    text, calls, err, malformed, _u = asyncio.run(
                        _one_round(model, session, tool_specs()))
                except Exception as exc:                  # a dead runner must not lose the sweep
                    tally[said][0] += 1
                    tally[said][1].add(f"error: {type(exc).__name__}")
                    continue
                if err and not malformed:
                    print(f"  {model_id} unavailable ({err}) — skipped")
                    tally = None
                    break
                got = calls[0].name if calls else ""
                args = dict(calls[0].input or {}) if calls else {}
                verdict, detail = _verdict(want_tool, want_arg, got, args, text, malformed)
                if verdict == "FAIL":
                    tally[said][0] += 1
                    tally[said][1].add(detail[:110])
            if tally is None:
                break
            print(f"    run {r + 1}/{runs}", end="\r", flush=True)
        if tally is None:
            continue
        took = _t.perf_counter() - t0
        gb, pct = _resident(endpoint, model_id)
        print(f"  {gb:.1f} GB, {pct:.0f}% in VRAM"
              + ("  PARTIAL OFFLOAD" if pct < 99 else "")
              + f" · {took:.0f} s · {took / (len(cases) * runs):.2f} s/call")
        # Positives and negatives are reported apart. A model that never fires scores well on
        # negatives and is useless; one that always fires scores well on positives and is dangerous.
        scores = {}
        for group, want_set in (("commands", True), ("negatives", False)):
            rows = [c for c in cases if bool(c[1]) is want_set]
            bad = sum(tally[c[0]][0] for c in rows)
            total = len(rows) * runs
            scores[group] = 100 * (total - bad) / total
            print(f"  {group:9} {total - bad:4}/{total:<4} ({scores[group]:.1f}%)")
        summaries.append((model_id, gb, scores["commands"], scores["negatives"],
                          took / (len(cases) * runs)))
        for said, want_tool, _wa, _why in cases:
            fails, samples = tally[said]
            if fails:
                print(f"    {fails:3}/{runs} FAIL  {said!r}")
                for s in sorted(samples)[:2]:
                    print(f"              {s}")
        _clear_vram(endpoint)
        print()

    _print_table(summaries)


def _print_table(summaries: list[tuple]) -> None:
    """One table, cheapest first. Size sits beside accuracy because it is half the verdict: two
    models that score alike are not equal if one holds a gigabyte more, and the router pays that
    VRAM for the life of the process."""
    if not summaries:
        return
    print(f"{'model':22} {'VRAM':>7} {'commands':>9} {'negatives':>10} {'s/call':>8}")
    for name, gb, cmd, neg, spc in sorted(summaries, key=lambda s: s[1]):
        print(f"{name:22} {gb:6.1f}G {cmd:8.1f}% {neg:9.1f}% {spc:8.3f}")


def _selfcheck() -> None:
    # The live tool-call suite (`--check-tools`) needs a model, so what is checkable offline is
    # that its cases are ANSWERABLE: every tool it names exists, every parameter it expects is
    # one that tool actually takes, and every enum value it wants is in the enum. Without this a
    # renamed tool or parameter reads as nine model failures rather than a stale test.
    from shared.config import load_schemas as _schemas
    _reg = {t["name"]: t for t in _schemas()["tools"]["tools"]}
    for _said, _want, _arg, _why in _TOOL_CASES:
        assert _want == "" or _want in _reg, f"_TOOL_CASES names an unknown tool: {_want!r}"
        if _arg:
            _props = _reg[_want]["parameters"]["properties"]
            assert _arg[0] in _props, f"{_want} takes no parameter {_arg[0]!r}"
            _enum = _props[_arg[0]].get("enum")
            assert _enum is None or _arg[1] in _enum, \
                f"{_want}.{_arg[0]} has no value {_arg[1]!r} (it allows {_enum})"
    print("tool_check selfcheck OK: every case names a real tool/param/enum")


if __name__ == "__main__":
    import sys
    if "--selfcheck" in sys.argv:
        _selfcheck()
    elif "--sweep" in sys.argv:
        # The dev trace in _one_round prints every delta, and a character outside the console
        # codepage raises UnicodeEncodeError that ends the turn (orchestrator.py:346, logged in
        # STATE). Replacing here keeps a model's own words from being scored as its failure. The
        # daemon still carries the bug; this only stops the harness inheriting it.
        sys.stdout.reconfigure(errors="replace")
        _a = [x for x in sys.argv[sys.argv.index("--sweep") + 1:] if not x.startswith("--")]
        _models = [m for m in _a[0].split(",") if m]
        _runs = int(_a[1]) if len(_a) > 1 else 25
        _temp = float(_a[2]) if len(_a) > 2 else None
        _start_local_servers()
        if "--embed" in sys.argv:
            _embed_sweep(_models)
        else:
            _sweep(_models, _runs, router_prompt="--router" in sys.argv, temp_override=_temp)
    else:
        _check_tools()
