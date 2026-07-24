use super::*;
use std::{
    collections::BTreeMap,
    sync::{Arc, Barrier},
    thread,
};

#[derive(Clone)]
struct FakeBoundary {
    shared: Arc<Mutex<FakeBoundaryState>>,
}

struct FakeBoundaryState {
    identity: AuthorityRuntimeIdentity,
    error: Option<String>,
}

impl FakeBoundary {
    fn new(identity: AuthorityRuntimeIdentity) -> Self {
        Self {
            shared: Arc::new(Mutex::new(FakeBoundaryState {
                identity,
                error: None,
            })),
        }
    }

    fn set_identity(&self, identity: AuthorityRuntimeIdentity) {
        self.shared.lock().unwrap().identity = identity;
    }

    fn fail_with(&self, value: &str) {
        self.shared.lock().unwrap().error = Some(value.to_owned());
    }
}

impl InstalledBoundaryVerifier for FakeBoundary {
    fn verify_installed_boundary(
        &mut self,
    ) -> Result<AuthorityRuntimeIdentity, RuntimeDependencyError> {
        let state = self.shared.lock().unwrap();
        if let Some(error) = &state.error {
            Err(RuntimeDependencyError::new(error.clone()))
        } else {
            Ok(state.identity.clone())
        }
    }
}

#[derive(Default)]
struct FakeLedgerStore {
    identity: Option<AuthorityRuntimeIdentity>,
    states: BTreeMap<RuntimeTicketRef, RuntimeTicketState>,
    run_bindings: BTreeMap<RuntimeTicketRef, [u8; 32]>,
    prepared_receipts: BTreeMap<RuntimeTicketRef, Vec<u8>>,
    policy_snapshots: BTreeMap<RuntimeTicketRef, Vec<u8>>,
    recovery_bundle_digests: BTreeMap<RuntimeTicketRef, String>,
    armed_receipts: BTreeMap<RuntimeTicketRef, Vec<u8>>,
    results: BTreeMap<RuntimeTicketRef, Vec<u8>>,
    terminal: BTreeMap<RuntimeTicketRef, RuntimeTerminalKind>,
    events: Vec<String>,
    partial_result: bool,
    fail_record_after_partial: bool,
    open_error: Option<String>,
    identity_error: Option<String>,
    fail_issue: bool,
    fail_consume: bool,
    fail_armed: bool,
}

struct FakeLedger {
    shared: Arc<Mutex<FakeLedgerStore>>,
    opened: bool,
}

impl FakeLedger {
    fn new(shared: Arc<Mutex<FakeLedgerStore>>) -> Self {
        Self {
            shared,
            opened: false,
        }
    }
}

