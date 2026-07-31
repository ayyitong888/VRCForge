use super::super::super::transaction::{
    IdempotentWriteDisposition, JournalContractProjection, JournalTerminal, JournalTransition,
    MaintenanceApplyFailure, StartupRecoveryDisposition,
};
use super::super::super::{
    preview_install, AuthorityInstallContent, AuthorityMaintenanceError, AuthorityMaintenanceStep,
    AuthorityPayloadDigest, RawBootstrapHelperObservation, RawHeldPayloadObservation,
    VerifiedBootstrapHelperIdentity, VerifiedMaintenanceLease,
};
use super::*;
use crate::primitive_evidence_authority_windows::AuthorityLayout;
use std::path::Path;

const COMMITTED_OUTCOME: Digest = [0xa1; 32];
const CONTAINED_OUTCOME: Digest = [0xa2; 32];
const COMMITTED_RECEIPT: Digest = [0xb1; 32];
const CONTAINED_RECEIPT: Digest = [0xb2; 32];
const SOURCE_RESOLVED_RECEIPT: Digest = [0xc1; 32];
const EXIT_READY_RECEIPT: Digest = [0xd0; 32];
const DELETE_PENDING_READBACK: Digest = [0xc2; 32];
const HANDLES_CLOSED_READBACK: Digest = [0xc3; 32];
const SCM_ABSENCE: Digest = [0xc4; 32];
const ZERO_RESIDUE_READBACK: Digest = [0xc5; 32];
const IDENTITY_READBACK: Digest = [0xc7; 32];
const WORKER_PID: u32 = 4_201;
const FINALIZER_PID: u32 = 4_202;
const FINALIZER_CREATION_TIME: u64 = 9_202;
const FINALIZER_IMAGE_SHA256: Digest = [0x96; 32];

fn finalizer_identity() -> NativeFinalizerProcessIdentity {
    NativeFinalizerProcessIdentity::new(
        FINALIZER_PID,
        FINALIZER_CREATION_TIME,
        FINALIZER_IMAGE_SHA256,
    )
    .unwrap()
}

fn restarted_finalizer_identity() -> NativeFinalizerProcessIdentity {
    NativeFinalizerProcessIdentity::new(
        FINALIZER_PID,
        FINALIZER_CREATION_TIME + 1,
        FINALIZER_IMAGE_SHA256,
    )
    .unwrap()
}

fn descriptor(seed: u8) -> AuthorityPayloadDigest {
    AuthorityPayloadDigest::new([seed; 32], 1_000 + u64::from(seed)).unwrap()
}

fn content(seed: u8) -> AuthorityInstallContent {
    AuthorityInstallContent::new(
        descriptor(seed),
        descriptor(seed + 1),
        descriptor(seed + 2),
        descriptor(seed + 3),
        descriptor(seed + 4),
        descriptor(seed + 5),
    )
    .unwrap()
}

fn layout() -> AuthorityLayout {
    AuthorityLayout::for_test_roots(Path::new(r"C:\Program Files"), Path::new(r"C:\ProgramData"))
        .unwrap()
}

fn held_observation(descriptor: AuthorityPayloadDigest, seed: u8) -> RawHeldPayloadObservation {
    RawHeldPayloadObservation {
        descriptor,
        volume_serial: 100 + u64::from(seed),
        file_id: [seed; 16],
        post_read_descriptor: descriptor,
        post_read_volume_serial: 100 + u64::from(seed),
        post_read_file_id: [seed; 16],
        handle_identity: 200 + u64::from(seed),
        regular_file: true,
        reparse_point: false,
        handle_held: true,
        write_sharing_denied: true,
        delete_sharing_denied: true,
        open_policy_receipt_sha256: [seed.saturating_add(40); 32],
        full_readback_receipt_sha256: [seed.saturating_add(80); 32],
    }
}

fn maintenance_lease(
    preview: &AuthorityMaintenancePreview,
    expected: &AuthorityInstallContent,
) -> VerifiedMaintenanceLease {
    let bootstrap = VerifiedBootstrapHelperIdentity::from_running_helper(
        expected.install_helper(),
        RawBootstrapHelperObservation {
            process_id: 77,
            process_creation_time: 9_001,
            image_volume_serial: 88,
            image_file_id: [19; 16],
            image_sha256: *expected.install_helper().sha256(),
            image_byte_length: expected.install_helper().byte_length(),
            image_handle_held: true,
            elevated_token: true,
            high_integrity: true,
            local_system: false,
            session_id: 1,
        },
    )
    .unwrap();
    VerifiedMaintenanceLease::for_test(
        preview,
        expected,
        bootstrap,
        held_observation(expected.service(), 21),
        held_observation(expected.controller(), 22),
        held_observation(expected.install_helper(), 23),
        held_observation(expected.lifecycle_driver(), 24),
        held_observation(expected.bridge_launcher(), 25),
        held_observation(expected.runtime_source_manifest(), 26),
    )
    .unwrap()
}

fn fixture() -> (
    AuthorityMaintenancePreview,
    VerifiedMaintenanceLease,
    NativeSystemTransactionBinding,
) {
    let expected = content(1);
    let preview = preview_install(&layout(), expected.clone()).unwrap();
    let lease = maintenance_lease(&preview, &expected);
    let worker = NativeWorkerProcessIdentity::new(WORKER_PID, 9_111, [0x94; 32]).unwrap();
    let binding = NativeSystemTransactionBinding::new(
        [0x91; 32],
        preview.plan_sha256().unwrap(),
        preview.generation_sha256().unwrap(),
        [0x92; 32],
        [0x93; 32],
        worker.digest(),
        WORKER_PID,
        9_111,
        [0x94; 32],
    )
    .unwrap();
    (preview, lease, binding)
}

struct FakeSystemOperations {
    snapshot: NativeSystemDurableSnapshot,
    maintenance_terminal: Option<JournalTerminal>,
    recovery_seal: Option<Digest>,
    events: Vec<&'static str>,
    fail_once: Option<&'static str>,
    maintenance_fail_once: Option<&'static str>,
    fail_apply_once: bool,
    apply_calls: usize,
    cleanup_failed_apply_calls: usize,
}

impl FakeSystemOperations {
    fn new(binding: &NativeSystemTransactionBinding) -> Self {
        Self {
            snapshot: NativeSystemDurableSnapshot::transaction_started(binding),
            maintenance_terminal: None,
            recovery_seal: None,
            events: Vec::new(),
            fail_once: None,
            maintenance_fail_once: None,
            fail_apply_once: false,
            apply_calls: 0,
            cleanup_failed_apply_calls: 0,
        }
    }

    fn enter(&mut self, event: &'static str) -> Result<(), AuthorityMaintenanceError> {
        self.events.push(event);
        if self.fail_once == Some(event) {
            self.fail_once = None;
            return Err(AuthorityMaintenanceError(
                "authority_native_test_fault_injected",
            ));
        }
        Ok(())
    }

    fn maintenance_event(&mut self, event: &'static str) -> Result<(), ()> {
        self.events.push(event);
        if self.maintenance_fail_once == Some(event) {
            self.maintenance_fail_once = None;
            return Err(());
        }
        Ok(())
    }

    fn durable_journal_state(&self) -> NativeDurableTransactionJournalState {
        match self.maintenance_terminal {
            None => NativeDurableTransactionJournalState::Ready,
            Some(JournalTerminal::Committed) => NativeDurableTransactionJournalState::Committed,
            Some(JournalTerminal::RolledBack | JournalTerminal::Contained) => {
                NativeDurableTransactionJournalState::Contained
            }
        }
    }

    fn terminal_receipt(kind: NativeSystemTransactionTerminalKind) -> Digest {
        match kind {
            NativeSystemTransactionTerminalKind::Committed => COMMITTED_RECEIPT,
            NativeSystemTransactionTerminalKind::Contained => CONTAINED_RECEIPT,
        }
    }
}

