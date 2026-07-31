//! Typed durable bridge for actor-separated finalizer commit publication.
//!
//! The legacy transaction entry point still has no typed store, protocol
//! evidence, or finalizer process lease in its signature, so its no-argument
//! readiness check remains fail-closed. New integration must construct this
//! adapter from an authenticated store and use the actor-specific surfaces
//! below; restart state is accepted only after a strict durable store scan.

use super::super::super::{
    finalizer_commit_protocol::{
        ApplyReadyEvidence, CandidateStoppedReadback, ExactSealedSecurityReadback,
        ExitReadyEvidence, FinalCommitEvidence, FinalizerCommitBinding,
        FinalizerCommitProtocolState, FinalizerCommitStage, NonceArtifactPair, SealReadyEvidence,
    },
    finalizer_commit_store_windows::{
        FinalizerCommitReceiptStore, FinalizerCommitRecoveryDirective,
        NativeElevatedFinalizerCommitLease, PersistedFinalizerCommitStage,
        PersistedReceiptFileReference, RecoveredFinalizerCommitState,
    },
    finalizer_generation_seal::GenerationSealTerminalAuthorization,
    AuthorityMaintenanceError, VerifiedElevatedMaintenanceCapability, VerifiedMaintenanceLease,
};

pub(super) const NATIVE_SYSTEM_TRANSACTION_ADAPTER_BLOCKER: &str =
    "authority_native_finalizer_owned_commit_protocol_missing";

/// The old caller cannot prove any of the typed inputs required by this
/// module. Keep it blocked until that caller is explicitly replaced; the
/// existence of the adapter alone is not evidence that legacy execution is
/// safe.
pub(super) fn require_finalizer_owned_commit_protocol() -> Result<(), AuthorityMaintenanceError> {
    Err(AuthorityMaintenanceError(
        NATIVE_SYSTEM_TRANSACTION_ADAPTER_BLOCKER,
    ))
}

/// Owns one authenticated receipt store and the state recovered from its
/// current durable tip. No constructor accepts a digest summary or caller-
/// supplied phase flags.
pub(super) struct NativeFinalizerCommitAdapter {
    store: FinalizerCommitReceiptStore,
    recovered: RecoveredFinalizerCommitState,
}

impl NativeFinalizerCommitAdapter {
    pub(super) fn begin(
        store: FinalizerCommitReceiptStore,
        transaction_started: FinalizerCommitProtocolState,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if transaction_started.latest_stage() != FinalizerCommitStage::TransactionStarted {
            return Err(AuthorityMaintenanceError(
                "authority_native_commit_adapter_start_stage_invalid",
            ));
        }
        let persisted = store.persist_transaction_started(&transaction_started)?;
        let recovered = store.recover_exact_tip(persisted.file())?;
        if recovered.protocol_state() != &transaction_started {
            return Err(AuthorityMaintenanceError(
                "authority_native_commit_adapter_start_readback_mismatch",
            ));
        }
        Ok(Self { store, recovered })
    }

    /// Recomputes every protocol field from the canonical durable chain. A
    /// caller cannot provide a cached state, phase, receipt digest, or actor.
    pub(super) fn restart(
        store: FinalizerCommitReceiptStore,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let recovered = store.recover()?.ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_receipt_missing",
        ))?;
        Ok(Self { store, recovered })
    }

    pub(super) fn binding(&self) -> FinalizerCommitBinding {
        self.recovered.protocol_state().binding()
    }

    pub(super) fn latest_stage(&self) -> FinalizerCommitStage {
        self.recovered.protocol_state().latest_stage()
    }

    pub(super) fn directive(&self) -> FinalizerCommitRecoveryDirective {
        self.recovered.directive()
    }

    pub(super) fn tip(&self) -> PersistedReceiptFileReference {
        self.recovered.tip()
    }

    pub(super) fn protocol_state(&self) -> &FinalizerCommitProtocolState {
        self.recovered.protocol_state()
    }

    pub(super) fn system_actor(&mut self) -> NativeSystemCommitActor<'_> {
        NativeSystemCommitActor { adapter: self }
    }

    pub(super) fn acquire_elevated_finalizer_lease(
        &self,
        capability: &VerifiedElevatedMaintenanceCapability,
        maintenance_lease: &VerifiedMaintenanceLease,
    ) -> Result<NativeElevatedFinalizerCommitLease, AuthorityMaintenanceError> {
        self.store
            .acquire_elevated_finalizer_lease(capability, maintenance_lease)
    }

    #[cfg(test)]
    fn acquire_elevated_finalizer_lease_for_test(
        &self,
        actor_epoch: u64,
    ) -> Result<NativeElevatedFinalizerCommitLease, AuthorityMaintenanceError> {
        self.store
            .acquire_elevated_finalizer_lease_for_test(actor_epoch)
    }

    pub(super) fn elevated_finalizer<'a>(
        &'a mut self,
        lease: &'a NativeElevatedFinalizerCommitLease,
    ) -> NativeElevatedFinalizerCommitActor<'a> {
        NativeElevatedFinalizerCommitActor {
            adapter: self,
            lease,
        }
    }

    fn adopt_persisted(
        &mut self,
        persisted: PersistedFinalizerCommitStage,
    ) -> Result<PersistedFinalizerCommitStage, AuthorityMaintenanceError> {
        let recovered = self.store.recover_exact_tip(persisted.file())?;
        if recovered.protocol_state().latest_stage() != persisted.file().stage() {
            return Err(AuthorityMaintenanceError(
                "authority_native_commit_adapter_durable_stage_mismatch",
            ));
        }
        self.recovered = recovered;
        Ok(persisted)
    }
}

