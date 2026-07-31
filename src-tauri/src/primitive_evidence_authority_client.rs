use crate::primitive_evidence_authority_windows::{
    AuthorityLayout, AUTHORITY_PIPE_NAME, AUTHORITY_PIPE_SDDL,
};
use serde::{de, Deserialize, Deserializer};
use serde_json::{Map, Number, Value};
use sha2::{Digest, Sha256};
use std::{
    fmt,
    io::{self, Read, Write},
    path::{Component, Path},
};

#[path = "primitive_evidence_authority_pipe/handle_tokens.rs"]
mod handle_tokens;
pub use handle_tokens::ExternalModelPartHandleTokens;

const REQUEST_SCHEMA: &str = "vrcforge.primitive_evidence_authority_request.v2";
const RESPONSE_SCHEMA: &str = "vrcforge.primitive_evidence_authority_response.v1";
const GENERATION_ATTESTATION_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_generation_attestation.v1";
const GENERATION_ATTESTATION_DOMAIN: &[u8] = b"vrcforge-authority-generation-attestation-v1\0";
// Parent-side exchange remains closed until the controller launcher slice is connected.
#[allow(dead_code)]
const CANONICAL_HANDSHAKE_DOMAIN: &[u8] = b"vrcforge-authority-canonical-handshake-v1\0";
const SERVICE_INSTANCE_DOMAIN: &[u8] = b"vrcforge-authority-service-instance-v1\0";
const FIXED_PIPE_IDENTITY_DOMAIN: &[u8] = b"vrcforge-authority-fixed-pipe-identity-v1\0";
const GENERATION_ATTESTATION_POLICY_ID: &str = "vrcforge.authority.generation-attestation.fixed.v1";
const GENERATION_ATTESTATION_PROOF_ALGORITHM: &str = "p256-sha256-raw-rs-low-s";
const MAX_REQUEST_FRAME_SIZE: usize = 64 * 1024;
const MAX_RESPONSE_FRAME_SIZE: usize = 16 * 1024 * 1024;
const CLIENT_IO_TIMEOUT_MS: u32 = 5_000;
const P256_ORDER: [u8; 32] = [
    0xff, 0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xbc, 0xe6, 0xfa, 0xad, 0xa7, 0x17, 0x9e, 0x84, 0xf3, 0xb9, 0xca, 0xc2, 0xfc, 0x63, 0x25, 0x51,
];
const P256_HALF_ORDER: [u8; 32] = [
    0x7f, 0xff, 0xff, 0xff, 0x80, 0x00, 0x00, 0x00, 0x7f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xde, 0x73, 0x7d, 0x56, 0xd3, 0x8b, 0xcf, 0x42, 0x79, 0xdc, 0xe5, 0x61, 0x7e, 0x31, 0x92, 0xa8,
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityClientError {
    code: &'static str,
    win32: Option<u32>,
}

impl AuthorityClientError {
    fn new(code: &'static str) -> Self {
        Self { code, win32: None }
    }

    pub fn from_code(code: &'static str) -> Self {
        Self::new(code)
    }

    #[cfg(windows)]
    fn last_win32(code: &'static str) -> Self {
        Self {
            code,
            win32: Some(unsafe { windows_sys::Win32::Foundation::GetLastError() }),
        }
    }

    pub fn code(&self) -> &'static str {
        self.code
    }

    pub fn win32(&self) -> Option<u32> {
        self.win32
    }
}

impl fmt::Display for AuthorityClientError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.win32 {
            Some(win32) => write!(formatter, "{} (win32={win32})", self.code),
            None => formatter.write_str(self.code),
        }
    }
}

impl std::error::Error for AuthorityClientError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AuthorityClientCommand {
    Status,
    SelfTest,
    RunModelPartComposition {
        request_id: String,
        handle_tokens: ExternalModelPartHandleTokens,
    },
    Cancel {
        request_id: String,
    },
    GetResult {
        request_id: String,
    },
}

impl AuthorityClientCommand {
    pub fn command_name(&self) -> &'static str {
        match self {
            Self::Status => "status",
            Self::SelfTest => "selfTest",
            Self::RunModelPartComposition { .. } => "runModelPartComposition",
            Self::Cancel { .. } => "cancel",
            Self::GetResult { .. } => "getResult",
        }
    }

    pub fn validate(&self) -> Result<(), AuthorityClientError> {
        match self {
            Self::RunModelPartComposition { request_id, .. }
            | Self::Cancel { request_id }
            | Self::GetResult { request_id } => require_request_id(request_id),
            Self::Status | Self::SelfTest => Ok(()),
        }
    }

    fn canonical_payload(&self) -> Result<Vec<u8>, AuthorityClientError> {
        self.validate()?;
        let value = match self {
            Self::Status => serde_json::json!({
                "command": self.command_name(),
                "schema": REQUEST_SCHEMA,
            }),
            Self::SelfTest => serde_json::json!({
                "command": self.command_name(),
                "schema": REQUEST_SCHEMA,
            }),
            Self::RunModelPartComposition {
                request_id,
                handle_tokens,
            } => serde_json::json!({
                "command": self.command_name(),
                "handleTokens": handle_tokens,
                "requestId": request_id,
                "schema": REQUEST_SCHEMA,
            }),
            Self::Cancel { request_id } | Self::GetResult { request_id } => {
                serde_json::json!({
                    "command": self.command_name(),
                    "requestId": request_id,
                    "schema": REQUEST_SCHEMA,
                })
            }
        };
        serde_json::to_vec(&value)
            .map_err(|_| AuthorityClientError::new("authority_client_request_encode_failed"))
    }
}

pub struct CanonicalAuthorityResponse {
    raw_bytes: Vec<u8>,
}

impl fmt::Debug for CanonicalAuthorityResponse {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CanonicalAuthorityResponse")
            .field("byte_len", &self.raw_bytes.len())
            .finish_non_exhaustive()
    }
}

impl CanonicalAuthorityResponse {
    pub fn raw_bytes(&self) -> &[u8] {
        &self.raw_bytes
    }
}

pub struct AuthorityClientExchange {
    command: &'static str,
    handshake: CanonicalAuthorityResponse,
    response: CanonicalAuthorityResponse,
}

