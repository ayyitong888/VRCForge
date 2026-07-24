use super::*;

pub(super) fn build_preview(
    layout: &AuthorityLayout,
    operation: AuthorityMaintenanceOperation,
    content: AuthorityInstallContent,
    prior: Option<VerifiedInstalledGeneration>,
) -> Result<AuthorityMaintenancePreview, AuthorityMaintenanceError> {
    match (operation, prior.is_some()) {
        (AuthorityMaintenanceOperation::Install, false)
        | (AuthorityMaintenanceOperation::Update, true)
        | (AuthorityMaintenanceOperation::Retire, true) => {}
        _ => {
            return Err(AuthorityMaintenanceError(
                "authority_operation_state_invalid",
            ))
        }
    }
    if prior
        .as_ref()
        .is_some_and(|value| value.activation_epoch == u64::MAX)
    {
        return Err(AuthorityMaintenanceError(
            "authority_activation_epoch_exhausted",
        ));
    }
    let binary_anchor = layout.binary_anchor().to_path_buf();
    let state_anchor = layout.state_anchor().to_path_buf();
    let binary_base = layout.binary_base().to_path_buf();
    let state_base = layout.state_base().to_path_buf();
    let binary_root = layout.binary_root().to_path_buf();
    let state_root = layout.state_root().to_path_buf();
    for path in [
        &binary_anchor,
        &state_anchor,
        &binary_base,
        &state_base,
        &binary_root,
        &state_root,
    ] {
        if path_string(path)?.is_empty() {
            return Err(AuthorityMaintenanceError(
                "authority_layout_projection_invalid",
            ));
        }
    }
    let policy_seed = fixed_policy_seed();
    let policy_sha256: [u8; 32] = Sha256::digest(&policy_seed).into();
    if let Some(prior) = prior.as_ref() {
        let prior_content =
            AuthorityInstallContent::new(prior.service, prior.controller, prior.install_helper)?;
        if derive_generation(&binary_root, &state_root, &prior_content, &policy_sha256)
            != prior.generation
        {
            return Err(AuthorityMaintenanceError(
                "authority_prior_generation_binding_mismatch",
            ));
        }
    }
    let generation = derive_generation(&binary_root, &state_root, &content, &policy_sha256);
    if operation == AuthorityMaintenanceOperation::Retire
        && prior.as_ref().map(|value| value.generation) != Some(generation)
    {
        return Err(AuthorityMaintenanceError(
            "authority_retire_generation_mismatch",
        ));
    }
    if operation == AuthorityMaintenanceOperation::Update
        && prior.as_ref().map(|value| value.generation) == Some(generation)
    {
        return Err(AuthorityMaintenanceError(
            "authority_update_generation_reuse",
        ));
    }

    let generation_hex = hex_lower(&generation);
    let prior_hex = prior.as_ref().map(|value| hex_lower(&value.generation));
    let binary_generations_root = binary_root.join("generations");
    let state_generations_root = state_root.join("generations");
    let activations_root = state_root.join("activations");
    let retirements_root = state_root.join("retirements");
    let recoveries_root = state_root.join("recoveries");
    let generation_binary_root = layout
        .generation_binary_root(&generation)
        .map_err(|_| AuthorityMaintenanceError("authority_generation_layout_invalid"))?;
    let generation_state_root = layout
        .generation_state_root(&generation)
        .map_err(|_| AuthorityMaintenanceError("authority_generation_layout_invalid"))?;
    let service_executable = generation_binary_root.join("vrcforge_primitive_evidence_service.exe");
    let controller_executable =
        generation_binary_root.join("vrcforge_primitive_evidence_controller.exe");
    let install_helper_executable =
        generation_binary_root.join("vrcforge_primitive_evidence_install_helper.exe");
    let ledger_file = generation_state_root.join("ledger.bin");
    let trust_manifest = generation_state_root.join("trust.json");
    let activation_manifest = activations_root.join(format!("{generation_hex}.json"));
    let retirement_manifest = prior_hex
        .as_ref()
        .map(|value| retirements_root.join(format!("{value}.json")));
    let binary_command = exact_service_command(&service_executable)?;
    let key_name = format!("{AUTHORITY_KEY_NAME_PREFIX}{generation_hex}");
    let service = ServiceConfigurationProjection {
        name: AUTHORITY_SERVICE_NAME,
        display_name: AUTHORITY_SERVICE_DISPLAY_NAME,
        account: AUTHORITY_SERVICE_ACCOUNT,
        service_type: "ownProcess",
        start: "demand",
        error_control: "normal",
        sid_type: "restricted",
        service_sid: SERVICE_SID,
        required_privileges: AUTHORITY_REQUIRED_PRIVILEGES.to_vec(),
        binary_command,
        security_sddl: SERVICE_SECURITY_SDDL,
    };
    let fixed_policy = FixedPolicyProjection {
        service: service.clone(),
        pipe_name: AUTHORITY_PIPE_NAME,
        pipe_security_sddl: AUTHORITY_PIPE_SDDL,
        binary_directory_sddl: BINARY_DIRECTORY_SDDL,
        binary_file_sddl: BINARY_FILE_SDDL,
        state_directory_sddl: STATE_DIRECTORY_SDDL,
        state_file_sddl: STATE_FILE_SDDL,
        key_name,
        key_algorithm: "ECDSA_P256",
        key_length_bits: 256,
        key_usage: "signOnly",
        key_export_policy: "noExport",
        key_security_sddl: KEY_SECURITY_SDDL,
        ledger_frame_size: FRAME_SIZE,
        ledger_max_result_size: MAX_RESULT_SIZE,
        ledger_identity_source: "protectedGenerationAndSignerReadback",
        protected_directory_owner_sid: LOCAL_SYSTEM_SID,
        protected_directory_exact_security_required: true,
        protected_directory_reparse_points_rejected: true,
        protected_directory_stable_object_identity_required: true,
        protected_directory_parent_opened_by_handle: true,
        protected_directory_child_created_relative_to_handle: true,
        protected_directory_handle_retained_through_transaction: true,
    };
    let transaction_digest = derive_transaction_digest(
        operation,
        &generation,
        prior.as_ref(),
        &content,
        &policy_sha256,
        &binary_root,
        &state_root,
    );
    let transaction_hex = hex_lower(&transaction_digest);
    let recovery_manifest = recoveries_root.join(format!("{transaction_hex}.json"));
    let maintenance_journal = state_anchor.join(format!(
        "VRCForgeEvidenceAuthority-maintenance-{transaction_hex}.journal"
    ));
    let active_head = activations_root.join("head.json");
    let retirement_staging_manifest = retirement_manifest.as_ref().map(|path| {
        path.with_file_name(format!(
            "{}.{}.staging",
            prior_hex.as_deref().unwrap_or("generation"),
            transaction_hex
        ))
    });
    let retirement_aborted_marker = retirement_manifest.as_ref().map(|path| {
        path.with_file_name(format!(
            "{}.{}.aborted",
            prior_hex.as_deref().unwrap_or("generation"),
            transaction_hex
        ))
    });
    let layout_projection = AuthorityGenerationLayout {
        binary_anchor: path_string(&binary_anchor)?,
        state_anchor: path_string(&state_anchor)?,
        binary_base: path_string(&binary_base)?,
        state_base: path_string(&state_base)?,
        binary_version_root: path_string(&binary_root)?,
        state_version_root: path_string(&state_root)?,
        binary_generations_root: path_string(&binary_generations_root)?,
        state_generations_root: path_string(&state_generations_root)?,
        activations_root: path_string(&activations_root)?,
        retirements_root: path_string(&retirements_root)?,
        recoveries_root: path_string(&recoveries_root)?,
        active_head: path_string(&active_head)?,
        maintenance_journal: path_string(&maintenance_journal)?,
        generation_binary_root: path_string(&generation_binary_root)?,
        generation_state_root: path_string(&generation_state_root)?,
        service_executable: path_string(&service_executable)?,
        controller_executable: path_string(&controller_executable)?,
        install_helper_executable: path_string(&install_helper_executable)?,
        ledger_file: path_string(&ledger_file)?,
        trust_manifest: path_string(&trust_manifest)?,
        activation_manifest: path_string(&activation_manifest)?,
        retirement_manifest: retirement_manifest
            .as_ref()
            .map(|path| path_string(path))
            .transpose()?,
        retirement_staging_manifest: retirement_staging_manifest
            .as_ref()
            .map(|path| path_string(path))
            .transpose()?,
        retirement_aborted_marker: retirement_aborted_marker
            .as_ref()
            .map(|path| path_string(path))
            .transpose()?,
        recovery_manifest: path_string(&recovery_manifest)?,
    };
    let mut steps = build_steps(
        operation,
        &content,
        prior.as_ref(),
        &layout_projection,
        &fixed_policy,
        &transaction_hex,
    );
    let plan_digest = derive_full_plan_digest(
        operation,
        &generation,
        prior.as_ref(),
        &content,
        &policy_sha256,
        &layout_projection,
        &fixed_policy,
        &steps,
    )?;
    let plan_hex = hex_lower(&plan_digest);
    match steps.first_mut().map(|step| &mut step.action) {
        Some(AuthorityMaintenanceAction::CreateDurableJournal { plan_sha256, .. }) => {
            *plan_sha256 = plan_hex.clone();
        }
        _ => {
            return Err(AuthorityMaintenanceError(
                "authority_journal_must_precede_mutation",
            ))
        }
    }
    let journal = JournalContractProjection {
        schema: MAINTENANCE_JOURNAL_SCHEMA,
        anchor_path: path_string(&state_anchor)?,
        anchor_source: "verifiedKnownFolderHandle",
        anchor_handle_held: true,
        anchor_stable_object_identity_required: true,
        anchor_reparse_points_rejected: true,
        path: path_string(&maintenance_journal)?,
        transaction_sha256: transaction_hex.clone(),
        plan_sha256: plan_hex.clone(),
        create_new: true,
        create_relative_to_anchor_handle: true,
        preexisting_path_rejected: true,
        exact_security_required: true,
        owner_sid: LOCAL_SYSTEM_SID,
        write_through: true,
        flush_file_after_every_transition: true,
        flush_parent_after_create: true,
        startup_recovery_required: true,
        terminal_states: ["committed", "rolledBack", "contained"],
        identical_terminal_is_idempotent: true,
        conflicting_terminal_rejected: true,
    };
    Ok(AuthorityMaintenancePreview {
        schema: MAINTENANCE_PREVIEW_SCHEMA,
        operation,
        generation: generation_hex,
        prior_generation: prior_hex,
        prior_generation_readback: prior.as_ref().map(PriorGenerationProjection::from),
        transaction_sha256: transaction_hex,
        plan_sha256: plan_hex,
        policy_sha256: hex_lower(&policy_sha256),
        content: ContentProjection::from(&content),
        layout: layout_projection,
        journal,
        fixed_policy,
        steps,
        automatic_execution_allowed: false,
        native_mutation_backend_available: false,
        execution_requires_verified_elevated_maintenance_capability: true,
        trusted_boundary_ready: false,
        blockers: vec![
            "authority_native_mutation_backend_disabled",
            "authority_protected_readback_required",
            "authority_service_supervision_not_connected",
        ],
    })
}