/// SYSTEM can publish only ApplyReady, SealReady, and ExitReady. The terminal
/// methods and elevated lease do not exist on this surface.
pub(super) struct NativeSystemCommitActor<'a> {
    adapter: &'a mut NativeFinalizerCommitAdapter,
}

impl NativeSystemCommitActor<'_> {
    pub(super) fn record_apply_ready(
        &mut self,
        evidence: ApplyReadyEvidence,
    ) -> Result<PersistedFinalizerCommitStage, AuthorityMaintenanceError> {
        let mut next = self.adapter.recovered.protocol_state().clone();
        let write = next.system_actor().record_apply_ready(evidence)?;
        let persisted = self
            .adapter
            .store
            .persist_system_transition(&next, &write)?;
        self.adapter.adopt_persisted(persisted)
    }

    pub(super) fn record_seal_ready(
        &mut self,
        evidence: SealReadyEvidence,
    ) -> Result<PersistedFinalizerCommitStage, AuthorityMaintenanceError> {
        let mut next = self.adapter.recovered.protocol_state().clone();
        let write = next.system_actor().record_seal_ready(evidence)?;
        let persisted = self
            .adapter
            .store
            .persist_system_transition(&next, &write)?;
        self.adapter.adopt_persisted(persisted)
    }

    pub(super) fn record_exit_ready(
        &mut self,
        evidence: ExitReadyEvidence,
    ) -> Result<PersistedFinalizerCommitStage, AuthorityMaintenanceError> {
        let mut next = self.adapter.recovered.protocol_state().clone();
        let write = next.system_actor().record_exit_ready(evidence)?;
        let persisted = self
            .adapter
            .store
            .persist_system_transition(&next, &write)?;
        self.adapter.adopt_persisted(persisted)
    }
}

/// Terminal publication requires the held current elevated-finalizer lease.
/// This surface contains no SYSTEM-owned transition.
pub(super) struct NativeElevatedFinalizerCommitActor<'a> {
    adapter: &'a mut NativeFinalizerCommitAdapter,
    lease: &'a NativeElevatedFinalizerCommitLease,
}

impl NativeElevatedFinalizerCommitActor<'_> {
    pub(super) fn record_seal_complete(
        &mut self,
        generation_authorization: &GenerationSealTerminalAuthorization,
        artifacts: NonceArtifactPair,
        sealed_security: ExactSealedSecurityReadback,
        candidate: CandidateStoppedReadback,
    ) -> Result<PersistedFinalizerCommitStage, AuthorityMaintenanceError> {
        let mut next = self.adapter.recovered.protocol_state().clone();
        let write = next.elevated_finalizer().record_seal_complete_authorized(
            generation_authorization,
            artifacts,
            sealed_security,
            candidate,
        )?;
        let persisted = self
            .adapter
            .store
            .persist_seal_complete_from_elevated_finalizer(
                &next,
                &write,
                generation_authorization,
                self.lease,
            )?;
        self.adapter.adopt_persisted(persisted)
    }

    pub(super) fn record_final_commit(
        &mut self,
        evidence: FinalCommitEvidence,
    ) -> Result<PersistedFinalizerCommitStage, AuthorityMaintenanceError> {
        let mut next = self.adapter.recovered.protocol_state().clone();
        let write = next.elevated_finalizer().record_final_commit(evidence)?;
        let persisted = self
            .adapter
            .store
            .persist_final_commit_from_elevated_finalizer(&next, &write, self.lease)?;
        self.adapter.adopt_persisted(persisted)
    }
}

#[cfg(test)]
#[path = "adapter/tests.rs"]
mod tests;
