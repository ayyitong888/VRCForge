use super::*;
use crate::primitive_basis_protected_evidence_bundle::{
    ProtectedBundleSigningDigest, ProtectedEvidenceBundleError, ReopenedBinaryLedgerReadback,
};
use std::{
    collections::BTreeMap,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc, Barrier,
    },
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
    verified_results: BTreeMap<RuntimeTicketRef, DurableVerifiedResult>,
    results: BTreeMap<RuntimeTicketRef, Vec<u8>>,
    pending_projections: BTreeMap<RuntimeTicketRef, (Vec<u8>, [u8; 32])>,
    projections: BTreeMap<RuntimeTicketRef, (Vec<u8>, [u8; 32])>,
    projection_receipts: BTreeMap<RuntimeTicketRef, DurableProjectionCommitReceipt>,
    terminal: BTreeMap<RuntimeTicketRef, RuntimeTerminalKind>,
    recovered_proof_digests: BTreeMap<RuntimeTicketRef, [u8; 32]>,
    events: Vec<String>,
    partial_result: bool,
    fail_record_after_partial: bool,
    fail_verified_pending_after_commit: bool,
    fail_result_after_commit: bool,
    fail_reopen_after_readback: bool,
    fail_projection_pending_after_commit: bool,
    fail_projection_after_commit: bool,
    omit_projection_receipt: bool,
    tamper_projection_receipt: bool,
    tamper_projection_receipt_identity: bool,
    open_error: Option<String>,
    identity_error: Option<String>,
    fail_issue: bool,
    fail_consume: bool,
    fail_armed: bool,
    sign_count: Arc<AtomicUsize>,
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

    fn record_verified_result_pending_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        result: &ServiceOwnedVerifiedRuntimeResult,
    ) -> Result<(), RuntimeDependencyError> {
        let mut store = self.shared.lock().unwrap();
        if store.run_bindings.get(ticket) != Some(run_binding_digest) {
            return Err(RuntimeDependencyError::new(
                "verified_result_run_binding_mismatch",
            ));
        }
        let already_pending = match store.states.get(ticket) {
            Some(RuntimeTicketState::Consumed) => false,
            Some(RuntimeTicketState::ResultPendingProjection) => true,
            _ => {
                return Err(RuntimeDependencyError::new(
                    "verified_result_transition_invalid",
                ))
            }
        };
        let pending = RuntimePendingVerifiedResult::new(
            ticket.clone(),
            *run_binding_digest,
            result.clone(),
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
            decode_digest(
                store
                    .recovery_bundle_digests
                    .get(ticket)
                    .ok_or_else(|| RuntimeDependencyError::new("missing_recovery_bundle"))?,
            )
            .ok_or_else(|| RuntimeDependencyError::new("invalid_recovery_bundle"))?,
            store
                .armed_receipts
                .get(ticket)
                .cloned()
                .ok_or_else(|| RuntimeDependencyError::new("missing_armed_receipt"))?,
            false,
            None,
        )?;
        let record = pending.durable_record()?;
        if already_pending {
            return if store.verified_results.get(ticket) == Some(&record) {
                Ok(())
            } else {
                Err(RuntimeDependencyError::new(
                    "verified_result_transition_invalid",
                ))
            };
        }
        store.verified_results.insert(ticket.clone(), record);
        store
            .states
            .insert(ticket.clone(), RuntimeTicketState::ResultPendingProjection);
        if store.fail_verified_pending_after_commit {
            store.fail_verified_pending_after_commit = false;
            return Err(RuntimeDependencyError::new(
                "crash_after_verified_result_pending_commit",
            ));
        }
        Ok(())
    }

    fn pending_verified_results(
        &mut self,
    ) -> Result<Vec<RuntimePendingVerifiedResult>, RuntimeDependencyError> {
        let store = self.shared.lock().unwrap();
        if store.partial_result {
            return Err(RuntimeDependencyError::new(
                "partial_result_recovery_required",
            ));
        }
        store
            .states
            .iter()
            .filter(|(_, state)| **state == RuntimeTicketState::ResultPendingProjection)
            .map(|(ticket, _)| {
                let record = store
                    .verified_results
                    .get(ticket)
                    .ok_or_else(|| RuntimeDependencyError::new("verified_result_record_missing"))?;
                let projection = store
                    .pending_projections
                    .get(ticket)
                    .cloned()
                    .map(|(bytes, digest)| {
                        VerifiedAuthorityResultProjection::from_immutable_ledger_readback(
                            bytes, digest,
                        )
                        .map_err(|error| RuntimeDependencyError::new(error.code()))
                    })
                    .transpose()?;
                RuntimePendingVerifiedResult::from_durable(
                    ticket.clone(),
                    record,
                    store
                        .prepared_receipts
                        .get(ticket)
                        .ok_or_else(|| RuntimeDependencyError::new("missing_prepared_receipt"))?,
                    store
                        .policy_snapshots
                        .get(ticket)
                        .ok_or_else(|| RuntimeDependencyError::new("missing_policy_snapshot"))?,
                    &decode_digest(
                        store.recovery_bundle_digests.get(ticket).ok_or_else(|| {
                            RuntimeDependencyError::new("missing_recovery_bundle")
                        })?,
                    )
                    .ok_or_else(|| RuntimeDependencyError::new("invalid_recovery_bundle"))?,
                    store
                        .armed_receipts
                        .get(ticket)
                        .ok_or_else(|| RuntimeDependencyError::new("missing_armed_receipt"))?,
                    store.results.contains_key(ticket),
                    projection,
                )
            })
            .collect()
    }

    fn record_result_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        result_bytes: &[u8],
    ) -> Result<(), RuntimeDependencyError> {
        let mut store = self.shared.lock().unwrap();
        if store.states.get(ticket) != Some(&RuntimeTicketState::ResultPendingProjection)
            || store.run_bindings.get(ticket) != Some(run_binding_digest)
        {
            return Err(RuntimeDependencyError::new("transition"));
        }
        if let Some(stored) = store.results.get(ticket) {
            return if stored.as_slice() == result_bytes {
                Ok(())
            } else {
                Err(RuntimeDependencyError::new("result_replacement_rejected"))
            };
        }
        let verified = store
            .verified_results
            .get(ticket)
            .ok_or_else(|| RuntimeDependencyError::new("verified_result_record_missing"))?;
        if verified.finalization_bytes() != result_bytes {
            return Err(RuntimeDependencyError::new("result_replacement_rejected"));
        }
        if store.fail_record_after_partial {
            store.partial_result = true;
            return Err(RuntimeDependencyError::new("partial_result_write"));
        }
        store.results.insert(ticket.clone(), result_bytes.to_vec());
        store.events.push(format!("result:{}", ticket.as_str()));
        if store.fail_result_after_commit {
            store.fail_result_after_commit = false;
            return Err(RuntimeDependencyError::new("crash_after_result_commit"));
        }
        Ok(())
    }

    fn result_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<Vec<u8>>, RuntimeDependencyError> {
        Ok(self.shared.lock().unwrap().results.get(ticket).cloned())
    }

    fn reopen_result_commit_terminal(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        result: &ServiceOwnedVerifiedRuntimeResult,
    ) -> Result<DurableBinaryLedgerTerminal, RuntimeDependencyError> {
        let mut store = self.shared.lock().unwrap();
        if store.states.get(ticket) != Some(&RuntimeTicketState::ResultPendingProjection)
            || store.run_bindings.get(ticket) != Some(run_binding_digest)
            || store.results.get(ticket).map(Vec::as_slice) != Some(result.finalization_bytes())
        {
            return Err(RuntimeDependencyError::new(
                "fake_binary_terminal_readback_invalid",
            ));
        }
        let terminal_sequence = 4;
        let terminal_frame_digest = [0x41; 32];
        let anchor_record_digest = [0x42; 32];
        let readback = ReopenedBinaryLedgerReadback::from_held_and_reopened_ledger(
            [0x31; 32],
            [0x32; 32],
            [0x33; 32],
            [0x34; 32],
            4096,
            512,
            terminal_sequence + 1,
            0,
            terminal_frame_digest,
            anchor_record_digest,
            terminal_sequence,
            terminal_frame_digest,
            *result.ticket_digest(),
        )
        .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        let terminal = DurableBinaryLedgerTerminal::from_reopened_result_commit(
            1,
            [0x21; 32],
            terminal_sequence - 1,
            terminal_sequence,
            [0x40; 32],
            terminal_frame_digest,
            *result.ticket_digest(),
            *result.finalization_digest(),
            terminal_sequence,
            terminal_frame_digest,
            *result.ticket_digest(),
            *result.run_binding_digest(),
            *result.prepared_receipt_digest(),
            *result.armed_receipt_digest(),
            *result.policy_snapshot_digest(),
            *result.recovery_bundle_digest(),
            *result.origin_envelope_digest(),
            *result.cleanup_digest(),
            anchor_record_digest,
            "2026-07-27T00:00:00.000000Z",
            "2026-07-27T00:00:01.000000Z",
            "2026-07-27T00:00:02.000000Z",
            readback,
        )
        .map_err(|error| RuntimeDependencyError::new(error.code()))?;
        if store.fail_reopen_after_readback {
            store.fail_reopen_after_readback = false;
            return Err(RuntimeDependencyError::new(
                "crash_after_binary_terminal_readback",
            ));
        }
        Ok(terminal)
    }

    fn record_projection_pending_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        projection: &VerifiedAuthorityResultProjection,
    ) -> Result<(), RuntimeDependencyError> {
        let mut store = self.shared.lock().unwrap();
        if store.states.get(ticket) != Some(&RuntimeTicketState::ResultPendingProjection)
            || store.run_bindings.get(ticket) != Some(run_binding_digest)
            || !store.results.contains_key(ticket)
        {
            return Err(RuntimeDependencyError::new(
                "fake_projection_transition_invalid",
            ));
        }
        let exact = (projection.canonical_bytes().to_vec(), *projection.sha256());
        match store.pending_projections.get(ticket) {
            Some(stored) if stored == &exact => return Ok(()),
            Some(_) => {
                return Err(RuntimeDependencyError::new(
                    "fake_projection_replacement_rejected",
                ))
            }
            None => {
                store.pending_projections.insert(ticket.clone(), exact);
            }
        }
        if store.fail_projection_pending_after_commit {
            store.fail_projection_pending_after_commit = false;
            return Err(RuntimeDependencyError::new(
                "crash_after_projection_pending_commit",
            ));
        }
        Ok(())
    }

    fn commit_projection_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        projection_digest: &[u8; 32],
    ) -> Result<(), RuntimeDependencyError> {
        let mut store = self.shared.lock().unwrap();
        if store.states.get(ticket) == Some(&RuntimeTicketState::Result)
            && store
                .projections
                .get(ticket)
                .is_some_and(|(_, digest)| digest == projection_digest)
        {
            return Ok(());
        }
        if store.states.get(ticket) != Some(&RuntimeTicketState::ResultPendingProjection)
            || store.run_bindings.get(ticket) != Some(run_binding_digest)
        {
            return Err(RuntimeDependencyError::new(
                "fake_projection_commit_transition_invalid",
            ));
        }
        let projection = store
            .pending_projections
            .remove(ticket)
            .filter(|(_, digest)| digest == projection_digest)
            .ok_or_else(|| RuntimeDependencyError::new("fake_projection_commit_mismatch"))?;
        let identity = store
            .identity
            .as_ref()
            .ok_or_else(|| RuntimeDependencyError::new("fake_ledger_identity_missing"))?;
        let receipt = DurableProjectionCommitReceipt::for_runtime_test(
            *identity.authority_generation_digest(),
            *identity.signer_key_id(),
            ticket.digest(),
            *run_binding_digest,
            &projection.0,
        );
        store.projections.insert(ticket.clone(), projection);
        store.projection_receipts.insert(ticket.clone(), receipt);
        store
            .states
            .insert(ticket.clone(), RuntimeTicketState::Result);
        store.events.push(format!("projection:{}", ticket.as_str()));
        if store.fail_projection_after_commit {
            store.fail_projection_after_commit = false;
            return Err(RuntimeDependencyError::new("crash_after_projection_commit"));
        }
        Ok(())
    }

    fn projection_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<VerifiedAuthorityResultProjection>, RuntimeDependencyError> {
        self.shared
            .lock()
            .unwrap()
            .projections
            .get(ticket)
            .cloned()
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
    ) -> Result<Option<DurableProjectionCommitReceipt>, RuntimeDependencyError> {
        let store = self.shared.lock().unwrap();
        if store.omit_projection_receipt {
            return Ok(None);
        }
        let identity = store
            .identity
            .as_ref()
            .ok_or_else(|| RuntimeDependencyError::new("fake_ledger_identity_missing"))?;
        let ledger_identity_digest = expected_ledger_identity_digest(identity)?;
        let receipt = store.projection_receipts.get(ticket).cloned();
        if receipt.as_ref().is_some_and(|receipt| {
            !receipt.verifies_for(
                identity.authority_generation_digest(),
                &ledger_identity_digest,
                &ticket.digest(),
                run_binding_digest,
                projection.canonical_bytes(),
            )
        }) {
            return Err(RuntimeDependencyError::new(
                "fake_projection_receipt_mismatch",
            ));
        }
        if store.tamper_projection_receipt {
            return Ok(Some(DurableProjectionCommitReceipt::for_runtime_test(
                *identity.authority_generation_digest(),
                *identity.signer_key_id(),
                ticket.digest(),
                *run_binding_digest,
                b"tampered-projection",
            )));
        }
        if store.tamper_projection_receipt_identity {
            return Ok(Some(DurableProjectionCommitReceipt::for_runtime_test(
                [0xee; 32],
                *identity.signer_key_id(),
                ticket.digest(),
                *run_binding_digest,
                projection.canonical_bytes(),
            )));
        }
        Ok(receipt)
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

    fn burn_recovered_with_reason(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        reason: RuntimeTerminalKind,
        proof: &RecoveredBurnProof,
    ) -> Result<(), RuntimeDependencyError> {
        if !matches!(
            reason,
            RuntimeTerminalKind::Cancelled | RuntimeTerminalKind::TimedOut
        ) || proof.recovery_proof_digest().iter().all(|byte| *byte == 0)
        {
            return Err(RuntimeDependencyError::new(
                "fake_recovered_burn_proof_invalid",
            ));
        }
        self.burn(ticket, run_binding_digest, reason)?;
        self.shared
            .lock()
            .unwrap()
            .recovered_proof_digests
            .insert(ticket.clone(), *proof.recovery_proof_digest());
        Ok(())
    }

    fn terminal_reason(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<RuntimeTerminalKind>, RuntimeDependencyError> {
        Ok(self.shared.lock().unwrap().terminal.get(ticket).copied())
    }
}

struct FakeProtectedSigner {
    key_id: [u8; 32],
    sign_count: Arc<AtomicUsize>,
}

impl ProtectedEvidenceBundleSigner for FakeProtectedSigner {
    fn signer_key_id(&self) -> [u8; 32] {
        self.key_id
    }

    fn sign_protected_bundle(
        &mut self,
        _digest: ProtectedBundleSigningDigest,
    ) -> Result<[u8; 64], ProtectedEvidenceBundleError> {
        self.sign_count.fetch_add(1, Ordering::SeqCst);
        let mut signature = [0u8; 64];
        signature[31] = 1;
        signature[63] = 1;
        Ok(signature)
    }
}

fn protected_projection_producer(
    identity: &AuthorityRuntimeIdentity,
    sign_count: Arc<AtomicUsize>,
) -> ProtectedEvidenceBundleProducer<FakeProtectedSigner> {
    ProtectedEvidenceBundleProducer::new(
        *identity.authority_generation_digest(),
        *identity.protected_manifest_digest(),
        *identity.installed_layout_digest(),
        *identity.service_binary_digest(),
        FakeProtectedSigner {
            key_id: *identity.signer_key_id(),
            sign_count,
        },
    )
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
    start_burned: bool,
    start_in_background: bool,
    cancel_error: bool,
    cancel_acknowledgement: SupervisorCancelAcknowledgement,
    terminal_on_cancel: Option<RuntimeTerminalKind>,
    abort_error: bool,
    recovery_error: bool,
    recovered_completed_result: Option<Vec<u8>>,
    recovered_burn: Option<RecoveredBurnSpec>,
    aborts: usize,
    recoveries: usize,
    starts: usize,
    prepares: usize,
    containments: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct RecoveredBurnSpec {
    reason: BurnReason,
    mutation: RecoveredBurnMutation,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RecoveredBurnMutation {
    None,
    MissingNormalBinding,
    AuthorityDigest,
    TicketDigest,
    RunBindingDigest,
    ArmedReceiptDigest,
    ArmedPresence,
    AdmissionPreparedDigest,
    AdmissionArmedDigest,
    AdmissionPolicyDigest,
    AdmissionRecoveryDigest,
    CleanupDigest,
    ZeroStageJournalHead,
    ZeroTerminationIntent,
    ZeroTerminalDigest,
}

fn test_admission_digests(context: &RuntimeRunContext) -> ([u8; 32], [u8; 32], [u8; 32], [u8; 32]) {
    let prepared = context.prepared_receipt().digest();
    let armed: [u8; 32] = Sha256::digest(
        context
            .armed_receipt()
            .expect("active test run is armed")
            .encode(),
    )
    .into();
    let policy: [u8; 32] = Sha256::digest(context.canonical_policy_snapshot()).into();
    let recovery = compute_recovery_bundle_digest(
        context.ticket().as_str(),
        &hex_encode(context.run_binding_digest()),
        &context.prepared_receipt().encode(),
        context.canonical_policy_snapshot(),
    )
    .ok()
    .and_then(|value| decode_digest(&value))
    .expect("test recovery digest");
    (prepared, armed, policy, recovery)
}

fn native_completed_for_test(
    context: &RuntimeRunContext,
    result_bytes: Vec<u8>,
) -> NativeCompletedRunProof {
    let result_bytes = protected_finalization_for_test(context, result_bytes);
    native_completed_exact_for_test(context, result_bytes)
}

fn native_completed_exact_for_test(
    context: &RuntimeRunContext,
    result_bytes: Vec<u8>,
) -> NativeCompletedRunProof {
    let terminal = CompletedRunProof::for_runtime_test(
        *context.authority_identity_digest(),
        context.ticket().digest(),
        *context.run_binding_digest(),
        result_bytes,
    );
    native_completed_from_terminal_for_test(context, terminal)
}

fn protected_finalization_for_test(context: &RuntimeRunContext, payload_bytes: Vec<u8>) -> Vec<u8> {
    let policy = prepared_protected_evidence_policy_readback(context.canonical_policy_snapshot())
        .expect("runtime test policy snapshot");
    let source = policy.source();
    let payload: serde_json::Value =
        serde_json::from_slice(&payload_bytes).expect("runtime test payload is JSON");
    let fixture_project_input_digest: [u8; 32] = Sha256::digest(&payload_bytes).into();
    let mut project = Sha256::new();
    project.update(b"vrcforge-runtime-test-project-binding-v1\0");
    project.update(&payload_bytes);
    let project_binding_digest: [u8; 32] = project.finalize().into();
    serde_json::to_vec(&serde_json::json!({
        "attestation": {
            "backendExecutableDigest": hex_encode(source.package_digest(3).unwrap()),
            "desktopExecutableDigest": hex_encode(source.package_digest(2).unwrap()),
            "fixtureDescriptorDigest": hex_encode(source.fixture_descriptor_digest()),
            "fixtureDigest": hex_encode(source.fixture_digest()),
            "fixtureProjectInputDigest": hex_encode(&fixture_project_input_digest),
            "fixtureSetDescriptorDigest": hex_encode(source.fixture_set_descriptor_digest()),
            "projectBindingDigest": hex_encode(&project_binding_digest),
            "runId": "runtime-test",
            "runnerDigest": hex_encode(source.package_digest(5).unwrap()),
            "runtimeBindingDigest": hex_encode(source.package_digest(15).unwrap()),
            "unityEditorDigest": hex_encode(source.package_digest(9).unwrap()),
            "unityPackageDigest": hex_encode(source.package_digest(6).unwrap())
        },
        "payload": payload,
        "schema": "vrcforge.primitive_basis_live_finalization.v4"
    }))
    .unwrap()
}

fn native_completed_from_terminal_for_test(
    context: &RuntimeRunContext,
    terminal: CompletedRunProof,
) -> NativeCompletedRunProof {
    let cleanup = *terminal.cleanup_receipt_digest();
    let policy = prepared_protected_evidence_policy_readback(context.canonical_policy_snapshot())
        .expect("runtime test policy snapshot");
    let origin_ticket = serde_json::json!({
        "issuedAt": "2026-07-27T00:00:00.000000Z",
        "policyId": policy.source().policy_id(),
        "runId": "runtime-test"
    });
    let origin_ticket_digest: [u8; 32] =
        Sha256::digest(serde_json::to_vec(&origin_ticket).unwrap()).into();
    let origin = serde_json::to_vec(&serde_json::json!({
        "authorityTicketDigest": hex_encode(terminal.ticket_digest()),
        "cleanupDigest": hex_encode(&cleanup),
        "schema": "vrcforge.primitive_basis_live_origin.v2",
        "ticket": origin_ticket,
        "ticketDigest": hex_encode(&origin_ticket_digest),
    }))
    .unwrap();
    let (prepared, armed, policy, recovery) = test_admission_digests(context);
    NativeCompletedRunProof::for_runtime_test(terminal, prepared, armed, policy, recovery, origin)
        .unwrap()
}

fn native_burned_for_test(context: &RuntimeRunContext, reason: BurnReason) -> NativeBurnedRunProof {
    let terminal = BurnedRunProof::for_runtime_test(
        *context.authority_identity_digest(),
        context.ticket().digest(),
        *context.run_binding_digest(),
        reason,
    );
    NativeBurnedRunProof::for_runtime_test(terminal, Some(test_admission_digests(context))).unwrap()
}

fn native_recovered_burned_for_test(
    context: &RuntimeRecoveryContext,
    spec: RecoveredBurnSpec,
) -> Result<NativeBurnedRunProof, RuntimeDependencyError> {
    let mut authority_identity_digest = *context.authority_identity_digest();
    let mut ticket_digest = context.ticket().digest();
    let mut run_binding_digest = *context.run_binding_digest();
    match spec.mutation {
        RecoveredBurnMutation::AuthorityDigest => authority_identity_digest[0] ^= 0xff,
        RecoveredBurnMutation::TicketDigest => ticket_digest[0] ^= 0xff,
        RecoveredBurnMutation::RunBindingDigest => run_binding_digest[0] ^= 0xff,
        _ => {}
    }
    let terminal = BurnedRunProof::for_runtime_test(
        authority_identity_digest,
        ticket_digest,
        run_binding_digest,
        spec.reason,
    );
    let run_context = run_context_from_recovery(context);
    let mut admission = context
        .armed_receipt()
        .map(|_| test_admission_digests(&run_context));
    let mut armed_digest: Option<[u8; 32]> = context
        .armed_receipt()
        .map(|receipt| Sha256::digest(receipt.encode()).into());
    match spec.mutation {
        RecoveredBurnMutation::ArmedReceiptDigest => {
            if let Some(digest) = armed_digest.as_mut() {
                digest[0] ^= 0xff;
            }
        }
        RecoveredBurnMutation::ArmedPresence => match armed_digest {
            Some(_) => {
                admission = None;
                armed_digest = None;
            }
            None => {
                admission = Some(([0x71; 32], [0x72; 32], [0x73; 32], [0x74; 32]));
                armed_digest = Some([0x72; 32]);
            }
        },
        RecoveredBurnMutation::AdmissionPreparedDigest => {
            if let Some((prepared, _, _, _)) = admission.as_mut() {
                prepared[0] ^= 0xff;
            }
        }
        RecoveredBurnMutation::AdmissionArmedDigest => {
            if let Some((_, armed, _, _)) = admission.as_mut() {
                armed[0] ^= 0xff;
            }
        }
        RecoveredBurnMutation::AdmissionPolicyDigest => {
            if let Some((_, _, policy, _)) = admission.as_mut() {
                policy[0] ^= 0xff;
            }
        }
        RecoveredBurnMutation::AdmissionRecoveryDigest => {
            if let Some((_, _, _, recovery)) = admission.as_mut() {
                recovery[0] ^= 0xff;
            }
        }
        _ => {}
    }
    if spec.mutation == RecoveredBurnMutation::MissingNormalBinding {
        return NativeBurnedRunProof::for_runtime_test(terminal, admission)
            .map_err(|error| RuntimeDependencyError::new(error.code()));
    }

    let mut stage_head = [0x81; 32];
    let mut intent = [0x82; 32];
    let mut terminal_digest = [0x83; 32];
    let mut cleanup = *terminal.cleanup_receipt_digest();
    match spec.mutation {
        RecoveredBurnMutation::CleanupDigest => cleanup[0] ^= 0xff,
        RecoveredBurnMutation::ZeroStageJournalHead => stage_head = [0; 32],
        RecoveredBurnMutation::ZeroTerminationIntent => intent = [0; 32],
        RecoveredBurnMutation::ZeroTerminalDigest => terminal_digest = [0; 32],
        _ => {}
    }
    NativeBurnedRunProof::for_runtime_recovered_test(
        terminal,
        admission,
        (armed_digest, stage_head, intent, terminal_digest, cleanup),
    )
    .map_err(|error| RuntimeDependencyError::new(error.code()))
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
                start_burned: false,
                start_in_background: false,
                cancel_error: false,
                cancel_acknowledgement: SupervisorCancelAcknowledgement::Recorded(
                    RuntimeTerminalKind::Cancelled,
                ),
                terminal_on_cancel: None,
                abort_error: false,
                recovery_error: false,
                recovered_completed_result: None,
                recovered_burn: None,
                aborts: 0,
                recoveries: 0,
                starts: 0,
                prepares: 0,
                containments: 0,
            })),
        }
    }

    fn complete_with(&self, bytes: Vec<u8>) -> Vec<u8> {
        let context = self.shared.lock().unwrap().active.clone().unwrap();
        let exact = protected_finalization_for_test(&context, bytes);
        let completed = native_completed_exact_for_test(&context, exact.clone());
        self.shared.lock().unwrap().poll =
            SupervisorPoll::Terminal(ValidatedNativeTerminalRun::Completed(completed));
        exact
    }

    fn terminate_with(&self, kind: RuntimeTerminalKind) {
        let context = self.shared.lock().unwrap().active.clone().unwrap();
        let reason = match kind {
            RuntimeTerminalKind::Cancelled => BurnReason::Cancelled,
            RuntimeTerminalKind::TimedOut => BurnReason::TimedOut,
            RuntimeTerminalKind::Failed => BurnReason::Failed,
            RuntimeTerminalKind::RestartRecovery => panic!("invalid supervisor terminal"),
        };
        let terminated = native_burned_for_test(&context, reason);
        self.shared.lock().unwrap().poll =
            SupervisorPoll::Terminal(ValidatedNativeTerminalRun::Burned(terminated));
    }

    fn fail_poll(&self) {
        self.shared.lock().unwrap().poll_error = true;
    }

    fn fail_start(&self) {
        self.shared.lock().unwrap().start_error = true;
    }

    fn burn_during_start(&self) {
        self.shared.lock().unwrap().start_burned = true;
    }

    fn start_in_background(&self) {
        self.shared.lock().unwrap().start_in_background = true;
    }

    fn arm_background_start(&self) {
        let mut state = self.shared.lock().unwrap();
        let context = state
            .active
            .clone()
            .expect("background start has an active context");
        let armed = ArmedRecoveryReceipt::for_runtime_test(
            context.prepared_receipt(),
            *context.run_binding_digest(),
        );
        state.poll = SupervisorPoll::Armed(armed);
    }

    fn fail_abort(&self) {
        self.shared.lock().unwrap().abort_error = true;
    }

    fn set_cancel_acknowledgement(&self, acknowledgement: SupervisorCancelAcknowledgement) {
        self.shared.lock().unwrap().cancel_acknowledgement = acknowledgement;
    }

    fn complete_cancel_race_with(&self, kind: RuntimeTerminalKind) {
        let mut state = self.shared.lock().unwrap();
        state.cancel_acknowledgement = SupervisorCancelAcknowledgement::AlreadyTerminal;
        state.terminal_on_cancel = Some(kind);
    }

    fn recover_completed_with(&self, result: Vec<u8>) {
        self.shared.lock().unwrap().recovered_completed_result = Some(result);
    }

    fn recover_burned_with(&self, reason: BurnReason, mutation: RecoveredBurnMutation) {
        self.shared.lock().unwrap().recovered_burn = Some(RecoveredBurnSpec { reason, mutation });
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
        Ok(PreparedRun::for_runtime_identity_test(
            state
                .prepared_identity_override
                .unwrap_or_else(|| identity.binding_digest()),
            *identity.authority_generation_digest(),
            *identity.protected_manifest_digest(),
            *identity.installed_layout_digest(),
            *identity.service_binary_digest(),
            expected_ledger_identity_digest(identity)?,
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
    ) -> Result<SupervisorStart, RuntimeDependencyError> {
        let mut state = self.shared.lock().unwrap();
        if state.start_error {
            return Err(RuntimeDependencyError::new("start_state_unknown"));
        }
        if state.start_burned {
            return Ok(SupervisorStart::Burned(BurnedRunProof::for_runtime_test(
                *context.authority_identity_digest(),
                context.ticket().digest(),
                *context.run_binding_digest(),
                BurnReason::Failed,
            )));
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
        if state.start_in_background {
            state.active = Some(context.clone());
            state.poll = SupervisorPoll::Starting;
            state.starts += 1;
            return Ok(SupervisorStart::Starting);
        }
        let armed = ArmedRecoveryReceipt::for_runtime_test(
            prepared.receipt(),
            *context.run_binding_digest(),
        );
        state.active = Some(context.clone().with_armed_receipt(armed.clone()));
        state.starts += 1;
        Ok(SupervisorStart::Armed(armed))
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
        match &poll {
            SupervisorPoll::Armed(receipt) => {
                state.active = Some(context.clone().with_armed_receipt(receipt.clone()));
                state.poll = SupervisorPoll::Running;
            }
            SupervisorPoll::Terminal(_) => state.active = None,
            SupervisorPoll::Starting | SupervisorPoll::Running => {}
        }
        Ok(poll)
    }

    fn cancel(
        &mut self,
        context: &RuntimeRunContext,
    ) -> Result<SupervisorCancelAcknowledgement, RuntimeDependencyError> {
        let mut state = self.shared.lock().unwrap();
        if state.cancel_error {
            return Err(RuntimeDependencyError::new("cancel_failed"));
        }
        if state.active.as_ref() != Some(context) {
            return Err(RuntimeDependencyError::new("wrong_ticket"));
        }
        if let Some(kind) = state.terminal_on_cancel.take() {
            let reason = match kind {
                RuntimeTerminalKind::Cancelled => BurnReason::Cancelled,
                RuntimeTerminalKind::TimedOut => BurnReason::TimedOut,
                RuntimeTerminalKind::Failed => BurnReason::Failed,
                RuntimeTerminalKind::RestartRecovery => {
                    return Err(RuntimeDependencyError::new("cancel_terminal_invalid"));
                }
            };
            state.poll = SupervisorPoll::Terminal(ValidatedNativeTerminalRun::Burned(
                native_burned_for_test(context, reason),
            ));
        }
        Ok(state.cancel_acknowledgement)
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
    ) -> Result<SupervisorRecovery, RuntimeDependencyError> {
        let mut state = self.shared.lock().unwrap();
        if state.recovery_error {
            return Err(RuntimeDependencyError::new("recovery_state_unknown"));
        }
        state.recoveries += 1;
        if let Some(result) = state.recovered_completed_result.clone() {
            let run_context = run_context_from_recovery(context);
            return Ok(SupervisorRecovery::Completed(native_completed_for_test(
                &run_context,
                result,
            )));
        }
        if let Some(spec) = state.recovered_burn {
            return native_recovered_burned_for_test(context, spec).map(SupervisorRecovery::Burned);
        }
        let terminal = BurnedRunProof::for_runtime_test(
            *context.authority_identity_digest(),
            context.ticket().digest(),
            *context.run_binding_digest(),
            BurnReason::RestartRecovery,
        );
        let run_context = run_context_from_recovery(context);
        let admission = context
            .armed_receipt()
            .map(|_| test_admission_digests(&run_context));
        NativeBurnedRunProof::for_runtime_test(terminal, admission)
            .map(SupervisorRecovery::Burned)
            .map_err(|error| RuntimeDependencyError::new(error.code()))
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
    let runtime_identity = boundary.shared.lock().unwrap().identity.clone();
    let sign_count = ledger_store.lock().unwrap().sign_count.clone();
    AuthorityRuntime::start_with_projection_producer(
        boundary,
        FakeLedger::new(ledger_store),
        supervisor,
        protected_projection_producer(&runtime_identity, sign_count),
    )
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
    assert!(base_ticket.matches_request(&base, "same-request"));
    assert!(!base_ticket.matches_request(&base, "other-request"));
    let changed_identity = AuthorityRuntimeIdentity::new(
        [0x71; 32],
        *base.signer_key_id(),
        *base.protected_manifest_digest(),
        *base.installed_layout_digest(),
        *base.service_binary_digest(),
    )
    .unwrap();
    assert!(!base_ticket.matches_request(&changed_identity, "same-request"));

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
fn disconnected_projection_producer_is_visible_and_rejects_before_issue() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let runtime = AuthorityRuntime::start(
        boundary,
        FakeLedger::new(store.clone()),
        FakeSupervisor::ready(),
    );
    let AuthorityRuntimeReply::Status(status) =
        runtime.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert!(!status.trusted_boundary_ready);
    assert!(!status.global_failure);
    assert_eq!(status.blockers, vec![BLOCKER_PROJECTION_NOT_CONNECTED]);
    assert_eq!(
        run(&runtime, "run-no-projector").unwrap_err().code(),
        "authority_projection_not_ready"
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
    let exact = supervisor.complete_with(b"{\"n\":1,\"opaque\":\"safe\"}".to_vec());
    let AuthorityRuntimeReply::ResultExact {
        request_id,
        projection,
        receipt,
    } = runtime
        .handle(AuthorityRuntimeCommand::GetResult {
            request_id: "run-1".to_owned(),
        })
        .unwrap()
    else {
        panic!("unexpected result reply");
    };
    assert_eq!(
        store.lock().unwrap().results.get(&ticket_ref("run-1")),
        Some(&exact)
    );
    assert_eq!(request_id, "run-1");
    let runtime_identity = identity(1);
    let ledger_identity_digest = expected_ledger_identity_digest(&runtime_identity).unwrap();
    assert!(receipt.verifies_for(
        runtime_identity.authority_generation_digest(),
        &ledger_identity_digest,
        &ticket_ref("run-1").digest(),
        projection.run_binding_digest(),
        projection.canonical_bytes(),
    ));
    let stored = store
        .lock()
        .unwrap()
        .projections
        .get(&ticket_ref("run-1"))
        .cloned()
        .unwrap();
    let immutable =
        VerifiedAuthorityResultProjection::from_immutable_ledger_readback(stored.0, stored.1)
            .unwrap();
    assert_eq!(projection, immutable);
    assert_eq!(
        run(&runtime, "run-1").unwrap_err().code(),
        "authority_request_duplicate"
    );
    let events = &store.lock().unwrap().events;
    assert_eq!(events.len(), 5);
    assert!(events[0].starts_with("issue:"));
    assert!(events[1].starts_with("consume:"));
    assert!(events[2].starts_with("armed:"));
    assert!(events[3].starts_with("result:"));
    assert!(events[4].starts_with("projection:"));
}

#[test]
fn background_start_is_accepted_before_armed_and_records_the_receipt_once() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    supervisor.start_in_background();
    let runtime = runtime(boundary, store.clone(), supervisor.clone());

    assert_eq!(
        run(&runtime, "run-background").unwrap(),
        AuthorityRuntimeReply::RunStarted {
            request_id: "run-background".to_owned()
        }
    );
    assert_eq!(
        store.lock().unwrap().events,
        vec![
            format!("issue:{}", ticket_ref("run-background").as_str()),
            format!("consume:{}", ticket_ref("run-background").as_str()),
        ]
    );

    let AuthorityRuntimeReply::Status(starting) =
        runtime.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected starting status");
    };
    assert_eq!(
        starting.active_request_id.as_deref(),
        Some("run-background")
    );
    assert_eq!(store.lock().unwrap().events.len(), 2);

    supervisor.arm_background_start();
    let AuthorityRuntimeReply::Status(armed) =
        runtime.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected armed status");
    };
    assert_eq!(armed.active_request_id.as_deref(), Some("run-background"));
    assert_eq!(
        store
            .lock()
            .unwrap()
            .events
            .iter()
            .filter(|event| event.starts_with("armed:"))
            .count(),
        1
    );

    let AuthorityRuntimeReply::Status(running) =
        runtime.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected running status");
    };
    assert_eq!(running.active_request_id.as_deref(), Some("run-background"));
    assert_eq!(
        store
            .lock()
            .unwrap()
            .events
            .iter()
            .filter(|event| event.starts_with("armed:"))
            .count(),
        1
    );
}