fn build_steps(
    operation: AuthorityMaintenanceOperation,
    content: &AuthorityInstallContent,
    prior: Option<&VerifiedInstalledGeneration>,
    layout: &AuthorityGenerationLayout,
    policy: &FixedPolicyProjection,
    transaction_sha256: &str,
) -> Vec<AuthorityMaintenanceStep> {
    let recovery = AuthorityRollbackAction::SealGenerationConsumed {
        recovery_manifest: layout.recovery_manifest.clone(),
    };
    let mut stable_parent_steps = vec![AuthorityMaintenanceStep {
        id: "createDurableJournal",
        action: AuthorityMaintenanceAction::CreateDurableJournal {
            anchor_path: layout.state_anchor.clone(),
            anchor_source: "verifiedKnownFolderHandle",
            anchor_handle_held: true,
            anchor_stable_object_identity_required: true,
            anchor_reparse_points_rejected: true,
            path: layout.maintenance_journal.clone(),
            transaction_sha256: transaction_sha256.to_string(),
            plan_sha256: String::new(),
            security_sddl: STATE_FILE_SDDL,
            owner_sid: LOCAL_SYSTEM_SID,
            exact_security_required: true,
            create_relative_to_anchor_handle: true,
            preexisting_path_rejected: true,
            create_new: true,
            never_reuse: true,
            write_through: true,
            flush_parent: true,
            flush_file_after_every_transition: true,
            recover_before_new_transaction: true,
            terminal_states: ["committed", "rolledBack", "contained"],
            identical_terminal_is_idempotent: true,
            conflicting_terminal_rejected: true,
            plan_digest_excludes_own_field: true,
        },
        failed_apply_cleanup: AuthorityRollbackAction::None,
        rollback: AuthorityRollbackAction::None,
    }];
    stable_parent_steps.extend(protected_parent_steps(layout));
    if operation == AuthorityMaintenanceOperation::Retire {
        let prior_generation = prior
            .map(|value| hex_lower(&value.generation))
            .unwrap_or_default();
        let prior = prior.expect("operation validation requires a prior generation");
        let restore = AuthorityRollbackAction::RestoreRetiredServiceConfiguration {
            generation: prior_generation.clone(),
            require_generation_handshake: true,
        };
        let abort_staging = AuthorityRollbackAction::MarkRetirementAbortedNoReuse {
            staging_path: layout
                .retirement_staging_manifest
                .clone()
                .unwrap_or_default(),
            aborted_marker_path: layout.retirement_aborted_marker.clone().unwrap_or_default(),
            write_through: true,
        };
        stable_parent_steps.push(stop_drain_step(prior, restore.clone()));
        stable_parent_steps.push(AuthorityMaintenanceStep {
            id: "removeServiceRegistration",
            action: AuthorityMaintenanceAction::RemoveServiceRegistration {
                service_name: AUTHORITY_SERVICE_NAME,
                requires_prior_stop_drain_proof: true,
            },
            failed_apply_cleanup: restore.clone(),
            rollback: restore,
        });
        stable_parent_steps.push(AuthorityMaintenanceStep {
            id: "stageRetirementTombstone",
            action: AuthorityMaintenanceAction::StageRetirementTombstone {
                staging_path: layout
                    .retirement_staging_manifest
                    .clone()
                    .unwrap_or_default(),
                final_path: layout.retirement_manifest.clone().unwrap_or_default(),
                aborted_marker_path: layout.retirement_aborted_marker.clone().unwrap_or_default(),
                contract: retirement_manifest_contract(prior, None, None),
                create_new: true,
                never_reuse: true,
                write_through: true,
                flush_file_before_completion: true,
                flush_parent_after_create: true,
                rehash_from_held_handle: true,
                complete_only_after_signature_and_exact_readback: true,
            },
            failed_apply_cleanup: abort_staging.clone(),
            rollback: abort_staging.clone(),
        });
        stable_parent_steps.push(AuthorityMaintenanceStep {
            id: "verifyRetirementPreconditions",
            action: AuthorityMaintenanceAction::VerifyProtectedReadback {
                generation: prior_generation.clone(),
                require_service_absent: true,
            },
            failed_apply_cleanup: abort_staging.clone(),
            rollback: abort_staging.clone(),
        });
        stable_parent_steps.push(AuthorityMaintenanceStep {
            id: "finalizeRetirementTombstone",
            action: AuthorityMaintenanceAction::FinalizeRetirementTombstoneAtomic {
                staging_path: layout
                    .retirement_staging_manifest
                    .clone()
                    .unwrap_or_default(),
                final_path: layout.retirement_manifest.clone().unwrap_or_default(),
                aborted_marker_path: layout.retirement_aborted_marker.clone().unwrap_or_default(),
                no_replace: true,
                flush_parent: true,
                aborted_marker_forbids_reuse: true,
                active_head_path: layout.active_head.clone(),
                expected_active_generation: prior_generation.clone(),
                expected_active_activation: verified_prior_activation_reference(prior),
                expected_active_epoch: prior.activation_epoch,
                compare_exchange_single_head: true,
                active_head_result: "retiredNoActiveGeneration",
                irreversible_commit: true,
                post_commit_failure_policy: "containWithoutGenerationRevival",
            },
            failed_apply_cleanup: abort_staging,
            rollback: recovery.clone(),
        });
        stable_parent_steps.push(AuthorityMaintenanceStep {
            id: "verifyRetiredReadback",
            action: AuthorityMaintenanceAction::VerifyProtectedReadback {
                generation: prior_generation,
                require_service_absent: true,
            },
            failed_apply_cleanup: recovery.clone(),
            rollback: AuthorityRollbackAction::None,
        });
        return stable_parent_steps;
    }

    let mut steps = stable_parent_steps;
    let target_generation = layout
        .generation_binary_root
        .rsplit(['\\', '/'])
        .next()
        .unwrap_or_default()
        .to_string();
    steps.extend([
        directory_step(
            "createBinaryGenerationDirectory",
            &layout.generation_binary_root,
            &layout.binary_generations_root,
            BINARY_DIRECTORY_SDDL,
            recovery.clone(),
        ),
        payload_step(
            "createServiceExecutable",
            "service",
            &layout.service_executable,
            content.service,
            recovery.clone(),
        ),
        payload_step(
            "createControllerExecutable",
            "controller",
            &layout.controller_executable,
            content.controller,
            recovery.clone(),
        ),
        payload_step(
            "createInstallHelperExecutable",
            "installHelper",
            &layout.install_helper_executable,
            content.install_helper,
            recovery.clone(),
        ),
        directory_step(
            "createStateGenerationDirectory",
            &layout.generation_state_root,
            &layout.state_generations_root,
            STATE_DIRECTORY_SDDL,
            recovery.clone(),
        ),
        AuthorityMaintenanceStep {
            id: "provisionMachineKey",
            action: AuthorityMaintenanceAction::ProvisionMachineKey {
                key_name: policy.key_name.clone(),
                algorithm: policy.key_algorithm,
                key_length_bits: policy.key_length_bits,
                usage: policy.key_usage,
                export_policy: policy.key_export_policy,
                security_sddl: policy.key_security_sddl,
                flush_provider_state_before_completion: true,
                complete_only_after_protected_readback: true,
                create_new: true,
                never_reuse: true,
            },
            failed_apply_cleanup: recovery.clone(),
            rollback: recovery.clone(),
        },
        AuthorityMaintenanceStep {
            id: "provisionLedger",
            action: AuthorityMaintenanceAction::ProvisionLedger {
                path: layout.ledger_file.clone(),
                identity_source: policy.ledger_identity_source,
                frame_size: policy.ledger_frame_size,
                max_result_size: policy.ledger_max_result_size,
                security_sddl: STATE_FILE_SDDL,
                write_through: true,
                flush_file_before_completion: true,
                flush_parent_after_create: true,
                rehash_identity_from_held_handle: true,
                complete_only_after_exact_readback: true,
                create_new: true,
                never_reuse: true,
            },
            failed_apply_cleanup: recovery.clone(),
            rollback: recovery.clone(),
        },
        AuthorityMaintenanceStep {
            id: "writeTrustManifest",
            action: AuthorityMaintenanceAction::WriteSignedManifest {
                path: layout.trust_manifest.clone(),
                contract: trust_manifest_contract(&target_generation),
                security_sddl: STATE_FILE_SDDL,
                write_through: true,
                flush_file_before_completion: true,
                flush_parent_after_create: true,
                rehash_from_held_handle: true,
                complete_only_after_signature_and_exact_readback: true,
                create_new: true,
                never_reuse: true,
            },
            failed_apply_cleanup: recovery.clone(),
            rollback: recovery.clone(),
        },
    ]);
    let service_rollback = match (operation, prior) {
        (AuthorityMaintenanceOperation::Install, _) => {
            AuthorityRollbackAction::RemoveNewServiceRegistration {
                generation: target_generation.clone(),
                require_stop_drain_proof: true,
            }
        }
        (AuthorityMaintenanceOperation::Update, Some(prior)) => {
            AuthorityRollbackAction::RestorePriorServiceConfiguration {
                generation: hex_lower(&prior.generation),
                require_generation_handshake: true,
            }
        }
        _ => AuthorityRollbackAction::None,
    };
    if operation == AuthorityMaintenanceOperation::Update {
        steps.push(stop_drain_step(
            prior.expect("operation validation requires a prior generation"),
            service_rollback.clone(),
        ));
    }
    steps.push(AuthorityMaintenanceStep {
        id: "configureServiceExact",
        action: AuthorityMaintenanceAction::ConfigureServiceExact {
            operation: if operation == AuthorityMaintenanceOperation::Install {
                "createNew"
            } else {
                "changeExact"
            },
            configuration: policy.service.clone(),
            requires_prior_stop_drain_proof: operation == AuthorityMaintenanceOperation::Update,
        },
        failed_apply_cleanup: service_rollback.clone(),
        rollback: service_rollback.clone(),
    });
    steps.push(AuthorityMaintenanceStep {
        id: "writeActivationManifest",
        action: AuthorityMaintenanceAction::WriteSignedManifest {
            path: layout.activation_manifest.clone(),
            contract: activation_manifest_contract(&target_generation, prior),
            security_sddl: STATE_FILE_SDDL,
            write_through: true,
            flush_file_before_completion: true,
            flush_parent_after_create: true,
            rehash_from_held_handle: true,
            complete_only_after_signature_and_exact_readback: true,
            create_new: true,
            never_reuse: true,
        },
        failed_apply_cleanup: recovery.clone(),
        rollback: recovery.clone(),
    });
    let active_head_rollback =
        AuthorityRollbackAction::RestoreActiveHeadAndSealGenerationConsumed {
            active_head_path: layout.active_head.clone(),
            target_generation: target_generation.clone(),
            target_activation: signed_successor_activation_reference(
                &target_generation,
                &layout.activation_manifest,
            ),
            target_epoch: prior
                .map(|value| value.activation_epoch.saturating_add(1))
                .unwrap_or(1),
            restore_previous_generation: prior.map(|value| hex_lower(&value.generation)),
            restore_previous_activation_sha256: prior
                .map(|value| hex_lower(&value.activation_manifest_sha256)),
            restore_previous_epoch: prior.map(|value| value.activation_epoch),
            delete_if_initial: prior.is_none(),
            compare_exchange_target_only: true,
            write_through: true,
            flush_parent_before_seal: true,
            recovery_manifest: layout.recovery_manifest.clone(),
        };
    steps.push(AuthorityMaintenanceStep {
        id: "advanceActiveHeadAtomic",
        action: AuthorityMaintenanceAction::AdvanceActiveHeadAtomic {
            path: layout.active_head.clone(),
            generation: target_generation.clone(),
            activation: signed_successor_activation_reference(
                &target_generation,
                &layout.activation_manifest,
            ),
            expected_previous_generation: prior.map(|value| hex_lower(&value.generation)),
            expected_previous_activation_sha256: prior
                .map(|value| hex_lower(&value.activation_manifest_sha256)),
            expected_epoch: prior
                .map(|value| value.activation_epoch.saturating_add(1))
                .unwrap_or(1),
            compare_exchange_single_head: true,
            reject_fork: true,
            write_through: true,
            flush_parent: true,
        },
        failed_apply_cleanup: active_head_rollback.clone(),
        rollback: active_head_rollback,
    });
    steps.push(AuthorityMaintenanceStep {
        id: "startServiceWithGenerationHandshake",
        action: AuthorityMaintenanceAction::StartServiceWithGenerationHandshake {
            generation: target_generation.clone(),
            expected_image_sha256: hex_lower(&content.service.sha256),
            trust_manifest_path: layout.trust_manifest.clone(),
            require_new_process_identity: true,
            require_held_image_identity: true,
            require_pipe_generation_handshake: true,
        },
        failed_apply_cleanup: service_rollback.clone(),
        rollback: service_rollback.clone(),
    });
    if operation == AuthorityMaintenanceOperation::Update {
        let prior = prior.expect("operation validation requires a prior generation");
        let retirement_abort = AuthorityRollbackAction::MarkRetirementAbortedNoReuse {
            staging_path: layout
                .retirement_staging_manifest
                .clone()
                .unwrap_or_default(),
            aborted_marker_path: layout.retirement_aborted_marker.clone().unwrap_or_default(),
            write_through: true,
        };
        steps.push(AuthorityMaintenanceStep {
            id: "verifySuccessorBeforeRetirement",
            action: AuthorityMaintenanceAction::VerifyProtectedReadback {
                generation: target_generation.clone(),
                require_service_absent: false,
            },
            failed_apply_cleanup: recovery.clone(),
            rollback: recovery.clone(),
        });
        steps.push(AuthorityMaintenanceStep {
            id: "stagePriorRetirementTombstone",
            action: AuthorityMaintenanceAction::StageRetirementTombstone {
                staging_path: layout
                    .retirement_staging_manifest
                    .clone()
                    .unwrap_or_default(),
                final_path: layout.retirement_manifest.clone().unwrap_or_default(),
                aborted_marker_path: layout.retirement_aborted_marker.clone().unwrap_or_default(),
                contract: retirement_manifest_contract(
                    prior,
                    Some(target_generation.clone()),
                    Some(signed_successor_activation_reference(
                        &target_generation,
                        &layout.activation_manifest,
                    )),
                ),
                create_new: true,
                never_reuse: true,
                write_through: true,
                flush_file_before_completion: true,
                flush_parent_after_create: true,
                rehash_from_held_handle: true,
                complete_only_after_signature_and_exact_readback: true,
            },
            failed_apply_cleanup: retirement_abort.clone(),
            rollback: retirement_abort.clone(),
        });
        steps.push(AuthorityMaintenanceStep {
            id: "finalizePriorRetirementTombstone",
            action: AuthorityMaintenanceAction::FinalizeRetirementTombstoneAtomic {
                staging_path: layout
                    .retirement_staging_manifest
                    .clone()
                    .unwrap_or_default(),
                final_path: layout.retirement_manifest.clone().unwrap_or_default(),
                aborted_marker_path: layout.retirement_aborted_marker.clone().unwrap_or_default(),
                no_replace: true,
                flush_parent: true,
                aborted_marker_forbids_reuse: true,
                active_head_path: layout.active_head.clone(),
                expected_active_generation: target_generation.clone(),
                expected_active_activation: signed_successor_activation_reference(
                    &target_generation,
                    &layout.activation_manifest,
                ),
                expected_active_epoch: prior.activation_epoch.saturating_add(1),
                compare_exchange_single_head: true,
                active_head_result: "unchangedSuccessor",
                irreversible_commit: true,
                post_commit_failure_policy: "containWithoutGenerationRevival",
            },
            failed_apply_cleanup: retirement_abort,
            rollback: recovery.clone(),
        });
    }
    steps.push(AuthorityMaintenanceStep {
        id: "verifyProtectedReadback",
        action: AuthorityMaintenanceAction::VerifyProtectedReadback {
            generation: target_generation,
            require_service_absent: false,
        },
        failed_apply_cleanup: AuthorityRollbackAction::None,
        rollback: AuthorityRollbackAction::None,
    });
    steps
}

