#[cfg(windows)]
#[allow(dead_code)]
#[path = "primitive_evidence_authority_service_runtime/production_admission.rs"]
pub(crate) mod production_admission;

use crate::primitive_basis_protected_evidence_bundle::{
    DurableBinaryLedgerTerminal, ProtectedEvidenceBundleError, ReopenedBinaryLedgerReadback,
    ServiceOwnedVerifiedRuntimeResult, VerifiedAuthorityResultProjection,
};
#[cfg(windows)]
use crate::primitive_basis_protected_evidence_bundle::{
    ProtectedBundleSigningDigest, ProtectedEvidenceBundleProducer, ProtectedEvidenceBundleSigner,
};
#[cfg(test)]
use crate::primitive_evidence_authority_blob::AuthenticatedProtectedBlobNamespace;
use crate::primitive_evidence_authority_contract::ProtocolExit;
#[cfg(windows)]
use crate::primitive_evidence_authority_contract::{
    AuthorityGenerationAttestationSigner, AuthorityGenerationBinding,
    AuthorityGenerationBindingVerifier, AuthorityProjectionCommitReceiptSigner,
    AuthorityProjectionCommitReceiptVerifier, ContractError,
};
use crate::primitive_evidence_authority_install::bootstrap::AuthenticatedFinalCommitBootstrap;
#[cfg(windows)]
use crate::primitive_evidence_authority_install::bootstrap::{
    AuthenticatedFinalCommitBoundary, AuthenticatedGenerationBindingReadback,
};
#[cfg(windows)]
use crate::primitive_evidence_authority_ledger::AuthenticatedPublishedAuthorityLedger;
use crate::primitive_evidence_authority_ledger::{
    AuthorityLedger, DurableResultCommitReadback, DurableVerifiedResult, LedgerIdentity,
    RecoveredBurnProof, TicketBurnReason, TicketState,
};
use crate::primitive_evidence_authority_pipe::AuthorityConnectionGate;
#[cfg(windows)]
use crate::primitive_evidence_authority_pipe::{
    InstalledControllerPolicy, FIXED_MODEL_PART_HANDLE_COUNT,
};
use crate::primitive_evidence_authority_runtime::{
    AuthorityRuntime, AuthorityRuntimeCommand, AuthorityRuntimeIdentity, AuthorityRuntimeReply,
    FixedModelPartSupervisor, InstalledBoundaryVerifier, RuntimeActiveTicket,
    RuntimeDependencyError, RuntimePendingVerifiedResult, RuntimeRecoveryContext,
    RuntimeRunContext, RuntimeTerminalKind, RuntimeTicketLedger, RuntimeTicketRef,
    RuntimeTicketState, SupervisorCancelAcknowledgement, SupervisorPoll, SupervisorRecovery,
    SupervisorStart,
};
#[cfg(windows)]
use crate::primitive_evidence_authority_supervisor::policy_source::{
    build_verified_readiness, prepare_model_part_run, FixedModelPartHandles,
    FixedModelPartHeldReadback, ProtectedRuntimeSourceReadback,
};
use crate::primitive_evidence_authority_supervisor::{
    BurnedRunProof, PreparedRun, VerifiedReadinessProof,
};
use std::path::{Component, Path, PathBuf};
#[cfg(windows)]
use std::sync::{Arc, Mutex};

#[cfg(windows)]
use crate::primitive_evidence_authority_supervisor::native_windows::{
    authority_process::HeldAuthorityProcessHandles,
    background::{
        BackgroundCancelAcknowledgement, BackgroundNativePoll, BackgroundNativeSupervisor,
        BackgroundRunKey,
    },
    verified_job_security_binding, ValidatedNativeTerminalRun, WindowsNativeSupervisorApi,
};
#[cfg(windows)]
use production_admission::ProductionRunAdmission;

pub(crate) const SERVICE_EXIT_SUCCESS: u32 = 0;
pub(crate) const SERVICE_EXIT_FAILURE: u32 = 2;
const PRODUCTION_SUPERVISOR_BLOCKER: &str = "authority_native_runtime_disabled";
const PRODUCTION_EVIDENCE_BINDINGS_BLOCKER: &str =
    "authority_protected_evidence_bindings_not_connected";
pub(crate) const PRODUCTION_PIPE_ADMISSION_BLOCKER: &str =
    "authority_controller_launch_receipt_not_connected";

/// Owns the process-level lifecycle around the one-peer-at-a-time pipe
/// session. A clean disconnect creates a fresh pipe instance; stop is clean;
/// every protocol or transport integrity failure is process-fatal.
pub(crate) fn run_fixed_pipe_loop<A, S>(
    gate: &AuthorityConnectionGate,
    mut attempt: A,
    mut stop_requested: S,
) -> u32
where
    A: FnMut() -> Result<ProtocolExit, String>,
    S: FnMut() -> bool,
{
    loop {
        if gate.has_failed() {
            return SERVICE_EXIT_FAILURE;
        }
        if stop_requested() || gate.is_stop_requested() {
            gate.request_stop();
            return SERVICE_EXIT_SUCCESS;
        }

        match attempt() {
            Ok(ProtocolExit::PeerClosed) => {}
            Ok(ProtocolExit::StopRequested) => {
                gate.request_stop();
                return SERVICE_EXIT_SUCCESS;
            }
            Ok(ProtocolExit::Fatal) => {
                gate.latch_failure();
                return SERVICE_EXIT_FAILURE;
            }
            Err(_) if !gate.has_failed() && (stop_requested() || gate.is_stop_requested()) => {
                gate.request_stop();
                return SERVICE_EXIT_SUCCESS;
            }
            Err(_) => {
                gate.latch_failure();
                return SERVICE_EXIT_FAILURE;
            }
        }
    }
}

pub(crate) fn require_production_pipe_admission() -> Result<(), RuntimeDependencyError> {
    Err(RuntimeDependencyError::new(
        PRODUCTION_PIPE_ADMISSION_BLOCKER,
    ))
}

pub(crate) fn require_production_runtime_ready(
    runtime: &AuthorityRuntime,
) -> Result<(), RuntimeDependencyError> {
    match runtime.handle(AuthorityRuntimeCommand::Status) {
        Ok(AuthorityRuntimeReply::Status(status))
            if status.trusted_boundary_ready
                && !status.global_failure
                && status.blockers.is_empty() =>
        {
            Ok(())
        }
        Ok(AuthorityRuntimeReply::Status(_)) => Err(RuntimeDependencyError::new(
            "authority_production_runtime_not_ready",
        )),
        Ok(_) => Err(RuntimeDependencyError::new(
            "authority_production_runtime_status_invalid",
        )),
        Err(error) => Err(RuntimeDependencyError::new(error.code())),
    }
}

#[derive(Debug)]
pub(crate) struct DurableRuntimeLedger {
    identity: LedgerIdentity,
    ledger: AuthorityLedger,
}

impl DurableRuntimeLedger {
    fn validate_path(path: &Path) -> Result<(), RuntimeDependencyError> {
        if !path.is_absolute()
            || path.as_os_str().is_empty()
            || path
                .components()
                .any(|part| matches!(part, Component::CurDir | Component::ParentDir))
            || path.file_name().and_then(|value| value.to_str()) != Some("ledger.bin")
        {
            return Err(RuntimeDependencyError::new(
                "authority_runtime_ledger_path_invalid",
            ));
        }
        Ok(())
    }

    /// Path-only construction is a test seam for legacy ledger fixtures. The
    /// production runtime must receive the ledger through the authenticated
    /// published-pair adoption path.
    #[cfg(test)]
    pub(crate) fn new(
        path: PathBuf,
        identity: LedgerIdentity,
    ) -> Result<Self, RuntimeDependencyError> {
        Self::validate_path(&path)?;
        let ledger = AuthorityLedger::open_existing(&path, identity.clone())
            .map_err(runtime_ledger_error)?;
        ledger
            .verify_authenticated_binding(&path, &identity)
            .map_err(runtime_ledger_error)?;
        Ok(Self { identity, ledger })
    }

    #[cfg(test)]
    fn from_test_ledger(
        path: PathBuf,
        identity: LedgerIdentity,
        ledger: AuthorityLedger,
    ) -> Result<Self, RuntimeDependencyError> {
        Self::validate_path(&path)?;
        ledger
            .verify_authenticated_binding(&path, &identity)
            .map_err(runtime_ledger_error)?;
        Ok(Self { identity, ledger })
    }

    #[cfg(windows)]
    pub(crate) fn from_published_adoption(
        path: PathBuf,
        identity: LedgerIdentity,
        adoption: AuthenticatedPublishedAuthorityLedger,
    ) -> Result<Self, RuntimeDependencyError> {
        Self::validate_path(&path)?;
        let ledger = adoption
            .consume_for_runtime(&path, &identity)
            .map_err(runtime_ledger_error)?;
        Ok(Self { identity, ledger })
    }