impl MaintenanceExecutor for FakeSystemOperations {
    fn recover_startup(
        &mut self,
        _journal: &JournalContractProjection,
    ) -> Result<StartupRecoveryDisposition, ()> {
        self.maintenance_event("apply.recover")?;
        Ok(StartupRecoveryDisposition::Clean)
    }

    fn create_journal(&mut self, _journal: &JournalContractProjection) -> Result<(), ()> {
        self.maintenance_event("apply.createJournal")
    }

    fn record_transition(
        &mut self,
        _step: &AuthorityMaintenanceStep,
        transition: JournalTransition,
    ) -> Result<(), ()> {
        match transition {
            JournalTransition::StepStarted => self.maintenance_event("apply.stepStarted"),
            JournalTransition::StepCompleted => self.maintenance_event("apply.stepCompleted"),
        }
    }

    fn apply(
        &mut self,
        _step: &AuthorityMaintenanceStep,
        _lease: &VerifiedMaintenanceLease,
    ) -> Result<(), MaintenanceApplyFailure> {
        self.events.push("apply.mutate");
        self.apply_calls += 1;
        if self.fail_apply_once {
            self.fail_apply_once = false;
            Err(MaintenanceApplyFailure::BeforeIrreversibleCommit)
        } else {
            Ok(())
        }
    }

    fn cleanup_failed_apply(&mut self, _step: &AuthorityMaintenanceStep) -> Result<(), ()> {
        self.events.push("apply.cleanupFailed");
        self.cleanup_failed_apply_calls += 1;
        Ok(())
    }

    fn rollback_completed(&mut self, _step: &AuthorityMaintenanceStep) -> Result<(), ()> {
        self.events.push("apply.rollbackCompleted");
        Ok(())
    }

    fn contain_post_commit(&mut self, _failed_step: &AuthorityMaintenanceStep) -> Result<(), ()> {
        self.events.push("apply.containPostCommit");
        Ok(())
    }

    fn seal_recovery_once(
        &mut self,
        _path: &str,
        content_sha256: Digest,
    ) -> Result<IdempotentWriteDisposition, ()> {
        self.events.push("apply.sealRecovery");
        match self.recovery_seal {
            None => {
                self.recovery_seal = Some(content_sha256);
                Ok(IdempotentWriteDisposition::Created)
            }
            Some(existing) if existing == content_sha256 => {
                Ok(IdempotentWriteDisposition::AlreadyIdentical)
            }
            Some(_) => Err(()),
        }
    }

    fn write_journal_terminal(
        &mut self,
        terminal: JournalTerminal,
    ) -> Result<IdempotentWriteDisposition, ()> {
        self.maintenance_event("apply.writeTerminal")?;
        match self.maintenance_terminal {
            None => {
                self.maintenance_terminal = Some(terminal);
                Ok(IdempotentWriteDisposition::Created)
            }
            Some(existing) if existing == terminal => {
                Ok(IdempotentWriteDisposition::AlreadyIdentical)
            }
            Some(_) => Err(()),
        }
    }
}

impl NativeSystemTransactionOperations for FakeSystemOperations {
    fn reopen_durable_snapshot(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeSystemDurableSnapshot, AuthorityMaintenanceError> {
        self.enter("system.reopenSnapshot")?;
        Ok(self.snapshot.clone())
    }

    fn reopen_durable_transaction_journal(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeDurableTransactionJournalState, AuthorityMaintenanceError> {
        self.enter("system.reopenJournal")?;
        Ok(self.durable_journal_state())
    }

    fn committed_outcome_readback(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<Digest, AuthorityMaintenanceError> {
        self.enter("system.committedReadback")?;
        Ok(COMMITTED_OUTCOME)
    }

    fn contained_outcome_readback(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<Digest, AuthorityMaintenanceError> {
        self.enter("system.containedReadback")?;
        Ok(CONTAINED_OUTCOME)
    }

    fn validate_exit_ready_receipt(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        source_stage_resolved_receipt_sha256: Digest,
        exit_ready_receipt_sha256: Digest,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.enter("system.validateExitReady")?;
        if self.snapshot.terminal != Some(terminal)
            || source_stage_resolved_receipt_sha256 != SOURCE_RESOLVED_RECEIPT
            || self.snapshot.source_stage_resolved_receipt_sha256 != Some(SOURCE_RESOLVED_RECEIPT)
            || exit_ready_receipt_sha256 != EXIT_READY_RECEIPT
            || self.snapshot.exit_ready_receipt_sha256 != Some(EXIT_READY_RECEIPT)
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_exit_ready_mismatch",
            ));
        }
        Ok(())
    }

    fn persist_transaction_terminal(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
        kind: NativeSystemTransactionTerminalKind,
        outcome_readback_sha256: Digest,
    ) -> Result<Digest, AuthorityMaintenanceError> {
        self.enter("system.persistTerminal")?;
        let expected = match kind {
            NativeSystemTransactionTerminalKind::Committed => COMMITTED_OUTCOME,
            NativeSystemTransactionTerminalKind::Contained => CONTAINED_OUTCOME,
        };
        if outcome_readback_sha256 != expected {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_outcome_mismatch",
            ));
        }
        let receipt = Self::terminal_receipt(kind);
        self.snapshot.terminal = Some(NativeSystemTransactionTerminal::new(kind, receipt)?);
        Ok(receipt)
    }

    fn resolve_source_staging(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
    ) -> Result<Digest, AuthorityMaintenanceError> {
        self.enter("system.resolveSourceStage")?;
        if self.snapshot.terminal != Some(terminal) {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_source_resolution_before_terminal",
            ));
        }
        self.snapshot.source_stage_resolved_receipt_sha256 = Some(SOURCE_RESOLVED_RECEIPT);
        Ok(SOURCE_RESOLVED_RECEIPT)
    }

    fn persist_exit_ready(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        source_stage_resolved_receipt_sha256: Digest,
    ) -> Result<Digest, AuthorityMaintenanceError> {
        self.enter("system.persistExitReady")?;
        if self.snapshot.terminal != Some(terminal)
            || source_stage_resolved_receipt_sha256 != SOURCE_RESOLVED_RECEIPT
            || self.snapshot.source_stage_resolved_receipt_sha256 != Some(SOURCE_RESOLVED_RECEIPT)
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_exit_ready_before_source_resolution",
            ));
        }
        self.snapshot.exit_ready_receipt_sha256 = Some(EXIT_READY_RECEIPT);
        Ok(EXIT_READY_RECEIPT)
    }
}

fn execute_system(
    preview: &AuthorityMaintenancePreview,
    lease: &mut VerifiedMaintenanceLease,
    binding: &NativeSystemTransactionBinding,
    operations: &mut FakeSystemOperations,
) -> Result<NativeSystemExitReadyOutcome, AuthorityMaintenanceError> {
    execute_bound_native_system_transaction(preview, lease, binding, operations)
}

