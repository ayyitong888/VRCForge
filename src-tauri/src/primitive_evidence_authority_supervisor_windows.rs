use super::*;
use hmac::{Hmac, Mac};
use serde_json::Value;

#[cfg(test)]
use crate::primitive_evidence_authority_pipe::{
    ScenarioStartExecutableRole, VerifiedScenarioExecutableLaunch, VerifiedScenarioStartContract,
};

#[path = "primitive_evidence_authority_supervisor_windows/native_job.rs"]
mod native_job;

#[path = "primitive_evidence_authority_supervisor_windows/authority_process.rs"]
pub(crate) mod authority_process;

pub(crate) fn verified_job_security_binding(
    identity: &crate::primitive_evidence_authority_runtime::AuthorityRuntimeIdentity,
) -> Result<policy_source::VerifiedJobSecurityBinding, SupervisorError> {
    native_job::verified_policy_binding(identity)
}

#[path = "primitive_evidence_authority_supervisor_windows/background.rs"]
pub(crate) mod background;

#[path = "primitive_evidence_authority_supervisor_windows/child_environment.rs"]
mod child_environment;

#[path = "primitive_evidence_authority_supervisor_windows/child_transport.rs"]
mod child_transport;

#[path = "primitive_evidence_authority_supervisor_windows/child_handshake.rs"]
mod child_handshake;

#[path = "primitive_evidence_authority_supervisor_windows/child_launcher.rs"]
mod child_launcher;

#[path = "primitive_evidence_authority_supervisor_windows/stage_journal.rs"]
pub(crate) mod stage_journal;

#[path = "primitive_evidence_authority_supervisor_windows/staged_start.rs"]
mod staged_start;

pub(crate) use staged_start::{
    NativeCompletedStageJournalBinding, NativeStartingAdvance, NativeStartingRun,
    NativeStartingTerminationAcknowledgement, ServiceOwnedStagedNativeApi,
};

pub(crate) const BACKEND_LISTENER_ADOPTION_BLOCKER: &str =
    "authority_backend_listener_adoption_not_supported";
pub(crate) const BRIDGE_TARGET_LISTENER_ADOPTION_BLOCKER: &str =
    "authority_bridge_target_listener_adoption_not_supported";
pub(crate) const BRIDGE_TARGET_REQUEST_AUTH_BLOCKER: &str =
    "authority_bridge_target_request_auth_not_connected";
pub(crate) const BRIDGE_TARGET_IN_MEMORY_STARTUP_BLOCKER: &str =
    "authority_bridge_target_in_memory_startup_not_connected";

const PRIVATE_PIPE_BINDING_DOMAIN: &[u8] = b"vrcforge-authority-private-pipe-v1\0";
const BRIDGE_CONTROL_PIPE_BINDING_DOMAIN: &[u8] = b"vrcforge-authority-bridge-control-pipe-v1\0";
const LISTENER_ACK_DOMAIN: &[u8] = b"vrcforge-authority-listener-ack-v1\0";
const BRIDGE_TARGET_ACK_DOMAIN: &[u8] = b"vrcforge-authority-bridge-target-ack-v1\0";
const HTTP_LIFECYCLE_DOMAIN: &[u8] = b"vrcforge-authority-http-lifecycle-v1\0";
const ADMISSION_BINDING_DOMAIN: &[u8] = b"vrcforge-authority-native-admission-v1\0";
const BACKEND_ADOPTION_FRAME_DOMAIN: &[u8] = b"vrcforge-authority-backend-adoption-frame-v1\0";
const BACKEND_ADOPTION_ACK_DOMAIN: &[u8] = b"vrcforge-authority-backend-adoption-ack-v1\0";
const BRIDGE_TARGET_ADOPTION_FRAME_DOMAIN: &[u8] = b"vrcforge-authority-bridge-target-frame-v1\0";
const BRIDGE_TARGET_ADOPTION_ACK_DOMAIN: &[u8] = b"vrcforge-authority-bridge-target-ack-frame-v1\0";
const BRIDGE_TARGET_REQUEST_AUTH_KEY_DOMAIN: &[u8] =
    b"vrcforge-authority-bridge-target-request-auth-key-v1\0";
const BRIDGE_TARGET_REQUEST_AUTH_KEY_DIGEST_DOMAIN: &[u8] =
    b"vrcforge-authority-bridge-target-request-auth-key-digest-v1\0";
const BRIDGE_TARGET_REQUEST_ACCOUNTING_DOMAIN: &[u8] =
    b"vrcforge-authority-bridge-target-request-accounting-v1\0";
const ARMED_STAGE_TERMINAL_DOMAIN: &[u8] = b"vrcforge-native-armed-stage-terminal-v1\0";
const TERMINATION_INTENT_RECEIPT_DOMAIN: &[u8] =
    b"vrcforge-authority-native-termination-intent-v1\0";
const MAX_ORIGIN_ENVELOPE_BYTES: usize = 128 * 1024;
const ORIGIN_ENVELOPE_SCHEMA_V2: &str = "vrcforge.primitive_basis_live_origin.v2";
const BACKEND_ADOPTION_FRAME_MAGIC: &[u8; 8] = b"VRCBSH01";
const BACKEND_ADOPTION_ACK_MAGIC: &[u8; 8] = b"VRCBAK01";
const BRIDGE_TARGET_ADOPTION_FRAME_MAGIC: &[u8; 8] = b"VRCBTF01";
const BRIDGE_TARGET_ADOPTION_ACK_MAGIC: &[u8; 8] = b"VRCBTA01";
const BACKEND_ADOPTION_PROTOCOL_VERSION: u16 = 1;
const BRIDGE_TARGET_ADOPTION_PROTOCOL_VERSION: u16 = 1;
const INNER_LIVE_BOOTSTRAP_VERSION: u16 = 4;
const INNER_LIVE_BOOTSTRAP_MAGIC: &[u8; 16] = b"VRCFPRIMLIVE4\0\0\0";
const INNER_LIVE_BOOTSTRAP_BYTES: usize = 400;
const MAX_BACKEND_ADOPTION_PAYLOAD_BYTES: usize = 256 * 1024;
const MAX_SOCKET_SHARE_BYTES: usize = 8 * 1024;
const BACKEND_ADOPTION_FRAME_FIXED_PAYLOAD_BYTES: usize = 191;
const BACKEND_ADOPTION_ACK_PAYLOAD_BYTES: usize = 261;
const BRIDGE_TARGET_ADOPTION_FRAME_FIXED_PAYLOAD_BYTES: usize = 421;
const BRIDGE_TARGET_ADOPTION_ACK_PAYLOAD_BYTES: usize = 511;
const MAX_BRIDGE_TARGET_ADOPTION_PAYLOAD_BYTES: usize = 64 * 1024;
const MAX_BRIDGE_TARGET_SOCKET_SHARE_BYTES: usize = 8 * 1024;
const MAX_BRIDGE_TARGET_STARTUP_MATERIAL_BYTES: usize = 4 * 1024;
const BACKEND_ADOPTION_ROLE_APP: u8 = 1;
const BRIDGE_TARGET_ADOPTION_ROLE: u8 = 1;
const ADDRESS_FAMILY_IPV4: u16 = 2;
const SOCKET_TYPE_STREAM: u16 = 1;
const PROTOCOL_TCP: u16 = 6;
const LOOPBACK_IPV4_NETWORK_ORDER: u32 = 0x7f00_0001;
const ACK_FLAG_SOCKET_FROM_SHARE: u16 = 1 << 0;
const ACK_FLAG_GETSOCKNAME_VERIFIED: u16 = 1 << 1;
const ACK_FLAG_TYPE_PROTOCOL_VERIFIED: u16 = 1 << 2;
const ACK_FLAG_OPTIONS_VERIFIED: u16 = 1 << 3;
const ACK_FLAG_BOOTSTRAP_PARSED: u16 = 1 << 4;
const ACK_FLAG_ORDINARY_BIND_DISABLED: u16 = 1 << 5;
const ACK_FLAG_FRAME_COMPLETE: u16 = 1 << 6;
const BACKEND_ADOPTION_ACK_REQUIRED_FLAGS: u16 = ACK_FLAG_SOCKET_FROM_SHARE
    | ACK_FLAG_GETSOCKNAME_VERIFIED
    | ACK_FLAG_TYPE_PROTOCOL_VERIFIED
    | ACK_FLAG_OPTIONS_VERIFIED
    | ACK_FLAG_BOOTSTRAP_PARSED
    | ACK_FLAG_ORDINARY_BIND_DISABLED
    | ACK_FLAG_FRAME_COMPLETE;
const BRIDGE_TARGET_ACK_FLAG_SOCKET_FROM_SHARE: u16 = 1 << 0;
const BRIDGE_TARGET_ACK_FLAG_GETSOCKNAME_VERIFIED: u16 = 1 << 1;
const BRIDGE_TARGET_ACK_FLAG_TYPE_PROTOCOL_VERIFIED: u16 = 1 << 2;
const BRIDGE_TARGET_ACK_FLAG_OPTIONS_VERIFIED: u16 = 1 << 3;
const BRIDGE_TARGET_ACK_FLAG_FACTORY_CREATED: u16 = 1 << 4;
const BRIDGE_TARGET_ACK_FLAG_HTTP_APP_MOUNTED: u16 = 1 << 5;
const BRIDGE_TARGET_ACK_FLAG_HEALTH_READY: u16 = 1 << 6;
const BRIDGE_TARGET_ACK_FLAG_ORDINARY_BIND_DISABLED: u16 = 1 << 7;
const BRIDGE_TARGET_ACK_FLAG_FRAME_COMPLETE: u16 = 1 << 8;
const BRIDGE_TARGET_ACK_FLAG_STARTUP_CONFIGURATION_APPLIED: u16 = 1 << 9;
const BRIDGE_TARGET_ACK_FLAG_REQUEST_AUTH_ENABLED: u16 = 1 << 10;
const BRIDGE_TARGET_ADOPTION_ACK_REQUIRED_FLAGS: u16 = BRIDGE_TARGET_ACK_FLAG_SOCKET_FROM_SHARE
    | BRIDGE_TARGET_ACK_FLAG_GETSOCKNAME_VERIFIED
    | BRIDGE_TARGET_ACK_FLAG_TYPE_PROTOCOL_VERIFIED
    | BRIDGE_TARGET_ACK_FLAG_OPTIONS_VERIFIED
    | BRIDGE_TARGET_ACK_FLAG_FACTORY_CREATED
    | BRIDGE_TARGET_ACK_FLAG_HTTP_APP_MOUNTED
    | BRIDGE_TARGET_ACK_FLAG_HEALTH_READY
    | BRIDGE_TARGET_ACK_FLAG_ORDINARY_BIND_DISABLED
    | BRIDGE_TARGET_ACK_FLAG_FRAME_COMPLETE
    | BRIDGE_TARGET_ACK_FLAG_STARTUP_CONFIGURATION_APPLIED
    | BRIDGE_TARGET_ACK_FLAG_REQUEST_AUTH_ENABLED;

pub(crate) struct BackendAdoptionFrame {
    run_binding_digest: Digest,
    private_pipe_binding_digest: Digest,
    listener_socket_object_id: u64,
    socket_share_bytes: Vec<u8>,
    inner_live_bootstrap_bytes: Vec<u8>,
    challenge: Digest,
}

impl BackendAdoptionFrame {
    pub(crate) fn new(
        run_binding_digest: Digest,
        private_pipe_binding_digest: Digest,
        listener_socket_object_id: u64,
        socket_share_bytes: Vec<u8>,
        inner_live_bootstrap_bytes: Vec<u8>,
        challenge: Digest,
    ) -> Result<Self, SupervisorError> {
        if is_zero_digest(&run_binding_digest)
            || is_zero_digest(&private_pipe_binding_digest)
            || listener_socket_object_id == 0
            || socket_share_bytes.is_empty()
            || socket_share_bytes.len() > MAX_SOCKET_SHARE_BYTES
            || inner_live_bootstrap_bytes.len() != INNER_LIVE_BOOTSTRAP_BYTES
            || inner_live_bootstrap_bytes.get(..INNER_LIVE_BOOTSTRAP_MAGIC.len())
                != Some(INNER_LIVE_BOOTSTRAP_MAGIC)
            || is_zero_digest(&challenge)
        {
            return Err(SupervisorError::new(
                "authority_backend_adoption_frame_invalid",
            ));
        }
        let payload_len = BACKEND_ADOPTION_FRAME_FIXED_PAYLOAD_BYTES
            .checked_add(socket_share_bytes.len())
            .and_then(|value| value.checked_add(inner_live_bootstrap_bytes.len()))
            .ok_or_else(|| SupervisorError::new("authority_backend_adoption_frame_too_large"))?;
        if payload_len > MAX_BACKEND_ADOPTION_PAYLOAD_BYTES {
            return Err(SupervisorError::new(
                "authority_backend_adoption_frame_too_large",
            ));
        }
        Ok(Self {
            run_binding_digest,
            private_pipe_binding_digest,
            listener_socket_object_id,
            socket_share_bytes,
            inner_live_bootstrap_bytes,
            challenge,
        })
    }

    pub(crate) fn encode_for_private_pipe(&self) -> SensitiveFrameBytes {
        let socket_share_len = self.socket_share_bytes.len();
        let bootstrap_len = self.inner_live_bootstrap_bytes.len();
        let payload_len =
            BACKEND_ADOPTION_FRAME_FIXED_PAYLOAD_BYTES + socket_share_len + bootstrap_len;
        let mut bytes = Vec::with_capacity(14 + payload_len);
        bytes.extend_from_slice(BACKEND_ADOPTION_FRAME_MAGIC);
        bytes.extend_from_slice(&BACKEND_ADOPTION_PROTOCOL_VERSION.to_be_bytes());
        bytes.extend_from_slice(&(payload_len as u32).to_be_bytes());
        bytes.extend_from_slice(&self.run_binding_digest);
        bytes.extend_from_slice(&self.private_pipe_binding_digest);
        bytes.push(BACKEND_ADOPTION_ROLE_APP);
        bytes.extend_from_slice(&ADDRESS_FAMILY_IPV4.to_be_bytes());
        bytes.extend_from_slice(&SOCKET_TYPE_STREAM.to_be_bytes());
        bytes.extend_from_slice(&PROTOCOL_TCP.to_be_bytes());
        bytes.extend_from_slice(&LOOPBACK_IPV4_NETWORK_ORDER.to_be_bytes());
        bytes.extend_from_slice(&APP_LOOPBACK_PORT.to_be_bytes());
        bytes.extend_from_slice(&self.listener_socket_object_id.to_be_bytes());
        bytes.extend_from_slice(&(socket_share_len as u32).to_be_bytes());
        bytes.extend_from_slice(&self.socket_share_bytes);
        bytes.extend_from_slice(&INNER_LIVE_BOOTSTRAP_VERSION.to_be_bytes());
        bytes.extend_from_slice(&(bootstrap_len as u32).to_be_bytes());
        bytes.extend_from_slice(&self.inner_live_bootstrap_bytes);
        let bootstrap_digest: Digest = Sha256::digest(&self.inner_live_bootstrap_bytes).into();
        bytes.extend_from_slice(&bootstrap_digest);
        bytes.extend_from_slice(&self.challenge);
        let mut hasher = Sha256::new();
        hasher.update(BACKEND_ADOPTION_FRAME_DOMAIN);
        hasher.update(&bytes);
        let frame_digest: Digest = hasher.finalize().into();
        bytes.extend_from_slice(&frame_digest);
        SensitiveFrameBytes(bytes)
    }
}

impl Drop for BackendAdoptionFrame {
    fn drop(&mut self) {
        self.socket_share_bytes.fill(0);
        self.inner_live_bootstrap_bytes.fill(0);
        self.challenge.fill(0);
    }
}

pub(crate) struct SensitiveFrameBytes(Vec<u8>);

impl SensitiveFrameBytes {
    pub(crate) fn as_bytes(&self) -> &[u8] {
        &self.0
    }
}

impl Drop for SensitiveFrameBytes {
    fn drop(&mut self) {
        self.0.fill(0);
    }
}

pub(crate) struct BackendAdoptionAck {
    run_binding_digest: Digest,
    private_pipe_binding_digest: Digest,
    challenge: Digest,
    listener_socket_object_id: u64,
    inner_live_bootstrap_version: u16,
    inner_live_bootstrap_digest: Digest,
    backend: ProcessKey,
    owner_executable_digest: Digest,
    owner_image_identity_digest: Digest,
    flags: u16,
}

impl BackendAdoptionAck {
    pub(crate) fn decode(bytes: &[u8]) -> Result<Self, SupervisorError> {
        const TOTAL_BYTES: usize = 14 + BACKEND_ADOPTION_ACK_PAYLOAD_BYTES;
        if bytes.len() != TOTAL_BYTES
            || &bytes[..8] != BACKEND_ADOPTION_ACK_MAGIC
            || u16::from_be_bytes([bytes[8], bytes[9]]) != BACKEND_ADOPTION_PROTOCOL_VERSION
            || u32::from_be_bytes([bytes[10], bytes[11], bytes[12], bytes[13]]) as usize
                != BACKEND_ADOPTION_ACK_PAYLOAD_BYTES
        {
            return Err(SupervisorError::new(
                "authority_backend_adoption_ack_invalid",
            ));
        }
        let digest_offset = TOTAL_BYTES - 32;
        let mut hasher = Sha256::new();
        hasher.update(BACKEND_ADOPTION_ACK_DOMAIN);
        hasher.update(&bytes[..digest_offset]);
        let expected_digest: Digest = hasher.finalize().into();
        if bytes[digest_offset..] != expected_digest {
            return Err(SupervisorError::new(
                "authority_backend_adoption_ack_digest_invalid",
            ));
        }
        let mut offset = 14usize;
        let run_binding_digest = frame_take_digest(bytes, &mut offset)?;
        let private_pipe_binding_digest = frame_take_digest(bytes, &mut offset)?;
        let challenge = frame_take_digest(bytes, &mut offset)?;
        let role = frame_take_u8(bytes, &mut offset)?;
        let address_family = frame_take_u16(bytes, &mut offset)?;
        let socket_type = frame_take_u16(bytes, &mut offset)?;
        let protocol = frame_take_u16(bytes, &mut offset)?;
        let address = frame_take_u32(bytes, &mut offset)?;
        let port = frame_take_u16(bytes, &mut offset)?;
        let listener_socket_object_id = frame_take_u64(bytes, &mut offset)?;
        let inner_live_bootstrap_version = frame_take_u16(bytes, &mut offset)?;
        let inner_live_bootstrap_digest = frame_take_digest(bytes, &mut offset)?;
        let pid = frame_take_u32(bytes, &mut offset)?;
        let creation_time = frame_take_u64(bytes, &mut offset)?;
        let owner_executable_digest = frame_take_digest(bytes, &mut offset)?;
        let owner_image_identity_digest = frame_take_digest(bytes, &mut offset)?;
        let flags = frame_take_u16(bytes, &mut offset)?;
        if offset != digest_offset
            || role != BACKEND_ADOPTION_ROLE_APP
            || address_family != ADDRESS_FAMILY_IPV4
            || socket_type != SOCKET_TYPE_STREAM
            || protocol != PROTOCOL_TCP
            || address != LOOPBACK_IPV4_NETWORK_ORDER
            || port != APP_LOOPBACK_PORT
            || listener_socket_object_id == 0
            || inner_live_bootstrap_version != INNER_LIVE_BOOTSTRAP_VERSION
            || is_zero_digest(&inner_live_bootstrap_digest)
            || pid == 0
            || creation_time == 0
            || is_zero_digest(&run_binding_digest)
            || is_zero_digest(&private_pipe_binding_digest)
            || is_zero_digest(&challenge)
            || is_zero_digest(&owner_executable_digest)
            || is_zero_digest(&owner_image_identity_digest)
            || flags != BACKEND_ADOPTION_ACK_REQUIRED_FLAGS
        {
            return Err(SupervisorError::new(
                "authority_backend_adoption_ack_invalid",
            ));
        }
        Ok(Self {
            run_binding_digest,
            private_pipe_binding_digest,
            challenge,
            listener_socket_object_id,
            inner_live_bootstrap_version,
            inner_live_bootstrap_digest,
            backend: ProcessKey { pid, creation_time },
            owner_executable_digest,
            owner_image_identity_digest,
            flags,
        })
    }

    fn verifies_for(
        &self,
        policy: &SupervisorPolicy,
        pipe: &PrivateBackendPipeLease,
        listener: &ServiceListenerLease,
        backend: &ProcessObservation,
    ) -> bool {
        self.run_binding_digest == policy.run_binding_digest
            && self.private_pipe_binding_digest == pipe.binding_digest
            && self.challenge == pipe.challenge_digest
            && self.listener_socket_object_id == listener.listener_socket_object_id
            && self.inner_live_bootstrap_version == INNER_LIVE_BOOTSTRAP_VERSION
            && self.inner_live_bootstrap_digest == pipe.inner_live_bootstrap_digest
            && self.backend == backend.key
            && self.owner_executable_digest == backend.executable_digest
            && self.owner_image_identity_digest
                == file_identity_digest(&backend.image_handle_identity)
            && self.flags == BACKEND_ADOPTION_ACK_REQUIRED_FLAGS
    }
}

pub(crate) struct BridgeTargetAdoptionFrame {
    run_binding_digest: Digest,
    ticket_digest: Digest,
    bridge_launch_binding_digest: Digest,
    private_pipe_binding_digest: Digest,
    private_pipe_instance_id: u64,
    challenge: Digest,
    adapter_executable_digest: Digest,
    bridge_target_manifest_digest: Digest,
    bridge_target_tree_digest: Digest,
    target_port: u16,
    listener_socket_object_id: u64,
    socket_share_bytes: Vec<u8>,
    startup_material: Vec<u8>,
    socket_share_digest: Digest,
    startup_material_digest: Digest,
    request_auth_key_digest: Digest,
}

impl BridgeTargetAdoptionFrame {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        policy: &SupervisorPolicy,
        run_binding_digest: Digest,
        ticket_digest: Digest,
        bridge_launch_binding_digest: Digest,
        control_pipe: &BridgeTargetControlPipeLease,
        adapter_executable_digest: Digest,
        target_port: u16,
        listener_socket_object_id: u64,
        socket_share_bytes: Vec<u8>,
        startup_material: Vec<u8>,
    ) -> Result<Self, SupervisorError> {
        let private_pipe_binding_digest = control_pipe.binding_digest;
        let private_pipe_instance_id = control_pipe.instance_id;
        let challenge = control_pipe.challenge_digest;
        let expected_adapter_executable_digest =
            policy.process_executable_digests[role_index(ProcessRole::BridgeListener)];
        let bridge_target_manifest_digest = policy.bridge_target_manifest_digest;
        let bridge_target_tree_digest = policy.bridge_target_tree_digest;
        if is_zero_digest(&run_binding_digest)
            || is_zero_digest(&ticket_digest)
            || is_zero_digest(&bridge_launch_binding_digest)
            || is_zero_digest(&private_pipe_binding_digest)
            || private_pipe_instance_id == 0
            || is_zero_digest(&challenge)
            || !control_pipe.created_new
            || !control_pipe.one_connection
            || !control_pipe.service_owned
            || !control_pipe.restricted_acl
            || !control_pipe.service_handle_held_through_shutdown
            || !control_pipe.restricted_service_handle_in_launch_allowlist
            || control_pipe.material_exposed_to_argv
            || control_pipe.material_exposed_to_environment
            || control_pipe.material_exposed_to_report
            || control_pipe.material_exposed_to_log
            || is_zero_digest(&adapter_executable_digest)
            || adapter_executable_digest != expected_adapter_executable_digest
            || is_zero_digest(&bridge_target_manifest_digest)
            || is_zero_digest(&bridge_target_tree_digest)
            || target_port < 1024
            || target_port == BRIDGE_LOOPBACK_PORT
            || target_port == APP_LOOPBACK_PORT
            || listener_socket_object_id == 0
            || socket_share_bytes.is_empty()
            || socket_share_bytes.len() > MAX_BRIDGE_TARGET_SOCKET_SHARE_BYTES
            || startup_material.is_empty()
            || startup_material.len() > MAX_BRIDGE_TARGET_STARTUP_MATERIAL_BYTES
        {
            return Err(SupervisorError::new(
                "authority_bridge_target_adoption_frame_invalid",
            ));
        }
        let payload_len = BRIDGE_TARGET_ADOPTION_FRAME_FIXED_PAYLOAD_BYTES
            .checked_add(socket_share_bytes.len())
            .and_then(|value| value.checked_add(startup_material.len()))
            .ok_or_else(|| {
                SupervisorError::new("authority_bridge_target_adoption_frame_too_large")
            })?;
        if payload_len > MAX_BRIDGE_TARGET_ADOPTION_PAYLOAD_BYTES {
            return Err(SupervisorError::new(
                "authority_bridge_target_adoption_frame_too_large",
            ));
        }
        let socket_share_digest = Sha256::digest(&socket_share_bytes).into();
        let startup_material_digest = Sha256::digest(&startup_material).into();
        let request_auth_key_digest = derive_bridge_target_request_auth_key_digest(
            &startup_material,
            &run_binding_digest,
            &ticket_digest,
            &bridge_launch_binding_digest,
            &private_pipe_binding_digest,
            &challenge,
            &adapter_executable_digest,
            &bridge_target_manifest_digest,
            &bridge_target_tree_digest,
            private_pipe_instance_id,
            target_port,
            listener_socket_object_id,
            &socket_share_digest,
            &startup_material_digest,
        );
        Ok(Self {
            run_binding_digest,
            ticket_digest,
            bridge_launch_binding_digest,
            private_pipe_binding_digest,
            private_pipe_instance_id,
            challenge,
            adapter_executable_digest,
            bridge_target_manifest_digest,
            bridge_target_tree_digest,
            target_port,
            listener_socket_object_id,
            socket_share_bytes,
            startup_material,
            socket_share_digest,
            startup_material_digest,
            request_auth_key_digest,
        })
    }

    pub(crate) fn encode_for_private_pipe(&self) -> SensitiveFrameBytes {
        let payload_len = BRIDGE_TARGET_ADOPTION_FRAME_FIXED_PAYLOAD_BYTES
            + self.socket_share_bytes.len()
            + self.startup_material.len();
        let mut bytes = Vec::with_capacity(14 + payload_len);
        bytes.extend_from_slice(BRIDGE_TARGET_ADOPTION_FRAME_MAGIC);
        bytes.extend_from_slice(&BRIDGE_TARGET_ADOPTION_PROTOCOL_VERSION.to_be_bytes());
        bytes.extend_from_slice(&(payload_len as u32).to_be_bytes());
        bytes.extend_from_slice(&self.run_binding_digest);
        bytes.extend_from_slice(&self.ticket_digest);
        bytes.extend_from_slice(&self.bridge_launch_binding_digest);
        bytes.extend_from_slice(&self.private_pipe_binding_digest);
        bytes.extend_from_slice(&self.challenge);
        bytes.extend_from_slice(&self.adapter_executable_digest);
        bytes.extend_from_slice(&self.bridge_target_manifest_digest);
        bytes.extend_from_slice(&self.bridge_target_tree_digest);
        bytes.extend_from_slice(&self.private_pipe_instance_id.to_be_bytes());
        bytes.push(BRIDGE_TARGET_ADOPTION_ROLE);
        bytes.extend_from_slice(&ADDRESS_FAMILY_IPV4.to_be_bytes());
        bytes.extend_from_slice(&SOCKET_TYPE_STREAM.to_be_bytes());
        bytes.extend_from_slice(&PROTOCOL_TCP.to_be_bytes());
        bytes.extend_from_slice(&LOOPBACK_IPV4_NETWORK_ORDER.to_be_bytes());
        bytes.extend_from_slice(&self.target_port.to_be_bytes());
        bytes.extend_from_slice(&self.listener_socket_object_id.to_be_bytes());
        bytes.extend_from_slice(&(self.socket_share_bytes.len() as u32).to_be_bytes());
        bytes.extend_from_slice(&self.socket_share_bytes);
        bytes.extend_from_slice(&(self.startup_material.len() as u32).to_be_bytes());
        bytes.extend_from_slice(&self.startup_material);
        bytes.extend_from_slice(&self.socket_share_digest);
        bytes.extend_from_slice(&self.startup_material_digest);
        bytes.extend_from_slice(&self.request_auth_key_digest);
        let mut hasher = Sha256::new();
        hasher.update(BRIDGE_TARGET_ADOPTION_FRAME_DOMAIN);
        hasher.update(&bytes);
        bytes.extend_from_slice(&<[u8; 32]>::from(hasher.finalize()));
        SensitiveFrameBytes(bytes)
    }
}

impl Drop for BridgeTargetAdoptionFrame {
    fn drop(&mut self) {
        self.socket_share_bytes.fill(0);
        self.startup_material.fill(0);
        self.challenge.fill(0);
    }
}

#[allow(clippy::too_many_arguments)]
fn derive_bridge_target_request_auth_key_digest(
    startup_material: &[u8],
    run_binding_digest: &Digest,
    ticket_digest: &Digest,
    bridge_launch_binding_digest: &Digest,
    private_pipe_binding_digest: &Digest,
    challenge: &Digest,
    adapter_executable_digest: &Digest,
    bridge_target_manifest_digest: &Digest,
    bridge_target_tree_digest: &Digest,
    private_pipe_instance_id: u64,
    target_port: u16,
    listener_socket_object_id: u64,
    socket_share_digest: &Digest,
    startup_material_digest: &Digest,
) -> Digest {
    let mut mac = Hmac::<Sha256>::new_from_slice(startup_material)
        .expect("HMAC-SHA256 accepts request auth startup material");
    mac.update(BRIDGE_TARGET_REQUEST_AUTH_KEY_DOMAIN);
    mac.update(run_binding_digest);
    mac.update(ticket_digest);
    mac.update(bridge_launch_binding_digest);
    mac.update(private_pipe_binding_digest);
    mac.update(challenge);
    mac.update(adapter_executable_digest);
    mac.update(bridge_target_manifest_digest);
    mac.update(bridge_target_tree_digest);
    mac.update(&private_pipe_instance_id.to_be_bytes());
    mac.update(&target_port.to_be_bytes());
    mac.update(&listener_socket_object_id.to_be_bytes());
    mac.update(socket_share_digest);
    mac.update(startup_material_digest);
    let mut request_auth_key: Digest = mac.finalize().into_bytes().into();
    let mut hasher = Sha256::new();
    hasher.update(BRIDGE_TARGET_REQUEST_AUTH_KEY_DIGEST_DOMAIN);
    hasher.update(request_auth_key);
    let key_digest = hasher.finalize().into();
    request_auth_key.fill(0);
    key_digest
}

pub(crate) struct BridgeTargetAdoptionAck {
    run_binding_digest: Digest,
    ticket_digest: Digest,
    bridge_launch_binding_digest: Digest,
    private_pipe_binding_digest: Digest,
    challenge: Digest,
    adapter_executable_digest: Digest,
    bridge_target_manifest_digest: Digest,
    bridge_target_tree_digest: Digest,
    private_pipe_instance_id: u64,
    target_port: u16,
    listener_socket_object_id: u64,
    socket_share_digest: Digest,
    startup_material_digest: Digest,
    request_auth_key_digest: Digest,
    controlled_health_request_count: u32,
    proxy_http_request_count_at_ack: u32,
    proxy_websocket_request_count_at_ack: u32,
    rejected_request_count_at_ack: u32,
    bypass_request_count_at_ack: u32,
    owner: ProcessKey,
    owner_executable_digest: Digest,
    owner_image_identity_digest: Digest,
    flags: u16,
}

impl BridgeTargetAdoptionAck {
    pub(crate) fn decode(bytes: &[u8]) -> Result<Self, SupervisorError> {
        const TOTAL_BYTES: usize = 14 + BRIDGE_TARGET_ADOPTION_ACK_PAYLOAD_BYTES;
        if bytes.len() != TOTAL_BYTES
            || &bytes[..8] != BRIDGE_TARGET_ADOPTION_ACK_MAGIC
            || u16::from_be_bytes([bytes[8], bytes[9]]) != BRIDGE_TARGET_ADOPTION_PROTOCOL_VERSION
            || u32::from_be_bytes([bytes[10], bytes[11], bytes[12], bytes[13]]) as usize
                != BRIDGE_TARGET_ADOPTION_ACK_PAYLOAD_BYTES
        {
            return Err(SupervisorError::new(
                "authority_bridge_target_adoption_ack_invalid",
            ));
        }
        let digest_offset = TOTAL_BYTES - 32;
        let mut hasher = Sha256::new();
        hasher.update(BRIDGE_TARGET_ADOPTION_ACK_DOMAIN);
        hasher.update(&bytes[..digest_offset]);
        let expected_digest: Digest = hasher.finalize().into();
        if bytes[digest_offset..] != expected_digest {
            return Err(SupervisorError::new(
                "authority_bridge_target_adoption_ack_digest_invalid",
            ));
        }
        let mut reader = BridgeTargetFrameReader::new(bytes, 14, digest_offset);
        let run_binding_digest = reader.digest()?;
        let ticket_digest = reader.digest()?;
        let bridge_launch_binding_digest = reader.digest()?;
        let private_pipe_binding_digest = reader.digest()?;
        let challenge = reader.digest()?;
        let adapter_executable_digest = reader.digest()?;
        let bridge_target_manifest_digest = reader.digest()?;
        let bridge_target_tree_digest = reader.digest()?;
        let private_pipe_instance_id = reader.u64()?;
        let role = reader.u8()?;
        let address_family = reader.u16()?;
        let socket_type = reader.u16()?;
        let protocol = reader.u16()?;
        let address = reader.u32()?;
        let target_port = reader.u16()?;
        let listener_socket_object_id = reader.u64()?;
        let socket_share_digest = reader.digest()?;
        let startup_material_digest = reader.digest()?;
        let request_auth_key_digest = reader.digest()?;
        let controlled_health_request_count = reader.u32()?;
        let proxy_http_request_count_at_ack = reader.u32()?;
        let proxy_websocket_request_count_at_ack = reader.u32()?;
        let rejected_request_count_at_ack = reader.u32()?;
        let bypass_request_count_at_ack = reader.u32()?;
        let pid = reader.u32()?;
        let creation_time = reader.u64()?;
        let owner_executable_digest = reader.digest()?;
        let owner_image_identity_digest = reader.digest()?;
        let flags = reader.u16()?;
        if reader.offset != digest_offset
            || role != BRIDGE_TARGET_ADOPTION_ROLE
            || address_family != ADDRESS_FAMILY_IPV4
            || socket_type != SOCKET_TYPE_STREAM
            || protocol != PROTOCOL_TCP
            || address != LOOPBACK_IPV4_NETWORK_ORDER
            || target_port < 1024
            || target_port == BRIDGE_LOOPBACK_PORT
            || target_port == APP_LOOPBACK_PORT
            || listener_socket_object_id == 0
            || private_pipe_instance_id == 0
            || pid == 0
            || creation_time == 0
            || flags != BRIDGE_TARGET_ADOPTION_ACK_REQUIRED_FLAGS
            || controlled_health_request_count != 1
            || proxy_http_request_count_at_ack != 0
            || proxy_websocket_request_count_at_ack != 0
            || rejected_request_count_at_ack != 0
            || bypass_request_count_at_ack != 0
        {
            return Err(SupervisorError::new(
                "authority_bridge_target_adoption_ack_invalid",
            ));
        }
        Ok(Self {
            run_binding_digest,
            ticket_digest,
            bridge_launch_binding_digest,
            private_pipe_binding_digest,
            challenge,
            adapter_executable_digest,
            bridge_target_manifest_digest,
            bridge_target_tree_digest,
            private_pipe_instance_id,
            target_port,
            listener_socket_object_id,
            socket_share_digest,
            startup_material_digest,
            request_auth_key_digest,
            controlled_health_request_count,
            proxy_http_request_count_at_ack,
            proxy_websocket_request_count_at_ack,
            rejected_request_count_at_ack,
            bypass_request_count_at_ack,
            owner: ProcessKey { pid, creation_time },
            owner_executable_digest,
            owner_image_identity_digest,
            flags,
        })
    }

    fn verifies_for(
        &self,
        frame: &BridgeTargetAdoptionFrame,
        owner: ProcessKey,
        owner_executable_digest: Digest,
        owner_image_identity_digest: Digest,
    ) -> bool {
        self.run_binding_digest == frame.run_binding_digest
            && self.ticket_digest == frame.ticket_digest
            && self.bridge_launch_binding_digest == frame.bridge_launch_binding_digest
            && self.private_pipe_binding_digest == frame.private_pipe_binding_digest
            && self.challenge == frame.challenge
            && self.adapter_executable_digest == frame.adapter_executable_digest
            && self.bridge_target_manifest_digest == frame.bridge_target_manifest_digest
            && self.bridge_target_tree_digest == frame.bridge_target_tree_digest
            && self.private_pipe_instance_id == frame.private_pipe_instance_id
            && self.target_port == frame.target_port
            && self.listener_socket_object_id == frame.listener_socket_object_id
            && self.socket_share_digest == frame.socket_share_digest
            && self.startup_material_digest == frame.startup_material_digest
            && self.request_auth_key_digest == frame.request_auth_key_digest
            && self.controlled_health_request_count == 1
            && self.proxy_http_request_count_at_ack == 0
            && self.proxy_websocket_request_count_at_ack == 0
            && self.rejected_request_count_at_ack == 0
            && self.bypass_request_count_at_ack == 0
            && self.owner == owner
            && self.owner_executable_digest == owner_executable_digest
            && self.owner_image_identity_digest == owner_image_identity_digest
            && self.flags == BRIDGE_TARGET_ADOPTION_ACK_REQUIRED_FLAGS
    }
}

struct BridgeTargetFrameReader<'a> {
    bytes: &'a [u8],
    offset: usize,
    limit: usize,
}

impl<'a> BridgeTargetFrameReader<'a> {
    fn new(bytes: &'a [u8], offset: usize, limit: usize) -> Self {
        Self {
            bytes,
            offset,
            limit,
        }
    }

    fn take<const N: usize>(&mut self) -> Result<[u8; N], SupervisorError> {
        let end = self
            .offset
            .checked_add(N)
            .ok_or_else(|| SupervisorError::new("authority_bridge_target_adoption_ack_invalid"))?;
        if end > self.limit {
            return Err(SupervisorError::new(
                "authority_bridge_target_adoption_ack_invalid",
            ));
        }
        let value = self.bytes[self.offset..end]
            .try_into()
            .map_err(|_| SupervisorError::new("authority_bridge_target_adoption_ack_invalid"))?;
        self.offset = end;
        Ok(value)
    }

    fn digest(&mut self) -> Result<Digest, SupervisorError> {
        let value = self.take::<32>()?;
        if is_zero_digest(&value) {
            return Err(SupervisorError::new(
                "authority_bridge_target_adoption_ack_invalid",
            ));
        }
        Ok(value)
    }

    fn u8(&mut self) -> Result<u8, SupervisorError> {
        Ok(self.take::<1>()?[0])
    }

    fn u16(&mut self) -> Result<u16, SupervisorError> {
        Ok(u16::from_be_bytes(self.take::<2>()?))
    }

    fn u32(&mut self) -> Result<u32, SupervisorError> {
        Ok(u32::from_be_bytes(self.take::<4>()?))
    }

    fn u64(&mut self) -> Result<u64, SupervisorError> {
        Ok(u64::from_be_bytes(self.take::<8>()?))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) enum NativeSupervisorPhase {
    Preflight,
    Prepare,
    LaunchSuspended,
    AssignJob,
    Resume,
    ObserveTerminal,
    Finalize,
    Contain,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeCapabilityReceipt {
    fresh_service_generation_attestation: bool,
    caller_attestation_envelope_present: bool,
    dedicated_restricted_runner: bool,
    service_owned_profile_acl: bool,
    stable_private_artifacts: bool,
    suspended_handle_list_launch: bool,
    kill_on_close_job: bool,
    completion_port_roster: bool,
    service_owned_listener_adoption: bool,
    service_owned_bridge_proxy: bool,
    bridge_target_in_memory_startup: bool,
    bridge_target_request_auth: bool,
    one_use_private_backend_pipe: bool,
    service_direct_http: bool,
    held_handle_finalization: bool,
    residue_readback: bool,
}

impl NativeCapabilityReceipt {
    #[cfg(test)]
    fn fully_connected() -> Self {
        Self {
            fresh_service_generation_attestation: true,
            caller_attestation_envelope_present: false,
            dedicated_restricted_runner: true,
            service_owned_profile_acl: true,
            stable_private_artifacts: true,
            suspended_handle_list_launch: true,
            kill_on_close_job: true,
            completion_port_roster: true,
            service_owned_listener_adoption: true,
            service_owned_bridge_proxy: true,
            bridge_target_in_memory_startup: true,
            bridge_target_request_auth: true,
            one_use_private_backend_pipe: true,
            service_direct_http: true,
            held_handle_finalization: true,
            residue_readback: true,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeAdmissionReceipt {
    prepared_receipt_digest: Digest,
    policy_snapshot_digest: Digest,
    recovery_bundle_digest: Digest,
    read_from_authority_store: bool,
    sealed_by_service: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeArmedAdmissionReceipt {
    prepared_receipt_digest: Digest,
    armed_receipt_digest: Digest,
    policy_snapshot_digest: Digest,
    recovery_bundle_digest: Digest,
    read_from_authority_store: bool,
    sealed_by_service: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeAdmissionBinding {
    prepared_receipt_digest: Digest,
    armed_receipt_digest: Digest,
    policy_snapshot_digest: Digest,
    recovery_bundle_digest: Digest,
    binding_digest: Digest,
}

impl NativeAdmissionBinding {
    pub(crate) fn prepared_receipt_digest(&self) -> &Digest {
        &self.prepared_receipt_digest
    }

    pub(crate) fn armed_receipt_digest(&self) -> &Digest {
        &self.armed_receipt_digest
    }

    pub(crate) fn policy_snapshot_digest(&self) -> &Digest {
        &self.policy_snapshot_digest
    }

    pub(crate) fn recovery_bundle_digest(&self) -> &Digest {
        &self.recovery_bundle_digest
    }

    pub(crate) fn binding_digest(&self) -> &Digest {
        &self.binding_digest
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct PrivateBackendPipeLease {
    instance_id: u64,
    binding_digest: Digest,
    challenge_digest: Digest,
    inner_live_bootstrap_digest: Digest,
    created_at: u64,
    created_new: bool,
    one_use: bool,
    service_owned: bool,
    restricted_acl: bool,
    service_handle_held: bool,
    material_exposed_to_argv: bool,
    material_exposed_to_environment: bool,
    material_exposed_to_report: bool,
    material_exposed_to_log: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct BridgeTargetControlPipeLease {
    instance_id: u64,
    binding_digest: Digest,
    challenge_digest: Digest,
    created_at: u64,
    created_new: bool,
    one_connection: bool,
    service_owned: bool,
    restricted_acl: bool,
    service_handle_held_through_shutdown: bool,
    restricted_service_handle_in_launch_allowlist: bool,
    material_exposed_to_argv: bool,
    material_exposed_to_environment: bool,
    material_exposed_to_report: bool,
    material_exposed_to_log: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct BridgeTargetControlPipeObservation {
    instance_id: u64,
    binding_digest: Digest,
    challenge_digest: Digest,
    peer: ProcessKey,
    peer_job_object_id: u64,
    peer_executable_digest: Digest,
    peer_image_identity: FileIdentity,
    accepted_at: u64,
    adoption_ack_at: u64,
    shutdown_requested_at: u64,
    accounting_read_at: u64,
    eof_observed_at: u64,
    accepted_connections: u32,
    peer_verified_from_pipe_and_process_handles: bool,
    ack_then_shutdown_then_accounting_then_eof: bool,
    replay_rejected: bool,
    reconnect_rejected: bool,
    service_handle_held_through_eof: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct PrivateBackendPipeAck {
    instance_id: u64,
    binding_digest: Digest,
    challenge_digest: Digest,
    peer: ProcessKey,
    peer_job_object_id: u64,
    peer_executable_digest: Digest,
    peer_image_identity: FileIdentity,
    accepted_at: u64,
    accepted_connections: u32,
    peer_verified_from_pipe_and_process_handles: bool,
    pid_table_only: bool,
    replay_rejected: bool,
    ack_read_from_service_pipe_handle: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ServiceListenerLease {
    role: SocketRole,
    local_port: u16,
    listener_socket_object_id: u64,
    created_at: u64,
    loopback_v4_only: bool,
    exclusive_address_use: bool,
    address_reuse_disabled: bool,
    service_created: bool,
    service_handle_held_until_adoption: bool,
    share_material_digest: Digest,
    share_material_exposed_to_argv: bool,
    share_material_exposed_to_environment: bool,
    share_material_exposed_to_report: bool,
    share_material_exposed_to_log: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct BridgeProxyLease {
    public_listener_socket_object_id: u64,
    public_port: u16,
    target_listener_socket_object_id: u64,
    target_port: u16,
    created_at: u64,
    service_owns_public_listener: bool,
    public_listener_never_transferred: bool,
    loopback_v4_only: bool,
    exclusive_address_use: bool,
    address_reuse_disabled: bool,
    service_handle_held_through_cleanup_begin: bool,
    request_auth_key_digest: Digest,
    request_auth_material_held_in_memory: bool,
    request_auth_exposed_to_argv: bool,
    request_auth_exposed_to_environment: bool,
    request_auth_exposed_to_report: bool,
    request_auth_exposed_to_log: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct BridgeProxyConnectionObservation {
    accepted_connection_object_id: u64,
    target_connection_object_id: u64,
    accepted_at: u64,
    closed_at: u64,
    byte_limit: u64,
    idle_timeout_ms: u64,
    http_request_count: u32,
    websocket_request_count: u32,
    semantic_request_parse_complete: bool,
    request_auth_injected: bool,
    response_or_websocket_close_complete: bool,
    both_handles_service_owned: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct BridgeProxyObservation {
    public_listener_socket_object_id: u64,
    public_port: u16,
    target_listener_socket_object_id: u64,
    target_port: u16,
    target_owner: ProcessKey,
    target_owner_job_object_id: u64,
    target_owner_executable_digest: Digest,
    target_owner_image_identity: FileIdentity,
    target_adopted_at: u64,
    target_adoption_binding_digest: Digest,
    target_socket_adopted_from_service_share: bool,
    target_adoption_ack_read_from_service_launch_pipe: bool,
    target_socket_object_identity_verified: bool,
    service_owns_public_listener: bool,
    target_identity_verified_from_socket_and_process_handles: bool,
    pid_table_only: bool,
    unity_bridge_launch_disabled: bool,
    unity_connected_to_service_proxy: bool,
    unexpected_bridge_launch_attempt: bool,
    release_then_bind_used: bool,
    target_ready_at: u64,
    public_proxy_enabled_at: u64,
    proxy_health_verified_at: u64,
    public_listener_hidden_until_target_ready: bool,
    health_verified_through_proxy: bool,
    explicit_http_and_websocket_semantic_proxy: bool,
    request_auth_key_digest: Digest,
    request_auth_injected_by_service: bool,
    controlled_health_request_count: u32,
    proxy_http_request_count: u32,
    proxy_websocket_request_count: u32,
    connections: Vec<BridgeProxyConnectionObservation>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct BridgeTargetRequestAccountingObservation {
    request_auth_key_digest: Digest,
    controlled_health_request_count: u32,
    proxy_http_request_count: u32,
    proxy_websocket_request_count: u32,
    total_target_request_count: u32,
    rejected_request_count: u32,
    bypass_request_count: u32,
    request_auth_header_stripped: bool,
    observed_at_shutdown: u64,
    read_from_adapter_shutdown_channel: bool,
    accounting_digest: Digest,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ListenerAdoptionAck {
    role: SocketRole,
    local_port: u16,
    listener_socket_object_id: u64,
    owner: ProcessKey,
    owner_job_object_id: u64,
    owner_executable_digest: Digest,
    owner_image_identity: FileIdentity,
    private_pipe_instance_id: u64,
    adopted_at: u64,
    ack_binding_digest: Digest,
    adopted_from_service_share: bool,
    ack_read_from_service_pipe_handle: bool,
    socket_object_identity_verified: bool,
    pid_table_only: bool,
    socket_adopted_from_share: bool,
    getsockname_verified: bool,
    type_and_protocol_verified: bool,
    socket_options_verified: bool,
    inner_live_bootstrap_version: u16,
    inner_live_bootstrap_digest: Digest,
    inner_live_bootstrap_parsed: bool,
    ordinary_bind_attempted: bool,
    pipe_closed_after_ack: bool,
    pipe_reconnect_rejected: bool,
    wire_ack_digest_verified: bool,
    loopback_v4_only: bool,
    exclusive_address_use: bool,
    address_reuse_disabled: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeJobReceipt {
    object_id: u64,
    deterministic_name_digest: Digest,
    security_binding_digest: Digest,
    exact_security_readback: bool,
    owner_local_system: bool,
    dacl_present: bool,
    dacl_defaulted: bool,
    dacl_protected: bool,
    dacl_ace_count: u16,
    system_access_mask: u32,
    service_access_mask: u32,
    created_at: u64,
    kill_on_job_close: bool,
    breakaway_allowed: bool,
    silent_breakaway_allowed: bool,
    active_process_limit: u32,
    completion_port_attached: bool,
    service_handle_held: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativePreparedFoundation {
    start_contract_digest: Digest,
    ticket_consumed_at: u64,
    runner: RunnerIdentityObservation,
    artifacts: Vec<StableArtifactObservation>,
    pipe: PrivateBackendPipeLease,
    bridge_control_pipe: BridgeTargetControlPipeLease,
    listeners: Vec<ServiceListenerLease>,
    bridge_proxy: BridgeProxyLease,
    job: NativeJobReceipt,
    admission: NativeAdmissionReceipt,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativePreparedEvidence {
    foundation: NativePreparedFoundation,
    bridge_root: AtomicRootLaunchReceipt,
}

impl std::ops::Deref for NativePreparedEvidence {
    type Target = NativePreparedFoundation;

    fn deref(&self) -> &Self::Target {
        &self.foundation
    }
}

impl std::ops::DerefMut for NativePreparedEvidence {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.foundation
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct SuspendedRootReceipt {
    role: ProcessRole,
    process: ProcessKey,
    parent: ProcessKey,
    executable_digest: Digest,
    image_identity: FileIdentity,
    runner_identity_digest: Digest,
    child_transport_contract_digest: Digest,
    raw_handle_list: RoleRawHandleListDigest,
    created_suspended_at: u64,
    job_list_attribute_applied: bool,
    job_assigned_at_creation: bool,
    job_membership_readback_before_return: bool,
    process_handle_held: bool,
    image_handle_held: bool,
    all_other_handles_non_inheritable: bool,
    breakaway_requested: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct JobAssignmentReceipt {
    process: ProcessKey,
    job_object_id: u64,
    membership_verified_at: u64,
    initial_assignment_call_performed: bool,
    job_membership_revalidated: bool,
    membership_readback_before_resume: bool,
    assigned_using_process_and_job_handles: bool,
    process_confirmed_job_member: bool,
    completion_port_assignment_observed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ResumedRootReceipt {
    start_contract_digest: Digest,
    process: ProcessKey,
    created_suspended_at: u64,
    job_membership_verified_at: u64,
    resumed_at: u64,
    job_object_id: u64,
    runner_identity_digest: Digest,
    child_transport_contract_digest: Digest,
    raw_handle_list: RoleRawHandleListDigest,
    all_other_handles_non_inheritable: bool,
    breakaway_requested: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AtomicRootLaunchReceipt {
    suspended: SuspendedRootReceipt,
    membership: JobAssignmentReceipt,
    resumed: ResumedRootReceipt,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct FixedHttpLifecycleReceipt {
    contract_digest: Digest,
    ticket_digest: Digest,
    run_binding_digest: Digest,
    private_pipe_instance_id: u64,
    listener_socket_object_id: u64,
    backend: ProcessKey,
    started_at: u64,
    finalized_at: u64,
    request_count: u32,
    service_direct: bool,
    caller_requests_present: bool,
    exact_sequence_observed: bool,
    responses_read_from_service_connection_handles: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeTerminalEvidence {
    bridge_root_launch: RootLaunchObservation,
    processes: Vec<ProcessObservation>,
    helpers: Vec<HelperProcessObservation>,
    job: JobObservation,
    pipe_ack: PrivateBackendPipeAck,
    bridge_control_pipe: BridgeTargetControlPipeObservation,
    listener_adoptions: Vec<ListenerAdoptionAck>,
    bridge_proxy: BridgeProxyObservation,
    http_lifecycle: FixedHttpLifecycleReceipt,
    finalization: Option<FinalizationObservation>,
    terminal: TerminalObservation,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeOriginEnvelopeReceipt {
    canonical_bytes: Vec<u8>,
    canonical_digest: Digest,
    origin_ticket_digest: Digest,
    authority_ticket_digest: Digest,
    result_digest: Digest,
    cleanup_receipt_digest: Digest,
    admission_binding_digest: Digest,
    cleanup_observed_at: u64,
    sealed_at: u64,
    built_from_service_held_evidence: bool,
    signed_by_service_after_cleanup: bool,
    caller_material_present: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeCleanupReceipt {
    private_pipe_instance_id: Option<u64>,
    private_pipe_closed: bool,
    pipe_challenge_zeroed: bool,
    no_pending_pipe_clients: bool,
    pipe_replay_rejected: bool,
    bridge_control_pipe_instance_id: Option<u64>,
    bridge_control_pipe_closed: bool,
    bridge_control_pipe_challenge_zeroed: bool,
    bridge_control_pipe_no_pending_clients: bool,
    bridge_control_pipe_replay_rejected: bool,
    closed_service_listener_ids: Vec<u64>,
    all_candidate_listener_duplicates_closed: bool,
    all_service_listener_handles_closed: bool,
    completion_port_drained: bool,
    no_inheritable_handle_residue: bool,
    no_port_drift: bool,
    bridge_proxy_listener_closed: bool,
    bridge_proxy_connections_closed: bool,
    bridge_target_listener_closed: bool,
    bridge_request_auth_credentials_zeroized: bool,
    bridge_target_request_accounting: Option<BridgeTargetRequestAccountingObservation>,
    containment_readback_complete: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeCleanupEvidence {
    sockets: Vec<SocketObservation>,
    cleanup: CleanupObservation,
    native: NativeCleanupReceipt,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeAbortEvidence {
    failed_phase: NativeSupervisorPhase,
    failure_code: &'static str,
    observation: AuthorityOwnedAbortObservation,
    native_cleanup: NativeCleanupReceipt,
}

#[derive(Clone)]
pub(crate) struct NativeRecoveredCompletedEvidence {
    journal: NativeRecoveredStageJournalEvidence,
    actions: NativeRecoveredStageActionEvidence,
    completed_stage: NativeCompletedStageJournalBinding,
    native_prepared: NativePreparedEvidence,
    suspended: SuspendedRootReceipt,
    launch: RootLaunchObservation,
    terminal: NativeTerminalEvidence,
    cleanup: NativeCleanupEvidence,
    armed_admission: NativeArmedAdmissionReceipt,
    origin: NativeOriginEnvelopeReceipt,
    external_actions_replayed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeRecoveredStageJournalEvidence {
    readback: staged_start::NativeStageJournalStoreReadback,
    sealed_file_identity: FileIdentity,
    sealed_parent_identity: FileIdentity,
    sealed_held_handle_binding_digest: Digest,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeRecoveredStageActionEvidence {
    prepared: Option<NativePreparedFoundation>,
    bridge_created: Option<staged_start::NativeCreatedRootReceipt>,
    bridge_resumed: Option<ResumedRootReceipt>,
    driver_created: Option<staged_start::NativeCreatedRootReceipt>,
    driver_resumed: Option<ResumedRootReceipt>,
    armed_admission: Option<NativeArmedAdmissionReceipt>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct NativeRecoveredNormalTerminationReceipt {
    kind: NativeTerminationKind,
    requested_at_unix_ms: u64,
    recorded_at_unix_ms: u64,
    branch_head_sequence: u64,
    branch_head_digest: Digest,
    intent_sequence: u64,
    intent_record_digest: Digest,
    armed_receipt_digest: Option<Digest>,
    terminal_payload_digest: Digest,
    cleanup_payload_digest: Digest,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum NativeRecoveredNormalTerminalEvidence {
    PreArmed {
        observation: AuthorityOwnedAbortObservation,
        native_cleanup: NativeCleanupReceipt,
    },
    Armed {
        terminal: NativeTerminalEvidence,
        cleanup: NativeCleanupEvidence,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeRecoveredNormalTerminationEvidence {
    journal: NativeRecoveredStageJournalEvidence,
    actions: NativeRecoveredStageActionEvidence,
    receipt: NativeRecoveredNormalTerminationReceipt,
    normal: NativeRecoveredNormalTerminalEvidence,
    external_actions_replayed: bool,
}

pub(crate) enum NativeRestartRecoveryEvidence {
    Completed(NativeRecoveredCompletedEvidence),
    NormalTerminated(NativeRecoveredNormalTerminationEvidence),
    Burned(NativeAbortEvidence),
}

#[derive(Clone, PartialEq, Eq)]
pub(crate) struct NativeCompletedRunProof {
    terminal: CompletedRunProof,
    admission: NativeAdmissionBinding,
    completed_stage: NativeCompletedStageJournalBinding,
    canonical_origin_envelope_bytes: Vec<u8>,
    canonical_origin_envelope_digest: Digest,
    origin_ticket_digest: Digest,
    authority_ticket_digest: Digest,
    origin_sealed_at: u64,
}

impl fmt::Debug for NativeCompletedRunProof {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NativeCompletedRunProof")
            .field("result_bytes", &"[redacted]")
            .field("origin_envelope", &"[redacted]")
            .field("finalized_at", &self.terminal.finalized_at())
            .field("cleanup_observed_at", &self.terminal.cleanup_observed_at())
            .field("origin_sealed_at", &self.origin_sealed_at)
            .finish()
    }
}

impl NativeCompletedRunProof {
    #[cfg(test)]
    pub(crate) fn for_runtime_test(
        terminal: CompletedRunProof,
        prepared_receipt_digest: Digest,
        armed_receipt_digest: Digest,
        policy_snapshot_digest: Digest,
        recovery_bundle_digest: Digest,
        canonical_origin_envelope_bytes: Vec<u8>,
    ) -> Result<Self, SupervisorError> {
        if [
            prepared_receipt_digest,
            armed_receipt_digest,
            policy_snapshot_digest,
            recovery_bundle_digest,
        ]
        .iter()
        .any(is_zero_digest)
            || canonical_origin_envelope_bytes.is_empty()
            || canonical_origin_envelope_bytes.len() > MAX_ORIGIN_ENVELOPE_BYTES
        {
            return Err(SupervisorError::new(
                "authority_native_runtime_test_proof_invalid",
            ));
        }
        let mut hasher = Sha256::new();
        hasher.update(ADMISSION_BINDING_DOMAIN);
        hasher.update(prepared_receipt_digest);
        hasher.update(armed_receipt_digest);
        hasher.update(policy_snapshot_digest);
        hasher.update(recovery_bundle_digest);
        let admission = NativeAdmissionBinding {
            prepared_receipt_digest,
            armed_receipt_digest,
            policy_snapshot_digest,
            recovery_bundle_digest,
            binding_digest: hasher.finalize().into(),
        };
        let completed_stage = NativeCompletedStageJournalBinding::for_runtime_test(
            recovery_bundle_digest,
            armed_receipt_digest,
        );
        let canonical_origin_envelope_digest =
            Sha256::digest(&canonical_origin_envelope_bytes).into();
        let origin_binding = parse_native_origin_ticket_binding(&canonical_origin_envelope_bytes)?;
        if origin_binding.authority_ticket_digest != *terminal.ticket_digest()
            || origin_binding.cleanup_digest != *terminal.cleanup_receipt_digest()
        {
            return Err(SupervisorError::new(
                "authority_native_origin_authority_ticket_mismatch",
            ));
        }
        let origin_sealed_at = terminal.cleanup_observed_at().saturating_add(1);
        Ok(Self {
            terminal,
            admission,
            completed_stage,
            canonical_origin_envelope_bytes,
            canonical_origin_envelope_digest,
            origin_ticket_digest: origin_binding.origin_ticket_digest,
            authority_ticket_digest: origin_binding.authority_ticket_digest,
            origin_sealed_at,
        })
    }

    pub(crate) fn terminal(&self) -> &CompletedRunProof {
        &self.terminal
    }

    pub(crate) fn result_bytes(&self) -> &[u8] {
        self.terminal.result_bytes()
    }

    pub(crate) fn result_digest(&self) -> &Digest {
        self.terminal.result_digest()
    }

    pub(crate) fn cleanup_receipt_digest(&self) -> &Digest {
        self.terminal.cleanup_receipt_digest()
    }

    pub(crate) fn canonical_origin_envelope_bytes(&self) -> &[u8] {
        &self.canonical_origin_envelope_bytes
    }

    pub(crate) fn canonical_origin_envelope_digest(&self) -> &Digest {
        &self.canonical_origin_envelope_digest
    }

    pub(crate) fn origin_ticket_digest(&self) -> &Digest {
        &self.origin_ticket_digest
    }

    pub(crate) fn authority_ticket_digest(&self) -> &Digest {
        &self.authority_ticket_digest
    }

    pub(crate) fn origin_sealed_at(&self) -> u64 {
        self.origin_sealed_at
    }

    pub(crate) fn admission(&self) -> &NativeAdmissionBinding {
        &self.admission
    }

    pub(crate) fn completed_stage(&self) -> &NativeCompletedStageJournalBinding {
        &self.completed_stage
    }
}

#[derive(Clone, PartialEq, Eq)]
pub(crate) struct NativeNormalTerminationRecoveryBinding {
    armed_receipt_digest: Option<Digest>,
    stage_journal_head_digest: Digest,
    termination_intent_digest: Digest,
    terminal_digest: Digest,
    cleanup_digest: Digest,
}

impl NativeNormalTerminationRecoveryBinding {
    fn from_verified_replay(material: stage_journal::VerifiedNormalTerminationMaterial) -> Self {
        Self {
            armed_receipt_digest: material.armed_receipt_digest(),
            stage_journal_head_digest: material.branch_head_digest(),
            termination_intent_digest: material.intent_record_digest(),
            terminal_digest: material.terminal_payload_digest(),
            cleanup_digest: material.cleanup_payload_digest(),
        }
    }

    pub(crate) fn armed_receipt_digest(&self) -> Option<&Digest> {
        self.armed_receipt_digest.as_ref()
    }

    pub(crate) fn stage_journal_head_digest(&self) -> &Digest {
        &self.stage_journal_head_digest
    }

    pub(crate) fn termination_intent_digest(&self) -> &Digest {
        &self.termination_intent_digest
    }

    pub(crate) fn terminal_digest(&self) -> &Digest {
        &self.terminal_digest
    }

    pub(crate) fn cleanup_digest(&self) -> &Digest {
        &self.cleanup_digest
    }
}

impl NativeRecoveredNormalTerminationReceipt {
    fn from_verified_replay(material: stage_journal::VerifiedNormalTerminationMaterial) -> Self {
        Self {
            kind: native_termination_kind(material.termination_kind()),
            requested_at_unix_ms: material.requested_at_unix_ms(),
            recorded_at_unix_ms: material.recorded_at_unix_ms(),
            branch_head_sequence: material.branch_head_sequence(),
            branch_head_digest: material.branch_head_digest(),
            intent_sequence: material.intent_sequence(),
            intent_record_digest: material.intent_record_digest(),
            armed_receipt_digest: material.armed_receipt_digest(),
            terminal_payload_digest: material.terminal_payload_digest(),
            cleanup_payload_digest: material.cleanup_payload_digest(),
        }
    }

    fn verifies_replay(&self, material: stage_journal::VerifiedNormalTerminationMaterial) -> bool {
        *self == Self::from_verified_replay(material)
    }
}

#[derive(Clone, PartialEq, Eq)]
pub(crate) struct NativeBurnedRunProof {
    terminal: BurnedRunProof,
    admission: Option<NativeAdmissionBinding>,
    normal_termination_recovery: Option<NativeNormalTerminationRecoveryBinding>,
}

impl fmt::Debug for NativeBurnedRunProof {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NativeBurnedRunProof")
            .field("reason", &self.terminal.reason())
            .field("terminal_ready_at", &self.terminal.terminal_ready_at())
            .field("cleanup_observed_at", &self.terminal.cleanup_observed_at())
            .finish()
    }
}

impl NativeBurnedRunProof {
    #[cfg(test)]
    pub(crate) fn for_runtime_test(
        terminal: BurnedRunProof,
        admission_digests: Option<(Digest, Digest, Digest, Digest)>,
    ) -> Result<Self, SupervisorError> {
        let admission = match admission_digests {
            Some((prepared, armed, policy, recovery)) => {
                if [prepared, armed, policy, recovery]
                    .iter()
                    .any(is_zero_digest)
                {
                    return Err(SupervisorError::new(
                        "authority_native_runtime_test_proof_invalid",
                    ));
                }
                let mut hasher = Sha256::new();
                hasher.update(ADMISSION_BINDING_DOMAIN);
                hasher.update(prepared);
                hasher.update(armed);
                hasher.update(policy);
                hasher.update(recovery);
                Some(NativeAdmissionBinding {
                    prepared_receipt_digest: prepared,
                    armed_receipt_digest: armed,
                    policy_snapshot_digest: policy,
                    recovery_bundle_digest: recovery,
                    binding_digest: hasher.finalize().into(),
                })
            }
            None => None,
        };
        Ok(Self {
            terminal,
            admission,
            normal_termination_recovery: None,
        })
    }

    #[cfg(test)]
    pub(crate) fn for_runtime_recovered_test(
        terminal: BurnedRunProof,
        admission_digests: Option<(Digest, Digest, Digest, Digest)>,
        recovery_digests: (Option<Digest>, Digest, Digest, Digest, Digest),
    ) -> Result<Self, SupervisorError> {
        let (armed, stage_head, intent, terminal_digest, cleanup) = recovery_digests;
        if !matches!(
            terminal.reason(),
            BurnReason::Cancelled | BurnReason::TimedOut
        ) || [stage_head, intent, terminal_digest, cleanup]
            .iter()
            .any(is_zero_digest)
            || armed.is_some_and(|digest| is_zero_digest(&digest))
            || admission_digests.is_some() != armed.is_some()
        {
            return Err(SupervisorError::new(
                "authority_native_runtime_test_recovery_invalid",
            ));
        }
        let mut proof = Self::for_runtime_test(terminal, admission_digests)?;
        proof.normal_termination_recovery = Some(NativeNormalTerminationRecoveryBinding {
            armed_receipt_digest: armed,
            stage_journal_head_digest: stage_head,
            termination_intent_digest: intent,
            terminal_digest,
            cleanup_digest: cleanup,
        });
        Ok(proof)
    }

    pub(crate) fn terminal(&self) -> &BurnedRunProof {
        &self.terminal
    }

    pub(crate) fn into_terminal(self) -> BurnedRunProof {
        self.terminal
    }

    pub(crate) fn cleanup_receipt_digest(&self) -> &Digest {
        self.terminal.cleanup_receipt_digest()
    }

    pub(crate) fn admission(&self) -> Option<&NativeAdmissionBinding> {
        self.admission.as_ref()
    }

    pub(crate) fn normal_termination_recovery(
        &self,
    ) -> Option<&NativeNormalTerminationRecoveryBinding> {
        self.normal_termination_recovery.as_ref()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum ValidatedNativeTerminalRun {
    Completed(NativeCompletedRunProof),
    Burned(NativeBurnedRunProof),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum NativeTerminalPoll {
    Running,
    Terminal(NativeTerminalEvidence),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum NativeTerminationKind {
    Cancelled,
    TimedOut,
}

impl NativeTerminationKind {
    fn digest_tag(self) -> u8 {
        match self {
            Self::Cancelled => 1,
            Self::TimedOut => 2,
        }
    }
}

fn native_termination_kind(kind: stage_journal::StageTerminationKind) -> NativeTerminationKind {
    match kind {
        stage_journal::StageTerminationKind::Cancelled => NativeTerminationKind::Cancelled,
        stage_journal::StageTerminationKind::TimedOut => NativeTerminationKind::TimedOut,
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NativeTerminationIntentReceipt {
    authority_identity_digest: Digest,
    ticket_digest: Digest,
    run_binding_digest: Digest,
    prepared_receipt_digest: Digest,
    armed_receipt_digest: Digest,
    policy_snapshot_digest: Digest,
    kind: NativeTerminationKind,
    requested_at_unix_ms: u64,
    recorded_at_unix_ms: u64,
    journal_sequence: u64,
    previous_record_digest: Digest,
    record_digest: Digest,
    append_flushed: bool,
    readback_verified: bool,
    service_owned_sealed_journal: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum NativeArmedTerminationAttempt {
    Recorded(NativeTerminationIntentReceipt),
    Uncertain,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum NativeArmedTerminationAcknowledgement {
    Recorded(NativeTerminationKind),
    Uncertain,
}

impl NativeTerminationIntentReceipt {
    #[allow(clippy::too_many_arguments)]
    fn from_service_journal_readback(
        policy: &SupervisorPolicy,
        prepared_receipt_digest: Digest,
        armed: &ArmedRecoveryReceipt,
        policy_snapshot_digest: Digest,
        kind: NativeTerminationKind,
        requested_at_unix_ms: u64,
        recorded_at_unix_ms: u64,
        journal_sequence: u64,
        previous_record_digest: Digest,
    ) -> Self {
        let armed_receipt_digest = Sha256::digest(armed.encode()).into();
        let mut receipt = Self {
            authority_identity_digest: policy.authority_identity_digest,
            ticket_digest: policy.ticket_digest,
            run_binding_digest: policy.run_binding_digest,
            prepared_receipt_digest,
            armed_receipt_digest,
            policy_snapshot_digest,
            kind,
            requested_at_unix_ms,
            recorded_at_unix_ms,
            journal_sequence,
            previous_record_digest,
            record_digest: [0; 32],
            append_flushed: true,
            readback_verified: true,
            service_owned_sealed_journal: true,
        };
        receipt.record_digest = termination_intent_receipt_digest(&receipt);
        receipt
    }
}

pub(crate) trait ServiceOwnedNativeApi: Send {
    fn preflight(
        &mut self,
        policy: &SupervisorPolicy,
    ) -> Result<NativeCapabilityReceipt, SupervisorError>;

    #[cfg(test)]
    fn prepare(
        &mut self,
        policy: &SupervisorPolicy,
        prepared: &PreparedRecoveryReceipt,
        policy_snapshot: &[u8],
    ) -> Result<NativePreparedEvidence, SupervisorError>;

    #[cfg(test)]
    fn launch_root_suspended(
        &mut self,
        policy: &SupervisorPolicy,
        prepared: &NativePreparedEvidence,
    ) -> Result<SuspendedRootReceipt, SupervisorError>;

    #[cfg(test)]
    fn assign_root_to_job(
        &mut self,
        policy: &SupervisorPolicy,
        prepared: &NativePreparedEvidence,
        suspended: &SuspendedRootReceipt,
    ) -> Result<JobAssignmentReceipt, SupervisorError>;

    #[cfg(test)]
    fn resume_root(
        &mut self,
        policy: &SupervisorPolicy,
        prepared: &NativePreparedEvidence,
        suspended: &SuspendedRootReceipt,
        assignment: &JobAssignmentReceipt,
    ) -> Result<ResumedRootReceipt, SupervisorError>;

    fn poll_terminal(
        &mut self,
        policy: &SupervisorPolicy,
        prepared: &NativePreparedEvidence,
        armed: &ArmedRecoveryReceipt,
    ) -> Result<NativeTerminalPoll, SupervisorError>;

    fn bind_admission_after_arm(
        &mut self,
        policy: &SupervisorPolicy,
        prepared: &NativePreparedEvidence,
        armed: &ArmedRecoveryReceipt,
        policy_snapshot: &[u8],
    ) -> Result<NativeArmedAdmissionReceipt, SupervisorError>;

    fn request_armed_termination(
        &mut self,
        policy: &SupervisorPolicy,
        prepared: &NativePreparedEvidence,
        armed: &ArmedRecoveryReceipt,
    ) -> Result<NativeArmedTerminationAttempt, SupervisorError>;

    fn contain_terminal(
        &mut self,
        policy: &SupervisorPolicy,
        prepared: &NativePreparedEvidence,
        terminal: &NativeTerminalEvidence,
    ) -> Result<NativeCleanupEvidence, SupervisorError>;

    fn seal_origin_after_cleanup(
        &mut self,
        policy: &SupervisorPolicy,
        prepared: &NativePreparedEvidence,
        terminal: &NativeTerminalEvidence,
        cleanup: &NativeCleanupEvidence,
        completed: &CompletedRunProof,
        admission: &NativeAdmissionBinding,
    ) -> Result<NativeOriginEnvelopeReceipt, SupervisorError>;

    fn contain_after_failure(
        &mut self,
        policy: &SupervisorPolicy,
        prepared: &PreparedRecoveryReceipt,
        armed: Option<&ArmedRecoveryReceipt>,
        phase: NativeSupervisorPhase,
        reason: BurnReason,
        failure_code: &'static str,
    ) -> Result<NativeAbortEvidence, SupervisorError>;

    fn recover_after_restart(
        &mut self,
        policy: &SupervisorPolicy,
        prepared: &PreparedRecoveryReceipt,
        armed: Option<&ArmedRecoveryReceipt>,
        policy_snapshot: &[u8],
        reason: BurnReason,
    ) -> Result<NativeRestartRecoveryEvidence, SupervisorError>;
}

/// Owns every live capability after the root has been created suspended,
/// atomically placed in the generation-bound Job, membership-revalidated, and
/// resumed. The outer runtime must durably persist `armed` before polling this
/// value. It is intentionally non-Clone so a second poll/cleanup lane cannot be
/// fabricated from receipt data.
pub(crate) struct NativeArmedRun {
    policy: SupervisorPolicy,
    prepared_receipt: PreparedRecoveryReceipt,
    policy_snapshot: Vec<u8>,
    native_prepared: NativePreparedEvidence,
    suspended: SuspendedRootReceipt,
    launch: RootLaunchObservation,
    armed: ArmedRecoveryReceipt,
    admission: NativeAdmissionBinding,
    termination_intent: Option<NativeTerminationIntentReceipt>,
    stage_journal: Option<staged_start::NativeStageJournalLease>,
    stage_termination_head_digest: Option<Digest>,
    stage_termination_intent_digest: Option<Digest>,
    normal_terminal_pending: Option<NativeArmedNormalTerminalPending>,
}

#[derive(Debug, Clone)]
struct NativeArmedNormalTerminalPending {
    proof: BurnedRunProof,
    terminal_digest: Digest,
    cleanup_digest: Digest,
}

impl NativeArmedRun {
    pub(crate) fn armed_receipt(&self) -> &ArmedRecoveryReceipt {
        &self.armed
    }
}

#[cfg(test)]
pub(crate) enum NativeStartOutcome {
    Armed(NativeArmedRun),
    Terminal(ValidatedNativeTerminalRun),
}

pub(crate) enum NativeAdvanceOutcome {
    Running(NativeArmedRun),
    Terminal(ValidatedNativeTerminalRun),
    Retrying(NativeArmedRun, &'static str),
}

pub(crate) struct ServiceOwnedNativeSupervisor<A: ServiceOwnedNativeApi> {
    api: A,
}

impl<A: ServiceOwnedNativeApi + ServiceOwnedStagedNativeApi> ServiceOwnedNativeSupervisor<A> {
    pub(crate) fn new(api: A) -> Self {
        Self { api }
    }

    #[cfg(test)]
    fn api(&self) -> &A {
        &self.api
    }

    #[cfg(test)]
    pub(crate) fn start_to_armed(
        &mut self,
        prepared_run: PreparedRun,
    ) -> Result<NativeStartOutcome, SupervisorError> {
        let policy = decode_supervisor_policy_snapshot(prepared_run.policy_snapshot())?;
        if !prepared_run.receipt().verifies_policy(&policy) {
            return Err(SupervisorError::new(
                "authority_native_prepared_policy_mismatch",
            ));
        }
        let prepared_receipt = prepared_run.receipt().clone();
        let policy_snapshot = prepared_run.policy_snapshot().to_vec();

        let capabilities = self.api.preflight(&policy)?;
        validate_capabilities(&capabilities)?;

        let native_prepared = match self
            .api
            .prepare(&policy, &prepared_receipt, &policy_snapshot)
        {
            Ok(value) => value,
            Err(error) => {
                return self
                    .burn_after_failure(
                        &policy,
                        &prepared_receipt,
                        None,
                        NativeSupervisorPhase::Prepare,
                        error.code(),
                        None,
                    )
                    .map(NativeStartOutcome::Terminal)
            }
        };
        if let Err(error) = validate_native_prepared(
            &policy,
            &prepared_receipt,
            &policy_snapshot,
            &native_prepared,
        ) {
            return self
                .burn_after_failure(
                    &policy,
                    &prepared_receipt,
                    None,
                    NativeSupervisorPhase::Prepare,
                    error.code(),
                    None,
                )
                .map(NativeStartOutcome::Terminal);
        }

        let suspended = match self.api.launch_root_suspended(&policy, &native_prepared) {
            Ok(value) => value,
            Err(error) => {
                return self
                    .burn_after_failure(
                        &policy,
                        &prepared_receipt,
                        None,
                        NativeSupervisorPhase::LaunchSuspended,
                        error.code(),
                        None,
                    )
                    .map(NativeStartOutcome::Terminal)
            }
        };
        if let Err(error) = validate_suspended_root(&policy, &native_prepared, &suspended) {
            return self
                .burn_after_failure(
                    &policy,
                    &prepared_receipt,
                    None,
                    NativeSupervisorPhase::LaunchSuspended,
                    error.code(),
                    None,
                )
                .map(NativeStartOutcome::Terminal);
        }

        let assignment = match self
            .api
            .assign_root_to_job(&policy, &native_prepared, &suspended)
        {
            Ok(value) => value,
            Err(error) => {
                return self
                    .burn_after_failure(
                        &policy,
                        &prepared_receipt,
                        None,
                        NativeSupervisorPhase::AssignJob,
                        error.code(),
                        None,
                    )
                    .map(NativeStartOutcome::Terminal)
            }
        };
        if let Err(error) =
            validate_job_assignment(&policy, &native_prepared, &suspended, &assignment)
        {
            return self
                .burn_after_failure(
                    &policy,
                    &prepared_receipt,
                    None,
                    NativeSupervisorPhase::AssignJob,
                    error.code(),
                    None,
                )
                .map(NativeStartOutcome::Terminal);
        }

        let resumed = match self
            .api
            .resume_root(&policy, &native_prepared, &suspended, &assignment)
        {
            Ok(value) => value,
            Err(error) => {
                return self
                    .burn_after_failure(
                        &policy,
                        &prepared_receipt,
                        None,
                        NativeSupervisorPhase::Resume,
                        error.code(),
                        None,
                    )
                    .map(NativeStartOutcome::Terminal)
            }
        };
        if let Err(error) =
            validate_resumed_root(&policy, &native_prepared, &suspended, &assignment, &resumed)
        {
            return self
                .burn_after_failure(
                    &policy,
                    &prepared_receipt,
                    None,
                    NativeSupervisorPhase::Resume,
                    error.code(),
                    None,
                )
                .map(NativeStartOutcome::Terminal);
        }
        let launch = root_launch_observation(ProcessRole::Driver, &resumed);
        let root_process = process_from_armed_root(&policy, &suspended, &launch);
        let armed = ArmedRecoveryReceipt::from_armed_launch(
            &policy,
            &prepared_receipt,
            &root_process,
            &launch,
        );
        let armed_admission = match self.api.bind_admission_after_arm(
            &policy,
            &native_prepared,
            &armed,
            &policy_snapshot,
        ) {
            Ok(value) => value,
            Err(error) => {
                return self
                    .burn_after_failure(
                        &policy,
                        &prepared_receipt,
                        Some(&armed),
                        NativeSupervisorPhase::Resume,
                        error.code(),
                        None,
                    )
                    .map(NativeStartOutcome::Terminal)
            }
        };
        let admission = match build_admission_binding(
            &prepared_receipt,
            &armed,
            &policy_snapshot,
            &native_prepared.admission,
            &armed_admission,
        ) {
            Ok(value) => value,
            Err(error) => {
                return self
                    .burn_after_failure(
                        &policy,
                        &prepared_receipt,
                        Some(&armed),
                        NativeSupervisorPhase::Resume,
                        error.code(),
                        None,
                    )
                    .map(NativeStartOutcome::Terminal)
            }
        };

        Ok(NativeStartOutcome::Armed(NativeArmedRun {
            policy,
            prepared_receipt,
            policy_snapshot,
            native_prepared,
            suspended,
            launch,
            armed,
            admission,
            termination_intent: None,
            stage_journal: None,
            stage_termination_head_digest: None,
            stage_termination_intent_digest: None,
            normal_terminal_pending: None,
        }))
    }

    pub(crate) fn request_armed_termination(
        &mut self,
        run: &mut NativeArmedRun,
    ) -> Result<NativeArmedTerminationAcknowledgement, SupervisorError> {
        if let Some(existing) = &run.termination_intent {
            return Ok(NativeArmedTerminationAcknowledgement::Recorded(
                existing.kind,
            ));
        }
        if run.stage_journal.is_some() {
            let armed_digest: Digest = Sha256::digest(run.armed.encode()).into();
            let prior_head = run
                .stage_journal
                .as_ref()
                .expect("stage journal presence was checked")
                .journal
                .head();
            let response = match self.api.record_stage_termination(
                &run.policy,
                &run.stage_journal
                    .as_ref()
                    .expect("stage journal presence was checked")
                    .journal,
                Some(armed_digest),
            )? {
                staged_start::NativeStageTerminationAttempt::Recorded(response) => response,
                staged_start::NativeStageTerminationAttempt::Uncertain => {
                    return Ok(NativeArmedTerminationAcknowledgement::Uncertain);
                }
            };
            staged_start::validate_stage_termination_timing(&run.policy, &response)?;
            let stage_kind = match response.kind {
                NativeTerminationKind::Cancelled => stage_journal::StageTerminationKind::Cancelled,
                NativeTerminationKind::TimedOut => stage_journal::StageTerminationKind::TimedOut,
            };
            let append = run
                .stage_journal
                .as_ref()
                .expect("stage journal presence was checked")
                .journal
                .plan_termination_intent(
                    stage_kind,
                    response.requested_at_unix_ms,
                    response.recorded_at_unix_ms,
                    Some(armed_digest),
                )
                .map_err(|error| SupervisorError::new(error.code()))?;
            staged_start::append_readback(
                run.stage_journal
                    .as_mut()
                    .expect("stage journal presence was checked"),
                &append,
                response.journal,
            )?;
            let stage_intent = run
                .stage_journal
                .as_ref()
                .expect("stage journal presence was checked")
                .journal
                .head();
            let receipt = NativeTerminationIntentReceipt::from_service_journal_readback(
                &run.policy,
                run.prepared_receipt.digest(),
                &run.armed,
                Sha256::digest(&run.policy_snapshot).into(),
                response.kind,
                response.requested_at_unix_ms,
                response.recorded_at_unix_ms,
                stage_intent.sequence,
                prior_head.record_digest,
            );
            validate_native_termination_intent(
                &run.policy,
                &run.prepared_receipt,
                &run.armed,
                &run.policy_snapshot,
                response.kind,
                &receipt,
            )?;
            run.stage_termination_head_digest = Some(prior_head.record_digest);
            run.stage_termination_intent_digest = Some(stage_intent.record_digest);
            run.termination_intent = Some(receipt);
            return Ok(NativeArmedTerminationAcknowledgement::Recorded(
                response.kind,
            ));
        }
        let receipt = match self.api.request_armed_termination(
            &run.policy,
            &run.native_prepared,
            &run.armed,
        )? {
            NativeArmedTerminationAttempt::Recorded(receipt) => receipt,
            NativeArmedTerminationAttempt::Uncertain => {
                return Ok(NativeArmedTerminationAcknowledgement::Uncertain);
            }
        };
        let kind = receipt.kind;
        validate_native_termination_intent(
            &run.policy,
            &run.prepared_receipt,
            &run.armed,
            &run.policy_snapshot,
            kind,
            &receipt,
        )?;
        run.termination_intent = Some(receipt);
        Ok(NativeArmedTerminationAcknowledgement::Recorded(kind))
    }

    pub(crate) fn advance_armed(&mut self, run: NativeArmedRun) -> NativeAdvanceOutcome {
        if run.normal_terminal_pending.is_some() {
            return self.advance_armed_stage_terminal(run);
        }
        let terminal = match self
            .api
            .poll_terminal(&run.policy, &run.native_prepared, &run.armed)
        {
            Ok(NativeTerminalPoll::Running) => {
                return NativeAdvanceOutcome::Running(run);
            }
            Ok(NativeTerminalPoll::Terminal(value)) => value,
            Err(error) => {
                return self.burn_armed_failure_or_retry(
                    run,
                    NativeSupervisorPhase::ObserveTerminal,
                    error.code(),
                );
            }
        };
        self.finish_armed_terminal(run, terminal)
    }

    fn finish_armed_terminal(
        &mut self,
        run: NativeArmedRun,
        terminal: NativeTerminalEvidence,
    ) -> NativeAdvanceOutcome {
        if let Err(error) =
            validate_native_terminal_after_intent(&terminal, run.termination_intent.as_ref())
        {
            return self.burn_armed_failure_or_retry(
                run,
                NativeSupervisorPhase::Finalize,
                error.code(),
            );
        }
        if let Err(error) = validate_native_terminal(
            &run.policy,
            &run.native_prepared,
            &run.suspended,
            &run.launch,
            &terminal,
        ) {
            return self.burn_armed_failure_or_retry(
                run,
                NativeSupervisorPhase::Finalize,
                error.code(),
            );
        }

        let cleanup = match self
            .api
            .contain_terminal(&run.policy, &run.native_prepared, &terminal)
        {
            Ok(value) => value,
            Err(error) => {
                return self.burn_armed_failure_or_retry(
                    run,
                    NativeSupervisorPhase::Contain,
                    error.code(),
                )
            }
        };
        if let Err(error) =
            validate_native_cleanup(&run.policy, &run.native_prepared, &terminal, &cleanup)
        {
            return self.burn_armed_failure_or_retry(
                run,
                NativeSupervisorPhase::Contain,
                error.code(),
            );
        }

        let observation = AuthorityOwnedRunObservation {
            ticket_consumed_at: run.native_prepared.ticket_consumed_at,
            runner: run.native_prepared.runner.clone(),
            artifacts: run.native_prepared.artifacts.clone(),
            launches: vec![terminal.bridge_root_launch.clone(), run.launch.clone()],
            processes: terminal.processes.clone(),
            helpers: terminal.helpers.clone(),
            job: terminal.job.clone(),
            sockets: cleanup.sockets.clone(),
            finalization: terminal.finalization.clone(),
            terminal: terminal.terminal.clone(),
            cleanup: cleanup.cleanup.clone(),
        };
        match validate_authority_owned_run(&run.policy, &observation) {
            Ok(ValidatedTerminalRun::Completed(proof)) => {
                let completed_stage = match run.stage_journal.as_ref() {
                    Some(stage_journal) => match verified_live_completed_stage_journal(
                        &run.policy,
                        &run.armed,
                        run.native_prepared.start_contract_digest,
                        stage_journal,
                    ) {
                        Ok(binding) => binding,
                        Err(error) => {
                            return self.burn_armed_failure_or_retry(
                                run,
                                NativeSupervisorPhase::Finalize,
                                error.code(),
                            )
                        }
                    },
                    None => {
                        #[cfg(test)]
                        {
                            NativeCompletedStageJournalBinding::for_runtime_test(
                                run.native_prepared.start_contract_digest,
                                Sha256::digest(run.armed.encode()).into(),
                            )
                        }
                        #[cfg(not(test))]
                        {
                            return self.burn_armed_failure_or_retry(
                                run,
                                NativeSupervisorPhase::Finalize,
                                "authority_native_completed_stage_journal_missing",
                            );
                        }
                    }
                };
                let origin = match self.api.seal_origin_after_cleanup(
                    &run.policy,
                    &run.native_prepared,
                    &terminal,
                    &cleanup,
                    &proof,
                    &run.admission,
                ) {
                    Ok(value) => value,
                    Err(error) => {
                        return self.burn_armed_failure_or_retry(
                            run,
                            NativeSupervisorPhase::Finalize,
                            error.code(),
                        )
                    }
                };
                if let Err(error) =
                    validate_origin_envelope(&run.policy, &proof, &run.admission, &origin)
                {
                    return self.burn_armed_failure_or_retry(
                        run,
                        NativeSupervisorPhase::Finalize,
                        error.code(),
                    );
                }
                NativeAdvanceOutcome::Terminal(ValidatedNativeTerminalRun::Completed(
                    NativeCompletedRunProof {
                        terminal: proof,
                        admission: run.admission,
                        completed_stage,
                        canonical_origin_envelope_bytes: origin.canonical_bytes,
                        canonical_origin_envelope_digest: origin.canonical_digest,
                        origin_ticket_digest: origin.origin_ticket_digest,
                        authority_ticket_digest: origin.authority_ticket_digest,
                        origin_sealed_at: origin.sealed_at,
                    },
                ))
            }
            Ok(ValidatedTerminalRun::Burned(proof)) => {
                if run.stage_journal.is_some() {
                    let Some(intent) = run.termination_intent.as_ref() else {
                        return self.burn_armed_failure_or_retry(
                            run,
                            NativeSupervisorPhase::Finalize,
                            "authority_native_stage_termination_intent_missing",
                        );
                    };
                    let Some(stage_head_digest) = run.stage_termination_head_digest else {
                        return self.burn_armed_failure_or_retry(
                            run,
                            NativeSupervisorPhase::Finalize,
                            "authority_native_stage_termination_head_missing",
                        );
                    };
                    let Some(stage_intent_digest) = run.stage_termination_intent_digest else {
                        return self.burn_armed_failure_or_retry(
                            run,
                            NativeSupervisorPhase::Finalize,
                            "authority_native_stage_termination_intent_missing",
                        );
                    };
                    let terminal_digest = derive_armed_stage_terminal_digest(
                        &run.policy,
                        &run.armed,
                        &stage_head_digest,
                        &stage_intent_digest,
                        intent.kind,
                        &proof,
                    );
                    let cleanup_digest = *proof.cleanup_receipt_digest();
                    let mut run = run;
                    run.normal_terminal_pending = Some(NativeArmedNormalTerminalPending {
                        proof,
                        terminal_digest,
                        cleanup_digest,
                    });
                    self.advance_armed_stage_terminal(run)
                } else {
                    NativeAdvanceOutcome::Terminal(ValidatedNativeTerminalRun::Burned(
                        NativeBurnedRunProof {
                            terminal: proof,
                            admission: Some(run.admission),
                            normal_termination_recovery: None,
                        },
                    ))
                }
            }
            Err(error) => {
                self.burn_armed_failure_or_retry(run, NativeSupervisorPhase::Finalize, error.code())
            }
        }
    }

    fn advance_armed_stage_terminal(&mut self, mut run: NativeArmedRun) -> NativeAdvanceOutcome {
        let Some(pending) = run.normal_terminal_pending.clone() else {
            return self.burn_armed_failure_or_retry(
                run,
                NativeSupervisorPhase::Finalize,
                "authority_native_stage_terminal_pending_missing",
            );
        };
        let Some(intent) = run.termination_intent.as_ref() else {
            return self.burn_armed_failure_or_retry(
                run,
                NativeSupervisorPhase::Finalize,
                "authority_native_stage_termination_intent_missing",
            );
        };
        let stage_kind = match intent.kind {
            NativeTerminationKind::Cancelled => stage_journal::StageTerminationKind::Cancelled,
            NativeTerminationKind::TimedOut => stage_journal::StageTerminationKind::TimedOut,
        };
        let Some(stage) = run.stage_journal.as_ref() else {
            return self.burn_armed_failure_or_retry(
                run,
                NativeSupervisorPhase::Finalize,
                "authority_native_stage_journal_missing",
            );
        };
        let head = stage.journal.head();
        let append = match head.kind {
            stage_journal::StageJournalRecordKind::TerminationIntent => stage
                .journal
                .plan_terminal(stage_kind, pending.terminal_digest),
            stage_journal::StageJournalRecordKind::Terminal => stage
                .journal
                .plan_cleanup(stage_kind, pending.cleanup_digest),
            stage_journal::StageJournalRecordKind::Cleanup => {
                let Some(stage_head_digest) = run.stage_termination_head_digest else {
                    return self.burn_armed_failure_or_retry(
                        run,
                        NativeSupervisorPhase::Finalize,
                        "authority_native_stage_termination_head_missing",
                    );
                };
                let Some(stage_intent_digest) = run.stage_termination_intent_digest else {
                    return self.burn_armed_failure_or_retry(
                        run,
                        NativeSupervisorPhase::Finalize,
                        "authority_native_stage_termination_intent_missing",
                    );
                };
                let armed_receipt_digest: Digest = Sha256::digest(run.armed.encode()).into();
                return NativeAdvanceOutcome::Terminal(ValidatedNativeTerminalRun::Burned(
                    NativeBurnedRunProof {
                        terminal: pending.proof,
                        admission: Some(run.admission),
                        normal_termination_recovery: Some(NativeNormalTerminationRecoveryBinding {
                            armed_receipt_digest: Some(armed_receipt_digest),
                            stage_journal_head_digest: stage_head_digest,
                            termination_intent_digest: stage_intent_digest,
                            terminal_digest: pending.terminal_digest,
                            cleanup_digest: pending.cleanup_digest,
                        }),
                    },
                ));
            }
            _ => {
                return self.burn_armed_failure_or_retry(
                    run,
                    NativeSupervisorPhase::Finalize,
                    "authority_native_stage_terminal_state_invalid",
                );
            }
        };
        let append = match append {
            Ok(append) => append,
            Err(error) => {
                return self.burn_armed_failure_or_retry(
                    run,
                    NativeSupervisorPhase::Finalize,
                    error.code(),
                );
            }
        };
        let readback = match self.api.append_stage_journal(
            &run.policy,
            append.prior_byte_len(),
            append.record_bytes(),
        ) {
            Ok(readback) => readback,
            Err(error) => return NativeAdvanceOutcome::Retrying(run, error.code()),
        };
        if let Err(error) = staged_start::append_readback(
            run.stage_journal
                .as_mut()
                .expect("stage journal presence was checked"),
            &append,
            readback,
        ) {
            return self.burn_armed_failure_or_retry(
                run,
                NativeSupervisorPhase::Finalize,
                error.code(),
            );
        }
        NativeAdvanceOutcome::Running(run)
    }

    fn burn_armed_failure_or_retry(
        &mut self,
        run: NativeArmedRun,
        phase: NativeSupervisorPhase,
        failure_code: &'static str,
    ) -> NativeAdvanceOutcome {
        match self.burn_after_failure(
            &run.policy,
            &run.prepared_receipt,
            Some(&run.armed),
            phase,
            failure_code,
            Some(run.admission.clone()),
        ) {
            Ok(terminal) => NativeAdvanceOutcome::Terminal(terminal),
            Err(error) => NativeAdvanceOutcome::Retrying(run, error.code()),
        }
    }

    #[cfg(test)]
    pub(crate) fn execute(
        &mut self,
        prepared_run: PreparedRun,
    ) -> Result<ValidatedNativeTerminalRun, SupervisorError> {
        let mut armed = match self.start_to_armed(prepared_run)? {
            NativeStartOutcome::Armed(run) => run,
            NativeStartOutcome::Terminal(terminal) => return Ok(terminal),
        };
        loop {
            match self.advance_armed(armed) {
                NativeAdvanceOutcome::Running(run) => armed = run,
                NativeAdvanceOutcome::Terminal(terminal) => return Ok(terminal),
                NativeAdvanceOutcome::Retrying(run, _) => armed = run,
            }
        }
    }

    fn burn_after_failure(
        &mut self,
        policy: &SupervisorPolicy,
        prepared: &PreparedRecoveryReceipt,
        armed: Option<&ArmedRecoveryReceipt>,
        phase: NativeSupervisorPhase,
        failure_code: &'static str,
        admission: Option<NativeAdmissionBinding>,
    ) -> Result<ValidatedNativeTerminalRun, SupervisorError> {
        self.burn_with_reason(
            policy,
            prepared,
            armed,
            phase,
            BurnReason::Failed,
            failure_code,
            admission,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn burn_with_reason(
        &mut self,
        policy: &SupervisorPolicy,
        prepared: &PreparedRecoveryReceipt,
        armed: Option<&ArmedRecoveryReceipt>,
        phase: NativeSupervisorPhase,
        reason: BurnReason,
        failure_code: &'static str,
        admission: Option<NativeAdmissionBinding>,
    ) -> Result<ValidatedNativeTerminalRun, SupervisorError> {
        let abort =
            self.api
                .contain_after_failure(policy, prepared, armed, phase, reason, failure_code)?;
        validate_native_abort_cleanup(&abort, phase, failure_code)?;
        let proof =
            validate_authority_owned_abort(policy, prepared, armed, &abort.observation, reason)?;
        Ok(ValidatedNativeTerminalRun::Burned(NativeBurnedRunProof {
            terminal: proof,
            admission,
            normal_termination_recovery: None,
        }))
    }

    pub(crate) fn abort_armed(
        &mut self,
        run: &NativeArmedRun,
        reason: BurnReason,
        failure_code: &'static str,
    ) -> Result<NativeBurnedRunProof, SupervisorError> {
        if !matches!(reason, BurnReason::Failed | BurnReason::RestartRecovery) {
            return Err(SupervisorError::new(
                "authority_native_abort_reason_invalid",
            ));
        }
        let terminal = self.burn_with_reason(
            &run.policy,
            &run.prepared_receipt,
            Some(&run.armed),
            NativeSupervisorPhase::Contain,
            reason,
            failure_code,
            Some(run.admission.clone()),
        )?;
        match terminal {
            ValidatedNativeTerminalRun::Burned(proof) => Ok(proof),
            ValidatedNativeTerminalRun::Completed(_) => Err(SupervisorError::new(
                "authority_native_abort_returned_completed",
            )),
        }
    }

    pub(crate) fn recover_after_restart(
        &mut self,
        prepared: &PreparedRecoveryReceipt,
        armed: Option<&ArmedRecoveryReceipt>,
        policy_snapshot: &[u8],
    ) -> Result<ValidatedNativeTerminalRun, SupervisorError> {
        let policy = decode_supervisor_policy_snapshot(policy_snapshot)?;
        if !prepared.verifies_policy(&policy)
            || !prepared.verifies_policy_snapshot(policy_snapshot)
            || armed
                .is_some_and(|receipt| !receipt.verifies_for(prepared, &policy.run_binding_digest))
        {
            return Err(SupervisorError::new(
                "authority_native_recovery_binding_invalid",
            ));
        }
        let reason = BurnReason::RestartRecovery;
        let failure_code = "authority_native_restart_recovery";
        let recovered =
            self.api
                .recover_after_restart(&policy, prepared, armed, policy_snapshot, reason)?;
        match recovered {
            NativeRestartRecoveryEvidence::Burned(abort) => {
                validate_native_abort_cleanup(
                    &abort,
                    NativeSupervisorPhase::Contain,
                    failure_code,
                )?;
                let proof = validate_authority_owned_abort(
                    &policy,
                    prepared,
                    armed,
                    &abort.observation,
                    reason,
                )?;
                Ok(ValidatedNativeTerminalRun::Burned(NativeBurnedRunProof {
                    terminal: proof,
                    admission: None,
                    normal_termination_recovery: None,
                }))
            }
            NativeRestartRecoveryEvidence::Completed(recovered) => {
                let armed = armed.ok_or_else(|| {
                    SupervisorError::new("authority_native_recovered_completion_not_armed")
                })?;
                self.validate_recovered_completed(
                    &policy,
                    prepared,
                    armed,
                    policy_snapshot,
                    recovered,
                )
                .map(ValidatedNativeTerminalRun::Completed)
            }
            NativeRestartRecoveryEvidence::NormalTerminated(recovered) => {
                match validate_recovered_normal_termination(
                    &policy,
                    prepared,
                    armed,
                    policy_snapshot,
                    recovered,
                ) {
                    Ok(proof) => Ok(ValidatedNativeTerminalRun::Burned(proof)),
                    Err(error) => self.burn_with_reason(
                        &policy,
                        prepared,
                        armed,
                        NativeSupervisorPhase::Contain,
                        BurnReason::RestartRecovery,
                        error.code(),
                        None,
                    ),
                }
            }
        }
    }

    fn validate_recovered_completed(
        &mut self,
        policy: &SupervisorPolicy,
        prepared: &PreparedRecoveryReceipt,
        armed: &ArmedRecoveryReceipt,
        policy_snapshot: &[u8],
        recovered: NativeRecoveredCompletedEvidence,
    ) -> Result<NativeCompletedRunProof, SupervisorError> {
        if recovered.external_actions_replayed || !recovered.completed_stage.verifies() {
            return Err(SupervisorError::new(
                "authority_native_recovered_completion_journal_invalid",
            ));
        }
        let (replay, journal_binding) = verified_recovered_stage_journal(
            policy,
            prepared,
            policy_snapshot,
            &recovered.journal,
        )?;
        let expected_completed_stage =
            NativeCompletedStageJournalBinding::from_verified_clean_armed(
                replay,
                &journal_binding,
                &recovered.journal.readback.canonical_bytes,
                Sha256::digest(armed.encode()).into(),
            )?;
        if recovered.completed_stage != expected_completed_stage {
            return Err(SupervisorError::new(
                "authority_native_recovered_completion_journal_invalid",
            ));
        }
        let validated_actions = validate_recovered_stage_actions(
            policy,
            prepared,
            Some(armed),
            policy_snapshot,
            journal_binding.start_contract_digest(),
            replay,
            &recovered.actions,
        )?;
        let recovered_armed = validated_actions.armed.as_ref().ok_or_else(|| {
            SupervisorError::new("authority_native_recovered_completion_not_armed")
        })?;
        if recovered_armed.native_prepared != recovered.native_prepared
            || recovered_armed.driver_root.suspended != recovered.suspended
            || recovered_armed.launch != recovered.launch
            || recovered.actions.armed_admission.as_ref() != Some(&recovered.armed_admission)
        {
            return Err(SupervisorError::new(
                "authority_native_recovered_completion_action_mismatch",
            ));
        }
        validate_native_prepared(
            policy,
            prepared,
            policy_snapshot,
            &recovered.native_prepared,
        )?;
        validate_suspended_root(policy, &recovered.native_prepared, &recovered.suspended)?;
        let root_process = process_from_armed_root(policy, &recovered.suspended, &recovered.launch);
        let expected_armed = ArmedRecoveryReceipt::from_armed_launch(
            policy,
            prepared,
            &root_process,
            &recovered.launch,
        );
        if &expected_armed != armed {
            return Err(SupervisorError::new(
                "authority_native_recovered_armed_receipt_mismatch",
            ));
        }
        let admission = build_admission_binding(
            prepared,
            armed,
            policy_snapshot,
            &recovered.native_prepared.admission,
            &recovered.armed_admission,
        )?;
        if validated_actions.admission.as_ref() != Some(&admission) {
            return Err(SupervisorError::new(
                "authority_native_recovered_completion_action_mismatch",
            ));
        }
        validate_native_terminal(
            policy,
            &recovered.native_prepared,
            &recovered.suspended,
            &recovered.launch,
            &recovered.terminal,
        )?;
        validate_native_cleanup(
            policy,
            &recovered.native_prepared,
            &recovered.terminal,
            &recovered.cleanup,
        )?;
        let observation = AuthorityOwnedRunObservation {
            ticket_consumed_at: recovered.native_prepared.ticket_consumed_at,
            runner: recovered.native_prepared.runner.clone(),
            artifacts: recovered.native_prepared.artifacts.clone(),
            launches: vec![
                recovered.terminal.bridge_root_launch.clone(),
                recovered.launch,
            ],
            processes: recovered.terminal.processes.clone(),
            helpers: recovered.terminal.helpers.clone(),
            job: recovered.terminal.job.clone(),
            sockets: recovered.cleanup.sockets.clone(),
            finalization: recovered.terminal.finalization.clone(),
            terminal: recovered.terminal.terminal.clone(),
            cleanup: recovered.cleanup.cleanup.clone(),
        };
        let proof = match validate_authority_owned_run(policy, &observation)? {
            ValidatedTerminalRun::Completed(proof) => proof,
            ValidatedTerminalRun::Burned(_) => {
                return Err(SupervisorError::new(
                    "authority_native_recovered_completion_not_completed",
                ));
            }
        };
        validate_origin_envelope(policy, &proof, &admission, &recovered.origin)?;
        Ok(NativeCompletedRunProof {
            terminal: proof,
            admission,
            completed_stage: recovered.completed_stage,
            canonical_origin_envelope_bytes: recovered.origin.canonical_bytes,
            canonical_origin_envelope_digest: recovered.origin.canonical_digest,
            origin_ticket_digest: recovered.origin.origin_ticket_digest,
            authority_ticket_digest: recovered.origin.authority_ticket_digest,
            origin_sealed_at: recovered.origin.sealed_at,
        })
    }
}

fn verified_live_completed_stage_journal(
    policy: &SupervisorPolicy,
    armed: &ArmedRecoveryReceipt,
    expected_start_contract_digest: Digest,
    lease: &staged_start::NativeStageJournalLease,
) -> Result<NativeCompletedStageJournalBinding, SupervisorError> {
    if lease.write_sequence == 0
        || lease.write_sequence != lease.journal.record_count() as u64
        || lease.journal.binding().start_contract_digest() != expected_start_contract_digest
        || lease.file_identity.volume_serial == 0
        || lease.file_identity.file_id.iter().all(|byte| *byte == 0)
        || lease.parent_identity.volume_serial == 0
        || lease.parent_identity.file_id.iter().all(|byte| *byte == 0)
        || lease.file_identity == lease.parent_identity
    {
        return Err(SupervisorError::new(
            "authority_native_completed_stage_journal_invalid",
        ));
    }
    let declaration_digest =
        staged_start::stage_declaration_digest(policy, lease.journal.binding());
    let replay = lease
        .journal
        .verified_replay_summary(declaration_digest)
        .map_err(|error| SupervisorError::new(error.code()))?;
    NativeCompletedStageJournalBinding::from_verified_clean_armed(
        replay,
        lease.journal.binding(),
        lease.journal.bytes(),
        Sha256::digest(armed.encode()).into(),
    )
}

fn validate_recovered_normal_termination(
    policy: &SupervisorPolicy,
    prepared: &PreparedRecoveryReceipt,
    armed: Option<&ArmedRecoveryReceipt>,
    policy_snapshot: &[u8],
    recovered: NativeRecoveredNormalTerminationEvidence,
) -> Result<NativeBurnedRunProof, SupervisorError> {
    if recovered.external_actions_replayed {
        return Err(SupervisorError::new(
            "authority_native_recovered_normal_actions_replayed",
        ));
    }
    let (replay, material, start_contract_digest) = verified_recovered_normal_termination_material(
        policy,
        prepared,
        policy_snapshot,
        &recovered.journal,
    )?;
    if !recovered.receipt.verifies_replay(material) {
        return Err(SupervisorError::new(
            "authority_native_recovered_normal_receipt_mismatch",
        ));
    }
    staged_start::validate_stage_termination_values(
        policy,
        recovered.receipt.kind,
        recovered.receipt.requested_at_unix_ms,
        recovered.receipt.recorded_at_unix_ms,
    )?;
    let validated_actions = validate_recovered_stage_actions(
        policy,
        prepared,
        armed,
        policy_snapshot,
        start_contract_digest,
        replay,
        &recovered.actions,
    )?;
    let reason = match recovered.receipt.kind {
        NativeTerminationKind::Cancelled => BurnReason::Cancelled,
        NativeTerminationKind::TimedOut => BurnReason::TimedOut,
    };
    let (proof, expected_terminal_digest) =
        match (armed, &recovered.normal, validated_actions.armed.as_ref()) {
            (
                None,
                NativeRecoveredNormalTerminalEvidence::PreArmed {
                    observation,
                    native_cleanup,
                },
                None,
            ) => {
                validate_recovered_normal_cleanup(native_cleanup)?;
                let proof = validate_authority_owned_staged_termination(
                    policy,
                    prepared,
                    None,
                    observation,
                    reason,
                )?;
                if !seconds_observation_not_before_millis(
                    observation.terminal.observed_at,
                    material.recorded_at_unix_ms(),
                ) {
                    return Err(SupervisorError::new(
                        "authority_native_recovered_normal_terminal_time_invalid",
                    ));
                }
                let terminal_digest =
                    staged_start::recovered_starting_terminal_digest(policy, material, observation);
                (proof, terminal_digest)
            }
            (
                Some(armed),
                NativeRecoveredNormalTerminalEvidence::Armed { terminal, cleanup },
                Some(armed_stage),
            ) => {
                let intent = NativeTerminationIntentReceipt::from_service_journal_readback(
                    policy,
                    prepared.digest(),
                    armed,
                    Sha256::digest(policy_snapshot).into(),
                    recovered.receipt.kind,
                    material.requested_at_unix_ms(),
                    material.recorded_at_unix_ms(),
                    material.intent_sequence(),
                    material.branch_head_digest(),
                );
                validate_native_termination_intent(
                    policy,
                    prepared,
                    armed,
                    policy_snapshot,
                    recovered.receipt.kind,
                    &intent,
                )?;
                validate_native_terminal_after_intent(terminal, Some(&intent))?;
                validate_native_terminal(
                    policy,
                    &armed_stage.native_prepared,
                    &armed_stage.driver_root.suspended,
                    &armed_stage.launch,
                    terminal,
                )?;
                validate_native_cleanup(policy, &armed_stage.native_prepared, terminal, cleanup)?;
                let observation = AuthorityOwnedRunObservation {
                    ticket_consumed_at: armed_stage.native_prepared.ticket_consumed_at,
                    runner: armed_stage.native_prepared.runner.clone(),
                    artifacts: armed_stage.native_prepared.artifacts.clone(),
                    launches: vec![
                        terminal.bridge_root_launch.clone(),
                        armed_stage.launch.clone(),
                    ],
                    processes: terminal.processes.clone(),
                    helpers: terminal.helpers.clone(),
                    job: terminal.job.clone(),
                    sockets: cleanup.sockets.clone(),
                    finalization: terminal.finalization.clone(),
                    terminal: terminal.terminal.clone(),
                    cleanup: cleanup.cleanup.clone(),
                };
                let proof = match validate_authority_owned_run(policy, &observation)? {
                    ValidatedTerminalRun::Burned(proof) if proof.reason() == reason => proof,
                    _ => {
                        return Err(SupervisorError::new(
                            "authority_native_recovered_normal_terminal_mismatch",
                        ));
                    }
                };
                let terminal_digest = derive_armed_stage_terminal_digest(
                    policy,
                    armed,
                    &material.branch_head_digest(),
                    &material.intent_record_digest(),
                    recovered.receipt.kind,
                    &proof,
                );
                (proof, terminal_digest)
            }
            _ => {
                return Err(SupervisorError::new(
                    "authority_native_recovered_normal_branch_mismatch",
                ));
            }
        };
    if expected_terminal_digest != material.terminal_payload_digest()
        || *proof.cleanup_receipt_digest() != material.cleanup_payload_digest()
    {
        return Err(SupervisorError::new(
            "authority_native_recovered_normal_terminal_mismatch",
        ));
    }

    Ok(NativeBurnedRunProof {
        terminal: proof,
        admission: validated_actions.admission,
        normal_termination_recovery: Some(
            NativeNormalTerminationRecoveryBinding::from_verified_replay(material),
        ),
    })
}

fn verified_recovered_stage_journal(
    policy: &SupervisorPolicy,
    prepared: &PreparedRecoveryReceipt,
    policy_snapshot: &[u8],
    evidence: &NativeRecoveredStageJournalEvidence,
) -> Result<
    (
        stage_journal::VerifiedStageJournalReplay,
        stage_journal::StageJournalBinding,
    ),
    SupervisorError,
> {
    let readback = &evidence.readback;
    if readback.created_new
        || readback.write_sequence == 0
        || !readback.append_flushed
        || !readback.reopened_from_held_handle
        || !readback.service_owned_parent
        || !readback.owner_local_system
        || !readback.protected_restricted_dacl
        || readback.file_is_reparse_point
        || readback.parent_is_reparse_point
        || !readback.single_link
        || !readback.service_handle_held
        || readback.file_identity != evidence.sealed_file_identity
        || readback.parent_identity != evidence.sealed_parent_identity
        || readback.file_identity.volume_serial == 0
        || readback.file_identity.file_id.iter().all(|byte| *byte == 0)
        || readback.parent_identity.volume_serial == 0
        || readback
            .parent_identity
            .file_id
            .iter()
            .all(|byte| *byte == 0)
        || readback.file_identity == readback.parent_identity
        || is_zero_digest(&evidence.sealed_held_handle_binding_digest)
        || staged_start::stage_journal_held_handle_binding_digest(
            &readback.canonical_bytes,
            &readback.file_identity,
            &readback.parent_identity,
        ) != evidence.sealed_held_handle_binding_digest
    {
        return Err(SupervisorError::new(
            "authority_native_recovered_stage_journal_identity_invalid",
        ));
    }
    let binding = staged_start::recovered_stage_journal_binding(
        policy,
        prepared,
        policy_snapshot,
        &readback.canonical_bytes,
    )?;
    let declaration_digest = staged_start::stage_declaration_digest(policy, &binding);
    let journal = stage_journal::StageJournal::reopen(&readback.canonical_bytes, &binding)
        .map_err(|error| SupervisorError::new(error.code()))?;
    if journal.record_count() as u64 != readback.write_sequence {
        return Err(SupervisorError::new(
            "authority_native_recovered_stage_journal_sequence_invalid",
        ));
    }
    let replay = journal
        .verified_replay_summary(declaration_digest)
        .map_err(|error| SupervisorError::new(error.code()))?;
    Ok((replay, binding))
}

fn verified_recovered_normal_termination_material(
    policy: &SupervisorPolicy,
    prepared: &PreparedRecoveryReceipt,
    policy_snapshot: &[u8],
    evidence: &NativeRecoveredStageJournalEvidence,
) -> Result<
    (
        stage_journal::VerifiedStageJournalReplay,
        stage_journal::VerifiedNormalTerminationMaterial,
        Digest,
    ),
    SupervisorError,
> {
    let (replay, binding) =
        verified_recovered_stage_journal(policy, prepared, policy_snapshot, evidence)?;
    match replay.normal_termination_material() {
        stage_journal::VerifiedNormalTerminationReplay::Complete(material) => {
            Ok((replay, material, binding.start_contract_digest()))
        }
        stage_journal::VerifiedNormalTerminationReplay::Pending(_) => Err(SupervisorError::new(
            "authority_native_recovered_normal_termination_pending",
        )),
    }
}

struct ValidatedRecoveredArmedStage {
    native_prepared: NativePreparedEvidence,
    driver_root: AtomicRootLaunchReceipt,
    launch: RootLaunchObservation,
}

struct ValidatedRecoveredStageActions {
    admission: Option<NativeAdmissionBinding>,
    armed: Option<ValidatedRecoveredArmedStage>,
}

fn validate_recovered_stage_actions(
    policy: &SupervisorPolicy,
    prepared: &PreparedRecoveryReceipt,
    armed: Option<&ArmedRecoveryReceipt>,
    policy_snapshot: &[u8],
    start_contract_digest: Digest,
    replay: stage_journal::VerifiedStageJournalReplay,
    evidence: &NativeRecoveredStageActionEvidence,
) -> Result<ValidatedRecoveredStageActions, SupervisorError> {
    let prepared_digest = evidence
        .prepared
        .as_ref()
        .map(|foundation| staged_start::prepared_foundation_digest(foundation));
    if let Some(foundation) = evidence.prepared.as_ref() {
        if foundation.start_contract_digest != start_contract_digest {
            return Err(SupervisorError::new(
                "authority_native_recovered_start_contract_receipt_mismatch",
            ));
        }
        validate_native_prepared_foundation(policy, prepared, policy_snapshot, foundation)?;
    }

    let bridge_created_digest = match evidence.bridge_created.as_ref() {
        Some(created) => {
            let foundation = evidence.prepared.as_ref().ok_or_else(|| {
                SupervisorError::new("authority_native_recovered_stage_action_evidence_invalid")
            })?;
            staged_start::validate_created_root(
                policy,
                foundation,
                created,
                ProcessRole::BridgeLauncher,
            )?;
            Some(staged_start::created_root_digest(created))
        }
        None => None,
    };
    let bridge_resumed_digest = match evidence.bridge_resumed.as_ref() {
        Some(resumed) => {
            let foundation = evidence.prepared.as_ref().ok_or_else(|| {
                SupervisorError::new("authority_native_recovered_stage_action_evidence_invalid")
            })?;
            let created = evidence.bridge_created.as_ref().ok_or_else(|| {
                SupervisorError::new("authority_native_recovered_stage_action_evidence_invalid")
            })?;
            validate_resumed_root(
                policy,
                foundation,
                &created.suspended,
                &created.membership,
                resumed,
            )?;
            Some(staged_start::resumed_root_digest(
                ProcessRole::BridgeLauncher,
                resumed,
            ))
        }
        None => None,
    };
    let driver_created_digest = match evidence.driver_created.as_ref() {
        Some(created) => {
            let foundation = evidence.prepared.as_ref().ok_or_else(|| {
                SupervisorError::new("authority_native_recovered_stage_action_evidence_invalid")
            })?;
            let bridge_resumed = evidence.bridge_resumed.as_ref().ok_or_else(|| {
                SupervisorError::new("authority_native_recovered_stage_action_evidence_invalid")
            })?;
            staged_start::validate_created_root(policy, foundation, created, ProcessRole::Driver)?;
            if created.suspended.created_suspended_at <= bridge_resumed.resumed_at {
                return Err(SupervisorError::new(
                    "authority_native_recovered_stage_action_evidence_invalid",
                ));
            }
            Some(staged_start::created_root_digest(created))
        }
        None => None,
    };
    let driver_resumed_digest = match evidence.driver_resumed.as_ref() {
        Some(resumed) => {
            let foundation = evidence.prepared.as_ref().ok_or_else(|| {
                SupervisorError::new("authority_native_recovered_stage_action_evidence_invalid")
            })?;
            let created = evidence.driver_created.as_ref().ok_or_else(|| {
                SupervisorError::new("authority_native_recovered_stage_action_evidence_invalid")
            })?;
            validate_resumed_root(
                policy,
                foundation,
                &created.suspended,
                &created.membership,
                resumed,
            )?;
            Some(staged_start::resumed_root_digest(
                ProcessRole::Driver,
                resumed,
            ))
        }
        None => None,
    };

    let mut admission = None;
    let mut recovered_armed = None;
    let armed_digest = match evidence.armed_admission.as_ref() {
        Some(armed_admission) => {
            let armed = armed.ok_or_else(|| {
                SupervisorError::new("authority_native_recovered_stage_armed_mismatch")
            })?;
            let foundation = evidence.prepared.as_ref().ok_or_else(|| {
                SupervisorError::new("authority_native_recovered_stage_action_evidence_invalid")
            })?;
            let bridge_created = evidence.bridge_created.as_ref().ok_or_else(|| {
                SupervisorError::new("authority_native_recovered_stage_action_evidence_invalid")
            })?;
            let bridge_resumed = evidence.bridge_resumed.as_ref().ok_or_else(|| {
                SupervisorError::new("authority_native_recovered_stage_action_evidence_invalid")
            })?;
            let driver_created = evidence.driver_created.as_ref().ok_or_else(|| {
                SupervisorError::new("authority_native_recovered_stage_action_evidence_invalid")
            })?;
            let driver_resumed = evidence.driver_resumed.as_ref().ok_or_else(|| {
                SupervisorError::new("authority_native_recovered_stage_action_evidence_invalid")
            })?;
            let native_prepared = NativePreparedEvidence {
                foundation: foundation.clone(),
                bridge_root: AtomicRootLaunchReceipt {
                    suspended: bridge_created.suspended.clone(),
                    membership: bridge_created.membership.clone(),
                    resumed: bridge_resumed.clone(),
                },
            };
            let driver_root = AtomicRootLaunchReceipt {
                suspended: driver_created.suspended.clone(),
                membership: driver_created.membership.clone(),
                resumed: driver_resumed.clone(),
            };
            validate_native_prepared(policy, prepared, policy_snapshot, &native_prepared)?;
            validate_atomic_root_launch(
                policy,
                &native_prepared.foundation,
                &driver_root,
                ProcessRole::Driver,
            )?;
            let launch = root_launch_observation(ProcessRole::Driver, driver_resumed);
            let root_process = process_from_armed_root(policy, &driver_created.suspended, &launch);
            let expected_armed =
                ArmedRecoveryReceipt::from_armed_launch(policy, prepared, &root_process, &launch);
            if &expected_armed != armed {
                return Err(SupervisorError::new(
                    "authority_native_recovered_stage_armed_mismatch",
                ));
            }
            admission = Some(build_admission_binding(
                prepared,
                armed,
                policy_snapshot,
                &native_prepared.admission,
                armed_admission,
            )?);
            recovered_armed = Some(ValidatedRecoveredArmedStage {
                native_prepared,
                driver_root,
                launch,
            });
            Some(Sha256::digest(armed.encode()).into())
        }
        None => None,
    };

    for (action, expected_digest) in [
        (stage_journal::StageAction::Prepare, prepared_digest),
        (
            stage_journal::StageAction::BridgeCreate,
            bridge_created_digest,
        ),
        (
            stage_journal::StageAction::BridgeResume,
            bridge_resumed_digest,
        ),
        (
            stage_journal::StageAction::DriverCreate,
            driver_created_digest,
        ),
        (
            stage_journal::StageAction::DriverResume,
            driver_resumed_digest,
        ),
        (stage_journal::StageAction::Arm, armed_digest),
    ] {
        let replayed_digest = replay
            .action_commitment(action)
            .and_then(|commitment| commitment.observed_payload_digest());
        if replayed_digest != expected_digest {
            return Err(SupervisorError::new(
                "authority_native_recovered_stage_action_payload_mismatch",
            ));
        }
    }
    let supplied_armed_digest = armed.map(|receipt| Sha256::digest(receipt.encode()).into());
    if replay.armed_receipt_digest() != supplied_armed_digest
        || replay.armed_receipt_digest() != armed_digest
        || admission.is_some() != armed.is_some()
    {
        return Err(SupervisorError::new(
            "authority_native_recovered_stage_armed_mismatch",
        ));
    }
    Ok(ValidatedRecoveredStageActions {
        admission,
        armed: recovered_armed,
    })
}

fn validate_recovered_normal_cleanup(native: &NativeCleanupReceipt) -> Result<(), SupervisorError> {
    if !native.private_pipe_closed
        || !native.pipe_challenge_zeroed
        || !native.no_pending_pipe_clients
        || !native.pipe_replay_rejected
        || !native.bridge_control_pipe_closed
        || !native.bridge_control_pipe_challenge_zeroed
        || !native.bridge_control_pipe_no_pending_clients
        || !native.bridge_control_pipe_replay_rejected
        || !native.all_candidate_listener_duplicates_closed
        || !native.all_service_listener_handles_closed
        || !native.completion_port_drained
        || !native.no_inheritable_handle_residue
        || !native.no_port_drift
        || !native.bridge_proxy_listener_closed
        || !native.bridge_proxy_connections_closed
        || !native.bridge_target_listener_closed
        || !native.bridge_request_auth_credentials_zeroized
        || !native.containment_readback_complete
    {
        return Err(SupervisorError::new(
            "authority_native_recovered_normal_cleanup_invalid",
        ));
    }
    Ok(())
}

#[derive(Debug, Default)]
pub(crate) struct WindowsNativeSupervisorApi;

impl ServiceOwnedNativeApi for WindowsNativeSupervisorApi {
    fn preflight(
        &mut self,
        _policy: &SupervisorPolicy,
    ) -> Result<NativeCapabilityReceipt, SupervisorError> {
        // The current backend binds a new listener itself. Starting any candidate before it can
        // adopt the authority-created socket would create an unprovable ownership gap.
        Err(SupervisorError::new(BACKEND_LISTENER_ADOPTION_BLOCKER))
    }

    #[cfg(test)]
    fn prepare(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &PreparedRecoveryReceipt,
        _policy_snapshot: &[u8],
    ) -> Result<NativePreparedEvidence, SupervisorError> {
        Err(SupervisorError::new(
            "authority_native_supervisor_preflight_required",
        ))
    }

    #[cfg(test)]
    fn launch_root_suspended(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &NativePreparedEvidence,
    ) -> Result<SuspendedRootReceipt, SupervisorError> {
        Err(SupervisorError::new(
            "authority_native_supervisor_preflight_required",
        ))
    }

    #[cfg(test)]
    fn assign_root_to_job(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &NativePreparedEvidence,
        _suspended: &SuspendedRootReceipt,
    ) -> Result<JobAssignmentReceipt, SupervisorError> {
        Err(SupervisorError::new(
            "authority_native_supervisor_preflight_required",
        ))
    }

    #[cfg(test)]
    fn resume_root(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &NativePreparedEvidence,
        _suspended: &SuspendedRootReceipt,
        _assignment: &JobAssignmentReceipt,
    ) -> Result<ResumedRootReceipt, SupervisorError> {
        Err(SupervisorError::new(
            "authority_native_supervisor_preflight_required",
        ))
    }

    fn poll_terminal(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &NativePreparedEvidence,
        _armed: &ArmedRecoveryReceipt,
    ) -> Result<NativeTerminalPoll, SupervisorError> {
        Err(SupervisorError::new(
            "authority_native_supervisor_preflight_required",
        ))
    }

    fn bind_admission_after_arm(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &NativePreparedEvidence,
        _armed: &ArmedRecoveryReceipt,
        _policy_snapshot: &[u8],
    ) -> Result<NativeArmedAdmissionReceipt, SupervisorError> {
        Err(SupervisorError::new(
            "authority_native_supervisor_preflight_required",
        ))
    }

    fn request_armed_termination(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &NativePreparedEvidence,
        _armed: &ArmedRecoveryReceipt,
    ) -> Result<NativeArmedTerminationAttempt, SupervisorError> {
        Err(SupervisorError::new(
            "authority_native_termination_intent_not_connected",
        ))
    }

    fn contain_terminal(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &NativePreparedEvidence,
        _terminal: &NativeTerminalEvidence,
    ) -> Result<NativeCleanupEvidence, SupervisorError> {
        Err(SupervisorError::new(
            "authority_native_supervisor_preflight_required",
        ))
    }

    fn contain_after_failure(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &PreparedRecoveryReceipt,
        _armed: Option<&ArmedRecoveryReceipt>,
        _phase: NativeSupervisorPhase,
        _reason: BurnReason,
        _failure_code: &'static str,
    ) -> Result<NativeAbortEvidence, SupervisorError> {
        Err(SupervisorError::new(
            "authority_native_supervisor_preflight_required",
        ))
    }

    fn recover_after_restart(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &PreparedRecoveryReceipt,
        _armed: Option<&ArmedRecoveryReceipt>,
        _policy_snapshot: &[u8],
        _reason: BurnReason,
    ) -> Result<NativeRestartRecoveryEvidence, SupervisorError> {
        Err(SupervisorError::new(
            "authority_native_supervisor_preflight_required",
        ))
    }

    fn seal_origin_after_cleanup(
        &mut self,
        _policy: &SupervisorPolicy,
        _prepared: &NativePreparedEvidence,
        _terminal: &NativeTerminalEvidence,
        _cleanup: &NativeCleanupEvidence,
        _completed: &CompletedRunProof,
        _admission: &NativeAdmissionBinding,
    ) -> Result<NativeOriginEnvelopeReceipt, SupervisorError> {
        Err(SupervisorError::new(
            "authority_native_supervisor_preflight_required",
        ))
    }
}

fn termination_intent_receipt_digest(receipt: &NativeTerminationIntentReceipt) -> Digest {
    let mut digest = Sha256::new();
    digest.update(TERMINATION_INTENT_RECEIPT_DOMAIN);
    for value in [
        receipt.authority_identity_digest,
        receipt.ticket_digest,
        receipt.run_binding_digest,
        receipt.prepared_receipt_digest,
        receipt.armed_receipt_digest,
        receipt.policy_snapshot_digest,
        receipt.previous_record_digest,
    ] {
        digest.update(value);
    }
    digest.update([receipt.kind.digest_tag()]);
    digest.update(receipt.requested_at_unix_ms.to_be_bytes());
    digest.update(receipt.recorded_at_unix_ms.to_be_bytes());
    digest.update(receipt.journal_sequence.to_be_bytes());
    digest.update([
        receipt.append_flushed as u8,
        receipt.readback_verified as u8,
        receipt.service_owned_sealed_journal as u8,
    ]);
    digest.finalize().into()
}

fn validate_native_termination_intent(
    policy: &SupervisorPolicy,
    prepared: &PreparedRecoveryReceipt,
    armed: &ArmedRecoveryReceipt,
    policy_snapshot: &[u8],
    kind: NativeTerminationKind,
    receipt: &NativeTerminationIntentReceipt,
) -> Result<(), SupervisorError> {
    let armed_receipt_digest: Digest = Sha256::digest(armed.encode()).into();
    let policy_snapshot_digest: Digest = Sha256::digest(policy_snapshot).into();
    let timing_valid = staged_start::validate_stage_termination_values(
        policy,
        kind,
        receipt.requested_at_unix_ms,
        receipt.recorded_at_unix_ms,
    )
    .is_ok()
        && receipt.recorded_at_unix_ms / 1_000 >= armed.resumed_at;
    if receipt.authority_identity_digest != policy.authority_identity_digest
        || receipt.ticket_digest != policy.ticket_digest
        || receipt.run_binding_digest != policy.run_binding_digest
        || receipt.prepared_receipt_digest != prepared.digest()
        || receipt.armed_receipt_digest != armed_receipt_digest
        || receipt.policy_snapshot_digest != policy_snapshot_digest
        || receipt.kind != kind
        || !timing_valid
        || receipt.journal_sequence == 0
        || is_zero_digest(&receipt.previous_record_digest)
        || is_zero_digest(&receipt.record_digest)
        || receipt.record_digest != termination_intent_receipt_digest(receipt)
        || !receipt.append_flushed
        || !receipt.readback_verified
        || !receipt.service_owned_sealed_journal
    {
        return Err(SupervisorError::new(
            "authority_native_termination_intent_invalid",
        ));
    }
    Ok(())
}

fn validate_native_terminal_after_intent(
    terminal: &NativeTerminalEvidence,
    intent: Option<&NativeTerminationIntentReceipt>,
) -> Result<(), SupervisorError> {
    match (intent.map(|receipt| receipt.kind), terminal.terminal.kind) {
        (None, TerminalKind::Completed | TerminalKind::Failed) => Ok(()),
        (Some(NativeTerminationKind::Cancelled), TerminalKind::Cancelled)
        | (Some(NativeTerminationKind::TimedOut), TerminalKind::TimedOut) => {
            let Some(receipt) = intent else {
                return Err(SupervisorError::new(
                    "authority_native_termination_terminal_invalid",
                ));
            };
            if !seconds_observation_not_before_millis(
                terminal.terminal.observed_at,
                receipt.recorded_at_unix_ms,
            ) {
                Err(SupervisorError::new(
                    "authority_native_termination_terminal_invalid",
                ))
            } else {
                Ok(())
            }
        }
        _ => Err(SupervisorError::new(
            "authority_native_termination_terminal_invalid",
        )),
    }
}

fn seconds_observation_not_before_millis(
    observed_at_unix_seconds: u64,
    recorded_at_unix_ms: u64,
) -> bool {
    observed_at_unix_seconds >= recorded_at_unix_ms / 1_000
}

fn validate_capabilities(receipt: &NativeCapabilityReceipt) -> Result<(), SupervisorError> {
    let checks = [
        (
            receipt.fresh_service_generation_attestation
                && !receipt.caller_attestation_envelope_present,
            "authority_generation_attestation_not_connected",
        ),
        (
            receipt.dedicated_restricted_runner,
            "authority_native_runner_not_connected",
        ),
        (
            receipt.service_owned_profile_acl,
            "authority_native_profile_acl_not_connected",
        ),
        (
            receipt.stable_private_artifacts,
            "authority_native_artifacts_not_connected",
        ),
        (
            receipt.suspended_handle_list_launch,
            "authority_native_suspended_launch_not_connected",
        ),
        (
            receipt.kill_on_close_job,
            "authority_native_job_not_connected",
        ),
        (
            receipt.completion_port_roster,
            "authority_native_roster_not_connected",
        ),
        (
            receipt.service_owned_listener_adoption,
            BACKEND_LISTENER_ADOPTION_BLOCKER,
        ),
        (
            receipt.service_owned_bridge_proxy,
            BRIDGE_TARGET_LISTENER_ADOPTION_BLOCKER,
        ),
        (
            receipt.bridge_target_in_memory_startup,
            BRIDGE_TARGET_IN_MEMORY_STARTUP_BLOCKER,
        ),
        (
            receipt.bridge_target_request_auth,
            BRIDGE_TARGET_REQUEST_AUTH_BLOCKER,
        ),
        (
            receipt.one_use_private_backend_pipe,
            "authority_native_private_pipe_not_connected",
        ),
        (
            receipt.service_direct_http,
            "authority_native_http_not_connected",
        ),
        (
            receipt.held_handle_finalization,
            "authority_native_finalization_not_connected",
        ),
        (
            receipt.residue_readback,
            "authority_native_cleanup_not_connected",
        ),
    ];
    checks
        .into_iter()
        .find(|(ready, _)| !ready)
        .map_or(Ok(()), |(_, code)| Err(SupervisorError::new(code)))
}

fn validate_native_prepared(
    policy: &SupervisorPolicy,
    prepared: &PreparedRecoveryReceipt,
    policy_snapshot: &[u8],
    native: &NativePreparedEvidence,
) -> Result<(), SupervisorError> {
    validate_native_prepared_foundation(policy, prepared, policy_snapshot, &native.foundation)?;
    validate_atomic_root_launch(
        policy,
        &native.foundation,
        &native.bridge_root,
        ProcessRole::BridgeLauncher,
    )
}

fn validate_native_prepared_foundation(
    policy: &SupervisorPolicy,
    prepared: &PreparedRecoveryReceipt,
    policy_snapshot: &[u8],
    native: &NativePreparedFoundation,
) -> Result<(), SupervisorError> {
    if is_zero_digest(&native.start_contract_digest)
        || native.ticket_consumed_at < policy.issued_at
        || native.ticket_consumed_at >= policy.deadline
    {
        return Err(SupervisorError::new(
            "authority_native_ticket_consume_time_invalid",
        ));
    }
    validate_runner_observation(policy, native.ticket_consumed_at, &native.runner)?;
    validate_native_artifacts(policy, native)?;
    validate_private_pipe_lease(policy, native)?;
    validate_bridge_control_pipe_lease(policy, native)?;
    validate_listener_leases(policy, native)?;
    validate_native_job(policy, native)?;

    let expected_snapshot_digest: Digest = Sha256::digest(policy_snapshot).into();
    if native.admission.prepared_receipt_digest != prepared.digest()
        || native.admission.policy_snapshot_digest != expected_snapshot_digest
        || is_zero_digest(&native.admission.recovery_bundle_digest)
        || !native.admission.read_from_authority_store
        || !native.admission.sealed_by_service
    {
        return Err(SupervisorError::new(
            "authority_native_admission_receipt_invalid",
        ));
    }
    Ok(())
}

fn validate_native_artifacts(
    policy: &SupervisorPolicy,
    native: &NativePreparedFoundation,
) -> Result<(), SupervisorError> {
    if native.artifacts.len() != policy.artifacts.len() {
        return Err(SupervisorError::new(
            "authority_native_artifact_set_mismatch",
        ));
    }
    let mut service_handles = BTreeSet::new();
    let mut candidate_handles = BTreeSet::new();
    let mut private_files = BTreeSet::new();
    for (expected, actual) in policy.artifacts.iter().zip(&native.artifacts) {
        if actual.binding_digest != expected.binding_digest
            || actual.direction != expected.direction
            || actual.created_at < native.ticket_consumed_at
            || actual.created_at >= policy.deadline
            || actual.service_handle_id == 0
            || actual.candidate_handle_id == 0
            || !service_handles.insert(actual.service_handle_id)
            || !candidate_handles.insert(actual.candidate_handle_id)
            || !private_files.insert(actual.private_identity)
            || is_zero_digest(&actual.content_digest)
            || !actual.created_new_private_copy
            || !actual.service_owned_parent
            || actual.parent_is_reparse_point
            || !actual.candidate_handle_explicitly_inherited
            || !actual.service_handle_held_through_terminal
            || !actual.content_digest_read_from_service_handle
            || actual.private_identity != actual.candidate_handle_identity
            || actual.private_identity != actual.path_identity_at_terminal
        {
            return Err(SupervisorError::new(
                "authority_native_private_artifact_invalid",
            ));
        }
        match actual.direction {
            ArtifactDirection::Input => {
                if actual.source_identity.is_none()
                    || actual.source_identity == Some(actual.private_identity)
                    || expected.expected_content_digest != Some(actual.content_digest)
                    || actual.content_length == 0
                {
                    return Err(SupervisorError::new("authority_native_input_copy_invalid"));
                }
            }
            ArtifactDirection::Output => {
                if actual.source_identity.is_some() {
                    return Err(SupervisorError::new("authority_native_output_copy_invalid"));
                }
            }
        }
    }
    Ok(())
}

fn validate_private_pipe_lease(
    policy: &SupervisorPolicy,
    native: &NativePreparedFoundation,
) -> Result<(), SupervisorError> {
    let pipe = &native.pipe;
    if pipe.instance_id == 0
        || pipe.binding_digest != derive_private_pipe_binding(policy, pipe.instance_id)
        || is_zero_digest(&pipe.challenge_digest)
        || is_zero_digest(&pipe.inner_live_bootstrap_digest)
        || pipe.created_at < native.ticket_consumed_at
        || pipe.created_at >= policy.deadline
        || !pipe.created_new
        || !pipe.one_use
        || !pipe.service_owned
        || !pipe.restricted_acl
        || !pipe.service_handle_held
        || pipe.material_exposed_to_argv
        || pipe.material_exposed_to_environment
        || pipe.material_exposed_to_report
        || pipe.material_exposed_to_log
    {
        return Err(SupervisorError::new(
            "authority_native_private_pipe_invalid",
        ));
    }
    Ok(())
}

fn validate_bridge_control_pipe_lease(
    policy: &SupervisorPolicy,
    native: &NativePreparedFoundation,
) -> Result<(), SupervisorError> {
    let pipe = &native.bridge_control_pipe;
    if pipe.instance_id == 0
        || pipe.instance_id == native.pipe.instance_id
        || pipe.binding_digest != derive_bridge_control_pipe_binding(policy, pipe.instance_id)
        || pipe.binding_digest == native.pipe.binding_digest
        || pipe.challenge_digest == native.pipe.challenge_digest
        || is_zero_digest(&pipe.challenge_digest)
        || pipe.created_at < native.ticket_consumed_at
        || pipe.created_at >= policy.deadline
        || !pipe.created_new
        || !pipe.one_connection
        || !pipe.service_owned
        || !pipe.restricted_acl
        || !pipe.service_handle_held_through_shutdown
        || !pipe.restricted_service_handle_in_launch_allowlist
        || pipe.material_exposed_to_argv
        || pipe.material_exposed_to_environment
        || pipe.material_exposed_to_report
        || pipe.material_exposed_to_log
    {
        return Err(SupervisorError::new(
            "authority_native_bridge_control_pipe_invalid",
        ));
    }
    Ok(())
}

fn validate_listener_leases(
    policy: &SupervisorPolicy,
    native: &NativePreparedFoundation,
) -> Result<(), SupervisorError> {
    if native.listeners.len() != policy.socket_policies.len() {
        return Err(SupervisorError::new(
            "authority_native_listener_set_mismatch",
        ));
    }
    let mut socket_ids = BTreeSet::new();
    for (expected, actual) in policy.socket_policies.iter().zip(&native.listeners) {
        if actual.role != expected.role
            || actual.local_port != expected.local_port
            || actual.listener_socket_object_id == 0
            || !socket_ids.insert(actual.listener_socket_object_id)
            || actual.created_at < native.ticket_consumed_at
            || actual.created_at >= policy.deadline
            || !actual.loopback_v4_only
            || !actual.exclusive_address_use
            || !actual.address_reuse_disabled
            || !actual.service_created
            || !actual.service_handle_held_until_adoption
            || is_zero_digest(&actual.share_material_digest)
            || actual.share_material_exposed_to_argv
            || actual.share_material_exposed_to_environment
            || actual.share_material_exposed_to_report
            || actual.share_material_exposed_to_log
        {
            return Err(SupervisorError::new(
                "authority_native_listener_lease_invalid",
            ));
        }
    }
    let bridge_target = native
        .listeners
        .iter()
        .find(|listener| listener.role == SocketRole::Bridge)
        .ok_or_else(|| SupervisorError::new("authority_native_bridge_target_missing"))?;
    let proxy = &native.bridge_proxy;
    if proxy.public_listener_socket_object_id == 0
        || proxy.public_listener_socket_object_id == bridge_target.listener_socket_object_id
        || socket_ids.contains(&proxy.public_listener_socket_object_id)
        || proxy.public_port != BRIDGE_LOOPBACK_PORT
        || proxy.target_listener_socket_object_id != bridge_target.listener_socket_object_id
        || proxy.target_port != bridge_target.local_port
        || proxy.created_at < native.ticket_consumed_at
        || proxy.created_at >= policy.deadline
        || !proxy.service_owns_public_listener
        || !proxy.public_listener_never_transferred
        || !proxy.loopback_v4_only
        || !proxy.exclusive_address_use
        || !proxy.address_reuse_disabled
        || !proxy.service_handle_held_through_cleanup_begin
        || is_zero_digest(&proxy.request_auth_key_digest)
        || !proxy.request_auth_material_held_in_memory
        || proxy.request_auth_exposed_to_argv
        || proxy.request_auth_exposed_to_environment
        || proxy.request_auth_exposed_to_report
        || proxy.request_auth_exposed_to_log
    {
        return Err(SupervisorError::new(
            "authority_native_bridge_proxy_lease_invalid",
        ));
    }
    Ok(())
}

fn validate_native_job(
    policy: &SupervisorPolicy,
    native: &NativePreparedFoundation,
) -> Result<(), SupervisorError> {
    let job = &native.job;
    if job.object_id != policy.job_object_id
        || job.deterministic_name_digest != policy.deterministic_job_name_digest
        || job.security_binding_digest != policy.job_security_binding_digest
        || is_zero_digest(&job.security_binding_digest)
        || !job.exact_security_readback
        || !job.owner_local_system
        || !job.dacl_present
        || job.dacl_defaulted
        || !job.dacl_protected
        || job.dacl_ace_count != 2
        || job.system_access_mask != native_job::JOB_OBJECT_ALL_ACCESS_EXACT
        || job.service_access_mask != native_job::SERVICE_JOB_ACCESS_EXACT
        || job.created_at < native.ticket_consumed_at
        || job.created_at >= policy.deadline
        || !job.kill_on_job_close
        || job.breakaway_allowed
        || job.silent_breakaway_allowed
        || job.active_process_limit != 0
        || !job.completion_port_attached
        || !job.service_handle_held
    {
        return Err(SupervisorError::new("authority_native_job_invalid"));
    }
    Ok(())
}

fn validate_suspended_root(
    policy: &SupervisorPolicy,
    native: &NativePreparedEvidence,
    suspended: &SuspendedRootReceipt,
) -> Result<(), SupervisorError> {
    validate_suspended_root_for_role(policy, native, suspended, ProcessRole::Driver)
}

fn validate_suspended_root_for_role(
    policy: &SupervisorPolicy,
    native: &NativePreparedFoundation,
    suspended: &SuspendedRootReceipt,
    expected_role: ProcessRole,
) -> Result<(), SupervisorError> {
    if suspended.role != expected_role
        || suspended.process.pid == 0
        || suspended.process.creation_time == 0
        || suspended.parent != policy.authority_process
        || suspended.executable_digest
            != policy.process_executable_digests[role_index(expected_role)]
        || suspended.image_identity.volume_serial == 0
        || suspended
            .image_identity
            .file_id
            .iter()
            .all(|byte| *byte == 0)
        || suspended.runner_identity_digest != policy.runner_identity_digest
        || suspended.child_transport_contract_digest != policy.child_transport_contract_digest
        || suspended.raw_handle_list.role()
            != suspended
                .role
                .child_bootstrap_role()
                .ok_or_else(|| SupervisorError::new("authority_native_suspended_root_invalid"))?
        || suspended.raw_handle_list.as_bytes() == &policy.child_transport_contract_digest
        || suspended.created_suspended_at < native.job.created_at
        || suspended.created_suspended_at >= policy.deadline
        || !suspended.job_list_attribute_applied
        || !suspended.job_assigned_at_creation
        || !suspended.job_membership_readback_before_return
        || !suspended.process_handle_held
        || !suspended.image_handle_held
        || !suspended.all_other_handles_non_inheritable
        || suspended.breakaway_requested
    {
        return Err(SupervisorError::new(
            "authority_native_suspended_root_invalid",
        ));
    }
    Ok(())
}

fn validate_atomic_root_launch(
    policy: &SupervisorPolicy,
    native: &NativePreparedFoundation,
    root: &AtomicRootLaunchReceipt,
    expected_role: ProcessRole,
) -> Result<(), SupervisorError> {
    validate_suspended_root_for_role(policy, native, &root.suspended, expected_role)?;
    validate_job_assignment(policy, native, &root.suspended, &root.membership)?;
    validate_resumed_root(
        policy,
        native,
        &root.suspended,
        &root.membership,
        &root.resumed,
    )
}

fn validate_job_assignment(
    policy: &SupervisorPolicy,
    _native: &NativePreparedFoundation,
    suspended: &SuspendedRootReceipt,
    assignment: &JobAssignmentReceipt,
) -> Result<(), SupervisorError> {
    if assignment.process != suspended.process
        || assignment.job_object_id != policy.job_object_id
        || assignment.membership_verified_at <= suspended.created_suspended_at
        || assignment.membership_verified_at >= policy.deadline
        || assignment.initial_assignment_call_performed
        || !assignment.job_membership_revalidated
        || !assignment.membership_readback_before_resume
        || !assignment.assigned_using_process_and_job_handles
        || !assignment.process_confirmed_job_member
        || !assignment.completion_port_assignment_observed
    {
        return Err(SupervisorError::new(
            "authority_native_job_assignment_invalid",
        ));
    }
    Ok(())
}

fn validate_resumed_root(
    policy: &SupervisorPolicy,
    native: &NativePreparedFoundation,
    suspended: &SuspendedRootReceipt,
    assignment: &JobAssignmentReceipt,
    resumed: &ResumedRootReceipt,
) -> Result<(), SupervisorError> {
    if resumed.process != suspended.process
        || resumed.start_contract_digest != native.start_contract_digest
        || resumed.created_suspended_at != suspended.created_suspended_at
        || resumed.job_membership_verified_at != assignment.membership_verified_at
        || resumed.resumed_at <= assignment.membership_verified_at
        || resumed.resumed_at >= policy.deadline
        || resumed.job_object_id != policy.job_object_id
        || resumed.runner_identity_digest != policy.runner_identity_digest
        || resumed.child_transport_contract_digest != policy.child_transport_contract_digest
        || resumed.raw_handle_list != suspended.raw_handle_list
        || resumed.raw_handle_list.role()
            != suspended
                .role
                .child_bootstrap_role()
                .ok_or_else(|| SupervisorError::new("authority_native_resume_sequence_invalid"))?
        || resumed.raw_handle_list.as_bytes() == &policy.child_transport_contract_digest
        || !resumed.all_other_handles_non_inheritable
        || resumed.breakaway_requested
        || native
            .artifacts
            .iter()
            .any(|artifact| artifact.created_at >= resumed.created_suspended_at)
        || native
            .listeners
            .iter()
            .any(|listener| listener.created_at >= resumed.created_suspended_at)
        || native.bridge_proxy.created_at >= resumed.created_suspended_at
    {
        return Err(SupervisorError::new(
            "authority_native_resume_sequence_invalid",
        ));
    }
    Ok(())
}

fn process_from_armed_root(
    policy: &SupervisorPolicy,
    suspended: &SuspendedRootReceipt,
    launch: &RootLaunchObservation,
) -> ProcessObservation {
    ProcessObservation {
        role: ProcessRole::Driver,
        key: suspended.process,
        parent_pid: policy.authority_process.pid,
        parent_creation_time: policy.authority_process.creation_time,
        supervisor_pid: policy.authority_process.pid,
        started_at: launch.resumed_at,
        executable_digest: suspended.executable_digest,
        executable_digest_read_from_image_handle: true,
        image_handle_identity: suspended.image_identity,
        image_path_identity_at_terminal: suspended.image_identity,
        runner_identity_digest: Some(policy.runner_identity_digest),
        job_object_id: Some(policy.job_object_id),
        job_member: true,
        breakaway_allowed: false,
        image_handle_held_through_terminal: true,
        process_handle_held_through_cleanup_begin: true,
        alive_at_finalization: true,
    }
}

fn root_launch_observation(
    role: ProcessRole,
    resumed: &ResumedRootReceipt,
) -> RootLaunchObservation {
    RootLaunchObservation {
        role,
        created_suspended_at: resumed.created_suspended_at,
        job_membership_verified_at: resumed.job_membership_verified_at,
        resumed_at: resumed.resumed_at,
        job_object_id: resumed.job_object_id,
        runner_identity_digest: resumed.runner_identity_digest,
        child_transport_contract_digest: resumed.child_transport_contract_digest,
        raw_handle_list: resumed.raw_handle_list,
        all_other_handles_non_inheritable: resumed.all_other_handles_non_inheritable,
        breakaway_requested: resumed.breakaway_requested,
    }
}

fn validate_native_terminal(
    policy: &SupervisorPolicy,
    prepared: &NativePreparedEvidence,
    suspended: &SuspendedRootReceipt,
    launch: &RootLaunchObservation,
    terminal: &NativeTerminalEvidence,
) -> Result<(), SupervisorError> {
    if terminal.processes.len() != PROCESS_ROLES.len() {
        return Err(SupervisorError::new(
            "authority_native_process_roster_incomplete",
        ));
    }
    let driver = &terminal.processes[role_index(ProcessRole::Driver)];
    let bridge_launcher = &terminal.processes[role_index(ProcessRole::BridgeLauncher)];
    let backend = &terminal.processes[role_index(ProcessRole::Backend)];
    let expected_bridge_launch =
        root_launch_observation(ProcessRole::BridgeLauncher, &prepared.bridge_root.resumed);
    if driver.role != ProcessRole::Driver
        || launch.child_transport_contract_digest != suspended.child_transport_contract_digest
        || launch.raw_handle_list != suspended.raw_handle_list
        || driver.key != suspended.process
        || driver.executable_digest != suspended.executable_digest
        || driver.image_handle_identity != suspended.image_identity
        || driver.started_at < launch.resumed_at
        || backend.role != ProcessRole::Backend
        || terminal.bridge_root_launch != expected_bridge_launch
        || terminal.bridge_root_launch.resumed_at >= launch.created_suspended_at
        || bridge_launcher.role != ProcessRole::BridgeLauncher
        || bridge_launcher.key != prepared.bridge_root.suspended.process
        || bridge_launcher.executable_digest != prepared.bridge_root.suspended.executable_digest
        || bridge_launcher.image_handle_identity != prepared.bridge_root.suspended.image_identity
        || bridge_launcher.started_at < terminal.bridge_root_launch.resumed_at
    {
        return Err(SupervisorError::new(
            "authority_native_root_or_backend_identity_drift",
        ));
    }
    validate_private_pipe_ack(policy, prepared, backend, &terminal.pipe_ack)?;
    validate_listener_adoptions(policy, prepared, terminal, backend)?;
    validate_bridge_control_pipe(policy, prepared, terminal)?;
    validate_bridge_proxy(policy, prepared, terminal)?;
    validate_http_lifecycle(policy, prepared, terminal, backend)?;

    if terminal.terminal.kind == TerminalKind::Completed {
        let finalization = terminal
            .finalization
            .as_ref()
            .ok_or_else(|| SupervisorError::new("authority_finalization_missing"))?;
        if finalization.source != FinalizationSource::AuthorityHeldOutputHandles
            || finalization.caller_report_present
            || !finalization.read_directly_from_held_handles
            || finalization.finalized_at != terminal.http_lifecycle.finalized_at
        {
            return Err(SupervisorError::new(
                "authority_native_finalization_source_invalid",
            ));
        }
    } else if terminal.finalization.is_some() {
        return Err(SupervisorError::new(
            "authority_native_burned_finalization_present",
        ));
    }
    Ok(())
}

fn validate_origin_envelope(
    policy: &SupervisorPolicy,
    completed: &CompletedRunProof,
    admission: &NativeAdmissionBinding,
    origin: &NativeOriginEnvelopeReceipt,
) -> Result<(), SupervisorError> {
    let computed_digest: Digest = Sha256::digest(&origin.canonical_bytes).into();
    let parsed = parse_native_origin_ticket_binding(&origin.canonical_bytes)?;
    if origin.canonical_bytes.is_empty()
        || origin.canonical_bytes.len() > MAX_ORIGIN_ENVELOPE_BYTES
        || origin.canonical_digest != computed_digest
        || origin.origin_ticket_digest != parsed.origin_ticket_digest
        || origin.authority_ticket_digest != parsed.authority_ticket_digest
        || origin.authority_ticket_digest != policy.ticket_digest
        || origin.authority_ticket_digest != *completed.ticket_digest()
        || origin.cleanup_receipt_digest != parsed.cleanup_digest
        || origin.result_digest != *completed.result_digest()
        || origin.cleanup_receipt_digest != *completed.cleanup_receipt_digest()
        || origin.admission_binding_digest != *admission.binding_digest()
        || origin.cleanup_observed_at != completed.cleanup_observed_at()
        || origin.sealed_at <= origin.cleanup_observed_at
        || !origin.built_from_service_held_evidence
        || !origin.signed_by_service_after_cleanup
        || origin.caller_material_present
    {
        return Err(SupervisorError::new(
            "authority_native_origin_envelope_invalid",
        ));
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ParsedNativeOriginTicketBinding {
    origin_ticket_digest: Digest,
    authority_ticket_digest: Digest,
    cleanup_digest: Digest,
}

fn parse_native_origin_ticket_binding(
    canonical_bytes: &[u8],
) -> Result<ParsedNativeOriginTicketBinding, SupervisorError> {
    if canonical_bytes.is_empty() || canonical_bytes.len() > MAX_ORIGIN_ENVELOPE_BYTES {
        return Err(SupervisorError::new(
            "authority_native_origin_envelope_invalid",
        ));
    }
    let envelope: Value = serde_json::from_slice(canonical_bytes)
        .map_err(|_| SupervisorError::new("authority_native_origin_envelope_invalid"))?;
    if !envelope.is_object()
        || serde_json::to_vec(&envelope)
            .map_err(|_| SupervisorError::new("authority_native_origin_envelope_invalid"))?
            != canonical_bytes
        || envelope.get("schema").and_then(Value::as_str) != Some(ORIGIN_ENVELOPE_SCHEMA_V2)
    {
        return Err(SupervisorError::new(
            "authority_native_origin_envelope_invalid",
        ));
    }
    let ticket = envelope
        .get("ticket")
        .filter(|value| value.is_object())
        .ok_or_else(|| SupervisorError::new("authority_native_origin_envelope_invalid"))?;
    let ticket_bytes = serde_json::to_vec(ticket)
        .map_err(|_| SupervisorError::new("authority_native_origin_envelope_invalid"))?;
    let computed_origin_ticket_digest: Digest = Sha256::digest(&ticket_bytes).into();
    let origin_ticket_digest = native_origin_digest_field(&envelope, "ticketDigest")?;
    if origin_ticket_digest != computed_origin_ticket_digest {
        return Err(SupervisorError::new(
            "authority_native_origin_ticket_digest_mismatch",
        ));
    }
    Ok(ParsedNativeOriginTicketBinding {
        origin_ticket_digest,
        authority_ticket_digest: native_origin_digest_field(&envelope, "authorityTicketDigest")?,
        cleanup_digest: native_origin_digest_field(&envelope, "cleanupDigest")?,
    })
}

fn native_origin_digest_field(envelope: &Value, field: &str) -> Result<Digest, SupervisorError> {
    let value = envelope
        .get(field)
        .and_then(Value::as_str)
        .filter(|value| value.len() == 64)
        .ok_or_else(|| SupervisorError::new("authority_native_origin_envelope_invalid"))?;
    let mut digest = [0u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        let high = native_origin_hex_nibble(pair[0])
            .ok_or_else(|| SupervisorError::new("authority_native_origin_envelope_invalid"))?;
        let low = native_origin_hex_nibble(pair[1])
            .ok_or_else(|| SupervisorError::new("authority_native_origin_envelope_invalid"))?;
        digest[index] = (high << 4) | low;
    }
    if is_zero_digest(&digest) {
        return Err(SupervisorError::new(
            "authority_native_origin_envelope_invalid",
        ));
    }
    Ok(digest)
}

fn native_origin_hex_nibble(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        _ => None,
    }
}

fn validate_private_pipe_ack(
    policy: &SupervisorPolicy,
    prepared: &NativePreparedEvidence,
    backend: &ProcessObservation,
    ack: &PrivateBackendPipeAck,
) -> Result<(), SupervisorError> {
    if ack.instance_id != prepared.pipe.instance_id
        || ack.binding_digest != prepared.pipe.binding_digest
        || ack.challenge_digest != prepared.pipe.challenge_digest
        || ack.peer != backend.key
        || ack.peer_job_object_id != policy.job_object_id
        || ack.peer_executable_digest != backend.executable_digest
        || ack.peer_image_identity != backend.image_handle_identity
        || ack.accepted_at < backend.started_at
        || ack.accepted_at >= policy.deadline
        || ack.accepted_connections != 1
        || !ack.peer_verified_from_pipe_and_process_handles
        || ack.pid_table_only
        || !ack.replay_rejected
        || !ack.ack_read_from_service_pipe_handle
    {
        return Err(SupervisorError::new(
            "authority_native_private_pipe_ack_invalid",
        ));
    }
    Ok(())
}

fn validate_listener_adoptions(
    policy: &SupervisorPolicy,
    prepared: &NativePreparedEvidence,
    terminal: &NativeTerminalEvidence,
    backend: &ProcessObservation,
) -> Result<(), SupervisorError> {
    let expected_adoptions = policy
        .socket_policies
        .iter()
        .zip(&prepared.listeners)
        .filter(|(endpoint, _)| endpoint.role == SocketRole::App)
        .collect::<Vec<_>>();
    if terminal.listener_adoptions.len() != expected_adoptions.len() {
        return Err(SupervisorError::new(
            "authority_native_listener_adoption_set_mismatch",
        ));
    }
    for ((endpoint, lease), ack) in expected_adoptions
        .into_iter()
        .zip(&terminal.listener_adoptions)
    {
        let expected_owner = &terminal.processes[role_index(endpoint.owner_role)];
        if ack.role != endpoint.role
            || ack.local_port != endpoint.local_port
            || ack.listener_socket_object_id != lease.listener_socket_object_id
            || ack.owner != expected_owner.key
            || ack.owner_job_object_id != policy.job_object_id
            || ack.owner_executable_digest != expected_owner.executable_digest
            || ack.owner_image_identity != expected_owner.image_handle_identity
            || ack.private_pipe_instance_id != prepared.pipe.instance_id
            || ack.adopted_at < expected_owner.started_at
            || ack.adopted_at < terminal.pipe_ack.accepted_at
            || ack.adopted_at >= policy.deadline
            || ack.ack_binding_digest
                != derive_listener_ack_binding(policy, lease, ack, &prepared.pipe)
            || !ack.adopted_from_service_share
            || !ack.ack_read_from_service_pipe_handle
            || !ack.socket_object_identity_verified
            || ack.pid_table_only
            || !ack.socket_adopted_from_share
            || !ack.getsockname_verified
            || !ack.type_and_protocol_verified
            || !ack.socket_options_verified
            || ack.inner_live_bootstrap_version != INNER_LIVE_BOOTSTRAP_VERSION
            || ack.inner_live_bootstrap_digest != prepared.pipe.inner_live_bootstrap_digest
            || !ack.inner_live_bootstrap_parsed
            || ack.ordinary_bind_attempted
            || !ack.pipe_closed_after_ack
            || !ack.pipe_reconnect_rejected
            || !ack.wire_ack_digest_verified
            || !ack.loopback_v4_only
            || !ack.exclusive_address_use
            || !ack.address_reuse_disabled
        {
            return Err(SupervisorError::new(
                "authority_native_listener_adoption_invalid",
            ));
        }
    }
    if terminal.pipe_ack.peer != backend.key {
        return Err(SupervisorError::new(
            "authority_native_backend_pipe_peer_mismatch",
        ));
    }
    Ok(())
}

fn validate_http_lifecycle(
    policy: &SupervisorPolicy,
    prepared: &NativePreparedEvidence,
    terminal: &NativeTerminalEvidence,
    backend: &ProcessObservation,
) -> Result<(), SupervisorError> {
    let http = &terminal.http_lifecycle;
    let app_listener = prepared
        .listeners
        .iter()
        .find(|item| item.role == SocketRole::App)
        .ok_or_else(|| SupervisorError::new("authority_native_app_listener_missing"))?;
    let finalized_before_deadline =
        terminal.terminal.kind == TerminalKind::TimedOut || http.finalized_at <= policy.deadline;
    if http.contract_digest != derive_http_lifecycle_contract(policy)
        || http.ticket_digest != policy.ticket_digest
        || http.run_binding_digest != policy.run_binding_digest
        || http.private_pipe_instance_id != prepared.pipe.instance_id
        || http.listener_socket_object_id != app_listener.listener_socket_object_id
        || http.backend != backend.key
        || http.started_at < terminal.pipe_ack.accepted_at
        || http.finalized_at < http.started_at
        || http.finalized_at > terminal.terminal.observed_at
        || !finalized_before_deadline
        || http.request_count == 0
        || !http.service_direct
        || http.caller_requests_present
        || !http.exact_sequence_observed
        || !http.responses_read_from_service_connection_handles
    {
        return Err(SupervisorError::new(
            "authority_native_http_lifecycle_invalid",
        ));
    }
    Ok(())
}

fn validate_bridge_proxy(
    policy: &SupervisorPolicy,
    prepared: &NativePreparedEvidence,
    terminal: &NativeTerminalEvidence,
) -> Result<(), SupervisorError> {
    let bridge_endpoint = policy
        .socket_policies
        .iter()
        .find(|endpoint| endpoint.role == SocketRole::Bridge)
        .ok_or_else(|| SupervisorError::new("authority_native_bridge_policy_missing"))?;
    let target_owner = &terminal.processes[role_index(bridge_endpoint.owner_role)];
    let unity = &terminal.processes[role_index(ProcessRole::Unity)];
    let proxy = &terminal.bridge_proxy;
    let bridge_target_lease = prepared
        .listeners
        .iter()
        .find(|listener| listener.role == SocketRole::Bridge)
        .ok_or_else(|| SupervisorError::new("authority_native_bridge_target_missing"))?;
    if proxy.public_listener_socket_object_id
        != prepared.bridge_proxy.public_listener_socket_object_id
        || proxy.public_port != BRIDGE_LOOPBACK_PORT
        || proxy.target_listener_socket_object_id
            != prepared.bridge_proxy.target_listener_socket_object_id
        || proxy.target_port != prepared.bridge_proxy.target_port
        || proxy.target_owner != target_owner.key
        || proxy.target_owner_job_object_id != policy.job_object_id
        || proxy.target_owner_executable_digest != target_owner.executable_digest
        || proxy.target_owner_image_identity != target_owner.image_handle_identity
        || proxy.target_adopted_at < target_owner.started_at
        || proxy.target_adopted_at >= policy.deadline
        || proxy.target_adoption_binding_digest
            != derive_bridge_target_ack_binding(
                policy,
                bridge_target_lease,
                &prepared.bridge_control_pipe,
                proxy,
            )
        || !proxy.target_socket_adopted_from_service_share
        || !proxy.target_adoption_ack_read_from_service_launch_pipe
        || !proxy.target_socket_object_identity_verified
        || !target_owner.job_member
        || !proxy.service_owns_public_listener
        || !proxy.target_identity_verified_from_socket_and_process_handles
        || proxy.pid_table_only
        || !proxy.unity_bridge_launch_disabled
        || !proxy.unity_connected_to_service_proxy
        || proxy.unexpected_bridge_launch_attempt
        || proxy.release_then_bind_used
        || proxy.target_ready_at != proxy.target_adopted_at
        || proxy.public_proxy_enabled_at <= proxy.target_ready_at
        || proxy.proxy_health_verified_at <= proxy.public_proxy_enabled_at
        || unity.started_at <= proxy.proxy_health_verified_at
        || !proxy.public_listener_hidden_until_target_ready
        || !proxy.health_verified_through_proxy
        || !proxy.explicit_http_and_websocket_semantic_proxy
        || proxy.request_auth_key_digest != prepared.bridge_proxy.request_auth_key_digest
        || !proxy.request_auth_injected_by_service
        || proxy.controlled_health_request_count != 1
        || proxy.connections.is_empty()
    {
        return Err(SupervisorError::new(
            "authority_native_bridge_proxy_invalid",
        ));
    }
    let mut accepted_ids = BTreeSet::new();
    let mut target_ids = BTreeSet::new();
    let mut observed_proxy_http_requests = 0_u32;
    let mut observed_proxy_websocket_requests = 0_u32;
    for connection in &proxy.connections {
        if connection.accepted_connection_object_id == 0
            || connection.target_connection_object_id == 0
            || !accepted_ids.insert(connection.accepted_connection_object_id)
            || !target_ids.insert(connection.target_connection_object_id)
            || connection.accepted_connection_object_id == connection.target_connection_object_id
            || connection.accepted_at < proxy.public_proxy_enabled_at
            || connection.closed_at < connection.accepted_at
            || connection.closed_at > terminal.terminal.intent_recorded_at
            || connection.byte_limit == 0
            || connection.byte_limit > 64 * 1024 * 1024
            || connection.idle_timeout_ms == 0
            || connection.idle_timeout_ms > 120_000
            || (connection.http_request_count == 0 && connection.websocket_request_count == 0)
            || !connection.semantic_request_parse_complete
            || !connection.request_auth_injected
            || !connection.response_or_websocket_close_complete
            || !connection.both_handles_service_owned
        {
            return Err(SupervisorError::new(
                "authority_native_bridge_proxy_connection_invalid",
            ));
        }
        observed_proxy_http_requests = observed_proxy_http_requests
            .checked_add(connection.http_request_count)
            .ok_or_else(|| SupervisorError::new("authority_native_bridge_request_count_invalid"))?;
        observed_proxy_websocket_requests = observed_proxy_websocket_requests
            .checked_add(connection.websocket_request_count)
            .ok_or_else(|| SupervisorError::new("authority_native_bridge_request_count_invalid"))?;
    }
    if proxy.proxy_http_request_count != observed_proxy_http_requests
        || proxy.proxy_websocket_request_count != observed_proxy_websocket_requests
    {
        return Err(SupervisorError::new(
            "authority_native_bridge_request_count_invalid",
        ));
    }
    Ok(())
}

fn validate_bridge_control_pipe(
    policy: &SupervisorPolicy,
    prepared: &NativePreparedEvidence,
    terminal: &NativeTerminalEvidence,
) -> Result<(), SupervisorError> {
    let lease = &prepared.bridge_control_pipe;
    let observed = &terminal.bridge_control_pipe;
    let target = &terminal.processes[role_index(ProcessRole::BridgeListener)];
    let eof_before_deadline = terminal.terminal.kind == TerminalKind::TimedOut
        || observed.eof_observed_at < policy.deadline;
    if observed.instance_id != lease.instance_id
        || observed.instance_id == prepared.pipe.instance_id
        || observed.binding_digest != lease.binding_digest
        || observed.challenge_digest != lease.challenge_digest
        || observed.peer != target.key
        || observed.peer_job_object_id != policy.job_object_id
        || observed.peer_executable_digest != target.executable_digest
        || observed.peer_image_identity != target.image_handle_identity
        || observed.accepted_at < target.started_at
        || observed.adoption_ack_at < observed.accepted_at
        || observed.adoption_ack_at != terminal.bridge_proxy.target_adopted_at
        || observed.shutdown_requested_at < terminal.terminal.intent_recorded_at
        || observed.accounting_read_at <= observed.shutdown_requested_at
        || observed.eof_observed_at <= observed.accounting_read_at
        || !eof_before_deadline
        || observed.accepted_connections != 1
        || !observed.peer_verified_from_pipe_and_process_handles
        || !observed.ack_then_shutdown_then_accounting_then_eof
        || !observed.replay_rejected
        || !observed.reconnect_rejected
        || !observed.service_handle_held_through_eof
    {
        return Err(SupervisorError::new(
            "authority_native_bridge_control_pipe_invalid",
        ));
    }
    Ok(())
}

fn validate_native_cleanup(
    policy: &SupervisorPolicy,
    prepared: &NativePreparedEvidence,
    terminal: &NativeTerminalEvidence,
    cleanup: &NativeCleanupEvidence,
) -> Result<(), SupervisorError> {
    let native = &cleanup.native;
    let expected_listener_ids = prepared
        .listeners
        .iter()
        .map(|item| item.listener_socket_object_id)
        .collect::<BTreeSet<_>>();
    let actual_listener_ids = native
        .closed_service_listener_ids
        .iter()
        .copied()
        .collect::<BTreeSet<_>>();
    let request_accounting = native
        .bridge_target_request_accounting
        .as_ref()
        .ok_or_else(|| {
            SupervisorError::new("authority_native_bridge_request_accounting_missing")
        })?;
    let proxy = &terminal.bridge_proxy;
    let expected_total = request_accounting
        .controlled_health_request_count
        .checked_add(request_accounting.proxy_http_request_count)
        .and_then(|value| value.checked_add(request_accounting.proxy_websocket_request_count))
        .and_then(|value| value.checked_add(request_accounting.rejected_request_count))
        .ok_or_else(|| SupervisorError::new("authority_native_bridge_request_count_invalid"))?;
    if native.private_pipe_instance_id != Some(prepared.pipe.instance_id)
        || !native.private_pipe_closed
        || !native.pipe_challenge_zeroed
        || !native.no_pending_pipe_clients
        || !native.pipe_replay_rejected
        || native.bridge_control_pipe_instance_id != Some(prepared.bridge_control_pipe.instance_id)
        || native.bridge_control_pipe_instance_id == native.private_pipe_instance_id
        || !native.bridge_control_pipe_closed
        || !native.bridge_control_pipe_challenge_zeroed
        || !native.bridge_control_pipe_no_pending_clients
        || !native.bridge_control_pipe_replay_rejected
        || native.closed_service_listener_ids.len() != expected_listener_ids.len()
        || actual_listener_ids != expected_listener_ids
        || !native.all_candidate_listener_duplicates_closed
        || !native.all_service_listener_handles_closed
        || !native.completion_port_drained
        || !native.no_inheritable_handle_residue
        || !native.no_port_drift
        || !native.bridge_proxy_listener_closed
        || !native.bridge_proxy_connections_closed
        || !native.bridge_target_listener_closed
        || !native.bridge_request_auth_credentials_zeroized
        || request_accounting.request_auth_key_digest != proxy.request_auth_key_digest
        || request_accounting.controlled_health_request_count
            != proxy.controlled_health_request_count
        || request_accounting.proxy_http_request_count != proxy.proxy_http_request_count
        || request_accounting.proxy_websocket_request_count != proxy.proxy_websocket_request_count
        || request_accounting.total_target_request_count != expected_total
        || request_accounting.rejected_request_count != 0
        || request_accounting.bypass_request_count != 0
        || !request_accounting.request_auth_header_stripped
        || request_accounting.observed_at_shutdown < terminal.terminal.intent_recorded_at
        || request_accounting.observed_at_shutdown
            != terminal.bridge_control_pipe.accounting_read_at
        || request_accounting.observed_at_shutdown > cleanup.cleanup.observed_at
        || terminal.bridge_control_pipe.eof_observed_at > cleanup.cleanup.observed_at
        || !request_accounting.read_from_adapter_shutdown_channel
        || request_accounting.accounting_digest
            != derive_bridge_target_request_accounting_digest(request_accounting)
        || !native.containment_readback_complete
        || cleanup.sockets.len() != policy.socket_policies.len()
    {
        return Err(SupervisorError::new("authority_native_cleanup_residue"));
    }
    Ok(())
}

fn validate_native_abort_cleanup(
    abort: &NativeAbortEvidence,
    phase: NativeSupervisorPhase,
    failure_code: &'static str,
) -> Result<(), SupervisorError> {
    let native = &abort.native_cleanup;
    if abort.failed_phase != phase
        || abort.failure_code != failure_code
        || !native.private_pipe_closed
        || !native.pipe_challenge_zeroed
        || !native.no_pending_pipe_clients
        || !native.pipe_replay_rejected
        || !native.bridge_control_pipe_closed
        || !native.bridge_control_pipe_challenge_zeroed
        || !native.bridge_control_pipe_no_pending_clients
        || !native.bridge_control_pipe_replay_rejected
        || !native.all_candidate_listener_duplicates_closed
        || !native.all_service_listener_handles_closed
        || !native.completion_port_drained
        || !native.no_inheritable_handle_residue
        || !native.no_port_drift
        || !native.bridge_proxy_listener_closed
        || !native.bridge_proxy_connections_closed
        || !native.bridge_target_listener_closed
        || !native.bridge_request_auth_credentials_zeroized
        || !native.containment_readback_complete
    {
        return Err(SupervisorError::new(
            "authority_native_abort_cleanup_unverified",
        ));
    }
    Ok(())
}

fn build_admission_binding(
    prepared: &PreparedRecoveryReceipt,
    armed: &ArmedRecoveryReceipt,
    policy_snapshot: &[u8],
    prepared_receipt: &NativeAdmissionReceipt,
    armed_receipt: &NativeArmedAdmissionReceipt,
) -> Result<NativeAdmissionBinding, SupervisorError> {
    let prepared_receipt_digest = prepared.digest();
    let armed_receipt_digest: Digest = Sha256::digest(armed.encode()).into();
    let policy_snapshot_digest: Digest = Sha256::digest(policy_snapshot).into();
    if prepared_receipt.prepared_receipt_digest != prepared_receipt_digest
        || prepared_receipt.policy_snapshot_digest != policy_snapshot_digest
        || is_zero_digest(&prepared_receipt.recovery_bundle_digest)
        || !prepared_receipt.read_from_authority_store
        || !prepared_receipt.sealed_by_service
        || armed_receipt.prepared_receipt_digest != prepared_receipt_digest
        || armed_receipt.armed_receipt_digest != armed_receipt_digest
        || armed_receipt.policy_snapshot_digest != policy_snapshot_digest
        || armed_receipt.recovery_bundle_digest != prepared_receipt.recovery_bundle_digest
        || !armed_receipt.read_from_authority_store
        || !armed_receipt.sealed_by_service
    {
        return Err(SupervisorError::new(
            "authority_native_admission_binding_invalid",
        ));
    }
    let mut hasher = Sha256::new();
    hasher.update(ADMISSION_BINDING_DOMAIN);
    hasher.update(prepared_receipt_digest);
    hasher.update(armed_receipt_digest);
    hasher.update(policy_snapshot_digest);
    hasher.update(prepared_receipt.recovery_bundle_digest);
    let binding_digest = hasher.finalize().into();
    Ok(NativeAdmissionBinding {
        prepared_receipt_digest,
        armed_receipt_digest,
        policy_snapshot_digest,
        recovery_bundle_digest: prepared_receipt.recovery_bundle_digest,
        binding_digest,
    })
}

fn derive_private_pipe_binding(policy: &SupervisorPolicy, instance_id: u64) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(PRIVATE_PIPE_BINDING_DOMAIN);
    hasher.update(policy.authority_identity_digest);
    hasher.update(policy.ticket_digest);
    hasher.update(policy.run_binding_digest);
    hasher.update(instance_id.to_be_bytes());
    hasher.finalize().into()
}

fn derive_bridge_control_pipe_binding(policy: &SupervisorPolicy, instance_id: u64) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(BRIDGE_CONTROL_PIPE_BINDING_DOMAIN);
    hasher.update(policy.authority_identity_digest);
    hasher.update(policy.ticket_digest);
    hasher.update(policy.run_binding_digest);
    hasher.update(instance_id.to_be_bytes());
    hasher.finalize().into()
}

fn derive_armed_stage_terminal_digest(
    policy: &SupervisorPolicy,
    armed: &ArmedRecoveryReceipt,
    stage_head_digest: &Digest,
    termination_intent_digest: &Digest,
    kind: NativeTerminationKind,
    proof: &BurnedRunProof,
) -> Digest {
    let armed_digest: Digest = Sha256::digest(armed.encode()).into();
    let kind_code = [match kind {
        NativeTerminationKind::Cancelled => 1,
        NativeTerminationKind::TimedOut => 2,
    }];
    let terminal_ready_at = proof.terminal_ready_at().to_be_bytes();
    let cleanup_observed_at = proof.cleanup_observed_at().to_be_bytes();
    let parts: [&[u8]; 10] = [
        &policy.authority_generation_digest,
        &policy.service_instance_digest,
        &policy.ticket_digest,
        &policy.run_binding_digest,
        &armed_digest,
        stage_head_digest,
        termination_intent_digest,
        &kind_code,
        &terminal_ready_at,
        &cleanup_observed_at,
    ];
    let mut digest = Sha256::new();
    digest.update(ARMED_STAGE_TERMINAL_DOMAIN);
    for part in parts {
        digest.update((part.len() as u64).to_be_bytes());
        digest.update(part);
    }
    digest.finalize().into()
}

fn derive_listener_ack_binding(
    policy: &SupervisorPolicy,
    lease: &ServiceListenerLease,
    ack: &ListenerAdoptionAck,
    pipe: &PrivateBackendPipeLease,
) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(LISTENER_ACK_DOMAIN);
    hasher.update(policy.ticket_digest);
    hasher.update(policy.run_binding_digest);
    hasher.update([socket_role_code(lease.role)]);
    hasher.update(lease.local_port.to_be_bytes());
    hasher.update(lease.listener_socket_object_id.to_be_bytes());
    hasher.update(pipe.instance_id.to_be_bytes());
    hasher.update(pipe.binding_digest);
    hasher.update(ack.owner.pid.to_be_bytes());
    hasher.update(ack.owner.creation_time.to_be_bytes());
    hasher.update(ack.owner_job_object_id.to_be_bytes());
    hasher.update(ack.owner_executable_digest);
    hasher.update(ack.owner_image_identity.volume_serial.to_be_bytes());
    hasher.update(ack.owner_image_identity.file_id);
    hasher.finalize().into()
}

fn derive_bridge_target_ack_binding(
    policy: &SupervisorPolicy,
    lease: &ServiceListenerLease,
    control_pipe: &BridgeTargetControlPipeLease,
    proxy: &BridgeProxyObservation,
) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(BRIDGE_TARGET_ACK_DOMAIN);
    hasher.update(policy.ticket_digest);
    hasher.update(policy.run_binding_digest);
    hasher.update(lease.local_port.to_be_bytes());
    hasher.update(lease.listener_socket_object_id.to_be_bytes());
    hasher.update(lease.share_material_digest);
    hasher.update(control_pipe.instance_id.to_be_bytes());
    hasher.update(control_pipe.binding_digest);
    hasher.update(control_pipe.challenge_digest);
    hasher.update(proxy.request_auth_key_digest);
    hasher.update(proxy.target_owner.pid.to_be_bytes());
    hasher.update(proxy.target_owner.creation_time.to_be_bytes());
    hasher.update(proxy.target_owner_job_object_id.to_be_bytes());
    hasher.update(proxy.target_owner_executable_digest);
    hasher.update(
        proxy
            .target_owner_image_identity
            .volume_serial
            .to_be_bytes(),
    );
    hasher.update(proxy.target_owner_image_identity.file_id);
    hasher.finalize().into()
}

fn derive_bridge_target_request_accounting_digest(
    accounting: &BridgeTargetRequestAccountingObservation,
) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(BRIDGE_TARGET_REQUEST_ACCOUNTING_DOMAIN);
    hasher.update(accounting.request_auth_key_digest);
    hasher.update(accounting.controlled_health_request_count.to_be_bytes());
    hasher.update(accounting.proxy_http_request_count.to_be_bytes());
    hasher.update(accounting.proxy_websocket_request_count.to_be_bytes());
    hasher.update(accounting.total_target_request_count.to_be_bytes());
    hasher.update(accounting.rejected_request_count.to_be_bytes());
    hasher.update(accounting.bypass_request_count.to_be_bytes());
    hasher.update([u8::from(accounting.request_auth_header_stripped)]);
    hasher.update(accounting.observed_at_shutdown.to_be_bytes());
    hasher.update([u8::from(accounting.read_from_adapter_shutdown_channel)]);
    hasher.finalize().into()
}

fn derive_http_lifecycle_contract(policy: &SupervisorPolicy) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(HTTP_LIFECYCLE_DOMAIN);
    hasher.update(policy.ticket_digest);
    hasher.update(policy.run_binding_digest);
    for endpoint in &policy.socket_policies {
        hasher.update([socket_role_code(endpoint.role)]);
        hasher.update(endpoint.local_port.to_be_bytes());
        hasher.update(endpoint.driver_binding_digest);
    }
    hasher.finalize().into()
}

fn file_identity_digest(identity: &FileIdentity) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(b"vrcforge-authority-file-identity-v1\0");
    hasher.update(identity.volume_serial.to_be_bytes());
    hasher.update(identity.file_id);
    hasher.finalize().into()
}

fn frame_take_u8(bytes: &[u8], offset: &mut usize) -> Result<u8, SupervisorError> {
    let value = *bytes
        .get(*offset)
        .ok_or_else(|| SupervisorError::new("authority_backend_adoption_ack_invalid"))?;
    *offset += 1;
    Ok(value)
}

fn frame_take_u16(bytes: &[u8], offset: &mut usize) -> Result<u16, SupervisorError> {
    let end = offset
        .checked_add(2)
        .ok_or_else(|| SupervisorError::new("authority_backend_adoption_ack_invalid"))?;
    let raw: [u8; 2] = bytes
        .get(*offset..end)
        .and_then(|value| value.try_into().ok())
        .ok_or_else(|| SupervisorError::new("authority_backend_adoption_ack_invalid"))?;
    *offset = end;
    Ok(u16::from_be_bytes(raw))
}

fn frame_take_u32(bytes: &[u8], offset: &mut usize) -> Result<u32, SupervisorError> {
    let end = offset
        .checked_add(4)
        .ok_or_else(|| SupervisorError::new("authority_backend_adoption_ack_invalid"))?;
    let raw: [u8; 4] = bytes
        .get(*offset..end)
        .and_then(|value| value.try_into().ok())
        .ok_or_else(|| SupervisorError::new("authority_backend_adoption_ack_invalid"))?;
    *offset = end;
    Ok(u32::from_be_bytes(raw))
}

fn frame_take_u64(bytes: &[u8], offset: &mut usize) -> Result<u64, SupervisorError> {
    let end = offset
        .checked_add(8)
        .ok_or_else(|| SupervisorError::new("authority_backend_adoption_ack_invalid"))?;
    let raw: [u8; 8] = bytes
        .get(*offset..end)
        .and_then(|value| value.try_into().ok())
        .ok_or_else(|| SupervisorError::new("authority_backend_adoption_ack_invalid"))?;
    *offset = end;
    Ok(u64::from_be_bytes(raw))
}

fn frame_take_digest(bytes: &[u8], offset: &mut usize) -> Result<Digest, SupervisorError> {
    let end = offset
        .checked_add(32)
        .ok_or_else(|| SupervisorError::new("authority_backend_adoption_ack_invalid"))?;
    let value = bytes
        .get(*offset..end)
        .and_then(|value| value.try_into().ok())
        .ok_or_else(|| SupervisorError::new("authority_backend_adoption_ack_invalid"))?;
    *offset = end;
    Ok(value)
}

#[cfg(test)]
mod native_tests {
    use super::*;
    use crate::primitive_evidence_authority_supervisor::tests::{
        completed_observation, digest, empty_abort_observation, policy,
    };
    use std::{
        fs::{self, File},
        ops::Deref,
        path::PathBuf,
        sync::atomic::{AtomicU64, Ordering},
    };

    static TEST_START_CONTRACT_COUNTER: AtomicU64 = AtomicU64::new(1);

    #[test]
    fn legacy_uncontracted_start_surface_is_test_only() {
        let source = include_str!("primitive_evidence_authority_supervisor_windows.rs")
            .replace("\r\n", "\n");
        assert!(source.contains("#[cfg(test)]\npub(crate) enum NativeStartOutcome"));
        assert!(source.contains("#[cfg(test)]\n    pub(crate) fn start_to_armed("));
        for method in [
            "fn prepare(",
            "fn launch_root_suspended(",
            "fn assign_root_to_job(",
            "fn resume_root(",
        ] {
            let test_gated = format!("#[cfg(test)]\n    {method}");
            assert!(
                source.contains(&test_gated),
                "legacy native method must remain outside production: {method}"
            );
        }
        let recovered_start = source
            .find("pub(crate) struct NativeRecoveredCompletedEvidence")
            .expect("recovered Completed evidence type");
        let recovered_end = source[recovered_start..]
            .find("pub(crate) struct NativeRecoveredStageJournalEvidence")
            .map(|offset| recovered_start + offset)
            .expect("next recovered evidence type");
        let recovered_completed = &source[recovered_start..recovered_end];
        for field in [
            "journal: NativeRecoveredStageJournalEvidence",
            "actions: NativeRecoveredStageActionEvidence",
            "completed_stage: NativeCompletedStageJournalBinding",
        ] {
            assert!(recovered_completed.contains(field));
        }
        for legacy_field in [
            ["recovered_from_", "sealed_native_journal"].concat(),
            ["journal_chain_", "digest"].concat(),
        ] {
            assert!(!recovered_completed.contains(&legacy_field));
        }
    }

    struct TestStartContract {
        contract: Option<VerifiedScenarioStartContract>,
        paths: [PathBuf; 2],
    }

    impl Deref for TestStartContract {
        type Target = VerifiedScenarioStartContract;

        fn deref(&self) -> &Self::Target {
            self.contract
                .as_ref()
                .expect("test start contract remains live")
        }
    }

    impl Drop for TestStartContract {
        fn drop(&mut self) {
            drop(self.contract.take());
            for path in &self.paths {
                let _ = fs::remove_file(path);
            }
        }
    }

    fn test_start_contract(prepared: &PreparedRun) -> TestStartContract {
        let nonce = TEST_START_CONTRACT_COUNTER.fetch_add(1, Ordering::Relaxed);
        let root = std::env::temp_dir();
        let paths = [
            root.join(format!(
                "vrcforge-start-driver-{}-{nonce}.bin",
                std::process::id()
            )),
            root.join(format!(
                "vrcforge-start-bridge-{}-{nonce}.bin",
                std::process::id()
            )),
        ];
        fs::write(&paths[0], b"driver-start-handle").expect("write driver start fixture");
        fs::write(&paths[1], b"bridge-start-handle").expect("write bridge start fixture");
        let driver = File::open(&paths[0]).expect("open driver start fixture");
        let bridge_launcher = File::open(&paths[1]).expect("open bridge start fixture");
        let policy_snapshot_digest: Digest = Sha256::digest(prepared.policy_snapshot()).into();
        let contract = VerifiedScenarioStartContract::for_test_from_files(
            driver,
            bridge_launcher,
            prepared.receipt().digest(),
            policy_snapshot_digest,
        )
        .expect("test start handles must produce a contract");
        TestStartContract {
            contract: Some(contract),
            paths,
        }
    }

    fn begin_staged_for_test(
        supervisor: &mut ServiceOwnedNativeSupervisor<MockNativeApi>,
        prepared: PreparedRun,
    ) -> (NativeStartingRun, TestStartContract) {
        let contract = test_start_contract(&prepared);
        let starting = supervisor
            .begin_start(prepared, &contract)
            .expect("stage declaration construction must succeed");
        (starting, contract)
    }

    fn advance_staged_for_test(
        supervisor: &mut ServiceOwnedNativeSupervisor<MockNativeApi>,
        starting: NativeStartingRun,
        contract: &TestStartContract,
    ) -> NativeStartingAdvance {
        supervisor.advance_starting(starting, contract)
    }

    fn native_test_digest_hex(value: &Digest) -> String {
        const HEX: &[u8; 16] = b"0123456789abcdef";
        let mut output = String::with_capacity(64);
        for byte in value {
            output.push(HEX[(byte >> 4) as usize] as char);
            output.push(HEX[(byte & 0x0f) as usize] as char);
        }
        output
    }

    fn bridge_control_pipe_for_frame(
        binding_digest: Digest,
        instance_id: u64,
        challenge_digest: Digest,
    ) -> BridgeTargetControlPipeLease {
        BridgeTargetControlPipeLease {
            instance_id,
            binding_digest,
            challenge_digest,
            created_at: 1,
            created_new: true,
            one_connection: true,
            service_owned: true,
            restricted_acl: true,
            service_handle_held_through_shutdown: true,
            restricted_service_handle_in_launch_allowlist: true,
            material_exposed_to_argv: false,
            material_exposed_to_environment: false,
            material_exposed_to_report: false,
            material_exposed_to_log: false,
        }
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    enum HostileMode {
        None,
        MissingListenerCapability,
        MissingBridgeCapability,
        MissingBridgeStartupCapability,
        MissingBridgeRequestAuthCapability,
        ArmedAdmissionMismatch,
        PipePidOnly,
        ListenerPidOnly,
        ProcessImageReplacement,
        UnexpectedJobChild,
        PortDrift,
        HandleResidue,
        OriginBindingMismatch,
        OriginAuthorityTicketMismatch,
        OriginTicketDigestMismatch,
        OriginTicketsSwapped,
        TerminalFailure,
        TerminalPendingOnce,
        BootstrapVersionDrift,
        BridgeProxyExposedEarly,
        BridgeRequestAuthDigestMismatch,
        BridgeRejectedRequest,
        BridgeRequestCountMismatch,
        BridgeAuthHeaderForwarded,
        BridgeRequestAuthNotZeroized,
    }

    #[derive(Clone, Copy, Debug)]
    enum StageCreateReopenFault {
        None,
        Bytes,
        FileIdentity,
        Security,
        CreatedNew,
        HeldHandle,
    }

    struct MockNativeApi {
        observation: AuthorityOwnedRunObservation,
        hostile: HostileMode,
        events: Vec<NativeSupervisorPhase>,
        containment_calls: usize,
        seal_calls: usize,
        terminal_polls: usize,
        armed_termination_kind: NativeTerminationKind,
        armed_termination_uncertain_once: bool,
        stage_bytes: Vec<u8>,
        stage_write_sequence: u64,
        stage_create_requests: usize,
        stage_create_actions: usize,
        stage_create_uncertain_after_write_once: bool,
        stage_create_rejected_no_mutation_once: bool,
        stage_create_reopen_fault: StageCreateReopenFault,
        stage_prepared: Option<NativePreparedEvidence>,
        stage_bridge_created: Option<staged_start::NativeCreatedRootReceipt>,
        stage_driver_created: Option<staged_start::NativeCreatedRootReceipt>,
        stage_driver_resumed: Option<ResumedRootReceipt>,
        stage_armed_admission: Option<NativeArmedAdmissionReceipt>,
        stage_append_requests: usize,
        stage_durable_appends: usize,
        stage_append_uncertain_after_write_once: bool,
        stage_termination_uncertain_after_write_once: bool,
        stage_containment_uncertain_once: bool,
        stage_containment_calls: usize,
        stage_created_roles: Vec<ProcessRole>,
        stage_resumed_roles: Vec<ProcessRole>,
        stage_termination_prior_head: Option<Digest>,
        stage_termination_intent_digest: Option<Digest>,
        stage_termination_requested_at_ms: Option<u64>,
        stage_termination_recorded_at_ms: Option<u64>,
        stage_normal_evidence: Option<staged_start::NativeStartingTerminationEvidence>,
        failure_containment_uncertain_once: bool,
        restart_completed: Option<NativeRecoveredCompletedEvidence>,
        restart_normal: Option<NativeRecoveredNormalTerminationEvidence>,
    }

    impl MockNativeApi {
        fn new(policy: &SupervisorPolicy, hostile: HostileMode) -> Self {
            Self {
                observation: completed_observation(policy),
                hostile,
                events: Vec::new(),
                containment_calls: 0,
                seal_calls: 0,
                terminal_polls: 0,
                armed_termination_kind: NativeTerminationKind::Cancelled,
                armed_termination_uncertain_once: false,
                stage_bytes: Vec::new(),
                stage_write_sequence: 0,
                stage_create_requests: 0,
                stage_create_actions: 0,
                stage_create_uncertain_after_write_once: false,
                stage_create_rejected_no_mutation_once: false,
                stage_create_reopen_fault: StageCreateReopenFault::None,
                stage_prepared: None,
                stage_bridge_created: None,
                stage_driver_created: None,
                stage_driver_resumed: None,
                stage_armed_admission: None,
                stage_append_requests: 0,
                stage_durable_appends: 0,
                stage_append_uncertain_after_write_once: false,
                stage_termination_uncertain_after_write_once: false,
                stage_containment_uncertain_once: false,
                stage_containment_calls: 0,
                stage_created_roles: Vec::new(),
                stage_resumed_roles: Vec::new(),
                stage_termination_prior_head: None,
                stage_termination_intent_digest: None,
                stage_termination_requested_at_ms: None,
                stage_termination_recorded_at_ms: None,
                stage_normal_evidence: None,
                failure_containment_uncertain_once: false,
                restart_completed: None,
                restart_normal: None,
            }
        }

        fn stage_store_readback(
            &self,
            created_new: bool,
            parent_flushed_after_create: bool,
        ) -> staged_start::NativeStageJournalStoreReadback {
            staged_start::NativeStageJournalStoreReadback {
                canonical_bytes: self.stage_bytes.clone(),
                file_identity: FileIdentity {
                    volume_serial: 77,
                    file_id: [0x77; 16],
                },
                parent_identity: FileIdentity {
                    volume_serial: 78,
                    file_id: [0x78; 16],
                },
                created_new,
                write_sequence: self.stage_write_sequence,
                append_flushed: true,
                parent_flushed_after_create,
                reopened_from_held_handle: true,
                service_owned_parent: true,
                owner_local_system: true,
                protected_restricted_dacl: true,
                file_is_reparse_point: false,
                parent_is_reparse_point: false,
                single_link: true,
                service_handle_held: true,
            }
        }

        fn append_stage_exact(
            &mut self,
            prior_byte_len: usize,
            record_bytes: &[u8],
        ) -> Result<staged_start::NativeStageJournalStoreReadback, SupervisorError> {
            self.stage_append_requests += 1;
            if self.stage_bytes.len() == prior_byte_len {
                self.stage_bytes.extend_from_slice(record_bytes);
                self.stage_write_sequence = self.stage_write_sequence.saturating_add(1);
                self.stage_durable_appends += 1;
            } else {
                let expected_len = prior_byte_len
                    .checked_add(record_bytes.len())
                    .ok_or_else(|| SupervisorError::new("test_stage_append_extent_invalid"))?;
                if self.stage_bytes.len() != expected_len
                    || self.stage_bytes[prior_byte_len..] != *record_bytes
                {
                    return Err(SupervisorError::new("test_stage_append_conflict"));
                }
            }
            let readback = self.stage_store_readback(false, false);
            if self.stage_append_uncertain_after_write_once {
                self.stage_append_uncertain_after_write_once = false;
                return Err(SupervisorError::new("test_stage_append_uncertain"));
            }
            Ok(readback)
        }

        fn prepared_evidence(
            &self,
            policy: &SupervisorPolicy,
            prepared: &PreparedRecoveryReceipt,
            policy_snapshot: &[u8],
            start_contract_digest: Digest,
        ) -> NativePreparedEvidence {
            let pipe_instance = 7_000;
            let pipe = PrivateBackendPipeLease {
                instance_id: pipe_instance,
                binding_digest: derive_private_pipe_binding(policy, pipe_instance),
                challenge_digest: digest(90),
                inner_live_bootstrap_digest: digest(94),
                created_at: 19,
                created_new: true,
                one_use: true,
                service_owned: true,
                restricted_acl: true,
                service_handle_held: true,
                material_exposed_to_argv: false,
                material_exposed_to_environment: false,
                material_exposed_to_report: false,
                material_exposed_to_log: false,
            };
            let bridge_control_pipe_instance = 7_001;
            let bridge_control_pipe = BridgeTargetControlPipeLease {
                instance_id: bridge_control_pipe_instance,
                binding_digest: derive_bridge_control_pipe_binding(
                    policy,
                    bridge_control_pipe_instance,
                ),
                challenge_digest: digest(95),
                created_at: 22,
                created_new: true,
                one_connection: true,
                service_owned: true,
                restricted_acl: true,
                service_handle_held_through_shutdown: true,
                restricted_service_handle_in_launch_allowlist: true,
                material_exposed_to_argv: false,
                material_exposed_to_environment: false,
                material_exposed_to_report: false,
                material_exposed_to_log: false,
            };
            let listeners = self
                .observation
                .sockets
                .iter()
                .map(|socket| ServiceListenerLease {
                    role: socket.role,
                    local_port: socket.local_port,
                    listener_socket_object_id: socket.listener_socket_id,
                    created_at: socket.prelaunch_idle_observed_at,
                    loopback_v4_only: true,
                    exclusive_address_use: true,
                    address_reuse_disabled: true,
                    service_created: true,
                    service_handle_held_until_adoption: true,
                    share_material_digest: digest(91 + socket_role_code(socket.role)),
                    share_material_exposed_to_argv: false,
                    share_material_exposed_to_environment: false,
                    share_material_exposed_to_report: false,
                    share_material_exposed_to_log: false,
                })
                .collect::<Vec<_>>();
            let bridge_target = listeners
                .iter()
                .find(|listener| listener.role == SocketRole::Bridge)
                .expect("bridge target");
            let bridge_proxy = BridgeProxyLease {
                public_listener_socket_object_id: 880,
                public_port: BRIDGE_LOOPBACK_PORT,
                target_listener_socket_object_id: bridge_target.listener_socket_object_id,
                target_port: bridge_target.local_port,
                created_at: 23,
                service_owns_public_listener: true,
                public_listener_never_transferred: true,
                loopback_v4_only: true,
                exclusive_address_use: true,
                address_reuse_disabled: true,
                service_handle_held_through_cleanup_begin: true,
                request_auth_key_digest: digest(97),
                request_auth_material_held_in_memory: true,
                request_auth_exposed_to_argv: false,
                request_auth_exposed_to_environment: false,
                request_auth_exposed_to_report: false,
                request_auth_exposed_to_log: false,
            };
            let bridge_launcher =
                &self.observation.processes[role_index(ProcessRole::BridgeLauncher)];
            let bridge_launch = self
                .observation
                .launches
                .iter()
                .find(|launch| launch.role == ProcessRole::BridgeLauncher)
                .expect("bridge launcher root");
            let bridge_suspended = SuspendedRootReceipt {
                role: ProcessRole::BridgeLauncher,
                process: bridge_launcher.key,
                parent: policy.authority_process,
                executable_digest: bridge_launcher.executable_digest,
                image_identity: bridge_launcher.image_handle_identity,
                runner_identity_digest: policy.runner_identity_digest,
                child_transport_contract_digest: policy.child_transport_contract_digest,
                raw_handle_list: bridge_launch.raw_handle_list,
                created_suspended_at: bridge_launch.created_suspended_at,
                job_list_attribute_applied: true,
                job_assigned_at_creation: true,
                job_membership_readback_before_return: true,
                process_handle_held: true,
                image_handle_held: true,
                all_other_handles_non_inheritable: true,
                breakaway_requested: false,
            };
            let bridge_membership = JobAssignmentReceipt {
                process: bridge_launcher.key,
                job_object_id: policy.job_object_id,
                membership_verified_at: bridge_launch.job_membership_verified_at,
                initial_assignment_call_performed: false,
                job_membership_revalidated: true,
                membership_readback_before_resume: true,
                assigned_using_process_and_job_handles: true,
                process_confirmed_job_member: true,
                completion_port_assignment_observed: true,
            };
            let bridge_resumed = ResumedRootReceipt {
                start_contract_digest,
                process: bridge_launcher.key,
                created_suspended_at: bridge_launch.created_suspended_at,
                job_membership_verified_at: bridge_launch.job_membership_verified_at,
                resumed_at: bridge_launch.resumed_at,
                job_object_id: policy.job_object_id,
                runner_identity_digest: policy.runner_identity_digest,
                child_transport_contract_digest: policy.child_transport_contract_digest,
                raw_handle_list: bridge_suspended.raw_handle_list,
                all_other_handles_non_inheritable: true,
                breakaway_requested: false,
            };
            NativePreparedEvidence {
                foundation: NativePreparedFoundation {
                    start_contract_digest,
                    ticket_consumed_at: self.observation.ticket_consumed_at,
                    runner: self.observation.runner.clone(),
                    artifacts: self.observation.artifacts.clone(),
                    pipe,
                    bridge_control_pipe,
                    listeners,
                    bridge_proxy,
                    job: NativeJobReceipt {
                        object_id: policy.job_object_id,
                        deterministic_name_digest: policy.deterministic_job_name_digest,
                        security_binding_digest: policy.job_security_binding_digest,
                        exact_security_readback: true,
                        owner_local_system: true,
                        dacl_present: true,
                        dacl_defaulted: false,
                        dacl_protected: true,
                        dacl_ace_count: 2,
                        system_access_mask: native_job::JOB_OBJECT_ALL_ACCESS_EXACT,
                        service_access_mask: native_job::SERVICE_JOB_ACCESS_EXACT,
                        created_at: 22,
                        kill_on_job_close: true,
                        breakaway_allowed: false,
                        silent_breakaway_allowed: false,
                        active_process_limit: 0,
                        completion_port_attached: true,
                        service_handle_held: true,
                    },
                    admission: NativeAdmissionReceipt {
                        prepared_receipt_digest: prepared.digest(),
                        policy_snapshot_digest: Sha256::digest(policy_snapshot).into(),
                        recovery_bundle_digest: digest(93),
                        read_from_authority_store: true,
                        sealed_by_service: true,
                    },
                },
                bridge_root: AtomicRootLaunchReceipt {
                    suspended: bridge_suspended,
                    membership: bridge_membership,
                    resumed: bridge_resumed,
                },
            }
        }

        fn suspended_root(&self, policy: &SupervisorPolicy) -> SuspendedRootReceipt {
            let driver = &self.observation.processes[role_index(ProcessRole::Driver)];
            let launch = self
                .observation
                .launches
                .iter()
                .find(|launch| launch.role == ProcessRole::Driver)
                .expect("driver launch");
            SuspendedRootReceipt {
                role: ProcessRole::Driver,
                process: driver.key,
                parent: policy.authority_process,
                executable_digest: driver.executable_digest,
                image_identity: driver.image_handle_identity,
                runner_identity_digest: policy.runner_identity_digest,
                child_transport_contract_digest: policy.child_transport_contract_digest,
                raw_handle_list: launch.raw_handle_list,
                created_suspended_at: launch.created_suspended_at,
                job_list_attribute_applied: true,
                job_assigned_at_creation: true,
                job_membership_readback_before_return: true,
                process_handle_held: true,
                image_handle_held: true,
                all_other_handles_non_inheritable: true,
                breakaway_requested: false,
            }
        }

        fn terminal_evidence(
            &self,
            policy: &SupervisorPolicy,
            prepared: &NativePreparedEvidence,
        ) -> NativeTerminalEvidence {
            let backend = &self.observation.processes[role_index(ProcessRole::Backend)];
            let mut pipe_ack = PrivateBackendPipeAck {
                instance_id: prepared.pipe.instance_id,
                binding_digest: prepared.pipe.binding_digest,
                challenge_digest: prepared.pipe.challenge_digest,
                peer: backend.key,
                peer_job_object_id: policy.job_object_id,
                peer_executable_digest: backend.executable_digest,
                peer_image_identity: backend.image_handle_identity,
                accepted_at: 51,
                accepted_connections: 1,
                peer_verified_from_pipe_and_process_handles: true,
                pid_table_only: false,
                replay_rejected: true,
                ack_read_from_service_pipe_handle: true,
            };
            if self.hostile == HostileMode::PipePidOnly {
                pipe_ack.peer_verified_from_pipe_and_process_handles = false;
                pipe_ack.pid_table_only = true;
            }
            let bridge_target =
                &self.observation.processes[role_index(ProcessRole::BridgeListener)];
            let (shutdown_requested_at, accounting_read_at, eof_observed_at) =
                if self.observation.terminal.kind == TerminalKind::TimedOut {
                    (
                        self.observation.terminal.intent_recorded_at,
                        self.observation
                            .terminal
                            .intent_recorded_at
                            .saturating_add(1),
                        self.observation
                            .terminal
                            .intent_recorded_at
                            .saturating_add(2),
                    )
                } else {
                    (113, 115, 116)
                };
            let bridge_control_pipe = BridgeTargetControlPipeObservation {
                instance_id: prepared.bridge_control_pipe.instance_id,
                binding_digest: prepared.bridge_control_pipe.binding_digest,
                challenge_digest: prepared.bridge_control_pipe.challenge_digest,
                peer: bridge_target.key,
                peer_job_object_id: policy.job_object_id,
                peer_executable_digest: bridge_target.executable_digest,
                peer_image_identity: bridge_target.image_handle_identity,
                accepted_at: 29,
                adoption_ack_at: 35,
                shutdown_requested_at,
                accounting_read_at,
                eof_observed_at,
                accepted_connections: 1,
                peer_verified_from_pipe_and_process_handles: true,
                ack_then_shutdown_then_accounting_then_eof: true,
                replay_rejected: true,
                reconnect_rejected: true,
                service_handle_held_through_eof: true,
            };

            let mut listener_adoptions = policy
                .socket_policies
                .iter()
                .zip(&prepared.listeners)
                .filter(|(endpoint, _)| endpoint.role == SocketRole::App)
                .map(|(endpoint, lease)| {
                    let owner = &self.observation.processes[role_index(endpoint.owner_role)];
                    let mut ack = ListenerAdoptionAck {
                        role: endpoint.role,
                        local_port: endpoint.local_port,
                        listener_socket_object_id: lease.listener_socket_object_id,
                        owner: owner.key,
                        owner_job_object_id: policy.job_object_id,
                        owner_executable_digest: owner.executable_digest,
                        owner_image_identity: owner.image_handle_identity,
                        private_pipe_instance_id: prepared.pipe.instance_id,
                        adopted_at: self
                            .observation
                            .sockets
                            .iter()
                            .find(|socket| socket.role == endpoint.role)
                            .expect("fixture socket")
                            .listener_ready_at,
                        ack_binding_digest: [0; 32],
                        adopted_from_service_share: true,
                        ack_read_from_service_pipe_handle: true,
                        socket_object_identity_verified: true,
                        pid_table_only: false,
                        socket_adopted_from_share: true,
                        getsockname_verified: true,
                        type_and_protocol_verified: true,
                        socket_options_verified: true,
                        inner_live_bootstrap_version: INNER_LIVE_BOOTSTRAP_VERSION,
                        inner_live_bootstrap_digest: prepared.pipe.inner_live_bootstrap_digest,
                        inner_live_bootstrap_parsed: true,
                        ordinary_bind_attempted: false,
                        pipe_closed_after_ack: true,
                        pipe_reconnect_rejected: true,
                        wire_ack_digest_verified: true,
                        loopback_v4_only: true,
                        exclusive_address_use: true,
                        address_reuse_disabled: true,
                    };
                    ack.ack_binding_digest =
                        derive_listener_ack_binding(policy, lease, &ack, &prepared.pipe);
                    ack
                })
                .collect::<Vec<_>>();
            if self.hostile == HostileMode::ListenerPidOnly {
                listener_adoptions[0].socket_object_identity_verified = false;
                listener_adoptions[0].pid_table_only = true;
            }
            if self.hostile == HostileMode::BootstrapVersionDrift {
                listener_adoptions[0].inner_live_bootstrap_version = 5;
            }

            let http_finalized_at = self
                .observation
                .finalization
                .as_ref()
                .map(|value| value.finalized_at)
                .unwrap_or(self.observation.terminal.observed_at);
            let app_listener = prepared
                .listeners
                .iter()
                .find(|listener| listener.role == SocketRole::App)
                .expect("app listener");
            let mut processes = self.observation.processes.clone();
            if self.hostile == HostileMode::ProcessImageReplacement {
                processes[role_index(ProcessRole::Backend)].image_path_identity_at_terminal =
                    FileIdentity {
                        volume_serial: 999,
                        file_id: [0xee; 16],
                    };
            }
            let mut job = self.observation.job.clone();
            if self.hostile == HostileMode::UnexpectedJobChild {
                job.assigned_processes.push(ProcessKey {
                    pid: 999,
                    creation_time: 9_999,
                });
            }
            let mut bridge_proxy = BridgeProxyObservation {
                public_listener_socket_object_id: prepared
                    .bridge_proxy
                    .public_listener_socket_object_id,
                public_port: BRIDGE_LOOPBACK_PORT,
                target_listener_socket_object_id: prepared
                    .bridge_proxy
                    .target_listener_socket_object_id,
                target_port: prepared.bridge_proxy.target_port,
                target_owner: self.observation.processes[role_index(ProcessRole::BridgeListener)]
                    .key,
                target_owner_job_object_id: policy.job_object_id,
                target_owner_executable_digest: self.observation.processes
                    [role_index(ProcessRole::BridgeListener)]
                .executable_digest,
                target_owner_image_identity: self.observation.processes
                    [role_index(ProcessRole::BridgeListener)]
                .image_handle_identity,
                target_adopted_at: 35,
                target_adoption_binding_digest: [0; 32],
                target_socket_adopted_from_service_share: true,
                target_adoption_ack_read_from_service_launch_pipe: true,
                target_socket_object_identity_verified: true,
                service_owns_public_listener: true,
                target_identity_verified_from_socket_and_process_handles: true,
                pid_table_only: false,
                unity_bridge_launch_disabled: true,
                unity_connected_to_service_proxy: true,
                unexpected_bridge_launch_attempt: false,
                release_then_bind_used: false,
                target_ready_at: 35,
                public_proxy_enabled_at: 36,
                proxy_health_verified_at: 37,
                public_listener_hidden_until_target_ready: true,
                health_verified_through_proxy: true,
                explicit_http_and_websocket_semantic_proxy: true,
                request_auth_key_digest: prepared.bridge_proxy.request_auth_key_digest,
                request_auth_injected_by_service: true,
                controlled_health_request_count: 1,
                proxy_http_request_count: 1,
                proxy_websocket_request_count: 1,
                connections: vec![BridgeProxyConnectionObservation {
                    accepted_connection_object_id: 8_800,
                    target_connection_object_id: 8_801,
                    accepted_at: 92,
                    closed_at: 109,
                    byte_limit: 4 * 1024 * 1024,
                    idle_timeout_ms: 30_000,
                    http_request_count: 1,
                    websocket_request_count: 1,
                    semantic_request_parse_complete: true,
                    request_auth_injected: true,
                    response_or_websocket_close_complete: true,
                    both_handles_service_owned: true,
                }],
            };
            let bridge_target_lease = prepared
                .listeners
                .iter()
                .find(|listener| listener.role == SocketRole::Bridge)
                .expect("bridge target listener");
            bridge_proxy.target_adoption_binding_digest = derive_bridge_target_ack_binding(
                policy,
                bridge_target_lease,
                &prepared.bridge_control_pipe,
                &bridge_proxy,
            );
            if self.hostile == HostileMode::BridgeProxyExposedEarly {
                bridge_proxy.public_proxy_enabled_at = 34;
                bridge_proxy.public_listener_hidden_until_target_ready = false;
            }
            if self.hostile == HostileMode::BridgeRequestAuthDigestMismatch {
                bridge_proxy.request_auth_key_digest = digest(0xee);
            }
            NativeTerminalEvidence {
                bridge_root_launch: self
                    .observation
                    .launches
                    .iter()
                    .find(|launch| launch.role == ProcessRole::BridgeLauncher)
                    .expect("bridge root launch")
                    .clone(),
                processes,
                helpers: self.observation.helpers.clone(),
                job,
                pipe_ack,
                bridge_control_pipe,
                listener_adoptions,
                bridge_proxy,
                http_lifecycle: FixedHttpLifecycleReceipt {
                    contract_digest: derive_http_lifecycle_contract(policy),
                    ticket_digest: policy.ticket_digest,
                    run_binding_digest: policy.run_binding_digest,
                    private_pipe_instance_id: prepared.pipe.instance_id,
                    listener_socket_object_id: app_listener.listener_socket_object_id,
                    backend: backend.key,
                    started_at: 91,
                    finalized_at: http_finalized_at,
                    request_count: 5,
                    service_direct: true,
                    caller_requests_present: false,
                    exact_sequence_observed: true,
                    responses_read_from_service_connection_handles: true,
                },
                finalization: self.observation.finalization.clone(),
                terminal: self.observation.terminal.clone(),
            }
        }

        fn native_cleanup(&self, prepared: &NativePreparedEvidence) -> NativeCleanupReceipt {
            let accounting_read_at = if self.observation.terminal.kind == TerminalKind::TimedOut {
                self.observation
                    .terminal
                    .intent_recorded_at
                    .saturating_add(1)
            } else {
                115
            };
            let mut request_accounting = BridgeTargetRequestAccountingObservation {
                request_auth_key_digest: prepared.bridge_proxy.request_auth_key_digest,
                controlled_health_request_count: 1,
                proxy_http_request_count: 1,
                proxy_websocket_request_count: 1,
                total_target_request_count: 3,
                rejected_request_count: 0,
                bypass_request_count: 0,
                request_auth_header_stripped: true,
                observed_at_shutdown: accounting_read_at,
                read_from_adapter_shutdown_channel: true,
                accounting_digest: [0; 32],
            };
            if self.hostile == HostileMode::BridgeRejectedRequest {
                request_accounting.rejected_request_count = 1;
                request_accounting.total_target_request_count = 4;
            }
            if self.hostile == HostileMode::BridgeRequestCountMismatch {
                request_accounting.total_target_request_count = 4;
            }
            if self.hostile == HostileMode::BridgeAuthHeaderForwarded {
                request_accounting.request_auth_header_stripped = false;
            }
            request_accounting.accounting_digest =
                derive_bridge_target_request_accounting_digest(&request_accounting);
            let mut receipt = NativeCleanupReceipt {
                private_pipe_instance_id: Some(prepared.pipe.instance_id),
                private_pipe_closed: true,
                pipe_challenge_zeroed: true,
                no_pending_pipe_clients: true,
                pipe_replay_rejected: true,
                bridge_control_pipe_instance_id: Some(prepared.bridge_control_pipe.instance_id),
                bridge_control_pipe_closed: true,
                bridge_control_pipe_challenge_zeroed: true,
                bridge_control_pipe_no_pending_clients: true,
                bridge_control_pipe_replay_rejected: true,
                closed_service_listener_ids: prepared
                    .listeners
                    .iter()
                    .map(|listener| listener.listener_socket_object_id)
                    .collect(),
                all_candidate_listener_duplicates_closed: true,
                all_service_listener_handles_closed: true,
                completion_port_drained: true,
                no_inheritable_handle_residue: true,
                no_port_drift: true,
                bridge_proxy_listener_closed: true,
                bridge_proxy_connections_closed: true,
                bridge_target_listener_closed: true,
                bridge_request_auth_credentials_zeroized: true,
                bridge_target_request_accounting: Some(request_accounting),
                containment_readback_complete: true,
            };
            if self.hostile == HostileMode::BridgeRequestAuthNotZeroized {
                receipt.bridge_request_auth_credentials_zeroized = false;
            }
            receipt
        }

        fn full_abort(&self) -> AuthorityOwnedAbortObservation {
            let mut cleanup = self.observation.cleanup.clone();
            cleanup.final_result_persisted = false;
            AuthorityOwnedAbortObservation {
                ticket_consumed_at: self.observation.ticket_consumed_at,
                runner: Some(self.observation.runner.clone()),
                artifacts: self.observation.artifacts.clone(),
                launches: self.observation.launches.clone(),
                processes: self.observation.processes.clone(),
                helpers: self.observation.helpers.clone(),
                job: Some(self.observation.job.clone()),
                sockets: self.observation.sockets.clone(),
                terminal: TerminalObservation {
                    kind: TerminalKind::Failed,
                    observed_at: self.observation.terminal.observed_at,
                    intent: TerminalIntent::Burn,
                    intent_recorded_at: self.observation.terminal.intent_recorded_at,
                },
                cleanup,
            }
        }
    }

    fn staged_hash_parts(domain: &[u8], parts: &[&[u8]]) -> Digest {
        let mut digest = Sha256::new();
        digest.update(domain);
        for part in parts {
            digest.update((part.len() as u64).to_be_bytes());
            digest.update(part);
        }
        digest.finalize().into()
    }

    impl ServiceOwnedNativeApi for MockNativeApi {
        fn preflight(
            &mut self,
            _policy: &SupervisorPolicy,
        ) -> Result<NativeCapabilityReceipt, SupervisorError> {
            self.events.push(NativeSupervisorPhase::Preflight);
            let mut capabilities = NativeCapabilityReceipt::fully_connected();
            if self.hostile == HostileMode::MissingListenerCapability {
                capabilities.service_owned_listener_adoption = false;
            }
            if self.hostile == HostileMode::MissingBridgeCapability {
                capabilities.service_owned_bridge_proxy = false;
            }
            if self.hostile == HostileMode::MissingBridgeStartupCapability {
                capabilities.bridge_target_in_memory_startup = false;
            }
            if self.hostile == HostileMode::MissingBridgeRequestAuthCapability {
                capabilities.bridge_target_request_auth = false;
            }
            Ok(capabilities)
        }

        fn prepare(
            &mut self,
            policy: &SupervisorPolicy,
            prepared: &PreparedRecoveryReceipt,
            policy_snapshot: &[u8],
        ) -> Result<NativePreparedEvidence, SupervisorError> {
            self.events.push(NativeSupervisorPhase::Prepare);
            Ok(self.prepared_evidence(policy, prepared, policy_snapshot, digest(98)))
        }

        fn launch_root_suspended(
            &mut self,
            policy: &SupervisorPolicy,
            _prepared: &NativePreparedEvidence,
        ) -> Result<SuspendedRootReceipt, SupervisorError> {
            self.events.push(NativeSupervisorPhase::LaunchSuspended);
            Ok(self.suspended_root(policy))
        }

        fn assign_root_to_job(
            &mut self,
            policy: &SupervisorPolicy,
            _prepared: &NativePreparedEvidence,
            suspended: &SuspendedRootReceipt,
        ) -> Result<JobAssignmentReceipt, SupervisorError> {
            self.events.push(NativeSupervisorPhase::AssignJob);
            Ok(JobAssignmentReceipt {
                process: suspended.process,
                job_object_id: policy.job_object_id,
                membership_verified_at: 31,
                initial_assignment_call_performed: false,
                job_membership_revalidated: true,
                membership_readback_before_resume: true,
                assigned_using_process_and_job_handles: true,
                process_confirmed_job_member: true,
                completion_port_assignment_observed: true,
            })
        }

        fn resume_root(
            &mut self,
            policy: &SupervisorPolicy,
            prepared: &NativePreparedEvidence,
            suspended: &SuspendedRootReceipt,
            assignment: &JobAssignmentReceipt,
        ) -> Result<ResumedRootReceipt, SupervisorError> {
            self.events.push(NativeSupervisorPhase::Resume);
            Ok(ResumedRootReceipt {
                start_contract_digest: prepared.start_contract_digest,
                process: suspended.process,
                created_suspended_at: suspended.created_suspended_at,
                job_membership_verified_at: assignment.membership_verified_at,
                resumed_at: 32,
                job_object_id: policy.job_object_id,
                runner_identity_digest: policy.runner_identity_digest,
                child_transport_contract_digest: policy.child_transport_contract_digest,
                raw_handle_list: suspended.raw_handle_list,
                all_other_handles_non_inheritable: true,
                breakaway_requested: false,
            })
        }

        fn bind_admission_after_arm(
            &mut self,
            _policy: &SupervisorPolicy,
            prepared: &NativePreparedEvidence,
            armed: &ArmedRecoveryReceipt,
            policy_snapshot: &[u8],
        ) -> Result<NativeArmedAdmissionReceipt, SupervisorError> {
            let mut receipt = NativeArmedAdmissionReceipt {
                prepared_receipt_digest: prepared.admission.prepared_receipt_digest,
                armed_receipt_digest: Sha256::digest(armed.encode()).into(),
                policy_snapshot_digest: Sha256::digest(policy_snapshot).into(),
                recovery_bundle_digest: prepared.admission.recovery_bundle_digest,
                read_from_authority_store: true,
                sealed_by_service: true,
            };
            if self.hostile == HostileMode::ArmedAdmissionMismatch {
                receipt.armed_receipt_digest = digest(0xf1);
                receipt.policy_snapshot_digest = digest(0xf2);
                receipt.recovery_bundle_digest = digest(0xf3);
            }
            self.stage_armed_admission = Some(receipt.clone());
            Ok(receipt)
        }

        fn request_armed_termination(
            &mut self,
            policy: &SupervisorPolicy,
            prepared: &NativePreparedEvidence,
            armed: &ArmedRecoveryReceipt,
        ) -> Result<NativeArmedTerminationAttempt, SupervisorError> {
            if self.armed_termination_uncertain_once {
                self.armed_termination_uncertain_once = false;
                return Ok(NativeArmedTerminationAttempt::Uncertain);
            }
            let kind = self.armed_termination_kind;
            let (requested_at_unix_ms, recorded_at_unix_ms) = match kind {
                NativeTerminationKind::Cancelled => (33_000, 34_000),
                NativeTerminationKind::TimedOut => (
                    policy.deadline.saturating_mul(1_000),
                    policy.deadline.saturating_mul(1_000).saturating_add(1),
                ),
            };
            Ok(NativeArmedTerminationAttempt::Recorded(
                NativeTerminationIntentReceipt::from_service_journal_readback(
                    policy,
                    prepared.admission.prepared_receipt_digest,
                    armed,
                    prepared.admission.policy_snapshot_digest,
                    kind,
                    requested_at_unix_ms,
                    recorded_at_unix_ms,
                    6,
                    digest(0xe1),
                ),
            ))
        }

        fn poll_terminal(
            &mut self,
            policy: &SupervisorPolicy,
            prepared: &NativePreparedEvidence,
            _armed: &ArmedRecoveryReceipt,
        ) -> Result<NativeTerminalPoll, SupervisorError> {
            self.events.push(NativeSupervisorPhase::ObserveTerminal);
            self.terminal_polls += 1;
            if self.hostile == HostileMode::TerminalPendingOnce && self.terminal_polls == 1 {
                return Ok(NativeTerminalPoll::Running);
            }
            if self.hostile == HostileMode::TerminalFailure {
                return Err(SupervisorError::new(
                    "authority_native_terminal_observation_failed",
                ));
            }
            Ok(NativeTerminalPoll::Terminal(
                self.terminal_evidence(policy, prepared),
            ))
        }

        fn contain_terminal(
            &mut self,
            _policy: &SupervisorPolicy,
            prepared: &NativePreparedEvidence,
            _terminal: &NativeTerminalEvidence,
        ) -> Result<NativeCleanupEvidence, SupervisorError> {
            self.events.push(NativeSupervisorPhase::Contain);
            let mut native = self.native_cleanup(prepared);
            if self.hostile == HostileMode::PortDrift {
                native.no_port_drift = false;
            }
            if self.hostile == HostileMode::HandleResidue {
                native.no_inheritable_handle_residue = false;
            }
            Ok(NativeCleanupEvidence {
                sockets: self.observation.sockets.clone(),
                cleanup: self.observation.cleanup.clone(),
                native,
            })
        }

        fn seal_origin_after_cleanup(
            &mut self,
            policy: &SupervisorPolicy,
            _prepared: &NativePreparedEvidence,
            _terminal: &NativeTerminalEvidence,
            _cleanup: &NativeCleanupEvidence,
            completed: &CompletedRunProof,
            admission: &NativeAdmissionBinding,
        ) -> Result<NativeOriginEnvelopeReceipt, SupervisorError> {
            self.events.push(NativeSupervisorPhase::Finalize);
            self.seal_calls += 1;
            let ticket = serde_json::json!({
                "issuedAt": "2026-07-27T00:00:00.000000Z",
                "runId": "native-supervisor-test"
            });
            let ticket_bytes = serde_json::to_vec(&ticket).unwrap();
            let canonical_ticket_digest: Digest = Sha256::digest(&ticket_bytes).into();
            let mut origin_ticket_digest = canonical_ticket_digest;
            let mut authority_ticket_digest = policy.ticket_digest;
            match self.hostile {
                HostileMode::OriginAuthorityTicketMismatch => {
                    authority_ticket_digest = digest(0xfc)
                }
                HostileMode::OriginTicketDigestMismatch => origin_ticket_digest = digest(0xfd),
                HostileMode::OriginTicketsSwapped => {
                    origin_ticket_digest = policy.ticket_digest;
                    authority_ticket_digest = canonical_ticket_digest;
                }
                _ => {}
            }
            let canonical_bytes = serde_json::to_vec(&serde_json::json!({
                "authorityTicketDigest": native_test_digest_hex(&authority_ticket_digest),
                "cleanupDigest": native_test_digest_hex(completed.cleanup_receipt_digest()),
                "schema": ORIGIN_ENVELOPE_SCHEMA_V2,
                "ticket": ticket,
                "ticketDigest": native_test_digest_hex(&origin_ticket_digest),
            }))
            .unwrap();
            let canonical_digest = Sha256::digest(&canonical_bytes).into();
            let mut result_digest = *completed.result_digest();
            if self.hostile == HostileMode::OriginBindingMismatch {
                result_digest = digest(0xfe);
            }
            Ok(NativeOriginEnvelopeReceipt {
                canonical_bytes,
                canonical_digest,
                origin_ticket_digest,
                authority_ticket_digest,
                result_digest,
                cleanup_receipt_digest: *completed.cleanup_receipt_digest(),
                admission_binding_digest: *admission.binding_digest(),
                cleanup_observed_at: completed.cleanup_observed_at(),
                sealed_at: completed.cleanup_observed_at() + 1,
                built_from_service_held_evidence: true,
                signed_by_service_after_cleanup: true,
                caller_material_present: false,
            })
        }

        fn contain_after_failure(
            &mut self,
            _policy: &SupervisorPolicy,
            _prepared: &PreparedRecoveryReceipt,
            _armed: Option<&ArmedRecoveryReceipt>,
            phase: NativeSupervisorPhase,
            _reason: BurnReason,
            failure_code: &'static str,
        ) -> Result<NativeAbortEvidence, SupervisorError> {
            self.events.push(NativeSupervisorPhase::Contain);
            self.containment_calls += 1;
            if self.failure_containment_uncertain_once {
                self.failure_containment_uncertain_once = false;
                return Err(SupervisorError::new(
                    "test_native_failure_containment_uncertain",
                ));
            }
            Ok(NativeAbortEvidence {
                failed_phase: phase,
                failure_code,
                observation: self.full_abort(),
                native_cleanup: NativeCleanupReceipt {
                    private_pipe_instance_id: Some(7_000),
                    private_pipe_closed: true,
                    pipe_challenge_zeroed: true,
                    no_pending_pipe_clients: true,
                    pipe_replay_rejected: true,
                    bridge_control_pipe_instance_id: Some(7_001),
                    bridge_control_pipe_closed: true,
                    bridge_control_pipe_challenge_zeroed: true,
                    bridge_control_pipe_no_pending_clients: true,
                    bridge_control_pipe_replay_rejected: true,
                    closed_service_listener_ids: vec![800, 801],
                    all_candidate_listener_duplicates_closed: true,
                    all_service_listener_handles_closed: true,
                    completion_port_drained: true,
                    no_inheritable_handle_residue: true,
                    no_port_drift: true,
                    bridge_proxy_listener_closed: true,
                    bridge_proxy_connections_closed: true,
                    bridge_target_listener_closed: true,
                    bridge_request_auth_credentials_zeroized: true,
                    bridge_target_request_accounting: None,
                    containment_readback_complete: true,
                },
            })
        }

        fn recover_after_restart(
            &mut self,
            _policy: &SupervisorPolicy,
            _prepared: &PreparedRecoveryReceipt,
            _armed: Option<&ArmedRecoveryReceipt>,
            _policy_snapshot: &[u8],
            _reason: BurnReason,
        ) -> Result<NativeRestartRecoveryEvidence, SupervisorError> {
            if let Some(completed) = self.restart_completed.take() {
                return Ok(NativeRestartRecoveryEvidence::Completed(completed));
            }
            if let Some(normal) = self.restart_normal.take() {
                return Ok(NativeRestartRecoveryEvidence::NormalTerminated(normal));
            }
            self.events.push(NativeSupervisorPhase::Contain);
            self.containment_calls += 1;
            Ok(NativeRestartRecoveryEvidence::Burned(NativeAbortEvidence {
                failed_phase: NativeSupervisorPhase::Contain,
                failure_code: "authority_native_restart_recovery",
                observation: self.full_abort(),
                native_cleanup: NativeCleanupReceipt {
                    private_pipe_instance_id: Some(7_000),
                    private_pipe_closed: true,
                    pipe_challenge_zeroed: true,
                    no_pending_pipe_clients: true,
                    pipe_replay_rejected: true,
                    bridge_control_pipe_instance_id: Some(7_001),
                    bridge_control_pipe_closed: true,
                    bridge_control_pipe_challenge_zeroed: true,
                    bridge_control_pipe_no_pending_clients: true,
                    bridge_control_pipe_replay_rejected: true,
                    closed_service_listener_ids: vec![800, 801],
                    all_candidate_listener_duplicates_closed: true,
                    all_service_listener_handles_closed: true,
                    completion_port_drained: true,
                    no_inheritable_handle_residue: true,
                    no_port_drift: true,
                    bridge_proxy_listener_closed: true,
                    bridge_proxy_connections_closed: true,
                    bridge_target_listener_closed: true,
                    bridge_request_auth_credentials_zeroized: true,
                    bridge_target_request_accounting: None,
                    containment_readback_complete: true,
                },
            }))
        }
    }

    impl ServiceOwnedStagedNativeApi for MockNativeApi {
        fn create_stage_journal(
            &mut self,
            _policy: &SupervisorPolicy,
            canonical_bytes: &[u8],
            mode: staged_start::NativeStageJournalCreateMode,
        ) -> staged_start::NativeStageJournalCreateOutcome {
            self.stage_create_requests += 1;
            if self.stage_create_rejected_no_mutation_once {
                self.stage_create_rejected_no_mutation_once = false;
                return staged_start::NativeStageJournalCreateOutcome::RejectedNoMutation(
                    "test_stage_create_rejected_no_mutation",
                );
            }
            match mode {
                staged_start::NativeStageJournalCreateMode::Create => {
                    if !self.stage_bytes.is_empty() {
                        return staged_start::NativeStageJournalCreateOutcome::RejectedNoMutation(
                            "test_stage_create_conflict",
                        );
                    }
                    self.stage_create_actions += 1;
                    self.stage_bytes = canonical_bytes.to_vec();
                    self.stage_write_sequence = 1;
                    let readback = self.stage_store_readback(true, true);
                    if self.stage_create_uncertain_after_write_once {
                        self.stage_create_uncertain_after_write_once = false;
                        return staged_start::NativeStageJournalCreateOutcome::Uncertain(
                            staged_start::NativeStageJournalCreateUncertainty {
                                file_identity: readback.file_identity,
                                parent_identity: readback.parent_identity,
                                held_handle_binding_digest:
                                    staged_start::stage_journal_held_handle_binding_digest(
                                        canonical_bytes,
                                        &readback.file_identity,
                                        &readback.parent_identity,
                                    ),
                            },
                            "test_stage_create_uncertain",
                        );
                    }
                    staged_start::NativeStageJournalCreateOutcome::Created(readback)
                }
                staged_start::NativeStageJournalCreateMode::ReconcileOnly {
                    held_handle_binding_digest,
                } => {
                    if self.stage_bytes.is_empty() || self.stage_bytes != canonical_bytes {
                        return staged_start::NativeStageJournalCreateOutcome::RejectedNoMutation(
                            "test_stage_create_reopen_conflict",
                        );
                    }
                    let mut readback = self.stage_store_readback(false, true);
                    let actual_binding = staged_start::stage_journal_held_handle_binding_digest(
                        canonical_bytes,
                        &readback.file_identity,
                        &readback.parent_identity,
                    );
                    if actual_binding != held_handle_binding_digest {
                        return staged_start::NativeStageJournalCreateOutcome::RejectedNoMutation(
                            "test_stage_create_held_handle_mismatch",
                        );
                    }
                    match self.stage_create_reopen_fault {
                        StageCreateReopenFault::None => {}
                        StageCreateReopenFault::Bytes => readback.canonical_bytes.push(0),
                        StageCreateReopenFault::FileIdentity => {
                            readback.file_identity.file_id[0] ^= 0xff;
                        }
                        StageCreateReopenFault::Security => {
                            readback.protected_restricted_dacl = false;
                        }
                        StageCreateReopenFault::CreatedNew => readback.created_new = true,
                        StageCreateReopenFault::HeldHandle => {
                            readback.reopened_from_held_handle = false;
                        }
                    }
                    staged_start::NativeStageJournalCreateOutcome::Reopened(readback)
                }
            }
        }

        fn append_stage_journal(
            &mut self,
            _policy: &SupervisorPolicy,
            prior_byte_len: usize,
            record_bytes: &[u8],
        ) -> Result<staged_start::NativeStageJournalStoreReadback, SupervisorError> {
            self.append_stage_exact(prior_byte_len, record_bytes)
        }

        fn prepare_foundation(
            &mut self,
            policy: &SupervisorPolicy,
            prepared: &PreparedRecoveryReceipt,
            policy_snapshot: &[u8],
            start_contract: &VerifiedScenarioStartContract,
        ) -> Result<NativePreparedFoundation, SupervisorError> {
            self.events.push(NativeSupervisorPhase::Prepare);
            let evidence = self.prepared_evidence(
                policy,
                prepared,
                policy_snapshot,
                start_contract.binding_digest(),
            );
            let foundation = evidence.foundation.clone();
            self.stage_prepared = Some(evidence);
            Ok(foundation)
        }

        fn create_root_suspended_in_job(
            &mut self,
            policy: &SupervisorPolicy,
            prepared: &NativePreparedFoundation,
            executable: &VerifiedScenarioExecutableLaunch<'_>,
            role: ProcessRole,
        ) -> Result<staged_start::NativeCreatedRootReceipt, SupervisorError> {
            if executable.start_contract_digest() != prepared.start_contract_digest
                || (role == ProcessRole::Driver
                    && executable.role() != ScenarioStartExecutableRole::Driver)
                || (role == ProcessRole::BridgeLauncher
                    && executable.role() != ScenarioStartExecutableRole::BridgeLauncher)
            {
                return Err(SupervisorError::new("test_stage_start_executable_mismatch"));
            }
            let receipt = match role {
                ProcessRole::BridgeLauncher => {
                    let prepared = self
                        .stage_prepared
                        .as_ref()
                        .ok_or_else(|| SupervisorError::new("test_stage_prepared_missing"))?;
                    let suspended = prepared.bridge_root.suspended.clone();
                    staged_start::NativeCreatedRootReceipt {
                        start_contract_digest: prepared.start_contract_digest,
                        executable_binding:
                            crate::primitive_evidence_authority_pipe::VerifiedScenarioExecutableCreateBinding::valid_for_launch_for_test(
                                executable,
                                file_identity_digest(&suspended.image_identity),
                            )
                            .map_err(|error| SupervisorError::new(error.code()))?,
                        suspended,
                        membership: prepared.bridge_root.membership.clone(),
                    }
                }
                ProcessRole::Driver => {
                    let suspended = self.suspended_root(policy);
                    staged_start::NativeCreatedRootReceipt {
                        start_contract_digest: prepared.start_contract_digest,
                        executable_binding:
                            crate::primitive_evidence_authority_pipe::VerifiedScenarioExecutableCreateBinding::valid_for_launch_for_test(
                                executable,
                                file_identity_digest(&suspended.image_identity),
                            )
                            .map_err(|error| SupervisorError::new(error.code()))?,
                        membership: JobAssignmentReceipt {
                            process: suspended.process,
                            job_object_id: policy.job_object_id,
                            membership_verified_at: 31,
                            initial_assignment_call_performed: false,
                            job_membership_revalidated: true,
                            membership_readback_before_resume: true,
                            assigned_using_process_and_job_handles: true,
                            process_confirmed_job_member: true,
                            completion_port_assignment_observed: true,
                        },
                        suspended,
                    }
                }
                _ => return Err(SupervisorError::new("test_stage_root_role_invalid")),
            };
            if role == ProcessRole::BridgeLauncher {
                self.stage_bridge_created = Some(receipt.clone());
            }
            if role == ProcessRole::Driver {
                self.stage_driver_created = Some(receipt.clone());
            }
            self.stage_created_roles.push(role);
            Ok(receipt)
        }

        fn resume_staged_root(
            &mut self,
            policy: &SupervisorPolicy,
            prepared: &NativePreparedFoundation,
            start_contract: &VerifiedScenarioStartContract,
            role: ProcessRole,
            created: &staged_start::NativeCreatedRootReceipt,
        ) -> Result<ResumedRootReceipt, SupervisorError> {
            if start_contract.binding_digest() != prepared.start_contract_digest {
                return Err(SupervisorError::new("test_stage_start_contract_mismatch"));
            }
            let resumed = match role {
                ProcessRole::BridgeLauncher => self
                    .stage_prepared
                    .as_ref()
                    .ok_or_else(|| SupervisorError::new("test_stage_prepared_missing"))?
                    .bridge_root
                    .resumed
                    .clone(),
                ProcessRole::Driver => ResumedRootReceipt {
                    start_contract_digest: prepared.start_contract_digest,
                    process: created.suspended.process,
                    created_suspended_at: created.suspended.created_suspended_at,
                    job_membership_verified_at: created.membership.membership_verified_at,
                    resumed_at: 32,
                    job_object_id: policy.job_object_id,
                    runner_identity_digest: policy.runner_identity_digest,
                    child_transport_contract_digest: policy.child_transport_contract_digest,
                    raw_handle_list: created.suspended.raw_handle_list,
                    all_other_handles_non_inheritable: true,
                    breakaway_requested: false,
                },
                _ => return Err(SupervisorError::new("test_stage_root_role_invalid")),
            };
            if role == ProcessRole::Driver {
                self.stage_driver_resumed = Some(resumed.clone());
            }
            self.stage_resumed_roles.push(role);
            Ok(resumed)
        }

        fn record_stage_termination(
            &mut self,
            policy: &SupervisorPolicy,
            journal: &stage_journal::StageJournal,
            armed_receipt_digest: Option<Digest>,
        ) -> Result<staged_start::NativeStageTerminationAttempt, SupervisorError> {
            let kind = self.armed_termination_kind;
            let configured_timing = match (
                self.stage_termination_requested_at_ms,
                self.stage_termination_recorded_at_ms,
            ) {
                (Some(requested), Some(recorded)) => Some((requested, recorded)),
                (None, None) => None,
                _ => return Err(SupervisorError::new("test_stage_termination_time_partial")),
            };
            let (requested_at_unix_ms, recorded_at_unix_ms) =
                configured_timing.unwrap_or_else(|| match kind {
                    NativeTerminationKind::Cancelled if armed_receipt_digest.is_some() => {
                        (33_000, 34_000)
                    }
                    NativeTerminationKind::Cancelled => (
                        policy.issued_at.saturating_mul(1_000).saturating_add(1),
                        policy.issued_at.saturating_mul(1_000).saturating_add(2),
                    ),
                    NativeTerminationKind::TimedOut => (
                        policy.deadline.saturating_mul(1_000),
                        policy.deadline.saturating_mul(1_000).saturating_add(1),
                    ),
                });
            let stage_kind = match kind {
                NativeTerminationKind::Cancelled => stage_journal::StageTerminationKind::Cancelled,
                NativeTerminationKind::TimedOut => stage_journal::StageTerminationKind::TimedOut,
            };
            let prior_head = journal.head().record_digest;
            let append = journal
                .plan_termination_intent(
                    stage_kind,
                    requested_at_unix_ms,
                    recorded_at_unix_ms,
                    armed_receipt_digest,
                )
                .map_err(|error| SupervisorError::new(error.code()))?;
            let readback =
                self.append_stage_exact(append.prior_byte_len(), append.record_bytes())?;
            let reopened = journal
                .verify_reopened_append(&append, &readback.canonical_bytes)
                .map_err(|error| SupervisorError::new(error.code()))?;
            self.stage_termination_prior_head = Some(prior_head);
            self.stage_termination_intent_digest = Some(reopened.head().record_digest);
            self.stage_termination_requested_at_ms = Some(requested_at_unix_ms);
            self.stage_termination_recorded_at_ms = Some(recorded_at_unix_ms);
            if self.stage_termination_uncertain_after_write_once {
                self.stage_termination_uncertain_after_write_once = false;
                return Ok(staged_start::NativeStageTerminationAttempt::Uncertain);
            }
            Ok(staged_start::NativeStageTerminationAttempt::Recorded(
                staged_start::NativeStageTerminationReadback {
                    kind,
                    requested_at_unix_ms,
                    recorded_at_unix_ms,
                    journal: readback,
                },
            ))
        }

        fn contain_starting_termination(
            &mut self,
            policy: &SupervisorPolicy,
            _prepared: &PreparedRecoveryReceipt,
            _journal: &stage_journal::StageJournal,
            kind: NativeTerminationKind,
        ) -> Result<staged_start::NativeStartingTerminationEvidence, SupervisorError> {
            self.stage_containment_calls += 1;
            if self.stage_containment_uncertain_once {
                self.stage_containment_uncertain_once = false;
                return Err(SupervisorError::new("test_stage_containment_uncertain"));
            }
            let mut observation = empty_abort_observation(policy);
            observation.terminal.kind = match kind {
                NativeTerminationKind::Cancelled => TerminalKind::Cancelled,
                NativeTerminationKind::TimedOut => TerminalKind::TimedOut,
            };
            if kind == NativeTerminationKind::TimedOut {
                observation.terminal.observed_at = policy.deadline.saturating_add(1);
                observation.terminal.intent_recorded_at = policy.deadline.saturating_add(2);
                observation.cleanup.observed_at = policy.deadline.saturating_add(3);
                for socket in &mut observation.cleanup.sockets {
                    socket.listener_exit_observed_at = observation.terminal.intent_recorded_at;
                    socket.exclusive_rebind_observed_at = observation.cleanup.observed_at;
                }
            }
            let stage_head = self
                .stage_termination_prior_head
                .ok_or_else(|| SupervisorError::new("test_stage_termination_head_missing"))?;
            let intent = self
                .stage_termination_intent_digest
                .ok_or_else(|| SupervisorError::new("test_stage_termination_intent_missing"))?;
            let requested = self
                .stage_termination_requested_at_ms
                .ok_or_else(|| SupervisorError::new("test_stage_termination_time_missing"))?;
            let recorded = self
                .stage_termination_recorded_at_ms
                .ok_or_else(|| SupervisorError::new("test_stage_termination_time_missing"))?;
            let kind_code = [match kind {
                NativeTerminationKind::Cancelled => 1,
                NativeTerminationKind::TimedOut => 2,
            }];
            let requested_bytes = requested.to_be_bytes();
            let recorded_bytes = recorded.to_be_bytes();
            let observed_bytes = observation.terminal.observed_at.to_be_bytes();
            let intent_recorded_bytes = observation.terminal.intent_recorded_at.to_be_bytes();
            let terminal_digest = staged_hash_parts(
                b"vrcforge-native-stage-terminal-observation-v1\0",
                &[
                    &policy.authority_identity_digest,
                    &policy.ticket_digest,
                    &policy.run_binding_digest,
                    &kind_code,
                    &requested_bytes,
                    &recorded_bytes,
                    &stage_head,
                    &intent,
                    &observed_bytes,
                    &intent_recorded_bytes,
                ],
            );
            let reason = match kind {
                NativeTerminationKind::Cancelled => BurnReason::Cancelled,
                NativeTerminationKind::TimedOut => BurnReason::TimedOut,
            };
            let cleanup_digest = derive_abort_cleanup_receipt(policy, &observation, reason);
            let evidence = staged_start::NativeStartingTerminationEvidence {
                observation,
                native_cleanup: NativeCleanupReceipt {
                    private_pipe_instance_id: None,
                    private_pipe_closed: true,
                    pipe_challenge_zeroed: true,
                    no_pending_pipe_clients: true,
                    pipe_replay_rejected: true,
                    bridge_control_pipe_instance_id: None,
                    bridge_control_pipe_closed: true,
                    bridge_control_pipe_challenge_zeroed: true,
                    bridge_control_pipe_no_pending_clients: true,
                    bridge_control_pipe_replay_rejected: true,
                    closed_service_listener_ids: Vec::new(),
                    all_candidate_listener_duplicates_closed: true,
                    all_service_listener_handles_closed: true,
                    completion_port_drained: true,
                    no_inheritable_handle_residue: true,
                    no_port_drift: true,
                    bridge_proxy_listener_closed: true,
                    bridge_proxy_connections_closed: true,
                    bridge_target_listener_closed: true,
                    bridge_request_auth_credentials_zeroized: true,
                    bridge_target_request_accounting: None,
                    containment_readback_complete: true,
                },
                terminal_digest,
                cleanup_digest,
            };
            self.stage_normal_evidence = Some(evidence.clone());
            Ok(evidence)
        }
    }

    fn execute(
        hostile: HostileMode,
    ) -> (
        Result<ValidatedNativeTerminalRun, SupervisorError>,
        MockNativeApi,
    ) {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut supervisor =
            ServiceOwnedNativeSupervisor::new(MockNativeApi::new(&policy, hostile));
        let result = supervisor.execute(prepared);
        (result, supervisor.api)
    }

    fn finish_staged_terminal(
        supervisor: &mut ServiceOwnedNativeSupervisor<MockNativeApi>,
        mut starting: NativeStartingRun,
        start_contract: &TestStartContract,
    ) -> NativeBurnedRunProof {
        for _ in 0..16 {
            match advance_staged_for_test(supervisor, starting, start_contract) {
                NativeStartingAdvance::Starting(next)
                | NativeStartingAdvance::Retrying(next, _) => starting = next,
                NativeStartingAdvance::Terminal(ValidatedNativeTerminalRun::Burned(proof)) => {
                    return proof;
                }
                NativeStartingAdvance::Terminal(ValidatedNativeTerminalRun::Completed(_)) => {
                    panic!("a Starting termination can never complete")
                }
                NativeStartingAdvance::Armed(_) => {
                    panic!("a terminated Starting run cannot cross the Armed barrier")
                }
            }
        }
        panic!("staged terminal did not converge within the bounded test loop")
    }

    fn finish_staged_armed(
        supervisor: &mut ServiceOwnedNativeSupervisor<MockNativeApi>,
        prepared: PreparedRun,
    ) -> NativeArmedRun {
        let (mut starting, start_contract) = begin_staged_for_test(supervisor, prepared);
        for _ in 0..24 {
            match advance_staged_for_test(supervisor, starting, &start_contract) {
                NativeStartingAdvance::Starting(next)
                | NativeStartingAdvance::Retrying(next, _) => starting = next,
                NativeStartingAdvance::Armed(armed) => return armed,
                NativeStartingAdvance::Terminal(_) => {
                    panic!("healthy staged start unexpectedly terminated")
                }
            }
        }
        panic!("staged start did not arm within the bounded test loop")
    }

    #[derive(Clone)]
    struct RecoveredNormalFixture {
        prepared: PreparedRecoveryReceipt,
        armed: Option<ArmedRecoveryReceipt>,
        policy_snapshot: Vec<u8>,
        evidence: NativeRecoveredNormalTerminationEvidence,
    }

    fn recovered_stage_journal_evidence(
        api: &MockNativeApi,
    ) -> NativeRecoveredStageJournalEvidence {
        let readback = api.stage_store_readback(false, false);
        NativeRecoveredStageJournalEvidence {
            sealed_file_identity: readback.file_identity,
            sealed_parent_identity: readback.parent_identity,
            sealed_held_handle_binding_digest:
                staged_start::stage_journal_held_handle_binding_digest(
                    &readback.canonical_bytes,
                    &readback.file_identity,
                    &readback.parent_identity,
                ),
            readback,
        }
    }

    fn recovered_stage_actions(api: &MockNativeApi) -> NativeRecoveredStageActionEvidence {
        let prepared = api.stage_prepared.as_ref();
        NativeRecoveredStageActionEvidence {
            prepared: prepared.map(|evidence| evidence.foundation.clone()),
            bridge_created: prepared.and_then(|_evidence| {
                api.stage_created_roles
                    .contains(&ProcessRole::BridgeLauncher)
                    .then(|| api.stage_bridge_created.clone())
                    .flatten()
            }),
            bridge_resumed: prepared.and_then(|evidence| {
                api.stage_resumed_roles
                    .contains(&ProcessRole::BridgeLauncher)
                    .then(|| evidence.bridge_root.resumed.clone())
            }),
            driver_created: api.stage_driver_created.clone(),
            driver_resumed: api.stage_driver_resumed.clone(),
            armed_admission: api.stage_armed_admission.clone(),
        }
    }

    #[derive(Clone)]
    struct RecoveredCompletedFixture {
        prepared: PreparedRecoveryReceipt,
        armed: ArmedRecoveryReceipt,
        policy_snapshot: Vec<u8>,
        evidence: NativeRecoveredCompletedEvidence,
        expected_stage: NativeCompletedStageJournalBinding,
    }

    fn recovered_completed_fixture() -> RecoveredCompletedFixture {
        let policy = policy();
        let prepared_run = PreparedRun::from_policy(&policy);
        let prepared = prepared_run.receipt().clone();
        let policy_snapshot = prepared_run.policy_snapshot().to_vec();
        let mut supervisor =
            ServiceOwnedNativeSupervisor::new(MockNativeApi::new(&policy, HostileMode::None));
        let armed_run = finish_staged_armed(&mut supervisor, prepared_run);
        let armed = armed_run.armed.clone();
        let native_prepared = armed_run.native_prepared.clone();
        let suspended = armed_run.suspended.clone();
        let launch = armed_run.launch.clone();
        let armed_admission = supervisor
            .api()
            .stage_armed_admission
            .clone()
            .expect("staged Armed admission");
        let journal = recovered_stage_journal_evidence(supervisor.api());
        let actions = recovered_stage_actions(supervisor.api());
        let completed_stage = verified_live_completed_stage_journal(
            &policy,
            &armed,
            native_prepared.start_contract_digest,
            armed_run
                .stage_journal
                .as_ref()
                .expect("staged Armed journal"),
        )
        .expect("clean Armed journal must bind completion");
        let terminal = supervisor
            .api()
            .terminal_evidence(&policy, &native_prepared);
        let cleanup = NativeCleanupEvidence {
            sockets: supervisor.api().observation.sockets.clone(),
            cleanup: supervisor.api().observation.cleanup.clone(),
            native: supervisor.api().native_cleanup(&native_prepared),
        };
        validate_native_terminal(&policy, &native_prepared, &suspended, &launch, &terminal)
            .expect("fixture terminal must bind the staged launch");
        validate_native_cleanup(&policy, &native_prepared, &terminal, &cleanup)
            .expect("fixture cleanup must bind the staged launch");
        let observation = AuthorityOwnedRunObservation {
            ticket_consumed_at: native_prepared.ticket_consumed_at,
            runner: native_prepared.runner.clone(),
            artifacts: native_prepared.artifacts.clone(),
            launches: vec![terminal.bridge_root_launch.clone(), launch.clone()],
            processes: terminal.processes.clone(),
            helpers: terminal.helpers.clone(),
            job: terminal.job.clone(),
            sockets: cleanup.sockets.clone(),
            finalization: terminal.finalization.clone(),
            terminal: terminal.terminal.clone(),
            cleanup: cleanup.cleanup.clone(),
        };
        let completed = match validate_authority_owned_run(&policy, &observation)
            .expect("fixture observation must validate")
        {
            ValidatedTerminalRun::Completed(completed) => completed,
            ValidatedTerminalRun::Burned(_) => panic!("fixture must complete"),
        };
        let origin = supervisor
            .api
            .seal_origin_after_cleanup(
                &policy,
                &native_prepared,
                &terminal,
                &cleanup,
                &completed,
                &armed_run.admission,
            )
            .expect("fixture origin");
        RecoveredCompletedFixture {
            prepared,
            armed,
            policy_snapshot,
            evidence: NativeRecoveredCompletedEvidence {
                journal,
                actions,
                completed_stage,
                native_prepared,
                suspended,
                launch,
                terminal,
                cleanup,
                armed_admission,
                origin,
                external_actions_replayed: false,
            },
            expected_stage: completed_stage,
        }
    }

    fn recover_completed_fixture(
        fixture: RecoveredCompletedFixture,
    ) -> (
        Result<ValidatedNativeTerminalRun, SupervisorError>,
        MockNativeApi,
    ) {
        let policy = decode_supervisor_policy_snapshot(&fixture.policy_snapshot)
            .expect("fixture policy snapshot must decode");
        let mut api = MockNativeApi::new(&policy, HostileMode::None);
        api.restart_completed = Some(fixture.evidence);
        let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
        let result = supervisor.recover_after_restart(
            &fixture.prepared,
            Some(&fixture.armed),
            &fixture.policy_snapshot,
        );
        (result, supervisor.api)
    }

    fn recovered_normal_receipt(
        policy: &SupervisorPolicy,
        prepared: &PreparedRecoveryReceipt,
        policy_snapshot: &[u8],
        journal: &NativeRecoveredStageJournalEvidence,
    ) -> NativeRecoveredNormalTerminationReceipt {
        let (_, material, _) = verified_recovered_normal_termination_material(
            policy,
            prepared,
            policy_snapshot,
            journal,
        )
        .expect("completed staged normal termination must replay");
        NativeRecoveredNormalTerminationReceipt::from_verified_replay(material)
    }

    fn configure_normal_terminal(
        api: &mut MockNativeApi,
        policy: &SupervisorPolicy,
        kind: NativeTerminationKind,
    ) {
        api.armed_termination_kind = kind;
        api.observation.finalization = None;
        api.observation.terminal.kind = match kind {
            NativeTerminationKind::Cancelled => TerminalKind::Cancelled,
            NativeTerminationKind::TimedOut => TerminalKind::TimedOut,
        };
        api.observation.terminal.intent = TerminalIntent::Burn;
        api.observation.cleanup.final_result_persisted = false;
        if kind == NativeTerminationKind::TimedOut {
            api.observation.terminal.observed_at = policy.deadline.saturating_add(1);
            api.observation.terminal.intent_recorded_at = policy.deadline.saturating_add(2);
            api.observation.cleanup.observed_at = policy.deadline.saturating_add(5);
            for socket in &mut api.observation.sockets {
                socket.ownership_verifications[3].observed_at =
                    api.observation.terminal.intent_recorded_at;
                socket.ownership_verifications[4].observed_at = api.observation.cleanup.observed_at;
            }
            for socket in &mut api.observation.cleanup.sockets {
                socket.listener_exit_observed_at = api.observation.terminal.intent_recorded_at;
                socket.exclusive_rebind_observed_at = api.observation.cleanup.observed_at;
            }
        }
    }

    fn recovered_pre_armed_fixture(kind: NativeTerminationKind) -> RecoveredNormalFixture {
        recovered_pre_armed_fixture_with_prepare(kind, false)
    }

    fn recovered_pre_armed_fixture_with_prepare(
        kind: NativeTerminationKind,
        observe_prepare: bool,
    ) -> RecoveredNormalFixture {
        recovered_pre_armed_fixture_with_timing(kind, observe_prepare, None)
    }

    fn recovered_pre_armed_fixture_with_timing(
        kind: NativeTerminationKind,
        observe_prepare: bool,
        timing: Option<(u64, u64)>,
    ) -> RecoveredNormalFixture {
        let policy = policy();
        let prepared_run = PreparedRun::from_policy(&policy);
        let prepared = prepared_run.receipt().clone();
        let policy_snapshot = prepared_run.policy_snapshot().to_vec();
        let mut api = MockNativeApi::new(&policy, HostileMode::None);
        if let Some((requested_at_unix_ms, recorded_at_unix_ms)) = timing {
            api.stage_termination_requested_at_ms = Some(requested_at_unix_ms);
            api.stage_termination_recorded_at_ms = Some(recorded_at_unix_ms);
        }
        configure_normal_terminal(&mut api, &policy, kind);
        let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
        let (mut starting, start_contract) = begin_staged_for_test(&mut supervisor, prepared_run);
        if observe_prepare {
            for _ in 0..8 {
                starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract)
                {
                    NativeStartingAdvance::Starting(next)
                    | NativeStartingAdvance::Retrying(next, _) => next,
                    NativeStartingAdvance::Armed(_) => {
                        panic!("prepare-only fixture crossed the Armed barrier")
                    }
                    NativeStartingAdvance::Terminal(_) => {
                        panic!("prepare-only fixture terminated before the request")
                    }
                };
                if supervisor.api().stage_prepared.is_some() {
                    break;
                }
            }
            assert!(supervisor.api().stage_prepared.is_some());
            assert!(supervisor.api().stage_created_roles.is_empty());
        }
        assert_eq!(
            supervisor
                .request_starting_termination(&mut starting)
                .expect("pre-Armed termination must be durably recorded"),
            NativeStartingTerminationAcknowledgement::Recorded(kind)
        );
        let proof = finish_staged_terminal(&mut supervisor, starting, &start_contract);
        assert_eq!(
            proof.terminal().reason(),
            match kind {
                NativeTerminationKind::Cancelled => BurnReason::Cancelled,
                NativeTerminationKind::TimedOut => BurnReason::TimedOut,
            }
        );
        let normal = supervisor
            .api()
            .stage_normal_evidence
            .clone()
            .expect("live pre-Armed closeout must retain typed evidence");
        let journal = recovered_stage_journal_evidence(supervisor.api());
        let receipt = recovered_normal_receipt(&policy, &prepared, &policy_snapshot, &journal);
        RecoveredNormalFixture {
            prepared,
            armed: None,
            policy_snapshot,
            evidence: NativeRecoveredNormalTerminationEvidence {
                journal,
                actions: recovered_stage_actions(supervisor.api()),
                receipt,
                normal: NativeRecoveredNormalTerminalEvidence::PreArmed {
                    observation: normal.observation,
                    native_cleanup: normal.native_cleanup,
                },
                external_actions_replayed: false,
            },
        }
    }

    fn recovered_armed_fixture(kind: NativeTerminationKind) -> RecoveredNormalFixture {
        let policy = policy();
        let prepared_run = PreparedRun::from_policy(&policy);
        let prepared = prepared_run.receipt().clone();
        let policy_snapshot = prepared_run.policy_snapshot().to_vec();
        let mut api = MockNativeApi::new(&policy, HostileMode::None);
        configure_normal_terminal(&mut api, &policy, kind);
        let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
        let mut armed_run = finish_staged_armed(&mut supervisor, prepared_run);
        let armed = armed_run.armed_receipt().clone();
        let actions = recovered_stage_actions(supervisor.api());
        let native_prepared = armed_run.native_prepared.clone();
        let terminal = supervisor
            .api()
            .terminal_evidence(&policy, &native_prepared);
        let cleanup = NativeCleanupEvidence {
            sockets: supervisor.api().observation.sockets.clone(),
            cleanup: supervisor.api().observation.cleanup.clone(),
            native: supervisor.api().native_cleanup(&native_prepared),
        };
        assert_eq!(
            supervisor
                .request_armed_termination(&mut armed_run)
                .expect("Armed termination must be durably recorded"),
            NativeArmedTerminationAcknowledgement::Recorded(kind)
        );
        validate_native_terminal_after_intent(&terminal, armed_run.termination_intent.as_ref())
            .expect("fixture terminal must follow the durable intent");
        validate_native_terminal(
            &policy,
            &native_prepared,
            &armed_run.suspended,
            &armed_run.launch,
            &terminal,
        )
        .expect("fixture terminal must match the staged native launch");
        validate_native_cleanup(&policy, &native_prepared, &terminal, &cleanup)
            .expect("fixture cleanup must match the staged native launch");
        let mut terminal_proof = None;
        for _ in 0..8 {
            match supervisor.advance_armed(armed_run) {
                NativeAdvanceOutcome::Running(next) | NativeAdvanceOutcome::Retrying(next, _) => {
                    armed_run = next;
                }
                NativeAdvanceOutcome::Terminal(ValidatedNativeTerminalRun::Burned(proof)) => {
                    terminal_proof = Some(proof);
                    break;
                }
                NativeAdvanceOutcome::Terminal(ValidatedNativeTerminalRun::Completed(_)) => {
                    panic!("normal Armed termination cannot complete");
                }
            }
        }
        let proof = terminal_proof.expect("Armed normal closeout must converge");
        assert_eq!(
            proof.terminal().reason(),
            match kind {
                NativeTerminationKind::Cancelled => BurnReason::Cancelled,
                NativeTerminationKind::TimedOut => BurnReason::TimedOut,
            }
        );
        let journal = recovered_stage_journal_evidence(supervisor.api());
        let receipt = recovered_normal_receipt(&policy, &prepared, &policy_snapshot, &journal);
        RecoveredNormalFixture {
            prepared,
            armed: Some(armed),
            policy_snapshot,
            evidence: NativeRecoveredNormalTerminationEvidence {
                journal,
                actions,
                receipt,
                normal: NativeRecoveredNormalTerminalEvidence::Armed { terminal, cleanup },
                external_actions_replayed: false,
            },
        }
    }

    fn recover_normal_fixture(
        fixture: RecoveredNormalFixture,
    ) -> (
        Result<ValidatedNativeTerminalRun, SupervisorError>,
        MockNativeApi,
    ) {
        let policy = decode_supervisor_policy_snapshot(&fixture.policy_snapshot)
            .expect("fixture policy snapshot must decode");
        let mut api = MockNativeApi::new(&policy, HostileMode::None);
        api.restart_normal = Some(fixture.evidence);
        let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
        let result = supervisor.recover_after_restart(
            &fixture.prepared,
            fixture.armed.as_ref(),
            &fixture.policy_snapshot,
        );
        (result, supervisor.api)
    }

    fn assert_restart_recovery_contained(fixture: RecoveredNormalFixture) {
        let (result, api) = recover_normal_fixture(fixture);
        let ValidatedNativeTerminalRun::Burned(proof) =
            result.expect("invalid normal replay must enter containment")
        else {
            panic!("invalid normal replay cannot complete");
        };
        assert_eq!(proof.terminal().reason(), BurnReason::RestartRecovery);
        assert!(proof.normal_termination_recovery().is_none());
        assert!(proof.admission().is_none());
        assert_eq!(api.containment_calls, 1);
        assert_eq!(api.terminal_polls, 0);
        assert_eq!(api.seal_calls, 0);
        assert_eq!(api.events, vec![NativeSupervisorPhase::Contain]);
        assert_eq!(api.stage_create_requests, 0);
        assert_eq!(api.stage_append_requests, 0);
        assert!(api.stage_created_roles.is_empty());
        assert!(api.stage_resumed_roles.is_empty());
    }

    #[test]
    fn live_completed_run_carries_the_exact_clean_armed_stage_binding() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut supervisor =
            ServiceOwnedNativeSupervisor::new(MockNativeApi::new(&policy, HostileMode::None));
        let mut armed = finish_staged_armed(&mut supervisor, prepared);
        let expected = verified_live_completed_stage_journal(
            &policy,
            armed.armed_receipt(),
            armed.native_prepared.start_contract_digest,
            armed.stage_journal.as_ref().expect("staged Armed journal"),
        )
        .expect("clean Armed binding");
        for _ in 0..8 {
            match supervisor.advance_armed(armed) {
                NativeAdvanceOutcome::Running(next) | NativeAdvanceOutcome::Retrying(next, _) => {
                    armed = next
                }
                NativeAdvanceOutcome::Terminal(ValidatedNativeTerminalRun::Completed(proof)) => {
                    assert_eq!(*proof.completed_stage(), expected);
                    assert!(proof.completed_stage().verifies());
                    return;
                }
                NativeAdvanceOutcome::Terminal(ValidatedNativeTerminalRun::Burned(_)) => {
                    panic!("healthy staged completion burned")
                }
            }
        }
        panic!("healthy staged completion did not converge")
    }

    #[test]
    fn restart_recovered_completed_replays_the_exact_v2_armed_journal() {
        let fixture = recovered_completed_fixture();
        let expected = fixture.expected_stage;
        let (result, api) = recover_completed_fixture(fixture);
        let ValidatedNativeTerminalRun::Completed(proof) =
            result.expect("exact completed replay must validate")
        else {
            panic!("exact completed replay cannot burn");
        };
        assert_eq!(*proof.completed_stage(), expected);
        assert_eq!(api.containment_calls, 0);
        assert_eq!(api.terminal_polls, 0);
        assert_eq!(api.seal_calls, 0);
        assert!(api.events.is_empty());
        assert_eq!(api.stage_create_requests, 0);
        assert_eq!(api.stage_append_requests, 0);
    }

    #[test]
    fn restart_recovered_completed_rejects_start_contract_and_action_drift() {
        let baseline = recovered_completed_fixture();

        let mut completed_binding = baseline.clone();
        completed_binding
            .evidence
            .completed_stage
            .start_contract_digest[0] ^= 0xff;
        assert!(recover_completed_fixture(completed_binding).0.is_err());

        let mut prepared_action = baseline.clone();
        prepared_action
            .evidence
            .actions
            .prepared
            .as_mut()
            .expect("prepared action")
            .start_contract_digest[0] ^= 0xff;
        assert!(recover_completed_fixture(prepared_action).0.is_err());

        let mut duplicated_terminal_view = baseline;
        duplicated_terminal_view
            .evidence
            .suspended
            .process
            .creation_time ^= 1;
        assert!(recover_completed_fixture(duplicated_terminal_view)
            .0
            .is_err());
    }

    #[test]
    fn restart_recovered_completed_rejects_legacy_and_torn_journals() {
        let baseline = recovered_completed_fixture();

        let mut legacy = baseline.clone();
        legacy.evidence.journal.readback.canonical_bytes[8..10]
            .copy_from_slice(&1_u16.to_be_bytes());
        legacy.evidence.journal.sealed_held_handle_binding_digest =
            staged_start::stage_journal_held_handle_binding_digest(
                &legacy.evidence.journal.readback.canonical_bytes,
                &legacy.evidence.journal.readback.file_identity,
                &legacy.evidence.journal.readback.parent_identity,
            );
        assert!(recover_completed_fixture(legacy).0.is_err());

        let mut torn = baseline;
        torn.evidence.journal.readback.canonical_bytes.pop();
        torn.evidence.journal.sealed_held_handle_binding_digest =
            staged_start::stage_journal_held_handle_binding_digest(
                &torn.evidence.journal.readback.canonical_bytes,
                &torn.evidence.journal.readback.file_identity,
                &torn.evidence.journal.readback.parent_identity,
            );
        assert!(recover_completed_fixture(torn).0.is_err());
    }

    #[test]
    fn restart_recovered_normal_termination_accepts_only_complete_typed_branches() {
        for fixture in [
            recovered_pre_armed_fixture(NativeTerminationKind::Cancelled),
            recovered_pre_armed_fixture(NativeTerminationKind::TimedOut),
            recovered_pre_armed_fixture_with_prepare(NativeTerminationKind::Cancelled, true),
            recovered_pre_armed_fixture_with_timing(
                NativeTerminationKind::Cancelled,
                false,
                Some((20_998, 20_999)),
            ),
            recovered_armed_fixture(NativeTerminationKind::Cancelled),
            recovered_armed_fixture(NativeTerminationKind::TimedOut),
        ] {
            let expected_reason = match fixture.evidence.receipt.kind {
                NativeTerminationKind::Cancelled => BurnReason::Cancelled,
                NativeTerminationKind::TimedOut => BurnReason::TimedOut,
            };
            let expected_armed = fixture.evidence.receipt.armed_receipt_digest;
            let expected_head = fixture.evidence.receipt.branch_head_digest;
            let expected_intent = fixture.evidence.receipt.intent_record_digest;
            let expected_terminal = fixture.evidence.receipt.terminal_payload_digest;
            let expected_cleanup = fixture.evidence.receipt.cleanup_payload_digest;
            let (result, api) = recover_normal_fixture(fixture);
            let ValidatedNativeTerminalRun::Burned(proof) =
                result.expect("complete normal replay must validate")
            else {
                panic!("normal replay cannot complete");
            };
            assert_eq!(proof.terminal().reason(), expected_reason);
            assert_eq!(proof.admission().is_some(), expected_armed.is_some());
            let binding = proof
                .normal_termination_recovery()
                .expect("normal replay must expose its exact journal binding");
            assert_eq!(binding.armed_receipt_digest().copied(), expected_armed);
            assert_eq!(*binding.stage_journal_head_digest(), expected_head);
            assert_eq!(*binding.termination_intent_digest(), expected_intent);
            assert_eq!(*binding.terminal_digest(), expected_terminal);
            assert_eq!(*binding.cleanup_digest(), expected_cleanup);
            assert_eq!(api.containment_calls, 0);
            assert!(api.events.is_empty());
        }
    }

    #[test]
    fn restart_recovered_normal_termination_contains_receipt_and_action_drift() {
        let baseline = recovered_armed_fixture(NativeTerminationKind::Cancelled);

        let mut kind = baseline.clone();
        kind.evidence.receipt.kind = NativeTerminationKind::TimedOut;
        assert_restart_recovery_contained(kind);

        let mut timing = baseline.clone();
        timing.evidence.receipt.recorded_at_unix_ms = timing
            .evidence
            .receipt
            .requested_at_unix_ms
            .saturating_sub(1);
        assert_restart_recovery_contained(timing);

        let mut branch = baseline.clone();
        branch.evidence.receipt.branch_head_digest[0] ^= 0xff;
        assert_restart_recovery_contained(branch);

        let mut intent = baseline.clone();
        intent.evidence.receipt.intent_record_digest[0] ^= 0xff;
        assert_restart_recovery_contained(intent);

        let mut action = baseline.clone();
        let bridge_created = action
            .evidence
            .actions
            .bridge_created
            .as_mut()
            .expect("Armed fixture has Bridge create evidence");
        bridge_created.suspended.created_suspended_at += 1;
        bridge_created.membership.membership_verified_at += 1;
        let bridge_resumed = action
            .evidence
            .actions
            .bridge_resumed
            .as_mut()
            .expect("Armed fixture has Bridge resume evidence");
        bridge_resumed.created_suspended_at += 1;
        bridge_resumed.job_membership_verified_at += 1;
        bridge_resumed.resumed_at += 1;
        assert_restart_recovery_contained(action);

        let mut start_contract = baseline.clone();
        start_contract
            .evidence
            .actions
            .prepared
            .as_mut()
            .expect("Armed fixture has prepared evidence")
            .start_contract_digest[0] ^= 0xff;
        assert_restart_recovery_contained(start_contract);

        let mut created_start_contract = baseline.clone();
        created_start_contract
            .evidence
            .actions
            .driver_created
            .as_mut()
            .expect("Armed fixture has Driver create evidence")
            .start_contract_digest[0] ^= 0xff;
        assert_restart_recovery_contained(created_start_contract);

        let mut armed = baseline.clone();
        armed
            .evidence
            .actions
            .armed_admission
            .as_mut()
            .expect("Armed fixture has admission evidence")
            .armed_receipt_digest[0] ^= 0xff;
        assert_restart_recovery_contained(armed);

        let mut pre_armed = recovered_pre_armed_fixture(NativeTerminationKind::Cancelled);
        pre_armed.evidence.receipt.armed_receipt_digest = Some(digest(0xe8));
        assert_restart_recovery_contained(pre_armed);
    }

    #[test]
    fn restart_recovered_normal_termination_contains_torn_replaced_or_replayed_evidence() {
        let baseline = recovered_armed_fixture(NativeTerminationKind::Cancelled);

        let mut torn = baseline.clone();
        torn.evidence
            .journal
            .readback
            .canonical_bytes
            .pop()
            .expect("complete journal is non-empty");
        torn.evidence.journal.sealed_held_handle_binding_digest =
            staged_start::stage_journal_held_handle_binding_digest(
                &torn.evidence.journal.readback.canonical_bytes,
                &torn.evidence.journal.readback.file_identity,
                &torn.evidence.journal.readback.parent_identity,
            );
        assert_restart_recovery_contained(torn);

        let mut replaced = baseline.clone();
        replaced.evidence.journal.readback.file_identity.file_id[0] ^= 0xff;
        assert_restart_recovery_contained(replaced);

        let mut parent_drift = baseline.clone();
        parent_drift
            .evidence
            .journal
            .readback
            .parent_identity
            .file_id[0] ^= 0xff;
        assert_restart_recovery_contained(parent_drift);

        let mut held_binding_drift = baseline.clone();
        held_binding_drift
            .evidence
            .journal
            .sealed_held_handle_binding_digest[0] ^= 0xff;
        assert_restart_recovery_contained(held_binding_drift);

        let mut replayed = baseline;
        replayed.evidence.external_actions_replayed = true;
        assert_restart_recovery_contained(replayed);
    }

    #[test]
    fn restart_recovered_normal_termination_enforces_deadline_and_cleanup_order() {
        let mut cancelled = recovered_armed_fixture(NativeTerminationKind::Cancelled);
        let policy = decode_supervisor_policy_snapshot(&cancelled.policy_snapshot)
            .expect("fixture policy snapshot must decode");
        let NativeRecoveredNormalTerminalEvidence::Armed { terminal, .. } =
            &mut cancelled.evidence.normal
        else {
            panic!("Armed fixture must carry native terminal evidence");
        };
        terminal.bridge_control_pipe.eof_observed_at = policy.deadline;
        assert_restart_recovery_contained(cancelled);

        let mut timed_out = recovered_armed_fixture(NativeTerminationKind::TimedOut);
        let NativeRecoveredNormalTerminalEvidence::Armed { terminal, cleanup } =
            &mut timed_out.evidence.normal
        else {
            panic!("Armed fixture must carry native terminal evidence");
        };
        terminal.bridge_control_pipe.eof_observed_at =
            cleanup.cleanup.observed_at.saturating_add(1);
        assert_restart_recovery_contained(timed_out);
    }

    #[test]
    fn restart_recovered_normal_termination_contains_every_pending_stage() {
        let policy = policy();
        let mut pending_journals = Vec::new();

        let mut declaration_supervisor =
            ServiceOwnedNativeSupervisor::new(MockNativeApi::new(&policy, HostileMode::None));
        let (starting, declaration_start_contract) = begin_staged_for_test(
            &mut declaration_supervisor,
            PreparedRun::from_policy(&policy),
        );
        let _starting = match advance_staged_for_test(
            &mut declaration_supervisor,
            starting,
            &declaration_start_contract,
        ) {
            NativeStartingAdvance::Starting(next) => next,
            _ => panic!("declaration must be durably written"),
        };
        pending_journals.push(recovered_stage_journal_evidence(
            declaration_supervisor.api(),
        ));

        let mut intent_supervisor =
            ServiceOwnedNativeSupervisor::new(MockNativeApi::new(&policy, HostileMode::None));
        let (mut starting, _intent_start_contract) =
            begin_staged_for_test(&mut intent_supervisor, PreparedRun::from_policy(&policy));
        assert!(matches!(
            intent_supervisor
                .request_starting_termination(&mut starting)
                .expect("termination intent must be durable"),
            NativeStartingTerminationAcknowledgement::Recorded(_)
        ));
        pending_journals.push(recovered_stage_journal_evidence(intent_supervisor.api()));

        let mut armed_supervisor =
            ServiceOwnedNativeSupervisor::new(MockNativeApi::new(&policy, HostileMode::None));
        let mut armed =
            finish_staged_armed(&mut armed_supervisor, PreparedRun::from_policy(&policy));
        pending_journals.push(recovered_stage_journal_evidence(armed_supervisor.api()));
        assert!(matches!(
            armed_supervisor
                .request_armed_termination(&mut armed)
                .expect("Armed termination intent must be durable"),
            NativeArmedTerminationAcknowledgement::Recorded(_)
        ));
        pending_journals.push(recovered_stage_journal_evidence(armed_supervisor.api()));

        let baseline = recovered_armed_fixture(NativeTerminationKind::Cancelled);
        for journal in pending_journals {
            let mut pending = baseline.clone();
            pending.evidence.journal = journal;
            assert_restart_recovery_contained(pending);
        }
    }

    #[test]
    fn lost_create_ack_reopens_the_exact_held_declaration_without_recreating() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut api = MockNativeApi::new(&policy, HostileMode::None);
        api.stage_create_uncertain_after_write_once = true;
        let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
        let (starting, start_contract) = begin_staged_for_test(&mut supervisor, prepared);
        let declared_bytes = starting.journal_bytes_for_test().to_vec();

        assert_eq!(
            starting.declaration_mode_for_test(),
            Some(staged_start::NativeStageJournalCreateMode::Create)
        );
        assert_eq!(supervisor.api().stage_create_requests, 0);
        assert_eq!(supervisor.api().stage_create_actions, 0);
        assert!(supervisor.api().stage_bytes.is_empty());

        let starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract) {
            NativeStartingAdvance::Retrying(starting, "test_stage_create_uncertain") => starting,
            _ => panic!("lost create acknowledgement must retain Declaring ownership"),
        };
        assert!(matches!(
            starting.declaration_mode_for_test(),
            Some(staged_start::NativeStageJournalCreateMode::ReconcileOnly {
                held_handle_binding_digest: _
            })
        ));
        assert_eq!(starting.journal_bytes_for_test(), declared_bytes);
        assert_eq!(supervisor.api().stage_create_requests, 1);
        assert_eq!(supervisor.api().stage_create_actions, 1);
        assert_eq!(supervisor.api().stage_write_sequence, 1);
        assert!(supervisor.api().events.is_empty());

        let starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract) {
            NativeStartingAdvance::Starting(starting) => starting,
            _ => panic!("exact held-handle reopen must complete declaration"),
        };
        assert_eq!(starting.declaration_mode_for_test(), None);
        assert_eq!(starting.journal_bytes_for_test(), declared_bytes);
        assert_eq!(supervisor.api().stage_create_requests, 2);
        assert_eq!(supervisor.api().stage_create_actions, 1);
        assert_eq!(supervisor.api().stage_write_sequence, 1);
        assert_eq!(supervisor.api().stage_append_requests, 0);
        assert!(supervisor.api().events.is_empty());
    }

    #[test]
    fn create_reopen_drift_fails_closed_without_recreating_the_journal() {
        for fault in [
            StageCreateReopenFault::Bytes,
            StageCreateReopenFault::FileIdentity,
            StageCreateReopenFault::Security,
            StageCreateReopenFault::CreatedNew,
            StageCreateReopenFault::HeldHandle,
        ] {
            let policy = policy();
            let prepared = PreparedRun::from_policy(&policy);
            let mut api = MockNativeApi::new(&policy, HostileMode::None);
            api.stage_create_uncertain_after_write_once = true;
            api.stage_create_reopen_fault = fault;
            api.failure_containment_uncertain_once = true;
            let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
            let (starting, start_contract) = begin_staged_for_test(&mut supervisor, prepared);
            let starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract)
            {
                NativeStartingAdvance::Retrying(starting, "test_stage_create_uncertain") => {
                    starting
                }
                _ => panic!("first create must become uncertain for {fault:?}"),
            };
            let starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract)
            {
                NativeStartingAdvance::Retrying(
                    starting,
                    "test_native_failure_containment_uncertain",
                ) => starting,
                _ => panic!("reopen drift must enter retained containment for {fault:?}"),
            };
            assert_eq!(supervisor.api().stage_create_requests, 2);
            assert_eq!(supervisor.api().stage_create_actions, 1);
            assert_eq!(supervisor.api().containment_calls, 1);

            assert!(matches!(
                advance_staged_for_test(&mut supervisor, starting, &start_contract),
                NativeStartingAdvance::Terminal(ValidatedNativeTerminalRun::Burned(_))
            ));
            assert_eq!(supervisor.api().stage_create_requests, 2);
            assert_eq!(supervisor.api().stage_create_actions, 1);
            assert_eq!(supervisor.api().containment_calls, 2);
            assert!(supervisor.api().events.iter().all(|phase| {
                *phase != NativeSupervisorPhase::Prepare
                    && *phase != NativeSupervisorPhase::LaunchSuspended
                    && *phase != NativeSupervisorPhase::Resume
            }));
        }
    }

    #[test]
    fn rejected_create_is_contained_without_a_create_or_retry() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut api = MockNativeApi::new(&policy, HostileMode::None);
        api.stage_create_rejected_no_mutation_once = true;
        api.failure_containment_uncertain_once = true;
        let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
        let (starting, start_contract) = begin_staged_for_test(&mut supervisor, prepared);
        let starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract) {
            NativeStartingAdvance::Retrying(
                starting,
                "test_native_failure_containment_uncertain",
            ) => starting,
            _ => panic!("explicit no-mutation rejection must enter retained containment"),
        };
        assert_eq!(supervisor.api().stage_create_requests, 1);
        assert_eq!(supervisor.api().stage_create_actions, 0);

        assert!(matches!(
            advance_staged_for_test(&mut supervisor, starting, &start_contract),
            NativeStartingAdvance::Terminal(ValidatedNativeTerminalRun::Burned(_))
        ));
        assert_eq!(supervisor.api().stage_create_requests, 1);
        assert_eq!(supervisor.api().stage_create_actions, 0);
        assert_eq!(supervisor.api().containment_calls, 2);
    }

    #[test]
    fn rejected_reconcile_after_lost_ack_never_repeats_the_create_action() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut api = MockNativeApi::new(&policy, HostileMode::None);
        api.stage_create_uncertain_after_write_once = true;
        api.failure_containment_uncertain_once = true;
        let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
        let (starting, start_contract) = begin_staged_for_test(&mut supervisor, prepared);
        let starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract) {
            NativeStartingAdvance::Retrying(starting, "test_stage_create_uncertain") => starting,
            _ => panic!("first create must retain its uncertain capability"),
        };
        supervisor.api.stage_create_rejected_no_mutation_once = true;
        let starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract) {
            NativeStartingAdvance::Retrying(
                starting,
                "test_native_failure_containment_uncertain",
            ) => starting,
            _ => panic!("reconcile rejection must enter retained containment"),
        };
        assert_eq!(supervisor.api().stage_create_requests, 2);
        assert_eq!(supervisor.api().stage_create_actions, 1);

        assert!(matches!(
            advance_staged_for_test(&mut supervisor, starting, &start_contract),
            NativeStartingAdvance::Terminal(ValidatedNativeTerminalRun::Burned(_))
        ));
        assert_eq!(supervisor.api().stage_create_requests, 2);
        assert_eq!(supervisor.api().stage_create_actions, 1);
        assert_eq!(supervisor.api().containment_calls, 2);
    }

    #[test]
    fn declaring_cancel_and_timeout_reconcile_before_recording_termination() {
        for kind in [
            NativeTerminationKind::Cancelled,
            NativeTerminationKind::TimedOut,
        ] {
            let policy = policy();
            let prepared = PreparedRun::from_policy(&policy);
            let mut api = MockNativeApi::new(&policy, HostileMode::None);
            api.armed_termination_kind = kind;
            api.stage_create_uncertain_after_write_once = true;
            let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
            let (starting, start_contract) = begin_staged_for_test(&mut supervisor, prepared);
            let mut starting =
                match advance_staged_for_test(&mut supervisor, starting, &start_contract) {
                    NativeStartingAdvance::Retrying(starting, "test_stage_create_uncertain") => {
                        starting
                    }
                    _ => panic!("declaration must be uncertain before {kind:?}"),
                };

            assert_eq!(
                supervisor
                    .request_starting_termination(&mut starting)
                    .expect("termination must reconcile the held declaration first"),
                NativeStartingTerminationAcknowledgement::Recorded(kind)
            );
            assert_eq!(supervisor.api().stage_create_requests, 2);
            assert_eq!(supervisor.api().stage_create_actions, 1);
            assert_eq!(supervisor.api().stage_append_requests, 1);
            assert_eq!(supervisor.api().stage_durable_appends, 1);
            assert!(supervisor.api().events.is_empty());

            let proof = finish_staged_terminal(&mut supervisor, starting, &start_contract);
            let expected_reason = match kind {
                NativeTerminationKind::Cancelled => BurnReason::Cancelled,
                NativeTerminationKind::TimedOut => BurnReason::TimedOut,
            };
            assert_eq!(proof.terminal().reason(), expected_reason);
            assert_eq!(supervisor.api().stage_create_requests, 2);
            assert_eq!(supervisor.api().stage_create_actions, 1);
        }
    }

    #[test]
    fn durable_staged_start_records_each_action_once_before_armed() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut supervisor =
            ServiceOwnedNativeSupervisor::new(MockNativeApi::new(&policy, HostileMode::None));
        let (mut starting, start_contract) = begin_staged_for_test(&mut supervisor, prepared);
        let mut advances = 0;
        let armed = loop {
            advances += 1;
            match advance_staged_for_test(&mut supervisor, starting, &start_contract) {
                NativeStartingAdvance::Starting(next) => starting = next,
                NativeStartingAdvance::Armed(armed) => break armed,
                NativeStartingAdvance::Retrying(_, code) => {
                    panic!("happy staged start unexpectedly retried: {code}")
                }
                NativeStartingAdvance::Terminal(_) => {
                    panic!("happy staged start unexpectedly terminated")
                }
            }
        };

        assert_eq!(advances, 13);
        assert!(armed
            .armed_receipt()
            .verifies_for(&armed.prepared_receipt, &policy.run_binding_digest));
        assert_eq!(supervisor.api().stage_write_sequence, 13);
        assert_eq!(supervisor.api().stage_create_requests, 1);
        assert_eq!(supervisor.api().stage_create_actions, 1);
        assert_eq!(supervisor.api().stage_durable_appends, 12);
        assert_eq!(supervisor.api().stage_append_requests, 12);
        assert_eq!(
            supervisor.api().stage_created_roles,
            vec![ProcessRole::BridgeLauncher, ProcessRole::Driver]
        );
        assert_eq!(
            supervisor.api().stage_resumed_roles,
            vec![ProcessRole::BridgeLauncher, ProcessRole::Driver]
        );

        let actions = recovered_stage_actions(supervisor.api());
        let bridge_created = actions.bridge_created.as_ref().unwrap();
        let driver_created = actions.driver_created.as_ref().unwrap();
        let baseline_created_digest = staged_start::created_root_digest(bridge_created);
        let mut substituted_created = bridge_created.clone();
        substituted_created.suspended.raw_handle_list = driver_created.suspended.raw_handle_list;
        assert_ne!(
            staged_start::created_root_digest(&substituted_created),
            baseline_created_digest
        );

        let bridge_resumed = actions.bridge_resumed.as_ref().unwrap();
        let driver_resumed = actions.driver_resumed.as_ref().unwrap();
        let baseline_resumed_digest =
            staged_start::resumed_root_digest(ProcessRole::BridgeLauncher, bridge_resumed);
        let mut substituted_resumed = bridge_resumed.clone();
        substituted_resumed.raw_handle_list = driver_resumed.raw_handle_list;
        assert_ne!(
            staged_start::resumed_root_digest(ProcessRole::BridgeLauncher, &substituted_resumed,),
            baseline_resumed_digest
        );
    }

    #[test]
    fn live_start_contract_digest_drift_is_contained_before_stage_creation() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let accepted = test_start_contract(&prepared);
        let drifted = test_start_contract(&prepared);
        assert_ne!(accepted.binding_digest(), drifted.binding_digest());
        let mut supervisor =
            ServiceOwnedNativeSupervisor::new(MockNativeApi::new(&policy, HostileMode::None));
        let starting = supervisor
            .begin_start(prepared, &accepted)
            .expect("accepted live start contract must declare in memory");

        assert!(matches!(
            supervisor.advance_starting(starting, &drifted),
            NativeStartingAdvance::Terminal(ValidatedNativeTerminalRun::Burned(_))
        ));
        assert_eq!(supervisor.api().stage_create_requests, 0);
        assert_eq!(supervisor.api().stage_create_actions, 0);
        assert_eq!(supervisor.api().containment_calls, 1);
    }

    #[test]
    fn staged_armed_cancel_seals_terminal_cleanup_and_survives_a_lost_append_ack() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut api = MockNativeApi::new(&policy, HostileMode::None);
        api.observation.finalization = None;
        api.observation.terminal.kind = TerminalKind::Cancelled;
        api.observation.terminal.intent = TerminalIntent::Burn;
        api.observation.cleanup.final_result_persisted = false;
        let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
        let mut armed = finish_staged_armed(&mut supervisor, prepared);
        let expected_armed_digest: Digest = Sha256::digest(armed.armed_receipt().encode()).into();

        assert_eq!(
            supervisor
                .request_armed_termination(&mut armed)
                .expect("Armed cancellation must append to the same journal"),
            NativeArmedTerminationAcknowledgement::Recorded(NativeTerminationKind::Cancelled)
        );
        assert_eq!(supervisor.api().stage_write_sequence, 14);
        supervisor.api.stage_append_uncertain_after_write_once = true;
        armed = match supervisor.advance_armed(armed) {
            NativeAdvanceOutcome::Retrying(armed, "test_stage_append_uncertain") => armed,
            _ => panic!("lost terminal append response must retain Armed ownership"),
        };
        assert_eq!(supervisor.api().terminal_polls, 1);
        assert_eq!(supervisor.api().stage_write_sequence, 15);

        let mut terminal = None;
        for _ in 0..8 {
            match supervisor.advance_armed(armed) {
                NativeAdvanceOutcome::Running(next) | NativeAdvanceOutcome::Retrying(next, _) => {
                    armed = next
                }
                NativeAdvanceOutcome::Terminal(value) => {
                    terminal = Some(value);
                    break;
                }
            }
        }
        let Some(ValidatedNativeTerminalRun::Burned(proof)) = terminal else {
            panic!("cancelled staged Armed run must finish burned")
        };
        assert_eq!(proof.terminal().reason(), BurnReason::Cancelled);
        assert!(proof.admission().is_some());
        let recovery = proof
            .normal_termination_recovery()
            .expect("Armed normal termination needs restart evidence");
        assert_eq!(
            recovery.armed_receipt_digest(),
            Some(&expected_armed_digest)
        );
        assert!([
            recovery.stage_journal_head_digest(),
            recovery.termination_intent_digest(),
            recovery.terminal_digest(),
            recovery.cleanup_digest(),
        ]
        .iter()
        .all(|digest| digest.iter().any(|byte| *byte != 0)));
        assert_eq!(supervisor.api().terminal_polls, 1);
        assert_eq!(supervisor.api().stage_durable_appends, 15);
        assert_eq!(supervisor.api().stage_write_sequence, 16);
        assert_eq!(supervisor.api().stage_append_requests, 16);
    }

    #[test]
    fn lost_observation_ack_reopens_exact_bytes_without_replaying_the_action() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut supervisor =
            ServiceOwnedNativeSupervisor::new(MockNativeApi::new(&policy, HostileMode::None));
        let (starting, start_contract) = begin_staged_for_test(&mut supervisor, prepared);
        let starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract) {
            NativeStartingAdvance::Starting(starting) => starting,
            _ => panic!("first transition must durably declare the run"),
        };
        let starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract) {
            NativeStartingAdvance::Starting(starting) => starting,
            _ => panic!("second transition must leave Prepare intent pending"),
        };
        supervisor.api.stage_append_uncertain_after_write_once = true;
        let starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract) {
            NativeStartingAdvance::Retrying(starting, "test_stage_append_uncertain") => starting,
            _ => panic!("lost observation acknowledgement must retain Starting"),
        };
        assert_eq!(
            supervisor
                .api()
                .events
                .iter()
                .filter(|phase| **phase == NativeSupervisorPhase::Prepare)
                .count(),
            1
        );
        assert_eq!(supervisor.api().stage_durable_appends, 2);

        let _starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract) {
            NativeStartingAdvance::Starting(starting) => starting,
            _ => panic!("observation retry must return to the next Starting phase"),
        };
        assert_eq!(
            supervisor
                .api()
                .events
                .iter()
                .filter(|phase| **phase == NativeSupervisorPhase::Prepare)
                .count(),
            1
        );
        assert_eq!(supervisor.api().stage_durable_appends, 2);
        assert_eq!(supervisor.api().stage_append_requests, 3);
    }

    #[test]
    fn uncertain_starting_termination_retries_the_same_record_and_never_starts_work() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut api = MockNativeApi::new(&policy, HostileMode::None);
        api.stage_termination_uncertain_after_write_once = true;
        let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
        let (mut starting, start_contract) = begin_staged_for_test(&mut supervisor, prepared);

        assert_eq!(
            supervisor
                .request_starting_termination(&mut starting)
                .expect("lost response is a typed uncertainty"),
            NativeStartingTerminationAcknowledgement::Uncertain
        );
        assert!(supervisor.api().events.is_empty());
        assert_eq!(supervisor.api().stage_durable_appends, 1);
        assert_eq!(
            supervisor
                .request_starting_termination(&mut starting)
                .expect("retry must reopen the exact durable intent"),
            NativeStartingTerminationAcknowledgement::Recorded(NativeTerminationKind::Cancelled)
        );
        assert_eq!(supervisor.api().stage_durable_appends, 1);

        let proof = finish_staged_terminal(&mut supervisor, starting, &start_contract);
        assert_eq!(proof.terminal().reason(), BurnReason::Cancelled);
        let recovery = proof
            .normal_termination_recovery()
            .expect("normal Starting termination needs restart evidence");
        assert!(recovery.armed_receipt_digest().is_none());
        assert!([
            recovery.stage_journal_head_digest(),
            recovery.termination_intent_digest(),
            recovery.terminal_digest(),
            recovery.cleanup_digest(),
        ]
        .iter()
        .all(|digest| digest.iter().any(|byte| *byte != 0)));
        assert!(supervisor.api().events.is_empty());
        assert_eq!(supervisor.api().stage_containment_calls, 1);
        assert_eq!(supervisor.api().stage_durable_appends, 3);
        assert_eq!(supervisor.api().stage_write_sequence, 4);
    }

    #[test]
    fn cancellation_reconciles_a_completed_action_observation_before_terminal_intent() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut supervisor =
            ServiceOwnedNativeSupervisor::new(MockNativeApi::new(&policy, HostileMode::None));
        let (starting, start_contract) = begin_staged_for_test(&mut supervisor, prepared);
        let starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract) {
            NativeStartingAdvance::Starting(starting) => starting,
            _ => panic!("first transition must durably declare the run"),
        };
        let starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract) {
            NativeStartingAdvance::Starting(starting) => starting,
            _ => panic!("second transition must leave Prepare intent pending"),
        };
        supervisor.api.stage_append_uncertain_after_write_once = true;
        let mut starting = match advance_staged_for_test(&mut supervisor, starting, &start_contract)
        {
            NativeStartingAdvance::Retrying(starting, _) => starting,
            _ => panic!("observation uncertainty must retain Starting"),
        };

        assert_eq!(
            supervisor
                .request_starting_termination(&mut starting)
                .expect("cancel must first reconcile the observation"),
            NativeStartingTerminationAcknowledgement::Recorded(NativeTerminationKind::Cancelled)
        );
        let proof = finish_staged_terminal(&mut supervisor, starting, &start_contract);
        assert_eq!(proof.terminal().reason(), BurnReason::Cancelled);
        assert_eq!(
            supervisor
                .api()
                .events
                .iter()
                .filter(|phase| **phase == NativeSupervisorPhase::Prepare)
                .count(),
            1
        );
        assert_eq!(supervisor.api().stage_durable_appends, 5);
    }

    #[test]
    fn timed_out_starting_run_retains_the_service_time_and_containment_retry() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut api = MockNativeApi::new(&policy, HostileMode::None);
        api.armed_termination_kind = NativeTerminationKind::TimedOut;
        api.stage_containment_uncertain_once = true;
        let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
        let (mut starting, start_contract) = begin_staged_for_test(&mut supervisor, prepared);
        assert_eq!(
            supervisor
                .request_starting_termination(&mut starting)
                .expect("service-selected timeout must be durable"),
            NativeStartingTerminationAcknowledgement::Recorded(NativeTerminationKind::TimedOut)
        );

        let proof = finish_staged_terminal(&mut supervisor, starting, &start_contract);
        assert_eq!(proof.terminal().reason(), BurnReason::TimedOut);
        assert_eq!(supervisor.api().stage_containment_calls, 2);
        assert_eq!(supervisor.api().stage_durable_appends, 3);
    }

    #[test]
    fn production_blocks_before_any_native_mutation_when_listener_cannot_be_adopted() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut supervisor = ServiceOwnedNativeSupervisor::new(WindowsNativeSupervisorApi);
        assert_eq!(
            supervisor
                .execute(prepared)
                .expect_err("production adapter must remain closed")
                .code(),
            BACKEND_LISTENER_ADOPTION_BLOCKER
        );
    }

    #[test]
    fn complete_native_chain_seals_origin_only_after_verified_cleanup() {
        let (result, api) = execute(HostileMode::None);
        let ValidatedNativeTerminalRun::Completed(proof) =
            result.expect("valid native evidence must complete")
        else {
            panic!("expected completed proof");
        };
        assert_eq!(proof.result_bytes(), b"canonical-result");
        let expected_result_digest: Digest = Sha256::digest(b"canonical-result").into();
        assert_eq!(proof.result_digest(), &expected_result_digest);
        assert_eq!(proof.authority_ticket_digest(), &policy().ticket_digest);
        assert_ne!(
            proof.origin_ticket_digest(),
            proof.authority_ticket_digest()
        );
        assert!(!proof.canonical_origin_envelope_bytes().is_empty());
        let expected_origin_digest: Digest =
            Sha256::digest(proof.canonical_origin_envelope_bytes()).into();
        assert_eq!(
            proof.canonical_origin_envelope_digest(),
            &expected_origin_digest
        );
        assert!(!is_zero_digest(proof.admission().binding_digest()));
        assert_eq!(api.containment_calls, 0);
        assert_eq!(api.seal_calls, 1);
        let debug = format!("{proof:?}");
        assert!(debug.contains("[redacted]"));
        assert!(!debug.contains("canonical-result"));
        assert!(!debug.contains("service-owned-origin-envelope"));
        let contain_index = api
            .events
            .iter()
            .position(|phase| *phase == NativeSupervisorPhase::Contain)
            .expect("cleanup event");
        let seal_index = api
            .events
            .iter()
            .position(|phase| *phase == NativeSupervisorPhase::Finalize)
            .expect("seal event");
        assert!(contain_index < seal_index);
    }

    #[test]
    fn staged_start_returns_armed_before_any_terminal_poll_or_cleanup() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut supervisor = ServiceOwnedNativeSupervisor::new(MockNativeApi::new(
            &policy,
            HostileMode::TerminalPendingOnce,
        ));
        let mut armed = match supervisor
            .start_to_armed(prepared)
            .expect("atomic launch must reach armed")
        {
            NativeStartOutcome::Armed(run) => run,
            NativeStartOutcome::Terminal(_) => panic!("valid launch must not terminate pre-arm"),
        };
        assert!(armed
            .armed_receipt()
            .verifies_for(&armed.prepared_receipt, &armed.policy.run_binding_digest));
        assert!(!supervisor
            .api()
            .events
            .contains(&NativeSupervisorPhase::ObserveTerminal));
        assert_eq!(supervisor.api().containment_calls, 0);
        assert_eq!(supervisor.api().seal_calls, 0);

        armed = match supervisor.advance_armed(armed) {
            NativeAdvanceOutcome::Running(run) => run,
            NativeAdvanceOutcome::Terminal(_) => panic!("pending poll must not terminalize"),
            NativeAdvanceOutcome::Retrying(_, code) => {
                panic!("healthy terminal poll must not retry: {code}")
            }
        };
        assert_eq!(supervisor.api().terminal_polls, 1);
        assert_eq!(supervisor.api().containment_calls, 0);
        assert_eq!(supervisor.api().seal_calls, 0);

        assert!(matches!(
            supervisor.advance_armed(armed),
            NativeAdvanceOutcome::Terminal(ValidatedNativeTerminalRun::Completed(_))
        ));
        assert_eq!(supervisor.api().terminal_polls, 2);
        assert_eq!(supervisor.api().seal_calls, 1);
    }

    #[test]
    fn termination_intent_is_durable_idempotent_and_first_wins() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut supervisor =
            ServiceOwnedNativeSupervisor::new(MockNativeApi::new(&policy, HostileMode::None));
        let mut armed = match supervisor
            .start_to_armed(prepared)
            .expect("valid launch must arm")
        {
            NativeStartOutcome::Armed(run) => run,
            NativeStartOutcome::Terminal(_) => panic!("valid launch must not terminate pre-arm"),
        };

        let first = supervisor
            .request_armed_termination(&mut armed)
            .expect("service journal must accept cancellation");
        supervisor.api.armed_termination_kind = NativeTerminationKind::TimedOut;
        let repeated = supervisor
            .request_armed_termination(&mut armed)
            .expect("same cancellation must be idempotent");
        assert_eq!(first, repeated);
        assert_eq!(
            first,
            NativeArmedTerminationAcknowledgement::Recorded(NativeTerminationKind::Cancelled)
        );
        let receipt = armed
            .termination_intent
            .as_ref()
            .expect("recorded acknowledgement must retain the exact receipt");
        assert_eq!(
            receipt.record_digest,
            termination_intent_receipt_digest(receipt)
        );
        assert!(receipt.append_flushed);
        assert!(receipt.readback_verified);
        assert!(receipt.service_owned_sealed_journal);
        assert_eq!(
            supervisor
                .request_armed_termination(&mut armed)
                .expect("first durable terminal intent must win"),
            NativeArmedTerminationAcknowledgement::Recorded(NativeTerminationKind::Cancelled)
        );
        assert_eq!(supervisor.api().terminal_polls, 0);
        assert_eq!(supervisor.api().containment_calls, 0);
        assert_eq!(supervisor.api().seal_calls, 0);
    }

    #[test]
    fn armed_termination_transport_uncertainty_retains_the_live_run_for_retry() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut api = MockNativeApi::new(&policy, HostileMode::None);
        api.armed_termination_uncertain_once = true;
        let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
        let mut armed = match supervisor
            .start_to_armed(prepared)
            .expect("valid launch must arm")
        {
            NativeStartOutcome::Armed(run) => run,
            NativeStartOutcome::Terminal(_) => panic!("valid launch must not terminate pre-arm"),
        };

        assert_eq!(
            supervisor
                .request_armed_termination(&mut armed)
                .expect("transport uncertainty is a typed acknowledgement"),
            NativeArmedTerminationAcknowledgement::Uncertain
        );
        assert!(armed.termination_intent.is_none());
        assert_eq!(
            supervisor
                .request_armed_termination(&mut armed)
                .expect("retry must observe the service-owned durable intent"),
            NativeArmedTerminationAcknowledgement::Recorded(NativeTerminationKind::Cancelled)
        );
        assert!(armed.termination_intent.is_some());
    }

    #[test]
    fn staged_armed_termination_preserves_milliseconds_for_same_second_terminal() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let requested_at_unix_ms = 110_998;
        let recorded_at_unix_ms = 110_999;
        let mut api = MockNativeApi::new(&policy, HostileMode::None);
        api.stage_termination_requested_at_ms = Some(requested_at_unix_ms);
        api.stage_termination_recorded_at_ms = Some(recorded_at_unix_ms);
        configure_normal_terminal(&mut api, &policy, NativeTerminationKind::Cancelled);
        let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
        let mut armed = finish_staged_armed(&mut supervisor, prepared);
        let terminal = supervisor
            .api()
            .terminal_evidence(&policy, &armed.native_prepared);

        assert_eq!(
            supervisor
                .request_armed_termination(&mut armed)
                .expect("same-second cancellation must retain its exact service time"),
            NativeArmedTerminationAcknowledgement::Recorded(NativeTerminationKind::Cancelled)
        );
        let intent = armed
            .termination_intent
            .as_ref()
            .expect("durable stage intent must be retained");
        assert_eq!(intent.requested_at_unix_ms, requested_at_unix_ms);
        assert_eq!(intent.recorded_at_unix_ms, recorded_at_unix_ms);
        assert_eq!(terminal.terminal.observed_at, recorded_at_unix_ms / 1_000);
        validate_native_terminal_after_intent(&terminal, Some(intent))
            .expect("a same-second terminal must follow the sequenced millisecond intent");
    }

    #[test]
    fn termination_intent_rejects_timing_digest_and_seal_drift() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut supervisor =
            ServiceOwnedNativeSupervisor::new(MockNativeApi::new(&policy, HostileMode::None));
        let armed = match supervisor
            .start_to_armed(prepared)
            .expect("valid launch must arm")
        {
            NativeStartOutcome::Armed(run) => run,
            NativeStartOutcome::Terminal(_) => panic!("valid launch must not terminate pre-arm"),
        };
        let policy_snapshot_digest: Digest = Sha256::digest(&armed.policy_snapshot).into();
        let baseline = NativeTerminationIntentReceipt::from_service_journal_readback(
            &armed.policy,
            armed.prepared_receipt.digest(),
            &armed.armed,
            policy_snapshot_digest,
            NativeTerminationKind::Cancelled,
            33_000,
            34_000,
            6,
            digest(0xe1),
        );
        validate_native_termination_intent(
            &armed.policy,
            &armed.prepared_receipt,
            &armed.armed,
            &armed.policy_snapshot,
            NativeTerminationKind::Cancelled,
            &baseline,
        )
        .expect("exact cancellation journal readback must validate");

        let timeout = NativeTerminationIntentReceipt::from_service_journal_readback(
            &armed.policy,
            armed.prepared_receipt.digest(),
            &armed.armed,
            policy_snapshot_digest,
            NativeTerminationKind::TimedOut,
            armed.policy.deadline.saturating_mul(1_000),
            armed
                .policy
                .deadline
                .saturating_mul(1_000)
                .saturating_add(1),
            7,
            baseline.record_digest,
        );
        validate_native_termination_intent(
            &armed.policy,
            &armed.prepared_receipt,
            &armed.armed,
            &armed.policy_snapshot,
            NativeTerminationKind::TimedOut,
            &timeout,
        )
        .expect("exact timeout journal readback must validate");

        let deadline_ms = armed.policy.deadline.saturating_mul(1_000);
        let last_millisecond_cancellation =
            NativeTerminationIntentReceipt::from_service_journal_readback(
                &armed.policy,
                armed.prepared_receipt.digest(),
                &armed.armed,
                policy_snapshot_digest,
                NativeTerminationKind::Cancelled,
                deadline_ms.saturating_sub(2),
                deadline_ms.saturating_sub(1),
                8,
                timeout.record_digest,
            );
        validate_native_termination_intent(
            &armed.policy,
            &armed.prepared_receipt,
            &armed.armed,
            &armed.policy_snapshot,
            NativeTerminationKind::Cancelled,
            &last_millisecond_cancellation,
        )
        .expect("deadline_ms - 1 must remain a cancellation");

        let crossing_timeout = NativeTerminationIntentReceipt::from_service_journal_readback(
            &armed.policy,
            armed.prepared_receipt.digest(),
            &armed.armed,
            policy_snapshot_digest,
            NativeTerminationKind::TimedOut,
            deadline_ms.saturating_sub(1),
            deadline_ms,
            9,
            last_millisecond_cancellation.record_digest,
        );
        validate_native_termination_intent(
            &armed.policy,
            &armed.prepared_receipt,
            &armed.armed,
            &armed.policy_snapshot,
            NativeTerminationKind::TimedOut,
            &crossing_timeout,
        )
        .expect("a pre-deadline request recorded at the deadline must remain a timeout");

        let mut hostile = Vec::new();
        let mut value = baseline.clone();
        value.requested_at_unix_ms = armed.policy.deadline.saturating_mul(1_000);
        value.recorded_at_unix_ms = armed.policy.deadline.saturating_mul(1_000);
        value.record_digest = termination_intent_receipt_digest(&value);
        hostile.push(value);
        let mut value = baseline.clone();
        value.journal_sequence = 0;
        value.record_digest = termination_intent_receipt_digest(&value);
        hostile.push(value);
        let mut value = baseline.clone();
        value.previous_record_digest = [0; 32];
        value.record_digest = termination_intent_receipt_digest(&value);
        hostile.push(value);
        let mut value = baseline.clone();
        value.append_flushed = false;
        value.record_digest = termination_intent_receipt_digest(&value);
        hostile.push(value);
        let mut value = baseline.clone();
        value.readback_verified = false;
        value.record_digest = termination_intent_receipt_digest(&value);
        hostile.push(value);
        let mut value = baseline.clone();
        value.service_owned_sealed_journal = false;
        value.record_digest = termination_intent_receipt_digest(&value);
        hostile.push(value);
        let mut value = baseline.clone();
        value.record_digest = digest(0xfe);
        hostile.push(value);

        for value in hostile {
            assert_eq!(
                validate_native_termination_intent(
                    &armed.policy,
                    &armed.prepared_receipt,
                    &armed.armed,
                    &armed.policy_snapshot,
                    NativeTerminationKind::Cancelled,
                    &value,
                )
                .expect_err("hostile termination receipt must fail closed")
                .code(),
                "authority_native_termination_intent_invalid"
            );
        }
    }

    #[test]
    fn completed_terminal_after_cancel_is_contained_and_never_green() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut supervisor =
            ServiceOwnedNativeSupervisor::new(MockNativeApi::new(&policy, HostileMode::None));
        let mut armed = match supervisor
            .start_to_armed(prepared)
            .expect("valid launch must arm")
        {
            NativeStartOutcome::Armed(run) => run,
            NativeStartOutcome::Terminal(_) => panic!("valid launch must not terminate pre-arm"),
        };
        supervisor
            .request_armed_termination(&mut armed)
            .expect("cancellation must be durably accepted");

        assert!(matches!(
            supervisor.advance_armed(armed),
            NativeAdvanceOutcome::Terminal(ValidatedNativeTerminalRun::Burned(_))
        ));
        assert_eq!(supervisor.api().containment_calls, 1);
        assert_eq!(supervisor.api().seal_calls, 0);
    }

    #[test]
    fn cancelled_terminal_after_durable_intent_uses_normal_cleanup_path() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut api = MockNativeApi::new(&policy, HostileMode::None);
        api.observation.finalization = None;
        api.observation.terminal.kind = TerminalKind::Cancelled;
        api.observation.terminal.intent = TerminalIntent::Burn;
        api.observation.cleanup.final_result_persisted = false;
        let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
        let mut armed = match supervisor
            .start_to_armed(prepared)
            .expect("valid launch must arm")
        {
            NativeStartOutcome::Armed(run) => run,
            NativeStartOutcome::Terminal(_) => panic!("valid launch must not terminate pre-arm"),
        };
        supervisor
            .request_armed_termination(&mut armed)
            .expect("cancellation must be durably accepted");

        let NativeAdvanceOutcome::Terminal(ValidatedNativeTerminalRun::Burned(proof)) =
            supervisor.advance_armed(armed)
        else {
            panic!("cancelled terminal must never complete or remain running");
        };
        assert_eq!(proof.terminal().reason(), BurnReason::Cancelled);
        assert!(proof.admission().is_some());
        assert_eq!(supervisor.api().containment_calls, 0);
        assert_eq!(supervisor.api().seal_calls, 0);
        assert!(supervisor
            .api()
            .events
            .contains(&NativeSupervisorPhase::Contain));
    }

    #[test]
    fn cancel_and_timeout_cannot_use_failure_abort_path() {
        for reason in [BurnReason::Cancelled, BurnReason::TimedOut] {
            let policy = policy();
            let prepared = PreparedRun::from_policy(&policy);
            let mut supervisor =
                ServiceOwnedNativeSupervisor::new(MockNativeApi::new(&policy, HostileMode::None));
            let armed = match supervisor
                .start_to_armed(prepared)
                .expect("valid launch must arm")
            {
                NativeStartOutcome::Armed(run) => run,
                NativeStartOutcome::Terminal(_) => {
                    panic!("valid launch must not terminate pre-arm")
                }
            };
            assert_eq!(
                supervisor
                    .abort_armed(&armed, reason, "must_not_be_used")
                    .expect_err("normal terminal kinds cannot use failure containment")
                    .code(),
                "authority_native_abort_reason_invalid"
            );
            assert_eq!(supervisor.api().containment_calls, 0);
        }
    }

    #[test]
    fn capability_failure_is_pre_mutation_and_does_not_claim_containment() {
        let (result, api) = execute(HostileMode::MissingListenerCapability);
        assert_eq!(
            result.expect_err("missing adoption must block").code(),
            BACKEND_LISTENER_ADOPTION_BLOCKER
        );
        assert_eq!(api.events, vec![NativeSupervisorPhase::Preflight]);
        assert_eq!(api.containment_calls, 0);
        assert_eq!(api.seal_calls, 0);

        let (result, api) = execute(HostileMode::MissingBridgeCapability);
        assert_eq!(
            result.expect_err("missing bridge proxy must block").code(),
            BRIDGE_TARGET_LISTENER_ADOPTION_BLOCKER
        );
        assert_eq!(api.events, vec![NativeSupervisorPhase::Preflight]);
        assert_eq!(api.containment_calls, 0);
        assert_eq!(api.seal_calls, 0);

        let (result, api) = execute(HostileMode::MissingBridgeStartupCapability);
        assert_eq!(
            result
                .expect_err("missing in-memory startup must block")
                .code(),
            BRIDGE_TARGET_IN_MEMORY_STARTUP_BLOCKER
        );
        assert_eq!(api.events, vec![NativeSupervisorPhase::Preflight]);
        assert_eq!(api.containment_calls, 0);
        assert_eq!(api.seal_calls, 0);

        let (result, api) = execute(HostileMode::MissingBridgeRequestAuthCapability);
        assert_eq!(
            result.expect_err("missing request auth must block").code(),
            BRIDGE_TARGET_REQUEST_AUTH_BLOCKER
        );
        assert_eq!(api.events, vec![NativeSupervisorPhase::Preflight]);
        assert_eq!(api.containment_calls, 0);
        assert_eq!(api.seal_calls, 0);
    }

    #[test]
    fn armed_admission_mismatch_after_resume_is_contained_and_burned() {
        let (result, api) = execute(HostileMode::ArmedAdmissionMismatch);
        assert!(matches!(
            result.expect("verified containment must yield a terminal proof"),
            ValidatedNativeTerminalRun::Burned(_)
        ));
        assert_eq!(api.containment_calls, 1);
        assert_eq!(api.seal_calls, 0);
        assert!(api.events.contains(&NativeSupervisorPhase::Resume));
        assert!(!api.events.contains(&NativeSupervisorPhase::ObserveTerminal));
    }

    #[test]
    fn pid_table_only_pipe_or_listener_claims_are_contained_and_never_green() {
        for mode in [HostileMode::PipePidOnly, HostileMode::ListenerPidOnly] {
            let (result, api) = execute(mode);
            assert!(matches!(
                result.expect("verified containment must yield burned proof"),
                ValidatedNativeTerminalRun::Burned(_)
            ));
            assert_eq!(api.containment_calls, 1);
            assert_eq!(api.seal_calls, 0);
        }

        let (result, api) = execute(HostileMode::BootstrapVersionDrift);
        assert!(matches!(
            result.expect("bootstrap drift must be contained"),
            ValidatedNativeTerminalRun::Burned(_)
        ));
        assert_eq!(api.containment_calls, 1);
        assert_eq!(api.seal_calls, 0);
    }

    #[test]
    fn native_job_validator_rejects_legacy_zero_and_every_security_drift() {
        let policy = policy();
        let api = MockNativeApi::new(&policy, HostileMode::None);
        let prepared_receipt = PreparedRecoveryReceipt::from_policy(&policy);
        let policy_snapshot = canonical_supervisor_policy_snapshot(&policy);
        let baseline =
            api.prepared_evidence(&policy, &prepared_receipt, &policy_snapshot, digest(98));
        validate_native_job(&policy, &baseline).expect("exact security receipt");

        let mut mutations = Vec::new();
        let mut value = baseline.job.clone();
        value.security_binding_digest = [0; 32];
        mutations.push(value);
        let mut value = baseline.job.clone();
        value.security_binding_digest = digest(0xee);
        mutations.push(value);
        let mut value = baseline.job.clone();
        value.exact_security_readback = false;
        mutations.push(value);
        let mut value = baseline.job.clone();
        value.owner_local_system = false;
        mutations.push(value);
        let mut value = baseline.job.clone();
        value.dacl_present = false;
        mutations.push(value);
        let mut value = baseline.job.clone();
        value.dacl_defaulted = true;
        mutations.push(value);
        let mut value = baseline.job.clone();
        value.dacl_protected = false;
        mutations.push(value);
        let mut value = baseline.job.clone();
        value.dacl_ace_count = 0;
        mutations.push(value);
        let mut value = baseline.job.clone();
        value.system_access_mask ^= 1;
        mutations.push(value);
        let mut value = baseline.job.clone();
        value.service_access_mask |= 0x0004_0000;
        mutations.push(value);

        let mut legacy = baseline.job.clone();
        legacy.security_binding_digest = [0; 32];
        legacy.exact_security_readback = false;
        legacy.owner_local_system = false;
        legacy.dacl_present = false;
        legacy.dacl_defaulted = true;
        legacy.dacl_protected = false;
        legacy.dacl_ace_count = 0;
        legacy.system_access_mask = 0;
        legacy.service_access_mask = 0;
        mutations.push(legacy);

        for job in mutations {
            let mut hostile = baseline.clone();
            hostile.job = job;
            assert_eq!(
                validate_native_job(&policy, &hostile).unwrap_err().code(),
                "authority_native_job_invalid"
            );
        }
    }

    #[test]
    fn suspended_launch_requires_atomic_job_membership_before_return_and_resume() {
        let policy = policy();
        let mut api = MockNativeApi::new(&policy, HostileMode::None);
        let prepared_receipt = PreparedRecoveryReceipt::from_policy(&policy);
        let policy_snapshot = canonical_supervisor_policy_snapshot(&policy);
        let prepared =
            api.prepared_evidence(&policy, &prepared_receipt, &policy_snapshot, digest(98));
        let suspended = api.suspended_root(&policy);
        validate_suspended_root(&policy, &prepared, &suspended)
            .expect("atomic create-time job membership must validate");
        assert_eq!(
            suspended.raw_handle_list.role(),
            ChildBootstrapRole::LifecycleDriver
        );
        assert_ne!(
            suspended.raw_handle_list.as_bytes(),
            &policy.child_transport_contract_digest
        );

        let mutations: [fn(&mut SuspendedRootReceipt); 3] = [
            |value: &mut SuspendedRootReceipt| value.job_list_attribute_applied = false,
            |value: &mut SuspendedRootReceipt| value.job_assigned_at_creation = false,
            |value: &mut SuspendedRootReceipt| value.job_membership_readback_before_return = false,
        ];
        for mutate in mutations {
            let mut hostile = suspended.clone();
            mutate(&mut hostile);
            assert_eq!(
                validate_suspended_root(&policy, &prepared, &hostile)
                    .unwrap_err()
                    .code(),
                "authority_native_suspended_root_invalid"
            );
        }
        let assignment = api
            .assign_root_to_job(&policy, &prepared, &suspended)
            .expect("readback-only membership verification");
        validate_job_assignment(&policy, &prepared, &suspended, &assignment)
            .expect("readback-only membership must validate");
        let resumed = api
            .resume_root(&policy, &prepared, &suspended, &assignment)
            .expect("independently measured raw list must survive resume");
        validate_resumed_root(&policy, &prepared, &suspended, &assignment, &resumed)
            .expect("exact suspended and resumed raw measurement must match");

        let mut substituted = resumed.clone();
        substituted.raw_handle_list = RoleRawHandleListDigest::derive(
            ChildBootstrapRole::LifecycleDriver,
            &[0x301usize, 0x302usize, 0x303usize],
        )
        .unwrap();
        assert_eq!(
            validate_resumed_root(&policy, &prepared, &suspended, &assignment, &substituted,)
                .unwrap_err()
                .code(),
            "authority_native_resume_sequence_invalid"
        );
        let mut swapped = resumed.clone();
        swapped.raw_handle_list = prepared.bridge_root.resumed.raw_handle_list;
        assert_ne!(swapped.raw_handle_list, suspended.raw_handle_list);
        assert_eq!(
            validate_resumed_root(&policy, &prepared, &suspended, &assignment, &swapped)
                .unwrap_err()
                .code(),
            "authority_native_resume_sequence_invalid"
        );

        let mut hostile = assignment.clone();
        hostile.initial_assignment_call_performed = true;
        assert_eq!(
            validate_job_assignment(&policy, &prepared, &suspended, &hostile)
                .unwrap_err()
                .code(),
            "authority_native_job_assignment_invalid"
        );
        let mut hostile = assignment.clone();
        hostile.job_membership_revalidated = false;
        assert_eq!(
            validate_job_assignment(&policy, &prepared, &suspended, &hostile)
                .unwrap_err()
                .code(),
            "authority_native_job_assignment_invalid"
        );
        let mut hostile = assignment;
        hostile.membership_readback_before_resume = false;
        assert_eq!(
            validate_job_assignment(&policy, &prepared, &suspended, &hostile)
                .unwrap_err()
                .code(),
            "authority_native_job_assignment_invalid"
        );
    }

    #[test]
    fn bridge_launcher_has_its_own_atomic_root_receipt_before_driver_launch() {
        let policy = policy();
        let api = MockNativeApi::new(&policy, HostileMode::None);
        let prepared_receipt = PreparedRecoveryReceipt::from_policy(&policy);
        let policy_snapshot = canonical_supervisor_policy_snapshot(&policy);
        let baseline =
            api.prepared_evidence(&policy, &prepared_receipt, &policy_snapshot, digest(98));
        validate_atomic_root_launch(
            &policy,
            &baseline,
            &baseline.bridge_root,
            ProcessRole::BridgeLauncher,
        )
        .expect("bridge launcher must be atomically job-bound before resume");

        let mut hostile = baseline.clone();
        hostile.bridge_root.suspended.job_list_attribute_applied = false;
        assert_eq!(
            validate_native_prepared(&policy, &prepared_receipt, &policy_snapshot, &hostile,)
                .unwrap_err()
                .code(),
            "authority_native_suspended_root_invalid"
        );

        let mut hostile = baseline.clone();
        hostile
            .bridge_root
            .membership
            .initial_assignment_call_performed = true;
        assert_eq!(
            validate_native_prepared(&policy, &prepared_receipt, &policy_snapshot, &hostile,)
                .unwrap_err()
                .code(),
            "authority_native_job_assignment_invalid"
        );

        let mut hostile = baseline;
        hostile.bridge_root.resumed.resumed_at =
            hostile.bridge_root.membership.membership_verified_at;
        assert_eq!(
            validate_native_prepared(&policy, &prepared_receipt, &policy_snapshot, &hostile,)
                .unwrap_err()
                .code(),
            "authority_native_resume_sequence_invalid"
        );
    }

    #[test]
    fn process_replacement_and_unexpected_job_child_are_contained() {
        for mode in [
            HostileMode::ProcessImageReplacement,
            HostileMode::UnexpectedJobChild,
        ] {
            let (result, api) = execute(mode);
            assert!(matches!(
                result.expect("verified containment must yield burned proof"),
                ValidatedNativeTerminalRun::Burned(_)
            ));
            assert_eq!(api.containment_calls, 1);
            assert_eq!(api.seal_calls, 0);
        }
    }

    #[test]
    fn cleanup_drift_or_handle_residue_prevents_origin_seal() {
        for mode in [
            HostileMode::PortDrift,
            HostileMode::HandleResidue,
            HostileMode::BridgeRequestAuthNotZeroized,
        ] {
            let (result, api) = execute(mode);
            assert!(matches!(
                result.expect("verified containment must yield burned proof"),
                ValidatedNativeTerminalRun::Burned(_)
            ));
            assert_eq!(api.containment_calls, 1);
            assert_eq!(api.seal_calls, 0);
        }
    }

    #[test]
    fn bridge_proxy_is_not_exposed_before_target_health_or_unity_start() {
        let (result, api) = execute(HostileMode::BridgeProxyExposedEarly);
        assert!(matches!(
            result.expect("early bridge exposure must be contained"),
            ValidatedNativeTerminalRun::Burned(_)
        ));
        assert_eq!(api.containment_calls, 1);
        assert_eq!(api.seal_calls, 0);
    }

    #[test]
    fn bridge_target_request_auth_rejects_digest_drift_bypass_and_header_forwarding() {
        for mode in [
            HostileMode::BridgeRequestAuthDigestMismatch,
            HostileMode::BridgeRejectedRequest,
            HostileMode::BridgeRequestCountMismatch,
            HostileMode::BridgeAuthHeaderForwarded,
        ] {
            let (result, api) = execute(mode);
            assert!(matches!(
                result.expect("invalid target request set must be contained"),
                ValidatedNativeTerminalRun::Burned(_)
            ));
            assert_eq!(api.containment_calls, 1);
            assert_eq!(api.seal_calls, 0);
        }
    }

    #[test]
    fn origin_binding_failure_after_cleanup_is_burned_not_completed() {
        for mode in [
            HostileMode::OriginBindingMismatch,
            HostileMode::OriginAuthorityTicketMismatch,
            HostileMode::OriginTicketDigestMismatch,
            HostileMode::OriginTicketsSwapped,
        ] {
            let (result, api) = execute(mode);
            assert!(matches!(
                result.expect("verified containment must yield burned proof"),
                ValidatedNativeTerminalRun::Burned(_)
            ));
            assert_eq!(api.seal_calls, 1);
            assert_eq!(api.containment_calls, 1);
        }
    }

    #[test]
    fn terminal_observation_failure_forces_containment() {
        let (result, api) = execute(HostileMode::TerminalFailure);
        assert!(matches!(
            result.expect("verified containment must yield burned proof"),
            ValidatedNativeTerminalRun::Burned(_)
        ));
        assert_eq!(api.containment_calls, 1);
        assert_eq!(api.seal_calls, 0);
    }

    #[test]
    fn armed_failure_containment_uncertainty_retains_the_live_run_for_retry() {
        let policy = policy();
        let prepared = PreparedRun::from_policy(&policy);
        let mut api = MockNativeApi::new(&policy, HostileMode::TerminalFailure);
        api.failure_containment_uncertain_once = true;
        let mut supervisor = ServiceOwnedNativeSupervisor::new(api);
        let armed = match supervisor
            .start_to_armed(prepared)
            .expect("valid launch must arm")
        {
            NativeStartOutcome::Armed(armed) => armed,
            NativeStartOutcome::Terminal(_) => panic!("valid launch must not terminate pre-arm"),
        };
        let armed = match supervisor.advance_armed(armed) {
            NativeAdvanceOutcome::Retrying(armed, "test_native_failure_containment_uncertain") => {
                armed
            }
            _ => panic!("containment uncertainty must retain the exact Armed run"),
        };
        assert_eq!(supervisor.api().containment_calls, 1);

        assert!(matches!(
            supervisor.advance_armed(armed),
            NativeAdvanceOutcome::Terminal(ValidatedNativeTerminalRun::Burned(_))
        ));
        assert_eq!(supervisor.api().containment_calls, 2);
        assert_eq!(supervisor.api().seal_calls, 0);
    }

    #[test]
    fn adoption_frame_has_exact_big_endian_layout_and_bounded_secret_payloads() {
        let mut bootstrap = vec![0x42; INNER_LIVE_BOOTSTRAP_BYTES];
        bootstrap[..INNER_LIVE_BOOTSTRAP_MAGIC.len()].copy_from_slice(INNER_LIVE_BOOTSTRAP_MAGIC);
        let frame = BackendAdoptionFrame::new(
            digest(1),
            digest(2),
            0x0102_0304_0506_0708,
            vec![0x31; 32],
            bootstrap,
            digest(3),
        )
        .expect("valid frame");
        let encoded = frame.encode_for_private_pipe();
        let bytes = encoded.as_bytes();
        assert_eq!(&bytes[..8], BACKEND_ADOPTION_FRAME_MAGIC);
        assert_eq!(u16::from_be_bytes([bytes[8], bytes[9]]), 1);
        assert_eq!(
            u32::from_be_bytes([bytes[10], bytes[11], bytes[12], bytes[13]]) as usize,
            bytes.len() - 14
        );
        assert_eq!(&bytes[14..46], digest(1));
        assert_eq!(&bytes[46..78], digest(2));
        assert_eq!(bytes[78], BACKEND_ADOPTION_ROLE_APP);
        assert_eq!(
            u16::from_be_bytes([bytes[79], bytes[80]]),
            ADDRESS_FAMILY_IPV4
        );
        assert_eq!(
            u16::from_be_bytes([bytes[89], bytes[90]]),
            APP_LOOPBACK_PORT
        );
        assert_eq!(
            u64::from_be_bytes(bytes[91..99].try_into().expect("listener id")),
            0x0102_0304_0506_0708
        );
        let digest_offset = bytes.len() - 32;
        let mut hasher = Sha256::new();
        hasher.update(BACKEND_ADOPTION_FRAME_DOMAIN);
        hasher.update(&bytes[..digest_offset]);
        assert_eq!(
            &bytes[digest_offset..],
            &<[u8; 32]>::from(hasher.finalize())
        );

        let oversized = BackendAdoptionFrame::new(
            digest(1),
            digest(2),
            1,
            vec![0; MAX_SOCKET_SHARE_BYTES + 1],
            {
                let mut bootstrap = vec![1; INNER_LIVE_BOOTSTRAP_BYTES];
                bootstrap[..INNER_LIVE_BOOTSTRAP_MAGIC.len()]
                    .copy_from_slice(INNER_LIVE_BOOTSTRAP_MAGIC);
                bootstrap
            },
            digest(3),
        );
        assert_eq!(
            match oversized {
                Err(error) => error.code(),
                Ok(_) => panic!("oversized socket share must fail"),
            },
            "authority_backend_adoption_frame_invalid"
        );

        let opaque_bootstrap = BackendAdoptionFrame::new(
            digest(1),
            digest(2),
            1,
            vec![1],
            vec![0x55; INNER_LIVE_BOOTSTRAP_BYTES],
            digest(3),
        );
        assert!(
            matches!(opaque_bootstrap, Err(ref error) if error.code() == "authority_backend_adoption_frame_invalid")
        );
    }

    #[test]
    fn adoption_ack_requires_exact_domain_digest_and_all_runtime_verification_flags() {
        let policy = policy();
        let observation = completed_observation(&policy);
        let prepared = MockNativeApi::new(&policy, HostileMode::None).prepared_evidence(
            &policy,
            &PreparedRecoveryReceipt::from_policy(&policy),
            &canonical_supervisor_policy_snapshot(&policy),
            digest(98),
        );
        let listener = &prepared.listeners[0];
        let backend = &observation.processes[role_index(ProcessRole::Backend)];
        let mut bytes = encode_valid_ack(&policy, &prepared.pipe, listener, backend);
        let ack = BackendAdoptionAck::decode(&bytes).expect("valid ack");
        assert!(ack.verifies_for(&policy, &prepared.pipe, listener, backend));

        let last = bytes.len() - 1;
        bytes[last] ^= 1;
        let tampered = BackendAdoptionAck::decode(&bytes);
        assert_eq!(
            match tampered {
                Err(error) => error.code(),
                Ok(_) => panic!("tampered ack must fail"),
            },
            "authority_backend_adoption_ack_digest_invalid"
        );
    }

    #[test]
    fn bridge_target_frame_has_an_independent_domain_and_exact_boundaries() {
        let policy = policy();
        let adapter_executable_digest =
            policy.process_executable_digests[role_index(ProcessRole::BridgeListener)];
        let frame = BridgeTargetAdoptionFrame::new(
            &policy,
            digest(1),
            digest(2),
            digest(3),
            &bridge_control_pipe_for_frame(digest(4), 0x0102_0304_0506_0708, digest(5)),
            adapter_executable_digest,
            49_221,
            99,
            b"service-owned-socket-share".to_vec(),
            b"one-use-private-startup".to_vec(),
        )
        .expect("valid bridge frame");
        let encoded = frame.encode_for_private_pipe();
        let bytes = encoded.as_bytes();
        assert_eq!(&bytes[..8], BRIDGE_TARGET_ADOPTION_FRAME_MAGIC);
        assert_eq!(u16::from_be_bytes([bytes[8], bytes[9]]), 1);
        assert_eq!(
            u32::from_be_bytes(bytes[10..14].try_into().expect("payload size")) as usize,
            bytes.len() - 14
        );
        assert_eq!(&bytes[14..46], digest(1));
        assert_eq!(&bytes[46..78], digest(2));
        assert_eq!(&bytes[78..110], digest(3));
        assert_eq!(&bytes[110..142], digest(4));
        assert_eq!(&bytes[142..174], digest(5));
        assert_eq!(&bytes[174..206], adapter_executable_digest);
        assert_eq!(&bytes[206..238], policy.bridge_target_manifest_digest);
        assert_eq!(&bytes[238..270], policy.bridge_target_tree_digest);
        assert_eq!(
            u64::from_be_bytes(bytes[270..278].try_into().expect("pipe instance")),
            0x0102_0304_0506_0708
        );
        assert_eq!(bytes[278], BRIDGE_TARGET_ADOPTION_ROLE);
        assert_eq!(
            u16::from_be_bytes(bytes[289..291].try_into().expect("target port")),
            49_221
        );
        assert_eq!(
            u64::from_be_bytes(bytes[291..299].try_into().expect("listener id")),
            99
        );
        assert_eq!(BRIDGE_TARGET_ADOPTION_FRAME_FIXED_PAYLOAD_BYTES, 421);
        assert_eq!(BRIDGE_TARGET_ADOPTION_ACK_PAYLOAD_BYTES, 511);
        assert_eq!(
            u32::from_be_bytes(bytes[299..303].try_into().expect("socket share size")) as usize,
            frame.socket_share_bytes.len()
        );
        assert_eq!(
            bytes.len(),
            14 + BRIDGE_TARGET_ADOPTION_FRAME_FIXED_PAYLOAD_BYTES
                + frame.socket_share_bytes.len()
                + frame.startup_material.len()
        );
        assert_eq!(
            frame.request_auth_key_digest,
            [
                153, 221, 123, 179, 252, 206, 147, 164, 102, 25, 184, 204, 155, 8, 86, 134, 56, 87,
                159, 186, 120, 93, 191, 18, 19, 31, 92, 88, 154, 198, 122, 67,
            ]
        );
        let digest_offset = bytes.len() - 32;
        let mut hasher = Sha256::new();
        hasher.update(BRIDGE_TARGET_ADOPTION_FRAME_DOMAIN);
        hasher.update(&bytes[..digest_offset]);
        assert_eq!(
            &bytes[digest_offset..],
            &<[u8; 32]>::from(hasher.finalize())
        );
        assert_eq!(
            <[u8; 32]>::from(Sha256::digest(bytes)),
            [
                108, 120, 131, 233, 250, 143, 245, 185, 187, 69, 144, 196, 218, 240, 18, 59, 201,
                72, 218, 7, 28, 108, 106, 228, 210, 148, 212, 56, 230, 112, 64, 190,
            ]
        );
    }

    #[test]
    fn bridge_target_frame_rejects_public_ports_and_secret_boundary_drift() {
        let policy = policy();
        let adapter_executable_digest =
            policy.process_executable_digests[role_index(ProcessRole::BridgeListener)];
        for target_port in [0, 1_023, BRIDGE_LOOPBACK_PORT, APP_LOOPBACK_PORT] {
            let result = BridgeTargetAdoptionFrame::new(
                &policy,
                digest(1),
                digest(2),
                digest(3),
                &bridge_control_pipe_for_frame(digest(4), 1, digest(5)),
                adapter_executable_digest,
                target_port,
                1,
                vec![1],
                vec![2],
            );
            assert!(matches!(
                result,
                Err(ref error) if error.code() == "authority_bridge_target_adoption_frame_invalid"
            ));
        }

        let oversized_share = BridgeTargetAdoptionFrame::new(
            &policy,
            digest(1),
            digest(2),
            digest(3),
            &bridge_control_pipe_for_frame(digest(4), 1, digest(5)),
            adapter_executable_digest,
            49_221,
            1,
            vec![1; MAX_BRIDGE_TARGET_SOCKET_SHARE_BYTES + 1],
            vec![2],
        );
        assert!(matches!(
            oversized_share,
            Err(ref error) if error.code() == "authority_bridge_target_adoption_frame_invalid"
        ));

        let empty_startup = BridgeTargetAdoptionFrame::new(
            &policy,
            digest(1),
            digest(2),
            digest(3),
            &bridge_control_pipe_for_frame(digest(4), 1, digest(5)),
            adapter_executable_digest,
            49_221,
            1,
            vec![1],
            Vec::new(),
        );
        assert!(matches!(
            empty_startup,
            Err(ref error) if error.code() == "authority_bridge_target_adoption_frame_invalid"
        ));
    }

    #[test]
    fn bridge_target_frame_binds_policy_owned_target_identity_and_request_auth() {
        let build = |policy: &SupervisorPolicy| {
            BridgeTargetAdoptionFrame::new(
                policy,
                digest(1),
                digest(2),
                digest(3),
                &bridge_control_pipe_for_frame(digest(4), 1, digest(5)),
                policy.process_executable_digests[role_index(ProcessRole::BridgeListener)],
                49_221,
                1,
                vec![1],
                vec![2],
            )
        };

        let policy = policy();
        let frame = build(&policy).expect("policy-bound bridge target frame");

        let mut manifest_drift = policy.clone();
        manifest_drift.bridge_target_manifest_digest = digest(240);
        let manifest_frame = build(&manifest_drift).expect("changed policy manifest");
        assert_ne!(
            manifest_frame.request_auth_key_digest,
            frame.request_auth_key_digest
        );

        let mut tree_drift = policy.clone();
        tree_drift.bridge_target_tree_digest = digest(241);
        let tree_frame = build(&tree_drift).expect("changed policy tree");
        assert_ne!(
            tree_frame.request_auth_key_digest,
            frame.request_auth_key_digest
        );

        let adapter_mismatch = BridgeTargetAdoptionFrame::new(
            &policy,
            digest(1),
            digest(2),
            digest(3),
            &bridge_control_pipe_for_frame(digest(4), 1, digest(5)),
            digest(242),
            49_221,
            1,
            vec![1],
            vec![2],
        );
        assert!(matches!(
            adapter_mismatch,
            Err(ref error) if error.code() == "authority_bridge_target_adoption_frame_invalid"
        ));

        for manifest_is_zero in [true, false] {
            let mut invalid_policy = policy.clone();
            if manifest_is_zero {
                invalid_policy.bridge_target_manifest_digest = [0; 32];
            } else {
                invalid_policy.bridge_target_tree_digest = [0; 32];
            }
            assert!(matches!(
                build(&invalid_policy),
                Err(ref error) if error.code() == "authority_bridge_target_adoption_frame_invalid"
            ));
        }
    }

    #[test]
    fn bridge_target_ack_requires_authenticated_health_and_no_early_proxy_requests() {
        let policy = policy();
        let adapter_executable_digest =
            policy.process_executable_digests[role_index(ProcessRole::BridgeListener)];
        let frame = BridgeTargetAdoptionFrame::new(
            &policy,
            digest(1),
            digest(2),
            digest(3),
            &bridge_control_pipe_for_frame(digest(4), 0x0102_0304_0506_0708, digest(5)),
            adapter_executable_digest,
            49_221,
            99,
            b"service-owned-socket-share".to_vec(),
            b"one-use-private-startup".to_vec(),
        )
        .expect("valid bridge frame");
        let owner = ProcessKey {
            pid: 4_242,
            creation_time: 9_999,
        };
        let mut bytes = encode_valid_bridge_target_ack(
            &frame,
            owner,
            digest(6),
            digest(7),
            BRIDGE_TARGET_ADOPTION_ACK_REQUIRED_FLAGS,
        );
        let ack = BridgeTargetAdoptionAck::decode(&bytes).expect("valid bridge ack");
        assert!(ack.verifies_for(&frame, owner, digest(6), digest(7)));
        assert_eq!(bytes.len(), 14 + BRIDGE_TARGET_ADOPTION_ACK_PAYLOAD_BYTES);
        assert_eq!(
            <[u8; 32]>::from(Sha256::digest(&bytes)),
            [
                187, 166, 209, 94, 40, 141, 1, 181, 117, 212, 169, 255, 247, 79, 239, 23, 102, 223,
                3, 79, 234, 182, 192, 211, 106, 172, 21, 9, 74, 69, 183, 251,
            ]
        );

        let mut invalid_health_count = encode_valid_bridge_target_ack(
            &frame,
            owner,
            digest(6),
            digest(7),
            BRIDGE_TARGET_ADOPTION_ACK_REQUIRED_FLAGS,
        );
        invalid_health_count[395..399].copy_from_slice(&2_u32.to_be_bytes());
        let digest_offset = invalid_health_count.len() - 32;
        let mut hasher = Sha256::new();
        hasher.update(BRIDGE_TARGET_ADOPTION_ACK_DOMAIN);
        hasher.update(&invalid_health_count[..digest_offset]);
        invalid_health_count[digest_offset..].copy_from_slice(&<[u8; 32]>::from(hasher.finalize()));
        assert!(matches!(
            BridgeTargetAdoptionAck::decode(&invalid_health_count),
            Err(ref error) if error.code() == "authority_bridge_target_adoption_ack_invalid"
        ));

        for digest_offset in [206_usize, 238] {
            let mut drift = encode_valid_bridge_target_ack(
                &frame,
                owner,
                digest(6),
                digest(7),
                BRIDGE_TARGET_ADOPTION_ACK_REQUIRED_FLAGS,
            );
            drift[digest_offset] ^= 1;
            resign_bridge_target_ack(&mut drift);
            let drift_ack =
                BridgeTargetAdoptionAck::decode(&drift).expect("authenticated hostile drift");
            assert!(!drift_ack.verifies_for(&frame, owner, digest(6), digest(7)));

            let mut zero = encode_valid_bridge_target_ack(
                &frame,
                owner,
                digest(6),
                digest(7),
                BRIDGE_TARGET_ADOPTION_ACK_REQUIRED_FLAGS,
            );
            zero[digest_offset..digest_offset + 32].fill(0);
            resign_bridge_target_ack(&mut zero);
            assert!(matches!(
                BridgeTargetAdoptionAck::decode(&zero),
                Err(ref error) if error.code() == "authority_bridge_target_adoption_ack_invalid"
            ));
        }

        for missing in [
            BRIDGE_TARGET_ACK_FLAG_HEALTH_READY,
            BRIDGE_TARGET_ACK_FLAG_ORDINARY_BIND_DISABLED,
            BRIDGE_TARGET_ACK_FLAG_STARTUP_CONFIGURATION_APPLIED,
            BRIDGE_TARGET_ACK_FLAG_REQUEST_AUTH_ENABLED,
        ] {
            let incomplete = encode_valid_bridge_target_ack(
                &frame,
                owner,
                digest(6),
                digest(7),
                BRIDGE_TARGET_ADOPTION_ACK_REQUIRED_FLAGS & !missing,
            );
            assert!(matches!(
                BridgeTargetAdoptionAck::decode(&incomplete),
                Err(ref error) if error.code() == "authority_bridge_target_adoption_ack_invalid"
            ));
        }

        let last = bytes.len() - 1;
        bytes[last] ^= 1;
        assert!(matches!(
            BridgeTargetAdoptionAck::decode(&bytes),
            Err(ref error) if error.code() == "authority_bridge_target_adoption_ack_digest_invalid"
        ));
    }

    #[test]
    fn bridge_target_blockers_name_each_unconnected_security_boundary() {
        assert_eq!(
            BRIDGE_TARGET_LISTENER_ADOPTION_BLOCKER,
            "authority_bridge_target_listener_adoption_not_supported"
        );
        assert_eq!(
            BRIDGE_TARGET_REQUEST_AUTH_BLOCKER,
            "authority_bridge_target_request_auth_not_connected"
        );
        assert_eq!(
            BRIDGE_TARGET_IN_MEMORY_STARTUP_BLOCKER,
            "authority_bridge_target_in_memory_startup_not_connected"
        );
    }

    fn encode_valid_bridge_target_ack(
        frame: &BridgeTargetAdoptionFrame,
        owner: ProcessKey,
        owner_executable_digest: Digest,
        owner_image_identity_digest: Digest,
        flags: u16,
    ) -> Vec<u8> {
        let mut bytes = Vec::with_capacity(14 + BRIDGE_TARGET_ADOPTION_ACK_PAYLOAD_BYTES);
        bytes.extend_from_slice(BRIDGE_TARGET_ADOPTION_ACK_MAGIC);
        bytes.extend_from_slice(&BRIDGE_TARGET_ADOPTION_PROTOCOL_VERSION.to_be_bytes());
        bytes.extend_from_slice(&(BRIDGE_TARGET_ADOPTION_ACK_PAYLOAD_BYTES as u32).to_be_bytes());
        bytes.extend_from_slice(&frame.run_binding_digest);
        bytes.extend_from_slice(&frame.ticket_digest);
        bytes.extend_from_slice(&frame.bridge_launch_binding_digest);
        bytes.extend_from_slice(&frame.private_pipe_binding_digest);
        bytes.extend_from_slice(&frame.challenge);
        bytes.extend_from_slice(&frame.adapter_executable_digest);
        bytes.extend_from_slice(&frame.bridge_target_manifest_digest);
        bytes.extend_from_slice(&frame.bridge_target_tree_digest);
        bytes.extend_from_slice(&frame.private_pipe_instance_id.to_be_bytes());
        bytes.push(BRIDGE_TARGET_ADOPTION_ROLE);
        bytes.extend_from_slice(&ADDRESS_FAMILY_IPV4.to_be_bytes());
        bytes.extend_from_slice(&SOCKET_TYPE_STREAM.to_be_bytes());
        bytes.extend_from_slice(&PROTOCOL_TCP.to_be_bytes());
        bytes.extend_from_slice(&LOOPBACK_IPV4_NETWORK_ORDER.to_be_bytes());
        bytes.extend_from_slice(&frame.target_port.to_be_bytes());
        bytes.extend_from_slice(&frame.listener_socket_object_id.to_be_bytes());
        bytes.extend_from_slice(&frame.socket_share_digest);
        bytes.extend_from_slice(&frame.startup_material_digest);
        bytes.extend_from_slice(&frame.request_auth_key_digest);
        bytes.extend_from_slice(&1_u32.to_be_bytes());
        bytes.extend_from_slice(&0_u32.to_be_bytes());
        bytes.extend_from_slice(&0_u32.to_be_bytes());
        bytes.extend_from_slice(&0_u32.to_be_bytes());
        bytes.extend_from_slice(&0_u32.to_be_bytes());
        bytes.extend_from_slice(&owner.pid.to_be_bytes());
        bytes.extend_from_slice(&owner.creation_time.to_be_bytes());
        bytes.extend_from_slice(&owner_executable_digest);
        bytes.extend_from_slice(&owner_image_identity_digest);
        bytes.extend_from_slice(&flags.to_be_bytes());
        let mut hasher = Sha256::new();
        hasher.update(BRIDGE_TARGET_ADOPTION_ACK_DOMAIN);
        hasher.update(&bytes);
        bytes.extend_from_slice(&<[u8; 32]>::from(hasher.finalize()));
        bytes
    }

    fn resign_bridge_target_ack(bytes: &mut [u8]) {
        assert_eq!(bytes.len(), 14 + BRIDGE_TARGET_ADOPTION_ACK_PAYLOAD_BYTES);
        let digest_offset = bytes.len() - 32;
        let mut hasher = Sha256::new();
        hasher.update(BRIDGE_TARGET_ADOPTION_ACK_DOMAIN);
        hasher.update(&bytes[..digest_offset]);
        bytes[digest_offset..].copy_from_slice(&<[u8; 32]>::from(hasher.finalize()));
    }

    fn encode_valid_ack(
        policy: &SupervisorPolicy,
        pipe: &PrivateBackendPipeLease,
        listener: &ServiceListenerLease,
        backend: &ProcessObservation,
    ) -> Vec<u8> {
        let mut bytes = Vec::with_capacity(14 + BACKEND_ADOPTION_ACK_PAYLOAD_BYTES);
        bytes.extend_from_slice(BACKEND_ADOPTION_ACK_MAGIC);
        bytes.extend_from_slice(&BACKEND_ADOPTION_PROTOCOL_VERSION.to_be_bytes());
        bytes.extend_from_slice(&(BACKEND_ADOPTION_ACK_PAYLOAD_BYTES as u32).to_be_bytes());
        bytes.extend_from_slice(&policy.run_binding_digest);
        bytes.extend_from_slice(&pipe.binding_digest);
        bytes.extend_from_slice(&pipe.challenge_digest);
        bytes.push(BACKEND_ADOPTION_ROLE_APP);
        bytes.extend_from_slice(&ADDRESS_FAMILY_IPV4.to_be_bytes());
        bytes.extend_from_slice(&SOCKET_TYPE_STREAM.to_be_bytes());
        bytes.extend_from_slice(&PROTOCOL_TCP.to_be_bytes());
        bytes.extend_from_slice(&LOOPBACK_IPV4_NETWORK_ORDER.to_be_bytes());
        bytes.extend_from_slice(&APP_LOOPBACK_PORT.to_be_bytes());
        bytes.extend_from_slice(&listener.listener_socket_object_id.to_be_bytes());
        bytes.extend_from_slice(&INNER_LIVE_BOOTSTRAP_VERSION.to_be_bytes());
        bytes.extend_from_slice(&pipe.inner_live_bootstrap_digest);
        bytes.extend_from_slice(&backend.key.pid.to_be_bytes());
        bytes.extend_from_slice(&backend.key.creation_time.to_be_bytes());
        bytes.extend_from_slice(&backend.executable_digest);
        bytes.extend_from_slice(&file_identity_digest(&backend.image_handle_identity));
        bytes.extend_from_slice(&BACKEND_ADOPTION_ACK_REQUIRED_FLAGS.to_be_bytes());
        let mut hasher = Sha256::new();
        hasher.update(BACKEND_ADOPTION_ACK_DOMAIN);
        hasher.update(&bytes);
        bytes.extend_from_slice(&<[u8; 32]>::from(hasher.finalize()));
        bytes
    }
}

// This module is intentionally fail-closed until the private backend startup path can adopt
// the exact listener object created by the authority service. Native supervision contracts and
// deterministic tests live here; no fallback based only on a PID/port lookup is permitted.
