from __future__ import annotations

import os
import sys
from pathlib import Path


DEFAULT_SETTINGS_PATH = Path(".gemini/settings.json")


def resolve_runtime_path(env_name: str, default: Path) -> Path:
    value = os.environ.get(env_name, "").strip()
    if not value:
        return default.resolve()
    return Path(value).expanduser().resolve()


def default_runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        if executable.parent.name.lower() == "backend":
            return executable.parent.parent
        return executable.parent
    return Path(__file__).resolve().parent


def default_user_data_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data).expanduser() / "VRCForge" / "agentic-app"
    return default_runtime_root()


ROOT_DIR = resolve_runtime_path("VRCFORGE_APP_DIR", default_runtime_root())
PORTABLE_MODE = bool(getattr(sys, "frozen", False)) or any(
    os.environ.get(name, "").strip()
    for name in (
        "VRCFORGE_APP_DIR",
        "VRCFORGE_USER_DATA_DIR",
        "VRCFORGE_CONFIG_DIR",
        "VRCFORGE_CONFIG_PATH",
        "VRCFORGE_LOG_DIR",
        "VRCFORGE_ARTIFACTS_DIR",
        "VRCFORGE_DASHBOARD_DIR",
        "VRCFORGE_SETTINGS_PATH",
    )
)
USER_DATA_DIR = resolve_runtime_path("VRCFORGE_USER_DATA_DIR", default_user_data_root())
CONFIG_DIR = resolve_runtime_path("VRCFORGE_CONFIG_DIR", USER_DATA_DIR / "config")
RUNTIME_SETTINGS_PATH = resolve_runtime_path(
    "VRCFORGE_SETTINGS_PATH",
    CONFIG_DIR / "settings.json" if PORTABLE_MODE else ROOT_DIR / DEFAULT_SETTINGS_PATH,
)
