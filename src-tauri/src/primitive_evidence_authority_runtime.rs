use crate::primitive_evidence_authority_ledger::compute_recovery_bundle_digest;
use crate::primitive_evidence_authority_supervisor::{
    derive_run_binding_digest, ArmedRecoveryReceipt, BurnReason, BurnedRunProof, CompletedRunProof,
    PreparedRecoveryReceipt, PreparedRun, VerifiedReadinessProof,
};
use sha2::{Digest, Sha256};
use std::sync::{Mutex, MutexGuard};

const BLOCKER_RUNTIME_STARTUP: &str = "authority_runtime_startup_failed";
const BLOCKER_RUNTIME_INTEGRITY: &str = "authority_runtime_integrity_failed";
const BLOCKER_SUPERVISOR_NOT_CONNECTED: &str = "authority_supervisor_not_connected";
const BLOCKER_SUPERVISOR_UNAVAILABLE: &str = "authority_supervisor_unavailable";
const MAX_EXACT_RESULT_BYTES: usize = 64 * 1024;
const RUNTIME_IDENTITY_DOMAIN: &[u8] = b"vrcforge-authority-runtime-identity-v1\0";
const RUNTIME_TICKET_DOMAIN: &[u8] = b"vrcforge-authority-runtime-ticket-v1\0";
const SUPERVISOR_NOT_CONNECTED_CODE: &str = "fixed_model_part_supervisor_not_connected";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityRuntimeError(&'static str);

impl AuthorityRuntimeError {
    fn new(code: &'static str) -> Self {
        Self(code)
    }

    pub fn code(&self) -> &'static str {
        self.0
    }

    pub fn requires_process_exit(&self) -> bool {
        self.0 == "authority_runtime_integrity_failed" || self.0 == "authority_runtime_lock_failed"
    }
}

impl std::fmt::Display for AuthorityRuntimeError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for AuthorityRuntimeError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeDependencyError(String);

impl RuntimeDependencyError {
    pub fn new(code: impl Into<String>) -> Self {
        Self(code.into())
    }

