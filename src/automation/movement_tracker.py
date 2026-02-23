"""Movement tracker — detects whether WASD keys are currently held.

Uses keyboard.hook in a background QThread so it works even when the app
does not have focus.  The is_moving property can be safely read from the
main thread on CPython (GIL protects the bool read); a threading.Lock is
used around the _held_keys set for full safety.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from PyQt6.QtCore import QObject, QThread

logger = logging.getLogger(__name__)

_MOVEMENT_KEYS = frozenset({"w", "a", "s", "d"})


class _MovementThread(QThread):
    """Background thread that hooks keyboard and tracks WASD held state."""

    def __init__(self, lock: threading.Lock, held: set, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._lock = lock
        self._held = held
        self._running = True
        self._hook = None

    def run(self) -> None:
        try:
            import keyboard
        except ImportError:
            logger.warning(
                "keyboard library not installed; movement tracking disabled. "
                "Install with: pip install keyboard"
            )
            return

        def on_event(event):
            if not self._running:
                return
            name = getattr(event, "name", None)
            if not name:
                return
            key = str(name).strip().lower()
            if key not in _MOVEMENT_KEYS:
                return
            with self._lock:
                if event.event_type == keyboard.KEY_DOWN:
                    self._held.add(key)
                elif event.event_type == keyboard.KEY_UP:
                    self._held.discard(key)

        try:
            self._hook = keyboard.hook(on_event)
        except Exception as e:
            logger.debug("keyboard hook failed for movement tracker: %s", e)
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
            self._held.clear()

    def stop(self) -> None:
        self._running = False


class MovementTracker(QObject):
    """Tracks whether any WASD key is currently held by the user.

    Usage::

        tracker = MovementTracker()
        tracker.start()
        ...
        if tracker.is_moving:
            ...
        tracker.stop()
    """

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._held: set[str] = set()
        self._thread: Optional[_MovementThread] = None

    @property
    def is_moving(self) -> bool:
        """True if at least one WASD key is currently held."""
        with self._lock:
            return len(self._held) > 0

    def start(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self._thread = _MovementThread(self._lock, self._held, self)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._thread.stop()
            self._thread.wait(2000)
            self._thread = None