fn first_event(events: &[&'static str], expected: &'static str) -> usize {
    events.iter().position(|value| *value == expected).unwrap()
}

#[test]
fn production_mutation_gate_remains_disabled() {
    assert!(!super::super::super::NATIVE_AUTHORITY_MUTATION_ENABLED);
}

#[test]
fn system_actor_stops_at_durable_exit_ready_without_lifecycle_operations() {
    let (preview, mut lease, binding) = fixture();
    let mut operations = FakeSystemOperations::new(&binding);
    let outcome = execute_system(&preview, &mut lease, &binding, &mut operations).unwrap();

    assert_eq!(
        outcome.terminal,
        NativeSystemTransactionTerminalKind::Committed
    );
    assert_eq!(
        outcome.exit_ready_receipt_sha256,
        operations.snapshot.exit_ready_receipt_sha256.unwrap()
    );
    for (before, after) in [
        ("apply.writeTerminal", "system.committedReadback"),
        ("system.committedReadback", "system.persistTerminal"),
        ("system.persistTerminal", "system.resolveSourceStage"),
        ("system.resolveSourceStage", "system.persistExitReady"),
    ] {
        assert!(
            first_event(&operations.events, before) < first_event(&operations.events, after),
            "{before} must precede {after}: {:?}",
            operations.events
        );
    }
    assert!(operations.events.iter().all(|event| {
        !matches!(
            *event,
            "finalizer.waitNatural"
                | "finalizer.requestStop"
                | "finalizer.waitProcess"
                | "finalizer.markDelete"
        )
    }));
}

#[test]
fn contained_apply_also_resolves_staging_before_exit_ready() {
    let (preview, mut lease, binding) = fixture();
    let mut operations = FakeSystemOperations::new(&binding);
    operations.fail_apply_once = true;
    let outcome = execute_system(&preview, &mut lease, &binding, &mut operations).unwrap();
    assert_eq!(
        outcome.terminal,
        NativeSystemTransactionTerminalKind::Contained
    );
    assert!(outcome.apply_recovery_reopened);
    assert_eq!(operations.cleanup_failed_apply_calls, 1);
    assert!(
        first_event(&operations.events, "system.persistTerminal")
            < first_event(&operations.events, "system.resolveSourceStage")
    );
}

#[test]
fn system_crash_recovery_never_reapplies_after_durable_terminal() {
    for fault in [
        "system.committedReadback",
        "system.persistTerminal",
        "system.resolveSourceStage",
        "system.persistExitReady",
    ] {
        let (preview, mut lease, binding) = fixture();
        let mut operations = FakeSystemOperations::new(&binding);
        operations.fail_once = Some(fault);
        assert!(execute_system(&preview, &mut lease, &binding, &mut operations).is_err());
        let apply_calls = operations.apply_calls;
        let outcome = execute_system(&preview, &mut lease, &binding, &mut operations)
            .unwrap_or_else(|error| panic!("fault {fault} did not recover: {}", error.code()));
        assert_ne!(outcome.exit_ready_receipt_sha256, [0; 32]);
        if fault != "system.committedReadback" && fault != "system.persistTerminal" {
            assert_eq!(operations.apply_calls, apply_calls, "fault {fault}");
        }
    }
}

#[test]
fn hostile_exit_ready_without_terminal_or_source_resolution_fails_closed() {
    let (preview, mut lease, binding) = fixture();
    let terminal = NativeSystemTransactionTerminal::new(
        NativeSystemTransactionTerminalKind::Committed,
        COMMITTED_RECEIPT,
    )
    .unwrap();
    let mut cases = Vec::new();
    let mut missing_terminal = NativeSystemDurableSnapshot::transaction_started(&binding);
    missing_terminal.source_stage_resolved_receipt_sha256 = Some(SOURCE_RESOLVED_RECEIPT);
    cases.push(missing_terminal);
    let mut missing_source = NativeSystemDurableSnapshot::transaction_started(&binding);
    missing_source.terminal = Some(terminal);
    missing_source.exit_ready_receipt_sha256 = Some([0xd1; 32]);
    cases.push(missing_source);
    let mut forged_exit = NativeSystemDurableSnapshot::transaction_started(&binding);
    forged_exit.terminal = Some(terminal);
    forged_exit.source_stage_resolved_receipt_sha256 = Some(SOURCE_RESOLVED_RECEIPT);
    forged_exit.exit_ready_receipt_sha256 = Some([0xd2; 32]);
    cases.push(forged_exit);

    for snapshot in cases {
        let mut operations = FakeSystemOperations::new(&binding);
        operations.snapshot = snapshot;
        operations.maintenance_terminal = Some(JournalTerminal::Committed);
        assert!(execute_system(&preview, &mut lease, &binding, &mut operations).is_err());
        assert!(!operations.events.contains(&"apply.mutate"));
    }
}

struct FakeFinalizerOperations {
    snapshot: NativeSystemDurableSnapshot,
    binding: NativeSystemTransactionBinding,
    events: Vec<&'static str>,
    live_state: NativeWorkerFinalizationLiveState,
    observed_service_identity: Digest,
    current_finalizer: NativeFinalizerProcessIdentity,
    persisted_intent_finalizer: Option<NativeFinalizerProcessIdentity>,
    persisted_delete_transition: Option<NativeServiceDeleteTransitionKind>,
    persisted_delete_readback: Option<Digest>,
    persisted_handles_readback: Option<Digest>,
    persisted_scm_absence_readback: Option<Digest>,
    persisted_zero_residue_readback: Option<Digest>,
    prior_finalizer_absent: bool,
    natural_exit: NativeWorkerNaturalExit,
    observed_worker: NativeWorkerProcessIdentity,
    service_stopped: bool,
    process_exited: bool,
    delete_pending: bool,
    handles_closed: bool,
    extra_service_handles: usize,
    service_absent: bool,
    residue_flags: [bool; 8],
    fail_once: Option<&'static str>,
}

impl FakeFinalizerOperations {
    fn exit_ready(binding: &NativeSystemTransactionBinding) -> Self {
        let terminal = NativeSystemTransactionTerminal::new(
            NativeSystemTransactionTerminalKind::Committed,
            COMMITTED_RECEIPT,
        )
        .unwrap();
        let mut snapshot = NativeSystemDurableSnapshot::transaction_started(binding);
        snapshot.terminal = Some(terminal);
        snapshot.source_stage_resolved_receipt_sha256 = Some(SOURCE_RESOLVED_RECEIPT);
        snapshot.exit_ready_receipt_sha256 = Some(EXIT_READY_RECEIPT);
        Self {
            snapshot,
            binding: binding.clone(),
            events: Vec::new(),
            live_state: NativeWorkerFinalizationLiveState::ExactServiceAndWorker,
            observed_service_identity: binding.exact_service_identity_sha256(),
            current_finalizer: finalizer_identity(),
            persisted_intent_finalizer: None,
            persisted_delete_transition: None,
            persisted_delete_readback: None,
            persisted_handles_readback: None,
            persisted_scm_absence_readback: None,
            persisted_zero_residue_readback: None,
            prior_finalizer_absent: false,
            natural_exit: NativeWorkerNaturalExit::Exited,
            observed_worker: binding.worker(),
            service_stopped: false,
            process_exited: false,
            delete_pending: false,
            handles_closed: false,
            extra_service_handles: 0,
            service_absent: false,
            residue_flags: [true; 8],
            fail_once: None,
        }
    }

    fn restart_after_crash(&self) -> Self {
        let service_absent = self.service_absent
            || self.live_state == NativeWorkerFinalizationLiveState::ServiceAbsentAndWorkerAbsent;
        let delete_pending = self.delete_pending
            || self
                .snapshot
                .service_delete_pending_receipt_sha256
                .is_some()
            || self.live_state == NativeWorkerFinalizationLiveState::DeletePendingAndWorkerAbsent;
        let live_state = if service_absent {
            NativeWorkerFinalizationLiveState::ServiceAbsentAndWorkerAbsent
        } else if delete_pending {
            NativeWorkerFinalizationLiveState::DeletePendingAndWorkerAbsent
        } else if self.service_stopped && self.process_exited {
            NativeWorkerFinalizationLiveState::ExactServiceStoppedAndWorkerAbsent
        } else {
            NativeWorkerFinalizationLiveState::ExactServiceAndWorker
        };
        let worker_absent = matches!(
            live_state,
            NativeWorkerFinalizationLiveState::ExactServiceStoppedAndWorkerAbsent
                | NativeWorkerFinalizationLiveState::DeletePendingAndWorkerAbsent
                | NativeWorkerFinalizationLiveState::ServiceAbsentAndWorkerAbsent
        );
        Self {
            snapshot: self.snapshot.clone(),
            binding: self.binding.clone(),
            events: Vec::new(),
            live_state,
            observed_service_identity: self.observed_service_identity,
            current_finalizer: restarted_finalizer_identity(),
            persisted_intent_finalizer: self.persisted_intent_finalizer,
            persisted_delete_transition: self.persisted_delete_transition,
            persisted_delete_readback: self.persisted_delete_readback,
            persisted_handles_readback: self.persisted_handles_readback,
            persisted_scm_absence_readback: self.persisted_scm_absence_readback,
            persisted_zero_residue_readback: self.persisted_zero_residue_readback,
            prior_finalizer_absent: true,
            natural_exit: NativeWorkerNaturalExit::Exited,
            observed_worker: self.observed_worker,
            service_stopped: worker_absent,
            process_exited: worker_absent,
            delete_pending,
            handles_closed: self
                .snapshot
                .finalizer_handles_closed_receipt_sha256
                .is_some(),
            extra_service_handles: self.extra_service_handles,
            service_absent,
            residue_flags: self.residue_flags,
            fail_once: None,
        }
    }

    fn enter(&mut self, event: &'static str) -> Result<(), AuthorityMaintenanceError> {
        self.events.push(event);
        if self.fail_once == Some(event) {
            self.fail_once = None;
            return Err(AuthorityMaintenanceError(
                "authority_native_test_fault_injected",
            ));
        }
        Ok(())
    }
}

impl NativeElevatedFinalizerOperations for FakeFinalizerOperations {
    fn reopen_durable_snapshot(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeSystemDurableSnapshot, AuthorityMaintenanceError> {
        self.enter("finalizer.reopenSnapshot")?;
        Ok(self.snapshot.clone())
    }

    fn validate_exit_ready_receipt(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        source_stage_resolved_receipt_sha256: Digest,
        exit_ready_receipt_sha256: Digest,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.enter("finalizer.validateExitReady")?;
        if self.snapshot.terminal != Some(terminal)
            || source_stage_resolved_receipt_sha256 != SOURCE_RESOLVED_RECEIPT
            || exit_ready_receipt_sha256 != EXIT_READY_RECEIPT
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_worker_exit_ready_mismatch",
            ));
        }
        Ok(())
    }

    fn observe_current_finalizer_identity(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeFinalizerProcessIdentity, AuthorityMaintenanceError> {
        self.enter("finalizer.observeCurrentFinalizer")?;
        Ok(self.current_finalizer)
    }

    fn validate_service_delete_intent_receipt(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        exit_ready_receipt_sha256: Digest,
        service_delete_intent_receipt_sha256: Digest,
    ) -> Result<NativeFinalizerProcessIdentity, AuthorityMaintenanceError> {
        self.enter("finalizer.validateDeleteIntent")?;
        let finalizer = self
            .persisted_intent_finalizer
            .ok_or(AuthorityMaintenanceError(
                "authority_native_test_delete_intent_invalid",
            ))?;
        if derive_service_delete_intent(binding, terminal, exit_ready_receipt_sha256, finalizer)
            != service_delete_intent_receipt_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_delete_intent_invalid",
            ));
        }
        Ok(finalizer)
    }

    fn validate_service_delete_pending_receipt(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        exit_ready_receipt_sha256: Digest,
        service_delete_intent_receipt_sha256: Digest,
        service_delete_pending_receipt_sha256: Digest,
    ) -> Result<NativeServiceDeleteTransitionKind, AuthorityMaintenanceError> {
        self.enter("finalizer.validateDeletePending")?;
        let transition = self
            .persisted_delete_transition
            .ok_or(AuthorityMaintenanceError(
                "authority_native_test_delete_pending_invalid",
            ))?;
        let readback = self
            .persisted_delete_readback
            .ok_or(AuthorityMaintenanceError(
                "authority_native_test_delete_pending_invalid",
            ))?;
        if derive_service_delete_pending(
            binding,
            terminal,
            exit_ready_receipt_sha256,
            service_delete_intent_receipt_sha256,
            transition,
            readback,
        ) != service_delete_pending_receipt_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_delete_pending_invalid",
            ));
        }
        Ok(transition)
    }

    fn validate_finalizer_handles_closed_receipt(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        service_delete_pending_receipt_sha256: Digest,
        finalizer_handles_closed_receipt_sha256: Digest,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.enter("finalizer.validateHandlesClosed")?;
        let readback = self
            .persisted_handles_readback
            .ok_or(AuthorityMaintenanceError(
                "authority_native_test_handles_closed_invalid",
            ))?;
        if derive_finalizer_handles_closed(binding, service_delete_pending_receipt_sha256, readback)
            != finalizer_handles_closed_receipt_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_handles_closed_invalid",
            ));
        }
        Ok(())
    }

    fn validate_service_absent_receipt(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        finalizer_handles_closed_receipt_sha256: Digest,
        service_absent_receipt_sha256: Digest,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.enter("finalizer.validateServiceAbsent")?;
        let scm_absence = self
            .persisted_scm_absence_readback
            .ok_or(AuthorityMaintenanceError(
                "authority_native_test_service_absent_invalid",
            ))?;
        let zero_residue =
            self.persisted_zero_residue_readback
                .ok_or(AuthorityMaintenanceError(
                    "authority_native_test_service_absent_invalid",
                ))?;
        if derive_service_absent(
            binding,
            terminal,
            finalizer_handles_closed_receipt_sha256,
            scm_absence,
            zero_residue,
        ) != service_absent_receipt_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_service_absent_invalid",
            ));
        }
        Ok(())
    }

    fn observe_worker_finalization_live_state(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeWorkerFinalizationLiveReadback, AuthorityMaintenanceError> {
        self.enter("finalizer.observeLiveState")?;
        NativeWorkerFinalizationLiveReadback::from_observed(
            self.live_state,
            self.observed_worker,
            self.observed_service_identity,
            IDENTITY_READBACK,
        )
    }

    fn observe_worker_identity(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeWorkerIdentityReadback, AuthorityMaintenanceError> {
        self.enter("finalizer.observeIdentity")?;
        if self.live_state == NativeWorkerFinalizationLiveState::ExactServiceStoppedAndWorkerAbsent
        {
            NativeWorkerIdentityReadback::exact_stopped(self.observed_worker, IDENTITY_READBACK)
        } else {
            NativeWorkerIdentityReadback::exact_running(self.observed_worker, IDENTITY_READBACK)
        }
    }

    fn wait_for_natural_worker_exit(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeWorkerNaturalExit, AuthorityMaintenanceError> {
        self.enter("finalizer.waitNatural")?;
        if self.natural_exit == NativeWorkerNaturalExit::Exited {
            self.process_exited = true;
            self.service_stopped = true;
        }
        Ok(self.natural_exit)
    }

    fn request_worker_service_stop(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.enter("finalizer.requestStop")?;
        self.service_stopped = true;
        Ok(())
    }

    fn wait_worker_service_stopped(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.enter("finalizer.waitStopped")?;
        if !self.service_stopped {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_service_not_stopped",
            ));
        }
        Ok(())
    }

    fn wait_exact_worker_process_exit(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.enter("finalizer.waitProcess")?;
        if !self.service_stopped {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_wait_before_stop",
            ));
        }
        self.process_exited = true;
        Ok(())
    }

    fn persist_service_delete_intent(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        exit_ready_receipt_sha256: Digest,
        finalizer: NativeFinalizerProcessIdentity,
        expected_receipt_sha256: Digest,
    ) -> Result<Digest, AuthorityMaintenanceError> {
        self.enter("finalizer.persistDeleteIntent")?;
        let expected =
            derive_service_delete_intent(binding, terminal, exit_ready_receipt_sha256, finalizer);
        if expected != expected_receipt_sha256 {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_delete_intent_invalid",
            ));
        }
        self.persisted_intent_finalizer = Some(finalizer);
        self.snapshot.service_delete_intent_receipt_sha256 = Some(expected);
        Ok(expected)
    }

    fn mark_worker_service_delete_pending(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<Digest, AuthorityMaintenanceError> {
        self.enter("finalizer.markDeletePending")?;
        if !self.process_exited {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_delete_before_process_exit",
            ));
        }
        self.delete_pending = true;
        self.live_state = NativeWorkerFinalizationLiveState::DeletePendingAndWorkerAbsent;
        Ok(DELETE_PENDING_READBACK)
    }

    fn persist_service_delete_pending(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        exit_ready_receipt_sha256: Digest,
        service_delete_intent_receipt_sha256: Digest,
        transition_kind: NativeServiceDeleteTransitionKind,
        delete_pending_readback_sha256: Digest,
        expected_receipt_sha256: Digest,
    ) -> Result<Digest, AuthorityMaintenanceError> {
        self.enter("finalizer.persistDeletePending")?;
        let expected = derive_service_delete_pending(
            binding,
            terminal,
            exit_ready_receipt_sha256,
            service_delete_intent_receipt_sha256,
            transition_kind,
            delete_pending_readback_sha256,
        );
        if !self.delete_pending
            || self.snapshot.service_delete_intent_receipt_sha256
                != Some(service_delete_intent_receipt_sha256)
            || expected != expected_receipt_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_delete_pending_invalid",
            ));
        }
        self.persisted_delete_transition = Some(transition_kind);
        self.persisted_delete_readback = Some(delete_pending_readback_sha256);
        self.snapshot.service_delete_pending_receipt_sha256 = Some(expected);
        Ok(expected)
    }

    fn observe_delete_target_state_transition(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
        service_delete_intent_receipt_sha256: Digest,
        live_state: NativeWorkerFinalizationLiveState,
    ) -> Result<Digest, AuthorityMaintenanceError> {
        self.enter("finalizer.observeDeleteTarget")?;
        if self.snapshot.service_delete_intent_receipt_sha256
            != Some(service_delete_intent_receipt_sha256)
            || !matches!(
                live_state,
                NativeWorkerFinalizationLiveState::DeletePendingAndWorkerAbsent
                    | NativeWorkerFinalizationLiveState::ServiceAbsentAndWorkerAbsent
            )
            || live_state != self.live_state
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_delete_target_invalid",
            ));
        }
        self.delete_pending = true;
        Ok(DELETE_PENDING_READBACK)
    }

    fn close_finalizer_handles(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<Digest, AuthorityMaintenanceError> {
        self.enter("finalizer.closeHandles")?;
        if !self.delete_pending {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_handles_closed_before_delete_pending",
            ));
        }
        self.handles_closed = true;
        Ok(HANDLES_CLOSED_READBACK)
    }

    fn recover_finalizer_handles_closed_after_restart(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
        service_delete_pending_receipt_sha256: Digest,
        prior_finalizer: NativeFinalizerProcessIdentity,
        current_finalizer: NativeFinalizerProcessIdentity,
    ) -> Result<Digest, AuthorityMaintenanceError> {
        self.enter("finalizer.recoverHandlesClosed")?;
        if !self.prior_finalizer_absent
            || self.persisted_intent_finalizer != Some(prior_finalizer)
            || current_finalizer != self.current_finalizer
            || current_finalizer == prior_finalizer
            || self.snapshot.service_delete_pending_receipt_sha256
                != Some(service_delete_pending_receipt_sha256)
            || !matches!(
                self.live_state,
                NativeWorkerFinalizationLiveState::DeletePendingAndWorkerAbsent
                    | NativeWorkerFinalizationLiveState::ServiceAbsentAndWorkerAbsent
            )
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_prior_finalizer_absence_unproven",
            ));
        }
        self.handles_closed = true;
        Ok(HANDLES_CLOSED_READBACK)
    }

    fn persist_finalizer_handles_closed(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        delete_pending_receipt_sha256: Digest,
        handles_closed_readback_sha256: Digest,
        expected_receipt_sha256: Digest,
    ) -> Result<Digest, AuthorityMaintenanceError> {
        self.enter("finalizer.persistHandlesClosed")?;
        let expected = derive_finalizer_handles_closed(
            binding,
            delete_pending_receipt_sha256,
            handles_closed_readback_sha256,
        );
        if !self.handles_closed || expected != expected_receipt_sha256 {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_handles_closed_invalid",
            ));
        }
        self.persisted_handles_readback = Some(handles_closed_readback_sha256);
        self.snapshot.finalizer_handles_closed_receipt_sha256 = Some(expected);
        Ok(expected)
    }

    fn scm_service_absence_readback(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<Digest, AuthorityMaintenanceError> {
        self.enter("finalizer.scmAbsence")?;
        if !self.handles_closed || self.extra_service_handles != 0 {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_service_still_present",
            ));
        }
        self.service_absent = true;
        self.live_state = NativeWorkerFinalizationLiveState::ServiceAbsentAndWorkerAbsent;
        Ok(SCM_ABSENCE)
    }

    fn remove_finalizer_staging_and_verify_zero_residue(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeZeroResidueReadback, AuthorityMaintenanceError> {
        self.enter("finalizer.verifyZeroResidue")?;
        NativeZeroResidueReadback::new(
            self.residue_flags[0] && self.service_absent,
            self.residue_flags[1],
            self.residue_flags[2],
            self.residue_flags[3],
            self.residue_flags[4] && self.process_exited,
            self.residue_flags[5],
            self.residue_flags[6],
            self.residue_flags[7],
            ZERO_RESIDUE_READBACK,
        )
    }

    fn verify_completed_finalization_readback(
        &mut self,
        _binding: &NativeSystemTransactionBinding,
    ) -> Result<NativeZeroResidueReadback, AuthorityMaintenanceError> {
        self.enter("finalizer.verifyCompleted")?;
        NativeZeroResidueReadback::new(
            self.residue_flags[0] && self.service_absent,
            self.residue_flags[1],
            self.residue_flags[2],
            self.residue_flags[3],
            self.residue_flags[4] && self.process_exited,
            self.residue_flags[5],
            self.residue_flags[6],
            self.residue_flags[7],
            ZERO_RESIDUE_READBACK,
        )
    }

    fn persist_service_absent(
        &mut self,
        binding: &NativeSystemTransactionBinding,
        terminal: NativeSystemTransactionTerminal,
        finalizer_handles_closed_receipt_sha256: Digest,
        scm_absence_readback_sha256: Digest,
        residue: &NativeZeroResidueReadback,
        expected_receipt_sha256: Digest,
    ) -> Result<Digest, AuthorityMaintenanceError> {
        self.enter("finalizer.persistServiceAbsent")?;
        let expected = derive_service_absent(
            binding,
            terminal,
            finalizer_handles_closed_receipt_sha256,
            scm_absence_readback_sha256,
            residue.readback_sha256,
        );
        if expected != expected_receipt_sha256
            || scm_absence_readback_sha256 != SCM_ABSENCE
            || residue.readback_sha256 != ZERO_RESIDUE_READBACK
        {
            return Err(AuthorityMaintenanceError(
                "authority_native_test_service_absent_invalid",
            ));
        }
        self.persisted_scm_absence_readback = Some(scm_absence_readback_sha256);
        self.persisted_zero_residue_readback = Some(residue.readback_sha256);
        self.snapshot.service_absent_receipt_sha256 = Some(expected);
        self.live_state = NativeWorkerFinalizationLiveState::ServiceAbsentAndWorkerAbsent;
        Ok(expected)
    }
}

