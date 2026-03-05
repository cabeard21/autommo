"""AppCoordinator — wires all application components together.

Owns all signal connections, calibration logic, hotkey dispatch,
and the capture toggle lifecycle.
"""

from __future__ import annotations

import logging
import sys

import cv2

from PyQt6.QtCore import QRect, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.analysis import SlotAnalyzer
from src.app.baseline_codec import (
    decode_baselines,
    decode_baselines_by_form,
    encode_baselines_by_form,
    encode_color_template,
    encode_gray_template,
)
from src.automation.binds import normalize_bind
from src.automation.global_hotkey import GlobalToggleListener
from src.automation.key_sender import KeySender
from src.automation.modifier_tracker import ShiftModifierTracker
from src.automation.movement_tracker import MovementTracker
from src.automation.queue_listener import QueueListener
from src.capture import CaptureWorker, ScreenCapture, compute_capture_plan
from src.models import AppConfig
from src.overlay import CalibrationOverlay
from src.ui import MainWindow
from src.ui.settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)


def monitor_rect_for_index(monitor_index: int, monitors: list[dict]) -> QRect:
    """Resolve a monitor index (1-based) to a QRect, with safe fallback."""
    if monitors:
        idx = min(max(1, monitor_index), len(monitors)) - 1
        m = monitors[idx]
        return QRect(m["left"], m["top"], m["width"], m["height"])
    return QRect(0, 0, 1920, 1080)


