"""Settings window — non-modal dialog for all configuration (Profile, Display, Capture, Detection, Automation, Calibration)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.models import AppConfig, BoundingBox
from src.automation.global_hotkey import CaptureOneKeyThread, format_bind_for_display

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"

LABEL_MIN_WIDTH = 70
SECTION_GAP = 10


def _label_style() -> str:
    return "font-size: 11px; color: #999; min-width: 70px;"


def _section_title_style() -> str:
    return "font-family: monospace; font-size: 10px; color: #666; font-weight: bold;"


def _row_label(text: str) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(_label_style())
    l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    l.setMinimumWidth(LABEL_MIN_WIDTH)
    return l


class SettingsDialog(QDialog):
    """Non-modal settings dialog. Emits config_updated when any value changes. Auto-saves after changes and shows last auto-save time."""

    config_updated = pyqtSignal(object)
    calibrate_requested = pyqtSignal()
    bounding_box_changed = pyqtSignal(object)
    slot_layout_changed = pyqtSignal(int, int, int)
    overlay_visibility_changed = pyqtSignal(bool)
    monitor_changed = pyqtSignal(int)

    def __init__(
        self,
        config: AppConfig,
        before_save_callback: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._before_save_callback = before_save_callback
        self._monitors: list[dict] = []
        self._capture_bind_thread: Optional[CaptureOneKeyThread] = None
        self._last_auto_saved: Optional[datetime] = None
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.timeout.connect(self._do_auto_save)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)
        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(SECTION_GAP)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setStyleSheet("QScrollArea { background: transparent; }")
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = QWidget()
        self._scroll_content = content
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(SECTION_GAP)

        content_layout.addWidget(self._profile_section())
        content_layout.addWidget(self._display_section())
        content_layout.addWidget(self._capture_section())
        content_layout.addWidget(self._detection_section())
        content_layout.addWidget(self._automation_section())
        content_layout.addWidget(self._spell_queue_section())
        content_layout.addWidget(self._calibration_section())
        content_layout.addStretch()
        self._scroll_area.setWidget(content)
        layout.addWidget(self._scroll_area)
        layout.addLayout(self._status_row())

    def _profile_section(self) -> QGroupBox:
        g = QGroupBox("Profile")
        g.setStyleSheet("QGroupBox { font-weight: bold; }")
        fl = QFormLayout(g)
        fl.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._edit_profile_name = QLineEdit()
        self._edit_profile_name.setPlaceholderText("e.g. Default")
        self._edit_profile_name.setClearButtonEnabled(True)
        fl.addRow(_row_label("Name:"), self._edit_profile_name)
        btn_row = QHBoxLayout()
        self._btn_export = QPushButton("Export")
        self._btn_import = QPushButton("Import")
        btn_row.addWidget(self._btn_export)
        btn_row.addWidget(self._btn_import)
        fl.addRow("", btn_row)
        return g

    def _display_section(self) -> QGroupBox:
        g = QGroupBox("Display")
        g.setStyleSheet("QGroupBox { font-weight: bold; }")
        fl = QFormLayout(g)
        self._monitor_combo = QComboBox()
        self._monitor_combo.setMinimumWidth(180)
        fl.addRow(_row_label("Monitor:"), self._monitor_combo)
        cb_row = QHBoxLayout()
        self._check_overlay = QCheckBox("Show Region Overlay")
        self._check_always_on_top = QCheckBox("Always on Top")
        cb_row.addWidget(self._check_overlay)
        cb_row.addWidget(self._check_always_on_top)
        fl.addRow("", cb_row)
        history_row = QHBoxLayout()
        self._spin_history_rows = QSpinBox()
        self._spin_history_rows.setRange(1, 10)
        self._spin_history_rows.setValue(3)
        self._spin_history_rows.setMaximumWidth(48)
        history_row.addWidget(self._spin_history_rows)
        history_row.addWidget(QLabel("(Last Action / Next Intention)"))
        help_l = history_row.itemAt(1).widget()
        help_l.setStyleSheet("font-size: 10px; color: #666;")
        fl.addRow(_row_label("History rows:"), history_row)
        return g

    def _capture_section(self) -> QGroupBox:
        g = QGroupBox("Capture Region")
        g.setStyleSheet("QGroupBox { font-weight: bold; }")
        grid = QGridLayout(g)
        self._spin_top = QSpinBox()
        self._spin_left = QSpinBox()
        self._spin_width = QSpinBox()
        self._spin_height = QSpinBox()
        for spin, max_val in [(self._spin_top, 4000), (self._spin_left, 8000), (self._spin_width, 2000), (self._spin_height, 500)]:
            spin.setRange(0, max_val)
        grid.addWidget(_row_label("Top:"), 0, 0)
        grid.addWidget(self._spin_top, 0, 1)
        grid.addWidget(_row_label("Left:"), 0, 2)
        grid.addWidget(self._spin_left, 0, 3)
        grid.addWidget(_row_label("Width:"), 1, 0)
        grid.addWidget(self._spin_width, 1, 1)
        grid.addWidget(_row_label("Height:"), 1, 2)
        grid.addWidget(self._spin_height, 1, 3)
        row2 = QHBoxLayout()
        row2.addWidget(_row_label("Slots:"))
        self._spin_slots = QSpinBox()
        self._spin_slots.setRange(1, 24)
        row2.addWidget(self._spin_slots)
        row2.addWidget(QLabel("Gap:"))
        self._spin_gap = QSpinBox()
        self._spin_gap.setRange(0, 20)
        self._spin_gap.setSuffix(" px")
        row2.addWidget(self._spin_gap)
        row2.addWidget(QLabel("Padding:"))
        self._spin_padding = QSpinBox()
        self._spin_padding.setRange(0, 20)
        self._spin_padding.setSuffix(" px")
        row2.addWidget(self._spin_padding)
        row2.addStretch()
        grid.addLayout(row2, 2, 0, 1, 4)
        return g

    def _detection_section(self) -> QGroupBox:
        g = QGroupBox("Detection")
        g.setStyleSheet("QGroupBox { font-weight: bold; }")
        fl = QFormLayout(g)
        self._spin_brightness_drop = QSpinBox()
        self._spin_brightness_drop.setRange(0, 255)
        self._spin_brightness_drop.setMaximumWidth(48)
        darken_help = QLabel("(?)")
        darken_help.setToolTip(
            "Pixels must darken by this many brightness levels (0-255) to count as on cooldown."
        )
        darken_help.setStyleSheet("color: #666; font-size: 11px;")
        darken_row = QHBoxLayout()
        darken_row.addWidget(self._spin_brightness_drop)
        darken_row.addWidget(darken_help)
        fl.addRow(_row_label("Darken:"), darken_row)
        self._slider_pixel_fraction = QSlider(Qt.Orientation.Horizontal)
        self._slider_pixel_fraction.setRange(10, 90)
        self._slider_pixel_fraction.setSingleStep(5)
        self._pixel_fraction_label = QLabel("0.30")
        self._pixel_fraction_label.setMinimumWidth(32)
        self._pixel_fraction_label.setStyleSheet("font-family: monospace; font-size: 11px;")
        trigger_help = QLabel("(?)")
        trigger_help.setToolTip(
            "Fraction of pixels that must be darkened to trigger cooldown detection."
        )
        trigger_help.setStyleSheet("color: #666; font-size: 11px;")
        trigger_row = QHBoxLayout()
        trigger_row.addWidget(self._slider_pixel_fraction)
        trigger_row.addWidget(self._pixel_fraction_label)
        trigger_row.addWidget(trigger_help)
        fl.addRow(_row_label("Trigger:"), trigger_row)
        return g

    def _automation_section(self) -> QGroupBox:
        g = QGroupBox("Automation")
        g.setStyleSheet("QGroupBox { font-weight: bold; }")
        fl = QFormLayout(g)
        self._btn_rebind = QPushButton("F24")
        self._btn_rebind.setStyleSheet("font-family: monospace;")
        self._btn_rebind.setMinimumWidth(56)
        rebind_help = QLabel("click to rebind")
        rebind_help.setStyleSheet("font-size: 10px; color: #666;")
        rebind_row = QHBoxLayout()
        rebind_row.addWidget(self._btn_rebind)
        rebind_row.addWidget(rebind_help)
        fl.addRow(_row_label("Hotkey bind:"), rebind_row)
        self._combo_hotkey_mode = QComboBox()
        self._combo_hotkey_mode.addItem("Toggle automation", "toggle")
        self._combo_hotkey_mode.addItem("Single fire next action", "single_fire")
        fl.addRow(_row_label("Hotkey mode:"), self._combo_hotkey_mode)
        self._spin_min_delay = QSpinBox()
        self._spin_min_delay.setRange(50, 2000)
        self._spin_min_delay.setMaximumWidth(56)
        fl.addRow(_row_label("Delay (ms):"), self._spin_min_delay)
        self._edit_window_title = QLineEdit()
        self._edit_window_title.setPlaceholderText("e.g. World of Warcraft")
        self._edit_window_title.setClearButtonEnabled(True)
        fl.addRow(_row_label("Window:"), self._edit_window_title)
        return g

    def _spell_queue_section(self) -> QGroupBox:
        g = QGroupBox("Spell Queue")
        g.setStyleSheet("QGroupBox { font-weight: bold; }")
        fl = QFormLayout(g)
        self._edit_queue_keys = QLineEdit()
        self._edit_queue_keys.setPlaceholderText("e.g. R, T, V")
        self._edit_queue_keys.setClearButtonEnabled(True)
        fl.addRow(_row_label("Queue keys:"), self._edit_queue_keys)
        queue_help = QLabel(
            "Manual presses of these keys (or bound keys not in priority) will queue to fire at next GCD"
        )
        queue_help.setStyleSheet("font-size: 10px; color: #666;")
        queue_help.setWordWrap(True)
        fl.addRow("", queue_help)
        self._spin_queue_timeout = QSpinBox()
        self._spin_queue_timeout.setRange(1000, 30000)
        self._spin_queue_timeout.setSuffix(" ms")
        self._spin_queue_timeout.setMaximumWidth(80)
        fl.addRow(_row_label("Queue timeout:"), self._spin_queue_timeout)
        self._spin_queue_fire_delay = QSpinBox()
        self._spin_queue_fire_delay.setRange(0, 300)
        self._spin_queue_fire_delay.setSuffix(" ms")
        self._spin_queue_fire_delay.setMaximumWidth(80)
        self._spin_queue_fire_delay.setToolTip("Delay after GCD ready before sending queued key (avoids firing too early)")
        fl.addRow(_row_label("Fire delay:"), self._spin_queue_fire_delay)
        return g

    def _calibration_section(self) -> QGroupBox:
        g = QGroupBox("Calibration")
        g.setStyleSheet("QGroupBox { font-weight: bold; }")
        layout = QVBoxLayout(g)
        self._btn_calibrate = QPushButton("Calibrate All Baselines")
        self._btn_calibrate.setStyleSheet(
            "background-color: #2d5a2d; color: #88ff88; border: 1px solid #3a7a3a;"
        )
        layout.addWidget(self._btn_calibrate)
        tip = QLabel("Tip: individual slots can be calibrated via right-click on Slot States")
        tip.setStyleSheet("font-family: monospace; font-size: 10px; color: #555;")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        return g

    def _status_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._auto_save_status_label = QLabel("Last auto-saved: —")
        self._auto_save_status_label.setStyleSheet("font-size: 10px; color: #666;")
        row.addWidget(self._auto_save_status_label)
        row.addStretch()
        return row

    def _connect_signals(self) -> None:
        self._edit_profile_name.textChanged.connect(self._on_profile_changed)
        self._btn_export.clicked.connect(self._on_export)
        self._btn_import.clicked.connect(self._on_import)
        self._monitor_combo.currentIndexChanged.connect(self._on_monitor_changed)
        self._check_overlay.toggled.connect(self._on_overlay_changed)
        self._check_always_on_top.toggled.connect(self._on_always_on_top_changed)
        self._spin_history_rows.valueChanged.connect(self._on_history_rows_changed)
        self._spin_top.valueChanged.connect(self._on_bbox_changed)
        self._spin_left.valueChanged.connect(self._on_bbox_changed)
        self._spin_width.valueChanged.connect(self._on_bbox_changed)
        self._spin_height.valueChanged.connect(self._on_bbox_changed)
        self._spin_slots.valueChanged.connect(self._on_slot_layout_changed)
        self._spin_gap.valueChanged.connect(self._on_slot_layout_changed)
        self._spin_padding.valueChanged.connect(self._on_slot_layout_changed)
        self._spin_brightness_drop.valueChanged.connect(self._on_detection_changed)
        self._slider_pixel_fraction.valueChanged.connect(self._on_detection_changed)
        self._btn_rebind.clicked.connect(self._on_rebind_clicked)
        self._combo_hotkey_mode.currentIndexChanged.connect(self._on_hotkey_mode_changed)
        self._spin_min_delay.valueChanged.connect(self._on_min_delay_changed)
        self._edit_window_title.textChanged.connect(self._on_window_title_changed)
        self._edit_queue_keys.textChanged.connect(self._on_queue_keys_changed)
        self._spin_queue_timeout.valueChanged.connect(self._on_queue_timeout_changed)
        self._spin_queue_fire_delay.valueChanged.connect(self._on_queue_fire_delay_changed)
        self._btn_calibrate.clicked.connect(self._on_calibrate_clicked)

    def sync_from_config(self) -> None:
        """Populate all controls from current config."""
        self._edit_profile_name.blockSignals(True)
        self._edit_profile_name.setText(getattr(self._config, "profile_name", "") or "")
        self._edit_profile_name.blockSignals(False)
        self._check_overlay.blockSignals(True)
        self._check_overlay.setChecked(self._config.overlay_enabled)
        self._check_overlay.blockSignals(False)
        self._check_always_on_top.blockSignals(True)
        self._check_always_on_top.setChecked(getattr(self._config, "always_on_top", False))
        self._check_always_on_top.blockSignals(False)
        self._spin_history_rows.blockSignals(True)
        self._spin_history_rows.setValue(getattr(self._config, "history_rows", 3))
        self._spin_history_rows.blockSignals(False)
        bb = self._config.bounding_box
        self._spin_top.blockSignals(True)
        self._spin_left.blockSignals(True)
        self._spin_width.blockSignals(True)
        self._spin_height.blockSignals(True)
        self._spin_top.setValue(bb.top)
        self._spin_left.setValue(bb.left)
        self._spin_width.setValue(bb.width)
        self._spin_height.setValue(bb.height)
        self._spin_top.blockSignals(False)
        self._spin_left.blockSignals(False)
        self._spin_width.blockSignals(False)
        self._spin_height.blockSignals(False)
        self._spin_slots.blockSignals(True)
        self._spin_gap.blockSignals(True)
        self._spin_padding.blockSignals(True)
        self._spin_slots.setValue(self._config.slot_count)
        self._spin_gap.setValue(self._config.slot_gap_pixels)
        self._spin_padding.setValue(self._config.slot_padding)
        self._spin_slots.blockSignals(False)
        self._spin_gap.blockSignals(False)
        self._spin_padding.blockSignals(False)
        self._spin_brightness_drop.blockSignals(True)
        self._slider_pixel_fraction.blockSignals(True)
        self._spin_brightness_drop.setValue(self._config.brightness_drop_threshold)
        self._slider_pixel_fraction.setValue(int(self._config.cooldown_pixel_fraction * 100))
        self._pixel_fraction_label.setText(f"{self._config.cooldown_pixel_fraction:.2f}")
        self._spin_brightness_drop.blockSignals(False)
        self._slider_pixel_fraction.blockSignals(False)
        key = getattr(self._config, "automation_toggle_bind", "") or ""
        self._btn_rebind.setText(format_bind_for_display(key) if key else "—")
        mode = getattr(self._config, "automation_hotkey_mode", "toggle") or "toggle"
        if mode not in ("toggle", "single_fire"):
            mode = "toggle"
        self._combo_hotkey_mode.blockSignals(True)
        idx = self._combo_hotkey_mode.findData(mode)
        self._combo_hotkey_mode.setCurrentIndex(idx if idx >= 0 else 0)
        self._combo_hotkey_mode.blockSignals(False)
        self._spin_min_delay.blockSignals(True)
        self._spin_min_delay.setValue(getattr(self._config, "min_press_interval_ms", 150))
        self._spin_min_delay.blockSignals(False)
        self._edit_window_title.blockSignals(True)
        self._edit_window_title.setText(getattr(self._config, "target_window_title", "") or "")
        self._edit_window_title.blockSignals(False)
        whitelist = getattr(self._config, "queue_whitelist", []) or []
        self._edit_queue_keys.blockSignals(True)
        self._edit_queue_keys.setText(", ".join(k for k in whitelist))
        self._edit_queue_keys.blockSignals(False)
        self._spin_queue_timeout.blockSignals(True)
        self._spin_queue_timeout.setValue(getattr(self._config, "queue_timeout_ms", 5000))
        self._spin_queue_timeout.blockSignals(False)
        self._spin_queue_fire_delay.blockSignals(True)
        self._spin_queue_fire_delay.setValue(getattr(self._config, "queue_fire_delay_ms", 100))
        self._spin_queue_fire_delay.blockSignals(False)
        self._update_monitor_combo()

    def _update_monitor_combo(self) -> None:
        self._monitor_combo.blockSignals(True)
        try:
            idx = self._monitor_combo.findData(self._config.monitor_index)
            if idx >= 0:
                self._monitor_combo.setCurrentIndex(idx)
        finally:
            self._monitor_combo.blockSignals(False)

    def populate_monitors(self, monitors: list[dict]) -> None:
        self._monitors = list(monitors)
        self._monitor_combo.blockSignals(True)
        try:
            self._monitor_combo.clear()
            for i, m in enumerate(monitors):
                self._monitor_combo.addItem(
                    f"Monitor {i + 1}: {m['width']}×{m['height']}", i + 1
                )
            if monitors:
                clamped = min(max(1, self._config.monitor_index), len(monitors))
                if self._config.monitor_index != clamped:
                    self._config.monitor_index = clamped
                self._monitor_combo.setCurrentIndex(clamped - 1)
        finally:
            self._monitor_combo.blockSignals(False)

    def _emit_config(self) -> None:
        self.config_updated.emit(self._config)
        self._auto_save_timer.stop()
        self._auto_save_timer.start(1000)

    def _do_auto_save(self) -> None:
        try:
            if self._before_save_callback:
                self._before_save_callback()
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump(self._config.to_dict(), f, indent=2)
            self._last_auto_saved = datetime.now()
            self._update_auto_save_status()
            logger.info(f"Config auto-saved to {CONFIG_PATH}")
        except Exception as e:
            logger.error(f"Config auto-save failed: {e}")

    def _update_auto_save_status(self) -> None:
        if self._last_auto_saved is None:
            self._auto_save_status_label.setText("Last auto-saved: —")
        else:
            self._auto_save_status_label.setText(
                self._last_auto_saved.strftime("Last auto-saved: %b %d, %H:%M")
            )

    def _on_profile_changed(self) -> None:
        self._config.profile_name = (self._edit_profile_name.text() or "").strip()
        self._emit_config()

    def _on_export(self) -> None:
        profile = (self._config.profile_name or "").strip()
        default_path = ""
        if profile:
            safe = "".join(c if c not in '<>:"/\\|?*' else "_" for c in profile)
            default_path = str(Path.home() / f"{safe}.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Config", default_path, "JSON (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            if self._before_save_callback:
                self._before_save_callback()
            with open(path, "w") as f:
                json.dump(self._config.to_dict(), f, indent=2)
            logger.info(f"Config exported to {path}")
        except Exception as e:
            logger.error(f"Export failed: {e}")

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Config", "", "JSON (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self._config = AppConfig.from_dict(data)
            self.sync_from_config()
            self._emit_config()
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump(self._config.to_dict(), f, indent=2)
            logger.info(f"Config imported from {path}")
        except Exception as e:
            logger.error(f"Import failed: {e}")

    def _on_monitor_changed(self, index: int) -> None:
        if index < 0:
            return
        mid = self._monitor_combo.itemData(index)
        if mid is not None:
            self._config.monitor_index = int(mid)
            self.monitor_changed.emit(self._config.monitor_index)
            self._emit_config()

    def _on_overlay_changed(self, checked: bool) -> None:
        self._config.overlay_enabled = checked
        self.overlay_visibility_changed.emit(checked)
        self._emit_config()

    def _on_always_on_top_changed(self, checked: bool) -> None:
        self._config.always_on_top = checked
        self._emit_config()

    def _on_history_rows_changed(self, value: int) -> None:
        self._config.history_rows = max(1, min(10, value))
        self._emit_config()

    def _on_bbox_changed(self) -> None:
        self._config.bounding_box = BoundingBox(
            top=self._spin_top.value(),
            left=self._spin_left.value(),
            width=self._spin_width.value(),
            height=self._spin_height.value(),
        )
        self.bounding_box_changed.emit(self._config.bounding_box)
        self._emit_config()

    def _on_slot_layout_changed(self) -> None:
        self._config.slot_count = self._spin_slots.value()
        self._config.slot_gap_pixels = self._spin_gap.value()
        self._config.slot_padding = self._spin_padding.value()
        self.slot_layout_changed.emit(
            self._config.slot_count,
            self._config.slot_gap_pixels,
            self._config.slot_padding,
        )
        self._emit_config()

    def _on_detection_changed(self) -> None:
        self._config.brightness_drop_threshold = self._spin_brightness_drop.value()
        self._config.cooldown_pixel_fraction = self._slider_pixel_fraction.value() / 100.0
        self._pixel_fraction_label.setText(f"{self._config.cooldown_pixel_fraction:.2f}")
        self._emit_config()

    def _on_rebind_clicked(self) -> None:
        if self._capture_bind_thread is not None and self._capture_bind_thread.isRunning():
            return
        self._btn_rebind.setText("...")
        self._btn_rebind.setEnabled(False)
        self._capture_bind_thread = CaptureOneKeyThread(self)
        self._capture_bind_thread.captured.connect(self._on_rebind_captured)
        self._capture_bind_thread.cancelled.connect(self._on_rebind_cancelled)
        self._capture_bind_thread.finished.connect(self._on_rebind_finished)
        self._capture_bind_thread.start()

    def _on_rebind_captured(self, bind_str: str) -> None:
        key = (bind_str or "").strip().lower()
        if key in ("esc", "escape"):
            self._on_rebind_cancelled()
            return
        self._config.automation_toggle_bind = (bind_str or "").strip()
        self._btn_rebind.setText(format_bind_for_display(self._config.automation_toggle_bind))
        self._btn_rebind.setEnabled(True)
        self._emit_config()

    def _on_rebind_cancelled(self) -> None:
        key = getattr(self._config, "automation_toggle_bind", "") or ""
        self._btn_rebind.setText(format_bind_for_display(key) if key else "—")
        self._btn_rebind.setEnabled(True)

    def _on_rebind_finished(self) -> None:
        self._capture_bind_thread = None
        if self._btn_rebind.text() == "...":
            key = getattr(self._config, "automation_toggle_bind", "") or ""
            self._btn_rebind.setText(format_bind_for_display(key) if key else "—")
            self._btn_rebind.setEnabled(True)

    def _on_min_delay_changed(self, value: int) -> None:
        self._config.min_press_interval_ms = max(50, min(2000, value))
        self._emit_config()

    def _on_hotkey_mode_changed(self, index: int) -> None:
        mode = self._combo_hotkey_mode.itemData(index)
        if mode not in ("toggle", "single_fire"):
            mode = "toggle"
        self._config.automation_hotkey_mode = str(mode)
        self._emit_config()

    def _on_window_title_changed(self) -> None:
        self._config.target_window_title = (self._edit_window_title.text() or "").strip()
        self._emit_config()

    def _on_queue_keys_changed(self) -> None:
        raw = (self._edit_queue_keys.text() or "").strip()
        keys = [k.strip().lower() for k in raw.split(",") if k.strip()]
        self._config.queue_whitelist = keys
        self._emit_config()

    def _on_queue_timeout_changed(self, value: int) -> None:
        self._config.queue_timeout_ms = max(1000, min(30000, value))

    def _on_queue_fire_delay_changed(self, value: int) -> None:
        self._config.queue_fire_delay_ms = max(0, min(300, value))
        self._emit_config()

    def _on_calibrate_clicked(self) -> None:
        self.calibrate_requested.emit()

    def closeEvent(self, event) -> None:
        event.accept()
        self.hide()

    def show_or_raise(self) -> None:
        self.sync_from_config()
        self._update_auto_save_status()
        self._resize_to_fit_content()
        if self.isVisible():
            self.raise_()
            self.activateWindow()
        else:
            self.show()

    def _resize_to_fit_content(self) -> None:
        """Size the dialog so content fits without scrollbars when there is room on screen."""
        content = self._scroll_content
        content.adjustSize()
        sh = content.sizeHint()
        ch = max(sh.height(), 400)
        cw = max(sh.width(), self.minimumWidth())
        status_h = 28
        frame_margin = 48
        preferred_w = cw + frame_margin
        preferred_h = ch + status_h + frame_margin
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            new_w = min(preferred_w, available.width())
            new_h = min(preferred_h, available.height())
            new_w = max(new_w, self.minimumWidth())
            new_h = max(new_h, 200)
            self.resize(new_w, new_h)
        else:
            self.resize(preferred_w, preferred_h)
