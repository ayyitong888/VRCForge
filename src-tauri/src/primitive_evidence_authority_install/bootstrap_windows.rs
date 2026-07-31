use super::*;
#[cfg(test)]
use crate::primitive_evidence_authority_install::maintenance_journal::{
    MaintenanceJournalTerminalKind, MaintenanceJournalTerminalReceipt,
};
use crate::{
    primitive_evidence_authority_install::finalizer_commit_store_windows::{
        VerifiedPublishedRuntimeBindingProjection, VerifiedPublishedRuntimeLedgerPair,
    },
    primitive_evidence_authority_install::worker_store_windows::{
        create_candidate_consumption_tombstone_for_candidate,
        open_candidate_activation_receipt_binding,
        open_candidate_consumption_tombstone_for_candidate,
        NativeCandidateActivationReceiptBinding, NativeCandidateConsumptionLease,
    },
    primitive_evidence_authority_key::{
        open_verified_machine_key, AuthorityKeyPolicy, OpenedAuthorityKey,
    },
    primitive_evidence_authority_ledger::{
        AuthenticatedPublishedAuthorityLedger, AuthorityLedger, LedgerIdentity,
    },
    primitive_evidence_authority_windows::inspect_installed_authority_for_generation,
};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    ffi::OsString,
    fs::{File, OpenOptions},
    io::{Read, Seek, SeekFrom},
    mem::{size_of, zeroed},
    os::windows::{
        ffi::{OsStrExt, OsStringExt},
        fs::{MetadataExt, OpenOptionsExt},
        io::{AsHandle, AsRawHandle, BorrowedHandle, FromRawHandle, RawHandle},
    },
    path::{Component, Path, PathBuf},
    ptr,
};
use windows_sys::Wdk::Foundation::{NtQueryObject, ObjectBasicInformation};
#[cfg(test)]
use windows_sys::Win32::Security::{GetSecurityDescriptorLength, SACL_SECURITY_INFORMATION};
#[cfg(test)]
use windows_sys::Win32::Storage::FileSystem::FILE_SHARE_DELETE;
use windows_sys::Win32::{
    Foundation::{
        GetHandleInformation, GetLastError, LocalFree, ERROR_INSUFFICIENT_BUFFER,
        ERROR_SERVICE_DOES_NOT_EXIST, HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE,
    },
    Security::{
        Authorization::{
            ConvertSecurityDescriptorToStringSecurityDescriptorW,
            ConvertStringSecurityDescriptorToSecurityDescriptorW, GetSecurityInfo, SDDL_REVISION_1,
            SE_FILE_OBJECT,
        },
        DACL_SECURITY_INFORMATION, GROUP_SECURITY_INFORMATION, LABEL_SECURITY_INFORMATION,
        OWNER_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR,
    },
    Storage::FileSystem::{
        GetDriveTypeW, GetFileInformationByHandle, GetFinalPathNameByHandleW, GetVolumePathNameW,
        ReOpenFile, BY_HANDLE_FILE_INFORMATION, FILE_ATTRIBUTE_DIRECTORY,
        FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
        FILE_FLAG_SEQUENTIAL_SCAN, FILE_READ_ATTRIBUTES, FILE_READ_DATA, FILE_READ_EA,
        FILE_SHARE_READ, FILE_SHARE_WRITE, READ_CONTROL, SYNCHRONIZE,
    },
    System::{
        Services::{
            CloseServiceHandle, OpenSCManagerW, OpenServiceW, QueryServiceObjectSecurity,
            SC_HANDLE, SC_MANAGER_CONNECT, SERVICE_QUERY_CONFIG, SERVICE_QUERY_STATUS,
        },
        Threading::{
            GetCurrentProcess, GetCurrentProcessId, GetProcessTimes, QueryFullProcessImageNameW,
        },
    },
};

const MAX_MANIFEST_BYTES: u64 = 1024 * 1024;
const MAX_LEDGER_ARTIFACT_BYTES: u64 = 512 * 1024 * 1024;
const MAX_RUNNER_POLICY_STATE_BYTES: u64 = 64 * 1024;
const PROTECTED_EXECUTABLE_READ_ACCESS: u32 =
    FILE_READ_DATA | FILE_READ_ATTRIBUTES | FILE_READ_EA | READ_CONTROL | SYNCHRONIZE;
const AUTHENTICATED_RUNTIME_SOURCE_IDENTITY_DOMAIN: &[u8] =
    b"vrcforge-authenticated-runtime-source-identity-v1\0";
const AUTHENTICATED_RUNNER_POLICY_IDENTITY_DOMAIN: &[u8] =
    b"vrcforge-authenticated-runner-policy-identity-v1\0";
const AUTHENTICATED_SERVICE_PATH_READBACK_DOMAIN: &[u8] =
    b"vrcforge-authenticated-service-path-readback-v1\0";
const AUTHENTICATED_SERVICE_FILE_IDENTITY_DOMAIN: &[u8] =
    b"vrcforge-authenticated-service-file-identity-v1\0";
const AUTHENTICATED_KEY_READBACK_DOMAIN: &[u8] = b"vrcforge-authenticated-key-readback-v1\0";
const AUTHENTICATED_SCM_READBACK_DOMAIN: &[u8] = b"vrcforge-authenticated-scm-readback-v1\0";
const SECURITY_INFORMATION: u32 = OWNER_SECURITY_INFORMATION
    | GROUP_SECURITY_INFORMATION
    | DACL_SECURITY_INFORMATION
    | LABEL_SECURITY_INFORMATION;
const READ_CONTROL_ACCESS: u32 = 0x0002_0000;
const DRIVE_FIXED_TYPE: u32 = 3;

pub(super) struct NativeBootstrapSourceCommon {
    held_files: Vec<HeldProtectedFile>,
    runtime_source_manifest: Option<HeldProtectedFile>,
    runner_policy_state: Option<HeldProtectedFile>,
    lifecycle_driver: Option<HeldProtectedFile>,
    bridge_launcher: Option<HeldProtectedFile>,
    opened_keys: Vec<HeldBootstrapKey>,
    service_security: Option<HeldServiceSecurity>,
    layout: Option<AuthorityLayout>,
    generation: Option<[u8; 32]>,
    activation_names: Vec<String>,
    service_image_path: Option<PathBuf>,
    service_executable_path_sha256: Option<[u8; 32]>,
    service_executable_file_identity_sha256: Option<[u8; 32]>,
    scm_readback_sha256: Option<[u8; 32]>,
}

pub(super) struct NativeCommittedRuntimeBootstrapSource {
    common: NativeBootstrapSourceCommon,
    published_runtime_pair: Option<VerifiedPublishedRuntimeLedgerPair>,
    published_runtime_binding: VerifiedPublishedRuntimeBindingProjection,
    published_final_commit_receipt_sha256: [u8; 32],
    authenticated_runtime_ledger: Option<AuthenticatedPublishedAuthorityLedger>,
}

pub(super) struct NativeCandidateValidationBootstrapSource {
    common: NativeBootstrapSourceCommon,
    candidate_credential: Option<HeldCandidateCredential>,
    candidate_consumption: Option<NativeCandidateConsumptionLease>,
    candidate_issuer: Option<activation::CandidateIssuerBinding>,
}

/// Unique held-handle capability for the exact runtime-source manifest opened
/// during authenticated FinalCommit bootstrap. It is deliberately not Clone.
pub(super) struct NativeAuthenticatedRuntimeSourceCapability {
    manifest: HeldProtectedFile,
}

/// Unique held-handle capability for the exact sealed runner-policy file.
/// It is deliberately not Clone and the canonical bytes are consumed once.
pub(super) struct NativeAuthenticatedRunnerPolicyCapability {
    generation: [u8; 32],
    transaction_sha256: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    descriptor: RunnerPolicyStateDescriptor,
    sealed_identity: RunnerPolicySealedIdentity,
    state: HeldProtectedFile,
}

pub(super) struct NativeAuthenticatedRunnerPolicyReadback {
    pub(super) held_file_identity_sha256: [u8; 32],
    pub(super) bytes: Vec<u8>,
}

/// Unique live-file capability for the exact two generation-root executables.
/// Neither this wrapper nor HeldProtectedFile implements Clone.
pub(super) struct NativeAuthenticatedProtectedRootExecutablesCapability {
    generation: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    lifecycle_driver_path: PathBuf,
    bridge_launcher_path: PathBuf,
    lifecycle_driver: HeldProtectedFile,
    bridge_launcher: HeldProtectedFile,
}

/// Two cloned live files in the only supported order: lifecycle driver,
/// followed by bridge launcher. This ownership wrapper is not Clone.
pub(super) struct NativeGenerationBoundProtectedExecutableHandles {
    generation: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    lifecycle_driver_path: PathBuf,
    bridge_launcher_path: PathBuf,
    lifecycle_driver_identity: HeldFileIdentity,
    bridge_launcher_identity: HeldFileIdentity,
    lifecycle_driver_descriptor: AuthorityPayloadDigest,
    bridge_launcher_descriptor: AuthorityPayloadDigest,
    lifecycle_driver: File,
    bridge_launcher: File,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct NativeAuthenticatedControllerSourceReadback {
    pub(super) controller_path: PathBuf,
    pub(super) descriptor: AuthorityPayloadDigest,
    pub(super) volume_serial: u32,
    pub(super) file_id: u64,
    pub(super) link_count: u32,
}

/// Non-clone ownership of the exact controller file object reopened from the
/// authenticated FinalCommit source. The file stays open after the bootstrap
/// boundary is released and is revalidated without resolving the path again.
pub(super) struct NativeAuthenticatedControllerSourceLease {
    readback: NativeAuthenticatedControllerSourceReadback,
    identity: HeldFileIdentity,
    file: File,
    security: NativeAuthenticatedSourceLeaseSecurity,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct NativeAuthenticatedInstallHelperSourceReadback {
    pub(super) install_helper_path: PathBuf,
    pub(super) descriptor: AuthorityPayloadDigest,
    pub(super) volume_serial: u32,
    pub(super) file_id: u64,
    pub(super) link_count: u32,
}

/// Non-clone ownership of the exact install-helper file object reopened from
/// the authenticated FinalCommit source.
pub(super) struct NativeAuthenticatedInstallHelperSourceLease {
    readback: NativeAuthenticatedInstallHelperSourceReadback,
    identity: HeldFileIdentity,
    file: File,
    security: NativeAuthenticatedSourceLeaseSecurity,
}

enum NativeAuthenticatedSourceLeaseSecurity {
    SealedBinary,
    #[cfg(test)]
    TestOnlyUnverified,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct NativeAuthenticatedGenerationBindingReadback {
    pub(super) service_executable_path_sha256: [u8; 32],
    pub(super) service_executable_file_identity_sha256: [u8; 32],
    pub(super) protected_key_readback_sha256: [u8; 32],
    pub(super) scm_readback_sha256: [u8; 32],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum NativeServiceConfigurationPhase {
    CandidateValidation,
    CommittedRuntime,
}

impl NativeBootstrapSourceCommon {
    fn new() -> Self {
        Self {
            held_files: Vec::new(),
            runtime_source_manifest: None,
            runner_policy_state: None,
            lifecycle_driver: None,
            bridge_launcher: None,
            opened_keys: Vec::new(),
            service_security: None,
            layout: None,
            generation: None,
            activation_names: Vec::new(),
            service_image_path: None,
            service_executable_path_sha256: None,
            service_executable_file_identity_sha256: None,
            scm_readback_sha256: None,
        }
    }

    fn hold(&mut self, file: HeldProtectedFile) {
        self.held_files.push(file);
    }

    fn hold_runtime_source_manifest(
        &mut self,
        file: HeldProtectedFile,
    ) -> Result<(), AuthorityBootstrapError> {
        if self.runtime_source_manifest.is_some() {
            return Err(AuthorityBootstrapError(
                "authority_runtime_source_capability_duplicate",
            ));
        }
        self.runtime_source_manifest = Some(file);
        Ok(())
    }

    fn hold_runner_policy_state(
        &mut self,
        file: HeldProtectedFile,
    ) -> Result<(), AuthorityBootstrapError> {
        if self.runner_policy_state.is_some() {
            return Err(AuthorityBootstrapError(
                "authority_runner_policy_capability_duplicate",
            ));
        }
        self.runner_policy_state = Some(file);
        Ok(())
    }

    fn hold_root_executables(
        &mut self,
        lifecycle_driver: HeldProtectedFile,
        bridge_launcher: HeldProtectedFile,
    ) -> Result<(), AuthorityBootstrapError> {
        if self.lifecycle_driver.is_some() || self.bridge_launcher.is_some() {
            return Err(AuthorityBootstrapError(
                "authority_root_executable_capability_duplicate",
            ));
        }
        self.lifecycle_driver = Some(lifecycle_driver);
        self.bridge_launcher = Some(bridge_launcher);
        Ok(())
    }

    fn reset_held_state(&mut self) {
        self.held_files.clear();
        self.runtime_source_manifest = None;
        self.runner_policy_state = None;
        self.lifecycle_driver = None;
        self.bridge_launcher = None;
        self.opened_keys.clear();
        self.service_security = None;
        self.layout = None;
        self.generation = None;
        self.activation_names.clear();
        self.service_image_path = None;
        self.service_executable_path_sha256 = None;
        self.service_executable_file_identity_sha256 = None;
        self.scm_readback_sha256 = None;
    }
}

impl std::ops::Deref for NativeCommittedRuntimeBootstrapSource {
    type Target = NativeBootstrapSourceCommon;

    fn deref(&self) -> &Self::Target {
        &self.common
    }
}

impl std::ops::DerefMut for NativeCommittedRuntimeBootstrapSource {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.common
    }
}

impl std::ops::Deref for NativeCandidateValidationBootstrapSource {
    type Target = NativeBootstrapSourceCommon;

    fn deref(&self) -> &Self::Target {
        &self.common
    }
}

impl std::ops::DerefMut for NativeCandidateValidationBootstrapSource {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.common
    }
}

impl NativeCandidateValidationBootstrapSource {
    pub(super) fn new() -> Self {
        Self {
            common: NativeBootstrapSourceCommon::new(),
            candidate_credential: None,
            candidate_consumption: None,
            candidate_issuer: None,
        }
    }

    fn reset_held_state(&mut self) {
        self.common.reset_held_state();
        self.candidate_credential = None;
        self.candidate_consumption = None;
        self.candidate_issuer = None;
    }
}

impl NativeCommittedRuntimeBootstrapSource {
    pub(super) fn new_authenticated_final_commit(
        pair: VerifiedPublishedRuntimeLedgerPair,
    ) -> Result<Self, AuthorityBootstrapError> {
        pair.revalidate().map_err(|_| {
            AuthorityBootstrapError("authority_final_commit_ledger_pair_not_verified")
        })?;
        let binding = pair
            .binding_projection()
            .map_err(|_| AuthorityBootstrapError("authority_final_commit_binding_not_verified"))?;
        let final_commit_receipt_sha256 = pair
            .final_commit_receipt_sha256()
            .map_err(|_| AuthorityBootstrapError("authority_final_commit_receipt_not_verified"))?;
        Ok(Self {
            common: NativeBootstrapSourceCommon::new(),
            published_runtime_pair: Some(pair),
            published_runtime_binding: binding,
            published_final_commit_receipt_sha256: final_commit_receipt_sha256,
            authenticated_runtime_ledger: None,
        })
    }

    pub(super) fn published_runtime_binding(&self) -> VerifiedPublishedRuntimeBindingProjection {
        self.published_runtime_binding
    }

    pub(super) fn published_final_commit_receipt_sha256(&self) -> [u8; 32] {
        self.published_final_commit_receipt_sha256
    }

    pub(super) fn take_authenticated_runtime_source(
        &mut self,
        expected: AuthorityPayloadDigest,
    ) -> Result<NativeAuthenticatedRuntimeSourceCapability, AuthorityBootstrapError> {
        let mut manifest = self
            .runtime_source_manifest
            .take()
            .ok_or(AuthorityBootstrapError(
                "authority_runtime_source_capability_missing",
            ))?;
        manifest.verify_exact_descriptor(expected)?;
        Ok(NativeAuthenticatedRuntimeSourceCapability { manifest })
    }

    pub(super) fn take_authenticated_runner_policy(
        &mut self,
        expected_generation: [u8; 32],
        expected_transaction_sha256: [u8; 32],
        expected: RunnerPolicyStateDescriptor,
        expected_sealed_identity: RunnerPolicySealedIdentity,
    ) -> Result<NativeAuthenticatedRunnerPolicyCapability, AuthorityBootstrapError> {
        let binding = self.published_runtime_binding;
        let final_commit_receipt_sha256 = self.published_final_commit_receipt_sha256;
        if self.generation != Some(expected_generation)
            || binding.generation_sha256() != expected_generation
            || binding.transaction_sha256() != expected_transaction_sha256
            || expected.generation_sha256() != expected_generation
            || expected.transaction_sha256() != expected_transaction_sha256
            || binding.expected_runner_policy_state_byte_length() != expected.byte_length()
            || binding.expected_runner_policy_state_bytes_sha256() != expected.bytes_sha256()
            || binding.expected_runner_policy_state_binding_sha256() != expected.binding_sha256()
            || binding.runner_policy_sealed_volume_serial()
                != expected_sealed_identity.volume_serial()
            || binding.runner_policy_sealed_file_id() != expected_sealed_identity.file_id()
            || binding.runner_policy_sealed_link_count() != expected_sealed_identity.link_count()
            || binding.runner_policy_sealed_attributes() != expected_sealed_identity.attributes()
            || expected_sealed_identity.validate().is_err()
            || final_commit_receipt_sha256.iter().all(|value| *value == 0)
        {
            return Err(AuthorityBootstrapError(
                "authority_runner_policy_final_commit_binding_mismatch",
            ));
        }
        let mut state = self
            .runner_policy_state
            .take()
            .ok_or(AuthorityBootstrapError(
                "authority_runner_policy_capability_missing",
            ))?;
        verify_runner_policy_held_file(&mut state, expected, expected_sealed_identity)?;
        Ok(NativeAuthenticatedRunnerPolicyCapability {
            generation: expected_generation,
            transaction_sha256: expected_transaction_sha256,
            final_commit_receipt_sha256,
            descriptor: expected,
            sealed_identity: expected_sealed_identity,
            state,
        })
    }

