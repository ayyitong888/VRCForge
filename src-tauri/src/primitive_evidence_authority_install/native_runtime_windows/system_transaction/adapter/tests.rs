use super::*;
use crate::primitive_evidence_authority_install::{
    bootstrap_activation::CandidateProcessEvidence,
    finalizer_commit_protocol::{
        ActiveHeadCasDisposition, ActiveHeadCasReadback, ActiveHeadPriorReadback,
        CandidateActivationIdentity, CommittedRuntimeIdentity, DurableFileIdentity,
        ExactServiceProcessIdentity, ExactServiceRuntimeIdentity, FinalizerCommitPlanBinding,
        OperationResiduePlan, OperationZeroResidueReadback, ProtocolWriteDisposition,
        ResidueDimension, ResidueObjectPlan, ResidueObjectReadback, TransactionStartedEvidence,
        WriterHandlesClosedReadback,
    },
    finalizer_commit_store_windows::FinalizerCommitReceiptStore,
    finalizer_generation_seal::{
        generation_progress_relative_name, GenerationSealBinding,
        GenerationSealTerminalAuthorization,
    },
    AuthorityMaintenanceOperation,
};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

static NEXT_TEMP: AtomicU64 = AtomicU64::new(1);

struct TempRoot(PathBuf);

impl TempRoot {
    fn new(label: &str) -> Self {
        let sequence = NEXT_TEMP.fetch_add(1, Ordering::Relaxed);
        let root = std::env::temp_dir().join(format!(
            "vrcforge-finalizer-adapter-{label}-{}-{sequence}",
            std::process::id()
        ));
        fs::create_dir(&root).unwrap();
        Self(root)
    }
}

