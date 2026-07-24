use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fmt,
    fs::{File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
};

#[cfg(unix)]
use std::os::unix::fs::MetadataExt as UnixMetadataExt;
#[cfg(windows)]
use std::os::windows::fs::MetadataExt as WindowsMetadataExt;
#[cfg(windows)]
use std::os::windows::fs::OpenOptionsExt;

pub const FRAME_SIZE: usize = 256;
pub const MAX_RESULT_SIZE: usize = 64 * 1024;
pub const MAX_RECOVERY_RECEIPT_SIZE: usize = 16 * 1024;
pub const MAX_POLICY_SNAPSHOT_SIZE: usize = 64 * 1024;

const MAGIC: &[u8; 16] = b"VRCFAUTHLEDGER01";
const VERSION: u16 = 3;
const PAYLOAD_OFFSET: usize = 192;
const PAYLOAD_SIZE: usize = 32;
const HASH_OFFSET: usize = FRAME_SIZE - 32;
const ANCHOR_MAGIC: &[u8; 16] = b"VRCFAUTHANCHOR01";
const ANCHOR_VERSION: u16 = 1;
const ANCHOR_RECORD_SIZE: usize = 576;
const ANCHOR_FRAME_OFFSET: usize = 232;
const ANCHOR_HASH_OFFSET: usize = ANCHOR_RECORD_SIZE - 32;
const NO_TERMINAL_SEQUENCE: u64 = u64::MAX;
const ZERO_DIGEST: [u8; 32] = [0; 32];
const RECOVERY_BUNDLE_DOMAIN: &[u8] = b"vrcforge-authority-recovery-bundle-v1\0";
#[cfg(test)]
const TEST_PREPARED_RECEIPT: &[u8] = b"sealed-test-prepared-receipt-v1";
#[cfg(test)]
const TEST_POLICY_SNAPSHOT: &[u8] = b"canonical-test-policy-snapshot-v1";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LedgerError(String);

impl LedgerError {
    fn new(code: impl Into<String>) -> Self {
        Self(code.into())
    }

