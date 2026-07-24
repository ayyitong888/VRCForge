use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fmt,
    fs::{File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::Path,
};

#[cfg(windows)]
use std::os::windows::fs::OpenOptionsExt;

pub const FRAME_SIZE: usize = 256;
pub const MAX_RESULT_SIZE: usize = 64 * 1024;

const MAGIC: &[u8; 16] = b"VRCFAUTHLEDGER01";
const VERSION: u16 = 1;
const PAYLOAD_OFFSET: usize = 192;
const PAYLOAD_SIZE: usize = 32;
const HASH_OFFSET: usize = FRAME_SIZE - 32;
const ZERO_DIGEST: [u8; 32] = [0; 32];

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
enum StoredTicketState {
    Issued,
    Consumed,
    Result { bytes: Vec<u8>, digest: [u8; 32] },
    Burned,
}

impl StoredTicketState {
    fn public(&self) -> TicketState {
        match self {
            Self::Issued => TicketState::Issued,
            Self::Consumed => TicketState::Consumed,
            Self::Result { .. } => TicketState::Result,
            Self::Burned => TicketState::Burned,
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
            _ => Err(LedgerError::new("ledger_event_invalid")),
        }
    }
}

#[derive(Debug)]
struct PendingResult {
    ticket_digest: [u8; 32],
    bytes: Vec<u8>,
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
    pending_result: Option<PendingResult>,
}

pub struct AuthorityLedger {
    file: File,
    identity: LedgerIdentity,
    states: BTreeMap<[u8; 32], StoredTicketState>,
    next_sequence: u64,
    previous_hash: [u8; 32],
    pending_result: Option<PendingResult>,
    poisoned: bool,
}

impl fmt::Debug for AuthorityLedger {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AuthorityLedger")
            .field("ticket_count", &self.states.len())
            .field("next_sequence", &self.next_sequence)
            .field("recovery_required", &self.pending_result.is_some())
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
        let mut ledger = Self {
            file,
            identity,
            states: BTreeMap::new(),
            next_sequence: 0,
            previous_hash: ZERO_DIGEST,
            pending_result: None,
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
        let loaded = load_frames(&mut file, &identity)?;
        Ok(Self {
            file,
            identity,
            states: loaded.states,
            next_sequence: loaded.next_sequence,
            previous_hash: loaded.previous_hash,
            pending_result: loaded.pending_result,
            poisoned: false,
        })
    }