    pub(super) fn take_authenticated_root_executables(
        &mut self,
        expected_generation: [u8; 32],
        expected_lifecycle_driver: AuthorityPayloadDigest,
        expected_bridge_launcher: AuthorityPayloadDigest,
    ) -> Result<NativeAuthenticatedProtectedRootExecutablesCapability, AuthorityBootstrapError>
    {
        if self.generation != Some(expected_generation) {
            return Err(AuthorityBootstrapError(
                "authority_root_executable_capability_wrong_lane",
            ));
        }
        let final_commit_receipt_sha256 = self.published_final_commit_receipt_sha256;
        if final_commit_receipt_sha256.iter().all(|value| *value == 0) {
            return Err(AuthorityBootstrapError(
                "authority_root_executable_receipt_not_verified",
            ));
        }
        let mut lifecycle_driver = self.lifecycle_driver.take().ok_or(AuthorityBootstrapError(
            "authority_lifecycle_driver_capability_missing",
        ))?;
        let mut bridge_launcher = self.bridge_launcher.take().ok_or(AuthorityBootstrapError(
            "authority_bridge_launcher_capability_missing",
        ))?;
        let layout = self.layout.as_ref().ok_or(AuthorityBootstrapError(
            "authority_root_executable_capability_missing",
        ))?;
        let lifecycle_driver_path = layout
            .lifecycle_driver_executable_for_generation(&expected_generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let bridge_launcher_path = layout
            .bridge_launcher_executable_for_generation(&expected_generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        if !paths_equal(&lifecycle_driver.path, &lifecycle_driver_path)
            || !paths_equal(&bridge_launcher.path, &bridge_launcher_path)
        {
            return Err(AuthorityBootstrapError(
                "authority_root_executable_capability_binding_mismatch",
            ));
        }
        lifecycle_driver.verify_exact_descriptor(expected_lifecycle_driver)?;
        bridge_launcher.verify_exact_descriptor(expected_bridge_launcher)?;
        require_exact_read_only_handle(&lifecycle_driver.file)?;
        require_exact_read_only_handle(&bridge_launcher.file)?;
        Ok(NativeAuthenticatedProtectedRootExecutablesCapability {
            generation: expected_generation,
            final_commit_receipt_sha256,
            lifecycle_driver_path,
            bridge_launcher_path,
            lifecycle_driver,
            bridge_launcher,
        })
    }

    pub(super) fn current_controller_source_readback(
        &mut self,
        expected_generation: [u8; 32],
        expected: AuthorityPayloadDigest,
    ) -> Result<NativeAuthenticatedControllerSourceLease, AuthorityBootstrapError> {
        if self.generation != Some(expected_generation) {
            return Err(AuthorityBootstrapError(
                "authority_controller_source_capability_wrong_lane",
            ));
        }
        let layout = self.layout.as_ref().ok_or(AuthorityBootstrapError(
            "authority_controller_source_capability_missing",
        ))?;
        let expected_path = layout
            .controller_executable_for_generation(&expected_generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let matches = self
            .held_files
            .iter()
            .enumerate()
            .filter(|(_, file)| {
                file.descriptor == expected && paths_equal(&file.path, &expected_path)
            })
            .map(|(index, _)| index)
            .collect::<Vec<_>>();
        let [index] = matches.as_slice() else {
            return Err(AuthorityBootstrapError(
                "authority_controller_source_capability_missing",
            ));
        };
        let file = &mut self.held_files[*index];
        file.verify_exact_descriptor(expected)?;
        require_exact_handle_path(&file.file, &expected_path)?;
        let identity = held_file_identity(&file.file)?;
        if identity != file.identity
            || identity.byte_length != expected.byte_length()
            || identity.link_count != 1
            || identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
        {
            return Err(AuthorityBootstrapError(
                "authority_controller_source_capability_binding_mismatch",
            ));
        }
        let readback = NativeAuthenticatedControllerSourceReadback {
            controller_path: expected_path,
            descriptor: expected,
            volume_serial: identity.volume_serial,
            file_id: identity.file_id,
            link_count: identity.link_count,
        };
        let lease_file = reopen_file_object_read_only(&file.file)?;
        let lease = NativeAuthenticatedControllerSourceLease {
            readback,
            identity,
            file: lease_file,
            security: NativeAuthenticatedSourceLeaseSecurity::SealedBinary,
        };
        lease.verify()?;
        Ok(lease)
    }

    pub(super) fn current_install_helper_source_readback(
        &mut self,
        expected_generation: [u8; 32],
        expected: AuthorityPayloadDigest,
    ) -> Result<NativeAuthenticatedInstallHelperSourceLease, AuthorityBootstrapError> {
        if self.generation != Some(expected_generation) {
            return Err(AuthorityBootstrapError(
                "authority_install_helper_source_capability_wrong_lane",
            ));
        }
        let layout = self.layout.as_ref().ok_or(AuthorityBootstrapError(
            "authority_install_helper_source_capability_missing",
        ))?;
        let expected_path = layout
            .install_helper_executable_for_generation(&expected_generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let matches = self
            .held_files
            .iter()
            .enumerate()
            .filter(|(_, file)| {
                file.descriptor == expected && paths_equal(&file.path, &expected_path)
            })
            .map(|(index, _)| index)
            .collect::<Vec<_>>();
        let [index] = matches.as_slice() else {
            return Err(AuthorityBootstrapError(
                "authority_install_helper_source_capability_missing",
            ));
        };
        let file = &mut self.held_files[*index];
        file.verify_exact_descriptor(expected)?;
        require_exact_handle_path(&file.file, &expected_path)?;
        let identity = held_file_identity(&file.file)?;
        if identity != file.identity
            || identity.byte_length != expected.byte_length()
            || identity.link_count != 1
            || identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
        {
            return Err(AuthorityBootstrapError(
                "authority_install_helper_source_capability_binding_mismatch",
            ));
        }
        let readback = NativeAuthenticatedInstallHelperSourceReadback {
            install_helper_path: expected_path,
            descriptor: expected,
            volume_serial: identity.volume_serial,
            file_id: identity.file_id,
            link_count: identity.link_count,
        };
        let lease_file = reopen_file_object_read_only(&file.file)?;
        let lease = NativeAuthenticatedInstallHelperSourceLease {
            readback,
            identity,
            file: lease_file,
            security: NativeAuthenticatedSourceLeaseSecurity::SealedBinary,
        };
        lease.verify()?;
        Ok(lease)
    }

    pub(super) fn take_authenticated_runtime_ledger(
        &mut self,
    ) -> Result<AuthenticatedPublishedAuthorityLedger, AuthorityBootstrapError> {
        self.authenticated_runtime_ledger
            .take()
            .ok_or(AuthorityBootstrapError(
                "authority_final_commit_ledger_not_adopted",
            ))
    }
}

impl NativeBootstrapSourceCommon {
    fn exact_current_key(
        &self,
        generation: [u8; 32],
        signer_key_id: [u8; 32],
    ) -> Result<&HeldBootstrapKey, AuthorityBootstrapError> {
        let index = exact_current_key_position(
            self.opened_keys.len(),
            |index| {
                let binding = &self.opened_keys[index];
                (binding.generation, *binding.readback.signer_key_id())
            },
            generation,
            signer_key_id,
        )?;
        Ok(&self.opened_keys[index])
    }

    pub(super) fn current_generation_binding_readback(
        &self,
        generation: [u8; 32],
        signer_key_id: [u8; 32],
    ) -> Result<NativeAuthenticatedGenerationBindingReadback, AuthorityBootstrapError> {
        let key = self.exact_current_key(generation, signer_key_id)?;
        key.opened
            .verify_current(&key.policy)
            .map_err(|_| AuthorityBootstrapError("authority_current_key_readback_changed"))?;
        Ok(NativeAuthenticatedGenerationBindingReadback {
            service_executable_path_sha256: self.service_executable_path_sha256.ok_or(
                AuthorityBootstrapError("authority_service_path_readback_missing"),
            )?,
            service_executable_file_identity_sha256: self
                .service_executable_file_identity_sha256
                .ok_or(AuthorityBootstrapError(
                "authority_service_file_identity_readback_missing",
            ))?,
            protected_key_readback_sha256: protected_key_readback_sha256(&key.readback)?,
            scm_readback_sha256: self
                .scm_readback_sha256
                .ok_or(AuthorityBootstrapError("authority_scm_readback_missing"))?,
        })
    }

    pub(super) fn sign_current_digest(
        &self,
        generation: [u8; 32],
        signer_key_id: [u8; 32],
        digest: &[u8; 32],
    ) -> Result<[u8; 64], AuthorityBootstrapError> {
        let binding = self.exact_current_key(generation, signer_key_id)?;
        binding
            .opened
            .verify_current(&binding.policy)
            .map_err(|_| AuthorityBootstrapError("authority_current_key_readback_changed"))?;
        binding
            .opened
            .sign_digest(digest)
            .map_err(|_| AuthorityBootstrapError("authority_current_key_sign_failed"))
    }

    pub(super) fn verify_current_digest_signature(
        &self,
        generation: [u8; 32],
        signer_key_id: [u8; 32],
        digest: &[u8; 32],
        signature: &[u8; 64],
    ) -> Result<(), AuthorityBootstrapError> {
        let binding = self.exact_current_key(generation, signer_key_id)?;
        binding
            .opened
            .verify_current(&binding.policy)
            .map_err(|_| AuthorityBootstrapError("authority_current_key_readback_changed"))?;
        binding
            .opened
            .verify_digest_signature(digest, signature)
            .map_err(|_| AuthorityBootstrapError("authority_current_key_signature_invalid"))
    }

    fn open_key(
        &mut self,
        generation: [u8; 32],
        signer_key_id: [u8; 32],
    ) -> Result<VerifiedAuthorityKeyReadback, AuthorityBootstrapError> {
        if self
            .opened_keys
            .iter()
            .any(|binding| binding.generation == generation)
        {
            return Err(AuthorityBootstrapError(
                "authority_manifest_generation_duplicate",
            ));
        }
        let policy = AuthorityKeyPolicy::new(generation, signer_key_id, SERVICE_SID)
            .map_err(|_| AuthorityBootstrapError("authority_key_policy_invalid"))?;
        let opened =
            open_verified_machine_key(&policy).map_err(|error| map_key_error(error.code()))?;
        let readback = opened.readback().clone();
        self.opened_keys.push(HeldBootstrapKey {
            generation,
            policy,
            readback: readback.clone(),
            opened,
        });
        Ok(readback)
    }
}

fn exact_current_key_position<F>(
    binding_count: usize,
    mut binding_at: F,
    generation: [u8; 32],
    signer_key_id: [u8; 32],
) -> Result<usize, AuthorityBootstrapError>
where
    F: FnMut(usize) -> ([u8; 32], [u8; 32]),
{
    let mut generation_seen = false;
    let mut exact = None;
    for index in 0..binding_count {
        let (candidate_generation, candidate_signer_key_id) = binding_at(index);
        if candidate_generation != generation {
            continue;
        }
        generation_seen = true;
        if candidate_signer_key_id != signer_key_id {
            continue;
        }
        if exact.is_some() {
            return Err(AuthorityBootstrapError(
                "authority_current_key_binding_ambiguous",
            ));
        }
        exact = Some(index);
    }
    exact.ok_or_else(|| {
        if generation_seen {
            AuthorityBootstrapError("authority_current_key_signer_mismatch")
        } else {
            AuthorityBootstrapError("authority_current_key_generation_mismatch")
        }
    })
}

fn authenticated_service_path_sha256(path: &Path) -> Result<[u8; 32], AuthorityBootstrapError> {
    if !path.is_absolute() || !path_is_fixed_local(path) {
        return Err(AuthorityBootstrapError(
            "authority_service_path_readback_invalid",
        ));
    }
    let words = path.as_os_str().encode_wide().collect::<Vec<_>>();
    if words.is_empty() {
        return Err(AuthorityBootstrapError(
            "authority_service_path_readback_invalid",
        ));
    }
    let mut digest = Sha256::new();
    digest.update(AUTHENTICATED_SERVICE_PATH_READBACK_DOMAIN);
    digest.update((words.len() as u64).to_be_bytes());
    for word in words {
        digest.update(word.to_le_bytes());
    }
    Ok(digest.finalize().into())
}

fn authenticated_service_file_identity_sha256(
    identity: HeldFileIdentity,
    descriptor: AuthorityPayloadDigest,
) -> Result<[u8; 32], AuthorityBootstrapError> {
    if identity.volume_serial == 0
        || identity.file_id == 0
        || identity.byte_length != descriptor.byte_length()
        || identity.link_count != 1
        || identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
    {
        return Err(AuthorityBootstrapError(
            "authority_service_file_identity_readback_invalid",
        ));
    }
    let mut digest = Sha256::new();
    digest.update(AUTHENTICATED_SERVICE_FILE_IDENTITY_DOMAIN);
    digest.update(identity.volume_serial.to_be_bytes());
    digest.update(identity.file_id.to_be_bytes());
    digest.update(identity.byte_length.to_be_bytes());
    digest.update(identity.link_count.to_be_bytes());
    digest.update(identity.attributes.to_be_bytes());
    digest.update(descriptor.sha256());
    Ok(digest.finalize().into())
}

fn protected_key_readback_sha256(
    readback: &VerifiedAuthorityKeyReadback,
) -> Result<[u8; 32], AuthorityBootstrapError> {
    let key_name = readback.key_name().as_bytes();
    if key_name.is_empty()
        || readback.signer_key_id().iter().all(|value| *value == 0)
        || readback.public_key_sec1()[0] != 0x04
    {
        return Err(AuthorityBootstrapError(
            "authority_current_key_readback_invalid",
        ));
    }
    let mut digest = Sha256::new();
    digest.update(AUTHENTICATED_KEY_READBACK_DOMAIN);
    digest.update((key_name.len() as u64).to_be_bytes());
    digest.update(key_name);
    digest.update(readback.signer_key_id());
    digest.update(readback.public_key_sec1());
    Ok(digest.finalize().into())
}

struct HeldBootstrapKey {
    generation: [u8; 32],
    policy: AuthorityKeyPolicy,
    readback: VerifiedAuthorityKeyReadback,
    opened: OpenedAuthorityKey,
}

fn verify_bootstrap_signature(
    common: &NativeBootstrapSourceCommon,
    generation: &[u8; 32],
    input: &ProtectedManifestSignatureInput,
) -> Result<(), AuthorityBootstrapError> {
    let binding = common
        .opened_keys
        .iter()
        .find(|binding| {
            &binding.generation == generation
                && binding.readback.signer_key_id() == &input.signer_key_id
        })
        .ok_or(AuthorityBootstrapError(
            "authority_manifest_signer_key_mismatch",
        ))?;
    binding
        .opened
        .verify_digest_signature(&input.digest, &input.signature_p1363)
        .map_err(|_| AuthorityBootstrapError("authority_manifest_signature_invalid"))
}

impl BootstrapSignatureVerifier for NativeCommittedRuntimeBootstrapSource {
    fn verify(
        &mut self,
        generation: &[u8; 32],
        input: &ProtectedManifestSignatureInput,
    ) -> Result<(), AuthorityBootstrapError> {
        verify_bootstrap_signature(&self.common, generation, input)
    }
}

impl BootstrapSignatureVerifier for NativeCandidateValidationBootstrapSource {
    fn verify(
        &mut self,
        generation: &[u8; 32],
        input: &ProtectedManifestSignatureInput,
    ) -> Result<(), AuthorityBootstrapError> {
        verify_bootstrap_signature(&self.common, generation, input)
    }
}

fn verify_common_still_stable(
    common: &mut NativeBootstrapSourceCommon,
    phase: NativeServiceConfigurationPhase,
) -> Result<(), AuthorityBootstrapError> {
    for file in &mut common.held_files {
        file.verify()?;
    }
    if let Some(runtime_source_manifest) = &mut common.runtime_source_manifest {
        runtime_source_manifest.verify()?;
    }
    if let Some(runner_policy_state) = &mut common.runner_policy_state {
        runner_policy_state.verify()?;
    }
    if let Some(lifecycle_driver) = &mut common.lifecycle_driver {
        lifecycle_driver.verify()?;
    }
    if let Some(bridge_launcher) = &mut common.bridge_launcher {
        bridge_launcher.verify()?;
    }
    let layout = common
        .layout
        .as_ref()
        .ok_or(AuthorityBootstrapError("authority_layout_unavailable"))?;
    let generation = common.generation.ok_or(AuthorityBootstrapError(
        "authority_active_head_not_verified",
    ))?;
    if activation_directory_names(&layout.activations_root())? != common.activation_names {
        return Err(AuthorityBootstrapError(
            "authority_activation_directory_not_unique",
        ));
    }
    let scm_readback_sha256 = verify_service_configuration(layout, &generation, phase)?;
    if common.scm_readback_sha256 != Some(scm_readback_sha256) {
        return Err(AuthorityBootstrapError("authority_scm_readback_changed"));
    }
    common
        .service_security
        .as_ref()
        .ok_or(AuthorityBootstrapError(
            "authority_service_security_not_verified",
        ))?
        .verify()?;
    if !paths_equal(
        &current_process_image_path()?,
        common
            .service_image_path
            .as_ref()
            .ok_or(AuthorityBootstrapError(
                "authority_service_image_binding_mismatch",
            ))?,
    ) {
        return Err(AuthorityBootstrapError(
            "authority_service_image_binding_mismatch",
        ));
    }
    if common.opened_keys.is_empty() {
        return Err(AuthorityBootstrapError("authority_key_missing"));
    }
    for binding in &common.opened_keys {
        binding
            .opened
            .verify_current(&binding.policy)
            .map_err(|_| AuthorityBootstrapError("authority_key_readback_changed"))?;
        if binding.opened.readback() != &binding.readback {
            return Err(AuthorityBootstrapError("authority_key_readback_changed"));
        }
    }
    Ok(())
}

impl InstalledServiceBootstrapSource for NativeCommittedRuntimeBootstrapSource {
    fn load_snapshot(
        &mut self,
    ) -> Result<(AuthorityLayout, AuthorityBootstrapSnapshot), AuthorityBootstrapError> {
        if self.authenticated_runtime_ledger.is_some() || self.published_runtime_pair.is_none() {
            return Err(AuthorityBootstrapError(
                "authority_final_commit_bootstrap_state_invalid",
            ));
        }
        self.reset_held_state();

        let layout = AuthorityLayout::installed()
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let active_path = layout.active_head_path();
        let active_head = open_protected_file(
            layout.state_anchor(),
            &active_path,
            STATE_DIRECTORY_SDDL,
            SEALED_GENERATION_DIRECTORY_SDDL,
            IMMUTABLE_STATE_FILE_SDDL,
            16 * 1024,
            true,
        )
        .map_err(|error| {
            if !active_path.exists() {
                AuthorityBootstrapError("authority_installation_missing")
            } else {
                error
            }
        })?;
        let active_head_bytes = active_head
            .bytes()
            .ok_or(AuthorityBootstrapError("authority_active_head_read_failed"))?
            .to_vec();
        let head = ProtectedActiveHead::parse_canonical(&active_head_bytes)
            .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?;
        let generation = head
            .generation()
            .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?;
        self.hold(active_head);

        let activation_names = activation_directory_names(&layout.activations_root())?;

        let trust_path = layout
            .trust_manifest_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let trust = open_protected_file(
            layout.state_anchor(),
            &trust_path,
            STATE_DIRECTORY_SDDL,
            SEALED_GENERATION_DIRECTORY_SDDL,
            IMMUTABLE_STATE_FILE_SDDL,
            MAX_MANIFEST_BYTES,
            true,
        )?;
        let trust_manifest_bytes = trust
            .bytes()
            .ok_or(AuthorityBootstrapError(
                "authority_trust_manifest_not_verified",
            ))?
            .to_vec();
        let parsed_trust = ProtectedDetachedManifestFile::parse_canonical(&trust_manifest_bytes)
            .map_err(|_| AuthorityBootstrapError("authority_trust_manifest_not_verified"))?;
        let trust_signature = parsed_trust
            .signature_input()
            .map_err(|_| AuthorityBootstrapError("authority_trust_manifest_not_verified"))?;
        self.hold(trust);

        let activation_path = layout
            .activation_manifest_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let activation = open_protected_file(
            layout.state_anchor(),
            &activation_path,
            STATE_DIRECTORY_SDDL,
            SEALED_GENERATION_DIRECTORY_SDDL,
            IMMUTABLE_STATE_FILE_SDDL,
            MAX_MANIFEST_BYTES,
            true,
        )?;
        let activation_manifest_bytes = activation
            .bytes()
            .ok_or(AuthorityBootstrapError(
                "authority_activation_manifest_not_verified",
            ))?
            .to_vec();
        let parsed_activation = ProtectedDetachedManifestFile::parse_canonical(
            &activation_manifest_bytes,
        )
        .map_err(|_| AuthorityBootstrapError("authority_activation_manifest_not_verified"))?;
        self.hold(activation);

        let service_path = layout
            .service_executable_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let service = open_protected_file(
            layout.binary_anchor(),
            &service_path,
            BINARY_DIRECTORY_SDDL,
            SEALED_GENERATION_DIRECTORY_SDDL,
            SEALED_BINARY_FILE_SDDL,
            MAX_AUTHORITY_BINARY_BYTES,
            false,
        )?;
        let service_descriptor = service.descriptor();
        let service_identity = service.identity;
        let service_executable_path_sha256 = authenticated_service_path_sha256(&service_path)?;
        let service_executable_file_identity_sha256 =
            authenticated_service_file_identity_sha256(service_identity, service_descriptor)?;

        let controller_path = layout
            .controller_executable_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let controller = open_protected_file(
            layout.binary_anchor(),
            &controller_path,
            BINARY_DIRECTORY_SDDL,
            SEALED_GENERATION_DIRECTORY_SDDL,
            SEALED_BINARY_FILE_SDDL,
            MAX_AUTHORITY_BINARY_BYTES,
            false,
        )?;
        let controller_descriptor = controller.descriptor();

        let install_helper_path = layout
            .install_helper_executable_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let install_helper = open_protected_file(
            layout.binary_anchor(),
            &install_helper_path,
            BINARY_DIRECTORY_SDDL,
            SEALED_GENERATION_DIRECTORY_SDDL,
            SEALED_BINARY_FILE_SDDL,
            MAX_AUTHORITY_BINARY_BYTES,
            false,
        )?;
        let install_helper_descriptor = install_helper.descriptor();
        let lifecycle_driver_path = layout
            .lifecycle_driver_executable_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let lifecycle_driver = open_protected_file(
            layout.binary_anchor(),
            &lifecycle_driver_path,
            BINARY_DIRECTORY_SDDL,
            SEALED_GENERATION_DIRECTORY_SDDL,
            SEALED_BINARY_FILE_SDDL,
            MAX_AUTHORITY_BINARY_BYTES,
            false,
        )?;
        let lifecycle_driver_descriptor = lifecycle_driver.descriptor();
        let bridge_launcher_path = layout
            .bridge_launcher_executable_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let bridge_launcher = open_protected_file(
            layout.binary_anchor(),
            &bridge_launcher_path,
            BINARY_DIRECTORY_SDDL,
            SEALED_GENERATION_DIRECTORY_SDDL,
            SEALED_BINARY_FILE_SDDL,
            MAX_AUTHORITY_BINARY_BYTES,
            false,
        )?;
        let bridge_launcher_descriptor = bridge_launcher.descriptor();
        let runtime_source_manifest_path = layout
            .runtime_source_manifest_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let runtime_source_manifest = open_protected_file(
            layout.state_anchor(),
            &runtime_source_manifest_path,
            STATE_DIRECTORY_SDDL,
            SEALED_GENERATION_DIRECTORY_SDDL,
            IMMUTABLE_STATE_FILE_SDDL,
            MAX_RUNTIME_SOURCE_MANIFEST_BYTES,
            false,
        )?;
        let runtime_source_manifest_descriptor = runtime_source_manifest.descriptor();
        let runner_policy_state_path = layout
            .runner_policy_state_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let runner_policy_state = open_protected_file(
            layout.state_anchor(),
            &runner_policy_state_path,
            STATE_DIRECTORY_SDDL,
            SEALED_GENERATION_DIRECTORY_SDDL,
            IMMUTABLE_STATE_FILE_SDDL,
            MAX_RUNNER_POLICY_STATE_BYTES,
            true,
        )
        .map_err(|_| AuthorityBootstrapError("authority_runner_policy_state_not_verified"))?;
        let runner_policy =
            CanonicalRunnerPolicyState::parse_canonical(runner_policy_state.bytes().ok_or(
                AuthorityBootstrapError("authority_runner_policy_state_read_failed"),
            )?)
            .map_err(|_| AuthorityBootstrapError("authority_runner_policy_state_not_verified"))?;
        let runner_policy_descriptor = runner_policy
            .descriptor()
            .map_err(|_| AuthorityBootstrapError("authority_runner_policy_descriptor_invalid"))?;
        let runner_policy_sealed_identity =
            runner_policy_sealed_identity_from_held(runner_policy_state.identity)?;
        let head_transaction_sha256 = head
            .transaction_sha256()
            .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?;
        if runner_policy_descriptor.generation_sha256() != generation
            || runner_policy_descriptor.transaction_sha256() != head_transaction_sha256
        {
            return Err(AuthorityBootstrapError(
                "authority_runner_policy_generation_transaction_mismatch",
            ));
        }
        let installed_content = AuthorityInstallContent::new(
            service_descriptor,
            controller_descriptor,
            install_helper_descriptor,
            lifecycle_driver_descriptor,
            bridge_launcher_descriptor,
            runtime_source_manifest_descriptor,
        )
        .map_err(|_| AuthorityBootstrapError("authority_installed_content_invalid"))?;
        self.hold(service);
        self.hold(controller);
        self.hold(install_helper);
        self.hold_root_executables(lifecycle_driver, bridge_launcher)?;
        self.hold_runtime_source_manifest(runtime_source_manifest)?;
        self.hold_runner_policy_state(runner_policy_state)?;

        let current_image_path = current_process_image_path()?;
        if !paths_equal(&current_image_path, &service_path) {
            return Err(AuthorityBootstrapError(
                "authority_service_image_binding_mismatch",
            ));
        }
        let scm_readback_sha256 = verify_service_configuration(
            &layout,
            &generation,
            NativeServiceConfigurationPhase::CommittedRuntime,
        )?;
        let service_security = HeldServiceSecurity::open()?;
        service_security.verify()?;

        let key_readback = self.open_key(generation, trust_signature.signer_key_id)?;
        let activation_history = load_historical_activation_chain(
            &mut self.common,
            &layout,
            &parsed_activation,
            head.activation_epoch(),
        )?;

        let ledger_identity = derive_ledger_identity(&generation, key_readback.signer_key_id())
            .map_err(|_| AuthorityBootstrapError("authority_ledger_identity_invalid"))?;
        let native_ledger_identity = LedgerIdentity::from_hex(
            &hex_lower(&generation),
            &hex_lower(key_readback.signer_key_id()),
        )
        .map_err(|_| AuthorityBootstrapError("authority_ledger_identity_invalid"))?;
        let pair = self
            .published_runtime_pair
            .take()
            .ok_or(AuthorityBootstrapError(
                "authority_final_commit_ledger_pair_missing",
            ))?;
        if pair.generation_sha256() != generation {
            return Err(AuthorityBootstrapError(
                "authority_final_commit_generation_mismatch",
            ));
        }
        let mut ledger =
            AuthorityLedger::adopt_verified_published_pair(pair, native_ledger_identity)
                .map_err(|error| map_ledger_error(error.code()))?;
        let ledger_readback = ledger
            .authenticated_pair_readback()
            .map_err(|error| map_ledger_error(error.code()))?;
        self.authenticated_runtime_ledger = Some(ledger);

        let preview = preview_install(&layout, installed_content.clone())
            .map_err(|_| AuthorityBootstrapError("authority_generation_recompute_failed"))?;
        if preview
            .generation_sha256()
            .map_err(|_| AuthorityBootstrapError("authority_generation_recompute_failed"))?
            != generation
        {
            return Err(AuthorityBootstrapError(
                "authority_active_head_plan_binding_mismatch",
            ));
        }
        let plan_sha256 = head
            .plan_sha256()
            .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?;
        let transaction_sha256 = head
            .transaction_sha256()
            .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?;
        if head.activation_epoch() == 1
            && (preview
                .plan_sha256()
                .map_err(|_| AuthorityBootstrapError("authority_plan_recompute_failed"))?
                != plan_sha256
                || preview.transaction_sha256().map_err(|_| {
                    AuthorityBootstrapError("authority_transaction_recompute_failed")
                })? != transaction_sha256)
        {
            return Err(AuthorityBootstrapError(
                "authority_active_head_plan_binding_mismatch",
            ));
        }
        let binding = self.published_runtime_binding;
        let maintenance_terminal_binding = Some(AuthorityBootstrapTerminalBinding {
            generation: binding.generation_sha256(),
            plan_sha256: binding.plan_sha256(),
            transaction_sha256: binding.transaction_sha256(),
            activation_epoch: binding.expected_activation_epoch(),
        });
        let (service_process_id, service_process_creation_time) = current_process_identity()?;
        let mut service_file_id = [0u8; 16];
        service_file_id[..8].copy_from_slice(&service_identity.file_id.to_be_bytes());
        let candidate_service_process = activation::CandidateProcessEvidence::from_held_process(
            service_process_id,
            service_process_creation_time,
            *service_descriptor.sha256(),
            service_identity.byte_length,
            u64::from(service_identity.volume_serial),
            service_file_id,
            service_identity.link_count,
            service_identity.attributes,
        )?;

        self.service_security = Some(service_security);
        self.layout = Some(layout.clone());
        self.generation = Some(generation);
        self.activation_names = activation_names.clone();
        self.service_image_path = Some(service_path);
        self.service_executable_path_sha256 = Some(service_executable_path_sha256);
        self.service_executable_file_identity_sha256 =
            Some(service_executable_file_identity_sha256);
        self.scm_readback_sha256 = Some(scm_readback_sha256);

        Ok((
            layout,
            AuthorityBootstrapSnapshot {
                schema: SERVICE_BOOTSTRAP_SCHEMA,
                active_head_bytes,
                trust_manifest_bytes,
                activation_manifest_bytes,
                activation_history,
                activation_directory_names: activation_names,
                installed_content,
                runner_policy_state: runner_policy_descriptor,
                runner_policy_sealed_identity,
                current_service_image: service_descriptor,
                key_readback,
                ledger_identity,
                ledger_frame_count: ledger_readback.frame_count(),
                ledger_byte_length: ledger_readback.ledger_byte_length(),
                ledger_sha256: *ledger_readback.ledger_sha256(),
                ledger_anchor_byte_length: ledger_readback.anchor_byte_length(),
                ledger_anchor_sha256: *ledger_readback.anchor_sha256(),
                active_ticket_count: ledger_readback.active_ticket_count(),
                protected_artifacts: REQUIRED_ARTIFACTS
                    .into_iter()
                    .map(exact_artifact_readback)
                    .collect(),
                service_process_identity_exact: true,
                service_process_id,
                service_process_creation_time,
                candidate_service_process,
                maintenance_terminal_binding,
            },
        ))
    }

    fn verify_still_stable(&mut self) -> Result<(), AuthorityBootstrapError> {
        let ledger = self
            .authenticated_runtime_ledger
            .as_ref()
            .ok_or(AuthorityBootstrapError(
                "authority_final_commit_ledger_not_adopted",
            ))?;
        ledger
            .verify_current_identity()
            .map_err(|error| map_ledger_error(error.code()))?;
        let observed = ledger
            .authenticated_published_binding_projection()
            .map_err(|error| map_ledger_error(error.code()))?;
        if self.published_runtime_binding != observed {
            return Err(AuthorityBootstrapError(
                "authority_final_commit_binding_changed",
            ));
        }
        verify_common_still_stable(
            &mut self.common,
            NativeServiceConfigurationPhase::CommittedRuntime,
        )
    }
}

impl CandidateValidationBootstrapSource for NativeCandidateValidationBootstrapSource {
    fn load_candidate_snapshot(
        &mut self,
        locator: activation::CandidateServiceStartLocator,
    ) -> Result<(AuthorityLayout, CandidateAuthorityBootstrapSnapshot), AuthorityBootstrapError>
    {
        self.reset_held_state();
        let layout = AuthorityLayout::installed()
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        self.layout = Some(layout.clone());

        // The one-use locator selects the exact credential first. In
        // particular, this lane never opens or derives its target generation
        // from the global active-head file: install has no head yet, while an
        // update still has the predecessor head at this point.
        let credential_readback = self.read_candidate(&locator.transaction_sha256())?;
        let binding = match &credential_readback {
            activation::CandidateCredentialReadback::Record { record, issuer, .. } => {
                let binding = record.binding()?;
                locator.validate_binding(binding)?;
                if *issuer != binding.issuer() {
                    return Err(AuthorityBootstrapError(
                        "authority_candidate_issuer_binding_mismatch",
                    ));
                }
                binding
            }
            activation::CandidateCredentialReadback::None => {
                return Err(AuthorityBootstrapError(
                    "authority_candidate_credential_missing",
                ))
            }
        };
        let generation = *binding.generation();
        let activation_epoch = binding.activation_epoch();
        let activation_names = activation_directory_names(&layout.activations_root())?;
        let active_head_path = layout.active_head_path();
        let prior_head = match std::fs::symlink_metadata(&active_head_path) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                CandidatePriorHeadObservation::Absent
            }
            Err(_) => {
                return Err(AuthorityBootstrapError(
                    "authority_candidate_active_head_prior_unavailable",
                ))
            }
            Ok(_) => {
                let held = open_protected_file(
                    layout.state_anchor(),
                    &active_head_path,
                    STATE_DIRECTORY_SDDL,
                    SEALED_GENERATION_DIRECTORY_SDDL,
                    IMMUTABLE_STATE_FILE_SDDL,
                    16 * 1024,
                    true,
                )?;
                let parsed = ProtectedActiveHead::parse_canonical(
                    held.bytes()
                        .ok_or(AuthorityBootstrapError("authority_active_head_read_failed"))?,
                )
                .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?;
                let head_sha256 = parsed
                    .digest()
                    .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?;
                self.hold(held);
                CandidatePriorHeadObservation::Present { head_sha256 }
            }
        };

        let trust_path = layout
            .trust_manifest_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let trust = open_protected_file(
            layout.state_anchor(),
            &trust_path,
            STATE_DIRECTORY_SDDL,
            STATE_GENERATION_DIRECTORY_SDDL,
            STATE_FILE_SDDL,
            MAX_MANIFEST_BYTES,
            true,
        )?;
        let trust_manifest_bytes = trust
            .bytes()
            .ok_or(AuthorityBootstrapError(
                "authority_trust_manifest_not_verified",
            ))?
            .to_vec();
        let parsed_trust = ProtectedDetachedManifestFile::parse_canonical(&trust_manifest_bytes)
            .map_err(|_| AuthorityBootstrapError("authority_trust_manifest_not_verified"))?;
        let trust_signature = parsed_trust
            .signature_input()
            .map_err(|_| AuthorityBootstrapError("authority_trust_manifest_not_verified"))?;
        self.hold(trust);

        let activation_path = layout
            .activation_manifest_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let activation = open_protected_file(
            layout.state_anchor(),
            &activation_path,
            STATE_DIRECTORY_SDDL,
            STATE_GENERATION_DIRECTORY_SDDL,
            STATE_FILE_SDDL,
            MAX_MANIFEST_BYTES,
            true,
        )?;
        let activation_manifest_bytes = activation
            .bytes()
            .ok_or(AuthorityBootstrapError(
                "authority_activation_manifest_not_verified",
            ))?
            .to_vec();
        let parsed_activation = ProtectedDetachedManifestFile::parse_canonical(
            &activation_manifest_bytes,
        )
        .map_err(|_| AuthorityBootstrapError("authority_activation_manifest_not_verified"))?;
        self.hold(activation);

        let service_path = layout
            .service_executable_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let service = open_protected_file(
            layout.binary_anchor(),
            &service_path,
            BINARY_DIRECTORY_SDDL,
            BINARY_GENERATION_DIRECTORY_SDDL,
            BINARY_FILE_SDDL,
            MAX_AUTHORITY_BINARY_BYTES,
            false,
        )?;
        let service_descriptor = service.descriptor();
        let service_identity = service.identity;
        let service_executable_path_sha256 = authenticated_service_path_sha256(&service_path)?;
        let service_executable_file_identity_sha256 =
            authenticated_service_file_identity_sha256(service_identity, service_descriptor)?;
        let controller_path = layout
            .controller_executable_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let controller = open_protected_file(
            layout.binary_anchor(),
            &controller_path,
            BINARY_DIRECTORY_SDDL,
            BINARY_GENERATION_DIRECTORY_SDDL,
            BINARY_FILE_SDDL,
            MAX_AUTHORITY_BINARY_BYTES,
            false,
        )?;
        let controller_descriptor = controller.descriptor();
        let install_helper_path = layout
            .install_helper_executable_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let install_helper = open_protected_file(
            layout.binary_anchor(),
            &install_helper_path,
            BINARY_DIRECTORY_SDDL,
            BINARY_GENERATION_DIRECTORY_SDDL,
            BINARY_FILE_SDDL,
            MAX_AUTHORITY_BINARY_BYTES,
            false,
        )?;
        let install_helper_descriptor = install_helper.descriptor();
        let lifecycle_driver_path = layout
            .lifecycle_driver_executable_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let lifecycle_driver = open_protected_file(
            layout.binary_anchor(),
            &lifecycle_driver_path,
            BINARY_DIRECTORY_SDDL,
            BINARY_GENERATION_DIRECTORY_SDDL,
            BINARY_FILE_SDDL,
            MAX_AUTHORITY_BINARY_BYTES,
            false,
        )?;
        let lifecycle_driver_descriptor = lifecycle_driver.descriptor();
        let bridge_launcher_path = layout
            .bridge_launcher_executable_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let bridge_launcher = open_protected_file(
            layout.binary_anchor(),
            &bridge_launcher_path,
            BINARY_DIRECTORY_SDDL,
            BINARY_GENERATION_DIRECTORY_SDDL,
            BINARY_FILE_SDDL,
            MAX_AUTHORITY_BINARY_BYTES,
            false,
        )?;
        let bridge_launcher_descriptor = bridge_launcher.descriptor();
        let runtime_source_manifest_path = layout
            .runtime_source_manifest_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let runtime_source_manifest = open_protected_file(
            layout.state_anchor(),
            &runtime_source_manifest_path,
            STATE_DIRECTORY_SDDL,
            STATE_GENERATION_DIRECTORY_SDDL,
            STATE_FILE_SDDL,
            MAX_RUNTIME_SOURCE_MANIFEST_BYTES,
            false,
        )?;
        let runtime_source_manifest_descriptor = runtime_source_manifest.descriptor();
        let installed_content = AuthorityInstallContent::new(
            service_descriptor,
            controller_descriptor,
            install_helper_descriptor,
            lifecycle_driver_descriptor,
            bridge_launcher_descriptor,
            runtime_source_manifest_descriptor,
        )
        .map_err(|_| AuthorityBootstrapError("authority_installed_content_invalid"))?;
        self.hold(service);
        self.hold(controller);
        self.hold(install_helper);
        self.hold_root_executables(lifecycle_driver, bridge_launcher)?;
        self.hold_runtime_source_manifest(runtime_source_manifest)?;

        if !paths_equal(&current_process_image_path()?, &service_path) {
            return Err(AuthorityBootstrapError(
                "authority_service_image_binding_mismatch",
            ));
        }
        let scm_readback_sha256 = verify_service_configuration(
            &layout,
            &generation,
            NativeServiceConfigurationPhase::CandidateValidation,
        )?;
        let service_security = HeldServiceSecurity::open()?;
        service_security.verify()?;

        let key_readback = self.open_key(generation, trust_signature.signer_key_id)?;
        let activation_history = load_historical_activation_chain(
            &mut self.common,
            &layout,
            &parsed_activation,
            activation_epoch,
        )?;
        let ledger_identity = derive_ledger_identity(&generation, key_readback.signer_key_id())
            .map_err(|_| AuthorityBootstrapError("authority_ledger_identity_invalid"))?;
        let native_ledger_identity = LedgerIdentity::from_hex(
            &hex_lower(&generation),
            &hex_lower(key_readback.signer_key_id()),
        )
        .map_err(|_| AuthorityBootstrapError("authority_ledger_identity_invalid"))?;
        let ledger_path = layout
            .ledger_file_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let anchor_path = layout
            .ledger_anchor_file_for_generation(&generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let ledger_before = open_protected_file(
            layout.state_anchor(),
            &ledger_path,
            STATE_DIRECTORY_SDDL,
            STATE_GENERATION_DIRECTORY_SDDL,
            LEDGER_FILE_SDDL,
            MAX_LEDGER_ARTIFACT_BYTES,
            false,
        )?
        .observation();
        let anchor_before = open_protected_file(
            layout.state_anchor(),
            &anchor_path,
            STATE_DIRECTORY_SDDL,
            STATE_GENERATION_DIRECTORY_SDDL,
            LEDGER_FILE_SDDL,
            MAX_LEDGER_ARTIFACT_BYTES,
            false,
        )?
        .observation();
        let ledger_readback =
            AuthorityLedger::inspect_existing_clean(&ledger_path, native_ledger_identity)
                .map_err(|error| map_ledger_error(error.code()))?;
        let ledger = open_protected_file(
            layout.state_anchor(),
            &ledger_path,
            STATE_DIRECTORY_SDDL,
            STATE_GENERATION_DIRECTORY_SDDL,
            LEDGER_FILE_SDDL,
            MAX_LEDGER_ARTIFACT_BYTES,
            false,
        )?;
        let anchor = open_protected_file(
            layout.state_anchor(),
            &anchor_path,
            STATE_DIRECTORY_SDDL,
            STATE_GENERATION_DIRECTORY_SDDL,
            LEDGER_FILE_SDDL,
            MAX_LEDGER_ARTIFACT_BYTES,
            false,
        )?;
        if ledger.observation() != ledger_before
            || anchor.observation() != anchor_before
            || ledger_before.descriptor.byte_length() != ledger_readback.ledger_byte_length()
            || ledger_before.descriptor.sha256() != ledger_readback.ledger_sha256()
            || anchor_before.descriptor.byte_length() != ledger_readback.anchor_byte_length()
            || anchor_before.descriptor.sha256() != ledger_readback.anchor_sha256()
        {
            return Err(AuthorityBootstrapError(
                "authority_ledger_artifact_identity_changed",
            ));
        }
        self.hold(ledger);
        self.hold(anchor);

        let (service_process_id, service_process_creation_time) = current_process_identity()?;
        let mut service_file_id = [0u8; 16];
        service_file_id[..8].copy_from_slice(&service_identity.file_id.to_be_bytes());
        let candidate_service_process = activation::CandidateProcessEvidence::from_held_process(
            service_process_id,
            service_process_creation_time,
            *service_descriptor.sha256(),
            service_identity.byte_length,
            u64::from(service_identity.volume_serial),
            service_file_id,
            service_identity.link_count,
            service_identity.attributes,
        )?;

        self.service_security = Some(service_security);
        self.generation = Some(generation);
        self.activation_names = activation_names.clone();
        self.service_image_path = Some(service_path);
        self.service_executable_path_sha256 = Some(service_executable_path_sha256);
        self.service_executable_file_identity_sha256 =
            Some(service_executable_file_identity_sha256);
        self.scm_readback_sha256 = Some(scm_readback_sha256);
        Ok((
            layout,
            CandidateAuthorityBootstrapSnapshot {
                schema: SERVICE_BOOTSTRAP_SCHEMA,
                credential_readback,
                prior_head,
                trust_manifest_bytes,
                activation_manifest_bytes,
                activation_history,
                activation_directory_names: activation_names,
                installed_content,
                current_service_image: service_descriptor,
                key_readback,
                ledger_identity,
                ledger_frame_count: ledger_readback.frame_count(),
                ledger_byte_length: ledger_readback.ledger_byte_length(),
                ledger_sha256: *ledger_readback.ledger_sha256(),
                ledger_anchor_byte_length: ledger_readback.anchor_byte_length(),
                ledger_anchor_sha256: *ledger_readback.anchor_sha256(),
                protected_artifacts: CANDIDATE_REQUIRED_ARTIFACTS
                    .into_iter()
                    .map(exact_artifact_readback)
                    .collect(),
                service_process_identity_exact: true,
                service_process_id,
                service_process_creation_time,
                candidate_service_process,
            },
        ))
    }

    fn verify_still_stable(&mut self) -> Result<(), AuthorityBootstrapError> {
        verify_common_still_stable(
            &mut self.common,
            NativeServiceConfigurationPhase::CandidateValidation,
        )?;
        let candidate = self
            .candidate_credential
            .as_mut()
            .ok_or(AuthorityBootstrapError(
                "authority_candidate_credential_missing",
            ))?;
        candidate.verify_exact_record()?;
        let binding = candidate.record.binding()?;
        let layout = self
            .common
            .layout
            .as_ref()
            .ok_or(AuthorityBootstrapError("authority_layout_unavailable"))?;
        let observed = open_candidate_activation_receipt_binding(
            layout,
            *binding.issuer().capsule_sha256(),
            *binding.plan_sha256(),
            *binding.generation(),
            *binding.transaction_sha256(),
        )
        .map_err(|error| AuthorityBootstrapError(error.code()))?;
        let issuer = candidate_issuer_from_native(&candidate.record, &observed)?;
        if Some(issuer) != self.candidate_issuer || issuer != binding.issuer() {
            return Err(AuthorityBootstrapError(
                "authority_candidate_issuer_binding_changed",
            ));
        }
        Ok(())
    }
}

impl activation::CandidateCredentialConsumer for NativeCandidateValidationBootstrapSource {
    fn read_candidate(
        &mut self,
        transaction_sha256: &[u8; 32],
    ) -> Result<activation::CandidateCredentialReadback, AuthorityBootstrapError> {
        if self.candidate_credential.is_some() {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_reader_state_invalid",
            ));
        }
        let layout = self
            .layout
            .as_ref()
            .ok_or(AuthorityBootstrapError("authority_layout_unavailable"))?;
        let file_name = activation::candidate_credential_file_name(transaction_sha256)?;
        let path = layout.candidate_activation_root().join(file_name);
        match std::fs::symlink_metadata(&path) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(activation::CandidateCredentialReadback::None)
            }
            Err(_) => {
                return Err(AuthorityBootstrapError(
                    "authority_candidate_credential_metadata_unavailable",
                ))
            }
            Ok(_) => {}
        }
        let held = open_candidate_credential(layout.state_root(), &path)?;
        let mut record = held.record.clone();
        let binding = record.binding()?;
        if binding.transaction_sha256() != transaction_sha256 {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_binding_mismatch",
            ));
        }
        let observed = open_candidate_activation_receipt_binding(
            layout,
            *binding.issuer().capsule_sha256(),
            *binding.plan_sha256(),
            *binding.generation(),
            *binding.transaction_sha256(),
        )
        .map_err(|error| AuthorityBootstrapError(error.code()))?;
        let issuer = candidate_issuer_from_native(&record, &observed)?;
        if issuer != binding.issuer() {
            return Err(AuthorityBootstrapError(
                "authority_candidate_issuer_binding_mismatch",
            ));
        }
        let credential_sha256 = record.credential_sha256()?;
        if let Some(consumption) =
            open_candidate_consumption_tombstone_for_candidate(layout, &credential_sha256)
                .map_err(|error| AuthorityBootstrapError(error.code()))?
        {
            if record.phase() != activation::CandidateCredentialPhase::Armed {
                return Err(AuthorityBootstrapError(
                    "authority_candidate_consumption_source_phase_invalid",
                ));
            }
            let consumed =
                activation::CandidateCredentialRecord::parse_canonical(consumption.bytes())?;
            if consumed.phase() != activation::CandidateCredentialPhase::Consumed
                || consumed.credential_sha256()? != credential_sha256
                || consumed.binding()? != binding
            {
                return Err(AuthorityBootstrapError(
                    "authority_candidate_consumption_tombstone_mismatch",
                ));
            }
            let (volume_serial, file_id, link_count, bytes_sha256) =
                consumption.durable_identity_with_link_count();
            if volume_serial == 0
                || file_id.iter().all(|byte| *byte == 0)
                || link_count != 1
                || bytes_sha256.iter().all(|byte| *byte == 0)
            {
                return Err(AuthorityBootstrapError(
                    "authority_candidate_consumption_tombstone_identity_invalid",
                ));
            }
            record = consumed;
            self.candidate_consumption = Some(consumption);
        }
        self.candidate_credential = Some(held);
        self.candidate_issuer = Some(issuer);
        Ok(activation::CandidateCredentialReadback::Record {
            record,
            issuer,
            armed_receipt_sha256: observed.candidate_credential_armed_receipt_sha256,
        })
    }

    fn consume_armed(
        &mut self,
        expected: &activation::CandidateCredentialRecord,
        request: &activation::CandidateValidationRequest,
        client_peer: activation::CandidateProcessEvidence,
    ) -> Result<activation::CandidateCredentialRecord, AuthorityBootstrapError> {
        if self.candidate_consumption.is_some() {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_compare_exchange_failed",
            ));
        }
        let held = self
            .candidate_credential
            .as_mut()
            .ok_or(AuthorityBootstrapError(
                "authority_candidate_credential_missing",
            ))?;
        held.verify_exact(expected)?;
        if expected.phase() != activation::CandidateCredentialPhase::Armed {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_compare_exchange_failed",
            ));
        }
        let binding = expected.binding()?;
        let layout = self
            .layout
            .as_ref()
            .ok_or(AuthorityBootstrapError("authority_layout_unavailable"))?;
        let observed = open_candidate_activation_receipt_binding(
            layout,
            *binding.issuer().capsule_sha256(),
            *binding.plan_sha256(),
            *binding.generation(),
            *binding.transaction_sha256(),
        )
        .map_err(|error| AuthorityBootstrapError(error.code()))?;
        let issuer = candidate_issuer_from_native(expected, &observed)?;
        if Some(issuer) != self.candidate_issuer || issuer != binding.issuer() {
            return Err(AuthorityBootstrapError(
                "authority_candidate_issuer_binding_changed",
            ));
        }
        let consumed = expected.consume_with_peer(request, client_peer)?;
        let consumed_bytes = consumed.canonical_bytes()?;
        let lease = create_candidate_consumption_tombstone_for_candidate(
            layout,
            &expected.credential_sha256()?,
            &consumed_bytes,
        )
        .map_err(|error| AuthorityBootstrapError(error.code()))?;
        if lease.bytes() != consumed_bytes
            || activation::CandidateCredentialRecord::parse_canonical(lease.bytes())? != consumed
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_consumption_not_verified",
            ));
        }
        let (volume_serial, file_id, link_count, bytes_sha256) =
            lease.durable_identity_with_link_count();
        if volume_serial == 0
            || file_id.iter().all(|byte| *byte == 0)
            || link_count != 1
            || bytes_sha256.iter().all(|byte| *byte == 0)
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_consumption_tombstone_identity_invalid",
            ));
        }
        self.candidate_consumption = Some(lease);
        Ok(consumed)
    }
}

