use crate::primitive_basis_protected_evidence_bundle::MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES;
use crate::primitive_evidence_authority_ledger::{DurableProjectionCommitReceipt, LedgerIdentity};
pub use crate::primitive_evidence_authority_pipe::ExternalModelPartHandleTokens;
use crate::primitive_evidence_authority_runtime::{
    AuthorityRuntime, AuthorityRuntimeCommand, AuthorityRuntimeError, AuthorityRuntimeReply,
    RuntimeTerminalKind,
};
use crate::primitive_evidence_authority_windows::{AUTHORITY_PIPE_NAME, AUTHORITY_PIPE_SDDL};
use serde::{de, Deserialize, Deserializer, Serialize};
use serde_json::{Map, Number, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fmt,
    io::{self, Read, Write},
    sync::Mutex,
};

pub const REQUEST_SCHEMA: &str = "vrcforge.primitive_evidence_authority_request.v2";
pub const RESPONSE_SCHEMA: &str = "vrcforge.primitive_evidence_authority_response.v1";
pub const GENERATION_ATTESTATION_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_generation_attestation.v1";
pub const PROJECTION_COMMIT_RECEIPT_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_projection_commit_receipt.v2";
pub const MAX_FRAME_SIZE: usize = 64 * 1024;
pub const MAX_RESPONSE_FRAME_SIZE: usize = 16 * 1024 * 1024;

const GENERATION_ATTESTATION_DOMAIN: &[u8] = b"vrcforge-authority-generation-attestation-v1\0";
const PROJECTION_COMMIT_RECEIPT_DIGEST_DOMAIN: &[u8] =
    b"vrcforge-authority-projection-commit-receipt-v2\0";
const SERVICE_INSTANCE_DOMAIN: &[u8] = b"vrcforge-authority-service-instance-v1\0";
const PEER_BINDING_DOMAIN: &[u8] = b"vrcforge-authority-peer-binding-v1\0";
const FIXED_PIPE_IDENTITY_DOMAIN: &[u8] = b"vrcforge-authority-fixed-pipe-identity-v1\0";
const GENERATION_ATTESTATION_POLICY_ID: &str = "vrcforge.authority.generation-attestation.fixed.v1";
const GENERATION_ATTESTATION_PROOF_ALGORITHM: &str = "p256-sha256-raw-rs-low-s";
const PROJECTION_COMMIT_RECEIPT_PROOF_ALGORITHM: &str = "p256-sha256-raw-rs-low-s";
const MAX_HANDSHAKES_PER_SERVICE_INSTANCE: usize = 4_096;
const P256_ORDER: [u8; 32] = [
    0xff, 0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xbc, 0xe6, 0xfa, 0xad, 0xa7, 0x17, 0x9e, 0x84, 0xf3, 0xb9, 0xca, 0xc2, 0xfc, 0x63, 0x25, 0x51,
];
const P256_HALF_ORDER: [u8; 32] = [
    0x7f, 0xff, 0xff, 0xff, 0x80, 0x00, 0x00, 0x00, 0x7f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xde, 0x73, 0x7d, 0x56, 0xd3, 0x8b, 0xcf, 0x42, 0x79, 0xdc, 0xe5, 0x61, 0x7e, 0x31, 0x92, 0xa8,
];

const SOURCE_BLOCKERS: [&str; 5] = [
    "authority_service_bootstrap_native_snapshot_not_connected",
    "authority_generation_binding_verifier_not_connected",
    "authority_generation_attestation_signer_not_connected",
    "authority_controller_client_not_connected",
    "authority_runtime_composition_not_connected",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContractError(String);

impl ContractError {
    pub(crate) fn new(code: impl Into<String>) -> Self {
        Self(code.into())
    }

    pub fn code(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for ContractError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ContractError {}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "command", deny_unknown_fields)]
pub enum Request {
    #[serde(rename = "handshake")]
    Handshake {
        schema: String,
        #[serde(rename = "expectedGeneration")]
        expected_generation: String,
        challenge: String,
    },
    #[serde(rename = "status")]
    Status { schema: String },
    #[serde(rename = "selfTest")]
    SelfTest { schema: String },
    #[serde(rename = "runModelPartComposition")]
    RunModelPartComposition {
        schema: String,
        #[serde(rename = "requestId")]
        request_id: String,
        #[serde(rename = "handleTokens")]
        handle_tokens: ExternalModelPartHandleTokens,
    },
    #[serde(rename = "cancel")]
    Cancel {
        schema: String,
        #[serde(rename = "requestId")]
        request_id: String,
    },
    #[serde(rename = "getResult")]
    GetResult {
        schema: String,
        #[serde(rename = "requestId")]
        request_id: String,
    },
}

impl Request {
    fn schema(&self) -> &str {
        match self {
            Self::Handshake { schema, .. }
            | Self::Status { schema }
            | Self::SelfTest { schema }
            | Self::RunModelPartComposition { schema, .. }
            | Self::Cancel { schema, .. }
            | Self::GetResult { schema, .. } => schema,
        }
    }

    pub fn command(&self) -> &'static str {
        match self {
            Self::Handshake { .. } => "handshake",
            Self::Status { .. } => "status",
            Self::SelfTest { .. } => "selfTest",
            Self::RunModelPartComposition { .. } => "runModelPartComposition",
            Self::Cancel { .. } => "cancel",
            Self::GetResult { .. } => "getResult",
        }
    }

    fn validate(&self) -> Result<(), ContractError> {
        if self.schema() != REQUEST_SCHEMA {
            return Err(ContractError::new("request_schema_mismatch"));
        }
        match self {
            Self::Handshake {
                expected_generation,
                challenge,
                ..
            } => {
                require_hex_32(expected_generation, "expected_generation_invalid")?;
                let challenge = require_hex_32(challenge, "handshake_challenge_invalid")?;
                if challenge.iter().all(|byte| *byte == 0) {
                    return Err(ContractError::new("handshake_challenge_invalid"));
                }
            }
            Self::RunModelPartComposition { request_id, .. } => {
                require_request_id(request_id)?;
            }
            Self::Cancel { request_id, .. } | Self::GetResult { request_id, .. } => {
                require_request_id(request_id)?;
            }
            Self::Status { .. } | Self::SelfTest { .. } => {}
        }
        Ok(())
    }
}

pub struct ReadOnlyAuthority;

impl ReadOnlyAuthority {
    pub fn new() -> Self {
        Self
    }

    pub fn handle(&mut self, request: Request) -> Result<Value, ContractError> {
        request.validate()?;
        let command = request.command();
        match request {
            Request::Handshake { .. } => Err(ContractError::new("authority_boundary_not_ready")),
            Request::Status { .. } => Ok(success_response(
                command,
                serde_json::json!({
                    "readOnly": true,
                    "trustedBoundaryReady": false,
                    "blockers": SOURCE_BLOCKERS,
                }),
            )),
            Request::SelfTest { .. } => Ok(success_response(
                command,
                serde_json::json!({
                    "passed": true,
                    "readOnly": true,
                    "trustedBoundaryReady": false,
                    "blockers": SOURCE_BLOCKERS,
                }),
            )),
            Request::RunModelPartComposition { .. }
            | Request::Cancel { .. }
            | Request::GetResult { .. } => Err(ContractError::new("authority_boundary_not_ready")),
        }
    }
}

impl Default for ReadOnlyAuthority {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityGenerationBinding {
    current_generation: [u8; 32],
    service_executable_sha256: [u8; 32],
    service_executable_path_sha256: [u8; 32],
    service_executable_file_identity_sha256: [u8; 32],
    service_process_id: u32,
    service_process_started_at: u64,
    protected_manifest_readback_sha256: [u8; 32],
    protected_key_readback_sha256: [u8; 32],
    signer_key_id: [u8; 32],
    protected_ledger_readback_sha256: [u8; 32],
    scm_readback_sha256: [u8; 32],
    bootstrap_receipt_sha256: [u8; 32],
}

impl AuthorityGenerationBinding {
    #[allow(clippy::too_many_arguments)]
    #[allow(dead_code)]
    pub(crate) fn new(
        current_generation: [u8; 32],
        service_executable_sha256: [u8; 32],
        service_executable_path_sha256: [u8; 32],
        service_executable_file_identity_sha256: [u8; 32],
        service_process_id: u32,
        service_process_started_at: u64,
        protected_manifest_readback_sha256: [u8; 32],
        protected_key_readback_sha256: [u8; 32],
        signer_key_id: [u8; 32],
        protected_ledger_readback_sha256: [u8; 32],
        scm_readback_sha256: [u8; 32],
        bootstrap_receipt_sha256: [u8; 32],
    ) -> Result<Self, ContractError> {
        let digests = [
            &current_generation,
            &service_executable_sha256,
            &service_executable_path_sha256,
            &service_executable_file_identity_sha256,
            &protected_manifest_readback_sha256,
            &protected_key_readback_sha256,
            &signer_key_id,
            &protected_ledger_readback_sha256,
            &scm_readback_sha256,
            &bootstrap_receipt_sha256,
        ];
        if digests
            .into_iter()
            .any(|digest| digest.iter().all(|byte| *byte == 0))
            || service_process_id == 0
            || service_process_started_at == 0
        {
            return Err(ContractError::new("authority_generation_binding_invalid"));
        }
        Ok(Self {
            current_generation,
            service_executable_sha256,
            service_executable_path_sha256,
            service_executable_file_identity_sha256,
            service_process_id,
            service_process_started_at,
            protected_manifest_readback_sha256,
            protected_key_readback_sha256,
            signer_key_id,
            protected_ledger_readback_sha256,
            scm_readback_sha256,
            bootstrap_receipt_sha256,
        })
    }

    #[allow(dead_code)]
    pub fn current_generation(&self) -> &[u8; 32] {
        &self.current_generation
    }

    #[allow(dead_code)]
    pub fn signer_key_id(&self) -> &[u8; 32] {
        &self.signer_key_id
    }

    pub fn service_instance_digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(SERVICE_INSTANCE_DOMAIN);
        digest.update(self.current_generation);
        digest.update(self.service_executable_sha256);
        digest.update(self.service_executable_path_sha256);
        digest.update(self.service_executable_file_identity_sha256);
        digest.update(self.service_process_id.to_be_bytes());
        digest.update(self.service_process_started_at.to_be_bytes());
        digest.update(fixed_pipe_identity_digest());
        digest.update(self.protected_manifest_readback_sha256);
        digest.update(self.protected_key_readback_sha256);
        digest.update(self.signer_key_id);
        digest.update(self.protected_ledger_readback_sha256);
        digest.update(self.scm_readback_sha256);
        digest.update(self.bootstrap_receipt_sha256);
        digest.finalize().into()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityPeerBinding {
    process_id: u32,
    process_started_at: u64,
    session_id: u32,
    executable_sha256: [u8; 32],
    executable_file_identity_sha256: [u8; 32],
}

impl AuthorityPeerBinding {
    pub(crate) fn new(
        process_id: u32,
        process_started_at: u64,
        session_id: u32,
        executable_sha256: [u8; 32],
        executable_file_identity_sha256: [u8; 32],
    ) -> Result<Self, ContractError> {
        if process_id == 0
            || process_started_at == 0
            || session_id == 0
            || executable_sha256.iter().all(|byte| *byte == 0)
            || executable_file_identity_sha256
                .iter()
                .all(|byte| *byte == 0)
        {
            return Err(ContractError::new("authority_peer_binding_invalid"));
        }
        Ok(Self {
            process_id,
            process_started_at,
            session_id,
            executable_sha256,
            executable_file_identity_sha256,
        })
    }

    pub fn digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(PEER_BINDING_DOMAIN);
        digest.update(self.process_id.to_be_bytes());
        digest.update(self.process_started_at.to_be_bytes());
        digest.update(self.session_id.to_be_bytes());
        digest.update(self.executable_sha256);
        digest.update(self.executable_file_identity_sha256);
        digest.finalize().into()
    }
}

pub trait AuthorityGenerationBindingVerifier {
    fn verify_current_generation_binding(
        &mut self,
    ) -> Result<AuthorityGenerationBinding, ContractError>;
}

pub trait AuthorityPeerBindingVerifier {
    fn verify_current_peer_binding(&mut self) -> Result<AuthorityPeerBinding, ContractError>;
}

pub(crate) trait AuthorityGenerationAttestationSigner {
    fn signer_key_id(&self) -> [u8; 32];

    fn sign_attestation_digest(&mut self, digest: &[u8; 32]) -> Result<[u8; 64], ContractError>;
}

pub(crate) trait AuthorityProjectionCommitReceiptVerifier {
    fn projection_commit_receipt_signer_key_id(&self) -> [u8; 32];

    fn verify_projection_commit_receipt_signature(
        &mut self,
        receipt_digest: &[u8; 32],
        signature: &[u8; 64],
    ) -> Result<(), ContractError>;
}

pub(crate) trait AuthorityProjectionCommitReceiptSigner:
    AuthorityProjectionCommitReceiptVerifier
{
    fn sign_projection_commit_receipt_digest(
        &mut self,
        receipt_digest: &[u8; 32],
    ) -> Result<[u8; 64], ContractError>;
}

pub trait AuthorityRuntimeHandler {
    fn handle_runtime_command(
        &self,
        command: AuthorityRuntimeCommand,
    ) -> Result<AuthorityRuntimeReply, AuthorityRuntimeError>;
}

impl AuthorityRuntimeHandler for AuthorityRuntime {
    fn handle_runtime_command(
        &self,
        command: AuthorityRuntimeCommand,
    ) -> Result<AuthorityRuntimeReply, AuthorityRuntimeError> {
        self.handle(command)
    }
}

#[derive(Debug, Default)]
pub struct HandshakeReplayGuard {
    state: Mutex<HandshakeReplayState>,
}

#[derive(Debug, Default)]
struct HandshakeReplayState {
    challenges: BTreeSet<[u8; 32]>,
    sequence: u64,
}

impl HandshakeReplayGuard {
    fn claim(&self, challenge: [u8; 32]) -> Result<u64, ContractError> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| ContractError::new("handshake_replay_guard_failed"))?;
        if state.challenges.contains(&challenge) {
            return Err(ContractError::new("handshake_challenge_replayed"));
        }
        if state.challenges.len() >= MAX_HANDSHAKES_PER_SERVICE_INSTANCE
            || state.sequence == u64::MAX
        {
            return Err(ContractError::new("handshake_replay_guard_exhausted"));
        }
        state.challenges.insert(challenge);
        state.sequence += 1;
        Ok(state.sequence)
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct AuthorityGenerationAttestation {
    schema: &'static str,
    proof_algorithm: &'static str,
    policy_id: &'static str,
    current_generation: String,
    service_instance_digest: String,
    service_executable_sha256: String,
    service_executable_path_sha256: String,
    service_executable_file_identity_sha256: String,
    service_process_id: u32,
    service_process_started_at: u64,
    pipe_name: &'static str,
    fixed_pipe_identity_digest: String,
    protected_manifest_readback_sha256: String,
    protected_key_readback_sha256: String,
    signer_key_id: String,
    protected_ledger_readback_sha256: String,
    scm_readback_sha256: String,
    bootstrap_receipt_sha256: String,
    peer_binding_sha256: String,
    challenge: String,
    sequence: u64,
    attestation_digest: String,
    signature_p256: String,
}

/// Consumes the run-only native handle capability before runtime dispatch.
///
/// `stage_service_owned_run` must duplicate and validate the peer handles and
/// place the resulting service-owned active bundle in the supervisor's pending
/// admission slot before returning success. An error must leave that one-use
/// capability burned and all partial handles closed. Raw peer handle values
/// must never be forwarded to `AuthorityRuntimeHandler`.
pub trait FixedModelPartHandleAdmission {
    fn stage_service_owned_run(
        &mut self,
        request_id: &str,
        handle_tokens: ExternalModelPartHandleTokens,
    ) -> Result<(), ContractError>;

    fn commit_runtime_start(&mut self, request_id: &str) -> Result<(), ContractError>;