    pub fn code(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for LedgerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for LedgerError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LedgerIdentity {
    authority_generation_digest: [u8; 32],
    signer_key_id: [u8; 32],
}

impl LedgerIdentity {
    pub fn from_hex(
        authority_generation_digest: &str,
        signer_key_id: &str,
    ) -> Result<Self, LedgerError> {
        let authority_generation_digest = decode_digest(
            authority_generation_digest,
            "authority_generation_digest_invalid",
        )?;
        let signer_key_id = decode_digest(signer_key_id, "signer_key_id_invalid")?;
        if authority_generation_digest == ZERO_DIGEST || signer_key_id == ZERO_DIGEST {
            return Err(LedgerError::new("ledger_identity_invalid"));
        }
        Ok(Self {
            authority_generation_digest,
            signer_key_id,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TicketState {
    Issued,
    Consumed,
    Result,
    Burned,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActiveLedgerTicket {
    ticket_digest: String,
    run_binding_digest: String,
    prepared_receipt: Vec<u8>,
    canonical_policy_snapshot: Vec<u8>,
    recovery_bundle_digest: String,
    armed_receipt: Option<Vec<u8>>,
}

impl ActiveLedgerTicket {
    pub fn ticket_digest(&self) -> &str {
        &self.ticket_digest
    }

    pub fn run_binding_digest(&self) -> &str {
        &self.run_binding_digest
    }

    pub fn prepared_receipt(&self) -> &[u8] {
        &self.prepared_receipt
    }

    pub fn canonical_policy_snapshot(&self) -> &[u8] {
        &self.canonical_policy_snapshot
    }

    pub fn recovery_bundle_digest(&self) -> &str {
        &self.recovery_bundle_digest
    }

    pub fn armed_receipt(&self) -> Option<&[u8]> {
        self.armed_receipt.as_deref()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum TicketBurnReason {
    Cancelled = 1,
    TimedOut = 2,
    Failed = 3,
    RestartRecovery = 4,
}

impl TicketBurnReason {
    fn decode(value: u8) -> Result<Self, LedgerError> {
        match value {
            1 => Ok(Self::Cancelled),
            2 => Ok(Self::TimedOut),
            3 => Ok(Self::Failed),
            4 => Ok(Self::RestartRecovery),
            _ => Err(LedgerError::new("ledger_burn_reason_invalid")),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum StoredTicketState {
    Issued {
        run_binding_digest: [u8; 32],
        prepared_receipt: Option<Vec<u8>>,
        canonical_policy_snapshot: Option<Vec<u8>>,
        recovery_bundle_digest: Option<[u8; 32]>,
    },
    Consumed {
        run_binding_digest: [u8; 32],
        prepared_receipt: Vec<u8>,
        canonical_policy_snapshot: Vec<u8>,
        recovery_bundle_digest: [u8; 32],
        armed_receipt: Option<Vec<u8>>,
    },
    Result {
        run_binding_digest: [u8; 32],
        bytes: Vec<u8>,
        digest: [u8; 32],
    },
    Burned {
        run_binding_digest: [u8; 32],
        reason: TicketBurnReason,
    },
}

impl StoredTicketState {
    fn public(&self) -> TicketState {
        match self {
            Self::Issued { .. } => TicketState::Issued,
            Self::Consumed { .. } => TicketState::Consumed,
            Self::Result { .. } => TicketState::Result,
            Self::Burned { .. } => TicketState::Burned,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
enum Event {
    Initialize = 1,
    Issued = 2,
    Consumed = 3,
    ResultChunk = 4,
    ResultCommit = 5,
    Burned = 6,
    PreparedReceiptChunk = 7,
    PreparedReceiptCommit = 8,
    ArmedReceiptChunk = 9,
    ArmedReceiptCommit = 10,
    PolicySnapshotChunk = 11,
    PolicySnapshotCommit = 12,
    RecoveryBundleCommit = 13,
}

impl Event {
    fn decode(value: u8) -> Result<Self, LedgerError> {
        match value {
            1 => Ok(Self::Initialize),
            2 => Ok(Self::Issued),
            3 => Ok(Self::Consumed),
            4 => Ok(Self::ResultChunk),
            5 => Ok(Self::ResultCommit),
            6 => Ok(Self::Burned),
            7 => Ok(Self::PreparedReceiptChunk),
            8 => Ok(Self::PreparedReceiptCommit),
            9 => Ok(Self::ArmedReceiptChunk),
            10 => Ok(Self::ArmedReceiptCommit),
            11 => Ok(Self::PolicySnapshotChunk),
            12 => Ok(Self::PolicySnapshotCommit),
            13 => Ok(Self::RecoveryBundleCommit),
            _ => Err(LedgerError::new("ledger_event_invalid")),
        }
    }

    fn is_terminal(self) -> bool {
        matches!(self, Self::ResultCommit | Self::Burned)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PendingBlobKind {
    Result,
    PreparedReceipt,
    ArmedReceipt,
    PolicySnapshot,
}

#[derive(Debug)]
struct PendingBlob {
    kind: PendingBlobKind,
    ticket_digest: [u8; 32],
    bytes: Vec<u8>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct TerminalAnchor {
    sequence: u64,
    frame_hash: [u8; 32],
    ticket_digest: [u8; 32],
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct StableFileIdentity {
    #[cfg(unix)]
    device: u64,
    #[cfg(unix)]
    inode: u64,
    #[cfg(windows)]
    creation_time: u64,
    #[cfg(windows)]
    file_attributes: u32,
    #[cfg(not(any(unix, windows)))]
    created: std::time::SystemTime,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
enum AnchorRecordKind {
    Intent = 1,
    Commit = 2,
}

struct DecodedAnchorRecord {
    kind: AnchorRecordKind,
    previous_hash: [u8; 32],
    frame: [u8; FRAME_SIZE],
    terminal: Option<TerminalAnchor>,
    record_hash: [u8; 32],
}

struct LoadedAnchor {
    committed_frames: Vec<[u8; FRAME_SIZE]>,
    trailing_intent: Option<[u8; FRAME_SIZE]>,
    trailing_partial: Vec<u8>,
    previous_hash: [u8; 32],
    terminal: Option<TerminalAnchor>,
}

struct DecodedFrame {
    event: Event,
    sequence: u64,
    identity: LedgerIdentity,
    ticket_digest: [u8; 32],
    result_digest: [u8; 32],
    previous_hash: [u8; 32],
    payload: Vec<u8>,
    frame_hash: [u8; 32],
}

struct LoadedLedger {
    states: BTreeMap<[u8; 32], StoredTicketState>,
    next_sequence: u64,
    previous_hash: [u8; 32],
    pending_blob: Option<PendingBlob>,
}

pub struct AuthorityLedger {
    file: File,
    path: PathBuf,
    file_identity: StableFileIdentity,
    anchor_file: File,
    anchor_path: PathBuf,
    anchor_file_identity: StableFileIdentity,
    identity: LedgerIdentity,
    states: BTreeMap<[u8; 32], StoredTicketState>,
    next_sequence: u64,
    previous_hash: [u8; 32],
    pending_blob: Option<PendingBlob>,
    anchor_previous_hash: [u8; 32],
    terminal_anchor: Option<TerminalAnchor>,
    poisoned: bool,
}

impl fmt::Debug for AuthorityLedger {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AuthorityLedger")
            .field("ticket_count", &self.states.len())
            .field("next_sequence", &self.next_sequence)
            .field("recovery_required", &self.pending_blob.is_some())
            .field("poisoned", &self.poisoned)
            .finish()
    }
}

impl AuthorityLedger {
    pub fn provision_new(path: &Path, identity: LedgerIdentity) -> Result<Self, LedgerError> {
        let file = match open_new_file(path) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                return Err(LedgerError::new("ledger_already_exists"));
            }
            Err(_) => return Err(LedgerError::new("ledger_provision_failed")),
        };
        let anchor_path = anchor_path(path);
        let anchor_file = match open_new_file(&anchor_path) {
            Ok(file) => file,
            Err(_) => {
                drop(file);
                let _ = std::fs::remove_file(path);
                return Err(LedgerError::new("ledger_anchor_provision_failed"));
            }
        };
        let file_identity = stable_file_identity(&file)
            .map_err(|_| LedgerError::new("ledger_file_identity_unavailable"))?;
        let anchor_file_identity = stable_file_identity(&anchor_file)
            .map_err(|_| LedgerError::new("ledger_anchor_identity_unavailable"))?;
        let mut ledger = Self {
            file,
            path: path.to_path_buf(),
            file_identity,
            anchor_file,
            anchor_path,
            anchor_file_identity,
            identity,
            states: BTreeMap::new(),
            next_sequence: 0,
            previous_hash: ZERO_DIGEST,
            pending_blob: None,
            anchor_previous_hash: ZERO_DIGEST,
            terminal_anchor: None,
            poisoned: false,
        };
        ledger.append_frame_raw(Event::Initialize, ZERO_DIGEST, ZERO_DIGEST, &[])?;
        Ok(ledger)
    }

    pub fn open_existing(path: &Path, identity: LedgerIdentity) -> Result<Self, LedgerError> {
        let mut file = match open_existing_file(path) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Err(LedgerError::new("ledger_missing"));
            }
            Err(_) => return Err(LedgerError::new("ledger_open_failed")),
        };
        let anchor_path = anchor_path(path);
        let mut anchor_file = match open_existing_file(&anchor_path) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Err(LedgerError::new("ledger_anchor_missing"));
            }
            Err(_) => return Err(LedgerError::new("ledger_anchor_open_failed")),
        };
        let file_identity = stable_file_identity(&file)
            .map_err(|_| LedgerError::new("ledger_file_identity_unavailable"))?;
        let anchor_file_identity = stable_file_identity(&anchor_file)
            .map_err(|_| LedgerError::new("ledger_anchor_identity_unavailable"))?;
        verify_stable_file(path, &file, &file_identity, "ledger_file_identity_changed")?;
        verify_stable_file(
            &anchor_path,
            &anchor_file,
            &anchor_file_identity,
            "ledger_anchor_identity_changed",
        )?;
        let loaded_anchor = reconcile_anchor_and_ledger(&mut file, &mut anchor_file, &identity)?;
        let loaded = load_frames(&mut file, &identity)?;
        Ok(Self {
            file,
            path: path.to_path_buf(),
            file_identity,
            anchor_file,
            anchor_path,
            anchor_file_identity,
            identity,
            states: loaded.states,
            next_sequence: loaded.next_sequence,
            previous_hash: loaded.previous_hash,
            pending_blob: loaded.pending_blob,
            anchor_previous_hash: loaded_anchor.previous_hash,
            terminal_anchor: loaded_anchor.terminal,
            poisoned: false,
        })
    }

    #[cfg(test)]
    pub fn issue(&mut self, ticket_digest: &str) -> Result<(), LedgerError> {
        self.issue_with_binding(ticket_digest, ticket_digest)
    }

    #[cfg(test)]
    pub fn issue_with_binding(
        &mut self,
        ticket_digest: &str,
        run_binding_digest: &str,
    ) -> Result<(), LedgerError> {
        self.issue_with_binding_and_receipt(
            ticket_digest,
            run_binding_digest,
            TEST_PREPARED_RECEIPT,
        )
    }

    #[cfg(test)]
    pub fn issue_with_binding_and_receipt(
        &mut self,
        ticket_digest: &str,
        run_binding_digest: &str,
        prepared_receipt: &[u8],
    ) -> Result<(), LedgerError> {
        self.issue_with_binding_and_recovery(
            ticket_digest,
            run_binding_digest,
            prepared_receipt,
            TEST_POLICY_SNAPSHOT,
        )
    }

    pub fn issue_with_binding_and_recovery(
        &mut self,
        ticket_digest: &str,
        run_binding_digest: &str,
        prepared_receipt: &[u8],
        canonical_policy_snapshot: &[u8],
    ) -> Result<(), LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        let run_binding = decode_nonzero_digest(run_binding_digest, "run_binding_digest_invalid")?;
        validate_recovery_receipt(prepared_receipt)?;
        validate_policy_snapshot(canonical_policy_snapshot)?;
        self.ensure_writable()?;
        if self.states.contains_key(&ticket) {
            return Err(LedgerError::new("ticket_duplicate"));
        }
        self.append_frame_raw(Event::Issued, ticket, run_binding, &[])?;
        self.states.insert(
            ticket,
            StoredTicketState::Issued {
                run_binding_digest: run_binding,
                prepared_receipt: None,
                canonical_policy_snapshot: None,
                recovery_bundle_digest: None,
            },
        );
        self.append_blob(
            Event::PreparedReceiptChunk,
            Event::PreparedReceiptCommit,
            ticket,
            prepared_receipt,
        )?;
        match self.states.get_mut(&ticket) {
            Some(StoredTicketState::Issued {
                prepared_receipt: stored,
                ..
            }) => *stored = Some(prepared_receipt.to_vec()),
            _ => return Err(LedgerError::new("ledger_transition_invalid")),
        }
        self.append_blob(
            Event::PolicySnapshotChunk,
            Event::PolicySnapshotCommit,
            ticket,
            canonical_policy_snapshot,
        )?;
        match self.states.get_mut(&ticket) {
            Some(StoredTicketState::Issued {
                canonical_policy_snapshot: stored,
                ..
            }) => *stored = Some(canonical_policy_snapshot.to_vec()),
            _ => return Err(LedgerError::new("ledger_transition_invalid")),
        }
        let bundle_digest = recovery_bundle_digest_value(
            &ticket,
            &run_binding,
            prepared_receipt,
            canonical_policy_snapshot,
        );
        self.append_frame_raw(Event::RecoveryBundleCommit, ticket, bundle_digest, &[])?;
        match self.states.get_mut(&ticket) {
            Some(StoredTicketState::Issued {
                recovery_bundle_digest: stored,
                ..
            }) => *stored = Some(bundle_digest),
            _ => return Err(LedgerError::new("ledger_transition_invalid")),
        }
        Ok(())
    }

    pub fn consume(&mut self, ticket_digest: &str) -> Result<(), LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        self.ensure_writable()?;
        let (
            run_binding_digest,
            prepared_receipt,
            canonical_policy_snapshot,
            recovery_bundle_digest,
        ) = match self.states.get(&ticket) {
            None => return Err(LedgerError::new("ticket_unknown")),
            Some(StoredTicketState::Issued {
                run_binding_digest,
                prepared_receipt: Some(prepared_receipt),
                canonical_policy_snapshot: Some(canonical_policy_snapshot),
                recovery_bundle_digest: Some(recovery_bundle_digest),
            }) => (
                *run_binding_digest,
                prepared_receipt.clone(),
                canonical_policy_snapshot.clone(),
                *recovery_bundle_digest,
            ),
            Some(StoredTicketState::Issued {
                prepared_receipt: None,
                ..
            }) => return Err(LedgerError::new("ledger_prepared_receipt_required")),
            Some(StoredTicketState::Issued {
                canonical_policy_snapshot: None,
                ..
            }) => return Err(LedgerError::new("ledger_policy_snapshot_required")),
            Some(StoredTicketState::Issued {
                recovery_bundle_digest: None,
                ..
            }) => return Err(LedgerError::new("ledger_recovery_bundle_required")),
            Some(_) => return Err(LedgerError::new("ticket_transition_invalid")),
        };
        self.append_frame_raw(Event::Consumed, ticket, run_binding_digest, &[])?;
        self.states.insert(
            ticket,
            StoredTicketState::Consumed {
                run_binding_digest,
                prepared_receipt,
                canonical_policy_snapshot,
                recovery_bundle_digest,
                armed_receipt: None,
            },
        );
        Ok(())
    }

    pub fn record_armed_receipt(
        &mut self,
        ticket_digest: &str,
        run_binding_digest: &str,
        armed_receipt: &[u8],
    ) -> Result<(), LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        let expected_binding =
            decode_nonzero_digest(run_binding_digest, "run_binding_digest_invalid")?;
        validate_recovery_receipt(armed_receipt)?;
        self.ensure_writable()?;
        match self.states.get(&ticket) {
            None => return Err(LedgerError::new("ticket_unknown")),
            Some(StoredTicketState::Consumed {
                run_binding_digest,
                armed_receipt: None,
                ..
            }) if *run_binding_digest == expected_binding => {}
            Some(StoredTicketState::Consumed {
                run_binding_digest, ..
            }) if *run_binding_digest != expected_binding => {
                return Err(LedgerError::new("ticket_run_binding_mismatch"));
            }
            Some(StoredTicketState::Consumed { .. }) => {
                return Err(LedgerError::new("armed_receipt_duplicate"));
            }
            Some(_) => return Err(LedgerError::new("ticket_transition_invalid")),
        }
        self.append_blob(
            Event::ArmedReceiptChunk,
            Event::ArmedReceiptCommit,
            ticket,
            armed_receipt,
        )?;
        match self.states.get_mut(&ticket) {
            Some(StoredTicketState::Consumed {
                armed_receipt: stored,
                ..
            }) => *stored = Some(armed_receipt.to_vec()),
            _ => return Err(LedgerError::new("ledger_transition_invalid")),
        }
        Ok(())
    }

    pub fn record_result_bytes(
        &mut self,
        ticket_digest: &str,
        result_bytes: &[u8],
    ) -> Result<(), LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        if result_bytes.is_empty() {
            return Err(LedgerError::new("result_bytes_invalid"));
        }
        if result_bytes.len() > MAX_RESULT_SIZE {
            return Err(LedgerError::new("result_too_large"));
        }
        self.ensure_writable()?;
        let run_binding_digest = match self.states.get(&ticket) {
            None => return Err(LedgerError::new("ticket_unknown")),
            Some(StoredTicketState::Consumed {
                run_binding_digest, ..
            }) => *run_binding_digest,
            Some(_) => return Err(LedgerError::new("ticket_transition_invalid")),
        };

        let result_digest: [u8; 32] = Sha256::digest(result_bytes).into();
        for chunk in result_bytes.chunks(PAYLOAD_SIZE) {
            self.append_frame_raw(Event::ResultChunk, ticket, ZERO_DIGEST, chunk)?;
        }
        let length_payload = (result_bytes.len() as u64).to_be_bytes();
        self.append_frame_raw(Event::ResultCommit, ticket, result_digest, &length_payload)?;
        self.states.insert(
            ticket,
            StoredTicketState::Result {
                run_binding_digest,
                bytes: result_bytes.to_vec(),
                digest: result_digest,
            },
        );
        Ok(())
    }

    pub fn burn(&mut self, ticket_digest: &str) -> Result<(), LedgerError> {
        self.burn_with_reason(ticket_digest, TicketBurnReason::Failed)
    }

    pub fn burn_with_reason(
        &mut self,
        ticket_digest: &str,
        reason: TicketBurnReason,
    ) -> Result<(), LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        self.ensure_writable()?;
        let run_binding_digest = match self.states.get(&ticket) {
            None => return Err(LedgerError::new("ticket_unknown")),
            Some(
                StoredTicketState::Issued {
                    run_binding_digest, ..
                }
                | StoredTicketState::Consumed {
                    run_binding_digest, ..
                },
            ) => *run_binding_digest,
            Some(_) => return Err(LedgerError::new("ticket_transition_invalid")),
        };
        self.append_frame_raw(Event::Burned, ticket, run_binding_digest, &[reason as u8])?;
        self.states.insert(
            ticket,
            StoredTicketState::Burned {
                run_binding_digest,
                reason,
            },
        );
        Ok(())
    }

    pub fn burn_recovered(
        &mut self,
        ticket_digest: &str,
        run_binding_digest: &str,
    ) -> Result<(), LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        let expected_binding =
            decode_nonzero_digest(run_binding_digest, "run_binding_digest_invalid")?;
        self.ensure_not_poisoned()?;
        let stored_binding = match self.states.get(&ticket) {
            None => return Err(LedgerError::new("ticket_unknown")),
            Some(
                StoredTicketState::Issued {
                    run_binding_digest,
                    prepared_receipt: Some(_),
                    canonical_policy_snapshot: Some(_),
                    recovery_bundle_digest: Some(_),
                }
                | StoredTicketState::Consumed {
                    run_binding_digest, ..
                },
            ) => *run_binding_digest,
            Some(StoredTicketState::Issued {
                prepared_receipt: None,
                ..
            }) => return Err(LedgerError::new("ledger_prepared_receipt_required")),
            Some(StoredTicketState::Issued {
                canonical_policy_snapshot: None,
                ..
            }) => return Err(LedgerError::new("ledger_policy_snapshot_required")),
            Some(StoredTicketState::Issued {
                recovery_bundle_digest: None,
                ..
            }) => return Err(LedgerError::new("ledger_recovery_bundle_required")),
            Some(_) => return Err(LedgerError::new("ticket_transition_invalid")),
        };
        if stored_binding != expected_binding {
            return Err(LedgerError::new("ticket_run_binding_mismatch"));
        }
        if self.pending_blob.as_ref().is_some_and(|pending| {
            pending.kind != PendingBlobKind::Result || pending.ticket_digest != ticket
        }) {
            return Err(LedgerError::new("ledger_recovery_receipt_required"));
        }
        self.append_frame_raw(
            Event::Burned,
            ticket,
            stored_binding,
            &[TicketBurnReason::RestartRecovery as u8],
        )?;
        self.states.insert(
            ticket,
            StoredTicketState::Burned {
                run_binding_digest: stored_binding,
                reason: TicketBurnReason::RestartRecovery,
            },
        );
        if self.pending_blob.as_ref().is_some_and(|pending| {
            pending.kind == PendingBlobKind::Result && pending.ticket_digest == ticket
        }) {
            self.pending_blob = None;
        }
        Ok(())
    }

    pub fn active_tickets(&self) -> Result<Vec<ActiveLedgerTicket>, LedgerError> {
        self.ensure_not_poisoned()?;
        let pending_ticket = match self.pending_blob.as_ref() {
            Some(pending) if pending.kind == PendingBlobKind::Result => Some(pending.ticket_digest),
            Some(pending) => return Err(LedgerError::new(pending_blob_error(pending.kind))),
            None => None,
        };
        let mut active = Vec::new();
        if let Some(ticket) = pending_ticket {
            let (
                binding,
                prepared_receipt,
                canonical_policy_snapshot,
                recovery_bundle_digest,
                armed_receipt,
            ) = match self.states.get(&ticket) {
                Some(StoredTicketState::Consumed {
                    run_binding_digest,
                    prepared_receipt,
                    canonical_policy_snapshot,
                    recovery_bundle_digest,
                    armed_receipt,
                }) => (
                    run_binding_digest,
                    prepared_receipt,
                    canonical_policy_snapshot,
                    recovery_bundle_digest,
                    armed_receipt,
                ),
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            };
            active.push(ActiveLedgerTicket {
                ticket_digest: hex_encode(&ticket),
                run_binding_digest: hex_encode(binding),
                prepared_receipt: prepared_receipt.clone(),
                canonical_policy_snapshot: canonical_policy_snapshot.clone(),
                recovery_bundle_digest: hex_encode(recovery_bundle_digest),
                armed_receipt: armed_receipt.clone(),
            });
        }
        for (ticket, state) in &self.states {
            if Some(*ticket) == pending_ticket {
                continue;
            }
            let (
                run_binding_digest,
                prepared_receipt,
                canonical_policy_snapshot,
                recovery_bundle_digest,
                armed_receipt,
            ) = match state {
                StoredTicketState::Issued {
                    run_binding_digest,
                    prepared_receipt: Some(prepared_receipt),
                    canonical_policy_snapshot: Some(canonical_policy_snapshot),
                    recovery_bundle_digest: Some(recovery_bundle_digest),
                } => (
                    run_binding_digest,
                    prepared_receipt,
                    canonical_policy_snapshot,
                    recovery_bundle_digest,
                    None,
                ),
                StoredTicketState::Issued {
                    prepared_receipt: None,
                    ..
                } => return Err(LedgerError::new("ledger_prepared_receipt_required")),
                StoredTicketState::Issued {
                    canonical_policy_snapshot: None,
                    ..
                } => return Err(LedgerError::new("ledger_policy_snapshot_required")),
                StoredTicketState::Issued {
                    recovery_bundle_digest: None,
                    ..
                } => return Err(LedgerError::new("ledger_recovery_bundle_required")),
                StoredTicketState::Consumed {
                    run_binding_digest,
                    prepared_receipt,
                    canonical_policy_snapshot,
                    recovery_bundle_digest,
                    armed_receipt,
                } => (
                    run_binding_digest,
                    prepared_receipt,
                    canonical_policy_snapshot,
                    recovery_bundle_digest,
                    armed_receipt.clone(),
                ),
                StoredTicketState::Result { .. } | StoredTicketState::Burned { .. } => continue,
            };
            active.push(ActiveLedgerTicket {
                ticket_digest: hex_encode(ticket),
                run_binding_digest: hex_encode(run_binding_digest),
                prepared_receipt: prepared_receipt.clone(),
                canonical_policy_snapshot: canonical_policy_snapshot.clone(),
                recovery_bundle_digest: hex_encode(recovery_bundle_digest),
                armed_receipt,
            })
        }
        Ok(active)
    }

    #[cfg(test)]
    pub fn burn_active(&mut self) -> Result<usize, LedgerError> {
        self.ensure_not_poisoned()?;

        let pending_ticket = match self.pending_blob.as_ref() {
            Some(pending) if pending.kind == PendingBlobKind::Result => Some(pending.ticket_digest),
            Some(pending) => return Err(LedgerError::new(pending_blob_error(pending.kind))),
            None => None,
        };
        let mut active = Vec::new();
        if let Some(ticket) = pending_ticket {
            active.push(ticket);
        }
        active.extend(self.states.iter().filter_map(|(ticket, state)| {
            (Some(*ticket) != pending_ticket
                && matches!(
                    state,
                    StoredTicketState::Issued { .. } | StoredTicketState::Consumed { .. }
                ))
            .then_some(*ticket)
        }));

        for ticket in &active {
            let run_binding_digest = match self.states.get(ticket) {
                Some(
                    StoredTicketState::Issued {
                        run_binding_digest, ..
                    }
                    | StoredTicketState::Consumed {
                        run_binding_digest, ..
                    },
                ) => *run_binding_digest,
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            };
            self.append_frame_raw(
                Event::Burned,
                *ticket,
                run_binding_digest,
                &[TicketBurnReason::RestartRecovery as u8],
            )?;
            self.states.insert(
                *ticket,
                StoredTicketState::Burned {
                    run_binding_digest,
                    reason: TicketBurnReason::RestartRecovery,
                },
            );
            if Some(*ticket) == pending_ticket {
                self.pending_blob = None;
            }
        }
        Ok(active.len())
    }

    pub fn state(&self, ticket_digest: &str) -> Result<Option<TicketState>, LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        self.ensure_healthy()?;
        Ok(self.states.get(&ticket).map(StoredTicketState::public))
    }

    pub fn result_bytes(&self, ticket_digest: &str) -> Result<Option<Vec<u8>>, LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        self.ensure_healthy()?;
        Ok(match self.states.get(&ticket) {
            Some(StoredTicketState::Result { bytes, .. }) => Some(bytes.clone()),
            _ => None,
        })
    }

    pub fn result_digest(&self, ticket_digest: &str) -> Result<Option<String>, LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        self.ensure_healthy()?;
        Ok(match self.states.get(&ticket) {
            Some(StoredTicketState::Result { digest, .. }) => Some(hex_encode(digest)),
            _ => None,
        })
    }

    pub fn run_binding_digest(&self, ticket_digest: &str) -> Result<Option<String>, LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        self.ensure_healthy()?;
        Ok(self.states.get(&ticket).map(|state| {
            let digest = match state {
                StoredTicketState::Issued {
                    run_binding_digest, ..
                }
                | StoredTicketState::Consumed {
                    run_binding_digest, ..
                }
                | StoredTicketState::Result {
                    run_binding_digest, ..
                }
                | StoredTicketState::Burned {
                    run_binding_digest, ..
                } => run_binding_digest,
            };
            hex_encode(digest)
        }))
    }

    pub fn burn_reason(
        &self,
        ticket_digest: &str,
    ) -> Result<Option<TicketBurnReason>, LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        self.ensure_healthy()?;
        Ok(match self.states.get(&ticket) {
            Some(StoredTicketState::Burned { reason, .. }) => Some(*reason),
            _ => None,
        })
    }

    fn append_frame_raw(
        &mut self,
        event: Event,
        ticket_digest: [u8; 32],
        result_digest: [u8; 32],
        payload: &[u8],
    ) -> Result<(), LedgerError> {
        self.ensure_not_poisoned()?;
        let next_sequence = self
            .next_sequence
            .checked_add(1)
            .ok_or_else(|| LedgerError::new("ledger_sequence_exhausted"))?;
        let frame = encode_frame(
            event,
            self.next_sequence,
            &self.identity,
            ticket_digest,
            result_digest,
            self.previous_hash,
            payload,
        )?;
        if verify_stable_file(
            &self.path,
            &self.file,
            &self.file_identity,
            "ledger_file_identity_changed",
        )
        .is_err()
        {
            self.poisoned = true;
            return Err(LedgerError::new("ledger_file_identity_changed"));
        }
        if verify_stable_file(
            &self.anchor_path,
            &self.anchor_file,
            &self.anchor_file_identity,
            "ledger_anchor_identity_changed",
        )
        .is_err()
        {
            self.poisoned = true;
            return Err(LedgerError::new("ledger_anchor_identity_changed"));
        }
        let intent = encode_anchor_record(
            AnchorRecordKind::Intent,
            &self.identity,
            self.anchor_previous_hash,
            &frame,
            self.terminal_anchor,
        )?;
        if self.anchor_file.write_all(&intent).is_err() || self.anchor_file.sync_all().is_err() {
            self.poisoned = true;
            return Err(LedgerError::new("ledger_anchor_append_failed"));
        }
        self.anchor_previous_hash
            .copy_from_slice(&intent[ANCHOR_HASH_OFFSET..]);
        if self.file.write_all(&frame).is_err() || self.file.sync_all().is_err() {
            self.poisoned = true;
            return Err(LedgerError::new("ledger_append_failed"));
        }
        let terminal_anchor = terminal_after_frame(self.terminal_anchor, &frame)?;
        let commit = encode_anchor_record(
            AnchorRecordKind::Commit,
            &self.identity,
            self.anchor_previous_hash,
            &frame,
            terminal_anchor,
        )?;
        if self.anchor_file.write_all(&commit).is_err() || self.anchor_file.sync_all().is_err() {
            self.poisoned = true;
            return Err(LedgerError::new("ledger_anchor_append_failed"));
        }
        self.anchor_previous_hash
            .copy_from_slice(&commit[ANCHOR_HASH_OFFSET..]);
        self.terminal_anchor = terminal_anchor;
        self.previous_hash.copy_from_slice(&frame[HASH_OFFSET..]);
        self.next_sequence = next_sequence;
        Ok(())
    }

    fn append_blob(
        &mut self,
        chunk_event: Event,
        commit_event: Event,
        ticket_digest: [u8; 32],
        bytes: &[u8],
    ) -> Result<(), LedgerError> {
        for chunk in bytes.chunks(PAYLOAD_SIZE) {
            self.append_frame_raw(chunk_event, ticket_digest, ZERO_DIGEST, chunk)?;
        }
        let digest: [u8; 32] = Sha256::digest(bytes).into();
        self.append_frame_raw(
            commit_event,
            ticket_digest,
            digest,
            &(bytes.len() as u64).to_be_bytes(),
        )
    }

    fn ensure_writable(&self) -> Result<(), LedgerError> {
        self.ensure_healthy()
    }

    fn ensure_healthy(&self) -> Result<(), LedgerError> {
        self.ensure_not_poisoned()?;
        if let Some(pending) = self.pending_blob.as_ref() {
            return Err(LedgerError::new(pending_blob_error(pending.kind)));
        }
        if self.states.values().any(|state| {
            matches!(
                state,
                StoredTicketState::Issued {
                    prepared_receipt: None,
                    ..
                }
            )
        }) {
            return Err(LedgerError::new("ledger_prepared_receipt_required"));
        }
        if self.states.values().any(|state| {
            matches!(
                state,
                StoredTicketState::Issued {
                    canonical_policy_snapshot: None,
                    ..
                }
            )
        }) {
            return Err(LedgerError::new("ledger_policy_snapshot_required"));
        }
        if self.states.values().any(|state| {
            matches!(
                state,
                StoredTicketState::Issued {
                    recovery_bundle_digest: None,
                    ..
                }
            )
        }) {
            return Err(LedgerError::new("ledger_recovery_bundle_required"));
        }
        Ok(())
    }

    fn ensure_not_poisoned(&self) -> Result<(), LedgerError> {
        if self.poisoned {
            return Err(LedgerError::new("ledger_poisoned"));
        }
        verify_stable_file(
            &self.path,
            &self.file,
            &self.file_identity,
            "ledger_file_identity_changed",
        )?;
        verify_stable_file(
            &self.anchor_path,
            &self.anchor_file,
            &self.anchor_file_identity,
            "ledger_anchor_identity_changed",
        )?;
        Ok(())
    }
}

fn pending_blob_error(kind: PendingBlobKind) -> &'static str {
    match kind {
        PendingBlobKind::Result => "ledger_recovery_required",
        PendingBlobKind::PreparedReceipt | PendingBlobKind::ArmedReceipt => {
            "ledger_recovery_receipt_required"
        }
        PendingBlobKind::PolicySnapshot => "ledger_policy_snapshot_required",
    }
}

fn open_new_file(path: &Path) -> std::io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true).write(true).create_new(true);
    #[cfg(windows)]
    options.share_mode(0);
    options.open(path)
}

