"""Spell queue listener: manual keypresses (whitelist or bound-but-not-priority) queue to fire at next GCD."""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal, QThread

if TYPE_CHECKING:
    from src.models import AppConfig

logger = logging.getLogger(__name__)

# Left mouse: do not trigger queue
_LEFT_MOUSE_NAMES = frozenset({"left", "left click", "mouse left"})


def _normalize_key(name: str) -> str:
    return str(name or "").strip().lower()


class _QueueHookThread(QThread):
    """Runs keyboard.hook; on key-down, if key qualifies, calls set_queue_value(value). One queued action; new press replaces."""

    def __init__(
        self,
        get_config: Callable[[], "AppConfig"],
        get_queue: Callable[[], Optional[dict]],
        set_queue_value: Callable[[dict], None],
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._get_config = get_config
        self._get_queue = get_queue
        self._set_queue_value = set_queue_value
        self._running = True
        self._hook = None

    def run(self) -> None:
        try:
            import keyboard
        except ImportError:
            logger.warning(
                "keyboard library not installed; spell queue listener disabled. pip install keyboard"
            )
            return

        def on_event(event):
            if not self._running:
                return
            logger.debug("Queue hook: key=%s, type=%s", getattr(event, "name", None), getattr(event, "event_type", None))
            if getattr(event, "event_type", None) != keyboard.KEY_DOWN:
                return
            name = getattr(event, "name", None)
            key = _normalize_key(name or "")
            if not key or key in _LEFT_MOUSE_NAMES:
                return
            try:
                config = self._get_config()
            except Exception:
                return
            if not getattr(config, "automation_enabled", False):
                return
            whitelist = getattr(config, "queue_whitelist", []) or []
            keybinds = getattr(config, "keybinds", []) or []
            priority_order = getattr(config, "priority_order", []) or []
            priority_keys = set()
            for idx in priority_order:
                if idx < len(keybinds) and (keybinds[idx] or "").strip():
                    priority_keys.add(_normalize_key(keybinds[idx]))
            if key in priority_keys:
                return
            if key in whitelist:
                existing = self._get_queue()
                if existing and existing.get("key") == key and existing.get("source") == "whitelist":
                    return
                self._set_queue_value({"key": key, "source": "whitelist"})
                return
            for slot_index, bind in enumerate(keybinds):
                if slot_index in priority_order:
                    continue
                if not (bind or "").strip():
                    continue
                if _normalize_key(bind) == key:
                    existing = self._get_queue()
                    if (
                        existing
                        and existing.get("source") == "tracked"
                        and existing.get("slot_index") == slot_index
                    ):
                        return
                    self._set_queue_value({"key": key, "slot_index": slot_index, "source": "tracked"})
                    return

        try:
            self._hook = keyboard.hook(on_event)
        except Exception as e:
            logger.debug("queue listener hook failed: %s", e)
            return
        while self._running:
            self.msleep(200)
        if self._hook is not None:
            try:
                keyboard.unhook(self._hook)
            except Exception:
                pass
            self._hook = None

    def stop(self) -> None:
        self._running = False


class QueueListener(QObject):
    """Listens for keypresses and sets a single queued override (whitelist or tracked slot not in priority).
    Emits queue_updated when queue is set or cleared. get_queue() returns current queue or None; if older
    than queue_timeout_ms, returns None and clears. clear_queue() clears and emits. Does not trigger on
    priority keys or left mouse. When automation is OFF, keypresses are ignored."""

    queue_updated = pyqtSignal(object)  # dict or None

    def __init__(self, get_config: Callable[[], "AppConfig"], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._get_config = get_config
        self._lock = threading.Lock()
        self._queue: Optional[dict] = None
        self._queue_time: float = 0.0
        self._thread: Optional[_QueueHookThread] = None

    def _get_queue_internal(self) -> Optional[dict]:
        with self._lock:
            return self._queue

    def get_queue(self) -> Optional[dict]:
        """Return current queue or None. If queue is older than queue_timeout_ms, clear and return None."""
        try:
            config = self._get_config()
            timeout_ms = getattr(config, "queue_timeout_ms", 5000) or 5000
            timeout_sec = timeout_ms / 1000.0
        except Exception:
            timeout_sec = 5.0
            timeout_ms = 5000
        with self._lock:
            if self._queue is None:
                return None
            age_ms = (time.time() - self._queue_time) * 1000
            logger.debug("Queue age: %sms, timeout: %sms", age_ms, timeout_ms)
            if (time.time() - self._queue_time) >= timeout_sec:
                self._queue = None
                self._queue_time = 0.0
                need_emit = True
            else:
                need_emit = False
                return self._queue.copy()
        if need_emit:
            self.queue_updated.emit(None)
        return None

    def clear_queue(self) -> None:
        """Clear the queue and emit queue_updated(None)."""
        with self._lock:
            had = self._queue is not None
            self._queue = None
            self._queue_time = 0.0
        if had:
            self.queue_updated.emit(None)

    def start(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return

        def set_value(value: dict) -> None:
            with self._lock:
                self._queue = value.copy()
                self._queue_time = time.time()
            self.queue_updated.emit(value)

        self._thread = _QueueHookThread(
            self._get_config,
            self._get_queue_internal,
            set_value,
            self,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._thread.stop()
            self._thread.wait(2000)
            self._thread = None
        self.clear_queue()
