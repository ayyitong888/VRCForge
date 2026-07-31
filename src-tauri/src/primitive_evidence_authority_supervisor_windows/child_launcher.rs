//! Private product adapter for protected child creation.
//!
//! The operating-system call is intentionally sealed behind one fixed request:
//! an exact held executable, no command line, one canonical Unicode
//! environment, the verified runner profile as working directory, the exact
//! three standard handles, and the Job/HANDLE_LIST attribute set. FinalCommit
//! now supplies the one-time typed policy source; privileged token/profile/root
//! machine readers, per-create live root-object revalidation, and the
//! production API join remain explicitly closed.

#![cfg(windows)]
#![cfg_attr(not(test), allow(dead_code))]

use super::{
    child_environment::{CanonicalChildEnvironmentBlock, VerifiedRunnerEnvironmentRootsCapability},
    child_handshake::{
        AdmittedChildControlSession, ExclusiveChildEndpointTransfer, ParentAffineHandshakeSession,
        ParentHandshakeError, ParentHeldChildAdmissionInputs, ParentHeldChildLaunchInputs,
        ParentProtocolProjection, VerifiedHeldChildLaunch,
    },
    child_transport::{
        observe_live_created_child_process_and_thread, CreatedSuspendedChildClosureBinding,
        InheritedClientHandleLease, ParentHandleExclusions, ParentPipeError,
        ParentPipeSecuritySpec, ParentPipeSet,
    },
    native_job::{
        NativeChildJobObservation, NativeJobLaunchAttributeBinding, NativeJobLaunchAttributeList,
        NativeJobRunnerLaunchBinding, NativeJobTerminalCompletion, NativeJobTerminalProof,
        NativeUnadmittedRootContainmentReceipt, WindowsNativeActiveJob, WindowsNativeJob,
        FIXED_CHILD_CREATION_FLAGS,
    },
    Digest, SupervisorError, SupervisorPolicy,
};
use crate::{
    primitive_evidence_authority_install::bootstrap::{
        AuthenticatedFinalCommitBoundary, AuthenticatedRunnerLaunchPolicy,
    },
    primitive_evidence_authority_pipe::{
        ScenarioStartExecutableRole, VerifiedScenarioExecutableCreateBinding,
        VerifiedScenarioExecutableLaunch, VerifiedScenarioStartContract,
    },
    primitive_evidence_child_protocol::windows_child_handshake::{
        ParentHeldAuthorityServerEvidence, ParentHeldRestrictedChildProcessEvidence,
    },
    primitive_evidence_child_protocol::{
        ChildBootstrapRole, PrivateControlCapability, RoleCapabilitySetBinding,
    },
    primitive_evidence_child_transport_windows::project_role_capability_set_from_verified_parent_pipe_contract,
    primitive_evidence_process_token_windows::{
        measure_expected_restricted_runner_primary_token_digest, ExpectedRestrictedRunnerSid,
        VerifiedRestrictedRunnerPrimaryTokenCapability,
    },
};
use std::{
    collections::BTreeSet,
    ffi::c_void,
    fmt,
    mem::{size_of, zeroed},
    os::windows::{
        ffi::OsStrExt,
        io::{AsHandle, AsRawHandle, BorrowedHandle, FromRawHandle, OwnedHandle, RawHandle},
    },
    path::Path,
    ptr::{null, null_mut},
    sync::{Arc, Mutex},
    time::Duration,
};
use windows_sys::Win32::{
    Foundation::{GetHandleInformation, HANDLE, HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE},
    System::Threading::{
        CreateProcessAsUserW, GetProcessId, GetProcessIdOfThread, GetThreadId, ResumeThread,
        PROCESS_INFORMATION, STARTF_USESTDHANDLES, STARTUPINFOEXW,
    },
};

const CHILD_CREATE_FAILED: &str = "authority_native_child_create_failed";
const CHILD_CREATE_RESULT_INVALID: &str = "authority_native_child_create_result_invalid";
const CHILD_CREATE_REQUEST_INVALID: &str = "authority_native_child_create_request_invalid";
const CHILD_RESUME_INVALID: &str = "authority_native_child_resume_invalid";
const CHILD_LAUNCH_CONTAINMENT_TIMEOUT: &str = "authority_native_child_launch_containment_timeout";
const RUNNER_JOB_BINDING_INVALID: &str = "authority_native_runner_job_binding_invalid";
const MAX_APPLICATION_UTF16_UNITS: usize = 32_767;
const MAX_COMPLETED_RUNNER_LAUNCH_SEQUENCES: usize = 1024;

#[derive(Clone, Copy, PartialEq, Eq)]
struct RunnerLaunchPolicyBinding {
    authority_generation_digest: Digest,
    run_binding_digest: Digest,
    runner_policy_digest: Digest,
    runner_account_digest: Digest,
    runner_profile_digest: Digest,
    job_object_id: u64,
    deterministic_job_name_digest: Digest,
    job_security_binding_digest: Digest,
}

impl RunnerLaunchPolicyBinding {
    fn from_policy(policy: &SupervisorPolicy) -> Result<Self, SupervisorError> {
        let value = Self {
            authority_generation_digest: policy.authority_generation_digest,
            run_binding_digest: policy.run_binding_digest,
            runner_policy_digest: policy.runner_policy_digest,
            runner_account_digest: policy.runner_account_digest,
            runner_profile_digest: policy.runner_profile_digest,
            job_object_id: policy.job_object_id,
            deterministic_job_name_digest: policy.deterministic_job_name_digest,
            job_security_binding_digest: policy.job_security_binding_digest,
        };
        if value.job_object_id == 0
            || value
                .digests()
                .iter()
                .any(|digest| digest.iter().all(|byte| *byte == 0))
        {
            return Err(SupervisorError::new(
                "authority_native_runner_policy_binding_invalid",
            ));
        }
        Ok(value)
    }

    fn digests(&self) -> [&Digest; 7] {
        [
            &self.authority_generation_digest,
            &self.run_binding_digest,
            &self.runner_policy_digest,
            &self.runner_account_digest,
            &self.runner_profile_digest,
            &self.deterministic_job_name_digest,
            &self.job_security_binding_digest,
        ]
    }

    fn validate_native_job_binding(
        &self,
        binding: NativeJobRunnerLaunchBinding,
    ) -> Result<(), SupervisorError> {
        if self.job_object_id != binding.object_id
            || self.deterministic_job_name_digest != binding.deterministic_name_digest
            || self.authority_generation_digest != binding.authority_generation_digest
            || self.run_binding_digest != binding.run_binding_digest
            || self.job_security_binding_digest != binding.security_binding_digest
        {
            return Err(SupervisorError::new(RUNNER_JOB_BINDING_INVALID));
        }
        Ok(())
    }

    fn validate_job(&self, job: &WindowsNativeJob) -> Result<(), SupervisorError> {
        self.validate_native_job_binding(job.runner_launch_binding()?)
    }

    fn validate_active_job(&self, job: &WindowsNativeActiveJob) -> Result<(), SupervisorError> {
        self.validate_native_job_binding(job.runner_launch_binding()?)
    }

    fn validate_launch_attributes(
        &self,
        binding: &NativeJobLaunchAttributeBinding,
    ) -> Result<(), SupervisorError> {
        self.validate_native_job_binding(binding.runner_launch_binding()?)
    }

    fn validate_child_job_observation(
        &self,
        observation: &NativeChildJobObservation,
    ) -> Result<(), SupervisorError> {
        self.validate_native_job_binding(observation.runner_launch_binding())
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
struct AuthenticatedRunnerStateBinding {
    generation: Digest,
    transaction_sha256: Digest,
    final_commit_receipt_sha256: Digest,
    held_file_identity_sha256: Digest,
    state_bytes_sha256: Digest,
    state_binding_sha256: Digest,
    account_binding_sha256: Digest,
    profile_binding_sha256: Digest,
    environment_roots_binding_sha256: Digest,
}

impl AuthenticatedRunnerStateBinding {
    fn from_authenticated(
        policy: &SupervisorPolicy,
        authenticated: &AuthenticatedRunnerLaunchPolicy,
        environment_roots_binding_sha256: Digest,
    ) -> Result<Self, SupervisorError> {
        let value = Self {
            generation: *authenticated.generation(),
            transaction_sha256: *authenticated.transaction_sha256(),
            final_commit_receipt_sha256: *authenticated.final_commit_receipt_sha256(),
            held_file_identity_sha256: *authenticated.held_file_identity_sha256(),
            state_bytes_sha256: *authenticated.state_bytes_sha256(),
            state_binding_sha256: *authenticated.state_binding_sha256(),
            account_binding_sha256: *authenticated.account_binding_sha256(),
            profile_binding_sha256: *authenticated.profile_binding_sha256(),
            environment_roots_binding_sha256,
        };
        if value.generation != policy.authority_generation_digest
            || value
                .digests()
                .iter()
                .any(|digest| digest.iter().all(|byte| *byte == 0))
        {
            return Err(SupervisorError::new(
                "authority_native_runner_authenticated_policy_binding_mismatch",
            ));
        }
        Ok(value)
    }

    fn validates_for(&self, policy: &SupervisorPolicy) -> bool {
        self.generation == policy.authority_generation_digest
            && self
                .digests()
                .iter()
                .all(|digest| digest.iter().any(|byte| *byte != 0))
    }

    fn digests(&self) -> [&Digest; 9] {
        [
            &self.generation,
            &self.transaction_sha256,
            &self.final_commit_receipt_sha256,
            &self.held_file_identity_sha256,
            &self.state_bytes_sha256,
            &self.state_binding_sha256,
            &self.account_binding_sha256,
            &self.profile_binding_sha256,
            &self.environment_roots_binding_sha256,
        ]
    }
}

#[derive(Default)]
struct RunnerLaunchMintState {
    active_run_binding: Option<Digest>,
    completed_run_bindings: BTreeSet<Digest>,
}

/// Generation-wide authority built once from the authenticated FinalCommit
/// runner-policy slot and verified machine capabilities. It can mint many
/// distinct run-bound sequences over its lifetime, but never concurrently and
/// never twice for the same run binding.
///
/// The production supervisor API must own this value plus at most one minted
/// preparation capability or affine run lease. Mint during verified preflight,
/// consume it into the first pending launch, and retain the lease through every
/// admitted or fault-held state. Only a verified terminal transition permits a
/// later sequential ticket without reopening FinalCommit.
pub(super) struct AuthenticatedRunnerLaunchAuthority {
    authenticated: AuthenticatedRunnerLaunchPolicy,
    primary_token: Arc<OwnedHandle>,
    expected_runner_sid: ExpectedRestrictedRunnerSid,
    primary_token_digest: Digest,
    environment_roots: VerifiedRunnerEnvironmentRootsCapability,
    mint_state: Arc<Mutex<RunnerLaunchMintState>>,
}

impl fmt::Debug for AuthenticatedRunnerLaunchAuthority {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("AuthenticatedRunnerLaunchAuthority(<held-and-redacted>)")
    }
}

impl AuthenticatedRunnerLaunchAuthority {
    /// Unique product constructor. The FinalCommit boundary burns its sealed
    /// runner-policy slot here; token and path material arrive only as opaque
    /// verified capabilities from their still-closed machine readers.
    pub(super) fn from_authenticated_final_commit_boundary(
        boundary: &mut AuthenticatedFinalCommitBoundary,
        primary_token: VerifiedRestrictedRunnerPrimaryTokenCapability,
        environment_roots: VerifiedRunnerEnvironmentRootsCapability,
    ) -> Result<Self, SupervisorError> {
        let authenticated = boundary
            .take_runner_launch_policy()
            .map_err(|error| SupervisorError::new(error.code()))?;
        Self::from_authenticated_launch_policy(authenticated, primary_token, environment_roots)
    }