    pub fn issue(&mut self, ticket_digest: &str) -> Result<(), LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        self.ensure_writable()?;
        if self.states.contains_key(&ticket) {
            return Err(LedgerError::new("ticket_duplicate"));
        }
        self.append_frame_raw(Event::Issued, ticket, ZERO_DIGEST, &[])?;
        self.states.insert(ticket, StoredTicketState::Issued);
        Ok(())
    }

    pub fn consume(&mut self, ticket_digest: &str) -> Result<(), LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        self.ensure_writable()?;
        match self.states.get(&ticket) {
            None => return Err(LedgerError::new("ticket_unknown")),
            Some(StoredTicketState::Issued) => {}
            Some(_) => return Err(LedgerError::new("ticket_transition_invalid")),
        }
        self.append_frame_raw(Event::Consumed, ticket, ZERO_DIGEST, &[])?;
        self.states.insert(ticket, StoredTicketState::Consumed);
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
        match self.states.get(&ticket) {
            None => return Err(LedgerError::new("ticket_unknown")),
            Some(StoredTicketState::Consumed) => {}
            Some(_) => return Err(LedgerError::new("ticket_transition_invalid")),
        }

        let result_digest: [u8; 32] = Sha256::digest(result_bytes).into();
        for chunk in result_bytes.chunks(PAYLOAD_SIZE) {
            self.append_frame_raw(Event::ResultChunk, ticket, ZERO_DIGEST, chunk)?;
        }
        let length_payload = (result_bytes.len() as u64).to_be_bytes();
        self.append_frame_raw(Event::ResultCommit, ticket, result_digest, &length_payload)?;
        self.states.insert(
            ticket,
            StoredTicketState::Result {
                bytes: result_bytes.to_vec(),
                digest: result_digest,
            },
        );
        Ok(())
    }

    pub fn burn(&mut self, ticket_digest: &str) -> Result<(), LedgerError> {
        let ticket = decode_nonzero_digest(ticket_digest, "ticket_digest_invalid")?;
        self.ensure_writable()?;
        match self.states.get(&ticket) {
            None => return Err(LedgerError::new("ticket_unknown")),
            Some(StoredTicketState::Issued | StoredTicketState::Consumed) => {}
            Some(_) => return Err(LedgerError::new("ticket_transition_invalid")),
        }
        self.append_frame_raw(Event::Burned, ticket, ZERO_DIGEST, &[])?;
        self.states.insert(ticket, StoredTicketState::Burned);
        Ok(())
    }

    pub fn burn_active(&mut self) -> Result<usize, LedgerError> {
        self.ensure_not_poisoned()?;

        let pending_ticket = self
            .pending_result
            .as_ref()
            .map(|pending| pending.ticket_digest);
        let mut active = Vec::new();
        if let Some(ticket) = pending_ticket {
            active.push(ticket);
        }
        active.extend(self.states.iter().filter_map(|(ticket, state)| {
            (Some(*ticket) != pending_ticket
                && matches!(
                    state,
                    StoredTicketState::Issued | StoredTicketState::Consumed
                ))
            .then_some(*ticket)
        }));

        for ticket in &active {
            self.append_frame_raw(Event::Burned, *ticket, ZERO_DIGEST, &[])?;
            self.states.insert(*ticket, StoredTicketState::Burned);
            if Some(*ticket) == pending_ticket {
                self.pending_result = None;
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
        if self.file.write_all(&frame).is_err() || self.file.sync_all().is_err() {
            self.poisoned = true;
            return Err(LedgerError::new("ledger_append_failed"));
        }
        self.previous_hash.copy_from_slice(&frame[HASH_OFFSET..]);
        self.next_sequence = next_sequence;
        Ok(())
    }

    fn ensure_writable(&self) -> Result<(), LedgerError> {
        self.ensure_healthy()
    }

    fn ensure_healthy(&self) -> Result<(), LedgerError> {
        self.ensure_not_poisoned()?;
        if self.pending_result.is_some() {
            Err(LedgerError::new("ledger_recovery_required"))
        } else {
            Ok(())
        }
    }

    fn ensure_not_poisoned(&self) -> Result<(), LedgerError> {
        if self.poisoned {
            Err(LedgerError::new("ledger_poisoned"))
        } else {
            Ok(())
        }
    }
}

fn open_new_file(path: &Path) -> std::io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true).append(true).create_new(true);
    #[cfg(windows)]
    options.share_mode(0);
    options.open(path)
}

fn open_existing_file(path: &Path) -> std::io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true).append(true);
    #[cfg(windows)]
    options.share_mode(0);
    options.open(path)
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
    let mut pending_result = None;
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
            apply_loaded_event(&mut states, &mut pending_result, &frame)?;
        }
        previous_hash = frame.frame_hash;
    }
    Ok(LoadedLedger {
        states,
        next_sequence: count,
        previous_hash,
        pending_result,
    })
}

