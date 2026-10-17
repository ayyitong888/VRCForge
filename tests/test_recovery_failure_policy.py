from __future__ import annotations

from test_recovery_safety_scope import scoped_incident


def _witness_ref(project, *, identity="c" * 32, tamper=False, dirty=None, scenes=None, active="Assets/Main.unity"):
    import hashlib,json,uuid
    relative = "Library/VRCForge/CheckpointEvidence/" + uuid.uuid4().hex + ".json"
    path=project/relative;path.parent.mkdir(parents=True,exist_ok=True)
    dirty_rows=[{"assetPath":name,"instanceId":1,"type":"Fixture","dirty":True,"dirtyCount":1,
                 "serializedState":value,"serializedFingerprint":hashlib.sha256(str(value).encode()).hexdigest(),"serializedComplete":True}
                for name,value in (dirty or {}).items()]
    scene_rows=scenes if scenes is not None else [{"handle":1,"path":active,"name":"Main","active":True,"loaded":True,"dirty":False,
             "serializedComplete":True,"topologyFingerprint":"b"*64}]
    payload=[{"schema":"vrcforge.recovery_witness.v1","coreIdentity":identity,"coreInstanceId":identity,
              "unityProcessId":123,"unityProcessStartedAtUtc":"2026-10-17T00:00:00Z",
              "projectPathDigest":hashlib.sha256(project.resolve().as_posix().encode()).hexdigest(),
              "complete":True,"serializationErrors":[],"loadedScenes":scene_rows,"dirtyAssets":dirty_rows}]
    raw=json.dumps(payload,separators=(",",":")).encode();path.write_bytes(raw)
    digest="0"*64 if tamper else hashlib.sha256(raw).hexdigest()
    return {"reference":relative,"sha256":digest,"byteCount":len(raw),"objectCount":1,"summary":{"complete":True}}


def _probe(project=None, *, identity="c" * 32, dirty=None, scenes=None, active="Assets/Main.unity"):
    if project is None:
        return {"ok":True,"unityProcessId":123,"dirtyAssetPaths":[],"loadedScenes":[],"activeScenePath":active}
    ref=_witness_ref(project,identity=identity,dirty=dirty,scenes=scenes,active=active)
    return {"ok":True,"recoveryEvidenceOnly":True,"recoveryEvidenceComplete":True,
            "recoveryEvidenceBefore":ref,"recoveryEvidenceAfter":ref}


def _restore_with_handler(tmp_path, handler):
    gateway, project, _, checkpoint, recovery = scoped_incident(tmp_path)
    gateway.checkpoint_recovery._append_checkpoint({
        **checkpoint,
        "createdAt": "2026-10-17T00:00:00Z",
        "targetTool": "vrcforge_manage_fx_animator",
    })
    service = gateway.checkpoint_recovery
    service.checkpoint_restore_prepare_handler = handler
    service.checkpoint_restore_handler = lambda *_: {"ok": False, "error": "reload failed"}
    result = service.restore_checkpoint({
        "checkpointId": checkpoint["id"],
        "recoveryId": recovery["id"],
        "confirmRestore": True,
    })
    current = service._select_apply_recovery({"recoveryId": recovery["id"]}, include_resolved=True)
    return service, result, current


def test_proven_zero_effect_failure_does_not_consume_attempt(tmp_path):
    calls = []

    def prepare(_project, **kwargs):
        calls.append(kwargs)
        if kwargs.get("capture_evidence_only"):
            return _probe(_project)
        return {"ok": False, "phase": "prepare_restore", "error": "injected before Core mutation"}

    service, result, current = _restore_with_handler(tmp_path, prepare)
    assert result["restoreFailurePolicy"]["classification"] == "proven_zero_effect"
    assert result["restoreFailurePolicy"]["counted"] is False
    assert result["canRetry"] is True and "文件没动，可以再试" in result["retryMessage"]
    assert result["turnControl"] == {"stopCurrentTurn": True, "automaticRetry": False}
    assert current["restoreAttemptCount"] == 0
    assert current["zeroEffectConsecutiveFailures"] == 1
    assert len([item for item in calls if item.get("capture_evidence_only")]) == 2
    assert service.doctor_restore_diagnostics({"recoveryId": current["id"]})["ok"]