    fn from_authenticated_launch_policy(
        authenticated: AuthenticatedRunnerLaunchPolicy,
        primary_token: VerifiedRestrictedRunnerPrimaryTokenCapability,
        environment_roots: VerifiedRunnerEnvironmentRootsCapability,
    ) -> Result<Self, SupervisorError> {
        if !primary_token.verifies_account_sid(authenticated.account_sid()) {
            return Err(SupervisorError::new(
                "authority_native_runner_primary_token_sid_mismatch",
            ));
        }
        environment_roots.verify_for(&authenticated)?;
        let (primary_token, expected_runner_sid, primary_token_digest) = primary_token
            .into_verified_parts()
            .map_err(|error| SupervisorError::new(error.code()))?;
        Ok(Self {
            authenticated,
            primary_token: Arc::new(primary_token),
            expected_runner_sid,
            primary_token_digest,
            environment_roots,
            mint_state: Arc::new(Mutex::new(RunnerLaunchMintState::default())),
        })
    }

    pub(super) fn mint_for_run(
        &mut self,
        policy: &SupervisorPolicy,
    ) -> Result<VerifiedRestrictedRunnerLaunchCapability, SupervisorError> {
        let policy_binding = RunnerLaunchPolicyBinding::from_policy(policy)?;
        let (environment, environment_roots_binding_sha256) = self
            .environment_roots
            .environment_for_run(policy.runner_profile_digest, &self.authenticated)?;
        let authenticated_state_binding = AuthenticatedRunnerStateBinding::from_authenticated(
            policy,
            &self.authenticated,
            environment_roots_binding_sha256,
        )?;
        let mut state = self
            .mint_state
            .lock()
            .map_err(|_| SupervisorError::new("authority_native_runner_mint_state_poisoned"))?;
        if state.active_run_binding.is_some() {
            return Err(SupervisorError::new(
                "authority_native_runner_launch_sequence_active",
            ));
        }
        if state
            .completed_run_bindings
            .contains(&policy.run_binding_digest)
        {
            return Err(SupervisorError::new(
                "authority_native_runner_launch_sequence_replayed",
            ));
        }
        if state.completed_run_bindings.len() >= MAX_COMPLETED_RUNNER_LAUNCH_SEQUENCES {
            return Err(SupervisorError::new(
                "authority_native_runner_launch_sequence_history_full",
            ));
        }
        state.active_run_binding = Some(policy.run_binding_digest);
        drop(state);
        Ok(VerifiedRestrictedRunnerLaunchCapability {
            primary_token: Arc::clone(&self.primary_token),
            expected_runner_sid: self.expected_runner_sid.clone(),
            primary_token_digest: self.primary_token_digest,
            policy_binding,
            authenticated_state_binding,
            environment,
            mint_state: Arc::clone(&self.mint_state),
            sequence_run_binding: policy.run_binding_digest,
        })
    }
}

/// Non-cloneable, one-use preparation capability for one policy-bound launch
/// sequence. Preparing the first child consumes this value into the affine run
/// lease retained by every later launch state.
pub(super) struct VerifiedRestrictedRunnerLaunchCapability {
    primary_token: Arc<OwnedHandle>,
    expected_runner_sid: ExpectedRestrictedRunnerSid,
    primary_token_digest: Digest,
    policy_binding: RunnerLaunchPolicyBinding,
    authenticated_state_binding: AuthenticatedRunnerStateBinding,
    environment: CanonicalChildEnvironmentBlock,
    mint_state: Arc<Mutex<RunnerLaunchMintState>>,
    sequence_run_binding: Digest,
}

impl fmt::Debug for VerifiedRestrictedRunnerLaunchCapability {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("VerifiedRestrictedRunnerLaunchCapability(<held-and-redacted>)")
    }
}

impl VerifiedRestrictedRunnerLaunchCapability {
    fn validate_for(&self, policy: &SupervisorPolicy) -> Result<(), SupervisorError> {
        let expected = RunnerLaunchPolicyBinding::from_policy(policy)?;
        let sequence_is_active = self
            .mint_state
            .lock()
            .map_err(|_| SupervisorError::new("authority_native_runner_mint_state_poisoned"))?
            .active_run_binding
            == Some(self.sequence_run_binding);
        let mut flags = 0u32;
        if self.policy_binding != expected
            || self.sequence_run_binding != policy.run_binding_digest
            || !sequence_is_active
            || !self.authenticated_state_binding.validates_for(policy)
            || self.environment.runner_profile_digest() != &expected.runner_profile_digest
            || unsafe {
                GetHandleInformation(self.primary_token.as_raw_handle().cast(), &mut flags)
            } == 0
            || flags & HANDLE_FLAG_INHERIT != 0
            || measure_expected_restricted_runner_primary_token_digest(
                self.primary_token.as_raw_handle().cast(),
                &self.expected_runner_sid,
            )
            .map_err(|error| SupervisorError::new(error.code()))?
                != self.primary_token_digest
        {
            return Err(SupervisorError::new(
                "authority_native_runner_launch_capability_invalid",
            ));
        }
        Ok(())
    }

    fn into_affine_run_lease(
        self,
        policy: &SupervisorPolicy,
        job: &WindowsNativeJob,
    ) -> Result<AffineRestrictedRunnerRunLease, SupervisorError> {
        self.policy_binding.validate_job(job)?;
        self.validate_for(policy)?;
        Ok(AffineRestrictedRunnerRunLease { capability: self })
    }

    #[cfg(test)]
    fn into_affine_run_lease_for_state_test(self) -> AffineRestrictedRunnerRunLease {
        AffineRestrictedRunnerRunLease { capability: self }
    }
}

/// Affine owner retained from the first prepare attempt through terminal
/// drain. Dropping this lease intentionally leaves the generation's active
/// slot occupied forever. Only the explicit typed receipt transitions below
/// can both record the run and clear that slot.
pub(super) struct AffineRestrictedRunnerRunLease {
    capability: VerifiedRestrictedRunnerLaunchCapability,
}

impl fmt::Debug for AffineRestrictedRunnerRunLease {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("AffineRestrictedRunnerRunLease(<held-and-redacted>)")
    }
}

impl Drop for AffineRestrictedRunnerRunLease {
    fn drop(&mut self) {
        // Fail closed: unwind, abandonment, and fault-held destruction must not
        // clear the generation slot or turn an unverified run into completion.
    }
}

impl AffineRestrictedRunnerRunLease {
    fn validate_for(&self, policy: &SupervisorPolicy) -> Result<(), SupervisorError> {
        self.capability.validate_for(policy)
    }

    fn validate_for_active_job(
        &self,
        policy: &SupervisorPolicy,
        job: &WindowsNativeActiveJob,
    ) -> Result<(), SupervisorError> {
        self.capability.policy_binding.validate_active_job(job)?;
        self.validate_for(policy)
    }

    fn validate_for_launch_attributes(
        &self,
        policy: &SupervisorPolicy,
        binding: &NativeJobLaunchAttributeBinding,
    ) -> Result<(), SupervisorError> {
        self.capability
            .policy_binding
            .validate_launch_attributes(binding)?;
        self.validate_for(policy)
    }

    fn validate_child_job_observation(
        &self,
        observation: &NativeChildJobObservation,
    ) -> Result<(), SupervisorError> {
        self.capability
            .policy_binding
            .validate_child_job_observation(observation)
    }

    fn primary_token(&self) -> BorrowedHandle<'_> {
        self.capability.primary_token.as_handle()
    }

    fn environment(&self) -> &CanonicalChildEnvironmentBlock {
        &self.capability.environment
    }

    fn expected_runner_sid(&self) -> ExpectedRestrictedRunnerSid {
        self.capability.expected_runner_sid.clone()
    }

    fn primary_token_digest(&self) -> &Digest {
        &self.capability.primary_token_digest
    }

    fn complete_after_initial_containment(
        self,
        receipt: NativeUnadmittedRootContainmentReceipt,
        expected_process_id: u32,
    ) -> Result<LaunchContainmentReceipt, (Self, SupervisorError)> {
        if expected_process_id == 0
            || receipt.process_id != expected_process_id
            || (!receipt.exact_job_membership_proven
                && !receipt.direct_process_termination_requested)
            || !receipt.job_termination_requested
            || !receipt.process_signaled
            || !receipt.exact_empty_terminal_job
        {
            return Err((
                self,
                SupervisorError::new("authority_native_runner_initial_containment_invalid"),
            ));
        }
        match self.finish_sequence() {
            Ok(()) => Ok(LaunchContainmentReceipt::Initial(receipt)),
            Err((lease, error)) => Err((lease, error)),
        }
    }

    fn complete_after_active_terminal_drain(
        self,
        proof: NativeJobTerminalProof,
    ) -> Result<LaunchContainmentReceipt, (Self, SupervisorError)> {
        let binding = &self.capability.policy_binding;
        let completion = match proof.consume_for_runner(
            binding.job_object_id,
            &binding.deterministic_job_name_digest,
            &binding.authority_generation_digest,
            &binding.run_binding_digest,
            &binding.job_security_binding_digest,
        ) {
            Ok(completion) => completion,
            Err(error) => return Err((self, error)),
        };
        match self.finish_sequence() {
            Ok(()) => Ok(LaunchContainmentReceipt::Active(completion)),
            Err((lease, error)) => Err((lease, error)),
        }
    }

    fn finish_sequence(self) -> Result<(), (Self, SupervisorError)> {
        if let Err(error) = self.commit_completed_sequence() {
            return Err((self, error));
        }
        Ok(())
    }

    fn commit_completed_sequence(&self) -> Result<(), SupervisorError> {
        let mut state = self
            .capability
            .mint_state
            .lock()
            .map_err(|_| SupervisorError::new("authority_native_runner_mint_state_poisoned"))?;
        if state.active_run_binding != Some(self.capability.sequence_run_binding)
            || state
                .completed_run_bindings
                .contains(&self.capability.sequence_run_binding)
        {
            return Err(SupervisorError::new(
                "authority_native_runner_launch_sequence_state_invalid",
            ));
        }
        if state.completed_run_bindings.len() >= MAX_COMPLETED_RUNNER_LAUNCH_SEQUENCES {
            return Err(SupervisorError::new(
                "authority_native_runner_launch_sequence_history_full",
            ));
        }
        if !state
            .completed_run_bindings
            .insert(self.capability.sequence_run_binding)
        {
            return Err(SupervisorError::new(
                "authority_native_runner_launch_sequence_state_invalid",
            ));
        }
        state.active_run_binding = None;
        Ok(())
    }
}

struct ExactChildCreationRequest<'a> {
    primary_token: BorrowedHandle<'a>,
    application_name: &'a [u16],
    environment: &'a [u16],
    working_directory: &'a [u16],
    standard_handles: [HANDLE; 3],
    creation_flags: u32,
    inherit_handles: bool,
    attribute_list: *mut c_void,
}

impl ExactChildCreationRequest<'_> {
    fn validate(&self) -> Result<(), SupervisorError> {
        let token = self.primary_token.as_raw_handle().cast::<c_void>();
        if token.is_null()
            || token == INVALID_HANDLE_VALUE
            || self.application_name.len() <= 1
            || self.application_name.len() > MAX_APPLICATION_UTF16_UNITS
            || self.application_name.last() != Some(&0)
            || self.application_name[..self.application_name.len() - 1].contains(&0)
            || self.environment.len() <= 2
            || self.environment.last() != Some(&0)
            || self.environment.get(self.environment.len() - 2) != Some(&0)
            || self.working_directory.len() <= 1
            || self.working_directory.last() != Some(&0)
            || self.working_directory[..self.working_directory.len() - 1].contains(&0)
            || self
                .standard_handles
                .iter()
                .any(|handle| handle.is_null() || *handle == INVALID_HANDLE_VALUE)
            || self
                .standard_handles
                .iter()
                .enumerate()
                .any(|(index, handle)| self.standard_handles[..index].contains(handle))
            || self.creation_flags != FIXED_CHILD_CREATION_FLAGS
            || !self.inherit_handles
            || self.attribute_list.is_null()
        {
            return Err(SupervisorError::new(CHILD_CREATE_REQUEST_INVALID));
        }
        Ok(())
    }
}

