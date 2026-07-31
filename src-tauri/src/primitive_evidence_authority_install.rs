use crate::primitive_evidence_authority_key::AUTHORITY_KEY_NAME_PREFIX;
use crate::primitive_evidence_authority_ledger::{ANCHOR_RECORD_SIZE, FRAME_SIZE, MAX_RESULT_SIZE};
use crate::primitive_evidence_authority_windows::{
    AuthorityLayout, AUTHORITY_PIPE_NAME, AUTHORITY_PIPE_SDDL,
    AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME, AUTHORITY_REQUIRED_PRIVILEGES,
    AUTHORITY_RUNNER_POLICY_STATE_FILE_NAME, AUTHORITY_SERVICE_ACCOUNT,
    AUTHORITY_SERVICE_DISPLAY_NAME, AUTHORITY_SERVICE_NAME,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{fmt, path::PathBuf};

#[path = "primitive_evidence_authority_install/manifest.rs"]
mod manifest;
use manifest::*;
#[path = "primitive_evidence_authority_install/receipt.rs"]
mod receipt;
use receipt::*;
#[path = "primitive_evidence_authority_install/security_policy.rs"]
pub(crate) mod security_policy;
use security_policy::SecurityPolicyBundle;
#[path = "primitive_evidence_authority_install/finalizer_commit_protocol.rs"]
pub(crate) mod finalizer_commit_protocol;
#[cfg(windows)]
#[path = "primitive_evidence_authority_install/finalizer_commit_store_windows.rs"]
pub(crate) mod finalizer_commit_store_windows;
#[cfg(windows)]
#[path = "primitive_evidence_authority_install/finalizer_generation_seal.rs"]
mod finalizer_generation_seal;
#[cfg(windows)]
#[path = "primitive_evidence_authority_install/finalizer_security_windows.rs"]
mod finalizer_security_windows;
#[path = "primitive_evidence_authority_install/preview.rs"]
mod preview;
#[cfg(windows)]
#[path = "primitive_evidence_authority_install/receipt_windows.rs"]
mod receipt_windows;
#[cfg(windows)]
#[path = "primitive_evidence_authority_install/runner_policy.rs"]
mod runner_policy;
use preview::build_preview;
#[cfg(test)]
use preview::derive_full_plan_digest;
#[path = "primitive_evidence_authority_install/transaction.rs"]
mod transaction;
use transaction::*;
#[path = "primitive_evidence_authority_install/bootstrap.rs"]
pub(crate) mod bootstrap;
#[path = "primitive_evidence_authority_install/bootstrap_activation.rs"]
mod bootstrap_activation;
#[cfg(windows)]
#[path = "primitive_evidence_authority_install/candidate_activation_orchestrator.rs"]
mod candidate_activation_orchestrator;
#[path = "primitive_evidence_authority_install/maintenance_journal.rs"]
mod maintenance_journal;
#[path = "primitive_evidence_authority_install/worker.rs"]
mod worker;
#[cfg(test)]
use worker::*;
#[cfg(windows)]
#[path = "primitive_evidence_authority_install/candidate_client_windows.rs"]
mod candidate_client_windows;
#[cfg(windows)]
#[path = "primitive_evidence_authority_candidate_pipe.rs"]
pub(crate) mod candidate_pipe;
#[cfg(windows)]
#[path = "primitive_evidence_authority_install/candidate_service_start_windows.rs"]
mod candidate_service_start_windows;
#[cfg(windows)]
#[path = "primitive_evidence_authority_install/native_runtime_windows.rs"]
mod native_runtime_windows;
#[cfg(windows)]
#[path = "primitive_evidence_authority_install/worker_store_windows.rs"]
mod worker_store_windows;
#[cfg(windows)]
#[path = "primitive_evidence_authority_install/worker_windows.rs"]
mod worker_windows;

pub const MAINTENANCE_PREVIEW_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_maintenance_preview.v4";
pub(crate) const RUNNER_POLICY_STATE_SCHEMA: &str =
    "vrcforge.primitive_evidence_runner_policy_state.v2";
pub(crate) const RUNNER_ACCOUNT_NAME: &str = "VRCForgeRunner";
pub const TRUST_MANIFEST_SCHEMA: &str = "vrcforge.primitive_evidence_authority_trust_manifest.v1";
pub const ACTIVE_GENERATION_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_active_generation.v1";
pub const RETIREMENT_MANIFEST_SCHEMA: &str = "vrcforge.primitive_evidence_authority_retirement.v1";
pub const RECOVERY_MANIFEST_SCHEMA: &str = "vrcforge.primitive_evidence_authority_recovery.v1";
pub const MAINTENANCE_JOURNAL_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_maintenance_journal.v1";
pub const DETACHED_MANIFEST_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_detached_manifest.v1";
pub const PROTECTED_DETACHED_MANIFEST_FILE_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_protected_manifest_file.v1";
pub const PROTECTED_ACTIVE_HEAD_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_protected_active_head.v1";
pub use crate::primitive_evidence_authority_windows::AUTHORITY_RUNTIME_SOURCE_MANIFEST_FILE_NAME as RUNTIME_SOURCE_MANIFEST_FILE_NAME;
pub const MAX_RUNTIME_SOURCE_MANIFEST_BYTES: u64 = 2 * 1024 * 1024;
pub(crate) const MAINTENANCE_WORKER_SERVICE_NAME: &str = worker::MAINTENANCE_WORKER_SERVICE_NAME;
const PROTECTED_GENERATION_PAYLOAD_COUNT: usize = 6;
const GENERATION_SEAL_OBJECT_COUNT: usize = 16;
const GENERATION_SEAL_TERMINAL_SEQUENCE: u32 = (GENERATION_SEAL_OBJECT_COUNT as u32) * 2 + 1;

const GENERATION_DOMAIN: &[u8] = b"vrcforge-authority-generation-v3\0";
const POLICY_DOMAIN: &[u8] = b"vrcforge-authority-fixed-policy-v1\0";
const INSTALLED_LAYOUT_DOMAIN: &[u8] = b"vrcforge-authority-installed-layout-v4\0";
const TRANSACTION_DOMAIN: &[u8] = b"vrcforge-authority-maintenance-transaction-v4\0";
const PLAN_DOMAIN: &[u8] = b"vrcforge-authority-maintenance-full-plan-v4\0";
const PAYLOAD_SET_DOMAIN: &[u8] = b"vrcforge-authority-held-payload-set-v2\0";
const BOOTSTRAP_HELPER_DOMAIN: &[u8] = b"vrcforge-authority-bootstrap-helper-v1\0";
const UNSIGNED_MANIFEST_DOMAIN: &[u8] = b"vrcforge-authority-unsigned-manifest-v1\0";
const RECOVERY_SEAL_DOMAIN: &[u8] = b"vrcforge-authority-recovery-seal-v1\0";
const LEDGER_DOMAIN: &[u8] = b"vrcforge-authority-ledger-identity-v1\0";
const MAX_AUTHORITY_BINARY_BYTES: u64 = 512 * 1024 * 1024;
const LOCAL_SYSTEM_SID: &str = security_policy::LOCAL_SYSTEM_SID;
const SERVICE_SID: &str = security_policy::AUTHORITY_SERVICE_SID;
const MAINTENANCE_SERVICE_SID: &str = security_policy::MAINTENANCE_SERVICE_SID;
// These aliases describe objects only while the restricted transaction is
// staging them. The elevated finalizer must replace them with the exact sealed
// descriptors from `SecurityPolicyBundle` before commit.
const BINARY_DIRECTORY_SDDL: &str = security_policy::STABLE_ROOT_SDDL;
const BINARY_GENERATION_DIRECTORY_SDDL: &str = security_policy::GENERATION_STAGING_SDDL;
const BINARY_FILE_SDDL: &str = security_policy::BINARY_STAGING_SDDL;
const SEALED_GENERATION_DIRECTORY_SDDL: &str = security_policy::GENERATION_SEALED_SDDL;
const SEALED_BINARY_FILE_SDDL: &str = security_policy::BINARY_SEALED_SDDL;
const STATE_DIRECTORY_SDDL: &str = security_policy::STABLE_ROOT_SDDL;
const STATE_GENERATION_DIRECTORY_SDDL: &str = security_policy::GENERATION_STAGING_SDDL;
const STATE_FILE_SDDL: &str = security_policy::STATE_STAGING_SDDL;
const IMMUTABLE_STATE_FILE_SDDL: &str = security_policy::STATE_IMMUTABLE_SDDL;
const LEDGER_FILE_SDDL: &str = security_policy::LEDGER_STAGING_SDDL;
const LEDGER_FINAL_FILE_SDDL: &str = security_policy::LEDGER_FINAL_SDDL;
const RUNTIME_BLOB_DIRECTORY_STAGING_SDDL: &str =
    security_policy::RUNTIME_BLOB_DIRECTORY_STAGING_SDDL;
const RUNTIME_BLOB_DIRECTORY_FINAL_SDDL: &str = security_policy::RUNTIME_BLOB_DIRECTORY_FINAL_SDDL;
const RUNTIME_BLOB_FILE_SDDL: &str = security_policy::RUNTIME_BLOB_FILE_SDDL;
const CANDIDATE_ACTIVATION_DIRECTORY_SDDL: &str =
    security_policy::CANDIDATE_ACTIVATION_NAMESPACE_SDDL;
const WORKER_NONCE_DIRECTORY_SDDL: &str = security_policy::WORKER_NONCE_NAMESPACE_SDDL;
const CANDIDATE_CONSUMPTION_DIRECTORY_SDDL: &str =
    security_policy::CANDIDATE_CONSUMPTION_NAMESPACE_SDDL;
const WORKER_NONCE_FILE_SDDL: &str = security_policy::WORKER_NONCE_STAGING_SDDL;
const CANDIDATE_CONSUMPTION_FILE_SDDL: &str = security_policy::CANDIDATE_CONSUMPTION_STAGING_SDDL;
const SEALED_NONCE_FILE_SDDL: &str = security_policy::NONCE_SEALED_SDDL;
const MAINTENANCE_CANDIDATE_SERVICE_ACCESS: u32 =
    security_policy::AUTHORITY_SERVICE_CANDIDATE_START_ACCESS;
const SERVICE_SECURITY_SDDL: &str = security_policy::AUTHORITY_SERVICE_CANDIDATE_START_SDDL;
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
    lifecycle_driver: AuthorityPayloadDigest,
    bridge_launcher: AuthorityPayloadDigest,
    runtime_source_manifest: AuthorityPayloadDigest,
}

