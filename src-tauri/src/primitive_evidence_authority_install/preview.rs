use super::*;

const EXACT_TARGET_SERVICE_CONFIGURATION_DOMAIN: &[u8] =
    b"vrcforge-authority-exact-target-service-configuration-v1\0";
const FINAL_COMMIT_RECEIPT_LEAF: &str = "05-final-commit.receipt.json";
const FINAL_COMMIT_GATE_DERIVATION: &str =
    "authenticatedStoreRoot+fixedLeaf+schema+bindingProjection+validReceipt";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct PostcommitRuntimeRecoveryContract {
    allow_runtime_restart_after_authenticated_final_commit: bool,
    require_final_commit_receipt_immutable: bool,
    require_active_head_binding: bool,
    require_exact_service_configuration: bool,
    require_exact_service_image: bool,
    require_exact_generation: bool,
    require_final_commit_gate_binding: bool,
    forbid_final_commit_receipt_rewrite: bool,
    require_previous_precommit_runtime_absence: bool,
    require_start_or_adopt_new_runtime_process_identity: bool,
    require_serving_readback: bool,
}

impl PostcommitRuntimeRecoveryContract {
    fn exact() -> Self {
        Self {
            allow_runtime_restart_after_authenticated_final_commit: true,
            require_final_commit_receipt_immutable: true,
            require_active_head_binding: true,
            require_exact_service_configuration: true,
            require_exact_service_image: true,
            require_exact_generation: true,
            require_final_commit_gate_binding: true,
            forbid_final_commit_receipt_rewrite: true,
            require_previous_precommit_runtime_absence: true,
            require_start_or_adopt_new_runtime_process_identity: true,
            require_serving_readback: true,
        }
    }

    pub(super) fn is_exact(&self) -> bool {
        *self == Self::exact()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct ExactTargetServicePlan {
    operation: AuthorityMaintenanceOperation,
    configuration: ServiceConfigurationProjection,
    exact_service_configuration_sha256: [u8; 32],
    plan_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
    generation_sha256: [u8; 32],
    expected_service_image_sha256: [u8; 32],
    active_head_path: String,
    ledger_path: String,
    ledger_anchor_path: String,
    final_commit_store_root: String,
    final_commit_receipt_leaf: &'static str,
    final_commit_gate_derivation: &'static str,
    postcommit_runtime_recovery: PostcommitRuntimeRecoveryContract,
}

impl ExactTargetServicePlan {
    pub(super) fn operation(&self) -> AuthorityMaintenanceOperation {
        self.operation
    }

    pub(super) fn configuration(&self) -> &ServiceConfigurationProjection {
        &self.configuration
    }

    pub(super) fn exact_service_configuration_sha256(&self) -> [u8; 32] {
        self.exact_service_configuration_sha256
    }

    pub(super) fn plan_sha256(&self) -> [u8; 32] {
        self.plan_sha256
    }

    pub(super) fn transaction_sha256(&self) -> [u8; 32] {
        self.transaction_sha256
    }

    pub(super) fn generation_sha256(&self) -> [u8; 32] {
        self.generation_sha256
    }

    pub(super) fn expected_service_image_sha256(&self) -> [u8; 32] {
        self.expected_service_image_sha256
    }

    pub(super) fn active_head_path(&self) -> &str {
        &self.active_head_path
    }

    pub(super) fn ledger_path(&self) -> &str {
        &self.ledger_path
    }

    pub(super) fn ledger_anchor_path(&self) -> &str {
        &self.ledger_anchor_path
    }

    pub(super) fn final_commit_store_root(&self) -> &str {
        &self.final_commit_store_root
    }

    pub(super) fn final_commit_receipt_leaf(&self) -> &'static str {
        self.final_commit_receipt_leaf
    }

    pub(super) fn final_commit_gate_derivation(&self) -> &'static str {
        self.final_commit_gate_derivation
    }

    pub(super) fn postcommit_runtime_recovery(&self) -> PostcommitRuntimeRecoveryContract {
        self.postcommit_runtime_recovery
    }
}

impl AuthorityMaintenancePreview {
    pub(super) fn exact_target_service_plan(
        &self,
    ) -> Result<ExactTargetServicePlan, AuthorityMaintenanceError> {
        extract_exact_target_service_plan(self)
    }
}

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
    let security_policy = SecurityPolicyBundle::exact();
    let policy_seed = fixed_policy_seed(&security_policy)?;
    let policy_sha256: [u8; 32] = Sha256::digest(&policy_seed).into();
    if let Some(prior) = prior.as_ref() {
        let prior_content = AuthorityInstallContent::new(
            prior.service,
            prior.controller,
            prior.install_helper,
            prior.lifecycle_driver,
            prior.bridge_launcher,
            prior.runtime_source_manifest,
        )?;
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
    let binary_maintenance_root = binary_root.join("maintenance");
    let state_maintenance_root = state_root.join("maintenance");
    let candidate_activation_root = layout.candidate_activation_root();
    let worker_nonce_root = layout.worker_nonce_root();
    let candidate_consumption_root = layout.candidate_consumption_root();
    let activations_root = state_root.join("activations");
    let retirements_root = state_root.join("retirements");
    let recoveries_root = state_root.join("recoveries");
    let finalizer_commits_root = state_root.join("finalizer-commits");
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
    let lifecycle_driver_executable =
        generation_binary_root.join("vrcforge_primitive_lifecycle_driver.exe");
    let bridge_launcher_executable =
        generation_binary_root.join("vrcforge_primitive_bridge_launcher.exe");
    let runtime_source_manifest = generation_state_root.join(RUNTIME_SOURCE_MANIFEST_FILE_NAME);
    let runner_policy_state = generation_state_root.join(AUTHORITY_RUNNER_POLICY_STATE_FILE_NAME);
    let protected_blob_namespace =
        generation_state_root.join(AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME);
    let ledger_file = generation_state_root.join("ledger.bin");
    let ledger_anchor_file = generation_state_root.join("ledger.bin.anchor");
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
        security_policy,
        maintenance_service_sid: MAINTENANCE_SERVICE_SID,
        maintenance_candidate_service_access: MAINTENANCE_CANDIDATE_SERVICE_ACCESS,
        pipe_name: AUTHORITY_PIPE_NAME,
        pipe_security_sddl: AUTHORITY_PIPE_SDDL,
        binary_directory_sddl: BINARY_DIRECTORY_SDDL,
        binary_generation_directory_sddl: BINARY_GENERATION_DIRECTORY_SDDL,
        binary_file_sddl: BINARY_FILE_SDDL,
        state_directory_sddl: STATE_DIRECTORY_SDDL,
        state_generation_directory_sddl: STATE_GENERATION_DIRECTORY_SDDL,
        state_file_sddl: STATE_FILE_SDDL,
        runner_policy_file_name: AUTHORITY_RUNNER_POLICY_STATE_FILE_NAME,
        runner_policy_schema: RUNNER_POLICY_STATE_SCHEMA,
        runner_account_name: RUNNER_ACCOUNT_NAME,
        runner_install_requires_create_new: true,
        runner_update_requires_authenticated_prior: true,
        runner_existing_account_requires_exact_sid_and_rights: true,
        runner_profile_requires_exact_identity_and_security: true,
        runner_policy_immutable_state_file: true,
        protected_blob_directory_name: AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME,
        protected_blob_directory_staging_sddl: RUNTIME_BLOB_DIRECTORY_STAGING_SDDL,
        protected_blob_directory_final_sddl: RUNTIME_BLOB_DIRECTORY_FINAL_SDDL,
        protected_blob_file_sddl: RUNTIME_BLOB_FILE_SDDL,
        protected_blob_directory_authority_access:
            security_policy::RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS,
        protected_blob_file_authority_access: security_policy::RUNTIME_BLOB_FILE_AUTHORITY_ACCESS,
        protected_blob_file_read_access: security_policy::RUNTIME_BLOB_FILE_READ_ACCESS,
        protected_blob_file_cleanup_access: security_policy::RUNTIME_BLOB_FILE_CLEANUP_ACCESS,
        protected_blob_create_new: true,
        protected_blob_bootstrap_open_only: true,
        protected_blob_share_access: 0,
        candidate_activation_directory_sddl: CANDIDATE_ACTIVATION_DIRECTORY_SDDL,
        worker_nonce_directory_sddl: WORKER_NONCE_DIRECTORY_SDDL,
        candidate_consumption_directory_sddl: CANDIDATE_CONSUMPTION_DIRECTORY_SDDL,
        worker_nonce_file_sddl: WORKER_NONCE_FILE_SDDL,
        candidate_consumption_file_sddl: CANDIDATE_CONSUMPTION_FILE_SDDL,
        sealed_nonce_file_sddl: SEALED_NONCE_FILE_SDDL,
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
    let finalizer_commit_store_root = finalizer_commits_root.join(&transaction_hex);
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
        binary_maintenance_root: path_string(&binary_maintenance_root)?,
        state_maintenance_root: path_string(&state_maintenance_root)?,
        candidate_activation_root: path_string(&candidate_activation_root)?,
        worker_nonce_root: path_string(&worker_nonce_root)?,
        candidate_consumption_root: path_string(&candidate_consumption_root)?,
        activations_root: path_string(&activations_root)?,
        retirements_root: path_string(&retirements_root)?,
        recoveries_root: path_string(&recoveries_root)?,
        finalizer_commits_root: path_string(&finalizer_commits_root)?,
        finalizer_commit_store_root: path_string(&finalizer_commit_store_root)?,
        active_head: path_string(&active_head)?,
        maintenance_journal: path_string(&maintenance_journal)?,
        generation_binary_root: path_string(&generation_binary_root)?,
        generation_state_root: path_string(&generation_state_root)?,
        service_executable: path_string(&service_executable)?,
        controller_executable: path_string(&controller_executable)?,
        install_helper_executable: path_string(&install_helper_executable)?,
        lifecycle_driver_executable: path_string(&lifecycle_driver_executable)?,
        bridge_launcher_executable: path_string(&bridge_launcher_executable)?,
        runtime_source_manifest: path_string(&runtime_source_manifest)?,
        runner_policy_state: path_string(&runner_policy_state)?,
        protected_blob_namespace: path_string(&protected_blob_namespace)?,
        ledger_file: path_string(&ledger_file)?,
        ledger_anchor_file: path_string(&ledger_anchor_file)?,
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
    let preview = AuthorityMaintenancePreview {
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
            "authority_system_worker_staging_not_complete",
            "authority_protected_readback_required",
            "authority_service_supervision_not_connected",
        ],
    };
    if operation != AuthorityMaintenanceOperation::Retire {
        let _ = preview.exact_target_service_plan()?;
    }
    Ok(preview)
}

