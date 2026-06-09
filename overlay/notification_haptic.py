"""
JuhRadial MX - haptic feedback when a desktop notification arrives.

Becomes a passive D-Bus monitor on the session bus and watches for calls to
org.freedesktop.Notifications.Notify (the method every notification daemon
receives when an app posts a notification). On each one it asks the daemon to
play the haptic pattern configured for the "notification" event, so the user
feels a pulse whenever a notification pops up.

The watcher runs its own GLib main loop on a background thread, isolated from
the overlay's Qt event loop, and talks to the daemon over its own GDBus
connection (a monitor connection can't make method calls, so a second normal
connection is used to send TriggerHaptic).

Which effect plays is the daemon's "notification" per-event pattern, chosen in
Settings -> Haptics, so this module only decides *when* to pulse, not how it
feels. Off by default (haptics.notify_on_notification).

Limitations:
  - Needs PyGObject (gi) and a session bus that allows BecomeMonitor (standard
    on KDE/GNOME/most desktops). Falls back to unavailable otherwise.

SPDX-License-Identifier: GPL-3.0
"""

import json
import threading
import time
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "juhradial" / "config.json"

# Minimum gap between two pulses, so a burst of notifications doesn't spam.
_MIN_PULSE_GAP_S = 0.4

# Daemon D-Bus identity (matches settings_config.py / the Rust service).
_DAEMON_NAME = "org.kde.juhradialmx"
_DAEMON_PATH = "/org/kde/juhradialmx/Daemon"
_DAEMON_IFACE = "org.kde.juhradialmx.Daemon"

# Desktop notification spec.
_NOTIF_IFACE = "org.freedesktop.Notifications"

try:
    import gi

    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib

    _GI_OK = True
except (ImportError, ValueError):
    _GI_OK = False


def load_enabled() -> bool:
    """Read haptics.notify_on_notification from config.json (default False)."""
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return bool(cfg.get("haptics", {}).get("notify_on_notification", False))
    except (OSError, ValueError):
        pass
    return False


def set_enabled(value: bool) -> None:
    """Persist haptics.notify_on_notification to config.json."""
    try:
        cfg = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        cfg.setdefault("haptics", {})["notify_on_notification"] = bool(value)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        tmp.replace(CONFIG_PATH)
    except (OSError, ValueError) as e:
        print(f"[NotifHaptic] Failed to save config: {e}")


class NotificationHaptic:
    """Fires the daemon's "notification" haptic pattern on each notification."""

    def __init__(self):
        self._thread = None
        self._loop = None
        self._monitor = None  # monitor connection (receive-only)
        self._sender = None  # normal connection (calls TriggerHaptic)
        self._last_pulse = 0.0
        self._running = False
        self._available = _GI_OK

    @property
    def available(self) -> bool:
        return self._available

    def start(self):
        if self._running or not self._available:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("[NotifHaptic] enabled")

    def stop(self):
        if not self._running:
            return
        self._running = False
        loop = self._loop
        if loop is not None:
            loop.quit()  # g_main_loop_quit is thread-safe
        print("[NotifHaptic] disabled")

    def _run(self):
        """Background thread: own GLib loop + a notification monitor."""
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        try:
            self._sender = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            addr = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
            mon = Gio.DBusConnection.new_for_address_sync(
                addr,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None,
                None,
            )
            mon.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus.Monitoring",
                "BecomeMonitor",
                GLib.Variant(
                    "(asu)",
                    ([f"interface='{_NOTIF_IFACE}',member='Notify'"], 0),
                ),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            mon.add_filter(self._on_message)
            self._monitor = mon
            self._loop = GLib.MainLoop.new(ctx, False)
            self._loop.run()
        except Exception as e:  # never let the watcher take down the overlay
            print(f"[NotifHaptic] monitor failed: {e}")
        finally:
            ctx.pop_thread_default()
            self._running = False

    def _on_message(self, _conn, message, incoming):
        try:
            if (
                incoming
                and message.get_message_type() == Gio.DBusMessageType.METHOD_CALL
                and message.get_interface() == _NOTIF_IFACE
                and message.get_member() == "Notify"
            ):
                self._on_notification()
        except Exception as e:
            print(f"[NotifHaptic] filter error: {e}")
        return message  # monitor: pass the message through untouched

    def _on_notification(self):
        # Re-read the flag so toggling it (tray/settings) takes effect live while
        # the watcher is running, without restarting the overlay.
        if not load_enabled():
            return
        now = time.monotonic()
        if now - self._last_pulse < _MIN_PULSE_GAP_S:
            return
        self._last_pulse = now
        try:
            # Fire-and-forget: a blocking call_sync from inside the monitor
            # filter deadlocks against this thread's loop, so dispatch async and
            # ignore the reply (we don't need it).
            self._sender.call(
                _DAEMON_NAME,
                _DAEMON_PATH,
                _DAEMON_IFACE,
                "TriggerHaptic",
                GLib.Variant("(s)", ("notification",)),
                None,
                Gio.DBusCallFlags.NONE,
                1000,
                None,
                None,
            )
            print("[NotifHaptic] notification -> pulse")
        except Exception as e:
            print(f"[NotifHaptic] trigger failed: {e}")
