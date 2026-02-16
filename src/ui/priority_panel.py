"""Priority panel — automation toggle, next intention, and drag-drop priority list."""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, QMimeData, QPoint, pyqtSignal
from PyQt6.QtGui import QDrag
from PyQt6.QtWidgets import (
    QCheckBox,
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


class PriorityItemWidget(QFrame):
    """One row in the priority list: handle, [key], status. Draggable for reorder; drag out to remove."""

    def __init__(self, slot_index: int, rank: int, keybind: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._slot_index = slot_index
        self._rank = rank
        self._keybind = keybind
        self._state = "unknown"
        self._cooldown_remaining: Optional[float] = None
        self._drag_start: Optional[QPoint] = None
        self.setAcceptDrops(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        self._handle_label = QLabel("\u28FF")  # Braille pattern for "handle"
        self._handle_label.setStyleSheet("color: #666;")
        layout.addWidget(self._handle_label)
        layout.addStretch(1)
        self._key_label = QLabel("[?]")
        self._key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._key_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        self._status_label = QLabel("—")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._status_label)
        self.setFixedHeight(44)
        self._update_style()

    @property
    def slot_index(self) -> int:
        return self._slot_index

    def set_rank(self, rank: int) -> None:
        self._rank = rank

    def set_keybind(self, keybind: str) -> None:
        self._keybind = keybind
        self._key_label.setText(f"[{keybind}]")

    def set_state(self, state: str, cooldown_remaining: Optional[float] = None) -> None:
        self._state = state
        self._cooldown_remaining = cooldown_remaining
        if state == "ready":
            self._status_label.setText("READY")
        elif cooldown_remaining is not None:
            self._status_label.setText(f"{cooldown_remaining:.1f}s")
        else:
            self._status_label.setText("NOT READY")
        self._update_style()

    def _update_style(self) -> None:
        bg_ready = "#2d5a2d"
        bg_not_ready = "#5a2d2d"
        text_ready = "#b8e0b8"
        text_not_ready = "#e0b8b8"
        if self._state == "ready":
            bg = bg_ready
            text_color = text_ready
        else:
            bg = bg_not_ready
            text_color = text_not_ready
        self._status_label.setStyleSheet(f"color: {text_color}; font-size: 10px;")
        self.setStyleSheet(
            f"PriorityItemWidget {{ background: {bg}; border: 1px solid #444; }}"
        )
        self._handle_label.setStyleSheet("color: #888;")
        self._key_label.setStyleSheet(f"color: {text_color}; font-weight: bold; font-size: 14px;")

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
        self._keybinds: list[str] = []  # keybinds[slot_index]
        self._states_by_index: dict[int, tuple[str, Optional[float]]] = {}
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

    def set_order(self, order: list[int]) -> None:
        """Replace the list with the given slot indices and refresh widgets."""
        self._order = list(order)
        self._rebuild_items()

    def get_order(self) -> list[int]:
        return list(self._order)

    def update_states(self, states: list[dict]) -> None:
        """Update status (READY/cooldown) for each item from state_updated."""
        by_index = {s["index"]: (s.get("state", "unknown"), s.get("cooldown_remaining")) for s in states}
        self._states_by_index = by_index
        for w in self._item_widgets:
            state, cd = by_index.get(w.slot_index, ("unknown", None))
            w.set_state(state, cd)

    def _rebuild_items(self) -> None:
        for w in self._item_widgets:
            w.deleteLater()
        self._item_widgets.clear()
        for rank, slot_index in enumerate(self._order, 1):
            keybind = self._keybinds[slot_index] if slot_index < len(self._keybinds) else "?"
            w = PriorityItemWidget(slot_index, rank, keybind or "?", self._list_container)
            state, cd = self._states_by_index.get(slot_index, ("unknown", None))
            w.set_state(state, cd)
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
    """Right-side panel: automation toggle, last action, next intention, priority list."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedWidth(200)
        self.setStyleSheet("PriorityPanel { background-color: #252525; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._check_automation = QCheckBox("Automation ON/OFF")
        self._check_automation.setChecked(False)
        layout.addWidget(self._check_automation)

        last_action_group = QGroupBox("Last Action")
        last_action_layout = QVBoxLayout(last_action_group)
        self._last_action_label = QLabel("—")
        self._last_action_label.setStyleSheet("color: #888; font-size: 11px;")
        last_action_layout.addWidget(self._last_action_label)
        layout.addWidget(last_action_group)

        next_intention_group = QGroupBox("Next Intention")
        next_intention_layout = QVBoxLayout(next_intention_group)
        self._next_intention_label = QLabel("—")
        self._next_intention_label.setStyleSheet("color: #888; font-size: 11px;")
        next_intention_layout.addWidget(self._next_intention_label)
        layout.addWidget(next_intention_group)

        priority_group = QGroupBox("Priority")
        priority_group_layout = QVBoxLayout(priority_group)
        self._priority_list = PriorityListWidget(self)
        priority_group_layout.addWidget(self._priority_list, 1)
        layout.addWidget(priority_group, 1)

    @property
    def automation_check(self) -> QCheckBox:
        return self._check_automation

    @property
    def last_action_label(self) -> QLabel:
        return self._last_action_label

    @property
    def next_intention_label(self) -> QLabel:
        return self._next_intention_label

    @property
    def priority_list(self) -> PriorityListWidget:
        return self._priority_list
