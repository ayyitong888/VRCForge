use super::*;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::{self, Cursor, Read, Write};
use std::path::Path;

const GENERATION: [u8; 32] = [0x24; 32];
const CHALLENGE: [u8; 32] = [0x42; 32];

struct ChunkedDuplex {
    input: Cursor<Vec<u8>>,
    output: Vec<u8>,
    read_chunk: usize,
    write_chunk: usize,
    next_read_error: Option<io::ErrorKind>,
    next_write_error: Option<io::ErrorKind>,
}

impl ChunkedDuplex {
    fn new(input: Vec<u8>, read_chunk: usize, write_chunk: usize) -> Self {
        Self {
            input: Cursor::new(input),
            output: Vec::new(),
            read_chunk,
            write_chunk,
            next_read_error: None,
            next_write_error: None,
        }
    }
}

impl Read for ChunkedDuplex {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        if let Some(kind) = self.next_read_error.take() {
            return Err(io::Error::from(kind));
        }
        let limit = buffer.len().min(self.read_chunk.max(1));
        self.input.read(&mut buffer[..limit])
    }
}

impl Write for ChunkedDuplex {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        if let Some(kind) = self.next_write_error.take() {
            return Err(io::Error::from(kind));
        }
        let limit = buffer.len().min(self.write_chunk.max(1));
        self.output.extend_from_slice(&buffer[..limit]);
        Ok(limit)
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

fn frame(payload: &[u8]) -> Vec<u8> {
    let mut framed = Vec::with_capacity(payload.len() + 4);
    framed.extend_from_slice(&(payload.len() as u32).to_be_bytes());
    framed.extend_from_slice(payload);
    framed
}

fn fixed_pipe_identity() -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(FIXED_PIPE_IDENTITY_DOMAIN);
    digest.update(AUTHORITY_PIPE_NAME.as_bytes());
    digest.update(AUTHORITY_PIPE_SDDL.as_bytes());
    digest.update((MAX_REQUEST_FRAME_SIZE as u64).to_be_bytes());
    digest.update((MAX_RESPONSE_FRAME_SIZE as u64).to_be_bytes());
    digest.update(GENERATION_ATTESTATION_POLICY_ID.as_bytes());
    digest.finalize().into()
}

fn handshake_response(generation: [u8; 32], challenge: [u8; 32]) -> Vec<u8> {
    let fixed_pipe_identity = fixed_pipe_identity();
    let service_executable = [0x59; 32];
    let service_executable_path = [0x58; 32];
    let service_executable_file_identity = [0x57; 32];
    let protected_manifest = [0x55; 32];
    let protected_key = [0x53; 32];
    let signer_key_id = [0x5a; 32];
    let protected_ledger = [0x54; 32];
    let scm_readback = [0x56; 32];
    let bootstrap_receipt = [0x52; 32];
    let service_process_id = 101u32;
    let service_process_started_at = 103u64;
    let mut service_instance_hasher = Sha256::new();
    service_instance_hasher.update(SERVICE_INSTANCE_DOMAIN);
    service_instance_hasher.update(generation);
    service_instance_hasher.update(service_executable);
    service_instance_hasher.update(service_executable_path);
    service_instance_hasher.update(service_executable_file_identity);
    service_instance_hasher.update(service_process_id.to_be_bytes());
    service_instance_hasher.update(service_process_started_at.to_be_bytes());
    service_instance_hasher.update(fixed_pipe_identity);
    service_instance_hasher.update(protected_manifest);
    service_instance_hasher.update(protected_key);
    service_instance_hasher.update(signer_key_id);
    service_instance_hasher.update(protected_ledger);
    service_instance_hasher.update(scm_readback);
    service_instance_hasher.update(bootstrap_receipt);
    let service_instance: [u8; 32] = service_instance_hasher.finalize().into();
    let peer_binding = [0x61; 32];
    let sequence = 7u64;
    let mut digest = Sha256::new();
    digest.update(GENERATION_ATTESTATION_DOMAIN);
    digest.update(GENERATION_ATTESTATION_POLICY_ID.as_bytes());
    digest.update(GENERATION_ATTESTATION_PROOF_ALGORITHM.as_bytes());
    digest.update(fixed_pipe_identity);
    digest.update(service_instance);
    digest.update(peer_binding);
    digest.update(challenge);
    digest.update(sequence.to_be_bytes());
    let attestation_digest: [u8; 32] = digest.finalize().into();
    let mut signature = [0u8; 64];
    signature[31] = 1;
    signature[63] = 1;
    serde_json::to_vec(&json!({
        "command": "handshake",
        "ok": true,
        "result": {
            "attestationDigest": hex_lower(&attestation_digest),
            "bootstrapReceiptSha256": hex_lower(&bootstrap_receipt),
            "challenge": hex_lower(&challenge),
            "currentGeneration": hex_lower(&generation),
            "fixedPipeIdentityDigest": hex_lower(&fixed_pipe_identity),
            "peerBindingSha256": hex_lower(&peer_binding),
            "pipeName": AUTHORITY_PIPE_NAME,
            "policyId": GENERATION_ATTESTATION_POLICY_ID,
            "proofAlgorithm": GENERATION_ATTESTATION_PROOF_ALGORITHM,
            "protectedKeyReadbackSha256": hex_lower(&protected_key),
            "protectedLedgerReadbackSha256": hex_lower(&protected_ledger),
            "protectedManifestReadbackSha256": hex_lower(&protected_manifest),
            "schema": GENERATION_ATTESTATION_SCHEMA,
            "scmReadbackSha256": hex_lower(&scm_readback),
            "sequence": sequence,
            "serviceExecutableFileIdentitySha256": hex_lower(&service_executable_file_identity),
            "serviceExecutablePathSha256": hex_lower(&service_executable_path),
            "serviceExecutableSha256": hex_lower(&service_executable),
            "serviceInstanceDigest": hex_lower(&service_instance),
            "serviceProcessId": service_process_id,
            "serviceProcessStartedAt": service_process_started_at,
            "signatureP256": hex_lower(&signature),
            "signerKeyId": hex_lower(&signer_key_id),
        },
        "schema": RESPONSE_SCHEMA,
    }))
    .unwrap()
}

