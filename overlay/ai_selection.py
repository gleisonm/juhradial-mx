"""
JuhRadial MX - selection capture & paste helpers

Getting the "current selection" the way Logitech Options+ does, across display
servers:

  - X11 (and KDE/Wayland with the primary-selection protocol): the highlighted
    text is already exposed as the PRIMARY selection, so we read it directly via
    Qt - no key injection, no clipboard mutation. This is the reliable path.
  - Fallback: simulate Ctrl+C and read the clipboard (needs a key injector such
    as a running ydotoold, xdotool, or wtype).

Pasting works in reverse: put the text on the clipboard and inject Ctrl+V using
whatever injector is available; if none is, the text is left on the clipboard
for a manual paste.

SPDX-License-Identifier: GPL-3.0
"""

import os
import shutil
import subprocess
import time

from PyQt6.QtGui import QClipboard, QGuiApplication

# Linux input event codes (for ydotool)
_KEY_LEFTCTRL = 29
_KEY_C = 46
_KEY_V = 47


def _clipboard():
    return QGuiApplication.clipboard()


def get_clipboard_text() -> str:
    cb = _clipboard()
    return cb.text(QClipboard.Mode.Clipboard) if cb is not None else ""


def set_clipboard_text(text: str) -> None:
    cb = _clipboard()
    if cb is not None:
        cb.setText(text or "", QClipboard.Mode.Clipboard)


def get_primary_text() -> str:
    """Return the PRIMARY (X11-style) selection, or '' if unsupported/empty."""
    cb = _clipboard()
    if cb is not None and cb.supportsSelection():
        return cb.text(QClipboard.Mode.Selection) or ""
    return ""


def _set_primary_text(text: str) -> None:
    cb = _clipboard()
    if cb is not None and cb.supportsSelection():
        cb.setText(text or "", QClipboard.Mode.Selection)


# ---------------------------------------------------------------------------
# Key injection (for the Ctrl+C fallback and for paste)
# ---------------------------------------------------------------------------


def _ydotool_socket_ready() -> bool:
    sock = os.environ.get("YDOTOOL_SOCKET") or f"/run/user/{os.getuid()}/.ydotool_socket"
    return os.path.exists(sock)


def _ydotool_key(*codes: int) -> bool:
    if not shutil.which("ydotool") or not _ydotool_socket_ready():
        return False
    seq = [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)]
    try:
        r = subprocess.run(["ydotool", "key", *seq],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=2, check=False)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _inject_ctrl(letter: str) -> bool:
    """Inject Ctrl+<letter> using the first available backend. Returns True on
    success, False if no injector is available."""
    on_x11 = bool(os.environ.get("DISPLAY")) and os.environ.get("XDG_SESSION_TYPE") != "wayland"

    if shutil.which("xdotool") and os.environ.get("DISPLAY"):
        try:
            r = subprocess.run(["xdotool", "key", "--clearmodifiers", f"ctrl+{letter}"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=2, check=False)
            if r.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass

    code = _KEY_C if letter == "c" else _KEY_V
    if _ydotool_key(_KEY_LEFTCTRL, code):
        return True

    if not on_x11 and shutil.which("wtype"):
        try:
            r = subprocess.run(["wtype", "-M", "ctrl", letter, "-m", "ctrl"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=2, check=False)
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            pass
    return False


def injector_available() -> bool:
    """Whether automatic Ctrl+V paste is possible on this system."""
    if shutil.which("xdotool") and os.environ.get("DISPLAY"):
        return True
    if shutil.which("ydotool") and _ydotool_socket_ready():
        return True
    on_wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland"
    return on_wayland and bool(shutil.which("wtype"))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def capture_selection(delay_ms: int = 120, preserve_clipboard: bool = True):
    """Return (selected_text, saved_clipboard).

    Tries the PRIMARY selection first (reliable, non-destructive). Only if that
    is empty/unsupported does it fall back to simulating Ctrl+C. ``saved_clipboard``
    is the clipboard value to restore later, or None when nothing was disturbed.
    """
    # 1. PRIMARY selection - the highlighted text, no side effects.
    primary = get_primary_text()
    if primary.strip():
        return primary, None

    # 2. Fallback: simulate Ctrl+C into the focused app, then read the clipboard.
    saved = get_clipboard_text() if preserve_clipboard else None
    sentinel = "\x00juhradial-no-selection\x00"
    set_clipboard_text(sentinel)

    if not _inject_ctrl("c"):
        # No injector and no primary selection: nothing we can grab.
        if saved is not None:
            set_clipboard_text(saved)
        return "", None

    time.sleep(max(delay_ms, 0) / 1000.0)
    QGuiApplication.processEvents()

    captured = get_clipboard_text()
    if captured == sentinel:
        captured = ""
    return captured, saved


def restore_clipboard(saved) -> None:
    if saved is not None:
        set_clipboard_text(saved)


def paste_text(text: str, restore=None, delay_ms: int = 120) -> bool:
    """Put ``text`` on the clipboard and try to inject Ctrl+V.

    Returns True if the paste keystroke was injected, False if no injector is
    available (the text is still on the clipboard for a manual Ctrl+V). When
    ``restore`` is given and the paste was injected, the clipboard is restored
    afterwards.
    """
    set_clipboard_text(text)
    _set_primary_text(text)  # also expose for middle-click paste
    QGuiApplication.processEvents()
    time.sleep(0.03)

    if not _inject_ctrl("v"):
        return False

    if restore is not None:
        time.sleep(max(delay_ms, 0) / 1000.0)
        set_clipboard_text(restore)
    return True
