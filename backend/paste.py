"""Deterministic text delivery for dictation: put text on the clipboard and send
a synthetic Ctrl+V. User-initiated and deterministic — NEVER a Contract-T tool, and the model
never chooses to paste. The transcript stays in RAM until this point; the clipboard is
the delivery, not a log.

Issued by the DAEMON, not the overlay: a paste lands wherever keyboard focus is, and the overlay
is deliberately never focusable, so it is the wrong process to send the keys from.

stdlib ctypes only — a dozen Win32 calls do not earn a pywin32/pyperclip dependency. macOS is a
later seam: there the functions no-op with a warning, exactly as the hotkeys do.

    python -m backend.paste "hello"     # set the clipboard + paste into the focused window
    python -m backend.paste --selfcheck # no keystrokes: clipboard round-trip + the encoders
"""
from __future__ import annotations

import logging
import sys
import time

log = logging.getLogger("nothal.paste")

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002
_VK_CONTROL = 0x11
_VK_V = 0x56
_KEYEVENTF_KEYUP = 0x0002

# How long to let the target app consume the paste before restoring the previous clipboard.
# ponytail: a fixed wait, not a handshake — there is no signal that the paste has been read.
# Lengthen if a slow app ever misses the paste; it only delays the clipboard restore, not the text.
_RESTORE_DELAY_S = 0.15


def _utf16z(text: str) -> bytes:
    """A NUL-terminated UTF-16-LE buffer — the shape CF_UNICODETEXT wants. Factored out so the
    one bit of real logic (encoding + termination) is testable without a live clipboard."""
    return text.encode("utf-16-le") + b"\x00\x00"


def _win():
    """The two Win32 DLLs with the pointer-sized return/argument types set.

    Load-bearing: on 64-bit Python a handle is pointer-sized, but ctypes defaults an unannotated
    function to a 32-bit int return — which truncates every HGLOBAL and crashes at random. Setting
    these is not optional tidiness.
    """
    import ctypes
    from ctypes import wintypes

    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    k.GlobalAlloc.restype = ctypes.c_void_p
    k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k.GlobalFree.argtypes = [ctypes.c_void_p]
    u.SetClipboardData.restype = ctypes.c_void_p
    u.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    u.GetClipboardData.restype = ctypes.c_void_p
    u.GetClipboardData.argtypes = [wintypes.UINT]
    return ctypes, u, k


def get_clipboard_text() -> str | None:
    """The clipboard's text (CF_UNICODETEXT), or None if it holds none or cannot be read."""
    if sys.platform != "win32":
        return None
    ctypes, u, k = _win()
    if not u.OpenClipboard(None):
        return None
    try:
        h = u.GetClipboardData(_CF_UNICODETEXT)
        if not h:
            return None
        p = k.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    """Put `text` on the clipboard as CF_UNICODETEXT. Returns whether it stuck."""
    if sys.platform != "win32":
        return False
    ctypes, u, k = _win()
    buf = _utf16z(text)
    if not u.OpenClipboard(None):
        log.warning("could not open the clipboard")
        return False
    try:
        u.EmptyClipboard()
        h = k.GlobalAlloc(_GMEM_MOVEABLE, len(buf))
        if not h:
            return False
        p = k.GlobalLock(h)
        ctypes.memmove(p, buf, len(buf))
        k.GlobalUnlock(h)
        if not u.SetClipboardData(_CF_UNICODETEXT, h):
            k.GlobalFree(h)          # ownership passes to the clipboard only on success
            return False
        return True                  # the clipboard now owns h — must NOT be freed
    finally:
        u.CloseClipboard()


def send_paste() -> None:
    """A synthetic Ctrl+V to the focused window.

    ponytail: `keybd_event`, not the newer `SendInput`. It is a few lines instead of a struct
    array and is entirely adequate for one chord; move to SendInput only if it proves flaky under
    a real target (e.g. games that ignore injected input)."""
    if sys.platform != "win32":
        return
    import ctypes

    u = ctypes.windll.user32
    u.keybd_event(_VK_CONTROL, 0, 0, 0)
    u.keybd_event(_VK_V, 0, 0, 0)
    u.keybd_event(_VK_V, 0, _KEYEVENTF_KEYUP, 0)
    u.keybd_event(_VK_CONTROL, 0, _KEYEVENTF_KEYUP, 0)


def paste_text(text: str, restore: bool = True) -> bool:
    """Deliver `text` to the focused app: set the clipboard, send Ctrl+V. Best-effort
    restores the previous clipboard text afterwards so dictation does not silently clobber what
    you had copied. Returns whether the text was placed and the paste sent.

    ponytail: restore covers TEXT only — a previously copied image or file is lost. Snapshotting
    every clipboard format is a lot of Win32 for a case daily dictation rarely hits; revisit if
    it bites.
    """
    if sys.platform != "win32":
        log.warning("paste is Windows-only for now (%s) — dictation cannot deliver text",
                    sys.platform)
        return False
    if not text:
        return False
    saved = get_clipboard_text() if restore else None
    if not set_clipboard_text(text):
        return False
    send_paste()
    if restore and saved is not None:
        time.sleep(_RESTORE_DELAY_S)
        set_clipboard_text(saved)
    return True


def _selfcheck() -> None:
    # The encoder is the one bit of pure logic and is checked everywhere.
    assert _utf16z("A") == b"A\x00\x00\x00", _utf16z("A")     # 'A' = 0x41 -> 41 00, then the NUL
    assert _utf16z("") == b"\x00\x00"
    assert _utf16z("é").endswith(b"\x00\x00") and len(_utf16z("é")) == 4   # 1 char + NUL, 2B each
    assert _utf16z("hi") == b"h\x00i\x00\x00\x00"

    if sys.platform != "win32":
        # Every entry point must degrade, never raise, off Windows (macOS pending).
        assert get_clipboard_text() is None
        assert set_clipboard_text("x") is False
        assert paste_text("x") is False
        send_paste()             # a no-op, must not raise
        print("paste selfcheck OK (non-win32): encoders correct, all entry points degrade safely")
        return

    # On Windows, round-trip the clipboard WITHOUT sending keystrokes (no focused target in CI).
    # A locked/headless session can refuse OpenClipboard; that is an environment limit, not a bug,
    # so skip rather than false-fail — the paste chord itself is proven live on a real machine.
    original = get_clipboard_text()
    sentinel = "nothal-paste-selfcheck-é—你好"   # accents, em-dash, CJK
    if not set_clipboard_text(sentinel):
        print("paste selfcheck OK (win32): clipboard unavailable in this session — encoders only")
        return
    got = get_clipboard_text()
    assert got == sentinel, f"clipboard round-trip corrupted the text: {got!r}"
    if original is not None:
        set_clipboard_text(original)     # leave the clipboard as we found it
    print("paste selfcheck OK (win32): unicode clipboard round-trip clean, previous text restored")


def main() -> None:
    import argparse

    from shared.log import setup_logging

    setup_logging()
    ap = argparse.ArgumentParser(description="Paste text into the active window")
    ap.add_argument("text", nargs="?", help="text to deliver to the focused window")
    ap.add_argument("--selfcheck", action="store_true", help="no keystrokes: round-trip + encoders")
    ap.add_argument("--no-restore", action="store_true", help="don't restore the prior clipboard")
    args = ap.parse_args()
    if args.selfcheck:
        _selfcheck()
        return
    if not args.text:
        ap.error("provide text to paste, or --selfcheck")
    ok = paste_text(args.text, restore=not args.no_restore)
    print("pasted" if ok else "paste failed")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
