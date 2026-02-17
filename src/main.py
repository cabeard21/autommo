"""Cooldown Reader — Main entry point.

Wires together: screen capture → slot analysis → UI + overlay.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
from pathlib import Path

from PyQt6.QtCore import QRect, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.automation.global_hotkey import GlobalToggleListener
from src.automation.key_sender import KeySender
from src.capture import ScreenCapture
from src.analysis import SlotAnalyzer
from src.models import AppConfig, BoundingBox
from src.overlay import CalibrationOverlay
from src.ui import MainWindow
from src.ui.settings_dialog import SettingsDialog

import numpy as np


def encode_baselines(baselines: dict[int, np.ndarray]) -> list[dict]:
    """Encode baselines for JSON: list of {shape: [h, w], data: base64} in slot order."""
    return [
        {"shape": list(ary.shape), "data": base64.b64encode(ary.tobytes()).decode()}
        for i in sorted(baselines.keys())
        for ary in [baselines[i]]
    ]


def decode_baselines(data: list[dict]) -> dict[int, np.ndarray]:
    """Decode baselines from config (list of {shape, data})."""
    result = {}
    for i, d in enumerate(data):
        shape = d.get("shape")
        b64 = d.get("data")
        if shape and b64:
            arr = np.frombuffer(base64.b64decode(b64), dtype=np.uint8)
            result[i] = arr.reshape(shape).copy()
    return result


logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parent
# When frozen (e.g. PyInstaller), bundle root is sys._MEIPASS; include cocktus.ico via --add-data
_BASE_PATH = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"
ICON_PATH = _BASE_PATH / "cocktus.ico"


class CaptureWorker(QThread):
    """Worker thread that captures frames and analyzes them at the configured FPS."""

    frame_captured = pyqtSignal(np.ndarray)  # Raw frame for preview
    state_updated = pyqtSignal(list)  # List of slot state dicts
    key_action = pyqtSignal(
        object
    )  # Dict when a key was sent or blocked (action, keybind, etc.)

    def __init__(self, analyzer: SlotAnalyzer, config: AppConfig, key_sender=None):
        super().__init__()
        self._analyzer = analyzer
        self._config = config
        self._key_sender = key_sender
        self._running = False
        self._capture: ScreenCapture | None = None
        self._active_monitor_index: int | None = None

    def _start_capture(self, monitor_index: int) -> None:
        self._capture = ScreenCapture(monitor_index=monitor_index)
        self._capture.start()
        self._active_monitor_index = monitor_index

    def _restart_capture(self, monitor_index: int) -> None:
        if self._capture is not None:
            self._capture.stop()
        self._start_capture(monitor_index)
        logger.info(f"Capture worker switched to monitor {monitor_index}")

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
                    frame = self._capture.grab_region(self._config.bounding_box)
                    self.frame_captured.emit(frame)

                    state = self._analyzer.analyze_frame(frame)
                    slot_dicts = [
                        {
                            "index": s.index,
                            "state": s.state.value,
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
                            "brightness": s.brightness,
                        }
                        for s in state.slots
                    ]
                    self.state_updated.emit(slot_dicts)
                    if self._key_sender is not None:
                        result = self._key_sender.evaluate_and_send(
                            state,
                            self._config.active_priority_order(),
                            self._config.keybinds,
                            getattr(self._config, "automation_enabled", False),
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


def load_config() -> AppConfig:
    """Load config from JSON, falling back to defaults."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        logger.info(f"Loaded config from {CONFIG_PATH}")
        return AppConfig.from_dict(data)
    logger.warning(f"Config not found at {CONFIG_PATH}, using defaults")
    return AppConfig()


def monitor_rect_for_index(monitor_index: int, monitors: list[dict]) -> QRect:
    """Resolve a monitor index (1-based) to a QRect, with safe fallback."""
    if monitors:
        idx = min(max(1, monitor_index), len(monitors)) - 1
        m = monitors[idx]
        return QRect(m["left"], m["top"], m["width"], m["height"])
    return QRect(0, 0, 1920, 1080)


