"""Main application window.

Controls:
- Monitor selector
- Bounding box calibration (top/left/width/height spinboxes)
- Live preview of captured region
- Per-slot state visualization
- Start/stop capture
- Calibrate baselines button
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QSlider,
)

import numpy as np

from src.models import AppConfig, BoundingBox

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "default_config.json"


class MainWindow(QMainWindow):
    """Primary control panel for Cooldown Reader."""

    # Emitted when bounding box changes, so overlay can update
    bounding_box_changed = pyqtSignal(BoundingBox)
    config_changed = pyqtSignal(AppConfig)
    # Emitted when slot layout changes (count, gap, padding) for overlay slot outlines
    slot_layout_changed = pyqtSignal(int, int, int)  # slot_count, slot_gap_pixels, slot_padding
    # Emitted when overlay visibility is toggled (True = show, False = hide)
    overlay_visibility_changed = pyqtSignal(bool)

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("Cooldown Reader")
        self.setMinimumSize(760, 400)

        self._build_ui()
        self._connect_signals()
        self._sync_ui_from_config()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- Monitor selector ---
        monitor_group = QGroupBox("Monitor")
        monitor_layout = QHBoxLayout(monitor_group)
        self._monitor_combo = QComboBox()
        monitor_layout.addWidget(QLabel("Monitor:"))
        monitor_layout.addWidget(self._monitor_combo)
        layout.addWidget(monitor_group)

        # --- Bounding box calibration ---
        bbox_group = QGroupBox("Capture Region (pixels relative to monitor)")
        bbox_layout = QHBoxLayout(bbox_group)

        self._spin_top = QSpinBox()
        self._spin_left = QSpinBox()
        self._spin_width = QSpinBox()
        self._spin_height = QSpinBox()

        for spin, label, max_val in [
            (self._spin_top, "Top:", 4000),
            (self._spin_left, "Left:", 8000),
            (self._spin_width, "Width:", 2000),
            (self._spin_height, "Height:", 500),
        ]:
            spin.setRange(0, max_val)
            spin.setSingleStep(1)
            bbox_layout.addWidget(QLabel(label))
            bbox_layout.addWidget(spin)

        self._check_overlay = QCheckBox("Show overlay")
        bbox_layout.addWidget(self._check_overlay)

        layout.addWidget(bbox_group)

        # --- Detection settings ---
        detect_group = QGroupBox("Detection")
        detect_layout = QHBoxLayout(detect_group)

        detect_layout.addWidget(QLabel("Slots:"))
        self._spin_slots = QSpinBox()
        self._spin_slots.setRange(1, 24)
        detect_layout.addWidget(self._spin_slots)

        detect_layout.addWidget(QLabel("Gap:"))
        self._spin_gap = QSpinBox()
        self._spin_gap.setRange(0, 20)
        self._spin_gap.setSuffix(" px")
        detect_layout.addWidget(self._spin_gap)

        detect_layout.addWidget(QLabel("Padding:"))
        self._spin_padding = QSpinBox()
        self._spin_padding.setRange(0, 20)
        self._spin_padding.setSuffix(" px")
        detect_layout.addWidget(self._spin_padding)

        detect_layout.addWidget(QLabel("Brightness drop:"))
        self._spin_brightness_drop = QSpinBox()
        self._spin_brightness_drop.setRange(0, 255)
        detect_layout.addWidget(self._spin_brightness_drop)

        detect_layout.addWidget(QLabel("CD fraction:"))
        self._slider_pixel_fraction = QSlider(Qt.Orientation.Horizontal)
        self._slider_pixel_fraction.setRange(10, 90)  # 0.10 to 0.90
        self._slider_pixel_fraction.setSingleStep(5)
        self._pixel_fraction_label = QLabel("0.30")
        detect_layout.addWidget(self._slider_pixel_fraction)
        detect_layout.addWidget(self._pixel_fraction_label)

        layout.addWidget(detect_group)

        # --- Controls ---
        controls_layout = QHBoxLayout()
        self._btn_start = QPushButton("Start Capture")
        self._btn_calibrate = QPushButton("Calibrate Baselines")
        self._btn_save_config = QPushButton("Save Config")
        controls_layout.addWidget(self._btn_start)
        controls_layout.addWidget(self._btn_calibrate)
        controls_layout.addWidget(self._btn_save_config)
        layout.addLayout(controls_layout)

        # --- Live preview ---
        preview_group = QGroupBox("Live Preview")
        preview_layout = QVBoxLayout(preview_group)
        self._preview_label = QLabel("No capture running")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumHeight(80)
        self._preview_label.setStyleSheet("background-color: #1a1a1a; color: #666;")
        preview_layout.addWidget(self._preview_label)
        layout.addWidget(preview_group)

        # --- Slot state display ---
        state_group = QGroupBox("Slot States")
        self._state_layout = QHBoxLayout(state_group)
        self._slot_labels: list[QLabel] = []
        layout.addWidget(state_group)

    def _connect_signals(self) -> None:
        """Wire up UI controls to config updates."""
        self._spin_top.valueChanged.connect(self._on_bbox_changed)
        self._spin_left.valueChanged.connect(self._on_bbox_changed)
        self._spin_width.valueChanged.connect(self._on_bbox_changed)
        self._spin_height.valueChanged.connect(self._on_bbox_changed)
        self._spin_slots.valueChanged.connect(self._on_slot_layout_changed)
        self._spin_gap.valueChanged.connect(self._on_slot_layout_changed)
        self._spin_padding.valueChanged.connect(self._on_slot_layout_changed)
        self._check_overlay.toggled.connect(self._on_overlay_toggled)
        self._spin_brightness_drop.valueChanged.connect(self._on_detection_changed)
        self._slider_pixel_fraction.valueChanged.connect(self._on_detection_changed)
        self._btn_save_config.clicked.connect(self._save_config)

    def _sync_ui_from_config(self) -> None:
        """Set UI controls to match current config."""
        bb = self._config.bounding_box
        self._spin_top.setValue(bb.top)
        self._spin_left.setValue(bb.left)
        self._spin_width.setValue(bb.width)
        self._spin_height.setValue(bb.height)
        # Block signals so slot spinbox setValue doesn't overwrite config before all are set
        self._spin_slots.blockSignals(True)
        self._spin_gap.blockSignals(True)
        self._spin_padding.blockSignals(True)
        try:
            self._spin_slots.setValue(self._config.slot_count)
            self._spin_gap.setValue(self._config.slot_gap_pixels)
            self._spin_padding.setValue(self._config.slot_padding)
        finally:
            self._spin_slots.blockSignals(False)
            self._spin_gap.blockSignals(False)
            self._spin_padding.blockSignals(False)
        self._check_overlay.setChecked(self._config.overlay_enabled)
        self._spin_brightness_drop.blockSignals(True)
        self._slider_pixel_fraction.blockSignals(True)
        try:
            self._spin_brightness_drop.setValue(self._config.brightness_drop_threshold)
            self._slider_pixel_fraction.setValue(int(self._config.cooldown_pixel_fraction * 100))
            self._pixel_fraction_label.setText(f"{self._config.cooldown_pixel_fraction:.2f}")
        finally:
            self._spin_brightness_drop.blockSignals(False)
            self._slider_pixel_fraction.blockSignals(False)

    def _on_bbox_changed(self) -> None:
        self._config.bounding_box = BoundingBox(
            top=self._spin_top.value(),
            left=self._spin_left.value(),
            width=self._spin_width.value(),
            height=self._spin_height.value(),
        )
        self.bounding_box_changed.emit(self._config.bounding_box)

    def _on_detection_changed(self) -> None:
        self._config.brightness_drop_threshold = self._spin_brightness_drop.value()
        self._config.cooldown_pixel_fraction = self._slider_pixel_fraction.value() / 100.0
        self._pixel_fraction_label.setText(f"{self._config.cooldown_pixel_fraction:.2f}")
        self.config_changed.emit(self._config)

    def _on_overlay_toggled(self, checked: bool) -> None:
        self._config.overlay_enabled = checked
        self.overlay_visibility_changed.emit(checked)

    def _on_slot_layout_changed(self) -> None:
        self._config.slot_count = self._spin_slots.value()
        self._config.slot_gap_pixels = self._spin_gap.value()
        self._config.slot_padding = self._spin_padding.value()
        self.config_changed.emit(self._config)
        self.slot_layout_changed.emit(
            self._config.slot_count,
            self._config.slot_gap_pixels,
            self._config.slot_padding,
        )

    def update_preview(self, frame: np.ndarray) -> None:
        """Update the live preview with a captured frame (BGR numpy array)."""
        h, w, ch = frame.shape
        bytes_per_line = ch * w
        # Convert BGR to RGB for Qt
        rgb = frame[:, :, ::-1].copy()
        qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg).scaledToWidth(
            self._preview_label.width(), Qt.TransformationMode.SmoothTransformation
        )
        self._preview_label.setPixmap(pixmap)

    def update_slot_states(self, states: list[dict]) -> None:
        """Update the slot state indicators.

        Args:
            states: List of dicts with keys: index, state, keybind, cooldown_remaining
        """
        # Rebuild labels if slot count changed
        if len(self._slot_labels) != len(states):
            for lbl in self._slot_labels:
                lbl.deleteLater()
            self._slot_labels.clear()
            for _ in states:
                lbl = QLabel()
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setMinimumWidth(52)
                lbl.setStyleSheet("border: 1px solid #444; padding: 4px;")
                self._state_layout.addWidget(lbl)
                self._slot_labels.append(lbl)

        for lbl, s in zip(self._slot_labels, states):
            keybind = s.get("keybind", "?")
            state = s.get("state", "unknown")
            cd = s.get("cooldown_remaining")

            text = f"[{keybind}]"
            if cd is not None:
                text += f"\n{cd:.1f}s"

            color = {
                "ready": "#2d5a2d",
                "on_cooldown": "#5a2d2d",
                "gcd": "#5a5a2d",
                "unknown": "#333333",
            }.get(state, "#333333")

            lbl.setText(text)
            lbl.setStyleSheet(
                f"background-color: {color}; color: white; border: 1px solid #444; padding: 4px;"
            )

    def _save_config(self) -> None:
        """Persist current config to JSON and show Saved ✓ feedback."""
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump(self._config.to_dict(), f, indent=2)
            logger.info(f"Config saved to {CONFIG_PATH}")
            self._btn_save_config.setText("Saved ✓")
            QTimer.singleShot(2000, self._revert_save_config_button)
        except Exception as e:
            logger.error(f"Config save failed: {e}")
            self._btn_save_config.setText("Save failed")
            self._btn_save_config.setStyleSheet("color: red;")
            QTimer.singleShot(2000, self._revert_save_config_button)

    def _revert_save_config_button(self) -> None:
        self._btn_save_config.setText("Save Config")
        self._btn_save_config.setStyleSheet("")

    def populate_monitors(self, monitors: list[dict]) -> None:
        """Fill the monitor dropdown with available monitors."""
        self._monitor_combo.clear()
        for i, m in enumerate(monitors):
            self._monitor_combo.addItem(
                f"Monitor {i + 1}: {m['width']}x{m['height']}", i + 1
            )
