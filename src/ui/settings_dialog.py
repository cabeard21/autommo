"""Settings window - non-modal dialog for all configuration (Profile, Display, Capture, Detection, Automation, Calibration)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.models import AppConfig, BoundingBox
from src.automation.global_hotkey import CaptureOneKeyThread, format_bind_for_display
from src.automation.binds import normalize_bind
from src.ui.themes import load_theme

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"

LABEL_MIN_WIDTH = 85
LABEL_MIN_WIDTH_NARROW = 50
LABEL_MIN_WIDTH_XNARROW = 34
SECTION_GAP = 10


def _row_label(text: str, narrow: bool = False, xnarrow: bool = False) -> QLabel:
    l = QLabel(text)
    if xnarrow:
        l.setObjectName("formLabelXnarrow")
        l.setMinimumWidth(LABEL_MIN_WIDTH_XNARROW)
    else:
        l.setObjectName("formLabelNarrow" if narrow else "formLabel")
        l.setMinimumWidth(LABEL_MIN_WIDTH_NARROW if narrow else LABEL_MIN_WIDTH)
    l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return l


def _section_frame(title: str, content: QWidget) -> QFrame:
    f = QFrame()
    f.setObjectName("section")
    layout = QVBoxLayout(f)
    layout.setContentsMargins(5, 6, 5, 6)
    layout.setSpacing(6)
    title_l = QLabel(title.upper())
    title_l.setObjectName("sectionTitle")
    layout.addWidget(title_l)
    layout.addWidget(content)
    return f


def _subsection_frame(title: str, content: QWidget) -> QFrame:
    f = QFrame()
    f.setObjectName("subsection")
    layout = QVBoxLayout(f)
    layout.setContentsMargins(4, 5, 4, 5)
    layout.setSpacing(4)
    title_l = QLabel(title.upper())
    title_l.setObjectName("subsectionTitle")
    layout.addWidget(title_l)
    layout.addWidget(content)
    return f


class SettingsDialog(QDialog):
    """Non-modal settings dialog. Emits config_updated when any value changes. Auto-saves after changes and shows last auto-save time."""

    config_updated = pyqtSignal(object)
    calibrate_requested = pyqtSignal()
    calibrate_buff_present_requested = pyqtSignal(str)
    bounding_box_changed = pyqtSignal(object)
    slot_layout_changed = pyqtSignal(int, int, int)
    overlay_visibility_changed = pyqtSignal(bool)
    monitor_changed = pyqtSignal(int)

    def __init__(
        self,
        config: AppConfig,
        before_save_callback: Optional[Callable[[], None]] = None,
        after_import_callback: Optional[Callable[["AppConfig"], None]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._before_save_callback = before_save_callback
        self._after_import_callback = after_import_callback
        self._monitors: list[dict] = []
        self._capture_bind_thread: Optional[CaptureOneKeyThread] = None
        self._capture_bind_target: Optional[str] = None
        self._rebind_event_filter_installed = False
        self._last_auto_saved: Optional[datetime] = None
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.timeout.connect(self._do_auto_save)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)
        self.setObjectName("settingsDialog")
        self.setStyleSheet(load_theme("dark") + "\n" + load_theme("settings-dark"))
        self._status_saving = False
        self._status_update_timer = QTimer(self)
        self._status_update_timer.setInterval(30_000)
        self._status_update_timer.timeout.connect(self._update_status_bar)
        self._build_ui()
        self._connect_signals()
        self._update_status_bar()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("settingsTabs")

        # General tab
        general_content = QWidget()
        general_layout = QVBoxLayout(general_content)
        general_layout.setSpacing(SECTION_GAP)
        general_layout.addWidget(_section_frame("Profile", self._profile_section()))
        general_layout.addWidget(_section_frame("Display", self._display_section()))
        general_layout.addWidget(_section_frame("Capture Region", self._capture_section()))
        general_layout.addWidget(_section_frame("Calibration", self._calibration_section()))
        general_layout.addStretch()
        general_scroll = QScrollArea()
        general_scroll.setWidgetResizable(True)
        general_scroll.setFrameShape(QFrame.Shape.NoFrame)
        general_scroll.setWidget(general_content)
        self._scroll_content = general_content
        self._tabs.addTab(general_scroll, "General")

        # Detection tab
        detection_content = QWidget()
        detection_layout = QVBoxLayout(detection_content)
        detection_layout.setSpacing(SECTION_GAP)
        detection_layout.addWidget(_section_frame("Detection", self._detection_section()))
        detection_layout.addStretch()
        detection_scroll = QScrollArea()
        detection_scroll.setWidgetResizable(True)
        detection_scroll.setFrameShape(QFrame.Shape.NoFrame)
        detection_scroll.setWidget(detection_content)
        self._tabs.addTab(detection_scroll, "Detection")

        # Automation tab
        automation_content = QWidget()
        automation_layout = QVBoxLayout(automation_content)
        automation_layout.setSpacing(SECTION_GAP)
        automation_layout.addWidget(_section_frame("Controls", self._automation_controls_section()))
        automation_layout.addWidget(_section_frame("Timing", self._automation_timing_section()))
        automation_layout.addWidget(_section_frame("Priority Lists", self._automation_priority_lists_section()))
        automation_layout.addWidget(_section_frame("Spell Queue", self._spell_queue_section()))
        automation_layout.addStretch()
        automation_scroll = QScrollArea()
        automation_scroll.setWidgetResizable(True)
        automation_scroll.setFrameShape(QFrame.Shape.NoFrame)
        automation_scroll.setWidget(automation_content)
        self._tabs.addTab(automation_scroll, "Automation")

        layout.addWidget(self._tabs)

        # Status bar (no Save button)
        status_bar = QWidget()
        status_bar.setObjectName("settingsStatusBar")
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(14, 6, 14, 6)
        self._status_dot = QLabel()
        self._status_dot.setObjectName("statusBarDot")
        self._status_dot.setFixedSize(6, 6)
        self._status_dot.setStyleSheet("background: #3a7a3a; border-radius: 3px;")
        self._status_text = QLabel("Last saved: -")
        self._status_text.setObjectName("statusBarText")
        status_layout.addWidget(self._status_dot)
        status_layout.addWidget(self._status_text)
        status_layout.addStretch()
        layout.addWidget(status_bar)

    def _profile_section(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        fl.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._edit_profile_name = QLineEdit()
        self._edit_profile_name.setPlaceholderText("e.g. Default")
        self._edit_profile_name.setClearButtonEnabled(True)
        fl.addRow(_row_label("Name:"), self._edit_profile_name)
        btn_row = QHBoxLayout()
        self._btn_export = QPushButton("Export")
        self._btn_import = QPushButton("Import")
        self._btn_new = QPushButton("New")
        btn_row.addWidget(self._btn_export)
        btn_row.addWidget(self._btn_import)
        btn_row.addWidget(self._btn_new)
        fl.addRow("", btn_row)
        return w

    def _display_section(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        self._monitor_combo = QComboBox()
        self._monitor_combo.setMinimumWidth(180)
        fl.addRow(_row_label("Monitor:"), self._monitor_combo)
        cb_row = QHBoxLayout()
        self._check_overlay = QCheckBox("Show Region Overlay")
        self._check_always_on_top = QCheckBox("Always on Top")
        self._check_active_screen_outline = QCheckBox("Show screen outline when active")
        self._check_active_screen_outline.setToolTip(
            "Draws a 1px green outline with a slight glow around the entire screen while capture is running."
        )
        cb_row.addWidget(self._check_overlay)
        cb_row.addWidget(self._check_always_on_top)
        cb_row.addWidget(self._check_active_screen_outline)
        fl.addRow("", cb_row)
        history_row = QHBoxLayout()
        self._spin_history_rows = QSpinBox()
        self._spin_history_rows.setRange(1, 10)
        self._spin_history_rows.setValue(3)
        self._spin_history_rows.setMaximumWidth(48)
        history_row.addWidget(self._spin_history_rows)
        help_l = QLabel("(Last Action / Next Intention)")
        help_l.setObjectName("hint")
        history_row.addWidget(help_l)
        fl.addRow(_row_label("History rows:"), history_row)
        return w

    def _capture_section(self) -> QWidget:
        w = QWidget()
        outer = QHBoxLayout(w)
        outer.addStretch()
        inner = QWidget()
        grid = QGridLayout(inner)
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
        outer.addWidget(inner)
        outer.addStretch()
        return w

    def _detection_section(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        self._spin_polling_fps = QSpinBox()
        self._spin_polling_fps.setRange(5, 120)
        self._spin_polling_fps.setMaximumWidth(64)
        self._spin_polling_fps.setToolTip(
            "Capture/analyze ticks per second. Higher is more responsive but uses more CPU."
        )
        fl.addRow(_row_label("Polling FPS:"), self._spin_polling_fps)
        self._spin_cooldown_min_ms = QSpinBox()
        self._spin_cooldown_min_ms.setRange(0, 5000)
        self._spin_cooldown_min_ms.setSuffix(" ms")
        self._spin_cooldown_min_ms.setMaximumWidth(92)
        self._spin_cooldown_min_ms.setToolTip(
            "Minimum not-ready duration before classifying as full cooldown. Shorter dips are treated as GCD."
        )
        fl.addRow(_row_label("Cooldown min:"), self._spin_cooldown_min_ms)
        self._combo_detection_region = QComboBox()
        self._combo_detection_region.addItem("Top-Left Quadrant", "top_left")
        self._combo_detection_region.addItem("Full Slot", "full")
        region_help = QLabel("(?)")
        region_help.setObjectName("hint")
        region_help.setToolTip(
            "Which area of each slot to check for cooldown darkness. Top-Left Quadrant is more precise for WoW's clockwise cooldown wipe."
        )
        region_row = QHBoxLayout()
        region_row.addWidget(self._combo_detection_region)
        region_row.addWidget(region_help)
        fl.addRow(_row_label("Region:"), region_row)
        self._spin_brightness_drop = QSpinBox()
        self._spin_brightness_drop.setRange(0, 255)
        self._spin_brightness_drop.setMaximumWidth(48)
        darken_help = QLabel("(?)")
        darken_help.setObjectName("hint")
        darken_help.setToolTip(
            "Pixels must darken by this many brightness levels (0-255) to count as on cooldown."
        )
        darken_row = QHBoxLayout()
        darken_row.addWidget(self._spin_brightness_drop)
        darken_row.addWidget(darken_help)
        fl.addRow(_row_label("Darken:"), darken_row)
        self._slider_pixel_fraction = QSlider(Qt.Orientation.Horizontal)
        self._slider_pixel_fraction.setRange(10, 90)
        self._slider_pixel_fraction.setSingleStep(5)
        self._pixel_fraction_label = QLabel("0.30")
        self._pixel_fraction_label.setMinimumWidth(32)
        trigger_help = QLabel("(?)")
        trigger_help.setObjectName("hint")
        trigger_help.setToolTip(
            "Fraction of pixels that must be darkened to trigger cooldown detection."
        )
        trigger_row = QHBoxLayout()
        trigger_row.addWidget(self._slider_pixel_fraction)
        trigger_row.addWidget(self._pixel_fraction_label)
        trigger_row.addWidget(trigger_help)
        fl.addRow(_row_label("Trigger:"), trigger_row)
        self._slider_change_pixel_fraction = QSlider(Qt.Orientation.Horizontal)
        self._slider_change_pixel_fraction.setRange(10, 90)
        self._slider_change_pixel_fraction.setSingleStep(5)
        self._change_pixel_fraction_label = QLabel("0.30")
        self._change_pixel_fraction_label.setMinimumWidth(32)
        change_help = QLabel("(?)")
        change_help.setObjectName("hint")
        change_help.setToolTip(
            "Fraction of pixels that may differ from baseline (dark or bright) before marking not-ready."
        )
        change_row = QHBoxLayout()
        change_row.addWidget(self._slider_change_pixel_fraction)
        change_row.addWidget(self._change_pixel_fraction_label)
        change_row.addWidget(change_help)
        fl.addRow(_row_label("Change:"), change_row)
        self._edit_cooldown_change_ignore_by_slot = QLineEdit()
        self._edit_cooldown_change_ignore_by_slot.setPlaceholderText("e.g. 5")
        self._edit_cooldown_change_ignore_by_slot.setToolTip(
            "Optional slot indexes where change-based cooldown detection is ignored."
        )
        change_ignore_help = QLabel("(?)")
        change_ignore_help.setObjectName("hint")
        change_ignore_help.setToolTip(
            "Example: 5 means slot 5 uses dark-cooldown detection only."
        )
        change_ignore_row = QHBoxLayout()
        change_ignore_row.addWidget(self._edit_cooldown_change_ignore_by_slot)
        change_ignore_row.addWidget(change_ignore_help)
        fl.addRow(_row_label("Change ignore:"), change_ignore_row)
        self._edit_cooldown_group_by_slot = QLineEdit()
        self._edit_cooldown_group_by_slot.setPlaceholderText("e.g. 0:builders, 1:builders")
        self._edit_cooldown_group_by_slot.setToolTip(
            "Optional cooldown-memory sharing groups as slot:group pairs (0-based)."
        )
        group_help = QLabel("(?)")
        group_help.setObjectName("hint")
        group_help.setToolTip(
            "Slots in the same group share cooldown-memory smoothing across form switches."
        )
        group_row = QHBoxLayout()
        group_row.addWidget(self._edit_cooldown_group_by_slot)
        group_row.addWidget(group_help)
        fl.addRow(_row_label("Cooldown groups:"), group_row)
        self._edit_detection_region_overrides = QLineEdit()
        self._edit_detection_region_overrides.setPlaceholderText("e.g. 1:top_left, 4:full")
        self._edit_detection_region_overrides.setToolTip(
            "Optional per-slot detection region overrides as slot:mode pairs (0-based). Modes: top_left, full."
        )
        region_override_help = QLabel("(?)")
        region_override_help.setObjectName("hint")
        region_override_help.setToolTip(
            "Example: 1:top_left keeps slot 1 on top-left cooldown detection while global Region can stay Full Slot."
        )
        region_override_row = QHBoxLayout()
        region_override_row.addWidget(self._edit_detection_region_overrides)
        region_override_row.addWidget(region_override_help)
        fl.addRow(_row_label("Region by slot:"), region_override_row)
        self._edit_detection_region_overrides_by_form = QLineEdit()
        self._edit_detection_region_overrides_by_form.setPlaceholderText(
            "e.g. normal=1:top_left; form_1=1:full"
        )
        self._edit_detection_region_overrides_by_form.setToolTip(
            "Optional per-form slot overrides as form=slot:mode lists, separated by ';'. "
            "Example: normal=1:top_left; form_1=1:full"
        )
        region_form_override_help = QLabel("(?)")
        region_form_override_help.setObjectName("hint")
        region_form_override_help.setToolTip(
            "Per-form overrides merge on top of Region by slot using active form id."
        )
        region_form_override_row = QHBoxLayout()
        region_form_override_row.addWidget(self._edit_detection_region_overrides_by_form)
        region_form_override_row.addWidget(region_form_override_help)
        fl.addRow(_row_label("Region by form:"), region_form_override_row)
        self._check_glow_enabled = QCheckBox("Enable glow ready override")
        self._check_glow_enabled.setToolTip(
            "If enabled, confirmed icon glow can mark a slot ready even when generic change-delta says not-ready."
        )
        fl.addRow("", self._check_glow_enabled)
        self._combo_glow_mode = QComboBox()
        self._combo_glow_mode.addItem("Color (legacy)", "color")
        self._combo_glow_mode.addItem("Hybrid motion", "hybrid_motion")
        self._combo_glow_mode.setToolTip(
            "Glow detection mode. Hybrid motion combines color with ring movement and rotation cues."
        )
        fl.addRow(_row_label("Glow mode:"), self._combo_glow_mode)
        glow_row = QHBoxLayout()
        self._spin_glow_ring_thickness = QSpinBox()
        self._spin_glow_ring_thickness.setRange(1, 12)
        self._spin_glow_ring_thickness.setPrefix("ring ")
        self._spin_glow_ring_thickness.setMaximumWidth(92)
        self._spin_glow_ring_thickness.setToolTip(
            "Thickness of the edge ring (pixels) used to look for glow."
        )
        self._spin_glow_value_delta = QSpinBox()
        self._spin_glow_value_delta.setRange(5, 120)
        self._spin_glow_value_delta.setPrefix("V+ ")
        self._spin_glow_value_delta.setMaximumWidth(82)
        self._spin_glow_value_delta.setToolTip(
            "Minimum brightness increase vs baseline in the ring to count as glow."
        )
        self._spin_glow_saturation_min = QSpinBox()
        self._spin_glow_saturation_min.setRange(0, 255)
        self._spin_glow_saturation_min.setPrefix("S>= ")
        self._spin_glow_saturation_min.setMaximumWidth(86)
        self._spin_glow_saturation_min.setToolTip(
            "Minimum HSV saturation for glow-colored ring pixels."
        )
        self._spin_glow_confirm_frames = QSpinBox()
        self._spin_glow_confirm_frames.setRange(1, 8)
        self._spin_glow_confirm_frames.setPrefix("N ")
        self._spin_glow_confirm_frames.setMaximumWidth(64)
        self._spin_glow_confirm_frames.setToolTip(
            "Consecutive glow frames required before glow is considered confirmed."
        )
        glow_row.addWidget(self._spin_glow_ring_thickness)
        glow_row.addWidget(self._spin_glow_value_delta)
        glow_row.addWidget(self._spin_glow_saturation_min)
        glow_row.addWidget(self._spin_glow_confirm_frames)
        glow_row.addStretch()
        fl.addRow(_row_label("Glow:"), glow_row)
        self._edit_glow_value_delta_by_slot = QLineEdit()
        self._edit_glow_value_delta_by_slot.setPlaceholderText("e.g. 4:55, 6:45")
        self._edit_glow_value_delta_by_slot.setToolTip(
            "Optional per-slot V+ overrides as slot:delta pairs (0-based slot index), comma-separated."
        )
        glow_slot_help = QLabel("(?)")
        glow_slot_help.setObjectName("hint")
        glow_slot_help.setToolTip(
            "Example: 4:55,6:45 means slot 4 uses V+=55 and slot 6 uses V+=45."
        )
        glow_slot_row = QHBoxLayout()
        glow_slot_row.addWidget(self._edit_glow_value_delta_by_slot)
        glow_slot_row.addWidget(glow_slot_help)
        fl.addRow(_row_label("Glow by slot:"), glow_slot_row)
        self._edit_glow_ring_fraction_by_slot = QLineEdit()
        self._edit_glow_ring_fraction_by_slot.setPlaceholderText("e.g. 5:0.08")
        self._edit_glow_ring_fraction_by_slot.setToolTip(
            "Optional per-slot yellow fraction overrides as slot:fraction pairs (0-based), comma-separated."
        )
        glow_frac_slot_help = QLabel("(?)")
        glow_frac_slot_help.setObjectName("hint")
        glow_frac_slot_help.setToolTip(
            "Example: 5:0.08 lowers yellow fraction threshold for slot 5 only."
        )
        glow_frac_slot_row = QHBoxLayout()
        glow_frac_slot_row.addWidget(self._edit_glow_ring_fraction_by_slot)
        glow_frac_slot_row.addWidget(glow_frac_slot_help)
        fl.addRow(_row_label("Y frac by slot:"), glow_frac_slot_row)
        self._edit_glow_override_cooldown_by_slot = QLineEdit()
        self._edit_glow_override_cooldown_by_slot.setPlaceholderText("e.g. 5")
        self._edit_glow_override_cooldown_by_slot.setToolTip(
            "Optional slot indexes where any confirmed glow (not just red) can override cooldown."
        )
        glow_override_help = QLabel("(?)")
        glow_override_help.setObjectName("hint")
        glow_override_help.setToolTip(
            "Example: 5,7 allows yellow/white glow to mark those slots ready while on cooldown."
        )
        glow_override_row = QHBoxLayout()
        glow_override_row.addWidget(self._edit_glow_override_cooldown_by_slot)
        glow_override_row.addWidget(glow_override_help)
        fl.addRow(_row_label("Glow->ready:"), glow_override_row)
        self._slider_glow_ring_fraction = QSlider(Qt.Orientation.Horizontal)
        self._slider_glow_ring_fraction.setRange(5, 60)
        self._slider_glow_ring_fraction.setSingleStep(1)
        self._slider_glow_ring_fraction.setToolTip(
            "Minimum fraction of ring pixels that must meet yellow glow criteria."
        )
        self._glow_ring_fraction_label = QLabel("0.18")
        self._glow_ring_fraction_label.setMinimumWidth(32)
        glow_frac_help = QLabel("(?)")
        glow_frac_help.setObjectName("hint")
        glow_frac_help.setToolTip(
            "Minimum fraction of ring pixels matching yellow glow color/brightness criteria."
        )
        glow_frac_row = QHBoxLayout()
        glow_frac_row.addWidget(self._slider_glow_ring_fraction)
        glow_frac_row.addWidget(self._glow_ring_fraction_label)
        glow_frac_row.addWidget(glow_frac_help)
        fl.addRow(_row_label("Yellow frac:"), glow_frac_row)
        self._slider_glow_red_ring_fraction = QSlider(Qt.Orientation.Horizontal)
        self._slider_glow_red_ring_fraction.setRange(5, 60)
        self._slider_glow_red_ring_fraction.setSingleStep(1)
        self._slider_glow_red_ring_fraction.setToolTip(
            "Minimum fraction of ring pixels that must meet red glow criteria."
        )
        self._glow_red_ring_fraction_label = QLabel("0.18")
        self._glow_red_ring_fraction_label.setMinimumWidth(32)
        glow_red_frac_help = QLabel("(?)")
        glow_red_frac_help.setObjectName("hint")
        glow_red_frac_help.setToolTip(
            "Minimum fraction of ring pixels matching red glow color/brightness criteria."
        )
        glow_red_frac_row = QHBoxLayout()
        glow_red_frac_row.addWidget(self._slider_glow_red_ring_fraction)
        glow_red_frac_row.addWidget(self._glow_red_ring_fraction_label)
        glow_red_frac_row.addWidget(glow_red_frac_help)
        fl.addRow(_row_label("Red frac:"), glow_red_frac_row)
        glow_hue_row = QHBoxLayout()
        self._spin_glow_yellow_hue_min = QSpinBox()
        self._spin_glow_yellow_hue_min.setRange(0, 179)
        self._spin_glow_yellow_hue_min.setPrefix("Y min ")
        self._spin_glow_yellow_hue_min.setMaximumWidth(86)
        self._spin_glow_yellow_hue_max = QSpinBox()
        self._spin_glow_yellow_hue_max.setRange(0, 179)
        self._spin_glow_yellow_hue_max.setPrefix("Y max ")
        self._spin_glow_yellow_hue_max.setMaximumWidth(86)
        self._spin_glow_red_hue_max_low = QSpinBox()
        self._spin_glow_red_hue_max_low.setRange(0, 179)
        self._spin_glow_red_hue_max_low.setPrefix("R<= ")
        self._spin_glow_red_hue_max_low.setMaximumWidth(78)
        self._spin_glow_red_hue_min_high = QSpinBox()
        self._spin_glow_red_hue_min_high.setRange(0, 179)
        self._spin_glow_red_hue_min_high.setPrefix("R>= ")
        self._spin_glow_red_hue_min_high.setMaximumWidth(78)
        glow_hue_row.addWidget(self._spin_glow_yellow_hue_min)
        glow_hue_row.addWidget(self._spin_glow_yellow_hue_max)
        glow_hue_row.addWidget(self._spin_glow_red_hue_max_low)
        glow_hue_row.addWidget(self._spin_glow_red_hue_min_high)
        glow_hue_row.addStretch()
        fl.addRow(_row_label("Glow hue:"), glow_hue_row)
        self._check_lock_ready_cast_bar = QCheckBox("Lock ready slots while cast bar active")
        fl.addRow("", self._check_lock_ready_cast_bar)
        cast_bar_row = QHBoxLayout()
        self._check_cast_bar_enabled = QCheckBox("Cast bar ROI")
        self._spin_cast_bar_left = QSpinBox()
        self._spin_cast_bar_left.setRange(-4000, 4000)
        self._spin_cast_bar_left.setPrefix("L ")
        self._spin_cast_bar_left.setMaximumWidth(74)
        self._spin_cast_bar_top = QSpinBox()
        self._spin_cast_bar_top.setRange(-4000, 4000)
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
        self._spin_cast_bar_activity.setPrefix("d ")
        self._spin_cast_bar_activity.setMaximumWidth(68)
        cast_bar_row.addWidget(self._check_cast_bar_enabled)
        cast_bar_row.addWidget(self._spin_cast_bar_left)
        cast_bar_row.addWidget(self._spin_cast_bar_top)
        cast_bar_row.addWidget(self._spin_cast_bar_width)
        cast_bar_row.addWidget(self._spin_cast_bar_height)
        cast_bar_row.addWidget(self._spin_cast_bar_activity)
        cast_bar_row.addStretch()
        fl.addRow(_row_label("Cast bar:"), cast_bar_row)
        buff_row = QHBoxLayout()
        self._combo_buff_roi = QComboBox()
        self._combo_buff_roi.setMinimumWidth(140)
        self._btn_add_buff_roi = QPushButton("+")
        self._btn_add_buff_roi.setFixedWidth(28)
        self._btn_remove_buff_roi = QPushButton("-")
        self._btn_remove_buff_roi.setObjectName("deleteButton")
        self._btn_remove_buff_roi.setFixedWidth(28)
        self._edit_buff_roi_name = QLineEdit()
        self._edit_buff_roi_name.setPlaceholderText("Buff name")
        buff_row.addWidget(self._combo_buff_roi)
        buff_row.addWidget(self._btn_add_buff_roi)
        buff_row.addWidget(self._btn_remove_buff_roi)
        buff_row.addWidget(self._edit_buff_roi_name, 1)
        fl.addRow(_row_label("Buff ROI:"), buff_row)
        buff_geom_row = QHBoxLayout()
        self._check_buff_roi_enabled = QCheckBox("Enabled")
        self._spin_buff_left = QSpinBox()
        self._spin_buff_left.setRange(-4000, 4000)
        self._spin_buff_left.setPrefix("L ")
        self._spin_buff_left.setMaximumWidth(74)
        self._spin_buff_top = QSpinBox()
        self._spin_buff_top.setRange(-4000, 4000)
        self._spin_buff_top.setPrefix("T ")
        self._spin_buff_top.setMaximumWidth(74)
        self._spin_buff_width = QSpinBox()
        self._spin_buff_width.setRange(0, 2000)
        self._spin_buff_width.setPrefix("W ")
        self._spin_buff_width.setMaximumWidth(74)
        self._spin_buff_height = QSpinBox()
        self._spin_buff_height.setRange(0, 500)
        self._spin_buff_height.setPrefix("H ")
        self._spin_buff_height.setMaximumWidth(74)
        buff_geom_row.addWidget(self._check_buff_roi_enabled)
        buff_geom_row.addWidget(self._spin_buff_left)
        buff_geom_row.addWidget(self._spin_buff_top)
        buff_geom_row.addWidget(self._spin_buff_width)
        buff_geom_row.addWidget(self._spin_buff_height)
        buff_geom_row.addStretch()
        fl.addRow(_row_label("Buff rect:"), buff_geom_row)
        buff_detect_row = QHBoxLayout()
        self._spin_buff_match_threshold = QSpinBox()
        self._spin_buff_match_threshold.setRange(50, 100)
        self._spin_buff_match_threshold.setPrefix("T ")
        self._spin_buff_match_threshold.setSuffix("%")
        self._spin_buff_match_threshold.setMaximumWidth(86)
        self._spin_buff_confirm_frames = QSpinBox()
        self._spin_buff_confirm_frames.setRange(1, 10)
        self._spin_buff_confirm_frames.setPrefix("N ")
        self._spin_buff_confirm_frames.setMaximumWidth(64)
        buff_detect_row.addWidget(self._spin_buff_match_threshold)
        buff_detect_row.addWidget(self._spin_buff_confirm_frames)
        buff_detect_row.addStretch()
        fl.addRow(_row_label("Buff detect:"), buff_detect_row)
        buff_cal_row = QHBoxLayout()
        self._btn_calibrate_buff_present = QPushButton("Calibrate Present")
        self._btn_clear_buff_templates = QPushButton("Clear")
        self._buff_calibration_status = QLabel("Uncalibrated")
        self._buff_calibration_status.setObjectName("hint")
        buff_cal_row.addWidget(self._btn_calibrate_buff_present)
        buff_cal_row.addWidget(self._btn_clear_buff_templates)
        buff_cal_row.addWidget(self._buff_calibration_status, 1)
        fl.addRow(_row_label("Buff calib:"), buff_cal_row)
        form_row = QHBoxLayout()
        self._combo_form = QComboBox()
        self._combo_form.setMinimumWidth(120)
        self._btn_add_form = QPushButton("+")
        self._btn_add_form.setFixedWidth(28)
        self._btn_remove_form = QPushButton("-")
        self._btn_remove_form.setObjectName("deleteButton")
        self._btn_remove_form.setFixedWidth(28)
        self._edit_form_name = QLineEdit()
        self._edit_form_name.setPlaceholderText("Form name")
        form_row.addWidget(self._combo_form)
        form_row.addWidget(self._btn_add_form)
        form_row.addWidget(self._btn_remove_form)
        form_row.addWidget(self._edit_form_name, 1)
        fl.addRow(_row_label("Forms:"), form_row)
        form_active_row = QHBoxLayout()
        self._combo_active_form = QComboBox()
        self._combo_active_form.setMinimumWidth(120)
        self._label_form_status = QLabel("normal")
        self._label_form_status.setObjectName("hint")
        form_active_row.addWidget(self._combo_active_form)
        form_active_row.addWidget(self._label_form_status, 1)
        fl.addRow(_row_label("Active form:"), form_active_row)
        detector_row = QHBoxLayout()
        self._combo_form_detector_type = QComboBox()
        self._combo_form_detector_type.addItem("Off", "off")
        self._combo_form_detector_type.addItem("Buff ROI", "buff_roi")
        self._combo_form_detector_type.setMinimumWidth(90)
        self._combo_form_detector_roi = QComboBox()
        self._combo_form_detector_roi.setMinimumWidth(120)
        detector_row.addWidget(self._combo_form_detector_type)
        detector_row.addWidget(self._combo_form_detector_roi, 1)
        fl.addRow(_row_label("Form detect:"), detector_row)
        detector_map_row = QHBoxLayout()
        self._combo_form_present = QComboBox()
        self._combo_form_absent = QComboBox()
        self._spin_form_confirm_frames = QSpinBox()
        self._spin_form_confirm_frames.setRange(1, 10)
        self._spin_form_confirm_frames.setPrefix("N ")
        self._spin_form_confirm_frames.setMaximumWidth(62)
        self._spin_form_settle_ms = QSpinBox()
        self._spin_form_settle_ms.setRange(0, 1000)
        self._spin_form_settle_ms.setSuffix(" ms")
        self._spin_form_settle_ms.setMaximumWidth(86)
        detector_map_row.addWidget(QLabel("present"))
        detector_map_row.addWidget(self._combo_form_present)
        detector_map_row.addWidget(QLabel("absent"))
        detector_map_row.addWidget(self._combo_form_absent)
        detector_map_row.addWidget(self._spin_form_confirm_frames)
        detector_map_row.addWidget(self._spin_form_settle_ms)
        detector_map_row.addStretch()
        fl.addRow(_row_label("Detect map:"), detector_map_row)
        return w

    def _automation_controls_section(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        self._btn_toggle_bind = QPushButton("-")
        self._btn_toggle_bind.setObjectName("bindButton")
        self._btn_toggle_bind.setMinimumWidth(72)
        self._btn_toggle_bind.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        toggle_help = QLabel("click to bind combo, right-click to clear")
        toggle_help.setObjectName("hint")
        toggle_row = QHBoxLayout()
        toggle_row.addWidget(self._btn_toggle_bind)
        toggle_row.addWidget(toggle_help)
        fl.addRow(_row_label("Toggle bind:"), toggle_row)
        self._btn_single_fire_bind = QPushButton("-")
        self._btn_single_fire_bind.setObjectName("bindButton")
        self._btn_single_fire_bind.setMinimumWidth(72)
        self._btn_single_fire_bind.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        single_help = QLabel("click to bind combo, right-click to clear")
        single_help.setObjectName("hint")
        single_row = QHBoxLayout()
        single_row.addWidget(self._btn_single_fire_bind)
        single_row.addWidget(single_help)
        fl.addRow(_row_label("Single bind:"), single_row)
        self._automation_bind_conflict_badge = QLabel("")
        self._automation_bind_conflict_badge.setObjectName("hint")
        self._automation_bind_conflict_badge.setWordWrap(True)
        self._automation_bind_conflict_badge.setVisible(False)
        fl.addRow("", self._automation_bind_conflict_badge)
        self._edit_window_title = QLineEdit()
        self._edit_window_title.setPlaceholderText("e.g. World of Warcraft")
        self._edit_window_title.setClearButtonEnabled(True)
        fl.addRow(_row_label("Window:"), self._edit_window_title)
        return w

    def _automation_timing_section(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        self._spin_min_delay = QSpinBox()
        self._spin_min_delay.setRange(50, 2000)
        self._spin_min_delay.setMaximumWidth(56)
        fl.addRow(_row_label("Delay (ms):"), self._spin_min_delay)
        self._spin_gcd_ms = QSpinBox()
        self._spin_gcd_ms.setRange(500, 3000)
        self._spin_gcd_ms.setSuffix(" ms")
        self._spin_gcd_ms.setMaximumWidth(80)
        self._spin_gcd_ms.setToolTip(
            "GCD duration used for queue suppression after sending a queued key."
        )
        fl.addRow(_row_label("GCD (ms):"), self._spin_gcd_ms)
        self._spin_queue_window = QSpinBox()
        self._spin_queue_window.setRange(0, 500)
        self._spin_queue_window.setMaximumWidth(56)
        fl.addRow(_row_label("Queue (ms):"), self._spin_queue_window)
        self._check_allow_cast_while_casting = QCheckBox("Allow sends while casting/channeling")
        fl.addRow("", self._check_allow_cast_while_casting)
        return w

    def _automation_priority_lists_section(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        self._combo_automation_profile = QComboBox()
        self._btn_add_automation_profile = QPushButton("+ New")
        self._btn_copy_automation_profile = QPushButton("Copy")
        self._btn_remove_automation_profile = QPushButton("Delete")
        self._btn_remove_automation_profile.setObjectName("deleteButton")
        self._btn_add_automation_profile.setFixedWidth(56)
        self._btn_copy_automation_profile.setMinimumWidth(54)
        self._btn_remove_automation_profile.setFixedWidth(56)
        profile_row = QHBoxLayout()
        profile_row.addWidget(self._combo_automation_profile, 1)
        profile_row.addWidget(self._btn_add_automation_profile)
        profile_row.addWidget(self._btn_copy_automation_profile)
        profile_row.addWidget(self._btn_remove_automation_profile)
        fl.addRow(_row_label("Active list:"), profile_row)
        self._edit_automation_profile_name = QLineEdit()
        self._edit_automation_profile_name.setPlaceholderText("e.g. Single Target")
        self._edit_automation_profile_name.setClearButtonEnabled(True)
        fl.addRow(_row_label("List name:"), self._edit_automation_profile_name)
        return w

    def _spell_queue_section(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        self._edit_queue_keys = QLineEdit()
        self._edit_queue_keys.setPlaceholderText("e.g. R, T, V")
        self._edit_queue_keys.setClearButtonEnabled(True)
        fl.addRow(_row_label("Queue keys:"), self._edit_queue_keys)
        queue_help = QLabel(
            "Manual presses of these keys (or bound keys not in priority) will queue to fire at next GCD"
        )
        queue_help.setObjectName("hint")
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
        return w

    def _calibration_section(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self._btn_calibrate = QPushButton("Calibrate All Baselines")
        self._btn_calibrate.setObjectName("calibrateButton")
        layout.addWidget(self._btn_calibrate)
        tip = QLabel("Tip: individual slots can be calibrated via right-click on Slot States")
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        return w

    def _update_status_bar(self) -> None:
        if self._status_saving:
            self._status_dot.setStyleSheet("background: #e5a522; border-radius: 3px;")
            self._status_text.setText("Saving...")
            self._status_text.setProperty("saving", True)
            self._status_text.style().unpolish(self._status_text)
            self._status_text.style().polish(self._status_text)
            return
        self._status_text.setProperty("saving", False)
        self._status_text.style().unpolish(self._status_text)
        self._status_text.style().polish(self._status_text)
        self._status_dot.setStyleSheet("background: #3a7a3a; border-radius: 3px;")
        if self._last_auto_saved is None:
            self._status_text.setText("Last saved: -")
            return
        delta = datetime.now() - self._last_auto_saved
        secs = int(delta.total_seconds())
        if secs < 60:
            self._status_text.setText("Last saved: just now")
        elif secs < 3600:
            self._status_text.setText(f"Last saved: {secs // 60}m ago")
        else:
            self._status_text.setText(f"Last saved: {secs // 3600}h ago")

    def _connect_signals(self) -> None:
        self._edit_profile_name.textChanged.connect(self._on_profile_changed)
        self._btn_export.clicked.connect(self._on_export)
        self._btn_import.clicked.connect(self._on_import)
        self._btn_new.clicked.connect(self._on_new)
        self._monitor_combo.currentIndexChanged.connect(self._on_monitor_changed)
        self._check_overlay.toggled.connect(self._on_overlay_changed)
        self._check_always_on_top.toggled.connect(self._on_always_on_top_changed)
        self._check_active_screen_outline.toggled.connect(self._on_active_screen_outline_changed)
        self._spin_history_rows.valueChanged.connect(self._on_history_rows_changed)
        self._spin_top.valueChanged.connect(self._on_bbox_changed)
        self._spin_left.valueChanged.connect(self._on_bbox_changed)
        self._spin_width.valueChanged.connect(self._on_bbox_changed)
        self._spin_height.valueChanged.connect(self._on_bbox_changed)
        self._spin_slots.valueChanged.connect(self._on_slot_layout_changed)
        self._spin_gap.valueChanged.connect(self._on_slot_layout_changed)
        self._spin_padding.valueChanged.connect(self._on_slot_layout_changed)
        self._spin_polling_fps.valueChanged.connect(self._on_detection_changed)
        self._spin_cooldown_min_ms.valueChanged.connect(self._on_detection_changed)
        self._combo_detection_region.currentIndexChanged.connect(self._on_detection_changed)
        self._spin_brightness_drop.valueChanged.connect(self._on_detection_changed)
        self._slider_pixel_fraction.valueChanged.connect(self._on_detection_changed)
        self._slider_change_pixel_fraction.valueChanged.connect(self._on_detection_changed)
        self._edit_cooldown_change_ignore_by_slot.editingFinished.connect(self._on_detection_changed)
        self._edit_cooldown_group_by_slot.editingFinished.connect(self._on_detection_changed)
        self._edit_detection_region_overrides.editingFinished.connect(self._on_detection_changed)
        self._edit_detection_region_overrides_by_form.editingFinished.connect(self._on_detection_changed)
        self._check_glow_enabled.toggled.connect(self._on_detection_changed)
        self._combo_glow_mode.currentIndexChanged.connect(self._on_detection_changed)
        self._spin_glow_ring_thickness.valueChanged.connect(self._on_detection_changed)
        self._spin_glow_value_delta.valueChanged.connect(self._on_detection_changed)
        self._spin_glow_saturation_min.valueChanged.connect(self._on_detection_changed)
        self._spin_glow_confirm_frames.valueChanged.connect(self._on_detection_changed)
        self._edit_glow_value_delta_by_slot.editingFinished.connect(self._on_detection_changed)
        self._edit_glow_ring_fraction_by_slot.editingFinished.connect(self._on_detection_changed)
        self._edit_glow_override_cooldown_by_slot.editingFinished.connect(self._on_detection_changed)
        self._slider_glow_ring_fraction.valueChanged.connect(self._on_detection_changed)
        self._slider_glow_red_ring_fraction.valueChanged.connect(self._on_detection_changed)
        self._spin_glow_yellow_hue_min.valueChanged.connect(self._on_detection_changed)
        self._spin_glow_yellow_hue_max.valueChanged.connect(self._on_detection_changed)
        self._spin_glow_red_hue_max_low.valueChanged.connect(self._on_detection_changed)
        self._spin_glow_red_hue_min_high.valueChanged.connect(self._on_detection_changed)
        self._check_lock_ready_cast_bar.toggled.connect(self._on_detection_changed)
        self._check_cast_bar_enabled.toggled.connect(self._on_detection_changed)
        self._spin_cast_bar_left.valueChanged.connect(self._on_detection_changed)
        self._spin_cast_bar_top.valueChanged.connect(self._on_detection_changed)
        self._spin_cast_bar_width.valueChanged.connect(self._on_detection_changed)
        self._spin_cast_bar_height.valueChanged.connect(self._on_detection_changed)
        self._spin_cast_bar_activity.valueChanged.connect(self._on_detection_changed)
        self._combo_buff_roi.currentIndexChanged.connect(self._on_buff_roi_selected)
        self._btn_add_buff_roi.clicked.connect(self._on_add_buff_roi)
        self._btn_remove_buff_roi.clicked.connect(self._on_remove_buff_roi)
        self._edit_buff_roi_name.textChanged.connect(self._on_detection_changed)
        self._check_buff_roi_enabled.toggled.connect(self._on_detection_changed)
        self._spin_buff_left.valueChanged.connect(self._on_detection_changed)
        self._spin_buff_top.valueChanged.connect(self._on_detection_changed)
        self._spin_buff_width.valueChanged.connect(self._on_detection_changed)
        self._spin_buff_height.valueChanged.connect(self._on_detection_changed)
        self._spin_buff_match_threshold.valueChanged.connect(self._on_detection_changed)
        self._spin_buff_confirm_frames.valueChanged.connect(self._on_detection_changed)
        self._btn_calibrate_buff_present.clicked.connect(self._on_calibrate_buff_present_clicked)
        self._btn_clear_buff_templates.clicked.connect(self._on_clear_buff_templates_clicked)
        self._combo_form.currentIndexChanged.connect(self._on_form_selected)
        self._btn_add_form.clicked.connect(self._on_add_form)
        self._btn_remove_form.clicked.connect(self._on_remove_form)
        self._edit_form_name.textChanged.connect(self._on_detection_changed)
        self._combo_active_form.currentIndexChanged.connect(self._on_detection_changed)
        self._combo_form_detector_type.currentIndexChanged.connect(self._on_detection_changed)
        self._combo_form_detector_roi.currentIndexChanged.connect(self._on_detection_changed)
        self._combo_form_present.currentIndexChanged.connect(self._on_detection_changed)
        self._combo_form_absent.currentIndexChanged.connect(self._on_detection_changed)
        self._spin_form_confirm_frames.valueChanged.connect(self._on_detection_changed)
        self._spin_form_settle_ms.valueChanged.connect(self._on_detection_changed)
        self._combo_automation_profile.currentIndexChanged.connect(self._on_automation_profile_selected)
        self._btn_add_automation_profile.clicked.connect(self._on_add_automation_profile)
        self._btn_copy_automation_profile.clicked.connect(self._on_copy_automation_profile)
        self._btn_remove_automation_profile.clicked.connect(self._on_remove_automation_profile)
        self._edit_automation_profile_name.textChanged.connect(self._on_automation_profile_name_changed)
        self._btn_toggle_bind.clicked.connect(self._on_rebind_toggle_clicked)
        self._btn_toggle_bind.customContextMenuRequested.connect(self._on_rebind_toggle_cleared)
        self._btn_single_fire_bind.clicked.connect(self._on_rebind_single_fire_clicked)
        self._btn_single_fire_bind.customContextMenuRequested.connect(self._on_rebind_single_fire_cleared)
        self._spin_min_delay.valueChanged.connect(self._on_min_delay_changed)
        self._spin_gcd_ms.valueChanged.connect(self._on_gcd_ms_changed)
        self._spin_queue_window.valueChanged.connect(self._on_queue_window_changed)
        self._check_allow_cast_while_casting.toggled.connect(self._on_allow_cast_while_casting_changed)
        self._edit_window_title.textChanged.connect(self._on_window_title_changed)
        self._edit_queue_keys.textChanged.connect(self._on_queue_keys_changed)
        self._spin_queue_timeout.valueChanged.connect(self._on_queue_timeout_changed)
        self._spin_queue_fire_delay.valueChanged.connect(self._on_queue_fire_delay_changed)
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
        self._check_active_screen_outline.blockSignals(True)
        self._check_active_screen_outline.setChecked(getattr(self._config, "show_active_screen_outline", False))
        self._check_active_screen_outline.blockSignals(False)
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
        self._spin_polling_fps.blockSignals(True)
        self._spin_cooldown_min_ms.blockSignals(True)
        self._spin_brightness_drop.blockSignals(True)
        self._slider_pixel_fraction.blockSignals(True)
        self._slider_change_pixel_fraction.blockSignals(True)
        self._combo_detection_region.blockSignals(True)
        self._edit_cooldown_change_ignore_by_slot.blockSignals(True)
        self._edit_cooldown_group_by_slot.blockSignals(True)
        self._edit_detection_region_overrides.blockSignals(True)
        self._edit_detection_region_overrides_by_form.blockSignals(True)
        self._check_glow_enabled.blockSignals(True)
        self._combo_glow_mode.blockSignals(True)
        self._spin_glow_ring_thickness.blockSignals(True)
        self._spin_glow_value_delta.blockSignals(True)
        self._spin_glow_saturation_min.blockSignals(True)
        self._spin_glow_confirm_frames.blockSignals(True)
        self._edit_glow_value_delta_by_slot.blockSignals(True)
        self._edit_glow_ring_fraction_by_slot.blockSignals(True)
        self._edit_glow_override_cooldown_by_slot.blockSignals(True)
        self._slider_glow_ring_fraction.blockSignals(True)
        self._slider_glow_red_ring_fraction.blockSignals(True)
        self._spin_glow_yellow_hue_min.blockSignals(True)
        self._spin_glow_yellow_hue_max.blockSignals(True)
        self._spin_glow_red_hue_max_low.blockSignals(True)
        self._spin_glow_red_hue_min_high.blockSignals(True)
        self._spin_polling_fps.setValue(int(getattr(self._config, "polling_fps", 20)))
        self._spin_cooldown_min_ms.setValue(int(getattr(self._config, "cooldown_min_duration_ms", 2000)))
        self._spin_brightness_drop.setValue(self._config.brightness_drop_threshold)
        region = (getattr(self._config, "detection_region", None) or "top_left").strip().lower()
        if region not in ("full", "top_left"):
            region = "top_left"
        idx = self._combo_detection_region.findData(region)
        self._combo_detection_region.setCurrentIndex(idx if idx >= 0 else 0)
        self._slider_pixel_fraction.setValue(int(self._config.cooldown_pixel_fraction * 100))
        self._pixel_fraction_label.setText(f"{self._config.cooldown_pixel_fraction:.2f}")
        self._slider_change_pixel_fraction.setValue(
            int(round(getattr(self._config, "cooldown_change_pixel_fraction", self._config.cooldown_pixel_fraction) * 100))
        )
        self._change_pixel_fraction_label.setText(
            f"{getattr(self._config, 'cooldown_change_pixel_fraction', self._config.cooldown_pixel_fraction):.2f}"
        )
        self._edit_cooldown_change_ignore_by_slot.setText(
            self._format_slot_index_list(
                getattr(self._config, "cooldown_change_ignore_by_slot", []) or []
            )
        )
        self._edit_cooldown_group_by_slot.setText(
            self._format_cooldown_group_by_slot(
                getattr(self._config, "cooldown_group_by_slot", {}) or {}
            )
        )
        self._edit_detection_region_overrides.setText(
            self._format_detection_region_overrides(
                getattr(self._config, "detection_region_overrides", {}) or {}
            )
        )
        self._edit_detection_region_overrides_by_form.setText(
            self._format_detection_region_overrides_by_form(
                getattr(self._config, "detection_region_overrides_by_form", {}) or {}
            )
        )
        self._check_glow_enabled.setChecked(bool(getattr(self._config, "glow_enabled", True)))
        glow_mode = str(getattr(self._config, "glow_mode", "color") or "color").strip().lower()
        if glow_mode not in ("color", "hybrid_motion"):
            glow_mode = "color"
        idx = self._combo_glow_mode.findData(glow_mode)
        self._combo_glow_mode.setCurrentIndex(idx if idx >= 0 else 0)
        self._spin_glow_ring_thickness.setValue(int(getattr(self._config, "glow_ring_thickness_px", 4)))
        self._spin_glow_value_delta.setValue(int(getattr(self._config, "glow_value_delta", 35)))
        self._spin_glow_saturation_min.setValue(int(getattr(self._config, "glow_saturation_min", 80)))
        self._spin_glow_confirm_frames.setValue(int(getattr(self._config, "glow_confirm_frames", 2)))
        self._edit_glow_value_delta_by_slot.setText(
            self._format_glow_value_delta_by_slot(
                getattr(self._config, "glow_value_delta_by_slot", {}) or {}
            )
        )
        self._edit_glow_ring_fraction_by_slot.setText(
            self._format_glow_ring_fraction_by_slot(
                getattr(self._config, "glow_ring_fraction_by_slot", {}) or {}
            )
        )
        self._edit_glow_override_cooldown_by_slot.setText(
            self._format_slot_index_list(
                getattr(self._config, "glow_override_cooldown_by_slot", []) or []
            )
        )
        self._slider_glow_ring_fraction.setValue(
            int(round(getattr(self._config, "glow_ring_fraction", 0.18) * 100))
        )
        self._slider_glow_red_ring_fraction.setValue(
            int(
                round(
                    getattr(
                        self._config,
                        "glow_red_ring_fraction",
                        getattr(self._config, "glow_ring_fraction", 0.18),
                    )
                    * 100
                )
            )
        )
        self._spin_glow_yellow_hue_min.setValue(int(getattr(self._config, "glow_yellow_hue_min", 18)))
        self._spin_glow_yellow_hue_max.setValue(int(getattr(self._config, "glow_yellow_hue_max", 42)))
        self._spin_glow_red_hue_max_low.setValue(int(getattr(self._config, "glow_red_hue_max_low", 12)))
        self._spin_glow_red_hue_min_high.setValue(int(getattr(self._config, "glow_red_hue_min_high", 168)))
        self._glow_ring_fraction_label.setText(f"{getattr(self._config, 'glow_ring_fraction', 0.18):.2f}")
        self._glow_red_ring_fraction_label.setText(
            f"{getattr(self._config, 'glow_red_ring_fraction', getattr(self._config, 'glow_ring_fraction', 0.18)):.2f}"
        )
        self._check_lock_ready_cast_bar.blockSignals(True)
        self._check_cast_bar_enabled.blockSignals(True)
        self._spin_cast_bar_left.blockSignals(True)
        self._spin_cast_bar_top.blockSignals(True)
        self._spin_cast_bar_width.blockSignals(True)
        self._spin_cast_bar_height.blockSignals(True)
        self._spin_cast_bar_activity.blockSignals(True)
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
        self._spin_polling_fps.blockSignals(False)
        self._spin_cooldown_min_ms.blockSignals(False)
        self._spin_brightness_drop.blockSignals(False)
        self._slider_pixel_fraction.blockSignals(False)
        self._slider_change_pixel_fraction.blockSignals(False)
        self._combo_detection_region.blockSignals(False)
        self._edit_cooldown_change_ignore_by_slot.blockSignals(False)
        self._edit_cooldown_group_by_slot.blockSignals(False)
        self._edit_detection_region_overrides.blockSignals(False)
        self._edit_detection_region_overrides_by_form.blockSignals(False)
        self._check_glow_enabled.blockSignals(False)
        self._combo_glow_mode.blockSignals(False)
        self._spin_glow_ring_thickness.blockSignals(False)
        self._spin_glow_value_delta.blockSignals(False)
        self._spin_glow_saturation_min.blockSignals(False)
        self._spin_glow_confirm_frames.blockSignals(False)
        self._edit_glow_value_delta_by_slot.blockSignals(False)
        self._edit_glow_ring_fraction_by_slot.blockSignals(False)
        self._edit_glow_override_cooldown_by_slot.blockSignals(False)
        self._slider_glow_ring_fraction.blockSignals(False)
        self._slider_glow_red_ring_fraction.blockSignals(False)
        self._spin_glow_yellow_hue_min.blockSignals(False)
        self._spin_glow_yellow_hue_max.blockSignals(False)
        self._spin_glow_red_hue_max_low.blockSignals(False)
        self._spin_glow_red_hue_min_high.blockSignals(False)
        self._check_lock_ready_cast_bar.blockSignals(False)
        self._check_cast_bar_enabled.blockSignals(False)
        self._spin_cast_bar_left.blockSignals(False)
        self._spin_cast_bar_top.blockSignals(False)
        self._spin_cast_bar_width.blockSignals(False)
        self._spin_cast_bar_height.blockSignals(False)
        self._spin_cast_bar_activity.blockSignals(False)
        self._sync_form_controls()
        self._sync_buff_roi_controls()
        self._sync_automation_profile_controls()
        self._spin_min_delay.blockSignals(True)
        self._spin_min_delay.setValue(getattr(self._config, "min_press_interval_ms", 150))
        self._spin_min_delay.blockSignals(False)
        self._spin_gcd_ms.blockSignals(True)
        self._spin_gcd_ms.setValue(int(getattr(self._config, "gcd_ms", 1500)))
        self._spin_gcd_ms.blockSignals(False)
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
        active_form = str(getattr(self._config, "active_form_id", "normal") or "normal").strip().lower() or "normal"
        self._btn_calibrate.setText(f"Calibrate Baselines ({active_form})")
        self._update_monitor_combo()
        self._update_status_bar()

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
        toggle_bind = normalize_bind(str(active.get("toggle_bind", "") or ""))
        single_fire_bind = normalize_bind(str(active.get("single_fire_bind", "") or ""))
        capture_active = (
            self._capture_bind_thread is not None and self._capture_bind_thread.isRunning()
        )
        self._btn_toggle_bind.setEnabled(not capture_active)
        self._btn_single_fire_bind.setEnabled(not capture_active)
        self._btn_toggle_bind.setText(
            "..."
            if capture_active and self._capture_bind_target == "toggle_bind"
            else (format_bind_for_display(toggle_bind) if toggle_bind else "-")
        )
        self._btn_single_fire_bind.setText(
            "..."
            if capture_active and self._capture_bind_target == "single_fire_bind"
            else (format_bind_for_display(single_fire_bind) if single_fire_bind else "-")
        )
        self._update_automation_bind_conflict_badge()
        self._btn_remove_automation_profile.setEnabled(len(self._config.priority_profiles) > 1)

    def _automation_bind_conflicts(self) -> list[str]:
        active = self._config.get_active_priority_profile()
        active_id = str(active.get("id", "") or "")
        active_name = str(active.get("name", "") or "Active").strip() or "Active"
        active_toggle = normalize_bind(str(active.get("toggle_bind", "") or ""))
        active_single = normalize_bind(str(active.get("single_fire_bind", "") or ""))
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
            other_toggle = normalize_bind(str(p.get("toggle_bind", "") or ""))
            other_single = normalize_bind(str(p.get("single_fire_bind", "") or ""))
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
                    f"Monitor {i + 1}: {m['width']}x{m['height']}", i + 1
                )
            if monitors:
                clamped = min(max(1, self._config.monitor_index), len(monitors))
                if self._config.monitor_index != clamped:
                    self._config.monitor_index = clamped
                self._monitor_combo.setCurrentIndex(clamped - 1)
        finally:
            self._monitor_combo.blockSignals(False)

    def selected_active_form_id(self) -> str:
        """Return the form selected in Settings for manual calibration actions."""
        form_id = str(self._combo_active_form.currentData() or "").strip().lower()
        if form_id:
            return form_id
        return str(getattr(self._config, "active_form_id", "normal") or "normal").strip().lower() or "normal"

    def _emit_config(self) -> None:
        self.config_updated.emit(self._config)
        self._auto_save_timer.stop()
        self._auto_save_timer.start(1000)

    def _do_auto_save(self) -> None:
        self._status_saving = True
        self._update_status_bar()
        try:
            if self._before_save_callback:
                self._before_save_callback()
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump(self._config.to_dict(), f, indent=2)
            self._last_auto_saved = datetime.now()
            logger.info(f"Config auto-saved to {CONFIG_PATH}")
        except Exception as e:
            logger.error(f"Config auto-save failed: {e}")
        finally:
            QTimer.singleShot(500, self._clear_saving_state)

    def _clear_saving_state(self) -> None:
        self._status_saving = False
        self._update_status_bar()

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
            if self._after_import_callback:
                self._after_import_callback(self._config)
            self.sync_from_config()
            self._emit_config()
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump(self._config.to_dict(), f, indent=2)
            logger.info(f"Config imported from {path}")
        except Exception as e:
            logger.error(f"Import failed: {e}")

    def _on_new(self) -> None:
        reply = QMessageBox.question(
            self,
            "Reset to Factory Defaults",
            "This will reset ALL settings and calibrations to factory defaults.\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._config = AppConfig()
        if self._after_import_callback:
            self._after_import_callback(self._config)
        self.sync_from_config()
        self._emit_config()
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump(self._config.to_dict(), f, indent=2)
            logger.info("Config reset to factory defaults")
        except Exception as e:
            logger.error(f"Failed to save reset config: {e}")

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

    def _on_active_screen_outline_changed(self, checked: bool) -> None:
        self._config.show_active_screen_outline = checked
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

    @staticmethod
    def _parse_glow_value_delta_by_slot(raw_text: str) -> dict[int, int]:
        out: dict[int, int] = {}
        for token in str(raw_text or "").split(","):
            part = token.strip()
            if not part:
                continue
            if ":" not in part:
                continue
            left, right = part.split(":", 1)
            try:
                slot_idx = int(left.strip())
                delta = int(right.strip())
            except Exception:
                continue
            if slot_idx < 0:
                continue
            delta = max(0, min(255, delta))
            out[slot_idx] = delta
        return out

    @staticmethod
    def _format_glow_value_delta_by_slot(overrides: dict) -> str:
        parsed: list[tuple[int, int]] = []
        for k, v in (overrides or {}).items():
            try:
                slot_idx = int(k)
                delta = int(v)
            except Exception:
                continue
            if slot_idx < 0:
                continue
            parsed.append((slot_idx, delta))
        items = [f"{slot_idx}:{delta}" for slot_idx, delta in sorted(parsed, key=lambda t: t[0])]
        return ", ".join(items)

    @staticmethod
    def _parse_glow_ring_fraction_by_slot(raw_text: str) -> dict[int, float]:
        out: dict[int, float] = {}
        for token in str(raw_text or "").split(","):
            part = token.strip()
            if not part or ":" not in part:
                continue
            left, right = part.split(":", 1)
            try:
                slot_idx = int(left.strip())
                frac = float(right.strip())
            except Exception:
                continue
            if slot_idx < 0:
                continue
            frac = max(0.0, min(1.0, frac))
            out[slot_idx] = frac
        return out

    @staticmethod
    def _format_glow_ring_fraction_by_slot(overrides: dict) -> str:
        parsed: list[tuple[int, float]] = []
        for k, v in (overrides or {}).items():
            try:
                slot_idx = int(k)
                frac = float(v)
            except Exception:
                continue
            if slot_idx < 0:
                continue
            parsed.append((slot_idx, frac))
        items = [f"{slot_idx}:{frac:.2f}" for slot_idx, frac in sorted(parsed, key=lambda t: t[0])]
        return ", ".join(items)

    @staticmethod
    def _parse_slot_index_list(raw_text: str) -> list[int]:
        out: list[int] = []
        seen: set[int] = set()
        for token in str(raw_text or "").split(","):
            part = token.strip()
            if not part:
                continue
            try:
                slot_idx = int(part)
            except Exception:
                continue
            if slot_idx < 0 or slot_idx in seen:
                continue
            seen.add(slot_idx)
            out.append(slot_idx)
        return out

    @staticmethod
    def _format_slot_index_list(values: list[int]) -> str:
        parsed: set[int] = set()
        for v in values or []:
            try:
                slot_idx = int(v)
            except Exception:
                continue
            if slot_idx < 0:
                continue
            parsed.add(slot_idx)
        return ", ".join(str(v) for v in sorted(parsed))

    @staticmethod
    def _parse_cooldown_group_by_slot(raw_text: str) -> dict[int, str]:
        out: dict[int, str] = {}
        for token in str(raw_text or "").split(","):
            part = token.strip()
            if not part or ":" not in part:
                continue
            left, right = part.split(":", 1)
            try:
                slot_idx = int(left.strip())
            except Exception:
                continue
            group_id = str(right or "").strip().lower()
            if slot_idx < 0 or not group_id:
                continue
            out[slot_idx] = group_id
        return out

    @staticmethod
    def _format_cooldown_group_by_slot(values: dict) -> str:
        parsed: list[tuple[int, str]] = []
        for k, v in dict(values or {}).items():
            try:
                slot_idx = int(k)
            except Exception:
                continue
            group_id = str(v or "").strip().lower()
            if slot_idx < 0 or not group_id:
                continue
            parsed.append((slot_idx, group_id))
        return ", ".join(f"{slot}:{gid}" for slot, gid in sorted(parsed, key=lambda t: t[0]))

    @staticmethod
    def _parse_detection_region_overrides(raw_text: str) -> dict[int, str]:
        out: dict[int, str] = {}
        for token in str(raw_text or "").split(","):
            part = token.strip()
            if not part or ":" not in part:
                continue
            left, right = part.split(":", 1)
            try:
                slot_idx = int(left.strip())
            except Exception:
                continue
            mode = str(right or "").strip().lower()
            if slot_idx < 0 or mode not in ("full", "top_left"):
                continue
            out[slot_idx] = mode
        return out

    @staticmethod
    def _format_detection_region_overrides(values: dict) -> str:
        parsed: list[tuple[int, str]] = []
        for k, v in dict(values or {}).items():
            try:
                slot_idx = int(k)
            except Exception:
                continue
            mode = str(v or "").strip().lower()
            if slot_idx < 0 or mode not in ("full", "top_left"):
                continue
            parsed.append((slot_idx, mode))
        return ", ".join(f"{slot}:{mode}" for slot, mode in sorted(parsed, key=lambda t: t[0]))

    @staticmethod
    def _parse_detection_region_overrides_by_form(raw_text: str) -> dict[str, dict[int, str]]:
        out: dict[str, dict[int, str]] = {}
        raw = str(raw_text or "").strip()
        if not raw:
            return out
        for token in raw.split(";"):
            part = token.strip()
            if not part or "=" not in part:
                continue
            left, right = part.split("=", 1)
            form_id = str(left or "").strip().lower()
            if not form_id:
                continue
            overrides = SettingsDialog._parse_detection_region_overrides(right)
            if overrides:
                out[form_id] = overrides
        return out

    @staticmethod
    def _format_detection_region_overrides_by_form(values: dict) -> str:
        chunks: list[str] = []
        parsed: list[tuple[str, str]] = []
        for form_id, overrides in dict(values or {}).items():
            fid = str(form_id or "").strip().lower()
            if not fid:
                continue
            rhs = SettingsDialog._format_detection_region_overrides(overrides)
            if not rhs:
                continue
            parsed.append((fid, rhs))
        for fid, rhs in sorted(parsed, key=lambda t: t[0]):
            chunks.append(f"{fid}={rhs}")
        return "; ".join(chunks)

    def _selected_form_index(self) -> int:
        idx = self._combo_form.currentIndex()
        if idx < 0:
            return -1
        form_id = str(self._combo_form.itemData(idx) or "")
        forms = list(getattr(self._config, "forms", []) or [])
        for i, form in enumerate(forms):
            if str(form.get("id", "") or "").strip().lower() == form_id:
                return i
        return -1

    def _sync_form_controls(self) -> None:
        forms = [dict(f) for f in list(getattr(self._config, "forms", []) or []) if isinstance(f, dict)]
        if not any(str(f.get("id", "") or "").strip().lower() == "normal" for f in forms):
            forms.insert(0, {"id": "normal", "name": "Normal"})
            self._config.forms = forms

        current_form_id = str(self._combo_form.currentData() or "")
        current_active_id = str(getattr(self._config, "active_form_id", "normal") or "normal").strip().lower()
        self._combo_form.blockSignals(True)
        self._combo_active_form.blockSignals(True)
        self._combo_form_present.blockSignals(True)
        self._combo_form_absent.blockSignals(True)
        self._combo_form.clear()
        self._combo_active_form.clear()
        self._combo_form_present.clear()
        self._combo_form_absent.clear()
        for form in forms:
            fid = str(form.get("id", "") or "").strip().lower()
            if not fid:
                continue
            name = str(form.get("name", "") or "").strip() or fid.title()
            self._combo_form.addItem(name, fid)
            self._combo_active_form.addItem(name, fid)
            self._combo_form_present.addItem(name, fid)
            self._combo_form_absent.addItem(name, fid)
        sel_idx = self._combo_form.findData(current_form_id)
        if sel_idx < 0:
            sel_idx = self._combo_form.findData(current_active_id)
        if sel_idx < 0:
            sel_idx = 0 if self._combo_form.count() > 0 else -1
        self._combo_form.setCurrentIndex(sel_idx)
        active_idx = self._combo_active_form.findData(current_active_id)
        if active_idx < 0:
            active_idx = 0 if self._combo_active_form.count() > 0 else -1
        self._combo_active_form.setCurrentIndex(active_idx)

        detector = getattr(self._config, "form_detector", {}) or {}
        detector_type = str(detector.get("type", "off") or "off").strip().lower()
        if detector_type not in ("off", "buff_roi"):
            detector_type = "off"
        type_idx = self._combo_form_detector_type.findData(detector_type)
        if type_idx < 0:
            type_idx = 0
        self._combo_form_detector_type.blockSignals(True)
        self._combo_form_detector_type.setCurrentIndex(type_idx)
        self._combo_form_detector_type.blockSignals(False)

        rois = [dict(r) for r in list(getattr(self._config, "buff_rois", []) or []) if isinstance(r, dict)]
        self._combo_form_detector_roi.blockSignals(True)
        current_roi = str(detector.get("roi_id", "") or "").strip().lower()
        self._combo_form_detector_roi.clear()
        self._combo_form_detector_roi.addItem("Select Buff ROI...", "")
        for roi in rois:
            rid = str(roi.get("id", "") or "").strip().lower()
            if not rid:
                continue
            rname = str(roi.get("name", "") or "").strip() or rid
            self._combo_form_detector_roi.addItem(rname, rid)
        roi_idx = self._combo_form_detector_roi.findData(current_roi)
        self._combo_form_detector_roi.setCurrentIndex(roi_idx if roi_idx >= 0 else 0)
        self._combo_form_detector_roi.blockSignals(False)

        present_form = str(detector.get("present_form", "normal") or "normal").strip().lower()
        absent_form = str(detector.get("absent_form", "normal") or "normal").strip().lower()
        p_idx = self._combo_form_present.findData(present_form)
        a_idx = self._combo_form_absent.findData(absent_form)
        self._combo_form_present.setCurrentIndex(p_idx if p_idx >= 0 else 0)
        self._combo_form_absent.setCurrentIndex(a_idx if a_idx >= 0 else 0)
        self._combo_form_present.blockSignals(False)
        self._combo_form_absent.blockSignals(False)
        self._combo_form.blockSignals(False)
        self._combo_active_form.blockSignals(False)

        selected_idx = self._selected_form_index()
        can_edit = selected_idx >= 0
        self._edit_form_name.setEnabled(can_edit)
        self._btn_remove_form.setEnabled(can_edit and str(self._combo_form.currentData() or "") != "normal")
        if selected_idx >= 0:
            sel_form = forms[selected_idx]
            self._edit_form_name.blockSignals(True)
            self._edit_form_name.setText(str(sel_form.get("name", "") or "").strip())
            self._edit_form_name.blockSignals(False)
        else:
            self._edit_form_name.blockSignals(True)
            self._edit_form_name.setText("")
            self._edit_form_name.blockSignals(False)
        self._spin_form_confirm_frames.blockSignals(True)
        self._spin_form_settle_ms.blockSignals(True)
        self._spin_form_confirm_frames.setValue(int(detector.get("confirm_frames", 2) or 2))
        self._spin_form_settle_ms.setValue(int(detector.get("settle_ms", 200) or 200))
        self._spin_form_confirm_frames.blockSignals(False)
        self._spin_form_settle_ms.blockSignals(False)
        self._label_form_status.setText(
            f"Current: {current_active_id}" if current_active_id else "Current: normal"
        )

    def _on_form_selected(self, _index: int) -> None:
        self._sync_form_controls()

    def _on_add_form(self) -> None:
        forms = [dict(f) for f in list(getattr(self._config, "forms", []) or []) if isinstance(f, dict)]
        existing = {str(f.get("id", "") or "").strip().lower() for f in forms}
        i = 1
        while f"form_{i}" in existing:
            i += 1
        fid = f"form_{i}"
        forms.append({"id": fid, "name": f"Form {i}"})
        self._config.forms = forms
        self._sync_form_controls()
        idx = self._combo_form.findData(fid)
        if idx >= 0:
            self._combo_form.setCurrentIndex(idx)
            self._combo_active_form.setCurrentIndex(self._combo_active_form.findData(fid))
        self._emit_config()

    def _on_remove_form(self) -> None:
        idx = self._selected_form_index()
        if idx < 0:
            return
        forms = [dict(f) for f in list(getattr(self._config, "forms", []) or []) if isinstance(f, dict)]
        fid = str(forms[idx].get("id", "") or "").strip().lower()
        if fid == "normal":
            return
        forms = [f for f in forms if str(f.get("id", "") or "").strip().lower() != fid]
        self._config.forms = forms
        if str(getattr(self._config, "active_form_id", "normal") or "normal").strip().lower() == fid:
            self._config.active_form_id = "normal"
        # Clear detector mappings to removed form.
        detector = getattr(self._config, "form_detector", {}) or {}
        if str(detector.get("present_form", "") or "").strip().lower() == fid:
            detector["present_form"] = "normal"
        if str(detector.get("absent_form", "") or "").strip().lower() == fid:
            detector["absent_form"] = "normal"
        self._config.form_detector = detector
        self._sync_form_controls()
        self._emit_config()

    def _selected_buff_roi_index(self) -> int:
        idx = self._combo_buff_roi.currentIndex()
        if idx < 0:
            return -1
        roi_id = str(self._combo_buff_roi.itemData(idx) or "")
        rois = list(getattr(self._config, "buff_rois", []) or [])
        for i, roi in enumerate(rois):
            if str(roi.get("id", "") or "").strip().lower() == roi_id:
                return i
        return -1

    def _sync_buff_roi_controls(self) -> None:
        rois = list(getattr(self._config, "buff_rois", []) or [])
        current_id = str(self._combo_buff_roi.currentData() or "")
        self._combo_buff_roi.blockSignals(True)
        self._combo_buff_roi.clear()
        for roi in rois:
            if not isinstance(roi, dict):
                continue
            roi_id = str(roi.get("id", "") or "").strip().lower()
            if not roi_id:
                continue
            roi_name = str(roi.get("name", "") or "").strip() or roi_id
            self._combo_buff_roi.addItem(roi_name, roi_id)
        idx = self._combo_buff_roi.findData(current_id)
        if idx < 0:
            idx = 0 if self._combo_buff_roi.count() > 0 else -1
        self._combo_buff_roi.setCurrentIndex(idx)
        self._combo_buff_roi.blockSignals(False)

        selected_idx = self._selected_buff_roi_index()
        enabled = selected_idx >= 0
        self._btn_remove_buff_roi.setEnabled(enabled and len(rois) > 0)
        for w in (
            self._edit_buff_roi_name,
            self._check_buff_roi_enabled,
            self._spin_buff_left,
            self._spin_buff_top,
            self._spin_buff_width,
            self._spin_buff_height,
            self._spin_buff_match_threshold,
            self._spin_buff_confirm_frames,
            self._btn_calibrate_buff_present,
            self._btn_clear_buff_templates,
        ):
            w.setEnabled(enabled)
        if not enabled:
            self._edit_buff_roi_name.setText("")
            self._check_buff_roi_enabled.setChecked(False)
            self._spin_buff_left.setValue(0)
            self._spin_buff_top.setValue(0)
            self._spin_buff_width.setValue(0)
            self._spin_buff_height.setValue(0)
            self._spin_buff_match_threshold.setValue(88)
            self._spin_buff_confirm_frames.setValue(2)
            self._buff_calibration_status.setText("No buff ROI")
            return
        roi = rois[selected_idx]
        self._edit_buff_roi_name.blockSignals(True)
        self._check_buff_roi_enabled.blockSignals(True)
        self._spin_buff_left.blockSignals(True)
        self._spin_buff_top.blockSignals(True)
        self._spin_buff_width.blockSignals(True)
        self._spin_buff_height.blockSignals(True)
        self._spin_buff_match_threshold.blockSignals(True)
        self._spin_buff_confirm_frames.blockSignals(True)
        self._edit_buff_roi_name.setText(str(roi.get("name", "") or "").strip())
        self._check_buff_roi_enabled.setChecked(bool(roi.get("enabled", True)))
        self._spin_buff_left.setValue(int(roi.get("left", 0)))
        self._spin_buff_top.setValue(int(roi.get("top", 0)))
        self._spin_buff_width.setValue(int(roi.get("width", 0)))
        self._spin_buff_height.setValue(int(roi.get("height", 0)))
        self._spin_buff_match_threshold.setValue(
            int(round(float(roi.get("match_threshold", 0.88)) * 100))
        )
        self._spin_buff_confirm_frames.setValue(int(roi.get("confirm_frames", 2)))
        calibration = roi.get("calibration", {})
        if not isinstance(calibration, dict):
            calibration = {}
        has_present = isinstance(calibration.get("present_template"), dict)
        if has_present:
            self._buff_calibration_status.setText("Present calibrated")
        else:
            self._buff_calibration_status.setText("Uncalibrated")
        self._edit_buff_roi_name.blockSignals(False)
        self._check_buff_roi_enabled.blockSignals(False)
        self._spin_buff_left.blockSignals(False)
        self._spin_buff_top.blockSignals(False)
        self._spin_buff_width.blockSignals(False)
        self._spin_buff_height.blockSignals(False)
        self._spin_buff_match_threshold.blockSignals(False)
        self._spin_buff_confirm_frames.blockSignals(False)

    def _on_buff_roi_selected(self, _index: int) -> None:
        self._sync_buff_roi_controls()

    def _on_add_buff_roi(self) -> None:
        rois = [dict(r) for r in list(getattr(self._config, "buff_rois", []) or []) if isinstance(r, dict)]
        existing = {str(r.get("id", "") or "").strip().lower() for r in rois}
        i = 1
        while f"buff_{i}" in existing:
            i += 1
        rid = f"buff_{i}"
        rois.append(
            {
                "id": rid,
                "name": f"Buff {i}",
                "enabled": True,
                "left": 0,
                "top": 0,
                "width": 48,
                "height": 48,
                "match_threshold": 0.88,
                "confirm_frames": 2,
                "calibration": {"present_template": None},
            }
        )
        self._config.buff_rois = rois
        self._sync_buff_roi_controls()
        self._sync_form_controls()
        idx = self._combo_buff_roi.findData(rid)
        if idx >= 0:
            self._combo_buff_roi.setCurrentIndex(idx)
        self._emit_config()

    def _on_remove_buff_roi(self) -> None:
        idx = self._selected_buff_roi_index()
        if idx < 0:
            return
        rois = [dict(r) for r in list(getattr(self._config, "buff_rois", []) or []) if isinstance(r, dict)]
        del rois[idx]
        self._config.buff_rois = rois
        self._sync_buff_roi_controls()
        self._sync_form_controls()
        self._emit_config()

    def _on_calibrate_buff_present_clicked(self) -> None:
        idx = self._selected_buff_roi_index()
        if idx < 0:
            return
        roi_id = str(self._config.buff_rois[idx].get("id", "") or "").strip().lower()
        if roi_id:
            self.calibrate_buff_present_requested.emit(roi_id)

    def _on_clear_buff_templates_clicked(self) -> None:
        idx = self._selected_buff_roi_index()
        if idx < 0:
            return
        roi = self._config.buff_rois[idx]
        calibration = roi.get("calibration", {})
        if not isinstance(calibration, dict):
            calibration = {}
        calibration["present_template"] = None
        roi["calibration"] = calibration
        self._sync_buff_roi_controls()
        self._emit_config()

    def _on_detection_changed(self) -> None:
        self._config.polling_fps = max(1, min(240, self._spin_polling_fps.value()))
        self._config.cooldown_min_duration_ms = max(0, min(10000, self._spin_cooldown_min_ms.value()))
        region = (self._combo_detection_region.currentData() or "top_left")
        if region not in ("full", "top_left"):
            region = "top_left"
        self._config.detection_region = region
        self._config.brightness_drop_threshold = self._spin_brightness_drop.value()
        self._config.cooldown_pixel_fraction = self._slider_pixel_fraction.value() / 100.0
        self._pixel_fraction_label.setText(f"{self._config.cooldown_pixel_fraction:.2f}")
        self._config.cooldown_change_pixel_fraction = self._slider_change_pixel_fraction.value() / 100.0
        self._change_pixel_fraction_label.setText(f"{self._config.cooldown_change_pixel_fraction:.2f}")
        self._config.cooldown_change_ignore_by_slot = self._parse_slot_index_list(
            self._edit_cooldown_change_ignore_by_slot.text()
        )
        self._config.cooldown_group_by_slot = self._parse_cooldown_group_by_slot(
            self._edit_cooldown_group_by_slot.text()
        )
        self._config.detection_region_overrides = self._parse_detection_region_overrides(
            self._edit_detection_region_overrides.text()
        )
        self._config.detection_region_overrides_by_form = (
            self._parse_detection_region_overrides_by_form(
                self._edit_detection_region_overrides_by_form.text()
            )
        )
        self._config.glow_enabled = self._check_glow_enabled.isChecked()
        glow_mode = str(self._combo_glow_mode.currentData() or "color").strip().lower()
        if glow_mode not in ("color", "hybrid_motion"):
            glow_mode = "color"
        self._config.glow_mode = glow_mode
        self._config.glow_ring_thickness_px = self._spin_glow_ring_thickness.value()
        self._config.glow_value_delta = self._spin_glow_value_delta.value()
        self._config.glow_value_delta_by_slot = self._parse_glow_value_delta_by_slot(
            self._edit_glow_value_delta_by_slot.text()
        )
        self._config.glow_saturation_min = self._spin_glow_saturation_min.value()
        self._config.glow_confirm_frames = self._spin_glow_confirm_frames.value()
        self._config.glow_ring_fraction = self._slider_glow_ring_fraction.value() / 100.0
        self._config.glow_ring_fraction_by_slot = self._parse_glow_ring_fraction_by_slot(
            self._edit_glow_ring_fraction_by_slot.text()
        )
        self._config.glow_red_ring_fraction = self._slider_glow_red_ring_fraction.value() / 100.0
        self._config.glow_override_cooldown_by_slot = self._parse_slot_index_list(
            self._edit_glow_override_cooldown_by_slot.text()
        )
        y_min = self._spin_glow_yellow_hue_min.value()
        y_max = self._spin_glow_yellow_hue_max.value()
        if y_min > y_max:
            y_max = y_min
        self._config.glow_yellow_hue_min = y_min
        self._config.glow_yellow_hue_max = y_max
        self._config.glow_red_hue_max_low = self._spin_glow_red_hue_max_low.value()
        self._config.glow_red_hue_min_high = self._spin_glow_red_hue_min_high.value()
        self._glow_ring_fraction_label.setText(f"{self._config.glow_ring_fraction:.2f}")
        self._glow_red_ring_fraction_label.setText(f"{self._config.glow_red_ring_fraction:.2f}")
        self._config.lock_ready_while_cast_bar_active = self._check_lock_ready_cast_bar.isChecked()
        self._config.cast_bar_region = {
            "enabled": self._check_cast_bar_enabled.isChecked(),
            "left": self._spin_cast_bar_left.value(),
            "top": self._spin_cast_bar_top.value(),
            "width": self._spin_cast_bar_width.value(),
            "height": self._spin_cast_bar_height.value(),
        }
        self._config.cast_bar_activity_threshold = float(self._spin_cast_bar_activity.value())
        buff_idx = self._selected_buff_roi_index()
        if buff_idx >= 0 and buff_idx < len(self._config.buff_rois):
            roi = self._config.buff_rois[buff_idx]
            roi["name"] = (self._edit_buff_roi_name.text() or "").strip() or str(
                roi.get("id", "") or "Buff"
            )
            roi["enabled"] = self._check_buff_roi_enabled.isChecked()
            roi["left"] = self._spin_buff_left.value()
            roi["top"] = self._spin_buff_top.value()
            roi["width"] = self._spin_buff_width.value()
            roi["height"] = self._spin_buff_height.value()
            roi["match_threshold"] = self._spin_buff_match_threshold.value() / 100.0
            roi["confirm_frames"] = self._spin_buff_confirm_frames.value()
            calibration = roi.get("calibration", {})
            if not isinstance(calibration, dict):
                calibration = {}
            calibration.setdefault("present_template", None)
            roi["calibration"] = calibration
            combo_idx = self._combo_buff_roi.currentIndex()
            if combo_idx >= 0:
                self._combo_buff_roi.setItemText(combo_idx, str(roi["name"]))
        form_idx = self._selected_form_index()
        if form_idx >= 0 and form_idx < len(self._config.forms):
            form = self._config.forms[form_idx]
            form["name"] = (self._edit_form_name.text() or "").strip() or str(
                form.get("id", "") or "Form"
            )
            combo_idx = self._combo_form.currentIndex()
            if combo_idx >= 0:
                self._combo_form.setItemText(combo_idx, str(form["name"]))
            active_idx = self._combo_active_form.findData(str(form.get("id", "") or "").strip().lower())
            if active_idx >= 0:
                self._combo_active_form.setItemText(active_idx, str(form["name"]))
            present_idx = self._combo_form_present.findData(str(form.get("id", "") or "").strip().lower())
            if present_idx >= 0:
                self._combo_form_present.setItemText(present_idx, str(form["name"]))
            absent_idx = self._combo_form_absent.findData(str(form.get("id", "") or "").strip().lower())
            if absent_idx >= 0:
                self._combo_form_absent.setItemText(absent_idx, str(form["name"]))
        self._config.active_form_id = str(self._combo_active_form.currentData() or "normal").strip().lower() or "normal"
        detector_type = str(self._combo_form_detector_type.currentData() or "off").strip().lower()
        if detector_type == "buff_roi":
            self._config.form_detector = {
                "type": "buff_roi",
                "roi_id": str(self._combo_form_detector_roi.currentData() or "").strip().lower(),
                "present_form": str(self._combo_form_present.currentData() or "normal").strip().lower() or "normal",
                "absent_form": str(self._combo_form_absent.currentData() or "normal").strip().lower() or "normal",
                "confirm_frames": int(self._spin_form_confirm_frames.value()),
                "settle_ms": int(self._spin_form_settle_ms.value()),
            }
        else:
            self._config.form_detector = {}
        active_form = str(getattr(self._config, "active_form_id", "normal") or "normal").strip().lower() or "normal"
        self._label_form_status.setText(f"Current: {active_form}")
        self._btn_calibrate.setText(f"Calibrate Baselines ({active_form})")
        self._emit_config()

    def _start_rebind_capture(self, target: str, button: QPushButton) -> None:
        if self._capture_bind_thread is not None and self._capture_bind_thread.isRunning():
            return
        self._capture_bind_target = target
        button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._capture_bind_thread = CaptureOneKeyThread(self)
        self._capture_bind_thread.captured.connect(self._on_rebind_captured)
        self._capture_bind_thread.cancelled.connect(self._on_rebind_cancelled)
        self._capture_bind_thread.finished.connect(self._on_rebind_finished)
        self._install_rebind_event_filter()
        self._sync_automation_profile_controls()
        self._capture_bind_thread.start()

    def _on_rebind_toggle_clicked(self) -> None:
        self._start_rebind_capture("toggle_bind", self._btn_toggle_bind)

    def _on_rebind_single_fire_clicked(self) -> None:
        self._start_rebind_capture("single_fire_bind", self._btn_single_fire_bind)

    def _clear_rebind(self, target: str) -> None:
        if target not in ("toggle_bind", "single_fire_bind"):
            return
        active = self._config.get_active_priority_profile()
        if str(active.get(target, "") or "") == "":
            return
        active[target] = ""
        self._sync_automation_profile_controls()
        self._emit_config()

    def _on_rebind_toggle_cleared(self, _pos) -> None:
        self._clear_rebind("toggle_bind")

    def _on_rebind_single_fire_cleared(self, _pos) -> None:
        self._clear_rebind("single_fire_bind")

    def _is_bind_in_use_elsewhere(self, bind: str, field_name: str) -> bool:
        bind = normalize_bind(bind)
        if not bind:
            return False
        active_id = self._config.active_priority_profile_id
        for p in self._config.priority_profiles:
            if str(p.get("id", "") or "") == active_id:
                continue
            if bind == normalize_bind(str(p.get("toggle_bind", "") or "")):
                return True
            if bind == normalize_bind(str(p.get("single_fire_bind", "") or "")):
                return True
        active = self._config.get_active_priority_profile()
        if field_name == "toggle_bind" and bind == normalize_bind(str(active.get("single_fire_bind", "") or "")):
            return True
        if field_name == "single_fire_bind" and bind == normalize_bind(str(active.get("toggle_bind", "") or "")):
            return True
        return False

    def _on_rebind_captured(self, bind_str: str) -> None:
        key = normalize_bind(bind_str)
        if not key:
            self._on_rebind_cancelled()
            return
        if key == "escape":
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
        self._remove_rebind_event_filter()
        self._capture_bind_thread = None
        self._capture_bind_target = None
        self._sync_automation_profile_controls()

    def _install_rebind_event_filter(self) -> None:
        if self._rebind_event_filter_installed:
            return
        app = QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self)
        self._rebind_event_filter_installed = True

    def _remove_rebind_event_filter(self) -> None:
        if not self._rebind_event_filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._rebind_event_filter_installed = False

    def eventFilter(self, watched, event):  # type: ignore[override]
        capture_active = self._capture_bind_thread is not None and self._capture_bind_thread.isRunning()
        if capture_active:
            etype = event.type()
            if etype in (
                QEvent.Type.ShortcutOverride,
                QEvent.Type.KeyPress,
                QEvent.Type.KeyRelease,
            ):
                return True
        return super().eventFilter(watched, event)

    def _on_min_delay_changed(self, value: int) -> None:
        self._config.min_press_interval_ms = max(50, min(2000, value))
        self._emit_config()

    def _on_gcd_ms_changed(self, value: int) -> None:
        self._config.gcd_ms = max(500, min(3000, value))
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
                "priority_items": [],
                "manual_actions": [],
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
                "priority_items": list(source.get("priority_items", [])),
                "manual_actions": list(source.get("manual_actions", [])),
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

    def _on_queue_keys_changed(self) -> None:
        raw = (self._edit_queue_keys.text() or "").strip()
        keys = [k.strip().lower() for k in raw.split(",") if k.strip()]
        self._config.queue_whitelist = keys
        self._emit_config()

    def _on_queue_timeout_changed(self, value: int) -> None:
        self._config.queue_timeout_ms = max(1000, min(30000, value))
        self._emit_config()

    def _on_queue_fire_delay_changed(self, value: int) -> None:
        self._config.queue_fire_delay_ms = max(0, min(300, value))
        self._emit_config()

    def _on_calibrate_clicked(self) -> None:
        self.calibrate_requested.emit()

    def closeEvent(self, event) -> None:
        self._remove_rebind_event_filter()
        event.accept()
        self.hide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._status_update_timer.start()

    def show_or_raise(self) -> None:
        self.sync_from_config()
        self._update_status_bar()
        if self.isVisible():
            self._resize_to_fit_content()
            self.raise_()
            self.activateWindow()
        else:
            self.show()
            QApplication.processEvents()
            self._resize_to_fit_content()

    def _resize_to_fit_content(self) -> None:
        """Size the dialog so content fits without scrollbars when there is room on screen."""
        content = self._scroll_content
        content.adjustSize()
        sh = content.sizeHint()
        ch = max(sh.height(), 400)
        cw = max(sh.width(), self.minimumWidth())
        status_h = 28
        frame_margin = 48
        preferred_w = cw + frame_margin + 40
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
