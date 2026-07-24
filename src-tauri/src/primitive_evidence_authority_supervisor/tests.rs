use super::*;

fn digest(seed: u8) -> Digest {
    [seed; 32]
}

fn file_identity(seed: u8) -> FileIdentity {
    FileIdentity {
        volume_serial: u64::from(seed) + 1,
        file_id: [seed; 16],
    }
}

fn process_key(index: u32) -> ProcessKey {
    ProcessKey {
        pid: 100 + index,
        creation_time: 1_000 + u64::from(index),
    }
}

fn policy() -> SupervisorPolicy {
    let mut policy = SupervisorPolicy {
        authority_identity_digest: digest(9),
        ticket_digest: digest(1),
        run_binding_digest: [0; 32],
        service_instance_digest: digest(8),
        runner_policy_digest: [0; 32],
        issued_at: 10,
        deadline: 1_000,
        authority_process: process_key(0),
        authority_parent_process: ProcessKey {
            pid: 90,
            creation_time: 900,
        },
        process_executable_digests: [
            digest(10),
            digest(11),
            digest(12),
            digest(13),
            digest(14),
            digest(15),
            digest(16),
        ],
        runner_identity_digest: digest(20),
        runner_account_digest: digest(21),
        runner_profile_digest: digest(22),
        inherited_handle_allowlist_digest: digest(23),
        deterministic_job_name_digest: digest(24),
        private_root_binding_digest: digest(25),
        job_object_id: 500,
        artifacts: vec![
            ArtifactExpectation {
                binding_digest: digest(30),
                direction: ArtifactDirection::Input,
                expected_content_digest: Some(digest(31)),
            },
            ArtifactExpectation {
                binding_digest: digest(32),
                direction: ArtifactDirection::Output,
                expected_content_digest: None,
            },
        ],
        socket_policies: vec![
            SocketPolicy {
                role: SocketRole::App,
                mode: SocketEndpointMode::FixedFixture,
                local_port: APP_LOOPBACK_PORT,
                owner_role: ProcessRole::Backend,
                driver_binding_digest: digest(40),
            },
            SocketPolicy {
                role: SocketRole::Bridge,
                mode: SocketEndpointMode::ServiceSelectedPrivate,
                local_port: 55_080,
                owner_role: ProcessRole::BridgeListener,
                driver_binding_digest: digest(41),
            },
        ],
        helper_policies: Vec::new(),
    };
    rebind_policy(&mut policy);
    policy
}

fn rebind_policy(policy: &mut SupervisorPolicy) {
    policy.runner_policy_digest = canonical_supervisor_policy_digest(policy);
    policy.run_binding_digest = derive_run_binding_digest(
        &policy.authority_identity_digest,
        &policy.ticket_digest,
        &policy.service_instance_digest,
        &policy.runner_policy_digest,
    );
}

fn process(
    policy: &SupervisorPolicy,
    role: ProcessRole,
    parent_pid: u32,
    parent_creation_time: u64,
    supervisor_pid: u32,
    started_at: u64,
) -> ProcessObservation {
    let index = role_index(role);
    let image_identity = file_identity(50 + index as u8);
    ProcessObservation {
        role,
        key: process_key(index as u32),
        parent_pid,
        parent_creation_time,
        supervisor_pid,
        started_at,
        executable_digest: policy.process_executable_digests[index],
        executable_digest_read_from_image_handle: true,
        image_handle_identity: image_identity,
        image_path_identity_at_terminal: image_identity,
        runner_identity_digest: role.is_candidate().then_some(policy.runner_identity_digest),
        job_object_id: role.is_candidate().then_some(policy.job_object_id),
        job_member: role.is_candidate(),
        breakaway_allowed: false,
        image_handle_held_through_terminal: true,
        process_handle_held_through_cleanup_begin: true,
        alive_at_finalization: role.is_candidate(),
    }
}

fn socket_verifications(
    policy: &SupervisorPolicy,
    role: SocketRole,
    owner: ProcessKey,
    owner_image_identity: FileIdentity,
    ready_at: u64,
) -> Vec<SocketVerificationObservation> {
    let owner_role = role.expected_owner();
    let times = [ready_at, ready_at + 5, ready_at + 10, 112, 115];
    SOCKET_VERIFICATION_PHASES
        .iter()
        .zip(times)
        .map(|(phase, observed_at)| SocketVerificationObservation {
            phase: *phase,
            observed_at,
            owner,
            owner_job_object_id: policy.job_object_id,
            owner_executable_digest: policy.process_executable_digests[role_index(owner_role)],
            owner_image_identity,
            listening: true,
            exclusive_address_use: true,
            address_reuse_disabled: true,
        })
        .collect()
}

