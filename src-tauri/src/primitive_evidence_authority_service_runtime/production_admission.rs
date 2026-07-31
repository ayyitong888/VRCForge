use crate::primitive_evidence_authority_contract::{
    AuthorityPeerBinding, AuthorityPeerBindingVerifier, ContractError, FixedControllerCommand,
    FixedModelPartHandleAdmission, InstalledControllerAdmission,
};
use crate::primitive_evidence_authority_install::bootstrap::GenerationBoundProtectedExecutableHandles;
#[cfg(test)]
use crate::primitive_evidence_authority_pipe::EXTERNAL_MODEL_PART_HANDLE_COUNT;
use crate::primitive_evidence_authority_pipe::{
    ActiveScenarioHandleBundle, AuthenticatedControllerCapability, ExternalModelPartHandleTokens,
    FixedScenarioHandleSnapshot, InstalledControllerCommandIntent,
    ValidatedExternalScenarioHandleBundle, VerifiedScenarioStartContract,
    WorkerScenarioHandleBundle, FIXED_MODEL_PART_HANDLE_COUNT,
};
use crate::primitive_evidence_authority_runtime::{
    AuthorityRuntimeIdentity, RuntimeDependencyError, RuntimeRunContext, RuntimeTicketRef,
};
use crate::primitive_evidence_authority_supervisor::native_windows::background::{
    BackgroundNativeStartSink, BackgroundTerminalLease, OwnedBackgroundRun,
};
use crate::primitive_evidence_authority_supervisor::native_windows::ValidatedNativeTerminalRun;
use crate::primitive_evidence_authority_supervisor::native_windows::WindowsNativeSupervisorApi;
use crate::primitive_evidence_authority_supervisor::{derive_run_binding_digest, PreparedRun};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    sync::{Arc, Mutex, MutexGuard},
};

use super::SharedAuthenticatedFinalCommitBoundary;

const ADMISSION_EMPTY: &str = "authority_production_run_admission_empty";
const ADMISSION_BUSY: &str = "authority_production_run_admission_busy";
const ADMISSION_ABORTED: &str = "authority_production_run_admission_aborted";
const ADMISSION_WORKER_OWNED: &str = "authority_production_run_admission_worker_owned";
const ADMISSION_LOCK_POISONED: &str = "authority_production_run_admission_lock_poisoned";
const ADMISSION_FINAL_COMMIT_LOCK_POISONED: &str =
    "authority_production_run_final_commit_lock_poisoned";
const ADMISSION_FINAL_COMMIT_BINDING_CHANGED: &str =
    "authority_production_run_final_commit_binding_changed";
const ADMISSION_REQUEST_MISMATCH: &str = "authority_production_run_request_mismatch";
const ADMISSION_TICKET_MISMATCH: &str = "authority_production_run_ticket_mismatch";
const ADMISSION_PREPARE_REPLAY: &str = "authority_production_run_prepare_replayed";
const ADMISSION_PREPARED_INVALID: &str = "authority_production_run_prepared_invalid";
const ADMISSION_PREPARED_MISMATCH: &str = "authority_production_run_prepared_mismatch";
const ADMISSION_START_ORDER_INVALID: &str = "authority_production_run_start_order_invalid";
const ADMISSION_COMMIT_ORDER_INVALID: &str = "authority_production_run_commit_order_invalid";
const ADMISSION_TAKE_ORDER_INVALID: &str = "authority_production_run_take_order_invalid";
const ADMISSION_WORKER_COMPLETION_ORDER_INVALID: &str =
    "authority_production_run_worker_completion_order_invalid";
const ADMISSION_WORKER_TERMINAL_MISMATCH: &str =
    "authority_production_run_worker_terminal_mismatch";
const ADMISSION_WORKER_HANDLE_HANDOFF_REPLAYED: &str =
    "authority_production_run_worker_handle_handoff_replayed";
const ADMISSION_WORKER_HANDLE_HANDOFF_INVALID: &str =
    "authority_production_run_worker_handle_handoff_invalid";
const ADMISSION_START_AUTHORIZATION_REPLAYED: &str =
    "authority_production_run_start_authorization_replayed";
const ADMISSION_START_AUTHORIZATION_INVALID: &str =
    "authority_production_run_start_authorization_invalid";
const ADMISSION_START_AUTHORIZATION_BINDING_MISMATCH: &str =
    "authority_production_run_start_authorization_binding_mismatch";
