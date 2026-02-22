from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np

from src.automation.binds import normalize_bind


class SlotState(Enum):
    READY = "ready"
    ON_COOLDOWN = "on_cooldown"
    CASTING = "casting"
    CHANNELING = "channeling"
    LOCKED = "locked"
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
    cast_progress: Optional[float] = None
    cast_ends_at: Optional[float] = None
    last_cast_start_at: Optional[float] = None
    last_cast_success_at: Optional[float] = None
    glow_candidate: bool = False
    glow_fraction: float = 0.0
    glow_ready: bool = False
    yellow_glow_candidate: bool = False
    yellow_glow_fraction: float = 0.0
    yellow_glow_ready: bool = False
    red_glow_candidate: bool = False
    red_glow_fraction: float = 0.0
    red_glow_ready: bool = False
    brightness: float = 0.0
    timestamp: float = 0.0

    @property
    def is_ready(self) -> bool:
        return self.state == SlotState.READY

    @property
    def is_casting(self) -> bool:
        return self.state in (SlotState.CASTING, SlotState.CHANNELING)


@dataclass
class ActionBarState:
    """Complete state of all slots at a point in time."""

    slots: list[SlotSnapshot] = field(default_factory=list)
    timestamp: float = 0.0
    cast_active: bool = False
    cast_ends_at: Optional[float] = None

    def ready_slots(self) -> list[SlotSnapshot]:
        return [s for s in self.slots if s.is_ready]

    def cooldown_slots(self) -> list[SlotSnapshot]:
        return [s for s in self.slots if s.state == SlotState.ON_COOLDOWN]

    def casting_slots(self) -> list[SlotSnapshot]:
        return [s for s in self.slots if s.is_casting]


@dataclass
class BoundingBox:
    """Screen-relative bounding box for capture region."""

    top: int = 900
    left: int = 500
    width: int = 400
    height: int = 50

    def as_mss_region(
        self, monitor_offset_x: int = 0, monitor_offset_y: int = 0
    ) -> dict:
        """Convert to mss-compatible region dict."""
        return {
            "top": self.top + monitor_offset_y,
            "left": self.left + monitor_offset_x,
            "width": self.width,
            "height": self.height,
        }

    def to_dict(self) -> dict:
        """Serialize to dict for JSON config file."""
        return {
            "top": self.top,
            "left": self.left,
            "width": self.width,
            "height": self.height,
        }