impl AuthorityInstallContent {
    pub fn new(
        service: AuthorityPayloadDigest,
        controller: AuthorityPayloadDigest,
        install_helper: AuthorityPayloadDigest,
        lifecycle_driver: AuthorityPayloadDigest,
        bridge_launcher: AuthorityPayloadDigest,
        runtime_source_manifest: AuthorityPayloadDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if runtime_source_manifest.byte_length > MAX_RUNTIME_SOURCE_MANIFEST_BYTES {
            return Err(AuthorityMaintenanceError(
                "authority_runtime_source_manifest_length_invalid",
            ));
        }
        let digests = [
            service.sha256,
            controller.sha256,
            install_helper.sha256,
            lifecycle_driver.sha256,
            bridge_launcher.sha256,
            runtime_source_manifest.sha256,
        ];
        if digests
            .iter()
            .enumerate()
            .any(|(index, digest)| digests[..index].contains(digest))
        {
            return Err(AuthorityMaintenanceError(
                "authority_payload_digest_collision",
            ));
        }
        Ok(Self {
            service,
            controller,
            install_helper,
            lifecycle_driver,
            bridge_launcher,
            runtime_source_manifest,
        })
    }

    pub fn service(&self) -> AuthorityPayloadDigest {
        self.service
    }

    pub fn controller(&self) -> AuthorityPayloadDigest {
        self.controller
    }

    pub fn install_helper(&self) -> AuthorityPayloadDigest {
        self.install_helper
    }

    pub fn lifecycle_driver(&self) -> AuthorityPayloadDigest {
        self.lifecycle_driver
    }

    pub fn bridge_launcher(&self) -> AuthorityPayloadDigest {
        self.bridge_launcher
    }