def test_unknown_failure_counts_even_when_ok_false_claims_no_mutation(tmp_path):
    def prepare(_project, **kwargs):
        if kwargs.get("capture_evidence_only"):
            return {"ok": False, "mutationStarted": False}
        return {"ok": False, "mutationStarted": False, "error": "transport ended"}

    _, result, current = _restore_with_handler(tmp_path, prepare)
    assert result["restoreFailurePolicy"]["classification"] == "unknown"
    assert result["canRetry"] is False and "retryMessage" not in result
    assert result["diagnostic"]["phase"] and result["diagnostic"]["exceptionType"]
    assert result["diagnostic"]["message"] and result["diagnostic"]["requestId"]
    assert current["restoreAttemptCount"] == 1
    assert current["zeroEffectConsecutiveFailures"] == 0


def test_changed_disk_or_observed_transaction_counts_incident(tmp_path):
    changed = {"done": False}

    def prepare(project, **kwargs):
        if kwargs.get("capture_evidence_only"):
            return _probe(_project)
        (project / "Assets" / "Recovery.controller").write_text("changed", encoding="utf-8")
        changed["done"] = True
        return {"ok": False, "phase": "prepare_restore", "transaction": {"closedScenes": ["Assets/Main.unity"]}}

    _, result, current = _restore_with_handler(tmp_path, prepare)
    assert changed["done"]
    assert result["restoreFailurePolicy"]["classification"] == "changed"
    assert result["restoreFailurePolicy"]["counted"] is True
    assert current["restoreAttemptCount"] == 1


def test_dirty_asset_only_core_change_counts_incident(tmp_path):
    probe_calls = {"count": 0}

    def prepare(_project, **kwargs):
        if kwargs.get("capture_evidence_only"):
            probe_calls["count"] += 1
            dirty = {"Assets/Avatar.prefab": "before" if probe_calls["count"] == 1 else "after"}
            return _probe(_project,dirty=dirty)
        return {"ok": False, "phase": "prepare_restore", "error": "Core changed dirty asset state"}

    _, result, current = _restore_with_handler(tmp_path, prepare)
    assert probe_calls["count"] == 2
    assert result["restoreFailurePolicy"]["classification"] == "changed"
    assert result["restoreFailurePolicy"]["coreProof"]["dirtyAssetsEqual"] is False
    assert current["restoreAttemptCount"] == 1


def test_empty_project_directory_change_counts_incident(tmp_path):
    from checkpoint_unity_evidence import capture_project_disk_snapshot, capture_core_observation, classify_restore_failure

    project = tmp_path / "project"
    (project / "Assets" / "EmptyFolder").mkdir(parents=True)
    before_disk = capture_project_disk_snapshot(project)
    (project / "Assets" / "EmptyFolder").rmdir()
    after_disk = capture_project_disk_snapshot(project)
    proof = classify_restore_failure(
        before_disk,
        after_disk,
        capture_core_observation(_probe()),
        capture_core_observation(_probe()),
    )
    assert before_disk["digest"] != after_disk["digest"]
    assert proof["classification"] == "changed"
    assert proof["counted"] is True


def test_observed_transaction_counts_even_when_disk_hashes_match(tmp_path):
    from checkpoint_unity_evidence import capture_project_disk_snapshot, capture_core_observation, classify_restore_failure

    before_disk = capture_project_disk_snapshot(tmp_path)
    after_disk = capture_project_disk_snapshot(tmp_path)
    before = capture_core_observation(_probe())
    after = capture_core_observation({**_probe(), "transaction": {"closedScenes": ["Assets/Main.unity"]}})
    proof = classify_restore_failure(before_disk, after_disk, before, after, after)
    assert proof["classification"] == "changed"
    assert proof["counted"] is True