fn completed_observation(policy: &SupervisorPolicy) -> AuthorityOwnedRunObservation {
    let canonical_result_bytes = b"canonical-result".to_vec();
    let canonical_result_digest: Digest = Sha256::digest(&canonical_result_bytes).into();
    let processes = vec![
        process(
            policy,
            ProcessRole::AuthorityService,
            policy.authority_parent_process.pid,
            policy.authority_parent_process.creation_time,
            0,
            5,
        ),
        process(policy, ProcessRole::Driver, 100, 1_000, 100, 32),
        process(policy, ProcessRole::Desktop, 101, 1_001, 101, 40),
        process(policy, ProcessRole::Backend, 102, 1_002, 102, 50),
        process(policy, ProcessRole::Unity, 101, 1_001, 101, 45),
        process(policy, ProcessRole::BridgeLauncher, 104, 1_004, 104, 60),
        process(policy, ProcessRole::BridgeListener, 105, 1_005, 105, 70),
    ];
    let candidate_keys = CANDIDATE_ROLES
        .iter()
        .map(|role| processes[role_index(*role)].key)
        .collect::<Vec<_>>();
    AuthorityOwnedRunObservation {
        ticket_consumed_at: 15,
        runner: RunnerIdentityObservation {
            identity_digest: policy.runner_identity_digest,
            account_digest: policy.runner_account_digest,
            profile_digest: policy.runner_profile_digest,
            validated_at: 16,
            dedicated_account: true,
            restricted_token: true,
            batch_logon: true,
            administrator_member: false,
            elevated: false,
            interactive_user_identity: false,
            service_identity: false,
            network_credentials_present: false,
            service_owned_profile: true,
            profile_is_reparse_point: false,
        },
        artifacts: vec![
            StableArtifactObservation {
                binding_digest: digest(30),
                direction: ArtifactDirection::Input,
                created_at: 17,
                service_handle_id: 600,
                candidate_handle_id: 700,
                source_identity: Some(file_identity(1)),
                private_identity: file_identity(2),
                candidate_handle_identity: file_identity(2),
                path_identity_at_terminal: file_identity(2),
                content_digest: digest(31),
                content_length: 128,
                content_digest_read_from_service_handle: true,
                created_new_private_copy: true,
                service_owned_parent: true,
                parent_is_reparse_point: false,
                candidate_handle_explicitly_inherited: true,
                service_handle_held_through_terminal: true,
            },
            StableArtifactObservation {
                binding_digest: digest(32),
                direction: ArtifactDirection::Output,
                created_at: 18,
                service_handle_id: 601,
                candidate_handle_id: 701,
                source_identity: None,
                private_identity: file_identity(3),
                candidate_handle_identity: file_identity(3),
                path_identity_at_terminal: file_identity(3),
                content_digest: canonical_result_digest,
                content_length: canonical_result_bytes.len() as u64,
                content_digest_read_from_service_handle: true,
                created_new_private_copy: true,
                service_owned_parent: true,
                parent_is_reparse_point: false,
                candidate_handle_explicitly_inherited: true,
                service_handle_held_through_terminal: true,
            },
        ],
        launches: vec![RootLaunchObservation {
            role: ProcessRole::Driver,
            created_suspended_at: 30,
            assigned_to_job_at: 31,
            resumed_at: 32,
            job_object_id: policy.job_object_id,
            runner_identity_digest: policy.runner_identity_digest,
            inherited_handle_allowlist_digest: policy.inherited_handle_allowlist_digest,
            all_other_handles_non_inheritable: true,
            breakaway_requested: false,
        }],
        processes,
        helpers: Vec::new(),
        job: JobObservation {
            object_id: policy.job_object_id,
            kill_on_job_close: true,
            breakaway_allowed: false,
            silent_breakaway_allowed: false,
            active_process_limit: 0,
            completion_port_supervised: true,
            assignment_history_complete: true,
            assigned_processes: candidate_keys.clone(),
            handle_held_through_cleanup_begin: true,
        },
        sockets: vec![
            SocketObservation {
                role: SocketRole::App,
                local_port: APP_LOOPBACK_PORT,
                prelaunch_idle_observed_at: 20,
                prelaunch_competing_owner: None,
                listener_ready_at: 80,
                listener_socket_id: 800,
                owner: process_key(3),
                owner_job_object_id: policy.job_object_id,
                owner_executable_digest: policy.process_executable_digests[3],
                owner_image_identity: file_identity(53),
                driver_binding_digest: policy.socket_policies[0].driver_binding_digest,
                loopback_v4_only: true,
                exclusive_address_use: true,
                address_reuse_disabled: true,
                ownership_verifications: socket_verifications(
                    policy,
                    SocketRole::App,
                    process_key(3),
                    file_identity(53),
                    80,
                ),
            },
            SocketObservation {
                role: SocketRole::Bridge,
                local_port: policy.socket_policies[1].local_port,
                prelaunch_idle_observed_at: 21,
                prelaunch_competing_owner: None,
                listener_ready_at: 90,
                listener_socket_id: 801,
                owner: process_key(6),
                owner_job_object_id: policy.job_object_id,
                owner_executable_digest: policy.process_executable_digests[6],
                owner_image_identity: file_identity(56),
                driver_binding_digest: policy.socket_policies[1].driver_binding_digest,
                loopback_v4_only: true,
                exclusive_address_use: true,
                address_reuse_disabled: true,
                ownership_verifications: socket_verifications(
                    policy,
                    SocketRole::Bridge,
                    process_key(6),
                    file_identity(56),
                    90,
                ),
            },
        ],
        finalization: Some(FinalizationObservation {
            source: FinalizationSource::AuthorityHeldOutputHandles,
            ticket_digest: policy.ticket_digest,
            run_binding_digest: policy.run_binding_digest,
            finalized_at: 100,
            output_binding_digests: vec![digest(32)],
            canonical_result_binding_digest: digest(32),
            canonical_result_digest,
            canonical_result_bytes,
            read_directly_from_held_handles: true,
            retained_until_cleanup_complete: true,
            caller_report_present: false,
        }),
        terminal: TerminalObservation {
            kind: TerminalKind::Completed,
            observed_at: 110,
            intent: TerminalIntent::CommitResult,
            intent_recorded_at: 111,
        },
        cleanup: CleanupObservation {
            observed_at: 120,
            exited_processes: candidate_keys,
            deleted_private_artifacts: vec![digest(30), digest(32)],
            sockets: vec![
                SocketCleanupObservation {
                    role: SocketRole::App,
                    local_port: APP_LOOPBACK_PORT,
                    closed_listener_socket_id: Some(800),
                    listener_exit_observed_at: 113,
                    exclusive_rebind_observed_at: 119,
                    exclusive_rebind_succeeded: true,
                    rebound_socket_object_id: 900,
                    competing_owner: None,
                    rebound_handle_closed: true,
                },
                SocketCleanupObservation {
                    role: SocketRole::Bridge,
                    local_port: policy.socket_policies[1].local_port,
                    closed_listener_socket_id: Some(801),
                    listener_exit_observed_at: 113,
                    exclusive_rebind_observed_at: 119,
                    exclusive_rebind_succeeded: true,
                    rebound_socket_object_id: 901,
                    competing_owner: None,
                    rebound_handle_closed: true,
                },
            ],
            job_terminated: true,
            job_handle_closed: true,
            no_live_descendants: true,
            all_process_handles_closed: true,
            all_file_handles_closed: true,
            private_root_removed: true,
            disposable_project_removed: true,
            runner_profile_removed: true,
            final_result_persisted: true,
            unknown_processes: Vec::new(),
            unknown_artifacts: Vec::new(),
            unknown_listeners: Vec::new(),
        },
    }
}