    pub fn code(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for RuntimeDependencyError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for RuntimeDependencyError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityRuntimeIdentity {
    authority_generation_digest: [u8; 32],
    signer_key_id: [u8; 32],
    protected_manifest_digest: [u8; 32],
    installed_layout_digest: [u8; 32],
    service_binary_digest: [u8; 32],
}

impl AuthorityRuntimeIdentity {
    pub fn new(
        authority_generation_digest: [u8; 32],
        signer_key_id: [u8; 32],
        protected_manifest_digest: [u8; 32],
        installed_layout_digest: [u8; 32],
        service_binary_digest: [u8; 32],
    ) -> Result<Self, AuthorityRuntimeError> {
        if [
            &authority_generation_digest,
            &signer_key_id,
            &protected_manifest_digest,
            &installed_layout_digest,
            &service_binary_digest,
        ]
        .into_iter()
        .any(|digest| digest.iter().all(|byte| *byte == 0))
        {
            return Err(AuthorityRuntimeError::new(
                "authority_runtime_identity_invalid",
            ));
        }
        Ok(Self {
            authority_generation_digest,
            signer_key_id,
            protected_manifest_digest,
            installed_layout_digest,
            service_binary_digest,
        })
    }

    pub fn authority_generation_digest(&self) -> &[u8; 32] {
        &self.authority_generation_digest
    }

    pub fn signer_key_id(&self) -> &[u8; 32] {
        &self.signer_key_id
    }

    pub fn protected_manifest_digest(&self) -> &[u8; 32] {
        &self.protected_manifest_digest
    }

    pub fn installed_layout_digest(&self) -> &[u8; 32] {
        &self.installed_layout_digest
    }

    pub fn service_binary_digest(&self) -> &[u8; 32] {
        &self.service_binary_digest
    }

    pub fn binding_digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(RUNTIME_IDENTITY_DOMAIN);
        digest.update(self.authority_generation_digest);
        digest.update(self.signer_key_id);
        digest.update(self.protected_manifest_digest);
        digest.update(self.installed_layout_digest);
        digest.update(self.service_binary_digest);
        digest.finalize().into()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct RuntimeTicketRef {
    digest: [u8; 32],
    persisted: String,
}

impl RuntimeTicketRef {
    fn for_request(identity: &AuthorityRuntimeIdentity, request_id: &str) -> Self {
        let mut digest = Sha256::new();
        digest.update(RUNTIME_TICKET_DOMAIN);
        digest.update(identity.binding_digest());
        digest.update((request_id.len() as u64).to_be_bytes());
        digest.update(request_id.as_bytes());
        let digest: [u8; 32] = digest.finalize().into();
        Self {
            persisted: hex_encode(&digest),
            digest,
        }
    }

    pub fn from_persisted(value: &str) -> Result<Self, RuntimeDependencyError> {
        let digest = decode_digest(value)
            .ok_or_else(|| RuntimeDependencyError::new("runtime_ticket_digest_invalid"))?;
        Ok(Self {
            digest,
            persisted: value.to_owned(),
        })
    }

    pub fn as_str(&self) -> &str {
        &self.persisted
    }

    pub fn digest(&self) -> [u8; 32] {
        self.digest
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeActiveTicket {
    ticket: RuntimeTicketRef,
    run_binding_digest: [u8; 32],
    prepared_receipt_bytes: Vec<u8>,
    canonical_policy_snapshot: Vec<u8>,
    recovery_bundle_digest: String,
    armed_receipt_bytes: Option<Vec<u8>>,
}

impl RuntimeActiveTicket {
    pub fn new(
        ticket: RuntimeTicketRef,
        run_binding_digest: [u8; 32],
        prepared_receipt_bytes: Vec<u8>,
        canonical_policy_snapshot: Vec<u8>,
        recovery_bundle_digest: String,
        armed_receipt_bytes: Option<Vec<u8>>,
    ) -> Result<Self, RuntimeDependencyError> {
        let expected_bundle = compute_recovery_bundle_digest(
            ticket.as_str(),
            &hex_encode(&run_binding_digest),
            &prepared_receipt_bytes,
            &canonical_policy_snapshot,
        )
        .map_err(|_| RuntimeDependencyError::new("runtime_recovery_bundle_invalid"))?;
        if run_binding_digest.iter().all(|byte| *byte == 0)
            || prepared_receipt_bytes.is_empty()
            || canonical_policy_snapshot.is_empty()
            || recovery_bundle_digest != expected_bundle
        {
            return Err(RuntimeDependencyError::new("runtime_run_binding_invalid"));
        }
        Ok(Self {
            ticket,
            run_binding_digest,
            prepared_receipt_bytes,
            canonical_policy_snapshot,
            recovery_bundle_digest,
            armed_receipt_bytes,
        })
    }

    pub fn ticket(&self) -> &RuntimeTicketRef {
        &self.ticket
    }

    pub fn run_binding_digest(&self) -> &[u8; 32] {
        &self.run_binding_digest
    }

    pub fn prepared_receipt_bytes(&self) -> &[u8] {
        &self.prepared_receipt_bytes
    }

    pub fn canonical_policy_snapshot(&self) -> &[u8] {
        &self.canonical_policy_snapshot
    }

    pub fn recovery_bundle_digest(&self) -> &str {
        &self.recovery_bundle_digest
    }

    pub fn armed_receipt_bytes(&self) -> Option<&[u8]> {
        self.armed_receipt_bytes.as_deref()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeRunContext {
    authority_identity_digest: [u8; 32],
    ticket: RuntimeTicketRef,
    run_binding_digest: [u8; 32],
    service_instance_digest: [u8; 32],
    runner_policy_digest: [u8; 32],
    prepared_receipt: PreparedRecoveryReceipt,
    canonical_policy_snapshot: Vec<u8>,
    armed_receipt: Option<ArmedRecoveryReceipt>,
}

impl RuntimeRunContext {
    pub fn authority_identity_digest(&self) -> &[u8; 32] {
        &self.authority_identity_digest
    }

    pub fn ticket(&self) -> &RuntimeTicketRef {
        &self.ticket
    }

    pub fn run_binding_digest(&self) -> &[u8; 32] {
        &self.run_binding_digest
    }

    pub fn service_instance_digest(&self) -> &[u8; 32] {
        &self.service_instance_digest
    }

    pub fn runner_policy_digest(&self) -> &[u8; 32] {
        &self.runner_policy_digest
    }

    pub fn prepared_receipt(&self) -> &PreparedRecoveryReceipt {
        &self.prepared_receipt
    }

    pub fn canonical_policy_snapshot(&self) -> &[u8] {
        &self.canonical_policy_snapshot
    }

    pub fn armed_receipt(&self) -> Option<&ArmedRecoveryReceipt> {
        self.armed_receipt.as_ref()
    }

    fn with_armed_receipt(mut self, armed_receipt: ArmedRecoveryReceipt) -> Self {
        self.armed_receipt = Some(armed_receipt);
        self
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeRecoveryContext {
    authority_identity_digest: [u8; 32],
    ticket: RuntimeTicketRef,
    run_binding_digest: [u8; 32],
    prepared_receipt: PreparedRecoveryReceipt,
    canonical_policy_snapshot: Vec<u8>,
    armed_receipt: Option<ArmedRecoveryReceipt>,
}

impl RuntimeRecoveryContext {
    pub fn authority_identity_digest(&self) -> &[u8; 32] {
        &self.authority_identity_digest
    }

    pub fn ticket(&self) -> &RuntimeTicketRef {
        &self.ticket
    }

    pub fn run_binding_digest(&self) -> &[u8; 32] {
        &self.run_binding_digest
    }

    pub fn prepared_receipt(&self) -> &PreparedRecoveryReceipt {
        &self.prepared_receipt
    }

    pub fn canonical_policy_snapshot(&self) -> &[u8] {
        &self.canonical_policy_snapshot
    }

    pub fn armed_receipt(&self) -> Option<&ArmedRecoveryReceipt> {
        self.armed_receipt.as_ref()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuntimeTicketState {
    Issued,
    Consumed,
    Result,
    Burned,
}

pub trait InstalledBoundaryVerifier: Send {
    fn verify_installed_boundary(
        &mut self,
    ) -> Result<AuthorityRuntimeIdentity, RuntimeDependencyError>;
}

pub trait RuntimeTicketLedger: Send {
    fn open_existing(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError>;

    fn active_tickets(&mut self) -> Result<Vec<RuntimeActiveTicket>, RuntimeDependencyError>;

    fn verify_identity(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError>;

    fn state(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<RuntimeTicketState>, RuntimeDependencyError>;

    fn issue(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        prepared_receipt_bytes: &[u8],
        canonical_policy_snapshot: &[u8],
    ) -> Result<(), RuntimeDependencyError>;

    fn consume(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
    ) -> Result<(), RuntimeDependencyError>;

    fn record_armed_receipt(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        armed_receipt_bytes: &[u8],
    ) -> Result<(), RuntimeDependencyError>;

    fn record_result_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        result_bytes: &[u8],
    ) -> Result<(), RuntimeDependencyError>;

    fn result_exact(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<Vec<u8>>, RuntimeDependencyError>;

    fn burn(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
        reason: RuntimeTerminalKind,
    ) -> Result<(), RuntimeDependencyError>;

    fn burn_recovered(
        &mut self,
        ticket: &RuntimeTicketRef,
        run_binding_digest: &[u8; 32],
    ) -> Result<(), RuntimeDependencyError>;

    fn terminal_reason(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<Option<RuntimeTerminalKind>, RuntimeDependencyError>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuntimeTerminalKind {
    Cancelled,
    TimedOut,
    Failed,
    RestartRecovery,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SupervisorPoll {
    Running,
    Completed(CompletedRunProof),
    Terminated(BurnedRunProof),
}

pub trait FixedModelPartSupervisor: Send {
    fn contain_all_orphans(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError>;

    fn readiness(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<VerifiedReadinessProof, RuntimeDependencyError>;

    fn self_test(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<VerifiedReadinessProof, RuntimeDependencyError>;

    fn prepare(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
        ticket: &RuntimeTicketRef,
        service_instance_digest: &[u8; 32],
    ) -> Result<PreparedRun, RuntimeDependencyError>;

    fn prepared_policy_snapshot(
        &mut self,
        prepared: &PreparedRun,
    ) -> Result<Vec<u8>, RuntimeDependencyError> {
        Ok(prepared.policy_snapshot().to_vec())
    }

    fn start(
        &mut self,
        prepared: PreparedRun,
        context: &RuntimeRunContext,
    ) -> Result<ArmedRecoveryReceipt, RuntimeDependencyError>;

    fn poll(
        &mut self,
        context: &RuntimeRunContext,
    ) -> Result<SupervisorPoll, RuntimeDependencyError>;

    fn cancel(&mut self, context: &RuntimeRunContext) -> Result<(), RuntimeDependencyError>;

    fn abort_and_wait_cleanup(
        &mut self,
        context: &RuntimeRunContext,
    ) -> Result<BurnedRunProof, RuntimeDependencyError>;

    fn recover_and_wait_cleanup(
        &mut self,
        context: &RuntimeRecoveryContext,
    ) -> Result<BurnedRunProof, RuntimeDependencyError>;
}

#[derive(Debug, Default)]
pub struct DisconnectedModelPartSupervisor;

impl FixedModelPartSupervisor for DisconnectedModelPartSupervisor {
    fn contain_all_orphans(
        &mut self,
        _identity: &AuthorityRuntimeIdentity,
    ) -> Result<(), RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(SUPERVISOR_NOT_CONNECTED_CODE))
    }

    fn readiness(
        &mut self,
        _identity: &AuthorityRuntimeIdentity,
    ) -> Result<VerifiedReadinessProof, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(SUPERVISOR_NOT_CONNECTED_CODE))
    }

    fn self_test(
        &mut self,
        _identity: &AuthorityRuntimeIdentity,
    ) -> Result<VerifiedReadinessProof, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(SUPERVISOR_NOT_CONNECTED_CODE))
    }

    fn prepare(
        &mut self,
        _identity: &AuthorityRuntimeIdentity,
        _ticket: &RuntimeTicketRef,
        _service_instance_digest: &[u8; 32],
    ) -> Result<PreparedRun, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(SUPERVISOR_NOT_CONNECTED_CODE))
    }

    fn start(
        &mut self,
        _prepared: PreparedRun,
        _context: &RuntimeRunContext,
    ) -> Result<ArmedRecoveryReceipt, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "fixed_model_part_supervisor_not_connected",
        ))
    }

    fn poll(
        &mut self,
        _context: &RuntimeRunContext,
    ) -> Result<SupervisorPoll, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "fixed_model_part_supervisor_not_connected",
        ))
    }

    fn cancel(&mut self, _context: &RuntimeRunContext) -> Result<(), RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "fixed_model_part_supervisor_not_connected",
        ))
    }

    fn abort_and_wait_cleanup(
        &mut self,
        _context: &RuntimeRunContext,
    ) -> Result<BurnedRunProof, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(
            "fixed_model_part_supervisor_not_connected",
        ))
    }

    fn recover_and_wait_cleanup(
        &mut self,
        _context: &RuntimeRecoveryContext,
    ) -> Result<BurnedRunProof, RuntimeDependencyError> {
        Err(RuntimeDependencyError::new(SUPERVISOR_NOT_CONNECTED_CODE))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AuthorityRuntimeCommand {
    Status,
    SelfTest,
    RunModelPartComposition { request_id: String },
    Cancel { request_id: String },
    GetResult { request_id: String },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityRuntimeStatus {
    pub trusted_boundary_ready: bool,
    pub global_failure: bool,
    pub blockers: Vec<&'static str>,
    pub active_request_id: Option<String>,
    pub startup_burned_tickets: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityRuntimeSelfTest {
    pub passed: bool,
    pub trusted_boundary_ready: bool,
    pub blockers: Vec<&'static str>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AuthorityRuntimeReply {
    Status(AuthorityRuntimeStatus),
    SelfTest(AuthorityRuntimeSelfTest),
    RunStarted {
        request_id: String,
    },
    CancelRequested {
        request_id: String,
        already_requested: bool,
    },
    AlreadyTerminated {
        request_id: String,
        reason: RuntimeTerminalKind,
    },
    ResultPending {
        request_id: String,
    },
    ResultExact {
        request_id: String,
        bytes: Vec<u8>,
    },
    ResultTerminated {
        request_id: String,
        reason: RuntimeTerminalKind,
    },
}

pub struct AuthorityRuntime {
    inner: Mutex<RuntimeInner>,
}

struct ActiveRun {
    request_id: String,
    context: RuntimeRunContext,
    cancel_requested: bool,
}

struct RuntimeInner {
    boundary: Box<dyn InstalledBoundaryVerifier>,
    ledger: Box<dyn RuntimeTicketLedger>,
    supervisor: Box<dyn FixedModelPartSupervisor>,
    identity: Option<AuthorityRuntimeIdentity>,
    active: Option<ActiveRun>,
    startup_burned_tickets: usize,
    global_blocker: Option<&'static str>,
}

impl AuthorityRuntime {
    pub fn start<B, L, S>(boundary: B, ledger: L, supervisor: S) -> Self
    where
        B: InstalledBoundaryVerifier + 'static,
        L: RuntimeTicketLedger + 'static,
        S: FixedModelPartSupervisor + 'static,
    {
        let mut inner = RuntimeInner {
            boundary: Box::new(boundary),
            ledger: Box::new(ledger),
            supervisor: Box::new(supervisor),
            identity: None,
            active: None,
            startup_burned_tickets: 0,
            global_blocker: None,
        };
        inner.bootstrap();
        Self {
            inner: Mutex::new(inner),
        }
    }

    pub fn handle(
        &self,
        command: AuthorityRuntimeCommand,
    ) -> Result<AuthorityRuntimeReply, AuthorityRuntimeError> {
        let mut inner = self.lock()?;
        match command {
            AuthorityRuntimeCommand::Status => Ok(AuthorityRuntimeReply::Status(inner.status())),
            AuthorityRuntimeCommand::SelfTest => {
                Ok(AuthorityRuntimeReply::SelfTest(inner.self_test()))
            }
            AuthorityRuntimeCommand::RunModelPartComposition { request_id } => {
                inner.run_model_part_composition(request_id)
            }
            AuthorityRuntimeCommand::Cancel { request_id } => inner.cancel(request_id),
            AuthorityRuntimeCommand::GetResult { request_id } => inner.get_result(request_id),
        }
    }

    fn lock(&self) -> Result<MutexGuard<'_, RuntimeInner>, AuthorityRuntimeError> {
        self.inner
            .lock()
            .map_err(|_| AuthorityRuntimeError::new("authority_runtime_lock_failed"))
    }
}

impl RuntimeInner {
    fn bootstrap(&mut self) {
        let identity = match self.boundary.verify_installed_boundary() {
            Ok(identity) => identity,
            Err(_) => {
                self.latch_global(BLOCKER_RUNTIME_STARTUP);
                return;
            }
        };
        if self.ledger.open_existing(&identity).is_err() {
            self.latch_global(BLOCKER_RUNTIME_STARTUP);
            return;
        }
        if self.supervisor.contain_all_orphans(&identity).is_err() {
            self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
            return;
        }
        let active_tickets = match self.ledger.active_tickets() {
            Ok(active_tickets) => active_tickets,
            Err(_) => {
                self.latch_global(BLOCKER_RUNTIME_STARTUP);
                return;
            }
        };
        let authority_identity_digest = identity.binding_digest();
        let mut burned = 0usize;
        let mut recovery_failed = false;
        for active in active_tickets {
            let prepared_receipt =
                match PreparedRecoveryReceipt::decode(active.prepared_receipt_bytes()) {
                    Ok(receipt)
                        if receipt.verifies_for(
                            &authority_identity_digest,
                            &active.ticket().digest(),
                            receipt.service_instance_digest(),
                        ) && derive_run_binding_digest(
                            &authority_identity_digest,
                            &active.ticket().digest(),
                            receipt.service_instance_digest(),
                            receipt.runner_policy_digest(),
                        ) == *active.run_binding_digest()
                            && receipt
                                .verifies_policy_snapshot(active.canonical_policy_snapshot()) =>
                    {
                        receipt
                    }
                    Ok(_) | Err(_) => {
                        recovery_failed = true;
                        continue;
                    }
                };
            let armed_receipt = match active.armed_receipt_bytes() {
                Some(bytes) => match ArmedRecoveryReceipt::decode(bytes) {
                    Ok(receipt)
                        if receipt.verifies_for(&prepared_receipt, active.run_binding_digest()) =>
                    {
                        Some(receipt)
                    }
                    Ok(_) | Err(_) => {
                        recovery_failed = true;
                        continue;
                    }
                },
                None => None,
            };
            let recovery = RuntimeRecoveryContext {
                authority_identity_digest,
                ticket: active.ticket().clone(),
                run_binding_digest: *active.run_binding_digest(),
                prepared_receipt,
                canonical_policy_snapshot: active.canonical_policy_snapshot().to_vec(),
                armed_receipt,
            };
            let proof = match self.supervisor.recover_and_wait_cleanup(&recovery) {
                Ok(proof)
                    if burned_proof_matches_recovery(&proof, &recovery)
                        && proof.reason() == BurnReason::RestartRecovery =>
                {
                    proof
                }
                Ok(_) | Err(_) => {
                    recovery_failed = true;
                    continue;
                }
            };
            if proof.cleanup_observed_at() < proof.terminal_ready_at()
                || self
                    .ledger
                    .burn_recovered(recovery.ticket(), recovery.run_binding_digest())
                    .is_err()
            {
                recovery_failed = true;
                continue;
            }
            burned += 1;
        }
        if recovery_failed {
            self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
            return;
        }
        if self.ledger.verify_identity(&identity).is_err() {
            self.latch_global(BLOCKER_RUNTIME_STARTUP);
            return;
        }
        self.identity = Some(identity);
        self.startup_burned_tickets = burned;
    }

    fn status(&mut self) -> AuthorityRuntimeStatus {
        if self.ensure_integrity().is_ok() {
            let _ = self.refresh_active();
        }
        let blockers = self.current_blockers(false);
        AuthorityRuntimeStatus {
            trusted_boundary_ready: blockers.is_empty(),
            global_failure: self.global_blocker.is_some(),
            blockers,
            active_request_id: self.active.as_ref().map(|run| run.request_id.clone()),
            startup_burned_tickets: self.startup_burned_tickets,
        }
    }

    fn self_test(&mut self) -> AuthorityRuntimeSelfTest {
        let mut blockers = Vec::new();
        if self.ensure_integrity().is_err() {
            blockers.push(self.global_blocker.unwrap_or(BLOCKER_RUNTIME_INTEGRITY));
        } else if let Err(blocker) = self.verified_readiness(true) {
            if blocker == BLOCKER_RUNTIME_INTEGRITY {
                self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
            }
            blockers.push(blocker);
        }
        AuthorityRuntimeSelfTest {
            passed: blockers.is_empty(),
            trusted_boundary_ready: blockers.is_empty(),
            blockers,
        }
    }

    fn run_model_part_composition(
        &mut self,
        request_id: String,
    ) -> Result<AuthorityRuntimeReply, AuthorityRuntimeError> {
        require_request_id(&request_id)?;
        self.ensure_integrity()?;
        self.refresh_active()?;
        if let Some(active) = &self.active {
            let code = if active.request_id == request_id {
                "authority_request_duplicate"
            } else {
                "authority_run_busy"
            };
            return Err(AuthorityRuntimeError::new(code));
        }
        let identity = self
            .identity
            .clone()
            .ok_or_else(|| AuthorityRuntimeError::new("authority_runtime_integrity_failed"))?;
        let readiness = match self.verified_readiness(false) {
            Ok(readiness) => readiness,
            Err(BLOCKER_RUNTIME_INTEGRITY) => {
                self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
                return Err(AuthorityRuntimeError::new(
                    "authority_runtime_integrity_failed",
                ));
            }
            Err(_) => return Err(AuthorityRuntimeError::new("authority_supervisor_not_ready")),
        };

        let ticket = ticket_ref_for_identity(&identity, &request_id);
        match self.ledger.state(&ticket) {
            Ok(None) => {}
            Ok(Some(_)) => {
                return Err(AuthorityRuntimeError::new("authority_request_duplicate"));
            }
            Err(_) => return self.fail_ledger_integrity(),
        }
        let prepared =
            match self
                .supervisor
                .prepare(&identity, &ticket, readiness.service_instance_digest())
            {
                Ok(prepared)
                    if prepared.verifies_for(
                        &identity.binding_digest(),
                        &ticket.digest(),
                        readiness.service_instance_digest(),
                    ) =>
                {
                    prepared
                }
                Ok(_) => {
                    return self.contain_untrusted_preparation(&identity);
                }
                Err(_) => return Err(AuthorityRuntimeError::new("authority_supervisor_not_ready")),
            };
        let prepared_receipt = prepared.receipt().clone();
        let canonical_policy_snapshot = match self.supervisor.prepared_policy_snapshot(&prepared) {
            Ok(snapshot) => snapshot,
            Err(_) => return self.contain_untrusted_preparation(&identity),
        };
        if !prepared_receipt.verifies_policy_snapshot(&canonical_policy_snapshot) {
            return self.contain_untrusted_preparation(&identity);
        }
        let context = run_context(
            &identity,
            ticket.clone(),
            prepared_receipt.clone(),
            canonical_policy_snapshot.clone(),
        );
        if self
            .ledger
            .issue(
                &ticket,
                context.run_binding_digest(),
                &prepared_receipt.encode(),
                &canonical_policy_snapshot,
            )
            .is_err()
            || self
                .ledger
                .consume(&ticket, context.run_binding_digest())
                .is_err()
        {
            return self.abort_after_uncertain_supervisor_error(&context, false);
        }
        let armed_receipt = match self.supervisor.start(prepared, &context) {
            Ok(receipt)
                if receipt.verifies_for(&prepared_receipt, context.run_binding_digest()) =>
            {
                receipt
            }
            Ok(_) => {
                return self.abort_after_uncertain_supervisor_error(&context, true);
            }
            Err(_) => return self.abort_after_uncertain_supervisor_error(&context, true),
        };
        let context = context.with_armed_receipt(armed_receipt.clone());
        if self
            .ledger
            .record_armed_receipt(
                &ticket,
                context.run_binding_digest(),
                &armed_receipt.encode(),
            )
            .is_err()
        {
            return self.abort_after_uncertain_supervisor_error(&context, false);
        }
        self.active = Some(ActiveRun {
            request_id: request_id.clone(),
            context,
            cancel_requested: false,
        });
        Ok(AuthorityRuntimeReply::RunStarted { request_id })
    }

    fn cancel(
        &mut self,
        request_id: String,
    ) -> Result<AuthorityRuntimeReply, AuthorityRuntimeError> {
        require_request_id(&request_id)?;
        self.ensure_integrity()?;
        self.refresh_active()?;
        if self
            .active
            .as_ref()
            .is_some_and(|active| active.request_id == request_id)
        {
            let already_requested = self
                .active
                .as_ref()
                .is_some_and(|active| active.cancel_requested);
            if already_requested {
                return Ok(AuthorityRuntimeReply::CancelRequested {
                    request_id,
                    already_requested: true,
                });
            }
            let context = self
                .active
                .as_ref()
                .map(|active| active.context.clone())
                .ok_or_else(|| AuthorityRuntimeError::new("authority_runtime_invariant_failed"))?;
            if self.supervisor.cancel(&context).is_err() {
                return self.abort_after_uncertain_supervisor_error(&context, true);
            }
            if let Some(active) = self.active.as_mut() {
                active.cancel_requested = true;
            }
            return Ok(AuthorityRuntimeReply::CancelRequested {
                request_id,
                already_requested: false,
            });
        }

        let ticket = self.ticket_for_request(&request_id)?;
        match self.ledger.state(&ticket) {
            Ok(Some(RuntimeTicketState::Burned)) => Ok(AuthorityRuntimeReply::AlreadyTerminated {
                reason: self.persisted_terminal_reason(&ticket)?,
                request_id,
            }),
            Ok(Some(RuntimeTicketState::Result)) => {
                Err(AuthorityRuntimeError::new("authority_result_already_final"))
            }
            Ok(Some(RuntimeTicketState::Issued | RuntimeTicketState::Consumed)) => {
                self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
                Err(AuthorityRuntimeError::new(
                    "authority_runtime_integrity_failed",
                ))
            }
            Ok(None) => Err(AuthorityRuntimeError::new("authority_request_not_found")),
            Err(_) => self.fail_ledger_integrity(),
        }
    }

    fn get_result(
        &mut self,
        request_id: String,
    ) -> Result<AuthorityRuntimeReply, AuthorityRuntimeError> {
        require_request_id(&request_id)?;
        self.ensure_integrity()?;
        self.refresh_active()?;
        let ticket = self.ticket_for_request(&request_id)?;
        match self.ledger.state(&ticket) {
            Ok(Some(RuntimeTicketState::Issued | RuntimeTicketState::Consumed)) => {
                Ok(AuthorityRuntimeReply::ResultPending { request_id })
            }
            Ok(Some(RuntimeTicketState::Result)) => match self.ledger.result_exact(&ticket) {
                Ok(Some(bytes)) if !bytes.is_empty() && bytes.len() <= MAX_EXACT_RESULT_BYTES => {
                    Ok(AuthorityRuntimeReply::ResultExact { request_id, bytes })
                }
                Ok(_) | Err(_) => self.fail_ledger_integrity(),
            },
            Ok(Some(RuntimeTicketState::Burned)) => Ok(AuthorityRuntimeReply::ResultTerminated {
                reason: self.persisted_terminal_reason(&ticket)?,
                request_id,
            }),
            Ok(None) => Err(AuthorityRuntimeError::new("authority_request_not_found")),
            Err(_) => self.fail_ledger_integrity(),
        }
    }

    fn ensure_integrity(&mut self) -> Result<(), AuthorityRuntimeError> {
        if self.global_blocker.is_some() {
            return Err(AuthorityRuntimeError::new(
                "authority_runtime_integrity_failed",
            ));
        }
        let expected = self
            .identity
            .clone()
            .ok_or_else(|| AuthorityRuntimeError::new("authority_runtime_integrity_failed"))?;
        let current = match self.boundary.verify_installed_boundary() {
            Ok(current) => current,
            Err(_) => {
                self.abort_active_without_ledger_write();
                self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
                return Err(AuthorityRuntimeError::new(
                    "authority_runtime_integrity_failed",
                ));
            }
        };
        if current != expected || self.ledger.verify_identity(&expected).is_err() {
            self.abort_active_without_ledger_write();
            self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
            return Err(AuthorityRuntimeError::new(
                "authority_runtime_integrity_failed",
            ));
        }
        Ok(())
    }

    fn refresh_active(&mut self) -> Result<(), AuthorityRuntimeError> {
        let Some(context) = self.active.as_ref().map(|active| active.context.clone()) else {
            return Ok(());
        };
        let poll = match self.supervisor.poll(&context) {
            Ok(poll) => poll,
            Err(_) => return self.abort_after_uncertain_supervisor_error(&context, true),
        };
        match poll {
            SupervisorPoll::Running => Ok(()),
            SupervisorPoll::Completed(completed) => {
                let computed_result_digest: [u8; 32] =
                    Sha256::digest(completed.result_bytes()).into();
                if !completed_proof_matches_context(&completed, &context)
                    || completed.cleanup_observed_at() < completed.finalized_at()
                    || completed
                        .cleanup_receipt_digest()
                        .iter()
                        .all(|byte| *byte == 0)
                    || completed.result_bytes().is_empty()
                    || completed.result_bytes().len() > MAX_EXACT_RESULT_BYTES
                    || completed.result_digest() != &computed_result_digest
                    || self
                        .ledger
                        .record_result_exact(
                            context.ticket(),
                            context.run_binding_digest(),
                            completed.result_bytes(),
                        )
                        .is_err()
                {
                    self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
                    return Err(AuthorityRuntimeError::new(
                        "authority_runtime_integrity_failed",
                    ));
                }
                self.active = None;
                Ok(())
            }
            SupervisorPoll::Terminated(terminated) => {
                if !burned_proof_matches_context(&terminated, &context)
                    || terminated.cleanup_observed_at() < terminated.terminal_ready_at()
                    || terminated
                        .cleanup_receipt_digest()
                        .iter()
                        .all(|byte| *byte == 0)
                {
                    self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
                    return Err(AuthorityRuntimeError::new(
                        "authority_runtime_integrity_failed",
                    ));
                }
                let reason = runtime_terminal_reason(terminated.reason());
                if self
                    .ledger
                    .burn(context.ticket(), context.run_binding_digest(), reason)
                    .is_err()
                {
                    return self.fail_ledger_integrity();
                }
                self.active = None;
                Ok(())
            }
        }
    }

    fn current_blockers(&mut self, run_self_test: bool) -> Vec<&'static str> {
        if let Some(blocker) = self.global_blocker {
            return vec![blocker];
        }
        match self.verified_readiness(run_self_test) {
            Ok(_) => Vec::new(),
            Err(BLOCKER_RUNTIME_INTEGRITY) => {
                self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
                vec![BLOCKER_RUNTIME_INTEGRITY]
            }
            Err(blocker) => vec![blocker],
        }
    }

    fn fail_ledger_integrity<T>(&mut self) -> Result<T, AuthorityRuntimeError> {
        self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
        Err(AuthorityRuntimeError::new(
            "authority_runtime_integrity_failed",
        ))
    }

    fn contain_untrusted_preparation<T>(
        &mut self,
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<T, AuthorityRuntimeError> {
        let containment_succeeded = self.supervisor.contain_all_orphans(identity).is_ok();
        self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
        if !containment_succeeded {
            return Err(AuthorityRuntimeError::new(
                "authority_runtime_integrity_failed",
            ));
        }
        Err(AuthorityRuntimeError::new(
            "authority_runtime_integrity_failed",
        ))
    }

    fn abort_after_uncertain_supervisor_error<T>(
        &mut self,
        context: &RuntimeRunContext,
        persist_burn: bool,
    ) -> Result<T, AuthorityRuntimeError> {
        let proof = self.supervisor.abort_and_wait_cleanup(context);
        if let Ok(proof) = proof {
            if burned_proof_matches_context(&proof, context)
                && proof.cleanup_observed_at() >= proof.terminal_ready_at()
                && !proof.cleanup_receipt_digest().iter().all(|byte| *byte == 0)
            {
                if persist_burn
                    && self
                        .ledger
                        .burn(
                            context.ticket(),
                            context.run_binding_digest(),
                            runtime_terminal_reason(proof.reason()),
                        )
                        .is_err()
                {
                    self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
                    return Err(AuthorityRuntimeError::new(
                        "authority_runtime_integrity_failed",
                    ));
                }
                self.active = None;
            }
        }
        self.latch_global(BLOCKER_RUNTIME_INTEGRITY);
        Err(AuthorityRuntimeError::new(
            "authority_runtime_integrity_failed",
        ))
    }

    fn abort_active_without_ledger_write(&mut self) {
        let Some(context) = self.active.as_ref().map(|active| active.context.clone()) else {
            return;
        };
        if let Ok(proof) = self.supervisor.abort_and_wait_cleanup(&context) {
            if burned_proof_matches_context(&proof, &context)
                && proof.cleanup_observed_at() >= proof.terminal_ready_at()
                && !proof.cleanup_receipt_digest().iter().all(|byte| *byte == 0)
            {
                self.active = None;
            }
        }
    }

    fn verified_readiness(
        &mut self,
        run_self_test: bool,
    ) -> Result<VerifiedReadinessProof, &'static str> {
        let identity = self.identity.clone().ok_or(BLOCKER_RUNTIME_INTEGRITY)?;
        let proof = if run_self_test {
            self.supervisor.self_test(&identity)
        } else {
            self.supervisor.readiness(&identity)
        }
        .map_err(|error| {
            if error.code() == SUPERVISOR_NOT_CONNECTED_CODE {
                BLOCKER_SUPERVISOR_NOT_CONNECTED
            } else {
                BLOCKER_SUPERVISOR_UNAVAILABLE
            }
        })?;
        if !proof.verifies_for(&identity.binding_digest()) {
            return Err(BLOCKER_RUNTIME_INTEGRITY);
        }
        Ok(proof)
    }

    fn ticket_for_request(
        &self,
        request_id: &str,
    ) -> Result<RuntimeTicketRef, AuthorityRuntimeError> {
        let identity = self
            .identity
            .as_ref()
            .ok_or_else(|| AuthorityRuntimeError::new("authority_runtime_integrity_failed"))?;
        Ok(ticket_ref_for_identity(identity, request_id))
    }

    fn persisted_terminal_reason(
        &mut self,
        ticket: &RuntimeTicketRef,
    ) -> Result<RuntimeTerminalKind, AuthorityRuntimeError> {
        match self.ledger.terminal_reason(ticket) {
            Ok(Some(reason)) => Ok(reason),
            Ok(None) | Err(_) => self.fail_ledger_integrity(),
        }
    }

    fn latch_global(&mut self, blocker: &'static str) {
        if self.global_blocker.is_none() {
            self.global_blocker = Some(blocker);
        }
    }
}

fn runtime_terminal_reason(reason: BurnReason) -> RuntimeTerminalKind {
    match reason {
        BurnReason::Cancelled => RuntimeTerminalKind::Cancelled,
        BurnReason::TimedOut => RuntimeTerminalKind::TimedOut,
        BurnReason::Failed => RuntimeTerminalKind::Failed,
        BurnReason::RestartRecovery => RuntimeTerminalKind::RestartRecovery,
    }
}

fn ticket_ref_for_identity(
    identity: &AuthorityRuntimeIdentity,
    request_id: &str,
) -> RuntimeTicketRef {
    RuntimeTicketRef::for_request(identity, request_id)
}

fn run_context(
    identity: &AuthorityRuntimeIdentity,
    ticket: RuntimeTicketRef,
    prepared_receipt: PreparedRecoveryReceipt,
    canonical_policy_snapshot: Vec<u8>,
) -> RuntimeRunContext {
    let authority_identity_digest = identity.binding_digest();
    let service_instance_digest = *prepared_receipt.service_instance_digest();
    let runner_policy_digest = *prepared_receipt.runner_policy_digest();
    let run_binding_digest = derive_run_binding_digest(
        &authority_identity_digest,
        &ticket.digest(),
        &service_instance_digest,
        &runner_policy_digest,
    );
    RuntimeRunContext {
        authority_identity_digest,
        ticket,
        run_binding_digest,
        service_instance_digest,
        runner_policy_digest,
        prepared_receipt,
        canonical_policy_snapshot,
        armed_receipt: None,
    }
}

fn completed_proof_matches_context(proof: &CompletedRunProof, context: &RuntimeRunContext) -> bool {
    proof.authority_identity_digest() == context.authority_identity_digest()
        && proof.ticket_digest() == &context.ticket().digest()
        && proof.run_binding_digest() == context.run_binding_digest()
}

fn burned_proof_matches_context(proof: &BurnedRunProof, context: &RuntimeRunContext) -> bool {
    proof.authority_identity_digest() == context.authority_identity_digest()
        && proof.ticket_digest() == &context.ticket().digest()
        && proof.run_binding_digest() == context.run_binding_digest()
        && proof.reason() != BurnReason::RestartRecovery
}

fn burned_proof_matches_recovery(proof: &BurnedRunProof, context: &RuntimeRecoveryContext) -> bool {
    proof.authority_identity_digest() == context.authority_identity_digest()
        && proof.ticket_digest() == &context.ticket().digest()
        && proof.run_binding_digest() == context.run_binding_digest()
        && !proof.cleanup_receipt_digest().iter().all(|byte| *byte == 0)
}

fn decode_digest(value: &str) -> Option<[u8; 32]> {
    if value.len() != 64
        || !value
            .as_bytes()
            .iter()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
    {
        return None;
    }
    let mut output = [0u8; 32];
    for (index, chunk) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (hex_nibble(chunk[0]) << 4) | hex_nibble(chunk[1]);
    }
    if output.iter().all(|byte| *byte == 0) {
        None
    } else {
        Some(output)
    }
}

fn hex_nibble(value: u8) -> u8 {
    match value {
        b'0'..=b'9' => value - b'0',
        b'a'..=b'f' => value - b'a' + 10,
        _ => 0,
    }
}

fn hex_encode(value: &[u8; 32]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(64);
    for byte in value {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn require_request_id(value: &str) -> Result<(), AuthorityRuntimeError> {
    let mut characters = value.chars();
    let first = characters
        .next()
        .filter(|value| value.is_ascii_alphanumeric());
    if first.is_none()
        || value.len() > 128
        || !characters
            .all(|value| value.is_ascii_alphanumeric() || matches!(value, '-' | '_' | '.' | ':'))
    {
        return Err(AuthorityRuntimeError::new("authority_request_id_invalid"));
    }
    Ok(())
}

#[cfg(test)]
#[path = "primitive_evidence_authority_runtime/tests.rs"]
mod tests;
