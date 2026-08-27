"""D37 format-quality eval (was backend.orchestrator --check-format). — Run:  python -m eval.format_check   |   python -m eval.format_check --selfcheck"""
from backend.orchestrator import (
    router, build_model, CLEANUP_PROVIDER, CLEANUP_MODEL, transform, DICTATION_CLEANUP,
)

# D37 (spec/60): what the spoken list commands must and must not do, as transcripts the STT would
# actually produce — no punctuation, spelled-out counting. The last two are the point of the whole
# feature: dictating ABOUT a list must stay prose.
_FORMAT_CASES = [
    ("enumerate list one buy milk two collect the dry cleaning three call the bank end list "
     "then I went home",
     ["1.", "2.", "3."], ["4.", "enumerate", "end list"],
     "numbered list, then the tail returns to prose (not a fourth item)"),
    ("itemize list one milk two eggs three bread end list",
     ["- "], ["1.", "2.", "itemize"],
     "itemize gives bullets despite the spoken counting"),
    ("enumerate list one buy two apples two get milk end list",
     ["1.", "2.", "two apples"], ["3."],
     "only the NEXT ordinal separates — a number inside an item is content"),
    ("please add a numbered list to the contract before we send it",
     [], ["1.", "- "],
     "TALKING ABOUT a list must not become one (the D37 failure mode)"),
    ("I asked them to itemize the costs in the schedule",
     [], ["1.", "- "],
     "...including a command verb used as an ordinary verb"),
    # The four below were live FAILURES on the first cut of the prompt (30-case sweep, 2026-07-30).
    # They are the regressions worth guarding: the shipped mention cases above all PASSED while
    # these broke, because none of them contains the trigger phrase word for word.
    ("the statute requires us to enumerate list items in schedule two",
     ["schedule two"], ["1.", "- "],
     "the trigger VERBATIM inside a sentence doing something else is prose"),
    ("he told me to itemize list everything before Friday",
     ["everything before Friday"], ["1.", "- "],
     "...and in reported speech"),
    ("I need to do three things one call the bank two send the email three go home",
     ["three things"], ["1.", "- "],
     "counting with NO command must stay prose — the most natural false positive"),
    ("list one is the priority list two can wait",
     ["list one"], ["1.", "- "],
     "bare ordinals must not format, and must not swallow the speaker's words"),
]


def _format_verdict(out: str, want: list[str], unwanted: list[str]) -> tuple[list[str], list[str]]:
    """Judge one _FORMAT_CASES result: (what's missing, what shouldn't be there).

    Both sides compare case-INSENSITIVELY. They did not until 2026-08-01, and the asymmetry was
    a real bug: `want` was matched against the raw output while `unwanted` was matched against a
    lowercased one, so a model that CAPITALISED a wanted phrase was marked failed. The prompt
    *requires* capitalisation, so the suite was penalising correct behaviour — qwen3.5:9b lost
    two cases to it (`schedule two` -> `Schedule Two`, `list one` -> `List one`), both with
    perfectly correct output. Any earlier scoreboard is suspect for the same reason.

    These cases test STRUCTURE — did a list appear, did the speaker's words survive. Casing is
    cleanup fidelity and belongs to a check that looks at it directly, not to a substring match
    that happens to be sensitive to it."""
    low = out.lower()
    return ([w for w in want if w.lower() not in low],
            [u for u in unwanted if u.lower() in low])


def _check_format() -> None:
    """D37, LIVE: run the list commands through the real `cleanup_dictation` model (spec/60).
    Detection is prompt-side, so this is the check that actually proves it — the offline selfcheck
    can only prove the prompt still says so. Skips rather than fails when the cleanup engine is
    unreachable, so it stays runnable on a machine with no key."""
    import asyncio

    model = (router.build_for_role("cleanup_dictation")
             or build_model(CLEANUP_PROVIDER, CLEANUP_MODEL))
    failures = []
    for said, want, unwanted, why in _FORMAT_CASES:
        out, err = asyncio.run(transform(model, said, DICTATION_CLEANUP))
        if err:
            print(f"SKIPPED — cleanup engine unavailable ({err.kind}: {err.detail})")
            return
        missing, present = _format_verdict(out, want, unwanted)
        ok = not missing and not present
        failures += [] if ok else [why]
        print(f"\n{'ok  ' if ok else 'FAIL'} {why}\n  said: {said}\n  got:  {out!r}")
        if missing:
            print(f"  missing: {missing}")
        if present:
            print(f"  must not contain: {present}")
    if failures:
        raise SystemExit(f"\n{len(failures)} of {len(_FORMAT_CASES)} format cases FAILED")
    print(f"\nformat check OK: {len(_FORMAT_CASES)} cases, including the two mention cases")

def _selfcheck() -> None:
    # D37 scoring (offline half): the live run needs a model, but the VERDICT is pure. Both sides
    # must be case-insensitive — the asymmetry that existed until 2026-08-01 failed models for
    # capitalising a wanted phrase, which the prompt requires them to do.
    assert _format_verdict("List one is the priority.", ["list one"], ["1.", "- "]) == ([], []), \
        "a wanted phrase must still match once the model capitalises it"
    assert _format_verdict("Schedule Two.", ["schedule two"], []) == ([], [])
    assert _format_verdict("1. Buy milk", [], ["1."]) == ([], ["1."]), "a real list is still caught"
    assert _format_verdict("prose only", ["absent"], []) == (["absent"], []), \
        "a genuinely missing phrase must still be reported"
    assert _format_verdict("ENUMERATE LIST", [], ["enumerate"]) == ([], ["enumerate"]), \
        "an unwanted phrase must be caught whatever its case"
    print("format_check selfcheck OK: scoring is case-insensitive both ways")


if __name__ == "__main__":
    import sys
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        _check_format()
