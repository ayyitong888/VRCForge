use crate::primitive_evidence_authority_key::AUTHORITY_KEY_NAME_PREFIX;
use crate::primitive_evidence_authority_ledger::{FRAME_SIZE, MAX_RESULT_SIZE};
use crate::primitive_evidence_authority_windows::{
    AuthorityLayout, AUTHORITY_PIPE_NAME, AUTHORITY_PIPE_SDDL, AUTHORITY_REQUIRED_PRIVILEGES,
    AUTHORITY_SERVICE_ACCOUNT, AUTHORITY_SERVICE_DISPLAY_NAME, AUTHORITY_SERVICE_NAME,
};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{fmt, path::PathBuf};

#[path = "primitive_evidence_authority_install/manifest.rs"]
mod manifest;
use manifest::*;
#[path = "primitive_evidence_authority_install/receipt.rs"]
mod receipt;
use receipt::*;
#[path = "primitive_evidence_authority_install/preview.rs"]
mod preview;
use preview::build_preview;
#[cfg(test)]
use preview::derive_full_plan_digest;
#[path = "primitive_evidence_authority_install/transaction.rs"]
mod transaction;
use transaction::*;

pub const MAINTENANCE_PREVIEW_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_maintenance_preview.v1";
pub const TRUST_MANIFEST_SCHEMA: &str = "vrcforge.primitive_evidence_authority_trust_manifest.v1";
pub const ACTIVE_GENERATION_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_active_generation.v1";
pub const RETIREMENT_MANIFEST_SCHEMA: &str = "vrcforge.primitive_evidence_authority_retirement.v1";
pub const RECOVERY_MANIFEST_SCHEMA: &str = "vrcforge.primitive_evidence_authority_recovery.v1";
pub const MAINTENANCE_JOURNAL_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_maintenance_journal.v1";
pub const DETACHED_MANIFEST_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_detached_manifest.v1";

const GENERATION_DOMAIN: &[u8] = b"vrcforge-authority-generation-v1\0";
const TRANSACTION_DOMAIN: &[u8] = b"vrcforge-authority-maintenance-transaction-v1\0";
const PLAN_DOMAIN: &[u8] = b"vrcforge-authority-maintenance-full-plan-v1\0";
const PAYLOAD_SET_DOMAIN: &[u8] = b"vrcforge-authority-held-payload-set-v1\0";
const BOOTSTRAP_HELPER_DOMAIN: &[u8] = b"vrcforge-authority-bootstrap-helper-v1\0";
const UNSIGNED_MANIFEST_DOMAIN: &[u8] = b"vrcforge-authority-unsigned-manifest-v1\0";
const RECOVERY_SEAL_DOMAIN: &[u8] = b"vrcforge-authority-recovery-seal-v1\0";
const LEDGER_DOMAIN: &[u8] = b"vrcforge-authority-ledger-identity-v1\0";
const MAX_AUTHORITY_BINARY_BYTES: u64 = 512 * 1024 * 1024;
const LOCAL_SYSTEM_SID: &str = "S-1-5-18";
const SERVICE_SID: &str = "S-1-5-80-627086344-872206109-3199044541-2745001037-75066892";
const BINARY_DIRECTORY_SDDL: &str =
    "O:SYG:SYD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)S:(ML;OICI;NW;;;HI)";
const BINARY_FILE_SDDL: &str =
    "O:SYG:SYD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1200a9;;;BU)S:(ML;;NW;;;HI)";
const STATE_DIRECTORY_SDDL: &str = "O:SYG:SYD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)S:(ML;OICI;NW;;;HI)";
const STATE_FILE_SDDL: &str = "O:SYG:SYD:P(A;;FA;;;SY)(A;;FA;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)S:(ML;;NW;;;HI)";
const SERVICE_SECURITY_SDDL: &str = "O:SYG:SYD:P(A;;FA;;;SY)(A;;0x000f01ff;;;BA)S:(ML;;NW;;;HI)";
const KEY_SECURITY_SDDL: &str =
    "O:SYG:SYD:P(A;;GA;;;SY)(A;;GA;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityMaintenanceError(&'static str);