fn extract_exact_target_service_plan(
    preview: &AuthorityMaintenancePreview,
) -> Result<ExactTargetServicePlan, AuthorityMaintenanceError> {
    let expected_configure_operation = match preview.operation {
        AuthorityMaintenanceOperation::Install => "createNew",
        AuthorityMaintenanceOperation::Update => "changeExact",
        AuthorityMaintenanceOperation::Retire => return Err(exact_target_service_plan_invalid()),
    };
    if preview.schema != MAINTENANCE_PREVIEW_SCHEMA {
        return Err(exact_target_service_plan_invalid());
    }
    let generation_sha256 =
        decode_hex_32(&preview.generation).map_err(|_| exact_target_service_plan_invalid())?;
    let plan_sha256 =
        decode_hex_32(&preview.plan_sha256).map_err(|_| exact_target_service_plan_invalid())?;
    let transaction_sha256 = decode_hex_32(&preview.transaction_sha256)
        .map_err(|_| exact_target_service_plan_invalid())?;
    let expected_service_image_sha256 = decode_hex_32(&preview.content.service.sha256)
        .map_err(|_| exact_target_service_plan_invalid())?;
    if generation_sha256 == [0; 32]
        || plan_sha256 == [0; 32]
        || transaction_sha256 == [0; 32]
        || expected_service_image_sha256 == [0; 32]
        || preview.layout.service_executable.is_empty()
        || preview.layout.active_head.is_empty()
        || preview.layout.finalizer_commits_root.is_empty()
        || preview.layout.finalizer_commit_store_root.is_empty()
        || preview.layout.runner_policy_state.is_empty()
        || preview.layout.protected_blob_namespace.is_empty()
        || preview.layout.ledger_file.is_empty()
        || preview.layout.ledger_anchor_file.is_empty()
    {
        return Err(exact_target_service_plan_invalid());
    }
    validate_preview_plan_transaction_binding(preview)?;
    let expected_store_root = path_string(
        &PathBuf::from(&preview.layout.finalizer_commits_root).join(&preview.transaction_sha256),
    )
    .map_err(|_| exact_target_service_plan_invalid())?;
    if expected_store_root != preview.layout.finalizer_commit_store_root {
        return Err(exact_target_service_plan_invalid());
    }
    validate_finalizer_commit_store_root(preview)?;
    validate_ledger_pair_contract(preview)?;
    validate_runner_policy_path_contract(preview)?;
    validate_protected_blob_namespace_path_contract(preview)?;
    validate_protected_blob_namespace_action_contract(preview)?;

    let configure = exact_action_step(&preview.steps, "configureServiceExact", |action| {
        matches!(
            action,
            AuthorityMaintenanceAction::ConfigureServiceExact { .. }
        )
    })?;
    let configuration = match &configure.action {
        AuthorityMaintenanceAction::ConfigureServiceExact {
            operation,
            configuration,
            final_commit_store_root,
            final_commit_receipt_leaf,
            require_authenticated_final_commit_gate_in_launch_configuration,
            require_precommit_dormant_mode,
            forbid_controller_pipe_before_final_commit,
            requires_prior_stop_drain_proof,
        } => {
            if *operation != expected_configure_operation
                || final_commit_store_root != &preview.layout.finalizer_commit_store_root
                || *final_commit_receipt_leaf != FINAL_COMMIT_RECEIPT_LEAF
                || !*require_authenticated_final_commit_gate_in_launch_configuration
                || !*require_precommit_dormant_mode
                || !*forbid_controller_pipe_before_final_commit
                || *requires_prior_stop_drain_proof
                    != (preview.operation == AuthorityMaintenanceOperation::Update)
                || configuration != &preview.fixed_policy.service
                || !service_configuration_is_exact(configuration, &preview.layout)
            {
                return Err(exact_target_service_plan_invalid());
            }
            configuration.clone()
        }
        _ => return Err(exact_target_service_plan_invalid()),
    };

    validate_start_committed_runtime(preview, &expected_service_image_sha256)?;
    validate_persist_final_commit(preview, &expected_service_image_sha256)?;
    validate_postcommit_readback(preview, &expected_service_image_sha256)?;
    validate_operation_dormant_readback(preview, &expected_service_image_sha256)?;
    validate_exact_target_service_action_order(preview)?;

    Ok(ExactTargetServicePlan {
        operation: preview.operation,
        exact_service_configuration_sha256: exact_service_configuration_sha256(&configuration),
        configuration,
        plan_sha256,
        transaction_sha256,
        generation_sha256,
        expected_service_image_sha256,
        active_head_path: preview.layout.active_head.clone(),
        ledger_path: preview.layout.ledger_file.clone(),
        ledger_anchor_path: preview.layout.ledger_anchor_file.clone(),
        final_commit_store_root: preview.layout.finalizer_commit_store_root.clone(),
        final_commit_receipt_leaf: FINAL_COMMIT_RECEIPT_LEAF,
        final_commit_gate_derivation: FINAL_COMMIT_GATE_DERIVATION,
        postcommit_runtime_recovery: PostcommitRuntimeRecoveryContract::exact(),
    })
}

fn exact_action_step<'a>(
    steps: &'a [AuthorityMaintenanceStep],
    expected_id: &str,
    is_expected_variant: impl Fn(&AuthorityMaintenanceAction) -> bool,
) -> Result<&'a AuthorityMaintenanceStep, AuthorityMaintenanceError> {
    let index = exact_action_index(steps, expected_id, is_expected_variant)?;
    Ok(&steps[index])
}

fn exact_action_index(
    steps: &[AuthorityMaintenanceStep],
    expected_id: &str,
    is_expected_variant: impl Fn(&AuthorityMaintenanceAction) -> bool,
) -> Result<usize, AuthorityMaintenanceError> {
    let mut found = None;
    for (index, step) in steps.iter().enumerate() {
        let id_matches = step.id == expected_id;
        let variant_matches = is_expected_variant(&step.action);
        if id_matches || variant_matches {
            if !id_matches || !variant_matches || found.is_some() {
                return Err(exact_target_service_plan_invalid());
            }
            found = Some(index);
        }
    }
    found.ok_or_else(exact_target_service_plan_invalid)
}

fn exact_step_by_id<'a>(
    steps: &'a [AuthorityMaintenanceStep],
    expected_id: &str,
) -> Result<&'a AuthorityMaintenanceStep, AuthorityMaintenanceError> {
    let mut found = None;
    for step in steps.iter().filter(|step| step.id == expected_id) {
        if found.is_some() {
            return Err(exact_target_service_plan_invalid());
        }
        found = Some(step);
    }
    found.ok_or_else(exact_target_service_plan_invalid)
}

fn require_action_absent(
    steps: &[AuthorityMaintenanceStep],
    forbidden_id: &str,
    is_forbidden_variant: impl Fn(&AuthorityMaintenanceAction) -> bool,
) -> Result<(), AuthorityMaintenanceError> {
    if steps
        .iter()
        .any(|step| step.id == forbidden_id || is_forbidden_variant(&step.action))
    {
        return Err(exact_target_service_plan_invalid());
    }
    Ok(())
}

