use super::*;
use std::path::Path;

fn descriptor(seed: u8) -> AuthorityPayloadDigest {
    AuthorityPayloadDigest::new([seed; 32], 1_000 + u64::from(seed)).unwrap()
}

fn verified_public_key(seed: u8) -> ([u8; 65], [u8; 32]) {
    let mut public_key = [seed; 65];
    public_key[0] = 0x04;
    let key_id = Sha256::digest(public_key).into();
    (public_key, key_id)
}

fn content(seed: u8) -> AuthorityInstallContent {
    AuthorityInstallContent::new(descriptor(seed), descriptor(seed + 1), descriptor(seed + 2))
        .unwrap()
}

fn layout() -> AuthorityLayout {
    AuthorityLayout::for_test_roots(Path::new(r"C:\Program Files"), Path::new(r"C:\ProgramData"))
        .unwrap()
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
    )
    .unwrap()
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
        value["fixedPolicy"]["service"]["requiredPrivileges"],
        serde_json::json!(AUTHORITY_REQUIRED_PRIVILEGES)
    );
    assert_eq!(value["fixedPolicy"]["pipeName"], AUTHORITY_PIPE_NAME);
    assert_eq!(
        value["fixedPolicy"]["pipeSecuritySddl"],
        AUTHORITY_PIPE_SDDL
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
    assert_eq!(payload_steps.len(), 3);
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
            write_through: true,
            flush_file_before_completion: true,
            flush_parent_after_create: true,
            rehash_identity_from_held_handle: true,
            complete_only_after_exact_readback: true,
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
        AuthorityRollbackAction::MarkRetirementAbortedNoReuse { .. }
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
        AuthorityMaintenanceAction::VerifyProtectedReadback {
            require_service_absent,
            ..
        } => *require_service_absent = true,
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
        "authority_native_mutation_backend_disabled"
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
fn start_failure_restores_active_head_before_sealing_or_requires_recovery() {
    let install = preview_install(&layout(), content(1)).unwrap();
    let installed = installed(&install);
    let update = preview_update(&layout(), content(10), installed.clone()).unwrap();
    for (preview, expect_initial_delete) in [(&install, true), (&update, false)] {
        let advance = preview
            .steps
            .iter()
            .find(|step| step.id == "advanceActiveHeadAtomic")
            .unwrap();
        match &advance.rollback {
            AuthorityRollbackAction::RestoreActiveHeadAndSealGenerationConsumed {
                target_activation,
                target_epoch,
                restore_previous_generation,
                restore_previous_activation_sha256,
                restore_previous_epoch,
                delete_if_initial,
                compare_exchange_target_only,
                flush_parent_before_seal,
                ..
            } => {
                assert_eq!(
                    *target_epoch,
                    if expect_initial_delete {
                        1
                    } else {
                        installed.activation_epoch + 1
                    }
                );
                assert!(matches!(
                    target_activation,
                    ProtectedActivationDigestReference::SignedManifestHeldHandleReadback {
                        generation,
                        manifest_path,
                        require_held_handle: true,
                        require_detached_signature_verification: true,
                        ..
                    } if generation == &preview.generation
                        && manifest_path == &preview.layout.activation_manifest
                ));
                assert_eq!(*delete_if_initial, expect_initial_delete);
                assert_eq!(restore_previous_generation.is_none(), expect_initial_delete);
                assert_eq!(
                    restore_previous_activation_sha256.is_none(),
                    expect_initial_delete
                );
                assert_eq!(restore_previous_epoch.is_none(), expect_initial_delete);
                assert!(*compare_exchange_target_only);
                assert!(*flush_parent_before_seal);
            }
            other => panic!("unexpected active-head rollback: {other:?}"),
        }
        let start = preview
            .steps
            .iter()
            .position(|step| step.id == "startServiceWithGenerationHandshake")
            .unwrap();
        let mut lease = maintenance_lease(preview);
        let mut executor = FakeExecutor {
            fail_apply_at: Some(start - 1),
            ..Default::default()
        };
        let report = execute_with_test_executor(preview, &mut lease, &mut executor);
        assert_eq!(report.status, "contained");
        assert_eq!(executor.recovery_seal_calls, 1);
        assert!(executor.rollback_order.contains(&"advanceActiveHeadAtomic"));

        let mut lease = maintenance_lease(preview);
        let mut executor = FakeExecutor {
            fail_apply_at: Some(start - 1),
            fail_rollback_at: Some("advanceActiveHeadAtomic"),
            ..Default::default()
        };
        let report = execute_with_test_executor(preview, &mut lease, &mut executor);
        assert_eq!(report.status, "recoveryRequired");
        assert!(report
            .rollback_failures
            .contains(&"advanceActiveHeadAtomic"));
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
            let post_commit = preview.steps[..failed_step].iter().any(|step| {
                matches!(
                    &step.action,
                    AuthorityMaintenanceAction::FinalizeRetirementTombstoneAtomic {
                        irreversible_commit: true,
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
    assert!(preview.steps[..failed_step]
        .iter()
        .all(|step| matches!(&step.rollback, AuthorityRollbackAction::None)));
    assert_eq!(preview.steps[failed_step - 1].id, "ensureRecoveriesRoot");
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
fn malformed_content_and_unverified_prior_state_fail_closed() {
    assert_eq!(
        AuthorityPayloadDigest::new([0; 32], 1).unwrap_err().code(),
        "authority_payload_digest_zero"
    );
    assert_eq!(
        AuthorityInstallContent::new(descriptor(1), descriptor(1), descriptor(2),)
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