@dataclass
class AppConfig:
    """Runtime application configuration."""

    monitor_index: int = 1
    bounding_box: BoundingBox = field(default_factory=BoundingBox)
    slot_count: int = 10
    slot_gap_pixels: int = 2
    slot_padding: int = 3
    polling_fps: int = 20
    brightness_threshold: float = (
        0.65  # Deprecated; kept for compatibility / future use
    )
    brightness_drop_threshold: int = (
        40  # 0-255; pixel counts as darkened if brightness dropped by more
    )
    cooldown_pixel_fraction: float = (
        0.30  # ON_COOLDOWN if this fraction of pixels darkened
    )
    cooldown_min_duration_ms: int = 2000
    # Extra detector: absolute baseline change fraction (captures bright overlays).
    cooldown_change_pixel_fraction: float = 0.30
    # Cooldown-release hysteresis controls to avoid early-ready flips.
    cooldown_release_factor: float = 0.70
    cooldown_release_confirm_ms: int = 260
    cooldown_release_quadrant_fraction: float = 0.22
    # Optional slot indexes where cooldown-change detector is ignored (dark detector still applies).
    cooldown_change_ignore_by_slot: list[int] = field(default_factory=list)
    # Which sub-region of each slot to use for cooldown fraction: "full" or "top_left" (quadrant last cleared by WoW wipe).
    detection_region: str = "top_left"
    # Per-slot override (slot_index -> "full"|"top_left"). Empty for now; allows future per-slot region.
    detection_region_overrides: dict[int, str] = field(default_factory=dict)
    # Per-form per-slot detection-region override:
    # {form_id: {slot_index: "full"|"top_left"}}.
    detection_region_overrides_by_form: dict[str, dict[int, str]] = field(
        default_factory=dict
    )
    # Optional cooldown group sharing across slots: {slot_index: "group_id"}.
    cooldown_group_by_slot: dict[int, str] = field(default_factory=dict)
    queue_window_ms: int = 120
    allow_cast_while_casting: bool = False
    lock_ready_while_cast_bar_active: bool = False
    cast_bar_region: dict = field(default_factory=dict)
    cast_bar_activity_threshold: float = 12.0
    cast_bar_history_frames: int = 8
    glow_enabled: bool = True
    glow_mode: str = "color"
    glow_ring_thickness_px: int = 4
    glow_value_delta: int = 35
    # Optional per-slot override for glow_value_delta: {slot_index: delta}.
    glow_value_delta_by_slot: dict[int, int] = field(default_factory=dict)
    glow_saturation_min: int = 80
    glow_ring_fraction: float = 0.18
    # Optional per-slot override for yellow glow ring-fraction threshold: {slot_index: fraction}.
    glow_ring_fraction_by_slot: dict[int, float] = field(default_factory=dict)
    glow_red_ring_fraction: float = 0.18
    # Optional per-slot cooldown override trigger for non-red glow (yellow/white proc icons).
    glow_override_cooldown_by_slot: list[int] = field(default_factory=list)
    glow_confirm_frames: int = 2
    glow_yellow_hue_min: int = 18
    glow_yellow_hue_max: int = 42
    glow_red_hue_max_low: int = 12
    glow_red_hue_min_high: int = 168
    # Hybrid glow mode (movement-aware) tunables.
    glow_motion_score_enter: float = 0.62
    glow_motion_score_exit: float = 0.42
    glow_motion_smoothing: float = 0.45
    glow_motion_weight_color: float = 0.35
    glow_motion_weight_ring: float = 0.55
    glow_motion_weight_rotation: float = 0.45
    glow_motion_center_penalty_weight: float = 0.35
    glow_motion_cooldown_penalty_weight: float = 0.25
    glow_motion_ring_delta_threshold: float = 14.0
    glow_motion_rotation_bins: int = 24
    glow_motion_min_hold_ms: int = 140
    glow_motion_min_off_ms: int = 80
    ocr_enabled: bool = True
    overlay_enabled: bool = True
    overlay_border_color: str = "#00FF00"
    show_active_screen_outline: bool = False
    always_on_top: bool = False
    keybinds: list[str] = field(
        default_factory=list
    )  # keybinds[slot_index] = key string, e.g. "5", "F"
    # User-defined display names per slot (e.g. "Fireball"); empty/missing = "Unidentified"
    slot_display_names: list[str] = field(default_factory=list)
    # Persisted baselines: list of {"shape": [h, w], "data": base64} per slot (decoded at runtime in analyzer)
    slot_baselines: list = field(default_factory=list)
    # Persisted per-form baselines: {form_id: [{shape:[h,w], data:base64}, ...]}
    slot_baselines_by_form: dict = field(default_factory=dict)
    # Supported visual forms (normal + transform/stance variants).
    forms: list[dict] = field(
        default_factory=lambda: [{"id": "normal", "name": "Normal"}]
    )
    # Current form id for UI/defaults; analyzer owns live active form detection.
    active_form_id: str = "normal"
    # Optional active-form detector configuration.
    form_detector: dict = field(default_factory=dict)
    # Slot indices that had their baseline set by "Calibrate This Slot" (show bold in UI)
    overwritten_baseline_slots: list[int] = field(default_factory=list)
    # Buff ROI templates used for buff-present / buff-missing readiness rules.
    buff_rois: list[dict] = field(default_factory=list)
    # Priority order for automation: kept for backward compatibility with older config files.
    priority_order: list[int] = field(default_factory=list)
    automation_enabled: bool = False
    # Legacy single hotkey fields kept for migration compatibility.
    automation_toggle_bind: str = ""
    automation_hotkey_mode: str = "toggle"
    # Multiple automation profiles, each with its own priority list + hotkeys.
    priority_profiles: list[dict] = field(default_factory=list)
    active_priority_profile_id: str = "default"
    # Minimum ms between keypresses when automation is sending keys
    min_press_interval_ms: int = 150
    # GCD duration used for queue suppression timing after queued sends
    gcd_ms: int = 1500
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

    @staticmethod
    def _normalize_manual_actions(raw_actions: object) -> list[dict]:
        """Normalize profile manual actions to [{id, name, keybind}] with unique ids."""
        normalized: list[dict] = []
        seen_ids: set[str] = set()
        for raw in list(raw_actions or []):
            if not isinstance(raw, dict):
                continue
            aid = str(raw.get("id", "") or "").strip().lower()
            if not aid:
                aid = f"manual_{len(normalized) + 1}"
            if aid in seen_ids:
                continue
            seen_ids.add(aid)
            name = (
                str(raw.get("name", "") or "").strip() or aid.replace("_", " ").title()
            )
            keybind = normalize_bind(str(raw.get("keybind", "") or ""))
            normalized.append({"id": aid, "name": name, "keybind": keybind})
        return normalized

    @staticmethod
    def _normalize_slot_keybinds(raw_keybinds: object) -> list[str]:
        normalized: list[str] = []
        for raw in list(raw_keybinds or []):
            normalized.append(normalize_bind(str(raw or "")))
        return normalized

    @staticmethod
    def _normalize_activation_rule(raw_rule: object) -> str:
        rule = str(raw_rule or "").strip().lower()
        if rule in ("always", "dot_refresh", "require_glow"):
            return rule
        return "always"

    @staticmethod
    def _normalize_ready_source(raw_source: object, item_type: str) -> str:
        source = str(raw_source or "").strip().lower()
        if source in ("slot", "always", "buff_present", "buff_missing"):
            return source
        return "always" if item_type == "manual" else "slot"

    @staticmethod
    def _normalize_form_id(raw_form_id: object) -> str:
        form_id = str(raw_form_id or "").strip().lower()
        return form_id or "normal"

    @staticmethod
    def _normalize_required_form(raw_required_form: object, form_ids: set[str]) -> str:
        required = str(raw_required_form or "").strip().lower()
        if not required:
            return ""
        return required if required in form_ids else ""

    @staticmethod
    def _normalize_forms(raw_forms: object) -> list[dict]:
        normalized: list[dict] = []
        seen: set[str] = set()
        for raw in list(raw_forms or []):
            if not isinstance(raw, dict):
                continue
            form_id = AppConfig._normalize_form_id(raw.get("id"))
            if form_id in seen:
                continue
            seen.add(form_id)
            name = str(raw.get("name", "") or "").strip() or form_id.title()
            normalized.append({"id": form_id, "name": name})
        if "normal" not in seen:
            normalized.insert(0, {"id": "normal", "name": "Normal"})
        return normalized

    @staticmethod
    def _normalize_form_detector(raw_detector: object, form_ids: set[str]) -> dict:
        if not isinstance(raw_detector, dict):
            return {}
        detector_type = str(raw_detector.get("type", "") or "").strip().lower()
        if detector_type != "buff_roi":
            return {}
        roi_id = str(raw_detector.get("roi_id", "") or "").strip().lower()
        present_form = AppConfig._normalize_form_id(raw_detector.get("present_form"))
        absent_form = AppConfig._normalize_form_id(raw_detector.get("absent_form"))
        if present_form not in form_ids:
            present_form = "normal"
        if absent_form not in form_ids:
            absent_form = "normal"
        return {
            "type": "buff_roi",
            "roi_id": roi_id,
            "present_form": present_form,
            "absent_form": absent_form,
            "confirm_frames": max(1, int(raw_detector.get("confirm_frames", 2) or 2)),
            "settle_ms": max(0, int(raw_detector.get("settle_ms", 200) or 200)),
        }

    @staticmethod
    def _normalize_slot_baselines_by_form(
        raw_baselines: object, form_ids: set[str]
    ) -> dict:
        if not isinstance(raw_baselines, dict):
            return {}
        normalized: dict[str, list] = {}
        for form_id, baselines in raw_baselines.items():
            fid = AppConfig._normalize_form_id(form_id)
            if fid not in form_ids:
                continue
            if isinstance(baselines, list):
                normalized[fid] = list(baselines)
        return normalized

    @staticmethod
    def _normalize_buff_template(raw_template: object) -> Optional[dict]:
        if not isinstance(raw_template, dict):
            return None
        shape = raw_template.get("shape")
        data = raw_template.get("data")
        if (
            not isinstance(shape, list)
            or len(shape) != 2
            or not all(isinstance(v, int) and v > 0 for v in shape)
            or not isinstance(data, str)
            or not data.strip()
        ):
            return None
        return {"shape": [int(shape[0]), int(shape[1])], "data": str(data)}

    @staticmethod
    def _normalize_buff_rois(raw_rois: object) -> list[dict]:
        normalized: list[dict] = []
        seen_ids: set[str] = set()
        for idx, raw in enumerate(list(raw_rois or []), start=1):
            if not isinstance(raw, dict):
                continue
            rid = str(raw.get("id", "") or "").strip().lower()
            if not rid:
                rid = f"buff_{idx}"
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            calibration = raw.get("calibration", {})
            if not isinstance(calibration, dict):
                calibration = {}
            present_template = AppConfig._normalize_buff_template(
                calibration.get("present_template")
            )
            missing_template = AppConfig._normalize_buff_template(
                calibration.get("missing_template")
            )
            normalized.append(
                {
                    "id": rid,
                    "name": str(raw.get("name", "") or "").strip()
                    or rid.replace("_", " ").title(),
                    "enabled": bool(raw.get("enabled", True)),
                    "left": int(raw.get("left", 0)),
                    "top": int(raw.get("top", 0)),
                    "width": max(0, int(raw.get("width", 0))),
                    "height": max(0, int(raw.get("height", 0))),
                    "match_threshold": max(
                        0.0, min(1.0, float(raw.get("match_threshold", 0.88)))
                    ),
                    "confirm_frames": max(1, int(raw.get("confirm_frames", 2))),
                    "motion_gate_threshold": max(0.0, float(raw.get("motion_gate_threshold", 0) or 0)),
                    "calibration": {
                        "present_template": present_template,
                        "missing_template": missing_template,
                    },
                }
            )
        return normalized

    @staticmethod
    def _normalize_priority_items(
        raw_items: object,
        fallback_order: object,
        form_ids: set[str],
    ) -> list[dict]:
        """
        Normalize profile priority items to:
        [{type:'slot', slot_index:int, activation_rule:str} | {type:'manual', action_id:str}]
        """
        normalized: list[dict] = []
        for raw in list(raw_items or []):
            if isinstance(raw, int):
                normalized.append(
                    {
                        "type": "slot",
                        "slot_index": raw,
                        "activation_rule": "always",
                        "ready_source": "slot",
                        "buff_roi_id": "",
                        "required_form": "",
                        "cast_does_not_block": True,
                    }
                )
                continue
            if not isinstance(raw, dict):
                continue
            itype = str(raw.get("type", "") or "").strip().lower()
            if itype == "slot":
                slot_index = raw.get("slot_index")
                if isinstance(slot_index, int):
                    normalized.append(
                        {
                            "type": "slot",
                            "slot_index": slot_index,
                            "activation_rule": AppConfig._normalize_activation_rule(
                                raw.get("activation_rule")
                            ),
                            "ready_source": AppConfig._normalize_ready_source(
                                raw.get("ready_source"), "slot"
                            ),
                            "buff_roi_id": str(raw.get("buff_roi_id", "") or "")
                            .strip()
                            .lower(),
                            "required_form": AppConfig._normalize_required_form(
                                raw.get("required_form"),
                                form_ids,
                            ),
                            "cast_does_not_block": bool(raw.get("cast_does_not_block", True)),
                        }
                    )
            elif itype == "manual":
                action_id = str(raw.get("action_id", "") or "").strip().lower()
                if action_id:
                    normalized.append(
                        {
                            "type": "manual",
                            "action_id": action_id,
                            "ready_source": AppConfig._normalize_ready_source(
                                raw.get("ready_source"), "manual"
                            ),
                            "buff_roi_id": str(raw.get("buff_roi_id", "") or "")
                            .strip()
                            .lower(),
                            "required_form": AppConfig._normalize_required_form(
                                raw.get("required_form"),
                                form_ids,
                            ),
                            "cast_does_not_block": bool(raw.get("cast_does_not_block", True)),
                        }
                    )
        if normalized:
            return normalized
        return [
            {
                "type": "slot",
                "slot_index": i,
                "activation_rule": "always",
                "ready_source": "slot",
                "buff_roi_id": "",
                "required_form": "",
                "cast_does_not_block": True,
            }
            for i in list(fallback_order or [])
            if isinstance(i, int)
        ]

    def _normalize_profiles(self) -> None:
        """Ensure automation profiles are valid and there is always an active profile."""
        self.keybinds = self._normalize_slot_keybinds(self.keybinds)
        self.buff_rois = self._normalize_buff_rois(self.buff_rois)
        self.forms = self._normalize_forms(self.forms)
        form_ids = {str(f.get("id", "") or "").strip().lower() for f in self.forms}
        self.active_form_id = self._normalize_form_id(self.active_form_id)
        if self.active_form_id not in form_ids:
            self.active_form_id = "normal"
        self.form_detector = self._normalize_form_detector(self.form_detector, form_ids)
        self.slot_baselines_by_form = self._normalize_slot_baselines_by_form(
            self.slot_baselines_by_form,
            form_ids,
        )
        if not self.slot_baselines_by_form and isinstance(self.slot_baselines, list):
            self.slot_baselines_by_form = {"normal": list(self.slot_baselines)}
        # Keep legacy mirror field aligned for compatibility with older code paths.
        self.slot_baselines = list(
            self.slot_baselines_by_form.get("normal", self.slot_baselines)
        )
        normalized: list[dict] = []
        seen_ids: set[str] = set()
        for p in list(self.priority_profiles or []):
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id", "") or "").strip().lower()
            if not pid:
                pid = f"profile_{len(normalized) + 1}"
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            name = str(p.get("name", "") or "").strip() or pid.replace("_", " ").title()
            order = p.get("priority_order", [])
            if not isinstance(order, list):
                order = []
            manual_actions = self._normalize_manual_actions(p.get("manual_actions", []))
            manual_action_ids = {str(a.get("id", "") or "") for a in manual_actions}
            priority_items = [
                item
                for item in self._normalize_priority_items(
                    p.get("priority_items", []), order, form_ids
                )
                if (
                    item.get("type") == "slot"
                    or str(item.get("action_id", "") or "") in manual_action_ids
                )
            ]
            slot_order = [
                int(item["slot_index"])
                for item in priority_items
                if item.get("type") == "slot"
                and isinstance(item.get("slot_index"), int)
            ]
            toggle_bind = normalize_bind(str(p.get("toggle_bind", "") or ""))
            single_fire_bind = normalize_bind(str(p.get("single_fire_bind", "") or ""))
            normalized.append(
                {
                    "id": pid,
                    "name": name,
                    "priority_order": slot_order,
                    "priority_items": priority_items,
                    "manual_actions": manual_actions,
                    "toggle_bind": toggle_bind,
                    "single_fire_bind": single_fire_bind,
                }
            )

        if not normalized:
            normalized = [
                {
                    "id": "default",
                    "name": "Default",
                    "priority_order": [
                        int(i) for i in self.priority_order if isinstance(i, int)
                    ],
                    "priority_items": [
                        {
                            "type": "slot",
                            "slot_index": int(i),
                            "activation_rule": "always",
                            "ready_source": "slot",
                            "buff_roi_id": "",
                            "required_form": "",
                        }
                        for i in self.priority_order
                        if isinstance(i, int)
                    ],
                    "manual_actions": [],
                    "toggle_bind": normalize_bind(
                        str(self.automation_toggle_bind or "")
                    ),
                    "single_fire_bind": (
                        normalize_bind(str(self.automation_toggle_bind or ""))
                        if (self.automation_hotkey_mode or "").strip().lower()
                        == "single_fire"
                        else ""
                    ),
                }
            ]

        self.priority_profiles = normalized
        active = (self.active_priority_profile_id or "").strip().lower()
        if not active or not any(p["id"] == active for p in normalized):
            self.active_priority_profile_id = normalized[0]["id"]
        else:
            self.active_priority_profile_id = active
        # Keep legacy mirror fields aligned with the active profile for compatibility.
        active_profile = next(
            (p for p in normalized if p["id"] == self.active_priority_profile_id),
            normalized[0],
        )
        self.priority_order = list(active_profile.get("priority_order", []))
        self.automation_toggle_bind = str(active_profile.get("toggle_bind", "") or "")
        self.automation_hotkey_mode = "toggle"

    def get_active_priority_profile(self) -> dict:
        self._normalize_profiles()
        for p in self.priority_profiles:
            if p["id"] == self.active_priority_profile_id:
                return p
        return self.priority_profiles[0]

    def ensure_priority_profiles(self) -> None:
        self._normalize_profiles()

    def set_active_priority_profile(self, profile_id: str) -> bool:
        self._normalize_profiles()
        pid = (profile_id or "").strip().lower()
        if not pid or not any(p["id"] == pid for p in self.priority_profiles):
            return False
        if self.active_priority_profile_id == pid:
            return False
        self.active_priority_profile_id = pid
        active = self.get_active_priority_profile()
        self.priority_order = list(active.get("priority_order", []))
        self.automation_toggle_bind = str(active.get("toggle_bind", "") or "")
        return True

    def active_priority_order(self) -> list[int]:
        return list(self.get_active_priority_profile().get("priority_order", []))

    def active_priority_items(self) -> list[dict]:
        return list(self.get_active_priority_profile().get("priority_items", []))

    def active_manual_actions(self) -> list[dict]:
        return list(self.get_active_priority_profile().get("manual_actions", []))

    @classmethod
    def from_dict(cls, data: dict) -> AppConfig:
        bb = data.get("bounding_box", {})
        raw_glow_delta_by_slot = data.get("detection", {}).get(
            "glow_value_delta_by_slot", {}
        )
        if not isinstance(raw_glow_delta_by_slot, dict):
            raw_glow_delta_by_slot = {}
        raw_glow_ring_frac_by_slot = data.get("detection", {}).get(
            "glow_ring_fraction_by_slot", {}
        )
        if not isinstance(raw_glow_ring_frac_by_slot, dict):
            raw_glow_ring_frac_by_slot = {}
        raw_glow_override_slots = data.get("detection", {}).get(
            "glow_override_cooldown_by_slot", []
        )
        if not isinstance(raw_glow_override_slots, list):
            raw_glow_override_slots = []
        raw_cooldown_change_ignore_slots = data.get("detection", {}).get(
            "cooldown_change_ignore_by_slot", []
        )
        if not isinstance(raw_cooldown_change_ignore_slots, list):
            raw_cooldown_change_ignore_slots = []
        raw_cooldown_group_by_slot = data.get("detection", {}).get(
            "cooldown_group_by_slot", {}
        )
        if not isinstance(raw_cooldown_group_by_slot, dict):
            raw_cooldown_group_by_slot = {}
        parsed_glow_delta_by_slot: dict[int, int] = {}
        for k, v in raw_glow_delta_by_slot.items():
            try:
                slot_idx = int(k)
                delta = int(v)
            except Exception:
                continue
            if slot_idx < 0:
                continue
            parsed_glow_delta_by_slot[slot_idx] = max(0, min(255, delta))
        parsed_glow_ring_frac_by_slot: dict[int, float] = {}
        for k, v in raw_glow_ring_frac_by_slot.items():
            try:
                slot_idx = int(k)
                frac = float(v)
            except Exception:
                continue
            if slot_idx < 0:
                continue
            parsed_glow_ring_frac_by_slot[slot_idx] = max(0.0, min(1.0, frac))
        parsed_glow_override_slots: list[int] = []
        seen_override_slots: set[int] = set()
        for v in raw_glow_override_slots:
            try:
                slot_idx = int(v)
            except Exception:
                continue
            if slot_idx < 0 or slot_idx in seen_override_slots:
                continue
            seen_override_slots.add(slot_idx)
            parsed_glow_override_slots.append(slot_idx)
        parsed_cooldown_change_ignore_slots: list[int] = []
        seen_change_ignore_slots: set[int] = set()
        for v in raw_cooldown_change_ignore_slots:
            try:
                slot_idx = int(v)
            except Exception:
                continue
            if slot_idx < 0 or slot_idx in seen_change_ignore_slots:
                continue
            seen_change_ignore_slots.add(slot_idx)
            parsed_cooldown_change_ignore_slots.append(slot_idx)
        raw_detection_region = (
            (data.get("detection", {}).get("detection_region") or "top_left")
            .strip()
            .lower()
        )
        if raw_detection_region not in ("full", "top_left"):
            raw_detection_region = "top_left"
        raw_region_overrides = (
            data.get("detection", {}).get("detection_region_overrides") or {}
        )
        parsed_region_overrides: dict[int, str] = {}
        if isinstance(raw_region_overrides, dict):
            for k, v in raw_region_overrides.items():
                try:
                    slot_idx = int(k)
                    mode = (str(v) or "full").strip().lower()
                    if mode in ("full", "top_left"):
                        parsed_region_overrides[slot_idx] = mode
                except (ValueError, TypeError):
                    continue
        raw_region_overrides_by_form = (
            data.get("detection", {}).get("detection_region_overrides_by_form") or {}
        )
        parsed_region_overrides_by_form: dict[str, dict[int, str]] = {}
        if isinstance(raw_region_overrides_by_form, dict):
            for raw_form_id, raw_form_overrides in raw_region_overrides_by_form.items():
                form_id = str(raw_form_id or "").strip().lower()
                if not form_id or not isinstance(raw_form_overrides, dict):
                    continue
                parsed_form_overrides: dict[int, str] = {}
                for k, v in raw_form_overrides.items():
                    try:
                        slot_idx = int(k)
                        mode = (str(v) or "full").strip().lower()
                        if mode in ("full", "top_left"):
                            parsed_form_overrides[slot_idx] = mode
                    except (ValueError, TypeError):
                        continue
                if parsed_form_overrides:
                    parsed_region_overrides_by_form[form_id] = parsed_form_overrides
        parsed_cooldown_group_by_slot: dict[int, str] = {}
        for k, v in raw_cooldown_group_by_slot.items():
            try:
                slot_idx = int(k)
            except Exception:
                continue
            group_id = str(v or "").strip().lower()
            if slot_idx < 0 or not group_id:
                continue
            parsed_cooldown_group_by_slot[slot_idx] = group_id
        hotkey_mode = (
            (data.get("automation_hotkey_mode", "toggle") or "toggle").strip().lower()
        )
        if hotkey_mode not in ("toggle", "single_fire"):
            hotkey_mode = "toggle"
        cfg = cls(
            monitor_index=data.get("monitor_index", 1),
            bounding_box=BoundingBox(**bb),
            slot_count=data.get("slots", {}).get("count", 10),
            slot_gap_pixels=data.get("slots", {}).get("gap_pixels", 2),
            slot_padding=data.get("slots", {}).get("padding", 3),
            polling_fps=data.get("detection", {}).get("polling_fps", 20),
            brightness_threshold=data.get("detection", {}).get(
                "brightness_threshold", 0.65
            ),
            brightness_drop_threshold=data.get("detection", {}).get(
                "brightness_drop_threshold",
                data.get("detection", {}).get("saturation_drop_threshold", 40),
            ),
            cooldown_pixel_fraction=data.get("detection", {}).get(
                "cooldown_pixel_fraction", 0.30
            ),
            cooldown_min_duration_ms=data.get("detection", {}).get(
                "cooldown_min_duration_ms", 2000
            ),
            cooldown_change_pixel_fraction=data.get("detection", {}).get(
                "cooldown_change_pixel_fraction",
                data.get("detection", {}).get("cooldown_pixel_fraction", 0.30),
            ),
            cooldown_release_factor=float(
                data.get("detection", {}).get("cooldown_release_factor", 0.70)
            ),
            cooldown_release_confirm_ms=int(
                data.get("detection", {}).get("cooldown_release_confirm_ms", 260)
            ),
            cooldown_release_quadrant_fraction=float(
                data.get("detection", {}).get(
                    "cooldown_release_quadrant_fraction",
                    0.22,
                )
            ),
            cooldown_change_ignore_by_slot=parsed_cooldown_change_ignore_slots,
            detection_region=raw_detection_region,
            detection_region_overrides=parsed_region_overrides,
            detection_region_overrides_by_form=parsed_region_overrides_by_form,
            cooldown_group_by_slot=parsed_cooldown_group_by_slot,
            queue_window_ms=data.get("detection", {}).get("queue_window_ms", 120),
            allow_cast_while_casting=data.get("detection", {}).get(
                "allow_cast_while_casting", False
            ),
            lock_ready_while_cast_bar_active=data.get("detection", {}).get(
                "lock_ready_while_cast_bar_active",
                False,
            ),
            cast_bar_region=data.get("detection", {}).get("cast_bar_region", {}),
            cast_bar_activity_threshold=data.get("detection", {}).get(
                "cast_bar_activity_threshold",
                12.0,
            ),
            cast_bar_history_frames=data.get("detection", {}).get(
                "cast_bar_history_frames", 8
            ),
            glow_enabled=data.get("detection", {}).get("glow_enabled", True),
            glow_mode=str(
                data.get("detection", {}).get("glow_mode", "color") or "color"
            )
            .strip()
            .lower(),
            glow_ring_thickness_px=int(
                data.get("detection", {}).get("glow_ring_thickness_px", 4)
            ),
            glow_value_delta=int(data.get("detection", {}).get("glow_value_delta", 35)),
            glow_value_delta_by_slot=parsed_glow_delta_by_slot,
            glow_saturation_min=int(
                data.get("detection", {}).get("glow_saturation_min", 80)
            ),
            glow_ring_fraction=float(
                data.get("detection", {}).get("glow_ring_fraction", 0.18)
            ),
            glow_ring_fraction_by_slot=parsed_glow_ring_frac_by_slot,
            glow_red_ring_fraction=float(
                data.get("detection", {}).get(
                    "glow_red_ring_fraction",
                    data.get("detection", {}).get("glow_ring_fraction", 0.18),
                )
            ),
            glow_override_cooldown_by_slot=parsed_glow_override_slots,
            glow_confirm_frames=int(
                data.get("detection", {}).get("glow_confirm_frames", 2)
            ),
            glow_yellow_hue_min=int(
                data.get("detection", {}).get("glow_yellow_hue_min", 18)
            ),
            glow_yellow_hue_max=int(
                data.get("detection", {}).get("glow_yellow_hue_max", 42)
            ),
            glow_red_hue_max_low=int(
                data.get("detection", {}).get("glow_red_hue_max_low", 12)
            ),
            glow_red_hue_min_high=int(
                data.get("detection", {}).get("glow_red_hue_min_high", 168)
            ),
            glow_motion_score_enter=float(
                data.get("detection", {}).get("glow_motion_score_enter", 0.62)
            ),
            glow_motion_score_exit=float(
                data.get("detection", {}).get("glow_motion_score_exit", 0.42)
            ),
            glow_motion_smoothing=float(
                data.get("detection", {}).get("glow_motion_smoothing", 0.45)
            ),
            glow_motion_weight_color=float(
                data.get("detection", {}).get("glow_motion_weight_color", 0.35)
            ),
            glow_motion_weight_ring=float(
                data.get("detection", {}).get("glow_motion_weight_ring", 0.55)
            ),
            glow_motion_weight_rotation=float(
                data.get("detection", {}).get("glow_motion_weight_rotation", 0.45)
            ),
            glow_motion_center_penalty_weight=float(
                data.get("detection", {}).get("glow_motion_center_penalty_weight", 0.35)
            ),
            glow_motion_cooldown_penalty_weight=float(
                data.get("detection", {}).get(
                    "glow_motion_cooldown_penalty_weight", 0.25
                )
            ),
            glow_motion_ring_delta_threshold=float(
                data.get("detection", {}).get("glow_motion_ring_delta_threshold", 14.0)
            ),
            glow_motion_rotation_bins=int(
                data.get("detection", {}).get("glow_motion_rotation_bins", 24)
            ),
            glow_motion_min_hold_ms=int(
                data.get("detection", {}).get("glow_motion_min_hold_ms", 140)
            ),
            glow_motion_min_off_ms=int(
                data.get("detection", {}).get("glow_motion_min_off_ms", 80)
            ),
            ocr_enabled=data.get("detection", {}).get("ocr_enabled", True),
            overlay_enabled=data.get("overlay", {}).get("enabled", True),
            overlay_border_color=data.get("overlay", {}).get("border_color", "#00FF00"),
            show_active_screen_outline=data.get("overlay", {}).get("show_active_screen_outline", False),
            always_on_top=data.get("display", {}).get("always_on_top", False),
            keybinds=cls._normalize_slot_keybinds(
                data.get("slots", {}).get("keybinds", [])
            ),
            slot_display_names=data.get("slot_display_names", []),
            slot_baselines=data.get("slot_baselines", []),
            slot_baselines_by_form=data.get("slot_baselines_by_form", {}),
            forms=data.get("forms", [{"id": "normal", "name": "Normal"}]),
            active_form_id=data.get("active_form_id", "normal"),
            form_detector=data.get("form_detector", {}),
            overwritten_baseline_slots=data.get("overwritten_baseline_slots", []),
            buff_rois=cls._normalize_buff_rois(data.get("buff_rois", [])),
            priority_order=data.get("priority_order", []),
            automation_enabled=data.get("automation_enabled", False),
            automation_toggle_bind=data.get("automation_toggle_bind", ""),
            automation_hotkey_mode=hotkey_mode,
            min_press_interval_ms=data.get("min_press_interval_ms", 150),
            gcd_ms=int(data.get("gcd_ms", 1500)),
            target_window_title=data.get("target_window_title", ""),
            profile_name=data.get("profile_name", ""),
            history_rows=data.get("history_rows", 3),
            queue_whitelist=[
                str(k).strip().lower()
                for k in data.get("queue_whitelist", [])
                if str(k).strip()
            ],
            queue_timeout_ms=int(data.get("queue_timeout_ms", 5000)),
            queue_fire_delay_ms=int(data.get("queue_fire_delay_ms", 100)),
        )
        raw_profiles = data.get("priority_profiles")
        if isinstance(raw_profiles, list):
            cfg.priority_profiles = list(raw_profiles)
            cfg.active_priority_profile_id = str(
                data.get("active_priority_profile_id", "default") or "default"
            )
        else:
            # Legacy migration path from single priority list + single hotkey.
            legacy_toggle_bind = normalize_bind(
                str(data.get("automation_toggle_bind", "") or "")
            )
            legacy_mode = (
                (data.get("automation_hotkey_mode", "toggle") or "toggle")
                .strip()
                .lower()
            )
            cfg.priority_profiles = [
                {
                    "id": "default",
                    "name": "Default",
                    "priority_order": list(data.get("priority_order", [])),
                    "priority_items": [
                        {
                            "type": "slot",
                            "slot_index": int(i),
                            "activation_rule": "always",
                            "ready_source": "slot",
                            "buff_roi_id": "",
                            "required_form": "",
                        }
                        for i in list(data.get("priority_order", []))
                        if isinstance(i, int)
                    ],
                    "manual_actions": [],
                    "toggle_bind": (
                        legacy_toggle_bind if legacy_mode == "toggle" else ""
                    ),
                    "single_fire_bind": (
                        legacy_toggle_bind if legacy_mode == "single_fire" else ""
                    ),
                }
            ]
            cfg.active_priority_profile_id = "default"
        cfg._normalize_profiles()
        if cfg.glow_mode not in ("color", "hybrid_motion"):
            cfg.glow_mode = "color"
        cfg.cooldown_release_factor = max(
            0.25, min(1.0, float(cfg.cooldown_release_factor))
        )
        cfg.cooldown_release_confirm_ms = max(0, int(cfg.cooldown_release_confirm_ms))
        cfg.cooldown_release_quadrant_fraction = max(
            0.0, min(1.0, float(cfg.cooldown_release_quadrant_fraction))
        )
        cfg.glow_motion_score_enter = max(0.1, float(cfg.glow_motion_score_enter))
        cfg.glow_motion_score_exit = max(0.05, float(cfg.glow_motion_score_exit))
        if cfg.glow_motion_score_exit >= cfg.glow_motion_score_enter:
            cfg.glow_motion_score_exit = max(0.05, cfg.glow_motion_score_enter * 0.75)
        cfg.glow_motion_smoothing = max(0.0, min(1.0, float(cfg.glow_motion_smoothing)))
        cfg.glow_motion_weight_color = max(0.0, float(cfg.glow_motion_weight_color))
        cfg.glow_motion_weight_ring = max(0.0, float(cfg.glow_motion_weight_ring))
        cfg.glow_motion_weight_rotation = max(
            0.0, float(cfg.glow_motion_weight_rotation)
        )
        cfg.glow_motion_center_penalty_weight = max(
            0.0, float(cfg.glow_motion_center_penalty_weight)
        )
        cfg.glow_motion_cooldown_penalty_weight = max(
            0.0, float(cfg.glow_motion_cooldown_penalty_weight)
        )
        cfg.glow_motion_ring_delta_threshold = max(
            1.0, float(cfg.glow_motion_ring_delta_threshold)
        )
        cfg.glow_motion_rotation_bins = max(8, int(cfg.glow_motion_rotation_bins))
        cfg.glow_motion_min_hold_ms = max(0, int(cfg.glow_motion_min_hold_ms))
        cfg.glow_motion_min_off_ms = max(0, int(cfg.glow_motion_min_off_ms))
        return cfg

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
                "cooldown_change_pixel_fraction": self.cooldown_change_pixel_fraction,
                "cooldown_release_factor": self.cooldown_release_factor,
                "cooldown_release_confirm_ms": self.cooldown_release_confirm_ms,
                "cooldown_release_quadrant_fraction": self.cooldown_release_quadrant_fraction,
                "cooldown_change_ignore_by_slot": [
                    int(v) for v in list(self.cooldown_change_ignore_by_slot or [])
                ],
                "detection_region": self.detection_region,
                "detection_region_overrides": {
                    str(int(k)): str(v)
                    for k, v in dict(self.detection_region_overrides or {}).items()
                },
                "detection_region_overrides_by_form": {
                    str(form_id): {
                        str(int(slot_idx)): str(mode)
                        for slot_idx, mode in dict(form_overrides or {}).items()
                        if str(mode) in ("full", "top_left")
                    }
                    for form_id, form_overrides in dict(
                        self.detection_region_overrides_by_form or {}
                    ).items()
                    if str(form_id or "").strip()
                },
                "cooldown_group_by_slot": {
                    str(int(k)): str(v)
                    for k, v in dict(self.cooldown_group_by_slot or {}).items()
                    if str(v or "").strip()
                },
                "queue_window_ms": self.queue_window_ms,
                "allow_cast_while_casting": self.allow_cast_while_casting,
                "lock_ready_while_cast_bar_active": self.lock_ready_while_cast_bar_active,
                "cast_bar_region": self.cast_bar_region,
                "cast_bar_activity_threshold": self.cast_bar_activity_threshold,
                "cast_bar_history_frames": self.cast_bar_history_frames,
                "glow_enabled": self.glow_enabled,
                "glow_mode": self.glow_mode,
                "glow_ring_thickness_px": self.glow_ring_thickness_px,
                "glow_value_delta": self.glow_value_delta,
                "glow_value_delta_by_slot": {
                    str(int(k)): int(v)
                    for k, v in dict(self.glow_value_delta_by_slot or {}).items()
                },
                "glow_saturation_min": self.glow_saturation_min,
                "glow_ring_fraction": self.glow_ring_fraction,
                "glow_ring_fraction_by_slot": {
                    str(int(k)): float(v)
                    for k, v in dict(self.glow_ring_fraction_by_slot or {}).items()
                },
                "glow_red_ring_fraction": self.glow_red_ring_fraction,
                "glow_override_cooldown_by_slot": [
                    int(v) for v in list(self.glow_override_cooldown_by_slot or [])
                ],
                "glow_confirm_frames": self.glow_confirm_frames,
                "glow_yellow_hue_min": self.glow_yellow_hue_min,
                "glow_yellow_hue_max": self.glow_yellow_hue_max,
                "glow_red_hue_max_low": self.glow_red_hue_max_low,
                "glow_red_hue_min_high": self.glow_red_hue_min_high,
                "glow_motion_score_enter": self.glow_motion_score_enter,
                "glow_motion_score_exit": self.glow_motion_score_exit,
                "glow_motion_smoothing": self.glow_motion_smoothing,
                "glow_motion_weight_color": self.glow_motion_weight_color,
                "glow_motion_weight_ring": self.glow_motion_weight_ring,
                "glow_motion_weight_rotation": self.glow_motion_weight_rotation,
                "glow_motion_center_penalty_weight": self.glow_motion_center_penalty_weight,
                "glow_motion_cooldown_penalty_weight": self.glow_motion_cooldown_penalty_weight,
                "glow_motion_ring_delta_threshold": self.glow_motion_ring_delta_threshold,
                "glow_motion_rotation_bins": self.glow_motion_rotation_bins,
                "glow_motion_min_hold_ms": self.glow_motion_min_hold_ms,
                "glow_motion_min_off_ms": self.glow_motion_min_off_ms,
                "ocr_enabled": self.ocr_enabled,
            },
            "overlay": {
                "enabled": self.overlay_enabled,
                "border_color": self.overlay_border_color,
                "show_active_screen_outline": self.show_active_screen_outline,
            },
            "display": {"always_on_top": self.always_on_top},
            "slot_baselines": self.slot_baselines,
            "slot_baselines_by_form": self.slot_baselines_by_form,
            "forms": self.forms,
            "active_form_id": self.active_form_id,
            "form_detector": self.form_detector,
            "overwritten_baseline_slots": self.overwritten_baseline_slots,
            "buff_rois": self.buff_rois,
            "priority_order": self.priority_order,
            "automation_enabled": self.automation_enabled,
            "automation_toggle_bind": self.automation_toggle_bind,
            "automation_hotkey_mode": self.automation_hotkey_mode,
            "priority_profiles": self.priority_profiles,
            "active_priority_profile_id": self.active_priority_profile_id,
            "min_press_interval_ms": self.min_press_interval_ms,
            "gcd_ms": self.gcd_ms,
            "target_window_title": self.target_window_title,
            "profile_name": self.profile_name,
            "history_rows": self.history_rows,
            "queue_whitelist": self.queue_whitelist,
            "queue_timeout_ms": self.queue_timeout_ms,
            "queue_fire_delay_ms": self.queue_fire_delay_ms,
        }
