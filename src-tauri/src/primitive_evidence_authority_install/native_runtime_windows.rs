use super::{
    native_maintenance_phases,
    receipt_windows::process_security,
    worker::{
        DurableSourceStagingReceipt, FinalizerHandlesClosedReceipt, MaintenanceWorkerCapsule,
        MaintenanceWorkerPhase, ServiceAbsentReceipt, ServiceDeleteIntentReceipt,
        ServiceDeletePendingReceipt, WorkerExitReadyReceipt, WorkerHandleHandoffReceipt,
        WorkerStagingCleanupReceipt,
    },
    worker_store_windows::{
        consume_native_worker_nonce, open_native_worker_bootstrap,
        open_native_worker_bootstrap_for_first_invocation,
        read_native_worker_capsule_for_connection, stage_native_worker_bootstrap,
        NativePersistedPipePrepared, NativeWorkerBootstrapStore,
    },
    worker_windows::{
        close_worker_finalizer_handles, create_start_worker, current_helper_process_binding,
        current_worker_process_binding, finish_worker_service_removal,
        launched_worker_process_binding, live_worker_scm_readback,
        mark_exit_ready_worker_service_delete_pending, observe_service_created_receipt,
        wait_for_worker_transaction_ready, wait_worker_service_absent_after_handles_closed,
        NativeWorkerHandoffClient, NativeWorkerHandoffServer, NativeWorkerServiceLease,
    },
    AuthorityMaintenanceError, AuthorityMaintenanceOperation, NativeMaintenanceBackend,
    NativeMaintenanceContainment, NativeMaintenanceMutationPhase, PreparedNativeInstallWorker,
};
use crate::primitive_evidence_authority_windows::AuthorityLayout;
use windows_sys::Win32::System::Threading::GetCurrentProcess;

#[allow(dead_code)]
#[path = "native_runtime_windows/system_transaction.rs"]
mod system_transaction;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum NativeWorkerServiceState {
    Initial,
    BootstrapPersisted,
    PipePrepared,
    ServiceStarted,
    SourceHandlesBound,
    DurableStagingVerified,
    TransactionStarted,
    SystemExitReady,
    WorkerServiceRemoved,
    CandidateGenerationSealed,
    ActiveHeadAdvanced,
    CommittedRuntimeStarted,
    UpdateSuccessorDormantVerified,
    UpdatePriorRetirementStaged,
    UpdatePriorRetirementFinalized,
    ZeroResidueVerified,
    FinalCommitPersisted,
    PostcommitReadbackVerified,
    RetirementFinalized,
    RetirementZeroResidueVerified,
    RetirementCommitPersisted,
    PostretirementReadbackVerified,
}