#[test]
fn background_start_cannot_report_running_before_a_durable_armed_receipt() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    supervisor.start_in_background();
    let runtime = runtime(boundary, store, supervisor.clone());
    run(&runtime, "run-background-drift").unwrap();
    supervisor.shared.lock().unwrap().poll = SupervisorPoll::Running;

    let AuthorityRuntimeReply::Status(status) =
        runtime.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected status reply");
    };
    assert!(status.global_failure);
    assert_eq!(status.blockers, vec![BLOCKER_RUNTIME_INTEGRITY]);
}

#[test]
fn fully_validated_supervisor_bytes_are_committed_without_reconstruction() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let runtime = runtime(boundary, store.clone(), supervisor.clone());
    run(&runtime, "run-sealed-result").unwrap();

    let payload = b"{\"binary\":\"safe\",\"order\":[3,1,2]}".to_vec();
    let context = supervisor.shared.lock().unwrap().active.clone().unwrap();
    let exact = protected_finalization_for_test(&context, payload);
    let proof = native_completed_exact_for_test(&context, exact.clone());
    supervisor.shared.lock().unwrap().poll =
        SupervisorPoll::Terminal(ValidatedNativeTerminalRun::Completed(proof));

    let AuthorityRuntimeReply::ResultExact { projection, .. } = runtime
        .handle(AuthorityRuntimeCommand::GetResult {
            request_id: "run-sealed-result".to_owned(),
        })
        .unwrap()
    else {
        panic!("unexpected result reply");
    };
    let stored = store
        .lock()
        .unwrap()
        .projections
        .get(&ticket_ref("run-sealed-result"))
        .cloned()
        .unwrap();
    assert_eq!(
        projection,
        VerifiedAuthorityResultProjection::from_immutable_ledger_readback(stored.0, stored.1)
            .unwrap()
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

#[derive(Debug, Clone, Copy)]
enum ProjectionCrashPoint {
    VerifiedResultPending,
    ResultCommitted,
    BinaryTerminalReopened,
    ProjectionPending,
}

fn assert_restart_recovers_projection_crash(point: ProjectionCrashPoint) {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let first = runtime(boundary.clone(), store.clone(), supervisor.clone());
    let request_id = format!("run-projection-crash-{point:?}");
    run(&first, &request_id).unwrap();
    {
        let mut state = store.lock().unwrap();
        match point {
            ProjectionCrashPoint::VerifiedResultPending => {
                state.fail_verified_pending_after_commit = true
            }
            ProjectionCrashPoint::ResultCommitted => state.fail_result_after_commit = true,
            ProjectionCrashPoint::BinaryTerminalReopened => state.fail_reopen_after_readback = true,
            ProjectionCrashPoint::ProjectionPending => {
                state.fail_projection_pending_after_commit = true
            }
        }
    }
    supervisor.complete_with(b"{\"result\":\"durable\"}".to_vec());

    assert_eq!(
        first
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: request_id.clone(),
            })
            .unwrap_err()
            .code(),
        "authority_runtime_integrity_failed"
    );
    drop(first);
    let ticket = ticket_ref(&request_id);
    let (signed_before_restart, pending_projection_before_restart) = {
        let state = store.lock().unwrap();
        assert_eq!(
            state.states.get(&ticket),
            Some(&RuntimeTicketState::ResultPendingProjection)
        );
        assert!(state.projections.is_empty());
        (
            state.sign_count.load(Ordering::SeqCst),
            state.pending_projections.get(&ticket).cloned(),
        )
    };
    if matches!(point, ProjectionCrashPoint::ProjectionPending) {
        assert_eq!(signed_before_restart, 1);
        assert!(pending_projection_before_restart.is_some());
    } else {
        assert_eq!(signed_before_restart, 0);
        assert!(pending_projection_before_restart.is_none());
    }

    let second = runtime(boundary, store.clone(), FakeSupervisor::ready());
    let AuthorityRuntimeReply::ResultExact { projection, .. } = second
        .handle(AuthorityRuntimeCommand::GetResult {
            request_id: request_id.clone(),
        })
        .unwrap()
    else {
        panic!("unexpected result reply");
    };
    let state = store.lock().unwrap();
    assert_eq!(state.states.get(&ticket), Some(&RuntimeTicketState::Result));
    assert!(state.pending_projections.is_empty());
    assert_eq!(state.sign_count.load(Ordering::SeqCst), 1);
    let persisted = state.projections.get(&ticket).cloned().unwrap();
    if let Some(pending_projection) = pending_projection_before_restart {
        assert_eq!(persisted, pending_projection);
    }
    assert_eq!(
        projection,
        Some(persisted)
            .map(|(bytes, digest)| {
                VerifiedAuthorityResultProjection::from_immutable_ledger_readback(bytes, digest)
                    .unwrap()
            })
            .unwrap()
    );
}

