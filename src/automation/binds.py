"""Shared helpers for parsing, normalizing, and displaying keybind strings."""
from __future__ import annotations

from typing import Optional

_MOD_ORDER = ("ctrl", "shift", "alt")

_MOD_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "left ctrl": "ctrl",
    "right ctrl": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "left control": "ctrl",
    "right control": "ctrl",
    "shift": "shift",
    "left shift": "shift",
    "right shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    "alt": "alt",
    "left alt": "alt",
    "right alt": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "alt gr": "alt",
    "altgr": "alt",
}

_KEY_ALIASES = {
    "esc": "escape",
    "return": "enter",
    "pgup": "page up",
    "pageup": "page up",
    "pgdn": "page down",
    "pagedown": "page down",
    "ins": "insert",
    "del": "delete",
    "spacebar": "space",
}


def normalize_key_token(token: str) -> str:
    """Normalize one key token (modifier or primary key) to canonical lowercase."""
    if not token:
        return ""
    t = str(token).strip().lower().replace("_", " ")
    t = " ".join(t.split())
    if not t:
        return ""
    if t in _MOD_ALIASES:
        return _MOD_ALIASES[t]
    return _KEY_ALIASES.get(t, t)


def is_modifier_token(token: str) -> bool:
    return normalize_key_token(token) in _MOD_ORDER


def normalize_bind_from_parts(modifiers: set[str], primary_key: str) -> str:
    """Build canonical bind from explicit modifiers + primary key."""
    key = normalize_key_token(primary_key)
    if not key or key in _MOD_ORDER:
        return ""
    mods = {normalize_key_token(m) for m in modifiers}
    mods = {m for m in mods if m in _MOD_ORDER}
    ordered = [m for m in _MOD_ORDER if m in mods]
    if ordered:
        return "+".join(ordered + [key])
    return key


def normalize_bind(bind: str) -> str:
    """Normalize a bind string (e.g. 'Control + 1' -> 'ctrl+1')."""
    if not bind:
        return ""
    parts = [normalize_key_token(p) for p in str(bind).split("+")]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    mods: set[str] = set()
    primary = ""
    for part in parts:
        if part in _MOD_ORDER:
            mods.add(part)
            continue
        if primary:
            return ""
        primary = part
    return normalize_bind_from_parts(mods, primary)


def parse_bind(bind: str) -> Optional[tuple[frozenset[str], str]]:
    """Parse bind into (modifiers, primary_key), or None if invalid/empty."""
    normalized = normalize_bind(bind)
    if not normalized:
        return None
    parts = normalized.split("+")
    primary = parts[-1]
    modifiers = frozenset(parts[:-1])
    return modifiers, primary


def format_bind_for_display(bind: str) -> str:
    """Convert stored bind string into UI display text."""
    normalized = normalize_bind(bind)
    if not normalized:
        return "Set"
    tokens: list[str] = []
    for part in normalized.split("+"):
        if part == "ctrl":
            tokens.append("Ctrl")
        elif part == "shift":
            tokens.append("Shift")
        elif part == "alt":
            tokens.append("Alt")
        elif part == "x1":
            tokens.append("Mouse 4")
        elif part == "x2":
            tokens.append("Mouse 5")
        elif part in ("left", "right", "middle"):
            tokens.append({"left": "LMB", "right": "RMB", "middle": "MMB"}[part])
        elif part.startswith("f") and part[1:].isdigit():
            tokens.append(part.upper())
        elif len(part) <= 2:
            tokens.append(part.upper())
        else:
            tokens.append(part.capitalize())
    return "+".join(tokens)
