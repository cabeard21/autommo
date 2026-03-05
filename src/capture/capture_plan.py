"""Capture plan computation — shared utility for CaptureWorker and calibration.

Computes the expanded capture bounding box (covering the action bar, cast bar ROI,
and all enabled buff ROIs) and the pixel offset of the action bar inside that box.
"""

from __future__ import annotations

from src.models import BoundingBox


def compute_capture_plan(
    action_bbox: BoundingBox,
    cast_bar_region: dict,
    buff_rois: list,
    monitor_width: int,
    monitor_height: int,
) -> tuple[BoundingBox, tuple[int, int]]:
    """Return the expanded capture bbox and the action-bar origin inside it.

    The capture region is the smallest rectangle that contains:
    - the action bar bounding box
    - the cast bar ROI (if enabled and sized > 1×1)
    - every enabled buff ROI sized > 1×1

    All coordinates are monitor-relative. The result is clamped to monitor bounds.

    Returns:
        capture_bbox: BoundingBox to pass to ScreenCapture.grab_region()
        action_origin: (x, y) pixel offset of action_bbox inside capture_bbox
    """
    left = int(action_bbox.left)
    top = int(action_bbox.top)
    right = left + int(action_bbox.width)
    bottom = top + int(action_bbox.height)

    if bool((cast_bar_region or {}).get("enabled", False)):
        cast_w = int(cast_bar_region.get("width", 0))
        cast_h = int(cast_bar_region.get("height", 0))
        if cast_w > 1 and cast_h > 1:
            cast_left = int(action_bbox.left) + int(cast_bar_region.get("left", 0))
            cast_top = int(action_bbox.top) + int(cast_bar_region.get("top", 0))
            left = min(left, cast_left)
            top = min(top, cast_top)
            right = max(right, cast_left + cast_w)
            bottom = max(bottom, cast_top + cast_h)

    for raw in list(buff_rois or []):
        if not isinstance(raw, dict):
            continue
        if not bool(raw.get("enabled", True)):
            continue
        roi_w = int(raw.get("width", 0))
        roi_h = int(raw.get("height", 0))
        if roi_w <= 1 or roi_h <= 1:
            continue
        roi_left = int(action_bbox.left) + int(raw.get("left", 0))
        roi_top = int(action_bbox.top) + int(raw.get("top", 0))
        left = min(left, roi_left)
        top = min(top, roi_top)
        right = max(right, roi_left + roi_w)
        bottom = max(bottom, roi_top + roi_h)

    left = max(0, min(left, monitor_width - 1))
    top = max(0, min(top, monitor_height - 1))
    right = max(left + 1, min(right, monitor_width))
    bottom = max(top + 1, min(bottom, monitor_height))

    capture_bbox = BoundingBox(
        top=top,
        left=left,
        width=max(1, right - left),
        height=max(1, bottom - top),
    )
    action_origin = (action_bbox.left - capture_bbox.left, action_bbox.top - capture_bbox.top)
    return capture_bbox, action_origin
