"""Key sender — sends keypresses based on slot states and priority order."""
from __future__ import annotations

import logging
import sys
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.models import AppConfig

from src.models import ActionBarState, SlotState
from src.automation.binds import normalize_bind

logger = logging.getLogger(__name__)


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
        self._single_fire_pending = False

    def update_config(self, config: "AppConfig") -> None:
        self._config = config

    def request_single_fire(self) -> None:
        """Arm one key send for the next valid ready action."""
        self._single_fire_pending = True

    def is_target_window_active(self) -> bool:
        """True if foreground window matches target_window_title, or target is empty."""
        return is_target_window_active(getattr(self._config, "target_window_title", "") or "")

    def _find_blocking_cast(self, state: ActionBarState) -> Optional[tuple[int, object]]:
        """Return first slot currently casting/channeling, if any."""
        for slot in state.slots:
            if slot.state in (SlotState.CASTING, SlotState.CHANNELING):
                return slot.index, slot
        return None

    def evaluate_and_send(
        self,
        state: ActionBarState,
        priority_items: list[dict],
        keybinds: list[str],
        manual_actions: list[dict],
        automation_enabled: bool,
    ) -> Optional[dict]:
        """
        If automation enabled, find first eligible item in priority_items and send its keybind (subject to
        min delay and target window). Returns None if nothing sent/blocked; otherwise a dict for the UI.
        """
        single_fire_pending = self._single_fire_pending
        if not automation_enabled and not single_fire_pending:
            return None

        min_interval_sec = (getattr(self._config, "min_press_interval_ms", 150) or 150) / 1000.0
        now = time.time()
        if now - self._last_send_time < min_interval_sec:
            return None

        allow_while_casting = bool(getattr(self._config, "allow_cast_while_casting", False))
        if not allow_while_casting:
            blocking = self._find_blocking_cast(state)
            if blocking is not None:
                blocking_index, blocking_slot = blocking
                queue_window_sec = (getattr(self._config, "queue_window_ms", 120) or 120) / 1000.0
                cast_ends_at = getattr(blocking_slot, "cast_ends_at", None)
                if cast_ends_at is None or now < (cast_ends_at + queue_window_sec):
                    return {
                        "action": "blocked",
                        "reason": "casting",
                        "slot_index": blocking_index,
                        "cast_ends_at": cast_ends_at,
                    }

        slots_by_index = {s.index: s for s in state.slots}
        manual_by_id = {
            str(a.get("id", "") or "").strip().lower(): a for a in (manual_actions or [])
        }
        for item in priority_items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "") or "").strip().lower()
            slot_index: Optional[int] = None
            display_name = "Unidentified"
            keybind: Optional[str] = None

            if item_type == "slot":
                slot_index = item.get("slot_index")
                if not isinstance(slot_index, int):
                    continue
                slot = slots_by_index.get(slot_index)
                if not slot or slot.state != SlotState.READY:
                    continue
                keybind = keybinds[slot_index] if slot_index < len(keybinds) else None
            elif item_type == "manual":
                action_id = str(item.get("action_id", "") or "").strip().lower()
                if not action_id:
                    continue
                action = manual_by_id.get(action_id)
                if not isinstance(action, dict):
                    continue
                keybind = normalize_bind(str(action.get("keybind", "") or ""))
                display_name = str(action.get("name", "") or "").strip() or "Manual Action"
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
                keyboard.send(keybind)
            except Exception as e:
                logger.warning("keyboard.send(%r) failed: %s", keybind, e)
                return None

            self._last_send_time = now
            if single_fire_pending:
                self._single_fire_pending = False
            logger.info("Sent key: %s", keybind)
            return {
                "keybind": keybind,
                "display_name": display_name,
                "item_type": item_type,
                "action": "sent",
                "timestamp": now,
                "slot_index": slot_index,
            }

        return None
