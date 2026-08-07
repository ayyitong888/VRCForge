from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any


class SkillPackageProjectionService:
    __slots__ = ("_host",)

    def __init__(self, host: Any) -> None:
        self._host = host

    def __getattr__(self, name: str) -> Any:
        return getattr(self._host, name)

    def _impl_skill_projection_path_is_link_like(self, path: Path) -> bool:
        try:
            if path.is_symlink():
                return True
            is_junction = getattr(path, "is_junction", None)
            if callable(is_junction) and is_junction():
                return True
            attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
            return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
        except OSError:
            return True

    def _impl_resolve_skill_projection_source(self, installed_root: Path, relative: str, *, label: str) -> tuple[Path, PurePosixPath]:
        if not relative or "\\" in relative:
            raise self._host.SkillPackageError(f"{label} must use a non-empty forward-slash relative path.")
        relative_path = PurePosixPath(relative)
        if relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
            raise self._host.SkillPackageError(f"Unsafe {label}: {relative}.")
        try:
            root = installed_root.resolve(strict=True)
            source = installed_root.joinpath(*relative_path.parts)
            resolved = source.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise self._host.SkillPackageError(f"{label} is missing or escapes the installed package: {relative}.") from exc
        current = source
        while True:
            if self._host._skill_projection_path_is_link_like(current):
                raise self._host.SkillPackageError(f"{label} cannot traverse a link or junction: {relative}.")
            if current == installed_root:
                break
            if installed_root not in current.parents:
                raise self._host.SkillPackageError(f"{label} escapes the installed package: {relative}.")
            current = current.parent
        if not resolved.is_file():
            raise self._host.SkillPackageError(f"{label} is not a regular file: {relative}.")
        return resolved, relative_path

    def _impl_copy_projected_skill_file(self, source: Path, target_dir: Path, relative: PurePosixPath) -> Path:
        destination = target_dir.joinpath(*relative.parts)
        current = target_dir
        for part in relative.parts[:-1]:
            current = current / part
            if current.exists():
                if self._host._skill_projection_path_is_link_like(current) or not current.is_dir():
                    raise RuntimeError(f"Refusing to project through unsafe skill directory: {current}")
            else:
                current.mkdir()
        if destination.exists() and (self._host._skill_projection_path_is_link_like(destination) or not destination.is_file()):
            raise RuntimeError(f"Refusing to overwrite unsafe projected skill file: {destination}")
        temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def _impl_write_projected_skill_state(self, target_dir: Path, enabled: bool) -> Path:
        state_path = target_dir / self._host.PROJECTED_SKILL_STATE_NAME
        if state_path.exists() and (self._host._skill_projection_path_is_link_like(state_path) or not state_path.is_file()):
            raise RuntimeError(f"Refusing to overwrite unsafe projected skill state: {state_path}")
        payload = json.dumps(
            {"enabled": bool(enabled), "schema": self._host.PROJECTED_SKILL_STATE_SCHEMA},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8") + b"\n"
        temporary = state_path.with_name(f".{state_path.name}.{secrets.token_hex(8)}.tmp")
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, state_path)
        finally:
            temporary.unlink(missing_ok=True)
        return state_path

    def _impl_capture_projected_skill_state(self, manifest: dict[str, Any]) -> tuple[Path, bytes | None]:
        target_name = self._host._projected_skill_name(manifest)
        state_path = self._host.AGENT_GATEWAY.user_skills_dir / target_name / self._host.PROJECTED_SKILL_STATE_NAME
        if not os.path.lexists(state_path):
            return state_path, None
        if self._host._skill_projection_path_is_link_like(state_path) or not state_path.is_file():
            raise RuntimeError(f"Projected skill state is not a regular file: {target_name}")
        metadata = state_path.stat(follow_symlinks=False)
        if metadata.st_size > self._host.PROJECTED_SKILL_STATE_MAX_BYTES:
            raise RuntimeError(f"Projected skill state exceeds its size limit: {target_name}")
        with state_path.open("rb") as stream:
            data = stream.read(self._host.PROJECTED_SKILL_STATE_MAX_BYTES + 1)
        if len(data) > self._host.PROJECTED_SKILL_STATE_MAX_BYTES:
            raise RuntimeError(f"Projected skill state exceeds its size limit: {target_name}")
        return state_path, data

    def _impl_restore_projected_skill_state(self, snapshot: tuple[Path, bytes | None]) -> None:
        state_path, data = snapshot
        target_dir = state_path.parent
        if data is None:
            if os.path.lexists(state_path):
                if self._host._skill_projection_path_is_link_like(state_path) or not state_path.is_file():
                    raise RuntimeError(f"Refusing to remove unsafe projected skill state: {target_dir.name}")
                state_path.unlink()
            return
        if (
            not target_dir.is_dir()
            or self._host._skill_projection_path_is_link_like(target_dir)
            or (os.path.lexists(state_path) and self._host._skill_projection_path_is_link_like(state_path))
        ):
            raise RuntimeError(f"Refusing to restore linked projected skill state: {target_dir.name}")
        temporary = state_path.with_name(f".{state_path.name}.{secrets.token_hex(8)}.rollback.tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _impl_set_projected_skills_enabled(self, manifests: list[dict[str, Any]], enabled: bool) -> list[dict[str, Any]]:
        snapshots = [self._host._capture_projected_skill_state(manifest) for manifest in manifests]
        try:
            return [self._host._set_projected_skill_enabled(manifest, enabled) for manifest in manifests]
        except Exception as exc:
            try:
                for snapshot in reversed(snapshots):
                    self._host._restore_projected_skill_state(snapshot)
            except Exception as restore_exc:
                raise RuntimeError(
                    f"Projected skill state update failed and prior state could not be restored: {restore_exc}"
                ) from exc
            raise

    def _impl_project_installed_skill(self, installed_path: Path, manifest: dict[str, Any], *, enabled: bool = True) -> dict[str, Any] | None:
        entrypoints = manifest.get("entrypoints") if isinstance(manifest.get("entrypoints"), dict) else {}
        skill_relative = str(entrypoints.get("skill") or "SKILL.md").strip()
        skill_file, _ = self._host._resolve_skill_projection_source(installed_path, skill_relative, label="skill entrypoint")
        try:
            parsed_skill = self._host.parse_skill_markdown(skill_file)
        except self._host.AgentGatewayError as exc:
            raise self._host.SkillPackageError(f"Installed SKILL.md cannot be projected: {exc}") from exc
        declared_support = parsed_skill.get("supportFiles") if isinstance(parsed_skill.get("supportFiles"), list) else []
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
            raise self._host.SkillPackageError(
                "Runtime support files must also be declared as manifest entrypoints: "
                + ", ".join(undeclared_projection_sources)
            )
        support_sources: list[tuple[Path, PurePosixPath]] = []
        projected_support: list[str] = []
        reserved_projection_paths = {"skill.md", self._host.PROJECTED_SKILL_STATE_NAME.casefold()}
        seen_support: set[str] = set(reserved_projection_paths)
        for entrypoint_name, raw_relative in sorted(entrypoints.items()):
            relative = str(raw_relative or "").strip()
            if entrypoint_name == "skill" or relative == skill_relative:
                continue
            source, relative_path = self._host._resolve_skill_projection_source(
                installed_path,
                relative,
                label=f"support entrypoint {entrypoint_name}",
            )
            normalized = relative_path.as_posix()
            collision_key = normalized.casefold()
            if collision_key in reserved_projection_paths:
                raise self._host.SkillPackageError(f"Support entrypoint cannot overwrite reserved projected skill state: {normalized}.")
            if collision_key in seen_support:
                continue
            seen_support.add(collision_key)
            support_sources.append((source, relative_path))
            projected_support.append(normalized)
        target_name = self._host._projected_skill_name(manifest)
        if not target_name:
            return None
        skills_root = self._host.AGENT_GATEWAY.user_skills_dir
        skills_root.mkdir(parents=True, exist_ok=True)
        if self._host._skill_projection_path_is_link_like(skills_root):
            raise RuntimeError(f"Refusing to write through linked user skill root: {skills_root}")
        target_dir = skills_root / target_name
        if target_dir.exists() and (self._host._skill_projection_path_is_link_like(target_dir) or not target_dir.is_dir()):
            raise RuntimeError(f"Refusing to write through symlinked skill directory: {target_dir}")
        staging_root = skills_root / ".package-projection-staging"
        if staging_root.exists() and (self._host._skill_projection_path_is_link_like(staging_root) or not staging_root.is_dir()):
            raise RuntimeError(f"Refusing to use unsafe skill projection staging root: {staging_root}")
        staging_root.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(16)
        staging_dir = staging_root / f"{target_name}.{token}.new"
        backup_dir = staging_root / f"{target_name}.{token}.old"
        staging_dir.mkdir()
        target_moved = False
        try:
            self._host._copy_projected_skill_file(skill_file, staging_dir, PurePosixPath("SKILL.md"))
            for source, relative_path in support_sources:
                self._host._copy_projected_skill_file(source, staging_dir, relative_path)
            self._host._write_projected_skill_state(staging_dir, enabled)
            if target_dir.exists():
                os.replace(target_dir, backup_dir)
                target_moved = True
            try:
                os.replace(staging_dir, target_dir)
            except Exception:
                if target_moved and backup_dir.exists() and not target_dir.exists():
                    os.replace(backup_dir, target_dir)
                    target_moved = False
                raise
            if target_moved:
                shutil.rmtree(backup_dir, ignore_errors=True)
                target_moved = False
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            if target_moved and backup_dir.exists() and not target_dir.exists():
                os.replace(backup_dir, target_dir)
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)
        state = self._host.AGENT_GATEWAY._find_user_skill(target_name)  # noqa: SLF001 - projection response mirrors runtime state.
        return {
            "name": target_name,
            "path": str(target_dir / "SKILL.md"),
            "enabled": bool(enabled),
            "supportFiles": projected_support,
            "skill": state,
        }

    def _impl_projected_skill_name(self, manifest: dict[str, Any]) -> str:
        target_name = str(manifest.get("skill_name") or manifest.get("skillName") or manifest.get("id") or "").strip()
        target_name = re.sub(r"[^a-z0-9_.-]+", "-", target_name.lower()).strip("-._")
        return target_name

    def _impl_set_projected_skill_enabled(self, manifest: dict[str, Any], enabled: bool) -> dict[str, Any]:
        target_name = self._host._projected_skill_name(manifest)
        if not target_name:
            return {"ok": True, "skipped": True, "reason": "manifest has no projected skill name"}
        target_dir = self._host.AGENT_GATEWAY.user_skills_dir / target_name
        skill_file = target_dir / "SKILL.md"
        if not skill_file.is_file():
            return {"ok": True, "name": target_name, "missing": True}
        if self._host._skill_projection_path_is_link_like(target_dir) or self._host._skill_projection_path_is_link_like(skill_file):
            raise RuntimeError(f"Refusing to update linked projected skill state: {target_name}")
        self._host._write_projected_skill_state(target_dir, enabled)
        return {"ok": True, "name": target_name, "skill": self._host.AGENT_GATEWAY._find_user_skill(target_name)}  # noqa: SLF001

    @contextmanager
    def _impl_delete_projected_skill_transaction(self, manifest: dict[str, Any]) -> Any:
        target_name = self._host._projected_skill_name(manifest)
        if not target_name:
            yield {"ok": True, "skipped": True, "reason": "manifest has no projected skill name"}
            return
        target_dir = self._host.AGENT_GATEWAY.user_skills_dir / target_name
        if not os.path.lexists(target_dir):
            yield {"ok": True, "name": target_name, "missing": True}
            return
        if self._host._skill_projection_path_is_link_like(target_dir) or not target_dir.is_dir():
            raise RuntimeError(f"Refusing to remove unsafe projected skill directory: {target_name}")
        staging_root = self._host.AGENT_GATEWAY.user_skills_dir / ".package-projection-staging"
        if os.path.lexists(staging_root) and (
            self._host._skill_projection_path_is_link_like(staging_root) or not staging_root.is_dir()
        ):
            raise RuntimeError(f"Refusing to use unsafe skill projection staging root: {staging_root}")
        staging_root.mkdir(parents=True, exist_ok=True)
        isolated_dir = staging_root / f"{target_name}.{secrets.token_hex(16)}.removed"
        os.replace(target_dir, isolated_dir)
        try:
            yield {"ok": True, "name": target_name, "deleted": target_name}
        except Exception:
            try:
                if os.path.lexists(target_dir):
                    raise RuntimeError(f"Projected skill path was recreated during rollback: {target_name}")
                os.replace(isolated_dir, target_dir)
            except Exception as restore_exc:
                raise RuntimeError(
                    f"Projected skill removal failed and prior projection could not be restored: {target_name}"
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