fn stop_drain_step(
    prior: &VerifiedInstalledGeneration,
    rollback: AuthorityRollbackAction,
) -> AuthorityMaintenanceStep {
    AuthorityMaintenanceStep {
        id: "stopDrainPriorServiceExact",
        action: AuthorityMaintenanceAction::StopDrainServiceExact {
            generation: hex_lower(&prior.generation),
            expected_process_id: prior.service_runtime.process_id,
            expected_process_creation_time: prior.service_runtime.process_creation_time,
            expected_image_sha256: hex_lower(&prior.service_runtime.image_sha256),
            expected_pipe_instance_id: hex_lower(&prior.service_runtime.pipe_instance_id),
            require_exact_process_identity: true,
            require_held_image_identity: true,
            require_pipe_close_proof: true,
            require_scm_stopped_readback: true,
        },
        failed_apply_cleanup: rollback.clone(),
        rollback,
    }
}

fn verified_prior_activation_reference(
    prior: &VerifiedInstalledGeneration,
) -> ProtectedActivationDigestReference {
    ProtectedActivationDigestReference::VerifiedInstalledGeneration {
        generation: hex_lower(&prior.generation),
        activation_sha256: hex_lower(&prior.activation_manifest_sha256),
        source: "verifiedInstalledGeneration",
    }
}