#[test]
fn restart_recovers_verified_result_pending_before_result_commit() {
    assert_restart_recovers_projection_crash(ProjectionCrashPoint::VerifiedResultPending);
}

#[test]
fn restart_recovers_result_committed_before_projection() {
    assert_restart_recovers_projection_crash(ProjectionCrashPoint::ResultCommitted);
}

#[test]
fn restart_recovers_after_binary_terminal_reopen() {
    assert_restart_recovers_projection_crash(ProjectionCrashPoint::BinaryTerminalReopened);
}

#[test]
fn restart_reuses_exact_signed_projection_pending_before_commit() {
    assert_restart_recovers_projection_crash(ProjectionCrashPoint::ProjectionPending);
}

#[test]
fn restart_replays_projection_committed_before_runtime_ack_without_resigning() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let first = runtime(boundary.clone(), store.clone(), supervisor.clone());
    run(&first, "run-projection-committed-crash").unwrap();
    store.lock().unwrap().fail_projection_after_commit = true;
    supervisor.complete_with(b"{\"result\":\"durable\"}".to_vec());
    assert_eq!(
        first
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: "run-projection-committed-crash".to_owned(),
            })
            .unwrap_err()
            .code(),
        "authority_runtime_integrity_failed"
    );
    drop(first);
    let ticket = ticket_ref("run-projection-committed-crash");
    let (projection_before, receipt_before) = {
        let state = store.lock().unwrap();
        assert_eq!(state.states.get(&ticket), Some(&RuntimeTicketState::Result));
        assert!(state.pending_projections.is_empty());
        assert_eq!(state.sign_count.load(Ordering::SeqCst), 1);
        (
            state.projections.get(&ticket).cloned().unwrap(),
            state.projection_receipts.get(&ticket).cloned().unwrap(),
        )
    };

    let second = runtime(boundary, store.clone(), FakeSupervisor::ready());
    let first_delivery = second
        .handle(AuthorityRuntimeCommand::GetResult {
            request_id: "run-projection-committed-crash".to_owned(),
        })
        .unwrap();
    let repeated_delivery = second
        .handle(AuthorityRuntimeCommand::GetResult {
            request_id: "run-projection-committed-crash".to_owned(),
        })
        .unwrap();
    assert_eq!(first_delivery, repeated_delivery);
    let AuthorityRuntimeReply::ResultExact {
        projection,
        receipt,
        ..
    } = first_delivery
    else {
        panic!("unexpected result reply");
    };
    assert_eq!(projection.canonical_bytes(), projection_before.0);
    assert_eq!(projection.sha256(), &projection_before.1);
    assert_eq!(receipt, receipt_before);
    assert_eq!(store.lock().unwrap().sign_count.load(Ordering::SeqCst), 1);
}