#[test]
fn elevated_finalizer_prefers_natural_exit_and_orders_every_durable_boundary() {
    let (_, _, binding) = fixture();
    let mut operations = FakeFinalizerOperations::exit_ready(&binding);
    let outcome = finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations).unwrap();
    assert!(!outcome.stop_was_required);
    assert_eq!(
        Some(outcome.service_absent_receipt_sha256),
        operations.snapshot.service_absent_receipt_sha256
    );
    assert!(!operations.events.contains(&"finalizer.requestStop"));
    for (before, after) in [
        ("finalizer.persistDeleteIntent", "finalizer.observeIdentity"),
        ("finalizer.observeIdentity", "finalizer.waitNatural"),
        ("finalizer.waitNatural", "finalizer.waitStopped"),
        ("finalizer.waitStopped", "finalizer.waitProcess"),
        ("finalizer.waitProcess", "finalizer.markDeletePending"),
        (
            "finalizer.markDeletePending",
            "finalizer.persistDeletePending",
        ),
        ("finalizer.persistDeletePending", "finalizer.closeHandles"),
        ("finalizer.closeHandles", "finalizer.persistHandlesClosed"),
        ("finalizer.persistHandlesClosed", "finalizer.scmAbsence"),
        ("finalizer.scmAbsence", "finalizer.verifyZeroResidue"),
        (
            "finalizer.verifyZeroResidue",
            "finalizer.persistServiceAbsent",
        ),
    ] {
        assert!(
            first_event(&operations.events, before) < first_event(&operations.events, after),
            "{before} must precede {after}: {:?}",
            operations.events
        );
    }
}

