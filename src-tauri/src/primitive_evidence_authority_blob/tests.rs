use super::*;

use std::{
    collections::BTreeSet,
    fs::{self, OpenOptions},
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};

#[cfg(windows)]
use std::os::windows::{fs as windows_fs, fs::OpenOptionsExt, io::AsRawHandle};
#[cfg(windows)]
use windows_sys::Win32::{
    Security::{
        Authorization::{SetSecurityInfo, SE_FILE_OBJECT},
        DACL_SECURITY_INFORMATION,
    },
    Storage::FileSystem::{
        FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE, READ_CONTROL, WRITE_DAC,
    },
};

fn digest(value: u8) -> BlobDigest {
    [value; 32]
}

fn unique_root(label: &str) -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system time")
        .as_nanos();
    std::env::temp_dir().join(format!(
        "vrcforge-protected-blob-{label}-{}-{nonce}",
        std::process::id()
    ))
}

fn context(kind: ProtectedBlobKind, ticket: u8, run: u8) -> ProtectedBlobBindingContext {
    ProtectedBlobBindingContext::new(
        kind,
        digest(ticket),
        digest(run),
        digest(41),
        digest(42),
        digest(43),
    )
    .expect("valid context")
}

#[cfg(windows)]
#[test]
fn production_blob_create_keeps_explicit_file_security_and_narrow_access() {
    const ACCESS_SYSTEM_SECURITY: u32 = 0x0100_0000;
    assert_eq!(BLOB_FILE_CREATE_ACCESS, RUNTIME_BLOB_FILE_AUTHORITY_ACCESS);
    assert_eq!(BLOB_FILE_READ_ACCESS, RUNTIME_BLOB_FILE_READ_ACCESS);
    assert_eq!(BLOB_FILE_CLEANUP_ACCESS, RUNTIME_BLOB_FILE_CLEANUP_ACCESS);
    for access in [
        BLOB_FILE_CREATE_ACCESS,
        BLOB_FILE_READ_ACCESS,
        BLOB_FILE_CLEANUP_ACCESS,
    ] {
        assert_eq!(access & ACCESS_SYSTEM_SECURITY, 0);
    }
    let descriptor = LocalSecurityDescriptor::from_sddl(RUNTIME_BLOB_FILE_SDDL)
        .expect("fixed runtime blob file descriptor must parse");
    assert!(!descriptor.0.is_null());

    let source = include_str!("../primitive_evidence_authority_blob.rs");
    assert!(source.contains("if disposition == FILE_CREATE && apply_production_security"));
    assert!(source.contains("Some(LocalSecurityDescriptor::from_sddl(RUNTIME_BLOB_FILE_SDDL)?)"));
    let compact = source
        .chars()
        .filter(|character| !character.is_whitespace())
        .collect::<String>();
    assert!(compact.contains("#[cfg(not(test))]{true}"));
}

#[test]
fn immutable_blob_materializes_and_reopens_by_binding() {
    let root = unique_root("roundtrip");
    let (mut authority, descriptor) =
        ProtectedBlobAuthority::provision_unsecured_test(root.clone(), digest(1), digest(2))
            .expect("provision namespace");
    let value = vec![0x5a; 192 * 1024 + 17];
    let binding = context(ProtectedBlobKind::VerifiedResult, 3, 4);

    let created = authority
        .materialize(binding, &value)
        .expect("materialize blob");
    assert_eq!(created.content(), value);
    assert_eq!(authority.metrics().create_count, 1);
    assert_eq!(authority.metrics().blob_flush_count, 1);
    assert_eq!(authority.metrics().directory_flush_count, 1);
    let reference = created.reference().clone();
    assert_eq!(reference.context.kind, ProtectedBlobKind::VerifiedResult);
    assert_eq!(reference.context.ticket_digest, digest(3));
    assert_eq!(reference.context.run_binding_digest, digest(4));
    assert_eq!(reference.context.prepared_source_digest, digest(41));
    assert_eq!(reference.context.policy_snapshot_digest, digest(42));
    assert_eq!(reference.context.recovery_bundle_digest, digest(43));
    assert_eq!(reference.content_length, value.len() as u64);
    assert_ne!(reference.object_identity_digest, [0; 32]);
    assert_ne!(reference.object_security_digest, [0; 32]);
    authority
        .verify_reference(&reference)
        .expect("held reference remains immutable");
    drop(authority);

    let mut reopened = ProtectedBlobAuthority::reopen_unsecured_test(root.clone(), descriptor)
        .expect("reopen namespace");
    let readback = reopened
        .reopen_bound(
            binding,
            *reference.content_digest(),
            *reference.binding_digest(),
        )
        .expect("reopen bound blob");
    assert_eq!(readback.reference(), &reference);
    assert_eq!(readback.content(), value);

    drop(reopened);
    fs::remove_dir_all(&root).expect("remove isolated test namespace");
}

