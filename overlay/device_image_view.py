"""Device image view with positioned hotspots + a drag-to-edit base.

Renders a device descriptor's image with a marker over each hotspot. In edit
mode the markers become draggable; positions are written back to the
descriptor (as fractions) so the caller can persist a user override. This is
the lightweight GTK counterpart of Logitune's QML hotspot editor.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gtk, Gdk, GdkPixbuf  # noqa: E402

from device_descriptors import resolve_image_path, save_user_override  # noqa: E402

_MARKER = 18  # marker diameter (px)

_CSS = b"""
.hotspot-marker {
  background-color: alpha(@accent_color, 0.85);
  border: 2px solid white;
  border-radius: 9999px;
  min-width: 14px;
  min-height: 14px;
}
.hotspot-marker.scroll { background-color: alpha(#3584e4, 0.85); }
.hotspot-marker.easyswitch {
  background-color: alpha(#2ec27e, 0.85);
  min-width: 10px;
  min-height: 10px;
}
.hotspot-marker.editable { border-color: #f6d32d; }
"""

_css_loaded = False


def _ensure_css():
    global _css_loaded
    if _css_loaded:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    _css_loaded = True


class DeviceImageView(Gtk.Box):
    """A device image with hotspot markers; markers are draggable in edit mode."""

    def __init__(self, descriptor, display_width: int = 320, on_dirty=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        _ensure_css()

        self.descriptor = descriptor
        self.on_dirty = on_dirty
        self._editable = False
        self._dirty = False
        self._marker_pos: dict = {}      # marker -> (x, y) top-left in canvas
        self._marker_hotspot: dict = {}  # marker -> Hotspot (only draggable ones)
        self._markers: list = []

        self._disp_w = display_width
        self._disp_h = display_width
        pixbuf = None
        img_path = resolve_image_path(descriptor.image)
        if img_path:
            try:
                src = GdkPixbuf.Pixbuf.new_from_file(str(img_path))
                if src.get_width() > 0:
                    self._disp_h = int(
                        display_width * src.get_height() / src.get_width()
                    )
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(img_path), self._disp_w, self._disp_h, False
                )
            except Exception:  # noqa: BLE001 - image is optional
                pixbuf = None

        self.canvas = Gtk.Fixed()
        self.canvas.set_size_request(self._disp_w, self._disp_h)
        self.canvas.set_halign(Gtk.Align.CENTER)

        if pixbuf is not None:
            texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            picture = Gtk.Picture.new_for_paintable(texture)
            picture.set_size_request(self._disp_w, self._disp_h)
            self.canvas.put(picture, 0, 0)
        else:
            placeholder = Gtk.Image.new_from_icon_name("input-mouse-symbolic")
            placeholder.set_pixel_size(96)
            placeholder.set_size_request(self._disp_w, self._disp_h)
            self.canvas.put(placeholder, 0, 0)

        self._build_markers()
        self.append(self.canvas)

    # ------------------------------------------------------------------
    def _make_marker(self, css_extra: str, tooltip: str) -> Gtk.Box:
        marker = Gtk.Box()
        marker.add_css_class("hotspot-marker")
        if css_extra:
            marker.add_css_class(css_extra)
        marker.set_size_request(_MARKER, _MARKER)
        if tooltip:
            marker.set_tooltip_text(tooltip)
        return marker

    def _place(self, marker: Gtk.Box, x_pct: float, y_pct: float):
        x = x_pct * self._disp_w - _MARKER / 2
        y = y_pct * self._disp_h - _MARKER / 2
        self.canvas.put(marker, x, y)
        self._marker_pos[marker] = (x, y)

    def _build_markers(self):
        for h in self.descriptor.hotspots:
            css = "scroll" if h.kind in ("scrollwheel", "thumbwheel", "pointer") else ""
            marker = self._make_marker(css, h.label or (h.cid or h.kind))
            self._place(marker, h.x_pct, h.y_pct)
            self._marker_hotspot[marker] = h
            self._markers.append(marker)
            self._attach_drag(marker)

        # Easy-Switch slots: shown as static green dots (not part of the editor base).
        for slot in self.descriptor.easy_switch_slots:
            marker = self._make_marker("easyswitch", slot.label or "Easy-Switch")
            self._place(marker, slot.x_pct, slot.y_pct)

    # ------------------------------------------------------------------
    def _attach_drag(self, marker: Gtk.Box):
        drag = Gtk.GestureDrag()

        def on_begin(_g, _sx, _sy):
            marker._drag_start = self._marker_pos[marker]

        def on_update(_g, off_x, off_y):
            if not self._editable:
                return
            sx, sy = getattr(marker, "_drag_start", self._marker_pos[marker])
            nx = max(-_MARKER / 2, min(self._disp_w - _MARKER / 2, sx + off_x))
            ny = max(-_MARKER / 2, min(self._disp_h - _MARKER / 2, sy + off_y))
            self.canvas.move(marker, nx, ny)
            self._marker_pos[marker] = (nx, ny)

        def on_end(_g, _ox, _oy):
            if not self._editable:
                return
            nx, ny = self._marker_pos[marker]
            hotspot = self._marker_hotspot[marker]
            hotspot.x_pct = max(0.0, min(1.0, (nx + _MARKER / 2) / self._disp_w))
            hotspot.y_pct = max(0.0, min(1.0, (ny + _MARKER / 2) / self._disp_h))
            self._set_dirty(True)

        drag.connect("drag-begin", on_begin)
        drag.connect("drag-update", on_update)
        drag.connect("drag-end", on_end)
        marker.add_controller(drag)

    # ------------------------------------------------------------------
    def set_editable(self, editable: bool):
        self._editable = editable
        for marker in self._markers:
            if editable:
                marker.add_css_class("editable")
                marker.set_cursor(Gdk.Cursor.new_from_name("move", None))
            else:
                marker.remove_css_class("editable")
                marker.set_cursor(None)

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def _set_dirty(self, dirty: bool):
        self._dirty = dirty
        if self.on_dirty:
            self.on_dirty(dirty)

    def save(self) -> bool:
        """Persist the current hotspot positions as a user override."""
        try:
            save_user_override(self.descriptor)
            self._set_dirty(False)
            return True
        except OSError:
            return False