fn candidate_issuer_from_native(
    armed: &activation::CandidateCredentialRecord,
    observed: &NativeCandidateActivationReceiptBinding,
) -> Result<activation::CandidateIssuerBinding, AuthorityBootstrapError> {
    if armed.phase() != activation::CandidateCredentialPhase::Armed
        || armed.credential_sha256()? != observed.candidate_credential_sha256
        || armed.record_sha256()? != observed.candidate_credential_armed_record_sha256
        || armed.armed_receipt_sha256()? != observed.candidate_credential_armed_receipt_sha256
        || armed.candidate_service() != Some(&observed.candidate_service)
        || observed.candidate_credential_armed_journal_sequence == 0
        || observed
            .candidate_credential_armed_journal_record_sha256
            .iter()
            .all(|byte| *byte == 0)
    {
        return Err(AuthorityBootstrapError(
            "authority_candidate_armed_receipt_binding_mismatch",
        ));
    }
    activation::CandidateIssuerBinding::new(
        observed.capsule_sha256,
        observed.transaction_started_receipt_sha256,
        observed.worker_started_receipt_sha256,
        observed.maintenance_worker,
        observed.nonce_consumption_receipt_sha256,
        observed.nonce_consumption_full_readback_sha256,
        observed.nonce_consumption_file_sha256,
        observed.nonce_consumption_file_volume_serial,
        observed.nonce_consumption_file_id,
    )
}