#[test]
fn content_address_separates_ticket_run_and_kind_and_never_reuses() {
    let root = unique_root("dimensions");
    let (mut authority, _) =
        ProtectedBlobAuthority::provision_unsecured_test(root.clone(), digest(5), digest(6))
            .expect("provision namespace");
    let value = b"same immutable evidence";
    let first = authority
        .materialize(context(ProtectedBlobKind::VerifiedResult, 7, 8), value)
        .expect("first blob");
    let first_reference = first.reference().clone();
    assert_eq!(
        authority
            .materialize(context(ProtectedBlobKind::VerifiedResult, 7, 8), value)
            .unwrap_err()
            .code(),
        "protected_blob_create_new_collision"
    );
    let other_ticket = authority
        .materialize(context(ProtectedBlobKind::VerifiedResult, 9, 8), value)
        .expect("other ticket");
    let other_run = authority
        .materialize(context(ProtectedBlobKind::VerifiedResult, 7, 10), value)
        .expect("other run");
    let other_kind = authority
        .materialize(context(ProtectedBlobKind::ResultCommit, 7, 8), value)
        .expect("other kind");
    let names = [
        first_reference.relative_name(),
        other_ticket.reference().relative_name(),
        other_run.reference().relative_name(),
        other_kind.reference().relative_name(),
    ]
    .into_iter()
    .collect::<BTreeSet<_>>();
    assert_eq!(names.len(), 4);
    for wrong in [
        context(ProtectedBlobKind::VerifiedResult, 9, 8),
        context(ProtectedBlobKind::VerifiedResult, 7, 10),
        context(ProtectedBlobKind::ResultCommit, 7, 8),
    ] {
        assert!(authority
            .reopen_bound(
                wrong,
                *first_reference.content_digest(),
                *first_reference.binding_digest(),
            )
            .is_err());
    }
    drop(authority);
    fs::remove_dir_all(&root).expect("remove isolated test namespace");
}

#[test]
fn maximum_projection_uses_constant_flushes_and_bounded_large_writes() {
    let root = unique_root("io-budget");
    let (mut authority, _) =
        ProtectedBlobAuthority::provision_unsecured_test(root.clone(), digest(11), digest(12))
            .expect("provision namespace");
    let value = vec![0xa5; ProtectedBlobKind::Projection.maximum_content_size()];
    authority
        .materialize(context(ProtectedBlobKind::Projection, 13, 14), &value)
        .expect("maximum projection");
    let metrics = authority.metrics();
    assert_eq!(metrics.create_count, 1);
    assert_eq!(metrics.blob_flush_count, 1);
    assert_eq!(metrics.directory_flush_count, 1);
    assert_eq!(
        metrics.bytes_written,
        (PROTECTED_BLOB_HEADER_SIZE + value.len()) as u64
    );
    assert!(metrics.write_call_count <= 162);
    assert!(metrics.write_call_count * 32 < metrics.bytes_written / 100);
    drop(authority);
    fs::remove_dir_all(&root).expect("remove isolated test namespace");
}

