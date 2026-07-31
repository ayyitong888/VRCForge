use super::*;
use std::path::Path;

fn descriptor(seed: u8) -> AuthorityPayloadDigest {
    AuthorityPayloadDigest::new([seed; 32], 1_000 + u64::from(seed)).unwrap()
}

#[test]
fn protected_manifest_file_is_canonical_typed_and_exactly_digest_bound() {
    let generation = [0x31; 32];
    let (public_key, key_id) = verified_public_key(0x32);
    let ledger_identity = derive_ledger_identity(&generation, &key_id).unwrap();
    let payload = CanonicalUnsignedManifestPayload::Trust {
        generation,
        signer_key_id: key_id,
        signer_public_key_sec1: public_key,
        ledger_identity,
        created_epoch: 1,
        valid: true,
        revoked: false,
    };
    let mut signature = [0u8; 64];
    signature[31] = 1;
    signature[63] = 1;
    let manifest = ProtectedDetachedManifestFile::new(payload, key_id, signature).unwrap();
    let bytes = manifest.canonical_bytes().unwrap();
    let parsed = ProtectedDetachedManifestFile::parse_canonical(&bytes).unwrap();
    assert_eq!(parsed.unsigned_payload().unwrap(), payload);
    assert_eq!(parsed.signature_input().unwrap().signer_key_id, key_id);

    let mut spaced = bytes.clone();
    spaced.push(b'\n');
    assert_eq!(
        ProtectedDetachedManifestFile::parse_canonical(&spaced)
            .unwrap_err()
            .code(),
        "authority_protected_manifest_not_canonical"
    );

    let digest_hex = hex_lower(&parsed.signature_input().unwrap().digest);
    let tampered = String::from_utf8(bytes)
        .unwrap()
        .replace(
            &format!("\"unsignedPayloadSha256\":\"{digest_hex}\""),
            &format!("\"unsignedPayloadSha256\":\"{}\"", "33".repeat(32)),
        )
        .into_bytes();
    assert_eq!(
        ProtectedDetachedManifestFile::parse_canonical(&tampered)
            .unwrap_err()
            .code(),
        "authority_protected_manifest_binding_invalid"
    );
}

#[test]
fn protected_active_head_binds_plan_transaction_epoch_and_previous_head() {
    let head = ProtectedActiveHead::new(
        [0x41; 32],
        [0x42; 32],
        2,
        [0x43; 32],
        [0x44; 32],
        Some([0x45; 32]),
    )
    .unwrap();
    let bytes = head.canonical_bytes().unwrap();
    let parsed = ProtectedActiveHead::parse_canonical(&bytes).unwrap();
    assert_eq!(parsed.generation().unwrap(), [0x41; 32]);
    assert_eq!(parsed.activation_manifest_sha256().unwrap(), [0x42; 32]);
    assert_eq!(parsed.activation_epoch(), 2);
    assert_eq!(parsed.transaction_sha256().unwrap(), [0x43; 32]);
    assert_eq!(parsed.plan_sha256().unwrap(), [0x44; 32]);
    assert_eq!(parsed.previous_head_sha256().unwrap(), Some([0x45; 32]));
    assert_ne!(parsed.digest().unwrap(), [0; 32]);

    let mut unknown: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    unknown["unexpected"] = serde_json::Value::Bool(true);
    assert_eq!(
        ProtectedActiveHead::parse_canonical(&serde_json::to_vec(&unknown).unwrap())
            .unwrap_err()
            .code(),
        "authority_active_head_invalid"
    );
}

fn verified_public_key(seed: u8) -> ([u8; 65], [u8; 32]) {
    let mut public_key = [seed; 65];
    public_key[0] = 0x04;
    let key_id = Sha256::digest(public_key).into();
    (public_key, key_id)
}

fn content(seed: u8) -> AuthorityInstallContent {
    AuthorityInstallContent::new(
        descriptor(seed),
        descriptor(seed + 1),
        descriptor(seed + 2),
        descriptor(seed + 3),
        descriptor(seed + 4),
        descriptor(seed + 5),
    )
    .unwrap()
}

fn layout() -> AuthorityLayout {
    AuthorityLayout::for_test_roots(Path::new(r"C:\Program Files"), Path::new(r"C:\ProgramData"))
        .unwrap()
}

#[test]
fn installed_layout_digest_is_stable_path_bound_and_not_the_maintenance_plan() {
    let first = preview_install(&layout(), content(1)).unwrap();
    let repeated = preview_install(&layout(), content(1)).unwrap();
    let moved_layout = AuthorityLayout::for_test_roots(
        Path::new(r"D:\Program Files"),
        Path::new(r"D:\ProgramData"),
    )
    .unwrap();
    let moved = preview_install(&moved_layout, content(1)).unwrap();

    assert_eq!(
        first.installed_layout_sha256(),
        repeated.installed_layout_sha256()
    );
    assert_ne!(first.installed_layout_sha256(), [0; 32]);
    assert_ne!(
        first.installed_layout_sha256(),
        first.plan_sha256().unwrap()
    );
    assert_ne!(
        first.installed_layout_sha256(),
        moved.installed_layout_sha256()
    );
}

fn sealed_installed_readback(
    preview: &AuthorityMaintenancePreview,
) -> SealedInstalledGenerationReadback {
    let (public_key, key_id) = verified_public_key(4);
    let generation = preview.generation_sha256().unwrap();
    let content = content_from_projection(&preview.content).unwrap();
    let ledger_identity = [9; 32];
    let trust_payload = CanonicalUnsignedManifestPayload::Trust {
        generation,
        signer_key_id: key_id,
        signer_public_key_sec1: public_key,
        ledger_identity,
        created_epoch: 1,
        valid: true,
        revoked: false,
    };
    let trust = detached(trust_payload, key_id);
    let activation_payload = CanonicalUnsignedManifestPayload::Activation {
        generation,
        trust_manifest_sha256: trust.unsigned_payload_sha256,
        signer_key_id: key_id,
        activated_epoch: 1,
        previous_generation: None,
        previous_activation_sha256: None,
        previous_activation_epoch: None,
        valid: true,
        revoked: false,
    };
    let activation = detached(activation_payload, key_id);
    SealedInstalledGenerationReadback {
        generation,
        payload_files: VerifiedPayloadFilesProof {
            service: content.service,
            controller: content.controller,
            install_helper: content.install_helper,
            lifecycle_driver: content.lifecycle_driver,
            bridge_launcher: content.bridge_launcher,
            runtime_source_manifest: content.runtime_source_manifest,
            receipt_sha256: [10; 32],
        },
        key: VerifiedKeyProof {
            signer_key_id: key_id,
            signer_public_key_sec1: public_key,
            receipt_sha256: [11; 32],
        },
        ledger: VerifiedLedgerProof {
            ledger_identity,
            receipt_sha256: [12; 32],
        },
        service_runtime: sealed_service(generation, content.service),
        manifests: RawManifestChainReadback {
            trust,
            activation,
            retirement: None,
            protected_activation_history: Vec::new(),
            observed_heads: vec![VerifiedProtectedActivationHead {
                generation,
                activation_manifest_sha256: activation.unsigned_payload_sha256,
                activation_epoch: 1,
                volume_serial: 25,
                file_id: [26; 16],
                protected_head_receipt_sha256: [27; 32],
            }],
        },
    }
}

fn installed(preview: &AuthorityMaintenancePreview) -> VerifiedInstalledGeneration {
    VerifiedInstalledGeneration::from_sealed_readback(sealed_installed_readback(preview)).unwrap()
}

struct StaticSealedGenerationSource {
    readback: Option<SealedInstalledGenerationReadback>,
    observed_expected: Option<[u8; 32]>,
    error: Option<&'static str>,
}

impl SealedInstalledGenerationSource for StaticSealedGenerationSource {
    fn read_sealed_generation(
        &mut self,
        expected_generation: [u8; 32],
    ) -> Result<SealedInstalledGenerationReadback, AuthorityMaintenanceError> {
        self.observed_expected = Some(expected_generation);
        if let Some(code) = self.error {
            return Err(AuthorityMaintenanceError(code));
        }
        self.readback.take().ok_or(AuthorityMaintenanceError(
            "authority_prior_generation_source_empty",
        ))
    }
}

#[test]
fn prior_generation_source_is_expected_generation_in_and_verified_generation_out() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let expected = preview.generation_sha256().unwrap();
    let mut exact = StaticSealedGenerationSource {
        readback: Some(sealed_installed_readback(&preview)),
        observed_expected: None,
        error: None,
    };
    let verified =
        VerifiedInstalledGeneration::from_expected_sealed_source(&mut exact, expected).unwrap();
    assert_eq!(exact.observed_expected, Some(expected));
    assert_eq!(verified.generation, expected);

    let mut mismatched_readback = sealed_installed_readback(&preview);
    mismatched_readback.generation = [0x77; 32];
    let mut mismatched = StaticSealedGenerationSource {
        readback: Some(mismatched_readback),
        observed_expected: None,
        error: None,
    };
    assert_eq!(
        VerifiedInstalledGeneration::from_expected_sealed_source(&mut mismatched, expected)
            .unwrap_err()
            .code(),
        "authority_prior_generation_source_mismatch"
    );

    let mut never_called = StaticSealedGenerationSource {
        readback: None,
        observed_expected: None,
        error: Some("authority_prior_generation_source_failed"),
    };
    assert_eq!(
        VerifiedInstalledGeneration::from_expected_sealed_source(&mut never_called, [0; 32])
            .unwrap_err()
            .code(),
        "authority_prior_generation_expected_invalid"
    );
    assert_eq!(never_called.observed_expected, None);

    assert_eq!(
        VerifiedInstalledGeneration::from_expected_sealed_source(&mut never_called, expected)
            .unwrap_err()
            .code(),
        "authority_prior_generation_source_failed"
    );
    assert_eq!(never_called.observed_expected, Some(expected));
}

fn detached(
    unsigned_payload: CanonicalUnsignedManifestPayload,
    signer_key_id: [u8; 32],
) -> DetachedManifestReadback {
    DetachedManifestReadback {
        unsigned_payload,
        unsigned_payload_sha256: canonical_unsigned_manifest_digest(&unsigned_payload),
        signature: VerifiedDetachedSignatureProof {
            signer_key_id,
            unsigned_payload_sha256: canonical_unsigned_manifest_digest(&unsigned_payload),
            receipt_sha256: [24; 32],
        },
    }
}

fn replace_current_activation_chain(
    readback: &mut SealedInstalledGenerationReadback,
    history: Vec<DetachedManifestReadback>,
    activation_epoch: u64,
    trust_epoch: u64,
    previous: Option<([u8; 32], [u8; 32], u64)>,
) {
    let trust = detached(
        CanonicalUnsignedManifestPayload::Trust {
            generation: readback.generation,
            signer_key_id: readback.key.signer_key_id,
            signer_public_key_sec1: readback.key.signer_public_key_sec1,
            ledger_identity: readback.ledger.ledger_identity,
            created_epoch: trust_epoch,
            valid: true,
            revoked: false,
        },
        readback.key.signer_key_id,
    );
    let activation = detached(
        CanonicalUnsignedManifestPayload::Activation {
            generation: readback.generation,
            trust_manifest_sha256: trust.unsigned_payload_sha256,
            signer_key_id: readback.key.signer_key_id,
            activated_epoch: activation_epoch,
            previous_generation: previous.map(|value| value.0),
            previous_activation_sha256: previous.map(|value| value.1),
            previous_activation_epoch: previous.map(|value| value.2),
            valid: true,
            revoked: false,
        },
        readback.key.signer_key_id,
    );
    readback.manifests.trust = trust;
    readback.manifests.activation = activation;
    readback.manifests.protected_activation_history = history;
    readback.manifests.observed_heads = vec![VerifiedProtectedActivationHead {
        generation: readback.generation,
        activation_manifest_sha256: activation.unsigned_payload_sha256,
        activation_epoch,
        volume_serial: 25,
        file_id: [26; 16],
        protected_head_receipt_sha256: [27; 32],
    }];
}

fn sealed_service(
    generation: [u8; 32],
    service: AuthorityPayloadDigest,
) -> SealedServiceGenerationReadback {
    SealedServiceGenerationReadback {
        scm: VerifiedScmConfigurationProof {
            generation,
            service_image_sha256: service.sha256,
            receipt_sha256: [13; 32],
        },
        security: VerifiedServiceSecurityProof {
            receipt_sha256: [14; 32],
        },
        process: VerifiedServiceProcessProof {
            process_id: 42,
            process_creation_time: 123_456,
            image_sha256: service.sha256,
            pipe_instance_id: [15; 16],
            held_image_receipt_sha256: [16; 32],
        },
        handshake: VerifiedGenerationHandshakeProof {
            generation,
            pipe_instance_id: [15; 16],
            receipt_sha256: [17; 32],
        },
    }
}

fn held_observation(descriptor: AuthorityPayloadDigest, seed: u8) -> RawHeldPayloadObservation {
    RawHeldPayloadObservation {
        descriptor,
        volume_serial: 100 + u64::from(seed),
        file_id: [seed; 16],
        post_read_descriptor: descriptor,
        post_read_volume_serial: 100 + u64::from(seed),
        post_read_file_id: [seed; 16],
        handle_identity: 200 + u64::from(seed),
        regular_file: true,
        reparse_point: false,
        handle_held: true,
        write_sharing_denied: true,
        delete_sharing_denied: true,
        open_policy_receipt_sha256: [seed.saturating_add(40); 32],
        full_readback_receipt_sha256: [seed.saturating_add(80); 32],
    }
}

fn maintenance_lease(preview: &AuthorityMaintenancePreview) -> VerifiedMaintenanceLease {
    let expected = content_from_projection(&preview.content).unwrap();
    let bootstrap = VerifiedBootstrapHelperIdentity::from_running_helper(
        expected.install_helper,
        RawBootstrapHelperObservation {
            process_id: 77,
            process_creation_time: 9001,
            image_volume_serial: 88,
            image_file_id: [19; 16],
            image_sha256: expected.install_helper.sha256,
            image_byte_length: expected.install_helper.byte_length,
            image_handle_held: true,
            elevated_token: true,
            high_integrity: true,
            local_system: false,
            session_id: 1,
        },
    )
    .unwrap();
    VerifiedMaintenanceLease::for_test(
        preview,
        &expected,
        bootstrap,
        held_observation(expected.service, 21),
        held_observation(expected.controller, 22),
        held_observation(expected.install_helper, 23),
        held_observation(expected.lifecycle_driver, 24),
        held_observation(expected.bridge_launcher, 25),
        held_observation(expected.runtime_source_manifest, 26),
    )
    .unwrap()
}

fn worker_bootstrap_staging(
    capsule: &MaintenanceWorkerCapsule,
) -> (
    MaintenanceWorkerLaunchContract,
    WorkerBootstrapIntentReceipt,
    WorkerBootstrapStagingReceipt,
) {
    let launch = MaintenanceWorkerLaunchContract::new(&layout(), capsule).unwrap();
    let intent = WorkerBootstrapIntentReceipt::new(capsule, &launch).unwrap();
    let capsule_bytes = capsule.canonical_bytes().unwrap();
    let capsule_file_sha256: [u8; 32] = Sha256::digest(&capsule_bytes).into();
    let receipt = WorkerBootstrapStagingReceipt::from_observed(
        capsule,
        &launch,
        501,
        [0xa1; 16],
        502,
        [0xa2; 16],
        WorkerBootstrapStagedFileBinding::from_observed(
            "install-helper",
            "vrcforge_primitive_evidence_install_helper.exe",
            capsule.install_helper_sha256().unwrap(),
            capsule.install_helper_byte_length(),
            501,
            [0xa3; 16],
            worker_bootstrap_file_readback_receipt(
                "install-helper",
                &capsule.install_helper_sha256().unwrap(),
                capsule.install_helper_byte_length(),
                501,
                &[0xa3; 16],
            ),
        ),
        WorkerBootstrapStagedFileBinding::from_observed(
            "capsule",
            "capsule.json",
            capsule_file_sha256,
            capsule_bytes.len() as u64,
            502,
            [0xa5; 16],
            worker_bootstrap_file_readback_receipt(
                "capsule",
                &capsule_file_sha256,
                capsule_bytes.len() as u64,
                502,
                &[0xa5; 16],
            ),
        ),
    )
    .unwrap();
    (launch, intent, receipt)
}

#[test]
fn generation_binds_all_binaries_layout_and_fixed_policy() {
    let base = preview_install(&layout(), content(1)).unwrap();
    for changed in [content(11), content(21), content(31)] {
        assert_ne!(
            base.generation,
            preview_install(&layout(), changed).unwrap().generation
        );
    }
    let other_layout = AuthorityLayout::for_test_roots(
        Path::new(r"D:\Program Files"),
        Path::new(r"D:\ProgramData"),
    )
    .unwrap();
    assert_ne!(
        base.generation,
        preview_install(&other_layout, content(1))
            .unwrap()
            .generation
    );
    assert_eq!(base.policy_sha256.len(), 64);
    assert!(base.prior_generation_readback.is_none());
}

#[test]
fn install_paths_are_generation_addressed_and_create_new_only() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    assert!(preview
        .layout
        .generation_binary_root
        .contains(&preview.generation));
    assert!(preview
        .layout
        .generation_state_root
        .contains(&preview.generation));
    let value = serde_json::to_value(&preview).unwrap();
    let text = value.to_string();
    assert!(!text.contains("sourcePath"));
    assert!(!text.contains("callerCommand"));
    for step in value["steps"].as_array().unwrap() {
        if step["action"].get("neverReuse").is_some() {
            assert_eq!(step["action"]["createNew"], true);
            assert_eq!(step["action"]["neverReuse"], true);
        }
    }
    assert_eq!(preview.steps[0].id, "createDurableJournal");
    assert_eq!(preview.journal.anchor_path, preview.layout.state_anchor);
    assert!(preview
        .layout
        .maintenance_journal
        .starts_with(&preview.layout.state_anchor));
    match &preview.steps[0].action {
        AuthorityMaintenanceAction::CreateDurableJournal {
            anchor_path,
            anchor_source,
            anchor_handle_held,
            anchor_stable_object_identity_required,
            anchor_reparse_points_rejected,
            create_relative_to_anchor_handle,
            preexisting_path_rejected,
            exact_security_required,
            owner_sid,
            ..
        } => {
            assert_eq!(anchor_path, &preview.layout.state_anchor);
            assert_eq!(*anchor_source, "verifiedKnownFolderHandle");
            assert!(*anchor_handle_held);
            assert!(*anchor_stable_object_identity_required);
            assert!(*anchor_reparse_points_rejected);
            assert!(*create_relative_to_anchor_handle);
            assert!(*preexisting_path_rejected);
            assert!(*exact_security_required);
            assert_eq!(*owner_sid, LOCAL_SYSTEM_SID);
        }
        other => panic!("unexpected journal action: {other:?}"),
    }
    assert_eq!(value["automaticExecutionAllowed"], false);
    assert_eq!(value["nativeMutationBackendAvailable"], false);
    assert_eq!(value["trustedBoundaryReady"], false);
}

fn assert_protected_parent_contract(preview: &AuthorityMaintenancePreview) {
    let expected = [
        (
            "ensureBinaryBase",
            preview.layout.binary_base.as_str(),
            preview.layout.binary_anchor.as_str(),
        ),
        (
            "ensureBinaryVersionRoot",
            preview.layout.binary_version_root.as_str(),
            preview.layout.binary_base.as_str(),
        ),
        (
            "ensureBinaryGenerationsRoot",
            preview.layout.binary_generations_root.as_str(),
            preview.layout.binary_version_root.as_str(),
        ),
        (
            "ensureBinaryMaintenanceRoot",
            preview.layout.binary_maintenance_root.as_str(),
            preview.layout.binary_version_root.as_str(),
        ),
        (
            "ensureStateBase",
            preview.layout.state_base.as_str(),
            preview.layout.state_anchor.as_str(),
        ),
        (
            "ensureStateVersionRoot",
            preview.layout.state_version_root.as_str(),
            preview.layout.state_base.as_str(),
        ),
        (
            "ensureStateGenerationsRoot",
            preview.layout.state_generations_root.as_str(),
            preview.layout.state_version_root.as_str(),
        ),
        (
            "ensureStateMaintenanceRoot",
            preview.layout.state_maintenance_root.as_str(),
            preview.layout.state_version_root.as_str(),
        ),
        (
            "ensureCandidateActivationRoot",
            preview.layout.candidate_activation_root.as_str(),
            preview.layout.state_version_root.as_str(),
        ),
        (
            "ensureWorkerNonceRoot",
            preview.layout.worker_nonce_root.as_str(),
            preview.layout.state_version_root.as_str(),
        ),
        (
            "ensureCandidateConsumptionRoot",
            preview.layout.candidate_consumption_root.as_str(),
            preview.layout.state_version_root.as_str(),
        ),
        (
            "ensureActivationsRoot",
            preview.layout.activations_root.as_str(),
            preview.layout.state_version_root.as_str(),
        ),
        (
            "ensureRetirementsRoot",
            preview.layout.retirements_root.as_str(),
            preview.layout.state_version_root.as_str(),
        ),
        (
            "ensureRecoveriesRoot",
            preview.layout.recoveries_root.as_str(),
            preview.layout.state_version_root.as_str(),
        ),
    ];
    assert!(preview.steps.len() >= expected.len());
    for (step, (expected_id, expected_path, expected_parent)) in
        preview.steps.iter().skip(1).zip(expected.into_iter())
    {
        assert_eq!(step.id, expected_id);
        match &step.action {
            AuthorityMaintenanceAction::EnsureProtectedDirectory {
                path,
                parent_path,
                security_sddl,
                owner_sid,
                create_if_missing,
                accept_existing,
                exact_security_required,
                reject_reparse_points,
                stable_object_identity_required,
                open_parent_by_handle,
                create_relative_to_parent_handle,
                retain_verified_handle,
            } => {
                assert_eq!(path, expected_path);
                assert_eq!(parent_path, expected_parent);
                assert!(!security_sddl.is_empty());
                assert_eq!(*owner_sid, LOCAL_SYSTEM_SID);
                assert!(create_if_missing);
                assert!(accept_existing);
                assert!(exact_security_required);
                assert!(reject_reparse_points);
                assert!(stable_object_identity_required);
                assert!(open_parent_by_handle);
                assert!(create_relative_to_parent_handle);
                assert!(retain_verified_handle);
                assert_eq!(
                    step.failed_apply_cleanup,
                    AuthorityRollbackAction::RestoreProtectedDirectoryState { path: path.clone() }
                );
                assert_eq!(step.rollback, AuthorityRollbackAction::None);
            }
            other => panic!("unexpected protected parent action: {other:?}"),
        }
    }
    assert_eq!(
        preview.fixed_policy.protected_directory_owner_sid,
        LOCAL_SYSTEM_SID
    );
    assert!(
        preview
            .fixed_policy
            .protected_directory_exact_security_required
    );
    assert!(
        preview
            .fixed_policy
            .protected_directory_reparse_points_rejected
    );
    assert!(
        preview
            .fixed_policy
            .protected_directory_stable_object_identity_required
    );
    assert!(
        preview
            .fixed_policy
            .protected_directory_parent_opened_by_handle
    );
    assert!(
        preview
            .fixed_policy
            .protected_directory_child_created_relative_to_handle
    );
    assert!(
        preview
            .fixed_policy
            .protected_directory_handle_retained_through_transaction
    );
}

