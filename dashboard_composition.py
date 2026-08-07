from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class DashboardCompositionContext:
    """Late-bound access to the dashboard facade's core runtime owners.

    The 1.5 strangler keeps ``dashboard_server`` globals as compatibility and
    monkeypatch seams while domains move behind narrower modules.  Providers
    are therefore resolved at access time: this context must never capture a
    second state object or bypass a facade replacement made by a test/host.

    This object owns no process, file handle, task, or communication endpoint;
    those lifecycles remain with the existing facade owners.
    """

    dashboard_state: Callable[[], Any]
    runtime_state: Callable[[], Any]
    event_bus: Callable[[], Any]
    agent_gateway: Callable[[], Any]
