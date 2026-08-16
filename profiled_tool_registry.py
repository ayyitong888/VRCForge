"""Capability-profile projections over one shared Agent tool registry.

Profiles decide which tools the model can see.  They do not duplicate handlers
and they are not a filesystem security boundary; callers must still enforce the
capabilities attached to the selected projection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping


ToolHandler = Callable[[dict[str, Any]], Any]
UNITY_PROJECT_ACCESS = "unity_project_access"


class CapabilityProfile(str, Enum):
    GENERAL = "general"
    UNITY_PROJECT = "unity_project"


class ToolSet(str, Enum):
    CORE = "core"
    GENERAL = "general"
    UNITY = "unity"


# Named public markers used by the product contract.
CoreToolSet = ToolSet.CORE
GeneralToolSet = ToolSet.GENERAL
UnityToolSet = ToolSet.UNITY


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    internal_name: str
    handler: ToolHandler
    tool_set: ToolSet
    model_name: str
    capabilities: frozenset[str] = frozenset()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolProjection:
    model_name: str
    internal_name: str
    handler: ToolHandler
    tool_set: ToolSet
    capabilities: frozenset[str]
    metadata: Mapping[str, Any]


class ProfiledToolRegistry:
    """Register each implementation once, then project it by capability profile."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._extra: dict[CapabilityProfile, dict[str, ToolProjection]] = {
            profile: {} for profile in CapabilityProfile
        }

    def register(
        self,
        internal_name: str,
        handler: ToolHandler,
        tool_set: ToolSet,
        *,
        model_name: str | None = None,
        capabilities: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> RegisteredTool:
        internal_name = str(internal_name or "").strip()
        if not internal_name or not callable(handler):
            raise ValueError("tool name and callable handler are required")
        if internal_name in self._tools:
            raise ValueError(f"tool already registered: {internal_name}")
        tool_set = ToolSet(tool_set)
        projected_name = str(model_name or _default_model_name(internal_name, tool_set)).strip()
        if not projected_name:
            raise ValueError("model tool name is required")
        effective_capabilities = frozenset(str(item).strip() for item in capabilities if str(item).strip())
        if tool_set is ToolSet.UNITY:
            effective_capabilities = effective_capabilities | {UNITY_PROJECT_ACCESS}
        registered = RegisteredTool(
            internal_name=internal_name,
            handler=handler,
            tool_set=tool_set,
            model_name=projected_name,
            capabilities=effective_capabilities,
            metadata=dict(metadata or {}),
        )
        self._assert_model_name_available(registered)
        self._tools[internal_name] = registered
        return registered

    def add_projection(
        self,
        internal_name: str,
        *,
        profile: CapabilityProfile,
        model_name: str,
        capabilities: Iterable[str] = (),
    ) -> ToolProjection:
        """Add an alias without registering or copying another implementation."""

        registered = self._tools.get(str(internal_name or "").strip())
        if registered is None:
            raise KeyError(f"unknown internal tool: {internal_name}")
        profile = CapabilityProfile(profile)
        model_name = str(model_name or "").strip()
        if not model_name or model_name in {item.model_name for item in self.project(profile)}:
            raise ValueError(f"model tool already registered for {profile.value}: {model_name}")
        projection = ToolProjection(
            model_name=model_name,
            internal_name=registered.internal_name,
            handler=registered.handler,
            tool_set=registered.tool_set,
            capabilities=frozenset(
                str(item).strip() for item in capabilities if str(item).strip()
            ),
            metadata=registered.metadata,
        )
        self._extra[profile][model_name] = projection
        return projection

    def add_unity_shell(self, internal_shell_name: str) -> ToolProjection:
        return self.add_projection(
            internal_shell_name,
            profile=CapabilityProfile.UNITY_PROJECT,
            model_name="unity_shell",
            capabilities=(UNITY_PROJECT_ACCESS,),
        )

    def project(self, profile: CapabilityProfile) -> tuple[ToolProjection, ...]:
        profile = CapabilityProfile(profile)
        included = {ToolSet.CORE, ToolSet.GENERAL}
        if profile is CapabilityProfile.UNITY_PROJECT:
            included.add(ToolSet.UNITY)
        projected = [
            ToolProjection(
                model_name=tool.model_name,
                internal_name=tool.internal_name,
                handler=tool.handler,
                tool_set=tool.tool_set,
                capabilities=tool.capabilities,
                metadata=tool.metadata,
            )
            for tool in self._tools.values()
            if tool.tool_set in included
        ]
        projected.extend(self._extra[profile].values())
        projected.sort(key=lambda item: item.model_name)
        return tuple(projected)

    def resolve(self, profile: CapabilityProfile, model_name: str) -> ToolProjection | None:
        requested = str(model_name or "").strip()
        return next((item for item in self.project(profile) if item.model_name == requested), None)

    def registered(self, internal_name: str) -> RegisteredTool | None:
        return self._tools.get(str(internal_name or "").strip())

    def _assert_model_name_available(self, candidate: RegisteredTool) -> None:
        profiles = (
            (CapabilityProfile.GENERAL, CapabilityProfile.UNITY_PROJECT)
            if candidate.tool_set in {ToolSet.CORE, ToolSet.GENERAL}
            else (CapabilityProfile.UNITY_PROJECT,)
        )
        for profile in profiles:
            if any(item.model_name == candidate.model_name for item in self.project(profile)):
                raise ValueError(
                    f"model tool already registered for {profile.value}: {candidate.model_name}"
                )


def _default_model_name(internal_name: str, tool_set: ToolSet) -> str:
    base = internal_name.removeprefix("vrcforge_")
    if tool_set is not ToolSet.UNITY:
        return base
    if base.startswith("unity_"):
        return base
    return f"unity_{base}"


__all__ = [
    "CapabilityProfile",
    "CoreToolSet",
    "GeneralToolSet",
    "ProfiledToolRegistry",
    "RegisteredTool",
    "ToolProjection",
    "ToolSet",
    "UNITY_PROJECT_ACCESS",
    "UnityToolSet",
]