fn open_existing_file(path: &Path) -> std::io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true).write(true);
    #[cfg(windows)]
    options.share_mode(0);
    options.open(path)
}

fn anchor_path(path: &Path) -> PathBuf {
    let mut value = path.as_os_str().to_os_string();
    value.push(".anchor");
    PathBuf::from(value)
}

fn stable_file_identity(file: &File) -> std::io::Result<StableFileIdentity> {
    let metadata = file.metadata()?;
    if !metadata.is_file() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "not a regular file",
        ));
    }
    #[cfg(unix)]
    {
        Ok(StableFileIdentity {
            device: metadata.dev(),
            inode: metadata.ino(),
        })
    }
    #[cfg(windows)]
    {
        Ok(StableFileIdentity {
            creation_time: metadata.creation_time(),
            file_attributes: metadata.file_attributes(),
        })
    }
    #[cfg(not(any(unix, windows)))]
    {
        Ok(StableFileIdentity {
            created: metadata.created()?,
        })
    }
}

fn verify_stable_file(
    path: &Path,
    file: &File,
    expected: &StableFileIdentity,
    error_code: &'static str,
) -> Result<(), LedgerError> {
    let path_metadata =
        std::fs::symlink_metadata(path).map_err(|_| LedgerError::new(error_code))?;
    if path_metadata.file_type().is_symlink() || !path_metadata.is_file() {
        return Err(LedgerError::new(error_code));
    }
    let handle_identity = stable_file_identity(file).map_err(|_| LedgerError::new(error_code))?;
    #[cfg(unix)]
    let path_identity = StableFileIdentity {
        device: path_metadata.dev(),
        inode: path_metadata.ino(),
    };
    #[cfg(windows)]
    let path_identity = StableFileIdentity {
        creation_time: path_metadata.creation_time(),
        file_attributes: path_metadata.file_attributes(),
    };
    #[cfg(not(any(unix, windows)))]
    let path_identity = StableFileIdentity {
        created: path_metadata
            .created()
            .map_err(|_| LedgerError::new(error_code))?,
    };
    if handle_identity != *expected || path_identity != *expected {
        return Err(LedgerError::new(error_code));
    }
    Ok(())
}

