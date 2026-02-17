"""Priority panel — automation toggle, next intention, and drag-drop priority list."""
from __future__ import annotations

import logging
import statistics
import time
from typing import Optional

from PyQt6.QtCore import Qt, QMimeData, QPoint, QTimer, pyqtSignal
from PyQt6.QtGui import QDrag, QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


logger = logging.getLogger(__name__)

MIME_SLOT = "application/x-cooldown-slot"
MIME_PRIORITY_ITEM = "application/x-cooldown-priority-item"
DRAG_THRESHOLD_PX = 5


class SlotButton(QPushButton):
    """Slot state button: right-click for menu, left-drag to add to priority list."""

    context_menu_requested = pyqtSignal(int)

    def __init__(self, slot_index: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._slot_index = slot_index
        self._drag_start: Optional[QPoint] = None

    @property
    def slot_index(self) -> int:
        return self._slot_index

    def contextMenuEvent(self, event) -> None:
        """Right-click: show context menu."""
        self.context_menu_requested.emit(self._slot_index)
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start is None:
            super().mouseMoveEvent(event)
            return
        if (event.position().toPoint() - self._drag_start).manhattanLength() < DRAG_THRESHOLD_PX:
            super().mouseMoveEvent(event)
            return
        mime = QMimeData()
        mime.setData(MIME_SLOT, str(self._slot_index).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)
        self._drag_start = None
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = None
        super().mouseReleaseEvent(event)


def _format_countdown(seconds: Optional[float]) -> str:
    """Format cooldown for display: no decimals, e.g. 12s or 1m."""
    if seconds is None or seconds <= 0:
        return "—"
    secs = int(seconds)
    if secs >= 60:
        return f"{secs // 60}m"
    return f"{secs}s"


class PriorityItemWidget(QFrame):
    """One row: handle + [key] (small), display name, countdown area (fixed width). Draggable for reorder."""

    def __init__(
        self,
        slot_index: int,
        rank: int,
        keybind: str,
        display_name: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._slot_index = slot_index
        self._rank = rank
        self._keybind = keybind
        self._display_name = display_name or "Unidentified"
        self._state = "unknown"
        self._cooldown_remaining: Optional[float] = None
        self._cast_progress: Optional[float] = None
        self._cast_ends_at: Optional[float] = None
        self._drag_start: Optional[QPoint] = None
        self.setAcceptDrops(False)
        self.setObjectName("priorityItem")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(6)
        self._handle_label = QLabel("\u28FF")  # drag handle
        self._handle_label.setObjectName("priorityHandle")
        layout.addWidget(self._handle_label)
        self._rank_label = QLabel(str(rank))
        self._rank_label.setObjectName("priorityRank")
        self._rank_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._rank_label)
        self._key_label = QLabel(f"[{keybind}]")
        self._key_label.setObjectName("priorityKey")
        layout.addWidget(self._key_label)
        self._name_label = QLabel(self._display_name)
        self._name_label.setObjectName("priorityName")
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._name_label.setMinimumWidth(0)
        self._name_label.setMinimumHeight(20)
        self._name_label.setWordWrap(False)
        layout.addWidget(self._name_label, 1)
        self._countdown_label = QLabel("—")
        self._countdown_label.setMinimumWidth(32)
        self._countdown_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        font = QFont("Consolas")
        if not font.exactMatch():
            font = QFont("Courier New")
        font.setPointSize(9)
        self._countdown_label.setFont(font)
        layout.addWidget(self._countdown_label)
        self._remove_btn = QLabel("−")
        self._remove_btn.setObjectName("priorityRemove")
        self._remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._remove_btn)
        self.setMinimumHeight(40)
        self.setFixedHeight(40)
        self._update_style()

    @property
    def slot_index(self) -> int:
        return self._slot_index

    def set_rank(self, rank: int) -> None:
        self._rank = rank
        self._rank_label.setText(str(rank))

    def set_keybind(self, keybind: str) -> None:
        self._keybind = keybind
        self._key_label.setText(f"[{keybind}]")

    def set_display_name(self, name: str) -> None:
        self._display_name = name or "Unidentified"
        self._update_name_elided()

    def _update_name_elided(self) -> None:
        """Set name label to full or elided text to avoid horizontal clipping."""
        w = self._name_label.width()
        if w <= 0:
            self._name_label.setText(self._display_name)
            return
        metrics = QFontMetrics(self._name_label.font())
        elided = metrics.elidedText(self._display_name, Qt.TextElideMode.ElideRight, w)
        self._name_label.setText(elided)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_name_elided()

    def set_state(
        self,
        state: str,
        cooldown_remaining: Optional[float] = None,
        cast_progress: Optional[float] = None,
        cast_ends_at: Optional[float] = None,
    ) -> None:
        self._state = state
        self._cooldown_remaining = cooldown_remaining
        self._cast_progress = cast_progress
        self._cast_ends_at = cast_ends_at
        if state == "casting":
            pct = int(round(max(0.0, min(1.0, cast_progress or 0.0)) * 100))
            self._countdown_label.setText(f"{pct}%")
        elif state == "channeling":
            if cast_ends_at:
                rem = max(0.0, cast_ends_at - time.time())
                self._countdown_label.setText(f"{rem:.1f}s")
            else:
                self._countdown_label.setText("chan")
        else:
            self._countdown_label.setText(_format_countdown(cooldown_remaining))
        self._update_style()

    def _update_style(self) -> None:
        if self._state == "ready":
            state = "ready"
        elif self._state == "casting":
            state = "casting"
        elif self._state == "channeling":
            state = "channeling"
        elif self._state == "locked":
            state = "locked"
        else:
            state = "cooldown"
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)
        text_color = {
            "ready": "#88ff88",
            "casting": "#a0c7ff",
            "channeling": "#ffd37a",
            "locked": "#cccccc",
        }.get(self._state, "#ff8888")
        self._key_label.setStyleSheet(f"color: {text_color};")
        self._name_label.setStyleSheet(f"color: {text_color};")
        self._countdown_label.setStyleSheet(f"color: {text_color};")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start is None:
            super().mouseMoveEvent(event)
            return
        if (event.position().toPoint() - self._drag_start).manhattanLength() < DRAG_THRESHOLD_PX:
            super().mouseMoveEvent(event)
            return
        mime = QMimeData()
        mime.setData(MIME_PRIORITY_ITEM, str(self._slot_index).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_start = None
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = None
        super().mouseReleaseEvent(event)


class PriorityListWidget(QWidget):
    """Vertical list of priority items. Accepts slot drops (add) and priority-item drops (reorder)."""

    order_changed = pyqtSignal(list)  # new list of slot indices

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._order: list[int] = []
        self._keybinds: list[str] = []
        self._display_names: list[str] = []
        self._states_by_index: dict[int, tuple[str, Optional[float], Optional[float], Optional[float]]] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_container)
        layout.addWidget(self._scroll)
        self._item_widgets: list[PriorityItemWidget] = []

    def set_keybinds(self, keybinds: list[str]) -> None:
        self._keybinds = keybinds
        for w in self._item_widgets:
            if w.slot_index < len(keybinds):
                w.set_keybind(keybinds[w.slot_index] or "?")

    def set_display_names(self, names: list[str]) -> None:
        self._display_names = list(names)
        for w in self._item_widgets:
            if w.slot_index < len(names) and names[w.slot_index].strip():
                w.set_display_name(names[w.slot_index].strip())
            else:
                w.set_display_name("Unidentified")

    def set_order(self, order: list[int]) -> None:
        """Replace the list with the given slot indices and refresh widgets."""
        self._order = list(order)
        self._rebuild_items()

    def get_order(self) -> list[int]:
        return list(self._order)

    def update_states(self, states: list[dict]) -> None:
        """Update status (READY/cooldown) for each item from state_updated."""
        by_index = {
            s["index"]: (
                s.get("state", "unknown"),
                s.get("cooldown_remaining"),
                s.get("cast_progress"),
                s.get("cast_ends_at"),
            )
            for s in states
        }
        self._states_by_index = by_index
        for w in self._item_widgets:
            state, cd, cast_progress, cast_ends_at = by_index.get(
                w.slot_index,
                ("unknown", None, None, None),
            )
            w.set_state(state, cd, cast_progress, cast_ends_at)

    def _rebuild_items(self) -> None:
        for w in self._item_widgets:
            w.deleteLater()
        self._item_widgets.clear()
        for rank, slot_index in enumerate(self._order, 1):
            keybind = self._keybinds[slot_index] if slot_index < len(self._keybinds) else "?"
            name = (
                self._display_names[slot_index].strip()
                if slot_index < len(self._display_names) and self._display_names[slot_index].strip()
                else "Unidentified"
            )
            w = PriorityItemWidget(slot_index, rank, keybind or "?", name, self._list_container)
            state, cd, cast_progress, cast_ends_at = self._states_by_index.get(
                slot_index,
                ("unknown", None, None, None),
            )
            w.set_state(state, cd, cast_progress, cast_ends_at)
            self._list_layout.insertWidget(self._list_layout.count() - 1, w)
            self._item_widgets.append(w)

    def _emit_order(self) -> None:
        self.order_changed.emit(self.get_order())

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_SLOT) or event.mimeData().hasFormat(MIME_PRIORITY_ITEM):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        pos = event.position().toPoint()
        if mime.hasFormat(MIME_SLOT):
            slot_index = int(mime.data(MIME_SLOT).data().decode())
            if slot_index in self._order:
                event.acceptProposedAction()
                return
            has_keybind = (
                slot_index < len(self._keybinds) and bool(self._keybinds[slot_index].strip())
            )
            if not has_keybind:
                event.acceptProposedAction()
                return
            self._order.append(slot_index)
            self._rebuild_items()
            self._emit_order()
        elif mime.hasFormat(MIME_PRIORITY_ITEM):
            from_index = int(mime.data(MIME_PRIORITY_ITEM).data().decode())
            if from_index not in self._order:
                event.ignore()
                return
            local_pos = self._list_container.mapFrom(self, pos)
            drop_idx = len(self._item_widgets)
            for i, w in enumerate(self._item_widgets):
                if local_pos.y() < w.y() + w.height() // 2:
                    drop_idx = i
                    break
            try:
                self._order.remove(from_index)
                self._order.insert(drop_idx, from_index)
            except ValueError:
                pass
            self._rebuild_items()
            self._emit_order()
        event.acceptProposedAction()

    def remove_slot(self, slot_index: int) -> None:
        """Remove a slot from the priority list (e.g. dropped outside)."""
        if slot_index in self._order:
            self._order.remove(slot_index)
            self._rebuild_items()
            self._emit_order()


