"""Key sender — sends keypresses based on slot states and priority order."""

from __future__ import annotations

import logging
import random
import sys
import time
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from src.models import AppConfig

from src.models import ActionBarState, SlotState
from src.automation.binds import normalize_bind, parse_bind
from src.automation.priority_rules import (
    item_matches_form,
    manual_item_is_eligible,
    slot_item_is_eligible_for_snapshot,
)

logger = logging.getLogger(__name__)

_WIN_SCAN_CODE_SLASH = 53
_WIN_SCAN_CODE_NUM_DIVIDE = 57397


def _is_target_window_active_win(target_title: str) -> bool:
    """Windows: True if foreground window title contains target_title (case-insensitive), or if target_title is empty."""
    if not (target_title or "").strip():
        return True
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        foreground = buf.value or ""
        return target_title.strip().lower() in foreground.lower()
    except Exception as e:
        logger.debug("Foreground window check failed: %s", e)
        return False


def is_target_window_active(target_window_title: str) -> bool:
    """True if we may send keys (target window focused or no target set)."""
    if sys.platform != "win32":
        return True
    return _is_target_window_active_win(target_window_title or "")


class KeySender:
    """Sends keypresses for the first READY slot in priority order, with min delay and optional window check."""

    def __init__(self, config: "AppConfig"):
        self._config = config
        self._last_send_time = 0.0
        self._next_send_allowed_at = 0.0
        # After sending a queued key, don't send priority key until this time (so game gets only the queued key).
        self._suppress_priority_until = 0.0
        self._single_fire_pending = False
        self._last_sent_item: Optional[dict] = None

    def update_config(self, config: "AppConfig") -> None:
        self._config = config

    def request_single_fire(self) -> None:
        """Arm one key send for the next valid ready action."""
        self._single_fire_pending = True

    def is_target_window_active(self) -> bool:
        """True if foreground window matches target_window_title, or target is empty."""
        return is_target_window_active(
            getattr(self._config, "target_window_title", "") or ""
        )

    def _blocking_cast_state(
        self, state: ActionBarState
    ) -> tuple[bool, Optional[float]]:
        """Return global cast-bar gate state and optional end timestamp."""
        return bool(getattr(state, "cast_active", False)), getattr(state, "cast_ends_at", None)

    def _sample_press_interval_sec(self) -> float:
        """Return next send gate interval with optional +/- jitter."""
        base_ms = max(0, int(getattr(self._config, "min_press_interval_ms", 150) or 0))
        jitter_ms = max(
            0, int(getattr(self._config, "press_interval_jitter_ms", 0) or 0)
        )
        if jitter_ms <= 0:
            return base_ms / 1000.0
        low_ms = max(0.0, float(base_ms - jitter_ms))
        high_ms = max(low_ms, float(base_ms + jitter_ms))
        return random.uniform(low_ms, high_ms) / 1000.0

    def _record_send(self, now: float) -> None:
        self._last_send_time = now
        self._next_send_allowed_at = now + self._sample_press_interval_sec()

    @staticmethod
    def _send_keybind(keyboard_module, keybind: str) -> None:
        parsed = parse_bind(keybind)
        if parsed is None:
            keyboard_module.send(keybind)
            return
        modifiers, primary = parsed
        if sys.platform != "win32" or primary not in ("slash", "num divide"):
            keyboard_module.send(keybind)
            return
        scan_code = (
            _WIN_SCAN_CODE_SLASH if primary == "slash" else _WIN_SCAN_CODE_NUM_DIVIDE
        )
        ordered_modifiers = [m for m in ("ctrl", "shift", "alt") if m in modifiers]
        for modifier in ordered_modifiers:
            keyboard_module.press(modifier)
        keyboard_module.press(scan_code)
        keyboard_module.release(scan_code)
        for modifier in reversed(ordered_modifiers):
            keyboard_module.release(modifier)

    def last_previous_action(self) -> Optional[dict]:
        if not isinstance(self._last_sent_item, dict):
            return None
        return dict(self._last_sent_item)

    @staticmethod
    def _item_previous_action_identity(
        item: dict,
        keybind: str,
        timestamp: float,
    ) -> Optional[dict]:
        if not isinstance(item, dict):
            return None
        item_type = str(item.get("type", "") or "").strip().lower()
        if item_type == "slot":
            slot_index = item.get("slot_index")
            if not isinstance(slot_index, int):
                return None
            return {
                "item_type": "slot",
                "slot_index": slot_index,
                "action_id": "",
                "keybind": keybind,
                "timestamp": timestamp,
            }
        if item_type == "manual":
            action_id = str(item.get("action_id", "") or "").strip().lower()
            if not action_id:
                return None
            return {
                "item_type": "manual",
                "slot_index": None,
                "action_id": action_id,
                "keybind": keybind,
                "timestamp": timestamp,
            }
        return None

    def evaluate_and_send(
        self,
        state: ActionBarState,
        priority_items: list[dict],
        keybinds: list[str],
        manual_actions: list[dict],
        automation_enabled: bool,
        buff_states: Optional[dict] = None,
        queued_override: Optional[dict] = None,
        on_queued_sent: Optional[Callable[[], None]] = None,
        movement_active: Optional[bool] = None,
    ) -> Optional[dict]:
        """
        If automation enabled, optionally handle queued override first (whitelist or tracked slot);
        then find first READY slot in priority_order and send its keybind. Returns None if nothing
        sent/blocked; otherwise a dict for the UI (may include "queued": True).
        """
        single_fire_pending = self._single_fire_pending
        if not automation_enabled and not single_fire_pending:
            return None

        now = time.time()
        min_interval_ok = now >= self._next_send_allowed_at
        window_ok = self.is_target_window_active()

        allow_while_casting = bool(
            getattr(self._config, "allow_cast_while_casting", False)
        )
        if not allow_while_casting:
            cast_active, cast_ends_at = self._blocking_cast_state(state)
            if cast_active:
                last_item_dnb = bool(
                    (self._last_sent_item or {}).get("cast_does_not_block", False)
                )
                if not last_item_dnb:
                    queue_window_sec = (
                        getattr(self._config, "queue_window_ms", 120) or 120
                    ) / 1000.0
                    if cast_ends_at is None or now < (cast_ends_at + queue_window_sec):
                        return {
                            "action": "blocked",
                            "reason": "casting",
                            "slot_index": None,
                            "cast_ends_at": cast_ends_at,
                        }

        slots_by_index = {s.index: s for s in state.slots}
        active_form_id = str(getattr(self._config, "active_form_id", "normal") or "normal").strip().lower()
        slot_detection_mode = str(
            getattr(self._config, "slot_detection_mode", "slot") or "slot"
        ).strip().lower()
        slot_detection_enabled = slot_detection_mode == "slot"
        previous_action = self.last_previous_action()

        def _priority_item_eligible(item: dict) -> bool:
            if not isinstance(item, dict):
                return False
            if not item_matches_form(item, active_form_id):
                return False
            item_type = str(item.get("type", "") or "").strip().lower()
            if item_type == "slot":
                if not slot_detection_enabled:
                    return False
                slot_index = item.get("slot_index")
                if not isinstance(slot_index, int):
                    return False
                slot = slots_by_index.get(slot_index)
                return slot_item_is_eligible_for_snapshot(
                    item,
                    slot,
                    buff_states=buff_states,
                    active_form_id=active_form_id,
                    movement_active=movement_active,
                    previous_action=previous_action,
                )
            if item_type == "manual":
                return manual_item_is_eligible(
                    item,
                    buff_states=buff_states,
                    active_form_id=active_form_id,
                    movement_active=movement_active,
                    previous_action=previous_action,
                )
            return False

        # Queued key fires only when at least one priority action is currently eligible.
        any_priority_ready = any(_priority_item_eligible(item) for item in (priority_items or []))

        if queued_override:
            source = queued_override.get("source")
            key = (queued_override.get("key") or "").strip()
            if source == "whitelist" and key:
                if any_priority_ready and min_interval_ok and window_ok:
                    logger.info("Queue override SENT: %s", queued_override)
                    # Wait so we don't fire before the game's GCD is actually ready (visual ready can be 1 frame early).
                    delay_sec = (
                        getattr(self._config, "queue_fire_delay_ms", 100) or 0
                    ) / 1000.0
                    if delay_sec > 0:
                        time.sleep(delay_sec)
                    try:
                        import keyboard

                        # Use same API as priority keys so the game receives the queued key the same way.
                        self._send_keybind(keyboard, key)
                    except Exception as e:
                        logger.warning("keyboard send(queued %r) failed: %s", key, e)
                        return None
                    self._record_send(now)
                    # Suppress priority for one configured GCD so only the queued key reaches the game.
                    gcd_sec = (getattr(self._config, "gcd_ms", 1500) or 1500) / 1000.0
                    self._suppress_priority_until = now + max(0.0, gcd_sec)
                    if on_queued_sent:
                        on_queued_sent()
                    logger.info("Sent queued key: %s", key)
                    return {
                        "keybind": key,
                        "action": "sent",
                        "timestamp": now,
                        "queued": True,
                    }
                logger.info(
                    "Queue override BLOCKED: window=%s, interval_ok=%s, any_priority_ready=%s",
                    window_ok,
                    min_interval_ok,
                    any_priority_ready,
                )
                return None
            if source == "tracked":
                if not slot_detection_enabled:
                    return None
                slot_index = queued_override.get("slot_index")
                if slot_index is not None and key:
                    slot = slots_by_index.get(slot_index)
                    if (
                        slot
                        and slot.state == SlotState.READY
                        and any_priority_ready
                        and min_interval_ok
                        and window_ok
                    ):
                        delay_sec = (
                            getattr(self._config, "queue_fire_delay_ms", 100) or 0
                        ) / 1000.0
                        if delay_sec > 0:
                            time.sleep(delay_sec)
                        try:
                            import keyboard

                            self._send_keybind(keyboard, key)
                        except Exception as e:
                            logger.warning(
                                "keyboard send(queued %r) failed: %s", key, e
                            )
                            return None
                        self._record_send(now)
                        gcd_sec = (getattr(self._config, "gcd_ms", 1500) or 1500) / 1000.0
                        self._suppress_priority_until = now + max(0.0, gcd_sec)
                        if on_queued_sent:
                            on_queued_sent()
                        logger.info("Sent queued key: %s (slot %s)", key, slot_index)
                        return {
                            "keybind": key,
                            "action": "sent",
                            "timestamp": now,
                            "slot_index": slot_index,
                            "queued": True,
                        }
                return None
            # Had a queued override but didn't send (wrong source/key); never send priority instead.
            return None

        if not min_interval_ok:
            return None
        gcd_suppress_enabled = bool(getattr(self._config, "gcd_suppress_enabled", True))
        single_fire_bypass = bool(getattr(self._config, "gcd_suppress_single_fire_bypass", False))
        if gcd_suppress_enabled:
            if not (single_fire_pending and single_fire_bypass):
                if now < self._suppress_priority_until:
                    return None

        manual_by_id = {
            str(a.get("id", "") or "").strip().lower(): a
            for a in (manual_actions or [])
        }
        for item in priority_items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "") or "").strip().lower()
            slot_index: Optional[int] = None
            display_name = "Unidentified"
            keybind: Optional[str] = None

            if item_type == "slot":
                if not slot_detection_enabled:
                    continue
                slot_index = item.get("slot_index")
                if not isinstance(slot_index, int):
                    continue
                slot = slots_by_index.get(slot_index)
                if not slot_item_is_eligible_for_snapshot(
                    item, slot, buff_states=buff_states, active_form_id=active_form_id,
                    movement_active=movement_active,
                    previous_action=previous_action,
                ):
                    continue
                keybind = keybinds[slot_index] if slot_index < len(keybinds) else None
            elif item_type == "manual":
                if not manual_item_is_eligible(
                    item,
                    buff_states=buff_states,
                    active_form_id=active_form_id,
                    movement_active=movement_active,
                    previous_action=previous_action,
                ):
                    continue
                action_id = str(item.get("action_id", "") or "").strip().lower()
                if not action_id:
                    continue
                action = manual_by_id.get(action_id)
                if not isinstance(action, dict):
                    continue
                keybind = str(action.get("keybind", "") or "").strip()
                display_name = (
                    str(action.get("name", "") or "").strip() or "Manual Action"
                )
            else:
                continue

            if not keybind:
                continue
            keybind = normalize_bind(str(keybind))
            if not keybind:
                continue

            if not self.is_target_window_active():
                return {
                    "keybind": keybind,
                    "display_name": display_name,
                    "item_type": item_type,
                    "action": "blocked",
                    "reason": "window",
                    "slot_index": slot_index,
                }

            try:
                import keyboard

                self._send_keybind(keyboard, keybind)
            except Exception as e:
                logger.warning("keyboard.send(%r) failed: %s", keybind, e)
                return None

            self._record_send(now)
            self._last_sent_item = self._item_previous_action_identity(item, keybind, now)
            if single_fire_pending:
                self._single_fire_pending = False
            logger.info("Sent key: %s", keybind)
            return {
                "keybind": keybind,
                "display_name": display_name,
                "item_type": item_type,
                "action_id": str(item.get("action_id", "") or "").strip().lower(),
                "action": "sent",
                "timestamp": now,
                "slot_index": slot_index,
            }

        return None