fn status_response() -> Vec<u8> {
    serde_json::to_vec(&json!({
        "command": "status",
        "ok": true,
        "result": {"trustedBoundaryReady": false},
        "schema": RESPONSE_SCHEMA,
    }))
    .unwrap()
}

fn framed_exchange(command_payload: &[u8]) -> Vec<u8> {
    let mut input = frame(&handshake_response(GENERATION, CHALLENGE));
    input.extend_from_slice(&frame(command_payload));
    input
}

fn decode_written_frames(bytes: &[u8]) -> Vec<Value> {
    let mut cursor = Cursor::new(bytes);
    let mut values = Vec::new();
    while cursor.position() < bytes.len() as u64 {
        let mut header = [0u8; 4];
        cursor.read_exact(&mut header).unwrap();
        let length = u32::from_be_bytes(header) as usize;
        let mut payload = vec![0u8; length];
        cursor.read_exact(&mut payload).unwrap();
        values.push(serde_json::from_slice(&payload).unwrap());
    }
    values
}

#[test]
fn partial_reads_and_writes_preserve_canonical_handshake_and_command_frames() {
    let transport = ChunkedDuplex::new(framed_exchange(&status_response()), 3, 2);
    let mut client =
        AuthorityControllerClient::establish_for_test(transport, GENERATION, CHALLENGE).unwrap();
    let response = client.execute(AuthorityClientCommand::Status).unwrap();
    assert_eq!(response.raw_bytes(), status_response());
    assert_eq!(
        client.handshake_raw_bytes(),
        handshake_response(GENERATION, CHALLENGE)
    );

    let transport = client.into_transport_for_test();
    let frames = decode_written_frames(&transport.output);
    assert_eq!(frames.len(), 2);
    assert_eq!(frames[0]["command"], "handshake");
    assert_eq!(frames[0]["expectedGeneration"], hex_lower(&GENERATION));
    assert_eq!(frames[0]["challenge"], hex_lower(&CHALLENGE));
    assert_eq!(
        frames[1],
        json!({"command":"status","schema":REQUEST_SCHEMA})
    );
}

#[test]
fn response_framing_rejects_empty_oversize_eof_and_truncation() {
    for (input, expected) in [
        (
            0u32.to_be_bytes().to_vec(),
            "authority_client_response_frame_empty",
        ),
        (
            ((MAX_RESPONSE_FRAME_SIZE as u32) + 1)
                .to_be_bytes()
                .to_vec(),
            "authority_client_response_frame_too_large",
        ),
        (vec![], "authority_client_response_eof"),
        (vec![0, 0], "authority_client_response_header_truncated"),
        (
            [4u32.to_be_bytes().as_slice(), b"{}"].concat(),
            "authority_client_response_body_truncated",
        ),
    ] {
        let mut transport = ChunkedDuplex::new(input, 2, 2);
        assert_eq!(
            read_response_frame(&mut transport).unwrap_err().code(),
            expected
        );
    }
}