    /// Empty/legacy-ledger test seam only. Production must inject the typed
    /// authority before bootstrap replays any ledger frames.
    #[cfg(test)]
    fn from_authenticated_ledger_with_test_namespace(
        path: PathBuf,
        identity: LedgerIdentity,
        mut ledger: AuthorityLedger,
        namespace: AuthenticatedProtectedBlobNamespace,
    ) -> Result<Self, RuntimeDependencyError> {
        let authority = namespace
            .into_authority()
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        ledger
            .attach_protected_blob_authority(authority)
            .map_err(runtime_ledger_error)?;
        Self::from_test_ledger(path, identity, ledger)
    }

    fn verify_runtime_identity(
        &self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError> {
        if identity.authority_generation_digest() != self.identity.authority_generation_digest()
            || identity.signer_key_id() != self.identity.signer_key_id()
        {
            return Err(RuntimeDependencyError::new(
                "authority_runtime_ledger_identity_mismatch",
            ));
        }
        Ok(())
    }

    fn ledger(&self) -> Result<&AuthorityLedger, RuntimeDependencyError> {
        Ok(&self.ledger)
    }

    fn ledger_mut(&mut self) -> Result<&mut AuthorityLedger, RuntimeDependencyError> {
        Ok(&mut self.ledger)
    }

    fn require_protected_blob_authority(&self) -> Result<(), RuntimeDependencyError> {
        if self.ledger()?.has_protected_blob_authority() {
            Ok(())
        } else {
            Err(RuntimeDependencyError::new(
                PRODUCTION_EVIDENCE_BINDINGS_BLOCKER,
            ))
        }
    }

    fn verify_run_binding(
        &self,
        ticket: &RuntimeTicketRef,
        expected: &[u8; 32],
    ) -> Result<(), RuntimeDependencyError> {
        let stored = self
            .ledger()?
            .run_binding_digest(ticket.as_str())
            .map_err(runtime_ledger_error)?;
        if stored.as_deref() != Some(&hex_lower(expected)) {
            return Err(RuntimeDependencyError::new(
                "authority_runtime_run_binding_mismatch",
            ));
        }
        Ok(())
    }

    fn reopen_projection_receipt(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        projection: &VerifiedAuthorityResultProjection,
    ) -> Result<
        crate::primitive_evidence_authority_ledger::DurableProjectionCommitReceipt,
        RuntimeDependencyError,
    > {
        self.verify_run_binding(ticket, run_binding_digest)?;
        self.ledger_mut()?
            .projection_commit_receipt_from_held_pair(
                ticket.as_str(),
                &hex_lower(run_binding_digest),
                projection.canonical_bytes(),
            )
            .map_err(runtime_ledger_error)
    }
}

impl RuntimeTicketLedger for DurableRuntimeLedger {
    fn open_existing(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError> {
        self.verify_runtime_identity(identity)?;
        self.ledger
            .verify_current_identity()
            .map_err(runtime_ledger_error)
    }

    fn active_tickets(&mut self) -> Result<Vec<RuntimeActiveTicket>, RuntimeDependencyError> {
        self.ledger()?
            .active_tickets()
            .map_err(runtime_ledger_error)?
            .into_iter()
            .map(|value| {
                RuntimeActiveTicket::new(
                    RuntimeTicketRef::from_persisted(value.ticket_digest())?,
                    decode_digest(value.run_binding_digest())?,
                    value.prepared_receipt().to_vec(),
                    value.canonical_policy_snapshot().to_vec(),
                    value.recovery_bundle_digest().to_owned(),
                    value.armed_receipt().map(ToOwned::to_owned),
                )
            })
            .collect()
    }

    fn verify_identity(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError> {
        self.verify_runtime_identity(identity)?;
        self.ledger()?
            .verify_current_identity()
            .map_err(runtime_ledger_error)
    }

    fn state(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<RuntimeTicketState>, RuntimeDependencyError> {
        self.ledger()?
            .state(ticket.as_str())
            .map(|state| state.map(runtime_ticket_state))
            .map_err(runtime_ledger_error)
    }

    fn issue(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        prepared_receipt_bytes: &[u8],
        canonical_policy_snapshot: &[u8],
    ) -> Result<(), RuntimeDependencyError> {
        self.ledger_mut()?
            .issue_with_binding_and_recovery(
                ticket.as_str(),
                &hex_lower(run_binding_digest),
                prepared_receipt_bytes,
                canonical_policy_snapshot,
            )
            .map_err(runtime_ledger_error)
    }

    fn consume(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
    ) -> Result<(), RuntimeDependencyError> {
        self.verify_run_binding(ticket, run_binding_digest)?;
        self.ledger_mut()?
            .consume(ticket.as_str())
            .map_err(runtime_ledger_error)
    }

    fn record_armed_receipt(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        armed_receipt_bytes: &[u8],
    ) -> Result<(), RuntimeDependencyError> {
        self.ledger_mut()?
            .record_armed_receipt(
                ticket.as_str(),
                &hex_lower(run_binding_digest),
                armed_receipt_bytes,
            )
            .map_err(runtime_ledger_error)
    }

    fn record_result_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        result_bytes: &[u8],
    ) -> Result<(), RuntimeDependencyError> {
        self.verify_run_binding(ticket, run_binding_digest)?;
        self.ledger_mut()?
            .record_result_bytes(ticket.as_str(), result_bytes)
            .map_err(runtime_ledger_error)
    }

    fn record_verified_result_pending_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        result: &ServiceOwnedVerifiedRuntimeResult,
    ) -> Result<(), RuntimeDependencyError> {
        let durable = DurableVerifiedResult::new(
            result.finalization_bytes().to_vec(),
            result.origin_envelope_bytes().to_vec(),
            ticket.digest(),
            *run_binding_digest,
            *result.finalization_digest(),
            *result.origin_envelope_digest(),
            *result.cleanup_digest(),
            *result.prepared_receipt_digest(),
            *result.armed_receipt_digest(),
            *result.policy_snapshot_digest(),
            *result.recovery_bundle_digest(),
        )
        .map_err(runtime_ledger_error)?;
        self.ledger_mut()?
            .record_verified_result_pending(
                ticket.as_str(),
                &hex_lower(run_binding_digest),
                &durable,
            )
            .map_err(runtime_ledger_error)
    }

    fn pending_verified_results(
        &mut self,
    ) -> Result<Vec<RuntimePendingVerifiedResult>, RuntimeDependencyError> {
        self.ledger()?
            .pending_verified_results()
            .map_err(runtime_ledger_error)?
            .into_iter()
            .map(|(ticket, value)| {
                let projection = value
                    .projection()
                    .map(|(bytes, digest)| {
                        VerifiedAuthorityResultProjection::from_immutable_ledger_readback(
                            bytes.to_vec(),
                            *digest,
                        )
                        .map_err(|error| RuntimeDependencyError::new(error.code()))
                    })
                    .transpose()?;
                RuntimePendingVerifiedResult::from_durable(
                    RuntimeTicketRef::from_persisted(&ticket)?,
                    value.record(),
                    value.prepared_receipt(),
                    value.canonical_policy_snapshot(),
                    value.recovery_bundle_digest(),
                    value.armed_receipt(),
                    value.result_committed(),
                    projection,
                )
            })
            .collect()
    }

    fn result_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<Vec<u8>>, RuntimeDependencyError> {
        self.ledger()?
            .result_bytes(ticket.as_str())
            .map_err(runtime_ledger_error)
    }

    fn reopen_result_commit_terminal(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        result: &ServiceOwnedVerifiedRuntimeResult,
    ) -> Result<DurableBinaryLedgerTerminal, RuntimeDependencyError> {
        self.verify_run_binding(ticket, run_binding_digest)?;
        let raw: DurableResultCommitReadback = self
            .ledger_mut()?
            .result_commit_readback_from_held_pair(
                ticket.as_str(),
                &hex_lower(run_binding_digest),
                result.finalization_bytes(),
            )
            .map_err(runtime_ledger_error)?;
        if raw.terminal_ticket_digest() != &ticket.digest()
            || raw.terminal_result_digest() != result.finalization_digest()
            || raw.run_binding_digest() != run_binding_digest
            || raw.prepared_receipt_digest() != result.prepared_receipt_digest()
            || raw.armed_receipt_digest() != result.armed_receipt_digest()
            || raw.policy_snapshot_digest() != result.policy_snapshot_digest()
            || raw.recovery_bundle_digest() != result.recovery_bundle_digest()
        {
            return Err(RuntimeDependencyError::new(
                "protected_binary_terminal_binding_mismatch",
            ));
        }
        let readback = ReopenedBinaryLedgerReadback::from_held_and_reopened_ledger(
            *raw.ledger_file_digest(),
            *raw.anchor_file_digest(),
            *raw.ledger_file_identity_digest(),
            *raw.anchor_file_identity_digest(),
            raw.ledger_length(),
            raw.anchor_length(),
            raw.frame_count(),
            0,
            *raw.latest_frame_digest(),
            *raw.anchor_record_digest(),
            raw.terminal_sequence(),
            *raw.terminal_frame_digest(),
            *raw.terminal_ticket_digest(),
        )
        .map_err(protected_bundle_error)?;
        let (issued_at, consumed_at, completed_at) = result
            .durable_terminal_timestamps()
            .map_err(protected_bundle_error)?;
        DurableBinaryLedgerTerminal::from_reopened_result_commit(
            raw.receipt_ordinal(),
            *raw.previous_receipt_digest(),
            raw.predecessor_sequence(),
            raw.terminal_sequence(),
            *raw.predecessor_frame_digest(),
            *raw.terminal_frame_digest(),
            *raw.terminal_ticket_digest(),
            *raw.terminal_result_digest(),
            raw.anchor_sequence(),
            *raw.anchor_frame_digest(),
            *raw.anchor_ticket_digest(),
            *raw.run_binding_digest(),
            *raw.prepared_receipt_digest(),
            *raw.armed_receipt_digest(),
            *raw.policy_snapshot_digest(),
            *raw.recovery_bundle_digest(),
            *result.origin_envelope_digest(),
            *result.cleanup_digest(),
            *raw.anchor_record_digest(),
            issued_at,
            consumed_at,
            completed_at,
            readback,
        )
        .map_err(protected_bundle_error)
    }

    fn record_projection_pending_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        projection: &VerifiedAuthorityResultProjection,
    ) -> Result<(), RuntimeDependencyError> {
        self.ledger_mut()?
            .record_projection_pending(
                ticket.as_str(),
                &hex_lower(run_binding_digest),
                projection.canonical_bytes(),
                projection.sha256(),
            )
            .map_err(runtime_ledger_error)
    }

