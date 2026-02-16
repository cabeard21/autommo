"""Slot analyzer — segments the action bar and detects cooldown states.

Uses brightness-based pixel-fraction comparison: store baseline grayscale per slot,
then count the fraction of pixels where brightness has dropped by more than
brightness_drop_threshold. If that fraction exceeds cooldown_pixel_fraction, mark
ON_COOLDOWN. Per-pixel comparison catches partial GCD sweeps. Phase 2: OCR.
"""
from __future__ import annotations

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


class SlotAnalyzer:
    """Analyzes a captured action bar image to determine per-slot cooldown state."""

    def __init__(self, config: AppConfig):
        self._config = config
        self._slot_configs: list[SlotConfig] = []
        self._baselines: dict[int, np.ndarray] = {}  # slot_index -> baseline grayscale (2D uint8)
        self._ocr_engine: Optional[object] = None  # Lazy-loaded OCREngine
        self._analyze_frame_count = 0
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
        logger.info(f"Calibrated brightness baselines for {len(self._baselines)} slots")

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

        for slot_cfg in self._slot_configs:
            slot_img = self.crop_slot(frame, slot_cfg)
            current_bright = self._get_brightness_channel(slot_img)
            baseline_bright = self._baselines.get(slot_cfg.index)

            if baseline_bright is None or baseline_bright.shape != current_bright.shape:
                state = SlotState.UNKNOWN
                darkened_fraction = 0.0
            else:
                # Pixels where brightness dropped by more than threshold
                drop = baseline_bright.astype(np.int16) - current_bright.astype(np.int16)
                darkened_count = np.sum(drop > thresh)
                total = current_bright.size
                darkened_fraction = darkened_count / total if total else 0.0
                state = (
                    SlotState.ON_COOLDOWN
                    if darkened_fraction >= frac_thresh
                    else SlotState.READY
                )

            # TODO Phase 2: If on cooldown and OCR enabled, read countdown number
            cooldown_remaining = None

            snapshots.append(
                SlotSnapshot(
                    index=slot_cfg.index,
                    state=state,
                    brightness=float(darkened_fraction),
                    cooldown_remaining=cooldown_remaining,
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