    fn abort_runtime_start(&mut self, request_id: &str) -> Result<(), ContractError>;
}

/// Typed non-run command observed only after the authenticated handshake.
/// The production admission implementation uses this to consume the exact
/// installed-controller launch before runtime dispatch.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FixedControllerCommand<'a> {
    Status,
    SelfTest,
    Cancel { request_id: &'a str },
    GetResult { request_id: &'a str },
}

pub trait InstalledControllerAdmission: FixedModelPartHandleAdmission {
    fn admit_non_run_command(
        &mut self,
        command: FixedControllerCommand<'_>,
    ) -> Result<(), ContractError>;
}

pub struct AuthorityServiceSession<'a, H, V, P, S> {
    runtime: &'a H,
    binding_verifier: &'a mut V,
    peer_verifier: &'a mut P,
    signer: &'a mut S,
    replay_guard: &'a HandshakeReplayGuard,
    handle_admission: Option<&'a mut dyn FixedModelPartHandleAdmission>,
    controller_admission: Option<&'a mut dyn InstalledControllerAdmission>,
    accepted_binding: Option<AuthorityGenerationBinding>,
    accepted_peer: Option<AuthorityPeerBinding>,
    poisoned: bool,
}

impl<'a, H, V, P, S> AuthorityServiceSession<'a, H, V, P, S>
where
    H: AuthorityRuntimeHandler,
    V: AuthorityGenerationBindingVerifier,
    P: AuthorityPeerBindingVerifier,
    S: AuthorityGenerationAttestationSigner + AuthorityProjectionCommitReceiptSigner,
{
    pub fn new(
        runtime: &'a H,
        binding_verifier: &'a mut V,
        peer_verifier: &'a mut P,
        signer: &'a mut S,
        replay_guard: &'a HandshakeReplayGuard,
    ) -> Self {
        Self {
            runtime,
            binding_verifier,
            peer_verifier,
            signer,
            replay_guard,
            handle_admission: None,
            controller_admission: None,
            accepted_binding: None,
            accepted_peer: None,
            poisoned: false,
        }
    }

    #[allow(dead_code)]
    pub fn new_with_handle_admission(
        runtime: &'a H,
        binding_verifier: &'a mut V,
        peer_verifier: &'a mut P,
        signer: &'a mut S,
        replay_guard: &'a HandshakeReplayGuard,
        handle_admission: &'a mut dyn FixedModelPartHandleAdmission,
    ) -> Self {
        Self {
            runtime,
            binding_verifier,
            peer_verifier,
            signer,
            replay_guard,
            handle_admission: Some(handle_admission),
            controller_admission: None,
            accepted_binding: None,
            accepted_peer: None,
            poisoned: false,
        }
    }

    pub fn new_with_installed_controller_admission(
        runtime: &'a H,
        binding_verifier: &'a mut V,
        peer_verifier: &'a mut P,
        signer: &'a mut S,
        replay_guard: &'a HandshakeReplayGuard,
        controller_admission: &'a mut dyn InstalledControllerAdmission,
    ) -> Self {
        Self {
            runtime,
            binding_verifier,
            peer_verifier,
            signer,
            replay_guard,
            handle_admission: None,
            controller_admission: Some(controller_admission),
            accepted_binding: None,
            accepted_peer: None,
            poisoned: false,
        }
    }

    pub fn is_poisoned(&self) -> bool {
        self.poisoned
    }

    pub fn handle(&mut self, request: Request) -> Result<Value, ContractError> {
        if self.poisoned {
            return Err(ContractError::new("authority_session_poisoned"));
        }
        if let Err(error) = request.validate() {
            self.poisoned = true;
            return Err(error);
        }
        let command = request.command();
        match request {
            Request::Handshake {
                expected_generation,
                challenge,
                ..
            } => self.handle_handshake(command, &expected_generation, &challenge),
            request => {
                self.require_stable_binding()?;
                self.admit_non_run_controller_command(&request)?;
                match request {
                    Request::RunModelPartComposition {
                        request_id,
                        handle_tokens,
                        ..
                    } => self.handle_run(command, request_id, handle_tokens),
                    request => {
                        let runtime_command = request_to_runtime_command(request)?;
                        self.dispatch_runtime(command, runtime_command)
                    }
                }
            }
        }
    }

    fn handle_run(
        &mut self,
        command: &str,
        request_id: String,
        handle_tokens: ExternalModelPartHandleTokens,
    ) -> Result<Value, ContractError> {
        let stage_result = if let Some(admission) = self.controller_admission.as_deref_mut() {
            admission.stage_service_owned_run(&request_id, handle_tokens)
        } else if let Some(admission) = self.handle_admission.as_deref_mut() {
            admission.stage_service_owned_run(&request_id, handle_tokens)
        } else {
            Err(ContractError::new(
                "authority_model_part_handle_admission_not_connected",
            ))
        };
        if let Err(error) = stage_result {
            self.poisoned = true;
            return Err(error);
        }

        let runtime_result =
            self.runtime
                .handle_runtime_command(AuthorityRuntimeCommand::RunModelPartComposition {
                    request_id: request_id.clone(),
                });
        match runtime_result {
            Ok(AuthorityRuntimeReply::RunStarted {
                request_id: started_request_id,
            }) if started_request_id == request_id => {
                if let Err(error) = self.commit_runtime_start(&request_id) {
                    self.poisoned = true;
                    if let Err(cleanup_error) = self.abort_runtime_start(&request_id) {
                        return Err(cleanup_error);
                    }
                    return Err(error);
                }
                Ok(success_response(
                    command,
                    runtime_reply_value(AuthorityRuntimeReply::RunStarted {
                        request_id: started_request_id,
                    })?,
                ))
            }
            Ok(_) => {
                let cleanup = self.abort_runtime_start(&request_id);
                self.poisoned = true;
                cleanup?;
                Err(ContractError::new(
                    "authority_model_part_runtime_start_reply_invalid",
                ))
            }
            Err(error) => {
                if let Err(cleanup_error) = self.abort_runtime_start(&request_id) {
                    self.poisoned = true;
                    return Err(cleanup_error);
                }
                if error.requires_process_exit() {
                    self.poisoned = true;
                }
                Err(ContractError::new(error.code()))
            }
        }
    }

    fn admit_non_run_controller_command(&mut self, request: &Request) -> Result<(), ContractError> {
        let command = match request {
            Request::Status { .. } => Some(FixedControllerCommand::Status),
            Request::SelfTest { .. } => Some(FixedControllerCommand::SelfTest),
            Request::Cancel { request_id, .. } => Some(FixedControllerCommand::Cancel {
                request_id: request_id.as_str(),
            }),
            Request::GetResult { request_id, .. } => Some(FixedControllerCommand::GetResult {
                request_id: request_id.as_str(),
            }),
            Request::Handshake { .. } | Request::RunModelPartComposition { .. } => None,
        };
        if let (Some(admission), Some(command)) =
            (self.controller_admission.as_deref_mut(), command)
        {
            if let Err(error) = admission.admit_non_run_command(command) {
                self.poisoned = true;
                return Err(error);
            }
        }
        Ok(())
    }

    fn commit_runtime_start(&mut self, request_id: &str) -> Result<(), ContractError> {
        if let Some(admission) = self.controller_admission.as_deref_mut() {
            admission.commit_runtime_start(request_id)
        } else if let Some(admission) = self.handle_admission.as_deref_mut() {
            admission.commit_runtime_start(request_id)
        } else {
            Err(ContractError::new(
                "authority_model_part_handle_admission_not_connected",
            ))
        }
    }

    fn abort_runtime_start(&mut self, request_id: &str) -> Result<(), ContractError> {
        if let Some(admission) = self.controller_admission.as_deref_mut() {
            admission.abort_runtime_start(request_id)
        } else if let Some(admission) = self.handle_admission.as_deref_mut() {
            admission.abort_runtime_start(request_id)
        } else {
            Err(ContractError::new(
                "authority_model_part_handle_admission_not_connected",
            ))
        }
    }

    fn dispatch_runtime(
        &mut self,
        command: &str,
        runtime_command: AuthorityRuntimeCommand,
    ) -> Result<Value, ContractError> {
        match self.runtime.handle_runtime_command(runtime_command) {
            Ok(reply) => match self.runtime_reply_value(reply) {
                Ok(value) => Ok(success_response(command, value)),
                Err(error) => {
                    self.poisoned = true;
                    Err(error)
                }
            },
            Err(error) => {
                if error.requires_process_exit() {
                    self.poisoned = true;
                }
                Err(ContractError::new(error.code()))
            }
        }
    }

    fn runtime_reply_value(
        &mut self,
        reply: AuthorityRuntimeReply,
    ) -> Result<Value, ContractError> {
        match reply {
            AuthorityRuntimeReply::ResultExact {
                request_id,
                projection,
                receipt,
            } => {
                self.require_stable_binding()?;
                let accepted_binding = self
                    .accepted_binding
                    .clone()
                    .ok_or_else(|| ContractError::new("authority_handshake_required"))?;
                verified_projection_response_with_signed_receipt(
                    request_id,
                    &projection,
                    &receipt,
                    &accepted_binding,
                    self.signer,
                )
            }
            reply => runtime_reply_value(reply),
        }
    }

    fn handle_handshake(
        &mut self,
        command: &str,
        expected_generation: &str,
        challenge: &str,
    ) -> Result<Value, ContractError> {
        if self.accepted_binding.is_some() {
            self.poisoned = true;
            return Err(ContractError::new("authority_handshake_duplicate"));
        }
        let expected_generation =
            require_hex_32(expected_generation, "expected_generation_invalid")?;
        let challenge_bytes = require_hex_32(challenge, "handshake_challenge_invalid")?;
        let binding = match self.binding_verifier.verify_current_generation_binding() {
            Ok(value) => value,
            Err(error) => {
                self.poisoned = true;
                return Err(error);
            }
        };
        let peer = match self.peer_verifier.verify_current_peer_binding() {
            Ok(value) => value,
            Err(error) => {
                self.poisoned = true;
                return Err(error);
            }
        };
        if binding.current_generation != expected_generation {
            self.poisoned = true;
            return Err(ContractError::new("authority_generation_drift"));
        }
        if binding.signer_key_id != self.signer.signer_key_id() {
            self.poisoned = true;
            return Err(ContractError::new("authority_attestation_signer_mismatch"));
        }
        let sequence = match self.replay_guard.claim(challenge_bytes) {
            Ok(value) => value,
            Err(error) => {
                self.poisoned = true;
                return Err(error);
            }
        };
        let attestation = match issue_generation_attestation(
            &binding,
            &peer,
            challenge,
            challenge_bytes,
            sequence,
            self.signer,
        ) {
            Ok(value) => value,
            Err(error) => {
                self.poisoned = true;
                return Err(error);
            }
        };
        let attestation = match serde_json::to_value(attestation) {
            Ok(value) => value,
            Err(_) => {
                self.poisoned = true;
                return Err(ContractError::new("attestation_serialization_failed"));
            }
        };
        self.accepted_binding = Some(binding);
        self.accepted_peer = Some(peer);
        Ok(success_response(command, attestation))
    }

    fn require_stable_binding(&mut self) -> Result<(), ContractError> {
        let Some(expected) = self.accepted_binding.as_ref() else {
            self.poisoned = true;
            return Err(ContractError::new("authority_handshake_required"));
        };
        let Some(expected_peer) = self.accepted_peer.as_ref() else {
            self.poisoned = true;
            return Err(ContractError::new("authority_peer_binding_missing"));
        };
        let current = match self.binding_verifier.verify_current_generation_binding() {
            Ok(value) => value,
            Err(error) => {
                self.poisoned = true;
                return Err(error);
            }
        };
        if &current != expected || current.signer_key_id != self.signer.signer_key_id() {
            self.poisoned = true;
            return Err(ContractError::new("authority_service_binding_changed"));
        }
        let current_peer = match self.peer_verifier.verify_current_peer_binding() {
            Ok(value) => value,
            Err(error) => {
                self.poisoned = true;
                return Err(error);
            }
        };
        if &current_peer != expected_peer {
            self.poisoned = true;
            return Err(ContractError::new("authority_peer_binding_changed"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProtocolExit {
    PeerClosed,
    StopRequested,
    Fatal,
}

#[allow(dead_code)]
pub fn run_authority_service_protocol<R, W, H, V, P, S, F>(
    reader: &mut R,
    writer: &mut W,
    session: &mut AuthorityServiceSession<'_, H, V, P, S>,
    mut stop_requested: F,
) -> Result<ProtocolExit, ContractError>
where
    R: Read,
    W: Write,
    H: AuthorityRuntimeHandler,
    V: AuthorityGenerationBindingVerifier,
    P: AuthorityPeerBindingVerifier,
    S: AuthorityGenerationAttestationSigner + AuthorityProjectionCommitReceiptSigner,
    F: FnMut() -> bool,
{
    loop {
        if stop_requested() {
            return Ok(ProtocolExit::StopRequested);
        }
        let request = match read_request_frame(reader) {
            Ok(Some(request)) => request,
            Ok(None) => return Ok(ProtocolExit::PeerClosed),
            Err(error) => {
                let _ = write_response_frame(writer, &error_response(error.code()));
                return Err(error);
            }
        };
        if stop_requested() {
            return Ok(ProtocolExit::StopRequested);
        }
        let response = match session.handle(request) {
            Ok(response) => response,
            Err(error) => error_response(error.code()),
        };
        let poisoned = session.is_poisoned();
        if stop_requested() {
            return Ok(if poisoned {
                ProtocolExit::Fatal
            } else {
                ProtocolExit::StopRequested
            });
        }
        write_response_frame(writer, &response)?;
        if poisoned {
            return Ok(ProtocolExit::Fatal);
        }
    }
}

pub fn run_authority_service_duplex_protocol<T, H, V, P, S, F>(
    transport: &mut T,
    session: &mut AuthorityServiceSession<'_, H, V, P, S>,
    mut stop_requested: F,
) -> Result<ProtocolExit, ContractError>
where
    T: Read + Write,
    H: AuthorityRuntimeHandler,
    V: AuthorityGenerationBindingVerifier,
    P: AuthorityPeerBindingVerifier,
    S: AuthorityGenerationAttestationSigner + AuthorityProjectionCommitReceiptSigner,
    F: FnMut() -> bool,
{
    loop {
        if stop_requested() {
            return Ok(ProtocolExit::StopRequested);
        }
        let request = match read_request_frame(transport) {
            Ok(Some(request)) => request,
            Ok(None) => return Ok(ProtocolExit::PeerClosed),
            Err(error) => {
                let _ = write_response_frame(transport, &error_response(error.code()));
                return Err(error);
            }
        };
        if stop_requested() {
            return Ok(ProtocolExit::StopRequested);
        }
        let response = match session.handle(request) {
            Ok(response) => response,
            Err(error) => error_response(error.code()),
        };
        let poisoned = session.is_poisoned();
        if stop_requested() {
            return Ok(if poisoned {
                ProtocolExit::Fatal
            } else {
                ProtocolExit::StopRequested
            });
        }
        write_response_frame(transport, &response)?;
        if poisoned {
            return Ok(ProtocolExit::Fatal);
        }
    }
}

/// Runs the installed-controller protocol for exactly one authenticated command.
///
/// A connection must send one handshake frame followed by at most one command
/// frame. After the command response is written, this function returns without
/// attempting to read another frame so dropping the transport closes the
/// one-use connection. A peer that closes after a successful handshake has not
/// dispatched a command and is treated as a normal, fail-closed disconnect.
pub fn run_authority_service_single_command_duplex_protocol<T, H, V, P, S, F>(
    transport: &mut T,
    session: &mut AuthorityServiceSession<'_, H, V, P, S>,
    mut stop_requested: F,
) -> Result<ProtocolExit, ContractError>
where
    T: Read + Write,
    H: AuthorityRuntimeHandler,
    V: AuthorityGenerationBindingVerifier,
    P: AuthorityPeerBindingVerifier,
    S: AuthorityGenerationAttestationSigner + AuthorityProjectionCommitReceiptSigner,
    F: FnMut() -> bool,
{
    if stop_requested() {
        return Ok(ProtocolExit::StopRequested);
    }
    let handshake = match read_request_frame(transport) {
        Ok(Some(request)) => request,
        Ok(None) => return Ok(ProtocolExit::PeerClosed),
        Err(error) => {
            let _ = write_response_frame(transport, &error_response(error.code()));
            return Err(error);
        }
    };
    if !matches!(&handshake, Request::Handshake { .. }) {
        let error = ContractError::new("authority_handshake_required");
        write_response_frame(transport, &error_response(error.code()))?;
        return Ok(ProtocolExit::Fatal);
    }
    if stop_requested() {
        return Ok(ProtocolExit::StopRequested);
    }
    let handshake_response = match session.handle(handshake) {
        Ok(response) => response,
        Err(error) => {
            write_response_frame(transport, &error_response(error.code()))?;
            return Ok(ProtocolExit::Fatal);
        }
    };
    if stop_requested() {
        return Ok(ProtocolExit::StopRequested);
    }
    write_response_frame(transport, &handshake_response)?;
    if session.is_poisoned() {
        return Ok(ProtocolExit::Fatal);
    }

    if stop_requested() {
        return Ok(ProtocolExit::StopRequested);
    }
    let command = match read_request_frame(transport) {
        Ok(Some(request)) => request,
        Ok(None) => return Ok(ProtocolExit::PeerClosed),
        Err(error) => {
            let _ = write_response_frame(transport, &error_response(error.code()));
            return Err(error);
        }
    };
    if stop_requested() {
        return Ok(ProtocolExit::StopRequested);
    }
    let response = match session.handle(command) {
        Ok(response) => response,
        Err(error) => error_response(error.code()),
    };
    let poisoned = session.is_poisoned();
    if stop_requested() {
        return Ok(if poisoned {
            ProtocolExit::Fatal
        } else {
            ProtocolExit::StopRequested
        });
    }
    write_response_frame(transport, &response)?;
    if poisoned {
        Ok(ProtocolExit::Fatal)
    } else {
        Ok(ProtocolExit::PeerClosed)
    }
}

fn request_to_runtime_command(request: Request) -> Result<AuthorityRuntimeCommand, ContractError> {
    match request {
        Request::Status { .. } => Ok(AuthorityRuntimeCommand::Status),
        Request::SelfTest { .. } => Ok(AuthorityRuntimeCommand::SelfTest),
        Request::RunModelPartComposition { .. } => Err(ContractError::new(
            "authority_model_part_handle_admission_required",
        )),
        Request::Cancel { request_id, .. } => Ok(AuthorityRuntimeCommand::Cancel { request_id }),
        Request::GetResult { request_id, .. } => {
            Ok(AuthorityRuntimeCommand::GetResult { request_id })
        }
        Request::Handshake { .. } => Err(ContractError::new("authority_handshake_invalid")),
    }
}

fn runtime_reply_value(reply: AuthorityRuntimeReply) -> Result<Value, ContractError> {
    let value = match reply {
        AuthorityRuntimeReply::Status(status) => serde_json::json!({
            "trustedBoundaryReady": status.trusted_boundary_ready,
            "globalFailure": status.global_failure,
            "blockers": status.blockers,
            "activeRequestId": status.active_request_id,
            "startupBurnedTickets": status.startup_burned_tickets,
        }),
        AuthorityRuntimeReply::SelfTest(result) => serde_json::json!({
            "passed": result.passed,
            "trustedBoundaryReady": result.trusted_boundary_ready,
            "blockers": result.blockers,
        }),
        AuthorityRuntimeReply::RunStarted { request_id } => serde_json::json!({
            "state": "started",
            "requestId": request_id,
        }),
        AuthorityRuntimeReply::CancelRequested {
            request_id,
            already_requested,
        } => serde_json::json!({
            "state": "cancelRequested",
            "requestId": request_id,
            "alreadyRequested": already_requested,
        }),
        AuthorityRuntimeReply::AlreadyTerminated { request_id, reason } => serde_json::json!({
            "state": "terminated",
            "requestId": request_id,
            "reason": terminal_kind(reason),
        }),
        AuthorityRuntimeReply::ResultPending { request_id } => serde_json::json!({
            "state": "pending",
            "requestId": request_id,
        }),
        AuthorityRuntimeReply::ResultExact { .. } => {
            return Err(ContractError::new(
                "authority_projection_commit_signature_required",
            ))
        }
        AuthorityRuntimeReply::ResultTerminated { request_id, reason } => serde_json::json!({
            "state": "terminated",
            "requestId": request_id,
            "reason": terminal_kind(reason),
        }),
    };
    Ok(value)
}

fn verified_projection_response_with_signed_receipt<S>(
    request_id: String,
    projection: &crate::primitive_basis_protected_evidence_bundle::VerifiedAuthorityResultProjection,
    receipt: &DurableProjectionCommitReceipt,
    accepted_binding: &AuthorityGenerationBinding,
    signer: &mut S,
) -> Result<Value, ContractError>
where
    S: AuthorityGenerationAttestationSigner + AuthorityProjectionCommitReceiptSigner,
{
    let signer_key_id = signer.signer_key_id();
    let accepted_generation = accepted_binding.current_generation();
    let expected_ledger_identity =
        LedgerIdentity::from_digests(*accepted_generation, signer_key_id)
            .map_err(|_| ContractError::new("authority_projection_commit_identity_invalid"))?
            .canonical_digest();
    if signer.projection_commit_receipt_signer_key_id() != signer_key_id
        || accepted_binding.signer_key_id() != &signer_key_id
        || receipt.authority_generation_digest() != accepted_generation
        || receipt.ledger_identity_digest() != &expected_ledger_identity
        || projection.authority_generation_digest() != accepted_generation
        || projection.ledger_identity_digest() != &expected_ledger_identity
    {
        return Err(ContractError::new(
            "authority_projection_commit_identity_mismatch",
        ));
    }
    if !receipt.verifies_for(
        accepted_generation,
        &expected_ledger_identity,
        projection.ticket_digest(),
        projection.run_binding_digest(),
        projection.canonical_bytes(),
    ) || receipt.projection_digest() != projection.sha256()
    {
        return Err(ContractError::new(
            "authority_projection_commit_receipt_mismatch",
        ));
    }
    let signed_receipt = issue_signed_projection_commit_receipt(
        receipt,
        accepted_generation,
        &signer_key_id,
        signer,
    )?;
    let mut response = verified_projection_response(
        request_id,
        projection.canonical_bytes(),
        projection.sha256(),
    )?;
    response
        .as_object_mut()
        .ok_or_else(|| ContractError::new("authority_result_response_invalid"))?
        .insert("projectionCommitReceipt".to_owned(), signed_receipt);
    Ok(response)
}

fn projection_commit_receipt_unsigned_value(
    receipt: &DurableProjectionCommitReceipt,
    signer_key_id: &[u8; 32],
) -> Value {
    serde_json::json!({
        "schema": PROJECTION_COMMIT_RECEIPT_SCHEMA,
        "event": receipt.event(),
        "proofAlgorithm": PROJECTION_COMMIT_RECEIPT_PROOF_ALGORITHM,
        "signerKeyId": hex_lower(signer_key_id),
        "authorityGenerationDigest": hex_lower(receipt.authority_generation_digest()),
        "ledgerIdentityDigest": hex_lower(receipt.ledger_identity_digest()),
        "ticketDigest": hex_lower(receipt.ticket_digest()),
        "runBindingDigest": hex_lower(receipt.run_binding_digest()),
        "projectionDigest": hex_lower(receipt.projection_digest()),
        "projectionLength": receipt.projection_length(),
        "terminalSequence": receipt.terminal_sequence(),
        "terminalFrameDigest": hex_lower(receipt.terminal_frame_digest()),
        "terminalTicketDigest": hex_lower(receipt.terminal_ticket_digest()),
        "anchorSequence": receipt.anchor_sequence(),
        "anchorFrameDigest": hex_lower(receipt.anchor_frame_digest()),
        "anchorTicketDigest": hex_lower(receipt.anchor_ticket_digest()),
        "anchorRecordDigest": hex_lower(receipt.anchor_record_digest()),
        "reopenReadback": {
            "schema": "vrcforge.primitive_evidence_authority_projection_commit_readback.v1",
            "readbackKind": "heldAndReopenedStable",
            "ledgerFileDigest": hex_lower(receipt.ledger_file_digest()),
            "anchorFileDigest": hex_lower(receipt.anchor_file_digest()),
            "ledgerFileIdentityDigest": hex_lower(receipt.ledger_file_identity_digest()),
            "anchorFileIdentityDigest": hex_lower(receipt.anchor_file_identity_digest()),
            "ledgerLength": receipt.ledger_length(),
            "anchorLength": receipt.anchor_length(),
            "frameCount": receipt.frame_count(),
            "activeTicketCount": receipt.active_ticket_count(),
            "latestFrameDigest": hex_lower(receipt.latest_frame_digest()),
        },
    })
}

fn issue_signed_projection_commit_receipt<S>(
    receipt: &DurableProjectionCommitReceipt,
    accepted_generation: &[u8; 32],
    signer_key_id: &[u8; 32],
    signer: &mut S,
) -> Result<Value, ContractError>
where
    S: AuthorityProjectionCommitReceiptSigner,
{
    if receipt.authority_generation_digest() != accepted_generation {
        return Err(ContractError::new(
            "authority_projection_commit_identity_mismatch",
        ));
    }
    let expected_ledger_identity =
        LedgerIdentity::from_digests(*accepted_generation, *signer_key_id)
            .map_err(|_| ContractError::new("authority_projection_commit_identity_invalid"))?
            .canonical_digest();
    if receipt.ledger_identity_digest() != &expected_ledger_identity {
        return Err(ContractError::new(
            "authority_projection_commit_identity_mismatch",
        ));
    }
    let unsigned = projection_commit_receipt_unsigned_value(receipt, signer_key_id);
    let receipt_digest = projection_commit_receipt_digest(&unsigned)?;
    let signature = signer.sign_projection_commit_receipt_digest(&receipt_digest)?;
    if !p256_signature_is_canonical_low_s(&signature) {
        return Err(ContractError::new(
            "authority_projection_commit_signature_invalid",
        ));
    }
    let mut signed = unsigned;
    let object = signed
        .as_object_mut()
        .ok_or_else(|| ContractError::new("authority_projection_commit_receipt_invalid"))?;
    object.insert(
        "receiptDigest".to_owned(),
        Value::String(hex_lower(&receipt_digest)),
    );
    object.insert(
        "signatureP256".to_owned(),
        Value::String(hex_lower(&signature)),
    );
    let canonical = serde_json::to_vec(&signed)
        .map_err(|_| ContractError::new("authority_projection_commit_receipt_invalid"))?;
    let verified = decode_and_verify_signed_projection_commit_receipt(
        &canonical,
        accepted_generation,
        signer_key_id,
        signer,
    )?;
    if verified != signed {
        return Err(ContractError::new(
            "authority_projection_commit_receipt_mismatch",
        ));
    }
    Ok(signed)
}

fn projection_commit_receipt_digest(unsigned: &Value) -> Result<[u8; 32], ContractError> {
    let canonical = serde_json::to_vec(unsigned)
        .map_err(|_| ContractError::new("authority_projection_commit_receipt_invalid"))?;
    let length = u64::try_from(canonical.len())
        .map_err(|_| ContractError::new("authority_projection_commit_receipt_invalid"))?;
    let mut digest = Sha256::new();
    digest.update(PROJECTION_COMMIT_RECEIPT_DIGEST_DOMAIN);
    digest.update(length.to_be_bytes());
    digest.update(&canonical);
    Ok(digest.finalize().into())
}

pub(crate) fn decode_and_verify_signed_projection_commit_receipt<S>(
    payload: &[u8],
    accepted_generation: &[u8; 32],
    accepted_signer_key_id: &[u8; 32],
    verifier: &mut S,
) -> Result<Value, ContractError>
where
    S: AuthorityProjectionCommitReceiptVerifier,
{
    if payload.is_empty() || payload.len() > MAX_FRAME_SIZE {
        return Err(ContractError::new(
            "authority_projection_commit_receipt_size_invalid",
        ));
    }
    if verifier.projection_commit_receipt_signer_key_id() != *accepted_signer_key_id {
        return Err(ContractError::new(
            "authority_projection_commit_signer_mismatch",
        ));
    }
    let strict = serde_json::from_slice::<StrictJsonValue>(payload).map_err(|error| {
        if error.to_string().contains("duplicate_object_key") {
            ContractError::new("authority_projection_commit_receipt_duplicate_key")
        } else {
            ContractError::new("authority_projection_commit_receipt_json_invalid")
        }
    })?;
    let canonical = serde_json::to_vec(&strict.0)
        .map_err(|_| ContractError::new("authority_projection_commit_receipt_json_invalid"))?;
    if canonical != payload {
        return Err(ContractError::new(
            "authority_projection_commit_receipt_noncanonical",
        ));
    }
    require_exact_object_keys(
        &strict.0,
        &[
            "schema",
            "event",
            "proofAlgorithm",
            "signerKeyId",
            "authorityGenerationDigest",
            "ledgerIdentityDigest",
            "ticketDigest",
            "runBindingDigest",
            "projectionDigest",
            "projectionLength",
            "terminalSequence",
            "terminalFrameDigest",
            "terminalTicketDigest",
            "anchorSequence",
            "anchorFrameDigest",
            "anchorTicketDigest",
            "anchorRecordDigest",
            "reopenReadback",
            "receiptDigest",
            "signatureP256",
        ],
        "authority_projection_commit_receipt_shape_invalid",
    )?;
    let object = strict
        .0
        .as_object()
        .ok_or_else(|| ContractError::new("authority_projection_commit_receipt_shape_invalid"))?;
    if object.get("schema").and_then(Value::as_str) != Some(PROJECTION_COMMIT_RECEIPT_SCHEMA)
        || object.get("event").and_then(Value::as_str) != Some("projectionCommit")
        || object.get("proofAlgorithm").and_then(Value::as_str)
            != Some(PROJECTION_COMMIT_RECEIPT_PROOF_ALGORITHM)
    {
        return Err(ContractError::new(
            "authority_projection_commit_receipt_policy_invalid",
        ));
    }
    let generation = require_receipt_digest_field(
        object,
        "authorityGenerationDigest",
        "authority_projection_commit_generation_invalid",
    )?;
    let signer_key_id = require_receipt_digest_field(
        object,
        "signerKeyId",
        "authority_projection_commit_signer_invalid",
    )?;
    let ledger_identity = require_receipt_digest_field(
        object,
        "ledgerIdentityDigest",
        "authority_projection_commit_ledger_identity_invalid",
    )?;
    if &generation != accepted_generation || &signer_key_id != accepted_signer_key_id {
        return Err(ContractError::new(
            "authority_projection_commit_identity_mismatch",
        ));
    }
    let expected_ledger_identity = LedgerIdentity::from_digests(generation, signer_key_id)
        .map_err(|_| ContractError::new("authority_projection_commit_identity_invalid"))?
        .canonical_digest();
    if ledger_identity != expected_ledger_identity {
        return Err(ContractError::new(
            "authority_projection_commit_identity_mismatch",
        ));
    }
    for field in [
        "ticketDigest",
        "runBindingDigest",
        "projectionDigest",
        "terminalFrameDigest",
        "terminalTicketDigest",
        "anchorFrameDigest",
        "anchorTicketDigest",
        "anchorRecordDigest",
    ] {
        require_receipt_digest_field(
            object,
            field,
            "authority_projection_commit_receipt_digest_invalid",
        )?;
    }
    let projection_length = require_receipt_u64(object, "projectionLength")?;
    let terminal_sequence = require_receipt_u64(object, "terminalSequence")?;
    let anchor_sequence = require_receipt_u64(object, "anchorSequence")?;
    if projection_length == 0
        || projection_length > MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES as u64
        || terminal_sequence != anchor_sequence
        || object.get("terminalFrameDigest") != object.get("anchorFrameDigest")
        || object.get("terminalTicketDigest") != object.get("ticketDigest")
        || object.get("anchorTicketDigest") != object.get("ticketDigest")
    {
        return Err(ContractError::new(
            "authority_projection_commit_receipt_binding_invalid",
        ));
    }
    let readback = object
        .get("reopenReadback")
        .ok_or_else(|| ContractError::new("authority_projection_commit_readback_invalid"))?;
    require_exact_object_keys(
        readback,
        &[
            "schema",
            "readbackKind",
            "ledgerFileDigest",
            "anchorFileDigest",
            "ledgerFileIdentityDigest",
            "anchorFileIdentityDigest",
            "ledgerLength",
            "anchorLength",
            "frameCount",
            "activeTicketCount",
            "latestFrameDigest",
        ],
        "authority_projection_commit_readback_invalid",
    )?;
    let readback = readback
        .as_object()
        .ok_or_else(|| ContractError::new("authority_projection_commit_readback_invalid"))?;
    if readback.get("schema").and_then(Value::as_str)
        != Some("vrcforge.primitive_evidence_authority_projection_commit_readback.v1")
        || readback.get("readbackKind").and_then(Value::as_str) != Some("heldAndReopenedStable")
    {
        return Err(ContractError::new(
            "authority_projection_commit_readback_invalid",
        ));
    }
    for field in [
        "ledgerFileDigest",
        "anchorFileDigest",
        "ledgerFileIdentityDigest",
        "anchorFileIdentityDigest",
        "latestFrameDigest",
    ] {
        require_receipt_digest_field(
            readback,
            field,
            "authority_projection_commit_readback_invalid",
        )?;
    }
    let ledger_length = require_receipt_u64(readback, "ledgerLength")?;
    let anchor_length = require_receipt_u64(readback, "anchorLength")?;
    let frame_count = require_receipt_u64(readback, "frameCount")?;
    let active_ticket_count = require_receipt_u64(readback, "activeTicketCount")?;
    if ledger_length == 0
        || anchor_length == 0
        || terminal_sequence.checked_add(1) != Some(frame_count)
        || active_ticket_count != 0
        || readback.get("latestFrameDigest") != object.get("terminalFrameDigest")
    {
        return Err(ContractError::new(
            "authority_projection_commit_readback_invalid",
        ));
    }
    let receipt_digest = require_receipt_digest_field(
        object,
        "receiptDigest",
        "authority_projection_commit_receipt_digest_invalid",
    )?;
    let signature = require_hex_64(
        object
            .get("signatureP256")
            .and_then(Value::as_str)
            .ok_or_else(|| ContractError::new("authority_projection_commit_signature_invalid"))?,
        "authority_projection_commit_signature_invalid",
    )?;
    if !p256_signature_is_canonical_low_s(&signature) {
        return Err(ContractError::new(
            "authority_projection_commit_signature_invalid",
        ));
    }
    let mut unsigned = strict.0.clone();
    let unsigned_object = unsigned
        .as_object_mut()
        .ok_or_else(|| ContractError::new("authority_projection_commit_receipt_shape_invalid"))?;
    unsigned_object.remove("receiptDigest");
    unsigned_object.remove("signatureP256");
    if projection_commit_receipt_digest(&unsigned)? != receipt_digest {
        return Err(ContractError::new(
            "authority_projection_commit_receipt_digest_mismatch",
        ));
    }
    verifier.verify_projection_commit_receipt_signature(&receipt_digest, &signature)?;
    Ok(strict.0)
}

fn require_exact_object_keys(
    value: &Value,
    expected: &[&str],
    code: &'static str,
) -> Result<(), ContractError> {
    let object = value.as_object().ok_or_else(|| ContractError::new(code))?;
    let actual = object.keys().map(String::as_str).collect::<BTreeSet<_>>();
    let expected = expected.iter().copied().collect::<BTreeSet<_>>();
    if actual != expected {
        return Err(ContractError::new(code));
    }
    Ok(())
}

fn require_receipt_digest_field(
    object: &Map<String, Value>,
    field: &str,
    code: &'static str,
) -> Result<[u8; 32], ContractError> {
    let digest = object
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| ContractError::new(code))?;
    let digest = require_hex_32(digest, code)?;
    if digest.iter().all(|byte| *byte == 0) {
        return Err(ContractError::new(code));
    }
    Ok(digest)
}

fn require_receipt_u64(object: &Map<String, Value>, field: &str) -> Result<u64, ContractError> {
    object
        .get(field)
        .and_then(Value::as_u64)
        .ok_or_else(|| ContractError::new("authority_projection_commit_receipt_number_invalid"))
}

fn verified_projection_response(
    request_id: String,
    bytes: &[u8],
    expected_digest: &[u8; 32],
) -> Result<Value, ContractError> {
    if bytes.is_empty() || bytes.len() > MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES {
        return Err(ContractError::new(
            "authority_result_projection_size_invalid",
        ));
    }
    let digest: [u8; 32] = Sha256::digest(bytes).into();
    if &digest != expected_digest {
        return Err(ContractError::new(
            "authority_result_projection_digest_mismatch",
        ));
    }
    Ok(serde_json::json!({
        "state": "exact",
        "requestId": request_id,
        "size": bytes.len(),
        "sha256": hex_lower(&digest),
        "encoding": "base64url-no-pad",
        "bytesBase64Url": base64url_no_pad(bytes),
    }))
}

fn terminal_kind(value: RuntimeTerminalKind) -> &'static str {
    match value {
        RuntimeTerminalKind::Cancelled => "cancelled",
        RuntimeTerminalKind::TimedOut => "timedOut",
        RuntimeTerminalKind::Failed => "failed",
        RuntimeTerminalKind::RestartRecovery => "restartRecovery",
    }
}

fn issue_generation_attestation<S: AuthorityGenerationAttestationSigner>(
    binding: &AuthorityGenerationBinding,
    peer: &AuthorityPeerBinding,
    challenge_text: &str,
    challenge: [u8; 32],
    sequence: u64,
    signer: &mut S,
) -> Result<AuthorityGenerationAttestation, ContractError> {
    let peer_binding = peer.digest();
    let service_instance = binding.service_instance_digest();
    let fixed_pipe_identity = fixed_pipe_identity_digest();
    let mut digest = Sha256::new();
    digest.update(GENERATION_ATTESTATION_DOMAIN);
    digest.update(GENERATION_ATTESTATION_POLICY_ID.as_bytes());
    digest.update(GENERATION_ATTESTATION_PROOF_ALGORITHM.as_bytes());
    digest.update(fixed_pipe_identity);
    digest.update(service_instance);
    digest.update(peer_binding);
    digest.update(challenge);
    digest.update(sequence.to_be_bytes());
    let attestation_digest: [u8; 32] = digest.finalize().into();
    let signature = signer.sign_attestation_digest(&attestation_digest)?;
    if !p256_signature_is_canonical_low_s(&signature) {
        return Err(ContractError::new(
            "authority_attestation_signature_invalid",
        ));
    }
    Ok(AuthorityGenerationAttestation {
        schema: GENERATION_ATTESTATION_SCHEMA,
        proof_algorithm: GENERATION_ATTESTATION_PROOF_ALGORITHM,
        policy_id: GENERATION_ATTESTATION_POLICY_ID,
        current_generation: hex_lower(&binding.current_generation),
        service_instance_digest: hex_lower(&service_instance),
        service_executable_sha256: hex_lower(&binding.service_executable_sha256),
        service_executable_path_sha256: hex_lower(&binding.service_executable_path_sha256),
        service_executable_file_identity_sha256: hex_lower(
            &binding.service_executable_file_identity_sha256,
        ),
        service_process_id: binding.service_process_id,
        service_process_started_at: binding.service_process_started_at,
        pipe_name: AUTHORITY_PIPE_NAME,
        fixed_pipe_identity_digest: hex_lower(&fixed_pipe_identity),
        protected_manifest_readback_sha256: hex_lower(&binding.protected_manifest_readback_sha256),
        protected_key_readback_sha256: hex_lower(&binding.protected_key_readback_sha256),
        signer_key_id: hex_lower(&binding.signer_key_id),
        protected_ledger_readback_sha256: hex_lower(&binding.protected_ledger_readback_sha256),
        scm_readback_sha256: hex_lower(&binding.scm_readback_sha256),
        bootstrap_receipt_sha256: hex_lower(&binding.bootstrap_receipt_sha256),
        peer_binding_sha256: hex_lower(&peer_binding),
        challenge: challenge_text.to_owned(),
        sequence,
        attestation_digest: hex_lower(&attestation_digest),
        signature_p256: hex_lower(&signature),
    })
}

#[derive(Debug, Clone)]
struct StrictJsonValue(Value);

impl<'de> Deserialize<'de> for StrictJsonValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_any(StrictValueVisitor).map(Self)
    }
}

struct StrictValueVisitor;

impl<'de> de::Visitor<'de> for StrictValueVisitor {
    type Value = Value;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("strict JSON without duplicate keys or floating-point numbers")
    }

    fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E> {
        Ok(Value::Bool(value))
    }

    fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E> {
        Ok(Value::Number(Number::from(value)))
    }

    fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E> {
        Ok(Value::Number(Number::from(value)))
    }

    fn visit_f64<E>(self, _value: f64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Err(E::custom("floating_point_not_allowed"))
    }

    fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Ok(Value::String(value.to_owned()))
    }

