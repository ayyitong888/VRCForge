//! Single-owner background supervision for the protected native run.
//!
//! The worker implementation is built in this module so the non-Clone live
//! native handles never cross into the request thread.  In particular, an
//! armed run must remain parked until the outer runtime has durably recorded
//! the exact armed receipt.  Cancellation and deadline expiry must use the
//! normal terminal, cleanup, and sealing path; failure containment is not a
//! substitute for either terminal kind.

use super::{
    ArmedRecoveryReceipt, BurnReason, Digest, NativeAdvanceOutcome, NativeArmedRun,
    NativeArmedTerminationAcknowledgement, NativeBurnedRunProof, NativeStartingAdvance,
    NativeStartingRun, NativeStartingTerminationAcknowledgement, NativeTerminationKind,
    PreparedRecoveryReceipt, PreparedRun, ServiceOwnedNativeApi, ServiceOwnedNativeSupervisor,
    ServiceOwnedStagedNativeApi, SupervisorError, ValidatedNativeTerminalRun,
};
use crate::primitive_evidence_authority_pipe::{
    VerifiedScenarioStartContract, WorkerScenarioHandleBundle,
};
use sha2::{Digest as _, Sha256};
use std::marker::PhantomData;
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender, SyncSender, TryRecvError};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const WORKER_POLL_INTERVAL: Duration = Duration::from_millis(20);
const WORKER_ACK_TIMEOUT: Duration = Duration::from_secs(5);
const WORKER_ABORT_TIMEOUT: Duration = Duration::from_secs(30);
const WORKER_SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(5);
const BACKGROUND_THREAD_NAME: &str = "vrcforge-native-supervisor";

const BACKGROUND_BUSY: &str = "authority_native_background_busy";
const BACKGROUND_BINDING_MISMATCH: &str = "authority_native_background_binding_mismatch";
const BACKGROUND_ARMED_REQUIRED: &str = "authority_native_background_armed_receipt_required";
const BACKGROUND_ARMED_MISMATCH: &str = "authority_native_background_armed_receipt_mismatch";
const BACKGROUND_TERMINAL_TAKEN: &str = "authority_native_background_terminal_already_taken";
const BACKGROUND_CHANNEL_CLOSED: &str = "authority_native_background_channel_closed";
const BACKGROUND_ACK_TIMEOUT: &str = "authority_native_background_ack_timeout";
const BACKGROUND_ABORT_TIMEOUT: &str = "authority_native_background_abort_timeout";
const BACKGROUND_SHUTDOWN_TIMEOUT: &str = "authority_native_background_shutdown_timeout";
const BACKGROUND_CLOCK_INVALID: &str = "authority_native_background_clock_invalid";
const BACKGROUND_STATE_INVALID: &str = "authority_native_background_state_invalid";
const BACKGROUND_THREAD_SPAWN_FAILED: &str = "authority_native_background_thread_spawn_failed";
const BACKGROUND_CORE_LOCK_FAILED: &str = "authority_native_background_core_lock_failed";
const BACKGROUND_SHUTDOWN: &str = "authority_native_background_shutdown";

/// Opaque ownership retained by the worker for the entire native run.  The
/// implementation is supplied by the production admission layer, but the
/// background worker is the only code allowed to validate and consume it.
pub(crate) trait BackgroundTerminalLease: Send + 'static {
    fn take_worker_handles(&mut self) -> Result<WorkerScenarioHandleBundle, &'static str>;

    fn consume_start_authorization(
        &mut self,
        worker_handles: &WorkerScenarioHandleBundle,
        prepared_receipt_digest: Digest,
        policy_snapshot_digest: Digest,
    ) -> Result<VerifiedScenarioStartContract, &'static str>;

    fn finalize_terminal(
        &mut self,
        worker_handles: &mut Option<WorkerScenarioHandleBundle>,
        terminal: &ValidatedNativeTerminalRun,
    ) -> Result<(), &'static str>;
}

/// Unique start envelope.  Neither the prepared capability nor its terminal
/// lease can be cloned or detached before the worker receives it.
pub(crate) struct OwnedBackgroundRun {
    prepared: PreparedRun,
    lease: Box<dyn BackgroundTerminalLease>,
}

impl std::fmt::Debug for OwnedBackgroundRun {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("OwnedBackgroundRun")
            .field("prepared_receipt", &self.prepared.receipt().digest())
            .finish_non_exhaustive()
    }
}

impl OwnedBackgroundRun {
    pub(crate) fn new(prepared: PreparedRun, lease: Box<dyn BackgroundTerminalLease>) -> Self {
        Self { prepared, lease }
    }

    fn key(&self) -> BackgroundRunKey {
        BackgroundRunKey::from_prepared(&self.prepared)
    }
}

/// Stable, non-secret identity for routing foreground calls to the exact live
/// preparation.  It contains only the digest of the already persisted
/// prepared receipt; live handles remain exclusively in the worker.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct BackgroundRunKey(Digest);

impl BackgroundRunKey {
    pub(crate) fn from_prepared(prepared: &PreparedRun) -> Self {
        Self(prepared.receipt().digest())
    }

    pub(crate) fn from_persisted(prepared: &PreparedRecoveryReceipt) -> Self {
        Self(prepared.digest())
    }
}

/// A take-once view of the worker.  This deliberately does not implement
/// `Clone`: a terminal proof is moved out exactly once.
#[derive(Debug)]
pub(crate) enum BackgroundNativePoll {
    Starting,
    Armed(ArmedRecoveryReceipt),
    Running,
    Terminal(ValidatedNativeTerminalRun),
}

/// Result of asking the single-owner worker to durably record normal
/// termination. `Uncertain` is deliberately not an error: once a command was
/// accepted by the channel but its acknowledgement was not observed, the
/// foreground must recover or retry instead of assuming the intent failed and
/// issuing a conflicting failure abort.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum BackgroundCancelAcknowledgement {
    Recorded(NativeTerminationKind),
    AlreadyRecorded(NativeTerminationKind),
    AlreadyTerminal,
    Uncertain,
}

/// Restart recovery runs entirely in the worker because the native driver may
/// acquire or contain live capabilities while validating its sealed journal.
#[derive(Debug)]
pub(crate) enum BackgroundNativeRecoveryPoll {
    Recovering,
    Terminal(ValidatedNativeTerminalRun),
}

trait BackgroundClock: Send + Sync + 'static {
    fn now_unix_seconds(&self) -> Result<u64, &'static str>;
}

struct SystemClock;

impl BackgroundClock for SystemClock {
    fn now_unix_seconds(&self) -> Result<u64, &'static str> {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|value| value.as_secs())
            .map_err(|_| BACKGROUND_CLOCK_INVALID)
    }
}

enum DriverAdvance<A, T> {
    Running(A),
    Terminal(T),
    Retrying(A, &'static str),
    FaultHeld(&'static str),
}

enum DriverStartingAdvance<S, A, T> {
    Starting(S),
    Armed(A),
    Terminal(T),
    Retrying(S, &'static str),
    FaultHeld(&'static str),
}

enum DriverAbort<T> {
    Terminal(T),
    Retrying(&'static str),
    FaultHeld(&'static str),
}

enum DriverStartingTerminationAcknowledgement {
    Recorded(NativeTerminationKind),
    Uncertain,
}

enum DriverArmedTerminationAcknowledgement {
    Recorded(NativeTerminationKind),
    Uncertain,
}

/// Narrow seam used to test the concurrency state machine without fabricating
/// native handles or service receipts.  The production adapter below is the
/// only implementation used outside tests.
trait BackgroundDriver: Send + 'static {
    type Prepared: Send + 'static;
    type Recovery: Send + 'static;
    type Starting: Send + 'static;
    type Armed: Send + 'static;
    type Receipt: Clone + PartialEq + Eq + Send + 'static;
    type Terminal: Send + 'static;

    fn begin_start(&mut self, prepared: Self::Prepared) -> Result<Self::Starting, &'static str>;
    fn starting_deadline(starting: &Self::Starting) -> u64;
    fn request_starting_termination(
        &mut self,
        starting: &mut Self::Starting,
    ) -> Result<DriverStartingTerminationAcknowledgement, &'static str>;
    fn advance_starting(
        &mut self,
        starting: Self::Starting,
    ) -> DriverStartingAdvance<Self::Starting, Self::Armed, Self::Terminal>;
    fn abort_starting(
        &mut self,
        starting: &mut Self::Starting,
        failure_code: &'static str,
    ) -> DriverAbort<Self::Terminal>;
    fn armed_receipt(armed: &Self::Armed) -> &Self::Receipt;
    fn deadline(armed: &Self::Armed) -> u64;
    fn request_termination(
        &mut self,
        armed: &mut Self::Armed,
    ) -> Result<DriverArmedTerminationAcknowledgement, &'static str>;
    fn advance(&mut self, armed: Self::Armed) -> DriverAdvance<Self::Armed, Self::Terminal>;
    fn abort(
        &mut self,
        armed: &mut Self::Armed,
        failure_code: &'static str,
    ) -> DriverAbort<Self::Terminal>;
    fn recover(&mut self, recovery: Self::Recovery) -> Result<Self::Terminal, &'static str>;
}

struct NativeRecoveryRequest {
    prepared: PreparedRecoveryReceipt,
    armed: Option<ArmedRecoveryReceipt>,
    policy_snapshot: Vec<u8>,
}

struct NativeDriver<A: ServiceOwnedNativeApi> {
    native: ServiceOwnedNativeSupervisor<A>,
    fault_held: Option<FaultHeldWorkerRunOwnership>,
}

struct OwnedNativeStartingRun {
    native: NativeStartingRun,
    ownership: Option<WorkerRunOwnership>,
}

struct OwnedNativeArmedRun {
    native: NativeArmedRun,
    ownership: Option<WorkerRunOwnership>,
}

struct WorkerRunOwnership {
    lease: Box<dyn BackgroundTerminalLease>,
    worker_handles: Option<WorkerScenarioHandleBundle>,
    start_contract: VerifiedScenarioStartContract,
}

struct PendingWorkerRunOwnership {
    lease: Box<dyn BackgroundTerminalLease>,
    worker_handles: Option<WorkerScenarioHandleBundle>,
}

enum FaultHeldWorkerRunOwnership {
    Pending(PendingWorkerRunOwnership),
    Authorized(WorkerRunOwnership),
}

impl<A: ServiceOwnedNativeApi> NativeDriver<A> {
    fn finish_terminal(
        &mut self,
        ownership: WorkerRunOwnership,
        terminal: ValidatedNativeTerminalRun,
    ) -> Result<ValidatedNativeTerminalRun, &'static str> {
        finish_terminal_ownership(&mut self.fault_held, ownership, terminal)
    }

    fn take_starting_ownership(
        starting: &mut OwnedNativeStartingRun,
    ) -> Result<WorkerRunOwnership, &'static str> {
        starting.ownership.take().ok_or(BACKGROUND_STATE_INVALID)
    }

    fn take_armed_ownership(
        armed: &mut OwnedNativeArmedRun,
    ) -> Result<WorkerRunOwnership, &'static str> {
        armed.ownership.take().ok_or(BACKGROUND_STATE_INVALID)
    }
}

fn claim_worker_ownership(
    fault_held: &mut Option<FaultHeldWorkerRunOwnership>,
    lease: Box<dyn BackgroundTerminalLease>,
) -> Result<PendingWorkerRunOwnership, &'static str> {
    let mut ownership = PendingWorkerRunOwnership {
        lease,
        worker_handles: None,
    };
    match ownership.lease.take_worker_handles() {
        Ok(worker_handles) => {
            ownership.worker_handles = Some(worker_handles);
            Ok(ownership)
        }
        Err(code) => {
            *fault_held = Some(FaultHeldWorkerRunOwnership::Pending(ownership));
            Err(code)
        }
    }
}

fn claim_worker_ownership_for_native_begin(
    fault_held: &mut Option<FaultHeldWorkerRunOwnership>,
    lease: Box<dyn BackgroundTerminalLease>,
    prepared: &PreparedRun,
) -> Result<WorkerRunOwnership, &'static str> {
    let mut ownership = claim_worker_ownership(fault_held, lease)?;
    let prepared_receipt_digest = prepared.receipt().digest();
    let policy_snapshot_digest: Digest = Sha256::digest(prepared.policy_snapshot()).into();
    let contract = {
        let PendingWorkerRunOwnership {
            lease,
            worker_handles,
        } = &mut ownership;
        match worker_handles.as_ref() {
            Some(worker_handles) => lease.consume_start_authorization(
                worker_handles,
                prepared_receipt_digest,
                policy_snapshot_digest,
            ),
            None => Err(BACKGROUND_STATE_INVALID),
        }
    };
    match contract {
        Ok(start_contract) => Ok(WorkerRunOwnership {
            lease: ownership.lease,
            worker_handles: ownership.worker_handles,
            start_contract,
        }),
        Err(code) => {
            *fault_held = Some(FaultHeldWorkerRunOwnership::Pending(ownership));
            Err(code)
        }
    }
}

fn finish_terminal_ownership(
    fault_held: &mut Option<FaultHeldWorkerRunOwnership>,
    mut ownership: WorkerRunOwnership,
    terminal: ValidatedNativeTerminalRun,
) -> Result<ValidatedNativeTerminalRun, &'static str> {
    if ownership.worker_handles.is_none() {
        *fault_held = Some(FaultHeldWorkerRunOwnership::Authorized(ownership));
        return Err(BACKGROUND_STATE_INVALID);
    }
    if let Err(code) = ownership
        .lease
        .finalize_terminal(&mut ownership.worker_handles, &terminal)
    {
        *fault_held = Some(FaultHeldWorkerRunOwnership::Authorized(ownership));
        return Err(code);
    }
    if ownership.worker_handles.is_some() {
        *fault_held = Some(FaultHeldWorkerRunOwnership::Authorized(ownership));
        return Err(BACKGROUND_STATE_INVALID);
    }
    Ok(terminal)
}

