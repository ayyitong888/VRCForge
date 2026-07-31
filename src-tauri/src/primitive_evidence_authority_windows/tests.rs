use super::*;

#[test]
fn layout_is_fixed_below_machine_roots() {
    let layout =
        AuthorityLayout::from_roots(Path::new(r"C:\Program Files"), Path::new(r"C:\ProgramData"))
            .expect("absolute machine roots should be accepted");
    assert_eq!(layout.binary_anchor(), Path::new(r"C:\Program Files"));
    assert_eq!(layout.state_anchor(), Path::new(r"C:\ProgramData"));
    assert_eq!(
        layout.binary_base(),
        Path::new(r"C:\Program Files\VRCForgeEvidenceAuthority")
    );
    assert_eq!(
        layout.state_base(),
        Path::new(r"C:\ProgramData\VRCForgeEvidenceAuthority")
    );
    assert_eq!(
        layout.binary_root(),
        Path::new(r"C:\Program Files\VRCForgeEvidenceAuthority\v1")
    );
    assert_eq!(
        layout.state_root(),
        Path::new(r"C:\ProgramData\VRCForgeEvidenceAuthority\v1")
    );
    assert_eq!(
        layout
            .controller_executable_for_generation(&[0x24; 32])
            .unwrap(),
        PathBuf::from(format!(
            r"C:\Program Files\VRCForgeEvidenceAuthority\v1\generations\{}\vrcforge_primitive_evidence_controller.exe",
            "24".repeat(32)
        ))
    );
    assert_eq!(
        layout
            .service_executable_for_generation(&[0x24; 32])
            .unwrap(),
        PathBuf::from(format!(
            r"C:\Program Files\VRCForgeEvidenceAuthority\v1\generations\{}\vrcforge_primitive_evidence_service.exe",
            "24".repeat(32)
        ))
    );
    assert_eq!(
        layout
            .install_helper_executable_for_generation(&[0x24; 32])
            .unwrap(),
        PathBuf::from(format!(
            r"C:\Program Files\VRCForgeEvidenceAuthority\v1\generations\{}\vrcforge_primitive_evidence_install_helper.exe",
            "24".repeat(32)
        ))
    );
    assert_eq!(
        layout.service_command_for_generation(&[0x24; 32]).unwrap(),
        format!(
            r#""C:\Program Files\VRCForgeEvidenceAuthority\v1\generations\{}\vrcforge_primitive_evidence_service.exe" --service"#,
            "24".repeat(32)
        )
    );
    assert_eq!(
        layout.generation_state_root(&[0x24; 32]).unwrap(),
        PathBuf::from(format!(
            r"C:\ProgramData\VRCForgeEvidenceAuthority\v1\generations\{}",
            "24".repeat(32)
        ))
    );
    let capsule = [0x35; 32];
    let capsule_hex = "35".repeat(32);
    assert_eq!(
        layout
            .maintenance_worker_source_stage_root(&capsule)
            .unwrap(),
        PathBuf::from(format!(
            r"C:\ProgramData\VRCForgeEvidenceAuthority\v1\maintenance\{capsule_hex}\stage.{capsule_hex}"
        ))
    );
    assert_eq!(
        layout
            .maintenance_worker_source_identity_ledger_file(&capsule)
            .unwrap(),
        PathBuf::from(format!(
            r"C:\ProgramData\VRCForgeEvidenceAuthority\v1\maintenance\{capsule_hex}\stage.{capsule_hex}\source-identities.json"
        ))
    );
    assert_eq!(
        layout
            .maintenance_worker_source_staging_receipt_file(&capsule)
            .unwrap(),
        PathBuf::from(format!(
            r"C:\ProgramData\VRCForgeEvidenceAuthority\v1\maintenance\{capsule_hex}\source-staging-receipt.json"
        ))
    );
    assert_eq!(
        layout.candidate_activation_root(),
        PathBuf::from(r"C:\ProgramData\VRCForgeEvidenceAuthority\v1\candidate-activation")
    );
    assert_eq!(
        layout.worker_nonce_root(),
        PathBuf::from(r"C:\ProgramData\VRCForgeEvidenceAuthority\v1\worker-nonce-receipts")
    );
    assert_eq!(
        layout.candidate_consumption_root(),
        PathBuf::from(
            r"C:\ProgramData\VRCForgeEvidenceAuthority\v1\candidate-consumption-tombstones"
        )
    );
    assert_eq!(
        layout.finalizer_commits_root(),
        PathBuf::from(r"C:\ProgramData\VRCForgeEvidenceAuthority\v1\finalizer-commits")
    );
    assert_eq!(
        layout
            .ledger_anchor_file_for_generation(&[0x24; 32])
            .unwrap(),
        PathBuf::from(format!(
            r"C:\ProgramData\VRCForgeEvidenceAuthority\v1\generations\{}\ledger.bin.anchor",
            "24".repeat(32)
        ))
    );
    assert_eq!(
        layout
            .controller_executable_for_generation(&[0; 32])
            .unwrap_err()
            .code(),
        "authority_generation_digest_invalid"
    );
}

