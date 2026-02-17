from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np


class SlotState(Enum):
    READY = "ready"
    ON_COOLDOWN = "on_cooldown"
    GCD = "gcd"
    UNKNOWN = "unknown"


@dataclass
class SlotConfig:
    """Static configuration for a single action bar slot."""
    index: int
    # Pixel region relative to the captured action bar image (not screen coords)
    x_offset: int = 0
    y_offset: int = 0
    width: int = 40
    height: int = 40


@dataclass
class SlotSnapshot:
    """The analyzed state of a single slot at a point in time."""
    index: int
    state: SlotState = SlotState.UNKNOWN
    keybind: Optional[str] = None
    cooldown_remaining: Optional[float] = None
    brightness: float = 0.0
    timestamp: float = 0.0

    @property
    def is_ready(self) -> bool:
        return self.state == SlotState.READY


@dataclass
class ActionBarState:
    """Complete state of all slots at a point in time."""
    slots: list[SlotSnapshot] = field(default_factory=list)
    timestamp: float = 0.0

    def ready_slots(self) -> list[SlotSnapshot]:
        return [s for s in self.slots if s.is_ready]

    def cooldown_slots(self) -> list[SlotSnapshot]:
        return [s for s in self.slots if s.state == SlotState.ON_COOLDOWN]


@dataclass
class BoundingBox:
    """Screen-relative bounding box for capture region."""
    top: int = 900
    left: int = 500
    width: int = 400
    height: int = 50

    def as_mss_region(self, monitor_offset_x: int = 0, monitor_offset_y: int = 0) -> dict:
        """Convert to mss-compatible region dict."""
        return {
            "top": self.top + monitor_offset_y,
            "left": self.left + monitor_offset_x,
            "width": self.width,
            "height": self.height,
        }

    def to_dict(self) -> dict:
        """Serialize to dict for JSON config file."""
        return {"top": self.top, "left": self.left, "width": self.width, "height": self.height}