fn apply_loaded_event(
    states: &mut BTreeMap<[u8; 32], StoredTicketState>,
    pending_result: &mut Option<PendingResult>,
    frame: &DecodedFrame,
) -> Result<(), LedgerError> {
    if frame.event == Event::Initialize || frame.ticket_digest == ZERO_DIGEST {
        return Err(LedgerError::new("ledger_transition_invalid"));
    }
    if let Some(pending) = pending_result.as_ref() {
        if frame.ticket_digest != pending.ticket_digest
            || !matches!(
                frame.event,
                Event::ResultChunk | Event::ResultCommit | Event::Burned
            )
        {
            return Err(LedgerError::new("ledger_transition_invalid"));
        }
    }

    match frame.event {
        Event::Issued => {
            if frame.result_digest != ZERO_DIGEST
                || !frame.payload.is_empty()
                || states
                    .insert(frame.ticket_digest, StoredTicketState::Issued)
                    .is_some()
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
        }
        Event::Consumed => {
            if frame.result_digest != ZERO_DIGEST
                || !frame.payload.is_empty()
                || states.get(&frame.ticket_digest) != Some(&StoredTicketState::Issued)
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            states.insert(frame.ticket_digest, StoredTicketState::Consumed);
        }
        Event::ResultChunk => {
            if frame.result_digest != ZERO_DIGEST
                || frame.payload.is_empty()
                || states.get(&frame.ticket_digest) != Some(&StoredTicketState::Consumed)
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            match pending_result {
                Some(pending) => {
                    if pending.bytes.len() + frame.payload.len() > MAX_RESULT_SIZE {
                        return Err(LedgerError::new("ledger_result_too_large"));
                    }
                    pending.bytes.extend_from_slice(&frame.payload);
                }
                None => {
                    *pending_result = Some(PendingResult {
                        ticket_digest: frame.ticket_digest,
                        bytes: frame.payload.clone(),
                    });
                }
            }
        }
        Event::ResultCommit => {
            if frame.result_digest == ZERO_DIGEST
                || frame.payload.len() != 8
                || states.get(&frame.ticket_digest) != Some(&StoredTicketState::Consumed)
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            let pending = pending_result
                .take()
                .ok_or_else(|| LedgerError::new("ledger_transition_invalid"))?;
            if pending.ticket_digest != frame.ticket_digest {
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
                    bytes: pending.bytes,
                    digest: frame.result_digest,
                },
            );
        }
        Event::Burned => {
            if frame.result_digest != ZERO_DIGEST
                || !frame.payload.is_empty()
                || !matches!(
                    states.get(&frame.ticket_digest),
                    Some(StoredTicketState::Issued | StoredTicketState::Consumed)
                )
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            if pending_result
                .as_ref()
                .is_some_and(|pending| pending.ticket_digest != frame.ticket_digest)
            {
                return Err(LedgerError::new("ledger_transition_invalid"));
            }
            *pending_result = None;
            states.insert(frame.ticket_digest, StoredTicketState::Burned);
        }
        Event::Initialize => return Err(LedgerError::new("ledger_transition_invalid")),
    }
    Ok(())
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
mod tests {
    use super::*;
    use std::{
        fs::{self, OpenOptions},
        io::{Read, Seek, SeekFrom, Write},
        path::PathBuf,
        process,
        time::{SystemTime, UNIX_EPOCH},
    };

    fn digest(byte: u8) -> String {
        format!("{byte:02x}").repeat(32)
    }

    fn identity(generation: u8, signer: u8) -> LedgerIdentity {
        LedgerIdentity::from_hex(&digest(generation), &digest(signer)).unwrap()
    }