#[test]
fn install_update_and_retire_require_the_same_protected_parent_contract() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let prior = installed(&install);
    let update = preview_update(&layout(), content(10), prior.clone()).unwrap();
    let retire = preview_retire(&layout(), prior).unwrap();
    for preview in [&install, &update, &retire] {
        assert_protected_parent_contract(preview);
    }
}

#[test]
fn exact_service_pipe_key_ledger_and_manifest_policy_is_auditable() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let value = serde_json::to_value(&preview).unwrap();
    assert_eq!(value["fixedPolicy"]["service"]["account"], "LocalSystem");
    assert_eq!(value["fixedPolicy"]["service"]["sidType"], "restricted");
    assert_eq!(
        value["fixedPolicy"]["securityPolicy"]["schema"],
        security_policy::SECURITY_POLICY_SCHEMA
    );
    assert_eq!(
        value["fixedPolicy"]["securityPolicy"]["objects"]
            .as_array()
            .map(Vec::len),
        Some(security_policy::SecurityObjectKind::ALL.len())
    );
    assert_eq!(
        value["fixedPolicy"]["securityPolicy"]["transitions"]
            .as_array()
            .map(Vec::len),
        Some(security_policy::SecurityPolicyTransition::ALL.len())
    );
    assert_eq!(
        value["fixedPolicy"]["service"]["securitySddl"],
        SERVICE_SECURITY_SDDL
    );
    assert_eq!(
        value["fixedPolicy"]["service"]["requiredPrivileges"],
        serde_json::json!(AUTHORITY_REQUIRED_PRIVILEGES)
    );
    assert_eq!(value["fixedPolicy"]["pipeName"], AUTHORITY_PIPE_NAME);
    assert_eq!(
        value["fixedPolicy"]["pipeSecuritySddl"],
        AUTHORITY_PIPE_SDDL
    );
    assert_eq!(
        value["fixedPolicy"]["maintenanceServiceSid"],
        MAINTENANCE_SERVICE_SID
    );
    assert_eq!(
        value["fixedPolicy"]["maintenanceCandidateServiceAccess"],
        MAINTENANCE_CANDIDATE_SERVICE_ACCESS
    );
    assert_eq!(
        value["fixedPolicy"]["binaryGenerationDirectorySddl"],
        BINARY_GENERATION_DIRECTORY_SDDL
    );
    assert_eq!(
        value["fixedPolicy"]["stateGenerationDirectorySddl"],
        STATE_GENERATION_DIRECTORY_SDDL
    );
    assert_eq!(
        value["fixedPolicy"]["workerNonceDirectorySddl"],
        WORKER_NONCE_DIRECTORY_SDDL
    );
    assert_eq!(
        value["fixedPolicy"]["candidateConsumptionDirectorySddl"],
        CANDIDATE_CONSUMPTION_DIRECTORY_SDDL
    );
    assert_eq!(
        value["fixedPolicy"]["workerNonceFileSddl"],
        WORKER_NONCE_FILE_SDDL
    );
    assert_eq!(
        value["fixedPolicy"]["candidateConsumptionFileSddl"],
        CANDIDATE_CONSUMPTION_FILE_SDDL
    );
    assert_eq!(
        value["fixedPolicy"]["sealedNonceFileSddl"],
        SEALED_NONCE_FILE_SDDL
    );
    assert_eq!(value["fixedPolicy"]["keyUsage"], "signOnly");
    assert_eq!(value["fixedPolicy"]["keyExportPolicy"], "noExport");
    assert_eq!(value["fixedPolicy"]["ledgerFrameSize"], FRAME_SIZE);
    assert!(value["steps"][0]["action"]["anchorPath"].is_string());
    assert!(value["steps"][0]["action"]["anchor_path"].is_null());
    assert!(value["steps"].as_array().unwrap().iter().any(|step| {
        step["action"]["contract"]["unsignedPayload"]["schema"] == TRUST_MANIFEST_SCHEMA
    }));
    let trust_step = value["steps"]
        .as_array()
        .unwrap()
        .iter()
        .find(|step| {
            step["action"]["contract"]["unsignedPayload"]["schema"] == TRUST_MANIFEST_SCHEMA
        })
        .unwrap();
    assert_eq!(
        trust_step["action"]["contract"]["unsignedPayload"]["createdEpochSource"],
        "protectedActivationChainEpoch"
    );
}

#[test]
fn restricted_maintenance_acl_separates_stable_roots_owned_generations_and_tombstones() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let maintenance_root_ace = format!("(A;OICI;0x001200a9;;;{MAINTENANCE_SERVICE_SID})");
    let maintenance_owned_ace = format!("(A;OICI;0x001300af;;;{MAINTENANCE_SERVICE_SID})");
    let maintenance_file_ace = format!("(A;;0x0013008f;;;{MAINTENANCE_SERVICE_SID})");
    let maintenance_worker_nonce_root_ace =
        format!("(A;OICI;0x001200ab;;;{MAINTENANCE_SERVICE_SID})");
    let maintenance_worker_nonce_file_ace = format!("(A;;0x0013008f;;;{MAINTENANCE_SERVICE_SID})");
    let maintenance_candidate_root_ace = format!("(A;OICI;0x001200a9;;;{MAINTENANCE_SERVICE_SID})");
    let maintenance_candidate_file_ace = format!("(A;;0x00120089;;;{MAINTENANCE_SERVICE_SID})");

    for stable_root in [BINARY_DIRECTORY_SDDL, STATE_DIRECTORY_SDDL] {
        assert!(stable_root.contains(&maintenance_root_ace));
        assert!(!stable_root.contains(&maintenance_owned_ace));
        assert!(!stable_root.contains(&format!("(A;OICI;0x001200af;;;{MAINTENANCE_SERVICE_SID})")));
    }
    for owned_generation in [
        BINARY_GENERATION_DIRECTORY_SDDL,
        STATE_GENERATION_DIRECTORY_SDDL,
    ] {
        assert!(owned_generation.contains(&maintenance_owned_ace));
    }
    for protected_file in [BINARY_FILE_SDDL, STATE_FILE_SDDL] {
        assert!(protected_file.contains(&maintenance_file_ace));
    }
    assert!(WORKER_NONCE_DIRECTORY_SDDL.contains(&maintenance_worker_nonce_root_ace));
    assert!(!WORKER_NONCE_DIRECTORY_SDDL.contains(&maintenance_owned_ace));
    assert!(WORKER_NONCE_FILE_SDDL.contains(&maintenance_worker_nonce_file_ace));
    assert!(CANDIDATE_CONSUMPTION_DIRECTORY_SDDL.contains(&maintenance_candidate_root_ace));
    assert!(CANDIDATE_CONSUMPTION_FILE_SDDL.contains(&maintenance_candidate_file_ace));
    assert!(!CANDIDATE_CONSUMPTION_FILE_SDDL.contains(&maintenance_file_ace));
    assert!(CANDIDATE_ACTIVATION_DIRECTORY_SDDL
        .contains(&format!("(A;OICI;0x001200ab;;;{MAINTENANCE_SERVICE_SID})")));

    for (step_id, expected_sddl) in [
        (
            "createBinaryGenerationDirectory",
            BINARY_GENERATION_DIRECTORY_SDDL,
        ),
        (
            "createStateGenerationDirectory",
            STATE_GENERATION_DIRECTORY_SDDL,
        ),
    ] {
        let step = preview
            .steps
            .iter()
            .find(|step| step.id == step_id)
            .unwrap();
        match &step.action {
            AuthorityMaintenanceAction::CreateDirectory { security_sddl, .. } => {
                assert_eq!(*security_sddl, expected_sddl);
            }
            other => panic!("unexpected generation action: {other:?}"),
        }
    }
}

#[test]
fn restricted_maintenance_service_access_is_query_and_candidate_start_only() {
    const SERVICE_QUERY_CONFIG: u32 = 0x0001;
    const SERVICE_CHANGE_CONFIG: u32 = 0x0002;
    const SERVICE_QUERY_STATUS: u32 = 0x0004;
    const SERVICE_START: u32 = 0x0010;
    const SERVICE_STOP: u32 = 0x0020;
    const DELETE: u32 = 0x0001_0000;
    const READ_CONTROL: u32 = 0x0002_0000;
    const WRITE_DAC: u32 = 0x0004_0000;
    const WRITE_OWNER: u32 = 0x0008_0000;

    assert_eq!(
        MAINTENANCE_CANDIDATE_SERVICE_ACCESS,
        SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS | SERVICE_START | READ_CONTROL
    );
    assert_eq!(
        MAINTENANCE_CANDIDATE_SERVICE_ACCESS
            & (SERVICE_CHANGE_CONFIG | SERVICE_STOP | DELETE | WRITE_DAC | WRITE_OWNER),
        0
    );
    let expected_ace =
        format!("(A;;0x{MAINTENANCE_CANDIDATE_SERVICE_ACCESS:08x};;;{MAINTENANCE_SERVICE_SID})");
    assert!(SERVICE_SECURITY_SDDL.contains(&expected_ace));
    assert_eq!(SERVICE_SECURITY_SDDL.matches(&expected_ace).count(), 1);
}

#[test]
fn held_source_copy_and_durable_completion_contracts_are_explicit() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let payload_steps = install
        .steps
        .iter()
        .filter_map(|step| match &step.action {
            AuthorityMaintenanceAction::CreatePayloadFile {
                source,
                source_handle_lease_required,
                source_write_sharing_denied,
                source_delete_sharing_denied,
                source_full_content_rehash_after_copy,
                destination_create_relative_to_verified_parent_handle,
                destination_handle_retained_through_readback,
                destination_write_delete_sharing_denied,
                write_through,
                flush_file_before_readback,
                flush_parent_after_create,
                rehash_destination_from_held_handle,
                verify_destination_stable_identity_and_path,
                complete_only_after_exact_readback,
                ..
            } => Some((
                *source,
                *source_handle_lease_required,
                *source_write_sharing_denied,
                *source_delete_sharing_denied,
                *source_full_content_rehash_after_copy,
                *destination_create_relative_to_verified_parent_handle,
                *destination_handle_retained_through_readback,
                *destination_write_delete_sharing_denied,
                *write_through,
                *flush_file_before_readback,
                *flush_parent_after_create,
                *rehash_destination_from_held_handle,
                *verify_destination_stable_identity_and_path,
                *complete_only_after_exact_readback,
            )),
            _ => None,
        })
        .collect::<Vec<_>>();
    assert_eq!(payload_steps.len(), 6);
    assert!(payload_steps.iter().all(|contract| {
        contract.0 == "verifiedMaintenanceLeaseHeldHandle"
            && contract.1
            && contract.2
            && contract.3
            && contract.4
            && contract.5
            && contract.6
            && contract.7
            && contract.8
            && contract.9
            && contract.10
            && contract.11
            && contract.12
            && contract.13
    }));

    assert!(install.steps.iter().any(|step| matches!(
        &step.action,
        AuthorityMaintenanceAction::ProvisionMachineKey {
            flush_provider_state_before_completion: true,
            complete_only_after_protected_readback: true,
            ..
        }
    )));
    assert!(install.steps.iter().any(|step| matches!(
        &step.action,
        AuthorityMaintenanceAction::ProvisionLedger {
            security_sddl: LEDGER_FILE_SDDL,
            write_through: true,
            flush_file_before_completion: true,
            flush_anchor_before_completion: true,
            flush_parent_after_create: true,
            rehash_identity_from_held_handle: true,
            rehash_anchor_from_held_handle: true,
            complete_only_after_exact_readback: true,
            complete_only_after_exact_pair_readback: true,
            create_pair_relative_to_verified_parent_handle: true,
            retain_both_handles_through_pair_readback: true,
            deny_write_delete_sharing_for_both: true,
            verify_each_local_reparse_free_single_link: true,
            require_distinct_physical_file_identities: true,
            persist_durable_pair_receipt_before_completion: true,
            create_new: true,
            anchor_create_new: true,
            never_reuse: true,
            anchor_never_reuse: true,
            ..
        }
    )));
    assert_eq!(
        install
            .steps
            .iter()
            .filter(|step| matches!(
                &step.action,
                AuthorityMaintenanceAction::WriteSignedManifest {
                    write_through: true,
                    flush_file_before_completion: true,
                    flush_parent_after_create: true,
                    rehash_from_held_handle: true,
                    complete_only_after_signature_and_exact_readback: true,
                    ..
                }
            ))
            .count(),
        2
    );

    let prior = installed(&install);
    for preview in [
        preview_update(&layout(), content(10), prior.clone()).unwrap(),
        preview_retire(&layout(), prior).unwrap(),
    ] {
        assert!(preview.steps.iter().any(|step| matches!(
            &step.action,
            AuthorityMaintenanceAction::StageRetirementTombstone {
                write_through: true,
                flush_file_before_completion: true,
                flush_parent_after_create: true,
                rehash_from_held_handle: true,
                complete_only_after_signature_and_exact_readback: true,
                ..
            }
        )));
    }
}

#[test]
fn ledger_pair_preview_matches_the_exact_finalizer_security_transition() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let ledger_steps = install
        .steps
        .iter()
        .filter_map(|step| match &step.action {
            AuthorityMaintenanceAction::ProvisionLedger {
                path,
                anchor_path,
                security_sddl,
                ..
            } => Some((path, anchor_path, *security_sddl)),
            _ => None,
        })
        .collect::<Vec<_>>();

    assert_eq!(ledger_steps.len(), 1);
    assert_eq!(ledger_steps[0].0, &install.layout.ledger_file);
    assert_eq!(ledger_steps[0].1, &install.layout.ledger_anchor_file);
    assert_ne!(ledger_steps[0].0, ledger_steps[0].1);
    assert_eq!(LEDGER_FILE_SDDL, security_policy::LEDGER_STAGING_SDDL);
    assert_eq!(LEDGER_FINAL_FILE_SDDL, security_policy::LEDGER_FINAL_SDDL);
    assert_eq!(ledger_steps[0].2, LEDGER_FILE_SDDL);
    assert_eq!(
        finalizer_security_windows::FinalizerSealTarget::LedgerFile.exact_security_transition(),
        (LEDGER_FILE_SDDL, LEDGER_FINAL_FILE_SDDL)
    );
}

#[test]
fn source_payload_requires_exclusive_stable_full_readback() {
    let expected = descriptor(1);
    let good = held_observation(expected, 21);
    assert!(VerifiedPayloadHandle::from_observation(expected, good).is_ok());

    let mut shared = good;
    shared.write_sharing_denied = false;
    assert_eq!(
        VerifiedPayloadHandle::from_observation(expected, shared)
            .unwrap_err()
            .code(),
        "authority_payload_handle_not_verified"
    );

    let mut content_changed = good;
    content_changed.post_read_descriptor = descriptor(9);
    assert_eq!(
        VerifiedPayloadHandle::from_observation(expected, content_changed)
            .unwrap_err()
            .code(),
        "authority_payload_handle_not_verified"
    );

    let mut identity_changed = good;
    identity_changed.post_read_file_id = [99; 16];
    assert_eq!(
        VerifiedPayloadHandle::from_observation(expected, identity_changed)
            .unwrap_err()
            .code(),
        "authority_payload_handle_not_verified"
    );
}

#[test]
fn update_and_retire_are_bound_to_verified_prior_generation() {
    let first = preview_install(&layout(), content(1)).unwrap();
    let first_installed = installed(&first);
    let update = preview_update(&layout(), content(10), first_installed.clone()).unwrap();
    assert_eq!(update.operation, AuthorityMaintenanceOperation::Update);
    assert_eq!(
        update.prior_generation.as_deref(),
        Some(first.generation.as_str())
    );
    assert_ne!(update.generation, first.generation);
    assert!(update
        .steps
        .iter()
        .any(|step| step.id == "stagePriorRetirementTombstone"));
    let update_retirement = update
        .steps
        .iter()
        .find(|step| step.id == "stagePriorRetirementTombstone")
        .unwrap();
    assert!(matches!(
        &update_retirement.failed_apply_cleanup,
        AuthorityRollbackAction::None
    ));
    assert!(matches!(
        &update_retirement.rollback,
        AuthorityRollbackAction::None
    ));
    let stop = update
        .steps
        .iter()
        .position(|step| step.id == "stopDrainPriorServiceExact")
        .unwrap();
    let change = update
        .steps
        .iter()
        .position(|step| step.id == "configureServiceExact")
        .unwrap();
    assert!(stop < change);
    match &update.steps[stop].action {
        AuthorityMaintenanceAction::StopDrainServiceExact {
            expected_process_id,
            expected_process_creation_time,
            expected_image_sha256,
            expected_pipe_instance_id,
            require_exact_process_identity,
            require_held_image_identity,
            require_pipe_close_proof,
            require_scm_stopped_readback,
            ..
        } => {
            assert_eq!(
                *expected_process_id,
                first_installed.service_runtime.process_id
            );
            assert_eq!(
                *expected_process_creation_time,
                first_installed.service_runtime.process_creation_time
            );
            assert_eq!(
                expected_image_sha256,
                &hex_lower(&first_installed.service_runtime.image_sha256)
            );
            assert_eq!(
                expected_pipe_instance_id,
                &hex_lower(&first_installed.service_runtime.pipe_instance_id)
            );
            assert!(*require_exact_process_identity);
            assert!(*require_held_image_identity);
            assert!(*require_pipe_close_proof);
            assert!(*require_scm_stopped_readback);
        }
        other => panic!("unexpected stop/drain action: {other:?}"),
    }
    assert!(matches!(
        &update.steps[change].rollback,
        AuthorityRollbackAction::RestorePriorServiceConfiguration {
            require_generation_handshake: true,
            ..
        }
    ));

    let retire = preview_retire(&layout(), first_installed).unwrap();
    assert_eq!(retire.operation, AuthorityMaintenanceOperation::Retire);
    assert_eq!(retire.generation, first.generation);
    assert!(retire.steps.iter().all(|step| !matches!(
        step.action,
        AuthorityMaintenanceAction::CreatePayloadFile { .. }
            | AuthorityMaintenanceAction::ProvisionMachineKey { .. }
            | AuthorityMaintenanceAction::ProvisionLedger { .. }
    )));
    let retirement_manifest = retire
        .steps
        .iter()
        .find(|step| step.id == "stageRetirementTombstone")
        .unwrap();
    assert!(matches!(
        &retirement_manifest.failed_apply_cleanup,
        AuthorityRollbackAction::MarkRetirementAbortedNoReuse { .. }
    ));
    let stop = retire
        .steps
        .iter()
        .position(|step| step.id == "stopDrainPriorServiceExact")
        .unwrap();
    let remove = retire
        .steps
        .iter()
        .position(|step| step.id == "removeServiceRegistration")
        .unwrap();
    let finalize = retire
        .steps
        .iter()
        .position(|step| step.id == "finalizeRetirementTombstone")
        .unwrap();
    assert!(stop < remove && remove < finalize);
}