    pub fn runtime_source_manifest(&self) -> AuthorityPayloadDigest {
        self.runtime_source_manifest
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
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
    lifecycle_driver: PayloadProjection,
    bridge_launcher: PayloadProjection,
    runtime_source_manifest: PayloadProjection,
}

impl From<&AuthorityInstallContent> for ContentProjection {
    fn from(value: &AuthorityInstallContent) -> Self {
        Self {
            service: value.service.into(),
            controller: value.controller.into(),
            install_helper: value.install_helper.into(),
            lifecycle_driver: value.lifecycle_driver.into(),
            bridge_launcher: value.bridge_launcher.into(),
            runtime_source_manifest: value.runtime_source_manifest.into(),
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
    binary_maintenance_root: String,
    state_maintenance_root: String,
    candidate_activation_root: String,
    worker_nonce_root: String,
    candidate_consumption_root: String,
    activations_root: String,
    retirements_root: String,
    recoveries_root: String,
    finalizer_commits_root: String,
    finalizer_commit_store_root: String,
    active_head: String,
    maintenance_journal: String,
    generation_binary_root: String,
    generation_state_root: String,
    service_executable: String,
    controller_executable: String,
    install_helper_executable: String,
    lifecycle_driver_executable: String,
    bridge_launcher_executable: String,
    runtime_source_manifest: String,
    runner_policy_state: String,
    protected_blob_namespace: String,
    ledger_file: String,
    ledger_anchor_file: String,
    trust_manifest: String,
    activation_manifest: String,
    retirement_manifest: Option<String>,
    retirement_staging_manifest: Option<String>,
    retirement_aborted_marker: Option<String>,
    recovery_manifest: String,
}

impl AuthorityGenerationLayout {
    fn installed_layout_sha256(&self) -> [u8; 32] {
        // Only durable installed locations belong in this identity. Maintenance
        // journals, recovery receipts, and retirement staging paths are bound
        // by their own transaction receipts and intentionally excluded here.
        let paths = [
            &self.binary_anchor,
            &self.state_anchor,
            &self.binary_base,
            &self.state_base,
            &self.binary_version_root,
            &self.state_version_root,
            &self.binary_generations_root,
            &self.state_generations_root,
            &self.binary_maintenance_root,
            &self.state_maintenance_root,
            &self.candidate_activation_root,
            &self.worker_nonce_root,
            &self.candidate_consumption_root,
            &self.activations_root,
            &self.retirements_root,
            &self.recoveries_root,
            &self.finalizer_commits_root,
            &self.active_head,
            &self.generation_binary_root,
            &self.generation_state_root,
            &self.service_executable,
            &self.controller_executable,
            &self.install_helper_executable,
            &self.lifecycle_driver_executable,
            &self.bridge_launcher_executable,
            &self.runtime_source_manifest,
            &self.runner_policy_state,
            &self.protected_blob_namespace,
            &self.ledger_file,
            &self.ledger_anchor_file,
            &self.trust_manifest,
            &self.activation_manifest,
        ];
        let mut digest = Sha256::new();
        digest.update(INSTALLED_LAYOUT_DOMAIN);
        digest.update((paths.len() as u64).to_be_bytes());
        for path in paths {
            digest.update((path.len() as u64).to_be_bytes());
            digest.update(path.as_bytes());
        }
        digest.finalize().into()
    }
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
    security_policy: SecurityPolicyBundle,
    maintenance_service_sid: &'static str,
    maintenance_candidate_service_access: u32,
    pipe_name: &'static str,
    pipe_security_sddl: &'static str,
    binary_directory_sddl: &'static str,
    binary_generation_directory_sddl: &'static str,
    binary_file_sddl: &'static str,
    state_directory_sddl: &'static str,
    state_generation_directory_sddl: &'static str,
    state_file_sddl: &'static str,
    runner_policy_file_name: &'static str,
    runner_policy_schema: &'static str,
    runner_account_name: &'static str,
    runner_install_requires_create_new: bool,
    runner_update_requires_authenticated_prior: bool,
    runner_existing_account_requires_exact_sid_and_rights: bool,
    runner_profile_requires_exact_identity_and_security: bool,
    runner_policy_immutable_state_file: bool,
    protected_blob_directory_name: &'static str,
    protected_blob_directory_staging_sddl: &'static str,
    protected_blob_directory_final_sddl: &'static str,
    protected_blob_file_sddl: &'static str,
    protected_blob_directory_authority_access: u32,
    protected_blob_file_authority_access: u32,
    protected_blob_file_read_access: u32,
    protected_blob_file_cleanup_access: u32,
    protected_blob_create_new: bool,
    protected_blob_bootstrap_open_only: bool,
    protected_blob_share_access: u32,
    candidate_activation_directory_sddl: &'static str,
    worker_nonce_directory_sddl: &'static str,
    candidate_consumption_directory_sddl: &'static str,
    worker_nonce_file_sddl: &'static str,
    candidate_consumption_file_sddl: &'static str,
    sealed_nonce_file_sddl: &'static str,
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
        anchor_path: String,
        identity_source: &'static str,
        frame_size: usize,
        max_result_size: usize,
        security_sddl: &'static str,
        write_through: bool,
        flush_file_before_completion: bool,
        flush_anchor_before_completion: bool,
        flush_parent_after_create: bool,
        rehash_identity_from_held_handle: bool,
        rehash_anchor_from_held_handle: bool,
        complete_only_after_exact_readback: bool,
        complete_only_after_exact_pair_readback: bool,
        create_pair_relative_to_verified_parent_handle: bool,
        retain_both_handles_through_pair_readback: bool,
        deny_write_delete_sharing_for_both: bool,
        verify_each_local_reparse_free_single_link: bool,
        require_distinct_physical_file_identities: bool,
        persist_durable_pair_receipt_before_completion: bool,
        create_new: bool,
        anchor_create_new: bool,
        never_reuse: bool,
        anchor_never_reuse: bool,
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
        final_commit_store_root: String,
        final_commit_receipt_leaf: &'static str,
        require_authenticated_final_commit_gate_in_launch_configuration: bool,
        require_precommit_dormant_mode: bool,
        forbid_controller_pipe_before_final_commit: bool,
        requires_prior_stop_drain_proof: bool,
    },
    ValidateCandidateServiceGenerationHandshake {
        generation: String,
        expected_image_sha256: String,
        trust_manifest_path: String,
        credential_schema: &'static str,
        maximum_credential_lifetime_millis: u64,
        require_scm_process_identity_before_arm: bool,
        require_atomic_prepared_to_armed_transition: bool,
        require_one_use_consumption: bool,
        keep_service_start_pending_through_candidate_exit: bool,
        require_new_process_identity: bool,
        require_held_image_identity: bool,
        require_candidate_only_pipe_generation_handshake: bool,
        forbid_runtime_controller_pipe: bool,
        require_candidate_exit_before_completion: bool,
    },
    SealCandidateGenerationForFinalCommit {
        generation: String,
        generation_binary_root: String,
        generation_state_root: String,
        worker_nonce_root: String,
        candidate_consumption_root: String,
        security_policy_source: &'static str,
        require_candidate_seal_ready_receipt: bool,
        require_worker_exit_ready_receipt: bool,
        require_candidate_stopped_and_all_writers_closed: bool,
        preserve_object_identity_and_bytes: bool,
        require_complete_generation_object_manifest: bool,
        require_each_file_identity_hash_and_security_receipt: bool,
        require_each_directory_identity_and_security_receipt: bool,
        reject_unlisted_generation_objects: bool,
        seal_nonce_artifacts_individually: bool,
        apply_exact_final_security_from_policy: bool,
        reopen_read_only_and_verify_full_security: bool,
        persist_seal_complete_before_active_head: bool,
        irreversible_roll_forward_boundary: bool,
        post_boundary_failure_policy: &'static str,
        elevated_finalizer_only: bool,
    },
    StartCommittedRuntime {
        generation: String,
        service_name: &'static str,
        expected_image_sha256: String,
        active_head_path: String,
        final_commit_store_root: String,
        final_commit_receipt_leaf: &'static str,
        final_commit_gate_derivation: &'static str,
        require_seal_complete_receipt: bool,
        require_active_head_compare_exchange_readback: bool,
        require_candidate_and_runtime_service_identity_match: bool,
        require_distinct_process_identity_from_candidate: bool,
        require_new_pipe_instance_identity: bool,
        require_committed_runtime_generation_handshake: bool,
        require_precommit_dormant_runtime_readback: bool,
        require_controller_pipe_absent_before_final_commit: bool,
        require_generation_writer_roster_empty_before_final_commit: bool,
        runtime_self_activates_only_after_durable_final_commit_readback: bool,
        hold_runtime_process_and_image_handles_through_final_commit: bool,
        elevated_finalizer_only: bool,
    },
    VerifyOperationZeroResidue {
        operation: AuthorityMaintenanceOperation,
        generation: String,
        prior_generation: Option<String>,
        state_maintenance_root: String,
        finalizer_commit_store_root: String,
        candidate_activation_root: String,
        worker_nonce_root: String,
        candidate_consumption_root: String,
        active_head_path: String,
        retirement_staging_manifest: Option<String>,
        retirement_aborted_marker: Option<String>,
        retirement_manifest: Option<String>,
        require_maintenance_service_absent: bool,
        require_no_staging_or_publishing_files: bool,
        require_finalizer_commit_store_preserved: bool,
        require_worker_process_and_transient_state_absent: bool,
        require_candidate_credentials_absent: bool,
        require_nonce_and_consumption_artifacts_sealed: bool,
        require_exact_active_head: bool,
        require_update_retirement_finalized: bool,
        reject_unplanned_residue: bool,
    },
    PersistFinalCommit {
        operation: AuthorityMaintenanceOperation,
        generation: String,
        service_name: &'static str,
        expected_image_sha256: String,
        active_head_path: String,
        final_commit_store_root: String,
        final_commit_receipt_leaf: &'static str,
        final_commit_gate_derivation: &'static str,
        retirement_manifest_path: Option<String>,
        require_seal_complete_receipt: bool,
        require_active_head_compare_exchange_readback: bool,
        require_runtime_identity_and_handshake_readback: bool,
        require_precommit_dormant_runtime_readback: bool,
        require_controller_pipe_absence_readback: bool,
        require_generation_writer_roster_empty_readback: bool,
        bind_runtime_self_activation_gate: bool,
        require_operation_zero_residue_readback: bool,
        require_update_retirement_readback: bool,
        atomic_create_new: bool,
        flush_file_before_publish: bool,
        no_replace: bool,
        flush_parent: bool,
        require_no_publishing_artifact_readback: bool,
        hold_runtime_process_and_image_handles_through_completion: bool,
        elevated_finalizer_only: bool,
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
    VerifyRetirementPreconditions {
        generation: String,
        service_name: &'static str,
        require_service_absent: bool,
        require_active_head_matches_generation: bool,
    },
    VerifyRetiredGenerationReadback {
        generation: String,
        service_name: &'static str,
        active_head_path: String,
        retirement_manifest_path: String,
        require_service_absent: bool,
        require_no_active_generation: bool,
        require_final_retirement_manifest: bool,
    },
    VerifyPrecommitDormantRuntimeReadback {
        generation: String,
        service_name: &'static str,
        expected_image_sha256: String,
        active_head_path: String,
        require_seal_complete_receipt: bool,
        require_exact_service_configuration: bool,
        require_exact_runtime_process_and_image_identity: bool,
        require_precommit_generation_handshake: bool,
        require_active_head_binding: bool,
        require_distinct_runtime_from_candidate: bool,
        require_runtime_dormant: bool,
        require_controller_pipe_absent: bool,
        require_generation_writer_roster_empty: bool,
    },
    VerifyPostcommitServingRuntimeReadback {
        generation: String,
        service_name: &'static str,
        expected_image_sha256: String,
        active_head_path: String,
        final_commit_store_root: String,
        final_commit_receipt_leaf: &'static str,
        final_commit_gate_derivation: &'static str,
        require_seal_complete_receipt: bool,
        require_final_commit_receipt: bool,
        require_exact_service_configuration: bool,
        require_same_precommit_runtime_process_and_image_identity: bool,
        require_runtime_observed_exact_final_commit_receipt: bool,
        require_controller_pipe_present_after_final_commit: bool,
        require_generation_pipe_handshake: bool,
        require_active_head_binding: bool,
        require_serving_state_bound_to_final_commit_gate: bool,
        require_runtime_healthy: bool,
        allow_recovery_runtime_restart_after_authenticated_final_commit: bool,
        require_recovery_final_commit_receipt_immutable: bool,
        require_recovery_active_head_binding: bool,
        require_recovery_exact_service_configuration: bool,
        require_recovery_exact_service_image: bool,
        require_recovery_exact_generation: bool,
        require_recovery_final_commit_gate_binding: bool,
        forbid_final_commit_receipt_rewrite_during_recovery: bool,
        require_recovery_previous_precommit_runtime_absence: bool,
        require_recovery_start_or_adopt_new_runtime_process_identity: bool,
        require_recovery_serving_readback: bool,
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
    StopCandidateValidationServiceExact {
        generation: String,
        expected_image_sha256: String,
        identity_source: &'static str,
        require_exact_process_identity: bool,
        require_natural_exit_or_owned_stop: bool,
        require_scm_stopped_readback: bool,
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
    pub fn operation(&self) -> AuthorityMaintenanceOperation {
        self.operation
    }

    pub fn generation_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.generation)
    }

    pub fn plan_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.plan_sha256)
    }