fn service_configuration_is_exact(
    configuration: &ServiceConfigurationProjection,
    layout: &AuthorityGenerationLayout,
) -> bool {
    let expected_binary_command = exact_service_command(&PathBuf::from(&layout.service_executable));
    configuration.name == AUTHORITY_SERVICE_NAME
        && configuration.display_name == AUTHORITY_SERVICE_DISPLAY_NAME
        && configuration.account == AUTHORITY_SERVICE_ACCOUNT
        && configuration.service_type == "ownProcess"
        && configuration.start == "demand"
        && configuration.error_control == "normal"
        && configuration.sid_type == "restricted"
        && configuration.service_sid == SERVICE_SID
        && configuration.required_privileges.as_slice() == AUTHORITY_REQUIRED_PRIVILEGES
        && expected_binary_command
            .as_ref()
            .is_ok_and(|value| value == &configuration.binary_command)
        && configuration.security_sddl == SERVICE_SECURITY_SDDL
}

fn validate_preview_plan_transaction_binding(
    preview: &AuthorityMaintenancePreview,
) -> Result<(), AuthorityMaintenanceError> {
    if preview.journal.schema != MAINTENANCE_JOURNAL_SCHEMA
        || preview.journal.transaction_sha256 != preview.transaction_sha256
        || preview.journal.plan_sha256 != preview.plan_sha256
        || preview.journal.path != preview.layout.maintenance_journal
    {
        return Err(exact_target_service_plan_invalid());
    }
    let expected_plan = derive_preview_plan_sha256(preview)?;
    let expected_transaction = derive_preview_transaction_sha256(preview)?;
    if decode_hex_32(&preview.plan_sha256).ok() != Some(expected_plan)
        || decode_hex_32(&preview.transaction_sha256).ok() != Some(expected_transaction)
    {
        return Err(exact_target_service_plan_invalid());
    }
    let step = exact_action_step(&preview.steps, "createDurableJournal", |action| {
        matches!(
            action,
            AuthorityMaintenanceAction::CreateDurableJournal { .. }
        )
    })?;
    if preview.steps.first().map(|first| std::ptr::eq(step, first)) != Some(true) {
        return Err(exact_target_service_plan_invalid());
    }
    match &step.action {
        AuthorityMaintenanceAction::CreateDurableJournal {
            path,
            transaction_sha256,
            plan_sha256,
            ..
        } if path == &preview.layout.maintenance_journal
            && transaction_sha256 == &preview.transaction_sha256
            && plan_sha256 == &preview.plan_sha256 =>
        {
            Ok(())
        }
        _ => Err(exact_target_service_plan_invalid()),
    }
}

fn derive_preview_plan_sha256(
    preview: &AuthorityMaintenancePreview,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let mut normalized_steps = preview.steps.clone();
    match normalized_steps.first_mut().map(|step| &mut step.action) {
        Some(AuthorityMaintenanceAction::CreateDurableJournal { plan_sha256, .. }) => {
            plan_sha256.clear();
        }
        _ => return Err(exact_target_service_plan_invalid()),
    }
    let canonical = serde_json::to_vec(&(
        MAINTENANCE_PREVIEW_SCHEMA,
        preview.operation,
        preview.generation.clone(),
        preview.prior_generation_readback.clone(),
        preview.policy_sha256.clone(),
        preview.content.clone(),
        preview.layout.clone(),
        preview.fixed_policy.clone(),
        normalized_steps,
    ))
    .map_err(|_| exact_target_service_plan_invalid())?;
    let mut digest = Sha256::new();
    digest.update(PLAN_DOMAIN);
    digest.update(canonical);
    Ok(digest.finalize().into())
}

fn derive_preview_transaction_sha256(
    preview: &AuthorityMaintenancePreview,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let generation =
        decode_hex_32(&preview.generation).map_err(|_| exact_target_service_plan_invalid())?;
    let service = decode_hex_32(&preview.content.service.sha256)
        .map_err(|_| exact_target_service_plan_invalid())?;
    let controller = decode_hex_32(&preview.content.controller.sha256)
        .map_err(|_| exact_target_service_plan_invalid())?;
    let install_helper = decode_hex_32(&preview.content.install_helper.sha256)
        .map_err(|_| exact_target_service_plan_invalid())?;
    let lifecycle_driver = decode_hex_32(&preview.content.lifecycle_driver.sha256)
        .map_err(|_| exact_target_service_plan_invalid())?;
    let bridge_launcher = decode_hex_32(&preview.content.bridge_launcher.sha256)
        .map_err(|_| exact_target_service_plan_invalid())?;
    let runtime_source_manifest = decode_hex_32(&preview.content.runtime_source_manifest.sha256)
        .map_err(|_| exact_target_service_plan_invalid())?;
    let policy =
        decode_hex_32(&preview.policy_sha256).map_err(|_| exact_target_service_plan_invalid())?;
    let mut digest = Sha256::new();
    digest.update(TRANSACTION_DOMAIN);
    digest.update([match preview.operation {
        AuthorityMaintenanceOperation::Install => 1,
        AuthorityMaintenanceOperation::Update => 2,
        AuthorityMaintenanceOperation::Retire => 3,
    }]);
    digest.update(generation);
    match &preview.prior_generation_readback {
        Some(prior) => {
            if preview.prior_generation.as_deref() != Some(prior.generation.as_str())
                || prior.manifest_version != 1
                || !prior.valid
                || prior.revoked
            {
                return Err(exact_target_service_plan_invalid());
            }
            let prior_generation = decode_hex_32(&prior.generation)
                .map_err(|_| exact_target_service_plan_invalid())?;
            let signer_key_id = decode_hex_32(&prior.signer_key_id)
                .map_err(|_| exact_target_service_plan_invalid())?;
            let signer_public_key = decode_hex_exact_vec(&prior.signer_public_key_sec1, 65)?;
            let trust_manifest = decode_hex_32(&prior.trust_manifest_sha256)
                .map_err(|_| exact_target_service_plan_invalid())?;
            let activation_manifest = decode_hex_32(&prior.activation_manifest_sha256)
                .map_err(|_| exact_target_service_plan_invalid())?;
            digest.update(prior_generation);
            digest.update(signer_key_id);
            digest.update(signer_public_key);
            digest.update(trust_manifest);
            digest.update(activation_manifest);
            digest.update(prior.activation_epoch.to_be_bytes());
        }
        None => {
            if preview.prior_generation.is_some() {
                return Err(exact_target_service_plan_invalid());
            }
            digest.update([0; 32]);
        }
    }
    digest.update(service);
    digest.update(preview.content.service.byte_length.to_be_bytes());
    digest.update(controller);
    digest.update(preview.content.controller.byte_length.to_be_bytes());
    digest.update(install_helper);
    digest.update(preview.content.install_helper.byte_length.to_be_bytes());
    digest.update(lifecycle_driver);
    digest.update(preview.content.lifecycle_driver.byte_length.to_be_bytes());
    digest.update(bridge_launcher);
    digest.update(preview.content.bridge_launcher.byte_length.to_be_bytes());
    digest.update(runtime_source_manifest);
    digest.update(
        preview
            .content
            .runtime_source_manifest
            .byte_length
            .to_be_bytes(),
    );
    digest.update(policy);
    digest.update(canonical_path_binding(&PathBuf::from(
        &preview.layout.binary_version_root,
    )));
    digest.update(canonical_path_binding(&PathBuf::from(
        &preview.layout.state_version_root,
    )));
    Ok(digest.finalize().into())
}

fn decode_hex_exact_vec(
    value: &str,
    byte_length: usize,
) -> Result<Vec<u8>, AuthorityMaintenanceError> {
    if value.len() != byte_length.saturating_mul(2) {
        return Err(exact_target_service_plan_invalid());
    }
    (0..byte_length)
        .map(|index| {
            u8::from_str_radix(&value[index * 2..index * 2 + 2], 16)
                .map_err(|_| exact_target_service_plan_invalid())
        })
        .collect()
}

fn validate_finalizer_commit_store_root(
    preview: &AuthorityMaintenancePreview,
) -> Result<(), AuthorityMaintenanceError> {
    let step = exact_step_by_id(&preview.steps, "createFinalizerCommitStoreRoot")?;
    let target_path_create_count = preview
        .steps
        .iter()
        .filter(|candidate| {
            matches!(
                &candidate.action,
                AuthorityMaintenanceAction::CreateDirectory { path, .. }
                    if path == &preview.layout.finalizer_commit_store_root
            )
        })
        .count();
    if target_path_create_count != 1 {
        return Err(exact_target_service_plan_invalid());
    }
    match &step.action {
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
        } if path == &preview.layout.finalizer_commit_store_root
            && parent_path == &preview.layout.finalizer_commits_root
            && *security_sddl == security_policy::FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL
            && *owner_sid == LOCAL_SYSTEM_SID
            && *exact_security_required
            && *reject_reparse_points
            && *stable_object_identity_required
            && *open_parent_by_handle
            && *create_relative_to_parent_handle
            && *retain_verified_handle
            && *create_new
            && *never_reuse =>
        {
            Ok(())
        }
        _ => Err(exact_target_service_plan_invalid()),
    }
}

