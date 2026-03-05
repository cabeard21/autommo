import sys
import types
import unittest
from unittest.mock import Mock, patch

from src.automation.key_sender import KeySender
from src.models import ActionBarState, AppConfig, SlotSnapshot, SlotState


def _ready_state() -> ActionBarState:
    return ActionBarState(slots=[SlotSnapshot(index=0, state=SlotState.READY)])


def _priority_items() -> list[dict]:
    return [
        {
            "type": "slot",
            "slot_index": 0,
            "activation_rule": "always",
            "ready_source": "slot",
        }
    ]


class KeySenderJitterTests(unittest.TestCase):
    def _make_sender(self, delay_ms: int = 150, jitter_ms: int = 0) -> tuple[KeySender, AppConfig]:
        cfg = AppConfig()
        cfg.min_press_interval_ms = delay_ms
        cfg.press_interval_jitter_ms = jitter_ms
        cfg.keybinds = ["1"]
        cfg.queue_fire_delay_ms = 0
        return KeySender(cfg), cfg

    def test_jitter_disabled_uses_fixed_interval(self) -> None:
        sender, _ = self._make_sender(delay_ms=150, jitter_ms=0)
        kb = types.SimpleNamespace(send=Mock())
        state = _ready_state()
        item = _priority_items()

        times = iter([100.0, 100.1, 100.15])
        with patch.dict(sys.modules, {"keyboard": kb}):
            with patch("src.automation.key_sender.time.time", side_effect=lambda: next(times)):
                r1 = sender.evaluate_and_send(state, item, ["1"], [], True)
                r2 = sender.evaluate_and_send(state, item, ["1"], [], True)
                r3 = sender.evaluate_and_send(state, item, ["1"], [], True)

        self.assertIsNotNone(r1)
        self.assertIsNone(r2)
        self.assertIsNotNone(r3)
        self.assertEqual(kb.send.call_count, 2)

    def test_jitter_enabled_samples_plus_minus_interval(self) -> None:
        sender, _ = self._make_sender(delay_ms=150, jitter_ms=20)
        kb = types.SimpleNamespace(send=Mock())
        state = _ready_state()
        item = _priority_items()

        times = iter([200.0, 200.16, 200.17, 200.299, 200.3])
        with patch.dict(sys.modules, {"keyboard": kb}):
            with patch("src.automation.key_sender.time.time", side_effect=lambda: next(times)):
                with patch(
                    "src.automation.key_sender.random.uniform",
                    side_effect=[170.0, 130.0, 130.0],
                ) as rand:
                    self.assertIsNotNone(
                        sender.evaluate_and_send(state, item, ["1"], [], True)
                    )
                    self.assertIsNone(sender.evaluate_and_send(state, item, ["1"], [], True))
                    self.assertIsNotNone(
                        sender.evaluate_and_send(state, item, ["1"], [], True)
                    )
                    self.assertIsNone(sender.evaluate_and_send(state, item, ["1"], [], True))
                    self.assertIsNotNone(
                        sender.evaluate_and_send(state, item, ["1"], [], True)
                    )

        self.assertEqual(kb.send.call_count, 3)
        self.assertEqual(rand.call_count, 3)
        rand.assert_any_call(130.0, 170.0)

    def test_jitter_applies_to_queued_override_sends(self) -> None:
        sender, cfg = self._make_sender(delay_ms=150, jitter_ms=20)
        cfg.gcd_ms = 0
        kb = types.SimpleNamespace(send=Mock())
        state = _ready_state()
        item = _priority_items()
        queued = {"source": "whitelist", "key": "q"}

        times = iter([300.0, 300.16, 300.17])
        with patch.dict(sys.modules, {"keyboard": kb}):
            with patch("src.automation.key_sender.time.time", side_effect=lambda: next(times)):
                with patch("src.automation.key_sender.time.sleep", return_value=None):
                    with patch("src.automation.key_sender.random.uniform", return_value=170.0):
                        r1 = sender.evaluate_and_send(
                            state,
                            item,
                            ["1"],
                            [],
                            True,
                            queued_override=queued,
                        )
                        r2 = sender.evaluate_and_send(
                            state,
                            item,
                            ["1"],
                            [],
                            True,
                            queued_override=queued,
                        )
                        r3 = sender.evaluate_and_send(
                            state,
                            item,
                            ["1"],
                            [],
                            True,
                            queued_override=queued,
                        )

        self.assertIsNotNone(r1)
        self.assertIsNone(r2)
        self.assertIsNotNone(r3)
        self.assertEqual(kb.send.call_args_list[0].args[0], "q")
        self.assertEqual(kb.send.call_args_list[1].args[0], "q")

    def test_jitter_lower_bound_clamped_to_zero(self) -> None:
        sender, _ = self._make_sender(delay_ms=50, jitter_ms=100)
        kb = types.SimpleNamespace(send=Mock())
        state = _ready_state()
        item = _priority_items()

        with patch.dict(sys.modules, {"keyboard": kb}):
            with patch("src.automation.key_sender.time.time", return_value=400.0):
                with patch(
                    "src.automation.key_sender.random.uniform", return_value=0.0
                ) as rand:
                    self.assertIsNotNone(
                        sender.evaluate_and_send(state, item, ["1"], [], True)
                    )

        rand.assert_called_once_with(0.0, 150.0)

    def test_config_round_trip_and_default_for_jitter(self) -> None:
        cfg = AppConfig()
        cfg.press_interval_jitter_ms = 42
        round_trip = AppConfig.from_dict(cfg.to_dict())
        self.assertEqual(round_trip.press_interval_jitter_ms, 42)

        defaulted = AppConfig.from_dict({"slots": {"count": 1, "keybinds": []}, "detection": {}})
        self.assertEqual(defaulted.press_interval_jitter_ms, 0)


