use super::finalizer_commit_protocol::RunnerPolicySealedIdentity;
use super::runner_policy::{CanonicalRunnerPolicyState, RunnerPolicyStateDescriptor};
use super::*;
use crate::primitive_evidence_authority_key::VerifiedAuthorityKeyReadback;
use std::{
    collections::BTreeSet,
    fmt,
    path::{Path, PathBuf},
};

#[cfg(windows)]
use super::finalizer_commit_store_windows::{
    RestrictedFinalizerCommitsParentRoot, VerifiedPublishedRuntimeBindingProjection,
};
#[cfg(windows)]
use crate::primitive_evidence_authority_ledger::AuthenticatedPublishedAuthorityLedger;

#[cfg(windows)]
use super::candidate_pipe::CandidatePeerEvidence;

use super::bootstrap_activation as activation;
#[allow(unused_imports)]
pub(crate) use activation::{
    candidate_pipe_name, CandidateImageEvidence, CandidateProcessEvidence,
    CandidateResponseExpectation, CandidateServicePeerObservation, CandidateServiceStartLocator,
    CandidateValidationHandshake, CandidateValidationRequest, UntrustedCandidateValidationResponse,
    VerifiedCandidateValidationReceipt, CANDIDATE_HANDSHAKE_WINDOW_MILLIS,
    MAX_CANDIDATE_HANDSHAKE_BYTES,
};
use activation::{
    prepare_candidate_activation_from_readback, CandidateActivationObservation,
    CandidateCredentialConsumer, CandidateCredentialReadback, PreparedCandidateValidation,
};

pub(crate) const SERVICE_BOOTSTRAP_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_service_bootstrap.v4";
const MAX_BOOTSTRAP_ACTIVATION_EPOCH: u64 = 1024;
#[cfg(windows)]
const AUTHENTICATED_LEDGER_READBACK_DOMAIN: &[u8] = b"vrcforge-authenticated-ledger-readback-v2\0";
#[cfg(windows)]
const AUTHENTICATED_CONTROLLER_SOURCE_BINDING_DOMAIN: &[u8] =
    b"vrcforge-authenticated-controller-source-binding-v2\0";