/// Exact signed handshake material revalidated by the controller's parent.
///
/// Shape validation is deliberately not enough: callers can obtain this value
/// only through [`verify_parent_controller_exchange`], which also invokes a
/// trusted cryptographic verifier for the protected authority key.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifiedAuthorityHandshake {
    canonical_handshake_sha256: [u8; 32],
    generation_sha256: [u8; 32],
    attestation_digest: [u8; 32],
    signature_p256: [u8; 64],
    signer_key_id: [u8; 32],
    peer_binding_sha256: [u8; 32],
}

#[allow(dead_code)] // parent-side exchange remains closed until the controller launcher slice is connected
impl VerifiedAuthorityHandshake {
    pub fn canonical_handshake_sha256(&self) -> &[u8; 32] {
        &self.canonical_handshake_sha256
    }

    pub fn generation_sha256(&self) -> &[u8; 32] {
        &self.generation_sha256
    }

    pub fn attestation_digest(&self) -> &[u8; 32] {
        &self.attestation_digest
    }

    pub fn signature_p256(&self) -> &[u8; 64] {
        &self.signature_p256
    }

    pub fn signer_key_id(&self) -> &[u8; 32] {
        &self.signer_key_id
    }

    pub fn peer_binding_sha256(&self) -> &[u8; 32] {
        &self.peer_binding_sha256
    }
}

/// Trusted signature operation supplied by the authenticated parent boundary.
/// Implementations must verify the raw P-256 signature over `digest`; returning
/// success based only on scalar shape or key-id equality violates this contract.
#[allow(dead_code)] // parent-side exchange remains closed until the controller launcher slice is connected
pub trait AuthorityHandshakeSignatureVerifier {
    fn verify_digest_signature(
        &mut self,
        signer_key_id: &[u8; 32],
        digest: &[u8; 32],
        signature: &[u8; 64],
    ) -> Result<(), AuthorityClientError>;
}

/// Revalidates the nested controller exchange and requires a cryptographic
/// signature decision from the parent's authenticated key capability.
///
/// The challenge is generated inside the controller and therefore is not a
/// parent-side authority input. Freshness is instead bound to the exact signed
/// peer digest, which the launcher derives from its held process and image.
/// The command response is checked only for canonical shape and command
/// equality; it is not cryptographically authenticated by the handshake and
/// must remain explicitly unverified until a separate signed response
/// transcript is available.
#[allow(dead_code)] // parent-side exchange remains closed until the controller launcher slice is connected
pub fn verify_parent_controller_exchange<V>(
    handshake_payload: &[u8],
    response_payload: &[u8],
    expected_generation: &[u8; 32],
    expected_peer_binding_sha256: &[u8; 32],
    expected_signer_key_id: &[u8; 32],
    expected_command: &str,
    verifier: &mut V,
) -> Result<VerifiedAuthorityHandshake, AuthorityClientError>
where
    V: AuthorityHandshakeSignatureVerifier,
{
    if expected_generation.iter().all(|byte| *byte == 0)
        || expected_peer_binding_sha256.iter().all(|byte| *byte == 0)
        || expected_signer_key_id.iter().all(|byte| *byte == 0)
        || !matches!(
            expected_command,
            "status" | "selfTest" | "runModelPartComposition" | "cancel" | "getResult"
        )
    {
        return Err(AuthorityClientError::new(
            "authority_parent_exchange_expectation_invalid",
        ));
    }
    let value = parse_canonical_response(handshake_payload)?;
    let response = match validate_response_shape(&value, "handshake")? {
        ResponseShape::Success(response) => response,
        ResponseShape::Error => {
            return Err(AuthorityClientError::new(
                "authority_client_handshake_rejected",
            ))
        }
    };
    let result = response
        .get("result")
        .and_then(Value::as_object)
        .ok_or_else(|| AuthorityClientError::new("authority_client_handshake_shape_invalid"))?;
    let challenge = parse_hex_array::<32>(text_field(result, "challenge")?)?;
    if challenge.iter().all(|byte| *byte == 0) {
        return Err(AuthorityClientError::new(
            "authority_client_handshake_challenge_mismatch",
        ));
    }
    validate_handshake_response(handshake_payload, expected_generation, &challenge)?;

    let peer_binding_sha256 = parse_required_digest(result, "peerBindingSha256")?;
    if !constant_time_equal(&peer_binding_sha256, expected_peer_binding_sha256) {
        return Err(AuthorityClientError::new(
            "authority_parent_exchange_peer_binding_mismatch",
        ));
    }
    let signer_key_id = parse_required_digest(result, "signerKeyId")?;
    if !constant_time_equal(&signer_key_id, expected_signer_key_id) {
        return Err(AuthorityClientError::new(
            "authority_parent_exchange_signer_mismatch",
        ));
    }
    let attestation_digest = parse_required_digest(result, "attestationDigest")?;
    let signature_p256 = parse_hex_array::<64>(text_field(result, "signatureP256")?)?;
    if !p256_signature_is_canonical_low_s(&signature_p256) {
        return Err(AuthorityClientError::new(
            "authority_client_handshake_signature_shape_invalid",
        ));
    }
    decode_command_response(response_payload, expected_command)?;
    verifier.verify_digest_signature(&signer_key_id, &attestation_digest, &signature_p256)?;
    let mut canonical_handshake = Sha256::new();
    canonical_handshake.update(CANONICAL_HANDSHAKE_DOMAIN);
    canonical_handshake.update((handshake_payload.len() as u64).to_be_bytes());
    canonical_handshake.update(handshake_payload);
    Ok(VerifiedAuthorityHandshake {
        canonical_handshake_sha256: canonical_handshake.finalize().into(),
        generation_sha256: *expected_generation,
        attestation_digest,
        signature_p256,
        signer_key_id,
        peer_binding_sha256,
    })
}

impl fmt::Debug for AuthorityClientExchange {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AuthorityClientExchange")
            .field("command", &self.command)
            .field("handshake_byte_len", &self.handshake.raw_bytes.len())
            .field("response_byte_len", &self.response.raw_bytes.len())
            .finish_non_exhaustive()
    }
}