#[test]
fn finalized_result_requires_exact_projection_commit_receipt_on_every_delivery() {
    for (request_id, omit, tamper_projection, tamper_identity) in [
        ("run-missing-projection-receipt", true, false, false),
        ("run-tampered-projection-receipt", false, true, false),
        ("run-wrong-identity-projection-receipt", false, false, true),
    ] {
        let boundary = FakeBoundary::new(identity(1));
        let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
        let supervisor = FakeSupervisor::ready();
        let runtime = runtime(boundary, store.clone(), supervisor.clone());
        run(&runtime, request_id).unwrap();
        supervisor.complete_with(b"{\"result\":\"durable\"}".to_vec());
        runtime
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: request_id.to_owned(),
            })
            .unwrap();
        let mut locked = store.lock().unwrap();
        locked.omit_projection_receipt = omit;
        locked.tamper_projection_receipt = tamper_projection;
        locked.tamper_projection_receipt_identity = tamper_identity;
        drop(locked);
        assert_eq!(
            runtime
                .handle(AuthorityRuntimeCommand::GetResult {
                    request_id: request_id.to_owned(),
                })
                .unwrap_err()
                .code(),
            "authority_runtime_integrity_failed"
        );
    }
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
    assert!(store
        .lock()
        .unwrap()
        .recovered_proof_digests
        .get(&ticket_ref("run-restart"))
        .is_none());
}