impl<A: ServiceOwnedNativeApi + ServiceOwnedStagedNativeApi + 'static> BackgroundDriver
    for NativeDriver<A>
{
    type Prepared = OwnedBackgroundRun;
    type Recovery = NativeRecoveryRequest;
    type Starting = OwnedNativeStartingRun;
    type Armed = OwnedNativeArmedRun;
    type Receipt = ArmedRecoveryReceipt;
    type Terminal = ValidatedNativeTerminalRun;

    fn begin_start(&mut self, prepared: Self::Prepared) -> Result<Self::Starting, &'static str> {
        let OwnedBackgroundRun { prepared, lease } = prepared;
        let ownership =
            claim_worker_ownership_for_native_begin(&mut self.fault_held, lease, &prepared)?;
        match self.native.begin_start(prepared, &ownership.start_contract) {
            Ok(native) => Ok(OwnedNativeStartingRun {
                native,
                ownership: Some(ownership),
            }),
            Err(error) => {
                self.fault_held = Some(FaultHeldWorkerRunOwnership::Authorized(ownership));
                Err(error.code())
            }
        }
    }

    fn starting_deadline(starting: &Self::Starting) -> u64 {
        ServiceOwnedNativeSupervisor::<A>::starting_deadline(&starting.native)
    }

    fn request_starting_termination(
        &mut self,
        starting: &mut Self::Starting,
    ) -> Result<DriverStartingTerminationAcknowledgement, &'static str> {
        self.native
            .request_starting_termination(&mut starting.native)
            .map(|acknowledgement| match acknowledgement {
                NativeStartingTerminationAcknowledgement::Recorded(kind) => {
                    DriverStartingTerminationAcknowledgement::Recorded(kind)
                }
                NativeStartingTerminationAcknowledgement::Uncertain => {
                    DriverStartingTerminationAcknowledgement::Uncertain
                }
            })
            .map_err(|error| error.code())
    }

    fn advance_starting(
        &mut self,
        starting: Self::Starting,
    ) -> DriverStartingAdvance<Self::Starting, Self::Armed, Self::Terminal> {
        let OwnedNativeStartingRun { native, ownership } = starting;
        let Some(ownership) = ownership else {
            return DriverStartingAdvance::FaultHeld(BACKGROUND_STATE_INVALID);
        };
        match self
            .native
            .advance_starting(native, &ownership.start_contract)
        {
            NativeStartingAdvance::Starting(native) => {
                DriverStartingAdvance::Starting(OwnedNativeStartingRun {
                    native,
                    ownership: Some(ownership),
                })
            }
            NativeStartingAdvance::Armed(native) => {
                DriverStartingAdvance::Armed(OwnedNativeArmedRun {
                    native,
                    ownership: Some(ownership),
                })
            }
            NativeStartingAdvance::Terminal(terminal) => {
                match self.finish_terminal(ownership, terminal) {
                    Ok(terminal) => DriverStartingAdvance::Terminal(terminal),
                    Err(code) => DriverStartingAdvance::FaultHeld(code),
                }
            }
            NativeStartingAdvance::Retrying(native, code) => DriverStartingAdvance::Retrying(
                OwnedNativeStartingRun {
                    native,
                    ownership: Some(ownership),
                },
                code,
            ),
        }
    }

    fn abort_starting(
        &mut self,
        starting: &mut Self::Starting,
        failure_code: &'static str,
    ) -> DriverAbort<Self::Terminal> {
        match self.native.abort_starting(&starting.native, failure_code) {
            Ok(proof) => {
                let ownership = match Self::take_starting_ownership(starting) {
                    Ok(ownership) => ownership,
                    Err(code) => return DriverAbort::FaultHeld(code),
                };
                match self.finish_terminal(ownership, ValidatedNativeTerminalRun::Burned(proof)) {
                    Ok(terminal) => DriverAbort::Terminal(terminal),
                    Err(code) => DriverAbort::FaultHeld(code),
                }
            }
            Err(error) => DriverAbort::Retrying(error.code()),
        }
    }

    fn armed_receipt(armed: &Self::Armed) -> &Self::Receipt {
        armed.native.armed_receipt()
    }

    fn deadline(armed: &Self::Armed) -> u64 {
        armed.native.policy.deadline
    }

    fn request_termination(
        &mut self,
        armed: &mut Self::Armed,
    ) -> Result<DriverArmedTerminationAcknowledgement, &'static str> {
        self.native
            .request_armed_termination(&mut armed.native)
            .map(|acknowledgement| match acknowledgement {
                NativeArmedTerminationAcknowledgement::Recorded(kind) => {
                    DriverArmedTerminationAcknowledgement::Recorded(kind)
                }
                NativeArmedTerminationAcknowledgement::Uncertain => {
                    DriverArmedTerminationAcknowledgement::Uncertain
                }
            })
            .map_err(|error| error.code())
    }

    fn advance(&mut self, armed: Self::Armed) -> DriverAdvance<Self::Armed, Self::Terminal> {
        let OwnedNativeArmedRun { native, ownership } = armed;
        let Some(ownership) = ownership else {
            return DriverAdvance::FaultHeld(BACKGROUND_STATE_INVALID);
        };
        match self.native.advance_armed(native) {
            NativeAdvanceOutcome::Running(native) => DriverAdvance::Running(OwnedNativeArmedRun {
                native,
                ownership: Some(ownership),
            }),
            NativeAdvanceOutcome::Terminal(terminal) => {
                match self.finish_terminal(ownership, terminal) {
                    Ok(terminal) => DriverAdvance::Terminal(terminal),
                    Err(code) => DriverAdvance::FaultHeld(code),
                }
            }
            NativeAdvanceOutcome::Retrying(native, code) => DriverAdvance::Retrying(
                OwnedNativeArmedRun {
                    native,
                    ownership: Some(ownership),
                },
                code,
            ),
        }
    }

    fn abort(
        &mut self,
        armed: &mut Self::Armed,
        failure_code: &'static str,
    ) -> DriverAbort<Self::Terminal> {
        match self
            .native
            .abort_armed(&armed.native, BurnReason::Failed, failure_code)
        {
            Ok(proof) => {
                let ownership = match Self::take_armed_ownership(armed) {
                    Ok(ownership) => ownership,
                    Err(code) => return DriverAbort::FaultHeld(code),
                };
                match self.finish_terminal(ownership, ValidatedNativeTerminalRun::Burned(proof)) {
                    Ok(terminal) => DriverAbort::Terminal(terminal),
                    Err(code) => DriverAbort::FaultHeld(code),
                }
            }
            Err(error) => DriverAbort::Retrying(error.code()),
        }
    }

    fn recover(&mut self, recovery: Self::Recovery) -> Result<Self::Terminal, &'static str> {
        self.native
            .recover_after_restart(
                &recovery.prepared,
                recovery.armed.as_ref(),
                &recovery.policy_snapshot,
            )
            .map_err(|error| error.code())
    }
}

enum WorkerCommand<P, R, X> {
    Start {
        key: BackgroundRunKey,
        prepared: P,
    },
    ConfirmArmed {
        key: BackgroundRunKey,
        receipt: R,
        reply: SyncSender<Result<(), &'static str>>,
    },
    Cancel {
        key: BackgroundRunKey,
        reply: SyncSender<Result<BackgroundCancelAcknowledgement, &'static str>>,
    },
    Recover {
        key: BackgroundRunKey,
        recovery: X,
    },
    Abort {
        key: BackgroundRunKey,
        failure_code: &'static str,
    },
    Shutdown,
}

enum WorkerEvent<R, T> {
    Armed {
        key: BackgroundRunKey,
        receipt: R,
    },
    Running {
        key: BackgroundRunKey,
        receipt: R,
    },
    Terminal {
        key: BackgroundRunKey,
        terminal: T,
    },
    Fault {
        key: Option<BackgroundRunKey>,
        code: &'static str,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum StartingControl {
    Active,
    TerminationPending,
    TerminationRecorded(NativeTerminationKind),
    AbortPending {
        failure_code: &'static str,
        exit_after: bool,
    },
}

enum WorkerState<D: BackgroundDriver> {
    Idle,
    Exit,
    FaultHeld {
        key: BackgroundRunKey,
        code: &'static str,
    },
    Starting {
        key: BackgroundRunKey,
        starting: D::Starting,
        control: StartingControl,
    },
    AwaitingArmedCommit {
        key: BackgroundRunKey,
        armed: D::Armed,
        termination: Option<NativeTerminationKind>,
    },
    Running {
        key: BackgroundRunKey,
        armed: D::Armed,
        termination: Option<NativeTerminationKind>,
    },
    ArmedAbortPending {
        key: BackgroundRunKey,
        armed: D::Armed,
        failure_code: &'static str,
        exit_after: bool,
    },
}

enum PublishedState<R, T> {
    Idle,
    Starting {
        key: BackgroundRunKey,
    },
    Armed {
        key: BackgroundRunKey,
        receipt: R,
    },
    Running {
        key: BackgroundRunKey,
        receipt: R,
    },
    Terminal {
        key: BackgroundRunKey,
        terminal: Option<T>,
    },
    TerminalTaken {
        key: BackgroundRunKey,
    },
    Fault {
        key: Option<BackgroundRunKey>,
        code: &'static str,
    },
}

#[derive(Debug)]
enum CorePoll<R, T> {
    Starting,
    Armed(R),
    Running,
    Terminal(T),
}

struct BackgroundCore<D: BackgroundDriver> {
    commands: Sender<WorkerCommand<D::Prepared, D::Receipt, D::Recovery>>,
    events: Receiver<WorkerEvent<D::Receipt, D::Terminal>>,
    worker_done: Receiver<()>,
    state: PublishedState<D::Receipt, D::Terminal>,
    worker: Option<JoinHandle<()>>,
    shutdown_requested: bool,
    _driver: PhantomData<D>,
}

struct BackgroundStartError<P> {
    code: &'static str,
    prepared: P,
}

impl<P> std::fmt::Debug for BackgroundStartError<P> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("BackgroundStartError")
            .field("code", &self.code)
            .finish_non_exhaustive()
    }
}

impl<D: BackgroundDriver> BackgroundCore<D> {
    fn spawn(driver: D, clock: Arc<dyn BackgroundClock>) -> Result<Self, &'static str> {
        let (command_tx, command_rx) = mpsc::channel();
        let (event_tx, event_rx) = mpsc::channel();
        let (worker_done_tx, worker_done_rx) = mpsc::sync_channel(1);
        let worker = thread::Builder::new()
            .name(BACKGROUND_THREAD_NAME.to_owned())
            .spawn(move || {
                let _completion = WorkerCompletionSignal(Some(worker_done_tx));
                worker_loop(driver, clock, command_rx, event_tx);
            })
            .map_err(|_| BACKGROUND_THREAD_SPAWN_FAILED)?;
        Ok(Self {
            commands: command_tx,
            events: event_rx,
            worker_done: worker_done_rx,
            state: PublishedState::Idle,
            worker: Some(worker),
            shutdown_requested: false,
            _driver: PhantomData,
        })
    }

    fn start(
        &mut self,
        key: BackgroundRunKey,
        prepared: D::Prepared,
    ) -> Result<(), BackgroundStartError<D::Prepared>> {
        if let Err(code) = self.drain_events() {
            return Err(BackgroundStartError { code, prepared });
        }
        if !matches!(
            self.state,
            PublishedState::Idle | PublishedState::TerminalTaken { .. }
        ) {
            return Err(BackgroundStartError {
                code: BACKGROUND_BUSY,
                prepared,
            });
        }
        self.state = PublishedState::Starting { key };
        if let Err(error) = self.commands.send(WorkerCommand::Start { key, prepared }) {
            let WorkerCommand::Start { prepared, .. } = error.0 else {
                unreachable!("start send error must return the start command")
            };
            self.state = PublishedState::Fault {
                key: Some(key),
                code: BACKGROUND_CHANNEL_CLOSED,
            };
            return Err(BackgroundStartError {
                code: BACKGROUND_CHANNEL_CLOSED,
                prepared,
            });
        }
        Ok(())
    }

    fn recover(
        &mut self,
        key: BackgroundRunKey,
        recovery: D::Recovery,
    ) -> Result<(), &'static str> {
        self.drain_events()?;
        if !matches!(
            self.state,
            PublishedState::Idle | PublishedState::TerminalTaken { .. }
        ) {
            return Err(BACKGROUND_BUSY);
        }
        self.state = PublishedState::Starting { key };
        if self
            .commands
            .send(WorkerCommand::Recover { key, recovery })
            .is_err()
        {
            self.state = PublishedState::Fault {
                key: Some(key),
                code: BACKGROUND_CHANNEL_CLOSED,
            };
            return Err(BACKGROUND_CHANNEL_CLOSED);
        }
        Ok(())
    }

    fn poll(
        &mut self,
        key: BackgroundRunKey,
        persisted_armed: Option<&D::Receipt>,
    ) -> Result<CorePoll<D::Receipt, D::Terminal>, &'static str> {
        self.drain_events()?;
        self.require_key(key)?;
        match &self.state {
            PublishedState::Starting { .. } => {
                if persisted_armed.is_some() {
                    Err(BACKGROUND_STATE_INVALID)
                } else {
                    Ok(CorePoll::Starting)
                }
            }
            PublishedState::Armed { receipt, .. } => match persisted_armed {
                None => Ok(CorePoll::Armed(receipt.clone())),
                Some(persisted) if persisted == receipt => {
                    self.confirm_armed(key, persisted.clone())?;
                    self.poll_after_confirm(key)
                }
                Some(_) => Err(BACKGROUND_ARMED_MISMATCH),
            },
            PublishedState::Running { receipt, .. } => match persisted_armed {
                Some(persisted) if persisted == receipt => Ok(CorePoll::Running),
                Some(_) => Err(BACKGROUND_ARMED_MISMATCH),
                None => Err(BACKGROUND_ARMED_REQUIRED),
            },
            PublishedState::Terminal { .. } => self.take_terminal(key).map(CorePoll::Terminal),
            PublishedState::TerminalTaken { .. } => Err(BACKGROUND_TERMINAL_TAKEN),
            PublishedState::Fault { code, .. } => Err(*code),
            PublishedState::Idle => Err(BACKGROUND_STATE_INVALID),
        }
    }

    fn request_cancel(
        &mut self,
        key: BackgroundRunKey,
        persisted_armed: Option<&D::Receipt>,
    ) -> Result<BackgroundCancelAcknowledgement, &'static str> {
        self.request_cancel_with_timeout(key, persisted_armed, WORKER_ACK_TIMEOUT)
    }

    fn request_cancel_with_timeout(
        &mut self,
        key: BackgroundRunKey,
        persisted_armed: Option<&D::Receipt>,
        acknowledgement_timeout: Duration,
    ) -> Result<BackgroundCancelAcknowledgement, &'static str> {
        self.drain_events()?;
        self.require_key(key)?;
        let requested_while_starting = matches!(self.state, PublishedState::Starting { .. });
        match &self.state {
            PublishedState::Starting { .. } => {
                if persisted_armed.is_some() {
                    return Err(BACKGROUND_STATE_INVALID);
                }
            }
            PublishedState::Armed { receipt, .. } => match persisted_armed {
                Some(persisted) if persisted == receipt => {
                    self.confirm_armed(key, persisted.clone())?;
                }
                Some(_) => return Err(BACKGROUND_ARMED_MISMATCH),
                None => return Err(BACKGROUND_ARMED_REQUIRED),
            },
            PublishedState::Running { receipt, .. } => match persisted_armed {
                Some(persisted) if persisted == receipt => {}
                Some(_) => return Err(BACKGROUND_ARMED_MISMATCH),
                None => return Err(BACKGROUND_ARMED_REQUIRED),
            },
            PublishedState::Terminal { .. } | PublishedState::TerminalTaken { .. } => {
                return Ok(BackgroundCancelAcknowledgement::AlreadyTerminal);
            }
            PublishedState::Fault { code, .. } => return Err(*code),
            PublishedState::Idle => return Err(BACKGROUND_STATE_INVALID),
        }
        self.drain_events()?;
        let can_queue = matches!(self.state, PublishedState::Running { .. })
            || (requested_while_starting
                && matches!(
                    self.state,
                    PublishedState::Starting { .. } | PublishedState::Armed { .. }
                ));
        if !can_queue {
            match &self.state {
                PublishedState::Terminal { .. } | PublishedState::TerminalTaken { .. } => {
                    return Ok(BackgroundCancelAcknowledgement::AlreadyTerminal);
                }
                PublishedState::Fault { code, .. } => return Err(*code),
                _ => return Err(BACKGROUND_STATE_INVALID),
            }
        }

        let (reply_tx, reply_rx) = mpsc::sync_channel(1);
        self.commands
            .send(WorkerCommand::Cancel {
                key,
                reply: reply_tx,
            })
            .map_err(|_| BACKGROUND_CHANNEL_CLOSED)?;
        match reply_rx.recv_timeout(acknowledgement_timeout) {
            Ok(reply) => {
                let drain_result = self.drain_events();
                match reply {
                    Ok(acknowledgement) => {
                        drain_result?;
                        Ok(acknowledgement)
                    }
                    Err(code) => {
                        if code == BACKGROUND_STATE_INVALID
                            && matches!(
                                self.state,
                                PublishedState::Terminal { .. }
                                    | PublishedState::TerminalTaken { .. }
                            )
                        {
                            Ok(BackgroundCancelAcknowledgement::AlreadyTerminal)
                        } else {
                            drain_result?;
                            Err(code)
                        }
                    }
                }
            }
            Err(RecvTimeoutError::Timeout | RecvTimeoutError::Disconnected) => {
                let drain_result = self.drain_events();
                match &self.state {
                    PublishedState::Terminal { .. } | PublishedState::TerminalTaken { .. } => {
                        Ok(BackgroundCancelAcknowledgement::AlreadyTerminal)
                    }
                    PublishedState::Fault { code, .. } => Err(*code),
                    _ => match drain_result {
                        Ok(()) | Err(BACKGROUND_CHANNEL_CLOSED) => {
                            Ok(BackgroundCancelAcknowledgement::Uncertain)
                        }
                        Err(code) => Err(code),
                    },
                }
            }
        }
    }

    fn poll_recovery(
        &mut self,
        key: BackgroundRunKey,
    ) -> Result<Option<D::Terminal>, &'static str> {
        self.drain_events()?;
        self.require_key(key)?;
        match &self.state {
            PublishedState::Starting { .. } => Ok(None),
            PublishedState::Terminal { .. } => self.take_terminal(key).map(Some),
            PublishedState::TerminalTaken { .. } => Err(BACKGROUND_TERMINAL_TAKEN),
            PublishedState::Fault { code, .. } => Err(*code),
            PublishedState::Idle
            | PublishedState::Armed { .. }
            | PublishedState::Running { .. } => Err(BACKGROUND_STATE_INVALID),
        }
    }

    fn abort_and_wait(
        &mut self,
        key: BackgroundRunKey,
        failure_code: &'static str,
    ) -> Result<D::Terminal, &'static str> {
        self.drain_events()?;
        self.require_key(key)?;
        if matches!(self.state, PublishedState::Terminal { .. }) {
            return self.take_terminal(key);
        }
        if matches!(self.state, PublishedState::TerminalTaken { .. }) {
            return Err(BACKGROUND_TERMINAL_TAKEN);
        }
        if let PublishedState::Fault { code, .. } = &self.state {
            return Err(*code);
        }
        self.commands
            .send(WorkerCommand::Abort { key, failure_code })
            .map_err(|_| BACKGROUND_CHANNEL_CLOSED)?;

        let deadline = std::time::Instant::now() + WORKER_ABORT_TIMEOUT;
        loop {
            let remaining = deadline
                .checked_duration_since(std::time::Instant::now())
                .ok_or(BACKGROUND_ABORT_TIMEOUT)?;
            let event = self
                .events
                .recv_timeout(remaining)
                .map_err(|error| match error {
                    RecvTimeoutError::Timeout => BACKGROUND_ABORT_TIMEOUT,
                    RecvTimeoutError::Disconnected => BACKGROUND_CHANNEL_CLOSED,
                })?;
            self.apply_event(event)?;
            match &self.state {
                PublishedState::Terminal { .. } => return self.take_terminal(key),
                PublishedState::Fault { code, .. } => return Err(*code),
                _ => {}
            }
        }
    }

    fn confirm_armed(
        &mut self,
        key: BackgroundRunKey,
        receipt: D::Receipt,
    ) -> Result<(), &'static str> {
        let (reply_tx, reply_rx) = mpsc::sync_channel(1);
        self.commands
            .send(WorkerCommand::ConfirmArmed {
                key,
                receipt,
                reply: reply_tx,
            })
            .map_err(|_| BACKGROUND_CHANNEL_CLOSED)?;
        reply_rx
            .recv_timeout(WORKER_ACK_TIMEOUT)
            .map_err(|_| BACKGROUND_ACK_TIMEOUT)??;
        self.drain_events()
    }

    fn poll_after_confirm(
        &mut self,
        key: BackgroundRunKey,
    ) -> Result<CorePoll<D::Receipt, D::Terminal>, &'static str> {
        self.drain_events()?;
        match &self.state {
            PublishedState::Running { .. } => Ok(CorePoll::Running),
            PublishedState::Terminal { .. } => self.take_terminal(key).map(CorePoll::Terminal),
            PublishedState::Fault { code, .. } => Err(*code),
            _ => Err(BACKGROUND_STATE_INVALID),
        }
    }

    fn require_key(&self, key: BackgroundRunKey) -> Result<(), &'static str> {
        let expected = match &self.state {
            PublishedState::Starting { key }
            | PublishedState::Armed { key, .. }
            | PublishedState::Running { key, .. }
            | PublishedState::Terminal { key, .. }
            | PublishedState::TerminalTaken { key } => Some(*key),
            PublishedState::Fault { key, .. } => *key,
            PublishedState::Idle => None,
        };
        if expected == Some(key) {
            Ok(())
        } else {
            Err(BACKGROUND_BINDING_MISMATCH)
        }
    }

    fn drain_events(&mut self) -> Result<(), &'static str> {
        loop {
            match self.events.try_recv() {
                Ok(event) => self.apply_event(event)?,
                Err(TryRecvError::Empty) => return Ok(()),
                Err(TryRecvError::Disconnected) => {
                    return match &self.state {
                        PublishedState::Terminal { .. } | PublishedState::TerminalTaken { .. } => {
                            Ok(())
                        }
                        PublishedState::Fault { code, .. } => Err(*code),
                        _ => Err(BACKGROUND_CHANNEL_CLOSED),
                    };
                }
            }
        }
    }

    fn apply_event(
        &mut self,
        event: WorkerEvent<D::Receipt, D::Terminal>,
    ) -> Result<(), &'static str> {
        match event {
            WorkerEvent::Armed { key, receipt } => {
                if !matches!(self.state, PublishedState::Starting { key: active } if active == key)
                {
                    return self.latch_fault(Some(key), BACKGROUND_STATE_INVALID);
                }
                self.state = PublishedState::Armed { key, receipt };
            }
            WorkerEvent::Running { key, receipt } => {
                if !matches!(self.state, PublishedState::Armed { key: active, .. } if active == key)
                {
                    return self.latch_fault(Some(key), BACKGROUND_STATE_INVALID);
                }
                self.state = PublishedState::Running { key, receipt };
            }
            WorkerEvent::Terminal { key, terminal } => {
                if !matches!(
                    self.state,
                    PublishedState::Starting { key: active }
                        | PublishedState::Armed { key: active, .. }
                        | PublishedState::Running { key: active, .. }
                        if active == key
                ) {
                    return self.latch_fault(Some(key), BACKGROUND_STATE_INVALID);
                }
                self.state = PublishedState::Terminal {
                    key,
                    terminal: Some(terminal),
                };
            }
            WorkerEvent::Fault { key, code } => {
                self.state = PublishedState::Fault { key, code };
            }
        }
        Ok(())
    }

    fn take_terminal(&mut self, key: BackgroundRunKey) -> Result<D::Terminal, &'static str> {
        let state = std::mem::replace(&mut self.state, PublishedState::TerminalTaken { key });
        match state {
            PublishedState::Terminal {
                key: terminal_key,
                terminal: Some(terminal),
            } if terminal_key == key => Ok(terminal),
            other => {
                self.state = other;
                Err(BACKGROUND_TERMINAL_TAKEN)
            }
        }
    }

    fn latch_fault<T>(
        &mut self,
        key: Option<BackgroundRunKey>,
        code: &'static str,
    ) -> Result<T, &'static str> {
        self.state = PublishedState::Fault { key, code };
        Err(code)
    }

    fn shutdown_and_wait(&mut self) -> Result<(), &'static str> {
        self.shutdown_and_wait_with_timeout(WORKER_SHUTDOWN_TIMEOUT)
    }

    fn shutdown_and_wait_with_timeout(&mut self, timeout: Duration) -> Result<(), &'static str> {
        if self.worker.is_none() {
            return self.validate_shutdown_state();
        }
        self.request_shutdown_once();

        let deadline = std::time::Instant::now() + timeout;
        let remaining = deadline
            .checked_duration_since(std::time::Instant::now())
            .ok_or(BACKGROUND_SHUTDOWN_TIMEOUT)?;
        match self.worker_done.recv_timeout(remaining) {
            Ok(()) | Err(RecvTimeoutError::Disconnected) => {}
            Err(RecvTimeoutError::Timeout) => return Err(BACKGROUND_SHUTDOWN_TIMEOUT),
        }

        loop {
            let finished = self
                .worker
                .as_ref()
                .map(JoinHandle::is_finished)
                .unwrap_or(true);
            if finished {
                break;
            }
            if std::time::Instant::now() >= deadline {
                return Err(BACKGROUND_SHUTDOWN_TIMEOUT);
            }
            thread::yield_now();
        }

        let worker = self.worker.take().ok_or(BACKGROUND_STATE_INVALID)?;
        let worker_panicked = worker.join().is_err();
        self.drain_shutdown_events()?;
        if worker_panicked {
            let key = match &self.state {
                PublishedState::Starting { key }
                | PublishedState::Armed { key, .. }
                | PublishedState::Running { key, .. }
                | PublishedState::Terminal { key, .. }
                | PublishedState::TerminalTaken { key } => Some(*key),
                PublishedState::Fault { key, .. } => *key,
                PublishedState::Idle => None,
            };
            self.state = PublishedState::Fault {
                key,
                code: BACKGROUND_CHANNEL_CLOSED,
            };
            Err(BACKGROUND_CHANNEL_CLOSED)
        } else {
            Ok(())
        }
    }

    fn drain_shutdown_events(&mut self) -> Result<(), &'static str> {
        loop {
            match self.events.try_recv() {
                Ok(event) => self.apply_event(event)?,
                Err(TryRecvError::Empty | TryRecvError::Disconnected) => break,
            }
        }
        self.validate_shutdown_state()
    }

    fn validate_shutdown_state(&mut self) -> Result<(), &'static str> {
        let active_key = match &self.state {
            PublishedState::Starting { key }
            | PublishedState::Armed { key, .. }
            | PublishedState::Running { key, .. } => Some(*key),
            _ => None,
        };
        if let Some(key) = active_key {
            self.state = PublishedState::Fault {
                key: Some(key),
                code: BACKGROUND_CHANNEL_CLOSED,
            };
            return Err(BACKGROUND_CHANNEL_CLOSED);
        }
        match &self.state {
            PublishedState::Fault { code, .. } => Err(*code),
            PublishedState::Idle
            | PublishedState::Terminal { .. }
            | PublishedState::TerminalTaken { .. } => Ok(()),
            PublishedState::Starting { .. }
            | PublishedState::Armed { .. }
            | PublishedState::Running { .. } => unreachable!("active state handled above"),
        }
    }

    fn request_shutdown_once(&mut self) {
        if !self.shutdown_requested {
            let _ = self.commands.send(WorkerCommand::Shutdown);
            self.shutdown_requested = true;
        }
    }
}

