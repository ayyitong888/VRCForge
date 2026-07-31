use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
    fs::{File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
};

use crate::primitive_evidence_authority_blob::{
    AuthenticatedProtectedBlobNamespace, ProtectedBlobAuthority, ProtectedBlobBindingContext,
    ProtectedBlobKind,
};

#[cfg(windows)]
use crate::primitive_evidence_authority_install::finalizer_commit_store_windows::{
    VerifiedPublishedRuntimeBindingProjection, VerifiedPublishedRuntimeLedgerNamespaceLease,
    VerifiedPublishedRuntimeLedgerPair,
};
#[cfg(unix)]
use std::os::unix::fs::MetadataExt as UnixMetadataExt;
#[cfg(windows)]
use std::os::windows::{
    fs::{MetadataExt as WindowsMetadataExt, OpenOptionsExt},
    io::AsRawHandle,
};
#[cfg(windows)]
use windows_sys::Win32::Storage::FileSystem::{
    FileIdInfo, FileStandardInfo, GetFileInformationByHandleEx, FILE_ATTRIBUTE_DIRECTORY,
    FILE_ATTRIBUTE_REPARSE_POINT, FILE_ID_INFO, FILE_STANDARD_INFO,
};

pub const FRAME_SIZE: usize = 256;
pub const MAX_RESULT_SIZE: usize = 64 * 1024;
pub const MAX_RECOVERY_RECEIPT_SIZE: usize = 16 * 1024;
pub const MAX_POLICY_SNAPSHOT_SIZE: usize = 64 * 1024;
pub const MAX_ORIGIN_ENVELOPE_SIZE: usize = 512 * 1024;
pub const MAX_RESULT_PROJECTION_SIZE: usize = 10 * 1024 * 1024 + 64 * 1024;

// One generation is intentionally much smaller than the surrounding
// FinalCommit file ceiling. These caps bound replay memory, protected storage,
// and historical terminal state, not only currently active tickets.
pub(crate) const MAX_GENERATION_FRAME_COUNT: u64 = 64 * 1024;
pub(crate) const MAX_GENERATION_TICKET_COUNT: u64 = 1024;
pub(crate) const MAX_GENERATION_BLOBS_PER_TICKET: u64 = 3;
pub(crate) const MAX_GENERATION_REFERENCED_BLOB_COUNT: u64 =
    MAX_GENERATION_TICKET_COUNT * MAX_GENERATION_BLOBS_PER_TICKET;
pub(crate) const MAX_GENERATION_LOGICAL_EVIDENCE_BYTES: u64 = 256 * 1024 * 1024;
pub(crate) const MAX_GENERATION_STORED_BYTES: u64 = 384 * 1024 * 1024;

const MAGIC: &[u8; 16] = b"VRCFAUTHLEDGER01";
const VERSION: u16 = 3;
const PAYLOAD_OFFSET: usize = 192;
const PAYLOAD_SIZE: usize = 32;
const HASH_OFFSET: usize = FRAME_SIZE - 32;
const ANCHOR_MAGIC: &[u8; 16] = b"VRCFAUTHANCHOR01";
const ANCHOR_VERSION: u16 = 1;
pub(crate) const ANCHOR_RECORD_SIZE: usize = 576;
const ANCHOR_FRAME_OFFSET: usize = 232;
const ANCHOR_HASH_OFFSET: usize = ANCHOR_RECORD_SIZE - 32;
const STORED_FRAME_BYTES: u64 = FRAME_SIZE as u64 + 2 * ANCHOR_RECORD_SIZE as u64;
const MAX_GENERATION_LEDGER_BYTES: u64 = MAX_GENERATION_FRAME_COUNT * FRAME_SIZE as u64;
const NO_TERMINAL_SEQUENCE: u64 = u64::MAX;
const ZERO_DIGEST: [u8; 32] = [0; 32];
const RECOVERY_BUNDLE_DOMAIN: &[u8] = b"vrcforge-authority-recovery-bundle-v1\0";
const RECOVERED_BURN_PROOF_DOMAIN: &[u8] = b"vrcforge-authority-recovered-burn-proof-v1\0";
const LEDGER_IDENTITY_DIGEST_DOMAIN: &[u8] = b"vrcforge-authority-ledger-identity-v1\0";
const VERIFIED_RESULT_RECORD_DOMAIN: &[u8] = b"vrcforge-authority-verified-result-record-v1\0";
const RESULT_RECEIPT_PREDECESSOR_DOMAIN: &[u8] =
    b"vrcforge-authority-result-receipt-predecessor-v1\0";
const VERIFIED_RESULT_RECORD_MAGIC: &[u8; 16] = b"VRCFVERRESULT001";
const VERIFIED_RESULT_RECORD_VERSION: u16 = 1;
const VERIFIED_RESULT_RECORD_DIGEST_COUNT: usize = 9;
const VERIFIED_RESULT_RECORD_FIXED_SIZE: usize =
    16 + 2 + 4 + 4 + VERIFIED_RESULT_RECORD_DIGEST_COUNT * 32 + 32;
const MAX_VERIFIED_RESULT_RECORD_SIZE: usize =
    VERIFIED_RESULT_RECORD_FIXED_SIZE + MAX_RESULT_SIZE + MAX_ORIGIN_ENVELOPE_SIZE;
