use super::*;
use std::sync::{Arc, Mutex};

#[derive(Clone)]
struct MockSigner {
    key_id: Digest,
    signature: [u8; 64],
    signed: Arc<Mutex<Vec<Digest>>>,
}

impl MockSigner {
    fn canonical() -> Self {
        let mut signature = [0u8; 64];
        signature[31] = 1;
        signature[63] = 1;
        Self {
            key_id: [0x51; 32],
            signature,
            signed: Arc::new(Mutex::new(Vec::new())),
        }
    }
}

impl ProtectedEvidenceBundleSigner for MockSigner {
    fn signer_key_id(&self) -> Digest {
        self.key_id
    }

    fn sign_protected_bundle(
        &mut self,
        digest: ProtectedBundleSigningDigest,
    ) -> Result<[u8; 64], ProtectedEvidenceBundleError> {
        self.signed.lock().unwrap().push(*digest.as_bytes());
        Ok(self.signature)
    }
}

fn digest(seed: u8) -> Digest {
    [seed; 32]
}

fn bindings() -> FixedProtectedEvidenceBindings {
    let authority = FixedAuthorityBinding::new(
        "authority-policy-v1",
        digest(1),
        digest(2),
        digest(3),
        digest(4),
        digest(5),
        digest(6),
        digest(7),
    )
    .unwrap();
    let package = FixedPackageBinding::new(
        "1.4.0",
        std::array::from_fn(|index| digest(20 + index as u8)),
    )
    .unwrap();
    let model = FixedModelEvidenceBinding::new(
        digest(40),
        digest(41),
        digest(42),
        digest(43),
        digest(44),
        digest(45),
    )
    .unwrap();
    FixedProtectedEvidenceBindings::new(authority, package, model)
}

fn prepared_source() -> PreparedProtectedEvidenceSource {
    let bindings = bindings();
    PreparedProtectedEvidenceSource::new(
        bindings.authority,
        bindings.package,
        bindings.model.fixture_set_descriptor_digest,
        bindings.model.fixture_set_digest,
        bindings.model.fixture_descriptor_digest,
        bindings.model.fixture_digest,
        digest(46),
    )
    .unwrap()
}

fn producer(signer: MockSigner) -> ProtectedEvidenceBundleProducer<MockSigner> {
    let source = prepared_source();
    ProtectedEvidenceBundleProducer::new(
        source.authority.authority_generation_digest,
        source.authority.protected_manifest_digest,
        source.authority.installed_layout_digest,
        source.authority.service_executable_digest,
        signer,
    )
}

fn runtime_result() -> ServiceOwnedVerifiedRuntimeResult {
    let bindings = bindings();
    let authority_ticket = digest(60);
    let cleanup = digest(61);
    let finalization = canonical_ascii_json(&serde_json::json!({
        "attestation": {
            "backendExecutableDigest": hex_lower(&bindings.package.digests[3]),
            "desktopExecutableDigest": hex_lower(&bindings.package.digests[2]),
            "fixtureDescriptorDigest": hex_lower(&bindings.model.fixture_descriptor_digest),
            "fixtureDigest": hex_lower(&bindings.model.fixture_digest),
            "fixtureProjectInputDigest": hex_lower(&bindings.model.fixture_project_input_digest),
            "fixtureSetDescriptorDigest": hex_lower(&bindings.model.fixture_set_descriptor_digest),
            "projectBindingDigest": hex_lower(&bindings.model.project_binding_digest),
            "runId": "source-contract-run",
            "runnerDigest": hex_lower(&bindings.package.digests[5]),
            "runtimeBindingDigest": hex_lower(&bindings.package.digests[15]),
            "startedAt": "2026-07-24T00:00:01.000000Z",
            "unityEditorDigest": hex_lower(&bindings.package.digests[9]),
            "unityPackageDigest": hex_lower(&bindings.package.digests[6])
        },
        "schema": "vrcforge.primitive_basis_live_finalization.v4"
    }))
    .unwrap();
    let origin_ticket = serde_json::json!({
        "issuedAt": "2026-07-24T00:00:00.000000Z",
        "policyId": "authority-policy-v1",
        "runId": "source-contract-run"
    });
    let origin_ticket_digest = digest_value_bytes(&origin_ticket);
    let origin = canonical_ascii_json(&serde_json::json!({
        "authorityTicketDigest": hex_lower(&authority_ticket),
        "cleanupDigest": hex_lower(&cleanup),
        "schema": ORIGIN_ENVELOPE_SCHEMA_V2,
        "signedAt": "2026-07-24T00:00:03.000000Z",
        "ticket": origin_ticket,
        "ticketDigest": hex_lower(&origin_ticket_digest)
    }))
    .unwrap();
    ServiceOwnedVerifiedRuntimeResult::from_verified_terminal(
        finalization,
        origin,
        authority_ticket,
        digest(62),
        cleanup,
        digest(63),
        digest(64),
        digest(65),
        digest(66),
    )
    .unwrap()
}

