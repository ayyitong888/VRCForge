from request_usage_reconciliation import reconcile_request_usage


def _usage(request_count, *, input_tokens=0, output_tokens=0, total_tokens=0, **extra):
    return {
        "requestCount": request_count,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": total_tokens,
        "exact": True,
        **extra,
    }


def test_reconciles_same_task_cumulative_usage_to_exact_delta():
    result = reconcile_request_usage(
        chain_id="task-1",
        expected_chain_id="task-1",
        parent_turn_id="turn-1",
        expected_parent_turn_id="turn-1",
        turn_id="turn-2",
        parent_request_count=20,
        parent_usage=_usage(20, input_tokens=100, output_tokens=20, total_tokens=120),
        observed_delta=_usage(3, input_tokens=30, output_tokens=6, total_tokens=36),
        cumulative_usage=_usage(23, input_tokens=130, output_tokens=26, total_tokens=156),
    )

    assert result["status"] == "verified"
    assert result["delta"] == {
        "requestCount": 3,
        "inputTokens": 30,
        "outputTokens": 6,
        "totalTokens": 36,
    }
    assert len(result["parentSnapshotSha256"]) == 64


def test_reconciles_cache_subset_without_filling_unknown_with_zero():
    result = reconcile_request_usage(
        chain_id="task-1",
        expected_chain_id="task-1",
        parent_turn_id="turn-1",
        expected_parent_turn_id="turn-1",
        turn_id="turn-2",
        parent_request_count=20,
        parent_usage=_usage(20, input_tokens=100, output_tokens=20, total_tokens=120, cacheReadTokens=100),
        observed_delta=_usage(3, input_tokens=30, output_tokens=6, total_tokens=36, cacheReadTokens=28),
        cumulative_usage=_usage(23, input_tokens=130, output_tokens=26, total_tokens=156, cacheReadTokens=128),
    )

    assert result["status"] == "verified"
    assert result["delta"]["cacheReadTokens"] == 28
    assert "cacheWriteTokens" not in result["delta"]


def test_parent_mismatch_reset_and_unknown_are_not_clamped_to_zero():
    mismatch = reconcile_request_usage(
        chain_id="task-1",
        expected_chain_id="other-task",
        parent_turn_id="turn-1",
        expected_parent_turn_id="turn-1",
        turn_id="turn-2",
        parent_request_count=20,
        parent_usage=_usage(20, input_tokens=100, output_tokens=20, total_tokens=120),
        observed_delta=_usage(3, input_tokens=30, output_tokens=6, total_tokens=36),
        cumulative_usage=_usage(23, input_tokens=130, output_tokens=26, total_tokens=156),
    )
    reset = reconcile_request_usage(
        chain_id="task-1",
        expected_chain_id="task-1",
        parent_turn_id="turn-1",
        expected_parent_turn_id="turn-1",
        turn_id="turn-2",
        parent_request_count=20,
        parent_usage=_usage(20, input_tokens=100, output_tokens=20, total_tokens=120),
        observed_delta=_usage(0, input_tokens=0, output_tokens=0, total_tokens=0),
        cumulative_usage=_usage(2, input_tokens=10, output_tokens=2, total_tokens=12),
    )
    unknown = reconcile_request_usage(
        chain_id="task-1",
        expected_chain_id="task-1",
        parent_turn_id="turn-1",
        expected_parent_turn_id="turn-1",
        turn_id="turn-2",
        parent_request_count=20,
        parent_usage=_usage(20, input_tokens=100, output_tokens=20, total_tokens=120),
        observed_delta={"requestCount": 3, "exact": False},
        cumulative_usage=_usage(23, input_tokens=130, output_tokens=26, total_tokens=156),
    )

    assert mismatch["status"] == "unknown"
    assert mismatch["reason"] == "chain_id_mismatch"
    assert reset["status"] == "unknown"
    assert reset["reason"] == "non_monotonic_requestCount"
    assert unknown["status"] == "unknown"
    assert unknown["reason"] == "observed_delta_not_exact"
    assert "delta" not in reset