#[cfg(windows)]
const AUTHENTICATED_INSTALL_HELPER_SOURCE_BINDING_DOMAIN: &[u8] =
    b"vrcforge-authenticated-install-helper-source-binding-v1\0";

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AuthorityBootstrapError(pub(super) &'static str);

impl AuthorityBootstrapError {
    pub(crate) fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for AuthorityBootstrapError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for AuthorityBootstrapError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) enum BootstrapArtifactKind {
    ActiveHead,
    TrustManifest,
    ActivationManifest,
    ServiceExecutable,
    ControllerExecutable,
    InstallHelperExecutable,
    LifecycleDriverExecutable,
    BridgeLauncherExecutable,
    RuntimeSourceManifest,
    RunnerPolicyState,
    Ledger,
    LedgerAnchor,
}

const REQUIRED_ARTIFACTS: [BootstrapArtifactKind; 12] = [
    BootstrapArtifactKind::ActiveHead,
    BootstrapArtifactKind::TrustManifest,
    BootstrapArtifactKind::ActivationManifest,
    BootstrapArtifactKind::ServiceExecutable,
    BootstrapArtifactKind::ControllerExecutable,
    BootstrapArtifactKind::InstallHelperExecutable,
    BootstrapArtifactKind::LifecycleDriverExecutable,
    BootstrapArtifactKind::BridgeLauncherExecutable,
    BootstrapArtifactKind::RuntimeSourceManifest,
    BootstrapArtifactKind::RunnerPolicyState,
    BootstrapArtifactKind::Ledger,
    BootstrapArtifactKind::LedgerAnchor,
];

const CANDIDATE_REQUIRED_ARTIFACTS: [BootstrapArtifactKind; 10] = [
    BootstrapArtifactKind::TrustManifest,
    BootstrapArtifactKind::ActivationManifest,
    BootstrapArtifactKind::ServiceExecutable,
    BootstrapArtifactKind::ControllerExecutable,
    BootstrapArtifactKind::InstallHelperExecutable,
    BootstrapArtifactKind::LifecycleDriverExecutable,
    BootstrapArtifactKind::BridgeLauncherExecutable,
    BootstrapArtifactKind::RuntimeSourceManifest,
    BootstrapArtifactKind::Ledger,
    BootstrapArtifactKind::LedgerAnchor,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ProtectedArtifactReadback {
    pub(crate) kind: BootstrapArtifactKind,
    pub(crate) path_exact: bool,
    pub(crate) local_volume: bool,
    pub(crate) reparse_free_held_chain: bool,
    pub(crate) single_link: bool,
    pub(crate) stable_identity: bool,
    pub(crate) exact_owner_and_acl: bool,
    pub(crate) full_held_handle_readback: bool,
}

impl ProtectedArtifactReadback {
    fn is_exact(self) -> bool {
        self.path_exact
            && self.local_volume
            && self.reparse_free_held_chain
            && self.single_link
            && self.stable_identity
            && self.exact_owner_and_acl
            && self.full_held_handle_readback
    }
}

#[derive(Debug, Clone)]
pub(crate) struct AuthorityBootstrapHistoricalGeneration {
    pub(crate) generation: [u8; 32],
    pub(crate) trust_manifest_bytes: Vec<u8>,
    pub(crate) activation_manifest_bytes: Vec<u8>,
    pub(crate) key_readback: VerifiedAuthorityKeyReadback,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct AuthorityBootstrapTerminalBinding {
    pub(crate) generation: [u8; 32],
    pub(crate) plan_sha256: [u8; 32],
    pub(crate) transaction_sha256: [u8; 32],
    pub(crate) activation_epoch: u64,
}

#[derive(Debug, Clone)]
pub(crate) struct AuthorityBootstrapSnapshot {
    pub(crate) schema: &'static str,
    pub(crate) active_head_bytes: Vec<u8>,
    pub(crate) trust_manifest_bytes: Vec<u8>,
    pub(crate) activation_manifest_bytes: Vec<u8>,
    pub(crate) activation_history: Vec<AuthorityBootstrapHistoricalGeneration>,
    pub(crate) activation_directory_names: Vec<String>,
    pub(crate) installed_content: AuthorityInstallContent,
    pub(super) runner_policy_state: RunnerPolicyStateDescriptor,
    pub(super) runner_policy_sealed_identity: RunnerPolicySealedIdentity,
    pub(crate) current_service_image: AuthorityPayloadDigest,
    pub(crate) key_readback: VerifiedAuthorityKeyReadback,
    pub(crate) ledger_identity: [u8; 32],
    pub(crate) ledger_frame_count: u64,
    pub(crate) ledger_byte_length: u64,
    pub(crate) ledger_sha256: [u8; 32],
    pub(crate) ledger_anchor_byte_length: u64,
    pub(crate) ledger_anchor_sha256: [u8; 32],
    pub(crate) active_ticket_count: usize,
    pub(crate) protected_artifacts: Vec<ProtectedArtifactReadback>,
    pub(crate) service_process_identity_exact: bool,
    pub(crate) service_process_id: u32,
    pub(crate) service_process_creation_time: u64,
    pub(crate) candidate_service_process: CandidateProcessEvidence,
    pub(crate) maintenance_terminal_binding: Option<AuthorityBootstrapTerminalBinding>,
}

#[derive(Debug, Clone)]
struct CandidateAuthorityBootstrapSnapshot {
    schema: &'static str,
    credential_readback: CandidateCredentialReadback,
    prior_head: CandidatePriorHeadObservation,
    trust_manifest_bytes: Vec<u8>,
    activation_manifest_bytes: Vec<u8>,
    activation_history: Vec<AuthorityBootstrapHistoricalGeneration>,
    activation_directory_names: Vec<String>,
    installed_content: AuthorityInstallContent,
    current_service_image: AuthorityPayloadDigest,
    key_readback: VerifiedAuthorityKeyReadback,
    ledger_identity: [u8; 32],
    ledger_frame_count: u64,
    ledger_byte_length: u64,
    ledger_sha256: [u8; 32],
    ledger_anchor_byte_length: u64,
    ledger_anchor_sha256: [u8; 32],
    protected_artifacts: Vec<ProtectedArtifactReadback>,
    service_process_identity_exact: bool,
    service_process_id: u32,
    service_process_creation_time: u64,
    candidate_service_process: CandidateProcessEvidence,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CandidatePriorHeadObservation {
    Absent,
    Present { head_sha256: [u8; 32] },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ValidatedAuthorityBootstrap {
    generation: [u8; 32],
    plan_sha256: [u8; 32],
    installed_layout_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
    active_head_sha256: [u8; 32],
    activation_manifest_sha256: [u8; 32],
    activation_epoch: u64,
    exact_service_configuration_sha256: [u8; 32],
    signer_key_id: [u8; 32],
    ledger_identity: [u8; 32],
    service_binary_sha256: [u8; 32],
    controller_binary_sha256: [u8; 32],
    controller_binary_byte_length: u64,
    install_helper_binary_sha256: [u8; 32],
    install_helper_binary_byte_length: u64,
    lifecycle_driver_binary_sha256: [u8; 32],
    lifecycle_driver_binary_byte_length: u64,
    bridge_launcher_binary_sha256: [u8; 32],
    bridge_launcher_binary_byte_length: u64,
    runtime_source_manifest: AuthorityPayloadDigest,
    runner_policy_state: RunnerPolicyStateDescriptor,
    runner_policy_sealed_identity: RunnerPolicySealedIdentity,
    service_process_id: u32,
    service_process_creation_time: u64,
    ledger_frame_count: u64,
    ledger_byte_length: u64,
    ledger_sha256: [u8; 32],
    ledger_anchor_byte_length: u64,
    ledger_anchor_sha256: [u8; 32],
    active_ticket_count: usize,
}

/// Only a self-authenticated immutable FinalCommit lane may construct this
/// runtime capability. Installed-state or maintenance-journal validation has
/// no conversion to it, and candidate activation remains a separate lane.
pub(crate) struct AuthenticatedFinalCommitBootstrap {
    validated: ValidatedAuthorityBootstrap,
    #[cfg(windows)]
    source: native_snapshot::NativeCommittedRuntimeBootstrapSource,
    #[cfg(windows)]
    ledger: AuthenticatedPublishedAuthorityLedger,
    #[cfg(windows)]
    runtime_source: AuthenticatedRuntimeSourceCapability,
    #[cfg(windows)]
    runner_policy: AuthenticatedRunnerPolicyCapability,
    #[cfg(windows)]
    root_executables: AuthenticatedProtectedRootExecutablesCapability,
}

#[cfg(windows)]
pub(crate) struct AuthenticatedFinalCommitBoundary {
    validated: ValidatedAuthorityBootstrap,
    source: native_snapshot::NativeCommittedRuntimeBootstrapSource,
    runtime_source: AuthenticatedRuntimeSourceCapability,
    runner_policy: Option<AuthenticatedRunnerPolicyCapability>,
    root_executables: AuthenticatedProtectedRootExecutablesCapability,
}

/// Immutable runtime-policy input projected only from an authenticated,
/// revalidated FinalCommit boundary. Callers can inspect this binding but
/// cannot construct one from caller-supplied digests or process identifiers.
#[cfg(windows)]
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct AuthenticatedFinalCommitPolicyBinding {
    generation: [u8; 32],
    signer_key_id: [u8; 32],
    protected_manifest_sha256: [u8; 32],
    installed_layout_sha256: [u8; 32],
    exact_service_configuration_sha256: [u8; 32],
    service_binary_sha256: [u8; 32],
    controller_binary_sha256: [u8; 32],
    controller_binary_byte_length: u64,
    install_helper_binary_sha256: [u8; 32],
    install_helper_binary_byte_length: u64,
    lifecycle_driver_binary_sha256: [u8; 32],
    lifecycle_driver_binary_byte_length: u64,
    bridge_launcher_binary_sha256: [u8; 32],
    bridge_launcher_binary_byte_length: u64,
    ledger_identity: [u8; 32],
    service_process_id: u32,
    service_process_creation_time: u64,
    final_commit_receipt_sha256: [u8; 32],
    published_runtime_binding: VerifiedPublishedRuntimeBindingProjection,
    runtime_source_manifest: AuthorityPayloadDigest,
    runner_policy_state: RunnerPolicyStateDescriptor,
    runner_policy_sealed_identity: RunnerPolicySealedIdentity,
}

/// Opaque, non-Clone authority to read the one runtime-source manifest held
/// open throughout the authenticated service-runtime boundary.
#[cfg(windows)]
pub(crate) struct AuthenticatedRuntimeSourceCapability {
    binding: AuthenticatedFinalCommitPolicyBinding,
    native: native_snapshot::NativeAuthenticatedRuntimeSourceCapability,
}

/// Opaque ownership of the exact sealed runner-policy file admitted by the
/// authenticated FinalCommit projection. It is deliberately non-Clone and
/// can produce its canonical readback only once.
#[cfg(windows)]
struct AuthenticatedRunnerPolicyCapability {
    generation: [u8; 32],
    transaction_sha256: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    descriptor: RunnerPolicyStateDescriptor,
    sealed_identity: RunnerPolicySealedIdentity,
    native: native_snapshot::NativeAuthenticatedRunnerPolicyCapability,
}

/// One-time canonical runner-policy readback bound to the exact generation,
/// transaction, FinalCommit receipt, and held-file identity.
#[cfg(windows)]
struct AuthenticatedRunnerPolicyReadback {
    generation: [u8; 32],
    transaction_sha256: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    descriptor: RunnerPolicyStateDescriptor,
    sealed_identity: RunnerPolicySealedIdentity,
    held_file_identity_sha256: [u8; 32],
    bytes: Vec<u8>,
}

/// Typed, one-use launch projection from the held runner-policy object. This
/// value is never assembled from request text or loose digests: its only
/// production source is `AuthenticatedRunnerPolicyReadback::into_launch_policy`.
#[cfg(windows)]
pub(crate) struct AuthenticatedRunnerLaunchPolicy {
    generation: [u8; 32],
    transaction_sha256: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    held_file_identity_sha256: [u8; 32],
    state_bytes_sha256: [u8; 32],
    state_binding_sha256: [u8; 32],
    account_binding_sha256: [u8; 32],
    profile_binding_sha256: [u8; 32],
    profile_identity_sha256: [u8; 32],
    profile_security_sha256: [u8; 32],
    account_sid: String,
    profile_root: PathBuf,
}

#[cfg(windows)]
impl fmt::Debug for AuthenticatedRunnerLaunchPolicy {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("AuthenticatedRunnerLaunchPolicy(<held-and-redacted>)")
    }
}

/// Opaque, non-Clone ownership of the two exact protected generation-root
/// executables held open from FinalCommit bootstrap. The native files stay
/// read-only and non-inheritable and are revalidated before every use.
#[cfg(windows)]
pub(crate) struct AuthenticatedProtectedRootExecutablesCapability {
    generation: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    lifecycle_driver: AuthorityPayloadDigest,
    bridge_launcher: AuthorityPayloadDigest,
    native: native_snapshot::NativeAuthenticatedProtectedRootExecutablesCapability,
}

/// Generation-bound, ordered lifecycle-driver/bridge-launcher live files.
/// This pair is deliberately non-Clone; callers may only consume the exact
/// read-only handles after a fresh invariant check.
#[cfg(windows)]
pub(crate) struct GenerationBoundProtectedExecutableHandles {
    generation: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    lifecycle_driver: AuthorityPayloadDigest,
    bridge_launcher: AuthorityPayloadDigest,
    native: native_snapshot::NativeGenerationBoundProtectedExecutableHandles,
}

#[cfg(windows)]
pub(crate) struct AuthenticatedRuntimeSourceReadback {
    descriptor: AuthorityPayloadDigest,
    identity_sha256: [u8; 32],
    bytes: Vec<u8>,
}

/// Complete, read-only service-generation evidence derived from the live
/// authenticated boundary. All fields are private so callers cannot assemble
/// a trusted binding from caller-supplied digests.
#[cfg(windows)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct AuthenticatedGenerationBindingReadback {
    current_generation: [u8; 32],
    service_executable_sha256: [u8; 32],
    controller_executable_sha256: [u8; 32],
    install_helper_executable_sha256: [u8; 32],
    lifecycle_driver_executable_sha256: [u8; 32],
    lifecycle_driver_executable_byte_length: u64,
    bridge_launcher_executable_sha256: [u8; 32],
    bridge_launcher_executable_byte_length: u64,
    installed_layout_sha256: [u8; 32],
    ledger_identity_sha256: [u8; 32],
    service_executable_path_sha256: [u8; 32],
    service_executable_file_identity_sha256: [u8; 32],
    service_process_id: u32,
    service_process_started_at: u64,
    protected_manifest_readback_sha256: [u8; 32],
    protected_key_readback_sha256: [u8; 32],
    signer_key_id: [u8; 32],
    protected_ledger_readback_sha256: [u8; 32],
    scm_readback_sha256: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
}

/// Fresh readback of the exact installed controller source held open by the
/// authenticated FinalCommit boundary. This non-Clone value owns an exact
/// reopened file lease; private fields prevent callers from substituting a
/// path, digest, or file identity.
#[cfg(windows)]
pub(crate) struct AuthenticatedControllerSourceReadback {
    generation: [u8; 32],
    service_process_id: u32,
    service_process_started_at: u64,
    controller_path: PathBuf,
    controller_sha256: [u8; 32],
    controller_byte_length: u64,
    volume_serial: u32,
    file_id: u64,
    link_count: u32,
    installed_layout_sha256: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    source_binding_sha256: [u8; 32],
    native: AuthenticatedControllerSourceObject,
}

#[cfg(windows)]
enum AuthenticatedControllerSourceObject {
    Held(native_snapshot::NativeAuthenticatedControllerSourceLease),
    #[cfg(test)]
    SnapshotOnly,
}

/// Fresh readback of the exact installed runtime-broker source held open by
/// the authenticated FinalCommit boundary. The value is intentionally
/// non-Clone, owns an exact reopened file lease, and can only be consumed by
/// the broker admission policy.
#[cfg(windows)]
pub(crate) struct AuthenticatedInstallHelperSourceReadback {
    generation: [u8; 32],
    service_process_id: u32,
    service_process_started_at: u64,
    install_helper_path: PathBuf,
    install_helper_sha256: [u8; 32],
    install_helper_byte_length: u64,
    volume_serial: u32,
    file_id: u64,
    link_count: u32,
    installed_layout_sha256: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    source_binding_sha256: [u8; 32],
    native: AuthenticatedInstallHelperSourceObject,
}

#[cfg(windows)]
enum AuthenticatedInstallHelperSourceObject {
    Held(native_snapshot::NativeAuthenticatedInstallHelperSourceLease),
    #[cfg(test)]
    SnapshotOnly,
}

#[cfg(windows)]
impl fmt::Debug for AuthenticatedControllerSourceReadback {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AuthenticatedControllerSourceReadback")
            .field("controller_path", &self.controller_path)
            .field("source_binding_sha256", &"<redacted>")
            .finish_non_exhaustive()
    }
}

#[cfg(windows)]
impl fmt::Debug for AuthenticatedInstallHelperSourceReadback {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AuthenticatedInstallHelperSourceReadback")
            .field("install_helper_path", &self.install_helper_path)
            .field("source_binding_sha256", &"<redacted>")
            .finish_non_exhaustive()
    }
}

impl AuthenticatedFinalCommitBootstrap {
    pub(crate) fn generation(&self) -> &[u8; 32] {
        self.validated.generation()
    }

    pub(crate) fn signer_key_id(&self) -> &[u8; 32] {
        self.validated.signer_key_id()
    }

    pub(crate) fn protected_manifest_sha256(&self) -> &[u8; 32] {
        self.validated.protected_manifest_sha256()
    }

    pub(crate) fn installed_layout_sha256(&self) -> &[u8; 32] {
        self.validated.installed_layout_sha256()
    }

    pub(crate) fn plan_sha256(&self) -> &[u8; 32] {
        self.validated.plan_sha256()
    }

    pub(crate) fn transaction_sha256(&self) -> &[u8; 32] {
        self.validated.transaction_sha256()
    }

    pub(crate) fn active_head_sha256(&self) -> &[u8; 32] {
        self.validated.active_head_sha256()
    }

    pub(crate) fn activation_epoch(&self) -> u64 {
        self.validated.activation_epoch()
    }

    pub(crate) fn exact_service_configuration_sha256(&self) -> &[u8; 32] {
        self.validated.exact_service_configuration_sha256()
    }

    pub(crate) fn ledger_identity(&self) -> &[u8; 32] {
        self.validated.ledger_identity()
    }

    pub(crate) fn ledger_byte_length(&self) -> u64 {
        self.validated.ledger_byte_length()
    }

    pub(crate) fn ledger_sha256(&self) -> &[u8; 32] {
        self.validated.ledger_sha256()
    }

    pub(crate) fn ledger_anchor_byte_length(&self) -> u64 {
        self.validated.ledger_anchor_byte_length()
    }

    pub(crate) fn ledger_anchor_sha256(&self) -> &[u8; 32] {
        self.validated.ledger_anchor_sha256()
    }

    pub(crate) fn service_binary_sha256(&self) -> &[u8; 32] {
        self.validated.service_binary_sha256()
    }

    pub(crate) fn service_process_id(&self) -> u32 {
        self.validated.service_process_id()
    }

    pub(crate) fn service_process_creation_time(&self) -> u64 {
        self.validated.service_process_creation_time()
    }

    pub(crate) fn active_ticket_count(&self) -> usize {
        self.validated.active_ticket_count()
    }

    pub(crate) fn receipt_sha256(&self) -> [u8; 32] {
        self.validated.receipt_sha256()
    }

    #[cfg(test)]
    fn validated(&self) -> &ValidatedAuthorityBootstrap {
        &self.validated
    }

    #[cfg(windows)]
    pub(crate) fn into_runtime_parts(
        self,
    ) -> (
        AuthenticatedFinalCommitBoundary,
        AuthenticatedPublishedAuthorityLedger,
    ) {
        (
            AuthenticatedFinalCommitBoundary {
                validated: self.validated,
                source: self.source,
                runtime_source: self.runtime_source,
                runner_policy: Some(self.runner_policy),
                root_executables: self.root_executables,
            },
            self.ledger,
        )
    }
}

#[cfg(windows)]
impl AuthenticatedFinalCommitBoundary {
    pub(crate) fn verify_still_stable(&mut self) -> Result<(), AuthorityBootstrapError> {
        self.source.verify_still_stable()?;
        let binding = self.source.published_runtime_binding();
        validate_exact_published_runtime_binding(
            &self.validated,
            self.runtime_source.binding.published_runtime_binding,
            binding,
        )?;
        self.runtime_source.verify()?;
        if let Some(runner_policy) = self.runner_policy.as_mut() {
            runner_policy.verify()?;
        }
        if self.root_executables.final_commit_receipt_sha256
            != *self.runtime_source.binding.final_commit_receipt_sha256()
            || self.source.published_final_commit_receipt_sha256()
                != self.root_executables.final_commit_receipt_sha256
        {
            return Err(AuthorityBootstrapError(
                "authority_root_executable_receipt_binding_mismatch",
            ));
        }
        self.root_executables.verify()
    }

    pub(crate) fn receipt_sha256(&self) -> [u8; 32] {
        self.validated.receipt_sha256()
    }

    pub(crate) fn sign_current_digest(
        &mut self,
        digest: &[u8; 32],
    ) -> Result<[u8; 64], AuthorityBootstrapError> {
        self.verify_still_stable()?;
        self.source.sign_current_digest(
            *self.validated.generation(),
            *self.validated.signer_key_id(),
            digest,
        )
    }

    pub(crate) fn verify_current_digest_signature(
        &mut self,
        digest: &[u8; 32],
        signature: &[u8; 64],
    ) -> Result<(), AuthorityBootstrapError> {
        self.verify_still_stable()?;
        self.source.verify_current_digest_signature(
            *self.validated.generation(),
            *self.validated.signer_key_id(),
            digest,
            signature,
        )
    }

    pub(crate) fn current_policy_binding(
        &mut self,
    ) -> Result<&AuthenticatedFinalCommitPolicyBinding, AuthorityBootstrapError> {
        self.verify_still_stable()?;
        Ok(self.runtime_source.binding())
    }

    pub(crate) fn current_generation_binding_readback(
        &mut self,
    ) -> Result<AuthenticatedGenerationBindingReadback, AuthorityBootstrapError> {
        self.verify_still_stable()?;
        let native = self.source.current_generation_binding_readback(
            *self.validated.generation(),
            *self.validated.signer_key_id(),
        )?;
        AuthenticatedGenerationBindingReadback::from_authenticated_boundary(
            &self.validated,
            self.runtime_source.binding(),
            native,
        )
    }

    pub(crate) fn current_controller_source_readback(
        &mut self,
    ) -> Result<AuthenticatedControllerSourceReadback, AuthorityBootstrapError> {
        self.verify_still_stable()?;
        let native = self.source.current_controller_source_readback(
            *self.validated.generation(),
            self.validated.controller_binary_descriptor()?,
        )?;
        AuthenticatedControllerSourceReadback::from_authenticated_boundary(
            &self.validated,
            self.runtime_source.binding(),
            native,
        )
    }

    pub(crate) fn current_install_helper_source_readback(
        &mut self,
    ) -> Result<AuthenticatedInstallHelperSourceReadback, AuthorityBootstrapError> {
        self.verify_still_stable()?;
        let native = self.source.current_install_helper_source_readback(
            *self.validated.generation(),
            self.validated.install_helper_binary_descriptor()?,
        )?;
        AuthenticatedInstallHelperSourceReadback::from_authenticated_boundary(
            &self.validated,
            self.runtime_source.binding(),
            native,
        )
    }

    pub(crate) fn runtime_source_capability(
        &mut self,
    ) -> Result<&mut AuthenticatedRuntimeSourceCapability, AuthorityBootstrapError> {
        self.verify_still_stable()?;
        Ok(&mut self.runtime_source)
    }

    fn take_runner_policy_capability(
        &mut self,
    ) -> Result<AuthenticatedRunnerPolicyCapability, AuthorityBootstrapError> {
        self.verify_still_stable()?;
        let mut capability = take_runner_policy_capability_once(&mut self.runner_policy)?;
        capability.verify()?;
        self.verify_still_stable()?;
        Ok(capability)
    }

    /// The unique production take point for the generation-wide launch
    /// authority. The sealed file is read exactly once, projected to typed
    /// state, and cross-checked again against the still-current FinalCommit
    /// boundary before the result leaves this method.
    pub(crate) fn take_runner_launch_policy(
        &mut self,
    ) -> Result<AuthenticatedRunnerLaunchPolicy, AuthorityBootstrapError> {
        let capability = self.take_runner_policy_capability()?;
        let authenticated = capability.read_once()?.into_launch_policy()?;
        self.verify_still_stable()?;
        let binding = self.runtime_source.binding();
        if authenticated.generation() != binding.generation()
            || authenticated.transaction_sha256() != self.validated.transaction_sha256()
            || authenticated.final_commit_receipt_sha256() != binding.final_commit_receipt_sha256()
        {
            return Err(AuthorityBootstrapError(
                "authority_runner_launch_policy_final_commit_mismatch",
            ));
        }
        Ok(authenticated)
    }

    #[allow(dead_code)]
    pub(crate) fn protected_root_executables_capability(
        &mut self,
    ) -> Result<&mut AuthenticatedProtectedRootExecutablesCapability, AuthorityBootstrapError> {
        self.verify_still_stable()?;
        Ok(&mut self.root_executables)
    }

    #[allow(dead_code)]
    pub(crate) fn clone_current_protected_scenario_executables(
        &mut self,
    ) -> Result<GenerationBoundProtectedExecutableHandles, AuthorityBootstrapError> {
        self.verify_still_stable()?;
        let mut handles = self.root_executables.clone_current()?;
        self.verify_still_stable()?;
        handles.verify_still_stable()?;
        Ok(handles)
    }
}

#[cfg(windows)]
fn take_runner_policy_capability_once<T>(
    slot: &mut Option<T>,
) -> Result<T, AuthorityBootstrapError> {
    slot.take().ok_or(AuthorityBootstrapError(
        "authority_runner_policy_capability_already_taken",
    ))
}

#[cfg(windows)]
impl AuthenticatedFinalCommitPolicyBinding {
    pub(crate) fn generation(&self) -> &[u8; 32] {
        &self.generation
    }

    pub(crate) fn signer_key_id(&self) -> &[u8; 32] {
        &self.signer_key_id
    }

    pub(crate) fn protected_manifest_sha256(&self) -> &[u8; 32] {
        &self.protected_manifest_sha256
    }

    pub(crate) fn installed_layout_sha256(&self) -> &[u8; 32] {
        &self.installed_layout_sha256
    }

    pub(crate) fn exact_service_configuration_sha256(&self) -> &[u8; 32] {
        &self.exact_service_configuration_sha256
    }

    pub(crate) fn service_binary_sha256(&self) -> &[u8; 32] {
        &self.service_binary_sha256
    }

    pub(crate) fn controller_binary_sha256(&self) -> &[u8; 32] {
        &self.controller_binary_sha256
    }

    pub(crate) fn controller_binary_byte_length(&self) -> u64 {
        self.controller_binary_byte_length
    }

    pub(crate) fn install_helper_binary_sha256(&self) -> &[u8; 32] {
        &self.install_helper_binary_sha256
    }

    pub(crate) fn install_helper_binary_byte_length(&self) -> u64 {
        self.install_helper_binary_byte_length
    }

    pub(crate) fn lifecycle_driver_binary_sha256(&self) -> &[u8; 32] {
        &self.lifecycle_driver_binary_sha256
    }

    pub(crate) fn lifecycle_driver_binary_byte_length(&self) -> u64 {
        self.lifecycle_driver_binary_byte_length
    }

    pub(crate) fn bridge_launcher_binary_sha256(&self) -> &[u8; 32] {
        &self.bridge_launcher_binary_sha256
    }

    pub(crate) fn bridge_launcher_binary_byte_length(&self) -> u64 {
        self.bridge_launcher_binary_byte_length
    }

    pub(crate) fn ledger_identity(&self) -> &[u8; 32] {
        &self.ledger_identity
    }

    pub(crate) fn service_process_id(&self) -> u32 {
        self.service_process_id
    }

    pub(crate) fn service_process_creation_time(&self) -> u64 {
        self.service_process_creation_time
    }

    pub(crate) fn final_commit_receipt_sha256(&self) -> &[u8; 32] {
        &self.final_commit_receipt_sha256
    }

    pub(crate) fn published_runtime_binding_sha256(&self) -> [u8; 32] {
        self.published_runtime_binding.complete_binding_sha256()
    }

    pub(crate) fn runtime_source_manifest(&self) -> AuthorityPayloadDigest {
        self.runtime_source_manifest
    }

    fn runner_policy_state(&self) -> RunnerPolicyStateDescriptor {
        self.runner_policy_state
    }

    fn runner_policy_sealed_identity(&self) -> RunnerPolicySealedIdentity {
        self.runner_policy_sealed_identity
    }

    #[cfg(test)]
    pub(crate) fn for_policy_source_test(seed: u8) -> Self {
        let digest = |tag: u8| [seed.wrapping_add(tag); 32];
        let runner_policy_sealed_identity =
            RunnerPolicySealedIdentity::exact_test_fixture(seed.wrapping_add(23));
        Self {
            generation: digest(1),
            signer_key_id: digest(2),
            protected_manifest_sha256: digest(3),
            installed_layout_sha256: digest(4),
            exact_service_configuration_sha256: digest(5),
            service_binary_sha256: digest(6),
            controller_binary_sha256: digest(16),
            controller_binary_byte_length: 160,
            install_helper_binary_sha256: digest(17),
            install_helper_binary_byte_length: 170,
            lifecycle_driver_binary_sha256: digest(18),
            lifecycle_driver_binary_byte_length: 180,
            bridge_launcher_binary_sha256: digest(19),
            bridge_launcher_binary_byte_length: 190,
            ledger_identity: digest(20),
            service_process_id: 4_100 + u32::from(seed),
            service_process_creation_time: 40_000 + u64::from(seed),
            final_commit_receipt_sha256: digest(7),
            published_runtime_binding:
                VerifiedPublishedRuntimeBindingProjection::for_bootstrap_test(
                    digest(1),
                    digest(8),
                    digest(9),
                    AuthorityMaintenanceOperation::Install,
                    digest(5),
                    digest(6),
                    digest(10),
                    digest(3),
                    1,
                    512,
                    digest(21),
                    digest(22),
                    runner_policy_sealed_identity.volume_serial(),
                    runner_policy_sealed_identity.file_id(),
                    runner_policy_sealed_identity.link_count(),
                    runner_policy_sealed_identity.attributes(),
                ),
            runtime_source_manifest: AuthorityPayloadDigest::new(digest(11), 128).unwrap(),
            runner_policy_state: RunnerPolicyStateDescriptor::exact_test_fixture(
                digest(1),
                digest(9),
                512,
                digest(21),
                digest(22),
            ),
            runner_policy_sealed_identity,
        }
    }
}

#[cfg(windows)]
impl AuthenticatedProtectedRootExecutablesCapability {
    fn new(
        validated: &ValidatedAuthorityBootstrap,
        final_commit_receipt_sha256: [u8; 32],
        mut native: native_snapshot::NativeAuthenticatedProtectedRootExecutablesCapability,
    ) -> Result<Self, AuthorityBootstrapError> {
        if final_commit_receipt_sha256.iter().all(|value| *value == 0) {
            return Err(AuthorityBootstrapError(
                "authority_root_executable_receipt_not_verified",
            ));
        }
        let lifecycle_driver = validated.lifecycle_driver_binary_descriptor()?;
        let bridge_launcher = validated.bridge_launcher_binary_descriptor()?;
        native.verify(
            *validated.generation(),
            final_commit_receipt_sha256,
            lifecycle_driver,
            bridge_launcher,
        )?;
        Ok(Self {
            generation: *validated.generation(),
            final_commit_receipt_sha256,
            lifecycle_driver,
            bridge_launcher,
            native,
        })
    }

    pub(crate) fn verify(&mut self) -> Result<(), AuthorityBootstrapError> {
        if self
            .final_commit_receipt_sha256
            .iter()
            .all(|value| *value == 0)
        {
            return Err(AuthorityBootstrapError(
                "authority_root_executable_receipt_not_verified",
            ));
        }
        self.native.verify(
            self.generation,
            self.final_commit_receipt_sha256,
            self.lifecycle_driver,
            self.bridge_launcher,
        )
    }

    #[allow(dead_code)]
    pub(crate) fn with_verified_files<R>(
        &mut self,
        operation: impl FnOnce(&std::fs::File, &std::fs::File) -> Result<R, AuthorityBootstrapError>,
    ) -> Result<R, AuthorityBootstrapError> {
        self.native.with_verified_files(
            self.generation,
            self.final_commit_receipt_sha256,
            self.lifecycle_driver,
            self.bridge_launcher,
            operation,
        )
    }

    fn clone_current(
        &mut self,
    ) -> Result<GenerationBoundProtectedExecutableHandles, AuthorityBootstrapError> {
        self.verify()?;
        let mut handles = GenerationBoundProtectedExecutableHandles {
            generation: self.generation,
            final_commit_receipt_sha256: self.final_commit_receipt_sha256,
            lifecycle_driver: self.lifecycle_driver,
            bridge_launcher: self.bridge_launcher,
            native: self.native.clone_current(
                self.generation,
                self.final_commit_receipt_sha256,
                self.lifecycle_driver,
                self.bridge_launcher,
            )?,
        };
        self.verify()?;
        handles.verify_still_stable()?;
        Ok(handles)
    }
}

#[cfg(windows)]
impl GenerationBoundProtectedExecutableHandles {
    pub(crate) fn generation(&self) -> &[u8; 32] {
        &self.generation
    }

    #[allow(dead_code)]
    pub(crate) fn final_commit_receipt_sha256(&self) -> &[u8; 32] {
        &self.final_commit_receipt_sha256
    }

    pub(crate) fn lifecycle_driver_descriptor(&self) -> AuthorityPayloadDigest {
        self.lifecycle_driver
    }

    pub(crate) fn bridge_launcher_descriptor(&self) -> AuthorityPayloadDigest {
        self.bridge_launcher
    }

    pub(crate) fn verify_still_stable(&mut self) -> Result<(), AuthorityBootstrapError> {
        if self
            .final_commit_receipt_sha256
            .iter()
            .all(|value| *value == 0)
        {
            return Err(AuthorityBootstrapError(
                "authority_root_executable_receipt_not_verified",
            ));
        }
        self.native.verify(
            self.generation,
            self.final_commit_receipt_sha256,
            self.lifecycle_driver,
            self.bridge_launcher,
        )
    }

    #[allow(dead_code)]
    pub(crate) fn into_verified_ordered_files(
        mut self,
    ) -> Result<[std::fs::File; 2], AuthorityBootstrapError> {
        self.verify_still_stable()?;
        self.native.into_verified_ordered_files(
            self.generation,
            self.final_commit_receipt_sha256,
            self.lifecycle_driver,
            self.bridge_launcher,
        )
    }
}

#[cfg(windows)]
impl AuthenticatedRuntimeSourceCapability {
    fn new(
        validated: &ValidatedAuthorityBootstrap,
        published_runtime_binding: VerifiedPublishedRuntimeBindingProjection,
        final_commit_receipt_sha256: [u8; 32],
        mut native: native_snapshot::NativeAuthenticatedRuntimeSourceCapability,
    ) -> Result<Self, AuthorityBootstrapError> {
        let binding = authenticated_final_commit_policy_binding(
            validated,
            published_runtime_binding,
            final_commit_receipt_sha256,
        )?;
        native.verify(binding.runtime_source_manifest())?;
        Ok(Self { binding, native })
    }

    pub(crate) fn binding(&self) -> &AuthenticatedFinalCommitPolicyBinding {
        &self.binding
    }

    pub(crate) fn verify(&mut self) -> Result<(), AuthorityBootstrapError> {
        self.native.verify(self.binding.runtime_source_manifest())
    }

    pub(crate) fn read_verified(
        &mut self,
    ) -> Result<AuthenticatedRuntimeSourceReadback, AuthorityBootstrapError> {
        self.native
            .read_verified(self.binding.runtime_source_manifest())
    }
}

#[cfg(windows)]
impl AuthenticatedRuntimeSourceReadback {
    pub(crate) fn into_parts(self) -> (AuthorityPayloadDigest, [u8; 32], Vec<u8>) {
        (self.descriptor, self.identity_sha256, self.bytes)
    }
}

#[cfg(windows)]
impl AuthenticatedRunnerPolicyCapability {
    fn new(
        validated: &ValidatedAuthorityBootstrap,
        final_commit_receipt_sha256: [u8; 32],
        mut native: native_snapshot::NativeAuthenticatedRunnerPolicyCapability,
    ) -> Result<Self, AuthorityBootstrapError> {
        let descriptor = validated.runner_policy_state;
        let sealed_identity = validated.runner_policy_sealed_identity;
        if final_commit_receipt_sha256.iter().all(|value| *value == 0)
            || descriptor.generation_sha256() != *validated.generation()
            || descriptor.transaction_sha256() != *validated.transaction_sha256()
            || sealed_identity.validate().is_err()
        {
            return Err(AuthorityBootstrapError(
                "authority_runner_policy_final_commit_binding_invalid",
            ));
        }
        native.verify(
            *validated.generation(),
            *validated.transaction_sha256(),
            final_commit_receipt_sha256,
            descriptor,
            sealed_identity,
        )?;
        Ok(Self {
            generation: *validated.generation(),
            transaction_sha256: *validated.transaction_sha256(),
            final_commit_receipt_sha256,
            descriptor,
            sealed_identity,
            native,
        })
    }

    pub(crate) fn verify(&mut self) -> Result<(), AuthorityBootstrapError> {
        self.native.verify(
            self.generation,
            self.transaction_sha256,
            self.final_commit_receipt_sha256,
            self.descriptor,
            self.sealed_identity,
        )
    }

    fn read_once(mut self) -> Result<AuthenticatedRunnerPolicyReadback, AuthorityBootstrapError> {
        self.verify()?;
        let native = self.native.read_once(
            self.generation,
            self.transaction_sha256,
            self.final_commit_receipt_sha256,
            self.descriptor,
            self.sealed_identity,
        )?;
        let state = CanonicalRunnerPolicyState::parse_canonical(&native.bytes)
            .map_err(|_| AuthorityBootstrapError("authority_runner_policy_canonical_invalid"))?;
        let descriptor = state
            .descriptor()
            .map_err(|_| AuthorityBootstrapError("authority_runner_policy_descriptor_invalid"))?;
        if descriptor != self.descriptor
            || descriptor.generation_sha256() != self.generation
            || descriptor.transaction_sha256() != self.transaction_sha256
            || native
                .held_file_identity_sha256
                .iter()
                .all(|value| *value == 0)
        {
            return Err(AuthorityBootstrapError(
                "authority_runner_policy_readback_binding_mismatch",
            ));
        }
        Ok(AuthenticatedRunnerPolicyReadback {
            generation: self.generation,
            transaction_sha256: self.transaction_sha256,
            final_commit_receipt_sha256: self.final_commit_receipt_sha256,
            descriptor,
            sealed_identity: self.sealed_identity,
            held_file_identity_sha256: native.held_file_identity_sha256,
            bytes: native.bytes,
        })
    }
}

#[cfg(windows)]
impl AuthenticatedRunnerPolicyReadback {
    pub(crate) fn generation(&self) -> &[u8; 32] {
        &self.generation
    }

    pub(crate) fn transaction_sha256(&self) -> &[u8; 32] {
        &self.transaction_sha256
    }

    pub(crate) fn final_commit_receipt_sha256(&self) -> &[u8; 32] {
        &self.final_commit_receipt_sha256
    }

    pub(super) fn descriptor(&self) -> RunnerPolicyStateDescriptor {
        self.descriptor
    }

    pub(super) fn sealed_identity(&self) -> RunnerPolicySealedIdentity {
        self.sealed_identity
    }

    pub(crate) fn held_file_identity_sha256(&self) -> &[u8; 32] {
        &self.held_file_identity_sha256
    }

    pub(crate) fn canonical_bytes(&self) -> &[u8] {
        &self.bytes
    }

    /// Consume the held-file readback and re-parse its already-authenticated
    /// canonical bytes into the only policy shape accepted by the native
    /// launcher adapter. Consuming `self` prevents a second launch capability
    /// from being projected from the same readback.
    fn into_launch_policy(
        self,
    ) -> Result<AuthenticatedRunnerLaunchPolicy, AuthorityBootstrapError> {
        self.sealed_identity.validate().map_err(|_| {
            AuthorityBootstrapError("authority_runner_launch_policy_sealed_identity_invalid")
        })?;
        let state = CanonicalRunnerPolicyState::parse_canonical(&self.bytes).map_err(|_| {
            AuthorityBootstrapError("authority_runner_launch_policy_canonical_invalid")
        })?;
        let descriptor = state.descriptor().map_err(|_| {
            AuthorityBootstrapError("authority_runner_launch_policy_descriptor_invalid")
        })?;
        let profile_root = state.profile_root().to_path_buf();
        let state_binding_sha256 = *state.binding_sha256();
        let account_binding_sha256 = *state.account_binding_sha256();
        let profile_binding_sha256 = *state.profile_binding_sha256();
        let profile_identity_sha256 = *state.profile_identity_sha256();
        let profile_security_sha256 = *state.profile_security_sha256();
        if descriptor != self.descriptor
            || descriptor.generation_sha256() != self.generation
            || descriptor.transaction_sha256() != self.transaction_sha256
            || state.generation_sha256() != &self.generation
            || state.transaction_sha256() != &self.transaction_sha256
            || self
                .final_commit_receipt_sha256
                .iter()
                .all(|value| *value == 0)
            || self
                .held_file_identity_sha256
                .iter()
                .all(|value| *value == 0)
            || [
                descriptor.bytes_sha256(),
                state_binding_sha256,
                account_binding_sha256,
                profile_binding_sha256,
                profile_identity_sha256,
                profile_security_sha256,
            ]
            .iter()
            .any(|digest| digest.iter().all(|value| *value == 0))
        {
            return Err(AuthorityBootstrapError(
                "authority_runner_launch_policy_binding_mismatch",
            ));
        }
        Ok(AuthenticatedRunnerLaunchPolicy {
            generation: self.generation,
            transaction_sha256: self.transaction_sha256,
            final_commit_receipt_sha256: self.final_commit_receipt_sha256,
            held_file_identity_sha256: self.held_file_identity_sha256,
            state_bytes_sha256: descriptor.bytes_sha256(),
            state_binding_sha256,
            account_binding_sha256,
            profile_binding_sha256,
            profile_identity_sha256,
            profile_security_sha256,
            account_sid: state.canonical_account_sid().to_owned(),
            profile_root,
        })
    }
}

#[cfg(windows)]
impl AuthenticatedRunnerLaunchPolicy {
    pub(crate) const fn generation(&self) -> &[u8; 32] {
        &self.generation
    }

    pub(crate) const fn transaction_sha256(&self) -> &[u8; 32] {
        &self.transaction_sha256
    }

    pub(crate) const fn final_commit_receipt_sha256(&self) -> &[u8; 32] {
        &self.final_commit_receipt_sha256
    }

    pub(crate) const fn held_file_identity_sha256(&self) -> &[u8; 32] {
        &self.held_file_identity_sha256
    }

    pub(crate) const fn state_bytes_sha256(&self) -> &[u8; 32] {
        &self.state_bytes_sha256
    }

    pub(crate) const fn state_binding_sha256(&self) -> &[u8; 32] {
        &self.state_binding_sha256
    }

    pub(crate) const fn account_binding_sha256(&self) -> &[u8; 32] {
        &self.account_binding_sha256
    }

    pub(crate) const fn profile_binding_sha256(&self) -> &[u8; 32] {
        &self.profile_binding_sha256
    }

    pub(crate) const fn profile_identity_sha256(&self) -> &[u8; 32] {
        &self.profile_identity_sha256
    }

    pub(crate) const fn profile_security_sha256(&self) -> &[u8; 32] {
        &self.profile_security_sha256
    }

    pub(crate) fn account_sid(&self) -> &str {
        &self.account_sid
    }

    pub(crate) fn profile_root(&self) -> &Path {
        &self.profile_root
    }

    #[cfg(test)]
    pub(crate) fn exact_test_fixture(generation: [u8; 32], transaction_sha256: [u8; 32]) -> Self {
        let state =
            CanonicalRunnerPolicyState::canonical_test_fixture(generation, transaction_sha256);
        let bytes = state
            .canonical_bytes()
            .expect("runner launch fixture must serialize");
        let descriptor = state
            .descriptor()
            .expect("runner launch fixture must have a descriptor");
        AuthenticatedRunnerPolicyReadback {
            generation,
            transaction_sha256,
            final_commit_receipt_sha256: [0x71; 32],
            descriptor,
            sealed_identity: RunnerPolicySealedIdentity::exact_test_fixture(0x72),
            held_file_identity_sha256: [0x73; 32],
            bytes,
        }
        .into_launch_policy()
        .expect("runner launch fixture must authenticate")
    }
}

#[cfg(windows)]
impl AuthenticatedGenerationBindingReadback {
    fn from_authenticated_boundary(
        validated: &ValidatedAuthorityBootstrap,
        binding: &AuthenticatedFinalCommitPolicyBinding,
        native: native_snapshot::NativeAuthenticatedGenerationBindingReadback,
    ) -> Result<Self, AuthorityBootstrapError> {
        if binding.generation() != validated.generation()
            || binding.signer_key_id() != validated.signer_key_id()
            || binding.protected_manifest_sha256() != validated.protected_manifest_sha256()
            || binding.installed_layout_sha256() != validated.installed_layout_sha256()
            || binding.exact_service_configuration_sha256()
                != validated.exact_service_configuration_sha256()
            || binding.service_binary_sha256() != validated.service_binary_sha256()
            || binding.controller_binary_sha256() != validated.controller_binary_sha256()
            || binding.install_helper_binary_sha256() != validated.install_helper_binary_sha256()
            || binding.lifecycle_driver_binary_sha256()
                != validated.lifecycle_driver_binary_sha256()
            || binding.lifecycle_driver_binary_byte_length()
                != validated.lifecycle_driver_binary_byte_length
            || binding.bridge_launcher_binary_sha256() != validated.bridge_launcher_binary_sha256()
            || binding.bridge_launcher_binary_byte_length()
                != validated.bridge_launcher_binary_byte_length
            || binding.ledger_identity() != validated.ledger_identity()
            || binding.service_process_id() != validated.service_process_id()
            || binding.service_process_creation_time() != validated.service_process_creation_time()
            || binding.runtime_source_manifest() != validated.runtime_source_manifest
            || binding.runner_policy_state() != validated.runner_policy_state
            || binding.runner_policy_sealed_identity() != validated.runner_policy_sealed_identity
        {
            return Err(AuthorityBootstrapError(
                "authority_generation_binding_boundary_mismatch",
            ));
        }
        let protected_ledger_readback_sha256 = validated.protected_ledger_readback_sha256()?;
        let readback = Self {
            current_generation: *validated.generation(),
            service_executable_sha256: *validated.service_binary_sha256(),
            controller_executable_sha256: *validated.controller_binary_sha256(),
            install_helper_executable_sha256: *validated.install_helper_binary_sha256(),
            lifecycle_driver_executable_sha256: *validated.lifecycle_driver_binary_sha256(),
            lifecycle_driver_executable_byte_length: validated.lifecycle_driver_binary_byte_length,
            bridge_launcher_executable_sha256: *validated.bridge_launcher_binary_sha256(),
            bridge_launcher_executable_byte_length: validated.bridge_launcher_binary_byte_length,
            installed_layout_sha256: *validated.installed_layout_sha256(),
            ledger_identity_sha256: *validated.ledger_identity(),
            service_executable_path_sha256: native.service_executable_path_sha256,
            service_executable_file_identity_sha256: native.service_executable_file_identity_sha256,
            service_process_id: validated.service_process_id(),
            service_process_started_at: validated.service_process_creation_time(),
            protected_manifest_readback_sha256: *validated.protected_manifest_sha256(),
            protected_key_readback_sha256: native.protected_key_readback_sha256,
            signer_key_id: *validated.signer_key_id(),
            protected_ledger_readback_sha256,
            scm_readback_sha256: native.scm_readback_sha256,
            final_commit_receipt_sha256: *binding.final_commit_receipt_sha256(),
        };
        readback.validate()?;
        Ok(readback)
    }

    fn validate(&self) -> Result<(), AuthorityBootstrapError> {
        let digests = [
            &self.current_generation,
            &self.service_executable_sha256,
            &self.controller_executable_sha256,
            &self.install_helper_executable_sha256,
            &self.lifecycle_driver_executable_sha256,
            &self.bridge_launcher_executable_sha256,
            &self.installed_layout_sha256,
            &self.ledger_identity_sha256,
            &self.service_executable_path_sha256,
            &self.service_executable_file_identity_sha256,
            &self.protected_manifest_readback_sha256,
            &self.protected_key_readback_sha256,
            &self.signer_key_id,
            &self.protected_ledger_readback_sha256,
            &self.scm_readback_sha256,
            &self.final_commit_receipt_sha256,
        ];
        if digests
            .into_iter()
            .any(|digest| digest.iter().all(|byte| *byte == 0))
            || self.lifecycle_driver_executable_byte_length == 0
            || self.bridge_launcher_executable_byte_length == 0
            || self.service_process_id == 0
            || self.service_process_started_at == 0
        {
            return Err(AuthorityBootstrapError(
                "authority_generation_binding_readback_invalid",
            ));
        }
        Ok(())
    }

    pub(crate) fn current_generation(&self) -> &[u8; 32] {
        &self.current_generation
    }

    pub(crate) fn service_executable_sha256(&self) -> &[u8; 32] {
        &self.service_executable_sha256
    }

    pub(crate) fn controller_executable_sha256(&self) -> &[u8; 32] {
        &self.controller_executable_sha256
    }

    pub(crate) fn install_helper_executable_sha256(&self) -> &[u8; 32] {
        &self.install_helper_executable_sha256
    }

    pub(crate) fn lifecycle_driver_executable_sha256(&self) -> &[u8; 32] {
        &self.lifecycle_driver_executable_sha256
    }

    pub(crate) fn lifecycle_driver_executable_byte_length(&self) -> u64 {
        self.lifecycle_driver_executable_byte_length
    }

    pub(crate) fn bridge_launcher_executable_sha256(&self) -> &[u8; 32] {
        &self.bridge_launcher_executable_sha256
    }

    pub(crate) fn bridge_launcher_executable_byte_length(&self) -> u64 {
        self.bridge_launcher_executable_byte_length
    }

    pub(crate) fn installed_layout_sha256(&self) -> &[u8; 32] {
        &self.installed_layout_sha256
    }

    pub(crate) fn ledger_identity_sha256(&self) -> &[u8; 32] {
        &self.ledger_identity_sha256
    }

    pub(crate) fn service_executable_path_sha256(&self) -> &[u8; 32] {
        &self.service_executable_path_sha256
    }

    pub(crate) fn service_executable_file_identity_sha256(&self) -> &[u8; 32] {
        &self.service_executable_file_identity_sha256
    }

    pub(crate) fn service_process_id(&self) -> u32 {
        self.service_process_id
    }

    pub(crate) fn service_process_started_at(&self) -> u64 {
        self.service_process_started_at
    }

    pub(crate) fn protected_manifest_readback_sha256(&self) -> &[u8; 32] {
        &self.protected_manifest_readback_sha256
    }

    pub(crate) fn protected_key_readback_sha256(&self) -> &[u8; 32] {
        &self.protected_key_readback_sha256
    }

    pub(crate) fn signer_key_id(&self) -> &[u8; 32] {
        &self.signer_key_id
    }

    pub(crate) fn protected_ledger_readback_sha256(&self) -> &[u8; 32] {
        &self.protected_ledger_readback_sha256
    }

    pub(crate) fn scm_readback_sha256(&self) -> &[u8; 32] {
        &self.scm_readback_sha256
    }

    pub(crate) fn final_commit_receipt_sha256(&self) -> &[u8; 32] {
        &self.final_commit_receipt_sha256
    }

    #[cfg(test)]
    pub(crate) fn for_policy_source_test(binding: &AuthenticatedFinalCommitPolicyBinding) -> Self {
        Self {
            current_generation: *binding.generation(),
            service_executable_sha256: *binding.service_binary_sha256(),
            controller_executable_sha256: *binding.controller_binary_sha256(),
            install_helper_executable_sha256: *binding.install_helper_binary_sha256(),
            lifecycle_driver_executable_sha256: *binding.lifecycle_driver_binary_sha256(),
            lifecycle_driver_executable_byte_length: binding.lifecycle_driver_binary_byte_length(),
            bridge_launcher_executable_sha256: *binding.bridge_launcher_binary_sha256(),
            bridge_launcher_executable_byte_length: binding.bridge_launcher_binary_byte_length(),
            installed_layout_sha256: *binding.installed_layout_sha256(),
            ledger_identity_sha256: *binding.ledger_identity(),
            service_executable_path_sha256: [0xd1; 32],
            service_executable_file_identity_sha256: [0xd2; 32],
            service_process_id: binding.service_process_id(),
            service_process_started_at: binding.service_process_creation_time(),
            protected_manifest_readback_sha256: [0xd3; 32],
            protected_key_readback_sha256: [0xd4; 32],
            signer_key_id: *binding.signer_key_id(),
            protected_ledger_readback_sha256: [0xd5; 32],
            scm_readback_sha256: [0xd6; 32],
            final_commit_receipt_sha256: *binding.final_commit_receipt_sha256(),
        }
    }

    #[cfg(test)]
    pub(crate) fn with_boundary_drift_for_policy_source_test(mut self, field: usize) -> Self {
        match field {
            0 => self.current_generation[0] ^= 1,
            1 => self.service_executable_sha256[0] ^= 1,
            2 => self.controller_executable_sha256[0] ^= 1,
            3 => self.install_helper_executable_sha256[0] ^= 1,
            4 => self.installed_layout_sha256[0] ^= 1,
            5 => self.ledger_identity_sha256[0] ^= 1,
            6 => self.service_process_id += 1,
            7 => self.service_process_started_at += 1,
            8 => self.signer_key_id[0] ^= 1,
            9 => self.final_commit_receipt_sha256[0] ^= 1,
            10 => self.lifecycle_driver_executable_sha256[0] ^= 1,
            11 => self.bridge_launcher_executable_sha256[0] ^= 1,
            12 => self.lifecycle_driver_executable_byte_length += 1,
            13 => self.bridge_launcher_executable_byte_length += 1,
            _ => panic!("unsupported policy-source test drift"),
        }
        self
    }
}

#[cfg(windows)]
impl AuthenticatedControllerSourceReadback {
    fn from_authenticated_boundary(
        validated: &ValidatedAuthorityBootstrap,
        binding: &AuthenticatedFinalCommitPolicyBinding,
        native: native_snapshot::NativeAuthenticatedControllerSourceLease,
    ) -> Result<Self, AuthorityBootstrapError> {
        native.verify()?;
        let snapshot = native.readback().clone();
        Self::from_authenticated_boundary_snapshot(
            validated,
            binding,
            snapshot,
            AuthenticatedControllerSourceObject::Held(native),
        )
    }

    #[cfg(test)]
    fn from_authenticated_boundary_for_test(
        validated: &ValidatedAuthorityBootstrap,
        binding: &AuthenticatedFinalCommitPolicyBinding,
        native: native_snapshot::NativeAuthenticatedControllerSourceReadback,
    ) -> Result<Self, AuthorityBootstrapError> {
        Self::from_authenticated_boundary_snapshot(
            validated,
            binding,
            native,
            AuthenticatedControllerSourceObject::SnapshotOnly,
        )
    }

    fn from_authenticated_boundary_snapshot(
        validated: &ValidatedAuthorityBootstrap,
        binding: &AuthenticatedFinalCommitPolicyBinding,
        native: native_snapshot::NativeAuthenticatedControllerSourceReadback,
        native_object: AuthenticatedControllerSourceObject,
    ) -> Result<Self, AuthorityBootstrapError> {
        use std::os::windows::ffi::OsStrExt;

        if binding.generation() != validated.generation()
            || binding.service_process_id() != validated.service_process_id()
            || binding.service_process_creation_time() != validated.service_process_creation_time()
            || binding.controller_binary_sha256() != validated.controller_binary_sha256()
            || binding.controller_binary_byte_length() != validated.controller_binary_byte_length
            || binding.installed_layout_sha256() != validated.installed_layout_sha256()
            || native.descriptor != validated.controller_binary_descriptor()?
            || native.controller_path.as_os_str().is_empty()
            || !native.controller_path.is_absolute()
            || native.controller_path.components().any(|component| {
                matches!(
                    component,
                    std::path::Component::CurDir | std::path::Component::ParentDir
                )
            })
            || native.volume_serial == 0
            || native.file_id == 0
            || native.link_count != 1
        {
            return Err(AuthorityBootstrapError(
                "authority_controller_source_readback_invalid",
            ));
        }
        let mut digest = Sha256::new();
        digest.update(AUTHENTICATED_CONTROLLER_SOURCE_BINDING_DOMAIN);
        digest.update(validated.generation());
        digest.update(validated.service_process_id().to_be_bytes());
        digest.update(validated.service_process_creation_time().to_be_bytes());
        digest.update(validated.installed_layout_sha256());
        digest.update(binding.final_commit_receipt_sha256());
        digest.update(native.descriptor.sha256());
        digest.update(native.descriptor.byte_length().to_be_bytes());
        digest.update(native.volume_serial.to_be_bytes());
        digest.update(native.file_id.to_be_bytes());
        digest.update(native.link_count.to_be_bytes());
        let path_words = native.controller_path.as_os_str().encode_wide();
        let mut path_length = 0u64;
        for word in path_words {
            digest.update(word.to_be_bytes());
            path_length = path_length.checked_add(1).ok_or(AuthorityBootstrapError(
                "authority_controller_source_readback_invalid",
            ))?;
        }
        digest.update(path_length.to_be_bytes());
        let source_binding_sha256 = digest.finalize().into();
        Ok(Self {
            generation: *validated.generation(),
            service_process_id: validated.service_process_id(),
            service_process_started_at: validated.service_process_creation_time(),
            controller_path: native.controller_path,
            controller_sha256: *native.descriptor.sha256(),
            controller_byte_length: native.descriptor.byte_length(),
            volume_serial: native.volume_serial,
            file_id: native.file_id,
            link_count: native.link_count,
            installed_layout_sha256: *validated.installed_layout_sha256(),
            final_commit_receipt_sha256: *binding.final_commit_receipt_sha256(),
            source_binding_sha256,
            native: native_object,
        })
    }

    pub(crate) fn verify_still_stable(&self) -> Result<(), AuthorityBootstrapError> {
        match &self.native {
            AuthenticatedControllerSourceObject::Held(native) => native.verify(),
            #[cfg(test)]
            AuthenticatedControllerSourceObject::SnapshotOnly => Err(AuthorityBootstrapError(
                "authority_controller_source_lease_unavailable",
            )),
        }
    }

    pub(crate) fn generation(&self) -> &[u8; 32] {
        &self.generation
    }

    pub(crate) fn service_process_id(&self) -> u32 {
        self.service_process_id
    }

    pub(crate) fn service_process_started_at(&self) -> u64 {
        self.service_process_started_at
    }

    pub(crate) fn controller_path(&self) -> &std::path::Path {
        &self.controller_path
    }

    pub(crate) fn controller_sha256(&self) -> &[u8; 32] {
        &self.controller_sha256
    }

    pub(crate) fn controller_byte_length(&self) -> u64 {
        self.controller_byte_length
    }

    pub(crate) fn volume_serial(&self) -> u32 {
        self.volume_serial
    }

    pub(crate) fn file_id(&self) -> u64 {
        self.file_id
    }

    pub(crate) fn link_count(&self) -> u32 {
        self.link_count
    }

    pub(crate) fn installed_layout_sha256(&self) -> &[u8; 32] {
        &self.installed_layout_sha256
    }

    pub(crate) fn final_commit_receipt_sha256(&self) -> &[u8; 32] {
        &self.final_commit_receipt_sha256
    }

    pub(crate) fn source_binding_sha256(&self) -> &[u8; 32] {
        &self.source_binding_sha256
    }

    /// Borrows the exact FinalCommit controller file object. The opaque
    /// readback remains the owner, so callers cannot replace the source with a
    /// path or caller-supplied identity while a transfer is in progress.
    pub(crate) fn held_file_handle(
        &self,
    ) -> Result<std::os::windows::io::BorrowedHandle<'_>, AuthorityBootstrapError> {
        self.verify_still_stable()?;
        match &self.native {
            AuthenticatedControllerSourceObject::Held(native) => Ok(native.file_handle()),
            #[cfg(test)]
            AuthenticatedControllerSourceObject::SnapshotOnly => Err(AuthorityBootstrapError(
                "authority_controller_source_lease_unavailable",
            )),
        }
    }
}