#[test]
fn install_and_update_validate_a_bounded_candidate_before_commit() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let prior = installed(&install);
    let update = preview_update(&layout(), content(10), prior).unwrap();
    for preview in [&install, &update] {
        let candidate = preview
            .steps
            .iter()
            .find(|step| step.id == "validateCandidateServiceGenerationHandshake")
            .unwrap();
        assert!(matches!(
            &candidate.action,
            AuthorityMaintenanceAction::ValidateCandidateServiceGenerationHandshake {
                credential_schema,
                maximum_credential_lifetime_millis,
                require_scm_process_identity_before_arm: true,
                require_atomic_prepared_to_armed_transition: true,
                require_one_use_consumption: true,
                keep_service_start_pending_through_candidate_exit: true,
                require_new_process_identity: true,
                require_held_image_identity: true,
                require_candidate_only_pipe_generation_handshake: true,
                forbid_runtime_controller_pipe: true,
                require_candidate_exit_before_completion: true,
                ..
            } if *credential_schema
                == bootstrap_activation::CANDIDATE_ACTIVATION_CREDENTIAL_SCHEMA
                && *maximum_credential_lifetime_millis
                    == bootstrap_activation::MAX_CANDIDATE_CREDENTIAL_LIFETIME_MILLIS
        ));
        assert!(matches!(
            &candidate.failed_apply_cleanup,
            AuthorityRollbackAction::StopCandidateValidationServiceExact {
                identity_source: "consumedCandidateCredentialReadback",
                require_exact_process_identity: true,
                require_natural_exit_or_owned_stop: true,
                require_scm_stopped_readback: true,
                ..
            }
        ));
        let candidate_index = preview
            .steps
            .iter()
            .position(|step| step.id == "validateCandidateServiceGenerationHandshake")
            .unwrap();
        let seal_index = preview
            .steps
            .iter()
            .position(|step| step.id == "sealCandidateGenerationForFinalCommit")
            .unwrap();
        let advance_index = preview
            .steps
            .iter()
            .position(|step| step.id == "advanceActiveHeadAtomic")
            .unwrap();
        let runtime_index = preview
            .steps
            .iter()
            .position(|step| step.id == "startCommittedRuntime")
            .unwrap();
        let zero_residue_index = preview
            .steps
            .iter()
            .position(|step| step.id == "verifyOperationZeroResidue")
            .unwrap();
        let final_commit_index = preview
            .steps
            .iter()
            .position(|step| step.id == "persistFinalCommit")
            .unwrap();
        assert!(
            candidate_index < seal_index
                && seal_index < advance_index
                && advance_index < runtime_index
                && runtime_index < zero_residue_index
                && zero_residue_index < final_commit_index
        );
        assert!(matches!(
            &preview.steps[seal_index].action,
            AuthorityMaintenanceAction::SealCandidateGenerationForFinalCommit {
                security_policy_source: "fixedPolicy.securityPolicy",
                require_candidate_seal_ready_receipt: true,
                require_worker_exit_ready_receipt: true,
                require_candidate_stopped_and_all_writers_closed: true,
                preserve_object_identity_and_bytes: true,
                require_complete_generation_object_manifest: true,
                require_each_file_identity_hash_and_security_receipt: true,
                require_each_directory_identity_and_security_receipt: true,
                reject_unlisted_generation_objects: true,
                seal_nonce_artifacts_individually: true,
                apply_exact_final_security_from_policy: true,
                reopen_read_only_and_verify_full_security: true,
                persist_seal_complete_before_active_head: true,
                irreversible_roll_forward_boundary: true,
                post_boundary_failure_policy: "resumeFinalCommitWithoutRevertingSealedGeneration",
                elevated_finalizer_only: true,
                ..
            }
        ));
        assert!(matches!(
            &preview.steps[seal_index].failed_apply_cleanup,
            AuthorityRollbackAction::None
        ));
        assert!(matches!(
            &preview.steps[seal_index].rollback,
            AuthorityRollbackAction::None
        ));
        let committed_readbacks = preview
            .steps
            .iter()
            .filter(|step| {
                matches!(
                    step.action,
                    AuthorityMaintenanceAction::VerifyPostcommitServingRuntimeReadback {
                        require_seal_complete_receipt: true,
                        require_final_commit_receipt: true,
                        require_exact_service_configuration: true,
                        require_same_precommit_runtime_process_and_image_identity: true,
                        require_runtime_observed_exact_final_commit_receipt: true,
                        require_controller_pipe_present_after_final_commit: true,
                        require_generation_pipe_handshake: true,
                        require_active_head_binding: true,
                        require_serving_state_bound_to_final_commit_gate: true,
                        require_runtime_healthy: true,
                        allow_recovery_runtime_restart_after_authenticated_final_commit: true,
                        require_recovery_final_commit_receipt_immutable: true,
                        require_recovery_active_head_binding: true,
                        require_recovery_exact_service_configuration: true,
                        require_recovery_exact_service_image: true,
                        require_recovery_exact_generation: true,
                        require_recovery_final_commit_gate_binding: true,
                        forbid_final_commit_receipt_rewrite_during_recovery: true,
                        require_recovery_previous_precommit_runtime_absence: true,
                        require_recovery_start_or_adopt_new_runtime_process_identity: true,
                        require_recovery_serving_readback: true,
                        ..
                    }
                )
            })
            .count();
        assert_eq!(committed_readbacks, 1);
        assert!(matches!(
            &preview.steps[runtime_index].action,
            AuthorityMaintenanceAction::StartCommittedRuntime {
                require_seal_complete_receipt: true,
                require_active_head_compare_exchange_readback: true,
                require_candidate_and_runtime_service_identity_match: true,
                require_distinct_process_identity_from_candidate: true,
                require_new_pipe_instance_identity: true,
                require_committed_runtime_generation_handshake: true,
                require_precommit_dormant_runtime_readback: true,
                require_controller_pipe_absent_before_final_commit: true,
                require_generation_writer_roster_empty_before_final_commit: true,
                runtime_self_activates_only_after_durable_final_commit_readback: true,
                hold_runtime_process_and_image_handles_through_final_commit: true,
                elevated_finalizer_only: true,
                ..
            }
        ));
        assert!(matches!(
            &preview.steps[runtime_index].failed_apply_cleanup,
            AuthorityRollbackAction::None
        ));
        assert!(matches!(
            &preview.steps[zero_residue_index].action,
            AuthorityMaintenanceAction::VerifyOperationZeroResidue {
                require_maintenance_service_absent: true,
                require_no_staging_or_publishing_files: true,
                require_worker_process_and_transient_state_absent: true,
                require_candidate_credentials_absent: true,
                require_nonce_and_consumption_artifacts_sealed: true,
                require_exact_active_head: true,
                reject_unplanned_residue: true,
                ..
            }
        ));
        assert!(matches!(
            &preview.steps[final_commit_index].action,
            AuthorityMaintenanceAction::PersistFinalCommit {
                require_seal_complete_receipt: true,
                require_active_head_compare_exchange_readback: true,
                require_runtime_identity_and_handshake_readback: true,
                require_precommit_dormant_runtime_readback: true,
                require_controller_pipe_absence_readback: true,
                require_generation_writer_roster_empty_readback: true,
                bind_runtime_self_activation_gate: true,
                require_operation_zero_residue_readback: true,
                atomic_create_new: true,
                flush_file_before_publish: true,
                no_replace: true,
                flush_parent: true,
                require_no_publishing_artifact_readback: true,
                hold_runtime_process_and_image_handles_through_completion: true,
                elevated_finalizer_only: true,
                ..
            }
        ));
        let activation = preview
            .steps
            .iter()
            .find(|step| step.id == "writeActivationManifest")
            .unwrap();
        assert!(matches!(
            &activation.rollback,
            AuthorityRollbackAction::DiscardManifestAndSealGenerationConsumed {
                manifest_path,
                ..
            } if manifest_path == &preview.layout.activation_manifest
        ));
    }
}

#[test]
fn successor_activation_digest_is_a_signed_held_handle_readback_reference() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let prior = installed(&install);
    let update = preview_update(&layout(), content(10), prior.clone()).unwrap();
    assert!(!serde_json::to_string(&update)
        .unwrap()
        .contains("protectedSuccessorActivationDigest"));

    let finalize = update
        .steps
        .iter()
        .find(|step| step.id == "finalizePriorRetirementTombstone")
        .unwrap();
    assert!(matches!(
        &finalize.action,
        AuthorityMaintenanceAction::FinalizeRetirementTombstoneAtomic {
            expected_active_epoch,
            expected_active_activation:
                ProtectedActivationDigestReference::SignedManifestHeldHandleReadback {
                    generation,
                    manifest_path,
                    source: "signedActivationManifestHeldHandle",
                    require_file_flush_before_readback: true,
                    require_held_handle: true,
                    require_stable_file_identity: true,
                    require_canonical_unsigned_payload_digest: true,
                    require_detached_signature_verification: true,
                    complete_only_after_exact_generation_and_digest_readback: true,
            },
            ..
        } if *expected_active_epoch == prior.activation_epoch + 1
            && generation == &update.generation
            && manifest_path == &update.layout.activation_manifest
    ));
    let advance = update
        .steps
        .iter()
        .find(|step| step.id == "advanceActiveHeadAtomic")
        .unwrap();
    assert!(matches!(
        &advance.action,
        AuthorityMaintenanceAction::AdvanceActiveHeadAtomic {
            activation:
                ProtectedActivationDigestReference::SignedManifestHeldHandleReadback {
                    generation,
                    manifest_path,
                    require_detached_signature_verification: true,
                    complete_only_after_exact_generation_and_digest_readback: true,
                    ..
                },
            ..
        } if generation == &update.generation && manifest_path == &update.layout.activation_manifest
    ));

    let mut steps = update.steps.clone();
    match &mut steps[0].action {
        AuthorityMaintenanceAction::CreateDurableJournal { plan_sha256, .. } => plan_sha256.clear(),
        other => panic!("journal is not first: {other:?}"),
    }
    let finalize = steps
        .iter_mut()
        .find(|step| step.id == "finalizePriorRetirementTombstone")
        .unwrap();
    match &mut finalize.action {
        AuthorityMaintenanceAction::FinalizeRetirementTombstoneAtomic {
            expected_active_activation:
                ProtectedActivationDigestReference::SignedManifestHeldHandleReadback {
                    require_detached_signature_verification,
                    ..
                },
            ..
        } => *require_detached_signature_verification = false,
        other => panic!("unexpected finalization action: {other:?}"),
    }
    let changed = derive_full_plan_digest(
        update.operation,
        &update.generation_sha256().unwrap(),
        Some(&prior),
        &content_from_projection(&update.content).unwrap(),
        &decode_hex_32(&update.policy_sha256).unwrap(),
        &update.layout,
        &update.fixed_policy,
        &steps,
    )
    .unwrap();
    assert_ne!(hex_lower(&changed), update.plan_sha256);
}

#[test]
fn exhausted_activation_epoch_rejects_update_and_retirement() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let mut prior = installed(&install);
    prior.activation_epoch = u64::MAX;
    assert_eq!(
        preview_update(&layout(), content(10), prior.clone())
            .unwrap_err()
            .code(),
        "authority_activation_epoch_exhausted"
    );
    assert_eq!(
        preview_retire(&layout(), prior).unwrap_err().code(),
        "authority_activation_epoch_exhausted"
    );
}

#[test]
fn full_plan_digest_commits_ordered_actions_but_excludes_only_its_own_field() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let mut steps = preview.steps.clone();
    match &mut steps[0].action {
        AuthorityMaintenanceAction::CreateDurableJournal { plan_sha256, .. } => plan_sha256.clear(),
        other => panic!("journal is not first: {other:?}"),
    }
    let generation = preview.generation_sha256().unwrap();
    let policy = decode_hex_32(&preview.policy_sha256).unwrap();
    let content = content_from_projection(&preview.content).unwrap();
    let baseline = derive_full_plan_digest(
        preview.operation,
        &generation,
        None,
        &content,
        &policy,
        &preview.layout,
        &preview.fixed_policy,
        &steps,
    )
    .unwrap();
    assert_eq!(hex_lower(&baseline), preview.plan_sha256);
    let last = steps.last_mut().unwrap();
    match &mut last.action {
        AuthorityMaintenanceAction::VerifyPostcommitServingRuntimeReadback {
            require_runtime_healthy,
            ..
        } => *require_runtime_healthy = !*require_runtime_healthy,
        other => panic!("unexpected final action: {other:?}"),
    }
    let changed = derive_full_plan_digest(
        preview.operation,
        &generation,
        None,
        &content,
        &policy,
        &preview.layout,
        &preview.fixed_policy,
        &steps,
    )
    .unwrap();
    assert_ne!(changed, baseline);
}

#[cfg(windows)]
#[test]
fn initial_install_uses_bootstrap_helper_capability_not_target_controller() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let mut lease = maintenance_lease(&preview);
    let capability =
        VerifiedElevatedMaintenanceCapability::from_sealed_bootstrap(&preview, &lease).unwrap();
    assert_eq!(capability.process_id(), 77);
    assert_eq!(
        capability.bootstrap_binding_sha256,
        lease.bootstrap_helper.binding_sha256
    );
    assert_eq!(
        execute_maintenance_transaction(&preview, &capability, &mut lease)
            .unwrap_err()
            .code(),
        "authority_system_worker_staging_not_complete"
    );
}

#[cfg(windows)]
#[test]
fn bootstrap_handle_loss_or_identity_replacement_cannot_execute() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let mut lost = maintenance_lease(&preview);
    let capability =
        VerifiedElevatedMaintenanceCapability::from_sealed_bootstrap(&preview, &lost).unwrap();
    match &mut lost.held_payloads {
        HeldPayloadLease::Test(handles) => handles.bootstrap_image_handle_live = false,
        #[allow(unreachable_patterns)]
        _ => unreachable!(),
    }
    assert_eq!(
        execute_maintenance_transaction(&preview, &capability, &mut lost)
            .unwrap_err()
            .code(),
        "authority_maintenance_capability_mismatch"
    );

    let mut replaced = maintenance_lease(&preview);
    let capability =
        VerifiedElevatedMaintenanceCapability::from_sealed_bootstrap(&preview, &replaced).unwrap();
    replaced.bootstrap_helper.binding_sha256 = [99; 32];
    assert_eq!(
        execute_maintenance_transaction(&preview, &capability, &mut replaced)
            .unwrap_err()
            .code(),
        "authority_maintenance_capability_mismatch"
    );
}

#[test]
fn manifest_chain_rejects_forks_and_epoch_downgrades() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let mut forked = sealed_installed_readback(&preview);
    let mut second_head = forked.manifests.observed_heads[0];
    second_head.protected_head_receipt_sha256 = [99; 32];
    forked.manifests.observed_heads.push(second_head);
    assert_eq!(
        VerifiedInstalledGeneration::from_sealed_readback(forked)
            .unwrap_err()
            .code(),
        "authority_manifest_unique_head_not_verified"
    );

    let mut unsealed_head = sealed_installed_readback(&preview);
    unsealed_head.manifests.observed_heads[0].protected_head_receipt_sha256 = [0; 32];
    assert_eq!(
        VerifiedInstalledGeneration::from_sealed_readback(unsealed_head)
            .unwrap_err()
            .code(),
        "authority_manifest_unique_head_not_verified"
    );

    let historical = detached(
        CanonicalUnsignedManifestPayload::Activation {
            generation: [31; 32],
            trust_manifest_sha256: [32; 32],
            signer_key_id: [33; 32],
            activated_epoch: 1,
            previous_generation: None,
            previous_activation_sha256: None,
            previous_activation_epoch: None,
            valid: true,
            revoked: false,
        },
        [33; 32],
    );

    let mut downgraded = sealed_installed_readback(&preview);
    replace_current_activation_chain(
        &mut downgraded,
        vec![historical],
        1,
        1,
        Some(([31; 32], historical.unsigned_payload_sha256, 1)),
    );
    assert_eq!(
        VerifiedInstalledGeneration::from_sealed_readback(downgraded)
            .unwrap_err()
            .code(),
        "authority_manifest_predecessor_not_verified"
    );

    let mut missing_predecessor = sealed_installed_readback(&preview);
    replace_current_activation_chain(
        &mut missing_predecessor,
        Vec::new(),
        2,
        2,
        Some(([31; 32], historical.unsigned_payload_sha256, 1)),
    );
    assert_eq!(
        VerifiedInstalledGeneration::from_sealed_readback(missing_predecessor)
            .unwrap_err()
            .code(),
        "authority_manifest_predecessor_not_verified"
    );

    let mut wrong_predecessor = sealed_installed_readback(&preview);
    replace_current_activation_chain(
        &mut wrong_predecessor,
        vec![historical],
        2,
        2,
        Some(([41; 32], historical.unsigned_payload_sha256, 1)),
    );
    assert_eq!(
        VerifiedInstalledGeneration::from_sealed_readback(wrong_predecessor)
            .unwrap_err()
            .code(),
        "authority_manifest_predecessor_not_verified"
    );

    let mut exact_predecessor = sealed_installed_readback(&preview);
    replace_current_activation_chain(
        &mut exact_predecessor,
        vec![historical],
        2,
        2,
        Some(([31; 32], historical.unsigned_payload_sha256, 1)),
    );
    assert!(VerifiedInstalledGeneration::from_sealed_readback(exact_predecessor).is_ok());

    let mut split_epoch_domain = sealed_installed_readback(&preview);
    replace_current_activation_chain(
        &mut split_epoch_domain,
        vec![historical],
        2,
        3,
        Some(([31; 32], historical.unsigned_payload_sha256, 1)),
    );
    assert_eq!(
        VerifiedInstalledGeneration::from_sealed_readback(split_epoch_domain)
            .unwrap_err()
            .code(),
        "authority_manifest_epoch_domain_mismatch"
    );
}

#[test]
fn activation_history_verifies_every_signed_link_back_to_genesis() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let genesis = detached(
        CanonicalUnsignedManifestPayload::Activation {
            generation: [31; 32],
            trust_manifest_sha256: [32; 32],
            signer_key_id: [33; 32],
            activated_epoch: 1,
            previous_generation: None,
            previous_activation_sha256: None,
            previous_activation_epoch: None,
            valid: true,
            revoked: false,
        },
        [33; 32],
    );
    let second = detached(
        CanonicalUnsignedManifestPayload::Activation {
            generation: [41; 32],
            trust_manifest_sha256: [42; 32],
            signer_key_id: [43; 32],
            activated_epoch: 2,
            previous_generation: Some([31; 32]),
            previous_activation_sha256: Some(genesis.unsigned_payload_sha256),
            previous_activation_epoch: Some(1),
            valid: true,
            revoked: false,
        },
        [43; 32],
    );
    let mut complete = sealed_installed_readback(&preview);
    replace_current_activation_chain(
        &mut complete,
        vec![genesis, second],
        3,
        3,
        Some(([41; 32], second.unsigned_payload_sha256, 2)),
    );
    assert!(VerifiedInstalledGeneration::from_sealed_readback(complete.clone()).is_ok());

    complete.manifests.protected_activation_history[0]
        .signature
        .receipt_sha256 = [0; 32];
    assert_eq!(
        VerifiedInstalledGeneration::from_sealed_readback(complete)
            .unwrap_err()
            .code(),
        "authority_detached_manifest_not_verified"
    );
}

#[test]
fn retirement_manifest_requires_exact_prior_and_successor_links() {
    let key_id = [7; 32];
    let retirement = detached(
        CanonicalUnsignedManifestPayload::Retirement {
            generation: [1; 32],
            prior_activation_sha256: [2; 32],
            retired_epoch: 4,
            successor_generation: Some([3; 32]),
            successor_activation_sha256: Some([4; 32]),
            valid: false,
            revoked: true,
        },
        key_id,
    );
    assert!(verify_retirement_link(
        &retirement,
        [1; 32],
        [2; 32],
        3,
        Some(([3; 32], [4; 32], 4)),
    )
    .is_ok());
    assert_eq!(
        verify_retirement_link(
            &retirement,
            [1; 32],
            [2; 32],
            3,
            Some(([5; 32], [4; 32], 4)),
        )
        .unwrap_err()
        .code(),
        "authority_retirement_manifest_link_invalid"
    );
    assert_eq!(
        verify_retirement_link(
            &retirement,
            [1; 32],
            [2; 32],
            4,
            Some(([3; 32], [4; 32], 4)),
        )
        .unwrap_err()
        .code(),
        "authority_retirement_manifest_link_invalid"
    );
}

#[test]
fn retirement_uses_plan_staging_atomic_finalize_and_nonreusable_abort_marker() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let prior = installed(&install);
    let retire = preview_retire(&layout(), prior.clone()).unwrap();
    let staging = retire.layout.retirement_staging_manifest.as_ref().unwrap();
    let aborted = retire.layout.retirement_aborted_marker.as_ref().unwrap();
    let final_path = retire.layout.retirement_manifest.as_ref().unwrap();
    assert!(staging.contains(&retire.transaction_sha256));
    assert!(aborted.contains(&retire.transaction_sha256));
    assert_ne!(staging, aborted);
    assert_ne!(staging, final_path);
    assert!(retire.steps.iter().any(|step| matches!(
        &step.action,
        AuthorityMaintenanceAction::FinalizeRetirementTombstoneAtomic {
            no_replace: true,
            flush_parent: true,
            aborted_marker_forbids_reuse: true,
            expected_active_epoch,
            ..
        } if *expected_active_epoch == prior.activation_epoch
    )));
}

#[derive(Default)]
struct FakeExecutor {
    fail_apply_at: Option<usize>,
    fail_after_irreversible_commit_at: Option<usize>,
    fail_rollback_at: Option<&'static str>,
    fail_transition_at: Option<usize>,
    applied: usize,
    transitions: Vec<(&'static str, JournalTransition)>,
    rollback_order: Vec<&'static str>,
    recovery_seal: Option<[u8; 32]>,
    recovery_seal_calls: usize,
    terminal: Option<JournalTerminal>,
    startup_recovery: Option<StartupRecoveryDisposition>,
    journal_created: bool,
    fail_create_journal: bool,
    payload_binding_seen: Option<[u8; 32]>,
    post_commit_containments: Vec<&'static str>,
    fail_post_commit_containment: bool,
}

impl MaintenanceExecutor for FakeExecutor {
    fn recover_startup(
        &mut self,
        _journal: &JournalContractProjection,
    ) -> Result<StartupRecoveryDisposition, ()> {
        Ok(self
            .startup_recovery
            .unwrap_or(StartupRecoveryDisposition::Clean))
    }

    fn create_journal(&mut self, _journal: &JournalContractProjection) -> Result<(), ()> {
        if self.fail_create_journal {
            return Err(());
        }
        self.journal_created = true;
        Ok(())
    }

    fn record_transition(
        &mut self,
        step: &AuthorityMaintenanceStep,
        transition: JournalTransition,
    ) -> Result<(), ()> {
        let index = self.transitions.len();
        self.transitions.push((step.id, transition));
        if self.fail_transition_at == Some(index) {
            Err(())
        } else {
            Ok(())
        }
    }

    fn apply(
        &mut self,
        step: &AuthorityMaintenanceStep,
        lease: &VerifiedMaintenanceLease,
    ) -> Result<(), MaintenanceApplyFailure> {
        self.payload_binding_seen = Some(lease.payloads.binding_sha256);
        let index = self.applied;
        self.applied += 1;
        if self.fail_after_irreversible_commit_at == Some(index) {
            assert!(matches!(
                &step.action,
                AuthorityMaintenanceAction::FinalizeRetirementTombstoneAtomic {
                    irreversible_commit: true,
                    ..
                }
            ));
            Err(MaintenanceApplyFailure::AfterIrreversibleCommit)
        } else if self.fail_apply_at == Some(index) {
            Err(MaintenanceApplyFailure::BeforeIrreversibleCommit)
        } else {
            Ok(())
        }
    }

    fn cleanup_failed_apply(&mut self, step: &AuthorityMaintenanceStep) -> Result<(), ()> {
        self.rollback_order.push(step.id);
        if self.fail_rollback_at == Some(step.id) {
            Err(())
        } else {
            Ok(())
        }
    }

    fn rollback_completed(&mut self, step: &AuthorityMaintenanceStep) -> Result<(), ()> {
        self.rollback_order.push(step.id);
        if self.fail_rollback_at == Some(step.id) {
            Err(())
        } else {
            Ok(())
        }
    }

    fn contain_post_commit(&mut self, failed_step: &AuthorityMaintenanceStep) -> Result<(), ()> {
        self.post_commit_containments.push(failed_step.id);
        if self.fail_post_commit_containment {
            Err(())
        } else {
            Ok(())
        }
    }

    fn seal_recovery_once(
        &mut self,
        _path: &str,
        content_sha256: [u8; 32],
    ) -> Result<IdempotentWriteDisposition, ()> {
        self.recovery_seal_calls += 1;
        match self.recovery_seal {
            None => {
                self.recovery_seal = Some(content_sha256);
                Ok(IdempotentWriteDisposition::Created)
            }
            Some(existing) if existing == content_sha256 => {
                Ok(IdempotentWriteDisposition::AlreadyIdentical)
            }
            Some(_) => Err(()),
        }
    }

    fn write_journal_terminal(
        &mut self,
        terminal: JournalTerminal,
    ) -> Result<IdempotentWriteDisposition, ()> {
        match self.terminal {
            None => {
                self.terminal = Some(terminal);
                Ok(IdempotentWriteDisposition::Created)
            }
            Some(existing) if existing == terminal => {
                Ok(IdempotentWriteDisposition::AlreadyIdentical)
            }
            Some(_) => Err(()),
        }
    }
}

#[test]
fn journal_precedes_mutation_and_fsync_transitions_are_ordered() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let mut lease = maintenance_lease(&preview);
    let binding = lease.payloads.binding_sha256;
    let mut executor = FakeExecutor::default();
    let report = execute_with_test_executor(&preview, &mut lease, &mut executor);
    assert_eq!(report.status, "committed");
    assert!(executor.journal_created);
    assert_eq!(executor.payload_binding_seen, Some(binding));
    assert_eq!(executor.transitions.len(), (preview.steps.len() - 1) * 2);
    for (index, step) in preview.steps.iter().skip(1).enumerate() {
        assert_eq!(
            executor.transitions[index * 2],
            (step.id, JournalTransition::StepStarted)
        );
        assert_eq!(
            executor.transitions[index * 2 + 1],
            (step.id, JournalTransition::StepCompleted)
        );
    }
    assert!(preview.journal.write_through);
    assert!(preview.journal.flush_file_after_every_transition);
    assert!(preview.journal.flush_parent_after_create);
}

