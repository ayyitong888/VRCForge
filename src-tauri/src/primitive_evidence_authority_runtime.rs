use crate::primitive_basis_protected_evidence_bundle::{
    DurableBinaryLedgerTerminal, ProtectedEvidenceBundleProducer, ProtectedEvidenceBundleSigner,
    ServiceOwnedVerifiedRuntimeResult, VerifiedAuthorityResultProjection,
};
#[cfg(windows)]
use crate::primitive_evidence_authority_ledger::TicketBurnReason;
use crate::primitive_evidence_authority_ledger::{
    compute_recovery_bundle_digest, DurableProjectionCommitReceipt, DurableVerifiedResult,
    LedgerIdentity, RecoveredBurnProof,
};
#[cfg(windows)]
use crate::primitive_evidence_authority_supervisor::native_windows::{
    NativeAdmissionBinding, NativeBurnedRunProof, NativeCompletedRunProof,
    ValidatedNativeTerminalRun,
};
use crate::primitive_evidence_authority_supervisor::{
    derive_run_binding_digest, prepared_protected_evidence_policy_readback, ArmedRecoveryReceipt,
    BurnReason, BurnedRunProof, CompletedRunProof, PreparedProtectedEvidencePolicyReadback,
    PreparedRecoveryReceipt, PreparedRun, VerifiedReadinessProof,
};
use sha2::{Digest, Sha256};
use std::sync::{Mutex, MutexGuard};

const BLOCKER_RUNTIME_STARTUP: &str = "authority_runtime_startup_failed";
const BLOCKER_RUNTIME_INTEGRITY: &str = "authority_runtime_integrity_failed";
const BLOCKER_SUPERVISOR_NOT_CONNECTED: &str = "authority_supervisor_not_connected";
const BLOCKER_SUPERVISOR_UNAVAILABLE: &str = "authority_supervisor_unavailable";
const BLOCKER_PROJECTION_NOT_CONNECTED: &str = "authority_projection_not_connected";
const MAX_RAW_FINALIZATION_BYTES: usize = 64 * 1024;
const RUNTIME_IDENTITY_DOMAIN: &[u8] = b"vrcforge-authority-runtime-identity-v1\0";
const RUNTIME_TICKET_DOMAIN: &[u8] = b"vrcforge-authority-runtime-ticket-v1\0";
const SUPERVISOR_NOT_CONNECTED_CODE: &str = "fixed_model_part_supervisor_not_connected";
const PROJECTION_NOT_CONNECTED_CODE: &str = "protected_projection_producer_not_connected";
const CANCEL_ACKNOWLEDGEMENT_UNCERTAIN_CODE: &str = "authority_cancel_acknowledgement_uncertain";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityRuntimeError(&'static str);

impl AuthorityRuntimeError {
    fn new(code: &'static str) -> Self {
        Self(code)
    }

    #[cfg(test)]
    pub(crate) fn for_contract_test(code: &'static str) -> Self {
        Self(code)
    }

    pub fn code(&self) -> &'static str {
        self.0
    }

    pub fn requires_process_exit(&self) -> bool {
        self.0 == "authority_runtime_integrity_failed" || self.0 == "authority_runtime_lock_failed"
    }
}

impl std::fmt::Display for AuthorityRuntimeError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for AuthorityRuntimeError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeDependencyError(String);

impl RuntimeDependencyError {
    pub fn new(code: impl Into<String>) -> Self {
        Self(code.into())
    }