fn signed_successor_activation_reference(
    generation: &str,
    manifest_path: &str,
) -> ProtectedActivationDigestReference {
    ProtectedActivationDigestReference::SignedManifestHeldHandleReadback {
        generation: generation.to_string(),
        manifest_path: manifest_path.to_string(),
        source: "signedActivationManifestHeldHandle",
        require_file_flush_before_readback: true,
        require_held_handle: true,
        require_stable_file_identity: true,
        require_canonical_unsigned_payload_digest: true,
        require_detached_signature_verification: true,
        complete_only_after_exact_generation_and_digest_readback: true,
    }
}

fn protected_parent_steps(layout: &AuthorityGenerationLayout) -> Vec<AuthorityMaintenanceStep> {
    vec![
        protected_directory_step(
            "ensureBinaryBase",
            &layout.binary_base,
            &layout.binary_anchor,
            BINARY_DIRECTORY_SDDL,
        ),
        protected_directory_step(
            "ensureBinaryVersionRoot",
            &layout.binary_version_root,
            &layout.binary_base,
            BINARY_DIRECTORY_SDDL,
        ),
        protected_directory_step(
            "ensureBinaryGenerationsRoot",
            &layout.binary_generations_root,
            &layout.binary_version_root,
            BINARY_DIRECTORY_SDDL,
        ),
        protected_directory_step(
            "ensureStateBase",
            &layout.state_base,
            &layout.state_anchor,
            STATE_DIRECTORY_SDDL,
        ),
        protected_directory_step(
            "ensureStateVersionRoot",
            &layout.state_version_root,
            &layout.state_base,
            STATE_DIRECTORY_SDDL,
        ),
        protected_directory_step(
            "ensureStateGenerationsRoot",
            &layout.state_generations_root,
            &layout.state_version_root,
            STATE_DIRECTORY_SDDL,
        ),
        protected_directory_step(
            "ensureActivationsRoot",
            &layout.activations_root,
            &layout.state_version_root,
            STATE_DIRECTORY_SDDL,
        ),
        protected_directory_step(
            "ensureRetirementsRoot",
            &layout.retirements_root,
            &layout.state_version_root,
            STATE_DIRECTORY_SDDL,
        ),
        protected_directory_step(
            "ensureRecoveriesRoot",
            &layout.recoveries_root,
            &layout.state_version_root,
            STATE_DIRECTORY_SDDL,
        ),
    ]
}

