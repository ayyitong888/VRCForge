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
    assert!(value["blockers"].as_array().unwrap().len() >= 18);
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