fn validate_ledger_pair_contract(
    preview: &AuthorityMaintenancePreview,
) -> Result<(), AuthorityMaintenanceError> {
    let ledger_path = PathBuf::from(&preview.layout.ledger_file);
    let anchor_path = PathBuf::from(&preview.layout.ledger_anchor_file);
    let generation_state_root = PathBuf::from(&preview.layout.generation_state_root);
    if ledger_path.parent() != Some(generation_state_root.as_path())
        || anchor_path.parent() != Some(generation_state_root.as_path())
        || ledger_path.file_name().and_then(|value| value.to_str()) != Some("ledger.bin")
        || anchor_path.file_name().and_then(|value| value.to_str()) != Some("ledger.bin.anchor")
        || ledger_path == anchor_path
    {
        return Err(exact_target_service_plan_invalid());
    }
    let step = exact_action_step(&preview.steps, "provisionLedger", |action| {
        matches!(action, AuthorityMaintenanceAction::ProvisionLedger { .. })
    })?;
    match &step.action {
        AuthorityMaintenanceAction::ProvisionLedger {
            path,
            anchor_path,
            identity_source,
            frame_size,
            max_result_size,
            security_sddl,
            write_through,
            flush_file_before_completion,
            flush_anchor_before_completion,
            flush_parent_after_create,
            rehash_identity_from_held_handle,
            rehash_anchor_from_held_handle,
            complete_only_after_exact_readback,
            complete_only_after_exact_pair_readback,
            create_pair_relative_to_verified_parent_handle,
            retain_both_handles_through_pair_readback,
            deny_write_delete_sharing_for_both,
            verify_each_local_reparse_free_single_link,
            require_distinct_physical_file_identities,
            persist_durable_pair_receipt_before_completion,
            create_new,
            anchor_create_new,
            never_reuse,
            anchor_never_reuse,
        } if path == &preview.layout.ledger_file
            && anchor_path == &preview.layout.ledger_anchor_file
            && *identity_source == preview.fixed_policy.ledger_identity_source
            && *frame_size == preview.fixed_policy.ledger_frame_size
            && *max_result_size == preview.fixed_policy.ledger_max_result_size
            && *security_sddl == LEDGER_FILE_SDDL
            && *write_through
            && *flush_file_before_completion
            && *flush_anchor_before_completion
            && *flush_parent_after_create
            && *rehash_identity_from_held_handle
            && *rehash_anchor_from_held_handle
            && *complete_only_after_exact_readback
            && *complete_only_after_exact_pair_readback
            && *create_pair_relative_to_verified_parent_handle
            && *retain_both_handles_through_pair_readback
            && *deny_write_delete_sharing_for_both
            && *verify_each_local_reparse_free_single_link
            && *require_distinct_physical_file_identities
            && *persist_durable_pair_receipt_before_completion
            && *create_new
            && *anchor_create_new
            && *never_reuse
            && *anchor_never_reuse =>
        {
            Ok(())
        }
        _ => Err(exact_target_service_plan_invalid()),
    }
}

fn validate_runner_policy_path_contract(
    preview: &AuthorityMaintenancePreview,
) -> Result<(), AuthorityMaintenanceError> {
    let policy_path = PathBuf::from(&preview.layout.runner_policy_state);
    let generation_state_root = PathBuf::from(&preview.layout.generation_state_root);
    if policy_path.parent() != Some(generation_state_root.as_path())
        || policy_path.file_name().and_then(|value| value.to_str())
            != Some(AUTHORITY_RUNNER_POLICY_STATE_FILE_NAME)
    {
        return Err(exact_target_service_plan_invalid());
    }
    Ok(())
}

fn validate_protected_blob_namespace_path_contract(
    preview: &AuthorityMaintenancePreview,
) -> Result<(), AuthorityMaintenanceError> {
    let namespace = PathBuf::from(&preview.layout.protected_blob_namespace);
    let generation_state_root = PathBuf::from(&preview.layout.generation_state_root);
    if namespace.parent() != Some(generation_state_root.as_path())
        || namespace.file_name().and_then(|value| value.to_str())
            != Some(AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME)
    {
        return Err(AuthorityMaintenanceError(
            "authority_protected_blob_namespace_layout_invalid",
        ));
    }
    Ok(())
}

fn validate_protected_blob_namespace_action_contract(
    preview: &AuthorityMaintenancePreview,
) -> Result<(), AuthorityMaintenanceError> {
    let step = exact_step_by_id(&preview.steps, "createProtectedBlobNamespace")?;
    let namespace = preview
        .steps
        .iter()
        .position(|candidate| std::ptr::eq(candidate, step))
        .ok_or_else(exact_target_service_plan_invalid)?;
    if preview
        .steps
        .iter()
        .filter(|candidate| {
            matches!(
                &candidate.action,
                AuthorityMaintenanceAction::CreateDirectory { path, .. }
                    if path == &preview.layout.protected_blob_namespace
            )
        })
        .count()
        != 1
    {
        return Err(exact_target_service_plan_invalid());
    }
    match &preview.steps[namespace].action {
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
        } if path == &preview.layout.protected_blob_namespace
            && parent_path == &preview.layout.generation_state_root
            && *security_sddl == RUNTIME_BLOB_DIRECTORY_STAGING_SDDL
            && *owner_sid == LOCAL_SYSTEM_SID
            && *exact_security_required
            && *reject_reparse_points
            && *stable_object_identity_required
            && *open_parent_by_handle
            && *create_relative_to_parent_handle
            && *retain_verified_handle
            && *create_new
            && *never_reuse => {}
        _ => return Err(exact_target_service_plan_invalid()),
    }
    if preview.fixed_policy.protected_blob_directory_name
        != AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME
        || preview.fixed_policy.protected_blob_directory_staging_sddl
            != RUNTIME_BLOB_DIRECTORY_STAGING_SDDL
        || preview.fixed_policy.protected_blob_directory_final_sddl
            != RUNTIME_BLOB_DIRECTORY_FINAL_SDDL
        || preview.fixed_policy.protected_blob_file_sddl != RUNTIME_BLOB_FILE_SDDL
        || preview
            .fixed_policy
            .protected_blob_directory_authority_access
            != security_policy::RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS
        || preview.fixed_policy.protected_blob_file_authority_access
            != security_policy::RUNTIME_BLOB_FILE_AUTHORITY_ACCESS
        || preview.fixed_policy.protected_blob_file_read_access
            != security_policy::RUNTIME_BLOB_FILE_READ_ACCESS
        || preview.fixed_policy.protected_blob_file_cleanup_access
            != security_policy::RUNTIME_BLOB_FILE_CLEANUP_ACCESS
        || !preview.fixed_policy.protected_blob_create_new
        || !preview.fixed_policy.protected_blob_bootstrap_open_only
        || preview.fixed_policy.protected_blob_share_access != 0
    {
        return Err(exact_target_service_plan_invalid());
    }
    let state_generation_step = exact_step_by_id(&preview.steps, "createStateGenerationDirectory")?;
    let state_generation = preview
        .steps
        .iter()
        .position(|candidate| std::ptr::eq(candidate, state_generation_step))
        .ok_or_else(exact_target_service_plan_invalid)?;
    let seal = exact_action_index(
        &preview.steps,
        "sealCandidateGenerationForFinalCommit",
        |action| {
            matches!(
                action,
                AuthorityMaintenanceAction::SealCandidateGenerationForFinalCommit { .. }
            )
        },
    )?;
    if !(state_generation < namespace && namespace < seal) {
        return Err(exact_target_service_plan_invalid());
    }
    Ok(())
}

fn validate_start_committed_runtime(
    preview: &AuthorityMaintenancePreview,
    expected_service_image_sha256: &[u8; 32],
) -> Result<(), AuthorityMaintenanceError> {
    let step = exact_action_step(&preview.steps, "startCommittedRuntime", |action| {
        matches!(
            action,
            AuthorityMaintenanceAction::StartCommittedRuntime { .. }
        )
    })?;
    match &step.action {
        AuthorityMaintenanceAction::StartCommittedRuntime {
            generation,
            service_name,
            expected_image_sha256,
            active_head_path,
            final_commit_store_root,
            final_commit_receipt_leaf,
            final_commit_gate_derivation,
            require_seal_complete_receipt,
            require_active_head_compare_exchange_readback,
            require_candidate_and_runtime_service_identity_match,
            require_distinct_process_identity_from_candidate,
            require_new_pipe_instance_identity,
            require_committed_runtime_generation_handshake,
            require_precommit_dormant_runtime_readback,
            require_controller_pipe_absent_before_final_commit,
            require_generation_writer_roster_empty_before_final_commit,
            runtime_self_activates_only_after_durable_final_commit_readback,
            hold_runtime_process_and_image_handles_through_final_commit,
            elevated_finalizer_only,
        } if generation == &preview.generation
            && *service_name == AUTHORITY_SERVICE_NAME
            && expected_image_sha256 == &hex_lower(expected_service_image_sha256)
            && active_head_path == &preview.layout.active_head
            && final_commit_store_root == &preview.layout.finalizer_commit_store_root
            && *final_commit_receipt_leaf == FINAL_COMMIT_RECEIPT_LEAF
            && *final_commit_gate_derivation == FINAL_COMMIT_GATE_DERIVATION
            && *require_seal_complete_receipt
            && *require_active_head_compare_exchange_readback
            && *require_candidate_and_runtime_service_identity_match
            && *require_distinct_process_identity_from_candidate
            && *require_new_pipe_instance_identity
            && *require_committed_runtime_generation_handshake
            && *require_precommit_dormant_runtime_readback
            && *require_controller_pipe_absent_before_final_commit
            && *require_generation_writer_roster_empty_before_final_commit
            && *runtime_self_activates_only_after_durable_final_commit_readback
            && *hold_runtime_process_and_image_handles_through_final_commit
            && *elevated_finalizer_only =>
        {
            Ok(())
        }
        _ => Err(exact_target_service_plan_invalid()),
    }
}

