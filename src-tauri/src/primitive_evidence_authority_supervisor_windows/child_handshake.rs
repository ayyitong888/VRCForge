//! Parent-owned authenticated startup for the fixed protected child transport.
//!
//! Production launch evidence is intentionally unavailable in this slice. The
//! state machine is complete and fail-closed, but only a future held-handle
//! adapter may construct the evidence that enters it.

#![cfg(windows)]
#![cfg_attr(not(test), allow(dead_code))]

use super::{
    child_environment::CanonicalChildEnvironmentBlock,
    child_transport::{
        observe_live_created_child_process_and_thread, CreatedChildLaunchAttributeBindingDigest,
        CreatedChildPrimaryThreadBindingDigest, CreatedChildProcessThreadObservation,
        ParentClientCopiesClosed, ParentControlPipe, ParentPipeError, ParentPipeServers,
        ParentPipeSetBindingDigest, ParentResultPipe,
    },
    native_job::{NativeChildJobObservation, WindowsNativeActiveJob},
    ProcessKey, ProcessRole, SupervisorPolicy,
};
use crate::primitive_evidence_authority_pipe::{
    ScenarioStartExecutableRole, VerifiedScenarioExecutableCreateBinding,
    VerifiedScenarioExecutableLaunch,
};
use crate::primitive_evidence_child_protocol::{
    windows_child_handshake::{
        child_transport_observation_digest_from_parts, observed_launch_source_digest,
        ParentHeldAuthorityServerEvidence, ParentHeldRestrictedChildProcessEvidence,
    },
    AuthorityBindingDigest, AuthorityChildExecutionContext, AuthorityHandshakeNonce,
    BootstrapDigest, ChildBootstrapBindings, ChildBootstrapRole, ChildImageContextDigest,
    ChildObservedLaunchContextDigest, ChildProtocolError, ChildTransportContractContextDigest,
    ControlServerIdentityContextDigest, FinalGenerationContextDigest, GlobalCapabilitySetDigest,
    JobMembershipEpochContextDigest, MinimalEnvironmentContextDigest,
    ParentChildBootstrapExpectations, ParentPreparedChildBootstrapFrame, PolicyBindingDigest,
    PreparedExpectationEnvelope, PrivateControlCapability, PrivateControlCapabilityCommitment,
    ReceivedBootstrapAck, ReceivedChildReady, RoleCapabilitySetBinding, RoleRawHandleListDigest,
    RunBindingDigest, RunnerTokenContextDigest, StartContractContextDigest, TicketBindingDigest,
    CHILD_BOOTSTRAP_ACK_LEN, CHILD_BOOTSTRAP_FRAME_LEN, CHILD_EXPECTATION_ENVELOPE_LEN,
    CHILD_READY_MESSAGE_LEN,
};
use sha2::{Digest as Sha2Digest, Sha256};
use std::{
    fmt,
    io::{self, Cursor, Write},
    os::windows::io::BorrowedHandle,
    ptr,
    sync::atomic::{compiler_fence, Ordering},
    time::{Duration, Instant},
};

const HANDSHAKE_SESSION_BINDING_DOMAIN: &[u8] =
    b"vrcforge-parent-child-handshake-session-binding-v1\0";
const ENDPOINT_TRANSFER_BINDING_DOMAIN: &[u8] =
    b"vrcforge-parent-child-endpoint-transfer-binding-v1\0";
const HELD_ADMISSION_BINDING_DOMAIN: &[u8] = b"vrcforge-parent-child-held-admission-binding-v1\0";
const HELD_ADMISSION_REVALIDATION_SOURCE_DOMAIN: &[u8] =
    b"vrcforge-parent-child-held-admission-revalidation-source-v1\0";
const HELD_LAUNCH_SESSION_SOURCE_DOMAIN: &[u8] =
    b"vrcforge-parent-child-held-launch-session-source-v1\0";
const PRODUCTION_EVIDENCE_BLOCKER: &str =
    "parent_child_handshake_production_evidence_not_connected";

macro_rules! define_parent_binding_digest {
    ($name:ident, $domain:ident, $error:literal) => {
        #[derive(Clone, Copy, PartialEq, Eq)]
        struct $name(BootstrapDigest);

        impl $name {
            fn derive(source: &BootstrapDigest) -> Result<Self, ParentHandshakeError> {
                if digest_is_zero(source) {
                    return Err(ParentHandshakeError::Binding($error));
                }
                let mut digest = Sha256::new();
                digest.update($domain);
                digest.update((source.len() as u16).to_be_bytes());
                digest.update(source);
                let value = Self(digest.finalize().into());
                if digest_is_zero(&value.0) {
                    return Err(ParentHandshakeError::Binding($error));
                }
                Ok(value)
            }

            fn as_bytes(&self) -> &BootstrapDigest {
                &self.0
            }
        }

        impl fmt::Debug for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str(concat!(stringify!($name), "(<redacted>)"))
            }
        }
    };
}

define_parent_binding_digest!(
    HandshakeSessionBindingDigest,
    HANDSHAKE_SESSION_BINDING_DOMAIN,
    "parent_child_handshake_session_binding_invalid"
);

#[derive(Debug)]
pub(super) enum ParentHandshakeError {
    Transport(ParentPipeError),
    Protocol(ChildProtocolError),
    Binding(&'static str),
}

impl ParentHandshakeError {
    pub(super) fn code(&self) -> &'static str {
        match self {
            Self::Transport(error) => error.code(),
            Self::Protocol(error) => error.code(),
            Self::Binding(code) => code,
        }
    }

    /// Preserves the native transport's quarantine signal without reducing it
    /// to a protocol or generic I/O code.
    pub(super) fn requires_session_containment(&self) -> bool {
        matches!(self, Self::Transport(error) if error.requires_session_containment())
    }

    /// Every failure happens after a child process was created and therefore
    /// requires the caller to keep that child suspended or terminate it.
    pub(super) const fn requires_child_termination(&self) -> bool {
        true
    }
}

impl fmt::Display for ParentHandshakeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}

impl std::error::Error for ParentHandshakeError {}

impl From<ParentPipeError> for ParentHandshakeError {
    fn from(error: ParentPipeError) -> Self {
        Self::Transport(error)
    }
}

impl From<ChildProtocolError> for ParentHandshakeError {
    fn from(error: ChildProtocolError) -> Self {
        Self::Protocol(error)
    }
}

/// Held launch evidence from sources independent of child-provided bytes.
/// There is deliberately no successful production constructor yet.
pub(super) struct VerifiedHeldChildLaunch {
    role: ChildBootstrapRole,
    process_key: ProcessKey,
    primary_thread: CreatedChildPrimaryThreadBindingDigest,
    image: ChildImageContextDigest,
    token: RunnerTokenContextDigest,
    environment: MinimalEnvironmentContextDigest,
    environment_authority_binding: BootstrapDigest,
    runner_profile_digest: BootstrapDigest,
    job_membership: JobMembershipEpochContextDigest,
    launch_attributes: CreatedChildLaunchAttributeBindingDigest,
    final_generation: FinalGenerationContextDigest,
    child_transport: ChildTransportContractContextDigest,
    start_contract: StartContractContextDigest,
    control_server: ControlServerIdentityContextDigest,
    observed_launch: ChildObservedLaunchContextDigest,
    session_binding: HandshakeSessionBindingDigest,
}

pub(super) struct ParentHeldChildLaunchInputs<'a, 'executable> {
    pub(super) closed_parent_copies: &'a ParentClientCopiesClosed,
    pub(super) child_process: &'a mut ParentHeldRestrictedChildProcessEvidence,
    pub(super) thread_observation: CreatedChildProcessThreadObservation,
    pub(super) role_capability_set: &'a RoleCapabilitySetBinding,
    pub(super) executable: &'a VerifiedScenarioExecutableLaunch<'executable>,
    pub(super) executable_create_binding: &'a VerifiedScenarioExecutableCreateBinding,
    pub(super) environment: &'a CanonicalChildEnvironmentBlock,
    pub(super) job: NativeChildJobObservation,
    pub(super) policy: &'a SupervisorPolicy,
    pub(super) control_server: &'a mut ParentHeldAuthorityServerEvidence,
}

pub(super) struct ParentHeldChildAdmissionInputs<'a, 'executable> {
    pub(super) child_process: &'a mut ParentHeldRestrictedChildProcessEvidence,
    pub(super) process: BorrowedHandle<'a>,
    pub(super) primary_thread: BorrowedHandle<'a>,
    pub(super) role_capability_set: &'a RoleCapabilitySetBinding,
    pub(super) executable: &'a VerifiedScenarioExecutableLaunch<'executable>,
    pub(super) executable_create_binding: &'a VerifiedScenarioExecutableCreateBinding,
    pub(super) environment: &'a CanonicalChildEnvironmentBlock,
    pub(super) job: &'a WindowsNativeActiveJob,
    pub(super) policy: &'a SupervisorPolicy,
    pub(super) control_server: &'a mut ParentHeldAuthorityServerEvidence,
}

impl fmt::Debug for VerifiedHeldChildLaunch {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VerifiedHeldChildLaunch")
            .field("role", &self.role)
            .field("process", &"<held-and-redacted>")
            .field("bindings", &"<redacted>")
            .finish()
    }
}