impl AuthorityClientExchange {
    pub fn command(&self) -> &'static str {
        self.command
    }

    pub fn handshake_raw_bytes(&self) -> &[u8] {
        self.handshake.raw_bytes()
    }

    pub fn response_raw_bytes(&self) -> &[u8] {
        self.response.raw_bytes()
    }
}

struct AuthorityControllerClient<T> {
    transport: T,
    handshake: CanonicalAuthorityResponse,
}

impl<T: Read + Write> AuthorityControllerClient<T> {
    fn establish(
        mut transport: T,
        expected_generation: [u8; 32],
        mut challenge: SensitiveChallenge,
    ) -> Result<Self, AuthorityClientError> {
        let mut challenge_hex = [0u8; 64];
        encode_hex_into(&challenge.bytes, &mut challenge_hex);
        let challenge_text = std::str::from_utf8(&challenge_hex)
            .map_err(|_| AuthorityClientError::new("authority_client_request_encode_failed"))?;
        let mut payload = ZeroizingBuffer(
            format!(
                "{{\"challenge\":\"{challenge_text}\",\"command\":\"handshake\",\"expectedGeneration\":\"{}\",\"schema\":\"{REQUEST_SCHEMA}\"}}",
                hex_lower(&expected_generation),
            )
            .into_bytes(),
        );
        challenge_hex.fill(0);
        std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
        write_request_frame(&mut transport, &payload.0)?;
        payload.clear();
        let response_bytes = read_response_frame(&mut transport)?;
        let handshake_result =
            validate_handshake_response(&response_bytes, &expected_generation, &challenge.bytes);
        challenge.clear();
        let handshake = handshake_result?;
        Ok(Self {
            transport,
            handshake,
        })
    }

    #[cfg(test)]
    fn establish_for_test(
        transport: T,
        expected_generation: [u8; 32],
        challenge: [u8; 32],
    ) -> Result<Self, AuthorityClientError> {
        Self::establish(
            transport,
            expected_generation,
            SensitiveChallenge::from_bytes_for_test(challenge)?,
        )
    }

    fn execute(
        &mut self,
        command: AuthorityClientCommand,
    ) -> Result<CanonicalAuthorityResponse, AuthorityClientError> {
        let command_name = command.command_name();
        let payload = command.canonical_payload()?;
        write_request_frame(&mut self.transport, &payload)?;
        let response = read_response_frame(&mut self.transport)?;
        decode_command_response(&response, command_name)
    }

    #[cfg(test)]
    fn handshake_raw_bytes(&self) -> &[u8] {
        self.handshake.raw_bytes()
    }

    #[cfg(test)]
    fn into_transport_for_test(self) -> T {
        self.transport
    }
}

#[cfg(windows)]
pub struct InstalledAuthorityClient {
    inner: AuthorityControllerClient<windows::FixedAuthorityPipeClient>,
}

#[cfg(not(windows))]
pub struct InstalledAuthorityClient;

#[cfg(windows)]
impl InstalledAuthorityClient {
    pub fn connect() -> Result<Self, AuthorityClientError> {
        let layout = AuthorityLayout::installed()
            .map_err(|error| AuthorityClientError::new(error.code()))?;
        let current_executable = std::env::current_exe().map_err(|_| {
            AuthorityClientError::new("authority_client_controller_image_unavailable")
        })?;
        let generation = derive_controller_generation_from_path(&layout, &current_executable)?;
        let transport = windows::FixedAuthorityPipeClient::connect(&layout, &generation)?;
        let challenge = SensitiveChallenge::generate()?;
        Ok(Self {
            inner: AuthorityControllerClient::establish(transport, generation, challenge)?,
        })
    }

    pub fn execute(
        self,
        command: AuthorityClientCommand,
    ) -> Result<AuthorityClientExchange, AuthorityClientError> {
        let command_name = command.command_name();
        let mut inner = self.inner;
        let response = inner.execute(command)?;
        Ok(AuthorityClientExchange {
            command: command_name,
            handshake: inner.handshake,
            response,
        })
    }
}

#[cfg(not(windows))]
impl InstalledAuthorityClient {
    pub fn connect() -> Result<Self, AuthorityClientError> {
        Err(AuthorityClientError::new(
            "authority_client_platform_unsupported",
        ))
    }

    pub fn execute(
        self,
        _command: AuthorityClientCommand,
    ) -> Result<AuthorityClientExchange, AuthorityClientError> {
        Err(AuthorityClientError::new(
            "authority_client_platform_unsupported",
        ))
    }
}

struct SensitiveChallenge {
    bytes: [u8; 32],
}

impl SensitiveChallenge {
    #[cfg(windows)]
    fn generate() -> Result<Self, AuthorityClientError> {
        let mut bytes = [0u8; 32];
        getrandom::fill(&mut bytes)
            .map_err(|_| AuthorityClientError::new("authority_client_secure_random_unavailable"))?;
        Self::from_bytes(bytes)
    }

    fn from_bytes(bytes: [u8; 32]) -> Result<Self, AuthorityClientError> {
        if bytes.iter().all(|byte| *byte == 0) {
            return Err(AuthorityClientError::new(
                "authority_client_challenge_invalid",
            ));
        }
        Ok(Self { bytes })
    }

    #[cfg(test)]
    fn from_bytes_for_test(bytes: [u8; 32]) -> Result<Self, AuthorityClientError> {
        Self::from_bytes(bytes)
    }

    fn clear(&mut self) {
        self.bytes.fill(0);
        std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
    }
}

impl Drop for SensitiveChallenge {
    fn drop(&mut self) {
        self.clear();
    }
}

struct ZeroizingBuffer(Vec<u8>);

impl ZeroizingBuffer {
    fn clear(&mut self) {
        self.0.fill(0);
        std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
    }
}

impl Drop for ZeroizingBuffer {
    fn drop(&mut self) {
        self.clear();
    }
}