fn align_terminal_and_cleanup_times(observation: &mut AuthorityOwnedRunObservation) {
    for socket in &mut observation.sockets {
        socket.ownership_verifications[3].observed_at = observation.terminal.intent_recorded_at;
        socket.ownership_verifications[4].observed_at = observation.cleanup.observed_at;
    }
    for socket in &mut observation.cleanup.sockets {
        socket.listener_exit_observed_at = observation.terminal.intent_recorded_at;
        socket.exclusive_rebind_observed_at = observation.cleanup.observed_at;
    }
}

fn empty_abort_observation(policy: &SupervisorPolicy) -> AuthorityOwnedAbortObservation {
    let complete = completed_observation(policy);
    AuthorityOwnedAbortObservation {
        ticket_consumed_at: 15,
        runner: None,
        artifacts: Vec::new(),
        launches: Vec::new(),
        processes: vec![complete.processes[0].clone()],
        helpers: Vec::new(),
        job: Some(JobObservation {
            object_id: policy.job_object_id,
            kill_on_job_close: true,
            breakaway_allowed: false,
            silent_breakaway_allowed: false,
            active_process_limit: 0,
            completion_port_supervised: true,
            assignment_history_complete: true,
            assigned_processes: Vec::new(),
            handle_held_through_cleanup_begin: true,
        }),
        sockets: Vec::new(),
        terminal: TerminalObservation {
            kind: TerminalKind::Failed,
            observed_at: 20,
            intent: TerminalIntent::Burn,
            intent_recorded_at: 21,
        },
        cleanup: CleanupObservation {
            observed_at: 30,
            exited_processes: Vec::new(),
            deleted_private_artifacts: Vec::new(),
            sockets: policy
                .socket_policies
                .iter()
                .enumerate()
                .map(|(index, endpoint)| SocketCleanupObservation {
                    role: endpoint.role,
                    local_port: endpoint.local_port,
                    closed_listener_socket_id: None,
                    listener_exit_observed_at: 21,
                    exclusive_rebind_observed_at: 29,
                    exclusive_rebind_succeeded: true,
                    rebound_socket_object_id: 950 + index as u64,
                    competing_owner: None,
                    rebound_handle_closed: true,
                })
                .collect(),
            job_terminated: true,
            job_handle_closed: true,
            no_live_descendants: true,
            all_process_handles_closed: true,
            all_file_handles_closed: true,
            private_root_removed: true,
            disposable_project_removed: true,
            runner_profile_removed: true,
            final_result_persisted: false,
            unknown_processes: Vec::new(),
            unknown_artifacts: Vec::new(),
            unknown_listeners: Vec::new(),
        },
    }
}