#[test]
fn precreated_or_redirected_journal_fails_before_any_maintenance_step() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let mut lease = maintenance_lease(&preview);
    let mut executor = FakeExecutor {
        fail_create_journal: true,
        ..Default::default()
    };
    let report = execute_with_test_executor(&preview, &mut lease, &mut executor);
    assert_eq!(report.status, "recoveryRequired");
    assert!(report.completed_steps.is_empty());
    assert_eq!(executor.applied, 0);
    assert!(executor.transitions.is_empty());
    assert_eq!(report.rollback_failures, vec!["journalDurability"]);
}

#[test]
fn interrupted_transition_requires_startup_recovery_before_new_work() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let mut lease = maintenance_lease(&preview);
    let mut interrupted = FakeExecutor {
        fail_transition_at: Some(0),
        ..Default::default()
    };
    let report = execute_with_test_executor(&preview, &mut lease, &mut interrupted);
    assert_eq!(report.status, "recoveryRequired");
    assert_eq!(report.journal_terminal, None);
    assert!(report
        .blockers
        .contains(&"authority_maintenance_journal_uncertain"));

    let mut recovered_lease = maintenance_lease(&preview);
    let mut recovered = FakeExecutor {
        startup_recovery: Some(StartupRecoveryDisposition::RecoveredContained),
        ..Default::default()
    };
    let report = execute_with_test_executor(&preview, &mut recovered_lease, &mut recovered);
    assert_eq!(report.startup_recovery, Some("contained"));
    assert_eq!(report.journal_terminal, Some("committed"));

    let mut rolled_back_lease = maintenance_lease(&preview);
    let mut rolled_back = FakeExecutor {
        startup_recovery: Some(StartupRecoveryDisposition::RecoveredRolledBack),
        ..Default::default()
    };
    let report = execute_with_test_executor(&preview, &mut rolled_back_lease, &mut rolled_back);
    assert_eq!(report.startup_recovery, Some("rolledBack"));
}

#[test]
fn undurable_completion_and_terminal_never_report_success() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let mut lease = maintenance_lease(&preview);
    let mut completion_lost = FakeExecutor {
        fail_transition_at: Some(1),
        ..Default::default()
    };
    let report = execute_with_test_executor(&preview, &mut lease, &mut completion_lost);
    assert_eq!(completion_lost.applied, 1);
    assert_eq!(report.status, "recoveryRequired");
    assert_eq!(report.journal_terminal, None);
    assert_eq!(report.failed_step, Some(preview.steps[1].id));
    assert!(report
        .blockers
        .contains(&"authority_maintenance_journal_uncertain"));
    assert!(completion_lost.rollback_order.is_empty());

    let mut lease = maintenance_lease(&preview);
    let mut terminal_conflict = FakeExecutor {
        terminal: Some(JournalTerminal::RolledBack),
        ..Default::default()
    };
    let report = execute_with_test_executor(&preview, &mut lease, &mut terminal_conflict);
    assert_eq!(report.status, "recoveryRequired");
    assert_eq!(report.journal_terminal, None);
    assert_eq!(report.rollback_failures, vec!["journalTerminal"]);
    assert!(report
        .blockers
        .contains(&"authority_maintenance_journal_uncertain"));
    assert!(!report.trusted_boundary_ready);
}

#[test]
fn recovery_and_terminal_seals_are_identical_idempotent_and_conflict_rejecting() {
    let mut executor = FakeExecutor::default();
    assert_eq!(
        executor.seal_recovery_once("recovery", [1; 32]),
        Ok(IdempotentWriteDisposition::Created)
    );
    assert_eq!(
        executor.seal_recovery_once("recovery", [1; 32]),
        Ok(IdempotentWriteDisposition::AlreadyIdentical)
    );
    assert_eq!(executor.seal_recovery_once("recovery", [2; 32]), Err(()));
    assert_eq!(
        executor.write_journal_terminal(JournalTerminal::Contained),
        Ok(IdempotentWriteDisposition::Created)
    );
    assert_eq!(
        executor.write_journal_terminal(JournalTerminal::Contained),
        Ok(IdempotentWriteDisposition::AlreadyIdentical)
    );
    assert_eq!(
        executor.write_journal_terminal(JournalTerminal::Committed),
        Err(())
    );
}

#[test]
fn candidate_failure_never_advances_active_head_and_restores_service_configuration() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let installed = installed(&install);
    let update = preview_update(&layout(), content(10), installed.clone()).unwrap();
    for preview in [&install, &update] {
        let advance = preview
            .steps
            .iter()
            .find(|step| step.id == "advanceActiveHeadAtomic")
            .unwrap();
        assert!(matches!(advance.rollback, AuthorityRollbackAction::None));
        assert!(matches!(
            advance.failed_apply_cleanup,
            AuthorityRollbackAction::None
        ));
        let candidate = preview
            .steps
            .iter()
            .position(|step| step.id == "validateCandidateServiceGenerationHandshake")
            .unwrap();
        let advance = preview
            .steps
            .iter()
            .position(|step| step.id == "advanceActiveHeadAtomic")
            .unwrap();
        assert!(candidate < advance);
        let mut lease = maintenance_lease(preview);
        let mut executor = FakeExecutor {
            fail_apply_at: Some(candidate - 1),
            ..Default::default()
        };
        let report = execute_with_test_executor(preview, &mut lease, &mut executor);
        assert_eq!(report.status, "contained");
        assert_eq!(executor.recovery_seal_calls, 1);
        assert!(!executor.rollback_order.contains(&"advanceActiveHeadAtomic"));
        let stop_candidate = executor
            .rollback_order
            .iter()
            .position(|step| *step == "validateCandidateServiceGenerationHandshake")
            .unwrap();
        let restore_service = executor
            .rollback_order
            .iter()
            .position(|step| *step == "configureServiceExact")
            .unwrap();
        assert!(stop_candidate < restore_service);

        let mut lease = maintenance_lease(preview);
        let mut executor = FakeExecutor {
            fail_apply_at: Some(candidate - 1),
            fail_rollback_at: Some("validateCandidateServiceGenerationHandshake"),
            ..Default::default()
        };
        let report = execute_with_test_executor(preview, &mut lease, &mut executor);
        assert_eq!(report.status, "recoveryRequired");
        assert!(report
            .rollback_failures
            .contains(&"validateCandidateServiceGenerationHandshake"));
    }
}

#[test]
fn seal_complete_is_a_roll_forward_boundary_for_runtime_start_failures() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let prior = installed(&install);
    let update = preview_update(&layout(), content(10), prior).unwrap();

    for preview in [&install, &update] {
        let runtime_start = preview
            .steps
            .iter()
            .position(|step| step.id == "startCommittedRuntime")
            .unwrap();
        let mut lease = maintenance_lease(preview);
        let mut executor = FakeExecutor {
            fail_apply_at: Some(runtime_start - 1),
            ..Default::default()
        };
        let report = execute_with_test_executor(preview, &mut lease, &mut executor);
        assert_eq!(report.status, "contained");
        assert_eq!(report.failed_step_cleanup, Some("postCommitContained"));
        assert!(executor.rollback_order.is_empty());
        assert_eq!(executor.post_commit_containments, ["startCommittedRuntime"]);

        let mut lease = maintenance_lease(preview);
        let mut fallback = FakeExecutor {
            fail_apply_at: Some(runtime_start - 1),
            fail_post_commit_containment: true,
            ..Default::default()
        };
        let report = execute_with_test_executor(preview, &mut lease, &mut fallback);
        assert_eq!(report.status, "recoveryRequired");
        assert!(fallback.rollback_order.is_empty());
        assert!(report.rollback_failures.contains(&"postCommitContainment"));
    }
}

#[test]
fn seal_action_failure_is_always_contained_as_roll_forward_only() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let seal = preview
        .steps
        .iter()
        .position(|step| step.id == "sealCandidateGenerationForFinalCommit")
        .unwrap();
    let mut lease = maintenance_lease(&preview);
    let mut executor = FakeExecutor {
        fail_apply_at: Some(seal - 1),
        ..Default::default()
    };

    let report = execute_with_test_executor(&preview, &mut lease, &mut executor);

    assert_eq!(report.status, "contained");
    assert_eq!(report.failed_step_cleanup, Some("postCommitContained"));
    assert_eq!(
        executor.post_commit_containments,
        ["sealCandidateGenerationForFinalCommit"]
    );
    assert!(executor.rollback_order.is_empty());
}

#[test]
fn final_commit_is_last_mutation_and_uses_a_transaction_bound_store() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let prior = installed(&install);
    let update = preview_update(&layout(), content(10), prior).unwrap();

    for preview in [&install, &update] {
        let runtime = preview
            .steps
            .iter()
            .position(|step| step.id == "startCommittedRuntime")
            .unwrap();
        let zero_residue = preview
            .steps
            .iter()
            .position(|step| step.id == "verifyOperationZeroResidue")
            .unwrap();
        let final_commit = preview
            .steps
            .iter()
            .position(|step| step.id == "persistFinalCommit")
            .unwrap();
        assert!(runtime < zero_residue && zero_residue < final_commit);
        assert_eq!(final_commit + 2, preview.steps.len());
        assert!(matches!(
            preview.steps[final_commit + 1].action,
            AuthorityMaintenanceAction::VerifyPostcommitServingRuntimeReadback { .. }
        ));
        assert!(preview
            .layout
            .finalizer_commit_store_root
            .contains(&preview.transaction_sha256));
        assert_ne!(
            preview.layout.finalizer_commit_store_root,
            preview.layout.state_maintenance_root
        );
        assert!(preview.steps.iter().any(|step| {
            step.id == "createFinalizerCommitStoreRoot"
                && matches!(
                    &step.action,
                    AuthorityMaintenanceAction::CreateDirectory {
                        path,
                        parent_path,
                        create_new: true,
                        never_reuse: true,
                        ..
                    } if path == &preview.layout.finalizer_commit_store_root
                        && parent_path == &preview.layout.finalizer_commits_root
                )
        }));
    }

    assert_ne!(
        install.layout.finalizer_commit_store_root,
        update.layout.finalizer_commit_store_root
    );
    let retirement = update
        .steps
        .iter()
        .position(|step| step.id == "finalizePriorRetirementTombstone")
        .unwrap();
    let zero_residue = update
        .steps
        .iter()
        .position(|step| step.id == "verifyOperationZeroResidue")
        .unwrap();
    assert!(retirement < zero_residue);
}

#[test]
fn every_update_failure_at_or_after_seal_rolls_forward_without_head_or_runtime_rollback() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let prior = installed(&install);
    let update = preview_update(&layout(), content(10), prior).unwrap();
    let seal = update
        .steps
        .iter()
        .position(|step| step.id == "sealCandidateGenerationForFinalCommit")
        .unwrap();

    for failed_step in seal..update.steps.len() {
        let mut lease = maintenance_lease(&update);
        let mut executor = FakeExecutor {
            fail_apply_at: Some(failed_step - 1),
            ..Default::default()
        };
        let report = execute_with_test_executor(&update, &mut lease, &mut executor);
        assert_eq!(
            report.status, "contained",
            "{}",
            update.steps[failed_step].id
        );
        assert_eq!(report.failed_step_cleanup, Some("postCommitContained"));
        assert_eq!(
            executor.post_commit_containments,
            [update.steps[failed_step].id]
        );
        assert!(executor.rollback_order.is_empty());
    }
}

#[test]
fn post_commit_failures_contain_without_reviving_retired_generation() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let prior = installed(&install);
    let previews = [
        preview_update(&layout(), content(10), prior.clone()).unwrap(),
        preview_retire(&layout(), prior).unwrap(),
    ];
    for preview in previews {
        let failed = preview.steps.len() - 1;
        assert!(preview.steps[..failed].iter().any(|step| matches!(
            &step.action,
            AuthorityMaintenanceAction::FinalizeRetirementTombstoneAtomic {
                irreversible_commit: true,
                post_commit_failure_policy: "containWithoutGenerationRevival",
                ..
            }
        )));
        let mut lease = maintenance_lease(&preview);
        let mut executor = FakeExecutor {
            fail_apply_at: Some(failed - 1),
            ..Default::default()
        };
        let report = execute_with_test_executor(&preview, &mut lease, &mut executor);
        assert_eq!(report.status, "contained");
        assert_eq!(report.failed_step_cleanup, Some("postCommitContained"));
        assert_eq!(
            executor.post_commit_containments,
            vec![preview.steps[failed].id]
        );
        assert!(executor.rollback_order.is_empty());
        assert!(!executor.rollback_order.contains(&"configureServiceExact"));
        assert!(report
            .blockers
            .contains(&"authority_post_commit_protected_readback_required"));

        let mut lease = maintenance_lease(&preview);
        let mut executor = FakeExecutor {
            fail_apply_at: Some(failed - 1),
            fail_post_commit_containment: true,
            ..Default::default()
        };
        let report = execute_with_test_executor(&preview, &mut lease, &mut executor);
        assert_eq!(report.status, "recoveryRequired");
        assert!(report.rollback_failures.contains(&"postCommitContainment"));
    }
}

#[test]
fn failure_after_irreversible_step_commit_contains_without_generic_cleanup() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let prior = installed(&install);
    let previews = [
        preview_update(&layout(), content(10), prior.clone()).unwrap(),
        preview_retire(&layout(), prior).unwrap(),
    ];
    for preview in previews {
        let irreversible_step = preview
            .steps
            .iter()
            .position(|step| {
                matches!(
                    &step.action,
                    AuthorityMaintenanceAction::FinalizeRetirementTombstoneAtomic {
                        irreversible_commit: true,
                        ..
                    }
                )
            })
            .unwrap();
        let mut lease = maintenance_lease(&preview);
        let mut executor = FakeExecutor {
            fail_after_irreversible_commit_at: Some(irreversible_step - 1),
            ..Default::default()
        };
        let report = execute_with_test_executor(&preview, &mut lease, &mut executor);
        assert_eq!(report.status, "contained");
        assert_eq!(
            report.failed_step,
            Some(preview.steps[irreversible_step].id)
        );
        assert_eq!(report.failed_step_cleanup, Some("postCommitContained"));
        assert!(executor.rollback_order.is_empty());
        assert_eq!(
            executor.post_commit_containments,
            vec![preview.steps[irreversible_step].id]
        );
        assert_eq!(executor.recovery_seal_calls, 1);
        assert!(report
            .blockers
            .contains(&"authority_post_commit_protected_readback_required"));
    }
}

#[test]
fn every_install_update_and_retire_fault_cleans_current_step_first() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let prior = installed(&install);
    let previews = vec![
        install,
        preview_update(&layout(), content(10), prior.clone()).unwrap(),
        preview_retire(&layout(), prior).unwrap(),
    ];
    for preview in previews {
        for failed_step in 1..preview.steps.len() {
            let mut lease = maintenance_lease(&preview);
            let mut executor = FakeExecutor {
                fail_apply_at: Some(failed_step - 1),
                ..Default::default()
            };
            let report = execute_with_test_executor(&preview, &mut lease, &mut executor);
            assert!(!report.trusted_boundary_ready);
            assert_eq!(report.failed_step, Some(preview.steps[failed_step].id));
            let current_step_is_roll_forward_only = matches!(
                &preview.steps[failed_step].action,
                AuthorityMaintenanceAction::SealCandidateGenerationForFinalCommit {
                    irreversible_roll_forward_boundary: true,
                    ..
                }
            );
            let post_commit = current_step_is_roll_forward_only
                || preview.steps[..failed_step].iter().any(|step| {
                    matches!(
                        &step.action,
                        AuthorityMaintenanceAction::FinalizeRetirementTombstoneAtomic {
                            irreversible_commit: true,
                            ..
                        } | AuthorityMaintenanceAction::SealCandidateGenerationForFinalCommit {
                            irreversible_roll_forward_boundary: true,
                            ..
                        }
                    )
                });
            let seal_expected = post_commit
                || rollback_requires_recovery_seal(
                    &preview.steps[failed_step].failed_apply_cleanup,
                )
                || preview.steps[1..failed_step]
                    .iter()
                    .any(|step| rollback_requires_recovery_seal(&step.rollback));
            assert_eq!(
                report.status,
                if seal_expected {
                    "contained"
                } else {
                    "rolledBack"
                }
            );
            assert_eq!(report.failure_cleanup_verified, Some(true));
            assert_eq!(
                report.failed_step_cleanup,
                Some(if post_commit {
                    "postCommitContained"
                } else {
                    rollback_resolution(&preview.steps[failed_step].failed_apply_cleanup)
                })
            );
            if post_commit {
                assert!(executor.rollback_order.is_empty());
            } else {
                assert_eq!(executor.rollback_order[0], preview.steps[failed_step].id);
                let expected_prior = preview.steps[1..failed_step]
                    .iter()
                    .rev()
                    .map(|step| step.id)
                    .collect::<Vec<_>>();
                assert_eq!(&executor.rollback_order[1..], expected_prior.as_slice());
            }
        }
        let mut lease = maintenance_lease(&preview);
        let report = execute_with_test_executor(&preview, &mut lease, &mut FakeExecutor::default());
        assert_eq!(report.status, "committed");
        assert_eq!(report.journal_terminal, Some("committed"));
        assert!(!report.trusted_boundary_ready);
        assert_eq!(report.failure_cleanup_verified, None);
    }
}

#[test]
fn generation_recovery_seal_is_not_removed_with_completed_parent_steps() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let failed_step = preview
        .steps
        .iter()
        .position(|step| step.id == "createBinaryGenerationDirectory")
        .unwrap();
    let recovery_root = preview
        .steps
        .iter()
        .position(|step| step.id == "ensureRecoveriesRoot")
        .unwrap();
    assert!(recovery_root < failed_step);
    assert!(matches!(
        &preview.steps[recovery_root].rollback,
        AuthorityRollbackAction::None
    ));
    assert!(preview.steps[..failed_step].iter().all(|step| !matches!(
        &step.rollback,
        AuthorityRollbackAction::RestoreProtectedDirectoryState { path }
            if path == &preview.layout.recoveries_root
    )));
    let mut executor = FakeExecutor {
        fail_apply_at: Some(failed_step - 1),
        ..Default::default()
    };
    let mut lease = maintenance_lease(&preview);
    let report = execute_with_test_executor(&preview, &mut lease, &mut executor);
    assert_eq!(report.status, "contained");
    assert_eq!(report.failed_step_cleanup, Some("recoverySealed"));
    assert_eq!(report.failure_cleanup_verified, Some(true));
    assert_eq!(report.recovery_seal, Some("created"));
    assert_eq!(executor.recovery_seal_calls, 1);
}

#[test]
fn rollback_fault_enters_recovery_required_without_ready_claim() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let failed_step = preview.steps[3].id;
    let mut executor = FakeExecutor {
        fail_apply_at: Some(2),
        fail_rollback_at: Some(failed_step),
        ..Default::default()
    };
    let mut lease = maintenance_lease(&preview);
    let report = execute_with_test_executor(&preview, &mut lease, &mut executor);
    assert_eq!(report.status, "recoveryRequired");
    assert_eq!(report.failed_step_cleanup, Some("uncertain"));
    assert_eq!(report.failure_cleanup_verified, Some(false));
    assert_eq!(report.rollback_failures, vec![failed_step]);
    assert_eq!(executor.rollback_order[0], failed_step);
    assert!(report
        .blockers
        .contains(&"authority_maintenance_cleanup_uncertain"));
    assert!(!report.trusted_boundary_ready);
}

#[test]
fn worker_capsule_binds_plan_generation_sources_consent_and_no_paths_or_private_key() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let lease = maintenance_lease(&preview);
    let capsule = MaintenanceWorkerCapsule::for_install(
        &preview, &lease, [0x41; 32], [0x42; 32], 1_000_000, 1_300_000,
    )
    .unwrap();
    let encoded = capsule.canonical_bytes().unwrap();
    let decoded =
        MaintenanceWorkerCapsule::parse_canonical(&encoded, &capsule.digest().unwrap()).unwrap();
    assert_eq!(decoded, capsule);
    assert_eq!(
        decoded.plan_sha256().unwrap(),
        preview.plan_sha256().unwrap()
    );
    assert_eq!(
        decoded.generation().unwrap(),
        preview.generation_sha256().unwrap()
    );
    assert_eq!(
        decoded.transaction_sha256().unwrap(),
        decode_hex_32(&preview.transaction_sha256).unwrap()
    );
    assert_ne!(decoded.digest().unwrap(), [0; 32]);
    let projected = std::str::from_utf8(&encoded).unwrap();
    assert!(!projected.contains(r"C:\"));
    assert!(projected.contains("\"sourcePathsPersisted\":false"));
    assert!(projected.contains("\"privateKeyMaterialPersisted\":false"));
    assert!(projected.contains("\"lifecycleDriver\""));
    assert!(projected.contains("\"bridgeLauncher\""));

    let mut legacy_shape: serde_json::Value = serde_json::from_slice(&encoded).unwrap();
    legacy_shape["schema"] = serde_json::Value::String(
        "vrcforge.primitive_evidence_authority_worker_capsule.v1".to_string(),
    );
    legacy_shape
        .as_object_mut()
        .unwrap()
        .remove("lifecycleDriver");
    legacy_shape
        .as_object_mut()
        .unwrap()
        .remove("bridgeLauncher");
    assert!(serde_json::from_value::<MaintenanceWorkerCapsule>(legacy_shape).is_err());
    let mut noncanonical = encoded.clone();
    noncanonical.push(b'\n');
    assert_eq!(
        MaintenanceWorkerCapsule::parse_canonical(&noncanonical, &capsule.digest().unwrap())
            .unwrap_err()
            .code(),
        "authority_worker_capsule_not_canonical"
    );

    let mut tampered = capsule.clone();
    tampered.worker_service_account = "interactive".to_string();
    assert_eq!(
        tampered.validate().unwrap_err().code(),
        "authority_worker_capsule_binding_invalid"
    );
}

#[test]
fn action_time_consent_is_exact_short_lived_local_and_single_use_bound() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let expected = content_from_projection(&preview.content).unwrap();
    let consent = serde_json::to_vec(&serde_json::json!({
        "schema": ACTION_TIME_CONSENT_SCHEMA,
        "operation": "install",
        "planSha256": preview.plan_sha256,
        "generation": preview.generation,
        "serviceSha256": hex_lower(expected.service.sha256()),
        "controllerSha256": hex_lower(expected.controller.sha256()),
        "installHelperSha256": hex_lower(expected.install_helper.sha256()),
        "lifecycleDriverSha256": hex_lower(expected.lifecycle_driver.sha256()),
        "bridgeLauncherSha256": hex_lower(expected.bridge_launcher.sha256()),
        "runtimeSourceManifestSha256": hex_lower(expected.runtime_source_manifest.sha256()),
        "transactionNonceSha256": hex_lower(&[0x43; 32]),
        "createdUnixMillis": 1_000_000,
        "expiresUnixMillis": 1_300_000,
        "approved": true,
        "localOnly": true,
        "singleUse": true,
    }))
    .unwrap();
    assert_eq!(
        validate_action_time_install_consent(&consent, &preview, &expected, 1_100_000).unwrap(),
        [0x43; 32]
    );
    assert_eq!(
        validate_action_time_install_consent(&consent, &preview, &expected, 1_300_001)
            .unwrap_err()
            .code(),
        "authority_action_time_consent_invalid"
    );
    let mut legacy: serde_json::Value = serde_json::from_slice(&consent).unwrap();
    legacy["schema"] = serde_json::Value::String(
        "vrcforge.primitive_evidence_authority_action_time_consent.v1".to_string(),
    );
    legacy
        .as_object_mut()
        .unwrap()
        .remove("lifecycleDriverSha256");
    legacy
        .as_object_mut()
        .unwrap()
        .remove("bridgeLauncherSha256");
    assert_eq!(
        validate_action_time_install_consent(
            &serde_json::to_vec(&legacy).unwrap(),
            &preview,
            &expected,
            1_100_000,
        )
        .unwrap_err()
        .code(),
        "authority_action_time_consent_invalid"
    );
    let mut wrong: serde_json::Value = serde_json::from_slice(&consent).unwrap();
    wrong["generation"] = serde_json::Value::String(hex_lower(&[0x44; 32]));
    assert_eq!(
        validate_action_time_install_consent(
            &serde_json::to_vec(&wrong).unwrap(),
            &preview,
            &expected,
            1_100_000,
        )
        .unwrap_err()
        .code(),
        "authority_action_time_consent_invalid"
    );
}

