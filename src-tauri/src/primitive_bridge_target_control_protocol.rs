use sha2::{Digest as _, Sha256};
use std::fmt;

pub type Digest = [u8; 32];

pub const PROTOCOL_VERSION: u16 = 1;
pub const SHUTDOWN_MAGIC: [u8; 8] = *b"VRCBSD01";
pub const ACCOUNTING_MAGIC: [u8; 8] = *b"VRCBAC01";
pub const SHUTDOWN_DOMAIN: &[u8] = b"vrcforge-authority-bridge-target-shutdown-request-v1\0";
pub const ACCOUNTING_DOMAIN: &[u8] = b"vrcforge-authority-bridge-target-shutdown-accounting-v1\0";
pub const HEADER_BYTES: usize = 14;
pub const SHUTDOWN_PAYLOAD_BYTES: usize = 182;
pub const ACCOUNTING_PAYLOAD_BYTES: usize = 318;
pub const SHUTDOWN_FRAME_BYTES: usize = HEADER_BYTES + SHUTDOWN_PAYLOAD_BYTES;
pub const ACCOUNTING_FRAME_BYTES: usize = HEADER_BYTES + ACCOUNTING_PAYLOAD_BYTES;

pub const SHUTDOWN_FLAG_GRACEFUL: u16 = 1 << 0;
pub const SHUTDOWN_FLAG_ACCOUNTING_REQUIRED: u16 = 1 << 1;
pub const SHUTDOWN_FLAG_CLOSE_AFTER_ACCOUNTING: u16 = 1 << 2;
pub const SHUTDOWN_REQUIRED_FLAGS: u16 = SHUTDOWN_FLAG_GRACEFUL
    | SHUTDOWN_FLAG_ACCOUNTING_REQUIRED
    | SHUTDOWN_FLAG_CLOSE_AFTER_ACCOUNTING;

pub const ACCOUNTING_FLAG_RUNNER_STOPPED: u16 = 1 << 0;
pub const ACCOUNTING_FLAG_REQUEST_AUTH_HEADER_STRIPPED: u16 = 1 << 1;
pub const ACCOUNTING_FLAG_CREDENTIALS_ZEROIZED: u16 = 1 << 2;
pub const ACCOUNTING_FLAG_FINAL_SNAPSHOT: u16 = 1 << 3;
pub const ACCOUNTING_REQUIRED_FLAGS: u16 = ACCOUNTING_FLAG_RUNNER_STOPPED
    | ACCOUNTING_FLAG_REQUEST_AUTH_HEADER_STRIPPED
    | ACCOUNTING_FLAG_CREDENTIALS_ZEROIZED
    | ACCOUNTING_FLAG_FINAL_SNAPSHOT;

const MIN_PRIVATE_TARGET_PORT: u16 = 1_024;
const PUBLIC_BRIDGE_PORT: u16 = 8_080;
const APP_PORT: u16 = 8_757;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ProtocolError(&'static str);

impl ProtocolError {
    pub const fn new(code: &'static str) -> Self {
        Self(code)
    }

    pub const fn code(self) -> &'static str {
        self.0
    }
}