    pub fn code(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for RuntimeDependencyError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for RuntimeDependencyError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityRuntimeIdentity {
    authority_generation_digest: [u8; 32],
    signer_key_id: [u8; 32],
    protected_manifest_digest: [u8; 32],
    installed_layout_digest: [u8; 32],
    service_binary_digest: [u8; 32],
}

impl AuthorityRuntimeIdentity {
    pub fn new(
        authority_generation_digest: [u8; 32],
        signer_key_id: [u8; 32],
        protected_manifest_digest: [u8; 32],
        installed_layout_digest: [u8; 32],
        service_binary_digest: [u8; 32],
    ) -> Result<Self, AuthorityRuntimeError> {
        if [
            &authority_generation_digest,
            &signer_key_id,
            &protected_manifest_digest,
            &installed_layout_digest,
            &service_binary_digest,
        ]
        .into_iter()
        .any(|digest| digest.iter().all(|byte| *byte == 0))
        {
            return Err(AuthorityRuntimeError::new(
                "authority_runtime_identity_invalid",
            ));
        }
        Ok(Self {
            authority_generation_digest,
            signer_key_id,
            protected_manifest_digest,
            installed_layout_digest,
            service_binary_digest,
        })
    }

    pub fn authority_generation_digest(&self) -> &[u8; 32] {
        &self.authority_generation_digest
    }

    pub fn signer_key_id(&self) -> &[u8; 32] {
        &self.signer_key_id
    }

    pub fn protected_manifest_digest(&self) -> &[u8; 32] {
        &self.protected_manifest_digest
    }

    pub fn installed_layout_digest(&self) -> &[u8; 32] {
        &self.installed_layout_digest
    }

    pub fn service_binary_digest(&self) -> &[u8; 32] {
        &self.service_binary_digest
    }

    pub fn binding_digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(RUNTIME_IDENTITY_DOMAIN);
        digest.update(self.authority_generation_digest);
        digest.update(self.signer_key_id);
        digest.update(self.protected_manifest_digest);
        digest.update(self.installed_layout_digest);
        digest.update(self.service_binary_digest);
        digest.finalize().into()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct RuntimeTicketRef {
    digest: [u8; 32],
    persisted: String,
}

impl RuntimeTicketRef {
    fn for_request(identity: &AuthorityRuntimeIdentity, request_id: &str) -> Self {
        let mut digest = Sha256::new();
        digest.update(RUNTIME_TICKET_DOMAIN);
        digest.update(identity.binding_digest());
        digest.update((request_id.len() as u64).to_be_bytes());
        digest.update(request_id.as_bytes());
        let digest: [u8; 32] = digest.finalize().into();
        Self {
            persisted: hex_encode(&digest),
            digest,
        }
    }

    pub fn from_persisted(value: &str) -> Result<Self, RuntimeDependencyError> {
        let digest = decode_digest(value)
            .ok_or_else(|| RuntimeDependencyError::new("runtime_ticket_digest_invalid"))?;
        Ok(Self {
            digest,
            persisted: value.to_owned(),
        })
    }

    pub fn as_str(&self) -> &str {
        &self.persisted
    }

    pub fn digest(&self) -> [u8; 32] {
        self.digest
    }

    /// Verifies that this opaque ticket was derived for the exact runtime
    /// identity and request. Callers cannot manufacture or recover the ticket
    /// preimage from a persisted digest.
    pub(crate) fn matches_request(
        &self,
        identity: &AuthorityRuntimeIdentity,
        request_id: &str,
    ) -> bool {
        *self == Self::for_request(identity, request_id)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeActiveTicket {
    ticket: RuntimeTicketRef,
    run_binding_digest: [u8; 32],
    prepared_receipt_bytes: Vec<u8>,
    canonical_policy_snapshot: Vec<u8>,
    recovery_bundle_digest: String,
    armed_receipt_bytes: Option<Vec<u8>>,
}

impl RuntimeActiveTicket {
    pub fn new(
        ticket: RuntimeTicketRef,
        run_binding_digest: [u8; 32],
        prepared_receipt_bytes: Vec<u8>,
        canonical_policy_snapshot: Vec<u8>,
        recovery_bundle_digest: String,
        armed_receipt_bytes: Option<Vec<u8>>,
    ) -> Result<Self, RuntimeDependencyError> {
        let expected_bundle = compute_recovery_bundle_digest(
            ticket.as_str(),
            &hex_encode(&run_binding_digest),
            &prepared_receipt_bytes,
            &canonical_policy_snapshot,
        )
        .map_err(|_| RuntimeDependencyError::new("runtime_recovery_bundle_invalid"))?;
        if run_binding_digest.iter().all(|byte| *byte == 0)
            || prepared_receipt_bytes.is_empty()
            || canonical_policy_snapshot.is_empty()
            || recovery_bundle_digest != expected_bundle
        {
            return Err(RuntimeDependencyError::new("runtime_run_binding_invalid"));
        }
        Ok(Self {
            ticket,
            run_binding_digest,
            prepared_receipt_bytes,
            canonical_policy_snapshot,
            recovery_bundle_digest,
            armed_receipt_bytes,
        })
    }

    pub fn ticket(&self) -> &RuntimeTicketRef {
        &self.ticket
    }

    pub fn run_binding_digest(&self) -> &[u8; 32] {
        &self.run_binding_digest
    }

    pub fn prepared_receipt_bytes(&self) -> &[u8] {
        &self.prepared_receipt_bytes
    }

    pub fn canonical_policy_snapshot(&self) -> &[u8] {
        &self.canonical_policy_snapshot
    }

    pub fn recovery_bundle_digest(&self) -> &str {
        &self.recovery_bundle_digest
    }

    pub fn armed_receipt_bytes(&self) -> Option<&[u8]> {
        self.armed_receipt_bytes.as_deref()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeRunContext {
    authority_identity_digest: [u8; 32],
    ticket: RuntimeTicketRef,
    run_binding_digest: [u8; 32],
    service_instance_digest: [u8; 32],
    runner_policy_digest: [u8; 32],
    prepared_receipt: PreparedRecoveryReceipt,
    canonical_policy_snapshot: Vec<u8>,
    armed_receipt: Option<ArmedRecoveryReceipt>,
}

impl RuntimeRunContext {
    pub fn authority_identity_digest(&self) -> &[u8; 32] {
        &self.authority_identity_digest
    }

    pub fn ticket(&self) -> &RuntimeTicketRef {
        &self.ticket
    }

    pub fn run_binding_digest(&self) -> &[u8; 32] {
        &self.run_binding_digest
    }

    pub fn service_instance_digest(&self) -> &[u8; 32] {
        &self.service_instance_digest
    }

    pub fn runner_policy_digest(&self) -> &[u8; 32] {
        &self.runner_policy_digest
    }

    pub fn prepared_receipt(&self) -> &PreparedRecoveryReceipt {
        &self.prepared_receipt
    }

    pub fn canonical_policy_snapshot(&self) -> &[u8] {
        &self.canonical_policy_snapshot
    }

    pub fn armed_receipt(&self) -> Option<&ArmedRecoveryReceipt> {
        self.armed_receipt.as_ref()
    }

    fn with_armed_receipt(mut self, armed_receipt: ArmedRecoveryReceipt) -> Self {
        self.armed_receipt = Some(armed_receipt);
        self
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeRecoveryContext {
    authority_identity_digest: [u8; 32],
    ticket: RuntimeTicketRef,
    run_binding_digest: [u8; 32],
    prepared_receipt: PreparedRecoveryReceipt,
    canonical_policy_snapshot: Vec<u8>,
    armed_receipt: Option<ArmedRecoveryReceipt>,
}

impl RuntimeRecoveryContext {
    pub fn authority_identity_digest(&self) -> &[u8; 32] {
        &self.authority_identity_digest
    }

    pub fn ticket(&self) -> &RuntimeTicketRef {
        &self.ticket
    }

    pub fn run_binding_digest(&self) -> &[u8; 32] {
        &self.run_binding_digest
    }

    pub fn prepared_receipt(&self) -> &PreparedRecoveryReceipt {
        &self.prepared_receipt
    }

    pub fn canonical_policy_snapshot(&self) -> &[u8] {
        &self.canonical_policy_snapshot
    }

    pub fn armed_receipt(&self) -> Option<&ArmedRecoveryReceipt> {
        self.armed_receipt.as_ref()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuntimeTicketState {
    Issued,
    Consumed,
    ResultPendingProjection,
    Result,
    Burned,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimePendingVerifiedResult {
    ticket: RuntimeTicketRef,
    run_binding_digest: [u8; 32],
    result: ServiceOwnedVerifiedRuntimeResult,
    prepared_receipt: Vec<u8>,
    canonical_policy_snapshot: Vec<u8>,
    recovery_bundle_digest: [u8; 32],
    armed_receipt: Vec<u8>,
    result_committed: bool,
    projection: Option<VerifiedAuthorityResultProjection>,
}

impl RuntimePendingVerifiedResult {
    pub(crate) fn new(
        ticket: RuntimeTicketRef,
        run_binding_digest: [u8; 32],
        result: ServiceOwnedVerifiedRuntimeResult,
        prepared_receipt: Vec<u8>,
        canonical_policy_snapshot: Vec<u8>,
        recovery_bundle_digest: [u8; 32],
        armed_receipt: Vec<u8>,
        result_committed: bool,
        projection: Option<VerifiedAuthorityResultProjection>,
    ) -> Result<Self, RuntimeDependencyError> {
        if ticket.digest() != *result.ticket_digest()
            || run_binding_digest != *result.run_binding_digest()
            || Sha256::digest(&prepared_receipt)[..] != *result.prepared_receipt_digest()
            || Sha256::digest(&canonical_policy_snapshot)[..] != *result.policy_snapshot_digest()
            || recovery_bundle_digest != *result.recovery_bundle_digest()
            || Sha256::digest(&armed_receipt)[..] != *result.armed_receipt_digest()
            || projection.is_some() && !result_committed
        {
            return Err(RuntimeDependencyError::new(
                "pending_verified_result_binding_mismatch",
            ));
        }
        Ok(Self {
            ticket,
            run_binding_digest,
            result,
            prepared_receipt,
            canonical_policy_snapshot,
            recovery_bundle_digest,
            armed_receipt,
            result_committed,
            projection,
        })
    }

    pub(crate) fn from_durable(
        ticket: RuntimeTicketRef,
        record: &DurableVerifiedResult,
        prepared_receipt: &[u8],
        canonical_policy_snapshot: &[u8],
        recovery_bundle_digest: &[u8; 32],
        armed_receipt: &[u8],
        result_committed: bool,
        projection: Option<VerifiedAuthorityResultProjection>,
    ) -> Result<Self, RuntimeDependencyError> {
        let result = ServiceOwnedVerifiedRuntimeResult::from_verified_terminal(
            record.finalization_bytes().to_vec(),
            record.origin_envelope_bytes().to_vec(),
            *record.ticket_digest(),
            *record.run_binding_digest(),
            *record.cleanup_digest(),
            *record.prepared_receipt_digest(),
            *record.armed_receipt_digest(),
            *record.policy_snapshot_digest(),
            *record.recovery_bundle_digest(),
        )
        .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        if result.finalization_digest() != record.finalization_digest()
            || result.origin_envelope_digest() != record.origin_envelope_digest()
        {
            return Err(RuntimeDependencyError::new(
                "pending_verified_result_digest_mismatch",
            ));
        }
        Self::new(
            ticket,
            *record.run_binding_digest(),
            result,
            prepared_receipt.to_vec(),
            canonical_policy_snapshot.to_vec(),
            *recovery_bundle_digest,
            armed_receipt.to_vec(),
            result_committed,
            projection,
        )
    }

    pub(crate) fn durable_record(&self) -> Result<DurableVerifiedResult, RuntimeDependencyError> {
        DurableVerifiedResult::new(
            self.result.finalization_bytes().to_vec(),
            self.result.origin_envelope_bytes().to_vec(),
            self.ticket.digest(),
            self.run_binding_digest,
            *self.result.finalization_digest(),
            *self.result.origin_envelope_digest(),
            *self.result.cleanup_digest(),
            *self.result.prepared_receipt_digest(),
            *self.result.armed_receipt_digest(),
            *self.result.policy_snapshot_digest(),
            *self.result.recovery_bundle_digest(),
        )
        .map_err(|error| RuntimeDependencyError::new(error.code()))
    }

    pub fn ticket(&self) -> &RuntimeTicketRef {
        &self.ticket
    }

    pub fn run_binding_digest(&self) -> &[u8; 32] {
        &self.run_binding_digest
    }

    pub(crate) fn result(&self) -> &ServiceOwnedVerifiedRuntimeResult {
        &self.result
    }

    pub(crate) fn prepared_receipt(&self) -> &[u8] {
        &self.prepared_receipt
    }

    pub(crate) fn canonical_policy_snapshot(&self) -> &[u8] {
        &self.canonical_policy_snapshot
    }

    pub(crate) fn recovery_bundle_digest(&self) -> &[u8; 32] {
        &self.recovery_bundle_digest
    }

    pub(crate) fn armed_receipt(&self) -> &[u8] {
        &self.armed_receipt
    }

    pub fn result_committed(&self) -> bool {
        self.result_committed
    }

    pub fn projection(&self) -> Option<&VerifiedAuthorityResultProjection> {
        self.projection.as_ref()
    }
}

pub trait InstalledBoundaryVerifier: Send {
    fn verify_installed_boundary(
        &mut self,
    ) -> Result<AuthorityRuntimeIdentity, RuntimeDependencyError>;
}

pub trait RuntimeTicketLedger: Send {
    fn open_existing(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError>;

    fn active_tickets(&mut self) -> Result<Vec<RuntimeActiveTicket>, RuntimeDependencyError>;

    fn verify_identity(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError>;

    fn state(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<RuntimeTicketState>, RuntimeDependencyError>;

    fn issue(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        prepared_receipt_bytes: &[u8],
        canonical_policy_snapshot: &[u8],
    ) -> Result<(), RuntimeDependencyError>;

    fn consume(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
    ) -> Result<(), RuntimeDependencyError>;

    fn record_armed_receipt(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        armed_receipt_bytes: &[u8],
    ) -> Result<(), RuntimeDependencyError>;

    fn record_result_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        result_bytes: &[u8],
    ) -> Result<(), RuntimeDependencyError>;

    fn record_verified_result_pending_exact(
        &mut self,
        _ticket: &RuntimeTicketRef,
        _run_binding_digest: &[u8; 32],
        _result: &ServiceOwnedVerifiedRuntimeResult,
    ) -> Result<(), RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "verified_result_pending_ledger_not_connected",
        ))
    }

    fn pending_verified_results(
        &mut self,
    ) -> Result<Vec<RuntimePendingVerifiedResult>, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "verified_result_pending_ledger_not_connected",
        ))
    }

    fn result_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<Vec<u8>>, RuntimeDependencyError>;

    fn reopen_result_commit_terminal(
        &mut self,
        _ticket: &RuntimeTicketRef,
        _run_binding_digest: &[u8; 32],
        _result: &ServiceOwnedVerifiedRuntimeResult,
    ) -> Result<DurableBinaryLedgerTerminal, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "protected_binary_terminal_readback_not_connected",
        ))
    }

    fn record_projection_pending_exact(
        &mut self,
        _ticket: &RuntimeTicketRef,
        _run_binding_digest: &[u8; 32],
        _projection: &VerifiedAuthorityResultProjection,
    ) -> Result<(), RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "protected_projection_ledger_not_connected",
        ))
    }

    fn commit_projection_exact(
        &mut self,
        _ticket: &RuntimeTicketRef,
        _run_binding_digest: &[u8; 32],
        _projection_digest: &[u8; 32],
    ) -> Result<(), RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "protected_projection_ledger_not_connected",
        ))
    }

    fn projection_exact(
        &mut self,
        _ticket: &RuntimeTicketRef,
    ) -> Result<Option<VerifiedAuthorityResultProjection>, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "protected_projection_ledger_not_connected",
        ))
    }

    fn projection_commit_receipt_exact(
        &mut self,
        _ticket: &RuntimeTicketRef,
        _run_binding_digest: &[u8; 32],
        _projection: &VerifiedAuthorityResultProjection,
    ) -> Result<Option<DurableProjectionCommitReceipt>, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "protected_projection_receipt_not_connected",
        ))
    }

    fn burn(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        reason: RuntimeTerminalKind,
    ) -> Result<(), RuntimeDependencyError>;

    fn burn_recovered(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
    ) -> Result<(), RuntimeDependencyError>;

    fn burn_recovered_with_reason(
        &mut self,
        _ticket: &RuntimeTicketRef,
        _run_binding_digest: &[u8; 32],
        _reason: RuntimeTerminalKind,
        _proof: &RecoveredBurnProof,
    ) -> Result<(), RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "recovered_terminal_reason_ledger_not_connected",
        ))
    }

    fn terminal_reason(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<RuntimeTerminalKind>, RuntimeDependencyError>;
}

