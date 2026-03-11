"""Shared config file loading, repair, and atomic persistence helpers."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.models import AppConfig

logger = logging.getLogger(__name__)

_DUPLICATE_DATA_QUOTE_RE = re.compile(r'("data"\s*:\s*)""([A-Za-z0-9+/=]*)"')


@dataclass(frozen=True)
class ConfigLoadResult:
    config: AppConfig
    recovered: bool = False
    used_defaults: bool = False
    repaired_text: str | None = None


def _repair_duplicate_baseline_quotes(raw_text: str) -> str:
    return _DUPLICATE_DATA_QUOTE_RE.sub(r'\1"\2"', raw_text)


def _decode_json(raw_text: str) -> dict[str, Any]:
    data = json.loads(raw_text)
    if not isinstance(data, dict):
        raise ValueError("Config root must be a JSON object")
    return data


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=2)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{path.stem}.",
        suffix=f"{path.suffix}.tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_app_config(path: Path, config: AppConfig) -> None:
    write_json_atomic(path, config.to_dict())


def _backup_broken_config(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.broken-{stamp}{path.suffix}")
    try:
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        return backup
    except Exception as exc:
        logger.error("Failed to back up broken config %s: %s", path, exc)
        return None


def _fallback_to_default_config(path: Path, reason: str) -> ConfigLoadResult:
    backup = _backup_broken_config(path)
    config = AppConfig()
    try:
        write_app_config(path, config)
        if backup is not None:
            logger.warning(
                "Replaced unrecoverable config at %s with defaults; backup saved to %s (%s)",
                path,
                backup,
                reason,
            )
        else:
            logger.warning(
                "Replaced unrecoverable config at %s with defaults (%s)",
                path,
                reason,
            )
    except Exception as exc:
        logger.error("Failed to rewrite default config at %s: %s", path, exc)
    return ConfigLoadResult(config=config, used_defaults=True)


def load_app_config(path: Path, *, fallback_to_defaults: bool = True) -> ConfigLoadResult:
    if not path.exists():
        if fallback_to_defaults:
            logger.warning("Config not found at %s, using defaults", path)
            return ConfigLoadResult(config=AppConfig(), used_defaults=True)
        raise FileNotFoundError(path)

    raw_text = path.read_text(encoding="utf-8")
    try:
        data = _decode_json(raw_text)
        logger.info("Loaded config from %s", path)
        return ConfigLoadResult(config=AppConfig.from_dict(data))
    except json.JSONDecodeError as exc:
        repaired_text = _repair_duplicate_baseline_quotes(raw_text)
        if repaired_text == raw_text:
            logger.error("Config at %s is invalid JSON and could not be repaired: %s", path, exc)
            if fallback_to_defaults:
                return _fallback_to_default_config(path, str(exc))
            raise
        try:
            repaired_data = _decode_json(repaired_text)
            config = AppConfig.from_dict(repaired_data)
            write_json_atomic(path, config.to_dict())
            logger.warning("Recovered malformed config at %s and rewrote a valid copy", path)
            return ConfigLoadResult(
                config=config,
                recovered=True,
                repaired_text=repaired_text,
            )
        except Exception as repair_exc:
            logger.error(
                "Config at %s is invalid JSON and repair failed: %s",
                path,
                repair_exc,
            )
            if fallback_to_defaults:
                return _fallback_to_default_config(path, str(repair_exc))
            raise
    except Exception as exc:
        logger.error("Could not load config at %s: %s", path, exc)
        if fallback_to_defaults:
            return _fallback_to_default_config(path, str(exc))
        raise
