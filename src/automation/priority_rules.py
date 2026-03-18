"""Priority item activation-rule helpers shared by UI and automation."""

from __future__ import annotations

from typing import Any, Optional

from src.models import SlotSnapshot


def normalize_activation_rule(raw_rule: object) -> str:
    rule = str(raw_rule or "").strip().lower()
    if rule in ("always", "dot_refresh", "require_glow"):
        return rule
    return "always"


def normalize_ready_source(raw_source: object, item_type: str) -> str:
    source = str(raw_source or "").strip().lower()
    if source in ("slot", "always"):
        return source
    return "always" if item_type == "manual" else "slot"


def normalize_conditions(
    raw_conditions: object,
    item_type: str,
    legacy_ready_source: object = None,
    legacy_buff_roi_id: object = None,
) -> list[dict]:
    """Normalize item conditions and migrate legacy single-buff ready_source fields."""
    normalized: list[dict] = []
    seen: set[tuple] = set()
    for raw in list(raw_conditions or []):
        if not isinstance(raw, dict):
            continue
        cond_type = str(raw.get("type", "") or "").strip().lower()
        if cond_type == "buff_state":
            buff_id = str(raw.get("buff_roi_id", "") or "").strip().lower()
            op = str(raw.get("op", "") or "").strip().lower()
            if not buff_id or op not in ("present", "missing", "candidate_present", "candidate_missing"):
                continue
            key = ("buff_state", buff_id, op)
            if key in seen:
                continue
            seen.add(key)
            normalized.append({"type": "buff_state", "buff_roi_id": buff_id, "op": op})
        elif cond_type == "moving":
            op = str(raw.get("op", "") or "").strip().lower()
            if op not in ("active", "inactive"):
                continue
            key = ("moving", op)
            if key in seen:
                continue
            seen.add(key)
            normalized.append({"type": "moving", "op": op})
        elif cond_type == "previous_action":
            op = str(raw.get("op", "is") or "is").strip().lower()
            if op not in ("is", "is_not"):
                continue
            target_type = str(raw.get("item_type", "") or "").strip().lower()
            if target_type == "slot":
                slot_index = raw.get("slot_index")
                if not isinstance(slot_index, int):
                    continue
                key = ("previous_action", op, "slot", slot_index)
                if key in seen:
                    continue
                seen.add(key)
                normalized.append(
                    {
                        "type": "previous_action",
                        "op": op,
                        "item_type": "slot",
                        "slot_index": slot_index,
                    }
                )
            elif target_type == "manual":
                action_id = str(raw.get("action_id", "") or "").strip().lower()
                if not action_id:
                    continue
                key = ("previous_action", op, "manual", action_id)
                if key in seen:
                    continue
                seen.add(key)
                normalized.append(
                    {
                        "type": "previous_action",
                        "op": op,
                        "item_type": "manual",
                        "action_id": action_id,
                    }
                )
    if normalized:
        return normalized

    legacy_source = str(legacy_ready_source or "").strip().lower()
    legacy_buff = str(legacy_buff_roi_id or "").strip().lower()
    if legacy_source in ("buff_present", "buff_missing") and legacy_buff:
        op = "present" if legacy_source == "buff_present" else "missing"
        return [{"type": "buff_state", "buff_roi_id": legacy_buff, "op": op}]
    return []


def normalize_required_form(raw_required_form: object) -> str:
    return str(raw_required_form or "").strip().lower()


def item_matches_form(item: dict, active_form_id: str) -> bool:
    required_form = normalize_required_form(item.get("required_form"))
    if not required_form:
        return True
    return required_form == str(active_form_id or "").strip().lower()


def dot_refresh_eligible(yellow_glow_ready: bool, red_glow_ready: bool) -> bool:
    """DoT refresh eligibility: no glow OR red glow (yellow-only blocks)."""
    return (not yellow_glow_ready and not red_glow_ready) or red_glow_ready


def _red_glow_ready_from_snapshot(slot: SlotSnapshot) -> bool:
    return bool(getattr(slot, "red_glow_ready", False))


def _red_glow_ready_from_state_dict(slot_state: dict[str, Any]) -> bool:
    return bool(slot_state.get("red_glow_ready", False))