impl RuntimeTicketLedger for FakeLedger {
    fn open_existing(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError> {
        let mut store = self.shared.lock().unwrap();
        if let Some(error) = &store.open_error {
            return Err(RuntimeDependencyError::new(error.clone()));
        }
        match &store.identity {
            Some(stored) if stored != identity => {
                return Err(RuntimeDependencyError::new("ledger_identity_drift"));
            }
            None => store.identity = Some(identity.clone()),
            Some(_) => {}
        }
        self.opened = true;
        Ok(())
    }

    fn active_tickets(&mut self) -> Result<Vec<RuntimeActiveTicket>, RuntimeDependencyError> {
        let store = self.shared.lock().unwrap();
        store
            .states
            .iter()
            .filter_map(|(ticket, state)| {
                matches!(
                    state,
                    RuntimeTicketState::Issued | RuntimeTicketState::Consumed
                )
                .then_some((ticket, store.run_bindings.get(ticket)))
            })
            .map(|(ticket, binding)| {
                RuntimeActiveTicket::new(
                    ticket.clone(),
                    binding
                        .copied()
                        .ok_or_else(|| RuntimeDependencyError::new("missing_run_binding"))?,
                    store
                        .prepared_receipts
                        .get(ticket)
                        .cloned()
                        .ok_or_else(|| RuntimeDependencyError::new("missing_prepared_receipt"))?,
                    store
                        .policy_snapshots
                        .get(ticket)
                        .cloned()
                        .ok_or_else(|| RuntimeDependencyError::new("missing_policy_snapshot"))?,
                    store
                        .recovery_bundle_digests
                        .get(ticket)
                        .cloned()
                        .ok_or_else(|| RuntimeDependencyError::new("missing_recovery_bundle"))?,
                    store.armed_receipts.get(ticket).cloned(),
                )
            })
            .collect()
    }

    fn verify_identity(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError> {
        let store = self.shared.lock().unwrap();
        if !self.opened || store.identity.as_ref() != Some(identity) || store.partial_result {
            return Err(RuntimeDependencyError::new(
                "ledger_identity_or_health_invalid",
            ));
        }
        if let Some(error) = &store.identity_error {
            return Err(RuntimeDependencyError::new(error.clone()));
        }
        Ok(())
    }

    fn state(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<RuntimeTicketState>, RuntimeDependencyError> {
        Ok(self.shared.lock().unwrap().states.get(ticket).copied())
    }

    fn issue(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        prepared_receipt_bytes: &[u8],
        canonical_policy_snapshot: &[u8],
    ) -> Result<(), RuntimeDependencyError> {
        let mut store = self.shared.lock().unwrap();
        if store.fail_issue {
            return Err(RuntimeDependencyError::new("issue_failed"));
        }
        if store.states.contains_key(ticket)
            || run_binding_digest.iter().all(|byte| *byte == 0)
            || prepared_receipt_bytes.is_empty()
            || canonical_policy_snapshot.is_empty()
        {
            return Err(RuntimeDependencyError::new("duplicate"));
        }
        store
            .run_bindings
            .insert(ticket.clone(), *run_binding_digest);
        store
            .prepared_receipts
            .insert(ticket.clone(), prepared_receipt_bytes.to_vec());
        store
            .policy_snapshots
            .insert(ticket.clone(), canonical_policy_snapshot.to_vec());
        let recovery_bundle_digest = compute_recovery_bundle_digest(
            ticket.as_str(),
            &hex_encode(run_binding_digest),
            prepared_receipt_bytes,
            canonical_policy_snapshot,
        )
        .map_err(|_| RuntimeDependencyError::new("invalid_recovery_bundle"))?;
        store
            .recovery_bundle_digests
            .insert(ticket.clone(), recovery_bundle_digest);
        store
            .states
            .insert(ticket.clone(), RuntimeTicketState::Issued);
        store.events.push(format!("issue:{}", ticket.as_str()));
        Ok(())
    }

    fn consume(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
    ) -> Result<(), RuntimeDependencyError> {
        let mut store = self.shared.lock().unwrap();
        if store.fail_consume {
            return Err(RuntimeDependencyError::new("consume_failed"));
        }
        if store.states.get(ticket) != Some(&RuntimeTicketState::Issued)
            || store.run_bindings.get(ticket) != Some(run_binding_digest)
        {
            return Err(RuntimeDependencyError::new("transition"));
        }
        store
            .states
            .insert(ticket.clone(), RuntimeTicketState::Consumed);
        store.events.push(format!("consume:{}", ticket.as_str()));
        Ok(())
    }

    fn record_armed_receipt(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        armed_receipt_bytes: &[u8],
    ) -> Result<(), RuntimeDependencyError> {
        let mut store = self.shared.lock().unwrap();
        if store.fail_armed {
            return Err(RuntimeDependencyError::new("armed_write_failed"));
        }
        if store.states.get(ticket) != Some(&RuntimeTicketState::Consumed)
            || store.run_bindings.get(ticket) != Some(run_binding_digest)
            || armed_receipt_bytes.is_empty()
            || store.armed_receipts.contains_key(ticket)
        {
            return Err(RuntimeDependencyError::new("armed_transition"));
        }
        store
            .armed_receipts
            .insert(ticket.clone(), armed_receipt_bytes.to_vec());
        store.events.push(format!("armed:{}", ticket.as_str()));
        Ok(())
    }

    fn record_result_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        result_bytes: &[u8],
    ) -> Result<(), RuntimeDependencyError> {
        let mut store = self.shared.lock().unwrap();
        if store.states.get(ticket) != Some(&RuntimeTicketState::Consumed)
            || store.run_bindings.get(ticket) != Some(run_binding_digest)
        {
            return Err(RuntimeDependencyError::new("transition"));
        }
        if store.fail_record_after_partial {
            store.partial_result = true;
            return Err(RuntimeDependencyError::new("partial_result_write"));
        }
        store.results.insert(ticket.clone(), result_bytes.to_vec());
        store
            .states
            .insert(ticket.clone(), RuntimeTicketState::Result);
        store.events.push(format!("result:{}", ticket.as_str()));
        Ok(())
    }

    fn result_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<Vec<u8>>, RuntimeDependencyError> {
        Ok(self.shared.lock().unwrap().results.get(ticket).cloned())
    }

    fn burn(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        reason: RuntimeTerminalKind,
    ) -> Result<(), RuntimeDependencyError> {
        let mut store = self.shared.lock().unwrap();
        match store.states.get(ticket) {
            Some(RuntimeTicketState::Issued | RuntimeTicketState::Consumed) => {}
            _ => return Err(RuntimeDependencyError::new("transition")),
        }
        if store.run_bindings.get(ticket) != Some(run_binding_digest) {
            return Err(RuntimeDependencyError::new("run_binding_mismatch"));
        }
        store
            .states
            .insert(ticket.clone(), RuntimeTicketState::Burned);
        store.terminal.insert(ticket.clone(), reason);
        store.events.push(format!("burn:{}", ticket.as_str()));
        if reason == RuntimeTerminalKind::RestartRecovery {
            store.partial_result = false;
        }
        Ok(())
    }

    fn burn_recovered(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
    ) -> Result<(), RuntimeDependencyError> {
        self.burn(
            ticket,
            run_binding_digest,
            RuntimeTerminalKind::RestartRecovery,
        )
    }

    fn terminal_reason(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<RuntimeTerminalKind>, RuntimeDependencyError> {
        Ok(self.shared.lock().unwrap().terminal.get(ticket).copied())
    }
}

#[derive(Clone)]
struct FakeSupervisor {
    shared: Arc<Mutex<FakeSupervisorState>>,
}

struct FakeSupervisorState {
    readiness_error: Option<String>,
    readiness_identity_override: Option<[u8; 32]>,
    prepared_identity_override: Option<[u8; 32]>,
    tamper_policy_snapshot: bool,
    service_instance_digest: [u8; 32],
    runner_policy_digest: [u8; 32],
    active: Option<RuntimeRunContext>,
    poll: SupervisorPoll,
    poll_error: bool,
    start_error: bool,
    cancel_error: bool,
    abort_error: bool,
    recovery_error: bool,
    aborts: usize,
    recoveries: usize,
    starts: usize,
    prepares: usize,
    containments: usize,
}

impl FakeSupervisor {
    fn ready() -> Self {
        Self {
            shared: Arc::new(Mutex::new(FakeSupervisorState {
                readiness_error: None,
                readiness_identity_override: None,
                prepared_identity_override: None,
                tamper_policy_snapshot: false,
                service_instance_digest: [0x51; 32],
                runner_policy_digest: [0x52; 32],
                active: None,
                poll: SupervisorPoll::Running,
                poll_error: false,
                start_error: false,
                cancel_error: false,
                abort_error: false,
                recovery_error: false,
                aborts: 0,
                recoveries: 0,
                starts: 0,
                prepares: 0,
                containments: 0,
            })),
        }
    }

    fn complete_with(&self, bytes: Vec<u8>) {
        let context = self.shared.lock().unwrap().active.clone().unwrap();
        let completed = CompletedRunProof::for_runtime_test(
            *context.authority_identity_digest(),
            context.ticket().digest(),
            *context.run_binding_digest(),
            bytes,
        );
        self.shared.lock().unwrap().poll = SupervisorPoll::Completed(completed);
    }

    fn terminate_with(&self, kind: RuntimeTerminalKind) {
        let context = self.shared.lock().unwrap().active.clone().unwrap();
        let reason = match kind {
            RuntimeTerminalKind::Cancelled => BurnReason::Cancelled,
            RuntimeTerminalKind::TimedOut => BurnReason::TimedOut,
            RuntimeTerminalKind::Failed => BurnReason::Failed,
            RuntimeTerminalKind::RestartRecovery => panic!("invalid supervisor terminal"),
        };
        let terminated = BurnedRunProof::for_runtime_test(
            *context.authority_identity_digest(),
            context.ticket().digest(),
            *context.run_binding_digest(),
            reason,
        );
        self.shared.lock().unwrap().poll = SupervisorPoll::Terminated(terminated);
    }

    fn fail_poll(&self) {
        self.shared.lock().unwrap().poll_error = true;
    }

    fn fail_start(&self) {
        self.shared.lock().unwrap().start_error = true;
    }

    fn fail_abort(&self) {
        self.shared.lock().unwrap().abort_error = true;
    }
}

impl FixedModelPartSupervisor for FakeSupervisor {
    fn contain_all_orphans(
        &mut self,
        _identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError> {
        self.shared.lock().unwrap().containments += 1;
        Ok(())
    }

    fn readiness(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<VerifiedReadinessProof, RuntimeDependencyError> {
        let state = self.shared.lock().unwrap();
        if let Some(error) = &state.readiness_error {
            return Err(RuntimeDependencyError::new(error.clone()));
        }
        Ok(VerifiedReadinessProof::for_runtime_test(
            state
                .readiness_identity_override
                .unwrap_or_else(|| identity.binding_digest()),
            state.service_instance_digest,
        ))
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
        let mut state = self.shared.lock().unwrap();
        state.prepares += 1;
        Ok(PreparedRun::for_runtime_test(
            state
                .prepared_identity_override
                .unwrap_or_else(|| identity.binding_digest()),
            ticket.digest(),
            *service_instance_digest,
            state.runner_policy_digest,
        ))
    }

    fn prepared_policy_snapshot(
        &mut self,
        prepared: &PreparedRun,
    ) -> Result<Vec<u8>, RuntimeDependencyError> {
        let mut snapshot = prepared.policy_snapshot().to_vec();
        if self.shared.lock().unwrap().tamper_policy_snapshot {
            snapshot[20] ^= 0x01;
        }
        Ok(snapshot)
    }

    fn start(
        &mut self,
        prepared: PreparedRun,
        context: &RuntimeRunContext,
    ) -> Result<ArmedRecoveryReceipt, RuntimeDependencyError> {
        let mut state = self.shared.lock().unwrap();
        if state.start_error {
            return Err(RuntimeDependencyError::new("start_state_unknown"));
        }
        if state.active.is_some() {
            return Err(RuntimeDependencyError::new("already_active"));
        }
        if !prepared.verifies_for(
            context.authority_identity_digest(),
            &context.ticket().digest(),
            context.service_instance_digest(),
        ) || prepared.receipt() != context.prepared_receipt()
            || prepared.policy_snapshot() != context.canonical_policy_snapshot()
        {
            return Err(RuntimeDependencyError::new("prepared_mismatch"));
        }
        let armed = ArmedRecoveryReceipt::for_runtime_test(
            prepared.receipt(),
            *context.run_binding_digest(),
        );
        state.active = Some(context.clone().with_armed_receipt(armed.clone()));
        state.starts += 1;
        Ok(armed)
    }

    fn poll(
        &mut self,
        context: &RuntimeRunContext,
    ) -> Result<SupervisorPoll, RuntimeDependencyError> {
        let mut state = self.shared.lock().unwrap();
        if state.poll_error {
            return Err(RuntimeDependencyError::new("poll_state_unknown"));
        }
        if state.active.as_ref() != Some(context) {
            return Err(RuntimeDependencyError::new("wrong_ticket"));
        }
        let poll = state.poll.clone();
        if !matches!(poll, SupervisorPoll::Running) {
            state.active = None;
        }
        Ok(poll)
    }

    fn cancel(&mut self, context: &RuntimeRunContext) -> Result<(), RuntimeDependencyError> {
        let state = self.shared.lock().unwrap();
        if state.cancel_error {
            return Err(RuntimeDependencyError::new("cancel_failed"));
        }
        if state.active.as_ref() != Some(context) {
            return Err(RuntimeDependencyError::new("wrong_ticket"));
        }
        Ok(())
    }

    fn abort_and_wait_cleanup(
        &mut self,
        context: &RuntimeRunContext,
    ) -> Result<BurnedRunProof, RuntimeDependencyError> {
        let mut state = self.shared.lock().unwrap();
        if state.abort_error {
            return Err(RuntimeDependencyError::new("abort_cleanup_unknown"));
        }
        if state
            .active
            .as_ref()
            .is_some_and(|active| active != context)
        {
            return Err(RuntimeDependencyError::new("wrong_ticket"));
        }
        state.active = None;
        state.aborts += 1;
        Ok(BurnedRunProof::for_runtime_test(
            *context.authority_identity_digest(),
            context.ticket().digest(),
            *context.run_binding_digest(),
            BurnReason::Failed,
        ))
    }

    fn recover_and_wait_cleanup(
        &mut self,
        context: &RuntimeRecoveryContext,
    ) -> Result<BurnedRunProof, RuntimeDependencyError> {
        let mut state = self.shared.lock().unwrap();
        if state.recovery_error {
            return Err(RuntimeDependencyError::new("recovery_state_unknown"));
        }
        state.recoveries += 1;
        Ok(BurnedRunProof::for_runtime_test(
            *context.authority_identity_digest(),
            context.ticket().digest(),
            *context.run_binding_digest(),
            BurnReason::RestartRecovery,
        ))
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

fn ticket_ref(request_id: &str) -> RuntimeTicketRef {
    ticket_ref_for_identity(&identity(1), request_id)
}

fn runtime(
    boundary: FakeBoundary,
    ledger_store: Arc<Mutex<FakeLedgerStore>>,
    supervisor: FakeSupervisor,
) -> AuthorityRuntime {
    AuthorityRuntime::start(boundary, FakeLedger::new(ledger_store), supervisor)
}

fn run(
    runtime: &AuthorityRuntime,
    request_id: &str,
) -> Result<AuthorityRuntimeReply, AuthorityRuntimeError> {
    runtime.handle(AuthorityRuntimeCommand::RunModelPartComposition {
        request_id: request_id.to_owned(),
    })
}

#[test]
fn startup_requires_all_identity_parts_and_a_healthy_existing_ledger() {
    for index in 0..5 {
        let mut parts = [[1u8; 32], [2; 32], [3; 32], [4; 32], [5; 32]];
        parts[index] = [0; 32];
        assert_eq!(
            AuthorityRuntimeIdentity::new(parts[0], parts[1], parts[2], parts[3], parts[4])
                .unwrap_err()
                .code(),
            "authority_runtime_identity_invalid"
        );
    }

    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore {
        open_error: Some("missing_or_corrupt_ledger:C:\\private".to_owned()),
        ..FakeLedgerStore::default()
    }));
    let runtime = runtime(boundary, store, FakeSupervisor::ready());
    let AuthorityRuntimeReply::Status(status) =
        runtime.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert!(status.global_failure);
    assert!(!status.trusted_boundary_ready);
    assert_eq!(status.blockers, vec![BLOCKER_RUNTIME_STARTUP]);
    assert_eq!(
        run(&runtime, "run-1").unwrap_err().code(),
        "authority_runtime_integrity_failed"
    );
}

#[test]
fn ticket_and_run_bindings_cover_every_runtime_identity_and_supervisor_input() {
    let base = identity(1);
    let base_ticket = ticket_ref_for_identity(&base, "same-request");
    let original_parts = [
        *base.authority_generation_digest(),
        *base.signer_key_id(),
        *base.protected_manifest_digest(),
        *base.installed_layout_digest(),
        *base.service_binary_digest(),
    ];
    for index in 0..original_parts.len() {
        let mut parts = original_parts;
        parts[index] = [0x70 + index as u8; 32];
        let changed =
            AuthorityRuntimeIdentity::new(parts[0], parts[1], parts[2], parts[3], parts[4])
                .unwrap();
        assert_ne!(
            base_ticket,
            ticket_ref_for_identity(&changed, "same-request")
        );
    }
    assert_ne!(base_ticket, ticket_ref_for_identity(&base, "other-request"));

    let prepared_run = PreparedRun::for_runtime_test(
        base.binding_digest(),
        base_ticket.digest(),
        [0x51; 32],
        [0x52; 32],
    );
    let first = run_context(
        &base,
        base_ticket.clone(),
        prepared_run.receipt().clone(),
        prepared_run.policy_snapshot().to_vec(),
    );
    let repeated = run_context(
        &base,
        base_ticket.clone(),
        prepared_run.receipt().clone(),
        prepared_run.policy_snapshot().to_vec(),
    );
    assert_eq!(first.run_binding_digest(), repeated.run_binding_digest());

    let other_instance = PreparedRun::for_runtime_test(
        base.binding_digest(),
        base_ticket.digest(),
        [0x53; 32],
        [0x52; 32],
    );
    let other_policy = PreparedRun::for_runtime_test(
        base.binding_digest(),
        base_ticket.digest(),
        [0x51; 32],
        [0x54; 32],
    );
    assert_ne!(
        first.run_binding_digest(),
        run_context(
            &base,
            base_ticket.clone(),
            other_instance.receipt().clone(),
            other_instance.policy_snapshot().to_vec(),
        )
        .run_binding_digest()
    );
    assert_ne!(
        first.run_binding_digest(),
        run_context(
            &base,
            base_ticket,
            other_policy.receipt().clone(),
            other_policy.policy_snapshot().to_vec(),
        )
        .run_binding_digest()
    );
}

#[test]
fn disconnected_supervisor_is_a_visible_blocker_and_cannot_issue() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let runtime = AuthorityRuntime::start(
        boundary,
        FakeLedger::new(store.clone()),
        DisconnectedModelPartSupervisor,
    );
    let AuthorityRuntimeReply::Status(status) =
        runtime.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert!(!status.trusted_boundary_ready);
    assert!(status.global_failure);
    assert_eq!(status.blockers, vec![BLOCKER_RUNTIME_INTEGRITY]);
    assert_eq!(
        run(&runtime, "run-1").unwrap_err().code(),
        "authority_runtime_integrity_failed"
    );
    assert!(store.lock().unwrap().events.is_empty());
}

#[test]
fn fixed_run_issues_consumes_and_replays_exact_result_bytes_once() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let runtime = runtime(boundary, store.clone(), supervisor.clone());
    assert_eq!(
        run(&runtime, "run-1").unwrap(),
        AuthorityRuntimeReply::RunStarted {
            request_id: "run-1".to_owned()
        }
    );
    assert_eq!(
        runtime
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: "run-1".to_owned()
            })
            .unwrap(),
        AuthorityRuntimeReply::ResultPending {
            request_id: "run-1".to_owned()
        }
    );
    let exact = b"{\"opaque\":\"\\u0000not-normalized\",\"n\":1}".to_vec();
    supervisor.complete_with(exact.clone());
    assert_eq!(
        runtime
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: "run-1".to_owned()
            })
            .unwrap(),
        AuthorityRuntimeReply::ResultExact {
            request_id: "run-1".to_owned(),
            bytes: exact
        }
    );
    assert_eq!(
        run(&runtime, "run-1").unwrap_err().code(),
        "authority_request_duplicate"
    );
    let events = &store.lock().unwrap().events;
    assert_eq!(events.len(), 4);
    assert!(events[0].starts_with("issue:"));
    assert!(events[1].starts_with("consume:"));
    assert!(events[2].starts_with("armed:"));
    assert!(events[3].starts_with("result:"));
}