pub(super) fn advance_native_worker_service_state(
    state: NativeWorkerServiceState,
    operation: AuthorityMaintenanceOperation,
    phase: NativeMaintenanceMutationPhase,
) -> Result<NativeWorkerServiceState, AuthorityMaintenanceError> {
    if !native_maintenance_phases(operation).contains(&phase) {
        return Err(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ));
    }
    match (state, phase) {
        (NativeWorkerServiceState::Initial, NativeMaintenanceMutationPhase::PersistBootstrap) => {
            Ok(NativeWorkerServiceState::BootstrapPersisted)
        }
        (
            NativeWorkerServiceState::BootstrapPersisted,
            NativeMaintenanceMutationPhase::PrepareFirstPipe,
        ) => Ok(NativeWorkerServiceState::PipePrepared),
        (
            NativeWorkerServiceState::PipePrepared,
            NativeMaintenanceMutationPhase::CreateStartWorker,
        ) => Ok(NativeWorkerServiceState::ServiceStarted),
        (
            NativeWorkerServiceState::ServiceStarted,
            NativeMaintenanceMutationPhase::BindSourceHandles,
        ) => Ok(NativeWorkerServiceState::SourceHandlesBound),
        (
            NativeWorkerServiceState::SourceHandlesBound,
            NativeMaintenanceMutationPhase::PersistSourceStaging,
        ) => Ok(NativeWorkerServiceState::DurableStagingVerified),
        (
            NativeWorkerServiceState::DurableStagingVerified,
            NativeMaintenanceMutationPhase::ConsumeNonceAndStartTransaction,
        ) => Ok(NativeWorkerServiceState::TransactionStarted),
        (
            NativeWorkerServiceState::TransactionStarted,
            NativeMaintenanceMutationPhase::AwaitSystemExitReady,
        ) => Ok(NativeWorkerServiceState::SystemExitReady),
        (
            NativeWorkerServiceState::SystemExitReady,
            NativeMaintenanceMutationPhase::StopWaitDeleteWorker,
        ) => Ok(NativeWorkerServiceState::WorkerServiceRemoved),
        (
            NativeWorkerServiceState::WorkerServiceRemoved,
            NativeMaintenanceMutationPhase::SealCandidateGeneration,
        ) => Ok(NativeWorkerServiceState::CandidateGenerationSealed),
        (
            NativeWorkerServiceState::CandidateGenerationSealed,
            NativeMaintenanceMutationPhase::AdvanceActiveHead,
        ) => Ok(NativeWorkerServiceState::ActiveHeadAdvanced),
        (
            NativeWorkerServiceState::ActiveHeadAdvanced,
            NativeMaintenanceMutationPhase::StartCommittedRuntime,
        ) => Ok(NativeWorkerServiceState::CommittedRuntimeStarted),
        (
            NativeWorkerServiceState::CommittedRuntimeStarted,
            NativeMaintenanceMutationPhase::VerifyZeroResidue,
        ) => Ok(NativeWorkerServiceState::ZeroResidueVerified),
        (
            NativeWorkerServiceState::CommittedRuntimeStarted,
            NativeMaintenanceMutationPhase::VerifyDormantSuccessor,
        ) => Ok(NativeWorkerServiceState::UpdateSuccessorDormantVerified),
        (
            NativeWorkerServiceState::UpdateSuccessorDormantVerified,
            NativeMaintenanceMutationPhase::StagePriorRetirement,
        ) => Ok(NativeWorkerServiceState::UpdatePriorRetirementStaged),
        (
            NativeWorkerServiceState::UpdatePriorRetirementStaged,
            NativeMaintenanceMutationPhase::FinalizePriorRetirement,
        ) => Ok(NativeWorkerServiceState::UpdatePriorRetirementFinalized),
        (
            NativeWorkerServiceState::UpdatePriorRetirementFinalized,
            NativeMaintenanceMutationPhase::VerifyUpdateZeroResidue,
        ) => Ok(NativeWorkerServiceState::ZeroResidueVerified),
        (
            NativeWorkerServiceState::ZeroResidueVerified,
            NativeMaintenanceMutationPhase::PersistFinalCommit,
        ) => Ok(NativeWorkerServiceState::FinalCommitPersisted),
        (
            NativeWorkerServiceState::FinalCommitPersisted,
            NativeMaintenanceMutationPhase::VerifyPostcommitReadback,
        ) => Ok(NativeWorkerServiceState::PostcommitReadbackVerified),
        (
            NativeWorkerServiceState::WorkerServiceRemoved,
            NativeMaintenanceMutationPhase::FinalizeRetirement,
        ) => Ok(NativeWorkerServiceState::RetirementFinalized),
        (
            NativeWorkerServiceState::RetirementFinalized,
            NativeMaintenanceMutationPhase::VerifyRetirementZeroResidue,
        ) => Ok(NativeWorkerServiceState::RetirementZeroResidueVerified),
        (
            NativeWorkerServiceState::RetirementZeroResidueVerified,
            NativeMaintenanceMutationPhase::PersistRetirementCommit,
        ) => Ok(NativeWorkerServiceState::RetirementCommitPersisted),
        (
            NativeWorkerServiceState::RetirementCommitPersisted,
            NativeMaintenanceMutationPhase::VerifyPostretirementReadback,
        ) => Ok(NativeWorkerServiceState::PostretirementReadbackVerified),
        _ => Err(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        )),
    }
}

/// Owns every helper-side handle in the exact order in which the temporary
/// maintenance service is allowed to become visible. The production mutation
/// gate is checked by the parent before this backend can receive any phase.
pub(super) struct NativeHelperWorkerBackend {
    layout: AuthorityLayout,
    state: NativeWorkerServiceState,
    store: Option<NativeWorkerBootstrapStore>,
    pipe_server: Option<NativeWorkerHandoffServer>,
    persisted_pipe: Option<NativePersistedPipePrepared>,
    service: Option<NativeWorkerServiceLease>,
    handoff: Option<WorkerHandleHandoffReceipt>,
    staging: Option<DurableSourceStagingReceipt>,
    exit_ready: Option<WorkerExitReadyReceipt>,
}

trait NativeBeforeTransactionContainmentOperations {
    fn recover_store(&mut self) -> Result<(), AuthorityMaintenanceError>;
    fn close_pipe(&mut self) -> Result<(), AuthorityMaintenanceError>;
    fn stop_wait_delete_worker(&mut self) -> Result<(), AuthorityMaintenanceError>;
    fn contain_partial_staging(&mut self) -> Result<(), AuthorityMaintenanceError>;
    fn verify_zero_residue(&mut self) -> Result<(), AuthorityMaintenanceError>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum NativeBeforeTransactionContainmentPhase {
    RecoverStore,
    ClosePipe,
    StopWaitDeleteWorker,
    ContainPartialStaging,
    VerifyZeroResidue,
}

const NATIVE_BEFORE_TRANSACTION_CONTAINMENT_PHASES: [NativeBeforeTransactionContainmentPhase; 5] = [
    NativeBeforeTransactionContainmentPhase::RecoverStore,
    NativeBeforeTransactionContainmentPhase::ClosePipe,
    NativeBeforeTransactionContainmentPhase::StopWaitDeleteWorker,
    NativeBeforeTransactionContainmentPhase::ContainPartialStaging,
    NativeBeforeTransactionContainmentPhase::VerifyZeroResidue,
];

#[derive(Debug, Clone, PartialEq, Eq)]
struct NativeBeforeTransactionCleanupFailure {
    phase: NativeBeforeTransactionContainmentPhase,
    code: &'static str,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct NativeBeforeTransactionContainmentFailure {
    primary: Option<&'static str>,
    cleanup: Vec<NativeBeforeTransactionCleanupFailure>,
}

impl NativeBeforeTransactionContainmentFailure {
    fn new() -> Self {
        Self {
            primary: None,
            cleanup: Vec::new(),
        }
    }