fn validate_persist_final_commit(
    preview: &AuthorityMaintenancePreview,
    expected_service_image_sha256: &[u8; 32],
) -> Result<(), AuthorityMaintenanceError> {
    let step = exact_action_step(&preview.steps, "persistFinalCommit", |action| {
        matches!(
            action,
            AuthorityMaintenanceAction::PersistFinalCommit { .. }
        )
    })?;
    match &step.action {
        AuthorityMaintenanceAction::PersistFinalCommit {
            operation,
            generation,
            service_name,
            expected_image_sha256,
            active_head_path,
            final_commit_store_root,
            final_commit_receipt_leaf,
            final_commit_gate_derivation,
            retirement_manifest_path,
            require_seal_complete_receipt,
            require_active_head_compare_exchange_readback,
            require_runtime_identity_and_handshake_readback,
            require_precommit_dormant_runtime_readback,
            require_controller_pipe_absence_readback,
            require_generation_writer_roster_empty_readback,
            bind_runtime_self_activation_gate,
            require_operation_zero_residue_readback,
            require_update_retirement_readback,
            atomic_create_new,
            flush_file_before_publish,
            no_replace,
            flush_parent,
            require_no_publishing_artifact_readback,
            hold_runtime_process_and_image_handles_through_completion,
            elevated_finalizer_only,
        } if *operation == preview.operation
            && generation == &preview.generation
            && *service_name == AUTHORITY_SERVICE_NAME
            && expected_image_sha256 == &hex_lower(expected_service_image_sha256)
            && active_head_path == &preview.layout.active_head
            && final_commit_store_root == &preview.layout.finalizer_commit_store_root
            && *final_commit_receipt_leaf == FINAL_COMMIT_RECEIPT_LEAF
            && *final_commit_gate_derivation == FINAL_COMMIT_GATE_DERIVATION
            && retirement_manifest_path == &preview.layout.retirement_manifest
            && *require_seal_complete_receipt
            && *require_active_head_compare_exchange_readback
            && *require_runtime_identity_and_handshake_readback
            && *require_precommit_dormant_runtime_readback
            && *require_controller_pipe_absence_readback
            && *require_generation_writer_roster_empty_readback
            && *bind_runtime_self_activation_gate
            && *require_operation_zero_residue_readback
            && *require_update_retirement_readback
                == (preview.operation == AuthorityMaintenanceOperation::Update)
            && *atomic_create_new
            && *flush_file_before_publish
            && *no_replace
            && *flush_parent
            && *require_no_publishing_artifact_readback
            && *hold_runtime_process_and_image_handles_through_completion
            && *elevated_finalizer_only =>
        {
            Ok(())
        }
        _ => Err(exact_target_service_plan_invalid()),
    }
}

fn validate_postcommit_readback(
    preview: &AuthorityMaintenancePreview,
    expected_service_image_sha256: &[u8; 32],
) -> Result<(), AuthorityMaintenanceError> {
    let step = exact_action_step(&preview.steps, "verifyProtectedReadback", |action| {
        matches!(
            action,
            AuthorityMaintenanceAction::VerifyPostcommitServingRuntimeReadback { .. }
        )
    })?;
    match &step.action {
        AuthorityMaintenanceAction::VerifyPostcommitServingRuntimeReadback {
            generation,
            service_name,
            expected_image_sha256,
            active_head_path,
            final_commit_store_root,
            final_commit_receipt_leaf,
            final_commit_gate_derivation,
            require_seal_complete_receipt,
            require_final_commit_receipt,
            require_exact_service_configuration,
            require_same_precommit_runtime_process_and_image_identity,
            require_runtime_observed_exact_final_commit_receipt,
            require_controller_pipe_present_after_final_commit,
            require_generation_pipe_handshake,
            require_active_head_binding,
            require_serving_state_bound_to_final_commit_gate,
            require_runtime_healthy,
            allow_recovery_runtime_restart_after_authenticated_final_commit,
            require_recovery_final_commit_receipt_immutable,
            require_recovery_active_head_binding,
            require_recovery_exact_service_configuration,
            require_recovery_exact_service_image,
            require_recovery_exact_generation,
            require_recovery_final_commit_gate_binding,
            forbid_final_commit_receipt_rewrite_during_recovery,
            require_recovery_previous_precommit_runtime_absence,
            require_recovery_start_or_adopt_new_runtime_process_identity,
            require_recovery_serving_readback,
        } if generation == &preview.generation
            && *service_name == AUTHORITY_SERVICE_NAME
            && expected_image_sha256 == &hex_lower(expected_service_image_sha256)
            && active_head_path == &preview.layout.active_head
            && final_commit_store_root == &preview.layout.finalizer_commit_store_root
            && *final_commit_receipt_leaf == FINAL_COMMIT_RECEIPT_LEAF
            && *final_commit_gate_derivation == FINAL_COMMIT_GATE_DERIVATION
            && *require_seal_complete_receipt
            && *require_final_commit_receipt
            && *require_exact_service_configuration
            && *require_same_precommit_runtime_process_and_image_identity
            && *require_runtime_observed_exact_final_commit_receipt
            && *require_controller_pipe_present_after_final_commit
            && *require_generation_pipe_handshake
            && *require_active_head_binding
            && *require_serving_state_bound_to_final_commit_gate
            && *require_runtime_healthy
            && *allow_recovery_runtime_restart_after_authenticated_final_commit
            && *require_recovery_final_commit_receipt_immutable
            && *require_recovery_active_head_binding
            && *require_recovery_exact_service_configuration
            && *require_recovery_exact_service_image
            && *require_recovery_exact_generation
            && *require_recovery_final_commit_gate_binding
            && *forbid_final_commit_receipt_rewrite_during_recovery
            && *require_recovery_previous_precommit_runtime_absence
            && *require_recovery_start_or_adopt_new_runtime_process_identity
            && *require_recovery_serving_readback =>
        {
            Ok(())
        }
        _ => Err(exact_target_service_plan_invalid()),
    }
}

fn validate_operation_dormant_readback(
    preview: &AuthorityMaintenancePreview,
    expected_service_image_sha256: &[u8; 32],
) -> Result<(), AuthorityMaintenanceError> {
    if preview.operation == AuthorityMaintenanceOperation::Install {
        return require_action_absent(
            &preview.steps,
            "verifySuccessorBeforeRetirement",
            |action| {
                matches!(
                    action,
                    AuthorityMaintenanceAction::VerifyPrecommitDormantRuntimeReadback { .. }
                )
            },
        );
    }
    let step = exact_action_step(
        &preview.steps,
        "verifySuccessorBeforeRetirement",
        |action| {
            matches!(
                action,
                AuthorityMaintenanceAction::VerifyPrecommitDormantRuntimeReadback { .. }
            )
        },
    )?;
    match &step.action {
        AuthorityMaintenanceAction::VerifyPrecommitDormantRuntimeReadback {
            generation,
            service_name,
            expected_image_sha256,
            active_head_path,
            require_seal_complete_receipt,
            require_exact_service_configuration,
            require_exact_runtime_process_and_image_identity,
            require_precommit_generation_handshake,
            require_active_head_binding,
            require_distinct_runtime_from_candidate,
            require_runtime_dormant,
            require_controller_pipe_absent,
            require_generation_writer_roster_empty,
        } if generation == &preview.generation
            && *service_name == AUTHORITY_SERVICE_NAME
            && expected_image_sha256 == &hex_lower(expected_service_image_sha256)
            && active_head_path == &preview.layout.active_head
            && *require_seal_complete_receipt
            && *require_exact_service_configuration
            && *require_exact_runtime_process_and_image_identity
            && *require_precommit_generation_handshake
            && *require_active_head_binding
            && *require_distinct_runtime_from_candidate
            && *require_runtime_dormant
            && *require_controller_pipe_absent
            && *require_generation_writer_roster_empty =>
        {
            Ok(())
        }
        _ => Err(exact_target_service_plan_invalid()),
    }
}

