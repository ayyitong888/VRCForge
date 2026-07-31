use super::*;
use std::{
    fs::{self, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::PathBuf,
    process,
    time::{SystemTime, UNIX_EPOCH},
};

#[test]
fn production_runtime_ledger_can_only_arrive_through_the_opaque_published_adoption() {
    let source = include_str!("../primitive_evidence_authority_ledger.rs");
    let wrapper = source
        .split("pub(crate) struct AuthenticatedPublishedAuthorityLedger {")
        .nth(1)
        .and_then(|value| value.split("}\n\n#[cfg(windows)]\nimpl fmt::Debug").next())
        .expect("opaque published ledger wrapper");
    assert!(wrapper.contains("ledger: AuthorityLedger"));
    assert!(!wrapper.contains("pub(crate) ledger:"));
    assert!(!source.contains("impl Clone for AuthenticatedPublishedAuthorityLedger"));
    assert!(source.contains(") -> Result<AuthenticatedPublishedAuthorityLedger, LedgerError>"));
    assert!(
        source.contains("verify_authenticated_runtime_binding(expected_path, expected_identity)")
    );
    assert!(source.contains(
        "self.namespace_verification != LedgerNamespaceVerification::AuthenticatedHeldHandle"
    ));
    assert!(source.contains("|| self.authenticated_namespace.is_none()"));
    assert!(source.contains("|| self.blob_authority.is_none()"));
    for constructor in [
        "pub(crate) fn provision_new(",
        "fn provision_new_inner(",
        "pub(crate) fn open_existing(",
        "fn open_existing_inner(",
        "fn open_new_file(",
        "fn open_existing_file(",
    ] {
        let offset = source.find(constructor).expect("legacy test constructor");
        let prefix = &source[..offset];
        assert!(
            prefix.ends_with("#[cfg(test)]\n    ") || prefix.ends_with("#[cfg(test)]\n"),
            "{constructor} must remain test-only"
        );
    }
}

fn digest(byte: u8) -> String {
    format!("{byte:02x}").repeat(32)
}

fn expect_ledger_error<T>(result: Result<T, LedgerError>) -> LedgerError {
    match result {
        Ok(_) => panic!("operation unexpectedly succeeded"),
        Err(error) => error,
    }
}

fn caps_for_usage_and_reserve(
    usage: GenerationUsage,
    reserve: GenerationOutstandingReserve,
) -> GenerationHardCaps {
    GenerationHardCaps {
        frames: usage.frames.checked_add(reserve.frames).unwrap(),
        tickets: usage.tickets,
        referenced_blobs: usage
            .referenced_blobs
            .checked_add(reserve.referenced_blobs)
            .unwrap(),
        logical_bytes: usage
            .logical_bytes
            .checked_add(reserve.logical_bytes)
            .unwrap(),
        stored_bytes: usage
            .stored_bytes_after(reserve.frames, reserve.blob_stored_bytes)
            .unwrap(),
    }
}

fn identity(generation: u8, signer: u8) -> LedgerIdentity {
    LedgerIdentity::from_hex(&digest(generation), &digest(signer)).unwrap()
}

fn protected_prepared_receipt(source: u8) -> Vec<u8> {
    const ENCODED_LENGTH: usize = 8 + 14 * 32 + 3 * 8;
    const PROTECTED_SOURCE_OFFSET: usize = 8 + 12 * 32;
    let mut value = vec![source.max(1); ENCODED_LENGTH];
    value[..8].copy_from_slice(b"VRCPRP04");
    value[PROTECTED_SOURCE_OFFSET..PROTECTED_SOURCE_OFFSET + 32]
        .copy_from_slice(&[source.max(1); 32]);
    value
}

fn protected_blob_root(path: &PathBuf) -> PathBuf {
    path.with_extension("protected-blobs")
}

fn provision_blob_authority(
    path: &PathBuf,
    ledger_identity: &LedgerIdentity,
) -> (
    ProtectedBlobAuthority,
    crate::primitive_evidence_authority_blob::ProtectedBlobNamespaceDescriptor,
) {
    let root = protected_blob_root(path);
    let _ = fs::remove_dir_all(&root);
    ProtectedBlobAuthority::provision_unsecured_test(
        root,
        *ledger_identity.authority_generation_digest(),
        ledger_identity.canonical_digest(),
    )
    .unwrap()
}

fn remove_protected(path: &PathBuf) {
    remove(path);
    let _ = fs::remove_dir_all(protected_blob_root(path));
}

#[test]
fn ledger_identity_digest_is_canonical_and_binds_both_parts() {
    let authority_generation_digest = [0x11; 32];
    let signer_key_id = [0x22; 32];
    let ledger_identity =
        LedgerIdentity::from_digests(authority_generation_digest, signer_key_id).unwrap();
    let mut expected = Sha256::new();
    expected.update(LEDGER_IDENTITY_DIGEST_DOMAIN);
    expected.update(authority_generation_digest);
    expected.update(signer_key_id);
    let expected: [u8; 32] = expected.finalize().into();

    assert_eq!(
        ledger_identity.authority_generation_digest(),
        &authority_generation_digest
    );
    assert_eq!(ledger_identity.signer_key_id(), &signer_key_id);
    assert_eq!(ledger_identity.canonical_digest(), expected);
    assert_eq!(
        LedgerIdentity::from_hex(&digest(0x11), &digest(0x22))
            .unwrap()
            .canonical_digest(),
        expected
    );
    assert_ne!(
        identity(0x12, 0x22).canonical_digest(),
        ledger_identity.canonical_digest()
    );
    assert_ne!(
        identity(0x11, 0x23).canonical_digest(),
        ledger_identity.canonical_digest()
    );
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
    let _ = fs::remove_file(anchor_path(path));
}

#[test]
fn authenticated_pair_readback_preserves_the_held_writable_pair() {
    let path = temp_path("authenticated-pair-readback");
    remove(&path);
    let ledger_identity = identity(0x15, 0x25);
    let ticket = digest(0x35);
    let run_binding = digest(0x45);
    {
        let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
        let initial = ledger.authenticated_pair_readback().unwrap();
        assert_eq!(initial.frame_count(), 1);
        assert_eq!(initial.active_ticket_count(), 0);
        ledger
            .issue_with_binding_and_recovery(
                &ticket,
                &run_binding,
                TEST_PREPARED_RECEIPT,
                TEST_POLICY_SNAPSHOT,
            )
            .unwrap();
        let issued = ledger.authenticated_pair_readback().unwrap();
        assert!(issued.frame_count() > initial.frame_count());
        assert_eq!(issued.active_ticket_count(), 1);
        ledger.consume(&ticket).unwrap();
    }
    {
        let ledger = AuthorityLedger::open_existing(&path, ledger_identity).unwrap();
        assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Consumed));
    }
    remove(&path);
}

#[cfg(windows)]
#[test]
fn authenticated_exclusive_pair_uses_only_the_already_held_handles() {
    let directory = temp_path("authenticated-exclusive-pair").with_extension("dir");
    let _ = fs::remove_dir_all(&directory);
    fs::create_dir(&directory).unwrap();
    let path = directory.join("ledger.bin");
    let anchor = anchor_path(&path);
    let ledger_identity = identity(0x16, 0x26);
    drop(AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap());
    let file = open_existing_file(&path).unwrap();
    let anchor_file = open_existing_file(&anchor).unwrap();
    let mut ledger = AuthorityLedger::adopt_authenticated_exclusive_pair(
        file,
        path.clone(),
        anchor_file,
        anchor.clone(),
        ledger_identity,
    )
    .unwrap();
    let readback = ledger.authenticated_pair_readback().unwrap();
    assert_eq!(readback.frame_count(), 1);
    assert_eq!(readback.active_ticket_count(), 0);
    drop(ledger);
    fs::remove_file(&path).unwrap();
    fs::remove_file(&anchor).unwrap();
    fs::remove_dir(&directory).unwrap();
}

fn receipt(byte: u8, length: usize) -> Vec<u8> {
    vec![byte; length]
}

fn recovered_burn_proof(
    ticket: &str,
    run_binding: &str,
    prepared_receipt: &[u8],
    armed_receipt: Option<&[u8]>,
    reason: TicketBurnReason,
    marker: u8,
) -> RecoveredBurnProof {
    let ticket_digest = decode_digest(ticket, "ticket").unwrap();
    let run_binding_digest = decode_digest(run_binding, "run_binding").unwrap();
    let prepared_receipt_digest: [u8; 32] = Sha256::digest(prepared_receipt).into();
    let armed_receipt_digest = armed_receipt.map(|receipt| Sha256::digest(receipt).into());
    let stage_journal_head_digest = [marker; 32];
    let termination_intent_digest = [marker.wrapping_add(1); 32];
    let terminal_digest = [marker.wrapping_add(2); 32];
    let cleanup_digest = [marker.wrapping_add(3); 32];
    let recovery_proof_digest = RecoveredBurnProof::canonical_digest(
        ticket_digest,
        run_binding_digest,
        prepared_receipt_digest,
        armed_receipt_digest,
        stage_journal_head_digest,
        termination_intent_digest,
        terminal_digest,
        cleanup_digest,
        reason,
    )
    .unwrap();
    RecoveredBurnProof::from_verified_digest(
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
    )
    .unwrap()
}

fn durable_verified_result(
    ticket: &str,
    run_binding: &str,
    prepared_receipt: &[u8],
    armed_receipt: &[u8],
    policy_snapshot: &[u8],
) -> DurableVerifiedResult {
    let ticket_digest = decode_digest(ticket, "ticket").unwrap();
    let run_binding_digest = decode_digest(run_binding, "run").unwrap();
    let cleanup_digest = [0x55; 32];
    let finalization_bytes = b"{\"ok\":true}".to_vec();
    let origin_envelope_bytes = format!(
        "{{\"cleanupDigest\":\"{}\",\"schema\":\"vrcforge.test_origin.v1\",\"ticket\":{{\"runId\":\"ledger-test\"}},\"ticketDigest\":\"{}\"}}",
        hex_encode(&cleanup_digest),
        ticket,
    )
    .into_bytes();
    let recovery_bundle_digest =
        compute_recovery_bundle_digest(ticket, run_binding, prepared_receipt, policy_snapshot)
            .ok()
            .and_then(|value| decode_digest(&value, "recovery").ok())
            .unwrap();
    DurableVerifiedResult::new(
        finalization_bytes.clone(),
        origin_envelope_bytes.clone(),
        ticket_digest,
        run_binding_digest,
        Sha256::digest(&finalization_bytes).into(),
        Sha256::digest(&origin_envelope_bytes).into(),
        cleanup_digest,
        Sha256::digest(prepared_receipt).into(),
        Sha256::digest(armed_receipt).into(),
        Sha256::digest(policy_snapshot).into(),
        recovery_bundle_digest,
    )
    .unwrap()
}

fn assert_pending_recovery_source(
    pending: &PendingVerifiedResult,
    prepared_receipt: &[u8],
    policy_snapshot: &[u8],
    armed_receipt: &[u8],
    record: &DurableVerifiedResult,
) {
    let recovery_bundle_digest = compute_recovery_bundle_digest(
        &hex_encode(record.ticket_digest()),
        &hex_encode(record.run_binding_digest()),
        prepared_receipt,
        policy_snapshot,
    )
    .ok()
    .and_then(|value| decode_digest(&value, "recovery").ok())
    .unwrap();
    assert_eq!(pending.record(), record);
    assert_eq!(pending.prepared_receipt(), prepared_receipt);
    assert_eq!(pending.canonical_policy_snapshot(), policy_snapshot);
    assert_eq!(pending.armed_receipt(), armed_receipt);
    assert_eq!(pending.recovery_bundle_digest(), &recovery_bundle_digest);
}

fn ledger_bytes(ledger: &mut AuthorityLedger) -> Vec<u8> {
    ledger.file.seek(SeekFrom::Start(0)).unwrap();
    let mut bytes = Vec::new();
    ledger.file.read_to_end(&mut bytes).unwrap();
    ledger.file.seek(SeekFrom::End(0)).unwrap();
    bytes
}

fn anchor_bytes(ledger: &mut AuthorityLedger) -> Vec<u8> {
    ledger.anchor_file.seek(SeekFrom::Start(0)).unwrap();
    let mut bytes = Vec::new();
    ledger.anchor_file.read_to_end(&mut bytes).unwrap();
    ledger.anchor_file.seek(SeekFrom::End(0)).unwrap();
    bytes
}