impl VerifiedHeldChildLaunch {
    pub(super) fn from_production_observations() -> Result<Self, ParentHandshakeError> {
        Err(ParentHandshakeError::Binding(PRODUCTION_EVIDENCE_BLOCKER))
    }

    pub(super) fn from_held_observations(
        inputs: ParentHeldChildLaunchInputs<'_, '_>,
    ) -> Result<Self, ParentHandshakeError> {
        let ParentHeldChildLaunchInputs {
            closed_parent_copies,
            child_process,
            thread_observation,
            role_capability_set,
            executable,
            executable_create_binding,
            environment,
            job,
            policy,
            control_server,
        } = inputs;
        closed_parent_copies.validate()?;
        child_process
            .revalidate()
            .map_err(|error| ParentHandshakeError::Binding(error.code()))?;
        control_server
            .revalidate()
            .map_err(|error| ParentHandshakeError::Binding(error.code()))?;
        let process_image_receipt_identity_digest = child_process
            .process_image_receipt_identity_digest()
            .map_err(|error| ParentHandshakeError::Binding(error.code()))?;
        executable
            .validate_created_process_image(
                executable_create_binding,
                process_image_receipt_identity_digest,
            )
            .map_err(|error| ParentHandshakeError::Binding(error.code()))?;
        let role = closed_parent_copies.role();
        let process_role = match role {
            ChildBootstrapRole::LifecycleDriver => ProcessRole::Driver,
            ChildBootstrapRole::BridgeLauncher => ProcessRole::BridgeLauncher,
        };
        let executable_role = match role {
            ChildBootstrapRole::LifecycleDriver => ScenarioStartExecutableRole::Driver,
            ChildBootstrapRole::BridgeLauncher => ScenarioStartExecutableRole::BridgeLauncher,
        };
        let process_key = closed_parent_copies.process_key();
        let primary_thread_id = thread_observation.primary_thread_id();
        let primary_thread_creation_time = thread_observation.primary_thread_creation_time();
        let primary_thread = thread_observation.primary_thread_binding();
        if child_process.role() != role
            || child_process.process_id() != process_key.pid
            || child_process.process_creation_time() != process_key.creation_time
            || thread_observation.role() != role
            || thread_observation.process_key() != process_key
            || primary_thread != closed_parent_copies.primary_thread_binding()
            || role_capability_set.role() != role
            || role_capability_set.raw_handle_list_digest()
                != closed_parent_copies.raw_handle_list_digest().as_bytes()
            || executable.role() != executable_role
            || executable.expected_content_digest()
                != policy.process_executable_digests[super::role_index(process_role)]
            || child_process.image_content_digest() != &executable.expected_content_digest()
            || environment.runner_profile_digest() != &policy.runner_profile_digest
            || job.role() != role
            || job.process_key() != process_key
            || job.primary_thread_id() != primary_thread_id
            || job.authority_generation_digest() != &policy.authority_generation_digest
            || control_server.process_id() != policy.authority_process.pid
            || control_server.process_creation_time() != policy.authority_process.creation_time
            || control_server.process_id() == process_key.pid
            || digest_is_zero(environment.observation_digest())
            || digest_is_zero(job.observation_digest())
            || digest_is_zero(job.membership_epoch_source())
            || digest_is_zero(&policy.authority_generation_digest)
            || digest_is_zero(&executable.start_contract_digest())
        {
            return Err(ParentHandshakeError::Binding(
                "parent_child_held_observation_unexpected",
            ));
        }
        let child_transport_source = child_transport_observation_digest_from_parts(
            role,
            control_server.process_id(),
            role_capability_set,
            closed_parent_copies.raw_handle_list_digest(),
        )
        .map_err(|error| ParentHandshakeError::Binding(error.code()))?;
        let control_server_source = control_server
            .observation_digest()
            .map_err(|error| ParentHandshakeError::Binding(error.code()))?;
        let environment_observation_digest = *environment.observation_digest();
        let environment_authority_binding = *environment.binding_digest();
        let runner_profile_digest = *environment.runner_profile_digest();
        let token = RunnerTokenContextDigest::derive(child_process.runner_token_digest())?;
        let image = ChildImageContextDigest::derive(child_process.image_measurement_digest())?;
        let environment = MinimalEnvironmentContextDigest::derive(&environment_observation_digest)?;
        let job_membership =
            JobMembershipEpochContextDigest::derive(job.membership_epoch_source())?;
        let launch_attributes = closed_parent_copies.launch_attribute_binding();
        let final_generation =
            FinalGenerationContextDigest::derive(&policy.authority_generation_digest)?;
        let child_transport = ChildTransportContractContextDigest::derive(&child_transport_source)?;
        let start_contract =
            StartContractContextDigest::derive(&executable.start_contract_digest())?;
        let control_server = ControlServerIdentityContextDigest::derive(&control_server_source)?;
        let observed_launch_source = observed_launch_source_digest(
            role,
            process_key.pid,
            process_key.creation_time,
            primary_thread_id,
            primary_thread_creation_time,
            &child_transport_source,
            child_process.runner_token_digest(),
            child_process.image_measurement_digest(),
            &environment_observation_digest,
            &control_server_source,
            job.observation_digest(),
        );
        let observed_launch = ChildObservedLaunchContextDigest::derive(&observed_launch_source)?;
        let mut session_source = Sha256::new();
        session_source.update(HELD_LAUNCH_SESSION_SOURCE_DOMAIN);
        session_source.update([role.wire_value()]);
        session_source.update(process_key.pid.to_be_bytes());
        session_source.update(process_key.creation_time.to_be_bytes());
        for value in [
            primary_thread.as_bytes(),
            image.as_bytes(),
            token.as_bytes(),
            environment.as_bytes(),
            &environment_authority_binding,
            &runner_profile_digest,
            job_membership.as_bytes(),
            launch_attributes.as_bytes(),
            final_generation.as_bytes(),
            child_transport.as_bytes(),
            start_contract.as_bytes(),
            control_server.as_bytes(),
            observed_launch.as_bytes(),
            closed_parent_copies.created_child_binding_digest(),
            closed_parent_copies.closure_binding_digest(),
        ] {
            session_source.update(value);
        }
        let session_binding =
            HandshakeSessionBindingDigest::derive(&session_source.finalize().into())?;
        let value = Self {
            role,
            process_key,
            primary_thread,
            image,
            token,
            environment,
            environment_authority_binding,
            runner_profile_digest,
            job_membership,
            launch_attributes,
            final_generation,
            child_transport,
            start_contract,
            control_server,
            observed_launch,
            session_binding,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), ParentHandshakeError> {
        if self.process_key.pid == 0 || self.process_key.creation_time == 0 {
            return Err(ParentHandshakeError::Binding(
                "parent_child_held_process_key_invalid",
            ));
        }
        let digests = [
            self.primary_thread.as_bytes(),
            self.image.as_bytes(),
            self.token.as_bytes(),
            self.environment.as_bytes(),
            &self.environment_authority_binding,
            &self.runner_profile_digest,
            self.job_membership.as_bytes(),
            self.launch_attributes.as_bytes(),
            self.final_generation.as_bytes(),
            self.child_transport.as_bytes(),
            self.start_contract.as_bytes(),
            self.control_server.as_bytes(),
            self.observed_launch.as_bytes(),
            self.session_binding.as_bytes(),
        ];
        if digests.iter().any(|digest| digest_is_zero(digest))
            || digests.iter().enumerate().any(|(index, digest)| {
                digests[..index]
                    .iter()
                    .any(|prior| digest_equal(digest, prior))
            })
        {
            return Err(ParentHandshakeError::Binding(
                "parent_child_held_launch_binding_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn execution_context(
        &self,
    ) -> Result<AuthorityChildExecutionContext, ParentHandshakeError> {
        Ok(
            AuthorityChildExecutionContext::from_independent_measurements(
                self.final_generation,
                self.child_transport,
                self.start_contract,
                self.job_membership,
                self.token,
                self.image,
                self.environment,
                self.control_server,
            )?,
        )
    }

    pub(super) const fn expected_observed_launch(&self) -> ChildObservedLaunchContextDigest {
        self.observed_launch
    }

    #[cfg(test)]
    fn for_test_from_closed_parent_copies(
        closed: &ParentClientCopiesClosed,
        seed: u8,
    ) -> Result<Self, ParentHandshakeError> {
        let mut value = Self {
            role: closed.role(),
            process_key: closed.process_key(),
            primary_thread: closed.primary_thread_binding(),
            image: ChildImageContextDigest::derive(&[seed.wrapping_add(1); 32])?,
            token: RunnerTokenContextDigest::derive(&[seed.wrapping_add(2); 32])?,
            environment: MinimalEnvironmentContextDigest::derive(&[seed.wrapping_add(3); 32])?,
            environment_authority_binding: [seed.wrapping_add(0x31); 32],
            runner_profile_digest: [seed.wrapping_add(0x32); 32],
            job_membership: JobMembershipEpochContextDigest::derive(&[seed.wrapping_add(4); 32])?,
            launch_attributes: closed.launch_attribute_binding(),
            final_generation: FinalGenerationContextDigest::derive(&[seed.wrapping_add(5); 32])?,
            child_transport: ChildTransportContractContextDigest::derive(
                &[seed.wrapping_add(6); 32],
            )?,
            start_contract: StartContractContextDigest::derive(&[seed.wrapping_add(7); 32])?,
            control_server: ControlServerIdentityContextDigest::derive(
                &[seed.wrapping_add(8); 32],
            )?,
            observed_launch: ChildObservedLaunchContextDigest::derive(&[seed.wrapping_add(9); 32])?,
            session_binding: HandshakeSessionBindingDigest::derive(&[seed.wrapping_add(10); 32])?,
        };
        let mut source = Sha256::new();
        source.update(HELD_LAUNCH_SESSION_SOURCE_DOMAIN);
        source.update([value.role.wire_value()]);
        source.update(value.process_key.pid.to_be_bytes());
        source.update(value.process_key.creation_time.to_be_bytes());
        for binding in [
            value.primary_thread.as_bytes(),
            value.image.as_bytes(),
            value.token.as_bytes(),
            value.environment.as_bytes(),
            &value.environment_authority_binding,
            &value.runner_profile_digest,
            value.job_membership.as_bytes(),
            value.launch_attributes.as_bytes(),
            value.final_generation.as_bytes(),
            value.child_transport.as_bytes(),
            value.start_contract.as_bytes(),
            value.control_server.as_bytes(),
            value.observed_launch.as_bytes(),
            closed.created_child_binding_digest(),
            closed.closure_binding_digest(),
        ] {
            source.update(binding);
        }
        value.session_binding = HandshakeSessionBindingDigest::derive(&source.finalize().into())?;
        value.validate()?;
        Ok(value)
    }
}

/// One-use proof that the parent closed all three client copies for this exact
/// role and pipe set before the child can be resumed.
pub(super) struct ExclusiveChildEndpointTransfer {
    closed_parent_copies: ParentClientCopiesClosed,
    role: ChildBootstrapRole,
    process_key: ProcessKey,
    primary_thread: CreatedChildPrimaryThreadBindingDigest,
    image: ChildImageContextDigest,
    token: RunnerTokenContextDigest,
    environment: MinimalEnvironmentContextDigest,
    environment_authority_binding: BootstrapDigest,
    runner_profile_digest: BootstrapDigest,
    job_membership: JobMembershipEpochContextDigest,
    launch_attributes: CreatedChildLaunchAttributeBindingDigest,
    final_generation: FinalGenerationContextDigest,
    child_transport: ChildTransportContractContextDigest,
    start_contract: StartContractContextDigest,
    control_server: ControlServerIdentityContextDigest,
    observed_launch: ChildObservedLaunchContextDigest,
    pipe_set_binding: ParentPipeSetBindingDigest,
    raw_handle_list_digest: RoleRawHandleListDigest,
    session_binding: HandshakeSessionBindingDigest,
    transfer_binding_digest: BootstrapDigest,
}

impl fmt::Debug for ExclusiveChildEndpointTransfer {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ExclusiveChildEndpointTransfer")
            .field("role", &self.role)
            .field("process", &"<held-and-redacted>")
            .field("bindings", &"<redacted>")
            .finish()
    }
}

impl ExclusiveChildEndpointTransfer {
    pub(super) fn from_closed_parent_copies(
        server_pipe_set_binding: ParentPipeSetBindingDigest,
        closed_parent_copies: ParentClientCopiesClosed,
        launch: VerifiedHeldChildLaunch,
    ) -> Result<Self, ParentHandshakeError> {
        launch.validate()?;
        closed_parent_copies.validate()?;
        let role = closed_parent_copies.role();
        let pipe_set_binding = closed_parent_copies.pipe_set_binding_digest();
        let raw_handle_list_digest = closed_parent_copies.raw_handle_list_digest();
        if role != launch.role
            || closed_parent_copies.process_key() != launch.process_key
            || closed_parent_copies.primary_thread_binding() != launch.primary_thread
            || closed_parent_copies.launch_attribute_binding() != launch.launch_attributes
            || raw_handle_list_digest.role() != role
            || pipe_set_binding != server_pipe_set_binding
            || digest_is_zero(closed_parent_copies.created_child_binding_digest())
            || digest_is_zero(closed_parent_copies.closure_binding_digest())
        {
            return Err(ParentHandshakeError::Binding(
                "parent_child_endpoint_closure_unexpected",
            ));
        }
        let mut digest = Sha256::new();
        digest.update(ENDPOINT_TRANSFER_BINDING_DOMAIN);
        digest.update([role.wire_value()]);
        digest.update(launch.process_key.pid.to_be_bytes());
        digest.update(launch.process_key.creation_time.to_be_bytes());
        for value in [
            launch.primary_thread.as_bytes(),
            launch.image.as_bytes(),
            launch.token.as_bytes(),
            launch.environment.as_bytes(),
            &launch.environment_authority_binding,
            &launch.runner_profile_digest,
            launch.job_membership.as_bytes(),
            launch.launch_attributes.as_bytes(),
            launch.final_generation.as_bytes(),
            launch.child_transport.as_bytes(),
            launch.start_contract.as_bytes(),
            launch.control_server.as_bytes(),
            launch.observed_launch.as_bytes(),
            closed_parent_copies.created_child_binding_digest(),
            pipe_set_binding.as_bytes(),
            raw_handle_list_digest.as_bytes(),
            closed_parent_copies.closure_binding_digest(),
            launch.session_binding.as_bytes(),
        ] {
            digest.update(value);
        }
        let transfer_binding_digest: BootstrapDigest = digest.finalize().into();
        if digest_is_zero(&transfer_binding_digest) {
            return Err(ParentHandshakeError::Binding(
                "parent_child_endpoint_transfer_binding_invalid",
            ));
        }
        Ok(Self {
            closed_parent_copies,
            role,
            process_key: launch.process_key,
            primary_thread: launch.primary_thread,
            image: launch.image,
            token: launch.token,
            environment: launch.environment,
            environment_authority_binding: launch.environment_authority_binding,
            runner_profile_digest: launch.runner_profile_digest,
            job_membership: launch.job_membership,
            launch_attributes: launch.launch_attributes,
            final_generation: launch.final_generation,
            child_transport: launch.child_transport,
            start_contract: launch.start_contract,
            control_server: launch.control_server,
            observed_launch: launch.observed_launch,
            pipe_set_binding,
            raw_handle_list_digest,
            session_binding: launch.session_binding,
            transfer_binding_digest,
        })
    }

    pub(super) const fn process_key(&self) -> ProcessKey {
        self.process_key
    }

    fn execution_context(&self) -> Result<AuthorityChildExecutionContext, ParentHandshakeError> {
        Ok(
            AuthorityChildExecutionContext::from_independent_measurements(
                self.final_generation,
                self.child_transport,
                self.start_contract,
                self.job_membership,
                self.token,
                self.image,
                self.environment,
                self.control_server,
            )?,
        )
    }

    pub(super) fn revalidate_held_observations(
        &self,
        expectation: &HeldChildAdmissionExpectation,
        inputs: ParentHeldChildAdmissionInputs<'_, '_>,
    ) -> Result<HeldChildAdmission, ParentHandshakeError> {
        self.validate()?;
        expectation.validate_against(self)?;
        let ParentHeldChildAdmissionInputs {
            child_process,
            process,
            primary_thread,
            role_capability_set,
            executable,
            executable_create_binding,
            environment,
            job,
            policy,
            control_server,
        } = inputs;
        // These two snapshots are intentionally created inside the
        // post-protocol revalidator. Supplying precomputed observations here
        // would turn the ACK admission check into a stale-before-ACK proof.
        let thread_observation =
            observe_live_created_child_process_and_thread(self.role, process, primary_thread)?;
        let job = job
            .observe_child_root(
                self.role,
                thread_observation.process_key(),
                thread_observation.primary_thread_id(),
            )
            .map_err(|error| ParentHandshakeError::Binding(error.code()))?;
        let fresh = VerifiedHeldChildLaunch::from_held_observations(ParentHeldChildLaunchInputs {
            closed_parent_copies: &self.closed_parent_copies,
            child_process,
            thread_observation,
            role_capability_set,
            executable,
            executable_create_binding,
            environment,
            job,
            policy,
            control_server,
        })?;
        if fresh.role != self.role
            || fresh.process_key != self.process_key
            || fresh.primary_thread != self.primary_thread
            || fresh.image != self.image
            || fresh.token != self.token
            || fresh.environment != self.environment
            || fresh.environment_authority_binding != self.environment_authority_binding
            || fresh.runner_profile_digest != self.runner_profile_digest
            || fresh.job_membership != self.job_membership
            || fresh.launch_attributes != self.launch_attributes
            || fresh.final_generation != self.final_generation
            || fresh.child_transport != self.child_transport
            || fresh.start_contract != self.start_contract
            || fresh.control_server != self.control_server
            || fresh.observed_launch != self.observed_launch
            || fresh.session_binding != self.session_binding
        {
            return Err(ParentHandshakeError::Binding(
                "parent_child_held_admission_observation_changed",
            ));
        }
        let mut source = Sha256::new();
        source.update(HELD_ADMISSION_REVALIDATION_SOURCE_DOMAIN);
        source.update(self.transfer_binding_digest);
        source.update(fresh.session_binding.as_bytes());
        source.update(executable_create_binding.binding_digest());
        HeldChildAdmission::from_revalidated_observations(expectation, source.finalize().into())
    }

    fn admission_expectation(&self) -> HeldChildAdmissionExpectation {
        HeldChildAdmissionExpectation {
            role: self.role,
            process_key: self.process_key,
            primary_thread: self.primary_thread,
            image: self.image,
            token: self.token,
            environment: self.environment,
            job_membership: self.job_membership,
            launch_attributes: self.launch_attributes,
            pipe_set_binding: self.pipe_set_binding,
            raw_handle_list_digest: self.raw_handle_list_digest,
            transfer_binding_digest: self.transfer_binding_digest,
        }
    }

    fn validate(&self) -> Result<(), ParentHandshakeError> {
        self.closed_parent_copies.validate()?;
        if self.closed_parent_copies.role() != self.role
            || self.closed_parent_copies.process_key() != self.process_key
            || self.closed_parent_copies.primary_thread_binding() != self.primary_thread
            || self.closed_parent_copies.launch_attribute_binding() != self.launch_attributes
            || self.closed_parent_copies.pipe_set_binding_digest() != self.pipe_set_binding
            || self.closed_parent_copies.raw_handle_list_digest() != self.raw_handle_list_digest
            || self.raw_handle_list_digest.role() != self.role
            || self.process_key.pid == 0
            || self.process_key.creation_time == 0
            || digest_is_zero(&self.transfer_binding_digest)
            || self.session_binding != self.derive_session_binding_digest()?
            || !digest_equal(
                &self.transfer_binding_digest,
                &self.derive_transfer_binding_digest(),
            )
        {
            return Err(ParentHandshakeError::Binding(
                "parent_child_endpoint_transfer_invalid",
            ));
        }
        Ok(())
    }

    fn derive_session_binding_digest(
        &self,
    ) -> Result<HandshakeSessionBindingDigest, ParentHandshakeError> {
        let mut source = Sha256::new();
        source.update(HELD_LAUNCH_SESSION_SOURCE_DOMAIN);
        source.update([self.role.wire_value()]);
        source.update(self.process_key.pid.to_be_bytes());
        source.update(self.process_key.creation_time.to_be_bytes());
        for value in [
            self.primary_thread.as_bytes(),
            self.image.as_bytes(),
            self.token.as_bytes(),
            self.environment.as_bytes(),
            &self.environment_authority_binding,
            &self.runner_profile_digest,
            self.job_membership.as_bytes(),
            self.launch_attributes.as_bytes(),
            self.final_generation.as_bytes(),
            self.child_transport.as_bytes(),
            self.start_contract.as_bytes(),
            self.control_server.as_bytes(),
            self.observed_launch.as_bytes(),
            self.closed_parent_copies.created_child_binding_digest(),
            self.closed_parent_copies.closure_binding_digest(),
        ] {
            source.update(value);
        }
        HandshakeSessionBindingDigest::derive(&source.finalize().into())
    }

    fn derive_transfer_binding_digest(&self) -> BootstrapDigest {
        let mut digest = Sha256::new();
        digest.update(ENDPOINT_TRANSFER_BINDING_DOMAIN);
        digest.update([self.role.wire_value()]);
        digest.update(self.process_key.pid.to_be_bytes());
        digest.update(self.process_key.creation_time.to_be_bytes());
        for value in [
            self.primary_thread.as_bytes(),
            self.image.as_bytes(),
            self.token.as_bytes(),
            self.environment.as_bytes(),
            &self.environment_authority_binding,
            &self.runner_profile_digest,
            self.job_membership.as_bytes(),
            self.launch_attributes.as_bytes(),
            self.final_generation.as_bytes(),
            self.child_transport.as_bytes(),
            self.start_contract.as_bytes(),
            self.control_server.as_bytes(),
            self.observed_launch.as_bytes(),
            self.closed_parent_copies.created_child_binding_digest(),
            self.pipe_set_binding.as_bytes(),
            self.raw_handle_list_digest.as_bytes(),
            self.closed_parent_copies.closure_binding_digest(),
            self.session_binding.as_bytes(),
        ] {
            digest.update(value);
        }
        digest.finalize().into()
    }
}

/// Parent protocol objects tied to the exact held launch and endpoint transfer.
/// Like held launch evidence, production construction remains unavailable.
pub(super) struct ParentProtocolProjection {
    role: ChildBootstrapRole,
    process_key: ProcessKey,
    raw_handle_list_digest: RoleRawHandleListDigest,
    session_binding: HandshakeSessionBindingDigest,
    transfer_binding_digest: BootstrapDigest,
    frame: ParentPreparedChildBootstrapFrame,
    expectations: ParentChildBootstrapExpectations,
}

impl fmt::Debug for ParentProtocolProjection {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ParentProtocolProjection")
            .field("role", &self.role)
            .field("bindings", &"<redacted>")
            .finish()
    }
}

impl ParentProtocolProjection {
    pub(super) fn from_production_observations() -> Result<Self, ParentHandshakeError> {
        Err(ParentHandshakeError::Binding(PRODUCTION_EVIDENCE_BLOCKER))
    }

    #[cfg_attr(test, allow(dead_code))]
    pub(super) fn prepare_for_held_transfer(
        transfer: &ExclusiveChildEndpointTransfer,
        policy: &SupervisorPolicy,
        role_capability_set: RoleCapabilitySetBinding,
        private_control_capability: PrivateControlCapability,
    ) -> Result<Self, ParentHandshakeError> {
        transfer.validate()?;
        if role_capability_set.role() != transfer.role
            || role_capability_set.raw_handle_list_digest()
                != transfer.raw_handle_list_digest.as_bytes()
            || transfer.runner_profile_digest != policy.runner_profile_digest
            || digest_is_zero(&transfer.environment_authority_binding)
        {
            return Err(ParentHandshakeError::Binding(
                "parent_child_handshake_policy_projection_unexpected",
            ));
        }
        let authority = AuthorityBindingDigest::derive(&policy.authority_identity_digest)?;
        let ticket = TicketBindingDigest::derive(&policy.ticket_digest)?;
        let run = RunBindingDigest::derive(&policy.run_binding_digest)?;
        let policy_binding = PolicyBindingDigest::derive(&policy.runner_policy_digest)?;
        let global = GlobalCapabilitySetDigest::derive(
            policy
                .child_transport_projection()
                .global_source_identities(),
        )?;
        let private_control_capability_commitment =
            PrivateControlCapabilityCommitment::from_parent_capability(
                &private_control_capability,
            )?;
        let expectations = ParentChildBootstrapExpectations::from_authority_projection(
            transfer.role,
            authority,
            ticket,
            run,
            policy_binding,
            global,
            role_capability_set.clone(),
            private_control_capability_commitment,
            transfer.observed_launch,
            transfer.execution_context()?,
        )?;
        let bindings = ChildBootstrapBindings::prepare_for_parent(
            authority,
            ticket,
            run,
            policy_binding,
            global,
            role_capability_set,
            private_control_capability,
        )?;
        let frame = ParentPreparedChildBootstrapFrame::prepare(transfer.role, bindings)?;
        Self::from_authority_projection(transfer, frame, expectations)
    }

    fn from_authority_projection(
        transfer: &ExclusiveChildEndpointTransfer,
        frame: ParentPreparedChildBootstrapFrame,
        expectations: ParentChildBootstrapExpectations,
    ) -> Result<Self, ParentHandshakeError> {
        transfer.validate()?;
        let transfer_execution_context_binding = transfer.execution_context()?.binding_digest()?;
        if frame.role() != transfer.role
            || expectations.role() != transfer.role
            || !digest_equal(
                frame.protocol_field_projection_digest().as_bytes(),
                expectations.protocol_field_projection_digest().as_bytes(),
            )
            || !digest_equal(
                transfer_execution_context_binding.as_bytes(),
                expectations.execution_context_binding_digest().as_bytes(),
            )
            || !digest_equal(
                transfer.raw_handle_list_digest.as_bytes(),
                expectations.role_raw_handle_list_digest(),
            )
            || expectations.expected_child_observation_context() != transfer.observed_launch
        {
            return Err(ParentHandshakeError::Binding(
                "parent_child_handshake_projection_unexpected",
            ));
        }
        Ok(Self {
            role: transfer.role,
            process_key: transfer.process_key,
            raw_handle_list_digest: transfer.raw_handle_list_digest,
            session_binding: transfer.session_binding,
            transfer_binding_digest: transfer.transfer_binding_digest,
            frame,
            expectations,
        })
    }
}

/// Non-cloneable owner of every parent endpoint and protocol state required for
/// one startup exchange.
pub(super) struct ParentAffineHandshakeSession {
    servers: ParentPipeServers,
    transfer: ExclusiveChildEndpointTransfer,
    frame: ParentPreparedChildBootstrapFrame,
    expectations: ParentChildBootstrapExpectations,
}

impl fmt::Debug for ParentAffineHandshakeSession {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ParentAffineHandshakeSession")
            .field("role", &self.transfer.role)
            .field("state", &"<affine-and-redacted>")
            .finish()
    }
}

impl ParentAffineHandshakeSession {
    pub(super) fn from_projection(
        servers: ParentPipeServers,
        transfer: ExclusiveChildEndpointTransfer,
        projection: ParentProtocolProjection,
    ) -> Result<Self, ParentHandshakeError> {
        transfer.validate()?;
        servers.verify_client_creator_is_current_parent()?;
        if servers.pipe_set_binding_digest() != transfer.pipe_set_binding
            || projection.role != transfer.role
            || projection.frame.role() != transfer.role
            || projection.process_key != transfer.process_key
            || projection.raw_handle_list_digest != transfer.raw_handle_list_digest
            || projection.session_binding != transfer.session_binding
            || !digest_equal(
                &projection.transfer_binding_digest,
                &transfer.transfer_binding_digest,
            )
        {
            return Err(ParentHandshakeError::Binding(
                "parent_child_handshake_projection_unexpected",
            ));
        }
        Ok(Self {
            servers,
            transfer,
            frame: projection.frame,
            expectations: projection.expectations,
        })
    }