fn protected_directory_step(
    id: &'static str,
    path: &str,
    parent_path: &str,
    security_sddl: &'static str,
) -> AuthorityMaintenanceStep {
    AuthorityMaintenanceStep {
        id,
        action: AuthorityMaintenanceAction::EnsureProtectedDirectory {
            path: path.to_string(),
            parent_path: parent_path.to_string(),
            security_sddl,
            owner_sid: LOCAL_SYSTEM_SID,
            create_if_missing: true,
            accept_existing: true,
            exact_security_required: true,
            reject_reparse_points: true,
            stable_object_identity_required: true,
            open_parent_by_handle: true,
            create_relative_to_parent_handle: true,
            retain_verified_handle: true,
        },
        failed_apply_cleanup: AuthorityRollbackAction::RestoreProtectedDirectoryState {
            path: path.to_string(),
        },
        rollback: AuthorityRollbackAction::None,
    }
}

fn directory_step(
    id: &'static str,
    path: &str,
    parent_path: &str,
    security_sddl: &'static str,
    rollback: AuthorityRollbackAction,
) -> AuthorityMaintenanceStep {
    AuthorityMaintenanceStep {
        id,
        action: AuthorityMaintenanceAction::CreateDirectory {
            path: path.to_string(),
            parent_path: parent_path.to_string(),
            security_sddl,
            owner_sid: LOCAL_SYSTEM_SID,
            exact_security_required: true,
            reject_reparse_points: true,
            stable_object_identity_required: true,
            open_parent_by_handle: true,
            create_relative_to_parent_handle: true,
            retain_verified_handle: true,
            create_new: true,
            never_reuse: true,
        },
        failed_apply_cleanup: rollback.clone(),
        rollback,
    }
}

