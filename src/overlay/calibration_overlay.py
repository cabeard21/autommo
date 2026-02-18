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
        self._monitor_geometry = monitor_geometry
        self._slot_count = 10
        self._slot_gap = 2
        self._slot_padding = 3

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

    def update_cast_bar_region(self, region: Optional[dict]) -> None:
        """Update cast-bar ROI (relative to capture bbox) and repaint."""
        self._cast_bar_region = dict(region or {})
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

    def paintEvent(self, event) -> None:
        """Draw the bounding box and per-slot analyzed regions."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

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

        # Pink/magenta 1px outlines for each analyzed slot region
        slot_pen = QPen(QColor("#FF00FF"), 1)
        painter.setPen(slot_pen)
        for rect in self._slot_analyzed_rects():
            if rect.width() > 0 and rect.height() > 0:
                painter.drawRect(rect)

        # Cyan 2px outline for cast-bar ROI (if enabled)
        cast_bar_rect = self._cast_bar_rect()
        if cast_bar_rect is not None:
            cast_bar_pen = QPen(QColor("#00E5FF"), 2)
            painter.setPen(cast_bar_pen)
            painter.drawRect(cast_bar_rect)

        painter.end()