fn assert_recovered_normal_burn(armed: bool, reason: BurnReason) {
    let label = match reason {
        BurnReason::Cancelled => "cancelled",
        BurnReason::TimedOut => "timed-out",
        BurnReason::Failed | BurnReason::RestartRecovery => panic!("normal reason required"),
    };
    let request_id = format!(
        "run-recovered-{label}-{}",
        if armed { "armed" } else { "pre-armed" }
    );
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let first_supervisor = FakeSupervisor::ready();
    if !armed {
        first_supervisor.start_in_background();
    }
    let first = runtime(boundary.clone(), store.clone(), first_supervisor);
    run(&first, &request_id).unwrap();
    drop(first);

    let recovery_supervisor = FakeSupervisor::ready();
    recovery_supervisor.recover_burned_with(reason, RecoveredBurnMutation::None);
    let second = runtime(boundary, store.clone(), recovery_supervisor.clone());
    let AuthorityRuntimeReply::Status(status) =
        second.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert!(!status.global_failure);
    assert_eq!(status.startup_burned_tickets, 1);
    assert_eq!(recovery_supervisor.shared.lock().unwrap().recoveries, 1);
    let expected_reason = runtime_terminal_reason(reason);
    assert_eq!(
        second
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: request_id.clone(),
            })
            .unwrap(),
        AuthorityRuntimeReply::ResultTerminated {
            request_id: request_id.clone(),
            reason: expected_reason,
        }
    );
    let locked = store.lock().unwrap();
    let ticket = ticket_ref(&request_id);
    assert_eq!(
        locked.states.get(&ticket),
        Some(&RuntimeTicketState::Burned)
    );
    assert_eq!(locked.terminal.get(&ticket), Some(&expected_reason));
    assert!(locked
        .recovered_proof_digests
        .get(&ticket)
        .is_some_and(|digest| digest.iter().any(|byte| *byte != 0)));
}