fn payload_step(
    id: &'static str,
    payload: &'static str,
    path: &str,
    descriptor: AuthorityPayloadDigest,
    rollback: AuthorityRollbackAction,
) -> AuthorityMaintenanceStep {
    AuthorityMaintenanceStep {
        id,
        action: AuthorityMaintenanceAction::CreatePayloadFile {
            payload,
            path: path.to_string(),
            sha256: hex_lower(&descriptor.sha256),
            byte_length: descriptor.byte_length,
            security_sddl: BINARY_FILE_SDDL,
            source: "verifiedMaintenanceLeaseHeldHandle",
            source_handle_lease_required: true,
            source_write_sharing_denied: true,
            source_delete_sharing_denied: true,
            source_full_content_rehash_after_copy: true,
            destination_create_relative_to_verified_parent_handle: true,
            destination_handle_retained_through_readback: true,
            destination_write_delete_sharing_denied: true,
            write_through: true,
            flush_file_before_readback: true,
            flush_parent_after_create: true,
            rehash_destination_from_held_handle: true,
            verify_destination_stable_identity_and_path: true,
            complete_only_after_exact_readback: true,
            create_new: true,
            never_reuse: true,
        },
        failed_apply_cleanup: rollback.clone(),
        rollback,
    }
}

fn derive_generation(
    binary_root: &PathBuf,
    state_root: &PathBuf,
    content: &AuthorityInstallContent,
    policy_sha256: &[u8; 32],
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(GENERATION_DOMAIN);
    digest.update(canonical_path_binding(binary_root));
    digest.update([0]);
    digest.update(canonical_path_binding(state_root));
    for descriptor in [content.service, content.controller, content.install_helper] {
        digest.update(descriptor.sha256);
        digest.update(descriptor.byte_length.to_be_bytes());
    }
    digest.update(policy_sha256);
    digest.finalize().into()
}

