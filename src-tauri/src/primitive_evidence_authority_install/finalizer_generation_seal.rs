//! Plan-bound sealing of every durable object required by one authority generation.
//!
//! A directory descriptor does not describe or protect the current descriptor
//! of its children. This module therefore inventories the two generation
//! directories through retained directory handles, opens every planned child
//! relative to those handles, seals every file independently, and seals the
//! generation directories only after a second exact inventory. The activation
//! and one-use artifacts live in shared namespaces; their exact leaves are
//! selected through held namespace handles while unrelated historical leaves
//! remain outside this generation manifest.

#![cfg(windows)]
#![cfg_attr(not(test), allow(dead_code))]

use super::{
    finalizer_commit_protocol::FinalizerCommitBinding,
    finalizer_commit_store_windows::AuthenticatedFinalizerGenerationProgressRoot,
    finalizer_security_windows::{
        authenticate_finalizer_root_capability, capture_preseal_identity_for_target,
        expected_publication_security_sha256, expected_security_digests,
        observe_reopened_seal_phase, recover_reopened_exact_sealed_object, seal_held_object,
        transition_publication_security, verify_reopened_sealed_object,
        with_finalizer_security_privilege, FinalizerObservedSealPhase,
        FinalizerPublicationSecurityPhase, FinalizerRootCapabilityKind,
        FinalizerRootCapabilityReadback, FinalizerRootSecurityPhase, FinalizerSealTarget,
        FinalizerSealedHandle, FinalizerSealedObjectType, FinalizerSecurityError,
        FinalizerSecuritySealReceipt, FinalizerStagingSecurityDescriptor, PreSealStableIdentity,
    },
    runner_policy::{RunnerPolicyStateDescriptor, RUNNER_POLICY_STATE_FILE_NAME},
    security_policy::{
        FINALIZER_COMMIT_TRANSACTION_PROGRESS_HANDLE_ACCESS,
        RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS, RUNTIME_BLOB_FILE_AUTHORITY_ACCESS,
        RUNTIME_BLOB_FILE_CLEANUP_ACCESS, RUNTIME_BLOB_FILE_READ_ACCESS,
    },
    AuthorityInstallContent, AuthorityMaintenanceError, AuthorityPayloadDigest,
    GENERATION_SEAL_OBJECT_COUNT, GENERATION_SEAL_TERMINAL_SEQUENCE,
    RUNTIME_SOURCE_MANIFEST_FILE_NAME,
};
use crate::primitive_evidence_authority_windows::{
    AuthorityLayout, AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::OsString,
    fmt,
    mem::{offset_of, size_of, zeroed},
    os::windows::{
        ffi::{OsStrExt, OsStringExt},
        io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle},
    },
    path::{Path, PathBuf},
    ptr, slice,
};
#[cfg(test)]
use windows_sys::Win32::Storage::FileSystem::FILE_ADD_FILE;
use windows_sys::{
    Wdk::{
        Foundation::{NtQueryObject, ObjectBasicInformation, OBJECT_ATTRIBUTES},
        Storage::FileSystem::{
            FileDispositionInformationEx, FileRenameInformation, NtCreateFile, NtFlushBuffersFile,
            NtSetInformationFile, FILE_CREATE, FILE_DIRECTORY_FILE, FILE_DISPOSITION_DELETE,
            FILE_DISPOSITION_INFORMATION_EX, FILE_NON_DIRECTORY_FILE, FILE_OPEN,
            FILE_OPEN_REPARSE_POINT, FILE_RENAME_INFORMATION, FILE_SYNCHRONOUS_IO_NONALERT,
            FILE_WRITE_THROUGH,
        },
    },
    Win32::{
        Foundation::{
            GetLastError, LocalFree, ERROR_NO_MORE_FILES, FILETIME, HANDLE, INVALID_HANDLE_VALUE,
            UNICODE_STRING,
        },
        Security::{
            Authorization::{
                ConvertSecurityDescriptorToStringSecurityDescriptorW, GetSecurityInfo,
                SDDL_REVISION_1, SE_FILE_OBJECT,
            },
            DACL_SECURITY_INFORMATION, GROUP_SECURITY_INFORMATION, LABEL_SECURITY_INFORMATION,
            OWNER_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR,
        },
        Storage::FileSystem::{
            CreateFileW, FileIdBothDirectoryInfo, FileIdBothDirectoryRestartInfo,
            GetFileInformationByHandle, GetFileInformationByHandleEx, GetFinalPathNameByHandleW,
            ReOpenFile, ReadFile, SetFilePointerEx, WriteFile, BY_HANDLE_FILE_INFORMATION, DELETE,
            FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_NORMAL, FILE_ATTRIBUTE_REPARSE_POINT,
            FILE_BEGIN, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
            FILE_ID_BOTH_DIR_INFO, FILE_LIST_DIRECTORY, FILE_READ_ATTRIBUTES, FILE_READ_DATA,
            FILE_READ_EA, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE, FILE_TRAVERSE,
            FILE_WRITE_DATA, OPEN_EXISTING, READ_CONTROL, SYNCHRONIZE, WRITE_DAC,
        },
        System::{
            Kernel::OBJ_CASE_INSENSITIVE,
            SystemServices::ACCESS_SYSTEM_SECURITY,
            Threading::{
                GetCurrentProcessId, GetProcessId, GetProcessTimes, OpenProcess,
                QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
            },
            IO::IO_STATUS_BLOCK,
        },
    },
};

pub(super) const GENERATION_SEAL_MANIFEST_SCHEMA: &str =
    "vrcforge.authority.generation-seal-manifest.v4";
pub(super) const GENERATION_OBJECT_MANIFEST_SCHEMA: &str =
    "vrcforge.authority.generation-object-manifest.v4";
pub(super) const GENERATION_SEAL_RECEIPT_SCHEMA: &str =
    "vrcforge.authority.generation-seal-receipt.v5";
pub(super) const GENERATION_SEAL_RESTART_READBACK_SCHEMA: &str =
    "vrcforge.authority.generation-seal-restart-readback.v5";
pub(super) const PRE_SEAL_WRITER_CLOSURE_SCHEMA: &str =
    "vrcforge.authority.pre-seal-writer-closure.v3";
pub(super) const GENERATION_SEAL_PROGRESS_SCHEMA: &str =
    "vrcforge.authority.generation-seal-progress.v5";
pub(super) const GENERATION_SEAL_PRODUCTION_ENABLED: bool = false;

const GENERATION_SEAL_MANIFEST_DOMAIN: &[u8] = b"vrcforge-authority-generation-seal-manifest-v4\0";
const GENERATION_OBJECT_MANIFEST_DOMAIN: &[u8] =
    b"vrcforge-authority-generation-object-manifest-v4\0";
const GENERATION_SEAL_RECEIPT_DOMAIN: &[u8] = b"vrcforge-authority-generation-seal-receipt-v5\0";
const GENERATION_SEAL_RESTART_DOMAIN: &[u8] =
    b"vrcforge-authority-generation-seal-restart-readback-v5\0";
const PRE_SEAL_WRITER_CLOSURE_DOMAIN: &[u8] = b"vrcforge-authority-pre-seal-writer-closure-v3\0";
const GENERATION_SEAL_PROGRESS_DOMAIN: &[u8] = b"vrcforge-authority-generation-seal-progress-v5\0";
const GENERATION_SEAL_FINAL_INVENTORY_DOMAIN: &[u8] =
    b"vrcforge-authority-generation-seal-final-inventory-v4\0";
const GENERATION_SEAL_TERMINAL_AUTHORIZATION_DOMAIN: &[u8] =
    b"vrcforge-authority-generation-seal-terminal-authorization-v5\0";
const PROTECTED_BLOB_NAMESPACE_SEAL_DOMAIN: &[u8] =
    b"vrcforge-authority-protected-blob-namespace-seal-v1\0";
const PROTECTED_BLOB_NAMESPACE_EMPTY_INVENTORY_DOMAIN: &[u8] =
    b"vrcforge-authority-protected-blob-namespace-empty-inventory-v1\0";
const PROTECTED_BLOB_NAMESPACE_CANONICAL_PATH_DOMAIN: &[u8] =
    b"vrcforge-authority-protected-blob-namespace-canonical-path-v1\0";
const MAX_SMALL_OBJECT_BYTES: u64 = 64 * 1024;
const DIRECTORY_ENUMERATION_BUFFER_BYTES: usize = 64 * 1024;
const FILE_OPENED_INFORMATION: usize = 1;
const FILE_CREATED_INFORMATION: usize = 2;
const STATUS_OBJECT_NAME_COLLISION: i32 = 0xc000_0035u32 as i32;
const STATUS_NO_SUCH_FILE: i32 = 0xc000_000fu32 as i32;
const STATUS_OBJECT_NAME_NOT_FOUND: i32 = 0xc000_0034u32 as i32;
const STATUS_OBJECT_PATH_NOT_FOUND: i32 = 0xc000_003au32 as i32;
const GENERATION_PROGRESS_PRIVATE_SUFFIX: &str = ".publishing";
const WRITER_EXCLUSION_ROSTER_DOMAIN: &[u8] =
    b"vrcforge-authority-generation-seal-writer-exclusion-roster-v3\0";
const FINALIZER_INVOCATION_DOMAIN: &[u8] =
    b"vrcforge-authority-generation-seal-finalizer-invocation-v3\0";

pub(super) const PROGRESS_ROOT_ACCESS: u32 = FINALIZER_COMMIT_TRANSACTION_PROGRESS_HANDLE_ACCESS;
const PROGRESS_FILE_READ_ACCESS: u32 = READ_CONTROL
    | SYNCHRONIZE
    | FILE_READ_DATA
    | FILE_READ_ATTRIBUTES
    | FILE_READ_EA
    | ACCESS_SYSTEM_SECURITY;
const PROGRESS_FILE_CREATE_ACCESS: u32 = PROGRESS_FILE_READ_ACCESS | FILE_WRITE_DATA;
const PROGRESS_FILE_SEAL_ACCESS: u32 = PROGRESS_FILE_READ_ACCESS | WRITE_DAC;
const PROGRESS_FILE_STAGING_RECOVERY_ACCESS: u32 = PROGRESS_FILE_READ_ACCESS | DELETE;
const PROGRESS_FILE_SEALED_RECOVERY_ACCESS: u32 = PROGRESS_FILE_READ_ACCESS | DELETE | WRITE_DAC;
const FULL_SECURITY_INFORMATION: u32 = OWNER_SECURITY_INFORMATION
    | GROUP_SECURITY_INFORMATION
    | DACL_SECURITY_INFORMATION
    | LABEL_SECURITY_INFORMATION;

type Digest32 = [u8; 32];

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "camelCase")]
pub(super) enum GenerationSealRoot {
    BinaryGeneration,
    StateGeneration,
    ActivationsNamespace,
    WorkerNonceNamespace,
    CandidateConsumptionNamespace,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "camelCase")]
pub(super) enum GenerationSealObjectRole {
    ServiceBinary,
    ControllerBinary,
    InstallHelperBinary,
    LifecycleDriverBinary,
    BridgeLauncherBinary,
    RuntimeSourceManifest,
    RunnerPolicyState,
    Ledger,
    LedgerAnchor,
    TrustManifest,
    ActivationManifest,
    WorkerNonce,
    CandidateConsumption,
    ProtectedBlobNamespace,
    BinaryGenerationDirectory,
    StateGenerationDirectory,
}

impl GenerationSealObjectRole {
    const ALL_IN_SEAL_ORDER: [Self; 16] = [
        Self::ServiceBinary,
        Self::ControllerBinary,
        Self::InstallHelperBinary,
        Self::LifecycleDriverBinary,
        Self::BridgeLauncherBinary,
        Self::RuntimeSourceManifest,
        Self::RunnerPolicyState,
        Self::Ledger,
        Self::LedgerAnchor,
        Self::TrustManifest,
        Self::ActivationManifest,
        Self::WorkerNonce,
        Self::CandidateConsumption,
        Self::ProtectedBlobNamespace,
        Self::BinaryGenerationDirectory,
        Self::StateGenerationDirectory,
    ];

    const fn root(self) -> GenerationSealRoot {
        match self {
            Self::ServiceBinary
            | Self::ControllerBinary
            | Self::InstallHelperBinary
            | Self::LifecycleDriverBinary
            | Self::BridgeLauncherBinary
            | Self::BinaryGenerationDirectory => GenerationSealRoot::BinaryGeneration,
            Self::Ledger
            | Self::LedgerAnchor
            | Self::RunnerPolicyState
            | Self::RuntimeSourceManifest
            | Self::TrustManifest
            | Self::ProtectedBlobNamespace
            | Self::StateGenerationDirectory => GenerationSealRoot::StateGeneration,
            Self::ActivationManifest => GenerationSealRoot::ActivationsNamespace,
            Self::WorkerNonce => GenerationSealRoot::WorkerNonceNamespace,
            Self::CandidateConsumption => GenerationSealRoot::CandidateConsumptionNamespace,
        }
    }

    const fn target(self) -> FinalizerSealTarget {
        match self {
            Self::ServiceBinary
            | Self::ControllerBinary
            | Self::InstallHelperBinary
            | Self::LifecycleDriverBinary
            | Self::BridgeLauncherBinary => FinalizerSealTarget::BinaryFile,
            Self::Ledger | Self::LedgerAnchor => FinalizerSealTarget::LedgerFile,
            Self::TrustManifest | Self::ActivationManifest | Self::RunnerPolicyState => {
                FinalizerSealTarget::ImmutableStateFile
            }
            Self::RuntimeSourceManifest => FinalizerSealTarget::RuntimeSourceManifestFile,
            Self::WorkerNonce => FinalizerSealTarget::WorkerNonceFile,
            Self::CandidateConsumption => FinalizerSealTarget::CandidateConsumptionFile,
            Self::ProtectedBlobNamespace => FinalizerSealTarget::RuntimeBlobDirectory,
            Self::BinaryGenerationDirectory | Self::StateGenerationDirectory => {
                FinalizerSealTarget::GenerationDirectory
            }
        }
    }

    const fn fixed_relative_name(self) -> Option<&'static str> {
        match self {
            Self::ServiceBinary => Some("vrcforge_primitive_evidence_service.exe"),
            Self::ControllerBinary => Some("vrcforge_primitive_evidence_controller.exe"),
            Self::InstallHelperBinary => Some("vrcforge_primitive_evidence_install_helper.exe"),
            Self::LifecycleDriverBinary => Some("vrcforge_primitive_lifecycle_driver.exe"),
            Self::BridgeLauncherBinary => Some("vrcforge_primitive_bridge_launcher.exe"),
            Self::RuntimeSourceManifest => Some(RUNTIME_SOURCE_MANIFEST_FILE_NAME),
            Self::RunnerPolicyState => Some(RUNNER_POLICY_STATE_FILE_NAME),
            Self::Ledger => Some("ledger.bin"),
            Self::LedgerAnchor => Some("ledger.bin.anchor"),
            Self::TrustManifest => Some("trust.json"),
            Self::ProtectedBlobNamespace => Some(AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME),
            Self::BinaryGenerationDirectory | Self::StateGenerationDirectory => Some("."),
            Self::ActivationManifest | Self::WorkerNonce | Self::CandidateConsumption => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct GenerationSealBinding {
    capsule_sha256: Digest32,
    plan_sha256: Digest32,
    generation_sha256: Digest32,
    transaction_sha256: Digest32,
    expected_generation_object_manifest_sha256: Digest32,
    final_commit_store_root_identity_sha256: Digest32,
}

impl GenerationSealBinding {
    pub(super) fn new(
        capsule_sha256: Digest32,
        plan_sha256: Digest32,
        generation_sha256: Digest32,
        transaction_sha256: Digest32,
        expected_generation_object_manifest_sha256: Digest32,
        final_commit_store_root_identity_sha256: Digest32,
    ) -> Result<Self, GenerationSealError> {
        let value = Self {
            capsule_sha256,
            plan_sha256,
            generation_sha256,
            transaction_sha256,
            expected_generation_object_manifest_sha256,
            final_commit_store_root_identity_sha256,
        };
        if [
            value.capsule_sha256,
            value.plan_sha256,
            value.generation_sha256,
            value.transaction_sha256,
            value.expected_generation_object_manifest_sha256,
            value.final_commit_store_root_identity_sha256,
        ]
        .iter()
        .any(is_zero_digest)
        {
            return Err(GenerationSealError(
                "authority_generation_seal_binding_invalid",
            ));
        }
        Ok(value)
    }

    pub(super) fn generation_sha256(&self) -> Digest32 {
        self.generation_sha256
    }

    pub(super) fn capsule_sha256(&self) -> Digest32 {
        self.capsule_sha256
    }

    pub(super) fn plan_sha256(&self) -> Digest32 {
        self.plan_sha256
    }

    pub(super) fn transaction_sha256(&self) -> Digest32 {
        self.transaction_sha256
    }

    pub(super) fn generation_object_manifest_sha256(&self) -> Digest32 {
        self.expected_generation_object_manifest_sha256
    }

    pub(super) fn final_commit_store_root_identity_sha256(&self) -> Digest32 {
        self.final_commit_store_root_identity_sha256
    }

    pub(super) fn from_commit_binding(
        value: FinalizerCommitBinding,
    ) -> Result<Self, GenerationSealError> {
        Self::new(
            value.capsule_sha256(),
            value.plan_sha256(),
            value.generation_sha256(),
            value.transaction_sha256(),
            value.plan_binding().generation_object_manifest_sha256(),
            value.final_commit_store_root_identity_sha256(),
        )
    }
}

/// Exact kernel identity of the runner-policy file observed through the held
/// sealed-generation handle after independent restart verification.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct GenerationSealedRunnerPolicyIdentity {
    volume_serial: u64,
    file_id: [u8; 16],
    link_count: u32,
    attributes: u32,
}

impl GenerationSealedRunnerPolicyIdentity {
    fn new(
        volume_serial: u64,
        file_id: [u8; 16],
        link_count: u32,
        attributes: u32,
    ) -> Result<Self, GenerationSealError> {
        let value = Self {
            volume_serial,
            file_id,
            link_count,
            attributes,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(self) -> Result<(), GenerationSealError> {
        if self.volume_serial == 0
            || self.file_id.iter().all(|value| *value == 0)
            || self.link_count != 1
            || self.attributes == 0
            || self.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
        {
            return Err(GenerationSealError(
                "authority_generation_seal_runner_policy_identity_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn volume_serial(self) -> u64 {
        self.volume_serial
    }

    pub(super) fn file_id(self) -> [u8; 16] {
        self.file_id
    }

    pub(super) fn link_count(self) -> u32 {
        self.link_count
    }

    pub(super) fn attributes(self) -> u32 {
        self.attributes
    }

    #[cfg(test)]
    pub(super) fn exact_test_fixture(seed: u8) -> Self {
        Self::new(
            u64::from(seed).saturating_add(1),
            [seed.max(1); 16],
            1,
            FILE_ATTRIBUTE_NORMAL,
        )
        .expect("sealed runner-policy identity fixture must be valid")
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct GenerationSealFileExpectation {
    byte_length: u64,
    bytes_sha256: Digest32,
}

impl GenerationSealFileExpectation {
    pub(super) fn new(
        byte_length: u64,
        bytes_sha256: Digest32,
    ) -> Result<Self, GenerationSealError> {
        if byte_length == 0 || is_zero_digest(&bytes_sha256) {
            return Err(GenerationSealError(
                "authority_generation_seal_file_expectation_invalid",
            ));
        }
        Ok(Self {
            byte_length,
            bytes_sha256,
        })
    }

    fn from_payload(value: AuthorityPayloadDigest) -> Self {
        Self {
            byte_length: value.byte_length(),
            bytes_sha256: *value.sha256(),
        }
    }
}

/// A ledger anchor is mandatory and cannot be supplied as an untyped extra
/// state-directory member. Its distinct constructor prevents callers from
/// building a generation material set that names only `ledger.bin`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct GenerationSealLedgerAnchorExpectation(GenerationSealFileExpectation);

impl GenerationSealLedgerAnchorExpectation {
    pub(super) fn new(
        byte_length: u64,
        bytes_sha256: Digest32,
    ) -> Result<Self, GenerationSealError> {
        Ok(Self(GenerationSealFileExpectation::new(
            byte_length,
            bytes_sha256,
        )?))
    }

    fn as_file_expectation(self) -> GenerationSealFileExpectation {
        self.0
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct GenerationSealRunnerPolicyExpectation {
    file: GenerationSealFileExpectation,
    generation_sha256: Digest32,
    transaction_sha256: Digest32,
    binding_sha256: Digest32,
}

impl GenerationSealRunnerPolicyExpectation {
    pub(super) fn from_descriptor(
        descriptor: RunnerPolicyStateDescriptor,
    ) -> Result<Self, GenerationSealError> {
        if is_zero_digest(&descriptor.generation_sha256())
            || is_zero_digest(&descriptor.transaction_sha256())
            || is_zero_digest(&descriptor.binding_sha256())
        {
            return Err(GenerationSealError(
                "authority_generation_seal_runner_policy_invalid",
            ));
        }
        Ok(Self {
            file: GenerationSealFileExpectation::new(
                descriptor.byte_length(),
                descriptor.bytes_sha256(),
            )?,
            generation_sha256: descriptor.generation_sha256(),
            transaction_sha256: descriptor.transaction_sha256(),
            binding_sha256: descriptor.binding_sha256(),
        })
    }

    fn as_file_expectation_for_binding(
        self,
        generation_sha256: Digest32,
        transaction_sha256: Digest32,
    ) -> Result<GenerationSealFileExpectation, GenerationSealError> {
        if self.generation_sha256 != generation_sha256
            || self.transaction_sha256 != transaction_sha256
            || is_zero_digest(&self.binding_sha256)
        {
            return Err(GenerationSealError(
                "authority_generation_seal_runner_policy_mismatch",
            ));
        }
        Ok(self.file)
    }

    fn binding_sha256(self) -> Digest32 {
        self.binding_sha256
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct GenerationSealMaterials {
    content: AuthorityInstallContent,
    runner_policy: GenerationSealRunnerPolicyExpectation,
    ledger: GenerationSealFileExpectation,
    ledger_anchor: GenerationSealLedgerAnchorExpectation,
    trust_manifest: GenerationSealFileExpectation,
    activation_manifest: GenerationSealFileExpectation,
    worker_nonce_key_sha256: Digest32,
    worker_nonce: GenerationSealFileExpectation,
    candidate_credential_sha256: Digest32,
    candidate_consumption: GenerationSealFileExpectation,
}

impl GenerationSealMaterials {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn new(
        content: AuthorityInstallContent,
        runner_policy: GenerationSealRunnerPolicyExpectation,
        ledger: GenerationSealFileExpectation,
        ledger_anchor: GenerationSealLedgerAnchorExpectation,
        trust_manifest: GenerationSealFileExpectation,
        activation_manifest: GenerationSealFileExpectation,
        worker_nonce_key_sha256: Digest32,
        worker_nonce: GenerationSealFileExpectation,
        candidate_credential_sha256: Digest32,
        candidate_consumption: GenerationSealFileExpectation,
    ) -> Result<Self, GenerationSealError> {
        if is_zero_digest(&worker_nonce_key_sha256) || is_zero_digest(&candidate_credential_sha256)
        {
            return Err(GenerationSealError(
                "authority_generation_seal_nonce_key_invalid",
            ));
        }
        Ok(Self {
            content,
            runner_policy,
            ledger,
            ledger_anchor,
            trust_manifest,
            activation_manifest,
            worker_nonce_key_sha256,
            worker_nonce,
            candidate_credential_sha256,
            candidate_consumption,
        })
    }

    pub(super) fn object_manifest_sha256(
        &self,
        generation_sha256: Digest32,
        transaction_sha256: Digest32,
    ) -> Result<Digest32, GenerationSealError> {
        if is_zero_digest(&generation_sha256) || is_zero_digest(&transaction_sha256) {
            return Err(GenerationSealError(
                "authority_generation_seal_binding_invalid",
            ));
        }
        let objects = self.build_objects(generation_sha256, transaction_sha256)?;
        let projection = GenerationObjectManifestProjection {
            schema: GENERATION_OBJECT_MANIFEST_SCHEMA,
            generation_sha256: hex_lower(&generation_sha256),
            runner_policy_binding_sha256: hex_lower(&self.runner_policy.binding_sha256()),
            worker_nonce_key_sha256: hex_lower(&self.worker_nonce_key_sha256),
            candidate_credential_sha256: hex_lower(&self.candidate_credential_sha256),
            objects: &objects,
            generation_directories_exhaustive: true,
            external_namespace_selection_by_exact_leaf: true,
            files_sealed_before_directories: true,
            pre_seal_generation_writer_roster_empty: true,
            pre_seal_ledger_writer_roster_empty: true,
        };
        let bytes = serde_json::to_vec(&projection).map_err(|_| {
            GenerationSealError("authority_generation_object_manifest_serialization_failed")
        })?;
        Ok(domain_digest(GENERATION_OBJECT_MANIFEST_DOMAIN, &bytes))
    }

    fn build_objects(
        &self,
        generation_sha256: Digest32,
        transaction_sha256: Digest32,
    ) -> Result<Vec<GenerationSealObjectPlan>, GenerationSealError> {
        let generation = hex_lower(&generation_sha256);
        let relative_paths = BTreeMap::from([
            (
                GenerationSealObjectRole::ActivationManifest,
                format!("{generation}.json"),
            ),
            (
                GenerationSealObjectRole::WorkerNonce,
                format!(
                    "nonce.{}.consumed.json",
                    hex_lower(&self.worker_nonce_key_sha256)
                ),
            ),
            (
                GenerationSealObjectRole::CandidateConsumption,
                format!(
                    "candidate.{}.consumed.json",
                    hex_lower(&self.candidate_credential_sha256)
                ),
            ),
        ]);
        let runner_policy = self
            .runner_policy
            .as_file_expectation_for_binding(generation_sha256, transaction_sha256)?;
        let file_contents = BTreeMap::from([
            (
                GenerationSealObjectRole::ServiceBinary,
                GenerationSealFileExpectation::from_payload(self.content.service()),
            ),
            (
                GenerationSealObjectRole::ControllerBinary,
                GenerationSealFileExpectation::from_payload(self.content.controller()),
            ),
            (
                GenerationSealObjectRole::InstallHelperBinary,
                GenerationSealFileExpectation::from_payload(self.content.install_helper()),
            ),
            (
                GenerationSealObjectRole::LifecycleDriverBinary,
                GenerationSealFileExpectation::from_payload(self.content.lifecycle_driver()),
            ),
            (
                GenerationSealObjectRole::BridgeLauncherBinary,
                GenerationSealFileExpectation::from_payload(self.content.bridge_launcher()),
            ),
            (
                GenerationSealObjectRole::RuntimeSourceManifest,
                GenerationSealFileExpectation::from_payload(self.content.runtime_source_manifest()),
            ),
            (GenerationSealObjectRole::RunnerPolicyState, runner_policy),
            (GenerationSealObjectRole::Ledger, self.ledger),
            (
                GenerationSealObjectRole::LedgerAnchor,
                self.ledger_anchor.as_file_expectation(),
            ),
            (GenerationSealObjectRole::TrustManifest, self.trust_manifest),
            (
                GenerationSealObjectRole::ActivationManifest,
                self.activation_manifest,
            ),
            (GenerationSealObjectRole::WorkerNonce, self.worker_nonce),
            (
                GenerationSealObjectRole::CandidateConsumption,
                self.candidate_consumption,
            ),
        ]);
        let mut objects = Vec::with_capacity(GenerationSealObjectRole::ALL_IN_SEAL_ORDER.len());
        for role in GenerationSealObjectRole::ALL_IN_SEAL_ORDER {
            let relative_path = role
                .fixed_relative_name()
                .map(str::to_string)
                .or_else(|| relative_paths.get(&role).cloned())
                .ok_or(GenerationSealError(
                    "authority_generation_seal_relative_path_invalid",
                ))?;
            objects.push(GenerationSealObjectPlan::new(
                role,
                relative_path,
                file_contents.get(&role).copied(),
            )?);
        }
        Ok(objects)
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct GenerationObjectManifestProjection<'a> {
    schema: &'static str,
    generation_sha256: String,
    runner_policy_binding_sha256: String,
    worker_nonce_key_sha256: String,
    candidate_credential_sha256: String,
    objects: &'a [GenerationSealObjectPlan],
    generation_directories_exhaustive: bool,
    external_namespace_selection_by_exact_leaf: bool,
    files_sealed_before_directories: bool,
    pre_seal_generation_writer_roster_empty: bool,
    pre_seal_ledger_writer_roster_empty: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct GenerationSealObjectPlan {
    role: GenerationSealObjectRole,
    root: GenerationSealRoot,
    relative_path: String,
    object_type: SealedObjectKind,
    expected_byte_length: Option<u64>,
    expected_bytes_sha256: Option<String>,
    staging_security_sha256: String,
    final_security_sha256: String,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
enum SealedObjectKind {
    File,
    Directory,
}

impl GenerationSealObjectPlan {
    fn new(
        role: GenerationSealObjectRole,
        relative_path: String,
        content: Option<GenerationSealFileExpectation>,
    ) -> Result<Self, GenerationSealError> {
        let target = role.target();
        let (staging_security_sha256, final_security_sha256) =
            expected_security_digests(target).map_err(GenerationSealError::from)?;
        let object_type = match target.object_type() {
            FinalizerSealedObjectType::File => SealedObjectKind::File,
            FinalizerSealedObjectType::Directory => SealedObjectKind::Directory,
        };
        let value = Self {
            role,
            root: role.root(),
            relative_path,
            object_type,
            expected_byte_length: content.map(|value| value.byte_length),
            expected_bytes_sha256: content.map(|value| hex_lower(&value.bytes_sha256)),
            staging_security_sha256: hex_lower(&staging_security_sha256),
            final_security_sha256: hex_lower(&final_security_sha256),
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), GenerationSealError> {
        if self.root != self.role.root() {
            return Err(GenerationSealError(
                "authority_generation_seal_object_root_invalid",
            ));
        }
        let target = self.role.target();
        let expected_kind = match target.object_type() {
            FinalizerSealedObjectType::File => SealedObjectKind::File,
            FinalizerSealedObjectType::Directory => SealedObjectKind::Directory,
        };
        if self.object_type != expected_kind
            || decode_hex_32(&self.staging_security_sha256).is_none()
            || decode_hex_32(&self.final_security_sha256).is_none()
        {
            return Err(GenerationSealError(
                "authority_generation_seal_object_policy_invalid",
            ));
        }
        let (expected_staging, expected_final) =
            expected_security_digests(target).map_err(GenerationSealError::from)?;
        if decode_hex_32(&self.staging_security_sha256) != Some(expected_staging)
            || decode_hex_32(&self.final_security_sha256) != Some(expected_final)
        {
            return Err(GenerationSealError(
                "authority_generation_seal_object_policy_invalid",
            ));
        }

        match self.object_type {
            SealedObjectKind::Directory => {
                let expected_relative_path = match self.role {
                    GenerationSealObjectRole::ProtectedBlobNamespace => {
                        AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME
                    }
                    GenerationSealObjectRole::BinaryGenerationDirectory
                    | GenerationSealObjectRole::StateGenerationDirectory => ".",
                    _ => {
                        return Err(GenerationSealError(
                            "authority_generation_seal_directory_plan_invalid",
                        ));
                    }
                };
                if self.relative_path != expected_relative_path
                    || self.expected_byte_length.is_some()
                    || self.expected_bytes_sha256.is_some()
                {
                    return Err(GenerationSealError(
                        "authority_generation_seal_directory_plan_invalid",
                    ));
                }
            }
            SealedObjectKind::File => {
                validate_relative_name(&self.relative_path)?;
                let length = self.expected_byte_length.ok_or(GenerationSealError(
                    "authority_generation_seal_file_plan_invalid",
                ))?;
                let maximum = target.maximum_byte_length().ok_or(GenerationSealError(
                    "authority_generation_seal_file_plan_invalid",
                ))?;
                if length == 0
                    || length > maximum
                    || self
                        .expected_bytes_sha256
                        .as_deref()
                        .and_then(decode_hex_32)
                        .is_none()
                {
                    return Err(GenerationSealError(
                        "authority_generation_seal_file_plan_invalid",
                    ));
                }
            }
        }
        if let Some(fixed) = self.role.fixed_relative_name() {
            if self.relative_path != fixed {
                return Err(GenerationSealError(
                    "authority_generation_seal_relative_path_invalid",
                ));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct GenerationSealManifest {
    schema: String,
    capsule_sha256: String,
    plan_sha256: String,
    generation_sha256: String,
    transaction_sha256: String,
    generation_object_manifest_sha256: String,
    runner_policy_binding_sha256: String,
    final_commit_store_root_identity_sha256: String,
    worker_nonce_key_sha256: String,
    candidate_credential_sha256: String,
    objects: Vec<GenerationSealObjectPlan>,
    generation_directories_exhaustive: bool,
    external_namespace_selection_by_exact_leaf: bool,
    files_sealed_before_directories: bool,
    pre_seal_generation_writer_roster_empty: bool,
    pre_seal_ledger_writer_roster_empty: bool,
}

impl GenerationSealManifest {
    pub(super) fn new(
        binding: GenerationSealBinding,
        materials: &GenerationSealMaterials,
    ) -> Result<Self, GenerationSealError> {
        let object_manifest_sha256 = materials
            .object_manifest_sha256(binding.generation_sha256, binding.transaction_sha256)?;
        if object_manifest_sha256 != binding.expected_generation_object_manifest_sha256 {
            return Err(GenerationSealError(
                "authority_generation_seal_plan_object_manifest_mismatch",
            ));
        }
        let generation = hex_lower(&binding.generation_sha256);
        let objects =
            materials.build_objects(binding.generation_sha256, binding.transaction_sha256)?;
        let value = Self {
            schema: GENERATION_SEAL_MANIFEST_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&binding.capsule_sha256),
            plan_sha256: hex_lower(&binding.plan_sha256),
            generation_sha256: generation,
            transaction_sha256: hex_lower(&binding.transaction_sha256),
            generation_object_manifest_sha256: hex_lower(&object_manifest_sha256),
            runner_policy_binding_sha256: hex_lower(&materials.runner_policy.binding_sha256()),
            final_commit_store_root_identity_sha256: hex_lower(
                &binding.final_commit_store_root_identity_sha256,
            ),
            worker_nonce_key_sha256: hex_lower(&materials.worker_nonce_key_sha256),
            candidate_credential_sha256: hex_lower(&materials.candidate_credential_sha256),
            objects,
            generation_directories_exhaustive: true,
            external_namespace_selection_by_exact_leaf: true,
            files_sealed_before_directories: true,
            pre_seal_generation_writer_roster_empty: true,
            pre_seal_ledger_writer_roster_empty: true,
        };
        value.validate()?;
        Ok(value)
    }

    pub(super) fn parse_canonical(bytes: &[u8]) -> Result<Self, GenerationSealError> {
        if bytes.is_empty() || bytes.len() > MAX_SMALL_OBJECT_BYTES as usize {
            return Err(GenerationSealError(
                "authority_generation_seal_manifest_size_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes).map_err(|_| {
            GenerationSealError("authority_generation_seal_manifest_serialization_invalid")
        })?;
        value.validate()?;
        if value.canonical_bytes()? != bytes {
            return Err(GenerationSealError(
                "authority_generation_seal_manifest_not_canonical",
            ));
        }
        Ok(value)
    }

    pub(super) fn canonical_bytes(&self) -> Result<Vec<u8>, GenerationSealError> {
        self.validate()?;
        serde_json::to_vec(self).map_err(|_| {
            GenerationSealError("authority_generation_seal_manifest_serialization_failed")
        })
    }

    pub(super) fn digest(&self) -> Result<Digest32, GenerationSealError> {
        let bytes = self.canonical_bytes()?;
        Ok(domain_digest(GENERATION_SEAL_MANIFEST_DOMAIN, &bytes))
    }

    fn binding(&self) -> Result<GenerationSealBinding, GenerationSealError> {
        GenerationSealBinding::new(
            decode_required_digest(&self.capsule_sha256)?,
            decode_required_digest(&self.plan_sha256)?,
            decode_required_digest(&self.generation_sha256)?,
            decode_required_digest(&self.transaction_sha256)?,
            decode_required_digest(&self.generation_object_manifest_sha256)?,
            decode_required_digest(&self.final_commit_store_root_identity_sha256)?,
        )
    }

    fn object(&self, role: GenerationSealObjectRole) -> &GenerationSealObjectPlan {
        &self.objects[role_index(role)]
    }

    pub(super) fn runner_policy_binding_sha256(&self) -> Result<Digest32, GenerationSealError> {
        decode_required_digest(&self.runner_policy_binding_sha256)
    }

    fn validate(&self) -> Result<(), GenerationSealError> {
        let binding = self.binding()?;
        if self.schema != GENERATION_SEAL_MANIFEST_SCHEMA
            || decode_required_digest(&self.worker_nonce_key_sha256).is_err()
            || decode_required_digest(&self.candidate_credential_sha256).is_err()
            || decode_required_digest(&self.generation_object_manifest_sha256).is_err()
            || decode_required_digest(&self.runner_policy_binding_sha256).is_err()
            || decode_required_digest(&self.final_commit_store_root_identity_sha256).is_err()
            || !self.generation_directories_exhaustive
            || !self.external_namespace_selection_by_exact_leaf
            || !self.files_sealed_before_directories
            || !self.pre_seal_generation_writer_roster_empty
            || !self.pre_seal_ledger_writer_roster_empty
            || self.objects.len() != GenerationSealObjectRole::ALL_IN_SEAL_ORDER.len()
        {
            return Err(GenerationSealError(
                "authority_generation_seal_manifest_invalid",
            ));
        }
        let mut paths = BTreeSet::new();
        let mut identities = BTreeSet::new();
        for (index, role) in GenerationSealObjectRole::ALL_IN_SEAL_ORDER
            .iter()
            .copied()
            .enumerate()
        {
            let object = self.objects.get(index).ok_or(GenerationSealError(
                "authority_generation_seal_manifest_incomplete",
            ))?;
            object.validate()?;
            if object.role != role
                || !paths.insert((object.root, object.relative_path.to_ascii_lowercase()))
                || !identities.insert(object.role)
            {
                return Err(GenerationSealError(
                    "authority_generation_seal_manifest_incomplete",
                ));
            }
        }
        let generation = hex_lower(&binding.generation_sha256);
        let projection = GenerationObjectManifestProjection {
            schema: GENERATION_OBJECT_MANIFEST_SCHEMA,
            generation_sha256: self.generation_sha256.clone(),
            runner_policy_binding_sha256: self.runner_policy_binding_sha256.clone(),
            worker_nonce_key_sha256: self.worker_nonce_key_sha256.clone(),
            candidate_credential_sha256: self.candidate_credential_sha256.clone(),
            objects: &self.objects,
            generation_directories_exhaustive: self.generation_directories_exhaustive,
            external_namespace_selection_by_exact_leaf: self
                .external_namespace_selection_by_exact_leaf,
            files_sealed_before_directories: self.files_sealed_before_directories,
            pre_seal_generation_writer_roster_empty: self.pre_seal_generation_writer_roster_empty,
            pre_seal_ledger_writer_roster_empty: self.pre_seal_ledger_writer_roster_empty,
        };
        let projection_bytes = serde_json::to_vec(&projection).map_err(|_| {
            GenerationSealError("authority_generation_object_manifest_serialization_failed")
        })?;
        if decode_hex_32(&self.generation_object_manifest_sha256)
            != Some(domain_digest(
                GENERATION_OBJECT_MANIFEST_DOMAIN,
                &projection_bytes,
            ))
            || binding.expected_generation_object_manifest_sha256
                != decode_required_digest(&self.generation_object_manifest_sha256)?
        {
            return Err(GenerationSealError(
                "authority_generation_seal_plan_object_manifest_mismatch",
            ));
        }
        if self
            .object(GenerationSealObjectRole::ActivationManifest)
            .relative_path
            != format!("{generation}.json")
            || self
                .object(GenerationSealObjectRole::WorkerNonce)
                .relative_path
                != format!("nonce.{}.consumed.json", self.worker_nonce_key_sha256)
            || self
                .object(GenerationSealObjectRole::CandidateConsumption)
                .relative_path
                != format!(
                    "candidate.{}.consumed.json",
                    self.candidate_credential_sha256
                )
        {
            return Err(GenerationSealError(
                "authority_generation_seal_manifest_binding_mismatch",
            ));
        }
        Ok(())
    }
}

/// Action-time proof that every process allowed to prepare this generation has
/// closed its generation-object and ledger writer handles.  The committed
/// runtime does not exist at this phase; its later dormant-start proof belongs
/// to the final-commit protocol, not to generation sealing.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PreSealWriterClosureReadback {
    schema: String,
    manifest_sha256: String,
    observer_invocation_sha256: String,
    worker_writer_roster_sha256: String,
    candidate_writer_roster_sha256: String,
    finalizer_writer_roster_sha256: String,
    generation_writer_roster_sha256: String,
    ledger_writer_roster_sha256: String,
    worker_writer_handle_count: u32,
    candidate_writer_handle_count: u32,
    finalizer_writer_handle_count: u32,
    generation_writer_handle_count: u32,
    ledger_writer_handle_count: u32,
    all_writer_process_identities_exact: bool,
}

impl PreSealWriterClosureReadback {
    #[allow(clippy::too_many_arguments)]
    fn new(
        manifest: &GenerationSealManifest,
        observer_invocation_sha256: Digest32,
        worker_writer_roster_sha256: Digest32,
        candidate_writer_roster_sha256: Digest32,
        finalizer_writer_roster_sha256: Digest32,
        generation_writer_roster_sha256: Digest32,
        ledger_writer_roster_sha256: Digest32,
        worker_writer_handle_count: u32,
        candidate_writer_handle_count: u32,
        finalizer_writer_handle_count: u32,
        generation_writer_handle_count: u32,
        ledger_writer_handle_count: u32,
        all_writer_process_identities_exact: bool,
    ) -> Result<Self, GenerationSealError> {
        let value = Self {
            schema: PRE_SEAL_WRITER_CLOSURE_SCHEMA.to_string(),
            manifest_sha256: hex_lower(&manifest.digest()?),
            observer_invocation_sha256: hex_lower(&observer_invocation_sha256),
            worker_writer_roster_sha256: hex_lower(&worker_writer_roster_sha256),
            candidate_writer_roster_sha256: hex_lower(&candidate_writer_roster_sha256),
            finalizer_writer_roster_sha256: hex_lower(&finalizer_writer_roster_sha256),
            generation_writer_roster_sha256: hex_lower(&generation_writer_roster_sha256),
            ledger_writer_roster_sha256: hex_lower(&ledger_writer_roster_sha256),
            worker_writer_handle_count,
            candidate_writer_handle_count,
            finalizer_writer_handle_count,
            generation_writer_handle_count,
            ledger_writer_handle_count,
            all_writer_process_identities_exact,
        };
        value.validate_against(manifest)?;
        Ok(value)
    }

    fn parse_canonical(
        bytes: &[u8],
        manifest: &GenerationSealManifest,
    ) -> Result<Self, GenerationSealError> {
        if bytes.is_empty() || bytes.len() > MAX_SMALL_OBJECT_BYTES as usize {
            return Err(GenerationSealError(
                "authority_generation_seal_writer_closure_size_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes).map_err(|_| {
            GenerationSealError("authority_generation_seal_writer_closure_serialization_invalid")
        })?;
        value.validate_against(manifest)?;
        if value.canonical_bytes(manifest)? != bytes {
            return Err(GenerationSealError(
                "authority_generation_seal_writer_closure_not_canonical",
            ));
        }
        Ok(value)
    }

    pub(super) fn canonical_bytes(
        &self,
        manifest: &GenerationSealManifest,
    ) -> Result<Vec<u8>, GenerationSealError> {
        self.validate_against(manifest)?;
        serde_json::to_vec(self).map_err(|_| {
            GenerationSealError("authority_generation_seal_writer_closure_serialization_failed")
        })
    }

    pub(super) fn digest(
        &self,
        manifest: &GenerationSealManifest,
    ) -> Result<Digest32, GenerationSealError> {
        Ok(domain_digest(
            PRE_SEAL_WRITER_CLOSURE_DOMAIN,
            &self.canonical_bytes(manifest)?,
        ))
    }

    fn validate_against(
        &self,
        manifest: &GenerationSealManifest,
    ) -> Result<(), GenerationSealError> {
        manifest.validate()?;
        if self.schema != PRE_SEAL_WRITER_CLOSURE_SCHEMA
            || decode_hex_32(&self.manifest_sha256) != Some(manifest.digest()?)
            || decode_required_digest(&self.observer_invocation_sha256).is_err()
            || decode_required_digest(&self.worker_writer_roster_sha256).is_err()
            || decode_required_digest(&self.candidate_writer_roster_sha256).is_err()
            || decode_required_digest(&self.finalizer_writer_roster_sha256).is_err()
            || decode_required_digest(&self.generation_writer_roster_sha256).is_err()
            || decode_required_digest(&self.ledger_writer_roster_sha256).is_err()
            || self.worker_writer_handle_count != 0
            || self.candidate_writer_handle_count != 0
            || self.finalizer_writer_handle_count != 0
            || self.generation_writer_handle_count != 0
            || self.ledger_writer_handle_count != 0
            || !self.all_writer_process_identities_exact
        {
            return Err(GenerationSealError(
                "authority_generation_seal_writer_closure_incomplete",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct NativeFinalizerInvocationReadback {
    process_id: u32,
    process_creation_time: u64,
    image_identity: PreSealStableIdentity,
    invocation_sha256: Digest32,
}

/// A process-epoch capability read only from the held current-process handle
/// and the held current image. Re-observing it in one process yields the same
/// digest; a restarted helper necessarily has a different kernel creation
/// time even when Windows later reuses the numeric PID.
pub(super) struct NativeGenerationSealInvocation {
    process: OwnedHandle,
    image: OwnedHandle,
    readback: NativeFinalizerInvocationReadback,
}

impl NativeGenerationSealInvocation {
    pub(super) fn observe_current() -> Result<Self, GenerationSealError> {
        let process_id = unsafe { GetCurrentProcessId() };
        if process_id == 0 {
            return Err(GenerationSealError(
                "authority_generation_seal_invocation_identity_invalid",
            ));
        }
        let raw = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
                0,
                process_id,
            )
        };
        if raw.is_null() || raw == INVALID_HANDLE_VALUE {
            return Err(GenerationSealError(
                "authority_generation_seal_invocation_process_open_failed",
            ));
        }
        let process = unsafe { OwnedHandle::from_raw_handle(raw as RawHandle) };
        let (observed_process_id, process_creation_time, image_path) =
            current_process_kernel_readback(&process)?;
        if observed_process_id != process_id {
            return Err(GenerationSealError(
                "authority_generation_seal_invocation_identity_invalid",
            ));
        }
        let image = open_current_image(&image_path)?;
        require_exact_handle_path(&image, &image_path)?;
        let image_identity =
            capture_preseal_identity_for_target(&image, FinalizerSealTarget::BinaryFile)
                .map_err(GenerationSealError::from)?;
        let invocation_sha256 = finalizer_invocation_digest(
            process_id,
            process_creation_time,
            &image_path,
            &image_identity,
        );
        let value = Self {
            process,
            image,
            readback: NativeFinalizerInvocationReadback {
                process_id,
                process_creation_time,
                image_identity,
                invocation_sha256,
            },
        };
        value.revalidate()?;
        Ok(value)
    }

    pub(super) fn digest(&self) -> Digest32 {
        self.readback.invocation_sha256
    }

    fn revalidate(&self) -> Result<(), GenerationSealError> {
        let (process_id, creation_time, image_path) =
            current_process_kernel_readback(&self.process)?;
        let current_process_id = unsafe { GetCurrentProcessId() };
        let image_identity =
            capture_preseal_identity_for_target(&self.image, FinalizerSealTarget::BinaryFile)
                .map_err(GenerationSealError::from)?;
        require_exact_handle_path(&self.image, &image_path)?;
        if process_id != current_process_id
            || process_id != self.readback.process_id
            || creation_time != self.readback.process_creation_time
            || image_identity != self.readback.image_identity
            || finalizer_invocation_digest(process_id, creation_time, &image_path, &image_identity)
                != self.readback.invocation_sha256
        {
            return Err(GenerationSealError(
                "authority_generation_seal_invocation_identity_drift",
            ));
        }
        Ok(())
    }
}

fn current_process_kernel_readback(
    process: &OwnedHandle,
) -> Result<(u32, u64, PathBuf), GenerationSealError> {
    let process_id = unsafe { GetProcessId(process.as_raw_handle().cast()) };
    let mut creation = FILETIME {
        dwLowDateTime: 0,
        dwHighDateTime: 0,
    };
    let mut exit = creation;
    let mut kernel = creation;
    let mut user = creation;
    if process_id == 0
        || unsafe {
            GetProcessTimes(
                process.as_raw_handle().cast(),
                &mut creation,
                &mut exit,
                &mut kernel,
                &mut user,
            )
        } == 0
    {
        return Err(GenerationSealError(
            "authority_generation_seal_invocation_identity_invalid",
        ));
    }
    let creation_time =
        (u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime);
    if creation_time == 0 {
        return Err(GenerationSealError(
            "authority_generation_seal_invocation_identity_invalid",
        ));
    }
    let mut words = vec![0u16; 32_768];
    let mut length = words.len() as u32;
    if unsafe {
        QueryFullProcessImageNameW(
            process.as_raw_handle().cast(),
            0,
            words.as_mut_ptr(),
            &mut length,
        )
    } == 0
        || length == 0
        || length as usize >= words.len()
    {
        return Err(GenerationSealError(
            "authority_generation_seal_invocation_image_path_invalid",
        ));
    }
    words.truncate(length as usize);
    if words.contains(&0) {
        return Err(GenerationSealError(
            "authority_generation_seal_invocation_image_path_invalid",
        ));
    }
    let image_path = PathBuf::from(OsString::from_wide(&words));
    if !image_path.is_absolute() {
        return Err(GenerationSealError(
            "authority_generation_seal_invocation_image_path_invalid",
        ));
    }
    Ok((process_id, creation_time, image_path))
}

fn open_current_image(path: &Path) -> Result<OwnedHandle, GenerationSealError> {
    let words = wide_null(path);
    let raw = unsafe {
        CreateFileW(
            words.as_ptr(),
            FILE_READ_DATA | FILE_READ_ATTRIBUTES | FILE_READ_EA | SYNCHRONIZE,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT,
            ptr::null_mut(),
        )
    };
    if raw.is_null() || raw == INVALID_HANDLE_VALUE {
        return Err(GenerationSealError(
            "authority_generation_seal_invocation_image_open_failed",
        ));
    }
    Ok(unsafe { OwnedHandle::from_raw_handle(raw as RawHandle) })
}

fn finalizer_invocation_digest(
    process_id: u32,
    creation_time: u64,
    image_path: &Path,
    image_identity: &PreSealStableIdentity,
) -> Digest32 {
    let words = image_path.as_os_str().encode_wide().collect::<Vec<_>>();
    let mut digest = Sha256::new();
    digest.update(FINALIZER_INVOCATION_DOMAIN);
    digest.update(process_id.to_be_bytes());
    digest.update(creation_time.to_be_bytes());
    digest.update((words.len() as u64).to_be_bytes());
    for word in words {
        digest.update(word.to_be_bytes());
    }
    digest.update(image_identity.volume_serial().to_be_bytes());
    digest.update(image_identity.file_id());
    digest.update(image_identity.link_count().to_be_bytes());
    digest.update(image_identity.byte_length().to_be_bytes());
    if let Some(bytes_sha256) = image_identity.bytes_sha256() {
        digest.update(bytes_sha256);
    }
    digest.finalize().into()
}

pub(super) struct PreSealWriterClosureCapability {
    readback: PreSealWriterClosureReadback,
    manifest_sha256: Digest32,
    observer_invocation_sha256: Digest32,
    authorization_sha256: Digest32,
    exclusion_leases: Vec<NativeWriterExclusionLease>,
}

struct NativeWriterExclusionLease {
    role: Option<GenerationSealObjectRole>,
    root: GenerationSealRoot,
    identity: PreSealStableIdentity,
    observed_phase: FinalizerObservedSealPhase,
    handle: OwnedHandle,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum WriterRosterClass {
    Worker,
    Candidate,
    Finalizer,
    Generation,
    Ledger,
}

impl WriterRosterClass {
    const fn tag(self) -> u8 {
        match self {
            Self::Worker => 1,
            Self::Candidate => 2,
            Self::Finalizer => 3,
            Self::Generation => 4,
            Self::Ledger => 5,
        }
    }
}

impl PreSealWriterClosureCapability {
    /// Acquires kernel share-mode leases that exclude content writers and
    /// delete/replace handles for every planned leaf, and excludes child
    /// creation/removal in both generation directories. The leases remain
    /// owned by this capability until the caller drops it after sealing.
    pub(super) fn observe_native(
        layout: &AuthorityLayout,
        manifest: &GenerationSealManifest,
        invocation: &NativeGenerationSealInvocation,
        roots: &NativeGenerationSealRootHandles,
    ) -> Result<Self, GenerationSealError> {
        manifest.validate()?;
        invocation.revalidate()?;
        roots.revalidate(layout, manifest)?;

        let mut inventory = GenerationSealInventory::default();
        for root in [
            GenerationSealRoot::BinaryGeneration,
            GenerationSealRoot::StateGeneration,
            GenerationSealRoot::ActivationsNamespace,
            GenerationSealRoot::WorkerNonceNamespace,
            GenerationSealRoot::CandidateConsumptionNamespace,
        ] {
            inventory.insert(root, enumerate_held_directory(&roots.root(root)?.handle)?);
        }
        inventory.validate_against(manifest)?;

        let mut exclusion_leases = Vec::with_capacity(GENERATION_SEAL_OBJECT_COUNT);
        for root in [
            GenerationSealRoot::BinaryGeneration,
            GenerationSealRoot::StateGeneration,
        ] {
            let authenticated = roots.root(root)?;
            let lease = reopen_directory_writer_exclusion(&authenticated.handle)?;
            require_exact_handle_path(
                &lease,
                roots.expected_path(layout, manifest, root)?.as_path(),
            )?;
            let identity = capture_preseal_identity_for_target(
                &lease,
                FinalizerSealTarget::GenerationDirectory,
            )
            .map_err(GenerationSealError::from)?;
            if identity != authenticated.capability.identity() {
                return Err(GenerationSealError(
                    "authority_generation_seal_writer_exclusion_identity_mismatch",
                ));
            }
            let observed_phase = observe_reopened_seal_phase(
                &lease,
                FinalizerSealTarget::GenerationDirectory,
                &identity,
            )
            .map_err(GenerationSealError::from)?;
            exclusion_leases.push(NativeWriterExclusionLease {
                role: None,
                root,
                identity,
                observed_phase,
                handle: lease,
            });
        }

        for planned in manifest
            .objects
            .iter()
            .filter(|object| object.object_type == SealedObjectKind::File)
        {
            let lease = with_finalizer_security_privilege(|| {
                open_relative_file(
                    &roots.root(planned.root)?.handle,
                    &planned.relative_path,
                    false,
                )
            })
            .map_err(GenerationSealError::from)?;
            let identity = capture_preseal_identity_for_target(&lease, planned.role.target())
                .map_err(GenerationSealError::from)?;
            validate_opened_file(planned, inventory.selected(planned)?, &identity)?;
            let observed_phase =
                observe_reopened_seal_phase(&lease, planned.role.target(), &identity)
                    .map_err(GenerationSealError::from)?;
            exclusion_leases.push(NativeWriterExclusionLease {
                role: Some(planned.role),
                root: planned.root,
                identity,
                observed_phase,
                handle: lease,
            });
        }
        if exclusion_leases.len() != GENERATION_SEAL_OBJECT_COUNT {
            return Err(GenerationSealError(
                "authority_generation_seal_writer_exclusion_incomplete",
            ));
        }

        let manifest_sha256 = manifest.digest()?;
        let worker = writer_exclusion_roster_digest(
            WriterRosterClass::Worker,
            manifest_sha256,
            exclusion_leases
                .iter()
                .filter(|lease| lease.role == Some(GenerationSealObjectRole::WorkerNonce)),
        );
        let candidate = writer_exclusion_roster_digest(
            WriterRosterClass::Candidate,
            manifest_sha256,
            exclusion_leases
                .iter()
                .filter(|lease| lease.role == Some(GenerationSealObjectRole::CandidateConsumption)),
        );
        let finalizer = writer_exclusion_roster_digest(
            WriterRosterClass::Finalizer,
            manifest_sha256,
            exclusion_leases.iter(),
        );
        let generation = writer_exclusion_roster_digest(
            WriterRosterClass::Generation,
            manifest_sha256,
            exclusion_leases.iter().filter(|lease| {
                matches!(
                    lease.root,
                    GenerationSealRoot::BinaryGeneration | GenerationSealRoot::StateGeneration
                )
            }),
        );
        let ledger = writer_exclusion_roster_digest(
            WriterRosterClass::Ledger,
            manifest_sha256,
            exclusion_leases.iter().filter(|lease| {
                matches!(
                    lease.role,
                    Some(GenerationSealObjectRole::Ledger | GenerationSealObjectRole::LedgerAnchor)
                )
            }),
        );
        let readback = PreSealWriterClosureReadback::new(
            manifest,
            invocation.digest(),
            worker,
            candidate,
            finalizer,
            generation,
            ledger,
            0,
            0,
            0,
            0,
            0,
            true,
        )?;
        let authorization_sha256 = writer_exclusion_authorization_digest(
            manifest_sha256,
            invocation.digest(),
            readback.digest(manifest)?,
            finalizer,
        );
        let value = Self {
            readback,
            manifest_sha256,
            observer_invocation_sha256: invocation.digest(),
            authorization_sha256,
            exclusion_leases,
        };
        value.revalidate(manifest, invocation)?;
        Ok(value)
    }

    fn revalidate(
        &self,
        manifest: &GenerationSealManifest,
        invocation: &NativeGenerationSealInvocation,
    ) -> Result<(), GenerationSealError> {
        invocation.revalidate()?;
        if self.manifest_sha256 != manifest.digest()?
            || self.observer_invocation_sha256 != invocation.digest()
            || decode_hex_32(&self.readback.observer_invocation_sha256) != Some(invocation.digest())
            || self.exclusion_leases.len() != GENERATION_SEAL_OBJECT_COUNT
        {
            return Err(GenerationSealError(
                "authority_generation_seal_writer_exclusion_binding_mismatch",
            ));
        }
        self.readback.validate_against(manifest)?;
        for lease in &self.exclusion_leases {
            let target = lease
                .role
                .map(GenerationSealObjectRole::target)
                .unwrap_or(FinalizerSealTarget::GenerationDirectory);
            let identity = capture_preseal_identity_for_target(&lease.handle, target)
                .map_err(GenerationSealError::from)?;
            if identity != lease.identity
                || observe_reopened_seal_phase(&lease.handle, target, &identity)
                    .map_err(GenerationSealError::from)?
                    != lease.observed_phase
            {
                return Err(GenerationSealError(
                    "authority_generation_seal_writer_exclusion_drift",
                ));
            }
        }
        let finalizer = writer_exclusion_roster_digest(
            WriterRosterClass::Finalizer,
            self.manifest_sha256,
            self.exclusion_leases.iter(),
        );
        if self.authorization_sha256
            != writer_exclusion_authorization_digest(
                self.manifest_sha256,
                invocation.digest(),
                self.readback.digest(manifest)?,
                finalizer,
            )
        {
            return Err(GenerationSealError(
                "authority_generation_seal_writer_exclusion_authorization_invalid",
            ));
        }
        Ok(())
    }

    fn checkpoint_writer_authorization(
        &self,
        manifest: &GenerationSealManifest,
        invocation: &NativeGenerationSealInvocation,
    ) -> Result<CheckpointWriterAuthorization, GenerationSealError> {
        self.revalidate(manifest, invocation)?;
        Ok(CheckpointWriterAuthorization {
            invocation_sha256: invocation.digest(),
            writer_exclusion_sha256: self.authorization_sha256,
        })
    }
}

fn writer_exclusion_authorization_digest(
    manifest_sha256: Digest32,
    invocation_sha256: Digest32,
    readback_sha256: Digest32,
    all_leases_sha256: Digest32,
) -> Digest32 {
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-generation-seal-writer-exclusion-authorization-v1\0");
    digest.update(manifest_sha256);
    digest.update(invocation_sha256);
    digest.update(readback_sha256);
    digest.update(all_leases_sha256);
    digest.finalize().into()
}

fn writer_exclusion_roster_digest<'a>(
    class: WriterRosterClass,
    manifest_sha256: Digest32,
    leases: impl Iterator<Item = &'a NativeWriterExclusionLease>,
) -> Digest32 {
    let mut digest = Sha256::new();
    digest.update(WRITER_EXCLUSION_ROSTER_DOMAIN);
    digest.update([class.tag()]);
    digest.update(manifest_sha256);
    let records = leases.collect::<Vec<_>>();
    digest.update((records.len() as u32).to_be_bytes());
    for lease in records {
        digest.update([lease.root as u8]);
        digest.update(
            lease
                .role
                .map(|role| role_index(role) as u64)
                .unwrap_or(u64::MAX)
                .to_be_bytes(),
        );
        digest.update(lease.identity.volume_serial().to_be_bytes());
        digest.update(lease.identity.file_id());
        digest.update(lease.identity.link_count().to_be_bytes());
        digest.update(lease.identity.byte_length().to_be_bytes());
        if let Some(bytes_sha256) = lease.identity.bytes_sha256() {
            digest.update(bytes_sha256);
        }
        digest.update([match lease.observed_phase {
            FinalizerObservedSealPhase::ExactStaging => 1,
            FinalizerObservedSealPhase::ExactSealed => 2,
        }]);
    }
    digest.update(0u32.to_be_bytes());
    digest.finalize().into()
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct GenerationSealStableIdentityRecord {
    volume_serial: u64,
    file_id: String,
    object_type: SealedObjectKind,
    link_count: u32,
    byte_length: u64,
    bytes_sha256: Option<String>,
}

impl GenerationSealStableIdentityRecord {
    fn from_identity(identity: &PreSealStableIdentity) -> Self {
        Self {
            volume_serial: identity.volume_serial(),
            file_id: hex_lower(identity.file_id()),
            object_type: match identity.object_type() {
                FinalizerSealedObjectType::File => SealedObjectKind::File,
                FinalizerSealedObjectType::Directory => SealedObjectKind::Directory,
            },
            link_count: identity.link_count(),
            byte_length: identity.byte_length(),
            bytes_sha256: identity.bytes_sha256().map(|value| hex_lower(value)),
        }
    }

    fn to_identity(&self) -> Result<PreSealStableIdentity, GenerationSealError> {
        let file_id = decode_hex_16(&self.file_id).ok_or(GenerationSealError(
            "authority_generation_seal_progress_identity_invalid",
        ))?;
        match self.object_type {
            SealedObjectKind::File => {
                PreSealStableIdentity::new_file(
                    self.volume_serial,
                    file_id,
                    self.link_count,
                    self.byte_length,
                    self.bytes_sha256.as_deref().and_then(decode_hex_32).ok_or(
                        GenerationSealError("authority_generation_seal_progress_identity_invalid"),
                    )?,
                )
            }
            SealedObjectKind::Directory => PreSealStableIdentity::new_directory(
                self.volume_serial,
                file_id,
                self.link_count,
                self.byte_length,
            ),
        }
        .map_err(GenerationSealError::from)
    }

    fn validate_against(
        &self,
        planned: &GenerationSealObjectPlan,
    ) -> Result<PreSealStableIdentity, GenerationSealError> {
        let identity = self.to_identity()?;
        if self.object_type != planned.object_type
            || identity.object_type() != planned.role.target().object_type()
            || identity.link_count() != 1
            || (planned.object_type == SealedObjectKind::File
                && (Some(identity.byte_length()) != planned.expected_byte_length
                    || identity.bytes_sha256().map(|value| hex_lower(value))
                        != planned.expected_bytes_sha256))
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_identity_mismatch",
            ));
        }
        Ok(identity)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct GenerationSealObjectIntent {
    role: GenerationSealObjectRole,
    root: GenerationSealRoot,
    relative_path: String,
    object_index: usize,
    identity: GenerationSealStableIdentityRecord,
    staging_security_sha256: String,
    final_security_sha256: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct CheckpointWriterAuthorization {
    invocation_sha256: Digest32,
    writer_exclusion_sha256: Digest32,
}

impl CheckpointWriterAuthorization {
    #[cfg(test)]
    fn exact_test_fixture(invocation_seed: u8, exclusion_seed: u8) -> Self {
        Self {
            invocation_sha256: [invocation_seed; 32],
            writer_exclusion_sha256: [exclusion_seed; 32],
        }
    }

    fn validate(self) -> Result<(), GenerationSealError> {
        if is_zero_digest(&self.invocation_sha256) || is_zero_digest(&self.writer_exclusion_sha256)
        {
            return Err(GenerationSealError(
                "authority_generation_seal_checkpoint_writer_invalid",
            ));
        }
        Ok(())
    }
}

impl GenerationSealObjectIntent {
    fn new(
        manifest: &GenerationSealManifest,
        object_index: usize,
        identity: &PreSealStableIdentity,
    ) -> Result<Self, GenerationSealError> {
        let planned = manifest
            .objects
            .get(object_index)
            .ok_or(GenerationSealError(
                "authority_generation_seal_progress_object_index_invalid",
            ))?;
        let value = Self {
            role: planned.role,
            root: planned.root,
            relative_path: planned.relative_path.clone(),
            object_index,
            identity: GenerationSealStableIdentityRecord::from_identity(identity),
            staging_security_sha256: planned.staging_security_sha256.clone(),
            final_security_sha256: planned.final_security_sha256.clone(),
        };
        value.validate_against(manifest, object_index)?;
        Ok(value)
    }

    fn validate_against(
        &self,
        manifest: &GenerationSealManifest,
        expected_index: usize,
    ) -> Result<PreSealStableIdentity, GenerationSealError> {
        let planned = manifest
            .objects
            .get(expected_index)
            .ok_or(GenerationSealError(
                "authority_generation_seal_progress_object_index_invalid",
            ))?;
        if self.object_index != expected_index
            || self.role != planned.role
            || self.root != planned.root
            || self.relative_path != planned.relative_path
            || self.staging_security_sha256 != planned.staging_security_sha256
            || self.final_security_sha256 != planned.final_security_sha256
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_intent_mismatch",
            ));
        }
        self.identity.validate_against(planned)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct GenerationSealProgressCheckpoint {
    schema: String,
    manifest_sha256: String,
    writer_closure_readback_sha256: String,
    writer_closure_readback: PreSealWriterClosureReadback,
    writer_invocation_sha256: String,
    writer_exclusion_capability_sha256: String,
    sequence: u32,
    previous_checkpoint_sha256: Option<String>,
    completed_objects: Vec<GenerationSealedObjectReceipt>,
    pending_intent: Option<GenerationSealObjectIntent>,
    terminal: bool,
}

impl GenerationSealProgressCheckpoint {
    fn genesis(
        manifest: &GenerationSealManifest,
        writer_closure: &PreSealWriterClosureReadback,
        writer: CheckpointWriterAuthorization,
    ) -> Result<Self, GenerationSealError> {
        writer.validate()?;
        let value = Self {
            schema: GENERATION_SEAL_PROGRESS_SCHEMA.to_string(),
            manifest_sha256: hex_lower(&manifest.digest()?),
            writer_closure_readback_sha256: hex_lower(&writer_closure.digest(manifest)?),
            writer_closure_readback: writer_closure.clone(),
            writer_invocation_sha256: hex_lower(&writer.invocation_sha256),
            writer_exclusion_capability_sha256: hex_lower(&writer.writer_exclusion_sha256),
            sequence: 0,
            previous_checkpoint_sha256: None,
            completed_objects: Vec::new(),
            pending_intent: None,
            terminal: false,
        };
        value.validate_against(manifest, writer_closure)?;
        Ok(value)
    }

    fn with_intent(
        previous: &Self,
        manifest: &GenerationSealManifest,
        writer_closure: &PreSealWriterClosureReadback,
        identity: &PreSealStableIdentity,
        writer: CheckpointWriterAuthorization,
    ) -> Result<Self, GenerationSealError> {
        writer.validate()?;
        previous.validate_against(manifest, writer_closure)?;
        if previous.terminal
            || previous.pending_intent.is_some()
            || previous.completed_objects.len() >= manifest.objects.len()
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_transition_invalid",
            ));
        }
        let object_index = previous.completed_objects.len();
        let value = Self {
            schema: previous.schema.clone(),
            manifest_sha256: previous.manifest_sha256.clone(),
            writer_closure_readback_sha256: previous.writer_closure_readback_sha256.clone(),
            writer_closure_readback: previous.writer_closure_readback.clone(),
            writer_invocation_sha256: hex_lower(&writer.invocation_sha256),
            writer_exclusion_capability_sha256: hex_lower(&writer.writer_exclusion_sha256),
            sequence: previous.sequence.checked_add(1).ok_or(GenerationSealError(
                "authority_generation_seal_progress_sequence_invalid",
            ))?,
            previous_checkpoint_sha256: Some(hex_lower(
                &previous.digest(manifest, writer_closure)?,
            )),
            completed_objects: previous.completed_objects.clone(),
            pending_intent: Some(GenerationSealObjectIntent::new(
                manifest,
                object_index,
                identity,
            )?),
            terminal: false,
        };
        value.validate_against(manifest, writer_closure)?;
        Ok(value)
    }

    fn with_completion(
        previous: &Self,
        manifest: &GenerationSealManifest,
        writer_closure: &PreSealWriterClosureReadback,
        receipt: GenerationSealedObjectReceipt,
        writer: CheckpointWriterAuthorization,
    ) -> Result<Self, GenerationSealError> {
        writer.validate()?;
        previous.validate_against(manifest, writer_closure)?;
        let intent = previous.pending_intent.as_ref().ok_or(GenerationSealError(
            "authority_generation_seal_progress_intent_missing",
        ))?;
        let object_index = previous.completed_objects.len();
        let expected_identity = intent.validate_against(manifest, object_index)?;
        receipt.validate_against(&manifest.objects[object_index])?;
        if receipt.stable_identity()? != expected_identity {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_completion_identity_mismatch",
            ));
        }
        let mut completed_objects = previous.completed_objects.clone();
        completed_objects.push(receipt);
        let value = Self {
            schema: previous.schema.clone(),
            manifest_sha256: previous.manifest_sha256.clone(),
            writer_closure_readback_sha256: previous.writer_closure_readback_sha256.clone(),
            writer_closure_readback: previous.writer_closure_readback.clone(),
            writer_invocation_sha256: hex_lower(&writer.invocation_sha256),
            writer_exclusion_capability_sha256: hex_lower(&writer.writer_exclusion_sha256),
            sequence: previous.sequence.checked_add(1).ok_or(GenerationSealError(
                "authority_generation_seal_progress_sequence_invalid",
            ))?,
            previous_checkpoint_sha256: Some(hex_lower(
                &previous.digest(manifest, writer_closure)?,
            )),
            completed_objects,
            pending_intent: None,
            terminal: false,
        };
        value.validate_against(manifest, writer_closure)?;
        Ok(value)
    }

    fn into_terminal(
        previous: &Self,
        manifest: &GenerationSealManifest,
        writer_closure: &PreSealWriterClosureReadback,
        writer: CheckpointWriterAuthorization,
    ) -> Result<Self, GenerationSealError> {
        writer.validate()?;
        previous.validate_against(manifest, writer_closure)?;
        if previous.terminal
            || previous.pending_intent.is_some()
            || previous.completed_objects.len() != manifest.objects.len()
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_terminal_incomplete",
            ));
        }
        let value = Self {
            schema: previous.schema.clone(),
            manifest_sha256: previous.manifest_sha256.clone(),
            writer_closure_readback_sha256: previous.writer_closure_readback_sha256.clone(),
            writer_closure_readback: previous.writer_closure_readback.clone(),
            writer_invocation_sha256: hex_lower(&writer.invocation_sha256),
            writer_exclusion_capability_sha256: hex_lower(&writer.writer_exclusion_sha256),
            sequence: previous.sequence.checked_add(1).ok_or(GenerationSealError(
                "authority_generation_seal_progress_sequence_invalid",
            ))?,
            previous_checkpoint_sha256: Some(hex_lower(
                &previous.digest(manifest, writer_closure)?,
            )),
            completed_objects: previous.completed_objects.clone(),
            pending_intent: None,
            terminal: true,
        };
        value.validate_against(manifest, writer_closure)?;
        Ok(value)
    }

    fn parse_canonical(
        bytes: &[u8],
        manifest: &GenerationSealManifest,
        writer_closure: &PreSealWriterClosureReadback,
    ) -> Result<Self, GenerationSealError> {
        if bytes.is_empty() || bytes.len() > MAX_SMALL_OBJECT_BYTES as usize {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_size_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes).map_err(|_| {
            GenerationSealError("authority_generation_seal_progress_serialization_invalid")
        })?;
        value.validate_against(manifest, writer_closure)?;
        if value.canonical_bytes(manifest, writer_closure)? != bytes {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_not_canonical",
            ));
        }
        Ok(value)
    }

    fn canonical_bytes(
        &self,
        manifest: &GenerationSealManifest,
        writer_closure: &PreSealWriterClosureReadback,
    ) -> Result<Vec<u8>, GenerationSealError> {
        self.validate_against(manifest, writer_closure)?;
        let bytes = serde_json::to_vec(self).map_err(|_| {
            GenerationSealError("authority_generation_seal_progress_serialization_failed")
        })?;
        if bytes.len() > MAX_SMALL_OBJECT_BYTES as usize {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_size_invalid",
            ));
        }
        Ok(bytes)
    }

    fn digest(
        &self,
        manifest: &GenerationSealManifest,
        writer_closure: &PreSealWriterClosureReadback,
    ) -> Result<Digest32, GenerationSealError> {
        Ok(domain_digest(
            GENERATION_SEAL_PROGRESS_DOMAIN,
            &self.canonical_bytes(manifest, writer_closure)?,
        ))
    }

    fn validate_against(
        &self,
        manifest: &GenerationSealManifest,
        writer_closure: &PreSealWriterClosureReadback,
    ) -> Result<(), GenerationSealError> {
        manifest.validate()?;
        writer_closure.validate_against(manifest)?;
        if self.schema != GENERATION_SEAL_PROGRESS_SCHEMA
            || decode_hex_32(&self.manifest_sha256) != Some(manifest.digest()?)
            || decode_hex_32(&self.writer_closure_readback_sha256)
                != Some(writer_closure.digest(manifest)?)
            || self.writer_closure_readback != *writer_closure
            || decode_required_digest(&self.writer_invocation_sha256).is_err()
            || decode_required_digest(&self.writer_exclusion_capability_sha256).is_err()
            || (self.sequence == 0
                && self.writer_invocation_sha256 != writer_closure.observer_invocation_sha256)
            || self.completed_objects.len() > manifest.objects.len()
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_binding_mismatch",
            ));
        }
        let mut object_identities = BTreeSet::new();
        for (index, receipt) in self.completed_objects.iter().enumerate() {
            receipt.validate_against(&manifest.objects[index])?;
            if !object_identities.insert((receipt.volume_serial, receipt.file_id.clone())) {
                return Err(GenerationSealError(
                    "authority_generation_seal_progress_identity_collision",
                ));
            }
        }
        if let Some(intent) = &self.pending_intent {
            let identity = intent.validate_against(manifest, self.completed_objects.len())?;
            if !object_identities.insert((identity.volume_serial(), hex_lower(identity.file_id())))
            {
                return Err(GenerationSealError(
                    "authority_generation_seal_progress_identity_collision",
                ));
            }
        }
        let expected_sequence = if self.terminal {
            if self.completed_objects.len() != manifest.objects.len()
                || self.pending_intent.is_some()
            {
                return Err(GenerationSealError(
                    "authority_generation_seal_progress_terminal_incomplete",
                ));
            }
            GENERATION_SEAL_TERMINAL_SEQUENCE
        } else {
            let completed = u32::try_from(self.completed_objects.len()).map_err(|_| {
                GenerationSealError("authority_generation_seal_progress_sequence_invalid")
            })?;
            completed * 2 + u32::from(self.pending_intent.is_some())
        };
        if self.sequence != expected_sequence
            || (self.sequence == 0) != self.previous_checkpoint_sha256.is_none()
            || self
                .previous_checkpoint_sha256
                .as_deref()
                .is_some_and(|value| decode_required_digest(value).is_err())
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_sequence_invalid",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct GenerationSealDurableReadback {
    relative_name: String,
    checkpoint_bytes: Vec<u8>,
    checkpoint_sha256: Digest32,
    store_root_identity_sha256: Digest32,
    volume_serial: u64,
    file_id: [u8; 16],
    link_count: u32,
    byte_length: u64,
    bytes_sha256: Digest32,
    descriptor_sha256: Digest32,
    create_new_no_replace: bool,
    write_through: bool,
    file_flushed: bool,
    parent_directory_flushed: bool,
    write_handle_closed_before_readback: bool,
    read_only_reopen_verified: bool,
    manifest_namespace_exhaustive: bool,
    no_case_aliases_or_reparse_points: bool,
}

impl GenerationSealDurableReadback {
    #[allow(clippy::too_many_arguments)]
    fn new(
        relative_name: String,
        checkpoint_bytes: Vec<u8>,
        checkpoint_sha256: Digest32,
        store_root_identity_sha256: Digest32,
        volume_serial: u64,
        file_id: [u8; 16],
        link_count: u32,
        byte_length: u64,
        bytes_sha256: Digest32,
        descriptor_sha256: Digest32,
        create_new_no_replace: bool,
        write_through: bool,
        file_flushed: bool,
        parent_directory_flushed: bool,
        write_handle_closed_before_readback: bool,
        read_only_reopen_verified: bool,
        manifest_namespace_exhaustive: bool,
        no_case_aliases_or_reparse_points: bool,
    ) -> Self {
        Self {
            relative_name,
            checkpoint_bytes,
            checkpoint_sha256,
            store_root_identity_sha256,
            volume_serial,
            file_id,
            link_count,
            byte_length,
            bytes_sha256,
            descriptor_sha256,
            create_new_no_replace,
            write_through,
            file_flushed,
            parent_directory_flushed,
            write_handle_closed_before_readback,
            read_only_reopen_verified,
            manifest_namespace_exhaustive,
            no_case_aliases_or_reparse_points,
        }
    }
}

/// The concrete store must implement create-new immutable files and return the
/// complete exact chain for this manifest.  The sealing engine never accepts a
/// boolean "saved" result; it independently checks bytes, identity, security,
/// flush/readback claims, sequence continuity, aliases, and forks.
trait GenerationSealProgressStore {
    fn load_exact_chain(
        &mut self,
        manifest_sha256: Digest32,
    ) -> Result<Vec<GenerationSealDurableReadback>, GenerationSealError>;

    fn compare_and_swap_create_new(
        &mut self,
        manifest_sha256: Digest32,
        expected_previous_checkpoint_sha256: Option<Digest32>,
        checkpoint_bytes: &[u8],
    ) -> Result<GenerationSealDurableReadback, GenerationSealError>;
}

/// Production sealing accepts only this opaque capability, never an arbitrary
/// trait implementation. Its constructor authenticates the exact
/// plan-derived per-transaction root and installs the concrete Windows store.
pub(super) struct AuthenticatedNativeGenerationSealProgressStore {
    inner: Box<dyn GenerationSealProgressStore>,
}

struct NativeGenerationSealProgressStore {
    root: OwnedHandle,
    expected_root_path: PathBuf,
    expected_root_capability: FinalizerRootCapabilityReadback,
    authenticated_root_sha256: Digest32,
    manifest: GenerationSealManifest,
    manifest_sha256: Digest32,
}

impl AuthenticatedNativeGenerationSealProgressStore {
    pub(super) const fn required_root_access() -> u32 {
        PROGRESS_ROOT_ACCESS
    }

    pub(super) fn expected_root_path(
        layout: &AuthorityLayout,
        manifest: &GenerationSealManifest,
    ) -> Result<PathBuf, GenerationSealError> {
        manifest.validate()?;
        Ok(layout
            .state_root()
            .join("finalizer-commits")
            .join(hex_lower(&manifest.binding()?.transaction_sha256())))
    }

    pub(super) fn authenticate(
        layout: &AuthorityLayout,
        manifest: &GenerationSealManifest,
        commit_binding: FinalizerCommitBinding,
        root_capability: AuthenticatedFinalizerGenerationProgressRoot,
    ) -> Result<Self, GenerationSealError> {
        manifest.validate()?;
        let manifest_binding = manifest.binding()?;
        let commit_seal_binding = GenerationSealBinding::from_commit_binding(commit_binding)?;
        let (root, issued_canonical_path, issued_root_identity_sha256) =
            root_capability.into_parts();
        let expected_root_path = Self::expected_root_path(layout, manifest)?;
        if manifest_binding != commit_seal_binding
            || issued_root_identity_sha256
                != commit_binding.final_commit_store_root_identity_sha256()
            || issued_root_identity_sha256
                != manifest_binding.final_commit_store_root_identity_sha256()
            || !issued_canonical_path
                .eq_ignore_ascii_case(expected_root_path.to_string_lossy().as_ref())
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_root_binding_mismatch",
            ));
        }
        require_exact_handle_path(&root, &expected_root_path)?;
        let expected_root_capability = authenticate_finalizer_root_capability(
            &root,
            FinalizerRootCapabilityKind::GenerationSealProgressRoot,
        )
        .map_err(GenerationSealError::from)?;
        if expected_root_capability.security_phase()
            != FinalizerRootSecurityPhase::ExactProgressNamespace
            || expected_root_capability.granted_access() != PROGRESS_ROOT_ACCESS
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_root_capability_invalid",
            ));
        }
        let manifest_sha256 = manifest.digest()?;
        let authenticated_root_sha256 = issued_root_identity_sha256;
        let store = NativeGenerationSealProgressStore {
            root,
            expected_root_path,
            expected_root_capability,
            authenticated_root_sha256,
            manifest: manifest.clone(),
            manifest_sha256,
        };
        store.revalidate_root()?;
        Ok(Self {
            inner: Box::new(store),
        })
    }
}

impl NativeGenerationSealProgressStore {
    fn revalidate_root(&self) -> Result<(), GenerationSealError> {
        require_exact_handle_path(&self.root, &self.expected_root_path)?;
        let current = authenticate_finalizer_root_capability(
            &self.root,
            FinalizerRootCapabilityKind::GenerationSealProgressRoot,
        )
        .map_err(GenerationSealError::from)?;
        if current != self.expected_root_capability
            || current.security_phase() != FinalizerRootSecurityPhase::ExactProgressNamespace
            || current.granted_access() != PROGRESS_ROOT_ACCESS
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_root_capability_drift",
            ));
        }
        Ok(())
    }

    fn progress_namespace_inventory(
        &self,
        manifest_sha256: Digest32,
    ) -> Result<ProgressNamespaceInventory, GenerationSealError> {
        self.require_manifest(manifest_sha256)?;
        self.revalidate_root()?;
        let mut selected = ProgressNamespaceInventory::default();
        for entry in enumerate_held_directory(&self.root)? {
            let Some(kind) =
                parse_generation_progress_namespace_name(&entry.relative_name, manifest_sha256)?
            else {
                if super::finalizer_commit_store_windows::is_typed_finalizer_commit_namespace_name(
                    &entry.relative_name,
                ) {
                    continue;
                }
                return Err(GenerationSealError(
                    "authority_generation_seal_transaction_namespace_unknown",
                ));
            };
            if entry.is_directory || entry.is_reparse || entry.file_id.iter().all(|byte| *byte == 0)
            {
                return Err(GenerationSealError(
                    "authority_generation_seal_progress_namespace_invalid",
                ));
            }
            let replaced = match kind {
                ProgressNamespaceName::Published(sequence) => {
                    if entry.byte_length == 0 {
                        return Err(GenerationSealError(
                            "authority_generation_seal_progress_namespace_invalid",
                        ));
                    }
                    selected.published.insert(sequence, entry)
                }
                ProgressNamespaceName::Publishing(sequence) => {
                    selected.publishing.insert(sequence, entry)
                }
            };
            if replaced.is_some() {
                return Err(GenerationSealError(
                    "authority_generation_seal_progress_namespace_invalid",
                ));
            }
        }
        if selected
            .published
            .keys()
            .enumerate()
            .any(|(index, sequence)| *sequence as usize != index)
            || selected.publishing.len() > 1
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_chain_gap_or_fork",
            ));
        }
        if let Some(sequence) = selected.publishing.keys().next().copied() {
            let published_len = selected.published.len();
            if sequence as usize > published_len
                || (sequence as usize != published_len
                    && !selected.published.contains_key(&sequence))
            {
                return Err(GenerationSealError(
                    "authority_generation_seal_progress_chain_gap_or_fork",
                ));
            }
        }
        self.revalidate_root()?;
        Ok(selected)
    }

    fn require_manifest(&self, manifest_sha256: Digest32) -> Result<(), GenerationSealError> {
        if manifest_sha256 != self.manifest_sha256 || is_zero_digest(&manifest_sha256) {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_store_binding_mismatch",
            ));
        }
        Ok(())
    }

    fn open_authenticated_progress_file(
        &self,
        relative_name: &str,
        kind: ProgressFileCapabilityKind,
        create_descriptor: Option<&FinalizerStagingSecurityDescriptor>,
    ) -> Result<Option<(OwnedHandle, ProgressFileCapabilityReadback)>, GenerationSealError> {
        self.revalidate_root()?;
        let handle = with_finalizer_security_privilege(|| {
            open_progress_file(
                &self.root,
                relative_name,
                kind.open_mode(),
                create_descriptor,
            )
        })
        .map_err(GenerationSealError::from)?;
        let Some(handle) = handle else {
            self.revalidate_root()?;
            return Ok(None);
        };
        let readback = authenticate_progress_file_handle(
            &handle,
            &self.expected_root_path.join(relative_name),
            kind,
        )?;
        self.revalidate_root()?;
        Ok(Some((handle, readback)))
    }

    fn recover_progress_publications(
        &self,
        manifest_sha256: Digest32,
    ) -> Result<(), GenerationSealError> {
        loop {
            let inventory = self.progress_namespace_inventory(manifest_sha256)?;
            let Some((&sequence, entry)) = inventory.publishing.iter().next() else {
                return Ok(());
            };
            let private_name = generation_progress_publishing_name(&manifest_sha256, sequence);
            let (inspection, initial) = self
                .open_authenticated_progress_file(
                    &private_name,
                    ProgressFileCapabilityKind::PublishingInspection,
                    None,
                )?
                .ok_or(GenerationSealError(
                    "authority_generation_seal_progress_publishing_missing",
                ))?;
            if initial.identity.file_id != entry.file_id
                || initial.identity.byte_length != entry.byte_length
            {
                return Err(GenerationSealError(
                    "authority_generation_seal_progress_publishing_identity_drift",
                ));
            }
            let (bytes, read_identity) = read_progress_file_bounded(&inspection)?;
            if read_identity != initial.identity {
                return Err(GenerationSealError(
                    "authority_generation_seal_progress_publishing_identity_drift",
                ));
            }
            let confirmed = authenticate_progress_file_handle(
                &inspection,
                &self.expected_root_path.join(&private_name),
                ProgressFileCapabilityKind::PublishingInspection,
            )?;
            if confirmed != initial {
                return Err(GenerationSealError(
                    "authority_generation_seal_progress_publishing_capability_drift",
                ));
            }
            let action = classify_progress_publishing_recovery(
                initial.security_phase,
                &bytes,
                &self.manifest,
                sequence,
            )?;
            drop(inspection);
            match action {
                ProgressPublishingRecoveryAction::DeleteStagingAndRetry => {
                    if inventory.published.contains_key(&sequence)
                        || sequence as usize != inventory.published.len()
                    {
                        return Err(GenerationSealError(
                            "authority_generation_seal_progress_staging_collision",
                        ));
                    }
                    let (staging, staging_capability) = self
                        .open_authenticated_progress_file(
                            &private_name,
                            ProgressFileCapabilityKind::PublishingStagingRecovery,
                            None,
                        )?
                        .ok_or(GenerationSealError(
                            "authority_generation_seal_progress_publishing_missing",
                        ))?;
                    let (current_bytes, current_identity) = read_progress_file_bounded(&staging)?;
                    if !same_progress_file_object(&staging_capability.identity, &initial.identity)
                        || current_identity != staging_capability.identity
                        || current_bytes != bytes
                    {
                        return Err(GenerationSealError(
                            "authority_generation_seal_progress_publishing_identity_drift",
                        ));
                    }
                    let confirmed = authenticate_progress_file_handle(
                        &staging,
                        &self.expected_root_path.join(&private_name),
                        ProgressFileCapabilityKind::PublishingStagingRecovery,
                    )?;
                    if confirmed != staging_capability {
                        return Err(GenerationSealError(
                            "authority_generation_seal_progress_publishing_capability_drift",
                        ));
                    }
                    delete_progress_publishing(&self.root, staging)?;
                }
                ProgressPublishingRecoveryAction::RollForwardSealed => {
                    let (sealed, sealed_capability) = self
                        .open_authenticated_progress_file(
                            &private_name,
                            ProgressFileCapabilityKind::PublishingSealedRecovery,
                            None,
                        )?
                        .ok_or(GenerationSealError(
                            "authority_generation_seal_progress_publishing_missing",
                        ))?;
                    let (current_bytes, current_identity) = read_progress_file_bounded(&sealed)?;
                    if !same_progress_file_object(&sealed_capability.identity, &initial.identity)
                        || current_identity != sealed_capability.identity
                        || current_bytes != bytes
                    {
                        return Err(GenerationSealError(
                            "authority_generation_seal_progress_publishing_identity_drift",
                        ));
                    }
                    self.finish_progress_publication(
                        manifest_sha256,
                        sequence,
                        &private_name,
                        sealed,
                        sealed_capability,
                        &bytes,
                    )?;
                }
            }
        }
    }

    fn readback_existing(
        &self,
        manifest_sha256: Digest32,
        sequence: u32,
        expected_bytes: Option<&[u8]>,
    ) -> Result<GenerationSealDurableReadback, GenerationSealError> {
        self.require_manifest(manifest_sha256)?;
        self.recover_final_progress_security(manifest_sha256, sequence)?;
        let relative_name = generation_progress_relative_name(&manifest_sha256, sequence);
        let (read_only, capability) = self
            .open_authenticated_progress_file(
                &relative_name,
                ProgressFileCapabilityKind::PublishedReadOnly,
                None,
            )?
            .ok_or(GenerationSealError(
                "authority_generation_seal_progress_open_failed",
            ))?;
        let (bytes, identity) = read_progress_file_stable(&read_only)?;
        if !progress_identity_matches_preseal(&capability.identity, &identity) {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_identity_drift",
            ));
        }
        validate_exact_progress_checkpoint(&bytes, &self.manifest, sequence)?;
        if expected_bytes.is_some_and(|expected| expected != bytes) {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_create_collision",
            ));
        }
        let descriptor_sha256 = expected_publication_security_sha256(
            FinalizerPublicationSecurityPhase::PublishedImmutable,
        )
        .map_err(GenerationSealError::from)?;
        verify_reopened_sealed_object(
            &read_only,
            FinalizerSealTarget::ImmutableStateFile,
            &identity,
            &descriptor_sha256,
        )
        .map_err(GenerationSealError::from)?;
        let confirmed = authenticate_progress_file_handle(
            &read_only,
            &self.expected_root_path.join(&relative_name),
            ProgressFileCapabilityKind::PublishedReadOnly,
        )?;
        if confirmed != capability {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_published_capability_drift",
            ));
        }
        Ok(GenerationSealDurableReadback::new(
            relative_name,
            bytes.clone(),
            domain_digest(GENERATION_SEAL_PROGRESS_DOMAIN, &bytes),
            self.authenticated_root_sha256,
            identity.volume_serial(),
            *identity.file_id(),
            identity.link_count(),
            identity.byte_length(),
            sha256_bytes(&bytes),
            descriptor_sha256,
            true,
            true,
            true,
            true,
            true,
            true,
            true,
            true,
        ))
    }

    fn recover_final_progress_security(
        &self,
        manifest_sha256: Digest32,
        sequence: u32,
    ) -> Result<(), GenerationSealError> {
        let final_name = generation_progress_relative_name(&manifest_sha256, sequence);
        let final_path = self.expected_root_path.join(&final_name);
        let Some((inspection, inspection_capability)) = self.open_authenticated_progress_file(
            &final_name,
            ProgressFileCapabilityKind::PublishingInspection,
            None,
        )?
        else {
            return Ok(());
        };
        let (inspection_bytes, inspection_identity) = read_progress_file_bounded(&inspection)?;
        if inspection_identity != inspection_capability.identity {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_published_identity_drift",
            ));
        }
        let recovery_action = classify_progress_final_recovery(
            inspection_capability.security_phase,
            &inspection_bytes,
            &self.manifest,
            sequence,
        )?;
        let confirmed = authenticate_progress_file_handle(
            &inspection,
            &final_path,
            ProgressFileCapabilityKind::PublishingInspection,
        )?;
        if confirmed != inspection_capability {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_published_capability_drift",
            ));
        }
        match recovery_action {
            ProgressFinalRecoveryAction::AlreadyImmutable => return Ok(()),
            ProgressFinalRecoveryAction::TightenPrivateSealed => {}
        }
        drop(inspection);

        let (sealed, sealed_capability) = self
            .open_authenticated_progress_file(
                &final_name,
                ProgressFileCapabilityKind::PublishingSealedRecovery,
                None,
            )?
            .ok_or(GenerationSealError(
                "authority_generation_seal_progress_published_private_missing",
            ))?;
        let (sealed_bytes, sealed_identity) = read_progress_file_bounded(&sealed)?;
        if sealed_capability.identity != inspection_identity
            || sealed_identity != inspection_identity
            || sealed_bytes != inspection_bytes
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_published_identity_drift",
            ));
        }
        validate_exact_progress_checkpoint(&sealed_bytes, &self.manifest, sequence)?;
        let confirmed = authenticate_progress_file_handle(
            &sealed,
            &final_path,
            ProgressFileCapabilityKind::PublishingSealedRecovery,
        )?;
        if confirmed != sealed_capability {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_published_capability_drift",
            ));
        }
        transition_publication_security(
            &sealed,
            FinalizerPublicationSecurityPhase::PrivateSealed,
            FinalizerPublicationSecurityPhase::PublishedImmutable,
        )
        .map_err(GenerationSealError::from)?;
        flush_progress_handle(
            &sealed,
            "authority_generation_seal_progress_published_private_tighten_flush_failed",
        )?;
        let tightened = authenticate_progress_file_handle(
            &sealed,
            &final_path,
            ProgressFileCapabilityKind::PublishedTightening,
        )?;
        let (tightened_bytes, tightened_identity) = read_progress_file_bounded(&sealed)?;
        if tightened.identity != inspection_identity
            || tightened_identity != inspection_identity
            || tightened_bytes != inspection_bytes
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_published_identity_drift",
            ));
        }
        drop(sealed);
        flush_progress_handle(
            &self.root,
            "authority_generation_seal_progress_parent_flush_failed",
        )?;
        Ok(())
    }

    fn create_progress_file(
        &self,
        manifest_sha256: Digest32,
        sequence: u32,
        checkpoint_bytes: &[u8],
    ) -> Result<GenerationSealDurableReadback, GenerationSealError> {
        let private_name = generation_progress_publishing_name(&manifest_sha256, sequence);
        let descriptor =
            FinalizerStagingSecurityDescriptor::for_target(FinalizerSealTarget::ImmutableStateFile)
                .map_err(GenerationSealError::from)?;
        let created = self.open_authenticated_progress_file(
            &private_name,
            ProgressFileCapabilityKind::PublishingCreate,
            Some(&descriptor),
        )?;
        let (created, created_capability) = if let Some(created) = created {
            created
        } else {
            self.recover_progress_publications(manifest_sha256)?;
            if self
                .progress_namespace_inventory(manifest_sha256)?
                .published
                .contains_key(&sequence)
            {
                return self.readback_existing(manifest_sha256, sequence, Some(checkpoint_bytes));
            }
            self.open_authenticated_progress_file(
                &private_name,
                ProgressFileCapabilityKind::PublishingCreate,
                Some(&descriptor),
            )?
            .ok_or(GenerationSealError(
                "authority_generation_seal_progress_publishing_name_busy",
            ))?
        };
        write_progress_file(&created, checkpoint_bytes)?;
        flush_progress_handle(
            &created,
            "authority_generation_seal_progress_file_flush_failed",
        )?;
        let (created_bytes, created_identity) = read_progress_file_bounded(&created)?;
        if created_bytes != checkpoint_bytes
            || !same_progress_file_object(&created_capability.identity, &created_identity)
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_write_readback_mismatch",
            ));
        }
        validate_exact_progress_checkpoint(&created_bytes, &self.manifest, sequence)?;
        let written_capability = authenticate_progress_file_handle(
            &created,
            &self.expected_root_path.join(&private_name),
            ProgressFileCapabilityKind::PublishingCreate,
        )?;
        if written_capability.identity != created_identity
            || written_capability.security_phase != ProgressSecurityPhase::Staging
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_publishing_capability_drift",
            ));
        }
        drop(created);
        let (sealing, sealing_capability) = self
            .open_authenticated_progress_file(
                &private_name,
                ProgressFileCapabilityKind::PublishingSeal,
                None,
            )?
            .ok_or(GenerationSealError(
                "authority_generation_seal_progress_publishing_missing",
            ))?;
        let (sealing_bytes, sealing_identity) = read_progress_file_bounded(&sealing)?;
        if sealing_capability.identity != created_identity
            || sealing_identity != created_identity
            || sealing_bytes != checkpoint_bytes
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_identity_drift",
            ));
        }
        if sealing_capability.security_phase != ProgressSecurityPhase::Staging {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_security_phase_mismatch",
            ));
        }
        transition_publication_security(
            &sealing,
            FinalizerPublicationSecurityPhase::Staging,
            FinalizerPublicationSecurityPhase::PrivateSealed,
        )
        .map_err(GenerationSealError::from)?;
        flush_progress_handle(
            &sealing,
            "authority_generation_seal_progress_private_seal_flush_failed",
        )?;
        let private_sealed_capability = authenticate_progress_file_handle(
            &sealing,
            &self.expected_root_path.join(&private_name),
            ProgressFileCapabilityKind::PublishingSealPrivate,
        )?;
        let (private_sealed_bytes, private_sealed_identity) = read_progress_file_bounded(&sealing)?;
        if private_sealed_capability.identity != created_identity
            || private_sealed_identity != created_identity
            || private_sealed_bytes != checkpoint_bytes
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_publishing_seal_mismatch",
            ));
        }
        drop(sealing);
        let (sealed, sealed_capability) = self
            .open_authenticated_progress_file(
                &private_name,
                ProgressFileCapabilityKind::PublishingSealedRecovery,
                None,
            )?
            .ok_or(GenerationSealError(
                "authority_generation_seal_progress_publishing_missing",
            ))?;
        let (sealed_bytes, sealed_identity) = read_progress_file_bounded(&sealed)?;
        if sealed_capability.identity != created_identity
            || sealed_identity != created_identity
            || sealed_bytes != checkpoint_bytes
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_publishing_seal_mismatch",
            ));
        }
        self.finish_progress_publication(
            manifest_sha256,
            sequence,
            &private_name,
            sealed,
            sealed_capability,
            checkpoint_bytes,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn finish_progress_publication(
        &self,
        manifest_sha256: Digest32,
        sequence: u32,
        private_name: &str,
        sealed: OwnedHandle,
        private_capability: ProgressFileCapabilityReadback,
        expected_bytes: &[u8],
    ) -> Result<GenerationSealDurableReadback, GenerationSealError> {
        let final_name = generation_progress_relative_name(&manifest_sha256, sequence);
        let private_identity = private_capability.identity;
        let confirmed = authenticate_progress_file_handle(
            &sealed,
            &self.expected_root_path.join(private_name),
            ProgressFileCapabilityKind::PublishingSealedRecovery,
        )?;
        let (confirmed_bytes, confirmed_identity) = read_progress_file_bounded(&sealed)?;
        if confirmed != private_capability
            || confirmed_identity != private_identity
            || confirmed_bytes != expected_bytes
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_publishing_capability_drift",
            ));
        }
        match rename_progress_no_replace(&sealed, &self.root, &final_name)? {
            ProgressRenameDisposition::Published => {
                if progress_file_identity(&sealed)? != private_identity {
                    return Err(GenerationSealError(
                        "authority_generation_seal_progress_publishing_identity_drift",
                    ));
                }
                let final_path = self.expected_root_path.join(&final_name);
                let renamed_private_capability = authenticate_progress_file_handle(
                    &sealed,
                    &final_path,
                    ProgressFileCapabilityKind::PublishingSealedRecovery,
                )?;
                if renamed_private_capability.identity != private_identity {
                    return Err(GenerationSealError(
                        "authority_generation_seal_progress_publishing_identity_drift",
                    ));
                }
                transition_publication_security(
                    &sealed,
                    FinalizerPublicationSecurityPhase::PrivateSealed,
                    FinalizerPublicationSecurityPhase::PublishedImmutable,
                )
                .map_err(GenerationSealError::from)?;
                flush_progress_handle(
                    &sealed,
                    "authority_generation_seal_progress_publish_tighten_flush_failed",
                )?;
                let tightened = authenticate_progress_file_handle(
                    &sealed,
                    &final_path,
                    ProgressFileCapabilityKind::PublishedTightening,
                )?;
                if tightened.identity != private_identity
                    || progress_file_identity(&sealed)? != private_identity
                {
                    return Err(GenerationSealError(
                        "authority_generation_seal_progress_publishing_identity_drift",
                    ));
                }
                drop(sealed);
                flush_progress_handle(
                    &self.root,
                    "authority_generation_seal_progress_parent_flush_failed",
                )?;
                let readback =
                    self.readback_existing(manifest_sha256, sequence, Some(expected_bytes))?;
                if readback.volume_serial != private_identity.volume_serial
                    || readback.file_id != private_identity.file_id
                    || readback.link_count != private_identity.link_count
                {
                    return Err(GenerationSealError(
                        "authority_generation_seal_progress_published_identity_drift",
                    ));
                }
                if self
                    .progress_namespace_inventory(manifest_sha256)?
                    .publishing
                    .contains_key(&sequence)
                {
                    return Err(GenerationSealError(
                        "authority_generation_seal_progress_publishing_residue",
                    ));
                }
                Ok(readback)
            }
            ProgressRenameDisposition::DestinationExists => {
                let published = self.readback_existing(manifest_sha256, sequence, None)?;
                validate_progress_publish_collision(
                    &published.checkpoint_bytes,
                    published.bytes_sha256,
                    expected_bytes,
                )?;
                let confirmed = authenticate_progress_file_handle(
                    &sealed,
                    &self.expected_root_path.join(private_name),
                    ProgressFileCapabilityKind::PublishingSealedRecovery,
                )?;
                let (private_bytes, current_identity) = read_progress_file_bounded(&sealed)?;
                if confirmed.identity != private_identity
                    || current_identity != private_identity
                    || private_bytes != expected_bytes
                {
                    return Err(GenerationSealError(
                        "authority_generation_seal_progress_publishing_identity_drift",
                    ));
                }
                delete_progress_publishing(&self.root, sealed)?;
                Ok(published)
            }
        }
    }
}

impl GenerationSealProgressStore for NativeGenerationSealProgressStore {
    fn load_exact_chain(
        &mut self,
        manifest_sha256: Digest32,
    ) -> Result<Vec<GenerationSealDurableReadback>, GenerationSealError> {
        self.recover_progress_publications(manifest_sha256)?;
        let inventory = self.progress_namespace_inventory(manifest_sha256)?;
        if !inventory.publishing.is_empty() {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_publishing_residue",
            ));
        }
        let mut chain = Vec::with_capacity(inventory.published.len());
        for (sequence, entry) in inventory.published {
            let readback = self.readback_existing(manifest_sha256, sequence, None)?;
            if readback.file_id != entry.file_id || readback.byte_length != entry.byte_length {
                return Err(GenerationSealError(
                    "authority_generation_seal_progress_namespace_readback_mismatch",
                ));
            }
            chain.push(readback);
        }
        self.revalidate_root()?;
        Ok(chain)
    }

    fn compare_and_swap_create_new(
        &mut self,
        manifest_sha256: Digest32,
        expected_previous_checkpoint_sha256: Option<Digest32>,
        checkpoint_bytes: &[u8],
    ) -> Result<GenerationSealDurableReadback, GenerationSealError> {
        self.require_manifest(manifest_sha256)?;
        if checkpoint_bytes.is_empty() || checkpoint_bytes.len() > MAX_SMALL_OBJECT_BYTES as usize {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_size_invalid",
            ));
        }
        let sequence = progress_sequence_from_envelope(checkpoint_bytes, manifest_sha256)?;
        let chain = self.load_exact_chain(manifest_sha256)?;
        if sequence as usize != chain.len()
            || chain.last().map(|value| value.checkpoint_sha256)
                != expected_previous_checkpoint_sha256
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_cas_mismatch",
            ));
        }
        self.create_progress_file(manifest_sha256, sequence, checkpoint_bytes)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ProgressNamespaceName {
    Published(u32),
    Publishing(u32),
}

pub(super) fn generation_progress_namespace_member(
    relative_name: &str,
) -> Result<Option<(Digest32, u32, bool)>, GenerationSealError> {
    Ok(
        parse_unbound_generation_progress_namespace_name(relative_name)?.map(
            |(manifest_sha256, kind)| match kind {
                ProgressNamespaceName::Published(sequence) => (manifest_sha256, sequence, false),
                ProgressNamespaceName::Publishing(sequence) => (manifest_sha256, sequence, true),
            },
        ),
    )
}

#[derive(Debug, Default)]
struct ProgressNamespaceInventory {
    published: BTreeMap<u32, DirectoryInventoryEntry>,
    publishing: BTreeMap<u32, DirectoryInventoryEntry>,
}

fn parse_generation_progress_namespace_name(
    relative_name: &str,
    manifest_sha256: Digest32,
) -> Result<Option<ProgressNamespaceName>, GenerationSealError> {
    let Some((observed_manifest_sha256, kind)) =
        parse_unbound_generation_progress_namespace_name(relative_name)?
    else {
        return Ok(None);
    };
    if observed_manifest_sha256 != manifest_sha256 {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_manifest_namespace_mismatch",
        ));
    }
    Ok(Some(kind))
}

#[cfg(test)]
fn parse_generation_progress_relative_name(
    relative_name: &str,
    manifest_sha256: Digest32,
) -> Result<Option<u32>, GenerationSealError> {
    match parse_generation_progress_namespace_name(relative_name, manifest_sha256)? {
        Some(ProgressNamespaceName::Published(sequence)) => Ok(Some(sequence)),
        Some(ProgressNamespaceName::Publishing(_)) => Err(GenerationSealError(
            "authority_generation_seal_progress_namespace_invalid",
        )),
        None => Ok(None),
    }
}

fn parse_unbound_generation_progress_namespace_name(
    relative_name: &str,
) -> Result<Option<(Digest32, ProgressNamespaceName)>, GenerationSealError> {
    const PREFIX: &str = "generation-seal.";
    if !relative_name.to_ascii_lowercase().starts_with(PREFIX) {
        return Ok(None);
    }
    if !relative_name.starts_with(PREFIX) {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_namespace_invalid",
        ));
    }
    let (final_name, publishing) = relative_name
        .strip_suffix(GENERATION_PROGRESS_PRIVATE_SUFFIX)
        .map(|value| (value, true))
        .unwrap_or((relative_name, false));
    let suffix = &final_name[PREFIX.len()..];
    let (manifest_text, sequence_text) = suffix.split_once('.').ok_or(GenerationSealError(
        "authority_generation_seal_progress_namespace_invalid",
    ))?;
    let manifest_sha256 = decode_hex_32(manifest_text)
        .filter(|value| !is_zero_digest(value) && manifest_text == hex_lower(value))
        .ok_or(GenerationSealError(
            "authority_generation_seal_progress_namespace_invalid",
        ))?;
    let sequence = sequence_text
        .strip_suffix(".json")
        .filter(|digits| digits.len() == 2 && digits.bytes().all(|byte| byte.is_ascii_digit()))
        .and_then(|digits| digits.parse::<u32>().ok())
        .filter(|sequence| *sequence <= GENERATION_SEAL_TERMINAL_SEQUENCE)
        .ok_or(GenerationSealError(
            "authority_generation_seal_progress_namespace_invalid",
        ))?;
    if final_name != generation_progress_relative_name(&manifest_sha256, sequence) {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_namespace_invalid",
        ));
    }
    let kind = if publishing {
        ProgressNamespaceName::Publishing(sequence)
    } else {
        ProgressNamespaceName::Published(sequence)
    };
    Ok(Some((manifest_sha256, kind)))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ProgressFileOpen {
    CreateNew,
    Sealing,
    StagingRecovery,
    SealedRecovery,
    ReadOnly,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ProgressFileCapabilityKind {
    PublishingInspection,
    PublishingCreate,
    PublishingSeal,
    PublishingSealPrivate,
    PublishingStagingRecovery,
    PublishingSealedRecovery,
    PublishedTightening,
    PublishedReadOnly,
}

impl ProgressFileCapabilityKind {
    const fn open_mode(self) -> ProgressFileOpen {
        match self {
            Self::PublishingInspection | Self::PublishedReadOnly => ProgressFileOpen::ReadOnly,
            Self::PublishingCreate => ProgressFileOpen::CreateNew,
            Self::PublishingSeal | Self::PublishingSealPrivate => ProgressFileOpen::Sealing,
            Self::PublishingStagingRecovery => ProgressFileOpen::StagingRecovery,
            Self::PublishingSealedRecovery | Self::PublishedTightening => {
                ProgressFileOpen::SealedRecovery
            }
        }
    }

    const fn expected_access(self) -> u32 {
        match self {
            Self::PublishingInspection | Self::PublishedReadOnly => PROGRESS_FILE_READ_ACCESS,
            Self::PublishingCreate => PROGRESS_FILE_CREATE_ACCESS,
            Self::PublishingSeal | Self::PublishingSealPrivate => PROGRESS_FILE_SEAL_ACCESS,
            Self::PublishingStagingRecovery => PROGRESS_FILE_STAGING_RECOVERY_ACCESS,
            Self::PublishingSealedRecovery | Self::PublishedTightening => {
                PROGRESS_FILE_SEALED_RECOVERY_ACCESS
            }
        }
    }

    const fn expected_phase(self) -> Option<ProgressSecurityPhase> {
        match self {
            Self::PublishingInspection => None,
            Self::PublishingCreate | Self::PublishingSeal | Self::PublishingStagingRecovery => {
                Some(ProgressSecurityPhase::Staging)
            }
            Self::PublishingSealPrivate | Self::PublishingSealedRecovery => {
                Some(ProgressSecurityPhase::PrivateSealed)
            }
            Self::PublishedTightening | Self::PublishedReadOnly => {
                Some(ProgressSecurityPhase::PublishedImmutable)
            }
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ProgressSecurityPhase {
    Staging,
    PrivateSealed,
    PublishedImmutable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ProgressFileIdentity {
    volume_serial: u64,
    file_id: [u8; 16],
    link_count: u32,
    byte_length: u64,
    attributes: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ProgressFileCapabilityReadback {
    identity: ProgressFileIdentity,
    granted_access: u32,
    security_phase: ProgressSecurityPhase,
    security_sha256: Digest32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ProgressPublishingRecoveryAction {
    DeleteStagingAndRetry,
    RollForwardSealed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ProgressFinalRecoveryAction {
    TightenPrivateSealed,
    AlreadyImmutable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ProgressRenameDisposition {
    Published,
    DestinationExists,
}

fn open_progress_file(
    parent: &OwnedHandle,
    name: &str,
    mode: ProgressFileOpen,
    create_descriptor: Option<&FinalizerStagingSecurityDescriptor>,
) -> Result<Option<OwnedHandle>, FinalizerSecurityError> {
    validate_relative_name(name).map_err(|_| {
        FinalizerSecurityError::new("authority_generation_seal_progress_name_invalid")
    })?;
    if (mode == ProgressFileOpen::CreateNew) != create_descriptor.is_some() {
        return Err(FinalizerSecurityError::new(
            "authority_generation_seal_progress_descriptor_invalid",
        ));
    }
    let mut words = name.encode_utf16().collect::<Vec<_>>();
    let name_bytes = words
        .len()
        .checked_mul(size_of::<u16>())
        .and_then(|value| u16::try_from(value).ok())
        .ok_or(FinalizerSecurityError::new(
            "authority_generation_seal_progress_name_invalid",
        ))?;
    let unicode = UNICODE_STRING {
        Length: name_bytes,
        MaximumLength: name_bytes,
        Buffer: words.as_mut_ptr(),
    };
    let attributes = OBJECT_ATTRIBUTES {
        Length: size_of::<OBJECT_ATTRIBUTES>() as u32,
        RootDirectory: parent.as_raw_handle().cast(),
        ObjectName: &unicode,
        Attributes: OBJ_CASE_INSENSITIVE as u32,
        SecurityDescriptor: create_descriptor
            .map(FinalizerStagingSecurityDescriptor::as_ptr)
            .unwrap_or(ptr::null_mut()),
        SecurityQualityOfService: ptr::null_mut(),
    };
    let (access, disposition, options, expected_information) = match mode {
        ProgressFileOpen::CreateNew => (
            PROGRESS_FILE_CREATE_ACCESS,
            FILE_CREATE,
            FILE_SYNCHRONOUS_IO_NONALERT
                | FILE_OPEN_REPARSE_POINT
                | FILE_NON_DIRECTORY_FILE
                | FILE_WRITE_THROUGH,
            FILE_CREATED_INFORMATION,
        ),
        ProgressFileOpen::Sealing => (
            PROGRESS_FILE_SEAL_ACCESS,
            FILE_OPEN,
            FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT | FILE_NON_DIRECTORY_FILE,
            FILE_OPENED_INFORMATION,
        ),
        ProgressFileOpen::StagingRecovery => (
            PROGRESS_FILE_STAGING_RECOVERY_ACCESS,
            FILE_OPEN,
            FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT | FILE_NON_DIRECTORY_FILE,
            FILE_OPENED_INFORMATION,
        ),
        ProgressFileOpen::SealedRecovery => (
            PROGRESS_FILE_SEALED_RECOVERY_ACCESS,
            FILE_OPEN,
            FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT | FILE_NON_DIRECTORY_FILE,
            FILE_OPENED_INFORMATION,
        ),
        ProgressFileOpen::ReadOnly => (
            PROGRESS_FILE_READ_ACCESS,
            FILE_OPEN,
            FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT | FILE_NON_DIRECTORY_FILE,
            FILE_OPENED_INFORMATION,
        ),
    };
    let mut status_block = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let mut handle = ptr::null_mut();
    let status = unsafe {
        NtCreateFile(
            &mut handle,
            access,
            &attributes,
            &mut status_block,
            ptr::null(),
            FILE_ATTRIBUTE_NORMAL,
            FILE_SHARE_READ,
            disposition,
            options,
            ptr::null(),
            0,
        )
    };
    if (mode == ProgressFileOpen::CreateNew && status == STATUS_OBJECT_NAME_COLLISION)
        || (mode != ProgressFileOpen::CreateNew
            && matches!(
                status,
                STATUS_NO_SUCH_FILE | STATUS_OBJECT_NAME_NOT_FOUND | STATUS_OBJECT_PATH_NOT_FOUND
            ))
    {
        return Ok(None);
    }
    if status < 0
        || handle.is_null()
        || handle == INVALID_HANDLE_VALUE
        || status_block.Information != expected_information
    {
        if !handle.is_null() && handle != INVALID_HANDLE_VALUE {
            unsafe { drop(OwnedHandle::from_raw_handle(handle as RawHandle)) };
        }
        return Err(FinalizerSecurityError::new(match mode {
            ProgressFileOpen::CreateNew => "authority_generation_seal_progress_create_failed",
            ProgressFileOpen::Sealing
            | ProgressFileOpen::StagingRecovery
            | ProgressFileOpen::SealedRecovery
            | ProgressFileOpen::ReadOnly => "authority_generation_seal_progress_open_failed",
        }));
    }
    Ok(Some(unsafe {
        OwnedHandle::from_raw_handle(handle as RawHandle)
    }))
}

fn authenticate_progress_file_handle(
    handle: &OwnedHandle,
    expected_path: &Path,
    kind: ProgressFileCapabilityKind,
) -> Result<ProgressFileCapabilityReadback, GenerationSealError> {
    require_exact_handle_path(handle, expected_path)?;
    let identity = progress_file_identity(handle)?;
    let granted_access =
        query_progress_granted_access(handle).map_err(GenerationSealError::from)?;
    if granted_access != kind.expected_access() {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_handle_access_mismatch",
        ));
    }
    let security_sha256 =
        with_finalizer_security_privilege(|| progress_complete_security_sha256(handle))
            .map_err(GenerationSealError::from)?;
    let security_phase = classify_progress_security_sha256(security_sha256)?;
    if kind
        .expected_phase()
        .is_some_and(|expected| expected != security_phase)
    {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_security_phase_mismatch",
        ));
    }
    Ok(ProgressFileCapabilityReadback {
        identity,
        granted_access,
        security_phase,
        security_sha256,
    })
}

fn classify_progress_security_sha256(
    actual: Digest32,
) -> Result<ProgressSecurityPhase, GenerationSealError> {
    let staging = expected_publication_security_sha256(FinalizerPublicationSecurityPhase::Staging)
        .map_err(GenerationSealError::from)?;
    let private_sealed =
        expected_publication_security_sha256(FinalizerPublicationSecurityPhase::PrivateSealed)
            .map_err(GenerationSealError::from)?;
    let published_immutable =
        expected_publication_security_sha256(FinalizerPublicationSecurityPhase::PublishedImmutable)
            .map_err(GenerationSealError::from)?;
    if actual == staging {
        Ok(ProgressSecurityPhase::Staging)
    } else if actual == private_sealed {
        Ok(ProgressSecurityPhase::PrivateSealed)
    } else if actual == published_immutable {
        Ok(ProgressSecurityPhase::PublishedImmutable)
    } else {
        Err(GenerationSealError(
            "authority_generation_seal_progress_security_phase_invalid",
        ))
    }
}

fn progress_file_identity(
    handle: &OwnedHandle,
) -> Result<ProgressFileIdentity, GenerationSealError> {
    let mut information = unsafe { zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    let raw: HANDLE = handle.as_raw_handle().cast();
    if raw.is_null()
        || raw == INVALID_HANDLE_VALUE
        || unsafe { GetFileInformationByHandle(raw, &mut information) } == 0
        || information.dwVolumeSerialNumber == 0
        || (information.nFileIndexHigh == 0 && information.nFileIndexLow == 0)
        || information.nNumberOfLinks != 1
        || information.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)
            != 0
    {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_file_identity_invalid",
        ));
    }
    let byte_length =
        (u64::from(information.nFileSizeHigh) << 32) | u64::from(information.nFileSizeLow);
    if byte_length > MAX_SMALL_OBJECT_BYTES {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_size_invalid",
        ));
    }
    let file_index =
        (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow);
    let mut file_id = [0u8; 16];
    file_id[..8].copy_from_slice(&file_index.to_be_bytes());
    Ok(ProgressFileIdentity {
        volume_serial: u64::from(information.dwVolumeSerialNumber),
        file_id,
        link_count: information.nNumberOfLinks,
        byte_length,
        attributes: information.dwFileAttributes,
    })
}

fn same_progress_file_object(left: &ProgressFileIdentity, right: &ProgressFileIdentity) -> bool {
    left.volume_serial == right.volume_serial
        && left.file_id == right.file_id
        && left.link_count == right.link_count
        && left.attributes == right.attributes
}

fn progress_identity_matches_preseal(
    identity: &ProgressFileIdentity,
    preseal: &PreSealStableIdentity,
) -> bool {
    identity.volume_serial == preseal.volume_serial()
        && identity.file_id == *preseal.file_id()
        && identity.link_count == preseal.link_count()
        && identity.byte_length == preseal.byte_length()
}

fn read_progress_file_bounded(
    file: &OwnedHandle,
) -> Result<(Vec<u8>, ProgressFileIdentity), GenerationSealError> {
    let before = progress_file_identity(file)?;
    seek_progress_file(file, 0)?;
    let mut bytes = vec![0u8; before.byte_length as usize];
    let mut offset = 0usize;
    while offset < bytes.len() {
        let mut read = 0u32;
        let remaining = &mut bytes[offset..];
        if unsafe {
            ReadFile(
                file.as_raw_handle().cast(),
                remaining.as_mut_ptr(),
                remaining.len().min(u32::MAX as usize) as u32,
                &mut read,
                ptr::null_mut(),
            )
        } == 0
            || read == 0
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_read_failed",
            ));
        }
        offset = offset
            .checked_add(read as usize)
            .ok_or(GenerationSealError(
                "authority_generation_seal_progress_read_failed",
            ))?;
    }
    let mut extra = [0u8; 1];
    let mut extra_read = 0u32;
    if unsafe {
        ReadFile(
            file.as_raw_handle().cast(),
            extra.as_mut_ptr(),
            1,
            &mut extra_read,
            ptr::null_mut(),
        )
    } == 0
        || extra_read != 0
    {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_length_mismatch",
        ));
    }
    let after = progress_file_identity(file)?;
    if before != after {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_identity_drift",
        ));
    }
    Ok((bytes, before))
}

fn validate_exact_progress_checkpoint(
    bytes: &[u8],
    manifest: &GenerationSealManifest,
    expected_sequence: u32,
) -> Result<GenerationSealProgressCheckpoint, GenerationSealError> {
    let parsed: GenerationSealProgressCheckpoint = serde_json::from_slice(bytes).map_err(|_| {
        GenerationSealError("authority_generation_seal_progress_serialization_invalid")
    })?;
    let writer_closure = parsed.writer_closure_readback.clone();
    let exact =
        GenerationSealProgressCheckpoint::parse_canonical(bytes, manifest, &writer_closure)?;
    if exact.sequence != expected_sequence {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_sequence_invalid",
        ));
    }
    Ok(exact)
}

fn classify_progress_publishing_recovery(
    phase: ProgressSecurityPhase,
    bytes: &[u8],
    manifest: &GenerationSealManifest,
    expected_sequence: u32,
) -> Result<ProgressPublishingRecoveryAction, GenerationSealError> {
    if bytes.len() > MAX_SMALL_OBJECT_BYTES as usize {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_size_invalid",
        ));
    }
    match phase {
        ProgressSecurityPhase::Staging => {
            validate_torn_or_exact_staging_checkpoint(bytes, manifest, expected_sequence)?;
            Ok(ProgressPublishingRecoveryAction::DeleteStagingAndRetry)
        }
        ProgressSecurityPhase::PrivateSealed => {
            validate_exact_progress_checkpoint(bytes, manifest, expected_sequence)?;
            Ok(ProgressPublishingRecoveryAction::RollForwardSealed)
        }
        ProgressSecurityPhase::PublishedImmutable => Err(GenerationSealError(
            "authority_generation_seal_progress_published_descriptor_at_private_name",
        )),
    }
}

fn classify_progress_final_recovery(
    phase: ProgressSecurityPhase,
    bytes: &[u8],
    manifest: &GenerationSealManifest,
    expected_sequence: u32,
) -> Result<ProgressFinalRecoveryAction, GenerationSealError> {
    if phase == ProgressSecurityPhase::Staging {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_staging_descriptor_at_final_name",
        ));
    }
    validate_exact_progress_checkpoint(bytes, manifest, expected_sequence)?;
    Ok(match phase {
        ProgressSecurityPhase::PrivateSealed => ProgressFinalRecoveryAction::TightenPrivateSealed,
        ProgressSecurityPhase::PublishedImmutable => ProgressFinalRecoveryAction::AlreadyImmutable,
        ProgressSecurityPhase::Staging => unreachable!("staging returned above"),
    })
}

fn validate_torn_or_exact_staging_checkpoint(
    bytes: &[u8],
    manifest: &GenerationSealManifest,
    expected_sequence: u32,
) -> Result<(), GenerationSealError> {
    let expected_prefix = format!(
        "{{\"schema\":\"{}\",\"manifestSha256\":\"{}\",",
        GENERATION_SEAL_PROGRESS_SCHEMA,
        hex_lower(&manifest.digest()?)
    );
    if expected_prefix.as_bytes().starts_with(bytes) {
        return Ok(());
    }
    match serde_json::from_slice::<GenerationSealProgressCheckpoint>(bytes) {
        Ok(_) => {
            validate_exact_progress_checkpoint(bytes, manifest, expected_sequence)?;
            Ok(())
        }
        Err(error) if error.is_eof() && bytes.starts_with(expected_prefix.as_bytes()) => Ok(()),
        Err(_) => Err(GenerationSealError(
            "authority_generation_seal_progress_publishing_content_unknown",
        )),
    }
}

fn validate_progress_publish_collision(
    published_bytes: &[u8],
    published_sha256: Digest32,
    expected_bytes: &[u8],
) -> Result<(), GenerationSealError> {
    if published_bytes != expected_bytes || published_sha256 != sha256_bytes(expected_bytes) {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_publish_collision_conflict",
        ));
    }
    Ok(())
}

#[repr(C)]
#[derive(Clone, Copy)]
struct ProgressObjectBasicInformation {
    attributes: u32,
    granted_access: u32,
    handle_count: u32,
    pointer_count: u32,
    reserved: [u32; 10],
}

fn query_progress_granted_access(handle: &OwnedHandle) -> Result<u32, FinalizerSecurityError> {
    let mut information = unsafe { zeroed::<ProgressObjectBasicInformation>() };
    let mut returned = 0u32;
    let status = unsafe {
        NtQueryObject(
            handle.as_raw_handle().cast(),
            ObjectBasicInformation,
            (&mut information as *mut ProgressObjectBasicInformation).cast(),
            size_of::<ProgressObjectBasicInformation>() as u32,
            &mut returned,
        )
    };
    if status < 0 || returned < (size_of::<u32>() * 2) as u32 {
        return Err(FinalizerSecurityError::new(
            "authority_generation_seal_progress_handle_access_unavailable",
        ));
    }
    Ok(information.granted_access)
}

struct ProgressLocalSecurityDescriptor(PSECURITY_DESCRIPTOR);

impl Drop for ProgressLocalSecurityDescriptor {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                LocalFree(self.0.cast());
            }
        }
    }
}

fn progress_complete_security_sha256(
    handle: &OwnedHandle,
) -> Result<Digest32, FinalizerSecurityError> {
    let mut descriptor = ptr::null_mut();
    if unsafe {
        GetSecurityInfo(
            handle.as_raw_handle().cast(),
            SE_FILE_OBJECT,
            FULL_SECURITY_INFORMATION,
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            &mut descriptor,
        )
    } != 0
        || descriptor.is_null()
    {
        return Err(FinalizerSecurityError::new(
            "authority_generation_seal_progress_security_readback_failed",
        ));
    }
    let descriptor = ProgressLocalSecurityDescriptor(descriptor);
    let mut encoded = ptr::null_mut();
    let mut length = 0u32;
    if unsafe {
        ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor.0,
            SDDL_REVISION_1,
            FULL_SECURITY_INFORMATION,
            &mut encoded,
            &mut length,
        )
    } == 0
        || encoded.is_null()
        || length == 0
        || length > 16 * 1024
    {
        return Err(FinalizerSecurityError::new(
            "authority_generation_seal_progress_security_readback_failed",
        ));
    }
    let mut words = unsafe { slice::from_raw_parts(encoded, length as usize) }.to_vec();
    unsafe {
        LocalFree(encoded.cast());
    }
    let terminator =
        words
            .iter()
            .position(|word| *word == 0)
            .ok_or(FinalizerSecurityError::new(
                "authority_generation_seal_progress_security_readback_failed",
            ))?;
    if terminator == 0 || words[terminator..].iter().any(|word| *word != 0) {
        return Err(FinalizerSecurityError::new(
            "authority_generation_seal_progress_security_readback_failed",
        ));
    }
    words.truncate(terminator);
    let canonical = String::from_utf16(&words).map_err(|_| {
        FinalizerSecurityError::new("authority_generation_seal_progress_security_readback_failed")
    })?;
    Ok(Sha256::digest(canonical.as_bytes()).into())
}

fn rename_progress_no_replace(
    file: &OwnedHandle,
    parent: &OwnedHandle,
    destination_name: &str,
) -> Result<ProgressRenameDisposition, GenerationSealError> {
    validate_relative_name(destination_name)?;
    let name = destination_name.encode_utf16().collect::<Vec<_>>();
    let name_byte_length = name
        .len()
        .checked_mul(size_of::<u16>())
        .and_then(|value| u32::try_from(value).ok())
        .ok_or(GenerationSealError(
            "authority_generation_seal_progress_name_invalid",
        ))?;
    let file_name_offset = offset_of!(FILE_RENAME_INFORMATION, FileName);
    let total_length = file_name_offset
        .checked_add(name_byte_length as usize)
        .ok_or(GenerationSealError(
            "authority_generation_seal_progress_name_invalid",
        ))?;
    let storage_words = total_length
        .checked_add(size_of::<usize>() - 1)
        .map(|value| value / size_of::<usize>())
        .ok_or(GenerationSealError(
            "authority_generation_seal_progress_name_invalid",
        ))?;
    let mut storage = vec![0usize; storage_words];
    let information = storage.as_mut_ptr().cast::<FILE_RENAME_INFORMATION>();
    unsafe {
        (*information).Anonymous.ReplaceIfExists = 0;
        (*information).RootDirectory = parent.as_raw_handle().cast();
        (*information).FileNameLength = name_byte_length;
        ptr::copy_nonoverlapping(
            name.as_ptr(),
            storage
                .as_mut_ptr()
                .cast::<u8>()
                .add(file_name_offset)
                .cast(),
            name.len(),
        );
    }
    let mut status = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let code = unsafe {
        NtSetInformationFile(
            file.as_raw_handle().cast(),
            &mut status,
            information.cast(),
            total_length as u32,
            FileRenameInformation,
        )
    };
    if code == STATUS_OBJECT_NAME_COLLISION {
        return Ok(ProgressRenameDisposition::DestinationExists);
    }
    if code < 0 {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_publish_failed",
        ));
    }
    Ok(ProgressRenameDisposition::Published)
}

fn delete_progress_publishing(
    parent: &OwnedHandle,
    publishing: OwnedHandle,
) -> Result<(), GenerationSealError> {
    progress_file_identity(&publishing)?;
    let disposition = FILE_DISPOSITION_INFORMATION_EX {
        Flags: FILE_DISPOSITION_DELETE,
    };
    let mut status = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    if unsafe {
        NtSetInformationFile(
            publishing.as_raw_handle().cast(),
            &mut status,
            (&disposition as *const FILE_DISPOSITION_INFORMATION_EX).cast(),
            size_of::<FILE_DISPOSITION_INFORMATION_EX>() as u32,
            FileDispositionInformationEx,
        )
    } < 0
    {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_publishing_delete_failed",
        ));
    }
    drop(publishing);
    flush_progress_handle(
        parent,
        "authority_generation_seal_progress_parent_flush_failed",
    )
}

fn progress_sequence_from_envelope(
    bytes: &[u8],
    manifest_sha256: Digest32,
) -> Result<u32, GenerationSealError> {
    if bytes.is_empty() || bytes.len() > MAX_SMALL_OBJECT_BYTES as usize {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_size_invalid",
        ));
    }
    let value: serde_json::Value = serde_json::from_slice(bytes).map_err(|_| {
        GenerationSealError("authority_generation_seal_progress_serialization_invalid")
    })?;
    let expected_manifest = hex_lower(&manifest_sha256);
    if value.get("schema").and_then(serde_json::Value::as_str)
        != Some(GENERATION_SEAL_PROGRESS_SCHEMA)
        || value
            .get("manifestSha256")
            .and_then(serde_json::Value::as_str)
            != Some(expected_manifest.as_str())
    {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_binding_mismatch",
        ));
    }
    let sequence = value
        .get("sequence")
        .and_then(serde_json::Value::as_u64)
        .and_then(|value| u32::try_from(value).ok())
        .filter(|value| *value <= GENERATION_SEAL_TERMINAL_SEQUENCE)
        .ok_or(GenerationSealError(
            "authority_generation_seal_progress_sequence_invalid",
        ))?;
    Ok(sequence)
}

fn validate_progress_envelope(
    bytes: &[u8],
    manifest_sha256: Digest32,
    expected_sequence: u32,
) -> Result<(), GenerationSealError> {
    if progress_sequence_from_envelope(bytes, manifest_sha256)? != expected_sequence {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_sequence_invalid",
        ));
    }
    Ok(())
}

fn write_progress_file(file: &OwnedHandle, bytes: &[u8]) -> Result<(), GenerationSealError> {
    if bytes.is_empty() || bytes.len() > MAX_SMALL_OBJECT_BYTES as usize {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_size_invalid",
        ));
    }
    seek_progress_file(file, 0)?;
    let mut offset = 0usize;
    while offset < bytes.len() {
        let mut written = 0u32;
        let remaining = &bytes[offset..];
        if unsafe {
            WriteFile(
                file.as_raw_handle().cast(),
                remaining.as_ptr(),
                remaining.len().min(u32::MAX as usize) as u32,
                &mut written,
                ptr::null_mut(),
            )
        } == 0
            || written == 0
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_write_failed",
            ));
        }
        offset = offset
            .checked_add(written as usize)
            .ok_or(GenerationSealError(
                "authority_generation_seal_progress_write_failed",
            ))?;
    }
    Ok(())
}

fn read_progress_file_stable(
    file: &OwnedHandle,
) -> Result<(Vec<u8>, PreSealStableIdentity), GenerationSealError> {
    let before = capture_preseal_identity_for_target(file, FinalizerSealTarget::ImmutableStateFile)
        .map_err(GenerationSealError::from)?;
    seek_progress_file(file, 0)?;
    let mut bytes = vec![0u8; before.byte_length() as usize];
    let mut offset = 0usize;
    while offset < bytes.len() {
        let mut read = 0u32;
        let remaining = &mut bytes[offset..];
        if unsafe {
            ReadFile(
                file.as_raw_handle().cast(),
                remaining.as_mut_ptr(),
                remaining.len().min(u32::MAX as usize) as u32,
                &mut read,
                ptr::null_mut(),
            )
        } == 0
            || read == 0
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_read_failed",
            ));
        }
        offset = offset
            .checked_add(read as usize)
            .ok_or(GenerationSealError(
                "authority_generation_seal_progress_read_failed",
            ))?;
    }
    let mut extra = [0u8; 1];
    let mut extra_read = 0u32;
    if unsafe {
        ReadFile(
            file.as_raw_handle().cast(),
            extra.as_mut_ptr(),
            1,
            &mut extra_read,
            ptr::null_mut(),
        )
    } == 0
        || extra_read != 0
    {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_length_mismatch",
        ));
    }
    let after = capture_preseal_identity_for_target(file, FinalizerSealTarget::ImmutableStateFile)
        .map_err(GenerationSealError::from)?;
    if before != after || before.bytes_sha256() != Some(&sha256_bytes(&bytes)) {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_identity_drift",
        ));
    }
    Ok((bytes, before))
}

fn seek_progress_file(file: &OwnedHandle, offset: i64) -> Result<(), GenerationSealError> {
    let mut observed = 0i64;
    if unsafe {
        SetFilePointerEx(
            file.as_raw_handle().cast(),
            offset,
            &mut observed,
            FILE_BEGIN,
        )
    } == 0
        || observed != offset
    {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_seek_failed",
        ));
    }
    Ok(())
}

fn flush_progress_handle(
    handle: &OwnedHandle,
    code: &'static str,
) -> Result<(), GenerationSealError> {
    let mut status = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    if unsafe { NtFlushBuffersFile(handle.as_raw_handle().cast(), &mut status) } < 0 {
        return Err(GenerationSealError(code));
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct DirectoryInventoryEntry {
    relative_name: String,
    file_id: [u8; 16],
    byte_length: u64,
    is_directory: bool,
    is_reparse: bool,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
struct GenerationSealInventory {
    entries: BTreeMap<GenerationSealRoot, Vec<DirectoryInventoryEntry>>,
}

impl GenerationSealInventory {
    fn insert(&mut self, root: GenerationSealRoot, entries: Vec<DirectoryInventoryEntry>) {
        self.entries.insert(root, entries);
    }

    fn validate_against(
        &self,
        manifest: &GenerationSealManifest,
    ) -> Result<(), GenerationSealError> {
        manifest.validate()?;
        for root in [
            GenerationSealRoot::BinaryGeneration,
            GenerationSealRoot::StateGeneration,
        ] {
            let expected = manifest
                .objects
                .iter()
                .filter(|object| object.root == root && object.relative_path != ".")
                .map(|object| (object.relative_path.as_str(), object.object_type))
                .collect::<BTreeMap<_, _>>();
            let actual_entries = self.entries.get(&root).ok_or(GenerationSealError(
                "authority_generation_seal_inventory_missing",
            ))?;
            let mut actual = BTreeMap::new();
            for entry in actual_entries {
                let expected_kind =
                    expected
                        .get(entry.relative_name.as_str())
                        .ok_or(GenerationSealError(
                            "authority_generation_seal_inventory_not_exhaustive",
                        ))?;
                validate_inventory_entry(entry, *expected_kind)?;
                if actual
                    .insert(entry.relative_name.as_str(), *expected_kind)
                    .is_some()
                {
                    return Err(GenerationSealError(
                        "authority_generation_seal_inventory_alias",
                    ));
                }
            }
            if actual != expected {
                return Err(GenerationSealError(
                    "authority_generation_seal_inventory_not_exhaustive",
                ));
            }
        }
        for root in [
            GenerationSealRoot::ActivationsNamespace,
            GenerationSealRoot::WorkerNonceNamespace,
            GenerationSealRoot::CandidateConsumptionNamespace,
        ] {
            let expected = manifest
                .objects
                .iter()
                .find(|object| object.root == root)
                .ok_or(GenerationSealError(
                    "authority_generation_seal_inventory_missing",
                ))?;
            let entries = self.entries.get(&root).ok_or(GenerationSealError(
                "authority_generation_seal_inventory_missing",
            ))?;
            let aliases = entries
                .iter()
                .filter(|entry| {
                    entry
                        .relative_name
                        .eq_ignore_ascii_case(&expected.relative_path)
                })
                .collect::<Vec<_>>();
            if aliases.len() != 1 || aliases[0].relative_name != expected.relative_path {
                return Err(GenerationSealError(
                    "authority_generation_seal_inventory_alias",
                ));
            }
            validate_inventory_entry(aliases[0], SealedObjectKind::File)?;
        }
        Ok(())
    }

    fn selected(
        &self,
        object: &GenerationSealObjectPlan,
    ) -> Result<&DirectoryInventoryEntry, GenerationSealError> {
        self.entries
            .get(&object.root)
            .and_then(|entries| {
                entries
                    .iter()
                    .find(|entry| entry.relative_name == object.relative_path)
            })
            .ok_or(GenerationSealError(
                "authority_generation_seal_inventory_missing",
            ))
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct FinalInventoryProjectionEntry<'a> {
    root: GenerationSealRoot,
    relative_name: &'a str,
    file_id: String,
    byte_length: u64,
    is_directory: bool,
    is_reparse: bool,
}

fn final_inventory_readback_digest(
    manifest: &GenerationSealManifest,
    inventory: &GenerationSealInventory,
) -> Result<Digest32, GenerationSealError> {
    inventory.validate_against(manifest)?;
    let mut entries = Vec::new();
    for root in [
        GenerationSealRoot::BinaryGeneration,
        GenerationSealRoot::StateGeneration,
        GenerationSealRoot::ActivationsNamespace,
        GenerationSealRoot::WorkerNonceNamespace,
        GenerationSealRoot::CandidateConsumptionNamespace,
    ] {
        for entry in inventory.entries.get(&root).ok_or(GenerationSealError(
            "authority_generation_seal_inventory_missing",
        ))? {
            entries.push(FinalInventoryProjectionEntry {
                root,
                relative_name: &entry.relative_name,
                file_id: hex_lower(&entry.file_id),
                byte_length: entry.byte_length,
                is_directory: entry.is_directory,
                is_reparse: entry.is_reparse,
            });
        }
    }
    entries.sort_by(|left, right| {
        left.root
            .cmp(&right.root)
            .then_with(|| left.relative_name.cmp(right.relative_name))
    });
    let bytes = serde_json::to_vec(&entries).map_err(|_| {
        GenerationSealError("authority_generation_seal_inventory_serialization_failed")
    })?;
    let mut bound = Vec::with_capacity(32 + bytes.len());
    bound.extend_from_slice(&manifest.digest()?);
    bound.extend_from_slice(&bytes);
    Ok(domain_digest(
        GENERATION_SEAL_FINAL_INVENTORY_DOMAIN,
        &bound,
    ))
}

fn validate_inventory_against_receipt(
    manifest: &GenerationSealManifest,
    inventory: &GenerationSealInventory,
    receipt: &GenerationSealReceipt,
) -> Result<(), GenerationSealError> {
    inventory.validate_against(manifest)?;
    receipt.validate_against(manifest)?;
    for (planned, sealed) in manifest.objects.iter().zip(&receipt.objects) {
        if planned.relative_path == "." {
            continue;
        }
        let entry = inventory.selected(planned)?;
        if decode_hex_16(&sealed.file_id) != Some(entry.file_id)
            || sealed.byte_length != entry.byte_length
            || entry.is_directory != (planned.object_type == SealedObjectKind::Directory)
        {
            return Err(GenerationSealError(
                "authority_generation_seal_inventory_receipt_mismatch",
            ));
        }
    }
    Ok(())
}

fn validate_inventory_entry(
    entry: &DirectoryInventoryEntry,
    expected_kind: SealedObjectKind,
) -> Result<(), GenerationSealError> {
    validate_relative_name(&entry.relative_name)?;
    if entry.file_id.iter().all(|byte| *byte == 0)
        || entry.is_reparse
        || entry.is_directory != (expected_kind == SealedObjectKind::Directory)
        || (expected_kind == SealedObjectKind::File && entry.byte_length == 0)
    {
        return Err(GenerationSealError(
            "authority_generation_seal_inventory_entry_invalid",
        ));
    }
    Ok(())
}

fn exact_sealed_handle_attributes(
    handle: &OwnedHandle,
    expected: &PreSealStableIdentity,
) -> Result<u32, GenerationSealError> {
    let mut information = unsafe { zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    let raw: HANDLE = handle.as_raw_handle().cast();
    if raw.is_null()
        || raw == INVALID_HANDLE_VALUE
        || unsafe { GetFileInformationByHandle(raw, &mut information) } == 0
    {
        return Err(GenerationSealError(
            "authority_generation_seal_sealed_identity_unavailable",
        ));
    }
    let file_index =
        (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow);
    let mut file_id = [0u8; 16];
    file_id[..8].copy_from_slice(&file_index.to_be_bytes());
    let byte_length =
        (u64::from(information.nFileSizeHigh) << 32) | u64::from(information.nFileSizeLow);
    let attributes = information.dwFileAttributes;
    let object_shape_valid = match expected.object_type() {
        FinalizerSealedObjectType::File => {
            attributes != 0
                && attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) == 0
        }
        FinalizerSealedObjectType::Directory => {
            attributes & FILE_ATTRIBUTE_DIRECTORY != 0
                && attributes & FILE_ATTRIBUTE_REPARSE_POINT == 0
        }
    };
    if u64::from(information.dwVolumeSerialNumber) != expected.volume_serial()
        || file_id != *expected.file_id()
        || information.nNumberOfLinks != expected.link_count()
        || byte_length != expected.byte_length()
        || !object_shape_valid
    {
        return Err(GenerationSealError(
            "authority_generation_seal_sealed_identity_mismatch",
        ));
    }
    Ok(attributes)
}

fn generation_sealed_receipt_from_held(
    planned: &GenerationSealObjectPlan,
    sealed: &FinalizerSealedHandle,
) -> Result<GenerationSealedObjectReceipt, GenerationSealError> {
    let attributes = exact_sealed_handle_attributes(
        sealed.read_only_handle(),
        sealed.receipt().stable_identity(),
    )?;
    GenerationSealedObjectReceipt::from_security(planned, sealed.receipt(), attributes)
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct GenerationSealedObjectReceipt {
    role: GenerationSealObjectRole,
    root: GenerationSealRoot,
    relative_path: String,
    object_type: SealedObjectKind,
    volume_serial: u64,
    file_id: String,
    link_count: u32,
    attributes: u32,
    byte_length: u64,
    bytes_sha256: Option<String>,
    staging_security_sha256: String,
    final_security_sha256: String,
    write_handle_closed_before_reopen: bool,
    read_only_reopen_verified: bool,
    recovered_exact_sealed_after_restart: bool,
}

impl GenerationSealedObjectReceipt {
    fn from_security(
        planned: &GenerationSealObjectPlan,
        receipt: &FinalizerSecuritySealReceipt,
        attributes: u32,
    ) -> Result<Self, GenerationSealError> {
        if receipt.target() != planned.role.target() {
            return Err(GenerationSealError(
                "authority_generation_seal_receipt_target_mismatch",
            ));
        }
        let identity = receipt.stable_identity();
        let value = Self {
            role: planned.role,
            root: planned.root,
            relative_path: planned.relative_path.clone(),
            object_type: match identity.object_type() {
                FinalizerSealedObjectType::File => SealedObjectKind::File,
                FinalizerSealedObjectType::Directory => SealedObjectKind::Directory,
            },
            volume_serial: identity.volume_serial(),
            file_id: hex_lower(identity.file_id()),
            link_count: identity.link_count(),
            attributes,
            byte_length: identity.byte_length(),
            bytes_sha256: identity.bytes_sha256().map(|value| hex_lower(value)),
            staging_security_sha256: hex_lower(receipt.staging_security_sha256()),
            final_security_sha256: hex_lower(receipt.sealed_security_sha256()),
            write_handle_closed_before_reopen: receipt.write_handle_closed_before_reopen(),
            read_only_reopen_verified: receipt.read_only_reopen_verified(),
            recovered_exact_sealed_after_restart: receipt.recovered_exact_sealed_after_restart(),
        };
        value.validate_against(planned)?;
        Ok(value)
    }

    fn stable_identity(&self) -> Result<PreSealStableIdentity, GenerationSealError> {
        let file_id = decode_hex_16(&self.file_id).ok_or(GenerationSealError(
            "authority_generation_seal_receipt_identity_invalid",
        ))?;
        match self.object_type {
            SealedObjectKind::File => {
                PreSealStableIdentity::new_file(
                    self.volume_serial,
                    file_id,
                    self.link_count,
                    self.byte_length,
                    self.bytes_sha256.as_deref().and_then(decode_hex_32).ok_or(
                        GenerationSealError("authority_generation_seal_receipt_identity_invalid"),
                    )?,
                )
            }
            SealedObjectKind::Directory => PreSealStableIdentity::new_directory(
                self.volume_serial,
                file_id,
                self.link_count,
                self.byte_length,
            ),
        }
        .map_err(GenerationSealError::from)
    }

    fn validate_against(
        &self,
        planned: &GenerationSealObjectPlan,
    ) -> Result<(), GenerationSealError> {
        planned.validate()?;
        let identity = self.stable_identity()?;
        if self.role != planned.role
            || self.root != planned.root
            || self.relative_path != planned.relative_path
            || self.object_type != planned.object_type
            || identity.link_count() != 1
            || self.attributes == 0
            || (planned.object_type == SealedObjectKind::File
                && self.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0)
            || (planned.object_type == SealedObjectKind::Directory
                && (self.attributes & FILE_ATTRIBUTE_DIRECTORY == 0
                    || self.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0))
            || self.staging_security_sha256 != planned.staging_security_sha256
            || self.final_security_sha256 != planned.final_security_sha256
            || !self.write_handle_closed_before_reopen
            || !self.read_only_reopen_verified
            || (planned.object_type == SealedObjectKind::File
                && (Some(identity.byte_length()) != planned.expected_byte_length
                    || identity.bytes_sha256().map(|value| hex_lower(value))
                        != planned.expected_bytes_sha256))
        {
            return Err(GenerationSealError(
                "authority_generation_seal_receipt_object_mismatch",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct ProtectedBlobNamespaceSealProjection {
    generation_sha256: Digest32,
    volume_serial: u64,
    file_id: [u8; 16],
    link_count: u32,
    attributes: u32,
    byte_length: u64,
    canonical_path_sha256: Digest32,
    initial_empty_inventory_sha256: Digest32,
    final_security_sha256: Digest32,
    file_security_sha256: Digest32,
    runtime_access: u32,
    share_access: u32,
    open_disposition: u32,
    file_create_access: u32,
    file_read_access: u32,
    file_cleanup_access: u32,
    seal_sha256: Digest32,
}

impl ProtectedBlobNamespaceSealProjection {
    fn from_verified_handle(
        roots: &NativeGenerationSealRoots,
        manifest: &GenerationSealManifest,
        receipt: &GenerationSealReceipt,
    ) -> Result<Self, GenerationSealError> {
        receipt.validate_against(manifest)?;
        let object = receipt
            .objects
            .iter()
            .find(|object| object.role == GenerationSealObjectRole::ProtectedBlobNamespace)
            .ok_or(GenerationSealError(
                "authority_protected_blob_namespace_seal_missing",
            ))?;
        let handle = with_finalizer_security_privilege(|| {
            open_relative_directory(
                roots
                    .root(GenerationSealRoot::StateGeneration)
                    .map_err(FinalizerSecurityError::from)?,
                AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME,
                false,
            )
        })
        .map_err(GenerationSealError::from)?;
        let expected_path = roots
            .path(GenerationSealRoot::StateGeneration)?
            .join(AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME);
        require_exact_handle_path(&handle, &expected_path)?;
        let canonical_path = canonical_handle_path(&handle)?;
        let canonical_path_sha256 = protected_blob_canonical_path_sha256(&canonical_path);
        let entries = enumerate_held_directory(&handle)?;
        if !entries.is_empty() {
            return Err(GenerationSealError(
                "authority_generation_seal_protected_blob_namespace_not_empty",
            ));
        }
        let file_security_sha256 = expected_security_digests(FinalizerSealTarget::RuntimeBlobFile)
            .map_err(GenerationSealError::from)?
            .1;
        let file_id = decode_hex_16(&object.file_id).ok_or(GenerationSealError(
            "authority_protected_blob_namespace_identity_invalid",
        ))?;
        let initial_empty_inventory_sha256 = protected_blob_empty_inventory_sha256(
            manifest.binding()?.generation_sha256(),
            object.volume_serial,
            file_id,
            canonical_path_sha256,
        );
        let mut value = Self {
            generation_sha256: manifest.binding()?.generation_sha256(),
            volume_serial: object.volume_serial,
            file_id,
            link_count: object.link_count,
            attributes: object.attributes,
            byte_length: object.byte_length,
            canonical_path_sha256,
            initial_empty_inventory_sha256,
            final_security_sha256: decode_required_digest(&object.final_security_sha256)?,
            file_security_sha256,
            runtime_access: RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS,
            share_access: 0,
            open_disposition: FILE_OPEN,
            file_create_access: RUNTIME_BLOB_FILE_AUTHORITY_ACCESS,
            file_read_access: RUNTIME_BLOB_FILE_READ_ACCESS,
            file_cleanup_access: RUNTIME_BLOB_FILE_CLEANUP_ACCESS,
            seal_sha256: [0; 32],
        };
        value.seal_sha256 = value.compute_seal_sha256();
        value.validate()?;
        Ok(value)
    }

    fn compute_seal_sha256(&self) -> Digest32 {
        let mut digest = Sha256::new();
        digest.update(PROTECTED_BLOB_NAMESPACE_SEAL_DOMAIN);
        digest.update(self.generation_sha256);
        digest
            .update((AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME.len() as u64).to_be_bytes());
        digest.update(AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME.as_bytes());
        digest.update(self.volume_serial.to_be_bytes());
        digest.update(self.file_id);
        digest.update(self.link_count.to_be_bytes());
        digest.update(self.attributes.to_be_bytes());
        digest.update(self.byte_length.to_be_bytes());
        digest.update(self.canonical_path_sha256);
        digest.update(self.initial_empty_inventory_sha256);
        digest.update(self.final_security_sha256);
        digest.update(self.file_security_sha256);
        digest.update(self.runtime_access.to_be_bytes());
        digest.update(self.share_access.to_be_bytes());
        digest.update(self.open_disposition.to_be_bytes());
        digest.update(self.file_create_access.to_be_bytes());
        digest.update(self.file_read_access.to_be_bytes());
        digest.update(self.file_cleanup_access.to_be_bytes());
        digest.finalize().into()
    }

    pub(super) fn validate(&self) -> Result<(), GenerationSealError> {
        let (_, expected_final) =
            expected_security_digests(FinalizerSealTarget::RuntimeBlobDirectory)
                .map_err(GenerationSealError::from)?;
        let (_, expected_file) = expected_security_digests(FinalizerSealTarget::RuntimeBlobFile)
            .map_err(GenerationSealError::from)?;
        if is_zero_digest(&self.generation_sha256)
            || self.volume_serial == 0
            || self.file_id.iter().all(|byte| *byte == 0)
            || self.link_count != 1
            || self.attributes & FILE_ATTRIBUTE_DIRECTORY == 0
            || self.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || self.byte_length != 0
            || is_zero_digest(&self.canonical_path_sha256)
            || self.initial_empty_inventory_sha256
                != protected_blob_empty_inventory_sha256(
                    self.generation_sha256,
                    self.volume_serial,
                    self.file_id,
                    self.canonical_path_sha256,
                )
            || self.final_security_sha256 != expected_final
            || self.file_security_sha256 != expected_file
            || self.runtime_access != RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS
            || self.share_access != 0
            || self.open_disposition != FILE_OPEN
            || self.file_create_access != RUNTIME_BLOB_FILE_AUTHORITY_ACCESS
            || self.file_read_access != RUNTIME_BLOB_FILE_READ_ACCESS
            || self.file_cleanup_access != RUNTIME_BLOB_FILE_CLEANUP_ACCESS
            || self.seal_sha256 != self.compute_seal_sha256()
        {
            return Err(GenerationSealError(
                "authority_protected_blob_namespace_seal_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn generation_sha256(self) -> Digest32 {
        self.generation_sha256
    }

    pub(super) fn volume_serial(self) -> u64 {
        self.volume_serial
    }

    pub(super) fn file_id(self) -> [u8; 16] {
        self.file_id
    }

    pub(super) fn link_count(self) -> u32 {
        self.link_count
    }

    pub(super) fn attributes(self) -> u32 {
        self.attributes
    }

    pub(super) fn byte_length(self) -> u64 {
        self.byte_length
    }

    pub(super) fn final_security_sha256(self) -> Digest32 {
        self.final_security_sha256
    }

    pub(super) fn canonical_path_sha256(self) -> Digest32 {
        self.canonical_path_sha256
    }

    pub(super) fn initial_empty_inventory_sha256(self) -> Digest32 {
        self.initial_empty_inventory_sha256
    }

    pub(super) fn file_security_sha256(self) -> Digest32 {
        self.file_security_sha256
    }

    pub(super) fn runtime_access(self) -> u32 {
        self.runtime_access
    }

    pub(super) fn share_access(self) -> u32 {
        self.share_access
    }

    pub(super) fn open_disposition(self) -> u32 {
        self.open_disposition
    }

    pub(super) fn file_create_access(self) -> u32 {
        self.file_create_access
    }

    pub(super) fn file_read_access(self) -> u32 {
        self.file_read_access
    }

    pub(super) fn file_cleanup_access(self) -> u32 {
        self.file_cleanup_access
    }

    pub(super) fn seal_sha256(self) -> Digest32 {
        self.seal_sha256
    }

    #[cfg(test)]
    pub(super) fn exact_test_fixture(generation_sha256: Digest32, seed: u8) -> Self {
        let (_, final_security_sha256) =
            expected_security_digests(FinalizerSealTarget::RuntimeBlobDirectory)
                .expect("runtime blob directory policy");
        let (_, file_security_sha256) =
            expected_security_digests(FinalizerSealTarget::RuntimeBlobFile)
                .expect("runtime blob file policy");
        let canonical_path_sha256 = [seed.wrapping_add(1).max(1); 32];
        let volume_serial = u64::from(seed) + 1;
        let file_id = [seed.max(1); 16];
        let mut value = Self {
            generation_sha256,
            volume_serial,
            file_id,
            link_count: 1,
            attributes: FILE_ATTRIBUTE_DIRECTORY,
            byte_length: 0,
            canonical_path_sha256,
            initial_empty_inventory_sha256: protected_blob_empty_inventory_sha256(
                generation_sha256,
                volume_serial,
                file_id,
                canonical_path_sha256,
            ),
            final_security_sha256,
            file_security_sha256,
            runtime_access: RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS,
            share_access: 0,
            open_disposition: FILE_OPEN,
            file_create_access: RUNTIME_BLOB_FILE_AUTHORITY_ACCESS,
            file_read_access: RUNTIME_BLOB_FILE_READ_ACCESS,
            file_cleanup_access: RUNTIME_BLOB_FILE_CLEANUP_ACCESS,
            seal_sha256: [0; 32],
        };
        value.seal_sha256 = value.compute_seal_sha256();
        value
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct GenerationSealReceipt {
    schema: String,
    manifest_sha256: String,
    objects: Vec<GenerationSealedObjectReceipt>,
    all_planned_objects_covered: bool,
    no_unlisted_generation_objects: bool,
    every_file_sealed_individually: bool,
    generation_directories_sealed_last: bool,
}

impl GenerationSealReceipt {
    fn new(
        manifest: &GenerationSealManifest,
        objects: Vec<GenerationSealedObjectReceipt>,
    ) -> Result<Self, GenerationSealError> {
        let value = Self {
            schema: GENERATION_SEAL_RECEIPT_SCHEMA.to_string(),
            manifest_sha256: hex_lower(&manifest.digest()?),
            objects,
            all_planned_objects_covered: true,
            no_unlisted_generation_objects: true,
            every_file_sealed_individually: true,
            generation_directories_sealed_last: true,
        };
        value.validate_against(manifest)?;
        Ok(value)
    }

    pub(super) fn parse_canonical(
        bytes: &[u8],
        manifest: &GenerationSealManifest,
    ) -> Result<Self, GenerationSealError> {
        if bytes.is_empty() || bytes.len() > MAX_SMALL_OBJECT_BYTES as usize {
            return Err(GenerationSealError(
                "authority_generation_seal_receipt_size_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes).map_err(|_| {
            GenerationSealError("authority_generation_seal_receipt_serialization_invalid")
        })?;
        value.validate_against(manifest)?;
        if value.canonical_bytes(manifest)? != bytes {
            return Err(GenerationSealError(
                "authority_generation_seal_receipt_not_canonical",
            ));
        }
        Ok(value)
    }

    pub(super) fn canonical_bytes(
        &self,
        manifest: &GenerationSealManifest,
    ) -> Result<Vec<u8>, GenerationSealError> {
        self.validate_against(manifest)?;
        serde_json::to_vec(self).map_err(|_| {
            GenerationSealError("authority_generation_seal_receipt_serialization_failed")
        })
    }

    pub(super) fn digest(
        &self,
        manifest: &GenerationSealManifest,
    ) -> Result<Digest32, GenerationSealError> {
        Ok(domain_digest(
            GENERATION_SEAL_RECEIPT_DOMAIN,
            &self.canonical_bytes(manifest)?,
        ))
    }

    fn validate_against(
        &self,
        manifest: &GenerationSealManifest,
    ) -> Result<(), GenerationSealError> {
        manifest.validate()?;
        if self.schema != GENERATION_SEAL_RECEIPT_SCHEMA
            || decode_hex_32(&self.manifest_sha256) != Some(manifest.digest()?)
            || !self.all_planned_objects_covered
            || !self.no_unlisted_generation_objects
            || !self.every_file_sealed_individually
            || !self.generation_directories_sealed_last
            || self.objects.len() != manifest.objects.len()
        {
            return Err(GenerationSealError(
                "authority_generation_seal_receipt_invalid",
            ));
        }
        let mut object_ids = BTreeSet::new();
        let mut saw_directory = false;
        for (index, planned) in manifest.objects.iter().enumerate() {
            let receipt = self.objects.get(index).ok_or(GenerationSealError(
                "authority_generation_seal_receipt_incomplete",
            ))?;
            receipt.validate_against(planned)?;
            if !object_ids.insert((receipt.volume_serial, receipt.file_id.clone())) {
                return Err(GenerationSealError(
                    "authority_generation_seal_receipt_identity_collision",
                ));
            }
            saw_directory |= receipt.object_type == SealedObjectKind::Directory;
            if saw_directory && receipt.object_type == SealedObjectKind::File {
                return Err(GenerationSealError(
                    "authority_generation_seal_receipt_order_invalid",
                ));
            }
        }
        Ok(())
    }
}

pub(super) fn generation_progress_relative_name(
    manifest_sha256: &Digest32,
    sequence: u32,
) -> String {
    format!(
        "generation-seal.{}.{sequence:02}.json",
        hex_lower(manifest_sha256)
    )
}

fn generation_progress_publishing_name(manifest_sha256: &Digest32, sequence: u32) -> String {
    format!(
        "{}{}",
        generation_progress_relative_name(manifest_sha256, sequence),
        GENERATION_PROGRESS_PRIVATE_SUFFIX
    )
}

fn validate_durable_progress_readback(
    readback: &GenerationSealDurableReadback,
    checkpoint: &GenerationSealProgressCheckpoint,
    manifest: &GenerationSealManifest,
    writer_closure: &PreSealWriterClosureReadback,
) -> Result<(), GenerationSealError> {
    let expected_root_identity_sha256 = manifest
        .binding()?
        .final_commit_store_root_identity_sha256();
    let expected_bytes = checkpoint.canonical_bytes(manifest, writer_closure)?;
    let expected_checkpoint_sha256 = checkpoint.digest(manifest, writer_closure)?;
    let expected_descriptor_sha256 =
        expected_publication_security_sha256(FinalizerPublicationSecurityPhase::PublishedImmutable)
            .map_err(GenerationSealError::from)?;
    if readback.relative_name
        != generation_progress_relative_name(&manifest.digest()?, checkpoint.sequence)
        || readback.checkpoint_bytes != expected_bytes
        || readback.checkpoint_sha256 != expected_checkpoint_sha256
        || readback.store_root_identity_sha256 != expected_root_identity_sha256
        || readback.volume_serial == 0
        || readback.file_id.iter().all(|byte| *byte == 0)
        || readback.link_count != 1
        || readback.byte_length != readback.checkpoint_bytes.len() as u64
        || readback.bytes_sha256 != sha256_bytes(&readback.checkpoint_bytes)
        || readback.descriptor_sha256 != expected_descriptor_sha256
        || !readback.create_new_no_replace
        || !readback.write_through
        || !readback.file_flushed
        || !readback.parent_directory_flushed
        || !readback.write_handle_closed_before_readback
        || !readback.read_only_reopen_verified
        || !readback.manifest_namespace_exhaustive
        || !readback.no_case_aliases_or_reparse_points
    {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_durable_readback_invalid",
        ));
    }
    Ok(())
}

fn validate_durable_progress_chain(
    chain: &[GenerationSealDurableReadback],
    manifest: &GenerationSealManifest,
    writer_closure: &PreSealWriterClosureReadback,
) -> Result<Vec<GenerationSealProgressCheckpoint>, GenerationSealError> {
    if chain.len() > GENERATION_SEAL_TERMINAL_SEQUENCE as usize + 1 {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_chain_fork",
        ));
    }
    let mut checkpoints = Vec::with_capacity(chain.len());
    let mut identities = BTreeSet::new();
    let mut root_identity = None;
    let mut previous_digest = None;
    let mut current_writer_epoch: Option<(Digest32, Digest32)> = None;
    let mut closed_writer_invocations = BTreeSet::new();
    for (index, readback) in chain.iter().enumerate() {
        let checkpoint = GenerationSealProgressCheckpoint::parse_canonical(
            &readback.checkpoint_bytes,
            manifest,
            writer_closure,
        )?;
        if checkpoint.sequence as usize != index
            || checkpoint
                .previous_checkpoint_sha256
                .as_deref()
                .and_then(decode_hex_32)
                != previous_digest
            || (index == 0 && checkpoint.previous_checkpoint_sha256.is_some())
            || (index > 0 && checkpoint.previous_checkpoint_sha256.is_none())
            || !identities.insert((readback.volume_serial, readback.file_id))
            || root_identity.is_some_and(|value| value != readback.store_root_identity_sha256)
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_chain_gap_or_fork",
            ));
        }
        let writer_epoch = (
            decode_required_digest(&checkpoint.writer_invocation_sha256)?,
            decode_required_digest(&checkpoint.writer_exclusion_capability_sha256)?,
        );
        if let Some(previous_epoch) = current_writer_epoch {
            if writer_epoch.0 == previous_epoch.0 {
                if writer_epoch.1 != previous_epoch.1 {
                    return Err(GenerationSealError(
                        "authority_generation_seal_checkpoint_writer_epoch_invalid",
                    ));
                }
            } else {
                if writer_epoch.1 == previous_epoch.1
                    || !closed_writer_invocations.insert(previous_epoch.0)
                    || closed_writer_invocations.contains(&writer_epoch.0)
                {
                    return Err(GenerationSealError(
                        "authority_generation_seal_checkpoint_writer_epoch_invalid",
                    ));
                }
                current_writer_epoch = Some(writer_epoch);
            }
        } else {
            current_writer_epoch = Some(writer_epoch);
        }
        validate_durable_progress_readback(readback, &checkpoint, manifest, writer_closure)?;
        root_identity.get_or_insert(readback.store_root_identity_sha256);
        previous_digest = Some(readback.checkpoint_sha256);
        checkpoints.push(checkpoint);
    }
    Ok(checkpoints)
}

fn recover_progress_chain(
    store: &mut dyn GenerationSealProgressStore,
    manifest: &GenerationSealManifest,
    writer_closure: &PreSealWriterClosureReadback,
) -> Result<
    (
        Vec<GenerationSealDurableReadback>,
        Vec<GenerationSealProgressCheckpoint>,
    ),
    GenerationSealError,
> {
    let chain = store.load_exact_chain(manifest.digest()?)?;
    let checkpoints = validate_durable_progress_chain(&chain, manifest, writer_closure)?;
    Ok((chain, checkpoints))
}

fn persist_progress_checkpoint(
    store: &mut dyn GenerationSealProgressStore,
    manifest: &GenerationSealManifest,
    writer_closure: &PreSealWriterClosureReadback,
    prior_chain: &[GenerationSealDurableReadback],
    checkpoint: &GenerationSealProgressCheckpoint,
) -> Result<Vec<GenerationSealDurableReadback>, GenerationSealError> {
    checkpoint.validate_against(manifest, writer_closure)?;
    if checkpoint.sequence as usize != prior_chain.len() {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_chain_gap_or_fork",
        ));
    }
    let expected_previous = prior_chain.last().map(|value| value.checkpoint_sha256);
    let bytes = checkpoint.canonical_bytes(manifest, writer_closure)?;
    let created =
        store.compare_and_swap_create_new(manifest.digest()?, expected_previous, &bytes)?;
    validate_durable_progress_readback(&created, checkpoint, manifest, writer_closure)?;
    let recovered = store.load_exact_chain(manifest.digest()?)?;
    let recovered_checkpoints =
        validate_durable_progress_chain(&recovered, manifest, writer_closure)?;
    if recovered.len() != prior_chain.len() + 1
        || recovered[..prior_chain.len()] != *prior_chain
        || recovered.last() != Some(&created)
        || recovered_checkpoints.last() != Some(checkpoint)
    {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_cas_readback_mismatch",
        ));
    }
    Ok(recovered)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum GenerationSealExecutionPhase {
    BeforeDurableIntent,
    RollForwardRequired {
        progress_tip_sha256: Option<Digest32>,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct GenerationSealExecutionError {
    source: GenerationSealError,
    phase: GenerationSealExecutionPhase,
}

impl GenerationSealExecutionError {
    fn before(source: GenerationSealError) -> Self {
        Self {
            source,
            phase: GenerationSealExecutionPhase::BeforeDurableIntent,
        }
    }

    fn roll_forward(source: GenerationSealError, progress_tip_sha256: Option<Digest32>) -> Self {
        Self {
            source,
            phase: GenerationSealExecutionPhase::RollForwardRequired {
                progress_tip_sha256,
            },
        }
    }

    pub(super) fn code(&self) -> &'static str {
        self.source.code()
    }

    pub(super) fn phase(&self) -> GenerationSealExecutionPhase {
        self.phase
    }
}

impl fmt::Display for GenerationSealExecutionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.source.fmt(formatter)
    }
}

impl std::error::Error for GenerationSealExecutionError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum GenerationSealRecoveryAction {
    SealFromExactStaging,
    AcceptExactSealed,
}

fn recovery_action_for_phase(
    checkpoint: &GenerationSealProgressCheckpoint,
    manifest: &GenerationSealManifest,
    writer_closure: &PreSealWriterClosureReadback,
    role: GenerationSealObjectRole,
    observed: FinalizerObservedSealPhase,
) -> Result<GenerationSealRecoveryAction, GenerationSealError> {
    checkpoint.validate_against(manifest, writer_closure)?;
    let intent = checkpoint
        .pending_intent
        .as_ref()
        .ok_or(GenerationSealError(
            "authority_generation_seal_progress_intent_missing",
        ))?;
    if intent.role != role || intent.object_index != checkpoint.completed_objects.len() {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_intent_mismatch",
        ));
    }
    Ok(match observed {
        FinalizerObservedSealPhase::ExactStaging => {
            GenerationSealRecoveryAction::SealFromExactStaging
        }
        FinalizerObservedSealPhase::ExactSealed => GenerationSealRecoveryAction::AcceptExactSealed,
    })
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct GenerationSealRestartReadback {
    schema: String,
    manifest_sha256: String,
    seal_receipt_sha256: String,
    reopened_object_count: usize,
    every_object_identity_bytes_and_security_exact: bool,
    generation_directories_still_exhaustive: bool,
    readback_sha256: String,
}

impl GenerationSealRestartReadback {
    fn from_verified_reopen(
        manifest: &GenerationSealManifest,
        receipt: &GenerationSealReceipt,
    ) -> Result<Self, GenerationSealError> {
        let manifest_sha256 = manifest.digest()?;
        let receipt_sha256 = receipt.digest(manifest)?;
        let readback_sha256 = restart_readback_digest(manifest, receipt)?;
        let value = Self {
            schema: GENERATION_SEAL_RESTART_READBACK_SCHEMA.to_string(),
            manifest_sha256: hex_lower(&manifest_sha256),
            seal_receipt_sha256: hex_lower(&receipt_sha256),
            reopened_object_count: receipt.objects.len(),
            every_object_identity_bytes_and_security_exact: true,
            generation_directories_still_exhaustive: true,
            readback_sha256: hex_lower(&readback_sha256),
        };
        value.validate(manifest, receipt)?;
        Ok(value)
    }

    fn validate(
        &self,
        manifest: &GenerationSealManifest,
        receipt: &GenerationSealReceipt,
    ) -> Result<(), GenerationSealError> {
        if self.schema != GENERATION_SEAL_RESTART_READBACK_SCHEMA
            || decode_hex_32(&self.manifest_sha256) != Some(manifest.digest()?)
            || decode_hex_32(&self.seal_receipt_sha256) != Some(receipt.digest(manifest)?)
            || self.reopened_object_count != manifest.objects.len()
            || !self.every_object_identity_bytes_and_security_exact
            || !self.generation_directories_still_exhaustive
            || decode_hex_32(&self.readback_sha256)
                != Some(restart_readback_digest(manifest, receipt)?)
        {
            return Err(GenerationSealError(
                "authority_generation_seal_restart_readback_invalid",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct GenerationSealTerminalProjection {
    binding: GenerationSealBinding,
    manifest_sha256: Digest32,
    generation_object_manifest_sha256: Digest32,
    writer_closure_readback_sha256: Digest32,
    sealing_invocation_sha256: Digest32,
    restart_invocation_sha256: Digest32,
    sealing_writer_exclusion_sha256: Digest32,
    seal_receipt_sha256: Digest32,
    terminal_checkpoint_sha256: Digest32,
    authenticated_progress_root_sha256: Digest32,
    restart_readback_sha256: Digest32,
    final_inventory_readback_sha256: Digest32,
    final_root_capabilities_sha256: Digest32,
    runner_policy_identity: GenerationSealedRunnerPolicyIdentity,
    protected_blob_namespace: ProtectedBlobNamespaceSealProjection,
    object_count: u32,
    terminal_sequence: u32,
    authorization_sha256: Digest32,
}

impl GenerationSealTerminalProjection {
    pub(super) fn binding(self) -> GenerationSealBinding {
        self.binding
    }

    pub(super) fn manifest_sha256(self) -> Digest32 {
        self.manifest_sha256
    }

    pub(super) fn generation_object_manifest_sha256(self) -> Digest32 {
        self.generation_object_manifest_sha256
    }

    pub(super) fn writer_closure_readback_sha256(self) -> Digest32 {
        self.writer_closure_readback_sha256
    }

    pub(super) fn seal_receipt_sha256(self) -> Digest32 {
        self.seal_receipt_sha256
    }

    pub(super) fn sealing_invocation_sha256(self) -> Digest32 {
        self.sealing_invocation_sha256
    }

    pub(super) fn restart_invocation_sha256(self) -> Digest32 {
        self.restart_invocation_sha256
    }

    pub(super) fn sealing_writer_exclusion_sha256(self) -> Digest32 {
        self.sealing_writer_exclusion_sha256
    }

    pub(super) fn terminal_checkpoint_sha256(self) -> Digest32 {
        self.terminal_checkpoint_sha256
    }

    pub(super) fn authenticated_progress_root_sha256(self) -> Digest32 {
        self.authenticated_progress_root_sha256
    }

    pub(super) fn restart_readback_sha256(self) -> Digest32 {
        self.restart_readback_sha256
    }

    pub(super) fn final_inventory_readback_sha256(self) -> Digest32 {
        self.final_inventory_readback_sha256
    }

    pub(super) fn final_root_capabilities_sha256(self) -> Digest32 {
        self.final_root_capabilities_sha256
    }

    pub(super) fn runner_policy_identity(self) -> GenerationSealedRunnerPolicyIdentity {
        self.runner_policy_identity
    }

    pub(super) fn protected_blob_namespace(self) -> ProtectedBlobNamespaceSealProjection {
        self.protected_blob_namespace
    }

    pub(super) fn object_count(self) -> u32 {
        self.object_count
    }

    pub(super) fn terminal_sequence(self) -> u32 {
        self.terminal_sequence
    }

    pub(super) fn authorization_sha256(self) -> Digest32 {
        self.authorization_sha256
    }

    fn compute_authorization_sha256(&self) -> Digest32 {
        let mut digest = Sha256::new();
        digest.update(GENERATION_SEAL_TERMINAL_AUTHORIZATION_DOMAIN);
        digest.update(self.binding.capsule_sha256());
        digest.update(self.binding.plan_sha256());
        digest.update(self.binding.generation_sha256());
        digest.update(self.binding.transaction_sha256());
        digest.update(self.binding.final_commit_store_root_identity_sha256());
        digest.update(self.manifest_sha256);
        digest.update(self.generation_object_manifest_sha256);
        digest.update(self.writer_closure_readback_sha256);
        digest.update(self.sealing_invocation_sha256);
        digest.update(self.restart_invocation_sha256);
        digest.update(self.sealing_writer_exclusion_sha256);
        digest.update(self.seal_receipt_sha256);
        digest.update(self.terminal_checkpoint_sha256);
        digest.update(self.authenticated_progress_root_sha256);
        digest.update(self.restart_readback_sha256);
        digest.update(self.final_inventory_readback_sha256);
        digest.update(self.final_root_capabilities_sha256);
        digest.update(self.runner_policy_identity.volume_serial().to_be_bytes());
        digest.update(self.runner_policy_identity.file_id());
        digest.update(self.runner_policy_identity.link_count().to_be_bytes());
        digest.update(self.runner_policy_identity.attributes().to_be_bytes());
        digest.update(self.protected_blob_namespace.seal_sha256());
        digest.update(self.object_count.to_be_bytes());
        digest.update(self.terminal_sequence.to_be_bytes());
        digest.finalize().into()
    }

    fn validate(&self) -> Result<(), GenerationSealError> {
        if self.binding.generation_object_manifest_sha256()
            != self.generation_object_manifest_sha256
            || self.authenticated_progress_root_sha256
                != self.binding.final_commit_store_root_identity_sha256()
            || [
                self.manifest_sha256,
                self.generation_object_manifest_sha256,
                self.writer_closure_readback_sha256,
                self.sealing_invocation_sha256,
                self.restart_invocation_sha256,
                self.sealing_writer_exclusion_sha256,
                self.seal_receipt_sha256,
                self.terminal_checkpoint_sha256,
                self.authenticated_progress_root_sha256,
                self.restart_readback_sha256,
                self.final_inventory_readback_sha256,
                self.final_root_capabilities_sha256,
            ]
            .iter()
            .any(is_zero_digest)
            || self.runner_policy_identity.validate().is_err()
            || self.protected_blob_namespace.validate().is_err()
            || self.protected_blob_namespace.generation_sha256() != self.binding.generation_sha256()
            || self.object_count != GENERATION_SEAL_OBJECT_COUNT as u32
            || self.terminal_sequence != GENERATION_SEAL_TERMINAL_SEQUENCE
            || self.authorization_sha256 != self.compute_authorization_sha256()
        {
            return Err(GenerationSealError(
                "authority_generation_seal_terminal_projection_invalid",
            ));
        }
        Ok(())
    }
}

/// Durable sealing is complete, but protocol SealComplete remains forbidden
/// until a later invocation performs the independent restart readback.
#[derive(Debug, PartialEq, Eq)]
pub(super) struct GenerationSealAwaitingRestart {
    binding: GenerationSealBinding,
    manifest_sha256: Digest32,
    sealing_invocation_sha256: Digest32,
    sealing_writer_exclusion_sha256: Digest32,
    seal_receipt_sha256: Digest32,
    terminal_checkpoint_sha256: Digest32,
    authenticated_progress_root_sha256: Digest32,
    independent_reopen_readback_sha256: Digest32,
    final_inventory_readback_sha256: Digest32,
    final_root_capabilities_sha256: Digest32,
}

impl GenerationSealAwaitingRestart {
    #[allow(clippy::too_many_arguments)]
    fn new(
        manifest: &GenerationSealManifest,
        writer_closure: &PreSealWriterClosureReadback,
        receipt: &GenerationSealReceipt,
        terminal_checkpoint: &GenerationSealProgressCheckpoint,
        progress_chain: &[GenerationSealDurableReadback],
        final_inventory: &GenerationSealInventory,
        final_root_capabilities_sha256: Digest32,
    ) -> Result<Self, GenerationSealError> {
        receipt.validate_against(manifest)?;
        terminal_checkpoint.validate_against(manifest, writer_closure)?;
        validate_inventory_against_receipt(manifest, final_inventory, receipt)?;
        let checkpoints =
            validate_durable_progress_chain(progress_chain, manifest, writer_closure)?;
        if !terminal_checkpoint.terminal
            || checkpoints.last() != Some(terminal_checkpoint)
            || terminal_checkpoint.completed_objects != receipt.objects
            || is_zero_digest(&final_root_capabilities_sha256)
        {
            return Err(GenerationSealError(
                "authority_generation_seal_awaiting_restart_invalid",
            ));
        }
        let authenticated_progress_root_sha256 = progress_chain
            .first()
            .map(|value| value.store_root_identity_sha256)
            .ok_or(GenerationSealError(
                "authority_generation_seal_progress_chain_gap_or_fork",
            ))?;
        if authenticated_progress_root_sha256
            != manifest
                .binding()?
                .final_commit_store_root_identity_sha256()
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_root_binding_mismatch",
            ));
        }
        Ok(Self {
            binding: manifest.binding()?,
            manifest_sha256: manifest.digest()?,
            sealing_invocation_sha256: decode_required_digest(
                &terminal_checkpoint.writer_invocation_sha256,
            )?,
            sealing_writer_exclusion_sha256: decode_required_digest(
                &terminal_checkpoint.writer_exclusion_capability_sha256,
            )?,
            seal_receipt_sha256: receipt.digest(manifest)?,
            terminal_checkpoint_sha256: terminal_checkpoint.digest(manifest, writer_closure)?,
            authenticated_progress_root_sha256,
            independent_reopen_readback_sha256: restart_readback_digest(manifest, receipt)?,
            final_inventory_readback_sha256: final_inventory_readback_digest(
                manifest,
                final_inventory,
            )?,
            final_root_capabilities_sha256,
        })
    }

    pub(super) fn binding(&self) -> GenerationSealBinding {
        self.binding
    }

    pub(super) fn manifest_sha256(&self) -> Digest32 {
        self.manifest_sha256
    }

    pub(super) fn sealing_invocation_sha256(&self) -> Digest32 {
        self.sealing_invocation_sha256
    }

    pub(super) fn seal_receipt_sha256(&self) -> Digest32 {
        self.seal_receipt_sha256
    }

    pub(super) fn terminal_checkpoint_sha256(&self) -> Digest32 {
        self.terminal_checkpoint_sha256
    }

    pub(super) fn authenticated_progress_root_sha256(&self) -> Digest32 {
        self.authenticated_progress_root_sha256
    }

    pub(super) fn independent_reopen_readback_sha256(&self) -> Digest32 {
        self.independent_reopen_readback_sha256
    }

    pub(super) fn final_inventory_readback_sha256(&self) -> Digest32 {
        self.final_inventory_readback_sha256
    }

    pub(super) fn final_root_capabilities_sha256(&self) -> Digest32 {
        self.final_root_capabilities_sha256
    }

    fn validate_durable_binding(
        &self,
        manifest: &GenerationSealManifest,
        writer_closure: &PreSealWriterClosureReadback,
        receipt: &GenerationSealReceipt,
        terminal_checkpoint: &GenerationSealProgressCheckpoint,
        progress_chain: &[GenerationSealDurableReadback],
    ) -> Result<(), GenerationSealError> {
        let progress_root = progress_chain
            .first()
            .map(|value| value.store_root_identity_sha256)
            .ok_or(GenerationSealError(
                "authority_generation_seal_progress_chain_gap_or_fork",
            ))?;
        if self.binding != manifest.binding()?
            || self.manifest_sha256 != manifest.digest()?
            || self.sealing_invocation_sha256
                != decode_required_digest(&terminal_checkpoint.writer_invocation_sha256)?
            || self.sealing_writer_exclusion_sha256
                != decode_required_digest(&terminal_checkpoint.writer_exclusion_capability_sha256)?
            || self.seal_receipt_sha256 != receipt.digest(manifest)?
            || self.terminal_checkpoint_sha256
                != terminal_checkpoint.digest(manifest, writer_closure)?
            || self.authenticated_progress_root_sha256 != progress_root
            || progress_root != self.binding.final_commit_store_root_identity_sha256()
            || self.independent_reopen_readback_sha256
                != restart_readback_digest(manifest, receipt)?
        {
            return Err(GenerationSealError(
                "authority_generation_seal_awaiting_restart_binding_mismatch",
            ));
        }
        Ok(())
    }
}

struct VerifiedGenerationSealRestart {
    readback: GenerationSealRestartReadback,
    final_inventory_readback_sha256: Digest32,
    final_root_capabilities_sha256: Digest32,
    runner_policy_identity: GenerationSealedRunnerPolicyIdentity,
    protected_blob_namespace: ProtectedBlobNamespaceSealProjection,
}

/// The only value that authorizes protocol SealComplete. Its fields are
/// private and it has no production generic or serialized constructor: it is
/// minted only after the authenticated 0..33 progress chain, exact 16-object
/// receipt, readonly restart verification, final inventory, and fixed root
/// capabilities all agree.
#[derive(Debug, PartialEq, Eq)]
pub(super) struct GenerationSealTerminalAuthorization {
    projection: GenerationSealTerminalProjection,
}

impl GenerationSealTerminalAuthorization {
    fn new(
        manifest: &GenerationSealManifest,
        writer_closure: &PreSealWriterClosureReadback,
        receipt: GenerationSealReceipt,
        terminal_checkpoint: &GenerationSealProgressCheckpoint,
        progress_chain: &[GenerationSealDurableReadback],
        restart_invocation_sha256: Digest32,
        verified_restart: VerifiedGenerationSealRestart,
    ) -> Result<Self, GenerationSealError> {
        receipt.validate_against(manifest)?;
        verified_restart.readback.validate(manifest, &receipt)?;
        let receipt_runner_policy_identity =
            runner_policy_identity_from_receipt(manifest, &receipt)?;
        if is_zero_digest(&verified_restart.final_inventory_readback_sha256)
            || is_zero_digest(&verified_restart.final_root_capabilities_sha256)
            || verified_restart.runner_policy_identity.validate().is_err()
            || verified_restart.runner_policy_identity != receipt_runner_policy_identity
            || verified_restart
                .protected_blob_namespace
                .validate()
                .is_err()
        {
            return Err(GenerationSealError(
                "authority_generation_seal_verified_restart_invalid",
            ));
        }
        terminal_checkpoint.validate_against(manifest, writer_closure)?;
        let checkpoints =
            validate_durable_progress_chain(progress_chain, manifest, writer_closure)?;
        if !terminal_checkpoint.terminal
            || terminal_checkpoint.sequence != GENERATION_SEAL_TERMINAL_SEQUENCE
            || terminal_checkpoint.completed_objects != receipt.objects
            || checkpoints.last() != Some(terminal_checkpoint)
            || progress_chain.len() != GENERATION_SEAL_TERMINAL_SEQUENCE as usize + 1
        {
            return Err(GenerationSealError(
                "authority_generation_seal_terminal_checkpoint_invalid",
            ));
        }
        let progress_root = progress_chain
            .first()
            .map(|value| value.store_root_identity_sha256)
            .ok_or(GenerationSealError(
                "authority_generation_seal_progress_chain_gap_or_fork",
            ))?;
        if progress_chain
            .iter()
            .any(|value| value.store_root_identity_sha256 != progress_root)
            || progress_root
                != manifest
                    .binding()?
                    .final_commit_store_root_identity_sha256()
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_root_drift",
            ));
        }
        let binding = manifest.binding()?;
        let sealing_invocation_sha256 =
            decode_required_digest(&terminal_checkpoint.writer_invocation_sha256)?;
        let sealing_writer_exclusion_sha256 =
            decode_required_digest(&terminal_checkpoint.writer_exclusion_capability_sha256)?;
        if is_zero_digest(&restart_invocation_sha256)
            || restart_invocation_sha256 == sealing_invocation_sha256
        {
            return Err(GenerationSealError(
                "authority_generation_seal_restart_invocation_not_distinct",
            ));
        }
        let mut projection = GenerationSealTerminalProjection {
            binding,
            manifest_sha256: manifest.digest()?,
            generation_object_manifest_sha256: binding.generation_object_manifest_sha256(),
            writer_closure_readback_sha256: writer_closure.digest(manifest)?,
            sealing_invocation_sha256,
            restart_invocation_sha256,
            sealing_writer_exclusion_sha256,
            seal_receipt_sha256: receipt.digest(manifest)?,
            terminal_checkpoint_sha256: terminal_checkpoint.digest(manifest, writer_closure)?,
            authenticated_progress_root_sha256: progress_root,
            restart_readback_sha256: decode_required_digest(
                &verified_restart.readback.readback_sha256,
            )?,
            final_inventory_readback_sha256: verified_restart.final_inventory_readback_sha256,
            final_root_capabilities_sha256: verified_restart.final_root_capabilities_sha256,
            runner_policy_identity: verified_restart.runner_policy_identity,
            protected_blob_namespace: verified_restart.protected_blob_namespace,
            object_count: GENERATION_SEAL_OBJECT_COUNT as u32,
            terminal_sequence: GENERATION_SEAL_TERMINAL_SEQUENCE,
            authorization_sha256: [0; 32],
        };
        projection.authorization_sha256 = projection.compute_authorization_sha256();
        projection.validate()?;
        Ok(Self { projection })
    }

    pub(super) fn projection(&self) -> GenerationSealTerminalProjection {
        self.projection
    }

    #[cfg(test)]
    #[allow(clippy::too_many_arguments)]
    pub(super) fn exact_test_fixture(
        binding: GenerationSealBinding,
        manifest_sha256: Digest32,
        writer_closure_readback_sha256: Digest32,
        seal_receipt_sha256: Digest32,
        terminal_checkpoint_sha256: Digest32,
        authenticated_progress_root_sha256: Digest32,
        restart_readback_sha256: Digest32,
        final_inventory_readback_sha256: Digest32,
        final_root_capabilities_sha256: Digest32,
    ) -> Result<Self, GenerationSealError> {
        let mut projection = GenerationSealTerminalProjection {
            binding,
            manifest_sha256,
            generation_object_manifest_sha256: binding.generation_object_manifest_sha256(),
            writer_closure_readback_sha256,
            sealing_invocation_sha256: [0x67; 32],
            restart_invocation_sha256: [0x68; 32],
            sealing_writer_exclusion_sha256: [0x69; 32],
            seal_receipt_sha256,
            terminal_checkpoint_sha256,
            authenticated_progress_root_sha256,
            restart_readback_sha256,
            final_inventory_readback_sha256,
            final_root_capabilities_sha256,
            runner_policy_identity: GenerationSealedRunnerPolicyIdentity::exact_test_fixture(0x6b),
            protected_blob_namespace: ProtectedBlobNamespaceSealProjection::exact_test_fixture(
                binding.generation_sha256(),
                0x6c,
            ),
            object_count: GENERATION_SEAL_OBJECT_COUNT as u32,
            terminal_sequence: GENERATION_SEAL_TERMINAL_SEQUENCE,
            authorization_sha256: [0; 32],
        };
        projection.authorization_sha256 = projection.compute_authorization_sha256();
        projection.validate()?;
        Ok(Self { projection })
    }
}

/// A validated nonterminal chain tip.  This is evidence that sealing must
/// roll forward; it is deliberately not a mutation capability.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct GenerationSealPartialResume {
    binding: GenerationSealBinding,
    manifest_sha256: Digest32,
    writer_closure_readback_sha256: Digest32,
    authenticated_progress_root_sha256: Digest32,
    progress_tip_sha256: Digest32,
    writer_invocation_sha256: Digest32,
    writer_exclusion_sha256: Digest32,
    sequence: u32,
    completed_object_count: u32,
    pending_role: Option<GenerationSealObjectRole>,
}

impl GenerationSealPartialResume {
    fn from_validated_chain(
        manifest: &GenerationSealManifest,
        writer_closure: &PreSealWriterClosureReadback,
        progress_chain: &[GenerationSealDurableReadback],
        checkpoints: &[GenerationSealProgressCheckpoint],
    ) -> Result<Self, GenerationSealError> {
        let checkpoint = checkpoints.last().ok_or(GenerationSealError(
            "authority_generation_seal_partial_resume_empty",
        ))?;
        if checkpoint.terminal
            || checkpoint.sequence >= GENERATION_SEAL_TERMINAL_SEQUENCE
            || progress_chain.len() != checkpoints.len()
            || progress_chain.len() != checkpoint.sequence as usize + 1
        {
            return Err(GenerationSealError(
                "authority_generation_seal_partial_resume_invalid",
            ));
        }
        let progress_root = progress_chain[0].store_root_identity_sha256;
        if is_zero_digest(&progress_root)
            || progress_chain
                .iter()
                .any(|value| value.store_root_identity_sha256 != progress_root)
            || progress_root
                != manifest
                    .binding()?
                    .final_commit_store_root_identity_sha256()
        {
            return Err(GenerationSealError(
                "authority_generation_seal_progress_root_drift",
            ));
        }
        Ok(Self {
            binding: manifest.binding()?,
            manifest_sha256: manifest.digest()?,
            writer_closure_readback_sha256: writer_closure.digest(manifest)?,
            authenticated_progress_root_sha256: progress_root,
            progress_tip_sha256: progress_chain
                .last()
                .map(|value| value.checkpoint_sha256)
                .ok_or(GenerationSealError(
                    "authority_generation_seal_partial_resume_empty",
                ))?,
            writer_invocation_sha256: decode_required_digest(&checkpoint.writer_invocation_sha256)?,
            writer_exclusion_sha256: decode_required_digest(
                &checkpoint.writer_exclusion_capability_sha256,
            )?,
            sequence: checkpoint.sequence,
            completed_object_count: u32::try_from(checkpoint.completed_objects.len()).map_err(
                |_| GenerationSealError("authority_generation_seal_partial_resume_invalid"),
            )?,
            pending_role: checkpoint.pending_intent.as_ref().map(|intent| intent.role),
        })
    }

    pub(super) fn phase(&self) -> GenerationSealExecutionPhase {
        GenerationSealExecutionPhase::RollForwardRequired {
            progress_tip_sha256: Some(self.progress_tip_sha256),
        }
    }

    #[cfg(test)]
    fn writer_epoch(&self) -> (Digest32, Digest32) {
        (self.writer_invocation_sha256, self.writer_exclusion_sha256)
    }
}

/// One fail-closed recovery classifier covers a truly empty namespace, a
/// validated roll-forward tip, a terminal checkpoint that still needs a real
/// process restart, and the only protocol authorization produced after that
/// restart.
#[derive(Debug, PartialEq, Eq)]
pub(super) enum GenerationSealResumeState {
    Empty,
    Partial(GenerationSealPartialResume),
    AwaitingRestart(GenerationSealAwaitingRestart),
    TerminalAuthorized(GenerationSealTerminalAuthorization),
}

fn restart_readback_digest(
    manifest: &GenerationSealManifest,
    receipt: &GenerationSealReceipt,
) -> Result<Digest32, GenerationSealError> {
    let mut digest = Sha256::new();
    digest.update(GENERATION_SEAL_RESTART_DOMAIN);
    digest.update(manifest.digest()?);
    digest.update(receipt.digest(manifest)?);
    digest.update((receipt.objects.len() as u64).to_be_bytes());
    for object in &receipt.objects {
        digest.update(object.volume_serial.to_be_bytes());
        digest.update(decode_hex_16(&object.file_id).ok_or(GenerationSealError(
            "authority_generation_seal_receipt_identity_invalid",
        ))?);
        digest.update(object.link_count.to_be_bytes());
        digest.update(object.byte_length.to_be_bytes());
        digest.update(object.attributes.to_be_bytes());
        digest.update(decode_required_digest(&object.final_security_sha256)?);
        if let Some(bytes_sha256) = &object.bytes_sha256 {
            digest.update(decode_required_digest(bytes_sha256)?);
        }
    }
    Ok(digest.finalize().into())
}

struct AuthenticatedGenerationSealRootHandle {
    handle: OwnedHandle,
    capability: FinalizerRootCapabilityReadback,
}

pub(super) struct NativeGenerationSealRootHandles {
    binary_generation: AuthenticatedGenerationSealRootHandle,
    state_generation: AuthenticatedGenerationSealRootHandle,
    activations_namespace: AuthenticatedGenerationSealRootHandle,
    worker_nonce_namespace: AuthenticatedGenerationSealRootHandle,
    candidate_consumption_namespace: AuthenticatedGenerationSealRootHandle,
}

impl NativeGenerationSealRootHandles {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn authenticate(
        layout: &AuthorityLayout,
        manifest: &GenerationSealManifest,
        binary_generation: OwnedHandle,
        state_generation: OwnedHandle,
        activations_namespace: OwnedHandle,
        worker_nonce_namespace: OwnedHandle,
        candidate_consumption_namespace: OwnedHandle,
    ) -> Result<Self, GenerationSealError> {
        manifest.validate()?;
        let generation = manifest.binding()?.generation_sha256();
        Ok(Self {
            binary_generation: authenticate_generation_seal_root_handle(
                binary_generation,
                &layout
                    .generation_binary_root(&generation)
                    .map_err(|_| GenerationSealError("authority_generation_seal_path_invalid"))?,
                FinalizerRootCapabilityKind::GenerationDirectory,
            )?,
            state_generation: authenticate_generation_seal_root_handle(
                state_generation,
                &layout
                    .generation_state_root(&generation)
                    .map_err(|_| GenerationSealError("authority_generation_seal_path_invalid"))?,
                FinalizerRootCapabilityKind::GenerationDirectory,
            )?,
            activations_namespace: authenticate_generation_seal_root_handle(
                activations_namespace,
                &layout.activations_root(),
                FinalizerRootCapabilityKind::ActivationManifestNamespace,
            )?,
            worker_nonce_namespace: authenticate_generation_seal_root_handle(
                worker_nonce_namespace,
                &layout.worker_nonce_root(),
                FinalizerRootCapabilityKind::WorkerNonceNamespace,
            )?,
            candidate_consumption_namespace: authenticate_generation_seal_root_handle(
                candidate_consumption_namespace,
                &layout.candidate_consumption_root(),
                FinalizerRootCapabilityKind::CandidateConsumptionNamespace,
            )?,
        })
    }

    fn root(
        &self,
        root: GenerationSealRoot,
    ) -> Result<&AuthenticatedGenerationSealRootHandle, GenerationSealError> {
        Ok(match root {
            GenerationSealRoot::BinaryGeneration => &self.binary_generation,
            GenerationSealRoot::StateGeneration => &self.state_generation,
            GenerationSealRoot::ActivationsNamespace => &self.activations_namespace,
            GenerationSealRoot::WorkerNonceNamespace => &self.worker_nonce_namespace,
            GenerationSealRoot::CandidateConsumptionNamespace => {
                &self.candidate_consumption_namespace
            }
        })
    }

    fn expected_path(
        &self,
        layout: &AuthorityLayout,
        manifest: &GenerationSealManifest,
        root: GenerationSealRoot,
    ) -> Result<PathBuf, GenerationSealError> {
        let generation = manifest.binding()?.generation_sha256();
        match root {
            GenerationSealRoot::BinaryGeneration => layout
                .generation_binary_root(&generation)
                .map_err(|_| GenerationSealError("authority_generation_seal_path_invalid")),
            GenerationSealRoot::StateGeneration => layout
                .generation_state_root(&generation)
                .map_err(|_| GenerationSealError("authority_generation_seal_path_invalid")),
            GenerationSealRoot::ActivationsNamespace => Ok(layout.activations_root()),
            GenerationSealRoot::WorkerNonceNamespace => Ok(layout.worker_nonce_root()),
            GenerationSealRoot::CandidateConsumptionNamespace => {
                Ok(layout.candidate_consumption_root())
            }
        }
    }

    fn revalidate(
        &self,
        layout: &AuthorityLayout,
        manifest: &GenerationSealManifest,
    ) -> Result<(), GenerationSealError> {
        manifest.validate()?;
        for root in [
            GenerationSealRoot::BinaryGeneration,
            GenerationSealRoot::StateGeneration,
            GenerationSealRoot::ActivationsNamespace,
            GenerationSealRoot::WorkerNonceNamespace,
            GenerationSealRoot::CandidateConsumptionNamespace,
        ] {
            let expected = self.root(root)?;
            require_exact_handle_path(
                &expected.handle,
                self.expected_path(layout, manifest, root)?.as_path(),
            )?;
            let current = authenticate_finalizer_root_capability(
                &expected.handle,
                root_capability_kind(root),
            )
            .map_err(GenerationSealError::from)?;
            if current != expected.capability {
                return Err(GenerationSealError(
                    "authority_generation_seal_root_capability_drift",
                ));
            }
        }
        Ok(())
    }
}

fn authenticate_generation_seal_root_handle(
    handle: OwnedHandle,
    expected_path: &Path,
    kind: FinalizerRootCapabilityKind,
) -> Result<AuthenticatedGenerationSealRootHandle, GenerationSealError> {
    require_exact_handle_path(&handle, expected_path)?;
    let capability =
        authenticate_finalizer_root_capability(&handle, kind).map_err(GenerationSealError::from)?;
    Ok(AuthenticatedGenerationSealRootHandle { handle, capability })
}

struct NativeGenerationSealRoots {
    binary_generation: Option<OwnedHandle>,
    state_generation: Option<OwnedHandle>,
    activations_namespace: OwnedHandle,
    worker_nonce_namespace: OwnedHandle,
    candidate_consumption_namespace: OwnedHandle,
    paths: BTreeMap<GenerationSealRoot, PathBuf>,
    expected_capabilities: BTreeMap<GenerationSealRoot, FinalizerRootCapabilityReadback>,
}

impl NativeGenerationSealRoots {
    fn new(
        layout: &AuthorityLayout,
        manifest: &GenerationSealManifest,
        handles: NativeGenerationSealRootHandles,
    ) -> Result<Self, GenerationSealError> {
        let generation = manifest.binding()?.generation_sha256();
        let paths = BTreeMap::from([
            (
                GenerationSealRoot::BinaryGeneration,
                layout
                    .generation_binary_root(&generation)
                    .map_err(|_| GenerationSealError("authority_generation_seal_path_invalid"))?,
            ),
            (
                GenerationSealRoot::StateGeneration,
                layout
                    .generation_state_root(&generation)
                    .map_err(|_| GenerationSealError("authority_generation_seal_path_invalid"))?,
            ),
            (
                GenerationSealRoot::ActivationsNamespace,
                layout.activations_root(),
            ),
            (
                GenerationSealRoot::WorkerNonceNamespace,
                layout.worker_nonce_root(),
            ),
            (
                GenerationSealRoot::CandidateConsumptionNamespace,
                layout.candidate_consumption_root(),
            ),
        ]);
        for (root, handle) in [
            (
                GenerationSealRoot::BinaryGeneration,
                &handles.binary_generation,
            ),
            (
                GenerationSealRoot::StateGeneration,
                &handles.state_generation,
            ),
            (
                GenerationSealRoot::ActivationsNamespace,
                &handles.activations_namespace,
            ),
            (
                GenerationSealRoot::WorkerNonceNamespace,
                &handles.worker_nonce_namespace,
            ),
            (
                GenerationSealRoot::CandidateConsumptionNamespace,
                &handles.candidate_consumption_namespace,
            ),
        ] {
            require_exact_handle_path(&handle.handle, &paths[&root])?;
            let expected_kind = match root {
                GenerationSealRoot::BinaryGeneration | GenerationSealRoot::StateGeneration => {
                    FinalizerRootCapabilityKind::GenerationDirectory
                }
                GenerationSealRoot::ActivationsNamespace => {
                    FinalizerRootCapabilityKind::ActivationManifestNamespace
                }
                GenerationSealRoot::WorkerNonceNamespace => {
                    FinalizerRootCapabilityKind::WorkerNonceNamespace
                }
                GenerationSealRoot::CandidateConsumptionNamespace => {
                    FinalizerRootCapabilityKind::CandidateConsumptionNamespace
                }
            };
            let current_capability =
                authenticate_finalizer_root_capability(&handle.handle, expected_kind)
                    .map_err(GenerationSealError::from)?;
            if current_capability != handle.capability
                || handle.capability.kind() != expected_kind
                || handle.capability.granted_access() == 0
                || is_zero_digest(&handle.capability.security_sha256())
                || current_capability.identity() != handle.capability.identity()
                || match root {
                    GenerationSealRoot::BinaryGeneration | GenerationSealRoot::StateGeneration => {
                        !matches!(
                            handle.capability.security_phase(),
                            FinalizerRootSecurityPhase::ExactStaging
                                | FinalizerRootSecurityPhase::ExactSealed
                        )
                    }
                    GenerationSealRoot::ActivationsNamespace
                    | GenerationSealRoot::WorkerNonceNamespace
                    | GenerationSealRoot::CandidateConsumptionNamespace => {
                        handle.capability.security_phase()
                            != FinalizerRootSecurityPhase::ExactNamespace
                    }
                }
            {
                return Err(GenerationSealError(
                    "authority_generation_seal_root_capability_mismatch",
                ));
            }
        }
        let expected_capabilities = BTreeMap::from([
            (
                GenerationSealRoot::BinaryGeneration,
                handles.binary_generation.capability,
            ),
            (
                GenerationSealRoot::StateGeneration,
                handles.state_generation.capability,
            ),
            (
                GenerationSealRoot::ActivationsNamespace,
                handles.activations_namespace.capability,
            ),
            (
                GenerationSealRoot::WorkerNonceNamespace,
                handles.worker_nonce_namespace.capability,
            ),
            (
                GenerationSealRoot::CandidateConsumptionNamespace,
                handles.candidate_consumption_namespace.capability,
            ),
        ]);
        Ok(Self {
            binary_generation: Some(handles.binary_generation.handle),
            state_generation: Some(handles.state_generation.handle),
            activations_namespace: handles.activations_namespace.handle,
            worker_nonce_namespace: handles.worker_nonce_namespace.handle,
            candidate_consumption_namespace: handles.candidate_consumption_namespace.handle,
            paths,
            expected_capabilities,
        })
    }

    fn root(&self, root: GenerationSealRoot) -> Result<&OwnedHandle, GenerationSealError> {
        let handle = match root {
            GenerationSealRoot::BinaryGeneration => self.binary_generation.as_ref(),
            GenerationSealRoot::StateGeneration => self.state_generation.as_ref(),
            GenerationSealRoot::ActivationsNamespace => Some(&self.activations_namespace),
            GenerationSealRoot::WorkerNonceNamespace => Some(&self.worker_nonce_namespace),
            GenerationSealRoot::CandidateConsumptionNamespace => {
                Some(&self.candidate_consumption_namespace)
            }
        }
        .ok_or(GenerationSealError(
            "authority_generation_seal_root_handle_missing",
        ))?;
        self.validate_current_root_capability(root, handle)?;
        Ok(handle)
    }

    fn take_generation_root(
        &mut self,
        root: GenerationSealRoot,
    ) -> Result<OwnedHandle, GenerationSealError> {
        let handle = match root {
            GenerationSealRoot::BinaryGeneration => self.binary_generation.as_ref(),
            GenerationSealRoot::StateGeneration => self.state_generation.as_ref(),
            _ => None,
        }
        .ok_or(GenerationSealError(
            "authority_generation_seal_root_handle_missing",
        ))?;
        self.validate_current_root_capability(root, handle)?;
        match root {
            GenerationSealRoot::BinaryGeneration => self.binary_generation.take(),
            GenerationSealRoot::StateGeneration => self.state_generation.take(),
            _ => None,
        }
        .ok_or(GenerationSealError(
            "authority_generation_seal_root_handle_missing",
        ))
    }

    fn replace_generation_root(
        &mut self,
        root: GenerationSealRoot,
        handle: OwnedHandle,
    ) -> Result<(), GenerationSealError> {
        if match root {
            GenerationSealRoot::BinaryGeneration => self.binary_generation.is_some(),
            GenerationSealRoot::StateGeneration => self.state_generation.is_some(),
            _ => {
                return Err(GenerationSealError(
                    "authority_generation_seal_root_handle_invalid",
                ));
            }
        } {
            return Err(GenerationSealError(
                "authority_generation_seal_root_handle_not_consumed",
            ));
        }
        require_exact_handle_path(&handle, self.path(root)?)?;
        let capability = authenticate_finalizer_root_capability(
            &handle,
            FinalizerRootCapabilityKind::GenerationDirectory,
        )
        .map_err(GenerationSealError::from)?;
        if capability.security_phase() != FinalizerRootSecurityPhase::ExactSealed {
            return Err(GenerationSealError(
                "authority_generation_seal_replacement_root_not_sealed",
            ));
        }
        self.expected_capabilities.insert(root, capability);
        match root {
            GenerationSealRoot::BinaryGeneration => self.binary_generation = Some(handle),
            GenerationSealRoot::StateGeneration => self.state_generation = Some(handle),
            _ => unreachable!("generation root validated above"),
        }
        Ok(())
    }

    fn path(&self, root: GenerationSealRoot) -> Result<&Path, GenerationSealError> {
        self.paths
            .get(&root)
            .map(PathBuf::as_path)
            .ok_or(GenerationSealError(
                "authority_generation_seal_path_invalid",
            ))
    }

    fn validate_current_root_capability(
        &self,
        root: GenerationSealRoot,
        handle: &OwnedHandle,
    ) -> Result<FinalizerRootCapabilityReadback, GenerationSealError> {
        require_exact_handle_path(handle, self.path(root)?)?;
        let kind = root_capability_kind(root);
        let actual = authenticate_finalizer_root_capability(handle, kind)
            .map_err(GenerationSealError::from)?;
        if self.expected_capabilities.get(&root) != Some(&actual) {
            return Err(GenerationSealError(
                "authority_generation_seal_root_capability_drift",
            ));
        }
        Ok(actual)
    }

    fn final_capabilities_sha256(&self) -> Result<Digest32, GenerationSealError> {
        let mut digest = Sha256::new();
        digest.update(b"vrcforge-authority-generation-seal-final-root-capabilities-v1\0");
        for (index, root) in [
            GenerationSealRoot::BinaryGeneration,
            GenerationSealRoot::StateGeneration,
            GenerationSealRoot::ActivationsNamespace,
            GenerationSealRoot::WorkerNonceNamespace,
            GenerationSealRoot::CandidateConsumptionNamespace,
        ]
        .iter()
        .copied()
        .enumerate()
        {
            let handle = match root {
                GenerationSealRoot::BinaryGeneration => self.binary_generation.as_ref(),
                GenerationSealRoot::StateGeneration => self.state_generation.as_ref(),
                GenerationSealRoot::ActivationsNamespace => Some(&self.activations_namespace),
                GenerationSealRoot::WorkerNonceNamespace => Some(&self.worker_nonce_namespace),
                GenerationSealRoot::CandidateConsumptionNamespace => {
                    Some(&self.candidate_consumption_namespace)
                }
            }
            .ok_or(GenerationSealError(
                "authority_generation_seal_root_handle_missing",
            ))?;
            let capability = self.validate_current_root_capability(root, handle)?;
            let expected_phase = match root {
                GenerationSealRoot::BinaryGeneration | GenerationSealRoot::StateGeneration => {
                    FinalizerRootSecurityPhase::ExactSealed
                }
                GenerationSealRoot::ActivationsNamespace
                | GenerationSealRoot::WorkerNonceNamespace
                | GenerationSealRoot::CandidateConsumptionNamespace => {
                    FinalizerRootSecurityPhase::ExactNamespace
                }
            };
            if capability.security_phase() != expected_phase {
                return Err(GenerationSealError(
                    "authority_generation_seal_final_root_phase_invalid",
                ));
            }
            let identity = capability.identity();
            let path_words = self
                .path(root)?
                .as_os_str()
                .encode_wide()
                .collect::<Vec<_>>();
            digest.update((index as u32).to_be_bytes());
            digest.update((path_words.len() as u64).to_be_bytes());
            for word in path_words {
                digest.update(word.to_be_bytes());
            }
            digest.update(identity.volume_serial().to_be_bytes());
            digest.update(identity.file_id());
            digest.update(identity.link_count().to_be_bytes());
            digest.update(capability.granted_access().to_be_bytes());
            digest.update(capability.security_sha256());
        }
        Ok(digest.finalize().into())
    }

    fn inventory(&self) -> Result<GenerationSealInventory, GenerationSealError> {
        let mut inventory = GenerationSealInventory::default();
        for root in [
            GenerationSealRoot::BinaryGeneration,
            GenerationSealRoot::StateGeneration,
            GenerationSealRoot::ActivationsNamespace,
            GenerationSealRoot::WorkerNonceNamespace,
            GenerationSealRoot::CandidateConsumptionNamespace,
        ] {
            inventory.insert(root, enumerate_held_directory(self.root(root)?)?);
        }
        Ok(inventory)
    }
}

pub(super) fn seal_complete_generation(
    layout: &AuthorityLayout,
    manifest: &GenerationSealManifest,
    invocation: &NativeGenerationSealInvocation,
    writer_closure_capability: &PreSealWriterClosureCapability,
    progress_store: &mut AuthenticatedNativeGenerationSealProgressStore,
    handles: NativeGenerationSealRootHandles,
) -> Result<GenerationSealAwaitingRestart, GenerationSealExecutionError> {
    if !GENERATION_SEAL_PRODUCTION_ENABLED {
        return Err(GenerationSealExecutionError::before(GenerationSealError(
            "authority_generation_seal_production_disabled",
        )));
    }
    let writer = writer_closure_capability
        .checkpoint_writer_authorization(manifest, invocation)
        .map_err(GenerationSealExecutionError::before)?;
    seal_complete_generation_enabled(
        layout,
        manifest,
        &writer_closure_capability.readback,
        writer,
        progress_store.inner.as_mut(),
        handles,
    )
}

fn seal_complete_generation_enabled(
    layout: &AuthorityLayout,
    manifest: &GenerationSealManifest,
    current_writer_closure: &PreSealWriterClosureReadback,
    writer: CheckpointWriterAuthorization,
    progress_store: &mut dyn GenerationSealProgressStore,
    handles: NativeGenerationSealRootHandles,
) -> Result<GenerationSealAwaitingRestart, GenerationSealExecutionError> {
    manifest
        .validate()
        .map_err(GenerationSealExecutionError::before)?;
    current_writer_closure
        .validate_against(manifest)
        .map_err(GenerationSealExecutionError::before)?;
    writer
        .validate()
        .map_err(GenerationSealExecutionError::before)?;
    if decode_required_digest(&current_writer_closure.observer_invocation_sha256)
        .map_err(GenerationSealExecutionError::before)?
        != writer.invocation_sha256
    {
        return Err(GenerationSealExecutionError::before(GenerationSealError(
            "authority_generation_seal_checkpoint_writer_invalid",
        )));
    }

    // An unreadable progress namespace is conservatively a roll-forward case:
    // an earlier create-new intent may exist even when its readback cannot be
    // authenticated by this invocation.
    let mut progress_chain = progress_store
        .load_exact_chain(
            manifest
                .digest()
                .map_err(GenerationSealExecutionError::before)?,
        )
        .map_err(|error| GenerationSealExecutionError::roll_forward(error, None))?;
    let origin_writer_closure = if let Some(genesis) = progress_chain.first() {
        let untrusted: GenerationSealProgressCheckpoint =
            serde_json::from_slice(&genesis.checkpoint_bytes).map_err(|_| {
                GenerationSealExecutionError::roll_forward(
                    GenerationSealError("authority_generation_seal_progress_serialization_invalid"),
                    None,
                )
            })?;
        untrusted.writer_closure_readback
    } else {
        current_writer_closure.clone()
    };
    origin_writer_closure
        .validate_against(manifest)
        .map_err(|error| GenerationSealExecutionError::roll_forward(error, None))?;
    let writer_closure = &origin_writer_closure;
    let checkpoints = validate_durable_progress_chain(&progress_chain, manifest, writer_closure)
        .map_err(|error| GenerationSealExecutionError::roll_forward(error, None))?;
    let mut current = if let Some(checkpoint) = checkpoints.last() {
        checkpoint.clone()
    } else {
        let genesis = GenerationSealProgressCheckpoint::genesis(manifest, writer_closure, writer)
            .map_err(GenerationSealExecutionError::before)?;
        progress_chain = persist_progress_checkpoint(
            progress_store,
            manifest,
            writer_closure,
            &progress_chain,
            &genesis,
        )
        .map_err(GenerationSealExecutionError::before)?;
        genesis
    };

    let mut roots = NativeGenerationSealRoots::new(layout, manifest, handles).map_err(|error| {
        classify_generation_seal_failure(error, &current, manifest, writer_closure)
    })?;
    let mut inventory = roots.inventory().map_err(|error| {
        classify_generation_seal_failure(error, &current, manifest, writer_closure)
    })?;
    inventory.validate_against(manifest).map_err(|error| {
        classify_generation_seal_failure(error, &current, manifest, writer_closure)
    })?;

    for object_index in 0..manifest.objects.len() {
        let planned = &manifest.objects[object_index];
        if object_index == 9 {
            inventory = roots.inventory().map_err(|error| {
                classify_generation_seal_failure(error, &current, manifest, writer_closure)
            })?;
            inventory.validate_against(manifest).map_err(|error| {
                classify_generation_seal_failure(error, &current, manifest, writer_closure)
            })?;
        }

        if object_index < current.completed_objects.len() {
            let receipt = &current.completed_objects[object_index];
            reverify_completed_object(&mut roots, manifest, planned, receipt, &inventory).map_err(
                |error| classify_generation_seal_failure(error, &current, manifest, writer_closure),
            )?;
            continue;
        }
        if object_index != current.completed_objects.len() {
            return Err(classify_generation_seal_failure(
                GenerationSealError("authority_generation_seal_progress_sequence_invalid"),
                &current,
                manifest,
                writer_closure,
            ));
        }

        let sealed_receipt = match planned.object_type {
            SealedObjectKind::File => {
                let selected = inventory.selected(planned).map_err(|error| {
                    classify_generation_seal_failure(error, &current, manifest, writer_closure)
                })?;
                let (identity, sealing_handle, recovered_receipt) = if let Some(intent) =
                    &current.pending_intent
                {
                    let expected_identity = intent
                        .validate_against(manifest, object_index)
                        .map_err(|error| {
                            classify_generation_seal_failure(
                                error,
                                &current,
                                manifest,
                                writer_closure,
                            )
                        })?;
                    let read_only = with_finalizer_security_privilege(|| {
                        open_relative_file(roots.root(planned.root)?, &planned.relative_path, false)
                    })
                    .map_err(GenerationSealError::from)
                    .map_err(|error| {
                        classify_generation_seal_failure(error, &current, manifest, writer_closure)
                    })?;
                    validate_opened_file(planned, selected, &expected_identity).map_err(
                        |error| {
                            classify_generation_seal_failure(
                                error,
                                &current,
                                manifest,
                                writer_closure,
                            )
                        },
                    )?;
                    let phase = observe_reopened_seal_phase(
                        &read_only,
                        planned.role.target(),
                        &expected_identity,
                    )
                    .map_err(GenerationSealError::from)
                    .map_err(|error| {
                        classify_generation_seal_failure(error, &current, manifest, writer_closure)
                    })?;
                    match recovery_action_for_phase(
                        &current,
                        manifest,
                        writer_closure,
                        planned.role,
                        phase,
                    )
                    .map_err(|error| {
                        classify_generation_seal_failure(error, &current, manifest, writer_closure)
                    })? {
                        GenerationSealRecoveryAction::AcceptExactSealed => {
                            let sealed = recover_reopened_exact_sealed_object(
                                read_only,
                                planned.role.target(),
                                expected_identity,
                            )
                            .map_err(GenerationSealError::from)
                            .map_err(|error| {
                                classify_generation_seal_failure(
                                    error,
                                    &current,
                                    manifest,
                                    writer_closure,
                                )
                            })?;
                            let receipt = generation_sealed_receipt_from_held(planned, &sealed)
                                .map_err(|error| {
                                    classify_generation_seal_failure(
                                        error,
                                        &current,
                                        manifest,
                                        writer_closure,
                                    )
                                })?;
                            drop(sealed);
                            (expected_identity, None::<OwnedHandle>, Some(receipt))
                        }
                        GenerationSealRecoveryAction::SealFromExactStaging => {
                            drop(read_only);
                            let sealing_handle = with_finalizer_security_privilege(|| {
                                open_relative_file(
                                    roots.root(planned.root)?,
                                    &planned.relative_path,
                                    true,
                                )
                            })
                            .map_err(GenerationSealError::from)
                            .map_err(|error| {
                                classify_generation_seal_failure(
                                    error,
                                    &current,
                                    manifest,
                                    writer_closure,
                                )
                            })?;
                            let observed = capture_preseal_identity_for_target(
                                &sealing_handle,
                                planned.role.target(),
                            )
                            .map_err(GenerationSealError::from)
                            .map_err(|error| {
                                classify_generation_seal_failure(
                                    error,
                                    &current,
                                    manifest,
                                    writer_closure,
                                )
                            })?;
                            if observed != expected_identity {
                                return Err(classify_generation_seal_failure(
                                    GenerationSealError(
                                        "authority_generation_seal_progress_identity_mismatch",
                                    ),
                                    &current,
                                    manifest,
                                    writer_closure,
                                ));
                            }
                            (expected_identity, Some(sealing_handle), None)
                        }
                    }
                } else {
                    let read_only = with_finalizer_security_privilege(|| {
                        open_relative_file(roots.root(planned.root)?, &planned.relative_path, false)
                    })
                    .map_err(GenerationSealError::from)
                    .map_err(|error| {
                        classify_generation_seal_failure(error, &current, manifest, writer_closure)
                    })?;
                    let expected_identity =
                        capture_preseal_identity_for_target(&read_only, planned.role.target())
                            .map_err(GenerationSealError::from)
                            .map_err(|error| {
                                classify_generation_seal_failure(
                                    error,
                                    &current,
                                    manifest,
                                    writer_closure,
                                )
                            })?;
                    validate_opened_file(planned, selected, &expected_identity).map_err(
                        |error| {
                            classify_generation_seal_failure(
                                error,
                                &current,
                                manifest,
                                writer_closure,
                            )
                        },
                    )?;
                    if observe_reopened_seal_phase(
                        &read_only,
                        planned.role.target(),
                        &expected_identity,
                    )
                    .map_err(GenerationSealError::from)
                    .map_err(|error| {
                        classify_generation_seal_failure(error, &current, manifest, writer_closure)
                    })? != FinalizerObservedSealPhase::ExactStaging
                    {
                        return Err(GenerationSealExecutionError::roll_forward(
                            GenerationSealError(
                                "authority_generation_seal_unexpected_pre_intent_phase",
                            ),
                            current.digest(manifest, writer_closure).ok(),
                        ));
                    }
                    drop(read_only);
                    let (sealing_handle, identity) = with_finalizer_security_privilege(|| {
                        let handle = open_relative_file(
                            roots.root(planned.root)?,
                            &planned.relative_path,
                            true,
                        )?;
                        let identity =
                            capture_preseal_identity_for_target(&handle, planned.role.target())?;
                        Ok((handle, identity))
                    })
                    .map_err(GenerationSealError::from)
                    .map_err(|error| {
                        classify_generation_seal_failure(error, &current, manifest, writer_closure)
                    })?;
                    if identity != expected_identity {
                        return Err(classify_generation_seal_failure(
                            GenerationSealError(
                                "authority_generation_seal_progress_identity_mismatch",
                            ),
                            &current,
                            manifest,
                            writer_closure,
                        ));
                    }
                    validate_opened_file(planned, selected, &identity).map_err(|error| {
                        classify_generation_seal_failure(error, &current, manifest, writer_closure)
                    })?;
                    let intent = GenerationSealProgressCheckpoint::with_intent(
                        &current,
                        manifest,
                        writer_closure,
                        &identity,
                        writer,
                    )
                    .map_err(|error| {
                        classify_generation_seal_failure(error, &current, manifest, writer_closure)
                    })?;
                    progress_chain = persist_progress_checkpoint(
                        progress_store,
                        manifest,
                        writer_closure,
                        &progress_chain,
                        &intent,
                    )
                    .map_err(|error| {
                        GenerationSealExecutionError::roll_forward(
                            error,
                            current.digest(manifest, writer_closure).ok(),
                        )
                    })?;
                    current = intent;
                    (identity, Some(sealing_handle), None)
                };

                if let Some(receipt) = recovered_receipt {
                    receipt
                } else {
                    let sealing_handle = sealing_handle.ok_or_else(|| {
                        classify_generation_seal_failure(
                            GenerationSealError("authority_generation_seal_handle_missing"),
                            &current,
                            manifest,
                            writer_closure,
                        )
                    })?;
                    let (staging_sddl, final_sddl) =
                        planned.role.target().exact_security_transition();
                    let sealed = seal_held_object(
                        sealing_handle,
                        planned.role.target(),
                        staging_sddl,
                        final_sddl,
                        identity,
                        || {
                            open_relative_file(
                                roots
                                    .root(planned.root)
                                    .map_err(FinalizerSecurityError::from)?,
                                &planned.relative_path,
                                false,
                            )
                        },
                    )
                    .map_err(GenerationSealError::from)
                    .map_err(|error| {
                        classify_generation_seal_failure(error, &current, manifest, writer_closure)
                    })?;
                    let receipt =
                        generation_sealed_receipt_from_held(planned, &sealed).map_err(|error| {
                            classify_generation_seal_failure(
                                error,
                                &current,
                                manifest,
                                writer_closure,
                            )
                        })?;
                    drop(sealed);
                    receipt
                }
            }
            SealedObjectKind::Directory => {
                if planned.role == GenerationSealObjectRole::ProtectedBlobNamespace {
                    seal_or_recover_relative_directory(
                        &roots,
                        manifest,
                        writer_closure,
                        planned,
                        object_index,
                        &inventory,
                        &mut current,
                        &mut progress_chain,
                        progress_store,
                        writer,
                    )?
                } else {
                    seal_or_recover_generation_directory(
                        &mut roots,
                        manifest,
                        writer_closure,
                        planned,
                        object_index,
                        &mut current,
                        &mut progress_chain,
                        progress_store,
                        writer,
                    )?
                }
            }
        };

        let completion = GenerationSealProgressCheckpoint::with_completion(
            &current,
            manifest,
            writer_closure,
            sealed_receipt,
            writer,
        )
        .map_err(|error| {
            classify_generation_seal_failure(error, &current, manifest, writer_closure)
        })?;
        progress_chain = persist_progress_checkpoint(
            progress_store,
            manifest,
            writer_closure,
            &progress_chain,
            &completion,
        )
        .map_err(|error| {
            GenerationSealExecutionError::roll_forward(
                error,
                current.digest(manifest, writer_closure).ok(),
            )
        })?;
        current = completion;
    }

    let final_inventory = roots.inventory().map_err(|error| {
        classify_generation_seal_failure(error, &current, manifest, writer_closure)
    })?;
    final_inventory
        .validate_against(manifest)
        .map_err(|error| {
            classify_generation_seal_failure(error, &current, manifest, writer_closure)
        })?;
    let receipt = GenerationSealReceipt::new(manifest, current.completed_objects.clone()).map_err(
        |error| classify_generation_seal_failure(error, &current, manifest, writer_closure),
    )?;
    reverify_all_objects_with_roots(&roots, manifest, &receipt, &final_inventory).map_err(
        |error| classify_generation_seal_failure(error, &current, manifest, writer_closure),
    )?;

    if !current.terminal {
        let terminal = GenerationSealProgressCheckpoint::into_terminal(
            &current,
            manifest,
            writer_closure,
            writer,
        )
        .map_err(|error| {
            classify_generation_seal_failure(error, &current, manifest, writer_closure)
        })?;
        progress_chain = persist_progress_checkpoint(
            progress_store,
            manifest,
            writer_closure,
            &progress_chain,
            &terminal,
        )
        .map_err(|error| {
            GenerationSealExecutionError::roll_forward(
                error,
                current.digest(manifest, writer_closure).ok(),
            )
        })?;
        current = terminal;
    }
    let final_checkpoints =
        validate_durable_progress_chain(&progress_chain, manifest, writer_closure).map_err(
            |error| classify_generation_seal_failure(error, &current, manifest, writer_closure),
        )?;
    if final_checkpoints.last() != Some(&current) || !current.terminal {
        return Err(classify_generation_seal_failure(
            GenerationSealError("authority_generation_seal_progress_terminal_incomplete"),
            &current,
            manifest,
            writer_closure,
        ));
    }
    let final_root_capabilities_sha256 = roots.final_capabilities_sha256().map_err(|error| {
        classify_generation_seal_failure(error, &current, manifest, writer_closure)
    })?;
    GenerationSealAwaitingRestart::new(
        manifest,
        writer_closure,
        &receipt,
        &current,
        &progress_chain,
        &final_inventory,
        final_root_capabilities_sha256,
    )
    .map_err(|error| classify_generation_seal_failure(error, &current, manifest, writer_closure))
}

fn classify_generation_seal_failure(
    error: GenerationSealError,
    checkpoint: &GenerationSealProgressCheckpoint,
    manifest: &GenerationSealManifest,
    writer_closure: &PreSealWriterClosureReadback,
) -> GenerationSealExecutionError {
    if checkpoint.sequence == 0 {
        GenerationSealExecutionError::before(error)
    } else {
        GenerationSealExecutionError::roll_forward(
            error,
            checkpoint.digest(manifest, writer_closure).ok(),
        )
    }
}

fn reverify_completed_object(
    roots: &mut NativeGenerationSealRoots,
    manifest: &GenerationSealManifest,
    planned: &GenerationSealObjectPlan,
    receipt: &GenerationSealedObjectReceipt,
    inventory: &GenerationSealInventory,
) -> Result<(), GenerationSealError> {
    receipt.validate_against(planned)?;
    let identity = receipt.stable_identity()?;
    let expected_security = decode_required_digest(&receipt.final_security_sha256)?;
    match planned.object_type {
        SealedObjectKind::File => {
            validate_opened_file(planned, inventory.selected(planned)?, &identity)?;
            let handle = with_finalizer_security_privilege(|| {
                open_relative_file(
                    roots
                        .root(planned.root)
                        .map_err(FinalizerSecurityError::from)?,
                    &planned.relative_path,
                    false,
                )
            })
            .map_err(GenerationSealError::from)?;
            if exact_sealed_handle_attributes(&handle, &identity)? != receipt.attributes {
                return Err(GenerationSealError(
                    "authority_generation_seal_sealed_attributes_mismatch",
                ));
            }
            verify_reopened_sealed_object(
                &handle,
                planned.role.target(),
                &identity,
                &expected_security,
            )
            .map_err(GenerationSealError::from)
        }
        SealedObjectKind::Directory => {
            if planned.role == GenerationSealObjectRole::ProtectedBlobNamespace {
                validate_opened_directory(
                    planned,
                    inventory.selected(planned)?,
                    &identity,
                    &with_finalizer_security_privilege(|| {
                        open_relative_directory(
                            roots
                                .root(planned.root)
                                .map_err(FinalizerSecurityError::from)?,
                            &planned.relative_path,
                            false,
                        )
                    })
                    .map_err(GenerationSealError::from)?,
                )?;
                let handle = with_finalizer_security_privilege(|| {
                    open_relative_directory(
                        roots
                            .root(planned.root)
                            .map_err(FinalizerSecurityError::from)?,
                        &planned.relative_path,
                        false,
                    )
                })
                .map_err(GenerationSealError::from)?;
                if exact_sealed_handle_attributes(&handle, &identity)? != receipt.attributes {
                    return Err(GenerationSealError(
                        "authority_generation_seal_sealed_attributes_mismatch",
                    ));
                }
                return verify_reopened_sealed_object(
                    &handle,
                    planned.role.target(),
                    &identity,
                    &expected_security,
                )
                .map_err(GenerationSealError::from);
            }
            let path = roots.path(planned.root)?.to_path_buf();
            let prior = roots.take_generation_root(planned.root)?;
            drop(prior);
            let handle =
                with_finalizer_security_privilege(|| open_absolute_directory(&path, false))
                    .map_err(GenerationSealError::from)?;
            if exact_sealed_handle_attributes(&handle, &identity)? != receipt.attributes {
                return Err(GenerationSealError(
                    "authority_generation_seal_sealed_attributes_mismatch",
                ));
            }
            verify_reopened_sealed_object(
                &handle,
                planned.role.target(),
                &identity,
                &expected_security,
            )
            .map_err(GenerationSealError::from)?;
            let entries = enumerate_held_directory(&handle)?;
            let mut observed = GenerationSealInventory::default();
            observed.insert(planned.root, entries);
            validate_generation_root_inventory(&observed, manifest, planned.root)?;
            roots.replace_generation_root(planned.root, handle)
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn seal_or_recover_relative_directory(
    roots: &NativeGenerationSealRoots,
    manifest: &GenerationSealManifest,
    writer_closure: &PreSealWriterClosureReadback,
    planned: &GenerationSealObjectPlan,
    object_index: usize,
    inventory: &GenerationSealInventory,
    current: &mut GenerationSealProgressCheckpoint,
    progress_chain: &mut Vec<GenerationSealDurableReadback>,
    progress_store: &mut dyn GenerationSealProgressStore,
    writer: CheckpointWriterAuthorization,
) -> Result<GenerationSealedObjectReceipt, GenerationSealExecutionError> {
    if planned.role != GenerationSealObjectRole::ProtectedBlobNamespace
        || planned.object_type != SealedObjectKind::Directory
        || planned.root != GenerationSealRoot::StateGeneration
    {
        return Err(classify_generation_seal_failure(
            GenerationSealError("authority_generation_seal_relative_directory_plan_invalid"),
            current,
            manifest,
            writer_closure,
        ));
    }
    let selected = inventory.selected(planned).map_err(|error| {
        classify_generation_seal_failure(error, current, manifest, writer_closure)
    })?;

    let (identity, sealing_handle, recovered_receipt) = if let Some(intent) =
        &current.pending_intent
    {
        let expected_identity =
            intent
                .validate_against(manifest, object_index)
                .map_err(|error| {
                    classify_generation_seal_failure(error, current, manifest, writer_closure)
                })?;
        let read_only = with_finalizer_security_privilege(|| {
            open_relative_directory(
                roots
                    .root(planned.root)
                    .map_err(FinalizerSecurityError::from)?,
                &planned.relative_path,
                false,
            )
        })
        .map_err(GenerationSealError::from)
        .map_err(|error| {
            classify_generation_seal_failure(error, current, manifest, writer_closure)
        })?;
        validate_opened_directory(planned, selected, &expected_identity, &read_only).map_err(
            |error| classify_generation_seal_failure(error, current, manifest, writer_closure),
        )?;
        let phase =
            observe_reopened_seal_phase(&read_only, planned.role.target(), &expected_identity)
                .map_err(GenerationSealError::from)
                .map_err(|error| {
                    classify_generation_seal_failure(error, current, manifest, writer_closure)
                })?;
        match recovery_action_for_phase(current, manifest, writer_closure, planned.role, phase)
            .map_err(|error| {
                classify_generation_seal_failure(error, current, manifest, writer_closure)
            })? {
            GenerationSealRecoveryAction::AcceptExactSealed => {
                let sealed = recover_reopened_exact_sealed_object(
                    read_only,
                    planned.role.target(),
                    expected_identity,
                )
                .map_err(GenerationSealError::from)
                .map_err(|error| {
                    classify_generation_seal_failure(error, current, manifest, writer_closure)
                })?;
                require_initial_protected_blob_namespace_empty(sealed.read_only_handle()).map_err(
                    |error| {
                        classify_generation_seal_failure(error, current, manifest, writer_closure)
                    },
                )?;
                let receipt =
                    generation_sealed_receipt_from_held(planned, &sealed).map_err(|error| {
                        classify_generation_seal_failure(error, current, manifest, writer_closure)
                    })?;
                drop(sealed);
                (expected_identity, None, Some(receipt))
            }
            GenerationSealRecoveryAction::SealFromExactStaging => {
                drop(read_only);
                let sealing_handle = with_finalizer_security_privilege(|| {
                    open_relative_directory(
                        roots
                            .root(planned.root)
                            .map_err(FinalizerSecurityError::from)?,
                        &planned.relative_path,
                        true,
                    )
                })
                .map_err(GenerationSealError::from)
                .map_err(|error| {
                    classify_generation_seal_failure(error, current, manifest, writer_closure)
                })?;
                let observed =
                    capture_preseal_identity_for_target(&sealing_handle, planned.role.target())
                        .map_err(GenerationSealError::from)
                        .map_err(|error| {
                            classify_generation_seal_failure(
                                error,
                                current,
                                manifest,
                                writer_closure,
                            )
                        })?;
                validate_opened_directory(planned, selected, &observed, &sealing_handle).map_err(
                    |error| {
                        classify_generation_seal_failure(error, current, manifest, writer_closure)
                    },
                )?;
                if observed != expected_identity {
                    return Err(classify_generation_seal_failure(
                        GenerationSealError("authority_generation_seal_progress_identity_mismatch"),
                        current,
                        manifest,
                        writer_closure,
                    ));
                }
                (expected_identity, Some(sealing_handle), None)
            }
        }
    } else {
        let read_only = with_finalizer_security_privilege(|| {
            open_relative_directory(
                roots
                    .root(planned.root)
                    .map_err(FinalizerSecurityError::from)?,
                &planned.relative_path,
                false,
            )
        })
        .map_err(GenerationSealError::from)
        .map_err(|error| {
            classify_generation_seal_failure(error, current, manifest, writer_closure)
        })?;
        let expected_identity =
            capture_preseal_identity_for_target(&read_only, planned.role.target())
                .map_err(GenerationSealError::from)
                .map_err(|error| {
                    classify_generation_seal_failure(error, current, manifest, writer_closure)
                })?;
        validate_opened_directory(planned, selected, &expected_identity, &read_only).map_err(
            |error| classify_generation_seal_failure(error, current, manifest, writer_closure),
        )?;
        if observe_reopened_seal_phase(&read_only, planned.role.target(), &expected_identity)
            .map_err(GenerationSealError::from)
            .map_err(|error| {
                classify_generation_seal_failure(error, current, manifest, writer_closure)
            })?
            != FinalizerObservedSealPhase::ExactStaging
        {
            return Err(GenerationSealExecutionError::roll_forward(
                GenerationSealError("authority_generation_seal_unexpected_pre_intent_phase"),
                current.digest(manifest, writer_closure).ok(),
            ));
        }
        drop(read_only);
        let sealing_handle = with_finalizer_security_privilege(|| {
            open_relative_directory(
                roots
                    .root(planned.root)
                    .map_err(FinalizerSecurityError::from)?,
                &planned.relative_path,
                true,
            )
        })
        .map_err(GenerationSealError::from)
        .map_err(|error| {
            classify_generation_seal_failure(error, current, manifest, writer_closure)
        })?;
        let identity = capture_preseal_identity_for_target(&sealing_handle, planned.role.target())
            .map_err(GenerationSealError::from)
            .map_err(|error| {
                classify_generation_seal_failure(error, current, manifest, writer_closure)
            })?;
        validate_opened_directory(planned, selected, &identity, &sealing_handle).map_err(
            |error| classify_generation_seal_failure(error, current, manifest, writer_closure),
        )?;
        if identity != expected_identity {
            return Err(classify_generation_seal_failure(
                GenerationSealError("authority_generation_seal_progress_identity_mismatch"),
                current,
                manifest,
                writer_closure,
            ));
        }
        let intent = GenerationSealProgressCheckpoint::with_intent(
            current,
            manifest,
            writer_closure,
            &identity,
            writer,
        )
        .map_err(|error| {
            classify_generation_seal_failure(error, current, manifest, writer_closure)
        })?;
        *progress_chain = persist_progress_checkpoint(
            progress_store,
            manifest,
            writer_closure,
            progress_chain,
            &intent,
        )
        .map_err(|error| {
            GenerationSealExecutionError::roll_forward(
                error,
                current.digest(manifest, writer_closure).ok(),
            )
        })?;
        *current = intent;
        (identity, Some(sealing_handle), None)
    };

    if let Some(receipt) = recovered_receipt {
        return Ok(receipt);
    }
    let sealing_handle = sealing_handle.ok_or_else(|| {
        classify_generation_seal_failure(
            GenerationSealError("authority_generation_seal_handle_missing"),
            current,
            manifest,
            writer_closure,
        )
    })?;
    let (staging_sddl, final_sddl) = planned.role.target().exact_security_transition();
    let sealed = seal_held_object(
        sealing_handle,
        planned.role.target(),
        staging_sddl,
        final_sddl,
        identity,
        || {
            open_relative_directory(
                roots
                    .root(planned.root)
                    .map_err(FinalizerSecurityError::from)?,
                &planned.relative_path,
                false,
            )
        },
    )
    .map_err(GenerationSealError::from)
    .map_err(|error| classify_generation_seal_failure(error, current, manifest, writer_closure))?;
    require_initial_protected_blob_namespace_empty(sealed.read_only_handle()).map_err(|error| {
        classify_generation_seal_failure(error, current, manifest, writer_closure)
    })?;
    generation_sealed_receipt_from_held(planned, &sealed)
        .map_err(|error| classify_generation_seal_failure(error, current, manifest, writer_closure))
}

fn validate_opened_directory(
    planned: &GenerationSealObjectPlan,
    selected: &DirectoryInventoryEntry,
    identity: &PreSealStableIdentity,
    handle: &OwnedHandle,
) -> Result<(), GenerationSealError> {
    if planned.object_type != SealedObjectKind::Directory
        || !selected.is_directory
        || selected.is_reparse
        || selected.file_id != *identity.file_id()
        || selected.byte_length != identity.byte_length()
    {
        return Err(GenerationSealError(
            "authority_generation_seal_relative_directory_identity_mismatch",
        ));
    }
    require_initial_protected_blob_namespace_empty(handle)
}

fn require_initial_protected_blob_namespace_empty(
    handle: &OwnedHandle,
) -> Result<(), GenerationSealError> {
    if !enumerate_held_directory(handle)?.is_empty() {
        return Err(GenerationSealError(
            "authority_generation_seal_protected_blob_namespace_not_empty",
        ));
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn seal_or_recover_generation_directory(
    roots: &mut NativeGenerationSealRoots,
    manifest: &GenerationSealManifest,
    writer_closure: &PreSealWriterClosureReadback,
    planned: &GenerationSealObjectPlan,
    object_index: usize,
    current: &mut GenerationSealProgressCheckpoint,
    progress_chain: &mut Vec<GenerationSealDurableReadback>,
    progress_store: &mut dyn GenerationSealProgressStore,
    writer: CheckpointWriterAuthorization,
) -> Result<GenerationSealedObjectReceipt, GenerationSealExecutionError> {
    if planned.object_type != SealedObjectKind::Directory {
        return Err(classify_generation_seal_failure(
            GenerationSealError("authority_generation_seal_directory_plan_invalid"),
            current,
            manifest,
            writer_closure,
        ));
    }
    let path = roots
        .path(planned.root)
        .map_err(|error| {
            classify_generation_seal_failure(error, current, manifest, writer_closure)
        })?
        .to_path_buf();

    let (identity, root_handle, recovered_receipt) = if let Some(intent) = &current.pending_intent {
        let expected_identity =
            intent
                .validate_against(manifest, object_index)
                .map_err(|error| {
                    classify_generation_seal_failure(error, current, manifest, writer_closure)
                })?;
        let original_root = roots.take_generation_root(planned.root).map_err(|error| {
            classify_generation_seal_failure(error, current, manifest, writer_closure)
        })?;
        let read_only = with_finalizer_security_privilege(|| open_absolute_directory(&path, false))
            .map_err(GenerationSealError::from)
            .map_err(|error| {
                classify_generation_seal_failure(error, current, manifest, writer_closure)
            })?;
        let phase =
            observe_reopened_seal_phase(&read_only, planned.role.target(), &expected_identity)
                .map_err(GenerationSealError::from)
                .map_err(|error| {
                    classify_generation_seal_failure(error, current, manifest, writer_closure)
                })?;
        match recovery_action_for_phase(current, manifest, writer_closure, planned.role, phase)
            .map_err(|error| {
                classify_generation_seal_failure(error, current, manifest, writer_closure)
            })? {
            GenerationSealRecoveryAction::AcceptExactSealed => {
                drop(original_root);
                let sealed = recover_reopened_exact_sealed_object(
                    read_only,
                    planned.role.target(),
                    expected_identity,
                )
                .map_err(GenerationSealError::from)
                .map_err(|error| {
                    classify_generation_seal_failure(error, current, manifest, writer_closure)
                })?;
                let entries =
                    enumerate_held_directory(sealed.read_only_handle()).map_err(|error| {
                        classify_generation_seal_failure(error, current, manifest, writer_closure)
                    })?;
                let mut observed = GenerationSealInventory::default();
                observed.insert(planned.root, entries);
                validate_generation_root_inventory(&observed, manifest, planned.root).map_err(
                    |error| {
                        classify_generation_seal_failure(error, current, manifest, writer_closure)
                    },
                )?;
                let receipt =
                    generation_sealed_receipt_from_held(planned, &sealed).map_err(|error| {
                        classify_generation_seal_failure(error, current, manifest, writer_closure)
                    })?;
                let read_only = sealed.into_read_only_handle();
                roots
                    .replace_generation_root(planned.root, read_only)
                    .map_err(|error| {
                        classify_generation_seal_failure(error, current, manifest, writer_closure)
                    })?;
                (expected_identity, None, Some(receipt))
            }
            GenerationSealRecoveryAction::SealFromExactStaging => {
                drop(read_only);
                let observed =
                    capture_preseal_identity_for_target(&original_root, planned.role.target())
                        .map_err(GenerationSealError::from)
                        .map_err(|error| {
                            classify_generation_seal_failure(
                                error,
                                current,
                                manifest,
                                writer_closure,
                            )
                        })?;
                if observed != expected_identity {
                    return Err(classify_generation_seal_failure(
                        GenerationSealError("authority_generation_seal_progress_identity_mismatch"),
                        current,
                        manifest,
                        writer_closure,
                    ));
                }
                (expected_identity, Some(original_root), None)
            }
        }
    } else {
        let read_only = with_finalizer_security_privilege(|| open_absolute_directory(&path, false))
            .map_err(GenerationSealError::from)
            .map_err(|error| {
                classify_generation_seal_failure(error, current, manifest, writer_closure)
            })?;
        let expected_identity =
            capture_preseal_identity_for_target(&read_only, planned.role.target())
                .map_err(GenerationSealError::from)
                .map_err(|error| {
                    classify_generation_seal_failure(error, current, manifest, writer_closure)
                })?;
        if observe_reopened_seal_phase(&read_only, planned.role.target(), &expected_identity)
            .map_err(GenerationSealError::from)
            .map_err(|error| {
                classify_generation_seal_failure(error, current, manifest, writer_closure)
            })?
            != FinalizerObservedSealPhase::ExactStaging
        {
            return Err(GenerationSealExecutionError::roll_forward(
                GenerationSealError("authority_generation_seal_unexpected_pre_intent_phase"),
                current.digest(manifest, writer_closure).ok(),
            ));
        }
        drop(read_only);
        let root_handle = roots.take_generation_root(planned.root).map_err(|error| {
            classify_generation_seal_failure(error, current, manifest, writer_closure)
        })?;
        let identity = capture_preseal_identity_for_target(&root_handle, planned.role.target())
            .map_err(GenerationSealError::from)
            .map_err(|error| {
                classify_generation_seal_failure(error, current, manifest, writer_closure)
            })?;
        if identity != expected_identity {
            return Err(classify_generation_seal_failure(
                GenerationSealError("authority_generation_seal_progress_identity_mismatch"),
                current,
                manifest,
                writer_closure,
            ));
        }
        let intent = GenerationSealProgressCheckpoint::with_intent(
            current,
            manifest,
            writer_closure,
            &identity,
            writer,
        )
        .map_err(|error| {
            classify_generation_seal_failure(error, current, manifest, writer_closure)
        })?;
        *progress_chain = persist_progress_checkpoint(
            progress_store,
            manifest,
            writer_closure,
            progress_chain,
            &intent,
        )
        .map_err(|error| {
            GenerationSealExecutionError::roll_forward(
                error,
                current.digest(manifest, writer_closure).ok(),
            )
        })?;
        *current = intent;
        (identity, Some(root_handle), None)
    };

    if let Some(receipt) = recovered_receipt {
        return Ok(receipt);
    }
    let root_handle = root_handle.ok_or_else(|| {
        classify_generation_seal_failure(
            GenerationSealError("authority_generation_seal_root_handle_missing"),
            current,
            manifest,
            writer_closure,
        )
    })?;
    let (staging_sddl, final_sddl) = planned.role.target().exact_security_transition();
    let sealed = seal_held_object(
        root_handle,
        planned.role.target(),
        staging_sddl,
        final_sddl,
        identity,
        || open_absolute_directory(&path, false),
    )
    .map_err(GenerationSealError::from)
    .map_err(|error| classify_generation_seal_failure(error, current, manifest, writer_closure))?;
    let entries = enumerate_held_directory(sealed.read_only_handle()).map_err(|error| {
        classify_generation_seal_failure(error, current, manifest, writer_closure)
    })?;
    let mut observed = GenerationSealInventory::default();
    observed.insert(planned.root, entries);
    validate_generation_root_inventory(&observed, manifest, planned.root).map_err(|error| {
        classify_generation_seal_failure(error, current, manifest, writer_closure)
    })?;
    let receipt = generation_sealed_receipt_from_held(planned, &sealed).map_err(|error| {
        classify_generation_seal_failure(error, current, manifest, writer_closure)
    })?;
    roots
        .replace_generation_root(planned.root, sealed.into_read_only_handle())
        .map_err(|error| {
            classify_generation_seal_failure(error, current, manifest, writer_closure)
        })?;
    Ok(receipt)
}

fn reverify_all_objects_with_roots(
    roots: &NativeGenerationSealRoots,
    manifest: &GenerationSealManifest,
    receipt: &GenerationSealReceipt,
    inventory: &GenerationSealInventory,
) -> Result<(), GenerationSealError> {
    receipt.validate_against(manifest)?;
    inventory.validate_against(manifest)?;
    for (planned, observed) in manifest.objects.iter().zip(&receipt.objects) {
        let identity = observed.stable_identity()?;
        let expected_security = decode_required_digest(&observed.final_security_sha256)?;
        match planned.object_type {
            SealedObjectKind::File => {
                validate_opened_file(planned, inventory.selected(planned)?, &identity)?;
                let handle = with_finalizer_security_privilege(|| {
                    open_relative_file(
                        roots
                            .root(planned.root)
                            .map_err(FinalizerSecurityError::from)?,
                        &planned.relative_path,
                        false,
                    )
                })
                .map_err(GenerationSealError::from)?;
                if exact_sealed_handle_attributes(&handle, &identity)? != observed.attributes {
                    return Err(GenerationSealError(
                        "authority_generation_seal_sealed_attributes_mismatch",
                    ));
                }
                verify_reopened_sealed_object(
                    &handle,
                    planned.role.target(),
                    &identity,
                    &expected_security,
                )
                .map_err(GenerationSealError::from)?;
            }
            SealedObjectKind::Directory => {
                if planned.role == GenerationSealObjectRole::ProtectedBlobNamespace {
                    let handle = with_finalizer_security_privilege(|| {
                        open_relative_directory(
                            roots
                                .root(planned.root)
                                .map_err(FinalizerSecurityError::from)?,
                            &planned.relative_path,
                            false,
                        )
                    })
                    .map_err(GenerationSealError::from)?;
                    validate_opened_directory(
                        planned,
                        inventory.selected(planned)?,
                        &identity,
                        &handle,
                    )?;
                    if exact_sealed_handle_attributes(&handle, &identity)? != observed.attributes {
                        return Err(GenerationSealError(
                            "authority_generation_seal_sealed_attributes_mismatch",
                        ));
                    }
                    verify_reopened_sealed_object(
                        &handle,
                        planned.role.target(),
                        &identity,
                        &expected_security,
                    )
                    .map_err(GenerationSealError::from)?;
                    continue;
                }
                if exact_sealed_handle_attributes(roots.root(planned.root)?, &identity)?
                    != observed.attributes
                {
                    return Err(GenerationSealError(
                        "authority_generation_seal_sealed_attributes_mismatch",
                    ));
                }
                verify_reopened_sealed_object(
                    roots.root(planned.root)?,
                    planned.role.target(),
                    &identity,
                    &expected_security,
                )
                .map_err(GenerationSealError::from)?;
                let entries = enumerate_held_directory(roots.root(planned.root)?)?;
                let mut observed_inventory = GenerationSealInventory::default();
                observed_inventory.insert(planned.root, entries);
                validate_generation_root_inventory(&observed_inventory, manifest, planned.root)?;
            }
        }
    }
    Ok(())
}

struct ValidatedGenerationSealProgress {
    raw_chain: Vec<GenerationSealDurableReadback>,
    checkpoints: Vec<GenerationSealProgressCheckpoint>,
    writer_closure: PreSealWriterClosureReadback,
}

fn load_validated_generation_seal_progress(
    progress_store: &mut dyn GenerationSealProgressStore,
    manifest: &GenerationSealManifest,
) -> Result<Option<ValidatedGenerationSealProgress>, GenerationSealError> {
    manifest.validate()?;
    let raw_chain = progress_store.load_exact_chain(manifest.digest()?)?;
    let Some(genesis) = raw_chain.first() else {
        return Ok(None);
    };
    // This parse extracts only the closure needed to perform the canonical
    // typed parse below.  No field from it is trusted before both the closure
    // and the complete exact chain validate against this manifest.
    let untrusted_genesis: GenerationSealProgressCheckpoint =
        serde_json::from_slice(&genesis.checkpoint_bytes).map_err(|_| {
            GenerationSealError("authority_generation_seal_progress_serialization_invalid")
        })?;
    let writer_closure = untrusted_genesis.writer_closure_readback;
    writer_closure.validate_against(manifest)?;
    let checkpoints = validate_durable_progress_chain(&raw_chain, manifest, &writer_closure)?;
    if checkpoints.is_empty() {
        return Err(GenerationSealError(
            "authority_generation_seal_progress_chain_gap_or_fork",
        ));
    }
    Ok(Some(ValidatedGenerationSealProgress {
        raw_chain,
        checkpoints,
        writer_closure,
    }))
}

fn verify_generation_seal_restart_roots(
    roots: &NativeGenerationSealRoots,
    manifest: &GenerationSealManifest,
    receipt: &GenerationSealReceipt,
) -> Result<(GenerationSealInventory, VerifiedGenerationSealRestart), GenerationSealError> {
    let inventory = roots.inventory()?;
    inventory.validate_against(manifest)?;
    reverify_all_objects_with_roots(roots, manifest, receipt, &inventory)?;
    let runner_policy_identity =
        runner_policy_identity_from_verified_held_handle(roots, manifest, receipt)?;
    let verified = VerifiedGenerationSealRestart {
        readback: GenerationSealRestartReadback::from_verified_reopen(manifest, receipt)?,
        final_inventory_readback_sha256: final_inventory_readback_digest(manifest, &inventory)?,
        final_root_capabilities_sha256: roots.final_capabilities_sha256()?,
        runner_policy_identity,
        protected_blob_namespace: ProtectedBlobNamespaceSealProjection::from_verified_handle(
            roots, manifest, receipt,
        )?,
    };
    Ok((inventory, verified))
}

fn runner_policy_identity_from_verified_held_handle(
    roots: &NativeGenerationSealRoots,
    manifest: &GenerationSealManifest,
    receipt: &GenerationSealReceipt,
) -> Result<GenerationSealedRunnerPolicyIdentity, GenerationSealError> {
    receipt.validate_against(manifest)?;
    let index = manifest
        .objects
        .iter()
        .position(|object| object.role == GenerationSealObjectRole::RunnerPolicyState)
        .ok_or(GenerationSealError(
            "authority_generation_seal_runner_policy_identity_missing",
        ))?;
    let planned = manifest.objects.get(index).ok_or(GenerationSealError(
        "authority_generation_seal_runner_policy_identity_missing",
    ))?;
    let sealed = receipt.objects.get(index).ok_or(GenerationSealError(
        "authority_generation_seal_runner_policy_identity_missing",
    ))?;
    if planned.object_type != SealedObjectKind::File
        || planned.root != GenerationSealRoot::StateGeneration
        || sealed.role != GenerationSealObjectRole::RunnerPolicyState
    {
        return Err(GenerationSealError(
            "authority_generation_seal_runner_policy_identity_invalid",
        ));
    }
    let expected = sealed.stable_identity()?;
    let expected_security = decode_required_digest(&sealed.final_security_sha256)?;
    let handle = with_finalizer_security_privilege(|| {
        open_relative_file(
            roots
                .root(planned.root)
                .map_err(FinalizerSecurityError::from)?,
            &planned.relative_path,
            false,
        )
    })
    .map_err(GenerationSealError::from)?;
    verify_reopened_sealed_object(
        &handle,
        planned.role.target(),
        &expected,
        &expected_security,
    )
    .map_err(GenerationSealError::from)?;
    let attributes = exact_sealed_handle_attributes(&handle, &expected)?;
    if attributes != sealed.attributes {
        return Err(GenerationSealError(
            "authority_generation_seal_sealed_attributes_mismatch",
        ));
    }
    let observed = GenerationSealedRunnerPolicyIdentity::new(
        expected.volume_serial(),
        *expected.file_id(),
        expected.link_count(),
        attributes,
    )?;
    if observed != runner_policy_identity_from_receipt(manifest, receipt)? {
        return Err(GenerationSealError(
            "authority_generation_seal_runner_policy_identity_mismatch",
        ));
    }
    Ok(observed)
}

fn runner_policy_identity_from_receipt(
    manifest: &GenerationSealManifest,
    receipt: &GenerationSealReceipt,
) -> Result<GenerationSealedRunnerPolicyIdentity, GenerationSealError> {
    receipt.validate_against(manifest)?;
    let index = manifest
        .objects
        .iter()
        .position(|object| object.role == GenerationSealObjectRole::RunnerPolicyState)
        .ok_or(GenerationSealError(
            "authority_generation_seal_runner_policy_identity_missing",
        ))?;
    let sealed = receipt.objects.get(index).ok_or(GenerationSealError(
        "authority_generation_seal_runner_policy_identity_missing",
    ))?;
    let identity = sealed.stable_identity()?;
    GenerationSealedRunnerPolicyIdentity::new(
        identity.volume_serial(),
        *identity.file_id(),
        identity.link_count(),
        sealed.attributes,
    )
}

#[allow(clippy::too_many_arguments)]
fn consume_generation_seal_awaiting_restart(
    manifest: &GenerationSealManifest,
    invocation: &NativeGenerationSealInvocation,
    awaiting_restart: GenerationSealAwaitingRestart,
    writer_closure: &PreSealWriterClosureReadback,
    receipt: GenerationSealReceipt,
    terminal_checkpoint: &GenerationSealProgressCheckpoint,
    raw_chain: &[GenerationSealDurableReadback],
    verified_restart: VerifiedGenerationSealRestart,
) -> Result<GenerationSealTerminalAuthorization, GenerationSealError> {
    invocation.revalidate()?;
    awaiting_restart.validate_durable_binding(
        manifest,
        writer_closure,
        &receipt,
        terminal_checkpoint,
        raw_chain,
    )?;
    if invocation.digest() == awaiting_restart.sealing_invocation_sha256 {
        return Err(GenerationSealError(
            "authority_generation_seal_restart_invocation_not_distinct",
        ));
    }
    if verified_restart.final_inventory_readback_sha256
        != awaiting_restart.final_inventory_readback_sha256
        || verified_restart.final_root_capabilities_sha256
            != awaiting_restart.final_root_capabilities_sha256
        || decode_required_digest(&verified_restart.readback.readback_sha256)?
            != awaiting_restart.independent_reopen_readback_sha256
    {
        return Err(GenerationSealError(
            "authority_generation_seal_restart_readback_drift",
        ));
    }
    GenerationSealTerminalAuthorization::new(
        manifest,
        writer_closure,
        receipt,
        terminal_checkpoint,
        raw_chain,
        invocation.digest(),
        verified_restart,
    )
}

/// Returns exactly one authenticated recovery state.  A malformed, aliased,
/// gapped, or unreadable nonempty namespace is always an error and can never
/// be collapsed into `Empty`.
pub(super) fn resume_generation_seal(
    layout: &AuthorityLayout,
    manifest: &GenerationSealManifest,
    invocation: &NativeGenerationSealInvocation,
    progress_store: &mut AuthenticatedNativeGenerationSealProgressStore,
    handles: NativeGenerationSealRootHandles,
) -> Result<GenerationSealResumeState, GenerationSealError> {
    if !GENERATION_SEAL_PRODUCTION_ENABLED {
        return Err(GenerationSealError(
            "authority_generation_seal_production_disabled",
        ));
    }
    resume_generation_seal_enabled(
        layout,
        manifest,
        invocation,
        progress_store.inner.as_mut(),
        handles,
    )
}

fn resume_generation_seal_enabled(
    layout: &AuthorityLayout,
    manifest: &GenerationSealManifest,
    invocation: &NativeGenerationSealInvocation,
    progress_store: &mut dyn GenerationSealProgressStore,
    handles: NativeGenerationSealRootHandles,
) -> Result<GenerationSealResumeState, GenerationSealError> {
    invocation.revalidate()?;
    let Some(progress) = load_validated_generation_seal_progress(progress_store, manifest)? else {
        return Ok(GenerationSealResumeState::Empty);
    };
    let terminal_checkpoint = progress.checkpoints.last().ok_or(GenerationSealError(
        "authority_generation_seal_progress_chain_gap_or_fork",
    ))?;
    if !terminal_checkpoint.terminal {
        return Ok(GenerationSealResumeState::Partial(
            GenerationSealPartialResume::from_validated_chain(
                manifest,
                &progress.writer_closure,
                &progress.raw_chain,
                &progress.checkpoints,
            )?,
        ));
    }
    if terminal_checkpoint.sequence != GENERATION_SEAL_TERMINAL_SEQUENCE {
        return Err(GenerationSealError(
            "authority_generation_seal_terminal_checkpoint_invalid",
        ));
    }
    let receipt =
        GenerationSealReceipt::new(manifest, terminal_checkpoint.completed_objects.clone())?;
    let roots = NativeGenerationSealRoots::new(layout, manifest, handles)?;
    let (inventory, verified_restart) =
        verify_generation_seal_restart_roots(&roots, manifest, &receipt)?;
    let awaiting_restart = GenerationSealAwaitingRestart::new(
        manifest,
        &progress.writer_closure,
        &receipt,
        terminal_checkpoint,
        &progress.raw_chain,
        &inventory,
        verified_restart.final_root_capabilities_sha256,
    )?;
    if invocation.digest() == awaiting_restart.sealing_invocation_sha256 {
        return Ok(GenerationSealResumeState::AwaitingRestart(awaiting_restart));
    }
    Ok(GenerationSealResumeState::TerminalAuthorized(
        consume_generation_seal_awaiting_restart(
            manifest,
            invocation,
            awaiting_restart,
            &progress.writer_closure,
            receipt,
            terminal_checkpoint,
            &progress.raw_chain,
            verified_restart,
        )?,
    ))
}

pub(super) fn reverify_generation_seal_after_restart(
    layout: &AuthorityLayout,
    manifest: &GenerationSealManifest,
    invocation: &NativeGenerationSealInvocation,
    awaiting_restart: GenerationSealAwaitingRestart,
    progress_store: &mut AuthenticatedNativeGenerationSealProgressStore,
    handles: NativeGenerationSealRootHandles,
) -> Result<GenerationSealTerminalAuthorization, GenerationSealError> {
    invocation.revalidate()?;
    let progress =
        load_validated_generation_seal_progress(progress_store.inner.as_mut(), manifest)?.ok_or(
            GenerationSealError("authority_generation_seal_progress_chain_gap_or_fork"),
        )?;
    let terminal_checkpoint = progress.checkpoints.last().ok_or(GenerationSealError(
        "authority_generation_seal_terminal_checkpoint_invalid",
    ))?;
    if !terminal_checkpoint.terminal
        || terminal_checkpoint.sequence != GENERATION_SEAL_TERMINAL_SEQUENCE
    {
        return Err(GenerationSealError(
            "authority_generation_seal_terminal_checkpoint_invalid",
        ));
    }
    let receipt =
        GenerationSealReceipt::new(manifest, terminal_checkpoint.completed_objects.clone())?;
    let roots = NativeGenerationSealRoots::new(layout, manifest, handles)?;
    let (_, verified_restart) = verify_generation_seal_restart_roots(&roots, manifest, &receipt)?;
    consume_generation_seal_awaiting_restart(
        manifest,
        invocation,
        awaiting_restart,
        &progress.writer_closure,
        receipt,
        terminal_checkpoint,
        &progress.raw_chain,
        verified_restart,
    )
}

fn validate_opened_file(
    planned: &GenerationSealObjectPlan,
    selected: &DirectoryInventoryEntry,
    identity: &PreSealStableIdentity,
) -> Result<(), GenerationSealError> {
    let expected_hash = planned
        .expected_bytes_sha256
        .as_deref()
        .and_then(decode_hex_32)
        .ok_or(GenerationSealError(
            "authority_generation_seal_file_plan_invalid",
        ))?;
    if selected.file_id != *identity.file_id()
        || selected.byte_length != identity.byte_length()
        || Some(identity.byte_length()) != planned.expected_byte_length
        || identity.bytes_sha256() != Some(&expected_hash)
        || identity.link_count() != 1
    {
        return Err(GenerationSealError(
            "authority_generation_seal_opened_file_mismatch",
        ));
    }
    Ok(())
}

fn validate_generation_root_inventory(
    inventory: &GenerationSealInventory,
    manifest: &GenerationSealManifest,
    root: GenerationSealRoot,
) -> Result<(), GenerationSealError> {
    let expected = manifest
        .objects
        .iter()
        .filter(|object| object.root == root && object.relative_path != ".")
        .map(|object| (object.relative_path.as_str(), object.object_type))
        .collect::<BTreeMap<_, _>>();
    let entries = inventory.entries.get(&root).ok_or(GenerationSealError(
        "authority_generation_seal_inventory_missing",
    ))?;
    let mut actual = BTreeMap::new();
    for entry in entries {
        let expected_kind =
            expected
                .get(entry.relative_name.as_str())
                .ok_or(GenerationSealError(
                    "authority_generation_seal_inventory_not_exhaustive",
                ))?;
        validate_inventory_entry(entry, *expected_kind)?;
        if actual
            .insert(entry.relative_name.as_str(), *expected_kind)
            .is_some()
        {
            return Err(GenerationSealError(
                "authority_generation_seal_inventory_alias",
            ));
        }
    }
    if actual != expected {
        return Err(GenerationSealError(
            "authority_generation_seal_inventory_not_exhaustive",
        ));
    }
    Ok(())
}

pub(super) fn enumerate_transaction_namespace_member_names(
    directory: &OwnedHandle,
) -> Result<Vec<String>, GenerationSealError> {
    let mut names = Vec::new();
    let mut case_folded = BTreeSet::new();
    for entry in enumerate_held_directory(directory)? {
        if entry.is_directory
            || entry.is_reparse
            || entry.file_id.iter().all(|byte| *byte == 0)
            || !case_folded.insert(entry.relative_name.to_ascii_lowercase())
        {
            return Err(GenerationSealError(
                "authority_generation_seal_transaction_namespace_invalid",
            ));
        }
        names.push(entry.relative_name);
    }
    names.sort();
    Ok(names)
}

fn enumerate_held_directory(
    directory: &OwnedHandle,
) -> Result<Vec<DirectoryInventoryEntry>, GenerationSealError> {
    let mut entries = Vec::new();
    let mut restart = true;
    loop {
        let mut storage = vec![0u64; DIRECTORY_ENUMERATION_BUFFER_BYTES / size_of::<u64>()];
        let information_class = if restart {
            FileIdBothDirectoryRestartInfo
        } else {
            FileIdBothDirectoryInfo
        };
        let ok = unsafe {
            GetFileInformationByHandleEx(
                directory.as_raw_handle().cast(),
                information_class,
                storage.as_mut_ptr().cast(),
                DIRECTORY_ENUMERATION_BUFFER_BYTES as u32,
            )
        };
        if ok == 0 {
            if unsafe { GetLastError() } == ERROR_NO_MORE_FILES {
                break;
            }
            return Err(GenerationSealError(
                "authority_generation_seal_directory_enumeration_failed",
            ));
        }
        restart = false;
        let bytes = unsafe {
            std::slice::from_raw_parts(
                storage.as_ptr().cast::<u8>(),
                DIRECTORY_ENUMERATION_BUFFER_BYTES,
            )
        };
        let mut offset = 0usize;
        loop {
            if offset
                .checked_add(size_of::<FILE_ID_BOTH_DIR_INFO>())
                .is_none_or(|end| end > bytes.len())
            {
                return Err(GenerationSealError(
                    "authority_generation_seal_directory_record_invalid",
                ));
            }
            let record = unsafe {
                ptr::read_unaligned(bytes.as_ptr().add(offset).cast::<FILE_ID_BOTH_DIR_INFO>())
            };
            let name_bytes = record.FileNameLength as usize;
            let name_offset = offset + offset_of!(FILE_ID_BOTH_DIR_INFO, FileName);
            if name_bytes == 0
                || name_bytes % 2 != 0
                || name_offset
                    .checked_add(name_bytes)
                    .is_none_or(|end| end > bytes.len())
            {
                return Err(GenerationSealError(
                    "authority_generation_seal_directory_record_invalid",
                ));
            }
            let name_words = unsafe {
                std::slice::from_raw_parts(
                    bytes.as_ptr().add(name_offset).cast::<u16>(),
                    name_bytes / 2,
                )
            };
            if name_words.contains(&0) {
                return Err(GenerationSealError(
                    "authority_generation_seal_directory_record_invalid",
                ));
            }
            let relative_name = String::from_utf16(name_words).map_err(|_| {
                GenerationSealError("authority_generation_seal_directory_record_invalid")
            })?;
            if relative_name != "." && relative_name != ".." {
                let mut file_id = [0u8; 16];
                file_id[..8].copy_from_slice(&(record.FileId as u64).to_be_bytes());
                if record.EndOfFile < 0 {
                    return Err(GenerationSealError(
                        "authority_generation_seal_directory_record_invalid",
                    ));
                }
                entries.push(DirectoryInventoryEntry {
                    relative_name,
                    file_id,
                    byte_length: record.EndOfFile as u64,
                    is_directory: record.FileAttributes & FILE_ATTRIBUTE_DIRECTORY != 0,
                    is_reparse: record.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0,
                });
            }
            if record.NextEntryOffset == 0 {
                break;
            }
            let next = record.NextEntryOffset as usize;
            if next < size_of::<FILE_ID_BOTH_DIR_INFO>()
                || offset
                    .checked_add(next)
                    .is_none_or(|value| value >= bytes.len())
            {
                return Err(GenerationSealError(
                    "authority_generation_seal_directory_record_invalid",
                ));
            }
            offset += next;
        }
    }
    entries.sort_by(|left, right| left.relative_name.cmp(&right.relative_name));
    let mut folded = BTreeSet::new();
    for entry in &entries {
        if !folded.insert(entry.relative_name.to_ascii_lowercase()) {
            return Err(GenerationSealError(
                "authority_generation_seal_inventory_alias",
            ));
        }
    }
    Ok(entries)
}

fn open_relative_file(
    parent: &OwnedHandle,
    name: &str,
    for_sealing: bool,
) -> Result<OwnedHandle, FinalizerSecurityError> {
    validate_relative_name(name).map_err(|_| {
        FinalizerSecurityError::new("authority_finalizer_generation_relative_name_invalid")
    })?;
    let mut words = name.encode_utf16().collect::<Vec<_>>();
    let name_bytes = words
        .len()
        .checked_mul(2)
        .and_then(|value| u16::try_from(value).ok())
        .ok_or(FinalizerSecurityError::new(
            "authority_finalizer_generation_relative_name_invalid",
        ))?;
    let unicode = UNICODE_STRING {
        Length: name_bytes,
        MaximumLength: name_bytes,
        Buffer: words.as_mut_ptr(),
    };
    let attributes = OBJECT_ATTRIBUTES {
        Length: size_of::<OBJECT_ATTRIBUTES>() as u32,
        RootDirectory: parent.as_raw_handle().cast(),
        ObjectName: &unicode,
        Attributes: OBJ_CASE_INSENSITIVE as u32,
        SecurityDescriptor: ptr::null(),
        SecurityQualityOfService: ptr::null(),
    };
    let mut status_block = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let mut handle = ptr::null_mut();
    let desired_access = READ_CONTROL
        | SYNCHRONIZE
        | FILE_READ_DATA
        | FILE_READ_ATTRIBUTES
        | FILE_READ_EA
        | ACCESS_SYSTEM_SECURITY
        | if for_sealing { WRITE_DAC } else { 0 };
    let status = unsafe {
        NtCreateFile(
            &mut handle,
            desired_access,
            &attributes,
            &mut status_block,
            ptr::null(),
            0,
            FILE_SHARE_READ,
            FILE_OPEN,
            FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT | FILE_NON_DIRECTORY_FILE,
            ptr::null(),
            0,
        )
    };
    if status < 0
        || handle.is_null()
        || handle == INVALID_HANDLE_VALUE
        || status_block.Information != FILE_OPENED_INFORMATION
    {
        if !handle.is_null() && handle != INVALID_HANDLE_VALUE {
            unsafe { drop(OwnedHandle::from_raw_handle(handle as RawHandle)) };
        }
        return Err(FinalizerSecurityError::new(
            "authority_finalizer_generation_relative_open_failed",
        ));
    }
    Ok(unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) })
}

fn open_relative_directory(
    parent: &OwnedHandle,
    name: &str,
    for_sealing: bool,
) -> Result<OwnedHandle, FinalizerSecurityError> {
    validate_relative_name(name).map_err(|_| {
        FinalizerSecurityError::new("authority_finalizer_generation_relative_name_invalid")
    })?;
    let mut words = name.encode_utf16().collect::<Vec<_>>();
    let name_bytes = words
        .len()
        .checked_mul(2)
        .and_then(|value| u16::try_from(value).ok())
        .ok_or(FinalizerSecurityError::new(
            "authority_finalizer_generation_relative_name_invalid",
        ))?;
    let unicode = UNICODE_STRING {
        Length: name_bytes,
        MaximumLength: name_bytes,
        Buffer: words.as_mut_ptr(),
    };
    let attributes = OBJECT_ATTRIBUTES {
        Length: size_of::<OBJECT_ATTRIBUTES>() as u32,
        RootDirectory: parent.as_raw_handle().cast(),
        ObjectName: &unicode,
        Attributes: OBJ_CASE_INSENSITIVE as u32,
        SecurityDescriptor: ptr::null(),
        SecurityQualityOfService: ptr::null(),
    };
    let mut status_block = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let mut handle = ptr::null_mut();
    let desired_access = READ_CONTROL
        | SYNCHRONIZE
        | FILE_LIST_DIRECTORY
        | FILE_TRAVERSE
        | FILE_READ_ATTRIBUTES
        | FILE_READ_EA
        | ACCESS_SYSTEM_SECURITY
        | if for_sealing { WRITE_DAC } else { 0 };
    let status = unsafe {
        NtCreateFile(
            &mut handle,
            desired_access,
            &attributes,
            &mut status_block,
            ptr::null(),
            0,
            0,
            FILE_OPEN,
            FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT | FILE_DIRECTORY_FILE,
            ptr::null(),
            0,
        )
    };
    if status < 0
        || handle.is_null()
        || handle == INVALID_HANDLE_VALUE
        || status_block.Information != FILE_OPENED_INFORMATION
    {
        if !handle.is_null() && handle != INVALID_HANDLE_VALUE {
            unsafe { drop(OwnedHandle::from_raw_handle(handle as RawHandle)) };
        }
        return Err(FinalizerSecurityError::new(
            "authority_finalizer_generation_relative_directory_open_failed",
        ));
    }
    Ok(unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) })
}

fn open_absolute_directory(
    path: &Path,
    for_sealing: bool,
) -> Result<OwnedHandle, FinalizerSecurityError> {
    if !path.is_absolute() {
        return Err(FinalizerSecurityError::new(
            "authority_finalizer_generation_root_path_invalid",
        ));
    }
    let words = wide_null(path);
    let desired_access = READ_CONTROL
        | SYNCHRONIZE
        | FILE_LIST_DIRECTORY
        | FILE_TRAVERSE
        | FILE_READ_ATTRIBUTES
        | FILE_READ_EA
        | ACCESS_SYSTEM_SECURITY
        | if for_sealing { WRITE_DAC } else { 0 };
    let handle = unsafe {
        CreateFileW(
            words.as_ptr(),
            desired_access,
            FILE_SHARE_READ,
            ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            ptr::null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE || handle.is_null() {
        return Err(FinalizerSecurityError::new(
            "authority_finalizer_generation_root_open_failed",
        ));
    }
    Ok(unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) })
}

fn reopen_directory_writer_exclusion(
    directory: &OwnedHandle,
) -> Result<OwnedHandle, GenerationSealError> {
    let desired_access =
        READ_CONTROL | SYNCHRONIZE | FILE_READ_ATTRIBUTES | FILE_READ_EA | ACCESS_SYSTEM_SECURITY;
    let handle = unsafe {
        ReOpenFile(
            directory.as_raw_handle().cast(),
            desired_access,
            FILE_SHARE_READ,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        )
    };
    if handle == INVALID_HANDLE_VALUE || handle.is_null() {
        return Err(GenerationSealError(
            "authority_generation_seal_writer_exclusion_failed",
        ));
    }
    Ok(unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) })
}

fn require_exact_handle_path(
    handle: &OwnedHandle,
    expected: &Path,
) -> Result<(), GenerationSealError> {
    if !expected.is_absolute() {
        return Err(GenerationSealError(
            "authority_generation_seal_path_invalid",
        ));
    }
    let actual = canonical_handle_path(handle)?;
    if !actual.eq_ignore_ascii_case(expected.to_string_lossy().as_ref()) {
        return Err(GenerationSealError(
            "authority_generation_seal_path_alias_rejected",
        ));
    }
    Ok(())
}

fn canonical_handle_path(handle: &OwnedHandle) -> Result<String, GenerationSealError> {
    let mut words = vec![0u16; 32_768];
    let length = unsafe {
        GetFinalPathNameByHandleW(
            handle.as_raw_handle().cast(),
            words.as_mut_ptr(),
            words.len() as u32,
            0,
        )
    } as usize;
    if length == 0 || length >= words.len() {
        return Err(GenerationSealError(
            "authority_generation_seal_path_readback_failed",
        ));
    }
    words.truncate(length);
    if words.contains(&0) {
        return Err(GenerationSealError(
            "authority_generation_seal_path_readback_failed",
        ));
    }
    let actual = OsString::from_wide(&words).to_string_lossy().into_owned();
    Ok(actual
        .strip_prefix(r"\\?\UNC\")
        .map(|value| format!(r"\\{value}"))
        .or_else(|| actual.strip_prefix(r"\\?\").map(str::to_string))
        .unwrap_or(actual))
}

fn protected_blob_canonical_path_sha256(path: &str) -> Digest32 {
    let mut digest = Sha256::new();
    digest.update(PROTECTED_BLOB_NAMESPACE_CANONICAL_PATH_DOMAIN);
    let folded = path.to_ascii_lowercase();
    digest.update((folded.len() as u64).to_be_bytes());
    digest.update(folded.as_bytes());
    digest.finalize().into()
}

fn protected_blob_empty_inventory_sha256(
    generation_sha256: Digest32,
    volume_serial: u64,
    file_id: [u8; 16],
    canonical_path_sha256: Digest32,
) -> Digest32 {
    let mut digest = Sha256::new();
    digest.update(PROTECTED_BLOB_NAMESPACE_EMPTY_INVENTORY_DOMAIN);
    digest.update(generation_sha256);
    digest.update(volume_serial.to_be_bytes());
    digest.update(file_id);
    digest.update(canonical_path_sha256);
    digest.update(0u64.to_be_bytes());
    digest.finalize().into()
}

fn validate_relative_name(name: &str) -> Result<(), GenerationSealError> {
    if name.is_empty()
        || name == "."
        || name == ".."
        || name.len() > 255
        || name.contains(['\\', '/', '\0', ':'])
    {
        return Err(GenerationSealError(
            "authority_generation_seal_relative_name_invalid",
        ));
    }
    Ok(())
}

fn wide_null(path: &Path) -> Vec<u16> {
    path.as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

fn role_index(role: GenerationSealObjectRole) -> usize {
    GenerationSealObjectRole::ALL_IN_SEAL_ORDER
        .iter()
        .position(|value| *value == role)
        .expect("all generation seal roles have a fixed index")
}

fn root_capability_kind(root: GenerationSealRoot) -> FinalizerRootCapabilityKind {
    match root {
        GenerationSealRoot::BinaryGeneration | GenerationSealRoot::StateGeneration => {
            FinalizerRootCapabilityKind::GenerationDirectory
        }
        GenerationSealRoot::ActivationsNamespace => {
            FinalizerRootCapabilityKind::ActivationManifestNamespace
        }
        GenerationSealRoot::WorkerNonceNamespace => {
            FinalizerRootCapabilityKind::WorkerNonceNamespace
        }
        GenerationSealRoot::CandidateConsumptionNamespace => {
            FinalizerRootCapabilityKind::CandidateConsumptionNamespace
        }
    }
}

fn domain_digest(domain: &[u8], bytes: &[u8]) -> Digest32 {
    let mut digest = Sha256::new();
    digest.update(domain);
    digest.update((bytes.len() as u64).to_be_bytes());
    digest.update(bytes);
    digest.finalize().into()
}

fn sha256_bytes(bytes: &[u8]) -> Digest32 {
    Sha256::digest(bytes).into()
}

fn decode_required_digest(value: &str) -> Result<Digest32, GenerationSealError> {
    decode_hex_32(value)
        .filter(|digest| !is_zero_digest(digest))
        .ok_or(GenerationSealError(
            "authority_generation_seal_digest_invalid",
        ))
}

fn decode_hex_32(value: &str) -> Option<Digest32> {
    decode_hex_exact::<32>(value)
}

fn decode_hex_16(value: &str) -> Option<[u8; 16]> {
    decode_hex_exact::<16>(value)
}

fn decode_hex_exact<const N: usize>(value: &str) -> Option<[u8; N]> {
    if value.len() != N * 2
        || value
            .bytes()
            .any(|byte| !byte.is_ascii_digit() && !(b'a'..=b'f').contains(&byte))
    {
        return None;
    }
    let mut output = [0u8; N];
    for (index, slot) in output.iter_mut().enumerate() {
        let high = hex_nibble(value.as_bytes()[index * 2])?;
        let low = hex_nibble(value.as_bytes()[index * 2 + 1])?;
        *slot = (high << 4) | low;
    }
    Some(output)
}

fn hex_nibble(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        _ => None,
    }
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

fn is_zero_digest(value: &Digest32) -> bool {
    value.iter().all(|byte| *byte == 0)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct GenerationSealError(&'static str);

impl GenerationSealError {
    pub(super) fn code(&self) -> &'static str {
        self.0
    }
}

impl From<FinalizerSecurityError> for GenerationSealError {
    fn from(value: FinalizerSecurityError) -> Self {
        Self(value.code())
    }
}

impl From<GenerationSealError> for FinalizerSecurityError {
    fn from(value: GenerationSealError) -> Self {
        FinalizerSecurityError::new(value.code())
    }
}

impl From<AuthorityMaintenanceError> for GenerationSealError {
    fn from(value: AuthorityMaintenanceError) -> Self {
        Self(value.code())
    }
}

impl fmt::Display for GenerationSealError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for GenerationSealError {}

#[cfg(test)]
mod tests {
    use super::*;

    fn materials() -> GenerationSealMaterials {
        GenerationSealMaterials::new(
            AuthorityInstallContent::new(
                AuthorityPayloadDigest::new([11; 32], 700_000).unwrap(),
                AuthorityPayloadDigest::new([12; 32], 800_000).unwrap(),
                AuthorityPayloadDigest::new([13; 32], 900_000).unwrap(),
                AuthorityPayloadDigest::new([14; 32], 1_000_000).unwrap(),
                AuthorityPayloadDigest::new([15; 32], 1_100_000).unwrap(),
                AuthorityPayloadDigest::new([16; 32], 1_200_000).unwrap(),
            )
            .unwrap(),
            GenerationSealRunnerPolicyExpectation::from_descriptor(
                RunnerPolicyStateDescriptor::exact_test_fixture(
                    [3; 32], [4; 32], 640, [20; 32], [27; 32],
                ),
            )
            .unwrap(),
            GenerationSealFileExpectation::new(512, [21; 32]).unwrap(),
            GenerationSealLedgerAnchorExpectation::new(576, [26; 32]).unwrap(),
            GenerationSealFileExpectation::new(600, [22; 32]).unwrap(),
            GenerationSealFileExpectation::new(700, [23; 32]).unwrap(),
            [31; 32],
            GenerationSealFileExpectation::new(800, [24; 32]).unwrap(),
            [32; 32],
            GenerationSealFileExpectation::new(900, [25; 32]).unwrap(),
        )
        .unwrap()
    }

    fn manifest() -> GenerationSealManifest {
        let materials = materials();
        let object_manifest_sha256 = materials.object_manifest_sha256([3; 32], [4; 32]).unwrap();
        GenerationSealManifest::new(
            GenerationSealBinding::new(
                [1; 32],
                [2; 32],
                [3; 32],
                [4; 32],
                object_manifest_sha256,
                [91; 32],
            )
            .unwrap(),
            &materials,
        )
        .unwrap()
    }

    fn inventory(manifest: &GenerationSealManifest) -> GenerationSealInventory {
        let mut inventory = GenerationSealInventory::default();
        let mut next_id = 1u64;
        for root in [
            GenerationSealRoot::BinaryGeneration,
            GenerationSealRoot::StateGeneration,
            GenerationSealRoot::ActivationsNamespace,
            GenerationSealRoot::WorkerNonceNamespace,
            GenerationSealRoot::CandidateConsumptionNamespace,
        ] {
            let mut entries = Vec::new();
            for object in manifest
                .objects
                .iter()
                .filter(|object| object.root == root && object.relative_path != ".")
            {
                let mut file_id = [0u8; 16];
                file_id[..8].copy_from_slice(&next_id.to_be_bytes());
                next_id += 1;
                entries.push(DirectoryInventoryEntry {
                    relative_name: object.relative_path.clone(),
                    file_id,
                    byte_length: object.expected_byte_length.unwrap_or(0),
                    is_directory: object.object_type == SealedObjectKind::Directory,
                    is_reparse: false,
                });
            }
            if matches!(
                root,
                GenerationSealRoot::ActivationsNamespace
                    | GenerationSealRoot::WorkerNonceNamespace
                    | GenerationSealRoot::CandidateConsumptionNamespace
            ) {
                let mut historical_id = [0u8; 16];
                historical_id[..8].copy_from_slice(&next_id.to_be_bytes());
                next_id += 1;
                entries.push(DirectoryInventoryEntry {
                    relative_name: format!("historical-{next_id}.json"),
                    file_id: historical_id,
                    byte_length: 10,
                    is_directory: false,
                    is_reparse: false,
                });
            }
            inventory.insert(root, entries);
        }
        inventory
    }

    fn receipts(manifest: &GenerationSealManifest) -> Vec<GenerationSealedObjectReceipt> {
        manifest
            .objects
            .iter()
            .enumerate()
            .map(|(index, object)| {
                let mut file_id = [0u8; 16];
                file_id[..8].copy_from_slice(&((index + 100) as u64).to_be_bytes());
                GenerationSealedObjectReceipt {
                    role: object.role,
                    root: object.root,
                    relative_path: object.relative_path.clone(),
                    object_type: object.object_type,
                    volume_serial: if object.root == GenerationSealRoot::BinaryGeneration {
                        10
                    } else {
                        20
                    },
                    file_id: hex_lower(&file_id),
                    link_count: 1,
                    attributes: if object.object_type == SealedObjectKind::Directory {
                        FILE_ATTRIBUTE_DIRECTORY
                    } else {
                        FILE_ATTRIBUTE_NORMAL
                    },
                    byte_length: object.expected_byte_length.unwrap_or(0),
                    bytes_sha256: object.expected_bytes_sha256.clone(),
                    staging_security_sha256: object.staging_security_sha256.clone(),
                    final_security_sha256: object.final_security_sha256.clone(),
                    write_handle_closed_before_reopen: true,
                    read_only_reopen_verified: true,
                    recovered_exact_sealed_after_restart: false,
                }
            })
            .collect()
    }

    fn inventory_for_receipts(
        manifest: &GenerationSealManifest,
        receipts: &[GenerationSealedObjectReceipt],
    ) -> GenerationSealInventory {
        let mut value = inventory(manifest);
        for (planned, receipt) in manifest.objects.iter().zip(receipts) {
            if planned.relative_path == "." {
                continue;
            }
            let selected = value
                .entries
                .get_mut(&planned.root)
                .unwrap()
                .iter_mut()
                .find(|entry| entry.relative_name == planned.relative_path)
                .unwrap();
            selected.file_id = decode_hex_16(&receipt.file_id).unwrap();
            selected.byte_length = receipt.byte_length;
        }
        value
    }

    fn writer_closure(manifest: &GenerationSealManifest) -> PreSealWriterClosureReadback {
        PreSealWriterClosureReadback::new(
            manifest, [40; 32], [41; 32], [42; 32], [43; 32], [44; 32], [45; 32], 0, 0, 0, 0, 0,
            true,
        )
        .unwrap()
    }

    fn checkpoint_writer() -> CheckpointWriterAuthorization {
        CheckpointWriterAuthorization::exact_test_fixture(40, 46)
    }

    #[derive(Default)]
    struct MemoryProgressStore {
        manifest_sha256: Option<Digest32>,
        chain: Vec<GenerationSealDurableReadback>,
        fail_after_durable_sequence: Option<u32>,
    }

    impl GenerationSealProgressStore for MemoryProgressStore {
        fn load_exact_chain(
            &mut self,
            manifest_sha256: Digest32,
        ) -> Result<Vec<GenerationSealDurableReadback>, GenerationSealError> {
            if self
                .manifest_sha256
                .is_some_and(|observed| observed != manifest_sha256)
            {
                return Err(GenerationSealError(
                    "authority_generation_seal_test_store_binding_mismatch",
                ));
            }
            Ok(self.chain.clone())
        }

        fn compare_and_swap_create_new(
            &mut self,
            manifest_sha256: Digest32,
            expected_previous_checkpoint_sha256: Option<Digest32>,
            checkpoint_bytes: &[u8],
        ) -> Result<GenerationSealDurableReadback, GenerationSealError> {
            if self
                .manifest_sha256
                .is_some_and(|observed| observed != manifest_sha256)
                || self.chain.last().map(|value| value.checkpoint_sha256)
                    != expected_previous_checkpoint_sha256
            {
                return Err(GenerationSealError(
                    "authority_generation_seal_test_store_cas_mismatch",
                ));
            }
            self.manifest_sha256 = Some(manifest_sha256);
            let raw: serde_json::Value =
                serde_json::from_slice(checkpoint_bytes).map_err(|_| {
                    GenerationSealError("authority_generation_seal_test_store_parse_failed")
                })?;
            let sequence = raw
                .get("sequence")
                .and_then(serde_json::Value::as_u64)
                .and_then(|value| u32::try_from(value).ok())
                .ok_or(GenerationSealError(
                    "authority_generation_seal_test_store_parse_failed",
                ))?;
            if sequence as usize != self.chain.len() {
                return Err(GenerationSealError(
                    "authority_generation_seal_test_store_sequence_mismatch",
                ));
            }
            let mut file_id = [0u8; 16];
            file_id[..8].copy_from_slice(&(u64::from(sequence) + 1).to_be_bytes());
            let descriptor_sha256 = expected_publication_security_sha256(
                FinalizerPublicationSecurityPhase::PublishedImmutable,
            )
            .unwrap();
            let checkpoint_sha256 =
                domain_digest(GENERATION_SEAL_PROGRESS_DOMAIN, checkpoint_bytes);
            let readback = GenerationSealDurableReadback::new(
                generation_progress_relative_name(&manifest_sha256, sequence),
                checkpoint_bytes.to_vec(),
                checkpoint_sha256,
                [91; 32],
                92,
                file_id,
                1,
                checkpoint_bytes.len() as u64,
                sha256_bytes(checkpoint_bytes),
                descriptor_sha256,
                true,
                true,
                true,
                true,
                true,
                true,
                true,
                true,
            );
            self.chain.push(readback.clone());
            if self.fail_after_durable_sequence == Some(sequence) {
                self.fail_after_durable_sequence = None;
                return Err(GenerationSealError(
                    "authority_generation_seal_test_store_post_flush_failure",
                ));
            }
            Ok(readback)
        }
    }

    fn persist_genesis(
        store: &mut MemoryProgressStore,
        manifest: &GenerationSealManifest,
        closure: &PreSealWriterClosureReadback,
    ) -> (
        Vec<GenerationSealDurableReadback>,
        GenerationSealProgressCheckpoint,
    ) {
        let genesis =
            GenerationSealProgressCheckpoint::genesis(manifest, closure, checkpoint_writer())
                .unwrap();
        let chain = persist_progress_checkpoint(store, manifest, closure, &[], &genesis).unwrap();
        (chain, genesis)
    }

    #[test]
    fn plan_covers_exact_fixed_generation_object_set_and_large_binaries() {
        let value = manifest();
        value.validate().unwrap();
        assert_eq!(
            GENERATION_SEAL_MANIFEST_SCHEMA,
            "vrcforge.authority.generation-seal-manifest.v4"
        );
        assert_eq!(
            GENERATION_OBJECT_MANIFEST_SCHEMA,
            "vrcforge.authority.generation-object-manifest.v4"
        );
        assert_eq!(GENERATION_SEAL_OBJECT_COUNT, 16);
        assert_eq!(GenerationSealObjectRole::ALL_IN_SEAL_ORDER.len(), 16);
        assert_eq!(GENERATION_SEAL_TERMINAL_SEQUENCE, 33);
        assert_eq!(GENERATION_SEAL_TERMINAL_SEQUENCE as usize + 1, 34);
        assert_eq!(value.objects.len(), GENERATION_SEAL_OBJECT_COUNT);
        assert_eq!(
            value
                .objects
                .iter()
                .map(|object| object.role)
                .collect::<Vec<_>>(),
            GenerationSealObjectRole::ALL_IN_SEAL_ORDER
        );
        assert_eq!(
            value
                .object(GenerationSealObjectRole::ServiceBinary)
                .expected_byte_length,
            Some(700_000)
        );
        let ledger_anchor = value.object(GenerationSealObjectRole::LedgerAnchor);
        assert_eq!(ledger_anchor.root, GenerationSealRoot::StateGeneration);
        assert_eq!(ledger_anchor.relative_path, "ledger.bin.anchor");
        assert_eq!(ledger_anchor.expected_byte_length, Some(576));
        assert_eq!(
            ledger_anchor.expected_bytes_sha256.as_deref(),
            Some(hex_lower(&[26; 32]).as_str())
        );
        let runner_policy = value.object(GenerationSealObjectRole::RunnerPolicyState);
        assert_eq!(runner_policy.root, GenerationSealRoot::StateGeneration);
        assert_eq!(runner_policy.relative_path, RUNNER_POLICY_STATE_FILE_NAME);
        assert_eq!(runner_policy.expected_byte_length, Some(640));
        assert_eq!(
            runner_policy.expected_bytes_sha256.as_deref(),
            Some(hex_lower(&[20; 32]).as_str())
        );
        let directory_count = value
            .objects
            .iter()
            .filter(|object| object.object_type == SealedObjectKind::Directory)
            .count();
        assert_eq!(directory_count, 3);
        assert!(value
            .objects
            .iter()
            .take(value.objects.len() - directory_count)
            .all(|object| object.object_type == SealedObjectKind::File));
        assert!(value
            .objects
            .iter()
            .skip(value.objects.len() - directory_count)
            .all(|object| object.object_type == SealedObjectKind::Directory));
    }

    #[test]
    fn plan_binding_rejects_a_different_object_manifest_digest() {
        let materials = materials();
        let actual = materials.object_manifest_sha256([3; 32], [4; 32]).unwrap();
        assert_ne!(actual, [99; 32]);
        assert_eq!(
            GenerationSealManifest::new(
                GenerationSealBinding::new([1; 32], [2; 32], [3; 32], [4; 32], [99; 32], [91; 32],)
                    .unwrap(),
                &materials,
            )
            .unwrap_err()
            .code(),
            "authority_generation_seal_plan_object_manifest_mismatch"
        );
    }

    #[test]
    fn runner_policy_expectation_is_typed_and_generation_and_transaction_bound() {
        let materials = materials();
        assert_eq!(
            materials
                .object_manifest_sha256([4; 32], [4; 32])
                .unwrap_err()
                .code(),
            "authority_generation_seal_runner_policy_mismatch"
        );
        assert_eq!(
            materials
                .object_manifest_sha256([3; 32], [5; 32])
                .unwrap_err()
                .code(),
            "authority_generation_seal_runner_policy_mismatch"
        );
        let descriptor = RunnerPolicyStateDescriptor::exact_test_fixture(
            [3; 32], [4; 32], 640, [20; 32], [27; 32],
        );
        let expectation = GenerationSealRunnerPolicyExpectation::from_descriptor(descriptor)
            .expect("canonical typed runner policy descriptor");
        assert_eq!(expectation.generation_sha256, [3; 32]);
        assert_eq!(expectation.transaction_sha256, [4; 32]);
        assert_eq!(expectation.binding_sha256, [27; 32]);
    }

    #[test]
    fn runner_policy_binding_is_explicit_in_the_v4_object_manifest() {
        let exact = materials();
        let exact_digest = exact.object_manifest_sha256([3; 32], [4; 32]).unwrap();
        let mut drift = materials();
        drift.runner_policy.binding_sha256 = [28; 32];
        let drift_digest = drift.object_manifest_sha256([3; 32], [4; 32]).unwrap();
        assert_ne!(exact_digest, drift_digest);

        let exact_manifest = manifest();
        assert_eq!(
            exact_manifest.runner_policy_binding_sha256().unwrap(),
            [27; 32]
        );
        let mut field_drift = exact_manifest;
        field_drift.runner_policy_binding_sha256 = hex_lower(&[28; 32]);
        assert_eq!(
            field_drift.validate().unwrap_err().code(),
            "authority_generation_seal_plan_object_manifest_mismatch"
        );
    }

    #[test]
    fn generation_inventory_rejects_extra_missing_reparse_and_case_alias() {
        let manifest = manifest();
        let exact = inventory(&manifest);
        exact.validate_against(&manifest).unwrap();

        let mut extra = exact.clone();
        extra
            .entries
            .get_mut(&GenerationSealRoot::BinaryGeneration)
            .unwrap()
            .push(DirectoryInventoryEntry {
                relative_name: "unlisted.exe".to_string(),
                file_id: [77; 16],
                byte_length: 1,
                is_directory: false,
                is_reparse: false,
            });
        assert_eq!(
            extra.validate_against(&manifest).unwrap_err().code(),
            "authority_generation_seal_inventory_not_exhaustive"
        );

        let mut missing = exact.clone();
        missing
            .entries
            .get_mut(&GenerationSealRoot::StateGeneration)
            .unwrap()
            .retain(|entry| entry.relative_name != "ledger.bin.anchor");
        assert_eq!(
            missing.validate_against(&manifest).unwrap_err().code(),
            "authority_generation_seal_inventory_not_exhaustive"
        );

        let mut reparse = exact.clone();
        reparse
            .entries
            .get_mut(&GenerationSealRoot::BinaryGeneration)
            .unwrap()[0]
            .is_reparse = true;
        assert_eq!(
            reparse.validate_against(&manifest).unwrap_err().code(),
            "authority_generation_seal_inventory_entry_invalid"
        );

        let mut alias = exact;
        let activation = manifest
            .object(GenerationSealObjectRole::ActivationManifest)
            .relative_path
            .to_ascii_uppercase();
        alias
            .entries
            .get_mut(&GenerationSealRoot::ActivationsNamespace)
            .unwrap()
            .push(DirectoryInventoryEntry {
                relative_name: activation,
                file_id: [78; 16],
                byte_length: 1,
                is_directory: false,
                is_reparse: false,
            });
        assert_eq!(
            alias.validate_against(&manifest).unwrap_err().code(),
            "authority_generation_seal_inventory_alias"
        );
    }

    #[test]
    fn receipt_requires_every_object_individually_and_directories_last() {
        let manifest = manifest();
        let exact = GenerationSealReceipt::new(&manifest, receipts(&manifest)).unwrap();
        let bytes = exact.canonical_bytes(&manifest).unwrap();
        assert_eq!(
            GenerationSealReceipt::parse_canonical(&bytes, &manifest).unwrap(),
            exact
        );

        let mut missing = receipts(&manifest);
        missing.remove(4);
        assert_eq!(
            GenerationSealReceipt::new(&manifest, missing)
                .unwrap_err()
                .code(),
            "authority_generation_seal_receipt_invalid"
        );

        let mut reordered = receipts(&manifest);
        reordered.swap(0, 10);
        assert!(GenerationSealReceipt::new(&manifest, reordered).is_err());

        let mut directory_only = receipts(&manifest);
        directory_only.drain(..10);
        assert!(GenerationSealReceipt::new(&manifest, directory_only).is_err());

        let mut missing_attribute: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        missing_attribute["objects"][0]
            .as_object_mut()
            .unwrap()
            .remove("attributes");
        assert!(GenerationSealReceipt::parse_canonical(
            &serde_json::to_vec(&missing_attribute).unwrap(),
            &manifest,
        )
        .is_err());

        let mut legacy = exact.clone();
        legacy.schema = "vrcforge.authority.generation-seal-receipt.v3".to_string();
        assert_eq!(
            legacy.validate_against(&manifest).unwrap_err().code(),
            "authority_generation_seal_receipt_invalid"
        );
    }

    #[test]
    fn receipt_rejects_hardlink_identity_and_content_or_security_drift() {
        let manifest = manifest();
        let mut hardlink = receipts(&manifest);
        hardlink[0].link_count = 2;
        assert!(GenerationSealReceipt::new(&manifest, hardlink).is_err());

        let mut content = receipts(&manifest);
        content[0].bytes_sha256 = Some(hex_lower(&[99; 32]));
        assert_eq!(
            GenerationSealReceipt::new(&manifest, content)
                .unwrap_err()
                .code(),
            "authority_generation_seal_receipt_object_mismatch"
        );

        let mut security = receipts(&manifest);
        security[0].final_security_sha256 = hex_lower(&[98; 32]);
        assert_eq!(
            GenerationSealReceipt::new(&manifest, security)
                .unwrap_err()
                .code(),
            "authority_generation_seal_receipt_object_mismatch"
        );
    }

    #[test]
    fn receipt_rejects_same_length_runner_policy_drift_at_the_seventh_object() {
        let manifest = manifest();
        let runner_index = role_index(GenerationSealObjectRole::RunnerPolicyState);
        assert_eq!(runner_index, 6);
        let mut drift = receipts(&manifest);
        let original_length = drift[runner_index].byte_length;
        drift[runner_index].bytes_sha256 = Some(hex_lower(&[99; 32]));
        assert_eq!(drift[runner_index].byte_length, original_length);
        assert_eq!(
            GenerationSealReceipt::new(&manifest, drift)
                .unwrap_err()
                .code(),
            "authority_generation_seal_receipt_object_mismatch"
        );
    }

    #[test]
    fn runner_policy_sealed_attributes_are_bound_into_restart_and_terminal_identity() {
        let manifest = manifest();
        let runner_index = role_index(GenerationSealObjectRole::RunnerPolicyState);
        let receipt = GenerationSealReceipt::new(&manifest, receipts(&manifest)).unwrap();
        let baseline_restart = restart_readback_digest(&manifest, &receipt).unwrap();
        let baseline_identity = runner_policy_identity_from_receipt(&manifest, &receipt).unwrap();

        let mut changed_objects = receipt.objects.clone();
        changed_objects[runner_index].attributes ^= 0x20;
        let changed = GenerationSealReceipt::new(&manifest, changed_objects).unwrap();
        assert_ne!(
            restart_readback_digest(&manifest, &changed).unwrap(),
            baseline_restart
        );
        assert_ne!(
            runner_policy_identity_from_receipt(&manifest, &changed).unwrap(),
            baseline_identity
        );
        assert_eq!(
            GENERATION_SEAL_RECEIPT_SCHEMA,
            "vrcforge.authority.generation-seal-receipt.v5"
        );
        assert_eq!(
            GENERATION_SEAL_RESTART_READBACK_SCHEMA,
            "vrcforge.authority.generation-seal-restart-readback.v5"
        );
        assert_eq!(
            GENERATION_SEAL_PROGRESS_SCHEMA,
            "vrcforge.authority.generation-seal-progress.v5"
        );
    }

    #[test]
    fn canonical_manifest_and_restart_receipt_are_plan_bound() {
        let manifest = manifest();
        let manifest_bytes = manifest.canonical_bytes().unwrap();
        assert_eq!(
            GenerationSealManifest::parse_canonical(&manifest_bytes).unwrap(),
            manifest
        );
        let receipt = GenerationSealReceipt::new(&manifest, receipts(&manifest)).unwrap();
        let restart =
            GenerationSealRestartReadback::from_verified_reopen(&manifest, &receipt).unwrap();
        restart.validate(&manifest, &receipt).unwrap();

        let mut changed_plan = manifest.clone();
        changed_plan.plan_sha256 = hex_lower(&[88; 32]);
        assert!(restart.validate(&changed_plan, &receipt).is_err());

        let mut tampered = restart;
        tampered.readback_sha256 = hex_lower(&[89; 32]);
        assert_eq!(
            tampered.validate(&manifest, &receipt).unwrap_err().code(),
            "authority_generation_seal_restart_readback_invalid"
        );

        let mut legacy =
            GenerationSealRestartReadback::from_verified_reopen(&manifest, &receipt).unwrap();
        legacy.schema = "vrcforge.authority.generation-seal-restart-readback.v3".to_string();
        assert_eq!(
            legacy.validate(&manifest, &receipt).unwrap_err().code(),
            "authority_generation_seal_restart_readback_invalid"
        );
    }

    #[test]
    fn pre_seal_writer_closure_rejects_every_nonempty_writer_roster() {
        let manifest = manifest();
        let exact = writer_closure(&manifest);
        exact.validate_against(&manifest).unwrap();
        let bytes = exact.canonical_bytes(&manifest).unwrap();
        assert_eq!(
            PreSealWriterClosureReadback::parse_canonical(&bytes, &manifest).unwrap(),
            exact
        );

        for field in 0..5 {
            let mut changed = exact.clone();
            match field {
                0 => changed.worker_writer_handle_count = 1,
                1 => changed.candidate_writer_handle_count = 1,
                2 => changed.finalizer_writer_handle_count = 1,
                3 => changed.generation_writer_handle_count = 1,
                4 => changed.ledger_writer_handle_count = 1,
                _ => unreachable!(),
            }
            assert_eq!(
                changed.validate_against(&manifest).unwrap_err().code(),
                "authority_generation_seal_writer_closure_incomplete"
            );
        }
    }

    #[test]
    fn durable_progress_recovers_every_object_boundary_and_only_terminalizes_after_all_objects() {
        let manifest = manifest();
        let closure = writer_closure(&manifest);
        let expected_receipts = receipts(&manifest);
        let mut store = MemoryProgressStore::default();
        let (mut chain, mut checkpoint) = persist_genesis(&mut store, &manifest, &closure);

        for (index, expected_receipt) in expected_receipts.iter().cloned().enumerate() {
            let identity = expected_receipt.stable_identity().unwrap();
            let intent = GenerationSealProgressCheckpoint::with_intent(
                &checkpoint,
                &manifest,
                &closure,
                &identity,
                checkpoint_writer(),
            )
            .unwrap();
            chain = persist_progress_checkpoint(&mut store, &manifest, &closure, &chain, &intent)
                .unwrap();

            // A restart before mutation resumes from exact staging.  A restart
            // after ACL mutation but before completion accepts only exact
            // sealed identity/content/security and writes the missing progress.
            let (_, recovered) = recover_progress_chain(&mut store, &manifest, &closure).unwrap();
            let recovered_intent = recovered.last().unwrap();
            assert_eq!(recovered_intent, &intent);
            assert_eq!(
                recovery_action_for_phase(
                    recovered_intent,
                    &manifest,
                    &closure,
                    manifest.objects[index].role,
                    FinalizerObservedSealPhase::ExactStaging,
                )
                .unwrap(),
                GenerationSealRecoveryAction::SealFromExactStaging
            );
            assert_eq!(
                recovery_action_for_phase(
                    recovered_intent,
                    &manifest,
                    &closure,
                    manifest.objects[index].role,
                    FinalizerObservedSealPhase::ExactSealed,
                )
                .unwrap(),
                GenerationSealRecoveryAction::AcceptExactSealed
            );

            let completion = GenerationSealProgressCheckpoint::with_completion(
                &intent,
                &manifest,
                &closure,
                expected_receipt,
                checkpoint_writer(),
            )
            .unwrap();
            chain =
                persist_progress_checkpoint(&mut store, &manifest, &closure, &chain, &completion)
                    .unwrap();
            let (_, recovered) = recover_progress_chain(&mut store, &manifest, &closure).unwrap();
            checkpoint = recovered.last().unwrap().clone();
            assert_eq!(checkpoint.completed_objects.len(), index + 1);
            assert!(!checkpoint.terminal);
        }

        assert_eq!(checkpoint.sequence, GENERATION_SEAL_TERMINAL_SEQUENCE - 1);
        let receipt =
            GenerationSealReceipt::new(&manifest, checkpoint.completed_objects.clone()).unwrap();
        assert_eq!(receipt.objects.len(), GENERATION_SEAL_OBJECT_COUNT);
        let terminal = GenerationSealProgressCheckpoint::into_terminal(
            &checkpoint,
            &manifest,
            &closure,
            CheckpointWriterAuthorization::exact_test_fixture(47, 48),
        )
        .unwrap();
        chain = persist_progress_checkpoint(&mut store, &manifest, &closure, &chain, &terminal)
            .unwrap();
        assert_eq!(terminal.sequence, GENERATION_SEAL_TERMINAL_SEQUENCE);
        assert!(terminal.terminal);
        assert_eq!(chain.len(), GENERATION_SEAL_TERMINAL_SEQUENCE as usize + 1);
        assert!(manifest.objects[..GENERATION_SEAL_OBJECT_COUNT - 3]
            .iter()
            .all(|object| object.object_type == SealedObjectKind::File));
        assert!(manifest.objects[GENERATION_SEAL_OBJECT_COUNT - 3..]
            .iter()
            .all(|object| object.object_type == SealedObjectKind::Directory));

        let final_inventory = inventory_for_receipts(&manifest, &expected_receipts);
        let awaiting = GenerationSealAwaitingRestart::new(
            &manifest,
            &closure,
            &receipt,
            &terminal,
            &chain,
            &final_inventory,
            [77; 32],
        )
        .unwrap();
        assert_eq!(awaiting.binding(), manifest.binding().unwrap());
        assert_eq!(awaiting.manifest_sha256(), manifest.digest().unwrap());
        assert_eq!(awaiting.sealing_invocation_sha256(), [47; 32]);
        assert_eq!(
            awaiting.seal_receipt_sha256(),
            receipt.digest(&manifest).unwrap()
        );
        assert_eq!(
            awaiting.terminal_checkpoint_sha256(),
            terminal.digest(&manifest, &closure).unwrap()
        );
        assert_eq!(awaiting.authenticated_progress_root_sha256(), [91; 32]);
        assert_ne!(awaiting.independent_reopen_readback_sha256(), [0; 32]);
        assert_ne!(awaiting.final_inventory_readback_sha256(), [0; 32]);
        assert_eq!(awaiting.final_root_capabilities_sha256(), [77; 32]);

        let rejected_same_invocation = VerifiedGenerationSealRestart {
            readback: GenerationSealRestartReadback::from_verified_reopen(&manifest, &receipt)
                .unwrap(),
            final_inventory_readback_sha256: final_inventory_readback_digest(
                &manifest,
                &final_inventory,
            )
            .unwrap(),
            final_root_capabilities_sha256: [77; 32],
            runner_policy_identity: runner_policy_identity_from_receipt(&manifest, &receipt)
                .unwrap(),
            protected_blob_namespace: ProtectedBlobNamespaceSealProjection::exact_test_fixture(
                manifest.binding().unwrap().generation_sha256(),
                0x6c,
            ),
        };
        assert_eq!(
            GenerationSealTerminalAuthorization::new(
                &manifest,
                &closure,
                receipt.clone(),
                &terminal,
                &chain,
                [47; 32],
                rejected_same_invocation,
            )
            .unwrap_err()
            .code(),
            "authority_generation_seal_restart_invocation_not_distinct"
        );
        let verified_restart = VerifiedGenerationSealRestart {
            readback: GenerationSealRestartReadback::from_verified_reopen(&manifest, &receipt)
                .unwrap(),
            final_inventory_readback_sha256: final_inventory_readback_digest(
                &manifest,
                &final_inventory,
            )
            .unwrap(),
            final_root_capabilities_sha256: [77; 32],
            runner_policy_identity: runner_policy_identity_from_receipt(&manifest, &receipt)
                .unwrap(),
            protected_blob_namespace: ProtectedBlobNamespaceSealProjection::exact_test_fixture(
                manifest.binding().unwrap().generation_sha256(),
                0x6c,
            ),
        };
        let authorization = GenerationSealTerminalAuthorization::new(
            &manifest,
            &closure,
            receipt.clone(),
            &terminal,
            &chain,
            [49; 32],
            verified_restart,
        )
        .unwrap();
        let projection = authorization.projection();
        assert_eq!(projection.binding(), manifest.binding().unwrap());
        assert_eq!(projection.manifest_sha256(), manifest.digest().unwrap());
        assert_eq!(
            projection.generation_object_manifest_sha256(),
            manifest
                .binding()
                .unwrap()
                .generation_object_manifest_sha256()
        );
        assert_eq!(
            projection.writer_closure_readback_sha256(),
            closure.digest(&manifest).unwrap()
        );
        assert_eq!(projection.sealing_invocation_sha256(), [47; 32]);
        assert_eq!(projection.restart_invocation_sha256(), [49; 32]);
        assert_eq!(projection.sealing_writer_exclusion_sha256(), [48; 32]);
        assert_ne!(projection.seal_receipt_sha256(), [0; 32]);
        assert_ne!(projection.terminal_checkpoint_sha256(), [0; 32]);
        assert_eq!(projection.authenticated_progress_root_sha256(), [91; 32]);
        assert_ne!(projection.restart_readback_sha256(), [0; 32]);
        assert_ne!(projection.final_inventory_readback_sha256(), [0; 32]);
        assert_eq!(projection.final_root_capabilities_sha256(), [77; 32]);
        assert_eq!(
            projection.object_count(),
            GENERATION_SEAL_OBJECT_COUNT as u32
        );
        assert_eq!(
            projection.terminal_sequence(),
            GENERATION_SEAL_TERMINAL_SEQUENCE
        );
        assert_ne!(projection.authorization_sha256(), [0; 32]);

        // Reverification in the same later process epoch is an exact,
        // manifest-bound replay: it cannot produce a different authority.
        let repeated = GenerationSealTerminalAuthorization::new(
            &manifest,
            &closure,
            receipt,
            &terminal,
            &chain,
            [49; 32],
            VerifiedGenerationSealRestart {
                readback: GenerationSealRestartReadback::from_verified_reopen(
                    &manifest,
                    &GenerationSealReceipt::new(&manifest, terminal.completed_objects.clone())
                        .unwrap(),
                )
                .unwrap(),
                final_inventory_readback_sha256: final_inventory_readback_digest(
                    &manifest,
                    &final_inventory,
                )
                .unwrap(),
                final_root_capabilities_sha256: [77; 32],
                runner_policy_identity: runner_policy_identity_from_receipt(
                    &manifest,
                    &GenerationSealReceipt::new(&manifest, terminal.completed_objects.clone())
                        .unwrap(),
                )
                .unwrap(),
                protected_blob_namespace: ProtectedBlobNamespaceSealProjection::exact_test_fixture(
                    manifest.binding().unwrap().generation_sha256(),
                    0x6c,
                ),
            },
        )
        .unwrap();
        assert_eq!(repeated.projection(), projection);
    }

    #[test]
    fn validated_resume_classification_never_treats_invalid_nonempty_chain_as_empty() {
        let manifest = manifest();
        let closure = writer_closure(&manifest);
        let mut empty = MemoryProgressStore::default();
        assert!(
            load_validated_generation_seal_progress(&mut empty, &manifest)
                .unwrap()
                .is_none()
        );

        let mut store = MemoryProgressStore::default();
        let (_, genesis) = persist_genesis(&mut store, &manifest, &closure);
        let validated = load_validated_generation_seal_progress(&mut store, &manifest)
            .unwrap()
            .unwrap();
        let partial = GenerationSealPartialResume::from_validated_chain(
            &manifest,
            &validated.writer_closure,
            &validated.raw_chain,
            &validated.checkpoints,
        )
        .unwrap();
        assert_eq!(
            partial.phase(),
            GenerationSealExecutionPhase::RollForwardRequired {
                progress_tip_sha256: genesis.digest(&manifest, &closure).ok(),
            }
        );
        assert_eq!(partial.writer_epoch(), ([40; 32], [46; 32]));

        let mut aliased = store;
        aliased.chain[0].relative_name = aliased.chain[0].relative_name.to_ascii_uppercase();
        assert!(load_validated_generation_seal_progress(&mut aliased, &manifest).is_err());

        let mut gapped = MemoryProgressStore::default();
        let (chain, genesis) = persist_genesis(&mut gapped, &manifest, &closure);
        let intent = GenerationSealProgressCheckpoint::with_intent(
            &genesis,
            &manifest,
            &closure,
            &receipts(&manifest)[0].stable_identity().unwrap(),
            checkpoint_writer(),
        )
        .unwrap();
        let chain =
            persist_progress_checkpoint(&mut gapped, &manifest, &closure, &chain, &intent).unwrap();
        gapped.chain = chain[1..].to_vec();
        assert!(load_validated_generation_seal_progress(&mut gapped, &manifest).is_err());
    }

    #[test]
    fn progress_writer_epochs_may_advance_but_never_drift_or_reappear() {
        let manifest = manifest();
        let closure = writer_closure(&manifest);
        let expected = receipts(&manifest);
        let writer_a = checkpoint_writer();
        let writer_b = CheckpointWriterAuthorization::exact_test_fixture(47, 48);

        let mut store = MemoryProgressStore::default();
        let (chain, genesis) = persist_genesis(&mut store, &manifest, &closure);
        let intent_a = GenerationSealProgressCheckpoint::with_intent(
            &genesis,
            &manifest,
            &closure,
            &expected[0].stable_identity().unwrap(),
            writer_a,
        )
        .unwrap();
        let chain = persist_progress_checkpoint(&mut store, &manifest, &closure, &chain, &intent_a)
            .unwrap();
        let completion_b = GenerationSealProgressCheckpoint::with_completion(
            &intent_a,
            &manifest,
            &closure,
            expected[0].clone(),
            writer_b,
        )
        .unwrap();
        let chain =
            persist_progress_checkpoint(&mut store, &manifest, &closure, &chain, &completion_b)
                .unwrap();
        let intent_b = GenerationSealProgressCheckpoint::with_intent(
            &completion_b,
            &manifest,
            &closure,
            &expected[1].stable_identity().unwrap(),
            writer_b,
        )
        .unwrap();
        let chain = persist_progress_checkpoint(&mut store, &manifest, &closure, &chain, &intent_b)
            .unwrap();
        let reappearing_a = GenerationSealProgressCheckpoint::with_completion(
            &intent_b,
            &manifest,
            &closure,
            expected[1].clone(),
            writer_a,
        )
        .unwrap();
        assert_eq!(
            persist_progress_checkpoint(&mut store, &manifest, &closure, &chain, &reappearing_a,)
                .unwrap_err()
                .code(),
            "authority_generation_seal_checkpoint_writer_epoch_invalid"
        );

        let mut drift_store = MemoryProgressStore::default();
        let (chain, genesis) = persist_genesis(&mut drift_store, &manifest, &closure);
        let drift = GenerationSealProgressCheckpoint::with_intent(
            &genesis,
            &manifest,
            &closure,
            &expected[0].stable_identity().unwrap(),
            CheckpointWriterAuthorization::exact_test_fixture(40, 99),
        )
        .unwrap();
        assert_eq!(
            persist_progress_checkpoint(&mut drift_store, &manifest, &closure, &chain, &drift,)
                .unwrap_err()
                .code(),
            "authority_generation_seal_checkpoint_writer_epoch_invalid"
        );
    }

    #[test]
    fn progress_rejects_identity_content_security_gap_fork_and_replay() {
        let manifest = manifest();
        let closure = writer_closure(&manifest);
        let expected_receipts = receipts(&manifest);
        let mut store = MemoryProgressStore::default();
        let (chain, genesis) = persist_genesis(&mut store, &manifest, &closure);
        let identity = expected_receipts[0].stable_identity().unwrap();
        let intent = GenerationSealProgressCheckpoint::with_intent(
            &genesis,
            &manifest,
            &closure,
            &identity,
            checkpoint_writer(),
        )
        .unwrap();
        let chain =
            persist_progress_checkpoint(&mut store, &manifest, &closure, &chain, &intent).unwrap();

        let mut changed_identity = intent.clone();
        changed_identity
            .pending_intent
            .as_mut()
            .unwrap()
            .identity
            .file_id = hex_lower(&[99; 16]);
        assert!(GenerationSealProgressCheckpoint::with_completion(
            &changed_identity,
            &manifest,
            &closure,
            expected_receipts[0].clone(),
            checkpoint_writer(),
        )
        .is_err());

        let mut changed_content = intent.clone();
        changed_content
            .pending_intent
            .as_mut()
            .unwrap()
            .identity
            .bytes_sha256 = Some(hex_lower(&[98; 32]));
        assert!(changed_content
            .validate_against(&manifest, &closure)
            .is_err());

        let mut changed_security = intent.clone();
        changed_security
            .pending_intent
            .as_mut()
            .unwrap()
            .final_security_sha256 = hex_lower(&[97; 32]);
        assert!(changed_security
            .validate_against(&manifest, &closure)
            .is_err());

        assert!(validate_durable_progress_chain(&chain[1..], &manifest, &closure).is_err());
        let mut replay = chain.clone();
        replay.push(chain[1].clone());
        assert!(validate_durable_progress_chain(&replay, &manifest, &closure).is_err());
        let mut fork = chain;
        let mut conflicting = fork[1].clone();
        conflicting.file_id = [77; 16];
        fork.push(conflicting);
        assert!(validate_durable_progress_chain(&fork, &manifest, &closure).is_err());
    }

    #[test]
    fn post_flush_intent_error_is_recovered_and_requires_roll_forward() {
        let manifest = manifest();
        let closure = writer_closure(&manifest);
        let mut store = MemoryProgressStore::default();
        let (chain, genesis) = persist_genesis(&mut store, &manifest, &closure);
        let identity = receipts(&manifest)[0].stable_identity().unwrap();
        let intent = GenerationSealProgressCheckpoint::with_intent(
            &genesis,
            &manifest,
            &closure,
            &identity,
            checkpoint_writer(),
        )
        .unwrap();
        store.fail_after_durable_sequence = Some(1);
        assert!(
            persist_progress_checkpoint(&mut store, &manifest, &closure, &chain, &intent,).is_err()
        );
        let (_, recovered) = recover_progress_chain(&mut store, &manifest, &closure).unwrap();
        assert_eq!(recovered.last(), Some(&intent));

        assert_eq!(
            classify_generation_seal_failure(
                GenerationSealError("simulated"),
                &intent,
                &manifest,
                &closure,
            )
            .phase(),
            GenerationSealExecutionPhase::RollForwardRequired {
                progress_tip_sha256: intent.digest(&manifest, &closure).ok(),
            }
        );
        assert_eq!(
            classify_generation_seal_failure(
                GenerationSealError("simulated"),
                &genesis,
                &manifest,
                &closure,
            )
            .phase(),
            GenerationSealExecutionPhase::BeforeDurableIntent
        );
    }

    #[test]
    fn durable_readback_rejects_missing_flush_or_nonexact_descriptor() {
        let manifest = manifest();
        let closure = writer_closure(&manifest);
        let mut store = MemoryProgressStore::default();
        let (chain, _) = persist_genesis(&mut store, &manifest, &closure);
        let mut no_parent_flush = chain[0].clone();
        no_parent_flush.parent_directory_flushed = false;
        assert!(validate_durable_progress_chain(&[no_parent_flush], &manifest, &closure).is_err());

        let mut wrong_descriptor = chain[0].clone();
        wrong_descriptor.descriptor_sha256 = [96; 32];
        assert!(validate_durable_progress_chain(&[wrong_descriptor], &manifest, &closure).is_err());

        let mut split_commit_root = chain[0].clone();
        split_commit_root.store_root_identity_sha256 = [95; 32];
        assert_eq!(
            validate_durable_progress_chain(&[split_commit_root], &manifest, &closure)
                .unwrap_err()
                .code(),
            "authority_generation_seal_progress_durable_readback_invalid"
        );
    }

    #[test]
    fn native_progress_namespace_is_exact_transaction_bound_and_fail_closed() {
        let manifest = manifest();
        let layout = AuthorityLayout::for_test_roots(
            Path::new(r"C:\Program Files"),
            Path::new(r"C:\ProgramData"),
        )
        .unwrap();
        let expected =
            AuthenticatedNativeGenerationSealProgressStore::expected_root_path(&layout, &manifest)
                .unwrap();
        assert_eq!(
            expected,
            layout
                .state_root()
                .join("finalizer-commits")
                .join(hex_lower(&manifest.binding().unwrap().transaction_sha256()))
        );
        assert_eq!(
            AuthenticatedNativeGenerationSealProgressStore::required_root_access(),
            PROGRESS_ROOT_ACCESS
        );
        assert_ne!(PROGRESS_ROOT_ACCESS & FILE_ADD_FILE, 0);
        assert_eq!(PROGRESS_ROOT_ACCESS & WRITE_DAC, 0);
        assert_eq!(PROGRESS_ROOT_ACCESS & DELETE, 0);

        let manifest_sha256 = manifest.digest().unwrap();
        let exact = generation_progress_relative_name(&manifest_sha256, 7);
        let private = generation_progress_publishing_name(&manifest_sha256, 7);
        assert_eq!(
            parse_generation_progress_relative_name(&exact, manifest_sha256).unwrap(),
            Some(7)
        );
        assert_eq!(
            parse_generation_progress_namespace_name(&private, manifest_sha256).unwrap(),
            Some(ProgressNamespaceName::Publishing(7))
        );
        assert_eq!(
            parse_generation_progress_relative_name(
                "05-final-commit.receipt.json",
                manifest_sha256
            )
            .unwrap(),
            None
        );
        for hostile in [
            exact.to_ascii_uppercase(),
            exact.replace(".07.", ".7."),
            generation_progress_relative_name(
                &manifest_sha256,
                GENERATION_SEAL_TERMINAL_SEQUENCE + 1,
            ),
            format!("{private}.tmp"),
        ] {
            assert_eq!(
                parse_generation_progress_namespace_name(&hostile, manifest_sha256)
                    .unwrap_err()
                    .code(),
                "authority_generation_seal_progress_namespace_invalid"
            );
        }
        for wrong_manifest in [
            generation_progress_relative_name(&[99; 32], 7),
            generation_progress_publishing_name(&[99; 32], 7),
        ] {
            assert_eq!(
                parse_generation_progress_namespace_name(&wrong_manifest, manifest_sha256)
                    .unwrap_err()
                    .code(),
                "authority_generation_seal_progress_manifest_namespace_mismatch"
            );
        }
    }

    #[test]
    fn native_progress_envelope_rejects_manifest_sequence_and_schema_drift() {
        let manifest = manifest();
        let closure = writer_closure(&manifest);
        let checkpoint =
            GenerationSealProgressCheckpoint::genesis(&manifest, &closure, checkpoint_writer())
                .unwrap();
        let bytes = checkpoint.canonical_bytes(&manifest, &closure).unwrap();
        let manifest_sha256 = manifest.digest().unwrap();
        assert_eq!(
            progress_sequence_from_envelope(&bytes, manifest_sha256).unwrap(),
            0
        );
        validate_progress_envelope(&bytes, manifest_sha256, 0).unwrap();
        assert!(validate_progress_envelope(&bytes, manifest_sha256, 1).is_err());

        for (field, replacement) in [
            ("schema", serde_json::json!("wrong")),
            ("manifestSha256", serde_json::json!(hex_lower(&[99; 32]))),
            (
                "sequence",
                serde_json::json!(GENERATION_SEAL_TERMINAL_SEQUENCE + 1),
            ),
        ] {
            let mut hostile: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
            hostile[field] = replacement;
            let hostile = serde_json::to_vec(&hostile).unwrap();
            assert!(progress_sequence_from_envelope(&hostile, manifest_sha256).is_err());
        }
    }

    fn genesis_checkpoint_bytes() -> (GenerationSealManifest, Vec<u8>) {
        let manifest = manifest();
        let closure = writer_closure(&manifest);
        let checkpoint =
            GenerationSealProgressCheckpoint::genesis(&manifest, &closure, checkpoint_writer())
                .unwrap();
        let bytes = checkpoint.canonical_bytes(&manifest, &closure).unwrap();
        (manifest, bytes)
    }

    #[test]
    fn progress_crash_cut_create_before_write_deletes_staging_and_retries() {
        let (manifest, _) = genesis_checkpoint_bytes();
        assert_eq!(
            classify_progress_publishing_recovery(
                ProgressSecurityPhase::Staging,
                &[],
                &manifest,
                0,
            )
            .unwrap(),
            ProgressPublishingRecoveryAction::DeleteStagingAndRetry
        );
    }

    #[test]
    fn progress_crash_cut_mid_write_deletes_only_plausibly_torn_staging() {
        let (manifest, bytes) = genesis_checkpoint_bytes();
        let cut = bytes.len() / 2;
        assert_eq!(
            classify_progress_publishing_recovery(
                ProgressSecurityPhase::Staging,
                &bytes[..cut],
                &manifest,
                0,
            )
            .unwrap(),
            ProgressPublishingRecoveryAction::DeleteStagingAndRetry
        );
        assert_eq!(
            classify_progress_publishing_recovery(
                ProgressSecurityPhase::Staging,
                b"unbound-staging-content",
                &manifest,
                0,
            )
            .unwrap_err()
            .code(),
            "authority_generation_seal_progress_publishing_content_unknown"
        );
    }

    #[test]
    fn progress_crash_cut_post_write_preseal_deletes_and_retries_exact_staging() {
        let (manifest, bytes) = genesis_checkpoint_bytes();
        assert_eq!(
            classify_progress_publishing_recovery(
                ProgressSecurityPhase::Staging,
                &bytes,
                &manifest,
                0,
            )
            .unwrap(),
            ProgressPublishingRecoveryAction::DeleteStagingAndRetry
        );
    }

    #[test]
    fn progress_crash_cut_postseal_prerename_rolls_forward_only_exact_bytes() {
        let (manifest, bytes) = genesis_checkpoint_bytes();
        assert_eq!(
            classify_progress_publishing_recovery(
                ProgressSecurityPhase::PrivateSealed,
                &bytes,
                &manifest,
                0,
            )
            .unwrap(),
            ProgressPublishingRecoveryAction::RollForwardSealed
        );
        let mut drifted = bytes.clone();
        drifted.push(b' ');
        assert!(classify_progress_publishing_recovery(
            ProgressSecurityPhase::PrivateSealed,
            &drifted,
            &manifest,
            0,
        )
        .is_err());
    }

    #[test]
    fn progress_crash_cut_after_rename_before_tighten_recovers_only_exact_private_sealed_final() {
        let (manifest, bytes) = genesis_checkpoint_bytes();
        assert_eq!(
            classify_progress_final_recovery(
                ProgressSecurityPhase::PrivateSealed,
                &bytes,
                &manifest,
                0,
            )
            .unwrap(),
            ProgressFinalRecoveryAction::TightenPrivateSealed
        );
        assert_eq!(
            classify_progress_final_recovery(
                ProgressSecurityPhase::PublishedImmutable,
                &bytes,
                &manifest,
                0,
            )
            .unwrap(),
            ProgressFinalRecoveryAction::AlreadyImmutable
        );
        assert_eq!(
            classify_progress_final_recovery(ProgressSecurityPhase::Staging, &bytes, &manifest, 0,)
                .unwrap_err()
                .code(),
            "authority_generation_seal_progress_staging_descriptor_at_final_name"
        );
        let mut drifted = bytes;
        drifted.push(b' ');
        assert!(classify_progress_final_recovery(
            ProgressSecurityPhase::PrivateSealed,
            &drifted,
            &manifest,
            0,
        )
        .is_err());
    }

    #[test]
    fn progress_file_capabilities_keep_delete_and_write_dac_phase_scoped() {
        assert_eq!(PROGRESS_FILE_CREATE_ACCESS & (DELETE | WRITE_DAC), 0);
        assert_eq!(
            PROGRESS_FILE_SEAL_ACCESS & (DELETE | WRITE_DAC | FILE_WRITE_DATA),
            WRITE_DAC
        );
        assert_eq!(
            PROGRESS_FILE_STAGING_RECOVERY_ACCESS & (DELETE | WRITE_DAC | FILE_WRITE_DATA),
            DELETE
        );
        assert_eq!(PROGRESS_FILE_SEALED_RECOVERY_ACCESS & DELETE, DELETE);
        assert_eq!(
            PROGRESS_FILE_SEALED_RECOVERY_ACCESS & (WRITE_DAC | FILE_WRITE_DATA),
            WRITE_DAC
        );
        assert_eq!(
            PROGRESS_FILE_READ_ACCESS & (DELETE | WRITE_DAC | FILE_WRITE_DATA),
            0
        );
        assert_eq!(
            ProgressFileCapabilityKind::PublishingSeal.expected_access(),
            PROGRESS_FILE_SEAL_ACCESS
        );
        assert_eq!(
            ProgressFileCapabilityKind::PublishingStagingRecovery.expected_access(),
            PROGRESS_FILE_STAGING_RECOVERY_ACCESS
        );
        assert_eq!(
            ProgressFileCapabilityKind::PublishingSealedRecovery.expected_access(),
            PROGRESS_FILE_SEALED_RECOVERY_ACCESS
        );
        assert_eq!(
            ProgressFileCapabilityKind::PublishedTightening.expected_access(),
            PROGRESS_FILE_SEALED_RECOVERY_ACCESS
        );
    }

    #[test]
    fn progress_unknown_descriptor_and_collision_content_fail_closed() {
        let staging =
            expected_publication_security_sha256(FinalizerPublicationSecurityPhase::Staging)
                .unwrap();
        let private_sealed =
            expected_publication_security_sha256(FinalizerPublicationSecurityPhase::PrivateSealed)
                .unwrap();
        let published_immutable = expected_publication_security_sha256(
            FinalizerPublicationSecurityPhase::PublishedImmutable,
        )
        .unwrap();
        assert_eq!(
            classify_progress_security_sha256(staging).unwrap(),
            ProgressSecurityPhase::Staging
        );
        assert_eq!(
            classify_progress_security_sha256(private_sealed).unwrap(),
            ProgressSecurityPhase::PrivateSealed
        );
        assert_eq!(
            classify_progress_security_sha256(published_immutable).unwrap(),
            ProgressSecurityPhase::PublishedImmutable
        );
        assert!(classify_progress_security_sha256([99; 32]).is_err());

        let expected = b"exact";
        validate_progress_publish_collision(expected, sha256_bytes(expected), expected).unwrap();
        assert_eq!(
            validate_progress_publish_collision(b"conflict", sha256_bytes(b"conflict"), expected)
                .unwrap_err()
                .code(),
            "authority_generation_seal_progress_publish_collision_conflict"
        );
    }

    #[test]
    fn production_entry_point_remains_fail_closed() {
        assert!(!GENERATION_SEAL_PRODUCTION_ENABLED);
    }
}
