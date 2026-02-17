"""Global hotkey listener for automation toggle (works when app does not have focus).

Uses the 'keyboard' library with a low-level hook (keyboard.hook) instead of add_hotkey,
so the toggle key is detected even when other keys (e.g. W) or mouse buttons (e.g. right-click)
are held. add_hotkey can miss the key in those cases; the raw hook sees every key down/up.
Only keyboard keys are supported for the toggle bind (e.g. F24 from a mouse key).
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal, QThread

logger = logging.getLogger(__name__)

# Mouse button names we do not register as keyboard hotkeys
_MOUSE_BIND_NAMES = frozenset({"x1", "x2", "left", "right", "middle"})


def format_bind_for_display(bind: str) -> str:
    """Convert stored bind string to display label (e.g. 'f5' -> 'F5', 'x1' -> 'Mouse 4')."""
    if not bind or not bind.strip():
        return "Set"
    b = bind.strip().lower()
    if b == "x1":
        return "Mouse 4"
    if b == "x2":
        return "Mouse 5"
    if b in ("left", "right", "middle"):
        return {"left": "LMB", "right": "RMB", "middle": "MMB"}.get(b, b)
    if len(b) <= 2 and b.startswith("f"):
        return b.upper()
    return b.upper() if len(b) <= 2 else b.capitalize()


def normalize_bind(bind: str) -> str:
    """Normalize bind string for comparison (lowercase, stripped)."""
    return bind.strip().lower() if bind else ""


def _is_keyboard_bind(bind: str) -> bool:
    """True if the bind is a keyboard key (we only listen for keyboard keys)."""
    return bool(bind) and normalize_bind(bind) not in _MOUSE_BIND_NAMES


class _ListenerThread(QThread):
    """Uses a low-level keyboard.hook to listen for the toggle key. Fires once per key press
    (ignores repeat) and works when other keys or mouse buttons are held."""

    triggered = pyqtSignal()

    def __init__(self, get_bind: Callable[[], str], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._get_bind = get_bind
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
            bind = normalize_bind(self._get_bind())
            if not _is_keyboard_bind(bind):
                self.msleep(500)
                continue
            try:
                if self._hook is not None:
                    try:
                        keyboard.unhook(self._hook)
                    except Exception:
                        pass
                    self._hook = None

                # Track key-down state so we trigger only on press, not on key repeat
                key_held = [False]

                def on_event(event):
                    if not self._running:
                        return
                    name = getattr(event, "name", None)
                    if not name:
                        return
                    key_normalized = str(name).strip().lower()
                    if key_normalized != bind:
                        return
                    if event.event_type == keyboard.KEY_DOWN:
                        if not key_held[0]:
                            key_held[0] = True
                            self.triggered.emit()
                    elif event.event_type == keyboard.KEY_UP:
                        key_held[0] = False

                self._hook = keyboard.hook(on_event)
            except Exception as e:
                logger.debug("keyboard hook failed for %r: %s", bind, e)

            while self._running and normalize_bind(self._get_bind()) == bind and _is_keyboard_bind(bind):
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
    """Captures the next keyboard key press and emits it as a bind string (keyboard only)."""

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

        result = [None]

        def on_event(event):
            if self._done or result[0] is not None:
                return
            if event.event_type == keyboard.KEY_DOWN:
                name = getattr(event, "name", None) or str(getattr(event, "scan_code", ""))
                if name:
                    result[0] = str(name).lower()
                    if self._hook is not None:
                        try:
                            keyboard.unhook(self._hook)
                        except Exception:
                            pass
                        self._hook = None

        self._hook = keyboard.hook(on_event)
        while not self._done and result[0] is None:
            self.msleep(50)
        if self._hook is not None:
            try:
                keyboard.unhook(self._hook)
            except Exception:
                pass
            self._hook = None
        if result[0] is not None:
            self.captured.emit(result[0])

    def cancel(self) -> None:
        self._done = True


class GlobalToggleListener(QObject):
    """Starts a background thread that emits when the automation toggle key is pressed."""

    triggered = pyqtSignal()

    def __init__(self, get_bind: Callable[[], str], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._get_bind = get_bind
        self._thread: Optional[_ListenerThread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self._thread = _ListenerThread(self._get_bind, self)
        self._thread.triggered.connect(self.triggered.emit)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._thread.stop()
            self._thread.wait(2000)
            self._thread = None
