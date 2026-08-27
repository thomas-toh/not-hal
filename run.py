"""Dev launcher — start the daemon and the Teleprompter overlay from one command.

    python run.py

One window, both logs interleaved, and **one quit stops both**: whichever child exits CLEANLY
takes the other with it, so tray > Quit and Ctrl-C in the console are each a single door out of
the whole app.

A CRASH is the exception and stays isolated: a child that dies with a nonzero code leaves the
other running, so you restart just the one that broke — and so a dead daemon still has a live
overlay to be reported by. That restart-one independence is why two processes beat merging them
into one.

Shutdown ASKS before it insists. The daemon may now own a headless local model server (Ollama),
and it can only stop that from its own cleanup path — so a bare `terminate()`, which on Windows is
TerminateProcess and runs no cleanup at all, would strand the server every time. Children are
started in their own process group and sent CTRL_BREAK first; terminate() and then kill() remain
as the escalation.

The polite signal is only polite if the CHILD makes it so, and that holds on BOTH platforms.
Python maps CTRL_C_EVENT/SIGINT to KeyboardInterrupt and nothing else, so CTRL_BREAK (Windows)
and SIGTERM (POSIX) each terminate the process outright by default and no `finally` runs. The
daemon converts whichever one applies (`orchestrator._catch_polite_stop`). This file asking
politely is worthless without that handler on the other end: measured 2026-08-02 on Windows, a
server the app had started was left running on every quit.

ponytail: still no Windows Job-Object lifetime tie — SIGKILL run.py and these
children can orphan; Ctrl-C and normal exit are handled. Add it when orphans are seen.
"""
from __future__ import annotations

import signal
import subprocess
import sys
import time

# Windows: a child must be in its OWN process group to be sent CTRL_BREAK without the event also
# hitting us. Both constants are Windows-only; off Windows the group flag is 0 and SIGTERM is the
# polite signal. SIGTERM does NOT unwind on its own either — the daemon has to catch it, exactly
# as it catches SIGBREAK here.
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_POLITE = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM)
GRACE_S = 8.0            # generous: the daemon may be waiting on a local server to stop

CHILDREN = {
    "daemon": [sys.executable, "-m", "backend.orchestrator"],   # voice loop + Contract P feed
    "overlay": [sys.executable, "-m", "frontend"],         # subscribes to the feed
}


def stop_others(exits: dict[str, int | None]) -> bool:
    """Should the survivors be stopped? True once some child has exited CLEANLY (code 0),
    which is what a deliberate quit looks like — tray > Quit, or Ctrl-C in the console.

    A crash (nonzero) is False: the survivor keeps running (crash isolation), and a
    child still running (None) decides nothing either way."""
    return any(code == 0 for code in exits.values())


def shutdown(procs: dict) -> None:
    """Ask, wait, then insist. The asking is what lets the daemon stop a local model server it
    started — TerminateProcess runs no cleanup, so without this the server is stranded on every
    quit that isn't a console Ctrl-C."""
    alive = [p for p in procs.values() if p.poll() is None]
    for p in alive:
        try:
            p.send_signal(_POLITE)
        except (OSError, ValueError):
            pass                                   # already gone, or no group to signal
    deadline = time.monotonic() + GRACE_S
    while time.monotonic() < deadline and any(p.poll() is None for p in alive):
        time.sleep(0.2)
    for p in alive:                                # ...then insist
        if p.poll() is None:
            p.terminate()
    for p in alive:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def main() -> int:
    procs = {name: subprocess.Popen(cmd, creationflags=_NEW_GROUP)
             for name, cmd in CHILDREN.items()}
    reported: set[str] = set()
    try:
        while any(p.poll() is None for p in procs.values()):
            exits = {name: p.poll() for name, p in procs.items()}
            for name, code in exits.items():
                if code is None or name in reported:
                    continue
                reported.add(name)
                if code == 0:
                    print(f"[run] {name} quit — stopping the other too.")
                else:
                    print(f"[run] {name} CRASHED (exit {code}); the other keeps running — "
                          f"restart it alone with: {' '.join(CHILDREN[name])}")
            if stop_others(exits):
                break
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown(procs)
    return 0


def _selfcheck() -> None:
    """The quit tie, all four cases. The policy is the only non-trivial part of this file;
    spawning real processes to test it would test subprocess, not the rule."""
    # Nothing has exited yet -> nobody is stopped.
    assert not stop_others({"daemon": None, "overlay": None})
    # A clean exit of EITHER side stops the other. These are the two doors out.
    assert stop_others({"daemon": 0, "overlay": None}), "Ctrl-C in the console must stop the overlay"
    assert stop_others({"daemon": None, "overlay": 0}), "tray Quit must stop the daemon"
    # A crash of either side must NOT — the survivor keeps running.
    assert not stop_others({"daemon": 1, "overlay": None}), "a daemon crash must spare the overlay"
    assert not stop_others({"daemon": None, "overlay": 1}), "an overlay crash must spare the daemon"
    assert not stop_others({"daemon": 3221225477, "overlay": None}), "a hard crash is still a crash"
    # Mixed: one crashed earlier, then the other was quit cleanly -> stop.
    assert stop_others({"daemon": 1, "overlay": 0})

    # Shutdown ASKS before insisting, so the daemon's `finally` can stop a local model server.
    # A child that answers the polite signal must never be terminated.
    class _Child:
        def __init__(self, obeys): self.obeys, self.signalled, self.terminated, self.n = obeys, False, False, 0
        def poll(self):
            self.n += 1
            return 0 if (self.obeys and self.signalled and self.n > 1) else None
        def send_signal(self, _s): self.signalled = True
        def terminate(self): self.terminated = True
        def wait(self, timeout=None): return 0
        def kill(self): pass

    good = _Child(obeys=True)
    shutdown({"daemon": good})
    assert good.signalled, "shutdown must ask first"
    assert not good.terminated, "a child that obeys the signal must not then be terminated"

    global GRACE_S
    _grace, GRACE_S = GRACE_S, 0.01                 # don't wait out the real grace period
    stubborn = _Child(obeys=False)
    shutdown({"daemon": stubborn})
    GRACE_S = _grace
    assert stubborn.signalled and stubborn.terminated, "a child that ignores it must be insisted on"

    print("run.py selfcheck OK — clean exit ties, crash isolates, "
          "shutdown asks before it insists")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        sys.exit(main())
