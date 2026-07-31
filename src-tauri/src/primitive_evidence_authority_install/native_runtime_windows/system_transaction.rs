//! Actor-separated maintenance transaction and finalization contracts.
//!
//! The SYSTEM worker may apply or recover the transaction, resolve its source
//! staging, persist `ExitReady`, and return. It is intentionally impossible for
//! a SYSTEM implementation of this module to request its own service stop,
//! wait for its own process, or delete its own service. Those capabilities live
//! exclusively on the elevated finalizer surface.
//!
//! The production mutation gate remains disabled. This module owns no Windows
//! handles and performs no SCM or filesystem calls; the native adapters must
//! implement these contracts from held handles and durable readbacks.

use super::super::{
    transaction::{execute_with_executor, MaintenanceExecutor},
    AuthorityMaintenanceError, AuthorityMaintenanceExecutionReport, AuthorityMaintenancePreview,
    VerifiedMaintenanceLease,
};
use sha2::{Digest as _, Sha256};

#[path = "system_transaction/adapter.rs"]
mod adapter;

type Digest = [u8; 32];

const WORKER_PROCESS_IDENTITY_DOMAIN: &[u8] =
    b"vrcforge-authority-native-worker-process-identity-v1\0";
const FINALIZER_PROCESS_IDENTITY_DOMAIN: &[u8] =
    b"vrcforge-authority-native-finalizer-process-identity-v1\0";
const SERVICE_DELETE_INTENT_DOMAIN: &[u8] = b"vrcforge-authority-native-service-delete-intent-v1\0";
const SERVICE_DELETE_PENDING_DOMAIN: &[u8] =
    b"vrcforge-authority-native-service-delete-pending-v1\0";
const FINALIZER_HANDLES_CLOSED_DOMAIN: &[u8] =
    b"vrcforge-authority-native-finalizer-handles-closed-v1\0";
const SERVICE_ABSENT_DOMAIN: &[u8] = b"vrcforge-authority-native-service-absent-v1\0";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct NativeWorkerProcessIdentity {
    process_id: u32,
    process_creation_time: u64,
    image_sha256: Digest,
}