fn append_frame_bytes(path: &PathBuf, frame: &[u8; FRAME_SIZE]) {
    let decoded = decode_frame(frame).unwrap();
    let mut anchor = OpenOptions::new()
        .read(true)
        .append(true)
        .open(anchor_path(path))
        .unwrap();
    let loaded = load_anchor(&mut anchor, &decoded.identity).unwrap();
    assert!(loaded.trailing_intent.is_none());
    let intent = encode_anchor_record(
        AnchorRecordKind::Intent,
        &decoded.identity,
        loaded.previous_hash,
        frame,
        loaded.terminal,
    )
    .unwrap();
    anchor.write_all(&intent).unwrap();
    anchor.sync_all().unwrap();
    let mut file = OpenOptions::new().append(true).open(path).unwrap();
    file.write_all(frame).unwrap();
    file.sync_all().unwrap();
    let commit = encode_anchor_record(
        AnchorRecordKind::Commit,
        &decoded.identity,
        intent[ANCHOR_HASH_OFFSET..].try_into().unwrap(),
        frame,
        terminal_after_frame(loaded.terminal, frame).unwrap(),
    )
    .unwrap();
    anchor.write_all(&commit).unwrap();
    anchor.sync_all().unwrap();
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
fn read_only_clean_inspection_never_repairs_an_incomplete_anchor_commit() {
    let path = temp_path("readonly-inspection");
    let ledger_identity = identity(0x12, 0x22);
    let ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
    drop(ledger);

    let clean = AuthorityLedger::inspect_existing_clean(&path, ledger_identity.clone()).unwrap();
    assert_eq!(clean.frame_count(), 1);
    assert_eq!(clean.active_ticket_count(), 0);

    let anchor = anchor_path(&path);
    let ledger_bytes = fs::read(&path).unwrap();
    let before = fs::read(&anchor).unwrap();
    let ledger_sha256: [u8; 32] = Sha256::digest(&ledger_bytes).into();
    let anchor_sha256: [u8; 32] = Sha256::digest(&before).into();
    assert_eq!(clean.ledger_byte_length(), ledger_bytes.len() as u64);
    assert_eq!(clean.ledger_sha256(), &ledger_sha256);
    assert_eq!(clean.anchor_byte_length(), before.len() as u64);
    assert_eq!(clean.anchor_sha256(), &anchor_sha256);
    assert_ne!(clean.ledger_sha256(), clean.anchor_sha256());
    let mut file = OpenOptions::new().append(true).open(&anchor).unwrap();
    file.write_all(&[0x44; 17]).unwrap();
    file.sync_all().unwrap();
    drop(file);
    let torn = fs::read(&anchor).unwrap();
    assert_eq!(
        AuthorityLedger::inspect_existing_clean(&path, ledger_identity)
            .unwrap_err()
            .code(),
        "ledger_recovery_required"
    );
    assert_eq!(fs::read(&anchor).unwrap(), torn);
    assert_ne!(torn, before);
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
        let recovery_frames = TEST_PREPARED_RECEIPT.len().div_ceil(PAYLOAD_SIZE)
            + 1
            + TEST_POLICY_SNAPSHOT.len().div_ceil(PAYLOAD_SIZE)
            + 1
            + 1;
        assert_eq!(issued_prefix.len(), FRAME_SIZE * (2 + recovery_frames));
        assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Issued));
        ledger.consume(&ticket).unwrap();
        let consumed_prefix = ledger_bytes(&mut ledger);
        assert_eq!(consumed_prefix.len(), FRAME_SIZE * (3 + recovery_frames));
        assert!(consumed_prefix.starts_with(&issued_prefix));
        ledger.record_result_bytes(&ticket, &result).unwrap();
        let committed = ledger_bytes(&mut ledger);
        let result_frames = result.len().div_ceil(PAYLOAD_SIZE) + 1;
        assert_eq!(
            committed.len(),
            FRAME_SIZE * (3 + recovery_frames + result_frames)
        );
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
fn verified_result_projection_transaction_survives_every_reopen_boundary() {
    let path = temp_path("projection-transaction");
    let ledger_identity = identity(0x61, 0x71);
    let ticket = digest(0x62);
    let run_binding = digest(0x63);
    let prepared = receipt(0x64, 41);
    let armed = receipt(0x65, 43);
    let policy = receipt(0x66, 47);
    let record = durable_verified_result(&ticket, &run_binding, &prepared, &armed, &policy);
    let projection = b"{\"projection\":true}".to_vec();
    let projection_digest: [u8; 32] = Sha256::digest(&projection).into();

    {
        let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
        ledger
            .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
            .unwrap();
        ledger.consume(&ticket).unwrap();
        ledger
            .record_armed_receipt(&ticket, &run_binding, &armed)
            .unwrap();
        ledger
            .record_verified_result_pending(&ticket, &run_binding, &record)
            .unwrap();
        assert_eq!(
            ledger.state(&ticket).unwrap(),
            Some(TicketState::ResultPendingProjection)
        );
        let pending = ledger.pending_verified_results().unwrap();
        assert_eq!(pending.len(), 1);
        assert_pending_recovery_source(&pending[0].1, &prepared, &policy, &armed, &record);
        assert!(!pending[0].1.result_committed());
        assert!(pending[0].1.projection().is_none());
    }
    {
        let mut ledger = AuthorityLedger::open_existing(&path, ledger_identity.clone()).unwrap();
        let pending = ledger.pending_verified_results().unwrap();
        assert_pending_recovery_source(&pending[0].1, &prepared, &policy, &armed, &record);
        assert!(!pending[0].1.result_committed());
        ledger
            .record_result_bytes(&ticket, record.finalization_bytes())
            .unwrap();
    }
    {
        let mut ledger = AuthorityLedger::open_existing(&path, ledger_identity.clone()).unwrap();
        let pending = ledger.pending_verified_results().unwrap();
        assert_pending_recovery_source(&pending[0].1, &prepared, &policy, &armed, &record);
        assert!(pending[0].1.result_committed());
        assert!(pending[0].1.projection().is_none());
        ledger
            .record_projection_pending(&ticket, &run_binding, &projection, &projection_digest)
            .unwrap();
    }
    let receipt = {
        let mut ledger = AuthorityLedger::open_existing(&path, ledger_identity.clone()).unwrap();
        let pending = ledger.pending_verified_results().unwrap();
        assert_pending_recovery_source(&pending[0].1, &prepared, &policy, &armed, &record);
        assert_eq!(
            pending[0].1.projection(),
            Some((projection.as_slice(), &projection_digest))
        );
        ledger
            .commit_projection(&ticket, &run_binding, &projection_digest)
            .unwrap();
        let receipt = ledger
            .projection_commit_receipt_from_held_pair(&ticket, &run_binding, &projection)
            .unwrap();
        assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Result));
        assert!(ledger.pending_verified_results().unwrap().is_empty());
        assert_eq!(
            ledger.projection_bytes(&ticket).unwrap(),
            Some((projection.clone(), projection_digest))
        );
        let later = digest(0x6f);
        ledger.issue(&later).unwrap();
        ledger.consume(&later).unwrap();
        ledger.burn(&later).unwrap();
        receipt
    };
    assert_eq!(receipt.event(), "projectionCommit");
    assert_eq!(
        receipt.authority_generation_digest(),
        ledger_identity.authority_generation_digest()
    );
    assert_eq!(
        receipt.ledger_identity_digest(),
        &ledger_identity.canonical_digest()
    );
    assert!(receipt.verifies_for(
        ledger_identity.authority_generation_digest(),
        &ledger_identity.canonical_digest(),
        &decode_digest(&ticket, "ticket").unwrap(),
        &decode_digest(&run_binding, "run").unwrap(),
        &projection,
    ));
    assert!(!receipt.verifies_for(
        &[0x99; 32],
        &ledger_identity.canonical_digest(),
        &decode_digest(&ticket, "ticket").unwrap(),
        &decode_digest(&run_binding, "run").unwrap(),
        &projection,
    ));
    assert!(!receipt.verifies_for(
        ledger_identity.authority_generation_digest(),
        &[0x98; 32],
        &decode_digest(&ticket, "ticket").unwrap(),
        &decode_digest(&run_binding, "run").unwrap(),
        &projection,
    ));
    assert_eq!(receipt.terminal_sequence(), receipt.anchor_sequence());
    assert_eq!(
        receipt.terminal_frame_digest(),
        receipt.anchor_frame_digest()
    );
    assert_eq!(
        receipt.latest_frame_digest(),
        receipt.terminal_frame_digest()
    );
    assert_eq!(receipt.active_ticket_count(), 0);
    let replayed = AuthorityLedger::reopen_projection_commit_receipt(
        &path,
        ledger_identity,
        &ticket,
        &run_binding,
        b"{\"projection\":true}",
    )
    .unwrap();
    assert_eq!(replayed, receipt);
    remove(&path);
}

#[test]
fn generation_usage_caps_accept_exact_boundaries_and_reject_each_overflow() {
    let stored_boundary = 2 * STORED_FRAME_BYTES + 20;
    let caps = GenerationHardCaps {
        frames: 2,
        tickets: 1,
        referenced_blobs: 1,
        logical_bytes: 10,
        stored_bytes: stored_boundary,
    };
    let mut exact = GenerationUsage::default();
    exact
        .commit_add(2, 1, 1, 10, 20, caps)
        .expect("exact generation boundary");
    assert_eq!(exact.stored_bytes().unwrap(), stored_boundary);

    for (additional, code) in [
        ((1, 0, 0, 0, 0), "ledger_generation_frame_limit_exceeded"),
        ((0, 1, 0, 0, 0), "ledger_generation_ticket_limit_exceeded"),
        ((0, 0, 1, 0, 0), "ledger_generation_blob_limit_exceeded"),
        ((0, 0, 0, 1, 0), "ledger_generation_logical_limit_exceeded"),
        ((0, 0, 0, 0, 1), "ledger_generation_stored_limit_exceeded"),
    ] {
        let mut candidate = exact;
        let before = candidate;
        assert_eq!(
            candidate
                .commit_add(
                    additional.0,
                    additional.1,
                    additional.2,
                    additional.3,
                    additional.4,
                    caps,
                )
                .unwrap_err()
                .code(),
            code
        );
        assert_eq!(candidate, before);
    }
}

#[test]
fn protected_outstanding_reserve_is_state_derived_and_exactly_cap_checked() {
    let ticket = digest(0x41);
    let run_binding = digest(0x42);
    let prepared = protected_prepared_receipt(0x43);
    let armed = receipt(0x44, 64);
    let policy = receipt(0x45, 96);
    let record = durable_verified_result(&ticket, &run_binding, &prepared, &armed, &policy);
    let run_binding_digest = decode_digest(&run_binding, "run").unwrap();
    let recovery_bundle_digest = [0x46; 32];
    let burn_reserve = GenerationOutstandingReserve {
        frames: 1,
        ..GenerationOutstandingReserve::default()
    };

    let issued = StoredTicketState::Issued {
        run_binding_digest,
        prepared_receipt: Some(prepared.clone()),
        canonical_policy_snapshot: Some(policy.clone()),
        recovery_bundle_digest: Some(recovery_bundle_digest),
    };
    assert_eq!(
        protected_outstanding_reserve_for_state(&issued).unwrap(),
        burn_reserve
    );
    let consumed_unarmed = StoredTicketState::Consumed {
        run_binding_digest,
        prepared_receipt: prepared.clone(),
        canonical_policy_snapshot: policy.clone(),
        recovery_bundle_digest,
        armed_receipt: None,
    };
    assert_eq!(
        protected_outstanding_reserve_for_state(&consumed_unarmed).unwrap(),
        burn_reserve
    );
    let consumed_armed = StoredTicketState::Consumed {
        run_binding_digest,
        prepared_receipt: prepared.clone(),
        canonical_policy_snapshot: policy.clone(),
        recovery_bundle_digest,
        armed_receipt: Some(armed.clone()),
    };
    let armed_reserve = protected_armed_success_reserve().unwrap();
    assert_eq!(
        protected_outstanding_reserve_for_state(&consumed_armed).unwrap(),
        armed_reserve
    );
    assert_eq!(armed_reserve.frames, 4);
    assert_eq!(armed_reserve.referenced_blobs, 3);
    assert_eq!(
        armed_reserve.logical_bytes,
        (MAX_VERIFIED_RESULT_RECORD_SIZE + MAX_RESULT_SIZE + MAX_RESULT_PROJECTION_SIZE) as u64
    );

    let pending_empty = StoredTicketState::ResultPendingProjection {
        run_binding_digest,
        prepared_receipt: prepared.clone(),
        canonical_policy_snapshot: policy.clone(),
        recovery_bundle_digest,
        armed_receipt: armed.clone(),
        verified_result: record.clone(),
        result: None,
        projection: None,
    };
    assert_eq!(
        protected_outstanding_reserve_for_state(&pending_empty).unwrap(),
        protected_pending_result_reserve(record.finalization_bytes().len()).unwrap()
    );
    let result_bytes = record.finalization_bytes().to_vec();
    let result_digest: [u8; 32] = Sha256::digest(&result_bytes).into();
    let pending_result = StoredTicketState::ResultPendingProjection {
        run_binding_digest,
        prepared_receipt: prepared.clone(),
        canonical_policy_snapshot: policy.clone(),
        recovery_bundle_digest,
        armed_receipt: armed.clone(),
        verified_result: record.clone(),
        result: Some((result_bytes.clone(), result_digest)),
        projection: None,
    };
    assert_eq!(
        protected_outstanding_reserve_for_state(&pending_result).unwrap(),
        protected_pending_projection_reserve().unwrap()
    );
    let projection = b"projection".to_vec();
    let projection_digest: [u8; 32] = Sha256::digest(&projection).into();
    let pending_commit = StoredTicketState::ResultPendingProjection {
        run_binding_digest,
        prepared_receipt: prepared.clone(),
        canonical_policy_snapshot: policy.clone(),
        recovery_bundle_digest,
        armed_receipt: armed.clone(),
        verified_result: record.clone(),
        result: Some((result_bytes, result_digest)),
        projection: Some((projection.clone(), projection_digest)),
    };
    assert_eq!(
        protected_outstanding_reserve_for_state(&pending_commit).unwrap(),
        burn_reserve
    );
    let impossible = StoredTicketState::ResultPendingProjection {
        run_binding_digest,
        prepared_receipt: prepared,
        canonical_policy_snapshot: policy,
        recovery_bundle_digest,
        armed_receipt: armed,
        verified_result: record,
        result: None,
        projection: Some((projection, projection_digest)),
    };
    assert_eq!(
        protected_outstanding_reserve_for_state(&impossible)
            .unwrap_err()
            .code(),
        "ledger_transition_invalid"
    );
    for terminal in [
        StoredTicketState::Result {
            run_binding_digest,
            bytes: b"done".to_vec(),
            digest: Sha256::digest(b"done").into(),
            projection: None,
        },
        StoredTicketState::Burned {
            run_binding_digest,
            reason: TicketBurnReason::Failed,
            recovery_proof_digest: None,
        },
    ] {
        assert_eq!(
            protected_outstanding_reserve_for_state(&terminal).unwrap(),
            GenerationOutstandingReserve::default()
        );
    }

    let usage = GenerationUsage {
        frames: 2,
        tickets: 1,
        referenced_blobs: 1,
        logical_bytes: 5,
        blob_stored_bytes: 7,
    };
    let exact_caps = caps_for_usage_and_reserve(usage, armed_reserve);
    ensure_generation_operation_with_reserve(usage, 0, 0, 0, 0, 0, armed_reserve, exact_caps)
        .unwrap();
    for (caps, code) in [
        (
            GenerationHardCaps {
                frames: exact_caps.frames - 1,
                ..exact_caps
            },
            "ledger_generation_frame_limit_exceeded",
        ),
        (
            GenerationHardCaps {
                referenced_blobs: exact_caps.referenced_blobs - 1,
                ..exact_caps
            },
            "ledger_generation_blob_limit_exceeded",
        ),
        (
            GenerationHardCaps {
                logical_bytes: exact_caps.logical_bytes - 1,
                ..exact_caps
            },
            "ledger_generation_logical_limit_exceeded",
        ),
        (
            GenerationHardCaps {
                stored_bytes: exact_caps.stored_bytes - 1,
                ..exact_caps
            },
            "ledger_generation_stored_limit_exceeded",
        ),
    ] {
        assert_eq!(
            ensure_generation_operation_with_reserve(usage, 0, 0, 0, 0, 0, armed_reserve, caps,)
                .unwrap_err()
                .code(),
            code
        );
    }
}