def test_negative_and_parent_snapshot_mismatch_are_unknown():
    negative = reconcile_request_usage(
        chain_id="task-1",
        expected_chain_id="task-1",
        parent_turn_id="turn-1",
        expected_parent_turn_id="turn-1",
        turn_id="turn-2",
        parent_request_count=-1,
        parent_usage={},
        observed_delta=_usage(1, input_tokens=1, output_tokens=1, total_tokens=2),
        cumulative_usage=_usage(1, input_tokens=1, output_tokens=1, total_tokens=2),
    )
    parent = _usage(20, input_tokens=100, output_tokens=20, total_tokens=120)
    snapshot_mismatch = reconcile_request_usage(
        chain_id="task-1",
        expected_chain_id="task-1",
        parent_turn_id="turn-1",
        expected_parent_turn_id="turn-1",
        turn_id="turn-2",
        parent_request_count=20,
        parent_usage=parent,
        observed_delta=_usage(3, input_tokens=30, output_tokens=6, total_tokens=36),
        cumulative_usage=_usage(23, input_tokens=130, output_tokens=26, total_tokens=156),
        expected_parent_snapshot_sha256="f" * 64,
    )

    assert negative["status"] == "unknown"
    assert negative["reason"] == "negative_parentRequestCount"
    assert snapshot_mismatch["status"] == "unknown"
    assert snapshot_mismatch["reason"] == "parent_snapshot_mismatch"


def test_initial_origin_treats_first_cache_counter_as_delta_from_known_zero():
    result = reconcile_request_usage(
        chain_id="task-origin",
        expected_chain_id="task-origin",
        parent_turn_id="",
        expected_parent_turn_id=None,
        turn_id="turn-1",
        parent_request_count=0,
        parent_usage={},
        observed_delta=_usage(1, input_tokens=10, output_tokens=2, total_tokens=12, cacheReadTokens=7),
        cumulative_usage=_usage(1, input_tokens=10, output_tokens=2, total_tokens=12, cacheReadTokens=7),
    )

    assert result["status"] == "verified"
    assert result["delta"]["cacheReadTokens"] == 7
    assert len(result["cumulativeSnapshotSha256"]) == 64


def test_resumed_legacy_seed_without_snapshot_digest_is_unknown():
    result = reconcile_request_usage(
        chain_id="task-legacy",
        expected_chain_id="task-legacy",
        parent_turn_id="turn-1",
        expected_parent_turn_id="turn-1",
        turn_id="turn-2",
        parent_request_count=20,
        parent_usage=_usage(20, input_tokens=100, output_tokens=20, total_tokens=120),
        observed_delta=_usage(3, input_tokens=30, output_tokens=6, total_tokens=36),
        cumulative_usage=_usage(23, input_tokens=130, output_tokens=26, total_tokens=156),
        expected_parent_snapshot_sha256="",
    )

    assert result["status"] == "unknown"
    assert result["reason"] == "parent_snapshot_missing"


def test_known_partial_cache_coverage_cannot_be_verified_as_exact_delta():
    for coverage in ("cacheUsageComplete", "cacheWriteUsageComplete"):
        result = reconcile_request_usage(
            chain_id="task", expected_chain_id="task", parent_turn_id="", expected_parent_turn_id=None,
            turn_id="turn", parent_request_count=0, parent_usage={},
            observed_delta=_usage(2, input_tokens=100, output_tokens=2, total_tokens=102,
                                  cacheReadTokens=80, cacheWriteTokens=0, **{coverage: False}),
            cumulative_usage=_usage(2, input_tokens=100, output_tokens=2, total_tokens=102,
                                    cacheReadTokens=80, cacheWriteTokens=0, **{coverage: False}),
        )
        assert result["status"] == "unknown"
        assert "delta" not in result