const WORKER_COMPLETION_DOMAIN: &[u8] = b"vrcforge-authority-worker-completion-v1\0";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RunAdmissionPhase {
    Staged,
    StartPending,
    Committed,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PreparedBinding {
    authority_identity_digest: [u8; 32],
    ticket_digest: [u8; 32],
    run_binding_digest: [u8; 32],
    prepared_receipt_digest: [u8; 32],
    policy_snapshot_digest: [u8; 32],
    scenario_handle_snapshot: Option<FixedScenarioHandleSnapshot>,
}

#[derive(Debug)]
struct ActiveRunAdmission<H> {
    request_id: String,
    phase: RunAdmissionPhase,
    handles: Option<H>,
    prepared_binding: Option<PreparedBinding>,
    prepared: Option<PreparedRun>,
}

#[derive(Debug)]
enum RunAdmissionSlot<H> {
    Empty,
    Active(ActiveRunAdmission<H>),
    Aborted {
        request_id: String,
    },
    WorkerOwned {
        request_id: String,
        completion_digest: [u8; 32],
    },
}

struct SharedRunAdmission<H> {
    slot: Arc<Mutex<RunAdmissionSlot<H>>>,
}

impl<H> Clone for SharedRunAdmission<H> {
    fn clone(&self) -> Self {
        Self {
            slot: Arc::clone(&self.slot),
        }
    }
}

impl<H> Default for SharedRunAdmission<H> {
    fn default() -> Self {
        Self {
            slot: Arc::new(Mutex::new(RunAdmissionSlot::Empty)),
        }
    }
}

impl<H> SharedRunAdmission<H> {
    fn lock(&self) -> Result<MutexGuard<'_, RunAdmissionSlot<H>>, RuntimeDependencyError> {
        self.slot
            .lock()
            .map_err(|_| dependency_error(ADMISSION_LOCK_POISONED))
    }

    fn stage(&self, request_id: &str, handles: H) -> Result<(), RuntimeDependencyError> {
        self.stage_with(request_id, || Ok(handles))
    }

    fn stage_with<F>(&self, request_id: &str, build: F) -> Result<(), RuntimeDependencyError>
    where
        F: FnOnce() -> Result<H, RuntimeDependencyError>,
    {
        let mut slot = self.lock()?;
        match &*slot {
            RunAdmissionSlot::Empty => {
                let handles = build()?;
                *slot = RunAdmissionSlot::Active(ActiveRunAdmission {
                    request_id: request_id.to_owned(),
                    phase: RunAdmissionPhase::Staged,
                    handles: Some(handles),
                    prepared_binding: None,
                    prepared: None,
                });
                Ok(())
            }
            RunAdmissionSlot::Aborted { .. } => Err(dependency_error(ADMISSION_ABORTED)),
            RunAdmissionSlot::WorkerOwned { .. } => Err(dependency_error(ADMISSION_WORKER_OWNED)),
            RunAdmissionSlot::Active(_) => Err(dependency_error(ADMISSION_BUSY)),
        }
    }

    fn stage_with_acquired<P, A, C>(
        &self,
        request_id: &str,
        acquire: A,
        compose: C,
    ) -> Result<(), RuntimeDependencyError>
    where
        A: FnOnce() -> Result<P, RuntimeDependencyError>,
        C: FnOnce(P) -> Result<H, RuntimeDependencyError>,
    {
        let acquired = acquire()?;
        self.stage_with(request_id, || compose(acquired))
    }

    fn prepare_with<F>(
        &self,
        identity: &AuthorityRuntimeIdentity,
        ticket: &RuntimeTicketRef,
        build: F,
    ) -> Result<PreparedRun, RuntimeDependencyError>
    where
        F: FnOnce(&H) -> Result<PreparedRun, RuntimeDependencyError>,
    {
        self.prepare_with_snapshot(identity, ticket, |handles| {
            build(handles).map(|prepared| (prepared, None))
        })
    }

    fn prepare_with_snapshot<F>(
        &self,
        identity: &AuthorityRuntimeIdentity,
        ticket: &RuntimeTicketRef,
        build: F,
    ) -> Result<PreparedRun, RuntimeDependencyError>
    where
        F: FnOnce(
            &H,
        ) -> Result<
            (PreparedRun, Option<FixedScenarioHandleSnapshot>),
            RuntimeDependencyError,
        >,
    {
        let mut slot = self.lock()?;
        let active = match &mut *slot {
            RunAdmissionSlot::Active(active) => active,
            RunAdmissionSlot::Empty => return Err(dependency_error(ADMISSION_EMPTY)),
            RunAdmissionSlot::Aborted { .. } => {
                return Err(dependency_error(ADMISSION_ABORTED));
            }
            RunAdmissionSlot::WorkerOwned { .. } => {
                return Err(dependency_error(ADMISSION_WORKER_OWNED));
            }
        };
        if !ticket.matches_request(identity, &active.request_id) {
            return Err(dependency_error(ADMISSION_TICKET_MISMATCH));
        }
        if active.phase != RunAdmissionPhase::Staged {
            return Err(dependency_error(ADMISSION_START_ORDER_INVALID));
        }
        if active.prepared_binding.is_some() {
            return Err(dependency_error(ADMISSION_PREPARE_REPLAY));
        }
        let handles = active
            .handles
            .as_ref()
            .ok_or_else(|| dependency_error(ADMISSION_PREPARED_INVALID))?;
        let (prepared, scenario_handle_snapshot) = build(handles)?;
        if !prepared.verifies_for(
            &identity.binding_digest(),
            &ticket.digest(),
            prepared.receipt().service_instance_digest(),
        ) || prepared.policy_snapshot().is_empty()
        {
            return Err(dependency_error(ADMISSION_PREPARED_INVALID));
        }
        active.prepared_binding = Some(prepared_binding(
            identity,
            ticket,
            &prepared,
            scenario_handle_snapshot,
        ));
        Ok(prepared)
    }

    fn queue_start(
        &self,
        identity: &AuthorityRuntimeIdentity,
        ticket: &RuntimeTicketRef,
        prepared: PreparedRun,
    ) -> Result<(), RuntimeDependencyError> {
        let mut slot = self.lock()?;
        let active = match &mut *slot {
            RunAdmissionSlot::Active(active) => active,
            RunAdmissionSlot::Empty => return Err(dependency_error(ADMISSION_EMPTY)),
            RunAdmissionSlot::Aborted { .. } => {
                return Err(dependency_error(ADMISSION_ABORTED));
            }
            RunAdmissionSlot::WorkerOwned { .. } => {
                return Err(dependency_error(ADMISSION_WORKER_OWNED));
            }
        };
        if active.phase != RunAdmissionPhase::Staged || active.prepared.is_some() {
            return Err(dependency_error(ADMISSION_START_ORDER_INVALID));
        }
        if !ticket.matches_request(identity, &active.request_id) {
            return Err(dependency_error(ADMISSION_TICKET_MISMATCH));
        }
        let expected = active
            .prepared_binding
            .as_ref()
            .ok_or_else(|| dependency_error(ADMISSION_START_ORDER_INVALID))?;
        let observed = prepared_binding(
            identity,
            ticket,
            &prepared,
            expected.scenario_handle_snapshot.clone(),
        );
        if expected != &observed
            || !prepared.verifies_for(
                &identity.binding_digest(),
                &ticket.digest(),
                prepared.receipt().service_instance_digest(),
            )
        {
            return Err(dependency_error(ADMISSION_PREPARED_MISMATCH));
        }
        active.prepared = Some(prepared);
        active.phase = RunAdmissionPhase::StartPending;
        Ok(())
    }

    fn queue_start_for_context(
        &self,
        context: &RuntimeRunContext,
        prepared: PreparedRun,
    ) -> Result<(), RuntimeDependencyError> {
        let mut slot = self.lock()?;
        let active = match &mut *slot {
            RunAdmissionSlot::Active(active) => active,
            RunAdmissionSlot::Empty => return Err(dependency_error(ADMISSION_EMPTY)),
            RunAdmissionSlot::Aborted { .. } => {
                return Err(dependency_error(ADMISSION_ABORTED));
            }
            RunAdmissionSlot::WorkerOwned { .. } => {
                return Err(dependency_error(ADMISSION_WORKER_OWNED));
            }
        };
        if active.phase != RunAdmissionPhase::Staged || active.prepared.is_some() {
            return Err(dependency_error(ADMISSION_START_ORDER_INVALID));
        }
        let expected = active
            .prepared_binding
            .as_ref()
            .ok_or_else(|| dependency_error(ADMISSION_START_ORDER_INVALID))?;
        let observed = PreparedBinding {
            authority_identity_digest: *context.authority_identity_digest(),
            ticket_digest: context.ticket().digest(),
            run_binding_digest: *context.run_binding_digest(),
            prepared_receipt_digest: prepared.receipt().digest(),
            policy_snapshot_digest: Sha256::digest(prepared.policy_snapshot()).into(),
            scenario_handle_snapshot: expected.scenario_handle_snapshot.clone(),
        };
        if expected != &observed
            || context.prepared_receipt() != prepared.receipt()
            || context.canonical_policy_snapshot() != prepared.policy_snapshot()
            || !prepared.verifies_for(
                context.authority_identity_digest(),
                &context.ticket().digest(),
                context.service_instance_digest(),
            )
        {
            return Err(dependency_error(ADMISSION_PREPARED_MISMATCH));
        }
        active.prepared = Some(prepared);
        active.phase = RunAdmissionPhase::StartPending;
        Ok(())
    }

    fn commit(&self, request_id: &str) -> Result<(), RuntimeDependencyError> {
        let mut slot = self.lock()?;
        let active = require_active(&mut slot, request_id)?;
        if active.phase != RunAdmissionPhase::StartPending
            || active.handles.is_none()
            || active.prepared.is_none()
        {
            return Err(dependency_error(ADMISSION_COMMIT_ORDER_INVALID));
        }
        active.phase = RunAdmissionPhase::Committed;
        Ok(())
    }

    fn commit_and_take(
        &self,
        request_id: &str,
    ) -> Result<OwnedCommittedRun<H>, RuntimeDependencyError> {
        let mut slot = self.lock()?;
        let active = require_active(&mut slot, request_id)?;
        if active.phase != RunAdmissionPhase::StartPending
            || active.handles.is_none()
            || active.prepared.is_none()
            || active.prepared_binding.is_none()
        {
            return Err(dependency_error(ADMISSION_COMMIT_ORDER_INVALID));
        }
        let request_id = active.request_id.clone();
        let prepared = active
            .prepared
            .take()
            .ok_or_else(|| dependency_error(ADMISSION_COMMIT_ORDER_INVALID))?;
        let handles = active
            .handles
            .take()
            .ok_or_else(|| dependency_error(ADMISSION_COMMIT_ORDER_INVALID))?;
        let prepared_binding = active
            .prepared_binding
            .take()
            .ok_or_else(|| dependency_error(ADMISSION_COMMIT_ORDER_INVALID))?;
        let completion_digest = worker_completion_digest(&request_id, &prepared_binding);
        *slot = RunAdmissionSlot::WorkerOwned {
            request_id: request_id.clone(),
            completion_digest,
        };
        Ok(OwnedCommittedRun {
            request_id,
            prepared,
            handles,
            prepared_binding,
            completion_digest,
            runs: self.clone(),
        })
    }

    fn abort(&self, request_id: &str) -> Result<(), RuntimeDependencyError> {
        let mut slot = self.lock()?;
        match &*slot {
            RunAdmissionSlot::Active(active) if active.request_id != request_id => {
                return Err(dependency_error(ADMISSION_REQUEST_MISMATCH));
            }
            RunAdmissionSlot::Active(_) => {}
            RunAdmissionSlot::Empty => return Err(dependency_error(ADMISSION_EMPTY)),
            RunAdmissionSlot::Aborted {
                request_id: aborted,
            } if aborted == request_id => {
                return Err(dependency_error(ADMISSION_ABORTED));
            }
            RunAdmissionSlot::Aborted { .. } => {
                return Err(dependency_error(ADMISSION_REQUEST_MISMATCH));
            }
            RunAdmissionSlot::WorkerOwned {
                request_id: owned, ..
            } if owned == request_id => {
                return Err(dependency_error(ADMISSION_WORKER_OWNED));
            }
            RunAdmissionSlot::WorkerOwned { .. } => {
                return Err(dependency_error(ADMISSION_REQUEST_MISMATCH));
            }
        }
        let safe_to_reopen = matches!(
            &*slot,
            RunAdmissionSlot::Active(ActiveRunAdmission {
                phase: RunAdmissionPhase::Staged,
                ..
            })
        );
        let replacement = if safe_to_reopen {
            RunAdmissionSlot::Empty
        } else {
            RunAdmissionSlot::Aborted {
                request_id: request_id.to_owned(),
            }
        };
        let previous = std::mem::replace(&mut *slot, replacement);
        drop(slot);
        drop(previous);
        Ok(())
    }

    fn take_committed(&self) -> Result<OwnedCommittedRun<H>, RuntimeDependencyError> {
        let mut slot = self.lock()?;
        let active = match &mut *slot {
            RunAdmissionSlot::Active(active) => active,
            RunAdmissionSlot::Empty => return Err(dependency_error(ADMISSION_EMPTY)),
            RunAdmissionSlot::Aborted { .. } => {
                return Err(dependency_error(ADMISSION_ABORTED));
            }
            RunAdmissionSlot::WorkerOwned { .. } => {
                return Err(dependency_error(ADMISSION_WORKER_OWNED));
            }
        };
        if active.phase != RunAdmissionPhase::Committed {
            return Err(dependency_error(ADMISSION_TAKE_ORDER_INVALID));
        }
        let request_id = active.request_id.clone();
        let prepared = active
            .prepared
            .take()
            .ok_or_else(|| dependency_error(ADMISSION_TAKE_ORDER_INVALID))?;
        let handles = active
            .handles
            .take()
            .ok_or_else(|| dependency_error(ADMISSION_TAKE_ORDER_INVALID))?;
        let prepared_binding = active
            .prepared_binding
            .take()
            .ok_or_else(|| dependency_error(ADMISSION_TAKE_ORDER_INVALID))?;
        let completion_digest = worker_completion_digest(&request_id, &prepared_binding);
        *slot = RunAdmissionSlot::WorkerOwned {
            request_id: request_id.clone(),
            completion_digest,
        };
        Ok(OwnedCommittedRun {
            request_id,
            prepared,
            handles,
            prepared_binding,
            completion_digest,
            runs: self.clone(),
        })
    }

    #[cfg(test)]
    fn finish_worker(
        &self,
        request_id: &str,
        completion_digest: &[u8; 32],
    ) -> Result<(), RuntimeDependencyError> {
        let mut slot = self.lock()?;
        match &*slot {
            RunAdmissionSlot::WorkerOwned {
                request_id: owned,
                completion_digest: expected,
            } if owned == request_id && expected == completion_digest => {
                *slot = RunAdmissionSlot::Empty;
                Ok(())
            }
            RunAdmissionSlot::WorkerOwned {
                request_id: owned, ..
            } if owned != request_id => Err(dependency_error(ADMISSION_REQUEST_MISMATCH)),
            RunAdmissionSlot::WorkerOwned { .. } => {
                Err(dependency_error(ADMISSION_WORKER_COMPLETION_ORDER_INVALID))
            }
            RunAdmissionSlot::Empty
            | RunAdmissionSlot::Active(_)
            | RunAdmissionSlot::Aborted { .. } => {
                Err(dependency_error(ADMISSION_WORKER_COMPLETION_ORDER_INVALID))
            }
        }
    }
}

fn worker_completion_digest(request_id: &str, binding: &PreparedBinding) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(WORKER_COMPLETION_DOMAIN);
    digest.update((request_id.len() as u64).to_be_bytes());
    digest.update(request_id.as_bytes());
    digest.update(binding.authority_identity_digest);
    digest.update(binding.ticket_digest);
    digest.update(binding.run_binding_digest);
    digest.update(binding.prepared_receipt_digest);
    digest.update(binding.policy_snapshot_digest);
    match &binding.scenario_handle_snapshot {
        Some(snapshot) => {
            digest.update([1]);
            digest.update(snapshot.digest());
        }
        None => digest.update([0]),
    }
    digest.finalize().into()
}

fn require_active<'a, H>(
    slot: &'a mut RunAdmissionSlot<H>,
    request_id: &str,
) -> Result<&'a mut ActiveRunAdmission<H>, RuntimeDependencyError> {
    match slot {
        RunAdmissionSlot::Active(active) if active.request_id == request_id => Ok(active),
        RunAdmissionSlot::Active(_) => Err(dependency_error(ADMISSION_REQUEST_MISMATCH)),
        RunAdmissionSlot::Empty => Err(dependency_error(ADMISSION_EMPTY)),
        RunAdmissionSlot::Aborted {
            request_id: aborted,
        } if aborted == request_id => Err(dependency_error(ADMISSION_ABORTED)),
        RunAdmissionSlot::Aborted { .. } => Err(dependency_error(ADMISSION_REQUEST_MISMATCH)),
        RunAdmissionSlot::WorkerOwned {
            request_id: owned, ..
        } if owned == request_id => Err(dependency_error(ADMISSION_WORKER_OWNED)),
        RunAdmissionSlot::WorkerOwned { .. } => Err(dependency_error(ADMISSION_REQUEST_MISMATCH)),
    }
}