trait ChildProcessCreationKernel {
    fn create(
        &mut self,
        request: &ExactChildCreationRequest<'_>,
    ) -> Result<PROCESS_INFORMATION, SupervisorError>;
}

struct WindowsChildProcessCreationKernel;

impl ChildProcessCreationKernel for WindowsChildProcessCreationKernel {
    fn create(
        &mut self,
        request: &ExactChildCreationRequest<'_>,
    ) -> Result<PROCESS_INFORMATION, SupervisorError> {
        request.validate()?;
        let mut startup: STARTUPINFOEXW = unsafe { zeroed() };
        startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = request.standard_handles[0];
        startup.StartupInfo.hStdOutput = request.standard_handles[1];
        startup.StartupInfo.hStdError = request.standard_handles[2];
        startup.lpAttributeList = request.attribute_list.cast();
        let mut information: PROCESS_INFORMATION = unsafe { zeroed() };
        if unsafe {
            CreateProcessAsUserW(
                request.primary_token.as_raw_handle().cast(),
                request.application_name.as_ptr(),
                null_mut(),
                null(),
                null(),
                i32::from(request.inherit_handles),
                request.creation_flags,
                request.environment.as_ptr().cast(),
                request.working_directory.as_ptr(),
                &startup.StartupInfo,
                &mut information,
            )
        } == 0
        {
            return Err(SupervisorError::new(CHILD_CREATE_FAILED));
        }
        Ok(information)
    }
}

pub(super) struct CreatedSuspendedProcess {
    process: OwnedHandle,
    primary_thread: OwnedHandle,
    process_id: u32,
    primary_thread_id: u32,
    resumed: bool,
}

impl fmt::Debug for CreatedSuspendedProcess {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("CreatedSuspendedProcess(<held-and-redacted>)")
    }
}

impl CreatedSuspendedProcess {
    fn from_information(information: PROCESS_INFORMATION) -> Result<Self, SupervisorError> {
        let process_valid =
            !information.hProcess.is_null() && information.hProcess != INVALID_HANDLE_VALUE;
        let thread_valid =
            !information.hThread.is_null() && information.hThread != INVALID_HANDLE_VALUE;
        if !process_valid || !thread_valid {
            if process_valid {
                drop(unsafe { OwnedHandle::from_raw_handle(information.hProcess as RawHandle) });
            }
            if thread_valid {
                drop(unsafe { OwnedHandle::from_raw_handle(information.hThread as RawHandle) });
            }
            return Err(SupervisorError::new(CHILD_CREATE_RESULT_INVALID));
        }
        let process = unsafe { OwnedHandle::from_raw_handle(information.hProcess as RawHandle) };
        let primary_thread =
            unsafe { OwnedHandle::from_raw_handle(information.hThread as RawHandle) };
        let mut process_flags = 0u32;
        let mut thread_flags = 0u32;
        if information.dwProcessId == 0
            || information.dwThreadId == 0
            || unsafe { GetProcessId(process.as_raw_handle().cast()) } != information.dwProcessId
            || unsafe { GetThreadId(primary_thread.as_raw_handle().cast()) }
                != information.dwThreadId
            || unsafe { GetProcessIdOfThread(primary_thread.as_raw_handle().cast()) }
                != information.dwProcessId
            || unsafe { GetHandleInformation(process.as_raw_handle().cast(), &mut process_flags) }
                == 0
            || unsafe {
                GetHandleInformation(primary_thread.as_raw_handle().cast(), &mut thread_flags)
            } == 0
            || process_flags & HANDLE_FLAG_INHERIT != 0
            || thread_flags & HANDLE_FLAG_INHERIT != 0
        {
            return Err(SupervisorError::new(CHILD_CREATE_RESULT_INVALID));
        }
        Ok(Self {
            process,
            primary_thread,
            process_id: information.dwProcessId,
            primary_thread_id: information.dwThreadId,
            resumed: false,
        })
    }

    fn process(&self) -> BorrowedHandle<'_> {
        self.process.as_handle()
    }

    fn primary_thread(&self) -> BorrowedHandle<'_> {
        self.primary_thread.as_handle()
    }

    fn resume_exactly_once(&mut self) -> Result<(), SupervisorError> {
        if self.resumed || unsafe { ResumeThread(self.primary_thread.as_raw_handle().cast()) } != 1
        {
            return Err(SupervisorError::new(CHILD_RESUME_INVALID));
        }
        self.resumed = true;
        Ok(())
    }
}

fn create_suspended_process_with_kernel<K: ChildProcessCreationKernel>(
    kernel: &mut K,
    policy: &SupervisorPolicy,
    runner: &AffineRestrictedRunnerRunLease,
    executable: &VerifiedScenarioExecutableLaunch<'_>,
    lease: &InheritedClientHandleLease,
    attributes: &mut NativeJobLaunchAttributeList<'_>,
) -> Result<CreatedSuspendedProcess, SupervisorError> {
    let application_name = executable_path_utf16(executable.resolved_path())?;
    let standard_handles = lease.inherited_raw_handles();
    let launch_binding = attributes.binding().clone();
    let request = ExactChildCreationRequest {
        primary_token: runner.primary_token(),
        application_name: &application_name,
        environment: runner.environment().as_utf16(),
        working_directory: runner.environment().working_directory_utf16(),
        standard_handles,
        creation_flags: attributes.creation_flags(),
        inherit_handles: attributes.inherit_handles(),
        attribute_list: attributes.raw_attribute_list().cast(),
    };
    create_suspended_process_from_exact_request_with_kernel(
        kernel,
        policy,
        runner,
        &launch_binding,
        &request,
    )
}

fn create_suspended_process_from_exact_request_with_kernel<K: ChildProcessCreationKernel>(
    kernel: &mut K,
    policy: &SupervisorPolicy,
    runner: &AffineRestrictedRunnerRunLease,
    launch_binding: &NativeJobLaunchAttributeBinding,
    request: &ExactChildCreationRequest<'_>,
) -> Result<CreatedSuspendedProcess, SupervisorError> {
    runner.validate_for_launch_attributes(policy, launch_binding)?;
    request.validate()?;
    CreatedSuspendedProcess::from_information(kernel.create(request)?)
}

fn create_suspended_process_as_restricted_runner(
    policy: &SupervisorPolicy,
    runner: &AffineRestrictedRunnerRunLease,
    executable: &VerifiedScenarioExecutableLaunch<'_>,
    lease: &InheritedClientHandleLease,
    attributes: &mut NativeJobLaunchAttributeList<'_>,
) -> Result<CreatedSuspendedProcess, SupervisorError> {
    create_suspended_process_with_kernel(
        &mut WindowsChildProcessCreationKernel,
        policy,
        runner,
        executable,
        lease,
        attributes,
    )
}

fn executable_path_utf16(path: &Path) -> Result<Vec<u16>, SupervisorError> {
    if !path.is_absolute() || path.as_os_str().is_empty() {
        return Err(SupervisorError::new(CHILD_CREATE_REQUEST_INVALID));
    }
    let value = path
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    if value.len() <= 1
        || value.len() > MAX_APPLICATION_UTF16_UNITS
        || value[..value.len() - 1].contains(&0)
    {
        return Err(SupervisorError::new(CHILD_CREATE_REQUEST_INVALID));
    }
    Ok(value)
}

pub(super) struct PendingBridgeLaunch {
    job: WindowsNativeActiveJob,
    child: PendingProtectedChild,
    runner: AffineRestrictedRunnerRunLease,
}

pub(super) struct AdmittedBridgeLaunch {
    job: WindowsNativeActiveJob,
    bridge: AdmittedProtectedChild,
    runner: AffineRestrictedRunnerRunLease,
}

pub(super) struct PendingDriverLaunch {
    job: WindowsNativeActiveJob,
    bridge: AdmittedProtectedChild,
    driver: PendingProtectedChild,
    runner: AffineRestrictedRunnerRunLease,
}

pub(super) struct AdmittedProtectedChildPair {
    job: WindowsNativeActiveJob,
    bridge: AdmittedProtectedChild,
    driver: AdmittedProtectedChild,
    runner: AffineRestrictedRunnerRunLease,
}

pub(super) struct TerminalProtectedChildPairOwner {
    bridge: AdmittedProtectedChild,
    driver: AdmittedProtectedChild,
    runner: AffineRestrictedRunnerRunLease,
}

pub(super) enum ProtectedChildPairCompletionOwner {
    Active(AdmittedProtectedChildPair),
    Terminal(TerminalProtectedChildPairOwner),
}

impl fmt::Debug for TerminalProtectedChildPairOwner {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let _held = (&self.bridge, &self.driver, &self.runner);
        formatter.write_str("TerminalProtectedChildPairOwner(<held-and-redacted>)")
    }
}

impl fmt::Debug for ProtectedChildPairCompletionOwner {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Active(pair) => formatter.debug_tuple("Active").field(pair).finish(),
            Self::Terminal(pair) => formatter.debug_tuple("Terminal").field(pair).finish(),
        }
    }
}

pub(super) enum ProtectedChildPairCompletionFailure {
    Active {
        pair: AdmittedProtectedChildPair,
        error: SupervisorError,
    },
    Terminal {
        pair: TerminalProtectedChildPairOwner,
        error: SupervisorError,
    },
}

impl ProtectedChildPairCompletionFailure {
    pub(super) fn code(&self) -> &'static str {
        match self {
            Self::Active { error, .. } | Self::Terminal { error, .. } => error.code(),
        }
    }

    pub(super) const fn retains_live_job(&self) -> bool {
        matches!(self, Self::Active { .. })
    }

    pub(super) fn into_parts(self) -> (ProtectedChildPairCompletionOwner, SupervisorError) {
        match self {
            Self::Active { pair, error } => {
                (ProtectedChildPairCompletionOwner::Active(pair), error)
            }
            Self::Terminal { pair, error } => {
                (ProtectedChildPairCompletionOwner::Terminal(pair), error)
            }
        }
    }
}

impl fmt::Debug for ProtectedChildPairCompletionFailure {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ProtectedChildPairCompletionFailure")
            .field(
                "kind",
                if self.retains_live_job() {
                    &"active"
                } else {
                    &"terminal"
                },
            )
            .field("code", &self.code())
            .finish()
    }
}

pub(super) enum ProtectedChildLaunchFailure {
    Rejected {
        error: SupervisorError,
    },
    Contained {
        receipt: LaunchContainmentReceipt,
        original_error: SupervisorError,
    },
    FaultHeldActive {
        job: WindowsNativeActiveJob,
        unattached_child: Option<CreatedSuspendedProcess>,
        runner: AffineRestrictedRunnerRunLease,
        original_error: SupervisorError,
        containment_error: SupervisorError,
    },
    FaultHeldInitial {
        job: WindowsNativeJob,
        child: CreatedSuspendedProcess,
        runner: AffineRestrictedRunnerRunLease,
        original_error: SupervisorError,
        containment_error: SupervisorError,
    },
    FaultHeldTerminal {
        unattached_child: Option<CreatedSuspendedProcess>,
        runner: AffineRestrictedRunnerRunLease,
        original_error: SupervisorError,
        containment_error: SupervisorError,
    },
}

#[derive(Debug)]
pub(super) enum LaunchContainmentReceipt {
    Initial(NativeUnadmittedRootContainmentReceipt),
    Active(NativeJobTerminalCompletion),
}