#[cfg(windows)]
impl AuthenticatedInstallHelperSourceReadback {
    fn from_authenticated_boundary(
        validated: &ValidatedAuthorityBootstrap,
        binding: &AuthenticatedFinalCommitPolicyBinding,
        native: native_snapshot::NativeAuthenticatedInstallHelperSourceLease,
    ) -> Result<Self, AuthorityBootstrapError> {
        native.verify()?;
        let snapshot = native.readback().clone();
        Self::from_authenticated_boundary_snapshot(
            validated,
            binding,
            snapshot,
            AuthenticatedInstallHelperSourceObject::Held(native),
        )
    }

    #[cfg(test)]
    fn from_authenticated_boundary_for_test(
        validated: &ValidatedAuthorityBootstrap,
        binding: &AuthenticatedFinalCommitPolicyBinding,
        native: native_snapshot::NativeAuthenticatedInstallHelperSourceReadback,
    ) -> Result<Self, AuthorityBootstrapError> {
        Self::from_authenticated_boundary_snapshot(
            validated,
            binding,
            native,
            AuthenticatedInstallHelperSourceObject::SnapshotOnly,
        )
    }

    fn from_authenticated_boundary_snapshot(
        validated: &ValidatedAuthorityBootstrap,
        binding: &AuthenticatedFinalCommitPolicyBinding,
        native: native_snapshot::NativeAuthenticatedInstallHelperSourceReadback,
        native_object: AuthenticatedInstallHelperSourceObject,
    ) -> Result<Self, AuthorityBootstrapError> {
        use std::os::windows::ffi::OsStrExt;

        if binding.generation() != validated.generation()
            || binding.service_process_id() != validated.service_process_id()
            || binding.service_process_creation_time() != validated.service_process_creation_time()
            || binding.install_helper_binary_sha256() != validated.install_helper_binary_sha256()
            || binding.install_helper_binary_byte_length()
                != validated.install_helper_binary_byte_length
            || binding.installed_layout_sha256() != validated.installed_layout_sha256()
            || native.descriptor != validated.install_helper_binary_descriptor()?
            || native.install_helper_path.as_os_str().is_empty()
            || !native.install_helper_path.is_absolute()
            || native.install_helper_path.components().any(|component| {
                matches!(
                    component,
                    std::path::Component::CurDir | std::path::Component::ParentDir
                )
            })
            || native.volume_serial == 0
            || native.file_id == 0
            || native.link_count != 1
        {
            return Err(AuthorityBootstrapError(
                "authority_install_helper_source_readback_invalid",
            ));
        }
        let mut digest = Sha256::new();
        digest.update(AUTHENTICATED_INSTALL_HELPER_SOURCE_BINDING_DOMAIN);
        digest.update(validated.generation());
        digest.update(validated.service_process_id().to_be_bytes());
        digest.update(validated.service_process_creation_time().to_be_bytes());
        digest.update(validated.installed_layout_sha256());
        digest.update(binding.final_commit_receipt_sha256());
        digest.update(native.descriptor.sha256());
        digest.update(native.descriptor.byte_length().to_be_bytes());
        digest.update(native.volume_serial.to_be_bytes());
        digest.update(native.file_id.to_be_bytes());
        digest.update(native.link_count.to_be_bytes());
        let path_words = native.install_helper_path.as_os_str().encode_wide();
        let mut path_length = 0u64;
        for word in path_words {
            digest.update(word.to_be_bytes());
            path_length = path_length.checked_add(1).ok_or(AuthorityBootstrapError(
                "authority_install_helper_source_readback_invalid",
            ))?;
        }
        digest.update(path_length.to_be_bytes());
        let source_binding_sha256 = digest.finalize().into();
        Ok(Self {
            generation: *validated.generation(),
            service_process_id: validated.service_process_id(),
            service_process_started_at: validated.service_process_creation_time(),
            install_helper_path: native.install_helper_path,
            install_helper_sha256: *native.descriptor.sha256(),
            install_helper_byte_length: native.descriptor.byte_length(),
            volume_serial: native.volume_serial,
            file_id: native.file_id,
            link_count: native.link_count,
            installed_layout_sha256: *validated.installed_layout_sha256(),
            final_commit_receipt_sha256: *binding.final_commit_receipt_sha256(),
            source_binding_sha256,
            native: native_object,
        })
    }

    pub(crate) fn verify_still_stable(&self) -> Result<(), AuthorityBootstrapError> {
        match &self.native {
            AuthenticatedInstallHelperSourceObject::Held(native) => native.verify(),
            #[cfg(test)]
            AuthenticatedInstallHelperSourceObject::SnapshotOnly => Err(AuthorityBootstrapError(
                "authority_install_helper_source_lease_unavailable",
            )),
        }
    }

    pub(crate) fn generation(&self) -> &[u8; 32] {
        &self.generation
    }

    pub(crate) fn service_process_id(&self) -> u32 {
        self.service_process_id
    }

    pub(crate) fn service_process_started_at(&self) -> u64 {
        self.service_process_started_at
    }

    pub(crate) fn install_helper_path(&self) -> &Path {
        &self.install_helper_path
    }

    pub(crate) fn install_helper_sha256(&self) -> &[u8; 32] {
        &self.install_helper_sha256
    }

    pub(crate) fn install_helper_byte_length(&self) -> u64 {
        self.install_helper_byte_length
    }

    pub(crate) fn volume_serial(&self) -> u32 {
        self.volume_serial
    }

    pub(crate) fn file_id(&self) -> u64 {
        self.file_id
    }

    pub(crate) fn link_count(&self) -> u32 {
        self.link_count
    }

    pub(crate) fn installed_layout_sha256(&self) -> &[u8; 32] {
        &self.installed_layout_sha256
    }

    pub(crate) fn final_commit_receipt_sha256(&self) -> &[u8; 32] {
        &self.final_commit_receipt_sha256
    }

    pub(crate) fn source_binding_sha256(&self) -> &[u8; 32] {
        &self.source_binding_sha256
    }
}

#[cfg(windows)]
fn authenticated_final_commit_policy_binding(
    validated: &ValidatedAuthorityBootstrap,
    published_runtime_binding: VerifiedPublishedRuntimeBindingProjection,
    final_commit_receipt_sha256: [u8; 32],
) -> Result<AuthenticatedFinalCommitPolicyBinding, AuthorityBootstrapError> {
    validate_published_runtime_binding(validated, published_runtime_binding)?;
    if final_commit_receipt_sha256.iter().all(|value| *value == 0) {
        return Err(AuthorityBootstrapError(
            "authority_final_commit_receipt_not_verified",
        ));
    }
    Ok(AuthenticatedFinalCommitPolicyBinding {
        generation: *validated.generation(),
        signer_key_id: *validated.signer_key_id(),
        protected_manifest_sha256: *validated.protected_manifest_sha256(),
        installed_layout_sha256: *validated.installed_layout_sha256(),
        exact_service_configuration_sha256: *validated.exact_service_configuration_sha256(),
        service_binary_sha256: *validated.service_binary_sha256(),
        controller_binary_sha256: *validated.controller_binary_sha256(),
        controller_binary_byte_length: validated.controller_binary_byte_length,
        install_helper_binary_sha256: *validated.install_helper_binary_sha256(),
        install_helper_binary_byte_length: validated.install_helper_binary_byte_length,
        lifecycle_driver_binary_sha256: *validated.lifecycle_driver_binary_sha256(),
        lifecycle_driver_binary_byte_length: validated.lifecycle_driver_binary_byte_length,
        bridge_launcher_binary_sha256: *validated.bridge_launcher_binary_sha256(),
        bridge_launcher_binary_byte_length: validated.bridge_launcher_binary_byte_length,
        ledger_identity: *validated.ledger_identity(),
        service_process_id: validated.service_process_id(),
        service_process_creation_time: validated.service_process_creation_time(),
        final_commit_receipt_sha256,
        published_runtime_binding,
        runtime_source_manifest: validated.runtime_source_manifest,
        runner_policy_state: validated.runner_policy_state,
        runner_policy_sealed_identity: validated.runner_policy_sealed_identity,
    })
}

impl ValidatedAuthorityBootstrap {
    pub(crate) fn generation(&self) -> &[u8; 32] {
        &self.generation
    }

    pub(crate) fn signer_key_id(&self) -> &[u8; 32] {
        &self.signer_key_id
    }

    pub(crate) fn protected_manifest_sha256(&self) -> &[u8; 32] {
        &self.activation_manifest_sha256
    }

    pub(crate) fn installed_layout_sha256(&self) -> &[u8; 32] {
        &self.installed_layout_sha256
    }

    pub(crate) fn plan_sha256(&self) -> &[u8; 32] {
        &self.plan_sha256
    }

    pub(crate) fn transaction_sha256(&self) -> &[u8; 32] {
        &self.transaction_sha256
    }

    pub(crate) fn active_head_sha256(&self) -> &[u8; 32] {
        &self.active_head_sha256
    }

    pub(crate) fn activation_epoch(&self) -> u64 {
        self.activation_epoch
    }

    pub(crate) fn exact_service_configuration_sha256(&self) -> &[u8; 32] {
        &self.exact_service_configuration_sha256
    }

    pub(crate) fn ledger_identity(&self) -> &[u8; 32] {
        &self.ledger_identity
    }

    pub(crate) fn ledger_byte_length(&self) -> u64 {
        self.ledger_byte_length
    }

    pub(crate) fn ledger_sha256(&self) -> &[u8; 32] {
        &self.ledger_sha256
    }

    pub(crate) fn ledger_anchor_byte_length(&self) -> u64 {
        self.ledger_anchor_byte_length
    }

    pub(crate) fn ledger_anchor_sha256(&self) -> &[u8; 32] {
        &self.ledger_anchor_sha256
    }

    pub(crate) fn service_binary_sha256(&self) -> &[u8; 32] {
        &self.service_binary_sha256
    }

    pub(crate) fn controller_binary_sha256(&self) -> &[u8; 32] {
        &self.controller_binary_sha256
    }

    pub(crate) fn controller_binary_byte_length(&self) -> u64 {
        self.controller_binary_byte_length
    }

    pub(crate) fn install_helper_binary_sha256(&self) -> &[u8; 32] {
        &self.install_helper_binary_sha256
    }

    pub(crate) fn install_helper_binary_byte_length(&self) -> u64 {
        self.install_helper_binary_byte_length
    }

    pub(crate) fn lifecycle_driver_binary_sha256(&self) -> &[u8; 32] {
        &self.lifecycle_driver_binary_sha256
    }

    pub(crate) fn lifecycle_driver_binary_byte_length(&self) -> u64 {
        self.lifecycle_driver_binary_byte_length
    }

    pub(crate) fn bridge_launcher_binary_sha256(&self) -> &[u8; 32] {
        &self.bridge_launcher_binary_sha256
    }

    pub(crate) fn bridge_launcher_binary_byte_length(&self) -> u64 {
        self.bridge_launcher_binary_byte_length
    }

    fn controller_binary_descriptor(
        &self,
    ) -> Result<AuthorityPayloadDigest, AuthorityBootstrapError> {
        AuthorityPayloadDigest::new(
            self.controller_binary_sha256,
            self.controller_binary_byte_length,
        )
        .map_err(|_| AuthorityBootstrapError("authority_controller_source_readback_invalid"))
    }

    fn install_helper_binary_descriptor(
        &self,
    ) -> Result<AuthorityPayloadDigest, AuthorityBootstrapError> {
        AuthorityPayloadDigest::new(
            self.install_helper_binary_sha256,
            self.install_helper_binary_byte_length,
        )
        .map_err(|_| AuthorityBootstrapError("authority_install_helper_source_readback_invalid"))
    }

    fn lifecycle_driver_binary_descriptor(
        &self,
    ) -> Result<AuthorityPayloadDigest, AuthorityBootstrapError> {
        AuthorityPayloadDigest::new(
            self.lifecycle_driver_binary_sha256,
            self.lifecycle_driver_binary_byte_length,
        )
        .map_err(|_| AuthorityBootstrapError("authority_lifecycle_driver_readback_invalid"))
    }

    fn bridge_launcher_binary_descriptor(
        &self,
    ) -> Result<AuthorityPayloadDigest, AuthorityBootstrapError> {
        AuthorityPayloadDigest::new(
            self.bridge_launcher_binary_sha256,
            self.bridge_launcher_binary_byte_length,
        )
        .map_err(|_| AuthorityBootstrapError("authority_bridge_launcher_readback_invalid"))
    }

    pub(crate) fn service_process_id(&self) -> u32 {
        self.service_process_id
    }

    pub(crate) fn service_process_creation_time(&self) -> u64 {
        self.service_process_creation_time
    }

    pub(crate) fn active_ticket_count(&self) -> usize {
        self.active_ticket_count
    }

    pub(crate) fn receipt_sha256(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(b"vrcforge-authority-service-bootstrap-receipt-v6\0");
        digest.update(self.generation);
        digest.update(self.plan_sha256);
        digest.update(self.installed_layout_sha256);
        digest.update(self.transaction_sha256);
        digest.update(self.active_head_sha256);
        digest.update(self.activation_manifest_sha256);
        digest.update(self.activation_epoch.to_be_bytes());
        digest.update(self.exact_service_configuration_sha256);
        digest.update(self.signer_key_id);
        digest.update(self.ledger_identity);
        digest.update(self.service_binary_sha256);
        digest.update(self.controller_binary_sha256);
        digest.update(self.controller_binary_byte_length.to_be_bytes());
        digest.update(self.install_helper_binary_sha256);
        digest.update(self.install_helper_binary_byte_length.to_be_bytes());
        digest.update(self.lifecycle_driver_binary_sha256);
        digest.update(self.lifecycle_driver_binary_byte_length.to_be_bytes());
        digest.update(self.bridge_launcher_binary_sha256);
        digest.update(self.bridge_launcher_binary_byte_length.to_be_bytes());
        digest.update(self.runtime_source_manifest.sha256());
        digest.update(self.runtime_source_manifest.byte_length().to_be_bytes());
        digest.update(self.runner_policy_state.generation_sha256());
        digest.update(self.runner_policy_state.transaction_sha256());
        digest.update(self.runner_policy_state.byte_length().to_be_bytes());
        digest.update(self.runner_policy_state.bytes_sha256());
        digest.update(self.runner_policy_state.binding_sha256());
        digest.update(
            self.runner_policy_sealed_identity
                .volume_serial()
                .to_be_bytes(),
        );
        digest.update(self.runner_policy_sealed_identity.file_id());
        digest.update(
            self.runner_policy_sealed_identity
                .link_count()
                .to_be_bytes(),
        );
        digest.update(
            self.runner_policy_sealed_identity
                .attributes()
                .to_be_bytes(),
        );
        digest.update(self.service_process_id.to_be_bytes());
        digest.update(self.service_process_creation_time.to_be_bytes());
        digest.finalize().into()
    }

    #[cfg(windows)]
    fn protected_ledger_readback_sha256(&self) -> Result<[u8; 32], AuthorityBootstrapError> {
        if self.ledger_identity.iter().all(|value| *value == 0)
            || self.ledger_frame_count == 0
            || self.ledger_byte_length == 0
            || self.ledger_sha256.iter().all(|value| *value == 0)
            || self.ledger_anchor_byte_length == 0
            || self.ledger_anchor_sha256.iter().all(|value| *value == 0)
        {
            return Err(AuthorityBootstrapError(
                "authority_ledger_readback_not_authenticated",
            ));
        }
        let active_ticket_count = u64::try_from(self.active_ticket_count)
            .map_err(|_| AuthorityBootstrapError("authority_ledger_readback_not_authenticated"))?;
        let mut digest = Sha256::new();
        digest.update(AUTHENTICATED_LEDGER_READBACK_DOMAIN);
        digest.update(self.generation);
        digest.update(self.signer_key_id);
        digest.update(self.ledger_identity);
        digest.update(self.ledger_frame_count.to_be_bytes());
        digest.update(self.ledger_byte_length.to_be_bytes());
        digest.update(self.ledger_sha256);
        digest.update(self.ledger_anchor_byte_length.to_be_bytes());
        digest.update(self.ledger_anchor_sha256);
        digest.update(active_ticket_count.to_be_bytes());
        Ok(digest.finalize().into())
    }
}

pub(crate) trait BootstrapSignatureVerifier {
    fn verify(
        &mut self,
        generation: &[u8; 32],
        input: &ProtectedManifestSignatureInput,
    ) -> Result<(), AuthorityBootstrapError>;
}