#[test]
fn every_prebind_crash_boundary_leaves_one_typed_safe_orphan() {
    let faults = [
        ProtectedBlobTestFault::AfterCreateBeforeValidation,
        ProtectedBlobTestFault::BeforeFirstWrite,
        ProtectedBlobTestFault::AfterBytes(1),
        ProtectedBlobTestFault::AfterBytes(PROTECTED_BLOB_HEADER_SIZE + 64 * 1024),
        ProtectedBlobTestFault::AfterBytes(PROTECTED_BLOB_HEADER_SIZE + 128 * 1024 + 3),
        ProtectedBlobTestFault::BeforeFlush,
        ProtectedBlobTestFault::AfterFlush,
        ProtectedBlobTestFault::BeforeDirectoryFlush,
        ProtectedBlobTestFault::AfterDirectoryFlush,
    ];
    for (index, fault) in faults.into_iter().enumerate() {
        let root = unique_root(&format!("orphan-{index}"));
        let (mut authority, _) =
            ProtectedBlobAuthority::provision_unsecured_test(root.clone(), digest(15), digest(16))
                .expect("provision namespace");
        authority.set_test_fault(fault);
        let value = vec![index as u8; 128 * 1024 + 3];
        assert!(authority
            .materialize(context(ProtectedBlobKind::VerifiedResult, 17, 18), &value,)
            .is_err());
        assert_eq!(authority.namespace_usage().0, 1, "fault {fault:?}");
        let receipts = authority
            .reconcile_unreferenced(&BTreeSet::new())
            .expect("contain deterministic orphan");
        assert_eq!(receipts.len(), 1);
        assert_eq!(
            receipts[0].relative_name(),
            authority.cleanup_receipts()[0].relative_name()
        );
        assert_ne!(receipts[0].receipt_digest(), &[0; 32]);
        assert_eq!(authority.namespace_usage(), (0, 0));
        assert_eq!(authority.metrics().cleanup_count, 1);
        assert!(authority.metrics().directory_flush_count >= 1);
        assert!(enumerate_relative_names(&authority.root)
            .expect("held root enumeration")
            .is_empty());
        drop(authority);
        fs::remove_dir_all(&root).expect("remove isolated test namespace");
    }
}

#[test]
fn cleanup_receipts_are_withheld_until_the_held_root_flush_completes() {
    for (index, fault, expected_flushes, expected_code) in [
        (
            0usize,
            ProtectedBlobTestFault::AfterCleanupDisposition,
            0u64,
            "protected_blob_test_cleanup_disposition_fault",
        ),
        (
            1usize,
            ProtectedBlobTestFault::BeforeCleanupDirectoryFlush,
            0u64,
            "protected_blob_test_cleanup_directory_flush_fault",
        ),
        (
            2usize,
            ProtectedBlobTestFault::AfterCleanupDirectoryFlush,
            1u64,
            "protected_blob_test_cleanup_directory_flush_fault",
        ),
    ] {
        let root = unique_root(&format!("cleanup-flush-{index}"));
        let (mut authority, _) =
            ProtectedBlobAuthority::provision_unsecured_test(root.clone(), digest(61), digest(62))
                .expect("provision namespace");
        authority.set_test_fault(ProtectedBlobTestFault::BeforeFirstWrite);
        authority
            .materialize(
                context(ProtectedBlobKind::VerifiedResult, 63, 64),
                b"uncommitted",
            )
            .expect_err("leave one deterministic orphan");
        assert_eq!(authority.namespace_usage().0, 1);
        authority.set_test_fault(fault);
        assert_eq!(
            authority
                .reconcile_unreferenced(&BTreeSet::new())
                .expect_err("cleanup flush boundary must fail closed")
                .code(),
            expected_code
        );
        assert_eq!(authority.namespace_usage(), (0, 0));
        assert!(authority.cleanup_receipts().is_empty());
        assert_eq!(authority.metrics().cleanup_count, 0);
        assert_eq!(authority.metrics().directory_flush_count, expected_flushes);
        assert!(enumerate_relative_names(&authority.root)
            .expect("held root readback")
            .is_empty());
        authority
            .materialize(
                context(ProtectedBlobKind::Projection, 65 + index as u8, 66),
                b"same-authority-reuse",
            )
            .expect("refreshed authority remains reusable");
        assert_eq!(authority.namespace_usage().0, 1);
        drop(authority);
        fs::remove_dir_all(&root).expect("remove isolated test namespace");
    }
}

