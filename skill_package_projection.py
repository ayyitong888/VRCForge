from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

from skill_packages import SkillPackageError


LEGACY_PROJECTED_SKILL_STATE_SCHEMA = "vrcforge.projected-skill-state.v1"
PROJECTION_DIGEST_DOMAIN = b"vrcforge.projected-skill-digest.v2\0"
PROJECTION_MAX_ENTRIES = 4_096
PROJECTION_MAX_FILE_BYTES = 8 * 1024 * 1024
PROJECTION_MAX_TOTAL_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _PreparedProjection:
    target_name: str
    package_id: str
    skill_file: Path
    support_sources: tuple[tuple[Path, PurePosixPath], ...]
    projected_support: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkillPackageProjectionPorts:
    """Fixed app capabilities needed by the internal projection owner."""

    user_skills_dir: Callable[[], Path]
    user_skill_lock: AbstractContextManager[object]
    find_user_skill: Callable[[str], dict[str, Any] | None]
    validate_projection_name: Callable[[str], None]
    make_conflict_error: Callable[[str], Exception]
    parse_skill: Callable[[Path], dict[str, Any]]
    parse_error_types: tuple[type[Exception], ...]
    installed_package_candidates: Callable[
        [str],
        tuple[tuple[Path, dict[str, Any]], ...],
    ]
    state_name: str
    state_schema: str
    state_max_bytes: int