    fn record(
        &mut self,
        phase: NativeBeforeTransactionContainmentPhase,
        error: AuthorityMaintenanceError,
    ) {
        if phase == NativeBeforeTransactionContainmentPhase::RecoverStore {
            self.primary = Some(error.code());
        } else {
            self.cleanup.push(NativeBeforeTransactionCleanupFailure {
                phase,
                code: error.code(),
            });
        }
    }

    fn into_authority_error(self) -> AuthorityMaintenanceError {
        match (self.primary.is_some(), self.cleanup.is_empty()) {
            (true, false) => {
                AuthorityMaintenanceError("authority_native_worker_recovery_and_cleanup_failed")
            }
            (true, true) => {
                AuthorityMaintenanceError("authority_native_worker_recovery_failed_after_cleanup")
            }
            (false, false) => AuthorityMaintenanceError("authority_native_worker_cleanup_failed"),
            (false, true) => {
                AuthorityMaintenanceError("authority_native_worker_containment_failure_invalid")
            }
        }
    }
}

fn run_before_transaction_containment<O>(
    operations: &mut O,
) -> Result<(), NativeBeforeTransactionContainmentFailure>
where
    O: NativeBeforeTransactionContainmentOperations,
{
    let mut failure = NativeBeforeTransactionContainmentFailure::new();
    for phase in NATIVE_BEFORE_TRANSACTION_CONTAINMENT_PHASES {
        let result = match phase {
            NativeBeforeTransactionContainmentPhase::RecoverStore => operations.recover_store(),
            NativeBeforeTransactionContainmentPhase::ClosePipe => operations.close_pipe(),
            NativeBeforeTransactionContainmentPhase::StopWaitDeleteWorker => {
                operations.stop_wait_delete_worker()
            }
            NativeBeforeTransactionContainmentPhase::ContainPartialStaging => {
                operations.contain_partial_staging()
            }
            NativeBeforeTransactionContainmentPhase::VerifyZeroResidue => {
                operations.verify_zero_residue()
            }
        };
        if let Err(error) = result {
            failure.record(phase, error);
        }
    }
    if failure.primary.is_none() && failure.cleanup.is_empty() {
        Ok(())
    } else {
        Err(failure)
    }
}

struct NativeBeforeTransactionContainment<'a> {
    backend: &'a mut NativeHelperWorkerBackend,
    prepared: &'a PreparedNativeInstallWorker,
    recovered_store: Option<NativeWorkerBootstrapStore>,
    recovery_required: bool,
    recovery_verified: bool,
    pipe_closed: bool,
    service_absence_verified: bool,
    staging_absence_verified: bool,
}

impl NativeBeforeTransactionContainmentOperations for NativeBeforeTransactionContainment<'_> {
    fn recover_store(&mut self) -> Result<(), AuthorityMaintenanceError> {
        if !self.recovery_required {
            self.recovery_verified = true;
            return Ok(());
        }
        let (capsule, store) =
            open_native_worker_bootstrap(&self.backend.layout, self.prepared.capsule_sha256)?;
        if capsule != self.prepared.capsule {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_capsule_mismatch",
            ));
        }
        self.recovered_store = Some(store);
        self.recovery_verified = true;
        Ok(())
    }

    fn close_pipe(&mut self) -> Result<(), AuthorityMaintenanceError> {
        drop(self.backend.pipe_server.take());
        drop(self.backend.persisted_pipe.take());
        self.pipe_closed =
            self.backend.pipe_server.is_none() && self.backend.persisted_pipe.is_none();
        if self.pipe_closed {
            Ok(())
        } else {
            Err(AuthorityMaintenanceError(
                "authority_native_worker_pipe_cleanup_failed",
            ))
        }
    }

    fn stop_wait_delete_worker(&mut self) -> Result<(), AuthorityMaintenanceError> {
        let result = match self.backend.service.take() {
            Some(service) => finish_worker_service_removal(service).map(|_| ()),
            None => Ok(()),
        };
        self.service_absence_verified = result.is_ok();
        result
    }

    fn contain_partial_staging(&mut self) -> Result<(), AuthorityMaintenanceError> {
        if self.recovery_required && !self.recovery_verified {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_recovery_store_unavailable_for_cleanup",
            ));
        }
        let Some(store) = self
            .recovered_store
            .as_mut()
            .or(self.backend.store.as_mut())
        else {
            self.staging_absence_verified = !matches!(
                self.backend.state,
                NativeWorkerServiceState::SourceHandlesBound
                    | NativeWorkerServiceState::DurableStagingVerified
                    | NativeWorkerServiceState::TransactionStarted
                    | NativeWorkerServiceState::SystemExitReady
                    | NativeWorkerServiceState::WorkerServiceRemoved
                    | NativeWorkerServiceState::CandidateGenerationSealed
                    | NativeWorkerServiceState::ActiveHeadAdvanced
                    | NativeWorkerServiceState::CommittedRuntimeStarted
                    | NativeWorkerServiceState::UpdateSuccessorDormantVerified
                    | NativeWorkerServiceState::UpdatePriorRetirementStaged
                    | NativeWorkerServiceState::UpdatePriorRetirementFinalized
                    | NativeWorkerServiceState::ZeroResidueVerified
                    | NativeWorkerServiceState::FinalCommitPersisted
                    | NativeWorkerServiceState::PostcommitReadbackVerified
                    | NativeWorkerServiceState::RetirementFinalized
                    | NativeWorkerServiceState::RetirementZeroResidueVerified
                    | NativeWorkerServiceState::RetirementCommitPersisted
                    | NativeWorkerServiceState::PostretirementReadbackVerified
            );
            return if self.staging_absence_verified {
                Ok(())
            } else {
                Err(AuthorityMaintenanceError(
                    "authority_native_worker_staging_cleanup_unavailable",
                ))
            };
        };
        let requires_cleanup = store.records().last().is_some_and(|record| {
            matches!(
                record.phase(),
                MaintenanceWorkerPhase::SourceStagingIntent
                    | MaintenanceWorkerPhase::SourceHandlesBound
            )
        });
        let result = if requires_cleanup {
            store
                .contain_partial_native_worker_staging(&self.backend.layout, &self.prepared.capsule)
                .map(|_| ())
        } else {
            Ok(())
        };
        self.staging_absence_verified = result.is_ok();
        result
    }

    fn verify_zero_residue(&mut self) -> Result<(), AuthorityMaintenanceError> {
        drop(self.recovered_store.take());
        drop(self.backend.store.take());
        drop(self.backend.pipe_server.take());
        drop(self.backend.persisted_pipe.take());
        drop(self.backend.service.take());
        self.backend.handoff = None;
        self.backend.staging = None;
        let local_handles_absent = self.backend.store.is_none()
            && self.backend.pipe_server.is_none()
            && self.backend.persisted_pipe.is_none()
            && self.backend.service.is_none()
            && self.recovered_store.is_none();
        if self.pipe_closed
            && self.service_absence_verified
            && self.staging_absence_verified
            && local_handles_absent
        {
            Ok(())
        } else {
            Err(AuthorityMaintenanceError(
                "authority_native_worker_zero_residue_unverified",
            ))
        }
    }
}