impl<D: BackgroundDriver> Drop for BackgroundCore<D> {
    fn drop(&mut self) {
        self.request_shutdown_once();
        let finished = self
            .worker
            .as_ref()
            .map(JoinHandle::is_finished)
            .unwrap_or(true);
        if finished {
            if let Some(worker) = self.worker.take() {
                let _ = worker.join();
            }
        } else {
            // Dropping a JoinHandle detaches the thread. The command and event
            // channels are then disconnected, so a responsive worker still
            // takes its normal shutdown/containment path without making Drop
            // an unbounded foreground wait.
            let _ = self.worker.take();
        }
    }
}

struct WorkerCompletionSignal(Option<SyncSender<()>>);

impl Drop for WorkerCompletionSignal {
    fn drop(&mut self) {
        if let Some(sender) = self.0.take() {
            let _ = sender.send(());
        }
    }
}

fn worker_loop<D: BackgroundDriver>(
    mut driver: D,
    clock: Arc<dyn BackgroundClock>,
    commands: Receiver<WorkerCommand<D::Prepared, D::Receipt, D::Recovery>>,
    events: Sender<WorkerEvent<D::Receipt, D::Terminal>>,
) {
    let mut state = WorkerState::<D>::Idle;
    loop {
        state = match state {
            WorkerState::Exit => return,
            WorkerState::Idle => match commands.recv() {
                Ok(WorkerCommand::Start { key, prepared }) => match driver.begin_start(prepared) {
                    Ok(starting) => WorkerState::Starting {
                        key,
                        starting,
                        control: StartingControl::Active,
                    },
                    Err(code) => {
                        let _ = events.send(WorkerEvent::Fault {
                            key: Some(key),
                            code,
                        });
                        WorkerState::FaultHeld { key, code }
                    }
                },
                Ok(WorkerCommand::Recover { key, recovery }) => {
                    match driver.recover(recovery) {
                        Ok(terminal) => {
                            let _ = events.send(WorkerEvent::Terminal { key, terminal });
                        }
                        Err(code) => {
                            let _ = events.send(WorkerEvent::Fault {
                                key: Some(key),
                                code,
                            });
                        }
                    }
                    WorkerState::Idle
                }
                Ok(WorkerCommand::Shutdown) | Err(_) => return,
                Ok(WorkerCommand::ConfirmArmed { reply, .. }) => {
                    let _ = reply.send(Err(BACKGROUND_STATE_INVALID));
                    WorkerState::Idle
                }
                Ok(WorkerCommand::Cancel { reply, .. }) => {
                    let _ = reply.send(Err(BACKGROUND_STATE_INVALID));
                    WorkerState::Idle
                }
                Ok(WorkerCommand::Abort { .. }) => WorkerState::Idle,
            },
            WorkerState::FaultHeld { key, code } => {
                match commands.recv_timeout(WORKER_POLL_INTERVAL) {
                    Ok(WorkerCommand::Cancel { reply, .. }) => {
                        let _ = reply.send(Err(code));
                        WorkerState::FaultHeld { key, code }
                    }
                    Ok(WorkerCommand::ConfirmArmed { reply, .. }) => {
                        let _ = reply.send(Err(code));
                        WorkerState::FaultHeld { key, code }
                    }
                    Ok(WorkerCommand::Shutdown) | Err(RecvTimeoutError::Disconnected) => {
                        WorkerState::Exit
                    }
                    Err(RecvTimeoutError::Timeout)
                    | Ok(WorkerCommand::Start { .. })
                    | Ok(WorkerCommand::Recover { .. })
                    | Ok(WorkerCommand::Abort { .. }) => WorkerState::FaultHeld { key, code },
                }
            }
            WorkerState::Starting {
                key,
                starting,
                control,
            } => match commands.recv_timeout(WORKER_POLL_INTERVAL) {
                Ok(WorkerCommand::Cancel {
                    key: command_key,
                    reply,
                }) if command_key == key => {
                    drive_starting_cancel(&mut driver, &events, key, starting, control, reply)
                }
                Ok(WorkerCommand::Cancel { reply, .. }) => {
                    let _ = reply.send(Err(BACKGROUND_BINDING_MISMATCH));
                    WorkerState::Starting {
                        key,
                        starting,
                        control,
                    }
                }
                Ok(WorkerCommand::Abort {
                    key: command_key,
                    failure_code,
                }) if command_key == key => {
                    drive_starting_abort(&mut driver, &events, key, starting, failure_code, false)
                }
                Ok(WorkerCommand::Shutdown) => drive_starting_abort(
                    &mut driver,
                    &events,
                    key,
                    starting,
                    BACKGROUND_SHUTDOWN,
                    true,
                ),
                Err(RecvTimeoutError::Disconnected) => {
                    let next = drive_starting_abort(
                        &mut driver,
                        &events,
                        key,
                        starting,
                        BACKGROUND_SHUTDOWN,
                        true,
                    );
                    if matches!(
                        &next,
                        WorkerState::Starting {
                            control: StartingControl::AbortPending { .. },
                            ..
                        }
                    ) {
                        thread::sleep(WORKER_POLL_INTERVAL);
                    }
                    next
                }
                Err(RecvTimeoutError::Timeout) => {
                    drive_starting(&mut driver, clock.as_ref(), &events, key, starting, control)
                }
                Ok(WorkerCommand::ConfirmArmed { reply, .. }) => {
                    let _ = reply.send(Err(BACKGROUND_STATE_INVALID));
                    WorkerState::Starting {
                        key,
                        starting,
                        control,
                    }
                }
                Ok(WorkerCommand::Start { .. })
                | Ok(WorkerCommand::Recover { .. })
                | Ok(WorkerCommand::Abort { .. }) => drive_starting_abort(
                    &mut driver,
                    &events,
                    key,
                    starting,
                    BACKGROUND_STATE_INVALID,
                    false,
                ),
            },
            WorkerState::AwaitingArmedCommit {
                key,
                armed,
                termination,
            } => match commands.recv_timeout(WORKER_POLL_INTERVAL) {
                Ok(WorkerCommand::ConfirmArmed {
                    key: command_key,
                    receipt,
                    reply,
                }) if command_key == key && receipt == *D::armed_receipt(&armed) => {
                    let published_receipt = D::armed_receipt(&armed).clone();
                    if events
                        .send(WorkerEvent::Running {
                            key,
                            receipt: published_receipt,
                        })
                        .is_err()
                    {
                        let _ = reply.send(Err(BACKGROUND_CHANNEL_CLOSED));
                        drive_armed_abort(
                            &mut driver,
                            &events,
                            key,
                            armed,
                            BACKGROUND_CHANNEL_CLOSED,
                            true,
                        )
                    } else {
                        let _ = reply.send(Ok(()));
                        WorkerState::Running {
                            key,
                            armed,
                            termination,
                        }
                    }
                }
                Ok(WorkerCommand::ConfirmArmed { reply, .. }) => {
                    let _ = reply.send(Err(BACKGROUND_ARMED_MISMATCH));
                    WorkerState::AwaitingArmedCommit {
                        key,
                        armed,
                        termination,
                    }
                }
                Ok(WorkerCommand::Cancel {
                    key: command_key,
                    reply,
                }) if command_key == key => drive_cancel_before_armed_commit(
                    &mut driver,
                    &events,
                    key,
                    armed,
                    termination,
                    reply,
                ),
                Ok(WorkerCommand::Cancel { reply, .. }) => {
                    let _ = reply.send(Err(BACKGROUND_BINDING_MISMATCH));
                    WorkerState::AwaitingArmedCommit {
                        key,
                        armed,
                        termination,
                    }
                }
                Ok(WorkerCommand::Abort {
                    key: command_key,
                    failure_code,
                }) if command_key == key => {
                    drive_armed_abort(&mut driver, &events, key, armed, failure_code, false)
                }
                Ok(WorkerCommand::Shutdown) | Err(RecvTimeoutError::Disconnected) => {
                    drive_armed_abort(&mut driver, &events, key, armed, BACKGROUND_SHUTDOWN, true)
                }
                Err(RecvTimeoutError::Timeout) => drive_awaiting_armed_commit(
                    &mut driver,
                    clock.as_ref(),
                    &events,
                    key,
                    armed,
                    termination,
                ),
                Ok(WorkerCommand::Start { .. })
                | Ok(WorkerCommand::Recover { .. })
                | Ok(WorkerCommand::Abort { .. }) => drive_armed_abort(
                    &mut driver,
                    &events,
                    key,
                    armed,
                    BACKGROUND_STATE_INVALID,
                    false,
                ),
            },
            WorkerState::Running {
                key,
                armed,
                termination,
            } => match commands.recv_timeout(WORKER_POLL_INTERVAL) {
                Ok(WorkerCommand::Cancel {
                    key: command_key,
                    reply,
                }) if command_key == key => {
                    drive_explicit_cancel(&mut driver, &events, key, armed, termination, reply)
                }
                Ok(WorkerCommand::Cancel { reply, .. }) => {
                    let _ = reply.send(Err(BACKGROUND_BINDING_MISMATCH));
                    WorkerState::Running {
                        key,
                        armed,
                        termination,
                    }
                }
                Ok(WorkerCommand::Abort {
                    key: command_key,
                    failure_code,
                }) if command_key == key => {
                    drive_armed_abort(&mut driver, &events, key, armed, failure_code, false)
                }
                Ok(WorkerCommand::Shutdown) | Err(RecvTimeoutError::Disconnected) => {
                    drive_armed_abort(&mut driver, &events, key, armed, BACKGROUND_SHUTDOWN, true)
                }
                Err(RecvTimeoutError::Timeout) => drive_running(
                    &mut driver,
                    clock.as_ref(),
                    &events,
                    key,
                    armed,
                    termination,
                ),
                Ok(WorkerCommand::ConfirmArmed { reply, .. }) => {
                    let _ = reply.send(Err(BACKGROUND_STATE_INVALID));
                    WorkerState::Running {
                        key,
                        armed,
                        termination,
                    }
                }
                Ok(WorkerCommand::Start { .. })
                | Ok(WorkerCommand::Recover { .. })
                | Ok(WorkerCommand::Abort { .. }) => drive_armed_abort(
                    &mut driver,
                    &events,
                    key,
                    armed,
                    BACKGROUND_STATE_INVALID,
                    false,
                ),
            },
            WorkerState::ArmedAbortPending {
                key,
                armed,
                failure_code,
                exit_after,
            } => match commands.recv_timeout(WORKER_POLL_INTERVAL) {
                Ok(WorkerCommand::Cancel { reply, .. }) => {
                    let _ = reply.send(Ok(BackgroundCancelAcknowledgement::Uncertain));
                    WorkerState::ArmedAbortPending {
                        key,
                        armed,
                        failure_code,
                        exit_after,
                    }
                }
                Ok(WorkerCommand::ConfirmArmed { reply, .. }) => {
                    let _ = reply.send(Err(BACKGROUND_STATE_INVALID));
                    WorkerState::ArmedAbortPending {
                        key,
                        armed,
                        failure_code,
                        exit_after,
                    }
                }
                Ok(WorkerCommand::Shutdown) | Err(RecvTimeoutError::Disconnected) => {
                    drive_armed_abort(&mut driver, &events, key, armed, failure_code, true)
                }
                Err(RecvTimeoutError::Timeout)
                | Ok(WorkerCommand::Start { .. })
                | Ok(WorkerCommand::Recover { .. })
                | Ok(WorkerCommand::Abort { .. }) => {
                    drive_armed_abort(&mut driver, &events, key, armed, failure_code, exit_after)
                }
            },
        };
    }
}