#[test]
fn restart_preserves_pre_armed_and_armed_cancelled_or_timed_out_reason() {
    for armed in [false, true] {
        for reason in [BurnReason::Cancelled, BurnReason::TimedOut] {
            assert_recovered_normal_burn(armed, reason);
        }
    }
}

fn assert_recovered_normal_burn_rejected(armed: bool, mutation: RecoveredBurnMutation) {
    let request_id = format!(
        "run-recovered-reject-{}-{mutation:?}",
        if armed { "armed" } else { "pre-armed" }
    );
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let first_supervisor = FakeSupervisor::ready();
    if !armed {
        first_supervisor.start_in_background();
    }
    let first = runtime(boundary.clone(), store.clone(), first_supervisor);
    run(&first, &request_id).unwrap();
    drop(first);

    let recovery_supervisor = FakeSupervisor::ready();
    recovery_supervisor.recover_burned_with(BurnReason::Cancelled, mutation);
    let second = runtime(boundary, store.clone(), recovery_supervisor.clone());
    let AuthorityRuntimeReply::Status(status) =
        second.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert!(status.global_failure);
    assert_eq!(status.blockers, vec![BLOCKER_RUNTIME_INTEGRITY]);
    assert_eq!(status.startup_burned_tickets, 0);
    assert_eq!(recovery_supervisor.shared.lock().unwrap().recoveries, 1);
    let locked = store.lock().unwrap();
    let ticket = ticket_ref(&request_id);
    assert_eq!(
        locked.states.get(&ticket),
        Some(&RuntimeTicketState::Consumed)
    );
    assert!(locked.terminal.get(&ticket).is_none());
    assert!(locked.recovered_proof_digests.get(&ticket).is_none());
}

