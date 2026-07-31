use super::windows::{
    admit_current_process_handles_with_revalidation_for_test, cancel_disconnect_result_for_test,
    claim_installed_launch_for_test, create_pipe_with_sddl,
    current_process_installed_policy_for_test, current_process_session_id,
    current_process_token_snapshot_for_test, duplicate_and_validate_scenario_handles_for_test,
    duplicate_scenario_handles_with_forced_failure, installed_launch_receipt_sha256_for_test,
    installed_runtime_broker_identity_for_test, open_test_client, process_is_active,
    reopen_scenario_file_object_for_test, scenario_granted_access_is_read_only_for_test,
    service_guard_binding_for_test, service_guard_is_inheritable_for_test, unique_test_pipe_name,
    validate_installed_controller_facts_for_test, validate_installed_runtime_broker_facts_for_test,
    ControllerLaunchRegistryHarness, InstalledControllerLaunchHarness,
    RuntimeBrokerAdmissionDropHarness, RuntimeBrokerRegistryHarness, SecurityDescriptor,
    TEST_PIPE_SDDL,
};
use super::*;
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    os::windows::{
        fs::{FileExt, OpenOptionsExt},
        io::AsRawHandle,
    },
    path::PathBuf,
    sync::{
        atomic::{AtomicU64, AtomicUsize, Ordering},
        mpsc, Arc, Barrier,
    },
    time::{Duration, Instant},
};
use windows_sys::Win32::Foundation::{ERROR_SHARING_VIOLATION, GENERIC_READ};
use windows_sys::Win32::Storage::FileSystem::{
    DELETE, FILE_APPEND_DATA, FILE_DELETE_CHILD, FILE_READ_ATTRIBUTES, FILE_READ_DATA,
    FILE_READ_EA, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE, FILE_WRITE_ATTRIBUTES,
    FILE_WRITE_DATA, FILE_WRITE_EA, READ_CONTROL, SYNCHRONIZE, WRITE_DAC, WRITE_OWNER,
};
use windows_sys::Win32::System::SystemServices::ACCESS_SYSTEM_SECURITY;

const GENERATION: [u8; 32] = [0x24; 32];
const CONTROLLER: [u8; 32] = [0x42; 32];
const LAUNCH_RECEIPT: [u8; 32] = [0x64; 32];
const INSTALLED_LAYOUT: [u8; 32] = [0x51; 32];
const FINAL_COMMIT_RECEIPT: [u8; 32] = [0x52; 32];
const CONTROLLER_SOURCE_BINDING: [u8; 32] = [0x53; 32];
const INSTALL_HELPER: [u8; 32] = [0x54; 32];
const INSTALL_HELPER_SOURCE_BINDING: [u8; 32] = [0x55; 32];
const SERVICE_PROCESS_ID: u32 = 900;
const SERVICE_STARTED_AT: u64 = 1_000;
const EXTERNAL_FIXED_INDICES: [usize; EXTERNAL_MODEL_PART_HANDLE_COUNT] = [1, 2, 3, 5, 6, 7];

#[test]
fn fixed_eight_role_order_has_one_source_and_exact_partition() {
    assert_eq!(
        FIXED_MODEL_PART_HANDLE_ROLES,
        [
            "driver",
            "desktop",
            "backend",
            "unity",
            "bridge_launcher",
            "bridge_listener",
            "fixture_contract",
            "fixture_baseline",
        ]
    );
    assert_eq!(FIXED_MODEL_PART_HANDLE_COUNT, 8);
    assert_eq!(
        EXTERNAL_MODEL_PART_HANDLE_ROLES,
        [
            "desktop",
            "backend",
            "unity",
            "bridge_listener",
            "fixture_contract",
            "fixture_baseline",
        ]
    );
    let parent_source = include_str!("../primitive_evidence_authority_pipe.rs");
    let handle_source = include_str!("handle_tokens.rs");
    assert_eq!(
        parent_source
            .matches("pub const FIXED_MODEL_PART_HANDLE_COUNT")
            .count(),
        0
    );
    assert_eq!(
        parent_source
            .matches("pub const FIXED_MODEL_PART_HANDLE_ROLES")
            .count(),
        0
    );
    assert_eq!(
        handle_source
            .matches("pub const FIXED_MODEL_PART_HANDLE_COUNT")
            .count(),
        1
    );
    assert_eq!(
        handle_source
            .matches("pub const FIXED_MODEL_PART_HANDLE_ROLES")
            .count(),
        1
    );
}

#[test]
fn external_source_and_service_guard_access_use_the_exact_read_only_allowlist() {
    let exact_read_only =
        FILE_READ_DATA | FILE_READ_EA | FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE;
    assert!(scenario_granted_access_is_read_only_for_test(
        exact_read_only
    ));
    assert!(scenario_granted_access_is_read_only_for_test(
        FILE_READ_DATA
    ));
    for excluded in [
        FILE_WRITE_DATA,
        FILE_APPEND_DATA,
        FILE_WRITE_EA,
        FILE_DELETE_CHILD,
        FILE_WRITE_ATTRIBUTES,
        DELETE,
        WRITE_DAC,
        WRITE_OWNER,
        ACCESS_SYSTEM_SECURITY,
        GENERIC_READ,
    ] {
        assert!(!scenario_granted_access_is_read_only_for_test(
            exact_read_only | excluded
        ));
    }
    assert!(!scenario_granted_access_is_read_only_for_test(
        FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE
    ));
}

fn fixed_handle_fixture(
    label: &str,
) -> (
    PathBuf,
    [PathBuf; FIXED_MODEL_PART_HANDLE_COUNT],
    [File; FIXED_MODEL_PART_HANDLE_COUNT],
    ExternalModelPartHandleTokens,
) {
    static SEQUENCE: AtomicU64 = AtomicU64::new(1);
    let root = std::env::temp_dir().join(format!(
        "vrcforge-authority-handle-{label}-{}-{}",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root).unwrap();
    let mut paths = Vec::with_capacity(FIXED_MODEL_PART_HANDLE_COUNT);
    let mut files = Vec::with_capacity(FIXED_MODEL_PART_HANDLE_COUNT);
    for index in 0..FIXED_MODEL_PART_HANDLE_COUNT {
        let path = root.join(format!("role-{index}.bin"));
        let mut writer = OpenOptions::new()
            .create_new(true)
            .write(true)
            .share_mode(0)
            .open(&path)
            .unwrap();
        writer.write_all(&[index as u8 + 1]).unwrap();
        writer.flush().unwrap();
        drop(writer);
        let file = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ)
            .open(&path)
            .unwrap();
        paths.push(path);
        files.push(file);
    }
    let paths: [PathBuf; FIXED_MODEL_PART_HANDLE_COUNT] = paths.try_into().unwrap();
    let files: [File; FIXED_MODEL_PART_HANDLE_COUNT] = files.try_into().ok().unwrap();
    let tokens = ExternalModelPartHandleTokens::try_from_values(std::array::from_fn(|index| {
        files[EXTERNAL_FIXED_INDICES[index]].as_raw_handle() as usize as u64
    }))
    .unwrap();
    (root, paths, files, tokens)
}

fn fixed_handle_fixture_with_guard_reopen_failure(
    label: &str,
    fail_external_index: usize,
) -> (
    PathBuf,
    [PathBuf; FIXED_MODEL_PART_HANDLE_COUNT],
    [File; FIXED_MODEL_PART_HANDLE_COUNT],
    ExternalModelPartHandleTokens,
) {
    assert!(fail_external_index < EXTERNAL_MODEL_PART_HANDLE_COUNT);
    static SEQUENCE: AtomicU64 = AtomicU64::new(1);
    let root = std::env::temp_dir().join(format!(
        "vrcforge-authority-guard-reopen-{label}-{}-{}",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root).unwrap();
    let mut paths = Vec::with_capacity(FIXED_MODEL_PART_HANDLE_COUNT);
    let mut files = Vec::with_capacity(FIXED_MODEL_PART_HANDLE_COUNT);
    let fail_fixed_index = EXTERNAL_FIXED_INDICES[fail_external_index];
    for index in 0..FIXED_MODEL_PART_HANDLE_COUNT {
        let path = root.join(format!("role-{index}.bin"));
        fs::write(&path, [index as u8 + 1]).unwrap();
        let file = OpenOptions::new()
            .read(true)
            .share_mode(if index == fail_fixed_index {
                0
            } else {
                FILE_SHARE_READ
            })
            .open(&path)
            .unwrap();
        paths.push(path);
        files.push(file);
    }
    let paths: [PathBuf; FIXED_MODEL_PART_HANDLE_COUNT] = paths.try_into().unwrap();
    let files: [File; FIXED_MODEL_PART_HANDLE_COUNT] = files.try_into().ok().unwrap();
    let tokens = ExternalModelPartHandleTokens::try_from_values(std::array::from_fn(|index| {
        files[EXTERNAL_FIXED_INDICES[index]].as_raw_handle() as usize as u64
    }))
    .unwrap();
    (root, paths, files, tokens)
}

fn compose_fixed_handle_fixture(
    source_files: &[File; FIXED_MODEL_PART_HANDLE_COUNT],
    external: ValidatedExternalScenarioHandleBundle,
) -> ActiveScenarioHandleBundle {
    external
        .compose_with_protected_roots([
            reopen_scenario_file_object_for_test(&source_files[0]).unwrap(),
            reopen_scenario_file_object_for_test(&source_files[4]).unwrap(),
        ])
        .unwrap()
}

fn remove_fixed_handle_fixture(root: &Path, paths: &[PathBuf; FIXED_MODEL_PART_HANDLE_COUNT]) {
    for path in paths {
        fs::remove_file(path).unwrap();
    }
    fs::remove_dir(root).unwrap();
}

fn set_handle_stream_position(file: &File, position: u64) {
    let mut borrowed = file;
    borrowed.seek(SeekFrom::Start(position)).unwrap();
}

fn handle_stream_position(file: &File) -> u64 {
    let mut borrowed = file;
    borrowed.stream_position().unwrap()
}

fn handle_stream_positions<const N: usize>(files: [&File; N]) -> [u64; N] {
    std::array::from_fn(|index| handle_stream_position(files[index]))
}

fn direct_handle_fixture(
    label: &str,
    writable: bool,
    share_mode: u32,
) -> (
    PathBuf,
    [PathBuf; FIXED_MODEL_PART_HANDLE_COUNT],
    ActiveScenarioHandleBundle,
) {
    static SEQUENCE: AtomicU64 = AtomicU64::new(1);
    let root = std::env::temp_dir().join(format!(
        "vrcforge-authority-direct-handle-{label}-{}-{}",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root).unwrap();
    let mut paths = Vec::with_capacity(FIXED_MODEL_PART_HANDLE_COUNT);
    let mut files = Vec::with_capacity(FIXED_MODEL_PART_HANDLE_COUNT);
    for index in 0..FIXED_MODEL_PART_HANDLE_COUNT {
        let path = root.join(format!("role-{index}.bin"));
        fs::write(&path, [index as u8 + 1]).unwrap();
        let file = OpenOptions::new()
            .read(true)
            .write(writable)
            .share_mode(share_mode)
            .open(&path)
            .unwrap();
        paths.push(path);
        files.push(file);
    }
    let paths: [PathBuf; FIXED_MODEL_PART_HANDLE_COUNT] = paths.try_into().unwrap();
    let files: [File; FIXED_MODEL_PART_HANDLE_COUNT] = files.try_into().ok().unwrap();
    (
        root,
        paths,
        ActiveScenarioHandleBundle::from_test_files(files),
    )
}

fn layout() -> AuthorityLayout {
    AuthorityLayout::for_test_roots(Path::new(r"C:\Program Files"), Path::new(r"C:\ProgramData"))
        .unwrap()
}

fn file_identity() -> StableFileIdentity {
    StableFileIdentity {
        volume_serial_number: 7,
        file_index: 11,
        size: 4096,
        creation_time: 13,
        last_write_time: 17,
        link_count: 1,
    }
}

fn receipt(layout: &AuthorityLayout, generation: [u8; 32]) -> VerifiedControllerLaunchReceipt {
    let path = layout
        .controller_executable_for_generation(&generation)
        .unwrap_or_else(|_| {
            PathBuf::from(
                r"C:\Program Files\VRCForgeEvidenceAuthority\v1\generations\invalid\vrcforge_primitive_evidence_controller.exe",
            )
        });
    VerifiedControllerLaunchReceipt::for_test(
        generation,
        path,
        CONTROLLER,
        7,
        101,
        103,
        file_identity(),
        LAUNCH_RECEIPT,
    )
}

fn policy() -> AuthorityPeerPolicy {
    let layout = layout();
    AuthorityPeerPolicy::for_installed_generation(&layout, receipt(&layout, GENERATION)).unwrap()
}

fn facts(policy: &AuthorityPeerPolicy) -> AuthorityPeerFacts<'_> {
    AuthorityPeerFacts {
        process_id: policy.expected_process_id(),
        process_creation_time: policy.expected_process_creation_time(),
        controller_path: policy.expected_controller_path(),
        controller_sha256: *policy.expected_controller_sha256(),
        running_image_file_identity: policy.expected_running_image_file_identity(),
        protected_launcher_receipt_sha256: *policy.expected_launcher_receipt_sha256(),
        running_process_handle_bound: true,
        running_image_object_bound: true,
        pipe_session_id: 7,
        token_session_id: 7,
        elevated: true,
        high_integrity: true,
        administrators_member: true,
    }
}

fn installed_controller_path() -> PathBuf {
    layout()
        .controller_executable_for_generation(&GENERATION)
        .unwrap()
}

fn installed_policy() -> InstalledControllerPolicy {
    InstalledControllerPolicy::for_test(
        GENERATION,
        SERVICE_PROCESS_ID,
        SERVICE_STARTED_AT,
        installed_controller_path(),
        CONTROLLER,
        file_identity(),
        INSTALLED_LAYOUT,
        FINAL_COMMIT_RECEIPT,
        CONTROLLER_SOURCE_BINDING,
    )
    .unwrap()
}

fn installed_facts(controller_path: &Path) -> AuthorityPeerFacts<'_> {
    AuthorityPeerFacts {
        process_id: SERVICE_PROCESS_ID + 1,
        process_creation_time: SERVICE_STARTED_AT + 1,
        controller_path,
        controller_sha256: CONTROLLER,
        running_image_file_identity: file_identity(),
        protected_launcher_receipt_sha256: [0; 32],
        running_process_handle_bound: true,
        running_image_object_bound: true,
        pipe_session_id: 7,
        token_session_id: 7,
        elevated: true,
        high_integrity: true,
        administrators_member: true,
    }
}

