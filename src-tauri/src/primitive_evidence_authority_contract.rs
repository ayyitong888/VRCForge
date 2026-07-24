use serde::{de, Deserialize, Deserializer};
use serde_json::{Map, Number, Value};
use std::{
    fmt,
    io::{self, Read, Write},
};

pub const REQUEST_SCHEMA: &str = "vrcforge.primitive_evidence_authority_request.v1";
pub const RESPONSE_SCHEMA: &str = "vrcforge.primitive_evidence_authority_response.v1";
pub const MAX_FRAME_SIZE: usize = 64 * 1024;

const SOURCE_BLOCKERS: [&str; 5] = [
    "restricted_service_boundary_not_implemented",
    "protected_key_not_provisioned",
    "persistent_service_ledger_not_connected",
    "process_supervision_not_implemented",
    "protected_full_projection_not_implemented",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContractError(String);

impl ContractError {
    fn new(code: impl Into<String>) -> Self {
        Self(code.into())
    }

    pub fn code(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for ContractError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ContractError {}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "command", deny_unknown_fields)]
pub enum Request {
    #[serde(rename = "status")]
    Status { schema: String },
    #[serde(rename = "selfTest")]
    SelfTest { schema: String },
    #[serde(rename = "runModelPartComposition")]
    RunModelPartComposition {
        schema: String,
        #[serde(rename = "requestId")]
        request_id: String,
    },
    #[serde(rename = "cancel")]
    Cancel {
        schema: String,
        #[serde(rename = "requestId")]
        request_id: String,
    },
    #[serde(rename = "getResult")]
    GetResult {
        schema: String,
        #[serde(rename = "requestId")]
        request_id: String,
    },
}

impl Request {
    fn schema(&self) -> &str {
        match self {
            Self::Status { schema }
            | Self::SelfTest { schema }
            | Self::RunModelPartComposition { schema, .. }
            | Self::Cancel { schema, .. }
            | Self::GetResult { schema, .. } => schema,
        }
    }

    pub fn command(&self) -> &'static str {
        match self {
            Self::Status { .. } => "status",
            Self::SelfTest { .. } => "selfTest",
            Self::RunModelPartComposition { .. } => "runModelPartComposition",
            Self::Cancel { .. } => "cancel",
            Self::GetResult { .. } => "getResult",
        }
    }

    fn validate(&self) -> Result<(), ContractError> {
        if self.schema() != REQUEST_SCHEMA {
            return Err(ContractError::new("request_schema_mismatch"));
        }
        match self {
            Self::RunModelPartComposition { request_id, .. } => {
                require_request_id(request_id)?;
            }
            Self::Cancel { request_id, .. } | Self::GetResult { request_id, .. } => {
                require_request_id(request_id)?;
            }
            Self::Status { .. } | Self::SelfTest { .. } => {}
        }
        Ok(())
    }
}

pub struct ReadOnlyAuthority;

impl ReadOnlyAuthority {
    pub fn new() -> Self {
        Self
    }

    pub fn handle(&mut self, request: Request) -> Result<Value, ContractError> {
        request.validate()?;
        let command = request.command();
        match request {
            Request::Status { .. } => Ok(success_response(
                command,
                serde_json::json!({
                    "readOnly": true,
                    "trustedBoundaryReady": false,
                    "blockers": SOURCE_BLOCKERS,
                }),
            )),
            Request::SelfTest { .. } => Ok(success_response(
                command,
                serde_json::json!({
                    "passed": true,
                    "readOnly": true,
                    "trustedBoundaryReady": false,
                    "blockers": SOURCE_BLOCKERS,
                }),
            )),
            Request::RunModelPartComposition { .. }
            | Request::Cancel { .. }
            | Request::GetResult { .. } => Err(ContractError::new("authority_boundary_not_ready")),
        }
    }
}

