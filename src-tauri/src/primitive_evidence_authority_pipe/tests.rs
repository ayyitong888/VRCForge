use super::windows::{
    create_pipe_with_sddl, current_process_session_id, open_test_client, process_is_active,
    unique_test_pipe_name, SecurityDescriptor, TEST_PIPE_SDDL,
};
use super::*;
use std::os::windows::io::AsRawHandle;

const GENERATION: [u8; 32] = [0x24; 32];
const CONTROLLER: [u8; 32] = [0x42; 32];
const LAUNCH_RECEIPT: [u8; 32] = [0x64; 32];

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
fn first_instance_flag_prevents_a_second_real_server() {
    let name = unique_test_pipe_name();
    let _first = create_pipe_with_sddl(&name, TEST_PIPE_SDDL).unwrap();
    let error = create_pipe_with_sddl(&name, TEST_PIPE_SDDL).unwrap_err();
    assert_eq!(error.code(), "authority_pipe_create_failed");
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