class AppCoordinator:
    """Owns and wires all application components."""

    def __init__(self, config: AppConfig, app: QApplication) -> None:
        self._config = config
        self._app = app
        self._is_running = False

        self._init_analyzer()
        self._init_ui()
        self._init_overlay()
        self._init_worker()
        self._wire_signals()
        self._wire_calibration()
        self._wire_hotkeys()
        self._wire_listeners()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _init_analyzer(self) -> None:
        self._analyzer = SlotAnalyzer(self._config)
        try:
            decoded_by_form = decode_baselines_by_form(
                getattr(self._config, "slot_baselines_by_form", {})
            )
            if decoded_by_form:
                self._analyzer.set_baselines_by_form(decoded_by_form)
            elif self._config.slot_baselines:
                decoded = decode_baselines(self._config.slot_baselines)
                if decoded:
                    self._analyzer.set_baselines(decoded)
        except Exception as e:
            logger.warning("Could not load saved baselines: %s", e)

    def _init_ui(self) -> None:
        self._window = MainWindow(self._config)
        self._window.set_before_save_callback(self._sync_baselines_to_config)

        self._settings_dialog = SettingsDialog(
            self._config,
            before_save_callback=self._sync_baselines_to_config,
            after_import_callback=self._load_baselines_from_config,
            parent=self._window,
        )

        capture = ScreenCapture(monitor_index=self._config.monitor_index)
        capture.start()
        self._monitors = capture.list_monitors()
        capture.stop()

        self._settings_dialog.populate_monitors(self._monitors)
        if getattr(self._config, "always_on_top", False):
            self._window.setWindowFlags(
                self._window.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
            )
        self._window.show()

    def _init_overlay(self) -> None:
        monitor_rect = monitor_rect_for_index(self._config.monitor_index, self._monitors)
        self._overlay = CalibrationOverlay(monitor_geometry=monitor_rect)
        self._overlay.update_bounding_box(self._config.bounding_box)
        self._overlay.update_cast_bar_region(getattr(self._config, "cast_bar_region", {}))
        self._overlay.update_buff_rois(getattr(self._config, "buff_rois", []) or [])
        self._overlay.update_show_active_screen_outline(
            getattr(self._config, "show_active_screen_outline", False)
        )
        self._overlay.update_form_detector(getattr(self._config, "form_detector", {}) or {})
        self._overlay.update_slot_detection_mode(
            getattr(self._config, "slot_detection_mode", "slot")
        )
        self._overlay.update_form_state(
            {"active_form_id": getattr(self._config, "active_form_id", "normal")}
        )
        self._overlay.update_slot_layout(
            self._config.slot_count,
            self._config.slot_gap_pixels,
            self._config.slot_padding,
        )
        if self._config.overlay_enabled:
            self._overlay.show()

    def _init_worker(self) -> None:
        self._key_sender = KeySender(self._config)
        self._worker = CaptureWorker(self._analyzer, self._config, self._key_sender)
        self._window.set_key_sender(self._key_sender)

    # ------------------------------------------------------------------
    # Baseline helpers
    # ------------------------------------------------------------------

    def _sync_baselines_to_config(self) -> None:
        baselines_by_form = self._analyzer.get_baselines_by_form()
        self._config.slot_baselines_by_form = encode_baselines_by_form(baselines_by_form)
        self._config.slot_baselines = self._config.slot_baselines_by_form.get("normal", [])

    def _load_baselines_from_config(self, cfg: AppConfig) -> None:
        try:
            decoded = decode_baselines_by_form(getattr(cfg, "slot_baselines_by_form", {}))
            if decoded:
                self._analyzer.set_baselines_by_form(decoded)
                logger.info("Loaded baselines for %d forms", len(decoded))
                return
            legacy = decode_baselines(getattr(cfg, "slot_baselines", []) or [])
            if legacy:
                self._analyzer.set_baselines(legacy)
                logger.info("Loaded %d legacy baselines from imported config", len(legacy))
                return
            self._analyzer.set_baselines_by_form({})
            logger.info("Imported config contains no baselines")
        except Exception as e:
            logger.warning("Could not load imported baselines: %s", e)

    # ------------------------------------------------------------------
    # Config change
    # ------------------------------------------------------------------

    def _on_config_changed(self, new_config: AppConfig) -> None:
        prev_mode = str(
            getattr(self._config, "slot_detection_mode", "slot") or "slot"
        ).strip().lower()
        self._config = new_config
        self._window.set_config(new_config)
        self._worker.update_config(new_config)
        self._key_sender.update_config(new_config)
        self._overlay.update_cast_bar_region(getattr(new_config, "cast_bar_region", {}))
        self._overlay.update_buff_rois(getattr(new_config, "buff_rois", []) or [])
        self._overlay.update_form_detector(getattr(new_config, "form_detector", {}) or {})
        self._overlay.update_slot_detection_mode(
            getattr(new_config, "slot_detection_mode", "slot")
        )
        self._overlay.update_bounding_box(new_config.bounding_box)
        self._overlay.update_slot_layout(
            new_config.slot_count,
            new_config.slot_gap_pixels,
            new_config.slot_padding,
        )
        self._overlay.update_monitor_geometry(
            monitor_rect_for_index(new_config.monitor_index, self._monitors)
        )
        self._overlay.update_show_active_screen_outline(
            getattr(new_config, "show_active_screen_outline", False)
        )
        if getattr(new_config, "overlay_enabled", True):
            self._overlay.show()
        else:
            self._overlay.hide()
        self._window.refresh_from_config()
        flags = self._window.windowFlags()
        if getattr(new_config, "always_on_top", False):
            self._window.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self._window.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        self._window.show()
        next_mode = str(
            getattr(new_config, "slot_detection_mode", "slot") or "slot"
        ).strip().lower()
        if prev_mode != next_mode:
            if next_mode == "buff_only":
                self._window.show_status_message(
                    "Buff-only mode enabled: slot items are ignored.", 2200
                )
            else:
                self._window.show_status_message("Slot detection mode enabled.", 1800)

    # ------------------------------------------------------------------
    # Key action dispatch
    # ------------------------------------------------------------------

    def _on_key_action(self, result: dict) -> None:
        slot_index = result.get("slot_index")
        item_type = str(result.get("item_type", "") or "").strip().lower()
        names = getattr(self._config, "slot_display_names", [])
        display_name = str(result.get("display_name", "") or "").strip() or "Unidentified"
        if item_type == "slot" and (
            slot_index is not None
            and slot_index < len(names)
            and (names[slot_index] or "").strip()
        ):
            display_name = (names[slot_index] or "").strip()
        if result.get("action") == "sent":
            self._window.record_last_action_sent(
                result["keybind"], result.get("timestamp", 0.0), display_name
            )
        elif result.get("action") == "blocked" and result.get("reason") == "window":
            self._window.set_next_intention_blocked(result["keybind"], display_name)
        elif result.get("action") == "blocked" and result.get("reason") == "casting":
            self._window.set_next_intention_casting_wait(
                slot_index=result.get("slot_index"),
                cast_ends_at=result.get("cast_ends_at"),
            )

    # ------------------------------------------------------------------
    # Capture toggle
    # ------------------------------------------------------------------

    def _toggle_capture(self) -> None:
        if self._is_running:
            self._worker.stop()
            self._window._btn_start.setText("▶ Start Capture")
            self._window.set_capture_running(False)
            self._overlay.set_capture_active(False)
            self._is_running = False
        else:
            self._worker.start()
            self._window._btn_start.setText("⏹ Stop Capture")
            self._window.set_capture_running(True)
            self._overlay.set_capture_active(True)
            self._is_running = True

    def _start_capture_if_stopped(self) -> None:
        if not self._is_running:
            self._worker.start()
            self._window._btn_start.setText("⏹ Stop Capture")
            self._window.set_capture_running(True)
            self._overlay.set_capture_active(True)
            self._is_running = True

    # ------------------------------------------------------------------
    # Hotkey handling
    # ------------------------------------------------------------------

    def _all_profile_binds(self) -> list[str]:
        binds: list[str] = []
        for p in getattr(self._config, "priority_profiles", []) or []:
            toggle_bind = normalize_bind(str(p.get("toggle_bind", "") or ""))
            single_fire_bind = normalize_bind(str(p.get("single_fire_bind", "") or ""))
            if toggle_bind:
                binds.append(toggle_bind)
            if single_fire_bind:
                binds.append(single_fire_bind)
        return binds

    def _on_hotkey_triggered(self, triggered_bind: str) -> None:
        bind = normalize_bind(triggered_bind or "")
        if not bind:
            return
        matched_profile = None
        matched_action = None
        for p in getattr(self._config, "priority_profiles", []) or []:
            if bind == normalize_bind(str(p.get("toggle_bind", "") or "")):
                matched_profile = p
                matched_action = "toggle"
                break
            if bind == normalize_bind(str(p.get("single_fire_bind", "") or "")):
                matched_profile = p
                matched_action = "single_fire"
                break
        if not matched_profile or not matched_action:
            return
        profile_id = str(matched_profile.get("id", "") or "").strip().lower()
        profile_name = str(matched_profile.get("name", "") or "").strip() or "Profile"
        switched = self._config.set_active_priority_profile(profile_id)
        if switched:
            self._window.set_active_priority_profile(profile_id, persist=True)
            self._window.show_status_message(f"Profile: {profile_name}", 1200)
        if matched_action == "single_fire":
            self._key_sender.request_single_fire()
            self._window.show_status_message(f"Single-fire armed ({profile_name})", 1200)
            return
        self._window.toggle_automation()

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def _calibration_form_id(self) -> str:
        return self._settings_dialog.selected_active_form_id()

    def _revert_calibrate_button(self, btn) -> None:
        btn.setText(f"Calibrate Baselines ({self._calibration_form_id()})")
        btn.setStyleSheet("")

    def _calibrate_baselines(self, button_to_update) -> None:
        btn = button_to_update
        target_form = self._calibration_form_id()
        baselines = self._analyzer.get_baselines()
        if target_form == "normal" and baselines:
            reply = QMessageBox.question(
                self._window,
                "Recalibrate all slots?",
                "You already have baselines set. Recalibrate all slots? This will replace existing baselines.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        try:
            cap = ScreenCapture(monitor_index=self._config.monitor_index)
            cap.start()
            frame = cap.grab_region(self._config.bounding_box)
            cap.stop()
            self._analyzer.calibrate_baselines(frame, form_id=target_form)
            logger.info("Baselines calibrated from current frame (form=%s)", target_form)
            self._sync_baselines_to_config()
            self._window.clear_overwritten_baseline_slots()
            btn.setText(f"Calibrated {target_form}")
            btn.setStyleSheet("")
            QTimer.singleShot(2000, lambda: self._revert_calibrate_button(btn))
        except Exception as e:
            logger.error("Calibration failed: %s", e)
            btn.setText("Calibration Failed")
            btn.setStyleSheet("color: red;")
            QTimer.singleShot(2000, lambda: self._revert_calibrate_button(btn))

    def _calibrate_buff_roi_present(self, roi_id: str) -> None:
        rid = str(roi_id or "").strip().lower()
        if not rid:
            return
        rois = [r for r in list(getattr(self._config, "buff_rois", []) or []) if isinstance(r, dict)]
        roi = next(
            (r for r in rois if str(r.get("id", "") or "").strip().lower() == rid),
            None,
        )
        if roi is None:
            self._window.show_status_message(f"Buff ROI not found: {rid}", 2000)
            return
        action = self._config.bounding_box
        roi_left = int(roi.get("left", 0))
        roi_top = int(roi.get("top", 0))
        roi_width = int(roi.get("width", 0))
        roi_height = int(roi.get("height", 0))
        if roi_width <= 1 or roi_height <= 1:
            self._window.show_status_message("Buff ROI size must be > 1x1", 2000)
            return
        try:
            cap = ScreenCapture(monitor_index=self._config.monitor_index)
            cap.start()
            monitor = cap.monitor_info
            mw = int(monitor["width"])
            mh = int(monitor["height"])
            bbox, action_origin = compute_capture_plan(
                action_bbox=action,
                cast_bar_region=getattr(self._config, "cast_bar_region", {}) or {},
                buff_rois=rois,
                monitor_width=mw,
                monitor_height=mh,
            )
            frame = cap.grab_region(bbox)
            cap.stop()
            x1 = int(action_origin[0]) + roi_left
            y1 = int(action_origin[1]) + roi_top
            x2 = x1 + roi_width
            y2 = y1 + roi_height
            if x1 < 0 or y1 < 0 or x2 > frame.shape[1] or y2 > frame.shape[0]:
                self._window.show_status_message("Buff ROI is out of capture frame", 2000)
                return
            crop = frame[y1:y2, x1:x2]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            calibration = roi.get("calibration", {})
            if not isinstance(calibration, dict):
                calibration = {}
            calibration["present_template"] = encode_gray_template(gray)
            calibration["present_template_color"] = encode_color_template(crop)
            roi["calibration"] = calibration
            self._settings_dialog.sync_from_config()
            self._on_config_changed(self._config)
            self._window.show_status_message(
                f"Buff '{roi.get('name', rid)}' present calibrated", 2000
            )
        except Exception as e:
            logger.error("Buff calibration failed: %s", e, exc_info=True)
            self._window.show_status_message(f"Buff calibration failed: {e}", 2000)

    def _calibrate_single_slot(self, slot_index: int) -> None:
        try:
            target_form = self._analyzer.active_form_id()
            cap = ScreenCapture(monitor_index=self._config.monitor_index)
            cap.start()
            frame = cap.grab_region(self._config.bounding_box)
            cap.stop()
            self._analyzer.calibrate_single_slot(frame, slot_index, form_id=target_form)
            self._window.mark_slot_recalibrated(slot_index)
            self._window.show_status_message(
                f"Slot {slot_index + 1} calibrated ({target_form})", 2000
            )
        except Exception as e:
            logger.error("Per-slot calibration failed: %s", e)
            self._window.show_status_message(f"Calibration failed: {e}", 2000)

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        self._settings_dialog.bounding_box_changed.connect(self._overlay.update_bounding_box)
        self._settings_dialog.slot_layout_changed.connect(self._overlay.update_slot_layout)
        self._settings_dialog.overlay_visibility_changed.connect(
            lambda visible: self._overlay.show() if visible else self._overlay.hide()
        )
        self._settings_dialog.monitor_changed.connect(
            lambda idx: self._overlay.update_monitor_geometry(
                monitor_rect_for_index(idx, self._monitors)
            )
        )
        self._settings_dialog.config_updated.connect(self._on_config_changed)
        self._overlay.bbox_geometry_edited.connect(self._settings_dialog.apply_bbox_geometry)
        self._overlay.buff_roi_geometry_edited.connect(
            self._settings_dialog.apply_buff_roi_geometry
        )

        self._window.config_changed.connect(self._on_config_changed)
        self._worker.frame_captured.connect(self._window.update_preview)
        self._worker.state_updated.connect(self._window.update_slot_states)
        self._worker.state_updated.connect(self._overlay.update_slot_states)
        self._worker.form_state_updated.connect(self._window.update_form_state)
        self._worker.form_state_updated.connect(self._overlay.update_form_state)
        self._worker.buff_state_updated.connect(self._window.update_buff_states)
        self._worker.buff_state_updated.connect(self._overlay.update_buff_states)
        self._worker.cast_bar_debug.connect(self._window.update_cast_bar_debug)
        self._worker.key_action.connect(self._on_key_action)

        self._window._btn_start.clicked.connect(self._toggle_capture)
        self._window.start_capture_requested.connect(self._start_capture_if_stopped)
        self._window._btn_settings.clicked.connect(self._settings_dialog.show_or_raise)

    def _wire_calibration(self) -> None:
        self._settings_dialog.calibrate_requested.connect(
            lambda: self._calibrate_baselines(self._settings_dialog._btn_calibrate)
        )
        self._settings_dialog.calibrate_buff_present_requested.connect(
            self._calibrate_buff_roi_present
        )
        self._window.calibrate_slot_requested.connect(self._calibrate_single_slot)

    def _wire_hotkeys(self) -> None:
        self._hotkey_listener = GlobalToggleListener(get_binds=self._all_profile_binds)
        self._hotkey_listener.triggered.connect(self._on_hotkey_triggered)
        self._hotkey_listener.start()

    def _wire_listeners(self) -> None:
        self._queue_listener = QueueListener(get_config=lambda: self._config)
        self._queue_listener.queue_updated.connect(self._window.set_queued_override)
        self._queue_listener.start()
        self._worker.set_queue_listener(self._queue_listener)
        self._window.set_queue_listener(self._queue_listener)

        self._movement_tracker = MovementTracker()
        self._movement_tracker.start()
        self._worker.set_movement_tracker(self._movement_tracker)

        self._modifier_tracker = ShiftModifierTracker()
        self._modifier_tracker.start()
        self._edit_mode_timer = QTimer(self._window)
        self._edit_mode_timer.setInterval(40)
        self._edit_mode_timer.timeout.connect(
            lambda: self._overlay.set_edit_mode_enabled(bool(self._modifier_tracker.is_pressed))
        )
        self._edit_mode_timer.start()

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        exit_code = self._app.exec()
        self._hotkey_listener.stop()
        self._queue_listener.stop()
        self._movement_tracker.stop()
        self._modifier_tracker.stop()
        if self._is_running:
            self._worker.stop()
        sys.exit(exit_code)
