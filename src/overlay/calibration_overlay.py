"""Calibration overlay — transparent always-on-top window for capture debug.

Normally click-through; while edit mode is enabled it accepts mouse input for
dragging and resizing bbox/buff ROIs.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtWidgets import QWidget

from src.models import BoundingBox

logger = logging.getLogger(__name__)


class CalibrationOverlay(QWidget):
    """Transparent overlay window that shows the capture bounding box and per-slot analyzed regions."""

    bbox_geometry_edited = pyqtSignal(int, int, int, int)
    buff_roi_geometry_edited = pyqtSignal(str, int, int, int, int)

    def __init__(self, monitor_geometry: QRect, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._bbox = BoundingBox()
        self._border_color = QColor("#00FF00")
        self._border_width = 2
        self._cast_bar_region: dict = {}
        self._buff_rois: list[dict] = []
        self._buff_states: dict[str, dict] = {}
        self._form_detector: dict = {}
        self._form_state: dict = {
            "active_form_id": "normal",
            "settling": False,
        }
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
        self._slot_detection_mode: str = "slot"
        self._edit_mode_enabled: bool = False
        self._drag_state: Optional[dict] = None
        self._hover_hit: Optional[dict] = None
        self._handle_size: int = 10
        self._min_rect_size: int = 8

        self._setup_window()

    def _window_flags(self) -> Qt.WindowType:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        if not self._edit_mode_enabled:
            flags |= Qt.WindowType.WindowTransparentForInput
        return flags

    def _apply_window_flags(self) -> None:
        was_visible = self.isVisible()
        self.setWindowFlags(self._window_flags())
        if was_visible:
            self.show()

    def _setup_window(self) -> None:
        """Configure the window to be transparent, frameless, always-on-top, click-through."""
        self._apply_window_flags()
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

    def update_form_detector(self, detector: Optional[dict]) -> None:
        self._form_detector = dict(detector or {})
        self.update()

    def update_form_state(self, state: Optional[dict]) -> None:
        if isinstance(state, dict):
            self._form_state = dict(state)
        else:
            self._form_state = {}
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

    def update_slot_detection_mode(self, mode: str) -> None:
        normalized = str(mode or "slot").strip().lower()
        if normalized not in ("slot", "buff_only"):
            normalized = "slot"
        self._slot_detection_mode = normalized
        self.update()

    def set_edit_mode_enabled(self, enabled: bool) -> None:
        enabled_norm = bool(enabled)
        if enabled_norm == self._edit_mode_enabled:
            return
        self._edit_mode_enabled = enabled_norm
        if not self._edit_mode_enabled:
            self._drag_state = None
            self._hover_hit = None
            self.unsetCursor()
        self._apply_window_flags()
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

    def _all_enabled_buff_rects(self) -> list[dict]:
        out: list[dict] = []
        for buff in self._buff_rois:
            if not isinstance(buff, dict):
                continue
            rect = self._buff_rect(buff)
            if rect is None:
                continue
            buff_id = str(buff.get("id", "") or "").strip().lower()
            if not buff_id:
                continue
            out.append({"id": buff_id, "rect": rect})
        return out

    def _rect_handles(self, rect: QRect) -> dict[str, QRect]:
        half = self._handle_size // 2
        cx = rect.left() + rect.width() // 2
        cy = rect.top() + rect.height() // 2
        l = rect.left()
        r = rect.right()
        t = rect.top()
        b = rect.bottom()
        s = self._handle_size
        return {
            "nw": QRect(l - half, t - half, s, s),
            "n": QRect(cx - half, t - half, s, s),
            "ne": QRect(r - half, t - half, s, s),
            "e": QRect(r - half, cy - half, s, s),
            "se": QRect(r - half, b - half, s, s),
            "s": QRect(cx - half, b - half, s, s),
            "sw": QRect(l - half, b - half, s, s),
            "w": QRect(l - half, cy - half, s, s),
        }

    @staticmethod
    def _distance_sq(a: QPoint, b: QPoint) -> int:
        dx = int(a.x() - b.x())
        dy = int(a.y() - b.y())
        return dx * dx + dy * dy

    def _nearest_by_center(self, point: QPoint, candidates: list[dict]) -> Optional[dict]:
        best: Optional[dict] = None
        best_dist: Optional[int] = None
        for item in candidates:
            rect = item.get("rect")
            if not isinstance(rect, QRect):
                continue
            center = rect.center()
            dist = self._distance_sq(point, center)
            if best is None or best_dist is None or dist < best_dist:
                best = item
                best_dist = dist
        return best

    def _hit_test(self, point: QPoint) -> Optional[dict]:
        buff_rects = self._all_enabled_buff_rects()

        buff_handle_hits: list[dict] = []
        for item in buff_rects:
            rect = item["rect"]
            for handle_name, handle_rect in self._rect_handles(rect).items():
                if handle_rect.contains(point):
                    buff_handle_hits.append(
                        {
                            "kind": "buff",
                            "id": item["id"],
                            "rect": rect,
                            "handle": handle_name,
                            "anchor": handle_rect.center(),
                        }
                    )
        if buff_handle_hits:
            best = self._nearest_by_center(
                point, [{"rect": QRect(h["anchor"], h["anchor"]), "hit": h} for h in buff_handle_hits]
            )
            if best is not None:
                return best["hit"]

        buff_inside = [item for item in buff_rects if item["rect"].contains(point)]
        if buff_inside:
            best = self._nearest_by_center(point, buff_inside)
            if best is not None:
                return {"kind": "buff", "id": best["id"], "rect": best["rect"], "handle": "move"}

        bbox_rect = QRect(self._bbox.left, self._bbox.top, self._bbox.width, self._bbox.height)
        for handle_name, handle_rect in self._rect_handles(bbox_rect).items():
            if handle_rect.contains(point):
                return {"kind": "bbox", "rect": bbox_rect, "handle": handle_name}
        if bbox_rect.contains(point):
            return {"kind": "bbox", "rect": bbox_rect, "handle": "move"}
        return None

    def _apply_drag_geometry(
        self, left: int, top: int, width: int, height: int, handle: str, dx: int, dy: int
    ) -> tuple[int, int, int, int]:
        max_w = max(1, int(self.width()))
        max_h = max(1, int(self.height()))
        min_size = self._min_rect_size

        l = int(left)
        t = int(top)
        r = int(left + width)
        b = int(top + height)

        if handle == "move":
            l += dx
            r += dx
            t += dy
            b += dy
            if l < 0:
                r -= l
                l = 0
            if t < 0:
                b -= t
                t = 0
            if r > max_w:
                shift = r - max_w
                l -= shift
                r -= shift
            if b > max_h:
                shift = b - max_h
                t -= shift
                b -= shift
            l = max(0, l)
            t = max(0, t)
            r = min(max_w, r)
            b = min(max_h, b)
            return l, t, max(min_size, r - l), max(min_size, b - t)

        if "w" in handle:
            l += dx
        if "e" in handle:
            r += dx
        if "n" in handle:
            t += dy
        if "s" in handle:
            b += dy

        if r - l < min_size:
            if "w" in handle and "e" not in handle:
                l = r - min_size
            else:
                r = l + min_size
        if b - t < min_size:
            if "n" in handle and "s" not in handle:
                t = b - min_size
            else:
                b = t + min_size

        if "w" in handle and "e" not in handle:
            l = max(0, min(l, r - min_size))
        if "e" in handle and "w" not in handle:
            r = min(max_w, max(r, l + min_size))
        if "n" in handle and "s" not in handle:
            t = max(0, min(t, b - min_size))
        if "s" in handle and "n" not in handle:
            b = min(max_h, max(b, t + min_size))

        l = max(0, min(l, max_w - min_size))
        t = max(0, min(t, max_h - min_size))
        r = max(l + min_size, min(r, max_w))
        b = max(t + min_size, min(b, max_h))
        return l, t, r - l, b - t

    def _cursor_for_handle(self, handle: str) -> Qt.CursorShape:
        if handle in ("n", "s"):
            return Qt.CursorShape.SizeVerCursor
        if handle in ("e", "w"):
            return Qt.CursorShape.SizeHorCursor
        if handle in ("ne", "sw"):
            return Qt.CursorShape.SizeBDiagCursor
        if handle in ("nw", "se"):
            return Qt.CursorShape.SizeFDiagCursor
        return Qt.CursorShape.SizeAllCursor

    def mousePressEvent(self, event) -> None:
        if not self._edit_mode_enabled or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        point = event.position().toPoint()
        hit = self._hit_test(point)
        if hit is None:
            super().mousePressEvent(event)
            return
        rect = hit["rect"]
        self._drag_state = {
            "kind": hit["kind"],
            "id": hit.get("id", ""),
            "handle": hit["handle"],
            "start": point,
            "left": int(rect.left()),
            "top": int(rect.top()),
            "width": int(rect.width()),
            "height": int(rect.height()),
        }
        self.setCursor(self._cursor_for_handle(str(hit["handle"])))
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if not self._edit_mode_enabled:
            super().mouseMoveEvent(event)
            return

        point = event.position().toPoint()
        if self._drag_state is None:
            hit = self._hit_test(point)
            self._hover_hit = hit
            if hit is None:
                self.unsetCursor()
            else:
                self.setCursor(self._cursor_for_handle(str(hit["handle"])))
            self.update()
            super().mouseMoveEvent(event)
            return

        start = self._drag_state["start"]
        dx = int(point.x() - start.x())
        dy = int(point.y() - start.y())
        left, top, width, height = self._apply_drag_geometry(
            self._drag_state["left"],
            self._drag_state["top"],
            self._drag_state["width"],
            self._drag_state["height"],
            str(self._drag_state["handle"]),
            dx,
            dy,
        )

        if self._drag_state["kind"] == "bbox":
            if (
                left != int(self._bbox.left)
                or top != int(self._bbox.top)
                or width != int(self._bbox.width)
                or height != int(self._bbox.height)
            ):
                self._bbox = BoundingBox(top=top, left=left, width=width, height=height)
                self.bbox_geometry_edited.emit(left, top, width, height)
                self.update()
        else:
            buff_id = str(self._drag_state.get("id", "") or "").strip().lower()
            rel_left = int(left - int(self._bbox.left))
            rel_top = int(top - int(self._bbox.top))
            updated = False
            for i, buff in enumerate(self._buff_rois):
                if not isinstance(buff, dict):
                    continue
                current_id = str(buff.get("id", "") or "").strip().lower()
                if current_id != buff_id:
                    continue
                new_buff = dict(buff)
                new_buff["left"] = int(rel_left)
                new_buff["top"] = int(rel_top)
                new_buff["width"] = int(width)
                new_buff["height"] = int(height)
                self._buff_rois[i] = new_buff
                self.buff_roi_geometry_edited.emit(
                    buff_id,
                    int(rel_left),
                    int(rel_top),
                    int(width),
                    int(height),
                )
                updated = True
                break
            if updated:
                self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_state = None
            if self._edit_mode_enabled:
                point = event.position().toPoint()
                hit = self._hit_test(point)
                if hit is None:
                    self.unsetCursor()
                else:
                    self.setCursor(self._cursor_for_handle(str(hit["handle"])))
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_hit = None
        if self._drag_state is None:
            self.unsetCursor()
        super().leaveEvent(event)

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
        if self._edit_mode_enabled:
            # Keep bbox interior hit-testable while editing on translucent overlays.
            painter.fillRect(
                QRect(self._bbox.left, self._bbox.top, self._bbox.width, self._bbox.height),
                QColor(255, 255, 255, 18),
            )

        space_above = self._bbox.top >= 20
        if self._slot_detection_mode == "slot":
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
                        self._bbox.top - 3 if self._bbox.top >= 20 else self._bbox.top + self._bbox.height + 14,
                        f"{d_status} {y_status}{yellow_frac:.2f} {r_status}{red_frac:.2f}",
                    )

            painter.setPen(QPen(QColor("#AAAAAA"), 1))
            painter.drawText(
                self._bbox.left + 4,
                self._bbox.top - 16 if space_above else self._bbox.top + self._bbox.height + 28,
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
            motion_score = float(state.get("motion_score", 0.0) or 0.0)
            motion_gate = float(state.get("motion_gate_threshold", 0.0) or 0.0)
            red_ready = bool(state.get("red_glow_ready", False))
            red_candidate = bool(state.get("red_glow_candidate", False))
            color = QColor("#35D07F") if present else QColor("#FF884D")
            if not calibrated:
                color = QColor("#BBBBBB")
            painter.setPen(QPen(color, 2))
            painter.drawRect(rect)
            if self._edit_mode_enabled:
                # Keep ROI interior hit-testable while editing on translucent overlays.
                painter.fillRect(rect, QColor(255, 255, 255, 18))
                for handle_rect in self._rect_handles(rect).values():
                    painter.fillRect(handle_rect, QColor(240, 240, 240, 200))
            name = str(buff.get("name", "") or "").strip() or buff_id
            tag = "P" if present else "M"
            if not calibrated:
                tag = "U"
            red_tag = "R" if red_ready else ("r" if red_candidate else ".")
            motion_part = f" M{motion_score:.1f}" if motion_gate > 0 else ""
            painter.drawText(
                rect.left() + 2,
                rect.top() - 4 if rect.top() > 10 else rect.bottom() + 12,
                f"BUFF {name}: {tag} {red_tag} {status} S{similarity:.2f}{motion_part}",
            )

        detector = self._form_detector if isinstance(self._form_detector, dict) else {}
        state = self._form_state if isinstance(self._form_state, dict) else {}
        det_type = str(detector.get("type", "off") or "off").strip().lower()
        roi_id = str(detector.get("roi_id", "") or "").strip().lower()
        present_form = str(detector.get("present_form", "normal") or "normal").strip().lower()
        absent_form = str(detector.get("absent_form", "normal") or "normal").strip().lower()
        active_form = str(state.get("active_form_id", "normal") or "normal").strip().lower()
        settling = bool(state.get("settling", False))
        settling_tag = "Y" if settling else "N"
        painter.setPen(QPen(QColor("#86D1FF"), 1))
        painter.drawText(
            self._bbox.left + 4,
            self._bbox.top + self._bbox.height + (42 if not space_above else 16),
            (
                f"FORMDBG active={active_form} settle={settling_tag} "
                f"det={det_type} roi={roi_id} P->{present_form} A->{absent_form}"
            ),
        )

        if self._edit_mode_enabled:
            bbox_rect = QRect(self._bbox.left, self._bbox.top, self._bbox.width, self._bbox.height)
            for handle_rect in self._rect_handles(bbox_rect).values():
                painter.fillRect(handle_rect, QColor(240, 240, 240, 200))
            painter.setPen(QPen(QColor("#FFFFFF"), 1))
            painter.drawText(
                self._bbox.left + 6,
                self._bbox.top + self._bbox.height + 56,
                "Edit mode: Shift held. Drag inside to move; drag handles to resize.",
            )

        painter.end()