fn load_historical_activation_chain(
    source: &mut NativeBootstrapSourceCommon,
    layout: &AuthorityLayout,
    current_activation: &ProtectedDetachedManifestFile,
    current_epoch: u64,
) -> Result<Vec<AuthorityBootstrapHistoricalGeneration>, AuthorityBootstrapError> {
    if current_epoch == 0 || current_epoch > MAX_BOOTSTRAP_ACTIVATION_EPOCH {
        return Err(AuthorityBootstrapError(
            "authority_service_bootstrap_update_chain_invalid",
        ));
    }
    let mut cursor = current_activation.clone();
    let mut expected_epoch = current_epoch;
    let mut seen = BTreeSet::new();
    let mut reverse_history = Vec::new();
    loop {
        let (
            cursor_generation,
            cursor_epoch,
            previous_generation,
            previous_activation_sha256,
            previous_activation_epoch,
        ) = match cursor
            .unsigned_payload()
            .map_err(|_| AuthorityBootstrapError("authority_activation_manifest_not_verified"))?
        {
            CanonicalUnsignedManifestPayload::Activation {
                generation,
                activated_epoch,
                previous_generation,
                previous_activation_sha256,
                previous_activation_epoch,
                ..
            } => (
                generation,
                activated_epoch,
                previous_generation,
                previous_activation_sha256,
                previous_activation_epoch,
            ),
            _ => {
                return Err(AuthorityBootstrapError(
                    "authority_activation_manifest_not_verified",
                ))
            }
        };
        if cursor_epoch != expected_epoch || !seen.insert(cursor_generation) {
            return Err(AuthorityBootstrapError(
                "authority_service_bootstrap_update_chain_invalid",
            ));
        }
        let (previous_generation, previous_epoch) = match (
            previous_generation,
            previous_activation_sha256,
            previous_activation_epoch,
        ) {
            (None, None, None) if cursor_epoch == 1 => break,
            (Some(generation), Some(_), Some(epoch))
                if epoch.checked_add(1) == Some(cursor_epoch) =>
            {
                (generation, epoch)
            }
            _ => {
                return Err(AuthorityBootstrapError(
                    "authority_service_bootstrap_update_chain_invalid",
                ))
            }
        };
        if reverse_history.len() >= MAX_BOOTSTRAP_ACTIVATION_EPOCH as usize - 1
            || seen.contains(&previous_generation)
        {
            return Err(AuthorityBootstrapError(
                "authority_service_bootstrap_update_chain_invalid",
            ));
        }

        let activation_path = layout
            .activation_manifest_for_generation(&previous_generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let activation = open_protected_file(
            layout.state_anchor(),
            &activation_path,
            STATE_DIRECTORY_SDDL,
            SEALED_GENERATION_DIRECTORY_SDDL,
            IMMUTABLE_STATE_FILE_SDDL,
            MAX_MANIFEST_BYTES,
            true,
        )?;
        let activation_manifest_bytes = activation
            .bytes()
            .ok_or(AuthorityBootstrapError(
                "authority_activation_manifest_not_verified",
            ))?
            .to_vec();
        let parsed_activation = ProtectedDetachedManifestFile::parse_canonical(
            &activation_manifest_bytes,
        )
        .map_err(|_| AuthorityBootstrapError("authority_activation_manifest_not_verified"))?;

        let trust_path = layout
            .trust_manifest_for_generation(&previous_generation)
            .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
        let trust = open_protected_file(
            layout.state_anchor(),
            &trust_path,
            STATE_DIRECTORY_SDDL,
            SEALED_GENERATION_DIRECTORY_SDDL,
            IMMUTABLE_STATE_FILE_SDDL,
            MAX_MANIFEST_BYTES,
            true,
        )?;
        let trust_manifest_bytes = trust
            .bytes()
            .ok_or(AuthorityBootstrapError(
                "authority_trust_manifest_not_verified",
            ))?
            .to_vec();
        let trust_signature = ProtectedDetachedManifestFile::parse_canonical(&trust_manifest_bytes)
            .and_then(|manifest| manifest.signature_input())
            .map_err(|_| AuthorityBootstrapError("authority_trust_manifest_not_verified"))?;
        let key_readback = source.open_key(previous_generation, trust_signature.signer_key_id)?;
        source.hold(activation);
        source.hold(trust);
        reverse_history.push(AuthorityBootstrapHistoricalGeneration {
            generation: previous_generation,
            trust_manifest_bytes,
            activation_manifest_bytes,
            key_readback,
        });
        cursor = parsed_activation;
        expected_epoch = previous_epoch;
    }
    let expected_history_length = usize::try_from(current_epoch - 1)
        .map_err(|_| AuthorityBootstrapError("authority_service_bootstrap_update_chain_invalid"))?;
    if reverse_history.len() != expected_history_length {
        return Err(AuthorityBootstrapError(
            "authority_service_bootstrap_update_chain_invalid",
        ));
    }
    reverse_history.reverse();
    Ok(reverse_history)
}

fn exact_artifact_readback(kind: BootstrapArtifactKind) -> ProtectedArtifactReadback {
    ProtectedArtifactReadback {
        kind,
        path_exact: true,
        local_volume: true,
        reparse_free_held_chain: true,
        single_link: true,
        stable_identity: true,
        exact_owner_and_acl: true,
        full_held_handle_readback: true,
    }
}

#[cfg(test)]
fn validate_committed_terminal_receipt(
    receipt: &MaintenanceJournalTerminalReceipt,
    generation: [u8; 32],
    plan_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
    activation_epoch: u64,
) -> Result<AuthorityBootstrapTerminalBinding, AuthorityBootstrapError> {
    if receipt.generation().ok() != Some(generation)
        || receipt.plan_sha256().ok() != Some(plan_sha256)
        || receipt.transaction_sha256().ok() != Some(transaction_sha256)
        || receipt.activation_epoch() != activation_epoch
        || receipt.terminal() != MaintenanceJournalTerminalKind::Committed
        || receipt.receipt_sha256().is_err()
    {
        return Err(AuthorityBootstrapError(
            "authority_maintenance_journal_not_terminal",
        ));
    }
    Ok(AuthorityBootstrapTerminalBinding {
        generation,
        plan_sha256,
        transaction_sha256,
        activation_epoch,
    })
}

fn verify_service_configuration(
    layout: &AuthorityLayout,
    generation: &[u8; 32],
    phase: NativeServiceConfigurationPhase,
) -> Result<[u8; 32], AuthorityBootstrapError> {
    let readback = inspect_installed_authority_for_generation(layout, generation)
        .map_err(|_| AuthorityBootstrapError("authority_service_readback_unavailable"))?;
    let process_id = unsafe { GetCurrentProcessId() };
    let exact = match phase {
        NativeServiceConfigurationPhase::CandidateValidation => {
            readback.candidate_service_configuration_exact_for_start_pending_process(process_id)
        }
        NativeServiceConfigurationPhase::CommittedRuntime => {
            readback.bootstrap_service_configuration_exact_for_process(process_id)
        }
    };
    if !exact {
        return Err(AuthorityBootstrapError(
            "authority_service_configuration_mismatch",
        ));
    }
    let serialized = serde_json::to_vec(&readback)
        .map_err(|_| AuthorityBootstrapError("authority_scm_readback_serialization_failed"))?;
    if serialized.is_empty() {
        return Err(AuthorityBootstrapError(
            "authority_scm_readback_serialization_failed",
        ));
    }
    let lane_tag = match phase {
        NativeServiceConfigurationPhase::CandidateValidation => 1u8,
        NativeServiceConfigurationPhase::CommittedRuntime => 2u8,
    };
    let mut digest = Sha256::new();
    digest.update(AUTHENTICATED_SCM_READBACK_DOMAIN);
    digest.update(generation);
    digest.update([lane_tag]);
    digest.update((serialized.len() as u64).to_be_bytes());
    digest.update(serialized);
    Ok(digest.finalize().into())
}

fn map_key_error(code: &str) -> AuthorityBootstrapError {
    match code {
        "authority_key_missing" => AuthorityBootstrapError("authority_key_missing"),
        "authority_key_security_descriptor_mismatch" => {
            AuthorityBootstrapError("authority_key_security_descriptor_mismatch")
        }
        _ => AuthorityBootstrapError("authority_key_not_verified"),
    }
}

fn map_ledger_error(code: &str) -> AuthorityBootstrapError {
    match code {
        "ledger_missing" => AuthorityBootstrapError("authority_ledger_missing"),
        "ledger_anchor_missing" => AuthorityBootstrapError("authority_ledger_anchor_missing"),
        "ledger_anchor_mismatch" => AuthorityBootstrapError("authority_ledger_anchor_mismatch"),
        "ledger_recovery_required" => AuthorityBootstrapError("authority_ledger_recovery_required"),
        _ => AuthorityBootstrapError("authority_ledger_not_verified"),
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct HeldFileIdentity {
    volume_serial: u32,
    file_id: u64,
    byte_length: u64,
    link_count: u32,
    attributes: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct HeldFileObservation {
    identity: HeldFileIdentity,
    descriptor: AuthorityPayloadDigest,
}

fn runner_policy_sealed_identity_from_held(
    identity: HeldFileIdentity,
) -> Result<RunnerPolicySealedIdentity, AuthorityBootstrapError> {
    let mut file_id = [0u8; 16];
    file_id[..8].copy_from_slice(&identity.file_id.to_be_bytes());
    RunnerPolicySealedIdentity::new(
        u64::from(identity.volume_serial),
        file_id,
        identity.link_count,
        identity.attributes,
    )
    .map_err(|_| AuthorityBootstrapError("authority_runner_policy_sealed_identity_invalid"))
}

fn verify_runner_policy_sealed_identity(
    observed: HeldFileIdentity,
    expected: RunnerPolicySealedIdentity,
) -> Result<(), AuthorityBootstrapError> {
    let observed = runner_policy_sealed_identity_from_held(observed)?;
    expected
        .validate()
        .map_err(|_| AuthorityBootstrapError("authority_runner_policy_sealed_identity_invalid"))?;
    if observed != expected {
        return Err(AuthorityBootstrapError(
            "authority_runner_policy_sealed_identity_mismatch",
        ));
    }
    Ok(())
}

struct HeldDirectory {
    file: File,
    path: PathBuf,
    identity: HeldFileIdentity,
    expected_sddl: Option<&'static str>,
}

impl HeldDirectory {
    fn verify(&self) -> Result<(), AuthorityBootstrapError> {
        let identity = held_file_identity(&self.file)?;
        if identity != self.identity
            || identity.attributes & FILE_ATTRIBUTE_DIRECTORY == 0
            || identity.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        {
            return Err(AuthorityBootstrapError(
                "authority_protected_directory_identity_changed",
            ));
        }
        require_exact_handle_path(&self.file, &self.path)?;
        if let Some(expected) = self.expected_sddl {
            verify_file_security(&self.file, expected)?;
        }
        Ok(())
    }
}

struct HeldProtectedFile {
    chain: Vec<HeldDirectory>,
    file: File,
    path: PathBuf,
    identity: HeldFileIdentity,
    descriptor: AuthorityPayloadDigest,
    bytes: Option<Vec<u8>>,
    expected_sddl: &'static str,
    maximum_size: u64,
}

struct HeldCandidateCredential {
    chain: Vec<HeldDirectory>,
    file: File,
    path: PathBuf,
    identity: HeldFileIdentity,
    descriptor: AuthorityPayloadDigest,
    record: activation::CandidateCredentialRecord,
}

impl HeldCandidateCredential {
    fn verify_exact(
        &mut self,
        expected: &activation::CandidateCredentialRecord,
    ) -> Result<(), AuthorityBootstrapError> {
        if &self.record != expected {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_compare_exchange_failed",
            ));
        }
        for directory in &self.chain {
            directory.verify()?;
        }
        require_exact_handle_path(&self.file, &self.path)?;
        verify_file_security(&self.file, STATE_FILE_SDDL)?;
        let (before, descriptor, bytes) = read_held_file(
            &mut self.file,
            activation::MAX_CANDIDATE_CREDENTIAL_BYTES,
            true,
        )?;
        if before != self.identity
            || descriptor != self.descriptor
            || bytes.as_deref() != Some(expected.canonical_bytes()?.as_slice())
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_compare_exchange_failed",
            ));
        }
        require_exact_handle_path(&self.file, &self.path)?;
        verify_file_security(&self.file, STATE_FILE_SDDL)?;
        for directory in &self.chain {
            directory.verify()?;
        }
        Ok(())
    }

    fn verify_exact_record(&mut self) -> Result<(), AuthorityBootstrapError> {
        let expected = self.record.clone();
        self.verify_exact(&expected)
    }
}

