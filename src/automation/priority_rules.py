"""Priority item activation-rule helpers shared by UI and automation."""

from __future__ import annotations

from typing import Any, Optional

from src.models import SlotSnapshot


def normalize_activation_rule(raw_rule: object) -> str:
    rule = str(raw_rule or "").strip().lower()
    if rule in ("always", "dot_refresh"):
        return rule
    return "always"


def normalize_ready_source(raw_source: object, item_type: str) -> str:
    source = str(raw_source or "").strip().lower()
    if source in ("slot", "always", "buff_present", "buff_missing"):
        return source
    return "always" if item_type == "manual" else "slot"


def dot_refresh_eligible(yellow_glow_ready: bool, red_glow_ready: bool) -> bool:
    """DoT refresh eligibility: no glow OR red glow (yellow-only blocks)."""
    return (not yellow_glow_ready and not red_glow_ready) or red_glow_ready


def _buff_ready(
    item: dict,
    buff_states: Optional[dict[str, Any]],
    item_type: str,
) -> bool:
    source = normalize_ready_source(item.get("ready_source"), item_type)
    if source == "always":
        return True
    if source == "slot":
        return True
    buff_id = str(item.get("buff_roi_id", "") or "").strip().lower()
    if not buff_id:
        return False
    if not isinstance(buff_states, dict):
        return False
    buff = buff_states.get(buff_id)
    if not isinstance(buff, dict):
        return False
    if not bool(buff.get("calibrated", False)):
        return False
    present = bool(buff.get("present", False))
    if source == "buff_present":
        return present
    if source == "buff_missing":
        return not present
    return False


def slot_item_is_eligible_for_snapshot(
    item: dict,
    slot: Optional[SlotSnapshot],
    buff_states: Optional[dict[str, Any]] = None,
) -> bool:
    if slot is None:
        return False
    if not _buff_ready(item, buff_states, "slot"):
        return False
    ready_source = normalize_ready_source(item.get("ready_source"), "slot")
    if ready_source == "slot" and not getattr(slot, "is_ready", False):
        return False
    if ready_source in ("buff_present", "buff_missing"):
        return True
    rule = normalize_activation_rule(item.get("activation_rule"))
    if rule == "always":
        return True
    return dot_refresh_eligible(
        bool(getattr(slot, "yellow_glow_ready", False)),
        bool(getattr(slot, "red_glow_ready", False)),
    )


def slot_item_is_eligible_for_state_dict(
    item: dict,
    slot_state: Optional[dict[str, Any]],
    buff_states: Optional[dict[str, Any]] = None,
) -> bool:
    if not isinstance(slot_state, dict):
        return False
    if not _buff_ready(item, buff_states, "slot"):
        return False
    ready_source = normalize_ready_source(item.get("ready_source"), "slot")
    if (
        ready_source == "slot"
        and str(slot_state.get("state", "") or "").strip().lower() != "ready"
    ):
        return False
    if ready_source in ("buff_present", "buff_missing"):
        return True
    rule = normalize_activation_rule(item.get("activation_rule"))
    if rule == "always":
        return True
    return dot_refresh_eligible(
        bool(slot_state.get("yellow_glow_ready", False)),
        bool(slot_state.get("red_glow_ready", False)),
    )


def manual_item_is_eligible(item: dict, buff_states: Optional[dict[str, Any]] = None) -> bool:
    return _buff_ready(item, buff_states, "manual")