#[test]
fn elevated_finalizer_requests_stop_only_after_natural_exit_grace_expires() {
    let (_, _, binding) = fixture();
    let mut operations = FakeFinalizerOperations::exit_ready(&binding);
    operations.natural_exit = NativeWorkerNaturalExit::StillRunning;
    let outcome = finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations).unwrap();
    assert!(outcome.stop_was_required);
    assert!(
        first_event(&operations.events, "finalizer.waitNatural")
            < first_event(&operations.events, "finalizer.requestStop")
    );
}

#[test]
fn self_pid_is_rejected_before_any_wait_stop_or_delete() {
    let (_, _, binding) = fixture();
    let mut operations = FakeFinalizerOperations::exit_ready(&binding);
    assert_eq!(
        finalize_bound_native_worker(WORKER_PID, &binding, &mut operations)
            .unwrap_err()
            .code(),
        "authority_native_worker_self_finalization_rejected"
    );
    assert!(operations.events.is_empty());
}

#[test]
fn pid_reuse_or_image_drift_is_rejected_before_wait_or_delete() {
    let (_, _, binding) = fixture();
    for observed in [
        NativeWorkerProcessIdentity::new(WORKER_PID, 9_112, [0x94; 32]).unwrap(),
        NativeWorkerProcessIdentity::new(WORKER_PID, 9_111, [0x95; 32]).unwrap(),
    ] {
        let mut operations = FakeFinalizerOperations::exit_ready(&binding);
        operations.observed_worker = observed;
        assert_eq!(
            finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations)
                .unwrap_err()
                .code(),
            "authority_native_worker_process_identity_mismatch"
        );
        assert!(!operations.events.contains(&"finalizer.waitNatural"));
        assert!(!operations.events.contains(&"finalizer.markDeletePending"));
    }
}

