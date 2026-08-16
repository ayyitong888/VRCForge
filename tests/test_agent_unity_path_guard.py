from __future__ import annotations

import pytest

from agent_unity_path_guard import UNITY_PROJECT_ACCESS, UnityPathGuard, UnityPathGuardError


ROOT = r"C:\Work\UnityProject"
OTHER = r"D:\Other\UnityProject"


@pytest.fixture
def guard() -> UnityPathGuard:
    return UnityPathGuard([ROOT, OTHER], current_root=ROOT)


def test_writes_inside_registered_root_reject_and_outside_permit(guard: UnityPathGuard) -> None:
    assert not guard.is_write_allowed(ROOT + r"\Assets\Avatar.prefab")
    assert guard.is_write_allowed(r"C:\Work\notes.txt")
    with pytest.raises(UnityPathGuardError):
        guard.authorize_write(ROOT + r"\Assets\Avatar.prefab")


def test_prefix_boundary_is_not_treated_as_inside(guard: UnityPathGuard) -> None:
    assert guard.is_write_allowed(r"C:\Work\UnityProject2\file.txt")
    assert not guard.is_write_allowed(OTHER + r"\Assets\file.txt")


def test_reads_remain_allowed_inside_roots(guard: UnityPathGuard) -> None:
    assert guard.is_read_allowed(ROOT + r"\ProjectSettings\ProjectVersion.txt")


def test_shell_rejects_root_cwd_or_direct_root_reference(guard: UnityPathGuard) -> None:
    assert not guard.is_shell_allowed("Get-ChildItem", cwd=ROOT + r"\Assets")
    assert not guard.is_shell_allowed("type C:/Work/UnityProject/Assets/a.txt", cwd=r"C:\Work")
    assert guard.is_shell_allowed("echo C:/Work/UnityProject2", cwd=r"C:\Work")


def test_unity_capability_allows_current_root_only(guard: UnityPathGuard) -> None:
    assert guard.is_write_allowed(ROOT + r"\Assets\a.txt", capability=UNITY_PROJECT_ACCESS)
    assert not guard.is_write_allowed(OTHER + r"\Assets\a.txt", capability=UNITY_PROJECT_ACCESS)
    assert guard.is_shell_allowed("Get-ChildItem", cwd=ROOT + r"\Assets", capability=UNITY_PROJECT_ACCESS)
    assert not guard.is_shell_allowed("Get-ChildItem", cwd=OTHER, capability=UNITY_PROJECT_ACCESS)


def test_roots_are_replaceable(guard: UnityPathGuard) -> None:
    guard.replace_roots([r"E:\NewUnity"])
    assert guard.is_write_allowed(ROOT + r"\Assets\a.txt")
    assert not guard.is_write_allowed(r"E:\NewUnity\Assets\a.txt")