#[test]
fn restart_normal_reason_requires_the_native_recovery_binding() {
    for armed in [false, true] {
        assert_recovered_normal_burn_rejected(armed, RecoveredBurnMutation::MissingNormalBinding);
    }
}

#[test]
fn restart_normal_reason_rejects_binding_digest_or_armed_presence_drift() {
    for armed in [false, true] {
        for mutation in [
            RecoveredBurnMutation::AuthorityDigest,
            RecoveredBurnMutation::TicketDigest,
            RecoveredBurnMutation::RunBindingDigest,
            RecoveredBurnMutation::ArmedPresence,
            RecoveredBurnMutation::CleanupDigest,
            RecoveredBurnMutation::ZeroStageJournalHead,
            RecoveredBurnMutation::ZeroTerminationIntent,
            RecoveredBurnMutation::ZeroTerminalDigest,
        ] {
            assert_recovered_normal_burn_rejected(armed, mutation);
        }
    }
    for mutation in [
        RecoveredBurnMutation::ArmedReceiptDigest,
        RecoveredBurnMutation::AdmissionPreparedDigest,
        RecoveredBurnMutation::AdmissionArmedDigest,
        RecoveredBurnMutation::AdmissionPolicyDigest,
        RecoveredBurnMutation::AdmissionRecoveryDigest,
    ] {
        assert_recovered_normal_burn_rejected(true, mutation);
    }
}