#[test]
fn exit_ready_and_terminal_are_mandatory_before_finalization() {
    let (_, _, binding) = fixture();
    let mut missing_exit = FakeFinalizerOperations::exit_ready(&binding);
    missing_exit.snapshot.exit_ready_receipt_sha256 = None;
    assert_eq!(
        finalize_bound_native_worker(FINALIZER_PID, &binding, &mut missing_exit)
            .unwrap_err()
            .code(),
        "authority_native_worker_exit_ready_missing"
    );
    assert_eq!(
        missing_exit.events,
        vec![
            "finalizer.observeCurrentFinalizer",
            "finalizer.reopenSnapshot"
        ]
    );

    let mut missing_terminal = FakeFinalizerOperations::exit_ready(&binding);
    missing_terminal.snapshot.terminal = None;
    assert_eq!(
        finalize_bound_native_worker(FINALIZER_PID, &binding, &mut missing_terminal)
            .unwrap_err()
            .code(),
        "authority_native_system_durable_snapshot_invalid"
    );
    assert!(!missing_terminal.events.contains(&"finalizer.waitNatural"));
}

#[test]
fn extra_service_handle_blocks_absence_and_final_receipt() {
    let (_, _, binding) = fixture();
    let mut operations = FakeFinalizerOperations::exit_ready(&binding);
    operations.extra_service_handles = 1;
    assert_eq!(
        finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations)
            .unwrap_err()
            .code(),
        "authority_native_test_service_still_present"
    );
    assert!(operations
        .snapshot
        .service_delete_pending_receipt_sha256
        .is_some());
    assert!(operations
        .snapshot
        .finalizer_handles_closed_receipt_sha256
        .is_some());
    assert!(operations.snapshot.service_absent_receipt_sha256.is_none());

    operations.extra_service_handles = 0;
    let outcome = finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations).unwrap();
    assert!(outcome.resumed_from_durable_state);
    assert_eq!(
        Some(outcome.service_absent_receipt_sha256),
        operations.snapshot.service_absent_receipt_sha256
    );
}

#[test]
fn crash_before_delete_intent_replays_without_any_lifecycle_action() {
    let (_, _, binding) = fixture();
    let mut operations = FakeFinalizerOperations::exit_ready(&binding);
    operations.fail_once = Some("finalizer.persistDeleteIntent");
    assert!(finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations).is_err());
    assert!(operations
        .snapshot
        .service_delete_intent_receipt_sha256
        .is_none());
    assert!(!operations.events.contains(&"finalizer.observeIdentity"));
    assert!(!operations.events.contains(&"finalizer.markDeletePending"));

    let mut restarted = operations.restart_after_crash();
    finalize_bound_native_worker(FINALIZER_PID, &binding, &mut restarted).unwrap();
    assert!(restarted.events.contains(&"finalizer.persistDeleteIntent"));
    assert!(restarted.events.contains(&"finalizer.markDeletePending"));
}

#[test]
fn crash_after_intent_before_delete_replays_from_exact_live_identity() {
    let (_, _, binding) = fixture();
    let mut operations = FakeFinalizerOperations::exit_ready(&binding);
    operations.fail_once = Some("finalizer.markDeletePending");
    assert!(finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations).is_err());
    assert!(operations
        .snapshot
        .service_delete_pending_receipt_sha256
        .is_none());
    assert!(operations
        .snapshot
        .service_delete_intent_receipt_sha256
        .is_some());
    assert_eq!(
        operations.live_state,
        NativeWorkerFinalizationLiveState::ExactServiceAndWorker
    );

    let mut restarted = operations.restart_after_crash();
    assert_eq!(
        restarted.live_state,
        NativeWorkerFinalizationLiveState::ExactServiceStoppedAndWorkerAbsent
    );
    let outcome = finalize_bound_native_worker(FINALIZER_PID, &binding, &mut restarted).unwrap();
    assert_eq!(
        Some(outcome.service_absent_receipt_sha256),
        restarted.snapshot.service_absent_receipt_sha256
    );
    assert!(restarted.events.contains(&"finalizer.markDeletePending"));
    assert!(!restarted.events.contains(&"finalizer.waitNatural"));
    assert!(!restarted.events.contains(&"finalizer.requestStop"));
    assert!(!restarted.events.contains(&"finalizer.waitStopped"));
    assert!(!restarted.events.contains(&"finalizer.waitProcess"));
}