impl ProtectedChildLaunchFailure {
    pub(super) fn code(&self) -> &'static str {
        match self {
            Self::Rejected { error } => error.code(),
            Self::Contained { original_error, .. } => original_error.code(),
            Self::FaultHeldActive {
                containment_error, ..
            }
            | Self::FaultHeldInitial {
                containment_error, ..
            }
            | Self::FaultHeldTerminal {
                containment_error, ..
            } => containment_error.code(),
        }
    }

    fn rejected(error: SupervisorError) -> Self {
        Self::Rejected { error }
    }
}

impl From<SupervisorError> for ProtectedChildLaunchFailure {
    fn from(error: SupervisorError) -> Self {
        Self::rejected(error)
    }
}

impl fmt::Debug for ProtectedChildLaunchFailure {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ProtectedChildLaunchFailure")
            .field(
                "kind",
                &match self {
                    Self::Rejected { .. } => "rejected",
                    Self::Contained { .. } => "contained",
                    Self::FaultHeldActive { .. } => "faultHeldActive",
                    Self::FaultHeldInitial { .. } => "faultHeldInitial",
                    Self::FaultHeldTerminal { .. } => "faultHeldTerminal",
                },
            )
            .field("code", &self.code())
            .finish()
    }
}

struct PendingProtectedChild {
    created: CreatedSuspendedProcess,
    session: ParentAffineHandshakeSession,
    child_process: ParentHeldRestrictedChildProcessEvidence,
    authority: ParentHeldAuthorityServerEvidence,
    role_capability_set: RoleCapabilitySetBinding,
    executable_create_binding: VerifiedScenarioExecutableCreateBinding,
}

struct AdmittedProtectedChild {
    created: CreatedSuspendedProcess,
    session: AdmittedChildControlSession,
    child_process: ParentHeldRestrictedChildProcessEvidence,
    authority: ParentHeldAuthorityServerEvidence,
    role_capability_set: RoleCapabilitySetBinding,
    executable_create_binding: VerifiedScenarioExecutableCreateBinding,
}

impl fmt::Debug for PendingBridgeLaunch {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("PendingBridgeLaunch(<held-and-redacted>)")
    }
}

impl fmt::Debug for AdmittedBridgeLaunch {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("AdmittedBridgeLaunch(<held-and-redacted>)")
    }
}

impl fmt::Debug for PendingDriverLaunch {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("PendingDriverLaunch(<held-and-redacted>)")
    }
}

impl fmt::Debug for AdmittedProtectedChildPair {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("AdmittedProtectedChildPair(<held-and-redacted>)")
    }
}

impl PendingBridgeLaunch {
    pub(super) fn prepare(
        policy: &SupervisorPolicy,
        runner: VerifiedRestrictedRunnerLaunchCapability,
        start_contract: &VerifiedScenarioStartContract,
        job: WindowsNativeJob,
    ) -> Result<Self, ProtectedChildLaunchFailure> {
        let runner = runner.into_affine_run_lease(policy, &job)?;
        let executable = start_contract
            .prepare_executable_launch(ScenarioStartExecutableRole::BridgeLauncher)
            .map_err(|error| SupervisorError::new(error.code()))?;
        let security = ParentPipeSecuritySpec::from_supervisor_policy(policy)
            .map_err(supervisor_error_from_pipe)?;
        let exclusions = {
            let handles = job.exclusion_handles();
            ParentHandleExclusions::from_borrowed(handles.job(), handles.completion_port())
                .map_err(supervisor_error_from_pipe)?
        };
        let pipes =
            ParentPipeSet::create(&security, &exclusions).map_err(supervisor_error_from_pipe)?;
        let (servers, lease) = pipes.take_inherited_client_handles();
        let role = ChildBootstrapRole::BridgeLauncher;
        let role_capability_set =
            project_role_capability_set(role, &lease, policy.authority_process.pid)?;
        let creation = (|| {
            let inherited = lease.inherited_borrowed_handles();
            let mut attributes = job.prepare_suspended_launch_attributes(&inherited)?;
            let binding = attributes.binding().clone();
            let created = create_suspended_process_as_restricted_runner(
                policy,
                &runner,
                &executable,
                &lease,
                &mut attributes,
            )?;
            Ok::<_, SupervisorError>((created, binding))
        })();
        let (created, launch_binding) = match creation {
            Ok(value) => value,
            Err(error) => {
                lease.close_after_create_failure();
                return Err(error.into());
            }
        };
        let active_job = match job.revalidate_created_root_before_resume_preserving_job(
            created.process(),
            created.primary_thread(),
            created.process_id,
            created.primary_thread_id,
            &launch_binding,
        ) {
            Ok(job) => job,
            Err(failure) => {
                lease.close_after_create_failure();
                let (mut job, error) = failure.into_parts();
                return match job.contain_unadmitted_created_root(created.process()) {
                    Ok(receipt) => match runner
                        .complete_after_initial_containment(receipt, created.process_id)
                    {
                        Ok(receipt) => Err(ProtectedChildLaunchFailure::Contained {
                            receipt,
                            original_error: error,
                        }),
                        Err((runner, containment_error)) => {
                            Err(ProtectedChildLaunchFailure::FaultHeldInitial {
                                job,
                                child: created,
                                runner,
                                original_error: error,
                                containment_error,
                            })
                        }
                    },
                    Err(containment_error) => Err(ProtectedChildLaunchFailure::FaultHeldInitial {
                        job,
                        child: created,
                        runner,
                        original_error: error,
                        containment_error,
                    }),
                };
            }
        };
        let closure = match CreatedSuspendedChildClosureBinding::from_held_suspended_create_result(
            role,
            created.process.as_raw_handle().cast(),
            created.primary_thread.as_raw_handle().cast(),
            created.process_id,
            created.primary_thread_id,
            launch_binding.binding_digest,
        ) {
            Ok(value) => value,
            Err(error) => {
                lease.close_after_create_failure();
                return Err(contain_active_job_then_error(
                    active_job,
                    runner,
                    None,
                    supervisor_error_from_pipe(error),
                ));
            }
        };
        let closed_parent_copies = match lease.close_parent_copies_after_create(closure) {
            Ok(value) => value,
            Err(error) => {
                return Err(contain_active_job_then_error(
                    active_job,
                    runner,
                    None,
                    supervisor_error_from_pipe(error),
                ));
            }
        };
        match assemble_pending_child(
            policy,
            &runner,
            &executable,
            role_capability_set,
            &active_job,
            servers,
            closed_parent_copies,
            created,
        ) {
            Ok(child) => Ok(Self {
                job: active_job,
                child,
                runner,
            }),
            Err(error) => Err(contain_active_job_then_error(
                active_job, runner, None, error,
            )),
        }
    }

    pub(super) fn resume_and_admit(
        self,
        policy: &SupervisorPolicy,
        start_contract: &VerifiedScenarioStartContract,
        timeout: Duration,
    ) -> Result<AdmittedBridgeLaunch, ProtectedChildLaunchFailure> {
        let Self { job, child, runner } = self;
        match resume_and_admit_child(
            policy,
            &runner,
            start_contract,
            &job,
            child,
            ChildBootstrapRole::BridgeLauncher,
            timeout,
        ) {
            Ok(bridge) => Ok(AdmittedBridgeLaunch {
                job,
                bridge,
                runner,
            }),
            Err(error) => Err(contain_active_job_then_error(job, runner, None, error)),
        }
    }
}

impl AdmittedBridgeLaunch {
    pub(super) fn prepare_driver(
        self,
        policy: &SupervisorPolicy,
        start_contract: &VerifiedScenarioStartContract,
    ) -> Result<PendingDriverLaunch, ProtectedChildLaunchFailure> {
        let Self {
            mut job,
            bridge,
            runner,
        } = self;
        if let Err(error) = runner.validate_for_active_job(policy, &job) {
            return Err(contain_active_job_then_error(job, runner, None, error));
        }
        let executable = match start_contract
            .prepare_executable_launch(ScenarioStartExecutableRole::Driver)
            .map_err(|error| SupervisorError::new(error.code()))
        {
            Ok(value) => value,
            Err(error) => {
                return Err(contain_active_job_then_error(job, runner, None, error));
            }
        };
        let security = match ParentPipeSecuritySpec::from_supervisor_policy(policy)
            .map_err(supervisor_error_from_pipe)
        {
            Ok(value) => value,
            Err(error) => {
                return Err(contain_active_job_then_error(job, runner, None, error));
            }
        };
        let exclusions = match {
            let handles = job.exclusion_handles();
            ParentHandleExclusions::from_borrowed(handles.job(), handles.completion_port())
                .map_err(supervisor_error_from_pipe)
        } {
            Ok(value) => value,
            Err(error) => {
                return Err(contain_active_job_then_error(job, runner, None, error));
            }
        };
        let pipes = match ParentPipeSet::create(&security, &exclusions) {
            Ok(value) => value,
            Err(error) => {
                return Err(contain_active_job_then_error(
                    job,
                    runner,
                    None,
                    supervisor_error_from_pipe(error),
                ));
            }
        };
        let (servers, lease) = pipes.take_inherited_client_handles();
        let role = ChildBootstrapRole::LifecycleDriver;
        let role_capability_set =
            match project_role_capability_set(role, &lease, policy.authority_process.pid) {
                Ok(value) => value,
                Err(error) => {
                    lease.close_after_create_failure();
                    return Err(contain_active_job_then_error(job, runner, None, error));
                }
            };
        let creation = (|| {
            let inherited = lease.inherited_borrowed_handles();
            let mut attributes = job.prepare_additional_suspended_launch_attributes(&inherited)?;
            let binding = attributes.binding().clone();
            let created = create_suspended_process_as_restricted_runner(
                policy,
                &runner,
                &executable,
                &lease,
                &mut attributes,
            )?;
            Ok::<_, SupervisorError>((created, binding))
        })();
        let (created, launch_binding) = match creation {
            Ok(value) => value,
            Err(error) => {
                lease.close_after_create_failure();
                return Err(contain_active_job_then_error(job, runner, None, error));
            }
        };
        if let Err(error) = job.revalidate_additional_root_before_resume(
            created.process(),
            created.primary_thread(),
            created.process_id,
            created.primary_thread_id,
            &launch_binding,
        ) {
            lease.close_after_create_failure();
            return Err(contain_active_job_then_error(
                job,
                runner,
                Some(created),
                error,
            ));
        }
        let closure = match CreatedSuspendedChildClosureBinding::from_held_suspended_create_result(
            role,
            created.process.as_raw_handle().cast(),
            created.primary_thread.as_raw_handle().cast(),
            created.process_id,
            created.primary_thread_id,
            launch_binding.binding_digest,
        ) {
            Ok(value) => value,
            Err(error) => {
                lease.close_after_create_failure();
                return Err(contain_active_job_then_error(
                    job,
                    runner,
                    None,
                    supervisor_error_from_pipe(error),
                ));
            }
        };
        let closed_parent_copies = match lease.close_parent_copies_after_create(closure) {
            Ok(value) => value,
            Err(error) => {
                return Err(contain_active_job_then_error(
                    job,
                    runner,
                    None,
                    supervisor_error_from_pipe(error),
                ));
            }
        };
        match assemble_pending_child(
            policy,
            &runner,
            &executable,
            role_capability_set,
            &job,
            servers,
            closed_parent_copies,
            created,
        ) {
            Ok(driver) => Ok(PendingDriverLaunch {
                job,
                bridge,
                driver,
                runner,
            }),
            Err(error) => Err(contain_active_job_then_error(job, runner, None, error)),
        }
    }
}

impl PendingDriverLaunch {
    pub(super) fn resume_and_admit(
        self,
        policy: &SupervisorPolicy,
        start_contract: &VerifiedScenarioStartContract,
        timeout: Duration,
    ) -> Result<AdmittedProtectedChildPair, ProtectedChildLaunchFailure> {
        let Self {
            job,
            bridge,
            driver,
            runner,
        } = self;
        match resume_and_admit_child(
            policy,
            &runner,
            start_contract,
            &job,
            driver,
            ChildBootstrapRole::LifecycleDriver,
            timeout,
        ) {
            Ok(driver) => Ok(AdmittedProtectedChildPair {
                job,
                bridge,
                driver,
                runner,
            }),
            Err(error) => Err(contain_active_job_then_error(job, runner, None, error)),
        }
    }
}