fn prepared_binding(
    identity: &AuthorityRuntimeIdentity,
    ticket: &RuntimeTicketRef,
    prepared: &PreparedRun,
    scenario_handle_snapshot: Option<FixedScenarioHandleSnapshot>,
) -> PreparedBinding {
    PreparedBinding {
        authority_identity_digest: identity.binding_digest(),
        ticket_digest: ticket.digest(),
        run_binding_digest: derive_run_binding_digest(
            &identity.binding_digest(),
            &ticket.digest(),
            prepared.receipt().service_instance_digest(),
            prepared.receipt().runner_policy_digest(),
        ),
        prepared_receipt_digest: prepared.receipt().digest(),
        policy_snapshot_digest: Sha256::digest(prepared.policy_snapshot()).into(),
        scenario_handle_snapshot,
    }
}

fn dependency_error(code: &'static str) -> RuntimeDependencyError {
    RuntimeDependencyError::new(code)
}

fn contract_error(error: RuntimeDependencyError) -> ContractError {
    ContractError::new(error.code())
}

trait ControllerCapability {
    type Handles;

    fn admit_command(
        &self,
        intent: &InstalledControllerCommandIntent,
    ) -> Result<(), RuntimeDependencyError>;

    fn admit_run(
        &self,
        request_id: &str,
        handle_tokens: ExternalModelPartHandleTokens,
    ) -> Result<Self::Handles, RuntimeDependencyError>;
}

impl ControllerCapability for Arc<AuthenticatedControllerCapability> {
    type Handles = ValidatedExternalScenarioHandleBundle;

    fn admit_command(
        &self,
        intent: &InstalledControllerCommandIntent,
    ) -> Result<(), RuntimeDependencyError> {
        AuthenticatedControllerCapability::admit_command(self.as_ref(), intent)
            .map_err(|error| dependency_error(error.code()))
    }

    fn admit_run(
        &self,
        request_id: &str,
        handle_tokens: ExternalModelPartHandleTokens,
    ) -> Result<Self::Handles, RuntimeDependencyError> {
        self.as_ref()
            .admit_external_model_part_command(request_id, handle_tokens)
            .map_err(|error| dependency_error(error.code()))
    }
}

struct ControllerAdmissionCore<C>
where
    C: ControllerCapability,
{
    capability: C,
}

impl<C> ControllerAdmissionCore<C>
where
    C: ControllerCapability,
{
    fn new(capability: C) -> Self {
        Self { capability }
    }

    fn admit_non_run(
        &self,
        command: FixedControllerCommand<'_>,
    ) -> Result<(), RuntimeDependencyError> {
        let intent = match command {
            FixedControllerCommand::Status => InstalledControllerCommandIntent::status(),
            FixedControllerCommand::SelfTest => InstalledControllerCommandIntent::self_test(),
            FixedControllerCommand::Cancel { request_id } => {
                InstalledControllerCommandIntent::cancel(request_id)
                    .map_err(|error| dependency_error(error.code()))?
            }
            FixedControllerCommand::GetResult { request_id } => {
                InstalledControllerCommandIntent::get_result(request_id)
                    .map_err(|error| dependency_error(error.code()))?
            }
        };
        self.capability.admit_command(&intent)
    }

    fn admit_run(
        &self,
        request_id: &str,
        handle_tokens: ExternalModelPartHandleTokens,
    ) -> Result<C::Handles, RuntimeDependencyError> {
        self.capability.admit_run(request_id, handle_tokens)
    }
}

#[derive(Clone)]
pub(crate) struct ProductionRunAdmission {
    runs: SharedRunAdmission<ActiveScenarioHandleBundle>,
    boundary: SharedAuthenticatedFinalCommitBoundary,
    expected_generation: [u8; 32],
    expected_final_commit_receipt_sha256: [u8; 32],
    start_sink: BackgroundNativeStartSink<WindowsNativeSupervisorApi>,
}

impl std::fmt::Debug for ProductionRunAdmission {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let state = match self.runs.slot.lock() {
            Ok(slot) => match &*slot {
                RunAdmissionSlot::Empty => "empty",
                RunAdmissionSlot::Active(active) => match active.phase {
                    RunAdmissionPhase::Staged => "staged",
                    RunAdmissionPhase::StartPending => "start_pending",
                    RunAdmissionPhase::Committed => "committed",
                },
                RunAdmissionSlot::Aborted { .. } => "aborted",
                RunAdmissionSlot::WorkerOwned { .. } => "worker_owned",
            },
            Err(_) => "poisoned",
        };
        formatter
            .debug_struct("ProductionRunAdmission")
            .field("state", &state)
            .finish()
    }
}

impl ProductionRunAdmission {
    pub(crate) fn new(
        boundary: SharedAuthenticatedFinalCommitBoundary,
        expected_generation: [u8; 32],
        expected_final_commit_receipt_sha256: [u8; 32],
        start_sink: BackgroundNativeStartSink<WindowsNativeSupervisorApi>,
    ) -> Result<Self, RuntimeDependencyError> {
        if expected_generation.iter().all(|value| *value == 0)
            || expected_final_commit_receipt_sha256
                .iter()
                .all(|value| *value == 0)
        {
            return Err(dependency_error(ADMISSION_FINAL_COMMIT_BINDING_CHANGED));
        }
        let admission = Self {
            runs: SharedRunAdmission::default(),
            boundary,
            expected_generation,
            expected_final_commit_receipt_sha256,
            start_sink,
        };
        admission.verify_current_final_commit_binding()?;
        Ok(admission)
    }

    fn verify_current_final_commit_binding(&self) -> Result<(), RuntimeDependencyError> {
        let mut boundary = self
            .boundary
            .lock()
            .map_err(|_| dependency_error(ADMISSION_FINAL_COMMIT_LOCK_POISONED))?;
        let binding = boundary
            .current_policy_binding()
            .map_err(|error| dependency_error(error.code()))?;
        if binding.generation() != &self.expected_generation
            || binding.final_commit_receipt_sha256() != &self.expected_final_commit_receipt_sha256
        {
            return Err(dependency_error(ADMISSION_FINAL_COMMIT_BINDING_CHANGED));
        }
        Ok(())
    }

    fn clone_current_protected_pair(
        &self,
    ) -> Result<GenerationBoundProtectedExecutableHandles, RuntimeDependencyError> {
        let mut boundary = self
            .boundary
            .lock()
            .map_err(|_| dependency_error(ADMISSION_FINAL_COMMIT_LOCK_POISONED))?;
        let binding = boundary
            .current_policy_binding()
            .map_err(|error| dependency_error(error.code()))?;
        if binding.generation() != &self.expected_generation
            || binding.final_commit_receipt_sha256() != &self.expected_final_commit_receipt_sha256
        {
            return Err(dependency_error(ADMISSION_FINAL_COMMIT_BINDING_CHANGED));
        }
        let handles = boundary
            .clone_current_protected_scenario_executables()
            .map_err(|error| dependency_error(error.code()))?;
        if handles.generation() != &self.expected_generation
            || handles.final_commit_receipt_sha256() != &self.expected_final_commit_receipt_sha256
        {
            return Err(dependency_error(ADMISSION_FINAL_COMMIT_BINDING_CHANGED));
        }
        drop(boundary);
        Ok(handles)
    }

    fn stage_external_run(
        &self,
        request_id: &str,
        external: ValidatedExternalScenarioHandleBundle,
    ) -> Result<(), RuntimeDependencyError> {
        self.runs.stage_with_acquired(
            request_id,
            || {
                self.clone_current_protected_pair()
                    .map(|protected| (protected, external))
            },
            |(protected, external)| {
                let protected = protected
                    .into_verified_ordered_files()
                    .map_err(|error| dependency_error(error.code()))?;
                external
                    .compose_with_protected_roots(protected)
                    .map_err(|error| dependency_error(error.code()))
            },
        )
    }

    /// Borrows the fixed eight service-owned files only for policy construction.
    /// The bundle remains in the admission slot across ledger persistence and start.
    pub(crate) fn prepare_with<F>(
        &self,
        identity: &AuthorityRuntimeIdentity,
        ticket: &RuntimeTicketRef,
        build: F,
    ) -> Result<PreparedRun, RuntimeDependencyError>
    where
        F: FnOnce(
            [&File; FIXED_MODEL_PART_HANDLE_COUNT],
        ) -> Result<PreparedRun, RuntimeDependencyError>,
    {
        self.runs
            .prepare_with_snapshot(identity, ticket, |handles| {
                let snapshot = handles
                    .capture_prepare_snapshot()
                    .map_err(|error| dependency_error(error.code()))?;
                let prepared = build(handles.files())?;
                handles
                    .validate_snapshot(&snapshot)
                    .map_err(|error| dependency_error(error.code()))?;
                Ok((prepared, Some(snapshot)))
            })
    }

    pub(crate) fn queue_start(
        &self,
        context: &RuntimeRunContext,
        prepared: PreparedRun,
    ) -> Result<(), RuntimeDependencyError> {
        self.runs.queue_start_for_context(context, prepared)
    }

    fn commit_and_enqueue(&self, request_id: &str) -> Result<(), RuntimeDependencyError> {
        // The admission lock is released by commit_and_take before enqueue
        // takes the background-core lock. The worker terminal path takes those
        // locks in the opposite lifetime, never at the same time.
        let committed = CommittedRun {
            owned: self.runs.commit_and_take(request_id)?,
        };
        self.start_sink
            .enqueue(committed.into_background_run())
            .map(|_| ())
            .map_err(|error| dependency_error(error.code()))
    }
}

pub(crate) struct ProductionControllerAdmission {
    core: ControllerAdmissionCore<Arc<AuthenticatedControllerCapability>>,
    runs: ProductionRunAdmission,
}

impl ProductionControllerAdmission {
    /// Splits one authenticated pipe capability into read-only handshake
    /// verification and the sole command-admission owner. Neither half exposes
    /// the shared capability or can mint another command admission.
    pub(crate) fn split(
        capability: AuthenticatedControllerCapability,
        runs: ProductionRunAdmission,
    ) -> (ProductionControllerPeerVerifier, Self) {
        let capability = Arc::new(capability);
        (
            ProductionControllerPeerVerifier {
                capability: Arc::clone(&capability),
            },
            Self {
                core: ControllerAdmissionCore::new(capability),
                runs,
            },
        )
    }
}

pub(crate) struct ProductionControllerPeerVerifier {
    capability: Arc<AuthenticatedControllerCapability>,
}

impl std::fmt::Debug for ProductionControllerPeerVerifier {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("ProductionControllerPeerVerifier")
            .field("process_id", &self.capability.process_id())
            .field(
                "process_creation_time",
                &self.capability.process_creation_time(),
            )
            .finish_non_exhaustive()
    }
}