fn write_request_frame<W: Write>(
    writer: &mut W,
    payload: &[u8],
) -> Result<(), AuthorityClientError> {
    if payload.is_empty() {
        return Err(AuthorityClientError::new(
            "authority_client_request_frame_empty",
        ));
    }
    if payload.len() > MAX_REQUEST_FRAME_SIZE {
        return Err(AuthorityClientError::new(
            "authority_client_request_frame_too_large",
        ));
    }
    let mut frame = ZeroizingBuffer(Vec::with_capacity(payload.len() + 4));
    frame
        .0
        .extend_from_slice(&(payload.len() as u32).to_be_bytes());
    frame.0.extend_from_slice(payload);
    write_all_checked(writer, &frame.0)?;
    writer.flush().map_err(map_write_error)
}

fn read_response_frame<R: Read>(reader: &mut R) -> Result<Vec<u8>, AuthorityClientError> {
    let mut header = [0u8; 4];
    read_exact_checked(
        reader,
        &mut header,
        "authority_client_response_eof",
        "authority_client_response_header_truncated",
    )?;
    let length = u32::from_be_bytes(header) as usize;
    if length == 0 {
        return Err(AuthorityClientError::new(
            "authority_client_response_frame_empty",
        ));
    }
    if length > MAX_RESPONSE_FRAME_SIZE {
        return Err(AuthorityClientError::new(
            "authority_client_response_frame_too_large",
        ));
    }
    let mut payload = vec![0u8; length];
    read_exact_checked(
        reader,
        &mut payload,
        "authority_client_response_body_truncated",
        "authority_client_response_body_truncated",
    )?;
    Ok(payload)
}

fn write_all_checked<W: Write>(
    writer: &mut W,
    mut bytes: &[u8],
) -> Result<(), AuthorityClientError> {
    while !bytes.is_empty() {
        match writer.write(bytes) {
            Ok(0) => {
                return Err(AuthorityClientError::new(
                    "authority_client_write_incomplete",
                ))
            }
            Ok(written) => bytes = &bytes[written..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(map_write_error(error)),
        }
    }
    Ok(())
}

fn read_exact_checked<R: Read>(
    reader: &mut R,
    buffer: &mut [u8],
    empty_code: &'static str,
    truncated_code: &'static str,
) -> Result<(), AuthorityClientError> {
    let mut offset = 0usize;
    while offset < buffer.len() {
        match reader.read(&mut buffer[offset..]) {
            Ok(0) => {
                return Err(AuthorityClientError::new(if offset == 0 {
                    empty_code
                } else {
                    truncated_code
                }))
            }
            Ok(read) => offset += read,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(map_read_error(error)),
        }
    }
    Ok(())
}

fn map_write_error(error: io::Error) -> AuthorityClientError {
    if matches!(
        error.kind(),
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
    ) {
        AuthorityClientError::new("authority_client_io_timeout")
    } else {
        AuthorityClientError {
            code: "authority_client_write_failed",
            win32: error
                .raw_os_error()
                .and_then(|value| u32::try_from(value).ok()),
        }
    }
}

fn map_read_error(error: io::Error) -> AuthorityClientError {
    if matches!(
        error.kind(),
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
    ) {
        AuthorityClientError::new("authority_client_io_timeout")
    } else {
        AuthorityClientError {
            code: "authority_client_read_failed",
            win32: error
                .raw_os_error()
                .and_then(|value| u32::try_from(value).ok()),
        }
    }
}

#[derive(Debug, Clone)]
struct StrictJsonValue(Value);

impl<'de> Deserialize<'de> for StrictJsonValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_any(StrictValueVisitor).map(Self)
    }
}

struct StrictValueVisitor;

impl<'de> de::Visitor<'de> for StrictValueVisitor {
    type Value = Value;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("strict JSON without duplicate keys or floating-point numbers")
    }

    fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E> {
        Ok(Value::Bool(value))
    }

    fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E> {
        Ok(Value::Number(Number::from(value)))
    }

    fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E> {
        Ok(Value::Number(Number::from(value)))
    }

    fn visit_f64<E>(self, _value: f64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Err(E::custom("floating_point_not_allowed"))
    }

    fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Ok(Value::String(value.to_owned()))
    }

    fn visit_string<E>(self, value: String) -> Result<Self::Value, E> {
        Ok(Value::String(value))
    }

    fn visit_none<E>(self) -> Result<Self::Value, E> {
        Ok(Value::Null)
    }

    fn visit_unit<E>(self) -> Result<Self::Value, E> {
        Ok(Value::Null)
    }

    fn visit_some<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: Deserializer<'de>,
    {
        StrictJsonValue::deserialize(deserializer).map(|value| value.0)
    }

    fn visit_seq<A>(self, mut sequence: A) -> Result<Self::Value, A::Error>
    where
        A: de::SeqAccess<'de>,
    {
        let mut values = Vec::new();
        while let Some(value) = sequence.next_element::<StrictJsonValue>()? {
            values.push(value.0);
        }
        Ok(Value::Array(values))
    }

    fn visit_map<A>(self, mut map: A) -> Result<Self::Value, A::Error>
    where
        A: de::MapAccess<'de>,
    {
        let mut values = Map::new();
        while let Some((key, value)) = map.next_entry::<String, StrictJsonValue>()? {
            if values.insert(key, value.0).is_some() {
                return Err(de::Error::custom("duplicate_object_key"));
            }
        }
        Ok(Value::Object(values))
    }
}

enum ResponseShape<'a> {
    Success(&'a Map<String, Value>),
    Error,
}

fn parse_canonical_response(payload: &[u8]) -> Result<Value, AuthorityClientError> {
    if payload.is_empty() {
        return Err(AuthorityClientError::new(
            "authority_client_response_frame_empty",
        ));
    }
    if payload.len() > MAX_RESPONSE_FRAME_SIZE {
        return Err(AuthorityClientError::new(
            "authority_client_response_frame_too_large",
        ));
    }
    let strict = serde_json::from_slice::<StrictJsonValue>(payload).map_err(|error| {
        let message = error.to_string();
        if message.contains("duplicate_object_key") {
            AuthorityClientError::new("authority_client_response_duplicate_key")
        } else if message.contains("floating_point_not_allowed") {
            AuthorityClientError::new("authority_client_response_float_rejected")
        } else {
            AuthorityClientError::new("authority_client_response_json_invalid")
        }
    })?;
    let canonical = serde_json::to_vec(&strict.0)
        .map_err(|_| AuthorityClientError::new("authority_client_response_json_invalid"))?;
    if canonical != payload {
        return Err(AuthorityClientError::new(
            "authority_client_response_noncanonical",
        ));
    }
    Ok(strict.0)
}