#[cfg(windows)]
#[test]
fn native_preparation_seals_request_sources_consent_and_capsule_as_one_capability() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let expected = content_from_projection(&preview.content).unwrap();
    let preparation = NativeInstallPreparation {
        preview: preview.clone(),
        content: expected.clone(),
        lease: maintenance_lease(&preview),
    };
    preparation
        .validate_request_binding(
            preview.plan_sha256().unwrap(),
            preview.generation_sha256().unwrap(),
            *expected.service.sha256(),
            *expected.controller.sha256(),
            *expected.install_helper.sha256(),
            *expected.lifecycle_driver.sha256(),
            *expected.bridge_launcher.sha256(),
            *expected.runtime_source_manifest.sha256(),
        )
        .unwrap();
    assert_eq!(
        preparation
            .validate_request_binding(
                preview.plan_sha256().unwrap(),
                preview.generation_sha256().unwrap(),
                [0x91; 32],
                *expected.controller.sha256(),
                *expected.install_helper.sha256(),
                *expected.lifecycle_driver.sha256(),
                *expected.bridge_launcher.sha256(),
                *expected.runtime_source_manifest.sha256(),
            )
            .unwrap_err()
            .code(),
        "authority_action_time_content_binding_mismatch"
    );
    let consent = serde_json::to_vec(&serde_json::json!({
        "schema": ACTION_TIME_CONSENT_SCHEMA,
        "operation": "install",
        "planSha256": preview.plan_sha256,
        "generation": preview.generation,
        "serviceSha256": hex_lower(expected.service.sha256()),
        "controllerSha256": hex_lower(expected.controller.sha256()),
        "installHelperSha256": hex_lower(expected.install_helper.sha256()),
        "lifecycleDriverSha256": hex_lower(expected.lifecycle_driver.sha256()),
        "bridgeLauncherSha256": hex_lower(expected.bridge_launcher.sha256()),
        "runtimeSourceManifestSha256": hex_lower(expected.runtime_source_manifest.sha256()),
        "transactionNonceSha256": hex_lower(&[0x92; 32]),
        "createdUnixMillis": 2_000_000,
        "expiresUnixMillis": 2_300_000,
        "approved": true,
        "localOnly": true,
        "singleUse": true,
    }))
    .unwrap();
    let consent_sha256: [u8; 32] = Sha256::digest(&consent).into();
    let prepared = preparation
        .seal_for_worker(&consent, consent_sha256, 2_100_000)
        .unwrap();
    assert!(prepared.lease.is_live());
    assert_eq!(prepared.capsule_sha256, prepared.capsule.digest().unwrap());
    assert_eq!(
        MaintenanceWorkerCapsule::parse_canonical(
            &prepared.capsule_bytes,
            &prepared.capsule_sha256,
        )
        .unwrap(),
        prepared.capsule
    );
}

#[cfg(windows)]
fn prepared_native_operation(
    preview: AuthorityMaintenancePreview,
    nonce_byte: u8,
) -> PreparedNativeInstallWorker {
    let expected = content_from_projection(&preview.content).unwrap();
    let preparation = NativeInstallPreparation {
        preview: preview.clone(),
        content: expected.clone(),
        lease: maintenance_lease(&preview),
    };
    let consent = serde_json::to_vec(&serde_json::json!({
        "schema": ACTION_TIME_CONSENT_SCHEMA,
        "operation": preview.operation(),
        "planSha256": preview.plan_sha256,
        "generation": preview.generation,
        "serviceSha256": hex_lower(expected.service.sha256()),
        "controllerSha256": hex_lower(expected.controller.sha256()),
        "installHelperSha256": hex_lower(expected.install_helper.sha256()),
        "lifecycleDriverSha256": hex_lower(expected.lifecycle_driver.sha256()),
        "bridgeLauncherSha256": hex_lower(expected.bridge_launcher.sha256()),
        "runtimeSourceManifestSha256": hex_lower(expected.runtime_source_manifest.sha256()),
        "transactionNonceSha256": hex_lower(&[nonce_byte; 32]),
        "createdUnixMillis": 3_000_000,
        "expiresUnixMillis": 3_300_000,
        "approved": true,
        "localOnly": true,
        "singleUse": true,
    }))
    .unwrap();
    let consent_sha256 = Sha256::digest(&consent).into();
    preparation
        .seal_for_worker(&consent, consent_sha256, 3_100_000)
        .unwrap()
}

#[cfg(windows)]
#[derive(Default)]
struct NativeMaintenanceMock {
    fail_at: Option<NativeMaintenanceMutationPhase>,
    fail_containment: bool,
    applied: Vec<(
        AuthorityMaintenanceOperation,
        NativeMaintenanceMutationPhase,
    )>,
    contained: Vec<(
        AuthorityMaintenanceOperation,
        NativeMaintenanceMutationPhase,
        NativeMaintenanceContainment,
    )>,
}

#[cfg(windows)]
impl NativeMaintenanceBackend for NativeMaintenanceMock {
    fn apply_phase(
        &mut self,
        _prepared: &mut PreparedNativeInstallWorker,
        operation: AuthorityMaintenanceOperation,
        phase: NativeMaintenanceMutationPhase,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.applied.push((operation, phase));
        if self.fail_at == Some(phase) {
            Err(AuthorityMaintenanceError(
                "authority_native_test_injected_failure",
            ))
        } else {
            Ok(())
        }
    }

    fn contain_failure(
        &mut self,
        _prepared: &mut PreparedNativeInstallWorker,
        operation: AuthorityMaintenanceOperation,
        failed_phase: NativeMaintenanceMutationPhase,
        containment: NativeMaintenanceContainment,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.contained.push((operation, failed_phase, containment));
        if self.fail_containment {
            Err(AuthorityMaintenanceError(
                "authority_native_test_injected_containment_failure",
            ))
        } else {
            Ok(())
        }
    }
}

#[cfg(windows)]
fn native_operation_previews() -> Vec<AuthorityMaintenancePreview> {
    let initial = preview_install(&layout(), content(1)).unwrap();
    let prior = installed(&initial);
    vec![
        initial,
        preview_update(&layout(), content(10), prior.clone()).unwrap(),
        preview_retire(&layout(), prior).unwrap(),
    ]
}

#[cfg(windows)]
#[test]
fn native_maintenance_gate_is_closed_before_every_real_backend_call() {
    for (index, preview) in native_operation_previews().into_iter().enumerate() {
        let operation = preview.operation();
        let retirement = operation == AuthorityMaintenanceOperation::Retire;
        let prepared = prepared_native_operation(preview, 0xa0 + index as u8);
        let mutation = validate_prepared_native_maintenance(&prepared, 3_100_000).unwrap();
        assert_eq!(mutation.schema, NATIVE_MAINTENANCE_MUTATION_SCHEMA);
        assert_eq!(
            mutation.ordered_phases,
            native_maintenance_phases(operation)
        );
        assert!(!mutation.production_mutation_enabled);
        assert!(mutation.worker_service_backend_connected);
        assert!(!mutation.native_transaction_executor_connected);
        assert!(mutation.durable_phase_receipt_required_before_advance);
        assert!(mutation.write_ahead_intent_required_for_partial_staging);
        assert_eq!(
            mutation.candidate_validation_required_before_commit,
            !retirement
        );
        assert_eq!(
            mutation.seal_complete_required_before_active_head,
            !retirement
        );
        assert_eq!(
            mutation.active_head_cas_required_before_runtime_start,
            !retirement
        );
        assert_eq!(
            mutation.active_head_cas_required_before_retirement_commit,
            retirement
        );
        assert_eq!(
            mutation.final_commit_required_after_runtime_handshake,
            !retirement
        );
        assert_eq!(mutation.retirement_commit_required, retirement);
        assert_eq!(
            mutation.zero_residue_required_before_final_commit,
            !retirement
        );
        assert_eq!(
            mutation.zero_residue_required_before_retirement_commit,
            retirement
        );
        assert_eq!(
            mutation.candidate_and_runtime_process_identity_must_differ,
            !retirement
        );
        assert!(mutation.system_worker_self_wait_forbidden);
        assert!(mutation.system_worker_self_delete_forbidden);
        assert_eq!(
            mutation.committed_runtime_started_only_after_worker_exit,
            !retirement
        );
        assert!(mutation.stop_wait_delete_required);
        assert!(mutation.exact_absence_readback_required);
        let mut expected_blockers = vec![
            "authority_native_mutation_disabled",
            "authority_native_transaction_executor_not_connected",
        ];
        if retirement {
            expected_blockers.push("authority_native_retirement_protocol_not_connected");
        }
        assert_eq!(mutation.blockers, expected_blockers);

        let mut backend = NativeMaintenanceMock::default();
        assert_eq!(
            execute_prepared_native_maintenance_with_backend(
                prepared,
                NativeMutationGate::production(),
                3_100_000,
                &mut backend,
            )
            .unwrap_err()
            .code(),
            "authority_native_mutation_disabled"
        );
        assert!(backend.applied.is_empty());
        assert!(backend.contained.is_empty());
    }
}

#[cfg(windows)]
#[test]
fn every_native_operation_uses_one_ordered_phase_contract() {
    for (index, preview) in native_operation_previews().into_iter().enumerate() {
        let operation = preview.operation();
        let prepared = prepared_native_operation(preview, 0xb0 + index as u8);
        let mut backend = NativeMaintenanceMock::default();
        let report = execute_prepared_native_maintenance_with_backend(
            prepared,
            NativeMutationGate::enabled_for_test(),
            3_100_000,
            &mut backend,
        )
        .unwrap();
        assert_eq!(report.status, "committed");
        assert_eq!(report.journal_terminal, Some("committed"));
        assert!(!report.trusted_boundary_ready);
        assert_eq!(backend.contained, Vec::new());
        assert_eq!(
            backend.applied,
            native_maintenance_phases(operation)
                .iter()
                .copied()
                .map(|phase| (operation, phase))
                .collect::<Vec<_>>()
        );
    }
}

#[cfg(windows)]
#[test]
fn native_phase_ownership_separates_transaction_worker_and_finalizer() {
    let exit_ready = NATIVE_INSTALL_MAINTENANCE_PHASES
        .iter()
        .position(|phase| *phase == NativeMaintenanceMutationPhase::AwaitSystemExitReady)
        .unwrap();
    let finalize = NATIVE_INSTALL_MAINTENANCE_PHASES
        .iter()
        .position(|phase| *phase == NativeMaintenanceMutationPhase::StopWaitDeleteWorker)
        .unwrap();
    let seal = NATIVE_INSTALL_MAINTENANCE_PHASES
        .iter()
        .position(|phase| *phase == NativeMaintenanceMutationPhase::SealCandidateGeneration)
        .unwrap();
    let head = NATIVE_INSTALL_MAINTENANCE_PHASES
        .iter()
        .position(|phase| *phase == NativeMaintenanceMutationPhase::AdvanceActiveHead)
        .unwrap();
    let activate = NATIVE_INSTALL_MAINTENANCE_PHASES
        .iter()
        .position(|phase| *phase == NativeMaintenanceMutationPhase::StartCommittedRuntime)
        .unwrap();
    let commit = NATIVE_INSTALL_MAINTENANCE_PHASES
        .iter()
        .position(|phase| *phase == NativeMaintenanceMutationPhase::PersistFinalCommit)
        .unwrap();
    let verify = NATIVE_INSTALL_MAINTENANCE_PHASES
        .iter()
        .position(|phase| *phase == NativeMaintenanceMutationPhase::VerifyZeroResidue)
        .unwrap();
    let postcommit = NATIVE_INSTALL_MAINTENANCE_PHASES
        .iter()
        .position(|phase| *phase == NativeMaintenanceMutationPhase::VerifyPostcommitReadback)
        .unwrap();
    assert!(
        exit_ready < finalize
            && finalize < seal
            && seal < head
            && head < activate
            && activate < verify
            && verify < commit
            && commit < postcommit
    );
    assert!(NATIVE_INSTALL_MAINTENANCE_PHASES
        .iter()
        .all(|phase| !matches!(
            phase,
            NativeMaintenanceMutationPhase::VerifyDormantSuccessor
                | NativeMaintenanceMutationPhase::StagePriorRetirement
                | NativeMaintenanceMutationPhase::FinalizePriorRetirement
                | NativeMaintenanceMutationPhase::VerifyUpdateZeroResidue
        )));

    let update_dormant = NATIVE_UPDATE_MAINTENANCE_PHASES
        .iter()
        .position(|phase| *phase == NativeMaintenanceMutationPhase::VerifyDormantSuccessor)
        .unwrap();
    let update_stage = NATIVE_UPDATE_MAINTENANCE_PHASES
        .iter()
        .position(|phase| *phase == NativeMaintenanceMutationPhase::StagePriorRetirement)
        .unwrap();
    let update_finalize = NATIVE_UPDATE_MAINTENANCE_PHASES
        .iter()
        .position(|phase| *phase == NativeMaintenanceMutationPhase::FinalizePriorRetirement)
        .unwrap();
    let update_residue = NATIVE_UPDATE_MAINTENANCE_PHASES
        .iter()
        .position(|phase| *phase == NativeMaintenanceMutationPhase::VerifyUpdateZeroResidue)
        .unwrap();
    let update_commit = NATIVE_UPDATE_MAINTENANCE_PHASES
        .iter()
        .position(|phase| *phase == NativeMaintenanceMutationPhase::PersistFinalCommit)
        .unwrap();
    let update_postcommit = NATIVE_UPDATE_MAINTENANCE_PHASES
        .iter()
        .position(|phase| *phase == NativeMaintenanceMutationPhase::VerifyPostcommitReadback)
        .unwrap();
    assert!(
        update_dormant < update_stage
            && update_stage < update_finalize
            && update_finalize < update_residue
            && update_residue < update_commit
            && update_commit < update_postcommit
    );
    assert_eq!(
        NativeMaintenanceMutationPhase::ConsumeNonceAndStartTransaction.containment(),
        NativeMaintenanceContainment::InterruptedTransaction
    );
    assert_eq!(
        NativeMaintenanceMutationPhase::AwaitSystemExitReady.containment(),
        NativeMaintenanceContainment::TransactionOutcomeBound
    );
    assert_eq!(
        NativeMaintenanceMutationPhase::StopWaitDeleteWorker.containment(),
        NativeMaintenanceContainment::FinalizerBeforeSeal
    );
    assert_eq!(
        NativeMaintenanceMutationPhase::SealCandidateGeneration.containment(),
        NativeMaintenanceContainment::ProbeSealCompleteDurability
    );
    for phase in [
        NativeMaintenanceMutationPhase::AdvanceActiveHead,
        NativeMaintenanceMutationPhase::StartCommittedRuntime,
    ] {
        assert_eq!(
            phase.containment(),
            NativeMaintenanceContainment::ResumeFromSealComplete
        );
    }
    assert_eq!(
        NativeMaintenanceMutationPhase::PersistFinalCommit.containment(),
        NativeMaintenanceContainment::ProbeFinalCommitDurability
    );
    assert_eq!(
        NativeMaintenanceMutationPhase::VerifyZeroResidue.containment(),
        NativeMaintenanceContainment::ResumeFromSealComplete
    );
    for phase in [
        NativeMaintenanceMutationPhase::VerifyDormantSuccessor,
        NativeMaintenanceMutationPhase::StagePriorRetirement,
    ] {
        assert_eq!(
            phase.containment(),
            NativeMaintenanceContainment::ResumeUpdatePriorRetirement
        );
    }
    assert_eq!(
        NativeMaintenanceMutationPhase::FinalizePriorRetirement.containment(),
        NativeMaintenanceContainment::ProbeUpdateRetirementDurability
    );
    assert_eq!(
        NativeMaintenanceMutationPhase::VerifyUpdateZeroResidue.containment(),
        NativeMaintenanceContainment::ResumeUpdateAfterRetirementCommit
    );
    assert_eq!(
        NativeMaintenanceMutationPhase::VerifyPostcommitReadback.containment(),
        NativeMaintenanceContainment::ResumeCommittedRuntimeAndVerify
    );

    assert!(NATIVE_RETIREMENT_MAINTENANCE_PHASES
        .iter()
        .all(|phase| !matches!(
            phase,
            NativeMaintenanceMutationPhase::SealCandidateGeneration
                | NativeMaintenanceMutationPhase::AdvanceActiveHead
                | NativeMaintenanceMutationPhase::StartCommittedRuntime
                | NativeMaintenanceMutationPhase::VerifyZeroResidue
                | NativeMaintenanceMutationPhase::VerifyDormantSuccessor
                | NativeMaintenanceMutationPhase::StagePriorRetirement
                | NativeMaintenanceMutationPhase::FinalizePriorRetirement
                | NativeMaintenanceMutationPhase::VerifyUpdateZeroResidue
                | NativeMaintenanceMutationPhase::PersistFinalCommit
                | NativeMaintenanceMutationPhase::VerifyPostcommitReadback
        )));
    assert_eq!(
        &NATIVE_RETIREMENT_MAINTENANCE_PHASES[8..],
        &[
            NativeMaintenanceMutationPhase::FinalizeRetirement,
            NativeMaintenanceMutationPhase::VerifyRetirementZeroResidue,
            NativeMaintenanceMutationPhase::PersistRetirementCommit,
            NativeMaintenanceMutationPhase::VerifyPostretirementReadback,
        ]
    );
    assert_eq!(
        NativeMaintenanceMutationPhase::FinalizeRetirement.containment(),
        NativeMaintenanceContainment::ProbeRetirementCommitDurability
    );
    assert_eq!(
        NativeMaintenanceMutationPhase::VerifyRetirementZeroResidue.containment(),
        NativeMaintenanceContainment::ResumeRetirementCommit
    );
    assert_eq!(
        NativeMaintenanceMutationPhase::PersistRetirementCommit.containment(),
        NativeMaintenanceContainment::ProbeRetirementCommitDurability
    );
    assert_eq!(
        NativeMaintenanceMutationPhase::VerifyPostretirementReadback.containment(),
        NativeMaintenanceContainment::ReadOnlyRetirementVerification
    );
}

#[cfg(windows)]
#[test]
fn native_worker_reports_ready_only_after_durable_prepare_and_before_execution() {
    use std::cell::{Cell, RefCell};
    use std::rc::Rc;

    let order = Rc::new(RefCell::new(Vec::new()));
    let prepare_order = Rc::clone(&order);
    let ready_order = Rc::clone(&order);
    let execute_order = Rc::clone(&order);
    let result = prepare_signal_and_execute_native_worker(
        move || {
            prepare_order.borrow_mut().push("prepare");
            Ok(7_u8)
        },
        move || {
            ready_order.borrow_mut().push("ready");
            Ok(())
        },
        move |transaction| {
            execute_order.borrow_mut().push("execute");
            Ok(transaction + 1)
        },
    )
    .unwrap();
    assert_eq!(result, 8);
    assert_eq!(*order.borrow(), ["prepare", "ready", "execute"]);

    let ready_called = Cell::new(false);
    let execute_called = Cell::new(false);
    assert_eq!(
        prepare_signal_and_execute_native_worker::<(), ()>(
            || Err(AuthorityMaintenanceError("prepare_failed")),
            || {
                ready_called.set(true);
                Ok(())
            },
            |_| {
                execute_called.set(true);
                Ok(())
            },
        )
        .unwrap_err()
        .code(),
        "prepare_failed"
    );
    assert!(!ready_called.get());
    assert!(!execute_called.get());

    let execute_called = Cell::new(false);
    assert_eq!(
        prepare_signal_and_execute_native_worker(
            || Ok(()),
            || Err("ready_failed"),
            |_| {
                execute_called.set(true);
                Ok(())
            },
        )
        .unwrap_err()
        .code(),
        "ready_failed"
    );
    assert!(!execute_called.get());
}

#[cfg(windows)]
#[test]
fn every_native_operation_phase_fault_enters_the_exact_containment_class() {
    for (operation_index, preview) in native_operation_previews().into_iter().enumerate() {
        let operation = preview.operation();
        for (phase_index, phase) in native_maintenance_phases(operation)
            .iter()
            .copied()
            .enumerate()
        {
            let prepared = prepared_native_operation(
                preview.clone(),
                0xc0 + operation_index as u8 * 10 + phase_index as u8,
            );
            let mut backend = NativeMaintenanceMock {
                fail_at: Some(phase),
                ..Default::default()
            };
            assert_eq!(
                execute_prepared_native_maintenance_with_backend(
                    prepared,
                    NativeMutationGate::enabled_for_test(),
                    3_100_000,
                    &mut backend,
                )
                .unwrap_err()
                .code(),
                "authority_native_test_injected_failure"
            );
            assert_eq!(backend.applied.last(), Some(&(operation, phase)));
            assert_eq!(
                backend.contained,
                vec![(operation, phase, phase.containment())]
            );
        }
    }
}

