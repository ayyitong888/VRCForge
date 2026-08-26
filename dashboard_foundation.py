from __future__ import annotations

import os
import secrets
import sys

import vrcforge_runtime_paths as runtime_paths


def read_vrcforge_version() -> str:
    try:
        value = (runtime_paths.ROOT_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return os.environ.get("VRCFORGE_VERSION", "").strip() or "0.0.0-dev"
    return value or os.environ.get("VRCFORGE_VERSION", "").strip() or "0.0.0-dev"


def resolve_app_session_token() -> str:
    token = os.environ.get("VRCFORGE_APP_SESSION_TOKEN", "").strip()
    if token:
        return token
    token_path = runtime_paths.CONFIG_DIR / "app-session-token"
    try:
        if token_path.exists():
            existing = token_path.read_text(encoding="utf-8").strip()
            if len(existing) >= 32:
                return existing
        generated = secrets.token_urlsafe(32)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(generated, encoding="utf-8")
        return generated
    except OSError:
        return secrets.token_urlsafe(32)


def app_auth_disabled_for_test_process() -> bool:
    if os.environ.get("VRCFORGE_DISABLE_APP_AUTH", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return "pytest" in sys.modules


def runtime_settings_path() -> str:
    return str(runtime_paths.RUNTIME_SETTINGS_PATH)