    fn visit_string<E>(self, value: String) -> Result<Self::Value, E> {
        Ok(Value::String(value))
    }

    fn visit_none<E>(self) -> Result<Self::Value, E> {
        Ok(Value::Null)
    }

    fn visit_unit<E>(self) -> Result<Self::Value, E> {
        Ok(Value::Null)
    }

    fn visit_some<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: Deserializer<'de>,
    {
        StrictJsonValue::deserialize(deserializer).map(|value| value.0)
    }

    fn visit_seq<A>(self, mut sequence: A) -> Result<Self::Value, A::Error>
    where
        A: de::SeqAccess<'de>,
    {
        let mut values = Vec::new();
        while let Some(value) = sequence.next_element::<StrictJsonValue>()? {
            values.push(value.0);
        }
        Ok(Value::Array(values))
    }

    fn visit_map<A>(self, mut map: A) -> Result<Self::Value, A::Error>
    where
        A: de::MapAccess<'de>,
    {
        let mut values = Map::new();
        while let Some((key, value)) = map.next_entry::<String, StrictJsonValue>()? {
            if values.insert(key, value.0).is_some() {
                return Err(de::Error::custom("duplicate_object_key"));
            }
        }
        Ok(Value::Object(values))
    }
}

pub fn decode_request_payload(payload: &[u8]) -> Result<Request, ContractError> {
    if payload.is_empty() {
        return Err(ContractError::new("frame_empty"));
    }
    if payload.len() > MAX_FRAME_SIZE {
        return Err(ContractError::new("frame_too_large"));
    }
    let strict = serde_json::from_slice::<StrictJsonValue>(payload).map_err(|error| {
        let message = error.to_string();
        if message.contains("duplicate_object_key") {
            ContractError::new("duplicate_object_key")
        } else if message.contains("floating_point_not_allowed") {
            ContractError::new("floating_point_not_allowed")
        } else {
            ContractError::new("request_json_invalid")
        }
    })?;
    let canonical =
        serde_json::to_vec(&strict.0).map_err(|_| ContractError::new("request_json_invalid"))?;
    if canonical != payload {
        return Err(ContractError::new("request_json_noncanonical"));
    }
    let request = serde_json::from_value::<Request>(strict.0)
        .map_err(|_| ContractError::new("request_shape_invalid"))?;
    request.validate()?;
    Ok(request)
}