fn installed_runtime_broker_path() -> PathBuf {
    layout()
        .install_helper_executable_for_generation(&GENERATION)
        .unwrap()
}

fn installed_runtime_broker_policy() -> InstalledRuntimeBrokerPolicy {
    InstalledRuntimeBrokerPolicy::for_test(
        GENERATION,
        SERVICE_PROCESS_ID,
        SERVICE_STARTED_AT,
        installed_runtime_broker_path(),
        INSTALL_HELPER,
        file_identity(),
        INSTALLED_LAYOUT,
        FINAL_COMMIT_RECEIPT,
        INSTALL_HELPER_SOURCE_BINDING,
    )
    .unwrap()
}

fn installed_runtime_broker_facts(install_helper_path: &Path) -> AuthorityPeerFacts<'_> {
    AuthorityPeerFacts {
        process_id: SERVICE_PROCESS_ID + 2,
        process_creation_time: SERVICE_STARTED_AT - 1,
        controller_path: install_helper_path,
        controller_sha256: INSTALL_HELPER,
        running_image_file_identity: file_identity(),
        protected_launcher_receipt_sha256: [0; 32],
        running_process_handle_bound: true,
        running_image_object_bound: true,
        pipe_session_id: 7,
        token_session_id: 7,
        elevated: true,
        high_integrity: true,
        administrators_member: true,
    }
}

#[test]
fn policy_accepts_only_the_exact_high_administrator_controller() {
    let policy = policy();
    evaluate_peer_policy(&policy, &facts(&policy)).unwrap();
}