impl AuthorityPeerBindingVerifier for ProductionControllerPeerVerifier {
    fn verify_current_peer_binding(&mut self) -> Result<AuthorityPeerBinding, ContractError> {
        self.capability
            .revalidate_connected_peer()
            .map_err(|error| ContractError::new(error.code()))?;
        AuthorityPeerBinding::new(
            self.capability.process_id(),
            self.capability.process_creation_time(),
            self.capability.session_id(),
            *self.capability.controller_sha256(),
            self.capability.controller_file_identity_digest(),
        )
    }
}

impl FixedModelPartHandleAdmission for ProductionControllerAdmission {
    fn stage_service_owned_run(
        &mut self,
        request_id: &str,
        handle_tokens: ExternalModelPartHandleTokens,
    ) -> Result<(), ContractError> {
        let external = self
            .core
            .admit_run(request_id, handle_tokens)
            .map_err(contract_error)?;
        self.runs
            .stage_external_run(request_id, external)
            .map_err(contract_error)
    }

    fn commit_runtime_start(&mut self, request_id: &str) -> Result<(), ContractError> {
        self.runs
            .commit_and_enqueue(request_id)
            .map_err(contract_error)
    }

    fn abort_runtime_start(&mut self, request_id: &str) -> Result<(), ContractError> {
        self.runs.runs.abort(request_id).map_err(contract_error)
    }
}

impl InstalledControllerAdmission for ProductionControllerAdmission {
    fn admit_non_run_command(
        &mut self,
        command: FixedControllerCommand<'_>,
    ) -> Result<(), ContractError> {
        self.core.admit_non_run(command).map_err(contract_error)
    }
}

struct OwnedCommittedRun<H> {
    request_id: String,
    prepared: PreparedRun,
    handles: H,
    prepared_binding: PreparedBinding,
    completion_digest: [u8; 32],
    runs: SharedRunAdmission<H>,
}

impl<H> std::fmt::Debug for OwnedCommittedRun<H> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("OwnedCommittedRun")
            .field("request_id", &self.request_id)
            .finish_non_exhaustive()
    }
}

#[cfg(test)]
impl<H> OwnedCommittedRun<H> {
    fn finish_for_test(self) -> Result<(), RuntimeDependencyError> {
        let Self {
            request_id,
            prepared,
            handles,
            prepared_binding: _,
            completion_digest,
            runs,
        } = self;
        drop(prepared);
        drop(handles);
        runs.finish_worker(&request_id, &completion_digest)
    }
}

/// Unique worker handoff. This type and both owned capabilities are deliberately non-Clone.
pub(crate) struct CommittedRun {
    owned: OwnedCommittedRun<ActiveScenarioHandleBundle>,
}

struct CommittedRunLease<H> {
    request_id: String,
    handles: Option<H>,
    worker_handle_handoff_taken: bool,
    start_authorization_consumed: bool,
    prepared_binding: PreparedBinding,
    completion_digest: [u8; 32],
    runs: SharedRunAdmission<H>,
}

impl std::fmt::Debug for CommittedRun {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("CommittedRun")
            .field("request_id", &self.owned.request_id)
            .finish_non_exhaustive()
    }
}

impl CommittedRun {
    fn into_background_run(self) -> OwnedBackgroundRun {
        let (prepared, lease) = self.owned.into_worker_parts();
        OwnedBackgroundRun::new(prepared, Box::new(lease))
    }
}

impl<H> OwnedCommittedRun<H> {
    fn into_worker_parts(self) -> (PreparedRun, CommittedRunLease<H>) {
        let Self {
            request_id,
            prepared,
            handles,
            prepared_binding,
            completion_digest,
            runs,
        } = self;
        (
            prepared,
            CommittedRunLease {
                request_id,
                handles: Some(handles),
                worker_handle_handoff_taken: false,
                start_authorization_consumed: false,
                prepared_binding,
                completion_digest,
                runs,
            },
        )
    }
}

impl<H> CommittedRunLease<H> {
    fn validate_terminal_binding(
        &self,
        terminal: &ValidatedNativeTerminalRun,
    ) -> Result<(), &'static str> {
        if terminal_matches_prepared_binding(terminal, &self.prepared_binding) {
            Ok(())
        } else {
            Err(ADMISSION_WORKER_TERMINAL_MISMATCH)
        }
    }

    #[cfg(test)]
    fn release(&mut self) -> Result<(), &'static str> {
        if self.handles.is_none() {
            return Err(ADMISSION_WORKER_COMPLETION_ORDER_INVALID);
        }
        self.runs
            .finish_worker(&self.request_id, &self.completion_digest)
            .map_err(|_| ADMISSION_WORKER_COMPLETION_ORDER_INVALID)?;
        drop(self.handles.take());
        Ok(())
    }
}

impl CommittedRunLease<ActiveScenarioHandleBundle> {
    fn take_worker_handles_with<F>(
        &mut self,
        duplicate: F,
    ) -> Result<WorkerScenarioHandleBundle, &'static str>
    where
        F: FnOnce(&ActiveScenarioHandleBundle) -> Result<WorkerScenarioHandleBundle, &'static str>,
    {
        if self.worker_handle_handoff_taken {
            return Err(ADMISSION_WORKER_HANDLE_HANDOFF_REPLAYED);
        }
        let handles = self
            .handles
            .as_ref()
            .ok_or(ADMISSION_WORKER_HANDLE_HANDOFF_INVALID)?;
        self.worker_handle_handoff_taken = true;
        let snapshot = self
            .prepared_binding
            .scenario_handle_snapshot
            .as_ref()
            .ok_or(ADMISSION_WORKER_HANDLE_HANDOFF_INVALID)?;
        handles
            .validate_snapshot(snapshot)
            .map_err(|error| error.code())?;
        let worker_handles = duplicate(handles)?;
        worker_handles
            .validate_snapshot(snapshot)
            .map_err(|error| error.code())?;
        Ok(worker_handles)
    }

    fn take_worker_handles(&mut self) -> Result<WorkerScenarioHandleBundle, &'static str> {
        self.take_worker_handles_with(|handles| {
            handles.try_clone_for_worker().map_err(|error| error.code())
        })
    }

    fn consume_start_authorization(
        &mut self,
        worker_handles: &WorkerScenarioHandleBundle,
        prepared_receipt_digest: [u8; 32],
        policy_snapshot_digest: [u8; 32],
    ) -> Result<VerifiedScenarioStartContract, &'static str> {
        if self.start_authorization_consumed {
            return Err(ADMISSION_START_AUTHORIZATION_REPLAYED);
        }
        // Burn before every fallible check. A failed first attempt must never
        // become a retry oracle over the same live file capabilities.
        self.start_authorization_consumed = true;
        if !self.worker_handle_handoff_taken {
            return Err(ADMISSION_START_AUTHORIZATION_INVALID);
        }
        if prepared_receipt_digest != self.prepared_binding.prepared_receipt_digest
            || policy_snapshot_digest != self.prepared_binding.policy_snapshot_digest
        {
            return Err(ADMISSION_START_AUTHORIZATION_BINDING_MISMATCH);
        }
        let handles = self
            .handles
            .as_ref()
            .ok_or(ADMISSION_START_AUTHORIZATION_INVALID)?;
        let snapshot = self
            .prepared_binding
            .scenario_handle_snapshot
            .as_ref()
            .ok_or(ADMISSION_START_AUTHORIZATION_INVALID)?;
        handles
            .verified_start_capability(worker_handles, snapshot)
            .map_err(|error| error.code())?
            .into_owned_contract(prepared_receipt_digest, policy_snapshot_digest)
            .map_err(|error| error.code())
    }

    fn finalize_terminal(
        &mut self,
        worker_handles: &mut Option<WorkerScenarioHandleBundle>,
        terminal: &ValidatedNativeTerminalRun,
    ) -> Result<(), &'static str> {
        let runs = self.runs.clone();
        let mut slot = runs
            .lock()
            .map_err(|_| ADMISSION_WORKER_COMPLETION_ORDER_INVALID)?;
        match &*slot {
            RunAdmissionSlot::WorkerOwned {
                request_id,
                completion_digest,
            } if request_id == &self.request_id && completion_digest == &self.completion_digest => {
            }
            RunAdmissionSlot::WorkerOwned { request_id, .. } if request_id != &self.request_id => {
                return Err(ADMISSION_REQUEST_MISMATCH);
            }
            _ => return Err(ADMISSION_WORKER_COMPLETION_ORDER_INVALID),
        }
        if !self.worker_handle_handoff_taken {
            return Err(ADMISSION_WORKER_HANDLE_HANDOFF_INVALID);
        }
        if !self.start_authorization_consumed {
            return Err(ADMISSION_START_AUTHORIZATION_INVALID);
        }
        let worker = worker_handles
            .as_ref()
            .ok_or(ADMISSION_WORKER_HANDLE_HANDOFF_INVALID)?;
        {
            let handles = self
                .handles
                .as_ref()
                .ok_or(ADMISSION_WORKER_HANDLE_HANDOFF_INVALID)?;
            let snapshot = self
                .prepared_binding
                .scenario_handle_snapshot
                .as_ref()
                .ok_or(ADMISSION_WORKER_HANDLE_HANDOFF_INVALID)?;
            let capability = handles
                .verified_start_capability(worker, snapshot)
                .map_err(|error| error.code())?;
            let _ = capability.roles();
            let _ = capability.snapshot_digest();
            let _ = capability.original_files();
            let _ = capability.worker_files();
        }
        self.validate_terminal_binding(terminal)?;

        // Every operation that can fail is complete while both handle sets
        // are still owned and the admission slot is locked. The remaining
        // tail is infallible and its order is part of the release contract.
        drop(worker_handles.take());
        drop(self.handles.take());
        *slot = RunAdmissionSlot::Empty;
        Ok(())
    }
}

impl BackgroundTerminalLease for CommittedRunLease<ActiveScenarioHandleBundle> {
    fn take_worker_handles(&mut self) -> Result<WorkerScenarioHandleBundle, &'static str> {
        CommittedRunLease::take_worker_handles(self)
    }

    fn consume_start_authorization(
        &mut self,
        worker_handles: &WorkerScenarioHandleBundle,
        prepared_receipt_digest: [u8; 32],
        policy_snapshot_digest: [u8; 32],
    ) -> Result<VerifiedScenarioStartContract, &'static str> {
        CommittedRunLease::consume_start_authorization(
            self,
            worker_handles,
            prepared_receipt_digest,
            policy_snapshot_digest,
        )
    }

    fn finalize_terminal(
        &mut self,
        worker_handles: &mut Option<WorkerScenarioHandleBundle>,
        terminal: &ValidatedNativeTerminalRun,
    ) -> Result<(), &'static str> {
        CommittedRunLease::finalize_terminal(self, worker_handles, terminal)
    }
}