fn derive_transaction_digest(
    operation: AuthorityMaintenanceOperation,
    generation: &[u8; 32],
    prior: Option<&VerifiedInstalledGeneration>,
    content: &AuthorityInstallContent,
    policy_sha256: &[u8; 32],
    binary_root: &PathBuf,
    state_root: &PathBuf,
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(TRANSACTION_DOMAIN);
    digest.update([match operation {
        AuthorityMaintenanceOperation::Install => 1,
        AuthorityMaintenanceOperation::Update => 2,
        AuthorityMaintenanceOperation::Retire => 3,
    }]);
    digest.update(generation);
    digest.update(prior.map(|value| value.generation).unwrap_or([0; 32]));
    if let Some(prior) = prior {
        digest.update(prior.signer_key_id);
        digest.update(prior.signer_public_key_sec1);
        digest.update(prior.trust_manifest_sha256);
        digest.update(prior.activation_manifest_sha256);
        digest.update(prior.activation_epoch.to_be_bytes());
    }
    digest.update(content.service.sha256);
    digest.update(content.service.byte_length.to_be_bytes());
    digest.update(content.controller.sha256);
    digest.update(content.controller.byte_length.to_be_bytes());
    digest.update(content.install_helper.sha256);
    digest.update(content.install_helper.byte_length.to_be_bytes());
    digest.update(policy_sha256);
    digest.update(canonical_path_binding(binary_root));
    digest.update(canonical_path_binding(state_root));
    digest.finalize().into()
}