fn validate_exact_target_service_action_order(
    preview: &AuthorityMaintenancePreview,
) -> Result<(), AuthorityMaintenanceError> {
    let index = |id: &str| {
        preview
            .steps
            .iter()
            .position(|step| step.id == id)
            .ok_or_else(exact_target_service_plan_invalid)
    };
    let store = index("createFinalizerCommitStoreRoot")?;
    let configure = index("configureServiceExact")?;
    let candidate = exact_action_index(
        &preview.steps,
        "validateCandidateServiceGenerationHandshake",
        |action| {
            matches!(
                action,
                AuthorityMaintenanceAction::ValidateCandidateServiceGenerationHandshake { .. }
            )
        },
    )?;
    let seal = exact_action_index(
        &preview.steps,
        "sealCandidateGenerationForFinalCommit",
        |action| {
            matches!(
                action,
                AuthorityMaintenanceAction::SealCandidateGenerationForFinalCommit { .. }
            )
        },
    )?;
    let active_head = exact_action_index(&preview.steps, "advanceActiveHeadAtomic", |action| {
        matches!(
            action,
            AuthorityMaintenanceAction::AdvanceActiveHeadAtomic { .. }
        )
    })?;
    let start = index("startCommittedRuntime")?;
    let zero_residue =
        exact_action_index(&preview.steps, "verifyOperationZeroResidue", |action| {
            matches!(
                action,
                AuthorityMaintenanceAction::VerifyOperationZeroResidue { .. }
            )
        })?;
    let commit = index("persistFinalCommit")?;
    let postcommit = index("verifyProtectedReadback")?;

    if !(store < configure
        && configure < candidate
        && candidate < seal
        && seal < active_head
        && active_head < start
        && start < zero_residue
        && zero_residue < commit
        && commit < postcommit)
    {
        return Err(exact_target_service_plan_invalid());
    }

    match preview.operation {
        AuthorityMaintenanceOperation::Install => {
            require_action_absent(&preview.steps, "stagePriorRetirementTombstone", |action| {
                matches!(
                    action,
                    AuthorityMaintenanceAction::StageRetirementTombstone { .. }
                )
            })?;
            require_action_absent(
                &preview.steps,
                "finalizePriorRetirementTombstone",
                |action| {
                    matches!(
                        action,
                        AuthorityMaintenanceAction::FinalizeRetirementTombstoneAtomic { .. }
                    )
                },
            )?;
        }
        AuthorityMaintenanceOperation::Update => {
            let dormant = index("verifySuccessorBeforeRetirement")?;
            let stage =
                exact_action_index(&preview.steps, "stagePriorRetirementTombstone", |action| {
                    matches!(
                        action,
                        AuthorityMaintenanceAction::StageRetirementTombstone { .. }
                    )
                })?;
            let retire = exact_action_index(
                &preview.steps,
                "finalizePriorRetirementTombstone",
                |action| {
                    matches!(
                        action,
                        AuthorityMaintenanceAction::FinalizeRetirementTombstoneAtomic { .. }
                    )
                },
            )?;
            if !(start < dormant && dormant < stage && stage < retire && retire < zero_residue) {
                return Err(exact_target_service_plan_invalid());
            }
        }
        AuthorityMaintenanceOperation::Retire => return Err(exact_target_service_plan_invalid()),
    }
    Ok(())
}

fn exact_service_configuration_sha256(configuration: &ServiceConfigurationProjection) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(EXACT_TARGET_SERVICE_CONFIGURATION_DOMAIN);
    digest.update(11_u64.to_be_bytes());
    update_named_string(&mut digest, b"name", configuration.name);
    update_named_string(&mut digest, b"displayName", configuration.display_name);
    update_named_string(&mut digest, b"account", configuration.account);
    update_named_string(&mut digest, b"serviceType", configuration.service_type);
    update_named_string(&mut digest, b"start", configuration.start);
    update_named_string(&mut digest, b"errorControl", configuration.error_control);
    update_named_string(&mut digest, b"sidType", configuration.sid_type);
    update_named_string(&mut digest, b"serviceSid", configuration.service_sid);
    digest.update(("requiredPrivileges".len() as u64).to_be_bytes());
    digest.update(b"requiredPrivileges");
    digest.update((configuration.required_privileges.len() as u64).to_be_bytes());
    for privilege in &configuration.required_privileges {
        digest.update((privilege.len() as u64).to_be_bytes());
        digest.update(privilege.as_bytes());
    }
    update_named_string(&mut digest, b"binaryCommand", &configuration.binary_command);
    update_named_string(&mut digest, b"securitySddl", configuration.security_sddl);
    digest.finalize().into()
}

fn update_named_string(digest: &mut Sha256, name: &[u8], value: &str) {
    digest.update((name.len() as u64).to_be_bytes());
    digest.update(name);
    digest.update((value.len() as u64).to_be_bytes());
    digest.update(value.as_bytes());
}

