"""Slot analyzer — segments the action bar and detects cooldown states.

Uses brightness-based pixel-fraction comparison: store baseline grayscale per slot,
then count the fraction of pixels where brightness has dropped by more than
brightness_drop_threshold. If that fraction exceeds cooldown_pixel_fraction, mark
ON_COOLDOWN. Per-pixel comparison catches partial GCD sweeps. Phase 2: OCR.
"""

from __future__ import annotations

from collections import deque
import base64
from dataclasses import dataclass
import logging
import time
from typing import Optional

import cv2
import numpy as np

from src.models import (
    ActionBarState,
    AppConfig,
    SlotConfig,
    SlotSnapshot,
    SlotState,
)

logger = logging.getLogger(__name__)


@dataclass
class _SlotRuntime:
    """Per-slot temporal memory for cooldown/glow hysteresis."""

    state: SlotState = SlotState.UNKNOWN
    last_darkened_fraction: float = 0.0
    cooldown_candidate_started_at: Optional[float] = None
    glow_candidate_frames: int = 0
    yellow_glow_candidate_frames: int = 0
    red_glow_candidate_frames: int = 0
    prev_glow_gray: Optional[np.ndarray] = None
    prev_glow_ring_bins: Optional[np.ndarray] = None
    glow_motion_score: float = 0.0
    glow_motion_ready: bool = False
    glow_motion_last_change_at: float = 0.0


@dataclass
class _BuffRuntime:
    """Per-buff temporal memory for template match confirmation."""

    candidate_frames: int = 0
    red_glow_candidate_frames: int = 0


@dataclass
class _CooldownGroupRuntime:
    was_cooldown: bool = False
    cooldown_candidate_started_at: Optional[float] = None
    cooldown_release_candidate_started_at: Optional[float] = None