    pub(super) fn run<R>(
        self,
        timeout: Duration,
        revalidator: R,
    ) -> Result<AdmittedChildControlSession, ParentHandshakeError>
    where
        R: HeldChildAdmissionRevalidator,
    {
        self.run_with_observer(timeout, revalidator, |_| {})
    }

    fn run_with_observer<R, O>(
        self,
        timeout: Duration,
        revalidator: R,
        mut observer: O,
    ) -> Result<AdmittedChildControlSession, ParentHandshakeError>
    where
        R: HeldChildAdmissionRevalidator,
        O: FnMut(HandshakeStage),
    {
        let Self {
            servers,
            transfer,
            frame,
            expectations,
        } = self;
        let deadline = HandshakeDeadline::new(timeout)?;
        let (bootstrap, mut control, result, pipe_set_binding) = servers.into_handshake_parts();
        if pipe_set_binding != transfer.pipe_set_binding {
            return Err(ParentHandshakeError::Binding(
                "parent_child_handshake_pipe_set_changed",
            ));
        }

        let mut ready_wire = SensitiveWireBytes::<CHILD_READY_MESSAGE_LEN>::zeroed();
        control.read_exact(ready_wire.as_mut_slice(), deadline.remaining())?;
        observer(HandshakeStage::ReadyBytesRead);
        let ready = ReceivedChildReady::read_from(&mut Cursor::new(ready_wire.as_slice()))?;
        observer(HandshakeStage::ReadyParsed);

        let mut authority_nonce_bytes = [0u8; 32];
        getrandom::fill(&mut authority_nonce_bytes).map_err(|_| {
            ParentHandshakeError::Binding("parent_child_handshake_secure_random_unavailable")
        })?;
        let authority_nonce = AuthorityHandshakeNonce::from_fresh_bytes(authority_nonce_bytes);
        volatile_zero(&mut authority_nonce_bytes);
        let authority_nonce = authority_nonce?;
        let expectation = PreparedExpectationEnvelope::prepare(
            &ready,
            authority_nonce,
            frame.frame_binding_digest(),
            &expectations,
        )?;
        observer(HandshakeStage::ExpectationPrepared);
        let mut expectation_wire = ExactWireCapture::<CHILD_EXPECTATION_ENVELOPE_LEN>::zeroed();
        let expectation_sent = expectation.write_to(&mut expectation_wire)?;
        expectation_wire.require_complete()?;
        control.write_exact(expectation_wire.as_slice(), deadline.remaining())?;
        observer(HandshakeStage::ExpectationWritten);

        let mut bootstrap_wire = ExactWireCapture::<CHILD_BOOTSTRAP_FRAME_LEN>::zeroed();
        let awaiting_ack = frame.write_complete_to(&mut bootstrap_wire)?;
        bootstrap_wire.require_complete()?;
        bootstrap.write_exact_and_close(bootstrap_wire.as_slice(), deadline.remaining())?;
        observer(HandshakeStage::BootstrapWrittenAndClosed);

        let mut ack_wire = SensitiveWireBytes::<CHILD_BOOTSTRAP_ACK_LEN>::zeroed();
        control.read_exact(ack_wire.as_mut_slice(), deadline.remaining())?;
        observer(HandshakeStage::AckBytesRead);
        let ack = ReceivedBootstrapAck::read_from(&mut Cursor::new(ack_wire.as_slice()))?;
        awaiting_ack.verify_ack(expectation_sent, ack)?;
        observer(HandshakeStage::ProtocolVerified);

        ProtocolExchangeVerified {
            control,
            result,
            transfer,
        }
        .promote(revalidator, &mut observer)
    }