#[test]
fn generation_operation_caps_fail_before_issue_or_blob_create() {
    let path = temp_path("generation-operation-preflight");
    let ledger_identity = identity(0x71, 0x72);
    let prepared = protected_prepared_receipt(0x73);
    let policy = receipt(0x74, 1024);
    let ticket = digest(0x75);
    let run_binding = digest(0x76);
    let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity).unwrap();
    let operation_frames =
        2 + blob_frame_count(prepared.len()).unwrap() + blob_frame_count(policy.len()).unwrap();
    ledger.generation_usage.frames = MAX_GENERATION_FRAME_COUNT - operation_frames + 1;
    let before_sequence = ledger.next_sequence;
    let before_ledger_length = ledger.file.metadata().unwrap().len();
    let before_anchor_length = ledger.anchor_file.metadata().unwrap().len();
    assert_eq!(
        ledger
            .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
            .unwrap_err()
            .code(),
        "ledger_generation_frame_limit_exceeded"
    );
    assert_eq!(ledger.next_sequence, before_sequence);
    assert_eq!(ledger.file.metadata().unwrap().len(), before_ledger_length);
    assert_eq!(
        ledger.anchor_file.metadata().unwrap().len(),
        before_anchor_length
    );
    assert!(!ledger
        .states
        .contains_key(&decode_digest(&ticket, "ticket").unwrap()));

    ledger.generation_usage = GenerationUsage {
        frames: ledger.next_sequence,
        tickets: MAX_GENERATION_TICKET_COUNT,
        ..GenerationUsage::default()
    };
    assert_eq!(
        ledger
            .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
            .unwrap_err()
            .code(),
        "ledger_generation_ticket_limit_exceeded"
    );
    assert_eq!(ledger.next_sequence, before_sequence);
    drop(ledger);
    remove(&path);

    let path = temp_path("generation-blob-preflight");
    let ledger_identity = identity(0x77, 0x78);
    let (blob_authority, _) = provision_blob_authority(&path, &ledger_identity);
    let mut ledger =
        AuthorityLedger::provision_new_with_blob_authority(&path, ledger_identity, blob_authority)
            .unwrap();
    ledger
        .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
        .unwrap();
    ledger.consume(&ticket).unwrap();
    let armed = receipt(0x79, 96);
    ledger
        .record_armed_receipt(&ticket, &run_binding, &armed)
        .unwrap();
    let record = durable_verified_result(&ticket, &run_binding, &prepared, &armed, &policy);
    ledger
        .record_verified_result_pending(&ticket, &run_binding, &record)
        .unwrap();
    ledger.generation_usage.referenced_blobs = MAX_GENERATION_REFERENCED_BLOB_COUNT;
    let before_sequence = ledger.next_sequence;
    let before_create_count = ledger.protected_blob_metrics().unwrap().create_count;
    assert_eq!(
        ledger
            .record_result_bytes(&ticket, record.finalization_bytes())
            .unwrap_err()
            .code(),
        "ledger_generation_blob_limit_exceeded"
    );
    assert_eq!(ledger.next_sequence, before_sequence);
    assert_eq!(
        ledger
            .blob_authority
            .as_ref()
            .expect("typed blob authority")
            .metrics()
            .create_count,
        before_create_count
    );
    drop(ledger);
    remove_protected(&path);
}

#[test]
fn protected_lane_rejects_direct_result_and_armed_reserve_fails_before_first_frame() {
    let path = temp_path("protected-armed-atomic-reserve");
    let ledger_identity = identity(0x51, 0x52);
    let ticket = digest(0x53);
    let ticket_digest = decode_digest(&ticket, "ticket").unwrap();
    let run_binding = digest(0x54);
    let prepared = protected_prepared_receipt(0x55);
    let policy = receipt(0x56, 192);
    let armed = receipt(0x57, 257);
    let (blob_authority, _) = provision_blob_authority(&path, &ledger_identity);
    let mut ledger =
        AuthorityLedger::provision_new_with_blob_authority(&path, ledger_identity, blob_authority)
            .unwrap();
    ledger
        .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
        .unwrap();
    ledger.consume(&ticket).unwrap();

    let before_sequence = ledger.next_sequence;
    let before_state = ledger.states.get(&ticket_digest).cloned().unwrap();
    assert_eq!(
        ledger
            .record_result_bytes(&ticket, b"unverified protected result")
            .unwrap_err()
            .code(),
        "protected_result_verification_required"
    );
    assert_eq!(ledger.next_sequence, before_sequence);
    assert_eq!(ledger.states.get(&ticket_digest), Some(&before_state));
    assert_eq!(ledger.protected_blob_metrics().unwrap().create_count, 0);

    let armed_frames = blob_frame_count(armed.len()).unwrap();
    let success_reserve = protected_armed_success_reserve().unwrap();
    ledger.generation_usage.frames = MAX_GENERATION_FRAME_COUNT
        .checked_sub(armed_frames)
        .and_then(|value| value.checked_sub(success_reserve.frames))
        .unwrap()
        + 1;
    let before_ledger_length = ledger.file.metadata().unwrap().len();
    let before_anchor_length = ledger.anchor_file.metadata().unwrap().len();
    assert_eq!(
        ledger
            .record_armed_receipt(&ticket, &run_binding, &armed)
            .unwrap_err()
            .code(),
        "ledger_generation_frame_limit_exceeded"
    );
    assert_eq!(ledger.next_sequence, before_sequence);
    assert_eq!(ledger.file.metadata().unwrap().len(), before_ledger_length);
    assert_eq!(
        ledger.anchor_file.metadata().unwrap().len(),
        before_anchor_length
    );
    assert_eq!(ledger.states.get(&ticket_digest), Some(&before_state));
    drop(ledger);
    remove_protected(&path);
}

#[test]
fn restart_recomputes_generation_caps_and_rejects_oversized_files_before_allocation() {
    let path = temp_path("generation-replay-caps");
    let ledger_identity = identity(0x79, 0x7a);
    let prepared = protected_prepared_receipt(0x7b);
    let policy = receipt(0x7c, 256);
    let ticket = digest(0x7d);
    let run_binding = digest(0x7e);
    let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
    ledger
        .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
        .unwrap();
    ledger.consume(&ticket).unwrap();
    ledger
        .record_result_bytes(&ticket, b"inline terminal")
        .unwrap();
    let exact = ledger.generation_usage;
    drop(ledger);

    let exact_caps = GenerationHardCaps {
        frames: exact.frames,
        tickets: exact.tickets,
        referenced_blobs: exact.referenced_blobs,
        logical_bytes: exact.logical_bytes,
        stored_bytes: exact.stored_bytes().unwrap(),
    };
    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(&path)
        .unwrap();
    let loaded = load_frames_with_caps(&mut file, &ledger_identity, None, exact_caps).unwrap();
    assert_eq!(loaded.generation_usage, exact);
    for (caps, code) in [
        (
            GenerationHardCaps {
                frames: exact.frames - 1,
                ..exact_caps
            },
            "ledger_generation_frame_limit_exceeded",
        ),
        (
            GenerationHardCaps {
                tickets: exact.tickets - 1,
                ..exact_caps
            },
            "ledger_generation_ticket_limit_exceeded",
        ),
        (
            GenerationHardCaps {
                logical_bytes: exact.logical_bytes - 1,
                ..exact_caps
            },
            "ledger_generation_logical_limit_exceeded",
        ),
        (
            GenerationHardCaps {
                stored_bytes: exact.stored_bytes().unwrap() - 1,
                ..exact_caps
            },
            "ledger_generation_stored_limit_exceeded",
        ),
    ] {
        assert_eq!(
            expect_ledger_error(load_frames_with_caps(
                &mut file,
                &ledger_identity,
                None,
                caps,
            ))
            .code(),
            code
        );
    }
    drop(file);
    remove(&path);

    let path = temp_path("generation-ledger-metadata-cap");
    let ledger_identity = identity(0x81, 0x82);
    drop(AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap());
    OpenOptions::new()
        .write(true)
        .open(&path)
        .unwrap()
        .set_len(MAX_GENERATION_LEDGER_BYTES + FRAME_SIZE as u64)
        .unwrap();
    assert_eq!(
        expect_ledger_error(AuthorityLedger::open_existing(&path, ledger_identity)).code(),
        "ledger_generation_frame_limit_exceeded"
    );
    remove(&path);

    let path = temp_path("generation-anchor-metadata-cap");
    let ledger_identity = identity(0x83, 0x84);
    drop(AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap());
    let maximum_anchor_bytes = MAX_GENERATION_FRAME_COUNT * 2 * ANCHOR_RECORD_SIZE as u64;
    OpenOptions::new()
        .write(true)
        .open(anchor_path(&path))
        .unwrap()
        .set_len(maximum_anchor_bytes + 1)
        .unwrap();
    assert_eq!(
        expect_ledger_error(AuthorityLedger::open_existing(&path, ledger_identity)).code(),
        "ledger_generation_frame_limit_exceeded"
    );
    remove(&path);
}

#[test]
fn protected_initial_replay_rejects_inline_evidence_and_budgets_before_blob_body_read() {
    let inline_path = temp_path("protected-required-inline-hostile");
    let ledger_identity = identity(0x85, 0x86);
    let prepared = protected_prepared_receipt(0x87);
    let policy = receipt(0x88, 256);
    let ticket = digest(0x89);
    let run_binding = digest(0x8a);
    let mut inline = AuthorityLedger::provision_new(&inline_path, ledger_identity.clone()).unwrap();
    inline
        .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
        .unwrap();
    inline.consume(&ticket).unwrap();
    inline
        .record_result_bytes(&ticket, b"complete inline terminal")
        .unwrap();
    drop(inline);
    let (authority, _) = provision_blob_authority(&inline_path, &ledger_identity);
    assert_eq!(
        expect_ledger_error(AuthorityLedger::open_existing_with_blob_authority(
            &inline_path,
            ledger_identity,
            authority,
        ))
        .code(),
        "protected_blob_inline_evidence_forbidden"
    );
    remove_protected(&inline_path);

    let path = temp_path("protected-replay-budget");
    let ledger_identity = identity(0x8b, 0x8c);
    let (authority, descriptor) = provision_blob_authority(&path, &ledger_identity);
    let mut ledger = AuthorityLedger::provision_new_with_blob_authority(
        &path,
        ledger_identity.clone(),
        authority,
    )
    .unwrap();
    ledger
        .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
        .unwrap();
    ledger.consume(&ticket).unwrap();
    let armed = receipt(0x8d, 96);
    ledger
        .record_armed_receipt(&ticket, &run_binding, &armed)
        .unwrap();
    let record = durable_verified_result(&ticket, &run_binding, &prepared, &armed, &policy);
    ledger
        .record_verified_result_pending(&ticket, &run_binding, &record)
        .unwrap();
    let exact = ledger.generation_usage;
    drop(ledger);

    let mut authority = ProtectedBlobAuthority::reopen_unsecured_test(
        protected_blob_root(&path),
        descriptor.clone(),
    )
    .unwrap();
    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(&path)
        .unwrap();
    let count_caps = GenerationHardCaps {
        frames: exact.frames,
        tickets: exact.tickets,
        referenced_blobs: 0,
        logical_bytes: exact.logical_bytes,
        stored_bytes: exact.stored_bytes().unwrap(),
    };
    assert_eq!(
        expect_ledger_error(load_frames_with_caps(
            &mut file,
            &ledger_identity,
            Some(&mut authority),
            count_caps,
        ))
        .code(),
        "ledger_generation_blob_limit_exceeded"
    );
    assert_eq!(authority.metrics().open_count, 0);
    drop(authority);

    let mut authority =
        ProtectedBlobAuthority::reopen_unsecured_test(protected_blob_root(&path), descriptor)
            .unwrap();
    let logical_caps = GenerationHardCaps {
        logical_bytes: exact.logical_bytes - 1,
        referenced_blobs: exact.referenced_blobs,
        ..count_caps
    };
    assert_eq!(
        expect_ledger_error(load_frames_with_caps(
            &mut file,
            &ledger_identity,
            Some(&mut authority),
            logical_caps,
        ))
        .code(),
        "protected_blob_generation_limit_exceeded"
    );
    assert_eq!(authority.metrics().open_count, 1);
    assert_eq!(
        authority.metrics().bytes_read,
        crate::primitive_evidence_authority_blob::PROTECTED_BLOB_HEADER_SIZE as u64
    );
    drop(file);
    drop(authority);
    remove_protected(&path);
}