class SlotAnalyzer:
    """Analyzes a captured action bar image to determine per-slot cooldown state."""

    def __init__(self, config: AppConfig):
        self._config = config
        self._slot_configs: list[SlotConfig] = []
        self._baselines: dict[int, np.ndarray] = (
            {}
        )  # Legacy mirror: "normal" slot baselines.
        self._baselines_by_form: dict[str, dict[int, np.ndarray]] = {"normal": {}}
        self._ocr_engine: Optional[object] = None  # Lazy-loaded OCREngine
        self._runtime: dict[int, _SlotRuntime] = {}
        self._cooldown_group_runtime: dict[str, _CooldownGroupRuntime] = {}
        self._analyze_frame_count = 0
        self._cast_bar_motion: deque[float] = deque(maxlen=8)
        self._cast_bar_prev_gray: Optional[np.ndarray] = None
        self._cast_bar_active_until: float = 0.0
        self._cast_bar_last_motion: float = 0.0
        self._cast_bar_last_activity: float = 0.0
        self._cast_bar_last_threshold: float = float(
            getattr(config, "cast_bar_activity_threshold", 12.0) or 12.0
        )
        self._cast_bar_last_deactivate_threshold: float = (
            self._cast_bar_last_threshold * 0.6
        )
        self._cast_bar_last_active: bool = False
        self._cast_bar_last_status: str = "off"
        self._cast_bar_last_present: bool = False
        self._cast_bar_last_directional: bool = False
        self._cast_bar_last_front: float = 0.0
        self._cast_bar_active_state: bool = False
        self._cast_bar_ltr_front_prev: Optional[float] = None
        self._cast_bar_rtl_front_prev: Optional[float] = None
        self._cast_bar_last_direction: str = "?"
        self._cast_bar_quiet_frames: int = 0
        self._cast_gate_active: bool = False
        self._frame_action_origin_x: int = 0
        self._frame_action_origin_y: int = 0
        self._ring_mask_cache: dict[tuple[int, int, int], np.ndarray] = {}
        self._buff_runtime: dict[str, _BuffRuntime] = {}
        self._buff_states: dict[str, dict] = {}
        self._buff_template_cache: dict[str, np.ndarray] = {}
        self._detection_region: str = (
            (getattr(config, "detection_region", None) or "top_left").strip().lower()
        )
        if self._detection_region not in ("full", "top_left"):
            self._detection_region = "top_left"
        self._detection_region_overrides: dict[int, str] = dict(
            getattr(config, "detection_region_overrides", None) or {}
        )
        self._detection_region_overrides_by_form: dict[str, dict[int, str]] = {}
        for form_id, overrides in dict(
            getattr(config, "detection_region_overrides_by_form", None) or {}
        ).items():
            fid = str(form_id or "").strip().lower()
            if not fid or not isinstance(overrides, dict):
                continue
            parsed: dict[int, str] = {}
            for slot_idx, mode in overrides.items():
                try:
                    idx = int(slot_idx)
                except Exception:
                    continue
                normalized_mode = str(mode or "").strip().lower()
                if normalized_mode in ("full", "top_left"):
                    parsed[idx] = normalized_mode
            if parsed:
                self._detection_region_overrides_by_form[fid] = parsed
        self._active_form_id: str = "normal"
        self._pending_form_id: str = "normal"
        self._pending_form_frames: int = 0
        self._form_last_changed_at: float = 0.0
        self._form_settle_until: float = 0.0
        self._recompute_slot_layout()

    def _recompute_slot_layout(self) -> None:
        """Calculate pixel regions for each slot based on config.

        Divides the bounding box width evenly among slot_count slots,
        accounting for gap_pixels between them.
        """
        total_width = self._config.bounding_box.width
        total_height = self._config.bounding_box.height
        gap = self._config.slot_gap_pixels
        count = self._config.slot_count

        # Each slot width = (total_width - (count-1)*gap) / count
        slot_w = max(1, (total_width - (count - 1) * gap) // count)
        slot_h = total_height

        self._slot_configs = []
        for i in range(count):
            x = i * (slot_w + gap)
            self._slot_configs.append(
                SlotConfig(index=i, x_offset=x, y_offset=0, width=slot_w, height=slot_h)
            )
            self._runtime.setdefault(i, _SlotRuntime())
        self._runtime = {i: self._runtime.get(i, _SlotRuntime()) for i in range(count)}
        logger.debug(
            f"Slot layout: {count} slots, each {slot_w}x{slot_h}px, gap={gap}px"
        )

    def update_config(self, config: AppConfig) -> None:
        """Update config and recompute layout. Clears baselines if layout changed."""
        layout_changed = (
            config.slot_count != self._config.slot_count
            or config.slot_gap_pixels != self._config.slot_gap_pixels
            or config.slot_padding != self._config.slot_padding
        )
        self._config = config
        self._detection_region = (
            (getattr(config, "detection_region", None) or "top_left").strip().lower()
        )
        if self._detection_region not in ("full", "top_left"):
            self._detection_region = "top_left"
        self._detection_region_overrides = dict(
            getattr(config, "detection_region_overrides", None) or {}
        )
        self._detection_region_overrides_by_form = {}
        for form_id, overrides in dict(
            getattr(config, "detection_region_overrides_by_form", None) or {}
        ).items():
            fid = str(form_id or "").strip().lower()
            if not fid or not isinstance(overrides, dict):
                continue
            parsed: dict[int, str] = {}
            for slot_idx, mode in overrides.items():
                try:
                    idx = int(slot_idx)
                except Exception:
                    continue
                normalized_mode = str(mode or "").strip().lower()
                if normalized_mode in ("full", "top_left"):
                    parsed[idx] = normalized_mode
            if parsed:
                self._detection_region_overrides_by_form[fid] = parsed
        self._recompute_slot_layout()
        if layout_changed:
            self._baselines.clear()
            self._baselines_by_form = {"normal": {}}
            self._runtime = {i: _SlotRuntime() for i in range(len(self._slot_configs))}
            logger.info("Slot layout changed; baselines cleared (recalibrate required)")
        if not self._baselines_by_form.get("normal"):
            self._baselines_by_form["normal"] = dict(self._baselines)
        self._baselines = dict(self._baselines_by_form.get("normal", {}))
        active_form = (
            str(getattr(config, "active_form_id", "normal") or "normal").strip().lower()
        )
        self._active_form_id = active_form or "normal"
        self._pending_form_id = self._active_form_id
        self._pending_form_frames = 0
        self._buff_runtime = {}
        self._buff_states = {}
        self._cooldown_group_runtime = {}

    def crop_slot(self, frame: np.ndarray, slot: SlotConfig) -> np.ndarray:
        """Extract a single slot's image from the action bar frame.

        Applies slot_padding as an inset on all four sides so the analyzed
        region excludes gap pixels and icon borders.
        """
        if frame is None or frame.size == 0:
            return np.empty((0, 0, 3), dtype=np.uint8)
        pad = self._config.slot_padding
        x1 = self._frame_action_origin_x + slot.x_offset + pad
        y1 = self._frame_action_origin_y + slot.y_offset + pad
        w = max(1, slot.width - 2 * pad)
        h = max(1, slot.height - 2 * pad)
        x2 = x1 + w
        y2 = y1 + h
        return frame[y1:y2, x1:x2]

    def compute_brightness(self, slot_image: np.ndarray) -> float:
        """Compute normalized average brightness (0.0 to 1.0) of a slot image.

        Kept for potential future use; main detection uses pixel-fraction comparison.
        """
        gray = cv2.cvtColor(slot_image, cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray) / 255.0)

    def _get_brightness_channel(self, bgr_crop: np.ndarray) -> np.ndarray:
        """Convert BGR crop to grayscale (0-255)."""
        if bgr_crop is None or bgr_crop.size == 0:
            return np.empty((0, 0), dtype=np.uint8)
        return cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2GRAY)

    def _ring_mask(self, h: int, w: int, thickness: int) -> np.ndarray:
        key = (h, w, thickness)
        cached = self._ring_mask_cache.get(key)
        if cached is not None:
            return cached
        t = max(1, min(thickness, max(1, min(h, w) // 3)))
        mask = np.ones((h, w), dtype=bool)
        if h > 2 * t and w > 2 * t:
            mask[t : h - t, t : w - t] = False
        self._ring_mask_cache[key] = mask
        return mask

    def _glow_signal(
        self, slot_index: int, slot_img: np.ndarray, baseline_bright: np.ndarray
    ) -> tuple[bool, float, bool, float]:
        if not bool(getattr(self._config, "glow_enabled", True)):
            return False, 0.0, False, 0.0
        h, w = baseline_bright.shape
        if slot_img.shape[0] != h or slot_img.shape[1] != w:
            return False, 0.0, False, 0.0
        ring_thickness = int(getattr(self._config, "glow_ring_thickness_px", 4) or 4)
        ring = self._ring_mask(h, w, ring_thickness)
        if not np.any(ring):
            return False, 0.0, False, 0.0

        hsv = cv2.cvtColor(slot_img, cv2.COLOR_BGR2HSV)
        hue = hsv[:, :, 0].astype(np.int16)
        sat = hsv[:, :, 1].astype(np.int16)
        val = hsv[:, :, 2].astype(np.int16)
        base = baseline_bright.astype(np.int16)
        value_delta = int(getattr(self._config, "glow_value_delta", 35) or 35)
        slot_overrides = getattr(self._config, "glow_value_delta_by_slot", {}) or {}
        if slot_index in slot_overrides:
            value_delta = int(slot_overrides[slot_index])
        sat_min = int(getattr(self._config, "glow_saturation_min", 80) or 80)
        bright_colored = (val >= (base + value_delta)) & (sat >= sat_min)

        yellow_h_min = int(getattr(self._config, "glow_yellow_hue_min", 18) or 18)
        yellow_h_max = int(getattr(self._config, "glow_yellow_hue_max", 42) or 42)
        red_h_max_low = int(getattr(self._config, "glow_red_hue_max_low", 12) or 12)
        red_h_min_high = int(getattr(self._config, "glow_red_hue_min_high", 168) or 168)

        yellow_cond = bright_colored & (hue >= yellow_h_min) & (hue <= yellow_h_max)
        red_cond = bright_colored & ((hue <= red_h_max_low) | (hue >= red_h_min_high))

        yellow_fraction = float(np.mean(yellow_cond[ring])) if np.any(ring) else 0.0
        red_fraction = float(np.mean(red_cond[ring])) if np.any(ring) else 0.0
        glow_frac_thresh = float(
            getattr(self._config, "glow_ring_fraction", 0.18) or 0.18
        )
        ring_frac_overrides = (
            getattr(self._config, "glow_ring_fraction_by_slot", {}) or {}
        )
        if slot_index in ring_frac_overrides:
            glow_frac_thresh = float(ring_frac_overrides[slot_index])
        red_glow_frac_thresh = float(
            getattr(self._config, "glow_red_ring_fraction", glow_frac_thresh)
            or glow_frac_thresh
        )
        return (
            yellow_fraction >= glow_frac_thresh,
            yellow_fraction,
            red_fraction >= red_glow_frac_thresh,
            red_fraction,
        )

    def _glow_mode(self) -> str:
        mode = (
            str(getattr(self._config, "glow_mode", "color") or "color").strip().lower()
        )
        return mode if mode in ("color", "hybrid_motion") else "color"

    @staticmethod
    def _angular_ring_bins(
        gray: np.ndarray,
        ring_mask: np.ndarray,
        bins: int,
    ) -> np.ndarray:
        h, w = gray.shape
        if h <= 1 or w <= 1 or bins <= 1:
            return np.zeros((max(1, bins),), dtype=np.float32)
        yy, xx = np.indices((h, w))
        cy = (h - 1) * 0.5
        cx = (w - 1) * 0.5
        ang = np.arctan2(yy - cy, xx - cx)  # [-pi, pi]
        ang_n = (ang + np.pi) / (2.0 * np.pi)  # [0, 1)
        idx = np.floor(ang_n * bins).astype(np.int32)
        idx = np.clip(idx, 0, bins - 1)
        vals = gray.astype(np.float32)[ring_mask]
        ids = idx[ring_mask]
        sums = np.bincount(ids, weights=vals, minlength=bins).astype(np.float32)
        counts = np.bincount(ids, minlength=bins).astype(np.float32)
        return sums / np.maximum(counts, 1.0)

    @staticmethod
    def _rotation_motion_score(
        curr_bins: np.ndarray,
        prev_bins: Optional[np.ndarray],
    ) -> float:
        if (
            prev_bins is None
            or prev_bins.shape != curr_bins.shape
            or curr_bins.size < 4
        ):
            return 0.0
        d0 = float(np.mean(np.abs(curr_bins - prev_bins)))
        if d0 <= 1e-6:
            return 0.0
        shift_limit = max(1, min(3, curr_bins.size // 8))
        best_shift = d0
        for s in range(1, shift_limit + 1):
            ds_pos = float(np.mean(np.abs(curr_bins - np.roll(prev_bins, s))))
            ds_neg = float(np.mean(np.abs(curr_bins - np.roll(prev_bins, -s))))
            best_shift = min(best_shift, ds_pos, ds_neg)
        gain = max(0.0, d0 - best_shift)
        return max(0.0, min(1.0, gain / max(1.0, d0)))

    @staticmethod
    def _max_quadrant_fraction(mask: np.ndarray) -> float:
        if mask.size == 0:
            return 0.0
        h, w = mask.shape
        if h < 2 or w < 2:
            return float(np.mean(mask))
        hh = h // 2
        ww = w // 2
        q1 = mask[:hh, :ww]
        q2 = mask[:hh, ww:]
        q3 = mask[hh:, :ww]
        q4 = mask[hh:, ww:]
        parts = [q1, q2, q3, q4]
        vals = [float(np.mean(q)) for q in parts if q.size > 0]
        return max(vals) if vals else 0.0

    def _hybrid_glow_signal(
        self,
        slot_index: int,
        slot_img: np.ndarray,
        baseline_bright: np.ndarray,
        yellow_glow_fraction: float,
        red_glow_fraction: float,
        darkened_fraction: float,
        changed_fraction: float,
        raw_cooldown: bool,
        now: float,
    ) -> tuple[bool, bool]:
        runtime = self._runtime.setdefault(slot_index, _SlotRuntime())
        h, w = baseline_bright.shape
        if slot_img.shape[0] != h or slot_img.shape[1] != w:
            runtime.prev_glow_gray = None
            runtime.prev_glow_ring_bins = None
            runtime.glow_motion_score = 0.0
            runtime.glow_motion_ready = False
            return False, False

        gray = cv2.cvtColor(slot_img, cv2.COLOR_BGR2GRAY)
        ring_thickness = int(getattr(self._config, "glow_ring_thickness_px", 4) or 4)
        ring = self._ring_mask(h, w, ring_thickness)
        if not np.any(ring):
            runtime.prev_glow_gray = None
            runtime.prev_glow_ring_bins = None
            runtime.glow_motion_score = 0.0
            runtime.glow_motion_ready = False
            return False, False

        ring_delta = 0.0
        center_delta = 0.0
        prev_gray = runtime.prev_glow_gray
        if prev_gray is not None and prev_gray.shape == gray.shape:
            abs_diff = np.abs(
                gray.astype(np.int16) - prev_gray.astype(np.int16)
            ).astype(np.float32)
            ring_vals = abs_diff[ring]
            if ring_vals.size > 0:
                ring_delta = float(np.mean(ring_vals))
            center = np.logical_not(ring)
            center_vals = abs_diff[center]
            if center_vals.size > 0:
                center_delta = float(np.mean(center_vals))

        bins = max(8, int(getattr(self._config, "glow_motion_rotation_bins", 24) or 24))
        curr_ring_bins = self._angular_ring_bins(gray, ring, bins)
        rot_score = self._rotation_motion_score(
            curr_ring_bins, runtime.prev_glow_ring_bins
        )

        ring_delta_thresh = float(
            getattr(self._config, "glow_motion_ring_delta_threshold", 14.0) or 14.0
        )
        ring_score = max(0.0, min(1.5, ring_delta / max(1.0, ring_delta_thresh)))

        # Bright ring fraction independent of hue, for white/blue rotating procs.
        val = cv2.cvtColor(slot_img, cv2.COLOR_BGR2HSV)[:, :, 2].astype(np.int16)
        base = baseline_bright.astype(np.int16)
        value_delta = int(getattr(self._config, "glow_value_delta", 35) or 35)
        slot_overrides = getattr(self._config, "glow_value_delta_by_slot", {}) or {}
        if slot_index in slot_overrides:
            value_delta = int(slot_overrides[slot_index])
        # Hybrid uses a softer bright delta than pure color mode.
        hybrid_value_delta = max(10, int(round(value_delta * 0.35)))
        bright_ring_fraction = (
            float(np.mean((val >= (base + hybrid_value_delta))[ring]))
            if np.any(ring)
            else 0.0
        )
        bright_score = max(0.0, min(1.6, bright_ring_fraction / 0.14))

        y_thresh = float(getattr(self._config, "glow_ring_fraction", 0.18) or 0.18)
        ring_frac_overrides = (
            getattr(self._config, "glow_ring_fraction_by_slot", {}) or {}
        )
        if slot_index in ring_frac_overrides:
            y_thresh = float(ring_frac_overrides[slot_index])
        r_thresh = float(
            getattr(self._config, "glow_red_ring_fraction", y_thresh) or y_thresh
        )
        color_score = max(
            yellow_glow_fraction / max(1e-6, y_thresh),
            red_glow_fraction / max(1e-6, r_thresh),
        )
        color_score = max(0.0, min(1.6, float(color_score)))
        color_score = max(color_score, bright_score)

        center_penalty = max(0.0, center_delta - (ring_delta * 1.10)) / max(
            1.0, ring_delta_thresh
        )
        center_penalty = max(0.0, min(1.5, center_penalty))

        cooldown_penalty = 0.0
        if raw_cooldown and (darkened_fraction >= 0.22 or changed_fraction >= 0.75):
            cooldown_penalty = max(darkened_fraction, changed_fraction)
            cooldown_penalty = max(0.0, min(1.5, cooldown_penalty * 2.0))

        raw_score = (
            float(getattr(self._config, "glow_motion_weight_color", 0.35) or 0.35)
            * color_score
            + float(getattr(self._config, "glow_motion_weight_ring", 0.55) or 0.55)
            * ring_score
            + float(getattr(self._config, "glow_motion_weight_rotation", 0.45) or 0.45)
            * rot_score
            - float(
                getattr(self._config, "glow_motion_center_penalty_weight", 0.35) or 0.35
            )
            * center_penalty
            - float(
                getattr(self._config, "glow_motion_cooldown_penalty_weight", 0.25)
                or 0.25
            )
            * cooldown_penalty
        )

        alpha = float(getattr(self._config, "glow_motion_smoothing", 0.45) or 0.45)
        alpha = max(0.0, min(1.0, alpha))
        runtime.glow_motion_score = ((1.0 - alpha) * runtime.glow_motion_score) + (
            alpha * raw_score
        )

        enter = float(getattr(self._config, "glow_motion_score_enter", 0.62) or 0.62)
        exit_ = float(getattr(self._config, "glow_motion_score_exit", 0.42) or 0.42)
        if exit_ >= enter:
            exit_ = enter * 0.75
        hold_ms = max(
            0.0, float(getattr(self._config, "glow_motion_min_hold_ms", 140) or 140)
        )
        off_ms = max(
            0.0, float(getattr(self._config, "glow_motion_min_off_ms", 80) or 80)
        )
        elapsed_ms = (now - runtime.glow_motion_last_change_at) * 1000.0

        if runtime.glow_motion_ready:
            if runtime.glow_motion_score <= exit_ and elapsed_ms >= hold_ms:
                runtime.glow_motion_ready = False
                runtime.glow_motion_last_change_at = now
        else:
            if runtime.glow_motion_score >= enter and elapsed_ms >= off_ms:
                runtime.glow_motion_ready = True
                runtime.glow_motion_last_change_at = now

        candidate = runtime.glow_motion_score >= exit_
        ready = runtime.glow_motion_ready

        runtime.prev_glow_gray = gray.copy()
        runtime.prev_glow_ring_bins = curr_ring_bins.copy()
        return candidate, ready

    def _forms_from_config(self) -> set[str]:
        raw_forms = list(getattr(self._config, "forms", []) or [])
        form_ids = {
            str(raw.get("id", "") or "").strip().lower()
            for raw in raw_forms
            if isinstance(raw, dict)
        }
        form_ids.discard("")
        form_ids.add("normal")
        return form_ids

    def _set_active_form_id(self, form_id: str, now: float) -> None:
        next_form = str(form_id or "normal").strip().lower() or "normal"
        if next_form == self._active_form_id:
            return
        self._active_form_id = next_form
        self._config.active_form_id = next_form
        self._form_last_changed_at = now
        settle_ms = int(
            getattr(self._config, "form_detector", {}).get("settle_ms", 200) or 200
        )
        self._form_settle_until = now + max(0.0, settle_ms / 1000.0)
        logger.info("Active form changed to '%s'", next_form)

    def _update_active_form_id(self, now: float) -> None:
        forms = self._forms_from_config()
        fallback_form = (
            str(getattr(self._config, "active_form_id", "normal") or "normal")
            .strip()
            .lower()
        )
        if fallback_form not in forms:
            fallback_form = "normal"
        detector = getattr(self._config, "form_detector", {}) or {}
        if (
            not isinstance(detector, dict)
            or str(detector.get("type", "") or "").strip().lower() != "buff_roi"
        ):
            self._pending_form_id = fallback_form
            self._pending_form_frames = 0
            self._set_active_form_id(fallback_form, now)
            return

        roi_id = str(detector.get("roi_id", "") or "").strip().lower()
        present_form = (
            str(detector.get("present_form", "normal") or "normal").strip().lower()
        )
        absent_form = (
            str(detector.get("absent_form", "normal") or "normal").strip().lower()
        )
        if present_form not in forms:
            present_form = "normal"
        if absent_form not in forms:
            absent_form = "normal"
        buff_state = self._buff_states.get(roi_id) if roi_id else None
        if not isinstance(buff_state, dict):
            self._pending_form_id = self._active_form_id
            self._pending_form_frames = 0
            return
        if not bool(buff_state.get("calibrated", False)):
            self._pending_form_id = self._active_form_id
            self._pending_form_frames = 0
            return
        status = str(buff_state.get("status", "ok") or "").strip().lower()
        if status and status != "ok":
            self._pending_form_id = self._active_form_id
            self._pending_form_frames = 0
            return
        target_form = (
            present_form if bool(buff_state.get("present", False)) else absent_form
        )
        if target_form == self._active_form_id:
            self._pending_form_id = target_form
            self._pending_form_frames = 0
            return
        if self._pending_form_id != target_form:
            self._pending_form_id = target_form
            self._pending_form_frames = 1
        else:
            self._pending_form_frames += 1
        confirm_frames = max(1, int(detector.get("confirm_frames", 2) or 2))
        if self._pending_form_frames >= confirm_frames:
            self._set_active_form_id(target_form, now)
            self._pending_form_id = target_form
            self._pending_form_frames = 0

    def active_form_id(self) -> str:
        return str(self._active_form_id or "normal")

    def is_form_settling(self) -> bool:
        return time.time() < self._form_settle_until

    def _baseline_for_slot(self, slot_index: int) -> Optional[np.ndarray]:
        active_form = self.active_form_id()
        active = self._baselines_by_form.get(active_form, {})
        normal = self._baselines_by_form.get("normal", {})
        baseline = active.get(slot_index)
        if baseline is not None:
            return baseline
        return normal.get(slot_index)

    def _cooldown_group_id_for_slot(self, slot_index: int) -> str:
        mapping = getattr(self._config, "cooldown_group_by_slot", {}) or {}
        group_id = mapping.get(slot_index)
        if not group_id:
            return f"slot:{slot_index}"
        return str(group_id).strip().lower() or f"slot:{slot_index}"

    def _effective_region_mode_overrides(self) -> dict[int, str]:
        active_form = self.active_form_id()
        merged = dict(self._detection_region_overrides or {})
        form_overrides = self._detection_region_overrides_by_form.get(active_form, {})
        if isinstance(form_overrides, dict):
            merged.update(form_overrides)
        return merged

    def calibrate_baselines(
        self, frame: np.ndarray, form_id: Optional[str] = None
    ) -> None:
        """Capture current frame as the 'ready' baseline for all slots.

        Stores the full grayscale (2D array) per slot for pixel-wise comparison.
        Call when all abilities are off cooldown.
        """
        self._frame_action_origin_x = 0
        self._frame_action_origin_y = 0
        target_form = (
            str(form_id or self._active_form_id or "normal").strip().lower() or "normal"
        )
        bucket = dict(self._baselines_by_form.get(target_form, {}))
        for slot_cfg in self._slot_configs:
            slot_img = self.crop_slot(frame, slot_cfg)
            gray = self._get_brightness_channel(slot_img)
            if gray.size == 0:
                logger.warning(
                    f"Skipping baseline for slot {slot_cfg.index}: empty crop"
                )
                continue
            bucket[slot_cfg.index] = gray.copy()
            self._runtime[slot_cfg.index] = _SlotRuntime()
        self._baselines_by_form[target_form] = bucket
        self._baselines = dict(self._baselines_by_form.get("normal", {}))
        logger.info(
            "Calibrated brightness baselines for %s slots in form '%s'",
            len(bucket),
            target_form,
        )

    def calibrate_single_slot(
        self,
        frame: np.ndarray,
        slot_index: int,
        form_id: Optional[str] = None,
    ) -> None:
        """Calibrate baseline for one slot only; overwrites that slot's entry in _baselines."""
        if slot_index < 0 or slot_index >= len(self._slot_configs):
            logger.warning(f"calibrate_single_slot: invalid slot_index {slot_index}")
            return
        self._frame_action_origin_x = 0
        self._frame_action_origin_y = 0
        slot_cfg = self._slot_configs[slot_index]
        slot_img = self.crop_slot(frame, slot_cfg)
        gray = self._get_brightness_channel(slot_img)
        if gray.size == 0:
            logger.warning(f"calibrate_single_slot: empty crop for slot {slot_index}")
            return
        target_form = (
            str(form_id or self._active_form_id or "normal").strip().lower() or "normal"
        )
        bucket = dict(self._baselines_by_form.get(target_form, {}))
        bucket[slot_index] = gray.copy()
        self._baselines_by_form[target_form] = bucket
        self._baselines = dict(self._baselines_by_form.get("normal", {}))
        self._runtime[slot_index] = _SlotRuntime()
        logger.info(
            "Calibrated baseline for slot %s in form '%s'", slot_index, target_form
        )

    def get_baselines(self) -> dict[int, np.ndarray]:
        """Return a copy of the current baselines (slot_index -> grayscale 2D array)."""
        return {
            k: v.copy() for k, v in self._baselines_by_form.get("normal", {}).items()
        }

    def set_baselines(self, baselines: dict[int, np.ndarray]) -> None:
        """Load baselines from a previous session (e.g. from config)."""
        self._baselines_by_form["normal"] = {k: v.copy() for k, v in baselines.items()}
        self._baselines = dict(self._baselines_by_form["normal"])
        logger.info(f"Loaded {len(self._baselines)} slot baselines from config")

    def get_baselines_by_form(self) -> dict[str, dict[int, np.ndarray]]:
        return {
            form_id: {slot: arr.copy() for slot, arr in baselines.items()}
            for form_id, baselines in self._baselines_by_form.items()
        }

    def set_baselines_by_form(
        self, baselines_by_form: dict[str, dict[int, np.ndarray]]
    ) -> None:
        normalized: dict[str, dict[int, np.ndarray]] = {}
        for form_id, baselines in dict(baselines_by_form or {}).items():
            fid = str(form_id or "").strip().lower()
            if not fid:
                continue
            if not isinstance(baselines, dict):
                continue
            normalized[fid] = {int(k): v.copy() for k, v in baselines.items()}
        if "normal" not in normalized:
            normalized["normal"] = {}
        self._baselines_by_form = normalized
        self._baselines = dict(self._baselines_by_form.get("normal", {}))
        logger.info(
            "Loaded slot baselines for %s forms from config",
            len(self._baselines_by_form),
        )

    def _cast_bar_active(self, frame: np.ndarray, action_x: int, action_y: int) -> bool:
        """Optional cast-bar activity detector using frame-to-frame ROI motion."""
        region = getattr(self._config, "cast_bar_region", {}) or {}
        self._cast_bar_last_motion = 0.0
        self._cast_bar_last_activity = 0.0
        self._cast_bar_last_threshold = float(
            getattr(self._config, "cast_bar_activity_threshold", 12.0) or 12.0
        )
        self._cast_bar_last_deactivate_threshold = self._cast_bar_last_threshold * 0.6
        self._cast_bar_last_active = False
        self._cast_bar_last_present = False
        self._cast_bar_last_directional = False
        self._cast_bar_last_front = 0.0
        if not region or not bool(region.get("enabled", False)):
            self._cast_bar_motion.clear()
            self._cast_bar_prev_gray = None
            self._cast_bar_ltr_front_prev = None
            self._cast_bar_rtl_front_prev = None
            self._cast_bar_quiet_frames = 0
            self._cast_bar_active_state = False
            self._cast_bar_last_status = "off"
            return False

        # Cast-bar ROI is configured relative to the action-bar bbox.
        x = action_x + int(region.get("left", 0))
        y = action_y + int(region.get("top", 0))
        w = int(region.get("width", 0))
        h = int(region.get("height", 0))
        if w <= 1 or h <= 1:
            self._cast_bar_motion.clear()
            self._cast_bar_prev_gray = None
            self._cast_bar_ltr_front_prev = None
            self._cast_bar_rtl_front_prev = None
            self._cast_bar_quiet_frames = 0
            self._cast_bar_active_state = False
            self._cast_bar_last_status = "invalid-roi"
            return False

        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(frame.shape[1], x1 + w)
        y2 = min(frame.shape[0], y1 + h)
        if x2 <= x1 or y2 <= y1:
            self._cast_bar_motion.clear()
            self._cast_bar_prev_gray = None
            self._cast_bar_ltr_front_prev = None
            self._cast_bar_rtl_front_prev = None
            self._cast_bar_quiet_frames = 0
            self._cast_bar_active_state = False
            self._cast_bar_last_status = "out-of-frame"
            return False

        crop = frame[y1:y2, x1:x2]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        sat = hsv[:, :, 1]
        val = hsv[:, :, 2]
        # Color-based presence (kept permissive for low-saturation UI themes).
        color_mask = (sat >= 28) & (val >= 28)
        color_cov = float(np.mean(color_mask)) if color_mask.size else 0.0
        color_col_cov = (
            np.mean(color_mask, axis=0)
            if color_mask.size
            else np.array([0.0], dtype=np.float32)
        )
        color_cols = np.where(color_col_cov > 0.10)[0]
        row_cov = (
            np.mean(color_mask, axis=1)
            if color_mask.size
            else np.array([0.0], dtype=np.float32)
        )
        row_peak = float(np.max(row_cov)) if row_cov.size else 0.0
        band_rows = float(np.mean(row_cov > 0.12)) if row_cov.size else 0.0
        color_present = (
            (color_cov >= 0.02) and (row_peak >= 0.20) and (band_rows <= 0.95)
        )

        # Structure-based presence fallback (for bars that are dim/desaturated).
        gray_present = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        row_means = (
            np.mean(gray_present, axis=1)
            if gray_present.size
            else np.array([0.0], dtype=np.float32)
        )
        row_variation = float(np.std(row_means)) if row_means.size else 0.0
        gy = cv2.Sobel(gray_present, cv2.CV_32F, 0, 1, ksize=3)
        h_edges = np.abs(gy) > 18.0
        row_edge_cov = (
            np.mean(h_edges, axis=1)
            if h_edges.size
            else np.array([0.0], dtype=np.float32)
        )
        edge_peak = float(np.max(row_edge_cov)) if row_edge_cov.size else 0.0
        edge_band = float(np.mean(row_edge_cov > 0.06)) if row_edge_cov.size else 0.0
        structure_present = (
            (row_variation >= 2.0) and (edge_peak >= 0.08) and (edge_band <= 0.70)
        )

        bar_present = color_present or structure_present
        self._cast_bar_last_present = bar_present

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        prev = self._cast_bar_prev_gray
        if prev is None or prev.shape != gray.shape:
            self._cast_bar_prev_gray = gray
            self._cast_bar_motion.clear()
            self._cast_bar_ltr_front_prev = None
            self._cast_bar_rtl_front_prev = None
            self._cast_bar_quiet_frames = 0
            self._cast_bar_active_state = False
            self._cast_bar_last_status = "priming"
            return False

        diff = cv2.absdiff(gray, prev)
        motion = float(np.mean(diff))
        self._cast_bar_prev_gray = gray
        self._cast_bar_motion.append(motion)
        self._cast_bar_last_motion = motion

        motion_mask = diff > 12
        col_cov = (
            np.mean(motion_mask, axis=0)
            if motion_mask.size
            else np.array([0.0], dtype=np.float32)
        )
        active_cols = np.where(col_cov > 0.10)[0]
        ltr_directional_ok = False
        rtl_directional_ok = False
        ltr_front = self._cast_bar_ltr_front_prev if self._cast_bar_ltr_front_prev is not None else 0.0
        rtl_front = self._cast_bar_rtl_front_prev if self._cast_bar_rtl_front_prev is not None else 0.0

        # --- LTR: motion-based (rightmost motion column advances right) ---
        if active_cols.size > 0 and col_cov.size > 1:
            cmin = int(active_cols.min())
            cmax = int(active_cols.max())
            span = (cmax - cmin + 1) / float(col_cov.size)
            ltr_front = float(cmax) / float(col_cov.size - 1)
            ltr_fwd = self._cast_bar_ltr_front_prev is None or ltr_front >= (self._cast_bar_ltr_front_prev - 0.08)
            ltr_directional_ok = ltr_fwd and (span <= 0.75)
            if ltr_fwd:
                self._cast_bar_ltr_front_prev = ltr_front
            self._cast_bar_quiet_frames = 0
        else:
            self._cast_bar_quiet_frames += 1
            if self._cast_bar_quiet_frames >= 3:
                self._cast_bar_ltr_front_prev = None
                self._cast_bar_rtl_front_prev = None
                self._cast_bar_last_direction = "?"

        # --- RTL: color-position-based (rightmost colored column retreats left) ---
        if color_cols.size > 0 and color_mask.shape[1] > 1:
            color_cmax = int(color_cols.max())
            rtl_front = 1.0 - float(color_cmax) / float(color_mask.shape[1] - 1)
            rtl_fwd = self._cast_bar_rtl_front_prev is None or rtl_front >= (self._cast_bar_rtl_front_prev - 0.08)
            rtl_directional_ok = rtl_fwd  # no span check: bar is expected to be wide
            if rtl_fwd:
                self._cast_bar_rtl_front_prev = rtl_front

        directional_ok = ltr_directional_ok or rtl_directional_ok

        if ltr_directional_ok and not rtl_directional_ok:
            self._cast_bar_last_direction = "ltr"
        elif rtl_directional_ok and not ltr_directional_ok:
            self._cast_bar_last_direction = "rtl"
        elif ltr_directional_ok and rtl_directional_ok:
            self._cast_bar_last_direction = "?"
        self._cast_bar_last_directional = directional_ok
        if self._cast_bar_last_direction == "ltr":
            self._cast_bar_last_front = float(ltr_front)
        elif self._cast_bar_last_direction == "rtl":
            self._cast_bar_last_front = float(rtl_front)
        else:
            self._cast_bar_last_front = float(max(ltr_front, rtl_front))

        history_frames = max(
            3, int(getattr(self._config, "cast_bar_history_frames", 8) or 8)
        )
        if self._cast_bar_motion.maxlen != history_frames:
            self._cast_bar_motion = deque(
                list(self._cast_bar_motion), maxlen=history_frames
            )
        if len(self._cast_bar_motion) < 2:
            self._cast_bar_last_status = "priming"
            return False
        activity = max(self._cast_bar_motion)
        activate_threshold = self._cast_bar_last_threshold
        deactivate_threshold = self._cast_bar_last_deactivate_threshold
        if self._cast_bar_active_state:
            active = activity >= deactivate_threshold and bar_present and directional_ok
        else:
            active = activity >= activate_threshold and bar_present and directional_ok
        self._cast_bar_active_state = active
        self._cast_bar_last_activity = activity
        self._cast_bar_last_active = active
        if active:
            self._cast_bar_last_status = "active"
        elif not bar_present:
            self._cast_bar_last_status = "no-bar"
        elif not directional_ok:
            self._cast_bar_last_status = "not-directional"
        else:
            self._cast_bar_last_status = "idle"
        return active

    def _decode_gray_template(self, template_dict: object) -> Optional[np.ndarray]:
        if not isinstance(template_dict, dict):
            return None
        shape = template_dict.get("shape")
        raw_b64 = template_dict.get("data")
        if (
            not isinstance(shape, list)
            or len(shape) != 2
            or not all(isinstance(v, int) and v > 0 for v in shape)
            or not isinstance(raw_b64, str)
            or not raw_b64.strip()
        ):
            return None
        try:
            key = f"{shape[0]}x{shape[1]}:{raw_b64}"
            cached = self._buff_template_cache.get(key)
            if cached is not None:
                return cached
            arr = np.frombuffer(base64.b64decode(raw_b64), dtype=np.uint8)
            arr = arr.reshape((int(shape[0]), int(shape[1]))).copy()
            self._buff_template_cache[key] = arr
            return arr
        except Exception:
            return None

    @staticmethod
    def _template_similarity(
        gray_roi: np.ndarray, gray_template: Optional[np.ndarray]
    ) -> float:
        if gray_template is None or gray_template.size == 0 or gray_roi.size == 0:
            return 0.0
        if gray_template.shape != gray_roi.shape:
            gray_template = cv2.resize(
                gray_template,
                (gray_roi.shape[1], gray_roi.shape[0]),
                interpolation=cv2.INTER_AREA,
            )
        diff = cv2.absdiff(gray_roi, gray_template)
        diff_score = max(0.0, 1.0 - (float(np.mean(diff)) / 255.0))

        # Add normalized correlation so global grayscale similarity alone
        # does not mark unrelated ROIs as "present" at low thresholds.
        roi_std = float(np.std(gray_roi))
        template_std = float(np.std(gray_template))
        if roi_std < 1e-6 or template_std < 1e-6:
            return diff_score
        corr = cv2.matchTemplate(gray_roi, gray_template, cv2.TM_CCOEFF_NORMED)
        corr_raw = float(corr[0, 0]) if corr.size else -1.0
        corr_score = max(0.0, min(1.0, (corr_raw + 1.0) * 0.5))
        return min(diff_score, corr_score)

    def _analyze_buffs(self, frame: np.ndarray, action_origin: tuple[int, int]) -> None:
        states: dict[str, dict] = {}
        action_x = int(action_origin[0])
        action_y = int(action_origin[1])
        red_h_max_low = int(getattr(self._config, "glow_red_hue_max_low", 12) or 12)
        red_h_min_high = int(getattr(self._config, "glow_red_hue_min_high", 168) or 168)
        sat_min = int(getattr(self._config, "glow_saturation_min", 80) or 80)
        glow_confirm_frames = max(
            1, int(getattr(self._config, "glow_confirm_frames", 2) or 2)
        )
        red_frac_thresh = float(
            getattr(self._config, "glow_red_ring_fraction", 0.18) or 0.18
        )
        for raw in list(getattr(self._config, "buff_rois", []) or []):
            if not isinstance(raw, dict):
                continue
            buff_id = str(raw.get("id", "") or "").strip().lower()
            if not buff_id:
                continue
            runtime = self._buff_runtime.setdefault(buff_id, _BuffRuntime())
            enabled = bool(raw.get("enabled", True))
            left = int(raw.get("left", 0))
            top = int(raw.get("top", 0))
            width = int(raw.get("width", 0))
            height = int(raw.get("height", 0))
            threshold = max(0.0, min(1.0, float(raw.get("match_threshold", 0.88))))
            confirm_frames = max(1, int(raw.get("confirm_frames", 2)))
            calibration = raw.get("calibration", {})
            if not isinstance(calibration, dict):
                calibration = {}
            present_t = self._decode_gray_template(calibration.get("present_template"))
            calibrated = present_t is not None

            status = "ok"
            present_similarity = 0.0
            missing_similarity = 0.0
            candidate = False
            present = False
            red_glow_candidate = False
            red_glow_ready = False
            red_glow_fraction = 0.0
            if not enabled:
                status = "off"
                runtime.candidate_frames = 0
                runtime.red_glow_candidate_frames = 0
            elif width <= 1 or height <= 1:
                status = "invalid-roi"
                runtime.candidate_frames = 0
                runtime.red_glow_candidate_frames = 0
            elif not calibrated:
                status = "uncalibrated"
                runtime.candidate_frames = 0
                runtime.red_glow_candidate_frames = 0
            else:
                x1 = action_x + left
                y1 = action_y + top
                x2 = x1 + width
                y2 = y1 + height
                if x1 < 0 or y1 < 0 or x2 > frame.shape[1] or y2 > frame.shape[0]:
                    status = "out-of-frame"
                    runtime.candidate_frames = 0
                    runtime.red_glow_candidate_frames = 0
                else:
                    roi = frame[y1:y2, x1:x2]
                    roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                    present_similarity = self._template_similarity(roi_gray, present_t)
                    candidate = present_similarity >= threshold
                    if candidate:
                        runtime.candidate_frames += 1
                    else:
                        runtime.candidate_frames = 0
                    present = runtime.candidate_frames >= confirm_frames

                    # Buff ROI red-glow detection used by buff-sourced DoT refresh rules.
                    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
                    hue = hsv[:, :, 0].astype(np.int16)
                    sat = hsv[:, :, 1].astype(np.int16)
                    val = hsv[:, :, 2].astype(np.int16)
                    h, w = roi_gray.shape
                    ring = self._ring_mask(
                        h,
                        w,
                        int(getattr(self._config, "glow_ring_thickness_px", 4) or 4),
                    )
                    if np.any(ring):
                        val_floor = max(64, int(np.percentile(val[ring], 60)))
                        red_cond = (
                            ((hue <= red_h_max_low) | (hue >= red_h_min_high))
                            & (sat >= sat_min)
                            & (val >= val_floor)
                        )
                        red_glow_fraction = float(np.mean(red_cond[ring]))
                        red_glow_candidate = red_glow_fraction >= red_frac_thresh
                    if red_glow_candidate:
                        runtime.red_glow_candidate_frames += 1
                    else:
                        runtime.red_glow_candidate_frames = 0
                    red_glow_ready = (
                        runtime.red_glow_candidate_frames >= glow_confirm_frames
                    )

            states[buff_id] = {
                "id": buff_id,
                "name": str(raw.get("name", "") or "").strip() or buff_id,
                "enabled": enabled,
                "calibrated": calibrated,
                "left": left,
                "top": top,
                "width": width,
                "height": height,
                "status": status,
                "present_similarity": float(present_similarity),
                "missing_similarity": float(missing_similarity),
                "candidate": bool(candidate),
                "candidate_frames": int(runtime.candidate_frames),
                "confirm_frames": int(confirm_frames),
                "present": bool(present),
                "red_glow_candidate": bool(red_glow_candidate),
                "red_glow_candidate_frames": int(runtime.red_glow_candidate_frames),
                "red_glow_fraction": float(red_glow_fraction),
                "red_glow_ready": bool(red_glow_ready),
            }
        self._buff_states = states

    def buff_states(self) -> dict[str, dict]:
        return {k: dict(v) for k, v in self._buff_states.items()}

    def cast_bar_debug(self) -> dict:
        """Latest cast-bar ROI motion debug info for UI."""
        return {
            "status": self._cast_bar_last_status,
            "motion": float(self._cast_bar_last_motion),
            "activity": float(self._cast_bar_last_activity),
            "threshold": float(self._cast_bar_last_threshold),
            "deactivate_threshold": float(self._cast_bar_last_deactivate_threshold),
            "active": bool(self._cast_bar_last_active),
            "present": bool(self._cast_bar_last_present),
            "directional": bool(self._cast_bar_last_directional),
            "front": float(self._cast_bar_last_front),
            "direction": str(self._cast_bar_last_direction),
            "gate_active": bool(self._cast_gate_active),
        }

    def form_state(self) -> dict:
        return {
            "active_form_id": self.active_form_id(),
            "settling": self.is_form_settling(),
            "last_changed_at": float(self._form_last_changed_at),
        }

    def _next_state_with_cast_logic(
        self,
        slot_index: int,
        darkened_fraction: float,
        is_raw_cooldown: bool,
        now: float,
        cast_gate_active: bool = True,
    ) -> tuple[
        SlotState, Optional[float], Optional[float], Optional[float], Optional[float]
    ]:
        """Return per-slot state without icon-based cast detection."""
        runtime = self._runtime.setdefault(slot_index, _SlotRuntime())
        if is_raw_cooldown:
            runtime.state = SlotState.ON_COOLDOWN
        else:
            runtime.state = SlotState.READY
        runtime.last_darkened_fraction = darkened_fraction
        return (
            runtime.state,
            None,
            None,
            None,
            None,
        )

    def analyze_frame(
        self,
        frame: np.ndarray,
        action_origin: tuple[int, int] = (0, 0),
    ) -> ActionBarState:
        """Analyze a full action bar frame and return state for all slots.

        Args:
            frame: BGR numpy array of the captured region.
            action_origin: top-left (x, y) of the action-bar bbox within frame.

        Returns:
            ActionBarState with a SlotSnapshot per slot.
        """
        now = time.time()
        snapshots: list[SlotSnapshot] = []
        self._frame_action_origin_x = int(action_origin[0])
        self._frame_action_origin_y = int(action_origin[1])

        thresh = self._config.brightness_drop_threshold
        frac_thresh = self._config.cooldown_pixel_fraction
        change_frac_thresh = float(
            getattr(self._config, "cooldown_change_pixel_fraction", frac_thresh)
            or frac_thresh
        )
        cooldown_min_sec = max(
            0.0, (getattr(self._config, "cooldown_min_duration_ms", 0) or 0) / 1000.0
        )
        glow_confirm_frames = max(
            1, int(getattr(self._config, "glow_confirm_frames", 2) or 2)
        )
        glow_mode = self._glow_mode()
        cast_bar_active = self._cast_bar_active(
            frame,
            self._frame_action_origin_x,
            self._frame_action_origin_y,
        )
        cast_bar_region = getattr(self._config, "cast_bar_region", {}) or {}
        cast_roi_enabled = bool(cast_bar_region.get("enabled", False))
        if cast_bar_active:
            # Keep gate active briefly to absorb frame ordering jitter between ROI motion and icon darkening.
            self._cast_bar_active_until = now + 0.25
        cast_gate_active = cast_roi_enabled and (
            cast_bar_active or (now < self._cast_bar_active_until)
        )
        self._cast_gate_active = cast_gate_active
        self._analyze_buffs(frame, action_origin)
        self._update_active_form_id(now)
        form_settling = now < self._form_settle_until
        override_slots = {
            int(v)
            for v in list(
                getattr(self._config, "glow_override_cooldown_by_slot", []) or []
            )
            if str(v).strip()
        }
        change_ignore_slots = {
            int(v)
            for v in list(
                getattr(self._config, "cooldown_change_ignore_by_slot", []) or []
            )
            if str(v).strip()
        }
        cooldown_groups_raw_seen: dict[str, bool] = {}
        region_overrides = self._effective_region_mode_overrides()

        for slot_cfg in self._slot_configs:
            slot_img = self.crop_slot(frame, slot_cfg)
            baseline_bright = self._baseline_for_slot(slot_cfg.index)
            region_mode = region_overrides.get(slot_cfg.index, self._detection_region)
            if region_mode == "top_left" and baseline_bright is not None:
                h, w = slot_img.shape[:2]
                slot_detect = slot_img[: h // 2, : w // 2]
                baseline_detect = baseline_bright[: h // 2, : w // 2]
                current_bright = self._get_brightness_channel(slot_detect)
                baseline_bright_for_frac = baseline_detect
            else:
                current_bright = self._get_brightness_channel(slot_img)
                baseline_bright_for_frac = baseline_bright
            glow_ready = False
            glow_candidate = False
            glow_fraction = 0.0
            yellow_glow_ready = False
            yellow_glow_candidate = False
            yellow_glow_fraction = 0.0
            red_glow_ready = False
            red_glow_candidate = False
            red_glow_fraction = 0.0

            if (
                current_bright.size == 0
                or baseline_bright_for_frac is None
                or baseline_bright_for_frac.shape != current_bright.shape
            ):
                runtime = self._runtime.setdefault(slot_cfg.index, _SlotRuntime())
                runtime.prev_glow_gray = None
                runtime.prev_glow_ring_bins = None
                runtime.glow_motion_score = 0.0
                runtime.glow_motion_ready = False
                state = SlotState.UNKNOWN
                darkened_fraction = 0.0
                cast_progress = None
                cast_ends_at = None
                last_cast_start_at = None
                last_cast_success_at = None
            else:
                # Pixels where brightness dropped by more than threshold (uses detection region only)
                drop = baseline_bright_for_frac.astype(
                    np.int16
                ) - current_bright.astype(np.int16)
                darkened_count = np.sum(drop > thresh)
                total = current_bright.size
                darkened_fraction = darkened_count / total if total else 0.0
                # Also treat large absolute change from baseline as cooldown/not-ready
                # so bright buff/debuff duration sweeps don't look ready.
                abs_delta = np.abs(drop)
                changed_count = np.sum(abs_delta > thresh)
                changed_fraction = changed_count / total if total else 0.0
                ignore_change_for_slot = slot_cfg.index in change_ignore_slots
                raw_dark_cooldown = darkened_fraction >= frac_thresh
                raw_changed_cooldown = (not ignore_change_for_slot) and (
                    changed_fraction >= change_frac_thresh
                )
                raw_cooldown = raw_dark_cooldown or raw_changed_cooldown

                # Cooldown hysteresis: once a slot is on cooldown, require a lower
                # release threshold before it can return to ready. This prevents
                # per-icon art/animation from flipping ready several seconds early.
                runtime = self._runtime.setdefault(slot_cfg.index, _SlotRuntime())
                group_id = self._cooldown_group_id_for_slot(slot_cfg.index)
                group_runtime = self._cooldown_group_runtime.setdefault(
                    group_id,
                    _CooldownGroupRuntime(),
                )
                prev_state = runtime.state
                if runtime.state == SlotState.ON_COOLDOWN or group_runtime.was_cooldown:
                    release_factor = float(
                        getattr(self._config, "cooldown_release_factor", 0.70) or 0.70
                    )
                    release_factor = max(0.25, min(1.0, release_factor))
                    dark_release_thresh = frac_thresh * release_factor
                    change_release_thresh = change_frac_thresh * release_factor
                    hold_dark_cooldown = darkened_fraction >= dark_release_thresh
                    hold_changed_cooldown = (not ignore_change_for_slot) and (
                        changed_fraction >= change_release_thresh
                    )
                    dark_mask = drop > thresh
                    max_quad_dark_fraction = self._max_quadrant_fraction(dark_mask)
                    quad_release_thresh = float(
                        getattr(
                            self._config,
                            "cooldown_release_quadrant_fraction",
                            max(frac_thresh, 0.22),
                        )
                        or max(frac_thresh, 0.22)
                    )
                    hold_quadrant_cooldown = (
                        max_quad_dark_fraction >= quad_release_thresh
                    )
                    raw_cooldown = (
                        raw_cooldown
                        or hold_dark_cooldown
                        or hold_changed_cooldown
                        or hold_quadrant_cooldown
                    )
                release_confirm_sec = max(
                    0.0,
                    (getattr(self._config, "cooldown_release_confirm_ms", 260) or 260)
                    / 1000.0,
                )
                if group_runtime.was_cooldown:
                    if raw_cooldown:
                        group_runtime.cooldown_release_candidate_started_at = None
                    else:
                        # Fast-path: pixels are clearly at baseline (well below release threshold),
                        # so skip the debounce. This prevents the 260ms release-confirm from adding
                        # latency when a GCD ends cleanly and the slot returns to its baseline state.
                        clearly_ready = darkened_fraction < dark_release_thresh * 0.5
                        if clearly_ready:
                            group_runtime.cooldown_release_candidate_started_at = None
                            # raw_cooldown remains False → immediate release
                        else:
                            if (
                                group_runtime.cooldown_release_candidate_started_at
                                is None
                            ):
                                group_runtime.cooldown_release_candidate_started_at = (
                                    now
                                )
                            if (
                                now
                                - group_runtime.cooldown_release_candidate_started_at
                            ) < release_confirm_sec:
                                raw_cooldown = True
                            else:
                                group_runtime.cooldown_release_candidate_started_at = (
                                    None
                                )
                else:
                    group_runtime.cooldown_release_candidate_started_at = None
                cooldown_groups_raw_seen[group_id] = cooldown_groups_raw_seen.get(
                    group_id, False
                ) or bool(raw_cooldown)
                cooldown_pending = False
                if raw_cooldown:
                    if group_runtime.cooldown_candidate_started_at is None:
                        group_runtime.cooldown_candidate_started_at = now
                    if (
                        not group_runtime.was_cooldown
                        and cooldown_min_sec > 0.0
                        and (now - group_runtime.cooldown_candidate_started_at)
                        < cooldown_min_sec
                    ):
                        cooldown_pending = True
                else:
                    group_runtime.cooldown_candidate_started_at = None
                (
                    state,
                    cast_progress,
                    cast_ends_at,
                    last_cast_start_at,
                    last_cast_success_at,
                ) = self._next_state_with_cast_logic(
                    slot_cfg.index,
                    darkened_fraction,
                    raw_cooldown and not cooldown_pending,
                    now,
                    cast_gate_active=cast_gate_active,
                )
                if cooldown_pending and state == SlotState.READY:
                    state = SlotState.GCD
                if (
                    form_settling
                    and prev_state != SlotState.UNKNOWN
                    and state not in (SlotState.CASTING, SlotState.CHANNELING)
                    and state != prev_state
                ):
                    state = prev_state
                (
                    yellow_glow_candidate,
                    yellow_glow_fraction,
                    red_glow_candidate,
                    red_glow_fraction,
                ) = self._glow_signal(slot_cfg.index, slot_img, baseline_bright)

                if glow_mode == "hybrid_motion":
                    hybrid_candidate, hybrid_ready = self._hybrid_glow_signal(
                        slot_cfg.index,
                        slot_img,
                        baseline_bright,
                        yellow_glow_fraction,
                        red_glow_fraction,
                        darkened_fraction,
                        changed_fraction,
                        raw_cooldown,
                        now,
                    )
                    yellow_glow_candidate = hybrid_candidate
                    yellow_glow_ready = hybrid_ready
                else:
                    if yellow_glow_candidate:
                        runtime.yellow_glow_candidate_frames += 1
                    else:
                        runtime.yellow_glow_candidate_frames = 0
                    yellow_glow_ready = (
                        runtime.yellow_glow_candidate_frames >= glow_confirm_frames
                    )
                    runtime.prev_glow_gray = None
                    runtime.prev_glow_ring_bins = None
                    runtime.glow_motion_score = 0.0
                    runtime.glow_motion_ready = False

                if red_glow_candidate:
                    runtime.red_glow_candidate_frames += 1
                else:
                    runtime.red_glow_candidate_frames = 0
                red_glow_ready = (
                    runtime.red_glow_candidate_frames >= glow_confirm_frames
                )
                glow_candidate = yellow_glow_candidate or red_glow_candidate
                glow_fraction = max(yellow_glow_fraction, red_glow_fraction)
                if glow_mode == "hybrid_motion":
                    glow_ready = bool(yellow_glow_ready or red_glow_ready)
                    runtime.glow_candidate_frames = int(yellow_glow_candidate)
                else:
                    if glow_candidate:
                        runtime.glow_candidate_frames += 1
                    else:
                        runtime.glow_candidate_frames = 0
                    glow_ready = runtime.glow_candidate_frames >= glow_confirm_frames
                allow_any_glow_override = slot_cfg.index in override_slots
                # Red glow is an explicit "refresh now" cue for DoT-style rules.
                # Allow it to override ON_COOLDOWN regardless of darkening source.
                if (
                    red_glow_ready or (allow_any_glow_override and glow_ready)
                ) and state == SlotState.ON_COOLDOWN:
                    state = SlotState.READY
                if cast_bar_active and bool(
                    getattr(self._config, "lock_ready_while_cast_bar_active", False)
                ):
                    if state == SlotState.READY:
                        state = SlotState.LOCKED
                group_runtime.was_cooldown = group_runtime.was_cooldown or (
                    state == SlotState.ON_COOLDOWN
                )

            # TODO Phase 2: If on cooldown and OCR enabled, read countdown number
            cooldown_remaining = None

            snapshots.append(
                SlotSnapshot(
                    index=slot_cfg.index,
                    state=state,
                    brightness=float(darkened_fraction),
                    cooldown_remaining=cooldown_remaining,
                    cast_progress=cast_progress,
                    cast_ends_at=cast_ends_at,
                    last_cast_start_at=last_cast_start_at,
                    last_cast_success_at=last_cast_success_at,
                    glow_candidate=bool(glow_candidate),
                    glow_fraction=float(glow_fraction),
                    glow_ready=bool(glow_ready),
                    yellow_glow_candidate=bool(yellow_glow_candidate),
                    yellow_glow_fraction=float(yellow_glow_fraction),
                    yellow_glow_ready=bool(yellow_glow_ready),
                    red_glow_candidate=bool(red_glow_candidate),
                    red_glow_fraction=float(red_glow_fraction),
                    red_glow_ready=bool(red_glow_ready),
                    timestamp=now,
                )
            )

        # Log per-slot summary occasionally for debugging
        for group_id, group_runtime in self._cooldown_group_runtime.items():
            raw_seen = cooldown_groups_raw_seen.get(group_id, False)
            if not raw_seen:
                group_runtime.cooldown_candidate_started_at = None
                group_runtime.cooldown_release_candidate_started_at = None
                group_runtime.was_cooldown = False
        self._analyze_frame_count += 1
        if logger.isEnabledFor(logging.DEBUG) and self._analyze_frame_count % 30 == 0:
            summary = ", ".join(
                f"s{s.index}={s.brightness:.2f}({s.state.value})" for s in snapshots
            )
            logger.debug(
                f"Slots: region={self._detection_region} thresh={thresh} frac_thresh={frac_thresh} | {summary}"
            )

        cast_ends_at = (
            self._cast_bar_active_until if now < self._cast_bar_active_until else None
        )
        return ActionBarState(
            slots=snapshots,
            timestamp=now,
            cast_active=bool(cast_gate_active),
            cast_ends_at=cast_ends_at,
        )
