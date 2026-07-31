#[allow(dead_code, unused_imports)]
#[path = "../primitive_basis_protected_evidence_bundle.rs"]
mod primitive_basis_protected_evidence_bundle;
#[allow(dead_code)]
#[path = "../primitive_bridge_target_control_protocol.rs"]
mod primitive_bridge_target_control_protocol;
#[path = "../primitive_evidence_authority_blob.rs"]
mod primitive_evidence_authority_blob;
#[path = "../primitive_evidence_authority_contract.rs"]
mod primitive_evidence_authority_contract;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_install.rs"]
mod primitive_evidence_authority_install;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_key.rs"]
mod primitive_evidence_authority_key;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_ledger.rs"]
mod primitive_evidence_authority_ledger;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_pipe.rs"]
mod primitive_evidence_authority_pipe;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_runtime.rs"]
mod primitive_evidence_authority_runtime;
#[path = "../primitive_evidence_authority_service_runtime.rs"]
mod primitive_evidence_authority_service_runtime;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_supervisor.rs"]
mod primitive_evidence_authority_supervisor;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_windows.rs"]
mod primitive_evidence_authority_windows;
#[allow(dead_code)]
#[path = "../primitive_evidence_child_protocol.rs"]
mod primitive_evidence_child_protocol;
#[cfg(windows)]
#[allow(dead_code)]
#[path = "../primitive_evidence_child_transport_windows.rs"]
mod primitive_evidence_child_transport_windows;
#[cfg(windows)]
#[allow(dead_code)]
#[path = "../primitive_evidence_process_token_windows.rs"]
mod primitive_evidence_process_token_windows;
#[cfg(windows)]
#[allow(dead_code)]
#[path = "../primitive_evidence_runtime_broker_transfer_windows.rs"]
mod primitive_evidence_runtime_broker_transfer_windows;
#[path = "../primitive_evidence_windows_service_host.rs"]
mod primitive_evidence_windows_service_host;

// Compile the future opaque pre-replay namespace handoff without exposing a
// late-attach production constructor. Bootstrap integration remains blocked.
const _: fn(
    primitive_evidence_authority_blob::AuthenticatedProtectedBlobNamespace,
) -> Result<
    primitive_evidence_authority_blob::ProtectedBlobAuthority,
    primitive_evidence_authority_blob::ProtectedBlobError,
> = primitive_evidence_authority_blob::AuthenticatedProtectedBlobNamespace::into_authority;

#[cfg(windows)]
use primitive_evidence_authority_contract::{
    run_authority_service_duplex_protocol, run_authority_service_single_command_duplex_protocol,
    AuthorityGenerationAttestationSigner, AuthorityGenerationBindingVerifier, AuthorityPeerBinding,
    AuthorityPeerBindingVerifier, AuthorityProjectionCommitReceiptSigner, AuthorityRuntimeHandler,
    AuthorityServiceSession, HandshakeReplayGuard, ProtocolExit,
};
use primitive_evidence_authority_install::bootstrap::CandidateServiceStartLocator;
#[cfg(windows)]
use primitive_evidence_authority_install::bootstrap::{
    await_candidate_validation_armed, bootstrap_authenticated_final_commit_runtime_read_only,
};
#[cfg(windows)]
use primitive_evidence_authority_install::candidate_pipe::CandidateValidationEndpoint;
#[cfg(windows)]
use primitive_evidence_authority_pipe::{
    AuthorityConnectionGate, AuthorityPeerIdentity, AuthorityPeerPolicy, AuthorityPipe,
};
#[cfg(windows)]
use primitive_evidence_authority_service_runtime::run_fixed_pipe_loop;
#[cfg(windows)]
use primitive_evidence_authority_service_runtime::{
    production_admission::{ProductionControllerAdmission, ProductionRunAdmission},
    ProductionControllerPolicySource, ProductionRuntimeComposition,
};
#[cfg(windows)]
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};

#[cfg(windows)]
const CANDIDATE_VALIDATION_ENDPOINT_CONNECTED: bool = false;

