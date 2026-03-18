"""Helpers for mutating automation priority profiles."""
from __future__ import annotations

import uuid

from src.automation.binds import normalize_bind


def next_manual_action_id(actions: list[dict]) -> str:
    existing_ids = {
        str(a.get("id", "") or "").strip().lower()
        for a in actions
        if isinstance(a, dict)
    }
    i = 1
    while f"manual_{i}" in existing_ids:
        i += 1
    return f"manual_{i}"


def copy_manual_action_in_profile(
    profile: dict, action_id: str
) -> tuple[list[dict], list[dict]] | tuple[None, None]:
    aid = (action_id or "").strip().lower()
    if not aid:
        return None, None

    actions = [a for a in list(profile.get("manual_actions", [])) if isinstance(a, dict)]
    items = [i for i in list(profile.get("priority_items", [])) if isinstance(i, dict)]
    source_action = next(
        (
            a
            for a in actions
            if str(a.get("id", "") or "").strip().lower() == aid
        ),
        None,
    )
    if not isinstance(source_action, dict):
        return None, None

    source_index = next(
        (
            idx
            for idx, item in enumerate(items)
            if str(item.get("type", "") or "").strip().lower() == "manual"
            and str(item.get("action_id", "") or "").strip().lower() == aid
        ),
        None,
    )
    if source_index is None:
        return None, None

    new_action_id = next_manual_action_id(actions)
    actions.append(
        {
            "id": new_action_id,
            "name": str(source_action.get("name", "") or "").strip() or "Manual Action",
            "keybind": normalize_bind(str(source_action.get("keybind", "") or "").strip()),
        }
    )

    source_item = dict(items[source_index])
    copied_item = {
        "type": "manual",
        "action_id": new_action_id,
        "item_id": uuid.uuid4().hex[:8],
        "ready_source": str(source_item.get("ready_source", "") or "").strip().lower()
        or "always",
        "buff_roi_id": str(source_item.get("buff_roi_id", "") or "").strip().lower(),
        "conditions": _normalize_manual_conditions(source_item.get("conditions", [])),
        "required_form": str(source_item.get("required_form", "") or "").strip().lower(),
        "cast_does_not_block": bool(source_item.get("cast_does_not_block", True)),
    }
    items.insert(source_index + 1, copied_item)
    return actions, items


def _normalize_manual_conditions(raw_conditions: object) -> list[dict]:
    normalized: list[dict] = []
    seen: set[tuple] = set()
    for raw in list(raw_conditions or []):
        if not isinstance(raw, dict):
            continue
        cond_type = str(raw.get("type", "") or "").strip().lower()
        if cond_type == "buff_state":
            buff_id = str(raw.get("buff_roi_id", "") or "").strip().lower()
            op = str(raw.get("op", "") or "").strip().lower()
            key = (cond_type, buff_id, op)
            if not buff_id or op not in (
                "present",
                "missing",
                "candidate_present",
                "candidate_missing",
            ):
                continue
        elif cond_type == "moving":
            op = str(raw.get("op", "") or "").strip().lower()
            key = (cond_type, op)
            if op not in ("active", "inactive"):
                continue
        elif cond_type == "previous_action":
            op = str(raw.get("op", "is") or "is").strip().lower()
            if op not in ("is", "is_not"):
                continue
            target_type = str(raw.get("item_type", "") or "").strip().lower()
            if target_type == "slot":
                slot_index = raw.get("slot_index")
                key = (cond_type, op, target_type, slot_index)
                if not isinstance(slot_index, int):
                    continue
                normalized_raw = {
                    "type": "previous_action",
                    "op": op,
                    "item_type": "slot",
                    "slot_index": slot_index,
                }
            elif target_type == "manual":
                action_id = str(raw.get("action_id", "") or "").strip().lower()
                key = (cond_type, op, target_type, action_id)
                if not action_id:
                    continue
                normalized_raw = {
                    "type": "previous_action",
                    "op": op,
                    "item_type": "manual",
                    "action_id": action_id,
                }
            else:
                continue
        else:
            continue
        if key in seen:
            continue
        seen.add(key)
        normalized.append(normalized_raw if cond_type == "previous_action" else dict(raw))
    return normalized