fn assert_error(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedRunObservation,
    expected: &'static str,
) {
    assert_eq!(
        validate_authority_owned_run(policy, observation)
            .expect_err("observation must fail closed")
            .code(),
        expected
    );
}

#[test]
fn production_status_never_claims_the_source_contract_is_ready() {
    let status = production_readiness();
    assert!(!status.trusted_boundary_ready());
    assert_eq!(status.blockers(), PRODUCTION_BLOCKERS);
    assert!(status
        .blockers()
        .contains(&"protected_process_launch_not_implemented"));
    assert!(status
        .blockers()
        .contains(&"isolated_runner_identity_not_provisioned"));
}

#[test]
fn verified_readiness_is_bound_only_to_identity_and_live_instance() {
    let proof = VerifiedReadinessProof::for_runtime_test(digest(1), digest(2));
    assert!(proof.verifies_for(&digest(1)));
    assert!(!proof.verifies_for(&digest(4)));

    let mut tampered = proof.clone();
    tampered.service_instance_digest = digest(5);
    assert!(!tampered.verifies_for(&digest(1)));
}

#[test]
fn authority_owned_complete_run_validates() {
    let policy = policy();
    let observation = completed_observation(&policy);
    let ValidatedTerminalRun::Completed(proof) =
        validate_authority_owned_run(&policy, &observation).unwrap()
    else {
        panic!("expected completed proof");
    };
    assert_eq!(
        proof.authority_identity_digest(),
        &policy.authority_identity_digest
    );
    assert_eq!(proof.ticket_digest(), &policy.ticket_digest);
    assert_eq!(proof.run_binding_digest(), &policy.run_binding_digest);
    assert_eq!(proof.result_bytes(), b"canonical-result");
    let expected_result_digest: Digest = Sha256::digest(b"canonical-result").into();
    assert_eq!(proof.result_digest(), &expected_result_digest);
    assert!(!is_zero_digest(proof.cleanup_receipt_digest()));
    assert_eq!(proof.finalized_at(), 100);
    assert_eq!(proof.cleanup_observed_at(), 120);
}

#[test]
fn cleanup_receipt_binds_identity_ticket_run_result_and_terminal_reason() {
    let policy = policy();
    let observation = completed_observation(&policy);
    let baseline = derive_cleanup_receipt(&policy, &observation);

    let mut changed_identity = policy.clone();
    changed_identity.authority_identity_digest = digest(90);
    assert_ne!(
        baseline,
        derive_cleanup_receipt(&changed_identity, &observation)
    );

    let mut changed_ticket_policy = policy.clone();
    changed_ticket_policy.ticket_digest = digest(91);
    let mut changed_ticket_observation = observation.clone();
    changed_ticket_observation
        .finalization
        .as_mut()
        .unwrap()
        .ticket_digest = changed_ticket_policy.ticket_digest;
    assert_ne!(
        baseline,
        derive_cleanup_receipt(&changed_ticket_policy, &changed_ticket_observation)
    );

    let mut changed_run_policy = policy.clone();
    changed_run_policy.run_binding_digest = digest(92);
    let mut changed_run_observation = observation.clone();
    changed_run_observation
        .finalization
        .as_mut()
        .unwrap()
        .run_binding_digest = changed_run_policy.run_binding_digest;
    assert_ne!(
        baseline,
        derive_cleanup_receipt(&changed_run_policy, &changed_run_observation)
    );

    let mut changed_result = observation.clone();
    changed_result
        .finalization
        .as_mut()
        .unwrap()
        .canonical_result_digest = digest(93);
    assert_ne!(baseline, derive_cleanup_receipt(&policy, &changed_result));

    let mut cancelled = observation.clone();
    cancelled.finalization = None;
    cancelled.terminal.kind = TerminalKind::Cancelled;
    let cancelled_receipt = derive_cleanup_receipt(&policy, &cancelled);
    let mut timed_out = cancelled;
    timed_out.terminal.kind = TerminalKind::TimedOut;
    assert_ne!(
        cancelled_receipt,
        derive_cleanup_receipt(&policy, &timed_out)
    );
}

#[test]
fn private_copy_path_replacement_is_rejected_even_with_original_handle_digest() {
    let policy = policy();
    let mut observation = completed_observation(&policy);
    observation.artifacts[0].path_identity_at_terminal = file_identity(99);
    assert_error(&policy, &observation, "authority_private_copy_replaced");
}

#[test]
fn executable_path_replacement_is_rejected_while_process_handle_is_live() {
    let policy = policy();
    let mut observation = completed_observation(&policy);
    observation.processes[2].image_path_identity_at_terminal = file_identity(99);
    assert_error(&policy, &observation, "authority_process_image_replaced");
}

#[test]
fn short_lived_extra_job_process_is_rejected_from_assignment_history() {
    let policy = policy();
    let mut observation = completed_observation(&policy);
    observation.job.assigned_processes.push(ProcessKey {
        pid: 999,
        creation_time: 9_999,
    });
    assert_error(&policy, &observation, "authority_job_unexpected_process");
}