#[test]
fn multi_object_cleanup_failure_refreshes_usage_before_same_authority_reuse() {
    let root = unique_root("cleanup-mid-delete");
    let (mut authority, _) =
        ProtectedBlobAuthority::provision_unsecured_test(root.clone(), digest(67), digest(68))
            .expect("provision namespace");
    for (kind, ticket) in [
        (ProtectedBlobKind::VerifiedResult, 69),
        (ProtectedBlobKind::Projection, 70),
    ] {
        authority.set_test_fault(ProtectedBlobTestFault::BeforeFirstWrite);
        authority
            .materialize(context(kind, ticket, 71), b"uncommitted")
            .expect_err("leave deterministic orphan");
    }
    assert_eq!(authority.namespace_usage().0, 2);
    authority.set_test_fault(ProtectedBlobTestFault::AfterCleanupDeletes(1));
    assert_eq!(
        authority
            .reconcile_unreferenced(&BTreeSet::new())
            .expect_err("stop after the first cleanup deletion")
            .code(),
        "protected_blob_test_cleanup_mid_delete_fault"
    );
    assert_eq!(authority.namespace_usage().0, 1);
    assert!(authority.cleanup_receipts().is_empty());
    assert_eq!(authority.metrics().cleanup_count, 0);
    assert_eq!(
        enumerate_relative_names(&authority.root)
            .expect("held root enumeration")
            .len(),
        1
    );
    authority
        .materialize(
            context(ProtectedBlobKind::ResultCommit, 72, 73),
            b"same-authority-reuse",
        )
        .expect("refreshed authority remains reusable");
    assert_eq!(authority.namespace_usage().0, 2);
    drop(authority);
    fs::remove_dir_all(&root).expect("remove isolated test namespace");
}

#[test]
fn cleanup_refresh_failure_poison_closes_every_later_operation() {
    let root = unique_root("cleanup-refresh-poison");
    let (mut authority, _) =
        ProtectedBlobAuthority::provision_unsecured_test(root.clone(), digest(74), digest(75))
            .expect("provision namespace");
    authority.set_test_fault(ProtectedBlobTestFault::BeforeFirstWrite);
    authority
        .materialize(
            context(ProtectedBlobKind::VerifiedResult, 76, 77),
            b"uncommitted",
        )
        .expect_err("leave one valid deterministic orphan");
    fs::write(root.join("zz-unknown-entry"), b"hostile")
        .expect("create a later-sorted hostile namespace entry");
    assert_eq!(
        authority
            .reconcile_unreferenced(&BTreeSet::new())
            .expect_err("refresh cannot authenticate the remaining namespace")
            .code(),
        "protected_blob_authority_poisoned"
    );
    assert_eq!(
        authority.verify_namespace().unwrap_err().code(),
        "protected_blob_authority_poisoned"
    );
    assert_eq!(
        authority
            .materialize(
                context(ProtectedBlobKind::Projection, 78, 79),
                b"must-not-reuse",
            )
            .unwrap_err()
            .code(),
        "protected_blob_authority_poisoned"
    );
    drop(authority);
    fs::remove_dir_all(&root).expect("remove isolated test namespace");
}

#[test]
fn namespace_enumeration_and_aggregate_storage_fail_before_unbounded_growth() {
    let enumeration_root = unique_root("enumeration-cap");
    fs::create_dir(&enumeration_root).expect("create enumeration root");
    for name in ["one", "two", "three"] {
        fs::write(enumeration_root.join(name), b"x").expect("create hostile entry");
    }
    let held = open_root(&enumeration_root).expect("hold enumeration root");
    assert_eq!(
        enumerate_relative_names_with_limit(&held, 2)
            .expect_err("third entry must exceed the bounded enumeration")
            .code(),
        "protected_blob_namespace_limit_exceeded"
    );
    drop(held);
    assert_eq!(
        fs::read_dir(&enumeration_root)
            .expect("readback hostile entries")
            .count(),
        3
    );
    fs::remove_dir_all(&enumeration_root).expect("remove enumeration root");

    let storage_root = unique_root("aggregate-storage-cap");
    let (mut authority, descriptor) = ProtectedBlobAuthority::provision_unsecured_test(
        storage_root.clone(),
        digest(65),
        digest(66),
    )
    .expect("provision namespace");
    let created = authority
        .materialize(
            context(ProtectedBlobKind::Projection, 67, 68),
            b"bounded persistent object",
        )
        .expect("materialize one object");
    let object_length = created.reference().object_length();
    drop(authority);
    let held = open_root(&storage_root).expect("hold storage root");
    assert_eq!(
        scan_namespace_usage_with_limits(&held, &descriptor, 1, object_length - 1)
            .expect_err("aggregate stored bytes must be checked during initial scan")
            .code(),
        "protected_blob_namespace_limit_exceeded"
    );
    assert_eq!(
        scan_namespace_usage_with_limits(&held, &descriptor, 1, object_length)
            .expect("exact aggregate boundary must pass"),
        (1, object_length)
    );
    drop(held);
    fs::remove_dir_all(&storage_root).expect("remove storage root");
}

