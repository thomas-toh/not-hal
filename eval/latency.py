"""Latency eval (was backend.orchestrator --check-latency). — Run:  python -m eval.latency [RUNS]   |   python -m eval.latency --selfcheck"""
import math

from backend.orchestrator import (
    transcribe, router, build_model, DAEMON_MODEL, card, tool_specs,
    DEFAULT_SYSTEM, disabled_note, Session, _one_round, _start_local_servers,
)

# The latency suite, added 2026-08-03. Built for VOLUME and reported at the TAIL, because the
# fault it exists to catch is rare: one run in thirty took 14.58 s with reasoning already off, and
# a median hides that completely. It times the two stages a user actually waits on — speech-to-text
# and one model round — because a slow tail in STT feels identical to a slow tail in the model and
# nothing has ever been able to tell them apart (STT's numbers exist only as single observations
# scattered through the log).
LATENCY_UTTERANCE = "what time is it"


def _spread(times: list[float]) -> str:
    """One line of distribution. Percentiles, never a mean: the mean is exactly the statistic that
    hides a rare spike, which is the only thing this suite is looking for."""
    s = sorted(times)
    n = len(s)

    def pct(p):                       # nearest-rank, so p95 is always an OBSERVED value
        return s[min(n - 1, max(0, math.ceil(p / 100 * n) - 1))]

    return (f"n={n:4}  min {s[0]:6.2f}  p50 {pct(50):6.2f}  p95 {pct(95):6.2f}  "
            f"p99 {pct(99):6.2f}  max {s[-1]:6.2f}  (spread {s[-1] - s[0]:.2f} s)")


def _check_latency(runs: int) -> None:
    """Time speech-to-text and one model round, many times, and report the tail.

    Runs default LOW for a cloud provider and high for a local one: a sweep worth having is
    hundreds of calls, and hundreds of calls carrying the full tool list is real money on a
    metered key. The estimate is printed before anything is spent.
    """
    import time as _t
    from pathlib import Path

    import numpy as np

    print(f"utterance: {LATENCY_UTTERANCE!r}\n")

    # --- speech-to-text: the same real WAV, repeatedly. Never measured as a distribution before.
    wav = Path(__file__).resolve().parent / "replay" / "wav" / "key_short.wav"
    if not wav.exists():
        print(f"STT   skipped — no {wav.name} (the replay WAVs are a recorded voice, gitignored)")
    else:
        import wave as _w
        with _w.open(str(wav), "rb") as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        audio = pcm.astype("float32") / 32768.0
        transcribe(audio)                        # warm the model out of the measurement
        times, texts = [], set()
        for _ in range(runs):
            t0 = _t.perf_counter()
            texts.add(transcribe(audio))
            times.append(_t.perf_counter() - t0)
        print(f"STT   {_spread(times)}")
        # Same audio in, same words out — a wobble here would mean the timing above is comparing
        # different work, not the same work at different speeds.
        print(f"      {len(texts)} distinct transcript(s): {sorted(texts)[0][:60]!r}")

    # --- one model round, through the router, exactly as a turn builds it.
    cfg = router.resolve("assistant")
    model = router.build_for_role("assistant") or build_model(None, DAEMON_MODEL)
    where = card(cfg["provider"]).get("where", "?") if cfg else "?"
    tools = tool_specs()
    est = runs * 2000                            # ~2k input tokens a round, tool list included
    print(f"\nmodel: {cfg}\n       {where}, {len(tools)} tools, ~{est:,} input tokens for {runs} runs"
          + ("  <-- METERED" if where == "cloud" else ""))

    r = latency_sweep(model, tools, DEFAULT_SYSTEM + disabled_note(), runs)
    print(f"\nmodel {_spread(r['times'])}")
    print(f"      tool call {r['tooled']}/{runs} · answered WITHOUT a tool {len(r['answered'])}"
          f"/{runs} · empty {r['empty']}/{runs} · errors {r['failed']}/{runs}")
    if r["answered"]:
        # For a question only a tool can answer, answering without one IS the failure — and the
        # samples are the evidence, because "it answered" and "it answered correctly" look the
        # same in a latency table.
        print(f"      answered-without-a-tool samples: {r['answered'][0][:90]!r}")
        print(f"                                       {r['answered'][-1][:90]!r}")


def latency_sweep(model, tools, system: str, runs: int) -> dict:
    """`runs` rounds of LATENCY_UTTERANCE through `model`. Returns timings AND what it did.

    Separated from the command so a measurement session can drive it across a matrix of models
    without going through the settings window between each — and so timing and correctness are
    always collected together. A suite that timed 200 rounds beautifully while never noticing the
    model was inventing the answer would be measuring the wrong thing precisely (2026-08-04).
    """
    import time as _t

    out = {"times": [], "tooled": 0, "empty": 0, "failed": 0, "answered": []}
    for _ in range(runs):
        session = Session(id="lat", system=system,
                          history=[{"role": "user", "content": LATENCY_UTTERANCE}])
        t0 = _t.perf_counter()
        text, calls, err, _malformed, _usage = _run_one(model, session, tools)
        out["times"].append(_t.perf_counter() - t0)
        if err:
            out["failed"] += 1
        elif calls:
            out["tooled"] += 1
        elif text.strip():
            out["answered"].append(text.strip())
        else:
            out["empty"] += 1
    return out


def _run_one(model, session, tools):
    """One round, synchronously — `_one_round` is async and this suite is a script."""
    import asyncio
    return asyncio.run(_one_round(model, session, tools))

def _selfcheck() -> None:
    # The latency suite reports the TAIL, so its percentiles have to be right — a p95 that quietly
    # averaged would hide exactly the rare spike the suite exists to find. Nearest-rank, so every
    # figure is an OBSERVED value, never an interpolation between two runs that never happened.
    _line = _spread([float(i) for i in range(1, 101)])
    for _want in ("n= 100", "min   1.00", "p50  50.00", "p95  95.00", "p99  99.00", "max 100.00"):
        assert _want in _line, f"percentile drift: {_want!r} not in {_line!r}"
    # One spike in four must SURFACE at p95 and in the spread, not be smoothed away.
    _spike = _spread([1.0, 1.0, 1.0, 14.58])
    assert "p95  14.58" in _spike and "spread 13.58" in _spike, _spike
    assert _spread([2.0]).count("2.00") >= 4, "a single sample is its own min/median/max"
    print("latency selfcheck OK: nearest-rank percentiles; a 1-in-4 spike surfaces at p95")


if __name__ == "__main__":
    import sys
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        _start_local_servers()
        _rest = [a for a in sys.argv[1:] if not a.startswith("-")]
        runs = int(_rest[0]) if _rest else 0
        if not runs:
            cfg = router.resolve("assistant")
            local = bool(cfg) and card(cfg["provider"]).get("where") == "local"
            runs = 200 if local else 20
        _check_latency(runs)