impl Drop for TempRoot {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn residue_dimensions() -> [ResidueDimension; 13] {
    [
        ResidueDimension::MaintenanceService,
        ResidueDimension::TransientStaging,
        ResidueDimension::CandidateActivationCredential,
        ResidueDimension::MaintenancePipe,
        ResidueDimension::WorkerProcessAndState,
        ResidueDimension::WorkerNonce,
        ResidueDimension::CandidateConsumption,
        ResidueDimension::FinalizerReceiptPublishing,
        ResidueDimension::ActiveHead,
        ResidueDimension::RetirementStaging,
        ResidueDimension::RetirementAborted,
        ResidueDimension::RetirementFinal,
        ResidueDimension::FinalizerCommitStore,
    ]
}

fn residue_object_plan(index: usize) -> ResidueObjectPlan {
    let dimension = residue_dimensions()[index];
    let binding_byte = 0x80_u8.saturating_add(index as u8);
    if matches!(
        dimension,
        ResidueDimension::WorkerNonce
            | ResidueDimension::CandidateConsumption
            | ResidueDimension::ActiveHead
            | ResidueDimension::FinalizerCommitStore
    ) {
        ResidueObjectPlan::present_exact(
            dimension,
            [binding_byte; 32],
            [0xa0_u8.saturating_add(index as u8); 32],
        )
        .unwrap()
    } else {
        ResidueObjectPlan::absent(dimension, [binding_byte; 32]).unwrap()
    }
}

fn residue_plan() -> OperationResiduePlan {
    OperationResiduePlan::new(
        AuthorityMaintenanceOperation::Install,
        std::array::from_fn(residue_object_plan),
    )
    .unwrap()
}

fn zero_residue(kernel_seed: u8) -> OperationZeroResidueReadback {
    let plan = residue_plan();
    let objects = std::array::from_fn(|index| {
        let object = residue_object_plan(index);
        let kernel_readback = [kernel_seed.saturating_add(index as u8); 32];
        if matches!(
            residue_dimensions()[index],
            ResidueDimension::WorkerNonce
                | ResidueDimension::CandidateConsumption
                | ResidueDimension::ActiveHead
                | ResidueDimension::FinalizerCommitStore
        ) {
            ResidueObjectReadback::present_exact(
                object,
                [0xa0_u8.saturating_add(index as u8); 32],
                kernel_readback,
            )
            .unwrap()
        } else {
            ResidueObjectReadback::absent(object, kernel_readback).unwrap()
        }
    });
    OperationZeroResidueReadback::new(plan, objects).unwrap()
}

fn plan_binding() -> FinalizerCommitPlanBinding {
    FinalizerCommitPlanBinding::new(
        AuthorityMaintenanceOperation::Install,
        [0x70; 32],
        [0x71; 32],
        [0x54; 32],
        [0x5a; 32],
        [0x61; 32],
        [0x62; 32],
        [0x64; 32],
        4,
        [0x58; 32],
        residue_plan(),
    )
    .unwrap()
}

fn binding_for_root(root: &Path, seed: u8) -> FinalizerCommitBinding {
    let root_sha256 =
        FinalizerCommitReceiptStore::unsecured_test_root_identity_sha256(root).unwrap();
    FinalizerCommitBinding::new(
        [seed; 32],
        [seed.wrapping_add(1); 32],
        [seed.wrapping_add(2); 32],
        [seed.wrapping_add(3); 32],
        plan_binding(),
        root_sha256,
    )
    .unwrap()
}

fn artifacts() -> NonceArtifactPair {
    NonceArtifactPair::new(
        DurableFileIdentity::new(0x101, [0x21; 16], 1, 121, [0x31; 32]).unwrap(),
        DurableFileIdentity::new(0x102, [0x22; 16], 1, 122, [0x32; 32]).unwrap(),
    )
    .unwrap()
}

fn worker() -> ExactServiceProcessIdentity {
    ExactServiceProcessIdentity::new([0x70; 32], 2222, 555, [0x71; 32]).unwrap()
}

fn service_process(process_id: u32, creation_time: u64) -> CandidateProcessEvidence {
    CandidateProcessEvidence::from_held_process(
        process_id,
        creation_time,
        [0x5a; 32],
        0x2000,
        0x3030,
        [0x31; 16],
        1,
        0x20,
    )
    .unwrap()
}

fn candidate_runtime() -> ExactServiceRuntimeIdentity {
    ExactServiceRuntimeIdentity::from_observed([0x54; 32], [0x55; 32], service_process(3333, 666))
        .unwrap()
}

fn committed_runtime() -> ExactServiceRuntimeIdentity {
    ExactServiceRuntimeIdentity::from_observed([0x54; 32], [0x58; 32], service_process(4242, 777))
        .unwrap()
}

fn seal_ready(binding: FinalizerCommitBinding) -> SealReadyEvidence {
    SealReadyEvidence::new(
        artifacts(),
        WriterHandlesClosedReadback::new(worker(), true, [0x41; 32], true, [0x42; 32]).unwrap(),
        CandidateActivationIdentity::new(
            binding,
            artifacts(),
            [0x54; 32],
            [0x55; 32],
            service_process(3333, 666),
            [0x5b; 32],
        )
        .unwrap(),
    )
    .unwrap()
}

fn sealed_security() -> ExactSealedSecurityReadback {
    ExactSealedSecurityReadback::new(
        [0x51; 32], [0x51; 32], [0x51; 32], true, true, true, [0x53; 32],
    )
    .unwrap()
}

fn candidate_stopped() -> CandidateStoppedReadback {
    CandidateStoppedReadback::exact_stopped(
        candidate_runtime().exact_runtime_instance_sha256(),
        3333,
        666,
        [0x5a; 32],
        [0x42; 32],
        [0x56; 32],
        [0x57; 32],
    )
    .unwrap()
}

fn generation_authorization(
    binding: FinalizerCommitBinding,
    seed: u8,
) -> GenerationSealTerminalAuthorization {
    let digest = |offset: u8| [seed.wrapping_add(offset); 32];
    GenerationSealTerminalAuthorization::exact_test_fixture(
        GenerationSealBinding::from_commit_binding(binding).unwrap(),
        digest(0),
        digest(1),
        digest(2),
        digest(3),
        binding.final_commit_store_root_identity_sha256(),
        digest(5),
        digest(6),
        digest(7),
    )
    .unwrap()
}

fn write_generation_progress(root: &Path, authorization: &GenerationSealTerminalAuthorization) {
    let projection = authorization.projection();
    for sequence in 0..=projection.terminal_sequence() {
        let name = generation_progress_relative_name(&projection.manifest_sha256(), sequence);
        fs::write(root.join(name), format!("adapter-progress-{sequence}")).unwrap();
    }
}

fn final_commit(binding: FinalizerCommitBinding) -> FinalCommitEvidence {
    let active_head = ActiveHeadCasReadback::new(
        ActiveHeadPriorReadback::present([0x61; 32], [0x61; 32]).unwrap(),
        [0x62; 32],
        [0x62; 32],
        binding.generation_sha256(),
        [0x64; 32],
        4,
        ActiveHeadCasDisposition::Applied,
        [0x63; 32],
    )
    .unwrap();
    FinalCommitEvidence::new(
        active_head,
        CommittedRuntimeIdentity::new(
            active_head,
            committed_runtime(),
            [0x66; 16],
            [0x67; 32],
            [0x68; 32],
            [0x6a; 32],
            binding.expected_final_commit_gate_sha256(),
        )
        .unwrap(),
        zero_residue(0xc0),
    )
    .unwrap()
}

fn adapter_at_exit_ready(root: &Path, seed: u8) -> NativeFinalizerCommitAdapter {
    let binding = binding_for_root(root, seed);
    let store = FinalizerCommitReceiptStore::open_unsecured_test(root, binding).unwrap();
    let state = FinalizerCommitProtocolState::transaction_started(
        binding,
        TransactionStartedEvidence::new(binding, worker(), [0x72; 32]).unwrap(),
    )
    .unwrap();
    let mut adapter = NativeFinalizerCommitAdapter::begin(store, state).unwrap();
    adapter
        .system_actor()
        .record_apply_ready(ApplyReadyEvidence::new([0x72; 32]).unwrap())
        .unwrap();
    adapter
        .system_actor()
        .record_seal_ready(seal_ready(binding))
        .unwrap();
    adapter
        .system_actor()
        .record_exit_ready(ExitReadyEvidence::new(worker(), [0x41; 32], [0x74; 32]).unwrap())
        .unwrap();
    adapter
}

#[test]
fn legacy_entry_stays_fail_closed_until_typed_inputs_are_wired() {
    assert_eq!(
        require_finalizer_owned_commit_protocol()
            .unwrap_err()
            .code(),
        NATIVE_SYSTEM_TRANSACTION_ADAPTER_BLOCKER
    );
}

#[test]
fn system_stops_at_durable_exit_ready_and_restart_recomputes_the_chain() {
    let root = TempRoot::new("restart");
    let adapter = adapter_at_exit_ready(&root.0, 0x11);
    let binding = adapter.binding();
    assert_eq!(adapter.latest_stage(), FinalizerCommitStage::ExitReady);
    assert_eq!(
        adapter.directive(),
        FinalizerCommitRecoveryDirective::RecoverGenerationSealTerminalAuthorization
    );
    let tip = adapter.tip();
    drop(adapter);

    let reopened = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
    let restarted = NativeFinalizerCommitAdapter::restart(reopened).unwrap();
    assert_eq!(restarted.latest_stage(), FinalizerCommitStage::ExitReady);
    assert_eq!(restarted.tip(), tip);
    assert_eq!(restarted.protocol_state().binding(), binding);
}

#[test]
fn held_finalizer_lease_is_required_for_terminal_durable_receipts_and_replay_is_exact() {
    let root = TempRoot::new("terminal");
    let mut adapter = adapter_at_exit_ready(&root.0, 0x21);
    let binding = adapter.binding();
    let authorization = generation_authorization(binding, 0x5d);
    write_generation_progress(&root.0, &authorization);
    let lease = adapter
        .acquire_elevated_finalizer_lease_for_test(101)
        .unwrap();

    let persisted = adapter
        .elevated_finalizer(&lease)
        .record_seal_complete(
            &authorization,
            artifacts(),
            sealed_security(),
            candidate_stopped(),
        )
        .unwrap();
    assert_eq!(persisted.disposition(), ProtocolWriteDisposition::Created);
    assert_eq!(adapter.latest_stage(), FinalizerCommitStage::SealComplete);

    let replay = adapter
        .elevated_finalizer(&lease)
        .record_seal_complete(
            &authorization,
            artifacts(),
            sealed_security(),
            candidate_stopped(),
        )
        .unwrap();
    assert_eq!(
        replay.disposition(),
        ProtocolWriteDisposition::AlreadyIdentical
    );

    let evidence = final_commit(binding);
    let committed = adapter
        .elevated_finalizer(&lease)
        .record_final_commit(evidence)
        .unwrap();
    assert_eq!(committed.disposition(), ProtocolWriteDisposition::Created);
    assert_eq!(adapter.latest_stage(), FinalizerCommitStage::FinalCommit);
    assert_eq!(
        adapter.directive(),
        FinalizerCommitRecoveryDirective::ReadOnlyVerifyFinalCommitAndRuntime
    );

    let replay = adapter
        .elevated_finalizer(&lease)
        .record_final_commit(evidence)
        .unwrap();
    assert_eq!(
        replay.disposition(),
        ProtocolWriteDisposition::AlreadyIdentical
    );
    assert!(adapter
        .acquire_elevated_finalizer_lease_for_test(102)
        .is_err());
}

#[test]
fn phase_confusion_and_cross_transaction_actor_swap_fail_without_advancing_state() {
    let first_root = TempRoot::new("actor-a");
    let second_root = TempRoot::new("actor-b");
    let mut first = adapter_at_exit_ready(&first_root.0, 0x31);
    let mut second = adapter_at_exit_ready(&second_root.0, 0x41);
    let first_lease = first
        .acquire_elevated_finalizer_lease_for_test(201)
        .unwrap();
    let first_binding = first.binding();

    assert!(first
        .elevated_finalizer(&first_lease)
        .record_final_commit(final_commit(first_binding))
        .is_err());
    assert_eq!(first.latest_stage(), FinalizerCommitStage::ExitReady);

    assert!(first
        .system_actor()
        .record_apply_ready(ApplyReadyEvidence::new([0x72; 32]).unwrap())
        .is_err());
    assert_eq!(first.latest_stage(), FinalizerCommitStage::ExitReady);

    let second_authorization = generation_authorization(second.binding(), 0x6d);
    write_generation_progress(&second_root.0, &second_authorization);
    assert!(second
        .elevated_finalizer(&first_lease)
        .record_seal_complete(
            &second_authorization,
            artifacts(),
            sealed_security(),
            candidate_stopped(),
        )
        .is_err());
    assert_eq!(second.latest_stage(), FinalizerCommitStage::ExitReady);
}