impl NativeAuthenticatedRuntimeSourceCapability {
    pub(super) fn verify(
        &mut self,
        expected: AuthorityPayloadDigest,
    ) -> Result<(), AuthorityBootstrapError> {
        self.manifest.verify_exact_descriptor(expected)
    }

    pub(super) fn read_verified(
        &mut self,
        expected: AuthorityPayloadDigest,
    ) -> Result<AuthenticatedRuntimeSourceReadback, AuthorityBootstrapError> {
        self.manifest.verify_exact_descriptor(expected)?;
        let (identity, descriptor, bytes) = read_held_file(
            &mut self.manifest.file,
            MAX_RUNTIME_SOURCE_MANIFEST_BYTES,
            true,
        )?;
        if identity != self.manifest.identity || descriptor != expected {
            return Err(AuthorityBootstrapError(
                "authority_runtime_source_capability_binding_mismatch",
            ));
        }
        let bytes = bytes.ok_or(AuthorityBootstrapError(
            "authority_runtime_source_capability_read_failed",
        ))?;
        self.manifest.verify_exact_descriptor(expected)?;

        let mut identity_digest = Sha256::new();
        identity_digest.update(AUTHENTICATED_RUNTIME_SOURCE_IDENTITY_DOMAIN);
        identity_digest.update(identity.volume_serial.to_be_bytes());
        identity_digest.update(identity.file_id.to_be_bytes());
        identity_digest.update(identity.byte_length.to_be_bytes());
        identity_digest.update(identity.link_count.to_be_bytes());
        identity_digest.update(identity.attributes.to_be_bytes());
        identity_digest.update(descriptor.sha256());
        Ok(AuthenticatedRuntimeSourceReadback {
            descriptor,
            identity_sha256: identity_digest.finalize().into(),
            bytes,
        })
    }
}

fn verify_runner_policy_held_file(
    state: &mut HeldProtectedFile,
    expected: RunnerPolicyStateDescriptor,
    expected_sealed_identity: RunnerPolicySealedIdentity,
) -> Result<Vec<u8>, AuthorityBootstrapError> {
    state.verify()?;
    verify_runner_policy_sealed_identity(state.identity, expected_sealed_identity)?;
    let expected_payload =
        AuthorityPayloadDigest::new(expected.bytes_sha256(), expected.byte_length())
            .map_err(|_| AuthorityBootstrapError("authority_runner_policy_descriptor_invalid"))?;
    if state.descriptor != expected_payload {
        return Err(AuthorityBootstrapError(
            "authority_runner_policy_file_descriptor_mismatch",
        ));
    }
    let (identity, descriptor, bytes) =
        read_held_file(&mut state.file, MAX_RUNNER_POLICY_STATE_BYTES, true)?;
    let bytes = bytes.ok_or(AuthorityBootstrapError(
        "authority_runner_policy_state_read_failed",
    ))?;
    if identity != state.identity || descriptor != expected_payload {
        return Err(AuthorityBootstrapError(
            "authority_runner_policy_held_file_identity_mismatch",
        ));
    }
    verify_runner_policy_sealed_identity(identity, expected_sealed_identity)?;
    let parsed = CanonicalRunnerPolicyState::parse_canonical(&bytes)
        .map_err(|_| AuthorityBootstrapError("authority_runner_policy_canonical_invalid"))?;
    let observed = parsed
        .descriptor()
        .map_err(|_| AuthorityBootstrapError("authority_runner_policy_descriptor_invalid"))?;
    if observed != expected {
        return Err(AuthorityBootstrapError(
            "authority_runner_policy_readback_binding_mismatch",
        ));
    }
    state.verify()?;
    Ok(bytes)
}

impl NativeAuthenticatedRunnerPolicyCapability {
    pub(super) fn verify(
        &mut self,
        expected_generation: [u8; 32],
        expected_transaction_sha256: [u8; 32],
        expected_final_commit_receipt_sha256: [u8; 32],
        expected: RunnerPolicyStateDescriptor,
        expected_sealed_identity: RunnerPolicySealedIdentity,
    ) -> Result<(), AuthorityBootstrapError> {
        if self.generation != expected_generation
            || self.transaction_sha256 != expected_transaction_sha256
            || self.final_commit_receipt_sha256 != expected_final_commit_receipt_sha256
            || self.descriptor != expected
            || self.sealed_identity != expected_sealed_identity
            || expected.generation_sha256() != expected_generation
            || expected.transaction_sha256() != expected_transaction_sha256
            || expected_final_commit_receipt_sha256
                .iter()
                .all(|value| *value == 0)
        {
            return Err(AuthorityBootstrapError(
                "authority_runner_policy_final_commit_binding_mismatch",
            ));
        }
        verify_runner_policy_held_file(&mut self.state, expected, expected_sealed_identity)
            .map(|_| ())
    }

    pub(super) fn read_once(
        mut self,
        expected_generation: [u8; 32],
        expected_transaction_sha256: [u8; 32],
        expected_final_commit_receipt_sha256: [u8; 32],
        expected: RunnerPolicyStateDescriptor,
        expected_sealed_identity: RunnerPolicySealedIdentity,
    ) -> Result<NativeAuthenticatedRunnerPolicyReadback, AuthorityBootstrapError> {
        self.verify(
            expected_generation,
            expected_transaction_sha256,
            expected_final_commit_receipt_sha256,
            expected,
            expected_sealed_identity,
        )?;
        let bytes =
            verify_runner_policy_held_file(&mut self.state, expected, expected_sealed_identity)?;
        let identity = self.state.identity;
        let mut digest = Sha256::new();
        digest.update(AUTHENTICATED_RUNNER_POLICY_IDENTITY_DOMAIN);
        digest.update(expected_generation);
        digest.update(expected_transaction_sha256);
        digest.update(expected_final_commit_receipt_sha256);
        digest.update(expected.byte_length().to_be_bytes());
        digest.update(expected.bytes_sha256());
        digest.update(expected.binding_sha256());
        digest.update(identity.volume_serial.to_be_bytes());
        digest.update(identity.file_id.to_be_bytes());
        digest.update(identity.byte_length.to_be_bytes());
        digest.update(identity.link_count.to_be_bytes());
        digest.update(identity.attributes.to_be_bytes());
        Ok(NativeAuthenticatedRunnerPolicyReadback {
            held_file_identity_sha256: digest.finalize().into(),
            bytes,
        })
    }
}

impl NativeAuthenticatedProtectedRootExecutablesCapability {
    pub(super) fn verify(
        &mut self,
        expected_generation: [u8; 32],
        expected_final_commit_receipt_sha256: [u8; 32],
        expected_lifecycle_driver: AuthorityPayloadDigest,
        expected_bridge_launcher: AuthorityPayloadDigest,
    ) -> Result<(), AuthorityBootstrapError> {
        validate_root_executable_paths(
            expected_generation,
            self.generation,
            &self.lifecycle_driver_path,
            &self.bridge_launcher_path,
        )?;
        if expected_final_commit_receipt_sha256
            .iter()
            .all(|value| *value == 0)
            || self.final_commit_receipt_sha256 != expected_final_commit_receipt_sha256
        {
            return Err(AuthorityBootstrapError(
                "authority_root_executable_receipt_binding_mismatch",
            ));
        }
        self.lifecycle_driver
            .verify_exact_descriptor(expected_lifecycle_driver)?;
        self.bridge_launcher
            .verify_exact_descriptor(expected_bridge_launcher)?;
        require_exact_read_only_handle(&self.lifecycle_driver.file)?;
        require_exact_read_only_handle(&self.bridge_launcher.file)?;
        if self.lifecycle_driver.identity == self.bridge_launcher.identity
            || paths_equal(&self.lifecycle_driver.path, &self.bridge_launcher.path)
        {
            return Err(AuthorityBootstrapError(
                "authority_root_executable_capability_alias",
            ));
        }
        Ok(())
    }

    pub(super) fn with_verified_files<R>(
        &mut self,
        expected_generation: [u8; 32],
        expected_final_commit_receipt_sha256: [u8; 32],
        expected_lifecycle_driver: AuthorityPayloadDigest,
        expected_bridge_launcher: AuthorityPayloadDigest,
        operation: impl FnOnce(&File, &File) -> Result<R, AuthorityBootstrapError>,
    ) -> Result<R, AuthorityBootstrapError> {
        self.verify(
            expected_generation,
            expected_final_commit_receipt_sha256,
            expected_lifecycle_driver,
            expected_bridge_launcher,
        )?;
        let result = operation(&self.lifecycle_driver.file, &self.bridge_launcher.file)?;
        self.verify(
            expected_generation,
            expected_final_commit_receipt_sha256,
            expected_lifecycle_driver,
            expected_bridge_launcher,
        )?;
        Ok(result)
    }