impl<'a> NativeBeforeTransactionContainment<'a> {
    fn new(
        backend: &'a mut NativeHelperWorkerBackend,
        prepared: &'a PreparedNativeInstallWorker,
    ) -> Self {
        let recovery_required = backend.handoff.is_some();
        let service_absence_verified = backend.service.is_none();
        Self {
            backend,
            prepared,
            recovered_store: None,
            recovery_required,
            recovery_verified: !recovery_required,
            pipe_closed: false,
            service_absence_verified,
            staging_absence_verified: false,
        }
    }
}

impl NativeHelperWorkerBackend {
    pub(super) fn new(layout: AuthorityLayout) -> Self {
        Self {
            layout,
            state: NativeWorkerServiceState::Initial,
            store: None,
            pipe_server: None,
            persisted_pipe: None,
            service: None,
            handoff: None,
            staging: None,
            exit_ready: None,
        }
    }

    fn persist_bootstrap(
        &mut self,
        prepared: &PreparedNativeInstallWorker,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.store.is_some() {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_state_invalid",
            ));
        }
        self.store = Some(stage_native_worker_bootstrap(
            &self.layout,
            &prepared.capsule,
            &prepared.capsule_bytes,
            &prepared.lease,
        )?);
        Ok(())
    }

    fn prepare_first_pipe(
        &mut self,
        prepared: &PreparedNativeInstallWorker,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.pipe_server.is_some() || self.persisted_pipe.is_some() {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_state_invalid",
            ));
        }
        let store = self.store.as_mut().ok_or(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ))?;
        let pipe = NativeWorkerHandoffServer::create(&prepared.capsule, store.launch())?;
        let persisted = store.persist_pipe_prepared_receipt(
            &self.layout,
            &prepared.capsule,
            pipe.prepared_receipt(),
        )?;
        self.pipe_server = Some(pipe);
        self.persisted_pipe = Some(persisted);
        Ok(())
    }

    fn create_start_worker(
        &mut self,
        prepared: &PreparedNativeInstallWorker,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.service.is_some() {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_state_invalid",
            ));
        }
        let pipe = self.pipe_server.as_ref().ok_or(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ))?;
        let persisted_pipe = self.persisted_pipe.take().ok_or(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ))?;
        let store = self.store.as_mut().ok_or(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ))?;
        let lease = create_start_worker(&prepared.capsule, store.launch(), pipe, &persisted_pipe)?;
        self.service = Some(lease);
        let service = self.service.as_ref().ok_or(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ))?;
        let receipt = observe_service_created_receipt(
            &prepared.capsule,
            store.launch(),
            store.bootstrap(),
            persisted_pipe.receipt(),
            service,
        )?;
        store.record_service_created(&self.layout, &prepared.capsule, persisted_pipe, receipt)
    }

    fn bind_source_handles(
        &mut self,
        prepared: &PreparedNativeInstallWorker,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.handoff.is_some() {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_state_invalid",
            ));
        }
        let store = self.store.as_mut().ok_or(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ))?;
        let pipe = self.pipe_server.as_mut().ok_or(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ))?;
        let service = self.service.as_ref().ok_or(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ))?;
        pipe.accept_exact_worker(service)?;
        let worker = launched_worker_process_binding(&prepared.capsule, store.launch(), service)?;
        let duplicated =
            service.duplicate_source_handles(prepared.lease.native_source_handles()?)?;
        let helper = current_helper_process_binding(&prepared.capsule)?;
        let handoff = WorkerHandleHandoffReceipt::from_observed(
            &prepared.capsule,
            helper,
            worker,
            pipe.prepared_receipt(),
            duplicated.values(),
        )?;
        store.persist_handoff_receipt(&self.layout, &prepared.capsule, handoff.clone())?;
        pipe.send_handoff(&prepared.capsule, &handoff, service)?;
        let transferred = duplicated.transfer();
        if transferred != handoff.duplicated_target_handle_values() {
            return Err(AuthorityMaintenanceError(
                "authority_worker_duplicated_handle_value_invalid",
            ));
        }
        self.handoff = Some(handoff);
        Ok(())
    }

    fn verify_durable_staging(
        &mut self,
        prepared: &PreparedNativeInstallWorker,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.staging.is_some() {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_state_invalid",
            ));
        }
        let store = self.store.as_mut().ok_or(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ))?;
        let pipe = self.pipe_server.as_ref().ok_or(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ))?;
        let service = self.service.as_ref().ok_or(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ))?;
        let frame = pipe.receive_durable_staging_frame(service)?;
        let staging =
            store.refresh_after_external_worker_staging(&self.layout, &prepared.capsule, &frame)?;
        let worker_started = store
            .worker_started_receipt()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_started_receipt_not_persisted",
            ))?;
        live_worker_scm_readback(
            &prepared.capsule,
            store.launch(),
            worker_started,
            service,
            pipe,
        )?;
        // The SYSTEM actor owns terminal staging resolution. The elevated
        // helper must release its temporary readback handles before ACK lets
        // that actor proceed to deletion.
        store.release_external_staging_readback_handles()?;
        pipe.acknowledge_durable_staging(&prepared.capsule, &staging, service)?;
        self.staging = Some(staging);
        Ok(())
    }

    fn observe_durable_transaction_start(
        &mut self,
        prepared: &PreparedNativeInstallWorker,
    ) -> Result<(), AuthorityMaintenanceError> {
        let service = self.service.as_ref().ok_or(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ))?;
        let contract = self
            .store
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_native_worker_state_invalid",
            ))?
            .launch();
        wait_for_worker_transaction_ready(&prepared.capsule, contract, service)?;

        drop(self.store.take());
        let (capsule, mut store) =
            open_native_worker_bootstrap(&self.layout, prepared.capsule.digest()?)?;
        if capsule != prepared.capsule {
            return Err(AuthorityMaintenanceError(
                "authority_worker_connection_capsule_changed",
            ));
        }
        match capsule.operation() {
            AuthorityMaintenanceOperation::Install | AuthorityMaintenanceOperation::Update => {
                let binding = store.candidate_activation_receipt_binding(&capsule)?;
                if binding.capsule_sha256 != capsule.digest()?
                    || binding.plan_sha256 != capsule.plan_sha256()?
                    || binding.generation_sha256 != capsule.generation()?
                    || binding.transaction_sha256 != capsule.transaction_sha256()?
                {
                    return Err(AuthorityMaintenanceError(
                        "authority_candidate_activation_receipt_binding_mismatch",
                    ));
                }
            }
            AuthorityMaintenanceOperation::Retire => {
                let binding = store.retirement_transaction_receipt_binding(&capsule)?;
                if binding.capsule_sha256 != capsule.digest()?
                    || binding.plan_sha256 != capsule.plan_sha256()?
                    || binding.generation_sha256 != capsule.generation()?
                    || binding.transaction_sha256 != capsule.transaction_sha256()?
                {
                    return Err(AuthorityMaintenanceError(
                        "authority_retirement_transaction_binding_mismatch",
                    ));
                }
            }
        }
        // Recovery opens the durable source files for exact readback. The
        // helper must immediately release those read handles so the SYSTEM
        // transaction remains the only actor able to resolve source staging.
        store.release_reopened_staging_readback_handles()?;
        self.store = Some(store);
        Ok(())
    }

    fn await_durable_exit_ready(
        &mut self,
        prepared: &PreparedNativeInstallWorker,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.exit_ready.is_some() {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_state_invalid",
            ));
        }
        let frame = self
            .pipe_server
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_native_worker_state_invalid",
            ))?
            .receive_exit_ready_frame(self.service.as_ref().ok_or(AuthorityMaintenanceError(
                "authority_native_worker_state_invalid",
            ))?)?;

        // The SYSTEM actor durably appended the terminal, source-resolution,
        // and ExitReady records. Discard the helper's stale in-memory prefix
        // and reopen the exact capsule before trusting the pipe frame.
        drop(self.store.take());
        let (capsule, store) =
            open_native_worker_bootstrap(&self.layout, prepared.capsule.digest()?)?;
        if capsule != prepared.capsule {
            return Err(AuthorityMaintenanceError(
                "authority_worker_connection_capsule_changed",
            ));
        }
        let terminal = store
            .records()
            .iter()
            .find(|record| record.phase().is_transaction_terminal())
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_transaction_terminal_missing",
            ))?;
        let cleanup = store
            .staging_cleanup_receipt()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_cleanup_receipt_not_persisted",
            ))?;
        let worker_started = store
            .worker_started_receipt()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_started_receipt_not_persisted",
            ))?;
        let exit_ready = WorkerExitReadyReceipt::parse_sealed_canonical(&frame)?;
        exit_ready.validate(&capsule, terminal, cleanup, worker_started)?;
        if store.exit_ready_receipt() != Some(&exit_ready)
            || exit_ready.worker()
                != self
                    .service
                    .as_ref()
                    .ok_or(AuthorityMaintenanceError(
                        "authority_native_worker_state_invalid",
                    ))?
                    .process_binding()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_exit_ready_readback_mismatch",
            ));
        }
        self.store = Some(store);
        self.exit_ready = Some(exit_ready);
        Ok(())
    }

    fn finalize_exit_ready_worker(
        &mut self,
        prepared: &PreparedNativeInstallWorker,
    ) -> Result<(), AuthorityMaintenanceError> {
        let capsule = &prepared.capsule;
        let store = self.store.as_mut().ok_or(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ))?;
        let terminal = store
            .records()
            .iter()
            .find(|record| record.phase().is_transaction_terminal())
            .cloned()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_transaction_terminal_missing",
            ))?;
        let cleanup = store
            .staging_cleanup_receipt()
            .cloned()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_cleanup_receipt_not_persisted",
            ))?;
        let worker_started =
            store
                .worker_started_receipt()
                .cloned()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_started_receipt_not_persisted",
                ))?;
        let exit_ready = self
            .exit_ready
            .as_ref()
            .cloned()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_exit_ready_receipt_not_persisted",
            ))?;
        if store.exit_ready_receipt() != Some(&exit_ready) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_exit_ready_readback_mismatch",
            ));
        }
        let finalizer = current_helper_process_binding(capsule)?;
        let service_created =
            store
                .service_created_receipt()
                .cloned()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_service_receipt_not_persisted",
                ))?;
        let delete_intent = ServiceDeleteIntentReceipt::from_observed(
            capsule,
            store.launch(),
            &service_created,
            &exit_ready,
            finalizer,
        )?;
        store.record_service_delete_intent_after_exit_ready(
            &self.layout,
            capsule,
            delete_intent.clone(),
        )?;
        let lease = self.service.take().ok_or(AuthorityMaintenanceError(
            "authority_native_worker_state_invalid",
        ))?;
        let delete_pending_service = mark_exit_ready_worker_service_delete_pending(
            capsule,
            &terminal,
            &cleanup,
            &worker_started,
            &exit_ready,
            lease,
        )?;
        let delete_pending = ServiceDeletePendingReceipt::from_delete_call(
            capsule,
            terminal.phase_receipt_sha256()?,
            &delete_intent,
            delete_pending_service.delete_pending_readback_sha256(),
        )?;
        if store
            .record_service_delete_pending_after_intent(
                &self.layout,
                capsule,
                delete_pending.clone(),
            )
            .is_err()
        {
            // The durable intent precedes DeleteService. Recovery may now
            // observe the target state and persist that fact without claiming
            // whether this process completed the API call.
            drop(self.pipe_server.take());
            drop(delete_pending_service);
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_delete_transition_recovery_required",
            ));
        }

        // The handles-closed boundary covers both SCM/process handles and the
        // one helper-side pipe handle. It is persisted only after all are gone.
        drop(self.pipe_server.take());
        let finalizer_handles_closed = close_worker_finalizer_handles(delete_pending_service)?;
        let handles_closed = FinalizerHandlesClosedReceipt::from_observed(
            capsule,
            &exit_ready,
            &delete_pending,
            finalizer_handles_closed.handles_closed_readback_sha256(),
        )?;
        store.record_finalizer_handles_closed(&self.layout, capsule, handles_closed)?;

        let absence = wait_worker_service_absent_after_handles_closed(&finalizer_handles_closed)?;
        let service_absent = ServiceAbsentReceipt::from_observed(
            capsule,
            &delete_pending,
            &cleanup,
            absence.service_absence_readback_sha256,
        )?;
        store.record_service_absent_after_handles_closed(&self.layout, capsule, service_absent)?;
        Ok(())
    }

    fn contain_before_transaction(
        &mut self,
        prepared: &PreparedNativeInstallWorker,
    ) -> Result<(), AuthorityMaintenanceError> {
        let mut containment = NativeBeforeTransactionContainment::new(self, prepared);
        run_before_transaction_containment(&mut containment)
            .map_err(NativeBeforeTransactionContainmentFailure::into_authority_error)
    }
}