impl NativeWorkerProcessIdentity {
    pub(super) fn new(
        process_id: u32,
        process_creation_time: u64,
        image_sha256: Digest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            process_id,
            process_creation_time,
            image_sha256,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if self.process_id == 0
            || self.process_creation_time == 0
            || is_zero_digest(&self.image_sha256)
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_process_identity_invalid",
            ));
        }
        Ok(())
    }

    fn digest(&self) -> Digest {
        let mut digest = Sha256::new();
        digest.update(WORKER_PROCESS_IDENTITY_DOMAIN);
        digest.update(self.process_id.to_be_bytes());
        digest.update(self.process_creation_time.to_be_bytes());
        digest.update(self.image_sha256);
        digest.finalize().into()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct NativeFinalizerProcessIdentity {
    process_id: u32,
    process_creation_time: u64,
    image_sha256: Digest,
}

impl NativeFinalizerProcessIdentity {
    pub(super) fn new(
        process_id: u32,
        process_creation_time: u64,
        image_sha256: Digest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            process_id,
            process_creation_time,
            image_sha256,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if self.process_id == 0
            || self.process_creation_time == 0
            || is_zero_digest(&self.image_sha256)
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_finalizer_process_identity_invalid",
            ));
        }
        Ok(())
    }

    fn process_id(&self) -> u32 {
        self.process_id
    }

    fn digest(&self) -> Digest {
        let mut digest = Sha256::new();
        digest.update(FINALIZER_PROCESS_IDENTITY_DOMAIN);
        digest.update(self.process_id.to_be_bytes());
        digest.update(self.process_creation_time.to_be_bytes());
        digest.update(self.image_sha256);
        digest.finalize().into()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct NativeSystemTransactionBinding {
    capsule_sha256: Digest,
    plan_sha256: Digest,
    generation_sha256: Digest,
    transaction_started_receipt_sha256: Digest,
    exact_service_identity_sha256: Digest,
    expected_worker_process_sha256: Digest,
    worker: NativeWorkerProcessIdentity,
}

impl NativeSystemTransactionBinding {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn new(
        capsule_sha256: Digest,
        plan_sha256: Digest,
        generation_sha256: Digest,
        transaction_started_receipt_sha256: Digest,
        exact_service_identity_sha256: Digest,
        expected_worker_process_sha256: Digest,
        worker_process_id: u32,
        worker_process_creation_time: u64,
        worker_image_sha256: Digest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let worker = NativeWorkerProcessIdentity::new(
            worker_process_id,
            worker_process_creation_time,
            worker_image_sha256,
        )?;
        let value = Self {
            capsule_sha256,
            plan_sha256,
            generation_sha256,
            transaction_started_receipt_sha256,
            exact_service_identity_sha256,
            expected_worker_process_sha256,
            worker,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.worker.validate()?;
        if [
            self.capsule_sha256,
            self.plan_sha256,
            self.generation_sha256,
            self.transaction_started_receipt_sha256,
            self.exact_service_identity_sha256,
            self.expected_worker_process_sha256,
        ]
        .iter()
        .any(is_zero_digest)
            || self.expected_worker_process_sha256 != self.worker.digest()
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_system_transaction_binding_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn worker(&self) -> NativeWorkerProcessIdentity {
        self.worker
    }

    pub(super) fn capsule_sha256(&self) -> Digest {
        self.capsule_sha256
    }

    pub(super) fn plan_sha256(&self) -> Digest {
        self.plan_sha256
    }

    pub(super) fn generation_sha256(&self) -> Digest {
        self.generation_sha256
    }

    pub(super) fn transaction_started_receipt_sha256(&self) -> Digest {
        self.transaction_started_receipt_sha256
    }

    pub(super) fn exact_service_identity_sha256(&self) -> Digest {
        self.exact_service_identity_sha256
    }

    pub(super) fn expected_worker_process_sha256(&self) -> Digest {
        self.expected_worker_process_sha256
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum NativeSystemTransactionTerminalKind {
    Committed,
    Contained,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum NativeDurableTransactionJournalState {
    Ready,
    Committed,
    Contained,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct NativeSystemTransactionTerminal {
    kind: NativeSystemTransactionTerminalKind,
    receipt_sha256: Digest,
}

impl NativeSystemTransactionTerminal {
    fn new(
        kind: NativeSystemTransactionTerminalKind,
        receipt_sha256: Digest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if is_zero_digest(&receipt_sha256) {
            return Err(AuthorityMaintenanceError(
                "authority_native_system_transaction_terminal_invalid",
            ));
        }
        Ok(Self {
            kind,
            receipt_sha256,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct NativeSystemDurableSnapshot {
    capsule_sha256: Digest,
    terminal: Option<NativeSystemTransactionTerminal>,
    source_stage_resolved_receipt_sha256: Option<Digest>,
    exit_ready_receipt_sha256: Option<Digest>,
    service_delete_intent_receipt_sha256: Option<Digest>,
    service_delete_pending_receipt_sha256: Option<Digest>,
    finalizer_handles_closed_receipt_sha256: Option<Digest>,
    service_absent_receipt_sha256: Option<Digest>,
}

impl NativeSystemDurableSnapshot {
    pub(super) fn transaction_started(binding: &NativeSystemTransactionBinding) -> Self {
        Self {
            capsule_sha256: binding.capsule_sha256,
            terminal: None,
            source_stage_resolved_receipt_sha256: None,
            exit_ready_receipt_sha256: None,
            service_delete_intent_receipt_sha256: None,
            service_delete_pending_receipt_sha256: None,
            finalizer_handles_closed_receipt_sha256: None,
            service_absent_receipt_sha256: None,
        }
    }

    fn validate(
        &self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<(), AuthorityMaintenanceError> {
        let ordered = [
            self.terminal.map(|value| value.receipt_sha256),
            self.source_stage_resolved_receipt_sha256,
            self.exit_ready_receipt_sha256,
            self.service_delete_intent_receipt_sha256,
            self.service_delete_pending_receipt_sha256,
            self.finalizer_handles_closed_receipt_sha256,
            self.service_absent_receipt_sha256,
        ];
        let mut missing_predecessor = false;
        let mut prior_present = true;
        for value in ordered {
            if value.is_some_and(|digest| is_zero_digest(&digest)) {
                return Err(AuthorityMaintenanceError(
                    "authority_native_system_durable_snapshot_invalid",
                ));
            }
            if value.is_some() && !prior_present {
                missing_predecessor = true;
            }
            prior_present = value.is_some();
        }
        if self.capsule_sha256 != binding.capsule_sha256 || missing_predecessor {
            return Err(AuthorityMaintenanceError(
                "authority_native_system_durable_snapshot_invalid",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct NativeZeroResidueReadback {
    service_absent: bool,
    temporary_staging_absent: bool,
    temporary_candidate_credential_absent: bool,
    pipe_absent: bool,
    worker_process_absent: bool,
    temporary_worker_state_absent: bool,
    nonce_consumption_receipt_present_and_valid: bool,
    candidate_consumption_tombstone_present_and_valid: bool,
    readback_sha256: Digest,
}

impl NativeZeroResidueReadback {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn new(
        service_absent: bool,
        temporary_staging_absent: bool,
        temporary_candidate_credential_absent: bool,
        pipe_absent: bool,
        worker_process_absent: bool,
        temporary_worker_state_absent: bool,
        nonce_consumption_receipt_present_and_valid: bool,
        candidate_consumption_tombstone_present_and_valid: bool,
        readback_sha256: Digest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            service_absent,
            temporary_staging_absent,
            temporary_candidate_credential_absent,
            pipe_absent,
            worker_process_absent,
            temporary_worker_state_absent,
            nonce_consumption_receipt_present_and_valid,
            candidate_consumption_tombstone_present_and_valid,
            readback_sha256,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if !self.service_absent
            || !self.temporary_staging_absent
            || !self.temporary_candidate_credential_absent
            || !self.pipe_absent
            || !self.worker_process_absent
            || !self.temporary_worker_state_absent
            || !self.nonce_consumption_receipt_present_and_valid
            || !self.candidate_consumption_tombstone_present_and_valid
            || is_zero_digest(&self.readback_sha256)
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_system_zero_residue_invalid",
            ));
        }
        Ok(())
    }
}

/// Capabilities available to the LocalSystem worker. SCM lifecycle operations
/// are deliberately absent from this trait.
pub(super) trait NativeSystemTransactionOperations: MaintenanceExecutor {
    fn reopen_durable_snapshot(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeSystemDurableSnapshot, AuthorityMaintenanceError>;

    fn reopen_durable_transaction_journal(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeDurableTransactionJournalState, AuthorityMaintenanceError>;

    fn committed_outcome_readback(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<Digest, AuthorityMaintenanceError>;

    fn contained_outcome_readback(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<Digest, AuthorityMaintenanceError>;

    fn validate_exit_ready_receipt(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        source_stage_resolved_receipt_sha256: Digest,
        exit_ready_receipt_sha256: Digest,
    ) -> Result<(), AuthorityMaintenanceError>;

    fn persist_transaction_terminal(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        kind: NativeSystemTransactionTerminalKind,
        outcome_readback_sha256: Digest,
    ) -> Result<Digest, AuthorityMaintenanceError>;

    fn resolve_source_staging(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
    ) -> Result<Digest, AuthorityMaintenanceError>;

    fn persist_exit_ready(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        source_stage_resolved_receipt_sha256: Digest,
    ) -> Result<Digest, AuthorityMaintenanceError>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct NativeSystemExitReadyOutcome {
    pub(super) terminal: NativeSystemTransactionTerminalKind,
    pub(super) exit_ready_receipt_sha256: Digest,
    pub(super) resumed_from_durable_state: bool,
    pub(super) apply_recovery_reopened: bool,
}

/// Legacy terminal-first harness retained for recovery regression tests.
/// Production calls fail before any operation until the finalizer-owned
/// commit protocol in `adapter` has concrete durable receipts and storage.
pub(super) fn execute_bound_native_system_transaction<O>(
    preview: &AuthorityMaintenancePreview,
    lease: &mut VerifiedMaintenanceLease,
    binding: &NativeSystemTransactionBinding,
    operations: &mut O,
) -> Result<NativeSystemExitReadyOutcome, AuthorityMaintenanceError>
where
    O: NativeSystemTransactionOperations,
{
    #[cfg(not(test))]
    adapter::require_finalizer_owned_commit_protocol()?;
    binding.validate()?;
    if preview.plan_sha256()? != binding.plan_sha256
        || preview.generation_sha256()? != binding.generation_sha256
    {
        return Err(AuthorityMaintenanceError(
            "authority_native_system_transaction_plan_mismatch",
        ));
    }
    let mut snapshot = reopen_and_validate(operations, binding)?;
    let resumed_from_durable_state = snapshot.terminal.is_some();
    let snapshot_terminal_reopened = validate_snapshot_terminal(operations, binding, &snapshot)?;

    if let (Some(terminal), Some(source_resolved), Some(exit_ready)) = (
        snapshot.terminal,
        snapshot.source_stage_resolved_receipt_sha256,
        snapshot.exit_ready_receipt_sha256,
    ) {
        let notified = operations.persist_exit_ready(binding, terminal, source_resolved)?;
        if notified != exit_ready {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_exit_ready_mismatch",
            ));
        }
        return Ok(NativeSystemExitReadyOutcome {
            terminal: terminal.kind,
            exit_ready_receipt_sha256: exit_ready,
            resumed_from_durable_state: true,
            apply_recovery_reopened: snapshot_terminal_reopened,
        });
    }

    let (terminal, apply_recovery_reopened) = match snapshot.terminal {
        Some(terminal) => (terminal, snapshot_terminal_reopened),
        None => establish_transaction_terminal(preview, lease, binding, operations)?,
    };
    snapshot = reopen_and_validate(operations, binding)?;
    if snapshot.terminal != Some(terminal) {
        return Err(AuthorityMaintenanceError(
            "authority_native_system_transaction_terminal_not_durable",
        ));
    }

    let source_resolved = match snapshot.source_stage_resolved_receipt_sha256 {
        Some(value) => value,
        None => {
            let receipt = operations.resolve_source_staging(binding, terminal)?;
            if is_zero_digest(&receipt) {
                return Err(AuthorityMaintenanceError(
                    "authority_native_worker_source_stage_resolution_invalid",
                ));
            }
            snapshot = reopen_and_validate(operations, binding)?;
            if snapshot.source_stage_resolved_receipt_sha256 != Some(receipt) {
                return Err(AuthorityMaintenanceError(
                    "authority_native_worker_source_stage_resolution_not_durable",
                ));
            }
            receipt
        }
    };

    let exit_ready = match snapshot.exit_ready_receipt_sha256 {
        Some(value) => value,
        None => {
            let receipt = operations.persist_exit_ready(binding, terminal, source_resolved)?;
            if is_zero_digest(&receipt) {
                return Err(AuthorityMaintenanceError(
                    "authority_native_worker_exit_ready_mismatch",
                ));
            }
            snapshot = reopen_and_validate(operations, binding)?;
            if snapshot.exit_ready_receipt_sha256 != Some(receipt) {
                return Err(AuthorityMaintenanceError(
                    "authority_native_worker_exit_ready_not_durable",
                ));
            }
            receipt
        }
    };

    Ok(NativeSystemExitReadyOutcome {
        terminal: terminal.kind,
        exit_ready_receipt_sha256: exit_ready,
        resumed_from_durable_state,
        apply_recovery_reopened,
    })
}

fn validate_snapshot_terminal<O>(
    operations: &mut O,
    binding: &NativeSystemTransactionBinding,
    snapshot: &NativeSystemDurableSnapshot,
) -> Result<bool, AuthorityMaintenanceError>
where
    O: NativeSystemTransactionOperations,
{
    let Some(terminal) = snapshot.terminal else {
        return Ok(false);
    };
    let durable = operations.reopen_durable_transaction_journal(binding)?;
    if journal_terminal_kind(durable) != Some(terminal.kind) {
        return Err(AuthorityMaintenanceError(
            "authority_native_transaction_terminal_conflict",
        ));
    }
    Ok(true)
}

fn reopen_and_validate<O>(
    operations: &mut O,
    binding: &NativeSystemTransactionBinding,
) -> Result<NativeSystemDurableSnapshot, AuthorityMaintenanceError>
where
    O: NativeSystemTransactionOperations,
{
    let snapshot = operations.reopen_durable_snapshot(binding)?;
    snapshot.validate(binding)?;
    validate_system_exit_ready_receipt(operations, binding, &snapshot)?;
    Ok(snapshot)
}

fn validate_system_exit_ready_receipt<O>(
    operations: &mut O,
    binding: &NativeSystemTransactionBinding,
    snapshot: &NativeSystemDurableSnapshot,
) -> Result<(), AuthorityMaintenanceError>
where
    O: NativeSystemTransactionOperations,
{
    if let (Some(terminal), Some(source_resolved), Some(exit_ready)) = (
        snapshot.terminal,
        snapshot.source_stage_resolved_receipt_sha256,
        snapshot.exit_ready_receipt_sha256,
    ) {
        operations.validate_exit_ready_receipt(binding, terminal, source_resolved, exit_ready)?;
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum NativeWorkerNaturalExit {
    Exited,
    StillRunning,
}

/// Exact live observation paired with the durable post-`ExitReady` prefix.
/// Every worker-absent state is valid only when the adapter has proved that the
/// exact process identity bound into `WorkerStarted` is no longer active.
/// `DeletePending` or `Absent` observed after a durable delete intent may be
/// persisted as a typed target-state transition, without claiming historical
/// knowledge of whether an earlier finalizer completed `DeleteService`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum NativeWorkerFinalizationLiveState {
    ExactServiceAndWorker,
    ExactServiceStoppedAndWorkerAbsent,
    DeletePendingAndWorkerAbsent,
    ServiceAbsentAndWorkerAbsent,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum NativeServiceDeleteTransitionKind {
    DeleteCallCompleted,
    TargetStateObserved,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct NativeWorkerFinalizationLiveReadback {
    state: NativeWorkerFinalizationLiveState,
    worker: NativeWorkerProcessIdentity,
    exact_service_identity_sha256: Digest,
    readback_sha256: Digest,
}

impl NativeWorkerFinalizationLiveReadback {
    pub(super) fn from_observed(
        state: NativeWorkerFinalizationLiveState,
        worker: NativeWorkerProcessIdentity,
        exact_service_identity_sha256: Digest,
        readback_sha256: Digest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            state,
            worker,
            exact_service_identity_sha256,
            readback_sha256,
        };
        if is_zero_digest(&value.exact_service_identity_sha256)
            || is_zero_digest(&value.readback_sha256)
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_finalization_live_readback_invalid",
            ));
        }
        Ok(value)
    }

    fn validate(
        &self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.worker != binding.worker {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_process_identity_mismatch",
            ));
        }
        if self.exact_service_identity_sha256 != binding.exact_service_identity_sha256
            || is_zero_digest(&self.readback_sha256)
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_finalization_live_readback_mismatch",
            ));
        }
        Ok(())
    }

    fn state(&self) -> NativeWorkerFinalizationLiveState {
        self.state
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct NativeWorkerIdentityReadback {
    worker: NativeWorkerProcessIdentity,
    process_present: bool,
    exact_service_stopped: bool,
    readback_sha256: Digest,
}

impl NativeWorkerIdentityReadback {
    pub(super) fn exact_running(
        worker: NativeWorkerProcessIdentity,
        readback_sha256: Digest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            worker,
            process_present: true,
            exact_service_stopped: false,
            readback_sha256,
        };
        value.validate(worker)?;
        Ok(value)
    }

    pub(super) fn exact_stopped(
        worker: NativeWorkerProcessIdentity,
        readback_sha256: Digest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            worker,
            process_present: false,
            exact_service_stopped: true,
            readback_sha256,
        };
        value.validate(worker)?;
        Ok(value)
    }

    fn validate(
        &self,
        expected: NativeWorkerProcessIdentity,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.worker != expected
            || is_zero_digest(&self.readback_sha256)
            || (!self.process_present && !self.exact_service_stopped)
            || (self.process_present && self.exact_service_stopped)
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_process_identity_mismatch",
            ));
        }
        Ok(())
    }

    fn require_exact_stopped_worker_absent(&self) -> Result<(), AuthorityMaintenanceError> {
        if self.process_present || !self.exact_service_stopped {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_stopped_absence_unproven",
            ));
        }
        Ok(())
    }
}

/// Capabilities available only to the elevated helper after the SYSTEM worker
/// has durably reached `ExitReady` and is about to return naturally.
pub(super) trait NativeElevatedFinalizerOperations {
    fn reopen_durable_snapshot(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeSystemDurableSnapshot, AuthorityMaintenanceError>;

    fn validate_exit_ready_receipt(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        source_stage_resolved_receipt_sha256: Digest,
        exit_ready_receipt_sha256: Digest,
    ) -> Result<(), AuthorityMaintenanceError>;

    fn observe_current_finalizer_identity(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeFinalizerProcessIdentity, AuthorityMaintenanceError>;

    fn validate_service_delete_intent_receipt(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        exit_ready_receipt_sha256: Digest,
        service_delete_intent_receipt_sha256: Digest,
    ) -> Result<NativeFinalizerProcessIdentity, AuthorityMaintenanceError>;

    fn validate_service_delete_pending_receipt(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        exit_ready_receipt_sha256: Digest,
        service_delete_intent_receipt_sha256: Digest,
        service_delete_pending_receipt_sha256: Digest,
    ) -> Result<NativeServiceDeleteTransitionKind, AuthorityMaintenanceError>;

    fn validate_finalizer_handles_closed_receipt(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        service_delete_pending_receipt_sha256: Digest,
        finalizer_handles_closed_receipt_sha256: Digest,
    ) -> Result<(), AuthorityMaintenanceError>;

    fn validate_service_absent_receipt(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        finalizer_handles_closed_receipt_sha256: Digest,
        service_absent_receipt_sha256: Digest,
    ) -> Result<(), AuthorityMaintenanceError>;

    fn observe_worker_finalization_live_state(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeWorkerFinalizationLiveReadback, AuthorityMaintenanceError>;

    fn observe_worker_identity(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeWorkerIdentityReadback, AuthorityMaintenanceError>;

    fn wait_for_natural_worker_exit(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeWorkerNaturalExit, AuthorityMaintenanceError>;

    fn request_worker_service_stop(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<(), AuthorityMaintenanceError>;

    fn wait_worker_service_stopped(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<(), AuthorityMaintenanceError>;

    fn wait_exact_worker_process_exit(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<(), AuthorityMaintenanceError>;

    fn persist_service_delete_intent(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        exit_ready_receipt_sha256: Digest,
        finalizer: NativeFinalizerProcessIdentity,
        expected_receipt_sha256: Digest,
    ) -> Result<Digest, AuthorityMaintenanceError>;

    fn mark_worker_service_delete_pending(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<Digest, AuthorityMaintenanceError>;

    fn persist_service_delete_pending(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        exit_ready_receipt_sha256: Digest,
        service_delete_intent_receipt_sha256: Digest,
        transition_kind: NativeServiceDeleteTransitionKind,
        delete_pending_readback_sha256: Digest,
        expected_receipt_sha256: Digest,
    ) -> Result<Digest, AuthorityMaintenanceError>;

    fn observe_delete_target_state_transition(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        service_delete_intent_receipt_sha256: Digest,
        live_state: NativeWorkerFinalizationLiveState,
    ) -> Result<Digest, AuthorityMaintenanceError>;

    fn close_finalizer_handles(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<Digest, AuthorityMaintenanceError>;

    /// Re-establishes only the *current* handles-closed fact after a prior
    /// finalizer disappeared. Implementations must bind the old finalizer
    /// identity, prove that process absent, and prove the old worker absent;
    /// they must not report the historical mechanism or timing of closure.
    fn recover_finalizer_handles_closed_after_restart(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        service_delete_pending_receipt_sha256: Digest,
        prior_finalizer: NativeFinalizerProcessIdentity,
        current_finalizer: NativeFinalizerProcessIdentity,
    ) -> Result<Digest, AuthorityMaintenanceError>;

    fn persist_finalizer_handles_closed(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        delete_pending_receipt_sha256: Digest,
        handles_closed_readback_sha256: Digest,
        expected_receipt_sha256: Digest,
    ) -> Result<Digest, AuthorityMaintenanceError>;

    fn scm_service_absence_readback(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<Digest, AuthorityMaintenanceError>;

    fn remove_finalizer_staging_and_verify_zero_residue(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeZeroResidueReadback, AuthorityMaintenanceError>;

    fn verify_completed_finalization_readback(
        &mut self,
        binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeZeroResidueReadback, AuthorityMaintenanceError>;

    fn persist_service_absent(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        finalizer_handles_closed_receipt_sha256: Digest,
        scm_absence_readback_sha256: Digest,
        residue: &NativeZeroResidueReadback,
        expected_receipt_sha256: Digest,
    ) -> Result<Digest, AuthorityMaintenanceError>;
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ReopenedNativeFinalizerSnapshot {
    durable: NativeSystemDurableSnapshot,
    intent_finalizer: Option<NativeFinalizerProcessIdentity>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct NativeElevatedFinalizationOutcome {
    pub(super) terminal: NativeSystemTransactionTerminalKind,
    pub(super) service_absent_receipt_sha256: Digest,
    pub(super) stop_was_required: bool,
    pub(super) resumed_from_durable_state: bool,
}

pub(super) fn finalize_bound_native_worker<O>(
    finalizer_process_id: u32,
    binding: &NativeSystemTransactionBinding,
    operations: &mut O,
) -> Result<NativeElevatedFinalizationOutcome, AuthorityMaintenanceError>
where
    O: NativeElevatedFinalizerOperations,
{
    binding.validate()?;
    require_distinct_finalizer_process(finalizer_process_id, binding.worker.process_id)?;
    let current_finalizer = operations.observe_current_finalizer_identity(binding)?;
    current_finalizer.validate()?;
    if current_finalizer.process_id() != finalizer_process_id {
        return Err(AuthorityMaintenanceError(
            "authority_native_finalizer_process_identity_mismatch",
        ));
    }
    let reopened = reopen_finalizer_snapshot(operations, binding)?;
    let mut snapshot = reopened.durable;
    let mut intent_finalizer = reopened.intent_finalizer;
    let terminal = snapshot.terminal.ok_or(AuthorityMaintenanceError(
        "authority_native_worker_transaction_terminal_missing",
    ))?;
    let exit_ready = snapshot
        .exit_ready_receipt_sha256
        .ok_or(AuthorityMaintenanceError(
            "authority_native_worker_exit_ready_missing",
        ))?;
    let live_readback = operations.observe_worker_finalization_live_state(binding)?;
    live_readback.validate(binding)?;
    validate_worker_finalization_live_state(&snapshot, live_readback.state())?;
    let resumed_from_durable_state = snapshot.service_delete_intent_receipt_sha256.is_some()
        || snapshot.service_delete_pending_receipt_sha256.is_some()
        || snapshot.finalizer_handles_closed_receipt_sha256.is_some()
        || snapshot.service_absent_receipt_sha256.is_some();

    if let Some(service_absent_receipt_sha256) = snapshot.service_absent_receipt_sha256 {
        let residue = operations.verify_completed_finalization_readback(binding)?;
        residue.validate()?;
        return Ok(NativeElevatedFinalizationOutcome {
            terminal: terminal.kind,
            service_absent_receipt_sha256,
            stop_was_required: false,
            resumed_from_durable_state: true,
        });
    }

    let delete_intent = match snapshot.service_delete_intent_receipt_sha256 {
        Some(receipt) => receipt,
        None => {
            let expected =
                derive_service_delete_intent(binding, terminal, exit_ready, current_finalizer);
            let receipt = operations.persist_service_delete_intent(
                binding,
                terminal,
                exit_ready,
                current_finalizer,
                expected,
            )?;
            if receipt != expected {
                return Err(AuthorityMaintenanceError(
                    "authority_native_worker_delete_intent_mismatch",
                ));
            }
            let reopened = reopen_finalizer_snapshot(operations, binding)?;
            snapshot = reopened.durable;
            intent_finalizer = reopened.intent_finalizer;
            if snapshot.service_delete_intent_receipt_sha256 != Some(receipt) {
                return Err(AuthorityMaintenanceError(
                    "authority_native_worker_delete_intent_not_durable",
                ));
            }
            receipt
        }
    };

    let mut stop_was_required = false;
    let mut marked_delete_this_run = false;
    let intent_finalizer = intent_finalizer.ok_or(AuthorityMaintenanceError(
        "authority_native_worker_delete_intent_finalizer_missing",
    ))?;
    let delete_pending = match snapshot.service_delete_pending_receipt_sha256 {
        Some(receipt) => receipt,
        None => {
            let (readback, transition_kind) = match live_readback.state() {
                NativeWorkerFinalizationLiveState::ExactServiceAndWorker => {
                    let identity = operations.observe_worker_identity(binding)?;
                    identity.validate(binding.worker)?;
                    match operations.wait_for_natural_worker_exit(binding)? {
                        NativeWorkerNaturalExit::Exited => {}
                        NativeWorkerNaturalExit::StillRunning => {
                            operations.request_worker_service_stop(binding)?;
                            stop_was_required = true;
                        }
                    }
                    operations.wait_worker_service_stopped(binding)?;
                    operations.wait_exact_worker_process_exit(binding)?;
                    marked_delete_this_run = true;
                    (
                        operations.mark_worker_service_delete_pending(binding)?,
                        NativeServiceDeleteTransitionKind::DeleteCallCompleted,
                    )
                }
                NativeWorkerFinalizationLiveState::ExactServiceStoppedAndWorkerAbsent => {
                    let identity = operations.observe_worker_identity(binding)?;
                    identity.validate(binding.worker)?;
                    identity.require_exact_stopped_worker_absent()?;
                    marked_delete_this_run = true;
                    (
                        operations.mark_worker_service_delete_pending(binding)?,
                        NativeServiceDeleteTransitionKind::DeleteCallCompleted,
                    )
                }
                state @ (NativeWorkerFinalizationLiveState::DeletePendingAndWorkerAbsent
                | NativeWorkerFinalizationLiveState::ServiceAbsentAndWorkerAbsent) => (
                    operations.observe_delete_target_state_transition(
                        binding,
                        delete_intent,
                        state,
                    )?,
                    NativeServiceDeleteTransitionKind::TargetStateObserved,
                ),
            };
            if is_zero_digest(&readback) {
                return Err(AuthorityMaintenanceError(
                    "authority_native_worker_delete_pending_invalid",
                ));
            }
            let expected = derive_service_delete_pending(
                binding,
                terminal,
                exit_ready,
                delete_intent,
                transition_kind,
                readback,
            );
            let receipt = operations.persist_service_delete_pending(
                binding,
                terminal,
                exit_ready,
                delete_intent,
                transition_kind,
                readback,
                expected,
            )?;
            if receipt != expected {
                return Err(AuthorityMaintenanceError(
                    "authority_native_worker_delete_pending_mismatch",
                ));
            }
            snapshot = reopen_finalizer_snapshot(operations, binding)?.durable;
            if snapshot.service_delete_pending_receipt_sha256 != Some(receipt) {
                return Err(AuthorityMaintenanceError(
                    "authority_native_worker_delete_pending_not_durable",
                ));
            }
            receipt
        }
    };

    let handles_closed = match snapshot.finalizer_handles_closed_receipt_sha256 {
        Some(receipt) => receipt,
        None => {
            let readback = if marked_delete_this_run {
                operations.close_finalizer_handles(binding)?
            } else {
                if current_finalizer == intent_finalizer {
                    return Err(AuthorityMaintenanceError(
                        "authority_native_finalizer_prior_identity_still_current",
                    ));
                }
                operations.recover_finalizer_handles_closed_after_restart(
                    binding,
                    delete_pending,
                    intent_finalizer,
                    current_finalizer,
                )?
            };
            if is_zero_digest(&readback) {
                return Err(AuthorityMaintenanceError(
                    "authority_native_finalizer_handles_closed_invalid",
                ));
            }
            let expected = derive_finalizer_handles_closed(binding, delete_pending, readback);
            let receipt = operations.persist_finalizer_handles_closed(
                binding,
                delete_pending,
                readback,
                expected,
            )?;
            if receipt != expected {
                return Err(AuthorityMaintenanceError(
                    "authority_native_finalizer_handles_closed_mismatch",
                ));
            }
            snapshot = reopen_finalizer_snapshot(operations, binding)?.durable;
            if snapshot.finalizer_handles_closed_receipt_sha256 != Some(receipt) {
                return Err(AuthorityMaintenanceError(
                    "authority_native_finalizer_handles_closed_not_durable",
                ));
            }
            receipt
        }
    };

    let scm_absence = operations.scm_service_absence_readback(binding)?;
    if is_zero_digest(&scm_absence) {
        return Err(AuthorityMaintenanceError(
            "authority_native_worker_service_absence_invalid",
        ));
    }
    let residue = operations.remove_finalizer_staging_and_verify_zero_residue(binding)?;
    residue.validate()?;
    let expected_service_absent = derive_service_absent(
        binding,
        terminal,
        handles_closed,
        scm_absence,
        residue.readback_sha256,
    );
    let service_absent = operations.persist_service_absent(
        binding,
        terminal,
        handles_closed,
        scm_absence,
        &residue,
        expected_service_absent,
    )?;
    if service_absent != expected_service_absent {
        return Err(AuthorityMaintenanceError(
            "authority_native_worker_service_absent_mismatch",
        ));
    }
    snapshot = reopen_finalizer_snapshot(operations, binding)?.durable;
    if snapshot.service_absent_receipt_sha256 != Some(service_absent) {
        return Err(AuthorityMaintenanceError(
            "authority_native_worker_service_absent_not_durable",
        ));
    }
    Ok(NativeElevatedFinalizationOutcome {
        terminal: terminal.kind,
        service_absent_receipt_sha256: service_absent,
        stop_was_required,
        resumed_from_durable_state,
    })
}

fn validate_worker_finalization_live_state(
    snapshot: &NativeSystemDurableSnapshot,
    live_state: NativeWorkerFinalizationLiveState,
) -> Result<(), AuthorityMaintenanceError> {
    use NativeWorkerFinalizationLiveState::{
        DeletePendingAndWorkerAbsent, ExactServiceAndWorker, ExactServiceStoppedAndWorkerAbsent,
        ServiceAbsentAndWorkerAbsent,
    };

    let durable_intent = snapshot.service_delete_intent_receipt_sha256.is_some();
    let durable_delete = snapshot.service_delete_pending_receipt_sha256.is_some();
    let durable_handles = snapshot.finalizer_handles_closed_receipt_sha256.is_some();
    let durable_absent = snapshot.service_absent_receipt_sha256.is_some();
    match (
        durable_intent,
        durable_delete,
        durable_handles,
        durable_absent,
        live_state,
    ) {
        (false, false, false, false, ExactServiceAndWorker) => Ok(()),
        (
            false,
            false,
            false,
            false,
            ExactServiceStoppedAndWorkerAbsent
            | DeletePendingAndWorkerAbsent
            | ServiceAbsentAndWorkerAbsent,
        ) => Err(AuthorityMaintenanceError(
            "authority_native_worker_delete_intent_missing",
        )),
        (
            true,
            false,
            false,
            false,
            ExactServiceAndWorker
            | ExactServiceStoppedAndWorkerAbsent
            | DeletePendingAndWorkerAbsent
            | ServiceAbsentAndWorkerAbsent,
        )
        | (true, true, false, false, DeletePendingAndWorkerAbsent | ServiceAbsentAndWorkerAbsent)
        | (true, true, true, false, DeletePendingAndWorkerAbsent | ServiceAbsentAndWorkerAbsent)
        | (true, true, true, true, ServiceAbsentAndWorkerAbsent) => Ok(()),
        _ => Err(AuthorityMaintenanceError(
            "authority_native_worker_finalization_live_state_mismatch",
        )),
    }
}

fn reopen_finalizer_snapshot<O>(
    operations: &mut O,
    binding: &NativeSystemTransactionBinding,
) -> Result<ReopenedNativeFinalizerSnapshot, AuthorityMaintenanceError>
where
    O: NativeElevatedFinalizerOperations,
{
    let snapshot = operations.reopen_durable_snapshot(binding)?;
    snapshot.validate(binding)?;
    if let (Some(terminal), Some(source_resolved), Some(exit_ready)) = (
        snapshot.terminal,
        snapshot.source_stage_resolved_receipt_sha256,
        snapshot.exit_ready_receipt_sha256,
    ) {
        operations.validate_exit_ready_receipt(binding, terminal, source_resolved, exit_ready)?;
    }

    let intent_finalizer = match (
        snapshot.terminal,
        snapshot.exit_ready_receipt_sha256,
        snapshot.service_delete_intent_receipt_sha256,
    ) {
        (Some(terminal), Some(exit_ready), Some(intent)) => {
            let finalizer = operations
                .validate_service_delete_intent_receipt(binding, terminal, exit_ready, intent)?;
            finalizer.validate()?;
            require_distinct_finalizer_process(finalizer.process_id(), binding.worker.process_id)?;
            Some(finalizer)
        }
        _ => None,
    };

    if let (Some(terminal), Some(exit_ready), Some(intent), Some(delete_pending)) = (
        snapshot.terminal,
        snapshot.exit_ready_receipt_sha256,
        snapshot.service_delete_intent_receipt_sha256,
        snapshot.service_delete_pending_receipt_sha256,
    ) {
        operations.validate_service_delete_pending_receipt(
            binding,
            terminal,
            exit_ready,
            intent,
            delete_pending,
        )?;
    }

    if let (Some(delete_pending), Some(handles_closed)) = (
        snapshot.service_delete_pending_receipt_sha256,
        snapshot.finalizer_handles_closed_receipt_sha256,
    ) {
        operations.validate_finalizer_handles_closed_receipt(
            binding,
            delete_pending,
            handles_closed,
        )?;
    }

    if let (Some(terminal), Some(handles_closed), Some(service_absent)) = (
        snapshot.terminal,
        snapshot.finalizer_handles_closed_receipt_sha256,
        snapshot.service_absent_receipt_sha256,
    ) {
        operations.validate_service_absent_receipt(
            binding,
            terminal,
            handles_closed,
            service_absent,
        )?;
    }

    Ok(ReopenedNativeFinalizerSnapshot {
        durable: snapshot,
        intent_finalizer,
    })
}

pub(super) fn require_distinct_finalizer_process(
    actor_process_id: u32,
    worker_process_id: u32,
) -> Result<(), AuthorityMaintenanceError> {
    if actor_process_id == 0 || worker_process_id == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_native_worker_process_identity_invalid",
        ));
    }
    if actor_process_id == worker_process_id {
        return Err(AuthorityMaintenanceError(
            "authority_native_worker_self_finalization_rejected",
        ));
    }
    Ok(())
}

fn establish_transaction_terminal<O>(
    preview: &AuthorityMaintenancePreview,
    lease: &mut VerifiedMaintenanceLease,
    binding: &NativeSystemTransactionBinding,
    operations: &mut O,
) -> Result<(NativeSystemTransactionTerminal, bool), AuthorityMaintenanceError>
where
    O: NativeSystemTransactionOperations,
{
    let initial = operations.reopen_durable_transaction_journal(binding)?;
    let (kind, reopened) = match journal_terminal_kind(initial) {
        Some(kind) => (kind, true),
        None => execute_and_reopen_transaction(preview, lease, binding, operations)?,
    };
    let outcome = match kind {
        NativeSystemTransactionTerminalKind::Committed => {
            operations.committed_outcome_readback(binding)?
        }
        NativeSystemTransactionTerminalKind::Contained => {
            operations.contained_outcome_readback(binding)?
        }
    };
    if is_zero_digest(&outcome) {
        return Err(AuthorityMaintenanceError(
            "authority_native_transaction_outcome_readback_invalid",
        ));
    }
    let receipt = operations.persist_transaction_terminal(binding, kind, outcome)?;
    Ok((
        NativeSystemTransactionTerminal::new(kind, receipt)?,
        reopened,
    ))
}

fn execute_and_reopen_transaction<O>(
    preview: &AuthorityMaintenancePreview,
    lease: &mut VerifiedMaintenanceLease,
    binding: &NativeSystemTransactionBinding,
    operations: &mut O,
) -> Result<(NativeSystemTransactionTerminalKind, bool), AuthorityMaintenanceError>
where
    O: NativeSystemTransactionOperations,
{
    let mut recovery_reopened = false;
    for attempt in 0..2 {
        let report = execute_with_executor(preview, lease, operations);
        let reported = classify_apply_report(&report);
        let durable = operations.reopen_durable_transaction_journal(binding)?;
        let durable_kind = journal_terminal_kind(durable);
        if let Some(reported_kind) = reported {
            if durable_kind != Some(reported_kind) {
                return Err(AuthorityMaintenanceError(
                    "authority_native_transaction_terminal_not_durable",
                ));
            }
            return Ok((
                reported_kind,
                recovery_reopened || report.failed_step.is_some(),
            ));
        }
        if let Some(durable_kind) = durable_kind {
            return Ok((durable_kind, true));
        }
        recovery_reopened = true;
        if attempt == 1 {
            return Err(AuthorityMaintenanceError(
                "authority_native_transaction_recovery_incomplete",
            ));
        }
    }
    Err(AuthorityMaintenanceError(
        "authority_native_transaction_recovery_incomplete",
    ))
}

fn journal_terminal_kind(
    state: NativeDurableTransactionJournalState,
) -> Option<NativeSystemTransactionTerminalKind> {
    match state {
        NativeDurableTransactionJournalState::Ready => None,
        NativeDurableTransactionJournalState::Committed => {
            Some(NativeSystemTransactionTerminalKind::Committed)
        }
        NativeDurableTransactionJournalState::Contained => {
            Some(NativeSystemTransactionTerminalKind::Contained)
        }
    }
}

fn classify_apply_report(
    report: &AuthorityMaintenanceExecutionReport,
) -> Option<NativeSystemTransactionTerminalKind> {
    if report.status == "committed"
        && report.journal_terminal == Some("committed")
        && report.failed_step.is_none()
        && report.rollback_failures.is_empty()
    {
        Some(NativeSystemTransactionTerminalKind::Committed)
    } else if matches!(report.status, "contained" | "rolledBack")
        && report.journal_terminal == Some(report.status)
        && report.failed_step.is_some()
        && report.failure_cleanup_verified == Some(true)
        && report.rollback_failures.is_empty()
    {
        Some(NativeSystemTransactionTerminalKind::Contained)
    } else {
        None
    }
}

fn derive_service_delete_pending(
    binding: &NativeSystemTransactionBinding,
    terminal: NativeSystemTransactionTerminal,
    exit_ready_receipt_sha256: Digest,
    service_delete_intent_receipt_sha256: Digest,
    transition_kind: NativeServiceDeleteTransitionKind,
    delete_pending_readback_sha256: Digest,
) -> Digest {
    let mut digest = Sha256::new();
    digest.update(SERVICE_DELETE_PENDING_DOMAIN);
    digest.update(binding.capsule_sha256);
    digest.update(terminal.receipt_sha256);
    digest.update(exit_ready_receipt_sha256);
    digest.update(service_delete_intent_receipt_sha256);
    digest.update(binding.exact_service_identity_sha256);
    digest.update(binding.expected_worker_process_sha256);
    digest.update([match transition_kind {
        NativeServiceDeleteTransitionKind::DeleteCallCompleted => 1,
        NativeServiceDeleteTransitionKind::TargetStateObserved => 2,
    }]);
    digest.update(delete_pending_readback_sha256);
    digest.finalize().into()
}

fn derive_service_delete_intent(
    binding: &NativeSystemTransactionBinding,
    terminal: NativeSystemTransactionTerminal,
    exit_ready_receipt_sha256: Digest,
    finalizer: NativeFinalizerProcessIdentity,
) -> Digest {
    let mut digest = Sha256::new();
    digest.update(SERVICE_DELETE_INTENT_DOMAIN);
    digest.update(binding.capsule_sha256);
    digest.update(binding.plan_sha256);
    digest.update(binding.generation_sha256);
    digest.update(binding.transaction_started_receipt_sha256);
    digest.update(terminal.receipt_sha256);
    digest.update(exit_ready_receipt_sha256);
    digest.update(binding.exact_service_identity_sha256);
    digest.update(binding.expected_worker_process_sha256);
    digest.update(finalizer.digest());
    digest.finalize().into()
}

fn derive_finalizer_handles_closed(
    binding: &NativeSystemTransactionBinding,
    service_delete_pending_receipt_sha256: Digest,
    handles_closed_readback_sha256: Digest,
) -> Digest {
    let mut digest = Sha256::new();
    digest.update(FINALIZER_HANDLES_CLOSED_DOMAIN);
    digest.update(binding.capsule_sha256);
    digest.update(service_delete_pending_receipt_sha256);
    digest.update(binding.exact_service_identity_sha256);
    digest.update(binding.expected_worker_process_sha256);
    digest.update(handles_closed_readback_sha256);
    digest.finalize().into()
}

fn derive_service_absent(
    binding: &NativeSystemTransactionBinding,
    terminal: NativeSystemTransactionTerminal,
    finalizer_handles_closed_receipt_sha256: Digest,
    scm_absence_readback_sha256: Digest,
    zero_residue_readback_sha256: Digest,
) -> Digest {
    let mut digest = Sha256::new();
    digest.update(SERVICE_ABSENT_DOMAIN);
    digest.update(binding.capsule_sha256);
    digest.update(binding.generation_sha256);
    digest.update(terminal.receipt_sha256);
    digest.update(finalizer_handles_closed_receipt_sha256);
    digest.update(binding.exact_service_identity_sha256);
    digest.update(binding.expected_worker_process_sha256);
    digest.update(scm_absence_readback_sha256);
    digest.update(zero_residue_readback_sha256);
    digest.finalize().into()
}

fn is_zero_digest(value: &Digest) -> bool {
    value.iter().all(|byte| *byte == 0)
}

#[cfg(test)]
#[path = "system_transaction/tests.rs"]
mod tests;