    fn commit_projection_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        projection_digest: &[u8; 32],
    ) -> Result<(), RuntimeDependencyError> {
        self.ledger_mut()?
            .commit_projection(
                ticket.as_str(),
                &hex_lower(run_binding_digest),
                projection_digest,
            )
            .map_err(runtime_ledger_error)
    }

    fn projection_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<VerifiedAuthorityResultProjection>, RuntimeDependencyError> {
        self.ledger()?
            .projection_bytes(ticket.as_str())
            .map_err(runtime_ledger_error)?
            .map(|(bytes, digest)| {
                VerifiedAuthorityResultProjection::from_immutable_ledger_readback(bytes, digest)
                    .map_err(|error| RuntimeDependencyError::new(error.code()))
            })
            .transpose()
    }

    fn projection_commit_receipt_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        projection: &VerifiedAuthorityResultProjection,
    ) -> Result<
        Option<crate::primitive_evidence_authority_ledger::DurableProjectionCommitReceipt>,
        RuntimeDependencyError,
    > {
        self.reopen_projection_receipt(ticket, run_binding_digest, projection)
            .map(Some)
    }

    fn burn(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        reason: RuntimeTerminalKind,
    ) -> Result<(), RuntimeDependencyError> {
        self.verify_run_binding(ticket, run_binding_digest)?;
        self.ledger_mut()?
            .burn_with_reason(ticket.as_str(), ledger_burn_reason(reason))
            .map_err(runtime_ledger_error)
    }

    fn burn_recovered(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
    ) -> Result<(), RuntimeDependencyError> {
        self.ledger_mut()?
            .burn_recovered(ticket.as_str(), &hex_lower(run_binding_digest))
            .map_err(runtime_ledger_error)
    }

    fn burn_recovered_with_reason(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        reason: RuntimeTerminalKind,
        proof: &RecoveredBurnProof,
    ) -> Result<(), RuntimeDependencyError> {
        self.verify_run_binding(ticket, run_binding_digest)?;
        self.ledger_mut()?
            .burn_recovered_with_reason(
                ticket.as_str(),
                &hex_lower(run_binding_digest),
                ledger_burn_reason(reason),
                proof,
            )
            .map_err(runtime_ledger_error)
    }

    fn terminal_reason(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<RuntimeTerminalKind>, RuntimeDependencyError> {
        self.ledger()?
            .burn_reason(ticket.as_str())
            .map(|reason| reason.map(runtime_terminal_kind))
            .map_err(runtime_ledger_error)
    }
}

#[cfg(windows)]
type SharedAuthenticatedFinalCommitBoundary = Arc<Mutex<AuthenticatedFinalCommitBoundary>>;

/// Service-only signing facade over the one authenticated FinalCommit
/// boundary. Cloning this value clones only the synchronized reference; the
/// machine-key handle remains unique inside the boundary and is never
/// exported or reopened by name.
#[cfg(windows)]
#[derive(Clone)]
pub(crate) struct ProductionAuthoritySigner {
    boundary: SharedAuthenticatedFinalCommitBoundary,
    signer_key_id: [u8; 32],
}

#[cfg(windows)]
impl ProductionAuthoritySigner {
    fn new(
        boundary: SharedAuthenticatedFinalCommitBoundary,
        authority_generation_digest: [u8; 32],
        signer_key_id: [u8; 32],
    ) -> Result<Self, RuntimeDependencyError> {
        if authority_generation_digest.iter().all(|byte| *byte == 0)
            || signer_key_id.iter().all(|byte| *byte == 0)
        {
            return Err(RuntimeDependencyError::new(
                "authority_production_signer_identity_invalid",
            ));
        }
        {
            let mut authenticated = boundary.lock().map_err(|_| {
                RuntimeDependencyError::new("authority_final_commit_boundary_lock_failed")
            })?;
            let binding = authenticated
                .current_policy_binding()
                .map_err(|error| RuntimeDependencyError::new(error.code()))?;
            if binding.generation() != &authority_generation_digest
                || binding.signer_key_id() != &signer_key_id
            {
                return Err(RuntimeDependencyError::new(
                    "authority_production_signer_identity_mismatch",
                ));
            }
        }
        Ok(Self {
            boundary,
            signer_key_id,
        })
    }

    fn sign_digest(&self, digest: &[u8; 32]) -> Result<[u8; 64], RuntimeDependencyError> {
        let mut boundary = self.boundary.lock().map_err(|_| {
            RuntimeDependencyError::new("authority_final_commit_boundary_lock_failed")
        })?;
        boundary
            .sign_current_digest(digest)
            .map_err(|error| RuntimeDependencyError::new(error.code()))
    }

    fn verify_digest(
        &self,
        digest: &[u8; 32],
        signature: &[u8; 64],
    ) -> Result<(), RuntimeDependencyError> {
        let mut boundary = self.boundary.lock().map_err(|_| {
            RuntimeDependencyError::new("authority_final_commit_boundary_lock_failed")
        })?;
        boundary
            .verify_current_digest_signature(digest, signature)
            .map_err(|error| RuntimeDependencyError::new(error.code()))
    }
}

#[cfg(windows)]
impl ProtectedEvidenceBundleSigner for ProductionAuthoritySigner {
    fn signer_key_id(&self) -> [u8; 32] {
        self.signer_key_id
    }

    fn sign_protected_bundle(
        &mut self,
        digest: ProtectedBundleSigningDigest,
    ) -> Result<[u8; 64], ProtectedEvidenceBundleError> {
        self.sign_digest(digest.as_bytes())
            .map_err(|_| ProtectedEvidenceBundleError::new("protected_bundle_signer_failed"))
    }
}

#[cfg(windows)]
impl AuthorityGenerationAttestationSigner for ProductionAuthoritySigner {
    fn signer_key_id(&self) -> [u8; 32] {
        self.signer_key_id
    }

    fn sign_attestation_digest(&mut self, digest: &[u8; 32]) -> Result<[u8; 64], ContractError> {
        self.sign_digest(digest)
            .map_err(|_| ContractError::new("authority_generation_attestation_sign_failed"))
    }
}

#[cfg(windows)]
impl AuthorityProjectionCommitReceiptVerifier for ProductionAuthoritySigner {
    fn projection_commit_receipt_signer_key_id(&self) -> [u8; 32] {
        self.signer_key_id
    }

    fn verify_projection_commit_receipt_signature(
        &mut self,
        receipt_digest: &[u8; 32],
        signature: &[u8; 64],
    ) -> Result<(), ContractError> {
        self.verify_digest(receipt_digest, signature)
            .map_err(|_| ContractError::new("authority_projection_commit_signature_invalid"))
    }
}

#[cfg(windows)]
impl AuthorityProjectionCommitReceiptSigner for ProductionAuthoritySigner {
    fn sign_projection_commit_receipt_digest(
        &mut self,
        receipt_digest: &[u8; 32],
    ) -> Result<[u8; 64], ContractError> {
        self.sign_digest(receipt_digest)
            .map_err(|_| ContractError::new("authority_projection_commit_sign_failed"))
    }
}