fn runtime_result_from_origin(
    origin: Value,
    authority_ticket: Digest,
    cleanup: Digest,
) -> Result<ServiceOwnedVerifiedRuntimeResult, ProtectedEvidenceBundleError> {
    let base = runtime_result();
    ServiceOwnedVerifiedRuntimeResult::from_verified_terminal(
        base.finalization_bytes.clone(),
        canonical_ascii_json(&origin).unwrap(),
        authority_ticket,
        base.run_binding_digest,
        cleanup,
        base.prepared_receipt_digest,
        base.armed_receipt_digest,
        base.policy_snapshot_digest,
        base.recovery_bundle_digest,
    )
}

fn readback(result: &ServiceOwnedVerifiedRuntimeResult) -> ReopenedBinaryLedgerReadback {
    ReopenedBinaryLedgerReadback::from_held_and_reopened_ledger(
        digest(70),
        digest(71),
        digest(72),
        digest(73),
        11 * 256,
        22 * 320,
        11,
        0,
        digest(74),
        digest(75),
        10,
        digest(74),
        result.authority_ticket_digest,
    )
    .unwrap()
}

fn terminal(result: &ServiceOwnedVerifiedRuntimeResult) -> DurableBinaryLedgerTerminal {
    DurableBinaryLedgerTerminal::from_reopened_result_commit(
        1,
        digest(76),
        9,
        10,
        digest(77),
        digest(74),
        result.authority_ticket_digest,
        result.finalization_digest,
        10,
        digest(74),
        result.authority_ticket_digest,
        result.run_binding_digest,
        result.prepared_receipt_digest,
        result.armed_receipt_digest,
        result.policy_snapshot_digest,
        result.recovery_bundle_digest,
        result.origin_envelope_digest,
        result.cleanup_digest,
        digest(75),
        "2026-07-24T00:00:00.000000Z",
        "2026-07-24T00:00:01.000000Z",
        "2026-07-24T00:00:04.000000Z",
        readback(result),
    )
    .unwrap()
}

fn projection() -> (
    VerifiedAuthorityResultProjection,
    MockSigner,
    ServiceOwnedVerifiedRuntimeResult,
    DurableBinaryLedgerTerminal,
) {
    let result = runtime_result();
    let terminal = terminal(&result);
    let signer = MockSigner::canonical();
    let observer = signer.clone();
    let projection = producer(signer)
        .produce(&prepared_source(), &result, &terminal)
        .unwrap();
    (projection, observer, result, terminal)
}

#[test]
fn prepared_source_round_trips_and_static_result_drift_fails_before_signing() {
    let source = prepared_source();
    let encoded = source.canonical_bytes();
    assert_eq!(
        PreparedProtectedEvidenceSource::decode(&encoded).unwrap(),
        source
    );
    assert_eq!(
        PreparedProtectedEvidenceSource::decode(&encoded)
            .unwrap()
            .digest(),
        source.digest()
    );
    let mut legacy = encoded.clone();
    legacy[..8].copy_from_slice(b"VRCPEB00");
    assert!(PreparedProtectedEvidenceSource::decode(&legacy).is_err());
    let mut trailing = encoded.clone();
    trailing.push(0);
    assert!(PreparedProtectedEvidenceSource::decode(&trailing).is_err());

    let baseline = runtime_result();
    let mut finalization = baseline.finalization.clone();
    finalization["attestation"]["backendExecutableDigest"] =
        Value::String(hex_lower(&digest(0x99)));
    let drifted = ServiceOwnedVerifiedRuntimeResult::from_verified_terminal(
        canonical_ascii_json(&finalization).unwrap(),
        baseline.origin_envelope_bytes.clone(),
        baseline.authority_ticket_digest,
        baseline.run_binding_digest,
        baseline.cleanup_digest,
        baseline.prepared_receipt_digest,
        baseline.armed_receipt_digest,
        baseline.policy_snapshot_digest,
        baseline.recovery_bundle_digest,
    )
    .unwrap();
    let signer = MockSigner::canonical();
    let observer = signer.clone();
    assert_eq!(
        producer(signer)
            .produce(&source, &drifted, &terminal(&drifted))
            .unwrap_err()
            .code(),
        "protected_result_binding_source_mismatch"
    );
    assert!(observer.signed.lock().unwrap().is_empty());
}