#[test]
fn layout_rejects_relative_and_traversing_roots() {
    assert_eq!(
        AuthorityLayout::from_roots(Path::new("Program Files"), Path::new(r"C:\ProgramData"))
            .unwrap_err()
            .code(),
        "authority_layout_root_invalid"
    );
    assert_eq!(
        AuthorityLayout::from_roots(
            Path::new(r"C:\safe\..\Program Files"),
            Path::new(r"C:\ProgramData")
        )
        .unwrap_err()
        .code(),
        "authority_layout_root_invalid"
    );
}

#[test]
fn plan_is_non_mutating_and_never_ready() {
    let layout =
        AuthorityLayout::from_roots(Path::new(r"C:\Program Files"), Path::new(r"C:\ProgramData"))
            .unwrap();
    let value = serde_json::to_value(build_install_plan(&layout)).unwrap();
    assert_eq!(value["mutationSupported"], false);
    assert_eq!(value["trustedBoundaryReady"], false);
    assert_eq!(value["candidatePayloadIncludesAuthority"], false);
    assert_eq!(value["serviceStart"], "demand");
    assert_eq!(
        value["serviceSecuritySddl"],
        AUTHORITY_SERVICE_SECURITY_SDDL
    );
    assert_eq!(value["serviceSidType"], "restricted");
    assert_eq!(
        value["generationPathPolicy"],
        "authority-generation-sha256-parent-create-new-never-reuse"
    );
    assert_eq!(value["layout"]["binaryAnchor"], r"C:\Program Files");
    assert_eq!(value["layout"]["stateAnchor"], r"C:\ProgramData");
    assert_eq!(
        value["layout"]["binaryBase"],
        r"C:\Program Files\VRCForgeEvidenceAuthority"
    );
    assert_eq!(
        value["layout"]["stateBase"],
        r"C:\ProgramData\VRCForgeEvidenceAuthority"
    );
    assert_eq!(
        value["layout"]["binaryVersionRoot"],
        r"C:\Program Files\VRCForgeEvidenceAuthority\v1"
    );
    assert_eq!(
        value["layout"]["stateVersionRoot"],
        r"C:\ProgramData\VRCForgeEvidenceAuthority\v1"
    );
    assert_eq!(
        value["layout"]["controllerExecutablePattern"],
        r"C:\Program Files\VRCForgeEvidenceAuthority\v1\generations\{authority-generation-sha256-lower}\vrcforge_primitive_evidence_controller.exe"
    );
    assert_eq!(
        value["layout"]["serviceExecutablePattern"],
        r"C:\Program Files\VRCForgeEvidenceAuthority\v1\generations\{authority-generation-sha256-lower}\vrcforge_primitive_evidence_service.exe"
    );
    assert_eq!(
        value["layout"]["installHelperExecutablePattern"],
        r"C:\Program Files\VRCForgeEvidenceAuthority\v1\generations\{authority-generation-sha256-lower}\vrcforge_primitive_evidence_install_helper.exe"
    );
    assert_eq!(
        value["layout"]["generationStateRootPattern"],
        r"C:\ProgramData\VRCForgeEvidenceAuthority\v1\generations\{authority-generation-sha256-lower}"
    );
    for forbidden in [
        "serviceExecutable",
        "controllerExecutable",
        "installHelperExecutable",
        "controllerPathPolicy",
    ] {
        assert!(value["layout"].get(forbidden).is_none());
        assert!(value.get(forbidden).is_none());
    }
    let blockers = value["blockers"].as_array().unwrap();
    assert_eq!(blockers.len(), 17);
    assert!(blockers
        .iter()
        .any(|value| value == "process_supervision_not_implemented"));
    assert!(blockers
        .iter()
        .any(|value| value == "private_finalization_not_implemented"));
}