#[test]
fn generation_policy_requires_a_sealed_generation_launch_receipt() {
    let layout = layout();
    let policy =
        AuthorityPeerPolicy::for_installed_generation(&layout, receipt(&layout, GENERATION))
            .unwrap();
    assert!(policy
        .expected_controller_path()
        .to_string_lossy()
        .contains(&format!(r"\generations\{}\", "24".repeat(32))));
    evaluate_peer_policy(&policy, &facts(&policy)).unwrap();

    assert_eq!(
        AuthorityPeerPolicy::for_installed_generation(&layout, receipt(&layout, [0; 32]))
            .unwrap_err()
            .code(),
        "authority_peer_controller_layout_invalid"
    );

    let mut wrong_path = receipt(&layout, GENERATION);
    wrong_path.controller_path = PathBuf::from(
        r"C:\Program Files\VRCForgeEvidenceAuthority\v1\generations\copy\vrcforge_primitive_evidence_controller.exe",
    );
    assert_eq!(
        AuthorityPeerPolicy::for_installed_generation(&layout, wrong_path)
            .unwrap_err()
            .code(),
        "authority_peer_controller_launch_path_mismatch"
    );
}

#[test]
fn policy_rejects_every_identity_shortcut() {
    let policy = policy();
    let cases = [
        (
            AuthorityPeerFacts {
                elevated: false,
                ..facts(&policy)
            },
            "authority_peer_not_elevated",
        ),
        (
            AuthorityPeerFacts {
                high_integrity: false,
                ..facts(&policy)
            },
            "authority_peer_integrity_too_low",
        ),
        (
            AuthorityPeerFacts {
                administrators_member: false,
                ..facts(&policy)
            },
            "authority_peer_not_administrator",
        ),
        (
            AuthorityPeerFacts {
                pipe_session_id: 8,
                ..facts(&policy)
            },
            "authority_peer_session_mismatch",
        ),
        (
            AuthorityPeerFacts {
                token_session_id: 8,
                ..facts(&policy)
            },
            "authority_peer_session_mismatch",
        ),
        (
            AuthorityPeerFacts {
                process_id: 102,
                ..facts(&policy)
            },
            "authority_peer_process_receipt_mismatch",
        ),
        (
            AuthorityPeerFacts {
                process_creation_time: 104,
                ..facts(&policy)
            },
            "authority_peer_process_receipt_mismatch",
        ),
        (
            AuthorityPeerFacts {
                running_process_handle_bound: false,
                ..facts(&policy)
            },
            "authority_peer_process_handle_unbound",
        ),
        (
            AuthorityPeerFacts {
                running_image_object_bound: false,
                ..facts(&policy)
            },
            "authority_peer_running_image_object_unbound",
        ),
        (
            AuthorityPeerFacts {
                controller_path: Path::new(r"C:\controller-copy.exe"),
                ..facts(&policy)
            },
            "authority_peer_controller_path_mismatch",
        ),
        (
            AuthorityPeerFacts {
                controller_sha256: [0x43; 32],
                ..facts(&policy)
            },
            "authority_peer_controller_digest_mismatch",
        ),
        (
            AuthorityPeerFacts {
                running_image_file_identity: StableFileIdentity {
                    file_index: 12,
                    ..file_identity()
                },
                ..facts(&policy)
            },
            "authority_peer_running_image_identity_mismatch",
        ),
        (
            AuthorityPeerFacts {
                protected_launcher_receipt_sha256: [0x65; 32],
                ..facts(&policy)
            },
            "authority_peer_launcher_receipt_mismatch",
        ),
    ];
    for (observed, expected_code) in cases {
        assert_eq!(
            evaluate_peer_policy(&policy, &observed).unwrap_err().code(),
            expected_code
        );
    }
}

#[test]
fn file_identity_binding_changes_for_every_replacement_signal() {
    let baseline = file_identity();
    let baseline_digest = baseline.binding_digest();
    for changed in [
        StableFileIdentity {
            volume_serial_number: baseline.volume_serial_number + 1,
            ..baseline
        },
        StableFileIdentity {
            file_index: baseline.file_index + 1,
            ..baseline
        },
        StableFileIdentity {
            size: baseline.size + 1,
            ..baseline
        },
        StableFileIdentity {
            creation_time: baseline.creation_time + 1,
            ..baseline
        },
        StableFileIdentity {
            last_write_time: baseline.last_write_time + 1,
            ..baseline
        },
        StableFileIdentity {
            link_count: baseline.link_count + 1,
            ..baseline
        },
    ] {
        assert_ne!(changed.binding_digest(), baseline_digest);
    }
}

#[test]
fn connection_gate_admits_exactly_one_concurrent_connection() {
    const THREADS: usize = 8;
    let gate = Arc::new(AuthorityConnectionGate::default());
    let start = Arc::new(Barrier::new(THREADS + 1));
    let observed = Arc::new(Barrier::new(THREADS + 1));
    let successes = Arc::new(AtomicUsize::new(0));
    let mut workers = Vec::new();
    for _ in 0..THREADS {
        let gate = Arc::clone(&gate);
        let start = Arc::clone(&start);
        let observed = Arc::clone(&observed);
        let successes = Arc::clone(&successes);
        workers.push(std::thread::spawn(move || {
            start.wait();
            let lease = gate.try_acquire().ok();
            if lease.is_some() {
                successes.fetch_add(1, Ordering::AcqRel);
            }
            observed.wait();
            drop(lease);
        }));
    }
    start.wait();
    observed.wait();
    assert_eq!(successes.load(Ordering::Acquire), 1);
    for worker in workers {
        worker.join().unwrap();
    }
    assert!(!gate.has_active_connection());
    gate.try_acquire().unwrap().release();
}

#[test]
fn stop_and_failure_latches_never_reopen_admission() {
    let stop_gate = AuthorityConnectionGate::default();
    let lease = stop_gate.try_acquire().unwrap();
    stop_gate.request_stop();
    assert!(stop_gate.is_stop_requested());
    assert!(lease.is_stop_requested());
    assert_eq!(
        stop_gate.try_acquire().unwrap_err().code(),
        "authority_pipe_stopping"
    );
    drop(lease);
    assert!(!stop_gate.has_active_connection());
    assert_eq!(
        stop_gate.try_acquire().unwrap_err().code(),
        "authority_pipe_stopping"
    );

    let failed_gate = AuthorityConnectionGate::default();
    failed_gate.latch_failure();
    assert!(failed_gate.is_stop_requested());
    assert_eq!(
        failed_gate.try_acquire().unwrap_err().code(),
        "authority_pipe_failed"
    );
}

#[test]
fn pending_scenario_bundle_transitions_once_through_consuming_active_and_burned() {
    let (root, paths, source_files, tokens) = fixed_handle_fixture("state");
    let pending = PendingScenarioHandleBundle::default();
    assert_eq!(pending.state(), ScenarioHandleBundleState::Pending);
    let mut revalidations = 0usize;
    let external =
        admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || {
            revalidations += 1;
            if revalidations == 1 {
                assert_eq!(pending.state(), ScenarioHandleBundleState::Consuming);
            }
            Ok(())
        })
        .unwrap();
    assert_eq!(revalidations, 2);
    assert_eq!(pending.state(), ScenarioHandleBundleState::Active);
    assert_eq!(external.state(), ScenarioHandleBundleState::Active);
    for (index, file) in external.files().into_iter().enumerate() {
        let mut byte = [0u8; 1];
        assert_eq!(file.seek_read(&mut byte, 0).unwrap(), 1);
        assert_eq!(byte[0], [2, 3, 4, 6, 7, 8][index]);
    }
    let active = compose_fixed_handle_fixture(&source_files, external);
    assert_eq!(
        admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
            .unwrap_err()
            .code(),
        "authority_model_part_handle_capability_already_consumed"
    );
    drop(source_files);
    assert!(fs::remove_file(&paths[0]).is_err());
    drop(active);
    assert_eq!(pending.state(), ScenarioHandleBundleState::Burned);
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn protected_external_alias_burns_all_handles_and_cannot_replay() {
    let (root, paths, source_files, tokens) = fixed_handle_fixture("protected-external-alias");
    let pending = PendingScenarioHandleBundle::default();
    let external =
        admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
            .unwrap();
    let error = external
        .compose_with_protected_roots([
            reopen_scenario_file_object_for_test(&source_files[1]).unwrap(),
            reopen_scenario_file_object_for_test(&source_files[4]).unwrap(),
        ])
        .unwrap_err();
    assert_eq!(error.code(), "authority_model_part_handle_identity_alias");
    assert_eq!(pending.state(), ScenarioHandleBundleState::Burned);
    assert_eq!(
        admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
            .unwrap_err()
            .code(),
        "authority_model_part_handle_capability_already_consumed"
    );

    drop(source_files);
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn service_guard_still_denies_writers_after_an_external_deny_write_bait_closes() {
    let share_all = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
    let (root, paths, caller_handles) = direct_handle_fixture("guard-bait", false, share_all);
    let bait = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .open(&paths[EXTERNAL_FIXED_INDICES[0]])
        .unwrap();
    let tokens = ExternalModelPartHandleTokens::try_from_values(std::array::from_fn(|index| {
        caller_handles.files()[EXTERNAL_FIXED_INDICES[index]].as_raw_handle() as usize as u64
    }))
    .unwrap();
    let pending = PendingScenarioHandleBundle::default();
    let service_guards =
        admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
            .unwrap();

    drop(bait);
    let denied = OpenOptions::new()
        .write(true)
        .share_mode(share_all)
        .open(&paths[EXTERNAL_FIXED_INDICES[0]])
        .unwrap_err();
    assert_eq!(denied.raw_os_error(), Some(ERROR_SHARING_VIOLATION as i32));

    drop(service_guards);
    let writer = OpenOptions::new()
        .write(true)
        .share_mode(share_all)
        .open(&paths[EXTERNAL_FIXED_INDICES[0]])
        .unwrap();
    drop(writer);
    drop(caller_handles);
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn service_guard_reopens_the_admitted_object_not_a_replacement_path() {
    let share_all = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
    let (root, paths, caller_handles) =
        direct_handle_fixture("guard-object-replacement", false, share_all);
    let fixed_index = EXTERNAL_FIXED_INDICES[0];
    let displaced = root.join("role-1-displaced.bin");
    fs::rename(&paths[fixed_index], &displaced).unwrap();
    fs::write(&paths[fixed_index], [0xfe]).unwrap();
    let replacement = OpenOptions::new()
        .read(true)
        .share_mode(share_all)
        .open(&paths[fixed_index])
        .unwrap();
    let tokens = ExternalModelPartHandleTokens::try_from_values(std::array::from_fn(|index| {
        caller_handles.files()[EXTERNAL_FIXED_INDICES[index]].as_raw_handle() as usize as u64
    }))
    .unwrap();
    let pending = PendingScenarioHandleBundle::default();
    let service_guards =
        admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
            .unwrap();

    let source_binding =
        service_guard_binding_for_test(caller_handles.files()[fixed_index]).unwrap();
    let guard_binding = service_guard_binding_for_test(service_guards.files()[0]).unwrap();
    let replacement_binding = service_guard_binding_for_test(&replacement).unwrap();
    assert_eq!(guard_binding, source_binding);
    assert_ne!(guard_binding.0, replacement_binding.0);
    assert_ne!(guard_binding.1, replacement_binding.1);
    assert!(!service_guard_is_inheritable_for_test(service_guards.files()[0]).unwrap());

    let mut admitted_byte = [0u8; 1];
    assert_eq!(
        service_guards.files()[0]
            .seek_read(&mut admitted_byte, 0)
            .unwrap(),
        1
    );
    assert_eq!(admitted_byte, [fixed_index as u8 + 1]);
    let mut replacement_byte = [0u8; 1];
    assert_eq!(replacement.seek_read(&mut replacement_byte, 0).unwrap(), 1);
    assert_eq!(replacement_byte, [0xfe]);

    let denied = OpenOptions::new()
        .write(true)
        .share_mode(share_all)
        .open(&displaced)
        .unwrap_err();
    assert_eq!(denied.raw_os_error(), Some(ERROR_SHARING_VIOLATION as i32));
    let replacement_writer = OpenOptions::new()
        .write(true)
        .share_mode(share_all)
        .open(&paths[fixed_index])
        .unwrap();
    drop(replacement_writer);

    drop(replacement);
    drop(service_guards);
    drop(caller_handles);
    fs::remove_file(displaced).unwrap();
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn service_guard_reopen_failure_burns_and_closes_every_partial_guard() {
    for fail_index in 0..EXTERNAL_MODEL_PART_HANDLE_COUNT {
        let (root, paths, source_files, tokens) = fixed_handle_fixture_with_guard_reopen_failure(
            &format!("partial-{fail_index}"),
            fail_index,
        );
        let pending = PendingScenarioHandleBundle::default();
        assert_eq!(
            admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
                .unwrap_err()
                .code(),
            "authority_model_part_service_guard_open_failed"
        );
        assert_eq!(pending.state(), ScenarioHandleBundleState::Burned);
        assert_eq!(
            admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
                .unwrap_err()
                .code(),
            "authority_model_part_handle_capability_already_consumed"
        );

        drop(source_files);
        remove_fixed_handle_fixture(&root, &paths);
    }
}

#[test]
fn service_guard_rejects_mutating_external_source_access_and_burns() {
    let share_all = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
    let (root, paths, caller_handles) =
        direct_handle_fixture("guard-mutating-access", true, share_all);
    let tokens = ExternalModelPartHandleTokens::try_from_values(std::array::from_fn(|index| {
        caller_handles.files()[EXTERNAL_FIXED_INDICES[index]].as_raw_handle() as usize as u64
    }))
    .unwrap();
    let pending = PendingScenarioHandleBundle::default();
    assert_eq!(
        admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
            .unwrap_err()
            .code(),
        "authority_model_part_worker_handle_access_invalid"
    );
    assert_eq!(pending.state(), ScenarioHandleBundleState::Burned);

    drop(caller_handles);
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn worker_bundle_duplicates_exactly_eight_files_in_fixed_role_order() {
    let (root, paths, source_files, tokens) = fixed_handle_fixture("worker-order");
    let pending = PendingScenarioHandleBundle::default();
    let external =
        admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
            .unwrap();
    let active = compose_fixed_handle_fixture(&source_files, external);
    let worker = active.try_clone_for_worker().unwrap();

    assert_eq!(worker.files().len(), FIXED_MODEL_PART_HANDLE_COUNT);
    for (index, file) in worker.files().into_iter().enumerate() {
        let mut byte = [0u8; 1];
        assert_eq!(file.seek_read(&mut byte, 0).unwrap(), 1);
        assert_eq!(byte[0], index as u8 + 1);
    }
    let debug = format!("{worker:?}");
    assert!(debug.contains("driver"));
    assert!(debug.contains("fixture_baseline"));
    assert!(!debug.contains(root.to_string_lossy().as_ref()));

    drop(source_files);
    drop(active);
    assert!(fs::remove_file(&paths[0]).is_err());
    drop(worker);
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn prepared_snapshot_revalidates_originals_and_exact_worker_duplicates() {
    let (root, paths, source_files, tokens) = fixed_handle_fixture("worker-snapshot");
    let pending = PendingScenarioHandleBundle::default();
    let external =
        admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
            .unwrap();
    let active = compose_fixed_handle_fixture(&source_files, external);
    let snapshot = active.capture_prepare_snapshot().unwrap();
    let worker = active.try_clone_for_worker().unwrap();

    active.validate_snapshot(&snapshot).unwrap();
    worker.validate_snapshot(&snapshot).unwrap();
    let debug = format!("{snapshot:?}");
    assert!(debug.contains("fixture_baseline"));
    assert!(!debug.contains(root.to_string_lossy().as_ref()));

    drop(source_files);
    drop(worker);
    drop(active);
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn verified_start_capability_borrows_exact_roles_digest_and_both_live_sets() {
    let (root, paths, source_files, tokens) = fixed_handle_fixture("verified-start");
    let pending = PendingScenarioHandleBundle::default();
    let external =
        admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
            .unwrap();
    let active = compose_fixed_handle_fixture(&source_files, external);
    let snapshot = active.capture_prepare_snapshot().unwrap();
    let worker = active.try_clone_for_worker().unwrap();
    let capability = active
        .verified_start_capability(&worker, &snapshot)
        .unwrap();

    assert_eq!(capability.roles(), &FIXED_MODEL_PART_HANDLE_ROLES);
    assert_eq!(capability.snapshot_digest(), snapshot.digest());
    for (index, (original, duplicate)) in capability
        .original_files()
        .into_iter()
        .zip(capability.worker_files())
        .enumerate()
    {
        let mut original_byte = [0u8; 1];
        let mut duplicate_byte = [0u8; 1];
        assert_eq!(original.seek_read(&mut original_byte, 0).unwrap(), 1);
        assert_eq!(duplicate.seek_read(&mut duplicate_byte, 0).unwrap(), 1);
        assert_eq!(original_byte, [index as u8 + 1]);
        assert_eq!(duplicate_byte, original_byte);
    }
    let debug = format!("{capability:?}");
    assert!(debug.contains("fixture_baseline"));
    assert!(!debug.contains(root.to_string_lossy().as_ref()));

    drop(capability);
    drop(source_files);
    drop(worker);
    drop(active);
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn reopened_worker_and_start_contract_file_objects_keep_all_cursors_independent() {
    let (root, paths, source_files, tokens) = fixed_handle_fixture("reopened-cursors");
    let pending = PendingScenarioHandleBundle::default();
    let external =
        admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
            .unwrap();
    let active = compose_fixed_handle_fixture(&source_files, external);
    let snapshot = active.capture_prepare_snapshot().unwrap();
    let worker = active.try_clone_for_worker().unwrap();

    let source_positions = std::array::from_fn(|index| 11 + index as u64);
    let worker_positions = std::array::from_fn(|index| 31 + index as u64);
    for (file, position) in active.files().into_iter().zip(source_positions) {
        set_handle_stream_position(file, position);
    }
    for (file, position) in worker.files().into_iter().zip(worker_positions) {
        set_handle_stream_position(file, position);
    }
    assert_eq!(handle_stream_positions(active.files()), source_positions);
    assert_eq!(handle_stream_positions(worker.files()), worker_positions);

    let capability = active
        .verified_start_capability(&worker, &snapshot)
        .unwrap();
    let contract = capability
        .into_owned_contract([0x71; 32], [0x72; 32])
        .unwrap();
    assert_eq!(handle_stream_positions(active.files()), source_positions);
    assert_eq!(handle_stream_positions(worker.files()), worker_positions);
    assert_eq!(
        contract.executable_stream_positions_for_test().unwrap(),
        [0, 0]
    );

    let contract_positions = [51, 61];
    contract
        .set_executable_stream_positions_for_test(contract_positions)
        .unwrap();
    assert_eq!(handle_stream_positions(active.files()), source_positions);
    assert_eq!(handle_stream_positions(worker.files()), worker_positions);
    assert_eq!(
        contract.executable_stream_positions_for_test().unwrap(),
        contract_positions
    );

    for _ in 0..3 {
        active.validate_snapshot(&snapshot).unwrap();
        worker.validate_snapshot(&snapshot).unwrap();
        assert!(contract.verifies_for(&[0x71; 32], &[0x72; 32]));
    }

    let barrier = Barrier::new(3);
    let source_files_for_verify = active.files();
    let worker_files_for_verify = worker.files();
    std::thread::scope(|scope| {
        for _ in 0..2 {
            let barrier = &barrier;
            let snapshot = &snapshot;
            let contract = &contract;
            let source_files_for_verify = source_files_for_verify;
            let worker_files_for_verify = worker_files_for_verify;
            scope.spawn(move || {
                barrier.wait();
                for _ in 0..3 {
                    snapshot
                        .validate_files_for_test(source_files_for_verify)
                        .unwrap();
                    snapshot
                        .validate_files_for_test(worker_files_for_verify)
                        .unwrap();
                    assert!(contract.verifies_for(&[0x71; 32], &[0x72; 32]));
                }
            });
        }
        barrier.wait();
    });

    assert_eq!(handle_stream_positions(active.files()), source_positions);
    assert_eq!(handle_stream_positions(worker.files()), worker_positions);
    assert_eq!(
        contract.executable_stream_positions_for_test().unwrap(),
        contract_positions
    );

    drop(contract);
    drop(source_files);
    drop(worker);
    drop(active);
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn start_contract_reopen_failure_and_alias_close_every_partial_file_object() {
    for inject_alias in [false, true] {
        let label = if inject_alias {
            "contract-reopen-alias"
        } else {
            "contract-reopen-failure"
        };
        let (root, paths, source_files, tokens) = fixed_handle_fixture(label);
        let pending = PendingScenarioHandleBundle::default();
        let external =
            admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
                .unwrap();
        let active = compose_fixed_handle_fixture(&source_files, external);
        let snapshot = active.capture_prepare_snapshot().unwrap();
        let worker = active.try_clone_for_worker().unwrap();
        let bridge_index = FIXED_MODEL_PART_HANDLE_ROLES
            .iter()
            .position(|role| *role == "bridge_launcher")
            .unwrap();
        let original_driver = active.files()[0];
        let capability = active
            .verified_start_capability(&worker, &snapshot)
            .unwrap();
        let error = capability
            .into_owned_contract_with_test([0x73; 32], [0x74; 32], |index, file| {
                if index != bridge_index {
                    return reopen_scenario_file_object_for_test(file).map_err(|_| {
                        std::io::Error::new(
                            std::io::ErrorKind::Other,
                            "driver reopen wrapper failure",
                        )
                    });
                }
                if inject_alias {
                    reopen_scenario_file_object_for_test(original_driver).map_err(|_| {
                        std::io::Error::new(
                            std::io::ErrorKind::Other,
                            "alias reopen wrapper failure",
                        )
                    })
                } else {
                    Err(std::io::Error::new(
                        std::io::ErrorKind::Other,
                        "forced bridge reopen failure",
                    ))
                }
            })
            .unwrap_err();
        assert_eq!(
            error.code(),
            if inject_alias {
                "authority_model_part_worker_handle_snapshot_mismatch"
            } else {
                "authority_model_part_worker_handle_clone_failed"
            }
        );

        drop(source_files);
        drop(worker);
        drop(active);
        // The first executable reopen and the failing/aliased second reopen
        // must both be closed, or FILE_SHARE_READ would block this cleanup.
        remove_fixed_handle_fixture(&root, &paths);
    }
}

#[test]
fn owned_start_contract_recomputes_its_binding_and_revalidates_live_files() {
    static SEQUENCE: AtomicU64 = AtomicU64::new(1);
    let root = std::env::temp_dir().join(format!(
        "vrcforge-authority-start-contract-{}-{}",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root).unwrap();
    let paths = [root.join("driver.bin"), root.join("bridge-launcher.bin")];
    fs::write(&paths[0], b"driver-start-contract").unwrap();
    fs::write(&paths[1], b"bridge-start-contract").unwrap();
    let share_all = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
    let open_contract = || {
        let driver = OpenOptions::new()
            .read(true)
            .share_mode(share_all)
            .open(&paths[0])
            .unwrap();
        let bridge_launcher = OpenOptions::new()
            .read(true)
            .share_mode(share_all)
            .open(&paths[1])
            .unwrap();
        VerifiedScenarioStartContract::for_test_from_files(
            driver,
            bridge_launcher,
            [0x31; 32],
            [0x32; 32],
        )
        .unwrap()
    };

    let mut corrupted = open_contract();
    let live_drift = open_contract();
    assert!(corrupted.verifies_for(&[0x31; 32], &[0x32; 32]));
    assert!(live_drift.verifies_for(&[0x31; 32], &[0x32; 32]));

    corrupted.corrupt_binding_digest_for_test();
    assert!(!corrupted.verifies_for(&[0x31; 32], &[0x32; 32]));

    let mut writer = OpenOptions::new()
        .write(true)
        .truncate(true)
        .share_mode(share_all)
        .open(&paths[0])
        .unwrap();
    writer.write_all(b"driver-start-drifted").unwrap();
    writer.flush().unwrap();
    drop(writer);
    assert!(!live_drift.verifies_for(&[0x31; 32], &[0x32; 32]));

    drop(corrupted);
    drop(live_drift);
    fs::remove_file(&paths[0]).unwrap();
    fs::remove_file(&paths[1]).unwrap();
    fs::remove_dir(root).unwrap();
}

#[test]
fn executable_create_binding_accepts_the_exact_held_process_image_object() {
    static SEQUENCE: AtomicU64 = AtomicU64::new(1);
    let root = std::env::temp_dir().join(format!(
        "vrcforge-authority-create-held-{}-{}",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root).unwrap();
    let driver = root.join("driver.bin");
    let bridge = root.join("bridge.bin");
    fs::write(&driver, b"driver-held-object").unwrap();
    fs::write(&bridge, b"bridge-held-object").unwrap();
    let share_all = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
    let open = |path: &std::path::Path| {
        OpenOptions::new()
            .read(true)
            .share_mode(share_all)
            .open(path)
            .unwrap()
    };
    let contract = VerifiedScenarioStartContract::for_test_from_files(
        open(&driver),
        open(&bridge),
        [0x31; 32],
        [0x32; 32],
    )
    .unwrap();
    let launch = contract
        .prepare_executable_launch(ScenarioStartExecutableRole::Driver)
        .unwrap();
    let process_image_receipt_identity_digest = [0x43; 32];
    let binding = launch
        .bind_created_process_image(&open(&driver), process_image_receipt_identity_digest)
        .unwrap();
    launch
        .validate_created_process_image(&binding, process_image_receipt_identity_digest)
        .unwrap();

    drop(launch);
    drop(contract);
    fs::remove_file(driver).unwrap();
    fs::remove_file(bridge).unwrap();
    fs::remove_dir(root).unwrap();
}

#[test]
fn executable_create_binding_rejects_same_bytes_from_a_different_object() {
    static SEQUENCE: AtomicU64 = AtomicU64::new(1);
    let root = std::env::temp_dir().join(format!(
        "vrcforge-authority-create-object-{}-{}",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root).unwrap();
    let driver = root.join("driver.bin");
    let copied_driver = root.join("driver-copy.bin");
    let bridge = root.join("bridge.bin");
    fs::write(&driver, b"same-driver-bytes").unwrap();
    fs::write(&copied_driver, b"same-driver-bytes").unwrap();
    fs::write(&bridge, b"bridge-bytes").unwrap();
    let share_all = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
    let open = |path: &std::path::Path| {
        OpenOptions::new()
            .read(true)
            .share_mode(share_all)
            .open(path)
            .unwrap()
    };
    let contract = VerifiedScenarioStartContract::for_test_from_files(
        open(&driver),
        open(&bridge),
        [0x31; 32],
        [0x32; 32],
    )
    .unwrap();
    let launch = contract
        .prepare_executable_launch(ScenarioStartExecutableRole::Driver)
        .unwrap();
    let binding = launch
        .bind_created_process_image(&open(&copied_driver), [0x44; 32])
        .unwrap();
    assert_eq!(
        launch
            .validate_created_process_image(&binding, [0x44; 32])
            .unwrap_err()
            .code(),
        "authority_model_part_worker_handle_snapshot_mismatch"
    );

    drop(launch);
    drop(contract);
    fs::remove_file(driver).unwrap();
    fs::remove_file(copied_driver).unwrap();
    fs::remove_file(bridge).unwrap();
    fs::remove_dir(root).unwrap();
}

#[test]
fn executable_create_binding_rejects_a_retained_start_handle_duplicate() {
    static SEQUENCE: AtomicU64 = AtomicU64::new(1);
    let root = std::env::temp_dir().join(format!(
        "vrcforge-authority-create-duplicate-{}-{}",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root).unwrap();
    let driver = root.join("driver.bin");
    let bridge = root.join("bridge.bin");
    fs::write(&driver, b"driver-bytes").unwrap();
    fs::write(&bridge, b"bridge-bytes").unwrap();
    let share_all = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
    let open = |path: &std::path::Path| {
        OpenOptions::new()
            .read(true)
            .share_mode(share_all)
            .open(path)
            .unwrap()
    };
    let contract = VerifiedScenarioStartContract::for_test_from_files(
        open(&driver),
        open(&bridge),
        [0x31; 32],
        [0x32; 32],
    )
    .unwrap();
    let launch = contract
        .prepare_executable_launch(ScenarioStartExecutableRole::Driver)
        .unwrap();
    let binding = launch
        .create_binding_for_test(&open(&driver), [0x45; 32], true)
        .unwrap();
    assert!(launch
        .validate_created_process_image(&binding, [0x45; 32])
        .is_err());

    drop(launch);
    drop(contract);
    fs::remove_file(driver).unwrap();
    fs::remove_file(bridge).unwrap();
    fs::remove_dir(root).unwrap();
}

#[test]
fn executable_launch_seam_exposes_no_clonable_file_or_raw_handle() {
    let source = include_str!("../primitive_evidence_authority_pipe.rs");
    assert!(!source.contains("pub(crate) fn file(&self) -> &File"));
    assert!(!source.contains("impl AsRawHandle for VerifiedScenarioExecutableLaunch"));
    assert!(!source.contains("impl AsHandle for VerifiedScenarioExecutableLaunch"));
}

#[test]
fn production_worker_and_start_contract_paths_use_handle_reopen_not_file_clone() {
    let source = include_str!("../primitive_evidence_authority_pipe.rs");
    let worker_start = source.find("pub(crate) fn try_clone_for_worker(").unwrap();
    let worker_end = source[worker_start..]
        .find("#[cfg(test)]\n        pub(crate) fn from_test_files")
        .map(|offset| worker_start + offset)
        .unwrap();
    let worker_path = &source[worker_start..worker_end];
    assert!(worker_path.contains("reopen_scenario_file_object_read_only"));
    assert!(!worker_path.contains("file.try_clone()"));

    let contract_start = source.find("pub(crate) fn into_owned_contract(").unwrap();
    let contract_end = source[contract_start..]
        .find("#[cfg(test)]\n        pub(super) fn into_owned_contract_with_test")
        .map(|offset| contract_start + offset)
        .unwrap();
    let contract_path = &source[contract_start..contract_end];
    assert!(contract_path.contains("reopen_scenario_file_object_read_only"));
    assert!(!contract_path.contains("file.try_clone()"));
    assert!(source.contains("ReOpenFile("));
}

#[test]
fn production_service_guard_has_no_path_or_name_reopen_fallback() {
    let source = include_str!("../primitive_evidence_authority_pipe.rs");
    let validation_start = source.find("fn validate_and_transfer(").unwrap();
    let validation_end = source[validation_start..]
        .find("fn duplicate_one_handle(")
        .map(|offset| validation_start + offset)
        .unwrap();
    let validation = &source[validation_start..validation_end];
    assert!(validation.contains("reopen_service_scenario_guard(&admitted[index])"));
    assert!(!validation.contains("OpenOptions::new"));
    assert!(!validation.contains(".open(path)"));
    assert!(!validation.contains("std::env"));
    assert!(!validation.contains("file_name()"));
    assert!(!validation.contains("with_file_name"));

    let guard_start = source
        .find("fn reopen_service_scenario_guard(source: &File)")
        .unwrap();
    let guard_end = source[guard_start..]
        .find("#[cfg(test)]\n    pub(super) fn service_guard_binding_for_test")
        .map(|offset| guard_start + offset)
        .unwrap();
    let guard = &source[guard_start..guard_end];
    assert!(guard.contains("reopen_scenario_file_object_with_access("));
    assert!(guard.contains("FILE_SHARE_READ"));
    assert!(!guard.contains("OpenOptions::new"));
    assert!(!source.contains("fn open_service_scenario_guard(path: &Path)"));
}

#[test]
fn prepared_snapshot_rejects_fixed_role_permutation() {
    let (root, paths, source_files, tokens) = fixed_handle_fixture("worker-permutation");
    let pending = PendingScenarioHandleBundle::default();
    let external =
        admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
            .unwrap();
    let active = compose_fixed_handle_fixture(&source_files, external);
    let snapshot = active.capture_prepare_snapshot().unwrap();
    let mut files = active.files().map(|file| file.try_clone().unwrap());
    files.swap(0, 1);
    let worker = WorkerScenarioHandleBundle::from_test_files(files, Arc::new(AtomicUsize::new(0)));

    assert_eq!(
        worker.validate_snapshot(&snapshot).unwrap_err().code(),
        "authority_model_part_worker_handle_snapshot_mismatch"
    );

    drop(source_files);
    drop(worker);
    drop(active);
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn prepared_snapshot_rejects_same_length_content_mutation() {
    let share_all = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
    let (root, paths, active) = direct_handle_fixture("same-length", true, share_all);
    let snapshot = active.capture_relaxed_snapshot_for_test().unwrap();

    assert_eq!(active.files()[3].seek_write(&[0xa5], 0).unwrap(), 1);
    active.files()[3].sync_all().unwrap();
    assert_eq!(
        active
            .validate_relaxed_snapshot_for_test(&snapshot)
            .unwrap_err()
            .code(),
        "authority_model_part_worker_handle_snapshot_mismatch"
    );

    drop(active);
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn prepared_snapshot_rejects_source_path_replacement() {
    let share_all = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
    let (root, paths, active) = direct_handle_fixture("path-replacement", false, share_all);
    let snapshot = active.capture_relaxed_snapshot_for_test().unwrap();
    let displaced = root.join("role-0-displaced.bin");

    fs::rename(&paths[0], &displaced).unwrap();
    fs::write(&paths[0], [1u8]).unwrap();
    assert_eq!(
        active
            .validate_relaxed_snapshot_for_test(&snapshot)
            .unwrap_err()
            .code(),
        "authority_model_part_worker_handle_snapshot_mismatch"
    );

    drop(active);
    fs::remove_file(displaced).unwrap();
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn worker_snapshot_rejects_modification_after_duplicate_takeover() {
    let share_all = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
    let (root, paths, active) = direct_handle_fixture("post-duplicate-mutation", true, share_all);
    let snapshot = active.capture_relaxed_snapshot_for_test().unwrap();
    let worker = active.clone_for_worker_relaxed_for_test().unwrap();

    assert_eq!(active.files()[6].seek_write(&[0xb6], 0).unwrap(), 1);
    active.files()[6].sync_all().unwrap();
    assert_eq!(
        worker
            .validate_relaxed_snapshot_for_test(&snapshot)
            .unwrap_err()
            .code(),
        "authority_model_part_worker_handle_snapshot_mismatch"
    );

    drop(worker);
    drop(active);
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn production_snapshot_requires_read_only_nonsharing_single_link_files() {
    let share_all = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
    let (root, paths, writable) = direct_handle_fixture("writable", true, share_all);
    assert_eq!(
        writable.capture_prepare_snapshot().unwrap_err().code(),
        "authority_model_part_worker_handle_access_invalid"
    );
    drop(writable);
    remove_fixed_handle_fixture(&root, &paths);

    let (root, paths, shared) = direct_handle_fixture("shared", false, share_all);
    assert_eq!(
        shared.capture_prepare_snapshot().unwrap_err().code(),
        "authority_model_part_worker_handle_sharing_invalid"
    );
    drop(shared);
    remove_fixed_handle_fixture(&root, &paths);

    let (root, paths, linked) = direct_handle_fixture("hard-link", false, share_all);
    let alias = root.join("role-0-alias.bin");
    fs::hard_link(&paths[0], &alias).unwrap();
    assert_eq!(
        linked.capture_prepare_snapshot().unwrap_err().code(),
        "authority_model_part_worker_handle_snapshot_invalid"
    );
    drop(linked);
    fs::remove_file(alias).unwrap();
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn worker_clone_failure_is_fixed_code_and_closes_every_partial_duplicate() {
    for fail_index in 0..FIXED_MODEL_PART_HANDLE_COUNT {
        let (root, paths, source_files, tokens) =
            fixed_handle_fixture(&format!("worker-clone-failure-{fail_index}"));
        let pending = PendingScenarioHandleBundle::default();
        let external =
            admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
                .unwrap();
        let active = compose_fixed_handle_fixture(&source_files, external);
        let error = active
            .try_clone_for_worker_with_test(|index, file| {
                if index == fail_index {
                    Err(std::io::Error::new(
                        std::io::ErrorKind::Other,
                        "forced worker clone failure",
                    ))
                } else {
                    reopen_scenario_file_object_for_test(file).map_err(|_| {
                        std::io::Error::new(
                            std::io::ErrorKind::Other,
                            "forced worker reopen wrapper failure",
                        )
                    })
                }
            })
            .unwrap_err();
        assert_eq!(
            error.code(),
            "authority_model_part_worker_handle_clone_failed"
        );
        assert_eq!(error.win32(), None);

        drop(source_files);
        drop(active);
        remove_fixed_handle_fixture(&root, &paths);
    }
}

#[test]
fn worker_clone_rejects_a_role_substitution_without_exposing_file_details() {
    let (root, paths, source_files, tokens) = fixed_handle_fixture("worker-role-drift");
    let pending = PendingScenarioHandleBundle::default();
    let external =
        admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || Ok(()))
            .unwrap();
    let active = compose_fixed_handle_fixture(&source_files, external);
    let originals = active.files();
    let error = active
        .try_clone_for_worker_with_test(|index, file| {
            if index == FIXED_MODEL_PART_HANDLE_COUNT - 1 {
                reopen_scenario_file_object_for_test(originals[0])
            } else {
                reopen_scenario_file_object_for_test(file)
            }
            .map_err(|_| {
                std::io::Error::new(
                    std::io::ErrorKind::Other,
                    "forced worker role reopen wrapper failure",
                )
            })
        })
        .unwrap_err();
    assert_eq!(
        error.code(),
        "authority_model_part_worker_handle_binding_mismatch"
    );
    assert_eq!(error.win32(), None);
    assert!(!format!("{error:?}").contains(root.to_string_lossy().as_ref()));

    drop(source_files);
    drop(active);
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn peer_revalidation_failure_after_duplication_burns_and_closes_the_bundle() {
    let (root, paths, source_files, tokens) = fixed_handle_fixture("post-peer-failure");
    let pending = PendingScenarioHandleBundle::default();
    let mut revalidations = 0usize;
    let error = admit_current_process_handles_with_revalidation_for_test(&pending, tokens, || {
        revalidations += 1;
        if revalidations == 2 {
            Err(AuthorityPipeError::new(
                "authority_peer_connected_pipe_binding_changed",
            ))
        } else {
            Ok(())
        }
    })
    .unwrap_err();
    assert_eq!(revalidations, 2);
    assert_eq!(
        error.code(),
        "authority_peer_connected_pipe_binding_changed"
    );
    assert_eq!(pending.state(), ScenarioHandleBundleState::Burned);
    drop(source_files);
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn duplicate_batch_closes_every_partial_result_on_each_middle_failure() {
    for fail_index in 0..EXTERNAL_MODEL_PART_HANDLE_COUNT {
        let (root, paths, source_files, tokens) =
            fixed_handle_fixture(&format!("partial-{fail_index}"));
        assert_eq!(
            duplicate_scenario_handles_with_forced_failure(tokens, fail_index)
                .unwrap_err()
                .code(),
            "authority_model_part_handle_duplicate_failed"
        );
        drop(source_files);
        remove_fixed_handle_fixture(&root, &paths);
    }
}

#[test]
fn duplicated_bundle_requires_unique_noninheritable_disk_files() {
    let (root, paths, source_files, tokens) = fixed_handle_fixture("valid-files");
    let duplicated = duplicate_and_validate_scenario_handles_for_test(tokens).unwrap();
    drop(source_files);
    assert!(fs::remove_file(&paths[1]).is_err());
    drop(duplicated);
    remove_fixed_handle_fixture(&root, &paths);

    let (root, paths, source_files, _) = fixed_handle_fixture("alias");
    let external_indices = [1usize, 2, 3, 5, 6, 7];
    let alias = source_files[external_indices[0]].try_clone().unwrap();
    let mut values = std::array::from_fn(|index| {
        source_files[external_indices[index]].as_raw_handle() as usize as u64
    });
    values[EXTERNAL_MODEL_PART_HANDLE_COUNT - 1] = alias.as_raw_handle() as usize as u64;
    let tokens = ExternalModelPartHandleTokens::try_from_values(values).unwrap();
    assert_eq!(
        duplicate_and_validate_scenario_handles_for_test(tokens)
            .unwrap_err()
            .code(),
        "authority_model_part_handle_identity_alias"
    );
    drop(alias);
    drop(source_files);
    remove_fixed_handle_fixture(&root, &paths);

    let (root, paths, source_files, _) = fixed_handle_fixture("non-disk");
    let pipe_name = unique_test_pipe_name();
    let pipe = create_pipe_with_sddl(&pipe_name, TEST_PIPE_SDDL).unwrap();
    let client = open_test_client(&pipe_name).unwrap();
    let mut values = std::array::from_fn(|index| {
        source_files[external_indices[index]].as_raw_handle() as usize as u64
    });
    values[EXTERNAL_MODEL_PART_HANDLE_COUNT - 1] = client.as_raw_handle() as usize as u64;
    let tokens = ExternalModelPartHandleTokens::try_from_values(values).unwrap();
    assert_eq!(
        duplicate_and_validate_scenario_handles_for_test(tokens)
            .unwrap_err()
            .code(),
        "authority_model_part_handle_type_invalid"
    );
    drop(client);
    drop(pipe);
    drop(source_files);
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn pending_bundle_can_be_burned_without_arming_and_never_reopens() {
    let pending = PendingScenarioHandleBundle::default();
    pending.burn().unwrap();
    assert_eq!(pending.state(), ScenarioHandleBundleState::Burned);
    assert_eq!(
        pending.burn().unwrap_err().code(),
        "authority_model_part_handle_capability_already_consumed"
    );
}

#[test]
fn policy_rejects_incomplete_launch_receipts() {
    let layout = layout();

    let mut zero_controller = receipt(&layout, GENERATION);
    zero_controller.controller_sha256 = [0; 32];
    assert_eq!(
        AuthorityPeerPolicy::for_installed_generation(&layout, zero_controller)
            .unwrap_err()
            .code(),
        "authority_peer_controller_digest_invalid"
    );

    let mut zero_process = receipt(&layout, GENERATION);
    zero_process.process_id = 0;
    assert_eq!(
        AuthorityPeerPolicy::for_installed_generation(&layout, zero_process)
            .unwrap_err()
            .code(),
        "authority_peer_process_receipt_invalid"
    );

    let mut zero_size = receipt(&layout, GENERATION);
    zero_size.running_image_file_identity.size = 0;
    assert_eq!(
        AuthorityPeerPolicy::for_installed_generation(&layout, zero_size)
            .unwrap_err()
            .code(),
        "authority_peer_running_image_identity_invalid"
    );

    let mut zero_file_id = receipt(&layout, GENERATION);
    zero_file_id.running_image_file_identity.file_index = 0;
    assert_eq!(
        AuthorityPeerPolicy::for_installed_generation(&layout, zero_file_id)
            .unwrap_err()
            .code(),
        "authority_peer_running_image_identity_invalid"
    );

    let mut zero_launcher = receipt(&layout, GENERATION);
    zero_launcher.protected_launcher_receipt_sha256 = [0; 32];
    assert_eq!(
        AuthorityPeerPolicy::for_installed_generation(&layout, zero_launcher)
            .unwrap_err()
            .code(),
        "authority_peer_launcher_receipt_invalid"
    );
}

#[test]
fn production_sddl_parses_without_elevation() {
    let descriptor = SecurityDescriptor::from_sddl(AUTHORITY_PIPE_SDDL).unwrap();
    assert!(!descriptor.0.is_null());
}

#[test]
fn current_process_token_groups_are_read_without_token_duplication_rights() {
    let (session_id, _, _, _) = current_process_token_snapshot_for_test().unwrap();
    assert_eq!(session_id, current_process_session_id().unwrap());
}

#[test]
fn first_instance_flag_prevents_a_second_real_server() {
    let name = unique_test_pipe_name();
    let _first = create_pipe_with_sddl(&name, TEST_PIPE_SDDL).unwrap();
    let error = create_pipe_with_sddl(&name, TEST_PIPE_SDDL).unwrap_err();
    assert_eq!(error.code(), "authority_pipe_create_failed");
}

#[test]
fn stop_handle_disconnects_a_connected_blocked_reader_without_waiting_for_io_timeout() {
    let name = unique_test_pipe_name();
    let pipe = create_pipe_with_sddl(&name, TEST_PIPE_SDDL).unwrap();
    let client = open_test_client(&name).unwrap();
    pipe.connect_client_for_test().unwrap();
    let stop = pipe.stop_handle();
    let (started_tx, started_rx) = mpsc::sync_channel(0);
    let reader = std::thread::spawn(move || {
        let mut pipe = pipe;
        started_tx.send(()).unwrap();
        let mut byte = [0u8; 1];
        pipe.read(&mut byte)
    });
    started_rx.recv().unwrap();

    let started = Instant::now();
    stop.cancel_pending_io().unwrap();
    let result = reader.join().unwrap();
    assert!(started.elapsed() < Duration::from_secs(2));
    match result {
        Ok(0) => {}
        Err(error) if error.kind() == std::io::ErrorKind::ConnectionAborted => {}
        other => panic!("unexpected read result: {other:?}"),
    }
    drop(client);
}

#[test]
fn stop_latched_before_accept_prevents_a_late_blocking_connect() {
    let name = unique_test_pipe_name();
    let pipe = create_pipe_with_sddl(&name, TEST_PIPE_SDDL).unwrap();
    pipe.stop_handle().cancel_pending_io().unwrap();

    let started = Instant::now();
    assert_eq!(
        pipe.connect_client_for_test().unwrap_err().code(),
        "authority_pipe_stopping"
    );
    assert!(started.elapsed() < Duration::from_secs(1));
}

#[test]
fn bounded_server_write_reaches_an_active_reader_without_a_kernel_drain() {
    let name = unique_test_pipe_name();
    let mut pipe = create_pipe_with_sddl(&name, TEST_PIPE_SDDL).unwrap();
    let client = open_test_client(&name).unwrap();
    pipe.connect_client_for_test().unwrap();
    let payload = b"bounded-authority-response";
    let reader = std::thread::spawn(move || {
        let mut client = File::from(client);
        let mut received = vec![0u8; payload.len()];
        client.read_exact(&mut received).unwrap();
        received
    });

    pipe.write_all(payload).unwrap();
    pipe.flush().unwrap();
    assert_eq!(reader.join().unwrap(), payload);
}

#[test]
fn current_process_cannot_obtain_identity_through_production_policy() {
    let name = unique_test_pipe_name();
    let pipe = create_pipe_with_sddl(&name, TEST_PIPE_SDDL).unwrap();
    let _client = open_test_client(&name).unwrap();
    let layout = layout();
    let mut launch = receipt(&layout, GENERATION);
    launch.process_id = std::process::id();
    launch.session_id = current_process_session_id().unwrap();
    let policy = AuthorityPeerPolicy::for_installed_generation(&layout, launch).unwrap();
    assert_eq!(
        pipe.accept_peer(&policy).unwrap_err().code(),
        "authority_peer_running_image_binding_backend_disabled"
    );
}

#[test]
fn installed_policy_reads_and_holds_the_connected_process_but_rejects_the_service_itself() {
    let name = unique_test_pipe_name();
    let pipe = create_pipe_with_sddl(&name, TEST_PIPE_SDDL).unwrap();
    let _client = open_test_client(&name).unwrap();
    let policy = current_process_installed_policy_for_test().unwrap();
    assert_eq!(
        pipe.accept_installed_controller(policy).unwrap_err().code(),
        "authority_controller_process_is_service"
    );
}

#[test]
fn installed_source_projection_rejects_missing_service_identity() {
    for (service_process_id, service_process_started_at) in
        [(0, SERVICE_STARTED_AT), (SERVICE_PROCESS_ID, 0)]
    {
        assert_eq!(
            InstalledControllerPolicy::for_test(
                GENERATION,
                service_process_id,
                service_process_started_at,
                installed_controller_path(),
                CONTROLLER,
                file_identity(),
                INSTALLED_LAYOUT,
                FINAL_COMMIT_RECEIPT,
                CONTROLLER_SOURCE_BINDING,
            )
            .unwrap_err()
            .code(),
            "authority_installed_controller_source_identity_invalid"
        );
    }
}

#[test]
fn runtime_broker_source_projection_rejects_loose_or_ambiguous_identity() {
    let path = installed_runtime_broker_path();
    let identity = file_identity();
    for (service_process_id, service_process_started_at) in
        [(0, SERVICE_STARTED_AT), (SERVICE_PROCESS_ID, 0)]
    {
        assert_eq!(
            InstalledRuntimeBrokerPolicy::for_test(
                GENERATION,
                service_process_id,
                service_process_started_at,
                path.clone(),
                INSTALL_HELPER,
                identity,
                INSTALLED_LAYOUT,
                FINAL_COMMIT_RECEIPT,
                INSTALL_HELPER_SOURCE_BINDING,
            )
            .unwrap_err()
            .code(),
            "authority_installed_runtime_broker_source_identity_invalid"
        );
    }

    for invalid_path in [
        PathBuf::from("relative-helper.exe"),
        PathBuf::from(r"C:\Program Files\VRCForge\..\runtime-broker.exe"),
    ] {
        assert_eq!(
            InstalledRuntimeBrokerPolicy::for_test(
                GENERATION,
                SERVICE_PROCESS_ID,
                SERVICE_STARTED_AT,
                invalid_path,
                INSTALL_HELPER,
                identity,
                INSTALLED_LAYOUT,
                FINAL_COMMIT_RECEIPT,
                INSTALL_HELPER_SOURCE_BINDING,
            )
            .unwrap_err()
            .code(),
            "authority_installed_runtime_broker_source_path_invalid"
        );
    }

    for (generation, helper, installed_layout, final_commit, source_binding) in [
        (
            [0; 32],
            INSTALL_HELPER,
            INSTALLED_LAYOUT,
            FINAL_COMMIT_RECEIPT,
            INSTALL_HELPER_SOURCE_BINDING,
        ),
        (
            GENERATION,
            [0; 32],
            INSTALLED_LAYOUT,
            FINAL_COMMIT_RECEIPT,
            INSTALL_HELPER_SOURCE_BINDING,
        ),
        (
            GENERATION,
            INSTALL_HELPER,
            [0; 32],
            FINAL_COMMIT_RECEIPT,
            INSTALL_HELPER_SOURCE_BINDING,
        ),
        (
            GENERATION,
            INSTALL_HELPER,
            INSTALLED_LAYOUT,
            [0; 32],
            INSTALL_HELPER_SOURCE_BINDING,
        ),
        (
            GENERATION,
            INSTALL_HELPER,
            INSTALLED_LAYOUT,
            FINAL_COMMIT_RECEIPT,
            [0; 32],
        ),
    ] {
        assert_eq!(
            InstalledRuntimeBrokerPolicy::for_test(
                generation,
                SERVICE_PROCESS_ID,
                SERVICE_STARTED_AT,
                path.clone(),
                helper,
                identity,
                installed_layout,
                final_commit,
                source_binding,
            )
            .unwrap_err()
            .code(),
            "authority_installed_runtime_broker_source_digest_invalid"
        );
    }

    for invalid_identity in [
        StableFileIdentity {
            file_index: 0,
            ..identity
        },
        StableFileIdentity {
            size: 0,
            ..identity
        },
        StableFileIdentity {
            link_count: 2,
            ..identity
        },
    ] {
        assert_eq!(
            InstalledRuntimeBrokerPolicy::for_test(
                GENERATION,
                SERVICE_PROCESS_ID,
                SERVICE_STARTED_AT,
                path.clone(),
                INSTALL_HELPER,
                invalid_identity,
                INSTALLED_LAYOUT,
                FINAL_COMMIT_RECEIPT,
                INSTALL_HELPER_SOURCE_BINDING,
            )
            .unwrap_err()
            .code(),
            "authority_installed_runtime_broker_source_identity_invalid"
        );
    }
}

#[test]
fn runtime_broker_facts_reject_privilege_source_session_and_epoch_drift() {
    let policy = installed_runtime_broker_policy();
    let install_helper_path = installed_runtime_broker_path();
    let wrong_path = PathBuf::from(r"C:\runtime-broker-copy.exe");
    let baseline = installed_runtime_broker_facts(&install_helper_path);
    validate_installed_runtime_broker_facts_for_test(&policy, &baseline).unwrap();

    let cases = [
        (
            AuthorityPeerFacts {
                elevated: false,
                ..baseline
            },
            "authority_peer_not_elevated",
        ),
        (
            AuthorityPeerFacts {
                high_integrity: false,
                ..baseline
            },
            "authority_peer_integrity_too_low",
        ),
        (
            AuthorityPeerFacts {
                administrators_member: false,
                ..baseline
            },
            "authority_peer_not_administrator",
        ),
        (
            AuthorityPeerFacts {
                process_id: 0,
                ..baseline
            },
            "authority_peer_process_receipt_invalid",
        ),
        (
            AuthorityPeerFacts {
                process_creation_time: 0,
                ..baseline
            },
            "authority_peer_process_receipt_invalid",
        ),
        (
            AuthorityPeerFacts {
                process_id: SERVICE_PROCESS_ID,
                ..baseline
            },
            "authority_runtime_broker_process_is_service",
        ),
        (
            AuthorityPeerFacts {
                process_creation_time: SERVICE_STARTED_AT,
                ..baseline
            },
            "authority_runtime_broker_did_not_precede_service",
        ),
        (
            AuthorityPeerFacts {
                process_creation_time: SERVICE_STARTED_AT + 1,
                ..baseline
            },
            "authority_runtime_broker_did_not_precede_service",
        ),
        (
            AuthorityPeerFacts {
                token_session_id: baseline.pipe_session_id + 1,
                ..baseline
            },
            "authority_peer_session_mismatch",
        ),
        (
            AuthorityPeerFacts {
                running_process_handle_bound: false,
                ..baseline
            },
            "authority_peer_process_handle_unbound",
        ),
        (
            AuthorityPeerFacts {
                running_image_object_bound: false,
                ..baseline
            },
            "authority_peer_running_image_object_unbound",
        ),
        (
            AuthorityPeerFacts {
                controller_path: &wrong_path,
                ..baseline
            },
            "authority_runtime_broker_path_mismatch",
        ),
        (
            AuthorityPeerFacts {
                controller_sha256: [0x56; 32],
                ..baseline
            },
            "authority_runtime_broker_digest_mismatch",
        ),
        (
            AuthorityPeerFacts {
                running_image_file_identity: StableFileIdentity {
                    volume_serial_number: baseline.running_image_file_identity.volume_serial_number
                        + 1,
                    ..baseline.running_image_file_identity
                },
                ..baseline
            },
            "authority_runtime_broker_image_identity_mismatch",
        ),
        (
            AuthorityPeerFacts {
                running_image_file_identity: StableFileIdentity {
                    file_index: baseline.running_image_file_identity.file_index + 1,
                    ..baseline.running_image_file_identity
                },
                ..baseline
            },
            "authority_runtime_broker_image_identity_mismatch",
        ),
        (
            AuthorityPeerFacts {
                running_image_file_identity: StableFileIdentity {
                    size: baseline.running_image_file_identity.size + 1,
                    ..baseline.running_image_file_identity
                },
                ..baseline
            },
            "authority_runtime_broker_image_identity_mismatch",
        ),
        (
            AuthorityPeerFacts {
                running_image_file_identity: StableFileIdentity {
                    creation_time: 0,
                    ..baseline.running_image_file_identity
                },
                ..baseline
            },
            "authority_runtime_broker_image_identity_mismatch",
        ),
        (
            AuthorityPeerFacts {
                running_image_file_identity: StableFileIdentity {
                    link_count: 2,
                    ..baseline.running_image_file_identity
                },
                ..baseline
            },
            "authority_runtime_broker_image_identity_mismatch",
        ),
    ];
    for (observed, expected_code) in cases {
        assert_eq!(
            validate_installed_runtime_broker_facts_for_test(&policy, &observed)
                .unwrap_err()
                .code(),
            expected_code
        );
    }
}

#[test]
fn runtime_broker_identity_binds_source_process_session_and_file_object() {
    let policy = installed_runtime_broker_policy();
    let install_helper_path = installed_runtime_broker_path();
    let baseline = installed_runtime_broker_facts(&install_helper_path);
    let expected = installed_runtime_broker_identity_for_test(&policy, &baseline).unwrap();

    for drifted in [
        AuthorityPeerFacts {
            process_id: baseline.process_id + 1,
            ..baseline
        },
        AuthorityPeerFacts {
            process_creation_time: baseline.process_creation_time - 1,
            ..baseline
        },
        AuthorityPeerFacts {
            pipe_session_id: baseline.pipe_session_id + 1,
            token_session_id: baseline.token_session_id + 1,
            ..baseline
        },
    ] {
        assert_ne!(
            installed_runtime_broker_identity_for_test(&policy, &drifted).unwrap(),
            expected
        );
    }

    let replacement = AuthorityPeerFacts {
        running_image_file_identity: StableFileIdentity {
            file_index: baseline.running_image_file_identity.file_index + 1,
            ..baseline.running_image_file_identity
        },
        ..baseline
    };
    assert_eq!(
        installed_runtime_broker_identity_for_test(&policy, &replacement)
            .unwrap_err()
            .code(),
        "authority_runtime_broker_image_identity_mismatch"
    );
}

#[test]
fn runtime_broker_registry_is_one_use_bounded_and_never_evicts() {
    let mut registry = RuntimeBrokerRegistryHarness::new();
    let capacity = registry.capacity();
    for index in 0..capacity {
        let mut identity = [0u8; 32];
        identity[..8].copy_from_slice(&(index as u64).to_be_bytes());
        identity[8] = 0xa5;
        registry.claim(identity).unwrap();
    }
    assert_eq!(registry.len(), capacity);

    let mut replay = [0u8; 32];
    replay[8] = 0xa5;
    assert_eq!(
        registry.claim(replay).unwrap_err().code(),
        "authority_runtime_broker_admission_replayed"
    );

    let mut new_identity = [0u8; 32];
    new_identity[..8].copy_from_slice(&(capacity as u64).to_be_bytes());
    new_identity[8] = 0xa5;
    assert_eq!(
        registry.claim(new_identity).unwrap_err().code(),
        "authority_runtime_broker_registry_exhausted"
    );
    assert_eq!(registry.len(), capacity);
}

#[test]
fn runtime_broker_capability_drop_burns_its_non_clone_admission() {
    let drifted = RuntimeBrokerAdmissionDropHarness::new();
    assert_eq!(
        drifted.fail_revalidation().unwrap_err().code(),
        "authority_runtime_broker_test_peer_drift"
    );
    assert_eq!(drifted.state(), RuntimeBrokerAdmissionState::Burned);
    assert_eq!(
        drifted.revalidate_after_burn().unwrap_err().code(),
        "authority_runtime_broker_capability_burned"
    );

    let mut capability = RuntimeBrokerAdmissionDropHarness::new();
    assert_eq!(
        capability.state(),
        RuntimeBrokerAdmissionState::Authenticated
    );
    capability.drop_capability();
    assert_eq!(capability.state(), RuntimeBrokerAdmissionState::Burned);

    let source = include_str!("../primitive_evidence_authority_pipe.rs");
    let capability = source
        .split("pub struct AuthenticatedRuntimeBrokerCapability")
        .nth(1)
        .unwrap()
        .split("impl fmt::Debug for AuthenticatedRuntimeBrokerCapability")
        .next()
        .unwrap();
    assert!(capability.contains("admission: RuntimeBrokerAdmissionLease"));
    assert!(!capability.contains("#[derive(Clone"));
    assert!(!capability.contains("impl Clone"));
    assert!(source.contains("impl Drop for RuntimeBrokerAdmissionLease"));
}

#[test]
fn installed_source_policies_retain_non_clone_final_commit_leases() {
    let pipe = include_str!("../primitive_evidence_authority_pipe.rs");
    let bootstrap = include_str!("../primitive_evidence_authority_install/bootstrap.rs");
    let native = include_str!("../primitive_evidence_authority_install/bootstrap_windows.rs");

    let controller_policy = pipe
        .split("struct InstalledControllerSourcePolicy")
        .nth(1)
        .unwrap()
        .split("pub struct InstalledControllerPolicy")
        .next()
        .unwrap();
    assert!(controller_policy.contains("source_lease: InstalledControllerSourceLease"));
    assert!(controller_policy.contains("verify_still_stable()"));
    let broker_policy = pipe
        .split("struct InstalledRuntimeBrokerSourcePolicy")
        .nth(1)
        .unwrap()
        .split("pub struct InstalledRuntimeBrokerPolicy")
        .next()
        .unwrap();
    assert!(broker_policy.contains("source_lease: InstalledRuntimeBrokerSourceLease"));
    assert!(broker_policy.contains("verify_still_stable()"));
    assert!(pipe.contains(
        "InstalledControllerSourceLease::Authenticated(source) => source\n                    .verify_still_stable()"
    ));
    assert!(pipe.contains(
        "InstalledRuntimeBrokerSourceLease::Authenticated(source) => source\n                    .verify_still_stable()"
    ));

    for type_name in [
        "AuthenticatedControllerSourceReadback",
        "AuthenticatedInstallHelperSourceReadback",
    ] {
        let declaration = bootstrap
            .split(&format!("pub(crate) struct {type_name}"))
            .next()
            .unwrap();
        let tail = declaration
            .rsplit('\n')
            .take(8)
            .collect::<Vec<_>>()
            .join("\n");
        assert!(!tail.contains("derive(Debug, Clone"));
    }
    assert!(native.contains("struct NativeAuthenticatedControllerSourceLease"));
    assert!(native.contains("struct NativeAuthenticatedInstallHelperSourceLease"));
    assert!(
        native
            .matches("security: NativeAuthenticatedSourceLeaseSecurity::SealedBinary")
            .count()
            >= 2
    );
}

#[test]
fn runtime_broker_running_image_observation_rejects_reparse_objects() {
    let source = include_str!("../primitive_evidence_authority_pipe.rs");
    let file_identity = source
        .split("fn query_file_identity(")
        .nth(1)
        .unwrap()
        .split("pub(super) fn current_process_session_id")
        .next()
        .unwrap();
    assert!(file_identity.contains("FILE_ATTRIBUTE_REPARSE_POINT"));
    assert!(file_identity.contains("FILE_ATTRIBUTE_DIRECTORY"));
    assert!(file_identity.contains("authority_peer_controller_file_type_invalid"));
}

#[test]
fn installed_controller_facts_reject_privilege_source_and_service_epoch_drift() {
    let policy = installed_policy();
    let controller_path = installed_controller_path();
    let wrong_path = PathBuf::from(r"C:\controller-copy.exe");
    let baseline = installed_facts(&controller_path);
    validate_installed_controller_facts_for_test(&policy, &baseline).unwrap();

    let cases = [
        (
            AuthorityPeerFacts {
                elevated: false,
                ..baseline
            },
            "authority_peer_not_elevated",
        ),
        (
            AuthorityPeerFacts {
                high_integrity: false,
                ..baseline
            },
            "authority_peer_integrity_too_low",
        ),
        (
            AuthorityPeerFacts {
                administrators_member: false,
                ..baseline
            },
            "authority_peer_not_administrator",
        ),
        (
            AuthorityPeerFacts {
                process_id: 0,
                ..baseline
            },
            "authority_peer_process_receipt_invalid",
        ),
        (
            AuthorityPeerFacts {
                process_id: SERVICE_PROCESS_ID,
                ..baseline
            },
            "authority_controller_process_is_service",
        ),
        (
            AuthorityPeerFacts {
                process_creation_time: SERVICE_STARTED_AT,
                ..baseline
            },
            "authority_controller_predates_service",
        ),
        (
            AuthorityPeerFacts {
                process_creation_time: SERVICE_STARTED_AT - 1,
                ..baseline
            },
            "authority_controller_predates_service",
        ),
        (
            AuthorityPeerFacts {
                token_session_id: baseline.pipe_session_id + 1,
                ..baseline
            },
            "authority_peer_session_mismatch",
        ),
        (
            AuthorityPeerFacts {
                running_process_handle_bound: false,
                ..baseline
            },
            "authority_peer_process_handle_unbound",
        ),
        (
            AuthorityPeerFacts {
                running_image_object_bound: false,
                ..baseline
            },
            "authority_peer_running_image_object_unbound",
        ),
        (
            AuthorityPeerFacts {
                controller_path: &wrong_path,
                ..baseline
            },
            "authority_peer_controller_path_mismatch",
        ),
        (
            AuthorityPeerFacts {
                controller_sha256: [0x43; 32],
                ..baseline
            },
            "authority_peer_controller_digest_mismatch",
        ),
        (
            AuthorityPeerFacts {
                running_image_file_identity: StableFileIdentity {
                    volume_serial_number: baseline.running_image_file_identity.volume_serial_number
                        + 1,
                    ..baseline.running_image_file_identity
                },
                ..baseline
            },
            "authority_peer_running_image_identity_mismatch",
        ),
        (
            AuthorityPeerFacts {
                running_image_file_identity: StableFileIdentity {
                    file_index: baseline.running_image_file_identity.file_index + 1,
                    ..baseline.running_image_file_identity
                },
                ..baseline
            },
            "authority_peer_running_image_identity_mismatch",
        ),
        (
            AuthorityPeerFacts {
                running_image_file_identity: StableFileIdentity {
                    size: baseline.running_image_file_identity.size + 1,
                    ..baseline.running_image_file_identity
                },
                ..baseline
            },
            "authority_peer_running_image_identity_mismatch",
        ),
        (
            AuthorityPeerFacts {
                running_image_file_identity: StableFileIdentity {
                    creation_time: 0,
                    ..baseline.running_image_file_identity
                },
                ..baseline
            },
            "authority_peer_running_image_identity_mismatch",
        ),
        (
            AuthorityPeerFacts {
                running_image_file_identity: StableFileIdentity {
                    link_count: 2,
                    ..baseline.running_image_file_identity
                },
                ..baseline
            },
            "authority_peer_running_image_identity_mismatch",
        ),
    ];
    for (observed, expected_code) in cases {
        assert_eq!(
            validate_installed_controller_facts_for_test(&policy, &observed)
                .unwrap_err()
                .code(),
            expected_code
        );
    }
}

#[test]
fn launch_receipt_binds_first_command_while_replay_key_stays_command_independent() {
    let policy = installed_policy();
    let controller_path = installed_controller_path();
    let facts = installed_facts(&controller_path);
    let status = InstalledControllerCommandIntent::status();
    let self_test = InstalledControllerCommandIntent::self_test();
    let (status_receipt, status_launch_identity) =
        installed_launch_receipt_sha256_for_test(&policy, &facts, &status).unwrap();
    let (self_test_receipt, self_test_launch_identity) =
        installed_launch_receipt_sha256_for_test(&policy, &facts, &self_test).unwrap();
    assert_ne!(status_receipt, self_test_receipt);
    assert_eq!(status_launch_identity, self_test_launch_identity);

    let _first_pipe = create_pipe_with_sddl(&unique_test_pipe_name(), TEST_PIPE_SDDL).unwrap();
    let _second_pipe = create_pipe_with_sddl(&unique_test_pipe_name(), TEST_PIPE_SDDL).unwrap();
    claim_installed_launch_for_test(status_launch_identity).unwrap();
    assert_eq!(
        claim_installed_launch_for_test(self_test_launch_identity)
            .unwrap_err()
            .code(),
        "authority_controller_launch_replayed"
    );

    let later_facts = AuthorityPeerFacts {
        process_creation_time: facts.process_creation_time + 1,
        ..facts
    };
    let (_, later_launch_identity) =
        installed_launch_receipt_sha256_for_test(&policy, &later_facts, &status).unwrap();
    assert_ne!(later_launch_identity, status_launch_identity);
    claim_installed_launch_for_test(later_launch_identity).unwrap();
}

#[test]
fn launch_registry_is_fixed_capacity_and_fails_closed_without_eviction() {
    let mut registry = ControllerLaunchRegistryHarness::new();
    let capacity = registry.capacity();
    for index in 0..capacity {
        let mut identity = [0u8; 32];
        identity[..8].copy_from_slice(&(index as u64).to_be_bytes());
        identity[8] = 0x5a;
        registry.claim(identity).unwrap();
    }
    assert_eq!(registry.len(), capacity);

    let mut replay = [0u8; 32];
    replay[..8].copy_from_slice(&0u64.to_be_bytes());
    replay[8] = 0x5a;
    assert_eq!(
        registry.claim(replay).unwrap_err().code(),
        "authority_controller_launch_replayed"
    );

    let mut new_identity = [0u8; 32];
    new_identity[..8].copy_from_slice(&(capacity as u64).to_be_bytes());
    new_identity[8] = 0x5a;
    assert_eq!(
        registry.claim(new_identity).unwrap_err().code(),
        "authority_controller_launch_registry_exhausted"
    );
    assert_eq!(registry.len(), capacity);
}

#[test]
fn cancellation_and_disconnect_failures_preserve_the_primary_error_and_shape() {
    cancel_disconnect_result_for_test(None, None).unwrap();

    let cancel = cancel_disconnect_result_for_test(Some(111), None).unwrap_err();
    assert_eq!(cancel.code(), "authority_pipe_cancel_failed");
    assert_eq!(cancel.win32(), Some(111));

    let disconnect = cancel_disconnect_result_for_test(None, Some(222)).unwrap_err();
    assert_eq!(disconnect.code(), "authority_pipe_disconnect_failed");
    assert_eq!(disconnect.win32(), Some(222));

    let combined = cancel_disconnect_result_for_test(Some(333), Some(444)).unwrap_err();
    assert_eq!(
        combined.code(),
        "authority_pipe_cancel_and_disconnect_failed"
    );
    assert_eq!(combined.win32(), Some(333));
}

#[test]
fn installed_server_transport_has_no_unbounded_kernel_drain_call() {
    let source = include_str!("../primitive_evidence_authority_pipe.rs");
    let forbidden_call = ["Flush", "File", "Buffers", "("].concat();
    assert!(!source.contains(&forbidden_call));
    assert!(source.contains("FILE_FLAG_OVERLAPPED"));
    assert!(source.contains("PIPE_CANCEL_SETTLE_TIMEOUT_MS"));
}

#[test]
fn first_parsed_command_is_bound_once_and_only_then_exposes_a_receipt() {
    let launch = InstalledControllerLaunchHarness::new();
    assert_eq!(launch.launch_state(), ControllerLaunchState::Authenticated);
    assert_eq!(
        launch.scenario_handle_state(),
        ScenarioHandleBundleState::Pending
    );
    assert_eq!(launch.launch_receipt_sha256(), None);

    launch
        .admit_command(&InstalledControllerCommandIntent::status())
        .unwrap();
    assert_eq!(launch.launch_state(), ControllerLaunchState::Consumed);
    assert_eq!(
        launch.scenario_handle_state(),
        ScenarioHandleBundleState::Burned
    );
    assert!(launch
        .launch_receipt_sha256()
        .is_some_and(|digest| digest.iter().any(|byte| *byte != 0)));
    assert_eq!(
        launch
            .admit_command(&InstalledControllerCommandIntent::self_test())
            .unwrap_err()
            .code(),
        "authority_controller_launch_already_consumed"
    );
}

#[test]
fn invalid_or_misrouted_first_command_burns_the_launch_and_handle_slot() {
    let invalid = InstalledControllerCommandIntent::Cancel {
        request_id: "bad request".to_string(),
    };
    let invalid_launch = InstalledControllerLaunchHarness::new();
    assert_eq!(
        invalid_launch.admit_command(&invalid).unwrap_err().code(),
        "authority_controller_request_id_invalid"
    );
    assert_eq!(invalid_launch.launch_state(), ControllerLaunchState::Burned);
    assert_eq!(
        invalid_launch.scenario_handle_state(),
        ScenarioHandleBundleState::Burned
    );
    assert_eq!(invalid_launch.launch_receipt_sha256(), None);

    let run = InstalledControllerCommandIntent::run_model_part_composition("request-1").unwrap();
    let misrouted_launch = InstalledControllerLaunchHarness::new();
    assert_eq!(
        misrouted_launch.admit_command(&run).unwrap_err().code(),
        "authority_controller_command_handles_required"
    );
    assert_eq!(
        misrouted_launch.launch_state(),
        ControllerLaunchState::Burned
    );
    assert_eq!(
        misrouted_launch.scenario_handle_state(),
        ScenarioHandleBundleState::Burned
    );
}

#[test]
fn model_part_command_owns_exactly_one_fixed_role_bundle_until_active_drop() {
    let (root, paths, source_files, tokens) = fixed_handle_fixture("installed-command");
    let command =
        InstalledControllerCommandIntent::run_model_part_composition("request-roles").unwrap();
    let launch = InstalledControllerLaunchHarness::new();
    let external = launch.admit_model_part(&command, tokens).unwrap();
    let active = compose_fixed_handle_fixture(&source_files, external);
    assert_eq!(launch.launch_state(), ControllerLaunchState::Consumed);
    assert_eq!(
        launch.scenario_handle_state(),
        ScenarioHandleBundleState::Active
    );
    assert!(launch.launch_receipt_sha256().is_some());
    for (index, file) in active.files().into_iter().enumerate() {
        let mut byte = [0u8; 1];
        assert_eq!(file.seek_read(&mut byte, 0).unwrap(), 1);
        assert_eq!(byte[0], index as u8 + 1);
    }
    assert_eq!(
        launch
            .admit_model_part(&command, tokens)
            .unwrap_err()
            .code(),
        "authority_controller_launch_already_consumed"
    );
    drop(source_files);
    assert!(fs::remove_file(&paths[0]).is_err());
    drop(active);
    assert_eq!(
        launch.scenario_handle_state(),
        ScenarioHandleBundleState::Burned
    );
    remove_fixed_handle_fixture(&root, &paths);
}

#[test]
fn command_intent_constructors_enforce_the_protocol_request_id_grammar() {
    for valid in [
        "a".to_string(),
        "A-1_b.c:d".to_string(),
        "z".repeat(MAX_REQUEST_ID_BYTES),
    ] {
        InstalledControllerCommandIntent::cancel(valid.as_str()).unwrap();
        InstalledControllerCommandIntent::get_result(valid.as_str()).unwrap();
        InstalledControllerCommandIntent::run_model_part_composition(valid.as_str()).unwrap();
    }
    for invalid in [
        "".to_string(),
        "-leading".to_string(),
        "has space".to_string(),
        "unicode-猫".to_string(),
        "z".repeat(MAX_REQUEST_ID_BYTES + 1),
    ] {
        assert_eq!(
            InstalledControllerCommandIntent::cancel(invalid)
                .unwrap_err()
                .code(),
            "authority_controller_request_id_invalid"
        );
    }
}

#[test]
fn self_test_is_non_mutating_and_does_not_need_elevation() {
    run_non_mutating_self_test().unwrap();
}

#[test]
fn exited_process_with_still_active_exit_code_is_not_live() {
    let mut child = std::process::Command::new("cmd.exe")
        .args(["/C", "exit", "259"])
        .spawn()
        .unwrap();
    let status = child.wait().unwrap();
    assert_eq!(status.code(), Some(259));
    assert!(!process_is_active(child.as_raw_handle().cast()).unwrap());
}
