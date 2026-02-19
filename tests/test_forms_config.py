import unittest

from src.automation.priority_rules import (
    manual_item_is_eligible,
    slot_item_is_eligible_for_state_dict,
)
from src.models import AppConfig


class FormsConfigTests(unittest.TestCase):
    def test_required_form_blocks_when_active_form_mismatch(self) -> None:
        item = {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "always",
            "ready_source": "slot",
            "required_form": "meta",
        }
        slot_state = {"state": "ready"}
        self.assertFalse(
            slot_item_is_eligible_for_state_dict(
                item,
                slot_state,
                buff_states={},
                active_form_id="normal",
            )
        )
        self.assertTrue(
            slot_item_is_eligible_for_state_dict(
                item,
                slot_state,
                buff_states={},
                active_form_id="meta",
            )
        )

    def test_manual_required_form_blocks_when_active_form_mismatch(self) -> None:
        item = {
            "type": "manual",
            "action_id": "manual_1",
            "ready_source": "always",
            "required_form": "meta",
        }
        self.assertFalse(
            manual_item_is_eligible(item, buff_states=None, active_form_id="normal")
        )
        self.assertTrue(
            manual_item_is_eligible(item, buff_states=None, active_form_id="meta")
        )

    def test_from_dict_migrates_legacy_slot_baselines_to_normal_form(self) -> None:
        legacy_baselines = [{"shape": [2, 2], "data": "AAAAAA=="}]
        cfg = AppConfig.from_dict(
            {
                "slot_baselines": legacy_baselines,
                "slots": {"count": 10, "gap_pixels": 2, "padding": 3, "keybinds": []},
                "detection": {},
            }
        )
        self.assertIn("normal", cfg.slot_baselines_by_form)
        self.assertEqual(cfg.slot_baselines_by_form["normal"], legacy_baselines)

    def test_from_dict_parses_cooldown_group_by_slot(self) -> None:
        cfg = AppConfig.from_dict(
            {
                "slots": {"count": 10, "gap_pixels": 2, "padding": 3, "keybinds": []},
                "detection": {"cooldown_group_by_slot": {"0": "builders", "1": "builders"}},
            }
        )
        self.assertEqual(cfg.cooldown_group_by_slot.get(0), "builders")
        self.assertEqual(cfg.cooldown_group_by_slot.get(1), "builders")

    def test_from_dict_parses_detection_region_overrides_by_form(self) -> None:
        cfg = AppConfig.from_dict(
            {
                "slots": {"count": 10, "gap_pixels": 2, "padding": 3, "keybinds": []},
                "forms": [
                    {"id": "normal", "name": "Normal"},
                    {"id": "form_1", "name": "Meta"},
                ],
                "detection": {
                    "detection_region_overrides": {"1": "top_left"},
                    "detection_region_overrides_by_form": {
                        "normal": {"1": "top_left"},
                        "form_1": {"1": "full"},
                    },
                },
            }
        )
        self.assertEqual(cfg.detection_region_overrides.get(1), "top_left")
        self.assertEqual(
            cfg.detection_region_overrides_by_form.get("normal", {}).get(1), "top_left"
        )
        self.assertEqual(
            cfg.detection_region_overrides_by_form.get("form_1", {}).get(1), "full"
        )

        serialized = cfg.to_dict()
        by_form = serialized.get("detection", {}).get(
            "detection_region_overrides_by_form", {}
        )
        self.assertEqual(by_form.get("normal", {}).get("1"), "top_left")
        self.assertEqual(by_form.get("form_1", {}).get("1"), "full")


if __name__ == "__main__":
    unittest.main()