fn terminal_after_frame(
    current: Option<TerminalAnchor>,
    frame: &[u8; FRAME_SIZE],
) -> Result<Option<TerminalAnchor>, LedgerError> {
    let decoded = decode_frame(frame)?;
    Ok(if decoded.event.is_terminal() {
        Some(TerminalAnchor {
            sequence: decoded.sequence,
            frame_hash: decoded.frame_hash,
            ticket_digest: decoded.ticket_digest,
        })
    } else {
        current
    })
}

fn encode_anchor_record(
    kind: AnchorRecordKind,
    identity: &LedgerIdentity,
    previous_hash: [u8; 32],
    frame: &[u8; FRAME_SIZE],
    terminal: Option<TerminalAnchor>,
) -> Result<[u8; ANCHOR_RECORD_SIZE], LedgerError> {
    let decoded = decode_frame(frame)?;
    if decoded.identity != *identity {
        return Err(LedgerError::new("ledger_anchor_identity_mismatch"));
    }
    let mut record = [0u8; ANCHOR_RECORD_SIZE];
    record[..16].copy_from_slice(ANCHOR_MAGIC);
    record[16..18].copy_from_slice(&ANCHOR_VERSION.to_be_bytes());
    record[18] = kind as u8;
    record[19] = decoded.event as u8;
    record[24..32].copy_from_slice(&decoded.sequence.to_be_bytes());
    record[32..64].copy_from_slice(&identity.authority_generation_digest);
    record[64..96].copy_from_slice(&identity.signer_key_id);
    record[96..128].copy_from_slice(&previous_hash);
    record[128..160].copy_from_slice(&decoded.frame_hash);
    match terminal {
        Some(terminal) => {
            record[160..168].copy_from_slice(&terminal.sequence.to_be_bytes());
            record[168..200].copy_from_slice(&terminal.frame_hash);
            record[200..232].copy_from_slice(&terminal.ticket_digest);
        }
        None => record[160..168].copy_from_slice(&NO_TERMINAL_SEQUENCE.to_be_bytes()),
    }
    record[ANCHOR_FRAME_OFFSET..ANCHOR_FRAME_OFFSET + FRAME_SIZE].copy_from_slice(frame);
    let hash = Sha256::digest(&record[..ANCHOR_HASH_OFFSET]);
    record[ANCHOR_HASH_OFFSET..].copy_from_slice(&hash);
    Ok(record)
}