    #[cfg(test)]
    fn protocol_verified_for_test(self) -> ProtocolExchangeVerified {
        let Self {
            servers,
            transfer,
            frame,
            expectations,
        } = self;
        let (bootstrap, control, result, pipe_set_binding) = servers.into_handshake_parts();
        assert_eq!(pipe_set_binding, transfer.pipe_set_binding);
        drop(bootstrap);
        drop(frame);
        drop(expectations);
        ProtocolExchangeVerified {
            control,
            result,
            transfer,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum HandshakeStage {
    ReadyBytesRead,
    ReadyParsed,
    ExpectationPrepared,
    ExpectationWritten,
    BootstrapWrittenAndClosed,
    AckBytesRead,
    ProtocolVerified,
    AdmissionRevalidated,
}

struct ProtocolExchangeVerified {
    control: ParentControlPipe,
    result: ParentResultPipe,
    transfer: ExclusiveChildEndpointTransfer,
}

impl ProtocolExchangeVerified {
    fn promote<R, O>(
        self,
        revalidator: R,
        observer: &mut O,
    ) -> Result<AdmittedChildControlSession, ParentHandshakeError>
    where
        R: HeldChildAdmissionRevalidator,
        O: FnMut(HandshakeStage),
    {
        let expectation = self.transfer.admission_expectation();
        expectation.validate_against(&self.transfer)?;
        let admission = revalidator.revalidate(expectation, &self.transfer)?;
        admission.validate(&self.transfer)?;
        observer(HandshakeStage::AdmissionRevalidated);
        Ok(AdmittedChildControlSession {
            control: self.control,
            result: self.result,
            transfer: self.transfer,
            admission,
        })
    }
}

pub(super) struct HeldChildAdmissionExpectation {
    role: ChildBootstrapRole,
    process_key: ProcessKey,
    primary_thread: CreatedChildPrimaryThreadBindingDigest,
    image: ChildImageContextDigest,
    token: RunnerTokenContextDigest,
    environment: MinimalEnvironmentContextDigest,
    job_membership: JobMembershipEpochContextDigest,
    launch_attributes: CreatedChildLaunchAttributeBindingDigest,
    pipe_set_binding: ParentPipeSetBindingDigest,
    raw_handle_list_digest: RoleRawHandleListDigest,
    transfer_binding_digest: BootstrapDigest,
}

impl fmt::Debug for HeldChildAdmissionExpectation {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("HeldChildAdmissionExpectation")
            .field("role", &self.role)
            .field("process", &"<held-and-redacted>")
            .field("bindings", &"<redacted>")
            .finish()
    }
}

impl HeldChildAdmissionExpectation {
    fn validate_against(
        &self,
        transfer: &ExclusiveChildEndpointTransfer,
    ) -> Result<(), ParentHandshakeError> {
        if self.role != transfer.role
            || self.process_key != transfer.process_key
            || self.primary_thread != transfer.primary_thread
            || self.image != transfer.image
            || self.token != transfer.token
            || self.environment != transfer.environment
            || self.job_membership != transfer.job_membership
            || self.launch_attributes != transfer.launch_attributes
            || self.pipe_set_binding != transfer.pipe_set_binding
            || self.raw_handle_list_digest != transfer.raw_handle_list_digest
            || !digest_equal(
                &self.transfer_binding_digest,
                &transfer.transfer_binding_digest,
            )
        {
            return Err(ParentHandshakeError::Binding(
                "parent_child_admission_expectation_unexpected",
            ));
        }
        Ok(())
    }
}

pub(super) trait HeldChildAdmissionRevalidator {
    fn revalidate(
        self,
        expectation: HeldChildAdmissionExpectation,
        transfer: &ExclusiveChildEndpointTransfer,
    ) -> Result<HeldChildAdmission, ParentHandshakeError>;
}

impl HeldChildAdmissionRevalidator for ParentHeldChildAdmissionInputs<'_, '_> {
    fn revalidate(
        self,
        expectation: HeldChildAdmissionExpectation,
        transfer: &ExclusiveChildEndpointTransfer,
    ) -> Result<HeldChildAdmission, ParentHandshakeError> {
        transfer.revalidate_held_observations(&expectation, self)
    }
}

pub(super) struct HeldChildAdmission {
    process_key: ProcessKey,
    transfer_binding_digest: BootstrapDigest,
    admission_binding_digest: BootstrapDigest,
}

impl fmt::Debug for HeldChildAdmission {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("HeldChildAdmission(<held-and-redacted>)")
    }
}

impl HeldChildAdmission {
    fn validate(
        &self,
        transfer: &ExclusiveChildEndpointTransfer,
    ) -> Result<(), ParentHandshakeError> {
        if self.process_key != transfer.process_key
            || !digest_equal(
                &self.transfer_binding_digest,
                &transfer.transfer_binding_digest,
            )
            || digest_is_zero(&self.admission_binding_digest)
            || digest_equal(
                &self.admission_binding_digest,
                &self.transfer_binding_digest,
            )
        {
            return Err(ParentHandshakeError::Binding(
                "parent_child_held_admission_unexpected",
            ));
        }
        Ok(())
    }

