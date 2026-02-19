"""Calibration overlay — a transparent, always-on-top window that draws a
colored rectangle showing the current capture bounding box.

The overlay is click-through (input passes to windows beneath it).
Position and size are controlled from the main UI, not by dragging.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtWidgets import QWidget

from src.models import BoundingBox

logger = logging.getLogger(__name__)


class CalibrationOverlay(QWidget):
    """Transparent overlay window that shows the capture bounding box and per-slot analyzed regions."""

    def __init__(self, monitor_geometry: QRect, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._bbox = BoundingBox()
        self._border_color = QColor("#00FF00")
        self._border_width = 2
        self._cast_bar_region: dict = {}
        self._buff_rois: list[dict] = []
        self._buff_states: dict[str, dict] = {}
        self._monitor_geometry = monitor_geometry
        self._slot_count = 10
        self._slot_gap = 2
        self._slot_padding = 3
        self._slot_glow_ready: dict[int, bool] = {}
        self._slot_glow_candidate: dict[int, bool] = {}
        self._slot_glow_fraction: dict[int, float] = {}
        self._slot_yellow_glow_ready: dict[int, bool] = {}
        self._slot_yellow_glow_candidate: dict[int, bool] = {}
        self._slot_yellow_glow_fraction: dict[int, float] = {}
        self._slot_red_glow_ready: dict[int, bool] = {}
        self._slot_red_glow_candidate: dict[int, bool] = {}
        self._slot_red_glow_fraction: dict[int, float] = {}
        self._show_active_screen_outline: bool = False
        self._capture_active: bool = False

        self._setup_window()

    def _setup_window(self) -> None:
        """Configure the window to be transparent, frameless, always-on-top, click-through."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # Hides from taskbar
            | Qt.WindowType.WindowTransparentForInput  # Click-through
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Cover the entire monitor
        self.setGeometry(self._monitor_geometry)

    def update_bounding_box(self, bbox: BoundingBox) -> None:
        """Update the displayed bounding box and repaint."""
        self._bbox = bbox
        self.update()  # Triggers paintEvent

    def update_slot_layout(self, slot_count: int, slot_gap: int, slot_padding: int) -> None:
        """Update slot layout (same math as SlotAnalyzer) and repaint per-slot outlines."""
        self._slot_count = slot_count
        self._slot_gap = slot_gap
        self._slot_padding = slot_padding
        self.update()

    def update_monitor_geometry(self, monitor_geometry: QRect) -> None:
        """Move/resize overlay to fully cover the selected monitor."""
        self._monitor_geometry = monitor_geometry
        self.setGeometry(self._monitor_geometry)
        self.update()

    def update_border_color(self, color: str) -> None:
        """Update the overlay border color."""
        self._border_color = QColor(color)
        self.update()

    def update_show_active_screen_outline(self, enabled: bool) -> None:
        """Enable/disable the full-screen 1px outline with glow when capture is active."""
        self._show_active_screen_outline = bool(enabled)
        self.update()

    def set_capture_active(self, active: bool) -> None:
        """Mark whether capture is running (used to show/hide active screen outline)."""
        self._capture_active = bool(active)
        self.update()

    def update_cast_bar_region(self, region: Optional[dict]) -> None:
        """Update cast-bar ROI (relative to capture bbox) and repaint."""
        self._cast_bar_region = dict(region or {})
        self.update()

    def update_buff_rois(self, rois: Optional[list[dict]]) -> None:
        self._buff_rois = [dict(r) for r in list(rois or []) if isinstance(r, dict)]
        self.update()

    def update_buff_states(self, states: Optional[dict]) -> None:
        self._buff_states = {
            str(k): dict(v) for k, v in dict(states or {}).items() if isinstance(v, dict)
        }
        self.update()

    def update_slot_states(self, states: list[dict]) -> None:
        """Update per-slot live flags from analyzer output (e.g., glow-ready)."""
        by_index_ready: dict[int, bool] = {}
        by_index_candidate: dict[int, bool] = {}
        by_index_fraction: dict[int, float] = {}
        by_index_yellow_ready: dict[int, bool] = {}
        by_index_yellow_candidate: dict[int, bool] = {}
        by_index_yellow_fraction: dict[int, float] = {}
        by_index_red_ready: dict[int, bool] = {}
        by_index_red_candidate: dict[int, bool] = {}
        by_index_red_fraction: dict[int, float] = {}
        for item in states or []:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            if not isinstance(idx, int):
                continue
            by_index_ready[idx] = bool(item.get("glow_ready", False))
            by_index_candidate[idx] = bool(item.get("glow_candidate", False))
            by_index_fraction[idx] = float(item.get("glow_fraction", 0.0) or 0.0)
            by_index_yellow_ready[idx] = bool(item.get("yellow_glow_ready", False))
            by_index_yellow_candidate[idx] = bool(item.get("yellow_glow_candidate", False))
            by_index_yellow_fraction[idx] = float(item.get("yellow_glow_fraction", 0.0) or 0.0)
            by_index_red_ready[idx] = bool(item.get("red_glow_ready", False))
            by_index_red_candidate[idx] = bool(item.get("red_glow_candidate", False))
            by_index_red_fraction[idx] = float(item.get("red_glow_fraction", 0.0) or 0.0)
        self._slot_glow_ready = by_index_ready
        self._slot_glow_candidate = by_index_candidate
        self._slot_glow_fraction = by_index_fraction
        self._slot_yellow_glow_ready = by_index_yellow_ready
        self._slot_yellow_glow_candidate = by_index_yellow_candidate
        self._slot_yellow_glow_fraction = by_index_yellow_fraction
        self._slot_red_glow_ready = by_index_red_ready
        self._slot_red_glow_candidate = by_index_red_candidate
        self._slot_red_glow_fraction = by_index_red_fraction
        self.update()

    def _slot_analyzed_rects(self) -> list[QRect]:
        """Compute analyzed region rects (after padding) using same math as SlotAnalyzer."""
        total_width = self._bbox.width
        total_height = self._bbox.height
        gap = self._slot_gap
        count = self._slot_count
        padding = self._slot_padding

        slot_w = max(1, (total_width - (count - 1) * gap) // count)
        slot_h = total_height

        rects: list[QRect] = []
        for i in range(count):
            x = i * (slot_w + gap)
            inner_w = max(0, slot_w - 2 * padding)
            inner_h = max(0, slot_h - 2 * padding)
            rects.append(
                QRect(
                    self._bbox.left + x + padding,
                    self._bbox.top + padding,
                    inner_w,
                    inner_h,
                )
            )
        return rects

    def _cast_bar_rect(self) -> Optional[QRect]:
        """Compute cast-bar ROI rect in absolute screen coordinates."""
        region = self._cast_bar_region or {}
        if not bool(region.get("enabled", False)):
            return None
        w = int(region.get("width", 0))
        h = int(region.get("height", 0))
        if w <= 0 or h <= 0:
            return None
        x = self._bbox.left + int(region.get("left", 0))
        y = self._bbox.top + int(region.get("top", 0))
        return QRect(x, y, w, h)

    def _buff_rect(self, buff: dict) -> Optional[QRect]:
        if not bool(buff.get("enabled", True)):
            return None
        w = int(buff.get("width", 0))
        h = int(buff.get("height", 0))
        if w <= 0 or h <= 0:
            return None
        x = self._bbox.left + int(buff.get("left", 0))
        y = self._bbox.top + int(buff.get("top", 0))
        return QRect(x, y, w, h)

    def paintEvent(self, event) -> None:
        """Draw the bounding box and per-slot analyzed regions."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Full-screen 1px green outline with slight glow when capture is active (if enabled)
        if self._show_active_screen_outline and self._capture_active:
            w, h = self.width(), self.height()
            if w > 0 and h > 0:
                green = QColor(self._border_color)
                # Glow: faint inner strokes then solid 1px edge
                for inset, alpha in [(4, 35), (3, 60), (2, 100), (1, 160)]:
                    green.setAlpha(alpha)
                    painter.setPen(QPen(green, 1))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(inset, inset, w - 1 - 2 * inset, h - 1 - 2 * inset)
                green.setAlpha(255)
                painter.setPen(QPen(green, 1))
                painter.drawRect(0, 0, w - 1, h - 1)

        monitor_local = QRect(0, 0, self.width(), self.height())
        bbox_local = QRect(
            self._bbox.left - self._monitor_geometry.left(),
            self._bbox.top - self._monitor_geometry.top(),
            self._bbox.width,
            self._bbox.height,
        )
        if not monitor_local.intersects(bbox_local):
            painter.setPen(QPen(QColor("#FF5555"), 2))
            painter.drawRect(10, 10, 380, 28)
            painter.setPen(QPen(QColor("#FFB0B0"), 1))
            painter.drawText(
                16,
                29,
                f"Overlay bbox off-screen: L{self._bbox.left} T{self._bbox.top} W{self._bbox.width} H{self._bbox.height}",
            )
            painter.end()
            return

        # Green bounding box
        pen = QPen(self._border_color, self._border_width)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(
            self._bbox.left,
            self._bbox.top,
            self._bbox.width,
            self._bbox.height,
        )

        # Slot outlines. Red-ready slots use red outline, yellow-ready use yellow.
        default_slot_pen = QPen(QColor("#FF00FF"), 1)
        yellow_slot_pen = QPen(QColor("#FFD84D"), 2)
        red_slot_pen = QPen(QColor("#FF5A5A"), 2)
        for idx, rect in enumerate(self._slot_analyzed_rects()):
            if rect.width() > 0 and rect.height() > 0:
                red_ready = self._slot_red_glow_ready.get(idx, False)
                yellow_ready = self._slot_yellow_glow_ready.get(idx, False)
                if red_ready:
                    painter.setPen(red_slot_pen)
                elif yellow_ready:
                    painter.setPen(yellow_slot_pen)
                else:
                    painter.setPen(default_slot_pen)
                painter.drawRect(rect)
                if red_ready or yellow_ready:
                    marker_size = max(4, min(10, rect.width() // 5, rect.height() // 5))
                    marker = QRect(
                        rect.left() + 1,
                        rect.top() + 1,
                        marker_size,
                        marker_size,
                    )
                    painter.fillRect(
                        marker, QColor(255, 90, 90, 210) if red_ready else QColor(255, 216, 77, 200)
                    )
                yellow_candidate = self._slot_yellow_glow_candidate.get(idx, False)
                red_candidate = self._slot_red_glow_candidate.get(idx, False)
                yellow_frac = self._slot_yellow_glow_fraction.get(idx, 0.0)
                red_frac = self._slot_red_glow_fraction.get(idx, 0.0)
                dot_ok = (not yellow_ready and not red_ready) or red_ready
                y_status = "Y" if yellow_ready else ("y" if yellow_candidate else ".")
                r_status = "R" if red_ready else ("r" if red_candidate else ".")
                d_status = "D+" if dot_ok else "D-"
                painter.setPen(
                    QPen(
                        QColor("#FF5A5A")
                        if red_ready or red_candidate
                        else (QColor("#FFD84D") if yellow_ready or yellow_candidate else QColor("#888888")),
                        1,
                    )
                )
                painter.drawText(
                    rect.left() + 2,
                    rect.bottom() - 3,
                    f"{d_status} {y_status}{yellow_frac:.2f} {r_status}{red_frac:.2f}",
                )

        painter.setPen(QPen(QColor("#AAAAAA"), 1))
        painter.drawText(
            self._bbox.left + 4,
            self._bbox.top - 6 if self._bbox.top > 14 else self._bbox.top + 12,
            "Dot debug: D+=eligible D-=blocked | Y/y yellow | R/r red",
        )

        # Cyan 2px outline for cast-bar ROI (if enabled)
        cast_bar_rect = self._cast_bar_rect()
        if cast_bar_rect is not None:
            cast_bar_pen = QPen(QColor("#00E5FF"), 2)
            painter.setPen(cast_bar_pen)
            painter.drawRect(cast_bar_rect)

        for buff in self._buff_rois:
            if not isinstance(buff, dict):
                continue
            rect = self._buff_rect(buff)
            if rect is None:
                continue
            buff_id = str(buff.get("id", "") or "").strip().lower()
            state = self._buff_states.get(buff_id, {})
            present = bool(state.get("present", False))
            calibrated = bool(state.get("calibrated", False))
            status = str(state.get("status", "ok") or "ok").strip().lower()
            similarity = float(state.get("present_similarity", 0.0) or 0.0)
            red_ready = bool(state.get("red_glow_ready", False))
            red_candidate = bool(state.get("red_glow_candidate", False))
            color = QColor("#35D07F") if present else QColor("#FF884D")
            if not calibrated:
                color = QColor("#BBBBBB")
            painter.setPen(QPen(color, 2))
            painter.drawRect(rect)
            name = str(buff.get("name", "") or "").strip() or buff_id
            tag = "P" if present else "M"
            if not calibrated:
                tag = "U"
            red_tag = "R" if red_ready else ("r" if red_candidate else ".")
            painter.drawText(
                rect.left() + 2,
                rect.top() - 4 if rect.top() > 10 else rect.top() + 12,
                f"BUFF {name}: {tag} {red_tag} {status} S{similarity:.2f}",
            )

        painter.end()
