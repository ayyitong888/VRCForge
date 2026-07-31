//! Durable, single-step transition contract for the native Starting phase.
//!
//! Every externally visible action has a sealed intent before it runs and a
//! sealed observation before the next action can begin. The operating-system
//! adapter owns the held stage-journal handle; this module accepts progress
//! only after an exact reopen/readback of that handle.

use super::stage_journal::{
    StageAction, StageJournal, StageJournalAppend, StageJournalBinding, StageJournalRecordKind,
    StageTerminationKind,
};
use super::*;
use crate::primitive_evidence_authority_pipe::{
    ScenarioStartExecutableRole, VerifiedScenarioExecutableCreateBinding,
    VerifiedScenarioExecutableLaunch, VerifiedScenarioStartContract,
};

const STAGE_DECLARATION_DOMAIN: &[u8] = b"vrcforge-native-stage-declaration-v2\0";
const STAGE_ACTION_INTENT_DOMAIN: &[u8] = b"vrcforge-native-stage-action-intent-v1\0";
const STAGE_PREPARED_OBSERVATION_DOMAIN: &[u8] = b"vrcforge-native-stage-prepared-observation-v2\0";
const STAGE_ROOT_CREATED_OBSERVATION_DOMAIN: &[u8] =
    b"vrcforge-native-stage-root-created-observation-v4\0";
const STAGE_ROOT_RESUMED_OBSERVATION_DOMAIN: &[u8] =
    b"vrcforge-native-stage-root-resumed-observation-v4\0";