    fn from_revalidated_observations(
        expectation: &HeldChildAdmissionExpectation,
        source: BootstrapDigest,
    ) -> Result<Self, ParentHandshakeError> {
        if digest_is_zero(&source) {
            return Err(ParentHandshakeError::Binding(
                "parent_child_held_admission_fixture_invalid",
            ));
        }
        let mut digest = Sha256::new();
        digest.update(HELD_ADMISSION_BINDING_DOMAIN);
        digest.update(expectation.process_key.pid.to_be_bytes());
        digest.update(expectation.process_key.creation_time.to_be_bytes());
        digest.update(expectation.transfer_binding_digest);
        digest.update(source);
        let value = Self {
            process_key: expectation.process_key,
            transfer_binding_digest: expectation.transfer_binding_digest,
            admission_binding_digest: digest.finalize().into(),
        };
        Ok(value)
    }

    #[cfg(test)]
    fn for_test(
        expectation: &HeldChildAdmissionExpectation,
        source: BootstrapDigest,
    ) -> Result<Self, ParentHandshakeError> {
        Self::from_revalidated_observations(expectation, source)
    }
}

/// Returned only after protocol verification and a second held-handle
/// revalidation. A parent-known ACK is never exposed as child identity.
pub(super) struct AdmittedChildControlSession {
    control: ParentControlPipe,
    result: ParentResultPipe,
    transfer: ExclusiveChildEndpointTransfer,
    admission: HeldChildAdmission,
}

impl fmt::Debug for AdmittedChildControlSession {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AdmittedChildControlSession")
            .field("role", &self.transfer.role)
            .field("process", &"<held-and-redacted>")
            .finish()
    }
}

impl AdmittedChildControlSession {
    pub(super) const fn process_key(&self) -> ProcessKey {
        self.transfer.process_key
    }