#[test]
fn fully_validated_supervisor_bytes_are_committed_without_reconstruction() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let runtime = runtime(boundary, store.clone(), supervisor.clone());
    run(&runtime, "run-sealed-result").unwrap();

    let exact = b"{\"binary\":\"\\u0000\\u0001\",\"order\":[3,1,2]}".to_vec();
    let context = supervisor.shared.lock().unwrap().active.clone().unwrap();
    let proof = CompletedRunProof::for_runtime_test(
        *context.authority_identity_digest(),
        context.ticket().digest(),
        *context.run_binding_digest(),
        exact.clone(),
    );
    supervisor.shared.lock().unwrap().poll = SupervisorPoll::Completed(proof);

    assert_eq!(
        runtime
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: "run-sealed-result".to_owned(),
            })
            .unwrap(),
        AuthorityRuntimeReply::ResultExact {
            request_id: "run-sealed-result".to_owned(),
            bytes: exact.clone(),
        }
    );
    assert_eq!(
        store
            .lock()
            .unwrap()
            .results
            .get(&ticket_ref("run-sealed-result")),
        Some(&exact)
    );
}

#[test]
fn concurrent_runs_are_serialized_and_only_one_is_issued() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let runtime = Arc::new(runtime(boundary, store.clone(), supervisor));
    let barrier = Arc::new(Barrier::new(3));
    let mut workers = Vec::new();
    for request_id in ["run-a", "run-b"] {
        let runtime = runtime.clone();
        let barrier = barrier.clone();
        workers.push(thread::spawn(move || {
            barrier.wait();
            run(&runtime, request_id)
        }));
    }
    barrier.wait();
    let outcomes: Vec<_> = workers
        .into_iter()
        .map(|worker| worker.join().unwrap())
        .collect();
    assert_eq!(outcomes.iter().filter(|outcome| outcome.is_ok()).count(), 1);
    assert_eq!(
        outcomes
            .iter()
            .filter_map(|outcome| outcome.as_ref().err())
            .map(AuthorityRuntimeError::code)
            .collect::<Vec<_>>(),
        vec!["authority_run_busy"]
    );
    assert_eq!(
        store
            .lock()
            .unwrap()
            .events
            .iter()
            .filter(|event| event.starts_with("issue:"))
            .count(),
        1
    );
}