impl fmt::Display for ProtocolError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for ProtocolError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ControlBinding {
    pub run_binding_digest: Digest,
    pub ticket_digest: Digest,
    pub bridge_launch_binding_digest: Digest,
    pub private_pipe_binding_digest: Digest,
    pub private_pipe_instance_id: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ShutdownRequest {
    pub binding: ControlBinding,
    pub sequence: u32,
    pub requested_at: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RequestAccounting {
    pub controlled_health_requests: u32,
    pub proxy_http_requests: u32,
    pub proxy_websocket_requests: u32,
    pub rejected_requests: u32,
    pub bypass_requests: u32,
    pub credentials_zeroized: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ProcessIdentity {
    pub pid: u32,
    pub creation_time: u64,
    pub executable_digest: Digest,
    pub image_identity_digest: Digest,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ShutdownAccounting {
    pub binding: ControlBinding,
    pub target_port: u16,
    pub listener_socket_object_id: u64,
    pub request_auth_key_digest: Digest,
    pub request_auth: RequestAccounting,
    pub observed_at_shutdown: u64,
    pub owner: ProcessIdentity,
    pub request_auth_header_stripped: bool,
}

#[derive(Debug, Default)]
pub struct ShutdownReplayGuard {
    attempted: bool,
    request_digest: Option<Digest>,
}

impl ShutdownReplayGuard {
    pub fn consumed(&self) -> bool {
        self.request_digest.is_some()
    }

    pub fn request_digest(&self) -> Option<Digest> {
        self.request_digest
    }

    pub fn consume(
        &mut self,
        bytes: &[u8],
        expected: ControlBinding,
    ) -> Result<ShutdownRequest, ProtocolError> {
        if self.attempted {
            return Err(ProtocolError::new("bridge_target_shutdown_replayed"));
        }
        self.attempted = true;
        let request = decode_shutdown_request(bytes)?;
        if request.binding != expected {
            return Err(ProtocolError::new("bridge_target_shutdown_binding_drifted"));
        }
        self.request_digest = Some(Sha256::digest(bytes).into());
        Ok(request)
    }
}

#[derive(Debug)]
pub struct AccountingEofDecoder {
    request: ShutdownRequest,
    bytes: Vec<u8>,
    finished: bool,
}

impl AccountingEofDecoder {
    pub fn new(request: ShutdownRequest) -> Self {
        Self {
            request,
            bytes: Vec::with_capacity(ACCOUNTING_FRAME_BYTES),
            finished: false,
        }
    }

    pub fn feed(&mut self, bytes: &[u8]) -> Result<(), ProtocolError> {
        if self.finished {
            return Err(ProtocolError::new("bridge_target_accounting_replayed"));
        }
        if bytes.is_empty() {
            return Err(ProtocolError::new("bridge_target_accounting_chunk_invalid"));
        }
        if self.bytes.len().saturating_add(bytes.len()) > ACCOUNTING_FRAME_BYTES {
            self.finished = true;
            self.clear();
            return Err(ProtocolError::new("bridge_target_accounting_oversized"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(())
    }

    pub fn finish_eof(&mut self) -> Result<ShutdownAccounting, ProtocolError> {
        if self.finished {
            return Err(ProtocolError::new("bridge_target_accounting_replayed"));
        }
        self.finished = true;
        let result = if self.bytes.len() == ACCOUNTING_FRAME_BYTES {
            decode_shutdown_accounting(&self.bytes, Some(self.request))
        } else {
            Err(ProtocolError::new("bridge_target_accounting_truncated"))
        };
        self.clear();
        result
    }

    fn clear(&mut self) {
        self.bytes.fill(0);
        self.bytes.clear();
    }
}

pub fn encode_shutdown_request(request: ShutdownRequest) -> Result<Vec<u8>, ProtocolError> {
    validate_binding(request.binding)?;
    if request.sequence != 1 || request.requested_at == 0 {
        return Err(ProtocolError::new("bridge_target_shutdown_invalid"));
    }
    let mut bytes = header(SHUTDOWN_MAGIC, SHUTDOWN_PAYLOAD_BYTES);
    push_binding(&mut bytes, request.binding);
    bytes.extend_from_slice(&request.sequence.to_be_bytes());
    bytes.extend_from_slice(&request.requested_at.to_be_bytes());
    bytes.extend_from_slice(&SHUTDOWN_REQUIRED_FLAGS.to_be_bytes());
    append_digest(&mut bytes, SHUTDOWN_DOMAIN);
    if bytes.len() != SHUTDOWN_FRAME_BYTES {
        return Err(ProtocolError::new("bridge_target_shutdown_layout_drifted"));
    }
    Ok(bytes)
}

pub fn decode_shutdown_request(bytes: &[u8]) -> Result<ShutdownRequest, ProtocolError> {
    validate_frame(
        bytes,
        SHUTDOWN_MAGIC,
        SHUTDOWN_PAYLOAD_BYTES,
        SHUTDOWN_DOMAIN,
        "bridge_target_shutdown",
    )?;
    let mut reader = Reader::new(&bytes[HEADER_BYTES..bytes.len() - 32]);
    let request = ShutdownRequest {
        binding: take_binding(&mut reader)?,
        sequence: reader.u32()?,
        requested_at: reader.u64()?,
    };
    let flags = reader.u16()?;
    if !reader.done() || flags != SHUTDOWN_REQUIRED_FLAGS {
        return Err(ProtocolError::new("bridge_target_shutdown_invalid"));
    }
    validate_binding(request.binding)?;
    if request.sequence != 1 || request.requested_at == 0 {
        return Err(ProtocolError::new("bridge_target_shutdown_invalid"));
    }
    Ok(request)
}

pub fn encode_shutdown_accounting(
    accounting: ShutdownAccounting,
) -> Result<Vec<u8>, ProtocolError> {
    validate_accounting(accounting)?;
    let mut bytes = header(ACCOUNTING_MAGIC, ACCOUNTING_PAYLOAD_BYTES);
    push_binding(&mut bytes, accounting.binding);
    bytes.extend_from_slice(&accounting.target_port.to_be_bytes());
    bytes.extend_from_slice(&accounting.listener_socket_object_id.to_be_bytes());
    bytes.extend_from_slice(&accounting.request_auth_key_digest);
    for count in [
        accounting.request_auth.controlled_health_requests,
        accounting.request_auth.proxy_http_requests,
        accounting.request_auth.proxy_websocket_requests,
        accounting.request_auth.rejected_requests,
        accounting.request_auth.bypass_requests,
    ] {
        bytes.extend_from_slice(&count.to_be_bytes());
    }
    bytes.push(u8::from(accounting.request_auth.credentials_zeroized));
    bytes.push(u8::from(accounting.request_auth_header_stripped));
    bytes.extend_from_slice(&accounting.observed_at_shutdown.to_be_bytes());
    bytes.extend_from_slice(&accounting.owner.pid.to_be_bytes());
    bytes.extend_from_slice(&accounting.owner.creation_time.to_be_bytes());
    bytes.extend_from_slice(&accounting.owner.executable_digest);
    bytes.extend_from_slice(&accounting.owner.image_identity_digest);
    bytes.extend_from_slice(&ACCOUNTING_REQUIRED_FLAGS.to_be_bytes());
    append_digest(&mut bytes, ACCOUNTING_DOMAIN);
    if bytes.len() != ACCOUNTING_FRAME_BYTES {
        return Err(ProtocolError::new(
            "bridge_target_accounting_layout_drifted",
        ));
    }
    Ok(bytes)
}

pub fn decode_shutdown_accounting(
    bytes: &[u8],
    expected: Option<ShutdownRequest>,
) -> Result<ShutdownAccounting, ProtocolError> {
    validate_frame(
        bytes,
        ACCOUNTING_MAGIC,
        ACCOUNTING_PAYLOAD_BYTES,
        ACCOUNTING_DOMAIN,
        "bridge_target_accounting",
    )?;
    let mut reader = Reader::new(&bytes[HEADER_BYTES..bytes.len() - 32]);
    let binding = take_binding(&mut reader)?;
    let target_port = reader.u16()?;
    let listener_socket_object_id = reader.u64()?;
    let request_auth_key_digest = reader.digest()?;
    let request_auth = RequestAccounting {
        controlled_health_requests: reader.u32()?,
        proxy_http_requests: reader.u32()?,
        proxy_websocket_requests: reader.u32()?,
        rejected_requests: reader.u32()?,
        bypass_requests: reader.u32()?,
        credentials_zeroized: reader.boolean()?,
    };
    let request_auth_header_stripped = reader.boolean()?;
    let observed_at_shutdown = reader.u64()?;
    let owner = ProcessIdentity {
        pid: reader.u32()?,
        creation_time: reader.u64()?,
        executable_digest: reader.digest()?,
        image_identity_digest: reader.digest()?,
    };
    let flags = reader.u16()?;
    if !reader.done() || flags != ACCOUNTING_REQUIRED_FLAGS {
        return Err(ProtocolError::new("bridge_target_accounting_invalid"));
    }
    let accounting = ShutdownAccounting {
        binding,
        target_port,
        listener_socket_object_id,
        request_auth_key_digest,
        request_auth,
        observed_at_shutdown,
        owner,
        request_auth_header_stripped,
    };
    validate_accounting(accounting)?;
    if expected.is_some_and(|request| request.binding != accounting.binding) {
        return Err(ProtocolError::new(
            "bridge_target_accounting_binding_drifted",
        ));
    }
    Ok(accounting)
}

fn validate_binding(binding: ControlBinding) -> Result<(), ProtocolError> {
    if [
        binding.run_binding_digest,
        binding.ticket_digest,
        binding.bridge_launch_binding_digest,
        binding.private_pipe_binding_digest,
    ]
    .iter()
    .any(|digest| !valid_digest(digest))
        || binding.private_pipe_instance_id == 0
    {
        return Err(ProtocolError::new("bridge_target_binding_invalid"));
    }
    Ok(())
}

fn validate_accounting(accounting: ShutdownAccounting) -> Result<(), ProtocolError> {
    validate_binding(accounting.binding)?;
    let counts = [
        accounting.request_auth.controlled_health_requests,
        accounting.request_auth.proxy_http_requests,
        accounting.request_auth.proxy_websocket_requests,
        accounting.request_auth.rejected_requests,
    ];
    let total = counts
        .into_iter()
        .try_fold(0_u32, u32::checked_add)
        .ok_or_else(|| ProtocolError::new("bridge_target_accounting_invalid"))?;
    if accounting.target_port < MIN_PRIVATE_TARGET_PORT
        || matches!(accounting.target_port, PUBLIC_BRIDGE_PORT | APP_PORT)
        || accounting.listener_socket_object_id == 0
        || !valid_digest(&accounting.request_auth_key_digest)
        || accounting.request_auth.controlled_health_requests != 1
        || total < accounting.request_auth.controlled_health_requests
        || !accounting.request_auth.credentials_zeroized
        || !accounting.request_auth_header_stripped
        || accounting.observed_at_shutdown == 0
        || accounting.owner.pid == 0
        || accounting.owner.creation_time == 0
        || !valid_digest(&accounting.owner.executable_digest)
        || !valid_digest(&accounting.owner.image_identity_digest)
    {
        return Err(ProtocolError::new("bridge_target_accounting_invalid"));
    }
    Ok(())
}

fn valid_digest(digest: &Digest) -> bool {
    digest.iter().any(|byte| *byte != 0)
}

fn header(magic: [u8; 8], payload_bytes: usize) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(HEADER_BYTES + payload_bytes);
    bytes.extend_from_slice(&magic);
    bytes.extend_from_slice(&PROTOCOL_VERSION.to_be_bytes());
    bytes.extend_from_slice(&(payload_bytes as u32).to_be_bytes());
    bytes
}

fn push_binding(bytes: &mut Vec<u8>, binding: ControlBinding) {
    bytes.extend_from_slice(&binding.run_binding_digest);
    bytes.extend_from_slice(&binding.ticket_digest);
    bytes.extend_from_slice(&binding.bridge_launch_binding_digest);
    bytes.extend_from_slice(&binding.private_pipe_binding_digest);
    bytes.extend_from_slice(&binding.private_pipe_instance_id.to_be_bytes());
}

fn append_digest(bytes: &mut Vec<u8>, domain: &[u8]) {
    let mut hasher = Sha256::new();
    hasher.update(domain);
    hasher.update(&*bytes);
    bytes.extend_from_slice(&hasher.finalize());
}

fn validate_frame(
    bytes: &[u8],
    magic: [u8; 8],
    payload_bytes: usize,
    domain: &[u8],
    code: &'static str,
) -> Result<(), ProtocolError> {
    let expected_size = HEADER_BYTES + payload_bytes;
    if bytes.len() < expected_size {
        return Err(ProtocolError::new(match code {
            "bridge_target_shutdown" => "bridge_target_shutdown_truncated",
            _ => "bridge_target_accounting_truncated",
        }));
    }
    if bytes.len() > expected_size {
        return Err(ProtocolError::new(match code {
            "bridge_target_shutdown" => "bridge_target_shutdown_oversized",
            _ => "bridge_target_accounting_oversized",
        }));
    }
    let version = u16::from_be_bytes([bytes[8], bytes[9]]);
    let encoded_payload = u32::from_be_bytes(bytes[10..14].try_into().expect("fixed header"));
    let mut hasher = Sha256::new();
    hasher.update(domain);
    hasher.update(&bytes[..bytes.len() - 32]);
    if bytes[..8] != magic
        || version != PROTOCOL_VERSION
        || encoded_payload != payload_bytes as u32
        || hasher.finalize().as_slice() != &bytes[bytes.len() - 32..]
    {
        return Err(ProtocolError::new(match code {
            "bridge_target_shutdown" => "bridge_target_shutdown_invalid",
            _ => "bridge_target_accounting_invalid",
        }));
    }
    Ok(())
}

fn take_binding(reader: &mut Reader<'_>) -> Result<ControlBinding, ProtocolError> {
    Ok(ControlBinding {
        run_binding_digest: reader.digest()?,
        ticket_digest: reader.digest()?,
        bridge_launch_binding_digest: reader.digest()?,
        private_pipe_binding_digest: reader.digest()?,
        private_pipe_instance_id: reader.u64()?,
    })
}

struct Reader<'a> {
    bytes: &'a [u8],
    offset: usize,
}

impl<'a> Reader<'a> {
    fn new(bytes: &'a [u8]) -> Self {
        Self { bytes, offset: 0 }
    }

    fn take<const N: usize>(&mut self) -> Result<[u8; N], ProtocolError> {
        let end = self.offset.saturating_add(N);
        let value = self
            .bytes
            .get(self.offset..end)
            .ok_or_else(|| ProtocolError::new("bridge_target_protocol_truncated"))?;
        self.offset = end;
        Ok(value.try_into().expect("fixed slice length"))
    }

    fn digest(&mut self) -> Result<Digest, ProtocolError> {
        let digest = self.take::<32>()?;
        if !valid_digest(&digest) {
            return Err(ProtocolError::new("bridge_target_binding_invalid"));
        }
        Ok(digest)
    }

    fn u16(&mut self) -> Result<u16, ProtocolError> {
        Ok(u16::from_be_bytes(self.take()?))
    }

    fn u32(&mut self) -> Result<u32, ProtocolError> {
        Ok(u32::from_be_bytes(self.take()?))
    }

    fn u64(&mut self) -> Result<u64, ProtocolError> {
        Ok(u64::from_be_bytes(self.take()?))
    }

    fn boolean(&mut self) -> Result<bool, ProtocolError> {
        match self.take::<1>()?[0] {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err(ProtocolError::new("bridge_target_protocol_noncanonical")),
        }
    }

    fn done(&self) -> bool {
        self.offset == self.bytes.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn digest(value: u8) -> Digest {
        [value; 32]
    }

    fn binding() -> ControlBinding {
        ControlBinding {
            run_binding_digest: digest(1),
            ticket_digest: digest(2),
            bridge_launch_binding_digest: digest(3),
            private_pipe_binding_digest: digest(4),
            private_pipe_instance_id: 0x0102_0304_0506_0708,
        }
    }

    fn shutdown() -> ShutdownRequest {
        ShutdownRequest {
            binding: binding(),
            sequence: 1,
            requested_at: 0x1112_1314_1516_1718,
        }
    }

    fn accounting() -> ShutdownAccounting {
        ShutdownAccounting {
            binding: binding(),
            target_port: 49_221,
            listener_socket_object_id: 99,
            request_auth_key_digest: digest(8),
            request_auth: RequestAccounting {
                controlled_health_requests: 1,
                proxy_http_requests: 2,
                proxy_websocket_requests: 3,
                rejected_requests: 0,
                bypass_requests: 0,
                credentials_zeroized: true,
            },
            observed_at_shutdown: 0x2122_2324_2526_2728,
            owner: ProcessIdentity {
                pid: 4_242,
                creation_time: 9_999,
                executable_digest: digest(6),
                image_identity_digest: digest(7),
            },
            request_auth_header_stripped: true,
        }
    }

    fn replace_digest(bytes: &mut [u8], domain: &[u8]) {
        let digest_offset = bytes.len() - 32;
        let mut hasher = Sha256::new();
        hasher.update(domain);
        hasher.update(&bytes[..digest_offset]);
        bytes[digest_offset..].copy_from_slice(&hasher.finalize());
    }

    fn sha256_hex(bytes: &[u8]) -> String {
        Sha256::digest(bytes)
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect()
    }

    #[test]
    fn exact_wire_layouts_round_trip() {
        let shutdown_bytes = encode_shutdown_request(shutdown()).unwrap();
        assert_eq!(shutdown_bytes.len(), 196);
        assert_eq!(&shutdown_bytes[..8], b"VRCBSD01");
        assert_eq!(
            sha256_hex(&shutdown_bytes),
            "50bb2ea74b8e7f778fe18c12c284e5bd7f1ae5e55187e19499795e885eb16775"
        );
        assert_eq!(
            decode_shutdown_request(&shutdown_bytes).unwrap(),
            shutdown()
        );

        let accounting_bytes = encode_shutdown_accounting(accounting()).unwrap();
        assert_eq!(accounting_bytes.len(), 332);
        assert_eq!(&accounting_bytes[..8], b"VRCBAC01");
        assert_eq!(
            sha256_hex(&accounting_bytes),
            "fc090b81cf5911096ad158f4a525953e901e33b145c749fb724e4dc488e0b8b7"
        );
        assert_eq!(
            decode_shutdown_accounting(&accounting_bytes, Some(shutdown())).unwrap(),
            accounting()
        );
    }

    #[test]
    fn shutdown_rejects_truncation_oversize_replay_and_binding_drift() {
        let bytes = encode_shutdown_request(shutdown()).unwrap();
        assert_eq!(
            decode_shutdown_request(&bytes[..bytes.len() - 1])
                .unwrap_err()
                .code(),
            "bridge_target_shutdown_truncated"
        );
        let mut oversized = bytes.clone();
        oversized.push(0);
        assert_eq!(
            decode_shutdown_request(&oversized).unwrap_err().code(),
            "bridge_target_shutdown_oversized"
        );

        let mut guard = ShutdownReplayGuard::default();
        assert_eq!(guard.consume(&bytes, binding()).unwrap(), shutdown());
        assert!(guard.consumed());
        assert_eq!(
            guard.consume(&bytes, binding()).unwrap_err().code(),
            "bridge_target_shutdown_replayed"
        );

        let mut drifted = bytes;
        drifted[14] ^= 0x40;
        replace_digest(&mut drifted, SHUTDOWN_DOMAIN);
        assert_eq!(
            ShutdownReplayGuard::default()
                .consume(&drifted, binding())
                .unwrap_err()
                .code(),
            "bridge_target_shutdown_binding_drifted"
        );

        let mut flag_drift = encode_shutdown_request(shutdown()).unwrap();
        flag_drift[163] ^= SHUTDOWN_FLAG_CLOSE_AFTER_ACCOUNTING as u8;
        replace_digest(&mut flag_drift, SHUTDOWN_DOMAIN);
        assert_eq!(
            decode_shutdown_request(&flag_drift).unwrap_err().code(),
            "bridge_target_shutdown_invalid"
        );
    }

    #[test]
    fn accounting_is_accepted_only_once_at_eof() {
        let bytes = encode_shutdown_accounting(accounting()).unwrap();
        let mut decoder = AccountingEofDecoder::new(shutdown());
        decoder.feed(&bytes[..17]).unwrap();
        decoder.feed(&bytes[17..]).unwrap();
        assert_eq!(decoder.finish_eof().unwrap(), accounting());
        assert_eq!(
            decoder.finish_eof().unwrap_err().code(),
            "bridge_target_accounting_replayed"
        );
        assert_eq!(
            decoder.feed(&bytes).unwrap_err().code(),
            "bridge_target_accounting_replayed"
        );

        let mut truncated = AccountingEofDecoder::new(shutdown());
        truncated.feed(&bytes[..bytes.len() - 1]).unwrap();
        assert_eq!(
            truncated.finish_eof().unwrap_err().code(),
            "bridge_target_accounting_truncated"
        );

        let mut oversized = AccountingEofDecoder::new(shutdown());
        let mut extra = bytes.clone();
        extra.push(0);
        assert_eq!(
            oversized.feed(&extra).unwrap_err().code(),
            "bridge_target_accounting_oversized"
        );

        let mut drifted = bytes;
        drifted[149] ^= 1;
        replace_digest(&mut drifted, ACCOUNTING_DOMAIN);
        let mut semantic_drift = AccountingEofDecoder::new(shutdown());
        semantic_drift.feed(&drifted).unwrap();
        assert_eq!(
            semantic_drift.finish_eof().unwrap_err().code(),
            "bridge_target_accounting_binding_drifted"
        );

        let mut noncanonical = encode_shutdown_accounting(accounting()).unwrap();
        noncanonical[212] = 2;
        replace_digest(&mut noncanonical, ACCOUNTING_DOMAIN);
        let mut field_drift = AccountingEofDecoder::new(shutdown());
        field_drift.feed(&noncanonical).unwrap();
        assert_eq!(
            field_drift.finish_eof().unwrap_err().code(),
            "bridge_target_protocol_noncanonical"
        );
    }
}
