"""Global modifier tracker for overlay edit mode.

Tracks whether Shift is currently held using keyboard.hook.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from PyQt6.QtCore import QObject, QThread

logger = logging.getLogger(__name__)

_SHIFT_KEYS = frozenset({"shift", "left shift", "right shift", "shift left", "shift right"})


class _ModifierThread(QThread):
    """Background thread that tracks Shift key-down/up globally."""

    def __init__(self, lock: threading.Lock, shift_down: list[bool], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._lock = lock
        self._shift_down = shift_down
        self._running = True
        self._hook = None

    def run(self) -> None:
        try:
            import keyboard
        except ImportError:
            logger.warning(
                "keyboard library not installed; Shift edit-mode tracking disabled. "
                "Install with: pip install keyboard"
            )
            return

        def on_event(event):
            if not self._running:
                return
            name = str(getattr(event, "name", "") or "").strip().lower()
            if name not in _SHIFT_KEYS:
                return
            with self._lock:
                if event.event_type == keyboard.KEY_DOWN:
                    self._shift_down[0] = True
                elif event.event_type == keyboard.KEY_UP:
                    self._shift_down[0] = False

        try:
            self._hook = keyboard.hook(on_event)
        except Exception as e:
            logger.debug("keyboard hook failed for shift modifier tracker: %s", e)
            return

        while self._running:
            self.msleep(100)

        if self._hook is not None:
            try:
                keyboard.unhook(self._hook)
            except Exception:
                pass
            self._hook = None

        with self._lock:
            self._shift_down[0] = False

    def stop(self) -> None:
        self._running = False


class ShiftModifierTracker(QObject):
    """Threaded global Shift-held tracker."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._shift_down = [False]
        self._thread: Optional[_ModifierThread] = None

    @property
    def is_pressed(self) -> bool:
        with self._lock:
            return bool(self._shift_down[0])

    def start(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self._thread = _ModifierThread(self._lock, self._shift_down, self)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._thread.stop()
            self._thread.wait(2000)
            self._thread = None