#[test]
fn crash_after_forced_stop_before_delete_resumes_from_exact_stopped_absence() {
    let (_, _, binding) = fixture();
    let mut operations = FakeFinalizerOperations::exit_ready(&binding);
    operations.natural_exit = NativeWorkerNaturalExit::StillRunning;
    operations.fail_once = Some("finalizer.markDeletePending");
    assert!(finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations).is_err());
    assert!(operations.events.contains(&"finalizer.requestStop"));
    assert!(operations.service_stopped);
    assert!(operations.process_exited);
    assert!(operations
        .snapshot
        .service_delete_intent_receipt_sha256
        .is_some());
    assert!(operations
        .snapshot
        .service_delete_pending_receipt_sha256
        .is_none());

    let mut restarted = operations.restart_after_crash();
    assert_eq!(
        restarted.live_state,
        NativeWorkerFinalizationLiveState::ExactServiceStoppedAndWorkerAbsent
    );
    let outcome = finalize_bound_native_worker(FINALIZER_PID, &binding, &mut restarted).unwrap();
    assert_eq!(
        Some(outcome.service_absent_receipt_sha256),
        restarted.snapshot.service_absent_receipt_sha256
    );
    assert!(restarted.events.contains(&"finalizer.observeIdentity"));
    assert!(restarted.events.contains(&"finalizer.markDeletePending"));
    for forbidden in [
        "finalizer.waitNatural",
        "finalizer.requestStop",
        "finalizer.waitStopped",
        "finalizer.waitProcess",
        "finalizer.observeDeleteTarget",
    ] {
        assert!(!restarted.events.contains(&forbidden));
    }
}

#[test]
fn crash_after_delete_before_transition_receipt_recovers_from_durable_intent() {
    let (_, _, binding) = fixture();
    let mut operations = FakeFinalizerOperations::exit_ready(&binding);
    operations.fail_once = Some("finalizer.persistDeletePending");
    assert!(finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations).is_err());
    assert!(operations
        .snapshot
        .service_delete_pending_receipt_sha256
        .is_none());
    assert_eq!(
        operations.live_state,
        NativeWorkerFinalizationLiveState::DeletePendingAndWorkerAbsent
    );

    let mut restarted = operations.restart_after_crash();
    let outcome = finalize_bound_native_worker(FINALIZER_PID, &binding, &mut restarted).unwrap();
    assert_eq!(
        Some(outcome.service_absent_receipt_sha256),
        restarted.snapshot.service_absent_receipt_sha256
    );
    assert!(restarted.events.contains(&"finalizer.observeDeleteTarget"));
    assert!(restarted.events.contains(&"finalizer.persistDeletePending"));
    assert!(!restarted.events.contains(&"finalizer.observeIdentity"));
    assert!(!restarted.events.contains(&"finalizer.markDeletePending"));
}

#[test]
fn durable_delete_resumes_handles_only_after_prior_finalizer_is_absent() {
    for fault in ["finalizer.closeHandles", "finalizer.persistHandlesClosed"] {
        let (_, _, binding) = fixture();
        let mut operations = FakeFinalizerOperations::exit_ready(&binding);
        operations.fail_once = Some(fault);
        assert!(finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations).is_err());
        assert!(operations
            .snapshot
            .service_delete_pending_receipt_sha256
            .is_some());
        assert!(operations
            .snapshot
            .finalizer_handles_closed_receipt_sha256
            .is_none());

        assert_eq!(
            finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations)
                .unwrap_err()
                .code(),
            "authority_native_finalizer_prior_identity_still_current"
        );

        let mut restarted = operations.restart_after_crash();
        let outcome = finalize_bound_native_worker(FINALIZER_PID, &binding, &mut restarted)
            .unwrap_or_else(|error| panic!("fault {fault} did not recover: {}", error.code()));
        assert_eq!(
            Some(outcome.service_absent_receipt_sha256),
            restarted.snapshot.service_absent_receipt_sha256
        );
        assert!(restarted.events.contains(&"finalizer.recoverHandlesClosed"));
        assert!(!restarted.events.contains(&"finalizer.observeIdentity"));
        assert!(!restarted.events.contains(&"finalizer.markDeletePending"));
        assert!(!restarted.events.contains(&"finalizer.closeHandles"));
    }
}

#[test]
fn durable_handles_closed_resumes_only_absence_boundary() {
    let (_, _, binding) = fixture();
    let mut operations = FakeFinalizerOperations::exit_ready(&binding);
    operations.fail_once = Some("finalizer.scmAbsence");
    assert!(finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations).is_err());
    assert!(operations
        .snapshot
        .finalizer_handles_closed_receipt_sha256
        .is_some());
    assert!(operations.snapshot.service_absent_receipt_sha256.is_none());

    let mut restarted = operations.restart_after_crash();
    let outcome = finalize_bound_native_worker(FINALIZER_PID, &binding, &mut restarted).unwrap();
    assert_eq!(
        Some(outcome.service_absent_receipt_sha256),
        restarted.snapshot.service_absent_receipt_sha256
    );
    assert!(restarted.events.contains(&"finalizer.scmAbsence"));
    assert!(!restarted.events.contains(&"finalizer.recoverHandlesClosed"));
    assert!(!restarted.events.contains(&"finalizer.markDeletePending"));
}

#[test]
fn durable_absent_replay_is_read_only_and_live_bound() {
    let (_, _, binding) = fixture();
    let mut operations = FakeFinalizerOperations::exit_ready(&binding);
    finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations).unwrap();

    let mut restarted = operations.restart_after_crash();
    let outcome = finalize_bound_native_worker(FINALIZER_PID, &binding, &mut restarted).unwrap();
    assert!(outcome.resumed_from_durable_state);
    assert_eq!(
        Some(outcome.service_absent_receipt_sha256),
        restarted.snapshot.service_absent_receipt_sha256
    );
    assert!(restarted.events.contains(&"finalizer.observeLiveState"));
    assert!(restarted.events.contains(&"finalizer.verifyCompleted"));
    for forbidden in [
        "finalizer.observeIdentity",
        "finalizer.markDeletePending",
        "finalizer.closeHandles",
        "finalizer.recoverHandlesClosed",
        "finalizer.scmAbsence",
        "finalizer.verifyZeroResidue",
        "finalizer.persistServiceAbsent",
    ] {
        assert!(!restarted.events.contains(&forbidden));
    }
}

#[test]
fn every_durable_finalization_receipt_is_strictly_recomputed_on_reopen() {
    let (_, _, binding) = fixture();
    let mut completed = FakeFinalizerOperations::exit_ready(&binding);
    finalize_bound_native_worker(FINALIZER_PID, &binding, &mut completed).unwrap();

    let mut intent_tamper = completed.restart_after_crash();
    intent_tamper.snapshot.service_delete_intent_receipt_sha256 = Some([0xe1; 32]);
    assert_eq!(
        finalize_bound_native_worker(FINALIZER_PID, &binding, &mut intent_tamper)
            .unwrap_err()
            .code(),
        "authority_native_test_delete_intent_invalid"
    );

    let mut delete_tamper = completed.restart_after_crash();
    delete_tamper.snapshot.service_delete_pending_receipt_sha256 = Some([0xe2; 32]);
    assert_eq!(
        finalize_bound_native_worker(FINALIZER_PID, &binding, &mut delete_tamper)
            .unwrap_err()
            .code(),
        "authority_native_test_delete_pending_invalid"
    );

    let mut handles_tamper = completed.restart_after_crash();
    handles_tamper
        .snapshot
        .finalizer_handles_closed_receipt_sha256 = Some([0xe3; 32]);
    assert_eq!(
        finalize_bound_native_worker(FINALIZER_PID, &binding, &mut handles_tamper)
            .unwrap_err()
            .code(),
        "authority_native_test_handles_closed_invalid"
    );

    let mut absent_tamper = completed.restart_after_crash();
    absent_tamper.snapshot.service_absent_receipt_sha256 = Some([0xe4; 32]);
    assert_eq!(
        finalize_bound_native_worker(FINALIZER_PID, &binding, &mut absent_tamper)
            .unwrap_err()
            .code(),
        "authority_native_test_service_absent_invalid"
    );

    for operations in [intent_tamper, delete_tamper, handles_tamper, absent_tamper] {
        assert!(!operations.events.contains(&"finalizer.markDeletePending"));
        assert!(!operations.events.contains(&"finalizer.closeHandles"));
        assert!(!operations.events.contains(&"finalizer.scmAbsence"));
    }
}