fn validate_response_shape<'a>(
    value: &'a Value,
    expected_command: &str,
) -> Result<ResponseShape<'a>, AuthorityClientError> {
    let object = value
        .as_object()
        .ok_or_else(|| AuthorityClientError::new("authority_client_response_shape_invalid"))?;
    if object.get("schema").and_then(Value::as_str) != Some(RESPONSE_SCHEMA) {
        return Err(AuthorityClientError::new(
            "authority_client_response_schema_mismatch",
        ));
    }
    match object.get("ok").and_then(Value::as_bool) {
        Some(true) => {
            require_exact_keys(object, &["command", "ok", "result", "schema"])?;
            if object.get("command").and_then(Value::as_str) != Some(expected_command) {
                return Err(AuthorityClientError::new(
                    "authority_client_response_command_mismatch",
                ));
            }
            Ok(ResponseShape::Success(object))
        }
        Some(false) => {
            require_exact_keys(object, &["error", "ok", "schema"])?;
            let error = object
                .get("error")
                .and_then(Value::as_object)
                .ok_or_else(|| {
                    AuthorityClientError::new("authority_client_response_shape_invalid")
                })?;
            require_exact_keys(error, &["code"])?;
            let code = error.get("code").and_then(Value::as_str).ok_or_else(|| {
                AuthorityClientError::new("authority_client_response_shape_invalid")
            })?;
            if code.is_empty()
                || code.len() > 160
                || !code
                    .bytes()
                    .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_')
            {
                return Err(AuthorityClientError::new(
                    "authority_client_response_shape_invalid",
                ));
            }
            Ok(ResponseShape::Error)
        }
        _ => Err(AuthorityClientError::new(
            "authority_client_response_shape_invalid",
        )),
    }
}

fn decode_command_response(
    payload: &[u8],
    expected_command: &str,
) -> Result<CanonicalAuthorityResponse, AuthorityClientError> {
    let value = parse_canonical_response(payload)?;
    validate_response_shape(&value, expected_command)?;
    Ok(CanonicalAuthorityResponse {
        raw_bytes: payload.to_vec(),
    })
}

fn validate_handshake_response(
    payload: &[u8],
    expected_generation: &[u8; 32],
    expected_challenge: &[u8; 32],
) -> Result<CanonicalAuthorityResponse, AuthorityClientError> {
    let value = parse_canonical_response(payload)?;
    let response = match validate_response_shape(&value, "handshake")? {
        ResponseShape::Success(response) => response,
        ResponseShape::Error => {
            return Err(AuthorityClientError::new(
                "authority_client_handshake_rejected",
            ))
        }
    };
    let result = response
        .get("result")
        .and_then(Value::as_object)
        .ok_or_else(|| AuthorityClientError::new("authority_client_handshake_shape_invalid"))?;
    const ATTESTATION_KEYS: [&str; 23] = [
        "attestationDigest",
        "bootstrapReceiptSha256",
        "challenge",
        "currentGeneration",
        "fixedPipeIdentityDigest",
        "peerBindingSha256",
        "pipeName",
        "policyId",
        "proofAlgorithm",
        "protectedKeyReadbackSha256",
        "protectedLedgerReadbackSha256",
        "protectedManifestReadbackSha256",
        "schema",
        "scmReadbackSha256",
        "sequence",
        "serviceExecutableFileIdentitySha256",
        "serviceExecutablePathSha256",
        "serviceExecutableSha256",
        "serviceInstanceDigest",
        "serviceProcessId",
        "serviceProcessStartedAt",
        "signatureP256",
        "signerKeyId",
    ];
    if result.len() != ATTESTATION_KEYS.len()
        || !ATTESTATION_KEYS.iter().all(|key| result.contains_key(*key))
    {
        return Err(AuthorityClientError::new(
            "authority_client_handshake_shape_invalid",
        ));
    }
    if text_field(result, "schema")? != GENERATION_ATTESTATION_SCHEMA
        || text_field(result, "proofAlgorithm")? != GENERATION_ATTESTATION_PROOF_ALGORITHM
        || text_field(result, "policyId")? != GENERATION_ATTESTATION_POLICY_ID
    {
        return Err(AuthorityClientError::new(
            "authority_client_handshake_policy_mismatch",
        ));
    }
    let generation = parse_hex_array::<32>(text_field(result, "currentGeneration")?)?;
    if !constant_time_equal(&generation, expected_generation) {
        return Err(AuthorityClientError::new(
            "authority_client_handshake_generation_mismatch",
        ));
    }
    let challenge = parse_hex_array::<32>(text_field(result, "challenge")?)?;
    if !constant_time_equal(&challenge, expected_challenge) {
        return Err(AuthorityClientError::new(
            "authority_client_handshake_challenge_mismatch",
        ));
    }
    if text_field(result, "pipeName")? != AUTHORITY_PIPE_NAME {
        return Err(AuthorityClientError::new(
            "authority_client_handshake_pipe_mismatch",
        ));
    }
    let fixed_pipe = parse_hex_array::<32>(text_field(result, "fixedPipeIdentityDigest")?)?;
    let expected_fixed_pipe = fixed_pipe_identity_digest();
    if !constant_time_equal(&fixed_pipe, &expected_fixed_pipe) {
        return Err(AuthorityClientError::new(
            "authority_client_handshake_pipe_identity_mismatch",
        ));
    }
    let service_instance = parse_required_digest(result, "serviceInstanceDigest")?;
    let peer_binding = parse_required_digest(result, "peerBindingSha256")?;
    let bootstrap_receipt = parse_required_digest(result, "bootstrapReceiptSha256")?;
    let protected_key = parse_required_digest(result, "protectedKeyReadbackSha256")?;
    let protected_ledger = parse_required_digest(result, "protectedLedgerReadbackSha256")?;
    let protected_manifest = parse_required_digest(result, "protectedManifestReadbackSha256")?;
    let scm_readback = parse_required_digest(result, "scmReadbackSha256")?;
    let service_executable_file_identity =
        parse_required_digest(result, "serviceExecutableFileIdentitySha256")?;
    let service_executable_path = parse_required_digest(result, "serviceExecutablePathSha256")?;
    let service_executable = parse_required_digest(result, "serviceExecutableSha256")?;
    let signer_key_id = parse_required_digest(result, "signerKeyId")?;
    let service_process_id = result
        .get("serviceProcessId")
        .and_then(Value::as_u64)
        .filter(|value| *value > 0 && *value <= u32::MAX as u64)
        .ok_or_else(|| AuthorityClientError::new("authority_client_handshake_shape_invalid"))?;
    let service_process_started_at = result
        .get("serviceProcessStartedAt")
        .and_then(Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| AuthorityClientError::new("authority_client_handshake_shape_invalid"))?;
    let mut service_instance_hasher = Sha256::new();
    service_instance_hasher.update(SERVICE_INSTANCE_DOMAIN);
    service_instance_hasher.update(generation);
    service_instance_hasher.update(service_executable);
    service_instance_hasher.update(service_executable_path);
    service_instance_hasher.update(service_executable_file_identity);
    service_instance_hasher.update((service_process_id as u32).to_be_bytes());
    service_instance_hasher.update(service_process_started_at.to_be_bytes());
    service_instance_hasher.update(expected_fixed_pipe);
    service_instance_hasher.update(protected_manifest);
    service_instance_hasher.update(protected_key);
    service_instance_hasher.update(signer_key_id);
    service_instance_hasher.update(protected_ledger);
    service_instance_hasher.update(scm_readback);
    service_instance_hasher.update(bootstrap_receipt);
    let expected_service_instance: [u8; 32] = service_instance_hasher.finalize().into();
    if !constant_time_equal(&service_instance, &expected_service_instance) {
        return Err(AuthorityClientError::new(
            "authority_client_handshake_service_instance_mismatch",
        ));
    }
    let sequence = result
        .get("sequence")
        .and_then(Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| AuthorityClientError::new("authority_client_handshake_shape_invalid"))?;
    let attestation_digest = parse_hex_array::<32>(text_field(result, "attestationDigest")?)?;
    let mut digest = Sha256::new();
    digest.update(GENERATION_ATTESTATION_DOMAIN);
    digest.update(GENERATION_ATTESTATION_POLICY_ID.as_bytes());
    digest.update(GENERATION_ATTESTATION_PROOF_ALGORITHM.as_bytes());
    digest.update(expected_fixed_pipe);
    digest.update(service_instance);
    digest.update(peer_binding);
    digest.update(challenge);
    digest.update(sequence.to_be_bytes());
    let expected_attestation_digest: [u8; 32] = digest.finalize().into();
    if !constant_time_equal(&attestation_digest, &expected_attestation_digest) {
        return Err(AuthorityClientError::new(
            "authority_client_handshake_digest_mismatch",
        ));
    }
    let signature = parse_hex_array::<64>(text_field(result, "signatureP256")?)?;
    if !p256_signature_is_canonical_low_s(&signature) {
        return Err(AuthorityClientError::new(
            "authority_client_handshake_signature_shape_invalid",
        ));
    }
    Ok(CanonicalAuthorityResponse {
        raw_bytes: payload.to_vec(),
    })
}

