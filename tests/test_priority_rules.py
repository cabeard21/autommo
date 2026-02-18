import unittest

from src.automation.priority_rules import (
    slot_item_is_eligible_for_snapshot,
    slot_item_is_eligible_for_state_dict,
)
from src.models import SlotSnapshot, SlotState


class PriorityRulesTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
