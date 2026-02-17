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
                if _is_keyboard_bind(normalize_bind(b))
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

                # Track key-down state so we trigger only on press, not on key repeat.
                key_held: dict[str, bool] = {}

                def on_event(event):
                    if not self._running:
                        return
                    name = getattr(event, "name", None)
                    if not name:
                        return
                    key_normalized = str(name).strip().lower()
                    if key_normalized not in binds:
                        return
                    if event.event_type == keyboard.KEY_DOWN:
                        if not key_held.get(key_normalized, False):
                            key_held[key_normalized] = True
                            self.triggered.emit(key_normalized)
                    elif event.event_type == keyboard.KEY_UP:
                        key_held[key_normalized] = False

                self._hook = keyboard.hook(on_event)
            except Exception as e:
                logger.debug("keyboard hook failed for %r: %s", sorted(binds), e)

            while self._running:
                current_binds = {
                    normalize_bind(b)
                    for b in (self._get_binds() or [])
                    if _is_keyboard_bind(normalize_bind(b))
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