enum OwnedPairCompletionFailure<HeldChildren> {
    Active {
        job: WindowsNativeActiveJob,
        held_children: HeldChildren,
        runner: AffineRestrictedRunnerRunLease,
        error: SupervisorError,
    },
    Terminal {
        held_children: HeldChildren,
        runner: AffineRestrictedRunnerRunLease,
        error: SupervisorError,
    },
}

fn complete_owned_pair_after_terminal_drain<HeldChildren>(
    job: WindowsNativeActiveJob,
    held_children: HeldChildren,
    runner: AffineRestrictedRunnerRunLease,
) -> Result<LaunchContainmentReceipt, OwnedPairCompletionFailure<HeldChildren>> {
    let proof = match job.into_terminal_proof() {
        Ok(proof) => proof,
        Err(failure) => {
            let (job, error) = failure.into_parts();
            return Err(OwnedPairCompletionFailure::Active {
                job,
                held_children,
                runner,
                error,
            });
        }
    };
    runner
        .complete_after_active_terminal_drain(proof)
        .map_err(|(runner, error)| OwnedPairCompletionFailure::Terminal {
            held_children,
            runner,
            error,
        })
}

impl AdmittedProtectedChildPair {
    /// This is the only pair-level completion surface. It consumes the exact
    /// held Job and obtains its opaque one-use proof from live kernel state;
    /// callers cannot supply a receipt, digest, or terminal assertion.
    pub(super) fn complete_after_terminal_drain(
        self,
    ) -> Result<LaunchContainmentReceipt, ProtectedChildPairCompletionFailure> {
        let Self {
            job,
            bridge,
            driver,
            runner,
        } = self;
        match complete_owned_pair_after_terminal_drain(job, (bridge, driver), runner) {
            Ok(receipt) => Ok(receipt),
            Err(OwnedPairCompletionFailure::Active {
                job,
                held_children: (bridge, driver),
                runner,
                error,
            }) => Err(ProtectedChildPairCompletionFailure::Active {
                pair: Self {
                    job,
                    bridge,
                    driver,
                    runner,
                },
                error,
            }),
            Err(OwnedPairCompletionFailure::Terminal {
                held_children: (bridge, driver),
                runner,
                error,
            }) => Err(ProtectedChildPairCompletionFailure::Terminal {
                pair: TerminalProtectedChildPairOwner {
                    bridge,
                    driver,
                    runner,
                },
                error,
            }),
        }
    }
}

fn assemble_pending_child(
    policy: &SupervisorPolicy,
    runner: &AffineRestrictedRunnerRunLease,
    executable: &VerifiedScenarioExecutableLaunch<'_>,
    role_capability_set: RoleCapabilitySetBinding,
    job: &WindowsNativeActiveJob,
    servers: super::child_transport::ParentPipeServers,
    closed_parent_copies: super::child_transport::ParentClientCopiesClosed,
    created: CreatedSuspendedProcess,
) -> Result<PendingProtectedChild, SupervisorError> {
    let role = closed_parent_copies.role();
    let mut child_process = ParentHeldRestrictedChildProcessEvidence::capture(
        role,
        created.process_id,
        runner.expected_runner_sid(),
    )
    .map_err(|error| SupervisorError::new(error.code()))?;
    if child_process.runner_token_digest() != runner.primary_token_digest() {
        return Err(SupervisorError::new(
            "authority_native_child_runner_token_mismatch",
        ));
    }
    let image_receipt = child_process
        .process_image_receipt_identity_digest()
        .map_err(|error| SupervisorError::new(error.code()))?;
    let executable_create_binding = executable
        .bind_created_process_image(
            child_process.process_image_file_for_exact_binding(),
            image_receipt,
        )
        .map_err(|error| SupervisorError::new(error.code()))?;
    let thread_observation = observe_live_created_child_process_and_thread(
        role,
        created.process(),
        created.primary_thread(),
    )
    .map_err(supervisor_error_from_pipe)?;
    let job_observation = job.observe_child_root(
        role,
        thread_observation.process_key(),
        thread_observation.primary_thread_id(),
    )?;
    runner.validate_child_job_observation(&job_observation)?;
    let mut authority = ParentHeldAuthorityServerEvidence::capture_current()
        .map_err(|error| SupervisorError::new(error.code()))?;
    let launch = VerifiedHeldChildLaunch::from_held_observations(ParentHeldChildLaunchInputs {
        closed_parent_copies: &closed_parent_copies,
        child_process: &mut child_process,
        thread_observation,
        role_capability_set: &role_capability_set,
        executable,
        executable_create_binding: &executable_create_binding,
        environment: runner.environment(),
        job: job_observation,
        policy,
        control_server: &mut authority,
    })
    .map_err(supervisor_error_from_handshake)?;
    let transfer = ExclusiveChildEndpointTransfer::from_closed_parent_copies(
        servers.pipe_set_binding_digest(),
        closed_parent_copies,
        launch,
    )
    .map_err(supervisor_error_from_handshake)?;
    let mut private_control = [0u8; 32];
    getrandom::fill(&mut private_control)
        .map_err(|_| SupervisorError::new("authority_native_child_control_random_unavailable"))?;
    let private_control = PrivateControlCapability::take_for_parent(&mut private_control)
        .map_err(|error| SupervisorError::new(error.code()))?;
    let projection = ParentProtocolProjection::prepare_for_held_transfer(
        &transfer,
        policy,
        role_capability_set.clone(),
        private_control,
    )
    .map_err(supervisor_error_from_handshake)?;
    let session = ParentAffineHandshakeSession::from_projection(servers, transfer, projection)
        .map_err(supervisor_error_from_handshake)?;
    Ok(PendingProtectedChild {
        created,
        session,
        child_process,
        authority,
        role_capability_set,
        executable_create_binding,
    })
}

fn resume_and_admit_child(
    policy: &SupervisorPolicy,
    runner: &AffineRestrictedRunnerRunLease,
    start_contract: &VerifiedScenarioStartContract,
    job: &WindowsNativeActiveJob,
    child: PendingProtectedChild,
    role: ChildBootstrapRole,
    timeout: Duration,
) -> Result<AdmittedProtectedChild, SupervisorError> {
    runner.validate_for_active_job(policy, job)?;
    let executable_role = match role {
        ChildBootstrapRole::LifecycleDriver => ScenarioStartExecutableRole::Driver,
        ChildBootstrapRole::BridgeLauncher => ScenarioStartExecutableRole::BridgeLauncher,
    };
    let executable = start_contract
        .prepare_executable_launch(executable_role)
        .map_err(|error| SupervisorError::new(error.code()))?;
    let PendingProtectedChild {
        mut created,
        session,
        mut child_process,
        mut authority,
        role_capability_set,
        executable_create_binding,
    } = child;
    let image_receipt = child_process
        .process_image_receipt_identity_digest()
        .map_err(|error| SupervisorError::new(error.code()))?;
    executable
        .validate_created_process_image(&executable_create_binding, image_receipt)
        .map_err(|error| SupervisorError::new(error.code()))?;
    created.resume_exactly_once()?;
    let admitted = session
        .run(
            timeout,
            ParentHeldChildAdmissionInputs {
                child_process: &mut child_process,
                process: created.process(),
                primary_thread: created.primary_thread(),
                role_capability_set: &role_capability_set,
                executable: &executable,
                executable_create_binding: &executable_create_binding,
                environment: runner.environment(),
                job,
                policy,
                control_server: &mut authority,
            },
        )
        .map_err(supervisor_error_from_handshake)?;
    Ok(AdmittedProtectedChild {
        created,
        session: admitted,
        child_process,
        authority,
        role_capability_set,
        executable_create_binding,
    })
}

fn project_role_capability_set(
    role: ChildBootstrapRole,
    lease: &InheritedClientHandleLease,
    server_process_id: u32,
) -> Result<RoleCapabilitySetBinding, SupervisorError> {
    let raw_handles = lease.inherited_raw_handles().map(|handle| handle as usize);
    project_role_capability_set_from_verified_parent_pipe_contract(
        role,
        raw_handles,
        server_process_id,
    )
    .map_err(|error| SupervisorError::new(error.code()))
}

fn contain_active_job_then_error(
    job: WindowsNativeActiveJob,
    runner: AffineRestrictedRunnerRunLease,
    unattached_child: Option<CreatedSuspendedProcess>,
    original_error: SupervisorError,
) -> ProtectedChildLaunchFailure {
    match job.into_terminal_proof() {
        Ok(proof) => match runner.complete_after_active_terminal_drain(proof) {
            Ok(receipt) => ProtectedChildLaunchFailure::Contained {
                receipt,
                original_error,
            },
            Err((runner, containment_error)) => ProtectedChildLaunchFailure::FaultHeldTerminal {
                unattached_child,
                runner,
                original_error,
                containment_error,
            },
        },
        Err(failure) => {
            let (job, containment_error) = failure.into_parts();
            let containment_error =
                if containment_error.code() == "authority_native_job_terminal_drain_timeout" {
                    SupervisorError::new(CHILD_LAUNCH_CONTAINMENT_TIMEOUT)
                } else {
                    containment_error
                };
            ProtectedChildLaunchFailure::FaultHeldActive {
                job,
                unattached_child,
                runner,
                original_error,
                containment_error,
            }
        }
    }
}

fn supervisor_error_from_pipe(error: ParentPipeError) -> SupervisorError {
    SupervisorError::new(error.code())
}

fn supervisor_error_from_handshake(error: ParentHandshakeError) -> SupervisorError {
    SupervisorError::new(error.code())
}

#[cfg(test)]
mod tests {
    use super::super::child_environment::{
        RUNNER_ENVIRONMENT_ROOTS_ACQUISITION_BLOCKER,
        RUNNER_ENVIRONMENT_ROOTS_LIVE_REVALIDATION_BLOCKER,
    };
    use super::super::native_job::tests::{
        real_active_job_for_policy, real_empty_job_for_policy, real_fault_held_live_job_for_policy,
        real_launch_bindings_for_policy, real_terminal_proof_for_policy,
        real_terminal_proof_without_notifications_for_policy,
    };
    use super::*;
    use crate::primitive_evidence_process_token_windows::RESTRICTED_RUNNER_PRIMARY_TOKEN_ACQUISITION_BLOCKER;
    use std::{
        os::windows::io::{AsHandle, OwnedHandle},
        panic::{catch_unwind, AssertUnwindSafe},
        path::PathBuf,
    };
    use windows_sys::Win32::{
        Foundation::{HANDLE, WAIT_OBJECT_0, WAIT_TIMEOUT},
        System::Threading::{
            OpenProcess, WaitForSingleObject, CREATE_NO_WINDOW, CREATE_SUSPENDED,
            CREATE_UNICODE_ENVIRONMENT, EXTENDED_STARTUPINFO_PRESENT, PROCESS_SYNCHRONIZE,
        },
    };

    const RUNNER_GENERATION: Digest = [0x31; 32];
    const RUNNER_TRANSACTION: Digest = [0x11; 32];
    const RUNNER_ACCOUNT_SID: &str = "S-1-5-21-111-222-333-1001";

    fn policy() -> SupervisorPolicy {
        super::super::runtime_test_policy_with_identity(
            [0x21; 32],
            [0x22; 32],
            [0x23; 32],
            [0x24; 32],
            Some((RUNNER_GENERATION, [0x25; 32], [0x26; 32], [0x27; 32])),
        )
    }

    fn authenticated() -> AuthenticatedRunnerLaunchPolicy {
        AuthenticatedRunnerLaunchPolicy::exact_test_fixture(RUNNER_GENERATION, RUNNER_TRANSACTION)
    }