#[test]
fn protected_replay_rejects_every_inline_evidence_event_before_state_checks() {
    let ledger_identity = identity(0x5e, 0x6e);
    let ticket_digest = decode_digest(&digest(0x7e), "test").unwrap();
    let inline_events = [
        Event::ResultChunk,
        Event::ResultCommit,
        Event::VerifiedResultChunk,
        Event::VerifiedResultCommit,
        Event::ProjectionChunk,
        Event::ProjectionPendingCommit,
    ];

    for (index, event) in inline_events.into_iter().enumerate() {
        let encoded = encode_frame(
            event,
            index as u64 + 1,
            &ledger_identity,
            ticket_digest,
            [0x8e; 32],
            [0x9e; 32],
            b"hostile-inline-evidence",
        )
        .unwrap();
        let frame = decode_frame(&encoded).unwrap();
        let mut states = BTreeMap::new();
        let mut pending_blob = None;
        let mut referenced_blob_names = BTreeSet::new();
        let mut generation_usage = GenerationUsage::default();

        assert_eq!(
            apply_loaded_event(
                &mut states,
                &mut pending_blob,
                &frame,
                None,
                &mut referenced_blob_names,
                &mut generation_usage,
                PRODUCTION_GENERATION_HARD_CAPS,
                ProtectedBlobReplayPolicy::ProtectedRequired,
                true,
            )
            .unwrap_err()
            .code(),
            "protected_blob_inline_evidence_forbidden",
            "event {event:?} reached state-dependent replay"
        );
        assert!(states.is_empty());
        assert!(pending_blob.is_none());
        assert!(referenced_blob_names.is_empty());
        assert_eq!(generation_usage, GenerationUsage::default());
    }
}

#[test]
fn protected_replay_rejects_crafted_result_blob_bind_from_consumed_state_before_blob_read() {
    let path = temp_path("protected-result-bind-bypass");
    let ledger_identity = identity(0x58, 0x59);
    let ticket = digest(0x5a);
    let ticket_digest = decode_digest(&ticket, "ticket").unwrap();
    let run_binding = digest(0x5b);
    let prepared = protected_prepared_receipt(0x5c);
    let policy = receipt(0x5d, 256);
    let result = b"crafted unverified protected result";
    let (blob_authority, descriptor) = provision_blob_authority(&path, &ledger_identity);
    let mut ledger = AuthorityLedger::provision_new_with_blob_authority(
        &path,
        ledger_identity.clone(),
        blob_authority,
    )
    .unwrap();
    ledger
        .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
        .unwrap();
    ledger.consume(&ticket).unwrap();
    let context = protected_blob_context_for_state(
        ProtectedBlobKind::ResultCommit,
        ticket_digest,
        ledger.states.get(&ticket_digest).unwrap(),
    )
    .unwrap();
    let reopened = ledger
        .blob_authority
        .as_mut()
        .unwrap()
        .materialize(context, result)
        .unwrap();
    let content_digest = *reopened.reference().content_digest();
    let binding_digest = *reopened.reference().binding_digest();
    ledger
        .append_frame_raw(
            Event::ResultBlobBind,
            ticket_digest,
            content_digest,
            &binding_digest,
        )
        .unwrap();
    drop(ledger);

    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(&path)
        .unwrap();
    let mut authority =
        ProtectedBlobAuthority::reopen_unsecured_test(protected_blob_root(&path), descriptor)
            .unwrap();
    assert_eq!(
        expect_ledger_error(load_frames_with_caps(
            &mut file,
            &ledger_identity,
            Some(&mut authority),
            PRODUCTION_GENERATION_HARD_CAPS,
        ))
        .code(),
        "ledger_transition_invalid"
    );
    assert_eq!(authority.metrics().open_count, 0);
    assert_eq!(authority.metrics().bytes_read, 0);
    drop(file);
    drop(authority);
    remove_protected(&path);
}

#[test]
fn protected_outstanding_reserve_is_consumed_by_each_durable_success_step() {
    let path = temp_path("protected-reserve-consumption");
    let ledger_identity = identity(0x61, 0x62);
    let ticket = digest(0x63);
    let run_binding = digest(0x64);
    let prepared = protected_prepared_receipt(0x65);
    let policy = receipt(0x66, 384);
    let armed = receipt(0x67, 192);
    let record = durable_verified_result(&ticket, &run_binding, &prepared, &armed, &policy);
    let projection = b"protected reserve projection".to_vec();
    let projection_digest: [u8; 32] = Sha256::digest(&projection).into();
    let (blob_authority, _) = provision_blob_authority(&path, &ledger_identity);
    let mut ledger =
        AuthorityLedger::provision_new_with_blob_authority(&path, ledger_identity, blob_authority)
            .unwrap();

    ledger
        .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
        .unwrap();
    let issued = protected_outstanding_reserve_for_states(&ledger.states).unwrap();
    assert_eq!(issued.frames, 1);
    assert_eq!(issued.referenced_blobs, 0);
    ledger.consume(&ticket).unwrap();
    assert_eq!(
        protected_outstanding_reserve_for_states(&ledger.states).unwrap(),
        issued
    );

    ledger
        .record_armed_receipt(&ticket, &run_binding, &armed)
        .unwrap();
    let armed_reserve = protected_outstanding_reserve_for_states(&ledger.states).unwrap();
    assert_eq!(armed_reserve, protected_armed_success_reserve().unwrap());
    ensure_generation_operation_with_reserve(
        ledger.generation_usage,
        0,
        0,
        0,
        0,
        0,
        armed_reserve,
        PRODUCTION_GENERATION_HARD_CAPS,
    )
    .unwrap();

    ledger
        .record_verified_result_pending(&ticket, &run_binding, &record)
        .unwrap();
    let after_verified = protected_outstanding_reserve_for_states(&ledger.states).unwrap();
    assert_eq!(
        after_verified,
        protected_pending_result_reserve(record.finalization_bytes().len()).unwrap()
    );
    assert_eq!(after_verified.frames, armed_reserve.frames - 1);
    assert_eq!(
        after_verified.referenced_blobs,
        armed_reserve.referenced_blobs - 1
    );

    ledger
        .record_result_bytes(&ticket, record.finalization_bytes())
        .unwrap();
    let after_result = protected_outstanding_reserve_for_states(&ledger.states).unwrap();
    assert_eq!(
        after_result,
        protected_pending_projection_reserve().unwrap()
    );
    assert_eq!(after_result.frames, after_verified.frames - 1);
    assert_eq!(
        after_result.referenced_blobs,
        after_verified.referenced_blobs - 1
    );

    ledger
        .record_projection_pending(&ticket, &run_binding, &projection, &projection_digest)
        .unwrap();
    let after_projection = protected_outstanding_reserve_for_states(&ledger.states).unwrap();
    assert_eq!(after_projection.frames, 1);
    assert_eq!(after_projection.referenced_blobs, 0);
    assert_eq!(after_projection.logical_bytes, 0);
    ledger
        .commit_projection(&ticket, &run_binding, &projection_digest)
        .unwrap();
    assert_eq!(
        protected_outstanding_reserve_for_states(&ledger.states).unwrap(),
        GenerationOutstandingReserve::default()
    );
    drop(ledger);
    remove_protected(&path);
}

#[test]
fn protected_restart_recomputes_armed_reserve_and_rejects_one_frame_short_history() {
    let path = temp_path("protected-armed-replay-reserve");
    let ledger_identity = identity(0x68, 0x69);
    let ticket = digest(0x6a);
    let run_binding = digest(0x6b);
    let prepared = protected_prepared_receipt(0x6c);
    let policy = receipt(0x6d, 320);
    let armed = receipt(0x6e, 160);
    let (blob_authority, descriptor) = provision_blob_authority(&path, &ledger_identity);
    let mut ledger = AuthorityLedger::provision_new_with_blob_authority(
        &path,
        ledger_identity.clone(),
        blob_authority,
    )
    .unwrap();
    ledger
        .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
        .unwrap();
    ledger.consume(&ticket).unwrap();
    ledger
        .record_armed_receipt(&ticket, &run_binding, &armed)
        .unwrap();
    let usage = ledger.generation_usage;
    let reserve = protected_outstanding_reserve_for_states(&ledger.states).unwrap();
    let exact_caps = caps_for_usage_and_reserve(usage, reserve);
    drop(ledger);

    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(&path)
        .unwrap();
    let mut authority = ProtectedBlobAuthority::reopen_unsecured_test(
        protected_blob_root(&path),
        descriptor.clone(),
    )
    .unwrap();
    let loaded = load_frames_with_caps(
        &mut file,
        &ledger_identity,
        Some(&mut authority),
        exact_caps,
    )
    .unwrap();
    assert_eq!(loaded.generation_usage, usage);
    assert_eq!(
        protected_outstanding_reserve_for_states(&loaded.states).unwrap(),
        reserve
    );
    drop(authority);

    let mut authority =
        ProtectedBlobAuthority::reopen_unsecured_test(protected_blob_root(&path), descriptor)
            .unwrap();
    assert_eq!(
        expect_ledger_error(load_frames_with_caps(
            &mut file,
            &ledger_identity,
            Some(&mut authority),
            GenerationHardCaps {
                frames: exact_caps.frames - 1,
                ..exact_caps
            },
        ))
        .code(),
        "ledger_generation_frame_limit_exceeded"
    );
    drop(file);
    drop(authority);
    remove_protected(&path);
}

#[test]
fn protected_restart_cleans_maximum_unbound_orphan_before_outstanding_reserve_check() {
    let path = temp_path("protected-max-orphan-reserve");
    let ledger_identity = identity(0x71, 0x72);
    let ticket = digest(0x73);
    let ticket_digest = decode_digest(&ticket, "ticket").unwrap();
    let run_binding = digest(0x74);
    let run_binding_digest = decode_digest(&run_binding, "run").unwrap();
    let prepared = protected_prepared_receipt(0x75);
    let policy = receipt(0x76, 512);
    let armed = receipt(0x77, 256);
    let finalization = vec![0x78; MAX_RESULT_SIZE];
    let origin = vec![0x79; MAX_ORIGIN_ENVELOPE_SIZE];
    let recovery_bundle_digest = decode_digest(
        &compute_recovery_bundle_digest(&ticket, &run_binding, &prepared, &policy).unwrap(),
        "recovery",
    )
    .unwrap();
    let record = DurableVerifiedResult::new(
        finalization.clone(),
        origin.clone(),
        ticket_digest,
        run_binding_digest,
        Sha256::digest(&finalization).into(),
        Sha256::digest(&origin).into(),
        [0x7a; 32],
        Sha256::digest(&prepared).into(),
        Sha256::digest(&armed).into(),
        Sha256::digest(&policy).into(),
        recovery_bundle_digest,
    )
    .unwrap();
    assert_eq!(record.encode().len(), MAX_VERIFIED_RESULT_RECORD_SIZE);
    let (blob_authority, descriptor) = provision_blob_authority(&path, &ledger_identity);
    let mut ledger = AuthorityLedger::provision_new_with_blob_authority(
        &path,
        ledger_identity.clone(),
        blob_authority,
    )
    .unwrap();
    ledger
        .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
        .unwrap();
    ledger.consume(&ticket).unwrap();
    ledger
        .record_armed_receipt(&ticket, &run_binding, &armed)
        .unwrap();
    let usage = ledger.generation_usage;
    let reserve = protected_outstanding_reserve_for_states(&ledger.states).unwrap();
    let exact_caps = caps_for_usage_and_reserve(usage, reserve);
    let context = protected_blob_context_for_state(
        ProtectedBlobKind::VerifiedResult,
        ticket_digest,
        ledger.states.get(&ticket_digest).unwrap(),
    )
    .unwrap();
    ledger
        .blob_authority
        .as_mut()
        .unwrap()
        .materialize(context, &record.encode())
        .unwrap();
    drop(ledger);

    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(&path)
        .unwrap();
    let mut authority = ProtectedBlobAuthority::reopen_unsecured_test(
        protected_blob_root(&path),
        descriptor.clone(),
    )
    .unwrap();
    assert_eq!(authority.namespace_usage().0, 1);
    assert_eq!(
        expect_ledger_error(load_frames_with_caps(
            &mut file,
            &ledger_identity,
            Some(&mut authority),
            exact_caps,
        ))
        .code(),
        "ledger_generation_stored_limit_exceeded"
    );
    assert_eq!(authority.namespace_usage().0, 1);
    drop(authority);

    let mut authority =
        ProtectedBlobAuthority::reopen_unsecured_test(protected_blob_root(&path), descriptor)
            .unwrap();
    let mut loaded = load_frames_with_caps_mode(
        &mut file,
        &ledger_identity,
        Some(&mut authority),
        exact_caps,
        false,
    )
    .unwrap();
    reconcile_loaded_protected_namespace(&mut loaded, &mut authority, exact_caps).unwrap();
    assert_eq!(authority.namespace_usage(), (0, 0));
    assert_eq!(authority.metrics().cleanup_count, 1);
    assert_eq!(loaded.generation_usage, usage);
    assert_eq!(
        protected_outstanding_reserve_for_states(&loaded.states).unwrap(),
        reserve
    );
    drop(file);
    drop(authority);
    remove_protected(&path);
}