#[test]
fn restart_burns_active_ticket_and_duplicate_cannot_run_again() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let first = runtime(boundary.clone(), store.clone(), FakeSupervisor::ready());
    run(&first, "run-restart").unwrap();
    drop(first);

    let recovery_supervisor = FakeSupervisor::ready();
    let second = runtime(boundary, store.clone(), recovery_supervisor.clone());
    let AuthorityRuntimeReply::Status(status) =
        second.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert_eq!(status.startup_burned_tickets, 1);
    assert_eq!(recovery_supervisor.shared.lock().unwrap().recoveries, 1);
    assert_eq!(
        second
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: "run-restart".to_owned()
            })
            .unwrap(),
        AuthorityRuntimeReply::ResultTerminated {
            request_id: "run-restart".to_owned(),
            reason: RuntimeTerminalKind::RestartRecovery,
        }
    );
    assert_eq!(
        run(&second, "run-restart").unwrap_err().code(),
        "authority_request_duplicate"
    );
}

#[test]
fn restart_orphan_is_not_burned_when_supervised_cleanup_is_unknown() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let first = runtime(boundary.clone(), store.clone(), FakeSupervisor::ready());
    run(&first, "run-recovery-unknown").unwrap();
    drop(first);

    let recovery_supervisor = FakeSupervisor::ready();
    recovery_supervisor.shared.lock().unwrap().recovery_error = true;
    let second = runtime(boundary, store.clone(), recovery_supervisor);
    let AuthorityRuntimeReply::Status(status) =
        second.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert!(status.global_failure);
    assert_eq!(status.blockers, vec![BLOCKER_RUNTIME_INTEGRITY]);
    assert_eq!(
        store
            .lock()
            .unwrap()
            .states
            .get(&ticket_ref("run-recovery-unknown")),
        Some(&RuntimeTicketState::Consumed)
    );
    assert!(store
        .lock()
        .unwrap()
        .terminal
        .get(&ticket_ref("run-recovery-unknown"))
        .is_none());
    let error = run(&second, "run-after-unknown-recovery").unwrap_err();
    assert!(error.requires_process_exit());
}