#[test]
fn response_io_timeout_and_would_block_fail_closed() {
    for kind in [io::ErrorKind::TimedOut, io::ErrorKind::WouldBlock] {
        let mut transport = ChunkedDuplex::new(Vec::new(), 4, 4);
        transport.next_read_error = Some(kind);
        assert_eq!(
            read_response_frame(&mut transport).unwrap_err().code(),
            "authority_client_io_timeout"
        );
    }

    let mut transport = ChunkedDuplex::new(Vec::new(), 4, 4);
    transport.next_write_error = Some(io::ErrorKind::TimedOut);
    assert_eq!(
        write_request_frame(&mut transport, b"{}")
            .unwrap_err()
            .code(),
        "authority_client_io_timeout"
    );
}

#[test]
fn strict_response_decoder_rejects_duplicates_noncanonical_and_bad_shapes() {
    let cases: [(&[u8], &str); 6] = [
        (
            br#"{"command":"status","ok":true,"ok":true,"result":{},"schema":"vrcforge.primitive_evidence_authority_response.v1"}"#,
            "authority_client_response_duplicate_key",
        ),
        (
            br#"{ "command":"status","ok":true,"result":{},"schema":"vrcforge.primitive_evidence_authority_response.v1"}"#,
            "authority_client_response_noncanonical",
        ),
        (
            br#"{"command":"status","ok":true,"result":{"ratio":1.5},"schema":"vrcforge.primitive_evidence_authority_response.v1"}"#,
            "authority_client_response_float_rejected",
        ),
        (
            br#"{"command":"status","extra":true,"ok":true,"result":{},"schema":"vrcforge.primitive_evidence_authority_response.v1"}"#,
            "authority_client_response_shape_invalid",
        ),
        (
            br#"{"command":"status","ok":true,"result":{},"schema":"wrong"}"#,
            "authority_client_response_schema_mismatch",
        ),
        (
            br#"{"command":"selfTest","ok":true,"result":{},"schema":"vrcforge.primitive_evidence_authority_response.v1"}"#,
            "authority_client_response_command_mismatch",
        ),
    ];
    for (payload, expected) in cases {
        assert_eq!(
            decode_command_response(payload, "status")
                .unwrap_err()
                .code(),
            expected
        );
    }
}

#[test]
fn canonical_service_errors_are_retained_as_untrusted_raw_responses() {
    let payload = serde_json::to_vec(&json!({
        "error": {"code": "authority_request_not_found"},
        "ok": false,
        "schema": RESPONSE_SCHEMA,
    }))
    .unwrap();
    let response = decode_command_response(&payload, "getResult").unwrap();
    assert_eq!(response.raw_bytes(), payload);

    assert_eq!(
        validate_handshake_response(&payload, &GENERATION, &CHALLENGE)
            .unwrap_err()
            .code(),
        "authority_client_handshake_rejected"
    );
}

#[test]
fn handshake_binds_generation_challenge_pipe_digest_and_attestation_digest() {
    let valid = handshake_response(GENERATION, CHALLENGE);
    validate_handshake_response(&valid, &GENERATION, &CHALLENGE).unwrap();

    let cases = [
        (
            "currentGeneration",
            hex_lower(&[0x25; 32]),
            "authority_client_handshake_generation_mismatch",
        ),
        (
            "challenge",
            hex_lower(&[0x43; 32]),
            "authority_client_handshake_challenge_mismatch",
        ),
        (
            "pipeName",
            r"\\.\pipe\wrong".to_string(),
            "authority_client_handshake_pipe_mismatch",
        ),
        (
            "fixedPipeIdentityDigest",
            hex_lower(&[0x44; 32]),
            "authority_client_handshake_pipe_identity_mismatch",
        ),
        (
            "attestationDigest",
            hex_lower(&[0x45; 32]),
            "authority_client_handshake_digest_mismatch",
        ),
        (
            "serviceExecutableSha256",
            hex_lower(&[0x46; 32]),
            "authority_client_handshake_service_instance_mismatch",
        ),
    ];
    for (field, replacement, expected) in cases {
        let mut value: Value = serde_json::from_slice(&valid).unwrap();
        value["result"][field] = Value::String(replacement);
        let payload = serde_json::to_vec(&value).unwrap();
        assert_eq!(
            validate_handshake_response(&payload, &GENERATION, &CHALLENGE)
                .unwrap_err()
                .code(),
            expected
        );
    }

    let mut bad_signature: Value = serde_json::from_slice(&valid).unwrap();
    bad_signature["result"]["signatureP256"] = Value::String("00".repeat(64));
    let payload = serde_json::to_vec(&bad_signature).unwrap();
    assert_eq!(
        validate_handshake_response(&payload, &GENERATION, &CHALLENGE)
            .unwrap_err()
            .code(),
        "authority_client_handshake_signature_shape_invalid"
    );
}