#[test]
fn persisted_finalizer_identity_drift_fails_before_lifecycle_recovery() {
    let (_, _, binding) = fixture();
    let mut completed = FakeFinalizerOperations::exit_ready(&binding);
    finalize_bound_native_worker(FINALIZER_PID, &binding, &mut completed).unwrap();
    let mut replay = completed.restart_after_crash();
    replay.persisted_intent_finalizer = Some(
        NativeFinalizerProcessIdentity::new(
            FINALIZER_PID,
            FINALIZER_CREATION_TIME + 9,
            FINALIZER_IMAGE_SHA256,
        )
        .unwrap(),
    );
    assert_eq!(
        finalize_bound_native_worker(FINALIZER_PID, &binding, &mut replay)
            .unwrap_err()
            .code(),
        "authority_native_test_delete_intent_invalid"
    );
    assert!(!replay.events.contains(&"finalizer.observeLiveState"));
    assert!(!replay.events.contains(&"finalizer.recoverHandlesClosed"));
}

#[test]
fn durable_prefix_and_live_scm_disagreement_never_rolls_forward() {
    let (_, _, binding) = fixture();
    let mut no_intent = FakeFinalizerOperations::exit_ready(&binding);
    no_intent.live_state = NativeWorkerFinalizationLiveState::ServiceAbsentAndWorkerAbsent;
    assert_eq!(
        finalize_bound_native_worker(FINALIZER_PID, &binding, &mut no_intent)
            .unwrap_err()
            .code(),
        "authority_native_worker_delete_intent_missing"
    );

    let mut durable_delete = FakeFinalizerOperations::exit_ready(&binding);
    durable_delete.fail_once = Some("finalizer.closeHandles");
    assert!(finalize_bound_native_worker(FINALIZER_PID, &binding, &mut durable_delete).is_err());
    durable_delete.fail_once = None;
    durable_delete.live_state = NativeWorkerFinalizationLiveState::ExactServiceAndWorker;
    durable_delete.events.clear();

    let mut durable_handles = FakeFinalizerOperations::exit_ready(&binding);
    durable_handles.fail_once = Some("finalizer.scmAbsence");
    assert!(finalize_bound_native_worker(FINALIZER_PID, &binding, &mut durable_handles).is_err());
    durable_handles.fail_once = None;
    durable_handles.live_state = NativeWorkerFinalizationLiveState::ExactServiceAndWorker;
    durable_handles.events.clear();

    let mut durable_absent = FakeFinalizerOperations::exit_ready(&binding);
    finalize_bound_native_worker(FINALIZER_PID, &binding, &mut durable_absent).unwrap();
    durable_absent.live_state = NativeWorkerFinalizationLiveState::DeletePendingAndWorkerAbsent;
    durable_absent.events.clear();

    for mut operations in [durable_delete, durable_handles, durable_absent] {
        assert_eq!(
            finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations)
                .unwrap_err()
                .code(),
            "authority_native_worker_finalization_live_state_mismatch"
        );
        assert!(!operations.events.contains(&"finalizer.markDeletePending"));
        assert!(!operations.events.contains(&"finalizer.closeHandles"));
    }
}

#[test]
fn service_identity_acl_and_worker_swaps_fail_before_delete_or_target_transition() {
    let (_, _, binding) = fixture();

    let mut service_swap = FakeFinalizerOperations::exit_ready(&binding);
    service_swap.observed_service_identity = [0xe1; 32];
    assert_eq!(
        finalize_bound_native_worker(FINALIZER_PID, &binding, &mut service_swap)
            .unwrap_err()
            .code(),
        "authority_native_worker_finalization_live_readback_mismatch"
    );

    let mut worker_swap = FakeFinalizerOperations::exit_ready(&binding);
    worker_swap.observed_worker =
        NativeWorkerProcessIdentity::new(WORKER_PID, 9_999, [0x94; 32]).unwrap();
    assert_eq!(
        finalize_bound_native_worker(FINALIZER_PID, &binding, &mut worker_swap)
            .unwrap_err()
            .code(),
        "authority_native_worker_process_identity_mismatch"
    );

    for operations in [&service_swap, &worker_swap] {
        assert!(!operations.events.contains(&"finalizer.persistDeleteIntent"));
        assert!(!operations.events.contains(&"finalizer.markDeletePending"));
        assert!(!operations.events.contains(&"finalizer.observeDeleteTarget"));
    }
}

#[test]
fn every_zero_residue_dimension_remains_mandatory() {
    for index in 0..8 {
        let (_, _, binding) = fixture();
        let mut operations = FakeFinalizerOperations::exit_ready(&binding);
        operations.residue_flags[index] = false;
        assert_eq!(
            finalize_bound_native_worker(FINALIZER_PID, &binding, &mut operations)
                .unwrap_err()
                .code(),
            "authority_native_system_zero_residue_invalid",
            "dimension {index}"
        );
        assert!(operations.snapshot.service_absent_receipt_sha256.is_none());
    }
}

#[test]
fn durable_one_use_tombstones_must_remain_present_and_valid() {
    let readback = NativeZeroResidueReadback::new(
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        ZERO_RESIDUE_READBACK,
    )
    .unwrap();
    assert!(readback.nonce_consumption_receipt_present_and_valid);
    assert!(readback.candidate_consumption_tombstone_present_and_valid);

    for missing_tombstone in [6, 7] {
        let mut flags = [true; 8];
        flags[missing_tombstone] = false;
        assert_eq!(
            NativeZeroResidueReadback::new(
                flags[0],
                flags[1],
                flags[2],
                flags[3],
                flags[4],
                flags[5],
                flags[6],
                flags[7],
                ZERO_RESIDUE_READBACK,
            )
            .unwrap_err()
            .code(),
            "authority_native_system_zero_residue_invalid"
        );
    }
}

#[test]
fn plan_generation_and_process_binding_fail_before_actor_operations() {
    let (preview, mut lease, binding) = fixture();
    let bad_worker = NativeWorkerProcessIdentity::new(WORKER_PID, 9_111, [0x94; 32]).unwrap();
    let bad_binding = NativeSystemTransactionBinding::new(
        binding.capsule_sha256,
        [0xd1; 32],
        binding.generation_sha256,
        binding.transaction_started_receipt_sha256,
        binding.exact_service_identity_sha256,
        bad_worker.digest(),
        WORKER_PID,
        9_111,
        [0x94; 32],
    )
    .unwrap();
    let mut system = FakeSystemOperations::new(&bad_binding);
    assert_eq!(
        execute_system(&preview, &mut lease, &bad_binding, &mut system)
            .unwrap_err()
            .code(),
        "authority_native_system_transaction_plan_mismatch"
    );
    assert!(system.events.is_empty());

    assert_eq!(
        NativeSystemTransactionBinding::new(
            binding.capsule_sha256,
            binding.plan_sha256,
            binding.generation_sha256,
            binding.transaction_started_receipt_sha256,
            binding.exact_service_identity_sha256,
            [0xee; 32],
            WORKER_PID,
            9_111,
            [0x94; 32],
        )
        .unwrap_err()
        .code(),
        "authority_native_system_transaction_binding_invalid"
    );
}