#[allow(clippy::too_many_arguments)]
fn drive_starting_cancel<D: BackgroundDriver>(
    driver: &mut D,
    events: &Sender<WorkerEvent<D::Receipt, D::Terminal>>,
    key: BackgroundRunKey,
    mut starting: D::Starting,
    control: StartingControl,
    reply: SyncSender<Result<BackgroundCancelAcknowledgement, &'static str>>,
) -> WorkerState<D> {
    match control {
        StartingControl::TerminationRecorded(kind) => {
            let _ = reply.send(Ok(BackgroundCancelAcknowledgement::AlreadyRecorded(kind)));
            return WorkerState::Starting {
                key,
                starting,
                control,
            };
        }
        StartingControl::AbortPending { .. } => {
            let _ = reply.send(Ok(BackgroundCancelAcknowledgement::Uncertain));
            return WorkerState::Starting {
                key,
                starting,
                control,
            };
        }
        StartingControl::Active | StartingControl::TerminationPending => {}
    }

    match driver.request_starting_termination(&mut starting) {
        Ok(DriverStartingTerminationAcknowledgement::Recorded(kind)) => {
            let acknowledgement = if control == StartingControl::TerminationPending {
                BackgroundCancelAcknowledgement::AlreadyRecorded(kind)
            } else {
                BackgroundCancelAcknowledgement::Recorded(kind)
            };
            let _ = reply.send(Ok(acknowledgement));
            WorkerState::Starting {
                key,
                starting,
                control: StartingControl::TerminationRecorded(kind),
            }
        }
        Ok(DriverStartingTerminationAcknowledgement::Uncertain) => {
            let _ = reply.send(Ok(BackgroundCancelAcknowledgement::Uncertain));
            WorkerState::Starting {
                key,
                starting,
                control: StartingControl::TerminationPending,
            }
        }
        Err(code) => {
            let _ = reply.send(Err(code));
            drive_starting_abort(driver, events, key, starting, code, false)
        }
    }
}

fn drive_starting<D: BackgroundDriver>(
    driver: &mut D,
    clock: &dyn BackgroundClock,
    events: &Sender<WorkerEvent<D::Receipt, D::Terminal>>,
    key: BackgroundRunKey,
    mut starting: D::Starting,
    mut control: StartingControl,
) -> WorkerState<D> {
    match control {
        StartingControl::AbortPending {
            failure_code,
            exit_after,
        } => {
            return drive_starting_abort(driver, events, key, starting, failure_code, exit_after);
        }
        StartingControl::TerminationPending => {
            match driver.request_starting_termination(&mut starting) {
                Ok(DriverStartingTerminationAcknowledgement::Recorded(kind)) => {
                    control = StartingControl::TerminationRecorded(kind);
                }
                Ok(DriverStartingTerminationAcknowledgement::Uncertain) => {
                    return WorkerState::Starting {
                        key,
                        starting,
                        control,
                    };
                }
                Err(code) => {
                    return drive_starting_abort(driver, events, key, starting, code, false);
                }
            }
        }
        StartingControl::Active => {
            let now = match clock.now_unix_seconds() {
                Ok(value) => value,
                Err(code) => {
                    return drive_starting_abort(driver, events, key, starting, code, false);
                }
            };
            if now >= D::starting_deadline(&starting) {
                match driver.request_starting_termination(&mut starting) {
                    Ok(DriverStartingTerminationAcknowledgement::Recorded(kind)) => {
                        control = StartingControl::TerminationRecorded(kind);
                    }
                    Ok(DriverStartingTerminationAcknowledgement::Uncertain) => {
                        return WorkerState::Starting {
                            key,
                            starting,
                            control: StartingControl::TerminationPending,
                        };
                    }
                    Err(code) => {
                        return drive_starting_abort(driver, events, key, starting, code, false);
                    }
                }
            }
        }
        StartingControl::TerminationRecorded(_) => {}
    }

    match driver.advance_starting(starting) {
        DriverStartingAdvance::Starting(starting) => WorkerState::Starting {
            key,
            starting,
            control,
        },
        DriverStartingAdvance::Retrying(starting, _code) => WorkerState::Starting {
            key,
            starting,
            control,
        },
        DriverStartingAdvance::Armed(armed) if control == StartingControl::Active => {
            let receipt = D::armed_receipt(&armed).clone();
            if events.send(WorkerEvent::Armed { key, receipt }).is_err() {
                return drive_armed_abort(
                    driver,
                    events,
                    key,
                    armed,
                    BACKGROUND_CHANNEL_CLOSED,
                    true,
                );
            }
            WorkerState::AwaitingArmedCommit {
                key,
                armed,
                termination: None,
            }
        }
        DriverStartingAdvance::Armed(armed) => {
            // A durable or uncertain starting termination intent is a one-way
            // boundary. Returning Armed could start fresh work after that
            // boundary, so contain the run instead of publishing the receipt.
            drive_armed_abort(driver, events, key, armed, BACKGROUND_STATE_INVALID, false)
        }
        DriverStartingAdvance::Terminal(terminal) => {
            let _ = events.send(WorkerEvent::Terminal { key, terminal });
            WorkerState::Idle
        }
        DriverStartingAdvance::FaultHeld(code) => {
            let _ = events.send(WorkerEvent::Fault {
                key: Some(key),
                code,
            });
            WorkerState::FaultHeld { key, code }
        }
    }
}