fn decode_anchor_record(
    record: &[u8; ANCHOR_RECORD_SIZE],
    expected_identity: &LedgerIdentity,
) -> Result<DecodedAnchorRecord, LedgerError> {
    if &record[..16] != ANCHOR_MAGIC
        || u16::from_be_bytes(record[16..18].try_into().unwrap()) != ANCHOR_VERSION
    {
        return Err(LedgerError::new("ledger_anchor_header_invalid"));
    }
    if record[20..24].iter().any(|byte| *byte != 0)
        || record[488..ANCHOR_HASH_OFFSET]
            .iter()
            .any(|byte| *byte != 0)
    {
        return Err(LedgerError::new("ledger_anchor_reserved_bytes_invalid"));
    }
    let expected_hash = Sha256::digest(&record[..ANCHOR_HASH_OFFSET]);
    if expected_hash[..] != record[ANCHOR_HASH_OFFSET..] {
        return Err(LedgerError::new("ledger_anchor_hash_mismatch"));
    }
    let kind = match record[18] {
        1 => AnchorRecordKind::Intent,
        2 => AnchorRecordKind::Commit,
        _ => return Err(LedgerError::new("ledger_anchor_record_invalid")),
    };
    let record_identity = LedgerIdentity {
        authority_generation_digest: record[32..64].try_into().unwrap(),
        signer_key_id: record[64..96].try_into().unwrap(),
    };
    if record_identity != *expected_identity {
        return Err(LedgerError::new("ledger_anchor_identity_mismatch"));
    }
    let frame: [u8; FRAME_SIZE] = record[ANCHOR_FRAME_OFFSET..ANCHOR_FRAME_OFFSET + FRAME_SIZE]
        .try_into()
        .unwrap();
    let decoded_frame = decode_frame(&frame)?;
    if decoded_frame.identity != *expected_identity
        || decoded_frame.sequence != u64::from_be_bytes(record[24..32].try_into().unwrap())
        || decoded_frame.event as u8 != record[19]
        || decoded_frame.frame_hash != record[128..160]
    {
        return Err(LedgerError::new("ledger_anchor_frame_mismatch"));
    }
    let terminal_sequence = u64::from_be_bytes(record[160..168].try_into().unwrap());
    let terminal_hash: [u8; 32] = record[168..200].try_into().unwrap();
    let terminal_ticket: [u8; 32] = record[200..232].try_into().unwrap();
    let terminal = if terminal_sequence == NO_TERMINAL_SEQUENCE {
        if terminal_hash != ZERO_DIGEST || terminal_ticket != ZERO_DIGEST {
            return Err(LedgerError::new("ledger_terminal_anchor_invalid"));
        }
        None
    } else {
        if terminal_hash == ZERO_DIGEST || terminal_ticket == ZERO_DIGEST {
            return Err(LedgerError::new("ledger_terminal_anchor_invalid"));
        }
        Some(TerminalAnchor {
            sequence: terminal_sequence,
            frame_hash: terminal_hash,
            ticket_digest: terminal_ticket,
        })
    };
    Ok(DecodedAnchorRecord {
        kind,
        previous_hash: record[96..128].try_into().unwrap(),
        frame,
        terminal,
        record_hash: record[ANCHOR_HASH_OFFSET..].try_into().unwrap(),
    })
}