impl NativeMaintenanceBackend for NativeHelperWorkerBackend {
    fn apply_phase(
        &mut self,
        prepared: &mut PreparedNativeInstallWorker,
        operation: AuthorityMaintenanceOperation,
        phase: NativeMaintenanceMutationPhase,
    ) -> Result<(), AuthorityMaintenanceError> {
        if operation != prepared.capsule.operation() {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_operation_mismatch",
            ));
        }
        let next_state = advance_native_worker_service_state(self.state, operation, phase)?;
        let result = match phase {
            NativeMaintenanceMutationPhase::PersistBootstrap => self.persist_bootstrap(prepared),
            NativeMaintenanceMutationPhase::PrepareFirstPipe => self.prepare_first_pipe(prepared),
            NativeMaintenanceMutationPhase::CreateStartWorker => self.create_start_worker(prepared),
            NativeMaintenanceMutationPhase::BindSourceHandles => self.bind_source_handles(prepared),
            NativeMaintenanceMutationPhase::PersistSourceStaging => {
                self.verify_durable_staging(prepared)
            }
            NativeMaintenanceMutationPhase::ConsumeNonceAndStartTransaction => {
                if self.staging.is_some()
                    && self.store.is_some()
                    && self.pipe_server.is_some()
                    && self.service.is_some()
                {
                    self.observe_durable_transaction_start(prepared)
                } else {
                    Err(AuthorityMaintenanceError(
                        "authority_native_worker_state_invalid",
                    ))
                }
            }
            NativeMaintenanceMutationPhase::AwaitSystemExitReady => {
                self.await_durable_exit_ready(prepared)
            }
            NativeMaintenanceMutationPhase::StopWaitDeleteWorker => {
                self.finalize_exit_ready_worker(prepared)
            }
            NativeMaintenanceMutationPhase::SealCandidateGeneration
            | NativeMaintenanceMutationPhase::AdvanceActiveHead
            | NativeMaintenanceMutationPhase::StartCommittedRuntime
            | NativeMaintenanceMutationPhase::VerifyDormantSuccessor
            | NativeMaintenanceMutationPhase::StagePriorRetirement
            | NativeMaintenanceMutationPhase::FinalizePriorRetirement
            | NativeMaintenanceMutationPhase::PersistFinalCommit
            | NativeMaintenanceMutationPhase::VerifyZeroResidue
            | NativeMaintenanceMutationPhase::VerifyUpdateZeroResidue
            | NativeMaintenanceMutationPhase::VerifyPostcommitReadback => {
                Err(AuthorityMaintenanceError(
                    "authority_native_finalizer_owned_commit_protocol_missing",
                ))
            }
            NativeMaintenanceMutationPhase::FinalizeRetirement
            | NativeMaintenanceMutationPhase::VerifyRetirementZeroResidue
            | NativeMaintenanceMutationPhase::PersistRetirementCommit
            | NativeMaintenanceMutationPhase::VerifyPostretirementReadback => Err(
                AuthorityMaintenanceError("authority_native_retirement_protocol_missing"),
            ),
        };
        if result.is_ok() {
            self.state = next_state;
        }
        result
    }

    fn contain_failure(
        &mut self,
        prepared: &mut PreparedNativeInstallWorker,
        _operation: AuthorityMaintenanceOperation,
        _failed_phase: NativeMaintenanceMutationPhase,
        containment: NativeMaintenanceContainment,
    ) -> Result<(), AuthorityMaintenanceError> {
        match containment {
            NativeMaintenanceContainment::BeforeTransaction => {
                self.contain_before_transaction(prepared)
            }
            NativeMaintenanceContainment::InterruptedTransaction => Err(AuthorityMaintenanceError(
                "authority_native_transaction_recovery_not_connected",
            )),
            NativeMaintenanceContainment::TransactionOutcomeBound => {
                Err(AuthorityMaintenanceError(
                    "authority_native_transaction_outcome_recovery_not_connected",
                ))
            }
            NativeMaintenanceContainment::FinalizerBeforeSeal => Err(AuthorityMaintenanceError(
                "authority_native_finalizer_before_seal_recovery_not_connected",
            )),
            NativeMaintenanceContainment::ProbeSealCompleteDurability => {
                Err(AuthorityMaintenanceError(
                    "authority_native_seal_complete_durability_probe_not_connected",
                ))
            }
            NativeMaintenanceContainment::ResumeFromSealComplete => Err(AuthorityMaintenanceError(
                "authority_native_seal_complete_roll_forward_not_connected",
            )),
            NativeMaintenanceContainment::ResumeUpdatePriorRetirement => {
                Err(AuthorityMaintenanceError(
                    "authority_native_update_prior_retirement_resume_not_connected",
                ))
            }
            NativeMaintenanceContainment::ProbeUpdateRetirementDurability => {
                Err(AuthorityMaintenanceError(
                    "authority_native_update_retirement_durability_probe_not_connected",
                ))
            }
            NativeMaintenanceContainment::ResumeUpdateAfterRetirementCommit => {
                Err(AuthorityMaintenanceError(
                    "authority_native_update_after_retirement_roll_forward_not_connected",
                ))
            }
            NativeMaintenanceContainment::ProbeFinalCommitDurability => {
                Err(AuthorityMaintenanceError(
                    "authority_native_final_commit_durability_probe_not_connected",
                ))
            }
            NativeMaintenanceContainment::ResumeCommittedRuntimeAndVerify => {
                Err(AuthorityMaintenanceError(
                    "authority_native_committed_runtime_recovery_not_connected",
                ))
            }
            NativeMaintenanceContainment::ProbeRetirementCommitDurability => {
                Err(AuthorityMaintenanceError(
                    "authority_native_retirement_commit_durability_probe_not_connected",
                ))
            }
            NativeMaintenanceContainment::ResumeRetirementCommit => Err(AuthorityMaintenanceError(
                "authority_native_retirement_roll_forward_not_connected",
            )),
            NativeMaintenanceContainment::ReadOnlyRetirementVerification => Err(
                AuthorityMaintenanceError("authority_native_retirement_readback_not_connected"),
            ),
        }
    }
}