struct ExactHandshakeVerifier {
    expected_signer: [u8; 32],
    expected_digest: [u8; 32],
    expected_signature: [u8; 64],
    calls: usize,
}

impl AuthorityHandshakeSignatureVerifier for ExactHandshakeVerifier {
    fn verify_digest_signature(
        &mut self,
        signer_key_id: &[u8; 32],
        digest: &[u8; 32],
        signature: &[u8; 64],
    ) -> Result<(), AuthorityClientError> {
        self.calls += 1;
        if signer_key_id != &self.expected_signer
            || digest != &self.expected_digest
            || signature != &self.expected_signature
        {
            return Err(AuthorityClientError::from_code(
                "test_cryptographic_signature_rejected",
            ));
        }
        Ok(())
    }
}

#[test]
fn parent_exchange_requires_exact_peer_signer_and_cryptographic_verifier() {
    let handshake = handshake_response(GENERATION, CHALLENGE);
    let value: Value = serde_json::from_slice(&handshake).unwrap();
    let expected_peer = [0x61; 32];
    let expected_signer = [0x5a; 32];
    let expected_digest =
        parse_hex_array::<32>(value["result"]["attestationDigest"].as_str().unwrap()).unwrap();
    let mut expected_signature = [0u8; 64];
    expected_signature[31] = 1;
    expected_signature[63] = 1;
    let mut verifier = ExactHandshakeVerifier {
        expected_signer,
        expected_digest,
        expected_signature,
        calls: 0,
    };
    let verified = verify_parent_controller_exchange(
        &handshake,
        &status_response(),
        &GENERATION,
        &expected_peer,
        &expected_signer,
        "status",
        &mut verifier,
    )
    .unwrap();
    assert_eq!(verifier.calls, 1);
    assert_eq!(verified.attestation_digest(), &expected_digest);
    assert_eq!(verified.signature_p256(), &expected_signature);
    assert_eq!(verified.signer_key_id(), &expected_signer);
    assert_eq!(verified.peer_binding_sha256(), &expected_peer);
    assert_eq!(verified.generation_sha256(), &GENERATION);
    let mut canonical_handshake = Sha256::new();
    canonical_handshake.update(CANONICAL_HANDSHAKE_DOMAIN);
    canonical_handshake.update((handshake.len() as u64).to_be_bytes());
    canonical_handshake.update(&handshake);
    let expected_canonical_handshake: [u8; 32] = canonical_handshake.finalize().into();
    assert_eq!(
        verified.canonical_handshake_sha256(),
        &expected_canonical_handshake
    );

    for (peer, signer, command, expected) in [
        (
            [0x62; 32],
            expected_signer,
            "status",
            "authority_parent_exchange_peer_binding_mismatch",
        ),
        (
            expected_peer,
            [0x5b; 32],
            "status",
            "authority_parent_exchange_signer_mismatch",
        ),
        (
            expected_peer,
            expected_signer,
            "selfTest",
            "authority_client_response_command_mismatch",
        ),
    ] {
        let mut verifier = ExactHandshakeVerifier {
            expected_signer,
            expected_digest,
            expected_signature,
            calls: 0,
        };
        assert_eq!(
            verify_parent_controller_exchange(
                &handshake,
                &status_response(),
                &GENERATION,
                &peer,
                &signer,
                command,
                &mut verifier,
            )
            .unwrap_err()
            .code(),
            expected
        );
    }

    let mut replaced: Value = serde_json::from_slice(&handshake).unwrap();
    let mut replacement = expected_signature;
    replacement[31] = 2;
    replaced["result"]["signatureP256"] = Value::String(hex_lower(&replacement));
    let replaced = serde_json::to_vec(&replaced).unwrap();
    let mut verifier = ExactHandshakeVerifier {
        expected_signer,
        expected_digest,
        expected_signature,
        calls: 0,
    };
    assert_eq!(
        verify_parent_controller_exchange(
            &replaced,
            &status_response(),
            &GENERATION,
            &expected_peer,
            &expected_signer,
            "status",
            &mut verifier,
        )
        .unwrap_err()
        .code(),
        "test_cryptographic_signature_rejected"
    );
    assert_eq!(verifier.calls, 1);
}