fn load_anchor(
    file: &mut File,
    expected_identity: &LedgerIdentity,
) -> Result<LoadedAnchor, LedgerError> {
    let length = file
        .metadata()
        .map_err(|_| LedgerError::new("ledger_anchor_metadata_failed"))?
        .len();
    if length == 0 {
        return Err(LedgerError::new("ledger_anchor_empty"));
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| LedgerError::new("ledger_anchor_read_failed"))?;
    let mut committed_frames = Vec::new();
    let mut pending: Option<DecodedAnchorRecord> = None;
    let mut previous_hash = ZERO_DIGEST;
    let mut terminal = None;
    let complete_count = length / ANCHOR_RECORD_SIZE as u64;
    for _ in 0..complete_count {
        let mut bytes = [0u8; ANCHOR_RECORD_SIZE];
        file.read_exact(&mut bytes)
            .map_err(|_| LedgerError::new("ledger_anchor_read_failed"))?;
        let record = decode_anchor_record(&bytes, expected_identity)?;
        if record.previous_hash != previous_hash {
            return Err(LedgerError::new("ledger_anchor_chain_mismatch"));
        }
        previous_hash = record.record_hash;
        match record.kind {
            AnchorRecordKind::Intent => {
                if u64::from_be_bytes(record.frame[24..32].try_into().unwrap())
                    != committed_frames.len() as u64
                {
                    return Err(LedgerError::new("ledger_sequence_invalid"));
                }
                if pending.is_some() || record.terminal != terminal {
                    return Err(LedgerError::new("ledger_anchor_transition_invalid"));
                }
                pending = Some(record);
            }
            AnchorRecordKind::Commit => {
                let intent = pending
                    .take()
                    .ok_or_else(|| LedgerError::new("ledger_anchor_transition_invalid"))?;
                if intent.frame != record.frame {
                    return Err(LedgerError::new("ledger_anchor_frame_mismatch"));
                }
                let expected_terminal = terminal_after_frame(terminal, &record.frame)?;
                if record.terminal != expected_terminal {
                    return Err(LedgerError::new("ledger_terminal_anchor_invalid"));
                }
                terminal = expected_terminal;
                committed_frames.push(record.frame);
            }
        }
    }
    let mut trailing_partial = vec![0u8; (length % ANCHOR_RECORD_SIZE as u64) as usize];
    if !trailing_partial.is_empty() {
        file.read_exact(&mut trailing_partial)
            .map_err(|_| LedgerError::new("ledger_anchor_read_failed"))?;
    }
    file.seek(SeekFrom::End(0))
        .map_err(|_| LedgerError::new("ledger_anchor_read_failed"))?;
    Ok(LoadedAnchor {
        committed_frames,
        trailing_intent: pending.map(|record| record.frame),
        trailing_partial,
        previous_hash,
        terminal,
    })
}

fn reconcile_anchor_and_ledger(
    ledger_file: &mut File,
    anchor_file: &mut File,
    identity: &LedgerIdentity,
) -> Result<LoadedAnchor, LedgerError> {
    let ledger_length = ledger_file
        .metadata()
        .map_err(|_| LedgerError::new("ledger_metadata_failed"))?
        .len() as usize;
    if ledger_length == 0 {
        return Err(LedgerError::new("ledger_empty"));
    }
    if ledger_length >= FRAME_SIZE {
        ledger_file
            .seek(SeekFrom::Start(0))
            .map_err(|_| LedgerError::new("ledger_read_failed"))?;
        let mut header = [0u8; FRAME_SIZE];
        ledger_file
            .read_exact(&mut header)
            .map_err(|_| LedgerError::new("ledger_read_failed"))?;
        let decoded = decode_frame(&header)?;
        if decoded.identity != *identity {
            return Err(LedgerError::new("ledger_identity_mismatch"));
        }
    }
    let mut anchor = load_anchor(anchor_file, identity)?;
    let committed_length = anchor.committed_frames.len() * FRAME_SIZE;
    if ledger_length < committed_length {
        return Err(LedgerError::new(if ledger_length % FRAME_SIZE == 0 {
            "ledger_rollback_detected"
        } else {
            "ledger_torn_tail"
        }));
    }
    ledger_file
        .seek(SeekFrom::Start(0))
        .map_err(|_| LedgerError::new("ledger_read_failed"))?;
    let mut ledger_bytes = vec![0u8; ledger_length];
    ledger_file
        .read_exact(&mut ledger_bytes)
        .map_err(|_| LedgerError::new("ledger_read_failed"))?;
    for (index, frame) in anchor.committed_frames.iter().enumerate() {
        let start = index * FRAME_SIZE;
        let actual: [u8; FRAME_SIZE] = ledger_bytes[start..start + FRAME_SIZE].try_into().unwrap();
        decode_frame(&actual)?;
        if ledger_bytes[start..start + FRAME_SIZE] != frame[..] {
            return Err(LedgerError::new("ledger_anchor_mismatch"));
        }
    }

    if !anchor.trailing_partial.is_empty() {
        let frame = anchor
            .trailing_intent
            .ok_or_else(|| LedgerError::new("ledger_anchor_torn_tail"))?;
        let terminal = terminal_after_frame(anchor.terminal, &frame)?;
        let expected_commit = encode_anchor_record(
            AnchorRecordKind::Commit,
            identity,
            anchor.previous_hash,
            &frame,
            terminal,
        )?;
        if anchor.trailing_partial.len() >= ANCHOR_RECORD_SIZE
            || anchor.trailing_partial != expected_commit[..anchor.trailing_partial.len()]
        {
            return Err(LedgerError::new("ledger_anchor_torn_tail"));
        }
        let complete_length = anchor_file
            .metadata()
            .map_err(|_| LedgerError::new("ledger_anchor_metadata_failed"))?
            .len()
            - anchor.trailing_partial.len() as u64;
        anchor_file
            .set_len(complete_length)
            .and_then(|_| anchor_file.seek(SeekFrom::End(0)).map(|_| ()))
            .and_then(|_| anchor_file.sync_all())
            .map_err(|_| LedgerError::new("ledger_anchor_recovery_failed"))?;
        anchor.trailing_partial.clear();
    }

    match anchor.trailing_intent {
        None => {
            if ledger_length % FRAME_SIZE != 0 {
                return Err(LedgerError::new("ledger_torn_tail"));
            }
            if ledger_length > committed_length {
                return Err(LedgerError::new("ledger_anchor_rollback_detected"));
            }
        }
        Some(frame) => {
            if ledger_length > committed_length + FRAME_SIZE {
                return Err(LedgerError::new(if ledger_length % FRAME_SIZE == 0 {
                    "ledger_anchor_rollback_detected"
                } else {
                    "ledger_torn_tail"
                }));
            }
            let tail = &ledger_bytes[committed_length..];
            if tail.len() == FRAME_SIZE {
                if tail != frame {
                    return Err(LedgerError::new("ledger_anchor_mismatch"));
                }
            } else {
                if tail != &frame[..tail.len()] {
                    return Err(LedgerError::new("ledger_torn_tail"));
                }
                if !tail.is_empty() {
                    ledger_file
                        .set_len(committed_length as u64)
                        .map_err(|_| LedgerError::new("ledger_recovery_failed"))?;
                }
                ledger_file
                    .seek(SeekFrom::End(0))
                    .map_err(|_| LedgerError::new("ledger_recovery_failed"))?;
                ledger_file
                    .write_all(&frame)
                    .and_then(|_| ledger_file.sync_all())
                    .map_err(|_| LedgerError::new("ledger_recovery_failed"))?;
            }
            let terminal = terminal_after_frame(anchor.terminal, &frame)?;
            let commit = encode_anchor_record(
                AnchorRecordKind::Commit,
                identity,
                anchor.previous_hash,
                &frame,
                terminal,
            )?;
            anchor_file
                .write_all(&commit)
                .and_then(|_| anchor_file.sync_all())
                .map_err(|_| LedgerError::new("ledger_anchor_recovery_failed"))?;
            anchor.previous_hash = commit[ANCHOR_HASH_OFFSET..].try_into().unwrap();
            anchor.terminal = terminal;
            anchor.committed_frames.push(frame);
            anchor.trailing_intent = None;
        }
    }
    ledger_file
        .seek(SeekFrom::End(0))
        .map_err(|_| LedgerError::new("ledger_read_failed"))?;
    Ok(anchor)
}

fn load_frames(
    file: &mut File,
    expected_identity: &LedgerIdentity,
) -> Result<LoadedLedger, LedgerError> {
    let length = file
        .metadata()
        .map_err(|_| LedgerError::new("ledger_metadata_failed"))?
        .len();
    if length == 0 {
        return Err(LedgerError::new("ledger_empty"));
    }
    if length % FRAME_SIZE as u64 != 0 {
        return Err(LedgerError::new("ledger_torn_tail"));
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| LedgerError::new("ledger_read_failed"))?;
    let count = length / FRAME_SIZE as u64;
    let mut states = BTreeMap::new();
    let mut previous_hash = ZERO_DIGEST;
    let mut pending_blob = None;
    for expected_sequence in 0..count {
        let mut bytes = [0u8; FRAME_SIZE];
        file.read_exact(&mut bytes)
            .map_err(|_| LedgerError::new("ledger_read_failed"))?;
        let frame = decode_frame(&bytes)?;
        if frame.sequence != expected_sequence {
            return Err(LedgerError::new("ledger_sequence_invalid"));
        }
        if frame.identity != *expected_identity {
            return Err(LedgerError::new("ledger_identity_mismatch"));
        }
        if frame.previous_hash != previous_hash {
            return Err(LedgerError::new("ledger_chain_mismatch"));
        }
        if expected_sequence == 0 {
            if frame.event != Event::Initialize
                || frame.ticket_digest != ZERO_DIGEST
                || frame.result_digest != ZERO_DIGEST
                || frame.previous_hash != ZERO_DIGEST
                || !frame.payload.is_empty()
            {
                return Err(LedgerError::new("ledger_header_invalid"));
            }
        } else {
            apply_loaded_event(&mut states, &mut pending_blob, &frame)?;
        }
        previous_hash = frame.frame_hash;
    }
    Ok(LoadedLedger {
        states,
        next_sequence: count,
        previous_hash,
        pending_blob,
    })
}