#[test]
fn producer_emits_exact_projection_bundle_ledger_and_hash_domains() {
    let (projection, signer, result, terminal) = projection();
    assert_eq!(projection.sha256(), &sha256(projection.canonical_bytes()));
    assert_eq!(
        projection.finalization_digest(),
        &result.finalization_digest
    );
    assert_eq!(
        projection.origin_envelope_digest(),
        &result.origin_envelope_digest
    );
    let value: Value = serde_json::from_slice(projection.canonical_bytes()).unwrap();
    assert_eq!(value["schema"], PROJECTION_SCHEMA);
    assert_eq!(value.as_object().unwrap().len(), 5);
    let bundle = &value["authorityBundle"];
    let ledger = &value["ledgerSnapshot"];
    assert_eq!(bundle["schema"], AUTHORITY_BUNDLE_SCHEMA);
    assert_eq!(bundle.as_object().unwrap().len(), 15);
    assert_eq!(ledger["schema"], LEDGER_SNAPSHOT_SCHEMA);
    assert_eq!(ledger.as_object().unwrap().len(), 8);
    assert_eq!(
        value["authorityBundleDigest"],
        hex_lower(&digest_value_bytes(bundle))
    );
    assert_eq!(
        value["ledgerSnapshotDigest"],
        hex_lower(&digest_value_bytes(ledger))
    );
    assert_eq!(
        bundle["ledgerSnapshotDigest"],
        value["ledgerSnapshotDigest"]
    );
    let row = &bundle["rows"][0];
    assert_eq!(
        row["finalizationDigest"],
        hex_lower(&sha256(&result.finalization_bytes))
    );
    assert_eq!(
        row["originEnvelopeDigest"],
        hex_lower(&sha256(&result.origin_envelope_bytes))
    );
    let receipt = &ledger["receipts"][0];
    assert_eq!(receipt["schema"], LEDGER_RECEIPT_SCHEMA);
    assert_eq!(
        receipt["ticketDigest"],
        hex_lower(result.authority_ticket_digest())
    );
    assert_eq!(
        receipt["originTicketDigest"],
        hex_lower(result.origin_ticket_digest())
    );
    assert_ne!(receipt["ticketDigest"], receipt["originTicketDigest"]);
    assert_eq!(receipt["ordinal"], 1);
    assert!(receipt.get("sequence").is_none());
    assert!(receipt.get("frameDigest").is_none());
    let binary = &receipt["binaryLedgerTerminal"];
    assert_eq!(binary["event"], "resultCommit");
    assert_eq!(binary["terminalSequence"], terminal.terminal_sequence);
    assert_eq!(binary["anchorSequence"], terminal.terminal_sequence);
    assert_eq!(binary["terminalFrameDigest"], binary["anchorFrameDigest"]);
    assert_eq!(binary["terminalTicketDigest"], binary["anchorTicketDigest"]);
    let mut unsigned_receipt = receipt.clone();
    unsigned_receipt
        .as_object_mut()
        .unwrap()
        .remove("receiptDigest");
    assert_eq!(
        receipt["receiptDigest"],
        hex_lower(&digest_value_bytes(&unsigned_receipt))
    );
    let mut unsigned_bundle = bundle.clone();
    unsigned_bundle.as_object_mut().unwrap().remove("signature");
    assert_eq!(
        signer.signed.lock().unwrap().as_slice(),
        &[digest_value_bytes(&unsigned_bundle)]
    );
}