/// A SYSTEM-side transaction context can exist only after the original
/// helper's persisted service receipt, one-use invocation claim, exact source
/// staging readback, helper ACK, and nonce consumption are all durable.
pub(super) struct NativeSystemWorkerTransactionContext {
    pub(super) capsule: MaintenanceWorkerCapsule,
    pub(super) store: NativeWorkerBootstrapStore,
    pub(super) pipe_client: NativeWorkerHandoffClient,
    pub(super) staging: DurableSourceStagingReceipt,
}

impl NativeSystemWorkerTransactionContext {
    pub(super) fn resolve_source_stage_and_persist_exit_ready(
        &mut self,
        layout: &AuthorityLayout,
        disposition: super::worker::WorkerStagingTerminalDisposition,
        adopted_generation_readback_sha256: Option<[u8; 32]>,
        containment_seal_sha256: Option<[u8; 32]>,
    ) -> Result<WorkerExitReadyReceipt, AuthorityMaintenanceError> {
        // SealReady may only be reached after the one-use nonce receipt and
        // its namespace have been reopened with read-only handles. Consuming
        // the prior lease closes both FILE_WRITE_DATA and FILE_ADD_FILE
        // capabilities before any finalizer-owned sealing can begin.
        self.store
            .reopen_nonce_consumption_readonly_before_seal_ready(layout, &self.capsule)?;
        let cleanup = self
            .store
            .resolve_native_worker_source_staging_after_terminal(
                layout,
                &self.capsule,
                disposition,
                adopted_generation_readback_sha256,
                containment_seal_sha256,
            )?;
        self.persist_and_send_exit_ready(layout, &cleanup)
    }

