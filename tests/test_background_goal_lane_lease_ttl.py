"""Dead lease holders must not hold lane capacity until restart."""
from background_goal_runtime import LEASE_TTL_SECONDS, RuntimeLaneBudget


def test_expired_leases_are_retired_on_acquire() -> None:
    now = {"value": 1000.0}
    budget = RuntimeLaneBudget(total_limit=1, background_limit=1, clock=lambda: now["value"])
    assert budget.acquire("background", "dead-holder") is True
    # Capacity is full for a second caller.
    assert budget.acquire_result("background", "second") == "capacity"
    # The holder died; after a full TTL its lease retires on the next acquire.
    now["value"] += LEASE_TTL_SECONDS + 1.0
    assert budget.acquire("background", "second") is True
    # A live lease within the TTL is still duplicate-protected.
    assert budget.acquire_result("background", "second") == "duplicate"