#[test]
fn v2_keeps_origin_and_authority_tickets_distinct_and_exactly_bound() {
    let result = runtime_result();
    assert_eq!(result.authority_ticket_digest(), &digest(60));
    assert_ne!(
        result.authority_ticket_digest(),
        result.origin_ticket_digest()
    );
    assert_eq!(
        result.origin_ticket_digest(),
        &digest_value_bytes(&result.origin_envelope["ticket"])
    );
    assert_eq!(
        digest_value(&result.origin_envelope, "authorityTicketDigest").unwrap(),
        *result.authority_ticket_digest()
    );
}

#[test]
fn v2_rejects_swapped_or_tampered_ticket_bindings() {
    let base = runtime_result();
    for mutation in ["origin", "authority", "swapped"] {
        let mut origin = base.origin_envelope.clone();
        match mutation {
            "origin" => {
                origin["ticketDigest"] = Value::String(hex_lower(&digest(90)));
            }
            "authority" => {
                origin["authorityTicketDigest"] = Value::String(hex_lower(&digest(91)));
            }
            "swapped" => {
                origin["ticketDigest"] = Value::String(hex_lower(base.authority_ticket_digest()));
                origin["authorityTicketDigest"] =
                    Value::String(hex_lower(base.origin_ticket_digest()));
            }
            _ => unreachable!(),
        }
        let error = runtime_result_from_origin(
            origin,
            *base.authority_ticket_digest(),
            base.cleanup_digest,
        )
        .unwrap_err();
        assert!(matches!(
            error.code(),
            "protected_origin_ticket_digest_mismatch"
                | "protected_authority_ticket_digest_mismatch"
        ));
    }
}

#[test]
fn legacy_v1_result_remains_parse_compatible_but_cannot_enter_protected_output() {
    let authority_ticket = digest(60);
    let cleanup = digest(61);
    let legacy = runtime_result_from_origin(
        serde_json::json!({
            "cleanupDigest": hex_lower(&cleanup),
            "schema": "vrcforge.primitive_basis_live_origin.v1",
            "ticket": {"runId": "legacy-source-contract-run"},
            "ticketDigest": hex_lower(&authority_ticket),
        }),
        authority_ticket,
        cleanup,
    )
    .expect("legacy v1 equality contract remains readable");
    assert_eq!(legacy.ticket_digest(), &authority_ticket);
    assert_eq!(legacy.origin_ticket_digest(), &authority_ticket);
    let terminal = terminal(&legacy);
    assert_eq!(
        producer(MockSigner::canonical())
            .produce(&prepared_source(), &legacy, &terminal)
            .unwrap_err()
            .code(),
        "protected_origin_envelope_v2_required"
    );
}

#[test]
fn unknown_origin_schema_cannot_use_legacy_single_ticket_fallback() {
    let authority_ticket = digest(60);
    let cleanup = digest(61);
    let error = runtime_result_from_origin(
        serde_json::json!({
            "cleanupDigest": hex_lower(&cleanup),
            "schema": "vrcforge.primitive_basis_live_origin.future",
            "ticket": {"runId": "unknown-source-contract-run"},
            "ticketDigest": hex_lower(&authority_ticket),
        }),
        authority_ticket,
        cleanup,
    )
    .unwrap_err();
    assert_eq!(error.code(), "protected_origin_envelope_schema_invalid");
}

#[test]
fn immutable_projection_round_trips_only_with_exact_bytes_and_digest() {
    let (projection, _, _, _) = projection();
    let restored = VerifiedAuthorityResultProjection::from_immutable_ledger_readback(
        projection.canonical_bytes().to_vec(),
        *projection.sha256(),
    )
    .unwrap();
    assert_eq!(restored, projection);

    let mut changed = projection.canonical_bytes().to_vec();
    let changed_index = changed.len() - 2;
    changed[changed_index] ^= 1;
    assert_eq!(
        VerifiedAuthorityResultProjection::from_immutable_ledger_readback(
            changed,
            *projection.sha256()
        )
        .unwrap_err()
        .code(),
        "protected_projection_readback_mismatch"
    );
}

