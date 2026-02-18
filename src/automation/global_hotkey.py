"""Global hotkey listener for automation profile binds (works when app does not have focus).

Uses the 'keyboard' library with a low-level hook (keyboard.hook) instead of add_hotkey,
so bound keys are detected even when other keys (e.g. W) or mouse buttons (e.g. right-click)
are held. add_hotkey can miss the key in those cases; the raw hook sees every key down/up.
Only keyboard keys are supported (e.g. F24 from a mouse key mapped in software).
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal, QThread

from src.automation.binds import (
    format_bind_for_display,
    is_modifier_token,
    normalize_bind,
    normalize_bind_from_parts,
    normalize_key_token,
    parse_bind,
)

logger = logging.getLogger(__name__)

# Mouse button names we do not register as keyboard hotkeys
_MOUSE_BIND_NAMES = frozenset({"x1", "x2", "left", "right", "middle"})


def _is_keyboard_bind(bind: str) -> bool:
    """True if the bind is a keyboard key (we only listen for keyboard keys)."""
    parsed = parse_bind(bind)
    if parsed is None:
        return False
    _, primary = parsed
    return primary not in _MOUSE_BIND_NAMES


class _ListenerThread(QThread):
    """Uses a low-level keyboard.hook to listen for one or more binds."""

    triggered = pyqtSignal(str)

    def __init__(self, get_binds: Callable[[], list[str]], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._get_binds = get_binds
        self._running = True
        self._hook = None

    def run(self) -> None:
        try:
            import keyboard
        except ImportError:
            logger.warning(
                "keyboard library not installed; global automation toggle hotkey disabled. "
                "Install with: pip install keyboard"
            )
            return

        while self._running:
            binds = {
                normalize_bind(b)
                for b in (self._get_binds() or [])
                if _is_keyboard_bind(b)
            }
            if not binds:
                self.msleep(500)
                continue
            try:
                if self._hook is not None:
                    try:
                        keyboard.unhook(self._hook)
                    except Exception:
                        pass
                    self._hook = None

                parsed_binds: dict[str, tuple[frozenset[str], str]] = {}
                key_to_bind: dict[str, set[str]] = {}
                for bind in binds:
                    parsed = parse_bind(bind)
                    if parsed is None:
                        continue
                    parsed_binds[bind] = parsed
                    key = parsed[1]
                    key_to_bind.setdefault(key, set()).add(bind)

                # Track key-down state so we trigger only once per press.
                held_keys: set[str] = set()
                held_modifiers: set[str] = set()
                active_triggers: set[str] = set()

                def on_event(event):
                    if not self._running:
                        return
                    name = getattr(event, "name", None)
                    if not name:
                        return
                    key_normalized = normalize_key_token(str(name))
                    if not key_normalized:
                        return
                    is_modifier = is_modifier_token(key_normalized)
                    if event.event_type == keyboard.KEY_DOWN:
                        if is_modifier:
                            held_modifiers.add(key_normalized)
                            return
                        if key_normalized in held_keys:
                            return
                        held_keys.add(key_normalized)
                        candidate = normalize_bind_from_parts(held_modifiers, key_normalized)
                        if candidate in parsed_binds and candidate not in active_triggers:
                            active_triggers.add(candidate)
                            self.triggered.emit(candidate)
                    elif event.event_type == keyboard.KEY_UP:
                        if is_modifier:
                            held_modifiers.discard(key_normalized)
                            return
                        held_keys.discard(key_normalized)
                        for bind in key_to_bind.get(key_normalized, set()):
                            active_triggers.discard(bind)

                self._hook = keyboard.hook(on_event)
            except Exception as e:
                logger.debug("keyboard hook failed for %r: %s", sorted(binds), e)

            while self._running:
                current_binds = {
                    normalize_bind(b)
                    for b in (self._get_binds() or [])
                    if _is_keyboard_bind(b)
                }
                if current_binds != binds:
                    break
                self.msleep(200)

        if self._hook is not None:
            try:
                keyboard.unhook(self._hook)
            except Exception:
                pass
            self._hook = None

    def stop(self) -> None:
        self._running = False


class CaptureOneKeyThread(QThread):
    """Captures the next keyboard combo and emits it as a bind string (keyboard only)."""

    captured = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._done = False
        self._hook = None

    def run(self) -> None:
        try:
            import keyboard
        except ImportError:
            self.cancelled.emit()
            return

        result = [""]
        held_modifiers: set[str] = set()
        pending_primary = [""]

        def on_event(event):
            if self._done or result[0]:
                return
            name = getattr(event, "name", None) or str(getattr(event, "scan_code", ""))
            token = normalize_key_token(str(name))
            if not token:
                return
            is_mod = is_modifier_token(token)
            if event.event_type == keyboard.KEY_DOWN:
                if is_mod:
                    held_modifiers.add(token)
                    return
                if pending_primary[0]:
                    return
                pending_primary[0] = token
                result[0] = normalize_bind_from_parts(held_modifiers, token)
                if self._hook is not None:
                    try:
                        keyboard.unhook(self._hook)
                    except Exception:
                        pass
                    self._hook = None
            elif event.event_type == keyboard.KEY_UP and is_mod:
                held_modifiers.discard(token)

        self._hook = keyboard.hook(on_event)
        while not self._done and not result[0]:
            self.msleep(50)
        if self._hook is not None:
            try:
                keyboard.unhook(self._hook)
            except Exception:
                pass
            self._hook = None
        if result[0]:
            self.captured.emit(result[0])

    def cancel(self) -> None:
        self._done = True


class GlobalToggleListener(QObject):
    """Starts a background thread that emits when any configured global bind is pressed."""

    triggered = pyqtSignal(str)

    def __init__(self, get_binds: Callable[[], list[str]], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._get_binds = get_binds
        self._thread: Optional[_ListenerThread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self._thread = _ListenerThread(self._get_binds, self)
        self._thread.triggered.connect(self.triggered.emit)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._thread.stop()
            self._thread.wait(2000)
            self._thread = None
