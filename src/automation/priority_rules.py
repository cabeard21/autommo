"""Priority item activation-rule helpers shared by UI and automation."""

from __future__ import annotations

from typing import Any, Optional

from src.models import SlotSnapshot


def normalize_activation_rule(raw_rule: object) -> str:
    rule = str(raw_rule or "").strip().lower()
    if rule in ("always", "dot_refresh"):
        return rule
    return "always"


def dot_refresh_eligible(yellow_glow_ready: bool, red_glow_ready: bool) -> bool:
    """DoT refresh eligibility: no glow OR red glow (yellow-only blocks)."""
    return (not yellow_glow_ready and not red_glow_ready) or red_glow_ready


def slot_item_is_eligible_for_snapshot(item: dict, slot: Optional[SlotSnapshot]) -> bool:
    if slot is None:
        return False
    if not getattr(slot, "is_ready", False):
        return False
    rule = normalize_activation_rule(item.get("activation_rule"))
    if rule == "always":
        return True
    return dot_refresh_eligible(
        bool(getattr(slot, "yellow_glow_ready", False)),
        bool(getattr(slot, "red_glow_ready", False)),
    )


def slot_item_is_eligible_for_state_dict(item: dict, slot_state: Optional[dict[str, Any]]) -> bool:
    if not isinstance(slot_state, dict):
        return False
    if str(slot_state.get("state", "") or "").strip().lower() != "ready":
        return False
    rule = normalize_activation_rule(item.get("activation_rule"))
    if rule == "always":
        return True
    return dot_refresh_eligible(
        bool(slot_state.get("yellow_glow_ready", False)),
        bool(slot_state.get("red_glow_ready", False)),
    )