#[cfg(windows)]
#[test]
fn native_containment_failure_never_reports_the_original_fault_as_contained() {
    let preview = native_operation_previews().remove(0);
    let prepared = prepared_native_operation(preview, 0xe1);
    let mut backend = NativeMaintenanceMock {
        fail_at: Some(NativeMaintenanceMutationPhase::AwaitSystemExitReady),
        fail_containment: true,
        ..Default::default()
    };
    assert_eq!(
        execute_prepared_native_maintenance_with_backend(
            prepared,
            NativeMutationGate::enabled_for_test(),
            3_100_000,
            &mut backend,
        )
        .unwrap_err()
        .code(),
        "authority_native_mutation_containment_failed"
    );
    assert_eq!(backend.contained.len(), 1);
}

#[cfg(windows)]
#[test]
fn every_partial_staging_cleanup_fault_stops_before_later_mutations() {
    use super::worker_store_windows::{
        run_partial_staging_cleanup_steps, NativePartialStagingCleanupPhase,
        NATIVE_PARTIAL_STAGING_CLEANUP_PHASES,
    };

    for failed_phase in NATIVE_PARTIAL_STAGING_CLEANUP_PHASES {
        let mut attempted = Vec::new();
        assert_eq!(
            run_partial_staging_cleanup_steps(|phase| {
                attempted.push(phase);
                if phase == failed_phase {
                    Err(AuthorityMaintenanceError(
                        "authority_partial_staging_test_injected_failure",
                    ))
                } else {
                    Ok(())
                }
            })
            .unwrap_err()
            .code(),
            "authority_partial_staging_test_injected_failure"
        );
        let failed_index = NATIVE_PARTIAL_STAGING_CLEANUP_PHASES
            .iter()
            .position(|phase| *phase == failed_phase)
            .unwrap();
        assert_eq!(
            attempted,
            NATIVE_PARTIAL_STAGING_CLEANUP_PHASES[..=failed_index]
        );
    }

    let mut completed = Vec::new();
    run_partial_staging_cleanup_steps(|phase| {
        completed.push(phase);
        Ok(())
    })
    .unwrap();
    assert_eq!(completed, NATIVE_PARTIAL_STAGING_CLEANUP_PHASES);
    assert_eq!(
        completed[0],
        NativePartialStagingCleanupPhase::CloseSourceHandles
    );
    assert_eq!(
        completed.last(),
        Some(&NativePartialStagingCleanupPhase::FlushStateParent)
    );
}

#[cfg(windows)]
#[test]
fn native_worker_service_state_machine_rejects_skips_repeats_and_reordering() {
    use super::native_runtime_windows::{
        advance_native_worker_service_state, NativeWorkerServiceState,
    };

    let mut state = NativeWorkerServiceState::Initial;
    for phase in NATIVE_INSTALL_MAINTENANCE_PHASES {
        state = advance_native_worker_service_state(
            state,
            AuthorityMaintenanceOperation::Install,
            phase,
        )
        .unwrap();
    }
    assert_eq!(state, NativeWorkerServiceState::PostcommitReadbackVerified);

    let mut update_state = NativeWorkerServiceState::Initial;
    for phase in NATIVE_UPDATE_MAINTENANCE_PHASES {
        update_state = advance_native_worker_service_state(
            update_state,
            AuthorityMaintenanceOperation::Update,
            phase,
        )
        .unwrap();
    }
    assert_eq!(
        update_state,
        NativeWorkerServiceState::PostcommitReadbackVerified
    );

    let mut retirement_state = NativeWorkerServiceState::Initial;
    for phase in NATIVE_RETIREMENT_MAINTENANCE_PHASES {
        retirement_state = advance_native_worker_service_state(
            retirement_state,
            AuthorityMaintenanceOperation::Retire,
            phase,
        )
        .unwrap();
    }
    assert_eq!(
        retirement_state,
        NativeWorkerServiceState::PostretirementReadbackVerified
    );

    for first_phase in NATIVE_INSTALL_MAINTENANCE_PHASES
        .into_iter()
        .chain(NATIVE_UPDATE_MAINTENANCE_PHASES)
        .chain(NATIVE_RETIREMENT_MAINTENANCE_PHASES)
        .filter(|phase| *phase != NativeMaintenanceMutationPhase::PersistBootstrap)
    {
        assert_eq!(
            advance_native_worker_service_state(
                NativeWorkerServiceState::Initial,
                AuthorityMaintenanceOperation::Install,
                first_phase,
            )
            .unwrap_err()
            .code(),
            "authority_native_worker_state_invalid"
        );
    }
    assert_eq!(
        advance_native_worker_service_state(
            NativeWorkerServiceState::BootstrapPersisted,
            AuthorityMaintenanceOperation::Install,
            NativeMaintenanceMutationPhase::PersistBootstrap,
        )
        .unwrap_err()
        .code(),
        "authority_native_worker_state_invalid"
    );
    assert_eq!(
        advance_native_worker_service_state(
            NativeWorkerServiceState::PostcommitReadbackVerified,
            AuthorityMaintenanceOperation::Install,
            NativeMaintenanceMutationPhase::VerifyZeroResidue,
        )
        .unwrap_err()
        .code(),
        "authority_native_worker_state_invalid"
    );
    assert_eq!(
        advance_native_worker_service_state(
            NativeWorkerServiceState::WorkerServiceRemoved,
            AuthorityMaintenanceOperation::Retire,
            NativeMaintenanceMutationPhase::SealCandidateGeneration,
        )
        .unwrap_err()
        .code(),
        "authority_native_worker_state_invalid"
    );
    assert_eq!(
        advance_native_worker_service_state(
            NativeWorkerServiceState::WorkerServiceRemoved,
            AuthorityMaintenanceOperation::Install,
            NativeMaintenanceMutationPhase::FinalizeRetirement,
        )
        .unwrap_err()
        .code(),
        "authority_native_worker_state_invalid"
    );
}

#[test]
fn worker_launch_is_fixed_to_one_service_one_command_and_one_capsule() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let lease = maintenance_lease(&preview);
    let capsule = MaintenanceWorkerCapsule::for_install(
        &preview, &lease, [0x51; 32], [0x52; 32], 1_000_000, 1_300_000,
    )
    .unwrap();
    let capsule_sha256 = capsule.digest().unwrap();
    let executable = layout()
        .maintenance_worker_executable(&capsule_sha256)
        .unwrap();
    let contract = MaintenanceWorkerLaunchContract::new(&layout(), &capsule).unwrap();
    assert_eq!(contract.capsule_sha256().unwrap(), capsule_sha256);
    assert_eq!(
        contract.worker_image_sha256().unwrap(),
        *content_from_projection(&preview.content)
            .unwrap()
            .install_helper
            .sha256()
    );
    assert_eq!(contract.service_name, MAINTENANCE_WORKER_SERVICE_NAME);
    assert_eq!(contract.account, "LocalSystem");
    assert_eq!(contract.start_type, "demand");
    assert!(contract.stop_wait_delete_after_transaction);
    assert_eq!(
        contract.binary_command(),
        format!(
            "\"{}\" --maintenance-worker {}",
            executable.display(),
            hex_lower(&capsule_sha256)
        )
    );
    assert!(contract.worker_staging_contract_exact());
}