    pub(crate) fn installed_layout_sha256(&self) -> [u8; 32] {
        self.layout.installed_layout_sha256()
    }

    pub fn transaction_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.transaction_sha256)
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
        installed.lifecycle_driver,
        installed.bridge_launcher,
        installed.runtime_source_manifest,
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

pub(crate) fn preview_retire_with_content(
    layout: &AuthorityLayout,
    content: AuthorityInstallContent,
    installed: VerifiedInstalledGeneration,
) -> Result<AuthorityMaintenancePreview, AuthorityMaintenanceError> {
    let expected = AuthorityInstallContent::new(
        installed.service,
        installed.controller,
        installed.install_helper,
        installed.lifecycle_driver,
        installed.bridge_launcher,
        installed.runtime_source_manifest,
    )?;
    if content != expected {
        return Err(AuthorityMaintenanceError(
            "authority_retire_source_binding_mismatch",
        ));
    }
    preview_retire(layout, installed)
}

pub(crate) fn validate_action_time_install_consent(
    consent_bytes: &[u8],
    preview: &AuthorityMaintenancePreview,
    content: &AuthorityInstallContent,
    now_unix_millis: u64,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    if consent_bytes.is_empty() || consent_bytes.len() > 64 * 1024 {
        return Err(AuthorityMaintenanceError(
            "authority_action_time_consent_invalid",
        ));
    }
    let consent: worker::AuthorityActionTimeConsent = serde_json::from_slice(consent_bytes)
        .map_err(|_| AuthorityMaintenanceError("authority_action_time_consent_invalid"))?;
    consent.validate_for_install(preview, content, now_unix_millis)
}

#[cfg(windows)]
pub(crate) struct NativeInstallPreparation {
    preview: AuthorityMaintenancePreview,
    content: AuthorityInstallContent,
    lease: VerifiedMaintenanceLease,
}

#[cfg(windows)]
impl NativeInstallPreparation {
    pub(crate) fn validate_request_binding(
        &self,
        plan_sha256: [u8; 32],
        generation: [u8; 32],
        service_sha256: [u8; 32],
        controller_sha256: [u8; 32],
        install_helper_sha256: [u8; 32],
        lifecycle_driver_sha256: [u8; 32],
        bridge_launcher_sha256: [u8; 32],
        runtime_source_manifest_sha256: [u8; 32],
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.content.service.sha256 != service_sha256
            || self.content.controller.sha256 != controller_sha256
            || self.content.install_helper.sha256 != install_helper_sha256
            || self.content.lifecycle_driver.sha256 != lifecycle_driver_sha256
            || self.content.bridge_launcher.sha256 != bridge_launcher_sha256
            || self.content.runtime_source_manifest.sha256 != runtime_source_manifest_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_action_time_content_binding_mismatch",
            ));
        }
        if self.preview.plan_sha256()? != plan_sha256
            || self.preview.generation_sha256()? != generation
        {
            return Err(AuthorityMaintenanceError(
                "authority_action_time_plan_binding_mismatch",
            ));
        }
        Ok(())
    }

    pub(crate) fn seal_for_worker(
        self,
        consent_bytes: &[u8],
        expected_consent_sha256: [u8; 32],
        now_unix_millis: u64,
    ) -> Result<PreparedNativeInstallWorker, AuthorityMaintenanceError> {
        if consent_bytes.is_empty() || consent_bytes.len() > 64 * 1024 {
            return Err(AuthorityMaintenanceError(
                "authority_action_time_consent_invalid",
            ));
        }
        if Sha256::digest(consent_bytes).as_slice() != expected_consent_sha256 {
            return Err(AuthorityMaintenanceError(
                "authority_action_time_consent_digest_mismatch",
            ));
        }
        let consent: worker::AuthorityActionTimeConsent = serde_json::from_slice(consent_bytes)
            .map_err(|_| AuthorityMaintenanceError("authority_action_time_consent_invalid"))?;
        let binding = consent.validate_for_operation_binding(
            &self.preview,
            &self.content,
            now_unix_millis,
        )?;
        let capsule = worker::MaintenanceWorkerCapsule::for_operation(
            &self.preview,
            &self.lease,
            binding.transaction_nonce_sha256,
            expected_consent_sha256,
            binding.created_unix_millis,
            binding.expires_unix_millis,
        )?;
        let capsule_sha256 = capsule.digest()?;
        let capsule_bytes = capsule.canonical_bytes()?;
        Ok(PreparedNativeInstallWorker {
            preview: self.preview,
            lease: self.lease,
            capsule,
            capsule_sha256,
            capsule_bytes,
        })
    }
}