const STAGE_TERMINAL_OBSERVATION_DOMAIN: &[u8] = b"vrcforge-native-stage-terminal-observation-v1\0";
const STAGE_HELD_HANDLE_BINDING_DOMAIN: &[u8] = b"vrcforge-native-stage-held-handle-binding-v1\0";
const RECOVERY_BUNDLE_DOMAIN: &[u8] = b"vrcforge-authority-recovery-bundle-v1\0";
const STAGED_API_NOT_CONNECTED: &str = "authority_native_staged_start_not_connected";

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeStageJournalStoreReadback {
    pub(crate) canonical_bytes: Vec<u8>,
    pub(crate) file_identity: FileIdentity,
    pub(crate) parent_identity: FileIdentity,
    pub(crate) created_new: bool,
    pub(crate) write_sequence: u64,
    pub(crate) append_flushed: bool,
    pub(crate) parent_flushed_after_create: bool,
    pub(crate) reopened_from_held_handle: bool,
    pub(crate) service_owned_parent: bool,
    pub(crate) owner_local_system: bool,
    pub(crate) protected_restricted_dacl: bool,
    pub(crate) file_is_reparse_point: bool,
    pub(crate) parent_is_reparse_point: bool,
    pub(crate) single_link: bool,
    pub(crate) service_handle_held: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum NativeStageJournalCreateOutcome {
    Created(NativeStageJournalStoreReadback),
    Uncertain(NativeStageJournalCreateUncertainty, &'static str),
    Reopened(NativeStageJournalStoreReadback),
    RejectedNoMutation(&'static str),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum NativeStageJournalCreateMode {
    Create,
    ReconcileOnly { held_handle_binding_digest: Digest },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct NativeStageJournalCreateUncertainty {
    pub(crate) file_identity: FileIdentity,
    pub(crate) parent_identity: FileIdentity,
    pub(crate) held_handle_binding_digest: Digest,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeCreatedRootReceipt {
    pub(crate) start_contract_digest: Digest,
    pub(crate) executable_binding: VerifiedScenarioExecutableCreateBinding,
    pub(crate) suspended: SuspendedRootReceipt,
    pub(crate) membership: JobAssignmentReceipt,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeStageTerminationReadback {
    pub(crate) kind: NativeTerminationKind,
    pub(crate) requested_at_unix_ms: u64,
    pub(crate) recorded_at_unix_ms: u64,
    pub(crate) journal: NativeStageJournalStoreReadback,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum NativeStageTerminationAttempt {
    Recorded(NativeStageTerminationReadback),
    Uncertain,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum NativeStartingTerminationAcknowledgement {
    Recorded(NativeTerminationKind),
    Uncertain,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeStartingTerminationEvidence {
    pub(crate) observation: AuthorityOwnedAbortObservation,
    pub(crate) native_cleanup: NativeCleanupReceipt,
    pub(crate) terminal_digest: Digest,
    pub(crate) cleanup_digest: Digest,
}

/// Native methods that can be called one bounded step at a time. The service
/// implementation must keep all live handles internally; receipts crossing
/// this seam are readback-only evidence.
pub(crate) trait ServiceOwnedStagedNativeApi: ServiceOwnedNativeApi {
    /// `Create` may perform the one declaration mutation. If its durable
    /// acknowledgement is uncertain, the returned witness binds the service's
    /// held handle. Every later call is `ReconcileOnly`: it may only read and
    /// reopen that same handle, never create, replace, or append. A
    /// `RejectedNoMutation` outcome certifies that the current call made no
    /// filesystem mutation.
    fn create_stage_journal(
        &mut self,
        _policy: &SupervisorPolicy,
        _canonical_bytes: &[u8],
        _mode: NativeStageJournalCreateMode,
    ) -> NativeStageJournalCreateOutcome {
        NativeStageJournalCreateOutcome::RejectedNoMutation(STAGED_API_NOT_CONNECTED)
    }

    /// Appends exactly one planned record. If a transport interruption occurs
    /// after the durable write, retrying the same prior length and record must
    /// return the already-written exact readback instead of appending twice.
    fn append_stage_journal(
        &mut self,
        _policy: &SupervisorPolicy,
        _prior_byte_len: usize,
        _record_bytes: &[u8],
    ) -> Result<NativeStageJournalStoreReadback, SupervisorError> {
        Err(SupervisorError::new(STAGED_API_NOT_CONNECTED))
    }

    fn prepare_foundation(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &PreparedRecoveryReceipt,
        _policy_snapshot: &[u8],
        _start_contract: &VerifiedScenarioStartContract,
    ) -> Result<NativePreparedFoundation, SupervisorError> {
        Err(SupervisorError::new(STAGED_API_NOT_CONNECTED))
    }

    fn create_root_suspended_in_job(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &NativePreparedFoundation,
        _executable: &VerifiedScenarioExecutableLaunch<'_>,
        _role: ProcessRole,
    ) -> Result<NativeCreatedRootReceipt, SupervisorError> {
        Err(SupervisorError::new(STAGED_API_NOT_CONNECTED))
    }

    fn resume_staged_root(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &NativePreparedFoundation,
        _start_contract: &VerifiedScenarioStartContract,
        _role: ProcessRole,
        _created: &NativeCreatedRootReceipt,
    ) -> Result<ResumedRootReceipt, SupervisorError> {
        Err(SupervisorError::new(STAGED_API_NOT_CONNECTED))
    }

    /// The service chooses Cancelled versus TimedOut at the durable append
    /// point using its own clock. The caller supplies neither a kind nor time.
    /// An uncertain transport result must be returned as `Uncertain`, never as
    /// an error that could make the caller discard the live Starting state.
    fn record_stage_termination(
        &mut self,
        _policy: &SupervisorPolicy,
        _journal: &StageJournal,
        _armed_receipt_digest: Option<Digest>,
    ) -> Result<NativeStageTerminationAttempt, SupervisorError> {
        Err(SupervisorError::new(STAGED_API_NOT_CONNECTED))
    }

    fn contain_starting_termination(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &PreparedRecoveryReceipt,
        _journal: &StageJournal,
        _kind: NativeTerminationKind,
    ) -> Result<NativeStartingTerminationEvidence, SupervisorError> {
        Err(SupervisorError::new(STAGED_API_NOT_CONNECTED))
    }
}

impl ServiceOwnedStagedNativeApi for WindowsNativeSupervisorApi {}

#[derive(Debug, Clone)]
pub(super) struct NativeStageJournalLease {
    pub(super) journal: StageJournal,
    pub(super) file_identity: FileIdentity,
    pub(super) parent_identity: FileIdentity,
    pub(super) write_sequence: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct NativeCompletedStageJournalBinding {
    pub(super) start_contract_digest: Digest,
    journal_binding_digest: Digest,
    canonical_journal_digest: Digest,
    head_sequence: u64,
    head_digest: Digest,
    armed_receipt_digest: Digest,
    binding_digest: Digest,
}

impl NativeCompletedStageJournalBinding {
    pub(super) fn from_verified_clean_armed(
        replay: stage_journal::VerifiedStageJournalReplay,
        journal_binding: &StageJournalBinding,
        canonical_journal_bytes: &[u8],
        armed_receipt_digest: Digest,
    ) -> Result<Self, SupervisorError> {
        if replay.stage() != stage_journal::StageJournalReplayStage::Armed
            || replay.next_action().is_some()
            || replay.pending_observation().is_some()
            || replay.termination_kind().is_some()
            || replay.terminal_payload_digest().is_some()
            || replay.cleanup_payload_digest().is_some()
            || replay.armed_receipt_digest() != Some(armed_receipt_digest)
            || canonical_journal_bytes.is_empty()
        {
            return Err(SupervisorError::new(
                "authority_native_completed_stage_journal_invalid",
            ));
        }
        let mut value = Self {
            start_contract_digest: journal_binding.start_contract_digest(),
            journal_binding_digest: journal_binding.digest(),
            canonical_journal_digest: Sha256::digest(canonical_journal_bytes).into(),
            head_sequence: replay.head_sequence(),
            head_digest: replay.head_digest(),
            armed_receipt_digest,
            binding_digest: [0; 32],
        };
        value.binding_digest = completed_stage_journal_binding_digest(&value);
        if !value.verifies() {
            return Err(SupervisorError::new(
                "authority_native_completed_stage_journal_invalid",
            ));
        }
        Ok(value)
    }

    pub(super) fn verifies(&self) -> bool {
        self.head_sequence != 0
            && [
                self.start_contract_digest,
                self.journal_binding_digest,
                self.canonical_journal_digest,
                self.head_digest,
                self.armed_receipt_digest,
                self.binding_digest,
            ]
            .iter()
            .all(|digest| !is_zero_digest(digest))
            && self.binding_digest == completed_stage_journal_binding_digest(self)
    }

    #[cfg(test)]
    pub(super) fn for_runtime_test(
        start_contract_digest: Digest,
        armed_receipt_digest: Digest,
    ) -> Self {
        let mut value = Self {
            start_contract_digest,
            journal_binding_digest: Sha256::digest(
                [
                    b"vrcforge-runtime-test-completed-stage-binding-v1\0".as_slice(),
                    start_contract_digest.as_slice(),
                ]
                .concat(),
            )
            .into(),
            canonical_journal_digest: Sha256::digest(
                [
                    b"vrcforge-runtime-test-completed-stage-journal-v1\0".as_slice(),
                    armed_receipt_digest.as_slice(),
                ]
                .concat(),
            )
            .into(),
            head_sequence: 1,
            head_digest: Sha256::digest(
                [
                    b"vrcforge-runtime-test-completed-stage-head-v1\0".as_slice(),
                    armed_receipt_digest.as_slice(),
                ]
                .concat(),
            )
            .into(),
            armed_receipt_digest,
            binding_digest: [0; 32],
        };
        value.binding_digest = completed_stage_journal_binding_digest(&value);
        value
    }
}

fn completed_stage_journal_binding_digest(value: &NativeCompletedStageJournalBinding) -> Digest {
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-native-completed-stage-journal-binding-v1\0");
    digest.update(value.start_contract_digest);
    digest.update(value.journal_binding_digest);
    digest.update(value.canonical_journal_digest);
    digest.update(value.head_sequence.to_be_bytes());
    digest.update(value.head_digest);
    digest.update(value.armed_receipt_digest);
    digest.finalize().into()
}

#[derive(Debug, Clone)]
struct StartingTermination {
    kind: NativeTerminationKind,
    requested_at_unix_ms: u64,
    recorded_at_unix_ms: u64,
    stage_head_digest: Digest,
    intent_digest: Digest,
    evidence: Option<NativeStartingTerminationEvidence>,
}

#[derive(Debug, Clone)]
enum NativeStartingContinuation {
    Prepared(NativePreparedFoundation),
    BridgeCreated(NativePreparedFoundation, NativeCreatedRootReceipt),
    BridgeReady(NativePreparedFoundation, AtomicRootLaunchReceipt),
    DriverCreated(
        NativePreparedFoundation,
        AtomicRootLaunchReceipt,
        NativeCreatedRootReceipt,
    ),
    DriverReady(
        NativePreparedFoundation,
        AtomicRootLaunchReceipt,
        AtomicRootLaunchReceipt,
    ),
}

impl NativeStartingContinuation {
    fn into_phase(self) -> NativeStartingPhase {
        match self {
            Self::Prepared(foundation) => NativeStartingPhase::Prepared(foundation),
            Self::BridgeCreated(foundation, created) => {
                NativeStartingPhase::BridgeCreated(foundation, created)
            }
            Self::BridgeReady(foundation, bridge) => {
                NativeStartingPhase::BridgeReady(foundation, bridge)
            }
            Self::DriverCreated(foundation, bridge, created) => {
                NativeStartingPhase::DriverCreated(foundation, bridge, created)
            }
            Self::DriverReady(foundation, bridge, driver) => {
                NativeStartingPhase::DriverReady(foundation, bridge, driver)
            }
        }
    }
}

#[derive(Debug, Clone)]
struct NativePendingStageObservation {
    action: StageAction,
    observation_digest: Digest,
    continuation: NativeStartingContinuation,
}

#[derive(Debug, Clone)]
enum NativeStartingPhase {
    Declaring(NativeStageJournalCreateMode),
    PrepareReady,
    PrepareIntent,
    Prepared(NativePreparedFoundation),
    BridgeCreateIntent(NativePreparedFoundation),
    BridgeCreated(NativePreparedFoundation, NativeCreatedRootReceipt),
    BridgeResumeIntent(NativePreparedFoundation, NativeCreatedRootReceipt),
    BridgeReady(NativePreparedFoundation, AtomicRootLaunchReceipt),
    DriverCreateIntent(NativePreparedFoundation, AtomicRootLaunchReceipt),
    DriverCreated(
        NativePreparedFoundation,
        AtomicRootLaunchReceipt,
        NativeCreatedRootReceipt,
    ),
    DriverResumeIntent(
        NativePreparedFoundation,
        AtomicRootLaunchReceipt,
        NativeCreatedRootReceipt,
    ),
    DriverReady(
        NativePreparedFoundation,
        AtomicRootLaunchReceipt,
        AtomicRootLaunchReceipt,
    ),
    ArmIntent(
        NativePreparedFoundation,
        AtomicRootLaunchReceipt,
        AtomicRootLaunchReceipt,
    ),
    ObservationPending(NativePendingStageObservation),
    FailureContainment {
        failure_code: &'static str,
        failed_phase: NativeSupervisorPhase,
    },
}

enum NativeStageDeclarationResolution {
    Ready,
    Uncertain(&'static str),
    Rejected(&'static str),
}

pub(crate) struct NativeStartingRun {
    policy: SupervisorPolicy,
    prepared_receipt: PreparedRecoveryReceipt,
    policy_snapshot: Vec<u8>,
    start_contract_digest: Digest,
    pending_declaration: Option<StageJournal>,
    journal: Option<NativeStageJournalLease>,
    phase: NativeStartingPhase,
    termination: Option<StartingTermination>,
}

#[cfg(test)]
impl NativeStartingRun {
    pub(crate) fn declaration_mode_for_test(&self) -> Option<NativeStageJournalCreateMode> {
        match self.phase {
            NativeStartingPhase::Declaring(mode) => Some(mode),
            _ => None,
        }
    }

    pub(crate) fn journal_bytes_for_test(&self) -> &[u8] {
        match (&self.pending_declaration, &self.journal) {
            (Some(declaration), None) => declaration.bytes(),
            (None, Some(lease)) => lease.journal.bytes(),
            _ => &[],
        }
    }
}

pub(crate) enum NativeStartingAdvance {
    Starting(NativeStartingRun),
    Armed(NativeArmedRun),
    Terminal(ValidatedNativeTerminalRun),
    Retrying(NativeStartingRun, &'static str),
}

impl<A: ServiceOwnedNativeApi + ServiceOwnedStagedNativeApi> ServiceOwnedNativeSupervisor<A> {
    pub(crate) fn begin_start(
        &mut self,
        prepared_run: PreparedRun,
        start_contract: &VerifiedScenarioStartContract,
    ) -> Result<NativeStartingRun, SupervisorError> {
        let policy = decode_supervisor_policy_snapshot(prepared_run.policy_snapshot())?;
        if !prepared_run.receipt().verifies_policy(&policy) {
            return Err(SupervisorError::new(
                "authority_native_prepared_policy_mismatch",
            ));
        }
        let prepared_receipt = prepared_run.receipt().clone();
        let policy_snapshot = prepared_run.policy_snapshot().to_vec();
        let policy_snapshot_digest: Digest = Sha256::digest(&policy_snapshot).into();
        if start_contract
            .snapshot_digest()
            .iter()
            .all(|byte| *byte == 0)
            || !start_contract.verifies_for(&prepared_receipt.digest(), &policy_snapshot_digest)
        {
            return Err(SupervisorError::new(
                "authority_native_start_contract_binding_invalid",
            ));
        }
        let binding = stage_journal_binding(
            &policy,
            &prepared_receipt,
            &policy_snapshot,
            start_contract.binding_digest(),
        )?;
        let declaration_digest = stage_declaration_digest(&policy, &binding);
        let journal = StageJournal::declare(binding, declaration_digest)
            .map_err(|error| SupervisorError::new(error.code()))?;
        Ok(NativeStartingRun {
            policy,
            prepared_receipt,
            policy_snapshot,
            start_contract_digest: start_contract.binding_digest(),
            pending_declaration: Some(journal),
            journal: None,
            phase: NativeStartingPhase::Declaring(NativeStageJournalCreateMode::Create),
            termination: None,
        })
    }

    pub(crate) fn starting_deadline(run: &NativeStartingRun) -> u64 {
        run.policy.deadline
    }

    pub(crate) fn request_starting_termination(
        &mut self,
        run: &mut NativeStartingRun,
    ) -> Result<NativeStartingTerminationAcknowledgement, SupervisorError> {
        if let Some(existing) = &run.termination {
            return Ok(NativeStartingTerminationAcknowledgement::Recorded(
                existing.kind,
            ));
        }
        if matches!(run.phase, NativeStartingPhase::Declaring(_)) {
            match self.reconcile_stage_declaration(run) {
                NativeStageDeclarationResolution::Ready => {}
                NativeStageDeclarationResolution::Uncertain(_) => {
                    return Ok(NativeStartingTerminationAcknowledgement::Uncertain);
                }
                NativeStageDeclarationResolution::Rejected(code) => {
                    mark_failed_start(run, code);
                    return Err(SupervisorError::new(code));
                }
            }
        }
        if let NativeStartingPhase::FailureContainment { failure_code, .. } = &run.phase {
            return Err(SupervisorError::new(*failure_code));
        }
        if matches!(run.phase, NativeStartingPhase::ObservationPending(_))
            && self.flush_pending_observation(run).is_err()
        {
            // The action already happened. Until its exact observation is
            // reconciled, no new action or conflicting terminal record may be
            // issued. The caller keeps the live run and retries this boundary.
            return Ok(NativeStartingTerminationAcknowledgement::Uncertain);
        }
        let prior_head = open_stage_lease(run)?.journal.head();
        let response = match self.api.record_stage_termination(
            &run.policy,
            &open_stage_lease(run)?.journal,
            None,
        )? {
            NativeStageTerminationAttempt::Recorded(response) => response,
            NativeStageTerminationAttempt::Uncertain => {
                return Ok(NativeStartingTerminationAcknowledgement::Uncertain);
            }
        };
        validate_stage_termination_timing(&run.policy, &response)?;
        let planned = open_stage_lease(run)?
            .journal
            .plan_termination_intent(
                stage_termination_kind(response.kind),
                response.requested_at_unix_ms,
                response.recorded_at_unix_ms,
                None,
            )
            .map_err(|error| SupervisorError::new(error.code()))?;
        append_readback(open_stage_lease_mut(run)?, &planned, response.journal)?;
        let intent_digest = open_stage_lease(run)?.journal.head().record_digest;
        run.termination = Some(StartingTermination {
            kind: response.kind,
            requested_at_unix_ms: response.requested_at_unix_ms,
            recorded_at_unix_ms: response.recorded_at_unix_ms,
            stage_head_digest: prior_head.record_digest,
            intent_digest,
            evidence: None,
        });
        Ok(NativeStartingTerminationAcknowledgement::Recorded(
            response.kind,
        ))
    }

    pub(crate) fn advance_starting(
        &mut self,
        mut run: NativeStartingRun,
        start_contract: &VerifiedScenarioStartContract,
    ) -> NativeStartingAdvance {
        let policy_snapshot_digest: Digest = Sha256::digest(&run.policy_snapshot).into();
        if start_contract.binding_digest() != run.start_contract_digest
            || !start_contract.verifies_for(&run.prepared_receipt.digest(), &policy_snapshot_digest)
        {
            return self
                .contain_failed_start(run, "authority_native_start_contract_binding_invalid");
        }
        if matches!(run.phase, NativeStartingPhase::Declaring(_)) {
            return match self.reconcile_stage_declaration(&mut run) {
                NativeStageDeclarationResolution::Ready => NativeStartingAdvance::Starting(run),
                NativeStageDeclarationResolution::Uncertain(code) => {
                    NativeStartingAdvance::Retrying(run, code)
                }
                NativeStageDeclarationResolution::Rejected(code) => {
                    self.contain_failed_start(run, code)
                }
            };
        }
        if run.termination.is_some() {
            return self.finish_starting_termination(run);
        }

        if matches!(run.phase, NativeStartingPhase::ObservationPending(_)) {
            return match self.flush_pending_observation(&mut run) {
                Ok(()) => NativeStartingAdvance::Starting(run),
                Err(error) => NativeStartingAdvance::Retrying(run, error.code()),
            };
        }

        macro_rules! contain_on_error {
            ($expression:expr) => {
                match $expression {
                    Ok(value) => value,
                    Err(error) => {
                        return self.contain_failed_start(run, error.code());
                    }
                }
            };
        }

        match run.phase.clone() {
            NativeStartingPhase::Declaring(_) => {
                unreachable!("declaration is reconciled before phase dispatch")
            }
            NativeStartingPhase::PrepareReady => {
                contain_on_error!(self.append_action_intent(&mut run, StageAction::Prepare));
                run.phase = NativeStartingPhase::PrepareIntent;
            }
            NativeStartingPhase::PrepareIntent => {
                let capabilities = contain_on_error!(self.api.preflight(&run.policy));
                contain_on_error!(validate_capabilities(&capabilities));
                let foundation = contain_on_error!(self.api.prepare_foundation(
                    &run.policy,
                    &run.prepared_receipt,
                    &run.policy_snapshot,
                    start_contract,
                ));
                contain_on_error!(validate_native_prepared_foundation(
                    &run.policy,
                    &run.prepared_receipt,
                    &run.policy_snapshot,
                    &foundation,
                ));
                contain_on_error!(validate_start_contract_foundation(
                    start_contract,
                    &foundation,
                ));
                let digest = prepared_foundation_digest(&foundation);
                return self.defer_observation(
                    run,
                    StageAction::Prepare,
                    digest,
                    NativeStartingContinuation::Prepared(foundation),
                );
            }
            NativeStartingPhase::Prepared(foundation) => {
                contain_on_error!(self.append_action_intent(&mut run, StageAction::BridgeCreate));
                run.phase = NativeStartingPhase::BridgeCreateIntent(foundation);
            }
            NativeStartingPhase::BridgeCreateIntent(foundation) => {
                let executable = contain_on_error!(start_contract
                    .prepare_executable_launch(ScenarioStartExecutableRole::BridgeLauncher,));
                contain_on_error!(validate_executable_launch_view(
                    start_contract,
                    &executable,
                    ScenarioStartExecutableRole::BridgeLauncher,
                ));
                let created = contain_on_error!(self.api.create_root_suspended_in_job(
                    &run.policy,
                    &foundation,
                    &executable,
                    ProcessRole::BridgeLauncher,
                ));
                contain_on_error!(executable.validate_created_process_image(
                    &created.executable_binding,
                    file_identity_digest(&created.suspended.image_identity),
                ));
                contain_on_error!(validate_created_root(
                    &run.policy,
                    &foundation,
                    &created,
                    ProcessRole::BridgeLauncher,
                ));
                let digest = created_root_digest(&created);
                return self.defer_observation(
                    run,
                    StageAction::BridgeCreate,
                    digest,
                    NativeStartingContinuation::BridgeCreated(foundation, created),
                );
            }
            NativeStartingPhase::BridgeCreated(foundation, created) => {
                contain_on_error!(self.append_action_intent(&mut run, StageAction::BridgeResume));
                run.phase = NativeStartingPhase::BridgeResumeIntent(foundation, created);
            }
            NativeStartingPhase::BridgeResumeIntent(foundation, created) => {
                let resumed = contain_on_error!(self.api.resume_staged_root(
                    &run.policy,
                    &foundation,
                    start_contract,
                    ProcessRole::BridgeLauncher,
                    &created,
                ));
                contain_on_error!(validate_resumed_root(
                    &run.policy,
                    &foundation,
                    &created.suspended,
                    &created.membership,
                    &resumed,
                ));
                let digest = resumed_root_digest(ProcessRole::BridgeLauncher, &resumed);
                return self.defer_observation(
                    run,
                    StageAction::BridgeResume,
                    digest,
                    NativeStartingContinuation::BridgeReady(
                        foundation,
                        AtomicRootLaunchReceipt {
                            suspended: created.suspended,
                            membership: created.membership,
                            resumed,
                        },
                    ),
                );
            }
            NativeStartingPhase::BridgeReady(foundation, bridge) => {
                contain_on_error!(self.append_action_intent(&mut run, StageAction::DriverCreate));
                run.phase = NativeStartingPhase::DriverCreateIntent(foundation, bridge);
            }
            NativeStartingPhase::DriverCreateIntent(foundation, bridge) => {
                let executable =
                    contain_on_error!(start_contract
                        .prepare_executable_launch(ScenarioStartExecutableRole::Driver,));
                contain_on_error!(validate_executable_launch_view(
                    start_contract,
                    &executable,
                    ScenarioStartExecutableRole::Driver,
                ));
                let created = contain_on_error!(self.api.create_root_suspended_in_job(
                    &run.policy,
                    &foundation,
                    &executable,
                    ProcessRole::Driver,
                ));
                contain_on_error!(executable.validate_created_process_image(
                    &created.executable_binding,
                    file_identity_digest(&created.suspended.image_identity),
                ));
                contain_on_error!(validate_created_root(
                    &run.policy,
                    &foundation,
                    &created,
                    ProcessRole::Driver,
                ));
                if created.suspended.created_suspended_at <= bridge.resumed.resumed_at {
                    return self
                        .contain_failed_start(run, "authority_native_root_launch_order_invalid");
                }
                let digest = created_root_digest(&created);
                return self.defer_observation(
                    run,
                    StageAction::DriverCreate,
                    digest,
                    NativeStartingContinuation::DriverCreated(foundation, bridge, created),
                );
            }
            NativeStartingPhase::DriverCreated(foundation, bridge, created) => {
                contain_on_error!(self.append_action_intent(&mut run, StageAction::DriverResume));
                run.phase = NativeStartingPhase::DriverResumeIntent(foundation, bridge, created);
            }
            NativeStartingPhase::DriverResumeIntent(foundation, bridge, created) => {
                let resumed = contain_on_error!(self.api.resume_staged_root(
                    &run.policy,
                    &foundation,
                    start_contract,
                    ProcessRole::Driver,
                    &created,
                ));
                contain_on_error!(validate_resumed_root(
                    &run.policy,
                    &foundation,
                    &created.suspended,
                    &created.membership,
                    &resumed,
                ));
                let digest = resumed_root_digest(ProcessRole::Driver, &resumed);
                return self.defer_observation(
                    run,
                    StageAction::DriverResume,
                    digest,
                    NativeStartingContinuation::DriverReady(
                        foundation,
                        bridge,
                        AtomicRootLaunchReceipt {
                            suspended: created.suspended,
                            membership: created.membership,
                            resumed,
                        },
                    ),
                );
            }
            NativeStartingPhase::DriverReady(foundation, bridge, driver) => {
                contain_on_error!(self.append_action_intent(&mut run, StageAction::Arm));
                run.phase = NativeStartingPhase::ArmIntent(foundation, bridge, driver);
            }
            NativeStartingPhase::ArmIntent(foundation, bridge, driver) => {
                return self.finish_arming(run, foundation, bridge, driver);
            }
            NativeStartingPhase::ObservationPending(_) => {
                unreachable!("pending observations are reconciled before phase dispatch")
            }
            NativeStartingPhase::FailureContainment { failure_code, .. } => {
                return self.contain_failed_start(run, failure_code);
            }
        }
        NativeStartingAdvance::Starting(run)
    }

    fn reconcile_stage_declaration(
        &mut self,
        run: &mut NativeStartingRun,
    ) -> NativeStageDeclarationResolution {
        let mode = match run.phase {
            NativeStartingPhase::Declaring(mode) => mode,
            _ => {
                return NativeStageDeclarationResolution::Rejected(
                    "authority_stage_journal_declaration_state_invalid",
                );
            }
        };
        let Some(declaration) = run.pending_declaration.as_ref() else {
            return NativeStageDeclarationResolution::Rejected(
                "authority_stage_journal_declaration_missing",
            );
        };
        let outcome = self
            .api
            .create_stage_journal(&run.policy, declaration.bytes(), mode);
        let expected_reopen_binding = match mode {
            NativeStageJournalCreateMode::Create => None,
            NativeStageJournalCreateMode::ReconcileOnly {
                held_handle_binding_digest,
            } => Some(held_handle_binding_digest),
        };
        let (readback, expected_created_new) = match outcome {
            NativeStageJournalCreateOutcome::Created(readback)
                if mode == NativeStageJournalCreateMode::Create =>
            {
                (readback, true)
            }
            NativeStageJournalCreateOutcome::Created(_) => {
                return NativeStageDeclarationResolution::Rejected(
                    "authority_stage_journal_reconcile_created_new",
                );
            }
            NativeStageJournalCreateOutcome::Uncertain(uncertainty, code) => {
                let binding =
                    match validate_stage_create_uncertainty(&uncertainty, declaration.bytes()) {
                        Ok(binding) => binding,
                        Err(error) => {
                            return NativeStageDeclarationResolution::Rejected(error.code());
                        }
                    };
                if expected_reopen_binding.is_some_and(|expected| expected != binding) {
                    return NativeStageDeclarationResolution::Rejected(
                        "authority_stage_journal_uncertain_handle_drift",
                    );
                }
                run.phase =
                    NativeStartingPhase::Declaring(NativeStageJournalCreateMode::ReconcileOnly {
                        held_handle_binding_digest: binding,
                    });
                return NativeStageDeclarationResolution::Uncertain(code);
            }
            NativeStageJournalCreateOutcome::Reopened(readback)
                if expected_reopen_binding.is_some() =>
            {
                (readback, false)
            }
            NativeStageJournalCreateOutcome::Reopened(_) => {
                return NativeStageDeclarationResolution::Rejected(
                    "authority_stage_journal_unexpected_reopen",
                );
            }
            NativeStageJournalCreateOutcome::RejectedNoMutation(code) => {
                return NativeStageDeclarationResolution::Rejected(code);
            }
        };
        if let Err(error) = validate_stage_store_readback(
            &readback,
            declaration.bytes(),
            None,
            expected_created_new,
            true,
        ) {
            return NativeStageDeclarationResolution::Rejected(error.code());
        }
        if let Err(error) = declaration.verify_declared_reopen(&readback.canonical_bytes) {
            return NativeStageDeclarationResolution::Rejected(error.code());
        }
        if let Some(expected_binding) = expected_reopen_binding {
            let reopened_binding = stage_journal_held_handle_binding_digest(
                declaration.bytes(),
                &readback.file_identity,
                &readback.parent_identity,
            );
            if reopened_binding != expected_binding {
                return NativeStageDeclarationResolution::Rejected(
                    "authority_stage_journal_reopened_handle_drift",
                );
            }
        }
        let declaration = run
            .pending_declaration
            .take()
            .expect("declaration presence was checked above");
        run.journal = Some(NativeStageJournalLease {
            journal: declaration,
            file_identity: readback.file_identity,
            parent_identity: readback.parent_identity,
            write_sequence: readback.write_sequence,
        });
        run.phase = NativeStartingPhase::PrepareReady;
        NativeStageDeclarationResolution::Ready
    }

    pub(crate) fn abort_starting(
        &mut self,
        run: &NativeStartingRun,
        failure_code: &'static str,
    ) -> Result<NativeBurnedRunProof, SupervisorError> {
        let abort = self.api.contain_after_failure(
            &run.policy,
            &run.prepared_receipt,
            None,
            phase_for_starting(&run.phase),
            BurnReason::Failed,
            failure_code,
        )?;
        validate_native_abort_cleanup(&abort, phase_for_starting(&run.phase), failure_code)?;
        let terminal = validate_authority_owned_abort(
            &run.policy,
            &run.prepared_receipt,
            None,
            &abort.observation,
            BurnReason::Failed,
        )?;
        Ok(NativeBurnedRunProof {
            terminal,
            admission: None,
            normal_termination_recovery: None,
        })
    }

    fn contain_failed_start(
        &mut self,
        mut run: NativeStartingRun,
        failure_code: &'static str,
    ) -> NativeStartingAdvance {
        mark_failed_start(&mut run, failure_code);
        match self.abort_starting(&run, failure_code) {
            Ok(proof) => NativeStartingAdvance::Terminal(ValidatedNativeTerminalRun::Burned(proof)),
            Err(error) => NativeStartingAdvance::Retrying(run, error.code()),
        }
    }

    fn append_action_intent(
        &mut self,
        run: &mut NativeStartingRun,
        action: StageAction,
    ) -> Result<(), SupervisorError> {
        let intent_digest = stage_action_intent_digest(&open_stage_lease(run)?.journal, action);
        let append = open_stage_lease(run)?
            .journal
            .plan_action_intent(action, intent_digest)
            .map_err(|error| SupervisorError::new(error.code()))?;
        let readback = self.api.append_stage_journal(
            &run.policy,
            append.prior_byte_len(),
            append.record_bytes(),
        )?;
        append_readback(open_stage_lease_mut(run)?, &append, readback)
    }

    fn append_action_observed(
        &mut self,
        run: &mut NativeStartingRun,
        action: StageAction,
        observation_digest: Digest,
    ) -> Result<(), SupervisorError> {
        let append = open_stage_lease(run)?
            .journal
            .plan_action_observed(action, observation_digest)
            .map_err(|error| SupervisorError::new(error.code()))?;
        let readback = self.api.append_stage_journal(
            &run.policy,
            append.prior_byte_len(),
            append.record_bytes(),
        )?;
        append_readback(open_stage_lease_mut(run)?, &append, readback)
    }

    fn defer_observation(
        &mut self,
        mut run: NativeStartingRun,
        action: StageAction,
        observation_digest: Digest,
        continuation: NativeStartingContinuation,
    ) -> NativeStartingAdvance {
        run.phase = NativeStartingPhase::ObservationPending(NativePendingStageObservation {
            action,
            observation_digest,
            continuation,
        });
        match self.flush_pending_observation(&mut run) {
            Ok(()) => NativeStartingAdvance::Starting(run),
            Err(error) => NativeStartingAdvance::Retrying(run, error.code()),
        }
    }

    fn flush_pending_observation(
        &mut self,
        run: &mut NativeStartingRun,
    ) -> Result<(), SupervisorError> {
        let pending = match &run.phase {
            NativeStartingPhase::ObservationPending(pending) => pending.clone(),
            _ => return Ok(()),
        };
        self.append_action_observed(run, pending.action, pending.observation_digest)?;
        run.phase = pending.continuation.into_phase();
        Ok(())
    }

    fn finish_arming(
        &mut self,
        mut run: NativeStartingRun,
        foundation: NativePreparedFoundation,
        bridge_root: AtomicRootLaunchReceipt,
        driver_root: AtomicRootLaunchReceipt,
    ) -> NativeStartingAdvance {
        macro_rules! contain_on_error {
            ($expression:expr) => {
                match $expression {
                    Ok(value) => value,
                    Err(error) => {
                        return self.contain_failed_start(run, error.code());
                    }
                }
            };
        }

        let native_prepared = NativePreparedEvidence {
            foundation,
            bridge_root,
        };
        contain_on_error!(validate_native_prepared(
            &run.policy,
            &run.prepared_receipt,
            &run.policy_snapshot,
            &native_prepared,
        ));
        contain_on_error!(validate_atomic_root_launch(
            &run.policy,
            &native_prepared.foundation,
            &driver_root,
            ProcessRole::Driver,
        ));
        let launch = root_launch_observation(ProcessRole::Driver, &driver_root.resumed);
        let root_process = process_from_armed_root(&run.policy, &driver_root.suspended, &launch);
        let armed = ArmedRecoveryReceipt::from_armed_launch(
            &run.policy,
            &run.prepared_receipt,
            &root_process,
            &launch,
        );
        let armed_admission = contain_on_error!(self.api.bind_admission_after_arm(
            &run.policy,
            &native_prepared,
            &armed,
            &run.policy_snapshot,
        ));
        let admission = contain_on_error!(build_admission_binding(
            &run.prepared_receipt,
            &armed,
            &run.policy_snapshot,
            &native_prepared.admission,
            &armed_admission,
        ));
        let armed_digest: Digest = Sha256::digest(armed.encode()).into();
        contain_on_error!(self.append_action_observed(&mut run, StageAction::Arm, armed_digest,));
        let stage_journal = contain_on_error!(run
            .journal
            .take()
            .ok_or_else(|| SupervisorError::new("authority_stage_journal_lease_missing")));
        NativeStartingAdvance::Armed(NativeArmedRun {
            policy: run.policy,
            prepared_receipt: run.prepared_receipt,
            policy_snapshot: run.policy_snapshot,
            native_prepared,
            suspended: driver_root.suspended,
            launch,
            armed,
            admission,
            termination_intent: None,
            stage_journal: Some(stage_journal),
            stage_termination_head_digest: None,
            stage_termination_intent_digest: None,
            normal_terminal_pending: None,
        })
    }

    fn finish_starting_termination(&mut self, mut run: NativeStartingRun) -> NativeStartingAdvance {
        if matches!(run.phase, NativeStartingPhase::ObservationPending(_)) {
            if let Err(error) = self.flush_pending_observation(&mut run) {
                return NativeStartingAdvance::Retrying(run, error.code());
            }
        }
        let mut termination = match run.termination.clone() {
            Some(termination) => termination,
            None => {
                return self.contain_failed_start(run, "authority_stage_termination_missing");
            }
        };

        if termination.evidence.is_none() {
            let evidence = match self.api.contain_starting_termination(
                &run.policy,
                &run.prepared_receipt,
                &match open_stage_lease(&run) {
                    Ok(lease) => lease,
                    Err(error) => return self.contain_failed_start(run, error.code()),
                }
                .journal,
                termination.kind,
            ) {
                Ok(evidence) => evidence,
                Err(error) => return NativeStartingAdvance::Retrying(run, error.code()),
            };
            if let Err(error) =
                validate_starting_termination_evidence(&run.policy, &termination, &evidence)
            {
                return self.contain_failed_start(run, error.code());
            }
            let reason = burn_reason(termination.kind);
            if let Err(error) = validate_authority_owned_staged_termination(
                &run.policy,
                &run.prepared_receipt,
                None,
                &evidence.observation,
                reason,
            ) {
                return self.contain_failed_start(run, error.code());
            }
            termination.evidence = Some(evidence);
            run.termination = Some(termination);
            return NativeStartingAdvance::Starting(run);
        }

        let evidence = termination
            .evidence
            .clone()
            .expect("termination evidence was checked above");
        let journal_kind = match open_stage_lease(&run) {
            Ok(lease) => lease.journal.head().kind,
            Err(error) => return self.contain_failed_start(run, error.code()),
        };
        match journal_kind {
            StageJournalRecordKind::TerminationIntent | StageJournalRecordKind::ActionObserved => {
                let terminal_append = match open_stage_lease(&run).and_then(|lease| {
                    lease
                        .journal
                        .plan_terminal(
                            stage_termination_kind(termination.kind),
                            evidence.terminal_digest,
                        )
                        .map_err(|error| SupervisorError::new(error.code()))
                }) {
                    Ok(append) => append,
                    Err(error) => {
                        return self.contain_failed_start(run, error.code());
                    }
                };
                let terminal_readback = match self.api.append_stage_journal(
                    &run.policy,
                    terminal_append.prior_byte_len(),
                    terminal_append.record_bytes(),
                ) {
                    Ok(readback) => readback,
                    Err(error) => return NativeStartingAdvance::Retrying(run, error.code()),
                };
                let append_result = open_stage_lease_mut(&mut run)
                    .and_then(|lease| append_readback(lease, &terminal_append, terminal_readback));
                if let Err(error) = append_result {
                    return NativeStartingAdvance::Retrying(run, error.code());
                }
                NativeStartingAdvance::Starting(run)
            }
            StageJournalRecordKind::Terminal => {
                let cleanup_append = match open_stage_lease(&run).and_then(|lease| {
                    lease
                        .journal
                        .plan_cleanup(
                            stage_termination_kind(termination.kind),
                            evidence.cleanup_digest,
                        )
                        .map_err(|error| SupervisorError::new(error.code()))
                }) {
                    Ok(append) => append,
                    Err(error) => {
                        return self.contain_failed_start(run, error.code());
                    }
                };
                let cleanup_readback = match self.api.append_stage_journal(
                    &run.policy,
                    cleanup_append.prior_byte_len(),
                    cleanup_append.record_bytes(),
                ) {
                    Ok(readback) => readback,
                    Err(error) => return NativeStartingAdvance::Retrying(run, error.code()),
                };
                let append_result = open_stage_lease_mut(&mut run)
                    .and_then(|lease| append_readback(lease, &cleanup_append, cleanup_readback));
                if let Err(error) = append_result {
                    return NativeStartingAdvance::Retrying(run, error.code());
                }
                NativeStartingAdvance::Starting(run)
            }
            StageJournalRecordKind::Cleanup => {
                let reason = burn_reason(termination.kind);
                let terminal = match validate_authority_owned_staged_termination(
                    &run.policy,
                    &run.prepared_receipt,
                    None,
                    &evidence.observation,
                    reason,
                ) {
                    Ok(terminal) => terminal,
                    Err(error) => {
                        return self.contain_failed_start(run, error.code());
                    }
                };
                NativeStartingAdvance::Terminal(ValidatedNativeTerminalRun::Burned(
                    NativeBurnedRunProof {
                        terminal,
                        admission: None,
                        normal_termination_recovery: Some(NativeNormalTerminationRecoveryBinding {
                            armed_receipt_digest: None,
                            stage_journal_head_digest: termination.stage_head_digest,
                            termination_intent_digest: termination.intent_digest,
                            terminal_digest: evidence.terminal_digest,
                            cleanup_digest: evidence.cleanup_digest,
                        }),
                    },
                ))
            }
            _ => {
                self.contain_failed_start(run, "authority_stage_termination_journal_state_invalid")
            }
        }
    }
}

fn mark_failed_start(run: &mut NativeStartingRun, failure_code: &'static str) {
    let failed_phase = phase_for_starting(&run.phase);
    run.phase = NativeStartingPhase::FailureContainment {
        failure_code,
        failed_phase,
    };
}

fn open_stage_lease(run: &NativeStartingRun) -> Result<&NativeStageJournalLease, SupervisorError> {
    match (&run.pending_declaration, &run.journal) {
        (None, Some(lease)) => Ok(lease),
        _ => Err(SupervisorError::new(
            "authority_stage_journal_lease_state_invalid",
        )),
    }
}

fn open_stage_lease_mut(
    run: &mut NativeStartingRun,
) -> Result<&mut NativeStageJournalLease, SupervisorError> {
    match (&run.pending_declaration, &mut run.journal) {
        (None, Some(lease)) => Ok(lease),
        _ => Err(SupervisorError::new(
            "authority_stage_journal_lease_state_invalid",
        )),
    }
}

pub(super) fn append_readback(
    lease: &mut NativeStageJournalLease,
    append: &StageJournalAppend,
    readback: NativeStageJournalStoreReadback,
) -> Result<(), SupervisorError> {
    let prior = Some((
        lease.file_identity,
        lease.parent_identity,
        lease.write_sequence,
    ));
    let mut expected = lease.journal.bytes().to_vec();
    expected.extend_from_slice(append.record_bytes());
    validate_stage_store_readback(&readback, &expected, prior, false, false)?;
    let reopened = lease
        .journal
        .verify_reopened_append(append, &readback.canonical_bytes)
        .map_err(|error| SupervisorError::new(error.code()))?;
    lease.journal = reopened;
    lease.write_sequence = readback.write_sequence;
    Ok(())
}

fn validate_stage_store_readback(
    readback: &NativeStageJournalStoreReadback,
    expected_bytes: &[u8],
    prior: Option<(FileIdentity, FileIdentity, u64)>,
    expected_created_new: bool,
    expected_parent_flushed_after_create: bool,
) -> Result<(), SupervisorError> {
    let identity_valid =
        valid_stage_store_identity(&readback.file_identity, &readback.parent_identity);
    let sequence_valid = prior.map_or(readback.write_sequence == 1, |(file, parent, sequence)| {
        readback.file_identity == file
            && readback.parent_identity == parent
            && readback.write_sequence == sequence.saturating_add(1)
    });
    if readback.canonical_bytes != expected_bytes
        || !identity_valid
        || readback.created_new != expected_created_new
        || !sequence_valid
        || !readback.append_flushed
        || readback.parent_flushed_after_create != expected_parent_flushed_after_create
        || !readback.reopened_from_held_handle
        || !readback.service_owned_parent
        || !readback.owner_local_system
        || !readback.protected_restricted_dacl
        || readback.file_is_reparse_point
        || readback.parent_is_reparse_point
        || !readback.single_link
        || !readback.service_handle_held
    {
        return Err(SupervisorError::new(
            "authority_stage_journal_store_readback_invalid",
        ));
    }
    Ok(())
}

fn validate_stage_create_uncertainty(
    uncertainty: &NativeStageJournalCreateUncertainty,
    expected_bytes: &[u8],
) -> Result<Digest, SupervisorError> {
    if !valid_stage_store_identity(&uncertainty.file_identity, &uncertainty.parent_identity) {
        return Err(SupervisorError::new(
            "authority_stage_journal_uncertain_identity_invalid",
        ));
    }
    let expected_binding = stage_journal_held_handle_binding_digest(
        expected_bytes,
        &uncertainty.file_identity,
        &uncertainty.parent_identity,
    );
    if uncertainty.held_handle_binding_digest != expected_binding {
        return Err(SupervisorError::new(
            "authority_stage_journal_uncertain_handle_binding_invalid",
        ));
    }
    Ok(expected_binding)
}

fn valid_stage_store_identity(file: &FileIdentity, parent: &FileIdentity) -> bool {
    file.volume_serial != 0
        && file.file_id.iter().any(|byte| *byte != 0)
        && parent.volume_serial != 0
        && parent.file_id.iter().any(|byte| *byte != 0)
        && file != parent
}

pub(super) fn stage_journal_held_handle_binding_digest(
    canonical_bytes: &[u8],
    file: &FileIdentity,
    parent: &FileIdentity,
) -> Digest {
    let canonical_digest: Digest = Sha256::digest(canonical_bytes).into();
    hash_parts(
        STAGE_HELD_HANDLE_BINDING_DOMAIN,
        &[
            &canonical_digest,
            &(canonical_bytes.len() as u64).to_be_bytes(),
            &file.volume_serial.to_be_bytes(),
            &file.file_id,
            &parent.volume_serial.to_be_bytes(),
            &parent.file_id,
        ],
    )
}

pub(super) fn validate_stage_termination_timing(
    policy: &SupervisorPolicy,
    response: &NativeStageTerminationReadback,
) -> Result<(), SupervisorError> {
    validate_stage_termination_values(
        policy,
        response.kind,
        response.requested_at_unix_ms,
        response.recorded_at_unix_ms,
    )
}

pub(super) fn validate_stage_termination_values(
    policy: &SupervisorPolicy,
    kind: NativeTerminationKind,
    requested_at_unix_ms: u64,
    recorded_at_unix_ms: u64,
) -> Result<(), SupervisorError> {
    let deadline_ms = policy
        .deadline
        .checked_mul(1_000)
        .ok_or_else(|| SupervisorError::new("authority_stage_deadline_invalid"))?;
    let issued_ms = policy
        .issued_at
        .checked_mul(1_000)
        .ok_or_else(|| SupervisorError::new("authority_stage_deadline_invalid"))?;
    let kind_valid = match kind {
        NativeTerminationKind::Cancelled => recorded_at_unix_ms < deadline_ms,
        NativeTerminationKind::TimedOut => recorded_at_unix_ms >= deadline_ms,
    };
    if requested_at_unix_ms < issued_ms || recorded_at_unix_ms < requested_at_unix_ms || !kind_valid
    {
        return Err(SupervisorError::new(
            "authority_stage_termination_timing_invalid",
        ));
    }
    Ok(())
}

pub(super) fn validate_created_root(
    policy: &SupervisorPolicy,
    prepared: &NativePreparedFoundation,
    created: &NativeCreatedRootReceipt,
    role: ProcessRole,
) -> Result<(), SupervisorError> {
    if created.start_contract_digest != prepared.start_contract_digest {
        return Err(SupervisorError::new(
            "authority_native_start_contract_receipt_mismatch",
        ));
    }
    let executable_role = match role {
        ProcessRole::Driver => ScenarioStartExecutableRole::Driver,
        ProcessRole::BridgeLauncher => ScenarioStartExecutableRole::BridgeLauncher,
        _ => {
            return Err(SupervisorError::new(
                "authority_native_start_executable_binding_invalid",
            ))
        }
    };
    if !created.executable_binding.verifies_persisted_receipt(
        executable_role,
        created.start_contract_digest,
        file_identity_digest(&created.suspended.image_identity),
    ) {
        return Err(SupervisorError::new(
            "authority_native_start_executable_binding_invalid",
        ));
    }
    validate_suspended_root_for_role(policy, prepared, &created.suspended, role)?;
    validate_job_assignment(policy, prepared, &created.suspended, &created.membership)
}

fn validate_start_contract_foundation(
    start_contract: &VerifiedScenarioStartContract,
    foundation: &NativePreparedFoundation,
) -> Result<(), SupervisorError> {
    if foundation.start_contract_digest != start_contract.binding_digest() {
        return Err(SupervisorError::new(
            "authority_native_start_contract_receipt_mismatch",
        ));
    }
    Ok(())
}

fn validate_executable_launch_view(
    start_contract: &VerifiedScenarioStartContract,
    executable: &VerifiedScenarioExecutableLaunch<'_>,
    expected_role: ScenarioStartExecutableRole,
) -> Result<(), SupervisorError> {
    if executable.role() != expected_role
        || executable.start_contract_digest() != start_contract.binding_digest()
        || executable.resolved_path().as_os_str().is_empty()
    {
        return Err(SupervisorError::new(
            "authority_native_start_executable_binding_invalid",
        ));
    }
    Ok(())
}

fn validate_starting_termination_evidence(
    policy: &SupervisorPolicy,
    termination: &StartingTermination,
    evidence: &NativeStartingTerminationEvidence,
) -> Result<(), SupervisorError> {
    let reason = burn_reason(termination.kind);
    let expected_terminal = starting_terminal_digest(policy, termination, &evidence.observation);
    let expected_cleanup = derive_abort_cleanup_receipt(policy, &evidence.observation, reason);
    let native = &evidence.native_cleanup;
    if evidence.terminal_digest != expected_terminal
        || evidence.cleanup_digest != expected_cleanup
        || !seconds_observation_not_before_millis(
            evidence.observation.terminal.observed_at,
            termination.recorded_at_unix_ms,
        )
        || !native.private_pipe_closed
        || !native.pipe_challenge_zeroed
        || !native.no_pending_pipe_clients
        || !native.pipe_replay_rejected
        || !native.bridge_control_pipe_closed
        || !native.bridge_control_pipe_challenge_zeroed
        || !native.bridge_control_pipe_no_pending_clients
        || !native.bridge_control_pipe_replay_rejected
        || !native.all_candidate_listener_duplicates_closed
        || !native.all_service_listener_handles_closed
        || !native.completion_port_drained
        || !native.no_inheritable_handle_residue
        || !native.no_port_drift
        || !native.bridge_proxy_listener_closed
        || !native.bridge_proxy_connections_closed
        || !native.bridge_target_listener_closed
        || !native.bridge_request_auth_credentials_zeroized
        || !native.containment_readback_complete
    {
        return Err(SupervisorError::new(
            "authority_stage_termination_evidence_invalid",
        ));
    }
    Ok(())
}

pub(super) fn stage_journal_binding(
    policy: &SupervisorPolicy,
    prepared: &PreparedRecoveryReceipt,
    policy_snapshot: &[u8],
    start_contract_digest: Digest,
) -> Result<StageJournalBinding, SupervisorError> {
    let policy_snapshot_digest: Digest = Sha256::digest(policy_snapshot).into();
    let recovery_bundle_digest =
        recovery_bundle_digest(policy, &prepared.encode(), policy_snapshot);
    StageJournalBinding::new(
        policy.authority_generation_digest,
        policy.service_instance_digest,
        policy.ticket_digest,
        policy.run_binding_digest,
        prepared.digest(),
        policy_snapshot_digest,
        recovery_bundle_digest,
        start_contract_digest,
    )
    .map_err(|error| SupervisorError::new(error.code()))
}

pub(super) fn recovered_stage_journal_binding(
    policy: &SupervisorPolicy,
    prepared: &PreparedRecoveryReceipt,
    policy_snapshot: &[u8],
    canonical_bytes: &[u8],
) -> Result<StageJournalBinding, SupervisorError> {
    let persisted = StageJournal::persisted_binding(canonical_bytes)
        .map_err(|error| SupervisorError::new(error.code()))?;
    let expected = stage_journal_binding(
        policy,
        prepared,
        policy_snapshot,
        persisted.start_contract_digest(),
    )?;
    if persisted != expected {
        return Err(SupervisorError::new(
            "authority_native_recovered_start_contract_binding_invalid",
        ));
    }
    Ok(expected)
}

pub(super) fn stage_declaration_digest(
    policy: &SupervisorPolicy,
    binding: &StageJournalBinding,
) -> Digest {
    hash_parts(
        STAGE_DECLARATION_DOMAIN,
        &[
            &policy.authority_identity_digest,
            &binding.digest(),
            &binding.start_contract_digest(),
            &policy.authority_process.pid.to_be_bytes(),
            &policy.authority_process.creation_time.to_be_bytes(),
            &policy.job_object_id.to_be_bytes(),
        ],
    )
}

fn stage_action_intent_digest(journal: &StageJournal, action: StageAction) -> Digest {
    let head = journal.head();
    hash_parts(
        STAGE_ACTION_INTENT_DOMAIN,
        &[
            &journal.binding().digest(),
            &[stage_action_code(action)],
            &head.sequence.to_be_bytes(),
            &head.record_digest,
        ],
    )
}

pub(super) fn prepared_foundation_digest(prepared: &NativePreparedFoundation) -> Digest {
    let mut digest = Sha256::new();
    digest.update(STAGE_PREPARED_OBSERVATION_DOMAIN);
    digest.update(prepared.start_contract_digest);
    digest.update(prepared.ticket_consumed_at.to_be_bytes());
    digest.update(prepared.runner.identity_digest);
    digest.update(prepared.job.object_id.to_be_bytes());
    digest.update(prepared.job.security_binding_digest);
    digest.update(prepared.pipe.binding_digest);
    digest.update(prepared.bridge_control_pipe.binding_digest);
    digest.update(prepared.bridge_proxy.request_auth_key_digest);
    digest.update(prepared.admission.prepared_receipt_digest);
    digest.update(prepared.admission.policy_snapshot_digest);
    digest.update(prepared.admission.recovery_bundle_digest);
    for artifact in &prepared.artifacts {
        digest.update(artifact.binding_digest);
        digest.update(artifact.content_digest);
        digest.update(artifact.private_identity.volume_serial.to_be_bytes());
        digest.update(artifact.private_identity.file_id);
    }
    for listener in &prepared.listeners {
        digest.update([socket_role_code(listener.role)]);
        digest.update(listener.local_port.to_be_bytes());
        digest.update(listener.listener_socket_object_id.to_be_bytes());
        digest.update(listener.share_material_digest);
    }
    digest.finalize().into()
}

pub(super) fn created_root_digest(created: &NativeCreatedRootReceipt) -> Digest {
    let suspended = &created.suspended;
    let membership = &created.membership;
    hash_parts(
        STAGE_ROOT_CREATED_OBSERVATION_DOMAIN,
        &[
            &created.start_contract_digest,
            &created.executable_binding.binding_digest(),
            &[process_role_code(suspended.role)],
            &suspended.process.pid.to_be_bytes(),
            &suspended.process.creation_time.to_be_bytes(),
            &suspended.executable_digest,
            &suspended.image_identity.volume_serial.to_be_bytes(),
            &suspended.image_identity.file_id,
            &suspended.child_transport_contract_digest,
            &[suspended.raw_handle_list.role().wire_value()],
            suspended.raw_handle_list.as_bytes(),
            &suspended.created_suspended_at.to_be_bytes(),
            &membership.job_object_id.to_be_bytes(),
            &membership.membership_verified_at.to_be_bytes(),
        ],
    )
}

pub(super) fn resumed_root_digest(role: ProcessRole, resumed: &ResumedRootReceipt) -> Digest {
    hash_parts(
        STAGE_ROOT_RESUMED_OBSERVATION_DOMAIN,
        &[
            &resumed.start_contract_digest,
            &[process_role_code(role)],
            &resumed.process.pid.to_be_bytes(),
            &resumed.process.creation_time.to_be_bytes(),
            &resumed.created_suspended_at.to_be_bytes(),
            &resumed.job_membership_verified_at.to_be_bytes(),
            &resumed.resumed_at.to_be_bytes(),
            &resumed.job_object_id.to_be_bytes(),
            &resumed.runner_identity_digest,
            &resumed.child_transport_contract_digest,
            &[resumed.raw_handle_list.role().wire_value()],
            resumed.raw_handle_list.as_bytes(),
        ],
    )
}

fn starting_terminal_digest(
    policy: &SupervisorPolicy,
    termination: &StartingTermination,
    observation: &AuthorityOwnedAbortObservation,
) -> Digest {
    stage_terminal_digest(
        policy,
        termination.kind,
        termination.requested_at_unix_ms,
        termination.recorded_at_unix_ms,
        termination.stage_head_digest,
        termination.intent_digest,
        observation,
    )
}

pub(super) fn recovered_starting_terminal_digest(
    policy: &SupervisorPolicy,
    material: stage_journal::VerifiedNormalTerminationMaterial,
    observation: &AuthorityOwnedAbortObservation,
) -> Digest {
    let kind = match material.termination_kind() {
        StageTerminationKind::Cancelled => NativeTerminationKind::Cancelled,
        StageTerminationKind::TimedOut => NativeTerminationKind::TimedOut,
    };
    stage_terminal_digest(
        policy,
        kind,
        material.requested_at_unix_ms(),
        material.recorded_at_unix_ms(),
        material.branch_head_digest(),
        material.intent_record_digest(),
        observation,
    )
}

#[allow(clippy::too_many_arguments)]
fn stage_terminal_digest(
    policy: &SupervisorPolicy,
    kind: NativeTerminationKind,
    requested_at_unix_ms: u64,
    recorded_at_unix_ms: u64,
    stage_head_digest: Digest,
    intent_digest: Digest,
    observation: &AuthorityOwnedAbortObservation,
) -> Digest {
    hash_parts(
        STAGE_TERMINAL_OBSERVATION_DOMAIN,
        &[
            &policy.authority_identity_digest,
            &policy.ticket_digest,
            &policy.run_binding_digest,
            &[termination_kind_code(kind)],
            &requested_at_unix_ms.to_be_bytes(),
            &recorded_at_unix_ms.to_be_bytes(),
            &stage_head_digest,
            &intent_digest,
            &observation.terminal.observed_at.to_be_bytes(),
            &observation.terminal.intent_recorded_at.to_be_bytes(),
        ],
    )
}

fn recovery_bundle_digest(
    policy: &SupervisorPolicy,
    prepared_receipt: &[u8],
    policy_snapshot: &[u8],
) -> Digest {
    let prepared_digest: Digest = Sha256::digest(prepared_receipt).into();
    let snapshot_digest: Digest = Sha256::digest(policy_snapshot).into();
    let mut digest = Sha256::new();
    digest.update(RECOVERY_BUNDLE_DOMAIN);
    digest.update(policy.ticket_digest);
    digest.update(policy.run_binding_digest);
    digest.update((prepared_receipt.len() as u64).to_be_bytes());
    digest.update(prepared_digest);
    digest.update((policy_snapshot.len() as u64).to_be_bytes());
    digest.update(snapshot_digest);
    digest.finalize().into()
}

fn hash_parts(domain: &[u8], parts: &[&[u8]]) -> Digest {
    let mut digest = Sha256::new();
    digest.update(domain);
    for part in parts {
        digest.update((part.len() as u64).to_be_bytes());
        digest.update(part);
    }
    digest.finalize().into()
}

fn stage_action_code(action: StageAction) -> u8 {
    match action {
        StageAction::Prepare => 1,
        StageAction::BridgeCreate => 2,
        StageAction::BridgeResume => 3,
        StageAction::DriverCreate => 4,
        StageAction::DriverResume => 5,
        StageAction::Arm => 6,
    }
}

fn stage_termination_kind(kind: NativeTerminationKind) -> StageTerminationKind {
    match kind {
        NativeTerminationKind::Cancelled => StageTerminationKind::Cancelled,
        NativeTerminationKind::TimedOut => StageTerminationKind::TimedOut,
    }
}

fn termination_kind_code(kind: NativeTerminationKind) -> u8 {
    match kind {
        NativeTerminationKind::Cancelled => 1,
        NativeTerminationKind::TimedOut => 2,
    }
}

fn burn_reason(kind: NativeTerminationKind) -> BurnReason {
    match kind {
        NativeTerminationKind::Cancelled => BurnReason::Cancelled,
        NativeTerminationKind::TimedOut => BurnReason::TimedOut,
    }
}

fn phase_for_starting(phase: &NativeStartingPhase) -> NativeSupervisorPhase {
    match phase {
        NativeStartingPhase::Declaring(_)
        | NativeStartingPhase::PrepareReady
        | NativeStartingPhase::PrepareIntent => NativeSupervisorPhase::Prepare,
        NativeStartingPhase::Prepared(_)
        | NativeStartingPhase::BridgeCreateIntent(_)
        | NativeStartingPhase::DriverCreateIntent(_, _)
        | NativeStartingPhase::DriverCreated(_, _, _) => NativeSupervisorPhase::LaunchSuspended,
        NativeStartingPhase::BridgeCreated(_, _)
        | NativeStartingPhase::BridgeResumeIntent(_, _)
        | NativeStartingPhase::BridgeReady(_, _)
        | NativeStartingPhase::DriverResumeIntent(_, _, _)
        | NativeStartingPhase::DriverReady(_, _, _)
        | NativeStartingPhase::ArmIntent(_, _, _) => NativeSupervisorPhase::Resume,
        NativeStartingPhase::ObservationPending(pending) => phase_for_action(pending.action),
        NativeStartingPhase::FailureContainment { failed_phase, .. } => *failed_phase,
    }
}

fn phase_for_action(action: StageAction) -> NativeSupervisorPhase {
    match action {
        StageAction::Prepare => NativeSupervisorPhase::Prepare,
        StageAction::BridgeCreate | StageAction::DriverCreate => {
            NativeSupervisorPhase::LaunchSuspended
        }
        StageAction::BridgeResume | StageAction::DriverResume | StageAction::Arm => {
            NativeSupervisorPhase::Resume
        }
    }
}