#[test]
fn worker_journal_recovery_is_same_capsule_only_at_every_crash_boundary() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let lease = maintenance_lease(&preview);
    let capsule = MaintenanceWorkerCapsule::for_install(
        &preview, &lease, [0x61; 32], [0x62; 32], 1_000_000, 1_300_000,
    )
    .unwrap();
    let capsule_sha256 = capsule.digest().unwrap();
    let (launch, intent, bootstrap) = worker_bootstrap_staging(&capsule);
    let bootstrap_bytes = bootstrap.canonical_bytes(&capsule, &launch).unwrap();
    assert_eq!(
        WorkerBootstrapStagingReceipt::parse_canonical(&bootstrap_bytes, &capsule, &launch,)
            .unwrap(),
        bootstrap
    );
    let intent_bytes = intent.canonical_bytes(&capsule, &launch).unwrap();
    assert_eq!(
        WorkerBootstrapIntentReceipt::parse_canonical(&intent_bytes, &capsule, &launch).unwrap(),
        intent
    );
    let mut records =
        vec![MaintenanceWorkerJournalRecord::first_intent(&capsule, &launch, &intent).unwrap()];
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &records,
            1_100_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ResumeSameCapsuleBeforeTransaction
    );
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &records,
            1_100_000,
            WorkerRecoveryEvidence::default(),
        )
        .unwrap_err()
        .code(),
        "authority_worker_recovery_evidence_missing"
    );
    records
        .push(authorize_capsule_staged(&capsule, &launch, &records, &intent, &bootstrap).unwrap());
    assert_eq!(
        validate_worker_journal(capsule_sha256, &records).unwrap(),
        MaintenanceWorkerRecoveryDisposition::ResumeSameCapsuleBeforeTransaction
    );
    let installed = content_from_projection(&preview.content).unwrap();
    let helper = WorkerProcessBinding::new(77, 9_001, *installed.install_helper.sha256());
    let worker = WorkerProcessBinding::new(202, 2_002, *installed.install_helper.sha256());
    let pipe =
        WorkerPipePreparedReceipt::from_observed(&capsule, &launch, helper.clone(), [0x63; 16])
            .unwrap();
    let replacement_pipe =
        WorkerPipePreparedReceipt::from_observed(&capsule, &launch, helper.clone(), [0x66; 16])
            .unwrap();
    let pipe_recovery = WorkerPipeRecoveryReceipt::from_observed(
        &capsule,
        &launch,
        &records,
        &pipe,
        &replacement_pipe,
        [0x67; 32],
    )
    .unwrap();
    let pipe_recovery_bytes = pipe_recovery.sealed_canonical_bytes().unwrap();
    assert_eq!(
        WorkerPipeRecoveryReceipt::parse_sealed_canonical(&pipe_recovery_bytes).unwrap(),
        pipe_recovery
    );
    assert_eq!(
        WorkerPipeRecoveryReceipt::from_observed(
            &capsule, &launch, &records, &pipe, &pipe, [0x67; 32],
        )
        .unwrap_err()
        .code(),
        "authority_worker_pipe_recovery_invalid"
    );
    let mut pipe_records = records.clone();
    pipe_records.push(authorize_pipe_prepared(&capsule, &launch, &pipe_records, &pipe).unwrap());
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &pipe_records,
            1_100_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                original_pipe: Some(&pipe),
                pipe: Some(&pipe),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ResumeSameCapsuleBeforeTransaction
    );
    let persisted_pipe_recovery = WorkerPipeRecoveryReceipt::from_observed(
        &capsule,
        &launch,
        &pipe_records,
        &pipe,
        &replacement_pipe,
        [0x68; 32],
    )
    .unwrap();
    pipe_records.push(
        authorize_pipe_recovered(
            &capsule,
            &launch,
            &pipe_records,
            &pipe,
            &persisted_pipe_recovery,
        )
        .unwrap(),
    );
    assert_eq!(
        validate_worker_recovery_bundle_with_containment(
            &capsule,
            &launch,
            &pipe_records,
            1_100_000,
            false,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                original_pipe: Some(&pipe),
                pipe: Some(&replacement_pipe),
                pipe_recovery: Some(&persisted_pipe_recovery),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
    );
    let service_created_after_recovery = ServiceCreatedReceipt::from_observed(
        &capsule,
        &launch,
        &bootstrap,
        &replacement_pipe,
        [0x69; 32],
        [0x6a; 32],
    )
    .unwrap();
    pipe_records.push(
        authorize_service_created_after_pipe(
            &capsule,
            &launch,
            &bootstrap,
            &replacement_pipe,
            &pipe_records,
            persisted_pipe_recovery.digest().unwrap(),
            &service_created_after_recovery,
        )
        .unwrap(),
    );
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &pipe_records,
            1_100_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                original_pipe: Some(&pipe),
                pipe: Some(&replacement_pipe),
                pipe_recovery: Some(&persisted_pipe_recovery),
                service_created: Some(&service_created_after_recovery),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ResumeSameCapsuleBeforeTransaction
    );
    let service_created = ServiceCreatedReceipt::from_observed(
        &capsule, &launch, &bootstrap, &pipe, [0x64; 32], [0x65; 32],
    )
    .unwrap();
    records.push(
        authorize_service_created(
            &capsule,
            &launch,
            &bootstrap,
            &pipe,
            &records,
            &service_created,
        )
        .unwrap(),
    );
    assert_eq!(
        validate_worker_recovery_bundle_with_containment(
            &capsule,
            &launch,
            &records,
            1_100_000,
            false,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
    );
    assert_eq!(
        validate_worker_recovery_bundle_with_containment(
            &capsule,
            &launch,
            &records,
            1_300_001,
            false,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
    );
    let handoff = WorkerHandleHandoffReceipt::from_observed(
        &capsule,
        helper,
        worker,
        &pipe,
        [0x100, 0x200, 0x300, 0x400, 0x500, 0x600],
    )
    .unwrap();
    let invocation_claim = WorkerInvocationClaimReceipt::new(
        &capsule,
        &launch,
        &bootstrap,
        &service_created,
        &pipe,
        &handoff,
        handoff.worker(),
    )
    .unwrap();
    assert_eq!(
        WorkerInvocationClaimReceipt::parse_sealed_canonical(
            &invocation_claim.sealed_canonical_bytes().unwrap()
        )
        .unwrap(),
        invocation_claim
    );
    let mut claimed_records = records.clone();
    claimed_records.push(
        authorize_worker_invocation_claimed(
            &capsule,
            &launch,
            &bootstrap,
            &service_created,
            &pipe,
            &handoff,
            handoff.worker(),
            &claimed_records,
            &invocation_claim,
        )
        .unwrap(),
    );
    assert_eq!(
        validate_worker_recovery_bundle_with_containment(
            &capsule,
            &launch,
            &claimed_records,
            1_100_000,
            false,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                invocation_claim: Some(&invocation_claim),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
    );
    let worker_started = WorkerStartedReceipt::from_observed(
        &capsule,
        &bootstrap,
        &service_created,
        &pipe,
        &handoff,
        true,
        true,
        0,
    )
    .unwrap();
    claimed_records.push(
        authorize_claimed_worker_started(
            &capsule,
            &launch,
            &bootstrap,
            &service_created,
            &pipe,
            &handoff,
            &invocation_claim,
            &claimed_records,
            &worker_started,
        )
        .unwrap(),
    );
    assert_eq!(
        validate_worker_recovery_bundle_with_containment(
            &capsule,
            &launch,
            &claimed_records,
            1_100_000,
            false,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                invocation_claim: Some(&invocation_claim),
                worker_started: Some(&worker_started),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
    );
    let mut wrong_worker_security_json = serde_json::to_value(&worker_started).unwrap();
    wrong_worker_security_json["workerLocalSystem"] = serde_json::Value::Bool(false);
    let wrong_worker_security: WorkerStartedReceipt =
        serde_json::from_value(wrong_worker_security_json).unwrap();
    assert_eq!(
        wrong_worker_security
            .validate(&capsule, &bootstrap, &service_created, &pipe, &handoff)
            .unwrap_err()
            .code(),
        "authority_worker_started_receipt_invalid"
    );
    records.push(
        authorize_worker_started(
            &capsule,
            &bootstrap,
            &service_created,
            &pipe,
            &handoff,
            &records,
            &worker_started,
        )
        .unwrap(),
    );
    assert_eq!(
        validate_worker_recovery_bundle_with_containment(
            &capsule,
            &launch,
            &records,
            1_100_000,
            false,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
    );
    let live_worker = WorkerLiveReadback::from_started_for_test(&worker_started).unwrap();
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &records,
            1_100_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                ..Default::default()
            },
        )
        .unwrap_err()
        .code(),
        "authority_worker_live_readback_missing"
    );
    assert_eq!(
        validate_worker_journal(capsule_sha256, &records).unwrap(),
        MaintenanceWorkerRecoveryDisposition::ResumeSameCapsuleBeforeTransaction
    );
    let journal_bytes = encode_worker_journal(capsule_sha256, &records).unwrap();
    assert_eq!(
        parse_worker_journal(&journal_bytes, capsule_sha256).unwrap(),
        records
    );
    let mut missing_final_newline = journal_bytes.clone();
    missing_final_newline.pop();
    assert_eq!(
        parse_worker_journal(&missing_final_newline, capsule_sha256)
            .unwrap_err()
            .code(),
        "authority_worker_journal_encoding_invalid"
    );
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &records,
            1_100_000,
            WorkerRecoveryEvidence::default(),
        )
        .unwrap_err()
        .code(),
        "authority_worker_recovery_evidence_missing"
    );
    let mut undurable_bootstrap_json = serde_json::to_value(&bootstrap).unwrap();
    undurable_bootstrap_json["directoriesFlushedAfterCreate"] = serde_json::Value::Bool(false);
    let undurable_bootstrap: WorkerBootstrapStagingReceipt =
        serde_json::from_value(undurable_bootstrap_json).unwrap();
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &records,
            1_100_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&undurable_bootstrap),
                ..Default::default()
            },
        )
        .unwrap_err()
        .code(),
        "authority_worker_bootstrap_staging_invalid"
    );
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &records,
            1_100_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                live_worker: Some(&live_worker),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ResumeSameCapsuleBeforeTransaction
    );
    let handoff_bytes = handoff.canonical_bytes(&capsule).unwrap();
    assert_eq!(
        WorkerHandleHandoffReceipt::parse_canonical(&handoff_bytes, &capsule).unwrap(),
        handoff
    );
    assert_eq!(handoff.helper().process_id(), 77);
    assert_eq!(handoff.helper().process_creation_time(), 9_001);
    assert_eq!(
        handoff.helper().image_sha256().unwrap(),
        *installed.install_helper.sha256()
    );
    assert_eq!(handoff.worker().process_id(), 202);
    assert_eq!(
        handoff.duplicated_target_handle_values(),
        [0x100, 0x200, 0x300, 0x400, 0x500, 0x600]
    );
    let identity_ledger = WorkerSourceIdentityLedger::from_observed(
        &capsule,
        records.last().unwrap(),
        &handoff,
        77,
        [0x64; 16],
        DurableStagedPayloadBinding::from_observed(
            StagedPayloadKind::Service,
            &capsule,
            lease.payloads.service.volume_serial,
            lease.payloads.service.file_id,
            lease.payloads.service.full_readback_receipt_sha256,
            77,
            [0x65; 16],
        )
        .unwrap(),
        DurableStagedPayloadBinding::from_observed(
            StagedPayloadKind::Controller,
            &capsule,
            lease.payloads.controller.volume_serial,
            lease.payloads.controller.file_id,
            lease.payloads.controller.full_readback_receipt_sha256,
            77,
            [0x67; 16],
        )
        .unwrap(),
        DurableStagedPayloadBinding::from_observed(
            StagedPayloadKind::InstallHelper,
            &capsule,
            lease.payloads.install_helper.volume_serial,
            lease.payloads.install_helper.file_id,
            lease.payloads.install_helper.full_readback_receipt_sha256,
            77,
            [0x69; 16],
        )
        .unwrap(),
        DurableStagedPayloadBinding::from_observed(
            StagedPayloadKind::LifecycleDriver,
            &capsule,
            lease.payloads.lifecycle_driver.volume_serial,
            lease.payloads.lifecycle_driver.file_id,
            lease.payloads.lifecycle_driver.full_readback_receipt_sha256,
            77,
            [0x6b; 16],
        )
        .unwrap(),
        DurableStagedPayloadBinding::from_observed(
            StagedPayloadKind::BridgeLauncher,
            &capsule,
            lease.payloads.bridge_launcher.volume_serial,
            lease.payloads.bridge_launcher.file_id,
            lease.payloads.bridge_launcher.full_readback_receipt_sha256,
            77,
            [0x6d; 16],
        )
        .unwrap(),
        DurableStagedPayloadBinding::from_observed(
            StagedPayloadKind::RuntimeSourceManifest,
            &capsule,
            lease.payloads.runtime_source_manifest.volume_serial,
            lease.payloads.runtime_source_manifest.file_id,
            lease
                .payloads
                .runtime_source_manifest
                .full_readback_receipt_sha256,
            77,
            [0x6f; 16],
        )
        .unwrap(),
    )
    .unwrap();
    let identity_ledger_bytes = identity_ledger
        .canonical_bytes(&capsule, records.last().unwrap(), &handoff)
        .unwrap();
    assert_eq!(
        WorkerSourceIdentityLedger::parse_canonical(
            &identity_ledger_bytes,
            &capsule,
            records.last().unwrap(),
            &handoff,
        )
        .unwrap(),
        identity_ledger
    );
    let mut forged_ledger_json = serde_json::to_value(&identity_ledger).unwrap();
    forged_ledger_json["service"]["fullReadbackReceiptSha256"] =
        serde_json::Value::String("7f".repeat(32));
    let forged_ledger: WorkerSourceIdentityLedger =
        serde_json::from_value(forged_ledger_json).unwrap();
    assert_eq!(
        forged_ledger
            .validate(&capsule, records.last().unwrap(), &handoff)
            .unwrap_err()
            .code(),
        "authority_worker_source_identity_ledger_invalid"
    );
    let staging = DurableSourceStagingReceipt::from_observed(
        &capsule,
        records.last().unwrap(),
        &handoff,
        &identity_ledger,
    )
    .unwrap();
    staging
        .validate_identity_ledger(
            &capsule,
            records.last().unwrap(),
            &handoff,
            &identity_ledger,
        )
        .unwrap();
    let source_staging_intent =
        WorkerSourceStagingIntentReceipt::new(&capsule, records.last().unwrap(), &handoff).unwrap();
    let source_staging_intent_bytes = source_staging_intent.sealed_canonical_bytes().unwrap();
    assert_eq!(
        WorkerSourceStagingIntentReceipt::parse_sealed_canonical(&source_staging_intent_bytes,)
            .unwrap(),
        source_staging_intent
    );
    let mut intent_records = records.clone();
    intent_records.push(
        authorize_source_staging_intent(
            &capsule,
            &intent_records,
            &handoff,
            &source_staging_intent,
        )
        .unwrap(),
    );
    assert_eq!(
        validate_worker_recovery_bundle_with_containment(
            &capsule,
            &launch,
            &intent_records,
            1_200_000,
            false,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                staging_intent: Some(&source_staging_intent),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
    );
    let partial_cleanup = WorkerPartialStagingCleanupReceipt::from_observed(
        &capsule,
        &source_staging_intent,
        [0x6d; 32],
    )
    .unwrap();
    assert_eq!(
        WorkerPartialStagingCleanupReceipt::parse_sealed_canonical(
            &partial_cleanup.sealed_canonical_bytes().unwrap()
        )
        .unwrap(),
        partial_cleanup
    );
    let mut contained_partial_records = intent_records.clone();
    contained_partial_records.push(
        authorize_partial_staging_contained(
            &capsule,
            &contained_partial_records,
            &source_staging_intent,
            &partial_cleanup,
        )
        .unwrap(),
    );
    assert_eq!(
        validate_worker_recovery_bundle_with_containment(
            &capsule,
            &launch,
            &contained_partial_records,
            1_200_000,
            false,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                staging_intent: Some(&source_staging_intent),
                partial_staging_cleanup: Some(&partial_cleanup),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
    );
    intent_records.push(
        authorize_source_handles_bound_after_intent(
            &capsule,
            &intent_records,
            &handoff,
            &source_staging_intent,
            &staging,
        )
        .unwrap(),
    );
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &intent_records,
            1_200_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                live_worker: Some(&live_worker),
                staging_intent: Some(&source_staging_intent),
                staging: Some(&staging),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ResumeSameCapsuleBeforeTransaction
    );
    let mut contained_complete_staging_records = intent_records.clone();
    contained_complete_staging_records.push(
        authorize_partial_staging_contained(
            &capsule,
            &contained_complete_staging_records,
            &source_staging_intent,
            &partial_cleanup,
        )
        .unwrap(),
    );
    assert_eq!(
        validate_worker_recovery_bundle_with_containment(
            &capsule,
            &launch,
            &contained_complete_staging_records,
            1_200_000,
            false,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                staging_intent: Some(&source_staging_intent),
                partial_staging_cleanup: Some(&partial_cleanup),
                staging: Some(&staging),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
    );
    let staging_bytes = staging
        .canonical_bytes(&capsule, &records[3], &handoff)
        .unwrap();
    assert_eq!(
        DurableSourceStagingReceipt::parse_canonical(
            &staging_bytes,
            &capsule,
            &records[3],
            &handoff,
        )
        .unwrap(),
        staging
    );
    let mut undurable_json = serde_json::to_value(&staging).unwrap();
    undurable_json["directoryFlushedAfterFiles"] = serde_json::Value::Bool(false);
    let undurable: DurableSourceStagingReceipt = serde_json::from_value(undurable_json).unwrap();
    assert_eq!(
        authorize_source_handles_bound(&capsule, &records, &handoff, &undurable)
            .unwrap_err()
            .code(),
        "authority_worker_durable_staging_invalid"
    );
    let mut path_leak_json = serde_json::to_value(&handoff).unwrap();
    path_leak_json["sourcePathsTransmitted"] = serde_json::Value::Bool(true);
    let path_leak: WorkerHandleHandoffReceipt = serde_json::from_value(path_leak_json).unwrap();
    assert_eq!(
        authorize_source_handles_bound(&capsule, &records, &path_leak, &staging)
            .unwrap_err()
            .code(),
        "authority_worker_handle_handoff_invalid"
    );
    records.push(authorize_source_handles_bound(&capsule, &records, &handoff, &staging).unwrap());
    assert_eq!(
        records.last().unwrap().phase_receipt_sha256().unwrap(),
        staging.digest().unwrap()
    );
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &records,
            1_200_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                worker_started: Some(&worker_started),
                live_worker: Some(&live_worker),
                ..Default::default()
            },
        )
        .unwrap_err()
        .code(),
        "authority_worker_recovery_evidence_missing"
    );
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &records,
            1_200_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                live_worker: Some(&live_worker),
                staging: Some(&staging),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ResumeSameCapsuleBeforeTransaction
    );
    assert_eq!(
        validate_worker_journal(capsule_sha256, &records).unwrap(),
        MaintenanceWorkerRecoveryDisposition::ResumeSameCapsuleBeforeTransaction
    );
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &records,
            1_300_001,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                live_worker: Some(&live_worker),
                staging: Some(&staging),
                ..Default::default()
            },
        )
        .unwrap_err()
        .code(),
        "authority_worker_action_time_consent_expired"
    );
    let nonce = WorkerNonceConsumptionReceipt::from_observed(
        &capsule, 1_200_000, 88, [0x77; 16], 88, [0x78; 16],
    )
    .unwrap();
    let substituted_identity_ledger = WorkerSourceIdentityLedger::from_observed(
        &capsule,
        &records[3],
        &handoff,
        77,
        [0x71; 16],
        DurableStagedPayloadBinding::from_observed(
            StagedPayloadKind::Service,
            &capsule,
            lease.payloads.service.volume_serial,
            lease.payloads.service.file_id,
            lease.payloads.service.full_readback_receipt_sha256,
            77,
            [0x72; 16],
        )
        .unwrap(),
        DurableStagedPayloadBinding::from_observed(
            StagedPayloadKind::Controller,
            &capsule,
            lease.payloads.controller.volume_serial,
            lease.payloads.controller.file_id,
            lease.payloads.controller.full_readback_receipt_sha256,
            77,
            [0x74; 16],
        )
        .unwrap(),
        DurableStagedPayloadBinding::from_observed(
            StagedPayloadKind::InstallHelper,
            &capsule,
            lease.payloads.install_helper.volume_serial,
            lease.payloads.install_helper.file_id,
            lease.payloads.install_helper.full_readback_receipt_sha256,
            77,
            [0x76; 16],
        )
        .unwrap(),
        DurableStagedPayloadBinding::from_observed(
            StagedPayloadKind::LifecycleDriver,
            &capsule,
            lease.payloads.lifecycle_driver.volume_serial,
            lease.payloads.lifecycle_driver.file_id,
            lease.payloads.lifecycle_driver.full_readback_receipt_sha256,
            77,
            [0x78; 16],
        )
        .unwrap(),
        DurableStagedPayloadBinding::from_observed(
            StagedPayloadKind::BridgeLauncher,
            &capsule,
            lease.payloads.bridge_launcher.volume_serial,
            lease.payloads.bridge_launcher.file_id,
            lease.payloads.bridge_launcher.full_readback_receipt_sha256,
            77,
            [0x7a; 16],
        )
        .unwrap(),
        DurableStagedPayloadBinding::from_observed(
            StagedPayloadKind::RuntimeSourceManifest,
            &capsule,
            lease.payloads.runtime_source_manifest.volume_serial,
            lease.payloads.runtime_source_manifest.file_id,
            lease
                .payloads
                .runtime_source_manifest
                .full_readback_receipt_sha256,
            77,
            [0x7c; 16],
        )
        .unwrap(),
    )
    .unwrap();
    let substituted_staging = DurableSourceStagingReceipt::from_observed(
        &capsule,
        &records[3],
        &handoff,
        &substituted_identity_ledger,
    )
    .unwrap();
    assert_eq!(
        staging
            .validate_identity_ledger(
                &capsule,
                &records[3],
                &handoff,
                &substituted_identity_ledger,
            )
            .unwrap_err()
            .code(),
        "authority_worker_source_identity_ledger_mismatch"
    );
    assert_ne!(
        substituted_staging.digest().unwrap(),
        staging.digest().unwrap()
    );
    assert_eq!(
        authorize_transaction_start(
            &capsule,
            &records,
            &handoff,
            &substituted_staging,
            &nonce,
            1_200_000,
        )
        .unwrap_err()
        .code(),
        "authority_worker_transaction_start_not_authorized"
    );
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &records,
            1_200_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                live_worker: Some(&live_worker),
                staging: Some(&substituted_staging),
                ..Default::default()
            },
        )
        .unwrap_err()
        .code(),
        "authority_worker_recovery_evidence_mismatch"
    );
    let (transaction_started_record, transaction_started) =
        authorize_transaction_start(&capsule, &records, &handoff, &staging, &nonce, 1_200_000)
            .unwrap();
    records.push(transaction_started_record);
    assert_eq!(
        records.last().unwrap().phase_receipt_sha256().unwrap(),
        transaction_started.digest().unwrap()
    );
    assert_eq!(
        validate_worker_journal(capsule_sha256, &records).unwrap(),
        MaintenanceWorkerRecoveryDisposition::RecoverSameTransaction
    );
    assert_eq!(
        validate_worker_recovery_bundle_with_containment(
            &capsule,
            &launch,
            &records,
            1_300_001,
            true,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                staging: Some(&staging),
                nonce_consumption: Some(&nonce),
                transaction_started: Some(&transaction_started),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::ContainInterruptedTransaction
    );
    let committed =
        TransactionCommittedReceipt::from_observed(&capsule, &transaction_started, [0x7c; 32])
            .unwrap();
    let mut committed_records = records.clone();
    committed_records.push(
        authorize_transaction_committed(
            &capsule,
            &committed_records,
            &transaction_started,
            &committed,
        )
        .unwrap(),
    );
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &committed_records,
            9_000_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                staging: Some(&staging),
                nonce_consumption: Some(&nonce),
                transaction_started: Some(&transaction_started),
                transaction_committed: Some(&committed),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::FinishServiceRemoval
    );
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &records,
            9_000_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                staging: Some(&staging),
                nonce_consumption: Some(&nonce),
                transaction_started: Some(&transaction_started),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::RecoverSameTransaction
    );
    let contained =
        TransactionContainedReceipt::from_observed(&capsule, &transaction_started, [0x79; 32])
            .unwrap();
    records.push(
        authorize_transaction_contained(&capsule, &records, &transaction_started, &contained)
            .unwrap(),
    );
    let transaction_terminal = records.last().unwrap().clone();
    assert_eq!(
        validate_worker_journal(capsule_sha256, &records).unwrap(),
        MaintenanceWorkerRecoveryDisposition::FinishServiceRemoval
    );
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &records,
            9_000_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                staging: Some(&staging),
                nonce_consumption: Some(&nonce),
                transaction_started: Some(&transaction_started),
                transaction_contained: Some(&contained),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::FinishServiceRemoval
    );
    let cleanup = WorkerStagingCleanupReceipt::from_observed(
        &capsule,
        &staging,
        &transaction_terminal,
        WorkerStagingTerminalDisposition::RemovedAfterRollback,
        true,
        None,
        Some([0x6c; 32]),
        [0x6d; 32],
    )
    .unwrap();
    let cleanup_bytes = cleanup
        .canonical_bytes(&capsule, &staging, &transaction_terminal)
        .unwrap();
    assert_eq!(
        WorkerStagingCleanupReceipt::parse_canonical(
            &cleanup_bytes,
            &capsule,
            &staging,
            &transaction_terminal,
        )
        .unwrap(),
        cleanup
    );
    let mut residue_json = serde_json::to_value(&cleanup).unwrap();
    residue_json["zeroUnexpectedEntries"] = serde_json::Value::Bool(false);
    let residue: WorkerStagingCleanupReceipt = serde_json::from_value(residue_json).unwrap();
    records.push(authorize_source_stage_resolved(&capsule, &records, &staging, &cleanup).unwrap());
    let exit_ready = WorkerExitReadyReceipt::from_observed(
        &capsule,
        &transaction_terminal,
        &cleanup,
        &worker_started,
    )
    .unwrap();
    records.push(
        authorize_worker_exit_ready(&capsule, &records, &cleanup, &worker_started, &exit_ready)
            .unwrap(),
    );
    let delete_intent = ServiceDeleteIntentReceipt::from_observed(
        &capsule,
        &launch,
        &service_created,
        &exit_ready,
        handoff.helper().clone(),
    )
    .unwrap();
    records.push(
        authorize_service_delete_intent_after_exit_ready(
            &capsule,
            &records,
            &exit_ready,
            &delete_intent,
        )
        .unwrap(),
    );
    let delete_pending = ServiceDeletePendingReceipt::from_delete_call(
        &capsule,
        contained.digest().unwrap(),
        &delete_intent,
        [0x7a; 32],
    )
    .unwrap();
    let observed_absence = ServiceDeletePendingReceipt::from_recovered_target_state(
        &capsule,
        contained.digest().unwrap(),
        &delete_intent,
        ServiceDeleteTargetState::Absent,
        [0x7e; 32],
    )
    .unwrap();
    let observed_absence_json = serde_json::to_value(&observed_absence).unwrap();
    assert_eq!(observed_absence_json["deleteCallCompleted"], false);
    assert_eq!(observed_absence_json["targetStateObserved"], true);
    assert!(observed_absence_json.get("serviceMarkedDelete").is_none());
    let sealed_contained_cleanup = WorkerStagingCleanupReceipt::from_observed(
        &capsule,
        &staging,
        &transaction_terminal,
        WorkerStagingTerminalDisposition::SealedContained,
        false,
        None,
        Some([0x6e; 32]),
        [0x6f; 32],
    )
    .unwrap();
    let journal_len_before_absence_rejection = records.len();
    assert_eq!(
        ServiceAbsentReceipt::from_observed(
            &capsule,
            &delete_pending,
            &sealed_contained_cleanup,
            [0x7f; 32],
        )
        .unwrap_err()
        .code(),
        "authority_worker_service_absent_receipt_invalid"
    );
    assert_eq!(records.len(), journal_len_before_absence_rejection);
    records.push(
        authorize_service_delete_pending(
            &capsule,
            &records,
            contained.digest().unwrap(),
            &delete_intent,
            &delete_pending,
        )
        .unwrap(),
    );
    let handles_closed = FinalizerHandlesClosedReceipt::from_observed(
        &capsule,
        &exit_ready,
        &delete_pending,
        [0x7d; 32],
    )
    .unwrap();
    records.push(
        authorize_finalizer_handles_closed(
            &capsule,
            &records,
            &exit_ready,
            &delete_pending,
            &handles_closed,
        )
        .unwrap(),
    );
    assert_eq!(
        validate_worker_journal(capsule_sha256, &records).unwrap(),
        MaintenanceWorkerRecoveryDisposition::FinishServiceRemoval
    );
    let absent =
        ServiceAbsentReceipt::from_observed(&capsule, &delete_pending, &cleanup, [0x7b; 32])
            .unwrap();
    assert_eq!(
        authorize_service_absent_after_handles_closed(
            &capsule,
            &records,
            &residue,
            &delete_pending,
            &handles_closed,
            &absent,
        )
        .unwrap_err()
        .code(),
        "authority_worker_staging_cleanup_invalid"
    );
    records.push(
        authorize_service_absent_after_handles_closed(
            &capsule,
            &records,
            &cleanup,
            &delete_pending,
            &handles_closed,
            &absent,
        )
        .unwrap(),
    );
    assert_eq!(
        records.last().unwrap().phase_receipt_sha256().unwrap(),
        absent.digest().unwrap()
    );
    assert_eq!(
        validate_worker_journal(capsule_sha256, &records).unwrap(),
        MaintenanceWorkerRecoveryDisposition::Complete
    );
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &records,
            9_000_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                staging: Some(&staging),
                nonce_consumption: Some(&nonce),
                transaction_started: Some(&transaction_started),
                transaction_contained: Some(&contained),
                exit_ready: Some(&exit_ready),
                delete_intent: Some(&delete_intent),
                delete_pending: Some(&delete_pending),
                ..Default::default()
            },
        )
        .unwrap_err()
        .code(),
        "authority_worker_recovery_evidence_missing"
    );
    assert_eq!(
        validate_worker_recovery_bundle(
            &capsule,
            &launch,
            &records,
            9_000_000,
            WorkerRecoveryEvidence {
                intent: Some(&intent),
                bootstrap: Some(&bootstrap),
                pipe: Some(&pipe),
                service_created: Some(&service_created),
                handoff: Some(&handoff),
                worker_started: Some(&worker_started),
                staging: Some(&staging),
                nonce_consumption: Some(&nonce),
                transaction_started: Some(&transaction_started),
                transaction_contained: Some(&contained),
                exit_ready: Some(&exit_ready),
                delete_intent: Some(&delete_intent),
                delete_pending: Some(&delete_pending),
                handles_closed: Some(&handles_closed),
                cleanup: Some(&cleanup),
                service_absent: Some(&absent),
                ..Default::default()
            },
        )
        .unwrap(),
        MaintenanceWorkerRecoveryDisposition::Complete
    );
    assert_eq!(
        validate_worker_journal([0x6e; 32], &records)
            .unwrap_err()
            .code(),
        "authority_worker_journal_integrity_failed"
    );
    let mut tampered = records.clone();
    tampered[3].record_sha256 = hex_lower(&[0x6f; 32]);
    assert_eq!(
        validate_worker_journal(capsule_sha256, &tampered)
            .unwrap_err()
            .code(),
        "authority_worker_journal_integrity_failed"
    );
    let mut tampered_receipt_json = serde_json::to_value(records.last().unwrap()).unwrap();
    tampered_receipt_json["phaseReceiptSha256"] = serde_json::Value::String(hex_lower(&[0x79; 32]));
    let tampered_receipt: MaintenanceWorkerJournalRecord =
        serde_json::from_value(tampered_receipt_json).unwrap();
    let mut tampered_receipt_chain = records.clone();
    *tampered_receipt_chain.last_mut().unwrap() = tampered_receipt;
    assert_eq!(
        validate_worker_journal(capsule_sha256, &tampered_receipt_chain)
            .unwrap_err()
            .code(),
        "authority_worker_journal_integrity_failed"
    );
    let mut zero_receipt_json = serde_json::to_value(&records[2]).unwrap();
    zero_receipt_json["phaseReceiptSha256"] = serde_json::Value::String("00".repeat(32));
    let zero_receipt: MaintenanceWorkerJournalRecord =
        serde_json::from_value(zero_receipt_json).unwrap();
    let mut zero_receipt_chain = records.clone();
    zero_receipt_chain[2] = zero_receipt;
    assert_eq!(
        validate_worker_journal(capsule_sha256, &zero_receipt_chain)
            .unwrap_err()
            .code(),
        "authority_worker_journal_integrity_failed"
    );
}

#[test]
fn worker_journal_append_fault_model_preserves_durable_prefix_and_contains_torn_tail() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let lease = maintenance_lease(&preview);
    let capsule = MaintenanceWorkerCapsule::for_install(
        &preview, &lease, [0x81; 32], [0x82; 32], 1_000_000, 1_300_000,
    )
    .unwrap();
    let capsule_sha256 = capsule.digest().unwrap();
    let (launch, intent, bootstrap) = worker_bootstrap_staging(&capsule);
    let first = MaintenanceWorkerJournalRecord::first_intent(&capsule, &launch, &intent).unwrap();
    let durable_records = vec![first];
    let second =
        authorize_capsule_staged(&capsule, &launch, &durable_records, &intent, &bootstrap).unwrap();
    let durable = encode_worker_journal(capsule_sha256, &durable_records).unwrap();
    let append = encode_worker_journal_append(capsule_sha256, &durable_records, &second).unwrap();

    for cut in 0..append.len() {
        let mut faulted = durable.clone();
        faulted.extend_from_slice(&append[..cut]);
        let recovery = parse_worker_journal_recovery(&faulted, capsule_sha256).unwrap();
        assert_eq!(recovery.records(), durable_records);
        assert_eq!(recovery.durable_byte_length(), durable.len());
        assert_eq!(recovery.torn_tail(), cut != 0);
        if cut != 0 {
            assert_eq!(
                parse_worker_journal(&faulted, capsule_sha256)
                    .unwrap_err()
                    .code(),
                "authority_worker_journal_encoding_invalid"
            );
        }
    }

    for block_size in [1usize, 7, 64, 512, 4096] {
        for cut in (block_size..append.len()).step_by(block_size) {
            let mut faulted = durable.clone();
            faulted.extend_from_slice(&append[..cut]);
            let recovery = parse_worker_journal_recovery(&faulted, capsule_sha256).unwrap();
            assert_eq!(recovery.records(), durable_records);
            assert!(recovery.torn_tail());
        }
    }

    let mut complete_before_failed_flush = durable.clone();
    complete_before_failed_flush.extend_from_slice(&append);
    let recovery =
        parse_worker_journal_recovery(&complete_before_failed_flush, capsule_sha256).unwrap();
    assert_eq!(recovery.records(), &[durable_records[0].clone(), second]);
    assert!(!recovery.torn_tail());
    assert!(complete_before_failed_flush.starts_with(&durable));

    let mut corrupted_prefix = durable.clone();
    corrupted_prefix[durable.len() / 2] ^= 1;
    corrupted_prefix.extend_from_slice(&append[..append.len() / 2]);
    assert!(parse_worker_journal_recovery(&corrupted_prefix, capsule_sha256).is_err());
}

#[test]
fn worker_helper_pipe_nonce_and_handle_adoption_fail_closed() {
    let preview = preview_install(&layout(), content(1)).unwrap();
    let expected = content_from_projection(&preview.content).unwrap();
    let lease = maintenance_lease(&preview);
    let capsule = MaintenanceWorkerCapsule::for_install(
        &preview, &lease, [0x83; 32], [0x84; 32], 1_000_000, 1_300_000,
    )
    .unwrap();
    let second_capsule = MaintenanceWorkerCapsule::for_install(
        &preview, &lease, [0x83; 32], [0x84; 32], 1_000_000, 1_300_000,
    )
    .unwrap();
    assert_ne!(
        worker_handoff_pipe_name(&capsule).unwrap(),
        worker_handoff_pipe_name(&second_capsule).unwrap()
    );
    let launch = MaintenanceWorkerLaunchContract::new(&layout(), &capsule).unwrap();
    let wrong_helper = WorkerProcessBinding::new(78, 9_001, *expected.install_helper.sha256());
    assert_eq!(
        WorkerPipePreparedReceipt::from_observed(&capsule, &launch, wrong_helper, [0x85; 16],)
            .unwrap_err()
            .code(),
        "authority_worker_pipe_prepared_invalid"
    );

    let nonce = WorkerNonceConsumptionReceipt::from_observed(
        &capsule, 1_200_000, 90, [0x86; 16], 90, [0x87; 16],
    )
    .unwrap();
    let mut replayable_json = serde_json::to_value(&nonce).unwrap();
    replayable_json["persistence"]["createNew"] = serde_json::Value::Bool(false);
    let replayable: WorkerNonceConsumptionReceipt =
        serde_json::from_value(replayable_json).unwrap();
    assert_eq!(
        replayable.validate(&capsule).unwrap_err().code(),
        "authority_worker_nonce_consumption_invalid"
    );
    assert_eq!(
        capsule.validate_consent_at(1_300_001).unwrap_err().code(),
        "authority_worker_action_time_consent_expired"
    );

    let mut handles = OneShotDuplicatedHandleValues::default();
    handles
        .arm([0x100, 0x200, 0x300, 0x400, 0x500, 0x600])
        .unwrap();
    assert_eq!(
        handles.take().unwrap(),
        [0x100, 0x200, 0x300, 0x400, 0x500, 0x600]
    );
    assert_eq!(
        handles.take().unwrap_err().code(),
        "authority_worker_duplicated_handles_already_adopted"
    );
    assert_eq!(
        handles
            .arm([0x700, 0x800, 0x900, 0xa00, 0xb00, 0xc00])
            .unwrap_err()
            .code(),
        "authority_worker_duplicated_handles_already_armed"
    );

    let local_system_helper = VerifiedBootstrapHelperIdentity::from_running_helper(
        expected.install_helper,
        RawBootstrapHelperObservation {
            process_id: 77,
            process_creation_time: 9_001,
            image_volume_serial: 88,
            image_file_id: [19; 16],
            image_sha256: expected.install_helper.sha256,
            image_byte_length: expected.install_helper.byte_length,
            image_handle_held: true,
            elevated_token: true,
            high_integrity: true,
            local_system: true,
            session_id: 0,
        },
    );
    assert_eq!(
        local_system_helper.unwrap_err().code(),
        "authority_bootstrap_helper_identity_not_verified"
    );
}