class SkillPackageProjectionService:
    """Own atomic package-to-runtime projection inside one fixed skill root.

    The service creates no process, persistent handle, network channel, lock,
    approval, or lifecycle. Every transient file handle is scoped to its
    operation, and callers retain package transaction and lock ownership.
    """

    __slots__ = ("_ports",)

    def __init__(self, ports: SkillPackageProjectionPorts) -> None:
        self._ports = ports

    @staticmethod
    def _path_is_link_like(path: Path) -> bool:
        try:
            if path.is_symlink():
                return True
            is_junction = getattr(path, "is_junction", None)
            if callable(is_junction) and is_junction():
                return True
            attributes = getattr(
                path.stat(follow_symlinks=False),
                "st_file_attributes",
                0,
            )
            return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
        except OSError:
            return True

    def resolve_source(
        self,
        installed_root: Path,
        relative: str,
        *,
        label: str,
    ) -> tuple[Path, PurePosixPath]:
        if not relative or "\\" in relative:
            raise SkillPackageError(
                f"{label} must use a non-empty forward-slash relative path."
            )
        relative_path = PurePosixPath(relative)
        if relative_path.is_absolute() or any(
            part in {"", ".", ".."} for part in relative_path.parts
        ):
            raise SkillPackageError(f"Unsafe {label}: {relative}.")
        try:
            root = installed_root.resolve(strict=True)
            source = installed_root.joinpath(*relative_path.parts)
            resolved = source.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise SkillPackageError(
                f"{label} is missing or escapes the installed package: {relative}."
            ) from exc
        current = source
        while True:
            if self._path_is_link_like(current):
                raise SkillPackageError(
                    f"{label} cannot traverse a link or junction: {relative}."
                )
            if current == installed_root:
                break
            if installed_root not in current.parents:
                raise SkillPackageError(
                    f"{label} escapes the installed package: {relative}."
                )
            current = current.parent
        if not resolved.is_file():
            raise SkillPackageError(
                f"{label} is not a regular file: {relative}."
            )
        return resolved, relative_path

    def _copy_file(
        self,
        source: Path,
        target_dir: Path,
        relative: PurePosixPath,
    ) -> Path:
        destination = target_dir.joinpath(*relative.parts)
        current = target_dir
        for part in relative.parts[:-1]:
            current = current / part
            if current.exists():
                if self._path_is_link_like(current) or not current.is_dir():
                    raise RuntimeError(
                        f"Refusing to project through unsafe skill directory: {current}"
                    )
            else:
                current.mkdir()
        if destination.exists() and (
            self._path_is_link_like(destination) or not destination.is_file()
        ):
            raise RuntimeError(
                f"Refusing to overwrite unsafe projected skill file: {destination}"
            )
        temporary = destination.with_name(
            f".{destination.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def _write_state(
        self,
        target_dir: Path,
        enabled: bool,
        package_id: str,
        projection_digest: str,
    ) -> Path:
        state_path = target_dir / self._ports.state_name
        if os.path.lexists(state_path) and (
            self._path_is_link_like(state_path) or not state_path.is_file()
        ):
            raise RuntimeError(
                f"Refusing to overwrite unsafe projected skill state: {state_path}"
            )
        payload = json.dumps(
            {
                "enabled": bool(enabled),
                "packageId": package_id,
                "projectionDigest": projection_digest,
                "schema": self._ports.state_schema,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8") + b"\n"
        temporary = state_path.with_name(
            f".{state_path.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, state_path)
        finally:
            temporary.unlink(missing_ok=True)
        return state_path

    def _name(self, manifest: dict[str, Any]) -> str:
        target_name = str(
            manifest.get("skill_name")
            or manifest.get("skillName")
            or manifest.get("id")
            or ""
        ).strip()
        return re.sub(r"[^a-z0-9_.-]+", "-", target_name.lower()).strip(
            "-._"
        )

    @staticmethod
    def _package_id(manifest: dict[str, Any]) -> str:
        return str(
            manifest.get("id")
            or manifest.get("packageId")
            or manifest.get("package_id")
            or ""
        ).strip()

    def _hash_projection_entries(
        self,
        entries: list[tuple[bytes, str, Path | None]],
    ) -> str:
        if len(entries) > PROJECTION_MAX_ENTRIES:
            raise self._ports.make_conflict_error(
                "Projected skill contains too many filesystem entries."
            )
        digest = hashlib.sha256(PROJECTION_DIGEST_DOMAIN)
        ordered = sorted(entries, key=lambda item: (item[1].casefold(), item[1], item[0]))
        digest.update(len(ordered).to_bytes(8, "big"))
        total_bytes = 0
        for entry_type, relative, source in ordered:
            path_bytes = relative.encode("utf-8")
            digest.update(entry_type)
            digest.update(len(path_bytes).to_bytes(8, "big"))
            digest.update(path_bytes)
            if entry_type == b"D":
                continue
            if source is None or self._path_is_link_like(source) or not source.is_file():
                raise self._ports.make_conflict_error(
                    f"Projected skill contains an unsafe file: {relative}"
                )
            metadata = source.stat(follow_symlinks=False)
            if metadata.st_size > PROJECTION_MAX_FILE_BYTES:
                raise self._ports.make_conflict_error(
                    f"Projected skill file exceeds its size limit: {relative}"
                )
            total_bytes += metadata.st_size
            if total_bytes > PROJECTION_MAX_TOTAL_BYTES:
                raise self._ports.make_conflict_error(
                    "Projected skill files exceed their total size limit."
                )
            digest.update(metadata.st_size.to_bytes(8, "big"))
            consumed = 0
            with source.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    consumed += len(chunk)
                    if consumed > metadata.st_size:
                        raise self._ports.make_conflict_error(
                            f"Projected skill file changed while being verified: {relative}"
                        )
                    digest.update(chunk)
            if consumed != metadata.st_size:
                raise self._ports.make_conflict_error(
                    f"Projected skill file changed while being verified: {relative}"
                )
        return digest.hexdigest()

    def _append_projection_entry(
        self,
        entries: list[tuple[bytes, str, Path | None]],
        entry: tuple[bytes, str, Path | None],
    ) -> None:
        entries.append(entry)
        if len(entries) > PROJECTION_MAX_ENTRIES:
            raise self._ports.make_conflict_error(
                "Projected skill contains too many filesystem entries."
            )

    def _projection_digest(self, target_dir: Path) -> str:
        pending = [target_dir]
        entries: list[tuple[bytes, str, Path | None]] = []
        while pending:
            directory = pending.pop()
            if self._path_is_link_like(directory) or not directory.is_dir():
                raise self._ports.make_conflict_error(
                    f"Projected skill contains an unsafe directory: {directory.name}"
                )
            for child in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
                if child.name == self._ports.state_name and child.parent == target_dir:
                    continue
                if self._path_is_link_like(child):
                    raise self._ports.make_conflict_error(
                        f"Projected skill contains a linked path: {child.name}"
                    )
                if child.is_dir():
                    self._append_projection_entry(
                        entries,
                        (b"D", child.relative_to(target_dir).as_posix(), None)
                    )
                    pending.append(child)
                elif child.is_file():
                    self._append_projection_entry(
                        entries,
                        (b"F", child.relative_to(target_dir).as_posix(), child)
                    )
                else:
                    raise self._ports.make_conflict_error(
                        f"Projected skill contains a non-regular path: {child.name}"
                    )
        return self._hash_projection_entries(entries)

    def _expected_projection_digest(self, prepared: _PreparedProjection) -> str:
        files: list[tuple[PurePosixPath, Path]] = [
            (PurePosixPath("SKILL.md"), prepared.skill_file),
            *[(relative, source) for source, relative in prepared.support_sources],
        ]
        directories: set[str] = set()
        entries: list[tuple[bytes, str, Path | None]] = []
        for relative, source in files:
            self._append_projection_entry(
                entries,
                (b"F", relative.as_posix(), source),
            )
            parent = relative.parent
            while parent != PurePosixPath("."):
                directories.add(parent.as_posix())
                parent = parent.parent
        for relative in directories:
            self._append_projection_entry(entries, (b"D", relative, None))
        return self._hash_projection_entries(entries)

    def _prepare_legacy_projection_source(
        self,
        installed_path: Path,
        manifest: dict[str, Any],
        expected_target_name: str,
    ) -> _PreparedProjection:
        target_name = self._name(manifest)
        package_id = self._package_id(manifest)
        if target_name != expected_target_name or not package_id:
            raise SkillPackageError("Installed projection identity does not match.")
        entrypoints = (
            manifest.get("entrypoints")
            if isinstance(manifest.get("entrypoints"), dict)
            else {}
        )
        skill_relative = str(entrypoints.get("skill") or "SKILL.md").strip()
        skill_file, _ = self.resolve_source(
            installed_path,
            skill_relative,
            label="skill entrypoint",
        )
        parsed_skill = self._ports.parse_skill(skill_file)
        parsed_name = self._name({"skill_name": parsed_skill.get("name")})
        if parsed_name != target_name:
            raise SkillPackageError(
                "Installed SKILL.md name must match the projected skill name."
            )
        declared_support = (
            parsed_skill.get("supportFiles")
            if isinstance(parsed_skill.get("supportFiles"), list)
            else []
        )
        projected_entrypoint_paths = {
            str(value or "").strip()
            for name, value in entrypoints.items()
            if name != "skill" and str(value or "").strip() != skill_relative
        }
        undeclared = sorted(
            str(value or "").strip()
            for value in declared_support
            if str(value or "").strip() not in projected_entrypoint_paths
        )
        if undeclared:
            raise SkillPackageError(
                "Runtime support files must also be declared as manifest entrypoints."
            )
        reserved = {"skill.md", self._ports.state_name.casefold()}
        seen = set(reserved)
        support_sources: list[tuple[Path, PurePosixPath]] = []
        projected_support: list[str] = []
        for entrypoint_name, raw_relative in sorted(entrypoints.items()):
            relative = str(raw_relative or "").strip()
            if entrypoint_name == "skill" or relative == skill_relative:
                continue
            source, relative_path = self.resolve_source(
                installed_path,
                relative,
                label=f"support entrypoint {entrypoint_name}",
            )
            normalized = relative_path.as_posix()
            collision_key = normalized.casefold()
            if collision_key in reserved:
                raise SkillPackageError(
                    f"Support entrypoint cannot overwrite projected state: {normalized}."
                )
            if collision_key in seen:
                continue
            seen.add(collision_key)
            support_sources.append((source, relative_path))
            projected_support.append(normalized)
        return _PreparedProjection(
            target_name=target_name,
            package_id=package_id,
            skill_file=skill_file,
            support_sources=tuple(support_sources),
            projected_support=tuple(projected_support),
        )

    def _migrate_legacy_owned_target(
        self,
        target_dir: Path,
        manifest: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[str, str, bool, bool]:
        package_id = self._package_id(manifest)
        if (
            set(state) != {"enabled", "schema"}
            or state.get("schema") != LEGACY_PROJECTED_SKILL_STATE_SCHEMA
            or not isinstance(state.get("enabled"), bool)
            or not package_id
        ):
            raise self._ports.make_conflict_error(
                f"Projected skill ownership state is invalid: {target_dir.name}"
            )
        current_digest = self._projection_digest(target_dir)
        matched = False
        for installed_root, installed_manifest in self._ports.installed_package_candidates(
            package_id
        ):
            try:
                prepared = self._prepare_legacy_projection_source(
                    installed_root,
                    installed_manifest,
                    target_dir.name,
                )
                if self._expected_projection_digest(prepared) == current_digest:
                    matched = True
                    break
            except (OSError, SkillPackageError, *self._ports.parse_error_types):
                continue
        if not matched:
            raise self._ports.make_conflict_error(
                "Legacy projected skill does not exactly match an installed package: "
                f"{target_dir.name}"
            )
        return package_id, current_digest, bool(state["enabled"]), True

    def _require_owned_target(
        self,
        target_dir: Path,
        manifest: dict[str, Any],
    ) -> tuple[str, str, bool, bool]:
        package_id = self._package_id(manifest)
        if not package_id:
            raise SkillPackageError("Projected skill package id is required.")
        state_path = target_dir / self._ports.state_name
        if not os.path.lexists(state_path):
            raise self._ports.make_conflict_error(
                f"User skill path is not owned by this package: {target_dir.name}"
            )
        if self._path_is_link_like(state_path) or not state_path.is_file():
            raise self._ports.make_conflict_error(
                f"Projected skill ownership state is unsafe: {target_dir.name}"
            )
        metadata = state_path.stat(follow_symlinks=False)
        if metadata.st_size > self._ports.state_max_bytes:
            raise self._ports.make_conflict_error(
                f"Projected skill ownership state is invalid: {target_dir.name}"
            )
        try:
            with state_path.open("rb") as stream:
                raw_state = stream.read(self._ports.state_max_bytes + 1)
            if len(raw_state) > self._ports.state_max_bytes:
                raise ValueError("state exceeds its size limit")
            state = json.loads(raw_state.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise self._ports.make_conflict_error(
                f"Projected skill ownership state is invalid: {target_dir.name}"
            ) from exc
        if not isinstance(state, dict):
            raise self._ports.make_conflict_error(
                f"Projected skill ownership state is invalid: {target_dir.name}"
            )
        if state.get("schema") == LEGACY_PROJECTED_SKILL_STATE_SCHEMA:
            return self._migrate_legacy_owned_target(target_dir, manifest, state)
        if (
            state.get("schema") != self._ports.state_schema
            or str(state.get("packageId") or "") != package_id
        ):
            raise self._ports.make_conflict_error(
                f"User skill path belongs to another owner: {target_dir.name}"
            )
        expected_digest = str(state.get("projectionDigest") or "")
        if not isinstance(state.get("enabled"), bool):
            raise self._ports.make_conflict_error(
                f"Projected skill ownership state is invalid: {target_dir.name}"
            )
        current_digest = self._projection_digest(target_dir)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_digest) or current_digest != expected_digest:
            raise self._ports.make_conflict_error(
                f"Projected skill was modified outside its owning package: {target_dir.name}"
            )
        return package_id, current_digest, bool(state["enabled"]), False

    def _capture_state(
        self,
        manifest: dict[str, Any],
    ) -> tuple[Path, bytes | None]:
        target_name = self._name(manifest)
        skills_root = self._ports.user_skills_dir()
        if target_name and os.path.lexists(skills_root) and (
            self._path_is_link_like(skills_root) or not skills_root.is_dir()
        ):
            raise RuntimeError(
                f"Projected skill root is not a safe directory: {skills_root}"
            )
        target_dir = skills_root / target_name
        if target_name and os.path.lexists(target_dir) and (
            self._path_is_link_like(target_dir) or not target_dir.is_dir()
        ):
            raise RuntimeError(
                f"Projected skill directory is not a safe directory: {target_name}"
            )
        state_path = target_dir / self._ports.state_name
        if not os.path.lexists(state_path):
            return state_path, None
        if self._path_is_link_like(state_path) or not state_path.is_file():
            raise RuntimeError(
                f"Projected skill state is not a regular file: {target_name}"
            )
        metadata = state_path.stat(follow_symlinks=False)
        if metadata.st_size > self._ports.state_max_bytes:
            raise RuntimeError(
                f"Projected skill state exceeds its size limit: {target_name}"
            )
        with state_path.open("rb") as stream:
            data = stream.read(self._ports.state_max_bytes + 1)
        if len(data) > self._ports.state_max_bytes:
            raise RuntimeError(
                f"Projected skill state exceeds its size limit: {target_name}"
            )
        return state_path, data

    def _restore_state(self, snapshot: tuple[Path, bytes | None]) -> None:
        state_path, data = snapshot
        target_dir = state_path.parent
        if data is None:
            if os.path.lexists(state_path):
                if self._path_is_link_like(state_path) or not state_path.is_file():
                    raise RuntimeError(
                        "Refusing to remove unsafe projected skill state: "
                        f"{target_dir.name}"
                    )
                state_path.unlink()
            return
        if (
            not target_dir.is_dir()
            or self._path_is_link_like(target_dir)
            or (
                os.path.lexists(state_path)
                and self._path_is_link_like(state_path)
            )
        ):
            raise RuntimeError(
                f"Refusing to restore linked projected skill state: {target_dir.name}"
            )
        temporary = state_path.with_name(
            f".{state_path.name}.{secrets.token_hex(8)}.rollback.tmp"
        )
        try:
            temporary.write_bytes(data)
            os.replace(temporary, state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def set_enabled_batch(
        self,
        manifests: list[dict[str, Any]],
        enabled: bool,
    ) -> list[dict[str, Any]]:
        with self._ports.user_skill_lock:
            return self._set_enabled_batch(manifests, enabled)

    def _set_enabled_batch(
        self,
        manifests: list[dict[str, Any]],
        enabled: bool,
    ) -> list[dict[str, Any]]:
        snapshots = [self._capture_state(manifest) for manifest in manifests]
        try:
            return [
                self._set_enabled(manifest, enabled) for manifest in manifests
            ]
        except Exception as exc:
            try:
                for snapshot in reversed(snapshots):
                    self._restore_state(snapshot)
            except Exception as restore_exc:
                raise RuntimeError(
                    "Projected skill state update failed and prior state could "
                    f"not be restored: {restore_exc}"
                ) from exc
            raise

    def project_installed(
        self,
        installed_path: Path,
        manifest: dict[str, Any],
        *,
        enabled: bool = True,
    ) -> dict[str, Any] | None:
        with self._ports.user_skill_lock:
            return self._project_installed(
                installed_path,
                manifest,
                enabled=enabled,
            )

    def _project_installed(
        self,
        installed_path: Path,
        manifest: dict[str, Any],
        *,
        enabled: bool = True,
    ) -> dict[str, Any] | None:
        target_name = self._name(manifest)
        if target_name:
            self._ports.validate_projection_name(target_name)
        skills_root = self._ports.user_skills_dir()
        target_dir = skills_root / target_name
        if target_name and os.path.lexists(skills_root) and (
            self._path_is_link_like(skills_root) or not skills_root.is_dir()
        ):
            raise RuntimeError(
                f"Refusing to write through linked user skill root: {skills_root}"
            )
        if target_name and os.path.lexists(target_dir) and (
            self._path_is_link_like(target_dir) or not target_dir.is_dir()
        ):
            raise RuntimeError(
                f"Refusing to write through symlinked skill directory: {target_dir}"
            )
        entrypoints = (
            manifest.get("entrypoints")
            if isinstance(manifest.get("entrypoints"), dict)
            else {}
        )
        skill_relative = str(entrypoints.get("skill") or "SKILL.md").strip()
        skill_file, _ = self.resolve_source(
            installed_path,
            skill_relative,
            label="skill entrypoint",
        )
        try:
            parsed_skill = self._ports.parse_skill(skill_file)
        except self._ports.parse_error_types as exc:
            raise SkillPackageError(
                f"Installed SKILL.md cannot be projected: {exc}"
            ) from exc
        parsed_name = self._name({"skill_name": parsed_skill.get("name")})
        if not parsed_name or parsed_name != target_name:
            raise SkillPackageError(
                "Installed SKILL.md name must match the projected skill name."
            )
        self._ports.validate_projection_name(parsed_name)
        declared_support = (
            parsed_skill.get("supportFiles")
            if isinstance(parsed_skill.get("supportFiles"), list)
            else []
        )
        projected_entrypoint_paths = {
            str(value or "").strip()
            for name, value in entrypoints.items()
            if name != "skill" and str(value or "").strip() != skill_relative
        }
        undeclared_projection_sources = sorted(
            str(value or "").strip()
            for value in declared_support
            if str(value or "").strip() not in projected_entrypoint_paths
        )
        if undeclared_projection_sources:
            raise SkillPackageError(
                "Runtime support files must also be declared as manifest "
                "entrypoints: " + ", ".join(undeclared_projection_sources)
            )
        support_sources: list[tuple[Path, PurePosixPath]] = []
        projected_support: list[str] = []
        reserved_projection_paths = {
            "skill.md",
            self._ports.state_name.casefold(),
        }
        seen_support: set[str] = set(reserved_projection_paths)
        for entrypoint_name, raw_relative in sorted(entrypoints.items()):
            relative = str(raw_relative or "").strip()
            if entrypoint_name == "skill" or relative == skill_relative:
                continue
            source, relative_path = self.resolve_source(
                installed_path,
                relative,
                label=f"support entrypoint {entrypoint_name}",
            )
            normalized = relative_path.as_posix()
            collision_key = normalized.casefold()
            if collision_key in reserved_projection_paths:
                raise SkillPackageError(
                    "Support entrypoint cannot overwrite reserved projected "
                    f"skill state: {normalized}."
                )
            if collision_key in seen_support:
                continue
            seen_support.add(collision_key)
            support_sources.append((source, relative_path))
            projected_support.append(normalized)
        if not target_name:
            return None
        package_id = self._package_id(manifest)
        if not package_id:
            raise SkillPackageError("Projected skill package id is required.")
        if os.path.lexists(target_dir):
            _owner, _digest, existing_enabled, _migrated = (
                self._require_owned_target(target_dir, manifest)
            )
            enabled = existing_enabled
        skills_root.mkdir(parents=True, exist_ok=True)
        if self._path_is_link_like(skills_root):
            raise RuntimeError(
                f"Refusing to write through linked user skill root: {skills_root}"
            )
        staging_root = skills_root / ".package-projection-staging"
        if staging_root.exists() and (
            self._path_is_link_like(staging_root) or not staging_root.is_dir()
        ):
            raise RuntimeError(
                f"Refusing to use unsafe skill projection staging root: {staging_root}"
            )
        staging_root.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(16)
        staging_dir = staging_root / f"{target_name}.{token}.new"
        backup_dir = staging_root / f"{target_name}.{token}.old"
        staging_dir.mkdir()
        target_moved = False
        try:
            self._copy_file(skill_file, staging_dir, PurePosixPath("SKILL.md"))
            for source, relative_path in support_sources:
                self._copy_file(source, staging_dir, relative_path)
            projection_digest = self._projection_digest(staging_dir)
            self._write_state(
                staging_dir,
                enabled,
                package_id,
                projection_digest,
            )
            if os.path.lexists(target_dir):
                os.replace(target_dir, backup_dir)
                target_moved = True
            try:
                os.replace(staging_dir, target_dir)
                state = self._ports.find_user_skill(target_name)
            except Exception as exc:
                try:
                    if os.path.lexists(target_dir) and not staging_dir.exists():
                        if (
                            self._path_is_link_like(target_dir)
                            or not target_dir.is_dir()
                        ):
                            raise RuntimeError(
                                "Projected skill target became unsafe during "
                                f"rollback: {target_name}"
                            )
                        try:
                            os.replace(target_dir, staging_dir)
                        except Exception:
                            if (
                                self._path_is_link_like(target_dir)
                                or not target_dir.is_dir()
                            ):
                                raise
                            shutil.rmtree(target_dir)
                    if (
                        target_moved
                        and backup_dir.exists()
                        and not os.path.lexists(target_dir)
                    ):
                        os.replace(backup_dir, target_dir)
                        target_moved = False
                except Exception as restore_exc:
                    recovery = (
                        str(backup_dir)
                        if backup_dir.exists()
                        else str(target_dir)
                    )
                    raise RuntimeError(
                        "Projected skill publish failed and the prior projection "
                        f"could not be restored; recovery data remains at: {recovery}"
                    ) from restore_exc
                raise
            if target_moved:
                shutil.rmtree(backup_dir, ignore_errors=True)
                target_moved = False
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            if backup_dir.exists() and not target_moved:
                shutil.rmtree(backup_dir, ignore_errors=True)
            if staging_root.exists():
                try:
                    staging_root.rmdir()
                except OSError:
                    pass
        return {
            "name": target_name,
            "path": str(target_dir / "SKILL.md"),
            "enabled": bool(enabled),
            "supportFiles": projected_support,
            "skill": state,
        }

    def _set_enabled(
        self,
        manifest: dict[str, Any],
        enabled: bool,
    ) -> dict[str, Any]:
        target_name = self._name(manifest)
        if not target_name:
            return {
                "ok": True,
                "skipped": True,
                "reason": "manifest has no projected skill name",
            }
        target_dir = self._ports.user_skills_dir() / target_name
        skill_file = target_dir / "SKILL.md"
        if not skill_file.is_file():
            return {"ok": True, "name": target_name, "missing": True}
        if self._path_is_link_like(target_dir) or self._path_is_link_like(
            skill_file
        ):
            raise RuntimeError(
                f"Refusing to update linked projected skill state: {target_name}"
            )
        package_id, projection_digest, _previous_enabled, _migrated = self._require_owned_target(
            target_dir,
            manifest,
        )
        self._write_state(
            target_dir,
            enabled,
            package_id,
            projection_digest,
        )
        return {
            "ok": True,
            "name": target_name,
            "skill": self._ports.find_user_skill(target_name),
        }

    @contextmanager
    def delete_transaction(
        self,
        manifest: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        with self._ports.user_skill_lock:
            with self._delete_transaction(manifest) as result:
                yield result

    @contextmanager
    def _delete_transaction(
        self,
        manifest: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        target_name = self._name(manifest)
        if not target_name:
            yield {
                "ok": True,
                "skipped": True,
                "reason": "manifest has no projected skill name",
            }
            return
        skills_root = self._ports.user_skills_dir()
        target_dir = skills_root / target_name
        if not os.path.lexists(target_dir):
            yield {"ok": True, "name": target_name, "missing": True}
            return
        if self._path_is_link_like(target_dir) or not target_dir.is_dir():
            raise RuntimeError(
                f"Refusing to remove unsafe projected skill directory: {target_name}"
            )
        self._require_owned_target(target_dir, manifest)
        staging_root = skills_root / ".package-projection-staging"
        if os.path.lexists(staging_root) and (
            self._path_is_link_like(staging_root) or not staging_root.is_dir()
        ):
            raise RuntimeError(
                f"Refusing to use unsafe skill projection staging root: {staging_root}"
            )
        staging_root.mkdir(parents=True, exist_ok=True)
        isolated_dir = (
            staging_root / f"{target_name}.{secrets.token_hex(16)}.removed"
        )
        os.replace(target_dir, isolated_dir)
        try:
            yield {"ok": True, "name": target_name, "deleted": target_name}
        except Exception:
            try:
                if os.path.lexists(target_dir):
                    raise RuntimeError(
                        "Projected skill path was recreated during rollback: "
                        f"{target_name}"
                    )
                os.replace(isolated_dir, target_dir)
            except Exception as restore_exc:
                raise RuntimeError(
                    "Projected skill removal failed and prior projection could "
                    f"not be restored: {target_name}"
                ) from restore_exc
            raise
        else:
            shutil.rmtree(isolated_dir, ignore_errors=True)
        finally:
            if staging_root.exists():
                try:
                    staging_root.rmdir()
                except OSError:
                    pass