#[cfg(windows)]
pub(crate) struct PreparedNativeInstallWorker {
    preview: AuthorityMaintenancePreview,
    lease: VerifiedMaintenanceLease,
    capsule: worker::MaintenanceWorkerCapsule,
    capsule_sha256: [u8; 32],
    capsule_bytes: Vec<u8>,
}

#[cfg(windows)]
pub(crate) fn prepare_native_install_sources(
    layout: &AuthorityLayout,
    service_path: &std::path::Path,
    controller_path: &std::path::Path,
    install_helper_path: &std::path::Path,
    lifecycle_driver_path: &std::path::Path,
    bridge_launcher_path: &std::path::Path,
    runtime_source_manifest_path: &std::path::Path,
) -> Result<NativeInstallPreparation, AuthorityMaintenanceError> {
    receipt_windows::prepare_native_install_sources(
        layout,
        service_path,
        controller_path,
        install_helper_path,
        lifecycle_driver_path,
        bridge_launcher_path,
        runtime_source_manifest_path,
    )
}

#[cfg(windows)]
pub(crate) fn prepare_native_update_sources(
    layout: &AuthorityLayout,
    service_path: &std::path::Path,
    controller_path: &std::path::Path,
    install_helper_path: &std::path::Path,
    lifecycle_driver_path: &std::path::Path,
    bridge_launcher_path: &std::path::Path,
    runtime_source_manifest_path: &std::path::Path,
    prior: VerifiedInstalledGeneration,
) -> Result<NativeInstallPreparation, AuthorityMaintenanceError> {
    receipt_windows::prepare_native_update_sources(
        layout,
        service_path,
        controller_path,
        install_helper_path,
        lifecycle_driver_path,
        bridge_launcher_path,
        runtime_source_manifest_path,
        prior,
    )
}

#[cfg(windows)]
pub(crate) fn prepare_native_retire_sources(
    layout: &AuthorityLayout,
    service_path: &std::path::Path,
    controller_path: &std::path::Path,
    install_helper_path: &std::path::Path,
    lifecycle_driver_path: &std::path::Path,
    bridge_launcher_path: &std::path::Path,
    runtime_source_manifest_path: &std::path::Path,
    prior: VerifiedInstalledGeneration,
) -> Result<NativeInstallPreparation, AuthorityMaintenanceError> {
    receipt_windows::prepare_native_retire_sources(
        layout,
        service_path,
        controller_path,
        install_helper_path,
        lifecycle_driver_path,
        bridge_launcher_path,
        runtime_source_manifest_path,
        prior,
    )
}

#[cfg(windows)]
pub(crate) fn read_native_verified_prior_generation(
    layout: &AuthorityLayout,
    expected_generation: [u8; 32],
) -> Result<VerifiedInstalledGeneration, AuthorityMaintenanceError> {
    let mut source = receipt_windows::NativeSealedInstalledGenerationSource::new(layout);
    VerifiedInstalledGeneration::from_expected_sealed_source(&mut source, expected_generation)
}

#[cfg(windows)]
pub(crate) fn execute_prepared_native_install(
    prepared: PreparedNativeInstallWorker,
) -> Result<AuthorityMaintenanceExecutionReport, AuthorityMaintenanceError> {
    let now_unix_millis: u64 = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|_| AuthorityMaintenanceError("authority_worker_clock_invalid"))?
        .as_millis()
        .try_into()
        .map_err(|_| AuthorityMaintenanceError("authority_worker_clock_invalid"))?;
    let layout = AuthorityLayout::installed()
        .map_err(|_| AuthorityMaintenanceError("authority_layout_unavailable"))?;
    let mut backend = native_runtime_windows::NativeHelperWorkerBackend::new(layout);
    execute_prepared_native_maintenance_with_backend(
        prepared,
        NativeMutationGate::production(),
        now_unix_millis,
        &mut backend,
    )
}

#[cfg(windows)]
const NATIVE_MAINTENANCE_MUTATION_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_native_mutation_preview.v1";
#[cfg(windows)]
const NATIVE_AUTHORITY_MUTATION_ENABLED: bool = false;