    pub(super) fn into_parts(
        self,
    ) -> (
        ParentControlPipe,
        ParentResultPipe,
        ExclusiveChildEndpointTransfer,
        HeldChildAdmission,
    ) {
        (self.control, self.result, self.transfer, self.admission)
    }
}

struct HandshakeDeadline(Instant);

impl HandshakeDeadline {
    fn new(timeout: Duration) -> Result<Self, ParentHandshakeError> {
        Instant::now()
            .checked_add(timeout)
            .map(Self)
            .ok_or(ParentHandshakeError::Binding(
                "parent_child_handshake_deadline_invalid",
            ))
    }

    fn remaining(&self) -> Duration {
        self.0.saturating_duration_since(Instant::now())
    }
}

struct SensitiveWireBytes<const N: usize>([u8; N]);

impl<const N: usize> SensitiveWireBytes<N> {
    fn zeroed() -> Self {
        Self([0; N])
    }

    fn as_slice(&self) -> &[u8] {
        &self.0
    }

    fn as_mut_slice(&mut self) -> &mut [u8] {
        &mut self.0
    }
}

impl<const N: usize> Drop for SensitiveWireBytes<N> {
    fn drop(&mut self) {
        volatile_zero(&mut self.0);
    }
}

struct ExactWireCapture<const N: usize> {
    bytes: SensitiveWireBytes<N>,
    offset: usize,
}

impl<const N: usize> ExactWireCapture<N> {
    fn zeroed() -> Self {
        Self {
            bytes: SensitiveWireBytes::zeroed(),
            offset: 0,
        }
    }

    fn require_complete(&self) -> Result<(), ParentHandshakeError> {
        if self.offset != N {
            return Err(ParentHandshakeError::Binding(
                "parent_child_handshake_wire_length_invalid",
            ));
        }
        Ok(())
    }