    pub(super) fn clone_current(
        &mut self,
        expected_generation: [u8; 32],
        expected_final_commit_receipt_sha256: [u8; 32],
        expected_lifecycle_driver: AuthorityPayloadDigest,
        expected_bridge_launcher: AuthorityPayloadDigest,
    ) -> Result<NativeGenerationBoundProtectedExecutableHandles, AuthorityBootstrapError> {
        self.verify(
            expected_generation,
            expected_final_commit_receipt_sha256,
            expected_lifecycle_driver,
            expected_bridge_launcher,
        )?;
        let [lifecycle_driver, bridge_launcher] =
            reopen_root_executable_pair(&self.lifecycle_driver.file, &self.bridge_launcher.file)?;
        let mut output = NativeGenerationBoundProtectedExecutableHandles {
            generation: self.generation,
            final_commit_receipt_sha256: self.final_commit_receipt_sha256,
            lifecycle_driver_path: self.lifecycle_driver_path.clone(),
            bridge_launcher_path: self.bridge_launcher_path.clone(),
            lifecycle_driver_identity: self.lifecycle_driver.identity,
            bridge_launcher_identity: self.bridge_launcher.identity,
            lifecycle_driver_descriptor: expected_lifecycle_driver,
            bridge_launcher_descriptor: expected_bridge_launcher,
            lifecycle_driver,
            bridge_launcher,
        };
        output.verify(
            expected_generation,
            expected_final_commit_receipt_sha256,
            expected_lifecycle_driver,
            expected_bridge_launcher,
        )?;
        self.verify(
            expected_generation,
            expected_final_commit_receipt_sha256,
            expected_lifecycle_driver,
            expected_bridge_launcher,
        )?;
        Ok(output)
    }
}

impl NativeGenerationBoundProtectedExecutableHandles {
    pub(super) fn verify(
        &mut self,
        expected_generation: [u8; 32],
        expected_final_commit_receipt_sha256: [u8; 32],
        expected_lifecycle_driver: AuthorityPayloadDigest,
        expected_bridge_launcher: AuthorityPayloadDigest,
    ) -> Result<(), AuthorityBootstrapError> {
        validate_root_executable_paths(
            expected_generation,
            self.generation,
            &self.lifecycle_driver_path,
            &self.bridge_launcher_path,
        )?;
        if expected_final_commit_receipt_sha256
            .iter()
            .all(|value| *value == 0)
            || self.final_commit_receipt_sha256 != expected_final_commit_receipt_sha256
            || self.lifecycle_driver_descriptor != expected_lifecycle_driver
            || self.bridge_launcher_descriptor != expected_bridge_launcher
            || self.lifecycle_driver_identity == self.bridge_launcher_identity
        {
            return Err(AuthorityBootstrapError(
                "authority_root_executable_clone_binding_mismatch",
            ));
        }
        verify_cloned_root_executable(
            &mut self.lifecycle_driver,
            &self.lifecycle_driver_path,
            self.lifecycle_driver_identity,
            expected_lifecycle_driver,
        )?;
        verify_cloned_root_executable(
            &mut self.bridge_launcher,
            &self.bridge_launcher_path,
            self.bridge_launcher_identity,
            expected_bridge_launcher,
        )
    }

    pub(super) fn into_verified_ordered_files(
        mut self,
        expected_generation: [u8; 32],
        expected_final_commit_receipt_sha256: [u8; 32],
        expected_lifecycle_driver: AuthorityPayloadDigest,
        expected_bridge_launcher: AuthorityPayloadDigest,
    ) -> Result<[File; 2], AuthorityBootstrapError> {
        self.verify(
            expected_generation,
            expected_final_commit_receipt_sha256,
            expected_lifecycle_driver,
            expected_bridge_launcher,
        )?;
        Ok([self.lifecycle_driver, self.bridge_launcher])
    }
}

fn validate_root_executable_paths(
    expected_generation: [u8; 32],
    observed_generation: [u8; 32],
    lifecycle_driver_path: &Path,
    bridge_launcher_path: &Path,
) -> Result<(), AuthorityBootstrapError> {
    let generation_leaf = hex_lower(&expected_generation);
    let expected_parent = lifecycle_driver_path
        .parent()
        .ok_or(AuthorityBootstrapError(
            "authority_root_executable_capability_path_invalid",
        ))?;
    if observed_generation != expected_generation
        || lifecycle_driver_path.file_name()
            != Some(std::ffi::OsStr::new(
                "vrcforge_primitive_lifecycle_driver.exe",
            ))
        || bridge_launcher_path.file_name()
            != Some(std::ffi::OsStr::new(
                "vrcforge_primitive_bridge_launcher.exe",
            ))
        || expected_parent.file_name().and_then(|value| value.to_str())
            != Some(generation_leaf.as_str())
        || bridge_launcher_path.parent() != Some(expected_parent)
    {
        return Err(AuthorityBootstrapError(
            "authority_root_executable_capability_path_invalid",
        ));
    }
    Ok(())
}

fn verify_cloned_root_executable(
    file: &mut File,
    expected_path: &Path,
    expected_identity: HeldFileIdentity,
    expected_descriptor: AuthorityPayloadDigest,
) -> Result<(), AuthorityBootstrapError> {
    require_exact_read_only_handle(file)?;
    require_exact_handle_path(file, expected_path)?;
    verify_file_security(file, SEALED_BINARY_FILE_SDDL)?;
    let (identity, descriptor, _) = read_held_file(file, MAX_AUTHORITY_BINARY_BYTES, false)?;
    if identity != expected_identity
        || descriptor != expected_descriptor
        || identity.link_count != 1
        || identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
    {
        return Err(AuthorityBootstrapError(
            "authority_root_executable_clone_binding_mismatch",
        ));
    }
    Ok(())
}

impl NativeAuthenticatedControllerSourceLease {
    pub(super) fn readback(&self) -> &NativeAuthenticatedControllerSourceReadback {
        &self.readback
    }

    pub(super) fn verify(&self) -> Result<(), AuthorityBootstrapError> {
        verify_authenticated_source_lease(
            &self.file,
            &self.readback.controller_path,
            self.identity,
            self.readback.descriptor,
            &self.security,
            "authority_controller_source_lease_binding_mismatch",
        )
    }

    pub(super) fn file_handle(&self) -> BorrowedHandle<'_> {
        self.file.as_handle()
    }
}

impl NativeAuthenticatedInstallHelperSourceLease {
    pub(super) fn readback(&self) -> &NativeAuthenticatedInstallHelperSourceReadback {
        &self.readback
    }

    pub(super) fn verify(&self) -> Result<(), AuthorityBootstrapError> {
        verify_authenticated_source_lease(
            &self.file,
            &self.readback.install_helper_path,
            self.identity,
            self.readback.descriptor,
            &self.security,
            "authority_install_helper_source_lease_binding_mismatch",
        )
    }
}

fn verify_authenticated_source_lease(
    file: &File,
    expected_path: &Path,
    expected_identity: HeldFileIdentity,
    expected_descriptor: AuthorityPayloadDigest,
    security: &NativeAuthenticatedSourceLeaseSecurity,
    mismatch_code: &'static str,
) -> Result<(), AuthorityBootstrapError> {
    require_exact_read_only_handle(file)?;
    require_exact_handle_path(file, expected_path)?;
    match security {
        NativeAuthenticatedSourceLeaseSecurity::SealedBinary => {
            verify_file_security(file, SEALED_BINARY_FILE_SDDL)?
        }
        #[cfg(test)]
        NativeAuthenticatedSourceLeaseSecurity::TestOnlyUnverified => {}
    }
    let (identity, descriptor, _) = read_held_file(file, MAX_AUTHORITY_BINARY_BYTES, false)?;
    if identity != expected_identity
        || descriptor != expected_descriptor
        || identity.link_count != 1
        || identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
    {
        return Err(AuthorityBootstrapError(mismatch_code));
    }
    Ok(())
}

impl HeldProtectedFile {
    fn descriptor(&self) -> AuthorityPayloadDigest {
        self.descriptor
    }

    fn bytes(&self) -> Option<&[u8]> {
        self.bytes.as_deref()
    }

    fn observation(&self) -> HeldFileObservation {
        HeldFileObservation {
            identity: self.identity,
            descriptor: self.descriptor,
        }
    }

    fn verify_exact_descriptor(
        &mut self,
        expected: AuthorityPayloadDigest,
    ) -> Result<(), AuthorityBootstrapError> {
        self.verify()?;
        if self.descriptor != expected {
            return Err(AuthorityBootstrapError(
                "authority_runtime_source_capability_binding_mismatch",
            ));
        }
        Ok(())
    }

    fn verify(&mut self) -> Result<(), AuthorityBootstrapError> {
        for directory in &self.chain {
            directory.verify()?;
        }
        require_exact_handle_path(&self.file, &self.path)?;
        require_non_inheritable_handle(&self.file)?;
        verify_file_security(&self.file, self.expected_sddl)?;
        let (identity, descriptor, _) = read_held_file(&mut self.file, self.maximum_size, false)?;
        if identity != self.identity || descriptor != self.descriptor {
            return Err(AuthorityBootstrapError(
                "authority_artifact_identity_changed",
            ));
        }
        Ok(())
    }
}

fn require_non_inheritable_handle(file: &File) -> Result<(), AuthorityBootstrapError> {
    let mut flags = 0u32;
    // SAFETY: the File owns a live kernel handle for the duration of this call,
    // and flags points to writable storage for the documented result.
    if unsafe { GetHandleInformation(file.as_raw_handle() as _, &mut flags) } == 0
        || flags & HANDLE_FLAG_INHERIT != 0
    {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_handle_inheritable",
        ));
    }
    Ok(())
}

#[repr(C)]
struct PublicObjectBasicInformation {
    attributes: u32,
    granted_access: u32,
    handle_count: u32,
    pointer_count: u32,
    reserved: [u32; 10],
}

fn query_handle_granted_access(file: &File) -> Result<u32, AuthorityBootstrapError> {
    let mut information = unsafe { zeroed::<PublicObjectBasicInformation>() };
    let mut returned = 0u32;
    let status = unsafe {
        NtQueryObject(
            file.as_raw_handle().cast(),
            ObjectBasicInformation,
            (&mut information as *mut PublicObjectBasicInformation).cast(),
            size_of::<PublicObjectBasicInformation>() as u32,
            &mut returned,
        )
    };
    if status < 0 || returned < (size_of::<u32>() * 2) as u32 {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_handle_access_unavailable",
        ));
    }
    Ok(information.granted_access)
}

fn require_exact_read_only_handle(file: &File) -> Result<(), AuthorityBootstrapError> {
    require_non_inheritable_handle(file)?;
    if query_handle_granted_access(file)? != PROTECTED_EXECUTABLE_READ_ACCESS {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_handle_access_invalid",
        ));
    }
    Ok(())
}

fn reopen_file_object_read_only(source: &File) -> Result<File, AuthorityBootstrapError> {
    let source_identity = held_file_identity(source)?;
    if source_identity.byte_length == 0
        || source_identity.link_count != 1
        || source_identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)
            != 0
    {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_reopen_source_invalid",
        ));
    }
    let raw = unsafe {
        ReOpenFile(
            source.as_raw_handle().cast(),
            PROTECTED_EXECUTABLE_READ_ACCESS,
            FILE_SHARE_READ,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN,
        )
    };
    if raw.is_null() || raw == INVALID_HANDLE_VALUE {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_reopen_failed",
        ));
    }
    // SAFETY: ReOpenFile returned a new owned handle. File closes it on every
    // success and error path after this conversion.
    let reopened = unsafe { File::from_raw_handle(raw as RawHandle) };
    require_exact_read_only_handle(&reopened)?;
    if held_file_identity(&reopened)? != source_identity {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_reopen_identity_mismatch",
        ));
    }
    Ok(reopened)
}

fn reopen_root_executable_pair(
    lifecycle_driver: &File,
    bridge_launcher: &File,
) -> Result<[File; 2], AuthorityBootstrapError> {
    let lifecycle_driver = reopen_file_object_read_only(lifecycle_driver).map_err(|_| {
        AuthorityBootstrapError("authority_lifecycle_driver_capability_clone_failed")
    })?;
    let bridge_launcher = reopen_file_object_read_only(bridge_launcher).map_err(|_| {
        AuthorityBootstrapError("authority_bridge_launcher_capability_clone_failed")
    })?;
    if held_file_identity(&lifecycle_driver)? == held_file_identity(&bridge_launcher)? {
        // Both local Files are closed by RAII before this error escapes.
        return Err(AuthorityBootstrapError(
            "authority_root_executable_capability_alias",
        ));
    }
    Ok([lifecycle_driver, bridge_launcher])
}

fn open_protected_file(
    anchor: &Path,
    expected_path: &Path,
    stable_directory_sddl: &'static str,
    generation_directory_sddl: &'static str,
    file_sddl: &'static str,
    maximum_size: u64,
    retain_bytes: bool,
) -> Result<HeldProtectedFile, AuthorityBootstrapError> {
    if !anchor.is_absolute() || !expected_path.is_absolute() || !path_is_fixed_local(expected_path)
    {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_path_invalid",
        ));
    }
    let relative = expected_path
        .strip_prefix(anchor)
        .map_err(|_| AuthorityBootstrapError("authority_protected_artifact_path_invalid"))?;
    let mut components = relative.components().collect::<Vec<_>>();
    let leaf = match components.pop() {
        Some(Component::Normal(value)) => value.to_os_string(),
        _ => {
            return Err(AuthorityBootstrapError(
                "authority_protected_artifact_path_invalid",
            ))
        }
    };
    if components
        .iter()
        .any(|value| !matches!(value, Component::Normal(_)))
    {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_path_invalid",
        ));
    }
    let mut current = anchor.to_path_buf();
    let mut chain = vec![open_held_directory(&current, None)?];
    for component in components {
        let Component::Normal(value) = component else {
            unreachable!();
        };
        current.push(value);
        chain.push(open_held_directory(
            &current,
            Some(directory_sddl_for_protected_child(
                &current,
                stable_directory_sddl,
                generation_directory_sddl,
            )),
        )?);
    }
    current.push(leaf);
    if !paths_equal(&current, expected_path) {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_path_invalid",
        ));
    }
    let metadata = std::fs::symlink_metadata(&current)
        .map_err(|_| AuthorityBootstrapError("authority_protected_artifact_missing"))?;
    if !metadata.is_file()
        || metadata.file_type().is_symlink()
        || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || metadata.len() == 0
        || metadata.len() > maximum_size
    {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_metadata_invalid",
        ));
    }
    let mut file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN)
        .open(&current)
        .map_err(|_| AuthorityBootstrapError("authority_protected_artifact_open_failed"))?;
    require_exact_read_only_handle(&file)?;
    require_exact_handle_path(&file, &current)?;
    verify_file_security(&file, file_sddl)?;
    let (identity, descriptor, bytes) = read_held_file(&mut file, maximum_size, retain_bytes)?;
    if identity.link_count != 1
        || identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
        || identity.byte_length != metadata.len()
    {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_identity_invalid",
        ));
    }
    Ok(HeldProtectedFile {
        chain,
        file,
        path: current,
        identity,
        descriptor,
        bytes,
        expected_sddl: file_sddl,
        maximum_size,
    })
}

fn directory_sddl_for_protected_child(
    path: &Path,
    stable_sddl: &'static str,
    generation_sddl: &'static str,
) -> &'static str {
    let leaf = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    let parent = path
        .parent()
        .and_then(Path::file_name)
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    let is_digest = leaf.len() == 64
        && leaf
            .as_bytes()
            .iter()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'));
    let is_run_owned = (matches!(parent, "generations" | "maintenance") && is_digest)
        || leaf.strip_prefix("stage.").is_some_and(|digest| {
            digest.len() == 64
                && digest
                    .as_bytes()
                    .iter()
                    .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
        });
    if stable_sddl == STATE_DIRECTORY_SDDL && leaf == "candidate-activation" {
        return CANDIDATE_ACTIVATION_DIRECTORY_SDDL;
    }
    if !is_run_owned {
        return stable_sddl;
    }
    generation_sddl
}

fn open_candidate_credential(
    anchor: &Path,
    expected_path: &Path,
) -> Result<HeldCandidateCredential, AuthorityBootstrapError> {
    if !anchor.is_absolute() || !expected_path.is_absolute() || !path_is_fixed_local(expected_path)
    {
        return Err(AuthorityBootstrapError(
            "authority_candidate_credential_path_invalid",
        ));
    }
    let relative = expected_path
        .strip_prefix(anchor)
        .map_err(|_| AuthorityBootstrapError("authority_candidate_credential_path_invalid"))?;
    let mut components = relative.components().collect::<Vec<_>>();
    let leaf = match components.pop() {
        Some(Component::Normal(value)) => value.to_os_string(),
        _ => {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_path_invalid",
            ))
        }
    };
    if components.len() != 1
        || components
            .iter()
            .any(|value| !matches!(value, Component::Normal(_)))
    {
        return Err(AuthorityBootstrapError(
            "authority_candidate_credential_path_invalid",
        ));
    }
    let mut current = anchor.to_path_buf();
    let mut chain = vec![open_held_directory(&current, None)?];
    for component in components {
        let Component::Normal(value) = component else {
            unreachable!();
        };
        if value != "candidate-activation" {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_path_invalid",
            ));
        }
        current.push(value);
        chain.push(open_held_directory(
            &current,
            Some(CANDIDATE_ACTIVATION_DIRECTORY_SDDL),
        )?);
    }
    current.push(leaf);
    if !paths_equal(&current, expected_path) {
        return Err(AuthorityBootstrapError(
            "authority_candidate_credential_path_invalid",
        ));
    }
    let metadata = std::fs::symlink_metadata(&current).map_err(|_| {
        AuthorityBootstrapError("authority_candidate_credential_metadata_unavailable")
    })?;
    if !metadata.is_file()
        || metadata.file_type().is_symlink()
        || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || metadata.len() == 0
        || metadata.len() > activation::MAX_CANDIDATE_CREDENTIAL_BYTES
    {
        return Err(AuthorityBootstrapError(
            "authority_candidate_credential_metadata_invalid",
        ));
    }
    let mut file = OpenOptions::new()
        .read(true)
        // The producer retains a read-only FILE_SHARE_READ lease through the
        // handshake. This second reader may coexist, while writes and deletes
        // remain denied by both held handles.
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(&current)
        .map_err(|_| AuthorityBootstrapError("authority_candidate_credential_open_failed"))?;
    require_exact_handle_path(&file, &current)?;
    verify_file_security(&file, STATE_FILE_SDDL)?;
    let (identity, descriptor, bytes) =
        read_held_file(&mut file, activation::MAX_CANDIDATE_CREDENTIAL_BYTES, true)?;
    if identity.link_count != 1
        || identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
        || identity.byte_length != metadata.len()
    {
        return Err(AuthorityBootstrapError(
            "authority_candidate_credential_identity_invalid",
        ));
    }
    let record = activation::CandidateCredentialRecord::parse_canonical(
        bytes.as_deref().unwrap_or_default(),
    )?;
    Ok(HeldCandidateCredential {
        chain,
        file,
        path: current,
        identity,
        descriptor,
        record,
    })
}