    fn temp_path(label: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "vrcforge-authority-ledger-{label}-{}-{nonce}.bin",
            process::id()
        ))
    }

    fn remove(path: &PathBuf) {
        let _ = fs::remove_file(path);
    }

    fn ledger_bytes(ledger: &mut AuthorityLedger) -> Vec<u8> {
        ledger.file.seek(SeekFrom::Start(0)).unwrap();
        let mut bytes = Vec::new();
        ledger.file.read_to_end(&mut bytes).unwrap();
        ledger.file.seek(SeekFrom::End(0)).unwrap();
        bytes
    }

    fn append_frame_bytes(path: &PathBuf, frame: &[u8; FRAME_SIZE]) {
        let mut file = OpenOptions::new().append(true).open(path).unwrap();
        file.write_all(frame).unwrap();
        file.sync_all().unwrap();
    }

    #[test]
    fn provisioning_is_explicit_and_runtime_open_never_rebuilds() {
        let path = temp_path("provisioning");
        let ledger_identity = identity(0x10, 0x20);
        assert_eq!(
            AuthorityLedger::open_existing(&path, ledger_identity.clone())
                .unwrap_err()
                .code(),
            "ledger_missing"
        );
        drop(AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap());
        assert_eq!(
            AuthorityLedger::provision_new(&path, ledger_identity.clone())
                .unwrap_err()
                .code(),
            "ledger_already_exists"
        );
        AuthorityLedger::open_existing(&path, ledger_identity).unwrap();
        remove(&path);
    }

    #[test]
    fn issue_consume_result_bytes_persist_and_replay_exactly() {
        let path = temp_path("lifecycle");
        let ticket = digest(0x31);
        let result = b"{\"ok\":false,\"code\":\"fixed-replay-response\",\"n\":17}".to_vec();
        let expected_digest: [u8; 32] = Sha256::digest(&result).into();
        {
            let mut ledger = AuthorityLedger::provision_new(&path, identity(0x11, 0x21)).unwrap();
            ledger.issue(&ticket).unwrap();
            let issued_prefix = ledger_bytes(&mut ledger);
            assert_eq!(issued_prefix.len(), FRAME_SIZE * 2);
            assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Issued));
            ledger.consume(&ticket).unwrap();
            let consumed_prefix = ledger_bytes(&mut ledger);
            assert_eq!(consumed_prefix.len(), FRAME_SIZE * 3);
            assert!(consumed_prefix.starts_with(&issued_prefix));
            ledger.record_result_bytes(&ticket, &result).unwrap();
            let committed = ledger_bytes(&mut ledger);
            let result_frames = result.len().div_ceil(PAYLOAD_SIZE) + 1;
            assert_eq!(committed.len(), FRAME_SIZE * (3 + result_frames));
            assert!(committed.starts_with(&consumed_prefix));
            assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Result));
            assert_eq!(ledger.result_bytes(&ticket).unwrap(), Some(result.clone()));
            assert_eq!(
                ledger.result_digest(&ticket).unwrap(),
                Some(hex_encode(&expected_digest))
            );
        }
        {
            let ledger = AuthorityLedger::open_existing(&path, identity(0x11, 0x21)).unwrap();
            assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Result));
            assert_eq!(ledger.result_bytes(&ticket).unwrap(), Some(result));
            assert_eq!(
                ledger.result_digest(&ticket).unwrap(),
                Some(hex_encode(&expected_digest))
            );
        }
        remove(&path);
    }

    #[test]
    fn duplicates_illegal_transitions_and_invalid_results_are_rejected() {
        let path = temp_path("transitions");
        let ticket = digest(0x32);
        let unknown = digest(0x33);
        let mut ledger = AuthorityLedger::provision_new(&path, identity(0x12, 0x22)).unwrap();

        assert_eq!(
            ledger.consume(&unknown).unwrap_err().code(),
            "ticket_unknown"
        );
        assert_eq!(
            ledger
                .record_result_bytes(&unknown, b"result")
                .unwrap_err()
                .code(),
            "ticket_unknown"
        );
        ledger.issue(&ticket).unwrap();
        assert_eq!(
            ledger.issue(&ticket).unwrap_err().code(),
            "ticket_duplicate"
        );
        assert_eq!(
            ledger
                .record_result_bytes(&ticket, b"result")
                .unwrap_err()
                .code(),
            "ticket_transition_invalid"
        );
        ledger.consume(&ticket).unwrap();
        assert_eq!(
            ledger.consume(&ticket).unwrap_err().code(),
            "ticket_transition_invalid"
        );
        assert_eq!(
            ledger.record_result_bytes(&ticket, b"").unwrap_err().code(),
            "result_bytes_invalid"
        );
        assert_eq!(
            ledger
                .record_result_bytes(&ticket, &vec![0; MAX_RESULT_SIZE + 1])
                .unwrap_err()
                .code(),
            "result_too_large"
        );
        ledger.record_result_bytes(&ticket, b"result").unwrap();
        assert_eq!(
            ledger
                .record_result_bytes(&ticket, b"result")
                .unwrap_err()
                .code(),
            "ticket_transition_invalid"
        );
        assert_eq!(
            ledger.burn(&ticket).unwrap_err().code(),
            "ticket_transition_invalid"
        );
        drop(ledger);
        remove(&path);
    }

    #[test]
    fn explicit_startup_burn_consumes_all_active_tickets() {
        let path = temp_path("burn-active");
        let issued = digest(0x34);
        let consumed = digest(0x35);
        let complete = digest(0x36);
        {
            let mut ledger = AuthorityLedger::provision_new(&path, identity(0x13, 0x23)).unwrap();
            ledger.issue(&issued).unwrap();
            ledger.issue(&consumed).unwrap();
            ledger.consume(&consumed).unwrap();
            ledger.issue(&complete).unwrap();
            ledger.consume(&complete).unwrap();
            ledger.record_result_bytes(&complete, b"complete").unwrap();
        }
        {
            let mut ledger = AuthorityLedger::open_existing(&path, identity(0x13, 0x23)).unwrap();
            assert_eq!(ledger.burn_active().unwrap(), 2);
            assert_eq!(ledger.state(&issued).unwrap(), Some(TicketState::Burned));
            assert_eq!(ledger.state(&consumed).unwrap(), Some(TicketState::Burned));
            assert_eq!(ledger.state(&complete).unwrap(), Some(TicketState::Result));
            assert_eq!(ledger.burn_active().unwrap(), 0);
        }
        remove(&path);
    }

    #[test]
    fn incomplete_result_is_blocked_until_explicit_startup_burn() {
        let path = temp_path("partial-result");
        let ledger_identity = identity(0x14, 0x24);
        let ticket = digest(0x37);
        let other = digest(0x38);
        let (sequence, previous_hash) = {
            let mut ledger =
                AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
            ledger.issue(&other).unwrap();
            ledger.issue(&ticket).unwrap();
            ledger.consume(&ticket).unwrap();
            (ledger.next_sequence, ledger.previous_hash)
        };
        append_frame_bytes(
            &path,
            &encode_frame(
                Event::ResultChunk,
                sequence,
                &ledger_identity,
                decode_digest(&ticket, "test").unwrap(),
                ZERO_DIGEST,
                previous_hash,
                b"durable-but-uncommitted",
            )
            .unwrap(),
        );

        let mut ledger = AuthorityLedger::open_existing(&path, ledger_identity.clone()).unwrap();
        assert_eq!(
            ledger.state(&ticket).unwrap_err().code(),
            "ledger_recovery_required"
        );
        assert_eq!(
            ledger.issue(&digest(0x39)).unwrap_err().code(),
            "ledger_recovery_required"
        );
        assert_eq!(
            ledger
                .record_result_bytes(&ticket, b"replacement")
                .unwrap_err()
                .code(),
            "ledger_recovery_required"
        );
        assert_eq!(ledger.burn_active().unwrap(), 2);
        assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Burned));
        assert_eq!(ledger.state(&other).unwrap(), Some(TicketState::Burned));
        drop(ledger);

        let ledger = AuthorityLedger::open_existing(&path, ledger_identity).unwrap();
        assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Burned));
        assert_eq!(ledger.state(&other).unwrap(), Some(TicketState::Burned));
        remove(&path);
    }

    #[test]
    fn torn_corrupt_and_identity_mismatched_ledgers_fail_closed() {
        let torn_path = temp_path("torn");
        {
            drop(AuthorityLedger::provision_new(&torn_path, identity(0x15, 0x25)).unwrap());
            let mut file = OpenOptions::new().append(true).open(&torn_path).unwrap();
            file.write_all(b"torn").unwrap();
            file.sync_all().unwrap();
        }
        assert_eq!(
            AuthorityLedger::open_existing(&torn_path, identity(0x15, 0x25))
                .unwrap_err()
                .code(),
            "ledger_torn_tail"
        );
        remove(&torn_path);

        let corrupt_path = temp_path("corrupt");
        {
            let mut ledger =
                AuthorityLedger::provision_new(&corrupt_path, identity(0x16, 0x26)).unwrap();
            ledger.issue(&digest(0x3a)).unwrap();
        }
        {
            let mut file = OpenOptions::new().write(true).open(&corrupt_path).unwrap();
            file.seek(SeekFrom::Start((FRAME_SIZE + 48) as u64))
                .unwrap();
            file.write_all(&[0x7f]).unwrap();
            file.sync_all().unwrap();
        }
        assert_eq!(
            AuthorityLedger::open_existing(&corrupt_path, identity(0x16, 0x26))
                .unwrap_err()
                .code(),
            "ledger_hash_mismatch"
        );
        remove(&corrupt_path);

        let mismatch_path = temp_path("identity");
        drop(AuthorityLedger::provision_new(&mismatch_path, identity(0x17, 0x27)).unwrap());
        assert_eq!(
            AuthorityLedger::open_existing(&mismatch_path, identity(0x18, 0x27))
                .unwrap_err()
                .code(),
            "ledger_identity_mismatch"
        );
        remove(&mismatch_path);
    }

    #[test]
    fn validly_hashed_bad_chain_sequence_and_transition_are_rejected() {
        let chain_path = temp_path("chain");
        let chain_identity = identity(0x19, 0x29);
        drop(AuthorityLedger::provision_new(&chain_path, chain_identity.clone()).unwrap());
        append_frame_bytes(
            &chain_path,
            &encode_frame(
                Event::Issued,
                1,
                &chain_identity,
                decode_digest(&digest(0x3b), "test").unwrap(),
                ZERO_DIGEST,
                [0x55; 32],
                &[],
            )
            .unwrap(),
        );
        assert_eq!(
            AuthorityLedger::open_existing(&chain_path, chain_identity)
                .unwrap_err()
                .code(),
            "ledger_chain_mismatch"
        );
        remove(&chain_path);

        let sequence_path = temp_path("sequence");
        let sequence_identity = identity(0x1a, 0x2a);
        let previous_hash = {
            let ledger =
                AuthorityLedger::provision_new(&sequence_path, sequence_identity.clone()).unwrap();
            ledger.previous_hash
        };
        append_frame_bytes(
            &sequence_path,
            &encode_frame(
                Event::Issued,
                2,
                &sequence_identity,
                decode_digest(&digest(0x3c), "test").unwrap(),
                ZERO_DIGEST,
                previous_hash,
                &[],
            )
            .unwrap(),
        );
        assert_eq!(
            AuthorityLedger::open_existing(&sequence_path, sequence_identity)
                .unwrap_err()
                .code(),
            "ledger_sequence_invalid"
        );
        remove(&sequence_path);

        let transition_path = temp_path("loaded-transition");
        let transition_identity = identity(0x1b, 0x2b);
        let previous_hash = {
            let ledger =
                AuthorityLedger::provision_new(&transition_path, transition_identity.clone())
                    .unwrap();
            ledger.previous_hash
        };
        append_frame_bytes(
            &transition_path,
            &encode_frame(
                Event::ResultCommit,
                1,
                &transition_identity,
                decode_digest(&digest(0x3d), "test").unwrap(),
                decode_digest(&digest(0x4d), "test").unwrap(),
                previous_hash,
                &8u64.to_be_bytes(),
            )
            .unwrap(),
        );
        assert_eq!(
            AuthorityLedger::open_existing(&transition_path, transition_identity)
                .unwrap_err()
                .code(),
            "ledger_transition_invalid"
        );
        remove(&transition_path);
    }

    #[test]
    fn result_commit_must_match_exact_length_and_digest() {
        let path = temp_path("bad-result-commit");
        let ledger_identity = identity(0x1c, 0x2c);
        let ticket = digest(0x3e);
        let ticket_digest = decode_digest(&ticket, "test").unwrap();
        let (sequence, previous_hash) = {
            let mut ledger =
                AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
            ledger.issue(&ticket).unwrap();
            ledger.consume(&ticket).unwrap();
            (ledger.next_sequence, ledger.previous_hash)
        };
        let chunk = encode_frame(
            Event::ResultChunk,
            sequence,
            &ledger_identity,
            ticket_digest,
            ZERO_DIGEST,
            previous_hash,
            b"exact bytes",
        )
        .unwrap();
        let chunk_hash = chunk[HASH_OFFSET..].try_into().unwrap();
        append_frame_bytes(&path, &chunk);
        let commit = encode_frame(
            Event::ResultCommit,
            sequence + 1,
            &ledger_identity,
            ticket_digest,
            Sha256::digest(b"different bytes").into(),
            chunk_hash,
            &11u64.to_be_bytes(),
        )
        .unwrap();
        append_frame_bytes(&path, &commit);
        assert_eq!(
            AuthorityLedger::open_existing(&path, ledger_identity)
                .unwrap_err()
                .code(),
            "ledger_result_invalid"
        );
        remove(&path);
    }

    #[cfg(windows)]
    #[test]
    fn windows_open_is_exclusive() {
        let path = temp_path("exclusive");
        let ledger_identity = identity(0x1d, 0x2d);
        let ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
        assert_eq!(
            AuthorityLedger::open_existing(&path, ledger_identity.clone())
                .unwrap_err()
                .code(),
            "ledger_open_failed"
        );
        drop(ledger);
        AuthorityLedger::open_existing(&path, ledger_identity).unwrap();
        remove(&path);
    }
}