    fn token_for(canonical_account_sid: &str) -> VerifiedRestrictedRunnerPrimaryTokenCapability {
        let file = std::fs::File::open(std::env::current_exe().unwrap()).unwrap();
        let handle: OwnedHandle = file.into();
        VerifiedRestrictedRunnerPrimaryTokenCapability::exact_test_fixture(
            handle,
            canonical_account_sid,
            [0x91; 32],
        )
    }

    fn authenticated_and_roots() -> (
        AuthenticatedRunnerLaunchPolicy,
        VerifiedRunnerEnvironmentRootsCapability,
    ) {
        let authenticated = authenticated();
        let roots = VerifiedRunnerEnvironmentRootsCapability::exact_test_fixture(&authenticated);
        (authenticated, roots)
    }

    fn authority() -> AuthenticatedRunnerLaunchAuthority {
        let (authenticated, roots) = authenticated_and_roots();
        AuthenticatedRunnerLaunchAuthority::from_authenticated_launch_policy(
            authenticated,
            token_for(RUNNER_ACCOUNT_SID),
            roots,
        )
        .unwrap()
    }

    fn policy_for_run(byte: u8) -> SupervisorPolicy {
        let mut value = policy();
        value.run_binding_digest = [byte; 32];
        value
    }

    fn initial_containment_receipt(process_id: u32) -> NativeUnadmittedRootContainmentReceipt {
        NativeUnadmittedRootContainmentReceipt {
            process_id,
            exact_job_membership_proven: true,
            job_termination_requested: true,
            direct_process_termination_requested: false,
            process_signaled: true,
            exact_empty_terminal_job: true,
        }
    }

    fn decoded_environment(capability: &VerifiedRestrictedRunnerLaunchCapability) -> Vec<String> {
        capability.environment.as_utf16()[..capability.environment.as_utf16().len() - 1]
            .split(|value| *value == 0)
            .filter(|value| !value.is_empty())
            .map(|value| String::from_utf16(value).unwrap())
            .collect()
    }

    struct HeldChildDropProbe(std::sync::Arc<std::sync::atomic::AtomicUsize>);

    impl Drop for HeldChildDropProbe {
        fn drop(&mut self) {
            self.0.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        }
    }

    fn held_child_drop_probes() -> (
        std::sync::Arc<std::sync::atomic::AtomicUsize>,
        (HeldChildDropProbe, HeldChildDropProbe),
    ) {
        let drops = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        (
            std::sync::Arc::clone(&drops),
            (
                HeldChildDropProbe(std::sync::Arc::clone(&drops)),
                HeldChildDropProbe(std::sync::Arc::clone(&drops)),
            ),
        )
    }