fn open_held_directory(
    path: &Path,
    expected_sddl: Option<&'static str>,
) -> Result<HeldDirectory, AuthorityBootstrapError> {
    let metadata = std::fs::symlink_metadata(path)
        .map_err(|_| AuthorityBootstrapError("authority_protected_directory_missing"))?;
    if !metadata.is_dir()
        || metadata.file_type().is_symlink()
        || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
    {
        return Err(AuthorityBootstrapError(
            "authority_protected_directory_identity_invalid",
        ));
    }
    let file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(|_| AuthorityBootstrapError("authority_protected_directory_open_failed"))?;
    let identity = held_file_identity(&file)?;
    if identity.attributes & FILE_ATTRIBUTE_DIRECTORY == 0
        || identity.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
    {
        return Err(AuthorityBootstrapError(
            "authority_protected_directory_identity_invalid",
        ));
    }
    require_exact_handle_path(&file, path)?;
    if let Some(expected) = expected_sddl {
        verify_file_security(&file, expected)?;
    }
    Ok(HeldDirectory {
        file,
        path: path.to_path_buf(),
        identity,
        expected_sddl,
    })
}

fn read_held_file(
    file: &File,
    maximum_size: u64,
    retain_bytes: bool,
) -> Result<(HeldFileIdentity, AuthorityPayloadDigest, Option<Vec<u8>>), AuthorityBootstrapError> {
    let before = held_file_identity(file)?;
    if before.byte_length == 0 || before.byte_length > maximum_size {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_size_invalid",
        ));
    }
    // ReOpenFile creates a distinct file object for the exact already-held
    // object. Its cursor cannot race the source or any independently reopened
    // live child capability, and no path is resolved again.
    let mut reader = reopen_file_object_read_only(file)?;
    let reader_before = held_file_identity(&reader)?;
    reader
        .seek(SeekFrom::Start(0))
        .map_err(|_| AuthorityBootstrapError("authority_protected_artifact_read_failed"))?;
    let mut digest = Sha256::new();
    let mut captured = retain_bytes.then(|| Vec::with_capacity(before.byte_length as usize));
    let mut buffer = [0u8; 64 * 1024];
    let mut length = 0u64;
    loop {
        let count = reader
            .read(&mut buffer)
            .map_err(|_| AuthorityBootstrapError("authority_protected_artifact_read_failed"))?;
        if count == 0 {
            break;
        }
        length = length
            .checked_add(count as u64)
            .ok_or(AuthorityBootstrapError(
                "authority_protected_artifact_size_invalid",
            ))?;
        if length > maximum_size {
            return Err(AuthorityBootstrapError(
                "authority_protected_artifact_size_invalid",
            ));
        }
        digest.update(&buffer[..count]);
        if let Some(bytes) = &mut captured {
            bytes.extend_from_slice(&buffer[..count]);
        }
    }
    let reader_after = held_file_identity(&reader)?;
    let after = held_file_identity(file)?;
    if before != after
        || reader_before != before
        || reader_after != before
        || length != before.byte_length
    {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_identity_changed",
        ));
    }
    let descriptor = AuthorityPayloadDigest::new(digest.finalize().into(), length)
        .map_err(|_| AuthorityBootstrapError("authority_protected_artifact_digest_invalid"))?;
    Ok((before, descriptor, captured))
}

fn held_file_identity(file: &File) -> Result<HeldFileIdentity, AuthorityBootstrapError> {
    let mut information = unsafe { std::mem::zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle().cast(), &mut information) } == 0
        || information.dwVolumeSerialNumber == 0
        || (information.nFileIndexHigh == 0 && information.nFileIndexLow == 0)
    {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_identity_unavailable",
        ));
    }
    Ok(HeldFileIdentity {
        volume_serial: information.dwVolumeSerialNumber,
        file_id: (u64::from(information.nFileIndexHigh) << 32)
            | u64::from(information.nFileIndexLow),
        byte_length: (u64::from(information.nFileSizeHigh) << 32)
            | u64::from(information.nFileSizeLow),
        link_count: information.nNumberOfLinks,
        attributes: information.dwFileAttributes,
    })
}

fn require_exact_handle_path(file: &File, expected: &Path) -> Result<(), AuthorityBootstrapError> {
    let mut words = vec![0u16; 32_768];
    let length = unsafe {
        GetFinalPathNameByHandleW(
            file.as_raw_handle().cast(),
            words.as_mut_ptr(),
            words.len() as u32,
            0,
        )
    } as usize;
    if length == 0 || length >= words.len() {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_path_readback_failed",
        ));
    }
    words.truncate(length);
    if words.contains(&0) {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_path_readback_failed",
        ));
    }
    let actual = OsString::from_wide(&words).to_string_lossy().into_owned();
    let actual = actual
        .strip_prefix(r"\\?\UNC\")
        .map(|value| format!(r"\\{value}"))
        .or_else(|| actual.strip_prefix(r"\\?\").map(str::to_string))
        .unwrap_or(actual);
    if !actual.eq_ignore_ascii_case(expected.to_string_lossy().as_ref()) {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_path_mismatch",
        ));
    }
    Ok(())
}

fn path_is_fixed_local(path: &Path) -> bool {
    let encoded = wide_path(path);
    let mut root = [0u16; 32_768];
    unsafe {
        GetVolumePathNameW(encoded.as_ptr(), root.as_mut_ptr(), root.len() as u32) != 0
            && GetDriveTypeW(root.as_ptr()) == DRIVE_FIXED_TYPE
    }
}

fn paths_equal(left: &Path, right: &Path) -> bool {
    left.to_string_lossy()
        .eq_ignore_ascii_case(right.to_string_lossy().as_ref())
}

fn activation_directory_names(path: &Path) -> Result<Vec<String>, AuthorityBootstrapError> {
    let mut names = std::fs::read_dir(path)
        .map_err(|_| AuthorityBootstrapError("authority_activation_directory_unavailable"))?
        .take(MAX_BOOTSTRAP_ACTIVATION_EPOCH as usize + 2)
        .map(|entry| {
            let entry = entry.map_err(|_| {
                AuthorityBootstrapError("authority_activation_directory_unavailable")
            })?;
            let metadata = std::fs::symlink_metadata(entry.path()).map_err(|_| {
                AuthorityBootstrapError("authority_activation_directory_unavailable")
            })?;
            if !metadata.is_file()
                || metadata.file_type().is_symlink()
                || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
            {
                return Err(AuthorityBootstrapError(
                    "authority_activation_directory_not_unique",
                ));
            }
            entry
                .file_name()
                .into_string()
                .map_err(|_| AuthorityBootstrapError("authority_activation_name_invalid"))
        })
        .collect::<Result<Vec<_>, _>>()?;
    if names.len() > MAX_BOOTSTRAP_ACTIVATION_EPOCH as usize + 1 {
        return Err(AuthorityBootstrapError(
            "authority_activation_directory_not_unique",
        ));
    }
    names.sort();
    Ok(names)
}

fn current_process_image_path() -> Result<PathBuf, AuthorityBootstrapError> {
    let mut words = vec![0u16; 32_768];
    let mut length = words.len() as u32;
    if unsafe {
        QueryFullProcessImageNameW(GetCurrentProcess(), 0, words.as_mut_ptr(), &mut length)
    } == 0
        || length == 0
        || length as usize >= words.len()
    {
        return Err(AuthorityBootstrapError(
            "authority_service_image_path_unavailable",
        ));
    }
    words.truncate(length as usize);
    if words.contains(&0) {
        return Err(AuthorityBootstrapError(
            "authority_service_image_path_unavailable",
        ));
    }
    Ok(PathBuf::from(OsString::from_wide(&words)))
}

fn current_process_identity() -> Result<(u32, u64), AuthorityBootstrapError> {
    let process_id = unsafe { GetCurrentProcessId() };
    let mut creation = unsafe { std::mem::zeroed() };
    let mut exit = unsafe { std::mem::zeroed() };
    let mut kernel = unsafe { std::mem::zeroed() };
    let mut user = unsafe { std::mem::zeroed() };
    if process_id == 0
        || unsafe {
            GetProcessTimes(
                GetCurrentProcess(),
                &mut creation,
                &mut exit,
                &mut kernel,
                &mut user,
            )
        } == 0
    {
        return Err(AuthorityBootstrapError(
            "authority_service_process_identity_unavailable",
        ));
    }
    let creation_time =
        (u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime);
    if creation_time == 0 {
        return Err(AuthorityBootstrapError(
            "authority_service_process_identity_unavailable",
        ));
    }
    Ok((process_id, creation_time))
}

struct HeldServiceSecurity {
    _manager: ServiceHandle,
    service: ServiceHandle,
}

impl HeldServiceSecurity {
    fn open() -> Result<Self, AuthorityBootstrapError> {
        let manager =
            ServiceHandle(unsafe { OpenSCManagerW(ptr::null(), ptr::null(), SC_MANAGER_CONNECT) });
        if manager.0.is_null() {
            return Err(AuthorityBootstrapError(
                "authority_scm_readback_unavailable",
            ));
        }
        let service_name = wide_text(AUTHORITY_SERVICE_NAME);
        let service = ServiceHandle(unsafe {
            OpenServiceW(
                manager.0,
                service_name.as_ptr(),
                SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS | READ_CONTROL_ACCESS,
            )
        });
        if service.0.is_null() {
            return Err(AuthorityBootstrapError(
                if unsafe { GetLastError() } == ERROR_SERVICE_DOES_NOT_EXIST {
                    "authority_service_not_installed"
                } else {
                    "authority_service_readback_denied"
                },
            ));
        }
        Ok(Self {
            _manager: manager,
            service,
        })
    }

    fn verify(&self) -> Result<(), AuthorityBootstrapError> {
        let actual = service_security_sddl(self.service.0)?;
        let expected = projected_sddl(SERVICE_SECURITY_SDDL)?;
        if actual != expected {
            return Err(AuthorityBootstrapError(
                "authority_service_security_mismatch",
            ));
        }
        Ok(())
    }
}

struct ServiceHandle(SC_HANDLE);

// SCM handles are process-wide kernel handles. This wrapper owns the sole
// CloseServiceHandle responsibility and exposes no shared mutation, so moving
// it with the runtime boundary to its service thread preserves that ownership.
unsafe impl Send for ServiceHandle {}

impl Drop for ServiceHandle {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { CloseServiceHandle(self.0) };
        }
    }
}

fn service_security_sddl(service: SC_HANDLE) -> Result<String, AuthorityBootstrapError> {
    let mut required = 0u32;
    unsafe {
        QueryServiceObjectSecurity(
            service,
            SECURITY_INFORMATION,
            ptr::null_mut(),
            0,
            &mut required,
        );
    }
    if required == 0 || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER {
        return Err(AuthorityBootstrapError(
            "authority_service_security_readback_failed",
        ));
    }
    let mut buffer = AlignedBuffer::new(required)?;
    if unsafe {
        QueryServiceObjectSecurity(
            service,
            SECURITY_INFORMATION,
            buffer.as_mut_u8().cast(),
            required,
            &mut required,
        )
    } == 0
    {
        return Err(AuthorityBootstrapError(
            "authority_service_security_readback_failed",
        ));
    }
    descriptor_sddl(buffer.as_mut_u8().cast())
}

struct AlignedBuffer {
    words: Vec<usize>,
}

impl AlignedBuffer {
    fn new(byte_length: u32) -> Result<Self, AuthorityBootstrapError> {
        let byte_length = usize::try_from(byte_length)
            .map_err(|_| AuthorityBootstrapError("authority_security_readback_too_large"))?;
        if byte_length == 0 || byte_length > 1024 * 1024 {
            return Err(AuthorityBootstrapError(
                "authority_security_readback_too_large",
            ));
        }
        let word_size = std::mem::size_of::<usize>();
        let count = byte_length
            .checked_add(word_size - 1)
            .ok_or(AuthorityBootstrapError(
                "authority_security_readback_too_large",
            ))?
            / word_size;
        Ok(Self {
            words: vec![0usize; count],
        })
    }

    fn as_mut_u8(&mut self) -> *mut u8 {
        self.words.as_mut_ptr().cast()
    }
}

fn verify_file_security(file: &File, expected: &str) -> Result<(), AuthorityBootstrapError> {
    if file_security_sddl(file)? != projected_sddl(expected)? {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_security_mismatch",
        ));
    }
    Ok(())
}

fn file_security_sddl(file: &File) -> Result<String, AuthorityBootstrapError> {
    let mut actual = ptr::null_mut();
    let status = unsafe {
        GetSecurityInfo(
            file.as_raw_handle().cast(),
            SE_FILE_OBJECT,
            SECURITY_INFORMATION,
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            &mut actual,
        )
    };
    if status != 0 || actual.is_null() {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_security_readback_failed",
        ));
    }
    let actual = OwnedSecurityDescriptor(actual);
    descriptor_sddl(actual.0)
}

#[cfg(test)]
fn file_discretionary_security_bytes(file: &File) -> Result<Vec<u8>, AuthorityBootstrapError> {
    let projection =
        OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION;
    let mut actual = ptr::null_mut();
    let status = unsafe {
        GetSecurityInfo(
            file.as_raw_handle().cast(),
            SE_FILE_OBJECT,
            projection,
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            &mut actual,
        )
    };
    if status != 0 || actual.is_null() {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_security_readback_failed",
        ));
    }
    let actual = OwnedSecurityDescriptor(actual);
    let length = unsafe { GetSecurityDescriptorLength(actual.0) };
    if length == 0 || length > 64 * 1024 {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_security_readback_failed",
        ));
    }
    Ok(unsafe { std::slice::from_raw_parts(actual.0.cast::<u8>(), length as usize) }.to_vec())
}

fn projected_sddl(value: &str) -> Result<String, AuthorityBootstrapError> {
    let words = value
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let mut descriptor = ptr::null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            words.as_ptr(),
            SDDL_REVISION_1,
            &mut descriptor,
            ptr::null_mut(),
        )
    } == 0
        || descriptor.is_null()
    {
        return Err(AuthorityBootstrapError(
            "authority_security_descriptor_invalid",
        ));
    }
    descriptor_sddl(OwnedSecurityDescriptor(descriptor).0)
}

struct OwnedSecurityDescriptor(PSECURITY_DESCRIPTOR);

impl Drop for OwnedSecurityDescriptor {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { LocalFree(self.0.cast()) };
        }
    }
}

fn descriptor_sddl(descriptor: PSECURITY_DESCRIPTOR) -> Result<String, AuthorityBootstrapError> {
    descriptor_sddl_with_projection(descriptor, SECURITY_INFORMATION)
}

fn descriptor_sddl_with_projection(
    descriptor: PSECURITY_DESCRIPTOR,
    projection: u32,
) -> Result<String, AuthorityBootstrapError> {
    let mut text = ptr::null_mut::<u16>();
    let mut length = 0u32;
    if unsafe {
        ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            SDDL_REVISION_1,
            projection,
            &mut text,
            &mut length,
        )
    } == 0
        || text.is_null()
        || length == 0
    {
        if !text.is_null() {
            unsafe { LocalFree(text.cast()) };
        }
        return Err(AuthorityBootstrapError(
            "authority_security_descriptor_readback_failed",
        ));
    }
    let mut words = unsafe { std::slice::from_raw_parts(text, length as usize) }.to_vec();
    unsafe { LocalFree(text.cast()) };
    if words.last() == Some(&0) {
        words.pop();
    }
    if words.is_empty() || words.contains(&0) {
        return Err(AuthorityBootstrapError(
            "authority_security_descriptor_readback_failed",
        ));
    }
    String::from_utf16(&words)
        .map_err(|_| AuthorityBootstrapError("authority_security_descriptor_readback_failed"))
}

