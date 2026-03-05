"""Cooldown Reader — Main entry point.

Wires together: screen capture → slot analysis → UI + overlay.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from src.app.coordinator import AppCoordinator
from src.models import AppConfig


logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parent
# When frozen (e.g. PyInstaller), bundle root is sys._MEIPASS; include cocktus.ico via --add-data
_BASE_PATH = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"
ICON_PATH = _BASE_PATH / "cocktus.ico"


def load_config() -> AppConfig:
    """Load config from JSON, falling back to defaults."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        logger.info(f"Loaded config from {CONFIG_PATH}")
        return AppConfig.from_dict(data)
    logger.warning(f"Config not found at {CONFIG_PATH}, using defaults")
    return AppConfig()


def main() -> None:
    config = load_config()
    config.automation_enabled = False

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    coordinator = AppCoordinator(config, app)
    coordinator.run()


if __name__ == "__main__":
    main()
