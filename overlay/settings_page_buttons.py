#!/usr/bin/env python3
"""
JuhRadial MX - Buttons Page

ButtonsPage: Actions Ring configuration and button assignment UI.

SPDX-License-Identifier: GPL-3.0
"""

import logging
import math

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, GLib, Pango

from i18n import _
from settings_config import ConfigManager
from settings_constants import MOUSE_BUTTONS, translate_radial_label
from settings_dialogs import SliceConfigDialog

logger = logging.getLogger(__name__)


class ButtonsPage(Gtk.ScrolledWindow):
    """Buttons configuration page - Premium UI Design"""

    # Icon mapping for each button type
    BUTTON_ICONS = {
        "middle": "input-mouse-symbolic",
        "shift_wheel": "media-playlist-shuffle-symbolic",
        "forward": "go-next-symbolic",
        "horizontal_scroll": "object-flip-horizontal-symbolic",
        "back": "go-previous-symbolic",
        "gesture": "input-touchpad-symbolic",
        "thumb": "view-app-grid-symbolic",
    }

    # Color hex values for slice indicators
    SLICE_COLORS = {
        "green": "#00e676",
        "yellow": "#ffd54f",
        "red": "#ff5252",
        "mauve": "#b388ff",
        "blue": "#4a9eff",
        "pink": "#ff80ab",
        "sapphire": "#00b4d8",
        "teal": "#0abdc6",
    }

    # Evdev button code to friendly name
    BUTTON_NAMES = {
        0x110: "Left Click",
        0x111: "Right Click",
        0x112: "Middle Click",
        0x113: "Side Button (Button 8)",
        0x114: "Extra Button (Button 9)",
        0x115: "Forward",
        0x116: "Back",
        0x117: "Task",
    }

    def __init__(self, on_button_config=None, parent_window=None, config_manager=None, generic_mode=False):
        super().__init__()
        self.on_button_config = on_button_config
        self.parent_window = parent_window
        self.config_manager = config_manager
        self._generic_mode = generic_mode
        self._capturing = False
        self._capture_timer = None
        self.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
        )  # Allow horizontal scroll when needed

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)

        # =============================================
        # ACTIONS RING CARD - Shows all 8 slices
        # =============================================
        radial_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        radial_card.add_css_class("radial-menu-card")

        # Header row
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        header_row.set_margin_bottom(12)

        # Large radial icon
        radial_icon_box = Gtk.Box()
        radial_icon_box.add_css_class("radial-icon-large")
        radial_icon_box.set_valign(Gtk.Align.CENTER)
        radial_icon = Gtk.Image.new_from_icon_name("view-app-grid-symbolic")
        radial_icon.set_pixel_size(28)
        radial_icon_box.append(radial_icon)
        header_row.append(radial_icon_box)

        # Text content
        radial_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        radial_text.set_hexpand(True)
        radial_text.set_valign(Gtk.Align.CENTER)

        radial_title = Gtk.Label(label=_("Actions Ring"))
        radial_title.set_halign(Gtk.Align.START)
        radial_title.add_css_class("radial-title")
        radial_text.append(radial_title)

        radial_subtitle = Gtk.Label(label=_("Click any action to customize"))
        radial_subtitle.set_halign(Gtk.Align.START)
        radial_subtitle.add_css_class("radial-subtitle")
        radial_text.append(radial_subtitle)

        header_row.append(radial_text)
        radial_card.append(header_row)

        # Interactive circular Actions Ring (mirrors the on-screen radial menu,
        # matching the Logi Options+ style). Each slice is a clickable chip
        # placed radially over a coloured ring.
        self._ring_holder = Gtk.Box()
        self._ring_holder.set_halign(Gtk.Align.CENTER)
        self._ring_holder.set_margin_top(6)
        self._ring_holder.append(self._build_actions_ring(self._get_current_slices()))
        radial_card.append(self._ring_holder)
        content.append(radial_card)

        if self._generic_mode:
            # =============================================
            # GENERIC: TRIGGER BUTTON BINDING CARD
            # =============================================
            from settings_widgets import SettingsCard, SettingRow

            trigger_card = SettingsCard(_("Radial Wheel Trigger"))

            # Current binding display
            current_code = self.config_manager.get(
                "generic_trigger_button", default=0x113
            )
            current_name = self.BUTTON_NAMES.get(
                current_code, f"Button {current_code:#x}"
            )

            trigger_row = SettingRow(
                _("Trigger Button"),
                _("Mouse button that opens the radial wheel"),
            )

            trigger_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self._trigger_label = Gtk.Label(label=current_name)
            self._trigger_label.add_css_class("heading")
            trigger_box.append(self._trigger_label)

            self._bind_btn = Gtk.Button(label=_("Rebind"))
            self._bind_btn.add_css_class("suggested-action")
            self._bind_btn.connect("clicked", self._on_start_capture)
            trigger_box.append(self._bind_btn)

            trigger_row.set_control(trigger_box)
            trigger_card.append(trigger_row)
            content.append(trigger_card)
        else:
            # =============================================
            # LOGITECH: EASY-SWITCH SHORTCUTS CARD
            # =============================================
            easyswitch_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            easyswitch_card.add_css_class("easyswitch-shortcuts-card")

            easyswitch_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            easyswitch_row.add_css_class("easyswitch-row")

            es_icon_box = Gtk.Box()
            es_icon_box.add_css_class("easyswitch-icon-box")
            es_icon_box.set_valign(Gtk.Align.CENTER)
            es_icon = Gtk.Image.new_from_icon_name("network-wireless-symbolic")
            es_icon.set_pixel_size(20)
            es_icon.add_css_class("easyswitch-icon")
            es_icon_box.append(es_icon)
            easyswitch_row.append(es_icon_box)

            es_text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            es_text_box.set_hexpand(True)
            es_text_box.set_valign(Gtk.Align.CENTER)

            es_title = Gtk.Label(label=_("Easy-Switch Shortcuts"))
            es_title.set_halign(Gtk.Align.START)
            es_title.add_css_class("easyswitch-title")
            es_text_box.append(es_title)

            es_desc = Gtk.Label(label=_("Replace Emoji with Easy-Switch 1, 2, 3 submenu"))
            es_desc.set_halign(Gtk.Align.START)
            es_desc.add_css_class("easyswitch-desc")
            es_text_box.append(es_desc)

            easyswitch_row.append(es_text_box)

            self.easyswitch_switch = Gtk.Switch()
            self.easyswitch_switch.set_valign(Gtk.Align.CENTER)
            self.easyswitch_switch.set_active(
                self.config_manager.get(
                    "radial_menu", "easy_switch_shortcuts", default=False
                )
            )
            self.easyswitch_switch.connect("state-set", self._on_easyswitch_toggled)
            easyswitch_row.append(self.easyswitch_switch)

            easyswitch_card.append(easyswitch_row)
            content.append(easyswitch_card)

            # =============================================
            # LOGITECH: BUTTON ASSIGNMENTS CARD
            # =============================================
            assignments_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            assignments_card.add_css_class("button-assignment-card")

            header = Gtk.Label(label=_("Button Assignments"))
            header.set_halign(Gtk.Align.START)
            header.add_css_class("button-assignment-header")
            assignments_card.append(header)

            self.button_rows = {}
            self.action_labels = {}

            for btn_id, btn_info in MOUSE_BUTTONS.items():
                row = self._create_button_row(btn_id, btn_info)
                self.button_rows[btn_id] = row
                assignments_card.append(row)

            content.append(assignments_card)
        self.set_child(content)

    def _create_button_row(self, btn_id, btn_info):
        """Create a premium styled button assignment row"""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.add_css_class("button-row")

        # Icon box
        icon_box = Gtk.Box()
        icon_box.add_css_class("button-icon-box")
        icon_box.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name(
            self.BUTTON_ICONS.get(btn_id, "input-mouse-symbolic")
        )
        icon.set_pixel_size(20)
        icon.add_css_class("button-icon")
        icon_box.append(icon)
        row.append(icon_box)

        # Text content
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        text_box.set_hexpand(True)
        text_box.set_valign(Gtk.Align.CENTER)

        name_label = Gtk.Label(label=btn_info["name"])
        name_label.set_halign(Gtk.Align.START)
        name_label.add_css_class("button-name")
        text_box.append(name_label)

        # Action badge
        action_label = Gtk.Label(label=btn_info["action"])
        action_label.set_halign(Gtk.Align.START)
        action_label.add_css_class("button-action")
        text_box.append(action_label)
        self.action_labels[btn_id] = action_label

        row.append(text_box)

        # Arrow button
        arrow = Gtk.Button()
        arrow.set_child(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        arrow.add_css_class("button-arrow")
        arrow.add_css_class("flat")
        arrow.set_valign(Gtk.Align.CENTER)
        arrow.connect("clicked", lambda _, bid=btn_id: self._on_button_click(bid))
        row.append(arrow)

        # Make entire row clickable
        row_click = Gtk.GestureClick()
        row_click.connect(
            "released", lambda g, n, x, y, bid=btn_id: self._on_button_click(bid)
        )
        row.add_controller(row_click)

        return row

    def _on_button_click(self, button_id):
        """Handle button configuration click"""
        if self.on_button_config:
            self.on_button_config(button_id)

    def refresh_button_labels(self):
        """Refresh the button action labels after config change"""
        for btn_id, action_label in self.action_labels.items():
            if btn_id in MOUSE_BUTTONS:
                action_label.set_text(MOUSE_BUTTONS[btn_id]["action"])

    def _get_current_slices(self):
        """Get the current radial menu slices from config"""
        if self.config_manager:
            slices = self.config_manager.get("radial_menu", "slices", default=[])
            if slices:
                return slices
        # Return defaults if no config
        return ConfigManager.DEFAULT_CONFIG["radial_menu"]["slices"]

    # =================================================================
    # Circular Actions Ring (Logi Options+ style)
    # =================================================================
    RING_SIZE = 340
    RING_INNER = 70
    RING_OUTER = 158

    def _hex_rgb(self, color_name):
        h = self.SLICE_COLORS.get(color_name, "#0abdc6")
        return (
            int(h[1:3], 16) / 255.0,
            int(h[3:5], 16) / 255.0,
            int(h[5:7], 16) / 255.0,
        )

    def _build_actions_ring(self, slices):
        """Build the interactive circular ring of slices.

        Geometry mirrors the on-screen radial menu: 8 sectors of 45 degrees,
        slice 0 centred at the top, going clockwise.
        """
        size = self.RING_SIZE
        cx = cy = size / 2.0
        icon_r = (self.RING_INNER + self.RING_OUTER) / 2.0

        overlay = Gtk.Overlay()
        overlay.set_size_request(size, size)

        ring_bg = Gtk.DrawingArea()
        ring_bg.set_size_request(size, size)
        ring_bg.set_draw_func(self._draw_ring, list(slices))
        overlay.set_child(ring_bg)
        # Hover highlight is painted on the ring itself (as the wedge shape),
        # not on the chips, so the chips stay transparent.
        self._ring_bg = ring_bg
        self._ring_hover = -1

        fixed = Gtk.Fixed()
        fixed.set_size_request(size, size)

        chip_w, chip_h = 90, 58
        for i, slice_data in enumerate(slices[:8]):
            chip = self._make_slice_chip(i, slice_data)
            chip.set_size_request(chip_w, chip_h)
            ang = math.radians(i * 45 - 90)
            x = cx + icon_r * math.cos(ang) - chip_w / 2.0
            y = cy + icon_r * math.sin(ang) - chip_h / 2.0
            fixed.put(chip, x, y)

        center = Gtk.Label(label=_("Actions\nRing"))
        center.set_justify(Gtk.Justification.CENTER)
        center.add_css_class("ring-center-label")
        center.set_size_request(2 * self.RING_INNER - 16, 40)
        fixed.put(center, cx - (self.RING_INNER - 8), cy - 20)

        overlay.add_overlay(fixed)
        return overlay

    def _draw_ring(self, area, cr, width, height, slices):
        """Paint the coloured donut segments behind the slice chips."""
        cx, cy = width / 2.0, height / 2.0
        inner, outer = self.RING_INNER, self.RING_OUTER

        hover = getattr(self, "_ring_hover", -1)
        for i, sd in enumerate(slices[:8]):
            a0 = math.radians(i * 45 - 22.5 - 90)
            a1 = math.radians(i * 45 + 22.5 - 90)
            r, g, b = self._hex_rgb(sd.get("color", "teal"))
            hot = (i == hover)

            cr.new_path()
            cr.arc(cx, cy, outer, a0, a1)
            cr.arc_negative(cx, cy, inner, a1, a0)
            cr.close_path()
            cr.set_source_rgba(r, g, b, 0.40 if hot else 0.16)
            cr.fill_preserve()
            cr.set_source_rgba(r, g, b, 0.95 if hot else 0.55)
            cr.set_line_width(1.5 if hot else 1.0)
            cr.stroke()

            if hot:
                # Soft white lift over the hovered wedge for extra feedback
                cr.new_path()
                cr.arc(cx, cy, outer, a0, a1)
                cr.arc_negative(cx, cy, inner, a1, a0)
                cr.close_path()
                cr.set_source_rgba(1, 1, 1, 0.07)
                cr.fill()

            # Brighter accent arc on the outer rim
            cr.new_path()
            cr.arc(cx, cy, outer - 1.5, a0 + 0.02, a1 - 0.02)
            cr.set_source_rgba(r, g, b, 1.0 if hot else 0.9)
            cr.set_line_width(4.0 if hot else 2.5)
            cr.stroke()

        # Centre disc
        cr.new_path()
        cr.arc(cx, cy, inner - 4, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 0.04)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.set_line_width(1.0)
        cr.stroke()

    def _make_slice_chip(self, index, slice_data):
        """A clickable icon+label chip for one slice.

        Plain Gtk.Box (not a Gtk.Button) so there is no theme hover/active
        rectangle — the hover feedback is the ring wedge instead.
        """
        chip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        chip.add_css_class("ring-slice-chip")
        chip.set_halign(Gtk.Align.CENTER)
        chip.set_valign(Gtk.Align.CENTER)
        try:
            chip.set_cursor_from_name("pointer")
        except Exception:
            pass

        icon = Gtk.Image.new_from_icon_name(
            slice_data.get("icon", "application-x-executable-symbolic")
        )
        icon.set_pixel_size(22)
        icon.add_css_class("ring-slice-icon")
        chip.append(icon)

        label = Gtk.Label(
            label=translate_radial_label(
                slice_data.get("label", f"Slice {index + 1}"),
                slice_data.get("action_id"),
            )
        )
        label.add_css_class("ring-slice-label")
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(11)
        label.set_justify(Gtk.Justification.CENTER)
        chip.append(label)

        # Click opens the slice editor; hover lights the matching ring wedge.
        click = Gtk.GestureClick()
        click.connect(
            "released", lambda g, n, x, y, idx=index: self._on_edit_slice(idx)
        )
        chip.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_a, idx=index: self._set_ring_hover(idx))
        motion.connect("leave", lambda *_a: self._set_ring_hover(-1))
        chip.add_controller(motion)
        return chip

    def _set_ring_hover(self, index):
        """Highlight (or clear) the hovered ring wedge."""
        if getattr(self, "_ring_hover", -1) == index:
            return
        self._ring_hover = index
        if getattr(self, "_ring_bg", None) is not None:
            self._ring_bg.queue_draw()

    def _refresh_ring(self):
        """Rebuild the ring after slices change."""
        if not hasattr(self, "_ring_holder"):
            return
        child = self._ring_holder.get_first_child()
        if child:
            self._ring_holder.remove(child)
        self._ring_holder.append(self._build_actions_ring(self._get_current_slices()))

    def _on_edit_slice(self, slice_index):
        """Open dialog to edit a specific slice"""
        if self.parent_window:
            dialog = SliceConfigDialog(
                self.parent_window,
                slice_index,
                self.config_manager,
                self._on_slice_saved,
            )
            dialog.present()

    def _on_slice_saved(self):
        """Called when a slice is saved - rebuild the ring."""
        self._refresh_ring()

    def _on_easyswitch_toggled(self, switch, state):
        """Handle Easy-Switch shortcuts toggle"""
        self.config_manager.set("radial_menu", "easy_switch_shortcuts", state)
        self.config_manager.save()
        # The Emoji slice label flips to "Easy-Switch"; rebuild to reflect it.
        self._refresh_ring()
        return False  # Allow switch to change state

    # =================================================================
    # GENERIC MODE: Mouse button capture for trigger binding
    # =================================================================

    def _on_start_capture(self, button):
        """Start listening for a mouse button press to bind as trigger."""
        if self._capturing:
            return
        self._capturing = True
        self._bind_btn.set_label(_("Press a mouse button..."))
        self._bind_btn.set_sensitive(False)
        self._trigger_label.set_text(_("Waiting..."))

        # Use GestureClick to capture any mouse button on the entire window
        self._capture_gesture = Gtk.GestureClick()
        self._capture_gesture.set_button(0)  # Listen for any button
        self._capture_gesture.connect("pressed", self._on_button_captured)
        self.get_root().add_controller(self._capture_gesture)

        # Timeout after 5 seconds
        self._capture_timer = GLib.timeout_add(5000, self._capture_timeout)

    def _on_button_captured(self, gesture, n_press, x, y):
        """A mouse button was pressed - bind it as the trigger."""
        if not self._capturing:
            return
        button_num = gesture.get_current_button()
        # Map GTK button numbers to evdev codes
        # GTK: 1=left, 2=middle, 3=right, 8=side, 9=extra
        gtk_to_evdev = {1: 0x110, 2: 0x112, 3: 0x111, 8: 0x113, 9: 0x114}
        evdev_code = gtk_to_evdev.get(button_num, 0x110 + button_num - 1)

        name = self.BUTTON_NAMES.get(evdev_code, f"Button {button_num}")
        self._trigger_label.set_text(name)
        self._bind_btn.set_label(_("Rebind"))
        self._bind_btn.set_sensitive(True)

        # Save to config
        self.config_manager.set("generic_trigger_button", evdev_code, auto_save=True)

        self._stop_capture()

    def _capture_timeout(self):
        """Cancel capture after timeout."""
        if self._capturing:
            current_code = self.config_manager.get(
                "generic_trigger_button", default=0x113
            )
            name = self.BUTTON_NAMES.get(current_code, f"Button {current_code:#x}")
            self._trigger_label.set_text(name)
            self._bind_btn.set_label(_("Rebind"))
            self._bind_btn.set_sensitive(True)
            self._stop_capture()
        return False  # Don't repeat

    def _stop_capture(self):
        """Clean up capture state."""
        self._capturing = False
        if self._capture_timer:
            GLib.source_remove(self._capture_timer)
            self._capture_timer = None
        if hasattr(self, "_capture_gesture") and self._capture_gesture:
            self.get_root().remove_controller(self._capture_gesture)
            self._capture_gesture = None