fn validate_bootstrap_snapshot<V: BootstrapSignatureVerifier>(
    layout: &AuthorityLayout,
    snapshot: &AuthorityBootstrapSnapshot,
    verifier: &mut V,
) -> Result<ValidatedAuthorityBootstrap, AuthorityBootstrapError> {
    if snapshot.schema != SERVICE_BOOTSTRAP_SCHEMA {
        return Err(AuthorityBootstrapError(
            "authority_service_bootstrap_schema_invalid",
        ));
    }
    validate_artifact_readbacks(&snapshot.protected_artifacts)?;
    if !snapshot.service_process_identity_exact {
        return Err(AuthorityBootstrapError(
            "authority_service_process_identity_not_exact",
        ));
    }
    if snapshot.service_process_id == 0 || snapshot.service_process_creation_time == 0 {
        return Err(AuthorityBootstrapError(
            "authority_service_process_identity_not_exact",
        ));
    }
    validate_ledger_pair_readback(
        snapshot.ledger_frame_count,
        snapshot.ledger_byte_length,
        snapshot.ledger_sha256,
        snapshot.ledger_anchor_byte_length,
        snapshot.ledger_anchor_sha256,
    )?;

    let head = ProtectedActiveHead::parse_canonical(&snapshot.active_head_bytes)
        .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?;
    let generation = head
        .generation()
        .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?;
    let activation_epoch = head.activation_epoch();
    if activation_epoch == 0
        || activation_epoch > MAX_BOOTSTRAP_ACTIVATION_EPOCH
        || (activation_epoch == 1
            && head
                .previous_head_sha256()
                .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?
                .is_some())
        || (activation_epoch > 1
            && head
                .previous_head_sha256()
                .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?
                .is_none())
    {
        return Err(AuthorityBootstrapError(
            "authority_service_bootstrap_update_chain_invalid",
        ));
    }
    let expected_chain_length = usize::try_from(activation_epoch)
        .map_err(|_| AuthorityBootstrapError("authority_service_bootstrap_update_chain_invalid"))?;
    if snapshot.activation_history.len().checked_add(1) != Some(expected_chain_length) {
        return Err(AuthorityBootstrapError(
            "authority_service_bootstrap_update_chain_invalid",
        ));
    }
    let mut chain_generations = snapshot
        .activation_history
        .iter()
        .map(|entry| entry.generation)
        .collect::<Vec<_>>();
    chain_generations.push(generation);
    validate_activation_directory(&snapshot.activation_directory_names, &chain_generations)?;

    if snapshot.current_service_image != snapshot.installed_content.service() {
        return Err(AuthorityBootstrapError(
            "authority_service_image_binding_mismatch",
        ));
    }
    snapshot.candidate_service_process.validate()?;
    if snapshot.candidate_service_process.process_id() != snapshot.service_process_id
        || snapshot.candidate_service_process.process_creation_time()
            != snapshot.service_process_creation_time
        || snapshot.candidate_service_process.image_sha256()
            != snapshot.current_service_image.sha256()
        || snapshot.candidate_service_process.image_byte_length()
            != snapshot.current_service_image.byte_length()
    {
        return Err(AuthorityBootstrapError(
            "authority_candidate_service_process_binding_mismatch",
        ));
    }
    let preview = preview_install(layout, snapshot.installed_content.clone())
        .map_err(|_| AuthorityBootstrapError("authority_generation_recompute_failed"))?;
    let exact_service_plan = preview
        .exact_target_service_plan()
        .map_err(|_| AuthorityBootstrapError("authority_service_configuration_not_verified"))?;
    let preview_generation = preview
        .generation_sha256()
        .map_err(|_| AuthorityBootstrapError("authority_generation_recompute_failed"))?;
    let head_plan = head
        .plan_sha256()
        .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?;
    let head_transaction = head
        .transaction_sha256()
        .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?;
    if snapshot.runner_policy_state.generation_sha256() != generation
        || snapshot.runner_policy_state.transaction_sha256() != head_transaction
        || snapshot.runner_policy_state.byte_length() == 0
        || snapshot
            .runner_policy_state
            .bytes_sha256()
            .iter()
            .all(|value| *value == 0)
        || snapshot
            .runner_policy_state
            .binding_sha256()
            .iter()
            .all(|value| *value == 0)
        || snapshot.runner_policy_sealed_identity.validate().is_err()
    {
        return Err(AuthorityBootstrapError(
            "authority_runner_policy_bootstrap_binding_mismatch",
        ));
    }
    let exact_terminal = Some(AuthorityBootstrapTerminalBinding {
        generation,
        plan_sha256: head_plan,
        transaction_sha256: head_transaction,
        activation_epoch,
    });
    if snapshot.maintenance_terminal_binding != exact_terminal {
        return Err(AuthorityBootstrapError(
            "authority_maintenance_journal_not_terminal",
        ));
    }
    if preview_generation != generation {
        return Err(AuthorityBootstrapError(
            "authority_active_head_plan_binding_mismatch",
        ));
    }
    if activation_epoch == 1 {
        let preview_plan = preview
            .plan_sha256()
            .map_err(|_| AuthorityBootstrapError("authority_plan_recompute_failed"))?;
        let preview_transaction = preview
            .transaction_sha256()
            .map_err(|_| AuthorityBootstrapError("authority_transaction_recompute_failed"))?;
        if head_plan != preview_plan || head_transaction != preview_transaction {
            return Err(AuthorityBootstrapError(
                "authority_active_head_plan_binding_mismatch",
            ));
        }
    }
    // Update plans include the predecessor's historical live-process tuple,
    // which is deliberately not reconstructed after restart. Later epochs are
    // admitted only through the complete signed activation chain plus the
    // exact protected terminal binding checked above.

    let mut predecessor = None;
    for (index, historical) in snapshot.activation_history.iter().enumerate() {
        let expected_epoch = u64::try_from(index)
            .ok()
            .and_then(|value| value.checked_add(1))
            .ok_or(AuthorityBootstrapError(
                "authority_service_bootstrap_update_chain_invalid",
            ))?;
        predecessor = Some(validate_generation_link(
            historical.generation,
            &historical.trust_manifest_bytes,
            &historical.activation_manifest_bytes,
            &historical.key_readback,
            expected_epoch,
            predecessor,
            verifier,
        )?);
    }
    let current = validate_generation_link(
        generation,
        &snapshot.trust_manifest_bytes,
        &snapshot.activation_manifest_bytes,
        &snapshot.key_readback,
        activation_epoch,
        predecessor,
        verifier,
    )?;
    if current.activation_sha256
        != head
            .activation_manifest_sha256()
            .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?
    {
        return Err(AuthorityBootstrapError(
            "authority_active_head_activation_binding_mismatch",
        ));
    }
    if snapshot.ledger_identity != current.ledger_identity {
        return Err(AuthorityBootstrapError(
            "authority_ledger_identity_mismatch",
        ));
    }

    Ok(ValidatedAuthorityBootstrap {
        generation,
        plan_sha256: head_plan,
        installed_layout_sha256: preview.installed_layout_sha256(),
        transaction_sha256: head_transaction,
        active_head_sha256: Sha256::digest(&snapshot.active_head_bytes).into(),
        activation_manifest_sha256: current.activation_sha256,
        activation_epoch,
        exact_service_configuration_sha256: exact_service_plan.exact_service_configuration_sha256(),
        signer_key_id: current.signer_key_id,
        ledger_identity: current.ledger_identity,
        service_binary_sha256: *snapshot.current_service_image.sha256(),
        controller_binary_sha256: *snapshot.installed_content.controller().sha256(),
        controller_binary_byte_length: snapshot.installed_content.controller().byte_length(),
        install_helper_binary_sha256: *snapshot.installed_content.install_helper().sha256(),
        install_helper_binary_byte_length: snapshot
            .installed_content
            .install_helper()
            .byte_length(),
        lifecycle_driver_binary_sha256: *snapshot.installed_content.lifecycle_driver().sha256(),
        lifecycle_driver_binary_byte_length: snapshot
            .installed_content
            .lifecycle_driver()
            .byte_length(),
        bridge_launcher_binary_sha256: *snapshot.installed_content.bridge_launcher().sha256(),
        bridge_launcher_binary_byte_length: snapshot
            .installed_content
            .bridge_launcher()
            .byte_length(),
        runtime_source_manifest: snapshot.installed_content.runtime_source_manifest(),
        runner_policy_state: snapshot.runner_policy_state,
        runner_policy_sealed_identity: snapshot.runner_policy_sealed_identity,
        service_process_id: snapshot.service_process_id,
        service_process_creation_time: snapshot.service_process_creation_time,
        ledger_frame_count: snapshot.ledger_frame_count,
        ledger_byte_length: snapshot.ledger_byte_length,
        ledger_sha256: snapshot.ledger_sha256,
        ledger_anchor_byte_length: snapshot.ledger_anchor_byte_length,
        ledger_anchor_sha256: snapshot.ledger_anchor_sha256,
        active_ticket_count: snapshot.active_ticket_count,
    })
}

#[cfg(windows)]
fn validate_published_runtime_binding(
    validated: &ValidatedAuthorityBootstrap,
    binding: VerifiedPublishedRuntimeBindingProjection,
) -> Result<(), AuthorityBootstrapError> {
    let expected_operation = if validated.activation_epoch == 1 {
        AuthorityMaintenanceOperation::Install
    } else {
        AuthorityMaintenanceOperation::Update
    };
    if binding.generation_sha256() != validated.generation
        || binding.plan_sha256() != validated.plan_sha256
        || binding.transaction_sha256() != validated.transaction_sha256
        || binding.expected_active_head_replacement_sha256() != validated.active_head_sha256
        || binding.expected_activation_manifest_sha256() != validated.activation_manifest_sha256
        || binding.expected_activation_epoch() != validated.activation_epoch
        || binding.exact_service_configuration_sha256()
            != validated.exact_service_configuration_sha256
        || binding.expected_service_image_sha256() != validated.service_binary_sha256
        || binding.expected_runner_policy_state_byte_length()
            != validated.runner_policy_state.byte_length()
        || binding.expected_runner_policy_state_bytes_sha256()
            != validated.runner_policy_state.bytes_sha256()
        || binding.expected_runner_policy_state_binding_sha256()
            != validated.runner_policy_state.binding_sha256()
        || binding.runner_policy_sealed_volume_serial()
            != validated.runner_policy_sealed_identity.volume_serial()
        || binding.runner_policy_sealed_file_id()
            != validated.runner_policy_sealed_identity.file_id()
        || binding.runner_policy_sealed_link_count()
            != validated.runner_policy_sealed_identity.link_count()
        || binding.runner_policy_sealed_attributes()
            != validated.runner_policy_sealed_identity.attributes()
        || binding.operation() != expected_operation
    {
        return Err(AuthorityBootstrapError(
            "authority_final_commit_runtime_binding_mismatch",
        ));
    }
    Ok(())
}

#[cfg(windows)]
fn validate_exact_published_runtime_binding(
    validated: &ValidatedAuthorityBootstrap,
    expected: VerifiedPublishedRuntimeBindingProjection,
    observed: VerifiedPublishedRuntimeBindingProjection,
) -> Result<(), AuthorityBootstrapError> {
    validate_published_runtime_binding(validated, expected)?;
    validate_published_runtime_binding(validated, observed)?;
    if observed != expected
        || observed.complete_binding_sha256() != expected.complete_binding_sha256()
    {
        return Err(AuthorityBootstrapError(
            "authority_final_commit_complete_projection_mismatch",
        ));
    }
    Ok(())
}

fn validate_candidate_bootstrap_snapshot<V: BootstrapSignatureVerifier>(
    layout: &AuthorityLayout,
    snapshot: &CandidateAuthorityBootstrapSnapshot,
    verifier: &mut V,
    locator: CandidateServiceStartLocator,
) -> Result<activation::CandidateActivationBinding, AuthorityBootstrapError> {
    if snapshot.schema != SERVICE_BOOTSTRAP_SCHEMA {
        return Err(AuthorityBootstrapError(
            "authority_service_bootstrap_schema_invalid",
        ));
    }
    validate_artifact_readbacks_for(&snapshot.protected_artifacts, &CANDIDATE_REQUIRED_ARTIFACTS)?;
    if !snapshot.service_process_identity_exact
        || snapshot.service_process_id == 0
        || snapshot.service_process_creation_time == 0
    {
        return Err(AuthorityBootstrapError(
            "authority_service_process_identity_not_exact",
        ));
    }
    validate_ledger_pair_readback(
        snapshot.ledger_frame_count,
        snapshot.ledger_byte_length,
        snapshot.ledger_sha256,
        snapshot.ledger_anchor_byte_length,
        snapshot.ledger_anchor_sha256,
    )?;

    let (record, issuer) = match &snapshot.credential_readback {
        CandidateCredentialReadback::Record { record, issuer, .. } => (record, *issuer),
        CandidateCredentialReadback::None => {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_missing",
            ))
        }
    };
    let binding = record.binding()?;
    locator.validate_binding(binding)?;
    if issuer != binding.issuer() {
        return Err(AuthorityBootstrapError(
            "authority_candidate_issuer_binding_mismatch",
        ));
    }
    let generation = *binding.generation();
    let activation_epoch = binding.activation_epoch();
    if activation_epoch == 0 || activation_epoch > MAX_BOOTSTRAP_ACTIVATION_EPOCH {
        return Err(AuthorityBootstrapError(
            "authority_service_bootstrap_update_chain_invalid",
        ));
    }
    let expected_chain_length = usize::try_from(activation_epoch)
        .map_err(|_| AuthorityBootstrapError("authority_service_bootstrap_update_chain_invalid"))?;
    if snapshot.activation_history.len().checked_add(1) != Some(expected_chain_length) {
        return Err(AuthorityBootstrapError(
            "authority_service_bootstrap_update_chain_invalid",
        ));
    }
    let mut chain_generations = snapshot
        .activation_history
        .iter()
        .map(|entry| entry.generation)
        .collect::<Vec<_>>();
    chain_generations.push(generation);
    let prior_head_present = match snapshot.prior_head {
        CandidatePriorHeadObservation::Absent if activation_epoch == 1 => false,
        CandidatePriorHeadObservation::Present { head_sha256 }
            if activation_epoch > 1 && head_sha256 == *binding.active_head_sha256() =>
        {
            true
        }
        _ => {
            return Err(AuthorityBootstrapError(
                "authority_candidate_active_head_prior_mismatch",
            ))
        }
    };
    validate_candidate_activation_directory(
        &snapshot.activation_directory_names,
        &chain_generations,
        prior_head_present,
    )?;

    if snapshot.current_service_image != snapshot.installed_content.service() {
        return Err(AuthorityBootstrapError(
            "authority_service_image_binding_mismatch",
        ));
    }
    snapshot.candidate_service_process.validate()?;
    if snapshot.candidate_service_process.process_id() != snapshot.service_process_id
        || snapshot.candidate_service_process.process_creation_time()
            != snapshot.service_process_creation_time
        || snapshot.candidate_service_process.image() != binding.target_service_image()
        || snapshot.candidate_service_process.image_sha256()
            != snapshot.current_service_image.sha256()
        || snapshot.candidate_service_process.image_byte_length()
            != snapshot.current_service_image.byte_length()
    {
        return Err(AuthorityBootstrapError(
            "authority_candidate_service_process_binding_mismatch",
        ));
    }

    let preview = preview_install(layout, snapshot.installed_content.clone())
        .map_err(|_| AuthorityBootstrapError("authority_generation_recompute_failed"))?;
    if preview
        .generation_sha256()
        .map_err(|_| AuthorityBootstrapError("authority_generation_recompute_failed"))?
        != generation
    {
        return Err(AuthorityBootstrapError(
            "authority_candidate_generation_binding_mismatch",
        ));
    }
    if activation_epoch == 1
        && (preview
            .plan_sha256()
            .map_err(|_| AuthorityBootstrapError("authority_plan_recompute_failed"))?
            != *binding.plan_sha256()
            || preview
                .transaction_sha256()
                .map_err(|_| AuthorityBootstrapError("authority_transaction_recompute_failed"))?
                != *binding.transaction_sha256())
    {
        return Err(AuthorityBootstrapError(
            "authority_candidate_plan_binding_mismatch",
        ));
    }

    let mut predecessor = None;
    for (index, historical) in snapshot.activation_history.iter().enumerate() {
        let expected_epoch = u64::try_from(index)
            .ok()
            .and_then(|value| value.checked_add(1))
            .ok_or(AuthorityBootstrapError(
                "authority_service_bootstrap_update_chain_invalid",
            ))?;
        predecessor = Some(validate_generation_link(
            historical.generation,
            &historical.trust_manifest_bytes,
            &historical.activation_manifest_bytes,
            &historical.key_readback,
            expected_epoch,
            predecessor,
            verifier,
        )?);
    }
    let current = validate_generation_link(
        generation,
        &snapshot.trust_manifest_bytes,
        &snapshot.activation_manifest_bytes,
        &snapshot.key_readback,
        activation_epoch,
        predecessor,
        verifier,
    )?;
    if current.activation_sha256 != *binding.activation_manifest_sha256() {
        return Err(AuthorityBootstrapError(
            "authority_candidate_activation_binding_mismatch",
        ));
    }
    if snapshot.ledger_identity != current.ledger_identity {
        return Err(AuthorityBootstrapError(
            "authority_ledger_identity_mismatch",
        ));
    }
    Ok(binding)
}

#[derive(Debug, Clone, Copy)]
struct VerifiedBootstrapGenerationLink {
    generation: [u8; 32],
    activation_sha256: [u8; 32],
    activation_epoch: u64,
    signer_key_id: [u8; 32],
    ledger_identity: [u8; 32],
}

#[allow(clippy::too_many_arguments)]
fn validate_generation_link<V: BootstrapSignatureVerifier>(
    expected_generation: [u8; 32],
    trust_manifest_bytes: &[u8],
    activation_manifest_bytes: &[u8],
    key_readback: &VerifiedAuthorityKeyReadback,
    expected_epoch: u64,
    predecessor: Option<VerifiedBootstrapGenerationLink>,
    verifier: &mut V,
) -> Result<VerifiedBootstrapGenerationLink, AuthorityBootstrapError> {
    let trust = ProtectedDetachedManifestFile::parse_canonical(trust_manifest_bytes)
        .map_err(|_| AuthorityBootstrapError("authority_trust_manifest_not_verified"))?;
    let activation = ProtectedDetachedManifestFile::parse_canonical(activation_manifest_bytes)
        .map_err(|_| AuthorityBootstrapError("authority_activation_manifest_not_verified"))?;
    let trust_signature = trust
        .signature_input()
        .map_err(|_| AuthorityBootstrapError("authority_trust_manifest_not_verified"))?;
    let activation_signature = activation
        .signature_input()
        .map_err(|_| AuthorityBootstrapError("authority_activation_manifest_not_verified"))?;
    let expected_key_id = *key_readback.signer_key_id();
    let expected_public_key = *key_readback.public_key_sec1();
    if trust_signature.signer_key_id != expected_key_id
        || activation_signature.signer_key_id != expected_key_id
    {
        return Err(AuthorityBootstrapError(
            "authority_manifest_signer_key_mismatch",
        ));
    }
    let expected_ledger_identity =
        derive_ledger_identity(&expected_generation, &expected_key_id)
            .map_err(|_| AuthorityBootstrapError("authority_ledger_identity_invalid"))?;
    match trust
        .unsigned_payload()
        .map_err(|_| AuthorityBootstrapError("authority_trust_manifest_not_verified"))?
    {
        CanonicalUnsignedManifestPayload::Trust {
            generation,
            signer_key_id,
            signer_public_key_sec1,
            ledger_identity,
            created_epoch,
            valid,
            revoked,
        } if generation == expected_generation
            && signer_key_id == expected_key_id
            && signer_public_key_sec1 == expected_public_key
            && ledger_identity == expected_ledger_identity
            && created_epoch == expected_epoch
            && valid
            && !revoked => {}
        _ => {
            return Err(AuthorityBootstrapError(
                "authority_trust_manifest_binding_invalid",
            ))
        }
    }
    let expected_previous = predecessor.map(|value| {
        (
            value.generation,
            value.activation_sha256,
            value.activation_epoch,
        )
    });
    match activation
        .unsigned_payload()
        .map_err(|_| AuthorityBootstrapError("authority_activation_manifest_not_verified"))?
    {
        CanonicalUnsignedManifestPayload::Activation {
            generation,
            trust_manifest_sha256,
            signer_key_id,
            activated_epoch,
            previous_generation,
            previous_activation_sha256,
            previous_activation_epoch,
            valid,
            revoked,
        } if generation == expected_generation
            && trust_manifest_sha256 == trust_signature.digest
            && signer_key_id == expected_key_id
            && activated_epoch == expected_epoch
            && match expected_previous {
                None => {
                    previous_generation.is_none()
                        && previous_activation_sha256.is_none()
                        && previous_activation_epoch.is_none()
                }
                Some((generation, digest, epoch)) => {
                    previous_generation == Some(generation)
                        && previous_activation_sha256 == Some(digest)
                        && previous_activation_epoch == Some(epoch)
                }
            }
            && valid
            && !revoked => {}
        _ => {
            return Err(AuthorityBootstrapError(
                "authority_manifest_predecessor_not_verified",
            ))
        }
    }
    verifier.verify(&expected_generation, &trust_signature)?;
    verifier.verify(&expected_generation, &activation_signature)?;
    Ok(VerifiedBootstrapGenerationLink {
        generation: expected_generation,
        activation_sha256: activation_signature.digest,
        activation_epoch: expected_epoch,
        signer_key_id: expected_key_id,
        ledger_identity: expected_ledger_identity,
    })
}

fn validate_artifact_readbacks(
    readbacks: &[ProtectedArtifactReadback],
) -> Result<(), AuthorityBootstrapError> {
    validate_artifact_readbacks_for(readbacks, &REQUIRED_ARTIFACTS)
}

fn validate_ledger_pair_readback(
    frame_count: u64,
    ledger_byte_length: u64,
    ledger_sha256: [u8; 32],
    anchor_byte_length: u64,
    anchor_sha256: [u8; 32],
) -> Result<(), AuthorityBootstrapError> {
    if frame_count == 0 {
        return Err(AuthorityBootstrapError("authority_ledger_genesis_missing"));
    }
    let expected_ledger_length =
        frame_count
            .checked_mul(FRAME_SIZE as u64)
            .ok_or(AuthorityBootstrapError(
                "authority_ledger_pair_readback_invalid",
            ))?;
    let expected_anchor_length = frame_count
        .checked_mul(2)
        .and_then(|value| value.checked_mul(ANCHOR_RECORD_SIZE as u64))
        .ok_or(AuthorityBootstrapError(
            "authority_ledger_pair_readback_invalid",
        ))?;
    if ledger_byte_length != expected_ledger_length
        || anchor_byte_length != expected_anchor_length
        || ledger_sha256.iter().all(|byte| *byte == 0)
        || anchor_sha256.iter().all(|byte| *byte == 0)
        || ledger_sha256 == anchor_sha256
    {
        return Err(AuthorityBootstrapError(
            "authority_ledger_pair_readback_invalid",
        ));
    }
    Ok(())
}

fn validate_artifact_readbacks_for(
    readbacks: &[ProtectedArtifactReadback],
    required: &[BootstrapArtifactKind],
) -> Result<(), AuthorityBootstrapError> {
    if readbacks.len() != required.len() {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_set_invalid",
        ));
    }
    let kinds = readbacks
        .iter()
        .map(|value| value.kind)
        .collect::<BTreeSet<_>>();
    if kinds != required.iter().copied().collect() {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_set_invalid",
        ));
    }
    if readbacks.iter().copied().any(|value| !value.is_exact()) {
        return Err(AuthorityBootstrapError(
            "authority_protected_artifact_readback_incomplete",
        ));
    }
    Ok(())
}

fn validate_activation_directory(
    names: &[String],
    generations: &[[u8; 32]],
) -> Result<(), AuthorityBootstrapError> {
    if generations.is_empty()
        || generations.len() > MAX_BOOTSTRAP_ACTIVATION_EPOCH as usize
        || generations.iter().collect::<BTreeSet<_>>().len() != generations.len()
    {
        return Err(AuthorityBootstrapError(
            "authority_activation_directory_not_unique",
        ));
    }
    let mut expected = generations
        .iter()
        .map(|generation| format!("{}.json", hex_lower(generation)))
        .collect::<Vec<_>>();
    expected.push("head.json".to_string());
    expected.sort();
    let mut actual = names.to_vec();
    actual.sort();
    if actual != expected {
        return Err(AuthorityBootstrapError(
            "authority_activation_directory_not_unique",
        ));
    }
    Ok(())
}

fn validate_candidate_activation_directory(
    names: &[String],
    generations: &[[u8; 32]],
    head_present: bool,
) -> Result<(), AuthorityBootstrapError> {
    if generations.is_empty()
        || generations.len() > MAX_BOOTSTRAP_ACTIVATION_EPOCH as usize
        || generations.iter().collect::<BTreeSet<_>>().len() != generations.len()
    {
        return Err(AuthorityBootstrapError(
            "authority_activation_directory_not_unique",
        ));
    }
    let mut expected = generations
        .iter()
        .map(|generation| format!("{}.json", hex_lower(generation)))
        .collect::<Vec<_>>();
    let mut actual = names.to_vec();
    actual.sort();
    expected.sort();
    if head_present {
        expected.push("head.json".to_string());
        expected.sort();
    }
    if actual != expected {
        return Err(AuthorityBootstrapError(
            "authority_activation_directory_not_unique",
        ));
    }
    Ok(())
}

trait InstalledServiceBootstrapSource: BootstrapSignatureVerifier {
    fn load_snapshot(
        &mut self,
    ) -> Result<(AuthorityLayout, AuthorityBootstrapSnapshot), AuthorityBootstrapError>;

    fn verify_still_stable(&mut self) -> Result<(), AuthorityBootstrapError>;
}

trait CandidateValidationBootstrapSource:
    BootstrapSignatureVerifier + CandidateCredentialConsumer
{
    fn load_candidate_snapshot(
        &mut self,
        locator: CandidateServiceStartLocator,
    ) -> Result<(AuthorityLayout, CandidateAuthorityBootstrapSnapshot), AuthorityBootstrapError>;

    fn verify_still_stable(&mut self) -> Result<(), AuthorityBootstrapError>;
}

fn bootstrap_installed_service_from_source<S>(
    source: &mut S,
) -> Result<ValidatedAuthorityBootstrap, AuthorityBootstrapError>
where
    S: InstalledServiceBootstrapSource,
{
    let (layout, snapshot) = source.load_snapshot()?;
    let validated = validate_bootstrap_snapshot(&layout, &snapshot, source)?;
    source.verify_still_stable()?;
    Ok(validated)
}

