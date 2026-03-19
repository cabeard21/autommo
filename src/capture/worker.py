"""CaptureWorker — background QThread for screen capture, analysis, and key sending."""

from __future__ import annotations

import logging

import numpy as np

from PyQt6.QtCore import QThread, pyqtSignal

from src.capture.capture_plan import compute_capture_plan
from src.capture.screen_capture import ScreenCapture
from src.models import AppConfig, BoundingBox

logger = logging.getLogger(__name__)


class CaptureWorker(QThread):
    """Worker thread that captures frames and analyzes them at the configured FPS."""

    frame_captured = pyqtSignal(np.ndarray)  # Raw frame for preview
    state_updated = pyqtSignal(list)  # List of slot state dicts
    form_state_updated = pyqtSignal(object)  # Dict with active_form_id and settle state
    buff_state_updated = pyqtSignal(object)  # Dict of buff ROI states
    cast_bar_debug = pyqtSignal(object)  # Live cast-bar ROI motion/status info
    action_history_debug = pyqtSignal(object)  # Live tracker / previous-action debug info
    key_action = pyqtSignal(object)  # Dict when a key was sent or blocked (action, keybind, etc.)

    def __init__(self, analyzer, config: AppConfig, key_sender=None):
        super().__init__()
        self._analyzer = analyzer
        self._config = config
        self._key_sender = key_sender
        self._queue_listener = None
        self._movement_tracker = None
        self._running = False
        self._capture: ScreenCapture | None = None
        self._active_monitor_index: int | None = None

    def set_queue_listener(self, listener) -> None:
        """Set the spell queue listener so the worker can pass queued override and clear on send."""
        self._queue_listener = listener

    def set_movement_tracker(self, tracker) -> None:
        """Set the movement tracker so the worker can pass movement state to evaluate_and_send."""
        self._movement_tracker = tracker

    def _start_capture(self, monitor_index: int) -> None:
        self._capture = ScreenCapture(monitor_index=monitor_index)
        self._capture.start()
        self._active_monitor_index = monitor_index

    def _restart_capture(self, monitor_index: int) -> None:
        if self._capture is not None:
            self._capture.stop()
        self._start_capture(monitor_index)
        logger.info(f"Capture worker switched to monitor {monitor_index}")

    def _capture_plan(self, monitor_width: int, monitor_height: int) -> tuple[BoundingBox, tuple[int, int]]:
        """Return capture bbox (expanded for cast ROI and buff ROIs) and action origin inside it."""
        return compute_capture_plan(
            action_bbox=self._config.bounding_box,
            cast_bar_region=getattr(self._config, "cast_bar_region", {}) or {},
            buff_rois=getattr(self._config, "buff_rois", []) or [],
            action_history_tracker=getattr(self._config, "action_history_tracker", {}) or {},
            monitor_width=monitor_width,
            monitor_height=monitor_height,
        )

    def _resolve_previous_action_from_tracker(
        self, tracker_debug: dict, timestamp: float
    ) -> dict:
        result = dict(tracker_debug or {})
        if self._key_sender is not None:
            pending = self._key_sender.pending_previous_action()
            if self._key_sender.pending_previous_action_timed_out(timestamp):
                result["event"] = "timeout"
                pending = None
            match = self._pending_action_tracker_match(pending)
            result.update(match)
            event = str(result.get("event", "none") or "none")
            status = str(result.get("status", "off") or "off")
            stable_present = bool(result.get("stable_present", False))
            confirm_frames = max(
                1,
                int(
                    (
                        getattr(self._config, "action_history_tracker", {}) or {}
                    ).get("confirm_frames", 2)
                    or 2
                ),
            )
            stationary_frames = int(result.get("spawn_stationary_frames", 0) or 0)
            template_confirmed = (
                match.get("matched", False)
                and status == "ok"
                and stable_present
                and stationary_frames >= confirm_frames
            )
            if pending is not None and not match.get("template_available", False):
                if event == "none":
                    result["event"] = "template_missing"
                self._key_sender.clear_confirmed_previous_action()
                self._key_sender.cancel_pending_previous_action()
            elif match.get("template_available", False):
                if template_confirmed:
                    if event == "none":
                        result["event"] = "template_confirmed"
                    self._key_sender.confirm_pending_previous_action(timestamp=timestamp)
                elif match.get("matched", False) and event == "cast_cancelled":
                    self._key_sender.cancel_pending_previous_action()
            result["pending_previous_action"] = self._key_sender.pending_previous_action()
            result["confirmed_previous_action"] = self._key_sender.confirmed_previous_action()
        return result

    def _pending_action_tracker_match(self, pending: dict | None) -> dict:
        if not isinstance(pending, dict):
            return {
                "template_available": False,
                "matched": False,
                "expected_action_label": "",
            }
        item_type = str(pending.get("item_type", "") or "").strip().lower()
        label = ""
        template: dict | None = None
        if item_type == "slot":
            slot_index = pending.get("slot_index")
            if isinstance(slot_index, int):
                templates = list(getattr(self._config, "slot_tracker_templates", []) or [])
                if 0 <= slot_index < len(templates):
                    template = templates[slot_index] if isinstance(templates[slot_index], dict) else None
                label = f"slot:{slot_index}"
        elif item_type == "manual":
            action_id = str(pending.get("action_id", "") or "").strip().lower()
            for action in self._config.active_manual_actions():
                if str(action.get("id", "") or "").strip().lower() == action_id:
                    template = (
                        dict(action.get("tracker_template", {}) or {})
                        if isinstance(action.get("tracker_template", {}), dict)
                        else None
                    )
                    label = str(action.get("name", "") or "").strip() or action_id
                    break
        if not isinstance(template, dict) or not template:
            return {
                "template_available": False,
                "matched": False,
                "expected_action_label": label,
            }
        match = self._analyzer.action_history_match(template)
        return {
            "template_available": bool(match.get("available", False)),
            "matched": bool(match.get("matched", False)),
            "expected_action_label": label,
            "match_gray_similarity": float(match.get("gray_similarity", 0.0) or 0.0),
            "match_color_similarity": float(match.get("color_similarity", 0.0) or 0.0),
            "match_effective_similarity": float(match.get("effective_similarity", 0.0) or 0.0),
            "match_threshold": float(match.get("threshold", 0.0) or 0.0),
            "match_color_threshold": float(match.get("color_threshold", 0.0) or 0.0),
            "match_color_enabled": bool(match.get("color_enabled", False)),
        }

    def run(self) -> None:
        self._running = True
        self._start_capture(self._config.monitor_index)
        try:
            interval = 1.0 / max(1, self._config.polling_fps)
            logger.info(f"Capture worker started at {self._config.polling_fps} FPS")

            while self._running:
                try:
                    if self._active_monitor_index != self._config.monitor_index:
                        self._restart_capture(self._config.monitor_index)
                    monitor = self._capture.monitor_info
                    capture_bbox, action_origin = self._capture_plan(
                        monitor_width=int(monitor["width"]),
                        monitor_height=int(monitor["height"]),
                    )
                    frame = self._capture.grab_region(capture_bbox)
                    ax, ay = action_origin
                    aw = int(self._config.bounding_box.width)
                    ah = int(self._config.bounding_box.height)
                    action_frame = frame[ay:ay + ah, ax:ax + aw]
                    if action_frame.size == 0:
                        action_frame = frame
                    slot_detection_mode = str(
                        getattr(self._config, "slot_detection_mode", "slot") or "slot"
                    ).strip().lower()
                    if slot_detection_mode != "buff_only":
                        self.frame_captured.emit(action_frame)

                    state = self._analyzer.analyze_frame(frame, action_origin=action_origin)
                    form_state = self._analyzer.form_state()
                    self._config.active_form_id = str(form_state.get("active_form_id", "normal") or "normal")
                    slot_dicts = [
                        {
                            "index": s.index,
                            "state": s.state.value,
                            "active_form_id": self._config.active_form_id,
                            "keybind": (
                                self._config.keybinds[s.index]
                                if s.index < len(self._config.keybinds)
                                else None
                            ),
                            "cooldown_remaining": s.cooldown_remaining,
                            "cast_progress": s.cast_progress,
                            "cast_ends_at": s.cast_ends_at,
                            "last_cast_start_at": s.last_cast_start_at,
                            "last_cast_success_at": s.last_cast_success_at,
                            "glow_candidate": bool(getattr(s, "glow_candidate", False)),
                            "glow_fraction": float(getattr(s, "glow_fraction", 0.0) or 0.0),
                            "glow_ready": bool(getattr(s, "glow_ready", False)),
                            "yellow_glow_candidate": bool(getattr(s, "yellow_glow_candidate", False)),
                            "yellow_glow_fraction": float(
                                getattr(s, "yellow_glow_fraction", 0.0) or 0.0
                            ),
                            "yellow_glow_ready": bool(getattr(s, "yellow_glow_ready", False)),
                            "red_glow_candidate": bool(getattr(s, "red_glow_candidate", False)),
                            "red_glow_fraction": float(getattr(s, "red_glow_fraction", 0.0) or 0.0),
                            "red_glow_ready": bool(getattr(s, "red_glow_ready", False)),
                            "brightness": s.brightness,
                        }
                        for s in state.slots
                    ]
                    # Snapshot queue at start of tick so priority never replaces it this tick.
                    queued = self._queue_listener.get_queue() if self._queue_listener else None
                    self.state_updated.emit(slot_dicts)
                    self.form_state_updated.emit(form_state)
                    buff_states = self._analyzer.buff_states()
                    self.buff_state_updated.emit(buff_states)
                    self.cast_bar_debug.emit(self._analyzer.cast_bar_debug())
                    tracker_debug = self._resolve_previous_action_from_tracker(
                        self._analyzer.action_history_debug(), state.timestamp
                    )
                    self.action_history_debug.emit(tracker_debug)
                    if self._key_sender is not None:
                        on_queued_sent = (
                            self._queue_listener.clear_queue if self._queue_listener else None
                        )
                        movement_active = (
                            self._movement_tracker.is_moving
                            if self._movement_tracker is not None
                            else None
                        )
                        result = self._key_sender.evaluate_and_send(
                            state,
                            self._config.active_priority_items(),
                            self._config.keybinds,
                            self._config.active_manual_actions(),
                            getattr(self._config, "automation_enabled", False),
                            buff_states=buff_states,
                            queued_override=queued,
                            on_queued_sent=on_queued_sent,
                            movement_active=movement_active,
                        )
                        if result is not None:
                            self.key_action.emit(result)

                except Exception as e:
                    logger.error(f"Capture error: {e}", exc_info=True)

                self.msleep(int(interval * 1000))
        finally:
            if self._capture is not None:
                self._capture.stop()

    def stop(self) -> None:
        self._running = False
        self.wait()

    def update_config(self, config: AppConfig) -> None:
        self._config = config
        self._analyzer.update_config(config)
        if self._key_sender is not None:
            self._key_sender.update_config(config)