fn drive_starting_abort<D: BackgroundDriver>(
    driver: &mut D,
    events: &Sender<WorkerEvent<D::Receipt, D::Terminal>>,
    key: BackgroundRunKey,
    mut starting: D::Starting,
    failure_code: &'static str,
    exit_after: bool,
) -> WorkerState<D> {
    match driver.abort_starting(&mut starting, failure_code) {
        DriverAbort::Terminal(terminal) => {
            let _ = events.send(WorkerEvent::Terminal { key, terminal });
            if exit_after {
                WorkerState::Exit
            } else {
                WorkerState::Idle
            }
        }
        DriverAbort::Retrying(_) => WorkerState::Starting {
            key,
            starting,
            control: StartingControl::AbortPending {
                failure_code,
                exit_after,
            },
        },
        DriverAbort::FaultHeld(code) => {
            let _ = events.send(WorkerEvent::Fault {
                key: Some(key),
                code,
            });
            if exit_after {
                WorkerState::Exit
            } else {
                WorkerState::FaultHeld { key, code }
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn drive_explicit_cancel<D: BackgroundDriver>(
    driver: &mut D,
    events: &Sender<WorkerEvent<D::Receipt, D::Terminal>>,
    key: BackgroundRunKey,
    mut armed: D::Armed,
    mut termination: Option<NativeTerminationKind>,
    reply: SyncSender<Result<BackgroundCancelAcknowledgement, &'static str>>,
) -> WorkerState<D> {
    if let Some(kind) = termination {
        let _ = reply.send(Ok(BackgroundCancelAcknowledgement::AlreadyRecorded(kind)));
        return WorkerState::Running {
            key,
            armed,
            termination,
        };
    }
    match driver.request_termination(&mut armed) {
        Ok(DriverArmedTerminationAcknowledgement::Recorded(kind)) => {
            termination = Some(kind);
            let _ = reply.send(Ok(BackgroundCancelAcknowledgement::Recorded(kind)));
            WorkerState::Running {
                key,
                armed,
                termination,
            }
        }
        Ok(DriverArmedTerminationAcknowledgement::Uncertain) => {
            let _ = reply.send(Ok(BackgroundCancelAcknowledgement::Uncertain));
            WorkerState::Running {
                key,
                armed,
                termination,
            }
        }
        Err(code) => {
            let _ = reply.send(Err(code));
            drive_armed_abort(driver, events, key, armed, code, false)
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn drive_cancel_before_armed_commit<D: BackgroundDriver>(
    driver: &mut D,
    events: &Sender<WorkerEvent<D::Receipt, D::Terminal>>,
    key: BackgroundRunKey,
    mut armed: D::Armed,
    mut termination: Option<NativeTerminationKind>,
    reply: SyncSender<Result<BackgroundCancelAcknowledgement, &'static str>>,
) -> WorkerState<D> {
    if let Some(kind) = termination {
        let _ = reply.send(Ok(BackgroundCancelAcknowledgement::AlreadyRecorded(kind)));
        return WorkerState::AwaitingArmedCommit {
            key,
            armed,
            termination,
        };
    }
    match driver.request_termination(&mut armed) {
        Ok(DriverArmedTerminationAcknowledgement::Recorded(kind)) => {
            termination = Some(kind);
            let _ = reply.send(Ok(BackgroundCancelAcknowledgement::Recorded(kind)));
            WorkerState::AwaitingArmedCommit {
                key,
                armed,
                termination,
            }
        }
        Ok(DriverArmedTerminationAcknowledgement::Uncertain) => {
            let _ = reply.send(Ok(BackgroundCancelAcknowledgement::Uncertain));
            WorkerState::AwaitingArmedCommit {
                key,
                armed,
                termination,
            }
        }
        Err(code) => {
            let _ = reply.send(Err(code));
            drive_armed_abort(driver, events, key, armed, code, false)
        }
    }
}

fn drive_awaiting_armed_commit<D: BackgroundDriver>(
    driver: &mut D,
    clock: &dyn BackgroundClock,
    events: &Sender<WorkerEvent<D::Receipt, D::Terminal>>,
    key: BackgroundRunKey,
    mut armed: D::Armed,
    mut termination: Option<NativeTerminationKind>,
) -> WorkerState<D> {
    if termination.is_none() {
        let now = match clock.now_unix_seconds() {
            Ok(value) => value,
            Err(code) => {
                return drive_armed_abort(driver, events, key, armed, code, false);
            }
        };
        if now < D::deadline(&armed) {
            return WorkerState::AwaitingArmedCommit {
                key,
                armed,
                termination,
            };
        }
        match driver.request_termination(&mut armed) {
            Ok(DriverArmedTerminationAcknowledgement::Recorded(kind)) => {
                termination = Some(kind);
            }
            Ok(DriverArmedTerminationAcknowledgement::Uncertain) => {
                return WorkerState::AwaitingArmedCommit {
                    key,
                    armed,
                    termination,
                };
            }
            Err(code) => {
                return drive_armed_abort(driver, events, key, armed, code, false);
            }
        }
    }

    match driver.advance(armed) {
        DriverAdvance::Running(armed) | DriverAdvance::Retrying(armed, _) => {
            WorkerState::AwaitingArmedCommit {
                key,
                armed,
                termination,
            }
        }
        DriverAdvance::Terminal(terminal) => {
            let _ = events.send(WorkerEvent::Terminal { key, terminal });
            WorkerState::Idle
        }
        DriverAdvance::FaultHeld(code) => {
            let _ = events.send(WorkerEvent::Fault {
                key: Some(key),
                code,
            });
            WorkerState::FaultHeld { key, code }
        }
    }
}

fn drive_running<D: BackgroundDriver>(
    driver: &mut D,
    clock: &dyn BackgroundClock,
    events: &Sender<WorkerEvent<D::Receipt, D::Terminal>>,
    key: BackgroundRunKey,
    mut armed: D::Armed,
    mut termination: Option<NativeTerminationKind>,
) -> WorkerState<D> {
    let now = match clock.now_unix_seconds() {
        Ok(value) => value,
        Err(code) => {
            return drive_armed_abort(driver, events, key, armed, code, false);
        }
    };
    if termination.is_none() && now >= D::deadline(&armed) {
        match driver.request_termination(&mut armed) {
            Ok(DriverArmedTerminationAcknowledgement::Recorded(kind)) => termination = Some(kind),
            Ok(DriverArmedTerminationAcknowledgement::Uncertain) => {
                return WorkerState::Running {
                    key,
                    armed,
                    termination,
                };
            }
            Err(code) => {
                return drive_armed_abort(driver, events, key, armed, code, false);
            }
        }
    }
    match driver.advance(armed) {
        DriverAdvance::Running(armed) => WorkerState::Running {
            key,
            armed,
            termination,
        },
        DriverAdvance::Terminal(terminal) => {
            let _ = events.send(WorkerEvent::Terminal { key, terminal });
            WorkerState::Idle
        }
        DriverAdvance::Retrying(armed, _code) => WorkerState::Running {
            key,
            armed,
            termination,
        },
        DriverAdvance::FaultHeld(code) => {
            let _ = events.send(WorkerEvent::Fault {
                key: Some(key),
                code,
            });
            WorkerState::FaultHeld { key, code }
        }
    }
}

fn drive_armed_abort<D: BackgroundDriver>(
    driver: &mut D,
    events: &Sender<WorkerEvent<D::Receipt, D::Terminal>>,
    key: BackgroundRunKey,
    mut armed: D::Armed,
    failure_code: &'static str,
    exit_after: bool,
) -> WorkerState<D> {
    match driver.abort(&mut armed, failure_code) {
        DriverAbort::Terminal(terminal) => {
            let _ = events.send(WorkerEvent::Terminal { key, terminal });
            if exit_after {
                WorkerState::Exit
            } else {
                WorkerState::Idle
            }
        }
        DriverAbort::Retrying(_) => WorkerState::ArmedAbortPending {
            key,
            armed,
            failure_code,
            exit_after,
        },
        DriverAbort::FaultHeld(code) => {
            let _ = events.send(WorkerEvent::Fault {
                key: Some(key),
                code,
            });
            if exit_after {
                WorkerState::Exit
            } else {
                WorkerState::FaultHeld { key, code }
            }
        }
    }
}

/// Foreground facade.  Only receipts and final proofs cross the channel; the
/// native API, supervisor, and non-Clone armed capability stay in one worker.
type SharedNativeBackgroundCore<A> = Arc<Mutex<BackgroundCore<NativeDriver<A>>>>;

pub(crate) struct BackgroundNativeStartSink<
    A: ServiceOwnedNativeApi + ServiceOwnedStagedNativeApi + 'static,
> {
    core: SharedNativeBackgroundCore<A>,
    failed_handoff: Arc<Mutex<Vec<OwnedBackgroundRun>>>,
}

impl<A: ServiceOwnedNativeApi + ServiceOwnedStagedNativeApi + 'static> Clone
    for BackgroundNativeStartSink<A>
{
    fn clone(&self) -> Self {
        Self {
            core: Arc::clone(&self.core),
            failed_handoff: Arc::clone(&self.failed_handoff),
        }
    }
}

impl<A: ServiceOwnedNativeApi + ServiceOwnedStagedNativeApi + 'static>
    BackgroundNativeStartSink<A>
{
    pub(crate) fn enqueue(
        &self,
        run: OwnedBackgroundRun,
    ) -> Result<BackgroundRunKey, SupervisorError> {
        let key = run.key();
        let mut core = match self.core.lock() {
            Ok(core) => core,
            Err(_) => {
                self.retain_failed_handoff(run);
                return Err(SupervisorError::new(BACKGROUND_CORE_LOCK_FAILED));
            }
        };
        match core.start(key, run) {
            Ok(()) => Ok(key),
            Err(error) => {
                let code = error.code;
                self.retain_failed_handoff(error.prepared);
                Err(SupervisorError::new(code))
            }
        }
    }

    fn retain_failed_handoff(&self, run: OwnedBackgroundRun) {
        match self.failed_handoff.lock() {
            Ok(mut retained) => retained.push(run),
            Err(poisoned) => poisoned.into_inner().push(run),
        }
    }
}

pub(crate) struct BackgroundNativeSupervisor<
    A: ServiceOwnedNativeApi + ServiceOwnedStagedNativeApi + 'static,
> {
    core: SharedNativeBackgroundCore<A>,
    failed_handoff: Arc<Mutex<Vec<OwnedBackgroundRun>>>,
}

impl<A: ServiceOwnedNativeApi + ServiceOwnedStagedNativeApi + 'static>
    BackgroundNativeSupervisor<A>
{
    pub(crate) fn new(api: A) -> Result<Self, SupervisorError> {
        BackgroundCore::spawn(
            NativeDriver {
                native: ServiceOwnedNativeSupervisor::new(api),
                fault_held: None,
            },
            Arc::new(SystemClock),
        )
        .map(|core| Self {
            core: Arc::new(Mutex::new(core)),
            failed_handoff: Arc::new(Mutex::new(Vec::new())),
        })
        .map_err(SupervisorError::new)
    }

    pub(crate) fn start_sink(&self) -> BackgroundNativeStartSink<A> {
        BackgroundNativeStartSink {
            core: Arc::clone(&self.core),
            failed_handoff: Arc::clone(&self.failed_handoff),
        }
    }

    pub(crate) fn poll(
        &mut self,
        key: BackgroundRunKey,
        persisted_armed: Option<&ArmedRecoveryReceipt>,
    ) -> Result<BackgroundNativePoll, SupervisorError> {
        self.core
            .lock()
            .map_err(|_| SupervisorError::new(BACKGROUND_CORE_LOCK_FAILED))?
            .poll(key, persisted_armed)
            .map(|poll| match poll {
                CorePoll::Starting => BackgroundNativePoll::Starting,
                CorePoll::Armed(receipt) => BackgroundNativePoll::Armed(receipt),
                CorePoll::Running => BackgroundNativePoll::Running,
                CorePoll::Terminal(terminal) => BackgroundNativePoll::Terminal(terminal),
            })
            .map_err(SupervisorError::new)
    }

    pub(crate) fn request_cancel(
        &mut self,
        key: BackgroundRunKey,
        persisted_armed: Option<&ArmedRecoveryReceipt>,
    ) -> Result<BackgroundCancelAcknowledgement, SupervisorError> {
        self.core
            .lock()
            .map_err(|_| SupervisorError::new(BACKGROUND_CORE_LOCK_FAILED))?
            .request_cancel(key, persisted_armed)
            .map_err(SupervisorError::new)
    }

    pub(crate) fn begin_recovery(
        &mut self,
        prepared: PreparedRecoveryReceipt,
        armed: Option<ArmedRecoveryReceipt>,
        policy_snapshot: Vec<u8>,
    ) -> Result<BackgroundRunKey, SupervisorError> {
        let key = BackgroundRunKey::from_persisted(&prepared);
        self.core
            .lock()
            .map_err(|_| SupervisorError::new(BACKGROUND_CORE_LOCK_FAILED))?
            .recover(
                key,
                NativeRecoveryRequest {
                    prepared,
                    armed,
                    policy_snapshot,
                },
            )
            .map(|_| key)
            .map_err(SupervisorError::new)
    }

    pub(crate) fn poll_recovery(
        &mut self,
        key: BackgroundRunKey,
    ) -> Result<BackgroundNativeRecoveryPoll, SupervisorError> {
        self.core
            .lock()
            .map_err(|_| SupervisorError::new(BACKGROUND_CORE_LOCK_FAILED))?
            .poll_recovery(key)
            .map(|terminal| match terminal {
                Some(terminal) => BackgroundNativeRecoveryPoll::Terminal(terminal),
                None => BackgroundNativeRecoveryPoll::Recovering,
            })
            .map_err(SupervisorError::new)
    }

    pub(crate) fn abort_and_wait_cleanup(
        &mut self,
        key: BackgroundRunKey,
        failure_code: &'static str,
    ) -> Result<NativeBurnedRunProof, SupervisorError> {
        match self
            .core
            .lock()
            .map_err(|_| SupervisorError::new(BACKGROUND_CORE_LOCK_FAILED))?
            .abort_and_wait(key, failure_code)
            .map_err(SupervisorError::new)?
        {
            ValidatedNativeTerminalRun::Burned(proof) => Ok(proof),
            ValidatedNativeTerminalRun::Completed(_) => Err(SupervisorError::new(
                "authority_native_background_abort_returned_completed",
            )),
        }
    }

    pub(crate) fn recover_and_wait_cleanup(
        &mut self,
        prepared: PreparedRecoveryReceipt,
        armed: Option<ArmedRecoveryReceipt>,
        policy_snapshot: Vec<u8>,
    ) -> Result<ValidatedNativeTerminalRun, SupervisorError> {
        let key = self.begin_recovery(prepared, armed, policy_snapshot)?;
        let deadline = Instant::now() + WORKER_ABORT_TIMEOUT;
        loop {
            match self.poll_recovery(key)? {
                BackgroundNativeRecoveryPoll::Recovering if Instant::now() < deadline => {
                    thread::sleep(WORKER_POLL_INTERVAL);
                }
                BackgroundNativeRecoveryPoll::Recovering => {
                    return Err(SupervisorError::new(BACKGROUND_ABORT_TIMEOUT));
                }
                BackgroundNativeRecoveryPoll::Terminal(terminal) => return Ok(terminal),
            }
        }
    }

    pub(crate) fn shutdown_and_wait(&mut self) -> Result<(), SupervisorError> {
        self.core
            .lock()
            .map_err(|_| SupervisorError::new(BACKGROUND_CORE_LOCK_FAILED))?
            .shutdown_and_wait()
            .map_err(SupervisorError::new)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::primitive_evidence_authority_supervisor::BurnedRunProof;
    use std::fs::{self, OpenOptions};
    use std::os::windows::fs::OpenOptionsExt;
    use std::panic::{catch_unwind, AssertUnwindSafe};
    use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
    use std::sync::Mutex;
    use windows_sys::Win32::Storage::FileSystem::FILE_SHARE_READ;

    static WORKER_HANDLE_FIXTURE_COUNTER: AtomicU64 = AtomicU64::new(1);

    #[derive(Clone)]
    struct TestClock(Arc<AtomicU64>);

    impl BackgroundClock for TestClock {
        fn now_unix_seconds(&self) -> Result<u64, &'static str> {
            Ok(self.0.load(Ordering::SeqCst))
        }
    }

    #[derive(Debug)]
    struct TestStarting {
        receipt: u64,
        deadline: u64,
        remaining_actions: usize,
        termination: Option<NativeTerminationKind>,
    }

    #[derive(Debug)]
    struct TestArmed {
        receipt: u64,
        deadline: u64,
        capability_id: u64,
    }

    #[derive(Debug, PartialEq, Eq)]
    enum TestTerminal {
        Completed,
        Cancelled,
        TimedOut,
        Failed,
    }

    #[derive(Default)]
    struct TestTrace {
        starting_advances: usize,
        starting_actions: usize,
        starting_retries: usize,
        advances: usize,
        armed_retries: usize,
        armed_capability_ids: Vec<u64>,
        intents: Vec<NativeTerminationKind>,
        aborts: usize,
        abort_fault: Option<&'static str>,
        recoveries: usize,
    }

    struct TestDriver {
        trace: Arc<Mutex<TestTrace>>,
        now: Arc<AtomicU64>,
        start_entered: Option<Arc<AtomicBool>>,
        start_gate: Option<Arc<AtomicBool>>,
        intent_gate: Option<Arc<AtomicBool>>,
        starting_termination_available: Option<Arc<AtomicBool>>,
        starting_advance_available: Option<Arc<AtomicBool>>,
        starting_abort_available: Option<Arc<AtomicBool>>,
        starting_actions: usize,
        starting_kind_override: Option<NativeTerminationKind>,
        pending_polls: usize,
        fail_intent: bool,
    }

    impl BackgroundDriver for TestDriver {
        type Prepared = (u64, u64);
        type Recovery = TestTerminal;
        type Starting = TestStarting;
        type Armed = TestArmed;
        type Receipt = u64;
        type Terminal = TestTerminal;

        fn begin_start(
            &mut self,
            (receipt, deadline): Self::Prepared,
        ) -> Result<Self::Starting, &'static str> {
            if let Some(entered) = &self.start_entered {
                entered.store(true, Ordering::SeqCst);
            }
            if let Some(gate) = &self.start_gate {
                while !gate.load(Ordering::SeqCst) {
                    thread::yield_now();
                }
            }
            Ok(TestStarting {
                receipt,
                deadline,
                remaining_actions: self.starting_actions,
                termination: None,
            })
        }

        fn starting_deadline(starting: &Self::Starting) -> u64 {
            starting.deadline
        }

        fn request_starting_termination(
            &mut self,
            starting: &mut Self::Starting,
        ) -> Result<DriverStartingTerminationAcknowledgement, &'static str> {
            if let Some(gate) = &self.intent_gate {
                while !gate.load(Ordering::SeqCst) {
                    thread::yield_now();
                }
            }
            if self
                .starting_termination_available
                .as_ref()
                .is_some_and(|available| !available.load(Ordering::SeqCst))
            {
                return Ok(DriverStartingTerminationAcknowledgement::Uncertain);
            }
            let kind = self.starting_kind_override.unwrap_or_else(|| {
                if self.now.load(Ordering::SeqCst) >= starting.deadline {
                    NativeTerminationKind::TimedOut
                } else {
                    NativeTerminationKind::Cancelled
                }
            });
            self.trace.lock().expect("trace lock").intents.push(kind);
            if self.fail_intent {
                Err("test_intent_failure")
            } else {
                starting.termination = Some(kind);
                Ok(DriverStartingTerminationAcknowledgement::Recorded(kind))
            }
        }

        fn advance_starting(
            &mut self,
            mut starting: Self::Starting,
        ) -> DriverStartingAdvance<Self::Starting, Self::Armed, Self::Terminal> {
            let mut trace = self.trace.lock().expect("trace lock");
            trace.starting_advances += 1;
            if self
                .starting_advance_available
                .as_ref()
                .is_some_and(|available| !available.load(Ordering::SeqCst))
            {
                trace.starting_retries += 1;
                return DriverStartingAdvance::Retrying(
                    starting,
                    "test_starting_advance_uncertain",
                );
            }
            if let Some(kind) = starting.termination {
                return DriverStartingAdvance::Terminal(match kind {
                    NativeTerminationKind::Cancelled => TestTerminal::Cancelled,
                    NativeTerminationKind::TimedOut => TestTerminal::TimedOut,
                });
            }
            trace.starting_actions += 1;
            drop(trace);
            if starting.remaining_actions > 1 {
                starting.remaining_actions -= 1;
                DriverStartingAdvance::Starting(starting)
            } else {
                DriverStartingAdvance::Armed(TestArmed {
                    receipt: starting.receipt,
                    deadline: starting.deadline,
                    capability_id: starting.receipt ^ 0xa5a5_a5a5_a5a5_a5a5,
                })
            }
        }

        fn abort_starting(
            &mut self,
            _starting: &mut Self::Starting,
            _failure_code: &'static str,
        ) -> DriverAbort<Self::Terminal> {
            let abort_fault = {
                let mut trace = self.trace.lock().expect("trace lock");
                trace.aborts += 1;
                trace.abort_fault
            };
            if let Some(code) = abort_fault {
                return DriverAbort::FaultHeld(code);
            }
            if self
                .starting_abort_available
                .as_ref()
                .is_some_and(|available| !available.load(Ordering::SeqCst))
            {
                DriverAbort::Retrying("test_starting_abort_uncertain")
            } else {
                DriverAbort::Terminal(TestTerminal::Failed)
            }
        }

        fn armed_receipt(armed: &Self::Armed) -> &Self::Receipt {
            &armed.receipt
        }

        fn deadline(armed: &Self::Armed) -> u64 {
            armed.deadline
        }

        fn request_termination(
            &mut self,
            armed: &mut Self::Armed,
        ) -> Result<DriverArmedTerminationAcknowledgement, &'static str> {
            if let Some(gate) = &self.intent_gate {
                while !gate.load(Ordering::SeqCst) {
                    thread::yield_now();
                }
            }
            if self
                .starting_termination_available
                .as_ref()
                .is_some_and(|available| !available.load(Ordering::SeqCst))
            {
                return Ok(DriverArmedTerminationAcknowledgement::Uncertain);
            }
            let kind = self.starting_kind_override.unwrap_or_else(|| {
                if self.now.load(Ordering::SeqCst) >= armed.deadline {
                    NativeTerminationKind::TimedOut
                } else {
                    NativeTerminationKind::Cancelled
                }
            });
            self.trace.lock().expect("trace lock").intents.push(kind);
            if self.fail_intent {
                Err("test_intent_failure")
            } else {
                Ok(DriverArmedTerminationAcknowledgement::Recorded(kind))
            }
        }

        fn advance(&mut self, armed: Self::Armed) -> DriverAdvance<Self::Armed, Self::Terminal> {
            let mut trace = self.trace.lock().expect("trace lock");
            trace.advances += 1;
            trace.armed_capability_ids.push(armed.capability_id);
            if self.pending_polls > 0 {
                self.pending_polls -= 1;
                trace.armed_retries += 1;
                return DriverAdvance::Retrying(armed, "test_armed_advance_retry");
            }
            let terminal = match trace.intents.last() {
                Some(NativeTerminationKind::Cancelled) => TestTerminal::Cancelled,
                Some(NativeTerminationKind::TimedOut) => TestTerminal::TimedOut,
                None => TestTerminal::Completed,
            };
            DriverAdvance::Terminal(terminal)
        }

        fn abort(
            &mut self,
            _armed: &mut Self::Armed,
            _failure_code: &'static str,
        ) -> DriverAbort<Self::Terminal> {
            let abort_fault = {
                let mut trace = self.trace.lock().expect("trace lock");
                trace.aborts += 1;
                trace.abort_fault
            };
            if let Some(code) = abort_fault {
                return DriverAbort::FaultHeld(code);
            }
            if self
                .starting_abort_available
                .as_ref()
                .is_some_and(|available| !available.load(Ordering::SeqCst))
            {
                DriverAbort::Retrying("test_armed_abort_uncertain")
            } else {
                DriverAbort::Terminal(TestTerminal::Failed)
            }
        }

        fn recover(&mut self, recovery: Self::Recovery) -> Result<Self::Terminal, &'static str> {
            self.trace.lock().expect("trace lock").recoveries += 1;
            Ok(recovery)
        }
    }

    fn key(byte: u8) -> BackgroundRunKey {
        BackgroundRunKey([byte; 32])
    }

    struct TestTerminalLease {
        accepts: bool,
        release_error: Option<&'static str>,
        drops: Arc<AtomicUsize>,
        releases: Arc<AtomicUsize>,
        worker_drops: Arc<AtomicUsize>,
    }

    impl Drop for TestTerminalLease {
        fn drop(&mut self) {
            self.drops.fetch_add(1, Ordering::SeqCst);
        }
    }

    impl BackgroundTerminalLease for TestTerminalLease {
        fn take_worker_handles(&mut self) -> Result<WorkerScenarioHandleBundle, &'static str> {
            Err("test_worker_handle_handoff_unconfigured")
        }

        fn consume_start_authorization(
            &mut self,
            worker_handles: &WorkerScenarioHandleBundle,
            prepared_receipt_digest: Digest,
            policy_snapshot_digest: Digest,
        ) -> Result<VerifiedScenarioStartContract, &'static str> {
            worker_handles
                .verified_start_capability_for_test()
                .into_owned_contract(prepared_receipt_digest, policy_snapshot_digest)
                .map_err(|error| error.code())
        }

        fn finalize_terminal(
            &mut self,
            worker_handles: &mut Option<WorkerScenarioHandleBundle>,
            _terminal: &ValidatedNativeTerminalRun,
        ) -> Result<(), &'static str> {
            if self.accepts {
                if let Some(error) = self.release_error {
                    return Err(error);
                }
                assert_eq!(
                    self.worker_drops.load(Ordering::SeqCst),
                    0,
                    "worker duplicates must remain held until finalization"
                );
                self.releases.fetch_add(1, Ordering::SeqCst);
                drop(worker_handles.take());
                Ok(())
            } else {
                Err("test_terminal_binding_mismatch")
            }
        }
    }

    fn test_worker_handles(worker_drops: Arc<AtomicUsize>) -> WorkerScenarioHandleBundle {
        let nonce = WORKER_HANDLE_FIXTURE_COUNTER.fetch_add(1, Ordering::Relaxed);
        let root = std::env::temp_dir().join(format!(
            "vrcforge-background-worker-handles-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir(&root).expect("create worker handle fixture directory");
        let paths: [std::path::PathBuf; 8] =
            std::array::from_fn(|index| root.join(format!("role-{index}.bin")));
        for (index, path) in paths.iter().enumerate() {
            fs::write(path, [index as u8 + 1]).expect("write worker handle fixture");
        }
        let files = std::array::from_fn(|index| {
            OpenOptions::new()
                .read(true)
                .share_mode(FILE_SHARE_READ)
                .open(&paths[index])
                .expect("open non-inheritable read-only worker handle")
        });
        let mut bundle = WorkerScenarioHandleBundle::from_test_files(files, worker_drops);
        bundle.set_drop_callback_for_test(Box::new(move || {
            for path in paths {
                let _ = fs::remove_file(path);
            }
            let _ = fs::remove_dir(root);
        }));
        bundle
    }

    fn test_worker_ownership(
        lease: TestTerminalLease,
        worker_drops: Arc<AtomicUsize>,
    ) -> WorkerRunOwnership {
        let mut lease: Box<dyn BackgroundTerminalLease> = Box::new(lease);
        let worker_handles = test_worker_handles(worker_drops);
        let prepared =
            PreparedRun::for_runtime_test([0x11; 32], [0x22; 32], [0x33; 32], [0x44; 32]);
        let prepared_receipt_digest = prepared.receipt().digest();
        let policy_snapshot_digest: Digest = Sha256::digest(prepared.policy_snapshot()).into();
        let start_contract = lease
            .consume_start_authorization(
                &worker_handles,
                prepared_receipt_digest,
                policy_snapshot_digest,
            )
            .expect("test start authorization");
        WorkerRunOwnership {
            lease,
            worker_handles: Some(worker_handles),
            start_contract,
        }
    }

    struct FailedHandoffLease {
        handoffs: Arc<AtomicUsize>,
        drops: Arc<AtomicUsize>,
    }

    impl Drop for FailedHandoffLease {
        fn drop(&mut self) {
            self.drops.fetch_add(1, Ordering::SeqCst);
        }
    }

    impl BackgroundTerminalLease for FailedHandoffLease {
        fn take_worker_handles(&mut self) -> Result<WorkerScenarioHandleBundle, &'static str> {
            self.handoffs.fetch_add(1, Ordering::SeqCst);
            Err("test_worker_handle_clone_failed")
        }

        fn consume_start_authorization(
            &mut self,
            _worker_handles: &WorkerScenarioHandleBundle,
            _prepared_receipt_digest: Digest,
            _policy_snapshot_digest: Digest,
        ) -> Result<VerifiedScenarioStartContract, &'static str> {
            panic!("failed handoff must never validate worker handles")
        }

        fn finalize_terminal(
            &mut self,
            _worker_handles: &mut Option<WorkerScenarioHandleBundle>,
            _terminal: &ValidatedNativeTerminalRun,
        ) -> Result<(), &'static str> {
            panic!("failed handoff must never reach terminal finalization")
        }
    }

    struct PreNativeDriftLease {
        worker_handles: Option<WorkerScenarioHandleBundle>,
        validations: Arc<AtomicUsize>,
        drops: Arc<AtomicUsize>,
    }

    impl Drop for PreNativeDriftLease {
        fn drop(&mut self) {
            self.drops.fetch_add(1, Ordering::SeqCst);
        }
    }

    impl BackgroundTerminalLease for PreNativeDriftLease {
        fn take_worker_handles(&mut self) -> Result<WorkerScenarioHandleBundle, &'static str> {
            self.worker_handles
                .take()
                .ok_or("test_worker_handle_handoff_replayed")
        }

        fn consume_start_authorization(
            &mut self,
            _worker_handles: &WorkerScenarioHandleBundle,
            _prepared_receipt_digest: Digest,
            _policy_snapshot_digest: Digest,
        ) -> Result<VerifiedScenarioStartContract, &'static str> {
            self.validations.fetch_add(1, Ordering::SeqCst);
            Err("authority_model_part_worker_handle_snapshot_mismatch")
        }

        fn finalize_terminal(
            &mut self,
            _worker_handles: &mut Option<WorkerScenarioHandleBundle>,
            _terminal: &ValidatedNativeTerminalRun,
        ) -> Result<(), &'static str> {
            panic!("pre-native drift must never reach terminal finalization")
        }
    }

    fn test_native_terminal() -> ValidatedNativeTerminalRun {
        let terminal = BurnedRunProof::for_runtime_test(
            [0x11; 32],
            [0x22; 32],
            [0x33; 32],
            BurnReason::Failed,
        );
        ValidatedNativeTerminalRun::Burned(
            NativeBurnedRunProof::for_runtime_test(terminal, None).unwrap(),
        )
    }

    #[test]
    fn native_worker_success_ownership_cannot_exist_without_a_start_contract() {
        let source = include_str!("background.rs");
        assert!(source.contains("start_contract: VerifiedScenarioStartContract"));
        let legacy_optional =
            ["start_contract: ", "Option<VerifiedScenarioStartContract>"].concat();
        assert!(!source.contains(&legacy_optional));
    }

    #[test]
    fn worker_handoff_failure_moves_the_original_lease_to_fault_held() {
        let handoffs = Arc::new(AtomicUsize::new(0));
        let drops = Arc::new(AtomicUsize::new(0));
        let mut fault_held = None;
        let result = claim_worker_ownership(
            &mut fault_held,
            Box::new(FailedHandoffLease {
                handoffs: Arc::clone(&handoffs),
                drops: Arc::clone(&drops),
            }),
        );
        assert!(matches!(result, Err("test_worker_handle_clone_failed")));
        assert!(fault_held.is_some());
        assert_eq!(handoffs.load(Ordering::SeqCst), 1);
        assert_eq!(drops.load(Ordering::SeqCst), 0);
        drop(fault_held);
        assert_eq!(drops.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn failed_first_start_authorization_is_consumed_once_and_fault_held() {
        let validations = Arc::new(AtomicUsize::new(0));
        let drops = Arc::new(AtomicUsize::new(0));
        let worker_drops = Arc::new(AtomicUsize::new(0));
        let mut fault_held = None;

        let prepared =
            PreparedRun::for_runtime_test([0x11; 32], [0x22; 32], [0x33; 32], [0x44; 32]);
        let result = claim_worker_ownership_for_native_begin(
            &mut fault_held,
            Box::new(PreNativeDriftLease {
                worker_handles: Some(test_worker_handles(Arc::clone(&worker_drops))),
                validations: Arc::clone(&validations),
                drops: Arc::clone(&drops),
            }),
            &prepared,
        );
        assert!(matches!(
            result,
            Err("authority_model_part_worker_handle_snapshot_mismatch")
        ));
        assert!(fault_held.is_some());
        assert_eq!(validations.load(Ordering::SeqCst), 1);
        assert_eq!(drops.load(Ordering::SeqCst), 0);
        assert_eq!(worker_drops.load(Ordering::SeqCst), 0);

        drop(fault_held);
        assert_eq!(drops.load(Ordering::SeqCst), 1);
        assert_eq!(worker_drops.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn terminal_mismatch_is_held_until_background_shutdown() {
        let drops = Arc::new(AtomicUsize::new(0));
        let releases = Arc::new(AtomicUsize::new(0));
        let worker_drops = Arc::new(AtomicUsize::new(0));
        let mut fault_held = None;
        let result = finish_terminal_ownership(
            &mut fault_held,
            test_worker_ownership(
                TestTerminalLease {
                    accepts: false,
                    release_error: None,
                    drops: Arc::clone(&drops),
                    releases: Arc::clone(&releases),
                    worker_drops: Arc::clone(&worker_drops),
                },
                Arc::clone(&worker_drops),
            ),
            test_native_terminal(),
        );
        assert_eq!(result.unwrap_err(), "test_terminal_binding_mismatch");
        assert!(fault_held.is_some());
        assert_eq!(drops.load(Ordering::SeqCst), 0);
        assert_eq!(releases.load(Ordering::SeqCst), 0);
        assert_eq!(worker_drops.load(Ordering::SeqCst), 0);
        drop(fault_held);
        assert_eq!(drops.load(Ordering::SeqCst), 1);
        assert_eq!(worker_drops.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn exact_terminal_releases_before_it_is_published() {
        let drops = Arc::new(AtomicUsize::new(0));
        let releases = Arc::new(AtomicUsize::new(0));
        let worker_drops = Arc::new(AtomicUsize::new(0));
        let mut fault_held = None;
        let terminal = finish_terminal_ownership(
            &mut fault_held,
            test_worker_ownership(
                TestTerminalLease {
                    accepts: true,
                    release_error: None,
                    drops: Arc::clone(&drops),
                    releases: Arc::clone(&releases),
                    worker_drops: Arc::clone(&worker_drops),
                },
                Arc::clone(&worker_drops),
            ),
            test_native_terminal(),
        )
        .unwrap();
        assert!(matches!(terminal, ValidatedNativeTerminalRun::Burned(_)));
        assert!(fault_held.is_none());
        assert_eq!(releases.load(Ordering::SeqCst), 1);
        assert_eq!(drops.load(Ordering::SeqCst), 1);
        assert_eq!(worker_drops.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn terminal_release_failure_keeps_the_lease_held_until_shutdown() {
        let drops = Arc::new(AtomicUsize::new(0));
        let releases = Arc::new(AtomicUsize::new(0));
        let worker_drops = Arc::new(AtomicUsize::new(0));
        let mut fault_held = None;
        let result = finish_terminal_ownership(
            &mut fault_held,
            test_worker_ownership(
                TestTerminalLease {
                    accepts: true,
                    release_error: Some("test_terminal_release_failed"),
                    drops: Arc::clone(&drops),
                    releases: Arc::clone(&releases),
                    worker_drops: Arc::clone(&worker_drops),
                },
                Arc::clone(&worker_drops),
            ),
            test_native_terminal(),
        );
        assert_eq!(result.unwrap_err(), "test_terminal_release_failed");
        assert!(fault_held.is_some());
        assert_eq!(releases.load(Ordering::SeqCst), 0);
        assert_eq!(drops.load(Ordering::SeqCst), 0);
        assert_eq!(worker_drops.load(Ordering::SeqCst), 0);
        drop(fault_held);
        assert_eq!(drops.load(Ordering::SeqCst), 1);
        assert_eq!(worker_drops.load(Ordering::SeqCst), 1);
    }

    fn core(
        now: Arc<AtomicU64>,
        trace: Arc<Mutex<TestTrace>>,
        start_gate: Option<Arc<AtomicBool>>,
        pending_polls: usize,
        fail_intent: bool,
    ) -> BackgroundCore<TestDriver> {
        BackgroundCore::spawn(
            TestDriver {
                trace,
                now: now.clone(),
                start_entered: None,
                start_gate,
                intent_gate: None,
                starting_termination_available: None,
                starting_advance_available: None,
                starting_abort_available: None,
                starting_actions: 1,
                starting_kind_override: None,
                pending_polls,
                fail_intent,
            },
            Arc::new(TestClock(now)),
        )
        .expect("worker must spawn")
    }

    fn wait_for_armed(core: &mut BackgroundCore<TestDriver>, key: BackgroundRunKey) -> u64 {
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        loop {
            match core.poll(key, None).expect("poll must remain valid") {
                CorePoll::Starting => {
                    assert!(std::time::Instant::now() < deadline, "worker did not arm");
                    thread::yield_now();
                }
                CorePoll::Armed(receipt) => return receipt,
                CorePoll::Running | CorePoll::Terminal(_) => {
                    panic!("run crossed armed barrier without confirmation")
                }
            }
        }
    }

    fn wait_for_terminal(
        core: &mut BackgroundCore<TestDriver>,
        key: BackgroundRunKey,
        receipt: &u64,
    ) -> TestTerminal {
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        loop {
            match core
                .poll(key, Some(receipt))
                .expect("poll must remain valid")
            {
                CorePoll::Running => {
                    assert!(
                        std::time::Instant::now() < deadline,
                        "worker did not finish"
                    );
                    thread::yield_now();
                }
                CorePoll::Terminal(terminal) => return terminal,
                CorePoll::Starting | CorePoll::Armed(_) => {
                    panic!("confirmed run regressed before terminal")
                }
            }
        }
    }

    fn wait_for_unarmed_terminal(
        core: &mut BackgroundCore<TestDriver>,
        key: BackgroundRunKey,
    ) -> TestTerminal {
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        loop {
            match core.poll(key, None).expect("poll must remain valid") {
                CorePoll::Starting => {
                    assert!(
                        std::time::Instant::now() < deadline,
                        "starting run did not finish"
                    );
                    thread::yield_now();
                }
                CorePoll::Terminal(terminal) => return terminal,
                CorePoll::Armed(_) | CorePoll::Running => {
                    panic!("terminated starting run crossed the armed barrier")
                }
            }
        }
    }

    fn wait_for_unconfirmed_armed_terminal(
        core: &mut BackgroundCore<TestDriver>,
        key: BackgroundRunKey,
    ) -> TestTerminal {
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        loop {
            match core.poll(key, None).expect("poll must remain valid") {
                CorePoll::Starting | CorePoll::Armed(_) => {
                    assert!(
                        std::time::Instant::now() < deadline,
                        "unconfirmed armed run did not terminate"
                    );
                    thread::yield_now();
                }
                CorePoll::Terminal(terminal) => return terminal,
                CorePoll::Running => {
                    panic!("unconfirmed armed run crossed the persistence barrier")
                }
            }
        }
    }

    #[test]
    fn exact_armed_confirmation_is_a_hard_poll_barrier() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = core(now, trace.clone(), None, 1, false);
        let key = key(1);
        core.start(key, (41, 100)).expect("start must queue");
        let receipt = wait_for_armed(&mut core, key);

        thread::sleep(Duration::from_millis(
            3 * WORKER_POLL_INTERVAL.as_millis() as u64,
        ));
        assert_eq!(trace.lock().expect("trace lock").advances, 0);
        assert_eq!(
            core.poll(key, Some(&42))
                .expect_err("wrong receipt must fail"),
            BACKGROUND_ARMED_MISMATCH
        );
        assert_eq!(trace.lock().expect("trace lock").advances, 0);

        assert!(matches!(
            core.poll(key, Some(&receipt))
                .expect("exact receipt confirms"),
            CorePoll::Running
        ));
        assert_eq!(
            wait_for_terminal(&mut core, key, &receipt),
            TestTerminal::Completed
        );
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn unconfirmed_armed_run_honors_deadline_and_uses_normal_terminal_path() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = core(now.clone(), trace.clone(), None, 2, false);
        let key = key(23);
        let receipt = 63;
        core.start(key, (receipt, 100)).expect("start must queue");
        assert_eq!(wait_for_armed(&mut core, key), receipt);

        now.store(100, Ordering::SeqCst);
        assert_eq!(
            wait_for_unconfirmed_armed_terminal(&mut core, key),
            TestTerminal::TimedOut
        );
        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.intents, vec![NativeTerminationKind::TimedOut]);
        assert_eq!(trace.advances, 3);
        assert_eq!(trace.aborts, 0);
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn unconfirmed_armed_deadline_uncertainty_never_advances_before_intent_is_durable() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let termination_available = Arc::new(AtomicBool::new(false));
        let mut core = BackgroundCore::spawn(
            TestDriver {
                trace: trace.clone(),
                now: now.clone(),
                start_entered: None,
                start_gate: None,
                intent_gate: None,
                starting_termination_available: Some(termination_available.clone()),
                starting_advance_available: None,
                starting_abort_available: None,
                starting_actions: 1,
                starting_kind_override: None,
                pending_polls: 0,
                fail_intent: false,
            },
            Arc::new(TestClock(now.clone())),
        )
        .expect("worker must spawn");
        let key = key(24);
        let receipt = 64;
        core.start(key, (receipt, 100)).expect("start must queue");
        assert_eq!(wait_for_armed(&mut core, key), receipt);

        now.store(100, Ordering::SeqCst);
        thread::sleep(Duration::from_millis(
            3 * WORKER_POLL_INTERVAL.as_millis() as u64,
        ));
        assert!(matches!(
            core.poll(key, None)
                .expect("uncertain timeout must remain at the armed barrier"),
            CorePoll::Armed(value) if value == receipt
        ));
        let current = trace.lock().expect("trace lock");
        assert_eq!(current.advances, 0);
        assert!(current.intents.is_empty());
        drop(current);

        termination_available.store(true, Ordering::SeqCst);
        assert_eq!(
            wait_for_unconfirmed_armed_terminal(&mut core, key),
            TestTerminal::TimedOut
        );
        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.intents, vec![NativeTerminationKind::TimedOut]);
        assert_eq!(trace.advances, 1);
        assert_eq!(trace.aborts, 0);
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn terminal_proof_can_be_taken_only_once() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = core(now, trace, None, 0, false);
        let key = key(2);
        core.start(key, (42, 100)).expect("start must queue");
        let receipt = wait_for_armed(&mut core, key);
        assert_eq!(
            wait_for_terminal(&mut core, key, &receipt),
            TestTerminal::Completed
        );
        assert_eq!(
            core.poll(key, Some(&receipt))
                .expect_err("terminal must not be replayed"),
            BACKGROUND_TERMINAL_TAKEN
        );
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn starting_cancel_waits_for_a_durable_intent_before_acknowledging() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let gate = Arc::new(AtomicBool::new(false));
        let mut core = core(now, trace.clone(), Some(gate.clone()), 0, false);
        let key = key(3);
        core.start(key, (43, 100)).expect("start must queue");
        let release_gate = gate.clone();
        let release = thread::spawn(move || {
            thread::sleep(Duration::from_millis(10));
            release_gate.store(true, Ordering::SeqCst);
        });
        assert_eq!(
            core.request_cancel(key, None)
                .expect("pre-arm request must wait for the service journal"),
            BackgroundCancelAcknowledgement::Recorded(NativeTerminationKind::Cancelled)
        );
        release.join().expect("gate release must complete");
        assert_eq!(
            trace.lock().expect("trace lock").intents,
            vec![NativeTerminationKind::Cancelled]
        );
        assert_eq!(
            wait_for_unarmed_terminal(&mut core, key),
            TestTerminal::Cancelled
        );
        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.starting_actions, 0);
        assert_eq!(trace.aborts, 0);
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn starting_cancel_timeout_is_uncertain_and_retry_observes_the_intent() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let intent_gate = Arc::new(AtomicBool::new(false));
        let mut core = BackgroundCore::spawn(
            TestDriver {
                trace: trace.clone(),
                now: now.clone(),
                start_entered: None,
                start_gate: None,
                intent_gate: Some(intent_gate.clone()),
                starting_termination_available: None,
                starting_advance_available: None,
                starting_abort_available: None,
                starting_actions: 100,
                starting_kind_override: None,
                pending_polls: 1,
                fail_intent: false,
            },
            Arc::new(TestClock(now)),
        )
        .expect("worker must spawn");
        let key = key(13);
        core.start(key, (53, 100)).expect("start must queue");
        let first_action_deadline = std::time::Instant::now() + Duration::from_secs(2);
        while trace.lock().expect("trace lock").starting_actions == 0 {
            assert!(
                std::time::Instant::now() < first_action_deadline,
                "worker did not enter staged start"
            );
            thread::yield_now();
        }
        let actions_before_cancel = trace.lock().expect("trace lock").starting_actions;
        assert_eq!(
            core.request_cancel_with_timeout(key, None, Duration::from_millis(10))
                .expect("accepted pre-arm request without an ack is uncertain"),
            BackgroundCancelAcknowledgement::Uncertain
        );
        assert!(trace.lock().expect("trace lock").intents.is_empty());

        intent_gate.store(true, Ordering::SeqCst);
        assert_eq!(
            core.request_cancel(key, None)
                .expect("retry must observe the worker-owned durable intent"),
            BackgroundCancelAcknowledgement::AlreadyRecorded(NativeTerminationKind::Cancelled)
        );
        assert_eq!(
            wait_for_unarmed_terminal(&mut core, key),
            TestTerminal::Cancelled
        );
        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.intents, vec![NativeTerminationKind::Cancelled]);
        assert_eq!(trace.starting_actions, actions_before_cancel);
        assert_eq!(trace.aborts, 0);
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn starting_cancel_between_steps_stops_new_actions_after_acknowledgement() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = BackgroundCore::spawn(
            TestDriver {
                trace: trace.clone(),
                now: now.clone(),
                start_entered: None,
                start_gate: None,
                intent_gate: None,
                starting_termination_available: None,
                starting_advance_available: None,
                starting_abort_available: None,
                starting_actions: 100,
                starting_kind_override: None,
                pending_polls: 0,
                fail_intent: false,
            },
            Arc::new(TestClock(now)),
        )
        .expect("worker must spawn");
        let key = key(14);
        core.start(key, (54, 100)).expect("start must queue");
        let first_action_deadline = std::time::Instant::now() + Duration::from_secs(2);
        while trace.lock().expect("trace lock").starting_actions == 0 {
            assert!(
                std::time::Instant::now() < first_action_deadline,
                "worker did not complete the first staged action"
            );
            thread::yield_now();
        }

        assert_eq!(
            core.request_cancel(key, None)
                .expect("cancel between steps must be journaled"),
            BackgroundCancelAcknowledgement::Recorded(NativeTerminationKind::Cancelled)
        );
        let actions_at_ack = trace.lock().expect("trace lock").starting_actions;
        assert!(actions_at_ack < 100, "cancel must precede the armed step");
        assert_eq!(
            core.request_cancel(key, None)
                .expect("repeated cancel must observe the durable kind"),
            BackgroundCancelAcknowledgement::AlreadyRecorded(NativeTerminationKind::Cancelled)
        );
        assert_eq!(
            wait_for_unarmed_terminal(&mut core, key),
            TestTerminal::Cancelled
        );
        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.starting_actions, actions_at_ack);
        assert_eq!(trace.intents, vec![NativeTerminationKind::Cancelled]);
        assert_eq!(trace.aborts, 0);
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn starting_deadline_uses_the_driver_recorded_kind() {
        let now = Arc::new(AtomicU64::new(100));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = BackgroundCore::spawn(
            TestDriver {
                trace: trace.clone(),
                now: now.clone(),
                start_entered: None,
                start_gate: None,
                intent_gate: None,
                starting_termination_available: None,
                starting_advance_available: None,
                starting_abort_available: None,
                starting_actions: 3,
                starting_kind_override: Some(NativeTerminationKind::Cancelled),
                pending_polls: 0,
                fail_intent: false,
            },
            Arc::new(TestClock(now)),
        )
        .expect("worker must spawn");
        let key = key(15);
        core.start(key, (55, 100)).expect("start must queue");
        assert_eq!(
            wait_for_unarmed_terminal(&mut core, key),
            TestTerminal::Cancelled
        );
        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.intents, vec![NativeTerminationKind::Cancelled]);
        assert_eq!(trace.starting_actions, 0);
        assert_eq!(trace.aborts, 0);
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn starting_cancel_transport_uncertain_blocks_actions_until_retry_records() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let termination_available = Arc::new(AtomicBool::new(false));
        let mut core = BackgroundCore::spawn(
            TestDriver {
                trace: trace.clone(),
                now: now.clone(),
                start_entered: None,
                start_gate: None,
                intent_gate: None,
                starting_termination_available: Some(termination_available.clone()),
                starting_advance_available: None,
                starting_abort_available: None,
                starting_actions: 100,
                starting_kind_override: None,
                pending_polls: 0,
                fail_intent: false,
            },
            Arc::new(TestClock(now)),
        )
        .expect("worker must spawn");
        let key = key(16);
        core.start(key, (56, 100)).expect("start must queue");
        let first_action_deadline = std::time::Instant::now() + Duration::from_secs(2);
        while trace.lock().expect("trace lock").starting_actions == 0 {
            assert!(
                std::time::Instant::now() < first_action_deadline,
                "worker did not complete the first staged action"
            );
            thread::yield_now();
        }

        assert_eq!(
            core.request_cancel(key, None)
                .expect("transport uncertainty is a typed acknowledgement"),
            BackgroundCancelAcknowledgement::Uncertain
        );
        let actions_at_uncertain = trace.lock().expect("trace lock").starting_actions;
        thread::sleep(Duration::from_millis(
            3 * WORKER_POLL_INTERVAL.as_millis() as u64,
        ));
        assert!(matches!(
            core.poll(key, None)
                .expect("uncertain termination must remain retryable"),
            CorePoll::Starting
        ));
        let current = trace.lock().expect("trace lock");
        assert_eq!(current.starting_actions, actions_at_uncertain);
        assert!(current.intents.is_empty());
        drop(current);

        termination_available.store(true, Ordering::SeqCst);
        assert_eq!(
            core.request_cancel(key, None)
                .expect("retry must observe the durable termination kind"),
            BackgroundCancelAcknowledgement::AlreadyRecorded(NativeTerminationKind::Cancelled)
        );
        assert_eq!(
            wait_for_unarmed_terminal(&mut core, key),
            TestTerminal::Cancelled
        );
        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.starting_actions, actions_at_uncertain);
        assert_eq!(trace.intents, vec![NativeTerminationKind::Cancelled]);
        assert_eq!(trace.aborts, 0);
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn starting_deadline_transport_uncertain_retries_without_advancing() {
        let now = Arc::new(AtomicU64::new(100));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let termination_available = Arc::new(AtomicBool::new(false));
        let mut core = BackgroundCore::spawn(
            TestDriver {
                trace: trace.clone(),
                now: now.clone(),
                start_entered: None,
                start_gate: None,
                intent_gate: None,
                starting_termination_available: Some(termination_available.clone()),
                starting_advance_available: None,
                starting_abort_available: None,
                starting_actions: 3,
                starting_kind_override: None,
                pending_polls: 0,
                fail_intent: false,
            },
            Arc::new(TestClock(now)),
        )
        .expect("worker must spawn");
        let key = key(17);
        core.start(key, (57, 100)).expect("start must queue");
        thread::sleep(Duration::from_millis(
            3 * WORKER_POLL_INTERVAL.as_millis() as u64,
        ));
        assert!(matches!(
            core.poll(key, None)
                .expect("uncertain deadline attempt must remain Starting"),
            CorePoll::Starting
        ));
        let current = trace.lock().expect("trace lock");
        assert_eq!(current.starting_advances, 0);
        assert_eq!(current.starting_actions, 0);
        assert!(current.intents.is_empty());
        drop(current);

        termination_available.store(true, Ordering::SeqCst);
        assert_eq!(
            wait_for_unarmed_terminal(&mut core, key),
            TestTerminal::TimedOut
        );
        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.starting_actions, 0);
        assert_eq!(trace.intents, vec![NativeTerminationKind::TimedOut]);
        assert_eq!(trace.aborts, 0);
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn starting_advance_retry_retains_state_without_replaying_actions() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let advance_available = Arc::new(AtomicBool::new(false));
        let mut core = BackgroundCore::spawn(
            TestDriver {
                trace: trace.clone(),
                now: now.clone(),
                start_entered: None,
                start_gate: None,
                intent_gate: None,
                starting_termination_available: None,
                starting_advance_available: Some(advance_available.clone()),
                starting_abort_available: None,
                starting_actions: 1,
                starting_kind_override: None,
                pending_polls: 0,
                fail_intent: false,
            },
            Arc::new(TestClock(now)),
        )
        .expect("worker must spawn");
        let key = key(18);
        core.start(key, (58, 100)).expect("start must queue");
        let retry_deadline = std::time::Instant::now() + Duration::from_secs(2);
        while trace.lock().expect("trace lock").starting_retries < 2 {
            assert!(
                std::time::Instant::now() < retry_deadline,
                "worker did not expose bounded starting retries"
            );
            thread::yield_now();
        }
        assert!(matches!(
            core.poll(key, None)
                .expect("retry must retain the published Starting state"),
            CorePoll::Starting
        ));
        assert_eq!(trace.lock().expect("trace lock").starting_actions, 0);

        advance_available.store(true, Ordering::SeqCst);
        let receipt = wait_for_armed(&mut core, key);
        let current = trace.lock().expect("trace lock");
        assert_eq!(current.starting_actions, 1);
        assert!(current.starting_retries >= 2);
        assert_eq!(current.aborts, 0);
        drop(current);
        assert!(matches!(
            core.poll(key, Some(&receipt))
                .expect("outer receipt persistence still releases the barrier"),
            CorePoll::Running
        ));
        assert_eq!(
            wait_for_terminal(&mut core, key, &receipt),
            TestTerminal::Completed
        );
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn armed_advance_retry_retains_the_exact_non_clone_capability() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = core(now, trace.clone(), None, 2, false);
        let key = key(22);
        let receipt = 62;
        core.start(key, (receipt, 100)).expect("start must queue");
        assert_eq!(wait_for_armed(&mut core, key), receipt);
        assert!(matches!(
            core.poll(key, Some(&receipt))
                .expect("exact receipt must release the Armed barrier"),
            CorePoll::Running
        ));
        assert_eq!(
            wait_for_terminal(&mut core, key, &receipt),
            TestTerminal::Completed
        );

        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.armed_retries, 2);
        assert_eq!(trace.advances, 3);
        let expected_capability_id = receipt ^ 0xa5a5_a5a5_a5a5_a5a5;
        assert_eq!(trace.armed_capability_ids.len(), 3);
        assert!(trace
            .armed_capability_ids
            .iter()
            .all(|capability_id| *capability_id == expected_capability_id));
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn starting_abort_retry_retains_capability_until_containment_succeeds() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let abort_available = Arc::new(AtomicBool::new(false));
        let mut core = BackgroundCore::spawn(
            TestDriver {
                trace: trace.clone(),
                now: now.clone(),
                start_entered: None,
                start_gate: None,
                intent_gate: None,
                starting_termination_available: None,
                starting_advance_available: None,
                starting_abort_available: Some(abort_available.clone()),
                starting_actions: 100,
                starting_kind_override: None,
                pending_polls: 0,
                fail_intent: false,
            },
            Arc::new(TestClock(now)),
        )
        .expect("worker must spawn");
        let key = key(19);
        core.start(key, (59, 100)).expect("start must queue");
        let first_action_deadline = std::time::Instant::now() + Duration::from_secs(2);
        while trace.lock().expect("trace lock").starting_actions == 0 {
            assert!(
                std::time::Instant::now() < first_action_deadline,
                "worker did not complete the first staged action"
            );
            thread::yield_now();
        }
        let actions_before_abort = trace.lock().expect("trace lock").starting_actions;
        core.commands
            .send(WorkerCommand::Abort {
                key,
                failure_code: "test_abort",
            })
            .expect("abort must queue");
        thread::sleep(Duration::from_millis(
            3 * WORKER_POLL_INTERVAL.as_millis() as u64,
        ));
        assert!(matches!(
            core.poll(key, None)
                .expect("failed containment must retain Starting ownership"),
            CorePoll::Starting
        ));
        let current = trace.lock().expect("trace lock");
        assert_eq!(current.starting_actions, actions_before_abort);
        assert!(current.aborts >= 2);
        drop(current);

        abort_available.store(true, Ordering::SeqCst);
        assert_eq!(
            wait_for_unarmed_terminal(&mut core, key),
            TestTerminal::Failed
        );
        assert_eq!(
            trace.lock().expect("trace lock").starting_actions,
            actions_before_abort
        );
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn armed_abort_retry_retains_capability_until_containment_succeeds() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let abort_available = Arc::new(AtomicBool::new(false));
        let mut core = BackgroundCore::spawn(
            TestDriver {
                trace: trace.clone(),
                now: now.clone(),
                start_entered: None,
                start_gate: None,
                intent_gate: None,
                starting_termination_available: None,
                starting_advance_available: None,
                starting_abort_available: Some(abort_available.clone()),
                starting_actions: 1,
                starting_kind_override: None,
                pending_polls: 100,
                fail_intent: false,
            },
            Arc::new(TestClock(now)),
        )
        .expect("worker must spawn");
        let key = key(20);
        core.start(key, (60, 100)).expect("start must queue");
        let receipt = wait_for_armed(&mut core, key);
        assert!(matches!(
            core.poll(key, Some(&receipt))
                .expect("exact receipt must release the Armed barrier"),
            CorePoll::Running
        ));
        core.commands
            .send(WorkerCommand::Abort {
                key,
                failure_code: "test_armed_abort",
            })
            .expect("abort must queue");
        thread::sleep(Duration::from_millis(
            3 * WORKER_POLL_INTERVAL.as_millis() as u64,
        ));
        assert!(matches!(
            core.poll(key, Some(&receipt))
                .expect("failed containment must retain Armed ownership"),
            CorePoll::Running
        ));
        assert!(trace.lock().expect("trace lock").aborts >= 2);

        abort_available.store(true, Ordering::SeqCst);
        assert_eq!(
            wait_for_terminal(&mut core, key, &receipt),
            TestTerminal::Failed
        );
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn cancellation_records_one_intent_then_uses_normal_terminal_path() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = core(now, trace.clone(), None, 2, false);
        let key = key(4);
        core.start(key, (44, 100)).expect("start must queue");
        let receipt = wait_for_armed(&mut core, key);
        assert_eq!(
            core.request_cancel(key, Some(&receipt))
                .expect("durable cancellation intent"),
            BackgroundCancelAcknowledgement::Recorded(NativeTerminationKind::Cancelled)
        );
        assert_eq!(
            core.request_cancel(key, Some(&receipt))
                .expect("same cancellation is idempotent"),
            BackgroundCancelAcknowledgement::AlreadyRecorded(NativeTerminationKind::Cancelled)
        );
        assert_eq!(
            wait_for_terminal(&mut core, key, &receipt),
            TestTerminal::Cancelled
        );
        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.intents, vec![NativeTerminationKind::Cancelled]);
        assert_eq!(trace.aborts, 0);
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn late_cancel_ack_is_uncertain_and_retry_observes_recorded_intent() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let intent_gate = Arc::new(AtomicBool::new(false));
        let mut core = BackgroundCore::spawn(
            TestDriver {
                trace: trace.clone(),
                now: now.clone(),
                start_entered: None,
                start_gate: None,
                intent_gate: Some(intent_gate.clone()),
                starting_termination_available: None,
                starting_advance_available: None,
                starting_abort_available: None,
                starting_actions: 1,
                starting_kind_override: None,
                pending_polls: 2,
                fail_intent: false,
            },
            Arc::new(TestClock(now)),
        )
        .expect("worker must spawn");
        let key = key(7);
        core.start(key, (47, 100)).expect("start must queue");
        let receipt = wait_for_armed(&mut core, key);

        assert_eq!(
            core.request_cancel_with_timeout(key, Some(&receipt), Duration::from_millis(10))
                .expect("a queued command with no acknowledgement is uncertain"),
            BackgroundCancelAcknowledgement::Uncertain
        );
        intent_gate.store(true, Ordering::SeqCst);
        assert_eq!(
            core.request_cancel(key, Some(&receipt))
                .expect("retry must read worker-owned intent state"),
            BackgroundCancelAcknowledgement::AlreadyRecorded(NativeTerminationKind::Cancelled)
        );
        assert_eq!(
            wait_for_terminal(&mut core, key, &receipt),
            TestTerminal::Cancelled
        );
        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.intents, vec![NativeTerminationKind::Cancelled]);
        assert_eq!(trace.aborts, 0);
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn cancellation_after_terminal_is_typed_and_does_not_replay_proof() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = core(now, trace.clone(), None, 0, false);
        let key = key(8);
        core.start(key, (48, 100)).expect("start must queue");
        let receipt = wait_for_armed(&mut core, key);
        assert_eq!(
            wait_for_terminal(&mut core, key, &receipt),
            TestTerminal::Completed
        );
        assert_eq!(
            core.request_cancel(key, Some(&receipt))
                .expect("terminal state is a typed acknowledgement"),
            BackgroundCancelAcknowledgement::AlreadyTerminal
        );
        assert!(trace.lock().expect("trace lock").intents.is_empty());
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn recovery_is_worker_owned_for_completed_replay_and_restart_containment() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = core(now, trace.clone(), None, 0, false);

        let completed_key = key(9);
        core.recover(completed_key, TestTerminal::Completed)
            .expect("completed recovery must queue");
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        loop {
            match core
                .poll_recovery(completed_key)
                .expect("completed recovery poll")
            {
                None => {
                    assert!(
                        std::time::Instant::now() < deadline,
                        "completed recovery did not finish"
                    );
                    thread::yield_now();
                }
                Some(terminal) => {
                    assert_eq!(terminal, TestTerminal::Completed);
                    break;
                }
            }
        }

        let contained_key = key(10);
        core.recover(contained_key, TestTerminal::Failed)
            .expect("restart containment must queue after take-once terminal");
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        loop {
            match core
                .poll_recovery(contained_key)
                .expect("containment recovery poll")
            {
                None => {
                    assert!(
                        std::time::Instant::now() < deadline,
                        "containment recovery did not finish"
                    );
                    thread::yield_now();
                }
                Some(terminal) => {
                    assert_eq!(terminal, TestTerminal::Failed);
                    break;
                }
            }
        }
        assert_eq!(trace.lock().expect("trace lock").recoveries, 2);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn shutdown_timeout_is_bounded_and_can_be_retried_after_worker_progress() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let entered = Arc::new(AtomicBool::new(false));
        let start_gate = Arc::new(AtomicBool::new(false));
        let mut core = BackgroundCore::spawn(
            TestDriver {
                trace: trace.clone(),
                now: now.clone(),
                start_entered: Some(entered.clone()),
                start_gate: Some(start_gate.clone()),
                intent_gate: None,
                starting_termination_available: None,
                starting_advance_available: None,
                starting_abort_available: None,
                starting_actions: 1,
                starting_kind_override: None,
                pending_polls: 0,
                fail_intent: false,
            },
            Arc::new(TestClock(now)),
        )
        .expect("worker must spawn");
        let key = key(11);
        core.start(key, (51, 100)).expect("start must queue");
        let entered_deadline = std::time::Instant::now() + Duration::from_secs(2);
        while !entered.load(Ordering::SeqCst) {
            assert!(
                std::time::Instant::now() < entered_deadline,
                "worker did not enter start"
            );
            thread::yield_now();
        }

        let shutdown_started = std::time::Instant::now();
        assert_eq!(
            core.shutdown_and_wait_with_timeout(Duration::from_millis(20))
                .expect_err("blocked worker must time out"),
            BACKGROUND_SHUTDOWN_TIMEOUT
        );
        assert!(shutdown_started.elapsed() < Duration::from_millis(500));
        assert!(core.worker.is_some(), "timeout must retain retry ownership");

        start_gate.store(true, Ordering::SeqCst);
        core.shutdown_and_wait_with_timeout(Duration::from_secs(2))
            .expect("shutdown retry must observe worker completion");
        assert_eq!(trace.lock().expect("trace lock").aborts, 1);
    }

    #[test]
    fn queued_async_fault_makes_shutdown_fail_closed() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = core(now, trace.clone(), None, 1_000, false);
        let key = key(13);
        core.start(key, (53, 100)).expect("start must queue");
        let receipt = wait_for_armed(&mut core, key);
        assert!(matches!(
            core.poll(key, Some(&receipt)).expect("run must confirm"),
            CorePoll::Running
        ));
        trace.lock().expect("trace lock").abort_fault = Some("test_async_terminal_release_failed");
        core.commands
            .send(WorkerCommand::Abort {
                key,
                failure_code: "test_async_abort",
            })
            .expect("abort must queue");

        let fault_deadline = std::time::Instant::now() + Duration::from_secs(2);
        while trace.lock().expect("trace lock").aborts == 0 {
            assert!(
                std::time::Instant::now() < fault_deadline,
                "worker did not enter fault containment"
            );
            thread::yield_now();
        }
        assert_eq!(
            core.shutdown_and_wait()
                .expect_err("queued worker fault must survive service shutdown"),
            "test_async_terminal_release_failed"
        );
        assert!(core.worker.is_none());
        assert_eq!(
            core.shutdown_and_wait()
                .expect_err("shutdown fault must remain sticky"),
            "test_async_terminal_release_failed"
        );
    }

    #[test]
    fn shutdown_originated_terminal_fault_exits_without_waiting_for_a_second_shutdown() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = core(now, trace.clone(), None, 1_000, false);
        let key = key(14);
        core.start(key, (54, 100)).expect("start must queue");
        let receipt = wait_for_armed(&mut core, key);
        assert!(matches!(
            core.poll(key, Some(&receipt)).expect("run must confirm"),
            CorePoll::Running
        ));
        trace.lock().expect("trace lock").abort_fault =
            Some("test_shutdown_terminal_binding_mismatch");

        let shutdown_started = std::time::Instant::now();
        assert_eq!(
            core.shutdown_and_wait()
                .expect_err("shutdown terminal mismatch must fail closed"),
            "test_shutdown_terminal_binding_mismatch"
        );
        assert!(shutdown_started.elapsed() < Duration::from_secs(2));
        assert!(core.worker.is_none());
        assert_eq!(trace.lock().expect("trace lock").aborts, 1);
        assert_eq!(
            core.shutdown_and_wait()
                .expect_err("terminal mismatch must remain sticky"),
            "test_shutdown_terminal_binding_mismatch"
        );
    }

    #[test]
    fn worker_panic_shutdown_failure_remains_sticky() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = core(now, trace.clone(), None, 0, false);
        let poison = trace.clone();
        assert!(catch_unwind(AssertUnwindSafe(|| {
            let _trace = poison.lock().unwrap();
            panic!("poison worker trace");
        }))
        .is_err());
        core.start(key(15), (55, 100)).expect("start must queue");

        assert_eq!(
            core.shutdown_and_wait()
                .expect_err("worker panic must fail shutdown"),
            BACKGROUND_CHANNEL_CLOSED
        );
        assert!(core.worker.is_none());
        assert_eq!(
            core.shutdown_and_wait()
                .expect_err("worker panic failure must remain sticky"),
            BACKGROUND_CHANNEL_CLOSED
        );
    }

    #[test]
    fn drop_never_waits_for_a_blocked_worker() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let entered = Arc::new(AtomicBool::new(false));
        let start_gate = Arc::new(AtomicBool::new(false));
        let mut core = BackgroundCore::spawn(
            TestDriver {
                trace: trace.clone(),
                now: now.clone(),
                start_entered: Some(entered.clone()),
                start_gate: Some(start_gate.clone()),
                intent_gate: None,
                starting_termination_available: None,
                starting_advance_available: None,
                starting_abort_available: None,
                starting_actions: 1,
                starting_kind_override: None,
                pending_polls: 0,
                fail_intent: false,
            },
            Arc::new(TestClock(now)),
        )
        .expect("worker must spawn");
        core.start(key(12), (52, 100)).expect("start must queue");
        let entered_deadline = std::time::Instant::now() + Duration::from_secs(2);
        while !entered.load(Ordering::SeqCst) {
            assert!(
                std::time::Instant::now() < entered_deadline,
                "worker did not enter start"
            );
            thread::yield_now();
        }

        let drop_started = std::time::Instant::now();
        drop(core);
        assert!(drop_started.elapsed() < Duration::from_millis(500));

        start_gate.store(true, Ordering::SeqCst);
        let cleanup_deadline = std::time::Instant::now() + Duration::from_secs(2);
        loop {
            if trace.lock().expect("trace lock").aborts == 1 {
                break;
            }
            assert!(
                std::time::Instant::now() < cleanup_deadline,
                "detached worker did not take its containment path"
            );
            thread::yield_now();
        }
    }

    #[test]
    fn deadline_records_timeout_then_uses_normal_terminal_path() {
        let now = Arc::new(AtomicU64::new(100));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = core(now, trace.clone(), None, 0, false);
        let key = key(5);
        core.start(key, (45, 100)).expect("start must queue");
        assert_eq!(
            wait_for_unarmed_terminal(&mut core, key),
            TestTerminal::TimedOut
        );
        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.intents, vec![NativeTerminationKind::TimedOut]);
        assert_eq!(trace.starting_actions, 0);
        assert_eq!(trace.aborts, 0);
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn armed_cancel_uses_the_service_recorded_kind_instead_of_the_outer_clock() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = BackgroundCore::spawn(
            TestDriver {
                trace: trace.clone(),
                now: now.clone(),
                start_entered: None,
                start_gate: None,
                intent_gate: None,
                starting_termination_available: None,
                starting_advance_available: None,
                starting_abort_available: None,
                starting_actions: 1,
                starting_kind_override: Some(NativeTerminationKind::TimedOut),
                pending_polls: 0,
                fail_intent: false,
            },
            Arc::new(TestClock(now)),
        )
        .expect("worker must spawn");
        let key = key(20);
        core.start(key, (60, 100)).expect("start must queue");
        let receipt = wait_for_armed(&mut core, key);

        assert_eq!(
            core.request_cancel(key, Some(&receipt))
                .expect("service must choose the durable terminal kind"),
            BackgroundCancelAcknowledgement::Recorded(NativeTerminationKind::TimedOut)
        );
        assert_eq!(
            wait_for_terminal(&mut core, key, &receipt),
            TestTerminal::TimedOut
        );
        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.intents, vec![NativeTerminationKind::TimedOut]);
        assert_eq!(trace.aborts, 0);
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn armed_cancel_transport_uncertainty_retains_capability_until_retry() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let termination_available = Arc::new(AtomicBool::new(false));
        let mut core = BackgroundCore::spawn(
            TestDriver {
                trace: trace.clone(),
                now: now.clone(),
                start_entered: None,
                start_gate: None,
                intent_gate: None,
                starting_termination_available: Some(termination_available.clone()),
                starting_advance_available: None,
                starting_abort_available: None,
                starting_actions: 1,
                starting_kind_override: None,
                pending_polls: 0,
                fail_intent: false,
            },
            Arc::new(TestClock(now)),
        )
        .expect("worker must spawn");
        let key = key(21);
        core.start(key, (61, 100)).expect("start must queue");
        let receipt = wait_for_armed(&mut core, key);

        assert_eq!(
            core.request_cancel(key, Some(&receipt))
                .expect("uncertainty is a typed acknowledgement"),
            BackgroundCancelAcknowledgement::Uncertain
        );
        {
            let trace = trace.lock().expect("trace lock");
            assert!(trace.intents.is_empty());
            assert_eq!(trace.aborts, 0);
        }

        termination_available.store(true, Ordering::SeqCst);
        assert_eq!(
            core.request_cancel(key, Some(&receipt))
                .expect("retry must record the exact service decision"),
            BackgroundCancelAcknowledgement::Recorded(NativeTerminationKind::Cancelled)
        );
        assert_eq!(
            wait_for_terminal(&mut core, key, &receipt),
            TestTerminal::Cancelled
        );
        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.intents, vec![NativeTerminationKind::Cancelled]);
        assert_eq!(trace.aborts, 0);
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }

    #[test]
    fn failed_intent_is_contained_as_failure_and_never_reported_as_cancelled() {
        let now = Arc::new(AtomicU64::new(10));
        let trace = Arc::new(Mutex::new(TestTrace::default()));
        let mut core = core(now, trace.clone(), None, 0, true);
        let key = key(6);
        core.start(key, (46, 100)).expect("start must queue");
        let receipt = wait_for_armed(&mut core, key);
        assert_eq!(
            core.request_cancel(key, Some(&receipt))
                .expect_err("invalid intent must fail closed"),
            "test_intent_failure"
        );
        assert_eq!(
            core.abort_and_wait(key, "test_abort")
                .expect("contained failure must remain retrievable"),
            TestTerminal::Failed
        );
        let trace = trace.lock().expect("trace lock");
        assert_eq!(trace.intents, vec![NativeTerminationKind::Cancelled]);
        assert_eq!(trace.aborts, 1);
        drop(trace);
        core.shutdown_and_wait().expect("worker shutdown");
    }
}