#[test]
fn legacy_v1_completed_receipt_remains_readable_but_is_marked_non_v2() {
    let (projection, _, _, _) = projection();
    let mut value: Value = serde_json::from_slice(projection.canonical_bytes()).unwrap();
    {
        let receipt = &mut value["ledgerSnapshot"]["receipts"][0];
        receipt["schema"] = Value::String(LEDGER_RECEIPT_SCHEMA_V1.to_owned());
        receipt
            .as_object_mut()
            .unwrap()
            .remove("originTicketDigest");
        let mut unsigned = receipt.clone();
        unsigned.as_object_mut().unwrap().remove("receiptDigest");
        receipt["receiptDigest"] = Value::String(hex_lower(&digest_value_bytes(&unsigned)));
    }
    let receipt_digest =
        digest_value(&value["ledgerSnapshot"]["receipts"][0], "receiptDigest").unwrap();
    value["ledgerSnapshot"]["terminalReceiptDigest"] = Value::String(hex_lower(&receipt_digest));
    let ledger_digest = digest_value_bytes(&value["ledgerSnapshot"]);
    value["ledgerSnapshotDigest"] = Value::String(hex_lower(&ledger_digest));
    value["authorityBundle"]["ledgerSnapshotDigest"] = Value::String(hex_lower(&ledger_digest));
    let bundle_digest = digest_value_bytes(&value["authorityBundle"]);
    value["authorityBundleDigest"] = Value::String(hex_lower(&bundle_digest));
    let bytes = canonical_ascii_json(&value).unwrap();
    let restored = VerifiedAuthorityResultProjection::from_immutable_ledger_readback(
        bytes.clone(),
        sha256(&bytes),
    )
    .expect("v1 completed receipts remain parse compatible");
    assert!(!restored.dual_ticket_binding_v2);
    assert_eq!(restored.origin_ticket_digest, restored.ticket_digest);
}