#[cfg(windows)]
pub(crate) struct ProductionGenerationBindingVerifier {
    boundary: SharedAuthenticatedFinalCommitBoundary,
    expected: AuthorityGenerationBinding,
}

#[cfg(windows)]
impl ProductionGenerationBindingVerifier {
    fn new(
        boundary: SharedAuthenticatedFinalCommitBoundary,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<Self, RuntimeDependencyError> {
        let expected = {
            let mut boundary = boundary.lock().map_err(|_| {
                RuntimeDependencyError::new("authority_final_commit_boundary_lock_failed")
            })?;
            let readback = boundary
                .current_generation_binding_readback()
                .map_err(|error| RuntimeDependencyError::new(error.code()))?;
            authority_generation_binding_from_readback(&readback)?
        };
        if expected.current_generation() != identity.authority_generation_digest()
            || expected.signer_key_id() != identity.signer_key_id()
        {
            return Err(RuntimeDependencyError::new(
                "authority_generation_runtime_identity_mismatch",
            ));
        }
        Ok(Self { boundary, expected })
    }

    fn verify_current(&mut self) -> Result<AuthorityGenerationBinding, RuntimeDependencyError> {
        let current = {
            let mut boundary = self.boundary.lock().map_err(|_| {
                RuntimeDependencyError::new("authority_final_commit_boundary_lock_failed")
            })?;
            let readback = boundary
                .current_generation_binding_readback()
                .map_err(|error| RuntimeDependencyError::new(error.code()))?;
            authority_generation_binding_from_readback(&readback)?
        };
        if current != self.expected {
            return Err(RuntimeDependencyError::new(
                "authority_generation_binding_changed",
            ));
        }
        Ok(current)
    }
}

#[cfg(windows)]
impl AuthorityGenerationBindingVerifier for ProductionGenerationBindingVerifier {
    fn verify_current_generation_binding(
        &mut self,
    ) -> Result<AuthorityGenerationBinding, ContractError> {
        self.verify_current()
            .map_err(|error| ContractError::new(error.code()))
    }
}

#[cfg(windows)]
fn authority_generation_binding_from_readback(
    readback: &AuthenticatedGenerationBindingReadback,
) -> Result<AuthorityGenerationBinding, RuntimeDependencyError> {
    AuthorityGenerationBinding::new(
        *readback.current_generation(),
        *readback.service_executable_sha256(),
        *readback.service_executable_path_sha256(),
        *readback.service_executable_file_identity_sha256(),
        readback.service_process_id(),
        readback.service_process_started_at(),
        *readback.protected_manifest_readback_sha256(),
        *readback.protected_key_readback_sha256(),
        *readback.signer_key_id(),
        *readback.protected_ledger_readback_sha256(),
        *readback.scm_readback_sha256(),
        *readback.final_commit_receipt_sha256(),
    )
    .map_err(|error| RuntimeDependencyError::new(error.code()))
}

#[cfg(windows)]
struct RevalidatingInstalledBoundary {
    expected_receipt_sha256: [u8; 32],
    identity: AuthorityRuntimeIdentity,
    boundary: SharedAuthenticatedFinalCommitBoundary,
}

#[cfg(windows)]
impl InstalledBoundaryVerifier for RevalidatingInstalledBoundary {
    fn verify_installed_boundary(
        &mut self,
    ) -> Result<AuthorityRuntimeIdentity, RuntimeDependencyError> {
        let mut boundary = self.boundary.lock().map_err(|_| {
            RuntimeDependencyError::new("authority_final_commit_boundary_lock_failed")
        })?;
        let current = boundary
            .current_policy_binding()
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        if current.generation() != self.identity.authority_generation_digest()
            || current.signer_key_id() != self.identity.signer_key_id()
            || current.protected_manifest_sha256() != self.identity.protected_manifest_digest()
            || current.installed_layout_sha256() != self.identity.installed_layout_digest()
            || current.service_binary_sha256() != self.identity.service_binary_digest()
            || boundary.receipt_sha256() != self.expected_receipt_sha256
        {
            return Err(RuntimeDependencyError::new(
                "authority_runtime_bootstrap_binding_changed",
            ));
        }
        Ok(self.identity.clone())
    }
}

#[cfg(windows)]
struct ProductionNativeSupervisor {
    background: BackgroundNativeSupervisor<WindowsNativeSupervisorApi>,
    runs: Option<ProductionRunAdmission>,
    policy_source: Option<ProductionPolicySource>,
}

#[cfg(windows)]
struct ProductionPolicySource {
    boundary: SharedAuthenticatedFinalCommitBoundary,
    authority_processes: Option<HeldAuthorityProcessHandles>,
}

#[cfg(windows)]
impl ProductionPolicySource {
    fn new(
        boundary: SharedAuthenticatedFinalCommitBoundary,
    ) -> Result<Self, RuntimeDependencyError> {
        let authority_processes = HeldAuthorityProcessHandles::open_current_process_tree()
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        Ok(Self {
            boundary,
            authority_processes: Some(authority_processes),
        })
    }

    fn readiness(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<VerifiedReadinessProof, RuntimeDependencyError> {
        let mut boundary = self.boundary.lock().map_err(|_| {
            RuntimeDependencyError::new("authority_final_commit_boundary_lock_failed")
        })?;
        let readback = boundary
            .current_generation_binding_readback()
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        let final_commit = boundary
            .current_policy_binding()
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        self.authority_processes
            .as_ref()
            .ok_or_else(|| RuntimeDependencyError::new("authority_process_handles_missing"))?
            .readback(final_commit)
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        build_verified_readiness(final_commit, identity, &readback)
            .map_err(|error| RuntimeDependencyError::new(error.code()))
    }

    fn prepare(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
        ticket: &RuntimeTicketRef,
        service_instance_digest: &[u8; 32],
        files: [&std::fs::File; FIXED_MODEL_PART_HANDLE_COUNT],
    ) -> Result<PreparedRun, RuntimeDependencyError> {
        let authority_processes = self
            .authority_processes
            .as_ref()
            .ok_or_else(|| RuntimeDependencyError::new("authority_process_handles_missing"))?;
        let mut boundary = self.boundary.lock().map_err(|_| {
            RuntimeDependencyError::new("authority_final_commit_boundary_lock_failed")
        })?;
        let generation_readback = boundary
            .current_generation_binding_readback()
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        let package = ProtectedRuntimeSourceReadback::read_from_capability(
            boundary
                .runtime_source_capability()
                .map_err(|error| RuntimeDependencyError::new(error.code()))?,
        )
        .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        let final_commit = boundary
            .current_policy_binding()
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        let readiness = build_verified_readiness(final_commit, identity, &generation_readback)
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        if readiness.service_instance_digest() != service_instance_digest {
            return Err(RuntimeDependencyError::new(
                "authority_policy_readiness_instance_changed",
            ));
        }
        let [driver, desktop, backend, unity, bridge_launcher, bridge_listener, fixture_contract, fixture_baseline] =
            files;
        let scenario = FixedModelPartHeldReadback::read_from_held_handles(
            &package,
            FixedModelPartHandles::new(
                driver,
                desktop,
                backend,
                unity,
                bridge_launcher,
                bridge_listener,
                fixture_contract,
                fixture_baseline,
            ),
        )
        .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        let processes = authority_processes
            .readback(final_commit)
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        let job_security = verified_job_security_binding(identity)
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        prepare_model_part_run(
            final_commit,
            identity,
            &readiness,
            ticket,
            &package,
            &scenario,
            &processes,
            &job_security,
        )
        .map_err(|error| RuntimeDependencyError::new(error.code()))
    }
}

#[cfg(windows)]
impl ProductionNativeSupervisor {
    fn new(
        boundary: SharedAuthenticatedFinalCommitBoundary,
        runs: ProductionRunAdmission,
        background: BackgroundNativeSupervisor<WindowsNativeSupervisorApi>,
    ) -> Result<Self, RuntimeDependencyError> {
        Ok(Self {
            background,
            runs: Some(runs),
            policy_source: Some(ProductionPolicySource::new(boundary)?),
        })
    }

    #[cfg(test)]
    fn blocked_for_test(
        background: BackgroundNativeSupervisor<WindowsNativeSupervisorApi>,
    ) -> Self {
        Self {
            background,
            runs: None,
            policy_source: None,
        }
    }

    fn blocked<T>() -> Result<T, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(PRODUCTION_SUPERVISOR_BLOCKER))
    }
}

