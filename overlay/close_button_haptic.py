"""
JuhRadial MX - haptic feedback when hovering a window's close (X) button

Experimental. On X11, polls the global cursor position and the active window's
titlebar geometry to estimate the close-button rectangle (top-right of the
titlebar). When the cursor enters that rectangle it fires a short haptic pulse
via the daemon. Works for any application:
  - Server-side decorated windows (Breeze/Qt/KDE): the WM draws the titlebar
    above the client area, located via _NET_FRAME_EXTENTS.
  - Client-side decorated windows (VS Code, GTK/GNOME, browsers with a custom
    titlebar): the app draws its own titlebar inside the window, so we anchor to
    the visible top-right corner (stripping the invisible _GTK_FRAME_EXTENTS
    shadow border when present) and assume a typical header-bar height.

Limitations:
  - X11 only (uses xdotool/xprop). Wayland restricts global pointer queries.
  - The close-button rect is a heuristic (a square near the top-right of the
    titlebar), not pixel-exact, and for CSD apps the titlebar height is an
    estimate. Fullscreen/borderless windows are skipped.

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
# Hit-box size as a fraction of the titlebar height. < 1.0 keeps the activation
# zone tighter around the actual X glyph (the button is smaller than the bar).
_BUTTON_SCALE = 0.62
# Estimated header-bar height (px) for client-side-decorated apps, which don't
# expose their titlebar height. Typical GTK/Electron header bars are ~35-46px.
_CSD_TITLEBAR_H = 40


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


def _props(wid, names):
    """Fetch several X properties in one xprop call -> {name: value_text}."""
    out = {}
    for line in _run(["xprop", "-id", wid, *names]).splitlines():
        # Format: <NAME>(<TYPE>) = <value>   ("<NAME>:  not found." when absent)
        if "(" in line and "=" in line:
            name = line.split("(", 1)[0].strip()
            out[name] = line.split("=", 1)[1].strip()
    return out


def _parse_int(value):
    """Parse an int property value, or None."""
    if not value:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _parse_extents(value):
    """Parse 'left, right, top, bottom' -> 4-tuple, or None."""
    if not value:
        return None
    try:
        nums = [int(n.strip()) for n in value.split(",")]
        return nums[0], nums[1], nums[2], nums[3]
    except (ValueError, IndexError):
        return None


def _root_cardinal(prop):
    """Read a single CARDINAL property off the root window, or None."""
    txt = _run(["xprop", "-root", prop])
    if "=" not in txt:
        return None
    try:
        return int(txt.split("=", 1)[1].strip())
    except ValueError:
        return None


def _stacking_top_first():
    """Top-level window ids, topmost first (EWMH _NET_CLIENT_LIST_STACKING)."""
    txt = _run(["xprop", "-root", "_NET_CLIENT_LIST_STACKING"])
    if "#" not in txt:
        return []
    ids = [s.strip() for s in txt.split("#", 1)[1].split(",") if s.strip()]
    ids.reverse()  # property is bottom-to-top; we want topmost first
    return ids


def _close_square(right_edge, top_edge, titlebar_h):
    """A small square hit-box near the top-right of a titlebar band.

    right_edge/top_edge are the visible top-right corner of the titlebar and
    titlebar_h its height. The square is _BUTTON_SCALE of that height (tighter
    than the full bar) and vertically centred in the band, with a small inset
    from the corner.
    """
    btn = max(12, int(round(titlebar_h * _BUTTON_SCALE)))
    inset = max(2, titlebar_h // 8)
    x2 = right_edge - inset
    x1 = x2 - btn
    cy = top_edge + titlebar_h / 2.0
    y1 = int(round(cy - btn / 2.0))
    y2 = int(round(cy + btn / 2.0))
    return (x1, y1, x2, y2)


_ALL_DESKTOPS = 0xFFFFFFFF

# Window types that never carry a hoverable close button (panels, docks, the
# desktop, menus, etc.). Matched as substrings of _NET_WM_WINDOW_TYPE.
_NO_CLOSE_TYPES = (
    "_DESKTOP",
    "_DOCK",
    "_TOOLBAR",
    "_MENU",
    "_SPLASH",
    "_NOTIFICATION",
    "_TOOLTIP",
    "_COMBO",
    "_DND",
)


def _window_rects(wid, current_desktop):
    """Return (full_rect, close_rect) for a top-level window, or None to skip.

    full_rect is the window's visible bounds — used to resolve occlusion so a
    background window only fires when its X is actually the front-most thing
    under the cursor. close_rect is the small hit-box over its close button.

    Skips windows that are fullscreen, minimised (hidden), or on another virtual
    desktop, none of which can show a hoverable close button here.
    """
    props = _props(
        wid,
        [
            "_NET_WM_WINDOW_TYPE",
            "_NET_WM_STATE",
            "_NET_WM_DESKTOP",
            "_NET_FRAME_EXTENTS",
            "_GTK_FRAME_EXTENTS",
        ],
    )
    win_type = props.get("_NET_WM_WINDOW_TYPE", "")
    if any(t in win_type for t in _NO_CLOSE_TYPES):
        return None
    state = props.get("_NET_WM_STATE", "")
    if "_NET_WM_STATE_FULLSCREEN" in state or "_NET_WM_STATE_HIDDEN" in state:
        return None
    desk = _parse_int(props.get("_NET_WM_DESKTOP"))
    if (
        current_desktop is not None
        and desk is not None
        and desk != current_desktop
        and desk != _ALL_DESKTOPS
    ):
        return None

    geo = _parse_shell(_run(["xdotool", "getwindowgeometry", "--shell", wid]))
    try:
        x, y = int(geo["X"]), int(geo["Y"])
        w, h = int(geo["WIDTH"]), int(geo["HEIGHT"])
    except (KeyError, ValueError):
        return None

    frame = _parse_extents(props.get("_NET_FRAME_EXTENTS"))
    if frame and frame[2] > 0:
        # Server-side decoration: the WM draws the titlebar ABOVE the client
        # area (y - top .. y), extending the window by the border on each side.
        left, right, top, bottom = frame
        full = (x - left, y - top, x + w + right, y + h + bottom)
        return full, _close_square(x + w + right, y - top, top)

    # Client-side decoration (or borderless): the app draws its own titlebar
    # inside the window. Strip the invisible GTK shadow border, if any, so we
    # anchor to the visible bounds, and estimate the header-bar height.
    gl, gr, gt, gb = _parse_extents(props.get("_GTK_FRAME_EXTENTS")) or (0, 0, 0, 0)
    full = (x + gl, y + gt, x + w - gr, y + h - gb)
    return full, _close_square(x + w - gr, y + gt, _CSD_TITLEBAR_H)


class CloseButtonHaptic:
    """Fires a haptic pulse when the cursor enters any window's X button.

    Tracks every visible top-level window (not just the focused one) in stacking
    order, so hovering the close button of an unfocused/background window also
    pulses, while occluded buttons behind other windows do not.
    """

    def __init__(self, trigger_haptic, event="slice_change"):
        self._trigger = trigger_haptic
        self._event = event
        self._windows = []
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
        self._windows = []
        self._was_inside = False
        print("[CloseHaptic] disabled")

    def _refresh_loop(self):
        last_count = -1
        while self._running:
            current_desktop = _root_cardinal("_NET_CURRENT_DESKTOP")
            windows = []
            for wid in _stacking_top_first():
                info = _window_rects(wid, current_desktop)
                if info is not None:
                    windows.append(info)
            self._windows = windows  # topmost first
            if len(windows) != last_count:
                last_count = len(windows)
                print(f"[CloseHaptic] tracking {len(windows)} window(s) with an X button")
            time.sleep(_WINDOW_REFRESH_MS / 1000.0)

    def _check_cursor(self):
        windows = self._windows
        if not windows:
            self._was_inside = False
            return
        pos = QCursor.pos()
        px, py = pos.x(), pos.y()
        inside = False
        # Walk topmost-first: the first window whose visible bounds contain the
        # cursor is the one actually on top there, so we test only its X button
        # (a close button hidden behind another window never fires).
        for full, close in windows:
            fx1, fy1, fx2, fy2 = full
            if fx1 <= px <= fx2 and fy1 <= py <= fy2:
                cx1, cy1, cx2, cy2 = close
                inside = cx1 <= px <= cx2 and cy1 <= py <= cy2
                break
        if inside and not self._was_inside:
            now = time.monotonic()
            if now - self._last_pulse >= _MIN_PULSE_GAP_S:
                self._last_pulse = now
                print("[CloseHaptic] cursor entered X button -> pulse")
                try:
                    self._trigger(self._event)
                except Exception as e:  # never let haptics break the overlay
                    print(f"[CloseHaptic] trigger failed: {e}")
        self._was_inside = inside
