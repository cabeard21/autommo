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
    QMessageBox,
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
        self._capture_bind_target: Optional[str] = None
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
        self._check_cast_detection = QCheckBox("Enable cast/channel detection")
        fl.addRow("", self._check_cast_detection)
        cast_band_row = QHBoxLayout()
        self._spin_cast_min_fraction = QSpinBox()
        self._spin_cast_min_fraction.setRange(1, 90)
        self._spin_cast_min_fraction.setSuffix("%")
        self._spin_cast_min_fraction.setMaximumWidth(70)
        self._spin_cast_max_fraction = QSpinBox()
        self._spin_cast_max_fraction.setRange(1, 95)
        self._spin_cast_max_fraction.setSuffix("%")
        self._spin_cast_max_fraction.setMaximumWidth(70)
        cast_band_row.addWidget(self._spin_cast_min_fraction)
        cast_band_row.addWidget(QLabel("to"))
        cast_band_row.addWidget(self._spin_cast_max_fraction)
        cast_band_row.addWidget(QLabel("cast band"))
        cast_band_row.addStretch()
        fl.addRow(_row_label("Cast band:"), cast_band_row)
        cast_timing_row = QHBoxLayout()
        self._spin_cast_confirm_frames = QSpinBox()
        self._spin_cast_confirm_frames.setRange(1, 10)
        self._spin_cast_confirm_frames.setMaximumWidth(56)
        self._spin_cast_min_ms = QSpinBox()
        self._spin_cast_min_ms.setRange(50, 3000)
        self._spin_cast_min_ms.setSuffix(" ms")
        self._spin_cast_min_ms.setMaximumWidth(92)
        self._spin_cast_max_ms = QSpinBox()
        self._spin_cast_max_ms.setRange(100, 8000)
        self._spin_cast_max_ms.setSuffix(" ms")
        self._spin_cast_max_ms.setMaximumWidth(92)
        cast_timing_row.addWidget(QLabel("confirm"))
        cast_timing_row.addWidget(self._spin_cast_confirm_frames)
        cast_timing_row.addWidget(QLabel("min"))
        cast_timing_row.addWidget(self._spin_cast_min_ms)
        cast_timing_row.addWidget(QLabel("max"))
        cast_timing_row.addWidget(self._spin_cast_max_ms)
        cast_timing_row.addStretch()
        fl.addRow(_row_label("Cast timing:"), cast_timing_row)
        cast_grace_row = QHBoxLayout()
        self._spin_cast_cancel_grace_ms = QSpinBox()
        self._spin_cast_cancel_grace_ms.setRange(0, 1000)
        self._spin_cast_cancel_grace_ms.setSuffix(" ms")
        self._spin_cast_cancel_grace_ms.setMaximumWidth(92)
        self._check_channeling_enabled = QCheckBox("Allow channeling mode")
        cast_grace_row.addWidget(self._spin_cast_cancel_grace_ms)
        cast_grace_row.addWidget(self._check_channeling_enabled)
        cast_grace_row.addStretch()
        fl.addRow(_row_label("Cancel grace:"), cast_grace_row)
        self._check_lock_ready_cast_bar = QCheckBox("Lock ready slots while cast bar active")
        fl.addRow("", self._check_lock_ready_cast_bar)
        cast_bar_row = QHBoxLayout()
        self._check_cast_bar_enabled = QCheckBox("Cast bar ROI")
        self._spin_cast_bar_left = QSpinBox()
        self._spin_cast_bar_left.setRange(0, 4000)
        self._spin_cast_bar_left.setPrefix("L ")
        self._spin_cast_bar_left.setMaximumWidth(74)
        self._spin_cast_bar_top = QSpinBox()
        self._spin_cast_bar_top.setRange(0, 4000)
        self._spin_cast_bar_top.setPrefix("T ")
        self._spin_cast_bar_top.setMaximumWidth(74)
        self._spin_cast_bar_width = QSpinBox()
        self._spin_cast_bar_width.setRange(0, 2000)
        self._spin_cast_bar_width.setPrefix("W ")
        self._spin_cast_bar_width.setMaximumWidth(74)
        self._spin_cast_bar_height = QSpinBox()
        self._spin_cast_bar_height.setRange(0, 500)
        self._spin_cast_bar_height.setPrefix("H ")
        self._spin_cast_bar_height.setMaximumWidth(74)
        self._spin_cast_bar_activity = QSpinBox()
        self._spin_cast_bar_activity.setRange(1, 80)
        self._spin_cast_bar_activity.setPrefix("Δ ")
        self._spin_cast_bar_activity.setMaximumWidth(68)
        cast_bar_row.addWidget(self._check_cast_bar_enabled)
        cast_bar_row.addWidget(self._spin_cast_bar_left)
        cast_bar_row.addWidget(self._spin_cast_bar_top)
        cast_bar_row.addWidget(self._spin_cast_bar_width)
        cast_bar_row.addWidget(self._spin_cast_bar_height)
        cast_bar_row.addWidget(self._spin_cast_bar_activity)
        cast_bar_row.addStretch()
        fl.addRow(_row_label("Cast bar:"), cast_bar_row)
        return g

    def _automation_section(self) -> QGroupBox:
        g = QGroupBox("Automation")
        g.setStyleSheet("QGroupBox { font-weight: bold; }")
        fl = QFormLayout(g)
        self._combo_automation_profile = QComboBox()
        self._btn_add_automation_profile = QPushButton("+")
        self._btn_copy_automation_profile = QPushButton("Copy")
        self._btn_remove_automation_profile = QPushButton("−")
        self._btn_add_automation_profile.setFixedWidth(28)
        self._btn_copy_automation_profile.setMinimumWidth(54)
        self._btn_remove_automation_profile.setFixedWidth(28)
        profile_row = QHBoxLayout()
        profile_row.addWidget(self._combo_automation_profile, 1)
        profile_row.addWidget(self._btn_add_automation_profile)
        profile_row.addWidget(self._btn_copy_automation_profile)
        profile_row.addWidget(self._btn_remove_automation_profile)
        fl.addRow(_row_label("List profile:"), profile_row)
        self._edit_automation_profile_name = QLineEdit()
        self._edit_automation_profile_name.setPlaceholderText("e.g. Single Target")
        self._edit_automation_profile_name.setClearButtonEnabled(True)
        fl.addRow(_row_label("List name:"), self._edit_automation_profile_name)
        self._btn_toggle_bind = QPushButton("—")
        self._btn_toggle_bind.setStyleSheet("font-family: monospace;")
        self._btn_toggle_bind.setMinimumWidth(72)
        toggle_help = QLabel("click to bind toggle")
        toggle_help.setStyleSheet("font-size: 10px; color: #666;")
        toggle_row = QHBoxLayout()
        toggle_row.addWidget(self._btn_toggle_bind)
        toggle_row.addWidget(toggle_help)
        fl.addRow(_row_label("Toggle bind:"), toggle_row)
        self._btn_single_fire_bind = QPushButton("—")
        self._btn_single_fire_bind.setStyleSheet("font-family: monospace;")
        self._btn_single_fire_bind.setMinimumWidth(72)
        single_help = QLabel("click to bind single fire")
        single_help.setStyleSheet("font-size: 10px; color: #666;")
        single_row = QHBoxLayout()
        single_row.addWidget(self._btn_single_fire_bind)
        single_row.addWidget(single_help)
        fl.addRow(_row_label("Single bind:"), single_row)
        self._automation_bind_conflict_badge = QLabel("")
        self._automation_bind_conflict_badge.setWordWrap(True)
        self._automation_bind_conflict_badge.setStyleSheet(
            "font-size: 10px; color: #ffb3b3; background: #4a1f1f; border: 1px solid #7a2f2f; border-radius: 3px; padding: 4px 6px;"
        )
        self._automation_bind_conflict_badge.setVisible(False)
        fl.addRow("", self._automation_bind_conflict_badge)
        self._spin_min_delay = QSpinBox()
        self._spin_min_delay.setRange(50, 2000)
        self._spin_min_delay.setMaximumWidth(56)
        fl.addRow(_row_label("Delay (ms):"), self._spin_min_delay)
        self._spin_queue_window = QSpinBox()
        self._spin_queue_window.setRange(0, 500)
        self._spin_queue_window.setMaximumWidth(56)
        fl.addRow(_row_label("Queue (ms):"), self._spin_queue_window)
        self._check_allow_cast_while_casting = QCheckBox("Allow sends while casting/channeling")
        fl.addRow("", self._check_allow_cast_while_casting)
        self._edit_window_title = QLineEdit()
        self._edit_window_title.setPlaceholderText("e.g. World of Warcraft")
        self._edit_window_title.setClearButtonEnabled(True)
        fl.addRow(_row_label("Window:"), self._edit_window_title)
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
        self._check_cast_detection.toggled.connect(self._on_detection_changed)
        self._spin_cast_min_fraction.valueChanged.connect(self._on_detection_changed)
        self._spin_cast_max_fraction.valueChanged.connect(self._on_detection_changed)
        self._spin_cast_confirm_frames.valueChanged.connect(self._on_detection_changed)
        self._spin_cast_min_ms.valueChanged.connect(self._on_detection_changed)
        self._spin_cast_max_ms.valueChanged.connect(self._on_detection_changed)
        self._spin_cast_cancel_grace_ms.valueChanged.connect(self._on_detection_changed)
        self._check_channeling_enabled.toggled.connect(self._on_detection_changed)
        self._check_lock_ready_cast_bar.toggled.connect(self._on_detection_changed)
        self._check_cast_bar_enabled.toggled.connect(self._on_detection_changed)
        self._spin_cast_bar_left.valueChanged.connect(self._on_detection_changed)
        self._spin_cast_bar_top.valueChanged.connect(self._on_detection_changed)
        self._spin_cast_bar_width.valueChanged.connect(self._on_detection_changed)
        self._spin_cast_bar_height.valueChanged.connect(self._on_detection_changed)
        self._spin_cast_bar_activity.valueChanged.connect(self._on_detection_changed)
        self._combo_automation_profile.currentIndexChanged.connect(self._on_automation_profile_selected)
        self._btn_add_automation_profile.clicked.connect(self._on_add_automation_profile)
        self._btn_copy_automation_profile.clicked.connect(self._on_copy_automation_profile)
        self._btn_remove_automation_profile.clicked.connect(self._on_remove_automation_profile)
        self._edit_automation_profile_name.textChanged.connect(self._on_automation_profile_name_changed)
        self._btn_toggle_bind.clicked.connect(self._on_rebind_toggle_clicked)
        self._btn_single_fire_bind.clicked.connect(self._on_rebind_single_fire_clicked)
        self._spin_min_delay.valueChanged.connect(self._on_min_delay_changed)
        self._spin_queue_window.valueChanged.connect(self._on_queue_window_changed)
        self._check_allow_cast_while_casting.toggled.connect(self._on_allow_cast_while_casting_changed)
        self._edit_window_title.textChanged.connect(self._on_window_title_changed)
        self._btn_calibrate.clicked.connect(self._on_calibrate_clicked)

    def sync_from_config(self) -> None:
        """Populate all controls from current config."""
        self._config.ensure_priority_profiles()
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
        self._check_cast_detection.blockSignals(True)
        self._spin_cast_min_fraction.blockSignals(True)
        self._spin_cast_max_fraction.blockSignals(True)
        self._spin_cast_confirm_frames.blockSignals(True)
        self._spin_cast_min_ms.blockSignals(True)
        self._spin_cast_max_ms.blockSignals(True)
        self._spin_cast_cancel_grace_ms.blockSignals(True)
        self._check_channeling_enabled.blockSignals(True)
        self._check_lock_ready_cast_bar.blockSignals(True)
        self._check_cast_bar_enabled.blockSignals(True)
        self._spin_cast_bar_left.blockSignals(True)
        self._spin_cast_bar_top.blockSignals(True)
        self._spin_cast_bar_width.blockSignals(True)
        self._spin_cast_bar_height.blockSignals(True)
        self._spin_cast_bar_activity.blockSignals(True)
        self._check_cast_detection.setChecked(getattr(self._config, "cast_detection_enabled", True))
        self._spin_cast_min_fraction.setValue(
            int(round(getattr(self._config, "cast_candidate_min_fraction", 0.05) * 100))
        )
        self._spin_cast_max_fraction.setValue(
            int(round(getattr(self._config, "cast_candidate_max_fraction", 0.22) * 100))
        )
        self._spin_cast_confirm_frames.setValue(getattr(self._config, "cast_confirm_frames", 2))
        self._spin_cast_min_ms.setValue(getattr(self._config, "cast_min_duration_ms", 150))
        self._spin_cast_max_ms.setValue(getattr(self._config, "cast_max_duration_ms", 3000))
        self._spin_cast_cancel_grace_ms.setValue(getattr(self._config, "cast_cancel_grace_ms", 120))
        self._check_channeling_enabled.setChecked(getattr(self._config, "channeling_enabled", True))
        self._check_lock_ready_cast_bar.setChecked(
            getattr(self._config, "lock_ready_while_cast_bar_active", False)
        )
        cast_bar_region = getattr(self._config, "cast_bar_region", {}) or {}
        self._check_cast_bar_enabled.setChecked(bool(cast_bar_region.get("enabled", False)))
        self._spin_cast_bar_left.setValue(int(cast_bar_region.get("left", 0)))
        self._spin_cast_bar_top.setValue(int(cast_bar_region.get("top", 0)))
        self._spin_cast_bar_width.setValue(int(cast_bar_region.get("width", 0)))
        self._spin_cast_bar_height.setValue(int(cast_bar_region.get("height", 0)))
        self._spin_cast_bar_activity.setValue(
            int(round(getattr(self._config, "cast_bar_activity_threshold", 12.0)))
        )
        self._spin_brightness_drop.blockSignals(False)
        self._slider_pixel_fraction.blockSignals(False)
        self._check_cast_detection.blockSignals(False)
        self._spin_cast_min_fraction.blockSignals(False)
        self._spin_cast_max_fraction.blockSignals(False)
        self._spin_cast_confirm_frames.blockSignals(False)
        self._spin_cast_min_ms.blockSignals(False)
        self._spin_cast_max_ms.blockSignals(False)
        self._spin_cast_cancel_grace_ms.blockSignals(False)
        self._check_channeling_enabled.blockSignals(False)
        self._check_lock_ready_cast_bar.blockSignals(False)
        self._check_cast_bar_enabled.blockSignals(False)
        self._spin_cast_bar_left.blockSignals(False)
        self._spin_cast_bar_top.blockSignals(False)
        self._spin_cast_bar_width.blockSignals(False)
        self._spin_cast_bar_height.blockSignals(False)
        self._spin_cast_bar_activity.blockSignals(False)
        self._sync_automation_profile_controls()
        self._spin_min_delay.blockSignals(True)
        self._spin_min_delay.setValue(getattr(self._config, "min_press_interval_ms", 150))
        self._spin_min_delay.blockSignals(False)
        self._spin_queue_window.blockSignals(True)
        self._spin_queue_window.setValue(getattr(self._config, "queue_window_ms", 120))
        self._spin_queue_window.blockSignals(False)
        self._check_allow_cast_while_casting.blockSignals(True)
        self._check_allow_cast_while_casting.setChecked(
            bool(getattr(self._config, "allow_cast_while_casting", False))
        )
        self._check_allow_cast_while_casting.blockSignals(False)
        self._edit_window_title.blockSignals(True)
        self._edit_window_title.setText(getattr(self._config, "target_window_title", "") or "")
        self._edit_window_title.blockSignals(False)
        self._update_monitor_combo()

    def _sync_automation_profile_controls(self) -> None:
        self._config.ensure_priority_profiles()
        self._combo_automation_profile.blockSignals(True)
        self._combo_automation_profile.clear()
        for p in self._config.priority_profiles:
            profile_name = str(p.get("name", "") or "").strip() or str(p.get("id", "Profile"))
            self._combo_automation_profile.addItem(profile_name, p.get("id"))
        active_id = self._config.active_priority_profile_id
        idx = self._combo_automation_profile.findData(active_id)
        self._combo_automation_profile.setCurrentIndex(idx if idx >= 0 else 0)
        self._combo_automation_profile.blockSignals(False)
        active = self._config.get_active_priority_profile()
        self._edit_automation_profile_name.blockSignals(True)
        self._edit_automation_profile_name.setText(str(active.get("name", "") or ""))
        self._edit_automation_profile_name.blockSignals(False)
        toggle_bind = str(active.get("toggle_bind", "") or "")
        single_fire_bind = str(active.get("single_fire_bind", "") or "")
        self._btn_toggle_bind.setText(format_bind_for_display(toggle_bind) if toggle_bind else "—")
        self._btn_single_fire_bind.setText(
            format_bind_for_display(single_fire_bind) if single_fire_bind else "—"
        )
        self._update_automation_bind_conflict_badge()
        self._btn_remove_automation_profile.setEnabled(len(self._config.priority_profiles) > 1)

    def _automation_bind_conflicts(self) -> list[str]:
        active = self._config.get_active_priority_profile()
        active_id = str(active.get("id", "") or "")
        active_name = str(active.get("name", "") or "Active").strip() or "Active"
        active_toggle = str(active.get("toggle_bind", "") or "").strip().lower()
        active_single = str(active.get("single_fire_bind", "") or "").strip().lower()
        conflicts: list[str] = []
        if active_toggle and active_toggle == active_single:
            conflicts.append(
                f"{active_name}: toggle and single use the same key ({format_bind_for_display(active_toggle)})"
            )
        for p in self._config.priority_profiles:
            pid = str(p.get("id", "") or "")
            if pid == active_id:
                continue
            pname = str(p.get("name", "") or pid or "Profile").strip() or "Profile"
            other_toggle = str(p.get("toggle_bind", "") or "").strip().lower()
            other_single = str(p.get("single_fire_bind", "") or "").strip().lower()
            if active_toggle:
                if active_toggle == other_toggle:
                    conflicts.append(
                        f"Toggle {format_bind_for_display(active_toggle)} is also toggle on {pname}"
                    )
                if active_toggle == other_single:
                    conflicts.append(
                        f"Toggle {format_bind_for_display(active_toggle)} is single-fire on {pname}"
                    )
            if active_single:
                if active_single == other_toggle:
                    conflicts.append(
                        f"Single {format_bind_for_display(active_single)} is toggle on {pname}"
                    )
                if active_single == other_single:
                    conflicts.append(
                        f"Single {format_bind_for_display(active_single)} is also single-fire on {pname}"
                    )
        return conflicts

    def _update_automation_bind_conflict_badge(self) -> None:
        conflicts = self._automation_bind_conflicts()
        if not conflicts:
            self._automation_bind_conflict_badge.clear()
            self._automation_bind_conflict_badge.setVisible(False)
            return
        self._automation_bind_conflict_badge.setText("Bind conflict:\n" + "\n".join(conflicts))
        self._automation_bind_conflict_badge.setVisible(True)

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
        cast_min = self._spin_cast_min_fraction.value() / 100.0
        cast_max = self._spin_cast_max_fraction.value() / 100.0
        if cast_min >= cast_max:
            cast_max = min(0.95, cast_min + 0.01)
        self._config.cast_detection_enabled = self._check_cast_detection.isChecked()
        self._config.cast_candidate_min_fraction = cast_min
        self._config.cast_candidate_max_fraction = cast_max
        self._config.cast_confirm_frames = self._spin_cast_confirm_frames.value()
        self._config.cast_min_duration_ms = self._spin_cast_min_ms.value()
        self._config.cast_max_duration_ms = max(
            self._spin_cast_max_ms.value(),
            self._config.cast_min_duration_ms,
        )
        self._config.cast_cancel_grace_ms = self._spin_cast_cancel_grace_ms.value()
        self._config.channeling_enabled = self._check_channeling_enabled.isChecked()
        self._config.lock_ready_while_cast_bar_active = self._check_lock_ready_cast_bar.isChecked()
        self._config.cast_bar_region = {
            "enabled": self._check_cast_bar_enabled.isChecked(),
            "left": self._spin_cast_bar_left.value(),
            "top": self._spin_cast_bar_top.value(),
            "width": self._spin_cast_bar_width.value(),
            "height": self._spin_cast_bar_height.value(),
        }
        self._config.cast_bar_activity_threshold = float(self._spin_cast_bar_activity.value())
        self._emit_config()

    def _start_rebind_capture(self, target: str, button: QPushButton) -> None:
        if self._capture_bind_thread is not None and self._capture_bind_thread.isRunning():
            return
        self._capture_bind_target = target
        button.setText("...")
        button.setEnabled(False)
        self._capture_bind_thread = CaptureOneKeyThread(self)
        self._capture_bind_thread.captured.connect(self._on_rebind_captured)
        self._capture_bind_thread.cancelled.connect(self._on_rebind_cancelled)
        self._capture_bind_thread.finished.connect(self._on_rebind_finished)
        self._capture_bind_thread.start()

    def _on_rebind_toggle_clicked(self) -> None:
        self._start_rebind_capture("toggle_bind", self._btn_toggle_bind)

    def _on_rebind_single_fire_clicked(self) -> None:
        self._start_rebind_capture("single_fire_bind", self._btn_single_fire_bind)

    def _is_bind_in_use_elsewhere(self, bind: str, field_name: str) -> bool:
        active_id = self._config.active_priority_profile_id
        for p in self._config.priority_profiles:
            if str(p.get("id", "") or "") == active_id:
                continue
            if bind and bind == str(p.get("toggle_bind", "") or "").strip().lower():
                return True
            if bind and bind == str(p.get("single_fire_bind", "") or "").strip().lower():
                return True
        active = self._config.get_active_priority_profile()
        if field_name == "toggle_bind" and bind and bind == str(active.get("single_fire_bind", "") or "").strip().lower():
            return True
        if field_name == "single_fire_bind" and bind and bind == str(active.get("toggle_bind", "") or "").strip().lower():
            return True
        return False

    def _on_rebind_captured(self, bind_str: str) -> None:
        key = (bind_str or "").strip().lower()
        if key in ("esc", "escape"):
            self._on_rebind_cancelled()
            return
        target = self._capture_bind_target
        if target not in ("toggle_bind", "single_fire_bind"):
            self._on_rebind_cancelled()
            return
        if self._is_bind_in_use_elsewhere(key, target):
            QMessageBox.warning(
                self,
                "Bind already in use",
                "That key is already used by another automation profile bind.",
            )
            self._on_rebind_cancelled()
            return
        active = self._config.get_active_priority_profile()
        active[target] = key
        self._sync_automation_profile_controls()
        self._emit_config()

    def _on_rebind_cancelled(self) -> None:
        self._sync_automation_profile_controls()

    def _on_rebind_finished(self) -> None:
        self._capture_bind_thread = None
        self._capture_bind_target = None
        self._sync_automation_profile_controls()

    def _on_min_delay_changed(self, value: int) -> None:
        self._config.min_press_interval_ms = max(50, min(2000, value))
        self._emit_config()

    def _on_queue_window_changed(self, value: int) -> None:
        self._config.queue_window_ms = max(0, min(500, value))
        self._emit_config()

    def _on_allow_cast_while_casting_changed(self, checked: bool) -> None:
        self._config.allow_cast_while_casting = bool(checked)
        self._emit_config()

    def _on_automation_profile_selected(self, index: int) -> None:
        if index < 0:
            return
        pid = str(self._combo_automation_profile.itemData(index) or "")
        if not pid:
            return
        changed = self._config.set_active_priority_profile(pid)
        self._sync_automation_profile_controls()
        if changed:
            self._emit_config()

    def _on_add_automation_profile(self) -> None:
        self._config.ensure_priority_profiles()
        existing_ids = {str(p.get("id", "") or "") for p in self._config.priority_profiles}
        i = 1
        while f"profile_{i}" in existing_ids:
            i += 1
        new_id = f"profile_{i}"
        self._config.priority_profiles.append(
            {
                "id": new_id,
                "name": f"Profile {i}",
                "priority_order": [],
                "toggle_bind": "",
                "single_fire_bind": "",
            }
        )
        self._config.set_active_priority_profile(new_id)
        self._sync_automation_profile_controls()
        self._emit_config()

    def _on_remove_automation_profile(self) -> None:
        self._config.ensure_priority_profiles()
        if len(self._config.priority_profiles) <= 1:
            return
        active_id = self._config.active_priority_profile_id
        self._config.priority_profiles = [
            p for p in self._config.priority_profiles if str(p.get("id", "") or "") != active_id
        ]
        self._config.ensure_priority_profiles()
        self._sync_automation_profile_controls()
        self._emit_config()

    def _on_copy_automation_profile(self) -> None:
        self._config.ensure_priority_profiles()
        source = self._config.get_active_priority_profile()
        existing_ids = {str(p.get("id", "") or "") for p in self._config.priority_profiles}
        i = 1
        while f"profile_{i}" in existing_ids:
            i += 1
        new_id = f"profile_{i}"
        base_name = str(source.get("name", "") or "Profile").strip() or "Profile"
        existing_names = {
            (str(p.get("name", "") or "").strip().lower()) for p in self._config.priority_profiles
        }
        new_name = f"{base_name} Copy"
        suffix = 2
        while new_name.strip().lower() in existing_names:
            new_name = f"{base_name} Copy {suffix}"
            suffix += 1
        self._config.priority_profiles.append(
            {
                "id": new_id,
                "name": new_name,
                "priority_order": list(source.get("priority_order", [])),
                "toggle_bind": str(source.get("toggle_bind", "") or ""),
                "single_fire_bind": str(source.get("single_fire_bind", "") or ""),
            }
        )
        self._config.set_active_priority_profile(new_id)
        self._sync_automation_profile_controls()
        self._emit_config()

    def _on_automation_profile_name_changed(self) -> None:
        active = self._config.get_active_priority_profile()
        active["name"] = (self._edit_automation_profile_name.text() or "").strip() or "Profile"
        idx = self._combo_automation_profile.currentIndex()
        if idx >= 0:
            self._combo_automation_profile.blockSignals(True)
            self._combo_automation_profile.setItemText(idx, active["name"])
            self._combo_automation_profile.blockSignals(False)
        self._emit_config()

    def _on_window_title_changed(self) -> None:
        self._config.target_window_title = (self._edit_window_title.text() or "").strip()
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
