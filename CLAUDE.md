# Instructions for LLMs

## Hard rules to comply with in this project without deviation ##

The following rules set out inviolable rules that all LLMs must comply with. Your goal is to minimize strikes, and if a strike has been made, to add session-specific corrections to reinforce the failed behavioural gateway. A session that accumulates more than 20 strikes would be terminated upon commit.

1. **Rule 1 - Gating and permissions:** User approval is inviolable. When something is (i) expressed to be gated to user approval, or (ii) an answer to aquestion, or (iii) a suggestion or option, or (iv) a decision flagged as the user's, your ONLY response is to STOP, ask, then WAIT for EXPRESS go-ahead. Do NOT implement, edit, or "just start" without EXPRESS go-ahead. Silence does not mean permission. You CANNOT answer your own questions is not permission. You will ONLY answer to an explicit "yes", "go ahead", "do it", or cognate expressions of consent for that precise decision. This overrides every other instinct in this file. A failure to comply with any of this is a STRIKE.
   
2. **Rule 2 - Structure of replies:** When responding to the user, ONLY use simple language without jargon. If there are options, propose them, state your recommendation, and explain why. DO NOT use technical jargon or hide behind confusing explanations. A confusing explanation which the user says he does not get is a STRIKE. Where a complex or technical term is used, start out your explanation defining those terms. An undefined term is a STRIKE.

3. **Rule 3 - Review gate on git:** User must review the diff, the proposed commit message and get user explicit OK before any `git commit`. An LLM will never `git push` without explicit approval. A failure to comply with any of this is a STRIKE. 

4. **Rule 4 - Edit to the moving repo:** User's codebase is expected to shift quickly in prototyping phase. When coding:
   - DO NOT preserve backward compatibility. Choose the simplest, cleanest implementation that entirely meets the current requirements. Refactor as required, but gate all large-scale refactors > 250k tokens by seeking permissions from the user.
   - DO NOT code patches over errors. You are NOT supposed to use janky fixes or "just for now" fixes in order to meet the user's requirements.
   - DO NOT be lazy. When the user states an outcome, building half "for now" is not meeting it. Build features fully and faithfully.
   - Minimalism governs the ROUTE, not the DESTINATION. Reaching for an existing tool, the standard library or a native feature before writing new code is right; arriving at less than what was asked for is not. YAGNI covers work nobody has requested yet — speculative abstractions, scaffolding "for later" — and never licenses trimming the stated requirement. Where a `ponytail` session mode says otherwise, this bullet wins.
   - DO curiously investigate faults, bugs, contradictions or issues.
   - Prefer established, well-maintained libraries over custom implementations.
   A failure to comply with any of this is a STRIKE.

5. **Rule 5 - Spec discipline:** `spec/docs/` represents the eventual published documentation of the app's behaviour. It is expected to be human-readable. Important *architectural* decisions go into `spec/docs/` (i.e., decisions which affect the overall structure of the app at a high level). Decisions on minor points are NOT to be promoted to `spec/docs/`, but recorded in `spec/NOTES.md/`. When making changes to `spec/docs/` or `spec/NOTES.md`, the change must be recorded *in the same commit*.
   - Every file in `spec/` carries a `Last reconciled:` date. This is to be updated with the DATE and TIME of the last modification. 

6. **Rule 6 - Progress tracking:** Project progress is recorded in `spec/plans/`, particularly in `spec/plans/ROADMAP.md` and ``spec/plans/STATE.md`.
   - `ROADMAP.md` records high-level progress of the project. High-level architectural progress is provided here. For the upcoming sessions, it would contain checklists of outstanding tasks. Upon session start, ALWAYS refer to ROADMAP.md and STATE.md.  
   - `STATE.md` is the record of build progress minutae. `spec/docs/` files never state how built something is. Sections describing unbuilt behaviour are tagged inline (e.g., `planned, M1`). Superseded decisions are DELETED, with persistent decisions ported into new decisions.

7. **Rule 7 - Schemas are executable:** `shared/schemas/*.json` are loaded by the code at runtime. Never duplicate their contents into code or prose — import/reference them. Adding a tool, earcon, or message type means editing the schema file, not scattering literals.

7. **Rule 8 - Draft discipline:** All agentically updated files - `CLAUDE.md`, `STATE.md`, and `ROADMAP.md`, should be kept thin. Be careful where you put information. Decisions which just reflect certain choices but do not constitute project-important decisions belong in NOTES. If not, it belongs in `spec/`.

8. **Rule 9 - Prose discipline:** Written work is for a reader, not performed at one. State propositions directly.
   - NO contrast constructions: "not X, but Y" / "it is Y, not X" / "not merely X". Say what is true.
   - NO rhetorical lead-ins that announce an explanation before delivering it ("the arithmetic decides it", "that fact sets the design", "here is the thing"). Signpost the reason plainly — "on the basis that", "because" — then state it.
   - NO dramatic register and NO aphoristic closers. A paragraph ends when the fact is delivered.
   - NO provenance stamps or cross-reference trails — "settled 2026-08-04, recorded in spec/20", "as decided above", "see § X for the full account". Nobody reads them. Record the point being made.
   - When adding to an EXISTING document, match its structure, density and voice. Do not insert a block that breaks its formatting. The user's beautification is not yours to overwrite.
   - If the explanation is longer than the thing it explains, cut the explanation.
   A failure to comply with any of this is a STRIKE.
## Repo map

```
CLAUDE.md          ← you are here; keep THIN (index + rules only, never the spec itself)
README.md          ← install / run notes
backend/           ← Python daemon: audio · orchestrator · brains · tools — the voice loop (build status in spec/plans/STATE.md)
frontend/          ← the overlay (component P) — PySide6/QML front-end on Contract P (D19); the spine (D23)
shared/            ← code + data BOTH sides import, never one-sided:
  config.py           repo-root locator + schema loader
  settings.py         user settings file — frontend writes, backend reads
  log.py              logging setup
  schemas/            EXECUTABLE truth — JSON the code imports (never copy values into code)
eval/              ← perf/quality harnesses, NOT in CI (need a key / recorded audio):
  latency.py · format_check.py · tool_check.py · b1_smoke.py
  replay.py + replay/ recorded-WAV replay through the real pipeline (WAVs untracked)
spec/              ← CURRENT TRUTH of the system
  docs/               the published spec — read the relevant file before working on an area:
    [tbc]          ← To be populated once we've settled what these contain
  plans/              ROADMAP.md (progress + next-session checklists) · STATE.md (build minutiae) — read at session start
  NOTES.md            minor operational decisions (topic-keyed, lean); never required reading
docs/              ← frozen decision records (01 scoping · 02 architecture · 04 bridge). Never retro-edited.
run.py             ← launcher: spawns the daemon (backend) + overlay (frontend)
sandbox/           ← UI mockups & spikes (not shipped)
```

## Other guiding methods when working with the user

- Be a constructive collaborator, not a validator: if something is wrong, overstated,
  or a misused term, say so plainly and offer the better path.
- When the user instructs you not to do something, do not over-interpret as a guardrail
  and include in specs — leads to bloating. Undo the change, then gate with a question
  whether a generalised rule is required. When in doubt, preference having no generalised
  rule.
- Keep a running task list on multi-step work.
- While working through a task, maintain a log of "modified files" for that task. Then, 
  for presentation completion of multi-step work: state at the end a "Changed files" 
  section, list all files which were modified since the last commit by that session.