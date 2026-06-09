"""
JuhRadial MX - haptic feedback when hovering a window's close (X) button

Experimental. On X11, polls the global cursor position and the active window's
server-side titlebar geometry to estimate the close-button rectangle (top-right
of the decoration, derived from _NET_FRAME_EXTENTS). When the cursor enters that
rectangle it fires a short haptic pulse via the daemon.

Limitations:
  - X11 only (uses xdotool/xprop). Wayland restricts global pointer queries.
  - Works for server-side decorated windows (Breeze/Qt/KDE apps). Apps with
    client-side decorations (VS Code, most GTK/GNOME apps, browsers with a custom
    titlebar) expose no _NET_FRAME_EXTENTS, so their X button can't be located.
  - The close-button rect is a heuristic (a square the height of the titlebar at
    the far right), not pixel-exact.

SPDX-License-Identifier: GPL-3.0
"""

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QCursor

CONFIG_PATH = Path.home() / ".config" / "juhradial" / "config.json"

# How often to re-read the active window geometry (cheap, but not every frame).
_WINDOW_REFRESH_MS = 300
# Cursor polling cadence.
_CURSOR_POLL_MS = 33
# Minimum gap between two pulses, so re-entering the button doesn't spam.
_MIN_PULSE_GAP_S = 0.35


def load_enabled() -> bool:
    """Read haptics.close_button_hover from config.json (default False)."""
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return bool(cfg.get("haptics", {}).get("close_button_hover", False))
    except (OSError, ValueError):
        pass
    return False


def set_enabled(value: bool) -> None:
    """Persist haptics.close_button_hover to config.json."""
    try:
        cfg = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        cfg.setdefault("haptics", {})["close_button_hover"] = bool(value)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        tmp.replace(CONFIG_PATH)
    except (OSError, ValueError) as e:
        print(f"[CloseHaptic] Failed to save config: {e}")


def _run(args, timeout=0.3):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _parse_shell(text):
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _active_close_rect():
    """Return (x1, y1, x2, y2) of the active window's close button, or None.

    None when the window has no server-side decoration (e.g. CSD apps) or the
    geometry can't be read.
    """
    aw = _run(["xdotool", "getactivewindow"]).strip()
    if not aw:
        return None

    geo = _parse_shell(_run(["xdotool", "getwindowgeometry", "--shell", aw]))
    try:
        x, y = int(geo["X"]), int(geo["Y"])
        w = int(geo["WIDTH"])
    except (KeyError, ValueError):
        return None

    extents = _run(["xprop", "-id", aw, "_NET_FRAME_EXTENTS"])
    # Format: _NET_FRAME_EXTENTS(CARDINAL) = left, right, top, bottom
    if "=" not in extents:
        return None
    try:
        nums = [int(n.strip()) for n in extents.split("=", 1)[1].split(",")]
        left, right, top, _bottom = nums[0], nums[1], nums[2], nums[3]
    except (ValueError, IndexError):
        return None
    if top <= 0:
        return None  # no titlebar (borderless / CSD)

    # Close button: a square ~titlebar-height at the far right of the titlebar,
    # which sits ABOVE the client area (y - top .. y). Add a tiny inset.
    inset = max(2, top // 8)
    x2 = x + w + right - inset
    x1 = x2 - top
    y1 = y - top + inset
    y2 = y - inset
    return (x1, y1, x2, y2)


class CloseButtonHaptic:
    """Fires a haptic pulse when the cursor enters the active window's X button."""

    def __init__(self, trigger_haptic, event="slice_change"):
        self._trigger = trigger_haptic
        self._event = event
        self._rect = None
        self._was_inside = False
        self._last_pulse = 0.0
        self._running = False
        self._available = bool(shutil.which("xdotool") and shutil.which("xprop"))

        self._cursor_timer = QTimer()
        self._cursor_timer.setInterval(_CURSOR_POLL_MS)
        self._cursor_timer.timeout.connect(self._check_cursor)

        self._refresh_thread = None

    @property
    def available(self) -> bool:
        return self._available

    def start(self):
        if self._running or not self._available:
            return
        self._running = True
        self._cursor_timer.start()
        self._refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._refresh_thread.start()
        print("[CloseHaptic] enabled")

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._cursor_timer.stop()
        self._rect = None
        self._was_inside = False
        print("[CloseHaptic] disabled")

    def _refresh_loop(self):
        while self._running:
            self._rect = _active_close_rect()
            time.sleep(_WINDOW_REFRESH_MS / 1000.0)

    def _check_cursor(self):
        rect = self._rect
        if not rect:
            self._was_inside = False
            return
        pos = QCursor.pos()
        x1, y1, x2, y2 = rect
        inside = x1 <= pos.x() <= x2 and y1 <= pos.y() <= y2
        if inside and not self._was_inside:
            now = time.monotonic()
            if now - self._last_pulse >= _MIN_PULSE_GAP_S:
                self._last_pulse = now
                try:
                    self._trigger(self._event)
                except Exception as e:  # never let haptics break the overlay
                    print(f"[CloseHaptic] trigger failed: {e}")
        self._was_inside = inside