#[test]
fn unknown_or_address_mismatched_orphans_fail_closed_without_deletion() {
    let unknown_root = unique_root("unknown-orphan");
    let (authority, descriptor) = ProtectedBlobAuthority::provision_unsecured_test(
        unknown_root.clone(),
        digest(19),
        digest(20),
    )
    .expect("provision namespace");
    drop(authority);
    let unknown = unknown_root.join("not-authority-owned.bin");
    fs::write(&unknown, b"foreign").expect("write unknown object");
    assert_eq!(
        ProtectedBlobAuthority::reopen_unsecured_test(unknown_root.clone(), descriptor)
            .unwrap_err()
            .code(),
        "protected_blob_namespace_unknown"
    );
    assert!(unknown.exists());
    fs::remove_dir_all(&unknown_root).expect("remove isolated test namespace");

    let mismatch_root = unique_root("address-mismatch");
    let (mut authority, descriptor) = ProtectedBlobAuthority::provision_unsecured_test(
        mismatch_root.clone(),
        digest(21),
        digest(22),
    )
    .expect("provision namespace");
    authority.set_test_fault(ProtectedBlobTestFault::BeforeFirstWrite);
    authority
        .materialize(context(ProtectedBlobKind::ResultCommit, 23, 24), b"unbound")
        .unwrap_err();
    drop(authority);
    let original = fs::read_dir(&mismatch_root)
        .expect("list isolated root")
        .next()
        .expect("one orphan")
        .expect("orphan entry")
        .path();
    let mut name = original.file_name().unwrap().to_string_lossy().into_owned();
    let address_start = name.rfind("-a").expect("address field") + 2;
    let replacement = if &name[address_start..address_start + 1] == "a" {
        "b"
    } else {
        "a"
    };
    name.replace_range(address_start..address_start + 1, replacement);
    let renamed = mismatch_root.join(name);
    fs::rename(&original, &renamed).expect("rename orphan address");
    assert_eq!(
        ProtectedBlobAuthority::reopen_unsecured_test(mismatch_root.clone(), descriptor)
            .unwrap_err()
            .code(),
        "protected_blob_unreferenced_address_invalid"
    );
    assert!(renamed.exists());
    fs::remove_dir_all(&mismatch_root).expect("remove isolated test namespace");

    let alias_root = unique_root("kind-alias");
    let (mut authority, descriptor) = ProtectedBlobAuthority::provision_unsecured_test(
        alias_root.clone(),
        digest(23),
        digest(24),
    )
    .expect("provision namespace");
    authority.set_test_fault(ProtectedBlobTestFault::BeforeFirstWrite);
    authority
        .materialize(context(ProtectedBlobKind::VerifiedResult, 25, 26), b"alias")
        .unwrap_err();
    drop(authority);
    let canonical = fs::read_dir(&alias_root)
        .expect("list isolated root")
        .next()
        .expect("one orphan")
        .expect("orphan entry")
        .path();
    let canonical_name = canonical
        .file_name()
        .unwrap()
        .to_string_lossy()
        .into_owned();
    for alias_kind in ["k1", "k0001"] {
        let alias_path = alias_root.join(canonical_name.replacen("k01", alias_kind, 1));
        fs::rename(&canonical, &alias_path).expect("install noncanonical kind alias");
        assert_eq!(
            ProtectedBlobAuthority::reopen_unsecured_test(alias_root.clone(), descriptor.clone(),)
                .unwrap_err()
                .code(),
            "protected_blob_namespace_unknown"
        );
        assert!(alias_path.exists());
        fs::rename(&alias_path, &canonical).expect("restore canonical orphan");
    }
    fs::remove_dir_all(&alias_root).expect("remove isolated test namespace");
}

