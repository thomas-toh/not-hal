# Handoff — Mac port, stage 0

Last reconciled: 2026-08-27 05:35

Read `CLAUDE.md`, then `spec/plans/mac-port.md` in full. You are building **stage 0 only**: make the Windows build boot cleanly under macOS with every Windows-only call guarded, and add the packaging and CI groundwork. No Mac backend is written in this stage; a Mac branch written on Windows is untested code and is not wanted.

## File scope

`pyproject.toml`, `install.sh` (new), `.github/workflows/checks.yml`, `run.py`, `backend/hotkeys.py`, `backend/paste.py`, `backend/tools.py`, `backend/llm/providers.py`, `frontend/__main__.py`, `frontend/tray.py`, `frontend/overlay_check.py`. Nothing else. A comment-sweep session may be committing at the same time; if `git status` shows staged changes you did not make, do not stage, unstage or commit anything — report and stop.

## Work

1. `pyproject.toml`: `sys_platform != "darwin"` markers on the CUDA wheels and the `gpu-cuda` extra. Nothing else in the file changes.
2. `install.sh`: the POSIX peer of `install.bat` — venv, `pip install -e .`, no CUDA. Short.
3. `checks.yml`: add a `macos-latest` leg to the matrix running the same steps; keep `windows-latest` as the reference.
4. Guard every Windows-only call listed under "What needs a second backend" and "Deletions on Mac" in `mac-port.md` so that on `sys.platform == "darwin"` it is skipped and the feature degrades the way the tools already do (a logged line, or a spoken sentence where one exists). Most already carry a guard; the job is the ones that do not: DWM corners, AppUserModelID, `IslandHitTest`, `DismissKey`, the long-path check, `winreg` in `tray.py`.
5. Verify: all 24 checks green on Windows, unchanged output. Then `python -c "import sys; sys.platform='darwin'; import frontend.__main__, backend.tools, backend.hotkeys, backend.paste, frontend.tray"` imports without error.

## Gates

No commit without the diff and message shown and an explicit OK. Record nothing in `STATE.md` or `ROADMAP.md` (a prose rewrite holds `spec/`); write anything that belongs there to `spec/plans/inbox-mac-port.md` and say so in your summary.