def _activation_allows(
    item: dict,
    glow_ready: bool,
    yellow_glow_ready: bool,
    red_glow_ready: bool,
) -> bool:
    rule = normalize_activation_rule(item.get("activation_rule"))
    if rule == "always":
        return True
    if rule == "require_glow":
        return bool(glow_ready)
    return dot_refresh_eligible(yellow_glow_ready, red_glow_ready)


def _dot_refresh_red_override(item: dict, red_glow_ready: bool) -> bool:
    rule = normalize_activation_rule(item.get("activation_rule"))
    return rule == "dot_refresh" and bool(red_glow_ready)


def _buff_condition_state(
    condition: dict,
    buff_states: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    if not isinstance(buff_states, dict):
        return None
    buff_id = str(condition.get("buff_roi_id", "") or "").strip().lower()
    if not buff_id:
        return None
    buff = buff_states.get(buff_id)
    if not isinstance(buff, dict):
        return None
    return buff


def _condition_buff_status_ok(buff: dict[str, Any]) -> bool:
    status = str(buff.get("status", "ok") or "").strip().lower()
    return not status or status == "ok"


def _movement_condition_passes(condition: dict, movement_active: Optional[bool]) -> bool:
    if movement_active is None:
        return True  # tracker not available, don't block
    op = str(condition.get("op", "") or "").strip().lower()
    if op == "active":
        return bool(movement_active)
    if op == "inactive":
        return not bool(movement_active)
    return False


def _previous_action_condition_passes(
    condition: dict,
    previous_action: Optional[dict[str, Any]],
) -> bool:
    if not isinstance(previous_action, dict):
        return False
    op = str(condition.get("op", "is") or "is").strip().lower()
    if op not in ("is", "is_not"):
        return False
    target_type = str(condition.get("item_type", "") or "").strip().lower()
    prev_type = str(previous_action.get("item_type", "") or "").strip().lower()
    matches = False
    if target_type == "slot":
        matches = (
            prev_type == "slot"
            and condition.get("slot_index") == previous_action.get("slot_index")
        )
    elif target_type == "manual":
        target_action_id = str(condition.get("action_id", "") or "").strip().lower()
        prev_action_id = str(previous_action.get("action_id", "") or "").strip().lower()
        matches = (
            prev_type == "manual"
            and bool(target_action_id)
            and target_action_id == prev_action_id
        )
    if op == "is_not":
        return not matches
    return matches


def _buff_condition_passes(
    condition: dict,
    buff_states: Optional[dict[str, Any]],
) -> bool:
    cond_type = str(condition.get("type", "") or "").strip().lower()
    if cond_type != "buff_state":
        return False
    buff = _buff_condition_state(condition, buff_states)
    if not isinstance(buff, dict):
        return False
    if not bool(buff.get("calibrated", False)):
        return False
    if not _condition_buff_status_ok(buff):
        return False
    present = bool(buff.get("present", False))
    op = str(condition.get("op", "") or "").strip().lower()
    if op == "present":
        return present
    if op == "missing":
        return not present
    # New ops — check single-frame candidate, not confirmed present
    candidate = bool(buff.get("candidate", False))
    if op == "candidate_present":
        return candidate
    if op == "candidate_missing":
        return not candidate
    return False


def _item_conditions(item: dict, item_type: str) -> list[dict]:
    return normalize_conditions(
        item.get("conditions", []),
        item_type=item_type,
        legacy_ready_source=item.get("ready_source"),
        legacy_buff_roi_id=item.get("buff_roi_id"),
    )


def _conditions_pass(
    item: dict,
    buff_states: Optional[dict[str, Any]],
    item_type: str,
    movement_active: Optional[bool] = None,
    previous_action: Optional[dict[str, Any]] = None,
) -> bool:
    for condition in _item_conditions(item, item_type):
        cond_type = str(condition.get("type", "") or "").strip().lower()
        if cond_type == "buff_state":
            if not _buff_condition_passes(condition, buff_states):
                return False
        elif cond_type == "moving":
            if not _movement_condition_passes(condition, movement_active):
                return False
        elif cond_type == "previous_action":
            if not _previous_action_condition_passes(condition, previous_action):
                return False
    return True


def _conditions_red_glow_ready(item: dict, buff_states: Optional[dict[str, Any]], item_type: str) -> bool:
    for condition in _item_conditions(item, item_type):
        buff = _buff_condition_state(condition, buff_states)
        if not isinstance(buff, dict):
            continue
        if not _condition_buff_status_ok(buff):
            continue
        if bool(buff.get("red_glow_ready", False)):
            return True
    return False


def slot_item_is_eligible_for_snapshot(
    item: dict,
    slot: Optional[SlotSnapshot],
    buff_states: Optional[dict[str, Any]] = None,
    active_form_id: str = "",
    movement_active: Optional[bool] = None,
    previous_action: Optional[dict[str, Any]] = None,
) -> bool:
    if not item_matches_form(item, active_form_id):
        return False
    if slot is None:
        return False
    ready_source = normalize_ready_source(item.get("ready_source"), "slot")
    condition_gate_ready = _conditions_pass(
        item,
        buff_states,
        "slot",
        movement_active=movement_active,
        previous_action=previous_action,
    )
    has_conditions = bool(_item_conditions(item, "slot"))
    slot_ready = bool(getattr(slot, "is_ready", False))
    glow_ready = bool(getattr(slot, "glow_ready", False))
    yellow_glow_ready = bool(getattr(slot, "yellow_glow_ready", False))
    red_glow_ready = _red_glow_ready_from_snapshot(slot)
    if has_conditions:
        red_glow_ready = _conditions_red_glow_ready(item, buff_states, "slot") or red_glow_ready
    if ready_source == "slot":
        if not condition_gate_ready:
            if has_conditions:
                return _dot_refresh_red_override(item, red_glow_ready) and slot_ready
            return False
        if has_conditions and _dot_refresh_red_override(item, red_glow_ready):
            return True
        if not slot_ready:
            return False
        return _activation_allows(item, glow_ready, yellow_glow_ready, red_glow_ready)
    if not condition_gate_ready:
        if has_conditions:
            return _dot_refresh_red_override(item, red_glow_ready) and slot_ready
        return False
    if has_conditions and _dot_refresh_red_override(item, red_glow_ready):
        return True
    if not slot_ready:
        return False
    return _activation_allows(item, glow_ready, yellow_glow_ready, red_glow_ready)


def slot_item_is_eligible_for_state_dict(
    item: dict,
    slot_state: Optional[dict[str, Any]],
    buff_states: Optional[dict[str, Any]] = None,
    active_form_id: str = "",
    movement_active: Optional[bool] = None,
    previous_action: Optional[dict[str, Any]] = None,
) -> bool:
    if not item_matches_form(item, active_form_id):
        return False
    if not isinstance(slot_state, dict):
        return False
    ready_source = normalize_ready_source(item.get("ready_source"), "slot")
    condition_gate_ready = _conditions_pass(
        item,
        buff_states,
        "slot",
        movement_active=movement_active,
        previous_action=previous_action,
    )
    has_conditions = bool(_item_conditions(item, "slot"))
    slot_ready = str(slot_state.get("state", "") or "").strip().lower() == "ready"
    glow_ready = bool(slot_state.get("glow_ready", False))
    yellow_glow_ready = bool(slot_state.get("yellow_glow_ready", False))
    red_glow_ready = _red_glow_ready_from_state_dict(slot_state)
    if has_conditions:
        red_glow_ready = _conditions_red_glow_ready(item, buff_states, "slot") or red_glow_ready
    if ready_source == "slot":
        if not condition_gate_ready:
            if has_conditions:
                return _dot_refresh_red_override(item, red_glow_ready) and slot_ready
            return False
        if has_conditions and _dot_refresh_red_override(item, red_glow_ready):
            return True
        if not slot_ready:
            return False
        return _activation_allows(item, glow_ready, yellow_glow_ready, red_glow_ready)
    if not condition_gate_ready:
        if has_conditions:
            return _dot_refresh_red_override(item, red_glow_ready) and slot_ready
        return False
    if has_conditions and _dot_refresh_red_override(item, red_glow_ready):
        return True
    if not slot_ready:
        return False
    return _activation_allows(item, glow_ready, yellow_glow_ready, red_glow_ready)


def manual_item_is_eligible(
    item: dict,
    buff_states: Optional[dict[str, Any]] = None,
    active_form_id: str = "",
    movement_active: Optional[bool] = None,
    previous_action: Optional[dict[str, Any]] = None,
) -> bool:
    if not item_matches_form(item, active_form_id):
        return False
    return _conditions_pass(
        item,
        buff_states,
        "manual",
        movement_active=movement_active,
        previous_action=previous_action,
    )
