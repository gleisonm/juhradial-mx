#!/usr/bin/env python3
"""
JuhRadial MX - Application Profile Dialogs

AddApplicationDialog, ApplicationProfilesGridDialog, and AppProfileSlicesDialog
for per-application radial menu profiles.

SPDX-License-Identifier: GPL-3.0
"""

import logging
import json
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw

from i18n import _
from settings_config import ConfigManager, config
from settings_constants import (
    RADIAL_ACTIONS,
    find_radial_action_index,
)
from settings_widgets import SettingsCard

logger = logging.getLogger(__name__)


class AddApplicationDialog(Adw.Window):
    """Dialog for adding a per-application profile"""

    def __init__(self, parent):
        super().__init__()
        self.parent_window = parent
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_title(_("Add Application Profile"))
        self.set_default_size(500, 600)

        # Main content
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        header.set_show_start_title_buttons(False)

        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_btn)

        main_box.append(header)

        # Content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(20)
        content.set_margin_end(20)

        # Description
        desc = Gtk.Label(
            label=_(
                "Create a custom profile for a specific application.\nThe radial menu will use this profile when the application is active."
            )
        )
        desc.set_wrap(True)
        desc.set_margin_bottom(16)
        content.append(desc)

        # Application selection
        app_card = SettingsCard(_("Select Application"))

        # Running apps list
        running_label = Gtk.Label(label=_("Running Applications:"))
        running_label.set_halign(Gtk.Align.START)
        running_label.set_margin_top(8)
        app_card.append(running_label)

        # Scrollable app list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(200)

        self.app_list = Gtk.ListBox()
        self.app_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.app_list.add_css_class("boxed-list")

        # Get running applications
        self._populate_running_apps()

        scrolled.set_child(self.app_list)
        app_card.append(scrolled)

        # Or enter manually
        manual_label = Gtk.Label(label=_("Or enter application class manually:"))
        manual_label.set_halign(Gtk.Align.START)
        manual_label.set_margin_top(16)
        app_card.append(manual_label)

        self.app_entry = Gtk.Entry()
        self.app_entry.set_placeholder_text(_("e.g., firefox, code, gimp"))
        self.app_entry.set_margin_top(8)
        app_card.append(self.app_entry)

        content.append(app_card)

        # Add button
        add_btn = Gtk.Button(label=_("Add Profile"))
        add_btn.add_css_class("suggested-action")
        add_btn.set_margin_top(16)
        add_btn.connect("clicked", self._on_add_clicked)
        content.append(add_btn)

        main_box.append(content)
        self.set_content(main_box)

    def _populate_running_apps(self):
        """Get list of running applications using D-Bus and process detection"""
        import subprocess
        import re

        apps = set()

        try:
            # Method 1: Get running KDE apps from D-Bus session bus
            # Apps register as org.kde.<appname>-<pid> or similar patterns
            result = subprocess.run(
                ["qdbus-qt6"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    line = line.strip()
                    # Match patterns like org.kde.dolphin-12345
                    match = re.match(r"org\.kde\.(\w+)-\d+", line)
                    if match:
                        app_name = match.group(1)
                        if app_name not in (
                            "KWin",
                            "plasmashell",
                            "kded",
                            "kglobalaccel",
                        ):
                            apps.add(app_name)
                    # Also match org.mozilla.firefox, org.chromium, etc.
                    match = re.match(r"org\.(\w+)\.(\w+)", line)
                    if match:
                        org, app = match.group(1), match.group(2)
                        if org in ("mozilla", "chromium", "gnome", "gtk"):
                            apps.add(app.lower())
        except Exception as e:
            logger.debug("D-Bus app detection failed: %s", e)

        try:
            # Method 2: Check for GUI processes with known .desktop files
            # Look at running processes and match against installed apps
            desktop_dirs = [
                Path("/usr/share/applications"),
                Path.home() / ".local/share/applications",
                Path("/var/lib/flatpak/exports/share/applications"),
                Path.home() / ".local/share/flatpak/exports/share/applications",
            ]

            # Get all installed app names from .desktop files
            installed_apps = {}
            for desktop_dir in desktop_dirs:
                if desktop_dir.exists():
                    for desktop_file in desktop_dir.glob("*.desktop"):
                        try:
                            content = desktop_file.read_text()
                            # Extract Exec line to get binary name
                            for line in content.split("\n"):
                                if line.startswith("Exec="):
                                    exec_cmd = line[5:].split()[
                                        0
                                    ]  # Get first word after Exec=
                                    binary = Path(exec_cmd).name
                                    # Map binary to desktop file name (app name)
                                    app_name = desktop_file.stem
                                    # Use shorter name if it's a reverse-domain style
                                    if "." in app_name:
                                        parts = app_name.split(".")
                                        app_name = (
                                            parts[-1] if len(parts) > 2 else app_name
                                        )
                                    installed_apps[binary] = app_name
                                    break
                        except (IOError, OSError, UnicodeDecodeError):
                            pass  # Desktop file not readable

            # Get running process names
            result = subprocess.run(
                ["ps", "-eo", "comm", "--no-headers"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                running_procs = set(result.stdout.strip().split("\n"))
                for proc in running_procs:
                    proc = proc.strip()
                    if proc in installed_apps:
                        apps.add(installed_apps[proc])
                    # Also check common GUI apps directly
                    elif proc in (
                        "firefox",
                        "chrome",
                        "chromium",
                        "code",
                        "konsole",
                        "dolphin",
                        "kate",
                        "okular",
                        "gwenview",
                        "spectacle",
                        "gimp",
                        "blender",
                        "inkscape",
                        "kwrite",
                        "vlc",
                        "mpv",
                        "obs",
                        "slack",
                        "discord",
                        "telegram-desktop",
                        "signal-desktop",
                        "spotify",
                        "thunderbird",
                        "evolution",
                        "nautilus",
                        "gedit",
                    ):
                        apps.add(proc)
        except Exception as e:
            logger.debug("Process detection failed: %s", e)

        # Add some common apps that user might want (grayed out if not detected)
        common_apps = [
            "firefox",
            "chrome",
            "code",
            "gimp",
            "blender",
            "inkscape",
            "libreoffice",
            "konsole",
            "dolphin",
            "okular",
            "gwenview",
            "kate",
            "kwrite",
            "spectacle",
            "vlc",
            "obs",
        ]

        # Combine detected apps with common apps (detected first)
        all_apps = list(apps)
        for app in common_apps:
            if app not in apps:
                all_apps.append(app)

        # Populate list
        for app in all_apps[:30]:  # Limit to 30 apps
            row = Adw.ActionRow()
            row.set_title(app)
            row.app_name = app

            # Mark as running if detected
            if app in apps:
                row.set_subtitle(_("Running"))

            # Add checkmark suffix (hidden initially)
            check = Gtk.Image.new_from_icon_name("object-select-symbolic")
            check.set_visible(False)
            row.add_suffix(check)
            row.check_icon = check

            self.app_list.append(row)

        if not all_apps:
            # Add placeholder if nothing found
            row = Adw.ActionRow()
            row.set_title(_("(Enter app name manually below)"))
            self.app_list.append(row)

        self.app_list.connect("row-selected", self._on_app_selected)

    def _on_app_selected(self, list_box, row):
        """Handle app selection"""
        # Clear all checkmarks
        child = list_box.get_first_child()
        while child:
            if hasattr(child, "check_icon"):
                child.check_icon.set_visible(False)
            child = child.get_next_sibling()

        # Show checkmark on selected
        if row and hasattr(row, "check_icon"):
            row.check_icon.set_visible(True)
            if hasattr(row, "app_name"):
                self.app_entry.set_text(row.app_name)

    def _on_add_clicked(self, button):
        """Add the application profile"""
        app_name = self.app_entry.get_text().strip()

        if not app_name:
            dialog = Adw.AlertDialog(
                heading=_("No Application Selected"),
                body=_("Please select or enter an application name."),
            )
            dialog.add_response("ok", _("OK"))
            dialog.present(self)
            return

        # Save profile
        profile_path = Path.home() / ".config" / "juhradial" / "profiles.json"

        try:
            profiles = {}
            if profile_path.exists():
                with open(profile_path, "r", encoding="utf-8") as f:
                    profiles = json.load(f)

            # Create app-specific profile (copy current radial layout)
            default_slices = config.get(
                "radial_menu",
                "slices",
                default=ConfigManager.DEFAULT_CONFIG["radial_menu"]["slices"],
            )
            profiles[app_name] = {
                "name": app_name,
                "slices": json.loads(json.dumps(default_slices)),
                "app_class": app_name,
            }

            # Atomic write
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = str(profile_path) + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(profiles, f, indent=2)
            os.replace(tmp_path, str(profile_path))

            logger.info("Created profile for: %s", app_name)

            if hasattr(self.parent_window, "show_toast"):
                self.parent_window.show_toast(
                    _("Profile created for {}").format(app_name)
                )

            self.close()

        except Exception as e:
            logger.error("Failed to save profile: %s", e)
            dialog = Adw.AlertDialog(
                heading=_("Error"), body=_("Failed to create profile: {}").format(e)
            )
            dialog.add_response("ok", _("OK"))
            dialog.present(self)


class ApplicationProfilesGridDialog(Adw.Window):
    """Grid view dialog for per-application profiles"""

    def __init__(self, parent):
        super().__init__()
        self.parent_window = parent
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_title(_("Application Profiles"))
        self.set_default_size(780, 560)
        self.add_css_class("background")

        self.profile_path = Path.home() / ".config" / "juhradial" / "profiles.json"

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        header.set_show_start_title_buttons(False)

        close_btn = Gtk.Button(label=_("Close"))
        close_btn.add_css_class("secondary-btn")
        close_btn.connect("clicked", lambda _: self.close())
        header.pack_start(close_btn)

        refresh_btn = Gtk.Button(label=_("Refresh"))
        refresh_btn.add_css_class("primary-btn")
        refresh_btn.connect("clicked", lambda _: self._reload_grid())
        header.pack_end(refresh_btn)

        main_box.append(header)

        self.status_label = Gtk.Label()
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_margin_top(12)
        self.status_label.set_margin_start(20)
        self.status_label.set_margin_end(20)
        self.status_label.add_css_class("dim-label")
        main_box.append(self.status_label)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.grid = Gtk.Grid()
        self.grid.set_column_spacing(12)
        self.grid.set_row_spacing(12)
        self.grid.set_margin_top(12)
        self.grid.set_margin_bottom(20)
        self.grid.set_margin_start(20)
        self.grid.set_margin_end(20)

        scrolled.set_child(self.grid)
        main_box.append(scrolled)

        self.set_content(main_box)
        self._reload_grid()

    def _load_profiles(self):
        """Load profiles dict from profiles.json"""
        if not self.profile_path.exists():
            return {}

        try:
            with open(self.profile_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error("Failed to load profiles: %s", e)
            return {}

    def _save_profiles(self, profiles):
        """Save profiles dict to profiles.json atomically"""
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = str(self.profile_path) + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=2)
        os.replace(tmp_path, str(self.profile_path))

    def _create_profile_card(self, app_name, profile):
        """Create a card widget for one app profile"""
        card = SettingsCard(app_name)
        card.set_size_request(240, -1)
        card.set_margin_top(4)
        card.set_margin_bottom(4)
        card.set_margin_start(4)
        card.set_margin_end(4)

        icon = Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
        icon.set_pixel_size(28)
        icon.set_halign(Gtk.Align.START)
        card.append(icon)

        slices = profile.get("slices", []) if isinstance(profile, dict) else []
        configured = 0
        for s in slices:
            if isinstance(s, dict) and s.get("type") and s.get("type") != "none":
                configured += 1

        subtitle = Gtk.Label(label=_("Slices: {}/8 configured").format(configured))
        subtitle.set_halign(Gtk.Align.START)
        subtitle.set_xalign(0.0)
        subtitle.add_css_class("dim-label")
        card.append(subtitle)

        actions_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        edit_btn = Gtk.Button(label=_("Edit Slices"))
        edit_btn.add_css_class("primary-btn")
        edit_btn.connect("clicked", self._on_edit_profile, app_name)
        actions_row.append(edit_btn)

        remove_btn = Gtk.Button(label=_("Remove"))
        remove_btn.add_css_class("danger-btn")
        remove_btn.connect("clicked", self._on_remove_profile, app_name)
        actions_row.append(remove_btn)

        card.append(actions_row)
        return card

    def _on_edit_profile(self, _button, app_name):
        dialog = AppProfileSlicesDialog(self, app_name)
        dialog.connect("close-request", lambda *_: self._reload_grid())
        dialog.present()

    def _on_remove_profile(self, _button, app_name):
        """Remove one app profile"""
        profiles = self._load_profiles()
        if app_name not in profiles:
            return

        try:
            del profiles[app_name]
            self._save_profiles(profiles)
            self._reload_grid()
            if hasattr(self.parent_window, "show_toast"):
                self.parent_window.show_toast(
                    _("Removed profile for {}").format(app_name)
                )
        except Exception as e:
            dialog = Adw.AlertDialog(
                heading=_("Error"),
                body=_("Failed to remove profile: {}").format(e),
            )
            dialog.add_response("ok", _("OK"))
            dialog.present(self)

    def _reload_grid(self):
        """Rebuild profile grid from disk"""
        while child := self.grid.get_first_child():
            self.grid.remove(child)

        profiles = self._load_profiles()
        app_profiles = [
            (name, data)
            for name, data in profiles.items()
            if name != "default" and isinstance(data, dict)
        ]
        app_profiles.sort(key=lambda item: item[0].lower())

        if not app_profiles:
            self.status_label.set_text(
                _("No application profiles yet. Use '+ Add Application' to create one.")
            )
            return

        self.status_label.set_text(_("Profiles: {}").format(len(app_profiles)))
        for idx, (app_name, profile) in enumerate(app_profiles):
            col = idx % 3
            row = idx // 3
            self.grid.attach(
                self._create_profile_card(app_name, profile), col, row, 1, 1
            )


class AppProfileSlicesDialog(Adw.Window):
    """Configure slices for one application profile"""

    def __init__(self, parent, app_name):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.add_css_class("background")
        self.parent_dialog = parent
        self.app_name = app_name
        self.profile_path = Path.home() / ".config" / "juhradial" / "profiles.json"
        self.set_title(_("Edit Profile: {}").format(app_name))
        self.set_default_size(560, 640)

        self.profile = self._load_profile(app_name)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        header.set_show_start_title_buttons(False)

        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.add_css_class("secondary-btn")
        cancel_btn.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_btn)

        save_btn = Gtk.Button(label=_("Save"))
        save_btn.add_css_class("primary-btn")
        save_btn.connect("clicked", self._on_save)
        header.pack_end(save_btn)
        main_box.append(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)

        desc = Gtk.Label(
            label=_(
                "Choose which action each slice should use when this application is active."
            )
        )
        desc.set_wrap(True)
        desc.set_halign(Gtk.Align.START)
        desc.set_xalign(0.0)
        desc.add_css_class("dim-label")
        content.append(desc)

        self.slice_dropdowns = {}
        slices = self.profile.get("slices", [])

        for i in range(8):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.add_css_class("setting-row")

            label = Gtk.Label(label=_("Slice {}").format(i + 1))
            label.set_width_chars(8)
            label.set_xalign(0.0)
            row.append(label)

            dropdown = Gtk.DropDown()
            action_names = [
                name
                for _action_id, name, _icon, _action_type, _command, _color in RADIAL_ACTIONS
            ]
            dropdown.set_model(Gtk.StringList.new(action_names))

            current_slice = (
                slices[i] if i < len(slices) and isinstance(slices[i], dict) else {}
            )
            current_action_id = current_slice.get("action_id")
            current_label = current_slice.get("label", "")

            selected_index = -1
            if current_action_id:
                for idx, (
                    action_id,
                    _name,
                    _icon,
                    _action_type,
                    _command,
                    _color,
                ) in enumerate(RADIAL_ACTIONS):
                    if action_id == current_action_id:
                        selected_index = idx
                        break
            if selected_index < 0 and current_label:
                selected_index = find_radial_action_index(current_label)
            if selected_index >= 0:
                dropdown.set_selected(selected_index)

            dropdown.set_hexpand(True)
            self.slice_dropdowns[i] = dropdown
            row.append(dropdown)
            content.append(row)

        self._build_hardware_section(content)

        scrolled.set_child(content)
        main_box.append(scrolled)
        self.set_content(main_box)

    def _build_hardware_section(self, content):
        """Per-app hardware overrides (DPI / SmartShift), Logitune-style.

        Each control has an 'Override' switch; when off, that setting is left
        out of the profile and the daemon keeps the user's global value.
        """
        device = self.profile.get("device", {})
        if not isinstance(device, dict):
            device = {}

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(8)
        content.append(sep)

        hw_label = Gtk.Label(label=_("Hardware (optional)"))
        hw_label.add_css_class("heading")
        hw_label.set_halign(Gtk.Align.START)
        hw_label.set_margin_top(8)
        content.append(hw_label)

        hw_desc = Gtk.Label(
            label=_(
                "Apply DPI / SmartShift only while this app is focused. "
                "Leave a switch off to keep your global setting."
            )
        )
        hw_desc.set_wrap(True)
        hw_desc.set_xalign(0.0)
        hw_desc.add_css_class("dim-label")
        content.append(hw_desc)

        # --- DPI override ---
        dpi_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        dpi_row.add_css_class("setting-row")
        dpi_lbl = Gtk.Label(label=_("Override DPI"))
        dpi_lbl.set_xalign(0.0)
        dpi_lbl.set_hexpand(True)
        dpi_row.append(dpi_lbl)
        self.dpi_override = Gtk.Switch()
        self.dpi_override.set_valign(Gtk.Align.CENTER)
        dpi_row.append(self.dpi_override)
        content.append(dpi_row)

        self.dpi_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 400, 8000, 50
        )
        self.dpi_scale.set_draw_value(True)
        self.dpi_scale.set_hexpand(True)
        self.dpi_scale.set_value(int(device.get("dpi", 1000)))
        content.append(self.dpi_scale)

        has_dpi = "dpi" in device
        self.dpi_override.set_active(has_dpi)
        self.dpi_scale.set_sensitive(has_dpi)
        self.dpi_override.connect(
            "state-set",
            lambda _s, state: (self.dpi_scale.set_sensitive(state), False)[1],
        )

        # --- SmartShift override ---
        ss_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        ss_row.add_css_class("setting-row")
        ss_lbl = Gtk.Label(label=_("Override SmartShift"))
        ss_lbl.set_xalign(0.0)
        ss_lbl.set_hexpand(True)
        ss_row.append(ss_lbl)
        self.ss_override = Gtk.Switch()
        self.ss_override.set_valign(Gtk.Align.CENTER)
        ss_row.append(self.ss_override)
        content.append(ss_row)

        ss_en_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        ss_en_lbl = Gtk.Label(label=_("SmartShift enabled"))
        ss_en_lbl.set_xalign(0.0)
        ss_en_lbl.set_hexpand(True)
        ss_en_row.append(ss_en_lbl)
        self.ss_enabled = Gtk.Switch()
        self.ss_enabled.set_valign(Gtk.Align.CENTER)
        ss_en_row.append(self.ss_enabled)
        content.append(ss_en_row)

        self.ss_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 1, 100, 1
        )
        self.ss_scale.set_draw_value(True)
        self.ss_scale.set_hexpand(True)
        self.ss_scale.set_value(int(device.get("smartshift_threshold", 50)))
        content.append(self.ss_scale)

        has_ss = ("smartshift_enabled" in device) or (
            "smartshift_threshold" in device
        )
        self.ss_override.set_active(has_ss)
        self.ss_enabled.set_active(bool(device.get("smartshift_enabled", True)))
        self.ss_enabled.set_sensitive(has_ss)
        self.ss_scale.set_sensitive(has_ss)

        def _toggle_ss(_s, state):
            self.ss_enabled.set_sensitive(state)
            self.ss_scale.set_sensitive(state)
            return False

        self.ss_override.connect("state-set", _toggle_ss)

    def _collect_device_settings(self):
        """Build the `device` dict from the hardware override widgets.

        Returns None when nothing is overridden (so the key is omitted)."""
        device = {}
        if getattr(self, "dpi_override", None) and self.dpi_override.get_active():
            device["dpi"] = int(self.dpi_scale.get_value())
        if getattr(self, "ss_override", None) and self.ss_override.get_active():
            device["smartshift_enabled"] = bool(self.ss_enabled.get_active())
            device["smartshift_threshold"] = int(self.ss_scale.get_value())
        return device or None

    def _load_profile(self, app_name):
        profiles = {}
        if self.profile_path.exists():
            try:
                with open(self.profile_path, "r", encoding="utf-8") as f:
                    profiles = json.load(f)
            except Exception:
                profiles = {}

        profile = profiles.get(app_name, {}) if isinstance(profiles, dict) else {}
        if not isinstance(profile, dict):
            profile = {}

        slices = profile.get("slices", [])
        if not isinstance(slices, list):
            slices = []

        while len(slices) < 8:
            slices.append({})

        profile["name"] = app_name
        profile["app_class"] = app_name
        profile["slices"] = slices[:8]
        return profile

    def _on_save(self, _button):
        new_slices = []
        for i in range(8):
            dropdown = self.slice_dropdowns[i]
            selected = dropdown.get_selected()
            if 0 <= selected < len(RADIAL_ACTIONS):
                action_id, label, icon, action_type, command, color = RADIAL_ACTIONS[
                    selected
                ]
                new_slices.append(
                    {
                        "label": label,
                        "action_id": action_id,
                        "type": action_type,
                        "command": command,
                        "color": color,
                        "icon": icon,
                    }
                )
            else:
                new_slices.append({})

        profiles = {}
        if self.profile_path.exists():
            try:
                with open(self.profile_path, "r", encoding="utf-8") as f:
                    profiles = json.load(f)
            except Exception:
                profiles = {}

        if not isinstance(profiles, dict):
            profiles = {}

        entry = {
            "name": self.app_name,
            "app_class": self.app_name,
            "slices": new_slices,
        }
        device = self._collect_device_settings()
        if device:
            entry["device"] = device
        profiles[self.app_name] = entry

        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = str(self.profile_path) + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=2)
        os.replace(tmp_path, str(self.profile_path))

        if hasattr(self.parent_dialog.parent_window, "show_toast"):
            self.parent_dialog.parent_window.show_toast(
                _("Updated profile for {}").format(self.app_name)
            )
        self.close()