#[test]
fn malformed_content_and_unverified_prior_state_fail_closed() {
    assert_eq!(
        AuthorityPayloadDigest::new([0; 32], 1).unwrap_err().code(),
        "authority_payload_digest_zero"
    );
    assert_eq!(
        AuthorityInstallContent::new(
            descriptor(1),
            descriptor(1),
            descriptor(2),
            descriptor(3),
            descriptor(4),
            descriptor(5),
        )
        .unwrap_err()
        .code(),
        "authority_payload_digest_collision"
    );
    assert_eq!(
        {
            let preview = preview_install(&layout(), content(1)).unwrap();
            let generation = preview.generation_sha256().unwrap();
            let installed_content = content_from_projection(&preview.content).unwrap();
            let (public_key, key_id) = verified_public_key(4);
            let ledger_identity = [9; 32];
            let trust = detached(
                CanonicalUnsignedManifestPayload::Trust {
                    generation,
                    signer_key_id: key_id,
                    signer_public_key_sec1: public_key,
                    ledger_identity,
                    created_epoch: 1,
                    valid: true,
                    revoked: false,
                },
                key_id,
            );
            let activation = detached(
                CanonicalUnsignedManifestPayload::Activation {
                    generation,
                    trust_manifest_sha256: trust.unsigned_payload_sha256,
                    signer_key_id: key_id,
                    activated_epoch: 1,
                    previous_generation: None,
                    previous_activation_sha256: None,
                    previous_activation_epoch: None,
                    valid: true,
                    revoked: false,
                },
                key_id,
            );
            VerifiedInstalledGeneration::from_sealed_readback(SealedInstalledGenerationReadback {
                generation,
                payload_files: VerifiedPayloadFilesProof {
                    service: installed_content.service,
                    controller: installed_content.controller,
                    install_helper: installed_content.install_helper,
                    lifecycle_driver: installed_content.lifecycle_driver,
                    bridge_launcher: installed_content.bridge_launcher,
                    runtime_source_manifest: installed_content.runtime_source_manifest,
                    receipt_sha256: [0; 32],
                },
                key: VerifiedKeyProof {
                    signer_key_id: key_id,
                    signer_public_key_sec1: public_key,
                    receipt_sha256: [11; 32],
                },
                ledger: VerifiedLedgerProof {
                    ledger_identity,
                    receipt_sha256: [12; 32],
                },
                service_runtime: sealed_service(generation, installed_content.service),
                manifests: RawManifestChainReadback {
                    trust,
                    activation,
                    retirement: None,
                    protected_activation_history: Vec::new(),
                    observed_heads: vec![VerifiedProtectedActivationHead {
                        generation,
                        activation_manifest_sha256: activation.unsigned_payload_sha256,
                        activation_epoch: 1,
                        volume_serial: 25,
                        file_id: [26; 16],
                        protected_head_receipt_sha256: [27; 32],
                    }],
                },
            })
        }
        .unwrap_err()
        .code(),
        "authority_installed_generation_not_verified"
    );
}

fn preview_action_mut<'a>(
    preview: &'a mut AuthorityMaintenancePreview,
    id: &str,
) -> &'a mut AuthorityMaintenanceAction {
    &mut preview
        .steps
        .iter_mut()
        .find(|step| step.id == id)
        .expect("fixture action must exist")
        .action
}

fn assert_exact_target_service_plan_invalid(preview: &AuthorityMaintenancePreview) {
    assert_eq!(
        preview.exact_target_service_plan().unwrap_err().code(),
        "authority_exact_target_service_plan_invalid"
    );
}

fn rebind_install_preview_plan(preview: &mut AuthorityMaintenancePreview) {
    let mut normalized_steps = preview.steps.clone();
    match &mut normalized_steps[0].action {
        AuthorityMaintenanceAction::CreateDurableJournal { plan_sha256, .. } => plan_sha256.clear(),
        other => panic!("journal is not first: {other:?}"),
    }
    let generation = preview.generation_sha256().unwrap();
    let policy = decode_hex_32(&preview.policy_sha256).unwrap();
    let content = content_from_projection(&preview.content).unwrap();
    let plan_sha256 = hex_lower(
        &derive_full_plan_digest(
            preview.operation,
            &generation,
            None,
            &content,
            &policy,
            &preview.layout,
            &preview.fixed_policy,
            &normalized_steps,
        )
        .unwrap(),
    );
    preview.plan_sha256 = plan_sha256.clone();
    preview.journal.plan_sha256 = plan_sha256.clone();
    match preview_action_mut(preview, "createDurableJournal") {
        AuthorityMaintenanceAction::CreateDurableJournal {
            plan_sha256: action_plan_sha256,
            ..
        } => *action_plan_sha256 = plan_sha256,
        other => panic!("journal action drifted: {other:?}"),
    }
}

#[test]
fn exact_target_service_plan_is_typed_stable_and_operation_bound() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let repeated = preview_install(&layout(), content(1)).unwrap();
    let plan = install.exact_target_service_plan().unwrap();
    let repeated_plan = repeated.exact_target_service_plan().unwrap();

    assert_eq!(plan.operation(), AuthorityMaintenanceOperation::Install);
    assert_eq!(
        plan.configuration().binary_command,
        format!("\"{}\" --service", install.layout.service_executable)
    );
    assert_eq!(
        plan.exact_service_configuration_sha256(),
        repeated_plan.exact_service_configuration_sha256()
    );
    assert_ne!(plan.exact_service_configuration_sha256(), [0; 32]);
    assert_eq!(plan.plan_sha256(), install.plan_sha256().unwrap());
    assert_eq!(
        plan.transaction_sha256(),
        install.transaction_sha256().unwrap()
    );
    assert_eq!(
        plan.generation_sha256(),
        install.generation_sha256().unwrap()
    );
    assert_eq!(plan.expected_service_image_sha256(), descriptor(1).sha256);
    assert_eq!(plan.active_head_path(), install.layout.active_head);
    assert_eq!(plan.ledger_path(), install.layout.ledger_file);
    assert_eq!(plan.ledger_anchor_path(), install.layout.ledger_anchor_file);
    assert_ne!(plan.ledger_path(), plan.ledger_anchor_path());
    assert_eq!(
        plan.final_commit_store_root(),
        install.layout.finalizer_commit_store_root
    );
    assert_eq!(
        plan.final_commit_receipt_leaf(),
        "05-final-commit.receipt.json"
    );
    assert_eq!(
        plan.final_commit_gate_derivation(),
        "authenticatedStoreRoot+fixedLeaf+schema+bindingProjection+validReceipt"
    );
    assert!(plan.postcommit_runtime_recovery().is_exact());

    let moved_layout = AuthorityLayout::for_test_roots(
        Path::new(r"D:\Program Files"),
        Path::new(r"D:\ProgramData"),
    )
    .unwrap();
    let moved = preview_install(&moved_layout, content(1)).unwrap();
    assert_ne!(
        plan.exact_service_configuration_sha256(),
        moved
            .exact_target_service_plan()
            .unwrap()
            .exact_service_configuration_sha256()
    );

    let update = preview_update(&layout(), content(10), installed(&install)).unwrap();
    assert_eq!(
        update.exact_target_service_plan().unwrap().operation(),
        AuthorityMaintenanceOperation::Update
    );
    let retire = preview_retire(&layout(), installed(&install)).unwrap();
    assert_exact_target_service_plan_invalid(&retire);
}

#[test]
fn exact_target_service_plan_rejects_missing_duplicate_and_wrong_variants() {
    let install = preview_install(&layout(), content(1)).unwrap();

    let mut missing = install.clone();
    missing
        .steps
        .retain(|step| step.id != "configureServiceExact");
    assert_exact_target_service_plan_invalid(&missing);

    let mut duplicate = install.clone();
    duplicate.steps.push(
        duplicate
            .steps
            .iter()
            .find(|step| step.id == "configureServiceExact")
            .unwrap()
            .clone(),
    );
    assert_exact_target_service_plan_invalid(&duplicate);

    let mut wrong_variant = install.clone();
    let replacement = wrong_variant
        .steps
        .iter()
        .find(|step| step.id == "createDurableJournal")
        .unwrap()
        .action
        .clone();
    *preview_action_mut(&mut wrong_variant, "configureServiceExact") = replacement;
    assert_exact_target_service_plan_invalid(&wrong_variant);

    let mut duplicate_start = install.clone();
    duplicate_start.steps.push(
        duplicate_start
            .steps
            .iter()
            .find(|step| step.id == "startCommittedRuntime")
            .unwrap()
            .clone(),
    );
    assert_exact_target_service_plan_invalid(&duplicate_start);

    let mut missing_commit = install.clone();
    missing_commit
        .steps
        .retain(|step| step.id != "persistFinalCommit");
    assert_exact_target_service_plan_invalid(&missing_commit);

    let mut wrong_readback_id = install;
    wrong_readback_id
        .steps
        .iter_mut()
        .find(|step| step.id == "verifyProtectedReadback")
        .unwrap()
        .id = "verifyProtectedReadbackDrifted";
    assert_exact_target_service_plan_invalid(&wrong_readback_id);
}

#[test]
fn protected_blob_namespace_action_and_fixed_policy_fail_closed_after_plan_rebinding() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let namespace_index = install
        .steps
        .iter()
        .position(|step| step.id == "createProtectedBlobNamespace")
        .unwrap();
    let state_generation_index = install
        .steps
        .iter()
        .position(|step| step.id == "createStateGenerationDirectory")
        .unwrap();
    let seal_index = install
        .steps
        .iter()
        .position(|step| step.id == "sealCandidateGenerationForFinalCommit")
        .unwrap();
    assert!(state_generation_index < namespace_index && namespace_index < seal_index);
    match &install.steps[namespace_index].action {
        AuthorityMaintenanceAction::CreateDirectory {
            path,
            parent_path,
            security_sddl,
            owner_sid,
            exact_security_required,
            reject_reparse_points,
            stable_object_identity_required,
            open_parent_by_handle,
            create_relative_to_parent_handle,
            retain_verified_handle,
            create_new,
            never_reuse,
        } => {
            assert_eq!(path, &install.layout.protected_blob_namespace);
            assert_eq!(parent_path, &install.layout.generation_state_root);
            assert_eq!(*security_sddl, RUNTIME_BLOB_DIRECTORY_STAGING_SDDL);
            assert_eq!(*owner_sid, LOCAL_SYSTEM_SID);
            assert!(*exact_security_required);
            assert!(*reject_reparse_points);
            assert!(*stable_object_identity_required);
            assert!(*open_parent_by_handle);
            assert!(*create_relative_to_parent_handle);
            assert!(*retain_verified_handle);
            assert!(*create_new);
            assert!(*never_reuse);
        }
        other => panic!("unexpected protected blob namespace action: {other:?}"),
    }

    let mut missing = install.clone();
    missing
        .steps
        .retain(|step| step.id != "createProtectedBlobNamespace");
    rebind_install_preview_plan(&mut missing);
    assert_exact_target_service_plan_invalid(&missing);

    let mut duplicate = install.clone();
    duplicate.steps.push(install.steps[namespace_index].clone());
    rebind_install_preview_plan(&mut duplicate);
    assert_exact_target_service_plan_invalid(&duplicate);

    for field in 0..6 {
        let mut action_drift = install.clone();
        let drifted_parent = action_drift.layout.state_generations_root.clone();
        if let AuthorityMaintenanceAction::CreateDirectory {
            parent_path,
            security_sddl,
            retain_verified_handle,
            create_relative_to_parent_handle,
            create_new,
            never_reuse,
            ..
        } = preview_action_mut(&mut action_drift, "createProtectedBlobNamespace")
        {
            match field {
                0 => *parent_path = drifted_parent,
                1 => *security_sddl = STATE_GENERATION_DIRECTORY_SDDL,
                2 => *retain_verified_handle = false,
                3 => *create_relative_to_parent_handle = false,
                4 => *create_new = false,
                5 => *never_reuse = false,
                _ => unreachable!(),
            }
        }
        rebind_install_preview_plan(&mut action_drift);
        assert_exact_target_service_plan_invalid(&action_drift);
    }

    let mut reordered = install.clone();
    let namespace = reordered.steps.remove(namespace_index);
    let seal = reordered
        .steps
        .iter()
        .position(|step| step.id == "sealCandidateGenerationForFinalCommit")
        .unwrap();
    reordered.steps.insert(seal + 1, namespace);
    rebind_install_preview_plan(&mut reordered);
    assert_exact_target_service_plan_invalid(&reordered);

    for field in 0..4 {
        let mut policy_drift = install.clone();
        match field {
            0 => policy_drift.fixed_policy.protected_blob_directory_name = "blob-drift",
            1 => policy_drift.fixed_policy.protected_blob_file_sddl = STATE_FILE_SDDL,
            2 => policy_drift.fixed_policy.protected_blob_file_read_access ^= 1,
            3 => policy_drift.fixed_policy.protected_blob_share_access = 1,
            _ => unreachable!(),
        }
        rebind_install_preview_plan(&mut policy_drift);
        assert_exact_target_service_plan_invalid(&policy_drift);
    }
}

#[test]
fn exact_target_service_plan_rejects_operation_path_flag_and_configuration_drift() {
    let install = preview_install(&layout(), content(1)).unwrap();

    let mut plan_digest = install.clone();
    plan_digest.plan_sha256 = "a5".repeat(32);
    assert_exact_target_service_plan_invalid(&plan_digest);

    let mut fully_relabelled_plan_digest = install.clone();
    let forged_plan = "b6".repeat(32);
    fully_relabelled_plan_digest.plan_sha256 = forged_plan.clone();
    fully_relabelled_plan_digest.journal.plan_sha256 = forged_plan.clone();
    if let AuthorityMaintenanceAction::CreateDurableJournal { plan_sha256, .. } =
        preview_action_mut(&mut fully_relabelled_plan_digest, "createDurableJournal")
    {
        *plan_sha256 = forged_plan;
    }
    assert_exact_target_service_plan_invalid(&fully_relabelled_plan_digest);

    let mut transaction_digest = install.clone();
    transaction_digest.transaction_sha256 = "c7".repeat(32);
    assert_exact_target_service_plan_invalid(&transaction_digest);

    let mut operation = install.clone();
    operation.operation = AuthorityMaintenanceOperation::Update;
    assert_exact_target_service_plan_invalid(&operation);

    let mut action_operation = install.clone();
    if let AuthorityMaintenanceAction::ConfigureServiceExact { operation, .. } =
        preview_action_mut(&mut action_operation, "configureServiceExact")
    {
        *operation = "changeExact";
    }
    assert_exact_target_service_plan_invalid(&action_operation);

    let mut root_path = install.clone();
    if let AuthorityMaintenanceAction::ConfigureServiceExact {
        final_commit_store_root,
        ..
    } = preview_action_mut(&mut root_path, "configureServiceExact")
    {
        *final_commit_store_root = r"C:\drifted-final-commit-root".to_string();
    }
    assert_exact_target_service_plan_invalid(&root_path);

    let mut root_layout = install.clone();
    root_layout.layout.finalizer_commit_store_root = r"C:\drifted-layout-root".to_string();
    assert_exact_target_service_plan_invalid(&root_layout);

    let mut root_descriptor = install.clone();
    if let AuthorityMaintenanceAction::CreateDirectory {
        security_sddl,
        create_relative_to_parent_handle,
        ..
    } = preview_action_mut(&mut root_descriptor, "createFinalizerCommitStoreRoot")
    {
        *security_sddl = STATE_DIRECTORY_SDDL;
        *create_relative_to_parent_handle = false;
    }
    assert_exact_target_service_plan_invalid(&root_descriptor);

    let mut duplicate_root = install.clone();
    duplicate_root.steps.push(
        duplicate_root
            .steps
            .iter()
            .find(|step| step.id == "createFinalizerCommitStoreRoot")
            .unwrap()
            .clone(),
    );
    assert_exact_target_service_plan_invalid(&duplicate_root);

    let mut aliased_root = install.clone();
    let mut aliased_step = aliased_root
        .steps
        .iter()
        .find(|step| step.id == "createFinalizerCommitStoreRoot")
        .unwrap()
        .clone();
    aliased_step.id = "createFinalizerCommitStoreRootAlias";
    aliased_root.steps.push(aliased_step);
    assert_exact_target_service_plan_invalid(&aliased_root);

    let mut flag = install.clone();
    if let AuthorityMaintenanceAction::ConfigureServiceExact {
        require_precommit_dormant_mode,
        ..
    } = preview_action_mut(&mut flag, "configureServiceExact")
    {
        *require_precommit_dormant_mode = false;
    }
    assert_exact_target_service_plan_invalid(&flag);

    let mut aliased_ledger_anchor = install.clone();
    aliased_ledger_anchor.layout.ledger_anchor_file =
        aliased_ledger_anchor.layout.ledger_file.clone();
    assert_exact_target_service_plan_invalid(&aliased_ledger_anchor);

    let mut unverified_ledger_anchor = install.clone();
    if let AuthorityMaintenanceAction::ProvisionLedger {
        flush_anchor_before_completion,
        rehash_anchor_from_held_handle,
        complete_only_after_exact_pair_readback,
        ..
    } = preview_action_mut(&mut unverified_ledger_anchor, "provisionLedger")
    {
        *flush_anchor_before_completion = false;
        *rehash_anchor_from_held_handle = false;
        *complete_only_after_exact_pair_readback = false;
    }
    assert_exact_target_service_plan_invalid(&unverified_ledger_anchor);

    for field in 0..6 {
        let mut weakened_pair_contract = install.clone();
        if let AuthorityMaintenanceAction::ProvisionLedger {
            create_pair_relative_to_verified_parent_handle,
            retain_both_handles_through_pair_readback,
            deny_write_delete_sharing_for_both,
            verify_each_local_reparse_free_single_link,
            require_distinct_physical_file_identities,
            persist_durable_pair_receipt_before_completion,
            ..
        } = preview_action_mut(&mut weakened_pair_contract, "provisionLedger")
        {
            match field {
                0 => *create_pair_relative_to_verified_parent_handle = false,
                1 => *retain_both_handles_through_pair_readback = false,
                2 => *deny_write_delete_sharing_for_both = false,
                3 => *verify_each_local_reparse_free_single_link = false,
                4 => *require_distinct_physical_file_identities = false,
                5 => *persist_durable_pair_receipt_before_completion = false,
                _ => unreachable!(),
            }
        }
        assert_exact_target_service_plan_invalid(&weakened_pair_contract);
    }

    let mut configuration = install;
    if let AuthorityMaintenanceAction::ConfigureServiceExact { configuration, .. } =
        preview_action_mut(&mut configuration, "configureServiceExact")
    {
        configuration.binary_command = r#""C:\drifted.exe" --service"#.to_string();
    }
    assert_exact_target_service_plan_invalid(&configuration);
}

#[test]
fn exact_target_service_plan_rejects_downstream_gate_and_dormant_drift() {
    let install = preview_install(&layout(), content(1)).unwrap();

    let mut start_flag = install.clone();
    if let AuthorityMaintenanceAction::StartCommittedRuntime {
        runtime_self_activates_only_after_durable_final_commit_readback,
        ..
    } = preview_action_mut(&mut start_flag, "startCommittedRuntime")
    {
        *runtime_self_activates_only_after_durable_final_commit_readback = false;
    }
    assert_exact_target_service_plan_invalid(&start_flag);

    let mut commit_leaf = install.clone();
    if let AuthorityMaintenanceAction::PersistFinalCommit {
        final_commit_receipt_leaf,
        ..
    } = preview_action_mut(&mut commit_leaf, "persistFinalCommit")
    {
        *final_commit_receipt_leaf = "drifted.receipt.json";
    }
    assert_exact_target_service_plan_invalid(&commit_leaf);

    let mut postcommit_gate = install.clone();
    if let AuthorityMaintenanceAction::VerifyPostcommitServingRuntimeReadback {
        require_serving_state_bound_to_final_commit_gate,
        ..
    } = preview_action_mut(&mut postcommit_gate, "verifyProtectedReadback")
    {
        *require_serving_state_bound_to_final_commit_gate = false;
    }
    assert_exact_target_service_plan_invalid(&postcommit_gate);

    let mut recovery_authentication = install.clone();
    if let AuthorityMaintenanceAction::VerifyPostcommitServingRuntimeReadback {
        require_recovery_final_commit_receipt_immutable,
        require_recovery_active_head_binding,
        ..
    } = preview_action_mut(&mut recovery_authentication, "verifyProtectedReadback")
    {
        *require_recovery_final_commit_receipt_immutable = false;
        *require_recovery_active_head_binding = false;
    }
    assert_exact_target_service_plan_invalid(&recovery_authentication);

    let mut recovery_rewrite = install.clone();
    if let AuthorityMaintenanceAction::VerifyPostcommitServingRuntimeReadback {
        forbid_final_commit_receipt_rewrite_during_recovery,
        require_recovery_previous_precommit_runtime_absence,
        require_recovery_start_or_adopt_new_runtime_process_identity,
        require_recovery_serving_readback,
        ..
    } = preview_action_mut(&mut recovery_rewrite, "verifyProtectedReadback")
    {
        *forbid_final_commit_receipt_rewrite_during_recovery = false;
        *require_recovery_previous_precommit_runtime_absence = false;
        *require_recovery_start_or_adopt_new_runtime_process_identity = false;
        *require_recovery_serving_readback = false;
    }
    assert_exact_target_service_plan_invalid(&recovery_rewrite);

    let mut reordered = install.clone();
    let configure = reordered
        .steps
        .iter()
        .position(|step| step.id == "configureServiceExact")
        .unwrap();
    let start = reordered
        .steps
        .iter()
        .position(|step| step.id == "startCommittedRuntime")
        .unwrap();
    reordered.steps.swap(configure, start);
    assert_exact_target_service_plan_invalid(&reordered);

    let update = preview_update(&layout(), content(10), installed(&install)).unwrap();
    let dormant = update
        .steps
        .iter()
        .find(|step| step.id == "verifySuccessorBeforeRetirement")
        .unwrap()
        .clone();

    let mut install_with_dormant = install;
    install_with_dormant.steps.push(dormant);
    assert_exact_target_service_plan_invalid(&install_with_dormant);

    let mut update_without_dormant = update.clone();
    update_without_dormant
        .steps
        .retain(|step| step.id != "verifySuccessorBeforeRetirement");
    assert_exact_target_service_plan_invalid(&update_without_dormant);

    let mut dormant_flag = update;
    if let AuthorityMaintenanceAction::VerifyPrecommitDormantRuntimeReadback {
        require_runtime_dormant,
        ..
    } = preview_action_mut(&mut dormant_flag, "verifySuccessorBeforeRetirement")
    {
        *require_runtime_dormant = false;
    }
    assert_exact_target_service_plan_invalid(&dormant_flag);
}