fn terminal_matches_prepared_binding(
    terminal: &ValidatedNativeTerminalRun,
    binding: &PreparedBinding,
) -> bool {
    let (
        proof_authority,
        proof_ticket,
        proof_run_binding,
        cleanup_digest,
        cleanup_order_valid,
        admission,
    ) = match terminal {
        ValidatedNativeTerminalRun::Completed(proof) => (
            proof.terminal().authority_identity_digest(),
            proof.terminal().ticket_digest(),
            proof.terminal().run_binding_digest(),
            proof.cleanup_receipt_digest(),
            proof.terminal().cleanup_observed_at() >= proof.terminal().finalized_at(),
            Some(proof.admission()),
        ),
        ValidatedNativeTerminalRun::Burned(proof) => (
            proof.terminal().authority_identity_digest(),
            proof.terminal().ticket_digest(),
            proof.terminal().run_binding_digest(),
            proof.cleanup_receipt_digest(),
            proof.terminal().cleanup_observed_at() >= proof.terminal().terminal_ready_at(),
            proof.admission(),
        ),
    };
    let base_matches = proof_authority == &binding.authority_identity_digest
        && proof_ticket == &binding.ticket_digest
        && proof_run_binding == &binding.run_binding_digest
        && cleanup_order_valid
        && cleanup_digest.iter().any(|byte| *byte != 0);
    if !base_matches {
        return false;
    }
    match admission {
        Some(admission) => {
            admission.prepared_receipt_digest() == &binding.prepared_receipt_digest
                && admission.policy_snapshot_digest() == &binding.policy_snapshot_digest
                && admission
                    .armed_receipt_digest()
                    .iter()
                    .any(|byte| *byte != 0)
                && admission
                    .recovery_bundle_digest()
                    .iter()
                    .any(|byte| *byte != 0)
                && admission.binding_digest().iter().any(|byte| *byte != 0)
        }
        // A failure can become terminal before the run reaches its armed
        // admission boundary. Its unforgeable pairing with this lease is the
        // worker-owned Starting state; the base proof still has to bind the
        // exact authority, ticket, run, cleanup receipt, and cleanup order.
        None => matches!(terminal, ValidatedNativeTerminalRun::Burned(_)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::primitive_evidence_authority_supervisor::native_windows::NativeBurnedRunProof;
    use crate::primitive_evidence_authority_supervisor::{BurnReason, BurnedRunProof};
    use std::{
        cell::{Cell, RefCell},
        fs::{self, OpenOptions},
        io::Write,
        os::windows::{fs::FileExt, fs::OpenOptionsExt},
        panic::{catch_unwind, AssertUnwindSafe},
        path::{Path, PathBuf},
        rc::Rc,
        sync::{
            atomic::{AtomicU64, AtomicUsize, Ordering},
            mpsc, Arc, TryLockError,
        },
        thread,
    };
    use windows_sys::Win32::Storage::FileSystem::FILE_SHARE_READ;

    const TEST_REQUEST: &str = "run-1";
    const TEST_TICKET_DOMAIN: &[u8] = b"vrcforge-authority-runtime-ticket-v1\0";

    #[derive(Debug)]
    struct FakeHandles {
        count: usize,
        drops: Rc<Cell<usize>>,
    }

    impl Drop for FakeHandles {
        fn drop(&mut self) {
            self.drops.set(self.drops.get() + 1);
        }
    }

    #[derive(Debug)]
    struct FakeCapability {
        consumed: Cell<bool>,
        commands: RefCell<Vec<InstalledControllerCommandIntent>>,
        handle_drops: Rc<Cell<usize>>,
    }

    impl FakeCapability {
        fn new(handle_drops: Rc<Cell<usize>>) -> Self {
            Self {
                consumed: Cell::new(false),
                commands: RefCell::new(Vec::new()),
                handle_drops,
            }
        }

        fn consume(&self) -> Result<(), RuntimeDependencyError> {
            if self.consumed.replace(true) {
                return Err(dependency_error(
                    "authority_installed_controller_command_already_consumed",
                ));
            }
            Ok(())
        }
    }

    impl ControllerCapability for FakeCapability {
        type Handles = FakeHandles;

        fn admit_command(
            &self,
            intent: &InstalledControllerCommandIntent,
        ) -> Result<(), RuntimeDependencyError> {
            self.consume()?;
            self.commands.borrow_mut().push(intent.clone());
            Ok(())
        }

        fn admit_run(
            &self,
            _request_id: &str,
            handle_tokens: ExternalModelPartHandleTokens,
        ) -> Result<Self::Handles, RuntimeDependencyError> {
            self.consume()?;
            if handle_tokens.values().len() != EXTERNAL_MODEL_PART_HANDLE_COUNT {
                return Err(dependency_error("authority_test_handle_count_invalid"));
            }
            Ok(FakeHandles {
                count: EXTERNAL_MODEL_PART_HANDLE_COUNT,
                drops: Rc::clone(&self.handle_drops),
            })
        }
    }

    fn identity(seed: u8) -> AuthorityRuntimeIdentity {
        AuthorityRuntimeIdentity::new(
            [seed; 32],
            [seed.wrapping_add(1); 32],
            [seed.wrapping_add(2); 32],
            [seed.wrapping_add(3); 32],
            [seed.wrapping_add(4); 32],
        )
        .unwrap()
    }

    fn ticket(identity: &AuthorityRuntimeIdentity, request_id: &str) -> RuntimeTicketRef {
        let mut digest = Sha256::new();
        digest.update(TEST_TICKET_DOMAIN);
        digest.update(identity.binding_digest());
        digest.update((request_id.len() as u64).to_be_bytes());
        digest.update(request_id.as_bytes());
        let digest: [u8; 32] = digest.finalize().into();
        RuntimeTicketRef::from_persisted(&hex_lower(&digest)).unwrap()
    }

    fn prepared(identity: &AuthorityRuntimeIdentity, ticket: &RuntimeTicketRef) -> PreparedRun {
        PreparedRun::for_runtime_test(
            identity.binding_digest(),
            ticket.digest(),
            [0x71; 32],
            [0x81; 32],
        )
    }

    fn pre_armed_terminal(binding: &PreparedBinding) -> ValidatedNativeTerminalRun {
        ValidatedNativeTerminalRun::Burned(
            NativeBurnedRunProof::for_runtime_test(
                BurnedRunProof::for_runtime_test(
                    binding.authority_identity_digest,
                    binding.ticket_digest,
                    binding.run_binding_digest,
                    BurnReason::Failed,
                ),
                None,
            )
            .unwrap(),
        )
    }

    fn consume_start_contract(
        lease: &mut CommittedRunLease<ActiveScenarioHandleBundle>,
        worker: &WorkerScenarioHandleBundle,
    ) -> VerifiedScenarioStartContract {
        let prepared_receipt_digest = lease.prepared_binding.prepared_receipt_digest;
        let policy_snapshot_digest = lease.prepared_binding.policy_snapshot_digest;
        lease
            .consume_start_authorization(worker, prepared_receipt_digest, policy_snapshot_digest)
            .expect("first exact start authorization must succeed")
    }

    fn tokens() -> ExternalModelPartHandleTokens {
        ExternalModelPartHandleTokens::try_from_values([0x11, 0x22, 0x33, 0x44, 0x55, 0x66])
            .unwrap()
    }

    fn active_handle_fixture(
        label: &str,
    ) -> (
        PathBuf,
        [PathBuf; FIXED_MODEL_PART_HANDLE_COUNT],
        ActiveScenarioHandleBundle,
    ) {
        static SEQUENCE: AtomicU64 = AtomicU64::new(1);
        let root = std::env::temp_dir().join(format!(
            "vrcforge-worker-admission-{label}-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        let mut paths = Vec::with_capacity(FIXED_MODEL_PART_HANDLE_COUNT);
        let mut files = Vec::with_capacity(FIXED_MODEL_PART_HANDLE_COUNT);
        for index in 0..FIXED_MODEL_PART_HANDLE_COUNT {
            let path = root.join(format!("role-{index}.bin"));
            let mut writer = OpenOptions::new()
                .create_new(true)
                .write(true)
                .share_mode(0)
                .open(&path)
                .unwrap();
            writer.write_all(&[index as u8 + 1]).unwrap();
            writer.flush().unwrap();
            drop(writer);
            let file = OpenOptions::new()
                .read(true)
                // Match the production service guard: retained read-only
                // capabilities must permit independent read-only ReOpenFile
                // objects while continuing to deny mutation sharing.
                .share_mode(FILE_SHARE_READ)
                .open(&path)
                .unwrap();
            paths.push(path);
            files.push(file);
        }
        (
            root,
            paths.try_into().unwrap(),
            ActiveScenarioHandleBundle::from_test_files(files.try_into().ok().unwrap()),
        )
    }

    fn remove_active_handle_fixture(root: &Path, paths: &[PathBuf; FIXED_MODEL_PART_HANDLE_COUNT]) {
        for path in paths {
            fs::remove_file(path).unwrap();
        }
        fs::remove_dir(root).unwrap();
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

    #[test]
    fn non_run_command_consumes_the_controller_capability_once() {
        let cases = [
            (
                FixedControllerCommand::Status,
                InstalledControllerCommandIntent::Status,
            ),
            (
                FixedControllerCommand::SelfTest,
                InstalledControllerCommandIntent::SelfTest,
            ),
            (
                FixedControllerCommand::Cancel {
                    request_id: TEST_REQUEST,
                },
                InstalledControllerCommandIntent::Cancel {
                    request_id: TEST_REQUEST.to_owned(),
                },
            ),
            (
                FixedControllerCommand::GetResult {
                    request_id: TEST_REQUEST,
                },
                InstalledControllerCommandIntent::GetResult {
                    request_id: TEST_REQUEST.to_owned(),
                },
            ),
        ];
        for (command, expected) in cases {
            let drops = Rc::new(Cell::new(0));
            let core = ControllerAdmissionCore::new(FakeCapability::new(drops));
            core.admit_non_run(command).unwrap();
            assert_eq!(
                core.admit_non_run(FixedControllerCommand::Status)
                    .unwrap_err()
                    .code(),
                "authority_installed_controller_command_already_consumed"
            );
            assert_eq!(core.capability.commands.borrow().as_slice(), &[expected]);
        }
    }

    #[test]
    fn controller_consumes_exactly_six_external_handles_before_slot_staging() {
        let drops = Rc::new(Cell::new(0));
        let runs = SharedRunAdmission::default();
        let core = ControllerAdmissionCore::new(FakeCapability::new(Rc::clone(&drops)));
        let handles = core.admit_run(TEST_REQUEST, tokens()).unwrap();
        assert_eq!(handles.count, EXTERNAL_MODEL_PART_HANDLE_COUNT);
        runs.stage(TEST_REQUEST, handles).unwrap();
        let identity = identity(0x11);
        let ticket = ticket(&identity, TEST_REQUEST);
        let prepared = runs
            .prepare_with(&identity, &ticket, |handles| {
                assert_eq!(handles.count, EXTERNAL_MODEL_PART_HANDLE_COUNT);
                Ok(prepared(&identity, &ticket))
            })
            .unwrap();
        assert_eq!(
            runs.prepare_with(&identity, &ticket, |_| {
                panic!("replayed prepare must fail before policy construction")
            })
            .unwrap_err()
            .code(),
            ADMISSION_PREPARE_REPLAY
        );
        runs.queue_start(&identity, &ticket, prepared).unwrap();
        runs.abort(TEST_REQUEST).unwrap();
        assert_eq!(drops.get(), 1);
    }

    #[test]
    fn acquired_boundary_is_released_before_the_run_slot_is_locked() {
        let runs = SharedRunAdmission::<usize>::default();
        let boundary = Arc::new(Mutex::new(()));
        let barrier = Arc::new(std::sync::Barrier::new(2));
        thread::scope(|scope| {
            let observer_slot = Arc::clone(&runs.slot);
            let observer_barrier = Arc::clone(&barrier);
            let observer = scope.spawn(move || {
                observer_barrier.wait();
                assert!(observer_slot.try_lock().is_ok());
                observer_barrier.wait();
            });

            runs.stage_with_acquired(
                TEST_REQUEST,
                || {
                    let boundary_guard = boundary.lock().unwrap();
                    assert!(runs.slot.try_lock().is_ok());
                    barrier.wait();
                    barrier.wait();
                    drop(boundary_guard);
                    Ok(7usize)
                },
                |value| {
                    assert!(matches!(
                        runs.slot.try_lock(),
                        Err(TryLockError::WouldBlock)
                    ));
                    assert!(boundary.try_lock().is_ok());
                    Ok(value)
                },
            )
            .unwrap();
            observer.join().unwrap();
        });
        runs.abort(TEST_REQUEST).unwrap();
    }

    #[test]
    fn busy_slot_still_burns_the_preacquired_one_use_bundle() {
        let original_drops = Rc::new(Cell::new(0));
        let rejected_drops = Rc::new(Cell::new(0));
        let acquisitions = Cell::new(0usize);
        let runs = SharedRunAdmission::default();
        runs.stage(
            TEST_REQUEST,
            FakeHandles {
                count: FIXED_MODEL_PART_HANDLE_COUNT,
                drops: Rc::clone(&original_drops),
            },
        )
        .unwrap();

        let error = runs
            .stage_with_acquired(
                "run-2",
                || {
                    acquisitions.set(acquisitions.get() + 1);
                    Ok(FakeHandles {
                        count: EXTERNAL_MODEL_PART_HANDLE_COUNT,
                        drops: Rc::clone(&rejected_drops),
                    })
                },
                |_| panic!("a busy slot must reject before composition"),
            )
            .unwrap_err();
        assert_eq!(error.code(), ADMISSION_BUSY);
        assert_eq!(acquisitions.get(), 1);
        assert_eq!(rejected_drops.get(), 1);
        assert_eq!(original_drops.get(), 0);
        runs.abort(TEST_REQUEST).unwrap();
        assert_eq!(original_drops.get(), 1);
    }

    #[test]
    fn composition_failure_closes_the_acquired_bundle_and_leaves_no_slot() {
        let drops = Rc::new(Cell::new(0));
        let runs = SharedRunAdmission::<FakeHandles>::default();
        let error = runs
            .stage_with_acquired(
                TEST_REQUEST,
                || {
                    Ok(FakeHandles {
                        count: EXTERNAL_MODEL_PART_HANDLE_COUNT,
                        drops: Rc::clone(&drops),
                    })
                },
                |_| Err(dependency_error("authority_test_composition_failed")),
            )
            .unwrap_err();
        assert_eq!(error.code(), "authority_test_composition_failed");
        assert_eq!(drops.get(), 1);
        assert!(matches!(*runs.lock().unwrap(), RunAdmissionSlot::Empty));
    }

    #[test]
    fn prepare_rejects_ticket_and_request_mismatch_without_releasing_the_slot() {
        let drops = Rc::new(Cell::new(0));
        let runs = SharedRunAdmission::default();
        runs.stage(
            TEST_REQUEST,
            FakeHandles {
                count: 8,
                drops: Rc::clone(&drops),
            },
        )
        .unwrap();
        let identity = identity(0x21);
        let wrong_ticket = ticket(&identity, "run-2");
        assert_eq!(
            runs.prepare_with(&identity, &wrong_ticket, |_| {
                panic!("mismatched ticket must fail before policy construction")
            })
            .unwrap_err()
            .code(),
            ADMISSION_TICKET_MISMATCH
        );
        runs.abort(TEST_REQUEST).unwrap();
        assert_eq!(drops.get(), 1);
    }

    #[test]
    fn start_before_stage_fails_closed() {
        let runs = SharedRunAdmission::<FakeHandles>::default();
        let identity = identity(0x31);
        let ticket = ticket(&identity, TEST_REQUEST);
        assert_eq!(
            runs.queue_start(&identity, &ticket, prepared(&identity, &ticket))
                .unwrap_err()
                .code(),
            ADMISSION_EMPTY
        );
    }

    #[test]
    fn commit_before_start_fails_and_abort_closes_handles() {
        let drops = Rc::new(Cell::new(0));
        let runs = SharedRunAdmission::default();
        runs.stage(
            TEST_REQUEST,
            FakeHandles {
                count: 8,
                drops: Rc::clone(&drops),
            },
        )
        .unwrap();
        assert_eq!(
            runs.commit("run-2").unwrap_err().code(),
            ADMISSION_REQUEST_MISMATCH
        );
        assert_eq!(
            runs.commit(TEST_REQUEST).unwrap_err().code(),
            ADMISSION_COMMIT_ORDER_INVALID
        );
        runs.abort(TEST_REQUEST).unwrap();
        assert_eq!(drops.get(), 1);
    }

    #[test]
    fn wrong_request_id_never_closes_or_moves_the_active_handles() {
        let drops = Rc::new(Cell::new(0));
        let runs = SharedRunAdmission::default();
        runs.stage(
            TEST_REQUEST,
            FakeHandles {
                count: 8,
                drops: Rc::clone(&drops),
            },
        )
        .unwrap();
        assert_eq!(
            runs.abort("run-2").unwrap_err().code(),
            ADMISSION_REQUEST_MISMATCH
        );
        assert_eq!(drops.get(), 0);
        runs.abort(TEST_REQUEST).unwrap();
        assert_eq!(drops.get(), 1);
    }

    #[test]
    fn staged_abort_closes_handles_and_reopens_the_slot() {
        let drops = Rc::new(Cell::new(0));
        let runs = SharedRunAdmission::default();
        runs.stage(
            TEST_REQUEST,
            FakeHandles {
                count: 8,
                drops: Rc::clone(&drops),
            },
        )
        .unwrap();
        runs.abort(TEST_REQUEST).unwrap();
        assert_eq!(drops.get(), 1);
        assert_eq!(
            runs.abort(TEST_REQUEST).unwrap_err().code(),
            ADMISSION_EMPTY
        );
        runs.stage(
            "run-2",
            FakeHandles {
                count: 8,
                drops: Rc::clone(&drops),
            },
        )
        .unwrap();
        runs.abort("run-2").unwrap();
        assert_eq!(drops.get(), 2);
    }

    #[test]
    fn start_pending_abort_is_terminal_and_replay_cannot_reopen_the_slot() {
        let drops = Rc::new(Cell::new(0));
        let runs = SharedRunAdmission::default();
        runs.stage(
            TEST_REQUEST,
            FakeHandles {
                count: 8,
                drops: Rc::clone(&drops),
            },
        )
        .unwrap();
        let identity = identity(0x40);
        let ticket = ticket(&identity, TEST_REQUEST);
        let prepared = runs
            .prepare_with(&identity, &ticket, |_| Ok(prepared(&identity, &ticket)))
            .unwrap();
        runs.queue_start(&identity, &ticket, prepared).unwrap();
        runs.abort(TEST_REQUEST).unwrap();
        assert_eq!(drops.get(), 1);
        assert_eq!(
            runs.abort(TEST_REQUEST).unwrap_err().code(),
            ADMISSION_ABORTED
        );
        assert_eq!(
            runs.stage(
                "run-2",
                FakeHandles {
                    count: 8,
                    drops: Rc::clone(&drops),
                },
            )
            .unwrap_err()
            .code(),
            ADMISSION_ABORTED
        );
        assert_eq!(drops.get(), 2);
    }

    #[test]
    fn committed_run_has_one_worker_handoff_and_no_commit_or_stage_replay() {
        let drops = Rc::new(Cell::new(0));
        let runs = SharedRunAdmission::default();
        runs.stage(
            TEST_REQUEST,
            FakeHandles {
                count: 8,
                drops: Rc::clone(&drops),
            },
        )
        .unwrap();
        let identity = identity(0x41);
        let ticket = ticket(&identity, TEST_REQUEST);
        let prepared = runs
            .prepare_with(&identity, &ticket, |_| Ok(prepared(&identity, &ticket)))
            .unwrap();
        runs.queue_start(&identity, &ticket, prepared).unwrap();
        runs.commit(TEST_REQUEST).unwrap();
        assert_eq!(
            runs.commit(TEST_REQUEST).unwrap_err().code(),
            ADMISSION_COMMIT_ORDER_INVALID
        );

        let committed = runs.take_committed().unwrap();
        assert_eq!(committed.request_id, TEST_REQUEST);
        assert_eq!(committed.handles.count, 8);
        assert_eq!(
            runs.take_committed().unwrap_err().code(),
            ADMISSION_WORKER_OWNED
        );
        assert_eq!(
            runs.stage(
                "run-2",
                FakeHandles {
                    count: 8,
                    drops: Rc::clone(&drops),
                },
            )
            .unwrap_err()
            .code(),
            ADMISSION_WORKER_OWNED
        );
        assert_eq!(
            runs.finish_worker("run-2", &committed.completion_digest)
                .unwrap_err()
                .code(),
            ADMISSION_REQUEST_MISMATCH
        );
        assert_eq!(
            runs.finish_worker(TEST_REQUEST, &[0x55; 32])
                .unwrap_err()
                .code(),
            ADMISSION_WORKER_COMPLETION_ORDER_INVALID
        );
        committed.finish_for_test().unwrap();
        assert_eq!(drops.get(), 2);
        runs.stage(
            "run-2",
            FakeHandles {
                count: 8,
                drops: Rc::clone(&drops),
            },
        )
        .unwrap();
        runs.abort("run-2").unwrap();
        assert_eq!(drops.get(), 3);
    }

    #[test]
    fn committed_active_bundle_has_one_exact_worker_duplicate_handoff() {
        let (root, paths, active) = active_handle_fixture("one-handoff");
        let runs = SharedRunAdmission::default();
        runs.stage(TEST_REQUEST, active).unwrap();
        let identity = identity(0x48);
        let ticket = ticket(&identity, TEST_REQUEST);
        let prepared = runs
            .prepare_with_snapshot(&identity, &ticket, |handles| {
                let snapshot = handles.capture_prepare_snapshot().unwrap();
                Ok((prepared(&identity, &ticket), Some(snapshot)))
            })
            .unwrap();
        runs.queue_start(&identity, &ticket, prepared).unwrap();
        let committed = runs.commit_and_take(TEST_REQUEST).unwrap();
        let (prepared, mut lease) = committed.into_worker_parts();
        drop(prepared);

        let mut worker = Some(lease.take_worker_handles().unwrap());
        let start_contract = consume_start_contract(&mut lease, worker.as_ref().unwrap());
        let prepared_receipt_digest = lease.prepared_binding.prepared_receipt_digest;
        let policy_snapshot_digest = lease.prepared_binding.policy_snapshot_digest;
        assert_eq!(
            lease
                .consume_start_authorization(
                    worker.as_ref().unwrap(),
                    prepared_receipt_digest,
                    policy_snapshot_digest,
                )
                .unwrap_err(),
            ADMISSION_START_AUTHORIZATION_REPLAYED
        );
        assert_eq!(
            worker.as_ref().unwrap().files().len(),
            FIXED_MODEL_PART_HANDLE_COUNT
        );
        for (index, file) in worker.as_ref().unwrap().files().into_iter().enumerate() {
            let mut byte = [0u8; 1];
            assert_eq!(file.seek_read(&mut byte, 0).unwrap(), 1);
            assert_eq!(byte[0], index as u8 + 1);
        }
        assert_eq!(
            lease.take_worker_handles().unwrap_err(),
            ADMISSION_WORKER_HANDLE_HANDOFF_REPLAYED
        );
        assert!(matches!(
            &*runs.slot.lock().unwrap(),
            RunAdmissionSlot::WorkerOwned { .. }
        ));

        assert!(fs::remove_file(&paths[0]).is_err());
        let terminal = pre_armed_terminal(&lease.prepared_binding);
        lease.finalize_terminal(&mut worker, &terminal).unwrap();
        assert!(worker.is_none());
        assert!(matches!(
            &*runs.slot.lock().unwrap(),
            RunAdmissionSlot::Empty
        ));
        drop(start_contract);
        remove_active_handle_fixture(&root, &paths);
    }

    #[test]
    fn terminal_finalize_closes_worker_then_originals_under_lock_before_empty() {
        let (root, paths, mut active) = active_handle_fixture("atomic-finalize");
        let runs = SharedRunAdmission::default();
        let order = Arc::new(AtomicUsize::new(0));
        let (start_tx, start_rx) = mpsc::channel();
        let (blocked_tx, blocked_rx) = mpsc::channel();
        let (finished_tx, finished_rx) = mpsc::channel();
        let contender_runs = runs.clone();
        let contender = thread::spawn(move || {
            start_rx.recv().unwrap();
            let blocked = matches!(
                contender_runs.slot.try_lock(),
                Err(TryLockError::WouldBlock)
            );
            blocked_tx.send(blocked).unwrap();
            let slot = contender_runs.slot.lock().unwrap();
            finished_tx
                .send(matches!(&*slot, RunAdmissionSlot::Empty))
                .unwrap();
        });
        let original_order = Arc::clone(&order);
        active.set_drop_callback_for_test(Box::new(move || {
            assert_eq!(original_order.fetch_add(1, Ordering::SeqCst), 1);
            start_tx.send(()).unwrap();
            assert!(blocked_rx.recv().unwrap());
        }));

        runs.stage(TEST_REQUEST, active).unwrap();
        let identity = identity(0x4b);
        let ticket = ticket(&identity, TEST_REQUEST);
        let prepared = runs
            .prepare_with_snapshot(&identity, &ticket, |handles| {
                let snapshot = handles.capture_prepare_snapshot().unwrap();
                Ok((prepared(&identity, &ticket), Some(snapshot)))
            })
            .unwrap();
        runs.queue_start(&identity, &ticket, prepared).unwrap();
        let committed = runs.commit_and_take(TEST_REQUEST).unwrap();
        let (prepared, mut lease) = committed.into_worker_parts();
        drop(prepared);
        let mut worker = lease.take_worker_handles().unwrap();
        let start_contract = consume_start_contract(&mut lease, &worker);
        let worker_order = Arc::clone(&order);
        worker.set_drop_callback_for_test(Box::new(move || {
            assert_eq!(worker_order.fetch_add(1, Ordering::SeqCst), 0);
        }));
        let mut worker = Some(worker);
        let terminal = pre_armed_terminal(&lease.prepared_binding);

        lease.finalize_terminal(&mut worker, &terminal).unwrap();

        assert!(worker.is_none());
        assert_eq!(order.load(Ordering::SeqCst), 2);
        assert!(finished_rx.recv().unwrap());
        contender.join().unwrap();
        assert!(matches!(
            &*runs.slot.lock().unwrap(),
            RunAdmissionSlot::Empty
        ));
        drop(start_contract);
        remove_active_handle_fixture(&root, &paths);
    }

    #[test]
    fn terminal_finalize_failure_retains_both_handle_sets_and_worker_slot() {
        let (root, paths, active) = active_handle_fixture("finalize-failure");
        let runs = SharedRunAdmission::default();
        runs.stage(TEST_REQUEST, active).unwrap();
        let identity = identity(0x4c);
        let ticket = ticket(&identity, TEST_REQUEST);
        let prepared = runs
            .prepare_with_snapshot(&identity, &ticket, |handles| {
                let snapshot = handles.capture_prepare_snapshot().unwrap();
                Ok((prepared(&identity, &ticket), Some(snapshot)))
            })
            .unwrap();
        runs.queue_start(&identity, &ticket, prepared).unwrap();
        let committed = runs.commit_and_take(TEST_REQUEST).unwrap();
        let (prepared, mut lease) = committed.into_worker_parts();
        drop(prepared);
        let mut worker = Some(lease.take_worker_handles().unwrap());
        let start_contract = consume_start_contract(&mut lease, worker.as_ref().unwrap());
        let drifted = ValidatedNativeTerminalRun::Burned(
            NativeBurnedRunProof::for_runtime_test(
                BurnedRunProof::for_runtime_test(
                    lease.prepared_binding.authority_identity_digest,
                    lease.prepared_binding.ticket_digest,
                    [0xd4; 32],
                    BurnReason::Failed,
                ),
                None,
            )
            .unwrap(),
        );

        assert_eq!(
            lease.finalize_terminal(&mut worker, &drifted),
            Err(ADMISSION_WORKER_TERMINAL_MISMATCH)
        );
        assert!(worker.is_some());
        assert!(lease.handles.is_some());
        assert!(fs::remove_file(&paths[0]).is_err());
        assert!(matches!(
            &*runs.slot.lock().unwrap(),
            RunAdmissionSlot::WorkerOwned { .. }
        ));

        let terminal = pre_armed_terminal(&lease.prepared_binding);
        lease.finalize_terminal(&mut worker, &terminal).unwrap();
        assert!(worker.is_none());
        drop(start_contract);
        remove_active_handle_fixture(&root, &paths);
    }

    #[test]
    fn terminal_finalize_revalidates_worker_duplicates_against_prepare_snapshot() {
        let (root, paths, active) = active_handle_fixture("terminal-snapshot-drift");
        let runs = SharedRunAdmission::default();
        runs.stage(TEST_REQUEST, active).unwrap();
        let identity = identity(0x4d);
        let ticket = ticket(&identity, TEST_REQUEST);
        let prepared = runs
            .prepare_with_snapshot(&identity, &ticket, |handles| {
                let snapshot = handles.capture_prepare_snapshot().unwrap();
                Ok((prepared(&identity, &ticket), Some(snapshot)))
            })
            .unwrap();
        runs.queue_start(&identity, &ticket, prepared).unwrap();
        let committed = runs.commit_and_take(TEST_REQUEST).unwrap();
        let (prepared, mut lease) = committed.into_worker_parts();
        drop(prepared);
        let valid_worker = lease.take_worker_handles().unwrap();
        let start_contract = consume_start_contract(&mut lease, &valid_worker);
        let mut permuted_files = valid_worker.files().map(|file| file.try_clone().unwrap());
        permuted_files.swap(0, 1);
        let mut worker = Some(WorkerScenarioHandleBundle::from_test_files(
            permuted_files,
            Arc::new(AtomicUsize::new(0)),
        ));
        drop(valid_worker);
        let terminal = pre_armed_terminal(&lease.prepared_binding);

        assert_eq!(
            lease.finalize_terminal(&mut worker, &terminal),
            Err("authority_model_part_worker_handle_snapshot_mismatch")
        );
        assert!(worker.is_some());
        assert!(lease.handles.is_some());
        assert!(fs::remove_file(&paths[0]).is_err());
        assert!(matches!(
            &*runs.slot.lock().unwrap(),
            RunAdmissionSlot::WorkerOwned { .. }
        ));

        drop(worker);
        drop(lease);
        drop(runs);
        drop(start_contract);
        remove_active_handle_fixture(&root, &paths);
    }

    #[test]
    fn worker_completion_digest_binds_the_exact_prepared_handle_snapshot() {
        let (first_root, first_paths, first_active) = active_handle_fixture("completion-first");
        let (second_root, second_paths, second_active) = active_handle_fixture("completion-second");
        let identity = identity(0x4a);
        let ticket = ticket(&identity, TEST_REQUEST);
        let prepared = prepared(&identity, &ticket);
        let first_snapshot = first_active.capture_prepare_snapshot().unwrap();
        let second_snapshot = second_active.capture_prepare_snapshot().unwrap();
        let without_snapshot = prepared_binding(&identity, &ticket, &prepared, None);
        let first_binding = prepared_binding(&identity, &ticket, &prepared, Some(first_snapshot));
        let second_binding = prepared_binding(&identity, &ticket, &prepared, Some(second_snapshot));

        let without_digest = worker_completion_digest(TEST_REQUEST, &without_snapshot);
        let first_digest = worker_completion_digest(TEST_REQUEST, &first_binding);
        let second_digest = worker_completion_digest(TEST_REQUEST, &second_binding);
        assert_ne!(without_digest, first_digest);
        assert_ne!(first_digest, second_digest);

        drop(first_active);
        drop(second_active);
        remove_active_handle_fixture(&first_root, &first_paths);
        remove_active_handle_fixture(&second_root, &second_paths);
    }

    #[test]
    fn failed_worker_clone_burns_the_handoff_and_keeps_admission_closed() {
        let (root, paths, active) = active_handle_fixture("clone-failure");
        let runs = SharedRunAdmission::default();
        runs.stage(TEST_REQUEST, active).unwrap();
        let identity = identity(0x49);
        let ticket = ticket(&identity, TEST_REQUEST);
        let prepared = runs
            .prepare_with_snapshot(&identity, &ticket, |handles| {
                let snapshot = handles.capture_prepare_snapshot().unwrap();
                Ok((prepared(&identity, &ticket), Some(snapshot)))
            })
            .unwrap();
        runs.queue_start(&identity, &ticket, prepared).unwrap();
        let committed = runs.commit_and_take(TEST_REQUEST).unwrap();
        let (prepared, mut lease) = committed.into_worker_parts();
        drop(prepared);

        assert_eq!(
            lease
                .take_worker_handles_with(|_| {
                    Err("authority_model_part_worker_handle_clone_failed")
                })
                .unwrap_err(),
            "authority_model_part_worker_handle_clone_failed"
        );
        assert_eq!(
            lease.take_worker_handles().unwrap_err(),
            ADMISSION_WORKER_HANDLE_HANDOFF_REPLAYED
        );
        assert!(matches!(
            &*runs.slot.lock().unwrap(),
            RunAdmissionSlot::WorkerOwned { .. }
        ));
        assert!(fs::remove_file(&paths[0]).is_err());

        drop(lease);
        assert!(matches!(
            &*runs.slot.lock().unwrap(),
            RunAdmissionSlot::WorkerOwned { .. }
        ));
        remove_active_handle_fixture(&root, &paths);
    }

    #[test]
    fn failed_first_start_validation_burns_authorization_and_cannot_retry() {
        let (root, paths, active) = active_handle_fixture("start-validation-failure");
        let runs = SharedRunAdmission::default();
        runs.stage(TEST_REQUEST, active).unwrap();
        let identity = identity(0x4e);
        let ticket = ticket(&identity, TEST_REQUEST);
        let prepared = runs
            .prepare_with_snapshot(&identity, &ticket, |handles| {
                let snapshot = handles.capture_prepare_snapshot().unwrap();
                Ok((prepared(&identity, &ticket), Some(snapshot)))
            })
            .unwrap();
        runs.queue_start(&identity, &ticket, prepared).unwrap();
        let committed = runs.commit_and_take(TEST_REQUEST).unwrap();
        let (prepared, mut lease) = committed.into_worker_parts();
        drop(prepared);
        let valid_worker = lease.take_worker_handles().unwrap();
        let mut drifted_files = valid_worker.files().map(|file| file.try_clone().unwrap());
        drifted_files.swap(0, 1);
        let drifted_worker = WorkerScenarioHandleBundle::from_test_files(
            drifted_files,
            Arc::new(AtomicUsize::new(0)),
        );
        let prepared_receipt_digest = lease.prepared_binding.prepared_receipt_digest;
        let policy_snapshot_digest = lease.prepared_binding.policy_snapshot_digest;

        assert_eq!(
            lease
                .consume_start_authorization(
                    &drifted_worker,
                    prepared_receipt_digest,
                    policy_snapshot_digest,
                )
                .unwrap_err(),
            "authority_model_part_worker_handle_snapshot_mismatch"
        );
        assert_eq!(
            lease
                .consume_start_authorization(
                    &valid_worker,
                    prepared_receipt_digest,
                    policy_snapshot_digest,
                )
                .unwrap_err(),
            ADMISSION_START_AUTHORIZATION_REPLAYED
        );
        assert!(lease.handles.is_some());
        assert!(fs::remove_file(&paths[0]).is_err());
        assert!(matches!(
            &*runs.slot.lock().unwrap(),
            RunAdmissionSlot::WorkerOwned { .. }
        ));

        drop(drifted_worker);
        drop(valid_worker);
        drop(lease);
        drop(runs);
        remove_active_handle_fixture(&root, &paths);
    }

    #[test]
    fn worker_terminal_must_bind_the_exact_preparation_before_slot_release() {
        let drops = Rc::new(Cell::new(0));
        let runs = SharedRunAdmission::default();
        runs.stage(
            TEST_REQUEST,
            FakeHandles {
                count: 8,
                drops: Rc::clone(&drops),
            },
        )
        .unwrap();
        let identity = identity(0x42);
        let ticket = ticket(&identity, TEST_REQUEST);
        let prepared = runs
            .prepare_with(&identity, &ticket, |_| Ok(prepared(&identity, &ticket)))
            .unwrap();
        let binding = prepared_binding(&identity, &ticket, &prepared, None);
        runs.queue_start(&identity, &ticket, prepared).unwrap();
        let committed = runs.commit_and_take(TEST_REQUEST).unwrap();
        let (prepared, mut lease) = committed.into_worker_parts();
        drop(prepared);

        let terminal = BurnedRunProof::for_runtime_test(
            binding.authority_identity_digest,
            binding.ticket_digest,
            binding.run_binding_digest,
            BurnReason::Failed,
        );
        let native = NativeBurnedRunProof::for_runtime_test(
            terminal,
            Some((
                binding.prepared_receipt_digest,
                [0x92; 32],
                binding.policy_snapshot_digest,
                [0x93; 32],
            )),
        )
        .unwrap();
        let terminal = ValidatedNativeTerminalRun::Burned(native);
        assert_eq!(lease.validate_terminal_binding(&terminal), Ok(()));

        let drifted_terminal = BurnedRunProof::for_runtime_test(
            binding.authority_identity_digest,
            binding.ticket_digest,
            [0x94; 32],
            BurnReason::Failed,
        );
        let drifted_terminal = ValidatedNativeTerminalRun::Burned(
            NativeBurnedRunProof::for_runtime_test(
                drifted_terminal,
                Some((
                    binding.prepared_receipt_digest,
                    [0x92; 32],
                    binding.policy_snapshot_digest,
                    [0x93; 32],
                )),
            )
            .unwrap(),
        );
        assert_eq!(
            lease.validate_terminal_binding(&drifted_terminal),
            Err(ADMISSION_WORKER_TERMINAL_MISMATCH)
        );
        assert_eq!(drops.get(), 0);
        assert!(matches!(
            &*runs.slot.lock().unwrap(),
            RunAdmissionSlot::WorkerOwned { .. }
        ));
        lease.release().unwrap();
        assert_eq!(drops.get(), 1);
        runs.stage(
            "run-2",
            FakeHandles {
                count: 8,
                drops: Rc::clone(&drops),
            },
        )
        .unwrap();
        runs.abort("run-2").unwrap();
        assert_eq!(drops.get(), 2);
    }

    #[test]
    fn pre_armed_burned_terminal_closes_the_exact_worker_lease() {
        let drops = Rc::new(Cell::new(0));
        let runs = SharedRunAdmission::default();
        runs.stage(
            TEST_REQUEST,
            FakeHandles {
                count: 8,
                drops: Rc::clone(&drops),
            },
        )
        .unwrap();
        let identity = identity(0x44);
        let ticket = ticket(&identity, TEST_REQUEST);
        let prepared = runs
            .prepare_with(&identity, &ticket, |_| Ok(prepared(&identity, &ticket)))
            .unwrap();
        let binding = prepared_binding(&identity, &ticket, &prepared, None);
        runs.queue_start(&identity, &ticket, prepared).unwrap();
        let committed = runs.commit_and_take(TEST_REQUEST).unwrap();
        let (prepared, mut lease) = committed.into_worker_parts();
        drop(prepared);

        let terminal = ValidatedNativeTerminalRun::Burned(
            NativeBurnedRunProof::for_runtime_test(
                BurnedRunProof::for_runtime_test(
                    binding.authority_identity_digest,
                    binding.ticket_digest,
                    binding.run_binding_digest,
                    BurnReason::Failed,
                ),
                None,
            )
            .unwrap(),
        );
        assert_eq!(lease.validate_terminal_binding(&terminal), Ok(()));
        lease.release().unwrap();
        assert_eq!(drops.get(), 1);
        assert!(matches!(
            &*runs.slot.lock().unwrap(),
            RunAdmissionSlot::Empty
        ));
    }

    #[test]
    fn dropping_worker_without_terminal_cannot_release_the_slot() {
        let drops = Rc::new(Cell::new(0));
        let runs = SharedRunAdmission::default();
        runs.stage(
            TEST_REQUEST,
            FakeHandles {
                count: 8,
                drops: Rc::clone(&drops),
            },
        )
        .unwrap();
        let identity = identity(0x43);
        let ticket = ticket(&identity, TEST_REQUEST);
        let prepared = runs
            .prepare_with(&identity, &ticket, |_| Ok(prepared(&identity, &ticket)))
            .unwrap();
        runs.queue_start(&identity, &ticket, prepared).unwrap();
        runs.commit(TEST_REQUEST).unwrap();
        let committed = runs.take_committed().unwrap();
        drop(committed);
        assert_eq!(drops.get(), 1);
        assert_eq!(
            runs.stage(
                "run-2",
                FakeHandles {
                    count: 8,
                    drops: Rc::clone(&drops),
                },
            )
            .unwrap_err()
            .code(),
            ADMISSION_WORKER_OWNED
        );
        assert_eq!(drops.get(), 2);
    }

    #[test]
    fn poisoned_lock_rejects_new_admission_and_drop_releases_the_handle() {
        let drops = Rc::new(Cell::new(0));
        let runs = SharedRunAdmission::default();
        runs.stage(
            TEST_REQUEST,
            FakeHandles {
                count: 8,
                drops: Rc::clone(&drops),
            },
        )
        .unwrap();
        let poison = runs.clone();
        assert!(catch_unwind(AssertUnwindSafe(|| {
            let _slot = poison.slot.lock().unwrap();
            panic!("poison admission lock");
        }))
        .is_err());
        assert_eq!(
            runs.commit(TEST_REQUEST).unwrap_err().code(),
            ADMISSION_LOCK_POISONED
        );
        drop(poison);
        drop(runs);
        assert_eq!(drops.get(), 1);
    }

    #[test]
    fn worker_completion_lock_failure_retains_handles_in_the_terminal_lease() {
        let drops = Rc::new(Cell::new(0));
        let runs = SharedRunAdmission::default();
        runs.stage(
            TEST_REQUEST,
            FakeHandles {
                count: 8,
                drops: Rc::clone(&drops),
            },
        )
        .unwrap();
        let identity = identity(0x45);
        let ticket = ticket(&identity, TEST_REQUEST);
        let prepared = runs
            .prepare_with(&identity, &ticket, |_| Ok(prepared(&identity, &ticket)))
            .unwrap();
        runs.queue_start(&identity, &ticket, prepared).unwrap();
        let committed = runs.commit_and_take(TEST_REQUEST).unwrap();
        let (prepared, mut lease) = committed.into_worker_parts();
        drop(prepared);

        let poison = runs.clone();
        assert!(catch_unwind(AssertUnwindSafe(|| {
            let _slot = poison.slot.lock().unwrap();
            panic!("poison worker completion lock");
        }))
        .is_err());
        assert_eq!(
            lease.release().unwrap_err(),
            ADMISSION_WORKER_COMPLETION_ORDER_INVALID
        );
        assert_eq!(drops.get(), 0);
        drop(lease);
        assert_eq!(drops.get(), 1);
    }
}