#[cfg(windows)]
struct HeldPeerVerifier<'a> {
    identity: &'a AuthorityPeerIdentity,
    policy: &'a AuthorityPeerPolicy,
}

#[cfg(windows)]
impl AuthorityPeerBindingVerifier for HeldPeerVerifier<'_> {
    fn verify_current_peer_binding(
        &mut self,
    ) -> Result<AuthorityPeerBinding, primitive_evidence_authority_contract::ContractError> {
        self.identity.revalidate(self.policy).map_err(|error| {
            primitive_evidence_authority_contract::ContractError::new(error.code())
        })?;
        AuthorityPeerBinding::new(
            self.identity.process_id(),
            self.identity.process_creation_time(),
            self.identity.session_id(),
            *self.identity.controller_sha256(),
            self.identity.controller_file_identity_digest(),
        )
    }
}

#[cfg(windows)]
#[allow(dead_code)]
fn serve_one_authenticated_connection<H, V, S>(
    gate: &AuthorityConnectionGate,
    policy: &AuthorityPeerPolicy,
    runtime: &H,
    binding_verifier: &mut V,
    signer: &mut S,
    replay_guard: &HandshakeReplayGuard,
    stop_requested: fn() -> bool,
) -> Result<ProtocolExit, String>
where
    H: AuthorityRuntimeHandler,
    V: AuthorityGenerationBindingVerifier,
    S: AuthorityGenerationAttestationSigner + AuthorityProjectionCommitReceiptSigner,
{
    let lease = gate
        .try_acquire()
        .map_err(|error| error.code().to_string())?;
    let mut pipe = AuthorityPipe::create().map_err(|error| error.code().to_string())?;
    let stop_handle = pipe.stop_handle();
    let watcher_gate = gate.clone();
    let watcher_done = Arc::new(AtomicBool::new(false));
    let watcher_done_signal = Arc::clone(&watcher_done);
    let watcher = std::thread::spawn(move || -> Result<(), String> {
        while !watcher_done_signal.load(Ordering::Acquire) {
            if stop_requested() {
                watcher_gate.request_stop();
                return stop_handle
                    .cancel_pending_io()
                    .map_err(|error| error.code().to_string());
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        Ok(())
    });

    let result = (|| {
        let identity = pipe
            .accept_peer(policy)
            .map_err(|error| error.code().to_string())?;
        let mut peer_verifier = HeldPeerVerifier {
            identity: &identity,
            policy,
        };
        let mut session = AuthorityServiceSession::new(
            runtime,
            binding_verifier,
            &mut peer_verifier,
            signer,
            replay_guard,
        );
        run_authority_service_duplex_protocol(&mut pipe, &mut session, || {
            gate.is_stop_requested() || stop_requested()
        })
        .map_err(|error| error.code().to_string())
    })();

    watcher_done.store(true, Ordering::Release);
    match watcher.join() {
        Ok(Ok(())) => {}
        Ok(Err(error)) => {
            gate.latch_failure();
            return Err(error);
        }
        Err(_) => {
            gate.latch_failure();
            return Err("authority_pipe_stop_watcher_failed".to_string());
        }
    }
    lease.release();
    if matches!(result, Ok(ProtocolExit::Fatal)) || (result.is_err() && !gate.is_stop_requested()) {
        gate.latch_failure();
    }
    result
}

#[cfg(windows)]
#[allow(dead_code)]
fn run_authenticated_service_loop<H, V, S>(
    policy: &AuthorityPeerPolicy,
    runtime: &H,
    binding_verifier: &mut V,
    signer: &mut S,
    stop_requested: fn() -> bool,
) -> u32
where
    H: AuthorityRuntimeHandler,
    V: AuthorityGenerationBindingVerifier,
    S: AuthorityGenerationAttestationSigner + AuthorityProjectionCommitReceiptSigner,
{
    let gate = AuthorityConnectionGate::default();
    let replay_guard = HandshakeReplayGuard::default();
    run_fixed_pipe_loop(
        &gate,
        || {
            serve_one_authenticated_connection(
                &gate,
                policy,
                runtime,
                binding_verifier,
                signer,
                &replay_guard,
                stop_requested,
            )
        },
        stop_requested,
    )
}

#[cfg(windows)]
fn serve_one_installed_controller_connection<H, V, S>(
    gate: &AuthorityConnectionGate,
    mut pipe: AuthorityPipe,
    policy_source: &ProductionControllerPolicySource,
    runs: &ProductionRunAdmission,
    runtime: &H,
    binding_verifier: &mut V,
    signer: &mut S,
    replay_guard: &HandshakeReplayGuard,
    stop_requested: fn() -> bool,
) -> Result<ProtocolExit, String>
where
    H: AuthorityRuntimeHandler,
    V: AuthorityGenerationBindingVerifier,
    S: AuthorityGenerationAttestationSigner + AuthorityProjectionCommitReceiptSigner,
{
    let lease = gate
        .try_acquire()
        .map_err(|error| error.code().to_string())?;
    let stop_handle = pipe.stop_handle();
    let watcher_gate = gate.clone();
    let watcher_done = Arc::new(AtomicBool::new(false));
    let watcher_done_signal = Arc::clone(&watcher_done);
    let watcher = std::thread::spawn(move || -> Result<(), String> {
        while !watcher_done_signal.load(Ordering::Acquire) {
            if stop_requested() {
                watcher_gate.request_stop();
                return stop_handle
                    .cancel_pending_io()
                    .map_err(|error| error.code().to_string());
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        Ok(())
    });

    let result = (|| {
        let (policy, expected_source_binding) = policy_source
            .current_policy_with_binding()
            .map_err(|error| error.code().to_string())?;
        let capability = pipe
            .accept_installed_controller(policy)
            .map_err(|error| error.code().to_string())?;
        policy_source
            .require_current_binding(&expected_source_binding)
            .map_err(|error| error.code().to_string())?;
        let (mut peer_verifier, mut controller_admission) =
            ProductionControllerAdmission::split(capability, runs.clone());
        let mut session = AuthorityServiceSession::new_with_installed_controller_admission(
            runtime,
            binding_verifier,
            &mut peer_verifier,
            signer,
            replay_guard,
            &mut controller_admission,
        );
        run_authority_service_single_command_duplex_protocol(&mut pipe, &mut session, || {
            gate.is_stop_requested() || stop_requested()
        })
        .map_err(|error| error.code().to_string())
    })();

    watcher_done.store(true, Ordering::Release);
    match watcher.join() {
        Ok(Ok(())) => {}
        Ok(Err(error)) => {
            gate.latch_failure();
            return Err(error);
        }
        Err(_) => {
            gate.latch_failure();
            return Err("authority_pipe_stop_watcher_failed".to_string());
        }
    }
    lease.release();
    if matches!(result, Ok(ProtocolExit::Fatal)) || (result.is_err() && !gate.is_stop_requested()) {
        gate.latch_failure();
    }
    result
}

#[cfg(windows)]
fn run_installed_controller_service_loop<H, V, S>(
    first_pipe: AuthorityPipe,
    policy_source: &ProductionControllerPolicySource,
    runs: &ProductionRunAdmission,
    runtime: &H,
    binding_verifier: &mut V,
    signer: &mut S,
    stop_requested: fn() -> bool,
) -> u32
where
    H: AuthorityRuntimeHandler,
    V: AuthorityGenerationBindingVerifier,
    S: AuthorityGenerationAttestationSigner + AuthorityProjectionCommitReceiptSigner,
{
    let gate = AuthorityConnectionGate::default();
    let replay_guard = HandshakeReplayGuard::default();
    let mut first_pipe = Some(first_pipe);
    run_fixed_pipe_loop(
        &gate,
        || {
            let pipe = match first_pipe.take() {
                Some(pipe) => pipe,
                None => AuthorityPipe::create().map_err(|error| error.code().to_string())?,
            };
            serve_one_installed_controller_connection(
                &gate,
                pipe,
                policy_source,
                runs,
                runtime,
                binding_verifier,
                signer,
                &replay_guard,
                stop_requested,
            )
        },
        stop_requested,
    )
}

fn main() {
    let arguments: Vec<std::ffi::OsString> = std::env::args_os().collect();
    match requested_mode(&arguments) {
        ServiceMode::SelfTestStdio => {
            #[cfg(windows)]
            {
                use primitive_evidence_authority_contract::run_read_only_protocol;
                use std::io::{stdin, stdout};

                let mut input = stdin().lock();
                let mut output = stdout().lock();
                if run_read_only_protocol(&mut input, &mut output).is_err() {
                    std::process::exit(2);
                }
            }

            #[cfg(not(windows))]
            std::process::exit(2);
        }
        ServiceMode::Service => {
            if primitive_evidence_windows_service_host::run_service_dispatcher_with_start_arguments(
                primitive_evidence_authority_windows::AUTHORITY_SERVICE_NAME,
                protected_service_body,
            )
            .is_err()
            {
                std::process::exit(2);
            }
        }
        ServiceMode::Reject => std::process::exit(2),
    }
}

fn protected_service_body(start_arguments: &[std::ffi::OsString]) -> u32 {
    #[cfg(not(windows))]
    {
        return 2;
    }

    #[cfg(windows)]
    {
        if primitive_evidence_windows_service_host::stop_requested() {
            return 0;
        }
        let start_mode = match protected_service_start_mode(start_arguments) {
            ProtectedServiceStartMode::Reject => return 2,
            value => value,
        };
        // Candidate activation is selected only by ephemeral SCM start
        // arguments bound to one credential. A normal service start has no
        // locator and can never enter this one-shot lane.
        if let ProtectedServiceStartMode::Candidate(locator) = start_mode {
            if !CANDIDATE_VALIDATION_ENDPOINT_CONNECTED {
                return 2;
            }
            let pending = match await_candidate_validation_armed(
                locator,
                primitive_evidence_windows_service_host::advance_candidate_start_pending_checkpoint,
            ) {
                Ok(value) => value,
                Err(_) => return 2,
            };
            let endpoint = match CandidateValidationEndpoint::prepare(&pending.pipe_instance_id()) {
                Ok(value) => value,
                Err(_) => return 2,
            };
            if endpoint.pipe_name().is_empty()
                || primitive_evidence_windows_service_host::stop_requested()
            {
                return 2;
            }
            return match endpoint.serve_one(
                primitive_evidence_windows_service_host::stop_requested,
                move |request, peer| {
                    pending
                        .complete_fixed_handshake(request, peer)
                        .map_err(|error| error.code())
                },
            ) {
                Ok(_)
                    if primitive_evidence_windows_service_host::mark_candidate_validation_complete()
                        .is_ok() =>
                {
                    0
                }
                Err(_) => 2,
                Ok(_) => 2,
            };
        }
        if primitive_evidence_authority_service_runtime::require_production_pipe_admission()
            .is_err()
        {
            return 2;
        }
        let bootstrap = match bootstrap_authenticated_final_commit_runtime_read_only() {
            Ok(value) => value,
            Err(_) => return 2,
        };
        let mut runtime =
            match primitive_evidence_authority_service_runtime::compose_production_runtime(
                bootstrap,
            ) {
                Ok(value) => value,
                Err(_) => return 2,
            };
        if runtime.verify_service_trust().is_err() {
            return shutdown_composition_before_exit(&runtime, 2);
        }
        if primitive_evidence_authority_service_runtime::require_production_runtime_ready(
            runtime.runtime(),
        )
        .is_err()
        {
            return shutdown_composition_before_exit(&runtime, 2);
        }
        if primitive_evidence_windows_service_host::stop_requested() {
            return shutdown_composition_before_exit(&runtime, 0);
        }
        let first_pipe = match AuthorityPipe::create() {
            Ok(value) => value,
            Err(_) => return shutdown_composition_before_exit(&runtime, 2),
        };
        if primitive_evidence_windows_service_host::report_running().is_err() {
            return shutdown_composition_before_exit(&runtime, 2);
        }
        let (runtime, mut binding_verifier, mut signer, policy_source, runs) =
            runtime.into_service_parts();
        let exit_code = run_installed_controller_service_loop(
            first_pipe,
            &policy_source,
            &runs,
            &runtime,
            &mut binding_verifier,
            &mut signer,
            primitive_evidence_windows_service_host::stop_requested,
        );
        if runtime.shutdown_and_wait().is_err() {
            return 2;
        }
        exit_code
    }
}

#[cfg(windows)]
fn shutdown_composition_before_exit(runtime: &ProductionRuntimeComposition, exit_code: u32) -> u32 {
    if runtime.shutdown_and_wait().is_err() {
        2
    } else {
        exit_code
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ProtectedServiceStartMode {
    CommittedRuntime,
    Candidate(CandidateServiceStartLocator),
    Reject,
}

fn protected_service_start_mode(arguments: &[std::ffi::OsString]) -> ProtectedServiceStartMode {
    if arguments.is_empty() {
        return ProtectedServiceStartMode::CommittedRuntime;
    }
    let Some(tokens) = arguments
        .iter()
        .map(|value| value.to_str())
        .collect::<Option<Vec<_>>>()
    else {
        return ProtectedServiceStartMode::Reject;
    };
    CandidateServiceStartLocator::parse_ordered(&tokens)
        .map(ProtectedServiceStartMode::Candidate)
        .unwrap_or(ProtectedServiceStartMode::Reject)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ServiceMode {
    SelfTestStdio,
    Service,
    Reject,
}

fn requested_mode(arguments: &[std::ffi::OsString]) -> ServiceMode {
    match arguments {
        [_, command] if command == "--self-test-stdio" => ServiceMode::SelfTestStdio,
        [_, command] if command == "--service" => ServiceMode::Service,
        _ => ServiceMode::Reject,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;

    fn arguments(values: &[&str]) -> Vec<OsString> {
        values.iter().map(OsString::from).collect()
    }

    #[test]
    fn self_test_and_service_modes_are_exact_and_never_alias() {
        assert_eq!(
            requested_mode(&arguments(&["service.exe", "--self-test-stdio"])),
            ServiceMode::SelfTestStdio
        );
        assert_eq!(
            requested_mode(&arguments(&["service.exe", "--service"])),
            ServiceMode::Service
        );
        for rejected in [
            arguments(&["service.exe"]),
            arguments(&["service.exe", "--self-test-stdio", "extra"]),
            arguments(&["service.exe", "--service", "extra"]),
            arguments(&["service.exe", "--status"]),
        ] {
            assert_eq!(requested_mode(&rejected), ServiceMode::Reject);
        }
    }

    #[test]
    fn production_candidate_endpoint_stays_closed() {
        #[cfg(windows)]
        assert!(!CANDIDATE_VALIDATION_ENDPOINT_CONNECTED);
    }

    #[test]
    fn service_start_lane_is_selected_only_by_an_exact_ephemeral_locator() {
        assert!(matches!(
            protected_service_start_mode(&[]),
            ProtectedServiceStartMode::CommittedRuntime
        ));
        let arguments = [
            "--candidate-validation-v1".to_string(),
            format!("--transaction-sha256={}", "11".repeat(32)),
            format!("--capsule-sha256={}", "22".repeat(32)),
            format!("--candidate-nonce={}", "33".repeat(32)),
            format!("--credential-sha256={}", "44".repeat(32)),
        ]
        .map(OsString::from);
        assert!(matches!(
            protected_service_start_mode(&arguments),
            ProtectedServiceStartMode::Candidate(_)
        ));
        let mut drifted = arguments.clone();
        drifted.swap(1, 2);
        assert!(matches!(
            protected_service_start_mode(&drifted),
            ProtectedServiceStartMode::Reject
        ));
        assert!(matches!(
            protected_service_start_mode(&arguments[..4]),
            ProtectedServiceStartMode::Reject
        ));
    }
}