    fn persist_and_send_exit_ready(
        &mut self,
        layout: &AuthorityLayout,
        cleanup: &WorkerStagingCleanupReceipt,
    ) -> Result<WorkerExitReadyReceipt, AuthorityMaintenanceError> {
        let terminal = self
            .store
            .records()
            .iter()
            .find(|record| record.phase().is_transaction_terminal())
            .cloned()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_transaction_terminal_missing",
            ))?;
        let worker_started =
            self.store
                .worker_started_receipt()
                .cloned()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_started_receipt_not_persisted",
                ))?;
        let exit_ready = WorkerExitReadyReceipt::from_observed(
            &self.capsule,
            &terminal,
            cleanup,
            &worker_started,
        )?;
        self.store
            .record_exit_ready(layout, &self.capsule, exit_ready.clone())?;
        self.pipe_client.send_exit_ready(
            &self.capsule,
            &terminal,
            cleanup,
            &worker_started,
            &exit_ready,
        )?;
        Ok(exit_ready)
    }
}

pub(super) fn prepare_native_system_worker_transaction(
    layout: &AuthorityLayout,
    capsule_sha256: [u8; 32],
    now_unix_millis: u64,
) -> Result<NativeSystemWorkerTransactionContext, AuthorityMaintenanceError> {
    let connection_capsule = read_native_worker_capsule_for_connection(layout, capsule_sha256)?;
    connection_capsule.validate_consent_at(now_unix_millis)?;
    let mut pipe_client = NativeWorkerHandoffClient::connect(&connection_capsule)?;
    let connection_launch =
        super::worker::MaintenanceWorkerLaunchContract::new(layout, &connection_capsule)?;
    let current_worker = current_worker_process_binding(&connection_capsule, &connection_launch)?;
    let handoff = pipe_client.receive_handoff(&connection_capsule, &current_worker)?;

    let (capsule, mut store) =
        open_native_worker_bootstrap_for_first_invocation(layout, capsule_sha256)?;
    if capsule != connection_capsule || store.launch() != &connection_launch {
        return Err(AuthorityMaintenanceError(
            "authority_worker_connection_capsule_changed",
        ));
    }
    let security = process_security(unsafe { GetCurrentProcess() })?;
    if !security.local_system || !security.high_integrity || security.session_id != 0 {
        return Err(AuthorityMaintenanceError(
            "authority_worker_system_identity_required",
        ));
    }
    store.claim_and_record_current_worker_started(
        layout,
        &capsule,
        &current_worker,
        security.local_system,
        security.high_integrity,
        security.session_id,
    )?;
    let worker_started = store
        .records()
        .last()
        .filter(|record| record.phase() == MaintenanceWorkerPhase::WorkerStarted)
        .cloned()
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_started_receipt_not_persisted",
        ))?;
    let source_handles = pipe_client.adopt_duplicated_source_handles(&handoff)?;
    let staging_result = store.stage_native_worker_sources(
        layout,
        &capsule,
        &worker_started,
        &handoff,
        source_handles,
    );
    let staging =
        match staging_result {
            Ok(value) => value,
            Err(error) => {
                if store.records().last().is_some_and(|record| {
                    record.phase() == MaintenanceWorkerPhase::SourceStagingIntent
                }) {
                    store
                        .contain_partial_native_worker_staging(layout, &capsule)
                        .map_err(|_| {
                            AuthorityMaintenanceError(
                                "authority_worker_partial_staging_containment_failed",
                            )
                        })?;
                }
                return Err(error);
            }
        };
    pipe_client.send_durable_staging(&capsule, &worker_started, &handoff, &staging)?;
    pipe_client.receive_durable_staging_ack(&capsule, &staging)?;
    let nonce = consume_native_worker_nonce(layout, &capsule, now_unix_millis)?;
    store.authorize_native_transaction_start(layout, &capsule, nonce, now_unix_millis)?;
    Ok(NativeSystemWorkerTransactionContext {
        capsule,
        store,
        pipe_client,
        staging,
    })
}

#[cfg(test)]
#[path = "native_runtime_windows/containment_tests.rs"]
mod containment_tests;