fn apply_loaded_event(
    states: &mut BTreeMap<[u8; 32], StoredTicketState>,
    pending_blob: &mut Option<PendingBlob>,
    frame: &DecodedFrame,
) -> Result<(), LedgerError> {
    if frame.event == Event::Initialize || frame.ticket_digest == ZERO_DIGEST {
        return Err(LedgerError::new("ledger_transition_invalid"));
    }
    if let Some(pending) = pending_blob.as_ref() {
        let allowed = match pending.kind {
            PendingBlobKind::Result => matches!(
                frame.event,
                Event::ResultChunk | Event::ResultCommit | Event::Burned
            ),
            PendingBlobKind::PreparedReceipt => matches!(
                frame.event,
                Event::PreparedReceiptChunk | Event::PreparedReceiptCommit
            ),
            PendingBlobKind::ArmedReceipt => matches!(
                frame.event,
                Event::ArmedReceiptChunk | Event::ArmedReceiptCommit
            ),
            PendingBlobKind::PolicySnapshot => matches!(
                frame.event,
                Event::PolicySnapshotChunk | Event::PolicySnapshotCommit
            ),
        };
        if frame.ticket_digest != pending.ticket_digest || !allowed {
            return Err(LedgerError::new("ledger_transition_invalid"));
        }
    }

    match frame.event {
        Event::Issued => {
            if frame.result_digest == ZERO_DIGEST
                || !frame.payload.is_empty()
                || states
                    .insert(
                        frame.ticket_digest,
                        StoredTicketState::Issued {
                            run_binding_digest: frame.result_digest,
                            prepared_receipt: None,
                            canonical_policy_snapshot: None,
                            recovery_bundle_digest: None,
                        },
                    )
                    .is_some()
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
        }
        Event::Consumed => {
            let (
                issued_binding,
                prepared_receipt,
                canonical_policy_snapshot,
                recovery_bundle_digest,
            ) = match states.get(&frame.ticket_digest) {
                Some(StoredTicketState::Issued {
                    run_binding_digest,
                    prepared_receipt: Some(prepared_receipt),
                    canonical_policy_snapshot: Some(canonical_policy_snapshot),
                    recovery_bundle_digest: Some(recovery_bundle_digest),
                }) => (
                    *run_binding_digest,
                    prepared_receipt.clone(),
                    canonical_policy_snapshot.clone(),
                    *recovery_bundle_digest,
                ),
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            };
            if frame.result_digest != issued_binding || !frame.payload.is_empty() {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            states.insert(
                frame.ticket_digest,
                StoredTicketState::Consumed {
                    run_binding_digest: issued_binding,
                    prepared_receipt,
                    canonical_policy_snapshot,
                    recovery_bundle_digest,
                    armed_receipt: None,
                },
            );
        }
        Event::ResultChunk => {
            let consumed_binding = match states.get(&frame.ticket_digest) {
                Some(StoredTicketState::Consumed {
                    run_binding_digest, ..
                }) => *run_binding_digest,
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            };
            if frame.result_digest != ZERO_DIGEST
                || frame.payload.is_empty()
                || consumed_binding == ZERO_DIGEST
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            match pending_blob {
                Some(pending) => {
                    if pending.kind != PendingBlobKind::Result {
                        return Err(LedgerError::new("ledger_transition_invalid"));
                    }
                    if pending.bytes.len() + frame.payload.len() > MAX_RESULT_SIZE {
                        return Err(LedgerError::new("ledger_result_too_large"));
                    }
                    pending.bytes.extend_from_slice(&frame.payload);
                }
                None => {
                    *pending_blob = Some(PendingBlob {
                        kind: PendingBlobKind::Result,
                        ticket_digest: frame.ticket_digest,
                        bytes: frame.payload.clone(),
                    });
                }
            }
        }
        Event::ResultCommit => {
            let consumed_binding = match states.get(&frame.ticket_digest) {
                Some(StoredTicketState::Consumed {
                    run_binding_digest, ..
                }) => *run_binding_digest,
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            };
            if frame.result_digest == ZERO_DIGEST || frame.payload.len() != 8 {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            let pending = pending_blob
                .take()
                .ok_or_else(|| LedgerError::new("ledger_transition_invalid"))?;
            if pending.kind != PendingBlobKind::Result
                || pending.ticket_digest != frame.ticket_digest
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            let declared_length = u64::from_be_bytes(frame.payload[..8].try_into().unwrap());
            let actual_digest: [u8; 32] = Sha256::digest(&pending.bytes).into();
            if declared_length != pending.bytes.len() as u64
                || pending.bytes.len() > MAX_RESULT_SIZE
                || actual_digest != frame.result_digest
            {
                return Err(LedgerError::new("ledger_result_invalid"));
            }
            states.insert(
                frame.ticket_digest,
                StoredTicketState::Result {
                    run_binding_digest: consumed_binding,
                    bytes: pending.bytes,
                    digest: frame.result_digest,
                },
            );
        }
        Event::PreparedReceiptChunk => {
            if !matches!(
                states.get(&frame.ticket_digest),
                Some(StoredTicketState::Issued {
                    prepared_receipt: None,
                    ..
                })
            ) || frame.result_digest != ZERO_DIGEST
                || frame.payload.is_empty()
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            append_loaded_blob_chunk(
                pending_blob,
                PendingBlobKind::PreparedReceipt,
                frame,
                MAX_RECOVERY_RECEIPT_SIZE,
            )?;
        }
        Event::PreparedReceiptCommit => {
            let bytes = commit_loaded_blob(
                pending_blob,
                PendingBlobKind::PreparedReceipt,
                frame,
                MAX_RECOVERY_RECEIPT_SIZE,
            )?;
            match states.get_mut(&frame.ticket_digest) {
                Some(StoredTicketState::Issued {
                    prepared_receipt: stored,
                    ..
                }) if stored.is_none() => *stored = Some(bytes),
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            }
        }
        Event::PolicySnapshotChunk => {
            if !matches!(
                states.get(&frame.ticket_digest),
                Some(StoredTicketState::Issued {
                    prepared_receipt: Some(_),
                    canonical_policy_snapshot: None,
                    recovery_bundle_digest: None,
                    ..
                })
            ) || frame.result_digest != ZERO_DIGEST
                || frame.payload.is_empty()
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            append_loaded_blob_chunk(
                pending_blob,
                PendingBlobKind::PolicySnapshot,
                frame,
                MAX_POLICY_SNAPSHOT_SIZE,
            )?;
        }
        Event::PolicySnapshotCommit => {
            let bytes = commit_loaded_blob(
                pending_blob,
                PendingBlobKind::PolicySnapshot,
                frame,
                MAX_POLICY_SNAPSHOT_SIZE,
            )?;
            match states.get_mut(&frame.ticket_digest) {
                Some(StoredTicketState::Issued {
                    canonical_policy_snapshot: stored,
                    recovery_bundle_digest: None,
                    ..
                }) if stored.is_none() => *stored = Some(bytes),
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            }
        }
        Event::RecoveryBundleCommit => {
            if !frame.payload.is_empty() || frame.result_digest == ZERO_DIGEST {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            match states.get_mut(&frame.ticket_digest) {
                Some(StoredTicketState::Issued {
                    run_binding_digest,
                    prepared_receipt: Some(prepared_receipt),
                    canonical_policy_snapshot: Some(canonical_policy_snapshot),
                    recovery_bundle_digest,
                }) if recovery_bundle_digest.is_none() => {
                    let expected = recovery_bundle_digest_value(
                        &frame.ticket_digest,
                        run_binding_digest,
                        prepared_receipt,
                        canonical_policy_snapshot,
                    );
                    if frame.result_digest != expected {
                        return Err(LedgerError::new("ledger_recovery_bundle_invalid"));
                    }
                    *recovery_bundle_digest = Some(expected);
                }
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            }
        }
        Event::ArmedReceiptChunk => {
            if !matches!(
                states.get(&frame.ticket_digest),
                Some(StoredTicketState::Consumed {
                    armed_receipt: None,
                    ..
                })
            ) || frame.result_digest != ZERO_DIGEST
                || frame.payload.is_empty()
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            append_loaded_blob_chunk(
                pending_blob,
                PendingBlobKind::ArmedReceipt,
                frame,
                MAX_RECOVERY_RECEIPT_SIZE,
            )?;
        }
        Event::ArmedReceiptCommit => {
            let bytes = commit_loaded_blob(
                pending_blob,
                PendingBlobKind::ArmedReceipt,
                frame,
                MAX_RECOVERY_RECEIPT_SIZE,
            )?;
            match states.get_mut(&frame.ticket_digest) {
                Some(StoredTicketState::Consumed {
                    armed_receipt: stored,
                    ..
                }) if stored.is_none() => *stored = Some(bytes),
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            }
        }
        Event::Burned => {
            let stored_binding = match states.get(&frame.ticket_digest) {
                Some(
                    StoredTicketState::Issued {
                        run_binding_digest,
                        prepared_receipt: Some(_),
                        canonical_policy_snapshot: Some(_),
                        recovery_bundle_digest: Some(_),
                    }
                    | StoredTicketState::Consumed {
                        run_binding_digest, ..
                    },
                ) => *run_binding_digest,
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            };
            if frame.result_digest != stored_binding || frame.payload.len() != 1 {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            let reason = TicketBurnReason::decode(frame.payload[0])?;
            if pending_blob.as_ref().is_some_and(|pending| {
                pending.kind != PendingBlobKind::Result
                    || pending.ticket_digest != frame.ticket_digest
            }) {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            *pending_blob = None;
            states.insert(
                frame.ticket_digest,
                StoredTicketState::Burned {
                    run_binding_digest: stored_binding,
                    reason,
                },
            );
        }
        Event::Initialize => return Err(LedgerError::new("ledger_transition_invalid")),
    }
    Ok(())
}

fn append_loaded_blob_chunk(
    pending_blob: &mut Option<PendingBlob>,
    kind: PendingBlobKind,
    frame: &DecodedFrame,
    max_size: usize,
) -> Result<(), LedgerError> {
    match pending_blob {
        Some(pending) => {
            if pending.kind != kind || pending.ticket_digest != frame.ticket_digest {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            if pending.bytes.len() + frame.payload.len() > max_size {
                return Err(LedgerError::new("ledger_recovery_receipt_too_large"));
            }
            pending.bytes.extend_from_slice(&frame.payload);
        }
        None => {
            *pending_blob = Some(PendingBlob {
                kind,
                ticket_digest: frame.ticket_digest,
                bytes: frame.payload.clone(),
            });
        }
    }
    Ok(())
}

fn commit_loaded_blob(
    pending_blob: &mut Option<PendingBlob>,
    kind: PendingBlobKind,
    frame: &DecodedFrame,
    max_size: usize,
) -> Result<Vec<u8>, LedgerError> {
    if frame.result_digest == ZERO_DIGEST || frame.payload.len() != 8 {
        return Err(LedgerError::new("ledger_transition_invalid"));
    }
    let pending = pending_blob
        .take()
        .ok_or_else(|| LedgerError::new("ledger_transition_invalid"))?;
    if pending.kind != kind || pending.ticket_digest != frame.ticket_digest {
        return Err(LedgerError::new("ledger_transition_invalid"));
    }
    let declared_length = u64::from_be_bytes(frame.payload[..8].try_into().unwrap());
    let actual_digest: [u8; 32] = Sha256::digest(&pending.bytes).into();
    if pending.bytes.is_empty()
        || pending.bytes.len() > max_size
        || declared_length != pending.bytes.len() as u64
        || actual_digest != frame.result_digest
    {
        return Err(LedgerError::new("ledger_recovery_receipt_invalid"));
    }
    Ok(pending.bytes)
}

fn encode_frame(
    event: Event,
    sequence: u64,
    identity: &LedgerIdentity,
    ticket_digest: [u8; 32],
    result_digest: [u8; 32],
    previous_hash: [u8; 32],
    payload: &[u8],
) -> Result<[u8; FRAME_SIZE], LedgerError> {
    if payload.len() > PAYLOAD_SIZE {
        return Err(LedgerError::new("ledger_payload_invalid"));
    }
    let mut frame = [0u8; FRAME_SIZE];
    frame[..16].copy_from_slice(MAGIC);
    frame[16..18].copy_from_slice(&VERSION.to_be_bytes());
    frame[18] = event as u8;
    frame[19] = payload.len() as u8;
    frame[24..32].copy_from_slice(&sequence.to_be_bytes());
    frame[32..64].copy_from_slice(&identity.authority_generation_digest);
    frame[64..96].copy_from_slice(&identity.signer_key_id);
    frame[96..128].copy_from_slice(&ticket_digest);
    frame[128..160].copy_from_slice(&result_digest);
    frame[160..192].copy_from_slice(&previous_hash);
    frame[PAYLOAD_OFFSET..PAYLOAD_OFFSET + payload.len()].copy_from_slice(payload);
    let hash = Sha256::digest(&frame[..HASH_OFFSET]);
    frame[HASH_OFFSET..].copy_from_slice(&hash);
    Ok(frame)
}

fn decode_frame(frame: &[u8; FRAME_SIZE]) -> Result<DecodedFrame, LedgerError> {
    if &frame[..16] != MAGIC || u16::from_be_bytes(frame[16..18].try_into().unwrap()) != VERSION {
        return Err(LedgerError::new("ledger_frame_header_invalid"));
    }
    let payload_length = frame[19] as usize;
    if payload_length > PAYLOAD_SIZE {
        return Err(LedgerError::new("ledger_payload_invalid"));
    }
    if frame[20..24].iter().any(|byte| *byte != 0)
        || frame[PAYLOAD_OFFSET + payload_length..HASH_OFFSET]
            .iter()
            .any(|byte| *byte != 0)
    {
        return Err(LedgerError::new("ledger_reserved_bytes_invalid"));
    }
    let expected_hash = Sha256::digest(&frame[..HASH_OFFSET]);
    if expected_hash[..] != frame[HASH_OFFSET..] {
        return Err(LedgerError::new("ledger_hash_mismatch"));
    }
    Ok(DecodedFrame {
        event: Event::decode(frame[18])?,
        sequence: u64::from_be_bytes(frame[24..32].try_into().unwrap()),
        identity: LedgerIdentity {
            authority_generation_digest: frame[32..64].try_into().unwrap(),
            signer_key_id: frame[64..96].try_into().unwrap(),
        },
        ticket_digest: frame[96..128].try_into().unwrap(),
        result_digest: frame[128..160].try_into().unwrap(),
        previous_hash: frame[160..192].try_into().unwrap(),
        payload: frame[PAYLOAD_OFFSET..PAYLOAD_OFFSET + payload_length].to_vec(),
        frame_hash: frame[HASH_OFFSET..].try_into().unwrap(),
    })
}

fn decode_nonzero_digest(value: &str, code: &'static str) -> Result<[u8; 32], LedgerError> {
    let digest = decode_digest(value, code)?;
    if digest == ZERO_DIGEST {
        return Err(LedgerError::new(code));
    }
    Ok(digest)
}

fn validate_recovery_receipt(receipt: &[u8]) -> Result<(), LedgerError> {
    if receipt.is_empty() {
        return Err(LedgerError::new("recovery_receipt_invalid"));
    }
    if receipt.len() > MAX_RECOVERY_RECEIPT_SIZE {
        return Err(LedgerError::new("recovery_receipt_too_large"));
    }
    Ok(())
}

fn validate_policy_snapshot(snapshot: &[u8]) -> Result<(), LedgerError> {
    if snapshot.is_empty() {
        return Err(LedgerError::new("policy_snapshot_invalid"));
    }
    if snapshot.len() > MAX_POLICY_SNAPSHOT_SIZE {
        return Err(LedgerError::new("policy_snapshot_too_large"));
    }
    Ok(())
}

pub fn compute_recovery_bundle_digest(
    ticket_digest: &str,
    run_binding_digest: &str,
    prepared_receipt: &[u8],
    canonical_policy_snapshot: &[u8],
) -> Result<String, LedgerError> {
    let ticket_digest = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
    let run_binding_digest =
        decode_nonzero_digest(run_binding_digest, "run_binding_digest_invalid")?;
    validate_recovery_receipt(prepared_receipt)?;
    validate_policy_snapshot(canonical_policy_snapshot)?;
    Ok(hex_encode(&recovery_bundle_digest_value(
        &ticket_digest,
        &run_binding_digest,
        prepared_receipt,
        canonical_policy_snapshot,
    )))
}

fn recovery_bundle_digest_value(
    ticket_digest: &[u8; 32],
    run_binding_digest: &[u8; 32],
    prepared_receipt: &[u8],
    canonical_policy_snapshot: &[u8],
) -> [u8; 32] {
    let prepared_digest: [u8; 32] = Sha256::digest(prepared_receipt).into();
    let snapshot_digest: [u8; 32] = Sha256::digest(canonical_policy_snapshot).into();
    let mut digest = Sha256::new();
    digest.update(RECOVERY_BUNDLE_DOMAIN);
    digest.update(ticket_digest);
    digest.update(run_binding_digest);
    digest.update((prepared_receipt.len() as u64).to_be_bytes());
    digest.update(prepared_digest);
    digest.update((canonical_policy_snapshot.len() as u64).to_be_bytes());
    digest.update(snapshot_digest);
    digest.finalize().into()
}

fn decode_digest(value: &str, code: &'static str) -> Result<[u8; 32], LedgerError> {
    if value.len() != 64
        || !value
            .as_bytes()
            .iter()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
    {
        return Err(LedgerError::new(code));
    }
    let mut output = [0u8; 32];
    for (index, chunk) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (hex_nibble(chunk[0]) << 4) | hex_nibble(chunk[1]);
    }
    Ok(output)
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

#[cfg(test)]
#[path = "primitive_evidence_authority_ledger/tests.rs"]
mod tests;
