from __future__ import annotations

from pathlib import Path

import pytest

import skill_packages
from skill_packages import SkillPackageService
from tests.test_skill_packages import make_skill_source


def _winerror(code: int) -> OSError:
    error = OSError(f"simulated Windows publish error {code}")
    error.winerror = code
    return error


def _publish_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    package_root = tmp_path / "skill-store"
    staging = package_root / ".staging" / "staged"
    destination = package_root / "skill" / "versions" / "1.0.0"
    staging.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    return package_root, staging, destination


def test_windows_publish_retries_transient_access_denied_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root, staging, destination = _publish_fixture(tmp_path)
    errors = iter((_winerror(5), _winerror(32)))
    calls = 0
    sleeps: list[float] = []

    def replace(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        error = next(errors, None)
        if error is not None:
            raise error
        source.rename(target)

    monkeypatch.setattr(skill_packages, "_is_retryable_windows_publish_error", lambda _error: True)
    monkeypatch.setattr(skill_packages.os, "replace", replace)
    monkeypatch.setattr(skill_packages.time, "sleep", sleeps.append)

    skill_packages._replace_staging_with_bounded_windows_retry(
        staging, destination, package_root
    )

    assert calls == 3
    assert sum(sleeps) <= 1.0
    assert destination.is_dir()
    assert not staging.exists()


def test_windows_publish_persistent_access_denied_keeps_original_error_and_registry_uncreated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root, staging, destination = _publish_fixture(tmp_path)
    original = _winerror(33)
    calls = 0
    sleeps: list[float] = []

    def replace(_source: Path, _target: Path) -> None:
        nonlocal calls
        calls += 1
        raise original

    monkeypatch.setattr(skill_packages, "_is_retryable_windows_publish_error", lambda _error: True)
    monkeypatch.setattr(skill_packages.os, "replace", replace)
    monkeypatch.setattr(skill_packages.time, "sleep", sleeps.append)

    with pytest.raises(OSError) as raised:
        skill_packages._replace_staging_with_bounded_windows_retry(
            staging, destination, package_root
        )

    assert raised.value is original
    assert calls == 5
    assert sum(sleeps) <= 1.0
    assert staging.is_dir()
    assert not destination.exists()
    assert not (package_root / "registry.json").exists()


def test_non_windows_publish_error_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root, staging, destination = _publish_fixture(tmp_path)
    original = _winerror(5)
    calls = 0

    def replace(_source: Path, _target: Path) -> None:
        nonlocal calls
        calls += 1
        raise original

    monkeypatch.setattr(skill_packages, "_is_retryable_windows_publish_error", lambda _error: False)
    monkeypatch.setattr(skill_packages.os, "replace", replace)

    with pytest.raises(OSError) as raised:
        skill_packages._replace_staging_with_bounded_windows_retry(
            staging, destination, package_root
        )

    assert raised.value is original
    assert calls == 1
    assert staging.is_dir()
    assert not destination.exists()


def test_windows_publish_does_not_retry_after_destination_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root, staging, destination = _publish_fixture(tmp_path)
    original = _winerror(5)
    calls = 0
    sleeps: list[float] = []

    def replace(_source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        target.mkdir()
        raise original

    monkeypatch.setattr(skill_packages, "_is_retryable_windows_publish_error", lambda _error: True)
    monkeypatch.setattr(skill_packages.os, "replace", replace)
    monkeypatch.setattr(skill_packages.time, "sleep", sleeps.append)

    with pytest.raises(OSError) as raised:
        skill_packages._replace_staging_with_bounded_windows_retry(
            staging, destination, package_root
        )

    assert raised.value is original
    assert calls == 1
    assert sleeps == []
    assert destination.is_dir()
    assert staging.is_dir()


def test_windows_publish_rejects_an_existing_destination_without_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root, staging, destination = _publish_fixture(tmp_path)
    destination.mkdir()
    calls = 0

    def replace(_source: Path, _target: Path) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(skill_packages, "_is_retryable_windows_publish_error", lambda _error: True)
    monkeypatch.setattr(skill_packages.os, "replace", replace)

    with pytest.raises(skill_packages.PackageSecurityError):
        skill_packages._replace_staging_with_bounded_windows_retry(
            staging, destination, package_root
        )

    assert calls == 0
    assert destination.is_dir()
    assert staging.is_dir()


def test_windows_publish_rejects_parent_identity_drift_before_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root, staging, destination = _publish_fixture(tmp_path)
    original = _winerror(5)
    calls = 0
    replace_parent = destination.parent

    def replace(_source: Path, _target: Path) -> None:
        nonlocal calls
        calls += 1
        replace_parent.rename(replace_parent.with_name("versions-moved"))
        replace_parent.mkdir()
        raise original

    monkeypatch.setattr(skill_packages, "_is_retryable_windows_publish_error", lambda _error: True)
    monkeypatch.setattr(skill_packages.os, "replace", replace)

    with pytest.raises(skill_packages.PackageSecurityError, match="parent changed"):
        skill_packages._replace_staging_with_bounded_windows_retry(
            staging, destination, package_root
        )

    assert calls == 1
    assert staging.is_dir()


def test_windows_publish_rejects_staging_identity_change_before_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root, staging, destination = _publish_fixture(tmp_path)
    original = _winerror(5)
    calls = 0

    def replace(_source: Path, _target: Path) -> None:
        nonlocal calls
        calls += 1
        staging.rename(staging.with_name("replacement"))
        staging.mkdir()
        raise original

    monkeypatch.setattr(skill_packages, "_is_retryable_windows_publish_error", lambda _error: True)
    monkeypatch.setattr(skill_packages.os, "replace", replace)

    with pytest.raises(skill_packages.PackageSecurityError, match="staging identity"):
        skill_packages._replace_staging_with_bounded_windows_retry(
            staging, destination, package_root
        )

    assert calls == 1
    assert staging.is_dir()


def test_persistent_publish_failure_from_install_leaves_registry_uncreated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    package = service.export_dev(
        make_skill_source(tmp_path), tmp_path / "fixture.vsk"
    ).package_path
    original = _winerror(5)
    calls = 0

    def replace(source: Path, target: Path) -> None:
        nonlocal calls
        if source.parent.name == ".staging" and target.name == "1.0.0":
            calls += 1
            raise original
        raise AssertionError("unexpected atomic replace before publish succeeded")

    monkeypatch.setattr(skill_packages, "_is_retryable_windows_publish_error", lambda _error: True)
    monkeypatch.setattr(skill_packages.os, "replace", replace)
    monkeypatch.setattr(skill_packages.time, "sleep", lambda _delay: None)

    with pytest.raises(OSError) as raised:
        service.install(package)

    assert raised.value is original
    assert calls == 5
    assert not service.registry_path.exists()