#[test]
fn fresh_protected_restart_reopens_result_and_projection_receipts_at_exact_physical_cap() {
    let path = temp_path("protected-fresh-terminal-readback");
    let ledger_identity = identity(0x7b, 0x7c);
    let ticket = digest(0x7d);
    let run_binding = digest(0x7e);
    let prepared = protected_prepared_receipt(0x7f);
    let policy = receipt(0x80, 640);
    let armed = receipt(0x81, 320);
    let record = durable_verified_result(&ticket, &run_binding, &prepared, &armed, &policy);
    let projection = vec![0x82; MAX_RESULT_PROJECTION_SIZE];
    let projection_digest: [u8; 32] = Sha256::digest(&projection).into();
    let (blob_authority, descriptor) = provision_blob_authority(&path, &ledger_identity);
    let mut ledger = AuthorityLedger::provision_new_with_blob_authority(
        &path,
        ledger_identity.clone(),
        blob_authority,
    )
    .unwrap();
    ledger
        .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
        .unwrap();
    ledger.consume(&ticket).unwrap();
    ledger
        .record_armed_receipt(&ticket, &run_binding, &armed)
        .unwrap();
    ledger
        .record_verified_result_pending(&ticket, &run_binding, &record)
        .unwrap();
    ledger
        .record_result_bytes(&ticket, record.finalization_bytes())
        .unwrap();
    drop(ledger);

    let authority = ProtectedBlobAuthority::reopen_unsecured_test(
        protected_blob_root(&path),
        descriptor.clone(),
    )
    .unwrap();
    let mut reopened = AuthorityLedger::open_existing_with_blob_authority(
        &path,
        ledger_identity.clone(),
        authority,
    )
    .unwrap();
    let result_readback = reopened
        .result_commit_readback_from_held_pair(&ticket, &run_binding, record.finalization_bytes())
        .unwrap();
    let result_prefix_count = result_readback.terminal_sequence() as usize + 1;
    assert_eq!(
        result_readback.terminal_result_digest(),
        record.finalization_digest()
    );
    assert_eq!(
        result_readback.run_binding_digest(),
        record.run_binding_digest()
    );
    assert_eq!(
        result_readback.terminal_ticket_digest(),
        record.ticket_digest()
    );
    reopened
        .record_projection_pending(&ticket, &run_binding, &projection, &projection_digest)
        .unwrap();
    reopened
        .commit_projection(&ticket, &run_binding, &projection_digest)
        .unwrap();
    let first_projection_receipt = reopened
        .projection_commit_receipt_from_held_pair(&ticket, &run_binding, &projection)
        .unwrap();
    let terminal_usage = reopened.generation_usage;
    let complete_ledger_bytes = ledger_bytes(&mut reopened);
    let frames: Vec<[u8; FRAME_SIZE]> = complete_ledger_bytes
        .chunks_exact(FRAME_SIZE)
        .map(|frame| frame.try_into().unwrap())
        .collect();
    assert_eq!(
        protected_outstanding_reserve_for_states(&reopened.states).unwrap(),
        GenerationOutstandingReserve::default()
    );
    drop(reopened);

    let mut authority = ProtectedBlobAuthority::reopen_unsecured_test(
        protected_blob_root(&path),
        descriptor.clone(),
    )
    .unwrap();
    let prefix = load_committed_frame_prefix_with_caps(
        &frames,
        result_prefix_count,
        &ledger_identity,
        Some(&mut authority),
        PRODUCTION_GENERATION_HARD_CAPS,
    )
    .unwrap();
    let prefix_reserve = protected_outstanding_reserve_for_states(&prefix.states).unwrap();
    let prefix_exact_caps = caps_for_usage_and_reserve(prefix.generation_usage, prefix_reserve);
    assert!(authority.namespace_usage().1 > prefix.generation_usage.blob_stored_bytes);
    drop(authority);

    let mut authority = ProtectedBlobAuthority::reopen_unsecured_test(
        protected_blob_root(&path),
        descriptor.clone(),
    )
    .unwrap();
    let exact_prefix = load_committed_frame_prefix_with_caps(
        &frames,
        result_prefix_count,
        &ledger_identity,
        Some(&mut authority),
        prefix_exact_caps,
    )
    .unwrap();
    assert_eq!(exact_prefix.generation_usage, prefix.generation_usage);
    assert_eq!(
        protected_outstanding_reserve_for_states(&exact_prefix.states).unwrap(),
        prefix_reserve
    );
    drop(authority);

    let exact_caps =
        caps_for_usage_and_reserve(terminal_usage, GenerationOutstandingReserve::default());
    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(&path)
        .unwrap();
    let mut authority = ProtectedBlobAuthority::reopen_unsecured_test(
        protected_blob_root(&path),
        descriptor.clone(),
    )
    .unwrap();
    let loaded = load_frames_with_caps(
        &mut file,
        &ledger_identity,
        Some(&mut authority),
        exact_caps,
    )
    .unwrap();
    assert_eq!(loaded.generation_usage, terminal_usage);
    assert_eq!(authority.namespace_usage().0, 3);
    drop(authority);

    let mut authority = ProtectedBlobAuthority::reopen_unsecured_test(
        protected_blob_root(&path),
        descriptor.clone(),
    )
    .unwrap();
    assert_eq!(
        expect_ledger_error(load_frames_with_caps(
            &mut file,
            &ledger_identity,
            Some(&mut authority),
            GenerationHardCaps {
                stored_bytes: exact_caps.stored_bytes - 1,
                ..exact_caps
            },
        ))
        .code(),
        "ledger_generation_stored_limit_exceeded"
    );
    assert_eq!(authority.metrics().open_count, 0);
    assert_eq!(authority.metrics().bytes_read, 0);
    drop(file);
    drop(authority);

    let authority =
        ProtectedBlobAuthority::reopen_unsecured_test(protected_blob_root(&path), descriptor)
            .unwrap();
    let mut reopened =
        AuthorityLedger::open_existing_with_blob_authority(&path, ledger_identity, authority)
            .unwrap();
    let replayed_result_readback = reopened
        .result_commit_readback_from_held_pair(&ticket, &run_binding, record.finalization_bytes())
        .unwrap();
    assert_eq!(
        replayed_result_readback.terminal_frame_digest(),
        result_readback.terminal_frame_digest()
    );
    assert_eq!(
        replayed_result_readback.terminal_result_digest(),
        result_readback.terminal_result_digest()
    );
    let replayed_projection_receipt = reopened
        .projection_commit_receipt_from_held_pair(&ticket, &run_binding, &projection)
        .unwrap();
    assert_eq!(replayed_projection_receipt, first_projection_receipt);
    drop(reopened);
    remove_protected(&path);
}

#[test]
fn protected_blob_transaction_uses_one_frame_per_large_value_and_exact_terminal_readback() {
    let path = temp_path("protected-blob-transaction");
    let ledger_identity = identity(0x81, 0x91);
    let ticket = digest(0x82);
    let run_binding = digest(0x83);
    let prepared = protected_prepared_receipt(0x84);
    let armed = receipt(0x85, 480);
    let policy = receipt(0x86, 4096);
    let record = durable_verified_result(&ticket, &run_binding, &prepared, &armed, &policy);
    let projection = vec![0x87; 2 * 1024 * 1024 + 17];
    let projection_digest: [u8; 32] = Sha256::digest(&projection).into();
    let (blob_authority, descriptor) = provision_blob_authority(&path, &ledger_identity);
    let terminal;

    {
        let mut ledger = AuthorityLedger::provision_new_with_blob_authority(
            &path,
            ledger_identity.clone(),
            blob_authority,
        )
        .unwrap();
        assert!(ledger.has_protected_blob_authority());
        ledger
            .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
            .unwrap();
        ledger.consume(&ticket).unwrap();
        ledger
            .record_armed_receipt(&ticket, &run_binding, &armed)
            .unwrap();

        let before_verified = ledger.next_sequence;
        ledger
            .record_verified_result_pending(&ticket, &run_binding, &record)
            .unwrap();
        assert_eq!(ledger.next_sequence, before_verified + 1);
        let before_result = ledger.next_sequence;
        ledger
            .record_result_bytes(&ticket, record.finalization_bytes())
            .unwrap();
        assert_eq!(ledger.next_sequence, before_result + 1);
        terminal = ledger
            .result_commit_readback_from_held_pair(
                &ticket,
                &run_binding,
                record.finalization_bytes(),
            )
            .unwrap();
        assert_eq!(terminal.frame_count(), terminal.terminal_sequence() + 1);
        assert_eq!(
            terminal.latest_frame_digest(),
            terminal.terminal_frame_digest()
        );
        assert_eq!(
            terminal.terminal_result_digest(),
            record.finalization_digest()
        );
        assert_eq!(terminal.run_binding_digest(), record.run_binding_digest());

        let before_projection = ledger.next_sequence;
        ledger
            .record_projection_pending(&ticket, &run_binding, &projection, &projection_digest)
            .unwrap();
        assert_eq!(ledger.next_sequence, before_projection + 1);
        let metrics = ledger.protected_blob_metrics().unwrap();
        assert_eq!(metrics.create_count, 3);
        assert_eq!(metrics.blob_flush_count, 3);
        assert_eq!(metrics.directory_flush_count, 3);
        assert!(metrics.bytes_written > projection.len() as u64);
        ledger
            .commit_projection(&ticket, &run_binding, &projection_digest)
            .unwrap();
        let projection_receipt = ledger
            .projection_commit_receipt_from_held_pair(&ticket, &run_binding, &projection)
            .unwrap();
        assert_eq!(projection_receipt.active_ticket_count(), 0);
    }

    assert_eq!(
        AuthorityLedger::open_existing(&path, ledger_identity.clone())
            .unwrap_err()
            .code(),
        "protected_blob_authority_not_connected"
    );
    let reopened_authority =
        ProtectedBlobAuthority::reopen_unsecured_test(protected_blob_root(&path), descriptor)
            .unwrap();
    let reopened = AuthorityLedger::open_existing_with_blob_authority(
        &path,
        ledger_identity,
        reopened_authority,
    )
    .unwrap();
    assert_eq!(reopened.state(&ticket).unwrap(), Some(TicketState::Result));
    assert_eq!(
        reopened.projection_bytes(&ticket).unwrap(),
        Some((projection, projection_digest))
    );
    drop(reopened);
    remove_protected(&path);
}