fn require_exact_keys(
    object: &Map<String, Value>,
    keys: &[&str],
) -> Result<(), AuthorityClientError> {
    if object.len() != keys.len() || !keys.iter().all(|key| object.contains_key(*key)) {
        return Err(AuthorityClientError::new(
            "authority_client_response_shape_invalid",
        ));
    }
    Ok(())
}

fn text_field<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<&'a str, AuthorityClientError> {
    object
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| AuthorityClientError::new("authority_client_handshake_shape_invalid"))
}

fn parse_required_digest(
    object: &Map<String, Value>,
    field: &str,
) -> Result<[u8; 32], AuthorityClientError> {
    let value = parse_hex_array::<32>(text_field(object, field)?)?;
    if value.iter().all(|byte| *byte == 0) {
        return Err(AuthorityClientError::new(
            "authority_client_handshake_shape_invalid",
        ));
    }
    Ok(value)
}

fn parse_hex_array<const N: usize>(value: &str) -> Result<[u8; N], AuthorityClientError> {
    if value.len() != N * 2
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(AuthorityClientError::new(
            "authority_client_handshake_shape_invalid",
        ));
    }
    let mut output = [0u8; N];
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

fn fixed_pipe_identity_digest() -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(FIXED_PIPE_IDENTITY_DOMAIN);
    digest.update(AUTHORITY_PIPE_NAME.as_bytes());
    digest.update(AUTHORITY_PIPE_SDDL.as_bytes());
    digest.update((MAX_REQUEST_FRAME_SIZE as u64).to_be_bytes());
    digest.update((MAX_RESPONSE_FRAME_SIZE as u64).to_be_bytes());
    digest.update(GENERATION_ATTESTATION_POLICY_ID.as_bytes());
    digest.finalize().into()
}

fn p256_signature_is_canonical_low_s(signature: &[u8; 64]) -> bool {
    let r: &[u8; 32] = signature[..32]
        .try_into()
        .expect("fixed raw P-256 signature shape");
    let s: &[u8; 32] = signature[32..]
        .try_into()
        .expect("fixed raw P-256 signature shape");
    scalar_is_nonzero_and_below(r, &P256_ORDER)
        && scalar_is_nonzero_and_below(s, &P256_ORDER)
        && s <= &P256_HALF_ORDER
}

fn scalar_is_nonzero_and_below(value: &[u8; 32], upper_bound: &[u8; 32]) -> bool {
    value.iter().any(|byte| *byte != 0) && value < upper_bound
}

fn constant_time_equal(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    let mut difference = 0u8;
    for (left_byte, right_byte) in left.iter().zip(right) {
        difference |= left_byte ^ right_byte;
    }
    difference == 0
}

