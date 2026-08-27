# Handoff — execute the comment review

Last reconciled: 2026-08-27 03:50

Read `CLAUDE.md`, then `spec/comment-review.md` in full. That file is the decision record: every ticked box is an accepted rewrite, every class decision C1–C5 is a rule to apply everywhere. Nothing in it has been applied yet — 518 citations still sit in the code.

## Scope

All Python, QML and JSON under `backend/`, `frontend/`, `shared/`, `eval/`, and `run.py`. Comments, docstrings and `$comment` strings only. Code behaviour does not change; any diff line that is not a comment, docstring or `$comment` value is a mistake.

## Work, in order

1. **Class decisions, mechanically.** C1 and C3: strip `spec/NN`, `spec/NN §x`, `DNN`, `Track X`, `STATE`, `ROADMAP #n` citations and keep the reason the citation was standing in for; if the citation *was* the whole comment, write the reason from the surrounding code or delete the line. C2: `docs/04` pointers deleted, sentence kept. C4: never cite a hard rule; state the reason. C5: `ponytail:` markers stay.
2. **The ticked line items** in Batch 1, exactly as written after each `→`.
3. **E — VoiceInk.** Remove the reference to VoiceInk and any other shipped app throughout the code (`orchestrator.py:106`, `:112`, and grep for others).
4. **Batches 2–4 were never written.** Read `frontend/`, `shared/`, `eval/` and `run.py` for the same five classes (A: the author's name or machine · B: chat-log phrasing such as "the review flagged" · C: comments about what an older comment said · D: privacy optics · E: named third-party products). Apply A–D directly under the same rules; anything in class E goes to the user first.
5. **Verify.** Run every command in `.github/workflows/checks.yml` offline. All must pass. Then `grep -rEn "spec/[0-9]{2}|hard rule [0-9]|docs/04|\bD[0-9]{2}\b|Thomas|VoiceInk|this box" backend frontend shared eval run.py` must return nothing that is not a false positive; list any it does.

## Method

Files are independent, so fan out one subagent per file (or per directory for small ones) with the rules above pasted in; no worktree is needed because scopes do not overlap. Review each agent's diff before moving on — the failure mode is an agent that rewrites a comment's meaning while stripping its citation.

## Gates

No commit without the diff and message shown and an explicit OK. When done, delete `spec/comment-review.md` and this file in the same commit, and note the sweep in `STATE.md`.