#[test]
fn process_graph_cannot_hide_or_reorder_a_role() {
    let policy = policy();
    let mut observation = completed_observation(&policy);
    observation.processes.swap(4, 5);
    assert_error(
        &policy,
        &observation,
        "authority_process_role_order_invalid",
    );
}

#[test]
fn driver_is_exactly_one_candidate_and_the_only_supervised_root() {
    let policy = policy();

    let mut missing = completed_observation(&policy);
    missing.processes.remove(role_index(ProcessRole::Driver));
    assert_error(&policy, &missing, "authority_process_graph_incomplete");

    let mut duplicate = completed_observation(&policy);
    let driver = duplicate.processes[role_index(ProcessRole::Driver)].clone();
    duplicate
        .processes
        .insert(role_index(ProcessRole::Driver), driver);
    assert_error(&policy, &duplicate, "authority_process_graph_incomplete");

    let mut missing_from_job = completed_observation(&policy);
    let driver_key = missing_from_job.processes[role_index(ProcessRole::Driver)].key;
    missing_from_job
        .job
        .assigned_processes
        .retain(|process| *process != driver_key);
    assert_error(
        &policy,
        &missing_from_job,
        "authority_job_unexpected_process",
    );

    for role in [ProcessRole::Desktop, ProcessRole::Unity] {
        let mut wrong_parent = completed_observation(&policy);
        let service = wrong_parent.processes[role_index(ProcessRole::AuthorityService)].key;
        let child = &mut wrong_parent.processes[role_index(role)];
        child.parent_pid = service.pid;
        child.parent_creation_time = service.creation_time;
        child.supervisor_pid = service.pid;
        assert_error(&policy, &wrong_parent, "authority_process_parent_mismatch");
    }

    let mut extra_root = completed_observation(&policy);
    extra_root.launches.push(RootLaunchObservation {
        role: ProcessRole::Desktop,
        created_suspended_at: 33,
        assigned_to_job_at: 34,
        resumed_at: 35,
        job_object_id: policy.job_object_id,
        runner_identity_digest: policy.runner_identity_digest,
        inherited_handle_allowlist_digest: policy.inherited_handle_allowlist_digest,
        all_other_handles_non_inheritable: true,
        breakaway_requested: false,
    });
    assert_error(&policy, &extra_root, "authority_root_launch_set_invalid");
}

#[test]
fn breakaway_or_unrestricted_handle_inheritance_is_rejected() {
    let policy = policy();
    let mut breakaway = completed_observation(&policy);
    breakaway.job.breakaway_allowed = true;
    assert_error(&policy, &breakaway, "authority_job_policy_mismatch");

    let mut inheritance = completed_observation(&policy);
    inheritance.launches[0].all_other_handles_non_inheritable = false;
    assert_error(&policy, &inheritance, "authority_launch_policy_mismatch");
}

#[test]
fn root_must_be_suspended_then_assigned_then_resumed() {
    let policy = policy();
    let mut observation = completed_observation(&policy);
    observation.launches[0].assigned_to_job_at = observation.launches[0].resumed_at + 1;
    assert_error(&policy, &observation, "authority_launch_sequence_invalid");
}

#[test]
fn interactive_admin_or_service_identity_cannot_be_the_runner() {
    let policy = policy();
    for mutate in [0u8, 1, 2] {
        let mut observation = completed_observation(&policy);
        match mutate {
            0 => observation.runner.interactive_user_identity = true,
            1 => observation.runner.administrator_member = true,
            2 => observation.runner.service_identity = true,
            _ => unreachable!(),
        }
        assert_error(&policy, &observation, "authority_runner_not_isolated");
    }
}

#[test]
fn caller_report_cannot_finalize_even_when_all_digests_match() {
    let policy = policy();
    let mut observation = completed_observation(&policy);
    let finalization = observation.finalization.as_mut().unwrap();
    finalization.source = FinalizationSource::CallerSuppliedReport;
    finalization.caller_report_present = true;
    assert_error(
        &policy,
        &observation,
        "authority_finalization_source_untrusted",
    );
}

#[test]
fn canonical_result_bytes_cannot_be_replaced_after_handle_validation() {
    let policy = policy();
    let mut observation = completed_observation(&policy);
    observation
        .finalization
        .as_mut()
        .unwrap()
        .canonical_result_bytes = b"replacement-result".to_vec();
    assert_error(&policy, &observation, "authority_canonical_result_mismatch");
}

#[test]
fn port_takeover_during_protected_phases_is_rejected() {
    let policy = policy();
    let mut observation = completed_observation(&policy);
    observation.sockets[0].ownership_verifications[2]
        .owner
        .creation_time += 1;
    assert_error(&policy, &observation, "authority_socket_owner_drift");
}

#[test]
fn pid_only_port_claim_cannot_replace_creation_bound_owner() {
    let policy = policy();
    let mut observation = completed_observation(&policy);
    observation.sockets[1].owner.creation_time += 1;
    assert_error(&policy, &observation, "authority_socket_owner_mismatch");
}

