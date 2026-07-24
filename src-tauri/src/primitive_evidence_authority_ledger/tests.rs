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
    let _ = fs::remove_file(anchor_path(path));
}

fn receipt(byte: u8, length: usize) -> Vec<u8> {
    vec![byte; length]
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