#[test]
fn protected_blob_orphan_and_all_three_ledger_commit_crash_boundaries_recover() {
    for phase in 0..=4usize {
        let path = temp_path(&format!("protected-blob-crash-{phase}"));
        let ledger_identity = identity(0x92, 0xa2);
        let ticket = digest(0x93);
        let run_binding = digest(0x94);
        let prepared = protected_prepared_receipt(0x95);
        let armed = receipt(0x96, 480);
        let policy = receipt(0x97, 1024);
        let record = durable_verified_result(&ticket, &run_binding, &prepared, &armed, &policy);
        let (blob_authority, descriptor) = provision_blob_authority(&path, &ledger_identity);
        {
            let mut ledger = AuthorityLedger::provision_new_with_blob_authority(
                &path,
                ledger_identity.clone(),
                blob_authority,
            )
            .unwrap();
            ledger
                .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
                .unwrap();
            ledger.consume(&ticket).unwrap();
            ledger
                .record_armed_receipt(&ticket, &run_binding, &armed)
                .unwrap();
            let ticket_digest = decode_digest(&ticket, "ticket").unwrap();
            let context = protected_blob_context_for_state(
                ProtectedBlobKind::VerifiedResult,
                ticket_digest,
                ledger.states.get(&ticket_digest).unwrap(),
            )
            .unwrap();
            let encoded = record.encode();
            let reopened = ledger
                .blob_authority
                .as_mut()
                .unwrap()
                .materialize(context, &encoded)
                .unwrap();
            let frame = encode_frame(
                Event::VerifiedResultBlobBind,
                ledger.next_sequence,
                &ledger.identity,
                ticket_digest,
                *reopened.reference().content_digest(),
                ledger.previous_hash,
                reopened.reference().binding_digest(),
            )
            .unwrap();
            if phase > 0 {
                let intent = encode_anchor_record(
                    AnchorRecordKind::Intent,
                    &ledger.identity,
                    ledger.anchor_previous_hash,
                    &frame,
                    ledger.terminal_anchor,
                )
                .unwrap();
                ledger.anchor_file.write_all(&intent).unwrap();
                ledger.anchor_file.sync_all().unwrap();
                if phase >= 2 {
                    let write_length = if phase == 2 {
                        FRAME_SIZE / 2
                    } else {
                        FRAME_SIZE
                    };
                    ledger.file.write_all(&frame[..write_length]).unwrap();
                    ledger.file.sync_all().unwrap();
                }
                if phase == 4 {
                    let commit = encode_anchor_record(
                        AnchorRecordKind::Commit,
                        &ledger.identity,
                        intent[ANCHOR_HASH_OFFSET..].try_into().unwrap(),
                        &frame,
                        terminal_after_frame(ledger.terminal_anchor, &frame).unwrap(),
                    )
                    .unwrap();
                    ledger
                        .anchor_file
                        .write_all(&commit[..ANCHOR_RECORD_SIZE / 2])
                        .unwrap();
                    ledger.anchor_file.sync_all().unwrap();
                }
            }
        }

        let reopened_authority =
            ProtectedBlobAuthority::reopen_unsecured_test(protected_blob_root(&path), descriptor)
                .unwrap();
        let ledger = AuthorityLedger::open_existing_with_blob_authority(
            &path,
            ledger_identity,
            reopened_authority,
        )
        .unwrap();
        if phase == 0 {
            assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Consumed));
            assert_eq!(
                ledger
                    .blob_authority
                    .as_ref()
                    .unwrap()
                    .cleanup_receipts()
                    .len(),
                1
            );
        } else {
            assert_eq!(
                ledger.state(&ticket).unwrap(),
                Some(TicketState::ResultPendingProjection)
            );
            assert!(ledger
                .blob_authority
                .as_ref()
                .unwrap()
                .cleanup_receipts()
                .is_empty());
        }
        drop(ledger);
        remove_protected(&path);
    }
}

#[test]
fn verified_result_and_projection_replacements_are_rejected() {
    let path = temp_path("projection-replacement");
    let ledger_identity = identity(0x67, 0x77);
    let ticket = digest(0x68);
    let run_binding = digest(0x69);
    let prepared = receipt(0x6a, 41);
    let armed = receipt(0x6b, 43);
    let policy = receipt(0x6c, 47);
    let record = durable_verified_result(&ticket, &run_binding, &prepared, &armed, &policy);
    let projection = b"{\"projection\":true}".to_vec();
    let projection_digest: [u8; 32] = Sha256::digest(&projection).into();
    let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
    ledger
        .issue_with_binding_and_recovery(&ticket, &run_binding, &prepared, &policy)
        .unwrap();
    ledger.consume(&ticket).unwrap();
    ledger
        .record_armed_receipt(&ticket, &run_binding, &armed)
        .unwrap();
    ledger
        .record_verified_result_pending(&ticket, &run_binding, &record)
        .unwrap();
    let mut replacement = record.clone();
    replacement.cleanup_digest = [0x7d; 32];
    assert_eq!(
        ledger
            .record_verified_result_pending(&ticket, &run_binding, &replacement)
            .unwrap_err()
            .code(),
        "verified_result_record_binding_mismatch"
    );
    assert_eq!(
        ledger
            .record_result_bytes(&ticket, b"replacement")
            .unwrap_err()
            .code(),
        "ticket_transition_invalid"
    );
    ledger
        .record_result_bytes(&ticket, record.finalization_bytes())
        .unwrap();
    ledger
        .record_projection_pending(&ticket, &run_binding, &projection, &projection_digest)
        .unwrap();
    let other_projection = b"{\"projection\":false}";
    let other_digest: [u8; 32] = Sha256::digest(other_projection).into();
    assert_eq!(
        ledger
            .record_projection_pending(&ticket, &run_binding, other_projection, &other_digest)
            .unwrap_err()
            .code(),
        "projection_binding_mismatch"
    );
    assert_eq!(
        ledger
            .commit_projection(&ticket, &run_binding, &other_digest)
            .unwrap_err()
            .code(),
        "projection_binding_mismatch"
    );
    ledger
        .commit_projection(&ticket, &run_binding, &projection_digest)
        .unwrap();
    ledger
        .commit_projection(&ticket, &run_binding, &projection_digest)
        .unwrap();
    drop(ledger);
    assert_eq!(
        AuthorityLedger::reopen_projection_commit_receipt(
            &path,
            ledger_identity.clone(),
            &ticket,
            &digest(0x7e),
            &projection,
        )
        .unwrap_err()
        .code(),
        "projection_binding_mismatch"
    );
    assert_eq!(
        AuthorityLedger::reopen_projection_commit_receipt(
            &path,
            ledger_identity,
            &ticket,
            &run_binding,
            other_projection,
        )
        .unwrap_err()
        .code(),
        "projection_binding_mismatch"
    );
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
        assert_eq!(
            ledger.burn_reason(&issued).unwrap(),
            Some(TicketBurnReason::RestartRecovery)
        );
        assert_eq!(
            ledger.burn_reason(&consumed).unwrap(),
            Some(TicketBurnReason::RestartRecovery)
        );
        assert_eq!(ledger.state(&complete).unwrap(), Some(TicketState::Result));
        assert_eq!(ledger.burn_active().unwrap(), 0);
    }
    {
        let ledger = AuthorityLedger::open_existing(&path, identity(0x13, 0x23)).unwrap();
        assert_eq!(
            ledger.burn_reason(&issued).unwrap(),
            Some(TicketBurnReason::RestartRecovery)
        );
    }
    remove(&path);
}

#[test]
fn active_ticket_recovery_requires_the_exact_persisted_run_binding() {
    let path = temp_path("bound-recovery");
    let ticket = digest(0x46);
    let run_binding = digest(0x47);
    {
        let mut ledger = AuthorityLedger::provision_new(&path, identity(0x16, 0x26)).unwrap();
        let prepared = receipt(0xa1, 73);
        let armed = receipt(0xb2, 91);
        ledger
            .issue_with_binding_and_receipt(&ticket, &run_binding, &prepared)
            .unwrap();
        ledger.consume(&ticket).unwrap();
        ledger
            .record_armed_receipt(&ticket, &run_binding, &armed)
            .unwrap();
        let active = ledger.active_tickets().unwrap();
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].ticket_digest(), ticket);
        assert_eq!(active[0].run_binding_digest(), run_binding);
        assert_eq!(active[0].prepared_receipt(), prepared);
        assert_eq!(active[0].canonical_policy_snapshot(), TEST_POLICY_SNAPSHOT);
        assert_eq!(active[0].armed_receipt(), Some(armed.as_slice()));
        assert_eq!(
            ledger
                .burn_recovered(&ticket, &digest(0x48))
                .unwrap_err()
                .code(),
            "ticket_run_binding_mismatch"
        );
        assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Consumed));
        ledger.burn_recovered(&ticket, &run_binding).unwrap();
        assert!(ledger.active_tickets().unwrap().is_empty());
        assert_eq!(
            ledger.burn_reason(&ticket).unwrap(),
            Some(TicketBurnReason::RestartRecovery)
        );
    }
    let ledger = AuthorityLedger::open_existing(&path, identity(0x16, 0x26)).unwrap();
    assert_eq!(
        ledger.run_binding_digest(&ticket).unwrap(),
        Some(run_binding)
    );
    remove(&path);
}

#[test]
fn recovered_pre_armed_normal_terminal_reasons_persist_exact_proof() {
    for (index, reason) in [TicketBurnReason::Cancelled, TicketBurnReason::TimedOut]
        .into_iter()
        .enumerate()
    {
        let path = temp_path(match reason {
            TicketBurnReason::Cancelled => "recovered-pre-armed-cancelled",
            TicketBurnReason::TimedOut => "recovered-pre-armed-timed-out",
            _ => unreachable!(),
        });
        let ledger_identity = identity(0x17 + index as u8, 0x27 + index as u8);
        let ticket = digest(0x49 + index as u8);
        let run_binding = digest(0x4b + index as u8);
        let prepared = receipt(0xb3 + index as u8, 73);
        let proof = recovered_burn_proof(
            &ticket,
            &run_binding,
            &prepared,
            None,
            reason,
            0x31 + index as u8 * 4,
        );
        let proof_digest = hex_encode(proof.recovery_proof_digest());
        {
            let mut ledger =
                AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
            ledger
                .issue_with_binding_and_receipt(&ticket, &run_binding, &prepared)
                .unwrap();
            ledger.consume(&ticket).unwrap();
            assert_eq!(ledger.active_tickets().unwrap()[0].armed_receipt(), None);
            ledger
                .burn_recovered_with_reason(&ticket, &run_binding, reason, &proof)
                .unwrap();
            assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Burned));
            assert_eq!(ledger.burn_reason(&ticket).unwrap(), Some(reason));
            assert_eq!(
                ledger.recovered_burn_proof_digest(&ticket).unwrap(),
                Some(proof_digest.clone())
            );
        }
        let ledger = AuthorityLedger::open_existing(&path, ledger_identity).unwrap();
        assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Burned));
        assert_eq!(ledger.burn_reason(&ticket).unwrap(), Some(reason));
        assert_eq!(
            ledger.recovered_burn_proof_digest(&ticket).unwrap(),
            Some(proof_digest)
        );
        remove(&path);
    }
}

#[test]
fn recovered_normal_terminal_requires_valid_exact_receipt_bindings() {
    let path = temp_path("recovered-proof-bindings");
    let ledger_identity = identity(0x19, 0x29);
    let ticket = digest(0x4d);
    let run_binding = digest(0x4e);
    let prepared = receipt(0xd1, 71);
    let armed = receipt(0xe1, 89);
    let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity).unwrap();
    ledger
        .issue_with_binding_and_receipt(&ticket, &run_binding, &prepared)
        .unwrap();
    ledger.consume(&ticket).unwrap();
    ledger
        .record_armed_receipt(&ticket, &run_binding, &armed)
        .unwrap();

    let exact = recovered_burn_proof(
        &ticket,
        &run_binding,
        &prepared,
        Some(&armed),
        TicketBurnReason::Cancelled,
        0x41,
    );
    let without_armed = recovered_burn_proof(
        &ticket,
        &run_binding,
        &prepared,
        None,
        TicketBurnReason::Cancelled,
        0x45,
    );
    let wrong_prepared = recovered_burn_proof(
        &ticket,
        &run_binding,
        &receipt(0xd2, 71),
        Some(&armed),
        TicketBurnReason::Cancelled,
        0x49,
    );
    let wrong_armed = recovered_burn_proof(
        &ticket,
        &run_binding,
        &prepared,
        Some(&receipt(0xe2, 89)),
        TicketBurnReason::Cancelled,
        0x4d,
    );
    for proof in [&without_armed, &wrong_prepared, &wrong_armed] {
        assert_eq!(
            ledger
                .burn_recovered_with_reason(
                    &ticket,
                    &run_binding,
                    TicketBurnReason::Cancelled,
                    proof,
                )
                .unwrap_err()
                .code(),
            "ledger_recovery_proof_binding_mismatch"
        );
    }
    assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Consumed));
    ledger
        .burn_recovered_with_reason(&ticket, &run_binding, TicketBurnReason::Cancelled, &exact)
        .unwrap();
    remove(&path);
}