pub trait ProtectedEvidenceProjectionProducer: Send {
    fn verify_runtime_identity(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError>;

    fn produce_projection(
        &mut self,
        policy: &PreparedProtectedEvidencePolicyReadback,
        result: &ServiceOwnedVerifiedRuntimeResult,
        terminal: &DurableBinaryLedgerTerminal,
    ) -> Result<VerifiedAuthorityResultProjection, RuntimeDependencyError>;

    fn verify_projection(
        &mut self,
        policy: &PreparedProtectedEvidencePolicyReadback,
        result: &ServiceOwnedVerifiedRuntimeResult,
        terminal: &DurableBinaryLedgerTerminal,
        projection: &VerifiedAuthorityResultProjection,
    ) -> Result<(), RuntimeDependencyError>;
}

impl<S: ProtectedEvidenceBundleSigner> ProtectedEvidenceProjectionProducer
    for ProtectedEvidenceBundleProducer<S>
{
    fn verify_runtime_identity(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError> {
        if self.matches_runtime_identity(
            identity.authority_generation_digest(),
            identity.signer_key_id(),
            identity.protected_manifest_digest(),
            identity.installed_layout_digest(),
            identity.service_binary_digest(),
        ) {
            Ok(())
        } else {
            Err(RuntimeDependencyError::new(
                "protected_projection_identity_mismatch",
            ))
        }
    }

    fn produce_projection(
        &mut self,
        policy: &PreparedProtectedEvidencePolicyReadback,
        result: &ServiceOwnedVerifiedRuntimeResult,
        terminal: &DurableBinaryLedgerTerminal,
    ) -> Result<VerifiedAuthorityResultProjection, RuntimeDependencyError> {
        self.produce(policy.source(), result, terminal)
            .map_err(|error| RuntimeDependencyError::new(error.code()))
    }

    fn verify_projection(
        &mut self,
        policy: &PreparedProtectedEvidencePolicyReadback,
        result: &ServiceOwnedVerifiedRuntimeResult,
        terminal: &DurableBinaryLedgerTerminal,
        projection: &VerifiedAuthorityResultProjection,
    ) -> Result<(), RuntimeDependencyError> {
        self.verify_existing_projection(policy.source(), result, terminal, projection)
            .map_err(|error| RuntimeDependencyError::new(error.code()))
    }
}

#[derive(Debug, Default)]
pub struct DisconnectedProtectedEvidenceProjectionProducer;

impl ProtectedEvidenceProjectionProducer for DisconnectedProtectedEvidenceProjectionProducer {
    fn verify_runtime_identity(
        &mut self,
        _identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(PROJECTION_NOT_CONNECTED_CODE))
    }

    fn produce_projection(
        &mut self,
        _policy: &PreparedProtectedEvidencePolicyReadback,
        _result: &ServiceOwnedVerifiedRuntimeResult,
        _terminal: &DurableBinaryLedgerTerminal,
    ) -> Result<VerifiedAuthorityResultProjection, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(PROJECTION_NOT_CONNECTED_CODE))
    }

    fn verify_projection(
        &mut self,
        _policy: &PreparedProtectedEvidencePolicyReadback,
        _result: &ServiceOwnedVerifiedRuntimeResult,
        _terminal: &DurableBinaryLedgerTerminal,
        _projection: &VerifiedAuthorityResultProjection,
    ) -> Result<(), RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(PROJECTION_NOT_CONNECTED_CODE))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuntimeTerminalKind {
    Cancelled,
    TimedOut,
    Failed,
    RestartRecovery,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SupervisorCancelAcknowledgement {
    Recorded(RuntimeTerminalKind),
    AlreadyRecorded(RuntimeTerminalKind),
    AlreadyTerminal,
    Uncertain,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SupervisorPoll {
    Starting,
    Armed(ArmedRecoveryReceipt),
    Running,
    #[cfg(windows)]
    Terminal(ValidatedNativeTerminalRun),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SupervisorRecovery {
    #[cfg(windows)]
    Completed(NativeCompletedRunProof),
    #[cfg(windows)]
    Burned(NativeBurnedRunProof),
    #[cfg(not(windows))]
    Burned(BurnedRunProof),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SupervisorStart {
    Starting,
    Armed(ArmedRecoveryReceipt),
    Burned(BurnedRunProof),
}

pub trait FixedModelPartSupervisor: Send {
    fn contain_all_orphans(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError>;

    fn readiness(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<VerifiedReadinessProof, RuntimeDependencyError>;

    fn self_test(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<VerifiedReadinessProof, RuntimeDependencyError>;

    fn prepare(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
        ticket: &RuntimeTicketRef,
        service_instance_digest: &[u8; 32],
    ) -> Result<PreparedRun, RuntimeDependencyError>;

    fn prepared_policy_snapshot(
        &mut self,
        prepared: &PreparedRun,
    ) -> Result<Vec<u8>, RuntimeDependencyError> {
        Ok(prepared.policy_snapshot().to_vec())
    }

    fn start(
        &mut self,
        prepared: PreparedRun,
        context: &RuntimeRunContext,
    ) -> Result<SupervisorStart, RuntimeDependencyError>;

    fn poll(
        &mut self,
        context: &RuntimeRunContext,
    ) -> Result<SupervisorPoll, RuntimeDependencyError>;

    fn cancel(
        &mut self,
        context: &RuntimeRunContext,
    ) -> Result<SupervisorCancelAcknowledgement, RuntimeDependencyError>;

    fn abort_and_wait_cleanup(
        &mut self,
        context: &RuntimeRunContext,
    ) -> Result<BurnedRunProof, RuntimeDependencyError>;

    fn recover_and_wait_cleanup(
        &mut self,
        context: &RuntimeRecoveryContext,
    ) -> Result<SupervisorRecovery, RuntimeDependencyError>;

    fn shutdown_and_wait(&mut self) -> Result<(), RuntimeDependencyError> {
        Ok(())
    }
}

#[derive(Debug, Default)]
pub struct DisconnectedModelPartSupervisor;

impl FixedModelPartSupervisor for DisconnectedModelPartSupervisor {
    fn contain_all_orphans(
        &mut self,
        _identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(SUPERVISOR_NOT_CONNECTED_CODE))
    }

    fn readiness(
        &mut self,
        _identity: &AuthorityRuntimeIdentity,
    ) -> Result<VerifiedReadinessProof, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(SUPERVISOR_NOT_CONNECTED_CODE))
    }

    fn self_test(
        &mut self,
        _identity: &AuthorityRuntimeIdentity,
    ) -> Result<VerifiedReadinessProof, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(SUPERVISOR_NOT_CONNECTED_CODE))
    }

    fn prepare(
        &mut self,
        _identity: &AuthorityRuntimeIdentity,
        _ticket: &RuntimeTicketRef,
        _service_instance_digest: &[u8; 32],
    ) -> Result<PreparedRun, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(SUPERVISOR_NOT_CONNECTED_CODE))
    }

    fn start(
        &mut self,
        _prepared: PreparedRun,
        _context: &RuntimeRunContext,
    ) -> Result<SupervisorStart, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "fixed_model_part_supervisor_not_connected",
        ))
    }

    fn poll(
        &mut self,
        _context: &RuntimeRunContext,
    ) -> Result<SupervisorPoll, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "fixed_model_part_supervisor_not_connected",
        ))
    }

    fn cancel(
        &mut self,
        _context: &RuntimeRunContext,
    ) -> Result<SupervisorCancelAcknowledgement, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "fixed_model_part_supervisor_not_connected",
        ))
    }

    fn abort_and_wait_cleanup(
        &mut self,
        _context: &RuntimeRunContext,
    ) -> Result<BurnedRunProof, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "fixed_model_part_supervisor_not_connected",
        ))
    }

    fn recover_and_wait_cleanup(
        &mut self,
        _context: &RuntimeRecoveryContext,
    ) -> Result<SupervisorRecovery, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(SUPERVISOR_NOT_CONNECTED_CODE))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AuthorityRuntimeCommand {
    Status,
    SelfTest,
    RunModelPartComposition { request_id: String },
    Cancel { request_id: String },
    GetResult { request_id: String },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityRuntimeStatus {
    pub trusted_boundary_ready: bool,
    pub global_failure: bool,
    pub blockers: Vec<&'static str>,
    pub active_request_id: Option<String>,
    pub startup_burned_tickets: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityRuntimeSelfTest {
    pub passed: bool,
    pub trusted_boundary_ready: bool,
    pub blockers: Vec<&'static str>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AuthorityRuntimeReply {
    Status(AuthorityRuntimeStatus),
    SelfTest(AuthorityRuntimeSelfTest),
    RunStarted {
        request_id: String,
    },
    CancelRequested {
        request_id: String,
        already_requested: bool,
    },
    AlreadyTerminated {
        request_id: String,
        reason: RuntimeTerminalKind,
    },
    ResultPending {
        request_id: String,
    },
    ResultExact {
        request_id: String,
        projection: VerifiedAuthorityResultProjection,
        receipt: DurableProjectionCommitReceipt,
    },
    ResultTerminated {
        request_id: String,
        reason: RuntimeTerminalKind,
    },
}

pub struct AuthorityRuntime {
    inner: Mutex<RuntimeInner>,
}

struct ActiveRun {
    request_id: String,
    context: RuntimeRunContext,
    cancel_requested: bool,
}

struct RuntimeInner {
    boundary: Box<dyn InstalledBoundaryVerifier>,
    ledger: Box<dyn RuntimeTicketLedger>,
    supervisor: Box<dyn FixedModelPartSupervisor>,
    projection_producer: Box<dyn ProtectedEvidenceProjectionProducer>,
    identity: Option<AuthorityRuntimeIdentity>,
    active: Option<ActiveRun>,
    startup_burned_tickets: usize,
    global_blocker: Option<&'static str>,
}

impl AuthorityRuntime {
    pub fn start<B, L, S>(boundary: B, ledger: L, supervisor: S) -> Self
    where
        B: InstalledBoundaryVerifier + 'static,
        L: RuntimeTicketLedger + 'static,
        S: FixedModelPartSupervisor + 'static,
    {
        Self::start_with_projection_producer(
            boundary,
            ledger,
            supervisor,
            DisconnectedProtectedEvidenceProjectionProducer,
        )
    }

    pub(crate) fn start_with_projection_producer<B, L, S, P>(
        boundary: B,
        ledger: L,
        supervisor: S,
        projection_producer: P,
    ) -> Self
    where
        B: InstalledBoundaryVerifier + 'static,
        L: RuntimeTicketLedger + 'static,
        S: FixedModelPartSupervisor + 'static,
        P: ProtectedEvidenceProjectionProducer + 'static,
    {
        let mut inner = RuntimeInner {
            boundary: Box::new(boundary),
            ledger: Box::new(ledger),
            supervisor: Box::new(supervisor),
            projection_producer: Box::new(projection_producer),
            identity: None,
            active: None,
            startup_burned_tickets: 0,
            global_blocker: None,
        };
        inner.bootstrap();
        Self {
            inner: Mutex::new(inner),
        }
    }

    pub fn handle(
        &self,
        command: AuthorityRuntimeCommand,
    ) -> Result<AuthorityRuntimeReply, AuthorityRuntimeError> {
        let mut inner = self.lock()?;
        match command {
            AuthorityRuntimeCommand::Status => Ok(AuthorityRuntimeReply::Status(inner.status())),
            AuthorityRuntimeCommand::SelfTest => {
                Ok(AuthorityRuntimeReply::SelfTest(inner.self_test()))
            }
            AuthorityRuntimeCommand::RunModelPartComposition { request_id } => {
                inner.run_model_part_composition(request_id)
            }
            AuthorityRuntimeCommand::Cancel { request_id } => inner.cancel(request_id),
            AuthorityRuntimeCommand::GetResult { request_id } => inner.get_result(request_id),
        }
    }

    fn lock(&self) -> Result<MutexGuard<'_, RuntimeInner>, AuthorityRuntimeError> {
        self.inner
            .lock()
            .map_err(|_| AuthorityRuntimeError::new("authority_runtime_lock_failed"))
    }

    pub(crate) fn shutdown_and_wait(&self) -> Result<(), AuthorityRuntimeError> {
        let mut inner = self.lock()?;
        if inner.supervisor.shutdown_and_wait().is_err() {
            inner.latch_global(BLOCKER_RUNTIME_INTEGRITY);
            return Err(AuthorityRuntimeError::new(
                "authority_runtime_shutdown_failed",
            ));
        }
        Ok(())
    }
}

impl RuntimeInner {
    fn bootstrap(&mut self) {
        let identity = match self.boundary.verify_installed_boundary() {
            Ok(identity) => identity,
            Err(_) => {
                self.latch_global(BLOCKER_RUNTIME_STARTUP);
                return;
            }
        };
        if self.ledger.open_existing(&identity).is_err() {
            self.latch_global(BLOCKER_RUNTIME_STARTUP);
            return;
        }
        if self.supervisor.contain_all_orphans(&identity).is_err() {
            self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
            return;
        }
        // Recovery may need to finish an already sealed successful run and
        // project its exact result. Keep the verified identity available to
        // that commit path, while readiness remains blocked until the entire
        // startup recovery pass succeeds.
        self.identity = Some(identity.clone());
        if self.recover_pending_verified_results(&identity).is_err() {
            self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
            return;
        }
        let active_tickets = match self.ledger.active_tickets() {
            Ok(active_tickets) => active_tickets,
            Err(_) => {
                self.latch_global(BLOCKER_RUNTIME_STARTUP);
                return;
            }
        };
        let authority_identity_digest = identity.binding_digest();
        let mut burned = 0usize;
        let mut recovery_failed = false;
        for active in active_tickets {
            let prepared_receipt =
                match PreparedRecoveryReceipt::decode(active.prepared_receipt_bytes()) {
                    Ok(receipt)
                        if receipt.verifies_for(
                            &authority_identity_digest,
                            &active.ticket().digest(),
                            receipt.service_instance_digest(),
                        ) && derive_run_binding_digest(
                            &authority_identity_digest,
                            &active.ticket().digest(),
                            receipt.service_instance_digest(),
                            receipt.runner_policy_digest(),
                        ) == *active.run_binding_digest()
                            && receipt
                                .verifies_policy_snapshot(active.canonical_policy_snapshot()) =>
                    {
                        receipt
                    }
                    Ok(_) | Err(_) => {
                        recovery_failed = true;
                        continue;
                    }
                };
            let armed_receipt = match active.armed_receipt_bytes() {
                Some(bytes) => match ArmedRecoveryReceipt::decode(bytes) {
                    Ok(receipt)
                        if receipt.verifies_for(&prepared_receipt, active.run_binding_digest()) =>
                    {
                        Some(receipt)
                    }
                    Ok(_) | Err(_) => {
                        recovery_failed = true;
                        continue;
                    }
                },
                None => None,
            };
            let recovery = RuntimeRecoveryContext {
                authority_identity_digest,
                ticket: active.ticket().clone(),
                run_binding_digest: *active.run_binding_digest(),
                prepared_receipt,
                canonical_policy_snapshot: active.canonical_policy_snapshot().to_vec(),
                armed_receipt,
            };
            match self.supervisor.recover_and_wait_cleanup(&recovery) {
                #[cfg(windows)]
                Ok(SupervisorRecovery::Completed(completed)) => {
                    let context = run_context_from_recovery(&recovery);
                    if !completed_proof_matches_context(completed.terminal(), &context)
                        || !native_admission_matches_context(completed.admission(), &context)
                        || self.commit_native_completed(&context, completed).is_err()
                    {
                        recovery_failed = true;
                    }
                }
                #[cfg(windows)]
                Ok(SupervisorRecovery::Burned(proof)) => {
                    let terminal = proof.terminal();
                    let committed = match terminal.reason() {
                        BurnReason::RestartRecovery
                            if proof.normal_termination_recovery().is_none()
                                && burned_proof_matches_recovery(terminal, &recovery)
                                && terminal.cleanup_observed_at()
                                    >= terminal.terminal_ready_at() =>
                        {
                            self.ledger
                                .burn_recovered(recovery.ticket(), recovery.run_binding_digest())
                                .is_ok()
                        }
                        BurnReason::Cancelled | BurnReason::TimedOut => {
                            recovered_normal_burn_proof(&proof, &recovery).is_some_and(
                                |(reason, recovered_proof)| {
                                    self.ledger
                                        .burn_recovered_with_reason(
                                            recovery.ticket(),
                                            recovery.run_binding_digest(),
                                            reason,
                                            &recovered_proof,
                                        )
                                        .is_ok()
                                },
                            )
                        }
                        _ => false,
                    };
                    if committed {
                        burned += 1;
                    } else {
                        recovery_failed = true;
                    }
                }
                #[cfg(not(windows))]
                Ok(SupervisorRecovery::Burned(proof))
                    if burned_proof_matches_recovery(&proof, &recovery)
                        && proof.reason() == BurnReason::RestartRecovery =>
                {
                    if proof.cleanup_observed_at() < proof.terminal_ready_at()
                        || self
                            .ledger
                            .burn_recovered(recovery.ticket(), recovery.run_binding_digest())
                            .is_err()
                    {
                        recovery_failed = true;
                        continue;
                    }
                    burned += 1;
                }
                #[cfg(windows)]
                Err(_) => recovery_failed = true,
                #[cfg(not(windows))]
                Ok(_) | Err(_) => recovery_failed = true,
            }
        }
        if recovery_failed {
            self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
            return;
        }
        if self.ledger.verify_identity(&identity).is_err() {
            self.latch_global(BLOCKER_RUNTIME_STARTUP);
            return;
        }
        self.identity = Some(identity);
        self.startup_burned_tickets = burned;
    }

    fn recover_pending_verified_results(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError> {
        let pending = self.ledger.pending_verified_results()?;
        if pending.is_empty() {
            return Ok(());
        }
        self.projection_producer.verify_runtime_identity(identity)?;
        for value in pending {
            self.complete_pending_projection(value, identity)?;
        }
        Ok(())
    }

    fn complete_pending_projection(
        &mut self,
        mut pending: RuntimePendingVerifiedResult,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError> {
        if pending.ticket().digest() != *pending.result().ticket_digest()
            || pending.run_binding_digest() != pending.result().run_binding_digest()
        {
            return Err(RuntimeDependencyError::new(
                "pending_verified_result_binding_mismatch",
            ));
        }
        let prepared_receipt = PreparedRecoveryReceipt::decode(pending.prepared_receipt())
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        let armed_receipt = ArmedRecoveryReceipt::decode(pending.armed_receipt())
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        let policy =
            prepared_protected_evidence_policy_readback(pending.canonical_policy_snapshot())
                .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        let recovery_bundle_digest = compute_recovery_bundle_digest(
            pending.ticket().as_str(),
            &hex_encode(pending.run_binding_digest()),
            pending.prepared_receipt(),
            pending.canonical_policy_snapshot(),
        )
        .map_err(|_| RuntimeDependencyError::new("pending_recovery_bundle_invalid"))?;
        if prepared_receipt.digest() != *pending.result().prepared_receipt_digest()
            || !prepared_receipt.verifies_policy_snapshot(pending.canonical_policy_snapshot())
            || !armed_receipt.verifies_for(&prepared_receipt, pending.run_binding_digest())
            || recovery_bundle_digest != hex_encode(pending.recovery_bundle_digest())
            || policy.authority_identity_digest() != &identity.binding_digest()
            || policy.authority_generation_digest() != identity.authority_generation_digest()
            || policy.ticket_digest() != &pending.ticket().digest()
            || policy.run_binding_digest() != pending.run_binding_digest()
            || policy.prepared_receipt_digest() != pending.result().prepared_receipt_digest()
            || policy.policy_snapshot_digest() != pending.result().policy_snapshot_digest()
            || !policy.source().matches_runtime_identity(
                identity.authority_generation_digest(),
                identity.protected_manifest_digest(),
                identity.installed_layout_digest(),
                identity.service_binary_digest(),
            )
        {
            return Err(RuntimeDependencyError::new(
                "pending_verified_result_policy_binding_mismatch",
            ));
        }
        if !pending.result_committed() {
            self.ledger.record_result_exact(
                pending.ticket(),
                pending.run_binding_digest(),
                pending.result().finalization_bytes(),
            )?;
            let readback = self.pending_verified_result_exact(pending.ticket())?;
            if !readback.result_committed()
                || readback.result() != pending.result()
                || readback.projection().is_some()
            {
                return Err(RuntimeDependencyError::new(
                    "pending_verified_result_readback_mismatch",
                ));
            }
            pending = readback;
        }
        let terminal = self.ledger.reopen_result_commit_terminal(
            pending.ticket(),
            pending.run_binding_digest(),
            pending.result(),
        )?;
        let projection = match pending.projection() {
            Some(projection) => {
                self.projection_producer.verify_projection(
                    &policy,
                    pending.result(),
                    &terminal,
                    projection,
                )?;
                projection.clone()
            }
            None => {
                let projection = self.projection_producer.produce_projection(
                    &policy,
                    pending.result(),
                    &terminal,
                )?;
                self.projection_producer.verify_projection(
                    &policy,
                    pending.result(),
                    &terminal,
                    &projection,
                )?;
                self.ledger.record_projection_pending_exact(
                    pending.ticket(),
                    pending.run_binding_digest(),
                    &projection,
                )?;
                let readback = self.pending_verified_result_exact(pending.ticket())?;
                if !readback.result_committed()
                    || readback.result() != pending.result()
                    || readback.projection() != Some(&projection)
                {
                    return Err(RuntimeDependencyError::new(
                        "pending_projection_readback_mismatch",
                    ));
                }
                self.projection_producer.verify_projection(
                    &policy,
                    readback.result(),
                    &terminal,
                    &projection,
                )?;
                projection
            }
        };
        self.ledger.commit_projection_exact(
            pending.ticket(),
            pending.run_binding_digest(),
            projection.sha256(),
        )?;
        let readback = self
            .ledger
            .projection_exact(pending.ticket())?
            .filter(|readback| readback == &projection)
            .ok_or_else(|| RuntimeDependencyError::new("projection_commit_readback_mismatch"))?;
        let ledger_identity_digest = expected_ledger_identity_digest(identity)?;
        if readback.authority_generation_digest() != identity.authority_generation_digest()
            || readback.ledger_identity_digest() != &ledger_identity_digest
        {
            return Err(RuntimeDependencyError::new(
                "projection_commit_identity_mismatch",
            ));
        }
        let receipt = self
            .ledger
            .projection_commit_receipt_exact(
                pending.ticket(),
                pending.run_binding_digest(),
                &readback,
            )?
            .ok_or_else(|| RuntimeDependencyError::new("projection_commit_receipt_missing"))?;
        if !receipt.verifies_for(
            identity.authority_generation_digest(),
            &ledger_identity_digest,
            &pending.ticket().digest(),
            pending.run_binding_digest(),
            readback.canonical_bytes(),
        ) {
            return Err(RuntimeDependencyError::new(
                "projection_commit_receipt_mismatch",
            ));
        }
        Ok(())
    }

    fn pending_verified_result_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<RuntimePendingVerifiedResult, RuntimeDependencyError> {
        let mut matches = self
            .ledger
            .pending_verified_results()?
            .into_iter()
            .filter(|pending| pending.ticket() == ticket);
        let value = matches
            .next()
            .ok_or_else(|| RuntimeDependencyError::new("pending_verified_result_missing"))?;
        if matches.next().is_some() {
            return Err(RuntimeDependencyError::new(
                "pending_verified_result_duplicate",
            ));
        }
        Ok(value)
    }

    fn status(&mut self) -> AuthorityRuntimeStatus {
        if self.ensure_integrity().is_ok() {
            let _ = self.refresh_active();
        }
        let blockers = self.current_blockers(false);
        AuthorityRuntimeStatus {
            trusted_boundary_ready: blockers.is_empty(),
            global_failure: self.global_blocker.is_some(),
            blockers,
            active_request_id: self.active.as_ref().map(|run| run.request_id.clone()),
            startup_burned_tickets: self.startup_burned_tickets,
        }
    }

    fn self_test(&mut self) -> AuthorityRuntimeSelfTest {
        let mut blockers = Vec::new();
        if self.ensure_integrity().is_err() {
            blockers.push(self.global_blocker.unwrap_or(BLOCKER_RUNTIME_INTEGRITY));
        } else if let Err(blocker) = self.verified_readiness(true) {
            if blocker == BLOCKER_RUNTIME_INTEGRITY {
                self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
            }
            blockers.push(blocker);
        }
        AuthorityRuntimeSelfTest {
            passed: blockers.is_empty(),
            trusted_boundary_ready: blockers.is_empty(),
            blockers,
        }
    }

    fn run_model_part_composition(
        &mut self,
        request_id: String,
    ) -> Result<AuthorityRuntimeReply, AuthorityRuntimeError> {
        require_request_id(&request_id)?;
        self.ensure_integrity()?;
        self.refresh_active()?;
        if let Some(active) = &self.active {
            let code = if active.request_id == request_id {
                "authority_request_duplicate"
            } else {
                "authority_run_busy"
            };
            return Err(AuthorityRuntimeError::new(code));
        }
        let identity = self
            .identity
            .clone()
            .ok_or_else(|| AuthorityRuntimeError::new("authority_runtime_integrity_failed"))?;
        let readiness = match self.verified_readiness(false) {
            Ok(readiness) => readiness,
            Err(BLOCKER_RUNTIME_INTEGRITY) => {
                self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
                return Err(AuthorityRuntimeError::new(
                    "authority_runtime_integrity_failed",
                ));
            }
            Err(BLOCKER_PROJECTION_NOT_CONNECTED) => {
                return Err(AuthorityRuntimeError::new("authority_projection_not_ready"));
            }
            Err(_) => return Err(AuthorityRuntimeError::new("authority_supervisor_not_ready")),
        };

        let ticket = ticket_ref_for_identity(&identity, &request_id);
        match self.ledger.state(&ticket) {
            Ok(None) => {}
            Ok(Some(_)) => {
                return Err(AuthorityRuntimeError::new("authority_request_duplicate"));
            }
            Err(_) => return self.fail_ledger_integrity(),
        }
        let prepared =
            match self
                .supervisor
                .prepare(&identity, &ticket, readiness.service_instance_digest())
            {
                Ok(prepared)
                    if prepared.verifies_for(
                        &identity.binding_digest(),
                        &ticket.digest(),
                        readiness.service_instance_digest(),
                    ) =>
                {
                    prepared
                }
                Ok(_) => {
                    return self.contain_untrusted_preparation(&identity);
                }
                Err(_) => return Err(AuthorityRuntimeError::new("authority_supervisor_not_ready")),
            };
        let prepared_receipt = prepared.receipt().clone();
        let canonical_policy_snapshot = match self.supervisor.prepared_policy_snapshot(&prepared) {
            Ok(snapshot) => snapshot,
            Err(_) => return self.contain_untrusted_preparation(&identity),
        };
        if !prepared_receipt.verifies_policy_snapshot(&canonical_policy_snapshot) {
            return self.contain_untrusted_preparation(&identity);
        }
        let context = run_context(
            &identity,
            ticket.clone(),
            prepared_receipt.clone(),
            canonical_policy_snapshot.clone(),
        );
        if self
            .ledger
            .issue(
                &ticket,
                context.run_binding_digest(),
                &prepared_receipt.encode(),
                &canonical_policy_snapshot,
            )
            .is_err()
            || self
                .ledger
                .consume(&ticket, context.run_binding_digest())
                .is_err()
        {
            return self.abort_after_uncertain_supervisor_error(&context, false);
        }
        let context = match self.supervisor.start(prepared, &context) {
            Ok(SupervisorStart::Starting) => context,
            Ok(SupervisorStart::Armed(receipt))
                if receipt.verifies_for(&prepared_receipt, context.run_binding_digest()) =>
            {
                let context = context.with_armed_receipt(receipt.clone());
                if self
                    .ledger
                    .record_armed_receipt(&ticket, context.run_binding_digest(), &receipt.encode())
                    .is_err()
                {
                    return self.abort_after_uncertain_supervisor_error(&context, false);
                }
                context
            }
            Ok(SupervisorStart::Burned(proof)) => {
                if !burned_proof_matches_context(&proof, &context)
                    || proof.cleanup_observed_at() < proof.terminal_ready_at()
                    || self
                        .ledger
                        .burn(
                            context.ticket(),
                            context.run_binding_digest(),
                            runtime_terminal_reason(proof.reason()),
                        )
                        .is_err()
                {
                    return self.fail_ledger_integrity();
                }
                return Err(AuthorityRuntimeError::new("authority_run_terminated"));
            }
            Ok(SupervisorStart::Armed(_)) => {
                return self.fail_ledger_integrity();
            }
            Err(_) => return self.abort_after_uncertain_supervisor_error(&context, true),
        };
        self.active = Some(ActiveRun {
            request_id: request_id.clone(),
            context,
            cancel_requested: false,
        });
        Ok(AuthorityRuntimeReply::RunStarted { request_id })
    }

    fn cancel(
        &mut self,
        request_id: String,
    ) -> Result<AuthorityRuntimeReply, AuthorityRuntimeError> {
        require_request_id(&request_id)?;
        self.ensure_integrity()?;
        self.refresh_active()?;
        if self
            .active
            .as_ref()
            .is_some_and(|active| active.request_id == request_id)
        {
            let already_requested = self
                .active
                .as_ref()
                .is_some_and(|active| active.cancel_requested);
            if already_requested {
                return Ok(AuthorityRuntimeReply::CancelRequested {
                    request_id,
                    already_requested: true,
                });
            }
            let context = self
                .active
                .as_ref()
                .map(|active| active.context.clone())
                .ok_or_else(|| AuthorityRuntimeError::new("authority_runtime_invariant_failed"))?;
            return match self.supervisor.cancel(&context) {
                Ok(SupervisorCancelAcknowledgement::Recorded(kind))
                    if matches!(
                        kind,
                        RuntimeTerminalKind::Cancelled | RuntimeTerminalKind::TimedOut
                    ) =>
                {
                    if let Some(active) = self.active.as_mut() {
                        active.cancel_requested = true;
                    }
                    Ok(AuthorityRuntimeReply::CancelRequested {
                        request_id,
                        already_requested: false,
                    })
                }
                Ok(SupervisorCancelAcknowledgement::AlreadyRecorded(kind))
                    if matches!(
                        kind,
                        RuntimeTerminalKind::Cancelled | RuntimeTerminalKind::TimedOut
                    ) =>
                {
                    if let Some(active) = self.active.as_mut() {
                        active.cancel_requested = true;
                    }
                    Ok(AuthorityRuntimeReply::CancelRequested {
                        request_id,
                        already_requested: true,
                    })
                }
                Ok(SupervisorCancelAcknowledgement::AlreadyTerminal) => {
                    self.refresh_active()?;
                    if self
                        .active
                        .as_ref()
                        .is_some_and(|active| active.request_id == request_id)
                    {
                        return self.fail_ledger_integrity();
                    }
                    match self.ledger.state(context.ticket()) {
                        Ok(Some(RuntimeTicketState::Burned)) => {
                            Ok(AuthorityRuntimeReply::AlreadyTerminated {
                                reason: self.persisted_terminal_reason(context.ticket())?,
                                request_id,
                            })
                        }
                        Ok(Some(
                            RuntimeTicketState::ResultPendingProjection
                            | RuntimeTicketState::Result,
                        )) => Err(AuthorityRuntimeError::new("authority_result_already_final")),
                        Ok(Some(RuntimeTicketState::Issued | RuntimeTicketState::Consumed))
                        | Ok(None)
                        | Err(_) => self.fail_ledger_integrity(),
                    }
                }
                Ok(SupervisorCancelAcknowledgement::Uncertain) => Err(AuthorityRuntimeError::new(
                    CANCEL_ACKNOWLEDGEMENT_UNCERTAIN_CODE,
                )),
                Ok(
                    SupervisorCancelAcknowledgement::Recorded(_)
                    | SupervisorCancelAcknowledgement::AlreadyRecorded(_),
                ) => self.fail_ledger_integrity(),
                Err(_) => self.abort_after_uncertain_supervisor_error(&context, true),
            };
        }

        let ticket = self.ticket_for_request(&request_id)?;
        match self.ledger.state(&ticket) {
            Ok(Some(RuntimeTicketState::Burned)) => Ok(AuthorityRuntimeReply::AlreadyTerminated {
                reason: self.persisted_terminal_reason(&ticket)?,
                request_id,
            }),
            Ok(Some(RuntimeTicketState::ResultPendingProjection | RuntimeTicketState::Result)) => {
                Err(AuthorityRuntimeError::new("authority_result_already_final"))
            }
            Ok(Some(RuntimeTicketState::Issued | RuntimeTicketState::Consumed)) => {
                self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
                Err(AuthorityRuntimeError::new(
                    "authority_runtime_integrity_failed",
                ))
            }
            Ok(None) => Err(AuthorityRuntimeError::new("authority_request_not_found")),
            Err(_) => self.fail_ledger_integrity(),
        }
    }

    fn get_result(
        &mut self,
        request_id: String,
    ) -> Result<AuthorityRuntimeReply, AuthorityRuntimeError> {
        require_request_id(&request_id)?;
        self.ensure_integrity()?;
        self.refresh_active()?;
        let ticket = self.ticket_for_request(&request_id)?;
        let identity = match self.identity.clone() {
            Some(identity) => identity,
            None => return self.fail_ledger_integrity(),
        };
        let ledger_identity_digest = match expected_ledger_identity_digest(&identity) {
            Ok(digest) => digest,
            Err(_) => return self.fail_ledger_integrity(),
        };
        match self.ledger.state(&ticket) {
            Ok(Some(
                RuntimeTicketState::Issued
                | RuntimeTicketState::Consumed
                | RuntimeTicketState::ResultPendingProjection,
            )) => Ok(AuthorityRuntimeReply::ResultPending { request_id }),
            Ok(Some(RuntimeTicketState::Result)) => {
                let projection = match self.ledger.projection_exact(&ticket) {
                    Ok(Some(projection))
                        if projection.authority_generation_digest()
                            == identity.authority_generation_digest()
                            && projection.ledger_identity_digest() == &ledger_identity_digest =>
                    {
                        projection
                    }
                    Ok(None) | Err(_) => return self.fail_ledger_integrity(),
                    Ok(Some(_)) => return self.fail_ledger_integrity(),
                };
                let receipt = match self.ledger.projection_commit_receipt_exact(
                    &ticket,
                    projection.run_binding_digest(),
                    &projection,
                ) {
                    Ok(Some(receipt))
                        if receipt.verifies_for(
                            identity.authority_generation_digest(),
                            &ledger_identity_digest,
                            &ticket.digest(),
                            projection.run_binding_digest(),
                            projection.canonical_bytes(),
                        ) =>
                    {
                        receipt
                    }
                    Ok(_) | Err(_) => return self.fail_ledger_integrity(),
                };
                Ok(AuthorityRuntimeReply::ResultExact {
                    request_id,
                    projection,
                    receipt,
                })
            }
            Ok(Some(RuntimeTicketState::Burned)) => Ok(AuthorityRuntimeReply::ResultTerminated {
                reason: self.persisted_terminal_reason(&ticket)?,
                request_id,
            }),
            Ok(None) => Err(AuthorityRuntimeError::new("authority_request_not_found")),
            Err(_) => self.fail_ledger_integrity(),
        }
    }

    fn ensure_integrity(&mut self) -> Result<(), AuthorityRuntimeError> {
        if self.global_blocker.is_some() {
            return Err(AuthorityRuntimeError::new(
                "authority_runtime_integrity_failed",
            ));
        }
        let expected = self
            .identity
            .clone()
            .ok_or_else(|| AuthorityRuntimeError::new("authority_runtime_integrity_failed"))?;
        let current = match self.boundary.verify_installed_boundary() {
            Ok(current) => current,
            Err(_) => {
                self.abort_active_without_ledger_write();
                self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
                return Err(AuthorityRuntimeError::new(
                    "authority_runtime_integrity_failed",
                ));
            }
        };
        if current != expected || self.ledger.verify_identity(&expected).is_err() {
            self.abort_active_without_ledger_write();
            self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
            return Err(AuthorityRuntimeError::new(
                "authority_runtime_integrity_failed",
            ));
        }
        Ok(())
    }

    fn refresh_active(&mut self) -> Result<(), AuthorityRuntimeError> {
        let Some(context) = self.active.as_ref().map(|active| active.context.clone()) else {
            return Ok(());
        };
        let poll = match self.supervisor.poll(&context) {
            Ok(poll) => poll,
            Err(_) => return self.abort_after_uncertain_supervisor_error(&context, true),
        };
        match poll {
            SupervisorPoll::Starting if context.armed_receipt().is_none() => Ok(()),
            SupervisorPoll::Armed(receipt)
                if context.armed_receipt().is_none()
                    && receipt
                        .verifies_for(context.prepared_receipt(), context.run_binding_digest()) =>
            {
                if self
                    .ledger
                    .record_armed_receipt(
                        context.ticket(),
                        context.run_binding_digest(),
                        &receipt.encode(),
                    )
                    .is_err()
                {
                    return self.abort_after_uncertain_supervisor_error(&context, false);
                }
                let armed_context = context.with_armed_receipt(receipt);
                let Some(active) = self.active.as_mut() else {
                    return self.fail_ledger_integrity();
                };
                active.context = armed_context;
                Ok(())
            }
            SupervisorPoll::Running if context.armed_receipt().is_some() => Ok(()),
            SupervisorPoll::Starting | SupervisorPoll::Armed(_) | SupervisorPoll::Running => {
                self.fail_ledger_integrity()
            }
            #[cfg(windows)]
            SupervisorPoll::Terminal(ValidatedNativeTerminalRun::Completed(completed)) => {
                self.commit_native_completed(&context, completed)
            }
            #[cfg(windows)]
            SupervisorPoll::Terminal(ValidatedNativeTerminalRun::Burned(terminated)) => {
                self.commit_native_burned(&context, terminated)
            }
        }
    }

    #[cfg(windows)]
    fn commit_native_completed(
        &mut self,
        context: &RuntimeRunContext,
        completed: NativeCompletedRunProof,
    ) -> Result<(), AuthorityRuntimeError> {
        let terminal_proof = completed.terminal();
        let computed_result_digest: [u8; 32] = Sha256::digest(completed.result_bytes()).into();
        let computed_origin_digest: [u8; 32] =
            Sha256::digest(completed.canonical_origin_envelope_bytes()).into();
        if !completed_proof_matches_context(terminal_proof, context)
            || terminal_proof.cleanup_observed_at() < terminal_proof.finalized_at()
            || completed.origin_sealed_at() <= terminal_proof.cleanup_observed_at()
            || terminal_proof
                .cleanup_receipt_digest()
                .iter()
                .all(|byte| *byte == 0)
            || completed.result_bytes().is_empty()
            || completed.result_bytes().len() > MAX_RAW_FINALIZATION_BYTES
            || completed.result_digest() != &computed_result_digest
            || completed.canonical_origin_envelope_digest() != &computed_origin_digest
            || !native_admission_matches_context(completed.admission(), context)
        {
            return self.fail_ledger_integrity();
        }
        let identity = match self.identity.clone() {
            Some(identity) => identity,
            None => return self.fail_ledger_integrity(),
        };
        if self
            .projection_producer
            .verify_runtime_identity(&identity)
            .is_err()
        {
            return self.fail_ledger_integrity();
        }
        let result = match ServiceOwnedVerifiedRuntimeResult::from_native_completed(&completed) {
            Ok(result) => result,
            Err(_) => return self.fail_ledger_integrity(),
        };
        if self
            .ledger
            .record_verified_result_pending_exact(
                context.ticket(),
                context.run_binding_digest(),
                &result,
            )
            .is_err()
        {
            return self.fail_ledger_integrity();
        }
        let pending = match self.pending_verified_result_exact(context.ticket()) {
            Ok(pending)
                if pending.run_binding_digest() == context.run_binding_digest()
                    && pending.result() == &result
                    && !pending.result_committed()
                    && pending.projection().is_none() =>
            {
                pending
            }
            Ok(_) | Err(_) => return self.fail_ledger_integrity(),
        };
        if self
            .complete_pending_projection(pending, &identity)
            .is_err()
        {
            return self.fail_ledger_integrity();
        }
        self.active = None;
        Ok(())
    }

    #[cfg(windows)]
    fn commit_native_burned(
        &mut self,
        context: &RuntimeRunContext,
        terminated: NativeBurnedRunProof,
    ) -> Result<(), AuthorityRuntimeError> {
        let terminal = terminated.terminal();
        if !burned_proof_matches_context(terminal, context)
            || terminal.cleanup_observed_at() < terminal.terminal_ready_at()
            || terminal
                .cleanup_receipt_digest()
                .iter()
                .all(|byte| *byte == 0)
            || context.armed_receipt().is_some()
                && !terminated
                    .admission()
                    .is_some_and(|admission| native_admission_matches_context(admission, context))
        {
            return self.fail_ledger_integrity();
        }
        let reason = runtime_terminal_reason(terminal.reason());
        if self
            .ledger
            .burn(context.ticket(), context.run_binding_digest(), reason)
            .is_err()
        {
            return self.fail_ledger_integrity();
        }
        self.active = None;
        Ok(())
    }

    fn current_blockers(&mut self, run_self_test: bool) -> Vec<&'static str> {
        if let Some(blocker) = self.global_blocker {
            return vec![blocker];
        }
        match self.verified_readiness(run_self_test) {
            Ok(_) => Vec::new(),
            Err(BLOCKER_RUNTIME_INTEGRITY) => {
                self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
                vec![BLOCKER_RUNTIME_INTEGRITY]
            }
            Err(blocker) => vec![blocker],
        }
    }

    fn fail_ledger_integrity<T>(&mut self) -> Result<T, AuthorityRuntimeError> {
        self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
        Err(AuthorityRuntimeError::new(
            "authority_runtime_integrity_failed",
        ))
    }

    fn contain_untrusted_preparation<T>(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<T, AuthorityRuntimeError> {
        let containment_succeeded = self.supervisor.contain_all_orphans(identity).is_ok();
        self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
        if !containment_succeeded {
            return Err(AuthorityRuntimeError::new(
                "authority_runtime_integrity_failed",
            ));
        }
        Err(AuthorityRuntimeError::new(
            "authority_runtime_integrity_failed",
        ))
    }

    fn abort_after_uncertain_supervisor_error<T>(
        &mut self,
        context: &RuntimeRunContext,
        persist_burn: bool,
    ) -> Result<T, AuthorityRuntimeError> {
        let proof = self.supervisor.abort_and_wait_cleanup(context);
        if let Ok(proof) = proof {
            if burned_proof_matches_context(&proof, context)
                && proof.cleanup_observed_at() >= proof.terminal_ready_at()
                && !proof.cleanup_receipt_digest().iter().all(|byte| *byte == 0)
            {
                if persist_burn
                    && self
                        .ledger
                        .burn(
                            context.ticket(),
                            context.run_binding_digest(),
                            runtime_terminal_reason(proof.reason()),
                        )
                        .is_err()
                {
                    self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
                    return Err(AuthorityRuntimeError::new(
                        "authority_runtime_integrity_failed",
                    ));
                }
                self.active = None;
            }
        }
        self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
        Err(AuthorityRuntimeError::new(
            "authority_runtime_integrity_failed",
        ))
    }

    fn abort_active_without_ledger_write(&mut self) {
        let Some(context) = self.active.as_ref().map(|active| active.context.clone()) else {
            return;
        };
        if let Ok(proof) = self.supervisor.abort_and_wait_cleanup(&context) {
            if burned_proof_matches_context(&proof, &context)
                && proof.cleanup_observed_at() >= proof.terminal_ready_at()
                && !proof.cleanup_receipt_digest().iter().all(|byte| *byte == 0)
            {
                self.active = None;
            }
        }
    }

    fn verified_readiness(
        &mut self,
        run_self_test: bool,
    ) -> Result<VerifiedReadinessProof, &'static str> {
        let identity = self.identity.clone().ok_or(BLOCKER_RUNTIME_INTEGRITY)?;
        let proof = if run_self_test {
            self.supervisor.self_test(&identity)
        } else {
            self.supervisor.readiness(&identity)
        }
        .map_err(|error| {
            if error.code() == SUPERVISOR_NOT_CONNECTED_CODE {
                BLOCKER_SUPERVISOR_NOT_CONNECTED
            } else {
                BLOCKER_SUPERVISOR_UNAVAILABLE
            }
        })?;
        if !proof.verifies_for(&identity.binding_digest()) {
            return Err(BLOCKER_RUNTIME_INTEGRITY);
        }
        self.projection_producer
            .verify_runtime_identity(&identity)
            .map_err(|error| {
                if error.code() == PROJECTION_NOT_CONNECTED_CODE {
                    BLOCKER_PROJECTION_NOT_CONNECTED
                } else {
                    BLOCKER_RUNTIME_INTEGRITY
                }
            })?;
        Ok(proof)
    }

    fn ticket_for_request(
        &self,
        request_id: &str,
    ) -> Result<RuntimeTicketRef, AuthorityRuntimeError> {
        let identity = self
            .identity
            .as_ref()
            .ok_or_else(|| AuthorityRuntimeError::new("authority_runtime_integrity_failed"))?;
        Ok(ticket_ref_for_identity(identity, request_id))
    }

    fn persisted_terminal_reason(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<RuntimeTerminalKind, AuthorityRuntimeError> {
        match self.ledger.terminal_reason(ticket) {
            Ok(Some(reason)) => Ok(reason),
            Ok(None) | Err(_) => self.fail_ledger_integrity(),
        }
    }

    fn latch_global(&mut self, blocker: &'static str) {
        if self.global_blocker.is_none() {
            self.global_blocker = Some(blocker);
        }
    }
}

fn runtime_terminal_reason(reason: BurnReason) -> RuntimeTerminalKind {
    match reason {
        BurnReason::Cancelled => RuntimeTerminalKind::Cancelled,
        BurnReason::TimedOut => RuntimeTerminalKind::TimedOut,
        BurnReason::Failed => RuntimeTerminalKind::Failed,
        BurnReason::RestartRecovery => RuntimeTerminalKind::RestartRecovery,
    }
}

fn expected_ledger_identity_digest(
    identity: &AuthorityRuntimeIdentity,
) -> Result<[u8; 32], RuntimeDependencyError> {
    LedgerIdentity::from_digests(
        *identity.authority_generation_digest(),
        *identity.signer_key_id(),
    )
    .map(|identity| identity.canonical_digest())
    .map_err(|error| RuntimeDependencyError::new(error.code()))
}

#[cfg(windows)]
fn native_admission_matches_context(
    admission: &NativeAdmissionBinding,
    context: &RuntimeRunContext,
) -> bool {
    let Some(armed) = context.armed_receipt() else {
        return false;
    };
    let armed_digest: [u8; 32] = Sha256::digest(armed.encode()).into();
    let policy_digest: [u8; 32] = Sha256::digest(context.canonical_policy_snapshot()).into();
    let recovery_digest = compute_recovery_bundle_digest(
        context.ticket().as_str(),
        &hex_encode(context.run_binding_digest()),
        &context.prepared_receipt().encode(),
        context.canonical_policy_snapshot(),
    )
    .ok()
    .and_then(|value| decode_digest(&value));
    admission.prepared_receipt_digest() == &context.prepared_receipt().digest()
        && admission.armed_receipt_digest() == &armed_digest
        && admission.policy_snapshot_digest() == &policy_digest
        && recovery_digest.as_ref() == Some(admission.recovery_bundle_digest())
}

fn ticket_ref_for_identity(
    identity: &AuthorityRuntimeIdentity,
    request_id: &str,
) -> RuntimeTicketRef {
    RuntimeTicketRef::for_request(identity, request_id)
}

fn run_context(
    identity: &AuthorityRuntimeIdentity,
    ticket: RuntimeTicketRef,
    prepared_receipt: PreparedRecoveryReceipt,
    canonical_policy_snapshot: Vec<u8>,
) -> RuntimeRunContext {
    let authority_identity_digest = identity.binding_digest();
    let service_instance_digest = *prepared_receipt.service_instance_digest();
    let runner_policy_digest = *prepared_receipt.runner_policy_digest();
    let run_binding_digest = derive_run_binding_digest(
        &authority_identity_digest,
        &ticket.digest(),
        &service_instance_digest,
        &runner_policy_digest,
    );
    RuntimeRunContext {
        authority_identity_digest,
        ticket,
        run_binding_digest,
        service_instance_digest,
        runner_policy_digest,
        prepared_receipt,
        canonical_policy_snapshot,
        armed_receipt: None,
    }
}

fn run_context_from_recovery(context: &RuntimeRecoveryContext) -> RuntimeRunContext {
    RuntimeRunContext {
        authority_identity_digest: context.authority_identity_digest,
        ticket: context.ticket.clone(),
        run_binding_digest: context.run_binding_digest,
        service_instance_digest: *context.prepared_receipt.service_instance_digest(),
        runner_policy_digest: *context.prepared_receipt.runner_policy_digest(),
        prepared_receipt: context.prepared_receipt.clone(),
        canonical_policy_snapshot: context.canonical_policy_snapshot.clone(),
        armed_receipt: context.armed_receipt.clone(),
    }
}

fn completed_proof_matches_context(proof: &CompletedRunProof, context: &RuntimeRunContext) -> bool {
    proof.authority_identity_digest() == context.authority_identity_digest()
        && proof.ticket_digest() == &context.ticket().digest()
        && proof.run_binding_digest() == context.run_binding_digest()
}

fn burned_proof_matches_context(proof: &BurnedRunProof, context: &RuntimeRunContext) -> bool {
    proof.authority_identity_digest() == context.authority_identity_digest()
        && proof.ticket_digest() == &context.ticket().digest()
        && proof.run_binding_digest() == context.run_binding_digest()
        && proof.reason() != BurnReason::RestartRecovery
}

fn burned_proof_matches_recovery(proof: &BurnedRunProof, context: &RuntimeRecoveryContext) -> bool {
    proof.authority_identity_digest() == context.authority_identity_digest()
        && proof.ticket_digest() == &context.ticket().digest()
        && proof.run_binding_digest() == context.run_binding_digest()
        && !proof.cleanup_receipt_digest().iter().all(|byte| *byte == 0)
}

#[cfg(windows)]
fn recovered_normal_burn_proof(
    proof: &NativeBurnedRunProof,
    context: &RuntimeRecoveryContext,
) -> Option<(RuntimeTerminalKind, RecoveredBurnProof)> {
    let terminal = proof.terminal();
    let (runtime_reason, ledger_reason) = match terminal.reason() {
        BurnReason::Cancelled => (RuntimeTerminalKind::Cancelled, TicketBurnReason::Cancelled),
        BurnReason::TimedOut => (RuntimeTerminalKind::TimedOut, TicketBurnReason::TimedOut),
        BurnReason::Failed | BurnReason::RestartRecovery => return None,
    };
    let binding = proof.normal_termination_recovery()?;
    if !burned_proof_matches_recovery(terminal, context)
        || terminal.cleanup_observed_at() < terminal.terminal_ready_at()
        || binding.cleanup_digest() != terminal.cleanup_receipt_digest()
    {
        return None;
    }

    let expected_armed_digest: Option<[u8; 32]> = context
        .armed_receipt()
        .map(|receipt| Sha256::digest(receipt.encode()).into());
    if binding.armed_receipt_digest().copied() != expected_armed_digest {
        return None;
    }
    match (context.armed_receipt(), proof.admission()) {
        (None, None) => {}
        (Some(_), Some(admission)) => {
            let run_context = run_context_from_recovery(context);
            if !native_admission_matches_context(admission, &run_context) {
                return None;
            }
        }
        _ => return None,
    }

    let prepared_receipt_digest = context.prepared_receipt().digest();
    let recovery_proof_digest = RecoveredBurnProof::canonical_digest(
        context.ticket().digest(),
        *context.run_binding_digest(),
        prepared_receipt_digest,
        expected_armed_digest,
        *binding.stage_journal_head_digest(),
        *binding.termination_intent_digest(),
        *binding.terminal_digest(),
        *binding.cleanup_digest(),
        ledger_reason,
    )
    .ok()?;
    let recovered = RecoveredBurnProof::from_verified_digest(
        recovery_proof_digest,
        context.ticket().digest(),
        *context.run_binding_digest(),
        prepared_receipt_digest,
        expected_armed_digest,
        *binding.stage_journal_head_digest(),
        *binding.termination_intent_digest(),
        *binding.terminal_digest(),
        *binding.cleanup_digest(),
        ledger_reason,
    )
    .ok()?;
    Some((runtime_reason, recovered))
}

fn decode_digest(value: &str) -> Option<[u8; 32]> {
    if value.len() != 64
        || !value
            .as_bytes()
            .iter()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
    {
        return None;
    }
    let mut output = [0u8; 32];
    for (index, chunk) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (hex_nibble(chunk[0]) << 4) | hex_nibble(chunk[1]);
    }
    if output.iter().all(|byte| *byte == 0) {
        None
    } else {
        Some(output)
    }
}

fn hex_nibble(value: u8) -> u8 {
    match value {
        b'0'..=b'9' => value - b'0',
        b'a'..=b'f' => value - b'a' + 10,
        _ => 0,
    }
}

fn hex_encode(value: &[u8; 32]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(64);
    for byte in value {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn require_request_id(value: &str) -> Result<(), AuthorityRuntimeError> {
    let mut characters = value.chars();
    let first = characters
        .next()
        .filter(|value| value.is_ascii_alphanumeric());
    if first.is_none()
        || value.len() > 128
        || !characters
            .all(|value| value.is_ascii_alphanumeric() || matches!(value, '-' | '_' | '.' | ':'))
    {
        return Err(AuthorityRuntimeError::new("authority_request_id_invalid"));
    }
    Ok(())
}

#[cfg(test)]
#[path = "primitive_evidence_authority_runtime/tests.rs"]
mod tests;
