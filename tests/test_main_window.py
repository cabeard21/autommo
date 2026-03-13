import unittest

from src.app.priority_profiles import copy_manual_action_in_profile


class MainWindowTests(unittest.TestCase):
    def test_copy_manual_action_duplicates_action_and_inserts_new_item_below(self) -> None:
        profile = {
            "manual_actions": [
                {"id": "manual_1", "name": "Kick", "keybind": "F"},
                {"id": "manual_2", "name": "Stun", "keybind": "G"},
            ],
            "priority_items": [
                {
                    "type": "slot",
                    "slot_index": 0,
                    "item_id": "slot0",
                    "activation_rule": "always",
                    "required_form": "",
                },
                {
                    "type": "manual",
                    "action_id": "manual_1",
                    "item_id": "man1a",
                    "ready_source": "always",
                    "buff_roi_id": "",
                    "conditions": [],
                    "required_form": "",
                    "cast_does_not_block": True,
                },
                {
                    "type": "manual",
                    "action_id": "manual_1",
                    "item_id": "man1b",
                    "ready_source": "always",
                    "buff_roi_id": "",
                    "conditions": [{"type": "moving", "op": "active"}],
                    "required_form": "bear",
                    "cast_does_not_block": False,
                },
                {
                    "type": "manual",
                    "action_id": "manual_2",
                    "item_id": "man2",
                    "ready_source": "always",
                    "buff_roi_id": "",
                    "conditions": [],
                    "required_form": "",
                    "cast_does_not_block": True,
                },
            ],
        }

        actions, items = copy_manual_action_in_profile(profile, "manual_1")

        self.assertIsNotNone(actions)
        self.assertIsNotNone(items)
        assert actions is not None
        assert items is not None
        self.assertEqual(["manual_1", "manual_2", "manual_3"], [a["id"] for a in actions])
        self.assertEqual("Kick", actions[-1]["name"])
        self.assertEqual("f", actions[-1]["keybind"])

        self.assertEqual(["slot", "manual_1", "manual_3", "manual_1", "manual_2"], [
            ("slot" if item.get("type") == "slot" else item.get("action_id"))
            for item in items
        ])
        copied = items[2]
        self.assertEqual("manual", copied["type"])
        self.assertEqual("manual_3", copied["action_id"])
        self.assertNotEqual("man1a", copied["item_id"])
        self.assertEqual("always", copied["ready_source"])
        self.assertEqual([], copied["conditions"])
        self.assertEqual("", copied["required_form"])
        self.assertTrue(copied["cast_does_not_block"])


if __name__ == "__main__":
    unittest.main()
