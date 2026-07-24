use hmac::{Hmac, Mac};
use serde::{de, Deserialize, Deserializer, Serialize};
use serde_json::{Number, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
    fs::File,
    io::{self, Read, Write},
    path::Path,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

pub const REQUEST_SCHEMA: &str = "vrcforge.primitive_origin_attestor_request.v1";
pub const RESPONSE_SCHEMA: &str = "vrcforge.primitive_origin_attestor_response.v1";
pub const TICKET_SCHEMA: &str = "vrcforge.primitive_basis_origin_ticket.v1";
pub const DIAGNOSTIC_SCHEMA: &str = "vrcforge.primitive_basis_origin_diagnostic.v1";
pub const PROOF_ALGORITHM: &str = "ecdsa-p256-sha256-raw-v1";
pub const ORIGIN_TRUST: &str = "pinned_external_supervisor";
pub const POLICY_ID: &str = "vrcforge-primitive-origin-v1";
pub const MAX_FRAME_SIZE: usize = 64 * 1024;

const TICKET_LIFETIME: Duration = Duration::from_secs(15 * 60);
const BLOCKERS: [&str; 5] = [
    "restricted_signer_boundary_not_implemented",
    "private_inner_key_delivery_not_implemented",
    "process_supervision_not_implemented",
    "cleanup_supervision_not_implemented",
    "artifact_copy_toc_tou_binding_not_implemented",
];
const P256_ORDER: [u8; 32] =
    hex_literal_32(b"FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551");
const P256_HALF_ORDER: [u8; 32] =
    hex_literal_32(b"7FFFFFFF800000007FFFFFFFFFFFFFFFDE737D56D38BCF4279DCE5617E3192A8");

type HmacSha256 = Hmac<Sha256>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AttestorError(String);

impl AttestorError {
    fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }

    pub fn code(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for AttestorError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for AttestorError {}

#[derive(Debug, Deserialize)]
#[serde(tag = "command", deny_unknown_fields)]
pub enum Request {
    #[serde(rename = "selfTest")]
    SelfTest { schema: String },
    #[serde(rename = "provision")]
    Provision { schema: String },
    #[serde(rename = "status")]
    Status { schema: String },
    #[serde(rename = "begin")]
    Begin {
        schema: String,
        binding: TicketBinding,
    },
    #[serde(rename = "finalize")]
    Finalize {
        schema: String,
        #[serde(rename = "sessionId")]
        session_id: String,
        #[serde(rename = "innerFinalization")]
        inner_finalization: StrictJsonValue,
    },
    #[serde(rename = "close")]
    Close { schema: String },
    #[serde(rename = "abort")]
    Abort { schema: String },
}

impl Request {
    fn schema(&self) -> &str {
        match self {
            Self::SelfTest { schema }
            | Self::Provision { schema }
            | Self::Status { schema }
            | Self::Begin { schema, .. }
            | Self::Finalize { schema, .. }
            | Self::Close { schema }
            | Self::Abort { schema } => schema,
        }
    }

    fn command(&self) -> &'static str {
        match self {
            Self::SelfTest { .. } => "selfTest",
            Self::Provision { .. } => "provision",
            Self::Status { .. } => "status",
            Self::Begin { .. } => "begin",
            Self::Finalize { .. } => "finalize",
            Self::Close { .. } => "close",
            Self::Abort { .. } => "abort",
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TicketBinding {
    manifest_digest: String,
    portable_digest: String,
    desktop_executable_digest: String,
    backend_executable_digest: String,
    backend_tree_digest: String,
    runner_digest: String,
    unity_package_digest: String,
    packaged_unity_tool_tree_digest: String,
    runtime_unity_tool_tree_digest: String,
    unity_editor_digest: String,
    bridge_launcher_executable_digest: String,
    bridge_listener_executable_digest: String,
    connector_digest: String,
    server_digest: String,
    dependency_set_digest: String,
    fixture_set_descriptor_digest: String,
    fixture_descriptor_digest: String,
    fixture_project_input_digest: String,
    fixture_digest: String,
    runtime_binding_digest: String,
}

impl TicketBinding {
    fn validate(&self) -> Result<(), AttestorError> {
        for digest in [
            &self.manifest_digest,
            &self.portable_digest,
            &self.desktop_executable_digest,
            &self.backend_executable_digest,
            &self.backend_tree_digest,
            &self.runner_digest,
            &self.unity_package_digest,
            &self.packaged_unity_tool_tree_digest,
            &self.runtime_unity_tool_tree_digest,
            &self.unity_editor_digest,
            &self.bridge_launcher_executable_digest,
            &self.bridge_listener_executable_digest,
            &self.connector_digest,
            &self.server_digest,
            &self.dependency_set_digest,
            &self.fixture_set_descriptor_digest,
            &self.fixture_descriptor_digest,
            &self.fixture_project_input_digest,
            &self.fixture_digest,
            &self.runtime_binding_digest,
        ] {
            require_digest(digest)?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct OriginTicket {
    schema: &'static str,
    policy_id: &'static str,
    ticket_id: String,
    run_id: String,
    challenge_digest: String,
    issued_at: String,
    expires_at: String,
    attestor_executable_digest: String,
    #[serde(flatten)]
    binding: TicketBinding,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct KeyStatus {
    present: bool,
    trusted_boundary_ready: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    signer_key_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    signer_public_key: Option<String>,
}

impl KeyStatus {
    fn absent() -> Self {
        Self {
            present: false,
            trusted_boundary_ready: false,
            signer_key_id: None,
            signer_public_key: None,
        }
    }
}

#[derive(Debug)]
pub struct SignedBytes {
    signer_key_id: String,
    signature: [u8; 64],
}

pub trait KeyStore {
    fn self_test(&mut self) -> Result<(), AttestorError>;
    fn provision(&mut self) -> Result<(KeyStatus, bool), AttestorError>;
    fn status(&mut self) -> Result<KeyStatus, AttestorError>;
    fn sign(&mut self, message: &[u8]) -> Result<SignedBytes, AttestorError>;
}

#[derive(Debug)]
struct Sensitive32([u8; 32]);

impl Sensitive32 {
    fn as_slice(&self) -> &[u8] {
        &self.0
    }

    fn clear(&mut self) {
        for byte in &mut self.0 {
            // Volatile stores prevent the explicit clear from being optimized away.
            unsafe { std::ptr::write_volatile(byte, 0) };
        }
        std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
    }
}

impl Drop for Sensitive32 {
    fn drop(&mut self) {
        self.clear();
    }
}

struct ActiveRun {
    session_id: String,
    ticket: OriginTicket,
    ticket_digest: String,
    signer_key_id: String,
    expires_at: SystemTime,
    inner_mac_key: Sensitive32,
    _challenge: Sensitive32,
}

enum Phase {
    Open,
    Active(ActiveRun),
    Terminal,
}

type RandomFill = fn(&mut [u8]) -> Result<(), AttestorError>;
type Clock = fn() -> Result<SystemTime, AttestorError>;

pub struct AttestorSession<K: KeyStore> {
    key_store: K,
    attestor_executable_digest: String,
    phase: Phase,
    seen: BTreeSet<&'static str>,
    random_fill: RandomFill,
    clock: Clock,
}

impl<K: KeyStore> AttestorSession<K> {
    pub fn new(key_store: K, attestor_executable_digest: String) -> Result<Self, AttestorError> {
        require_digest(&attestor_executable_digest)?;
        Ok(Self {
            key_store,
            attestor_executable_digest,
            phase: Phase::Open,
            seen: BTreeSet::new(),
            random_fill: fill_random,
            clock: current_time,
        })
    }

    #[cfg(test)]
    fn with_test_sources(
        key_store: K,
        attestor_executable_digest: String,
        random_fill: RandomFill,
        clock: Clock,
    ) -> Result<Self, AttestorError> {
        let mut session = Self::new(key_store, attestor_executable_digest)?;
        session.random_fill = random_fill;
        session.clock = clock;
        Ok(session)
    }

    pub fn is_terminal(&self) -> bool {
        matches!(self.phase, Phase::Terminal)
    }

    pub fn handle(&mut self, request: Request) -> Result<Value, AttestorError> {
        if self.is_terminal() {
            return Err(AttestorError::new("session_terminated"));
        }
        if request.schema() != REQUEST_SCHEMA {
            return Err(AttestorError::new("request_schema_mismatch"));
        }
        let command = request.command();
        if self.seen.contains(command) {
            return Err(AttestorError::new("duplicate_command"));
        }
        self.seen.insert(command);

        match request {
            Request::SelfTest { .. } => {
                if !matches!(self.phase, Phase::Open) {
                    return Err(AttestorError::new("command_out_of_order"));
                }
                self.key_store.self_test()?;
                Ok(success_response(
                    "selfTest",
                    serde_json::json!({ "ready": true }),
                ))
            }
            Request::Provision { .. } => {
                if !matches!(self.phase, Phase::Open) {
                    return Err(AttestorError::new("command_out_of_order"));
                }
                let (status, created) = self.key_store.provision()?;
                Ok(success_response(
                    "provision",
                    serde_json::json!({ "created": created, "key": status }),
                ))
            }
            Request::Status { .. } => {
                if !matches!(self.phase, Phase::Open) {
                    return Err(AttestorError::new("command_out_of_order"));
                }
                let status = self.key_store.status()?;
                Ok(success_response(
                    "status",
                    serde_json::json!({ "key": status }),
                ))
            }
            Request::Begin { binding, .. } => self.begin(binding),
            Request::Finalize {
                session_id,
                inner_finalization,
                ..
            } => self.finalize(session_id, inner_finalization),
            Request::Close { .. } => {
                if matches!(self.phase, Phase::Active(_)) {
                    return Err(AttestorError::new("active_run_not_finalized"));
                }
                self.phase = Phase::Terminal;
                Ok(success_response(
                    "close",
                    serde_json::json!({ "closed": true }),
                ))
            }
            Request::Abort { .. } => {
                let previous = std::mem::replace(&mut self.phase, Phase::Terminal);
                let active = matches!(previous, Phase::Active(_));
                drop(previous);
                Ok(success_response(
                    "abort",
                    serde_json::json!({
                        "terminal": true,
                        "activeRunDiscarded": active,
                        "ownedProcessCount": 0,
                        "processCleanupRequired": false,
                        "portsReleased": false,
                        "blockers": BLOCKERS,
                    }),
                ))
            }
        }
    }

    fn begin(&mut self, binding: TicketBinding) -> Result<Value, AttestorError> {
        if !matches!(self.phase, Phase::Open) {
            return Err(AttestorError::new("command_out_of_order"));
        }
        binding.validate()?;
        let status = self.key_store.status()?;
        if !status.trusted_boundary_ready {
            return Err(AttestorError::new("restricted_signer_boundary_required"));
        }
        let signer_key_id = status
            .signer_key_id
            .filter(|_| status.present)
            .ok_or_else(|| AttestorError::new("signing_key_not_provisioned"))?;
        require_digest(&signer_key_id)?;

        let mut challenge_nonce = [0u8; 16];
        let mut inner_mac_key = Sensitive32([0; 32]);
        let mut ticket_nonce = [0u8; 16];
        (self.random_fill)(&mut challenge_nonce)?;
        (self.random_fill)(&mut inner_mac_key.0)?;
        (self.random_fill)(&mut ticket_nonce)?;
        let mut challenge_bytes = [0u8; 32];
        hex_encode_fixed(&challenge_nonce, &mut challenge_bytes);
        clear_bytes(&mut challenge_nonce);
        let challenge = Sensitive32(challenge_bytes);
        let challenge_digest = sha256_hex(challenge.as_slice());
        let session_id = format!("primitive-origin-{}", hex_encode(&ticket_nonce));
        let issued = (self.clock)()?;
        let expires = issued
            .checked_add(TICKET_LIFETIME)
            .ok_or_else(|| AttestorError::new("clock_out_of_range"))?;
        let ticket = OriginTicket {
            schema: TICKET_SCHEMA,
            policy_id: POLICY_ID,
            ticket_id: format!("ticket-{}", hex_encode(&ticket_nonce)),
            run_id: format!("primitive-live-{}", &challenge_digest[..32]),
            challenge_digest,
            issued_at: format_utc(issued)?,
            expires_at: format_utc(expires)?,
            attestor_executable_digest: self.attestor_executable_digest.clone(),
            binding,
        };
        let ticket_value = serde_json::to_value(&ticket)
            .map_err(|_| AttestorError::new("ticket_serialization_failed"))?;
        let ticket_digest = sha256_hex(&canonical_json(&ticket_value)?);
        let public_ticket = ticket_value.clone();
        self.phase = Phase::Active(ActiveRun {
            session_id: session_id.clone(),
            ticket,
            ticket_digest: ticket_digest.clone(),
            signer_key_id: signer_key_id.clone(),
            expires_at: expires,
            inner_mac_key,
            _challenge: challenge,
        });

        Ok(success_response(
            "begin",
            serde_json::json!({
                "sessionId": session_id,
                "signerKeyId": signer_key_id,
                "ticket": public_ticket,
                "ticketDigest": ticket_digest,
                "privateDeliveryReady": false,
                "blockers": BLOCKERS,
            }),
        ))
    }

    fn finalize(
        &mut self,
        session_id: String,
        inner_finalization: StrictJsonValue,
    ) -> Result<Value, AttestorError> {
        let phase = std::mem::replace(&mut self.phase, Phase::Terminal);
        let active = match phase {
            Phase::Active(active) => active,
            other => {
                self.phase = other;
                return Err(AttestorError::new("command_out_of_order"));
            }
        };
        if session_id != active.session_id {
            return Err(AttestorError::new("session_id_mismatch"));
        }
        let finalized_at = (self.clock)()?;
        if finalized_at > active.expires_at {
            return Err(AttestorError::new("ticket_expired"));
        }
        verify_inner_mac(&inner_finalization.0, active.inner_mac_key.as_slice())?;
        let finalization_digest = sha256_hex(&canonical_json(&inner_finalization.0)?);
        let signed_at = format_utc(finalized_at)?;
        let unsigned = BlockedDiagnosticUnsigned {
            schema: DIAGNOSTIC_SCHEMA,
            proof_algorithm: PROOF_ALGORITHM,
            origin_trust: ORIGIN_TRUST,
            signer_key_id: active.signer_key_id.clone(),
            attestor_executable_digest: self.attestor_executable_digest.clone(),
            ticket: active.ticket,
            ticket_digest: active.ticket_digest,
            finalization_digest,
            signed_at,
            status: "blocked",
            blockers: BLOCKERS,
        };
        let unsigned_value = serde_json::to_value(&unsigned)
            .map_err(|_| AttestorError::new("diagnostic_serialization_failed"))?;
        let signed = self.key_store.sign(&canonical_json(&unsigned_value)?)?;
        if signed.signer_key_id != unsigned.signer_key_id {
            return Err(AttestorError::new("signer_identity_changed"));
        }
        let signature = normalize_low_s(signed.signature)?;
        let mut diagnostic = unsigned_value;
        diagnostic
            .as_object_mut()
            .ok_or_else(|| AttestorError::new("diagnostic_serialization_failed"))?
            .insert(
                "signature".to_string(),
                Value::String(base64url_encode(&signature)),
            );
        Ok(success_response(
            "finalize",
            serde_json::json!({ "diagnostic": diagnostic }),
        ))
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct BlockedDiagnosticUnsigned {
    schema: &'static str,
    proof_algorithm: &'static str,
    origin_trust: &'static str,
    signer_key_id: String,
    attestor_executable_digest: String,
    ticket: OriginTicket,
    ticket_digest: String,
    finalization_digest: String,
    signed_at: String,
    status: &'static str,
    blockers: [&'static str; 5],
}

#[derive(Debug, Clone)]
pub struct StrictJsonValue(Value);

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
        formatter.write_str("JSON without floating-point numbers or duplicate object keys")
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
        Err(E::custom("floating-point JSON is not accepted"))
    }

    fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Ok(Value::String(value.to_string()))
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
        let mut values = BTreeMap::<String, Value>::new();
        while let Some((key, value)) = map.next_entry::<String, StrictJsonValue>()? {
            if values.insert(key, value.0).is_some() {
                return Err(de::Error::custom("duplicate JSON object key"));
            }
        }
        Ok(Value::Object(values.into_iter().collect()))
    }
}

pub fn run_protocol<R: Read, W: Write, K: KeyStore>(
    reader: &mut R,
    writer: &mut W,
    session: &mut AttestorSession<K>,
) -> Result<(), AttestorError> {
    loop {
        let request = match read_request_frame(reader) {
            Ok(Some(request)) => request,
            Ok(None) => {
                write_error_frame(writer, "early_eof")?;
                return Err(AttestorError::new("early_eof"));
            }
            Err(error) => {
                write_error_frame(writer, error.code())?;
                return Err(error);
            }
        };
        match session.handle(request) {
            Ok(response) => write_json_frame(writer, &response)?,
            Err(error) => {
                write_error_frame(writer, error.code())?;
                return Err(error);
            }
        }
        if session.is_terminal() {
            return Ok(());
        }
    }
}

fn read_request_frame<R: Read>(reader: &mut R) -> Result<Option<Request>, AttestorError> {
    let mut header = [0u8; 4];
    let mut first = [0u8; 1];
    match reader.read(&mut first) {
        Ok(0) => return Ok(None),
        Ok(_) => header[0] = first[0],
        Err(error) if error.kind() == io::ErrorKind::Interrupted => {
            return read_request_frame(reader)
        }
        Err(_) => return Err(AttestorError::new("frame_read_failed")),
    }
    reader
        .read_exact(&mut header[1..])
        .map_err(|_| AttestorError::new("truncated_frame_header"))?;
    let size = u32::from_be_bytes(header) as usize;
    if size == 0 || size > MAX_FRAME_SIZE {
        return Err(AttestorError::new("frame_size_rejected"));
    }
    let mut payload = vec![0u8; size];
    reader
        .read_exact(&mut payload)
        .map_err(|_| AttestorError::new("truncated_frame_payload"))?;
    let request = serde_json::from_slice::<Request>(&payload)
        .map_err(|_| AttestorError::new("request_json_rejected"))?;
    Ok(Some(request))
}

fn write_error_frame<W: Write>(writer: &mut W, code: &str) -> Result<(), AttestorError> {
    write_json_frame(
        writer,
        &serde_json::json!({
            "schema": RESPONSE_SCHEMA,
            "ok": false,
            "error": code,
        }),
    )
}

fn write_json_frame<W: Write>(writer: &mut W, value: &Value) -> Result<(), AttestorError> {
    let payload = serde_json::to_vec(value)
        .map_err(|_| AttestorError::new("response_serialization_failed"))?;
    if payload.is_empty() || payload.len() > MAX_FRAME_SIZE {
        return Err(AttestorError::new("response_size_rejected"));
    }
    writer
        .write_all(&(payload.len() as u32).to_be_bytes())
        .and_then(|_| writer.write_all(&payload))
        .and_then(|_| writer.flush())
        .map_err(|_| AttestorError::new("frame_write_failed"))
}

fn success_response(command: &str, result: Value) -> Value {
    serde_json::json!({
        "schema": RESPONSE_SCHEMA,
        "ok": true,
        "command": command,
        "result": result,
    })
}

fn verify_inner_mac(value: &Value, key: &[u8]) -> Result<(), AttestorError> {
    let mut attestation = value
        .as_object()
        .and_then(|object| object.get("attestation"))
        .and_then(Value::as_object)
        .cloned()
        .ok_or_else(|| AttestorError::new("inner_attestation_missing"))?;
    let proof = attestation
        .remove("proof")
        .and_then(|value| value.as_str().map(str::to_owned))
        .ok_or_else(|| AttestorError::new("inner_proof_missing"))?;
    let proof = decode_digest(&proof)?;
    let unsigned = Value::Object(attestation);
    let mut verifier = HmacSha256::new_from_slice(key)
        .map_err(|_| AttestorError::new("inner_mac_setup_failed"))?;
    verifier.update(&canonical_json(&unsigned)?);
    verifier
        .verify_slice(&proof)
        .map_err(|_| AttestorError::new("inner_proof_mismatch"))
}

pub fn canonical_json(value: &Value) -> Result<Vec<u8>, AttestorError> {
    let mut output = Vec::new();
    write_canonical(value, &mut output)?;
    Ok(output)
}

fn write_canonical(value: &Value, output: &mut Vec<u8>) -> Result<(), AttestorError> {
    match value {
        Value::Null => output.extend_from_slice(b"null"),
        Value::Bool(true) => output.extend_from_slice(b"true"),
        Value::Bool(false) => output.extend_from_slice(b"false"),
        Value::Number(number) if number.is_i64() || number.is_u64() => {
            output.extend_from_slice(number.to_string().as_bytes())
        }
        Value::Number(_) => return Err(AttestorError::new("floating_point_json_rejected")),
        Value::String(value) => write_ascii_json_string(value, output),
        Value::Array(values) => {
            output.push(b'[');
            for (index, value) in values.iter().enumerate() {
                if index > 0 {
                    output.push(b',');
                }
                write_canonical(value, output)?;
            }
            output.push(b']');
        }
        Value::Object(values) => {
            output.push(b'{');
            let mut entries = values.iter().collect::<Vec<_>>();
            entries.sort_by(|left, right| left.0.cmp(right.0));
            for (index, (key, value)) in entries.into_iter().enumerate() {
                if index > 0 {
                    output.push(b',');
                }
                write_ascii_json_string(key, output);
                output.push(b':');
                write_canonical(value, output)?;
            }
            output.push(b'}');
        }
    }
    Ok(())
}

fn write_ascii_json_string(value: &str, output: &mut Vec<u8>) {
    output.push(b'"');
    for character in value.chars() {
        match character {
            '"' => output.extend_from_slice(br#"\""#),
            '\\' => output.extend_from_slice(br#"\\"#),
            '\u{08}' => output.extend_from_slice(br#"\b"#),
            '\u{0c}' => output.extend_from_slice(br#"\f"#),
            '\n' => output.extend_from_slice(br#"\n"#),
            '\r' => output.extend_from_slice(br#"\r"#),
            '\t' => output.extend_from_slice(br#"\t"#),
            character if character <= '\u{1f}' || character >= '\u{7f}' => {
                let code = character as u32;
                if code <= 0xffff {
                    output.extend_from_slice(format!("\\u{code:04x}").as_bytes());
                } else {
                    let adjusted = code - 0x1_0000;
                    let high = 0xd800 + (adjusted >> 10);
                    let low = 0xdc00 + (adjusted & 0x3ff);
                    output.extend_from_slice(format!("\\u{high:04x}\\u{low:04x}").as_bytes());
                }
            }
            character => {
                let mut buffer = [0u8; 4];
                output.extend_from_slice(character.encode_utf8(&mut buffer).as_bytes());
            }
        }
    }
    output.push(b'"');
}

pub fn normalize_low_s(mut signature: [u8; 64]) -> Result<[u8; 64], AttestorError> {
    let r: [u8; 32] = signature[..32]
        .try_into()
        .map_err(|_| AttestorError::new("signature_length_invalid"))?;
    let s: [u8; 32] = signature[32..]
        .try_into()
        .map_err(|_| AttestorError::new("signature_length_invalid"))?;
    if is_zero(&r) || r >= P256_ORDER || is_zero(&s) || s >= P256_ORDER {
        return Err(AttestorError::new("signature_scalar_invalid"));
    }
    if s > P256_HALF_ORDER {
        signature[32..].copy_from_slice(&subtract_be(&P256_ORDER, &s));
    }
    Ok(signature)
}

fn subtract_be(left: &[u8; 32], right: &[u8; 32]) -> [u8; 32] {
    let mut output = [0u8; 32];
    let mut borrow = 0i16;
    for index in (0..32).rev() {
        let difference = left[index] as i16 - right[index] as i16 - borrow;
        if difference < 0 {
            output[index] = (difference + 256) as u8;
            borrow = 1;
        } else {
            output[index] = difference as u8;
            borrow = 0;
        }
    }
    output
}

fn is_zero(value: &[u8; 32]) -> bool {
    value.iter().all(|byte| *byte == 0)
}

fn fill_random(output: &mut [u8]) -> Result<(), AttestorError> {
    getrandom::fill(output).map_err(|_| AttestorError::new("secure_random_unavailable"))
}

fn current_time() -> Result<SystemTime, AttestorError> {
    Ok(SystemTime::now())
}

fn format_utc(value: SystemTime) -> Result<String, AttestorError> {
    let duration = value
        .duration_since(UNIX_EPOCH)
        .map_err(|_| AttestorError::new("clock_out_of_range"))?;
    let seconds = duration.as_secs();
    let days = (seconds / 86_400) as i64;
    let second_of_day = seconds % 86_400;
    let (year, month, day) = civil_from_days(days);
    if !(1970..=9999).contains(&year) {
        return Err(AttestorError::new("clock_out_of_range"));
    }
    let hour = second_of_day / 3_600;
    let minute = (second_of_day % 3_600) / 60;
    let second = second_of_day % 60;
    let millis = duration.subsec_millis();
    Ok(format!(
        "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{millis:03}Z"
    ))
}

fn civil_from_days(days_since_epoch: i64) -> (i64, i64, i64) {
    let shifted = days_since_epoch + 719_468;
    let era = if shifted >= 0 {
        shifted
    } else {
        shifted - 146_096
    } / 146_097;
    let day_of_era = shifted - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let mut year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    if month <= 2 {
        year += 1;
    }
    (year, month, day)
}

pub fn executable_sha256(path: &Path) -> Result<String, AttestorError> {
    let mut file = File::open(path).map_err(|_| AttestorError::new("executable_open_failed"))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|_| AttestorError::new("executable_read_failed"))?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(hex_encode(&hasher.finalize()))
}

fn require_digest(value: &str) -> Result<(), AttestorError> {
    if value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        Ok(())
    } else {
        Err(AttestorError::new("digest_rejected"))
    }
}

fn decode_digest(value: &str) -> Result<[u8; 32], AttestorError> {
    require_digest(value)?;
    let mut output = [0u8; 32];
    for (index, byte) in output.iter_mut().enumerate() {
        *byte = (hex_value(value.as_bytes()[index * 2])? << 4)
            | hex_value(value.as_bytes()[index * 2 + 1])?;
    }
    Ok(output)
}

fn hex_value(value: u8) -> Result<u8, AttestorError> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err(AttestorError::new("hex_rejected")),
    }
}

fn sha256_hex(value: &[u8]) -> String {
    hex_encode(&Sha256::digest(value))
}

fn hex_encode(value: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn hex_encode_fixed(value: &[u8], output: &mut [u8]) {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    assert_eq!(output.len(), value.len() * 2);
    for (index, byte) in value.iter().enumerate() {
        output[index * 2] = HEX[(byte >> 4) as usize];
        output[index * 2 + 1] = HEX[(byte & 0x0f) as usize];
    }
}

fn clear_bytes(value: &mut [u8]) {
    for byte in value {
        unsafe { std::ptr::write_volatile(byte, 0) };
    }
    std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
}

fn base64url_encode(value: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut output = String::with_capacity((value.len() * 4).div_ceil(3));
    let mut index = 0;
    while index + 3 <= value.len() {
        let bits = ((value[index] as u32) << 16)
            | ((value[index + 1] as u32) << 8)
            | value[index + 2] as u32;
        output.push(TABLE[((bits >> 18) & 0x3f) as usize] as char);
        output.push(TABLE[((bits >> 12) & 0x3f) as usize] as char);
        output.push(TABLE[((bits >> 6) & 0x3f) as usize] as char);
        output.push(TABLE[(bits & 0x3f) as usize] as char);
        index += 3;
    }
    let remaining = value.len() - index;
    if remaining == 1 {
        let bits = (value[index] as u32) << 16;
        output.push(TABLE[((bits >> 18) & 0x3f) as usize] as char);
        output.push(TABLE[((bits >> 12) & 0x3f) as usize] as char);
    } else if remaining == 2 {
        let bits = ((value[index] as u32) << 16) | ((value[index + 1] as u32) << 8);
        output.push(TABLE[((bits >> 18) & 0x3f) as usize] as char);
        output.push(TABLE[((bits >> 12) & 0x3f) as usize] as char);
        output.push(TABLE[((bits >> 6) & 0x3f) as usize] as char);
    }
    output
}

const fn hex_literal_32(value: &[u8; 64]) -> [u8; 32] {
    let mut output = [0u8; 32];
    let mut index = 0;
    while index < 32 {
        output[index] =
            (const_hex_value(value[index * 2]) << 4) | const_hex_value(value[index * 2 + 1]);
        index += 1;
    }
    output
}

const fn const_hex_value(value: u8) -> u8 {
    match value {
        b'0'..=b'9' => value - b'0',
        b'A'..=b'F' => value - b'A' + 10,
        _ => 0,
    }
}

#[cfg(windows)]
pub struct CngKeyStore;

#[cfg(windows)]
impl CngKeyStore {
    pub fn new() -> Self {
        Self
    }
}

#[cfg(windows)]
impl KeyStore for CngKeyStore {
    fn self_test(&mut self) -> Result<(), AttestorError> {
        let provider = cng::Provider::open()?;
        provider.require_p256()?;
        let mut probe = [0u8; 32];
        fill_random(&mut probe)?;
        Sensitive32(probe).clear();
        Ok(())
    }

    fn provision(&mut self) -> Result<(KeyStatus, bool), AttestorError> {
        Err(AttestorError::new(
            "restricted_signer_provisioning_required",
        ))
    }

    fn status(&mut self) -> Result<KeyStatus, AttestorError> {
        let provider = cng::Provider::open()?;
        match provider.open_key()? {
            Some(key) => key.status(),
            None => Ok(KeyStatus::absent()),
        }
    }

    fn sign(&mut self, message: &[u8]) -> Result<SignedBytes, AttestorError> {
        let provider = cng::Provider::open()?;
        let key = provider
            .open_key()?
            .ok_or_else(|| AttestorError::new("signing_key_not_provisioned"))?;
        key.sign(message)
    }
}

#[cfg(windows)]
mod cng {
    use super::*;
    use std::ptr::{null, null_mut};
    use windows_sys::{
        core::w,
        Win32::{
            Foundation::NTE_BAD_KEYSET,
            Security::Cryptography::{
                NCryptExportKey, NCryptFreeObject, NCryptIsAlgSupported, NCryptOpenKey,
                NCryptOpenStorageProvider, NCryptSignHash, BCRYPT_ECCPUBLIC_BLOB,
                BCRYPT_ECDSA_P256_ALGORITHM, BCRYPT_ECDSA_PUBLIC_P256_MAGIC,
                MS_KEY_STORAGE_PROVIDER, NCRYPT_KEY_HANDLE, NCRYPT_PROV_HANDLE, NCRYPT_SILENT_FLAG,
            },
        },
    };

    pub(super) struct Provider(NCRYPT_PROV_HANDLE);

    impl Provider {
        pub(super) fn open() -> Result<Self, AttestorError> {
            let mut handle = 0;
            let status =
                unsafe { NCryptOpenStorageProvider(&mut handle, MS_KEY_STORAGE_PROVIDER, 0) };
            check(status, "key_provider_unavailable")?;
            if handle == 0 {
                return Err(AttestorError::new("key_provider_unavailable"));
            }
            Ok(Self(handle))
        }

        pub(super) fn open_key(&self) -> Result<Option<Key>, AttestorError> {
            let mut handle = 0;
            let status = unsafe {
                NCryptOpenKey(
                    self.0,
                    &mut handle,
                    w!("VRCForge Primitive Origin Attestor P256 v1"),
                    0,
                    NCRYPT_SILENT_FLAG,
                )
            };
            if status == NTE_BAD_KEYSET {
                return Ok(None);
            }
            check(status, "signing_key_open_failed")?;
            if handle == 0 {
                return Err(AttestorError::new("signing_key_open_failed"));
            }
            Ok(Some(Key(handle)))
        }

        pub(super) fn require_p256(&self) -> Result<(), AttestorError> {
            let status = unsafe { NCryptIsAlgSupported(self.0, BCRYPT_ECDSA_P256_ALGORITHM, 0) };
            check(status, "p256_signing_unavailable")
        }
    }

    impl Drop for Provider {
        fn drop(&mut self) {
            if self.0 != 0 {
                unsafe { NCryptFreeObject(self.0) };
                self.0 = 0;
            }
        }
    }

    pub(super) struct Key(NCRYPT_KEY_HANDLE);

    impl Key {
        fn public_key(&self) -> Result<[u8; 65], AttestorError> {
            let mut size = 0u32;
            let status = unsafe {
                NCryptExportKey(
                    self.0,
                    0,
                    BCRYPT_ECCPUBLIC_BLOB,
                    null(),
                    null_mut(),
                    0,
                    &mut size,
                    0,
                )
            };
            check(status, "signer_public_key_export_failed")?;
            if size != 72 {
                return Err(AttestorError::new("signer_public_key_shape_invalid"));
            }
            let mut blob = [0u8; 72];
            let status = unsafe {
                NCryptExportKey(
                    self.0,
                    0,
                    BCRYPT_ECCPUBLIC_BLOB,
                    null(),
                    blob.as_mut_ptr(),
                    blob.len() as u32,
                    &mut size,
                    0,
                )
            };
            check(status, "signer_public_key_export_failed")?;
            let magic = u32::from_le_bytes(blob[..4].try_into().unwrap_or_default());
            let coordinate_size = u32::from_le_bytes(blob[4..8].try_into().unwrap_or_default());
            if size != 72 || magic != BCRYPT_ECDSA_PUBLIC_P256_MAGIC || coordinate_size != 32 {
                return Err(AttestorError::new("signer_public_key_shape_invalid"));
            }
            let mut public_key = [0u8; 65];
            public_key[0] = 0x04;
            public_key[1..].copy_from_slice(&blob[8..]);
            Ok(public_key)
        }

        pub(super) fn status(&self) -> Result<KeyStatus, AttestorError> {
            let public_key = self.public_key()?;
            Ok(KeyStatus {
                present: true,
                trusted_boundary_ready: false,
                signer_key_id: Some(sha256_hex(&public_key)),
                signer_public_key: Some(base64url_encode(&public_key)),
            })
        }

        pub(super) fn sign(&self, message: &[u8]) -> Result<SignedBytes, AttestorError> {
            let public_key = self.public_key()?;
            let signer_key_id = sha256_hex(&public_key);
            let hash = Sha256::digest(message);
            let mut signature = [0u8; 64];
            let mut size = 0u32;
            let status = unsafe {
                NCryptSignHash(
                    self.0,
                    null(),
                    hash.as_ptr(),
                    hash.len() as u32,
                    signature.as_mut_ptr(),
                    signature.len() as u32,
                    &mut size,
                    NCRYPT_SILENT_FLAG,
                )
            };
            check(status, "signing_failed")?;
            if size != 64 {
                return Err(AttestorError::new("signature_length_invalid"));
            }
            Ok(SignedBytes {
                signer_key_id,
                signature,
            })
        }
    }

    impl Drop for Key {
        fn drop(&mut self) {
            if self.0 != 0 {
                unsafe { NCryptFreeObject(self.0) };
                self.0 = 0;
            }
        }
    }

    fn check(status: i32, code: &'static str) -> Result<(), AttestorError> {
        if status >= 0 {
            Ok(())
        } else {
            Err(AttestorError::new(code))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    struct FakeKeyStore {
        present: bool,
        trusted_boundary_ready: bool,
        signer_key_id: String,
        signature: [u8; 64],
    }

    impl FakeKeyStore {
        fn ready() -> Self {
            let mut signature = [0u8; 64];
            signature[31] = 1;
            let high_s = subtract_be(
                &P256_ORDER,
                &[
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 1,
                ],
            );
            signature[32..].copy_from_slice(&high_s);
            Self {
                present: true,
                trusted_boundary_ready: true,
                signer_key_id: "11".repeat(32),
                signature,
            }
        }
    }

    impl KeyStore for FakeKeyStore {
        fn self_test(&mut self) -> Result<(), AttestorError> {
            Ok(())
        }

        fn provision(&mut self) -> Result<(KeyStatus, bool), AttestorError> {
            let created = !self.present;
            self.present = true;
            Ok((self.status()?, created))
        }

        fn status(&mut self) -> Result<KeyStatus, AttestorError> {
            if self.present {
                Ok(KeyStatus {
                    present: true,
                    trusted_boundary_ready: self.trusted_boundary_ready,
                    signer_key_id: Some(self.signer_key_id.clone()),
                    signer_public_key: Some(base64url_encode(&[4u8; 65])),
                })
            } else {
                Ok(KeyStatus::absent())
            }
        }

        fn sign(&mut self, _message: &[u8]) -> Result<SignedBytes, AttestorError> {
            Ok(SignedBytes {
                signer_key_id: self.signer_key_id.clone(),
                signature: self.signature,
            })
        }
    }

    fn test_random(output: &mut [u8]) -> Result<(), AttestorError> {
        output.fill(7);
        Ok(())
    }

    fn test_clock() -> Result<SystemTime, AttestorError> {
        Ok(UNIX_EPOCH + Duration::from_millis(1_700_000_000_123))
    }

    fn session() -> AttestorSession<FakeKeyStore> {
        AttestorSession::with_test_sources(
            FakeKeyStore::ready(),
            "22".repeat(32),
            test_random,
            test_clock,
        )
        .unwrap()
    }

    fn binding_value() -> Value {
        serde_json::json!({
            "manifestDigest": "aa".repeat(32),
            "portableDigest": "ab".repeat(32),
            "desktopExecutableDigest": "ac".repeat(32),
            "backendExecutableDigest": "ad".repeat(32),
            "backendTreeDigest": "ae".repeat(32),
            "runnerDigest": "af".repeat(32),
            "unityPackageDigest": "ba".repeat(32),
            "packagedUnityToolTreeDigest": "bb".repeat(32),
            "runtimeUnityToolTreeDigest": "bc".repeat(32),
            "unityEditorDigest": "bd".repeat(32),
            "bridgeLauncherExecutableDigest": "be".repeat(32),
            "bridgeListenerExecutableDigest": "bf".repeat(32),
            "connectorDigest": "ca".repeat(32),
            "serverDigest": "cb".repeat(32),
            "dependencySetDigest": "cc".repeat(32),
            "fixtureSetDescriptorDigest": "cd".repeat(32),
            "fixtureDescriptorDigest": "ce".repeat(32),
            "fixtureProjectInputDigest": "cf".repeat(32),
            "fixtureDigest": "da".repeat(32),
            "runtimeBindingDigest": "db".repeat(32),
        })
    }

    fn begin_request() -> Request {
        serde_json::from_value(serde_json::json!({
            "schema": REQUEST_SCHEMA,
            "command": "begin",
            "binding": binding_value(),
        }))
        .unwrap()
    }

    fn begin(session: &mut AttestorSession<FakeKeyStore>) -> (String, Value) {
        let response = session.handle(begin_request()).unwrap();
        let result = &response["result"];
        (result["sessionId"].as_str().unwrap().to_string(), response)
    }

    fn inner_finalization() -> StrictJsonValue {
        let mut attestation = serde_json::json!({
            "runId": "test",
            "message": "\u{1f680}",
        });
        let unsigned = canonical_json(&attestation).unwrap();
        let mut mac = HmacSha256::new_from_slice(&[7u8; 32]).unwrap();
        mac.update(&unsigned);
        let proof = hex_encode(&mac.finalize().into_bytes());
        attestation["proof"] = Value::String(proof);
        StrictJsonValue(serde_json::json!({ "attestation": attestation }))
    }

    #[test]
    fn canonical_json_matches_ascii_sorted_contract() {
        let value = serde_json::json!({ "z": "\u{1f680}", "a": [2, "é", true] });
        assert_eq!(
            String::from_utf8(canonical_json(&value).unwrap()).unwrap(),
            r#"{"a":[2,"\u00e9",true],"z":"\ud83d\ude80"}"#
        );
        assert!(canonical_json(&serde_json::json!(1.25)).is_err());
    }

    #[test]
    fn strict_inner_json_rejects_duplicate_keys_and_floats() {
        let duplicate = format!(
            r#"{{"schema":"{}","command":"finalize","sessionId":"x","innerFinalization":{{"a":1,"a":2}}}}"#,
            REQUEST_SCHEMA
        );
        assert!(serde_json::from_str::<Request>(&duplicate).is_err());
        let float = format!(
            r#"{{"schema":"{}","command":"finalize","sessionId":"x","innerFinalization":{{"a":1.5}}}}"#,
            REQUEST_SCHEMA
        );
        assert!(serde_json::from_str::<Request>(&float).is_err());
    }

    #[test]
    fn request_rejects_unknown_fields_and_duplicate_commands() {
        let unknown = serde_json::json!({
            "schema": REQUEST_SCHEMA,
            "command": "status",
            "unexpected": true,
        });
        assert!(serde_json::from_value::<Request>(unknown).is_err());

        let mut session = session();
        session
            .handle(Request::Status {
                schema: REQUEST_SCHEMA.to_string(),
            })
            .unwrap();
        let error = session
            .handle(Request::Status {
                schema: REQUEST_SCHEMA.to_string(),
            })
            .unwrap_err();
        assert_eq!(error.code(), "duplicate_command");
    }

    #[test]
    fn begin_keeps_secrets_private_and_cannot_claim_trusted_origin() {
        let mut session = session();
        let (_, response) = begin(&mut session);
        let encoded = serde_json::to_string(&response).unwrap();
        assert!(!encoded.contains("innerMacKey"));
        assert!(!encoded.contains("originVerified"));
        assert_eq!(response["result"]["privateDeliveryReady"], false);
        assert_eq!(response["result"]["ticket"]["schema"], TICKET_SCHEMA);
        assert_eq!(response["result"]["ticket"]["policyId"], POLICY_ID);
        assert_eq!(
            response["result"]["ticket"]["challengeDigest"],
            sha256_hex(b"07070707070707070707070707070707")
        );
        assert!(response["result"]["ticket"]["runId"]
            .as_str()
            .unwrap()
            .starts_with("primitive-live-"));
    }

    #[test]
    fn begin_rejects_a_same_identity_signing_key() {
        let mut store = FakeKeyStore::ready();
        store.trusted_boundary_ready = false;
        let mut session =
            AttestorSession::with_test_sources(store, "22".repeat(32), test_random, test_clock)
                .unwrap();
        let error = session.handle(begin_request()).unwrap_err();
        assert_eq!(error.code(), "restricted_signer_boundary_required");
    }

    #[cfg(windows)]
    #[test]
    fn production_provision_never_creates_a_same_identity_key() {
        let mut store = CngKeyStore::new();
        let error = store.provision().unwrap_err();
        assert_eq!(error.code(), "restricted_signer_provisioning_required");
    }

    #[test]
    fn finalize_is_one_shot_low_s_and_only_emits_blocked_diagnostic() {
        let mut session = session();
        let (session_id, _) = begin(&mut session);
        let response = session
            .handle(Request::Finalize {
                schema: REQUEST_SCHEMA.to_string(),
                session_id,
                inner_finalization: inner_finalization(),
            })
            .unwrap();
        let diagnostic = &response["result"]["diagnostic"];
        assert_eq!(diagnostic["schema"], DIAGNOSTIC_SCHEMA);
        assert_ne!(
            diagnostic["schema"],
            "vrcforge.primitive_basis_live_origin.v1"
        );
        assert_eq!(diagnostic["proofAlgorithm"], PROOF_ALGORITHM);
        assert_eq!(diagnostic["originTrust"], ORIGIN_TRUST);
        assert!(diagnostic.get("originVerified").is_none());
        assert!(diagnostic.get("signerPublicKey").is_none());
        let signature = base64url_decode(diagnostic["signature"].as_str().unwrap());
        assert_eq!(signature.len(), 64);
        assert_eq!(&signature[..31], &[0u8; 31]);
        assert_eq!(signature[31], 1);
        assert_eq!(&signature[32..63], &[0u8; 31]);
        assert_eq!(signature[63], 1);
        let error = session
            .handle(Request::Finalize {
                schema: REQUEST_SCHEMA.to_string(),
                session_id: "repeat".to_string(),
                inner_finalization: inner_finalization(),
            })
            .unwrap_err();
        assert_eq!(error.code(), "session_terminated");
    }

    #[test]
    fn failed_finalize_consumes_the_active_session() {
        let mut session = session();
        let (_session_id, _) = begin(&mut session);
        let error = session
            .handle(Request::Finalize {
                schema: REQUEST_SCHEMA.to_string(),
                session_id: "wrong".to_string(),
                inner_finalization: inner_finalization(),
            })
            .unwrap_err();
        assert_eq!(error.code(), "session_id_mismatch");
        assert!(session.is_terminal());
    }

    #[test]
    fn expired_ticket_cannot_be_finalized() {
        let mut session = session();
        let (session_id, _) = begin(&mut session);
        if let Phase::Active(active) = &mut session.phase {
            active.expires_at = UNIX_EPOCH;
        } else {
            panic!("active phase missing");
        }
        let error = session
            .handle(Request::Finalize {
                schema: REQUEST_SCHEMA.to_string(),
                session_id,
                inner_finalization: inner_finalization(),
            })
            .unwrap_err();
        assert_eq!(error.code(), "ticket_expired");
        assert!(session.is_terminal());
    }

    #[test]
    fn abort_discards_active_secrets_without_signing() {
        let mut session = session();
        let _ = begin(&mut session);
        let response = session
            .handle(Request::Abort {
                schema: REQUEST_SCHEMA.to_string(),
            })
            .unwrap();
        assert!(session.is_terminal());
        assert_eq!(response["result"]["activeRunDiscarded"], true);
        assert_eq!(response["result"]["ownedProcessCount"], 0);
        assert!(response["result"].get("signature").is_none());
    }

    #[test]
    fn framing_rejects_oversize_truncation_and_early_eof() {
        let mut oversized = Cursor::new(((MAX_FRAME_SIZE + 1) as u32).to_be_bytes().to_vec());
        assert_eq!(
            read_request_frame(&mut oversized).unwrap_err().code(),
            "frame_size_rejected"
        );
        let mut truncated = Cursor::new(vec![0, 0, 0, 8, b'{']);
        assert_eq!(
            read_request_frame(&mut truncated).unwrap_err().code(),
            "truncated_frame_payload"
        );
        let mut input = Cursor::new(Vec::<u8>::new());
        let mut output = Vec::new();
        let error = run_protocol(&mut input, &mut output, &mut session()).unwrap_err();
        assert_eq!(error.code(), "early_eof");
        assert!(!output.is_empty());
    }

    #[test]
    fn explicit_close_completes_a_bounded_protocol_session() {
        let request = serde_json::to_vec(&serde_json::json!({
            "schema": REQUEST_SCHEMA,
            "command": "close",
        }))
        .unwrap();
        let mut framed = (request.len() as u32).to_be_bytes().to_vec();
        framed.extend_from_slice(&request);
        let mut input = Cursor::new(framed);
        let mut output = Vec::new();
        let mut session = session();
        run_protocol(&mut input, &mut output, &mut session).unwrap();
        assert!(session.is_terminal());
    }

    #[test]
    fn sensitive_bytes_are_explicitly_cleared() {
        let mut secret = Sensitive32([0x5a; 32]);
        secret.clear();
        assert_eq!(secret.0, [0u8; 32]);
    }

    #[test]
    fn utc_format_is_stable() {
        assert_eq!(
            format_utc(UNIX_EPOCH + Duration::from_millis(1_700_000_000_123)).unwrap(),
            "2023-11-14T22:13:20.123Z"
        );
    }

    #[test]
    fn low_s_normalization_rejects_bounds_and_preserves_half_order() {
        let mut zero_r = [0u8; 64];
        zero_r[63] = 1;
        assert_eq!(
            normalize_low_s(zero_r).unwrap_err().code(),
            "signature_scalar_invalid"
        );

        let mut half = [0u8; 64];
        half[31] = 1;
        half[32..].copy_from_slice(&P256_HALF_ORDER);
        assert_eq!(normalize_low_s(half).unwrap(), half);

        let mut order_s = [0u8; 64];
        order_s[31] = 1;
        order_s[32..].copy_from_slice(&P256_ORDER);
        assert_eq!(
            normalize_low_s(order_s).unwrap_err().code(),
            "signature_scalar_invalid"
        );
    }

    fn base64url_decode(value: &str) -> Vec<u8> {
        let mut output = Vec::new();
        let mut accumulator = 0u32;
        let mut bits = 0usize;
        for byte in value.bytes() {
            let value = match byte {
                b'A'..=b'Z' => byte - b'A',
                b'a'..=b'z' => byte - b'a' + 26,
                b'0'..=b'9' => byte - b'0' + 52,
                b'-' => 62,
                b'_' => 63,
                _ => panic!("invalid base64url"),
            } as u32;
            accumulator = (accumulator << 6) | value;
            bits += 6;
            if bits >= 8 {
                bits -= 8;
                output.push(((accumulator >> bits) & 0xff) as u8);
            }
        }
        output
    }
}