#[cfg(windows)]
impl FixedModelPartSupervisor for ProductionNativeSupervisor {
    fn contain_all_orphans(
        &mut self,
        _identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError> {
        Self::blocked()
    }

    fn readiness(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<VerifiedReadinessProof, RuntimeDependencyError> {
        self.policy_source
            .as_mut()
            .ok_or_else(|| RuntimeDependencyError::new(PRODUCTION_SUPERVISOR_BLOCKER))?
            .readiness(identity)
    }

    fn self_test(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<VerifiedReadinessProof, RuntimeDependencyError> {
        self.readiness(identity)
    }

    fn prepare(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
        ticket: &RuntimeTicketRef,
        service_instance_digest: &[u8; 32],
    ) -> Result<PreparedRun, RuntimeDependencyError> {
        let policy_source = self
            .policy_source
            .as_mut()
            .ok_or_else(|| RuntimeDependencyError::new(PRODUCTION_SUPERVISOR_BLOCKER))?;
        self.runs
            .as_ref()
            .ok_or_else(|| RuntimeDependencyError::new(PRODUCTION_SUPERVISOR_BLOCKER))?
            .prepare_with(identity, ticket, |files| {
                policy_source.prepare(identity, ticket, service_instance_digest, files)
            })
    }

    fn start(
        &mut self,
        prepared: PreparedRun,
        context: &RuntimeRunContext,
    ) -> Result<SupervisorStart, RuntimeDependencyError> {
        self.runs
            .as_ref()
            .ok_or_else(|| RuntimeDependencyError::new(PRODUCTION_SUPERVISOR_BLOCKER))?
            .queue_start(context, prepared)?;
        // The authenticated contract commits and transfers this pending run to
        // the background worker before it returns RunStarted to the peer.
        Ok(SupervisorStart::Starting)
    }

    fn poll(
        &mut self,
        context: &RuntimeRunContext,
    ) -> Result<SupervisorPoll, RuntimeDependencyError> {
        let key = BackgroundRunKey::from_persisted(context.prepared_receipt());
        self.background
            .poll(key, context.armed_receipt())
            .map(|poll| match poll {
                BackgroundNativePoll::Starting => SupervisorPoll::Starting,
                BackgroundNativePoll::Armed(receipt) => SupervisorPoll::Armed(receipt),
                BackgroundNativePoll::Running => SupervisorPoll::Running,
                BackgroundNativePoll::Terminal(terminal) => SupervisorPoll::Terminal(terminal),
            })
            .map_err(|error| RuntimeDependencyError::new(error.code()))
    }

    fn cancel(
        &mut self,
        context: &RuntimeRunContext,
    ) -> Result<SupervisorCancelAcknowledgement, RuntimeDependencyError> {
        let key = BackgroundRunKey::from_persisted(context.prepared_receipt());
        self.background
            .request_cancel(key, context.armed_receipt())
            .map(|acknowledgement| match acknowledgement {
                BackgroundCancelAcknowledgement::Recorded(kind) => {
                    SupervisorCancelAcknowledgement::Recorded(runtime_native_termination_kind(kind))
                }
                BackgroundCancelAcknowledgement::AlreadyRecorded(kind) => {
                    SupervisorCancelAcknowledgement::AlreadyRecorded(
                        runtime_native_termination_kind(kind),
                    )
                }
                BackgroundCancelAcknowledgement::AlreadyTerminal => {
                    SupervisorCancelAcknowledgement::AlreadyTerminal
                }
                BackgroundCancelAcknowledgement::Uncertain => {
                    SupervisorCancelAcknowledgement::Uncertain
                }
            })
            .map_err(|error| RuntimeDependencyError::new(error.code()))
    }

    fn abort_and_wait_cleanup(
        &mut self,
        context: &RuntimeRunContext,
    ) -> Result<BurnedRunProof, RuntimeDependencyError> {
        let key = BackgroundRunKey::from_persisted(context.prepared_receipt());
        self.background
            .abort_and_wait_cleanup(key, "authority_runtime_requested_abort")
            .map(|proof| proof.into_terminal())
            .map_err(|error| RuntimeDependencyError::new(error.code()))
    }

    fn recover_and_wait_cleanup(
        &mut self,
        context: &RuntimeRecoveryContext,
    ) -> Result<SupervisorRecovery, RuntimeDependencyError> {
        self.background
            .recover_and_wait_cleanup(
                context.prepared_receipt().clone(),
                context.armed_receipt().cloned(),
                context.canonical_policy_snapshot().to_vec(),
            )
            .map(|terminal| match terminal {
                ValidatedNativeTerminalRun::Completed(proof) => {
                    SupervisorRecovery::Completed(proof)
                }
                ValidatedNativeTerminalRun::Burned(proof) => SupervisorRecovery::Burned(proof),
            })
            .map_err(|error| RuntimeDependencyError::new(error.code()))
    }

    fn shutdown_and_wait(&mut self) -> Result<(), RuntimeDependencyError> {
        self.background
            .shutdown_and_wait()
            .map_err(|error| RuntimeDependencyError::new(error.code()))
    }
}

#[cfg(windows)]
fn runtime_native_termination_kind(
    kind: crate::primitive_evidence_authority_supervisor::native_windows::NativeTerminationKind,
) -> RuntimeTerminalKind {
    match kind {
        crate::primitive_evidence_authority_supervisor::native_windows::NativeTerminationKind::Cancelled => {
            RuntimeTerminalKind::Cancelled
        }
        crate::primitive_evidence_authority_supervisor::native_windows::NativeTerminationKind::TimedOut => {
            RuntimeTerminalKind::TimedOut
        }
    }
}

#[cfg(windows)]
pub(crate) struct ProductionRuntimeComposition {
    runtime: AuthorityRuntime,
    binding_verifier: ProductionGenerationBindingVerifier,
    signer: ProductionAuthoritySigner,
    controller_policy_source: ProductionControllerPolicySource,
    run_admission: ProductionRunAdmission,
}

#[cfg(windows)]
#[derive(Clone)]
pub(crate) struct ProductionControllerPolicySource {
    boundary: SharedAuthenticatedFinalCommitBoundary,
}

#[cfg(windows)]
impl ProductionControllerPolicySource {
    pub(crate) fn current_policy_with_binding(
        &self,
    ) -> Result<(InstalledControllerPolicy, [u8; 32]), RuntimeDependencyError> {
        let source = self
            .boundary
            .lock()
            .map_err(|_| {
                RuntimeDependencyError::new("authority_final_commit_boundary_lock_failed")
            })?
            .current_controller_source_readback()
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        let binding = *source.source_binding_sha256();
        InstalledControllerPolicy::from_authenticated_source(source)
            .map(|policy| (policy, binding))
            .map_err(|error| RuntimeDependencyError::new(error.code()))
    }

    pub(crate) fn require_current_binding(
        &self,
        expected: &[u8; 32],
    ) -> Result<(), RuntimeDependencyError> {
        let source = self
            .boundary
            .lock()
            .map_err(|_| {
                RuntimeDependencyError::new("authority_final_commit_boundary_lock_failed")
            })?
            .current_controller_source_readback()
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        if source.source_binding_sha256() != expected {
            return Err(RuntimeDependencyError::new(
                "authority_controller_source_binding_changed",
            ));
        }
        Ok(())
    }
}

#[cfg(windows)]
impl ProductionRuntimeComposition {
    pub(crate) fn runtime(&self) -> &AuthorityRuntime {
        &self.runtime
    }

    pub(crate) fn shutdown_and_wait(&self) -> Result<(), RuntimeDependencyError> {
        self.runtime
            .shutdown_and_wait()
            .map_err(|error| RuntimeDependencyError::new(error.code()))
    }

    pub(crate) fn verify_service_trust(&mut self) -> Result<(), RuntimeDependencyError> {
        let binding = self.binding_verifier.verify_current()?;
        if binding.signer_key_id() != &self.signer.signer_key_id {
            return Err(RuntimeDependencyError::new(
                "authority_service_signer_binding_mismatch",
            ));
        }
        Ok(())
    }

    pub(crate) fn into_service_parts(
        self,
    ) -> (
        AuthorityRuntime,
        ProductionGenerationBindingVerifier,
        ProductionAuthoritySigner,
        ProductionControllerPolicySource,
        ProductionRunAdmission,
    ) {
        (
            self.runtime,
            self.binding_verifier,
            self.signer,
            self.controller_policy_source,
            self.run_admission,
        )
    }
}

#[cfg(windows)]
pub(crate) fn compose_production_runtime(
    bootstrap: AuthenticatedFinalCommitBootstrap,
) -> Result<ProductionRuntimeComposition, RuntimeDependencyError> {
    let identity = runtime_identity_from_bootstrap(&bootstrap)?;
    let ledger_identity =
        LedgerIdentity::from_digests(*bootstrap.generation(), *bootstrap.signer_key_id())
            .map_err(runtime_ledger_error)?;
    if ledger_identity.canonical_digest() != *bootstrap.ledger_identity() {
        return Err(RuntimeDependencyError::new(
            "authority_runtime_ledger_identity_mismatch",
        ));
    }
    let expected_receipt_sha256 = bootstrap.receipt_sha256();
    let (authenticated_boundary, authenticated_ledger) = bootstrap.into_runtime_parts();
    let authenticated_boundary = Arc::new(Mutex::new(authenticated_boundary));
    let controller_policy_source = ProductionControllerPolicySource {
        boundary: Arc::clone(&authenticated_boundary),
    };
    // One admission slot is shared by the authenticated controller boundary
    // and the production supervisor. It cannot be recreated per request.
    let background = BackgroundNativeSupervisor::new(WindowsNativeSupervisorApi)
        .map_err(|error| RuntimeDependencyError::new(error.code()))?;
    let (run_generation, run_final_commit_receipt_sha256) = {
        let mut boundary = authenticated_boundary.lock().map_err(|_| {
            RuntimeDependencyError::new("authority_final_commit_boundary_lock_failed")
        })?;
        let binding = boundary
            .current_policy_binding()
            .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        (
            *binding.generation(),
            *binding.final_commit_receipt_sha256(),
        )
    };
    let run_admission = ProductionRunAdmission::new(
        Arc::clone(&authenticated_boundary),
        run_generation,
        run_final_commit_receipt_sha256,
        background.start_sink(),
    )?;
    let signer = ProductionAuthoritySigner::new(
        Arc::clone(&authenticated_boundary),
        *identity.authority_generation_digest(),
        *identity.signer_key_id(),
    )?;
    let binding_verifier =
        ProductionGenerationBindingVerifier::new(Arc::clone(&authenticated_boundary), &identity)?;
    let ledger_path = authenticated_ledger
        .authenticated_runtime_path()
        .map_err(runtime_ledger_error)?;
    let projection_producer = ProtectedEvidenceBundleProducer::new(
        *identity.authority_generation_digest(),
        *identity.protected_manifest_digest(),
        *identity.installed_layout_digest(),
        *identity.service_binary_digest(),
        signer.clone(),
    );
    let boundary = RevalidatingInstalledBoundary {
        expected_receipt_sha256,
        identity,
        boundary: Arc::clone(&authenticated_boundary),
    };
    let ledger = DurableRuntimeLedger::from_published_adoption(
        ledger_path,
        ledger_identity,
        authenticated_ledger,
    )?;
    ledger.require_protected_blob_authority()?;
    Ok(ProductionRuntimeComposition {
        runtime: AuthorityRuntime::start_with_projection_producer(
            boundary,
            ledger,
            ProductionNativeSupervisor::new(
                Arc::clone(&authenticated_boundary),
                run_admission.clone(),
                background,
            )?,
            projection_producer,
        ),
        binding_verifier,
        signer,
        controller_policy_source,
        run_admission,
    })
}

fn runtime_identity_from_bootstrap(
    bootstrap: &AuthenticatedFinalCommitBootstrap,
) -> Result<AuthorityRuntimeIdentity, RuntimeDependencyError> {
    AuthorityRuntimeIdentity::new(
        *bootstrap.generation(),
        *bootstrap.signer_key_id(),
        *bootstrap.protected_manifest_sha256(),
        *bootstrap.installed_layout_sha256(),
        *bootstrap.service_binary_sha256(),
    )
    .map_err(|error| RuntimeDependencyError::new(error.code()))
}

fn runtime_ledger_error(
    error: crate::primitive_evidence_authority_ledger::LedgerError,
) -> RuntimeDependencyError {
    RuntimeDependencyError::new(error.code())
}

fn protected_bundle_error(error: ProtectedEvidenceBundleError) -> RuntimeDependencyError {
    RuntimeDependencyError::new(error.code())
}

fn runtime_ticket_state(state: TicketState) -> RuntimeTicketState {
    match state {
        TicketState::Issued => RuntimeTicketState::Issued,
        TicketState::Consumed => RuntimeTicketState::Consumed,
        TicketState::ResultPendingProjection => RuntimeTicketState::ResultPendingProjection,
        TicketState::Result => RuntimeTicketState::Result,
        TicketState::Burned => RuntimeTicketState::Burned,
    }
}

fn ledger_burn_reason(reason: RuntimeTerminalKind) -> TicketBurnReason {
    match reason {
        RuntimeTerminalKind::Cancelled => TicketBurnReason::Cancelled,
        RuntimeTerminalKind::TimedOut => TicketBurnReason::TimedOut,
        RuntimeTerminalKind::Failed => TicketBurnReason::Failed,
        RuntimeTerminalKind::RestartRecovery => TicketBurnReason::RestartRecovery,
    }
}

fn runtime_terminal_kind(reason: TicketBurnReason) -> RuntimeTerminalKind {
    match reason {
        TicketBurnReason::Cancelled => RuntimeTerminalKind::Cancelled,
        TicketBurnReason::TimedOut => RuntimeTerminalKind::TimedOut,
        TicketBurnReason::Failed => RuntimeTerminalKind::Failed,
        TicketBurnReason::RestartRecovery => RuntimeTerminalKind::RestartRecovery,
    }
}

fn decode_digest(value: &str) -> Result<[u8; 32], RuntimeDependencyError> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(RuntimeDependencyError::new(
            "authority_runtime_digest_invalid",
        ));
    }
    let mut output = [0u8; 32];
    for (index, slot) in output.iter_mut().enumerate() {
        let offset = index * 2;
        *slot = u8::from_str_radix(&value[offset..offset + 2], 16)
            .map_err(|_| RuntimeDependencyError::new("authority_runtime_digest_invalid"))?;
    }
    if output.iter().all(|byte| *byte == 0) {
        return Err(RuntimeDependencyError::new(
            "authority_runtime_digest_invalid",
        ));
    }
    Ok(output)
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

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};
    use std::{
        cell::Cell,
        cell::RefCell,
        collections::VecDeque,
        fs,
        sync::atomic::{AtomicU64, Ordering},
    };