def test_probe_typeerror_is_one_callback_invocation(tmp_path):
    gateway, project, _, checkpoint, _ = scoped_incident(tmp_path)
    calls = []

    def prepare(_project, **kwargs):
        calls.append(kwargs)
        raise TypeError("probe transport failed")

    service = gateway.checkpoint_recovery
    service.checkpoint_restore_prepare_handler = prepare
    result = service._invoke_restore_prepare(
        project,
        checkpoint_files=checkpoint["archiveFiles"],
        capture_evidence_only=True,
        request_id="restore-test",
    )
    assert len(calls) == 1
    assert result["exceptionType"] == "TypeError"
    assert result["requestId"] == "restore-test"


def test_tampered_or_incomplete_witness_is_not_core_proof(tmp_path):
    from checkpoint_unity_evidence import capture_core_observation

    tampered = _witness_ref(tmp_path, tamper=True)
    invalid = capture_core_observation(
        {"recoveryEvidenceBefore": tampered, "recoveryEvidenceComplete": True},
        side="before",
        project_root=tmp_path,
    )
    assert invalid["valid"] is False
    incomplete = _witness_ref(tmp_path, identity="")
    invalid_identity = capture_core_observation(
        {"recoveryEvidenceBefore": incomplete, "recoveryEvidenceComplete": True},
        side="before",
        project_root=tmp_path,
    )
    assert invalid_identity["valid"] is False
    assert capture_core_observation(
        {"recoveryEvidenceBefore": _witness_ref(tmp_path), "recoveryEvidenceComplete": False},
        side="before",
        project_root=tmp_path,
    )["valid"] is False


def test_three_consecutive_zero_effect_failures_block_only_auto_launch(tmp_path):
    gateway, project, _, checkpoint, recovery = scoped_incident(tmp_path)
    gateway.checkpoint_recovery._append_checkpoint({
        **checkpoint,
        "createdAt": "2026-10-17T00:00:00Z",
        "targetTool": "vrcforge_manage_fx_animator",
    })
    service = gateway.checkpoint_recovery

    def prepare(_project, **kwargs):
        if kwargs.get("capture_evidence_only"):
            return _probe(_project)
        return {"ok": False, "phase": "prepare_restore", "error": "injected before Core mutation"}

    service.checkpoint_restore_prepare_handler = prepare
    service.checkpoint_restore_handler = lambda *_: {"ok": False, "error": "reload failed"}
    for expected in (1, 2, 3):
        result = service.restore_checkpoint({
            "checkpointId": checkpoint["id"], "recoveryId": recovery["id"], "confirmRestore": True,
        })
        assert result["restoreFailurePolicy"]["counted"] is False
        current = service._select_apply_recovery({"recoveryId": recovery["id"]})
        assert current["restoreAttemptCount"] == 0
        assert current["zeroEffectConsecutiveFailures"] == expected
    blocked = service.restore_checkpoint({
        "checkpointId": checkpoint["id"], "recoveryId": recovery["id"],
        "confirmRestore": True, "_vrcforge_incident_restore_auto": True,
    })
    assert blocked["errorCode"] == "recovery_auto_launch_blocked"
    assert blocked["requiresExplicitUserConfirmation"] is True
    assert blocked["requiresNewInvocation"] is True
    assert blocked["turnControl"]["automaticRetry"] is False


def test_diagnostic_redacts_paths_and_doctor_keeps_safe_fields(tmp_path):
    from checkpoint_unity_evidence import redact_restore_failure_diagnostic

    diagnostic = redact_restore_failure_diagnostic({
        "phase": "reload", "exceptionType": "ValueError",
        "error": r"secret token=abc C:\Users\someone\project\file.txt",
        "requestId": "req-123",
    }, {"classification": "unknown", "counted": True})
    assert diagnostic["phase"] == "reload"
    assert diagnostic["exceptionType"] == "ValueError"
    assert diagnostic["requestId"] == "req-123"
    assert "C:\\Users" not in diagnostic["message"]
    assert "abc" not in diagnostic["message"]