#[test]
fn cancellation_is_terminal_idempotent_and_never_records_a_result() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let runtime = runtime(boundary, store.clone(), supervisor.clone());
    run(&runtime, "run-cancel").unwrap();
    assert_eq!(
        runtime
            .handle(AuthorityRuntimeCommand::Cancel {
                request_id: "run-cancel".to_owned()
            })
            .unwrap(),
        AuthorityRuntimeReply::CancelRequested {
            request_id: "run-cancel".to_owned(),
            already_requested: false
        }
    );
    assert_eq!(
        runtime
            .handle(AuthorityRuntimeCommand::Cancel {
                request_id: "run-cancel".to_owned()
            })
            .unwrap(),
        AuthorityRuntimeReply::CancelRequested {
            request_id: "run-cancel".to_owned(),
            already_requested: true
        }
    );
    assert_eq!(
        store.lock().unwrap().states.get(&ticket_ref("run-cancel")),
        Some(&RuntimeTicketState::Consumed)
    );
    supervisor.terminate_with(RuntimeTerminalKind::Cancelled);
    assert_eq!(
        runtime
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: "run-cancel".to_owned()
            })
            .unwrap(),
        AuthorityRuntimeReply::ResultTerminated {
            request_id: "run-cancel".to_owned(),
            reason: RuntimeTerminalKind::Cancelled,
        }
    );
    assert!(!store
        .lock()
        .unwrap()
        .events
        .iter()
        .any(|event| event.starts_with("result:")));
}

