"""Priority panel - automation toggle, next intention, and drag-drop priority list."""
from __future__ import annotations

import logging
import statistics
import time
from typing import Optional

from PyQt6.QtCore import Qt, QMimeData, QPoint, QTimer, pyqtSignal
from PyQt6.QtGui import QDrag, QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.automation.priority_rules import (
    manual_item_is_eligible,
    normalize_activation_rule,
    normalize_ready_source,
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
        return "-"
    secs = int(seconds)
    if secs >= 60:
        return f"{secs // 60}m"
    return f"{secs}s"


class PriorityItemWidget(QFrame):
    """One row: handle + [key] + name + countdown. Draggable for reorder."""
    remove_requested = pyqtSignal(str)

    def __init__(
        self,
        item_key: str,
        item_type: str,
        slot_index: Optional[int],
        action_id: Optional[str],
        activation_rule: str,
        ready_source: str,
        buff_roi_id: str,
        buff_rois: list[dict],
        rank: int,
        keybind: str,
        display_name: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._item_key = item_key
        self._item_type = item_type
        self._slot_index = slot_index
        self._action_id = action_id
        self._activation_rule = normalize_activation_rule(activation_rule)
        self._ready_source = normalize_ready_source(ready_source, item_type)
        self._buff_roi_id = str(buff_roi_id or "").strip().lower()
        self._buff_rois = [dict(r) for r in list(buff_rois or []) if isinstance(r, dict)]
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

        self._handle_label = QLabel("\u28FF")
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

        self._rule_label = QLabel("")
        self._rule_label.setMinimumWidth(28)
        self._rule_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rule_label.setStyleSheet("font-family: monospace; font-size: 9px; color: #d3a75b;")
        layout.addWidget(self._rule_label)

        self._countdown_label = QLabel("-")
        self._countdown_label.setMinimumWidth(32)
        self._countdown_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        font = QFont("Consolas")
        if not font.exactMatch():
            font = QFont("Courier New")
        font.setPointSize(9)
        self._countdown_label.setFont(font)
        layout.addWidget(self._countdown_label)

        self._remove_btn = QLabel("-")
        self._remove_btn.setObjectName("priorityRemove")
        self._remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remove_btn.setToolTip("Remove from priority list")
        layout.addWidget(self._remove_btn)

        self.setMinimumHeight(40)
        self.setFixedHeight(40)
        self._update_style()

    @property
    def item_key(self) -> str:
        return self._item_key

    @property
    def item_type(self) -> str:
        return self._item_type

    @property
    def slot_index(self) -> Optional[int]:
        return self._slot_index

    @property
    def action_id(self) -> Optional[str]:
        return self._action_id

    @property
    def activation_rule(self) -> str:
        return self._activation_rule

    def set_activation_rule(self, activation_rule: str) -> None:
        self._activation_rule = normalize_activation_rule(activation_rule)
        self._update_rule_label()

    def set_ready_source(self, ready_source: str, buff_roi_id: str) -> None:
        self._ready_source = normalize_ready_source(ready_source, self._item_type)
        self._buff_roi_id = str(buff_roi_id or "").strip().lower()
        self._update_rule_label()

    def _buff_name(self, buff_id: str) -> str:
        bid = str(buff_id or "").strip().lower()
        for b in self._buff_rois:
            if str(b.get("id", "") or "").strip().lower() == bid:
                return str(b.get("name", "") or "").strip() or bid
        return bid

    def _update_rule_label(self) -> None:
        tokens: list[str] = []
        if self._item_type == "slot" and self._activation_rule == "dot_refresh":
            tokens.append("DOT")
        if self._ready_source == "buff_present":
            tokens.append(f"B+:{self._buff_name(self._buff_roi_id)}")
        elif self._ready_source == "buff_missing":
            tokens.append(f"B-:{self._buff_name(self._buff_roi_id)}")
        self._rule_label.setText(" ".join(tokens))

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
        w = self._name_label.width()
        if w <= 0:
            self._name_label.setText(self._display_name)
            return
        metrics = QFontMetrics(self._name_label.font())
        self._name_label.setText(metrics.elidedText(self._display_name, Qt.TextElideMode.ElideRight, w))

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
            if self._remove_btn.geometry().contains(event.position().toPoint()):
                self.remove_requested.emit(self._item_key)
                event.accept()
                return
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
        mime.setData(MIME_PRIORITY_ITEM, self._item_key.encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        result = drag.exec(Qt.DropAction.MoveAction)
        if result == Qt.DropAction.IgnoreAction:
            self.remove_requested.emit(self._item_key)
        self._drag_start = None
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self._item_type not in ("manual", "slot"):
            event.ignore()
            return
        menu = QMenu(self)
        rename_action = None
        rebind_action = None
        remove_action = None
        always_action = None
        dot_refresh_action = None
        ready_always_action = None
        slot_ready_action = None
        ready_actions: dict[object, tuple[str, str]] = {}
        if self._item_type == "manual" and self._action_id:
            rename_action = menu.addAction("Rename...")
            rebind_action = menu.addAction("Rebind...")
            menu.addSeparator()
            ready_menu = menu.addMenu("Ready Source")
            ready_always_action = ready_menu.addAction("Always")
            ready_always_action.setCheckable(True)
            ready_always_action.setChecked(self._ready_source == "always")
            for buff in self._buff_rois:
                buff_id = str(buff.get("id", "") or "").strip().lower()
                buff_name = str(buff.get("name", "") or "").strip() or buff_id
                if not buff_id:
                    continue
                a_present = ready_menu.addAction(f"Buff present: {buff_name}")
                a_present.setCheckable(True)
                a_present.setChecked(
                    self._ready_source == "buff_present" and self._buff_roi_id == buff_id
                )
                ready_actions[a_present] = ("buff_present", buff_id)
                a_missing = ready_menu.addAction(f"Buff missing: {buff_name}")
                a_missing.setCheckable(True)
                a_missing.setChecked(
                    self._ready_source == "buff_missing" and self._buff_roi_id == buff_id
                )
                ready_actions[a_missing] = ("buff_missing", buff_id)
            menu.addSeparator()
            remove_action = menu.addAction("Remove")
        elif self._item_type == "slot":
            always_action = menu.addAction("Activation: Always")
            always_action.setCheckable(True)
            always_action.setChecked(self._activation_rule == "always")
            dot_refresh_action = menu.addAction(
                "Activation: DoT refresh (no glow or red; buff gate required, red overrides slot)"
            )
            dot_refresh_action.setCheckable(True)
            dot_refresh_action.setChecked(self._activation_rule == "dot_refresh")
            menu.addSeparator()
            ready_menu = menu.addMenu("Ready Source")
            ready_always_action = ready_menu.addAction("Always")
            ready_always_action.setCheckable(True)
            ready_always_action.setChecked(self._ready_source == "always")
            slot_ready_action = ready_menu.addAction("Use slot icon state")
            slot_ready_action.setCheckable(True)
            slot_ready_action.setChecked(self._ready_source == "slot")
            for buff in self._buff_rois:
                buff_id = str(buff.get("id", "") or "").strip().lower()
                buff_name = str(buff.get("name", "") or "").strip() or buff_id
                if not buff_id:
                    continue
                a_present = ready_menu.addAction(f"Buff present: {buff_name}")
                a_present.setCheckable(True)
                a_present.setChecked(
                    self._ready_source == "buff_present" and self._buff_roi_id == buff_id
                )
                ready_actions[a_present] = ("buff_present", buff_id)
                a_missing = ready_menu.addAction(f"Buff missing: {buff_name}")
                a_missing.setCheckable(True)
                a_missing.setChecked(
                    self._ready_source == "buff_missing" and self._buff_roi_id == buff_id
                )
                ready_actions[a_missing] = ("buff_missing", buff_id)
        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        parent = self.parent()
        while parent is not None and not (
            hasattr(parent, "_on_manual_item_action")
            and hasattr(parent, "_on_slot_item_activation_rule_changed")
        ):
            parent = parent.parent()
        if parent is None:
            return
        if chosen == rename_action and self._action_id:
            parent._on_manual_item_action(self._action_id, "rename")
        elif chosen == rebind_action and self._action_id:
            parent._on_manual_item_action(self._action_id, "rebind")
        elif chosen == remove_action and self._action_id:
            parent._on_manual_item_action(self._action_id, "remove")
        elif chosen == always_action:
            parent._on_slot_item_activation_rule_changed(self._item_key, "always")
        elif chosen == dot_refresh_action:
            parent._on_slot_item_activation_rule_changed(self._item_key, "dot_refresh")
        elif chosen == ready_always_action:
            parent._on_item_ready_source_changed(self._item_key, "always", "")
        elif chosen == slot_ready_action:
            parent._on_item_ready_source_changed(self._item_key, "slot", "")
        elif chosen in ready_actions:
            source, buff_id = ready_actions[chosen]
            parent._on_item_ready_source_changed(self._item_key, source, buff_id)


class PriorityListWidget(QWidget):
    """Vertical list of priority items. Accepts slot drops (add) and priority-item drops (reorder)."""

    items_changed = pyqtSignal(list)
    manual_action_rename_requested = pyqtSignal(str)
    manual_action_rebind_requested = pyqtSignal(str)
    manual_action_remove_requested = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._items: list[dict] = []
        self._keybinds: list[str] = []
        self._display_names: list[str] = []
        self._manual_actions: list[dict] = []
        self._buff_rois: list[dict] = []
        self._buff_states: dict[str, dict] = {}
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

    @staticmethod
    def _item_key(item: dict) -> str:
        if str(item.get("type", "") or "").strip().lower() == "slot":
            return f"slot:{item.get('slot_index')}"
        return f"manual:{str(item.get('action_id', '') or '').strip().lower()}"

    def set_keybinds(self, keybinds: list[str]) -> None:
        self._keybinds = keybinds
        for w in self._item_widgets:
            if w.item_type == "slot" and isinstance(w.slot_index, int) and w.slot_index < len(keybinds):
                w.set_keybind(keybinds[w.slot_index] or "?")

    def set_display_names(self, names: list[str]) -> None:
        self._display_names = list(names)
        for w in self._item_widgets:
            if w.item_type == "slot" and isinstance(w.slot_index, int):
                if w.slot_index < len(names) and names[w.slot_index].strip():
                    w.set_display_name(names[w.slot_index].strip())
                else:
                    w.set_display_name("Unidentified")

    def set_manual_actions(self, actions: list[dict]) -> None:
        self._manual_actions = list(actions)
        self._rebuild_items()

    def set_buff_rois(self, rois: list[dict]) -> None:
        self._buff_rois = [dict(r) for r in list(rois or []) if isinstance(r, dict)]
        self._rebuild_items()

    def set_buff_states(self, states: dict) -> None:
        self._buff_states = {
            str(k): dict(v) for k, v in dict(states or {}).items() if isinstance(v, dict)
        }
        self._apply_manual_item_states()

    def set_items(self, items: list[dict]) -> None:
        normalized: list[dict] = []
        for item in list(items or []):
            if not isinstance(item, dict):
                continue
            out = dict(item)
            if str(out.get("type", "") or "").strip().lower() == "slot":
                out["activation_rule"] = normalize_activation_rule(
                    out.get("activation_rule")
                )
                out["ready_source"] = normalize_ready_source(
                    out.get("ready_source"), "slot"
                )
                out["buff_roi_id"] = str(out.get("buff_roi_id", "") or "").strip().lower()
            elif str(out.get("type", "") or "").strip().lower() == "manual":
                out["ready_source"] = normalize_ready_source(
                    out.get("ready_source"), "manual"
                )
                out["buff_roi_id"] = str(out.get("buff_roi_id", "") or "").strip().lower()
            normalized.append(out)
        self._items = normalized
        self._rebuild_items()

    def get_items(self) -> list[dict]:
        return [dict(i) for i in self._items]

    def update_states(self, states: list[dict]) -> None:
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
            if w.item_type == "slot" and isinstance(w.slot_index, int):
                state, cd, cast_progress, cast_ends_at = by_index.get(
                    w.slot_index,
                    ("unknown", None, None, None),
                )
                w.set_state(state, cd, cast_progress, cast_ends_at)
        self._apply_manual_item_states()

    def _manual_action_by_id(self, action_id: str) -> Optional[dict]:
        aid = str(action_id or "").strip().lower()
        for action in self._manual_actions:
            if str(action.get("id", "") or "").strip().lower() == aid:
                return action
        return None

    def _rebuild_items(self) -> None:
        for w in self._item_widgets:
            w.deleteLater()
        self._item_widgets.clear()
        for rank, item in enumerate(self._items, 1):
            item_type = str(item.get("type", "") or "").strip().lower()
            slot_index = item.get("slot_index")
            action_id = str(item.get("action_id", "") or "").strip().lower()
            activation_rule = normalize_activation_rule(item.get("activation_rule"))
            ready_source = normalize_ready_source(item.get("ready_source"), item_type)
            buff_roi_id = str(item.get("buff_roi_id", "") or "").strip().lower()
            if item_type == "slot" and isinstance(slot_index, int):
                keybind = self._keybinds[slot_index] if slot_index < len(self._keybinds) else "?"
                name = (
                    self._display_names[slot_index].strip()
                    if slot_index < len(self._display_names) and self._display_names[slot_index].strip()
                    else "Unidentified"
                )
            elif item_type == "manual":
                action = self._manual_action_by_id(action_id)
                if not isinstance(action, dict):
                    continue
                keybind = str(action.get("keybind", "") or "").strip() or "?"
                name = str(action.get("name", "") or "").strip() or "Manual Action"
            else:
                continue

            w = PriorityItemWidget(
                self._item_key(item),
                item_type,
                slot_index if isinstance(slot_index, int) else None,
                action_id if item_type == "manual" and action_id else None,
                activation_rule,
                ready_source,
                buff_roi_id,
                self._buff_rois,
                rank,
                keybind or "?",
                name,
                self._list_container,
            )
            w.set_activation_rule(activation_rule)
            w.set_ready_source(ready_source, buff_roi_id)
            if item_type == "slot" and isinstance(slot_index, int):
                state, cd, cast_progress, cast_ends_at = self._states_by_index.get(
                    slot_index,
                    ("unknown", None, None, None),
                )
                w.set_state(state, cd, cast_progress, cast_ends_at)
            else:
                w.set_state("unknown", None, None, None)
            self._list_layout.insertWidget(self._list_layout.count() - 1, w)
            w.remove_requested.connect(self.remove_item_by_key)
            self._item_widgets.append(w)
        self._apply_manual_item_states()

    def _item_by_key(self, item_key: str) -> Optional[dict]:
        for item in self._items:
            if self._item_key(item) == item_key:
                return item
        return None

    def _apply_manual_item_states(self) -> None:
        for w in self._item_widgets:
            if w.item_type != "manual":
                continue
            item = self._item_by_key(w.item_key)
            if not isinstance(item, dict):
                w.set_state("unknown", None, None, None)
                continue
            eligible = manual_item_is_eligible(item, buff_states=self._buff_states)
            w.set_state("ready" if eligible else "on_cooldown", None, None, None)

    def _emit_items(self) -> None:
        self.items_changed.emit(self.get_items())

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_SLOT) or event.mimeData().hasFormat(MIME_PRIORITY_ITEM):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        pos = event.position().toPoint()
        if mime.hasFormat(MIME_SLOT):
            slot_index = int(mime.data(MIME_SLOT).data().decode())
            if any(
                str(item.get("type", "") or "").strip().lower() == "slot"
                and item.get("slot_index") == slot_index
                for item in self._items
            ):
                event.acceptProposedAction()
                return
            has_keybind = slot_index < len(self._keybinds) and bool(self._keybinds[slot_index].strip())
            if not has_keybind:
                event.acceptProposedAction()
                return
            self._items.append(
                {"type": "slot", "slot_index": slot_index, "activation_rule": "always"}
            )
            self._rebuild_items()
            self._emit_items()
        elif mime.hasFormat(MIME_PRIORITY_ITEM):
            from_key = str(mime.data(MIME_PRIORITY_ITEM).data().decode() or "")
            from_item = next((i for i in self._items if self._item_key(i) == from_key), None)
            if from_item is None:
                event.ignore()
                return
            local_pos = self._list_container.mapFrom(self, pos)
            drop_idx = len(self._item_widgets)
            for i, w in enumerate(self._item_widgets):
                if local_pos.y() < w.y() + w.height() // 2:
                    drop_idx = i
                    break
            try:
                self._items.remove(from_item)
                self._items.insert(drop_idx, from_item)
            except ValueError:
                pass
            self._rebuild_items()
            self._emit_items()
        event.acceptProposedAction()

    def remove_item_by_key(self, item_key: str) -> None:
        before = len(self._items)
        self._items = [i for i in self._items if self._item_key(i) != item_key]
        if len(self._items) != before:
            self._rebuild_items()
            self._emit_items()

    def _on_manual_item_action(self, action_id: str, op: str) -> None:
        if op == "rename":
            self.manual_action_rename_requested.emit(action_id)
        elif op == "rebind":
            self.manual_action_rebind_requested.emit(action_id)
        elif op == "remove":
            self.manual_action_remove_requested.emit(action_id)

    def _on_slot_item_activation_rule_changed(self, item_key: str, activation_rule: str) -> None:
        rule = normalize_activation_rule(activation_rule)
        for item in self._items:
            if self._item_key(item) != item_key:
                continue
            if str(item.get("type", "") or "").strip().lower() != "slot":
                continue
            item["activation_rule"] = rule
            self._rebuild_items()
            self._emit_items()
            return

    def _on_item_ready_source_changed(
        self, item_key: str, ready_source: str, buff_roi_id: str
    ) -> None:
        for item in self._items:
            if self._item_key(item) != item_key:
                continue
            item_type = str(item.get("type", "") or "").strip().lower()
            item["ready_source"] = normalize_ready_source(ready_source, item_type)
            item["buff_roi_id"] = str(buff_roi_id or "").strip().lower()
            self._rebuild_items()
            self._emit_items()
            return


class PriorityPanel(QWidget):
    """Right-side panel: priority list only (Last Action and Next Intention are in main window left column)."""

    gcd_updated = pyqtSignal(float)
    add_manual_action_requested = pyqtSignal()

    GCD_WINDOW_SIZE = 20
    GCD_MIN_SAMPLES = 3

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

        title_row = QHBoxLayout()
        title = QLabel("PRIORITY")
        title.setStyleSheet(
            "font-family: monospace; font-size: 10px; color: #666; font-weight: bold; letter-spacing: 1.5px;"
        )
        title_row.addWidget(title)
        title_row.addStretch(1)
        self._btn_add_manual = QPushButton("+ manual")
        self._btn_add_manual.setObjectName("priorityAddManual")
        self._btn_add_manual.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_add_manual.setToolTip("Add action not tied to a monitored slot")
        self._btn_add_manual.clicked.connect(self.add_manual_action_requested.emit)
        title_row.addWidget(self._btn_add_manual)
        priority_inner.addLayout(title_row)

        self._priority_list = PriorityListWidget(self)
        priority_inner.addWidget(self._priority_list, 1)
        layout.addWidget(priority_frame, 1)

    @property
    def last_action_label(self) -> QLabel:
        if not hasattr(self, "_dummy_last_action_label"):
            self._dummy_last_action_label = QLabel("-")
        return self._dummy_last_action_label

    @property
    def next_intention_label(self) -> QLabel:
        if not hasattr(self, "_dummy_next_intention_label"):
            self._dummy_next_intention_label = QLabel("-")
        return self._dummy_next_intention_label

    @property
    def priority_list(self) -> PriorityListWidget:
        return self._priority_list

    def update_last_action_sent(self, keybind: str, timestamp: float, display_name: str = "Unidentified") -> None:
        self._last_action_keybind = keybind
        self._last_action_display_name = display_name or "Unidentified"
        self._last_action_timestamp = timestamp
        self._last_action_label.setText(f"[{keybind}] {self._last_action_display_name}")
        if not self._last_action_timer.isActive():
            self._last_action_timer.start()
        self._on_last_action_timer()
        self.record_send_timestamp(timestamp)

    def record_send_timestamp(self, timestamp: float) -> None:
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
        self._last_action_label.setText(
            f"[{self._last_action_keybind}] {self._last_action_display_name}  {elapsed:.1f}s ago"
        )

    def update_next_intention_blocked(self, keybind: str, display_name: str = "Unidentified") -> None:
        name = display_name or "Unidentified"
        self._next_intention_label.setText(f"[{keybind}] {name} - waiting (window)")

    def stop_last_action_timer(self) -> None:
        self._last_action_timer.stop()

    def _compute_estimated_gcd(self) -> Optional[float]:
        if len(self._send_timestamps) < self.GCD_MIN_SAMPLES:
            return None
        intervals = [
            self._send_timestamps[i] - self._send_timestamps[i - 1]
            for i in range(1, len(self._send_timestamps))
        ]
        return statistics.median(intervals)

    def reset_gcd_estimate(self) -> None:
        self._send_timestamps.clear()
