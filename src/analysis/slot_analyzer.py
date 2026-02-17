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


class SlotAnalyzer:
    """Analyzes a captured action bar image to determine per-slot cooldown state."""

    def __init__(self, config: AppConfig):
        self._config = config
        self._slot_configs: list[SlotConfig] = []
        self._baselines: dict[int, np.ndarray] = {}  # slot_index -> baseline grayscale (2D uint8)
        self._ocr_engine: Optional[object] = None  # Lazy-loaded OCREngine
        self._runtime: dict[int, _SlotRuntime] = {}
        self._analyze_frame_count = 0
        self._cast_bar_means: deque[float] = deque(maxlen=8)
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
        pad = self._config.slot_padding
        x1 = slot.x_offset + pad
        y1 = slot.y_offset + pad
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
        return cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2GRAY)

    def calibrate_baselines(self, frame: np.ndarray) -> None:
        """Capture current frame as the 'ready' baseline for all slots.

        Stores the full grayscale (2D array) per slot for pixel-wise comparison.
        Call when all abilities are off cooldown.
        """
        for slot_cfg in self._slot_configs:
            slot_img = self.crop_slot(frame, slot_cfg)
            self._baselines[slot_cfg.index] = self._get_brightness_channel(slot_img).copy()
            self._runtime[slot_cfg.index] = _SlotRuntime()
        logger.info(f"Calibrated brightness baselines for {len(self._baselines)} slots")

    def calibrate_single_slot(self, frame: np.ndarray, slot_index: int) -> None:
        """Calibrate baseline for one slot only; overwrites that slot's entry in _baselines."""
        if slot_index < 0 or slot_index >= len(self._slot_configs):
            logger.warning(f"calibrate_single_slot: invalid slot_index {slot_index}")
            return
        slot_cfg = self._slot_configs[slot_index]
        slot_img = self.crop_slot(frame, slot_cfg)
        self._baselines[slot_index] = self._get_brightness_channel(slot_img).copy()
        self._runtime[slot_index] = _SlotRuntime()
        logger.info(f"Calibrated baseline for slot {slot_index}")

    def get_baselines(self) -> dict[int, np.ndarray]:
        """Return a copy of the current baselines (slot_index -> grayscale 2D array)."""
        return {k: v.copy() for k, v in self._baselines.items()}

    def set_baselines(self, baselines: dict[int, np.ndarray]) -> None:
        """Load baselines from a previous session (e.g. from config)."""
        self._baselines = {k: v.copy() for k, v in baselines.items()}
        logger.info(f"Loaded {len(self._baselines)} slot baselines from config")

    def _cast_bar_active(self, frame: np.ndarray) -> bool:
        """Optional cast-bar activity detector using a bounded rolling mean range."""
        region = getattr(self._config, "cast_bar_region", {}) or {}
        if not region or not bool(region.get("enabled", False)):
            self._cast_bar_means.clear()
            return False

        x = int(region.get("left", 0))
        y = int(region.get("top", 0))
        w = int(region.get("width", 0))
        h = int(region.get("height", 0))
        if w <= 1 or h <= 1:
            return False

        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(frame.shape[1], x1 + w)
        y2 = min(frame.shape[0], y1 + h)
        if x2 <= x1 or y2 <= y1:
            return False

        crop = frame[y1:y2, x1:x2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        self._cast_bar_means.append(float(np.mean(gray)))
        history_frames = max(3, int(getattr(self._config, "cast_bar_history_frames", 8) or 8))
        if self._cast_bar_means.maxlen != history_frames:
            self._cast_bar_means = deque(list(self._cast_bar_means), maxlen=history_frames)
        if len(self._cast_bar_means) < 3:
            return False
        activity = max(self._cast_bar_means) - min(self._cast_bar_means)
        threshold = float(getattr(self._config, "cast_bar_activity_threshold", 12.0) or 12.0)
        return activity >= threshold

    def _next_state_with_cast_logic(
        self,
        slot_index: int,
        darkened_fraction: float,
        is_raw_cooldown: bool,
        now: float,
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

    def analyze_frame(self, frame: np.ndarray) -> ActionBarState:
        """Analyze a full action bar frame and return state for all slots.

        Args:
            frame: BGR numpy array of the captured action bar region.

        Returns:
            ActionBarState with a SlotSnapshot per slot.
        """
        now = time.time()
        snapshots: list[SlotSnapshot] = []

        thresh = self._config.brightness_drop_threshold
        frac_thresh = self._config.cooldown_pixel_fraction
        cast_bar_active = self._cast_bar_active(frame)

        for slot_cfg in self._slot_configs:
            slot_img = self.crop_slot(frame, slot_cfg)
            current_bright = self._get_brightness_channel(slot_img)
            baseline_bright = self._baselines.get(slot_cfg.index)

            if baseline_bright is None or baseline_bright.shape != current_bright.shape:
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
                raw_cooldown = darkened_fraction >= frac_thresh
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
                )
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