#[test]
fn failed_timeout_and_cancelled_terminals_remain_distinct() {
    for (index, reason) in [
        RuntimeTerminalKind::Failed,
        RuntimeTerminalKind::TimedOut,
        RuntimeTerminalKind::Cancelled,
    ]
    .into_iter()
    .enumerate()
    {
        let request_id = format!("run-terminal-{index}");
        let boundary = FakeBoundary::new(identity(index as u8 + 1));
        let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
        let supervisor = FakeSupervisor::ready();
        let runtime = runtime(boundary, store, supervisor.clone());
        run(&runtime, &request_id).unwrap();
        supervisor.terminate_with(reason);
        assert_eq!(
            runtime
                .handle(AuthorityRuntimeCommand::GetResult {
                    request_id: request_id.clone()
                })
                .unwrap(),
            AuthorityRuntimeReply::ResultTerminated { request_id, reason }
        );
    }
    assert_eq!(
        runtime_terminal_reason(BurnReason::Failed),
        RuntimeTerminalKind::Failed
    );
}

#[test]
fn verified_terminal_reason_survives_runtime_restart() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let first = runtime(boundary.clone(), store.clone(), supervisor.clone());
    run(&first, "run-persisted-terminal").unwrap();
    supervisor.terminate_with(RuntimeTerminalKind::Failed);
    assert_eq!(
        first
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: "run-persisted-terminal".to_owned(),
            })
            .unwrap(),
        AuthorityRuntimeReply::ResultTerminated {
            request_id: "run-persisted-terminal".to_owned(),
            reason: RuntimeTerminalKind::Failed,
        }
    );
    drop(first);

    let second = runtime(boundary, store, FakeSupervisor::ready());
    assert_eq!(
        second
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: "run-persisted-terminal".to_owned(),
            })
            .unwrap(),
        AuthorityRuntimeReply::ResultTerminated {
            request_id: "run-persisted-terminal".to_owned(),
            reason: RuntimeTerminalKind::Failed,
        }
    );
}

#[test]
fn terminal_proof_for_another_ticket_latches_integrity_failure() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let runtime = runtime(boundary, store.clone(), supervisor.clone());
    run(&runtime, "run-ticket-bound").unwrap();

    let context = supervisor.shared.lock().unwrap().active.clone().unwrap();
    let completed = CompletedRunProof::for_runtime_test(
        *context.authority_identity_digest(),
        ticket_ref("run-other").digest(),
        *context.run_binding_digest(),
        b"result".to_vec(),
    );
    supervisor.shared.lock().unwrap().poll = SupervisorPoll::Completed(completed);

    assert_eq!(
        runtime
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: "run-ticket-bound".to_owned(),
            })
            .unwrap_err()
            .code(),
        "authority_runtime_integrity_failed"
    );
    let store = store.lock().unwrap();
    assert_eq!(
        store.states.get(&ticket_ref("run-ticket-bound")),
        Some(&RuntimeTicketState::Consumed)
    );
    assert!(!store.results.contains_key(&ticket_ref("run-ticket-bound")));
}

#[test]
fn completed_proof_with_wrong_run_binding_is_rejected() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let runtime = runtime(boundary, store.clone(), supervisor.clone());
    run(&runtime, "run-binding-bound").unwrap();
    let context = supervisor.shared.lock().unwrap().active.clone().unwrap();
    let completed = CompletedRunProof::for_runtime_test(
        *context.authority_identity_digest(),
        context.ticket().digest(),
        [0x99; 32],
        b"result".to_vec(),
    );
    supervisor.shared.lock().unwrap().poll = SupervisorPoll::Completed(completed);

    let error = runtime
        .handle(AuthorityRuntimeCommand::GetResult {
            request_id: "run-binding-bound".to_owned(),
        })
        .unwrap_err();
    assert!(error.requires_process_exit());
    assert_eq!(
        store
            .lock()
            .unwrap()
            .states
            .get(&ticket_ref("run-binding-bound")),
        Some(&RuntimeTicketState::Consumed)
    );
}

#[test]
fn burned_proof_cannot_replay_across_service_instances() {
    let first_supervisor = FakeSupervisor::ready();
    let first_store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let first = runtime(
        FakeBoundary::new(identity(1)),
        first_store,
        first_supervisor.clone(),
    );
    run(&first, "same-request").unwrap();
    let first_context = first_supervisor
        .shared
        .lock()
        .unwrap()
        .active
        .clone()
        .unwrap();
    let replay = BurnedRunProof::for_runtime_test(
        *first_context.authority_identity_digest(),
        first_context.ticket().digest(),
        *first_context.run_binding_digest(),
        BurnReason::Failed,
    );

    let second_supervisor = FakeSupervisor::ready();
    second_supervisor
        .shared
        .lock()
        .unwrap()
        .service_instance_digest = [0x61; 32];
    let second_store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let second = runtime(
        FakeBoundary::new(identity(1)),
        second_store.clone(),
        second_supervisor.clone(),
    );
    run(&second, "same-request").unwrap();
    second_supervisor.shared.lock().unwrap().poll = SupervisorPoll::Terminated(replay);

    let error = second
        .handle(AuthorityRuntimeCommand::GetResult {
            request_id: "same-request".to_owned(),
        })
        .unwrap_err();
    assert!(error.requires_process_exit());
    assert_eq!(
        second_store
            .lock()
            .unwrap()
            .states
            .get(&ticket_ref("same-request")),
        Some(&RuntimeTicketState::Consumed)
    );
}