#[test]
fn canonical_parser_rejects_duplicates_noncanonical_non_ascii_and_private_data() {
    for (raw, code) in [
        (
            br#"{"a":1,"a":1}"#.as_slice(),
            "protected_finalization_invalid",
        ),
        (br#"{ "a":1}"#.as_slice(), "protected_finalization_invalid"),
        (
            "{\"value\":\"非ASCII\"}".as_bytes(),
            "protected_private_or_non_ascii_value",
        ),
        (
            br#"{"privateKey":"-----BEGIN PRIVATE KEY-----"}"#.as_slice(),
            "protected_private_or_non_ascii_value",
        ),
    ] {
        let error =
            parse_canonical_ascii_object(raw, 1024, "protected_finalization_invalid").unwrap_err();
        assert!(
            error.code() == code || error.code() == "protected_finalization_invalid",
            "unexpected code {}",
            error.code()
        );
    }
}

#[test]
fn result_origin_cleanup_and_admission_drift_are_rejected_before_signing() {
    let result = runtime_result();
    for field in [
        "result",
        "origin",
        "cleanup",
        "runBinding",
        "prepared",
        "armed",
        "policy",
        "recovery",
    ] {
        let mut terminal = terminal(&result);
        match field {
            "result" => terminal.terminal_result_digest = digest(90),
            "origin" => terminal.origin_envelope_digest = digest(91),
            "cleanup" => terminal.cleanup_digest = digest(92),
            "runBinding" => terminal.run_binding_digest = digest(93),
            "prepared" => terminal.prepared_receipt_digest = digest(94),
            "armed" => terminal.armed_receipt_digest = digest(95),
            "policy" => terminal.policy_snapshot_digest = digest(96),
            "recovery" => terminal.recovery_bundle_digest = digest(97),
            _ => unreachable!(),
        }
        let mut producer = producer(MockSigner::canonical());
        assert_eq!(
            producer
                .produce(&prepared_source(), &result, &terminal)
                .unwrap_err()
                .code(),
            "protected_terminal_result_binding_mismatch"
        );
    }
}

#[test]
fn binary_rollback_anchor_and_reopen_mismatch_are_rejected() {
    let result = runtime_result();
    let valid = readback(&result);
    assert_eq!(
        DurableBinaryLedgerTerminal::from_reopened_result_commit(
            1,
            digest(76),
            10,
            10,
            digest(77),
            digest(74),
            result.authority_ticket_digest,
            result.finalization_digest,
            10,
            digest(74),
            result.authority_ticket_digest,
            result.run_binding_digest,
            result.prepared_receipt_digest,
            result.armed_receipt_digest,
            result.policy_snapshot_digest,
            result.recovery_bundle_digest,
            result.origin_envelope_digest,
            result.cleanup_digest,
            digest(75),
            "2026-07-24T00:00:00.000000Z",
            "2026-07-24T00:00:01.000000Z",
            "2026-07-24T00:00:04.000000Z",
            valid.clone(),
        )
        .unwrap_err()
        .code(),
        "protected_binary_terminal_invalid"
    );
    assert!(ReopenedBinaryLedgerReadback::from_held_and_reopened_ledger(
        digest(70),
        digest(71),
        digest(72),
        digest(73),
        1024,
        2048,
        10,
        0,
        digest(74),
        digest(75),
        10,
        digest(74),
        result.authority_ticket_digest,
    )
    .is_err());
}

#[test]
fn invalid_or_high_s_signatures_are_rejected() {
    let result = runtime_result();
    let terminal = terminal(&result);
    for signature in [[0u8; 64], {
        let mut signature = [0u8; 64];
        signature[31] = 1;
        signature[32..].copy_from_slice(&P256_ORDER);
        signature[63] -= 1;
        signature
    }] {
        let mut signer = MockSigner::canonical();
        signer.signature = signature;
        let error = producer(signer)
            .produce(&prepared_source(), &result, &terminal)
            .unwrap_err();
        assert_eq!(error.code(), "protected_bundle_signature_invalid");
    }
}

#[test]
fn schema_confusion_and_unknown_projection_fields_fail_closed() {
    let (projection, _, _, _) = projection();
    let mut value: Value = serde_json::from_slice(projection.canonical_bytes()).unwrap();
    for mutation in ["installSchema", "unknownField"] {
        let mut changed = value.clone();
        if mutation == "installSchema" {
            changed["schema"] =
                Value::String("vrcforge.primitive_evidence_authority_bundle.v1".to_owned());
        } else {
            changed
                .as_object_mut()
                .unwrap()
                .insert("accepted".to_owned(), Value::Bool(true));
        }
        let bytes = canonical_ascii_json(&changed).unwrap();
        let error = VerifiedAuthorityResultProjection::from_immutable_ledger_readback(
            bytes.clone(),
            sha256(&bytes),
        )
        .unwrap_err();
        assert!(matches!(
            error.code(),
            "protected_projection_schema_invalid" | "protected_projection_shape_invalid"
        ));
    }
    value["authorityBundle"]["originVerified"] = Value::Bool(true);
    let bytes = canonical_ascii_json(&value).unwrap();
    assert!(
        VerifiedAuthorityResultProjection::from_immutable_ledger_readback(
            bytes.clone(),
            sha256(&bytes)
        )
        .is_err()
    );
}

#[test]
fn projection_size_bound_fits_unpadded_base64url_inside_the_wire_limit() {
    let encoded = (MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES * 4 + 2) / 3;
    assert_eq!(MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES, 10_551_296);
    assert_eq!(encoded, 14_068_395);
    assert!(encoded + 2_700_000 < 16 * 1024 * 1024);
    let oversized = vec![b' '; MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES + 1];
    assert_eq!(
        VerifiedAuthorityResultProjection::from_immutable_ledger_readback(
            oversized.clone(),
            sha256(&oversized),
        )
        .unwrap_err()
        .code(),
        "protected_projection_readback_mismatch"
    );
}

#[test]
fn timestamps_require_exact_microseconds_and_real_utc_calendar_values() {
    for valid in ["2024-02-29T23:59:59.000000Z", "2026-07-24T00:00:03.000001Z"] {
        require_timestamp(valid).unwrap();
    }
    for invalid in [
        "0000-01-01T00:00:00.000000Z",
        "2026-02-29T00:00:00.000000Z",
        "2026-04-31T00:00:00.000000Z",
        "2026-13-01T00:00:00.000000Z",
        "2026-07-24T24:00:00.000000Z",
        "2026-07-24T00:60:00.000000Z",
        "2026-07-24T00:00:60.000000Z",
        "2026-07-24T00:00:03Z",
        "2026-07-24T00:00:03.000Z",
    ] {
        assert_eq!(
            require_timestamp(invalid).unwrap_err().code(),
            "protected_timestamp_invalid",
            "unexpectedly accepted {invalid}",
        );
    }
}