#[allow(clippy::too_many_arguments)]
pub(super) fn derive_full_plan_digest(
    operation: AuthorityMaintenanceOperation,
    generation: &[u8; 32],
    prior: Option<&VerifiedInstalledGeneration>,
    content: &AuthorityInstallContent,
    policy_sha256: &[u8; 32],
    layout: &AuthorityGenerationLayout,
    fixed_policy: &FixedPolicyProjection,
    steps: &[AuthorityMaintenanceStep],
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    match steps.first().map(|step| &step.action) {
        Some(AuthorityMaintenanceAction::CreateDurableJournal { plan_sha256, .. })
            if plan_sha256.is_empty() => {}
        _ => {
            return Err(AuthorityMaintenanceError(
                "authority_plan_self_field_not_normalized",
            ))
        }
    }
    let prior_projection = prior.map(PriorGenerationProjection::from);
    let content_projection = ContentProjection::from(content);
    let canonical = serde_json::to_vec(&(
        MAINTENANCE_PREVIEW_SCHEMA,
        operation,
        hex_lower(generation),
        prior_projection,
        hex_lower(policy_sha256),
        content_projection,
        layout,
        fixed_policy,
        steps,
    ))
    .map_err(|_| AuthorityMaintenanceError("authority_plan_canonicalization_failed"))?;
    let mut digest = Sha256::new();
    digest.update(PLAN_DOMAIN);
    digest.update(canonical);
    Ok(digest.finalize().into())
}

fn fixed_policy_seed() -> Vec<u8> {
    let mut value = Vec::new();
    value.extend_from_slice(LEDGER_DOMAIN);
    for item in [
        AUTHORITY_SERVICE_NAME,
        AUTHORITY_SERVICE_DISPLAY_NAME,
        AUTHORITY_SERVICE_ACCOUNT,
        SERVICE_SID,
        AUTHORITY_PIPE_NAME,
        AUTHORITY_PIPE_SDDL,
        BINARY_DIRECTORY_SDDL,
        BINARY_FILE_SDDL,
        STATE_DIRECTORY_SDDL,
        STATE_FILE_SDDL,
        SERVICE_SECURITY_SDDL,
        KEY_SECURITY_SDDL,
        AUTHORITY_KEY_NAME_PREFIX,
        "ownProcess",
        "demand",
        "normal",
        "restricted",
        "ECDSA_P256",
        "signOnly",
        "noExport",
        TRUST_MANIFEST_SCHEMA,
        ACTIVE_GENERATION_SCHEMA,
        RETIREMENT_MANIFEST_SCHEMA,
        RECOVERY_MANIFEST_SCHEMA,
        "manifestVersion=1",
        "protectedActivationChainEpoch",
        "valid",
        "revoked",
        "previousGeneration",
        "previousActivationDigest",
        "previousActivationEpoch",
        "runtimeDerivedSignerAndManifestDigest",
        "protectedDirectoryOwner=S-1-5-18",
        "protectedDirectoryExactSecurity",
        "protectedDirectoryRejectReparsePoints",
        "protectedDirectoryStableObjectIdentity",
        "protectedDirectoryOpenParentByHandle",
        "protectedDirectoryCreateRelativeToParentHandle",
        "protectedDirectoryRetainVerifiedHandle",
    ] {
        value.extend_from_slice(item.as_bytes());
        value.push(0);
    }
    for privilege in AUTHORITY_REQUIRED_PRIVILEGES {
        value.extend_from_slice(privilege.as_bytes());
        value.push(0);
    }
    value.extend_from_slice(&(FRAME_SIZE as u64).to_be_bytes());
    value.extend_from_slice(&(MAX_RESULT_SIZE as u64).to_be_bytes());
    value
}

fn exact_service_command(path: &PathBuf) -> Result<String, AuthorityMaintenanceError> {
    let value = path_string(path)?;
    if value.contains('"') || value.is_empty() {
        return Err(AuthorityMaintenanceError(
            "authority_service_command_invalid",
        ));
    }
    Ok(format!("\"{value}\" --service"))
}

fn path_string(path: &std::path::Path) -> Result<String, AuthorityMaintenanceError> {
    path.to_str()
        .filter(|value| !value.is_empty() && !value.contains('\0'))
        .map(str::to_string)
        .ok_or(AuthorityMaintenanceError("authority_layout_path_invalid"))
}

fn canonical_path_binding(path: &std::path::Path) -> Vec<u8> {
    path.to_string_lossy()
        .replace('/', "\\")
        .to_ascii_lowercase()
        .into_bytes()
}