#[test]
fn generation_bound_ticket_and_readiness_proof_cannot_replay() {
    let first_identity = identity(1);
    let second_identity = identity(2);
    assert_ne!(
        ticket_ref_for_identity(&first_identity, "same-request"),
        ticket_ref_for_identity(&second_identity, "same-request")
    );

    let supervisor = FakeSupervisor::ready();
    supervisor
        .shared
        .lock()
        .unwrap()
        .readiness_identity_override = Some(first_identity.binding_digest());
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let runtime = runtime(
        FakeBoundary::new(second_identity),
        store.clone(),
        supervisor,
    );
    let AuthorityRuntimeReply::SelfTest(self_test) =
        runtime.handle(AuthorityRuntimeCommand::SelfTest).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert!(!self_test.passed);
    assert_eq!(self_test.blockers, vec![BLOCKER_RUNTIME_INTEGRITY]);
    let AuthorityRuntimeReply::Status(status) =
        runtime.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert!(status.global_failure);
    assert_eq!(status.blockers, vec![BLOCKER_RUNTIME_INTEGRITY]);
    assert!(store.lock().unwrap().events.is_empty());
    let error = run(&runtime, "same-request").unwrap_err();
    assert!(error.requires_process_exit());
}

#[test]
fn unknown_start_or_poll_state_aborts_and_burns_only_with_cleanup_proof() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    supervisor.fail_start();
    let first_runtime = runtime(boundary, store.clone(), supervisor.clone());
    assert_eq!(
        run(&first_runtime, "run-start-unknown").unwrap_err().code(),
        "authority_runtime_integrity_failed"
    );
    assert_eq!(
        store
            .lock()
            .unwrap()
            .states
            .get(&ticket_ref("run-start-unknown")),
        Some(&RuntimeTicketState::Burned)
    );
    assert!(store
        .lock()
        .unwrap()
        .events
        .iter()
        .any(|event| event.starts_with("burn:")));
    assert_eq!(supervisor.shared.lock().unwrap().aborts, 1);

    let boundary = FakeBoundary::new(identity(2));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let second_runtime = runtime(boundary, store.clone(), supervisor.clone());
    run(&second_runtime, "run-poll-unknown").unwrap();
    supervisor.fail_poll();
    assert_eq!(
        second_runtime
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: "run-poll-unknown".to_owned()
            })
            .unwrap_err()
            .code(),
        "authority_runtime_integrity_failed"
    );
    assert_eq!(
        store
            .lock()
            .unwrap()
            .states
            .get(&ticket_ref_for_identity(&identity(2), "run-poll-unknown")),
        Some(&RuntimeTicketState::Burned)
    );
    assert!(store
        .lock()
        .unwrap()
        .events
        .iter()
        .any(|event| event.starts_with("burn:")));

    let boundary = FakeBoundary::new(identity(3));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    supervisor.fail_start();
    supervisor.fail_abort();
    let third_runtime = runtime(boundary, store.clone(), supervisor);
    let error = run(&third_runtime, "run-abort-unknown").unwrap_err();
    assert!(error.requires_process_exit());
    assert_eq!(
        store
            .lock()
            .unwrap()
            .states
            .get(&ticket_ref_for_identity(&identity(3), "run-abort-unknown")),
        Some(&RuntimeTicketState::Consumed)
    );
    assert!(!store
        .lock()
        .unwrap()
        .events
        .iter()
        .any(|event| event.starts_with("burn:")));
}

#[test]
fn partial_result_latches_failure_and_next_start_burns_the_ticket() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore {
        fail_record_after_partial: true,
        ..FakeLedgerStore::default()
    }));
    let supervisor = FakeSupervisor::ready();
    let first = runtime(boundary.clone(), store.clone(), supervisor.clone());
    run(&first, "run-partial").unwrap();
    supervisor.complete_with(b"partial".to_vec());
    assert_eq!(
        first
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: "run-partial".to_owned()
            })
            .unwrap_err()
            .code(),
        "authority_runtime_integrity_failed"
    );
    let AuthorityRuntimeReply::Status(status) =
        first.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert!(status.global_failure);
    drop(first);

    store.lock().unwrap().fail_record_after_partial = false;
    let second = runtime(boundary, store, FakeSupervisor::ready());
    let AuthorityRuntimeReply::Status(status) =
        second.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert_eq!(status.startup_burned_tickets, 1);
    assert!(!status.global_failure);
}

#[test]
fn protected_manifest_or_other_identity_drift_latches_global_failure() {
    let original = identity(1);
    let boundary = FakeBoundary::new(original.clone());
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let runtime = runtime(boundary.clone(), store, FakeSupervisor::ready());

    let drifted = AuthorityRuntimeIdentity::new(
        *original.authority_generation_digest(),
        *original.signer_key_id(),
        [99; 32],
        *original.installed_layout_digest(),
        *original.service_binary_digest(),
    )
    .unwrap();
    boundary.set_identity(drifted);
    let AuthorityRuntimeReply::Status(status) =
        runtime.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert!(status.global_failure);
    assert_eq!(status.blockers, vec![BLOCKER_RUNTIME_INTEGRITY]);
    assert_eq!(
        run(&runtime, "run-after-drift").unwrap_err().code(),
        "authority_runtime_integrity_failed"
    );
}

