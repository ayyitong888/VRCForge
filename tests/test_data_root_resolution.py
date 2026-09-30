from __future__ import annotations

import os
from pathlib import Path

import vrcforge_runtime_paths as runtime_paths


# ---------------------------------------------------------------------------
# resolve_data_dir() matrix: env > registry > default rule, portable skips registry
# ---------------------------------------------------------------------------


def test_env_var_wins_over_registry_and_defaults(tmp_path: Path, monkeypatch) -> None:
    env_dir = tmp_path / "env-data"
    registry_dir = tmp_path / "registry-data"
    monkeypatch.setenv("VRCFORGE_DATA_DIR", str(env_dir))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    calls: list[str] = []

    def fake_registry() -> str | None:
        calls.append("read")
        return str(registry_dir)

    monkeypatch.setattr(runtime_paths, "_read_registry_data_dir", fake_registry)
    monkeypatch.setattr(
        runtime_paths,
        "ROOT_DIR",
        (tmp_path / "Program Files" / "VRCForge").resolve(),
    )
    monkeypatch.setattr(runtime_paths, "PORTABLE_MODE", False)

    assert runtime_paths.resolve_data_dir() == env_dir.resolve()
    # env short-circuits the whole chain: registry must never be read.
    assert calls == []


def test_registry_value_wins_over_default_rule(tmp_path: Path, monkeypatch) -> None:
    registry_dir = tmp_path / "registry-data"
    monkeypatch.delenv("VRCFORGE_DATA_DIR", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    monkeypatch.setattr(runtime_paths, "PORTABLE_MODE", False)
    monkeypatch.setattr(runtime_paths, "ROOT_DIR", tmp_path / "installed" / "VRCForge")
    monkeypatch.setattr(runtime_paths, "_read_registry_data_dir", lambda: str(registry_dir))

    assert runtime_paths.resolve_data_dir() == registry_dir.resolve()


def test_registry_read_failure_falls_back_to_default_rule(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("VRCFORGE_DATA_DIR", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    monkeypatch.setattr(runtime_paths, "PORTABLE_MODE", False)
    monkeypatch.setattr(runtime_paths, "ROOT_DIR", (tmp_path / "portable").resolve())
    monkeypatch.setattr(runtime_paths, "_read_registry_data_dir", lambda: None)

    assert runtime_paths.resolve_data_dir() == (tmp_path / "portable" / "data").resolve()


def test_default_under_program_files_uses_local_app_data(tmp_path: Path, monkeypatch) -> None:
    local_root = tmp_path / "LocalAppData" / "VRCForge" / "agentic-app"
    monkeypatch.delenv("VRCFORGE_DATA_DIR", raising=False)

    pf = tmp_path / "Program Files"
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    monkeypatch.setattr(runtime_paths, "PORTABLE_MODE", False)
    monkeypatch.setattr(runtime_paths, "_read_registry_data_dir", lambda: None)
    monkeypatch.setattr(runtime_paths, "default_user_data_root", lambda: local_root)
    monkeypatch.setattr(runtime_paths, "ROOT_DIR", pf / "VRCForge")

    assert runtime_paths.resolve_data_dir() == local_root


def test_default_outside_program_files_uses_root_data_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("VRCFORGE_DATA_DIR", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    monkeypatch.setattr(runtime_paths, "PORTABLE_MODE", False)
    monkeypatch.setattr(runtime_paths, "_read_registry_data_dir", lambda: None)
    monkeypatch.setattr(runtime_paths, "ROOT_DIR", (tmp_path / "D-VRCForge").resolve())

    assert runtime_paths.resolve_data_dir() == runtime_paths.ROOT_DIR / "data"


def test_frozen_portable_default_is_root_data_dir(tmp_path: Path, monkeypatch) -> None:
    # Frozen portable installs (app folder outside Program Files) take the
    # same default rule: <app dir>/data, never LOCALAPPDATA.
    monkeypatch.delenv("VRCFORGE_DATA_DIR", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    monkeypatch.setattr(runtime_paths, "PORTABLE_MODE", True)
    monkeypatch.setattr(runtime_paths, "_read_registry_data_dir", lambda: None)
    monkeypatch.setattr(runtime_paths, "ROOT_DIR", (tmp_path / "app").resolve())

    assert runtime_paths.resolve_data_dir() == runtime_paths.ROOT_DIR / "data"


def test_portable_mode_never_reads_registry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("VRCFORGE_DATA_DIR", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    def fail_if_called() -> str | None:
        raise AssertionError("registry must not be read in portable mode")

    monkeypatch.setattr(runtime_paths, "PORTABLE_MODE", True)
    monkeypatch.setattr(runtime_paths, "_read_registry_data_dir", fail_if_called)
    monkeypatch.setattr(runtime_paths, "ROOT_DIR", (tmp_path / "app").resolve())

    assert runtime_paths.resolve_data_dir() == runtime_paths.ROOT_DIR / "data"


# ---------------------------------------------------------------------------
# is_under_program_files()
# ---------------------------------------------------------------------------


def test_is_under_program_files_true_inside_case_insensitive(tmp_path: Path, monkeypatch) -> None:
    pf = tmp_path / "Program Files"
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    assert runtime_paths.is_under_program_files(pf / "VRCForge") is True
    assert runtime_paths.is_under_program_files(pf) is True
    mixed_case = Path(str(pf).replace("Program Files", "program files")) / "vrcforge"
    assert runtime_paths.is_under_program_files(mixed_case) is True


def test_is_under_program_files_x86_branch(tmp_path: Path, monkeypatch) -> None:
    pfx = tmp_path / "Program Files (x86)"
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.setenv("ProgramFiles(x86)", str(pfx))

    assert runtime_paths.is_under_program_files(pfx / "VRCForge") is True
    assert runtime_paths.is_under_program_files(tmp_path / "other") is False


def test_is_under_program_files_false_outside(tmp_path: Path, monkeypatch) -> None:
    pf = tmp_path / "Program Files"
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    # Same-looking sibling prefix must NOT count as inside.
    assert runtime_paths.is_under_program_files(tmp_path / "Program FilesX") is False
    assert runtime_paths.is_under_program_files(tmp_path / "elsewhere" / "VRCForge") is False


def test_is_under_program_files_missing_env_vars_is_false(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    assert runtime_paths.is_under_program_files(tmp_path / "anywhere") is False


# ---------------------------------------------------------------------------
# data_dir_writable()
# ---------------------------------------------------------------------------


def test_data_dir_writable_existing_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_paths, "DATA_DIR", tmp_path)
    assert runtime_paths.data_dir_writable() is True


def test_data_dir_writable_uses_nearest_existing_parent(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "missing" / "deep" / "data"
    monkeypatch.setattr(runtime_paths, "DATA_DIR", target)
    assert runtime_paths.data_dir_writable() is True


def test_data_dir_writable_checks_target_itself_when_it_exists(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    checked: list[Path] = []

    def fake_access(path: object, mode: int) -> bool:
        checked.append(Path(str(path)))
        return False

    monkeypatch.setattr(os, "access", fake_access)
    monkeypatch.setattr(runtime_paths, "DATA_DIR", target)

    assert runtime_paths.data_dir_writable() is False
    assert checked == [target]


def test_data_dir_writable_checks_parent_when_target_missing(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "missing" / "data"
    checked: list[Path] = []

    def fake_access(path: object, mode: int) -> bool:
        checked.append(Path(str(path)))
        return True

    monkeypatch.setattr(os, "access", fake_access)
    monkeypatch.setattr(runtime_paths, "DATA_DIR", target)

    assert runtime_paths.data_dir_writable() is True
    assert checked == [tmp_path]


def test_data_dir_module_constant_is_resolver_output() -> None:
    assert isinstance(runtime_paths.DATA_DIR, Path)
    assert runtime_paths.DATA_DIR == runtime_paths.resolve_data_dir()