#[test]
fn service_command_readback_binds_generation_path_and_exact_arguments() {
    let expected = Path::new(
        r"C:\Program Files\VRCForgeEvidenceAuthority\v1\generations\0123\vrcforge_primitive_evidence_service.exe",
    );
    assert_eq!(
        compare_service_command(
            r#""c:\program files\vrcforgeevidenceauthority\v1\generations\0123\vrcforge_primitive_evidence_service.exe" --service"#,
            expected,
        ),
        (true, true)
    );
    assert_eq!(
        compare_service_command(
            r#""C:\Program Files\VRCForgeEvidenceAuthority\v1\generations\9999\vrcforge_primitive_evidence_service.exe" --service"#,
            expected,
        ),
        (false, true)
    );
    assert_eq!(
        compare_service_command(
            r#""C:\Program Files\VRCForgeEvidenceAuthority\v1\generations\0123\vrcforge_primitive_evidence_service.exe" --SERVICE"#,
            expected,
        ),
        (true, false)
    );
}

#[test]
fn unbound_readback_is_explicitly_diagnostic_only() {
    let diagnostic = serde_json::to_value(AuthorityReadback::absent(
        "authority_service_not_installed",
        None,
    ))
    .unwrap();
    assert_eq!(diagnostic["expectedGeneration"], serde_json::Value::Null);
    assert_eq!(diagnostic["generationBound"], false);
    assert_eq!(diagnostic["diagnosticOnly"], true);
    assert_eq!(diagnostic["trustedBoundaryReady"], false);
    assert_eq!(diagnostic["protectedReadbackComplete"], false);
    for field in [
        "serviceDaclExact",
        "runningProcessIdentityExact",
        "runningImagePathExact",
        "runningImageFileIdentityExact",
        "runningImageSha256Exact",
        "runningImageGenerationHandshakeExact",
        "controllerLaunchReceiptExact",
        "binaryAnchorChainExact",
        "stateAnchorChainExact",
        "generationPayloadsExact",
        "signingKeyExact",
        "ledgerExact",
        "trustManifestExact",
        "activationManifestExact",
        "retirementStateExact",
        "recoveryStateExact",
    ] {
        assert_eq!(diagnostic[field], false, "{field} must fail closed");
    }
    assert!(diagnostic["blockers"]
        .as_array()
        .unwrap()
        .iter()
        .any(|value| value == "authority_generation_required_for_readback"));
    for blocker in PERMANENT_BLOCKERS {
        assert!(diagnostic["blockers"]
            .as_array()
            .unwrap()
            .iter()
            .any(|value| value == blocker));
    }

    let generation = [0x5a; 32];
    let bound = serde_json::to_value(AuthorityReadback::absent(
        "authority_service_not_installed",
        Some(&generation),
    ))
    .unwrap();
    assert_eq!(bound["expectedGeneration"], "5a".repeat(32));
    assert_eq!(bound["generationBound"], true);
    assert_eq!(bound["diagnosticOnly"], false);
    assert!(!bound["blockers"]
        .as_array()
        .unwrap()
        .iter()
        .any(|value| value == "authority_generation_required_for_readback"));
}

