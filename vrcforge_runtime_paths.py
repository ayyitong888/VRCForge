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


def _read_registry_data_dir() -> str | None:
    """Read the configured data dir from HKCU\\Software\\VRCForge\\DataDir.

    Returns None on any read failure (missing key/value, wrong type, blank).
    """
    try:
        import winreg
    except ImportError:  # pragma: no cover - VRCForge targets Windows
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\VRCForge") as key:
            value, _ = winreg.QueryValueEx(key, "DataDir")
    except OSError:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def write_registry_data_dir(value: str) -> None:
    """Persist the configured data dir to HKCU\\Software\\VRCForge\\DataDir.

    Any failure (winreg unavailable, registry policy, I/O error) is silently
    swallowed: callers never crash on registry problems and tests can inject a
    substitute through monkeypatching.
    """
    try:
        import winreg
    except ImportError:  # pragma: no cover - VRCForge targets Windows
        return
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\VRCForge") as key:
            winreg.SetValueEx(key, "DataDir", 0, winreg.REG_SZ, value)
    except OSError:
        return


def is_under_program_files(path: Path) -> bool:
    """True when `path` lies under %ProgramFiles% or %ProgramFiles(x86)%.

    Case-insensitive prefix containment via pathlib relative_to.
    """
    target = path.resolve()
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            continue
        base = Path(raw).expanduser().resolve()
        try:
            target.relative_to(base)
        except ValueError:
            continue
        return True
    return False


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


def _default_data_dir() -> Path:
    if is_under_program_files(ROOT_DIR):
        return default_user_data_root()
    return ROOT_DIR / "data"


def resolve_data_dir() -> Path:
    """Resolve the configurable data root.

    Order: VRCFORGE_DATA_DIR env var > HKCU DataDir registry value (skipped in
    portable mode) > default rule (under Program Files -> LOCALAPPDATA, else
    <app dir>/data). Never falls inside Program Files by default.
    """
    value = os.environ.get("VRCFORGE_DATA_DIR", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    if not PORTABLE_MODE:
        registry_value = _read_registry_data_dir()
        if registry_value:
            return Path(registry_value).expanduser().resolve()
    return _default_data_dir()


DATA_DIR = resolve_data_dir()


def data_dir_writable() -> bool:
    """Whether DATA_DIR is writable.

    Checks DATA_DIR itself when it exists, otherwise the nearest existing
    parent. Uses os.access only - never probes by writing a file.
    """
    target = DATA_DIR
    if target.exists():
        return os.access(target, os.W_OK)
    parent = target.parent
    while not parent.exists():
        if parent.parent == parent:
            return False
        parent = parent.parent
    return os.access(parent, os.W_OK)
