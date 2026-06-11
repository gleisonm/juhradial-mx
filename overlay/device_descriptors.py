"""Device descriptors — JSON-driven device metadata (Logitune-style).

A descriptor describes one mouse model: which product IDs it covers, which
HID++ capabilities it exposes (drives UI visibility), its remappable controls,
and hotspot positions over the device image. Descriptors live in:

  <repo>/devices/<id>/descriptor.json          (bundled, read-only)
  ~/.config/juhradial/devices/<id>/descriptor.json   (user overrides, editable)

User overrides win over bundled ones with the same id, which is how the visual
hotspot editor saves tweaks without touching the shipped files.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("juhradial.devices")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Control:
    cid: str
    name: str
    evdev: Optional[str] = None
    divertable: bool = False


@dataclass
class Hotspot:
    x_pct: float
    y_pct: float
    cid: Optional[str] = None
    kind: str = "button"
    label: Optional[str] = None
    side: str = "right"


@dataclass
class EasySwitchSlot:
    x_pct: float
    y_pct: float
    label: Optional[str] = None


@dataclass
class DeviceDescriptor:
    id: str
    name: str
    status: str = "beta"
    product_ids: list[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)
    dpi: Optional[dict] = None
    controls: list[Control] = field(default_factory=list)
    hotspots: list[Hotspot] = field(default_factory=list)
    easy_switch_slots: list[EasySwitchSlot] = field(default_factory=list)
    image: Optional[str] = None
    source_path: Optional[Path] = None

    def has_feature(self, name: str) -> bool:
        return bool(self.features.get(name, False))

    def matches_pid(self, pid: int) -> bool:
        for raw in self.product_ids:
            try:
                if int(raw, 16) == pid:
                    return True
            except (ValueError, TypeError):
                continue
        return False

    def to_json(self) -> dict:
        """Serialise back to the on-disk JSON shape (for the editor's save)."""
        obj: dict = {
            "$schema": "../schema.json",
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "productIds": list(self.product_ids),
        }
        if self.features:
            obj["features"] = dict(self.features)
        if self.dpi:
            obj["dpi"] = dict(self.dpi)
        if self.controls:
            obj["controls"] = [
                {
                    k: v
                    for k, v in {
                        "cid": c.cid,
                        "name": c.name,
                        "evdev": c.evdev,
                        "divertable": c.divertable,
                    }.items()
                    if v is not None
                }
                for c in self.controls
            ]
        if self.hotspots:
            obj["hotspots"] = [
                {
                    k: v
                    for k, v in {
                        "cid": h.cid,
                        "kind": h.kind,
                        "label": h.label,
                        "xPct": round(h.x_pct, 4),
                        "yPct": round(h.y_pct, 4),
                        "side": h.side,
                    }.items()
                    if v is not None
                }
                for h in self.hotspots
            ]
        if self.easy_switch_slots:
            obj["easySwitchSlots"] = [
                {
                    k: v
                    for k, v in {
                        "xPct": round(s.x_pct, 4),
                        "yPct": round(s.y_pct, 4),
                        "label": s.label,
                    }.items()
                    if v is not None
                }
                for s in self.easy_switch_slots
            ]
        if self.image:
            obj["image"] = self.image
        return obj


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _bundled_devices_dirs() -> list[Path]:
    here = Path(__file__).resolve().parent.parent
    return [
        here / "devices",
        Path("/usr/share/juhradialmx/devices"),
        Path("/usr/share/juhradial/devices"),
    ]


def user_devices_dir() -> Path:
    return Path.home() / ".config" / "juhradial" / "devices"


def _parse(obj: dict, source: Path) -> Optional[DeviceDescriptor]:
    try:
        controls = [
            Control(
                cid=c["cid"],
                name=c.get("name", c["cid"]),
                evdev=c.get("evdev"),
                divertable=bool(c.get("divertable", False)),
            )
            for c in obj.get("controls", [])
        ]
        hotspots = [
            Hotspot(
                x_pct=float(h["xPct"]),
                y_pct=float(h["yPct"]),
                cid=h.get("cid"),
                kind=h.get("kind", "button"),
                label=h.get("label"),
                side=h.get("side", "right"),
            )
            for h in obj.get("hotspots", [])
        ]
        slots = [
            EasySwitchSlot(
                x_pct=float(s["xPct"]),
                y_pct=float(s["yPct"]),
                label=s.get("label"),
            )
            for s in obj.get("easySwitchSlots", [])
        ]
        return DeviceDescriptor(
            id=obj["id"],
            name=obj.get("name", obj["id"]),
            status=obj.get("status", "beta"),
            product_ids=list(obj.get("productIds", [])),
            features=dict(obj.get("features", {})),
            dpi=obj.get("dpi"),
            controls=controls,
            hotspots=hotspots,
            easy_switch_slots=slots,
            image=obj.get("image"),
            source_path=source,
        )
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("Skipping malformed descriptor %s: %s", source, e)
        return None


def _scan_dir(root: Path, out: dict[str, DeviceDescriptor]):
    if not root.is_dir():
        return
    for sub in sorted(root.iterdir()):
        descriptor = sub / "descriptor.json"
        if not descriptor.is_file():
            continue
        try:
            with open(descriptor, "r", encoding="utf-8") as f:
                obj = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to read %s: %s", descriptor, e)
            continue
        parsed = _parse(obj, descriptor)
        if parsed:
            out[parsed.id] = parsed  # later dirs (user) override earlier (bundled)


def load_descriptors() -> list[DeviceDescriptor]:
    """Load all descriptors; user overrides win over bundled by id."""
    out: dict[str, DeviceDescriptor] = {}
    for d in _bundled_devices_dirs():
        _scan_dir(d, out)
    _scan_dir(user_devices_dir(), out)  # user overrides last
    return list(out.values())


def match_descriptor(
    name: Optional[str] = None, pid: Optional[int] = None
) -> Optional[DeviceDescriptor]:
    """Find the best descriptor for a connected device by PID, then name."""
    descriptors = load_descriptors()
    if pid is not None:
        for d in descriptors:
            if d.matches_pid(pid):
                return d
    if name:
        needle = name.strip().lower()
        # Exact, then substring either direction (e.g. "MX Master 4" vs "mx master 4 for business")
        for d in descriptors:
            if d.name.lower() == needle:
                return d
        for d in descriptors:
            dl = d.name.lower()
            if dl and (dl in needle or needle in dl):
                return d
    return None


def resolve_image_path(image: Optional[str]) -> Optional[Path]:
    """Resolve a descriptor image name against the bundled assets dir."""
    if not image:
        return None
    here = Path(__file__).resolve().parent.parent
    candidates = [
        here / "assets" / "devices" / image,
        Path("/usr/share/juhradialmx/assets/devices") / image,
        Path("/usr/share/juhradial/assets/devices") / image,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def save_user_override(descriptor: DeviceDescriptor) -> Path:
    """Write the descriptor to the user override dir (atomic). Returns the path."""
    dest_dir = user_devices_dir() / descriptor.id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "descriptor.json"
    tmp = dest.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(descriptor.to_json(), f, indent=2)
    tmp.replace(dest)
    logger.info("Saved device override: %s", dest)
    return dest