fn require_request_id(value: &str) -> Result<(), AuthorityClientError> {
    let mut characters = value.chars();
    let first = characters
        .next()
        .ok_or_else(|| AuthorityClientError::new("authority_client_request_id_invalid"))?;
    if !first.is_ascii_alphanumeric()
        || value.len() > 128
        || !characters.all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.' | ':')
        })
    {
        return Err(AuthorityClientError::new(
            "authority_client_request_id_invalid",
        ));
    }
    Ok(())
}

fn derive_controller_generation_from_path(
    layout: &AuthorityLayout,
    controller_executable: &Path,
) -> Result<[u8; 32], AuthorityClientError> {
    if !controller_executable.is_absolute()
        || controller_executable
            .components()
            .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
        || controller_executable
            .file_name()
            .and_then(|value| value.to_str())
            != Some("vrcforge_primitive_evidence_controller.exe")
    {
        return Err(AuthorityClientError::new(
            "authority_client_controller_image_invalid",
        ));
    }
    let generation_text = controller_executable
        .parent()
        .and_then(Path::file_name)
        .and_then(|value| value.to_str())
        .ok_or_else(|| AuthorityClientError::new("authority_client_controller_image_invalid"))?;
    let generation = parse_hex_array::<32>(generation_text)
        .map_err(|_| AuthorityClientError::new("authority_client_controller_image_invalid"))?;
    if generation.iter().all(|byte| *byte == 0) {
        return Err(AuthorityClientError::new(
            "authority_client_controller_image_invalid",
        ));
    }
    let expected = layout
        .controller_executable_for_generation(&generation)
        .map_err(|_| AuthorityClientError::new("authority_client_controller_image_invalid"))?;
    if !installed_paths_equal(controller_executable, &expected) {
        return Err(AuthorityClientError::new(
            "authority_client_controller_image_invalid",
        ));
    }
    Ok(generation)
}