impl AuthorityMaintenanceError {
    pub fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for AuthorityMaintenanceError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for AuthorityMaintenanceError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AuthorityPayloadDigest {
    sha256: [u8; 32],
    byte_length: u64,
}

impl AuthorityPayloadDigest {
    pub fn new(sha256: [u8; 32], byte_length: u64) -> Result<Self, AuthorityMaintenanceError> {
        if sha256.iter().all(|value| *value == 0) {
            return Err(AuthorityMaintenanceError("authority_payload_digest_zero"));
        }
        if byte_length == 0 || byte_length > MAX_AUTHORITY_BINARY_BYTES {
            return Err(AuthorityMaintenanceError(
                "authority_payload_length_invalid",
            ));
        }
        Ok(Self {
            sha256,
            byte_length,
        })
    }

    pub fn sha256(&self) -> &[u8; 32] {
        &self.sha256
    }

    pub fn byte_length(&self) -> u64 {
        self.byte_length
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityInstallContent {
    service: AuthorityPayloadDigest,
    controller: AuthorityPayloadDigest,
    install_helper: AuthorityPayloadDigest,
}

impl AuthorityInstallContent {
    pub fn new(
        service: AuthorityPayloadDigest,
        controller: AuthorityPayloadDigest,
        install_helper: AuthorityPayloadDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if service.sha256 == controller.sha256
            || service.sha256 == install_helper.sha256
            || controller.sha256 == install_helper.sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_payload_digest_collision",
            ));
        }
        Ok(Self {
            service,
            controller,
            install_helper,
        })
    }
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum AuthorityMaintenanceOperation {
    Install,
    Update,
    Retire,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct PayloadProjection {
    sha256: String,
    byte_length: u64,
}

impl From<AuthorityPayloadDigest> for PayloadProjection {
    fn from(value: AuthorityPayloadDigest) -> Self {
        Self {
            sha256: hex_lower(&value.sha256),
            byte_length: value.byte_length,
        }
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct ContentProjection {
    service: PayloadProjection,
    controller: PayloadProjection,
    install_helper: PayloadProjection,
}

impl From<&AuthorityInstallContent> for ContentProjection {
    fn from(value: &AuthorityInstallContent) -> Self {
        Self {
            service: value.service.into(),
            controller: value.controller.into(),
            install_helper: value.install_helper.into(),
        }
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct AuthorityGenerationLayout {
    binary_anchor: String,
    state_anchor: String,
    binary_base: String,
    state_base: String,
    binary_version_root: String,
    state_version_root: String,
    binary_generations_root: String,
    state_generations_root: String,
    activations_root: String,
    retirements_root: String,
    recoveries_root: String,
    active_head: String,
    maintenance_journal: String,
    generation_binary_root: String,
    generation_state_root: String,
    service_executable: String,
    controller_executable: String,
    install_helper_executable: String,
    ledger_file: String,
    trust_manifest: String,
    activation_manifest: String,
    retirement_manifest: Option<String>,
    retirement_staging_manifest: Option<String>,
    retirement_aborted_marker: Option<String>,
    recovery_manifest: String,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ServiceConfigurationProjection {
    name: &'static str,
    display_name: &'static str,
    account: &'static str,
    service_type: &'static str,
    start: &'static str,
    error_control: &'static str,
    sid_type: &'static str,
    service_sid: &'static str,
    required_privileges: Vec<&'static str>,
    binary_command: String,
    security_sddl: &'static str,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct FixedPolicyProjection {
    service: ServiceConfigurationProjection,
    pipe_name: &'static str,
    pipe_security_sddl: &'static str,
    binary_directory_sddl: &'static str,
    binary_file_sddl: &'static str,
    state_directory_sddl: &'static str,
    state_file_sddl: &'static str,
    key_name: String,
    key_algorithm: &'static str,
    key_length_bits: u32,
    key_usage: &'static str,
    key_export_policy: &'static str,
    key_security_sddl: &'static str,
    ledger_frame_size: usize,
    ledger_max_result_size: usize,
    ledger_identity_source: &'static str,
    protected_directory_owner_sid: &'static str,
    protected_directory_exact_security_required: bool,
    protected_directory_reparse_points_rejected: bool,
    protected_directory_stable_object_identity_required: bool,
    protected_directory_parent_opened_by_handle: bool,
    protected_directory_child_created_relative_to_handle: bool,
    protected_directory_handle_retained_through_transaction: bool,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub(crate) enum ProtectedActivationDigestReference {
    VerifiedInstalledGeneration {
        generation: String,
        activation_sha256: String,
        source: &'static str,
    },
    SignedManifestHeldHandleReadback {
        generation: String,
        manifest_path: String,
        source: &'static str,
        require_file_flush_before_readback: bool,
        require_held_handle: bool,
        require_stable_file_identity: bool,
        require_canonical_unsigned_payload_digest: bool,
        require_detached_signature_verification: bool,
        complete_only_after_exact_generation_and_digest_readback: bool,
    },
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub(crate) enum AuthorityMaintenanceAction {
    CreateDurableJournal {
        anchor_path: String,
        anchor_source: &'static str,
        anchor_handle_held: bool,
        anchor_stable_object_identity_required: bool,
        anchor_reparse_points_rejected: bool,
        path: String,
        transaction_sha256: String,
        plan_sha256: String,
        security_sddl: &'static str,
        owner_sid: &'static str,
        exact_security_required: bool,
        create_relative_to_anchor_handle: bool,
        preexisting_path_rejected: bool,
        create_new: bool,
        never_reuse: bool,
        write_through: bool,
        flush_parent: bool,
        flush_file_after_every_transition: bool,
        recover_before_new_transaction: bool,
        terminal_states: [&'static str; 3],
        identical_terminal_is_idempotent: bool,
        conflicting_terminal_rejected: bool,
        plan_digest_excludes_own_field: bool,
    },
    EnsureProtectedDirectory {
        path: String,
        parent_path: String,
        security_sddl: &'static str,
        owner_sid: &'static str,
        create_if_missing: bool,
        accept_existing: bool,
        exact_security_required: bool,
        reject_reparse_points: bool,
        stable_object_identity_required: bool,
        open_parent_by_handle: bool,
        create_relative_to_parent_handle: bool,
        retain_verified_handle: bool,
    },
    CreateDirectory {
        path: String,
        parent_path: String,
        security_sddl: &'static str,
        owner_sid: &'static str,
        exact_security_required: bool,
        reject_reparse_points: bool,
        stable_object_identity_required: bool,
        open_parent_by_handle: bool,
        create_relative_to_parent_handle: bool,
        retain_verified_handle: bool,
        create_new: bool,
        never_reuse: bool,
    },
    CreatePayloadFile {
        payload: &'static str,
        path: String,
        sha256: String,
        byte_length: u64,
        security_sddl: &'static str,
        source: &'static str,
        source_handle_lease_required: bool,
        source_write_sharing_denied: bool,
        source_delete_sharing_denied: bool,
        source_full_content_rehash_after_copy: bool,
        destination_create_relative_to_verified_parent_handle: bool,
        destination_handle_retained_through_readback: bool,
        destination_write_delete_sharing_denied: bool,
        write_through: bool,
        flush_file_before_readback: bool,
        flush_parent_after_create: bool,
        rehash_destination_from_held_handle: bool,
        verify_destination_stable_identity_and_path: bool,
        complete_only_after_exact_readback: bool,
        create_new: bool,
        never_reuse: bool,
    },
    ProvisionMachineKey {
        key_name: String,
        algorithm: &'static str,
        key_length_bits: u32,
        usage: &'static str,
        export_policy: &'static str,
        security_sddl: &'static str,
        flush_provider_state_before_completion: bool,
        complete_only_after_protected_readback: bool,
        create_new: bool,
        never_reuse: bool,
    },
    ProvisionLedger {
        path: String,
        identity_source: &'static str,
        frame_size: usize,
        max_result_size: usize,
        security_sddl: &'static str,
        write_through: bool,
        flush_file_before_completion: bool,
        flush_parent_after_create: bool,
        rehash_identity_from_held_handle: bool,
        complete_only_after_exact_readback: bool,
        create_new: bool,
        never_reuse: bool,
    },
    WriteSignedManifest {
        path: String,
        contract: DetachedManifestContractProjection,
        security_sddl: &'static str,
        write_through: bool,
        flush_file_before_completion: bool,
        flush_parent_after_create: bool,
        rehash_from_held_handle: bool,
        complete_only_after_signature_and_exact_readback: bool,
        create_new: bool,
        never_reuse: bool,
    },
    AdvanceActiveHeadAtomic {
        path: String,
        generation: String,
        activation: ProtectedActivationDigestReference,
        expected_previous_generation: Option<String>,
        expected_previous_activation_sha256: Option<String>,
        expected_epoch: u64,
        compare_exchange_single_head: bool,
        reject_fork: bool,
        write_through: bool,
        flush_parent: bool,
    },
    StopDrainServiceExact {
        generation: String,
        expected_process_id: u32,
        expected_process_creation_time: u64,
        expected_image_sha256: String,
        expected_pipe_instance_id: String,
        require_exact_process_identity: bool,
        require_held_image_identity: bool,
        require_pipe_close_proof: bool,
        require_scm_stopped_readback: bool,
    },
    ConfigureServiceExact {
        operation: &'static str,
        configuration: ServiceConfigurationProjection,
        requires_prior_stop_drain_proof: bool,
    },
    StartServiceWithGenerationHandshake {
        generation: String,
        expected_image_sha256: String,
        trust_manifest_path: String,
        require_new_process_identity: bool,
        require_held_image_identity: bool,
        require_pipe_generation_handshake: bool,
    },
    RemoveServiceRegistration {
        service_name: &'static str,
        requires_prior_stop_drain_proof: bool,
    },
    StageRetirementTombstone {
        staging_path: String,
        final_path: String,
        aborted_marker_path: String,
        contract: DetachedManifestContractProjection,
        create_new: bool,
        never_reuse: bool,
        write_through: bool,
        flush_file_before_completion: bool,
        flush_parent_after_create: bool,
        rehash_from_held_handle: bool,
        complete_only_after_signature_and_exact_readback: bool,
    },
    FinalizeRetirementTombstoneAtomic {
        staging_path: String,
        final_path: String,
        aborted_marker_path: String,
        no_replace: bool,
        flush_parent: bool,
        aborted_marker_forbids_reuse: bool,
        active_head_path: String,
        expected_active_generation: String,
        expected_active_activation: ProtectedActivationDigestReference,
        expected_active_epoch: u64,
        compare_exchange_single_head: bool,
        active_head_result: &'static str,
        irreversible_commit: bool,
        post_commit_failure_policy: &'static str,
    },
    VerifyProtectedReadback {
        generation: String,
        require_service_absent: bool,
    },
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub(crate) enum AuthorityRollbackAction {
    None,
    RestoreProtectedDirectoryState {
        path: String,
    },
    RemoveNewServiceRegistration {
        generation: String,
        require_stop_drain_proof: bool,
    },
    RestorePriorServiceConfiguration {
        generation: String,
        require_generation_handshake: bool,
    },
    RestoreRetiredServiceConfiguration {
        generation: String,
        require_generation_handshake: bool,
    },
    DiscardCreatedManifest {
        manifest_path: String,
    },
    SealGenerationConsumed {
        recovery_manifest: String,
    },
    DiscardManifestAndSealGenerationConsumed {
        manifest_path: String,
        recovery_manifest: String,
    },
    MarkRetirementAbortedNoReuse {
        staging_path: String,
        aborted_marker_path: String,
        write_through: bool,
    },
    RestoreActiveHeadAndSealGenerationConsumed {
        active_head_path: String,
        target_generation: String,
        target_activation: ProtectedActivationDigestReference,
        target_epoch: u64,
        restore_previous_generation: Option<String>,
        restore_previous_activation_sha256: Option<String>,
        restore_previous_epoch: Option<u64>,
        delete_if_initial: bool,
        compare_exchange_target_only: bool,
        write_through: bool,
        flush_parent_before_seal: bool,
        recovery_manifest: String,
    },
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct AuthorityMaintenanceStep {
    id: &'static str,
    action: AuthorityMaintenanceAction,
    failed_apply_cleanup: AuthorityRollbackAction,
    rollback: AuthorityRollbackAction,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct AuthorityMaintenancePreview {
    schema: &'static str,
    operation: AuthorityMaintenanceOperation,
    generation: String,
    prior_generation: Option<String>,
    prior_generation_readback: Option<PriorGenerationProjection>,
    transaction_sha256: String,
    plan_sha256: String,
    policy_sha256: String,
    content: ContentProjection,
    layout: AuthorityGenerationLayout,
    journal: JournalContractProjection,
    fixed_policy: FixedPolicyProjection,
    steps: Vec<AuthorityMaintenanceStep>,
    automatic_execution_allowed: bool,
    native_mutation_backend_available: bool,
    execution_requires_verified_elevated_maintenance_capability: bool,
    trusted_boundary_ready: bool,
    blockers: Vec<&'static str>,
}

impl AuthorityMaintenancePreview {
    pub fn generation_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.generation)
    }

    pub fn plan_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.plan_sha256)
    }

    pub fn steps(&self) -> &[AuthorityMaintenanceStep] {
        &self.steps
    }

    pub fn trusted_boundary_ready(&self) -> bool {
        self.trusted_boundary_ready
    }
}

pub(crate) fn preview_install(
    layout: &AuthorityLayout,
    content: AuthorityInstallContent,
) -> Result<AuthorityMaintenancePreview, AuthorityMaintenanceError> {
    build_preview(
        layout,
        AuthorityMaintenanceOperation::Install,
        content,
        None,
    )
}

pub(crate) fn preview_update(
    layout: &AuthorityLayout,
    content: AuthorityInstallContent,
    prior: VerifiedInstalledGeneration,
) -> Result<AuthorityMaintenancePreview, AuthorityMaintenanceError> {
    build_preview(
        layout,
        AuthorityMaintenanceOperation::Update,
        content,
        Some(prior),
    )
}

pub(crate) fn preview_retire(
    layout: &AuthorityLayout,
    installed: VerifiedInstalledGeneration,
) -> Result<AuthorityMaintenancePreview, AuthorityMaintenanceError> {
    let content = AuthorityInstallContent::new(
        installed.service,
        installed.controller,
        installed.install_helper,
    )?;
    let expected = installed.generation;
    let preview = build_preview(
        layout,
        AuthorityMaintenanceOperation::Retire,
        content,
        Some(installed),
    )?;
    if preview.generation_sha256()? != expected {
        return Err(AuthorityMaintenanceError(
            "authority_retire_generation_mismatch",
        ));
    }
    Ok(preview)
}

#[cfg(windows)]
pub(crate) struct VerifiedElevatedMaintenanceCapability {
    bootstrap_process_id: u32,
    bootstrap_process_creation_time: u64,
    bootstrap_binding_sha256: [u8; 32],
    plan_sha256: [u8; 32],
    generation: [u8; 32],
    payload_set_binding_sha256: [u8; 32],
}

#[cfg(windows)]
impl VerifiedElevatedMaintenanceCapability {
    pub(crate) fn from_sealed_bootstrap(
        preview: &AuthorityMaintenancePreview,
        lease: &VerifiedMaintenanceLease,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let generation = preview.generation_sha256()?;
        let content = content_from_projection(&preview.content)?;
        if !lease.is_live()
            || lease.plan_sha256 != preview.plan_sha256()?
            || lease.generation != generation
            || lease.bootstrap_helper.process_id == 0
            || lease.bootstrap_helper.process_creation_time == 0
            || lease.bootstrap_helper.image != content.install_helper
            || !lease.payloads.content_matches(&content)
        {
            return Err(AuthorityMaintenanceError(
                "authority_maintenance_bootstrap_binding_mismatch",
            ));
        }
        Ok(Self {
            bootstrap_process_id: lease.bootstrap_helper.process_id,
            bootstrap_process_creation_time: lease.bootstrap_helper.process_creation_time,
            bootstrap_binding_sha256: lease.bootstrap_helper.binding_sha256,
            plan_sha256: preview.plan_sha256()?,
            generation,
            payload_set_binding_sha256: lease.payloads.binding_sha256,
        })
    }

    pub(crate) fn process_id(&self) -> u32 {
        self.bootstrap_process_id
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct AuthorityMaintenanceExecutionReport {
    status: &'static str,
    startup_recovery: Option<&'static str>,
    journal_terminal: Option<&'static str>,
    recovery_seal: Option<&'static str>,
    trusted_boundary_ready: bool,
    completed_steps: Vec<&'static str>,
    failed_step: Option<&'static str>,
    failed_step_cleanup: Option<&'static str>,
    failure_cleanup_verified: Option<bool>,
    rollback_failures: Vec<&'static str>,
    blockers: Vec<&'static str>,
}

#[cfg(windows)]
pub(crate) fn execute_maintenance_transaction(
    preview: &AuthorityMaintenancePreview,
    capability: &VerifiedElevatedMaintenanceCapability,
    lease: &mut VerifiedMaintenanceLease,
) -> Result<AuthorityMaintenanceExecutionReport, AuthorityMaintenanceError> {
    if capability.plan_sha256 != preview.plan_sha256()?
        || capability.generation != preview.generation_sha256()?
        || capability.process_id() == 0
        || capability.bootstrap_process_creation_time
            != lease.bootstrap_helper.process_creation_time
        || capability.bootstrap_binding_sha256 != lease.bootstrap_helper.binding_sha256
        || !lease.is_live()
        || lease.plan_sha256 != preview.plan_sha256()?
        || lease.generation != preview.generation_sha256()?
        || capability.payload_set_binding_sha256 != lease.payloads.binding_sha256
        || !lease
            .payloads
            .content_matches(&content_from_projection(&preview.content)?)
    {
        return Err(AuthorityMaintenanceError(
            "authority_maintenance_capability_mismatch",
        ));
    }
    Err(AuthorityMaintenanceError(
        "authority_native_mutation_backend_disabled",
    ))
}

fn content_from_projection(
    projection: &ContentProjection,
) -> Result<AuthorityInstallContent, AuthorityMaintenanceError> {
    AuthorityInstallContent::new(
        AuthorityPayloadDigest::new(
            decode_hex_32(&projection.service.sha256)?,
            projection.service.byte_length,
        )?,
        AuthorityPayloadDigest::new(
            decode_hex_32(&projection.controller.sha256)?,
            projection.controller.byte_length,
        )?,
        AuthorityPayloadDigest::new(
            decode_hex_32(&projection.install_helper.sha256)?,
            projection.install_helper.byte_length,
        )?,
    )
}

fn hex_lower(value: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        output.push(DIGITS[(byte >> 4) as usize] as char);
        output.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    output
}

fn decode_hex_32(value: &str) -> Result<[u8; 32], AuthorityMaintenanceError> {
    if value.len() != 64 {
        return Err(AuthorityMaintenanceError("authority_digest_invalid"));
    }
    let mut output = [0u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?;
    }
    Ok(output)
}

fn hex_nibble(value: u8) -> Result<u8, AuthorityMaintenanceError> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err(AuthorityMaintenanceError("authority_digest_invalid")),
    }
}

#[cfg(test)]
#[path = "primitive_evidence_authority_install/tests.rs"]
mod tests;