class KeySenderBuffOnlyModeTests(unittest.TestCase):
    def test_buff_only_skips_slot_items(self) -> None:
        cfg = AppConfig()
        cfg.slot_detection_mode = "buff_only"
        sender = KeySender(cfg)
        kb = types.SimpleNamespace(send=Mock())
        state = _ready_state()
        item = _priority_items()

        with patch.dict(sys.modules, {"keyboard": kb}):
            result = sender.evaluate_and_send(state, item, ["1"], [], True)

        self.assertIsNone(result)
        kb.send.assert_not_called()

    def test_buff_only_allows_manual_action_send(self) -> None:
        cfg = AppConfig()
        cfg.slot_detection_mode = "buff_only"
        sender = KeySender(cfg)
        kb = types.SimpleNamespace(send=Mock())
        state = ActionBarState(slots=[])
        priority_items = [{"type": "manual", "action_id": "manual_1", "ready_source": "always"}]
        manual_actions = [{"id": "manual_1", "name": "Manual 1", "keybind": "q"}]

        with patch.dict(sys.modules, {"keyboard": kb}):
            result = sender.evaluate_and_send(
                state, priority_items, ["1"], manual_actions, True
            )

        self.assertIsNotNone(result)
        self.assertEqual(kb.send.call_count, 1)
        self.assertEqual(kb.send.call_args_list[0].args[0], "q")

    def test_buff_only_ignores_tracked_queue_override(self) -> None:
        cfg = AppConfig()
        cfg.slot_detection_mode = "buff_only"
        sender = KeySender(cfg)
        kb = types.SimpleNamespace(send=Mock())
        state = ActionBarState(slots=[])
        queued = {"source": "tracked", "key": "1", "slot_index": 0}

        with patch.dict(sys.modules, {"keyboard": kb}):
            result = sender.evaluate_and_send(
                state, _priority_items(), ["1"], [], True, queued_override=queued
            )

        self.assertIsNone(result)
        kb.send.assert_not_called()

    def test_buff_only_whitelist_queue_can_send_with_manual_candidate(self) -> None:
        cfg = AppConfig()
        cfg.slot_detection_mode = "buff_only"
        cfg.queue_fire_delay_ms = 0
        sender = KeySender(cfg)
        kb = types.SimpleNamespace(send=Mock())
        state = ActionBarState(slots=[])
        priority_items = [{"type": "manual", "action_id": "manual_1", "ready_source": "always"}]
        manual_actions = [{"id": "manual_1", "name": "Manual 1", "keybind": "q"}]
        queued = {"source": "whitelist", "key": "e"}

        with patch.dict(sys.modules, {"keyboard": kb}):
            result = sender.evaluate_and_send(
                state,
                priority_items,
                ["1"],
                manual_actions,
                True,
                queued_override=queued,
            )

        self.assertIsNotNone(result)
        self.assertEqual(kb.send.call_count, 1)
        self.assertEqual(kb.send.call_args_list[0].args[0], "e")

if __name__ == "__main__":
    unittest.main()
