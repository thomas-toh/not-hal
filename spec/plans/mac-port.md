# Mac port — scope and stages

Last reconciled: 2026-08-27 05:30

Survey of every Windows-specific path in the code, 2026-08-27. Effort: S = under an hour, M = a session, L = more than one session.

## What ports for free

Written with the second platform in mind and already carrying a working POSIX branch: `run.py` (SIGTERM fallback), `orchestrator.py` (SIGBREAK guard), `broadcaster.py`, `shared/settings.py` (`~/.config/nothal`), `keyring` (Keychain), the whole LLM/provider stack, `audio/wake.py`, `audio/speak.py` (Kokoro on onnxruntime, CPU), `audio/listen.py` (the CUDA preload self-disables; STT falls to CPU). Cost on the Mac is measurement, not code: CPU Whisper latency has never been taken, and NOTES says a Metal engine is added only if it disappoints.

## What needs a second backend

| Area | Windows today | Mac | Effort |
|------|---------------|-----|--------|
| Hotkeys, the two doors — `backend/hotkeys.py:236-312` | `RegisterHotKey` + `PeekMessageW` pump + `GetAsyncKeyState` for hold/tap | Carbon `RegisterEventHotKey` (decided, spec/50) + `CGEventSourceKeyState`; needs the Accessibility-permission story | L |
| Esc dismiss + theme change — `frontend/__main__.py:145-201` | `WM_HOTKEY` native event filter, `WM_SETTINGCHANGE` | Carbon hotkey + `NSDistributedNotificationCenter` | L |
| Dictation delivery — `backend/paste.py` | Win32 clipboard + `keybd_event` Ctrl+V | `NSPasteboard` (or `pbcopy`) + `CGEventPost` Cmd+V; Accessibility permission | M |
| Overlay window flags — `frontend/__main__.py:280-300` | `WS_EX_NOACTIVATE`, `TOPMOST` | `Qt.WindowDoesNotAcceptFocus` covers part; `NSPanel` non-activating for the rest | M |
| Tray theme — `frontend/tray.py:36-60` | `winreg` light/dark | `defaults read -g AppleInterfaceStyle` | S |
| Reduced motion — `frontend/__main__.py:105-113` | `SystemParametersInfoW` | `NSWorkspace.accessibilityDisplayShouldReduceMotion` | S |
| `system_status` — `tools.py:85-131` | `GetForegroundWindow`, `GetSystemPowerStatus` | `osascript` frontmost app, `pmset -g batt` | S |
| `open_app` — `tools.py:411-528` | Start Menu enumeration, `shell:AppsFolder` | `/Applications` + `open -a` | S/M |
| `focus_window` — `tools.py:531-630` | `EnumWindows`, `SetForegroundWindow` | `osascript "tell app to activate"` (per app, not per window) | M |
| `media_control` — `tools.py:369-378,632-650` | `VK_MEDIA_*` | `NX_KEYTYPE_PLAY` system-defined events | M |
| `find_document` — `tools.py:184-232` | PowerShell COM → Windows Search SQL | `mdfind`; shorter than the original | M |
| `search_email` — `tools.py:235-355` | PowerShell COM → Outlook, registry profile probe | Mail.app AppleScript; different object model, no profile probe | L — defer |
| Overlay check coverage — `overlay_check.py:431-449,537-560` | two Win32-only checks, skipped elsewhere | Mac peers | M |

## What is a redesign, not a port

- **Per-region click-through on the island** (`frontend/__main__.py:205-280`, `WM_NCHITTEST`). macOS has no per-point hit-test message; the only lever is window-level `ignoresMouseEvents` toggled from a mouse-move monitor. The peek interaction needs a different mechanism.
- **`search_email`** — see above. Ship the Mac without it first.

## Deletions on Mac

`DwmSetWindowAttribute` corners and dark caption (`__main__.py:311-340`, `tray.py:129-146`), `SetCurrentProcessExplicitAppUserModelID` (`__main__.py:347-355`), the PySide6 long-path check (`__main__.py:93-98`), `CREATE_NO_WINDOW` on `ollama serve` (already `getattr`-guarded).

## Stages

Each stage leaves the Windows build byte-identical and green; every Mac branch sits behind `sys.platform == "darwin"` beside the existing Windows one.

**Stage 0 — groundwork, buildable on Windows.** `pyproject.toml` platform markers (`sys_platform != "darwin"` on `gpu-cuda` and the CUDA wheels); `install.sh` peer to `install.bat`; `macos-latest` leg in `checks.yml`; every Windows-only call above guarded so the Mac boots to the wake-word entrance with nothing crashing. Verify: all 24 checks pass on Windows; `python -c "import frontend, backend"` under a faked `sys.platform="darwin"` raises nothing.

**Stage 1 — boots on the Mac.** Needs the machine. Wake word → STT → brain → TTS, overlay renders, tray, settings window, Keychain key. Take the owed measurements: CPU Whisper p50/p95 ( `eval/latency.py`), the 4/4 replay run (copy `eval/replay/wav/` by hand), the earcon-into-VAD and BT-duplex watch items. Decides whether a Metal STT engine is needed.

**Stage 2 — the doors.** Carbon hotkeys, Esc dismiss, Accessibility permission prompt on first run. This is the largest item and the first one a user notices.

**Stage 3 — dictation delivery.** `paste.py` Mac backend.

**Stage 4 — tools.** `system_status`, `open_app`, `focus_window`, `find_document`, `media_control`, in that order; each lands with its selfcheck. `search_email` deferred.

**Stage 5 — island click-through redesign.** Last, and only if the peek interaction matters on Mac.

## Constraint

Stages 1–5 need Claude Code running on the Mac itself: every backend above is only provable against the real OS, and a Mac branch written blind on Windows is untested code. Stage 0 is the only stage a Windows session can finish.