@dataclass
class AppConfig:
    """Runtime application configuration."""
    monitor_index: int = 1
    bounding_box: BoundingBox = field(default_factory=BoundingBox)
    slot_count: int = 10
    slot_gap_pixels: int = 2
    slot_padding: int = 3
    polling_fps: int = 20
    brightness_threshold: float = 0.65  # Deprecated; kept for compatibility / future use
    brightness_drop_threshold: int = 40  # 0-255; pixel counts as darkened if brightness dropped by more
    cooldown_pixel_fraction: float = 0.30  # ON_COOLDOWN if this fraction of pixels darkened
    cooldown_min_duration_ms: int = 2000
    ocr_enabled: bool = True
    overlay_enabled: bool = True
    overlay_border_color: str = "#00FF00"
    always_on_top: bool = False
    keybinds: list[str] = field(default_factory=list)  # keybinds[slot_index] = key string, e.g. "5", "F"
    # User-defined display names per slot (e.g. "Fireball"); empty/missing = "Unidentified"
    slot_display_names: list[str] = field(default_factory=list)
    # Persisted baselines: list of {"shape": [h, w], "data": base64} per slot (decoded at runtime in analyzer)
    slot_baselines: list = field(default_factory=list)
    # Slot indices that had their baseline set by "Calibrate This Slot" (show bold in UI)
    overwritten_baseline_slots: list[int] = field(default_factory=list)
    # Priority order for automation: list of slot indices (first READY in this order is "next")
    priority_order: list[int] = field(default_factory=list)
    automation_enabled: bool = False
    # Global hotkey to toggle automation (e.g. "f5", "x1" for mouse side button); empty = not set
    automation_toggle_bind: str = ""
    # Global hotkey behavior: "toggle" keeps automation on/off, "single_fire" queues one next action
    automation_hotkey_mode: str = "toggle"
    # Minimum ms between keypresses when automation is sending keys
    min_press_interval_ms: int = 150
    # If non-empty, only send keys when foreground window title contains this (case-insensitive)
    target_window_title: str = ""
    # Profile name (e.g. "Default") to distinguish which profile is loaded; used for export default filename
    profile_name: str = ""
    # Number of visible entries in Last Action history (1-10)
    history_rows: int = 3
    # Spell queue: keys in this list (or bound keys not in priority) queue to fire at next GCD
    queue_whitelist: list[str] = field(default_factory=list)
    # Max ms to keep a queued action before clearing (prevents stale queue)
    queue_timeout_ms: int = 5000
    # Ms to wait after detecting GCD ready before sending queued key (avoids firing too early)
    queue_fire_delay_ms: int = 100

    @classmethod
    def from_dict(cls, data: dict) -> AppConfig:
        bb = data.get("bounding_box", {})
        hotkey_mode = (data.get("automation_hotkey_mode", "toggle") or "toggle").strip().lower()
        if hotkey_mode not in ("toggle", "single_fire"):
            hotkey_mode = "toggle"
        return cls(
            monitor_index=data.get("monitor_index", 1),
            bounding_box=BoundingBox(**bb),
            slot_count=data.get("slots", {}).get("count", 10),
            slot_gap_pixels=data.get("slots", {}).get("gap_pixels", 2),
            slot_padding=data.get("slots", {}).get("padding", 3),
            polling_fps=data.get("detection", {}).get("polling_fps", 20),
            brightness_threshold=data.get("detection", {}).get("brightness_threshold", 0.65),
            brightness_drop_threshold=data.get("detection", {}).get(
                "brightness_drop_threshold",
                data.get("detection", {}).get("saturation_drop_threshold", 40),
            ),
            cooldown_pixel_fraction=data.get("detection", {}).get("cooldown_pixel_fraction", 0.30),
            cooldown_min_duration_ms=data.get("detection", {}).get("cooldown_min_duration_ms", 2000),
            ocr_enabled=data.get("detection", {}).get("ocr_enabled", True),
            overlay_enabled=data.get("overlay", {}).get("enabled", True),
            overlay_border_color=data.get("overlay", {}).get("border_color", "#00FF00"),
            always_on_top=data.get("display", {}).get("always_on_top", False),
            keybinds=data.get("slots", {}).get("keybinds", []),
            slot_display_names=data.get("slot_display_names", []),
            slot_baselines=data.get("slot_baselines", []),
            overwritten_baseline_slots=data.get("overwritten_baseline_slots", []),
            priority_order=data.get("priority_order", []),
            automation_enabled=data.get("automation_enabled", False),
            automation_toggle_bind=data.get("automation_toggle_bind", ""),
            automation_hotkey_mode=hotkey_mode,
            min_press_interval_ms=data.get("min_press_interval_ms", 150),
            target_window_title=data.get("target_window_title", ""),
            profile_name=data.get("profile_name", ""),
            history_rows=data.get("history_rows", 3),
            queue_whitelist=[str(k).strip().lower() for k in data.get("queue_whitelist", []) if str(k).strip()],
            queue_timeout_ms=int(data.get("queue_timeout_ms", 5000)),
            queue_fire_delay_ms=int(data.get("queue_fire_delay_ms", 100)),
        )

    def to_dict(self) -> dict:
        """Serialize to dict for JSON config file (round-trip with from_dict)."""
        return {
            "monitor_index": self.monitor_index,
            "bounding_box": self.bounding_box.to_dict(),
            "slots": {
                "count": self.slot_count,
                "gap_pixels": self.slot_gap_pixels,
                "padding": self.slot_padding,
                "keybinds": self.keybinds,
            },
            "slot_display_names": self.slot_display_names,
            "detection": {
                "polling_fps": self.polling_fps,
                "brightness_threshold": self.brightness_threshold,
                "brightness_drop_threshold": self.brightness_drop_threshold,
                "cooldown_pixel_fraction": self.cooldown_pixel_fraction,
                "cooldown_min_duration_ms": self.cooldown_min_duration_ms,
                "ocr_enabled": self.ocr_enabled,
            },
            "overlay": {
                "enabled": self.overlay_enabled,
                "border_color": self.overlay_border_color,
            },
            "display": {"always_on_top": self.always_on_top},
            "slot_baselines": self.slot_baselines,
            "overwritten_baseline_slots": self.overwritten_baseline_slots,
            "priority_order": self.priority_order,
            "automation_enabled": self.automation_enabled,
            "automation_toggle_bind": self.automation_toggle_bind,
            "automation_hotkey_mode": self.automation_hotkey_mode,
            "min_press_interval_ms": self.min_press_interval_ms,
            "target_window_title": self.target_window_title,
            "profile_name": self.profile_name,
            "history_rows": self.history_rows,
            "queue_whitelist": self.queue_whitelist,
            "queue_timeout_ms": self.queue_timeout_ms,
            "queue_fire_delay_ms": self.queue_fire_delay_ms,
        }
