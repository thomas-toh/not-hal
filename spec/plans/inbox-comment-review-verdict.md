# Review verdict — the comment sweep (staged, uncommitted)

Last reconciled: 2026-08-27 05:10

Independently verified: token-stream, JSON-structure and QML comment-strip proofs against HEAD all hold; residual grep for `spec/NN`, `DNN`, `Track X`, `docs/04`, `hard rule`, the author's name, VoiceInk and "this box" is empty across the sweep scope; all 24 checks pass; `overlay_check` passed 6/6 consecutive runs so the flake is not reproducible here. Every ticked comment-review item is applied. The sweep is sound. Fix the items below before commit.

## Must fix — a reason or fact was lost

- `backend/orchestrator.py:1030` — "RAM only" lost its reason. → "RAM only — never written to disk".
- `backend/tools.py:137` — "This is NOT a raw-shell tool" lost the prohibition. → "This is NOT a raw-shell tool, which the registry forbids".
- `backend/audio/listen.py:53` — `WHISPER_MODEL = "small.en"` lost that it was a chosen model, not a default. → one clause saying it was chosen for accuracy against speed on this hardware.
- `backend/hotkeys.py:179` — the Door redesign is parked, and the rewrite reads as scheduled work. → "needs the Door redesign (parked) that separates mechanism from policy".
- `shared/schemas/settings.json:179` — "a design constant" lost that it is a safety rule. → "a safety-rule constant".
- `frontend/overlay_check.py:188` — "Both readings show at once" lost its qualifier and now reads as always true. Restore "during a first run" or equivalent.
- `frontend/tray.py:1` — '"it is running"' has no referent. → '"the app is running"'.

## Must fix — user-visible text reads wrong

- Eight argparse descriptions and two more begin with a lowercase noun "app" ("app hotkeys — the two doors", "app orchestrator — the M0 loop", …): `listen.py:366`, `speak.py:309`, `wake.py:85`, `broadcaster.py:548`, `hotkeys.py:436`, `claude.py:316`, `compat.py:644`, `paste.py:191`, `orchestrator.py:1781`, `eval/replay.py:271`, `frontend/__main__.py:331`. "not-hal" is the product name and belongs in `--help`; put it back in these strings.
- `frontend/al/README.md:42` — "(the dev)" as an attribution. Drop the parenthetical.
- `eval/latency.py:48` — printed "the dev's voice" → "a recorded voice", as `replay.py` already says.

## Missed by the sweep

- `pyproject.toml:14,22,32,33,53` — five citations (`spec/20, D30`, `D10`, `D23`, `D20`, `D27`). Tracked and shipped; was outside the scope list.
- `backend/__init__.py:1` — still cites `spec/`.
- Second-tier citations left in place across edited files: `M0`/`M1`/`M2`, `S-06`, `X-01`, `P-02`, `B-02`, `G-05`, `C2`/`C3`, `Layer 2`, `step 5/6` (`llm/__init__.py:2`, `claude.py:1`, `base.py:66,236`, `broadcaster.py:57`, `compat.py:57`, `hotkeys.py:197`, `checks.yml:82`, `run.py:24`, `router.py:17`, `tray.py:1`, `settings.json:109,130,144`). Same class as C1; either sweep them under the same rule or record that they stay.
- "not-hal" still in prose at `app_aliases.json:2`, `settings.json:2,109,244,310`, `tools.json:3`, `status.json:59`, `al/README.md:42`, `backend/__init__.py:1` — inconsistent with the "the app" rule the sweep applied elsewhere. Either is fine; pick one.

## Noted, no action

- Dangling parentheticals after a citation was cut: `orchestrator.py:193,620,1681`, `tools.py:138,405` ("a sanctioned Windows backend" — sanctioned by whom), `compat.py:9` ("the B2 row was widened" — row of what). Polish.
- Pre-existing stale comments the sweep preserved: `orchestrator.py:86-90` and `llm/providers.py:15` say the router is unbuilt; `backend/router.py` exists.
- `overlay_check.py:385` flake: 0/6 here, 1/5 in the sweep session. Leave the STATE note.