#[test]
fn dependency_details_are_never_projected_to_runtime_responses() {
    let boundary = FakeBoundary::new(identity(1));
    boundary.fail_with("C:\\private\\secret.key:rollback-marker-123");
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let runtime = runtime(boundary, store, FakeSupervisor::ready());
    let AuthorityRuntimeReply::Status(status) =
        runtime.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    let projected = format!("{status:?}");
    assert_eq!(status.blockers, vec![BLOCKER_RUNTIME_STARTUP]);
    assert!(!projected.contains("private"));
    assert!(!projected.contains("rollback-marker-123"));
}

#[test]
fn invalid_ids_and_nonactive_terminal_states_fail_closed() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let runtime = runtime(boundary, store, FakeSupervisor::ready());
    for invalid in ["", " bad", "bad/path", &"x".repeat(129)] {
        assert_eq!(
            run(&runtime, invalid).unwrap_err().code(),
            "authority_request_id_invalid"
        );
    }
    assert_eq!(
        runtime
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: "missing".to_owned()
            })
            .unwrap_err()
            .code(),
        "authority_request_not_found"
    );
}

#[test]
fn duplicate_ticket_is_rejected_before_any_new_preparation() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let runtime = runtime(boundary, store, supervisor.clone());
    run(&runtime, "run-prepare-once").unwrap();
    supervisor.terminate_with(RuntimeTerminalKind::Failed);
    runtime
        .handle(AuthorityRuntimeCommand::GetResult {
            request_id: "run-prepare-once".to_owned(),
        })
        .unwrap();
    assert_eq!(supervisor.shared.lock().unwrap().prepares, 1);
    assert_eq!(
        run(&runtime, "run-prepare-once").unwrap_err().code(),
        "authority_request_duplicate"
    );
    let state = supervisor.shared.lock().unwrap();
    assert_eq!(state.prepares, 1);
    assert_eq!(state.aborts, 0);
}

#[test]
fn every_failure_after_prepare_invokes_supervised_cleanup() {
    for (index, stage) in ["issue", "consume", "armed"].into_iter().enumerate() {
        let boundary = FakeBoundary::new(identity(index as u8 + 1));
        let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
        match stage {
            "issue" => store.lock().unwrap().fail_issue = true,
            "consume" => store.lock().unwrap().fail_consume = true,
            "armed" => store.lock().unwrap().fail_armed = true,
            _ => unreachable!(),
        }
        let supervisor = FakeSupervisor::ready();
        let runtime = runtime(boundary, store, supervisor.clone());
        let error = run(&runtime, &format!("run-{stage}-failure")).unwrap_err();
        assert!(error.requires_process_exit());
        let state = supervisor.shared.lock().unwrap();
        assert_eq!(state.prepares, 1, "stage={stage}");
        assert_eq!(state.aborts, 1, "stage={stage}");
    }
}

#[test]
fn invalid_prepared_receipt_contains_orphans_before_global_failure() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let runtime = runtime(boundary, store.clone(), supervisor.clone());
    {
        let mut state = supervisor.shared.lock().unwrap();
        state.containments = 0;
        state.prepared_identity_override = Some([0xA1; 32]);
    }

    let error = run(&runtime, "run-invalid-prepared-receipt").unwrap_err();

    assert_eq!(error.code(), "authority_runtime_integrity_failed");
    assert!(error.requires_process_exit());
    let AuthorityRuntimeReply::Status(status) =
        runtime.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert!(status.global_failure);
    assert_eq!(status.blockers, vec![BLOCKER_RUNTIME_INTEGRITY]);
    let state = supervisor.shared.lock().unwrap();
    assert_eq!(state.prepares, 1);
    assert_eq!(state.containments, 1);
    drop(state);
    let ledger = store.lock().unwrap();
    assert!(ledger.states.is_empty());
    assert!(ledger.events.is_empty());
}

#[test]
fn invalid_prepared_policy_snapshot_contains_orphans_before_global_failure() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let runtime = runtime(boundary, store.clone(), supervisor.clone());
    {
        let mut state = supervisor.shared.lock().unwrap();
        state.containments = 0;
        state.tamper_policy_snapshot = true;
    }

    let error = run(&runtime, "run-invalid-prepared-policy").unwrap_err();

    assert_eq!(error.code(), "authority_runtime_integrity_failed");
    assert!(error.requires_process_exit());
    let AuthorityRuntimeReply::Status(status) =
        runtime.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert!(status.global_failure);
    assert_eq!(status.blockers, vec![BLOCKER_RUNTIME_INTEGRITY]);
    let state = supervisor.shared.lock().unwrap();
    assert_eq!(state.prepares, 1);
    assert_eq!(state.containments, 1);
    drop(state);
    let ledger = store.lock().unwrap();
    assert!(ledger.states.is_empty());
    assert!(ledger.events.is_empty());
}

#[test]
fn restart_contains_orphans_before_rejecting_a_tampered_policy_snapshot() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let first_supervisor = FakeSupervisor::ready();
    let first = runtime(boundary.clone(), store.clone(), first_supervisor);
    run(&first, "run-policy-tamper").unwrap();
    drop(first);
    let ticket = ticket_ref("run-policy-tamper");
    store
        .lock()
        .unwrap()
        .policy_snapshots
        .get_mut(&ticket)
        .unwrap()[20] ^= 0x01;

    let recovery_supervisor = FakeSupervisor::ready();
    let second = runtime(boundary, store, recovery_supervisor.clone());
    let AuthorityRuntimeReply::Status(status) =
        second.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert!(status.global_failure);
    assert_eq!(recovery_supervisor.shared.lock().unwrap().containments, 1);
    assert_eq!(recovery_supervisor.shared.lock().unwrap().recoveries, 0);
}
