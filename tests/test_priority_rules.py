import unittest

from src.automation.priority_rules import (
    manual_item_is_eligible,
    slot_item_is_eligible_for_snapshot,
    slot_item_is_eligible_for_state_dict,
)
from src.models import SlotSnapshot, SlotState


class PriorityRulesTests(unittest.TestCase):
    def test_manual_always_is_eligible_without_buff_state(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_1",
            "ready_source": "always",
            "buff_roi_id": "",
        }
        self.assertTrue(manual_item_is_eligible(item, buff_states=None))

    def test_manual_buff_present_requires_calibrated_present_ok(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_1",
            "ready_source": "buff_present",
            "buff_roi_id": "dot1",
        }
        buff_states = {"dot1": {"calibrated": True, "present": True, "status": "ok"}}
        self.assertTrue(manual_item_is_eligible(item, buff_states=buff_states))

    def test_manual_buff_missing_requires_calibrated_missing_ok(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_1",
            "ready_source": "buff_missing",
            "buff_roi_id": "dot1",
        }
        buff_states = {"dot1": {"calibrated": True, "present": False, "status": "ok"}}
        self.assertTrue(manual_item_is_eligible(item, buff_states=buff_states))

    def test_manual_buff_present_not_eligible_when_uncalibrated(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_1",
            "ready_source": "buff_present",
            "buff_roi_id": "dot1",
        }
        buff_states = {"dot1": {"calibrated": False, "present": True, "status": "ok"}}
        self.assertFalse(manual_item_is_eligible(item, buff_states=buff_states))

    def test_manual_buff_present_not_eligible_when_status_not_ok(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_1",
            "ready_source": "buff_present",
            "buff_roi_id": "dot1",
        }
        buff_states = {
            "dot1": {"calibrated": True, "present": True, "status": "out-of-frame"}
        }
        self.assertFalse(manual_item_is_eligible(item, buff_states=buff_states))

    def test_buff_gated_dot_refresh_does_not_bypass_failed_buff_gate_when_slot_not_ready_state_dict(
        self,
    ) -> None:
        item = {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "dot_refresh",
            "ready_source": "buff_missing",
            "buff_roi_id": "dot1",
        }
        slot_state = {
            "state": "on_cooldown",
            "yellow_glow_ready": False,
            "red_glow_ready": True,
        }
        buff_states = {"dot1": {"calibrated": True, "present": True, "status": "ok"}}
        self.assertFalse(
            slot_item_is_eligible_for_state_dict(item, slot_state, buff_states=buff_states)
        )

    def test_buff_gated_dot_refresh_can_bypass_failed_buff_gate_when_slot_ready_state_dict(
        self,
    ) -> None:
        item = {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "dot_refresh",
            "ready_source": "buff_missing",
            "buff_roi_id": "dot1",
        }
        slot_state = {
            "state": "ready",
            "yellow_glow_ready": False,
            "red_glow_ready": False,
        }
        buff_states = {
            "dot1": {
                "calibrated": True,
                "present": True,
                "status": "ok",
                "red_glow_ready": True,
            }
        }
        self.assertTrue(
            slot_item_is_eligible_for_state_dict(item, slot_state, buff_states=buff_states)
        )

    def test_buff_gated_dot_refresh_stays_blocked_without_red_glow_state_dict(self) -> None:
        item = {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "dot_refresh",
            "ready_source": "buff_missing",
            "buff_roi_id": "dot1",
        }
        slot_state = {
            "state": "on_cooldown",
            "yellow_glow_ready": False,
            "red_glow_ready": False,
        }
        buff_states = {"dot1": {"calibrated": True, "present": True, "status": "ok"}}
        self.assertFalse(
            slot_item_is_eligible_for_state_dict(item, slot_state, buff_states=buff_states)
        )

    def test_buff_gated_dot_refresh_requires_slot_ready_without_red_override_state_dict(self) -> None:
        item = {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "dot_refresh",
            "ready_source": "buff_missing",
            "buff_roi_id": "dot1",
        }
        slot_state = {
            "state": "on_cooldown",
            "yellow_glow_ready": False,
            "red_glow_ready": False,
        }
        buff_states = {"dot1": {"calibrated": True, "present": False, "status": "ok"}}
        self.assertFalse(
            slot_item_is_eligible_for_state_dict(item, slot_state, buff_states=buff_states)
        )

    def test_buff_gated_dot_refresh_allows_red_override_when_buff_gate_passes_state_dict(self) -> None:
        item = {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "dot_refresh",
            "ready_source": "buff_missing",
            "buff_roi_id": "dot1",
        }
        slot_state = {
            "state": "on_cooldown",
            "yellow_glow_ready": False,
            "red_glow_ready": False,
        }
        buff_states = {
            "dot1": {
                "calibrated": True,
                "present": False,
                "status": "ok",
                "red_glow_ready": True,
            }
        }
        self.assertTrue(
            slot_item_is_eligible_for_state_dict(item, slot_state, buff_states=buff_states)
        )

    def test_buff_gated_requires_slot_ready_even_when_buff_gate_passes_state_dict(self) -> None:
        item = {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "always",
            "ready_source": "buff_missing",
            "buff_roi_id": "dot1",
        }
        slot_state = {
            "state": "on_cooldown",
            "yellow_glow_ready": False,
            "red_glow_ready": False,
        }
        buff_states = {"dot1": {"calibrated": True, "present": False, "status": "ok"}}
        self.assertFalse(
            slot_item_is_eligible_for_state_dict(item, slot_state, buff_states=buff_states)
        )

    def test_buff_gated_eligible_when_buff_gate_and_slot_ready_state_dict(self) -> None:
        item = {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "always",
            "ready_source": "buff_missing",
            "buff_roi_id": "dot1",
        }
        slot_state = {
            "state": "ready",
            "yellow_glow_ready": False,
            "red_glow_ready": False,
        }
        buff_states = {"dot1": {"calibrated": True, "present": False, "status": "ok"}}
        self.assertTrue(
            slot_item_is_eligible_for_state_dict(item, slot_state, buff_states=buff_states)
        )

    def test_buff_gated_always_does_not_use_red_glow_override_state_dict(self) -> None:
        item = {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "always",
            "ready_source": "buff_missing",
            "buff_roi_id": "dot1",
        }
        slot_state = {
            "state": "on_cooldown",
            "yellow_glow_ready": False,
            "red_glow_ready": True,
        }
        buff_states = {"dot1": {"calibrated": True, "present": True, "status": "ok"}}
        self.assertFalse(
            slot_item_is_eligible_for_state_dict(item, slot_state, buff_states=buff_states)
        )

    def test_buff_missing_not_eligible_when_buff_status_not_ok(self) -> None:
        item = {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "always",
            "ready_source": "buff_missing",
            "buff_roi_id": "dot1",
        }
        slot_state = {"state": "ready"}
        buff_states = {
            "dot1": {"calibrated": True, "present": False, "status": "out-of-frame"}
        }
        self.assertFalse(
            slot_item_is_eligible_for_state_dict(item, slot_state, buff_states=buff_states)
        )

    def test_slot_source_dot_refresh_behavior_is_unchanged_snapshot(self) -> None:
        item = {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "dot_refresh",
            "ready_source": "slot",
        }
        slot = SlotSnapshot(
            index=0,
            state=SlotState.READY,
            yellow_glow_ready=True,
            red_glow_ready=False,
        )
        self.assertFalse(slot_item_is_eligible_for_snapshot(item, slot, buff_states={}))

    def test_require_glow_state_dict_requires_slot_ready_and_glow(self) -> None:
        item = {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "require_glow",
            "ready_source": "slot",
        }
        self.assertTrue(
            slot_item_is_eligible_for_state_dict(
                item,
                {"state": "ready", "glow_ready": True},
                buff_states={},
            )
        )
        self.assertFalse(
            slot_item_is_eligible_for_state_dict(
                item,
                {"state": "ready", "glow_ready": False},
                buff_states={},
            )
        )
        self.assertFalse(
            slot_item_is_eligible_for_state_dict(
                item,
                {"state": "on_cooldown", "glow_ready": True},
                buff_states={},
            )
        )

    def test_require_glow_snapshot_requires_glow_ready(self) -> None:
        item = {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "require_glow",
            "ready_source": "slot",
        }
        slot_glow = SlotSnapshot(
            index=0,
            state=SlotState.READY,
            glow_ready=True,
        )
        slot_no_glow = SlotSnapshot(
            index=0,
            state=SlotState.READY,
            glow_ready=False,
        )
        self.assertTrue(slot_item_is_eligible_for_snapshot(item, slot_glow, buff_states={}))
        self.assertFalse(
            slot_item_is_eligible_for_snapshot(item, slot_no_glow, buff_states={})
        )

    def test_manual_multiple_buff_conditions_are_anded(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_1",
            "ready_source": "always",
            "conditions": [
                {"type": "buff_state", "buff_roi_id": "a", "op": "present"},
                {"type": "buff_state", "buff_roi_id": "b", "op": "missing"},
            ],
        }
        buff_states = {
            "a": {"calibrated": True, "present": True, "status": "ok"},
            "b": {"calibrated": True, "present": False, "status": "ok"},
        }
        self.assertTrue(manual_item_is_eligible(item, buff_states=buff_states))
        buff_states["b"]["present"] = True
        self.assertFalse(manual_item_is_eligible(item, buff_states=buff_states))

    def test_slot_multiple_conditions_dot_refresh_red_override_uses_any_condition_buff(self) -> None:
        item = {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "dot_refresh",
            "ready_source": "slot",
            "conditions": [
                {"type": "buff_state", "buff_roi_id": "a", "op": "missing"},
                {"type": "buff_state", "buff_roi_id": "b", "op": "present"},
            ],
        }
        slot_state = {
            "state": "ready",
            "yellow_glow_ready": False,
            "red_glow_ready": False,
        }
        buff_states = {
            "a": {
                "calibrated": True,
                "present": True,  # fails missing
                "status": "ok",
                "red_glow_ready": True,  # override source
            },
            "b": {"calibrated": True, "present": False, "status": "ok"},
        }
        self.assertTrue(
            slot_item_is_eligible_for_state_dict(item, slot_state, buff_states=buff_states)
        )

    # --- candidate_present / candidate_missing tests ---

    def test_candidate_present_passes_when_candidate_true(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_1",
            "ready_source": "always",
            "conditions": [
                {"type": "buff_state", "buff_roi_id": "charge", "op": "candidate_present"}
            ],
        }
        buff_states = {"charge": {"calibrated": True, "present": False, "candidate": True, "status": "ok"}}
        self.assertTrue(manual_item_is_eligible(item, buff_states=buff_states))

    def test_candidate_present_fails_when_candidate_false(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_1",
            "ready_source": "always",
            "conditions": [
                {"type": "buff_state", "buff_roi_id": "charge", "op": "candidate_present"}
            ],
        }
        buff_states = {"charge": {"calibrated": True, "present": False, "candidate": False, "status": "ok"}}
        self.assertFalse(manual_item_is_eligible(item, buff_states=buff_states))

    def test_candidate_missing_passes_when_candidate_false(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_1",
            "ready_source": "always",
            "conditions": [
                {"type": "buff_state", "buff_roi_id": "charge", "op": "candidate_missing"}
            ],
        }
        buff_states = {"charge": {"calibrated": True, "present": False, "candidate": False, "status": "ok"}}
        self.assertTrue(manual_item_is_eligible(item, buff_states=buff_states))

    def test_candidate_missing_fails_when_candidate_true(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_1",
            "ready_source": "always",
            "conditions": [
                {"type": "buff_state", "buff_roi_id": "charge", "op": "candidate_missing"}
            ],
        }
        buff_states = {"charge": {"calibrated": True, "present": False, "candidate": True, "status": "ok"}}
        self.assertFalse(manual_item_is_eligible(item, buff_states=buff_states))

    def test_candidate_missing_blocks_when_fully_present_and_candidate(self) -> None:
        """Regression: candidate_missing blocks even if buff is fully confirmed present."""
        item = {
            "type": "manual",
            "action_id": "manual_1",
            "ready_source": "always",
            "conditions": [
                {"type": "buff_state", "buff_roi_id": "charge", "op": "candidate_missing"}
            ],
        }
        buff_states = {"charge": {"calibrated": True, "present": True, "candidate": True, "status": "ok"}}
        self.assertFalse(manual_item_is_eligible(item, buff_states=buff_states))

    def test_candidate_present_blocks_when_uncalibrated(self) -> None:
        """Uncalibrated buff still blocks candidate_present (inherited guard)."""
        item = {
            "type": "manual",
            "action_id": "manual_1",
            "ready_source": "always",
            "conditions": [
                {"type": "buff_state", "buff_roi_id": "charge", "op": "candidate_present"}
            ],
        }
        buff_states = {"charge": {"calibrated": False, "present": False, "candidate": True, "status": "ok"}}
        self.assertFalse(manual_item_is_eligible(item, buff_states=buff_states))

    def test_manual_previous_action_matches_slot(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_2",
            "conditions": [
                {"type": "previous_action", "item_type": "slot", "slot_index": 0}
            ],
        }
        previous_action = {"item_type": "slot", "slot_index": 0, "timestamp": 1.0}
        self.assertTrue(
            manual_item_is_eligible(item, buff_states=None, previous_action=previous_action)
        )

    def test_manual_previous_action_matches_manual(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_2",
            "conditions": [
                {"type": "previous_action", "item_type": "manual", "action_id": "manual_1"}
            ],
        }
        previous_action = {"item_type": "manual", "action_id": "manual_1", "timestamp": 1.0}
        self.assertTrue(
            manual_item_is_eligible(item, buff_states=None, previous_action=previous_action)
        )

    def test_manual_previous_action_fails_without_previous_action(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_2",
            "conditions": [
                {"type": "previous_action", "item_type": "manual", "action_id": "manual_1"}
            ],
        }
        self.assertFalse(manual_item_is_eligible(item, buff_states=None, previous_action=None))

    def test_manual_previous_action_fails_when_different(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_2",
            "conditions": [
                {"type": "previous_action", "item_type": "manual", "action_id": "manual_1"}
            ],
        }
        previous_action = {"item_type": "manual", "action_id": "manual_3", "timestamp": 1.0}
        self.assertFalse(
            manual_item_is_eligible(item, buff_states=None, previous_action=previous_action)
        )

    def test_slot_previous_action_and_buff_conditions_are_anded(self) -> None:
        item = {
            "type": "slot",
            "slot_index": 1,
            "activation_rule": "always",
            "ready_source": "slot",
            "conditions": [
                {"type": "previous_action", "item_type": "manual", "action_id": "manual_1"},
                {"type": "buff_state", "buff_roi_id": "dot1", "op": "missing"},
            ],
        }
        slot_state = {"state": "ready"}
        buff_states = {"dot1": {"calibrated": True, "present": False, "status": "ok"}}
        previous_action = {"item_type": "manual", "action_id": "manual_1", "timestamp": 1.0}
        self.assertTrue(
            slot_item_is_eligible_for_state_dict(
                item,
                slot_state,
                buff_states=buff_states,
                previous_action=previous_action,
            )
        )
        self.assertFalse(
            slot_item_is_eligible_for_state_dict(
                item,
                slot_state,
                buff_states=buff_states,
                previous_action={"item_type": "manual", "action_id": "manual_9"},
            )
        )

    def test_manual_previous_action_is_not_passes_when_different(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_2",
            "conditions": [
                {
                    "type": "previous_action",
                    "op": "is_not",
                    "item_type": "manual",
                    "action_id": "manual_1",
                }
            ],
        }
        previous_action = {"item_type": "manual", "action_id": "manual_3", "timestamp": 1.0}
        self.assertTrue(
            manual_item_is_eligible(item, buff_states=None, previous_action=previous_action)
        )

    def test_manual_previous_action_is_not_fails_when_same(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_2",
            "conditions": [
                {
                    "type": "previous_action",
                    "op": "is_not",
                    "item_type": "manual",
                    "action_id": "manual_1",
                }
            ],
        }
        previous_action = {"item_type": "manual", "action_id": "manual_1", "timestamp": 1.0}
        self.assertFalse(
            manual_item_is_eligible(item, buff_states=None, previous_action=previous_action)
        )

    def test_manual_previous_action_is_not_fails_without_previous_action(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_2",
            "conditions": [
                {
                    "type": "previous_action",
                    "op": "is_not",
                    "item_type": "manual",
                    "action_id": "manual_1",
                }
            ],
        }
        self.assertFalse(manual_item_is_eligible(item, buff_states=None, previous_action=None))

    def test_slot_previous_action_is_not_and_buff_conditions_are_anded(self) -> None:
        item = {
            "type": "slot",
            "slot_index": 1,
            "activation_rule": "always",
            "ready_source": "slot",
            "conditions": [
                {
                    "type": "previous_action",
                    "op": "is_not",
                    "item_type": "manual",
                    "action_id": "manual_1",
                },
                {"type": "buff_state", "buff_roi_id": "dot1", "op": "missing"},
            ],
        }
        slot_state = {"state": "ready"}
        buff_states = {"dot1": {"calibrated": True, "present": False, "status": "ok"}}
        self.assertTrue(
            slot_item_is_eligible_for_state_dict(
                item,
                slot_state,
                buff_states=buff_states,
                previous_action={"item_type": "manual", "action_id": "manual_9"},
            )
        )
        self.assertFalse(
            slot_item_is_eligible_for_state_dict(
                item,
                slot_state,
                buff_states=buff_states,
                previous_action={"item_type": "manual", "action_id": "manual_1"},
            )
        )

    def test_manual_previous_action_is_self_passes_when_previous_action_is_same_manual(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_2",
            "conditions": [
                {
                    "type": "previous_action",
                    "op": "is",
                    "item_type": "manual",
                    "action_id": "manual_2",
                }
            ],
        }
        previous_action = {"item_type": "manual", "action_id": "manual_2", "timestamp": 1.0}
        self.assertTrue(
            manual_item_is_eligible(item, buff_states=None, previous_action=previous_action)
        )

    def test_manual_previous_action_is_not_self_passes_when_previous_action_differs(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_2",
            "conditions": [
                {
                    "type": "previous_action",
                    "op": "is_not",
                    "item_type": "manual",
                    "action_id": "manual_2",
                }
            ],
        }
        previous_action = {"item_type": "manual", "action_id": "manual_3", "timestamp": 1.0}
        self.assertTrue(
            manual_item_is_eligible(item, buff_states=None, previous_action=previous_action)
        )

    def test_manual_previous_action_is_not_self_fails_when_previous_action_is_same(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_2",
            "conditions": [
                {
                    "type": "previous_action",
                    "op": "is_not",
                    "item_type": "manual",
                    "action_id": "manual_2",
                }
            ],
        }
        previous_action = {"item_type": "manual", "action_id": "manual_2", "timestamp": 1.0}
        self.assertFalse(
            manual_item_is_eligible(item, buff_states=None, previous_action=previous_action)
        )


if __name__ == "__main__":
    unittest.main()