#[cfg(windows)]
#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
enum NativeMaintenanceMutationPhase {
    PersistBootstrap,
    PrepareFirstPipe,
    CreateStartWorker,
    BindSourceHandles,
    PersistSourceStaging,
    ConsumeNonceAndStartTransaction,
    AwaitSystemExitReady,
    StopWaitDeleteWorker,
    SealCandidateGeneration,
    AdvanceActiveHead,
    StartCommittedRuntime,
    VerifyDormantSuccessor,
    StagePriorRetirement,
    FinalizePriorRetirement,
    PersistFinalCommit,
    VerifyZeroResidue,
    VerifyUpdateZeroResidue,
    VerifyPostcommitReadback,
    FinalizeRetirement,
    VerifyRetirementZeroResidue,
    PersistRetirementCommit,
    VerifyPostretirementReadback,
}

#[cfg(windows)]
impl NativeMaintenanceMutationPhase {
    fn id(self) -> &'static str {
        match self {
            Self::PersistBootstrap => "persistBootstrap",
            Self::PrepareFirstPipe => "prepareFirstPipe",
            Self::CreateStartWorker => "createStartWorker",
            Self::BindSourceHandles => "bindSourceHandles",
            Self::PersistSourceStaging => "persistSourceStaging",
            Self::ConsumeNonceAndStartTransaction => "consumeNonceAndStartTransaction",
            Self::AwaitSystemExitReady => "awaitSystemExitReady",
            Self::StopWaitDeleteWorker => "stopWaitDeleteWorker",
            Self::SealCandidateGeneration => "sealCandidateGenerationForFinalCommit",
            Self::AdvanceActiveHead => "advanceActiveHeadAtomic",
            Self::StartCommittedRuntime => "startCommittedRuntime",
            Self::VerifyDormantSuccessor => "verifySuccessorBeforeRetirement",
            Self::StagePriorRetirement => "stagePriorRetirementTombstone",
            Self::FinalizePriorRetirement => "finalizePriorRetirementTombstone",
            Self::PersistFinalCommit => "persistFinalCommit",
            Self::VerifyZeroResidue => "verifyOperationZeroResidue",
            Self::VerifyUpdateZeroResidue => "verifyOperationZeroResidue",
            Self::VerifyPostcommitReadback => "verifyProtectedReadback",
            Self::FinalizeRetirement => "finalizeRetirement",
            Self::VerifyRetirementZeroResidue => "verifyRetirementZeroResidue",
            Self::PersistRetirementCommit => "persistRetirementCommit",
            Self::VerifyPostretirementReadback => "verifyPostretirementReadback",
        }
    }

    fn containment(self) -> NativeMaintenanceContainment {
        match self {
            Self::PersistBootstrap
            | Self::PrepareFirstPipe
            | Self::CreateStartWorker
            | Self::BindSourceHandles
            | Self::PersistSourceStaging => NativeMaintenanceContainment::BeforeTransaction,
            Self::ConsumeNonceAndStartTransaction => {
                NativeMaintenanceContainment::InterruptedTransaction
            }
            Self::AwaitSystemExitReady => NativeMaintenanceContainment::TransactionOutcomeBound,
            Self::StopWaitDeleteWorker => NativeMaintenanceContainment::FinalizerBeforeSeal,
            Self::SealCandidateGeneration => {
                NativeMaintenanceContainment::ProbeSealCompleteDurability
            }
            Self::AdvanceActiveHead | Self::StartCommittedRuntime => {
                NativeMaintenanceContainment::ResumeFromSealComplete
            }
            Self::VerifyDormantSuccessor | Self::StagePriorRetirement => {
                NativeMaintenanceContainment::ResumeUpdatePriorRetirement
            }
            Self::FinalizePriorRetirement => {
                NativeMaintenanceContainment::ProbeUpdateRetirementDurability
            }
            Self::VerifyZeroResidue => NativeMaintenanceContainment::ResumeFromSealComplete,
            Self::VerifyUpdateZeroResidue => {
                NativeMaintenanceContainment::ResumeUpdateAfterRetirementCommit
            }
            Self::PersistFinalCommit => NativeMaintenanceContainment::ProbeFinalCommitDurability,
            Self::VerifyPostcommitReadback => {
                NativeMaintenanceContainment::ResumeCommittedRuntimeAndVerify
            }
            Self::FinalizeRetirement => {
                NativeMaintenanceContainment::ProbeRetirementCommitDurability
            }
            Self::VerifyRetirementZeroResidue => {
                NativeMaintenanceContainment::ResumeRetirementCommit
            }
            Self::PersistRetirementCommit => {
                NativeMaintenanceContainment::ProbeRetirementCommitDurability
            }
            Self::VerifyPostretirementReadback => {
                NativeMaintenanceContainment::ReadOnlyRetirementVerification
            }
        }
    }
}

#[cfg(windows)]
const NATIVE_INSTALL_MAINTENANCE_PHASES: [NativeMaintenanceMutationPhase; 14] = [
    NativeMaintenanceMutationPhase::PersistBootstrap,
    NativeMaintenanceMutationPhase::PrepareFirstPipe,
    NativeMaintenanceMutationPhase::CreateStartWorker,
    NativeMaintenanceMutationPhase::BindSourceHandles,
    NativeMaintenanceMutationPhase::PersistSourceStaging,
    NativeMaintenanceMutationPhase::ConsumeNonceAndStartTransaction,
    NativeMaintenanceMutationPhase::AwaitSystemExitReady,
    NativeMaintenanceMutationPhase::StopWaitDeleteWorker,
    NativeMaintenanceMutationPhase::SealCandidateGeneration,
    NativeMaintenanceMutationPhase::AdvanceActiveHead,
    NativeMaintenanceMutationPhase::StartCommittedRuntime,
    NativeMaintenanceMutationPhase::VerifyZeroResidue,
    NativeMaintenanceMutationPhase::PersistFinalCommit,
    NativeMaintenanceMutationPhase::VerifyPostcommitReadback,
];

#[cfg(windows)]
const NATIVE_UPDATE_MAINTENANCE_PHASES: [NativeMaintenanceMutationPhase; 17] = [
    NativeMaintenanceMutationPhase::PersistBootstrap,
    NativeMaintenanceMutationPhase::PrepareFirstPipe,
    NativeMaintenanceMutationPhase::CreateStartWorker,
    NativeMaintenanceMutationPhase::BindSourceHandles,
    NativeMaintenanceMutationPhase::PersistSourceStaging,
    NativeMaintenanceMutationPhase::ConsumeNonceAndStartTransaction,
    NativeMaintenanceMutationPhase::AwaitSystemExitReady,
    NativeMaintenanceMutationPhase::StopWaitDeleteWorker,
    NativeMaintenanceMutationPhase::SealCandidateGeneration,
    NativeMaintenanceMutationPhase::AdvanceActiveHead,
    NativeMaintenanceMutationPhase::StartCommittedRuntime,
    NativeMaintenanceMutationPhase::VerifyDormantSuccessor,
    NativeMaintenanceMutationPhase::StagePriorRetirement,
    NativeMaintenanceMutationPhase::FinalizePriorRetirement,
    NativeMaintenanceMutationPhase::VerifyUpdateZeroResidue,
    NativeMaintenanceMutationPhase::PersistFinalCommit,
    NativeMaintenanceMutationPhase::VerifyPostcommitReadback,
];