class PriorityPanel(QWidget):
    """Right-side panel: priority list only (Last Action and Next Intention are in main window left column)."""

    gcd_updated = pyqtSignal(float)  # Estimated GCD in seconds

    GCD_WINDOW_SIZE = 20  # Number of recent send timestamps to keep
    GCD_MIN_SAMPLES = 3   # Minimum sends before estimating (need >= 2 intervals)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setStyleSheet("PriorityPanel { background: transparent; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._last_action_keybind: Optional[str] = None
        self._last_action_display_name: str = "Unidentified"
        self._last_action_timestamp: float = 0.0
        self._last_action_timer = QTimer(self)
        self._last_action_timer.setInterval(100)
        self._last_action_timer.timeout.connect(self._on_last_action_timer)
        self._send_timestamps: list[float] = []

        priority_frame = QFrame()
        priority_frame.setObjectName("priorityPanel")
        priority_inner = QVBoxLayout(priority_frame)
        priority_inner.setContentsMargins(8, 8, 8, 8)
        title = QLabel("PRIORITY ↕")
        title.setStyleSheet(
            "font-family: monospace; font-size: 10px; color: #666; font-weight: bold; letter-spacing: 1.5px;"
        )
        priority_inner.addWidget(title)
        self._priority_list = PriorityListWidget(self)
        priority_inner.addWidget(self._priority_list, 1)
        layout.addWidget(priority_frame, 1)

    @property
    def last_action_label(self) -> QLabel:
        """Deprecated: Last Action is now in main window. Returns a dummy label for backwards compat."""
        if not hasattr(self, "_dummy_last_action_label"):
            self._dummy_last_action_label = QLabel("—")
        return self._dummy_last_action_label

    @property
    def next_intention_label(self) -> QLabel:
        """Deprecated: Next Intention is now in main window. Returns a dummy label for backwards compat."""
        if not hasattr(self, "_dummy_next_intention_label"):
            self._dummy_next_intention_label = QLabel("—")
        return self._dummy_next_intention_label

    @property
    def priority_list(self) -> PriorityListWidget:
        return self._priority_list

    def update_last_action_sent(self, keybind: str, timestamp: float, display_name: str = "Unidentified") -> None:
        """Legacy: update label and record for GCD. Prefer record_send_timestamp when main window has its own Last Action display."""
        self._last_action_keybind = keybind
        self._last_action_display_name = display_name or "Unidentified"
        self._last_action_timestamp = timestamp
        self._last_action_label.setText(f"[{keybind}] {self._last_action_display_name}")
        if not self._last_action_timer.isActive():
            self._last_action_timer.start()
        self._on_last_action_timer()
        self.record_send_timestamp(timestamp)

    def record_send_timestamp(self, timestamp: float) -> None:
        """Record a key-send timestamp for GCD estimation. Call from main window when Last Action is displayed there."""
        self._send_timestamps.append(timestamp)
        if len(self._send_timestamps) > self.GCD_WINDOW_SIZE:
            self._send_timestamps = self._send_timestamps[-self.GCD_WINDOW_SIZE:]
        gcd = self._compute_estimated_gcd()
        if gcd is not None:
            self.gcd_updated.emit(gcd)

    def _on_last_action_timer(self) -> None:
        if self._last_action_keybind is None:
            self._last_action_timer.stop()
            return
        elapsed = time.time() - self._last_action_timestamp
        self._last_action_label.setText(f"[{self._last_action_keybind}] {self._last_action_display_name}  {elapsed:.1f}s ago")

    def update_next_intention_blocked(self, keybind: str, display_name: str = "Unidentified") -> None:
        """Set Next Intention to [keybind] display_name — waiting (window)."""
        name = display_name or "Unidentified"
        self._next_intention_label.setText(f"[{keybind}] {name} — waiting (window)")

    def stop_last_action_timer(self) -> None:
        """Stop the 'Xs ago' timer (e.g. when automation is turned off)."""
        self._last_action_timer.stop()

    def _compute_estimated_gcd(self) -> Optional[float]:
        """Estimate the GCD from the median interval between recent key sends.

        Uses the median rather than the mean so that occasional long gaps
        (when no ability was available) are ignored as outliers.
        """
        if len(self._send_timestamps) < self.GCD_MIN_SAMPLES:
            return None
        intervals = [
            self._send_timestamps[i] - self._send_timestamps[i - 1]
            for i in range(1, len(self._send_timestamps))
        ]
        return statistics.median(intervals)

    def reset_gcd_estimate(self) -> None:
        """Clear tracked send timestamps (e.g. when priority list changes)."""
        self._send_timestamps.clear()