impl Default for ReadOnlyAuthority {
    fn default() -> Self {
        Self::new()
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

pub fn decode_request_payload(payload: &[u8]) -> Result<Request, ContractError> {
    if payload.is_empty() {
        return Err(ContractError::new("frame_empty"));
    }
    if payload.len() > MAX_FRAME_SIZE {
        return Err(ContractError::new("frame_too_large"));
    }
    let strict = serde_json::from_slice::<StrictJsonValue>(payload).map_err(|error| {
        let message = error.to_string();
        if message.contains("duplicate_object_key") {
            ContractError::new("duplicate_object_key")
        } else if message.contains("floating_point_not_allowed") {
            ContractError::new("floating_point_not_allowed")
        } else {
            ContractError::new("request_json_invalid")
        }
    })?;
    let request = serde_json::from_value::<Request>(strict.0)
        .map_err(|_| ContractError::new("request_shape_invalid"))?;
    request.validate()?;
    Ok(request)
}

pub fn read_request_frame<R: Read>(reader: &mut R) -> Result<Option<Request>, ContractError> {
    let mut header = [0u8; 4];
    loop {
        match reader.read(&mut header[..1]) {
            Ok(0) => return Ok(None),
            Ok(1) => break,
            Ok(_) => unreachable!(),
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => return Err(ContractError::new("frame_read_failed")),
        }
    }
    reader
        .read_exact(&mut header[1..])
        .map_err(|error| match error.kind() {
            io::ErrorKind::UnexpectedEof => ContractError::new("frame_header_truncated"),
            _ => ContractError::new("frame_read_failed"),
        })?;
    let length = u32::from_be_bytes(header) as usize;
    if length == 0 {
        return Err(ContractError::new("frame_empty"));
    }
    if length > MAX_FRAME_SIZE {
        return Err(ContractError::new("frame_too_large"));
    }
    let mut payload = vec![0u8; length];
    reader
        .read_exact(&mut payload)
        .map_err(|error| match error.kind() {
            io::ErrorKind::UnexpectedEof => ContractError::new("frame_body_truncated"),
            _ => ContractError::new("frame_read_failed"),
        })?;
    decode_request_payload(&payload).map(Some)
}

pub fn write_response_frame<W: Write>(
    writer: &mut W,
    response: &Value,
) -> Result<(), ContractError> {
    let payload = serde_json::to_vec(response)
        .map_err(|_| ContractError::new("response_serialization_failed"))?;
    if payload.is_empty() || payload.len() > MAX_FRAME_SIZE {
        return Err(ContractError::new("response_frame_invalid"));
    }
    writer
        .write_all(&(payload.len() as u32).to_be_bytes())
        .and_then(|_| writer.write_all(&payload))
        .and_then(|_| writer.flush())
        .map_err(|_| ContractError::new("response_write_failed"))
}

pub fn run_read_only_protocol<R: Read, W: Write>(
    reader: &mut R,
    writer: &mut W,
) -> Result<(), ContractError> {
    let mut authority = ReadOnlyAuthority::new();
    loop {
        let request = match read_request_frame(reader) {
            Ok(Some(value)) => value,
            Ok(None) => return Ok(()),
            Err(error) => {
                write_response_frame(writer, &error_response(error.code()))?;
                return Err(error);
            }
        };
        let response = match authority.handle(request) {
            Ok(value) => value,
            Err(error) => error_response(error.code()),
        };
        write_response_frame(writer, &response)?;
    }
}

fn success_response(command: &str, result: Value) -> Value {
    serde_json::json!({
        "schema": RESPONSE_SCHEMA,
        "ok": true,
        "command": command,
        "result": result,
    })
}

fn error_response(code: &str) -> Value {
    serde_json::json!({
        "schema": RESPONSE_SCHEMA,
        "ok": false,
        "error": { "code": code },
    })
}

fn require_request_id(value: &str) -> Result<(), ContractError> {
    let mut characters = value.chars();
    let first = characters
        .next()
        .filter(|character| character.is_ascii_alphanumeric())
        .ok_or_else(|| ContractError::new("request_id_invalid"))?;
    let valid = first.is_ascii_alphanumeric()
        && value.len() <= 128
        && characters.all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.' | ':')
        });
    if !valid {
        return Err(ContractError::new("request_id_invalid"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn request(command: &str, fields: &str) -> Vec<u8> {
        format!(
            r#"{{"schema":"{}","command":"{}"{}}}"#,
            REQUEST_SCHEMA, command, fields
        )
        .into_bytes()
    }

    fn frame(payload: &[u8]) -> Vec<u8> {
        [(payload.len() as u32).to_be_bytes().as_slice(), payload].concat()
    }

    fn response_frames(mut bytes: &[u8]) -> Vec<Value> {
        let mut responses = Vec::new();
        while !bytes.is_empty() {
            let length = u32::from_be_bytes(bytes[..4].try_into().unwrap()) as usize;
            responses.push(serde_json::from_slice(&bytes[4..4 + length]).unwrap());
            bytes = &bytes[4 + length..];
        }
        responses
    }

    #[test]
    fn exact_allowlist_parses_all_commands() {
        let cases = [
            request("status", ""),
            request("selfTest", ""),
            request("runModelPartComposition", r#","requestId":"request-1""#),
            request("cancel", r#","requestId":"request-1""#),
            request("getResult", r#","requestId":"request-1""#),
        ];

        for payload in cases {
            decode_request_payload(&payload).expect("allowlisted command should parse");
        }
    }

    #[test]
    fn authority_commands_are_not_part_of_the_protocol() {
        for command in ["sign", "provision", "reset", "delete", "finalize"] {
            let error = decode_request_payload(&request(command, "")).unwrap_err();
            assert_eq!(error.code(), "request_shape_invalid");
        }
    }

    #[test]
    fn unknown_duplicate_float_and_oversize_payloads_are_rejected() {
        let unknown = format!(
            r#"{{"schema":"{}","command":"status","extra":true}}"#,
            REQUEST_SCHEMA
        );
        assert_eq!(
            decode_request_payload(unknown.as_bytes())
                .unwrap_err()
                .code(),
            "request_shape_invalid"
        );

        let duplicate = format!(
            r#"{{"schema":"{}","schema":"{}","command":"status"}}"#,
            REQUEST_SCHEMA, REQUEST_SCHEMA
        );
        assert_eq!(
            decode_request_payload(duplicate.as_bytes())
                .unwrap_err()
                .code(),
            "duplicate_object_key"
        );

        let float = format!(
            r#"{{"schema":"{}","command":"status","value":1.5}}"#,
            REQUEST_SCHEMA
        );
        assert_eq!(
            decode_request_payload(float.as_bytes()).unwrap_err().code(),
            "floating_point_not_allowed"
        );

        let oversize = vec![b' '; MAX_FRAME_SIZE + 1];
        assert_eq!(
            decode_request_payload(&oversize).unwrap_err().code(),
            "frame_too_large"
        );
    }

    #[test]
    fn invalid_schema_ids_and_caller_supplied_manifest_are_rejected() {
        let wrong_schema = br#"{"schema":"wrong","command":"status"}"#;
        assert_eq!(
            decode_request_payload(wrong_schema).unwrap_err().code(),
            "request_schema_mismatch"
        );

        let unsafe_id = request("runModelPartComposition", r#","requestId":"../escape""#);
        assert_eq!(
            decode_request_payload(&unsafe_id).unwrap_err().code(),
            "request_id_invalid"
        );

        let caller_manifest = request(
            "runModelPartComposition",
            r#","requestId":"request-2","manifestDigest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa""#,
        );
        assert_eq!(
            decode_request_payload(&caller_manifest).unwrap_err().code(),
            "request_shape_invalid"
        );
    }

    #[test]
    fn framed_reader_rejects_zero_large_and_truncated_frames() {
        let mut zero = Cursor::new(0u32.to_be_bytes().to_vec());
        assert_eq!(
            read_request_frame(&mut zero).unwrap_err().code(),
            "frame_empty"
        );

        let mut large = Cursor::new(((MAX_FRAME_SIZE + 1) as u32).to_be_bytes().to_vec());
        assert_eq!(
            read_request_frame(&mut large).unwrap_err().code(),
            "frame_too_large"
        );

        let mut truncated_header = Cursor::new(vec![0, 0, 0]);
        assert_eq!(
            read_request_frame(&mut truncated_header)
                .unwrap_err()
                .code(),
            "frame_header_truncated"
        );

        let mut truncated_body = Cursor::new([4u32.to_be_bytes().as_slice(), b"{}"].concat());
        assert_eq!(
            read_request_frame(&mut truncated_body).unwrap_err().code(),
            "frame_body_truncated"
        );
    }

    #[test]
    fn source_stub_is_read_only_and_fails_closed() {
        let mut authority = ReadOnlyAuthority::new();
        let status = authority
            .handle(decode_request_payload(&request("status", "")).unwrap())
            .unwrap();
        assert_eq!(status["result"]["trustedBoundaryReady"], false);
        assert_eq!(status["result"]["readOnly"], true);

        let self_test = authority
            .handle(decode_request_payload(&request("selfTest", "")).unwrap())
            .unwrap();
        assert_eq!(self_test["result"]["trustedBoundaryReady"], false);

        for payload in [
            request("runModelPartComposition", r#","requestId":"request-3""#),
            request("cancel", r#","requestId":"request-3""#),
            request("getResult", r#","requestId":"request-3""#),
        ] {
            let command = decode_request_payload(&payload).unwrap();
            assert_eq!(
                authority.handle(command).unwrap_err().code(),
                "authority_boundary_not_ready"
            );
        }
    }

    #[test]
    fn framed_protocol_reports_status_but_rejects_run_and_result() {
        let status = request("status", "");
        let run = request("runModelPartComposition", r#","requestId":"request-4""#);
        let result = request("getResult", r#","requestId":"request-4""#);
        let mut input = Cursor::new([frame(&status), frame(&run), frame(&result)].concat());
        let mut output = Vec::new();

        run_read_only_protocol(&mut input, &mut output).unwrap();
        let responses = response_frames(&output);
        assert_eq!(responses.len(), 3);
        assert_eq!(responses[0]["ok"], true);
        assert_eq!(responses[0]["result"]["trustedBoundaryReady"], false);
        assert_eq!(responses[1]["ok"], false);
        assert_eq!(
            responses[1]["error"]["code"],
            "authority_boundary_not_ready"
        );
        assert_eq!(responses[2]["ok"], false);
        assert_eq!(
            responses[2]["error"]["code"],
            "authority_boundary_not_ready"
        );
    }
}