fn bootstrap_candidate_validation_from_source<S>(
    source: &mut S,
    locator: CandidateServiceStartLocator,
    now_unix_millis: u64,
) -> Result<PreparedCandidateValidation, AuthorityBootstrapError>
where
    S: CandidateValidationBootstrapSource,
{
    let (layout, snapshot) = source.load_candidate_snapshot(locator)?;
    let binding = validate_candidate_bootstrap_snapshot(&layout, &snapshot, source, locator)?;
    let trust = ProtectedDetachedManifestFile::parse_canonical(&snapshot.trust_manifest_bytes)
        .map_err(|_| AuthorityBootstrapError("authority_trust_manifest_not_verified"))?;
    let trust_manifest_sha256 = trust
        .signature_input()
        .map_err(|_| AuthorityBootstrapError("authority_trust_manifest_not_verified"))?
        .digest;
    let issuer = match &snapshot.credential_readback {
        CandidateCredentialReadback::Record { issuer, .. } => *issuer,
        CandidateCredentialReadback::None => {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_missing",
            ))
        }
    };
    let observation = CandidateActivationObservation::new_with_issuer(
        *binding.generation(),
        *binding.plan_sha256(),
        *binding.transaction_sha256(),
        binding.activation_epoch(),
        *binding.active_head_sha256(),
        trust_manifest_sha256,
        *binding.activation_manifest_sha256(),
        snapshot.ledger_identity,
        snapshot.candidate_service_process,
        issuer,
    )?;
    source.verify_still_stable()?;
    prepare_candidate_activation_from_readback(
        &observation,
        now_unix_millis,
        snapshot.credential_readback,
    )
}

#[cfg(windows)]
pub(crate) fn bootstrap_authenticated_final_commit_runtime_read_only(
) -> Result<AuthenticatedFinalCommitBootstrap, AuthorityBootstrapError> {
    native::probe_installed_service_state()?;
    let layout = AuthorityLayout::installed()
        .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
    let final_commit = RestrictedFinalizerCommitsParentRoot::open_installed(&layout)
        .map_err(|_| AuthorityBootstrapError("authority_final_commit_store_not_verified"))?
        .verify_published_final_commit()
        .map_err(|_| AuthorityBootstrapError("authority_final_commit_not_verified"))?;
    let pair = final_commit
        .into_held_runtime_ledger_pair()
        .map_err(|_| AuthorityBootstrapError("authority_final_commit_ledger_pair_not_verified"))?;
    let mut source =
        native_snapshot::NativeCommittedRuntimeBootstrapSource::new_authenticated_final_commit(
            pair,
        )?;
    let validated = bootstrap_installed_service_from_source(&mut source)?;
    let binding = source.published_runtime_binding();
    let final_commit_receipt_sha256 = source.published_final_commit_receipt_sha256();
    validate_published_runtime_binding(&validated, binding)?;
    let mut ledger = source.take_authenticated_runtime_ledger()?;
    let ledger_binding = ledger
        .authenticated_published_binding_projection()
        .map_err(|_| AuthorityBootstrapError("authority_final_commit_binding_changed"))?;
    validate_exact_published_runtime_binding(&validated, binding, ledger_binding)?;
    let ledger_readback = ledger
        .authenticated_pair_readback()
        .map_err(|_| AuthorityBootstrapError("authority_ledger_readback_changed"))?;
    if ledger_readback.frame_count() != validated.ledger_frame_count
        || ledger_readback.active_ticket_count() != validated.active_ticket_count
        || ledger_readback.ledger_byte_length() != validated.ledger_byte_length
        || ledger_readback.ledger_sha256() != &validated.ledger_sha256
        || ledger_readback.anchor_byte_length() != validated.ledger_anchor_byte_length
        || ledger_readback.anchor_sha256() != &validated.ledger_anchor_sha256
    {
        return Err(AuthorityBootstrapError("authority_ledger_readback_changed"));
    }
    source.verify_still_stable()?;
    let native_runtime_source =
        source.take_authenticated_runtime_source(validated.runtime_source_manifest)?;
    let runtime_source = AuthenticatedRuntimeSourceCapability::new(
        &validated,
        binding,
        final_commit_receipt_sha256,
        native_runtime_source,
    )?;
    let native_runner_policy = source.take_authenticated_runner_policy(
        *validated.generation(),
        *validated.transaction_sha256(),
        validated.runner_policy_state,
        validated.runner_policy_sealed_identity,
    )?;
    let runner_policy = AuthenticatedRunnerPolicyCapability::new(
        &validated,
        final_commit_receipt_sha256,
        native_runner_policy,
    )?;
    let native_root_executables = source.take_authenticated_root_executables(
        *validated.generation(),
        validated.lifecycle_driver_binary_descriptor()?,
        validated.bridge_launcher_binary_descriptor()?,
    )?;
    let root_executables = AuthenticatedProtectedRootExecutablesCapability::new(
        &validated,
        final_commit_receipt_sha256,
        native_root_executables,
    )?;
    Ok(AuthenticatedFinalCommitBootstrap {
        validated,
        source,
        ledger,
        runtime_source,
        runner_policy,
        root_executables,
    })
}

#[cfg(windows)]
pub(crate) struct PendingCandidateValidation {
    source: native_snapshot::NativeCandidateValidationBootstrapSource,
    prepared: PreparedCandidateValidation,
}

#[cfg(windows)]
impl PendingCandidateValidation {
    pub(crate) fn credential_sha256(&self) -> [u8; 32] {
        self.prepared.credential_sha256()
    }

    pub(crate) fn pipe_instance_id(&self) -> [u8; 16] {
        self.prepared.pipe_instance_id()
    }

    pub(crate) fn complete_fixed_handshake(
        mut self,
        request: CandidateValidationRequest,
        client_peer: CandidatePeerEvidence,
    ) -> Result<CandidateValidationHandshake, AuthorityBootstrapError> {
        self.source.verify_still_stable()?;
        let client_peer = client_peer
            .into_verified_process_evidence()
            .map_err(|_| AuthorityBootstrapError("authority_candidate_client_peer_invalid"))?;
        let now_unix_millis = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|_| AuthorityBootstrapError("authority_candidate_clock_invalid"))?
            .as_millis()
            .try_into()
            .map_err(|_| AuthorityBootstrapError("authority_candidate_clock_invalid"))?;
        self.prepared.complete_fixed_handshake(
            request,
            client_peer,
            now_unix_millis,
            &mut self.source,
        )
    }
}

#[cfg(windows)]
pub(crate) fn prepare_candidate_validation_once(
    locator: CandidateServiceStartLocator,
) -> Result<PendingCandidateValidation, AuthorityBootstrapError> {
    native::probe_candidate_service_identity()?;
    let now_unix_millis = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|_| AuthorityBootstrapError("authority_candidate_clock_invalid"))?
        .as_millis()
        .try_into()
        .map_err(|_| AuthorityBootstrapError("authority_candidate_clock_invalid"))?;
    let mut source = native_snapshot::NativeCandidateValidationBootstrapSource::new();
    let prepared =
        bootstrap_candidate_validation_from_source(&mut source, locator, now_unix_millis)?;
    Ok(PendingCandidateValidation { source, prepared })
}

#[cfg(windows)]
const CANDIDATE_ARMING_WAIT_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(30);
#[cfg(windows)]
const CANDIDATE_ARMING_POLL_INTERVAL: std::time::Duration = std::time::Duration::from_millis(50);

#[cfg(windows)]
fn candidate_arming_error_is_retryable(error: &AuthorityBootstrapError) -> bool {
    matches!(
        error.code(),
        "authority_candidate_credential_missing"
            | "authority_candidate_credential_not_armed"
            | "authority_candidate_armed_journal_not_current"
    )
}

#[cfg(windows)]
fn next_candidate_start_pending_checkpoint(current: u32) -> Result<u32, AuthorityBootstrapError> {
    current.checked_add(1).ok_or(AuthorityBootstrapError(
        "authority_candidate_start_pending_checkpoint_exhausted",
    ))
}

#[cfg(windows)]
fn candidate_arming_remaining_millis(
    deadline: std::time::Instant,
) -> Result<u32, AuthorityBootstrapError> {
    let remaining = deadline.saturating_duration_since(std::time::Instant::now());
    if remaining.is_zero() {
        return Err(AuthorityBootstrapError(
            "authority_candidate_arming_timeout",
        ));
    }
    Ok(remaining.as_millis().clamp(1, u128::from(u32::MAX)) as u32)
}

/// Waits only for the exact Prepared-to-Armed publication window. Every read
/// is bracketed by a caller-supplied START_PENDING status refresh, so a state
/// transition, stop request, or failed SCM checkpoint aborts before the
/// candidate endpoint can be exposed.
#[cfg(windows)]
pub(crate) fn await_candidate_validation_armed<F>(
    locator: CandidateServiceStartLocator,
    refresh_start_pending: F,
) -> Result<PendingCandidateValidation, AuthorityBootstrapError>
where
    F: FnMut(u32, u32) -> Result<(), &'static str>,
{
    await_candidate_validation_armed_with(
        || prepare_candidate_validation_once(locator),
        refresh_start_pending,
        std::thread::sleep,
        CANDIDATE_ARMING_WAIT_TIMEOUT,
        CANDIDATE_ARMING_POLL_INTERVAL,
    )
}

#[cfg(windows)]
fn await_candidate_validation_armed_with<T, P, F, S>(
    mut prepare: P,
    mut refresh_start_pending: F,
    mut sleep: S,
    timeout: std::time::Duration,
    poll_interval: std::time::Duration,
) -> Result<T, AuthorityBootstrapError>
where
    P: FnMut() -> Result<T, AuthorityBootstrapError>,
    F: FnMut(u32, u32) -> Result<(), &'static str>,
    S: FnMut(std::time::Duration),
{
    if timeout.is_zero() || poll_interval.is_zero() || poll_interval > timeout {
        return Err(AuthorityBootstrapError(
            "authority_candidate_arming_wait_policy_invalid",
        ));
    }
    let deadline =
        std::time::Instant::now()
            .checked_add(timeout)
            .ok_or(AuthorityBootstrapError(
                "authority_candidate_arming_deadline_invalid",
            ))?;
    // The service host publishes checkpoint 1 when it first enters
    // START_PENDING. Candidate arming owns the strictly increasing suffix.
    let mut checkpoint = 2u32;
    loop {
        let remaining_millis = candidate_arming_remaining_millis(deadline)?;
        refresh_start_pending(checkpoint, remaining_millis).map_err(|_| {
            AuthorityBootstrapError("authority_candidate_start_pending_refresh_failed")
        })?;
        candidate_arming_remaining_millis(deadline)?;
        match prepare() {
            Ok(pending) => {
                checkpoint = next_candidate_start_pending_checkpoint(checkpoint)?;
                candidate_arming_remaining_millis(deadline)?;
                refresh_start_pending(
                    checkpoint,
                    activation::CANDIDATE_START_PENDING_HANDSHAKE_WAIT_HINT_MILLIS,
                )
                .map_err(|_| {
                    AuthorityBootstrapError("authority_candidate_start_pending_refresh_failed")
                })?;
                return Ok(pending);
            }
            Err(error) if candidate_arming_error_is_retryable(&error) => {}
            Err(error) => return Err(error),
        }
        checkpoint = next_candidate_start_pending_checkpoint(checkpoint)?;
        let remaining = deadline.saturating_duration_since(std::time::Instant::now());
        if remaining.is_zero() {
            return Err(AuthorityBootstrapError(
                "authority_candidate_arming_timeout",
            ));
        }
        sleep(poll_interval.min(remaining));
    }
}

#[cfg(not(windows))]
pub(crate) fn bootstrap_authenticated_final_commit_runtime_read_only(
) -> Result<AuthenticatedFinalCommitBootstrap, AuthorityBootstrapError> {
    Err(AuthorityBootstrapError(
        "authority_service_bootstrap_platform_unsupported",
    ))
}

#[cfg(not(windows))]
pub(crate) struct PendingCandidateValidation;

#[cfg(not(windows))]
pub(crate) fn prepare_candidate_validation_once(
    _locator: CandidateServiceStartLocator,
) -> Result<PendingCandidateValidation, AuthorityBootstrapError> {
    Err(AuthorityBootstrapError(
        "authority_service_bootstrap_platform_unsupported",
    ))
}

#[cfg(windows)]
mod native {
    use super::*;
    use std::{
        fs::{File, OpenOptions},
        io::Read,
        os::windows::{
            fs::{MetadataExt, OpenOptionsExt},
            io::AsRawHandle,
        },
        path::Path,
    };
    use windows_sys::Win32::Storage::FileSystem::{
        GetDriveTypeW, GetFileInformationByHandle, GetVolumePathNameW, BY_HANDLE_FILE_INFORMATION,
        FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_READ,
    };
    use windows_sys::Win32::{
        Foundation::{CloseHandle, GetLastError, ERROR_INSUFFICIENT_BUFFER, HANDLE},
        Security::{
            CreateWellKnownSid, EqualSid, GetSidSubAuthority, GetSidSubAuthorityCount,
            GetTokenInformation, IsValidSid, TokenIntegrityLevel, TokenSessionId, TokenUser,
            WinLocalSystemSid, TOKEN_INFORMATION_CLASS, TOKEN_MANDATORY_LABEL, TOKEN_QUERY,
            TOKEN_USER,
        },
        System::{
            SystemServices::SECURITY_MANDATORY_HIGH_RID,
            Threading::{GetCurrentProcess, OpenProcessToken},
        },
    };

    const MAX_ACTIVE_HEAD_BYTES: u64 = 16 * 1024;

    #[derive(Clone, Copy, PartialEq, Eq)]
    enum ServiceBootstrapProbeLane {
        CandidateValidation,
        CommittedRuntime,
    }

    fn probe_service_prerequisites_with<I, H>(
        lane: ServiceBootstrapProbeLane,
        identity: I,
        committed_head: H,
    ) -> Result<(), AuthorityBootstrapError>
    where
        I: FnOnce() -> Result<(), AuthorityBootstrapError>,
        H: FnOnce() -> Result<(), AuthorityBootstrapError>,
    {
        identity()?;
        if lane == ServiceBootstrapProbeLane::CommittedRuntime {
            committed_head()?;
        }
        Ok(())
    }

    pub(super) fn probe_candidate_service_identity() -> Result<(), AuthorityBootstrapError> {
        probe_service_prerequisites_with(
            ServiceBootstrapProbeLane::CandidateValidation,
            require_local_system_service_identity,
            || {
                Err(AuthorityBootstrapError(
                    "authority_candidate_opened_active_head",
                ))
            },
        )
    }

    pub(super) fn probe_installed_service_state() -> Result<(), AuthorityBootstrapError> {
        probe_service_prerequisites_with(
            ServiceBootstrapProbeLane::CommittedRuntime,
            require_local_system_service_identity,
            || {
                let layout = AuthorityLayout::installed()
                    .map_err(|_| AuthorityBootstrapError("authority_layout_unavailable"))?;
                let held_head = open_local_single_link_file(
                    &layout.active_head_path(),
                    MAX_ACTIVE_HEAD_BYTES,
                    "authority_active_head",
                )?;
                ProtectedActiveHead::parse_canonical(&held_head.bytes)
                    .map_err(|_| AuthorityBootstrapError("authority_active_head_not_verified"))?;
                drop(held_head);
                Ok(())
            },
        )
    }

    #[cfg(test)]
    pub(super) fn candidate_probe_skips_committed_head_for_test(
    ) -> Result<(), AuthorityBootstrapError> {
        probe_service_prerequisites_with(
            ServiceBootstrapProbeLane::CandidateValidation,
            || Ok(()),
            || {
                Err(AuthorityBootstrapError(
                    "authority_candidate_opened_active_head",
                ))
            },
        )
    }

    fn require_local_system_service_identity() -> Result<(), AuthorityBootstrapError> {
        let mut raw_token: HANDLE = std::ptr::null_mut();
        if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut raw_token) } == 0
            || raw_token.is_null()
        {
            return Err(AuthorityBootstrapError(
                "authority_service_identity_unavailable",
            ));
        }
        struct Token(HANDLE);
        impl Drop for Token {
            fn drop(&mut self) {
                if !self.0.is_null() {
                    unsafe {
                        CloseHandle(self.0);
                    }
                }
            }
        }
        let token = Token(raw_token);
        let session_id: u32 = query_token_fixed(token.0, TokenSessionId)?;
        let integrity = query_token_buffer(token.0, TokenIntegrityLevel)?;
        let label = unsafe { &*(integrity.as_ptr().cast::<TOKEN_MANDATORY_LABEL>()) };
        if label.Label.Sid.is_null() || unsafe { IsValidSid(label.Label.Sid) } == 0 {
            return Err(AuthorityBootstrapError(
                "authority_service_identity_invalid",
            ));
        }
        let sub_authority_count = unsafe { *GetSidSubAuthorityCount(label.Label.Sid) } as u32;
        if sub_authority_count == 0 {
            return Err(AuthorityBootstrapError(
                "authority_service_identity_invalid",
            ));
        }
        let integrity_rid =
            unsafe { *GetSidSubAuthority(label.Label.Sid, sub_authority_count - 1) };
        let user = query_token_buffer(token.0, TokenUser)?;
        let token_user = unsafe { &*(user.as_ptr().cast::<TOKEN_USER>()) };
        if token_user.User.Sid.is_null() || unsafe { IsValidSid(token_user.User.Sid) } == 0 {
            return Err(AuthorityBootstrapError(
                "authority_service_identity_invalid",
            ));
        }
        let mut system_sid = [0usize; 9];
        let mut system_sid_size = (system_sid.len() * std::mem::size_of::<usize>()) as u32;
        if unsafe {
            CreateWellKnownSid(
                WinLocalSystemSid,
                std::ptr::null_mut(),
                system_sid.as_mut_ptr().cast(),
                &mut system_sid_size,
            )
        } == 0
        {
            return Err(AuthorityBootstrapError(
                "authority_service_identity_unavailable",
            ));
        }
        if unsafe { IsValidSid(system_sid.as_ptr().cast_mut().cast()) } == 0 {
            return Err(AuthorityBootstrapError(
                "authority_service_identity_unavailable",
            ));
        }
        let is_local_system =
            unsafe { EqualSid(token_user.User.Sid, system_sid.as_mut_ptr().cast()) != 0 };
        if !is_local_system || session_id != 0 || integrity_rid < SECURITY_MANDATORY_HIGH_RID as u32
        {
            return Err(AuthorityBootstrapError(
                "authority_service_system_identity_required",
            ));
        }
        Ok(())
    }

    fn query_token_fixed<T: Copy>(
        token: HANDLE,
        class: TOKEN_INFORMATION_CLASS,
    ) -> Result<T, AuthorityBootstrapError> {
        let mut value = std::mem::MaybeUninit::<T>::zeroed();
        let mut required = 0u32;
        if unsafe {
            GetTokenInformation(
                token,
                class,
                value.as_mut_ptr().cast(),
                std::mem::size_of::<T>() as u32,
                &mut required,
            )
        } == 0
            || required as usize != std::mem::size_of::<T>()
        {
            return Err(AuthorityBootstrapError(
                "authority_service_identity_unavailable",
            ));
        }
        Ok(unsafe { value.assume_init() })
    }

    fn query_token_buffer(
        token: HANDLE,
        class: TOKEN_INFORMATION_CLASS,
    ) -> Result<Vec<usize>, AuthorityBootstrapError> {
        let mut required = 0u32;
        unsafe {
            GetTokenInformation(token, class, std::ptr::null_mut(), 0, &mut required);
        }
        if required == 0
            || required > 64 * 1024
            || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER
        {
            return Err(AuthorityBootstrapError(
                "authority_service_identity_unavailable",
            ));
        }
        let word_size = std::mem::size_of::<usize>();
        let word_count =
            (required as usize)
                .checked_add(word_size - 1)
                .ok_or(AuthorityBootstrapError(
                    "authority_service_identity_unavailable",
                ))?
                / word_size;
        let mut buffer = vec![0usize; word_count];
        let mut written = 0u32;
        if unsafe {
            GetTokenInformation(
                token,
                class,
                buffer.as_mut_ptr().cast(),
                required,
                &mut written,
            )
        } == 0
            || written != required
        {
            return Err(AuthorityBootstrapError(
                "authority_service_identity_unavailable",
            ));
        }
        Ok(buffer)
    }

    struct HeldReadback {
        _file: File,
        bytes: Vec<u8>,
    }

    fn open_local_single_link_file(
        path: &Path,
        maximum_size: u64,
        code_prefix: &'static str,
    ) -> Result<HeldReadback, AuthorityBootstrapError> {
        if !path.is_absolute() || !path_is_local(path) {
            return Err(error_for(code_prefix, "_path_invalid"));
        }
        let path_metadata =
            std::fs::symlink_metadata(path).map_err(|_| error_for(code_prefix, "_missing"))?;
        if !path_metadata.is_file()
            || path_metadata.file_type().is_symlink()
            || path_metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || path_metadata.len() == 0
            || path_metadata.len() > maximum_size
        {
            return Err(error_for(code_prefix, "_metadata_invalid"));
        }
        let mut file = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
            .open(path)
            .map_err(|_| error_for(code_prefix, "_open_failed"))?;
        let before = file_identity(&file, code_prefix)?;
        if before.2 != 1 {
            return Err(error_for(code_prefix, "_link_count_invalid"));
        }
        let mut bytes = Vec::with_capacity(path_metadata.len() as usize);
        file.read_to_end(&mut bytes)
            .map_err(|_| error_for(code_prefix, "_read_failed"))?;
        if bytes.is_empty() || bytes.len() as u64 > maximum_size {
            return Err(error_for(code_prefix, "_size_invalid"));
        }
        let after = file_identity(&file, code_prefix)?;
        if before != after || bytes.len() as u64 != path_metadata.len() {
            return Err(error_for(code_prefix, "_identity_changed"));
        }
        Ok(HeldReadback { _file: file, bytes })
    }

    fn file_identity(
        file: &File,
        code_prefix: &'static str,
    ) -> Result<(u32, u64, u32), AuthorityBootstrapError> {
        let mut information = unsafe { std::mem::zeroed::<BY_HANDLE_FILE_INFORMATION>() };
        if unsafe { GetFileInformationByHandle(file.as_raw_handle().cast(), &mut information) } == 0
            || information.dwVolumeSerialNumber == 0
            || (information.nFileIndexHigh == 0 && information.nFileIndexLow == 0)
        {
            return Err(error_for(code_prefix, "_identity_unavailable"));
        }
        Ok((
            information.dwVolumeSerialNumber,
            (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow),
            information.nNumberOfLinks,
        ))
    }

    fn path_is_local(path: &Path) -> bool {
        use std::os::windows::ffi::OsStrExt;
        const DRIVE_REMOTE: u32 = 4;
        let encoded = path
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let mut root = [0u16; 32_768];
        unsafe {
            GetVolumePathNameW(encoded.as_ptr(), root.as_mut_ptr(), root.len() as u32) != 0
                && GetDriveTypeW(root.as_ptr()) != DRIVE_REMOTE
        }
    }

    fn error_for(prefix: &'static str, suffix: &'static str) -> AuthorityBootstrapError {
        match (prefix, suffix) {
            ("authority_active_head", "_path_invalid") => {
                AuthorityBootstrapError("authority_active_head_path_invalid")
            }
            ("authority_active_head", "_missing") => {
                AuthorityBootstrapError("authority_active_head_missing")
            }
            ("authority_active_head", "_metadata_invalid") => {
                AuthorityBootstrapError("authority_active_head_metadata_invalid")
            }
            ("authority_active_head", "_open_failed") => {
                AuthorityBootstrapError("authority_active_head_open_failed")
            }
            ("authority_active_head", "_link_count_invalid") => {
                AuthorityBootstrapError("authority_active_head_link_count_invalid")
            }
            ("authority_active_head", "_read_failed") => {
                AuthorityBootstrapError("authority_active_head_read_failed")
            }
            ("authority_active_head", "_size_invalid") => {
                AuthorityBootstrapError("authority_active_head_size_invalid")
            }
            ("authority_active_head", "_identity_changed") => {
                AuthorityBootstrapError("authority_active_head_identity_changed")
            }
            _ => AuthorityBootstrapError("authority_active_head_identity_unavailable"),
        }
    }
}