#[test]
fn parent_pid_reuse_cannot_replace_creation_bound_parent() {
    let policy = policy();
    let mut observation = completed_observation(&policy);
    observation.processes[role_index(ProcessRole::Backend)].parent_creation_time += 1;
    assert_error(&policy, &observation, "authority_process_parent_mismatch");
}

#[test]
fn cleanup_residue_and_competing_listener_are_rejected() {
    let policy = policy();
    let mut residue = completed_observation(&policy);
    residue.cleanup.private_root_removed = false;
    assert_error(&policy, &residue, "authority_cleanup_residue");

    let mut listener = completed_observation(&policy);
    listener.cleanup.sockets[0].competing_owner = Some(process_key(2));
    assert_error(&policy, &listener, "authority_cleanup_socket_mismatch");
}

#[test]
fn cancelled_run_must_burn_before_cleanup() {
    let policy = policy();
    let mut observation = completed_observation(&policy);
    observation.finalization = None;
    observation.terminal = TerminalObservation {
        kind: TerminalKind::Cancelled,
        observed_at: 100,
        intent: TerminalIntent::Unresolved,
        intent_recorded_at: 101,
    };
    observation.cleanup.final_result_persisted = false;
    assert_error(&policy, &observation, "authority_cancel_intent_invalid");

    observation.terminal.intent = TerminalIntent::Burn;
    let ValidatedTerminalRun::Burned(proof) =
        validate_authority_owned_run(&policy, &observation).unwrap()
    else {
        panic!("expected burned proof");
    };
    assert_eq!(
        proof.authority_identity_digest(),
        &policy.authority_identity_digest
    );
    assert_eq!(proof.ticket_digest(), &policy.ticket_digest);
    assert_eq!(proof.run_binding_digest(), &policy.run_binding_digest);
    assert_eq!(proof.reason(), BurnReason::Cancelled);
    assert!(!is_zero_digest(proof.cleanup_receipt_digest()));
    assert_eq!(proof.terminal_ready_at(), 101);
    assert_eq!(proof.cleanup_observed_at(), 120);
}

#[test]
fn timed_out_run_must_burn_and_cannot_have_finalization() {
    let policy = policy();
    let mut observation = completed_observation(&policy);
    observation.finalization = None;
    observation.terminal = TerminalObservation {
        kind: TerminalKind::TimedOut,
        observed_at: policy.deadline,
        intent: TerminalIntent::Unresolved,
        intent_recorded_at: policy.deadline + 1,
    };
    observation.cleanup.observed_at = policy.deadline + 2;
    observation.cleanup.final_result_persisted = false;
    align_terminal_and_cleanup_times(&mut observation);
    assert_error(&policy, &observation, "authority_timeout_intent_invalid");

    observation.terminal.intent = TerminalIntent::Burn;
    let ValidatedTerminalRun::Burned(proof) =
        validate_authority_owned_run(&policy, &observation).unwrap()
    else {
        panic!("expected burned proof");
    };
    assert_eq!(proof.reason(), BurnReason::TimedOut);
}

#[test]
fn failed_run_is_a_distinct_burned_terminal() {
    let policy = policy();
    let mut observation = completed_observation(&policy);
    observation.finalization = None;
    observation.terminal = TerminalObservation {
        kind: TerminalKind::Failed,
        observed_at: 100,
        intent: TerminalIntent::Burn,
        intent_recorded_at: 101,
    };
    observation.cleanup.final_result_persisted = false;
    let ValidatedTerminalRun::Burned(proof) =
        validate_authority_owned_run(&policy, &observation).unwrap()
    else {
        panic!("expected burned proof");
    };
    assert_eq!(proof.reason(), BurnReason::Failed);
}

#[test]
fn timeout_or_cancel_still_requires_complete_cleanup() {
    let policy = policy();
    for kind in [
        TerminalKind::Cancelled,
        TerminalKind::TimedOut,
        TerminalKind::Failed,
    ] {
        let mut observation = completed_observation(&policy);
        observation.finalization = None;
        observation.terminal = TerminalObservation {
            kind,
            observed_at: if kind == TerminalKind::TimedOut {
                policy.deadline
            } else {
                100
            },
            intent: TerminalIntent::Burn,
            intent_recorded_at: if kind == TerminalKind::TimedOut {
                policy.deadline + 1
            } else {
                101
            },
        };
        observation.cleanup.observed_at = observation.terminal.intent_recorded_at + 1;
        observation.cleanup.final_result_persisted = false;
        align_terminal_and_cleanup_times(&mut observation);
        observation.cleanup.runner_profile_removed = false;
        assert_error(&policy, &observation, "authority_cleanup_residue");
    }
}

#[test]
fn duplicated_artifact_or_cleanup_entries_do_not_pass_set_comparison() {
    let policy = policy();
    let mut artifact = completed_observation(&policy);
    artifact.artifacts[1].service_handle_id = artifact.artifacts[0].service_handle_id;
    assert_error(&policy, &artifact, "authority_artifact_handle_invalid");

    let mut cleanup = completed_observation(&policy);
    cleanup.cleanup.deleted_private_artifacts[1] = digest(30);
    assert_error(&policy, &cleanup, "authority_cleanup_artifact_mismatch");
}