#[test]
fn command_surface_is_fixed_and_request_ids_fail_closed() {
    assert_eq!(
        REQUEST_SCHEMA,
        "vrcforge.primitive_evidence_authority_request.v2"
    );
    assert_eq!(
        RESPONSE_SCHEMA,
        "vrcforge.primitive_evidence_authority_response.v1"
    );
    let commands = [
        (AuthorityClientCommand::Status, "status", None),
        (AuthorityClientCommand::SelfTest, "selfTest", None),
        (
            AuthorityClientCommand::RunModelPartComposition {
                request_id: "request-1".to_string(),
                handle_tokens: ExternalModelPartHandleTokens::try_from_values([
                    0x11, 0x22, 0x33, 0x44, 0x55, 0x66,
                ])
                .unwrap(),
            },
            "runModelPartComposition",
            Some("request-1"),
        ),
        (
            AuthorityClientCommand::Cancel {
                request_id: "request-2".to_string(),
            },
            "cancel",
            Some("request-2"),
        ),
        (
            AuthorityClientCommand::GetResult {
                request_id: "request-3".to_string(),
            },
            "getResult",
            Some("request-3"),
        ),
    ];
    for (command, expected_name, request_id) in commands {
        let payload = command.canonical_payload().unwrap();
        let value: Value = serde_json::from_slice(&payload).unwrap();
        assert_eq!(value["command"], expected_name);
        assert_eq!(value.get("requestId").and_then(Value::as_str), request_id);
        assert_eq!(value["schema"], REQUEST_SCHEMA);
        if expected_name == "runModelPartComposition" {
            assert_eq!(
                value["handleTokens"],
                serde_json::json!([
                    "0000000000000011",
                    "0000000000000022",
                    "0000000000000033",
                    "0000000000000044",
                    "0000000000000055",
                    "0000000000000066",
                ])
            );
            let debug = format!("{command:?}");
            assert!(!debug.contains("0000000000000011"));
        } else {
            assert!(value.get("handleTokens").is_none());
        }
    }

    for request_id in ["", "-bad", "bad space", &"x".repeat(129)] {
        let command = AuthorityClientCommand::GetResult {
            request_id: request_id.to_string(),
        };
        assert_eq!(
            command.canonical_payload().unwrap_err().code(),
            "authority_client_request_id_invalid"
        );
    }
}

#[test]
fn installed_generation_comes_only_from_the_fixed_controller_image_path() {
    let layout = AuthorityLayout::for_test_roots(
        Path::new(r"C:\Program Files"),
        Path::new(r"C:\ProgramData"),
    )
    .unwrap();
    let expected = layout
        .controller_executable_for_generation(&GENERATION)
        .unwrap();
    assert_eq!(
        derive_controller_generation_from_path(&layout, &expected).unwrap(),
        GENERATION
    );
    for rejected in [
        expected.with_file_name("copy.exe"),
        Path::new(r"C:\temp")
            .join(hex_lower(&GENERATION))
            .join("vrcforge_primitive_evidence_controller.exe"),
        Path::new(r"C:\Program Files\VRCForgeEvidenceAuthority\v1\generations")
            .join("AA".repeat(32))
            .join("vrcforge_primitive_evidence_controller.exe"),
    ] {
        assert_eq!(
            derive_controller_generation_from_path(&layout, &rejected)
                .unwrap_err()
                .code(),
            "authority_client_controller_image_invalid"
        );
    }
}

#[test]
fn challenge_bytes_are_zeroed_explicitly() {
    let mut challenge = SensitiveChallenge::from_bytes_for_test(CHALLENGE).unwrap();
    challenge.clear();
    assert!(challenge.bytes.iter().all(|byte| *byte == 0));
}

#[cfg(windows)]
#[test]
fn operating_system_challenges_are_nonzero_and_fresh_per_connection() {
    let mut first = SensitiveChallenge::generate().unwrap();
    let mut second = SensitiveChallenge::generate().unwrap();
    assert!(first.bytes.iter().any(|byte| *byte != 0));
    assert!(second.bytes.iter().any(|byte| *byte != 0));
    assert_ne!(first.bytes, second.bytes);
    first.clear();
    second.clear();
}