#[cfg(windows)]
#[path = "bootstrap_windows.rs"]
mod native_snapshot;

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    struct FakeVerifier {
        expected: Vec<[u8; 32]>,
        fail_at: Option<usize>,
        calls: usize,
    }

    struct FakeInstalledSource {
        snapshot: Option<AuthorityBootstrapSnapshot>,
        expected: Vec<[u8; 32]>,
        load_error: Option<&'static str>,
        stable_error: Option<&'static str>,
        calls: usize,
        stability_checks: usize,
        verified_generations: Vec<[u8; 32]>,
        candidate_record: Option<activation::CandidateCredentialRecord>,
        candidate_prior_head: Option<CandidatePriorHeadObservation>,
    }

    impl BootstrapSignatureVerifier for FakeInstalledSource {
        fn verify(
            &mut self,
            generation: &[u8; 32],
            input: &ProtectedManifestSignatureInput,
        ) -> Result<(), AuthorityBootstrapError> {
            let call = self.calls;
            self.calls += 1;
            if self.expected.get(call) != Some(&input.digest) {
                return Err(AuthorityBootstrapError(
                    "authority_manifest_signature_invalid",
                ));
            }
            self.verified_generations.push(*generation);
            Ok(())
        }
    }

    impl InstalledServiceBootstrapSource for FakeInstalledSource {
        fn load_snapshot(
            &mut self,
        ) -> Result<(AuthorityLayout, AuthorityBootstrapSnapshot), AuthorityBootstrapError>
        {
            if let Some(code) = self.load_error {
                return Err(AuthorityBootstrapError(code));
            }
            Ok((
                layout(),
                self.snapshot
                    .take()
                    .ok_or(AuthorityBootstrapError("authority_installation_missing"))?,
            ))
        }

        fn verify_still_stable(&mut self) -> Result<(), AuthorityBootstrapError> {
            self.stability_checks += 1;
            if let Some(code) = self.stable_error {
                return Err(AuthorityBootstrapError(code));
            }
            Ok(())
        }
    }

    impl CandidateValidationBootstrapSource for FakeInstalledSource {
        fn load_candidate_snapshot(
            &mut self,
            locator: CandidateServiceStartLocator,
        ) -> Result<(AuthorityLayout, CandidateAuthorityBootstrapSnapshot), AuthorityBootstrapError>
        {
            if let Some(code) = self.load_error {
                return Err(AuthorityBootstrapError(code));
            }
            let credential_readback = self.read_candidate(&locator.transaction_sha256())?;
            let snapshot = self
                .snapshot
                .take()
                .ok_or(AuthorityBootstrapError("authority_installation_missing"))?;
            Ok((
                layout(),
                CandidateAuthorityBootstrapSnapshot {
                    schema: snapshot.schema,
                    credential_readback,
                    prior_head: self
                        .candidate_prior_head
                        .unwrap_or(CandidatePriorHeadObservation::Absent),
                    trust_manifest_bytes: snapshot.trust_manifest_bytes,
                    activation_manifest_bytes: snapshot.activation_manifest_bytes,
                    activation_history: snapshot.activation_history,
                    activation_directory_names: snapshot.activation_directory_names,
                    installed_content: snapshot.installed_content,
                    current_service_image: snapshot.current_service_image,
                    key_readback: snapshot.key_readback,
                    ledger_identity: snapshot.ledger_identity,
                    ledger_frame_count: snapshot.ledger_frame_count,
                    ledger_byte_length: snapshot.ledger_byte_length,
                    ledger_sha256: snapshot.ledger_sha256,
                    ledger_anchor_byte_length: snapshot.ledger_anchor_byte_length,
                    ledger_anchor_sha256: snapshot.ledger_anchor_sha256,
                    protected_artifacts: snapshot
                        .protected_artifacts
                        .into_iter()
                        .filter(|value| {
                            !matches!(
                                value.kind,
                                BootstrapArtifactKind::ActiveHead
                                    | BootstrapArtifactKind::RunnerPolicyState
                            )
                        })
                        .collect(),
                    service_process_identity_exact: snapshot.service_process_identity_exact,
                    service_process_id: snapshot.service_process_id,
                    service_process_creation_time: snapshot.service_process_creation_time,
                    candidate_service_process: snapshot.candidate_service_process,
                },
            ))
        }

        fn verify_still_stable(&mut self) -> Result<(), AuthorityBootstrapError> {
            InstalledServiceBootstrapSource::verify_still_stable(self)
        }
    }

    impl CandidateCredentialConsumer for FakeInstalledSource {
        fn read_candidate(
            &mut self,
            transaction_sha256: &[u8; 32],
        ) -> Result<activation::CandidateCredentialReadback, AuthorityBootstrapError> {
            match self.candidate_record.clone() {
                None => Ok(activation::CandidateCredentialReadback::None),
                Some(record) if record.binding()?.transaction_sha256() == transaction_sha256 => {
                    let issuer = record.binding()?.issuer();
                    let armed_receipt_sha256 = record.armed_receipt_sha256().unwrap_or([0; 32]);
                    Ok(activation::CandidateCredentialReadback::Record {
                        record,
                        issuer,
                        armed_receipt_sha256,
                    })
                }
                Some(_) => Err(AuthorityBootstrapError(
                    "authority_candidate_credential_binding_mismatch",
                )),
            }
        }

        fn consume_armed(
            &mut self,
            expected: &activation::CandidateCredentialRecord,
            request: &CandidateValidationRequest,
            client_peer: CandidateProcessEvidence,
        ) -> Result<activation::CandidateCredentialRecord, AuthorityBootstrapError> {
            let current = self
                .candidate_record
                .as_ref()
                .ok_or(AuthorityBootstrapError(
                    "authority_candidate_credential_missing",
                ))?;
            if current != expected || current.phase() != activation::CandidateCredentialPhase::Armed
            {
                return Err(AuthorityBootstrapError(
                    "authority_candidate_credential_compare_exchange_failed",
                ));
            }
            let consumed = current.consume_with_peer(request, client_peer)?;
            self.candidate_record = Some(consumed.clone());
            Ok(consumed)
        }
    }

    impl BootstrapSignatureVerifier for FakeVerifier {
        fn verify(
            &mut self,
            _generation: &[u8; 32],
            input: &ProtectedManifestSignatureInput,
        ) -> Result<(), AuthorityBootstrapError> {
            let call = self.calls;
            self.calls += 1;
            if self.fail_at == Some(call) || self.expected.get(call) != Some(&input.digest) {
                return Err(AuthorityBootstrapError(
                    "authority_manifest_signature_invalid",
                ));
            }
            Ok(())
        }
    }

    fn layout() -> AuthorityLayout {
        AuthorityLayout::for_test_roots(
            Path::new(r"C:\Program Files"),
            Path::new(r"C:\ProgramData"),
        )
        .unwrap()
    }

    fn content() -> AuthorityInstallContent {
        AuthorityInstallContent::new(
            AuthorityPayloadDigest::new([0x11; 32], 101).unwrap(),
            AuthorityPayloadDigest::new([0x12; 32], 102).unwrap(),
            AuthorityPayloadDigest::new([0x13; 32], 103).unwrap(),
            AuthorityPayloadDigest::new([0x14; 32], 104).unwrap(),
            AuthorityPayloadDigest::new([0x15; 32], 105).unwrap(),
            AuthorityPayloadDigest::new([0x16; 32], 106).unwrap(),
        )
        .unwrap()
    }

    fn exact_artifacts() -> Vec<ProtectedArtifactReadback> {
        REQUIRED_ARTIFACTS
            .into_iter()
            .map(|kind| ProtectedArtifactReadback {
                kind,
                path_exact: true,
                local_volume: true,
                reparse_free_held_chain: true,
                single_link: true,
                stable_identity: true,
                exact_owner_and_acl: true,
                full_held_handle_readback: true,
            })
            .collect()
    }

    fn valid_snapshot() -> (AuthorityBootstrapSnapshot, Vec<[u8; 32]>) {
        let layout = layout();
        let installed_content = content();
        let preview = preview_install(&layout, installed_content.clone()).unwrap();
        let generation = preview.generation_sha256().unwrap();
        let mut public_key = [0x22; 65];
        public_key[0] = 0x04;
        let key_id: [u8; 32] = Sha256::digest(public_key).into();
        let key_readback = VerifiedAuthorityKeyReadback::for_test(public_key);
        let ledger_identity = derive_ledger_identity(&generation, &key_id).unwrap();
        let mut signature = [0u8; 64];
        signature[31] = 1;
        signature[63] = 1;
        let trust = ProtectedDetachedManifestFile::new(
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
            signature,
        )
        .unwrap();
        let trust_digest = trust.signature_input().unwrap().digest;
        let activation = ProtectedDetachedManifestFile::new(
            CanonicalUnsignedManifestPayload::Activation {
                generation,
                trust_manifest_sha256: trust_digest,
                signer_key_id: key_id,
                activated_epoch: 1,
                previous_generation: None,
                previous_activation_sha256: None,
                previous_activation_epoch: None,
                valid: true,
                revoked: false,
            },
            key_id,
            signature,
        )
        .unwrap();
        let activation_digest = activation.signature_input().unwrap().digest;
        let head = ProtectedActiveHead::new(
            generation,
            activation_digest,
            1,
            preview.transaction_sha256().unwrap(),
            preview.plan_sha256().unwrap(),
            None,
        )
        .unwrap();
        (
            AuthorityBootstrapSnapshot {
                schema: SERVICE_BOOTSTRAP_SCHEMA,
                active_head_bytes: head.canonical_bytes().unwrap(),
                trust_manifest_bytes: trust.canonical_bytes().unwrap(),
                activation_manifest_bytes: activation.canonical_bytes().unwrap(),
                activation_history: Vec::new(),
                activation_directory_names: vec![
                    "head.json".to_string(),
                    format!("{}.json", hex_lower(&generation)),
                ],
                installed_content: installed_content.clone(),
                runner_policy_state: CanonicalRunnerPolicyState::canonical_test_fixture(
                    generation,
                    preview.transaction_sha256().unwrap(),
                )
                .descriptor()
                .unwrap(),
                runner_policy_sealed_identity: RunnerPolicySealedIdentity::exact_test_fixture(0x6b),
                current_service_image: installed_content.service(),
                key_readback,
                ledger_identity,
                ledger_frame_count: 1,
                ledger_byte_length: FRAME_SIZE as u64,
                ledger_sha256: [0x51; 32],
                ledger_anchor_byte_length: 2 * ANCHOR_RECORD_SIZE as u64,
                ledger_anchor_sha256: [0x52; 32],
                active_ticket_count: 0,
                protected_artifacts: exact_artifacts(),
                service_process_identity_exact: true,
                service_process_id: 701,
                service_process_creation_time: 8_001,
                candidate_service_process: CandidateProcessEvidence::from_held_process(
                    701,
                    8_001,
                    *installed_content.service().sha256(),
                    installed_content.service().byte_length(),
                    0x4141,
                    [0x42; 16],
                    1,
                    0x20,
                )
                .unwrap(),
                maintenance_terminal_binding: Some(AuthorityBootstrapTerminalBinding {
                    generation,
                    plan_sha256: preview.plan_sha256().unwrap(),
                    transaction_sha256: preview.transaction_sha256().unwrap(),
                    activation_epoch: 1,
                }),
            },
            vec![trust_digest, activation_digest],
        )
    }

    fn append_test_update(
        snapshot: &mut AuthorityBootstrapSnapshot,
        signatures: &mut Vec<[u8; 32]>,
        seed: u8,
    ) {
        let prior_head = ProtectedActiveHead::parse_canonical(&snapshot.active_head_bytes).unwrap();
        let prior_generation = prior_head.generation().unwrap();
        let prior_activation_sha256 = prior_head.activation_manifest_sha256().unwrap();
        let prior_key_id = *snapshot.key_readback.signer_key_id();
        let prior_public_key = *snapshot.key_readback.public_key_sec1();
        let prior_content = snapshot.installed_content.clone();
        let prior = VerifiedInstalledGeneration {
            generation: prior_generation,
            service: prior_content.service(),
            controller: prior_content.controller(),
            install_helper: prior_content.install_helper(),
            lifecycle_driver: prior_content.lifecycle_driver(),
            bridge_launcher: prior_content.bridge_launcher(),
            runtime_source_manifest: prior_content.runtime_source_manifest(),
            signer_key_id: prior_key_id,
            signer_public_key_sec1: prior_public_key,
            trust_manifest_sha256: signatures[signatures.len() - 2],
            activation_manifest_sha256: prior_activation_sha256,
            activation_epoch: prior_head.activation_epoch(),
            service_runtime: VerifiedServiceRuntimeProof {
                process_id: u32::from(seed) + 1,
                process_creation_time: u64::from(seed) + 2,
                image_sha256: *prior_content.service().sha256(),
                pipe_instance_id: [seed.wrapping_add(3); 16],
            },
        };
        let updated_content = AuthorityInstallContent::new(
            AuthorityPayloadDigest::new([seed.wrapping_add(1); 32], u64::from(seed) + 201).unwrap(),
            AuthorityPayloadDigest::new([seed.wrapping_add(2); 32], u64::from(seed) + 202).unwrap(),
            AuthorityPayloadDigest::new([seed.wrapping_add(3); 32], u64::from(seed) + 203).unwrap(),
            AuthorityPayloadDigest::new([seed.wrapping_add(4); 32], u64::from(seed) + 204).unwrap(),
            AuthorityPayloadDigest::new([seed.wrapping_add(5); 32], u64::from(seed) + 205).unwrap(),
            AuthorityPayloadDigest::new([seed.wrapping_add(6); 32], u64::from(seed) + 206).unwrap(),
        )
        .unwrap();
        let preview = preview_update(&layout(), updated_content.clone(), prior).unwrap();
        let generation = preview.generation_sha256().unwrap();
        let activation_epoch = prior_head.activation_epoch().checked_add(1).unwrap();
        let mut public_key = [seed.wrapping_add(0x40); 65];
        public_key[0] = 0x04;
        let key_id: [u8; 32] = Sha256::digest(public_key).into();
        let key_readback = VerifiedAuthorityKeyReadback::for_test(public_key);
        let ledger_identity = derive_ledger_identity(&generation, &key_id).unwrap();
        let mut signature = [0u8; 64];
        signature[31] = seed.max(1);
        signature[63] = seed.max(1);
        let trust = ProtectedDetachedManifestFile::new(
            CanonicalUnsignedManifestPayload::Trust {
                generation,
                signer_key_id: key_id,
                signer_public_key_sec1: public_key,
                ledger_identity,
                created_epoch: activation_epoch,
                valid: true,
                revoked: false,
            },
            key_id,
            signature,
        )
        .unwrap();
        let trust_digest = trust.signature_input().unwrap().digest;
        let activation = ProtectedDetachedManifestFile::new(
            CanonicalUnsignedManifestPayload::Activation {
                generation,
                trust_manifest_sha256: trust_digest,
                signer_key_id: key_id,
                activated_epoch: activation_epoch,
                previous_generation: Some(prior_generation),
                previous_activation_sha256: Some(prior_activation_sha256),
                previous_activation_epoch: Some(prior_head.activation_epoch()),
                valid: true,
                revoked: false,
            },
            key_id,
            signature,
        )
        .unwrap();
        let activation_digest = activation.signature_input().unwrap().digest;
        let head = ProtectedActiveHead::new(
            generation,
            activation_digest,
            activation_epoch,
            preview.transaction_sha256().unwrap(),
            preview.plan_sha256().unwrap(),
            Some(prior_head.digest().unwrap()),
        )
        .unwrap();
        snapshot.active_head_bytes = head.canonical_bytes().unwrap();
        snapshot
            .activation_history
            .push(AuthorityBootstrapHistoricalGeneration {
                generation: prior_generation,
                trust_manifest_bytes: snapshot.trust_manifest_bytes.clone(),
                activation_manifest_bytes: snapshot.activation_manifest_bytes.clone(),
                key_readback: snapshot.key_readback.clone(),
            });
        snapshot.trust_manifest_bytes = trust.canonical_bytes().unwrap();
        snapshot.activation_manifest_bytes = activation.canonical_bytes().unwrap();
        snapshot
            .activation_directory_names
            .push(format!("{}.json", hex_lower(&generation)));
        snapshot.installed_content = updated_content.clone();
        snapshot.runner_policy_state = CanonicalRunnerPolicyState::canonical_test_fixture(
            generation,
            preview.transaction_sha256().unwrap(),
        )
        .descriptor()
        .unwrap();
        snapshot.runner_policy_sealed_identity =
            RunnerPolicySealedIdentity::exact_test_fixture(seed.wrapping_add(0x6b));
        snapshot.current_service_image = updated_content.service();
        snapshot.key_readback = key_readback;
        snapshot.ledger_identity = ledger_identity;
        snapshot.service_process_id = u32::from(seed) + 701;
        snapshot.service_process_creation_time = u64::from(seed) + 8_001;
        snapshot.candidate_service_process = CandidateProcessEvidence::from_held_process(
            snapshot.service_process_id,
            snapshot.service_process_creation_time,
            *updated_content.service().sha256(),
            updated_content.service().byte_length(),
            0x4141,
            [seed.wrapping_add(0x42); 16],
            1,
            0x20,
        )
        .unwrap();
        snapshot.maintenance_terminal_binding = Some(AuthorityBootstrapTerminalBinding {
            generation,
            plan_sha256: preview.plan_sha256().unwrap(),
            transaction_sha256: preview.transaction_sha256().unwrap(),
            activation_epoch,
        });
        signatures.extend([trust_digest, activation_digest]);
    }

    fn valid_update_snapshot() -> (AuthorityBootstrapSnapshot, Vec<[u8; 32]>) {
        let (mut snapshot, mut signatures) = valid_snapshot();
        append_test_update(&mut snapshot, &mut signatures, 0x20);
        (snapshot, signatures)
    }

    fn valid_three_epoch_snapshot() -> (AuthorityBootstrapSnapshot, Vec<[u8; 32]>) {
        let (mut snapshot, mut signatures) = valid_update_snapshot();
        append_test_update(&mut snapshot, &mut signatures, 0x30);
        (snapshot, signatures)
    }

    fn rewrite_current_activation_link(
        snapshot: &mut AuthorityBootstrapSnapshot,
        signatures: &mut [[u8; 32]],
        previous_generation: Option<[u8; 32]>,
        previous_activation_sha256: Option<[u8; 32]>,
        previous_activation_epoch: Option<u64>,
    ) {
        let head = ProtectedActiveHead::parse_canonical(&snapshot.active_head_bytes).unwrap();
        let generation = head.generation().unwrap();
        let key_id = *snapshot.key_readback.signer_key_id();
        let trust_digest = signatures[signatures.len() - 2];
        let mut signature = [0u8; 64];
        signature[31] = 0x71;
        signature[63] = 0x72;
        let activation = ProtectedDetachedManifestFile::new(
            CanonicalUnsignedManifestPayload::Activation {
                generation,
                trust_manifest_sha256: trust_digest,
                signer_key_id: key_id,
                activated_epoch: head.activation_epoch(),
                previous_generation,
                previous_activation_sha256,
                previous_activation_epoch,
                valid: true,
                revoked: false,
            },
            key_id,
            signature,
        )
        .unwrap();
        let activation_digest = activation.signature_input().unwrap().digest;
        snapshot.activation_manifest_bytes = activation.canonical_bytes().unwrap();
        snapshot.active_head_bytes = ProtectedActiveHead::new(
            generation,
            activation_digest,
            head.activation_epoch(),
            head.transaction_sha256().unwrap(),
            head.plan_sha256().unwrap(),
            head.previous_head_sha256().unwrap(),
        )
        .unwrap()
        .canonical_bytes()
        .unwrap();
        let final_index = signatures.len() - 1;
        signatures[final_index] = activation_digest;
    }

    fn installed_source(
        snapshot: AuthorityBootstrapSnapshot,
        expected: Vec<[u8; 32]>,
    ) -> FakeInstalledSource {
        FakeInstalledSource {
            snapshot: Some(snapshot),
            expected,
            load_error: None,
            stable_error: None,
            calls: 0,
            stability_checks: 0,
            verified_generations: Vec::new(),
            candidate_record: None,
            candidate_prior_head: None,
        }
    }

    fn candidate_record_for_snapshot(
        snapshot: &AuthorityBootstrapSnapshot,
    ) -> (
        activation::CandidateCredentialRecord,
        CandidateValidationRequest,
    ) {
        let head = ProtectedActiveHead::parse_canonical(&snapshot.active_head_bytes).unwrap();
        let trust =
            ProtectedDetachedManifestFile::parse_canonical(&snapshot.trust_manifest_bytes).unwrap();
        let maintenance_worker = CandidateProcessEvidence::from_held_process(
            0x2828,
            0x2929_3030,
            [0x2a; 32],
            0x2b2b,
            0x2c2c,
            [0x2d; 16],
            1,
            0x20,
        )
        .unwrap();
        let issuer = activation::CandidateIssuerBinding::new(
            [0x21; 32],
            [0x22; 32],
            [0x23; 32],
            maintenance_worker,
            [0x24; 32],
            [0x25; 32],
            [0x26; 32],
            0x2727,
            [0x28; 16],
        )
        .unwrap();
        let observation = CandidateActivationObservation::new_with_issuer(
            head.generation().unwrap(),
            head.plan_sha256().unwrap(),
            head.transaction_sha256().unwrap(),
            head.activation_epoch(),
            head.digest().unwrap(),
            trust.signature_input().unwrap().digest,
            head.activation_manifest_sha256().unwrap(),
            snapshot.ledger_identity,
            snapshot.candidate_service_process,
            issuer,
        )
        .unwrap();
        let binding =
            activation::CandidateActivationBinding::new(observation, [0x91; 32], 10_000, 20_000)
                .unwrap();
        let request =
            CandidateValidationRequest::new(binding.credential_sha256(), *binding.nonce()).unwrap();
        let record = activation::CandidateCredentialRecord::prepared(binding)
            .unwrap()
            .arm_with_receipt([0xa1; 32], snapshot.candidate_service_process)
            .unwrap();
        (record, request)
    }

    #[test]
    fn candidate_bootstrap_is_locator_selected_and_does_not_consume_the_active_head() {
        let (mut candidate_snapshot, expected) = valid_snapshot();
        let (record, request) = candidate_record_for_snapshot(&candidate_snapshot);
        let locator = CandidateServiceStartLocator::from_binding(record.binding().unwrap());
        let expected_generation = *record.binding().unwrap().generation();
        let client_peer = *record.binding().unwrap().issuer().maintenance_worker();
        candidate_snapshot.active_head_bytes = b"not-a-head".to_vec();
        candidate_snapshot
            .activation_directory_names
            .retain(|name| name != "head.json");
        let mut candidate = installed_source(candidate_snapshot, expected);
        candidate.candidate_record = Some(record);
        let prepared =
            bootstrap_candidate_validation_from_source(&mut candidate, locator, 15_000).unwrap();
        assert_eq!(candidate.stability_checks, 1);
        assert_eq!(
            candidate.candidate_record.as_ref().unwrap().phase(),
            activation::CandidateCredentialPhase::Armed
        );
        CandidateValidationBootstrapSource::verify_still_stable(&mut candidate).unwrap();
        let handshake = prepared
            .complete_fixed_handshake(request, client_peer, 15_000, &mut candidate)
            .unwrap();
        assert_eq!(handshake.generation(), &expected_generation);
        assert_eq!(
            candidate.candidate_record.unwrap().phase(),
            activation::CandidateCredentialPhase::Consumed
        );

        let (snapshot, expected) = valid_snapshot();
        let (record, _) = candidate_record_for_snapshot(&snapshot);
        let mut wrong_locator_arguments =
            CandidateServiceStartLocator::from_binding(record.binding().unwrap())
                .ordered_service_arguments();
        let first_digest_character = wrong_locator_arguments[1].find('=').unwrap() + 1;
        let replacement = if &wrong_locator_arguments[1]
            [first_digest_character..first_digest_character + 1]
            == "a"
        {
            "b"
        } else {
            "a"
        };
        wrong_locator_arguments[1].replace_range(
            first_digest_character..first_digest_character + 1,
            replacement,
        );
        let wrong_locator = CandidateServiceStartLocator::parse_ordered(
            &wrong_locator_arguments
                .iter()
                .map(String::as_str)
                .collect::<Vec<_>>(),
        )
        .unwrap();
        let mut wrong = installed_source(snapshot, expected);
        wrong.candidate_record = Some(record);
        assert_eq!(
            bootstrap_candidate_validation_from_source(&mut wrong, wrong_locator, 15_000)
                .unwrap_err()
                .code(),
            "authority_candidate_credential_binding_mismatch"
        );

        let (snapshot, expected) = valid_snapshot();
        let (record, _) = candidate_record_for_snapshot(&snapshot);
        let binding = record.binding().unwrap();
        let locator = CandidateServiceStartLocator::from_binding(binding);
        let mut appeared = installed_source(snapshot, expected);
        appeared.candidate_record = Some(record);
        appeared.candidate_prior_head = Some(CandidatePriorHeadObservation::Present {
            head_sha256: *binding.active_head_sha256(),
        });
        assert_eq!(
            bootstrap_candidate_validation_from_source(&mut appeared, locator, 15_000)
                .unwrap_err()
                .code(),
            "authority_candidate_active_head_prior_mismatch"
        );
    }

    #[cfg(windows)]
    #[test]
    fn first_install_candidate_probe_does_not_require_an_active_head() {
        native::candidate_probe_skips_committed_head_for_test().unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn candidate_arming_wait_brackets_success_with_a_fresh_handshake_window() {
        let attempts = std::cell::Cell::new(0u32);
        let mut checkpoints = Vec::new();
        let mut sleeps = Vec::new();
        let result = await_candidate_validation_armed_with(
            || {
                let attempt = attempts.get();
                attempts.set(attempt + 1);
                match attempt {
                    0 => Err(AuthorityBootstrapError(
                        "authority_candidate_credential_missing",
                    )),
                    1 => Err(AuthorityBootstrapError(
                        "authority_candidate_credential_not_armed",
                    )),
                    2 => Err(AuthorityBootstrapError(
                        "authority_candidate_armed_journal_not_current",
                    )),
                    _ => Ok(0x5au8),
                }
            },
            |checkpoint, wait_hint_millis| {
                checkpoints.push((checkpoint, wait_hint_millis));
                Ok(())
            },
            |duration| sleeps.push(duration),
            std::time::Duration::from_secs(1),
            std::time::Duration::from_millis(1),
        )
        .unwrap();
        assert_eq!(result, 0x5a);
        assert_eq!(attempts.get(), 4);
        assert_eq!(
            checkpoints
                .iter()
                .map(|(checkpoint, _)| *checkpoint)
                .collect::<Vec<_>>(),
            vec![2, 3, 4, 5, 6]
        );
        assert!(checkpoints[..checkpoints.len() - 1]
            .iter()
            .all(|(_, wait_hint)| (1..=1_000).contains(wait_hint)));
        assert_eq!(
            checkpoints.last(),
            Some(&(
                6,
                activation::CANDIDATE_START_PENDING_HANDSHAKE_WAIT_HINT_MILLIS,
            ))
        );
        assert!(
            activation::CANDIDATE_START_PENDING_HANDSHAKE_WAIT_HINT_MILLIS
                >= CANDIDATE_HANDSHAKE_WINDOW_MILLIS
                    + activation::CANDIDATE_HANDSHAKE_CLOSEOUT_GRACE_MILLIS
        );
        assert_eq!(sleeps, vec![std::time::Duration::from_millis(1); 3]);

        let mut prepare_called = false;
        let error = await_candidate_validation_armed_with(
            || {
                prepare_called = true;
                Ok(())
            },
            |_, _| Err("not_start_pending"),
            |_| {},
            std::time::Duration::from_secs(1),
            std::time::Duration::from_millis(1),
        )
        .unwrap_err();
        assert_eq!(
            error.code(),
            "authority_candidate_start_pending_refresh_failed"
        );
        assert!(!prepare_called);
    }

    #[cfg(windows)]
    #[test]
    fn candidate_arming_wait_fails_closed_on_nonpublication_error() {
        for code in [
            "authority_candidate_credential_binding_mismatch",
            "authority_candidate_credential_replayed",
            "authority_candidate_issuer_binding_mismatch",
            "authority_candidate_armed_journal_binding_mismatch",
        ] {
            let attempts = std::cell::Cell::new(0u32);
            let error = await_candidate_validation_armed_with(
                || {
                    attempts.set(attempts.get() + 1);
                    Err::<(), _>(AuthorityBootstrapError(code))
                },
                |checkpoint, wait_hint_millis| {
                    assert_eq!(checkpoint, 2);
                    assert!((1..=1_000).contains(&wait_hint_millis));
                    Ok(())
                },
                |_| panic!("non-publication error must not sleep"),
                std::time::Duration::from_secs(1),
                std::time::Duration::from_millis(1),
            )
            .unwrap_err();
            assert_eq!(error.code(), code);
            assert_eq!(attempts.get(), 1);
        }
    }

    #[test]
    fn installed_bootstrap_rejects_missing_replaced_reparsed_linked_and_acl_drift() {
        let mut absent = FakeInstalledSource {
            snapshot: None,
            expected: Vec::new(),
            load_error: Some("authority_installation_missing"),
            stable_error: None,
            calls: 0,
            stability_checks: 0,
            verified_generations: Vec::new(),
            candidate_record: None,
            candidate_prior_head: None,
        };
        assert_eq!(
            bootstrap_installed_service_from_source(&mut absent)
                .unwrap_err()
                .code(),
            "authority_installation_missing"
        );

        for (field, expected_code) in [
            (
                "stableIdentity",
                "authority_protected_artifact_readback_incomplete",
            ),
            (
                "reparseChain",
                "authority_protected_artifact_readback_incomplete",
            ),
            (
                "singleLink",
                "authority_protected_artifact_readback_incomplete",
            ),
            ("acl", "authority_protected_artifact_readback_incomplete"),
        ] {
            let (mut snapshot, expected) = valid_snapshot();
            match field {
                "stableIdentity" => snapshot.protected_artifacts[0].stable_identity = false,
                "reparseChain" => snapshot.protected_artifacts[0].reparse_free_held_chain = false,
                "singleLink" => snapshot.protected_artifacts[0].single_link = false,
                "acl" => snapshot.protected_artifacts[0].exact_owner_and_acl = false,
                _ => unreachable!(),
            }
            let mut source = installed_source(snapshot, expected);
            assert_eq!(
                bootstrap_installed_service_from_source(&mut source)
                    .unwrap_err()
                    .code(),
                expected_code,
                "{field}"
            );
            assert_eq!(source.stability_checks, 0, "{field}");
        }

        let (snapshot, expected) = valid_snapshot();
        let mut replaced_after_validation = installed_source(snapshot, expected);
        replaced_after_validation.stable_error = Some("authority_artifact_identity_changed");
        assert_eq!(
            bootstrap_installed_service_from_source(&mut replaced_after_validation)
                .unwrap_err()
                .code(),
            "authority_artifact_identity_changed"
        );
        assert_eq!(replaced_after_validation.stability_checks, 1);
    }

    #[test]
    fn installed_bootstrap_rejects_manifest_key_ledger_anchor_journal_and_service_drift() {
        let (mut snapshot, expected) = valid_snapshot();
        snapshot.trust_manifest_bytes[0] ^= 1;
        let mut source = installed_source(snapshot, expected);
        assert_eq!(
            bootstrap_installed_service_from_source(&mut source)
                .unwrap_err()
                .code(),
            "authority_trust_manifest_not_verified"
        );

        let (mut snapshot, expected) = valid_snapshot();
        let mut other_public_key = [0x33; 65];
        other_public_key[0] = 0x04;
        snapshot.key_readback = VerifiedAuthorityKeyReadback::for_test(other_public_key);
        let mut source = installed_source(snapshot, expected);
        assert_eq!(
            bootstrap_installed_service_from_source(&mut source)
                .unwrap_err()
                .code(),
            "authority_manifest_signer_key_mismatch"
        );

        let (mut snapshot, expected) = valid_snapshot();
        snapshot.ledger_identity[0] ^= 1;
        let mut source = installed_source(snapshot, expected);
        assert_eq!(
            bootstrap_installed_service_from_source(&mut source)
                .unwrap_err()
                .code(),
            "authority_ledger_identity_mismatch"
        );

        let mut anchor_drift = FakeInstalledSource {
            snapshot: None,
            expected: Vec::new(),
            load_error: Some("authority_ledger_anchor_mismatch"),
            stable_error: None,
            calls: 0,
            stability_checks: 0,
            verified_generations: Vec::new(),
            candidate_record: None,
            candidate_prior_head: None,
        };
        assert_eq!(
            bootstrap_installed_service_from_source(&mut anchor_drift)
                .unwrap_err()
                .code(),
            "authority_ledger_anchor_mismatch"
        );

        let (mut snapshot, expected) = valid_snapshot();
        snapshot.maintenance_terminal_binding = None;
        let mut source = installed_source(snapshot, expected);
        assert_eq!(
            bootstrap_installed_service_from_source(&mut source)
                .unwrap_err()
                .code(),
            "authority_maintenance_journal_not_terminal"
        );

        let (mut snapshot, expected) = valid_snapshot();
        snapshot.current_service_image = snapshot.installed_content.controller();
        let mut source = installed_source(snapshot, expected);
        assert_eq!(
            bootstrap_installed_service_from_source(&mut source)
                .unwrap_err()
                .code(),
            "authority_service_image_binding_mismatch"
        );
    }

    #[test]
    fn installed_bootstrap_accepts_one_exact_stable_snapshot() {
        let (snapshot, expected) = valid_snapshot();
        let mut source = installed_source(snapshot, expected);
        let validated = bootstrap_installed_service_from_source(&mut source).unwrap();
        assert_ne!(validated.receipt_sha256(), [0; 32]);
        assert_eq!(source.calls, 2);
        assert_eq!(source.stability_checks, 1);
    }

    #[test]
    fn installed_bootstrap_accepts_an_exact_two_epoch_update_chain() {
        let (snapshot, expected) = valid_update_snapshot();
        let expected_generation = ProtectedActiveHead::parse_canonical(&snapshot.active_head_bytes)
            .unwrap()
            .generation()
            .unwrap();
        let prior_generation = snapshot.activation_history[0].generation;
        let mut source = installed_source(snapshot, expected);
        let validated = bootstrap_installed_service_from_source(&mut source).unwrap();
        assert_eq!(validated.generation(), &expected_generation);
        assert_eq!(validated.activation_epoch, 2);
        assert_eq!(source.calls, 4);
        assert_eq!(
            source.verified_generations,
            vec![
                prior_generation,
                prior_generation,
                expected_generation,
                expected_generation,
            ]
        );
        assert_eq!(source.stability_checks, 1);
    }

    #[test]
    fn installed_bootstrap_accepts_a_complete_three_epoch_chain() {
        let (snapshot, expected) = valid_three_epoch_snapshot();
        let expected_generations = snapshot
            .activation_history
            .iter()
            .map(|entry| entry.generation)
            .chain(std::iter::once(
                ProtectedActiveHead::parse_canonical(&snapshot.active_head_bytes)
                    .unwrap()
                    .generation()
                    .unwrap(),
            ))
            .flat_map(|generation| [generation, generation])
            .collect::<Vec<_>>();
        let mut source = installed_source(snapshot, expected);
        let validated = bootstrap_installed_service_from_source(&mut source).unwrap();
        assert_eq!(validated.activation_epoch, 3);
        assert_eq!(source.calls, 6);
        assert_eq!(source.verified_generations, expected_generations);
        assert_eq!(source.stability_checks, 1);
    }

    #[test]
    fn update_chain_rejects_gaps_forks_rollbacks_and_extra_files() {
        let (mut missing, expected) = valid_update_snapshot();
        missing.activation_history.clear();
        let mut source = installed_source(missing, expected);
        assert_eq!(
            bootstrap_installed_service_from_source(&mut source)
                .unwrap_err()
                .code(),
            "authority_service_bootstrap_update_chain_invalid"
        );

        let (base, expected) = valid_update_snapshot();
        let predecessor = &base.activation_history[0];
        let predecessor_activation =
            ProtectedDetachedManifestFile::parse_canonical(&predecessor.activation_manifest_bytes)
                .unwrap()
                .signature_input()
                .unwrap()
                .digest;
        for (previous_generation, previous_digest, previous_epoch) in [
            (Some([0x81; 32]), Some(predecessor_activation), Some(1)),
            (Some(predecessor.generation), Some([0x82; 32]), Some(1)),
            (
                Some(predecessor.generation),
                Some(predecessor_activation),
                Some(0),
            ),
        ] {
            let mut snapshot = base.clone();
            let mut signatures = expected.clone();
            rewrite_current_activation_link(
                &mut snapshot,
                &mut signatures,
                previous_generation,
                previous_digest,
                previous_epoch,
            );
            let mut source = installed_source(snapshot, signatures);
            assert_eq!(
                bootstrap_installed_service_from_source(&mut source)
                    .unwrap_err()
                    .code(),
                "authority_manifest_predecessor_not_verified"
            );
        }

        let (mut extra, expected) = valid_update_snapshot();
        extra
            .activation_directory_names
            .push(format!("{}.json", hex_lower(&[0x83; 32])));
        let mut source = installed_source(extra, expected);
        assert_eq!(
            bootstrap_installed_service_from_source(&mut source)
                .unwrap_err()
                .code(),
            "authority_activation_directory_not_unique"
        );

        let (mut rollback, expected) = valid_three_epoch_snapshot();
        let head = ProtectedActiveHead::parse_canonical(&rollback.active_head_bytes).unwrap();
        rollback.active_head_bytes = ProtectedActiveHead::new(
            head.generation().unwrap(),
            head.activation_manifest_sha256().unwrap(),
            2,
            head.transaction_sha256().unwrap(),
            head.plan_sha256().unwrap(),
            head.previous_head_sha256().unwrap(),
        )
        .unwrap()
        .canonical_bytes()
        .unwrap();
        rollback
            .maintenance_terminal_binding
            .as_mut()
            .unwrap()
            .activation_epoch = 2;
        let mut source = installed_source(rollback, expected);
        assert_eq!(
            bootstrap_installed_service_from_source(&mut source)
                .unwrap_err()
                .code(),
            "authority_service_bootstrap_update_chain_invalid"
        );
    }

    #[test]
    fn update_chain_rejects_cross_key_substitution_and_manifest_tampering() {
        let (mut cross_key, expected) = valid_update_snapshot();
        cross_key.activation_history[0].key_readback = cross_key.key_readback.clone();
        let mut source = installed_source(cross_key, expected);
        assert_eq!(
            bootstrap_installed_service_from_source(&mut source)
                .unwrap_err()
                .code(),
            "authority_manifest_signer_key_mismatch"
        );

        let (mut trust_tampered, expected) = valid_update_snapshot();
        trust_tampered.activation_history[0].trust_manifest_bytes[0] ^= 1;
        let mut source = installed_source(trust_tampered, expected);
        assert_eq!(
            bootstrap_installed_service_from_source(&mut source)
                .unwrap_err()
                .code(),
            "authority_trust_manifest_not_verified"
        );

        let (mut activation_noncanonical, expected) = valid_update_snapshot();
        activation_noncanonical
            .activation_manifest_bytes
            .push(b'\n');
        let mut source = installed_source(activation_noncanonical, expected);
        assert_eq!(
            bootstrap_installed_service_from_source(&mut source)
                .unwrap_err()
                .code(),
            "authority_activation_manifest_not_verified"
        );

        let (mut binary_drift, expected) = valid_update_snapshot();
        binary_drift.installed_content = AuthorityInstallContent::new(
            AuthorityPayloadDigest::new([0xa1; 32], 301).unwrap(),
            AuthorityPayloadDigest::new([0xa2; 32], 302).unwrap(),
            AuthorityPayloadDigest::new([0xa3; 32], 303).unwrap(),
            AuthorityPayloadDigest::new([0xa4; 32], 304).unwrap(),
            AuthorityPayloadDigest::new([0xa5; 32], 305).unwrap(),
            AuthorityPayloadDigest::new([0xa6; 32], 306).unwrap(),
        )
        .unwrap();
        binary_drift.current_service_image = binary_drift.installed_content.service();
        binary_drift.candidate_service_process = CandidateProcessEvidence::from_held_process(
            binary_drift.service_process_id,
            binary_drift.service_process_creation_time,
            *binary_drift.current_service_image.sha256(),
            binary_drift.current_service_image.byte_length(),
            0x4141,
            [0xa4; 16],
            1,
            0x20,
        )
        .unwrap();
        let mut source = installed_source(binary_drift, expected);
        assert_eq!(
            bootstrap_installed_service_from_source(&mut source)
                .unwrap_err()
                .code(),
            "authority_active_head_plan_binding_mismatch"
        );
    }

    #[test]
    fn update_head_and_terminal_must_match_without_fabricating_historical_plan_inputs() {
        let (mut snapshot, expected) = valid_update_snapshot();
        let head = ProtectedActiveHead::parse_canonical(&snapshot.active_head_bytes).unwrap();
        let plan_sha256 = [0x91; 32];
        let transaction_sha256 = [0x92; 32];
        snapshot.active_head_bytes = ProtectedActiveHead::new(
            head.generation().unwrap(),
            head.activation_manifest_sha256().unwrap(),
            head.activation_epoch(),
            transaction_sha256,
            plan_sha256,
            head.previous_head_sha256().unwrap(),
        )
        .unwrap()
        .canonical_bytes()
        .unwrap();
        snapshot.maintenance_terminal_binding = Some(AuthorityBootstrapTerminalBinding {
            generation: head.generation().unwrap(),
            plan_sha256,
            transaction_sha256,
            activation_epoch: head.activation_epoch(),
        });

        let mut stale_source = installed_source(snapshot.clone(), expected.clone());
        assert_eq!(
            bootstrap_installed_service_from_source(&mut stale_source)
                .unwrap_err()
                .code(),
            "authority_runner_policy_bootstrap_binding_mismatch"
        );

        snapshot.runner_policy_state = CanonicalRunnerPolicyState::canonical_test_fixture(
            head.generation().unwrap(),
            transaction_sha256,
        )
        .descriptor()
        .unwrap();
        snapshot.runner_policy_sealed_identity =
            RunnerPolicySealedIdentity::exact_test_fixture(0x7d);
        let mut source = installed_source(snapshot.clone(), expected.clone());
        assert_eq!(
            bootstrap_installed_service_from_source(&mut source)
                .unwrap()
                .activation_epoch,
            2
        );

        for field in 0..4 {
            let mut drifted = snapshot.clone();
            let binding = drifted.maintenance_terminal_binding.as_mut().unwrap();
            match field {
                0 => binding.generation[0] ^= 1,
                1 => binding.plan_sha256[0] ^= 1,
                2 => binding.transaction_sha256[0] ^= 1,
                3 => binding.activation_epoch = 1,
                _ => unreachable!(),
            }
            let mut source = installed_source(drifted, expected.clone());
            assert_eq!(
                bootstrap_installed_service_from_source(&mut source)
                    .unwrap_err()
                    .code(),
                "authority_maintenance_journal_not_terminal",
                "terminal field {field}"
            );
        }
    }

    #[test]
    fn exact_genesis_bootstrap_validates_and_binds_a_stable_receipt() {
        let (snapshot, expected) = valid_snapshot();
        let expected_head_sha256: [u8; 32] = Sha256::digest(&snapshot.active_head_bytes).into();
        let expected_head =
            ProtectedActiveHead::parse_canonical(&snapshot.active_head_bytes).unwrap();
        let expected_plan = preview_install(&layout(), content()).unwrap();
        let mut verifier = FakeVerifier {
            expected,
            fail_at: None,
            calls: 0,
        };
        let first = validate_bootstrap_snapshot(&layout(), &snapshot, &mut verifier).unwrap();
        assert_eq!(verifier.calls, 2);
        assert_eq!(first.active_ticket_count(), 0);
        assert_ne!(first.receipt_sha256(), [0; 32]);
        assert_eq!(first.plan_sha256(), &expected_head.plan_sha256().unwrap());
        assert_eq!(
            first.transaction_sha256(),
            &expected_head.transaction_sha256().unwrap()
        );
        assert_eq!(first.active_head_sha256(), &expected_head_sha256);
        assert_eq!(first.activation_epoch(), 1);
        assert_eq!(
            first.exact_service_configuration_sha256(),
            &expected_plan
                .exact_target_service_plan()
                .unwrap()
                .exact_service_configuration_sha256()
        );
        assert_eq!(first.service_process_id(), snapshot.service_process_id);
        assert_eq!(
            first.service_process_creation_time(),
            snapshot.service_process_creation_time
        );
        assert_eq!(
            first.generation(),
            &expected_plan.generation_sha256().unwrap()
        );
    }

    #[test]
    fn stable_bootstrap_receipt_excludes_mutable_ledger_progress() {
        let (snapshot, expected) = valid_snapshot();
        let mut verifier = FakeVerifier {
            expected,
            fail_at: None,
            calls: 0,
        };
        let baseline = validate_bootstrap_snapshot(&layout(), &snapshot, &mut verifier).unwrap();
        let baseline_receipt = baseline.receipt_sha256();

        let mut advanced = baseline;
        advanced.ledger_frame_count = 2;
        advanced.ledger_byte_length = 2 * FRAME_SIZE as u64;
        advanced.ledger_sha256 = [0x61; 32];
        advanced.ledger_anchor_byte_length = 4 * ANCHOR_RECORD_SIZE as u64;
        advanced.ledger_anchor_sha256 = [0x62; 32];
        advanced.active_ticket_count = 1;
        assert_eq!(advanced.receipt_sha256(), baseline_receipt);

        let mut controller_drift = baseline;
        controller_drift.controller_binary_sha256[0] ^= 1;
        assert_ne!(controller_drift.receipt_sha256(), baseline_receipt);

        let mut install_helper_drift = baseline;
        install_helper_drift.install_helper_binary_sha256[0] ^= 1;
        assert_ne!(install_helper_drift.receipt_sha256(), baseline_receipt);

        let mut runner_same_length_drift = baseline;
        runner_same_length_drift.runner_policy_state =
            RunnerPolicyStateDescriptor::exact_test_fixture(
                *baseline.generation(),
                *baseline.transaction_sha256(),
                baseline.runner_policy_state.byte_length(),
                [0x71; 32],
                baseline.runner_policy_state.binding_sha256(),
            );
        assert_ne!(runner_same_length_drift.receipt_sha256(), baseline_receipt);

        let mut runner_binding_drift = baseline;
        runner_binding_drift.runner_policy_state = RunnerPolicyStateDescriptor::exact_test_fixture(
            *baseline.generation(),
            *baseline.transaction_sha256(),
            baseline.runner_policy_state.byte_length(),
            baseline.runner_policy_state.bytes_sha256(),
            [0x72; 32],
        );
        assert_ne!(runner_binding_drift.receipt_sha256(), baseline_receipt);

        for field in 0..4 {
            let mut runner_identity_drift = baseline;
            runner_identity_drift.runner_policy_sealed_identity = baseline
                .runner_policy_sealed_identity
                .with_field_drift_for_test(field);
            assert_ne!(
                runner_identity_drift.receipt_sha256(),
                baseline_receipt,
                "runner sealed identity field {field}"
            );
        }

        advanced.ledger_identity[0] ^= 1;
        assert_ne!(advanced.receipt_sha256(), baseline_receipt);
    }

    #[test]
    fn legacy_bootstrap_schema_is_rejected() {
        let (mut snapshot, expected) = valid_snapshot();
        snapshot.schema = "vrcforge.primitive_evidence_authority_service_bootstrap.v3";
        let mut verifier = FakeVerifier {
            expected,
            fail_at: None,
            calls: 0,
        };
        assert_eq!(
            validate_bootstrap_snapshot(&layout(), &snapshot, &mut verifier)
                .unwrap_err()
                .code(),
            "authority_service_bootstrap_schema_invalid"
        );
    }

    #[test]
    fn runner_policy_snapshot_requires_exact_outer_generation_and_transaction() {
        for field in 0..2 {
            let (mut snapshot, expected) = valid_snapshot();
            let exact = snapshot.runner_policy_state;
            let generation = if field == 0 {
                [0x91; 32]
            } else {
                exact.generation_sha256()
            };
            let transaction = if field == 1 {
                [0x92; 32]
            } else {
                exact.transaction_sha256()
            };
            snapshot.runner_policy_state = RunnerPolicyStateDescriptor::exact_test_fixture(
                generation,
                transaction,
                exact.byte_length(),
                exact.bytes_sha256(),
                exact.binding_sha256(),
            );
            let mut verifier = FakeVerifier {
                expected,
                fail_at: None,
                calls: 0,
            };
            assert_eq!(
                validate_bootstrap_snapshot(&layout(), &snapshot, &mut verifier)
                    .unwrap_err()
                    .code(),
                "authority_runner_policy_bootstrap_binding_mismatch"
            );
        }
    }

    #[test]
    fn committed_bootstrap_requires_runner_policy_while_candidate_shape_stays_fixed() {
        assert_eq!(REQUIRED_ARTIFACTS.len(), 12);
        assert_eq!(CANDIDATE_REQUIRED_ARTIFACTS.len(), 10);
        assert!(REQUIRED_ARTIFACTS.contains(&BootstrapArtifactKind::RunnerPolicyState));
        assert!(!CANDIDATE_REQUIRED_ARTIFACTS.contains(&BootstrapArtifactKind::RunnerPolicyState));
    }

    #[cfg(windows)]
    #[test]
    fn runner_policy_capability_slot_has_one_public_typed_take_and_noncloneable_owners() {
        let mut slot = Some(7u8);
        assert_eq!(take_runner_policy_capability_once(&mut slot).unwrap(), 7);
        assert_eq!(
            take_runner_policy_capability_once(&mut slot)
                .unwrap_err()
                .code(),
            "authority_runner_policy_capability_already_taken"
        );
        assert!(std::mem::needs_drop::<AuthenticatedRunnerPolicyCapability>());
        assert!(std::mem::needs_drop::<AuthenticatedRunnerPolicyReadback>());
        assert!(std::mem::needs_drop::<AuthenticatedRunnerLaunchPolicy>());

        let source = include_str!("bootstrap.rs");
        let production_source = source
            .split("#[cfg(test)]\nmod tests")
            .next()
            .expect("bootstrap production source");
        let native = include_str!("bootstrap_windows.rs");
        assert!(production_source.contains("struct AuthenticatedRunnerPolicyCapability"));
        assert!(
            !production_source.contains("pub(crate) struct AuthenticatedRunnerPolicyCapability")
        );
        assert!(production_source.contains("fn read_once("));
        assert!(!production_source.contains("pub(crate) fn read_once("));
        assert_eq!(
            production_source
                .matches("pub(crate) fn take_runner_launch_policy(")
                .count(),
            1
        );
        assert!(!production_source.contains("pub(crate) fn take_runner_policy_capability("));
        assert!(production_source.contains("capability.read_once()?.into_launch_policy()?"));
        assert!(native.contains("pub(super) struct NativeAuthenticatedRunnerPolicyCapability"));
        assert!(native.contains(".runner_policy_state"));
        assert!(native.contains(".take()"));
        assert!(!production_source
            .contains("derive(Clone)\nstruct AuthenticatedRunnerPolicyCapability"));
        assert!(!production_source
            .contains("derive(Clone)\npub(crate) struct AuthenticatedRunnerLaunchPolicy"));
        assert!(!native.contains(
            "derive(Clone)\npub(super) struct NativeAuthenticatedRunnerPolicyCapability"
        ));
    }

    #[cfg(windows)]
    #[test]
    fn published_runtime_binding_is_exactly_cross_bound_to_bootstrap() {
        let (snapshot, expected) = valid_snapshot();
        let mut verifier = FakeVerifier {
            expected,
            fail_at: None,
            calls: 0,
        };
        let validated = validate_bootstrap_snapshot(&layout(), &snapshot, &mut verifier).unwrap();
        let make = |generation,
                    plan,
                    transaction,
                    operation,
                    service_configuration,
                    service_image,
                    active_head,
                    activation_manifest,
                    activation_epoch| {
            VerifiedPublishedRuntimeBindingProjection::for_bootstrap_test(
                generation,
                plan,
                transaction,
                operation,
                service_configuration,
                service_image,
                active_head,
                activation_manifest,
                activation_epoch,
                validated.runner_policy_state.byte_length(),
                validated.runner_policy_state.bytes_sha256(),
                validated.runner_policy_state.binding_sha256(),
                validated.runner_policy_sealed_identity.volume_serial(),
                validated.runner_policy_sealed_identity.file_id(),
                validated.runner_policy_sealed_identity.link_count(),
                validated.runner_policy_sealed_identity.attributes(),
            )
        };
        let operation = if validated.activation_epoch == 1 {
            AuthorityMaintenanceOperation::Install
        } else {
            AuthorityMaintenanceOperation::Update
        };
        let exact = make(
            validated.generation,
            validated.plan_sha256,
            validated.transaction_sha256,
            operation,
            validated.exact_service_configuration_sha256,
            validated.service_binary_sha256,
            validated.active_head_sha256,
            validated.activation_manifest_sha256,
            validated.activation_epoch,
        );
        validate_published_runtime_binding(&validated, exact).unwrap();

        let drift = |value: [u8; 32]| {
            let mut changed = value;
            changed[0] ^= 1;
            changed
        };
        let mismatches = [
            make(
                drift(validated.generation),
                validated.plan_sha256,
                validated.transaction_sha256,
                operation,
                validated.exact_service_configuration_sha256,
                validated.service_binary_sha256,
                validated.active_head_sha256,
                validated.activation_manifest_sha256,
                validated.activation_epoch,
            ),
            make(
                validated.generation,
                drift(validated.plan_sha256),
                validated.transaction_sha256,
                operation,
                validated.exact_service_configuration_sha256,
                validated.service_binary_sha256,
                validated.active_head_sha256,
                validated.activation_manifest_sha256,
                validated.activation_epoch,
            ),
            make(
                validated.generation,
                validated.plan_sha256,
                drift(validated.transaction_sha256),
                operation,
                validated.exact_service_configuration_sha256,
                validated.service_binary_sha256,
                validated.active_head_sha256,
                validated.activation_manifest_sha256,
                validated.activation_epoch,
            ),
            make(
                validated.generation,
                validated.plan_sha256,
                validated.transaction_sha256,
                AuthorityMaintenanceOperation::Retire,
                validated.exact_service_configuration_sha256,
                validated.service_binary_sha256,
                validated.active_head_sha256,
                validated.activation_manifest_sha256,
                validated.activation_epoch,
            ),
            make(
                validated.generation,
                validated.plan_sha256,
                validated.transaction_sha256,
                operation,
                drift(validated.exact_service_configuration_sha256),
                validated.service_binary_sha256,
                validated.active_head_sha256,
                validated.activation_manifest_sha256,
                validated.activation_epoch,
            ),
            make(
                validated.generation,
                validated.plan_sha256,
                validated.transaction_sha256,
                operation,
                validated.exact_service_configuration_sha256,
                drift(validated.service_binary_sha256),
                validated.active_head_sha256,
                validated.activation_manifest_sha256,
                validated.activation_epoch,
            ),
            make(
                validated.generation,
                validated.plan_sha256,
                validated.transaction_sha256,
                operation,
                validated.exact_service_configuration_sha256,
                validated.service_binary_sha256,
                drift(validated.active_head_sha256),
                validated.activation_manifest_sha256,
                validated.activation_epoch,
            ),
            make(
                validated.generation,
                validated.plan_sha256,
                validated.transaction_sha256,
                operation,
                validated.exact_service_configuration_sha256,
                validated.service_binary_sha256,
                validated.active_head_sha256,
                drift(validated.activation_manifest_sha256),
                validated.activation_epoch,
            ),
            make(
                validated.generation,
                validated.plan_sha256,
                validated.transaction_sha256,
                operation,
                validated.exact_service_configuration_sha256,
                validated.service_binary_sha256,
                validated.active_head_sha256,
                validated.activation_manifest_sha256,
                validated.activation_epoch + 1,
            ),
        ];
        for mismatch in mismatches {
            assert_eq!(
                validate_published_runtime_binding(&validated, mismatch)
                    .unwrap_err()
                    .code(),
                "authority_final_commit_runtime_binding_mismatch"
            );
        }

        assert_eq!(
            VerifiedPublishedRuntimeBindingProjection::COMPLETE_FIELD_COUNT,
            41
        );
        for index in 0..VerifiedPublishedRuntimeBindingProjection::COMPLETE_FIELD_COUNT {
            let mismatch = exact.with_complete_field_drift_for_test(index);
            assert_ne!(
                exact.complete_binding_sha256(),
                mismatch.complete_binding_sha256()
            );
            assert!(validate_exact_published_runtime_binding(&validated, exact, mismatch).is_err());
        }

        let final_commit_receipt_sha256 = [0xf1; 32];
        let policy = authenticated_final_commit_policy_binding(
            &validated,
            exact,
            final_commit_receipt_sha256,
        )
        .unwrap();
        assert_eq!(
            policy.final_commit_receipt_sha256(),
            &final_commit_receipt_sha256
        );
        assert_eq!(
            policy.published_runtime_binding_sha256(),
            exact.complete_binding_sha256()
        );
        assert_eq!(
            policy.runtime_source_manifest(),
            validated.runtime_source_manifest
        );
        assert_eq!(policy.runner_policy_state(), validated.runner_policy_state);
        assert_eq!(
            policy.runner_policy_sealed_identity(),
            validated.runner_policy_sealed_identity
        );
        assert_eq!(
            policy.lifecycle_driver_binary_sha256(),
            validated.lifecycle_driver_binary_sha256()
        );
        assert_eq!(
            policy.lifecycle_driver_binary_byte_length(),
            validated.lifecycle_driver_binary_byte_length
        );
        assert_eq!(
            policy.bridge_launcher_binary_sha256(),
            validated.bridge_launcher_binary_sha256()
        );
        assert_eq!(
            policy.bridge_launcher_binary_byte_length(),
            validated.bridge_launcher_binary_byte_length
        );
    }

    #[cfg(windows)]
    #[test]
    fn authenticated_generation_binding_uses_complete_live_readback() {
        let (snapshot, expected) = valid_snapshot();
        let mut verifier = FakeVerifier {
            expected,
            fail_at: None,
            calls: 0,
        };
        let validated = validate_bootstrap_snapshot(&layout(), &snapshot, &mut verifier).unwrap();
        let projection = VerifiedPublishedRuntimeBindingProjection::for_bootstrap_test(
            validated.generation,
            validated.plan_sha256,
            validated.transaction_sha256,
            AuthorityMaintenanceOperation::Install,
            validated.exact_service_configuration_sha256,
            validated.service_binary_sha256,
            validated.active_head_sha256,
            validated.activation_manifest_sha256,
            validated.activation_epoch,
            validated.runner_policy_state.byte_length(),
            validated.runner_policy_state.bytes_sha256(),
            validated.runner_policy_state.binding_sha256(),
            validated.runner_policy_sealed_identity.volume_serial(),
            validated.runner_policy_sealed_identity.file_id(),
            validated.runner_policy_sealed_identity.link_count(),
            validated.runner_policy_sealed_identity.attributes(),
        );
        let policy =
            authenticated_final_commit_policy_binding(&validated, projection, [0xf1; 32]).unwrap();
        let native = native_snapshot::NativeAuthenticatedGenerationBindingReadback {
            service_executable_path_sha256: [0xe1; 32],
            service_executable_file_identity_sha256: [0xe2; 32],
            protected_key_readback_sha256: [0xe3; 32],
            scm_readback_sha256: [0xe4; 32],
        };
        let readback = AuthenticatedGenerationBindingReadback::from_authenticated_boundary(
            &validated, &policy, native,
        )
        .unwrap();
        assert_eq!(readback.current_generation(), validated.generation());
        assert_eq!(
            readback.service_executable_sha256(),
            validated.service_binary_sha256()
        );
        assert_eq!(
            readback.controller_executable_sha256(),
            validated.controller_binary_sha256()
        );
        assert_eq!(
            readback.install_helper_executable_sha256(),
            validated.install_helper_binary_sha256()
        );
        assert_eq!(
            readback.lifecycle_driver_executable_sha256(),
            validated.lifecycle_driver_binary_sha256()
        );
        assert_eq!(
            readback.lifecycle_driver_executable_byte_length(),
            validated.lifecycle_driver_binary_byte_length
        );
        assert_eq!(
            readback.bridge_launcher_executable_sha256(),
            validated.bridge_launcher_binary_sha256()
        );
        assert_eq!(
            readback.bridge_launcher_executable_byte_length(),
            validated.bridge_launcher_binary_byte_length
        );
        assert_eq!(
            readback.installed_layout_sha256(),
            validated.installed_layout_sha256()
        );
        assert_eq!(
            readback.ledger_identity_sha256(),
            validated.ledger_identity()
        );
        assert_eq!(readback.service_executable_path_sha256(), &[0xe1; 32]);
        assert_eq!(
            readback.service_executable_file_identity_sha256(),
            &[0xe2; 32]
        );
        assert_eq!(
            readback.service_process_id(),
            validated.service_process_id()
        );
        assert_eq!(
            readback.service_process_started_at(),
            validated.service_process_creation_time()
        );
        assert_eq!(
            readback.protected_manifest_readback_sha256(),
            validated.protected_manifest_sha256()
        );
        assert_eq!(readback.protected_key_readback_sha256(), &[0xe3; 32]);
        assert_eq!(readback.signer_key_id(), validated.signer_key_id());
        assert_eq!(
            readback.protected_ledger_readback_sha256(),
            &validated.protected_ledger_readback_sha256().unwrap()
        );
        assert_eq!(readback.scm_readback_sha256(), &[0xe4; 32]);
        assert_eq!(readback.final_commit_receipt_sha256(), &[0xf1; 32]);

        let mut invalid_readbacks = Vec::new();
        for index in 0..16 {
            let mut invalid = readback;
            match index {
                0 => invalid.current_generation = [0; 32],
                1 => invalid.service_executable_sha256 = [0; 32],
                2 => invalid.controller_executable_sha256 = [0; 32],
                3 => invalid.install_helper_executable_sha256 = [0; 32],
                4 => invalid.lifecycle_driver_executable_sha256 = [0; 32],
                5 => invalid.bridge_launcher_executable_sha256 = [0; 32],
                6 => invalid.installed_layout_sha256 = [0; 32],
                7 => invalid.ledger_identity_sha256 = [0; 32],
                8 => invalid.service_executable_path_sha256 = [0; 32],
                9 => invalid.service_executable_file_identity_sha256 = [0; 32],
                10 => invalid.protected_manifest_readback_sha256 = [0; 32],
                11 => invalid.protected_key_readback_sha256 = [0; 32],
                12 => invalid.signer_key_id = [0; 32],
                13 => invalid.protected_ledger_readback_sha256 = [0; 32],
                14 => invalid.scm_readback_sha256 = [0; 32],
                15 => invalid.final_commit_receipt_sha256 = [0; 32],
                _ => unreachable!(),
            }
            invalid_readbacks.push(invalid);
        }
        let mut missing_process = readback;
        missing_process.service_process_id = 0;
        invalid_readbacks.push(missing_process);
        let mut missing_start = readback;
        missing_start.service_process_started_at = 0;
        invalid_readbacks.push(missing_start);
        let mut missing_lifecycle_length = readback;
        missing_lifecycle_length.lifecycle_driver_executable_byte_length = 0;
        invalid_readbacks.push(missing_lifecycle_length);
        let mut missing_bridge_length = readback;
        missing_bridge_length.bridge_launcher_executable_byte_length = 0;
        invalid_readbacks.push(missing_bridge_length);
        for invalid in invalid_readbacks {
            assert_eq!(
                invalid.validate().unwrap_err().code(),
                "authority_generation_binding_readback_invalid"
            );
        }

        for field in 0..17 {
            let mut mismatched =
                authenticated_final_commit_policy_binding(&validated, projection, [0xf1; 32])
                    .unwrap();
            match field {
                0 => mismatched.generation[0] ^= 1,
                1 => mismatched.signer_key_id[0] ^= 1,
                2 => mismatched.protected_manifest_sha256[0] ^= 1,
                3 => mismatched.installed_layout_sha256[0] ^= 1,
                4 => mismatched.exact_service_configuration_sha256[0] ^= 1,
                5 => mismatched.service_binary_sha256[0] ^= 1,
                6 => mismatched.controller_binary_sha256[0] ^= 1,
                7 => mismatched.install_helper_binary_sha256[0] ^= 1,
                8 => mismatched.lifecycle_driver_binary_sha256[0] ^= 1,
                9 => mismatched.lifecycle_driver_binary_byte_length += 1,
                10 => mismatched.bridge_launcher_binary_sha256[0] ^= 1,
                11 => mismatched.bridge_launcher_binary_byte_length += 1,
                12 => mismatched.ledger_identity[0] ^= 1,
                13 => mismatched.service_process_id += 1,
                14 => mismatched.service_process_creation_time += 1,
                15 => {
                    mismatched.runtime_source_manifest =
                        AuthorityPayloadDigest::new([0xf2; 32], 129).unwrap()
                }
                16 => {
                    mismatched.runner_policy_state = RunnerPolicyStateDescriptor::exact_test_fixture(
                        *validated.generation(),
                        *validated.transaction_sha256(),
                        validated.runner_policy_state.byte_length(),
                        [0xf3; 32],
                        validated.runner_policy_state.binding_sha256(),
                    )
                }
                _ => unreachable!(),
            }
            assert_eq!(
                AuthenticatedGenerationBindingReadback::from_authenticated_boundary(
                    &validated,
                    &mismatched,
                    native,
                )
                .unwrap_err()
                .code(),
                "authority_generation_binding_boundary_mismatch",
                "binding field {field}"
            );
        }

        let baseline_ledger = validated.protected_ledger_readback_sha256().unwrap();
        for field in 0..7 {
            let mut drifted = validated;
            match field {
                0 => drifted.ledger_identity[0] ^= 1,
                1 => drifted.ledger_frame_count += 1,
                2 => drifted.ledger_byte_length += 1,
                3 => drifted.ledger_sha256[0] ^= 1,
                4 => drifted.ledger_anchor_byte_length += 1,
                5 => drifted.ledger_anchor_sha256[0] ^= 1,
                6 => drifted.active_ticket_count += 1,
                _ => unreachable!(),
            }
            assert_ne!(
                drifted.protected_ledger_readback_sha256().unwrap(),
                baseline_ledger,
                "ledger field {field}"
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn authenticated_controller_source_requires_the_exact_final_commit_boundary() {
        let (snapshot, expected) = valid_snapshot();
        let mut verifier = FakeVerifier {
            expected,
            fail_at: None,
            calls: 0,
        };
        let validated = validate_bootstrap_snapshot(&layout(), &snapshot, &mut verifier).unwrap();
        let projection = VerifiedPublishedRuntimeBindingProjection::for_bootstrap_test(
            validated.generation,
            validated.plan_sha256,
            validated.transaction_sha256,
            AuthorityMaintenanceOperation::Install,
            validated.exact_service_configuration_sha256,
            validated.service_binary_sha256,
            validated.active_head_sha256,
            validated.activation_manifest_sha256,
            validated.activation_epoch,
            validated.runner_policy_state.byte_length(),
            validated.runner_policy_state.bytes_sha256(),
            validated.runner_policy_state.binding_sha256(),
            validated.runner_policy_sealed_identity.volume_serial(),
            validated.runner_policy_sealed_identity.file_id(),
            validated.runner_policy_sealed_identity.link_count(),
            validated.runner_policy_sealed_identity.attributes(),
        );
        let policy =
            authenticated_final_commit_policy_binding(&validated, projection, [0xf1; 32]).unwrap();
        let descriptor = validated.controller_binary_descriptor().unwrap();
        let controller_path = layout()
            .controller_executable_for_generation(validated.generation())
            .unwrap();
        let native = native_snapshot::NativeAuthenticatedControllerSourceReadback {
            controller_path: controller_path.clone(),
            descriptor,
            volume_serial: 0x31,
            file_id: 0x41,
            link_count: 1,
        };
        let readback = AuthenticatedControllerSourceReadback::from_authenticated_boundary_for_test(
            &validated,
            &policy,
            native.clone(),
        )
        .unwrap();
        assert_eq!(readback.generation(), validated.generation());
        assert_eq!(
            readback.service_process_id(),
            validated.service_process_id()
        );
        assert_eq!(
            readback.service_process_started_at(),
            validated.service_process_creation_time()
        );
        assert_eq!(readback.controller_path(), controller_path);
        assert_eq!(
            readback.controller_sha256(),
            validated.controller_binary_sha256()
        );
        assert_eq!(
            readback.controller_byte_length(),
            validated.controller_binary_byte_length()
        );
        assert_eq!(readback.volume_serial(), 0x31);
        assert_eq!(readback.file_id(), 0x41);
        assert_eq!(readback.link_count(), 1);
        assert_eq!(
            readback.installed_layout_sha256(),
            validated.installed_layout_sha256()
        );
        assert_eq!(readback.final_commit_receipt_sha256(), &[0xf1; 32]);
        assert!(readback
            .source_binding_sha256()
            .iter()
            .any(|value| *value != 0));
        let invalid_native = [
            native_snapshot::NativeAuthenticatedControllerSourceReadback {
                descriptor: AuthorityPayloadDigest::new([0xe1; 32], descriptor.byte_length())
                    .unwrap(),
                ..native.clone()
            },
            native_snapshot::NativeAuthenticatedControllerSourceReadback {
                descriptor: AuthorityPayloadDigest::new(
                    *descriptor.sha256(),
                    descriptor.byte_length() + 1,
                )
                .unwrap(),
                ..native.clone()
            },
            native_snapshot::NativeAuthenticatedControllerSourceReadback {
                volume_serial: 0,
                ..native.clone()
            },
            native_snapshot::NativeAuthenticatedControllerSourceReadback {
                file_id: 0,
                ..native.clone()
            },
            native_snapshot::NativeAuthenticatedControllerSourceReadback {
                link_count: 2,
                ..native.clone()
            },
            native_snapshot::NativeAuthenticatedControllerSourceReadback {
                controller_path: PathBuf::from("relative-controller.exe"),
                ..native.clone()
            },
            native_snapshot::NativeAuthenticatedControllerSourceReadback {
                controller_path: PathBuf::from(r"C:\Program Files\VRCForge\..\controller.exe"),
                ..native.clone()
            },
        ];
        for invalid in invalid_native {
            assert_eq!(
                AuthenticatedControllerSourceReadback::from_authenticated_boundary_for_test(
                    &validated, &policy, invalid,
                )
                .unwrap_err()
                .code(),
                "authority_controller_source_readback_invalid"
            );
        }

        for field in 0..6 {
            let mut mismatched =
                authenticated_final_commit_policy_binding(&validated, projection, [0xf1; 32])
                    .unwrap();
            match field {
                0 => mismatched.generation[0] ^= 1,
                1 => mismatched.service_process_id += 1,
                2 => mismatched.service_process_creation_time += 1,
                3 => mismatched.controller_binary_sha256[0] ^= 1,
                4 => mismatched.controller_binary_byte_length += 1,
                5 => mismatched.installed_layout_sha256[0] ^= 1,
                _ => unreachable!(),
            }
            assert_eq!(
                AuthenticatedControllerSourceReadback::from_authenticated_boundary_for_test(
                    &validated,
                    &mismatched,
                    native.clone(),
                )
                .unwrap_err()
                .code(),
                "authority_controller_source_readback_invalid",
                "binding field {field}"
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn authenticated_install_helper_source_requires_the_exact_final_commit_boundary() {
        let (snapshot, expected) = valid_snapshot();
        let mut verifier = FakeVerifier {
            expected,
            fail_at: None,
            calls: 0,
        };
        let validated = validate_bootstrap_snapshot(&layout(), &snapshot, &mut verifier).unwrap();
        let projection = VerifiedPublishedRuntimeBindingProjection::for_bootstrap_test(
            validated.generation,
            validated.plan_sha256,
            validated.transaction_sha256,
            AuthorityMaintenanceOperation::Install,
            validated.exact_service_configuration_sha256,
            validated.service_binary_sha256,
            validated.active_head_sha256,
            validated.activation_manifest_sha256,
            validated.activation_epoch,
            validated.runner_policy_state.byte_length(),
            validated.runner_policy_state.bytes_sha256(),
            validated.runner_policy_state.binding_sha256(),
            validated.runner_policy_sealed_identity.volume_serial(),
            validated.runner_policy_sealed_identity.file_id(),
            validated.runner_policy_sealed_identity.link_count(),
            validated.runner_policy_sealed_identity.attributes(),
        );
        let policy =
            authenticated_final_commit_policy_binding(&validated, projection, [0xf2; 32]).unwrap();
        let descriptor = validated.install_helper_binary_descriptor().unwrap();
        let install_helper_path = layout()
            .install_helper_executable_for_generation(validated.generation())
            .unwrap();
        let native = native_snapshot::NativeAuthenticatedInstallHelperSourceReadback {
            install_helper_path: install_helper_path.clone(),
            descriptor,
            volume_serial: 0x51,
            file_id: 0x61,
            link_count: 1,
        };
        let readback =
            AuthenticatedInstallHelperSourceReadback::from_authenticated_boundary_for_test(
                &validated,
                &policy,
                native.clone(),
            )
            .unwrap();
        assert_eq!(readback.generation(), validated.generation());
        assert_eq!(
            readback.service_process_id(),
            validated.service_process_id()
        );
        assert_eq!(
            readback.service_process_started_at(),
            validated.service_process_creation_time()
        );
        assert_eq!(readback.install_helper_path(), install_helper_path);
        assert_eq!(
            readback.install_helper_sha256(),
            validated.install_helper_binary_sha256()
        );
        assert_eq!(
            readback.install_helper_byte_length(),
            validated.install_helper_binary_byte_length()
        );
        assert_eq!(readback.volume_serial(), 0x51);
        assert_eq!(readback.file_id(), 0x61);
        assert_eq!(readback.link_count(), 1);
        assert_eq!(
            readback.installed_layout_sha256(),
            validated.installed_layout_sha256()
        );
        assert_eq!(readback.final_commit_receipt_sha256(), &[0xf2; 32]);
        assert!(readback
            .source_binding_sha256()
            .iter()
            .any(|value| *value != 0));
        let replacement =
            AuthenticatedInstallHelperSourceReadback::from_authenticated_boundary_for_test(
                &validated,
                &policy,
                native_snapshot::NativeAuthenticatedInstallHelperSourceReadback {
                    file_id: native.file_id + 1,
                    ..native.clone()
                },
            )
            .unwrap();
        assert_ne!(
            replacement.source_binding_sha256(),
            readback.source_binding_sha256()
        );

        let invalid_native = [
            native_snapshot::NativeAuthenticatedInstallHelperSourceReadback {
                descriptor: AuthorityPayloadDigest::new([0xe2; 32], descriptor.byte_length())
                    .unwrap(),
                ..native.clone()
            },
            native_snapshot::NativeAuthenticatedInstallHelperSourceReadback {
                descriptor: AuthorityPayloadDigest::new(
                    *descriptor.sha256(),
                    descriptor.byte_length() + 1,
                )
                .unwrap(),
                ..native.clone()
            },
            native_snapshot::NativeAuthenticatedInstallHelperSourceReadback {
                volume_serial: 0,
                ..native.clone()
            },
            native_snapshot::NativeAuthenticatedInstallHelperSourceReadback {
                file_id: 0,
                ..native.clone()
            },
            native_snapshot::NativeAuthenticatedInstallHelperSourceReadback {
                link_count: 2,
                ..native.clone()
            },
            native_snapshot::NativeAuthenticatedInstallHelperSourceReadback {
                install_helper_path: PathBuf::from("relative-helper.exe"),
                ..native.clone()
            },
            native_snapshot::NativeAuthenticatedInstallHelperSourceReadback {
                install_helper_path: PathBuf::from(
                    r"C:\Program Files\VRCForge\..\runtime-broker.exe",
                ),
                ..native.clone()
            },
        ];
        for invalid in invalid_native {
            assert_eq!(
                AuthenticatedInstallHelperSourceReadback::from_authenticated_boundary_for_test(
                    &validated, &policy, invalid,
                )
                .unwrap_err()
                .code(),
                "authority_install_helper_source_readback_invalid"
            );
        }

        for field in 0..6 {
            let mut mismatched =
                authenticated_final_commit_policy_binding(&validated, projection, [0xf2; 32])
                    .unwrap();
            match field {
                0 => mismatched.generation[0] ^= 1,
                1 => mismatched.service_process_id += 1,
                2 => mismatched.service_process_creation_time += 1,
                3 => mismatched.install_helper_binary_sha256[0] ^= 1,
                4 => mismatched.install_helper_binary_byte_length += 1,
                5 => mismatched.installed_layout_sha256[0] ^= 1,
                _ => unreachable!(),
            }
            assert_eq!(
                AuthenticatedInstallHelperSourceReadback::from_authenticated_boundary_for_test(
                    &validated,
                    &mismatched,
                    native.clone(),
                )
                .unwrap_err()
                .code(),
                "authority_install_helper_source_readback_invalid",
                "binding field {field}"
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn final_commit_bootstrap_requires_the_published_capability_lane() {
        let source = include_str!("bootstrap.rs");
        assert!(source.contains("verify_published_final_commit()"));
        assert!(source.contains("into_held_runtime_ledger_pair()"));
        assert!(source.contains("new_authenticated_final_commit(pair)"));
        assert!(source.contains("AuthenticatedFinalCommitBootstrap {"));
        assert!(source.contains("take_authenticated_runtime_source"));
        assert!(source.contains("AuthenticatedRuntimeSourceCapability"));
        assert!(source.contains("take_authenticated_root_executables"));
        assert!(source.contains("AuthenticatedProtectedRootExecutablesCapability"));
        assert!(source.contains("protected_root_executables_capability"));
        assert!(source.contains("clone_current_protected_scenario_executables"));
        assert!(source.contains("GenerationBoundProtectedExecutableHandles"));
        assert!(source.contains("final_commit_receipt_sha256"));
        assert!(source.contains("into_verified_ordered_files"));
        let fabricated_receipt_assignment = [
            "final_commit_receipt_sha256: self.validated.",
            "receipt_sha256()",
        ]
        .concat();
        assert!(!source.contains(&fabricated_receipt_assignment));

        let sign_start = source.find("pub(crate) fn sign_current_digest(").unwrap();
        let verify_start = source
            .find("pub(crate) fn verify_current_digest_signature(")
            .unwrap();
        let capability_start = source
            .find("pub(crate) fn runtime_source_capability(")
            .unwrap();
        assert!(source[sign_start..verify_start].contains("self.verify_still_stable()?"));
        assert!(source[verify_start..capability_start].contains("self.verify_still_stable()?"));

        let native_source = include_str!("bootstrap_windows.rs");
        assert!(!native_source.contains("inspect_existing_machine_key"));
        assert!(native_source.contains(".verify_current(&binding.policy)"));
        assert!(native_source.contains("NativeAuthenticatedProtectedRootExecutablesCapability"));
        assert!(native_source.contains("GetHandleInformation"));
        assert!(native_source.contains("HANDLE_FLAG_INHERIT"));
        assert!(native_source.contains("with_verified_files"));
        assert!(native_source.contains("ReOpenFile("));
        assert!(native_source.contains("reopen_root_executable_pair"));
        assert!(native_source.contains("PROTECTED_EXECUTABLE_READ_ACCESS"));
        assert!(!native_source.contains("self.lifecycle_driver.file.try_clone()"));
        assert!(!native_source.contains("self.bridge_launcher.file.try_clone()"));
        assert!(native_source.contains("verify_cloned_root_executable"));
        assert!(native_source.contains("SEALED_BINARY_FILE_SDDL"));
    }

    #[test]
    fn every_artifact_readback_dimension_and_duplicate_kind_fail_closed() {
        for index in 0..REQUIRED_ARTIFACTS.len() {
            for field in 0..7 {
                let (mut snapshot, expected) = valid_snapshot();
                let readback = &mut snapshot.protected_artifacts[index];
                match field {
                    0 => readback.path_exact = false,
                    1 => readback.local_volume = false,
                    2 => readback.reparse_free_held_chain = false,
                    3 => readback.single_link = false,
                    4 => readback.stable_identity = false,
                    5 => readback.exact_owner_and_acl = false,
                    6 => readback.full_held_handle_readback = false,
                    _ => unreachable!(),
                }
                let mut verifier = FakeVerifier {
                    expected,
                    fail_at: None,
                    calls: 0,
                };
                assert_eq!(
                    validate_bootstrap_snapshot(&layout(), &snapshot, &mut verifier)
                        .unwrap_err()
                        .code(),
                    "authority_protected_artifact_readback_incomplete"
                );
                assert_eq!(verifier.calls, 0);
            }
        }
        let (mut snapshot, expected) = valid_snapshot();
        snapshot.protected_artifacts[1].kind = snapshot.protected_artifacts[0].kind;
        let mut verifier = FakeVerifier {
            expected,
            fail_at: None,
            calls: 0,
        };
        assert_eq!(
            validate_bootstrap_snapshot(&layout(), &snapshot, &mut verifier)
                .unwrap_err()
                .code(),
            "authority_protected_artifact_set_invalid"
        );
    }

    #[test]
    fn head_manifest_key_ledger_and_signature_faults_fail_before_ready() {
        let (mut snapshot, expected) = valid_snapshot();
        snapshot
            .activation_directory_names
            .push("fork.json".to_string());
        let mut verifier = FakeVerifier {
            expected,
            fail_at: None,
            calls: 0,
        };
        assert_eq!(
            validate_bootstrap_snapshot(&layout(), &snapshot, &mut verifier)
                .unwrap_err()
                .code(),
            "authority_activation_directory_not_unique"
        );

        let (mut snapshot, expected) = valid_snapshot();
        snapshot.ledger_identity[0] ^= 1;
        let mut verifier = FakeVerifier {
            expected,
            fail_at: None,
            calls: 0,
        };
        assert_eq!(
            validate_bootstrap_snapshot(&layout(), &snapshot, &mut verifier)
                .unwrap_err()
                .code(),
            "authority_ledger_identity_mismatch"
        );

        let (snapshot, expected) = valid_snapshot();
        let mut verifier = FakeVerifier {
            expected,
            fail_at: Some(1),
            calls: 0,
        };
        assert_eq!(
            validate_bootstrap_snapshot(&layout(), &snapshot, &mut verifier)
                .unwrap_err()
                .code(),
            "authority_manifest_signature_invalid"
        );
        assert_eq!(verifier.calls, 2);
    }
}