def main() -> None:
    config = load_config()
    config.automation_enabled = False

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    # --- Initialize components ---
    analyzer = SlotAnalyzer(config)
    if config.slot_baselines:
        try:
            decoded = decode_baselines(config.slot_baselines)
            if decoded:
                analyzer.set_baselines(decoded)
        except Exception as e:
            logger.warning(f"Could not load saved baselines: {e}")

    # --- Main window ---
    window = MainWindow(config)

    def sync_baselines_to_config() -> None:
        config.slot_baselines = encode_baselines(analyzer.get_baselines())

    window.set_before_save_callback(sync_baselines_to_config)

    # --- Settings dialog (single instance, non-modal; close = hide) ---
    settings_dialog = SettingsDialog(config, before_save_callback=sync_baselines_to_config, parent=window)

    # Short-lived mss on main thread for monitor list and overlay setup
    capture = ScreenCapture(monitor_index=config.monitor_index)
    capture.start()
    monitors = capture.list_monitors()
    settings_dialog.populate_monitors(monitors)
    if getattr(config, "always_on_top", False):
        window.setWindowFlags(window.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
    window.show()

    # --- Calibration overlay ---
    # Get the monitor geometry for overlay positioning
    monitors = capture.list_monitors()
    monitor_rect = monitor_rect_for_index(config.monitor_index, monitors)

    overlay = CalibrationOverlay(monitor_geometry=monitor_rect)
    overlay.update_bounding_box(config.bounding_box)
    if config.overlay_enabled:
        overlay.show()

    capture.stop()

    # --- Key sender and capture worker ---
    key_sender = KeySender(config)
    worker = CaptureWorker(analyzer, config, key_sender)
    window.set_key_sender(key_sender)

    def on_config_changed(new_config: AppConfig) -> None:
        worker.update_config(new_config)
        key_sender.update_config(new_config)
        window.refresh_from_config()
        # Apply always-on-top to main window when changed from Settings
        flags = window.windowFlags()
        if getattr(new_config, "always_on_top", False):
            window.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        else:
            window.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        window.show()

    # --- Wire signals: only Settings dialog drives overlay/bbox/slots (main window no longer has those controls) ---
    def apply_overlay_visibility(visible: bool) -> None:
        overlay.show() if visible else overlay.hide()

    def apply_monitor(monitor_index: int) -> None:
        overlay.update_monitor_geometry(monitor_rect_for_index(monitor_index, monitors))

    settings_dialog.bounding_box_changed.connect(overlay.update_bounding_box)
    settings_dialog.slot_layout_changed.connect(overlay.update_slot_layout)
    settings_dialog.overlay_visibility_changed.connect(apply_overlay_visibility)
    settings_dialog.monitor_changed.connect(apply_monitor)
    settings_dialog.config_updated.connect(on_config_changed)

    window.config_changed.connect(on_config_changed)
    worker.frame_captured.connect(window.update_preview)
    worker.state_updated.connect(window.update_slot_states)

    def on_key_action(result: dict) -> None:
        slot_index = result.get("slot_index")
        names = getattr(config, "slot_display_names", [])
        display_name = "Unidentified"
        if (
            slot_index is not None
            and slot_index < len(names)
            and (names[slot_index] or "").strip()
        ):
            display_name = (names[slot_index] or "").strip()
        if result.get("action") == "sent":
            window.record_last_action_sent(
                result["keybind"], result.get("timestamp", 0.0), display_name
            )
        elif result.get("action") == "blocked" and result.get("reason") == "window":
            window.set_next_intention_blocked(result["keybind"], display_name)
        elif result.get("action") == "blocked" and result.get("reason") == "casting":
            window.set_next_intention_casting_wait(
                slot_index=result.get("slot_index"),
                cast_ends_at=result.get("cast_ends_at"),
            )

    worker.key_action.connect(on_key_action)

    # Emit initial slot layout so overlay draws slot outlines (from config; no window control)
    overlay.update_slot_layout(config.slot_count, config.slot_gap_pixels, config.slot_padding)

    # Start/stop capture via button
    is_running = [False]

    def toggle_capture():
        if is_running[0]:
            worker.stop()
            window._btn_start.setText("▶ Start Capture")
            window.set_capture_running(False)
            is_running[0] = False
        else:
            worker.start()
            window._btn_start.setText("⏹ Stop Capture")
            window.set_capture_running(True)
            is_running[0] = True

    window._btn_start.clicked.connect(toggle_capture)

    def on_start_capture_requested():
        if not is_running[0]:
            worker.start()
            window._btn_start.setText("⏹ Stop Capture")
            window.set_capture_running(True)
            is_running[0] = True

    window.start_capture_requested.connect(on_start_capture_requested)

    # Global hotkey action (works when app does not have focus)
    def all_profile_binds() -> list[str]:
        binds: list[str] = []
        for p in getattr(config, "priority_profiles", []) or []:
            toggle_bind = str(p.get("toggle_bind", "") or "").strip().lower()
            single_fire_bind = str(p.get("single_fire_bind", "") or "").strip().lower()
            if toggle_bind:
                binds.append(toggle_bind)
            if single_fire_bind:
                binds.append(single_fire_bind)
        return binds

    def on_hotkey_triggered(triggered_bind: str):
        bind = (triggered_bind or "").strip().lower()
        if not bind:
            return
        matched_profile = None
        matched_action = None
        for p in getattr(config, "priority_profiles", []) or []:
            if bind == str(p.get("toggle_bind", "") or "").strip().lower():
                matched_profile = p
                matched_action = "toggle"
                break
            if bind == str(p.get("single_fire_bind", "") or "").strip().lower():
                matched_profile = p
                matched_action = "single_fire"
                break
        if not matched_profile or not matched_action:
            return
        profile_id = str(matched_profile.get("id", "") or "").strip().lower()
        profile_name = str(matched_profile.get("name", "") or "").strip() or "Profile"
        switched = config.set_active_priority_profile(profile_id)
        if switched:
            window.set_active_priority_profile(profile_id, persist=True)
            window.show_status_message(f"Profile: {profile_name}", 1200)
        if matched_action == "single_fire":
            key_sender.request_single_fire()
            window.show_status_message(f"Single-fire armed ({profile_name})", 1200)
            return
        window.toggle_automation()

    hotkey_listener = GlobalToggleListener(get_binds=all_profile_binds)
    hotkey_listener.triggered.connect(on_hotkey_triggered)
    hotkey_listener.start()

    # Calibrate baselines: grab one frame on main thread with short-lived mss
    def revert_calibrate_button(btn):
        btn.setText("Calibrate All Baselines")
        btn.setStyleSheet("")

    def calibrate_baselines(button_to_update):
        btn = button_to_update
        baselines = analyzer.get_baselines()
        if baselines:
            reply = QMessageBox.question(
                window,
                "Recalibrate all slots?",
                "You already have baselines set. Recalibrate all slots? This will replace existing baselines.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        try:
            cap = ScreenCapture(monitor_index=config.monitor_index)
            cap.start()
            frame = cap.grab_region(config.bounding_box)
            cap.stop()
            analyzer.calibrate_baselines(frame)
            logger.info("Baselines calibrated from current frame")
            sync_baselines_to_config()  # Update config in memory so baselines are not lost when switching windows
            window.clear_overwritten_baseline_slots()
            btn.setText("Calibrated ✓")
            btn.setStyleSheet("")
            QTimer.singleShot(2000, lambda: revert_calibrate_button(btn))
        except Exception as e:
            logger.error(f"Calibration failed: {e}")
            btn.setText("Calibration Failed")
            btn.setStyleSheet("color: red;")
            QTimer.singleShot(2000, lambda: revert_calibrate_button(btn))

    settings_dialog.calibrate_requested.connect(lambda: calibrate_baselines(settings_dialog._btn_calibrate))

    def calibrate_single_slot(slot_index: int) -> None:
        try:
            cap = ScreenCapture(monitor_index=config.monitor_index)
            cap.start()
            frame = cap.grab_region(config.bounding_box)
            cap.stop()
            analyzer.calibrate_single_slot(frame, slot_index)
            window.mark_slot_recalibrated(slot_index)
            window.show_status_message(f"Slot {slot_index + 1} calibrated ✓", 2000)
        except Exception as e:
            logger.error(f"Per-slot calibration failed: {e}")
            window.show_status_message(f"Calibration failed: {e}", 2000)

    window.calibrate_slot_requested.connect(calibrate_single_slot)

    # Settings button opens or raises the settings dialog
    window._btn_settings.clicked.connect(settings_dialog.show_or_raise)

    # --- Run ---
    exit_code = app.exec()

    # Cleanup
    hotkey_listener.stop()
    if is_running[0]:
        worker.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