fn wide_path(path: &Path) -> Vec<u16> {
    path.as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

fn wide_text(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    struct TemporaryRoot(PathBuf);

    impl Drop for TemporaryRoot {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn temporary_root(label: &str) -> TemporaryRoot {
        use std::sync::atomic::{AtomicU64, Ordering};

        static NEXT_TEMP_ROOT: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "vrcforge-bootstrap-{label}-{}-{}",
            std::process::id(),
            NEXT_TEMP_ROOT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&path).unwrap();
        TemporaryRoot(path)
    }

    fn receipt(
        terminal: MaintenanceJournalTerminalKind,
        generation: [u8; 32],
        plan: [u8; 32],
        transaction: [u8; 32],
        epoch: u64,
    ) -> MaintenanceJournalTerminalReceipt {
        MaintenanceJournalTerminalReceipt::new(
            generation,
            plan,
            transaction,
            epoch,
            terminal,
            7,
            [0x44; 32],
            [0x55; 32],
        )
        .unwrap()
    }

    #[test]
    fn bootstrap_accepts_only_exact_committed_terminal_binding() {
        let generation = [0x11; 32];
        let plan = [0x22; 32];
        let transaction = [0x33; 32];
        let committed = receipt(
            MaintenanceJournalTerminalKind::Committed,
            generation,
            plan,
            transaction,
            1,
        );
        assert_eq!(
            validate_committed_terminal_receipt(&committed, generation, plan, transaction, 1)
                .unwrap(),
            AuthorityBootstrapTerminalBinding {
                generation,
                plan_sha256: plan,
                transaction_sha256: transaction,
                activation_epoch: 1,
            }
        );

        for terminal in [
            MaintenanceJournalTerminalKind::RolledBack,
            MaintenanceJournalTerminalKind::Contained,
        ] {
            assert_eq!(
                validate_committed_terminal_receipt(
                    &receipt(terminal, generation, plan, transaction, 1),
                    generation,
                    plan,
                    transaction,
                    1,
                )
                .unwrap_err()
                .code(),
                "authority_maintenance_journal_not_terminal"
            );
        }

        for (actual_generation, actual_plan, actual_transaction, actual_epoch) in [
            ([0x12; 32], plan, transaction, 1),
            (generation, [0x23; 32], transaction, 1),
            (generation, plan, [0x34; 32], 1),
            (generation, plan, transaction, 2),
        ] {
            let drifted = receipt(
                MaintenanceJournalTerminalKind::Committed,
                actual_generation,
                actual_plan,
                actual_transaction,
                actual_epoch,
            );
            assert_eq!(
                validate_committed_terminal_receipt(&drifted, generation, plan, transaction, 1,)
                    .unwrap_err()
                    .code(),
                "authority_maintenance_journal_not_terminal"
            );
        }
    }

    #[test]
    fn protected_chain_uses_run_owned_acl_only_at_exact_child_boundaries() {
        let digest = "ab".repeat(32);
        assert_eq!(
            directory_sddl_for_protected_child(
                &PathBuf::from(format!(r"C:\root\generations\{digest}")),
                BINARY_DIRECTORY_SDDL,
                BINARY_GENERATION_DIRECTORY_SDDL,
            ),
            BINARY_GENERATION_DIRECTORY_SDDL
        );
        assert_eq!(
            directory_sddl_for_protected_child(
                &PathBuf::from(format!(r"C:\root\generations\{digest}")),
                BINARY_DIRECTORY_SDDL,
                SEALED_GENERATION_DIRECTORY_SDDL,
            ),
            SEALED_GENERATION_DIRECTORY_SDDL
        );
        assert_ne!(
            BINARY_GENERATION_DIRECTORY_SDDL,
            SEALED_GENERATION_DIRECTORY_SDDL
        );
        assert_ne!(BINARY_FILE_SDDL, SEALED_BINARY_FILE_SDDL);
        assert_ne!(STATE_FILE_SDDL, IMMUTABLE_STATE_FILE_SDDL);
        assert_eq!(
            directory_sddl_for_protected_child(
                &PathBuf::from(format!(r"C:\root\maintenance\{digest}")),
                STATE_DIRECTORY_SDDL,
                STATE_GENERATION_DIRECTORY_SDDL,
            ),
            STATE_GENERATION_DIRECTORY_SDDL
        );
        assert_eq!(
            directory_sddl_for_protected_child(
                &PathBuf::from(format!(r"C:\root\maintenance\{digest}\stage.{digest}")),
                STATE_DIRECTORY_SDDL,
                STATE_GENERATION_DIRECTORY_SDDL,
            ),
            STATE_GENERATION_DIRECTORY_SDDL
        );
        assert_eq!(
            directory_sddl_for_protected_child(
                &PathBuf::from(r"C:\root\candidate-activation"),
                STATE_DIRECTORY_SDDL,
                STATE_GENERATION_DIRECTORY_SDDL,
            ),
            CANDIDATE_ACTIVATION_DIRECTORY_SDDL
        );
        for stable in [
            PathBuf::from(r"C:\root\generations"),
            PathBuf::from(r"C:\root\activations"),
            PathBuf::from(r"C:\root\maintenance"),
            PathBuf::from(r"C:\root\generations\not-a-digest"),
        ] {
            assert_eq!(
                directory_sddl_for_protected_child(
                    &stable,
                    STATE_DIRECTORY_SDDL,
                    STATE_GENERATION_DIRECTORY_SDDL,
                ),
                STATE_DIRECTORY_SDDL
            );
        }
    }

    #[test]
    fn protected_root_executable_pair_is_generation_leaf_and_order_bound() {
        let generation = [0xab; 32];
        let generation_leaf = hex_lower(&generation);
        let root = PathBuf::from(format!(r"C:\protected\generations\{generation_leaf}"));
        let lifecycle = root.join("vrcforge_primitive_lifecycle_driver.exe");
        let bridge = root.join("vrcforge_primitive_bridge_launcher.exe");
        validate_root_executable_paths(generation, generation, &lifecycle, &bridge).unwrap();

        for (observed_generation, lifecycle_path, bridge_path) in [
            ([0xac; 32], lifecycle.clone(), bridge.clone()),
            (generation, root.join("driver.exe"), bridge.clone()),
            (generation, lifecycle.clone(), root.join("launcher.exe")),
            (
                generation,
                lifecycle.clone(),
                PathBuf::from(r"C:\protected\other").join("vrcforge_primitive_bridge_launcher.exe"),
            ),
        ] {
            assert_eq!(
                validate_root_executable_paths(
                    generation,
                    observed_generation,
                    &lifecycle_path,
                    &bridge_path,
                )
                .unwrap_err()
                .code(),
                "authority_root_executable_capability_path_invalid"
            );
        }
    }

    fn controller_source_lease_for_test(
        path: &Path,
        source: &File,
    ) -> NativeAuthenticatedControllerSourceLease {
        let identity = held_file_identity(source).unwrap();
        let (_, descriptor, _) = read_held_file(source, 1024, false).unwrap();
        NativeAuthenticatedControllerSourceLease {
            readback: NativeAuthenticatedControllerSourceReadback {
                controller_path: path.to_path_buf(),
                descriptor,
                volume_serial: identity.volume_serial,
                file_id: identity.file_id,
                link_count: identity.link_count,
            },
            identity,
            file: reopen_file_object_read_only(source).unwrap(),
            security: NativeAuthenticatedSourceLeaseSecurity::TestOnlyUnverified,
        }
    }

    fn install_helper_source_lease_for_test(
        path: &Path,
        source: &File,
    ) -> NativeAuthenticatedInstallHelperSourceLease {
        let identity = held_file_identity(source).unwrap();
        let (_, descriptor, _) = read_held_file(source, 1024, false).unwrap();
        NativeAuthenticatedInstallHelperSourceLease {
            readback: NativeAuthenticatedInstallHelperSourceReadback {
                install_helper_path: path.to_path_buf(),
                descriptor,
                volume_serial: identity.volume_serial,
                file_id: identity.file_id,
                link_count: identity.link_count,
            },
            identity,
            file: reopen_file_object_read_only(source).unwrap(),
            security: NativeAuthenticatedSourceLeaseSecurity::TestOnlyUnverified,
        }
    }

    #[test]
    fn authenticated_source_leases_survive_bootstrap_owner_drop_and_revalidate() {
        let root = temporary_root("authenticated-source-lease-owner-drop");
        let controller_path = root.0.join("controller.exe");
        let helper_path = root.0.join("helper.exe");
        std::fs::write(&controller_path, b"held-controller-source").unwrap();
        std::fs::write(&helper_path, b"held-helper-source").unwrap();
        let controller_owner = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&controller_path)
            .unwrap();
        let helper_owner = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&helper_path)
            .unwrap();
        let controller_lease =
            controller_source_lease_for_test(&controller_path, &controller_owner);
        let helper_lease = install_helper_source_lease_for_test(&helper_path, &helper_owner);
        drop(controller_owner);
        drop(helper_owner);

        controller_lease.verify().unwrap();
        controller_lease.verify().unwrap();
        helper_lease.verify().unwrap();
        helper_lease.verify().unwrap();
        assert!(OpenOptions::new()
            .write(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&controller_path)
            .is_err());
        assert!(OpenOptions::new()
            .write(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&helper_path)
            .is_err());

        drop(controller_lease);
        drop(helper_lease);
        OpenOptions::new()
            .write(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&controller_path)
            .unwrap();
        OpenOptions::new()
            .write(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&helper_path)
            .unwrap();
    }

    #[test]
    fn same_bytes_path_open_cannot_forge_an_authenticated_source_lease() {
        let root = temporary_root("authenticated-source-lease-forgery");
        let original_path = root.0.join("controller-original.exe");
        let replacement_path = root.0.join("controller-replacement.exe");
        let bytes = b"same-controller-source-bytes";
        std::fs::write(&original_path, bytes).unwrap();
        std::fs::write(&replacement_path, bytes).unwrap();
        let original = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&original_path)
            .unwrap();
        let replacement = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&replacement_path)
            .unwrap();
        let original_identity = held_file_identity(&original).unwrap();
        let (_, original_descriptor, _) = read_held_file(&original, 1024, false).unwrap();
        let forged = NativeAuthenticatedControllerSourceLease {
            readback: NativeAuthenticatedControllerSourceReadback {
                controller_path: original_path,
                descriptor: original_descriptor,
                volume_serial: original_identity.volume_serial,
                file_id: original_identity.file_id,
                link_count: original_identity.link_count,
            },
            identity: original_identity,
            file: reopen_file_object_read_only(&replacement).unwrap(),
            security: NativeAuthenticatedSourceLeaseSecurity::TestOnlyUnverified,
        };
        assert!(forged.verify().is_err());

        let mut drifted = controller_source_lease_for_test(&replacement_path, &replacement);
        drifted.identity.file_id ^= 1;
        assert_eq!(
            drifted.verify().unwrap_err().code(),
            "authority_controller_source_lease_binding_mismatch"
        );
    }

    #[test]
    fn reopened_file_objects_keep_source_and_pair_cursors_independent() {
        use std::io::{Seek, SeekFrom};

        let root = temporary_root("held-file-cursor");
        let path = root.0.join("protected-executable.bin");
        std::fs::write(&path, b"generation-bound-protected-executable").unwrap();
        let mut original = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&path)
            .unwrap();
        original.seek(SeekFrom::Start(7)).unwrap();
        let mut lifecycle_driver = reopen_file_object_read_only(&original).unwrap();
        let mut bridge_launcher = reopen_file_object_read_only(&original).unwrap();
        assert_eq!(original.stream_position().unwrap(), 7);
        assert_eq!(lifecycle_driver.stream_position().unwrap(), 0);
        assert_eq!(bridge_launcher.stream_position().unwrap(), 0);

        lifecycle_driver.seek(SeekFrom::Start(3)).unwrap();
        bridge_launcher.seek(SeekFrom::Start(11)).unwrap();
        assert_eq!(original.stream_position().unwrap(), 7);
        assert_eq!(lifecycle_driver.stream_position().unwrap(), 3);
        assert_eq!(bridge_launcher.stream_position().unwrap(), 11);

        let source_identity = held_file_identity(&original).unwrap();
        assert_eq!(
            held_file_identity(&lifecycle_driver).unwrap(),
            source_identity
        );
        assert_eq!(
            held_file_identity(&bridge_launcher).unwrap(),
            source_identity
        );
        assert_eq!(source_identity.link_count, 1);
        assert_eq!(
            source_identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT),
            0
        );
        require_exact_handle_path(&lifecycle_driver, &path).unwrap();
        require_exact_handle_path(&bridge_launcher, &path).unwrap();
        assert_eq!(
            query_handle_granted_access(&lifecycle_driver).unwrap(),
            PROTECTED_EXECUTABLE_READ_ACCESS
        );
        assert_eq!(
            query_handle_granted_access(&bridge_launcher).unwrap(),
            PROTECTED_EXECUTABLE_READ_ACCESS
        );
        require_non_inheritable_handle(&lifecycle_driver).unwrap();
        require_non_inheritable_handle(&bridge_launcher).unwrap();
        let source_security = file_discretionary_security_bytes(&original).unwrap();
        assert_eq!(
            file_discretionary_security_bytes(&lifecycle_driver).unwrap(),
            source_security
        );
        assert_eq!(
            file_discretionary_security_bytes(&bridge_launcher).unwrap(),
            source_security
        );

        let expected_bytes = b"generation-bound-protected-executable";
        let expected_sha256: [u8; 32] = Sha256::digest(expected_bytes).into();
        let (_, descriptor, captured) = read_held_file(&mut lifecycle_driver, 1024, true).unwrap();
        assert_eq!(descriptor.sha256(), &expected_sha256);
        assert_eq!(descriptor.byte_length(), expected_bytes.len() as u64);
        assert_eq!(captured.as_deref(), Some(expected_bytes.as_slice()));
        assert_eq!(original.stream_position().unwrap(), 7);
        assert_eq!(lifecycle_driver.stream_position().unwrap(), 3);
        assert_eq!(bridge_launcher.stream_position().unwrap(), 11);

        original.seek(SeekFrom::End(-3)).unwrap();
        let source_position = original.stream_position().unwrap();
        let (_, descriptor, captured) = read_held_file(&mut original, 1024, false).unwrap();
        assert_eq!(descriptor.sha256(), &expected_sha256);
        assert!(captured.is_none());
        assert_eq!(original.stream_position().unwrap(), source_position);
        assert_eq!(lifecycle_driver.stream_position().unwrap(), 3);
        assert_eq!(bridge_launcher.stream_position().unwrap(), 11);
    }

    #[test]
    fn failed_pair_validation_closes_both_reopened_files() {
        let root = temporary_root("pair-close");
        let path = root.0.join("protected-executable.bin");
        std::fs::write(&path, b"pair-close-proof").unwrap();
        let original = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&path)
            .unwrap();

        assert_eq!(
            reopen_root_executable_pair(&original, &original)
                .unwrap_err()
                .code(),
            "authority_root_executable_capability_alias"
        );

        // The two read-only, FILE_SHARE_READ-only reopens would block this
        // writer if either escaped the error path.
        OpenOptions::new()
            .write(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&path)
            .unwrap();
    }

    #[test]
    fn same_bytes_replacement_object_cannot_satisfy_the_sealed_runner_identity() {
        let root = temporary_root("runner-policy-replacement");
        let original_path = root.0.join("runner-policy-original.json");
        let replacement_path = root.0.join("runner-policy-replacement.json");
        let bytes = b"same-canonical-runner-policy-bytes";
        std::fs::write(&original_path, bytes).unwrap();
        std::fs::write(&replacement_path, bytes).unwrap();

        let original = OpenOptions::new().read(true).open(&original_path).unwrap();
        let replacement = OpenOptions::new()
            .read(true)
            .open(&replacement_path)
            .unwrap();
        let original_identity = held_file_identity(&original).unwrap();
        let replacement_identity = held_file_identity(&replacement).unwrap();
        assert_eq!(
            original_identity.byte_length,
            replacement_identity.byte_length
        );
        assert_ne!(original_identity.file_id, replacement_identity.file_id);

        let sealed = runner_policy_sealed_identity_from_held(original_identity).unwrap();
        assert_eq!(
            verify_runner_policy_sealed_identity(replacement_identity, sealed)
                .unwrap_err()
                .code(),
            "authority_runner_policy_sealed_identity_mismatch"
        );
        verify_runner_policy_sealed_identity(original_identity, sealed).unwrap();
    }

    #[test]
    fn bootstrap_security_projection_includes_the_label_without_full_sacl_access() {
        assert_eq!(
            SECURITY_INFORMATION,
            OWNER_SECURITY_INFORMATION
                | GROUP_SECURITY_INFORMATION
                | DACL_SECURITY_INFORMATION
                | LABEL_SECURITY_INFORMATION
        );
        assert_ne!(SECURITY_INFORMATION & LABEL_SECURITY_INFORMATION, 0);
        assert_eq!(SECURITY_INFORMATION & SACL_SECURITY_INFORMATION, 0);
    }

    #[test]
    fn candidate_and_committed_sources_are_disjoint_types() {
        let source = include_str!("bootstrap_windows.rs");
        let production = source
            .rsplit_once("\n#[cfg(test)]\nmod tests {")
            .map(|(production, _)| production)
            .expect("production source before the test module");
        assert!(production.contains("struct NativeCommittedRuntimeBootstrapSource {"));
        assert!(production.contains("struct NativeCandidateValidationBootstrapSource {"));
        assert!(production.contains(
            "impl InstalledServiceBootstrapSource for NativeCommittedRuntimeBootstrapSource"
        ));
        assert!(production.contains(
            "impl CandidateValidationBootstrapSource for NativeCandidateValidationBootstrapSource"
        ));
        assert!(!production.contains(
            "impl InstalledServiceBootstrapSource for NativeCandidateValidationBootstrapSource"
        ));
        let committed = production
            .split("struct NativeCommittedRuntimeBootstrapSource {")
            .nth(1)
            .and_then(|value| {
                value
                    .split("struct NativeCandidateValidationBootstrapSource {")
                    .next()
            })
            .expect("committed source shape");
        assert!(committed
            .contains("published_runtime_binding: VerifiedPublishedRuntimeBindingProjection"));
        assert!(committed.contains("published_final_commit_receipt_sha256: [u8; 32]"));
        assert!(!committed.contains("Option<VerifiedPublishedRuntimeBindingProjection>"));
    }

    #[test]
    fn runner_policy_capability_uses_dedicated_errors_and_consuming_readback() {
        let source = include_str!("bootstrap_windows.rs");
        let take_surface = source
            .split("pub(super) fn take_authenticated_runner_policy")
            .nth(1)
            .unwrap()
            .split("pub(super) fn take_authenticated_root_executables")
            .next()
            .unwrap();
        let read_surface = source
            .split("fn verify_runner_policy_held_file")
            .nth(1)
            .unwrap()
            .split("impl NativeAuthenticatedProtectedRootExecutablesCapability")
            .next()
            .unwrap();
        assert!(take_surface.contains(".runner_policy_state"));
        assert!(take_surface.contains(".take()"));
        assert!(read_surface.contains("pub(super) fn read_once("));
        assert!(read_surface.contains("mut self"));
        assert!(!take_surface.contains("authority_runtime_source"));
        assert!(!read_surface.contains("authority_runtime_source"));
    }

    #[test]
    fn current_key_selection_requires_one_exact_generation_and_signer() {
        let bindings = [([0x11; 32], [0x21; 32]), ([0x12; 32], [0x22; 32])];
        assert_eq!(
            exact_current_key_position(
                bindings.len(),
                |index| bindings[index],
                [0x12; 32],
                [0x22; 32],
            )
            .unwrap(),
            1
        );
        assert_eq!(
            exact_current_key_position(
                bindings.len(),
                |index| bindings[index],
                [0x13; 32],
                [0x22; 32],
            )
            .unwrap_err()
            .code(),
            "authority_current_key_generation_mismatch"
        );
        assert_eq!(
            exact_current_key_position(
                bindings.len(),
                |index| bindings[index],
                [0x12; 32],
                [0x23; 32],
            )
            .unwrap_err()
            .code(),
            "authority_current_key_signer_mismatch"
        );
        let duplicate = [([0x31; 32], [0x41; 32]); 2];
        assert_eq!(
            exact_current_key_position(
                duplicate.len(),
                |index| duplicate[index],
                [0x31; 32],
                [0x41; 32],
            )
            .unwrap_err()
            .code(),
            "authority_current_key_binding_ambiguous"
        );
    }
}
