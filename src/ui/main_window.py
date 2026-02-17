"""Main application window — streamlined for gameplay (enable bar, preview, slots, last action, next intention, priority, status bar)."""

from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics, QImage, QPixmap, QKeySequence
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

import numpy as np

from src.models import AppConfig, BoundingBox
from src.ui.priority_panel import (
    MIME_PRIORITY_ITEM,
    PriorityPanel,
    SlotButton,
)
from src.automation.global_hotkey import format_bind_for_display

if TYPE_CHECKING:
    from src.automation.key_sender import KeySender

# Theme and accent colors (used when setting dynamic styles not in QSS)
KEY_CYAN = "#66eeff"
KEY_GREEN = "#88ff88"
KEY_YELLOW = "#eecc55"
KEY_BLUE = "#7db5ff"

SECTION_BG = "#252535"
SECTION_BG_DARK = "#1e1e2e"
SECTION_BORDER = "#3a3a4a"


def _load_main_window_theme() -> str:
    """Load dark theme QSS for the main window."""
    try:
        from src.ui.themes import load_theme

        return load_theme("dark")
    except Exception:
        return ""


class _SlotStatesRow(QWidget):
    """Horizontal row of slot buttons that stay square and fit the left column width."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(3)
        self._buttons: list[SlotButton] = []
        self._gap = 3

    def set_buttons(self, buttons: list[SlotButton]) -> None:
        for b in self._buttons:
            b.setParent(None)
            b.deleteLater()
        self._buttons = list(buttons)
        for b in self._buttons:
            b.setParent(self)
            self._layout.addWidget(b)
        self._update_sizes()

    def _update_sizes(self) -> None:
        n = len(self._buttons)
        if n == 0:
            return
        w = self.width()
        if w <= 0:
            return
        total_gap = (n - 1) * self._gap
        # Keep this row height stable; very large squares can push the lower panel
        # over the scroll threshold and cause resize/scrollbar oscillation.
        side = max(24, min(34, (w - total_gap) // n))
        for b in self._buttons:
            b.setFixedSize(side, side)

    def minimumSizeHint(self) -> QSize:
        n = len(self._buttons)
        if n == 0:
            return super().minimumSizeHint()
        # Report a small width so the left panel can shrink when window is narrowed.
        min_side = 24
        total_gap = (n - 1) * self._gap
        return QSize(min_side * n + total_gap, min_side)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_sizes()


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
                slot_index = int(
                    event.mimeData().data(MIME_PRIORITY_ITEM).data().decode()
                )
                self._on_priority_drop_remove(slot_index)
            except (ValueError, TypeError):
                pass
        event.acceptProposedAction()


class _ActionEntryRow(QWidget):
    """One row: key (colored), name + status, time. Used for Last Action and Next Intention."""

    def __init__(
        self,
        key: str,
        name: str,
        status: str,
        time_text: str = "",
        key_color: str = KEY_CYAN,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("actionEntryRow")
        self.setStyleSheet(
            f"background: {SECTION_BG}; border-radius: 3px; padding: 4px 6px;"
        )
        self.setMinimumHeight(52)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)
        self._key_label = QLabel(key)
        self._key_label.setObjectName("actionKey")
        self._key_label.setStyleSheet(
            f"font-family: monospace; font-size: 14px; font-weight: bold; color: {key_color}; min-width: 24px;"
        )
        self._key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._key_label)
        info = QVBoxLayout()
        info.setSpacing(2)
        self._name_label = QLabel(name)
        self._name_label.setObjectName("actionName")
        self._name_label.setStyleSheet("font-size: 11px; color: #ccc;")
        self._name_label.setMinimumWidth(0)
        self._name_label.setMinimumHeight(18)
        self._name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self._name_label.setWordWrap(False)
        info.addWidget(self._name_label)
        self._status_label = QLabel(status)
        self._status_label.setObjectName("actionMeta")
        self._status_label.setMinimumHeight(14)
        self._status_label.setStyleSheet(
            "font-size: 9px; color: #666; font-family: monospace;"
        )
        info.addWidget(self._status_label)
        layout.addLayout(info, 1)
        self._time_label = QLabel(time_text)
        self._time_label.setObjectName("actionTime")
        self._time_label.setStyleSheet(
            "font-size: 9px; color: #555; font-family: monospace;"
        )
        self._time_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        # Keep row geometry stable while this text updates every 100 ms.
        self._time_label.setFixedWidth(
            max(42, QFontMetrics(self._time_label.font()).horizontalAdvance("0000.0s"))
        )
        layout.addWidget(self._time_label)

    def set_time(self, text: str) -> None:
        self._time_label.setText(text)

    def set_content(
        self, key: str, name: str, status: str, key_color: str = KEY_CYAN
    ) -> None:
        self._key_label.setText(key)
        self._key_label.setStyleSheet(
            f"font-family: monospace; font-size: 14px; font-weight: bold; color: {key_color}; min-width: 24px;"
        )
        self._name_label.setText(name)
        self._status_label.setText(status)


class LastActionHistoryWidget(QWidget):
    """Last Action section: sent actions with fixed duration (time to fire). N placeholder rows when empty; no live counter."""

    def __init__(
        self,
        max_rows: int = 3,
        parent: Optional[QWidget] = None,
        show_title: bool = True,
    ):
        super().__init__(parent)
        self._max_rows = max(1, max_rows)
        self._entries: list[tuple[QWidget, QGraphicsOpacityEffect]] = (
            []
        )  # (row, opacity_effect) — time is fixed per row
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)
        if show_title:
            title = QLabel("LAST ACTION")
            title.setObjectName("sectionTitle")
            title.setFixedHeight(28)
            title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            title.setStyleSheet(
                "font-family: monospace; font-size: 10px; color: #666; font-weight: bold; letter-spacing: 1.5px;"
            )
            layout.addWidget(title)
        self._rows_container = QVBoxLayout()
        self._rows_container.setSpacing(4)
        layout.addLayout(self._rows_container)
        self._placeholder_rows: list[QWidget] = []
        for _ in range(self._max_rows):
            ph = _ActionEntryRow(
                "—", "No actions recorded", "", "", key_color="#555", parent=self
            )
            ph.setStyleSheet(ph.styleSheet() + " opacity: 0.7;")
            self._placeholder_rows.append(ph)
            self._rows_container.addWidget(ph)

    def set_max_rows(self, n: int) -> None:
        n = max(1, min(10, n))
        if n < self._max_rows:
            for i in range(self._max_rows - n):
                ph = self._placeholder_rows.pop()
                self._rows_container.removeWidget(ph)
                ph.deleteLater()
            while len(self._entries) > n:
                row, eff = self._entries.pop()
                self._rows_container.removeWidget(row)
                row.deleteLater()
        elif n > self._max_rows:
            for i in range(n - self._max_rows):
                ph = _ActionEntryRow(
                    "—", "No actions recorded", "", "", key_color="#555", parent=self
                )
                ph.setStyleSheet(ph.styleSheet() + " opacity: 0.7;")
                self._placeholder_rows.append(ph)
                self._rows_container.addWidget(ph)
        self._max_rows = n
        for i, ph in enumerate(self._placeholder_rows):
            ph.setVisible(i >= len(self._entries))
        self._update_opacities()

    def add_entry(
        self, keybind: str, display_name: str, duration_seconds: float
    ) -> None:
        """Add a sent action; duration_seconds is shown once (time since previous fire), not updated."""
        row = _ActionEntryRow(
            keybind,
            display_name or "Unidentified",
            "sent",
            f"{duration_seconds:.1f}s",
            KEY_CYAN,
            self,
        )
        eff = QGraphicsOpacityEffect(self)
        row.setGraphicsEffect(eff)
        self._entries.insert(0, (row, eff))
        self._rows_container.insertWidget(0, row)
        for i in range(min(len(self._entries), len(self._placeholder_rows))):
            self._placeholder_rows[i].hide()
        while len(self._entries) > self._max_rows:
            old_row, old_eff = self._entries.pop()
            self._rows_container.removeWidget(old_row)
            old_row.deleteLater()
            if len(self._entries) < len(self._placeholder_rows):
                self._placeholder_rows[len(self._entries)].show()
        self._update_opacities()

    def _update_opacities(self) -> None:
        for i, (row, eff) in enumerate(self._entries):
            op = max(0.2, 1.0 - (i * 0.25))
            eff.setOpacity(op)


logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "default_config.json"


class MainWindow(QMainWindow):
    """Primary control panel for Cooldown Reader."""

    # Emitted when bounding box changes, so overlay can update
    bounding_box_changed = pyqtSignal(BoundingBox)
    config_changed = pyqtSignal(AppConfig)
    # Emitted when slot layout changes (count, gap, padding) for overlay slot outlines
    slot_layout_changed = pyqtSignal(
        int, int, int
    )  # slot_count, slot_gap_pixels, slot_padding
    # Emitted when overlay visibility is toggled (True = show, False = hide)
    overlay_visibility_changed = pyqtSignal(bool)
    monitor_changed = pyqtSignal(int)
    # Emitted when user chooses "Calibrate This Slot" for a slot index
    calibrate_slot_requested = pyqtSignal(int)
    start_capture_requested = pyqtSignal()

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config
        self._key_sender: Optional["KeySender"] = None
        self._queued_override: Optional[dict] = None
        self._queue_listener: Optional[object] = None
        self._listening_slot_index: Optional[int] = None
        self._slots_recalibrated: set[int] = set(
            getattr(config, "overwritten_baseline_slots", [])
        )
        self._before_save_callback: Optional[Callable[[], None]] = None
        self._last_saved_config: Optional[dict] = None
        self._last_action_sent_time: Optional[float] = (
            None  # for "time since last fire" on Next Intention + duration for new Last Action
        )
        self.setWindowTitle("Cooldown Reader")
        self.setMinimumSize(580, 400)
        # Default height: fit full layout without main scrollbar (generous for DPI/fonts)
        self.resize(800, 700)

        self._build_ui()
        _qss = _load_main_window_theme()
        if _qss:
            self.setStyleSheet(self.styleSheet() + "\n" + _qss)
        self.setStatusBar(QStatusBar())
        self._profile_status_label = QLabel("Profile: —")
        self._profile_status_label.setStyleSheet(
            "font-size: 10px; font-family: monospace; color: #555;"
        )
        self.statusBar().addWidget(self._profile_status_label)
        self._status_message_label = QLabel()
        self._status_message_label.setStyleSheet("color: #555; font-size: 10px;")
        self.statusBar().addWidget(self._status_message_label, 1)
        self._gcd_label = QLabel("Est. GCD: —")
        self._gcd_label.setStyleSheet(
            "font-size: 10px; font-family: monospace; color: #555;"
        )
        self.statusBar().addPermanentWidget(self._gcd_label)
        self._next_intention_timer = QTimer(self)
        self._next_intention_timer.setInterval(100)
        self._next_intention_timer.timeout.connect(self._update_next_intention_time)
        self._connect_signals()
        self._sync_ui_from_config()

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        top_layout = QVBoxLayout(central)
        top_layout.setContentsMargins(16, 16, 16, 16)
        top_layout.setSpacing(14)

        # --- Enable bar ---
        enable_bar = QHBoxLayout()
        enable_bar.setSpacing(10)
        self._btn_automation_toggle = QPushButton()
        self._btn_automation_toggle.setObjectName("enableToggle")
        self._btn_automation_toggle.setMinimumHeight(32)
        self._btn_automation_toggle.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._btn_automation_toggle.clicked.connect(self._on_automation_toggle_clicked)
        self._btn_automation_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        enable_bar.addWidget(self._btn_automation_toggle)
        self._bind_display = QLabel("Toggle: —")
        self._bind_display.setObjectName("bindDisplay")
        self._bind_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        enable_bar.addWidget(self._bind_display)
        top_layout.addLayout(enable_bar)

        # --- Content split: left (fixed top + scroll) | right (priority) ---
        content_split = QHBoxLayout()
        content_split.setSpacing(14)
        left_column = QWidget(central)
        left_column.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        left_column_layout = QVBoxLayout(left_column)
        left_column_layout.setContentsMargins(0, 0, 0, 0)
        left_column_layout.setSpacing(10)

        # Fixed row: Start Capture + Settings (always visible)
        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self._btn_start = QPushButton("▶ Start Capture")
        self._btn_start.setObjectName("btnStartCapture")
        self._btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_start.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        button_row.addWidget(self._btn_start)
        self._btn_settings = QPushButton("⚙ Settings")
        self._btn_settings.setObjectName("btnSettings")
        self._btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        button_row.addWidget(self._btn_settings)
        left_column_layout.addLayout(button_row)

        # Live Preview (fixed, not in scroll)
        preview_frame = QFrame(left_column)
        preview_frame.setObjectName("sectionFrame")
        preview_frame.setStyleSheet(
            f"background: {SECTION_BG}; border: 1px solid {SECTION_BORDER}; border-radius: 4px; padding: 8px;"
        )
        preview_inner = QVBoxLayout(preview_frame)
        preview_inner.setContentsMargins(8, 8, 8, 8)
        title_preview = QLabel("LIVE PREVIEW")
        title_preview.setObjectName("sectionTitle")
        title_preview.setFixedHeight(28)
        title_preview.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        title_preview.setStyleSheet(
            "font-family: monospace; font-size: 10px; color: #666; font-weight: bold; letter-spacing: 1.5px;"
        )
        preview_inner.addWidget(title_preview)
        self._preview_label = QLabel("No capture running")
        self._preview_label.setObjectName("previewLabel")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumHeight(42)
        self._preview_label.setStyleSheet(
            "background: #111; border-radius: 3px; color: #666; font-size: 11px;"
        )
        self._preview_label.setScaledContents(False)
        self._preview_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        preview_inner.addWidget(self._preview_label)
        preview_frame.setMinimumHeight(96)
        preview_frame.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        left_column_layout.addWidget(preview_frame)

        # Slot States row (fixed, not in scroll)
        self._slot_states_row = _SlotStatesRow(left_column)
        self._slot_states_row.setFixedHeight(34)
        self._slot_buttons: list[SlotButton] = []
        left_column_layout.addWidget(self._slot_states_row)

        # Scroll area: only Last Action + Next Intention
        self._left_panel = _LeftPanel(parent=central)
        self._left_panel.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        left_layout = QVBoxLayout(self._left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(14)
        last_action_frame = QFrame()
        last_action_frame.setObjectName("sectionFrameDark")
        last_action_frame.setStyleSheet(
            f"background: {SECTION_BG_DARK}; border: 1px solid {SECTION_BORDER}; border-radius: 4px; padding: 8px;"
        )
        last_action_inner = QVBoxLayout(last_action_frame)
        last_action_inner.setContentsMargins(8, 8, 8, 8)
        title_last = QLabel("LAST ACTION")
        title_last.setObjectName("sectionTitle")
        title_last.setFixedHeight(28)
        title_last.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        title_last.setStyleSheet(
            "font-family: monospace; font-size: 10px; color: #666; font-weight: bold; letter-spacing: 1.5px;"
        )
        last_action_inner.addWidget(title_last)
        history_rows = getattr(self._config, "history_rows", 3)
        self._last_action_history = LastActionHistoryWidget(
            max_rows=history_rows, parent=last_action_frame, show_title=False
        )
        self._last_action_history.setStyleSheet("background: transparent;")
        self._last_action_history.setMinimumHeight(80)
        last_action_inner.addWidget(self._last_action_history)
        last_action_frame.setMinimumHeight(140)
        last_action_frame.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        left_layout.addWidget(last_action_frame)
        next_frame = QFrame()
        next_frame.setObjectName("sectionFrameDark")
        next_frame.setStyleSheet(
            f"background: {SECTION_BG_DARK}; border: 1px solid {SECTION_BORDER}; border-radius: 4px; padding: 8px 10px;"
        )
        next_inner = QVBoxLayout(next_frame)
        next_inner.setContentsMargins(8, 8, 8, 8)
        title_next = QLabel("NEXT INTENTION")
        title_next.setObjectName("sectionTitle")
        title_next.setFixedHeight(28)
        title_next.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        title_next.setStyleSheet(
            "font-family: monospace; font-size: 10px; color: #666; font-weight: bold; letter-spacing: 1.5px;"
        )
        next_inner.addWidget(title_next)
        self._next_intention_row = _ActionEntryRow(
            "—", "no action", "", "", key_color="#555", parent=next_frame
        )
        next_inner.addWidget(self._next_intention_row)
        next_frame.setMinimumHeight(28 + 16 + 52)
        next_frame.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        left_layout.addWidget(next_frame)
        left_layout.addStretch(1)
        # When no capture: show centered play button; when capture running: show Last Action + Next Intention
        scroll_content = QStackedWidget(central)
        scroll_content.setMinimumHeight(220)
        placeholder = QWidget()
        placeholder.setMinimumHeight(220)
        placeholder_layout = QVBoxLayout(placeholder)
        placeholder_layout.setContentsMargins(0, 0, 0, 0)
        placeholder_layout.addStretch(1)
        _play_btn = QPushButton("▶  Start Capture")
        _play_btn.setObjectName("placeholderPlayButton")
        _play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _play_btn.setMinimumSize(200, 56)
        _play_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        font = _play_btn.font()
        font.setPointSize(14)
        font.setWeight(600)
        _play_btn.setFont(font)
        _play_btn.setStyleSheet(
            "QPushButton { background: #2a2a3a; border: 2px solid #4a4a5a; border-radius: 8px; color: #88aacc; padding: 12px 24px; }"
            " QPushButton:hover { background: #333348; border-color: #66eeff; color: #66eeff; }"
        )
        _play_btn.clicked.connect(self.start_capture_requested.emit)
        placeholder_layout.addWidget(_play_btn, 0, Qt.AlignmentFlag.AlignCenter)
        placeholder_layout.addStretch(1)
        scroll_content.addWidget(placeholder)
        scroll_content.addWidget(self._left_panel)
        scroll_content.setCurrentIndex(0)
        self._scroll_content_stack = scroll_content
        left_scroll = QScrollArea()
        left_scroll.setWidget(scroll_content)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        left_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        left_column_layout.addWidget(left_scroll, 1)
        content_split.addWidget(left_column, 1)
        self._priority_panel = PriorityPanel(self)
        self._priority_panel.setFixedWidth(210)
        content_split.addWidget(self._priority_panel, 0)
        self._left_panel.set_drop_remove_callback(self._on_priority_drop_remove)

        top_layout.addLayout(content_split, 1)

    def _connect_signals(self) -> None:
        self._priority_panel.priority_list.order_changed.connect(
            self._on_priority_order_changed
        )
        self._priority_panel.gcd_updated.connect(self._on_gcd_updated)

    def _active_priority_profile(self) -> dict:
        return self._config.get_active_priority_profile()

    def _active_priority_order(self) -> list[int]:
        return list(self._active_priority_profile().get("priority_order", []))

    def _set_priority_list_from_active_profile(self) -> None:
        self._priority_panel.priority_list.blockSignals(True)
        try:
            self._priority_panel.priority_list.set_order(self._active_priority_order())
        finally:
            self._priority_panel.priority_list.blockSignals(False)

    def set_active_priority_profile(
        self, profile_id: str, persist: bool = False
    ) -> None:
        changed = self._config.set_active_priority_profile(profile_id)
        if (
            not changed
            and self._config.active_priority_profile_id
            != (profile_id or "").strip().lower()
        ):
            return
        profile = self._active_priority_profile()
        profile_name = str(profile.get("name", "") or "").strip() or "Default"
        self._profile_status_label.setText(f"Automation: {profile_name}")
        self._set_priority_list_from_active_profile()
        self._update_bind_display()
        if persist:
            self._save_config()

    def _sync_ui_from_config(self) -> None:
        """Set UI to match current config (main window only owns enable, bind display, priority, slots)."""
        while len(self._config.keybinds) < self._config.slot_count:
            self._config.keybinds.append("")
        self._config.automation_enabled = False
        self._update_automation_button_text()
        self._update_bind_display()
        profile_name = (
            str(self._active_priority_profile().get("name", "") or "").strip()
            or "Default"
        )
        self._profile_status_label.setText(f"Automation: {profile_name}")
        self._priority_panel.priority_list.set_keybinds(self._config.keybinds)
        self._priority_panel.priority_list.set_display_names(
            getattr(self._config, "slot_display_names", [])
        )
        self._priority_panel.priority_list.blockSignals(True)
        try:
            self._priority_panel.priority_list.set_order(
                getattr(self._config, "priority_order", [])
            )
        finally:
            self._priority_panel.priority_list.blockSignals(False)
        self._prepopulate_slot_buttons()
        self._last_action_history.set_max_rows(getattr(self._config, "history_rows", 3))
        if CONFIG_PATH.exists():
            self._last_saved_config = copy.deepcopy(self._config.to_dict())
        self._maybe_auto_save()

    def refresh_from_config(self) -> None:
        """Called when config is updated from Settings dialog: refresh slot count, bind display, history rows."""
        self._prepopulate_slot_buttons()
        self._update_automation_button_text()
        self._update_bind_display()
        self._last_action_history.set_max_rows(getattr(self._config, "history_rows", 3))
        profile_name = (
            str(self._active_priority_profile().get("name", "") or "").strip()
            or "Default"
        )
        self._profile_status_label.setText(f"Automation: {profile_name}")
        self._priority_panel.priority_list.set_keybinds(self._config.keybinds)
        self._priority_panel.priority_list.set_display_names(
            getattr(self._config, "slot_display_names", [])
        )
        self._priority_panel.priority_list.blockSignals(True)
        try:
            self._priority_panel.priority_list.set_order(
                getattr(self._config, "priority_order", [])
            )
        finally:
            self._priority_panel.priority_list.blockSignals(False)

    def _maybe_auto_save(self) -> None:
        """If there are unsaved changes (compared to _last_saved_config, excluding automation_enabled), save and show status."""
        if self._last_saved_config is None:
            self._save_config()
            return
        try:
            current = self._config.to_dict()
            current_compare = {
                k: v for k, v in current.items() if k != "automation_enabled"
            }
            last_compare = {
                k: v
                for k, v in self._last_saved_config.items()
                if k != "automation_enabled"
            }
            if current_compare != last_compare:
                self._save_config()
        except Exception:
            self._save_config()

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
                btn = SlotButton(i, self._slot_states_row)
                btn.setObjectName("slotButton")
                btn.setStyleSheet(
                    "border: 1px solid #444; padding: 4px; font-family: monospace; font-size: 10px; font-weight: bold;"
                )
                btn.context_menu_requested.connect(self._show_slot_menu)
                self._slot_buttons.append(btn)
            self._slot_states_row.set_buttons(self._slot_buttons)
        for i, btn in enumerate(self._slot_buttons):
            keybind = (
                self._config.keybinds[i] if i < len(self._config.keybinds) else "?"
            )
            self._apply_slot_button_style(
                btn, "unknown", keybind or "?", None, slot_index=i
            )
        self._priority_panel.priority_list.set_keybinds(self._config.keybinds)
        self._priority_panel.priority_list.update_states(
            [
                {
                    "index": i,
                    "state": "unknown",
                    "keybind": (
                        self._config.keybinds[i]
                        if i < len(self._config.keybinds)
                        else None
                    ),
                    "cooldown_remaining": None,
                }
                for i in range(n)
            ]
        )

    def _update_automation_button_text(self) -> None:
        """Set toggle button to Enabled/Disabled (green/gray) and bind display to Toggle: [key]."""
        self._btn_automation_toggle.setProperty(
            "enabled", "true" if self._config.automation_enabled else "false"
        )
        self._btn_automation_toggle.style().unpolish(self._btn_automation_toggle)
        self._btn_automation_toggle.style().polish(self._btn_automation_toggle)
        if self._config.automation_enabled:
            self._btn_automation_toggle.setText("Enabled")
        else:
            self._btn_automation_toggle.setText("Disabled")
        self._update_bind_display()

    def _update_bind_display(self) -> None:
        profile = self._active_priority_profile()
        toggle_bind = str(profile.get("toggle_bind", "") or "").strip()
        single_fire_bind = str(profile.get("single_fire_bind", "") or "").strip()
        display_toggle = format_bind_for_display(toggle_bind) if toggle_bind else "—"
        display_single = (
            format_bind_for_display(single_fire_bind) if single_fire_bind else "—"
        )
        self._bind_display.setTextFormat(Qt.TextFormat.RichText)
        self._bind_display.setText(
            f"Toggle: <span style='color:{KEY_CYAN}'>{display_toggle}</span>"
            f" | Single: <span style='color:{KEY_CYAN}'>{display_single}</span>"
        )

    def _on_automation_toggle_clicked(self) -> None:
        self._config.automation_enabled = not self._config.automation_enabled
        if not self._config.automation_enabled:
            self._priority_panel.stop_last_action_timer()
            if self._queue_listener is not None and hasattr(
                self._queue_listener, "clear_queue"
            ):
                self._queue_listener.clear_queue()
        self._update_automation_button_text()
        self.config_changed.emit(self._config)

    def toggle_automation(self) -> None:
        """Toggle automation on/off (e.g. from global hotkey)."""
        self._config.automation_enabled = not self._config.automation_enabled
        if not self._config.automation_enabled:
            self._priority_panel.stop_last_action_timer()
            if self._queue_listener is not None and hasattr(
                self._queue_listener, "clear_queue"
            ):
                self._queue_listener.clear_queue()
        self._update_automation_button_text()
        self.config_changed.emit(self._config)

    def set_key_sender(self, key_sender: Optional["KeySender"]) -> None:
        self._key_sender = key_sender

    def _on_priority_order_changed(self, order: list) -> None:
        profile = self._active_priority_profile()
        profile["priority_order"] = list(order)
        self._config.priority_order = list(order)
        self.config_changed.emit(self._config)
        self._maybe_auto_save()

    def _on_gcd_updated(self, gcd_seconds: float) -> None:
        """Update the estimated GCD display in the status bar."""
        self._gcd_label.setText(f"Est. GCD: {gcd_seconds:.2f}s")

    def record_last_action_sent(
        self, keybind: str, timestamp: float, display_name: str = "Unidentified"
    ) -> None:
        """Record a sent action. Duration = time since previous action (only reset here, never when intention appears)."""
        # Elapsed is time between actions; we only update _last_action_sent_time when an action is actually sent
        elapsed = (
            (timestamp - self._last_action_sent_time)
            if self._last_action_sent_time is not None
            else 0.0
        )
        self._last_action_history.add_entry(
            keybind, display_name or "Unidentified", elapsed
        )
        self._last_action_sent_time = (
            timestamp  # reset only on send; Next Intention counter uses this
        )
        self._priority_panel.record_send_timestamp(timestamp)

    def set_next_intention_blocked(
        self, keybind: str, display_name: str = "Unidentified"
    ) -> None:
        """Show next intention as blocked (wrong window)."""
        self._next_intention_row.set_content(
            keybind, display_name or "Unidentified", "ready (window)", KEY_YELLOW
        )

    def set_queued_override(self, q: Optional[dict]) -> None:
        """Update the current spell queue state (dict or None). Next intention row shows queued key when set."""
        logger.debug("set_queued_override called with: %s", q)
        self._queued_override = q

    def set_queue_listener(self, listener: Optional[object]) -> None:
        """Set the spell queue listener so we can clear the queue when automation is toggled off."""
        self._queue_listener = listener

    def set_next_intention_casting_wait(
        self,
        slot_index: Optional[int],
        cast_ends_at: Optional[float],
    ) -> None:
        """Show next intention as waiting for a current cast/channel to finish."""
        name = "cast/channel"
        if slot_index is not None:
            names = getattr(self._config, "slot_display_names", [])
            if slot_index < len(names) and (names[slot_index] or "").strip():
                name = (names[slot_index] or "").strip()
            else:
                name = f"slot {slot_index + 1}"
        if cast_ends_at:
            remaining = max(0.0, cast_ends_at - time.time())
            status = f"waiting: casting ({remaining:.1f}s)"
        else:
            status = "waiting: channeling"
        self._next_intention_row.set_content("…", name, status, KEY_BLUE)

    def _on_priority_drop_remove(self, slot_index: int) -> None:
        """Called when a priority item is dropped on the left panel (remove from list)."""
        self._priority_panel.priority_list.remove_slot(slot_index)
        order = self._priority_panel.priority_list.get_order()
        profile = self._active_priority_profile()
        profile["priority_order"] = list(order)
        self._config.priority_order = list(order)
        self.config_changed.emit(self._config)
        self._maybe_auto_save()

    # Padding (px) around the preview image inside the Live Preview panel
    PREVIEW_PADDING = 12

    def set_capture_running(self, running: bool) -> None:
        """Show Last Action + Next Intention when capture is running; otherwise show the centered play placeholder."""
        self._scroll_content_stack.setCurrentIndex(1 if running else 0)
        if running:
            self._last_action_sent_time = time.time()
            self._next_intention_timer.start()
            self._update_next_intention_time()
        else:
            self._next_intention_timer.stop()
            self._last_action_sent_time = None
            self._next_intention_row.set_time("")

    def _update_next_intention_time(self) -> None:
        """Live counter: time since last action sent. Only resets when an action is sent (record_last_action_sent), not when intention appears."""
        if self._last_action_sent_time is not None:
            self._next_intention_row.set_time(
                f"{time.time() - self._last_action_sent_time:.1f}s"
            )

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
            "casting": "#2a3f66",
            "channeling": "#5a4a1f",
            "locked": "#3f3f3f",
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
        for slot_index in self._active_priority_order():
            if by_index.get(slot_index) == "ready":
                return slot_index
        return None

    def _next_casting_priority_slot(
        self, states: list[dict]
    ) -> tuple[Optional[int], Optional[float]]:
        """First slot in priority order currently casting/channeling and its cast_ends_at."""
        by_index = {s["index"]: s for s in states}
        for slot_index in self._active_priority_order():
            slot = by_index.get(slot_index)
            if not slot:
                continue
            if slot.get("state") in ("casting", "channeling"):
                return slot_index, slot.get("cast_ends_at")
        return None, None

    def _show_slot_menu(self, slot_index: int) -> None:
        """Show context menu: Bind Key, Calibrate This Slot, Rename (identify skill)."""
        if slot_index < 0 or slot_index >= len(self._slot_buttons):
            return
        btn = self._slot_buttons[slot_index]
        menu = QMenu(self)
        menu.addAction("Bind Key", lambda: self._start_listening_for_key(slot_index))
        menu.addAction(
            "Calibrate This Slot",
            lambda: self.calibrate_slot_requested.emit(slot_index),
        )
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
            self._priority_panel.priority_list.set_display_names(
                self._config.slot_display_names
            )
            self.config_changed.emit(self._config)
            self._maybe_auto_save()

    def _start_listening_for_key(self, slot_index: int) -> None:
        """Turn slot button blue and show status; next keypress will bind (or Esc cancel)."""
        self._cancel_listening()
        self._listening_slot_index = slot_index
        if slot_index < len(self._slot_buttons):
            self._slot_buttons[slot_index].setStyleSheet(
                "background-color: #2d2d5a; color: white; border: 1px solid #444; padding: 4px;"
            )
        self._show_status_message(
            f"Press a key to bind to slot {slot_index + 1}... (Esc to cancel)"
        )

    def _cancel_listening(self) -> None:
        """Cancel key-binding mode and revert button / status."""
        if self._listening_slot_index is None:
            return
        idx = self._listening_slot_index
        self._listening_slot_index = None
        self._status_message_label.setText("")
        if idx < len(self._slot_buttons):
            keybind = (
                self._config.keybinds[idx] if idx < len(self._config.keybinds) else "?"
            )
            self._apply_slot_button_style(
                self._slot_buttons[idx], "unknown", keybind or "?", slot_index=idx
            )

    def keyPressEvent(self, event) -> None:
        """Capture key when in bind mode (slot keybind): Esc cancels, any other key binds to the slot."""
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
                self._status_message_label.setText("")
                if idx < len(self._slot_buttons):
                    self._apply_slot_button_style(
                        self._slot_buttons[idx], "unknown", key_str, slot_index=idx
                    )
                self.config_changed.emit(self._config)
                self._maybe_auto_save()
            event.accept()
            return
        super().keyPressEvent(event)

    def update_slot_states(self, states: list[dict]) -> None:
        """Update the slot state indicators (QPushButtons with keybind + state color).

        Args:
            states: List of dicts with keys: index, state, keybind, cooldown_remaining
        """
        # Ignore transient empty payloads while capture is running to avoid
        # rebuilding the slot row and causing visible geometry churn.
        if not states:
            return

        # Pad keybinds so we can index by slot
        while len(self._config.keybinds) < len(states):
            self._config.keybinds.append("")

        if len(self._slot_buttons) != len(states):
            for b in self._slot_buttons:
                b.deleteLater()
            self._slot_buttons.clear()
            for i in range(len(states)):
                btn = SlotButton(i, self._slot_states_row)
                btn.setObjectName("slotButton")
                btn.setStyleSheet(
                    "border: 1px solid #444; padding: 4px; font-family: monospace; font-size: 10px; font-weight: bold;"
                )
                btn.context_menu_requested.connect(self._show_slot_menu)
                self._slot_buttons.append(btn)
            self._slot_states_row.set_buttons(self._slot_buttons)

        for btn, s in zip(self._slot_buttons, states):
            keybind = s.get("keybind")
            if keybind is None and s["index"] < len(self._config.keybinds):
                keybind = self._config.keybinds[s["index"]] or None
            keybind = keybind or "?"
            state = s.get("state", "unknown")
            cd = s.get("cooldown_remaining")
            self._apply_slot_button_style(
                btn, state, keybind, cd, slot_index=s["index"]
            )

        self._priority_panel.priority_list.set_keybinds(self._config.keybinds)
        self._priority_panel.priority_list.update_states(states)
        if self._queued_override:
            keybind = (self._queued_override.get("key") or "?").strip() or "?"

        casting_slot, cast_ends_at = self._next_casting_priority_slot(states)
        if casting_slot is not None:
            self.set_next_intention_casting_wait(casting_slot, cast_ends_at)
            return

        next_slot = self._next_ready_priority_slot(states)
        if next_slot is not None:
            keybind = (
                self._config.keybinds[next_slot]
                if next_slot < len(self._config.keybinds)
                else "?"
            )
            keybind = keybind or "?"
            names = getattr(self._config, "slot_display_names", [])
            slot_name = "Unidentified"
            if self._queued_override.get("source") == "tracked":
                si = self._queued_override.get("slot_index")
                if si is not None and si < len(names) and (names[si] or "").strip():
                    slot_name = (names[si] or "").strip()
            states_by_idx = {s["index"]: s for s in states}
            slot_ready = False
            if self._queued_override.get("source") == "tracked":
                si = self._queued_override.get("slot_index")
                if si is not None:
                    slot_ready = (states_by_idx.get(si) or {}).get("state") == "ready"
            suffix = (
                "queued (waiting)"
                if not slot_ready and self._queued_override.get("source") == "tracked"
                else "queued"
            )
            self._next_intention_row.set_content(keybind, slot_name, suffix, KEY_CYAN)
        else:
            next_slot = self._next_ready_priority_slot(states)
            if next_slot is not None:
                keybind = (
                    self._config.keybinds[next_slot]
                    if next_slot < len(self._config.keybinds)
                    else "?"
                )
                keybind = keybind or "?"
                names = getattr(self._config, "slot_display_names", [])
                slot_name = "Unidentified"
                if next_slot < len(names) and (names[next_slot] or "").strip():
                    slot_name = (names[next_slot] or "").strip()
                if not self._config.automation_enabled:
                    suffix = "ready (paused)"
                    color = KEY_YELLOW
                elif (
                    self._key_sender is None
                    or not self._key_sender.is_target_window_active()
                ):
                    suffix = "ready (window)"
                    color = KEY_YELLOW
                else:
                    suffix = "ready — next"
                    color = KEY_GREEN
                self._next_intention_row.set_content(keybind, slot_name, suffix, color)
            else:
                self._next_intention_row.set_content("—", "no action", "", "#555")

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
        """Persist current config to JSON and show status message."""
        try:
            if self._before_save_callback:
                self._before_save_callback()
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump(self._config.to_dict(), f, indent=2)
            logger.info(f"Config saved to {CONFIG_PATH}")
            self._last_saved_config = copy.deepcopy(self._config.to_dict())
            self._show_status_message("Settings saved", 2000)
        except Exception as e:
            logger.error(f"Config save failed: {e}")
            self._show_status_message("Save failed", 3000)

    def show_status_message(self, text: str, timeout_ms: int = 0) -> None:
        """Show text in the status bar to the right of the Settings button. If timeout_ms > 0, clear after that many ms."""
        self._status_message_label.setText(text)
        if timeout_ms > 0:
            QTimer.singleShot(
                timeout_ms, lambda: self._status_message_label.setText("")
            )

    def _show_status_message(self, text: str, timeout_ms: int = 0) -> None:
        """Internal alias for show_status_message."""
        self.show_status_message(text, timeout_ms)

    def _on_settings_clicked(self) -> None:
        """No-op; main.py connects _btn_settings to settings_dialog.show_or_raise."""
        pass