#[test]
fn restart_commits_a_durably_completed_native_result_instead_of_burning_it() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let first = runtime(boundary.clone(), store.clone(), FakeSupervisor::ready());
    run(&first, "run-recovered-complete").unwrap();
    drop(first);

    let recovery_supervisor = FakeSupervisor::ready();
    recovery_supervisor.recover_completed_with(br#"{"result":"recovered"}"#.to_vec());
    let second = runtime(boundary, store.clone(), recovery_supervisor.clone());
    let AuthorityRuntimeReply::Status(status) =
        second.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert!(!status.global_failure);
    assert_eq!(status.startup_burned_tickets, 0);
    assert_eq!(recovery_supervisor.shared.lock().unwrap().recoveries, 1);

    let AuthorityRuntimeReply::ResultExact { projection, .. } = second
        .handle(AuthorityRuntimeCommand::GetResult {
            request_id: "run-recovered-complete".to_owned(),
        })
        .unwrap()
    else {
        panic!("durably completed recovery must preserve the result");
    };
    assert!(!projection.canonical_bytes().is_empty());
    assert_eq!(
        store
            .lock()
            .unwrap()
            .states
            .get(&ticket_ref("run-recovered-complete")),
        Some(&RuntimeTicketState::Result)
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
fn uncertain_cancel_acknowledgement_is_retryable_without_failure_abort() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let runtime = runtime(boundary, store, supervisor.clone());
    run(&runtime, "run-cancel-uncertain").unwrap();
    supervisor.set_cancel_acknowledgement(SupervisorCancelAcknowledgement::Uncertain);

    let error = runtime
        .handle(AuthorityRuntimeCommand::Cancel {
            request_id: "run-cancel-uncertain".to_owned(),
        })
        .expect_err("missing acknowledgement must not be reported as success");
    assert_eq!(error.code(), CANCEL_ACKNOWLEDGEMENT_UNCERTAIN_CODE);
    assert!(!error.requires_process_exit());
    assert_eq!(supervisor.shared.lock().unwrap().aborts, 0);

    supervisor.set_cancel_acknowledgement(SupervisorCancelAcknowledgement::AlreadyRecorded(
        RuntimeTerminalKind::Cancelled,
    ));
    assert_eq!(
        runtime
            .handle(AuthorityRuntimeCommand::Cancel {
                request_id: "run-cancel-uncertain".to_owned(),
            })
            .unwrap(),
        AuthorityRuntimeReply::CancelRequested {
            request_id: "run-cancel-uncertain".to_owned(),
            already_requested: true,
        }
    );
    assert_eq!(
        runtime
            .handle(AuthorityRuntimeCommand::Cancel {
                request_id: "run-cancel-uncertain".to_owned(),
            })
            .unwrap(),
        AuthorityRuntimeReply::CancelRequested {
            request_id: "run-cancel-uncertain".to_owned(),
            already_requested: true,
        }
    );
    assert_eq!(supervisor.shared.lock().unwrap().aborts, 0);
}

#[test]
fn terminal_cancel_race_requires_durable_terminal_readback() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    let runtime = runtime(boundary, store.clone(), supervisor.clone());
    run(&runtime, "run-cancel-terminal-race").unwrap();
    supervisor.complete_cancel_race_with(RuntimeTerminalKind::Cancelled);

    assert_eq!(
        runtime
            .handle(AuthorityRuntimeCommand::Cancel {
                request_id: "run-cancel-terminal-race".to_owned(),
            })
            .unwrap(),
        AuthorityRuntimeReply::AlreadyTerminated {
            request_id: "run-cancel-terminal-race".to_owned(),
            reason: RuntimeTerminalKind::Cancelled,
        }
    );
    assert_eq!(
        store
            .lock()
            .unwrap()
            .states
            .get(&ticket_ref("run-cancel-terminal-race")),
        Some(&RuntimeTicketState::Burned)
    );
    assert_eq!(supervisor.shared.lock().unwrap().aborts, 0);
}

#[test]
fn cancellation_during_background_start_waits_on_the_supervisor_contract() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    supervisor.start_in_background();
    let runtime = runtime(boundary, store.clone(), supervisor.clone());
    run(&runtime, "run-cancel-starting").unwrap();

    assert_eq!(
        runtime
            .handle(AuthorityRuntimeCommand::Cancel {
                request_id: "run-cancel-starting".to_owned(),
            })
            .unwrap(),
        AuthorityRuntimeReply::CancelRequested {
            request_id: "run-cancel-starting".to_owned(),
            already_requested: false,
        }
    );
    assert_eq!(
        store.lock().unwrap().events,
        vec![
            format!("issue:{}", ticket_ref("run-cancel-starting").as_str()),
            format!("consume:{}", ticket_ref("run-cancel-starting").as_str()),
        ]
    );
    assert_eq!(supervisor.shared.lock().unwrap().aborts, 0);

    supervisor.arm_background_start();
    let AuthorityRuntimeReply::Status(status) =
        runtime.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected status reply");
    };
    assert_eq!(
        status.active_request_id.as_deref(),
        Some("run-cancel-starting")
    );
    assert_eq!(
        store
            .lock()
            .unwrap()
            .events
            .iter()
            .filter(|event| event.starts_with("armed:"))
            .count(),
        1
    );
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
    let completed = native_completed_from_terminal_for_test(&context, completed);
    supervisor.shared.lock().unwrap().poll =
        SupervisorPoll::Terminal(ValidatedNativeTerminalRun::Completed(completed));

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
    let completed = native_completed_from_terminal_for_test(&context, completed);
    supervisor.shared.lock().unwrap().poll =
        SupervisorPoll::Terminal(ValidatedNativeTerminalRun::Completed(completed));

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
    let replay = native_burned_for_test(&first_context, BurnReason::Failed);

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
    second_supervisor.shared.lock().unwrap().poll =
        SupervisorPoll::Terminal(ValidatedNativeTerminalRun::Burned(replay));

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
fn prearm_terminal_proof_is_burned_once_without_second_containment() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore::default()));
    let supervisor = FakeSupervisor::ready();
    supervisor.burn_during_start();
    let runtime = runtime(boundary, store.clone(), supervisor.clone());
    assert_eq!(
        run(&runtime, "run-prearm-burned").unwrap_err().code(),
        "authority_run_terminated"
    );
    assert_eq!(supervisor.shared.lock().unwrap().aborts, 0);
    assert_eq!(
        store
            .lock()
            .unwrap()
            .states
            .get(&ticket_ref("run-prearm-burned")),
        Some(&RuntimeTicketState::Burned)
    );
    assert_eq!(
        runtime
            .handle(AuthorityRuntimeCommand::GetResult {
                request_id: "run-prearm-burned".to_owned(),
            })
            .unwrap(),
        AuthorityRuntimeReply::ResultTerminated {
            request_id: "run-prearm-burned".to_owned(),
            reason: RuntimeTerminalKind::Failed,
        }
    );
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
fn partial_result_latches_failure_and_restart_stays_fail_closed() {
    let boundary = FakeBoundary::new(identity(1));
    let store = Arc::new(Mutex::new(FakeLedgerStore {
        fail_record_after_partial: true,
        ..FakeLedgerStore::default()
    }));
    let supervisor = FakeSupervisor::ready();
    let first = runtime(boundary.clone(), store.clone(), supervisor.clone());
    run(&first, "run-partial").unwrap();
    supervisor.complete_with(b"{\"partial\":true}".to_vec());
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

    let second = runtime(boundary, store, FakeSupervisor::ready());
    let AuthorityRuntimeReply::Status(status) =
        second.handle(AuthorityRuntimeCommand::Status).unwrap()
    else {
        panic!("unexpected reply");
    };
    assert_eq!(status.startup_burned_tickets, 0);
    assert!(status.global_failure);
    assert_eq!(status.blockers, vec![BLOCKER_RUNTIME_INTEGRITY]);
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
