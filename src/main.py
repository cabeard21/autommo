"""Cooldown Reader — Main entry point.

Wires together: screen capture → slot analysis → UI + overlay.
"""
from __future__ import annotations

import base64
import json
import logging
import sys
from pathlib import Path

from PyQt6.QtCore import QRect, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.capture import ScreenCapture
from src.analysis import SlotAnalyzer
from src.models import AppConfig, BoundingBox
from src.overlay import CalibrationOverlay
from src.ui import MainWindow

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

CONFIG_PATH = Path(__file__).parent.parent / "config" / "default_config.json"


class CaptureWorker(QThread):
    """Worker thread that captures frames and analyzes them at the configured FPS."""

    frame_captured = pyqtSignal(np.ndarray)          # Raw frame for preview
    state_updated = pyqtSignal(list)                  # List of slot state dicts

    def __init__(self, analyzer: SlotAnalyzer, config: AppConfig):
        super().__init__()
        self._analyzer = analyzer
        self._config = config
        self._running = False

    def run(self) -> None:
        self._running = True
        self._capture = ScreenCapture(monitor_index=self._config.monitor_index)
        self._capture.start()
        try:
            interval = 1.0 / max(1, self._config.polling_fps)
            logger.info(f"Capture worker started at {self._config.polling_fps} FPS")

            while self._running:
                try:
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
                            "brightness": s.brightness,
                        }
                        for s in state.slots
                    ]
                    self.state_updated.emit(slot_dicts)

                except Exception as e:
                    logger.error(f"Capture error: {e}", exc_info=True)

                self.msleep(int(interval * 1000))
        finally:
            self._capture.stop()

    def stop(self) -> None:
        self._running = False
        self.wait()

    def update_config(self, config: AppConfig) -> None:
        self._config = config
        self._analyzer.update_config(config)


def load_config() -> AppConfig:
    """Load config from JSON, falling back to defaults."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        logger.info(f"Loaded config from {CONFIG_PATH}")
        return AppConfig.from_dict(data)
    logger.warning(f"Config not found at {CONFIG_PATH}, using defaults")
    return AppConfig()


def main() -> None:
    config = load_config()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

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
    # Short-lived mss on main thread for monitor list and overlay setup
    capture = ScreenCapture(monitor_index=config.monitor_index)
    capture.start()
    window.populate_monitors(capture.list_monitors())
    window.show()

    # --- Calibration overlay ---
    # Get the monitor geometry for overlay positioning
    monitors = capture.list_monitors()
    if monitors:
        m = monitors[min(config.monitor_index - 1, len(monitors) - 1)]
        monitor_rect = QRect(m["left"], m["top"], m["width"], m["height"])
    else:
        monitor_rect = QRect(0, 0, 1920, 1080)

    overlay = CalibrationOverlay(monitor_geometry=monitor_rect)
    overlay.update_bounding_box(config.bounding_box)
    if config.overlay_enabled:
        overlay.show()

    capture.stop()

    # --- Capture worker ---
    worker = CaptureWorker(analyzer, config)

    # --- Wire signals ---
    window.bounding_box_changed.connect(overlay.update_bounding_box)
    window.slot_layout_changed.connect(overlay.update_slot_layout)
    window.overlay_visibility_changed.connect(
        lambda visible: overlay.show() if visible else overlay.hide()
    )
    window.config_changed.connect(worker.update_config)
    worker.frame_captured.connect(window.update_preview)
    worker.state_updated.connect(window.update_slot_states)

    # Emit initial slot layout so overlay draws slot outlines
    window.slot_layout_changed.emit(
        config.slot_count, config.slot_gap_pixels, config.slot_padding
    )

    # Start/stop capture via button
    is_running = [False]

    def toggle_capture():
        if is_running[0]:
            worker.stop()
            window._btn_start.setText("Start Capture")
            is_running[0] = False
        else:
            worker.start()
            window._btn_start.setText("Stop Capture")
            is_running[0] = True

    window._btn_start.clicked.connect(toggle_capture)

    # Calibrate baselines: grab one frame on main thread with short-lived mss
    def revert_calibrate_button():
        window._btn_calibrate.setText("Calibrate Baselines")
        window._btn_calibrate.setStyleSheet("")

    def calibrate_baselines():
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
            window.clear_overwritten_baseline_slots()
            window._btn_calibrate.setText("Calibrated ✓")
            window._btn_calibrate.setStyleSheet("")
            QTimer.singleShot(2000, revert_calibrate_button)
        except Exception as e:
            logger.error(f"Calibration failed: {e}")
            window._btn_calibrate.setText("Calibration Failed")
            window._btn_calibrate.setStyleSheet("color: red;")
            QTimer.singleShot(2000, revert_calibrate_button)

    window._btn_calibrate.clicked.connect(calibrate_baselines)

    def calibrate_single_slot(slot_index: int) -> None:
        try:
            cap = ScreenCapture(monitor_index=config.monitor_index)
            cap.start()
            frame = cap.grab_region(config.bounding_box)
            cap.stop()
            analyzer.calibrate_single_slot(frame, slot_index)
            window.mark_slot_recalibrated(slot_index)
            window.statusBar().showMessage(f"Slot {slot_index + 1} calibrated ✓")
            QTimer.singleShot(2000, window.statusBar().clearMessage)
        except Exception as e:
            logger.error(f"Per-slot calibration failed: {e}")
            window.statusBar().showMessage(f"Calibration failed: {e}")
            QTimer.singleShot(2000, window.statusBar().clearMessage)

    window.calibrate_slot_requested.connect(calibrate_single_slot)

    # --- Run ---
    exit_code = app.exec()

    # Cleanup
    if is_running[0]:
        worker.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