fn installed_paths_equal(left: &Path, right: &Path) -> bool {
    fn normalized(value: &Path) -> String {
        let text = value.to_string_lossy().replace('/', "\\");
        text.strip_prefix(r"\\?\").unwrap_or(&text).to_lowercase()
    }
    normalized(left) == normalized(right)
}

fn hex_lower(value: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        output.push(DIGITS[(byte >> 4) as usize] as char);
        output.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    output
}

fn encode_hex_into(value: &[u8], output: &mut [u8]) {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    assert_eq!(output.len(), value.len() * 2);
    for (index, byte) in value.iter().enumerate() {
        output[index * 2] = DIGITS[(byte >> 4) as usize];
        output[index * 2 + 1] = DIGITS[(byte & 0x0f) as usize];
    }
}

#[cfg(windows)]
mod windows {
    use super::*;
    use std::{
        ffi::{OsStr, OsString},
        mem::zeroed,
        os::windows::{
            ffi::{OsStrExt, OsStringExt},
            io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle},
        },
        path::PathBuf,
        ptr,
    };
    use windows_sys::Win32::{
        Foundation::{
            GetLastError, ERROR_BROKEN_PIPE, ERROR_IO_PENDING, ERROR_MORE_DATA,
            ERROR_OPERATION_ABORTED, GENERIC_READ, GENERIC_WRITE, INVALID_HANDLE_VALUE,
            WAIT_OBJECT_0, WAIT_TIMEOUT,
        },
        Storage::FileSystem::{
            CreateFileW, ReadFile, WriteFile, FILE_FLAG_OVERLAPPED, OPEN_EXISTING, SYNCHRONIZE,
        },
        System::{
            Pipes::{GetNamedPipeServerProcessId, WaitNamedPipeW},
            Threading::{
                CreateEventW, GetProcessId, OpenProcess, QueryFullProcessImageNameW,
                WaitForMultipleObjects, WaitForSingleObject, PROCESS_QUERY_LIMITED_INFORMATION,
            },
            IO::{CancelIoEx, GetOverlappedResult, OVERLAPPED},
        },
    };

    pub(super) struct FixedAuthorityPipeClient {
        pipe: OwnedHandle,
        service_process: OwnedHandle,
    }

    impl fmt::Debug for FixedAuthorityPipeClient {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("FixedAuthorityPipeClient")
                .finish_non_exhaustive()
        }
    }

    impl FixedAuthorityPipeClient {
        pub(super) fn connect(
            layout: &AuthorityLayout,
            generation: &[u8; 32],
        ) -> Result<Self, AuthorityClientError> {
            let pipe_name = wide_null(OsStr::new(AUTHORITY_PIPE_NAME));
            if unsafe { WaitNamedPipeW(pipe_name.as_ptr(), CLIENT_IO_TIMEOUT_MS) } == 0 {
                return Err(AuthorityClientError::last_win32(
                    "authority_client_pipe_unavailable",
                ));
            }
            let handle = unsafe {
                CreateFileW(
                    pipe_name.as_ptr(),
                    GENERIC_READ | GENERIC_WRITE,
                    0,
                    ptr::null(),
                    OPEN_EXISTING,
                    FILE_FLAG_OVERLAPPED,
                    ptr::null_mut(),
                )
            };
            if handle == INVALID_HANDLE_VALUE {
                return Err(AuthorityClientError::last_win32(
                    "authority_client_pipe_open_failed",
                ));
            }
            let pipe = unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) };
            let mut service_process_id = 0u32;
            if unsafe {
                GetNamedPipeServerProcessId(pipe.as_raw_handle().cast(), &mut service_process_id)
            } == 0
                || service_process_id == 0
            {
                return Err(AuthorityClientError::last_win32(
                    "authority_client_service_identity_unavailable",
                ));
            }
            let service_handle = unsafe {
                OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
                    0,
                    service_process_id,
                )
            };
            if service_handle.is_null() {
                return Err(AuthorityClientError::last_win32(
                    "authority_client_service_process_unavailable",
                ));
            }
            let service_process =
                unsafe { OwnedHandle::from_raw_handle(service_handle as RawHandle) };
            if unsafe { GetProcessId(service_process.as_raw_handle().cast()) } != service_process_id
                || unsafe { WaitForSingleObject(service_process.as_raw_handle().cast(), 0) }
                    != WAIT_TIMEOUT
            {
                return Err(AuthorityClientError::new(
                    "authority_client_service_process_changed",
                ));
            }
            let observed_path = query_process_path(service_process.as_raw_handle().cast())?;
            let expected_path = layout
                .service_executable_for_generation(generation)
                .map_err(|_| AuthorityClientError::new("authority_client_service_image_invalid"))?;
            if !installed_paths_equal(&observed_path, &expected_path) {
                return Err(AuthorityClientError::new(
                    "authority_client_service_image_mismatch",
                ));
            }
            Ok(Self {
                pipe,
                service_process,
            })
        }

        fn raw_pipe(&self) -> windows_sys::Win32::Foundation::HANDLE {
            self.pipe.as_raw_handle().cast()
        }

        fn raw_service(&self) -> windows_sys::Win32::Foundation::HANDLE {
            self.service_process.as_raw_handle().cast()
        }
    }

    impl Read for FixedAuthorityPipeClient {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            if buffer.is_empty() {
                return Ok(0);
            }
            let length = u32::try_from(buffer.len()).unwrap_or(u32::MAX);
            let mut operation = OverlappedOperation::new()?;
            let mut read = 0u32;
            let started = unsafe {
                ReadFile(
                    self.raw_pipe(),
                    buffer.as_mut_ptr().cast(),
                    length,
                    &mut read,
                    &mut operation.overlapped,
                )
            };
            if started != 0 {
                return Ok(read as usize);
            }
            match unsafe { GetLastError() } {
                ERROR_IO_PENDING => wait_overlapped(
                    self.raw_pipe(),
                    self.raw_service(),
                    &mut operation,
                    IoOperation::Read,
                )
                .map(|value| value as usize),
                ERROR_MORE_DATA if read != 0 => Ok(read as usize),
                ERROR_BROKEN_PIPE => Ok(0),
                error => Err(io::Error::from_raw_os_error(error as i32)),
            }
        }
    }

    impl Write for FixedAuthorityPipeClient {
        fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
            if buffer.is_empty() {
                return Ok(0);
            }
            let length = u32::try_from(buffer.len()).unwrap_or(u32::MAX);
            let mut operation = OverlappedOperation::new()?;
            let mut written = 0u32;
            let started = unsafe {
                WriteFile(
                    self.raw_pipe(),
                    buffer.as_ptr().cast(),
                    length,
                    &mut written,
                    &mut operation.overlapped,
                )
            };
            if started != 0 {
                return Ok(written as usize);
            }
            if unsafe { GetLastError() } != ERROR_IO_PENDING {
                return Err(io::Error::last_os_error());
            }
            wait_overlapped(
                self.raw_pipe(),
                self.raw_service(),
                &mut operation,
                IoOperation::Write,
            )
            .map(|value| value as usize)
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    struct OverlappedOperation {
        event: OwnedHandle,
        overlapped: OVERLAPPED,
    }

    impl OverlappedOperation {
        fn new() -> io::Result<Self> {
            let event = unsafe { CreateEventW(ptr::null(), 1, 0, ptr::null()) };
            if event.is_null() {
                return Err(io::Error::last_os_error());
            }
            let event = unsafe { OwnedHandle::from_raw_handle(event as RawHandle) };
            let mut overlapped = unsafe { zeroed::<OVERLAPPED>() };
            overlapped.hEvent = event.as_raw_handle().cast();
            Ok(Self { event, overlapped })
        }
    }

    #[derive(Clone, Copy)]
    enum IoOperation {
        Read,
        Write,
    }

    fn wait_overlapped(
        pipe: windows_sys::Win32::Foundation::HANDLE,
        service_process: windows_sys::Win32::Foundation::HANDLE,
        operation: &mut OverlappedOperation,
        operation_kind: IoOperation,
    ) -> io::Result<u32> {
        let handles = [operation.event.as_raw_handle().cast(), service_process];
        let wait = unsafe {
            WaitForMultipleObjects(
                handles.len() as u32,
                handles.as_ptr(),
                0,
                CLIENT_IO_TIMEOUT_MS,
            )
        };
        if wait != WAIT_OBJECT_0 {
            unsafe {
                CancelIoEx(pipe, &operation.overlapped);
            }
            let mut cancelled = 0u32;
            unsafe {
                GetOverlappedResult(pipe, &operation.overlapped, &mut cancelled, 1);
            }
            return Err(if wait == WAIT_TIMEOUT {
                io::Error::from(io::ErrorKind::TimedOut)
            } else if wait == WAIT_OBJECT_0 + 1 {
                io::Error::from(io::ErrorKind::ConnectionAborted)
            } else {
                io::Error::last_os_error()
            });
        }
        let mut transferred = 0u32;
        if unsafe { GetOverlappedResult(pipe, &operation.overlapped, &mut transferred, 0) } == 0 {
            let error = unsafe { GetLastError() };
            if matches!(operation_kind, IoOperation::Read)
                && error == ERROR_MORE_DATA
                && transferred != 0
            {
                return Ok(transferred);
            }
            if error == ERROR_OPERATION_ABORTED {
                return Err(io::Error::from(io::ErrorKind::ConnectionAborted));
            }
            return Err(io::Error::from_raw_os_error(error as i32));
        }
        Ok(transferred)
    }

    fn query_process_path(
        process: windows_sys::Win32::Foundation::HANDLE,
    ) -> Result<PathBuf, AuthorityClientError> {
        let mut buffer = vec![0u16; 32_768];
        let mut length = buffer.len() as u32;
        if unsafe { QueryFullProcessImageNameW(process, 0, buffer.as_mut_ptr(), &mut length) } == 0
            || length == 0
            || length as usize >= buffer.len()
        {
            return Err(AuthorityClientError::last_win32(
                "authority_client_service_image_unavailable",
            ));
        }
        Ok(PathBuf::from(OsString::from_wide(
            &buffer[..length as usize],
        )))
    }

    fn wide_null(value: &OsStr) -> Vec<u16> {
        value.encode_wide().chain(std::iter::once(0)).collect()
    }
}

#[cfg(test)]
#[path = "primitive_evidence_authority_client/tests.rs"]
mod tests;
