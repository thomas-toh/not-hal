"""Word-replacement: a deterministic find-and-replace over a transcript,
applied BEFORE the LLM cleanup. The exact way to fix acronyms, names and jargon the STT
reliably mishears — a lookup, not a model guess (the deterministic-first principle).

The table is shared/schemas/word_replacements.json (the schema is the truth, this
loads it), user-curated. An empty list is a no-op, so dictation is unaffected until the user
adds entries, and cleanup being off does not skip it — deterministic fixes always apply.
"""
from __future__ import annotations

import re

from shared.config import load_schemas


def _compile(replacements: list[dict]) -> list[tuple[re.Pattern, str]]:
    """(pattern, to) pairs, in listed order. Whole-word (\\b), case-insensitive, `from`
    regex-escaped. Empty/malformed entries are skipped rather than thrown — a bad hand-edited
    table must never break dictation.
    ponytail: \\b suits alphanumeric acronyms/names/phrases (the case). A `from` that starts or
    ends with punctuation (".NET", "C++") won't match cleanly; switch to lookarounds if that
    ever comes up."""
    out = []
    for r in replacements:
        frm = r.get("from", "")
        if not frm:
            continue
        out.append((re.compile(rf"\b{re.escape(frm)}\b", re.IGNORECASE), r.get("to", "")))
    return out


def apply(text: str, table: list[dict] | None = None) -> str:
    """Apply the word-replacement table to `text`. `table` defaults to the shipped schema; the
    selfcheck passes its own. `to` is substituted literally (via a function) so a replacement
    containing backslashes or `\\g<0>` can't be read as a regex backreference. Sequential — a
    later entry can see an earlier one's output; for a small curated table that is the curator's
    call.
    ponytail: recompiles per call. Dictation runs once per utterance over a tiny table, so a
    cache would buy nothing measurable — add one keyed on id(table) only if it ever shows."""
    if table is None:
        table = load_schemas()["word_replacements"]["replacements"]
    for pat, to in _compile(table):
        text = pat.sub(lambda _m, to=to: to, text)
    return text


def _selfcheck() -> None:
    tbl = [
        {"from": "jon smith", "to": "Jon Smyth"},
        {"from": "api", "to": "API"},
    ]
    # Whole-word, case-insensitive, replaced with `to` exactly.
    assert apply("the api call", tbl) == "the API call"
    assert apply("ApI rocks", tbl) == "API rocks"
    # No partial-word matches: 'api' inside 'therapist'.
    assert apply("therapist", tbl) == "therapist"
    # Multi-word phrase matches as a unit.
    assert apply("call jon smith today", tbl) == "call Jon Smyth today"
    # `to` is literal — no group-reference interpretation of backslashes.
    assert apply("path", [{"from": "path", "to": r"C:\1 & \g<0>"}]) == r"C:\1 & \g<0>"
    # Empty table and empty `from` are no-ops.
    assert apply("unchanged", []) == "unchanged"
    assert apply("keep this", [{"from": "", "to": "x"}]) == "keep this"
    # The shipped schema loads and is well-formed. It ships EMPTY — the table is the user's to
    # curate — so the guarantee worth pinning is that an untouched install changes nothing.
    shipped = load_schemas()["word_replacements"]["replacements"]
    assert isinstance(shipped, list)
    assert apply("nothing here is rewritten", shipped) == "nothing here is rewritten"

    print("replace selfcheck OK: whole-word case-insensitive literal replacement, phrases, "
          "no partial matches, empty no-op, shipped schema loads")


if __name__ == "__main__":
    _selfcheck()