#[test]
fn recovered_burn_rejects_invalid_cross_bound_or_replacement_proof() {
    let path = temp_path("recovered-terminal-conflicts");
    let ledger_identity = identity(0x1a, 0x2a);
    let ticket = digest(0x4f);
    let run_binding = digest(0x50);
    let other_ticket = digest(0x51);
    let other_binding = digest(0x52);
    let result_ticket = digest(0x53);
    let result_binding = digest(0x54);
    let prepared = receipt(0xd3, 71);
    let other_prepared = receipt(0xd4, 71);
    let result_prepared = receipt(0xd5, 71);
    let proof = recovered_burn_proof(
        &ticket,
        &run_binding,
        &prepared,
        None,
        TicketBurnReason::Cancelled,
        0x51,
    );
    let replacement = recovered_burn_proof(
        &ticket,
        &run_binding,
        &prepared,
        None,
        TicketBurnReason::Cancelled,
        0x55,
    );
    let timed_out = recovered_burn_proof(
        &ticket,
        &run_binding,
        &prepared,
        None,
        TicketBurnReason::TimedOut,
        0x59,
    );
    let other_proof = recovered_burn_proof(
        &other_ticket,
        &other_binding,
        &other_prepared,
        None,
        TicketBurnReason::Cancelled,
        0x5d,
    );
    let wrong_run_proof = recovered_burn_proof(
        &ticket,
        &other_binding,
        &prepared,
        None,
        TicketBurnReason::Cancelled,
        0x60,
    );
    let result_proof = recovered_burn_proof(
        &result_ticket,
        &result_binding,
        &result_prepared,
        None,
        TicketBurnReason::TimedOut,
        0x61,
    );
    {
        let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
        for (active_ticket, active_binding, prepared_receipt) in [
            (&ticket, &run_binding, &prepared),
            (&other_ticket, &other_binding, &other_prepared),
            (&result_ticket, &result_binding, &result_prepared),
        ] {
            ledger
                .issue_with_binding_and_receipt(active_ticket, active_binding, prepared_receipt)
                .unwrap();
            ledger.consume(active_ticket).unwrap();
        }

        let mut zero_proof = proof.clone();
        zero_proof.recovery_proof_digest = ZERO_DIGEST;
        let mut wrong_proof = proof.clone();
        wrong_proof.recovery_proof_digest = [0x7f; 32];
        for invalid in [&zero_proof, &wrong_proof] {
            assert_eq!(
                ledger
                    .burn_recovered_with_reason(
                        &ticket,
                        &run_binding,
                        TicketBurnReason::Cancelled,
                        invalid,
                    )
                    .unwrap_err()
                    .code(),
                "ledger_recovery_proof_invalid"
            );
        }
        assert_eq!(
            ledger
                .burn_recovered_with_reason(
                    &ticket,
                    &other_binding,
                    TicketBurnReason::Cancelled,
                    &proof,
                )
                .unwrap_err()
                .code(),
            "ticket_run_binding_mismatch"
        );
        assert_eq!(
            ledger
                .burn_recovered_with_reason(
                    &other_ticket,
                    &other_binding,
                    TicketBurnReason::Cancelled,
                    &proof,
                )
                .unwrap_err()
                .code(),
            "ledger_recovery_proof_binding_mismatch"
        );
        assert_eq!(
            ledger
                .burn_recovered_with_reason(
                    &ticket,
                    &run_binding,
                    TicketBurnReason::Cancelled,
                    &wrong_run_proof,
                )
                .unwrap_err()
                .code(),
            "ledger_recovery_proof_binding_mismatch"
        );
        assert_eq!(
            ledger
                .burn_recovered_with_reason(
                    &ticket,
                    &run_binding,
                    TicketBurnReason::Failed,
                    &proof,
                )
                .unwrap_err()
                .code(),
            "ledger_recovery_burn_reason_invalid"
        );

        ledger
            .record_result_bytes(&result_ticket, b"durable-result")
            .unwrap();
        assert_eq!(
            ledger
                .burn_recovered_with_reason(
                    &result_ticket,
                    &result_binding,
                    TicketBurnReason::TimedOut,
                    &result_proof,
                )
                .unwrap_err()
                .code(),
            "ticket_transition_invalid"
        );
        assert_eq!(
            ledger.result_bytes(&result_ticket).unwrap(),
            Some(b"durable-result".to_vec())
        );

        ledger
            .burn_recovered_with_reason(&ticket, &run_binding, TicketBurnReason::Cancelled, &proof)
            .unwrap();
        let original_digest = hex_encode(proof.recovery_proof_digest());
        for (conflicting_reason, conflicting_proof) in [
            (TicketBurnReason::Cancelled, &replacement),
            (TicketBurnReason::TimedOut, &timed_out),
        ] {
            assert_eq!(
                ledger
                    .burn_recovered_with_reason(
                        &ticket,
                        &run_binding,
                        conflicting_reason,
                        conflicting_proof,
                    )
                    .unwrap_err()
                    .code(),
                "ticket_transition_invalid"
            );
        }
        assert_eq!(
            ledger.recovered_burn_proof_digest(&ticket).unwrap(),
            Some(original_digest)
        );

        ledger
            .burn_recovered(&other_ticket, &other_binding)
            .unwrap();
        assert_eq!(
            ledger.burn_reason(&other_ticket).unwrap(),
            Some(TicketBurnReason::RestartRecovery)
        );
        assert_eq!(
            ledger.recovered_burn_proof_digest(&other_ticket).unwrap(),
            None
        );
        assert_ne!(
            hex_encode(other_proof.recovery_proof_digest()),
            hex_encode(proof.recovery_proof_digest())
        );
    }
    let ledger = AuthorityLedger::open_existing(&path, ledger_identity).unwrap();
    assert_eq!(
        ledger.burn_reason(&ticket).unwrap(),
        Some(TicketBurnReason::Cancelled)
    );
    assert_eq!(
        ledger.recovered_burn_proof_digest(&ticket).unwrap(),
        Some(hex_encode(proof.recovery_proof_digest()))
    );
    assert_eq!(
        ledger.burn_reason(&other_ticket).unwrap(),
        Some(TicketBurnReason::RestartRecovery)
    );
    assert_eq!(
        ledger.result_bytes(&result_ticket).unwrap(),
        Some(b"durable-result".to_vec())
    );
    remove(&path);
}

#[test]
fn recovered_normal_terminal_rejects_ledger_or_anchor_prefix_rollback() {
    for rollback_anchor in [false, true] {
        let path = temp_path(if rollback_anchor {
            "recovered-anchor-prefix-rollback"
        } else {
            "recovered-ledger-prefix-rollback"
        });
        let ledger_identity = identity(
            if rollback_anchor { 0x1b } else { 0x1c },
            if rollback_anchor { 0x2b } else { 0x2c },
        );
        let ticket = digest(if rollback_anchor { 0x55 } else { 0x56 });
        let run_binding = digest(if rollback_anchor { 0x57 } else { 0x58 });
        let prepared = receipt(0xe1, 75);
        let proof = recovered_burn_proof(
            &ticket,
            &run_binding,
            &prepared,
            None,
            TicketBurnReason::TimedOut,
            0x65,
        );
        let (old_ledger_prefix, old_anchor_prefix) = {
            let mut ledger =
                AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
            ledger
                .issue_with_binding_and_receipt(&ticket, &run_binding, &prepared)
                .unwrap();
            ledger.consume(&ticket).unwrap();
            let prefixes = (ledger_bytes(&mut ledger), anchor_bytes(&mut ledger));
            ledger
                .burn_recovered_with_reason(
                    &ticket,
                    &run_binding,
                    TicketBurnReason::TimedOut,
                    &proof,
                )
                .unwrap();
            prefixes
        };

        if rollback_anchor {
            fs::write(anchor_path(&path), old_anchor_prefix).unwrap();
        } else {
            fs::write(&path, old_ledger_prefix).unwrap();
        }
        assert_eq!(
            AuthorityLedger::open_existing(&path, ledger_identity)
                .unwrap_err()
                .code(),
            if rollback_anchor {
                "ledger_anchor_rollback_detected"
            } else {
                "ledger_rollback_detected"
            }
        );
        remove(&path);
    }
}

#[test]
fn prepared_and_optional_armed_receipts_persist_exactly() {
    let path = temp_path("recovery-receipts");
    let ticket = digest(0x61);
    let run_binding = digest(0x62);
    let prepared = receipt(0xc1, PAYLOAD_SIZE * 2 + 7);
    let armed = receipt(0xc2, PAYLOAD_SIZE * 3 + 5);
    {
        let mut ledger = AuthorityLedger::provision_new(&path, identity(0x31, 0x41)).unwrap();
        ledger
            .issue_with_binding_and_receipt(&ticket, &run_binding, &prepared)
            .unwrap();
        assert_eq!(
            ledger.active_tickets().unwrap()[0].prepared_receipt(),
            prepared.as_slice()
        );
        assert_eq!(ledger.active_tickets().unwrap()[0].armed_receipt(), None);
        ledger.consume(&ticket).unwrap();
        ledger
            .record_armed_receipt(&ticket, &run_binding, &armed)
            .unwrap();
    }
    let ledger = AuthorityLedger::open_existing(&path, identity(0x31, 0x41)).unwrap();
    let active = ledger.active_tickets().unwrap();
    assert_eq!(active.len(), 1);
    assert_eq!(active[0].ticket_digest(), ticket);
    assert_eq!(active[0].run_binding_digest(), run_binding);
    assert_eq!(active[0].prepared_receipt(), prepared);
    assert_eq!(active[0].armed_receipt(), Some(armed.as_slice()));
    remove(&path);
}

#[test]
fn canonical_policy_snapshot_is_persisted_and_bundle_bound() {
    let path = temp_path("canonical-policy-snapshot");
    let ticket = digest(0x7b);
    let binding = digest(0x7c);
    let prepared = receipt(0xa7, 67);
    let snapshot = receipt(0xa8, PAYLOAD_SIZE * 4 + 11);
    let expected_bundle =
        compute_recovery_bundle_digest(&ticket, &binding, &prepared, &snapshot).unwrap();
    {
        let mut ledger = AuthorityLedger::provision_new(&path, identity(0x3c, 0x4c)).unwrap();
        ledger
            .issue_with_binding_and_recovery(&ticket, &binding, &prepared, &snapshot)
            .unwrap();
        let active = ledger.active_tickets().unwrap();
        assert_eq!(active[0].prepared_receipt(), prepared);
        assert_eq!(active[0].canonical_policy_snapshot(), snapshot);
        assert_eq!(active[0].recovery_bundle_digest(), expected_bundle);
        ledger.consume(&ticket).unwrap();
    }
    let ledger = AuthorityLedger::open_existing(&path, identity(0x3c, 0x4c)).unwrap();
    let active = ledger.active_tickets().unwrap();
    assert_eq!(active[0].canonical_policy_snapshot(), snapshot);
    assert_eq!(active[0].recovery_bundle_digest(), expected_bundle);
    remove(&path);
}

#[test]
fn missing_or_uncommitted_policy_bundle_fails_globally() {
    let path = temp_path("missing-policy-bundle");
    let ledger_identity = identity(0x3d, 0x4d);
    let ticket = decode_digest(&digest(0x7d), "test").unwrap();
    let binding = decode_digest(&digest(0x7e), "test").unwrap();
    {
        let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
        ledger
            .append_frame_raw(Event::Issued, ticket, binding, &[])
            .unwrap();
        ledger
            .append_blob(
                Event::PreparedReceiptChunk,
                Event::PreparedReceiptCommit,
                ticket,
                &receipt(0xa9, 42),
            )
            .unwrap();
    }
    let ledger = AuthorityLedger::open_existing(&path, ledger_identity).unwrap();
    assert_eq!(
        ledger.active_tickets().unwrap_err().code(),
        "ledger_policy_snapshot_required"
    );
    remove(&path);

    let uncommitted_path = temp_path("uncommitted-policy-snapshot");
    let ledger_identity = identity(0x3e, 0x4e);
    {
        let mut ledger =
            AuthorityLedger::provision_new(&uncommitted_path, ledger_identity.clone()).unwrap();
        ledger
            .append_frame_raw(Event::Issued, ticket, binding, &[])
            .unwrap();
        ledger
            .append_blob(
                Event::PreparedReceiptChunk,
                Event::PreparedReceiptCommit,
                ticket,
                &receipt(0xaa, 44),
            )
            .unwrap();
        ledger
            .append_frame_raw(
                Event::PolicySnapshotChunk,
                ticket,
                ZERO_DIGEST,
                b"partial-policy",
            )
            .unwrap();
    }
    let ledger = AuthorityLedger::open_existing(&uncommitted_path, ledger_identity).unwrap();
    assert_eq!(
        ledger.active_tickets().unwrap_err().code(),
        "ledger_policy_snapshot_required"
    );
    remove(&uncommitted_path);

    let missing_commit_path = temp_path("missing-bundle-commit");
    let ledger_identity = identity(0x3f, 0x4f);
    {
        let mut ledger =
            AuthorityLedger::provision_new(&missing_commit_path, ledger_identity.clone()).unwrap();
        ledger
            .append_frame_raw(Event::Issued, ticket, binding, &[])
            .unwrap();
        ledger
            .append_blob(
                Event::PreparedReceiptChunk,
                Event::PreparedReceiptCommit,
                ticket,
                &receipt(0xab, 46),
            )
            .unwrap();
        ledger
            .append_blob(
                Event::PolicySnapshotChunk,
                Event::PolicySnapshotCommit,
                ticket,
                &receipt(0xac, 74),
            )
            .unwrap();
    }
    let ledger = AuthorityLedger::open_existing(&missing_commit_path, ledger_identity).unwrap();
    assert_eq!(
        ledger.active_tickets().unwrap_err().code(),
        "ledger_recovery_bundle_required"
    );
    remove(&missing_commit_path);

    let wrong_bundle_path = temp_path("wrong-bundle-commit");
    let ledger_identity = identity(0x40, 0x50);
    {
        let mut ledger =
            AuthorityLedger::provision_new(&wrong_bundle_path, ledger_identity.clone()).unwrap();
        ledger
            .append_frame_raw(Event::Issued, ticket, binding, &[])
            .unwrap();
        ledger
            .append_blob(
                Event::PreparedReceiptChunk,
                Event::PreparedReceiptCommit,
                ticket,
                &receipt(0xad, 47),
            )
            .unwrap();
        ledger
            .append_blob(
                Event::PolicySnapshotChunk,
                Event::PolicySnapshotCommit,
                ticket,
                &receipt(0xae, 75),
            )
            .unwrap();
        ledger
            .append_frame_raw(Event::RecoveryBundleCommit, ticket, [0xff; 32], &[])
            .unwrap();
    }
    assert_eq!(
        AuthorityLedger::open_existing(&wrong_bundle_path, ledger_identity)
            .unwrap_err()
            .code(),
        "ledger_recovery_bundle_invalid"
    );
    remove(&wrong_bundle_path);
}