pub fn read_request_frame<R: Read>(reader: &mut R) -> Result<Option<Request>, ContractError> {
    let mut header = [0u8; 4];
    loop {
        match reader.read(&mut header[..1]) {
            Ok(0) => return Ok(None),
            Ok(1) => break,
            Ok(_) => unreachable!(),
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => return Err(ContractError::new("frame_read_failed")),
        }
    }
    reader
        .read_exact(&mut header[1..])
        .map_err(|error| match error.kind() {
            io::ErrorKind::UnexpectedEof => ContractError::new("frame_header_truncated"),
            _ => ContractError::new("frame_read_failed"),
        })?;
    let length = u32::from_be_bytes(header) as usize;
    if length == 0 {
        return Err(ContractError::new("frame_empty"));
    }
    if length > MAX_FRAME_SIZE {
        return Err(ContractError::new("frame_too_large"));
    }
    let mut payload = vec![0u8; length];
    reader
        .read_exact(&mut payload)
        .map_err(|error| match error.kind() {
            io::ErrorKind::UnexpectedEof => ContractError::new("frame_body_truncated"),
            _ => ContractError::new("frame_read_failed"),
        })?;
    decode_request_payload(&payload).map(Some)
}

pub fn write_response_frame<W: Write>(
    writer: &mut W,
    response: &Value,
) -> Result<(), ContractError> {
    let mut counter = CountingWriter::default();
    serde_json::to_writer(&mut counter, response)
        .map_err(|_| ContractError::new("response_serialization_failed"))?;
    if counter.length == 0 || counter.length > MAX_RESPONSE_FRAME_SIZE {
        return Err(ContractError::new("response_frame_invalid"));
    }
    let frame_length =
        u32::try_from(counter.length).map_err(|_| ContractError::new("response_frame_invalid"))?;
    writer
        .write_all(&frame_length.to_be_bytes())
        .map_err(|_| ContractError::new("response_write_failed"))
        .and_then(|_| {
            let mut frame = ExactFrameWriter::new(writer, counter.length);
            serde_json::to_writer(&mut frame, response)
                .map_err(|_| ContractError::new("response_write_failed"))?;
            frame.finish()
        })
}

#[derive(Debug, Default)]
struct CountingWriter {
    length: usize,
}

impl Write for CountingWriter {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        self.length = self.length.saturating_add(buffer.len());
        Ok(buffer.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

struct ExactFrameWriter<'a, W> {
    writer: &'a mut W,
    expected: usize,
    written: usize,
}

impl<'a, W: Write> ExactFrameWriter<'a, W> {
    fn new(writer: &'a mut W, expected: usize) -> Self {
        Self {
            writer,
            expected,
            written: 0,
        }
    }

    fn finish(self) -> Result<(), ContractError> {
        if self.written != self.expected {
            return Err(ContractError::new("response_write_incomplete"));
        }
        self.writer
            .flush()
            .map_err(|_| ContractError::new("response_write_failed"))
    }
}

impl<W: Write> Write for ExactFrameWriter<'_, W> {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        let remaining = self.expected.saturating_sub(self.written);
        if buffer.len() > remaining {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "response exceeded counted frame",
            ));
        }
        let written = self.writer.write(buffer)?;
        if written == 0 && !buffer.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::WriteZero,
                "response write made no progress",
            ));
        }
        self.written = self.written.saturating_add(written);
        Ok(written)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.writer.flush()
    }
}

pub fn run_read_only_protocol<R: Read, W: Write>(
    reader: &mut R,
    writer: &mut W,
) -> Result<(), ContractError> {
    let mut authority = ReadOnlyAuthority::new();
    loop {
        let request = match read_request_frame(reader) {
            Ok(Some(value)) => value,
            Ok(None) => return Ok(()),
            Err(error) => {
                write_response_frame(writer, &error_response(error.code()))?;
                return Err(error);
            }
        };
        let response = match authority.handle(request) {
            Ok(value) => value,
            Err(error) => error_response(error.code()),
        };
        write_response_frame(writer, &response)?;
    }
}

fn success_response(command: &str, result: Value) -> Value {
    serde_json::json!({
        "schema": RESPONSE_SCHEMA,
        "ok": true,
        "command": command,
        "result": result,
    })
}

fn error_response(code: &str) -> Value {
    serde_json::json!({
        "schema": RESPONSE_SCHEMA,
        "ok": false,
        "error": { "code": code },
    })
}

fn require_request_id(value: &str) -> Result<(), ContractError> {
    let mut characters = value.chars();
    let first = characters
        .next()
        .filter(|character| character.is_ascii_alphanumeric())
        .ok_or_else(|| ContractError::new("request_id_invalid"))?;
    let valid = first.is_ascii_alphanumeric()
        && value.len() <= 128
        && characters.all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.' | ':')
        });
    if !valid {
        return Err(ContractError::new("request_id_invalid"));
    }
    Ok(())
}

fn require_hex_32(value: &str, code: &'static str) -> Result<[u8; 32], ContractError> {
    if value.len() != 64
        || !value
            .as_bytes()
            .iter()
            .all(|byte| byte.is_ascii_digit() || matches!(*byte, b'a'..=b'f'))
    {
        return Err(ContractError::new(code));
    }
    let mut decoded = [0u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        decoded[index] = (hex_nibble(pair[0]) << 4) | hex_nibble(pair[1]);
    }
    Ok(decoded)
}

fn require_hex_64(value: &str, code: &'static str) -> Result<[u8; 64], ContractError> {
    if value.len() != 128
        || !value
            .as_bytes()
            .iter()
            .all(|byte| byte.is_ascii_digit() || matches!(*byte, b'a'..=b'f'))
    {
        return Err(ContractError::new(code));
    }
    let mut decoded = [0u8; 64];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        decoded[index] = (hex_nibble(pair[0]) << 4) | hex_nibble(pair[1]);
    }
    Ok(decoded)
}

fn hex_nibble(value: u8) -> u8 {
    match value {
        b'0'..=b'9' => value - b'0',
        b'a'..=b'f' => value - b'a' + 10,
        _ => 0,
    }
}

fn hex_lower(value: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        output.push(DIGITS[(byte >> 4) as usize] as char);
        output.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    output
}

fn base64url_no_pad(value: &[u8]) -> String {
    const DIGITS: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let full_chunks = value.len() / 3;
    let remainder = value.len() % 3;
    let mut output = String::with_capacity(
        full_chunks * 4
            + match remainder {
                0 => 0,
                1 => 2,
                _ => 3,
            },
    );
    for chunk in value.chunks_exact(3) {
        output.push(DIGITS[(chunk[0] >> 2) as usize] as char);
        output.push(DIGITS[(((chunk[0] & 0x03) << 4) | (chunk[1] >> 4)) as usize] as char);
        output.push(DIGITS[(((chunk[1] & 0x0f) << 2) | (chunk[2] >> 6)) as usize] as char);
        output.push(DIGITS[(chunk[2] & 0x3f) as usize] as char);
    }
    let tail = &value[full_chunks * 3..];
    if let Some(first) = tail.first() {
        output.push(DIGITS[(*first >> 2) as usize] as char);
        output.push(
            DIGITS[(((*first & 0x03) << 4) | (tail.get(1).copied().unwrap_or(0) >> 4)) as usize]
                as char,
        );
        if let Some(second) = tail.get(1) {
            output.push(DIGITS[((*second & 0x0f) << 2) as usize] as char);
        }
    }
    output
}

fn fixed_pipe_identity_digest() -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(FIXED_PIPE_IDENTITY_DOMAIN);
    digest.update(AUTHORITY_PIPE_NAME.as_bytes());
    digest.update(AUTHORITY_PIPE_SDDL.as_bytes());
    digest.update((MAX_FRAME_SIZE as u64).to_be_bytes());
    digest.update((MAX_RESPONSE_FRAME_SIZE as u64).to_be_bytes());
    digest.update(GENERATION_ATTESTATION_POLICY_ID.as_bytes());
    digest.finalize().into()
}

fn p256_signature_is_canonical_low_s(signature: &[u8; 64]) -> bool {
    let r: &[u8; 32] = signature[..32]
        .try_into()
        .expect("fixed raw P-256 signature shape");
    let s: &[u8; 32] = signature[32..]
        .try_into()
        .expect("fixed raw P-256 signature shape");
    scalar_is_nonzero_and_below(r, &P256_ORDER)
        && scalar_is_nonzero_and_below(s, &P256_ORDER)
        && s <= &P256_HALF_ORDER
}

