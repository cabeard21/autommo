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

import copy
import json
import logging
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QPoint, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QKeySequence, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
    QSlider,
)

import numpy as np

from src.models import AppConfig, BoundingBox
from src.ui.priority_panel import (
    MIME_PRIORITY_ITEM,
    PriorityPanel,
    SlotButton,
)


class _LeftPanel(QWidget):
    """Left content area; accepts drops of priority items to remove them from the list."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._on_priority_drop_remove: Callable[[int], None] = lambda _: None

    def set_drop_remove_callback(self, callback: Callable[[int], None]) -> None:
        self._on_priority_drop_remove = callback

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_PRIORITY_ITEM):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_PRIORITY_ITEM):
            try:
                slot_index = int(event.mimeData().data(MIME_PRIORITY_ITEM).data().decode())
                self._on_priority_drop_remove(slot_index)
            except (ValueError, TypeError):
                pass
        event.acceptProposedAction()

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
    # Emitted when user chooses "Calibrate This Slot" for a slot index
    calibrate_slot_requested = pyqtSignal(int)

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config
        self._listening_slot_index: Optional[int] = None
        # Slots whose baseline was set by "Calibrate This Slot" (show bold; persisted in config)
        self._slots_recalibrated: set[int] = set(getattr(config, "overwritten_baseline_slots", []))
        self._before_save_callback: Optional[Callable[[], None]] = None
        self._last_saved_config: Optional[dict] = None
        self.setWindowTitle("Cooldown Reader")
        self.setMinimumSize(800, 400)

        self._build_ui()
        self.setStatusBar(QStatusBar())
        self._connect_signals()
        self._sync_ui_from_config()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        top_layout = QHBoxLayout(central)
        top_layout.setContentsMargins(0, 0, 0, 0)

        # Left panel: all existing content (drop target for removing priority items)
        self._left_panel = _LeftPanel(parent=central)
        layout = QVBoxLayout(self._left_panel)

        # --- Monitor selector ---
        monitor_group = QGroupBox("Monitor")
        monitor_layout = QHBoxLayout(monitor_group)
        self._monitor_combo = QComboBox()
        self._monitor_combo.setMaximumWidth(180)
        monitor_layout.addWidget(QLabel("Monitor:"))
        monitor_layout.addWidget(self._monitor_combo)
        monitor_layout.addStretch(1)
        self._check_overlay = QCheckBox("Show Region Overlay")
        monitor_layout.addWidget(self._check_overlay)
        layout.addWidget(monitor_group)

        # --- Capture Region ---
        capture_group = QGroupBox("Capture Region")
        capture_layout = QVBoxLayout(capture_group)

        # Row 1: Region position and size (pixels relative to monitor)
        region_row = QHBoxLayout()
        region_row.setSpacing(4)
        self._spin_top = QSpinBox()
        self._spin_left = QSpinBox()
        self._spin_width = QSpinBox()
        self._spin_height = QSpinBox()
        for i, (spin, label, max_val) in enumerate([
            (self._spin_top, "Top:", 4000),
            (self._spin_left, "Left:", 8000),
            (self._spin_width, "Width:", 2000),
            (self._spin_height, "Height:", 500),
        ]):
            spin.setRange(0, max_val)
            spin.setSingleStep(1)
            region_row.addWidget(QLabel(label))
            region_row.addWidget(spin)
            if i < 3:
                region_row.addStretch(1)
        capture_layout.addLayout(region_row)

        # Row 2: Slot layout (how the region is divided)
        slots_row = QHBoxLayout()
        slots_row.setSpacing(4)
        slots_row.addWidget(QLabel("Slots:"))
        self._spin_slots = QSpinBox()
        self._spin_slots.setRange(1, 24)
        slots_row.addWidget(self._spin_slots)
        slots_row.addStretch(1)
        slots_row.addWidget(QLabel("Gap:"))
        self._spin_gap = QSpinBox()
        self._spin_gap.setRange(0, 20)
        self._spin_gap.setSuffix(" px")
        slots_row.addWidget(self._spin_gap)
        slots_row.addStretch(1)
        slots_row.addWidget(QLabel("Padding:"))
        self._spin_padding = QSpinBox()
        self._spin_padding.setRange(0, 20)
        self._spin_padding.setSuffix(" px")
        slots_row.addWidget(self._spin_padding)
        slots_row.addStretch(1)
        capture_layout.addLayout(slots_row)

        # --- Detection settings ---
        detect_group = QGroupBox("Detection")
        detect_layout = QVBoxLayout(detect_group)

        # Darken threshold: how much a pixel must drop to count as "darkened"
        darken_row = QHBoxLayout()
        darken_row.addWidget(QLabel("Darken:"))
        self._spin_brightness_drop = QSpinBox()
        self._spin_brightness_drop.setRange(0, 255)
        darken_row.addWidget(self._spin_brightness_drop)
        darken_help = QLabel("(?)")
        darken_help.setStyleSheet("color: #666; font-size: 11px;")
        darken_help.setCursor(Qt.CursorShape.PointingHandCursor)
        darken_help.setToolTip(
            "Each pixel is compared to its calibrated baseline. If brightness drops by more than "
            "this amount (0–255), the pixel counts as \"darkened\" (e.g. by a cooldown overlay).\n\n"
            "• Higher value = need a bigger drop to count (stricter, fewer pixels trigger).\n"
            "• Lower value = smaller drop counts (more sensitive; may see cooldown earlier)."
        )
        darken_row.addWidget(darken_help)
        darken_row.addStretch()
        detect_layout.addLayout(darken_row)

        # Trigger fraction: fraction of pixels darkened to mark slot as ON_COOLDOWN
        trigger_row = QHBoxLayout()
        trigger_row.addWidget(QLabel("Trigger:"))
        self._slider_pixel_fraction = QSlider(Qt.Orientation.Horizontal)
        self._slider_pixel_fraction.setRange(10, 90)  # 0.10 to 0.90
        self._slider_pixel_fraction.setSingleStep(5)
        self._slider_pixel_fraction.setMaximumWidth(200)
        self._pixel_fraction_label = QLabel("0.30")
        self._pixel_fraction_label.setMinimumWidth(32)
        trigger_row.addWidget(self._slider_pixel_fraction)
        trigger_row.addWidget(self._pixel_fraction_label)
        trigger_help = QLabel("(?)")
        trigger_help.setStyleSheet("color: #666; font-size: 11px;")
        trigger_help.setCursor(Qt.CursorShape.PointingHandCursor)
        trigger_help.setToolTip(
            "A slot is marked ON COOLDOWN when this fraction of its pixels are \"darkened\" "
            "(compared to the Darken threshold).\n\n"
            "• Slide RIGHT (higher) = need more darkened pixels to trigger (less sensitive; "
            "reduces false cooldowns, but may miss short or partial overlays).\n"
            "• Slide LEFT (lower) = need fewer darkened pixels (more sensitive; triggers earlier, "
            "good for GCD or partial sweeps)."
        )
        trigger_row.addWidget(trigger_help)
        trigger_row.addStretch()
        detect_layout.addLayout(trigger_row)

        # Capture Region and Detection side by side
        capture_detect_row = QHBoxLayout()
        capture_detect_row.addWidget(capture_group, 1)
        capture_detect_row.addWidget(detect_group, 1)
        layout.addLayout(capture_detect_row)

        # --- Controls ---
        controls_layout = QHBoxLayout()
        self._btn_start = QPushButton("Start Capture")
        self._btn_calibrate = QPushButton("Calibrate Baselines")
        self._btn_save_config = QPushButton("Save Settings")
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
        self._preview_label.setMinimumWidth(200)
        self._preview_label.setScaledContents(False)
        self._preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._preview_label.setStyleSheet("background-color: #1a1a1a; color: #666;")
        preview_layout.addWidget(self._preview_label)
        layout.addWidget(preview_group)

        # --- Slot state display ---
        state_group = QGroupBox("Slot States")
        self._state_layout = QHBoxLayout(state_group)
        self._slot_buttons: list[SlotButton] = []
        layout.addWidget(state_group)

        top_layout.addWidget(self._left_panel, 1)
        self._priority_panel = PriorityPanel(self)
        top_layout.addWidget(self._priority_panel, 0)
        self._left_panel.set_drop_remove_callback(self._on_priority_drop_remove)

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
        self._priority_panel.automation_check.toggled.connect(self._on_automation_toggled)
        self._priority_panel.priority_list.order_changed.connect(self._on_priority_order_changed)
        self._priority_panel.bind_captured.connect(self._on_automation_bind_captured)

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
        # Pad keybinds to match slot count
        while len(self._config.keybinds) < self._config.slot_count:
            self._config.keybinds.append("")
        self._spin_brightness_drop.blockSignals(True)
        self._slider_pixel_fraction.blockSignals(True)
        try:
            self._spin_brightness_drop.setValue(self._config.brightness_drop_threshold)
            self._slider_pixel_fraction.setValue(int(self._config.cooldown_pixel_fraction * 100))
            self._pixel_fraction_label.setText(f"{self._config.cooldown_pixel_fraction:.2f}")
        finally:
            self._spin_brightness_drop.blockSignals(False)
            self._slider_pixel_fraction.blockSignals(False)
        self._priority_panel.automation_check.blockSignals(True)
        try:
            self._config.automation_enabled = False
            self._priority_panel.automation_check.setChecked(False)
        finally:
            self._priority_panel.automation_check.blockSignals(False)
        self._priority_panel.priority_list.set_keybinds(self._config.keybinds)
        self._priority_panel.priority_list.set_display_names(getattr(self._config, "slot_display_names", []))
        self._priority_panel.priority_list.blockSignals(True)
        try:
            self._priority_panel.priority_list.set_order(getattr(self._config, "priority_order", []))
        finally:
            self._priority_panel.priority_list.blockSignals(False)
        self._priority_panel.set_toggle_bind(getattr(self._config, "automation_toggle_bind", ""))
        self._prepopulate_slot_buttons()
        if CONFIG_PATH.exists():
            self._last_saved_config = copy.deepcopy(self._config.to_dict())
        self._update_save_button_state()

    def _update_save_button_state(self) -> None:
        """Enable button when there are unsaved changes; disable when in sync. Label is always 'Save Settings'.
        automation_enabled is excluded from the comparison (it resets on launch and toggling it should not affect save state).
        """
        self._btn_save_config.setText("Save Settings")
        if self._last_saved_config is None:
            self._btn_save_config.setEnabled(True)
            return
        try:
            current = self._config.to_dict()
            current_compare = {k: v for k, v in current.items() if k != "automation_enabled"}
            last_compare = {k: v for k, v in self._last_saved_config.items() if k != "automation_enabled"}
            if current_compare == last_compare:
                self._btn_save_config.setEnabled(False)
            else:
                self._btn_save_config.setEnabled(True)
        except Exception:
            self._btn_save_config.setEnabled(True)

    def _prepopulate_slot_buttons(self) -> None:
        """Build slot buttons from config (slot_count + keybinds) in a not-ready state. Used on load before capture runs."""
        n = self._config.slot_count
        while len(self._config.keybinds) < n:
            self._config.keybinds.append("")
        if len(self._slot_buttons) != n:
            for b in self._slot_buttons:
                b.deleteLater()
            self._slot_buttons.clear()
            for i in range(n):
                btn = SlotButton(i, self._state_layout.parentWidget())
                btn.setMinimumWidth(52)
                btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
                btn.setStyleSheet("border: 1px solid #444; padding: 4px;")
                btn.context_menu_requested.connect(self._show_slot_menu)
                self._state_layout.addWidget(btn)
                self._slot_buttons.append(btn)
        for i, btn in enumerate(self._slot_buttons):
            keybind = self._config.keybinds[i] if i < len(self._config.keybinds) else "?"
            self._apply_slot_button_style(btn, "unknown", keybind or "?", None, slot_index=i)
        self._priority_panel.priority_list.set_keybinds(self._config.keybinds)
        self._priority_panel.priority_list.update_states(
            [{"index": i, "state": "unknown", "keybind": self._config.keybinds[i] if i < len(self._config.keybinds) else None, "cooldown_remaining": None} for i in range(n)]
        )

    def _on_bbox_changed(self) -> None:
        self._config.bounding_box = BoundingBox(
            top=self._spin_top.value(),
            left=self._spin_left.value(),
            width=self._spin_width.value(),
            height=self._spin_height.value(),
        )
        self.bounding_box_changed.emit(self._config.bounding_box)
        self._update_save_button_state()

    def _on_detection_changed(self) -> None:
        self._config.brightness_drop_threshold = self._spin_brightness_drop.value()
        self._config.cooldown_pixel_fraction = self._slider_pixel_fraction.value() / 100.0
        self._pixel_fraction_label.setText(f"{self._config.cooldown_pixel_fraction:.2f}")
        self.config_changed.emit(self._config)
        self._update_save_button_state()

    def _on_overlay_toggled(self, checked: bool) -> None:
        self._config.overlay_enabled = checked
        self.overlay_visibility_changed.emit(checked)
        self._update_save_button_state()

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
        self._prepopulate_slot_buttons()
        self._update_save_button_state()

    def _on_automation_toggled(self, checked: bool) -> None:
        self._config.automation_enabled = checked
        self.config_changed.emit(self._config)

    def _on_automation_bind_captured(self, bind_str: str) -> None:
        self._config.automation_toggle_bind = bind_str
        self.config_changed.emit(self._config)
        self._update_save_button_state()

    def _on_priority_order_changed(self, order: list) -> None:
        self._config.priority_order = list(order)
        self.config_changed.emit(self._config)
        self._update_save_button_state()

    def _on_priority_drop_remove(self, slot_index: int) -> None:
        """Called when a priority item is dropped on the left panel (remove from list)."""
        self._priority_panel.priority_list.remove_slot(slot_index)
        self._config.priority_order = self._priority_panel.priority_list.get_order()
        self.config_changed.emit(self._config)
        self._update_save_button_state()

    # Padding (px) around the preview image inside the Live Preview panel
    PREVIEW_PADDING = 12

    def update_preview(self, frame: np.ndarray) -> None:
        """Update the live preview with a captured frame (BGR numpy array).

        Scales the image to fit inside the label with equal padding on all sides,
        preserving aspect ratio (letterbox or pillarbox as needed).
        """
        h, w, ch = frame.shape
        bytes_per_line = ch * w
        rgb = frame[:, :, ::-1].copy()
        qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)

        max_w = max(1, self._preview_label.width() - 2 * self.PREVIEW_PADDING)
        max_h = max(1, self._preview_label.height() - 2 * self.PREVIEW_PADDING)
        scaled = pixmap.scaled(
            max_w,
            max_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview_label.setPixmap(scaled)

    def _apply_slot_button_style(
        self,
        btn: QPushButton,
        state: str,
        keybind: str,
        cooldown_remaining: Optional[float] = None,
        slot_index: int = -1,
    ) -> None:
        """Set slot button text, color, and bold if baseline was recalibrated. Skip if this slot is listening."""
        idx = self._slot_buttons.index(btn) if btn in self._slot_buttons else slot_index
        if idx >= 0 and self._listening_slot_index == idx:
            return  # Keep blue while listening
        display_key = keybind if keybind else "?"
        text = f"[{display_key}]"
        if cooldown_remaining is not None:
            text += f"\n{cooldown_remaining:.1f}s"
        btn.setText(text)
        color = {
            "ready": "#2d5a2d",
            "on_cooldown": "#5a2d2d",
            "gcd": "#5a5a2d",
            "unknown": "#333333",
        }.get(state, "#333333")
        btn.setStyleSheet(
            f"background-color: {color}; color: white; border: 1px solid #444; padding: 4px;"
        )
        font = btn.font()
        font.setBold(idx >= 0 and idx in self._slots_recalibrated)
        btn.setFont(font)

    def _next_ready_priority_slot(self, states: list[dict]) -> Optional[int]:
        """First slot in priority_order that is READY; None if none or automation off."""
        by_index = {s["index"]: s.get("state") for s in states}
        for slot_index in getattr(self._config, "priority_order", []):
            if by_index.get(slot_index) == "ready":
                return slot_index
        return None

    def _show_slot_menu(self, slot_index: int) -> None:
        """Show context menu: Bind Key, Calibrate This Slot, Rename (identify skill)."""
        if slot_index < 0 or slot_index >= len(self._slot_buttons):
            return
        btn = self._slot_buttons[slot_index]
        menu = QMenu(self)
        menu.addAction("Bind Key", lambda: self._start_listening_for_key(slot_index))
        menu.addAction("Calibrate This Slot", lambda: self.calibrate_slot_requested.emit(slot_index))
        menu.addAction("Rename...", lambda: self._rename_slot(slot_index))
        pos = btn.mapToGlobal(QPoint(0, 0)) - QPoint(0, menu.sizeHint().height())
        menu.popup(pos)

    def _rename_slot(self, slot_index: int) -> None:
        """Open modal to set display name for this slot (e.g. skill name)."""
        names = getattr(self._config, "slot_display_names", [])
        while len(names) <= slot_index:
            names.append("")
        current = names[slot_index].strip() or "Unidentified"
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Slot",
            "Skill / action name:",
            text=current if current != "Unidentified" else "",
        )
        if ok and new_name is not None:
            while len(self._config.slot_display_names) <= slot_index:
                self._config.slot_display_names.append("")
            self._config.slot_display_names[slot_index] = new_name.strip()
            self._priority_panel.priority_list.set_display_names(self._config.slot_display_names)
            self.config_changed.emit(self._config)
            self._update_save_button_state()

    def _start_listening_for_key(self, slot_index: int) -> None:
        """Turn slot button blue and show status; next keypress will bind (or Esc cancel)."""
        self._cancel_listening()
        self._listening_slot_index = slot_index
        if slot_index < len(self._slot_buttons):
            self._slot_buttons[slot_index].setStyleSheet(
                "background-color: #2d2d5a; color: white; border: 1px solid #444; padding: 4px;"
            )
        self.statusBar().showMessage(
            f"Press a key to bind to slot {slot_index + 1}... (Esc to cancel)"
        )

    def _cancel_listening(self) -> None:
        """Cancel key-binding mode and revert button / status."""
        if self._listening_slot_index is None:
            return
        idx = self._listening_slot_index
        self._listening_slot_index = None
        self.statusBar().clearMessage()
        if idx < len(self._slot_buttons):
            keybind = self._config.keybinds[idx] if idx < len(self._config.keybinds) else "?"
            self._apply_slot_button_style(
                self._slot_buttons[idx], "unknown", keybind or "?", slot_index=idx
            )

    def keyPressEvent(self, event) -> None:
        """Capture key when in bind mode: Esc cancels, any other key binds to the slot."""
        if self._listening_slot_index is not None:
            if event.key() == Qt.Key.Key_Escape:
                self._cancel_listening()
                event.accept()
                return
            key_str = QKeySequence(event.key()).toString().strip()
            if key_str:
                idx = self._listening_slot_index
                while len(self._config.keybinds) <= idx:
                    self._config.keybinds.append("")
                self._config.keybinds[idx] = key_str
                self._listening_slot_index = None
                self.statusBar().clearMessage()
                if idx < len(self._slot_buttons):
                    self._apply_slot_button_style(
                        self._slot_buttons[idx], "unknown", key_str, slot_index=idx
                    )
                self.config_changed.emit(self._config)
                self._update_save_button_state()
            event.accept()
            return
        super().keyPressEvent(event)

    def update_slot_states(self, states: list[dict]) -> None:
        """Update the slot state indicators (QPushButtons with keybind + state color).

        Args:
            states: List of dicts with keys: index, state, keybind, cooldown_remaining
        """
        # Pad keybinds so we can index by slot
        while len(self._config.keybinds) < len(states):
            self._config.keybinds.append("")

        if len(self._slot_buttons) != len(states):
            for b in self._slot_buttons:
                b.deleteLater()
            self._slot_buttons.clear()
            for i in range(len(states)):
                btn = SlotButton(i, self._state_layout.parentWidget())
                btn.setMinimumWidth(52)
                btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
                btn.setStyleSheet("border: 1px solid #444; padding: 4px;")
                btn.context_menu_requested.connect(self._show_slot_menu)
                self._state_layout.addWidget(btn)
                self._slot_buttons.append(btn)

        for btn, s in zip(self._slot_buttons, states):
            keybind = s.get("keybind")
            if keybind is None and s["index"] < len(self._config.keybinds):
                keybind = self._config.keybinds[s["index"]] or None
            keybind = keybind or "?"
            state = s.get("state", "unknown")
            cd = s.get("cooldown_remaining")
            self._apply_slot_button_style(btn, state, keybind, cd, slot_index=s["index"])

        self._priority_panel.priority_list.set_keybinds(self._config.keybinds)
        self._priority_panel.priority_list.update_states(states)
        next_slot = self._next_ready_priority_slot(states)
        if next_slot is not None and self._config.automation_enabled:
            keybind = (
                self._config.keybinds[next_slot]
                if next_slot < len(self._config.keybinds)
                else "?"
            )
            self._priority_panel.next_intention_label.setText(f"[{keybind or '?'}] — ready")
        else:
            self._priority_panel.next_intention_label.setText("—")

    def set_before_save_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """Set a callback run before writing config (e.g. to sync baselines from analyzer)."""
        self._before_save_callback = callback

    def mark_slots_recalibrated(self, slot_indices: set[int]) -> None:
        """Mark these slots as having recalibrated baselines (show label in bold)."""
        self._slots_recalibrated |= slot_indices

    def mark_slot_recalibrated(self, slot_index: int) -> None:
        """Mark one slot as having its baseline overwritten by Calibrate This Slot (show bold, persist)."""
        self._slots_recalibrated.add(slot_index)
        if slot_index not in self._config.overwritten_baseline_slots:
            self._config.overwritten_baseline_slots.append(slot_index)

    def clear_overwritten_baseline_slots(self) -> None:
        """Clear which slots are marked as overwritten (e.g. after full Calibrate Baselines)."""
        self._slots_recalibrated.clear()
        self._config.overwritten_baseline_slots.clear()

    def _save_config(self) -> None:
        """Persist current config to JSON and show Saved ✓ feedback."""
        try:
            if self._before_save_callback:
                self._before_save_callback()
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump(self._config.to_dict(), f, indent=2)
            logger.info(f"Config saved to {CONFIG_PATH}")
            self._last_saved_config = copy.deepcopy(self._config.to_dict())
            self._btn_save_config.setText("Saved ✓")
            self._btn_save_config.setEnabled(False)
            QTimer.singleShot(2000, self._revert_save_config_button)
        except Exception as e:
            logger.error(f"Config save failed: {e}")
            self._btn_save_config.setText("Save failed")
            self._btn_save_config.setStyleSheet("color: red;")
            QTimer.singleShot(2000, self._revert_save_config_button)

    def _revert_save_config_button(self) -> None:
        self._btn_save_config.setStyleSheet("")
        self._update_save_button_state()

    def populate_monitors(self, monitors: list[dict]) -> None:
        """Fill the monitor dropdown with available monitors."""
        self._monitor_combo.clear()
        for i, m in enumerate(monitors):
            self._monitor_combo.addItem(
                f"Monitor {i + 1}: {m['width']}x{m['height']}", i + 1
            )