#[test]
fn replacement_and_hardlink_drift_are_rejected() {
    let replacement_root = unique_root("replacement");
    let (mut authority, descriptor) = ProtectedBlobAuthority::provision_unsecured_test(
        replacement_root.clone(),
        digest(25),
        digest(26),
    )
    .expect("provision namespace");
    let binding = context(ProtectedBlobKind::ResultCommit, 27, 28);
    let created = authority.materialize(binding, b"terminal").unwrap();
    let reference = created.reference().clone();
    let object_path = replacement_root.join(reference.relative_name());
    drop(authority);
    let object_bytes = fs::read(&object_path).expect("read object");
    fs::remove_file(&object_path).expect("remove original object");
    fs::write(&object_path, object_bytes).expect("replace same bytes");
    let mut reopened =
        ProtectedBlobAuthority::reopen_unsecured_test(replacement_root.clone(), descriptor)
            .expect("reopen namespace");
    assert!(reopened
        .reopen_bound(
            binding,
            *reference.content_digest(),
            *reference.binding_digest(),
        )
        .is_err());
    drop(reopened);
    fs::remove_dir_all(&replacement_root).expect("remove isolated test namespace");

    let hardlink_root = unique_root("hardlink");
    let (mut authority, descriptor) = ProtectedBlobAuthority::provision_unsecured_test(
        hardlink_root.clone(),
        digest(29),
        digest(30),
    )
    .expect("provision namespace");
    let binding = context(ProtectedBlobKind::VerifiedResult, 31, 32);
    let created = authority.materialize(binding, b"verified").unwrap();
    let reference = created.reference().clone();
    let object_path = hardlink_root.join(reference.relative_name());
    drop(authority);
    let outside_link = hardlink_root.with_extension("hostile-link");
    fs::hard_link(&object_path, &outside_link).expect("create hostile hardlink");
    assert_eq!(
        ProtectedBlobAuthority::reopen_unsecured_test(hardlink_root.clone(), descriptor)
            .unwrap_err()
            .code(),
        "protected_blob_identity_invalid"
    );
    fs::remove_file(&outside_link).expect("remove hostile hardlink");
    fs::remove_dir_all(&hardlink_root).expect("remove isolated test namespace");
}

#[cfg(windows)]
#[test]
fn acl_and_reparse_drift_are_rejected() {
    let acl_root = unique_root("acl");
    let (mut authority, descriptor) =
        ProtectedBlobAuthority::provision_unsecured_test(acl_root.clone(), digest(33), digest(34))
            .expect("provision namespace");
    let binding = context(ProtectedBlobKind::ResultCommit, 35, 36);
    let created = authority.materialize(binding, b"acl-bound").unwrap();
    let reference = created.reference().clone();
    let object_path = acl_root.join(reference.relative_name());
    drop(authority);
    let file = OpenOptions::new()
        .access_mode(READ_CONTROL | WRITE_DAC)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
        .open(&object_path)
        .expect("open security descriptor");
    let status = unsafe {
        SetSecurityInfo(
            file.as_raw_handle().cast(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null(),
            std::ptr::null(),
        )
    };
    assert_eq!(status, 0);
    drop(file);
    assert_eq!(
        ProtectedBlobAuthority::reopen_unsecured_test(acl_root.clone(), descriptor)
            .unwrap_err()
            .code(),
        "protected_blob_security_mismatch"
    );
    fs::remove_dir_all(&acl_root).expect("remove isolated test namespace");

    let reparse_root = unique_root("reparse");
    let (mut authority, descriptor) = ProtectedBlobAuthority::provision_unsecured_test(
        reparse_root.clone(),
        digest(37),
        digest(38),
    )
    .expect("provision namespace");
    let binding = context(ProtectedBlobKind::Projection, 39, 40);
    let created = authority.materialize(binding, b"projection").unwrap();
    let reference = created.reference().clone();
    let object_path = reparse_root.join(reference.relative_name());
    drop(authority);
    fs::remove_file(&object_path).expect("remove original object");
    let target = reparse_root.with_extension("symlink-target");
    fs::write(&target, b"target").expect("write symlink target");
    match windows_fs::symlink_file(&target, &object_path) {
        Ok(()) => {
            assert_eq!(
                ProtectedBlobAuthority::reopen_unsecured_test(reparse_root.clone(), descriptor)
                    .unwrap_err()
                    .code(),
                "protected_blob_identity_invalid"
            );
        }
        Err(error) if error.kind() == std::io::ErrorKind::PermissionDenied => {
            assert!(!windows_blob_identity_observation_valid(
                false,
                1,
                windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT,
                1,
                &[1; 16],
                1,
                1,
                false,
                false,
            ));
        }
        Err(error) => panic!("unexpected symlink failure: {error}"),
    }
    fs::remove_file(&target).expect("remove symlink target");
    fs::remove_dir_all(&reparse_root).expect("remove isolated test namespace");
}