#[test]
fn candidate_and_committed_service_states_have_separate_exact_readbacks() {
    let mut readback = AuthorityReadback::absent("test", Some(&[0x31; 32]));
    readback.service_installed = true;
    readback.service_binary_path_exact = true;
    readback.service_executable_path_exact = true;
    readback.service_arguments_exact = true;
    readback.service_account_exact = true;
    readback.service_type_exact = true;
    readback.service_start_exact = true;
    readback.service_error_control_exact = true;
    readback.service_dacl_exact = true;
    readback.service_sid_restricted = true;
    readback.required_privileges_exact = true;
    readback.service_current_state = SERVICE_STATE_START_PENDING;
    readback.observed_service_process_id = Some(77);

    assert!(readback.candidate_service_configuration_exact_for_start_pending_process(77));
    assert!(!readback.candidate_service_configuration_exact_for_start_pending_process(0));
    assert!(!readback.candidate_service_configuration_exact_for_start_pending_process(78));
    assert!(!readback.bootstrap_service_configuration_exact_for_process(77));
    assert!(!readback.candidate_service_configuration_exact_for_stopped_success());

    readback.service_dacl_exact = false;
    assert!(!readback.candidate_service_configuration_exact_for_start_pending_process(77));
    readback.service_dacl_exact = true;

    readback.service_current_state = SERVICE_STATE_STOPPED;
    readback.observed_service_process_id = None;
    readback.service_win32_exit_code = 0;
    readback.service_specific_exit_code = 0;
    assert!(readback.candidate_service_configuration_exact_for_stopped_success());
    readback.service_win32_exit_code = 1;
    assert!(!readback.candidate_service_configuration_exact_for_stopped_success());
    readback.service_win32_exit_code = 0;
    readback.service_specific_exit_code = 1;
    assert!(!readback.candidate_service_configuration_exact_for_stopped_success());
    readback.service_specific_exit_code = 0;

    readback.service_current_state = 4;
    readback.service_running = true;
    readback.running_process_id = Some(77);
    assert!(!readback.candidate_service_configuration_exact_for_start_pending_process(77));
    assert!(readback.bootstrap_service_configuration_exact_for_process(77));

    readback.service_dacl_exact = false;
    assert!(!readback.bootstrap_service_configuration_exact_for_process(77));

    let serialized = serde_json::to_value(&readback).unwrap();
    assert!(serialized.get("serviceCurrentState").is_none());
    assert!(serialized.get("observedServiceProcessId").is_none());
}

#[test]
fn pipe_policy_excludes_unprivileged_principals_and_create_instance_access() {
    assert!(AUTHORITY_PIPE_SDDL.contains(";;;SY"));
    assert!(AUTHORITY_PIPE_SDDL.contains(";;;BA"));
    assert!(AUTHORITY_PIPE_SDDL.contains(";;;HI"));
    for forbidden in [";;;WD", ";;;AU", ";;;BU", "(A;;GA;;;BA)"] {
        assert!(!AUTHORITY_PIPE_SDDL.contains(forbidden));
    }
    assert!(AUTHORITY_PIPE_SDDL.contains("0x0012019b"));
}

#[test]
fn candidate_service_policy_grants_the_worker_only_query_and_start() {
    assert!(AUTHORITY_SERVICE_SECURITY_SDDL.contains("(A;;0x000f01ff;;;SY)"));
    assert!(AUTHORITY_SERVICE_SECURITY_SDDL.contains("(A;;0x00070037;;;BA)"));
    assert!(AUTHORITY_SERVICE_SECURITY_SDDL.contains(
        "(A;;0x00020015;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)"
    ));
    for forbidden in ["(A;;FA;;;BA)", ";;;WD)", ";;;AU)", ";;;BU)"] {
        assert!(!AUTHORITY_SERVICE_SECURITY_SDDL.contains(forbidden));
    }
}

#[test]
fn privilege_set_is_exact_and_stable() {
    assert_eq!(
        AUTHORITY_REQUIRED_PRIVILEGES,
        [
            "SeAssignPrimaryTokenPrivilege",
            "SeIncreaseQuotaPrivilege",
            "SeTcbPrivilege",
        ]
    );
}