    fn as_slice(&self) -> &[u8] {
        self.bytes.as_slice()
    }
}

impl<const N: usize> Write for ExactWireCapture<N> {
    fn write(&mut self, source: &[u8]) -> io::Result<usize> {
        if source.is_empty() {
            return Ok(0);
        }
        let remaining = N.saturating_sub(self.offset);
        if source.len() > remaining {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "fixed handshake wire overflow",
            ));
        }
        self.bytes.0[self.offset..self.offset + source.len()].copy_from_slice(source);
        self.offset += source.len();
        Ok(source.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

fn digest_is_zero(value: &BootstrapDigest) -> bool {
    value.iter().all(|byte| *byte == 0)
}

fn digest_equal(left: &BootstrapDigest, right: &BootstrapDigest) -> bool {
    left.iter()
        .zip(right)
        .fold(0u8, |difference, (left, right)| difference | (left ^ right))
        == 0
}

#[inline(never)]
fn volatile_zero(bytes: &mut [u8]) {
    for byte in bytes {
        unsafe {
            ptr::write_volatile(byte, 0);
        }
    }
    compiler_fence(Ordering::SeqCst);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::primitive_evidence_child_protocol::{
        child_role_capability_schema, AuthorityBindingDigest, ChildBootstrapBindings,
        ChildHandshakeNonce, ChildRoleCapabilitySlotBinding, GlobalCapabilitySetDigest,
        PolicyBindingDigest, PreparedChildReady, PrivateControlCapability,
        PrivateControlCapabilityCommitment, RoleCapabilitySetBinding, RunBindingDigest,
        TicketBindingDigest, GLOBAL_CAPABILITY_SOURCE_COUNT,
    };
    use std::thread;

    use super::super::child_transport::{
        test_parent_handshake_pipe_fixture, CreatedSuspendedChildClosureBinding,
        TestChildPipePeers, TestParentHandshakePipeFixture,
    };

    struct SessionParts {
        servers: ParentPipeServers,
        transfer: ExclusiveChildEndpointTransfer,
        projection: ParentProtocolProjection,
        child_peers: TestChildPipePeers,
        role_capability_set: RoleCapabilitySetBinding,
        raw_handle_list_digest: RoleRawHandleListDigest,
        expected_observation: ChildObservedLaunchContextDigest,
        process_key: ProcessKey,
    }

    struct SessionFixture {
        session: ParentAffineHandshakeSession,
        child_peers: TestChildPipePeers,
        role_capability_set: RoleCapabilitySetBinding,
        raw_handle_list_digest: RoleRawHandleListDigest,
        expected_observation: ChildObservedLaunchContextDigest,
        process_key: ProcessKey,
    }

    enum TestHeldAdmissionRevalidator {
        Accept(BootstrapDigest),
        Reject(&'static str),
    }

    impl HeldChildAdmissionRevalidator for TestHeldAdmissionRevalidator {
        fn revalidate(
            self,
            expectation: HeldChildAdmissionExpectation,
            _transfer: &ExclusiveChildEndpointTransfer,
        ) -> Result<HeldChildAdmission, ParentHandshakeError> {
            match self {
                Self::Accept(source) => HeldChildAdmission::for_test(&expectation, source),
                Self::Reject(code) => Err(ParentHandshakeError::Binding(code)),
            }
        }
    }

    fn role_capability_set(
        role: ChildBootstrapRole,
        raw_handles: &[usize; 3],
        seed: u8,
    ) -> RoleCapabilitySetBinding {
        let slots = child_role_capability_schema(role)
            .iter()
            .enumerate()
            .map(|(index, descriptor)| {
                ChildRoleCapabilitySlotBinding::new(
                    descriptor.semantic(),
                    [seed.wrapping_add(index as u8); 32],
                    raw_handles[index],
                )
            })
            .collect::<Vec<_>>();
        RoleCapabilitySetBinding::derive_from_fixed_slots(role, &slots).unwrap()
    }

    fn protocol_projection(
        transfer: &ExclusiveChildEndpointTransfer,
        role_capability_set: &RoleCapabilitySetBinding,
        expected_observation: ChildObservedLaunchContextDigest,
        execution_context: AuthorityChildExecutionContext,
        seed: u8,
    ) -> ParentProtocolProjection {
        let authority = AuthorityBindingDigest::derive(&[seed.wrapping_add(1); 32]).unwrap();
        let ticket = TicketBindingDigest::derive(&[seed.wrapping_add(2); 32]).unwrap();
        let run = RunBindingDigest::derive(&[seed.wrapping_add(3); 32]).unwrap();
        let policy = PolicyBindingDigest::derive(&[seed.wrapping_add(4); 32]).unwrap();
        let global_sources: [BootstrapDigest; GLOBAL_CAPABILITY_SOURCE_COUNT] =
            std::array::from_fn(|index| [seed.wrapping_add(0x20 + index as u8); 32]);
        let global = GlobalCapabilitySetDigest::derive(&global_sources).unwrap();
        let mut private_source = [seed.wrapping_add(0x40); 32];
        let private_control =
            PrivateControlCapability::take_for_parent(&mut private_source).unwrap();
        assert!(private_source.iter().all(|byte| *byte == 0));
        let commitment =
            PrivateControlCapabilityCommitment::from_parent_capability(&private_control).unwrap();
        let expectations = ParentChildBootstrapExpectations::from_authority_projection(
            transfer.role,
            authority,
            ticket,
            run,
            policy,
            global,
            role_capability_set.clone(),
            commitment,
            expected_observation,
            execution_context,
        )
        .unwrap();
        let bindings = ChildBootstrapBindings::prepare_for_parent(
            authority,
            ticket,
            run,
            policy,
            global,
            role_capability_set.clone(),
            private_control,
        )
        .unwrap();
        let frame = ParentPreparedChildBootstrapFrame::prepare(transfer.role, bindings).unwrap();
        ParentProtocolProjection::from_authority_projection(transfer, frame, expectations).unwrap()
    }

    fn session_parts(role: ChildBootstrapRole, seed: u8) -> SessionParts {
        let process_key = ProcessKey {
            pid: 0x2000 + u32::from(seed),
            creation_time: 0x3000 + u64::from(seed),
        };
        let TestParentHandshakePipeFixture {
            servers,
            closed_parent_copies,
            child_peers,
            inherited_raw_handles,
        } = test_parent_handshake_pipe_fixture(
            role,
            process_key,
            [seed.wrapping_add(0x10); 32],
            [seed.wrapping_add(0x11); 32],
        )
        .unwrap();
        let role_capability_set =
            role_capability_set(role, &inherited_raw_handles, seed.wrapping_add(0x50));
        let raw_handle_list_digest =
            RoleRawHandleListDigest::derive(role, &inherited_raw_handles).unwrap();
        assert_eq!(
            role_capability_set.raw_handle_list_digest(),
            raw_handle_list_digest.as_bytes()
        );
        let launch = VerifiedHeldChildLaunch::for_test_from_closed_parent_copies(
            &closed_parent_copies,
            seed.wrapping_add(0x60),
        )
        .unwrap();
        let expected_observation = launch.observed_launch;
        let execution_context = launch.execution_context().unwrap();
        let server_binding = servers.pipe_set_binding_digest();
        let transfer = ExclusiveChildEndpointTransfer::from_closed_parent_copies(
            server_binding,
            closed_parent_copies,
            launch,
        )
        .unwrap();
        let projection = protocol_projection(
            &transfer,
            &role_capability_set,
            expected_observation,
            execution_context,
            seed.wrapping_add(0x70),
        );
        SessionParts {
            servers,
            transfer,
            projection,
            child_peers,
            role_capability_set,
            raw_handle_list_digest,
            expected_observation,
            process_key,
        }
    }

    fn session_fixture(role: ChildBootstrapRole, seed: u8) -> SessionFixture {
        let SessionParts {
            servers,
            transfer,
            projection,
            child_peers,
            role_capability_set,
            raw_handle_list_digest,
            expected_observation,
            process_key,
        } = session_parts(role, seed);
        let session =
            ParentAffineHandshakeSession::from_projection(servers, transfer, projection).unwrap();
        SessionFixture {
            session,
            child_peers,
            role_capability_set,
            raw_handle_list_digest,
            expected_observation,
            process_key,
        }
    }

    fn ready_bytes(
        role_capability_set: &RoleCapabilitySetBinding,
        raw_handle_list_digest: RoleRawHandleListDigest,
        observation: ChildObservedLaunchContextDigest,
        nonce_byte: u8,
    ) -> Vec<u8> {
        let mut bytes = Vec::new();
        let _waiting = PreparedChildReady::prepare(
            role_capability_set.role(),
            ChildHandshakeNonce::from_fresh_bytes([nonce_byte; 32]).unwrap(),
            role_capability_set,
            raw_handle_list_digest,
            observation,
        )
        .unwrap()
        .write_to(&mut bytes)
        .unwrap();
        assert_eq!(bytes.len(), CHILD_READY_MESSAGE_LEN);
        bytes
    }

    #[test]
    fn zero_create_event_cannot_mint_a_production_session() {
        let create_error = CreatedSuspendedChildClosureBinding::from_production_create_result()
            .expect_err("production create evidence remains unavailable");
        assert_eq!(
            create_error.code(),
            "parent_created_suspended_child_evidence_not_connected"
        );
        assert_eq!(
            VerifiedHeldChildLaunch::from_production_observations()
                .expect_err("held launch evidence remains unavailable")
                .code(),
            PRODUCTION_EVIDENCE_BLOCKER
        );
        assert_eq!(
            ParentProtocolProjection::from_production_observations()
                .expect_err("parent projection remains unavailable")
                .code(),
            PRODUCTION_EVIDENCE_BLOCKER
        );
    }

    #[test]
    fn transfer_and_session_reject_wrong_closure_process_pipe_session_raw_and_role() {
        let process = ProcessKey {
            pid: 0x4101,
            creation_time: 0x4102,
        };
        let first = test_parent_handshake_pipe_fixture(
            ChildBootstrapRole::LifecycleDriver,
            process,
            [0x41; 32],
            [0x42; 32],
        )
        .unwrap();
        let second = test_parent_handshake_pipe_fixture(
            ChildBootstrapRole::LifecycleDriver,
            ProcessKey {
                pid: 0x4201,
                creation_time: 0x4202,
            },
            [0x43; 32],
            [0x44; 32],
        )
        .unwrap();
        let launch = VerifiedHeldChildLaunch::for_test_from_closed_parent_copies(
            &second.closed_parent_copies,
            0x45,
        )
        .unwrap();
        let wrong_closure = ExclusiveChildEndpointTransfer::from_closed_parent_copies(
            first.servers.pipe_set_binding_digest(),
            second.closed_parent_copies,
            launch,
        )
        .expect_err("a close token from another pipe set must fail");
        assert_eq!(
            wrong_closure.code(),
            "parent_child_endpoint_closure_unexpected"
        );
        drop(first);

        let fixture = test_parent_handshake_pipe_fixture(
            ChildBootstrapRole::LifecycleDriver,
            process,
            [0x51; 32],
            [0x52; 32],
        )
        .unwrap();
        let mut wrong_process_launch = VerifiedHeldChildLaunch::for_test_from_closed_parent_copies(
            &fixture.closed_parent_copies,
            0x53,
        )
        .unwrap();
        wrong_process_launch.process_key.creation_time += 1;
        assert_eq!(
            ExclusiveChildEndpointTransfer::from_closed_parent_copies(
                fixture.servers.pipe_set_binding_digest(),
                fixture.closed_parent_copies,
                wrong_process_launch,
            )
            .expect_err("the close event must bind the exact process key")
            .code(),
            "parent_child_endpoint_closure_unexpected"
        );

        let fixture = test_parent_handshake_pipe_fixture(
            ChildBootstrapRole::LifecycleDriver,
            process,
            [0x61; 32],
            [0x62; 32],
        )
        .unwrap();
        let foreign = test_parent_handshake_pipe_fixture(
            ChildBootstrapRole::LifecycleDriver,
            ProcessKey {
                pid: 0x4301,
                creation_time: 0x4302,
            },
            [0x63; 32],
            [0x64; 32],
        )
        .unwrap();
        let mut wrong_primary_thread = VerifiedHeldChildLaunch::for_test_from_closed_parent_copies(
            &fixture.closed_parent_copies,
            0x65,
        )
        .unwrap();
        wrong_primary_thread.primary_thread = foreign.closed_parent_copies.primary_thread_binding();
        assert_eq!(
            ExclusiveChildEndpointTransfer::from_closed_parent_copies(
                fixture.servers.pipe_set_binding_digest(),
                fixture.closed_parent_copies,
                wrong_primary_thread,
            )
            .expect_err("the close event must bind the primary-thread epoch")
            .code(),
            "parent_child_endpoint_closure_unexpected"
        );
        drop(foreign);

        let fixture = test_parent_handshake_pipe_fixture(
            ChildBootstrapRole::LifecycleDriver,
            process,
            [0x54; 32],
            [0x55; 32],
        )
        .unwrap();
        let mut cross_role_launch = VerifiedHeldChildLaunch::for_test_from_closed_parent_copies(
            &fixture.closed_parent_copies,
            0x56,
        )
        .unwrap();
        cross_role_launch.role = ChildBootstrapRole::BridgeLauncher;
        assert_eq!(
            ExclusiveChildEndpointTransfer::from_closed_parent_copies(
                fixture.servers.pipe_set_binding_digest(),
                fixture.closed_parent_copies,
                cross_role_launch,
            )
            .expect_err("the close event must bind the exact role")
            .code(),
            "parent_child_endpoint_closure_unexpected"
        );

        let mut parts = session_parts(ChildBootstrapRole::LifecycleDriver, 0x21);
        parts.projection.process_key.creation_time += 1;
        assert_eq!(
            ParentAffineHandshakeSession::from_projection(
                parts.servers,
                parts.transfer,
                parts.projection,
            )
            .expect_err("projection process drift must fail")
            .code(),
            "parent_child_handshake_projection_unexpected"
        );

        let mut parts = session_parts(ChildBootstrapRole::LifecycleDriver, 0x22);
        parts.projection.session_binding =
            HandshakeSessionBindingDigest::derive(&[0xec; 32]).unwrap();
        assert_eq!(
            ParentAffineHandshakeSession::from_projection(
                parts.servers,
                parts.transfer,
                parts.projection,
            )
            .expect_err("cross-session projection must fail")
            .code(),
            "parent_child_handshake_projection_unexpected"
        );

        let mut parts = session_parts(ChildBootstrapRole::LifecycleDriver, 0x23);
        parts.projection.raw_handle_list_digest = RoleRawHandleListDigest::derive(
            ChildBootstrapRole::LifecycleDriver,
            &[0x101, 0x202, 0x303],
        )
        .unwrap();
        assert_eq!(
            ParentAffineHandshakeSession::from_projection(
                parts.servers,
                parts.transfer,
                parts.projection,
            )
            .expect_err("raw-handle projection drift must fail")
            .code(),
            "parent_child_handshake_projection_unexpected"
        );

        let first = session_parts(ChildBootstrapRole::LifecycleDriver, 0x24);
        let second = session_parts(ChildBootstrapRole::LifecycleDriver, 0x25);
        assert_eq!(
            ParentAffineHandshakeSession::from_projection(
                first.servers,
                second.transfer,
                second.projection,
            )
            .expect_err("servers from another pipe set must fail")
            .code(),
            "parent_child_handshake_projection_unexpected"
        );
    }

    #[test]
    fn projection_rejects_cross_run_fields_and_execution_context_before_io() {
        let SessionParts {
            transfer,
            projection: first_projection,
            ..
        } = session_parts(ChildBootstrapRole::LifecycleDriver, 0x26);
        let ParentProtocolProjection {
            frame: first_frame, ..
        } = first_projection;
        let ParentProtocolProjection {
            expectations: foreign_expectations,
            ..
        } = session_parts(ChildBootstrapRole::LifecycleDriver, 0x27).projection;
        assert_eq!(
            ParentProtocolProjection::from_authority_projection(
                &transfer,
                first_frame,
                foreign_expectations,
            )
            .expect_err("frame and expectation fields from different runs must fail")
            .code(),
            "parent_child_handshake_projection_unexpected"
        );

        let ParentProtocolProjection {
            frame: foreign_frame,
            expectations: foreign_expectations,
            ..
        } = session_parts(ChildBootstrapRole::LifecycleDriver, 0x28).projection;
        assert_eq!(
            ParentProtocolProjection::from_authority_projection(
                &transfer,
                foreign_frame,
                foreign_expectations,
            )
            .expect_err("a self-consistent foreign execution context must not transplant")
            .code(),
            "parent_child_handshake_projection_unexpected"
        );
    }

    #[test]
    fn held_transfer_prepares_one_policy_bound_frame_and_expectation_pair() {
        let SessionParts {
            servers,
            mut transfer,
            role_capability_set,
            child_peers,
            ..
        } = session_parts(ChildBootstrapRole::BridgeLauncher, 0x29);
        let policy = crate::primitive_evidence_authority_supervisor::tests::policy();
        transfer.runner_profile_digest = policy.runner_profile_digest;
        transfer.session_binding = transfer.derive_session_binding_digest().unwrap();
        transfer.transfer_binding_digest = transfer.derive_transfer_binding_digest();
        transfer.validate().unwrap();
        let mut private_source = [0xe1; 32];
        let private_control =
            PrivateControlCapability::take_for_parent(&mut private_source).unwrap();
        let projection = ParentProtocolProjection::prepare_for_held_transfer(
            &transfer,
            &policy,
            role_capability_set,
            private_control,
        )
        .unwrap();
        ParentAffineHandshakeSession::from_projection(servers, transfer, projection).unwrap();
        assert!(private_source.iter().all(|byte| *byte == 0));
        drop(child_peers);
    }

    #[test]
    fn observation_mismatch_writes_zero_expectation_bytes() {
        let SessionFixture {
            session,
            mut child_peers,
            role_capability_set,
            raw_handle_list_digest,
            expected_observation,
            ..
        } = session_fixture(ChildBootstrapRole::LifecycleDriver, 0x31);
        let mismatched_observation = ChildObservedLaunchContextDigest::derive(&[0xee; 32]).unwrap();
        assert_ne!(
            mismatched_observation.as_bytes(),
            expected_observation.as_bytes()
        );
        let ready = ready_bytes(
            &role_capability_set,
            raw_handle_list_digest,
            mismatched_observation,
            0x91,
        );
        let mut stages = Vec::new();
        thread::scope(|scope| {
            let peer = scope.spawn(move || {
                child_peers
                    .write_control_exact(&ready, Duration::from_secs(2))
                    .unwrap();
                let mut first_expectation_byte = [0u8; 1];
                child_peers
                    .read_control_exact(&mut first_expectation_byte, Duration::from_secs(2))
                    .expect_err("the parent must close without writing an expectation")
                    .code()
            });
            let error = session
                .run_with_observer(
                    Duration::from_secs(2),
                    TestHeldAdmissionRevalidator::Accept([0xa1; 32]),
                    |stage| stages.push(stage),
                )
                .expect_err("independent observation mismatch must fail");
            assert_eq!(error.code(), "child_expectation_ready_binding_invalid");
            assert!(error.requires_child_termination());
            assert_eq!(peer.join().unwrap(), "parent_pipe_broken");
        });
        assert_eq!(
            stages,
            [HandshakeStage::ReadyBytesRead, HandshakeStage::ReadyParsed]
        );
    }

    #[test]
    fn wire_order_is_ready_expectation_bootstrap_close_then_ack() {
        let SessionFixture {
            session,
            mut child_peers,
            role_capability_set,
            raw_handle_list_digest,
            expected_observation,
            ..
        } = session_fixture(ChildBootstrapRole::LifecycleDriver, 0x32);
        let ready = ready_bytes(
            &role_capability_set,
            raw_handle_list_digest,
            expected_observation,
            0x92,
        );
        let mut stages = Vec::new();
        thread::scope(|scope| {
            let peer = scope.spawn(move || {
                child_peers
                    .write_control_exact(&ready, Duration::from_secs(2))
                    .unwrap();
                let mut expectation = [0u8; CHILD_EXPECTATION_ENVELOPE_LEN];
                child_peers
                    .read_control_exact(&mut expectation, Duration::from_secs(2))
                    .unwrap();
                assert_eq!(&expectation[..8], b"VRCEXP02");
                let mut bootstrap = [0u8; CHILD_BOOTSTRAP_FRAME_LEN];
                child_peers
                    .read_bootstrap_exact(&mut bootstrap, Duration::from_secs(2))
                    .unwrap();
                assert_eq!(&bootstrap[..8], b"VRCCHD02");
                let mut trailing = [0u8; 1];
                assert_eq!(
                    child_peers
                        .read_bootstrap_exact(&mut trailing, Duration::from_secs(2))
                        .expect_err("the sole bootstrap writer must already be closed")
                        .code(),
                    "parent_pipe_broken"
                );
                child_peers
                    .write_control_exact(&[0u8; CHILD_BOOTSTRAP_ACK_LEN], Duration::from_secs(2))
                    .unwrap();
            });
            let error = session
                .run_with_observer(
                    Duration::from_secs(2),
                    TestHeldAdmissionRevalidator::Accept([0xa2; 32]),
                    |stage| stages.push(stage),
                )
                .expect_err("the intentionally invalid ACK must fail parsing");
            assert_eq!(error.code(), "child_bootstrap_ack_length_invalid");
            peer.join().unwrap();
        });
        assert_eq!(
            stages,
            [
                HandshakeStage::ReadyBytesRead,
                HandshakeStage::ReadyParsed,
                HandshakeStage::ExpectationPrepared,
                HandshakeStage::ExpectationWritten,
                HandshakeStage::BootstrapWrittenAndClosed,
                HandshakeStage::AckBytesRead,
            ]
        );
    }

    #[test]
    fn typed_timeout_broken_and_quarantine_errors_are_preserved() {
        let SessionFixture {
            session,
            child_peers,
            ..
        } = session_fixture(ChildBootstrapRole::LifecycleDriver, 0x33);
        let timeout = session
            .run(
                Duration::from_millis(25),
                TestHeldAdmissionRevalidator::Accept([0xa3; 32]),
            )
            .expect_err("an idle ready pipe must time out");
        assert_eq!(timeout.code(), "parent_pipe_io_timeout");
        assert!(!timeout.requires_session_containment());
        assert!(timeout.requires_child_termination());
        drop(child_peers);

        let SessionFixture {
            session,
            child_peers,
            ..
        } = session_fixture(ChildBootstrapRole::LifecycleDriver, 0x34);
        drop(child_peers);
        let broken = session
            .run(
                Duration::from_secs(2),
                TestHeldAdmissionRevalidator::Accept([0xa4; 32]),
            )
            .expect_err("a closed peer must remain a typed broken-pipe error");
        assert_eq!(broken.code(), "parent_pipe_broken");
        assert!(!broken.requires_session_containment());

        let quarantined = ParentHandshakeError::from(ParentPipeError::quarantined_for_test());
        assert_eq!(quarantined.code(), "parent_pipe_io_quarantined");
        assert!(quarantined.requires_session_containment());
    }

    #[test]
    fn protocol_verified_state_still_requires_typed_held_admission() {
        let SessionFixture {
            session,
            child_peers,
            ..
        } = session_fixture(ChildBootstrapRole::LifecycleDriver, 0x35);
        let mut stages = Vec::new();
        let error = session
            .protocol_verified_for_test()
            .promote(
                TestHeldAdmissionRevalidator::Reject("test_held_admission_revalidation_failed"),
                &mut |stage| stages.push(stage),
            )
            .expect_err("protocol verification alone must not admit a child");
        assert_eq!(error.code(), "test_held_admission_revalidation_failed");
        assert!(stages.is_empty());
        drop(child_peers);

        let SessionFixture {
            session,
            child_peers,
            process_key,
            ..
        } = session_fixture(ChildBootstrapRole::BridgeLauncher, 0x36);
        let admitted = session
            .protocol_verified_for_test()
            .promote(
                TestHeldAdmissionRevalidator::Accept([0xa6; 32]),
                &mut |stage| stages.push(stage),
            )
            .unwrap();
        assert_eq!(admitted.process_key(), process_key);
        let (_control, _result, transfer, admission) = admitted.into_parts();
        assert_eq!(transfer.process_key(), process_key);
        admission.validate(&transfer).unwrap();
        assert_eq!(stages, [HandshakeStage::AdmissionRevalidated]);
        drop(child_peers);
    }
}
