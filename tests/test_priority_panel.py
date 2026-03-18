import os
import unittest

from PyQt6.QtWidgets import QApplication

from src.ui.priority_panel import PriorityListWidget


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class PriorityPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_previous_action_target_options_include_current_manual_item(self) -> None:
        widget = PriorityListWidget()
        widget.set_manual_actions(
            [
                {"id": "manual_1", "name": "Kick", "keybind": "1"},
                {"id": "manual_2", "name": "Stun", "keybind": "2"},
            ]
        )
        widget.set_items(
            [
                {"type": "manual", "action_id": "manual_1", "item_id": "m1"},
                {"type": "manual", "action_id": "manual_2", "item_id": "m2"},
            ]
        )

        options = widget._previous_action_target_options("m1")

        self.assertIn(
            {
                "label": "Kick",
                "condition": {
                    "type": "previous_action",
                    "item_type": "manual",
                    "action_id": "manual_1",
                },
            },
            options,
        )

    def test_previous_action_target_options_include_current_slot_item(self) -> None:
        widget = PriorityListWidget()
        widget.set_display_names(["Fireball", "Frostbolt"])
        widget.set_items(
            [
                {"type": "slot", "slot_index": 0, "item_id": "s1"},
                {"type": "slot", "slot_index": 1, "item_id": "s2"},
            ]
        )

        options = widget._previous_action_target_options("s1")

        self.assertIn(
            {
                "label": "Fireball",
                "condition": {
                    "type": "previous_action",
                    "item_type": "slot",
                    "slot_index": 0,
                },
            },
            options,
        )


if __name__ == "__main__":
    unittest.main()