const FILE_IDENTITY_DIGEST_DOMAIN: &[u8] = b"vrcforge-authority-ledger-file-identity-v1\0";
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
        Self::from_digests(authority_generation_digest, signer_key_id)
    }

    pub fn from_digests(
        authority_generation_digest: [u8; 32],
        signer_key_id: [u8; 32],
    ) -> Result<Self, LedgerError> {
        if authority_generation_digest == ZERO_DIGEST || signer_key_id == ZERO_DIGEST {
            return Err(LedgerError::new("ledger_identity_invalid"));
        }
        Ok(Self {
            authority_generation_digest,
            signer_key_id,
        })
    }

    pub fn authority_generation_digest(&self) -> &[u8; 32] {
        &self.authority_generation_digest
    }

    pub fn signer_key_id(&self) -> &[u8; 32] {
        &self.signer_key_id
    }

    pub fn canonical_digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(LEDGER_IDENTITY_DIGEST_DOMAIN);
        digest.update(self.authority_generation_digest);
        digest.update(self.signer_key_id);
        digest.finalize().into()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TicketState {
    Issued,
    Consumed,
    ResultPendingProjection,
    Result,
    Burned,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DurableVerifiedResult {
    finalization_bytes: Vec<u8>,
    origin_envelope_bytes: Vec<u8>,
    ticket_digest: [u8; 32],
    run_binding_digest: [u8; 32],
    finalization_digest: [u8; 32],
    origin_envelope_digest: [u8; 32],
    cleanup_digest: [u8; 32],
    prepared_receipt_digest: [u8; 32],
    armed_receipt_digest: [u8; 32],
    policy_snapshot_digest: [u8; 32],
    recovery_bundle_digest: [u8; 32],
}

impl DurableVerifiedResult {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        finalization_bytes: Vec<u8>,
        origin_envelope_bytes: Vec<u8>,
        ticket_digest: [u8; 32],
        run_binding_digest: [u8; 32],
        finalization_digest: [u8; 32],
        origin_envelope_digest: [u8; 32],
        cleanup_digest: [u8; 32],
        prepared_receipt_digest: [u8; 32],
        armed_receipt_digest: [u8; 32],
        policy_snapshot_digest: [u8; 32],
        recovery_bundle_digest: [u8; 32],
    ) -> Result<Self, LedgerError> {
        let value = Self {
            finalization_bytes,
            origin_envelope_bytes,
            ticket_digest,
            run_binding_digest,
            finalization_digest,
            origin_envelope_digest,
            cleanup_digest,
            prepared_receipt_digest,
            armed_receipt_digest,
            policy_snapshot_digest,
            recovery_bundle_digest,
        };
        value.validate()?;
        Ok(value)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, LedgerError> {
        if bytes.len() < VERIFIED_RESULT_RECORD_FIXED_SIZE
            || bytes.get(..16) != Some(VERIFIED_RESULT_RECORD_MAGIC)
            || u16::from_be_bytes(bytes[16..18].try_into().unwrap())
                != VERIFIED_RESULT_RECORD_VERSION
        {
            return Err(LedgerError::new("verified_result_record_invalid"));
        }
        let finalization_length = u32::from_be_bytes(bytes[18..22].try_into().unwrap()) as usize;
        let origin_length = u32::from_be_bytes(bytes[22..26].try_into().unwrap()) as usize;
        let expected_length = VERIFIED_RESULT_RECORD_FIXED_SIZE
            .checked_add(finalization_length)
            .and_then(|value| value.checked_add(origin_length))
            .ok_or_else(|| LedgerError::new("verified_result_record_invalid"))?;
        if finalization_length == 0
            || finalization_length > MAX_RESULT_SIZE
            || origin_length == 0
            || origin_length > MAX_ORIGIN_ENVELOPE_SIZE
            || bytes.len() != expected_length
        {
            return Err(LedgerError::new("verified_result_record_invalid"));
        }
        let digest_offset = bytes.len() - 32;
        let expected_digest: [u8; 32] =
            Sha256::digest([VERIFIED_RESULT_RECORD_DOMAIN, &bytes[..digest_offset]].concat())
                .into();
        if bytes[digest_offset..] != expected_digest {
            return Err(LedgerError::new("verified_result_record_digest_mismatch"));
        }
        let mut offset = 26usize;
        let mut take_digest = || {
            let digest: [u8; 32] = bytes[offset..offset + 32].try_into().unwrap();
            offset += 32;
            digest
        };
        let ticket_digest = take_digest();
        let run_binding_digest = take_digest();
        let finalization_digest = take_digest();
        let origin_envelope_digest = take_digest();
        let cleanup_digest = take_digest();
        let prepared_receipt_digest = take_digest();
        let armed_receipt_digest = take_digest();
        let policy_snapshot_digest = take_digest();
        let recovery_bundle_digest = take_digest();
        let finalization_bytes = bytes[offset..offset + finalization_length].to_vec();
        offset += finalization_length;
        let origin_envelope_bytes = bytes[offset..offset + origin_length].to_vec();
        offset += origin_length;
        if offset + 32 != bytes.len() {
            return Err(LedgerError::new("verified_result_record_invalid"));
        }
        Self::new(
            finalization_bytes,
            origin_envelope_bytes,
            ticket_digest,
            run_binding_digest,
            finalization_digest,
            origin_envelope_digest,
            cleanup_digest,
            prepared_receipt_digest,
            armed_receipt_digest,
            policy_snapshot_digest,
            recovery_bundle_digest,
        )
    }

    pub fn encode(&self) -> Vec<u8> {
        let mut bytes = Vec::with_capacity(
            VERIFIED_RESULT_RECORD_FIXED_SIZE
                + self.finalization_bytes.len()
                + self.origin_envelope_bytes.len(),
        );
        bytes.extend_from_slice(VERIFIED_RESULT_RECORD_MAGIC);
        bytes.extend_from_slice(&VERIFIED_RESULT_RECORD_VERSION.to_be_bytes());
        bytes.extend_from_slice(&(self.finalization_bytes.len() as u32).to_be_bytes());
        bytes.extend_from_slice(&(self.origin_envelope_bytes.len() as u32).to_be_bytes());
        for digest in [
            self.ticket_digest,
            self.run_binding_digest,
            self.finalization_digest,
            self.origin_envelope_digest,
            self.cleanup_digest,
            self.prepared_receipt_digest,
            self.armed_receipt_digest,
            self.policy_snapshot_digest,
            self.recovery_bundle_digest,
        ] {
            bytes.extend_from_slice(&digest);
        }
        bytes.extend_from_slice(&self.finalization_bytes);
        bytes.extend_from_slice(&self.origin_envelope_bytes);
        let digest = Sha256::digest([VERIFIED_RESULT_RECORD_DOMAIN, &bytes].concat());
        bytes.extend_from_slice(&digest);
        bytes
    }

    pub fn finalization_bytes(&self) -> &[u8] {
        &self.finalization_bytes
    }

    pub fn origin_envelope_bytes(&self) -> &[u8] {
        &self.origin_envelope_bytes
    }

    pub fn ticket_digest(&self) -> &[u8; 32] {
        &self.ticket_digest
    }

    pub fn run_binding_digest(&self) -> &[u8; 32] {
        &self.run_binding_digest
    }

    pub fn finalization_digest(&self) -> &[u8; 32] {
        &self.finalization_digest
    }

    pub fn origin_envelope_digest(&self) -> &[u8; 32] {
        &self.origin_envelope_digest
    }

    pub fn cleanup_digest(&self) -> &[u8; 32] {
        &self.cleanup_digest
    }

    pub fn prepared_receipt_digest(&self) -> &[u8; 32] {
        &self.prepared_receipt_digest
    }

    pub fn armed_receipt_digest(&self) -> &[u8; 32] {
        &self.armed_receipt_digest
    }

    pub fn policy_snapshot_digest(&self) -> &[u8; 32] {
        &self.policy_snapshot_digest
    }

    pub fn recovery_bundle_digest(&self) -> &[u8; 32] {
        &self.recovery_bundle_digest
    }

    fn validate(&self) -> Result<(), LedgerError> {
        let computed_finalization_digest: [u8; 32] =
            Sha256::digest(&self.finalization_bytes).into();
        let computed_origin_digest: [u8; 32] = Sha256::digest(&self.origin_envelope_bytes).into();
        if self.finalization_bytes.is_empty()
            || self.finalization_bytes.len() > MAX_RESULT_SIZE
            || self.origin_envelope_bytes.is_empty()
            || self.origin_envelope_bytes.len() > MAX_ORIGIN_ENVELOPE_SIZE
            || [
                self.ticket_digest,
                self.run_binding_digest,
                self.finalization_digest,
                self.origin_envelope_digest,
                self.cleanup_digest,
                self.prepared_receipt_digest,
                self.armed_receipt_digest,
                self.policy_snapshot_digest,
                self.recovery_bundle_digest,
            ]
            .iter()
            .any(|digest| *digest == ZERO_DIGEST)
            || computed_finalization_digest != self.finalization_digest
            || computed_origin_digest != self.origin_envelope_digest
        {
            return Err(LedgerError::new("verified_result_record_invalid"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PendingVerifiedResult {
    record: DurableVerifiedResult,
    prepared_receipt: Vec<u8>,
    canonical_policy_snapshot: Vec<u8>,
    recovery_bundle_digest: [u8; 32],
    armed_receipt: Vec<u8>,
    result_committed: bool,
    projection: Option<(Vec<u8>, [u8; 32])>,
}

impl PendingVerifiedResult {
    pub fn record(&self) -> &DurableVerifiedResult {
        &self.record
    }

    pub fn prepared_receipt(&self) -> &[u8] {
        &self.prepared_receipt
    }

    pub fn canonical_policy_snapshot(&self) -> &[u8] {
        &self.canonical_policy_snapshot
    }

    pub fn recovery_bundle_digest(&self) -> &[u8; 32] {
        &self.recovery_bundle_digest
    }

    pub fn armed_receipt(&self) -> &[u8] {
        &self.armed_receipt
    }

    pub fn result_committed(&self) -> bool {
        self.result_committed
    }

    pub fn projection(&self) -> Option<(&[u8], &[u8; 32])> {
        self.projection
            .as_ref()
            .map(|(bytes, digest)| (bytes.as_slice(), digest))
    }
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

/// Supervisor-verified evidence for preserving a normal terminal reason while
/// recovering an active ticket after restart.
///
/// The digest is domain-separated and binds every field needed to distinguish
/// one recovery decision from another. An absent Armed receipt is represented
/// explicitly so a pre-Armed cancellation or timeout cannot be confused with
/// an Armed run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecoveredBurnProof {
    recovery_proof_digest: [u8; 32],
    ticket_digest: [u8; 32],
    run_binding_digest: [u8; 32],
    prepared_receipt_digest: [u8; 32],
    armed_receipt_digest: Option<[u8; 32]>,
    stage_journal_head_digest: [u8; 32],
    termination_intent_digest: [u8; 32],
    terminal_digest: [u8; 32],
    cleanup_digest: [u8; 32],
    reason: TicketBurnReason,
}

impl RecoveredBurnProof {
    #[allow(clippy::too_many_arguments)]
    pub fn from_verified_digest(
        recovery_proof_digest: [u8; 32],
        ticket_digest: [u8; 32],
        run_binding_digest: [u8; 32],
        prepared_receipt_digest: [u8; 32],
        armed_receipt_digest: Option<[u8; 32]>,
        stage_journal_head_digest: [u8; 32],
        termination_intent_digest: [u8; 32],
        terminal_digest: [u8; 32],
        cleanup_digest: [u8; 32],
        reason: TicketBurnReason,
    ) -> Result<Self, LedgerError> {
        let proof = Self {
            recovery_proof_digest,
            ticket_digest,
            run_binding_digest,
            prepared_receipt_digest,
            armed_receipt_digest,
            stage_journal_head_digest,
            termination_intent_digest,
            terminal_digest,
            cleanup_digest,
            reason,
        };
        proof.validate()?;
        Ok(proof)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn canonical_digest(
        ticket_digest: [u8; 32],
        run_binding_digest: [u8; 32],
        prepared_receipt_digest: [u8; 32],
        armed_receipt_digest: Option<[u8; 32]>,
        stage_journal_head_digest: [u8; 32],
        termination_intent_digest: [u8; 32],
        terminal_digest: [u8; 32],
        cleanup_digest: [u8; 32],
        reason: TicketBurnReason,
    ) -> Result<[u8; 32], LedgerError> {
        if !matches!(
            reason,
            TicketBurnReason::Cancelled | TicketBurnReason::TimedOut
        ) {
            return Err(LedgerError::new("ledger_recovery_burn_reason_invalid"));
        }
        if [
            ticket_digest,
            run_binding_digest,
            prepared_receipt_digest,
            stage_journal_head_digest,
            termination_intent_digest,
            terminal_digest,
            cleanup_digest,
        ]
        .iter()
        .any(|digest| *digest == ZERO_DIGEST)
            || armed_receipt_digest.is_some_and(|digest| digest == ZERO_DIGEST)
        {
            return Err(LedgerError::new("ledger_recovery_proof_invalid"));
        }

        let mut digest = Sha256::new();
        digest.update(RECOVERED_BURN_PROOF_DOMAIN);
        digest.update([reason as u8]);
        digest.update(ticket_digest);
        digest.update(run_binding_digest);
        digest.update(prepared_receipt_digest);
        match armed_receipt_digest {
            Some(armed) => {
                digest.update([1]);
                digest.update(armed);
            }
            None => {
                digest.update([0]);
                digest.update(ZERO_DIGEST);
            }
        }
        digest.update(stage_journal_head_digest);
        digest.update(termination_intent_digest);
        digest.update(terminal_digest);
        digest.update(cleanup_digest);
        Ok(digest.finalize().into())
    }

    pub fn recovery_proof_digest(&self) -> &[u8; 32] {
        &self.recovery_proof_digest
    }

    fn validate(&self) -> Result<(), LedgerError> {
        let expected = Self::canonical_digest(
            self.ticket_digest,
            self.run_binding_digest,
            self.prepared_receipt_digest,
            self.armed_receipt_digest,
            self.stage_journal_head_digest,
            self.termination_intent_digest,
            self.terminal_digest,
            self.cleanup_digest,
            self.reason,
        )?;
        if self.recovery_proof_digest == ZERO_DIGEST || self.recovery_proof_digest != expected {
            return Err(LedgerError::new("ledger_recovery_proof_invalid"));
        }
        Ok(())
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
        projection: Option<(Vec<u8>, [u8; 32])>,
    },
    ResultPendingProjection {
        run_binding_digest: [u8; 32],
        prepared_receipt: Vec<u8>,
        canonical_policy_snapshot: Vec<u8>,
        recovery_bundle_digest: [u8; 32],
        armed_receipt: Vec<u8>,
        verified_result: DurableVerifiedResult,
        result: Option<(Vec<u8>, [u8; 32])>,
        projection: Option<(Vec<u8>, [u8; 32])>,
    },
    Burned {
        run_binding_digest: [u8; 32],
        reason: TicketBurnReason,
        recovery_proof_digest: Option<[u8; 32]>,
    },
}

impl StoredTicketState {
    fn public(&self) -> TicketState {
        match self {
            Self::Issued { .. } => TicketState::Issued,
            Self::Consumed { .. } => TicketState::Consumed,
            Self::ResultPendingProjection { .. } => TicketState::ResultPendingProjection,
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
    VerifiedResultChunk = 14,
    VerifiedResultCommit = 15,
    ProjectionChunk = 16,
    ProjectionPendingCommit = 17,
    ProjectionCommit = 18,
    RecoveredBurned = 19,
    VerifiedResultBlobBind = 20,
    ResultBlobBind = 21,
    ProjectionBlobBind = 22,
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
            14 => Ok(Self::VerifiedResultChunk),
            15 => Ok(Self::VerifiedResultCommit),
            16 => Ok(Self::ProjectionChunk),
            17 => Ok(Self::ProjectionPendingCommit),
            18 => Ok(Self::ProjectionCommit),
            19 => Ok(Self::RecoveredBurned),
            20 => Ok(Self::VerifiedResultBlobBind),
            21 => Ok(Self::ResultBlobBind),
            22 => Ok(Self::ProjectionBlobBind),
            _ => Err(LedgerError::new("ledger_event_invalid")),
        }
    }

    fn is_terminal(self) -> bool {
        matches!(
            self,
            Self::ResultCommit
                | Self::ResultBlobBind
                | Self::ProjectionCommit
                | Self::Burned
                | Self::RecoveredBurned
        )
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PendingBlobKind {
    Result,
    PreparedReceipt,
    ArmedReceipt,
    PolicySnapshot,
    VerifiedResult,
    Projection,
}

#[derive(Debug, PartialEq, Eq)]
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
    volume_serial: u64,
    #[cfg(windows)]
    file_id: [u8; 16],
    #[cfg(windows)]
    link_count: u32,
    #[cfg(windows)]
    file_attributes: u32,
    #[cfg(windows)]
    byte_length: u64,
    #[cfg(not(any(unix, windows)))]
    created: std::time::SystemTime,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LedgerNamespaceVerification {
    PathAndHandle,
    AuthenticatedHeldHandle,
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct GenerationHardCaps {
    frames: u64,
    tickets: u64,
    referenced_blobs: u64,
    logical_bytes: u64,
    stored_bytes: u64,
}

const PRODUCTION_GENERATION_HARD_CAPS: GenerationHardCaps = GenerationHardCaps {
    frames: MAX_GENERATION_FRAME_COUNT,
    tickets: MAX_GENERATION_TICKET_COUNT,
    referenced_blobs: MAX_GENERATION_REFERENCED_BLOB_COUNT,
    logical_bytes: MAX_GENERATION_LOGICAL_EVIDENCE_BYTES,
    stored_bytes: MAX_GENERATION_STORED_BYTES,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ProtectedBlobReplayPolicy {
    LegacyAllowed,
    ProtectedRequired,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct GenerationUsage {
    frames: u64,
    tickets: u64,
    referenced_blobs: u64,
    logical_bytes: u64,
    blob_stored_bytes: u64,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct GenerationOutstandingReserve {
    frames: u64,
    referenced_blobs: u64,
    logical_bytes: u64,
    blob_stored_bytes: u64,
}

impl GenerationOutstandingReserve {
    fn checked_add(self, other: Self) -> Result<Self, LedgerError> {
        Ok(Self {
            frames: self
                .frames
                .checked_add(other.frames)
                .ok_or_else(|| LedgerError::new("ledger_generation_frame_limit_exceeded"))?,
            referenced_blobs: self
                .referenced_blobs
                .checked_add(other.referenced_blobs)
                .ok_or_else(|| LedgerError::new("ledger_generation_blob_limit_exceeded"))?,
            logical_bytes: self
                .logical_bytes
                .checked_add(other.logical_bytes)
                .ok_or_else(|| LedgerError::new("ledger_generation_logical_limit_exceeded"))?,
            blob_stored_bytes: self
                .blob_stored_bytes
                .checked_add(other.blob_stored_bytes)
                .ok_or_else(|| LedgerError::new("ledger_generation_stored_limit_exceeded"))?,
        })
    }

    fn checked_sub(self, other: Self) -> Result<Self, LedgerError> {
        Ok(Self {
            frames: self
                .frames
                .checked_sub(other.frames)
                .ok_or_else(|| LedgerError::new("ledger_generation_reserve_invalid"))?,
            referenced_blobs: self
                .referenced_blobs
                .checked_sub(other.referenced_blobs)
                .ok_or_else(|| LedgerError::new("ledger_generation_reserve_invalid"))?,
            logical_bytes: self
                .logical_bytes
                .checked_sub(other.logical_bytes)
                .ok_or_else(|| LedgerError::new("ledger_generation_reserve_invalid"))?,
            blob_stored_bytes: self
                .blob_stored_bytes
                .checked_sub(other.blob_stored_bytes)
                .ok_or_else(|| LedgerError::new("ledger_generation_reserve_invalid"))?,
        })
    }

    fn with_blob_budget(
        frames: u64,
        referenced_blobs: u64,
        logical_bytes: u64,
    ) -> Result<Self, LedgerError> {
        let headers = referenced_blobs
            .checked_mul(
                crate::primitive_evidence_authority_blob::PROTECTED_BLOB_HEADER_SIZE as u64,
            )
            .ok_or_else(|| LedgerError::new("ledger_generation_stored_limit_exceeded"))?;
        Ok(Self {
            frames,
            referenced_blobs,
            logical_bytes,
            blob_stored_bytes: logical_bytes
                .checked_add(headers)
                .ok_or_else(|| LedgerError::new("ledger_generation_stored_limit_exceeded"))?,
        })
    }
}

impl GenerationUsage {
    fn stored_bytes(&self) -> Result<u64, LedgerError> {
        self.stored_bytes_after(0, 0)
            .ok_or_else(|| LedgerError::new("ledger_generation_stored_limit_exceeded"))
    }

    fn stored_bytes_after(
        &self,
        additional_frames: u64,
        additional_blob_stored_bytes: u64,
    ) -> Option<u64> {
        self.frames
            .checked_add(additional_frames)?
            .checked_mul(STORED_FRAME_BYTES)?
            .checked_add(self.blob_stored_bytes)?
            .checked_add(additional_blob_stored_bytes)
    }

    fn ensure_add(
        &self,
        additional_frames: u64,
        additional_tickets: u64,
        additional_referenced_blobs: u64,
        additional_logical_bytes: u64,
        additional_blob_stored_bytes: u64,
        caps: GenerationHardCaps,
    ) -> Result<(), LedgerError> {
        let frames = self
            .frames
            .checked_add(additional_frames)
            .ok_or_else(|| LedgerError::new("ledger_generation_frame_limit_exceeded"))?;
        let tickets = self
            .tickets
            .checked_add(additional_tickets)
            .ok_or_else(|| LedgerError::new("ledger_generation_ticket_limit_exceeded"))?;
        let referenced_blobs = self
            .referenced_blobs
            .checked_add(additional_referenced_blobs)
            .ok_or_else(|| LedgerError::new("ledger_generation_blob_limit_exceeded"))?;
        let logical_bytes = self
            .logical_bytes
            .checked_add(additional_logical_bytes)
            .ok_or_else(|| LedgerError::new("ledger_generation_logical_limit_exceeded"))?;
        let stored_bytes = self
            .stored_bytes_after(additional_frames, additional_blob_stored_bytes)
            .ok_or_else(|| LedgerError::new("ledger_generation_stored_limit_exceeded"))?;
        if frames > caps.frames {
            return Err(LedgerError::new("ledger_generation_frame_limit_exceeded"));
        }
        if tickets > caps.tickets {
            return Err(LedgerError::new("ledger_generation_ticket_limit_exceeded"));
        }
        if referenced_blobs > caps.referenced_blobs {
            return Err(LedgerError::new("ledger_generation_blob_limit_exceeded"));
        }
        if logical_bytes > caps.logical_bytes {
            return Err(LedgerError::new("ledger_generation_logical_limit_exceeded"));
        }
        if stored_bytes > caps.stored_bytes {
            return Err(LedgerError::new("ledger_generation_stored_limit_exceeded"));
        }
        Ok(())
    }

    fn commit_add(
        &mut self,
        additional_frames: u64,
        additional_tickets: u64,
        additional_referenced_blobs: u64,
        additional_logical_bytes: u64,
        additional_blob_stored_bytes: u64,
        caps: GenerationHardCaps,
    ) -> Result<(), LedgerError> {
        self.ensure_add(
            additional_frames,
            additional_tickets,
            additional_referenced_blobs,
            additional_logical_bytes,
            additional_blob_stored_bytes,
            caps,
        )?;
        self.frames += additional_frames;
        self.tickets += additional_tickets;
        self.referenced_blobs += additional_referenced_blobs;
        self.logical_bytes += additional_logical_bytes;
        self.blob_stored_bytes += additional_blob_stored_bytes;
        Ok(())
    }
}

fn protected_armed_success_reserve() -> Result<GenerationOutstandingReserve, LedgerError> {
    let logical_bytes = (MAX_VERIFIED_RESULT_RECORD_SIZE as u64)
        .checked_add(MAX_RESULT_SIZE as u64)
        .and_then(|value| value.checked_add(MAX_RESULT_PROJECTION_SIZE as u64))
        .ok_or_else(|| LedgerError::new("ledger_generation_logical_limit_exceeded"))?;
    GenerationOutstandingReserve::with_blob_budget(4, 3, logical_bytes)
}

fn protected_pending_result_reserve(
    finalization_length: usize,
) -> Result<GenerationOutstandingReserve, LedgerError> {
    if finalization_length == 0 || finalization_length > MAX_RESULT_SIZE {
        return Err(LedgerError::new("ledger_generation_reserve_invalid"));
    }
    let logical_bytes = (finalization_length as u64)
        .checked_add(MAX_RESULT_PROJECTION_SIZE as u64)
        .ok_or_else(|| LedgerError::new("ledger_generation_logical_limit_exceeded"))?;
    GenerationOutstandingReserve::with_blob_budget(3, 2, logical_bytes)
}

fn protected_pending_projection_reserve() -> Result<GenerationOutstandingReserve, LedgerError> {
    GenerationOutstandingReserve::with_blob_budget(2, 1, MAX_RESULT_PROJECTION_SIZE as u64)
}

fn protected_outstanding_reserve_for_state(
    state: &StoredTicketState,
) -> Result<GenerationOutstandingReserve, LedgerError> {
    match state {
        StoredTicketState::Issued { .. }
        | StoredTicketState::Consumed {
            armed_receipt: None,
            ..
        } => Ok(GenerationOutstandingReserve {
            frames: 1,
            ..GenerationOutstandingReserve::default()
        }),
        StoredTicketState::Consumed {
            armed_receipt: Some(_),
            ..
        } => protected_armed_success_reserve(),
        StoredTicketState::ResultPendingProjection {
            verified_result,
            result: None,
            projection: None,
            ..
        } => protected_pending_result_reserve(verified_result.finalization_bytes().len()),
        StoredTicketState::ResultPendingProjection {
            result: Some(_),
            projection: None,
            ..
        } => protected_pending_projection_reserve(),
        StoredTicketState::ResultPendingProjection {
            result: Some(_),
            projection: Some(_),
            ..
        } => Ok(GenerationOutstandingReserve {
            frames: 1,
            ..GenerationOutstandingReserve::default()
        }),
        StoredTicketState::ResultPendingProjection {
            result: None,
            projection: Some(_),
            ..
        } => Err(LedgerError::new("ledger_transition_invalid")),
        StoredTicketState::Result { .. } | StoredTicketState::Burned { .. } => {
            Ok(GenerationOutstandingReserve::default())
        }
    }
}

fn protected_outstanding_reserve_for_states(
    states: &BTreeMap<[u8; 32], StoredTicketState>,
) -> Result<GenerationOutstandingReserve, LedgerError> {
    states
        .values()
        .try_fold(GenerationOutstandingReserve::default(), |total, state| {
            total.checked_add(protected_outstanding_reserve_for_state(state)?)
        })
}

fn ensure_generation_operation_with_reserve(
    usage: GenerationUsage,
    additional_frames: u64,
    additional_tickets: u64,
    additional_referenced_blobs: u64,
    additional_logical_bytes: u64,
    additional_blob_stored_bytes: u64,
    reserve_after: GenerationOutstandingReserve,
    caps: GenerationHardCaps,
) -> Result<(), LedgerError> {
    usage.ensure_add(
        additional_frames
            .checked_add(reserve_after.frames)
            .ok_or_else(|| LedgerError::new("ledger_generation_frame_limit_exceeded"))?,
        additional_tickets,
        additional_referenced_blobs
            .checked_add(reserve_after.referenced_blobs)
            .ok_or_else(|| LedgerError::new("ledger_generation_blob_limit_exceeded"))?,
        additional_logical_bytes
            .checked_add(reserve_after.logical_bytes)
            .ok_or_else(|| LedgerError::new("ledger_generation_logical_limit_exceeded"))?,
        additional_blob_stored_bytes
            .checked_add(reserve_after.blob_stored_bytes)
            .ok_or_else(|| LedgerError::new("ledger_generation_stored_limit_exceeded"))?,
        caps,
    )
}

struct LoadedLedger {
    states: BTreeMap<[u8; 32], StoredTicketState>,
    next_sequence: u64,
    previous_hash: [u8; 32],
    pending_blob: Option<PendingBlob>,
    referenced_blob_names: BTreeSet<String>,
    generation_usage: GenerationUsage,
}

pub struct AuthorityLedger {
    file: File,
    path: PathBuf,
    file_identity: StableFileIdentity,
    anchor_file: File,
    anchor_path: PathBuf,
    anchor_file_identity: StableFileIdentity,
    namespace_verification: LedgerNamespaceVerification,
    #[cfg(windows)]
    authenticated_namespace: Option<VerifiedPublishedRuntimeLedgerNamespaceLease>,
    identity: LedgerIdentity,
    states: BTreeMap<[u8; 32], StoredTicketState>,
    next_sequence: u64,
    previous_hash: [u8; 32],
    pending_blob: Option<PendingBlob>,
    blob_authority: Option<ProtectedBlobAuthority>,
    referenced_blob_names: BTreeSet<String>,
    generation_usage: GenerationUsage,
    anchor_previous_hash: [u8; 32],
    terminal_anchor: Option<TerminalAnchor>,
    poisoned: bool,
}

/// Opaque one-use ownership of the exact published FinalCommit ledger pair,
/// its authenticated held namespace, and its protected blob authority. Raw
/// path-opened ledgers cannot construct this production runtime capability.
#[cfg(windows)]
pub(crate) struct AuthenticatedPublishedAuthorityLedger {
    ledger: AuthorityLedger,
}

#[cfg(windows)]
impl fmt::Debug for AuthenticatedPublishedAuthorityLedger {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("AuthenticatedPublishedAuthorityLedger(<held-and-redacted>)")
    }
}

#[cfg(windows)]
impl AuthenticatedPublishedAuthorityLedger {
    pub(crate) fn authenticated_pair_readback(
        &mut self,
    ) -> Result<AuthorityLedgerReadback, LedgerError> {
        self.ledger.authenticated_pair_readback()
    }

    pub(crate) fn verify_current_identity(&self) -> Result<(), LedgerError> {
        self.ledger.verify_current_identity()
    }

    pub(crate) fn authenticated_published_binding_projection(
        &self,
    ) -> Result<VerifiedPublishedRuntimeBindingProjection, LedgerError> {
        self.ledger.authenticated_published_binding_projection()
    }

    pub(crate) fn authenticated_runtime_path(&self) -> Result<PathBuf, LedgerError> {
        self.ledger.authenticated_runtime_path()
    }

    pub(crate) fn consume_for_runtime(
        self,
        expected_path: &Path,
        expected_identity: &LedgerIdentity,
    ) -> Result<AuthorityLedger, LedgerError> {
        self.ledger
            .verify_authenticated_runtime_binding(expected_path, expected_identity)?;
        Ok(self.ledger)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AuthorityLedgerReadback {
    frame_count: u64,
    active_ticket_count: usize,
    ledger_byte_length: u64,
    ledger_sha256: [u8; 32],
    anchor_byte_length: u64,
    anchor_sha256: [u8; 32],
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct DurableResultCommitReadback {
    receipt_ordinal: u64,
    previous_receipt_digest: [u8; 32],
    predecessor_sequence: u64,
    terminal_sequence: u64,
    predecessor_frame_digest: [u8; 32],
    terminal_frame_digest: [u8; 32],
    terminal_ticket_digest: [u8; 32],
    terminal_result_digest: [u8; 32],
    anchor_sequence: u64,
    anchor_frame_digest: [u8; 32],
    anchor_ticket_digest: [u8; 32],
    run_binding_digest: [u8; 32],
    prepared_receipt_digest: [u8; 32],
    armed_receipt_digest: [u8; 32],
    policy_snapshot_digest: [u8; 32],
    recovery_bundle_digest: [u8; 32],
    anchor_record_digest: [u8; 32],
    ledger_file_digest: [u8; 32],
    anchor_file_digest: [u8; 32],
    ledger_file_identity_digest: [u8; 32],
    anchor_file_identity_digest: [u8; 32],
    ledger_length: u64,
    anchor_length: u64,
    frame_count: u64,
    latest_frame_digest: [u8; 32],
}

impl DurableResultCommitReadback {
    pub(crate) const fn receipt_ordinal(&self) -> u64 {
        self.receipt_ordinal
    }

    pub(crate) const fn previous_receipt_digest(&self) -> &[u8; 32] {
        &self.previous_receipt_digest
    }

    pub(crate) const fn predecessor_sequence(&self) -> u64 {
        self.predecessor_sequence
    }

    pub(crate) const fn terminal_sequence(&self) -> u64 {
        self.terminal_sequence
    }

    pub(crate) const fn predecessor_frame_digest(&self) -> &[u8; 32] {
        &self.predecessor_frame_digest
    }

    pub(crate) const fn terminal_frame_digest(&self) -> &[u8; 32] {
        &self.terminal_frame_digest
    }

    pub(crate) const fn terminal_ticket_digest(&self) -> &[u8; 32] {
        &self.terminal_ticket_digest
    }

    pub(crate) const fn terminal_result_digest(&self) -> &[u8; 32] {
        &self.terminal_result_digest
    }

    pub(crate) const fn anchor_sequence(&self) -> u64 {
        self.anchor_sequence
    }

    pub(crate) const fn anchor_frame_digest(&self) -> &[u8; 32] {
        &self.anchor_frame_digest
    }

    pub(crate) const fn anchor_ticket_digest(&self) -> &[u8; 32] {
        &self.anchor_ticket_digest
    }

    pub(crate) const fn run_binding_digest(&self) -> &[u8; 32] {
        &self.run_binding_digest
    }

    pub(crate) const fn prepared_receipt_digest(&self) -> &[u8; 32] {
        &self.prepared_receipt_digest
    }

    pub(crate) const fn armed_receipt_digest(&self) -> &[u8; 32] {
        &self.armed_receipt_digest
    }

    pub(crate) const fn policy_snapshot_digest(&self) -> &[u8; 32] {
        &self.policy_snapshot_digest
    }

    pub(crate) const fn recovery_bundle_digest(&self) -> &[u8; 32] {
        &self.recovery_bundle_digest
    }

    pub(crate) const fn anchor_record_digest(&self) -> &[u8; 32] {
        &self.anchor_record_digest
    }

    pub(crate) const fn ledger_file_digest(&self) -> &[u8; 32] {
        &self.ledger_file_digest
    }

    pub(crate) const fn anchor_file_digest(&self) -> &[u8; 32] {
        &self.anchor_file_digest
    }

    pub(crate) const fn ledger_file_identity_digest(&self) -> &[u8; 32] {
        &self.ledger_file_identity_digest
    }

    pub(crate) const fn anchor_file_identity_digest(&self) -> &[u8; 32] {
        &self.anchor_file_identity_digest
    }

    pub(crate) const fn ledger_length(&self) -> u64 {
        self.ledger_length
    }

    pub(crate) const fn anchor_length(&self) -> u64 {
        self.anchor_length
    }

    pub(crate) const fn frame_count(&self) -> u64 {
        self.frame_count
    }

    pub(crate) const fn latest_frame_digest(&self) -> &[u8; 32] {
        &self.latest_frame_digest
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DurableProjectionCommitReceipt {
    authority_generation_digest: [u8; 32],
    ledger_identity_digest: [u8; 32],
    ticket_digest: [u8; 32],
    run_binding_digest: [u8; 32],
    projection_digest: [u8; 32],
    projection_length: u64,
    terminal_sequence: u64,
    terminal_frame_digest: [u8; 32],
    terminal_ticket_digest: [u8; 32],
    anchor_sequence: u64,
    anchor_frame_digest: [u8; 32],
    anchor_ticket_digest: [u8; 32],
    anchor_record_digest: [u8; 32],
    ledger_file_digest: [u8; 32],
    anchor_file_digest: [u8; 32],
    ledger_file_identity_digest: [u8; 32],
    anchor_file_identity_digest: [u8; 32],
    ledger_length: u64,
    anchor_length: u64,
    frame_count: u64,
    active_ticket_count: u64,
    latest_frame_digest: [u8; 32],
}

impl DurableProjectionCommitReceipt {
    pub fn event(&self) -> &'static str {
        "projectionCommit"
    }

    pub fn authority_generation_digest(&self) -> &[u8; 32] {
        &self.authority_generation_digest
    }

    pub fn ledger_identity_digest(&self) -> &[u8; 32] {
        &self.ledger_identity_digest
    }

    pub fn ticket_digest(&self) -> &[u8; 32] {
        &self.ticket_digest
    }

    pub fn run_binding_digest(&self) -> &[u8; 32] {
        &self.run_binding_digest
    }

    pub fn projection_digest(&self) -> &[u8; 32] {
        &self.projection_digest
    }

    pub fn projection_length(&self) -> u64 {
        self.projection_length
    }

    pub fn terminal_sequence(&self) -> u64 {
        self.terminal_sequence
    }

    pub fn terminal_frame_digest(&self) -> &[u8; 32] {
        &self.terminal_frame_digest
    }

    pub fn terminal_ticket_digest(&self) -> &[u8; 32] {
        &self.terminal_ticket_digest
    }

    pub fn anchor_sequence(&self) -> u64 {
        self.anchor_sequence
    }

    pub fn anchor_frame_digest(&self) -> &[u8; 32] {
        &self.anchor_frame_digest
    }

    pub fn anchor_ticket_digest(&self) -> &[u8; 32] {
        &self.anchor_ticket_digest
    }

    pub fn anchor_record_digest(&self) -> &[u8; 32] {
        &self.anchor_record_digest
    }

    pub fn ledger_file_digest(&self) -> &[u8; 32] {
        &self.ledger_file_digest
    }

    pub fn anchor_file_digest(&self) -> &[u8; 32] {
        &self.anchor_file_digest
    }

    pub fn ledger_file_identity_digest(&self) -> &[u8; 32] {
        &self.ledger_file_identity_digest
    }

    pub fn anchor_file_identity_digest(&self) -> &[u8; 32] {
        &self.anchor_file_identity_digest
    }

    pub fn ledger_length(&self) -> u64 {
        self.ledger_length
    }

    pub fn anchor_length(&self) -> u64 {
        self.anchor_length
    }

    pub fn frame_count(&self) -> u64 {
        self.frame_count
    }

    pub fn active_ticket_count(&self) -> u64 {
        self.active_ticket_count
    }

    pub fn latest_frame_digest(&self) -> &[u8; 32] {
        &self.latest_frame_digest
    }

    pub fn verifies_for(
        &self,
        authority_generation_digest: &[u8; 32],
        ledger_identity_digest: &[u8; 32],
        ticket_digest: &[u8; 32],
        run_binding_digest: &[u8; 32],
        canonical_projection: &[u8],
    ) -> bool {
        let projection_digest: [u8; 32] = Sha256::digest(canonical_projection).into();
        self.authority_generation_digest == *authority_generation_digest
            && self.ledger_identity_digest == *ledger_identity_digest
            && self.ticket_digest == *ticket_digest
            && self.run_binding_digest == *run_binding_digest
            && self.projection_digest == projection_digest
            && self.projection_length == canonical_projection.len() as u64
            && self.terminal_sequence == self.anchor_sequence
            && self.terminal_frame_digest == self.anchor_frame_digest
            && self.terminal_frame_digest == self.latest_frame_digest
            && self.terminal_ticket_digest == self.ticket_digest
            && self.anchor_ticket_digest == self.ticket_digest
            && self.terminal_sequence.checked_add(1) == Some(self.frame_count)
            && self.frame_count.checked_mul(FRAME_SIZE as u64) == Some(self.ledger_length)
            && self
                .frame_count
                .checked_mul(2)
                .and_then(|value| value.checked_mul(ANCHOR_RECORD_SIZE as u64))
                == Some(self.anchor_length)
            && self.active_ticket_count == 0
            && [
                self.authority_generation_digest,
                self.ledger_identity_digest,
                self.ticket_digest,
                self.run_binding_digest,
                self.projection_digest,
                self.terminal_frame_digest,
                self.anchor_record_digest,
                self.ledger_file_digest,
                self.anchor_file_digest,
                self.ledger_file_identity_digest,
                self.anchor_file_identity_digest,
            ]
            .iter()
            .all(|digest| *digest != ZERO_DIGEST)
    }

    #[cfg(test)]
    pub(crate) fn for_runtime_test(
        authority_generation_digest: [u8; 32],
        signer_key_id: [u8; 32],
        ticket_digest: [u8; 32],
        run_binding_digest: [u8; 32],
        projection_bytes: &[u8],
    ) -> Self {
        let projection_digest: [u8; 32] = Sha256::digest(projection_bytes).into();
        let ledger_identity_digest =
            LedgerIdentity::from_digests(authority_generation_digest, signer_key_id)
                .unwrap()
                .canonical_digest();
        Self {
            authority_generation_digest,
            ledger_identity_digest,
            ticket_digest,
            run_binding_digest,
            projection_digest,
            projection_length: projection_bytes.len() as u64,
            terminal_sequence: 7,
            terminal_frame_digest: [0x71; 32],
            terminal_ticket_digest: ticket_digest,
            anchor_sequence: 7,
            anchor_frame_digest: [0x71; 32],
            anchor_ticket_digest: ticket_digest,
            anchor_record_digest: [0x72; 32],
            ledger_file_digest: [0x73; 32],
            anchor_file_digest: [0x74; 32],
            ledger_file_identity_digest: [0x75; 32],
            anchor_file_identity_digest: [0x76; 32],
            ledger_length: 8 * FRAME_SIZE as u64,
            anchor_length: 16 * ANCHOR_RECORD_SIZE as u64,
            frame_count: 8,
            active_ticket_count: 0,
            latest_frame_digest: [0x71; 32],
        }
    }
}

impl AuthorityLedgerReadback {
    pub fn frame_count(&self) -> u64 {
        self.frame_count
    }

    pub fn active_ticket_count(&self) -> usize {
        self.active_ticket_count
    }

    pub fn ledger_byte_length(&self) -> u64 {
        self.ledger_byte_length
    }

    pub fn ledger_sha256(&self) -> &[u8; 32] {
        &self.ledger_sha256
    }

    pub fn anchor_byte_length(&self) -> u64 {
        self.anchor_byte_length
    }

    pub fn anchor_sha256(&self) -> &[u8; 32] {
        &self.anchor_sha256
    }
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
    #[cfg(test)]
    pub(crate) fn provision_new(
        path: &Path,
        identity: LedgerIdentity,
    ) -> Result<Self, LedgerError> {
        Self::provision_new_inner(path, identity, None)
    }

    #[cfg(test)]
    pub(crate) fn provision_new_with_blob_authority(
        path: &Path,
        identity: LedgerIdentity,
        blob_authority: ProtectedBlobAuthority,
    ) -> Result<Self, LedgerError> {
        verify_blob_namespace(&blob_authority, &identity)?;
        Self::provision_new_inner(path, identity, Some(blob_authority))
    }

    #[cfg(test)]
    fn provision_new_inner(
        path: &Path,
        identity: LedgerIdentity,
        blob_authority: Option<ProtectedBlobAuthority>,
    ) -> Result<Self, LedgerError> {
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
            namespace_verification: LedgerNamespaceVerification::PathAndHandle,
            #[cfg(windows)]
            authenticated_namespace: None,
            identity,
            states: BTreeMap::new(),
            next_sequence: 0,
            previous_hash: ZERO_DIGEST,
            pending_blob: None,
            blob_authority,
            referenced_blob_names: BTreeSet::new(),
            generation_usage: GenerationUsage::default(),
            anchor_previous_hash: ZERO_DIGEST,
            terminal_anchor: None,
            poisoned: false,
        };
        ledger.append_frame_raw(Event::Initialize, ZERO_DIGEST, ZERO_DIGEST, &[])?;
        Ok(ledger)
    }

    #[cfg(test)]
    pub(crate) fn open_existing(
        path: &Path,
        identity: LedgerIdentity,
    ) -> Result<Self, LedgerError> {
        Self::open_existing_inner(path, identity, None)
    }

    #[cfg(test)]
    pub(crate) fn open_existing_with_blob_authority(
        path: &Path,
        identity: LedgerIdentity,
        blob_authority: ProtectedBlobAuthority,
    ) -> Result<Self, LedgerError> {
        verify_blob_namespace(&blob_authority, &identity)?;
        Self::open_existing_inner(path, identity, Some(blob_authority))
    }

    #[cfg(test)]
    fn open_existing_inner(
        path: &Path,
        identity: LedgerIdentity,
        blob_authority: Option<ProtectedBlobAuthority>,
    ) -> Result<Self, LedgerError> {
        let file = match open_existing_file(path) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Err(LedgerError::new("ledger_missing"));
            }
            Err(_) => return Err(LedgerError::new("ledger_open_failed")),
        };
        let anchor_path = anchor_path(path);
        let anchor_file = match open_existing_file(&anchor_path) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Err(LedgerError::new("ledger_anchor_missing"));
            }
            Err(_) => return Err(LedgerError::new("ledger_anchor_open_failed")),
        };
        Self::load_opened_pair(
            file,
            path.to_path_buf(),
            anchor_file,
            anchor_path,
            identity,
            LedgerNamespaceVerification::PathAndHandle,
            blob_authority,
        )
    }

    /// Consumes the only production capability that may transfer the
    /// published FinalCommit ledger pair into the service runtime. The exact
    /// share-denying handles remain owned by this ledger; their authenticated
    /// namespace, security and granted-access readbacks are revalidated on
    /// every integrity check.
    #[cfg(windows)]
    pub(crate) fn adopt_verified_published_pair(
        pair: VerifiedPublishedRuntimeLedgerPair,
        identity: LedgerIdentity,
    ) -> Result<AuthenticatedPublishedAuthorityLedger, LedgerError> {
        if pair.generation_sha256() != *identity.authority_generation_digest() {
            return Err(LedgerError::new("ledger_authenticated_generation_mismatch"));
        }
        let storage = pair
            .into_storage_set()
            .map_err(|_| LedgerError::new("ledger_authenticated_namespace_invalid"))?;
        let adoption = storage
            .into_adoption()
            .map_err(|_| LedgerError::new("ledger_authenticated_namespace_invalid"))?;
        adoption.consume_with(
            |namespace, protected_blob_namespace, file, path, anchor_file, anchor_path| {
                validate_authenticated_pair_paths(&path, &anchor_path)?;
                let blob_authority =
                    AuthenticatedProtectedBlobNamespace::from_verified_final_commit(
                        protected_blob_namespace,
                        *identity.authority_generation_digest(),
                        identity.canonical_digest(),
                    )
                    .and_then(AuthenticatedProtectedBlobNamespace::into_authority)
                    .map_err(|_| {
                        LedgerError::new("protected_blob_authenticated_namespace_invalid")
                    })?;
                verify_blob_namespace(&blob_authority, &identity)?;
                let mut ledger = Self::load_opened_pair(
                    file,
                    path,
                    anchor_file,
                    anchor_path,
                    identity,
                    LedgerNamespaceVerification::AuthenticatedHeldHandle,
                    Some(blob_authority),
                )?;
                if namespace.generation_sha256() != *ledger.identity.authority_generation_digest() {
                    return Err(LedgerError::new("ledger_authenticated_generation_mismatch"));
                }
                ledger.authenticated_namespace = Some(namespace);
                ledger.verify_current_identity()?;
                Ok(AuthenticatedPublishedAuthorityLedger { ledger })
            },
        )
    }

    /// Adopts the exact read/write, share-denying ledger pair opened beneath an
    /// already-authenticated namespace. The caller must keep that namespace
    /// capability alive for at least as long as this ledger. No pathname is
    /// reopened between authentication and runtime ownership transfer.
    #[cfg(all(windows, test))]
    pub(crate) fn adopt_authenticated_exclusive_pair(
        file: File,
        path: PathBuf,
        anchor_file: File,
        anchor_path: PathBuf,
        identity: LedgerIdentity,
    ) -> Result<Self, LedgerError> {
        validate_authenticated_pair_paths(&path, &anchor_path)?;
        Self::load_opened_pair(
            file,
            path,
            anchor_file,
            anchor_path,
            identity,
            LedgerNamespaceVerification::AuthenticatedHeldHandle,
            None,
        )
    }

    fn load_opened_pair(
        mut file: File,
        path: PathBuf,
        mut anchor_file: File,
        anchor_path: PathBuf,
        identity: LedgerIdentity,
        namespace_verification: LedgerNamespaceVerification,
        mut blob_authority: Option<ProtectedBlobAuthority>,
    ) -> Result<Self, LedgerError> {
        let file_identity = stable_file_identity(&file)
            .map_err(|_| LedgerError::new("ledger_file_identity_unavailable"))?;
        let anchor_file_identity = stable_file_identity(&anchor_file)
            .map_err(|_| LedgerError::new("ledger_anchor_identity_unavailable"))?;
        verify_file_identity(
            namespace_verification,
            &path,
            &file,
            &file_identity,
            "ledger_file_identity_changed",
        )?;
        verify_file_identity(
            namespace_verification,
            &anchor_path,
            &anchor_file,
            &anchor_file_identity,
            "ledger_anchor_identity_changed",
        )?;
        let loaded_anchor = reconcile_anchor_and_ledger(&mut file, &mut anchor_file, &identity)?;
        let mut loaded = load_frames(&mut file, &identity, blob_authority.as_mut())?;
        if let Some(authority) = blob_authority.as_mut() {
            reconcile_loaded_protected_namespace(
                &mut loaded,
                authority,
                PRODUCTION_GENERATION_HARD_CAPS,
            )?;
        }
        Ok(Self {
            file,
            path,
            file_identity,
            anchor_file,
            anchor_path,
            anchor_file_identity,
            namespace_verification,
            #[cfg(windows)]
            authenticated_namespace: None,
            identity,
            states: loaded.states,
            next_sequence: loaded.next_sequence,
            previous_hash: loaded.previous_hash,
            pending_blob: loaded.pending_blob,
            blob_authority,
            referenced_blob_names: loaded.referenced_blob_names,
            generation_usage: loaded.generation_usage,
            anchor_previous_hash: loaded_anchor.previous_hash,
            terminal_anchor: loaded_anchor.terminal,
            poisoned: false,
        })
    }

    /// Produces the bootstrap snapshot from the same pair that remains owned
    /// by the runtime. This method never closes and reopens either artifact.
    pub(crate) fn authenticated_pair_readback(
        &mut self,
    ) -> Result<AuthorityLedgerReadback, LedgerError> {
        self.ensure_healthy()?;
        self.verify_current_identity()?;
        if self.pending_blob.is_some()
            || self.states.values().any(|state| {
                matches!(
                    state,
                    StoredTicketState::Issued {
                        prepared_receipt: None,
                        ..
                    } | StoredTicketState::Issued {
                        canonical_policy_snapshot: None,
                        ..
                    } | StoredTicketState::Issued {
                        recovery_bundle_digest: None,
                        ..
                    }
                )
            })
        {
            return Err(LedgerError::new("ledger_recovery_required"));
        }
        let ledger_byte_length = self
            .file
            .metadata()
            .map_err(|_| LedgerError::new("ledger_metadata_failed"))?
            .len();
        let expected_ledger_length = self
            .next_sequence
            .checked_mul(FRAME_SIZE as u64)
            .ok_or_else(|| LedgerError::new("ledger_size_invalid"))?;
        if ledger_byte_length != expected_ledger_length {
            return Err(LedgerError::new("ledger_size_invalid"));
        }
        let ledger_anchor_byte_length = self
            .anchor_file
            .metadata()
            .map_err(|_| LedgerError::new("ledger_anchor_metadata_failed"))?
            .len();
        let expected_anchor_length = self
            .next_sequence
            .checked_mul(2)
            .and_then(|value| value.checked_mul(ANCHOR_RECORD_SIZE as u64))
            .ok_or_else(|| LedgerError::new("ledger_anchor_size_invalid"))?;
        if ledger_anchor_byte_length != expected_anchor_length {
            return Err(LedgerError::new("ledger_anchor_size_invalid"));
        }
        let ledger_sha256 = hash_file_prefix(&mut self.file, ledger_byte_length)?;
        let anchor_sha256 = hash_file_prefix(&mut self.anchor_file, ledger_anchor_byte_length)?;
        if self.file.seek(SeekFrom::End(0)).is_err()
            || self.anchor_file.seek(SeekFrom::End(0)).is_err()
        {
            self.poisoned = true;
            return Err(LedgerError::new("ledger_cursor_restore_failed"));
        }
        self.verify_current_identity()?;
        let active_ticket_count = self
            .states
            .values()
            .filter(|state| {
                matches!(
                    state,
                    StoredTicketState::Issued { .. } | StoredTicketState::Consumed { .. }
                )
            })
            .count();
        Ok(AuthorityLedgerReadback {
            frame_count: self.next_sequence,
            active_ticket_count,
            ledger_byte_length,
            ledger_sha256,
            anchor_byte_length: ledger_anchor_byte_length,
            anchor_sha256,
        })
    }

    fn verify_exact_pair_binding(
        &self,
        expected_path: &Path,
        expected_identity: &LedgerIdentity,
    ) -> Result<(), LedgerError> {
        if self.path != expected_path
            || self.anchor_path != self::anchor_path(expected_path)
            || &self.identity != expected_identity
        {
            return Err(LedgerError::new(
                "ledger_authenticated_pair_binding_mismatch",
            ));
        }
        self.verify_current_identity()
    }

    #[cfg(test)]
    pub(crate) fn verify_authenticated_binding(
        &self,
        expected_path: &Path,
        expected_identity: &LedgerIdentity,
    ) -> Result<(), LedgerError> {
        self.verify_exact_pair_binding(expected_path, expected_identity)
    }

    #[cfg(windows)]
    fn verify_authenticated_runtime_binding(
        &self,
        expected_path: &Path,
        expected_identity: &LedgerIdentity,
    ) -> Result<(), LedgerError> {
        self.verify_exact_pair_binding(expected_path, expected_identity)?;
        if self.namespace_verification != LedgerNamespaceVerification::AuthenticatedHeldHandle
            || self.authenticated_namespace.is_none()
            || self.blob_authority.is_none()
        {
            return Err(LedgerError::new(
                "ledger_authenticated_runtime_authority_missing",
            ));
        }
        Ok(())
    }

    #[cfg(windows)]
    pub(crate) fn authenticated_published_binding_projection(
        &self,
    ) -> Result<VerifiedPublishedRuntimeBindingProjection, LedgerError> {
        self.verify_current_identity()?;
        self.authenticated_namespace
            .as_ref()
            .ok_or_else(|| LedgerError::new("ledger_authenticated_namespace_missing"))?
            .binding_projection()
            .map_err(|_| LedgerError::new("ledger_authenticated_namespace_changed"))
    }

    #[cfg(windows)]
    pub(crate) fn authenticated_runtime_path(&self) -> Result<PathBuf, LedgerError> {
        self.verify_current_identity()?;
        if self.namespace_verification != LedgerNamespaceVerification::AuthenticatedHeldHandle
            || self.authenticated_namespace.is_none()
        {
            return Err(LedgerError::new("ledger_authenticated_namespace_missing"));
        }
        Ok(self.path.clone())
    }

    pub fn inspect_existing_clean(
        path: &Path,
        identity: LedgerIdentity,
    ) -> Result<AuthorityLedgerReadback, LedgerError> {
        let mut file = match open_existing_file_read_only(path) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Err(LedgerError::new("ledger_missing"));
            }
            Err(_) => return Err(LedgerError::new("ledger_open_failed")),
        };
        let anchor_path = anchor_path(path);
        let mut anchor_file = match open_existing_file_read_only(&anchor_path) {
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

        let anchor = load_anchor(&mut anchor_file, &identity)?;
        if anchor.trailing_intent.is_some() || !anchor.trailing_partial.is_empty() {
            return Err(LedgerError::new("ledger_recovery_required"));
        }
        let ledger_length = file
            .metadata()
            .map_err(|_| LedgerError::new("ledger_metadata_failed"))?
            .len();
        let committed_length = anchor
            .committed_frames
            .len()
            .checked_mul(FRAME_SIZE)
            .ok_or_else(|| LedgerError::new("ledger_size_invalid"))?
            as u64;
        if ledger_length != committed_length || ledger_length % FRAME_SIZE as u64 != 0 {
            return Err(LedgerError::new(if ledger_length < committed_length {
                "ledger_rollback_detected"
            } else {
                "ledger_anchor_rollback_detected"
            }));
        }
        file.seek(SeekFrom::Start(0))
            .map_err(|_| LedgerError::new("ledger_read_failed"))?;
        for expected in &anchor.committed_frames {
            let mut actual = [0u8; FRAME_SIZE];
            file.read_exact(&mut actual)
                .map_err(|_| LedgerError::new("ledger_read_failed"))?;
            decode_frame(&actual)?;
            if actual != *expected {
                return Err(LedgerError::new("ledger_anchor_mismatch"));
            }
        }
        let loaded = load_frames(&mut file, &identity, None)?;
        if loaded.pending_blob.is_some()
            || loaded.states.values().any(|state| {
                matches!(
                    state,
                    StoredTicketState::Issued {
                        prepared_receipt: None,
                        ..
                    } | StoredTicketState::Issued {
                        canonical_policy_snapshot: None,
                        ..
                    } | StoredTicketState::Issued {
                        recovery_bundle_digest: None,
                        ..
                    }
                )
            })
        {
            return Err(LedgerError::new("ledger_recovery_required"));
        }
        let active_ticket_count = loaded
            .states
            .values()
            .filter(|state| {
                matches!(
                    state,
                    StoredTicketState::Issued { .. } | StoredTicketState::Consumed { .. }
                )
            })
            .count();
        let anchor_length = anchor_file
            .metadata()
            .map_err(|_| LedgerError::new("ledger_anchor_metadata_failed"))?
            .len();
        let ledger_sha256 = hash_file_prefix(&mut file, ledger_length)?;
        let anchor_sha256 = hash_file_prefix(&mut anchor_file, anchor_length)?;
        verify_stable_file(path, &file, &file_identity, "ledger_file_identity_changed")?;
        verify_stable_file(
            &anchor_path,
            &anchor_file,
            &anchor_file_identity,
            "ledger_anchor_identity_changed",
        )?;
        Ok(AuthorityLedgerReadback {
            frame_count: loaded.next_sequence,
            active_ticket_count,
            ledger_byte_length: ledger_length,
            ledger_sha256,
            anchor_byte_length: anchor_length,
            anchor_sha256,
        })
    }

    pub fn verify_current_identity(&self) -> Result<(), LedgerError> {
        self.ensure_healthy()?;
        self.verify_storage_identity()
    }

    pub(crate) fn has_protected_blob_authority(&self) -> bool {
        self.blob_authority.is_some()
    }

    #[cfg(test)]
    pub(crate) fn attach_protected_blob_authority(
        &mut self,
        mut authority: ProtectedBlobAuthority,
    ) -> Result<(), LedgerError> {
        self.ensure_healthy()?;
        if self.blob_authority.is_some() {
            return Err(LedgerError::new("protected_blob_authority_duplicate"));
        }
        verify_blob_namespace(&authority, &self.identity)?;
        self.verify_current_identity()?;
        let mut loaded = load_frames(&mut self.file, &self.identity, Some(&mut authority))?;
        reconcile_loaded_protected_namespace(
            &mut loaded,
            &mut authority,
            PRODUCTION_GENERATION_HARD_CAPS,
        )?;
        if loaded.states != self.states
            || loaded.next_sequence != self.next_sequence
            || loaded.previous_hash != self.previous_hash
            || loaded.pending_blob != self.pending_blob
            || loaded.generation_usage != self.generation_usage
        {
            return Err(LedgerError::new("protected_blob_ledger_replay_mismatch"));
        }
        self.referenced_blob_names = loaded.referenced_blob_names;
        self.blob_authority = Some(authority);
        if self.file.seek(SeekFrom::End(0)).is_err() {
            self.poisoned = true;
            return Err(LedgerError::new("ledger_seek_failed"));
        }
        self.verify_current_identity()
    }

    #[cfg(test)]
    pub(crate) fn protected_blob_metrics(
        &self,
    ) -> Option<crate::primitive_evidence_authority_blob::ProtectedBlobIoMetrics> {
        self.blob_authority
            .as_ref()
            .map(ProtectedBlobAuthority::metrics)
    }

    fn verify_storage_identity(&self) -> Result<(), LedgerError> {
        #[cfg(windows)]
        if let Some(namespace) = &self.authenticated_namespace {
            namespace
                .revalidate_pair(&self.file, &self.anchor_file)
                .map_err(|_| LedgerError::new("ledger_authenticated_namespace_changed"))?;
            if namespace.generation_sha256() != *self.identity.authority_generation_digest() {
                return Err(LedgerError::new("ledger_authenticated_generation_mismatch"));
            }
        }
        if let Some(authority) = &self.blob_authority {
            verify_blob_namespace(authority, &self.identity)?;
            authority.verify_namespace().map_err(blob_ledger_error)?;
        }
        verify_file_identity(
            self.namespace_verification,
            &self.path,
            &self.file,
            &self.file_identity,
            "ledger_file_identity_changed",
        )?;
        verify_file_identity(
            self.namespace_verification,
            &self.anchor_path,
            &self.anchor_file,
            &self.anchor_file_identity,
            "ledger_anchor_identity_changed",
        )
    }

    /// Reopens the exact immutable result-commit prefix through the held
    /// ledger, anchor, and protected-blob capabilities. Later projection
    /// frames are deliberately excluded from the returned prefix hashes.
    pub(crate) fn result_commit_readback_from_held_pair(
        &mut self,
        ticket_digest: &str,
        expected_run_binding_digest: &str,
        result_bytes: &[u8],
    ) -> Result<DurableResultCommitReadback, LedgerError> {
        self.ensure_healthy()?;
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        let expected_run_binding =
            decode_nonzero_digest(expected_run_binding_digest, "run_binding_digest_invalid")?;
        if result_bytes.is_empty() || result_bytes.len() > MAX_RESULT_SIZE {
            return Err(LedgerError::new("result_bytes_invalid"));
        }
        let result_digest: [u8; 32] = Sha256::digest(result_bytes).into();
        let result = Self::result_commit_readback_from_held_files(
            &self.path,
            &mut self.file,
            &self.file_identity,
            &self.anchor_path,
            &mut self.anchor_file,
            &self.anchor_file_identity,
            self.namespace_verification,
            &self.identity,
            ticket,
            expected_run_binding,
            result_bytes,
            result_digest,
            self.blob_authority.as_mut(),
        );
        let ledger_restored = self.file.seek(SeekFrom::End(0)).is_ok();
        let anchor_restored = self.anchor_file.seek(SeekFrom::End(0)).is_ok();
        if !ledger_restored || !anchor_restored {
            self.poisoned = true;
            return Err(LedgerError::new(if !ledger_restored {
                "ledger_seek_failed"
            } else {
                "ledger_anchor_seek_failed"
            }));
        }
        result
    }

    #[allow(clippy::too_many_arguments)]
    fn result_commit_readback_from_held_files(
        path: &Path,
        file: &mut File,
        file_identity: &StableFileIdentity,
        anchor_path: &Path,
        anchor_file: &mut File,
        anchor_file_identity: &StableFileIdentity,
        namespace_verification: LedgerNamespaceVerification,
        identity: &LedgerIdentity,
        ticket: [u8; 32],
        expected_run_binding: [u8; 32],
        result_bytes: &[u8],
        result_digest: [u8; 32],
        mut blob_authority: Option<&mut ProtectedBlobAuthority>,
    ) -> Result<DurableResultCommitReadback, LedgerError> {
        verify_file_identity(
            namespace_verification,
            path,
            file,
            file_identity,
            "ledger_file_identity_changed",
        )?;
        verify_file_identity(
            namespace_verification,
            anchor_path,
            anchor_file,
            anchor_file_identity,
            "ledger_anchor_identity_changed",
        )?;
        let anchor = load_anchor(anchor_file, identity)?;
        if anchor.trailing_intent.is_some() || !anchor.trailing_partial.is_empty() {
            return Err(LedgerError::new("ledger_recovery_required"));
        }
        let ledger_length = file
            .metadata()
            .map_err(|_| LedgerError::new("ledger_metadata_failed"))?
            .len();
        let committed_length = (anchor.committed_frames.len() as u64)
            .checked_mul(FRAME_SIZE as u64)
            .ok_or_else(|| LedgerError::new("ledger_size_invalid"))?;
        if ledger_length != committed_length || ledger_length % FRAME_SIZE as u64 != 0 {
            return Err(LedgerError::new("ledger_anchor_mismatch"));
        }
        file.seek(SeekFrom::Start(0))
            .map_err(|_| LedgerError::new("ledger_read_failed"))?;
        for expected in &anchor.committed_frames {
            let mut actual = [0u8; FRAME_SIZE];
            file.read_exact(&mut actual)
                .map_err(|_| LedgerError::new("ledger_read_failed"))?;
            if actual != *expected {
                return Err(LedgerError::new("ledger_anchor_mismatch"));
            }
        }

        let mut matching_terminal = None;
        let mut receipt_ordinal = 0u64;
        for frame_bytes in &anchor.committed_frames {
            let frame = decode_frame(frame_bytes)?;
            if matches!(frame.event, Event::ResultCommit | Event::ResultBlobBind) {
                receipt_ordinal = receipt_ordinal
                    .checked_add(1)
                    .ok_or_else(|| LedgerError::new("ledger_sequence_exhausted"))?;
                if frame.ticket_digest == ticket {
                    let event_shape_valid = match frame.event {
                        Event::ResultCommit => {
                            frame.payload.len() == 8
                                && u64::from_be_bytes(frame.payload[..8].try_into().unwrap())
                                    == result_bytes.len() as u64
                        }
                        Event::ResultBlobBind => frame.payload.len() == 32,
                        _ => false,
                    };
                    if frame.result_digest != result_digest || !event_shape_valid {
                        return Err(LedgerError::new("result_commit_binding_mismatch"));
                    }
                    if matching_terminal
                        .replace((frame.sequence, receipt_ordinal))
                        .is_some()
                    {
                        return Err(LedgerError::new("result_commit_duplicate"));
                    }
                }
            }
        }
        let (terminal_sequence, receipt_ordinal) =
            matching_terminal.ok_or_else(|| LedgerError::new("result_commit_missing"))?;
        if terminal_sequence == 0 {
            return Err(LedgerError::new("result_commit_terminal_invalid"));
        }
        let frame_count = terminal_sequence
            .checked_add(1)
            .ok_or_else(|| LedgerError::new("ledger_sequence_exhausted"))?;
        let prefix = load_committed_frame_prefix(
            &anchor.committed_frames,
            frame_count as usize,
            identity,
            blob_authority.as_deref_mut(),
        )?;
        if prefix.pending_blob.is_some() {
            return Err(LedgerError::new("ledger_recovery_required"));
        }
        let (
            stored_run_binding,
            prepared_receipt,
            policy_snapshot,
            recovery_bundle_digest,
            armed_receipt,
            stored_result,
        ) = match prefix.states.get(&ticket) {
            Some(StoredTicketState::ResultPendingProjection {
                run_binding_digest,
                prepared_receipt,
                canonical_policy_snapshot,
                recovery_bundle_digest,
                armed_receipt,
                result: Some(result),
                projection: None,
                ..
            }) => (
                *run_binding_digest,
                prepared_receipt,
                canonical_policy_snapshot,
                *recovery_bundle_digest,
                armed_receipt,
                result,
            ),
            _ => return Err(LedgerError::new("result_commit_state_invalid")),
        };
        if stored_run_binding != expected_run_binding
            || stored_result.0.as_slice() != result_bytes
            || stored_result.1 != result_digest
        {
            return Err(LedgerError::new("result_commit_binding_mismatch"));
        }
        let active_other_ticket_count = prefix
            .states
            .iter()
            .filter(|(candidate, state)| {
                **candidate != ticket
                    && matches!(
                        state,
                        StoredTicketState::Issued { .. }
                            | StoredTicketState::Consumed { .. }
                            | StoredTicketState::ResultPendingProjection { .. }
                    )
            })
            .count();
        if active_other_ticket_count != 0 {
            return Err(LedgerError::new("result_commit_active_ticket_invalid"));
        }

        let terminal_frame = decode_frame(
            anchor
                .committed_frames
                .get(terminal_sequence as usize)
                .ok_or_else(|| LedgerError::new("result_commit_missing"))?,
        )?;
        if !matches!(
            terminal_frame.event,
            Event::ResultCommit | Event::ResultBlobBind
        ) || terminal_frame.sequence != terminal_sequence
            || terminal_frame.ticket_digest != ticket
            || terminal_frame.result_digest != result_digest
        {
            return Err(LedgerError::new("result_commit_terminal_invalid"));
        }
        let predecessor_sequence = terminal_sequence - 1;
        let predecessor_frame = decode_frame(
            anchor
                .committed_frames
                .get(predecessor_sequence as usize)
                .ok_or_else(|| LedgerError::new("result_commit_terminal_invalid"))?,
        )?;
        let anchor_record_index = terminal_sequence
            .checked_mul(2)
            .and_then(|value| value.checked_add(1))
            .ok_or_else(|| LedgerError::new("ledger_sequence_exhausted"))?;
        let anchor_record_offset = anchor_record_index
            .checked_mul(ANCHOR_RECORD_SIZE as u64)
            .ok_or_else(|| LedgerError::new("ledger_size_invalid"))?;
        anchor_file
            .seek(SeekFrom::Start(anchor_record_offset))
            .map_err(|_| LedgerError::new("ledger_anchor_read_failed"))?;
        let mut anchor_record_bytes = [0u8; ANCHOR_RECORD_SIZE];
        anchor_file
            .read_exact(&mut anchor_record_bytes)
            .map_err(|_| LedgerError::new("ledger_anchor_read_failed"))?;
        let anchor_record = decode_anchor_record(&anchor_record_bytes, identity)?;
        let expected_terminal = TerminalAnchor {
            sequence: terminal_sequence,
            frame_hash: terminal_frame.frame_hash,
            ticket_digest: ticket,
        };
        if anchor_record.kind != AnchorRecordKind::Commit
            || anchor_record.frame != anchor.committed_frames[terminal_sequence as usize]
            || anchor_record.terminal != Some(expected_terminal)
        {
            return Err(LedgerError::new("result_commit_anchor_invalid"));
        }

        let receipt_ledger_length = frame_count
            .checked_mul(FRAME_SIZE as u64)
            .ok_or_else(|| LedgerError::new("ledger_size_invalid"))?;
        let receipt_anchor_length = frame_count
            .checked_mul(2)
            .and_then(|value| value.checked_mul(ANCHOR_RECORD_SIZE as u64))
            .ok_or_else(|| LedgerError::new("ledger_size_invalid"))?;
        let ledger_file_digest = hash_file_prefix(file, receipt_ledger_length)?;
        let anchor_file_digest = hash_file_prefix(anchor_file, receipt_anchor_length)?;
        verify_file_identity(
            namespace_verification,
            path,
            file,
            file_identity,
            "ledger_file_identity_changed",
        )?;
        verify_file_identity(
            namespace_verification,
            anchor_path,
            anchor_file,
            anchor_file_identity,
            "ledger_anchor_identity_changed",
        )?;
        let current_file_identity = stable_file_identity(file)
            .map_err(|_| LedgerError::new("ledger_file_identity_changed"))?;
        let current_anchor_file_identity = stable_file_identity(anchor_file)
            .map_err(|_| LedgerError::new("ledger_anchor_identity_changed"))?;
        let mut previous_receipt = Sha256::new();
        previous_receipt.update(RESULT_RECEIPT_PREDECESSOR_DOMAIN);
        previous_receipt.update(identity.canonical_digest());
        previous_receipt.update(receipt_ordinal.to_be_bytes());
        previous_receipt.update(predecessor_sequence.to_be_bytes());
        previous_receipt.update(predecessor_frame.frame_hash);
        previous_receipt.update(ticket);

        Ok(DurableResultCommitReadback {
            receipt_ordinal,
            previous_receipt_digest: previous_receipt.finalize().into(),
            predecessor_sequence,
            terminal_sequence,
            predecessor_frame_digest: predecessor_frame.frame_hash,
            terminal_frame_digest: terminal_frame.frame_hash,
            terminal_ticket_digest: terminal_frame.ticket_digest,
            terminal_result_digest: terminal_frame.result_digest,
            anchor_sequence: expected_terminal.sequence,
            anchor_frame_digest: expected_terminal.frame_hash,
            anchor_ticket_digest: expected_terminal.ticket_digest,
            run_binding_digest: stored_run_binding,
            prepared_receipt_digest: Sha256::digest(prepared_receipt).into(),
            armed_receipt_digest: Sha256::digest(armed_receipt).into(),
            policy_snapshot_digest: Sha256::digest(policy_snapshot).into(),
            recovery_bundle_digest,
            anchor_record_digest: anchor_record.record_hash,
            ledger_file_digest,
            anchor_file_digest,
            ledger_file_identity_digest: stable_file_identity_digest(
                b"ledger",
                &current_file_identity,
            ),
            anchor_file_identity_digest: stable_file_identity_digest(
                b"anchor",
                &current_anchor_file_identity,
            ),
            ledger_length: receipt_ledger_length,
            anchor_length: receipt_anchor_length,
            frame_count,
            latest_frame_digest: prefix.previous_hash,
        })
    }

    /// Reconstructs the durable projection receipt from the exact ledger and
    /// anchor handles already owned by this ledger. The pair remains held with
    /// its original sharing contract for the entire readback and is restored
    /// to append position before this method returns.
    pub fn projection_commit_receipt_from_held_pair(
        &mut self,
        ticket_digest: &str,
        expected_run_binding_digest: &str,
        canonical_projection: &[u8],
    ) -> Result<DurableProjectionCommitReceipt, LedgerError> {
        self.ensure_healthy()?;
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        let expected_run_binding =
            decode_nonzero_digest(expected_run_binding_digest, "run_binding_digest_invalid")?;
        if canonical_projection.is_empty()
            || canonical_projection.len() > MAX_RESULT_PROJECTION_SIZE
        {
            return Err(LedgerError::new("projection_bytes_invalid"));
        }
        let projection_digest: [u8; 32] = Sha256::digest(canonical_projection).into();
        let result = Self::projection_commit_receipt_from_held_files(
            &self.path,
            &mut self.file,
            &self.file_identity,
            &self.anchor_path,
            &mut self.anchor_file,
            &self.anchor_file_identity,
            self.namespace_verification,
            &self.identity,
            ticket,
            expected_run_binding,
            canonical_projection,
            projection_digest,
            self.blob_authority.as_mut(),
        );
        let ledger_restored = self.file.seek(SeekFrom::End(0)).is_ok();
        let anchor_restored = self.anchor_file.seek(SeekFrom::End(0)).is_ok();
        if !ledger_restored || !anchor_restored {
            self.poisoned = true;
            return Err(LedgerError::new(if !ledger_restored {
                "ledger_seek_failed"
            } else {
                "ledger_anchor_seek_failed"
            }));
        }
        result
    }

    #[cfg(test)]
    pub fn reopen_projection_commit_receipt(
        path: &Path,
        identity: LedgerIdentity,
        ticket_digest: &str,
        expected_run_binding_digest: &str,
        canonical_projection: &[u8],
    ) -> Result<DurableProjectionCommitReceipt, LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        let expected_run_binding =
            decode_nonzero_digest(expected_run_binding_digest, "run_binding_digest_invalid")?;
        if canonical_projection.is_empty()
            || canonical_projection.len() > MAX_RESULT_PROJECTION_SIZE
        {
            return Err(LedgerError::new("projection_bytes_invalid"));
        }
        let projection_digest: [u8; 32] = Sha256::digest(canonical_projection).into();
        let mut file = open_existing_file_read_only(path).map_err(|error| {
            LedgerError::new(if error.kind() == std::io::ErrorKind::NotFound {
                "ledger_missing"
            } else {
                "ledger_open_failed"
            })
        })?;
        let anchor_path = anchor_path(path);
        let mut anchor_file = open_existing_file_read_only(&anchor_path).map_err(|error| {
            LedgerError::new(if error.kind() == std::io::ErrorKind::NotFound {
                "ledger_anchor_missing"
            } else {
                "ledger_anchor_open_failed"
            })
        })?;
        let file_identity = stable_file_identity(&file)
            .map_err(|_| LedgerError::new("ledger_file_identity_unavailable"))?;
        let anchor_file_identity = stable_file_identity(&anchor_file)
            .map_err(|_| LedgerError::new("ledger_anchor_identity_unavailable"))?;
        Self::projection_commit_receipt_from_held_files(
            path,
            &mut file,
            &file_identity,
            &anchor_path,
            &mut anchor_file,
            &anchor_file_identity,
            LedgerNamespaceVerification::PathAndHandle,
            &identity,
            ticket,
            expected_run_binding,
            canonical_projection,
            projection_digest,
            None,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn projection_commit_receipt_from_held_files(
        path: &Path,
        file: &mut File,
        file_identity: &StableFileIdentity,
        anchor_path: &Path,
        anchor_file: &mut File,
        anchor_file_identity: &StableFileIdentity,
        namespace_verification: LedgerNamespaceVerification,
        identity: &LedgerIdentity,
        ticket: [u8; 32],
        expected_run_binding: [u8; 32],
        canonical_projection: &[u8],
        projection_digest: [u8; 32],
        mut blob_authority: Option<&mut ProtectedBlobAuthority>,
    ) -> Result<DurableProjectionCommitReceipt, LedgerError> {
        verify_file_identity(
            namespace_verification,
            path,
            file,
            file_identity,
            "ledger_file_identity_changed",
        )?;
        verify_file_identity(
            namespace_verification,
            anchor_path,
            anchor_file,
            anchor_file_identity,
            "ledger_anchor_identity_changed",
        )?;
        let anchor = load_anchor(anchor_file, identity)?;
        if anchor.trailing_intent.is_some() || !anchor.trailing_partial.is_empty() {
            return Err(LedgerError::new("ledger_recovery_required"));
        }
        let ledger_length = file
            .metadata()
            .map_err(|_| LedgerError::new("ledger_metadata_failed"))?
            .len();
        let committed_length = (anchor.committed_frames.len() as u64)
            .checked_mul(FRAME_SIZE as u64)
            .ok_or_else(|| LedgerError::new("ledger_size_invalid"))?;
        if ledger_length != committed_length || ledger_length % FRAME_SIZE as u64 != 0 {
            return Err(LedgerError::new("ledger_anchor_mismatch"));
        }
        file.seek(SeekFrom::Start(0))
            .map_err(|_| LedgerError::new("ledger_read_failed"))?;
        for expected in &anchor.committed_frames {
            let mut actual = [0u8; FRAME_SIZE];
            file.read_exact(&mut actual)
                .map_err(|_| LedgerError::new("ledger_read_failed"))?;
            if actual != *expected {
                return Err(LedgerError::new("ledger_anchor_mismatch"));
            }
        }
        let loaded = load_frames(file, identity, blob_authority.as_deref_mut())?;
        if loaded.next_sequence != anchor.committed_frames.len() as u64 {
            return Err(LedgerError::new("ledger_anchor_mismatch"));
        }

        let mut matching_sequence = None;
        for frame_bytes in &anchor.committed_frames {
            let frame = decode_frame(frame_bytes)?;
            if frame.event == Event::ProjectionCommit && frame.ticket_digest == ticket {
                if frame.result_digest != projection_digest || !frame.payload.is_empty() {
                    return Err(LedgerError::new("projection_binding_mismatch"));
                }
                if matching_sequence.replace(frame.sequence).is_some() {
                    return Err(LedgerError::new("projection_commit_duplicate"));
                }
            }
        }
        let terminal_sequence =
            matching_sequence.ok_or_else(|| LedgerError::new("projection_commit_missing"))?;
        let frame_count = terminal_sequence
            .checked_add(1)
            .ok_or_else(|| LedgerError::new("ledger_sequence_exhausted"))?;
        let prefix = load_committed_frame_prefix(
            &anchor.committed_frames,
            frame_count as usize,
            identity,
            blob_authority.as_deref_mut(),
        )?;
        if prefix.pending_blob.is_some() {
            return Err(LedgerError::new("ledger_recovery_required"));
        }
        let (stored_run_binding, stored_projection) = match prefix.states.get(&ticket) {
            Some(StoredTicketState::Result {
                run_binding_digest,
                projection: Some(projection),
                ..
            }) => (*run_binding_digest, projection),
            _ => return Err(LedgerError::new("projection_commit_state_invalid")),
        };
        if stored_run_binding != expected_run_binding
            || stored_projection.0.as_slice() != canonical_projection
            || stored_projection.1 != projection_digest
        {
            return Err(LedgerError::new("projection_binding_mismatch"));
        }
        let active_ticket_count = prefix
            .states
            .values()
            .filter(|state| {
                matches!(
                    state,
                    StoredTicketState::Issued { .. }
                        | StoredTicketState::Consumed { .. }
                        | StoredTicketState::ResultPendingProjection { .. }
                )
            })
            .count() as u64;
        if active_ticket_count != 0 {
            return Err(LedgerError::new("projection_commit_active_ticket_invalid"));
        }
        let terminal_frame = decode_frame(
            anchor
                .committed_frames
                .get(terminal_sequence as usize)
                .ok_or_else(|| LedgerError::new("projection_commit_missing"))?,
        )?;
        if terminal_frame.event != Event::ProjectionCommit
            || terminal_frame.sequence != terminal_sequence
            || terminal_frame.ticket_digest != ticket
            || terminal_frame.result_digest != projection_digest
        {
            return Err(LedgerError::new("projection_commit_terminal_invalid"));
        }
        let anchor_record_index = terminal_sequence
            .checked_mul(2)
            .and_then(|value| value.checked_add(1))
            .ok_or_else(|| LedgerError::new("ledger_sequence_exhausted"))?;
        let anchor_record_offset = anchor_record_index
            .checked_mul(ANCHOR_RECORD_SIZE as u64)
            .ok_or_else(|| LedgerError::new("ledger_size_invalid"))?;
        anchor_file
            .seek(SeekFrom::Start(anchor_record_offset))
            .map_err(|_| LedgerError::new("ledger_anchor_read_failed"))?;
        let mut anchor_record_bytes = [0u8; ANCHOR_RECORD_SIZE];
        anchor_file
            .read_exact(&mut anchor_record_bytes)
            .map_err(|_| LedgerError::new("ledger_anchor_read_failed"))?;
        let anchor_record = decode_anchor_record(&anchor_record_bytes, identity)?;
        let expected_terminal = TerminalAnchor {
            sequence: terminal_sequence,
            frame_hash: terminal_frame.frame_hash,
            ticket_digest: ticket,
        };
        if anchor_record.kind != AnchorRecordKind::Commit
            || anchor_record.frame != anchor.committed_frames[terminal_sequence as usize]
            || anchor_record.terminal != Some(expected_terminal)
        {
            return Err(LedgerError::new("projection_commit_anchor_invalid"));
        }

        let receipt_ledger_length = frame_count
            .checked_mul(FRAME_SIZE as u64)
            .ok_or_else(|| LedgerError::new("ledger_size_invalid"))?;
        let receipt_anchor_length = frame_count
            .checked_mul(2)
            .and_then(|value| value.checked_mul(ANCHOR_RECORD_SIZE as u64))
            .ok_or_else(|| LedgerError::new("ledger_size_invalid"))?;
        let ledger_file_digest = hash_file_prefix(file, receipt_ledger_length)?;
        let anchor_file_digest = hash_file_prefix(anchor_file, receipt_anchor_length)?;
        verify_file_identity(
            namespace_verification,
            path,
            file,
            file_identity,
            "ledger_file_identity_changed",
        )?;
        verify_file_identity(
            namespace_verification,
            anchor_path,
            anchor_file,
            anchor_file_identity,
            "ledger_anchor_identity_changed",
        )?;
        let current_file_identity = stable_file_identity(file)
            .map_err(|_| LedgerError::new("ledger_file_identity_changed"))?;
        let current_anchor_file_identity = stable_file_identity(anchor_file)
            .map_err(|_| LedgerError::new("ledger_anchor_identity_changed"))?;

        Ok(DurableProjectionCommitReceipt {
            authority_generation_digest: *identity.authority_generation_digest(),
            ledger_identity_digest: identity.canonical_digest(),
            ticket_digest: ticket,
            run_binding_digest: stored_run_binding,
            projection_digest,
            projection_length: canonical_projection.len() as u64,
            terminal_sequence,
            terminal_frame_digest: terminal_frame.frame_hash,
            terminal_ticket_digest: terminal_frame.ticket_digest,
            anchor_sequence: expected_terminal.sequence,
            anchor_frame_digest: expected_terminal.frame_hash,
            anchor_ticket_digest: expected_terminal.ticket_digest,
            anchor_record_digest: anchor_record.record_hash,
            ledger_file_digest,
            anchor_file_digest,
            ledger_file_identity_digest: stable_file_identity_digest(
                b"ledger",
                &current_file_identity,
            ),
            anchor_file_identity_digest: stable_file_identity_digest(
                b"anchor",
                &current_anchor_file_identity,
            ),
            ledger_length: receipt_ledger_length,
            anchor_length: receipt_anchor_length,
            frame_count,
            active_ticket_count,
            latest_frame_digest: prefix.previous_hash,
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
        let prepared_frames = blob_frame_count(prepared_receipt.len())?;
        let policy_frames = blob_frame_count(canonical_policy_snapshot.len())?;
        let operation_frames = 2u64
            .checked_add(prepared_frames)
            .and_then(|value| value.checked_add(policy_frames))
            .ok_or_else(|| LedgerError::new("ledger_generation_frame_limit_exceeded"))?;
        let operation_logical_bytes = (prepared_receipt.len() as u64)
            .checked_add(canonical_policy_snapshot.len() as u64)
            .ok_or_else(|| LedgerError::new("ledger_generation_logical_limit_exceeded"))?;
        self.generation_usage.ensure_add(
            operation_frames,
            1,
            0,
            operation_logical_bytes,
            0,
            PRODUCTION_GENERATION_HARD_CAPS,
        )?;
        self.ensure_protected_transition_capacity(
            &ticket,
            GenerationOutstandingReserve {
                frames: 1,
                ..GenerationOutstandingReserve::default()
            },
            operation_frames,
            1,
            0,
            operation_logical_bytes,
            0,
        )?;
        self.append_frame_raw(Event::Issued, ticket, run_binding, &[])?;
        self.generation_usage
            .commit_add(0, 1, 0, 0, 0, PRODUCTION_GENERATION_HARD_CAPS)?;
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
        self.ensure_protected_transition_capacity(
            &ticket,
            GenerationOutstandingReserve {
                frames: 1,
                ..GenerationOutstandingReserve::default()
            },
            1,
            0,
            0,
            0,
            0,
        )?;
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
        let armed_frame_count = blob_frame_count(armed_receipt.len())?;
        self.ensure_protected_transition_capacity(
            &ticket,
            protected_armed_success_reserve()?,
            armed_frame_count,
            0,
            0,
            armed_receipt.len() as u64,
            0,
        )?;
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
        let (run_binding_digest, verified_pending) = match self.states.get(&ticket) {
            None => return Err(LedgerError::new("ticket_unknown")),
            Some(StoredTicketState::Consumed {
                run_binding_digest, ..
            }) if self.blob_authority.is_none() => (*run_binding_digest, false),
            Some(StoredTicketState::Consumed { .. }) => {
                return Err(LedgerError::new("protected_result_verification_required"));
            }
            Some(StoredTicketState::ResultPendingProjection {
                run_binding_digest,
                verified_result,
                result: None,
                ..
            }) if verified_result.finalization_bytes() == result_bytes => {
                (*run_binding_digest, true)
            }
            Some(StoredTicketState::ResultPendingProjection {
                run_binding_digest,
                verified_result,
                result: Some((stored, digest)),
                ..
            }) if verified_result.finalization_bytes() == result_bytes
                && stored.as_slice() == result_bytes
                && *digest == Sha256::digest(result_bytes)[..] =>
            {
                return Ok(())
            }
            Some(_) => return Err(LedgerError::new("ticket_transition_invalid")),
        };

        let result_digest: [u8; 32] = Sha256::digest(result_bytes).into();
        if self.blob_authority.is_some() {
            let replacement = if verified_pending {
                protected_pending_projection_reserve()?
            } else {
                GenerationOutstandingReserve::default()
            };
            let context = protected_blob_context_for_state(
                ProtectedBlobKind::ResultCommit,
                ticket,
                self.states
                    .get(&ticket)
                    .ok_or_else(|| LedgerError::new("ticket_unknown"))?,
            )?;
            let bound_digest = self.append_protected_blob_bind(
                Event::ResultBlobBind,
                context,
                result_bytes,
                replacement,
            )?;
            if bound_digest != result_digest {
                return Err(LedgerError::new("protected_blob_content_digest_mismatch"));
            }
        } else {
            self.append_blob(
                Event::ResultChunk,
                Event::ResultCommit,
                ticket,
                result_bytes,
            )?;
        }
        if verified_pending {
            match self.states.get_mut(&ticket) {
                Some(StoredTicketState::ResultPendingProjection {
                    result, projection, ..
                }) if result.is_none() && projection.is_none() => {
                    *result = Some((result_bytes.to_vec(), result_digest));
                }
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            }
        } else {
            self.states.insert(
                ticket,
                StoredTicketState::Result {
                    run_binding_digest,
                    bytes: result_bytes.to_vec(),
                    digest: result_digest,
                    projection: None,
                },
            );
        }
        Ok(())
    }

    pub fn record_verified_result_pending(
        &mut self,
        ticket_digest: &str,
        run_binding_digest: &str,
        record: &DurableVerifiedResult,
    ) -> Result<(), LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        let expected_binding =
            decode_nonzero_digest(run_binding_digest, "run_binding_digest_invalid")?;
        if record.ticket_digest != ticket || record.run_binding_digest != expected_binding {
            return Err(LedgerError::new("verified_result_record_binding_mismatch"));
        }
        record.validate()?;
        self.ensure_writable()?;
        let prepared_receipt_digest = |bytes: &[u8]| -> [u8; 32] { Sha256::digest(bytes).into() };
        match self.states.get(&ticket) {
            Some(StoredTicketState::Consumed {
                run_binding_digest,
                prepared_receipt,
                canonical_policy_snapshot,
                recovery_bundle_digest,
                armed_receipt: Some(armed_receipt),
            }) if *run_binding_digest == expected_binding
                && prepared_receipt_digest(prepared_receipt) == record.prepared_receipt_digest
                && prepared_receipt_digest(armed_receipt) == record.armed_receipt_digest
                && prepared_receipt_digest(canonical_policy_snapshot)
                    == record.policy_snapshot_digest
                && *recovery_bundle_digest == record.recovery_bundle_digest => {}
            Some(StoredTicketState::ResultPendingProjection {
                run_binding_digest,
                verified_result,
                ..
            }) if *run_binding_digest == expected_binding && verified_result == record => {
                return Ok(())
            }
            Some(StoredTicketState::Consumed { .. })
            | Some(StoredTicketState::ResultPendingProjection { .. }) => {
                return Err(LedgerError::new("verified_result_record_binding_mismatch"));
            }
            None => return Err(LedgerError::new("ticket_unknown")),
            Some(_) => return Err(LedgerError::new("ticket_transition_invalid")),
        }
        let encoded = record.encode();
        if self.blob_authority.is_some() {
            let context = protected_blob_context_for_state(
                ProtectedBlobKind::VerifiedResult,
                ticket,
                self.states
                    .get(&ticket)
                    .ok_or_else(|| LedgerError::new("ticket_unknown"))?,
            )?;
            self.append_protected_blob_bind(
                Event::VerifiedResultBlobBind,
                context,
                &encoded,
                protected_pending_result_reserve(record.finalization_bytes().len())?,
            )?;
        } else {
            self.append_blob(
                Event::VerifiedResultChunk,
                Event::VerifiedResultCommit,
                ticket,
                &encoded,
            )?;
        }
        let state = self
            .states
            .remove(&ticket)
            .ok_or_else(|| LedgerError::new("ledger_transition_invalid"))?;
        let StoredTicketState::Consumed {
            run_binding_digest,
            prepared_receipt,
            canonical_policy_snapshot,
            recovery_bundle_digest,
            armed_receipt,
        } = state
        else {
            return Err(LedgerError::new("ledger_transition_invalid"));
        };
        let armed_receipt = armed_receipt
            .ok_or_else(|| LedgerError::new("verified_result_record_binding_mismatch"))?;
        self.states.insert(
            ticket,
            StoredTicketState::ResultPendingProjection {
                run_binding_digest,
                prepared_receipt,
                canonical_policy_snapshot,
                recovery_bundle_digest,
                armed_receipt,
                verified_result: record.clone(),
                result: None,
                projection: None,
            },
        );
        Ok(())
    }

    pub fn record_projection_pending(
        &mut self,
        ticket_digest: &str,
        run_binding_digest: &str,
        projection_bytes: &[u8],
        projection_digest: &[u8; 32],
    ) -> Result<(), LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        let expected_binding =
            decode_nonzero_digest(run_binding_digest, "run_binding_digest_invalid")?;
        if projection_bytes.is_empty()
            || projection_bytes.len() > MAX_RESULT_PROJECTION_SIZE
            || *projection_digest == ZERO_DIGEST
            || Sha256::digest(projection_bytes)[..] != *projection_digest
        {
            return Err(LedgerError::new("projection_bytes_invalid"));
        }
        self.ensure_writable()?;
        match self.states.get(&ticket) {
            Some(StoredTicketState::ResultPendingProjection {
                run_binding_digest,
                result: Some(_),
                projection: None,
                ..
            }) if *run_binding_digest == expected_binding => {}
            Some(StoredTicketState::ResultPendingProjection {
                run_binding_digest,
                projection: Some((stored, digest)),
                ..
            }) if *run_binding_digest == expected_binding
                && stored.as_slice() == projection_bytes
                && digest == projection_digest =>
            {
                return Ok(())
            }
            Some(StoredTicketState::ResultPendingProjection { .. }) => {
                return Err(LedgerError::new("projection_binding_mismatch"));
            }
            None => return Err(LedgerError::new("ticket_unknown")),
            Some(_) => return Err(LedgerError::new("ticket_transition_invalid")),
        }
        if self.blob_authority.is_some() {
            let context = protected_blob_context_for_state(
                ProtectedBlobKind::Projection,
                ticket,
                self.states
                    .get(&ticket)
                    .ok_or_else(|| LedgerError::new("ticket_unknown"))?,
            )?;
            let bound_digest = self.append_protected_blob_bind(
                Event::ProjectionBlobBind,
                context,
                projection_bytes,
                GenerationOutstandingReserve {
                    frames: 1,
                    ..GenerationOutstandingReserve::default()
                },
            )?;
            if bound_digest != *projection_digest {
                return Err(LedgerError::new("protected_blob_content_digest_mismatch"));
            }
        } else {
            self.append_blob(
                Event::ProjectionChunk,
                Event::ProjectionPendingCommit,
                ticket,
                projection_bytes,
            )?;
        }
        match self.states.get_mut(&ticket) {
            Some(StoredTicketState::ResultPendingProjection {
                projection: stored, ..
            }) if stored.is_none() => {
                *stored = Some((projection_bytes.to_vec(), *projection_digest));
            }
            _ => return Err(LedgerError::new("ledger_transition_invalid")),
        }
        Ok(())
    }

    pub fn commit_projection(
        &mut self,
        ticket_digest: &str,
        run_binding_digest: &str,
        projection_digest: &[u8; 32],
    ) -> Result<(), LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        let expected_binding =
            decode_nonzero_digest(run_binding_digest, "run_binding_digest_invalid")?;
        self.ensure_writable()?;
        let (result_bytes, result_digest, projection_bytes) = match self.states.get(&ticket) {
            Some(StoredTicketState::ResultPendingProjection {
                run_binding_digest,
                result: Some((result_bytes, result_digest)),
                projection: Some((projection_bytes, stored_projection_digest)),
                ..
            }) if *run_binding_digest == expected_binding
                && stored_projection_digest == projection_digest =>
            {
                (
                    result_bytes.clone(),
                    *result_digest,
                    projection_bytes.clone(),
                )
            }
            Some(StoredTicketState::Result {
                run_binding_digest,
                projection: Some((_, stored_projection_digest)),
                ..
            }) if *run_binding_digest == expected_binding
                && stored_projection_digest == projection_digest =>
            {
                return Ok(())
            }
            Some(StoredTicketState::ResultPendingProjection { .. })
            | Some(StoredTicketState::Result { .. }) => {
                return Err(LedgerError::new("projection_binding_mismatch"));
            }
            None => return Err(LedgerError::new("ticket_unknown")),
            Some(_) => return Err(LedgerError::new("ticket_transition_invalid")),
        };
        self.ensure_protected_transition_capacity(
            &ticket,
            GenerationOutstandingReserve::default(),
            1,
            0,
            0,
            0,
            0,
        )?;
        self.append_frame_raw(Event::ProjectionCommit, ticket, *projection_digest, &[])?;
        self.states.insert(
            ticket,
            StoredTicketState::Result {
                run_binding_digest: expected_binding,
                bytes: result_bytes,
                digest: result_digest,
                projection: Some((projection_bytes, *projection_digest)),
            },
        );
        Ok(())
    }

    pub fn pending_verified_results(
        &self,
    ) -> Result<Vec<(String, PendingVerifiedResult)>, LedgerError> {
        self.ensure_healthy()?;
        Ok(self
            .states
            .iter()
            .filter_map(|(ticket, state)| match state {
                StoredTicketState::ResultPendingProjection {
                    prepared_receipt,
                    canonical_policy_snapshot,
                    recovery_bundle_digest,
                    armed_receipt,
                    verified_result,
                    result,
                    projection,
                    ..
                } => Some((
                    hex_encode(ticket),
                    PendingVerifiedResult {
                        record: verified_result.clone(),
                        prepared_receipt: prepared_receipt.clone(),
                        canonical_policy_snapshot: canonical_policy_snapshot.clone(),
                        recovery_bundle_digest: *recovery_bundle_digest,
                        armed_receipt: armed_receipt.clone(),
                        result_committed: result.is_some(),
                        projection: projection.clone(),
                    },
                )),
                _ => None,
            })
            .collect())
    }

    pub fn projection_bytes(
        &self,
        ticket_digest: &str,
    ) -> Result<Option<(Vec<u8>, [u8; 32])>, LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        self.ensure_healthy()?;
        Ok(match self.states.get(&ticket) {
            Some(StoredTicketState::Result {
                projection: Some(projection),
                ..
            }) => Some(projection.clone()),
            _ => None,
        })
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
        self.ensure_protected_transition_capacity(
            &ticket,
            GenerationOutstandingReserve::default(),
            1,
            0,
            0,
            0,
            0,
        )?;
        self.append_frame_raw(Event::Burned, ticket, run_binding_digest, &[reason as u8])?;
        self.states.insert(
            ticket,
            StoredTicketState::Burned {
                run_binding_digest,
                reason,
                recovery_proof_digest: None,
            },
        );
        Ok(())
    }

    pub fn burn_recovered(
        &mut self,
        ticket_digest: &str,
        run_binding_digest: &str,
    ) -> Result<(), LedgerError> {
        self.burn_recovered_internal(
            ticket_digest,
            run_binding_digest,
            TicketBurnReason::RestartRecovery,
            None,
        )
    }

    /// Persists the exact terminal reason already verified by restart recovery.
    ///
    /// `Cancelled` and `TimedOut` require a supervisor-verified proof bound to
    /// the exact ticket, run, prepared receipt, optional Armed receipt, stage
    /// journal head, termination intent, terminal record, and cleanup record.
    /// `burn_recovered` remains the proof-free fail-closed fallback when no
    /// trustworthy normal terminal exists.
    pub fn burn_recovered_with_reason(
        &mut self,
        ticket_digest: &str,
        run_binding_digest: &str,
        reason: TicketBurnReason,
        recovery_proof: &RecoveredBurnProof,
    ) -> Result<(), LedgerError> {
        self.burn_recovered_internal(
            ticket_digest,
            run_binding_digest,
            reason,
            Some(recovery_proof),
        )
    }

    fn burn_recovered_internal(
        &mut self,
        ticket_digest: &str,
        run_binding_digest: &str,
        reason: TicketBurnReason,
        recovery_proof: Option<&RecoveredBurnProof>,
    ) -> Result<(), LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        let expected_binding =
            decode_nonzero_digest(run_binding_digest, "run_binding_digest_invalid")?;
        self.ensure_not_poisoned()?;
        if !matches!(
            reason,
            TicketBurnReason::Cancelled
                | TicketBurnReason::TimedOut
                | TicketBurnReason::RestartRecovery
        ) {
            return Err(LedgerError::new("ledger_recovery_burn_reason_invalid"));
        }
        let (stored_binding, prepared_receipt, armed_receipt) = match self.states.get(&ticket) {
            None => return Err(LedgerError::new("ticket_unknown")),
            Some(StoredTicketState::Issued {
                run_binding_digest,
                prepared_receipt: Some(prepared_receipt),
                canonical_policy_snapshot: Some(_),
                recovery_bundle_digest: Some(_),
            }) => (*run_binding_digest, prepared_receipt.as_slice(), None),
            Some(StoredTicketState::Consumed {
                run_binding_digest,
                prepared_receipt,
                armed_receipt,
                ..
            }) => (
                *run_binding_digest,
                prepared_receipt.as_slice(),
                armed_receipt.as_deref(),
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
        if stored_binding != expected_binding {
            return Err(LedgerError::new("ticket_run_binding_mismatch"));
        }
        let recovery_proof_digest = match reason {
            TicketBurnReason::Cancelled | TicketBurnReason::TimedOut => {
                let proof = recovery_proof
                    .ok_or_else(|| LedgerError::new("ledger_recovery_proof_required"))?;
                proof.validate()?;
                let prepared_receipt_digest: [u8; 32] = Sha256::digest(prepared_receipt).into();
                let armed_receipt_digest =
                    armed_receipt.map(|receipt| Sha256::digest(receipt).into());
                if proof.ticket_digest != ticket
                    || proof.run_binding_digest != stored_binding
                    || proof.prepared_receipt_digest != prepared_receipt_digest
                    || proof.armed_receipt_digest != armed_receipt_digest
                    || proof.reason != reason
                {
                    return Err(LedgerError::new("ledger_recovery_proof_binding_mismatch"));
                }
                Some(proof.recovery_proof_digest)
            }
            TicketBurnReason::RestartRecovery if recovery_proof.is_none() => None,
            _ => return Err(LedgerError::new("ledger_recovery_burn_reason_invalid")),
        };
        if self.pending_blob.as_ref().is_some_and(|pending| {
            pending.kind != PendingBlobKind::Result || pending.ticket_digest != ticket
        }) {
            return Err(LedgerError::new("ledger_recovery_receipt_required"));
        }
        self.ensure_protected_transition_capacity(
            &ticket,
            GenerationOutstandingReserve::default(),
            1,
            0,
            0,
            0,
            0,
        )?;
        match recovery_proof_digest {
            Some(proof_digest) => self.append_frame_raw(
                Event::RecoveredBurned,
                ticket,
                proof_digest,
                &[reason as u8],
            )?,
            None => {
                self.append_frame_raw(Event::Burned, ticket, stored_binding, &[reason as u8])?
            }
        }
        self.states.insert(
            ticket,
            StoredTicketState::Burned {
                run_binding_digest: stored_binding,
                reason,
                recovery_proof_digest,
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
                StoredTicketState::ResultPendingProjection { .. }
                | StoredTicketState::Result { .. }
                | StoredTicketState::Burned { .. } => continue,
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

        if self.blob_authority.is_some() {
            let mut reserve_after = protected_outstanding_reserve_for_states(&self.states)?;
            for ticket in &active {
                let state = self
                    .states
                    .get(ticket)
                    .ok_or_else(|| LedgerError::new("ledger_transition_invalid"))?;
                reserve_after =
                    reserve_after.checked_sub(protected_outstanding_reserve_for_state(state)?)?;
            }
            ensure_generation_operation_with_reserve(
                self.generation_usage,
                active.len() as u64,
                0,
                0,
                0,
                0,
                reserve_after,
                PRODUCTION_GENERATION_HARD_CAPS,
            )?;
        }

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
                    recovery_proof_digest: None,
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
                | StoredTicketState::ResultPendingProjection {
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

    pub fn recovered_burn_proof_digest(
        &self,
        ticket_digest: &str,
    ) -> Result<Option<String>, LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        self.ensure_healthy()?;
        Ok(match self.states.get(&ticket) {
            Some(StoredTicketState::Burned {
                recovery_proof_digest: Some(digest),
                ..
            }) => Some(hex_encode(digest)),
            _ => None,
        })
    }

    fn protected_reserve_after_ticket_transition(
        &self,
        ticket_digest: &[u8; 32],
        replacement: GenerationOutstandingReserve,
    ) -> Result<GenerationOutstandingReserve, LedgerError> {
        if self.blob_authority.is_none() {
            return Ok(GenerationOutstandingReserve::default());
        }
        let current = protected_outstanding_reserve_for_states(&self.states)?;
        let without_current = match self.states.get(ticket_digest) {
            Some(state) => current.checked_sub(protected_outstanding_reserve_for_state(state)?)?,
            None => current,
        };
        without_current.checked_add(replacement)
    }

    fn ensure_protected_transition_capacity(
        &self,
        ticket_digest: &[u8; 32],
        replacement: GenerationOutstandingReserve,
        additional_frames: u64,
        additional_tickets: u64,
        additional_referenced_blobs: u64,
        additional_logical_bytes: u64,
        additional_blob_stored_bytes: u64,
    ) -> Result<(), LedgerError> {
        if self.blob_authority.is_none() {
            return Ok(());
        }
        let reserve_after =
            self.protected_reserve_after_ticket_transition(ticket_digest, replacement)?;
        ensure_generation_operation_with_reserve(
            self.generation_usage,
            additional_frames,
            additional_tickets,
            additional_referenced_blobs,
            additional_logical_bytes,
            additional_blob_stored_bytes,
            reserve_after,
            PRODUCTION_GENERATION_HARD_CAPS,
        )
    }

    fn append_frame_raw(
        &mut self,
        event: Event,
        ticket_digest: [u8; 32],
        result_digest: [u8; 32],
        payload: &[u8],
    ) -> Result<(), LedgerError> {
        self.ensure_not_poisoned()?;
        self.generation_usage
            .ensure_add(1, 0, 0, 0, 0, PRODUCTION_GENERATION_HARD_CAPS)?;
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
        if let Err(error) = self.verify_storage_identity() {
            self.poisoned = true;
            return Err(error);
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
        self.generation_usage
            .commit_add(1, 0, 0, 0, 0, PRODUCTION_GENERATION_HARD_CAPS)?;
        if let Err(error) = self.verify_storage_identity() {
            self.poisoned = true;
            return Err(error);
        }
        Ok(())
    }

    fn append_blob(
        &mut self,
        chunk_event: Event,
        commit_event: Event,
        ticket_digest: [u8; 32],
        bytes: &[u8],
    ) -> Result<(), LedgerError> {
        let frame_count = blob_frame_count(bytes.len())?;
        self.generation_usage.ensure_add(
            frame_count,
            0,
            0,
            bytes.len() as u64,
            0,
            PRODUCTION_GENERATION_HARD_CAPS,
        )?;
        for chunk in bytes.chunks(PAYLOAD_SIZE) {
            self.append_frame_raw(chunk_event, ticket_digest, ZERO_DIGEST, chunk)?;
        }
        let digest: [u8; 32] = Sha256::digest(bytes).into();
        self.append_frame_raw(
            commit_event,
            ticket_digest,
            digest,
            &(bytes.len() as u64).to_be_bytes(),
        )?;
        self.generation_usage.commit_add(
            0,
            0,
            0,
            bytes.len() as u64,
            0,
            PRODUCTION_GENERATION_HARD_CAPS,
        )
    }

    fn append_protected_blob_bind(
        &mut self,
        event: Event,
        context: ProtectedBlobBindingContext,
        bytes: &[u8],
        reserve_after: GenerationOutstandingReserve,
    ) -> Result<[u8; 32], LedgerError> {
        let content_length = bytes.len() as u64;
        let object_length = content_length
            .checked_add(
                crate::primitive_evidence_authority_blob::PROTECTED_BLOB_HEADER_SIZE as u64,
            )
            .ok_or_else(|| LedgerError::new("ledger_generation_stored_limit_exceeded"))?;
        self.ensure_protected_transition_capacity(
            context.ticket_digest(),
            reserve_after,
            1,
            0,
            1,
            content_length,
            object_length,
        )?;
        self.generation_usage.ensure_add(
            1,
            0,
            1,
            content_length,
            object_length,
            PRODUCTION_GENERATION_HARD_CAPS,
        )?;
        let authority = self
            .blob_authority
            .as_mut()
            .ok_or_else(|| LedgerError::new("protected_blob_authority_not_connected"))?;
        let reopened = authority
            .materialize(context, bytes)
            .map_err(blob_ledger_error)?;
        let reference = reopened.reference();
        let content_digest = *reference.content_digest();
        let binding_digest = *reference.binding_digest();
        let relative_name = reference.relative_name().to_owned();
        let observed_object_length = reference.object_length();
        self.append_frame_raw(
            event,
            *context.ticket_digest(),
            content_digest,
            &binding_digest,
        )?;
        self.generation_usage.commit_add(
            0,
            0,
            1,
            content_length,
            observed_object_length,
            PRODUCTION_GENERATION_HARD_CAPS,
        )?;
        self.referenced_blob_names.insert(relative_name);
        Ok(content_digest)
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
        verify_file_identity(
            self.namespace_verification,
            &self.path,
            &self.file,
            &self.file_identity,
            "ledger_file_identity_changed",
        )?;
        verify_file_identity(
            self.namespace_verification,
            &self.anchor_path,
            &self.anchor_file,
            &self.anchor_file_identity,
            "ledger_anchor_identity_changed",
        )?;
        Ok(())
    }
}

fn validate_authenticated_pair_paths(path: &Path, anchor_path: &Path) -> Result<(), LedgerError> {
    if !path.is_absolute()
        || path.file_name().and_then(|value| value.to_str()) != Some("ledger.bin")
        || anchor_path != self::anchor_path(path)
        || path.components().any(|part| {
            matches!(
                part,
                std::path::Component::CurDir | std::path::Component::ParentDir
            )
        })
    {
        return Err(LedgerError::new("ledger_authenticated_pair_path_invalid"));
    }
    Ok(())
}

fn pending_blob_error(kind: PendingBlobKind) -> &'static str {
    match kind {
        PendingBlobKind::Result => "ledger_recovery_required",
        PendingBlobKind::PreparedReceipt | PendingBlobKind::ArmedReceipt => {
            "ledger_recovery_receipt_required"
        }
        PendingBlobKind::PolicySnapshot => "ledger_policy_snapshot_required",
        PendingBlobKind::VerifiedResult | PendingBlobKind::Projection => {
            "ledger_result_projection_recovery_required"
        }
    }
}

#[cfg(test)]
fn open_new_file(path: &Path) -> std::io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true).write(true).create_new(true);
    #[cfg(windows)]
    options.share_mode(0);
    options.open(path)
}

#[cfg(test)]
fn open_existing_file(path: &Path) -> std::io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true).write(true);
    #[cfg(windows)]
    options.share_mode(0);
    options.open(path)
}

fn open_existing_file_read_only(path: &Path) -> std::io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true);
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
        let mut id = unsafe { std::mem::zeroed::<FILE_ID_INFO>() };
        let mut standard = unsafe { std::mem::zeroed::<FILE_STANDARD_INFO>() };
        let raw = file.as_raw_handle().cast();
        if unsafe {
            GetFileInformationByHandleEx(
                raw,
                FileIdInfo,
                (&mut id as *mut FILE_ID_INFO).cast(),
                std::mem::size_of::<FILE_ID_INFO>() as u32,
            )
        } == 0
            || unsafe {
                GetFileInformationByHandleEx(
                    raw,
                    FileStandardInfo,
                    (&mut standard as *mut FILE_STANDARD_INFO).cast(),
                    std::mem::size_of::<FILE_STANDARD_INFO>() as u32,
                )
            } == 0
            || standard.Directory != 0
            || standard.DeletePending != 0
            || standard.NumberOfLinks != 1
            || standard.EndOfFile < 0
            || id.VolumeSerialNumber == 0
            || id.FileId.Identifier.iter().all(|byte| *byte == 0)
            || u64::try_from(standard.EndOfFile).ok() != Some(metadata.len())
            || metadata.file_attributes()
                & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)
                != 0
        {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "unstable ledger file identity",
            ));
        }
        Ok(StableFileIdentity {
            volume_serial: id.VolumeSerialNumber,
            file_id: id.FileId.Identifier,
            link_count: standard.NumberOfLinks,
            file_attributes: metadata.file_attributes(),
            byte_length: metadata.len(),
        })
    }
    #[cfg(not(any(unix, windows)))]
    {
        Ok(StableFileIdentity {
            created: metadata.created()?,
        })
    }
}

fn stable_file_identity_digest(role: &[u8], identity: &StableFileIdentity) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(FILE_IDENTITY_DIGEST_DOMAIN);
    digest.update((role.len() as u64).to_be_bytes());
    digest.update(role);
    #[cfg(unix)]
    {
        digest.update(identity.device.to_be_bytes());
        digest.update(identity.inode.to_be_bytes());
    }
    #[cfg(windows)]
    {
        digest.update(identity.volume_serial.to_be_bytes());
        digest.update(identity.file_id);
        digest.update(identity.link_count.to_be_bytes());
        digest.update(identity.file_attributes.to_be_bytes());
    }
    #[cfg(not(any(unix, windows)))]
    {
        let duration = identity
            .created
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default();
        digest.update(duration.as_secs().to_be_bytes());
        digest.update(duration.subsec_nanos().to_be_bytes());
    }
    digest.finalize().into()
}

fn hash_file_prefix(file: &mut File, length: u64) -> Result<[u8; 32], LedgerError> {
    if length == 0
        || file
            .metadata()
            .map_err(|_| LedgerError::new("ledger_metadata_failed"))?
            .len()
            < length
    {
        return Err(LedgerError::new("ledger_size_invalid"));
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| LedgerError::new("ledger_read_failed"))?;
    let mut remaining = length;
    let mut buffer = [0u8; 8192];
    let mut digest = Sha256::new();
    while remaining > 0 {
        let take = remaining.min(buffer.len() as u64) as usize;
        file.read_exact(&mut buffer[..take])
            .map_err(|_| LedgerError::new("ledger_read_failed"))?;
        digest.update(&buffer[..take]);
        remaining -= take as u64;
    }
    Ok(digest.finalize().into())
}

fn verify_file_identity(
    namespace_verification: LedgerNamespaceVerification,
    path: &Path,
    file: &File,
    expected: &StableFileIdentity,
    error_code: &'static str,
) -> Result<(), LedgerError> {
    match namespace_verification {
        LedgerNamespaceVerification::PathAndHandle => {
            verify_stable_file(path, file, expected, error_code)
        }
        LedgerNamespaceVerification::AuthenticatedHeldHandle => {
            let actual = stable_file_identity(file).map_err(|_| LedgerError::new(error_code))?;
            if !same_stable_file_object(&actual, expected) {
                return Err(LedgerError::new(error_code));
            }
            Ok(())
        }
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
    let path_matches = path_metadata.file_attributes() == handle_identity.file_attributes
        && path_metadata.len() == handle_identity.byte_length
        && expected.link_count > 0
        && expected.volume_serial != 0
        && expected.file_id.iter().any(|byte| *byte != 0);
    #[cfg(not(any(unix, windows)))]
    let path_identity = StableFileIdentity {
        created: path_metadata
            .created()
            .map_err(|_| LedgerError::new(error_code))?,
    };
    #[cfg(any(unix, not(any(unix, windows))))]
    let path_matches = path_identity == *expected;
    if !same_stable_file_object(&handle_identity, expected) || !path_matches {
        return Err(LedgerError::new(error_code));
    }
    Ok(())
}

fn same_stable_file_object(left: &StableFileIdentity, right: &StableFileIdentity) -> bool {
    #[cfg(unix)]
    {
        left.device == right.device && left.inode == right.inode
    }
    #[cfg(windows)]
    {
        left.volume_serial == right.volume_serial
            && left.file_id == right.file_id
            && left.link_count == right.link_count
            && left.file_attributes == right.file_attributes
    }
    #[cfg(not(any(unix, windows)))]
    {
        left.created == right.created
    }
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
    load_anchor_with_caps(file, expected_identity, PRODUCTION_GENERATION_HARD_CAPS)
}

fn load_anchor_with_caps(
    file: &mut File,
    expected_identity: &LedgerIdentity,
    caps: GenerationHardCaps,
) -> Result<LoadedAnchor, LedgerError> {
    let length = file
        .metadata()
        .map_err(|_| LedgerError::new("ledger_anchor_metadata_failed"))?
        .len();
    if length == 0 {
        return Err(LedgerError::new("ledger_anchor_empty"));
    }
    let maximum_anchor_bytes = caps
        .frames
        .checked_mul(2)
        .and_then(|records| records.checked_mul(ANCHOR_RECORD_SIZE as u64))
        .ok_or_else(|| LedgerError::new("ledger_generation_frame_limit_exceeded"))?;
    if length > maximum_anchor_bytes {
        return Err(LedgerError::new("ledger_generation_frame_limit_exceeded"));
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| LedgerError::new("ledger_anchor_read_failed"))?;
    let maximum_committed = usize::try_from(caps.frames)
        .map_err(|_| LedgerError::new("ledger_generation_frame_limit_exceeded"))?;
    let committed_capacity = usize::try_from(length / (2 * ANCHOR_RECORD_SIZE as u64))
        .map_err(|_| LedgerError::new("ledger_generation_frame_limit_exceeded"))?
        .min(maximum_committed);
    let mut committed_frames = Vec::new();
    committed_frames
        .try_reserve_exact(committed_capacity)
        .map_err(|_| LedgerError::new("ledger_generation_allocation_failed"))?;
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
    let ledger_length_u64 = ledger_file
        .metadata()
        .map_err(|_| LedgerError::new("ledger_metadata_failed"))?
        .len();
    if ledger_length_u64 > MAX_GENERATION_LEDGER_BYTES {
        return Err(LedgerError::new("ledger_generation_frame_limit_exceeded"));
    }
    let ledger_length = usize::try_from(ledger_length_u64)
        .map_err(|_| LedgerError::new("ledger_generation_frame_limit_exceeded"))?;
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
    let mut ledger_bytes = Vec::new();
    ledger_bytes
        .try_reserve_exact(ledger_length)
        .map_err(|_| LedgerError::new("ledger_generation_allocation_failed"))?;
    ledger_bytes.resize(ledger_length, 0);
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

fn reconcile_loaded_protected_namespace(
    loaded: &mut LoadedLedger,
    authority: &mut ProtectedBlobAuthority,
    caps: GenerationHardCaps,
) -> Result<(), LedgerError> {
    authority
        .reconcile_unreferenced(&loaded.referenced_blob_names)
        .map_err(blob_ledger_error)?;
    loaded.generation_usage.blob_stored_bytes = authority.namespace_usage().1;
    ensure_generation_operation_with_reserve(
        loaded.generation_usage,
        0,
        0,
        0,
        0,
        0,
        protected_outstanding_reserve_for_states(&loaded.states)?,
        caps,
    )
}

fn load_frames(
    file: &mut File,
    expected_identity: &LedgerIdentity,
    blob_authority: Option<&mut ProtectedBlobAuthority>,
) -> Result<LoadedLedger, LedgerError> {
    load_frames_with_caps_mode(
        file,
        expected_identity,
        blob_authority,
        PRODUCTION_GENERATION_HARD_CAPS,
        false,
    )
}

fn load_frames_with_caps(
    file: &mut File,
    expected_identity: &LedgerIdentity,
    blob_authority: Option<&mut ProtectedBlobAuthority>,
    caps: GenerationHardCaps,
) -> Result<LoadedLedger, LedgerError> {
    load_frames_with_caps_mode(file, expected_identity, blob_authority, caps, true)
}

fn load_frames_with_caps_mode(
    file: &mut File,
    expected_identity: &LedgerIdentity,
    mut blob_authority: Option<&mut ProtectedBlobAuthority>,
    caps: GenerationHardCaps,
    enforce_outstanding_reserve: bool,
) -> Result<LoadedLedger, LedgerError> {
    let blob_policy = if blob_authority.is_some() {
        ProtectedBlobReplayPolicy::ProtectedRequired
    } else {
        ProtectedBlobReplayPolicy::LegacyAllowed
    };
    let length = file
        .metadata()
        .map_err(|_| LedgerError::new("ledger_metadata_failed"))?
        .len();
    if length == 0 {
        return Err(LedgerError::new("ledger_empty"));
    }
    let maximum_ledger_bytes = caps
        .frames
        .checked_mul(FRAME_SIZE as u64)
        .ok_or_else(|| LedgerError::new("ledger_generation_frame_limit_exceeded"))?;
    if length > maximum_ledger_bytes {
        return Err(LedgerError::new("ledger_generation_frame_limit_exceeded"));
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
    let mut referenced_blob_names = BTreeSet::new();
    let namespace_blob_stored_bytes = blob_authority
        .as_deref()
        .map(|authority| authority.namespace_usage().1)
        .unwrap_or(0);
    let mut generation_usage = GenerationUsage {
        frames: count,
        blob_stored_bytes: namespace_blob_stored_bytes,
        ..GenerationUsage::default()
    };
    generation_usage.ensure_add(0, 0, 0, 0, 0, caps)?;
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
            apply_loaded_event(
                &mut states,
                &mut pending_blob,
                &frame,
                blob_authority.as_deref_mut(),
                &mut referenced_blob_names,
                &mut generation_usage,
                caps,
                blob_policy,
                true,
            )?;
        }
        previous_hash = frame.frame_hash;
    }
    if enforce_outstanding_reserve && blob_policy == ProtectedBlobReplayPolicy::ProtectedRequired {
        ensure_generation_operation_with_reserve(
            generation_usage,
            0,
            0,
            0,
            0,
            0,
            protected_outstanding_reserve_for_states(&states)?,
            caps,
        )?;
    }
    Ok(LoadedLedger {
        states,
        next_sequence: count,
        previous_hash,
        pending_blob,
        referenced_blob_names,
        generation_usage,
    })
}

fn load_committed_frame_prefix(
    frames: &[[u8; FRAME_SIZE]],
    count: usize,
    expected_identity: &LedgerIdentity,
    blob_authority: Option<&mut ProtectedBlobAuthority>,
) -> Result<LoadedLedger, LedgerError> {
    load_committed_frame_prefix_with_caps(
        frames,
        count,
        expected_identity,
        blob_authority,
        PRODUCTION_GENERATION_HARD_CAPS,
    )
}

fn load_committed_frame_prefix_with_caps(
    frames: &[[u8; FRAME_SIZE]],
    count: usize,
    expected_identity: &LedgerIdentity,
    mut blob_authority: Option<&mut ProtectedBlobAuthority>,
    caps: GenerationHardCaps,
) -> Result<LoadedLedger, LedgerError> {
    let blob_policy = if blob_authority.is_some() {
        ProtectedBlobReplayPolicy::ProtectedRequired
    } else {
        ProtectedBlobReplayPolicy::LegacyAllowed
    };
    if count == 0 || count > frames.len() || count as u64 > caps.frames {
        return Err(LedgerError::new("ledger_size_invalid"));
    }
    let mut states = BTreeMap::new();
    let mut previous_hash = ZERO_DIGEST;
    let mut pending_blob = None;
    let mut referenced_blob_names = BTreeSet::new();
    let mut generation_usage = GenerationUsage {
        frames: count as u64,
        ..GenerationUsage::default()
    };
    generation_usage.ensure_add(0, 0, 0, 0, 0, caps)?;
    for (expected_sequence, bytes) in frames[..count].iter().enumerate() {
        let frame = decode_frame(bytes)?;
        if frame.sequence != expected_sequence as u64 {
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
            apply_loaded_event(
                &mut states,
                &mut pending_blob,
                &frame,
                blob_authority.as_deref_mut(),
                &mut referenced_blob_names,
                &mut generation_usage,
                caps,
                blob_policy,
                false,
            )?;
        }
        previous_hash = frame.frame_hash;
    }
    if blob_policy == ProtectedBlobReplayPolicy::ProtectedRequired {
        ensure_generation_operation_with_reserve(
            generation_usage,
            0,
            0,
            0,
            0,
            0,
            protected_outstanding_reserve_for_states(&states)?,
            caps,
        )?;
    }
    Ok(LoadedLedger {
        states,
        next_sequence: count as u64,
        previous_hash,
        pending_blob,
        referenced_blob_names,
        generation_usage,
    })
}

fn reopen_loaded_protected_blob(
    states: &BTreeMap<[u8; 32], StoredTicketState>,
    pending_blob: &Option<PendingBlob>,
    frame: &DecodedFrame,
    kind: ProtectedBlobKind,
    blob_authority: Option<&mut ProtectedBlobAuthority>,
    referenced_blob_names: &mut BTreeSet<String>,
    generation_usage: &mut GenerationUsage,
    caps: GenerationHardCaps,
    namespace_storage_precounted: bool,
) -> Result<Vec<u8>, LedgerError> {
    if pending_blob.is_some() || frame.result_digest == ZERO_DIGEST || frame.payload.len() != 32 {
        return Err(LedgerError::new("protected_blob_bind_invalid"));
    }
    let context = protected_blob_context_for_state(
        kind,
        frame.ticket_digest,
        states
            .get(&frame.ticket_digest)
            .ok_or_else(|| LedgerError::new("ticket_unknown"))?,
    )?;
    let binding_digest: [u8; 32] = frame.payload.as_slice().try_into().unwrap();
    generation_usage.ensure_add(0, 0, 1, 0, 0, caps)?;
    let remaining_logical_bytes = caps
        .logical_bytes
        .checked_sub(generation_usage.logical_bytes)
        .ok_or_else(|| LedgerError::new("ledger_generation_logical_limit_exceeded"))?;
    let maximum_object_bytes = if namespace_storage_precounted {
        (kind.maximum_content_size() as u64)
            .checked_add(
                crate::primitive_evidence_authority_blob::PROTECTED_BLOB_HEADER_SIZE as u64,
            )
            .ok_or_else(|| LedgerError::new("ledger_generation_stored_limit_exceeded"))?
    } else {
        caps.stored_bytes
            .checked_sub(generation_usage.stored_bytes()?)
            .ok_or_else(|| LedgerError::new("ledger_generation_stored_limit_exceeded"))?
    };
    let authority =
        blob_authority.ok_or_else(|| LedgerError::new("protected_blob_authority_not_connected"))?;
    let reopened = authority
        .reopen_bound_with_limits(
            context,
            frame.result_digest,
            binding_digest,
            remaining_logical_bytes,
            maximum_object_bytes,
        )
        .map_err(blob_ledger_error)?;
    if reopened.reference().context() != &context
        || reopened.reference().content_digest() != &frame.result_digest
        || reopened.reference().binding_digest() != &binding_digest
    {
        return Err(LedgerError::new("protected_blob_bind_invalid"));
    }
    let reference = reopened.reference();
    generation_usage.commit_add(
        0,
        0,
        1,
        reference.content_length(),
        if namespace_storage_precounted {
            0
        } else {
            reference.object_length()
        },
        caps,
    )?;
    if !referenced_blob_names.insert(reference.relative_name().to_owned()) {
        return Err(LedgerError::new("protected_blob_bind_duplicate"));
    }
    Ok(reopened.into_content())
}

fn apply_loaded_event(
    states: &mut BTreeMap<[u8; 32], StoredTicketState>,
    pending_blob: &mut Option<PendingBlob>,
    frame: &DecodedFrame,
    mut blob_authority: Option<&mut ProtectedBlobAuthority>,
    referenced_blob_names: &mut BTreeSet<String>,
    generation_usage: &mut GenerationUsage,
    caps: GenerationHardCaps,
    blob_policy: ProtectedBlobReplayPolicy,
    namespace_storage_precounted: bool,
) -> Result<(), LedgerError> {
    if frame.event == Event::Initialize || frame.ticket_digest == ZERO_DIGEST {
        return Err(LedgerError::new("ledger_transition_invalid"));
    }
    if blob_policy == ProtectedBlobReplayPolicy::ProtectedRequired
        && matches!(
            frame.event,
            Event::ResultChunk
                | Event::ResultCommit
                | Event::VerifiedResultChunk
                | Event::VerifiedResultCommit
                | Event::ProjectionChunk
                | Event::ProjectionPendingCommit
        )
    {
        return Err(LedgerError::new("protected_blob_inline_evidence_forbidden"));
    }
    if let Some(pending) = pending_blob.as_ref() {
        let allowed = match pending.kind {
            PendingBlobKind::Result => matches!(
                frame.event,
                Event::ResultChunk | Event::ResultCommit | Event::Burned | Event::RecoveredBurned
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
            PendingBlobKind::VerifiedResult => matches!(
                frame.event,
                Event::VerifiedResultChunk | Event::VerifiedResultCommit
            ),
            PendingBlobKind::Projection => matches!(
                frame.event,
                Event::ProjectionChunk | Event::ProjectionPendingCommit
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
                || states.contains_key(&frame.ticket_digest)
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            generation_usage.commit_add(0, 1, 0, 0, 0, caps)?;
            states.insert(
                frame.ticket_digest,
                StoredTicketState::Issued {
                    run_binding_digest: frame.result_digest,
                    prepared_receipt: None,
                    canonical_policy_snapshot: None,
                    recovery_bundle_digest: None,
                },
            );
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
                Some(StoredTicketState::ResultPendingProjection {
                    run_binding_digest,
                    result: None,
                    ..
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
            let (consumed_binding, verified_result) = match states.get(&frame.ticket_digest) {
                Some(StoredTicketState::Consumed {
                    run_binding_digest, ..
                }) => (*run_binding_digest, None),
                Some(StoredTicketState::ResultPendingProjection {
                    run_binding_digest,
                    verified_result,
                    result: None,
                    ..
                }) => (*run_binding_digest, Some(verified_result.clone())),
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
            generation_usage.commit_add(0, 0, 0, pending.bytes.len() as u64, 0, caps)?;
            if let Some(verified_result) = verified_result {
                if verified_result.finalization_bytes() != pending.bytes {
                    return Err(LedgerError::new("verified_result_record_binding_mismatch"));
                }
                match states.get_mut(&frame.ticket_digest) {
                    Some(StoredTicketState::ResultPendingProjection {
                        run_binding_digest,
                        result,
                        projection,
                        ..
                    }) if *run_binding_digest == consumed_binding
                        && result.is_none()
                        && projection.is_none() =>
                    {
                        *result = Some((pending.bytes, frame.result_digest));
                    }
                    _ => return Err(LedgerError::new("ledger_transition_invalid")),
                }
            } else {
                states.insert(
                    frame.ticket_digest,
                    StoredTicketState::Result {
                        run_binding_digest: consumed_binding,
                        bytes: pending.bytes,
                        digest: frame.result_digest,
                        projection: None,
                    },
                );
            }
        }
        Event::ResultBlobBind => {
            let (consumed_binding, verified_result) = match states.get(&frame.ticket_digest) {
                Some(StoredTicketState::ResultPendingProjection {
                    run_binding_digest,
                    verified_result,
                    result: None,
                    projection: None,
                    ..
                }) => (*run_binding_digest, verified_result.clone()),
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            };
            let bytes = reopen_loaded_protected_blob(
                states,
                pending_blob,
                frame,
                ProtectedBlobKind::ResultCommit,
                blob_authority.as_deref_mut(),
                referenced_blob_names,
                generation_usage,
                caps,
                namespace_storage_precounted,
            )?;
            if bytes.is_empty() || bytes.len() > MAX_RESULT_SIZE {
                return Err(LedgerError::new("ledger_result_invalid"));
            }
            if verified_result.finalization_bytes() != bytes {
                return Err(LedgerError::new("verified_result_record_binding_mismatch"));
            }
            match states.get_mut(&frame.ticket_digest) {
                Some(StoredTicketState::ResultPendingProjection {
                    run_binding_digest,
                    result,
                    projection,
                    ..
                }) if *run_binding_digest == consumed_binding
                    && result.is_none()
                    && projection.is_none() =>
                {
                    *result = Some((bytes, frame.result_digest));
                }
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            }
        }
        Event::VerifiedResultChunk => {
            if !matches!(
                states.get(&frame.ticket_digest),
                Some(StoredTicketState::Consumed { .. })
            ) || frame.result_digest != ZERO_DIGEST
                || frame.payload.is_empty()
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            append_loaded_blob_chunk(
                pending_blob,
                PendingBlobKind::VerifiedResult,
                frame,
                VERIFIED_RESULT_RECORD_FIXED_SIZE + MAX_RESULT_SIZE + MAX_ORIGIN_ENVELOPE_SIZE,
            )?;
        }
        Event::VerifiedResultCommit => {
            let bytes = commit_loaded_blob(
                pending_blob,
                PendingBlobKind::VerifiedResult,
                frame,
                VERIFIED_RESULT_RECORD_FIXED_SIZE + MAX_RESULT_SIZE + MAX_ORIGIN_ENVELOPE_SIZE,
            )?;
            generation_usage.commit_add(0, 0, 0, bytes.len() as u64, 0, caps)?;
            let record = DurableVerifiedResult::decode(&bytes)?;
            if record.ticket_digest != frame.ticket_digest {
                return Err(LedgerError::new("verified_result_record_binding_mismatch"));
            }
            let state = states
                .remove(&frame.ticket_digest)
                .ok_or_else(|| LedgerError::new("ledger_transition_invalid"))?;
            let StoredTicketState::Consumed {
                run_binding_digest,
                prepared_receipt,
                canonical_policy_snapshot,
                recovery_bundle_digest,
                armed_receipt: Some(armed_receipt),
            } = state
            else {
                return Err(LedgerError::new("ledger_transition_invalid"));
            };
            let prepared_receipt_digest: [u8; 32] = Sha256::digest(&prepared_receipt).into();
            let armed_receipt_digest: [u8; 32] = Sha256::digest(&armed_receipt).into();
            let policy_snapshot_digest: [u8; 32] =
                Sha256::digest(&canonical_policy_snapshot).into();
            if record.run_binding_digest != run_binding_digest
                || record.prepared_receipt_digest != prepared_receipt_digest
                || record.armed_receipt_digest != armed_receipt_digest
                || record.policy_snapshot_digest != policy_snapshot_digest
                || record.recovery_bundle_digest != recovery_bundle_digest
            {
                return Err(LedgerError::new("verified_result_record_binding_mismatch"));
            }
            states.insert(
                frame.ticket_digest,
                StoredTicketState::ResultPendingProjection {
                    run_binding_digest,
                    prepared_receipt,
                    canonical_policy_snapshot,
                    recovery_bundle_digest,
                    armed_receipt,
                    verified_result: record,
                    result: None,
                    projection: None,
                },
            );
        }
        Event::VerifiedResultBlobBind => {
            if !matches!(
                states.get(&frame.ticket_digest),
                Some(StoredTicketState::Consumed {
                    armed_receipt: Some(_),
                    ..
                })
            ) {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            let bytes = reopen_loaded_protected_blob(
                states,
                pending_blob,
                frame,
                ProtectedBlobKind::VerifiedResult,
                blob_authority.as_deref_mut(),
                referenced_blob_names,
                generation_usage,
                caps,
                namespace_storage_precounted,
            )?;
            let record = DurableVerifiedResult::decode(&bytes)?;
            if record.ticket_digest != frame.ticket_digest {
                return Err(LedgerError::new("verified_result_record_binding_mismatch"));
            }
            let state = states
                .remove(&frame.ticket_digest)
                .ok_or_else(|| LedgerError::new("ledger_transition_invalid"))?;
            let StoredTicketState::Consumed {
                run_binding_digest,
                prepared_receipt,
                canonical_policy_snapshot,
                recovery_bundle_digest,
                armed_receipt: Some(armed_receipt),
            } = state
            else {
                return Err(LedgerError::new("ledger_transition_invalid"));
            };
            let prepared_receipt_digest: [u8; 32] = Sha256::digest(&prepared_receipt).into();
            let armed_receipt_digest: [u8; 32] = Sha256::digest(&armed_receipt).into();
            let policy_snapshot_digest: [u8; 32] =
                Sha256::digest(&canonical_policy_snapshot).into();
            if record.run_binding_digest != run_binding_digest
                || record.prepared_receipt_digest != prepared_receipt_digest
                || record.armed_receipt_digest != armed_receipt_digest
                || record.policy_snapshot_digest != policy_snapshot_digest
                || record.recovery_bundle_digest != recovery_bundle_digest
            {
                return Err(LedgerError::new("verified_result_record_binding_mismatch"));
            }
            states.insert(
                frame.ticket_digest,
                StoredTicketState::ResultPendingProjection {
                    run_binding_digest,
                    prepared_receipt,
                    canonical_policy_snapshot,
                    recovery_bundle_digest,
                    armed_receipt,
                    verified_result: record,
                    result: None,
                    projection: None,
                },
            );
        }
        Event::ProjectionChunk => {
            if !matches!(
                states.get(&frame.ticket_digest),
                Some(StoredTicketState::ResultPendingProjection {
                    result: Some(_),
                    projection: None,
                    ..
                })
            ) || frame.result_digest != ZERO_DIGEST
                || frame.payload.is_empty()
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            append_loaded_blob_chunk(
                pending_blob,
                PendingBlobKind::Projection,
                frame,
                MAX_RESULT_PROJECTION_SIZE,
            )?;
        }
        Event::ProjectionPendingCommit => {
            let bytes = commit_loaded_blob(
                pending_blob,
                PendingBlobKind::Projection,
                frame,
                MAX_RESULT_PROJECTION_SIZE,
            )?;
            generation_usage.commit_add(0, 0, 0, bytes.len() as u64, 0, caps)?;
            match states.get_mut(&frame.ticket_digest) {
                Some(StoredTicketState::ResultPendingProjection {
                    result: Some(_),
                    projection,
                    ..
                }) if projection.is_none() => {
                    *projection = Some((bytes, frame.result_digest));
                }
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            }
        }
        Event::ProjectionBlobBind => {
            if !matches!(
                states.get(&frame.ticket_digest),
                Some(StoredTicketState::ResultPendingProjection {
                    result: Some(_),
                    projection: None,
                    ..
                })
            ) {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            let bytes = reopen_loaded_protected_blob(
                states,
                pending_blob,
                frame,
                ProtectedBlobKind::Projection,
                blob_authority.as_deref_mut(),
                referenced_blob_names,
                generation_usage,
                caps,
                namespace_storage_precounted,
            )?;
            if bytes.is_empty() || bytes.len() > MAX_RESULT_PROJECTION_SIZE {
                return Err(LedgerError::new("projection_bytes_invalid"));
            }
            match states.get_mut(&frame.ticket_digest) {
                Some(StoredTicketState::ResultPendingProjection {
                    result: Some(_),
                    projection,
                    ..
                }) if projection.is_none() => {
                    *projection = Some((bytes, frame.result_digest));
                }
                _ => return Err(LedgerError::new("ledger_transition_invalid")),
            }
        }
        Event::ProjectionCommit => {
            if frame.result_digest == ZERO_DIGEST || !frame.payload.is_empty() {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            let state = states
                .remove(&frame.ticket_digest)
                .ok_or_else(|| LedgerError::new("ledger_transition_invalid"))?;
            let StoredTicketState::ResultPendingProjection {
                run_binding_digest,
                prepared_receipt: _,
                canonical_policy_snapshot: _,
                recovery_bundle_digest: _,
                armed_receipt: _,
                verified_result: _,
                result: Some((result_bytes, result_digest)),
                projection: Some((projection_bytes, projection_digest)),
            } = state
            else {
                return Err(LedgerError::new("ledger_transition_invalid"));
            };
            if projection_digest != frame.result_digest {
                return Err(LedgerError::new("projection_binding_mismatch"));
            }
            states.insert(
                frame.ticket_digest,
                StoredTicketState::Result {
                    run_binding_digest,
                    bytes: result_bytes,
                    digest: result_digest,
                    projection: Some((projection_bytes, projection_digest)),
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
            generation_usage.commit_add(0, 0, 0, bytes.len() as u64, 0, caps)?;
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
            generation_usage.commit_add(0, 0, 0, bytes.len() as u64, 0, caps)?;
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
            generation_usage.commit_add(0, 0, 0, bytes.len() as u64, 0, caps)?;
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
                    recovery_proof_digest: None,
                },
            );
        }
        Event::RecoveredBurned => {
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
            if frame.result_digest == ZERO_DIGEST || frame.payload.len() != 1 {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            let reason = TicketBurnReason::decode(frame.payload[0])?;
            if !matches!(
                reason,
                TicketBurnReason::Cancelled | TicketBurnReason::TimedOut
            ) || pending_blob.as_ref().is_some_and(|pending| {
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
                    recovery_proof_digest: Some(frame.result_digest),
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

fn blob_frame_count(byte_length: usize) -> Result<u64, LedgerError> {
    if byte_length == 0 {
        return Err(LedgerError::new("ledger_blob_empty"));
    }
    let chunks = byte_length
        .checked_add(PAYLOAD_SIZE - 1)
        .and_then(|value| value.checked_div(PAYLOAD_SIZE))
        .and_then(|value| u64::try_from(value).ok())
        .ok_or_else(|| LedgerError::new("ledger_generation_frame_limit_exceeded"))?;
    chunks
        .checked_add(1)
        .ok_or_else(|| LedgerError::new("ledger_generation_frame_limit_exceeded"))
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

fn blob_ledger_error(
    error: crate::primitive_evidence_authority_blob::ProtectedBlobError,
) -> LedgerError {
    LedgerError::new(error.code().to_owned())
}

fn verify_blob_namespace(
    authority: &ProtectedBlobAuthority,
    identity: &LedgerIdentity,
) -> Result<(), LedgerError> {
    authority.verify_namespace().map_err(blob_ledger_error)?;
    let descriptor = authority.descriptor();
    if descriptor.generation_digest() != identity.authority_generation_digest()
        || descriptor.ledger_identity_digest() != &identity.canonical_digest()
    {
        return Err(LedgerError::new("protected_blob_ledger_identity_mismatch"));
    }
    Ok(())
}

fn prepared_source_digest_from_receipt(bytes: &[u8]) -> Result<[u8; 32], LedgerError> {
    const MAGIC: &[u8; 8] = b"VRCPRP04";
    const ENCODED_LENGTH: usize = 8 + 14 * 32 + 3 * 8;
    const PROTECTED_SOURCE_OFFSET: usize = 8 + 12 * 32;
    if bytes.len() != ENCODED_LENGTH || &bytes[..8] != MAGIC {
        return Err(LedgerError::new(
            "protected_blob_prepared_source_unavailable",
        ));
    }
    let value: [u8; 32] = bytes[PROTECTED_SOURCE_OFFSET..PROTECTED_SOURCE_OFFSET + 32]
        .try_into()
        .unwrap();
    if value == ZERO_DIGEST {
        return Err(LedgerError::new(
            "protected_blob_prepared_source_unavailable",
        ));
    }
    Ok(value)
}

fn protected_blob_context_for_state(
    kind: ProtectedBlobKind,
    ticket: [u8; 32],
    state: &StoredTicketState,
) -> Result<ProtectedBlobBindingContext, LedgerError> {
    let (run_binding, prepared_receipt, policy_snapshot, recovery_bundle) = match state {
        StoredTicketState::Consumed {
            run_binding_digest,
            prepared_receipt,
            canonical_policy_snapshot,
            recovery_bundle_digest,
            ..
        }
        | StoredTicketState::ResultPendingProjection {
            run_binding_digest,
            prepared_receipt,
            canonical_policy_snapshot,
            recovery_bundle_digest,
            ..
        } => (
            *run_binding_digest,
            prepared_receipt.as_slice(),
            canonical_policy_snapshot.as_slice(),
            *recovery_bundle_digest,
        ),
        _ => return Err(LedgerError::new("protected_blob_ticket_state_invalid")),
    };
    ProtectedBlobBindingContext::new(
        kind,
        ticket,
        run_binding,
        prepared_source_digest_from_receipt(prepared_receipt)?,
        Sha256::digest(policy_snapshot).into(),
        recovery_bundle,
    )
    .map_err(blob_ledger_error)
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
