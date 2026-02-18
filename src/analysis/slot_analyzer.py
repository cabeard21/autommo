"""Slot analyzer — segments the action bar and detects cooldown states.

Uses brightness-based pixel-fraction comparison: store baseline grayscale per slot,
then count the fraction of pixels where brightness has dropped by more than
brightness_drop_threshold. If that fraction exceeds cooldown_pixel_fraction, mark
ON_COOLDOWN. Per-pixel comparison catches partial GCD sweeps. Phase 2: OCR.
"""
from __future__ import annotations

from collections import deque
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
    """Per-slot temporal memory used by cast-state transition logic."""

    state: SlotState = SlotState.UNKNOWN
    cast_candidate_frames: int = 0
    cast_started_at: Optional[float] = None
    cast_ends_at: Optional[float] = None
    last_cast_start_at: Optional[float] = None
    last_cast_success_at: Optional[float] = None
    last_darkened_fraction: float = 0.0
    glow_candidate_frames: int = 0
    yellow_glow_candidate_frames: int = 0
    red_glow_candidate_frames: int = 0


class SlotAnalyzer:
    """Analyzes a captured action bar image to determine per-slot cooldown state."""

    def __init__(self, config: AppConfig):
        self._config = config
        self._slot_configs: list[SlotConfig] = []
        self._baselines: dict[int, np.ndarray] = {}  # slot_index -> baseline grayscale (2D uint8)
        self._ocr_engine: Optional[object] = None  # Lazy-loaded OCREngine
        self._runtime: dict[int, _SlotRuntime] = {}
        self._analyze_frame_count = 0
        self._cast_bar_motion: deque[float] = deque(maxlen=8)
        self._cast_bar_prev_gray: Optional[np.ndarray] = None
        self._cast_bar_active_until: float = 0.0
        self._cast_bar_last_motion: float = 0.0
        self._cast_bar_last_activity: float = 0.0
        self._cast_bar_last_threshold: float = float(
            getattr(config, "cast_bar_activity_threshold", 12.0) or 12.0
        )
        self._cast_bar_last_deactivate_threshold: float = self._cast_bar_last_threshold * 0.6
        self._cast_bar_last_active: bool = False
        self._cast_bar_last_status: str = "off"
        self._cast_bar_last_present: bool = False
        self._cast_bar_last_directional: bool = False
        self._cast_bar_last_front: float = 0.0
        self._cast_bar_active_state: bool = False
        self._cast_bar_front_prev: Optional[float] = None
        self._cast_bar_quiet_frames: int = 0
        self._cast_gate_active: bool = False
        self._frame_action_origin_x: int = 0
        self._frame_action_origin_y: int = 0
        self._ring_mask_cache: dict[tuple[int, int, int], np.ndarray] = {}
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
        logger.debug(f"Slot layout: {count} slots, each {slot_w}x{slot_h}px, gap={gap}px")

    def update_config(self, config: AppConfig) -> None:
        """Update config and recompute layout. Clears baselines if layout changed."""
        layout_changed = (
            config.slot_count != self._config.slot_count
            or config.slot_gap_pixels != self._config.slot_gap_pixels
            or config.slot_padding != self._config.slot_padding
        )
        self._config = config
        self._recompute_slot_layout()
        if layout_changed:
            self._baselines.clear()
            self._runtime = {i: _SlotRuntime() for i in range(len(self._slot_configs))}
            logger.info("Slot layout changed; baselines cleared (recalibrate required)")

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
        self, slot_img: np.ndarray, baseline_bright: np.ndarray
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
        glow_frac_thresh = float(getattr(self._config, "glow_ring_fraction", 0.18) or 0.18)
        red_glow_frac_thresh = float(
            getattr(self._config, "glow_red_ring_fraction", glow_frac_thresh) or glow_frac_thresh
        )
        return (
            yellow_fraction >= glow_frac_thresh,
            yellow_fraction,
            red_fraction >= red_glow_frac_thresh,
            red_fraction,
        )

    def calibrate_baselines(self, frame: np.ndarray) -> None:
        """Capture current frame as the 'ready' baseline for all slots.

        Stores the full grayscale (2D array) per slot for pixel-wise comparison.
        Call when all abilities are off cooldown.
        """
        self._frame_action_origin_x = 0
        self._frame_action_origin_y = 0
        for slot_cfg in self._slot_configs:
            slot_img = self.crop_slot(frame, slot_cfg)
            gray = self._get_brightness_channel(slot_img)
            if gray.size == 0:
                logger.warning(f"Skipping baseline for slot {slot_cfg.index}: empty crop")
                continue
            self._baselines[slot_cfg.index] = gray.copy()
            self._runtime[slot_cfg.index] = _SlotRuntime()
        logger.info(f"Calibrated brightness baselines for {len(self._baselines)} slots")

    def calibrate_single_slot(self, frame: np.ndarray, slot_index: int) -> None:
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
        self._baselines[slot_index] = gray.copy()
        self._runtime[slot_index] = _SlotRuntime()
        logger.info(f"Calibrated baseline for slot {slot_index}")

    def get_baselines(self) -> dict[int, np.ndarray]:
        """Return a copy of the current baselines (slot_index -> grayscale 2D array)."""
        return {k: v.copy() for k, v in self._baselines.items()}

    def set_baselines(self, baselines: dict[int, np.ndarray]) -> None:
        """Load baselines from a previous session (e.g. from config)."""
        self._baselines = {k: v.copy() for k, v in baselines.items()}
        logger.info(f"Loaded {len(self._baselines)} slot baselines from config")

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
            self._cast_bar_front_prev = None
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
            self._cast_bar_front_prev = None
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
            self._cast_bar_front_prev = None
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
        row_cov = np.mean(color_mask, axis=1) if color_mask.size else np.array([0.0], dtype=np.float32)
        row_peak = float(np.max(row_cov)) if row_cov.size else 0.0
        band_rows = float(np.mean(row_cov > 0.12)) if row_cov.size else 0.0
        color_present = (color_cov >= 0.02) and (row_peak >= 0.20) and (band_rows <= 0.95)

        # Structure-based presence fallback (for bars that are dim/desaturated).
        gray_present = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        row_means = np.mean(gray_present, axis=1) if gray_present.size else np.array([0.0], dtype=np.float32)
        row_variation = float(np.std(row_means)) if row_means.size else 0.0
        gy = cv2.Sobel(gray_present, cv2.CV_32F, 0, 1, ksize=3)
        h_edges = np.abs(gy) > 18.0
        row_edge_cov = np.mean(h_edges, axis=1) if h_edges.size else np.array([0.0], dtype=np.float32)
        edge_peak = float(np.max(row_edge_cov)) if row_edge_cov.size else 0.0
        edge_band = float(np.mean(row_edge_cov > 0.06)) if row_edge_cov.size else 0.0
        structure_present = (row_variation >= 2.0) and (edge_peak >= 0.08) and (edge_band <= 0.70)

        bar_present = color_present or structure_present
        self._cast_bar_last_present = bar_present

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        prev = self._cast_bar_prev_gray
        if prev is None or prev.shape != gray.shape:
            self._cast_bar_prev_gray = gray
            self._cast_bar_motion.clear()
            self._cast_bar_front_prev = None
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
        col_cov = np.mean(motion_mask, axis=0) if motion_mask.size else np.array([0.0], dtype=np.float32)
        active_cols = np.where(col_cov > 0.10)[0]
        directional_ok = False
        front = self._cast_bar_front_prev if self._cast_bar_front_prev is not None else 0.0
        if active_cols.size > 0 and col_cov.size > 1:
            cmin = int(active_cols.min())
            cmax = int(active_cols.max())
            span = (cmax - cmin + 1) / float(col_cov.size)
            front = float(cmax) / float(col_cov.size - 1)
            prev_front = self._cast_bar_front_prev
            forward_ok = prev_front is None or front >= (prev_front - 0.08)
            directional_ok = forward_ok and (span <= 0.75)
            if directional_ok:
                self._cast_bar_front_prev = front
            self._cast_bar_quiet_frames = 0
        else:
            self._cast_bar_quiet_frames += 1
            if self._cast_bar_quiet_frames >= 3:
                self._cast_bar_front_prev = None
        self._cast_bar_last_directional = directional_ok
        self._cast_bar_last_front = float(front or 0.0)

        history_frames = max(3, int(getattr(self._config, "cast_bar_history_frames", 8) or 8))
        if self._cast_bar_motion.maxlen != history_frames:
            self._cast_bar_motion = deque(list(self._cast_bar_motion), maxlen=history_frames)
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
            "gate_active": bool(self._cast_gate_active),
        }

    def _next_state_with_cast_logic(
        self,
        slot_index: int,
        darkened_fraction: float,
        is_raw_cooldown: bool,
        now: float,
        cast_gate_active: bool = True,
    ) -> tuple[SlotState, Optional[float], Optional[float], Optional[float], Optional[float]]:
        """Return cast-aware state and timing metadata for one slot."""
        runtime = self._runtime.setdefault(slot_index, _SlotRuntime())
        cast_enabled = bool(getattr(self._config, "cast_detection_enabled", True))
        min_frac = float(getattr(self._config, "cast_candidate_min_fraction", 0.05) or 0.05)
        max_frac = float(getattr(self._config, "cast_candidate_max_fraction", 0.22) or 0.22)
        confirm_frames = max(1, int(getattr(self._config, "cast_confirm_frames", 2) or 2))
        cast_min_sec = max(0.05, (getattr(self._config, "cast_min_duration_ms", 150) or 150) / 1000.0)
        cast_max_sec = max(cast_min_sec, (getattr(self._config, "cast_max_duration_ms", 3000) or 3000) / 1000.0)
        cancel_grace_sec = max(0.0, (getattr(self._config, "cast_cancel_grace_ms", 120) or 120) / 1000.0)
        channeling_enabled = bool(getattr(self._config, "channeling_enabled", True))
        cast_candidate = min_frac <= darkened_fraction < max_frac

        if not cast_enabled:
            runtime.state = SlotState.ON_COOLDOWN if is_raw_cooldown else SlotState.READY
            runtime.cast_candidate_frames = 0
            runtime.cast_started_at = None
            runtime.cast_ends_at = None
            runtime.last_darkened_fraction = darkened_fraction
            return (
                runtime.state,
                None,
                None,
                runtime.last_cast_start_at,
                runtime.last_cast_success_at,
            )

        if is_raw_cooldown:
            runtime.state = SlotState.ON_COOLDOWN
            runtime.cast_candidate_frames = 0
            if runtime.cast_started_at is not None:
                runtime.last_cast_success_at = now
            runtime.cast_started_at = None
            runtime.cast_ends_at = None
            runtime.last_darkened_fraction = darkened_fraction
            return (
                runtime.state,
                None,
                None,
                runtime.last_cast_start_at,
                runtime.last_cast_success_at,
            )

        if runtime.state in (SlotState.CASTING, SlotState.CHANNELING):
            cast_started_at = runtime.cast_started_at or now
            elapsed = now - cast_started_at
            cast_ends_at = runtime.cast_ends_at
            if cast_candidate:
                if (
                    channeling_enabled
                    and runtime.state == SlotState.CASTING
                    and elapsed >= cast_max_sec
                ):
                    runtime.state = SlotState.CHANNELING
                    cast_ends_at = None
                    runtime.cast_ends_at = None
                runtime.last_darkened_fraction = darkened_fraction
                progress = None
                if runtime.state == SlotState.CASTING and cast_ends_at is not None:
                    progress = min(1.0, max(0.0, elapsed / cast_max_sec))
                return (
                    runtime.state,
                    progress,
                    cast_ends_at,
                    runtime.last_cast_start_at,
                    runtime.last_cast_success_at,
                )
            if elapsed < (cast_min_sec + cancel_grace_sec):
                runtime.last_darkened_fraction = darkened_fraction
                progress = min(1.0, max(0.0, elapsed / cast_max_sec))
                return (
                    runtime.state,
                    progress,
                    cast_ends_at,
                    runtime.last_cast_start_at,
                    runtime.last_cast_success_at,
                )
            runtime.state = SlotState.READY
            runtime.cast_started_at = None
            runtime.cast_ends_at = None
            runtime.cast_candidate_frames = 0
            runtime.last_darkened_fraction = darkened_fraction
            return (
                runtime.state,
                None,
                None,
                runtime.last_cast_start_at,
                runtime.last_cast_success_at,
            )

        if cast_candidate:
            if not cast_gate_active:
                # If cast-bar ROI gating is enabled and inactive, suppress entering cast state.
                runtime.cast_candidate_frames = 0
                runtime.state = SlotState.READY
                runtime.cast_started_at = None
                runtime.cast_ends_at = None
                runtime.last_darkened_fraction = darkened_fraction
                return (
                    runtime.state,
                    None,
                    None,
                    runtime.last_cast_start_at,
                    runtime.last_cast_success_at,
                )
            runtime.cast_candidate_frames += 1
            if runtime.cast_candidate_frames >= confirm_frames:
                runtime.state = SlotState.CASTING
                runtime.cast_started_at = now
                runtime.last_cast_start_at = now
                runtime.cast_ends_at = now + cast_max_sec
                runtime.last_darkened_fraction = darkened_fraction
                return (
                    runtime.state,
                    0.0,
                    runtime.cast_ends_at,
                    runtime.last_cast_start_at,
                    runtime.last_cast_success_at,
                )
            runtime.state = SlotState.READY
            runtime.last_darkened_fraction = darkened_fraction
            return (
                runtime.state,
                None,
                None,
                runtime.last_cast_start_at,
                runtime.last_cast_success_at,
            )

        runtime.cast_candidate_frames = 0
        runtime.state = SlotState.READY
        runtime.cast_started_at = None
        runtime.cast_ends_at = None
        runtime.last_darkened_fraction = darkened_fraction
        return (
            runtime.state,
            None,
            None,
            runtime.last_cast_start_at,
            runtime.last_cast_success_at,
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
            getattr(self._config, "cooldown_change_pixel_fraction", frac_thresh) or frac_thresh
        )
        glow_confirm_frames = max(1, int(getattr(self._config, "glow_confirm_frames", 2) or 2))
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
        cast_gate_active = (not cast_roi_enabled) or cast_bar_active or (now < self._cast_bar_active_until)
        self._cast_gate_active = cast_gate_active

        for slot_cfg in self._slot_configs:
            slot_img = self.crop_slot(frame, slot_cfg)
            current_bright = self._get_brightness_channel(slot_img)
            baseline_bright = self._baselines.get(slot_cfg.index)
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
                or baseline_bright is None
                or baseline_bright.shape != current_bright.shape
            ):
                state = SlotState.UNKNOWN
                darkened_fraction = 0.0
                cast_progress = None
                cast_ends_at = None
                last_cast_start_at = None
                last_cast_success_at = None
            else:
                # Pixels where brightness dropped by more than threshold
                drop = baseline_bright.astype(np.int16) - current_bright.astype(np.int16)
                darkened_count = np.sum(drop > thresh)
                total = current_bright.size
                darkened_fraction = darkened_count / total if total else 0.0
                # Also treat large absolute change from baseline as cooldown/not-ready
                # so bright buff/debuff duration sweeps don't look ready.
                abs_delta = np.abs(drop)
                changed_count = np.sum(abs_delta > thresh)
                changed_fraction = changed_count / total if total else 0.0
                raw_dark_cooldown = darkened_fraction >= frac_thresh
                raw_changed_cooldown = changed_fraction >= change_frac_thresh
                raw_cooldown = raw_dark_cooldown or raw_changed_cooldown

                # Cooldown hysteresis: once a slot is on cooldown, require a lower
                # release threshold before it can return to ready. This prevents
                # per-icon art/animation from flipping ready several seconds early.
                runtime = self._runtime.setdefault(slot_cfg.index, _SlotRuntime())
                if runtime.state == SlotState.ON_COOLDOWN:
                    release_factor = 0.5
                    dark_release_thresh = frac_thresh * release_factor
                    change_release_thresh = change_frac_thresh * release_factor
                    hold_dark_cooldown = darkened_fraction >= dark_release_thresh
                    hold_changed_cooldown = changed_fraction >= change_release_thresh
                    raw_cooldown = raw_cooldown or hold_dark_cooldown or hold_changed_cooldown
                (
                    state,
                    cast_progress,
                    cast_ends_at,
                    last_cast_start_at,
                    last_cast_success_at,
                ) = self._next_state_with_cast_logic(
                    slot_cfg.index,
                    darkened_fraction,
                    raw_cooldown,
                    now,
                    cast_gate_active=cast_gate_active,
                )
                (
                    yellow_glow_candidate,
                    yellow_glow_fraction,
                    red_glow_candidate,
                    red_glow_fraction,
                ) = self._glow_signal(slot_img, baseline_bright)
                glow_candidate = yellow_glow_candidate or red_glow_candidate
                glow_fraction = max(yellow_glow_fraction, red_glow_fraction)
                if glow_candidate:
                    runtime.glow_candidate_frames += 1
                else:
                    runtime.glow_candidate_frames = 0
                if yellow_glow_candidate:
                    runtime.yellow_glow_candidate_frames += 1
                else:
                    runtime.yellow_glow_candidate_frames = 0
                if red_glow_candidate:
                    runtime.red_glow_candidate_frames += 1
                else:
                    runtime.red_glow_candidate_frames = 0
                glow_ready = runtime.glow_candidate_frames >= glow_confirm_frames
                yellow_glow_ready = runtime.yellow_glow_candidate_frames >= glow_confirm_frames
                red_glow_ready = runtime.red_glow_candidate_frames >= glow_confirm_frames
                # Red glow is an explicit "refresh now" cue for DoT-style rules.
                # Allow it to override ON_COOLDOWN regardless of darkening source.
                if red_glow_ready and state == SlotState.ON_COOLDOWN:
                    state = SlotState.READY
                if cast_bar_active and bool(
                    getattr(self._config, "lock_ready_while_cast_bar_active", False)
                ):
                    if state == SlotState.READY:
                        state = SlotState.LOCKED

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
        self._analyze_frame_count += 1
        if logger.isEnabledFor(logging.DEBUG) and self._analyze_frame_count % 30 == 0:
            summary = ", ".join(
                f"s{s.index}={s.brightness:.2f}({s.state.value})" for s in snapshots
            )
            logger.debug(f"Slots: thresh={thresh} frac_thresh={frac_thresh} | {summary}")

        return ActionBarState(slots=snapshots, timestamp=now)