fn exact_target_service_plan_invalid() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_exact_target_service_plan_invalid")
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
            action: AuthorityMaintenanceAction::VerifyRetirementPreconditions {
                generation: prior_generation.clone(),
                service_name: AUTHORITY_SERVICE_NAME,
                require_service_absent: true,
                require_active_head_matches_generation: true,
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
            action: AuthorityMaintenanceAction::VerifyRetiredGenerationReadback {
                generation: prior_generation,
                service_name: AUTHORITY_SERVICE_NAME,
                active_head_path: layout.active_head.clone(),
                retirement_manifest_path: layout.retirement_manifest.clone().unwrap_or_default(),
                require_service_absent: true,
                require_no_active_generation: true,
                require_final_retirement_manifest: true,
            },
            failed_apply_cleanup: recovery.clone(),
            rollback: AuthorityRollbackAction::None,
        });
        return stable_parent_steps;
    }

    let mut steps = stable_parent_steps;
    steps.push(directory_step(
        "createFinalizerCommitStoreRoot",
        &layout.finalizer_commit_store_root,
        &layout.finalizer_commits_root,
        security_policy::FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL,
        recovery.clone(),
    ));
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
            BINARY_GENERATION_DIRECTORY_SDDL,
            recovery.clone(),
        ),
        payload_step(
            "createServiceExecutable",
            "service",
            &layout.service_executable,
            content.service,
            BINARY_FILE_SDDL,
            recovery.clone(),
        ),
        payload_step(
            "createControllerExecutable",
            "controller",
            &layout.controller_executable,
            content.controller,
            BINARY_FILE_SDDL,
            recovery.clone(),
        ),
        payload_step(
            "createInstallHelperExecutable",
            "installHelper",
            &layout.install_helper_executable,
            content.install_helper,
            BINARY_FILE_SDDL,
            recovery.clone(),
        ),
        payload_step(
            "createLifecycleDriverExecutable",
            "lifecycleDriver",
            &layout.lifecycle_driver_executable,
            content.lifecycle_driver,
            BINARY_FILE_SDDL,
            recovery.clone(),
        ),
        payload_step(
            "createBridgeLauncherExecutable",
            "bridgeLauncher",
            &layout.bridge_launcher_executable,
            content.bridge_launcher,
            BINARY_FILE_SDDL,
            recovery.clone(),
        ),
        directory_step(
            "createStateGenerationDirectory",
            &layout.generation_state_root,
            &layout.state_generations_root,
            STATE_GENERATION_DIRECTORY_SDDL,
            recovery.clone(),
        ),
        directory_step(
            "createProtectedBlobNamespace",
            &layout.protected_blob_namespace,
            &layout.generation_state_root,
            RUNTIME_BLOB_DIRECTORY_STAGING_SDDL,
            recovery.clone(),
        ),
        payload_step(
            "createRuntimeSourceManifest",
            "runtimeSourceManifest",
            &layout.runtime_source_manifest,
            content.runtime_source_manifest,
            STATE_FILE_SDDL,
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
            failed_apply_cleanup: AuthorityRollbackAction::None,
            rollback: AuthorityRollbackAction::None,
        },
        AuthorityMaintenanceStep {
            id: "provisionLedger",
            action: AuthorityMaintenanceAction::ProvisionLedger {
                path: layout.ledger_file.clone(),
                anchor_path: layout.ledger_anchor_file.clone(),
                identity_source: policy.ledger_identity_source,
                frame_size: policy.ledger_frame_size,
                max_result_size: policy.ledger_max_result_size,
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
    let activation_rollback = AuthorityRollbackAction::DiscardManifestAndSealGenerationConsumed {
        manifest_path: layout.activation_manifest.clone(),
        recovery_manifest: layout.recovery_manifest.clone(),
    };
    let candidate_cleanup = AuthorityRollbackAction::StopCandidateValidationServiceExact {
        generation: target_generation.clone(),
        expected_image_sha256: hex_lower(&content.service.sha256),
        identity_source: "consumedCandidateCredentialReadback",
        require_exact_process_identity: true,
        require_natural_exit_or_owned_stop: true,
        require_scm_stopped_readback: true,
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
            final_commit_store_root: layout.finalizer_commit_store_root.clone(),
            final_commit_receipt_leaf: FINAL_COMMIT_RECEIPT_LEAF,
            require_authenticated_final_commit_gate_in_launch_configuration: true,
            require_precommit_dormant_mode: true,
            forbid_controller_pipe_before_final_commit: true,
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
        failed_apply_cleanup: activation_rollback.clone(),
        rollback: activation_rollback,
    });
    steps.push(AuthorityMaintenanceStep {
        id: "validateCandidateServiceGenerationHandshake",
        action: AuthorityMaintenanceAction::ValidateCandidateServiceGenerationHandshake {
            generation: target_generation.clone(),
            expected_image_sha256: hex_lower(&content.service.sha256),
            trust_manifest_path: layout.trust_manifest.clone(),
            credential_schema: bootstrap_activation::CANDIDATE_ACTIVATION_CREDENTIAL_SCHEMA,
            maximum_credential_lifetime_millis:
                bootstrap_activation::MAX_CANDIDATE_CREDENTIAL_LIFETIME_MILLIS,
            require_scm_process_identity_before_arm: true,
            require_atomic_prepared_to_armed_transition: true,
            require_one_use_consumption: true,
            keep_service_start_pending_through_candidate_exit: true,
            require_new_process_identity: true,
            require_held_image_identity: true,
            require_candidate_only_pipe_generation_handshake: true,
            forbid_runtime_controller_pipe: true,
            require_candidate_exit_before_completion: true,
        },
        failed_apply_cleanup: candidate_cleanup.clone(),
        rollback: candidate_cleanup,
    });
    steps.push(AuthorityMaintenanceStep {
        id: "sealCandidateGenerationForFinalCommit",
        action: AuthorityMaintenanceAction::SealCandidateGenerationForFinalCommit {
            generation: target_generation.clone(),
            generation_binary_root: layout.generation_binary_root.clone(),
            generation_state_root: layout.generation_state_root.clone(),
            worker_nonce_root: layout.worker_nonce_root.clone(),
            candidate_consumption_root: layout.candidate_consumption_root.clone(),
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
        },
        failed_apply_cleanup: AuthorityRollbackAction::None,
        rollback: AuthorityRollbackAction::None,
    });
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
        failed_apply_cleanup: AuthorityRollbackAction::None,
        rollback: AuthorityRollbackAction::None,
    });
    steps.push(AuthorityMaintenanceStep {
        id: "startCommittedRuntime",
        action: AuthorityMaintenanceAction::StartCommittedRuntime {
            generation: target_generation.clone(),
            service_name: AUTHORITY_SERVICE_NAME,
            expected_image_sha256: hex_lower(&content.service.sha256),
            active_head_path: layout.active_head.clone(),
            final_commit_store_root: layout.finalizer_commit_store_root.clone(),
            final_commit_receipt_leaf: FINAL_COMMIT_RECEIPT_LEAF,
            final_commit_gate_derivation: FINAL_COMMIT_GATE_DERIVATION,
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
        },
        failed_apply_cleanup: AuthorityRollbackAction::None,
        rollback: AuthorityRollbackAction::None,
    });
    if operation == AuthorityMaintenanceOperation::Update {
        let prior = prior.expect("operation validation requires a prior generation");
        steps.push(AuthorityMaintenanceStep {
            id: "verifySuccessorBeforeRetirement",
            action: AuthorityMaintenanceAction::VerifyPrecommitDormantRuntimeReadback {
                generation: target_generation.clone(),
                service_name: AUTHORITY_SERVICE_NAME,
                expected_image_sha256: hex_lower(&content.service.sha256),
                active_head_path: layout.active_head.clone(),
                require_seal_complete_receipt: true,
                require_exact_service_configuration: true,
                require_exact_runtime_process_and_image_identity: true,
                require_precommit_generation_handshake: true,
                require_active_head_binding: true,
                require_distinct_runtime_from_candidate: true,
                require_runtime_dormant: true,
                require_controller_pipe_absent: true,
                require_generation_writer_roster_empty: true,
            },
            failed_apply_cleanup: AuthorityRollbackAction::None,
            rollback: AuthorityRollbackAction::None,
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
            failed_apply_cleanup: AuthorityRollbackAction::None,
            rollback: AuthorityRollbackAction::None,
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
            failed_apply_cleanup: AuthorityRollbackAction::None,
            rollback: AuthorityRollbackAction::None,
        });
    }
    steps.push(AuthorityMaintenanceStep {
        id: "verifyOperationZeroResidue",
        action: AuthorityMaintenanceAction::VerifyOperationZeroResidue {
            operation,
            generation: target_generation.clone(),
            prior_generation: prior.map(|value| hex_lower(&value.generation)),
            state_maintenance_root: layout.state_maintenance_root.clone(),
            finalizer_commit_store_root: layout.finalizer_commit_store_root.clone(),
            candidate_activation_root: layout.candidate_activation_root.clone(),
            worker_nonce_root: layout.worker_nonce_root.clone(),
            candidate_consumption_root: layout.candidate_consumption_root.clone(),
            active_head_path: layout.active_head.clone(),
            retirement_staging_manifest: layout.retirement_staging_manifest.clone(),
            retirement_aborted_marker: layout.retirement_aborted_marker.clone(),
            retirement_manifest: layout.retirement_manifest.clone(),
            require_maintenance_service_absent: true,
            require_no_staging_or_publishing_files: true,
            require_finalizer_commit_store_preserved: true,
            require_worker_process_and_transient_state_absent: true,
            require_candidate_credentials_absent: true,
            require_nonce_and_consumption_artifacts_sealed: true,
            require_exact_active_head: true,
            require_update_retirement_finalized: operation == AuthorityMaintenanceOperation::Update,
            reject_unplanned_residue: true,
        },
        failed_apply_cleanup: AuthorityRollbackAction::None,
        rollback: AuthorityRollbackAction::None,
    });
    steps.push(AuthorityMaintenanceStep {
        id: "persistFinalCommit",
        action: AuthorityMaintenanceAction::PersistFinalCommit {
            operation,
            generation: target_generation.clone(),
            service_name: AUTHORITY_SERVICE_NAME,
            expected_image_sha256: hex_lower(&content.service.sha256),
            active_head_path: layout.active_head.clone(),
            final_commit_store_root: layout.finalizer_commit_store_root.clone(),
            final_commit_receipt_leaf: FINAL_COMMIT_RECEIPT_LEAF,
            final_commit_gate_derivation: FINAL_COMMIT_GATE_DERIVATION,
            retirement_manifest_path: layout.retirement_manifest.clone(),
            require_seal_complete_receipt: true,
            require_active_head_compare_exchange_readback: true,
            require_runtime_identity_and_handshake_readback: true,
            require_precommit_dormant_runtime_readback: true,
            require_controller_pipe_absence_readback: true,
            require_generation_writer_roster_empty_readback: true,
            bind_runtime_self_activation_gate: true,
            require_operation_zero_residue_readback: true,
            require_update_retirement_readback: operation == AuthorityMaintenanceOperation::Update,
            atomic_create_new: true,
            flush_file_before_publish: true,
            no_replace: true,
            flush_parent: true,
            require_no_publishing_artifact_readback: true,
            hold_runtime_process_and_image_handles_through_completion: true,
            elevated_finalizer_only: true,
        },
        failed_apply_cleanup: AuthorityRollbackAction::None,
        rollback: AuthorityRollbackAction::None,
    });
    steps.push(AuthorityMaintenanceStep {
        id: "verifyProtectedReadback",
        action: AuthorityMaintenanceAction::VerifyPostcommitServingRuntimeReadback {
            generation: target_generation,
            service_name: AUTHORITY_SERVICE_NAME,
            expected_image_sha256: hex_lower(&content.service.sha256),
            active_head_path: layout.active_head.clone(),
            final_commit_store_root: layout.finalizer_commit_store_root.clone(),
            final_commit_receipt_leaf: FINAL_COMMIT_RECEIPT_LEAF,
            final_commit_gate_derivation: FINAL_COMMIT_GATE_DERIVATION,
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
            "ensureBinaryMaintenanceRoot",
            &layout.binary_maintenance_root,
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
            "ensureStateMaintenanceRoot",
            &layout.state_maintenance_root,
            &layout.state_version_root,
            STATE_DIRECTORY_SDDL,
        ),
        protected_directory_step(
            "ensureCandidateActivationRoot",
            &layout.candidate_activation_root,
            &layout.state_version_root,
            CANDIDATE_ACTIVATION_DIRECTORY_SDDL,
        ),
        protected_directory_step(
            "ensureWorkerNonceRoot",
            &layout.worker_nonce_root,
            &layout.state_version_root,
            WORKER_NONCE_DIRECTORY_SDDL,
        ),
        protected_directory_step(
            "ensureCandidateConsumptionRoot",
            &layout.candidate_consumption_root,
            &layout.state_version_root,
            CANDIDATE_CONSUMPTION_DIRECTORY_SDDL,
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
        protected_directory_step(
            "ensureFinalizerCommitsRoot",
            &layout.finalizer_commits_root,
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
    security_sddl: &'static str,
    rollback: AuthorityRollbackAction,
) -> AuthorityMaintenanceStep {
    AuthorityMaintenanceStep {
        id,
        action: AuthorityMaintenanceAction::CreatePayloadFile {
            payload,
            path: path.to_string(),
            sha256: hex_lower(&descriptor.sha256),
            byte_length: descriptor.byte_length,
            security_sddl,
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
    for descriptor in [
        content.service,
        content.controller,
        content.install_helper,
        content.lifecycle_driver,
        content.bridge_launcher,
        content.runtime_source_manifest,
    ] {
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
    digest.update(content.lifecycle_driver.sha256);
    digest.update(content.lifecycle_driver.byte_length.to_be_bytes());
    digest.update(content.bridge_launcher.sha256);
    digest.update(content.bridge_launcher.byte_length.to_be_bytes());
    digest.update(content.runtime_source_manifest.sha256);
    digest.update(content.runtime_source_manifest.byte_length.to_be_bytes());
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

fn fixed_policy_seed(
    security_policy: &SecurityPolicyBundle,
) -> Result<Vec<u8>, AuthorityMaintenanceError> {
    let mut value = Vec::new();
    value.extend_from_slice(POLICY_DOMAIN);
    append_labeled_policy_value(
        &mut value,
        b"protectedBlobLeaf",
        AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME.as_bytes(),
    );
    append_labeled_policy_value(
        &mut value,
        b"protectedBlobDirectoryStagingSddl",
        RUNTIME_BLOB_DIRECTORY_STAGING_SDDL.as_bytes(),
    );
    append_labeled_policy_value(
        &mut value,
        b"protectedBlobDirectoryFinalSddl",
        RUNTIME_BLOB_DIRECTORY_FINAL_SDDL.as_bytes(),
    );
    append_labeled_policy_value(
        &mut value,
        b"protectedBlobFileSddl",
        RUNTIME_BLOB_FILE_SDDL.as_bytes(),
    );
    for (label, number) in [
        (
            b"protectedBlobDirectoryAuthorityAccess".as_slice(),
            security_policy::RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS,
        ),
        (
            b"protectedBlobFileAuthorityAccess".as_slice(),
            security_policy::RUNTIME_BLOB_FILE_AUTHORITY_ACCESS,
        ),
        (
            b"protectedBlobFileReadAccess".as_slice(),
            security_policy::RUNTIME_BLOB_FILE_READ_ACCESS,
        ),
        (
            b"protectedBlobFileCleanupAccess".as_slice(),
            security_policy::RUNTIME_BLOB_FILE_CLEANUP_ACCESS,
        ),
        (b"protectedBlobShareAccess".as_slice(), 0),
    ] {
        append_labeled_policy_value(&mut value, label, &number.to_be_bytes());
    }
    for (label, enabled) in [
        (b"protectedBlobCreateNew".as_slice(), true),
        (b"protectedBlobBootstrapOpenOnly".as_slice(), true),
        (b"protectedBlobHeldParentRelative".as_slice(), true),
    ] {
        append_labeled_policy_value(&mut value, label, &[u8::from(enabled)]);
    }
    for item in [
        AUTHORITY_SERVICE_NAME,
        AUTHORITY_SERVICE_DISPLAY_NAME,
        AUTHORITY_SERVICE_ACCOUNT,
        SERVICE_SID,
        MAINTENANCE_SERVICE_SID,
        AUTHORITY_PIPE_NAME,
        AUTHORITY_PIPE_SDDL,
        BINARY_DIRECTORY_SDDL,
        BINARY_GENERATION_DIRECTORY_SDDL,
        BINARY_FILE_SDDL,
        STATE_DIRECTORY_SDDL,
        STATE_GENERATION_DIRECTORY_SDDL,
        STATE_FILE_SDDL,
        AUTHORITY_RUNNER_POLICY_STATE_FILE_NAME,
        RUNNER_POLICY_STATE_SCHEMA,
        RUNNER_ACCOUNT_NAME,
        WORKER_NONCE_DIRECTORY_SDDL,
        CANDIDATE_CONSUMPTION_DIRECTORY_SDDL,
        WORKER_NONCE_FILE_SDDL,
        CANDIDATE_CONSUMPTION_FILE_SDDL,
        SEALED_NONCE_FILE_SDDL,
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
        PROTECTED_DETACHED_MANIFEST_FILE_SCHEMA,
        PROTECTED_ACTIVE_HEAD_SCHEMA,
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
        "runnerInstallCreateNew",
        "runnerUpdateAuthenticatedPrior",
        "runnerExistingAccountExactSidAndRights",
        "runnerProfileExactIdentityAndSecurity",
        "runnerPolicyImmutableStateFile",
    ] {
        value.extend_from_slice(item.as_bytes());
        value.push(0);
    }
    for privilege in AUTHORITY_REQUIRED_PRIVILEGES {
        value.extend_from_slice(privilege.as_bytes());
        value.push(0);
    }
    let security_policy_bytes = security_policy
        .canonical_bytes()
        .map_err(|_| AuthorityMaintenanceError("authority_security_policy_invalid"))?;
    append_labeled_policy_value(
        &mut value,
        b"canonicalSecurityPolicyBundle",
        &security_policy_bytes,
    );
    value.extend_from_slice(&MAINTENANCE_CANDIDATE_SERVICE_ACCESS.to_be_bytes());
    value.extend_from_slice(&(FRAME_SIZE as u64).to_be_bytes());
    value.extend_from_slice(&(MAX_RESULT_SIZE as u64).to_be_bytes());
    Ok(value)
}

fn append_labeled_policy_value(target: &mut Vec<u8>, label: &[u8], value: &[u8]) {
    target.extend_from_slice(&(label.len() as u64).to_be_bytes());
    target.extend_from_slice(label);
    target.extend_from_slice(&(value.len() as u64).to_be_bytes());
    target.extend_from_slice(value);
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

#[cfg(test)]
mod policy_seed_tests {
    use super::*;

    fn encoded_labeled_value(label: &[u8], value: &[u8]) -> Vec<u8> {
        let mut encoded = Vec::new();
        append_labeled_policy_value(&mut encoded, label, value);
        encoded
    }

    #[test]
    fn protected_blob_contract_is_labeled_once_and_each_field_changes_the_policy_digest() {
        let security_policy = SecurityPolicyBundle::exact();
        let seed = fixed_policy_seed(&security_policy).unwrap();
        assert!(seed.starts_with(POLICY_DOMAIN));
        assert!(!seed.starts_with(GENERATION_DOMAIN));
        let security_policy_bytes = security_policy.canonical_bytes().unwrap();
        let fields = [
            encoded_labeled_value(
                b"protectedBlobLeaf",
                AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME.as_bytes(),
            ),
            encoded_labeled_value(
                b"protectedBlobDirectoryStagingSddl",
                RUNTIME_BLOB_DIRECTORY_STAGING_SDDL.as_bytes(),
            ),
            encoded_labeled_value(
                b"protectedBlobDirectoryFinalSddl",
                RUNTIME_BLOB_DIRECTORY_FINAL_SDDL.as_bytes(),
            ),
            encoded_labeled_value(b"protectedBlobFileSddl", RUNTIME_BLOB_FILE_SDDL.as_bytes()),
            encoded_labeled_value(
                b"protectedBlobDirectoryAuthorityAccess",
                &security_policy::RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS.to_be_bytes(),
            ),
            encoded_labeled_value(
                b"protectedBlobFileAuthorityAccess",
                &security_policy::RUNTIME_BLOB_FILE_AUTHORITY_ACCESS.to_be_bytes(),
            ),
            encoded_labeled_value(
                b"protectedBlobFileReadAccess",
                &security_policy::RUNTIME_BLOB_FILE_READ_ACCESS.to_be_bytes(),
            ),
            encoded_labeled_value(
                b"protectedBlobFileCleanupAccess",
                &security_policy::RUNTIME_BLOB_FILE_CLEANUP_ACCESS.to_be_bytes(),
            ),
            encoded_labeled_value(b"protectedBlobShareAccess", &0u32.to_be_bytes()),
            encoded_labeled_value(b"protectedBlobCreateNew", &[1]),
            encoded_labeled_value(b"protectedBlobBootstrapOpenOnly", &[1]),
            encoded_labeled_value(b"protectedBlobHeldParentRelative", &[1]),
            encoded_labeled_value(b"canonicalSecurityPolicyBundle", &security_policy_bytes),
        ];
        let baseline: [u8; 32] = Sha256::digest(&seed).into();
        for field in fields {
            let offsets = seed
                .windows(field.len())
                .enumerate()
                .filter_map(|(offset, candidate)| (candidate == field).then_some(offset))
                .collect::<Vec<_>>();
            assert_eq!(
                offsets.len(),
                1,
                "policy field must be encoded exactly once"
            );
            let mut drift = seed.clone();
            let value_offset = offsets[0] + field.len() - 1;
            drift[value_offset] ^= 1;
            let changed: [u8; 32] = Sha256::digest(&drift).into();
            assert_ne!(changed, baseline);
        }
    }
}