fn scalar_is_nonzero_and_below(value: &[u8; 32], upper_bound: &[u8; 32]) -> bool {
    value.iter().any(|byte| *byte != 0) && value < upper_bound
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        cell::Cell,
        io::Cursor,
        sync::{
            atomic::{AtomicBool, Ordering},
            Arc,
        },
    };

    struct MockRuntime {
        commands: Mutex<Vec<AuthorityRuntimeCommand>>,
        run_was_staged: Option<Arc<AtomicBool>>,
    }

    impl MockRuntime {
        fn new() -> Self {
            Self {
                commands: Mutex::new(Vec::new()),
                run_was_staged: None,
            }
        }

        fn requiring_staged_run(marker: Arc<AtomicBool>) -> Self {
            Self {
                commands: Mutex::new(Vec::new()),
                run_was_staged: Some(marker),
            }
        }
    }

    impl AuthorityRuntimeHandler for MockRuntime {
        fn handle_runtime_command(
            &self,
            command: AuthorityRuntimeCommand,
        ) -> Result<AuthorityRuntimeReply, AuthorityRuntimeError> {
            if matches!(
                command,
                AuthorityRuntimeCommand::RunModelPartComposition { .. }
            ) && self
                .run_was_staged
                .as_ref()
                .is_some_and(|marker| !marker.load(Ordering::Acquire))
            {
                return Err(AuthorityRuntimeError::for_contract_test(
                    "authority_runtime_integrity_failed",
                ));
            }
            self.commands.lock().unwrap().push(command.clone());
            Ok(match command {
                AuthorityRuntimeCommand::Status => AuthorityRuntimeReply::Status(
                    crate::primitive_evidence_authority_runtime::AuthorityRuntimeStatus {
                        trusted_boundary_ready: true,
                        global_failure: false,
                        blockers: Vec::new(),
                        active_request_id: None,
                        startup_burned_tickets: 0,
                    },
                ),
                AuthorityRuntimeCommand::SelfTest => AuthorityRuntimeReply::SelfTest(
                    crate::primitive_evidence_authority_runtime::AuthorityRuntimeSelfTest {
                        passed: true,
                        trusted_boundary_ready: true,
                        blockers: Vec::new(),
                    },
                ),
                AuthorityRuntimeCommand::RunModelPartComposition { request_id } => {
                    AuthorityRuntimeReply::RunStarted { request_id }
                }
                AuthorityRuntimeCommand::Cancel { request_id } => {
                    AuthorityRuntimeReply::CancelRequested {
                        request_id,
                        already_requested: false,
                    }
                }
                AuthorityRuntimeCommand::GetResult { request_id } => {
                    AuthorityRuntimeReply::ResultPending { request_id }
                }
            })
        }
    }

    #[derive(Default)]
    struct MockHandleAdmission {
        staged: Vec<(String, [u64; 6])>,
        committed: Vec<String>,
        aborted: Vec<String>,
        stage_marker: Option<Arc<AtomicBool>>,
        fail_commit: bool,
    }

    impl FixedModelPartHandleAdmission for MockHandleAdmission {
        fn stage_service_owned_run(
            &mut self,
            request_id: &str,
            handle_tokens: ExternalModelPartHandleTokens,
        ) -> Result<(), ContractError> {
            self.staged
                .push((request_id.to_string(), handle_tokens.values()));
            if let Some(marker) = &self.stage_marker {
                marker.store(true, Ordering::Release);
            }
            Ok(())
        }

        fn commit_runtime_start(&mut self, request_id: &str) -> Result<(), ContractError> {
            self.committed.push(request_id.to_string());
            if self.fail_commit {
                Err(ContractError::new("test_model_part_handle_commit_failed"))
            } else {
                Ok(())
            }
        }

        fn abort_runtime_start(&mut self, request_id: &str) -> Result<(), ContractError> {
            self.aborted.push(request_id.to_string());
            Ok(())
        }
    }

    #[derive(Default)]
    struct MockInstalledControllerAdmission {
        handles: MockHandleAdmission,
        commands: Vec<(String, Option<String>)>,
        fail_non_run: bool,
    }

    impl FixedModelPartHandleAdmission for MockInstalledControllerAdmission {
        fn stage_service_owned_run(
            &mut self,
            request_id: &str,
            handle_tokens: ExternalModelPartHandleTokens,
        ) -> Result<(), ContractError> {
            self.handles
                .stage_service_owned_run(request_id, handle_tokens)
        }

        fn commit_runtime_start(&mut self, request_id: &str) -> Result<(), ContractError> {
            self.handles.commit_runtime_start(request_id)
        }

        fn abort_runtime_start(&mut self, request_id: &str) -> Result<(), ContractError> {
            self.handles.abort_runtime_start(request_id)
        }
    }

    impl InstalledControllerAdmission for MockInstalledControllerAdmission {
        fn admit_non_run_command(
            &mut self,
            command: FixedControllerCommand<'_>,
        ) -> Result<(), ContractError> {
            let observed = match command {
                FixedControllerCommand::Status => ("status".to_owned(), None),
                FixedControllerCommand::SelfTest => ("selfTest".to_owned(), None),
                FixedControllerCommand::Cancel { request_id } => {
                    ("cancel".to_owned(), Some(request_id.to_owned()))
                }
                FixedControllerCommand::GetResult { request_id } => {
                    ("getResult".to_owned(), Some(request_id.to_owned()))
                }
            };
            self.commands.push(observed);
            if self.fail_non_run {
                Err(ContractError::new(
                    "test_installed_controller_command_rejected",
                ))
            } else {
                Ok(())
            }
        }
    }

    struct ExactResultRuntime {
        projection:
            crate::primitive_basis_protected_evidence_bundle::VerifiedAuthorityResultProjection,
        receipt: DurableProjectionCommitReceipt,
        deliveries: Mutex<usize>,
    }

    impl AuthorityRuntimeHandler for ExactResultRuntime {
        fn handle_runtime_command(
            &self,
            command: AuthorityRuntimeCommand,
        ) -> Result<AuthorityRuntimeReply, AuthorityRuntimeError> {
            match command {
                AuthorityRuntimeCommand::GetResult { request_id } => {
                    *self.deliveries.lock().unwrap() += 1;
                    Ok(AuthorityRuntimeReply::ResultExact {
                        request_id,
                        projection: self.projection.clone(),
                        receipt: self.receipt.clone(),
                    })
                }
                _ => Err(AuthorityRuntimeError::for_contract_test(
                    "unexpected_test_command",
                )),
            }
        }
    }

    fn exact_result_runtime() -> ExactResultRuntime {
        exact_result_runtime_with_identity([0x31; 32], [0x19; 32])
    }

    fn exact_result_runtime_with_identity(
        authority_generation_digest: [u8; 32],
        signer_key_id: [u8; 32],
    ) -> ExactResultRuntime {
        let ticket_digest = [0x41; 32];
        let run_binding_digest = [0x42; 32];
        let canonical_projection = b"{\"projection\":true}".to_vec();
        let ledger_identity_digest =
            LedgerIdentity::from_digests(authority_generation_digest, signer_key_id)
                .unwrap()
                .canonical_digest();
        let projection = crate::primitive_basis_protected_evidence_bundle::VerifiedAuthorityResultProjection::for_signed_receipt_contract_test(
            canonical_projection.clone(),
            authority_generation_digest,
            ledger_identity_digest,
            ticket_digest,
            run_binding_digest,
        )
        .unwrap();
        let receipt = DurableProjectionCommitReceipt::for_runtime_test(
            authority_generation_digest,
            signer_key_id,
            ticket_digest,
            run_binding_digest,
            &canonical_projection,
        );
        ExactResultRuntime {
            projection,
            receipt,
            deliveries: Mutex::new(0),
        }
    }

    struct SequenceBindingVerifier {
        bindings: Vec<AuthorityGenerationBinding>,
        index: usize,
    }

    impl SequenceBindingVerifier {
        fn new(bindings: Vec<AuthorityGenerationBinding>) -> Self {
            Self { bindings, index: 0 }
        }
    }

    impl AuthorityGenerationBindingVerifier for SequenceBindingVerifier {
        fn verify_current_generation_binding(
            &mut self,
        ) -> Result<AuthorityGenerationBinding, ContractError> {
            let index = self.index.min(self.bindings.len().saturating_sub(1));
            self.index += 1;
            self.bindings
                .get(index)
                .cloned()
                .ok_or_else(|| ContractError::new("test_binding_missing"))
        }
    }

    struct MockSigner {
        key_id: [u8; 32],
        signed: Vec<[u8; 32]>,
        signed_projection_receipts: Vec<[u8; 32]>,
    }

    impl MockSigner {
        fn new() -> Self {
            Self {
                key_id: [0x19; 32],
                signed: Vec::new(),
                signed_projection_receipts: Vec::new(),
            }
        }
    }

    impl AuthorityGenerationAttestationSigner for MockSigner {
        fn signer_key_id(&self) -> [u8; 32] {
            self.key_id
        }

        fn sign_attestation_digest(
            &mut self,
            digest: &[u8; 32],
        ) -> Result<[u8; 64], ContractError> {
            self.signed.push(*digest);
            let mut signature = [0u8; 64];
            signature[31] = 1;
            signature[63] = 1;
            Ok(signature)
        }
    }

    impl AuthorityProjectionCommitReceiptVerifier for MockSigner {
        fn projection_commit_receipt_signer_key_id(&self) -> [u8; 32] {
            self.key_id
        }

        fn verify_projection_commit_receipt_signature(
            &mut self,
            receipt_digest: &[u8; 32],
            signature: &[u8; 64],
        ) -> Result<(), ContractError> {
            if signature != &mock_projection_commit_signature(receipt_digest) {
                return Err(ContractError::new(
                    "authority_projection_commit_signature_mismatch",
                ));
            }
            Ok(())
        }
    }

    impl AuthorityProjectionCommitReceiptSigner for MockSigner {
        fn sign_projection_commit_receipt_digest(
            &mut self,
            receipt_digest: &[u8; 32],
        ) -> Result<[u8; 64], ContractError> {
            self.signed_projection_receipts.push(*receipt_digest);
            Ok(mock_projection_commit_signature(receipt_digest))
        }
    }

    fn mock_projection_commit_signature(receipt_digest: &[u8; 32]) -> [u8; 64] {
        let mut signature = [0u8; 64];
        signature[31] = 1;
        signature[48..64].copy_from_slice(&receipt_digest[..16]);
        signature[63] |= 1;
        signature
    }

    struct HighSSigner;

    impl AuthorityGenerationAttestationSigner for HighSSigner {
        fn signer_key_id(&self) -> [u8; 32] {
            [0x19; 32]
        }

        fn sign_attestation_digest(
            &mut self,
            _digest: &[u8; 32],
        ) -> Result<[u8; 64], ContractError> {
            let mut signature = [0u8; 64];
            signature[31] = 1;
            signature[32..].copy_from_slice(&P256_HALF_ORDER);
            signature[63] = signature[63].saturating_add(1);
            Ok(signature)
        }
    }

    impl AuthorityProjectionCommitReceiptVerifier for HighSSigner {
        fn projection_commit_receipt_signer_key_id(&self) -> [u8; 32] {
            [0x19; 32]
        }

        fn verify_projection_commit_receipt_signature(
            &mut self,
            _receipt_digest: &[u8; 32],
            _signature: &[u8; 64],
        ) -> Result<(), ContractError> {
            Err(ContractError::new(
                "authority_projection_commit_signature_mismatch",
            ))
        }
    }

    impl AuthorityProjectionCommitReceiptSigner for HighSSigner {
        fn sign_projection_commit_receipt_digest(
            &mut self,
            _receipt_digest: &[u8; 32],
        ) -> Result<[u8; 64], ContractError> {
            let mut signature = [0u8; 64];
            signature[31] = 1;
            signature[32..].copy_from_slice(&P256_HALF_ORDER);
            signature[63] = signature[63].saturating_add(1);
            Ok(signature)
        }
    }

    fn binding(generation: u8, service_executable: u8) -> AuthorityGenerationBinding {
        AuthorityGenerationBinding::new(
            [generation; 32],
            [service_executable; 32],
            [0x12; 32],
            [0x13; 32],
            101,
            103,
            [0x14; 32],
            [0x15; 32],
            [0x19; 32],
            [0x16; 32],
            [0x17; 32],
            [0x18; 32],
        )
        .unwrap()
    }

    fn peer() -> AuthorityPeerBinding {
        AuthorityPeerBinding::new(201, 203, 7, [0x21; 32], [0x22; 32]).unwrap()
    }

    struct SequencePeerVerifier {
        peers: Vec<AuthorityPeerBinding>,
        index: usize,
    }

    impl SequencePeerVerifier {
        fn stable() -> Self {
            Self {
                peers: vec![peer()],
                index: 0,
            }
        }

        fn new(peers: Vec<AuthorityPeerBinding>) -> Self {
            Self { peers, index: 0 }
        }
    }

    impl AuthorityPeerBindingVerifier for SequencePeerVerifier {
        fn verify_current_peer_binding(&mut self) -> Result<AuthorityPeerBinding, ContractError> {
            let index = self.index.min(self.peers.len().saturating_sub(1));
            self.index += 1;
            self.peers
                .get(index)
                .cloned()
                .ok_or_else(|| ContractError::new("test_peer_binding_missing"))
        }
    }

    fn exact_result_response<S>(
        runtime: &ExactResultRuntime,
        signer: &mut S,
        challenge: u8,
    ) -> (Result<Value, ContractError>, bool)
    where
        S: AuthorityGenerationAttestationSigner + AuthorityProjectionCommitReceiptSigner,
    {
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0x31, 0x32)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let replay = HandshakeReplayGuard::default();
        let mut session = AuthorityServiceSession::new(
            runtime,
            &mut verifier,
            &mut peer_verifier,
            signer,
            &replay,
        );
        session
            .handle(decode_request_payload(&handshake([0x31; 32], [challenge; 32])).unwrap())
            .unwrap();
        let response = session.handle(
            decode_request_payload(&request("getResult", r#","requestId":"signed-result""#))
                .unwrap(),
        );
        let poisoned = session.is_poisoned();
        (response, poisoned)
    }

    struct FailingReceiptSigner {
        inner: MockSigner,
    }

    impl AuthorityGenerationAttestationSigner for FailingReceiptSigner {
        fn signer_key_id(&self) -> [u8; 32] {
            self.inner.signer_key_id()
        }

        fn sign_attestation_digest(
            &mut self,
            digest: &[u8; 32],
        ) -> Result<[u8; 64], ContractError> {
            self.inner.sign_attestation_digest(digest)
        }
    }

    impl AuthorityProjectionCommitReceiptVerifier for FailingReceiptSigner {
        fn projection_commit_receipt_signer_key_id(&self) -> [u8; 32] {
            self.inner.key_id
        }

        fn verify_projection_commit_receipt_signature(
            &mut self,
            _receipt_digest: &[u8; 32],
            _signature: &[u8; 64],
        ) -> Result<(), ContractError> {
            Err(ContractError::new(
                "authority_projection_commit_signature_mismatch",
            ))
        }
    }

    impl AuthorityProjectionCommitReceiptSigner for FailingReceiptSigner {
        fn sign_projection_commit_receipt_digest(
            &mut self,
            _receipt_digest: &[u8; 32],
        ) -> Result<[u8; 64], ContractError> {
            Err(ContractError::new(
                "authority_projection_commit_signing_failed",
            ))
        }
    }

    struct HighSReceiptSigner {
        inner: MockSigner,
    }

    impl AuthorityGenerationAttestationSigner for HighSReceiptSigner {
        fn signer_key_id(&self) -> [u8; 32] {
            self.inner.signer_key_id()
        }

        fn sign_attestation_digest(
            &mut self,
            digest: &[u8; 32],
        ) -> Result<[u8; 64], ContractError> {
            self.inner.sign_attestation_digest(digest)
        }
    }

    impl AuthorityProjectionCommitReceiptVerifier for HighSReceiptSigner {
        fn projection_commit_receipt_signer_key_id(&self) -> [u8; 32] {
            self.inner.key_id
        }

        fn verify_projection_commit_receipt_signature(
            &mut self,
            _receipt_digest: &[u8; 32],
            _signature: &[u8; 64],
        ) -> Result<(), ContractError> {
            Ok(())
        }
    }

    impl AuthorityProjectionCommitReceiptSigner for HighSReceiptSigner {
        fn sign_projection_commit_receipt_digest(
            &mut self,
            _receipt_digest: &[u8; 32],
        ) -> Result<[u8; 64], ContractError> {
            let mut signature = [0u8; 64];
            signature[31] = 1;
            signature[32..].copy_from_slice(&P256_HALF_ORDER);
            signature[63] = signature[63].saturating_add(1);
            Ok(signature)
        }
    }

    struct ChunkedDuplex {
        input: Cursor<Vec<u8>>,
        output: Vec<u8>,
        read_chunk: usize,
        write_chunk: usize,
    }

    impl Read for ChunkedDuplex {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            let length = buffer.len().min(self.read_chunk);
            self.input.read(&mut buffer[..length])
        }
    }

    impl Write for ChunkedDuplex {
        fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
            let length = buffer.len().min(self.write_chunk);
            self.output.extend_from_slice(&buffer[..length]);
            Ok(length)
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    fn request(command: &str, fields: &str) -> Vec<u8> {
        let handle_tokens = if command == "runModelPartComposition"
            && !fields.contains("\"handleTokens\"")
        {
            r#","handleTokens":["0000000000000011","0000000000000022","0000000000000033","0000000000000044","0000000000000055","0000000000000066"]"#
        } else {
            ""
        };
        let value: Value = serde_json::from_str(&format!(
            r#"{{"schema":"{}","command":"{}"{}{} }}"#,
            REQUEST_SCHEMA, command, fields, handle_tokens
        ))
        .unwrap();
        serde_json::to_vec(&value).unwrap()
    }

    fn handshake(expected_generation: [u8; 32], challenge: [u8; 32]) -> Vec<u8> {
        serde_json::to_vec(&serde_json::json!({
            "command": "handshake",
            "schema": REQUEST_SCHEMA,
            "expectedGeneration": hex_lower(&expected_generation),
            "challenge": hex_lower(&challenge),
        }))
        .unwrap()
    }

    fn frame(payload: &[u8]) -> Vec<u8> {
        [(payload.len() as u32).to_be_bytes().as_slice(), payload].concat()
    }

    fn response_frames(mut bytes: &[u8]) -> Vec<Value> {
        let mut responses = Vec::new();
        while !bytes.is_empty() {
            let length = u32::from_be_bytes(bytes[..4].try_into().unwrap()) as usize;
            responses.push(serde_json::from_slice(&bytes[4..4 + length]).unwrap());
            bytes = &bytes[4 + length..];
        }
        responses
    }

    #[test]
    fn exact_allowlist_parses_all_commands() {
        assert_eq!(
            REQUEST_SCHEMA,
            "vrcforge.primitive_evidence_authority_request.v2"
        );
        assert_eq!(
            RESPONSE_SCHEMA,
            "vrcforge.primitive_evidence_authority_response.v1"
        );
        let cases = [
            handshake([0x11; 32], [0x22; 32]),
            request("status", ""),
            request("selfTest", ""),
            request("runModelPartComposition", r#","requestId":"request-1""#),
            request("cancel", r#","requestId":"request-1""#),
            request("getResult", r#","requestId":"request-1""#),
        ];

        for payload in cases {
            decode_request_payload(&payload).expect("allowlisted command should parse");
        }
    }

    #[test]
    fn run_request_requires_exact_six_tokens_and_other_commands_reject_them() {
        let missing = serde_json::to_vec(&serde_json::json!({
            "command": "runModelPartComposition",
            "requestId": "request-1",
            "schema": REQUEST_SCHEMA,
        }))
        .unwrap();
        assert_eq!(
            decode_request_payload(&missing).unwrap_err().code(),
            "request_shape_invalid"
        );

        for invalid_tokens in [
            r#"[]"#,
            r#"["0000000000000011","0000000000000022","0000000000000033","0000000000000044","0000000000000055"]"#,
            r#"["0000000000000011","0000000000000022","0000000000000033","0000000000000044","0000000000000055","0000000000000066","0000000000000077"]"#,
            r#"["0000000000000011","0000000000000022","0000000000000033","0000000000000044","0000000000000055","0000000000000066","0000000000000077","0000000000000088"]"#,
            r#"["0000000000000011","0000000000000022","0000000000000033","0000000000000044","0000000000000055","0000000000000011"]"#,
            r#"["0000000000000011","0000000000000022","0000000000000033","0000000000000044","0000000000000055","00000000000000AA"]"#,
            r#"["0000000000000011","0000000000000022","0000000000000033","0000000000000044","0000000000000055","0000000000000000"]"#,
        ] {
            let payload = request(
                "runModelPartComposition",
                &format!(r#","requestId":"request-1","handleTokens":{invalid_tokens}"#),
            );
            assert_eq!(
                decode_request_payload(&payload).unwrap_err().code(),
                "request_shape_invalid"
            );
        }

        let cancel_with_tokens = request(
            "cancel",
            r#","requestId":"request-1","handleTokens":["0000000000000011","0000000000000022","0000000000000033","0000000000000044","0000000000000055","0000000000000066"]"#,
        );
        assert_eq!(
            decode_request_payload(&cancel_with_tokens)
                .unwrap_err()
                .code(),
            "request_shape_invalid"
        );

        let run = decode_request_payload(&request(
            "runModelPartComposition",
            r#","requestId":"request-redaction""#,
        ))
        .unwrap();
        let debug = format!("{run:?}");
        assert!(!debug.contains("0000000000000011"));
        assert!(!debug.contains("0000000000000066"));
    }

    #[test]
    fn legacy_schema_and_legacy_eight_tokens_fail_during_decode_without_side_effects() {
        let runtime = MockRuntime::new();
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0xb1, 0xb2)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = MockSigner::new();
        let replay = HandshakeReplayGuard::default();
        let mut admission = MockHandleAdmission::default();
        let session = AuthorityServiceSession::new_with_handle_admission(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
            &mut admission,
        );

        let legacy_schema = "vrcforge.primitive_evidence_authority_request.v1";
        let legacy_requests = [
            serde_json::json!({
                "challenge": "22".repeat(32),
                "command": "handshake",
                "expectedGeneration": "11".repeat(32),
                "schema": legacy_schema,
            }),
            serde_json::json!({"command":"status","schema":legacy_schema}),
            serde_json::json!({"command":"selfTest","schema":legacy_schema}),
            serde_json::json!({
                "command":"runModelPartComposition",
                "handleTokens":[
                    "0000000000000011","0000000000000022","0000000000000033",
                    "0000000000000044","0000000000000055","0000000000000066"
                ],
                "requestId":"legacy-run",
                "schema":legacy_schema,
            }),
            serde_json::json!({"command":"cancel","requestId":"legacy-run","schema":legacy_schema}),
            serde_json::json!({"command":"getResult","requestId":"legacy-run","schema":legacy_schema}),
        ];
        for value in legacy_requests {
            assert_eq!(
                decode_request_payload(&serde_json::to_vec(&value).unwrap())
                    .unwrap_err()
                    .code(),
                "request_schema_mismatch"
            );
        }

        let legacy_eight = serde_json::to_vec(&serde_json::json!({
            "command":"runModelPartComposition",
            "handleTokens":[
                "0000000000000011","0000000000000022","0000000000000033",
                "0000000000000044","0000000000000055","0000000000000066",
                "0000000000000077","0000000000000088"
            ],
            "requestId":"legacy-eight",
            "schema":REQUEST_SCHEMA,
        }))
        .unwrap();
        assert_eq!(
            decode_request_payload(&legacy_eight).unwrap_err().code(),
            "request_shape_invalid"
        );

        drop(session);
        assert_eq!(verifier.index, 0);
        assert_eq!(peer_verifier.index, 0);
        assert!(signer.signed.is_empty());
        assert!(signer.signed_projection_receipts.is_empty());
        assert!(admission.staged.is_empty());
        assert!(admission.committed.is_empty());
        assert!(admission.aborted.is_empty());
        assert!(runtime.commands.lock().unwrap().is_empty());
    }

    #[test]
    fn authority_commands_are_not_part_of_the_protocol() {
        for command in ["sign", "provision", "reset", "delete", "finalize"] {
            let error = decode_request_payload(&request(command, "")).unwrap_err();
            assert_eq!(error.code(), "request_shape_invalid");
        }
    }

    #[test]
    fn unknown_duplicate_float_and_oversize_payloads_are_rejected() {
        let unknown = serde_json::to_vec(&serde_json::json!({
            "schema": REQUEST_SCHEMA,
            "command": "status",
            "extra": true,
        }))
        .unwrap();
        assert_eq!(
            decode_request_payload(&unknown).unwrap_err().code(),
            "request_shape_invalid"
        );

        let duplicate = format!(
            r#"{{"schema":"{}","schema":"{}","command":"status"}}"#,
            REQUEST_SCHEMA, REQUEST_SCHEMA
        );
        assert_eq!(
            decode_request_payload(duplicate.as_bytes())
                .unwrap_err()
                .code(),
            "duplicate_object_key"
        );

        let float = format!(
            r#"{{"command":"status","schema":"{}","value":1.5}}"#,
            REQUEST_SCHEMA
        );
        assert_eq!(
            decode_request_payload(float.as_bytes()).unwrap_err().code(),
            "floating_point_not_allowed"
        );

        let oversize = vec![b' '; MAX_FRAME_SIZE + 1];
        assert_eq!(
            decode_request_payload(&oversize).unwrap_err().code(),
            "frame_too_large"
        );
    }

    #[test]
    fn noncanonical_json_is_rejected_before_dispatch() {
        let whitespace = format!(r#"{{ "command":"status","schema":"{}"}}"#, REQUEST_SCHEMA);
        assert_eq!(
            decode_request_payload(whitespace.as_bytes())
                .unwrap_err()
                .code(),
            "request_json_noncanonical"
        );

        let wrong_order = format!(r#"{{"schema":"{}","command":"status"}}"#, REQUEST_SCHEMA);
        assert_eq!(
            decode_request_payload(wrong_order.as_bytes())
                .unwrap_err()
                .code(),
            "request_json_noncanonical"
        );
    }

    #[test]
    fn peer_binding_rejects_noninteractive_session_zero() {
        assert_eq!(
            AuthorityPeerBinding::new(201, 203, 0, [0x21; 32], [0x22; 32])
                .unwrap_err()
                .code(),
            "authority_peer_binding_invalid"
        );
    }

    #[test]
    fn base64url_encoding_is_unpadded_and_canonical() {
        assert_eq!(base64url_no_pad(b""), "");
        assert_eq!(base64url_no_pad(b"f"), "Zg");
        assert_eq!(base64url_no_pad(b"fo"), "Zm8");
        assert_eq!(base64url_no_pad(b"foo"), "Zm9v");
        assert_eq!(base64url_no_pad(&[0xfb, 0xff]), "-_8");
    }

    #[test]
    fn request_size_gate_allows_exact_limit_and_rejects_over_one() {
        let exact = vec![b' '; MAX_FRAME_SIZE];
        assert_eq!(
            decode_request_payload(&exact).unwrap_err().code(),
            "request_json_invalid"
        );
        let over = vec![b' '; MAX_FRAME_SIZE + 1];
        assert_eq!(
            decode_request_payload(&over).unwrap_err().code(),
            "frame_too_large"
        );
    }

    #[test]
    fn projection_wire_accepts_exact_limit_and_rejects_over_one_or_digest_drift() {
        let exact = vec![0x5a; MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES];
        let exact_digest: [u8; 32] = Sha256::digest(&exact).into();
        let response =
            verified_projection_response("projection-max".to_string(), &exact, &exact_digest)
                .unwrap();
        assert_eq!(
            response["size"],
            MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES
        );
        assert_eq!(response["encoding"], "base64url-no-pad");
        assert!(!response["bytesBase64Url"].as_str().unwrap().contains('='));
        let mut counter = CountingWriter::default();
        write_response_frame(&mut counter, &response).unwrap();
        assert!(counter.length <= MAX_RESPONSE_FRAME_SIZE + 4);

        let over = vec![0x5a; MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES + 1];
        let over_digest: [u8; 32] = Sha256::digest(&over).into();
        assert_eq!(
            verified_projection_response("projection-over".to_string(), &over, &over_digest)
                .unwrap_err()
                .code(),
            "authority_result_projection_size_invalid"
        );
        assert_eq!(
            verified_projection_response("projection-drift".to_string(), b"{}", &[0x11; 32])
                .unwrap_err()
                .code(),
            "authority_result_projection_digest_mismatch"
        );
    }

    #[test]
    fn projection_commit_receipt_wire_is_exact_and_projection_bound() {
        let projection = b"{\"projection\":true}";
        let authority_generation_digest = [0x11; 32];
        let signer_key_id = [0x12; 32];
        let ticket_digest = [0x31; 32];
        let run_binding_digest = [0x32; 32];
        let ledger_identity_digest =
            crate::primitive_evidence_authority_ledger::LedgerIdentity::from_digests(
                authority_generation_digest,
                signer_key_id,
            )
            .unwrap()
            .canonical_digest();
        let receipt = DurableProjectionCommitReceipt::for_runtime_test(
            authority_generation_digest,
            signer_key_id,
            ticket_digest,
            run_binding_digest,
            projection,
        );
        assert!(receipt.verifies_for(
            &authority_generation_digest,
            &ledger_identity_digest,
            &ticket_digest,
            &run_binding_digest,
            projection,
        ));
        assert!(!receipt.verifies_for(
            &[0x13; 32],
            &ledger_identity_digest,
            &ticket_digest,
            &run_binding_digest,
            projection,
        ));
        assert!(!receipt.verifies_for(
            &authority_generation_digest,
            &[0x14; 32],
            &ticket_digest,
            &run_binding_digest,
            projection,
        ));
        assert!(!receipt.verifies_for(
            &authority_generation_digest,
            &ledger_identity_digest,
            &ticket_digest,
            &run_binding_digest,
            b"replacement",
        ));
        let projection_digest: [u8; 32] = Sha256::digest(projection).into();
        let value = projection_commit_receipt_unsigned_value(&receipt, &signer_key_id);
        assert_eq!(
            value,
            serde_json::json!({
                "schema": PROJECTION_COMMIT_RECEIPT_SCHEMA,
                "event": "projectionCommit",
                "proofAlgorithm": PROJECTION_COMMIT_RECEIPT_PROOF_ALGORITHM,
                "signerKeyId": hex_lower(&signer_key_id),
                "authorityGenerationDigest": hex_lower(&authority_generation_digest),
                "ledgerIdentityDigest": hex_lower(&ledger_identity_digest),
                "ticketDigest": hex_lower(&ticket_digest),
                "runBindingDigest": hex_lower(&run_binding_digest),
                "projectionDigest": hex_lower(&projection_digest),
                "projectionLength": projection.len(),
                "terminalSequence": 7,
                "terminalFrameDigest": hex_lower(&[0x71; 32]),
                "terminalTicketDigest": hex_lower(&ticket_digest),
                "anchorSequence": 7,
                "anchorFrameDigest": hex_lower(&[0x71; 32]),
                "anchorTicketDigest": hex_lower(&ticket_digest),
                "anchorRecordDigest": hex_lower(&[0x72; 32]),
                "reopenReadback": {
                    "schema": "vrcforge.primitive_evidence_authority_projection_commit_readback.v1",
                    "readbackKind": "heldAndReopenedStable",
                    "ledgerFileDigest": hex_lower(&[0x73; 32]),
                    "anchorFileDigest": hex_lower(&[0x74; 32]),
                    "ledgerFileIdentityDigest": hex_lower(&[0x75; 32]),
                    "anchorFileIdentityDigest": hex_lower(&[0x76; 32]),
                    "ledgerLength": 8 * 256,
                    "anchorLength": 16 * 576,
                    "frameCount": 8,
                    "activeTicketCount": 0,
                    "latestFrameDigest": hex_lower(&[0x71; 32]),
                },
            })
        );
        let encoded = serde_json::to_vec(&value).unwrap();
        let decoded: Value = serde_json::from_slice(&encoded).unwrap();
        assert_eq!(decoded, value);
    }

    #[test]
    fn signed_projection_commit_receipt_binds_exact_canonical_body_and_machine_identity() {
        let runtime = exact_result_runtime();
        let mut signer = MockSigner::new();
        let (response, poisoned) = exact_result_response(&runtime, &mut signer, 0x51);
        let response = response.unwrap();
        assert!(!poisoned);
        let receipt = response["result"]["projectionCommitReceipt"].clone();
        assert_eq!(receipt["schema"], PROJECTION_COMMIT_RECEIPT_SCHEMA);
        assert_eq!(
            receipt["proofAlgorithm"],
            PROJECTION_COMMIT_RECEIPT_PROOF_ALGORITHM
        );
        assert_eq!(receipt["signerKeyId"], "19".repeat(32));
        assert_eq!(receipt["authorityGenerationDigest"], "31".repeat(32));
        assert_eq!(
            receipt
                .as_object()
                .unwrap()
                .keys()
                .map(String::as_str)
                .collect::<BTreeSet<_>>(),
            [
                "anchorFrameDigest",
                "anchorRecordDigest",
                "anchorSequence",
                "anchorTicketDigest",
                "authorityGenerationDigest",
                "event",
                "ledgerIdentityDigest",
                "projectionDigest",
                "projectionLength",
                "proofAlgorithm",
                "receiptDigest",
                "reopenReadback",
                "runBindingDigest",
                "schema",
                "signatureP256",
                "signerKeyId",
                "terminalFrameDigest",
                "terminalSequence",
                "terminalTicketDigest",
                "ticketDigest",
            ]
            .into_iter()
            .collect::<BTreeSet<_>>()
        );
        let payload = serde_json::to_vec(&receipt).unwrap();
        assert_eq!(
            decode_and_verify_signed_projection_commit_receipt(
                &payload,
                &[0x31; 32],
                &[0x19; 32],
                &mut signer,
            )
            .unwrap(),
            receipt
        );
        let mut unsigned = receipt.clone();
        unsigned.as_object_mut().unwrap().remove("receiptDigest");
        unsigned.as_object_mut().unwrap().remove("signatureP256");
        let canonical_unsigned = serde_json::to_vec(&unsigned).unwrap();
        let mut expected = Sha256::new();
        expected.update(PROJECTION_COMMIT_RECEIPT_DIGEST_DOMAIN);
        expected.update((canonical_unsigned.len() as u64).to_be_bytes());
        expected.update(&canonical_unsigned);
        let expected: [u8; 32] = expected.finalize().into();
        assert_eq!(receipt["receiptDigest"], hex_lower(&expected));
        assert_eq!(signer.signed_projection_receipts, vec![expected]);
    }

    #[test]
    fn signed_projection_commit_receipt_rejects_every_body_replacement_after_digest_refresh() {
        let runtime = exact_result_runtime();
        let mut signer = MockSigner::new();
        let (response, poisoned) = exact_result_response(&runtime, &mut signer, 0x52);
        assert!(!poisoned);
        let signed = response.unwrap()["result"]["projectionCommitReceipt"].clone();
        let top_level_fields = [
            "schema",
            "event",
            "proofAlgorithm",
            "signerKeyId",
            "authorityGenerationDigest",
            "ledgerIdentityDigest",
            "ticketDigest",
            "runBindingDigest",
            "projectionDigest",
            "projectionLength",
            "terminalSequence",
            "terminalFrameDigest",
            "terminalTicketDigest",
            "anchorSequence",
            "anchorFrameDigest",
            "anchorTicketDigest",
            "anchorRecordDigest",
        ];
        let readback_fields = [
            "schema",
            "readbackKind",
            "ledgerFileDigest",
            "anchorFileDigest",
            "ledgerFileIdentityDigest",
            "anchorFileIdentityDigest",
            "ledgerLength",
            "anchorLength",
            "frameCount",
            "activeTicketCount",
            "latestFrameDigest",
        ];
        for (scope, field) in top_level_fields
            .iter()
            .map(|field| ("top", *field))
            .chain(readback_fields.iter().map(|field| ("readback", *field)))
        {
            let mut replaced = signed.clone();
            let target = if scope == "top" {
                replaced.as_object_mut().unwrap().get_mut(field).unwrap()
            } else {
                replaced["reopenReadback"]
                    .as_object_mut()
                    .unwrap()
                    .get_mut(field)
                    .unwrap()
            };
            replace_receipt_test_value(target);
            refresh_projection_commit_receipt_digest(&mut replaced);
            let payload = serde_json::to_vec(&replaced).unwrap();
            assert!(
                decode_and_verify_signed_projection_commit_receipt(
                    &payload,
                    &[0x31; 32],
                    &[0x19; 32],
                    &mut signer,
                )
                .is_err(),
                "replacement unexpectedly verified for {scope}.{field}"
            );
        }
    }

    #[test]
    fn signed_projection_commit_receipt_rejects_signature_algorithm_key_and_legacy_wire() {
        let runtime = exact_result_runtime();
        let mut signer = MockSigner::new();
        let (response, poisoned) = exact_result_response(&runtime, &mut signer, 0x53);
        assert!(!poisoned);
        let signed = response.unwrap()["result"]["projectionCommitReceipt"].clone();

        let mut replaced_signature = signed.clone();
        let mut other_canonical_signature = [0u8; 64];
        other_canonical_signature[31] = 2;
        other_canonical_signature[63] = 1;
        replaced_signature["signatureP256"] = Value::String(hex_lower(&other_canonical_signature));
        assert!(decode_and_verify_signed_projection_commit_receipt(
            &serde_json::to_vec(&replaced_signature).unwrap(),
            &[0x31; 32],
            &[0x19; 32],
            &mut signer,
        )
        .is_err());

        let mut replaced_digest = signed.clone();
        replaced_digest["receiptDigest"] = Value::String("ee".repeat(32));
        assert!(decode_and_verify_signed_projection_commit_receipt(
            &serde_json::to_vec(&replaced_digest).unwrap(),
            &[0x31; 32],
            &[0x19; 32],
            &mut signer,
        )
        .is_err());

        let mut high_s = signed.clone();
        let mut high_s_signature = [0u8; 64];
        high_s_signature[31] = 1;
        high_s_signature[32..].copy_from_slice(&P256_HALF_ORDER);
        high_s_signature[63] = high_s_signature[63].saturating_add(1);
        high_s["signatureP256"] = Value::String(hex_lower(&high_s_signature));
        assert!(decode_and_verify_signed_projection_commit_receipt(
            &serde_json::to_vec(&high_s).unwrap(),
            &[0x31; 32],
            &[0x19; 32],
            &mut signer,
        )
        .is_err());

        for (field, replacement) in [
            ("proofAlgorithm", Value::String("replacement".to_owned())),
            ("signerKeyId", Value::String("29".repeat(32))),
        ] {
            let mut replaced = signed.clone();
            replaced[field] = replacement;
            refresh_projection_commit_receipt_digest(&mut replaced);
            assert!(decode_and_verify_signed_projection_commit_receipt(
                &serde_json::to_vec(&replaced).unwrap(),
                &[0x31; 32],
                &[0x19; 32],
                &mut signer,
            )
            .is_err());
        }

        let mut legacy = signed.clone();
        legacy["schema"] = Value::String(
            "vrcforge.primitive_evidence_authority_projection_commit_receipt.v1".to_owned(),
        );
        legacy.as_object_mut().unwrap().remove("proofAlgorithm");
        legacy.as_object_mut().unwrap().remove("signerKeyId");
        legacy.as_object_mut().unwrap().remove("receiptDigest");
        legacy.as_object_mut().unwrap().remove("signatureP256");
        assert!(decode_and_verify_signed_projection_commit_receipt(
            &serde_json::to_vec(&legacy).unwrap(),
            &[0x31; 32],
            &[0x19; 32],
            &mut signer,
        )
        .is_err());

        let encoded = serde_json::to_string(&signed).unwrap();
        let duplicate = format!("{{\"event\":\"projectionCommit\",{}", &encoded[1..]);
        assert_eq!(
            decode_and_verify_signed_projection_commit_receipt(
                duplicate.as_bytes(),
                &[0x31; 32],
                &[0x19; 32],
                &mut signer,
            )
            .unwrap_err()
            .code(),
            "authority_projection_commit_receipt_duplicate_key"
        );
    }

    #[test]
    fn projection_commit_signing_failure_or_high_s_poison_the_service_session() {
        let runtime = exact_result_runtime();
        let mut failing = FailingReceiptSigner {
            inner: MockSigner::new(),
        };
        let (failure, poisoned) = exact_result_response(&runtime, &mut failing, 0x54);
        assert_eq!(
            failure.unwrap_err().code(),
            "authority_projection_commit_signing_failed"
        );
        assert!(poisoned);

        let mut high_s = HighSReceiptSigner {
            inner: MockSigner::new(),
        };
        let (failure, poisoned) = exact_result_response(&runtime, &mut high_s, 0x55);
        assert_eq!(
            failure.unwrap_err().code(),
            "authority_projection_commit_signature_invalid"
        );
        assert!(poisoned);
    }

    #[test]
    fn projection_commit_receipt_identity_is_rechecked_before_signing() {
        for runtime in [
            exact_result_runtime_with_identity([0x30; 32], [0x19; 32]),
            exact_result_runtime_with_identity([0x31; 32], [0x18; 32]),
        ] {
            let mut signer = MockSigner::new();
            let (failure, poisoned) = exact_result_response(&runtime, &mut signer, 0x58);
            assert_eq!(
                failure.unwrap_err().code(),
                "authority_projection_commit_identity_mismatch"
            );
            assert!(poisoned);
            assert!(signer.signed_projection_receipts.is_empty());
        }
    }

    #[test]
    fn projection_commit_can_be_resigned_after_restart_without_changing_ledger_facts() {
        let runtime = exact_result_runtime();
        let original_projection = runtime.projection.clone();
        let original_receipt = runtime.receipt.clone();
        let mut signer = MockSigner::new();
        let (first, first_poisoned) = exact_result_response(&runtime, &mut signer, 0x56);
        let (second, second_poisoned) = exact_result_response(&runtime, &mut signer, 0x57);
        assert!(!first_poisoned && !second_poisoned);
        let first = first.unwrap()["result"]["projectionCommitReceipt"].clone();
        let second = second.unwrap()["result"]["projectionCommitReceipt"].clone();
        assert_eq!(first["receiptDigest"], second["receiptDigest"]);
        assert_eq!(first, second);
        assert_eq!(runtime.projection, original_projection);
        assert_eq!(runtime.receipt, original_receipt);
        assert_eq!(*runtime.deliveries.lock().unwrap(), 2);
        assert_eq!(signer.signed_projection_receipts.len(), 2);
        assert_eq!(
            signer.signed_projection_receipts[0],
            signer.signed_projection_receipts[1]
        );
    }

    fn replace_receipt_test_value(value: &mut Value) {
        match value {
            Value::String(text) if text.len() == 64 => *text = "ff".repeat(32),
            Value::String(text) => text.push_str("-replacement"),
            Value::Number(number) => {
                *value = Value::Number(Number::from(number.as_u64().unwrap() + 1));
            }
            _ => panic!("unexpected receipt test value"),
        }
    }

    fn refresh_projection_commit_receipt_digest(value: &mut Value) {
        let mut unsigned = value.clone();
        unsigned.as_object_mut().unwrap().remove("receiptDigest");
        unsigned.as_object_mut().unwrap().remove("signatureP256");
        value["receiptDigest"] = Value::String(hex_lower(
            &projection_commit_receipt_digest(&unsigned).unwrap(),
        ));
    }

    #[test]
    fn raw_p256_signature_requires_nonzero_r_and_canonical_low_s() {
        let mut signature = [0u8; 64];
        signature[31] = 1;
        signature[63] = 1;
        assert!(p256_signature_is_canonical_low_s(&signature));

        let mut zero_r = signature;
        zero_r[..32].fill(0);
        assert!(!p256_signature_is_canonical_low_s(&zero_r));

        let mut zero_s = signature;
        zero_s[32..].fill(0);
        assert!(!p256_signature_is_canonical_low_s(&zero_s));

        let mut order_r = signature;
        order_r[..32].copy_from_slice(&P256_ORDER);
        assert!(!p256_signature_is_canonical_low_s(&order_r));

        let mut high_s = signature;
        high_s[32..].copy_from_slice(&P256_HALF_ORDER);
        high_s[63] = high_s[63].saturating_add(1);
        assert!(!p256_signature_is_canonical_low_s(&high_s));
    }

    #[test]
    fn response_frame_accepts_exact_limit_rejects_over_one_and_streams_partial_writes() {
        let overhead = serde_json::to_vec(&serde_json::json!({ "payload": "" }))
            .unwrap()
            .len();
        let exact = serde_json::json!({
            "payload": "x".repeat(MAX_RESPONSE_FRAME_SIZE - overhead),
        });
        let mut transport = ChunkedDuplex {
            input: Cursor::new(Vec::new()),
            output: Vec::new(),
            read_chunk: 1,
            write_chunk: 7,
        };
        write_response_frame(&mut transport, &exact).unwrap();
        assert_eq!(transport.output.len(), MAX_RESPONSE_FRAME_SIZE + 4);
        assert_eq!(
            u32::from_be_bytes(transport.output[..4].try_into().unwrap()) as usize,
            MAX_RESPONSE_FRAME_SIZE
        );
        drop(exact);
        drop(transport);

        let over = serde_json::json!({
            "payload": "x".repeat(MAX_RESPONSE_FRAME_SIZE - overhead + 1),
        });
        let mut output = Vec::new();
        assert_eq!(
            write_response_frame(&mut output, &over).unwrap_err().code(),
            "response_frame_invalid"
        );
        assert!(output.is_empty());
    }

    #[test]
    fn invalid_schema_ids_and_caller_supplied_manifest_are_rejected() {
        let wrong_schema = br#"{"command":"status","schema":"wrong"}"#;
        assert_eq!(
            decode_request_payload(wrong_schema).unwrap_err().code(),
            "request_schema_mismatch"
        );

        let unsafe_id = request("runModelPartComposition", r#","requestId":"../escape""#);
        assert_eq!(
            decode_request_payload(&unsafe_id).unwrap_err().code(),
            "request_id_invalid"
        );

        let caller_manifest = request(
            "runModelPartComposition",
            r#","requestId":"request-2","manifestDigest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa""#,
        );
        assert_eq!(
            decode_request_payload(&caller_manifest).unwrap_err().code(),
            "request_shape_invalid"
        );

        let caller_attestation = request(
            "status",
            r#","attestation":{"currentGeneration":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}"#,
        );
        assert_eq!(
            decode_request_payload(&caller_attestation)
                .unwrap_err()
                .code(),
            "request_shape_invalid"
        );
    }

    #[test]
    fn framed_reader_rejects_zero_large_and_truncated_frames() {
        let mut zero = Cursor::new(0u32.to_be_bytes().to_vec());
        assert_eq!(
            read_request_frame(&mut zero).unwrap_err().code(),
            "frame_empty"
        );

        let mut large = Cursor::new(((MAX_FRAME_SIZE + 1) as u32).to_be_bytes().to_vec());
        assert_eq!(
            read_request_frame(&mut large).unwrap_err().code(),
            "frame_too_large"
        );

        let mut truncated_header = Cursor::new(vec![0, 0, 0]);
        assert_eq!(
            read_request_frame(&mut truncated_header)
                .unwrap_err()
                .code(),
            "frame_header_truncated"
        );

        let mut truncated_body = Cursor::new([4u32.to_be_bytes().as_slice(), b"{}"].concat());
        assert_eq!(
            read_request_frame(&mut truncated_body).unwrap_err().code(),
            "frame_body_truncated"
        );
    }

    #[test]
    fn source_stub_is_read_only_and_fails_closed() {
        let mut authority = ReadOnlyAuthority::new();
        let status = authority
            .handle(decode_request_payload(&request("status", "")).unwrap())
            .unwrap();
        assert_eq!(status["result"]["trustedBoundaryReady"], false);
        assert_eq!(status["result"]["readOnly"], true);
        assert_eq!(
            status["result"]["blockers"],
            serde_json::json!(SOURCE_BLOCKERS)
        );

        let self_test = authority
            .handle(decode_request_payload(&request("selfTest", "")).unwrap())
            .unwrap();
        assert_eq!(self_test["result"]["trustedBoundaryReady"], false);

        for payload in [
            request("runModelPartComposition", r#","requestId":"request-3""#),
            request("cancel", r#","requestId":"request-3""#),
            request("getResult", r#","requestId":"request-3""#),
        ] {
            let command = decode_request_payload(&payload).unwrap();
            assert_eq!(
                authority.handle(command).unwrap_err().code(),
                "authority_boundary_not_ready"
            );
        }
    }

    #[test]
    fn framed_protocol_reports_status_but_rejects_run_and_result() {
        let status = request("status", "");
        let run = request("runModelPartComposition", r#","requestId":"request-4""#);
        let result = request("getResult", r#","requestId":"request-4""#);
        let mut input = Cursor::new([frame(&status), frame(&run), frame(&result)].concat());
        let mut output = Vec::new();

        run_read_only_protocol(&mut input, &mut output).unwrap();
        let responses = response_frames(&output);
        assert_eq!(responses.len(), 3);
        assert_eq!(responses[0]["ok"], true);
        assert_eq!(responses[0]["result"]["trustedBoundaryReady"], false);
        assert_eq!(responses[1]["ok"], false);
        assert_eq!(
            responses[1]["error"]["code"],
            "authority_boundary_not_ready"
        );
        assert_eq!(responses[2]["ok"], false);
        assert_eq!(
            responses[2]["error"]["code"],
            "authority_boundary_not_ready"
        );
    }

    #[test]
    fn handshake_is_required_and_returns_only_service_issued_binding() {
        let runtime = MockRuntime::new();
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0x31, 0x32)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = MockSigner::new();
        let replay = HandshakeReplayGuard::default();
        let mut session = AuthorityServiceSession::new(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
        );

        let before = session
            .handle(decode_request_payload(&request("status", "")).unwrap())
            .unwrap_err();
        assert_eq!(before.code(), "authority_handshake_required");
        assert!(session.is_poisoned());
        assert!(runtime.commands.lock().unwrap().is_empty());
        drop(session);

        let mut session = AuthorityServiceSession::new(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
        );

        let response = session
            .handle(decode_request_payload(&handshake([0x31; 32], [0x41; 32])).unwrap())
            .unwrap();
        assert_eq!(response["ok"], true);
        assert_eq!(response["result"]["schema"], GENERATION_ATTESTATION_SCHEMA);
        assert_eq!(
            response["result"]["proofAlgorithm"],
            GENERATION_ATTESTATION_PROOF_ALGORITHM
        );
        assert_eq!(
            response["result"]["policyId"],
            GENERATION_ATTESTATION_POLICY_ID
        );
        assert_eq!(response["result"]["currentGeneration"], "31".repeat(32));
        assert_eq!(response["result"]["pipeName"], AUTHORITY_PIPE_NAME);
        assert_eq!(response["result"]["serviceProcessId"], 101);
        assert_eq!(
            response["result"]["fixedPipeIdentityDigest"]
                .as_str()
                .unwrap()
                .len(),
            64
        );
        assert_eq!(
            response["result"]["serviceInstanceDigest"]
                .as_str()
                .unwrap()
                .len(),
            64
        );
        let fields = response["result"]
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect::<BTreeSet<_>>();
        assert_eq!(
            fields,
            [
                "attestationDigest",
                "bootstrapReceiptSha256",
                "challenge",
                "currentGeneration",
                "fixedPipeIdentityDigest",
                "peerBindingSha256",
                "pipeName",
                "policyId",
                "proofAlgorithm",
                "protectedKeyReadbackSha256",
                "protectedLedgerReadbackSha256",
                "protectedManifestReadbackSha256",
                "schema",
                "scmReadbackSha256",
                "sequence",
                "serviceExecutableFileIdentitySha256",
                "serviceExecutablePathSha256",
                "serviceExecutableSha256",
                "serviceInstanceDigest",
                "serviceProcessId",
                "serviceProcessStartedAt",
                "signatureP256",
                "signerKeyId",
            ]
            .into_iter()
            .collect()
        );
        assert_eq!(
            response["result"]["signatureP256"].as_str().unwrap().len(),
            128
        );
        let status = session
            .handle(decode_request_payload(&request("status", "")).unwrap())
            .unwrap();
        assert_eq!(status["result"]["trustedBoundaryReady"], true);
        assert_eq!(runtime.commands.lock().unwrap().len(), 1);
        assert_eq!(
            session
                .handle(decode_request_payload(&handshake([0x31; 32], [0x42; 32])).unwrap())
                .unwrap_err()
                .code(),
            "authority_handshake_duplicate"
        );
        assert!(session.is_poisoned());
        drop(session);
        assert_eq!(signer.signed.len(), 1);
    }

    #[test]
    fn generation_drift_and_service_replacement_poison_without_runtime_dispatch() {
        let runtime = MockRuntime::new();
        let replay = HandshakeReplayGuard::default();

        let mut drift_verifier = SequenceBindingVerifier::new(vec![binding(0x51, 0x52)]);
        let mut drift_peer_verifier = SequencePeerVerifier::stable();
        let mut drift_signer = MockSigner::new();
        let mut drift = AuthorityServiceSession::new(
            &runtime,
            &mut drift_verifier,
            &mut drift_peer_verifier,
            &mut drift_signer,
            &replay,
        );
        assert_eq!(
            drift
                .handle(decode_request_payload(&handshake([0x50; 32], [0x53; 32])).unwrap())
                .unwrap_err()
                .code(),
            "authority_generation_drift"
        );
        assert!(drift.is_poisoned());

        let mut replacement_verifier =
            SequenceBindingVerifier::new(vec![binding(0x61, 0x62), binding(0x61, 0x63)]);
        let mut replacement_peer_verifier = SequencePeerVerifier::stable();
        let mut replacement_signer = MockSigner::new();
        let mut replacement = AuthorityServiceSession::new(
            &runtime,
            &mut replacement_verifier,
            &mut replacement_peer_verifier,
            &mut replacement_signer,
            &replay,
        );
        replacement
            .handle(decode_request_payload(&handshake([0x61; 32], [0x64; 32])).unwrap())
            .unwrap();
        assert_eq!(
            replacement
                .handle(decode_request_payload(&request("status", "")).unwrap())
                .unwrap_err()
                .code(),
            "authority_service_binding_changed"
        );
        assert!(replacement.is_poisoned());
        assert!(runtime.commands.lock().unwrap().is_empty());
        drop(replacement);

        let changed_peer = AuthorityPeerBinding::new(202, 203, 7, [0x21; 32], [0x22; 32]).unwrap();
        let mut peer_binding_verifier = SequenceBindingVerifier::new(vec![binding(0x66, 0x67)]);
        let mut peer_verifier = SequencePeerVerifier::new(vec![peer(), changed_peer]);
        let mut peer_signer = MockSigner::new();
        let mut peer_drift = AuthorityServiceSession::new(
            &runtime,
            &mut peer_binding_verifier,
            &mut peer_verifier,
            &mut peer_signer,
            &replay,
        );
        peer_drift
            .handle(decode_request_payload(&handshake([0x66; 32], [0x68; 32])).unwrap())
            .unwrap();
        assert_eq!(
            peer_drift
                .handle(decode_request_payload(&request("status", "")).unwrap())
                .unwrap_err()
                .code(),
            "authority_peer_binding_changed"
        );
        assert!(peer_drift.is_poisoned());
        assert!(runtime.commands.lock().unwrap().is_empty());
    }

    #[test]
    fn protocol_emits_one_error_then_exits_fatal_after_binding_poison() {
        let runtime = MockRuntime::new();
        let mut verifier =
            SequenceBindingVerifier::new(vec![binding(0x69, 0x6a), binding(0x69, 0x6b)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = MockSigner::new();
        let replay = HandshakeReplayGuard::default();
        let mut session = AuthorityServiceSession::new(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
        );
        let mut input = Cursor::new(
            [
                frame(&handshake([0x69; 32], [0x6c; 32])),
                frame(&request("status", "")),
                frame(&request("selfTest", "")),
            ]
            .concat(),
        );
        let mut output = Vec::new();
        assert_eq!(
            run_authority_service_protocol(&mut input, &mut output, &mut session, || false)
                .unwrap(),
            ProtocolExit::Fatal
        );
        let responses = response_frames(&output);
        assert_eq!(responses.len(), 2);
        assert_eq!(responses[0]["ok"], true);
        assert_eq!(
            responses[1]["error"]["code"],
            "authority_service_binding_changed"
        );
        assert!(runtime.commands.lock().unwrap().is_empty());
    }

    #[test]
    fn post_dispatch_poison_cannot_be_downgraded_by_a_concurrent_stop() {
        {
            let runtime = MockRuntime::new();
            let mut verifier =
                SequenceBindingVerifier::new(vec![binding(0x6d, 0x6e), binding(0x6d, 0x6f)]);
            let mut peer_verifier = SequencePeerVerifier::stable();
            let mut signer = MockSigner::new();
            let replay = HandshakeReplayGuard::default();
            let mut session = AuthorityServiceSession::new(
                &runtime,
                &mut verifier,
                &mut peer_verifier,
                &mut signer,
                &replay,
            );
            let mut input = Cursor::new(
                [
                    frame(&handshake([0x6d; 32], [0x70; 32])),
                    frame(&request("status", "")),
                ]
                .concat(),
            );
            let mut output = Vec::new();
            let calls = Cell::new(0usize);
            let exit =
                run_authority_service_protocol(&mut input, &mut output, &mut session, || {
                    calls.set(calls.get() + 1);
                    calls.get() >= 6
                })
                .unwrap();
            assert_eq!(exit, ProtocolExit::Fatal);
            assert!(session.is_poisoned());
            assert_eq!(response_frames(&output).len(), 1);
        }

        for single_command in [false, true] {
            let runtime = MockRuntime::new();
            let mut verifier =
                SequenceBindingVerifier::new(vec![binding(0x71, 0x72), binding(0x71, 0x73)]);
            let mut peer_verifier = SequencePeerVerifier::stable();
            let mut signer = MockSigner::new();
            let replay = HandshakeReplayGuard::default();
            let mut session = AuthorityServiceSession::new(
                &runtime,
                &mut verifier,
                &mut peer_verifier,
                &mut signer,
                &replay,
            );
            let mut transport = ChunkedDuplex {
                input: Cursor::new(
                    [
                        frame(&handshake([0x71; 32], [0x74; 32])),
                        frame(&request("status", "")),
                    ]
                    .concat(),
                ),
                output: Vec::new(),
                read_chunk: 3,
                write_chunk: 5,
            };
            let calls = Cell::new(0usize);
            let stop = || {
                calls.set(calls.get() + 1);
                calls.get() >= 6
            };
            let exit = if single_command {
                run_authority_service_single_command_duplex_protocol(
                    &mut transport,
                    &mut session,
                    stop,
                )
            } else {
                run_authority_service_duplex_protocol(&mut transport, &mut session, stop)
            }
            .unwrap();
            assert_eq!(exit, ProtocolExit::Fatal);
            assert!(session.is_poisoned());
            assert_eq!(response_frames(&transport.output).len(), 1);
        }
    }

    #[test]
    fn handshake_challenge_cannot_replay_across_connections() {
        let runtime = MockRuntime::new();
        let replay = HandshakeReplayGuard::default();
        let challenge = [0x71; 32];

        let mut first_verifier = SequenceBindingVerifier::new(vec![binding(0x72, 0x73)]);
        let mut first_peer_verifier = SequencePeerVerifier::stable();
        let mut first_signer = MockSigner::new();
        let mut first = AuthorityServiceSession::new(
            &runtime,
            &mut first_verifier,
            &mut first_peer_verifier,
            &mut first_signer,
            &replay,
        );
        first
            .handle(decode_request_payload(&handshake([0x72; 32], challenge)).unwrap())
            .unwrap();
        drop(first);

        let mut second_verifier = SequenceBindingVerifier::new(vec![binding(0x72, 0x73)]);
        let mut second_peer_verifier = SequencePeerVerifier::stable();
        let mut second_signer = MockSigner::new();
        let mut second = AuthorityServiceSession::new(
            &runtime,
            &mut second_verifier,
            &mut second_peer_verifier,
            &mut second_signer,
            &replay,
        );
        assert_eq!(
            second
                .handle(decode_request_payload(&handshake([0x72; 32], challenge)).unwrap())
                .unwrap_err()
                .code(),
            "handshake_challenge_replayed"
        );
        assert!(second.is_poisoned());
        drop(second);
        assert!(second_signer.signed.is_empty());
    }

    #[test]
    fn attestation_rejects_noncanonical_high_s_signature() {
        let runtime = MockRuntime::new();
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0x75, 0x76)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = HighSSigner;
        let replay = HandshakeReplayGuard::default();
        let mut session = AuthorityServiceSession::new(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
        );
        assert_eq!(
            session
                .handle(decode_request_payload(&handshake([0x75; 32], [0x77; 32])).unwrap())
                .unwrap_err()
                .code(),
            "authority_attestation_signature_invalid"
        );
        assert!(session.is_poisoned());
        assert!(runtime.commands.lock().unwrap().is_empty());
    }

    #[test]
    fn run_capability_is_consumed_before_runtime_dispatch_and_default_is_fail_closed() {
        let runtime = MockRuntime::new();
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0x7a, 0x7b)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = MockSigner::new();
        let replay = HandshakeReplayGuard::default();
        let mut session = AuthorityServiceSession::new(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
        );
        session
            .handle(decode_request_payload(&handshake([0x7a; 32], [0x7c; 32])).unwrap())
            .unwrap();
        assert_eq!(
            session
                .handle(
                    decode_request_payload(&request(
                        "runModelPartComposition",
                        r#","requestId":"default-closed""#,
                    ))
                    .unwrap(),
                )
                .unwrap_err()
                .code(),
            "authority_model_part_handle_admission_not_connected"
        );
        assert!(session.is_poisoned());
        assert!(runtime.commands.lock().unwrap().is_empty());
        drop(session);

        let staged = Arc::new(AtomicBool::new(false));
        let runtime = MockRuntime::requiring_staged_run(Arc::clone(&staged));
        let mut admission = MockHandleAdmission {
            stage_marker: Some(staged),
            ..MockHandleAdmission::default()
        };
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0x7d, 0x7e)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = MockSigner::new();
        let mut session = AuthorityServiceSession::new_with_handle_admission(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
            &mut admission,
        );
        session
            .handle(decode_request_payload(&handshake([0x7d; 32], [0x7f; 32])).unwrap())
            .unwrap();
        let response = session
            .handle(
                decode_request_payload(&request(
                    "runModelPartComposition",
                    r#","requestId":"staged-first""#,
                ))
                .unwrap(),
            )
            .unwrap();
        assert_eq!(response["result"]["requestId"], "staged-first");
        let response_bytes = serde_json::to_vec(&response).unwrap();
        assert!(!response_bytes
            .windows("0000000000000011".len())
            .any(|window| window == b"0000000000000011"));
        assert!(!response_bytes
            .windows(b"handleTokens".len())
            .any(|window| window == b"handleTokens"));
        drop(session);
        assert_eq!(admission.staged.len(), 1);
        assert_eq!(admission.staged[0].0, "staged-first");
        assert_eq!(admission.staged[0].1, [0x11, 0x22, 0x33, 0x44, 0x55, 0x66]);
        assert_eq!(admission.committed, ["staged-first"]);
        assert!(admission.aborted.is_empty());
    }

    #[test]
    fn installed_controller_admission_consumes_the_typed_command_before_dispatch() {
        let replay = HandshakeReplayGuard::default();
        let runtime = MockRuntime::new();
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0xa1, 0xa2)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = MockSigner::new();
        let mut admission = MockInstalledControllerAdmission::default();
        let mut session = AuthorityServiceSession::new_with_installed_controller_admission(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
            &mut admission,
        );
        session
            .handle(decode_request_payload(&handshake([0xa1; 32], [0xa3; 32])).unwrap())
            .unwrap();
        assert_eq!(
            session
                .handle(decode_request_payload(&request("status", "")).unwrap())
                .unwrap()["ok"],
            true
        );
        drop(session);
        assert_eq!(admission.commands, [("status".to_owned(), None)]);
        assert_eq!(runtime.commands.lock().unwrap().len(), 1);

        let staged = Arc::new(AtomicBool::new(false));
        let runtime = MockRuntime::requiring_staged_run(Arc::clone(&staged));
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0xa4, 0xa5)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = MockSigner::new();
        let mut admission = MockInstalledControllerAdmission {
            handles: MockHandleAdmission {
                stage_marker: Some(staged),
                ..MockHandleAdmission::default()
            },
            ..MockInstalledControllerAdmission::default()
        };
        let mut session = AuthorityServiceSession::new_with_installed_controller_admission(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
            &mut admission,
        );
        session
            .handle(decode_request_payload(&handshake([0xa4; 32], [0xa6; 32])).unwrap())
            .unwrap();
        assert_eq!(
            session
                .handle(
                    decode_request_payload(&request(
                        "runModelPartComposition",
                        r#","requestId":"installed-run""#,
                    ))
                    .unwrap(),
                )
                .unwrap()["result"]["requestId"],
            "installed-run"
        );
        drop(session);
        assert_eq!(admission.handles.staged[0].0, "installed-run");
        assert_eq!(admission.handles.committed, ["installed-run"]);
        assert!(admission.commands.is_empty());

        let runtime = MockRuntime::new();
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0xa7, 0xa8)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = MockSigner::new();
        let mut admission = MockInstalledControllerAdmission {
            fail_non_run: true,
            ..MockInstalledControllerAdmission::default()
        };
        let mut session = AuthorityServiceSession::new_with_installed_controller_admission(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
            &mut admission,
        );
        session
            .handle(decode_request_payload(&handshake([0xa7; 32], [0xa9; 32])).unwrap())
            .unwrap();
        assert_eq!(
            session
                .handle(
                    decode_request_payload(&request("cancel", r#","requestId":"blocked-cancel""#,))
                        .unwrap(),
                )
                .unwrap_err()
                .code(),
            "test_installed_controller_command_rejected"
        );
        assert!(session.is_poisoned());
        assert!(runtime.commands.lock().unwrap().is_empty());
    }

    #[test]
    fn staged_run_is_aborted_when_runtime_start_or_commit_fails() {
        let replay = HandshakeReplayGuard::default();
        let marker = Arc::new(AtomicBool::new(false));
        let runtime = MockRuntime::requiring_staged_run(marker);
        let mut admission = MockHandleAdmission::default();
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0x84, 0x85)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = MockSigner::new();
        let mut session = AuthorityServiceSession::new_with_handle_admission(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
            &mut admission,
        );
        session
            .handle(decode_request_payload(&handshake([0x84; 32], [0x86; 32])).unwrap())
            .unwrap();
        assert_eq!(
            session
                .handle(
                    decode_request_payload(&request(
                        "runModelPartComposition",
                        r#","requestId":"runtime-failed""#,
                    ))
                    .unwrap(),
                )
                .unwrap_err()
                .code(),
            "authority_runtime_integrity_failed"
        );
        assert!(session.is_poisoned());
        drop(session);
        assert_eq!(admission.aborted, ["runtime-failed"]);

        let runtime = MockRuntime::new();
        let mut admission = MockHandleAdmission {
            fail_commit: true,
            ..MockHandleAdmission::default()
        };
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0x87, 0x88)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = MockSigner::new();
        let mut session = AuthorityServiceSession::new_with_handle_admission(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
            &mut admission,
        );
        session
            .handle(decode_request_payload(&handshake([0x87; 32], [0x89; 32])).unwrap())
            .unwrap();
        assert_eq!(
            session
                .handle(
                    decode_request_payload(&request(
                        "runModelPartComposition",
                        r#","requestId":"commit-failed""#,
                    ))
                    .unwrap(),
                )
                .unwrap_err()
                .code(),
            "test_model_part_handle_commit_failed"
        );
        assert!(session.is_poisoned());
        drop(session);
        assert_eq!(admission.committed, ["commit-failed"]);
        assert_eq!(admission.aborted, ["commit-failed"]);
    }

    #[test]
    fn five_commands_route_to_runtime_after_one_handshake() {
        let runtime = MockRuntime::new();
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0x81, 0x82)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = MockSigner::new();
        let replay = HandshakeReplayGuard::default();
        let mut handle_admission = MockHandleAdmission::default();
        let mut session = AuthorityServiceSession::new_with_handle_admission(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
            &mut handle_admission,
        );
        session
            .handle(decode_request_payload(&handshake([0x81; 32], [0x83; 32])).unwrap())
            .unwrap();

        for payload in [
            request("status", ""),
            request("selfTest", ""),
            request("runModelPartComposition", r#","requestId":"route-1""#),
            request("cancel", r#","requestId":"route-1""#),
            request("getResult", r#","requestId":"route-1""#),
        ] {
            assert_eq!(
                session
                    .handle(decode_request_payload(&payload).unwrap())
                    .unwrap()["ok"],
                true
            );
        }
        let commands = runtime.commands.lock().unwrap();
        assert_eq!(commands.len(), 5);
        drop(commands);
        let result = session
            .handle(
                decode_request_payload(&request("getResult", r#","requestId":"route-2""#)).unwrap(),
            )
            .unwrap();
        assert_eq!(result["result"]["state"], "pending");
        drop(session);
        assert_eq!(handle_admission.committed, ["route-1"]);
    }

    #[test]
    fn stop_before_dispatch_and_stop_after_dispatch_both_close_without_reply() {
        for stop_after_call in [2usize, 3usize] {
            let runtime = MockRuntime::new();
            let mut verifier = SequenceBindingVerifier::new(vec![binding(0x91, 0x92)]);
            let mut peer_verifier = SequencePeerVerifier::stable();
            let mut signer = MockSigner::new();
            let replay = HandshakeReplayGuard::default();
            let mut session = AuthorityServiceSession::new(
                &runtime,
                &mut verifier,
                &mut peer_verifier,
                &mut signer,
                &replay,
            );
            session
                .handle(decode_request_payload(&handshake([0x91; 32], [0x93; 32])).unwrap())
                .unwrap();
            let mut input = Cursor::new(frame(&request("status", "")));
            let mut output = Vec::new();
            let calls = Cell::new(0usize);
            let exit =
                run_authority_service_protocol(&mut input, &mut output, &mut session, || {
                    calls.set(calls.get() + 1);
                    calls.get() >= stop_after_call
                })
                .unwrap();
            assert_eq!(exit, ProtocolExit::StopRequested);
            assert!(output.is_empty());
            assert_eq!(
                runtime.commands.lock().unwrap().len(),
                usize::from(stop_after_call == 3)
            );
        }
    }

    #[test]
    fn duplex_protocol_tolerates_partial_header_body_and_response_writes() {
        let runtime = MockRuntime::new();
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0xb1, 0xb2)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = MockSigner::new();
        let replay = HandshakeReplayGuard::default();
        let mut session = AuthorityServiceSession::new(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
        );
        let input = [
            frame(&handshake([0xb1; 32], [0xb3; 32])),
            frame(&request("status", "")),
        ]
        .concat();
        let mut transport = ChunkedDuplex {
            input: Cursor::new(input),
            output: Vec::new(),
            read_chunk: 1,
            write_chunk: 2,
        };
        assert_eq!(
            run_authority_service_duplex_protocol(&mut transport, &mut session, || false).unwrap(),
            ProtocolExit::PeerClosed
        );
        let responses = response_frames(&transport.output);
        assert_eq!(responses.len(), 2);
        assert_eq!(
            responses[0]["result"]["schema"],
            GENERATION_ATTESTATION_SCHEMA
        );
        assert_eq!(responses[1]["result"]["trustedBoundaryReady"], true);
    }

    #[test]
    fn installed_single_command_protocol_never_consumes_a_third_frame() {
        let runtime = MockRuntime::new();
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0xb4, 0xb5)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = MockSigner::new();
        let replay = HandshakeReplayGuard::default();
        let mut admission = MockInstalledControllerAdmission::default();
        let mut session = AuthorityServiceSession::new_with_installed_controller_admission(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
            &mut admission,
        );
        let handshake_frame = frame(&handshake([0xb4; 32], [0xb6; 32]));
        let command_frame = frame(&request("status", ""));
        let trailing_frame = frame(&request("selfTest", ""));
        let expected_consumed = handshake_frame.len() + command_frame.len();
        let input = [handshake_frame, command_frame, trailing_frame].concat();
        let mut transport = ChunkedDuplex {
            input: Cursor::new(input),
            output: Vec::new(),
            read_chunk: 1,
            write_chunk: 2,
        };

        assert_eq!(
            run_authority_service_single_command_duplex_protocol(
                &mut transport,
                &mut session,
                || false,
            )
            .unwrap(),
            ProtocolExit::PeerClosed
        );
        assert_eq!(transport.input.position(), expected_consumed as u64);
        let responses = response_frames(&transport.output);
        assert_eq!(responses.len(), 2);
        assert_eq!(
            responses[0]["result"]["schema"],
            GENERATION_ATTESTATION_SCHEMA
        );
        assert_eq!(responses[1]["result"]["trustedBoundaryReady"], true);
        assert!(!session.is_poisoned());
        drop(session);
        assert_eq!(admission.commands, [("status".to_owned(), None)]);
        assert_eq!(runtime.commands.lock().unwrap().len(), 1);
    }

    #[test]
    fn installed_single_command_protocol_closes_safely_on_eof_after_handshake() {
        let runtime = MockRuntime::new();
        let mut verifier = SequenceBindingVerifier::new(vec![binding(0xb7, 0xb8)]);
        let mut peer_verifier = SequencePeerVerifier::stable();
        let mut signer = MockSigner::new();
        let replay = HandshakeReplayGuard::default();
        let mut admission = MockInstalledControllerAdmission::default();
        let mut session = AuthorityServiceSession::new_with_installed_controller_admission(
            &runtime,
            &mut verifier,
            &mut peer_verifier,
            &mut signer,
            &replay,
            &mut admission,
        );
        let mut transport = ChunkedDuplex {
            input: Cursor::new(frame(&handshake([0xb7; 32], [0xb9; 32]))),
            output: Vec::new(),
            read_chunk: 1,
            write_chunk: 2,
        };

        assert_eq!(
            run_authority_service_single_command_duplex_protocol(
                &mut transport,
                &mut session,
                || false,
            )
            .unwrap(),
            ProtocolExit::PeerClosed
        );
        let responses = response_frames(&transport.output);
        assert_eq!(responses.len(), 1);
        assert_eq!(
            responses[0]["result"]["schema"],
            GENERATION_ATTESTATION_SCHEMA
        );
        assert!(!session.is_poisoned());
        assert!(runtime.commands.lock().unwrap().is_empty());
        drop(session);
        assert!(admission.commands.is_empty());
    }
}