#[test]
fn canonical_policy_snapshot_round_trips_and_rejects_every_security_field_substitution() {
    let mut baseline = policy();
    baseline.helper_policies.push(HelperProcessPolicy {
        binding_digest: digest(70),
        executable_digest: digest(71),
        allowed_parent_executable_digests: vec![baseline.process_executable_digests[2]],
        max_instances: 8,
        allowed_exit_codes: vec![0, 1],
    });
    rebind_policy(&mut baseline);
    let snapshot = canonical_supervisor_policy_snapshot(&baseline);
    let decoded = decode_supervisor_policy_snapshot(&snapshot).unwrap();
    assert_eq!(decoded, baseline);
    let prepared = PreparedRecoveryReceipt::from_policy(&baseline);
    assert!(prepared.verifies_policy_snapshot(&snapshot));

    let mut mutations = Vec::new();
    let mut value = baseline.clone();
    value.authority_identity_digest = digest(80);
    mutations.push(value);
    let mut value = baseline.clone();
    value.ticket_digest = digest(81);
    mutations.push(value);
    let mut value = baseline.clone();
    value.service_instance_digest = digest(82);
    mutations.push(value);
    let mut value = baseline.clone();
    value.issued_at += 1;
    mutations.push(value);
    let mut value = baseline.clone();
    value.deadline += 1;
    mutations.push(value);
    let mut value = baseline.clone();
    value.authority_process.pid += 1;
    mutations.push(value);
    let mut value = baseline.clone();
    value.authority_process.creation_time += 1;
    mutations.push(value);
    let mut value = baseline.clone();
    value.authority_parent_process.pid += 1;
    mutations.push(value);
    let mut value = baseline.clone();
    value.authority_parent_process.creation_time += 1;
    mutations.push(value);
    for index in 0..PROCESS_ROLES.len() {
        let mut value = baseline.clone();
        value.process_executable_digests[index] = digest(100 + index as u8);
        mutations.push(value);
    }
    for mut value in [baseline.clone(), baseline.clone(), baseline.clone()] {
        let slot = mutations.len() % 3;
        match slot {
            0 => value.runner_identity_digest = digest(110),
            1 => value.runner_account_digest = digest(111),
            _ => value.runner_profile_digest = digest(112),
        }
        mutations.push(value);
    }
    let mut value = baseline.clone();
    value.inherited_handle_allowlist_digest = digest(113);
    mutations.push(value);
    let mut value = baseline.clone();
    value.deterministic_job_name_digest = digest(114);
    mutations.push(value);
    let mut value = baseline.clone();
    value.private_root_binding_digest = digest(115);
    mutations.push(value);
    let mut value = baseline.clone();
    value.job_object_id += 1;
    mutations.push(value);
    let mut value = baseline.clone();
    value.artifacts[0].binding_digest = digest(118);
    mutations.push(value);
    let mut value = baseline.clone();
    value.artifacts[0].direction = ArtifactDirection::Output;
    mutations.push(value);
    let mut value = baseline.clone();
    value.artifacts[0].expected_content_digest = Some(digest(116));
    mutations.push(value);
    let mut value = baseline.clone();
    value.socket_policies[0].role = SocketRole::Bridge;
    mutations.push(value);
    let mut value = baseline.clone();
    value.socket_policies[1].mode = SocketEndpointMode::FixedFixture;
    mutations.push(value);
    let mut value = baseline.clone();
    value.socket_policies[1].local_port += 1;
    mutations.push(value);
    let mut value = baseline.clone();
    value.socket_policies[0].owner_role = ProcessRole::Unity;
    mutations.push(value);
    let mut value = baseline.clone();
    value.socket_policies[0].driver_binding_digest = digest(117);
    mutations.push(value);
    let mut value = baseline.clone();
    value.helper_policies[0].binding_digest = digest(119);
    mutations.push(value);
    let mut value = baseline.clone();
    value.helper_policies[0].executable_digest = digest(120);
    mutations.push(value);
    let mut value = baseline.clone();
    value.helper_policies[0].allowed_parent_executable_digests[0] = digest(121);
    mutations.push(value);
    let mut value = baseline.clone();
    value.helper_policies[0].max_instances += 1;
    mutations.push(value);
    let mut value = baseline.clone();
    value.helper_policies[0].allowed_exit_codes.push(2);
    mutations.push(value);

    for mutation in mutations {
        assert_ne!(
            canonical_supervisor_policy_digest(&mutation),
            baseline.runner_policy_digest
        );
        assert!(
            !prepared.verifies_policy_snapshot(&canonical_supervisor_policy_snapshot(&mutation))
        );
    }
}