#[cfg(windows)]
const NATIVE_RETIREMENT_MAINTENANCE_PHASES: [NativeMaintenanceMutationPhase; 12] = [
    NativeMaintenanceMutationPhase::PersistBootstrap,
    NativeMaintenanceMutationPhase::PrepareFirstPipe,
    NativeMaintenanceMutationPhase::CreateStartWorker,
    NativeMaintenanceMutationPhase::BindSourceHandles,
    NativeMaintenanceMutationPhase::PersistSourceStaging,
    NativeMaintenanceMutationPhase::ConsumeNonceAndStartTransaction,
    NativeMaintenanceMutationPhase::AwaitSystemExitReady,
    NativeMaintenanceMutationPhase::StopWaitDeleteWorker,
    NativeMaintenanceMutationPhase::FinalizeRetirement,
    NativeMaintenanceMutationPhase::VerifyRetirementZeroResidue,
    NativeMaintenanceMutationPhase::PersistRetirementCommit,
    NativeMaintenanceMutationPhase::VerifyPostretirementReadback,
];

#[cfg(windows)]
fn native_maintenance_phases(
    operation: AuthorityMaintenanceOperation,
) -> &'static [NativeMaintenanceMutationPhase] {
    match operation {
        AuthorityMaintenanceOperation::Install => &NATIVE_INSTALL_MAINTENANCE_PHASES,
        AuthorityMaintenanceOperation::Update => &NATIVE_UPDATE_MAINTENANCE_PHASES,
        AuthorityMaintenanceOperation::Retire => &NATIVE_RETIREMENT_MAINTENANCE_PHASES,
    }
}

#[cfg(windows)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum NativeMaintenanceContainment {
    BeforeTransaction,
    InterruptedTransaction,
    TransactionOutcomeBound,
    FinalizerBeforeSeal,
    ProbeSealCompleteDurability,
    ResumeFromSealComplete,
    ResumeUpdatePriorRetirement,
    ProbeUpdateRetirementDurability,
    ResumeUpdateAfterRetirementCommit,
    ProbeFinalCommitDurability,
    ResumeCommittedRuntimeAndVerify,
    ProbeRetirementCommitDurability,
    ResumeRetirementCommit,
    ReadOnlyRetirementVerification,
}

#[cfg(windows)]
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct NativeMaintenanceMutationPreview {
    schema: &'static str,
    operation: AuthorityMaintenanceOperation,
    plan_sha256: String,
    generation: String,
    capsule_sha256: String,
    ordered_phases: Vec<NativeMaintenanceMutationPhase>,
    production_mutation_enabled: bool,
    worker_service_backend_connected: bool,
    native_transaction_executor_connected: bool,
    durable_phase_receipt_required_before_advance: bool,
    write_ahead_intent_required_for_partial_staging: bool,
    candidate_validation_required_before_commit: bool,
    seal_complete_required_before_active_head: bool,
    active_head_cas_required_before_runtime_start: bool,
    active_head_cas_required_before_retirement_commit: bool,
    final_commit_required_after_runtime_handshake: bool,
    retirement_commit_required: bool,
    zero_residue_required_before_final_commit: bool,
    zero_residue_required_before_retirement_commit: bool,
    candidate_and_runtime_process_identity_must_differ: bool,
    phase_aware_security_recovery_required: bool,
    system_worker_self_wait_forbidden: bool,
    system_worker_self_delete_forbidden: bool,
    committed_runtime_started_only_after_worker_exit: bool,
    stop_wait_delete_required: bool,
    exact_absence_readback_required: bool,
    blockers: Vec<&'static str>,
}

#[cfg(windows)]
#[derive(Clone, Copy)]
struct NativeMutationGate {
    enabled: bool,
}

#[cfg(windows)]
impl NativeMutationGate {
    const fn production() -> Self {
        Self {
            enabled: NATIVE_AUTHORITY_MUTATION_ENABLED,
        }
    }

    #[cfg(test)]
    const fn enabled_for_test() -> Self {
        Self { enabled: true }
    }
}

#[cfg(windows)]
trait NativeMaintenanceBackend {
    fn apply_phase(
        &mut self,
        prepared: &mut PreparedNativeInstallWorker,
        operation: AuthorityMaintenanceOperation,
        phase: NativeMaintenanceMutationPhase,
    ) -> Result<(), AuthorityMaintenanceError>;

    fn contain_failure(
        &mut self,
        prepared: &mut PreparedNativeInstallWorker,
        operation: AuthorityMaintenanceOperation,
        failed_phase: NativeMaintenanceMutationPhase,
        containment: NativeMaintenanceContainment,
    ) -> Result<(), AuthorityMaintenanceError>;
}

#[cfg(windows)]
struct DisconnectedNativeMaintenanceBackend;

#[cfg(windows)]
impl NativeMaintenanceBackend for DisconnectedNativeMaintenanceBackend {
    fn apply_phase(
        &mut self,
        _prepared: &mut PreparedNativeInstallWorker,
        _operation: AuthorityMaintenanceOperation,
        _phase: NativeMaintenanceMutationPhase,
    ) -> Result<(), AuthorityMaintenanceError> {
        Err(AuthorityMaintenanceError(
            "authority_native_mutation_backend_not_connected",
        ))
    }

    fn contain_failure(
        &mut self,
        _prepared: &mut PreparedNativeInstallWorker,
        _operation: AuthorityMaintenanceOperation,
        _failed_phase: NativeMaintenanceMutationPhase,
        _containment: NativeMaintenanceContainment,
    ) -> Result<(), AuthorityMaintenanceError> {
        Err(AuthorityMaintenanceError(
            "authority_native_mutation_backend_not_connected",
        ))
    }
}