    fn open_process_probe(process_id: u32) -> OwnedHandle {
        let raw = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, process_id) };
        assert!(!raw.is_null(), "open an exact process lifetime probe");
        unsafe { OwnedHandle::from_raw_handle(raw as RawHandle) }
    }

    #[derive(Default)]
    struct CountingChildCreationKernel {
        create_count: usize,
    }

    impl ChildProcessCreationKernel for CountingChildCreationKernel {
        fn create(
            &mut self,
            _request: &ExactChildCreationRequest<'_>,
        ) -> Result<PROCESS_INFORMATION, SupervisorError> {
            self.create_count += 1;
            Err(SupervisorError::new(
                "authority_native_child_test_kernel_called",
            ))
        }
    }

    #[test]
    fn privileged_machine_capability_acquisition_and_live_roots_revalidation_stay_closed() {
        assert_eq!(
            VerifiedRestrictedRunnerPrimaryTokenCapability::from_production_machine_readback()
                .unwrap_err()
                .code(),
            RESTRICTED_RUNNER_PRIMARY_TOKEN_ACQUISITION_BLOCKER
        );
        assert_eq!(
            VerifiedRunnerEnvironmentRootsCapability::from_production_machine_readback()
                .unwrap_err()
                .code(),
            RUNNER_ENVIRONMENT_ROOTS_ACQUISITION_BLOCKER
        );
        let (_authenticated, roots) = authenticated_and_roots();
        assert_eq!(
            roots
                .revalidate_live_same_objects_before_launch()
                .unwrap_err()
                .code(),
            RUNNER_ENVIRONMENT_ROOTS_LIVE_REVALIDATION_BLOCKER
        );
    }

    #[test]
    fn generation_authority_mints_a_redacted_minimal_environment_adapter() {
        std::env::set_var("VRCFORGE_TEST_ADAPTER_INJECTION", "must-not-cross");
        let mut authority = authority();
        assert_eq!(
            format!("{authority:?}"),
            "AuthenticatedRunnerLaunchAuthority(<held-and-redacted>)"
        );
        let capability = authority.mint_for_run(&policy()).unwrap();
        let environment = decoded_environment(&capability);
        assert!(environment
            .iter()
            .all(|entry| !entry.contains("VRCFORGE_TEST_ADAPTER_INJECTION")));
        assert!(environment
            .iter()
            .all(|entry| !entry.contains("VRCForgeEvidenceAuthority")));
        assert_eq!(
            capability.authenticated_state_binding.generation,
            RUNNER_GENERATION
        );
        assert_eq!(
            format!("{capability:?}"),
            "VerifiedRestrictedRunnerLaunchCapability(<held-and-redacted>)"
        );
        std::env::remove_var("VRCFORGE_TEST_ADAPTER_INJECTION");
    }

    #[test]
    fn adapter_rejects_wrong_sid_and_invalid_token_capability() {
        let (authenticated, roots) = authenticated_and_roots();
        assert_eq!(
            AuthenticatedRunnerLaunchAuthority::from_authenticated_launch_policy(
                authenticated,
                token_for("S-1-5-21-111-222-333-1002"),
                roots,
            )
            .unwrap_err()
            .code(),
            "authority_native_runner_primary_token_sid_mismatch"
        );

        let (authenticated, roots) = authenticated_and_roots();
        assert_eq!(
            AuthenticatedRunnerLaunchAuthority::from_authenticated_launch_policy(
                authenticated,
                token_for(RUNNER_ACCOUNT_SID).with_token_digest_for_test([0; 32]),
                roots,
            )
            .unwrap_err()
            .code(),
            "authority_runner_primary_token_capability_invalid"
        );
        let mut authority = authority();
        let capability = authority.mint_for_run(&policy()).unwrap();
        assert!(matches!(
            capability.validate_for(&policy()).unwrap_err().code(),
            "child_handshake_process_token_unavailable"
                | "child_handshake_process_token_invalid"
                | "child_handshake_dedicated_runner_token_required"
        ));
    }

    #[test]
    fn cross_run_job_is_rejected_before_pipe_or_kernel_work_for_both_roles() {
        let mut initial_job_policy = policy_for_run(0xb1);
        let initial_job = real_empty_job_for_policy(&mut initial_job_policy);
        let mut initial_runner_policy = initial_job_policy.clone();
        initial_runner_policy.run_binding_digest = [0xb2; 32];
        let mut initial_authority = authority();
        let error = initial_authority
            .mint_for_run(&initial_runner_policy)
            .unwrap()
            .into_affine_run_lease(&initial_runner_policy, &initial_job)
            .unwrap_err();
        assert_eq!(error.code(), RUNNER_JOB_BINDING_INVALID);

        let mut active_job_policy = policy_for_run(0xb3);
        let (active_job, _process_id) = real_active_job_for_policy(&mut active_job_policy);
        let mut active_runner_policy = active_job_policy.clone();
        active_runner_policy.run_binding_digest = [0xb4; 32];
        let mut active_authority = authority();
        let active_runner = active_authority
            .mint_for_run(&active_runner_policy)
            .unwrap()
            .into_affine_run_lease_for_state_test();
        assert_eq!(
            active_runner
                .validate_for_active_job(&active_runner_policy, &active_job)
                .unwrap_err()
                .code(),
            RUNNER_JOB_BINDING_INVALID
        );
        drop(active_job);

        let mut attribute_job_policy = policy_for_run(0xb5);
        let (bridge_binding, driver_binding) =
            real_launch_bindings_for_policy(&mut attribute_job_policy);
        let mut attribute_runner_policy = attribute_job_policy.clone();
        attribute_runner_policy.run_binding_digest = [0xb6; 32];
        let mut attribute_authority = authority();
        let attribute_runner = attribute_authority
            .mint_for_run(&attribute_runner_policy)
            .unwrap()
            .into_affine_run_lease_for_state_test();
        let token = std::fs::File::open(std::env::current_exe().unwrap()).unwrap();
        let application = executable_path_utf16(&std::env::current_exe().unwrap()).unwrap();
        let environment = "A=B\0\0".encode_utf16().collect::<Vec<_>>();
        let working_directory = "C:\\Runner\0".encode_utf16().collect::<Vec<_>>();
        let request = ExactChildCreationRequest {
            primary_token: token.as_handle(),
            application_name: &application,
            environment: &environment,
            working_directory: &working_directory,
            standard_handles: [1usize as HANDLE, 2usize as HANDLE, 3usize as HANDLE],
            creation_flags: FIXED_CHILD_CREATION_FLAGS,
            inherit_handles: true,
            attribute_list: 1usize as *mut c_void,
        };
        let mut kernel = CountingChildCreationKernel::default();
        for binding in [&bridge_binding, &driver_binding] {
            assert_eq!(
                create_suspended_process_from_exact_request_with_kernel(
                    &mut kernel,
                    &attribute_runner_policy,
                    &attribute_runner,
                    binding,
                    &request,
                )
                .unwrap_err()
                .code(),
                RUNNER_JOB_BINDING_INVALID
            );
        }
        assert_eq!(
            kernel.create_count, 0,
            "cross-run bridge and driver bindings must fail before the kernel create call"
        );
    }

    #[test]
    fn adapter_rejects_profile_system_and_install_root_drift() {
        for drift in 0..3 {
            let (authenticated, roots) = authenticated_and_roots();
            let roots = match drift {
                0 => roots.with_profile_root_drift_for_test(),
                1 => roots.with_system_root_drift_for_test(),
                _ => roots.with_install_root_drift_for_test(),
            };
            assert_eq!(
                AuthenticatedRunnerLaunchAuthority::from_authenticated_launch_policy(
                    authenticated,
                    token_for(RUNNER_ACCOUNT_SID),
                    roots,
                )
                .unwrap_err()
                .code(),
                "authority_runner_environment_roots_binding_mismatch"
            );
        }
    }

    #[test]
    fn ordinary_capability_or_live_lease_drop_and_panic_permanently_block_generation() {
        let mut capability_drop_authority = authority();
        let first_policy = policy_for_run(0x87);
        let capability = capability_drop_authority
            .mint_for_run(&first_policy)
            .unwrap();
        drop(capability);
        assert_eq!(
            capability_drop_authority
                .mint_for_run(&policy_for_run(0x88))
                .unwrap_err()
                .code(),
            "authority_native_runner_launch_sequence_active"
        );

        let mut lease_drop_authority = authority();
        let first_policy = policy_for_run(0x89);
        let lease = lease_drop_authority
            .mint_for_run(&first_policy)
            .unwrap()
            .into_affine_run_lease_for_state_test();
        drop(lease);
        assert_eq!(
            lease_drop_authority
                .mint_for_run(&policy_for_run(0x8a))
                .unwrap_err()
                .code(),
            "authority_native_runner_launch_sequence_active"
        );

        let mut panic_authority = authority();
        let first_policy = policy_for_run(0x8b);
        let lease = panic_authority
            .mint_for_run(&first_policy)
            .unwrap()
            .into_affine_run_lease_for_state_test();
        assert!(catch_unwind(AssertUnwindSafe(|| {
            let _live_pending_lease = lease;
            panic!("hostile unwind");
        }))
        .is_err());
        assert_eq!(
            panic_authority
                .mint_for_run(&policy_for_run(0x8c))
                .unwrap_err()
                .code(),
            "authority_native_runner_launch_sequence_active"
        );
    }

    #[test]
    fn only_explicit_typed_containment_burn_allows_a_distinct_sequential_run() {
        let mut completed_authority = authority();
        let first_policy = policy_for_run(0x91);
        let lease = completed_authority
            .mint_for_run(&first_policy)
            .unwrap()
            .into_affine_run_lease_for_state_test();
        let receipt = lease
            .complete_after_initial_containment(initial_containment_receipt(4_201), 4_201)
            .unwrap();
        assert!(matches!(receipt, LaunchContainmentReceipt::Initial(_)));
        assert_eq!(
            completed_authority
                .mint_for_run(&first_policy)
                .unwrap_err()
                .code(),
            "authority_native_runner_launch_sequence_replayed"
        );
        let second_policy = policy_for_run(0x92);
        completed_authority.mint_for_run(&second_policy).unwrap();

        let mut failed_containment_authority = authority();
        let first_policy = policy_for_run(0x93);
        let lease = failed_containment_authority
            .mint_for_run(&first_policy)
            .unwrap()
            .into_affine_run_lease_for_state_test();
        let (lease, error) = lease
            .complete_after_initial_containment(initial_containment_receipt(4_202), 4_203)
            .unwrap_err();
        assert_eq!(
            error.code(),
            "authority_native_runner_initial_containment_invalid"
        );
        drop(lease);
        assert_eq!(
            failed_containment_authority
                .mint_for_run(&policy_for_run(0x94))
                .unwrap_err()
                .code(),
            "authority_native_runner_launch_sequence_active"
        );

        let mut terminal_authority = authority();
        let mut terminal_policy = policy_for_run(0x95);
        let terminal_proof = real_terminal_proof_for_policy(&mut terminal_policy);
        let lease = terminal_authority
            .mint_for_run(&terminal_policy)
            .unwrap()
            .into_affine_run_lease_for_state_test();
        assert!(matches!(
            lease
                .complete_after_active_terminal_drain(terminal_proof)
                .unwrap(),
            LaunchContainmentReceipt::Active(_)
        ));
        terminal_authority
            .mint_for_run(&policy_for_run(0x96))
            .unwrap();
    }

    #[test]
    fn wrong_job_terminal_proof_cannot_clear_the_generation_slot() {
        let mut authority = authority();
        let mut proved_policy = policy_for_run(0x97);
        let terminal_proof = real_terminal_proof_for_policy(&mut proved_policy);
        let mut first_policy = proved_policy.clone();
        first_policy.job_object_id = first_policy.job_object_id.saturating_add(1);
        let lease = authority
            .mint_for_run(&first_policy)
            .unwrap()
            .into_affine_run_lease_for_state_test();
        let (lease, error) = lease
            .complete_after_active_terminal_drain(terminal_proof)
            .unwrap_err();
        assert_eq!(
            error.code(),
            "authority_native_runner_terminal_job_binding_invalid"
        );
        drop(lease);
        assert_eq!(
            authority
                .mint_for_run(&policy_for_run(0x98))
                .unwrap_err()
                .code(),
            "authority_native_runner_launch_sequence_active"
        );
    }

    #[test]
    fn wrong_run_terminal_proof_cannot_clear_the_generation_slot() {
        let mut authority = authority();
        let mut proved_policy = policy_for_run(0x98);
        let terminal_proof = real_terminal_proof_for_policy(&mut proved_policy);
        let mut other_run_policy = proved_policy.clone();
        other_run_policy.run_binding_digest = [0x99; 32];
        let lease = authority
            .mint_for_run(&other_run_policy)
            .unwrap()
            .into_affine_run_lease_for_state_test();
        let (lease, error) = lease
            .complete_after_active_terminal_drain(terminal_proof)
            .unwrap_err();
        assert_eq!(
            error.code(),
            "authority_native_runner_terminal_job_binding_invalid"
        );
        drop(lease);
        assert_eq!(
            authority
                .mint_for_run(&policy_for_run(0x9a))
                .unwrap_err()
                .code(),
            "authority_native_runner_launch_sequence_active"
        );
    }

    #[test]
    fn stripped_real_job_notifications_still_clear_the_active_generation_slot() {
        let mut authority = authority();
        let mut terminal_policy = policy_for_run(0x99);
        let terminal_proof =
            real_terminal_proof_without_notifications_for_policy(&mut terminal_policy);
        let lease = authority
            .mint_for_run(&terminal_policy)
            .unwrap()
            .into_affine_run_lease_for_state_test();
        assert!(matches!(
            lease
                .complete_after_active_terminal_drain(terminal_proof)
                .unwrap(),
            LaunchContainmentReceipt::Active(_)
        ));
        authority.mint_for_run(&policy_for_run(0x9a)).unwrap();
    }

    #[test]
    fn active_pair_completion_failure_retains_every_owner_until_explicit_drop() {
        let mut authority = authority();
        let mut terminal_policy = policy_for_run(0x9b);
        let (job, process_id) = real_fault_held_live_job_for_policy(&mut terminal_policy);
        let process_probe = open_process_probe(process_id);
        assert_eq!(
            unsafe { WaitForSingleObject(process_probe.as_raw_handle().cast(), 0) },
            WAIT_TIMEOUT,
            "the hostile live root starts held by the exact Job"
        );
        let runner = authority
            .mint_for_run(&terminal_policy)
            .unwrap()
            .into_affine_run_lease_for_state_test();
        let (drops, held_children) = held_child_drop_probes();
        let failure = complete_owned_pair_after_terminal_drain(job, held_children, runner)
            .expect_err("fault-held live Job must return typed active ownership");
        match &failure {
            OwnedPairCompletionFailure::Active {
                held_children,
                error,
                ..
            } => {
                let _both_children_still_held = (&held_children.0, &held_children.1);
                assert_eq!(error.code(), "authority_native_job_terminal_fault_held");
            }
            OwnedPairCompletionFailure::Terminal { .. } => {
                panic!("a live Job failure cannot be projected as terminal")
            }
        }
        assert_eq!(drops.load(std::sync::atomic::Ordering::SeqCst), 0);
        assert_eq!(
            unsafe { WaitForSingleObject(process_probe.as_raw_handle().cast(), 0) },
            WAIT_TIMEOUT,
            "the typed Active failure still owns the live kill-on-close Job"
        );
        assert_eq!(
            authority
                .mint_for_run(&policy_for_run(0x9c))
                .unwrap_err()
                .code(),
            "authority_native_runner_launch_sequence_active"
        );

        drop(failure);
        assert_eq!(drops.load(std::sync::atomic::Ordering::SeqCst), 2);
        assert_eq!(
            unsafe { WaitForSingleObject(process_probe.as_raw_handle().cast(), 5_000) },
            WAIT_OBJECT_0,
            "explicit failure drop closes the held Job and contains its live root"
        );
        assert_eq!(
            authority
                .mint_for_run(&policy_for_run(0x9d))
                .unwrap_err()
                .code(),
            "authority_native_runner_launch_sequence_active",
            "drop containment cannot report the unverified run complete"
        );
    }

    #[test]
    fn terminal_pair_completion_failure_retains_runner_and_children_without_clearing_slot() {
        let mut authority = authority();
        let mut proved_policy = policy_for_run(0xa1);
        let (job, process_id) = real_active_job_for_policy(&mut proved_policy);
        let process_probe = open_process_probe(process_id);
        let mut wrong_runner_policy = proved_policy.clone();
        wrong_runner_policy.job_object_id = wrong_runner_policy.job_object_id.saturating_add(1);
        let runner = authority
            .mint_for_run(&wrong_runner_policy)
            .unwrap()
            .into_affine_run_lease_for_state_test();
        let (drops, held_children) = held_child_drop_probes();
        let failure = complete_owned_pair_after_terminal_drain(job, held_children, runner)
            .expect_err("wrong runner binding must return typed terminal ownership");
        match &failure {
            OwnedPairCompletionFailure::Terminal {
                held_children,
                error,
                ..
            } => {
                let _both_children_still_held = (&held_children.0, &held_children.1);
                assert_eq!(
                    error.code(),
                    "authority_native_runner_terminal_job_binding_invalid"
                );
            }
            OwnedPairCompletionFailure::Active { .. } => {
                panic!("a consumed terminal proof cannot retain a live Job")
            }
        }
        assert_eq!(drops.load(std::sync::atomic::Ordering::SeqCst), 0);
        assert_eq!(
            unsafe { WaitForSingleObject(process_probe.as_raw_handle().cast(), 0) },
            WAIT_OBJECT_0,
            "Terminal failure is issued only after exact Job containment"
        );
        assert_eq!(
            authority
                .mint_for_run(&policy_for_run(0xa2))
                .unwrap_err()
                .code(),
            "authority_native_runner_launch_sequence_active"
        );
        drop(failure);
        assert_eq!(drops.load(std::sync::atomic::Ordering::SeqCst), 2);
        assert_eq!(
            authority
                .mint_for_run(&policy_for_run(0xa3))
                .unwrap_err()
                .code(),
            "authority_native_runner_launch_sequence_active"
        );
    }

    #[test]
    fn runner_generation_authority_and_run_capability_are_noncloneable_owners() {
        let _product_constructor =
            AuthenticatedRunnerLaunchAuthority::from_authenticated_final_commit_boundary;
        assert!(std::mem::needs_drop::<AuthenticatedRunnerLaunchPolicy>());
        assert!(std::mem::needs_drop::<AuthenticatedRunnerLaunchAuthority>());
        assert!(std::mem::needs_drop::<
            VerifiedRestrictedRunnerLaunchCapability,
        >());
        assert!(std::mem::needs_drop::<AffineRestrictedRunnerRunLease>());
        let bootstrap_source = include_str!("../primitive_evidence_authority_install/bootstrap.rs");
        let launcher_source = include_str!("child_launcher.rs");
        let bootstrap_production = bootstrap_source
            .split("#[cfg(test)]\nmod tests")
            .next()
            .expect("bootstrap production source");
        let launcher_production = launcher_source
            .split("#[cfg(test)]\nmod tests")
            .next()
            .expect("launcher production source");
        assert!(bootstrap_production.contains("pub(crate) fn take_runner_launch_policy("));
        assert!(bootstrap_production.contains("capability.read_once()?.into_launch_policy()?"));
        assert!(!bootstrap_production
            .contains("derive(Clone)\npub(crate) struct AuthenticatedRunnerLaunchPolicy"));
        assert_eq!(
            launcher_production
                .matches("pub(super) fn from_authenticated_final_commit_boundary(")
                .count(),
            1
        );
        assert!(!launcher_production.contains("pub(super) fn from_authenticated_launch_policy("));
        assert!(!launcher_production
            .contains("derive(Clone)\npub(super) struct AuthenticatedRunnerLaunchAuthority"));
        assert!(!launcher_production
            .contains("derive(Clone)\npub(super) struct VerifiedRestrictedRunnerLaunchCapability"));
        assert!(!launcher_production
            .contains("derive(Clone)\npub(super) struct AffineRestrictedRunnerRunLease"));
        assert!(
            !launcher_production.contains("impl Drop for VerifiedRestrictedRunnerLaunchCapability")
        );
    }

    #[test]
    fn pending_admitted_and_both_fault_held_branches_retain_the_affine_lease() {
        let launcher_source = include_str!("child_launcher.rs");
        let production = launcher_source
            .split("#[cfg(test)]\nmod tests")
            .next()
            .expect("launcher production source");
        let preparation = production
            .split("impl PendingBridgeLaunch")
            .nth(1)
            .expect("pending bridge implementation")
            .split("impl AdmittedBridgeLaunch")
            .next()
            .expect("pending bridge slice");
        assert!(preparation.contains("runner: VerifiedRestrictedRunnerLaunchCapability,"));
        assert!(!preparation.contains("runner: &VerifiedRestrictedRunnerLaunchCapability,"));
        assert!(preparation.contains("runner.into_affine_run_lease(policy, &job)?"));

        for owner in [
            "PendingBridgeLaunch",
            "AdmittedBridgeLaunch",
            "PendingDriverLaunch",
            "AdmittedProtectedChildPair",
        ] {
            let declaration = production
                .split(&format!("pub(super) struct {owner}"))
                .nth(1)
                .unwrap_or_else(|| panic!("missing {owner}"))
                .split('}')
                .next()
                .expect("state declaration");
            assert!(
                declaration.contains("runner: AffineRestrictedRunnerRunLease,"),
                "{owner} lost the affine runner lease"
            );
        }

        let failure = production
            .split("pub(super) enum ProtectedChildLaunchFailure")
            .nth(1)
            .expect("launch failure declaration");
        for branch in ["FaultHeldActive", "FaultHeldInitial", "FaultHeldTerminal"] {
            let declaration = failure
                .split(&format!("{branch} {{"))
                .nth(1)
                .unwrap_or_else(|| panic!("missing {branch}"))
                .split('}')
                .next()
                .expect("fault-held declaration");
            assert!(
                declaration.contains("runner: AffineRestrictedRunnerRunLease,"),
                "{branch} lost the affine runner lease"
            );
        }

        let drop_impl = production
            .split("impl Drop for AffineRestrictedRunnerRunLease")
            .nth(1)
            .expect("affine lease drop contract")
            .split("impl AffineRestrictedRunnerRunLease")
            .next()
            .expect("affine lease drop body");
        assert!(!drop_impl.contains("active_run_binding"));
        assert!(!drop_impl.contains("completed_run_bindings"));
        assert_eq!(
            production
                .matches("state.active_run_binding = None;")
                .count(),
            1
        );
        assert!(!production.contains("NativeJobTerminalDrainReceipt"));
        let pair_completion = production
            .split("pub(super) fn complete_after_terminal_drain")
            .nth(1)
            .expect("pair terminal completion")
            .split("fn assemble_pending_child")
            .next()
            .expect("pair terminal completion body");
        assert!(!pair_completion.contains("receipt:"));
        assert!(pair_completion.contains("complete_owned_pair_after_terminal_drain("));
        assert!(pair_completion.contains("ProtectedChildPairCompletionFailure::Active"));
        assert!(pair_completion.contains("ProtectedChildPairCompletionFailure::Terminal"));
        let owned_pair_completion = production
            .split("fn complete_owned_pair_after_terminal_drain")
            .nth(1)
            .expect("owned pair terminal completion")
            .split("impl AdmittedProtectedChildPair")
            .next()
            .expect("owned pair terminal completion body");
        assert!(owned_pair_completion.contains("let proof = match job.into_terminal_proof()"));
        assert!(owned_pair_completion.contains("let (job, error) = failure.into_parts();"));
        assert!(owned_pair_completion.contains("OwnedPairCompletionFailure::Active"));
        assert!(owned_pair_completion.contains("OwnedPairCompletionFailure::Terminal"));
        assert!(!production.contains("map_err(|failure| failure.into_parts().1)"));
        let typed_pair_failure = production
            .split("pub(super) enum ProtectedChildPairCompletionFailure")
            .nth(1)
            .expect("typed pair completion failure")
            .split("pub(super) enum ProtectedChildLaunchFailure")
            .next()
            .expect("typed pair completion failure body");
        assert!(typed_pair_failure.contains("Active {"));
        assert!(typed_pair_failure.contains("pair: AdmittedProtectedChildPair"));
        assert!(typed_pair_failure.contains("Terminal {"));
        assert!(typed_pair_failure.contains("pair: TerminalProtectedChildPairOwner"));
        assert!(typed_pair_failure.contains("pub(super) fn into_parts(self)"));
        assert!(!typed_pair_failure.contains("derive(Clone"));

        let native_source = include_str!("native_job.rs");
        let native_production = native_source
            .split("#[cfg(test)]\npub(super) mod tests")
            .next()
            .expect("native Job production source");
        assert!(native_production.contains("struct NativeJobTerminalDrainReceipt {"));
        assert!(!native_production.contains("pub(super) struct NativeJobTerminalDrainReceipt"));
        assert!(native_production.contains("pub(super) struct NativeJobTerminalProof {"));
        assert!(!production.contains("NativeJobTerminalProof {"));
        assert_eq!(
            native_production
                .matches("return Ok(NativeJobTerminalProof {")
                .count(),
            1,
            "only the consuming held-Job transition may construct a proof"
        );
        let proof_declaration = native_production
            .split("pub(super) struct NativeJobTerminalProof {")
            .nth(1)
            .expect("opaque terminal proof")
            .split('}')
            .next()
            .expect("opaque terminal proof fields");
        assert!(!proof_declaration.contains("pub(super)"));
        let key_slice = native_production
            .split("struct NativeJobTerminalProofKey(Digest);")
            .nth(1)
            .expect("per-instance terminal proof key")
            .split("#[derive(Clone, PartialEq, Eq)]\nstruct NativeJobTerminalDrainReceipt")
            .next()
            .expect("terminal proof key implementation");
        assert!(key_slice.contains("getrandom::fill(&mut value)"));
        assert!(key_slice.contains("impl Drop for NativeJobTerminalProofKey"));
        assert!(key_slice.contains("fn volatile_zero_terminal_key(value: &mut Digest)"));
        assert!(key_slice.contains("write_volatile(byte, 0)"));
        assert!(key_slice.contains("compiler_fence(Ordering::SeqCst)"));
        assert!(key_slice.contains("volatile_zero_terminal_key(&mut self.0);"));
        assert!(!key_slice.contains("self.0.fill(0);"));
        assert!(!key_slice.contains("derive(Clone"));
        assert!(!key_slice.contains("impl fmt::Debug for NativeJobTerminalProofKey"));
        let terminal_readback = native_production
            .split("fn root_process_terminal_readback_digest(")
            .nth(1)
            .expect("held process terminal readback")
            .split("enum NativeJobCompletionKind")
            .next()
            .expect("held process terminal readback body");
        for binding in [
            "process.terminal_exit_code()?",
            "membership.role.wire_value()",
            "process_id.to_be_bytes()",
            "process.creation_time.to_be_bytes()",
            "process.epoch_digest",
            "exit_code.to_be_bytes()",
        ] {
            assert!(terminal_readback.contains(binding), "missing {binding}");
        }
        assert!(native_production.contains("GetExitCodeProcess(self.handle.raw(), &mut exit_code)"));
        assert!(native_production.contains("exit_code == STILL_ACTIVE as u32"));
        assert!(native_production.contains("root_process_terminal_readback_digest: Digest,"));
        assert!(native_production.contains("completion_transcript_digest: Digest,"));
        let completed_validation = native_production
            .split("fn validate_completed_kernel_state(")
            .nth(1)
            .expect("completed live-kernel validation")
            .split("fn finish_terminal_drain(")
            .next()
            .expect("completed live-kernel validation body");
        assert_eq!(
            completed_validation
                .matches("query_stable_terminal_snapshot(root_process_count)")
                .count(),
            2,
            "proof issuance keeps two stable empty-roster observations"
        );
        assert!(completed_validation.contains(
            "first_terminal_process_readback != receipt.root_process_terminal_readback_digest"
        ));
        assert!(completed_validation.contains(
            "self.roster.completion_transcript_digest != receipt.completion_transcript_digest"
        ));
    }

    #[test]
    fn source_contract_keeps_runner_job_joins_before_pipe_create_kernel_create_and_resume() {
        let source = include_str!("child_launcher.rs");
        let production = source
            .split("#[cfg(test)]\nmod tests")
            .next()
            .expect("launcher production source");

        let bridge_prepare = production
            .split("impl PendingBridgeLaunch")
            .nth(1)
            .expect("bridge preparation")
            .split("impl AdmittedBridgeLaunch")
            .next()
            .expect("bridge preparation body");
        assert!(
            bridge_prepare
                .find("runner.into_affine_run_lease(policy, &job)?")
                .unwrap()
                < bridge_prepare.find("ParentPipeSet::create").unwrap()
        );

        let driver_prepare = production
            .split("impl AdmittedBridgeLaunch")
            .nth(1)
            .expect("driver preparation")
            .split("impl PendingDriverLaunch")
            .next()
            .expect("driver preparation body");
        assert!(
            driver_prepare
                .find("runner.validate_for_active_job(policy, &job)")
                .unwrap()
                < driver_prepare.find("ParentPipeSet::create").unwrap()
        );

        let kernel_dispatch = production
            .split("fn create_suspended_process_from_exact_request_with_kernel")
            .nth(1)
            .expect("kernel dispatch")
            .split("fn create_suspended_process_as_restricted_runner")
            .next()
            .expect("kernel dispatch body");
        assert!(
            kernel_dispatch
                .find("runner.validate_for_launch_attributes(policy, launch_binding)?")
                .unwrap()
                < kernel_dispatch.find("kernel.create(request)?").unwrap()
        );

        let resume = production
            .split("fn resume_and_admit_child")
            .nth(1)
            .expect("resume path")
            .split("fn project_role_capability_set")
            .next()
            .expect("resume path body");
        assert!(
            resume
                .find("runner.validate_for_active_job(policy, job)?")
                .unwrap()
                < resume.find("created.resume_exactly_once()?").unwrap()
        );
    }

    #[test]
    fn exact_create_request_rejects_null_command_surrogates_and_weak_material() {
        let token = std::fs::File::open(std::env::current_exe().unwrap()).unwrap();
        let application = executable_path_utf16(&std::env::current_exe().unwrap()).unwrap();
        let environment = "A=B\0\0".encode_utf16().collect::<Vec<_>>();
        let working_directory = "C:\\Runner\0".encode_utf16().collect::<Vec<_>>();
        let handles = [1usize as HANDLE, 2usize as HANDLE, 3usize as HANDLE];
        let valid_flags = CREATE_SUSPENDED
            | CREATE_UNICODE_ENVIRONMENT
            | CREATE_NO_WINDOW
            | EXTENDED_STARTUPINFO_PRESENT;
        let request = ExactChildCreationRequest {
            primary_token: token.as_handle(),
            application_name: &application,
            environment: &environment,
            working_directory: &working_directory,
            standard_handles: handles,
            creation_flags: valid_flags,
            inherit_handles: true,
            attribute_list: 1usize as *mut c_void,
        };
        request.validate().unwrap();

        let mut bad_application = application.clone();
        bad_application.insert(1, 0);
        assert_eq!(
            ExactChildCreationRequest {
                application_name: &bad_application,
                ..request
            }
            .validate()
            .unwrap_err()
            .code(),
            CHILD_CREATE_REQUEST_INVALID
        );
        assert_eq!(
            ExactChildCreationRequest {
                standard_handles: [handles[0], handles[0], handles[2]],
                ..request
            }
            .validate()
            .unwrap_err()
            .code(),
            CHILD_CREATE_REQUEST_INVALID
        );
        assert_eq!(
            ExactChildCreationRequest {
                inherit_handles: false,
                ..request
            }
            .validate()
            .unwrap_err()
            .code(),
            CHILD_CREATE_REQUEST_INVALID
        );
    }

    #[test]
    fn executable_path_is_absolute_single_nul_and_bounded() {
        let current = std::env::current_exe().unwrap();
        let encoded = executable_path_utf16(&current).unwrap();
        assert_eq!(encoded.last(), Some(&0));
        assert!(!encoded[..encoded.len() - 1].contains(&0));
        assert_eq!(
            executable_path_utf16(&PathBuf::from("relative.exe"))
                .unwrap_err()
                .code(),
            CHILD_CREATE_REQUEST_INVALID
        );
    }
}