def test_incident_export_contains_doctor_restore_diagnostics(tmp_path):
    service, result, current = _restore_with_handler(
        tmp_path,
        lambda _project, **kwargs: {"ok": False} if not kwargs.get("capture_evidence_only") else {"ok": False},
    )
    bundle = service.export_interrupted_apply_incident_bundle({"recoveryId": current["id"]})
    assert bundle["ok"]
    assert bundle["bundle"]["doctorRestoreDiagnostics"]["schema"] == "vrcforge.restore_failure_doctor.v1"


def test_weak_summary_without_serialized_witness_never_exempts(tmp_path):
    from checkpoint_unity_evidence import capture_core_observation
    result=capture_core_observation({"ok":False,"unityProcessId":123,"dirtyAssetPaths":[],"scenes":[],"mutationStarted":False},project_root=tmp_path)
    assert result["valid"] is False


def test_failure_summary_is_additive_to_machine_rejection(tmp_path):
    gateway, project, _, checkpoint, _ = scoped_incident(tmp_path)
    raw_error = "currentStateDigest no longer matches local skill state; preview and approve again."
    raw_message = "checkpoint_state_digest_mismatch"
    raw_digest = "a" * 64
    result = gateway.checkpoint_recovery._record_restore_failure_policy(
        checkpoint=checkpoint,
        recovery=None,
        result={
            "ok": False,
            "error": raw_error,
            "message": raw_message,
            "errorCode": "checkpoint_state_digest_mismatch",
            "currentStateDigest": raw_digest,
        },
        before_disk=None,
        tentative_attempt=False,
        request_id="failure-summary-test",
        phase="prepare_restore",
    )

    # Keep machine-readable rejection fields byte-for-byte intact while the
    # Chinese sentence is exposed through the additive failureSummary field.
    assert result["error"] == raw_error
    assert result["message"] == raw_message
    assert result["errorCode"] == "checkpoint_state_digest_mismatch"
    assert result["currentStateDigest"] == raw_digest
    assert result["failureSummary"]
    assert result["failureSummary"] != raw_error


def test_recovery_user_message_preserves_redacted_diagnostic(tmp_path):
    def prepare(project, **kwargs):
        if kwargs.get("capture_evidence_only"):
            return _probe(project)
        raise ValueError("fixture failure at C:/private/avatar.controller")
    _, result, _ = _restore_with_handler(tmp_path, prepare)
    assert result["error"] == "fixture failure at C:/private/avatar.controller"
    assert result["failureSummary"] != result["error"]
    assert "文件没动，可以再试" in result["failureSummary"]
    assert result["diagnostic"]["exceptionType"] == "ValueError"
    assert "private" not in result["diagnostic"]["message"]


def test_native_restore_failure_stops_before_second_provider_call(tmp_path):
    from test_native_runtime_gateway import setup_gateway, run, call
    receipt = {"ok": False, "failureSummary": "恢复准备失败，文件没动，可以再试。",
               "error": "恢复准备失败，文件没动，可以再试。", "message": "恢复准备失败，文件没动，可以再试。",
               "turnControl": {"stopCurrentTurn": True, "automaticRetry": False}}
    gateway, model, _ = setup_gateway(tmp_path, [
        call("restore", "vrcforge_restore_checkpoint", {}),
        lambda _: (_ for _ in ()).throw(AssertionError("Automatic retry/model continuation occurred")),
    ])
    gateway.register_tool("vrcforge_restore_checkpoint", "When to use: restore. When NOT to use: read.",
                          "plan/preview", lambda _: receipt)
    result = run(gateway, tmp_path, message="Restore this checkpoint", exposureLayer="execution")
    assert len(model.requests) == 1
    assert result["plan"]["continueLoop"] is False
    assert result["plan"]["reply"] == receipt["failureSummary"]
    assert result["plan"]["restoreFailure"]["turnControl"]["automaticRetry"] is False