#[test]
fn prepared_and_armed_receipts_reject_zeroed_or_tampered_security_digests() {
    let policy = policy();
    let prepared = PreparedRecoveryReceipt::from_policy(&policy);
    let prepared_bytes = prepared.encode();
    assert_eq!(
        PreparedRecoveryReceipt::decode(&prepared_bytes).unwrap(),
        prepared
    );
    for offset in (8..8 + 10 * 32).step_by(32) {
        let mut tampered = prepared_bytes.clone();
        tampered[offset..offset + 32].fill(0);
        assert!(PreparedRecoveryReceipt::decode(&tampered).is_err());
    }

    let observation = completed_observation(&policy);
    let armed = ArmedRecoveryReceipt::from_armed_launch(
        &policy,
        &prepared,
        &observation.processes[role_index(ProcessRole::Driver)],
        &observation.launches[0],
    );
    let armed_bytes = armed.encode();
    assert_eq!(ArmedRecoveryReceipt::decode(&armed_bytes).unwrap(), armed);
    for offset in (8..8 + 11 * 32).step_by(32) {
        let mut tampered = armed_bytes.clone();
        tampered[offset..offset + 32].fill(0);
        assert!(ArmedRecoveryReceipt::decode(&tampered).is_err());
    }
}

#[test]
fn authorized_short_lived_helpers_are_tracked_without_a_fixed_process_cap() {
    let mut policy = policy();
    policy.helper_policies.push(HelperProcessPolicy {
        binding_digest: digest(70),
        executable_digest: digest(71),
        allowed_parent_executable_digests: vec![policy.process_executable_digests[2]],
        max_instances: 4,
        allowed_exit_codes: vec![0],
    });
    rebind_policy(&mut policy);
    let mut observation = completed_observation(&policy);
    let helper_key = ProcessKey {
        pid: 777,
        creation_time: 7_777,
    };
    observation.helpers.push(HelperProcessObservation {
        policy_binding_digest: digest(70),
        key: helper_key,
        parent: observation.processes[role_index(ProcessRole::Desktop)].key,
        supervisor_pid: observation.processes[role_index(ProcessRole::Desktop)]
            .key
            .pid,
        started_at: 42,
        executable_digest: digest(71),
        executable_digest_read_from_image_handle: true,
        image_handle_identity: file_identity(77),
        image_path_identity_at_terminal: file_identity(77),
        runner_identity_digest: policy.runner_identity_digest,
        job_object_id: policy.job_object_id,
        job_member: true,
        breakaway_allowed: false,
        image_handle_held_through_terminal: true,
        process_handle_held_through_cleanup_begin: true,
        alive_at_finalization: false,
        exited_at: 95,
        exit_code: Some(0),
        completion_port_exit_observed: true,
        terminated_by_job: false,
    });
    observation.job.assigned_processes.push(helper_key);
    observation.cleanup.exited_processes.push(helper_key);
    validate_authority_owned_run(&policy, &observation).unwrap();

    let mut unknown = observation.clone();
    unknown.helpers[0].policy_binding_digest = digest(99);
    assert_error(&policy, &unknown, "authority_helper_process_unexpected");

    let mut hidden = observation;
    hidden
        .cleanup
        .exited_processes
        .retain(|key| *key != helper_key);
    assert_error(&policy, &hidden, "authority_cleanup_process_mismatch");
}

#[test]
fn sealed_abort_validator_covers_no_launch_partial_launch_and_restart_cleanup() {
    let policy = policy();
    let prepared = PreparedRecoveryReceipt::from_policy(&policy);
    let no_launch = empty_abort_observation(&policy);
    let failed =
        validate_authority_owned_abort(&policy, &prepared, None, &no_launch, BurnReason::Failed)
            .unwrap();
    assert_eq!(failed.reason(), BurnReason::Failed);

    let complete = completed_observation(&policy);
    let driver = complete.processes[role_index(ProcessRole::Driver)].clone();
    let launch = complete.launches[0].clone();
    let armed = ArmedRecoveryReceipt::from_armed_launch(&policy, &prepared, &driver, &launch);
    let mut partial = empty_abort_observation(&policy);
    partial.runner = Some(complete.runner.clone());
    partial.runner.as_mut().unwrap().validated_at = 16;
    partial.artifacts = vec![complete.artifacts[0].clone()];
    partial.launches = vec![launch];
    partial.processes.push(driver.clone());
    partial.job.as_mut().unwrap().assigned_processes = vec![driver.key];
    partial.cleanup.exited_processes = vec![driver.key];
    partial.cleanup.deleted_private_artifacts = vec![complete.artifacts[0].binding_digest];
    let recovered = validate_authority_owned_abort(
        &policy,
        &prepared,
        Some(&armed),
        &partial,
        BurnReason::RestartRecovery,
    )
    .unwrap();
    assert_eq!(recovered.reason(), BurnReason::RestartRecovery);

    let mut unknown_residue = partial;
    unknown_residue.cleanup.unknown_processes.push(ProcessKey {
        pid: 999,
        creation_time: 9_999,
    });
    assert_eq!(
        validate_authority_owned_abort(
            &policy,
            &prepared,
            Some(&armed),
            &unknown_residue,
            BurnReason::RestartRecovery,
        )
        .unwrap_err()
        .code(),
        "authority_abort_cleanup_residue"
    );
}