    static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(1);

    struct TestDirectory(PathBuf);

    impl TestDirectory {
        fn new() -> Self {
            let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "vrcforge-authority-service-runtime-{}-{sequence}",
                std::process::id()
            ));
            fs::create_dir(&path).expect("create isolated service runtime test directory");
            Self(path)
        }

        fn ledger_path(&self) -> PathBuf {
            self.0.join("ledger.bin")
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn runtime_identity(generation: u8, signer: u8) -> AuthorityRuntimeIdentity {
        AuthorityRuntimeIdentity::new(
            [generation; 32],
            [signer; 32],
            [0x31; 32],
            [0x41; 32],
            [0x51; 32],
        )
        .expect("valid runtime identity")
    }

    fn protected_prepared_receipt(source: u8) -> Vec<u8> {
        const ENCODED_LENGTH: usize = 8 + 14 * 32 + 3 * 8;
        const PROTECTED_SOURCE_OFFSET: usize = 8 + 12 * 32;
        let mut value = vec![source.max(1); ENCODED_LENGTH];
        value[..8].copy_from_slice(b"VRCPRP04");
        value[PROTECTED_SOURCE_OFFSET..PROTECTED_SOURCE_OFFSET + 32]
            .copy_from_slice(&[source.max(1); 32]);
        value
    }

    fn verified_runtime_result(
        ticket_digest: [u8; 32],
        run_binding_digest: [u8; 32],
        prepared: &[u8],
        armed: &[u8],
        policy: &[u8],
    ) -> ServiceOwnedVerifiedRuntimeResult {
        let cleanup_digest = [0xc1; 32];
        let finalization = serde_json::to_vec(&serde_json::json!({
            "attestation": {
                "startedAt": "2026-07-31T00:00:01.000000Z"
            },
            "schema": "vrcforge.test_finalization.v1"
        }))
        .unwrap();
        let origin = serde_json::to_vec(&serde_json::json!({
            "cleanupDigest": hex_lower(&cleanup_digest),
            "schema": "vrcforge.primitive_basis_live_origin.v1",
            "signedAt": "2026-07-31T00:00:02.000000Z",
            "ticket": {
                "issuedAt": "2026-07-31T00:00:00.000000Z",
                "runId": "durable-terminal-test"
            },
            "ticketDigest": hex_lower(&ticket_digest)
        }))
        .unwrap();
        let recovery_bundle_digest =
            crate::primitive_evidence_authority_ledger::compute_recovery_bundle_digest(
                &hex_lower(&ticket_digest),
                &hex_lower(&run_binding_digest),
                prepared,
                policy,
            )
            .ok()
            .and_then(|value| decode_digest(&value).ok())
            .unwrap();
        ServiceOwnedVerifiedRuntimeResult::from_verified_terminal(
            finalization,
            origin,
            ticket_digest,
            run_binding_digest,
            cleanup_digest,
            Sha256::digest(prepared).into(),
            Sha256::digest(armed).into(),
            Sha256::digest(policy).into(),
            recovery_bundle_digest,
        )
        .unwrap()
    }

    #[test]
    fn clean_disconnect_recreates_the_connection_until_stop() {
        let gate = AuthorityConnectionGate::default();
        let attempts = Cell::new(0usize);
        let exits = RefCell::new(VecDeque::from([
            Ok(ProtocolExit::PeerClosed),
            Ok(ProtocolExit::PeerClosed),
            Ok(ProtocolExit::StopRequested),
        ]));

        let code = run_fixed_pipe_loop(
            &gate,
            || {
                attempts.set(attempts.get() + 1);
                exits
                    .borrow_mut()
                    .pop_front()
                    .expect("bounded connection attempt")
            },
            || false,
        );

        assert_eq!(code, SERVICE_EXIT_SUCCESS);
        assert_eq!(attempts.get(), 3);
        assert!(gate.is_stop_requested());
        assert!(!gate.has_failed());
    }

    #[test]
    fn stop_before_accept_never_creates_a_pipe_attempt() {
        let gate = AuthorityConnectionGate::default();
        let attempts = Cell::new(0usize);

        let code = run_fixed_pipe_loop(
            &gate,
            || {
                attempts.set(attempts.get() + 1);
                Ok(ProtocolExit::PeerClosed)
            },
            || true,
        );

        assert_eq!(code, SERVICE_EXIT_SUCCESS);
        assert_eq!(attempts.get(), 0);
        assert!(gate.is_stop_requested());
        assert!(!gate.has_failed());
    }

    #[test]
    fn externally_cancelled_accept_is_a_clean_stop() {
        let gate = AuthorityConnectionGate::default();
        let stop = Cell::new(false);

        let code = run_fixed_pipe_loop(
            &gate,
            || {
                stop.set(true);
                Err("authority_pipe_accept_failed".to_string())
            },
            || stop.get(),
        );

        assert_eq!(code, SERVICE_EXIT_SUCCESS);
        assert!(gate.is_stop_requested());
        assert!(!gate.has_failed());
    }

    #[test]
    fn fatal_protocol_exit_latches_process_failure() {
        let gate = AuthorityConnectionGate::default();
        let code = run_fixed_pipe_loop(&gate, || Ok(ProtocolExit::Fatal), || false);

        assert_eq!(code, SERVICE_EXIT_FAILURE);
        assert!(gate.is_stop_requested());
        assert!(gate.has_failed());
    }

    #[test]
    fn transport_failure_latches_process_failure() {
        let gate = AuthorityConnectionGate::default();
        let code = run_fixed_pipe_loop(
            &gate,
            || Err("authority_pipe_frame_invalid".to_string()),
            || false,
        );

        assert_eq!(code, SERVICE_EXIT_FAILURE);
        assert!(gate.is_stop_requested());
        assert!(gate.has_failed());
    }

    #[test]
    fn prelatched_failure_is_never_downgraded_to_clean_stop() {
        let gate = AuthorityConnectionGate::default();
        gate.latch_failure();
        let attempts = Cell::new(0usize);

        let code = run_fixed_pipe_loop(
            &gate,
            || {
                attempts.set(attempts.get() + 1);
                Ok(ProtocolExit::PeerClosed)
            },
            || true,
        );

        assert_eq!(code, SERVICE_EXIT_FAILURE);
        assert_eq!(attempts.get(), 0);
    }

    #[test]
    fn production_pipe_admission_stays_closed_without_a_launch_receipt() {
        assert_eq!(
            require_production_pipe_admission().unwrap_err().code(),
            PRODUCTION_PIPE_ADMISSION_BLOCKER
        );
    }

    #[test]
    fn protected_blob_restart_join_uses_only_pre_replay_bootstrap_adoption() {
        let bootstrap = include_str!("primitive_evidence_authority_install/bootstrap_windows.rs");
        let ledger = include_str!("primitive_evidence_authority_ledger.rs");
        let blob = include_str!("primitive_evidence_authority_blob.rs");
        let runtime = include_str!("primitive_evidence_authority_service_runtime.rs");
        let production_runtime = runtime
            .split("#[cfg(test)]\nmod tests")
            .next()
            .expect("production runtime implementation boundary");
        assert!(bootstrap.contains(
            "AuthorityLedger::adopt_verified_published_pair(pair, native_ledger_identity)"
        ));
        assert!(!bootstrap.contains("adopt_verified_published_pair_with_blob_authority"));
        assert!(
            ledger.contains("#[cfg(test)]\n    pub(crate) fn open_existing_with_blob_authority")
        );
        assert!(
            ledger.contains("#[cfg(test)]\n    pub(crate) fn provision_new_with_blob_authority")
        );
        assert!(blob.contains("    fn from_authenticated_held_root("));
        assert!(!blob.contains("pub(crate) fn from_authenticated_held_root("));
        assert!(!production_runtime.contains("AuthenticatedProtectedBlobPreReplay"));
        assert!(!production_runtime.contains("compose_production_runtime_with_optional_bindings"));
        assert!(production_runtime.contains("DurableRuntimeLedger::from_published_adoption("));
        assert!(production_runtime.contains("ledger.require_protected_blob_authority()?;"));
    }

    #[test]
    fn production_runtime_ledger_has_no_path_only_reopen_fallback() {
        let source = include_str!("primitive_evidence_authority_service_runtime.rs");
        let implementation = source
            .split("#[cfg(test)]\nmod tests")
            .next()
            .expect("production implementation boundary");
        let struct_body = implementation
            .split("pub(crate) struct DurableRuntimeLedger {")
            .nth(1)
            .and_then(|value| value.split("}\n\nimpl DurableRuntimeLedger").next())
            .expect("durable runtime ledger struct");
        assert!(struct_body.contains("ledger: AuthorityLedger"));
        assert!(!struct_body.contains("Option<AuthorityLedger>"));

        let path_only_constructor = implementation
            .split("#[cfg(test)]\n    pub(crate) fn new(")
            .nth(1)
            .and_then(|value| {
                value
                    .split("    #[cfg(test)]\n    fn from_test_ledger(")
                    .next()
            })
            .expect("test-only path constructor");
        assert!(path_only_constructor.contains("AuthorityLedger::open_existing"));
        assert_eq!(
            implementation
                .matches("AuthorityLedger::open_existing")
                .count(),
            1,
            "the only path-based open must remain inside the test-only constructor"
        );

        let runtime_ledger_impl = implementation
            .split("impl RuntimeTicketLedger for DurableRuntimeLedger {")
            .nth(1)
            .expect("runtime ledger implementation");
        let open_existing = runtime_ledger_impl
            .split("fn open_existing(")
            .nth(1)
            .and_then(|value| value.split("    fn active_tickets(").next())
            .expect("runtime open_existing implementation");
        assert!(open_existing.contains("verify_current_identity"));
        assert!(!open_existing.contains("AuthorityLedger::open_existing"));
        let published_constructor = implementation
            .split("pub(crate) fn from_published_adoption(")
            .nth(1)
            .and_then(|value| {
                value
                    .split("    /// Empty/legacy-ledger test seam only.")
                    .next()
            })
            .expect("published runtime constructor");
        assert!(published_constructor.contains("AuthenticatedPublishedAuthorityLedger"));
        assert!(published_constructor.contains("consume_for_runtime"));
        assert!(!published_constructor.contains("AuthorityLedger::open_existing"));
    }

    #[test]
    fn production_ledger_gate_rejects_the_legacy_inline_fallback() {
        let directory = TestDirectory::new();
        let path = directory.ledger_path();
        let identity = LedgerIdentity::from_digests([0x10; 32], [0x20; 32]).unwrap();
        let ledger = AuthorityLedger::provision_new(&path, identity.clone()).unwrap();
        let adapter = DurableRuntimeLedger::from_test_ledger(path, identity, ledger)
            .expect("adopt legacy ledger for gate test");
        assert_eq!(
            adapter
                .require_protected_blob_authority()
                .unwrap_err()
                .code(),
            PRODUCTION_EVIDENCE_BINDINGS_BLOCKER
        );
    }

    #[test]
    fn typed_blob_namespace_rejects_cross_generation_ledger_adoption() {
        let directory = TestDirectory::new();
        let path = directory.ledger_path();
        let identity = LedgerIdentity::from_digests([0x15; 32], [0x25; 32]).unwrap();
        let namespace =
            crate::primitive_evidence_authority_blob::AuthenticatedProtectedBlobNamespace::provision_unsecured_test(
                directory.0.join("protected-blobs"),
                [0x35; 32],
                identity.canonical_digest(),
            )
            .unwrap();
        let ledger = AuthorityLedger::provision_new(&path, identity.clone()).unwrap();
        let error = match DurableRuntimeLedger::from_authenticated_ledger_with_test_namespace(
            path, identity, ledger, namespace,
        ) {
            Ok(_) => panic!("cross-generation blob namespace was accepted"),
            Err(error) => error,
        };
        assert_eq!(error.code(), "protected_blob_ledger_identity_mismatch");
    }

    #[test]
    fn durable_runtime_ledger_reopens_exact_blob_bound_result_terminal() {
        let directory = TestDirectory::new();
        let path = directory.ledger_path();
        let blob_root = directory.0.join("protected-blobs");
        let identity = LedgerIdentity::from_digests([0x16; 32], [0x26; 32]).unwrap();
        let blob_namespace =
            crate::primitive_evidence_authority_blob::AuthenticatedProtectedBlobNamespace::provision_unsecured_test(
                blob_root,
                *identity.authority_generation_digest(),
                identity.canonical_digest(),
            )
            .unwrap();
        let ledger = AuthorityLedger::provision_new(&path, identity.clone()).unwrap();
        let mut adapter = DurableRuntimeLedger::from_authenticated_ledger_with_test_namespace(
            path.clone(),
            identity.clone(),
            ledger,
            blob_namespace,
        )
        .unwrap();
        adapter.require_protected_blob_authority().unwrap();
        let ticket_digest = [0x36; 32];
        let ticket = RuntimeTicketRef::from_persisted(&hex_lower(&ticket_digest)).unwrap();
        let run_binding = [0x46; 32];
        let prepared = protected_prepared_receipt(0x56);
        let armed = vec![0x66; 480];
        let policy = vec![0x76; 4096];
        let result =
            verified_runtime_result(ticket_digest, run_binding, &prepared, &armed, &policy);
        adapter
            .issue(&ticket, &run_binding, &prepared, &policy)
            .unwrap();
        adapter.consume(&ticket, &run_binding).unwrap();
        adapter
            .record_armed_receipt(&ticket, &run_binding, &armed)
            .unwrap();
        adapter
            .record_verified_result_pending_exact(&ticket, &run_binding, &result)
            .unwrap();
        adapter
            .record_result_exact(&ticket, &run_binding, result.finalization_bytes())
            .unwrap();
        adapter
            .reopen_result_commit_terminal(&ticket, &run_binding, &result)
            .expect("exact result terminal");

        let projection = VerifiedAuthorityResultProjection::for_signed_receipt_contract_test(
            b"{\"projection\":true}".to_vec(),
            [0x16; 32],
            [0x86; 32],
            ticket_digest,
            run_binding,
        )
        .unwrap();
        adapter
            .record_projection_pending_exact(&ticket, &run_binding, &projection)
            .unwrap();
        adapter
            .reopen_result_commit_terminal(&ticket, &run_binding, &result)
            .expect("result prefix remains exact after later projection frame");
        drop(adapter);
        let restart_error = match AuthorityLedger::open_existing(&path, identity) {
            Ok(_) => panic!("blob-backed restart replayed without its authority"),
            Err(error) => error,
        };
        assert_eq!(
            restart_error.code(),
            "protected_blob_authority_not_connected"
        );
    }

    #[test]
    fn durable_runtime_ledger_requires_the_exact_absolute_generation_binding() {
        let directory = TestDirectory::new();
        let path = directory.ledger_path();
        let identity = LedgerIdentity::from_digests([0x11; 32], [0x21; 32]).unwrap();
        drop(AuthorityLedger::provision_new(&path, identity.clone()).unwrap());

        let mut adapter = DurableRuntimeLedger::new(path, identity).unwrap();
        let mismatch = runtime_identity(0x11, 0x22);
        assert_eq!(
            RuntimeTicketLedger::open_existing(&mut adapter, &mismatch)
                .unwrap_err()
                .code(),
            "authority_runtime_ledger_identity_mismatch"
        );
        adapter.ledger.verify_current_identity().unwrap();

        RuntimeTicketLedger::open_existing(&mut adapter, &runtime_identity(0x11, 0x21))
            .expect("exact generation and signer open the durable ledger");
        adapter.ledger.verify_current_identity().unwrap();
    }

    #[test]
    fn durable_runtime_ledger_adopts_the_authenticated_pair_without_reopen() {
        let directory = TestDirectory::new();
        let path = directory.ledger_path();
        let identity = LedgerIdentity::from_digests([0x14; 32], [0x24; 32]).unwrap();
        drop(AuthorityLedger::provision_new(&path, identity.clone()).unwrap());
        let ledger = AuthorityLedger::open_existing(&path, identity.clone()).unwrap();

        let mut adapter = DurableRuntimeLedger::from_test_ledger(path, identity, ledger).unwrap();
        adapter.ledger.verify_current_identity().unwrap();
        RuntimeTicketLedger::open_existing(&mut adapter, &runtime_identity(0x14, 0x24)).unwrap();
        adapter.ledger.verify_current_identity().unwrap();
    }

    #[test]
    fn durable_runtime_ledger_maps_ticket_lifecycle_and_persists_terminal_reason() {
        let directory = TestDirectory::new();
        let path = directory.ledger_path();
        let identity = LedgerIdentity::from_digests([0x12; 32], [0x22; 32]).unwrap();
        drop(AuthorityLedger::provision_new(&path, identity.clone()).unwrap());

        let mut adapter = DurableRuntimeLedger::new(path.clone(), identity.clone()).unwrap();
        RuntimeTicketLedger::open_existing(&mut adapter, &runtime_identity(0x12, 0x22)).unwrap();
        let ticket = RuntimeTicketRef::from_persisted(&hex_lower(&[0x32; 32])).unwrap();
        let run_binding = [0x42; 32];
        adapter
            .issue(
                &ticket,
                &run_binding,
                b"sealed-prepared-receipt",
                b"canonical-policy-snapshot",
            )
            .unwrap();
        adapter.consume(&ticket, &run_binding).unwrap();
        adapter
            .burn(&ticket, &run_binding, RuntimeTerminalKind::Cancelled)
            .unwrap();
        assert_eq!(
            adapter.terminal_reason(&ticket).unwrap(),
            Some(RuntimeTerminalKind::Cancelled)
        );
        drop(adapter);

        let reopened = AuthorityLedger::open_existing(&path, identity).unwrap();
        assert_eq!(
            reopened.burn_reason(ticket.as_str()).unwrap(),
            Some(TicketBurnReason::Cancelled)
        );
    }

    #[test]
    fn durable_runtime_ledger_persists_a_verified_recovered_normal_reason() {
        let directory = TestDirectory::new();
        let path = directory.ledger_path();
        let identity = LedgerIdentity::from_digests([0x15; 32], [0x25; 32]).unwrap();
        drop(AuthorityLedger::provision_new(&path, identity.clone()).unwrap());

        let mut adapter = DurableRuntimeLedger::new(path.clone(), identity.clone()).unwrap();
        RuntimeTicketLedger::open_existing(&mut adapter, &runtime_identity(0x15, 0x25)).unwrap();
        let ticket_digest = [0x35; 32];
        let ticket = RuntimeTicketRef::from_persisted(&hex_lower(&ticket_digest)).unwrap();
        let run_binding = [0x45; 32];
        let prepared = b"sealed-prepared-recovery-receipt";
        adapter
            .issue(
                &ticket,
                &run_binding,
                prepared,
                b"canonical-recovery-policy-snapshot",
            )
            .unwrap();
        adapter.consume(&ticket, &run_binding).unwrap();

        let prepared_digest: [u8; 32] = Sha256::digest(prepared).into();
        let stage_head = [0x55; 32];
        let intent = [0x65; 32];
        let terminal = [0x75; 32];
        let cleanup = [0x85; 32];
        let proof_digest = RecoveredBurnProof::canonical_digest(
            ticket_digest,
            run_binding,
            prepared_digest,
            None,
            stage_head,
            intent,
            terminal,
            cleanup,
            TicketBurnReason::TimedOut,
        )
        .unwrap();
        let proof = RecoveredBurnProof::from_verified_digest(
            proof_digest,
            ticket_digest,
            run_binding,
            prepared_digest,
            None,
            stage_head,
            intent,
            terminal,
            cleanup,
            TicketBurnReason::TimedOut,
        )
        .unwrap();
        RuntimeTicketLedger::burn_recovered_with_reason(
            &mut adapter,
            &ticket,
            &run_binding,
            RuntimeTerminalKind::TimedOut,
            &proof,
        )
        .unwrap();
        assert_eq!(
            adapter.terminal_reason(&ticket).unwrap(),
            Some(RuntimeTerminalKind::TimedOut)
        );
        drop(adapter);

        let reopened = AuthorityLedger::open_existing(&path, identity).unwrap();
        assert_eq!(
            reopened.burn_reason(ticket.as_str()).unwrap(),
            Some(TicketBurnReason::TimedOut)
        );
    }

    #[cfg(windows)]
    #[test]
    fn production_native_supervisor_gate_never_enters_the_native_executor() {
        let background = BackgroundNativeSupervisor::new(WindowsNativeSupervisorApi).unwrap();
        let mut supervisor = ProductionNativeSupervisor::blocked_for_test(background);
        assert_eq!(
            supervisor
                .contain_all_orphans(&runtime_identity(0x13, 0x23))
                .unwrap_err()
                .code(),
            PRODUCTION_SUPERVISOR_BLOCKER
        );
        supervisor.shutdown_and_wait().unwrap();
    }
}