#[test]
fn recovery_receipts_are_bounded_binding_exact_and_single_commit() {
    let path = temp_path("receipt-contract");
    let ticket = digest(0x6e);
    let binding = digest(0x6f);
    let mut ledger = AuthorityLedger::provision_new(&path, identity(0x38, 0x48)).unwrap();
    assert_eq!(
        ledger
            .issue_with_binding_and_receipt(&ticket, &binding, b"")
            .unwrap_err()
            .code(),
        "recovery_receipt_invalid"
    );
    assert_eq!(
        ledger
            .issue_with_binding_and_receipt(
                &ticket,
                &binding,
                &vec![0; MAX_RECOVERY_RECEIPT_SIZE + 1],
            )
            .unwrap_err()
            .code(),
        "recovery_receipt_too_large"
    );
    ledger
        .issue_with_binding_and_receipt(&ticket, &binding, &receipt(0xf1, 37))
        .unwrap();
    ledger.consume(&ticket).unwrap();
    assert_eq!(
        ledger
            .record_armed_receipt(&ticket, &digest(0x70), &receipt(0xf2, 39))
            .unwrap_err()
            .code(),
        "ticket_run_binding_mismatch"
    );
    ledger
        .record_armed_receipt(&ticket, &binding, &receipt(0xf2, 39))
        .unwrap();
    assert_eq!(
        ledger
            .record_armed_receipt(&ticket, &binding, &receipt(0xf3, 39))
            .unwrap_err()
            .code(),
        "armed_receipt_duplicate"
    );
    drop(ledger);
    remove(&path);
}

#[test]
fn uncommitted_armed_receipt_blocks_all_active_recovery() {
    let path = temp_path("uncommitted-armed");
    let ledger_identity = identity(0x39, 0x49);
    let ticket = digest(0x71);
    {
        let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
        ledger
            .issue_with_binding_and_receipt(&ticket, &digest(0x72), &receipt(0xf4, 43))
            .unwrap();
        ledger.consume(&ticket).unwrap();
        ledger
            .append_frame_raw(
                Event::ArmedReceiptChunk,
                decode_digest(&ticket, "test").unwrap(),
                ZERO_DIGEST,
                b"partial-armed",
            )
            .unwrap();
    }
    let ledger = AuthorityLedger::open_existing(&path, ledger_identity).unwrap();
    assert_eq!(
        ledger.active_tickets().unwrap_err().code(),
        "ledger_recovery_receipt_required"
    );
    remove(&path);
}

#[test]
fn consume_and_recovery_fail_globally_without_a_committed_prepared_receipt() {
    let path = temp_path("missing-prepared");
    let ledger_identity = identity(0x32, 0x42);
    let ticket = decode_digest(&digest(0x63), "test").unwrap();
    let binding = decode_digest(&digest(0x64), "test").unwrap();
    {
        let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
        ledger
            .append_frame_raw(Event::Issued, ticket, binding, &[])
            .unwrap();
    }
    let mut ledger = AuthorityLedger::open_existing(&path, ledger_identity).unwrap();
    assert_eq!(
        ledger.consume(&hex_encode(&ticket)).unwrap_err().code(),
        "ledger_prepared_receipt_required"
    );
    assert_eq!(
        ledger.active_tickets().unwrap_err().code(),
        "ledger_prepared_receipt_required"
    );
    remove(&path);
}

#[test]
fn uncommitted_and_torn_recovery_receipts_fail_closed() {
    let uncommitted_path = temp_path("uncommitted-prepared");
    let ledger_identity = identity(0x33, 0x43);
    let ticket = decode_digest(&digest(0x65), "test").unwrap();
    let binding = decode_digest(&digest(0x66), "test").unwrap();
    {
        let mut ledger =
            AuthorityLedger::provision_new(&uncommitted_path, ledger_identity.clone()).unwrap();
        ledger
            .append_frame_raw(Event::Issued, ticket, binding, &[])
            .unwrap();
        ledger
            .append_frame_raw(Event::PreparedReceiptChunk, ticket, ZERO_DIGEST, b"partial")
            .unwrap();
    }
    let ledger = AuthorityLedger::open_existing(&uncommitted_path, ledger_identity).unwrap();
    assert_eq!(
        ledger.active_tickets().unwrap_err().code(),
        "ledger_recovery_receipt_required"
    );
    remove(&uncommitted_path);

    let torn_path = temp_path("torn-prepared");
    {
        let mut ledger = AuthorityLedger::provision_new(&torn_path, identity(0x34, 0x44)).unwrap();
        ledger
            .issue_with_binding_and_receipt(&digest(0x67), &digest(0x68), &receipt(0xd1, 48))
            .unwrap();
    }
    let mut file = OpenOptions::new().append(true).open(&torn_path).unwrap();
    file.write_all(b"partial-frame").unwrap();
    file.sync_all().unwrap();
    drop(file);
    assert_eq!(
        AuthorityLedger::open_existing(&torn_path, identity(0x34, 0x44))
            .unwrap_err()
            .code(),
        "ledger_torn_tail"
    );
    remove(&torn_path);
}

#[test]
fn protected_anchor_rejects_complete_frame_truncation_and_issue_before_rollback() {
    let path = temp_path("ledger-rollback");
    let ledger_identity = identity(0x35, 0x45);
    let old_bytes;
    {
        let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
        old_bytes = ledger_bytes(&mut ledger);
        ledger
            .issue_with_binding_and_receipt(&digest(0x69), &digest(0x6a), &receipt(0xe1, 41))
            .unwrap();
    }
    fs::write(&path, &old_bytes).unwrap();
    assert_eq!(
        AuthorityLedger::open_existing(&path, ledger_identity)
            .unwrap_err()
            .code(),
        "ledger_rollback_detected"
    );
    remove(&path);
}

#[test]
fn protected_anchor_rejects_anchor_rollback() {
    let path = temp_path("anchor-rollback");
    let ledger_identity = identity(0x36, 0x46);
    let old_anchor;
    {
        let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
        old_anchor = anchor_bytes(&mut ledger);
        ledger
            .issue_with_binding_and_receipt(&digest(0x6b), &digest(0x6c), &receipt(0xe2, 45))
            .unwrap();
    }

    fs::write(anchor_path(&path), &old_anchor).unwrap();
    assert_eq!(
        AuthorityLedger::open_existing(&path, ledger_identity.clone())
            .unwrap_err()
            .code(),
        "ledger_anchor_rollback_detected"
    );
    remove(&path);
}

#[test]
fn anchor_intent_recovers_crashes_before_and_after_the_ledger_append() {
    for ledger_bytes_written in [0, FRAME_SIZE / 2, FRAME_SIZE] {
        let path = temp_path(match ledger_bytes_written {
            0 => "intent-before-ledger",
            FRAME_SIZE => "intent-after-ledger",
            _ => "intent-during-ledger",
        });
        let ledger_identity = identity(0x3a, 0x4a);
        let ticket = digest(0x73 + (ledger_bytes_written / (FRAME_SIZE / 2)) as u8);
        let binding = digest(0x76 + (ledger_bytes_written / (FRAME_SIZE / 2)) as u8);
        {
            let mut ledger =
                AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
            ledger
                .issue_with_binding_and_receipt(&ticket, &binding, &receipt(0xf5, 35))
                .unwrap();
            ledger.consume(&ticket).unwrap();
            let frame = encode_frame(
                Event::Burned,
                ledger.next_sequence,
                &ledger.identity,
                decode_digest(&ticket, "test").unwrap(),
                decode_digest(&binding, "test").unwrap(),
                ledger.previous_hash,
                &[TicketBurnReason::Failed as u8],
            )
            .unwrap();
            let intent = encode_anchor_record(
                AnchorRecordKind::Intent,
                &ledger.identity,
                ledger.anchor_previous_hash,
                &frame,
                ledger.terminal_anchor,
            )
            .unwrap();
            ledger.anchor_file.write_all(&intent).unwrap();
            ledger.anchor_file.sync_all().unwrap();
            if ledger_bytes_written != 0 {
                ledger
                    .file
                    .write_all(&frame[..ledger_bytes_written])
                    .unwrap();
                ledger.file.sync_all().unwrap();
            }
        }
        let ledger = AuthorityLedger::open_existing(&path, ledger_identity).unwrap();
        assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Burned));
        assert_eq!(
            ledger.burn_reason(&ticket).unwrap(),
            Some(TicketBurnReason::Failed)
        );
        assert_eq!(
            ledger.next_sequence as usize * 2 * ANCHOR_RECORD_SIZE,
            ledger.anchor_file.metadata().unwrap().len() as usize
        );
        drop(ledger);
        remove(&path);
    }
}

#[test]
fn anchor_recovers_a_verified_partial_commit_after_the_ledger_append() {
    let path = temp_path("partial-anchor-commit");
    let ledger_identity = identity(0x3b, 0x4b);
    let ticket = digest(0x79);
    let binding = digest(0x7a);
    {
        let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
        ledger
            .issue_with_binding_and_receipt(&ticket, &binding, &receipt(0xf6, 36))
            .unwrap();
        ledger.consume(&ticket).unwrap();
        let frame = encode_frame(
            Event::Burned,
            ledger.next_sequence,
            &ledger.identity,
            decode_digest(&ticket, "test").unwrap(),
            decode_digest(&binding, "test").unwrap(),
            ledger.previous_hash,
            &[TicketBurnReason::TimedOut as u8],
        )
        .unwrap();
        let intent = encode_anchor_record(
            AnchorRecordKind::Intent,
            &ledger.identity,
            ledger.anchor_previous_hash,
            &frame,
            ledger.terminal_anchor,
        )
        .unwrap();
        ledger.anchor_file.write_all(&intent).unwrap();
        ledger.anchor_file.sync_all().unwrap();
        ledger.file.write_all(&frame).unwrap();
        ledger.file.sync_all().unwrap();
        let commit = encode_anchor_record(
            AnchorRecordKind::Commit,
            &ledger.identity,
            intent[ANCHOR_HASH_OFFSET..].try_into().unwrap(),
            &frame,
            terminal_after_frame(ledger.terminal_anchor, &frame).unwrap(),
        )
        .unwrap();
        ledger
            .anchor_file
            .write_all(&commit[..ANCHOR_RECORD_SIZE / 2])
            .unwrap();
        ledger.anchor_file.sync_all().unwrap();
    }
    let ledger = AuthorityLedger::open_existing(&path, ledger_identity).unwrap();
    assert_eq!(ledger.state(&ticket).unwrap(), Some(TicketState::Burned));
    assert_eq!(
        ledger.burn_reason(&ticket).unwrap(),
        Some(TicketBurnReason::TimedOut)
    );
    drop(ledger);
    remove(&path);
}

#[cfg(unix)]
#[test]
fn held_file_identity_rejects_path_replacement() {
    let path = temp_path("stable-file-identity");
    let moved = path.with_extension("moved");
    let mut ledger = AuthorityLedger::provision_new(&path, identity(0x37, 0x47)).unwrap();
    fs::rename(&path, &moved).unwrap();
    fs::write(&path, b"replacement").unwrap();
    assert_eq!(
        ledger.issue(&digest(0x6d)).unwrap_err().code(),
        "ledger_file_identity_changed"
    );
    let _ = fs::remove_file(moved);
    remove(&path);
}

#[test]
fn explicit_burn_reason_persists_exactly() {
    let path = temp_path("burn-reason");
    let ticket = digest(0x45);
    {
        let mut ledger = AuthorityLedger::provision_new(&path, identity(0x15, 0x25)).unwrap();
        ledger.issue(&ticket).unwrap();
        ledger.consume(&ticket).unwrap();
        ledger
            .burn_with_reason(&ticket, TicketBurnReason::TimedOut)
            .unwrap();
        assert_eq!(
            ledger.burn_reason(&ticket).unwrap(),
            Some(TicketBurnReason::TimedOut)
        );
    }
    let ledger = AuthorityLedger::open_existing(&path, identity(0x15, 0x25)).unwrap();
    assert_eq!(
        ledger.burn_reason(&ticket).unwrap(),
        Some(TicketBurnReason::TimedOut)
    );
    remove(&path);
}

#[test]
fn incomplete_result_is_blocked_until_explicit_startup_burn() {
    let path = temp_path("partial-result");
    let ledger_identity = identity(0x14, 0x24);
    let ticket = digest(0x37);
    let other = digest(0x38);
    let (sequence, previous_hash) = {
        let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
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
    let active = ledger.active_tickets().unwrap();
    assert_eq!(active.len(), 2);
    assert_eq!(active[0].ticket_digest(), ticket);
    for entry in active {
        ledger
            .burn_recovered(entry.ticket_digest(), entry.run_binding_digest())
            .unwrap();
    }
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
            AuthorityLedger::provision_new(&transition_path, transition_identity.clone()).unwrap();
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
        let mut ledger = AuthorityLedger::provision_new(&path, ledger_identity.clone()).unwrap();
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