#[cfg(windows)]
fn validate_prepared_native_maintenance(
    prepared: &PreparedNativeInstallWorker,
    now_unix_millis: u64,
) -> Result<NativeMaintenanceMutationPreview, AuthorityMaintenanceError> {
    if prepared.capsule_sha256.iter().all(|value| *value == 0)
        || prepared.capsule_bytes.is_empty()
        || worker::MaintenanceWorkerCapsule::parse_canonical(
            &prepared.capsule_bytes,
            &prepared.capsule_sha256,
        )? != prepared.capsule
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_capsule_binding_invalid",
        ));
    }
    prepared.capsule.validate_consent_at(now_unix_millis)?;
    if !prepared.lease.is_live()
        || prepared.preview.plan_sha256()? != prepared.capsule.plan_sha256()?
        || prepared.preview.generation_sha256()? != prepared.capsule.generation()?
        || prepared.preview.operation() != prepared.capsule.operation()
        || !prepared
            .lease
            .payloads
            .content_matches(&content_from_projection(&prepared.preview.content)?)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_capsule_binding_invalid",
        ));
    }
    let operation = prepared.preview.operation();
    let retirement = operation == AuthorityMaintenanceOperation::Retire;
    let mut blockers = vec![
        "authority_native_mutation_disabled",
        "authority_native_transaction_executor_not_connected",
    ];
    if retirement {
        blockers.push("authority_native_retirement_protocol_not_connected");
    }
    Ok(NativeMaintenanceMutationPreview {
        schema: NATIVE_MAINTENANCE_MUTATION_SCHEMA,
        operation,
        plan_sha256: hex_lower(&prepared.preview.plan_sha256()?),
        generation: hex_lower(&prepared.preview.generation_sha256()?),
        capsule_sha256: hex_lower(&prepared.capsule_sha256),
        ordered_phases: native_maintenance_phases(operation).to_vec(),
        production_mutation_enabled: NATIVE_AUTHORITY_MUTATION_ENABLED,
        worker_service_backend_connected: true,
        native_transaction_executor_connected: false,
        durable_phase_receipt_required_before_advance: true,
        write_ahead_intent_required_for_partial_staging: true,
        candidate_validation_required_before_commit: !retirement,
        seal_complete_required_before_active_head: !retirement,
        active_head_cas_required_before_runtime_start: !retirement,
        active_head_cas_required_before_retirement_commit: retirement,
        final_commit_required_after_runtime_handshake: !retirement,
        retirement_commit_required: retirement,
        zero_residue_required_before_final_commit: !retirement,
        zero_residue_required_before_retirement_commit: retirement,
        candidate_and_runtime_process_identity_must_differ: !retirement,
        phase_aware_security_recovery_required: true,
        system_worker_self_wait_forbidden: true,
        system_worker_self_delete_forbidden: true,
        committed_runtime_started_only_after_worker_exit: !retirement,
        stop_wait_delete_required: true,
        exact_absence_readback_required: true,
        blockers,
    })
}

#[cfg(windows)]
fn execute_prepared_native_maintenance_with_backend<B: NativeMaintenanceBackend>(
    mut prepared: PreparedNativeInstallWorker,
    gate: NativeMutationGate,
    now_unix_millis: u64,
    backend: &mut B,
) -> Result<AuthorityMaintenanceExecutionReport, AuthorityMaintenanceError> {
    let mutation = validate_prepared_native_maintenance(&prepared, now_unix_millis)?;
    if !gate.enabled {
        return Err(AuthorityMaintenanceError(
            "authority_native_mutation_disabled",
        ));
    }
    let mut completed_steps = Vec::new();
    for phase in mutation.ordered_phases {
        if let Err(error) = backend.apply_phase(&mut prepared, mutation.operation, phase) {
            backend
                .contain_failure(
                    &mut prepared,
                    mutation.operation,
                    phase,
                    phase.containment(),
                )
                .map_err(|_| {
                    AuthorityMaintenanceError("authority_native_mutation_containment_failed")
                })?;
            return Err(error);
        }
        completed_steps.push(phase.id());
    }
    Ok(AuthorityMaintenanceExecutionReport {
        status: "committed",
        startup_recovery: None,
        journal_terminal: Some("committed"),
        recovery_seal: None,
        trusted_boundary_ready: false,
        completed_steps,
        failed_step: None,
        failed_step_cleanup: None,
        failure_cleanup_verified: None,
        rollback_failures: Vec::new(),
        blockers: vec!["authority_runtime_trusted_boundary_not_enabled"],
    })
}

#[cfg(windows)]
pub(crate) fn validate_native_system_worker_bootstrap(
    layout: &AuthorityLayout,
    capsule_sha256: [u8; 32],
) -> Result<(), AuthorityMaintenanceError> {
    let (capsule, store) =
        worker_store_windows::open_native_worker_bootstrap(layout, capsule_sha256)?;
    let current = worker_windows::current_worker_process_binding(&capsule, store.launch())?;
    if store.journal_requires_containment() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_journal_requires_containment",
        ));
    }
    if current.process_id() == 0
        || current.image_sha256()? != capsule.install_helper_sha256()?
        || store.records().len() < 2
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_native_bootstrap_invalid",
        ));
    }
    Ok(())
}

#[cfg(windows)]
fn prepare_signal_and_execute_native_worker<T, R>(
    prepare: impl FnOnce() -> Result<T, AuthorityMaintenanceError>,
    on_ready: impl FnOnce() -> Result<(), &'static str>,
    execute: impl FnOnce(T) -> Result<R, AuthorityMaintenanceError>,
) -> Result<R, AuthorityMaintenanceError> {
    let transaction = prepare()?;
    on_ready().map_err(AuthorityMaintenanceError)?;
    execute(transaction)
}

#[cfg(windows)]
pub(crate) fn execute_native_system_worker(
    layout: &AuthorityLayout,
    capsule_sha256: [u8; 32],
    on_ready: impl FnOnce() -> Result<(), &'static str>,
) -> Result<(), AuthorityMaintenanceError> {
    if !NATIVE_AUTHORITY_MUTATION_ENABLED {
        return Err(AuthorityMaintenanceError(
            "authority_native_mutation_disabled",
        ));
    }
    let now_unix_millis: u64 = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|_| AuthorityMaintenanceError("authority_worker_clock_invalid"))?
        .as_millis()
        .try_into()
        .map_err(|_| AuthorityMaintenanceError("authority_worker_clock_invalid"))?;
    prepare_signal_and_execute_native_worker(
        || {
            native_runtime_windows::prepare_native_system_worker_transaction(
                layout,
                capsule_sha256,
                now_unix_millis,
            )
        },
        on_ready,
        |_transaction| {
            Err(AuthorityMaintenanceError(
                "authority_native_transaction_executor_not_connected",
            ))
        },
    )
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
        "authority_system_worker_staging_not_complete",
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
        AuthorityPayloadDigest::new(
            decode_hex_32(&projection.lifecycle_driver.sha256)?,
            projection.lifecycle_driver.byte_length,
        )?,
        AuthorityPayloadDigest::new(
            decode_hex_32(&projection.bridge_launcher.sha256)?,
            projection.bridge_launcher.byte_length,
        )?,
        AuthorityPayloadDigest::new(
            decode_hex_32(&projection.runtime_source_manifest.sha256)?,
            projection.runtime_source_manifest.byte_length,
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

pub(crate) fn derive_ledger_identity(
    generation: &[u8; 32],
    signer_key_id: &[u8; 32],
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    if generation.iter().all(|value| *value == 0) || signer_key_id.iter().all(|value| *value == 0) {
        return Err(AuthorityMaintenanceError(
            "authority_ledger_identity_invalid",
        ));
    }
    let mut digest = Sha256::new();
    digest.update(LEDGER_DOMAIN);
    digest.update(generation);
    digest.update(signer_key_id);
    Ok(digest.finalize().into())
}

pub(crate) fn authority_service_sid() -> &'static str {
    SERVICE_SID
}

pub(crate) fn authority_state_file_sddl() -> &'static str {
    STATE_FILE_SDDL
}

pub(crate) fn authority_binary_file_sddl() -> &'static str {
    BINARY_FILE_SDDL
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
