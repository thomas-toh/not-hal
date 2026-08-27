# Handoff — to the orchestrator session

Last reconciled: 2026-08-27 04:20

Two things. Code is ready for you to review. `spec/` is mid-rewrite and is not yours to touch.

---

## (i) Code is ready for review

A publication sweep ran over every comment, docstring, `$comment` value and developer-facing string in the repo. **55 tracked files, 786 insertions, 788 deletions, all staged, nothing committed.** Behaviour does not change.

### What changed

- **Internal citations removed.** `spec/NN`, `DNN`, `Track X`, `STATE`, `ROADMAP`, `docs/04`, and "hard rule N" are gone from comments, docstrings and `$comment` values across `backend/`, `frontend/`, `shared/`, `eval/`, `run.py` and `.github/workflows/checks.yml`. The reason each citation stood for is kept in the sentence. `ponytail:` markers are untouched.
- **The same citations removed from string literals.** 44 sites: 12 `argparse` descriptions, one flag help string, 4 selfcheck `print()` summaries, one `log.info`, 20 assert messages, and one display-only data string. These change `--help` output and failure text. Nothing matches on their contents, which was checked before editing.
- **JSON prose swept.** The `description`, `clock` and `meaning` fields in `status.json`, `targets.json`, `earcons.json` and `word_replacements.json`. No code reads any of them, which was checked first. `tools.json` descriptions ARE read at runtime and were left alone.
- **The dev's name is gone** from every file the project writes, including gitignored ones. The convention is now `the dev` for whoever builds this and `the user` for whoever runs it.
- **`not-hal` reduced in prose.** 32 uses in comments and docstrings became "the app". 15 literal uses stayed, because renaming them breaks something real — `KEY_SERVICE = "not-hal"` in `shared/config.py:17` is the OS credential-store service name, and the keyring error strings are what a user reads in Credential Manager.

### How it was verified

- **Comment-only proof.** Each file was compared against `HEAD` by meaning, not text: Python by token stream with docstrings allowed to differ, JSON by parsed structure, QML by comment-stripped source. The only non-comment changes are the intended ones.
- **JSON structure proof.** The parsed trees were walked key by key. 56 changed values, every one a prose key. No key added or removed, no runtime value touched.
- **All 24 checks in `.github/workflows/checks.yml` pass**, run offline.

### One real finding, unfixed

`frontend/overlay_check.py:385` is flaky. The assertion `a fully-hidden boot island must clear booting and the latch` failed once in five full suite runs and passed on every isolated re-run. It is a timing assertion. This predates the sweep, which changed only text. It is recorded in `STATE.md` and left alone.

### Open, awaiting a decision

- **~170 `spec/00`–`spec/70` shorthand citations inside `spec/` prose.** Deliberately left. These are the spec's internal cross-references, not code citations. `spec/70` is the odd one: there is no 70 document, so its 31 sites are genuinely dead.
- **Two review documents survive with the dev's name in them** — `spec/comment-review.md` and `spec/plans/handoff-comment-review.md`. The name appears inside quotations of the lines that were removed, so sweeping them would destroy the record. Both were meant to be deleted on completion; `spec/` is gitignored, so deleting them is not recoverable through git.
- **`logs/*.log` still carry the old `Thomas Smith` selfcheck fixture.** Gitignored runtime artifacts that regenerate.

### The drafted commit message

    Strip internal citations and personal references from shipped comments

    Applies the accepted comment-review decisions across backend/, frontend/,
    shared/, eval/, run.py and checks.yml.

    Internal citations (spec/NN, DNN, Track X, STATE, ROADMAP, docs/04, "hard
    rule N") are out of comments, docstrings, $comment values and the
    developer-facing strings; the reason each stood for is kept. The author's
    name is replaced by "the dev" throughout, and "not-hal" gives way to "the
    app" in prose while staying wherever it is a literal identifier.

    Comments, docstrings and $comment values only, except for 44 string
    literals and four JSON prose fields swept on purpose. Verified by
    comparing token streams (Python), parsed structure (JSON) and
    comment-stripped source (QML) against HEAD. All 24 checks in
    .github/workflows/checks.yml pass.

**No commit without the dev seeing the diff and saying so.**

---

## (ii) Stay out of `spec/`

A prose rewrite is in flight. The plan is `spec/plans/prose-rewrite.md`.

**Do not edit any file under `spec/`.** That includes `STATE.md` and `ROADMAP.md`, which the session rules normally have you update alongside your work.

The reason is that the rewrite is a voice change across whole documents, and a correctly-written paragraph appended mid-flight still has to be re-read and re-fitted. Concurrent edits make the review round meaningless.

**If your work produces something that belongs in `spec/`**, write it to `spec/plans/inbox-<topic>.md` as a new file and say so in your summary. New files are safe; edits to existing ones are not. The dev folds the inbox in when the rewrite reaches that document.

`spec/` is gitignored, so nothing there will show up in your `git diff` or your commits. That makes an accidental edit silent. Treat the directory as read-only.

Reading `spec/` is fine and still expected at session start.
