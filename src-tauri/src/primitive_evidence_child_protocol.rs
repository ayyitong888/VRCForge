//! Fixed bootstrap frame shared by the two protected child roots.
//!
//! The frame is deliberately incapable of carrying executable names, paths,
//! command lines, serialized objects, or signing material. A platform launcher
//! must deliver exactly one complete frame through the one fixed inherited
//! bootstrap slot. Child entry points do not accept a fallback transport.

#![cfg_attr(not(test), allow(dead_code))]

#[cfg(windows)]
#[path = "primitive_evidence_child_handshake_windows.rs"]
pub(crate) mod windows_child_handshake;

use hmac::{Hmac, Mac};
use sha2::{Digest as Sha2Digest, Sha256};
use std::{
    fmt,
    io::{self, Read, Write},
    mem, ptr,
    sync::atomic::{compiler_fence, Ordering},
};

#[cfg(test)]
use std::sync::atomic::AtomicU8;

pub const CHILD_BOOTSTRAP_FRAME_LEN: usize = 336;
pub const CHILD_BOOTSTRAP_TRANSPORT_READ_LIMIT: usize = CHILD_BOOTSTRAP_FRAME_LEN + 1;
pub const GLOBAL_CAPABILITY_SOURCE_COUNT: usize = 8;
pub const GLOBAL_CAPABILITY_SOURCE_ROLES: [&str; GLOBAL_CAPABILITY_SOURCE_COUNT] = [
    "driver",
    "desktop",
    "backend",
    "unity",
    "bridge_launcher",
    "bridge_listener",
    "fixture_contract",
    "fixture_baseline",
];

const CHILD_BOOTSTRAP_MAGIC: [u8; 8] = *b"VRCCHD02";
const CHILD_BOOTSTRAP_VERSION: u16 = 2;
const CHILD_BOOTSTRAP_HEADER_LEN: usize = 16;
const CHILD_BOOTSTRAP_FIELD_COUNT: usize = 8;
const CHILD_BOOTSTRAP_DIGEST_LEN: usize = 32;
const CHILD_BOOTSTRAP_FIELD_HEADER_LEN: usize = 4;
const CHILD_BOOTSTRAP_FIELD_WIRE_LEN: usize =
    CHILD_BOOTSTRAP_FIELD_HEADER_LEN + CHILD_BOOTSTRAP_DIGEST_LEN;
const CHILD_BOOTSTRAP_BINDING_OFFSET: usize =
    CHILD_BOOTSTRAP_HEADER_LEN + CHILD_BOOTSTRAP_FIELD_COUNT * CHILD_BOOTSTRAP_FIELD_WIRE_LEN;
const CHILD_BOOTSTRAP_BINDING_DOMAIN: &[u8] = b"vrcforge-child-bootstrap-frame-v2\0";
const AUTHORITY_BINDING_DOMAIN: &[u8] = b"vrcforge-child-authority-binding-v1\0";
const TICKET_BINDING_DOMAIN: &[u8] = b"vrcforge-child-ticket-binding-v1\0";
const RUN_BINDING_DOMAIN: &[u8] = b"vrcforge-child-run-binding-v1\0";
const POLICY_BINDING_DOMAIN: &[u8] = b"vrcforge-child-policy-binding-v1\0";
const GLOBAL_CAPABILITY_SET_DOMAIN: &[u8] = b"vrcforge-child-global-capability-set-v1\0";
const PRIVATE_CONTROL_CAPABILITY_COMMITMENT_DOMAIN: &[u8] =
    b"vrcforge-child-private-control-capability-commitment-v1\0";
const ROLE_INHERITED_CAPABILITY_SET_DOMAIN: &[u8] =
    b"vrcforge-child-role-inherited-capability-set-v3\0";
const RAW_HANDLE_LIST_DOMAIN: &[u8] = b"vrcforge-child-role-raw-handle-list-v3\0";
const CHILD_READY_MAGIC: [u8; 8] = *b"VRCRDY02";
const CHILD_READY_VERSION: u16 = 2;
const CHILD_READY_BINDING_DOMAIN: &[u8] = b"vrcforge-child-ready-binding-v2\0";
const CHILD_EXPECTATION_MAGIC: [u8; 8] = *b"VRCEXP02";
const CHILD_EXPECTATION_VERSION: u16 = 2;
const CHILD_EXPECTATION_BINDING_DOMAIN: &[u8] = b"vrcforge-child-expectation-envelope-binding-v2\0";
const CHILD_EXPECTATION_PEER_CHALLENGE_DOMAIN: &[u8] =
    b"vrcforge-child-expectation-peer-challenge-v1\0";
const CHILD_HANDSHAKE_TRANSCRIPT_DOMAIN: &[u8] =
    b"vrcforge-child-bootstrap-handshake-transcript-v2\0";
const CHILD_BOOTSTRAP_ACK_MAGIC: [u8; 8] = *b"VRCACK02";
const CHILD_BOOTSTRAP_ACK_VERSION: u16 = 2;
const CHILD_BOOTSTRAP_ACK_KEY_DOMAIN: &[u8] = b"vrcforge-child-bootstrap-ack-key-v1\0";
const CHILD_BOOTSTRAP_ACK_MAC_DOMAIN: &[u8] = b"vrcforge-child-bootstrap-ack-mac-v2\0";
const CHILD_VALIDATED_CONTEXT_DOMAIN: &[u8] = b"vrcforge-child-validated-context-v1\0";
const CHILD_OBSERVED_LAUNCH_CONTEXT_DOMAIN: &[u8] = b"vrcforge-child-observed-launch-context-v1\0";
const FINAL_GENERATION_CONTEXT_DOMAIN: &[u8] = b"vrcforge-final-generation-context-v1\0";
const CHILD_TRANSPORT_CONTRACT_CONTEXT_DOMAIN: &[u8] =
    b"vrcforge-child-transport-contract-context-v1\0";
const START_CONTRACT_CONTEXT_DOMAIN: &[u8] = b"vrcforge-start-contract-context-v1\0";
const JOB_MEMBERSHIP_EPOCH_CONTEXT_DOMAIN: &[u8] = b"vrcforge-job-membership-epoch-context-v1\0";
const RUNNER_TOKEN_CONTEXT_DOMAIN: &[u8] = b"vrcforge-runner-token-context-v1\0";
const CHILD_IMAGE_CONTEXT_DOMAIN: &[u8] = b"vrcforge-child-image-context-v1\0";
const MINIMAL_ENVIRONMENT_CONTEXT_DOMAIN: &[u8] = b"vrcforge-minimal-environment-context-v1\0";
const CONTROL_SERVER_IDENTITY_CONTEXT_DOMAIN: &[u8] =
    b"vrcforge-control-server-identity-context-v1\0";
const PARENT_PROTOCOL_FIELD_PROJECTION_DOMAIN: &[u8] =
    b"vrcforge-parent-protocol-field-projection-v1\0";
const AUTHORITY_EXECUTION_CONTEXT_BINDING_DOMAIN: &[u8] =
    b"vrcforge-authority-execution-context-binding-v1\0";

const CHILD_CONTROL_MESSAGE_HEADER_LEN: usize = 16;
pub(crate) const CHILD_HANDSHAKE_NONCE_LEN: usize = 32;
pub(crate) const CHILD_READY_MESSAGE_LEN: usize =
    CHILD_CONTROL_MESSAGE_HEADER_LEN + 4 * CHILD_BOOTSTRAP_DIGEST_LEN + CHILD_BOOTSTRAP_DIGEST_LEN;
const CHILD_READY_BINDING_OFFSET: usize = CHILD_READY_MESSAGE_LEN - CHILD_BOOTSTRAP_DIGEST_LEN;
const CHILD_EXPECTATION_FIELD_COUNT: usize = 21;
pub(crate) const CHILD_EXPECTATION_ENVELOPE_LEN: usize = CHILD_CONTROL_MESSAGE_HEADER_LEN
    + CHILD_EXPECTATION_FIELD_COUNT * CHILD_BOOTSTRAP_DIGEST_LEN
    + CHILD_BOOTSTRAP_DIGEST_LEN;
const CHILD_EXPECTATION_BINDING_OFFSET: usize =
    CHILD_EXPECTATION_ENVELOPE_LEN - CHILD_BOOTSTRAP_DIGEST_LEN;
const CHILD_BOOTSTRAP_ACK_FIELD_COUNT: usize = 6;
pub(crate) const CHILD_BOOTSTRAP_ACK_LEN: usize = CHILD_CONTROL_MESSAGE_HEADER_LEN
    + CHILD_BOOTSTRAP_ACK_FIELD_COUNT * CHILD_BOOTSTRAP_DIGEST_LEN
    + CHILD_BOOTSTRAP_DIGEST_LEN;
const CHILD_BOOTSTRAP_ACK_MAC_OFFSET: usize = CHILD_BOOTSTRAP_ACK_LEN - CHILD_BOOTSTRAP_DIGEST_LEN;

type HmacSha256 = Hmac<Sha256>;

pub type BootstrapDigest = [u8; CHILD_BOOTSTRAP_DIGEST_LEN];

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) struct ParentProtocolFieldProjectionDigest(BootstrapDigest);

impl ParentProtocolFieldProjectionDigest {
    pub(crate) fn as_bytes(&self) -> &BootstrapDigest {
        &self.0
    }
}

impl fmt::Debug for ParentProtocolFieldProjectionDigest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ParentProtocolFieldProjectionDigest(<redacted>)")
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) struct AuthorityChildExecutionContextBindingDigest(BootstrapDigest);

impl AuthorityChildExecutionContextBindingDigest {
    pub(crate) fn as_bytes(&self) -> &BootstrapDigest {
        &self.0
    }
}

impl fmt::Debug for AuthorityChildExecutionContextBindingDigest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("AuthorityChildExecutionContextBindingDigest(<redacted>)")
    }
}

#[cfg(test)]
static ZEROIZED_DROP_MASK: AtomicU8 = AtomicU8::new(0);
#[cfg(test)]
const SECRET_DROP_BIT: u8 = 1;
#[cfg(test)]
const ENCODED_FRAME_DROP_BIT: u8 = 2;
#[cfg(test)]
const TRANSPORT_FRAME_DROP_BIT: u8 = 4;
#[cfg(test)]
const READY_MESSAGE_DROP_BIT: u8 = 8;
#[cfg(test)]
const EXPECTATION_MESSAGE_DROP_BIT: u8 = 16;
#[cfg(test)]
const ACK_MESSAGE_DROP_BIT: u8 = 32;
#[cfg(test)]
const ACK_KEY_DROP_BIT: u8 = 64;

#[cfg(test)]
fn record_zeroized_drop(bit: u8, bytes: &[u8]) {
    if bytes.iter().all(|byte| *byte == 0) {
        ZEROIZED_DROP_MASK.fetch_or(bit, Ordering::SeqCst);
    }
}

#[inline(never)]
fn volatile_zero(bytes: &mut [u8]) {
    for byte in bytes {
        // SAFETY: `byte` is an exclusive reference to one initialized byte.
        // Volatile writes plus the compiler fence keep the erasure observable.
        unsafe { ptr::write_volatile(byte, 0) };
    }
    compiler_fence(Ordering::SeqCst);
}

struct SensitiveBytes<const N: usize>([u8; N]);

impl<const N: usize> SensitiveBytes<N> {
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

impl<const N: usize> Drop for SensitiveBytes<N> {
    fn drop(&mut self) {
        volatile_zero(&mut self.0);
    }
}

fn derive_domain_binding(
    domain: &'static [u8],
    source: &BootstrapDigest,
    error_code: &'static str,
) -> Result<BootstrapDigest, ChildProtocolError> {
    if is_zero_digest(source) {
        return Err(ChildProtocolError::new(error_code));
    }
    let mut hasher = Sha256::new();
    hasher.update(domain);
    hasher.update((source.len() as u16).to_be_bytes());
    hasher.update(source);
    let value = hasher.finalize().into();
    if is_zero_digest(&value) {
        return Err(ChildProtocolError::new(error_code));
    }
    Ok(value)
}

macro_rules! define_binding_digest {
    ($name:ident, $domain:ident, $error:literal) => {
        #[derive(Clone, Copy, PartialEq, Eq)]
        pub struct $name(BootstrapDigest);

        impl $name {
            pub fn derive(source: &BootstrapDigest) -> Result<Self, ChildProtocolError> {
                Ok(Self(derive_domain_binding($domain, source, $error)?))
            }

            fn from_wire(value: BootstrapDigest) -> Result<Self, ChildProtocolError> {
                if is_zero_digest(&value) {
                    return Err(ChildProtocolError::new($error));
                }
                Ok(Self(value))
            }

            pub fn as_bytes(&self) -> &BootstrapDigest {
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

define_binding_digest!(
    AuthorityBindingDigest,
    AUTHORITY_BINDING_DOMAIN,
    "child_bootstrap_authority_binding_invalid"
);
define_binding_digest!(
    TicketBindingDigest,
    TICKET_BINDING_DOMAIN,
    "child_bootstrap_ticket_binding_invalid"
);
define_binding_digest!(
    RunBindingDigest,
    RUN_BINDING_DOMAIN,
    "child_bootstrap_run_binding_invalid"
);
define_binding_digest!(
    PolicyBindingDigest,
    POLICY_BINDING_DOMAIN,
    "child_bootstrap_policy_binding_invalid"
);
define_binding_digest!(
    ChildObservedLaunchContextDigest,
    CHILD_OBSERVED_LAUNCH_CONTEXT_DOMAIN,
    "child_observed_launch_context_invalid"
);
define_binding_digest!(
    FinalGenerationContextDigest,
    FINAL_GENERATION_CONTEXT_DOMAIN,
    "child_final_generation_context_invalid"
);
define_binding_digest!(
    ChildTransportContractContextDigest,
    CHILD_TRANSPORT_CONTRACT_CONTEXT_DOMAIN,
    "child_transport_contract_context_invalid"
);
define_binding_digest!(
    StartContractContextDigest,
    START_CONTRACT_CONTEXT_DOMAIN,
    "child_start_contract_context_invalid"
);
define_binding_digest!(
    JobMembershipEpochContextDigest,
    JOB_MEMBERSHIP_EPOCH_CONTEXT_DOMAIN,
    "child_job_membership_epoch_context_invalid"
);
define_binding_digest!(
    RunnerTokenContextDigest,
    RUNNER_TOKEN_CONTEXT_DOMAIN,
    "child_runner_token_context_invalid"
);
define_binding_digest!(
    ChildImageContextDigest,
    CHILD_IMAGE_CONTEXT_DOMAIN,
    "child_image_context_invalid"
);
define_binding_digest!(
    MinimalEnvironmentContextDigest,
    MINIMAL_ENVIRONMENT_CONTEXT_DOMAIN,
    "child_minimal_environment_context_invalid"
);
define_binding_digest!(
    ControlServerIdentityContextDigest,
    CONTROL_SERVER_IDENTITY_CONTEXT_DOMAIN,
    "child_control_server_identity_context_invalid"
);

pub struct PrivateControlCapability(BootstrapDigest);

impl PrivateControlCapability {
    /// Moves freshly generated parent-side capability material into the
    /// protocol owner and erases the caller's source buffer.
    pub(crate) fn take_for_parent(
        source: &mut BootstrapDigest,
    ) -> Result<Self, ChildProtocolError> {
        Self::take_and_erase(source)
    }

    #[cfg(test)]
    pub fn take_from(source: &mut BootstrapDigest) -> Result<Self, ChildProtocolError> {
        Self::take_and_erase(source)
    }

    fn take_and_erase(source: &mut BootstrapDigest) -> Result<Self, ChildProtocolError> {
        if is_zero_digest(source) {
            volatile_zero(source);
            return Err(ChildProtocolError::new(
                "child_bootstrap_private_control_capability_invalid",
            ));
        }
        let mut value = [0; CHILD_BOOTSTRAP_DIGEST_LEN];
        value.copy_from_slice(source);
        volatile_zero(source);
        Ok(Self(value))
    }

    fn take_from_wire(source: &mut BootstrapDigest) -> Result<Self, ChildProtocolError> {
        if is_zero_digest(source) {
            volatile_zero(source);
            return Err(ChildProtocolError::new(
                "child_bootstrap_private_control_capability_invalid",
            ));
        }
        let mut value = [0; CHILD_BOOTSTRAP_DIGEST_LEN];
        value.copy_from_slice(source);
        volatile_zero(source);
        Ok(Self(value))
    }

    fn encoded_field(&self) -> &BootstrapDigest {
        &self.0
    }

    fn commitment(&self) -> Result<PrivateControlCapabilityCommitment, ChildProtocolError> {
        PrivateControlCapabilityCommitment::derive(self)
    }

    fn derive_ack_key_and_consume(&mut self) -> Result<BootstrapAckKey, ChildProtocolError> {
        let key = BootstrapAckKey::derive(self)?;
        volatile_zero(&mut self.0);
        Ok(key)
    }

    #[cfg(test)]
    fn bytes_for_test(&self) -> &BootstrapDigest {
        &self.0
    }

    #[cfg(test)]
    fn zeroize_for_test(&mut self) {
        volatile_zero(&mut self.0);
    }
}

impl fmt::Debug for PrivateControlCapability {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("PrivateControlCapability(<redacted>)")
    }
}

impl Drop for PrivateControlCapability {
    fn drop(&mut self) {
        volatile_zero(&mut self.0);
        #[cfg(test)]
        record_zeroized_drop(SECRET_DROP_BIT, &self.0);
    }
}

struct BootstrapAckKey(BootstrapDigest);

impl BootstrapAckKey {
    fn derive(capability: &PrivateControlCapability) -> Result<Self, ChildProtocolError> {
        let mut derivation = HmacSha256::new_from_slice(capability.encoded_field())
            .expect("HMAC-SHA256 accepts fixed protocol keys");
        derivation.update(CHILD_BOOTSTRAP_ACK_KEY_DOMAIN);
        let value: BootstrapDigest = derivation.finalize().into_bytes().into();
        if is_zero_digest(&value) {
            return Err(ChildProtocolError::new("child_bootstrap_ack_key_invalid"));
        }
        Ok(Self(value))
    }

    fn as_bytes(&self) -> &BootstrapDigest {
        &self.0
    }
}

impl fmt::Debug for BootstrapAckKey {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("BootstrapAckKey(<redacted>)")
    }
}

impl Drop for BootstrapAckKey {
    fn drop(&mut self) {
        volatile_zero(&mut self.0);
        #[cfg(test)]
        record_zeroized_drop(ACK_KEY_DROP_BIT, &self.0);
    }
}

/// Domain-separated, non-bearer commitment supplied only by the independent
/// expectation source. The frame continues to carry the one-use secret; this
/// value lets validation prove that the committed service expected that exact
/// secret without adding another wire field to the frame.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct PrivateControlCapabilityCommitment(BootstrapDigest);

impl PrivateControlCapabilityCommitment {
    fn derive(capability: &PrivateControlCapability) -> Result<Self, ChildProtocolError> {
        let mut digest = Sha256::new();
        digest.update(PRIVATE_CONTROL_CAPABILITY_COMMITMENT_DOMAIN);
        digest.update((capability.encoded_field().len() as u16).to_be_bytes());
        digest.update(capability.encoded_field());
        let value = digest.finalize().into();
        if is_zero_digest(&value) {
            return Err(ChildProtocolError::new(
                "child_bootstrap_private_control_commitment_invalid",
            ));
        }
        Ok(Self(value))
    }

    fn encoded_field(&self) -> &BootstrapDigest {
        &self.0
    }

    pub(crate) fn from_parent_capability(
        capability: &PrivateControlCapability,
    ) -> Result<Self, ChildProtocolError> {
        Self::derive(capability)
    }

    fn from_wire(value: BootstrapDigest) -> Result<Self, ChildProtocolError> {
        if is_zero_digest(&value) {
            return Err(ChildProtocolError::new(
                "child_bootstrap_private_control_commitment_invalid",
            ));
        }
        Ok(Self(value))
    }
}

impl fmt::Debug for PrivateControlCapabilityCommitment {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("PrivateControlCapabilityCommitment(<redacted>)")
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum ChildBootstrapRole {
    LifecycleDriver = 1,
    BridgeLauncher = 2,
}

impl ChildBootstrapRole {
    fn from_wire(value: u8) -> Result<Self, ChildProtocolError> {
        match value {
            1 => Ok(Self::LifecycleDriver),
            2 => Ok(Self::BridgeLauncher),
            _ => Err(ChildProtocolError::new("child_bootstrap_role_invalid")),
        }
    }

    pub(crate) fn wire_value(self) -> u8 {
        self as u8
    }
}

/// The only inherited capability slot from which either child may load its
/// bootstrap. The native launcher wiring remains a separate, closed slice.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum ChildBootstrapTransportSlot {
    BootstrapFrame = 1,
}

impl ChildBootstrapTransportSlot {
    pub const fn fixed() -> Self {
        Self::BootstrapFrame
    }

    pub fn try_from_id(value: u8) -> Result<Self, ChildProtocolError> {
        match value {
            1 => Ok(Self::BootstrapFrame),
            _ => Err(ChildProtocolError::new(
                "child_bootstrap_transport_slot_invalid",
            )),
        }
    }

    pub const fn id(self) -> u8 {
        self as u8
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChildProtocolError(&'static str);

impl ChildProtocolError {
    fn new(code: &'static str) -> Self {
        Self(code)
    }

    pub fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for ChildProtocolError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for ChildProtocolError {}

/// Domain-separated digest of the exact fixed eight-source capability set.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct GlobalCapabilitySetDigest(BootstrapDigest);

impl GlobalCapabilitySetDigest {
    pub fn derive(
        source_capability_digests: &[BootstrapDigest; GLOBAL_CAPABILITY_SOURCE_COUNT],
    ) -> Result<Self, ChildProtocolError> {
        validate_distinct_nonzero_digests(
            source_capability_digests,
            "child_bootstrap_global_capability_set_invalid",
        )?;
        let mut hasher = Sha256::new();
        hasher.update(GLOBAL_CAPABILITY_SET_DOMAIN);
        hasher.update((GLOBAL_CAPABILITY_SOURCE_COUNT as u16).to_be_bytes());
        for (role, digest) in GLOBAL_CAPABILITY_SOURCE_ROLES
            .iter()
            .zip(source_capability_digests)
        {
            hasher.update((role.len() as u16).to_be_bytes());
            hasher.update(role.as_bytes());
            hasher.update(digest);
        }
        Self::from_wire(hasher.finalize().into())
    }

    fn from_wire(value: BootstrapDigest) -> Result<Self, ChildProtocolError> {
        if is_zero_digest(&value) {
            return Err(ChildProtocolError::new(
                "child_bootstrap_global_capability_set_invalid",
            ));
        }
        Ok(Self(value))
    }

    pub fn as_bytes(&self) -> &BootstrapDigest {
        &self.0
    }
}

impl fmt::Debug for GlobalCapabilitySetDigest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("GlobalCapabilitySetDigest(<redacted>)")
    }
}

/// One jointly derived, role-specific capability contract. Its semantic and
/// raw-handle projections always come from the same fixed, ordered slot table.
/// The Windows transport is the only production source of verified slot
/// observations; this binding alone is never an authority expectation source.
#[derive(Clone, PartialEq, Eq)]
pub struct RoleCapabilitySetBinding {
    role: ChildBootstrapRole,
    slot_count: u16,
    semantic_digest: BootstrapDigest,
    raw_handle_list_digest: BootstrapDigest,
}

/// Canonical, role-aware measurement of the exact three raw startup values.
/// The digest exposes no raw value and is intentionally independent of
/// capability content, server identity, and policy expectations.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) struct RoleRawHandleListDigest {
    role: ChildBootstrapRole,
    digest: BootstrapDigest,
}

impl RoleRawHandleListDigest {
    pub(crate) fn derive(
        role: ChildBootstrapRole,
        raw_handles: &[usize; CHILD_STANDARD_HANDLE_SLOT_COUNT],
    ) -> Result<Self, ChildProtocolError> {
        if raw_handles
            .iter()
            .any(|value| *value == 0 || *value == usize::MAX)
            || raw_handles
                .iter()
                .enumerate()
                .any(|(index, value)| raw_handles[..index].contains(value))
        {
            return Err(ChildProtocolError::new(
                "child_bootstrap_raw_handle_list_invalid",
            ));
        }
        let schema = child_role_capability_schema(role);
        let mut hasher = Sha256::new();
        hasher.update(RAW_HANDLE_LIST_DOMAIN);
        hasher.update([role.wire_value()]);
        hasher.update([mem::size_of::<usize>() as u8]);
        hasher.update((CHILD_STANDARD_HANDLE_SLOT_COUNT as u16).to_be_bytes());
        for (index, (descriptor, raw_handle)) in schema.iter().zip(raw_handles).enumerate() {
            let access = descriptor.purpose().access_contract();
            hasher.update((index as u16).to_be_bytes());
            hasher.update(descriptor.semantic().wire_value().to_be_bytes());
            hasher.update([descriptor.purpose().wire_value()]);
            hasher.update([
                u8::from(access.readable()),
                u8::from(access.writable()),
                u8::from(access.metadata_readable()),
            ]);
            hasher.update(raw_handle.to_ne_bytes());
        }
        let value: BootstrapDigest = hasher.finalize().into();
        if is_zero_digest(&value) {
            return Err(ChildProtocolError::new(
                "child_bootstrap_raw_handle_list_invalid",
            ));
        }
        Ok(Self {
            role,
            digest: value,
        })
    }

    fn from_wire(
        role: ChildBootstrapRole,
        digest: BootstrapDigest,
    ) -> Result<Self, ChildProtocolError> {
        if is_zero_digest(&digest) {
            return Err(ChildProtocolError::new(
                "child_bootstrap_raw_handle_list_invalid",
            ));
        }
        Ok(Self { role, digest })
    }

    pub(crate) const fn role(&self) -> ChildBootstrapRole {
        self.role
    }

    pub(crate) fn as_bytes(&self) -> &BootstrapDigest {
        &self.digest
    }
}

impl fmt::Debug for RoleRawHandleListDigest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RoleRawHandleListDigest")
            .field("role", &self.role)
            .field("digest", &"<redacted>")
            .finish()
    }
}

impl RoleCapabilitySetBinding {
    pub fn role(&self) -> ChildBootstrapRole {
        self.role
    }

    pub fn slot_count(&self) -> u16 {
        self.slot_count
    }

    pub fn semantic_digest(&self) -> &BootstrapDigest {
        &self.semantic_digest
    }

    pub fn raw_handle_list_digest(&self) -> &BootstrapDigest {
        &self.raw_handle_list_digest
    }

    pub(crate) fn derive_from_fixed_slots(
        role: ChildBootstrapRole,
        slots: &[ChildRoleCapabilitySlotBinding],
    ) -> Result<Self, ChildProtocolError> {
        let expected = child_role_capability_schema(role);
        if slots.len() != expected.len() {
            return Err(ChildProtocolError::new(
                "child_bootstrap_role_capability_count_invalid",
            ));
        }

        let mut semantic_values = Vec::with_capacity(slots.len());
        let mut raw_values = Vec::with_capacity(slots.len());
        for (slot, expected_descriptor) in slots.iter().zip(expected) {
            if slot.semantic != expected_descriptor.semantic() {
                return Err(ChildProtocolError::new(
                    "child_bootstrap_role_capability_mapping_invalid",
                ));
            }
            if is_zero_digest(&slot.capability_digest)
                || semantic_values
                    .iter()
                    .any(|prior| digests_equal(prior, &slot.capability_digest))
                || slot.raw_handle == 0
                || slot.raw_handle == usize::MAX
                || raw_values.contains(&slot.raw_handle)
            {
                return Err(ChildProtocolError::new(
                    "child_bootstrap_role_capability_slot_invalid",
                ));
            }
            semantic_values.push(slot.capability_digest);
            raw_values.push(slot.raw_handle);
        }

        let mut semantic_hasher = Sha256::new();
        semantic_hasher.update(ROLE_INHERITED_CAPABILITY_SET_DOMAIN);
        semantic_hasher.update([role.wire_value()]);
        semantic_hasher.update((slots.len() as u16).to_be_bytes());
        for (slot, descriptor) in slots.iter().zip(expected) {
            let access = descriptor.purpose().access_contract();
            semantic_hasher.update(slot.semantic.wire_value().to_be_bytes());
            semantic_hasher.update([descriptor.purpose().wire_value()]);
            semantic_hasher.update([
                u8::from(access.readable()),
                u8::from(access.writable()),
                u8::from(access.metadata_readable()),
            ]);
            semantic_hasher.update(slot.capability_digest);
        }
        let semantic_digest: BootstrapDigest = semantic_hasher.finalize().into();

        let raw_handles = std::array::from_fn(|index| slots[index].raw_handle);
        let raw_handle_list_digest =
            *RoleRawHandleListDigest::derive(role, &raw_handles)?.as_bytes();
        if is_zero_digest(&semantic_digest)
            || is_zero_digest(&raw_handle_list_digest)
            || digests_equal(&semantic_digest, &raw_handle_list_digest)
        {
            return Err(ChildProtocolError::new(
                "child_bootstrap_role_capability_set_invalid",
            ));
        }
        Ok(Self {
            role,
            slot_count: slots.len() as u16,
            semantic_digest,
            raw_handle_list_digest,
        })
    }

    fn from_authenticated_expectation(
        role: ChildBootstrapRole,
        semantic_digest: BootstrapDigest,
        raw_handle_list_digest: RoleRawHandleListDigest,
    ) -> Result<Self, ChildProtocolError> {
        if raw_handle_list_digest.role() != role
            || is_zero_digest(&semantic_digest)
            || digests_equal(&semantic_digest, raw_handle_list_digest.as_bytes())
        {
            return Err(ChildProtocolError::new(
                "child_bootstrap_role_capability_set_invalid",
            ));
        }
        Ok(Self {
            role,
            slot_count: CHILD_STANDARD_HANDLE_SLOT_COUNT as u16,
            semantic_digest,
            raw_handle_list_digest: *raw_handle_list_digest.as_bytes(),
        })
    }

    #[cfg(test)]
    pub(crate) fn derive_for_test(
        role: ChildBootstrapRole,
        slots: &[ChildRoleCapabilitySlotBinding],
    ) -> Result<Self, ChildProtocolError> {
        Self::derive_from_fixed_slots(role, slots)
    }
}

impl fmt::Debug for RoleCapabilitySetBinding {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RoleCapabilitySetBinding")
            .field("role", &self.role)
            .field("slotCount", &self.slot_count)
            .field("semanticDigest", &"<redacted>")
            .field("rawHandleListDigest", &"<redacted>")
            .finish()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u16)]
pub(crate) enum ChildRoleCapabilitySlot {
    DriverBootstrapRead = 1,
    DriverPrivateControlDuplex = 2,
    DriverStructuredResultWrite = 3,
    BridgeBootstrapRead = 101,
    BridgePrivateControlDuplex = 102,
    BridgeStructuredResultWrite = 103,
}

impl ChildRoleCapabilitySlot {
    pub(crate) fn wire_value(self) -> u16 {
        self as u16
    }
}

pub(crate) const CHILD_STANDARD_HANDLE_SLOT_COUNT: usize = 3;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub(crate) enum ChildStandardHandlePurpose {
    BootstrapRead = 1,
    PrivateControlDuplex = 2,
    StructuredResultWrite = 3,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ChildStandardHandleAccessContract {
    readable: bool,
    writable: bool,
    metadata_readable: bool,
}

impl ChildStandardHandleAccessContract {
    pub(crate) const fn readable(self) -> bool {
        self.readable
    }

    pub(crate) const fn writable(self) -> bool {
        self.writable
    }

    pub(crate) const fn metadata_readable(self) -> bool {
        self.metadata_readable
    }
}

impl ChildStandardHandlePurpose {
    pub(crate) fn wire_value(self) -> u8 {
        self as u8
    }

    pub(crate) const fn access_contract(self) -> ChildStandardHandleAccessContract {
        match self {
            Self::BootstrapRead => ChildStandardHandleAccessContract {
                readable: true,
                writable: false,
                metadata_readable: false,
            },
            Self::PrivateControlDuplex => ChildStandardHandleAccessContract {
                readable: true,
                writable: true,
                metadata_readable: false,
            },
            Self::StructuredResultWrite => ChildStandardHandleAccessContract {
                readable: false,
                writable: true,
                metadata_readable: true,
            },
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ChildRoleCapabilitySlotDescriptor {
    semantic: ChildRoleCapabilitySlot,
    purpose: ChildStandardHandlePurpose,
}

impl ChildRoleCapabilitySlotDescriptor {
    const fn new(semantic: ChildRoleCapabilitySlot, purpose: ChildStandardHandlePurpose) -> Self {
        Self { semantic, purpose }
    }

    pub(crate) fn semantic(self) -> ChildRoleCapabilitySlot {
        self.semantic
    }

    pub(crate) fn purpose(self) -> ChildStandardHandlePurpose {
        self.purpose
    }
}

const DRIVER_CAPABILITY_SCHEMA: [ChildRoleCapabilitySlotDescriptor;
    CHILD_STANDARD_HANDLE_SLOT_COUNT] = [
    ChildRoleCapabilitySlotDescriptor::new(
        ChildRoleCapabilitySlot::DriverBootstrapRead,
        ChildStandardHandlePurpose::BootstrapRead,
    ),
    ChildRoleCapabilitySlotDescriptor::new(
        ChildRoleCapabilitySlot::DriverPrivateControlDuplex,
        ChildStandardHandlePurpose::PrivateControlDuplex,
    ),
    ChildRoleCapabilitySlotDescriptor::new(
        ChildRoleCapabilitySlot::DriverStructuredResultWrite,
        ChildStandardHandlePurpose::StructuredResultWrite,
    ),
];

const BRIDGE_CAPABILITY_SCHEMA: [ChildRoleCapabilitySlotDescriptor;
    CHILD_STANDARD_HANDLE_SLOT_COUNT] = [
    ChildRoleCapabilitySlotDescriptor::new(
        ChildRoleCapabilitySlot::BridgeBootstrapRead,
        ChildStandardHandlePurpose::BootstrapRead,
    ),
    ChildRoleCapabilitySlotDescriptor::new(
        ChildRoleCapabilitySlot::BridgePrivateControlDuplex,
        ChildStandardHandlePurpose::PrivateControlDuplex,
    ),
    ChildRoleCapabilitySlotDescriptor::new(
        ChildRoleCapabilitySlot::BridgeStructuredResultWrite,
        ChildStandardHandlePurpose::StructuredResultWrite,
    ),
];

pub(crate) fn child_role_capability_schema(
    role: ChildBootstrapRole,
) -> &'static [ChildRoleCapabilitySlotDescriptor; CHILD_STANDARD_HANDLE_SLOT_COUNT] {
    match role {
        ChildBootstrapRole::LifecycleDriver => &DRIVER_CAPABILITY_SCHEMA,
        ChildBootstrapRole::BridgeLauncher => &BRIDGE_CAPABILITY_SCHEMA,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ChildRoleCapabilitySlotBinding {
    semantic: ChildRoleCapabilitySlot,
    capability_digest: BootstrapDigest,
    raw_handle: usize,
}

impl ChildRoleCapabilitySlotBinding {
    pub(crate) fn new(
        semantic: ChildRoleCapabilitySlot,
        capability_digest: BootstrapDigest,
        raw_handle: usize,
    ) -> Self {
        Self {
            semantic,
            capability_digest,
            raw_handle,
        }
    }
}

#[cfg(test)]
pub(crate) type TestRoleCapabilitySlotBinding = ChildRoleCapabilitySlotBinding;

/// Exact run bindings projected by the authority into a child bootstrap.
///
/// Every non-secret binding has a distinct type and derivation domain. The
/// private control capability is an owning, non-Copy bearer secret.
pub struct ChildBootstrapBindings {
    authority_digest: AuthorityBindingDigest,
    ticket_digest: TicketBindingDigest,
    run_digest: RunBindingDigest,
    policy_digest: PolicyBindingDigest,
    global_capability_set_digest: GlobalCapabilitySetDigest,
    role_capability_set: RoleCapabilitySetBinding,
    private_control_capability: PrivateControlCapability,
}

impl fmt::Debug for ChildBootstrapBindings {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ChildBootstrapBindings")
            .field("authorityDigest", &"<redacted>")
            .field("ticketDigest", &"<redacted>")
            .field("runDigest", &"<redacted>")
            .field("policyDigest", &"<redacted>")
            .field("globalCapabilitySetDigest", &"<redacted>")
            .field("roleInheritedCapabilitySetDigest", &"<redacted>")
            .field("rawHandleListDigest", &"<redacted>")
            .field("privateControlCapability", &"<redacted>")
            .finish()
    }
}

impl ChildBootstrapBindings {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn prepare_for_parent(
        authority_digest: AuthorityBindingDigest,
        ticket_digest: TicketBindingDigest,
        run_digest: RunBindingDigest,
        policy_digest: PolicyBindingDigest,
        global_capability_set_digest: GlobalCapabilitySetDigest,
        role_capability_set: RoleCapabilitySetBinding,
        private_control_capability: PrivateControlCapability,
    ) -> Result<Self, ChildProtocolError> {
        let all_binding_values = [
            authority_digest.as_bytes(),
            ticket_digest.as_bytes(),
            run_digest.as_bytes(),
            policy_digest.as_bytes(),
            global_capability_set_digest.as_bytes(),
            role_capability_set.semantic_digest(),
            role_capability_set.raw_handle_list_digest(),
            private_control_capability.encoded_field(),
        ];
        if has_duplicate_digest_refs(&all_binding_values) {
            return Err(ChildProtocolError::new(
                "child_bootstrap_binding_value_reused",
            ));
        }
        Ok(Self {
            authority_digest,
            ticket_digest,
            run_digest,
            policy_digest,
            global_capability_set_digest,
            role_capability_set,
            private_control_capability,
        })
    }

    #[allow(clippy::too_many_arguments)]
    fn new(
        authority_digest: AuthorityBindingDigest,
        ticket_digest: TicketBindingDigest,
        run_digest: RunBindingDigest,
        policy_digest: PolicyBindingDigest,
        global_capability_set_digest: GlobalCapabilitySetDigest,
        role_capability_set: RoleCapabilitySetBinding,
        private_control_capability: PrivateControlCapability,
    ) -> Result<Self, ChildProtocolError> {
        Self::prepare_for_parent(
            authority_digest,
            ticket_digest,
            run_digest,
            policy_digest,
            global_capability_set_digest,
            role_capability_set,
            private_control_capability,
        )
    }

    pub fn authority_digest(&self) -> &AuthorityBindingDigest {
        &self.authority_digest
    }

    pub fn ticket_digest(&self) -> &TicketBindingDigest {
        &self.ticket_digest
    }

    pub fn run_digest(&self) -> &RunBindingDigest {
        &self.run_digest
    }

    pub fn policy_digest(&self) -> &PolicyBindingDigest {
        &self.policy_digest
    }

    pub fn global_capability_set_digest(&self) -> &GlobalCapabilitySetDigest {
        &self.global_capability_set_digest
    }

    pub fn role_capability_set(&self) -> &RoleCapabilitySetBinding {
        &self.role_capability_set
    }

    #[cfg(test)]
    fn private_control_capability_for_test(&self) -> &PrivateControlCapability {
        &self.private_control_capability
    }

    fn ordered_fields(
        &self,
    ) -> [(ChildBootstrapFieldSemantic, &BootstrapDigest); CHILD_BOOTSTRAP_FIELD_COUNT] {
        [
            (
                ChildBootstrapFieldSemantic::AuthorityDigest,
                self.authority_digest.as_bytes(),
            ),
            (
                ChildBootstrapFieldSemantic::TicketDigest,
                self.ticket_digest.as_bytes(),
            ),
            (
                ChildBootstrapFieldSemantic::RunDigest,
                self.run_digest.as_bytes(),
            ),
            (
                ChildBootstrapFieldSemantic::PolicyDigest,
                self.policy_digest.as_bytes(),
            ),
            (
                ChildBootstrapFieldSemantic::GlobalCapabilitySetDigest,
                self.global_capability_set_digest.as_bytes(),
            ),
            (
                ChildBootstrapFieldSemantic::RoleInheritedCapabilitySetDigest,
                self.role_capability_set.semantic_digest(),
            ),
            (
                ChildBootstrapFieldSemantic::RawHandleListDigest,
                self.role_capability_set.raw_handle_list_digest(),
            ),
            (
                ChildBootstrapFieldSemantic::PrivateControlCapability,
                self.private_control_capability.encoded_field(),
            ),
        ]
    }
}

#[allow(clippy::too_many_arguments)]
fn derive_parent_protocol_field_projection_digest(
    role: ChildBootstrapRole,
    authority_digest: &AuthorityBindingDigest,
    ticket_digest: &TicketBindingDigest,
    run_digest: &RunBindingDigest,
    policy_digest: &PolicyBindingDigest,
    global_capability_set_digest: &GlobalCapabilitySetDigest,
    role_capability_set: &RoleCapabilitySetBinding,
    private_control_capability_commitment: &PrivateControlCapabilityCommitment,
) -> Result<ParentProtocolFieldProjectionDigest, ChildProtocolError> {
    let mut hasher = Sha256::new();
    hasher.update(PARENT_PROTOCOL_FIELD_PROJECTION_DOMAIN);
    hasher.update([role.wire_value()]);
    for value in [
        authority_digest.as_bytes(),
        ticket_digest.as_bytes(),
        run_digest.as_bytes(),
        policy_digest.as_bytes(),
        global_capability_set_digest.as_bytes(),
        role_capability_set.semantic_digest(),
        role_capability_set.raw_handle_list_digest(),
        private_control_capability_commitment.encoded_field(),
    ] {
        hasher.update(value);
    }
    let value = hasher.finalize().into();
    if is_zero_digest(&value) {
        return Err(ChildProtocolError::new(
            "child_parent_protocol_field_projection_invalid",
        ));
    }
    Ok(ParentProtocolFieldProjectionDigest(value))
}

/// Authority-side context measured independently from the child bootstrap
/// frame. Each aggregate has its own derivation domain so a value cannot be
/// substituted across lifecycle, transport, process, or identity meanings.
pub(crate) struct AuthorityChildExecutionContext {
    final_generation: FinalGenerationContextDigest,
    child_transport_contract: ChildTransportContractContextDigest,
    start_contract: StartContractContextDigest,
    job_membership_epoch: JobMembershipEpochContextDigest,
    runner_token: RunnerTokenContextDigest,
    child_image: ChildImageContextDigest,
    minimal_environment: MinimalEnvironmentContextDigest,
    control_server_identity: ControlServerIdentityContextDigest,
}

impl AuthorityChildExecutionContext {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn from_independent_measurements(
        final_generation: FinalGenerationContextDigest,
        child_transport_contract: ChildTransportContractContextDigest,
        start_contract: StartContractContextDigest,
        job_membership_epoch: JobMembershipEpochContextDigest,
        runner_token: RunnerTokenContextDigest,
        child_image: ChildImageContextDigest,
        minimal_environment: MinimalEnvironmentContextDigest,
        control_server_identity: ControlServerIdentityContextDigest,
    ) -> Result<Self, ChildProtocolError> {
        let values = [
            final_generation.as_bytes(),
            child_transport_contract.as_bytes(),
            start_contract.as_bytes(),
            job_membership_epoch.as_bytes(),
            runner_token.as_bytes(),
            child_image.as_bytes(),
            minimal_environment.as_bytes(),
            control_server_identity.as_bytes(),
        ];
        if has_duplicate_digest_refs(&values) {
            return Err(ChildProtocolError::new(
                "child_authority_execution_context_reused",
            ));
        }
        Ok(Self {
            final_generation,
            child_transport_contract,
            start_contract,
            job_membership_epoch,
            runner_token,
            child_image,
            minimal_environment,
            control_server_identity,
        })
    }

    fn ordered_fields(&self) -> [&BootstrapDigest; 8] {
        [
            self.final_generation.as_bytes(),
            self.child_transport_contract.as_bytes(),
            self.start_contract.as_bytes(),
            self.job_membership_epoch.as_bytes(),
            self.runner_token.as_bytes(),
            self.child_image.as_bytes(),
            self.minimal_environment.as_bytes(),
            self.control_server_identity.as_bytes(),
        ]
    }

    pub(crate) fn binding_digest(
        &self,
    ) -> Result<AuthorityChildExecutionContextBindingDigest, ChildProtocolError> {
        let mut hasher = Sha256::new();
        hasher.update(AUTHORITY_EXECUTION_CONTEXT_BINDING_DOMAIN);
        for value in self.ordered_fields() {
            hasher.update(value);
        }
        let value = hasher.finalize().into();
        if is_zero_digest(&value) {
            return Err(ChildProtocolError::new(
                "child_authority_execution_context_binding_invalid",
            ));
        }
        Ok(AuthorityChildExecutionContextBindingDigest(value))
    }

    fn clone_for_state(&self) -> Self {
        Self {
            final_generation: self.final_generation,
            child_transport_contract: self.child_transport_contract,
            start_contract: self.start_contract,
            job_membership_epoch: self.job_membership_epoch,
            runner_token: self.runner_token,
            child_image: self.child_image,
            minimal_environment: self.minimal_environment,
            control_server_identity: self.control_server_identity,
        }
    }
}

impl fmt::Debug for AuthorityChildExecutionContext {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("AuthorityChildExecutionContext(<redacted>)")
    }
}

/// Authority-side typed projection used to prepare the independently delivered
/// expectation envelope. This type never parses child-provided bytes.
pub(crate) struct ParentChildBootstrapExpectations {
    role: ChildBootstrapRole,
    authority_digest: AuthorityBindingDigest,
    ticket_digest: TicketBindingDigest,
    run_digest: RunBindingDigest,
    policy_digest: PolicyBindingDigest,
    global_capability_set_digest: GlobalCapabilitySetDigest,
    role_capability_set: RoleCapabilitySetBinding,
    private_control_capability_commitment: PrivateControlCapabilityCommitment,
    expected_child_observation_context: ChildObservedLaunchContextDigest,
    execution_context: AuthorityChildExecutionContext,
    protocol_field_projection_digest: ParentProtocolFieldProjectionDigest,
    execution_context_binding_digest: AuthorityChildExecutionContextBindingDigest,
}

impl ParentChildBootstrapExpectations {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn from_authority_projection(
        role: ChildBootstrapRole,
        authority_digest: AuthorityBindingDigest,
        ticket_digest: TicketBindingDigest,
        run_digest: RunBindingDigest,
        policy_digest: PolicyBindingDigest,
        global_capability_set_digest: GlobalCapabilitySetDigest,
        role_capability_set: RoleCapabilitySetBinding,
        private_control_capability_commitment: PrivateControlCapabilityCommitment,
        expected_child_observation_context: ChildObservedLaunchContextDigest,
        execution_context: AuthorityChildExecutionContext,
    ) -> Result<Self, ChildProtocolError> {
        validate_expectation_fields(
            role,
            &authority_digest,
            &ticket_digest,
            &run_digest,
            &policy_digest,
            &global_capability_set_digest,
            &role_capability_set,
            &private_control_capability_commitment,
        )?;
        validate_execution_context_is_distinct(
            &execution_context,
            &[
                authority_digest.as_bytes(),
                ticket_digest.as_bytes(),
                run_digest.as_bytes(),
                policy_digest.as_bytes(),
                global_capability_set_digest.as_bytes(),
                role_capability_set.semantic_digest(),
                role_capability_set.raw_handle_list_digest(),
                private_control_capability_commitment.encoded_field(),
                expected_child_observation_context.as_bytes(),
            ],
        )?;
        if [
            authority_digest.as_bytes(),
            ticket_digest.as_bytes(),
            run_digest.as_bytes(),
            policy_digest.as_bytes(),
            global_capability_set_digest.as_bytes(),
            role_capability_set.semantic_digest(),
            role_capability_set.raw_handle_list_digest(),
            private_control_capability_commitment.encoded_field(),
        ]
        .iter()
        .any(|value| digests_equal(value, expected_child_observation_context.as_bytes()))
        {
            return Err(ChildProtocolError::new(
                "child_parent_observation_context_reused",
            ));
        }
        let protocol_field_projection_digest = derive_parent_protocol_field_projection_digest(
            role,
            &authority_digest,
            &ticket_digest,
            &run_digest,
            &policy_digest,
            &global_capability_set_digest,
            &role_capability_set,
            &private_control_capability_commitment,
        )?;
        let execution_context_binding_digest = execution_context.binding_digest()?;
        Ok(Self {
            role,
            authority_digest,
            ticket_digest,
            run_digest,
            policy_digest,
            global_capability_set_digest,
            role_capability_set,
            private_control_capability_commitment,
            expected_child_observation_context,
            execution_context,
            protocol_field_projection_digest,
            execution_context_binding_digest,
        })
    }

    pub(crate) const fn role(&self) -> ChildBootstrapRole {
        self.role
    }

    pub(crate) fn protocol_field_projection_digest(&self) -> &ParentProtocolFieldProjectionDigest {
        &self.protocol_field_projection_digest
    }

    pub(crate) fn execution_context_binding_digest(
        &self,
    ) -> &AuthorityChildExecutionContextBindingDigest {
        &self.execution_context_binding_digest
    }

    pub(crate) fn role_raw_handle_list_digest(&self) -> &BootstrapDigest {
        self.role_capability_set.raw_handle_list_digest()
    }

    pub(crate) const fn expected_child_observation_context(
        &self,
    ) -> ChildObservedLaunchContextDigest {
        self.expected_child_observation_context
    }
}

impl fmt::Debug for ParentChildBootstrapExpectations {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ParentChildBootstrapExpectations")
            .field("role", &self.role)
            .field("bindings", &"<redacted>")
            .finish()
    }
}

/// Independently obtained expectations required before a received frame can be
/// called validated. This prevents a self-consistent but mislabelled frame from
/// defining its own authority, ticket, run, policy, or inherited capabilities.
pub struct ChildBootstrapExpectations {
    role: ChildBootstrapRole,
    authority_digest: AuthorityBindingDigest,
    ticket_digest: TicketBindingDigest,
    run_digest: RunBindingDigest,
    policy_digest: PolicyBindingDigest,
    global_capability_set_digest: GlobalCapabilitySetDigest,
    role_capability_set: RoleCapabilitySetBinding,
    private_control_capability_commitment: PrivateControlCapabilityCommitment,
}

impl ChildBootstrapExpectations {
    #[cfg(test)]
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new_for_test(
        role: ChildBootstrapRole,
        authority_digest: AuthorityBindingDigest,
        ticket_digest: TicketBindingDigest,
        run_digest: RunBindingDigest,
        policy_digest: PolicyBindingDigest,
        global_capability_set_digest: GlobalCapabilitySetDigest,
        role_capability_set: RoleCapabilitySetBinding,
        private_control_capability_commitment: PrivateControlCapabilityCommitment,
    ) -> Result<Self, ChildProtocolError> {
        validate_expectation_fields(
            role,
            &authority_digest,
            &ticket_digest,
            &run_digest,
            &policy_digest,
            &global_capability_set_digest,
            &role_capability_set,
            &private_control_capability_commitment,
        )?;
        Ok(Self {
            role,
            authority_digest,
            ticket_digest,
            run_digest,
            policy_digest,
            global_capability_set_digest,
            role_capability_set,
            private_control_capability_commitment,
        })
    }

    #[allow(clippy::too_many_arguments)]
    fn from_authenticated_envelope(
        role: ChildBootstrapRole,
        authority_digest: AuthorityBindingDigest,
        ticket_digest: TicketBindingDigest,
        run_digest: RunBindingDigest,
        policy_digest: PolicyBindingDigest,
        global_capability_set_digest: GlobalCapabilitySetDigest,
        role_capability_set: RoleCapabilitySetBinding,
        private_control_capability_commitment: PrivateControlCapabilityCommitment,
    ) -> Result<Self, ChildProtocolError> {
        validate_expectation_fields(
            role,
            &authority_digest,
            &ticket_digest,
            &run_digest,
            &policy_digest,
            &global_capability_set_digest,
            &role_capability_set,
            &private_control_capability_commitment,
        )?;
        Ok(Self {
            role,
            authority_digest,
            ticket_digest,
            run_digest,
            policy_digest,
            global_capability_set_digest,
            role_capability_set,
            private_control_capability_commitment,
        })
    }
}

#[allow(clippy::too_many_arguments)]
fn validate_expectation_fields(
    role: ChildBootstrapRole,
    authority_digest: &AuthorityBindingDigest,
    ticket_digest: &TicketBindingDigest,
    run_digest: &RunBindingDigest,
    policy_digest: &PolicyBindingDigest,
    global_capability_set_digest: &GlobalCapabilitySetDigest,
    role_capability_set: &RoleCapabilitySetBinding,
    private_control_capability_commitment: &PrivateControlCapabilityCommitment,
) -> Result<(), ChildProtocolError> {
    if role_capability_set.role() != role {
        return Err(ChildProtocolError::new(
            "child_bootstrap_role_capability_set_mismatch",
        ));
    }
    let values = [
        authority_digest.as_bytes(),
        ticket_digest.as_bytes(),
        run_digest.as_bytes(),
        policy_digest.as_bytes(),
        global_capability_set_digest.as_bytes(),
        role_capability_set.semantic_digest(),
        role_capability_set.raw_handle_list_digest(),
        private_control_capability_commitment.encoded_field(),
    ];
    if has_duplicate_digest_refs(&values) {
        return Err(ChildProtocolError::new(
            "child_bootstrap_binding_value_reused",
        ));
    }
    Ok(())
}

fn validate_execution_context_is_distinct(
    context: &AuthorityChildExecutionContext,
    expectation_values: &[&BootstrapDigest],
) -> Result<(), ChildProtocolError> {
    let context_values = context.ordered_fields();
    if context_values.iter().any(|context_value| {
        expectation_values
            .iter()
            .any(|expectation_value| digests_equal(context_value, expectation_value))
    }) {
        return Err(ChildProtocolError::new(
            "child_authority_execution_context_reused",
        ));
    }
    Ok(())
}

fn derive_peer_authentication_challenge(
    role: ChildBootstrapRole,
    fields: &[&BootstrapDigest; 22],
) -> Result<BootstrapDigest, ChildProtocolError> {
    if fields.iter().any(|value| is_zero_digest(value)) || has_duplicate_digest_refs(fields) {
        return Err(ChildProtocolError::new(
            "child_expectation_peer_challenge_invalid",
        ));
    }
    let mut hasher = Sha256::new();
    hasher.update(CHILD_EXPECTATION_PEER_CHALLENGE_DOMAIN);
    hasher.update([role.wire_value()]);
    hasher.update((fields.len() as u16).to_be_bytes());
    for (index, value) in fields.iter().enumerate() {
        hasher.update((index as u16).to_be_bytes());
        hasher.update(value);
    }
    let digest: BootstrapDigest = hasher.finalize().into();
    if is_zero_digest(&digest) {
        return Err(ChildProtocolError::new(
            "child_expectation_peer_challenge_invalid",
        ));
    }
    Ok(digest)
}

impl fmt::Debug for ChildBootstrapExpectations {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ChildBootstrapExpectations")
            .field("role", &self.role)
            .field("bindings", &"<redacted>")
            .finish()
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) struct ChildHandshakeNonce(BootstrapDigest);

impl ChildHandshakeNonce {
    pub(crate) fn from_fresh_bytes(value: BootstrapDigest) -> Result<Self, ChildProtocolError> {
        if is_zero_digest(&value) {
            return Err(ChildProtocolError::new("child_ready_nonce_invalid"));
        }
        Ok(Self(value))
    }

    fn as_bytes(&self) -> &BootstrapDigest {
        &self.0
    }
}

impl fmt::Debug for ChildHandshakeNonce {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ChildHandshakeNonce(<redacted>)")
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) struct AuthorityHandshakeNonce(BootstrapDigest);

impl AuthorityHandshakeNonce {
    pub(crate) fn from_fresh_bytes(value: BootstrapDigest) -> Result<Self, ChildProtocolError> {
        if is_zero_digest(&value) {
            return Err(ChildProtocolError::new(
                "child_expectation_authority_nonce_invalid",
            ));
        }
        Ok(Self(value))
    }

    fn as_bytes(&self) -> &BootstrapDigest {
        &self.0
    }
}

impl fmt::Debug for AuthorityHandshakeNonce {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("AuthorityHandshakeNonce(<redacted>)")
    }
}

/// Child-originated first message. Its digest fields describe the role-local
/// startup objects that were independently measured before the child runs.
pub(crate) struct PreparedChildReady {
    role: ChildBootstrapRole,
    child_nonce: ChildHandshakeNonce,
    role_semantic_digest: BootstrapDigest,
    raw_handle_list_digest: RoleRawHandleListDigest,
    child_observation_context_digest: ChildObservedLaunchContextDigest,
    ready_binding_digest: BootstrapDigest,
    wire: SensitiveBytes<CHILD_READY_MESSAGE_LEN>,
}

impl PreparedChildReady {
    pub(crate) fn prepare(
        role: ChildBootstrapRole,
        child_nonce: ChildHandshakeNonce,
        role_capability_set: &RoleCapabilitySetBinding,
        raw_handle_list_digest: RoleRawHandleListDigest,
        child_observation_context_digest: ChildObservedLaunchContextDigest,
    ) -> Result<Self, ChildProtocolError> {
        if role_capability_set.role() != role
            || raw_handle_list_digest.role() != role
            || !digests_equal(
                role_capability_set.raw_handle_list_digest(),
                raw_handle_list_digest.as_bytes(),
            )
            || is_zero_digest(role_capability_set.semantic_digest())
            || digests_equal(
                role_capability_set.semantic_digest(),
                child_observation_context_digest.as_bytes(),
            )
            || digests_equal(
                raw_handle_list_digest.as_bytes(),
                child_observation_context_digest.as_bytes(),
            )
        {
            return Err(ChildProtocolError::new("child_ready_role_binding_invalid"));
        }
        let mut wire = SensitiveBytes::<CHILD_READY_MESSAGE_LEN>::zeroed();
        write_control_header(
            wire.as_mut_slice(),
            &CHILD_READY_MAGIC,
            CHILD_READY_VERSION,
            role,
            CHILD_READY_MESSAGE_LEN,
        );
        let mut offset = CHILD_CONTROL_MESSAGE_HEADER_LEN;
        put_digest(wire.as_mut_slice(), &mut offset, child_nonce.as_bytes());
        put_digest(
            wire.as_mut_slice(),
            &mut offset,
            role_capability_set.semantic_digest(),
        );
        put_digest(
            wire.as_mut_slice(),
            &mut offset,
            raw_handle_list_digest.as_bytes(),
        );
        put_digest(
            wire.as_mut_slice(),
            &mut offset,
            child_observation_context_digest.as_bytes(),
        );
        debug_assert_eq!(offset, CHILD_READY_BINDING_OFFSET);
        let ready_binding_digest = domain_hash(
            CHILD_READY_BINDING_DOMAIN,
            &wire.as_slice()[..CHILD_READY_BINDING_OFFSET],
        );
        wire.as_mut_slice()[CHILD_READY_BINDING_OFFSET..].copy_from_slice(&ready_binding_digest);
        Ok(Self {
            role,
            child_nonce,
            role_semantic_digest: *role_capability_set.semantic_digest(),
            raw_handle_list_digest,
            child_observation_context_digest,
            ready_binding_digest,
            wire,
        })
    }

    pub(crate) fn write_to<W: Write>(
        self,
        writer: &mut W,
    ) -> Result<ChildAwaitingExpectation, ChildProtocolError> {
        write_all_retry_interrupted(writer, self.wire.as_slice(), "child_ready_write_failed")?;
        Ok(ChildAwaitingExpectation { ready: self })
    }

    #[cfg(test)]
    fn as_bytes_for_test(&self) -> &[u8] {
        self.wire.as_slice()
    }
}

impl fmt::Debug for PreparedChildReady {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PreparedChildReady")
            .field("role", &self.role)
            .field("message", &"<redacted>")
            .finish()
    }
}

impl Drop for PreparedChildReady {
    fn drop(&mut self) {
        volatile_zero(&mut self.role_semantic_digest);
        volatile_zero(&mut self.child_observation_context_digest.0);
        volatile_zero(&mut self.ready_binding_digest);
        volatile_zero(self.wire.as_mut_slice());
        #[cfg(test)]
        record_zeroized_drop(READY_MESSAGE_DROP_BIT, self.wire.as_slice());
    }
}

/// Parent-side structural parse of a ready message. It is not authority proof.
pub(crate) struct ReceivedChildReady {
    role: ChildBootstrapRole,
    child_nonce: ChildHandshakeNonce,
    role_semantic_digest: BootstrapDigest,
    raw_handle_list_digest: RoleRawHandleListDigest,
    child_observation_context_digest: ChildObservedLaunchContextDigest,
    ready_binding_digest: BootstrapDigest,
    wire: SensitiveBytes<CHILD_READY_MESSAGE_LEN>,
}

impl ReceivedChildReady {
    pub(crate) fn read_from<R: Read>(reader: &mut R) -> Result<Self, ChildProtocolError> {
        let wire = read_fixed_message::<R, CHILD_READY_MESSAGE_LEN>(
            reader,
            "child_ready_read_failed",
            "child_ready_length_invalid",
        )?;
        parse_child_ready(wire)
    }

    pub(crate) fn child_nonce(&self) -> ChildHandshakeNonce {
        self.child_nonce
    }

    pub(crate) fn challenge(&self) -> ReceivedChildReadyChallenge<'_> {
        ReceivedChildReadyChallenge { ready: self }
    }

    #[cfg(test)]
    fn as_bytes_for_test(&self) -> &[u8] {
        self.wire.as_slice()
    }
}

pub(crate) struct ReceivedChildReadyChallenge<'a> {
    ready: &'a ReceivedChildReady,
}

impl ReceivedChildReadyChallenge<'_> {
    pub(crate) fn role(&self) -> ChildBootstrapRole {
        self.ready.role
    }

    pub(crate) fn child_nonce(&self) -> ChildHandshakeNonce {
        self.ready.child_nonce
    }

    pub(crate) fn role_semantic_digest(&self) -> &BootstrapDigest {
        &self.ready.role_semantic_digest
    }

    pub(crate) fn raw_handle_list_digest(&self) -> &RoleRawHandleListDigest {
        &self.ready.raw_handle_list_digest
    }

    pub(crate) fn child_observation_context_digest(&self) -> &ChildObservedLaunchContextDigest {
        &self.ready.child_observation_context_digest
    }

    pub(crate) fn ready_binding_digest(&self) -> &BootstrapDigest {
        &self.ready.ready_binding_digest
    }
}

impl fmt::Debug for ReceivedChildReadyChallenge<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ReceivedChildReadyChallenge")
            .field("role", &self.ready.role)
            .field("bindings", &"<redacted>")
            .finish()
    }
}

impl fmt::Debug for ReceivedChildReady {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ReceivedChildReady")
            .field("role", &self.role)
            .field("message", &"<redacted>")
            .finish()
    }
}

impl Drop for ReceivedChildReady {
    fn drop(&mut self) {
        volatile_zero(&mut self.role_semantic_digest);
        volatile_zero(&mut self.child_observation_context_digest.0);
        volatile_zero(&mut self.ready_binding_digest);
        volatile_zero(self.wire.as_mut_slice());
    }
}

fn parse_child_ready(
    wire: SensitiveBytes<CHILD_READY_MESSAGE_LEN>,
) -> Result<ReceivedChildReady, ChildProtocolError> {
    let role = validate_control_header(
        wire.as_slice(),
        &CHILD_READY_MAGIC,
        CHILD_READY_VERSION,
        CHILD_READY_MESSAGE_LEN,
        "child_ready",
    )?;
    let mut offset = CHILD_CONTROL_MESSAGE_HEADER_LEN;
    let child_nonce =
        ChildHandshakeNonce::from_fresh_bytes(take_digest(wire.as_slice(), &mut offset))?;
    let role_semantic_digest = take_digest(wire.as_slice(), &mut offset);
    let raw_handle_list_digest =
        RoleRawHandleListDigest::from_wire(role, take_digest(wire.as_slice(), &mut offset))?;
    let child_observation_context_digest =
        ChildObservedLaunchContextDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?;
    if is_zero_digest(&role_semantic_digest)
        || digests_equal(&role_semantic_digest, raw_handle_list_digest.as_bytes())
        || digests_equal(
            &role_semantic_digest,
            child_observation_context_digest.as_bytes(),
        )
        || digests_equal(
            raw_handle_list_digest.as_bytes(),
            child_observation_context_digest.as_bytes(),
        )
    {
        return Err(ChildProtocolError::new("child_ready_role_binding_invalid"));
    }
    if offset != CHILD_READY_BINDING_OFFSET {
        return Err(ChildProtocolError::new("child_ready_length_invalid"));
    }
    let mut ready_binding_digest = [0u8; CHILD_BOOTSTRAP_DIGEST_LEN];
    ready_binding_digest.copy_from_slice(&wire.as_slice()[CHILD_READY_BINDING_OFFSET..]);
    let expected = domain_hash(
        CHILD_READY_BINDING_DOMAIN,
        &wire.as_slice()[..CHILD_READY_BINDING_OFFSET],
    );
    if is_zero_digest(&ready_binding_digest) || !digests_equal(&ready_binding_digest, &expected) {
        return Err(ChildProtocolError::new("child_ready_binding_invalid"));
    }
    Ok(ReceivedChildReady {
        role,
        child_nonce,
        role_semantic_digest,
        raw_handle_list_digest,
        child_observation_context_digest,
        ready_binding_digest,
        wire,
    })
}

/// Authority-owned second message. The envelope binding is structural; only
/// an independently authenticated peer witness may promote its contents into
/// child-side expectations.
pub(crate) struct PreparedExpectationEnvelope {
    role: ChildBootstrapRole,
    transcript: ChildHandshakeTranscript,
    wire: SensitiveBytes<CHILD_EXPECTATION_ENVELOPE_LEN>,
}

impl PreparedExpectationEnvelope {
    pub(crate) fn prepare(
        ready: &ReceivedChildReady,
        authority_nonce: AuthorityHandshakeNonce,
        frame_binding_digest: &BootstrapDigest,
        expectations: &ParentChildBootstrapExpectations,
    ) -> Result<Self, ChildProtocolError> {
        if expectations.role != ready.role
            || expectations.role_capability_set.role() != ready.role
            || !digests_equal(
                expectations.role_capability_set.semantic_digest(),
                &ready.role_semantic_digest,
            )
            || !digests_equal(
                expectations.role_capability_set.raw_handle_list_digest(),
                ready.raw_handle_list_digest.as_bytes(),
            )
            || !digests_equal(
                expectations.expected_child_observation_context.as_bytes(),
                ready.child_observation_context_digest.as_bytes(),
            )
            || digests_equal(ready.child_nonce.as_bytes(), authority_nonce.as_bytes())
            || is_zero_digest(frame_binding_digest)
        {
            return Err(ChildProtocolError::new(
                "child_expectation_ready_binding_invalid",
            ));
        }
        let mut wire = SensitiveBytes::<CHILD_EXPECTATION_ENVELOPE_LEN>::zeroed();
        write_control_header(
            wire.as_mut_slice(),
            &CHILD_EXPECTATION_MAGIC,
            CHILD_EXPECTATION_VERSION,
            ready.role,
            CHILD_EXPECTATION_ENVELOPE_LEN,
        );
        let mut offset = CHILD_CONTROL_MESSAGE_HEADER_LEN;
        for value in [
            &ready.ready_binding_digest,
            ready.child_nonce.as_bytes(),
            authority_nonce.as_bytes(),
            frame_binding_digest,
            expectations.expected_child_observation_context.as_bytes(),
            expectations.execution_context.final_generation.as_bytes(),
            expectations
                .execution_context
                .child_transport_contract
                .as_bytes(),
            expectations.execution_context.start_contract.as_bytes(),
            expectations
                .execution_context
                .job_membership_epoch
                .as_bytes(),
            expectations.execution_context.runner_token.as_bytes(),
            expectations.execution_context.child_image.as_bytes(),
            expectations
                .execution_context
                .minimal_environment
                .as_bytes(),
            expectations
                .execution_context
                .control_server_identity
                .as_bytes(),
            expectations.authority_digest.as_bytes(),
            expectations.ticket_digest.as_bytes(),
            expectations.run_digest.as_bytes(),
            expectations.policy_digest.as_bytes(),
            expectations.global_capability_set_digest.as_bytes(),
            expectations.role_capability_set.semantic_digest(),
            expectations.role_capability_set.raw_handle_list_digest(),
            expectations
                .private_control_capability_commitment
                .encoded_field(),
        ] {
            put_digest(wire.as_mut_slice(), &mut offset, value);
        }
        debug_assert_eq!(offset, CHILD_EXPECTATION_BINDING_OFFSET);
        let envelope_binding_digest = domain_hash(
            CHILD_EXPECTATION_BINDING_DOMAIN,
            &wire.as_slice()[..CHILD_EXPECTATION_BINDING_OFFSET],
        );
        wire.as_mut_slice()[CHILD_EXPECTATION_BINDING_OFFSET..]
            .copy_from_slice(&envelope_binding_digest);
        let transcript = ChildHandshakeTranscript::derive(
            ready.role,
            ready.child_nonce,
            authority_nonce,
            &ready.ready_binding_digest,
            &envelope_binding_digest,
            frame_binding_digest,
            &ready.child_observation_context_digest,
            &expectations.execution_context,
            expectations.role_capability_set.semantic_digest(),
            expectations.role_capability_set.raw_handle_list_digest(),
            ready.wire.as_slice(),
            wire.as_slice(),
        )?;
        Ok(Self {
            role: ready.role,
            transcript,
            wire,
        })
    }

    pub(crate) fn write_to<W: Write>(
        self,
        writer: &mut W,
    ) -> Result<ParentExpectationSent, ChildProtocolError> {
        write_all_retry_interrupted(
            writer,
            self.wire.as_slice(),
            "child_expectation_write_failed",
        )?;
        Ok(ParentExpectationSent {
            transcript: self.transcript.clone_for_state(),
        })
    }

    #[cfg(test)]
    fn as_bytes_for_test(&self) -> &[u8] {
        self.wire.as_slice()
    }
}

impl fmt::Debug for PreparedExpectationEnvelope {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PreparedExpectationEnvelope")
            .field("role", &self.role)
            .field("message", &"<redacted>")
            .finish()
    }
}

impl Drop for PreparedExpectationEnvelope {
    fn drop(&mut self) {
        volatile_zero(self.wire.as_mut_slice());
        #[cfg(test)]
        record_zeroized_drop(EXPECTATION_MESSAGE_DROP_BIT, self.wire.as_slice());
    }
}

/// Linear proof that the authority successfully wrote the exact envelope.
pub(crate) struct ParentExpectationSent {
    transcript: ChildHandshakeTranscript,
}

impl ParentExpectationSent {
    fn transcript(&self) -> &ChildHandshakeTranscript {
        &self.transcript
    }
}

impl fmt::Debug for ParentExpectationSent {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ParentExpectationSent(<redacted>)")
    }
}

pub(crate) struct ChildAwaitingExpectation {
    ready: PreparedChildReady,
}

impl ChildAwaitingExpectation {
    pub(crate) fn read_expectation_from<R: Read>(
        self,
        reader: &mut R,
    ) -> Result<PendingExpectationEnvelope, ChildProtocolError> {
        let wire = read_fixed_message::<R, CHILD_EXPECTATION_ENVELOPE_LEN>(
            reader,
            "child_expectation_read_failed",
            "child_expectation_length_invalid",
        )?;
        parse_expectation_envelope(self.ready, wire)
    }
}

impl fmt::Debug for ChildAwaitingExpectation {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ChildAwaitingExpectation")
            .field("role", &self.ready.role)
            .field("state", &"<redacted>")
            .finish()
    }
}

/// Structurally parsed envelope awaiting a peer-authentication witness. There
/// is intentionally no public or crate-visible witness constructor in this
/// pure protocol slice.
pub(crate) struct PendingExpectationEnvelope {
    ready: PreparedChildReady,
    authority_nonce: AuthorityHandshakeNonce,
    frame_binding_digest: BootstrapDigest,
    execution_context: AuthorityChildExecutionContext,
    authority_digest: AuthorityBindingDigest,
    ticket_digest: TicketBindingDigest,
    run_digest: RunBindingDigest,
    policy_digest: PolicyBindingDigest,
    global_capability_set_digest: GlobalCapabilitySetDigest,
    role_capability_set: RoleCapabilitySetBinding,
    private_control_capability_commitment: PrivateControlCapabilityCommitment,
    envelope_binding_digest: BootstrapDigest,
    peer_challenge_digest: BootstrapDigest,
    transcript: ChildHandshakeTranscript,
    wire: SensitiveBytes<CHILD_EXPECTATION_ENVELOPE_LEN>,
}

impl PendingExpectationEnvelope {
    pub(crate) fn authenticate(
        self,
        witness: AuthenticatedControlPeerWitness,
    ) -> Result<AuthenticatedExpectationEnvelope, ChildProtocolError> {
        if witness.role != self.ready.role
            || !digests_equal(&witness.challenge_digest, &self.peer_challenge_digest)
        {
            return Err(ChildProtocolError::new(
                "child_expectation_peer_witness_unexpected",
            ));
        }
        let expectations = ChildBootstrapExpectations::from_authenticated_envelope(
            self.ready.role,
            self.authority_digest,
            self.ticket_digest,
            self.run_digest,
            self.policy_digest,
            self.global_capability_set_digest,
            self.role_capability_set.clone(),
            self.private_control_capability_commitment,
        )?;
        Ok(AuthenticatedExpectationEnvelope {
            role: self.ready.role,
            expectations,
            child_observation_context_digest: self.ready.child_observation_context_digest,
            execution_context: self.execution_context.clone_for_state(),
            transcript: self.transcript.clone_for_state(),
        })
    }

    #[cfg(test)]
    fn peer_witness_for_test(&self) -> AuthenticatedControlPeerWitness {
        AuthenticatedControlPeerWitness {
            role: self.ready.role,
            challenge_digest: self.peer_challenge_digest,
            _private: (),
        }
    }

    #[cfg(test)]
    fn peer_challenge_fields_for_test(&self) -> [BootstrapDigest; 22] {
        [
            *self.ready.child_nonce.as_bytes(),
            *self.authority_nonce.as_bytes(),
            self.ready.ready_binding_digest,
            self.envelope_binding_digest,
            self.frame_binding_digest,
            *self.ready.child_observation_context_digest.as_bytes(),
            *self.execution_context.final_generation.as_bytes(),
            *self.execution_context.child_transport_contract.as_bytes(),
            *self.execution_context.start_contract.as_bytes(),
            *self.execution_context.job_membership_epoch.as_bytes(),
            *self.execution_context.runner_token.as_bytes(),
            *self.execution_context.child_image.as_bytes(),
            *self.execution_context.minimal_environment.as_bytes(),
            *self.execution_context.control_server_identity.as_bytes(),
            *self.authority_digest.as_bytes(),
            *self.ticket_digest.as_bytes(),
            *self.run_digest.as_bytes(),
            *self.policy_digest.as_bytes(),
            *self.global_capability_set_digest.as_bytes(),
            *self.role_capability_set.semantic_digest(),
            *self.role_capability_set.raw_handle_list_digest(),
            *self.private_control_capability_commitment.encoded_field(),
        ]
    }

    pub(crate) fn peer_authentication_challenge(&self) -> PendingExpectationPeerChallenge<'_> {
        PendingExpectationPeerChallenge { pending: self }
    }
}

pub(crate) struct PendingExpectationPeerChallenge<'a> {
    pending: &'a PendingExpectationEnvelope,
}

impl PendingExpectationPeerChallenge<'_> {
    pub(crate) fn role(&self) -> ChildBootstrapRole {
        self.pending.ready.role
    }

    pub(crate) fn child_nonce(&self) -> ChildHandshakeNonce {
        self.pending.ready.child_nonce
    }

    pub(crate) fn authority_nonce(&self) -> AuthorityHandshakeNonce {
        self.pending.authority_nonce
    }

    pub(crate) fn expected_child_observation_context(&self) -> &ChildObservedLaunchContextDigest {
        &self.pending.ready.child_observation_context_digest
    }

    pub(crate) fn frame_binding_digest(&self) -> &BootstrapDigest {
        &self.pending.frame_binding_digest
    }

    pub(crate) fn raw_handle_list_digest(&self) -> &RoleRawHandleListDigest {
        &self.pending.ready.raw_handle_list_digest
    }

    pub(crate) fn final_generation_context(&self) -> &FinalGenerationContextDigest {
        &self.pending.execution_context.final_generation
    }

    pub(crate) fn child_transport_contract_context(&self) -> &ChildTransportContractContextDigest {
        &self.pending.execution_context.child_transport_contract
    }

    pub(crate) fn start_contract_context(&self) -> &StartContractContextDigest {
        &self.pending.execution_context.start_contract
    }

    pub(crate) fn job_membership_epoch_context(&self) -> &JobMembershipEpochContextDigest {
        &self.pending.execution_context.job_membership_epoch
    }

    pub(crate) fn runner_token_context(&self) -> &RunnerTokenContextDigest {
        &self.pending.execution_context.runner_token
    }

    pub(crate) fn child_image_context(&self) -> &ChildImageContextDigest {
        &self.pending.execution_context.child_image
    }

    pub(crate) fn minimal_environment_context(&self) -> &MinimalEnvironmentContextDigest {
        &self.pending.execution_context.minimal_environment
    }

    pub(crate) fn control_server_identity_context(&self) -> &ControlServerIdentityContextDigest {
        &self.pending.execution_context.control_server_identity
    }

    pub(crate) fn challenge_digest(&self) -> &BootstrapDigest {
        &self.pending.peer_challenge_digest
    }
}

impl fmt::Debug for PendingExpectationPeerChallenge<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PendingExpectationPeerChallenge")
            .field("role", &self.pending.ready.role)
            .field("bindings", &"<redacted>")
            .finish()
    }
}

impl fmt::Debug for PendingExpectationEnvelope {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PendingExpectationEnvelope")
            .field("role", &self.ready.role)
            .field("state", &"unauthenticated")
            .field("message", &"<redacted>")
            .finish()
    }
}

impl Drop for PendingExpectationEnvelope {
    fn drop(&mut self) {
        volatile_zero(&mut self.frame_binding_digest);
        volatile_zero(&mut self.envelope_binding_digest);
        volatile_zero(&mut self.peer_challenge_digest);
        volatile_zero(self.wire.as_mut_slice());
        #[cfg(test)]
        record_zeroized_drop(EXPECTATION_MESSAGE_DROP_BIT, self.wire.as_slice());
    }
}

pub(crate) struct AuthenticatedControlPeerWitness {
    role: ChildBootstrapRole,
    challenge_digest: BootstrapDigest,
    _private: (),
}

impl fmt::Debug for AuthenticatedControlPeerWitness {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("AuthenticatedControlPeerWitness(<redacted>)")
    }
}

pub(crate) struct AuthenticatedExpectationEnvelope {
    role: ChildBootstrapRole,
    expectations: ChildBootstrapExpectations,
    child_observation_context_digest: ChildObservedLaunchContextDigest,
    execution_context: AuthorityChildExecutionContext,
    transcript: ChildHandshakeTranscript,
}

impl AuthenticatedExpectationEnvelope {
    pub(crate) fn read_and_validate_bootstrap<R: Read>(
        self,
        reader: &mut R,
    ) -> Result<ChildValidatedHandshake, ChildProtocolError> {
        let transport = ChildBootstrapTransportFrame::read_complete_from(
            ChildBootstrapTransportSlot::fixed(),
            reader,
        )?;
        let validated = transport
            .begin_validation(self.role)?
            .validate(&self.expectations)?;
        if !digests_equal(
            validated.frame_binding_digest(),
            &self.transcript.frame_binding_digest,
        ) {
            return Err(ChildProtocolError::new(
                "child_expectation_frame_binding_unexpected",
            ));
        }
        Ok(ChildValidatedHandshake {
            validated,
            child_observation_context_digest: self.child_observation_context_digest,
            execution_context: self.execution_context,
            transcript: self.transcript,
        })
    }
}

impl fmt::Debug for AuthenticatedExpectationEnvelope {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AuthenticatedExpectationEnvelope")
            .field("role", &self.role)
            .field("state", &"authenticated")
            .finish()
    }
}

fn parse_expectation_envelope(
    ready: PreparedChildReady,
    wire: SensitiveBytes<CHILD_EXPECTATION_ENVELOPE_LEN>,
) -> Result<PendingExpectationEnvelope, ChildProtocolError> {
    let role = validate_control_header(
        wire.as_slice(),
        &CHILD_EXPECTATION_MAGIC,
        CHILD_EXPECTATION_VERSION,
        CHILD_EXPECTATION_ENVELOPE_LEN,
        "child_expectation",
    )?;
    if role != ready.role {
        return Err(ChildProtocolError::new("child_expectation_role_unexpected"));
    }
    let mut offset = CHILD_CONTROL_MESSAGE_HEADER_LEN;
    let ready_binding_digest = take_digest(wire.as_slice(), &mut offset);
    let child_nonce =
        ChildHandshakeNonce::from_fresh_bytes(take_digest(wire.as_slice(), &mut offset))?;
    let authority_nonce =
        AuthorityHandshakeNonce::from_fresh_bytes(take_digest(wire.as_slice(), &mut offset))?;
    let frame_binding_digest = take_digest(wire.as_slice(), &mut offset);
    let expected_child_observation_context =
        ChildObservedLaunchContextDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?;
    let execution_context = AuthorityChildExecutionContext::from_independent_measurements(
        FinalGenerationContextDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?,
        ChildTransportContractContextDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?,
        StartContractContextDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?,
        JobMembershipEpochContextDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?,
        RunnerTokenContextDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?,
        ChildImageContextDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?,
        MinimalEnvironmentContextDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?,
        ControlServerIdentityContextDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?,
    )?;
    let authority_digest =
        AuthorityBindingDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?;
    let ticket_digest = TicketBindingDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?;
    let run_digest = RunBindingDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?;
    let policy_digest = PolicyBindingDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?;
    let global_capability_set_digest =
        GlobalCapabilitySetDigest::from_wire(take_digest(wire.as_slice(), &mut offset))?;
    let role_semantic_digest = take_digest(wire.as_slice(), &mut offset);
    let role_raw_handle_list_digest =
        RoleRawHandleListDigest::from_wire(role, take_digest(wire.as_slice(), &mut offset))?;
    let private_control_capability_commitment =
        PrivateControlCapabilityCommitment::from_wire(take_digest(wire.as_slice(), &mut offset))?;
    if offset != CHILD_EXPECTATION_BINDING_OFFSET {
        return Err(ChildProtocolError::new("child_expectation_length_invalid"));
    }
    let mut envelope_binding_digest = [0u8; CHILD_BOOTSTRAP_DIGEST_LEN];
    envelope_binding_digest.copy_from_slice(&wire.as_slice()[CHILD_EXPECTATION_BINDING_OFFSET..]);
    let expected_envelope_binding = domain_hash(
        CHILD_EXPECTATION_BINDING_DOMAIN,
        &wire.as_slice()[..CHILD_EXPECTATION_BINDING_OFFSET],
    );
    if is_zero_digest(&envelope_binding_digest)
        || !digests_equal(&envelope_binding_digest, &expected_envelope_binding)
    {
        return Err(ChildProtocolError::new("child_expectation_binding_invalid"));
    }
    if !digests_equal(&ready_binding_digest, &ready.ready_binding_digest)
        || child_nonce != ready.child_nonce
        || child_nonce.as_bytes() == authority_nonce.as_bytes()
        || is_zero_digest(&frame_binding_digest)
        || expected_child_observation_context != ready.child_observation_context_digest
        || !digests_equal(&role_semantic_digest, &ready.role_semantic_digest)
        || !digests_equal(
            role_raw_handle_list_digest.as_bytes(),
            ready.raw_handle_list_digest.as_bytes(),
        )
    {
        return Err(ChildProtocolError::new(
            "child_expectation_ready_binding_invalid",
        ));
    }
    let role_capability_set = RoleCapabilitySetBinding::from_authenticated_expectation(
        role,
        role_semantic_digest,
        role_raw_handle_list_digest,
    )?;
    validate_expectation_fields(
        role,
        &authority_digest,
        &ticket_digest,
        &run_digest,
        &policy_digest,
        &global_capability_set_digest,
        &role_capability_set,
        &private_control_capability_commitment,
    )?;
    validate_execution_context_is_distinct(
        &execution_context,
        &[
            authority_digest.as_bytes(),
            ticket_digest.as_bytes(),
            run_digest.as_bytes(),
            policy_digest.as_bytes(),
            global_capability_set_digest.as_bytes(),
            role_capability_set.semantic_digest(),
            role_capability_set.raw_handle_list_digest(),
            private_control_capability_commitment.encoded_field(),
        ],
    )?;
    let peer_challenge_digest = derive_peer_authentication_challenge(
        role,
        &[
            child_nonce.as_bytes(),
            authority_nonce.as_bytes(),
            &ready.ready_binding_digest,
            &envelope_binding_digest,
            &frame_binding_digest,
            ready.child_observation_context_digest.as_bytes(),
            execution_context.final_generation.as_bytes(),
            execution_context.child_transport_contract.as_bytes(),
            execution_context.start_contract.as_bytes(),
            execution_context.job_membership_epoch.as_bytes(),
            execution_context.runner_token.as_bytes(),
            execution_context.child_image.as_bytes(),
            execution_context.minimal_environment.as_bytes(),
            execution_context.control_server_identity.as_bytes(),
            authority_digest.as_bytes(),
            ticket_digest.as_bytes(),
            run_digest.as_bytes(),
            policy_digest.as_bytes(),
            global_capability_set_digest.as_bytes(),
            role_capability_set.semantic_digest(),
            role_capability_set.raw_handle_list_digest(),
            private_control_capability_commitment.encoded_field(),
        ],
    )?;
    let transcript = ChildHandshakeTranscript::derive(
        role,
        child_nonce,
        authority_nonce,
        &ready.ready_binding_digest,
        &envelope_binding_digest,
        &frame_binding_digest,
        &ready.child_observation_context_digest,
        &execution_context,
        role_capability_set.semantic_digest(),
        role_capability_set.raw_handle_list_digest(),
        ready.wire.as_slice(),
        wire.as_slice(),
    )?;
    Ok(PendingExpectationEnvelope {
        ready,
        authority_nonce,
        frame_binding_digest,
        execution_context,
        authority_digest,
        ticket_digest,
        run_digest,
        policy_digest,
        global_capability_set_digest,
        role_capability_set,
        private_control_capability_commitment,
        envelope_binding_digest,
        peer_challenge_digest,
        transcript,
        wire,
    })
}

pub(crate) struct ChildHandshakeTranscript {
    role: ChildBootstrapRole,
    child_nonce: ChildHandshakeNonce,
    authority_nonce: AuthorityHandshakeNonce,
    ready_binding_digest: BootstrapDigest,
    expectation_binding_digest: BootstrapDigest,
    frame_binding_digest: BootstrapDigest,
    transcript_digest: BootstrapDigest,
    validated_context_digest: BootstrapDigest,
}

impl ChildHandshakeTranscript {
    #[allow(clippy::too_many_arguments)]
    fn derive(
        role: ChildBootstrapRole,
        child_nonce: ChildHandshakeNonce,
        authority_nonce: AuthorityHandshakeNonce,
        ready_binding_digest: &BootstrapDigest,
        expectation_binding_digest: &BootstrapDigest,
        frame_binding_digest: &BootstrapDigest,
        child_observation_context_digest: &ChildObservedLaunchContextDigest,
        execution_context: &AuthorityChildExecutionContext,
        role_semantic_digest: &BootstrapDigest,
        raw_handle_list_digest: &BootstrapDigest,
        ready_wire: &[u8],
        expectation_wire: &[u8],
    ) -> Result<Self, ChildProtocolError> {
        if ready_wire.len() != CHILD_READY_MESSAGE_LEN
            || expectation_wire.len() != CHILD_EXPECTATION_ENVELOPE_LEN
            || is_zero_digest(ready_binding_digest)
            || is_zero_digest(expectation_binding_digest)
            || is_zero_digest(frame_binding_digest)
            || child_nonce.as_bytes() == authority_nonce.as_bytes()
        {
            return Err(ChildProtocolError::new(
                "child_handshake_transcript_invalid",
            ));
        }
        let mut hasher = Sha256::new();
        hasher.update(CHILD_HANDSHAKE_TRANSCRIPT_DOMAIN);
        hasher.update([role.wire_value()]);
        hasher.update((ready_wire.len() as u16).to_be_bytes());
        hasher.update(ready_wire);
        hasher.update((expectation_wire.len() as u16).to_be_bytes());
        hasher.update(expectation_wire);
        hasher.update(frame_binding_digest);
        let transcript_digest: BootstrapDigest = hasher.finalize().into();
        if is_zero_digest(&transcript_digest) {
            return Err(ChildProtocolError::new(
                "child_handshake_transcript_invalid",
            ));
        }
        let validated_context_digest = derive_validated_context_digest(
            role,
            child_observation_context_digest,
            execution_context,
            role_semantic_digest,
            raw_handle_list_digest,
            frame_binding_digest,
            &transcript_digest,
        )?;
        Ok(Self {
            role,
            child_nonce,
            authority_nonce,
            ready_binding_digest: *ready_binding_digest,
            expectation_binding_digest: *expectation_binding_digest,
            frame_binding_digest: *frame_binding_digest,
            transcript_digest,
            validated_context_digest,
        })
    }

    fn clone_for_state(&self) -> Self {
        Self {
            role: self.role,
            child_nonce: self.child_nonce,
            authority_nonce: self.authority_nonce,
            ready_binding_digest: self.ready_binding_digest,
            expectation_binding_digest: self.expectation_binding_digest,
            frame_binding_digest: self.frame_binding_digest,
            transcript_digest: self.transcript_digest,
            validated_context_digest: self.validated_context_digest,
        }
    }
}

impl fmt::Debug for ChildHandshakeTranscript {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ChildHandshakeTranscript")
            .field("role", &self.role)
            .field("bindings", &"<redacted>")
            .finish()
    }
}

#[allow(clippy::too_many_arguments)]
fn derive_validated_context_digest(
    role: ChildBootstrapRole,
    child_observation_context_digest: &ChildObservedLaunchContextDigest,
    execution_context: &AuthorityChildExecutionContext,
    role_semantic_digest: &BootstrapDigest,
    raw_handle_list_digest: &BootstrapDigest,
    frame_binding_digest: &BootstrapDigest,
    transcript_digest: &BootstrapDigest,
) -> Result<BootstrapDigest, ChildProtocolError> {
    let values = [
        child_observation_context_digest.as_bytes(),
        role_semantic_digest,
        raw_handle_list_digest,
        frame_binding_digest,
        transcript_digest,
    ];
    if values.iter().any(|value| is_zero_digest(value)) || has_duplicate_digest_refs(&values) {
        return Err(ChildProtocolError::new("child_validated_context_invalid"));
    }
    let mut hasher = Sha256::new();
    hasher.update(CHILD_VALIDATED_CONTEXT_DOMAIN);
    hasher.update([role.wire_value()]);
    hasher.update(child_observation_context_digest.as_bytes());
    for value in execution_context.ordered_fields() {
        hasher.update(value);
    }
    hasher.update(role_semantic_digest);
    hasher.update(raw_handle_list_digest);
    hasher.update(frame_binding_digest);
    hasher.update(transcript_digest);
    let digest: BootstrapDigest = hasher.finalize().into();
    if is_zero_digest(&digest) {
        return Err(ChildProtocolError::new("child_validated_context_invalid"));
    }
    Ok(digest)
}

pub(crate) struct ChildValidatedHandshake {
    validated: ValidatedChildBootstrap,
    child_observation_context_digest: ChildObservedLaunchContextDigest,
    execution_context: AuthorityChildExecutionContext,
    transcript: ChildHandshakeTranscript,
}

impl ChildValidatedHandshake {
    pub(crate) fn prepare_ack(mut self) -> Result<PreparedBootstrapAck, ChildProtocolError> {
        let validated_context_digest = derive_validated_context_digest(
            self.validated.role(),
            &self.child_observation_context_digest,
            &self.execution_context,
            self.validated
                .bindings
                .role_capability_set
                .semantic_digest(),
            self.validated
                .bindings
                .role_capability_set
                .raw_handle_list_digest(),
            self.validated.frame_binding_digest(),
            &self.transcript.transcript_digest,
        )?;
        if !digests_equal(
            &validated_context_digest,
            &self.transcript.validated_context_digest,
        ) {
            return Err(ChildProtocolError::new(
                "child_validated_context_unexpected",
            ));
        }
        let ack_key = self
            .validated
            .bindings
            .private_control_capability
            .derive_ack_key_and_consume()?;
        let mut wire = BootstrapAckWire::zeroed();
        write_control_header(
            wire.as_mut_slice(),
            &CHILD_BOOTSTRAP_ACK_MAGIC,
            CHILD_BOOTSTRAP_ACK_VERSION,
            self.validated.role(),
            CHILD_BOOTSTRAP_ACK_LEN,
        );
        let mut offset = CHILD_CONTROL_MESSAGE_HEADER_LEN;
        for value in [
            self.transcript.child_nonce.as_bytes(),
            self.transcript.authority_nonce.as_bytes(),
            &self.transcript.frame_binding_digest,
            &self.transcript.expectation_binding_digest,
            &self.transcript.transcript_digest,
            &validated_context_digest,
        ] {
            put_digest(wire.as_mut_slice(), &mut offset, value);
        }
        debug_assert_eq!(offset, CHILD_BOOTSTRAP_ACK_MAC_OFFSET);
        let mac = bootstrap_ack_mac(&ack_key, &wire.as_slice()[..CHILD_BOOTSTRAP_ACK_MAC_OFFSET])?;
        drop(ack_key);
        wire.as_mut_slice()[CHILD_BOOTSTRAP_ACK_MAC_OFFSET..].copy_from_slice(&mac);
        Ok(PreparedBootstrapAck {
            validated: self.validated,
            wire,
        })
    }
}

impl fmt::Debug for ChildValidatedHandshake {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ChildValidatedHandshake")
            .field("role", &self.validated.role())
            .field("state", &"validated")
            .finish()
    }
}

struct BootstrapAckWire(SensitiveBytes<CHILD_BOOTSTRAP_ACK_LEN>);

impl BootstrapAckWire {
    fn zeroed() -> Self {
        Self(SensitiveBytes::zeroed())
    }

    fn as_slice(&self) -> &[u8] {
        self.0.as_slice()
    }

    fn as_mut_slice(&mut self) -> &mut [u8] {
        self.0.as_mut_slice()
    }

    #[cfg(test)]
    fn zeroize_for_test(&mut self) {
        volatile_zero(self.as_mut_slice());
    }
}

impl Drop for BootstrapAckWire {
    fn drop(&mut self) {
        volatile_zero(self.0.as_mut_slice());
        #[cfg(test)]
        record_zeroized_drop(ACK_MESSAGE_DROP_BIT, self.0.as_slice());
    }
}

pub(crate) struct PreparedBootstrapAck {
    validated: ValidatedChildBootstrap,
    wire: BootstrapAckWire,
}

impl PreparedBootstrapAck {
    pub(crate) fn write_to<W: Write>(
        self,
        writer: &mut W,
    ) -> Result<ValidatedChildBootstrap, ChildProtocolError> {
        write_all_retry_interrupted(
            writer,
            self.wire.as_slice(),
            "child_bootstrap_ack_write_failed",
        )?;
        let Self { validated, wire } = self;
        drop(wire);
        Ok(validated)
    }

    #[cfg(test)]
    fn as_bytes_for_test(&self) -> &[u8] {
        self.wire.as_slice()
    }
}

impl fmt::Debug for PreparedBootstrapAck {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PreparedBootstrapAck")
            .field("role", &self.validated.role())
            .field("message", &"<redacted>")
            .finish()
    }
}

pub(crate) struct ReceivedBootstrapAck {
    role: ChildBootstrapRole,
    child_nonce: ChildHandshakeNonce,
    authority_nonce: AuthorityHandshakeNonce,
    frame_binding_digest: BootstrapDigest,
    expectation_binding_digest: BootstrapDigest,
    transcript_digest: BootstrapDigest,
    validated_context_digest: BootstrapDigest,
    mac: BootstrapDigest,
    wire: BootstrapAckWire,
}

impl ReceivedBootstrapAck {
    pub(crate) fn read_from<R: Read>(reader: &mut R) -> Result<Self, ChildProtocolError> {
        let sensitive = read_fixed_message::<R, CHILD_BOOTSTRAP_ACK_LEN>(
            reader,
            "child_bootstrap_ack_read_failed",
            "child_bootstrap_ack_length_invalid",
        )?;
        let mut wire = BootstrapAckWire::zeroed();
        wire.as_mut_slice().copy_from_slice(sensitive.as_slice());
        parse_bootstrap_ack(wire)
    }

    fn verify(
        &self,
        ack_key: &BootstrapAckKey,
        transcript: &ChildHandshakeTranscript,
    ) -> Result<(), ChildProtocolError> {
        if self.role != transcript.role
            || self.child_nonce != transcript.child_nonce
            || self.authority_nonce != transcript.authority_nonce
            || !digests_equal(&self.frame_binding_digest, &transcript.frame_binding_digest)
            || !digests_equal(
                &self.expectation_binding_digest,
                &transcript.expectation_binding_digest,
            )
            || !digests_equal(&self.transcript_digest, &transcript.transcript_digest)
            || !digests_equal(
                &self.validated_context_digest,
                &transcript.validated_context_digest,
            )
        {
            return Err(ChildProtocolError::new(
                "child_bootstrap_ack_transcript_unexpected",
            ));
        }
        let mut verifier = HmacSha256::new_from_slice(ack_key.as_bytes())
            .expect("HMAC-SHA256 accepts fixed protocol keys");
        verifier.update(CHILD_BOOTSTRAP_ACK_MAC_DOMAIN);
        verifier.update(&self.wire.as_slice()[..CHILD_BOOTSTRAP_ACK_MAC_OFFSET]);
        verifier
            .verify_slice(&self.mac)
            .map_err(|_| ChildProtocolError::new("child_bootstrap_ack_mac_invalid"))
    }
}

impl fmt::Debug for ReceivedBootstrapAck {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ReceivedBootstrapAck")
            .field("role", &self.role)
            .field("message", &"<redacted>")
            .finish()
    }
}

impl Drop for ReceivedBootstrapAck {
    fn drop(&mut self) {
        volatile_zero(&mut self.frame_binding_digest);
        volatile_zero(&mut self.expectation_binding_digest);
        volatile_zero(&mut self.transcript_digest);
        volatile_zero(&mut self.validated_context_digest);
        volatile_zero(&mut self.mac);
    }
}

fn parse_bootstrap_ack(wire: BootstrapAckWire) -> Result<ReceivedBootstrapAck, ChildProtocolError> {
    let role = validate_control_header(
        wire.as_slice(),
        &CHILD_BOOTSTRAP_ACK_MAGIC,
        CHILD_BOOTSTRAP_ACK_VERSION,
        CHILD_BOOTSTRAP_ACK_LEN,
        "child_bootstrap_ack",
    )?;
    let mut offset = CHILD_CONTROL_MESSAGE_HEADER_LEN;
    let child_nonce =
        ChildHandshakeNonce::from_fresh_bytes(take_digest(wire.as_slice(), &mut offset))?;
    let authority_nonce =
        AuthorityHandshakeNonce::from_fresh_bytes(take_digest(wire.as_slice(), &mut offset))?;
    let frame_binding_digest = take_digest(wire.as_slice(), &mut offset);
    let expectation_binding_digest = take_digest(wire.as_slice(), &mut offset);
    let transcript_digest = take_digest(wire.as_slice(), &mut offset);
    let validated_context_digest = take_digest(wire.as_slice(), &mut offset);
    if offset != CHILD_BOOTSTRAP_ACK_MAC_OFFSET
        || child_nonce.as_bytes() == authority_nonce.as_bytes()
        || is_zero_digest(&frame_binding_digest)
        || is_zero_digest(&expectation_binding_digest)
        || is_zero_digest(&transcript_digest)
        || is_zero_digest(&validated_context_digest)
    {
        return Err(ChildProtocolError::new(
            "child_bootstrap_ack_fields_invalid",
        ));
    }
    let mut mac = [0u8; CHILD_BOOTSTRAP_DIGEST_LEN];
    mac.copy_from_slice(&wire.as_slice()[CHILD_BOOTSTRAP_ACK_MAC_OFFSET..]);
    if is_zero_digest(&mac) {
        return Err(ChildProtocolError::new("child_bootstrap_ack_mac_invalid"));
    }
    Ok(ReceivedBootstrapAck {
        role,
        child_nonce,
        authority_nonce,
        frame_binding_digest,
        expectation_binding_digest,
        transcript_digest,
        validated_context_digest,
        mac,
        wire,
    })
}

fn bootstrap_ack_mac(
    ack_key: &BootstrapAckKey,
    prefix: &[u8],
) -> Result<BootstrapDigest, ChildProtocolError> {
    if prefix.len() != CHILD_BOOTSTRAP_ACK_MAC_OFFSET {
        return Err(ChildProtocolError::new(
            "child_bootstrap_ack_length_invalid",
        ));
    }
    let mut mac = HmacSha256::new_from_slice(ack_key.as_bytes())
        .expect("HMAC-SHA256 accepts fixed protocol keys");
    mac.update(CHILD_BOOTSTRAP_ACK_MAC_DOMAIN);
    mac.update(prefix);
    let value: BootstrapDigest = mac.finalize().into_bytes().into();
    if is_zero_digest(&value) {
        return Err(ChildProtocolError::new("child_bootstrap_ack_mac_invalid"));
    }
    Ok(value)
}

fn write_all_retry_interrupted<W: Write>(
    writer: &mut W,
    bytes: &[u8],
    error_code: &'static str,
) -> Result<(), ChildProtocolError> {
    let mut offset = 0usize;
    while offset < bytes.len() {
        match writer.write(&bytes[offset..]) {
            Ok(0) => return Err(ChildProtocolError::new(error_code)),
            Ok(written) if written <= bytes.len() - offset => offset += written,
            Ok(_) => return Err(ChildProtocolError::new(error_code)),
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => return Err(ChildProtocolError::new(error_code)),
        }
    }
    Ok(())
}

fn read_fixed_message<R: Read, const N: usize>(
    reader: &mut R,
    read_error_code: &'static str,
    length_error_code: &'static str,
) -> Result<SensitiveBytes<N>, ChildProtocolError> {
    let mut bytes = SensitiveBytes::<N>::zeroed();
    let mut offset = 0usize;
    while offset < N {
        match reader.read(&mut bytes.as_mut_slice()[offset..]) {
            Ok(0) => return Err(ChildProtocolError::new(length_error_code)),
            Ok(read) if read <= N - offset => offset += read,
            Ok(_) => return Err(ChildProtocolError::new(read_error_code)),
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => return Err(ChildProtocolError::new(read_error_code)),
        }
    }
    Ok(bytes)
}

fn write_control_header(
    bytes: &mut [u8],
    magic: &[u8; 8],
    version: u16,
    role: ChildBootstrapRole,
    message_len: usize,
) {
    debug_assert!(bytes.len() >= CHILD_CONTROL_MESSAGE_HEADER_LEN);
    debug_assert!(message_len <= u16::MAX as usize);
    bytes[..8].copy_from_slice(magic);
    bytes[8..10].copy_from_slice(&version.to_be_bytes());
    bytes[10] = role.wire_value();
    bytes[11] = 0;
    bytes[12..14].copy_from_slice(&(message_len as u16).to_be_bytes());
    bytes[14..16].fill(0);
}

fn validate_control_header(
    bytes: &[u8],
    magic: &[u8; 8],
    version: u16,
    expected_len: usize,
    namespace: &'static str,
) -> Result<ChildBootstrapRole, ChildProtocolError> {
    if bytes.len() != expected_len
        || usize::from(u16::from_be_bytes([bytes[12], bytes[13]])) != expected_len
    {
        return Err(ChildProtocolError::new(control_message_error(
            namespace, "length",
        )));
    }
    if bytes[..8] != magic[..] {
        return Err(ChildProtocolError::new(control_message_error(
            namespace, "magic",
        )));
    }
    if u16::from_be_bytes([bytes[8], bytes[9]]) != version {
        return Err(ChildProtocolError::new(control_message_error(
            namespace, "version",
        )));
    }
    if bytes[11] != 0 || bytes[14] != 0 || bytes[15] != 0 {
        return Err(ChildProtocolError::new(control_message_error(
            namespace, "reserved",
        )));
    }
    ChildBootstrapRole::from_wire(bytes[10])
        .map_err(|_| ChildProtocolError::new(control_message_error(namespace, "role")))
}

fn control_message_error(namespace: &'static str, kind: &'static str) -> &'static str {
    match (namespace, kind) {
        ("child_ready", "length") => "child_ready_length_invalid",
        ("child_ready", "magic") => "child_ready_magic_invalid",
        ("child_ready", "version") => "child_ready_version_invalid",
        ("child_ready", "reserved") => "child_ready_reserved_invalid",
        ("child_ready", "role") => "child_ready_role_invalid",
        ("child_expectation", "length") => "child_expectation_length_invalid",
        ("child_expectation", "magic") => "child_expectation_magic_invalid",
        ("child_expectation", "version") => "child_expectation_version_invalid",
        ("child_expectation", "reserved") => "child_expectation_reserved_invalid",
        ("child_expectation", "role") => "child_expectation_role_invalid",
        ("child_bootstrap_ack", "length") => "child_bootstrap_ack_length_invalid",
        ("child_bootstrap_ack", "magic") => "child_bootstrap_ack_magic_invalid",
        ("child_bootstrap_ack", "version") => "child_bootstrap_ack_version_invalid",
        ("child_bootstrap_ack", "reserved") => "child_bootstrap_ack_reserved_invalid",
        ("child_bootstrap_ack", "role") => "child_bootstrap_ack_role_invalid",
        _ => "child_control_message_invalid",
    }
}

fn put_digest(bytes: &mut [u8], offset: &mut usize, value: &BootstrapDigest) {
    bytes[*offset..*offset + CHILD_BOOTSTRAP_DIGEST_LEN].copy_from_slice(value);
    *offset += CHILD_BOOTSTRAP_DIGEST_LEN;
}

fn take_digest(bytes: &[u8], offset: &mut usize) -> BootstrapDigest {
    let mut value = [0u8; CHILD_BOOTSTRAP_DIGEST_LEN];
    value.copy_from_slice(&bytes[*offset..*offset + CHILD_BOOTSTRAP_DIGEST_LEN]);
    *offset += CHILD_BOOTSTRAP_DIGEST_LEN;
    value
}

fn domain_hash(domain: &'static [u8], bytes: &[u8]) -> BootstrapDigest {
    let mut hasher = Sha256::new();
    hasher.update(domain);
    hasher.update((bytes.len() as u32).to_be_bytes());
    hasher.update(bytes);
    hasher.finalize().into()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u16)]
enum ChildBootstrapFieldSemantic {
    AuthorityDigest = 1,
    TicketDigest = 2,
    RunDigest = 3,
    PolicyDigest = 4,
    GlobalCapabilitySetDigest = 5,
    RoleInheritedCapabilitySetDigest = 6,
    RawHandleListDigest = 7,
    PrivateControlCapability = 8,
}

impl ChildBootstrapFieldSemantic {
    fn wire_value(self) -> u16 {
        self as u16
    }
}

struct ChildBootstrapEofWitness;

/// A complete transport read bound to the one fixed inherited slot. The only
/// constructor owns the read loop and observes EOF after exactly one frame.
pub struct ChildBootstrapTransportFrame {
    slot: ChildBootstrapTransportSlot,
    bytes: [u8; CHILD_BOOTSTRAP_FRAME_LEN],
    _eof_witness: ChildBootstrapEofWitness,
}

impl fmt::Debug for ChildBootstrapTransportFrame {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ChildBootstrapTransportFrame")
            .field("slot", &self.slot)
            .field("eofObserved", &true)
            .field("bytes", &"<redacted>")
            .finish()
    }
}

impl ChildBootstrapTransportFrame {
    pub(crate) fn read_complete_from<R: Read>(
        slot: ChildBootstrapTransportSlot,
        reader: &mut R,
    ) -> Result<Self, ChildProtocolError> {
        if slot != ChildBootstrapTransportSlot::fixed() {
            return Err(ChildProtocolError::new(
                "child_bootstrap_transport_slot_invalid",
            ));
        }
        let mut observed = SensitiveBytes::<CHILD_BOOTSTRAP_TRANSPORT_READ_LIMIT>::zeroed();
        let mut observed_len = 0usize;
        loop {
            if observed_len == CHILD_BOOTSTRAP_TRANSPORT_READ_LIMIT {
                return Err(ChildProtocolError::new(
                    "child_bootstrap_transport_length_invalid",
                ));
            }
            match reader.read(&mut observed.as_mut_slice()[observed_len..]) {
                Ok(0) => break,
                Ok(read) => {
                    let remaining = CHILD_BOOTSTRAP_TRANSPORT_READ_LIMIT - observed_len;
                    if read > remaining {
                        return Err(ChildProtocolError::new(
                            "child_bootstrap_transport_read_invalid",
                        ));
                    }
                    observed_len += read;
                }
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => {
                    return Err(ChildProtocolError::new(
                        "child_bootstrap_transport_read_failed",
                    ))
                }
            }
        }
        if observed_len != CHILD_BOOTSTRAP_FRAME_LEN {
            return Err(ChildProtocolError::new(
                "child_bootstrap_transport_length_invalid",
            ));
        }
        let mut bytes = [0u8; CHILD_BOOTSTRAP_FRAME_LEN];
        bytes.copy_from_slice(&observed.as_slice()[..CHILD_BOOTSTRAP_FRAME_LEN]);
        Ok(Self {
            slot,
            bytes,
            _eof_witness: ChildBootstrapEofWitness,
        })
    }

    pub fn slot(&self) -> ChildBootstrapTransportSlot {
        self.slot
    }

    #[cfg(test)]
    fn decode_for_role(
        self,
        expected_role: ChildBootstrapRole,
        expectations: &ChildBootstrapExpectations,
    ) -> Result<ValidatedChildBootstrap, ChildProtocolError> {
        if expectations.role != expected_role {
            return Err(ChildProtocolError::new(
                "child_bootstrap_expectation_role_mismatch",
            ));
        }
        self.begin_validation(expected_role)?.validate(expectations)
    }

    /// Parses only the self-authenticating wire shape needed to challenge an
    /// independently authenticated control peer. The returned value exposes no
    /// authority, ticket, run, policy, or capability-set binding before that
    /// peer supplies the matching expectations.
    pub fn begin_validation(
        self,
        expected_role: ChildBootstrapRole,
    ) -> Result<PendingChildBootstrapValidation, ChildProtocolError> {
        PendingChildBootstrapValidation::parse_for_role_and_slot(
            &self.bytes,
            expected_role,
            self.slot,
        )
    }

    #[cfg(test)]
    fn zeroize_for_test(&mut self) {
        volatile_zero(&mut self.bytes);
    }
}

/// Structurally valid bootstrap material awaiting an independent expectation
/// source. It is deliberately non-Clone, keeps the one-use control capability
/// opaque, and can only consume it while validating authenticated expectations.
pub struct PendingChildBootstrapValidation {
    role: ChildBootstrapRole,
    transport_slot: ChildBootstrapTransportSlot,
    authority_digest: AuthorityBindingDigest,
    ticket_digest: TicketBindingDigest,
    run_digest: RunBindingDigest,
    policy_digest: PolicyBindingDigest,
    global_capability_set_digest: GlobalCapabilitySetDigest,
    role_capability_semantic_digest: BootstrapDigest,
    role_capability_raw_handle_digest: BootstrapDigest,
    private_control_capability: PrivateControlCapability,
    frame_binding_digest: BootstrapDigest,
}

impl fmt::Debug for PendingChildBootstrapValidation {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PendingChildBootstrapValidation")
            .field("role", &self.role)
            .field("transportSlot", &self.transport_slot)
            .field("bindings", &"<redacted>")
            .finish()
    }
}

impl PendingChildBootstrapValidation {
    pub fn role(&self) -> ChildBootstrapRole {
        self.role
    }

    pub fn transport_slot(&self) -> ChildBootstrapTransportSlot {
        self.transport_slot
    }

    pub fn frame_binding_digest(&self) -> &BootstrapDigest {
        &self.frame_binding_digest
    }

    #[cfg(test)]
    fn private_control_capability_for_test(&self) -> &PrivateControlCapability {
        &self.private_control_capability
    }

    fn parse_for_role_and_slot(
        bytes: &[u8],
        expected_role: ChildBootstrapRole,
        expected_slot: ChildBootstrapTransportSlot,
    ) -> Result<Self, ChildProtocolError> {
        let ParsedChildBootstrap {
            role,
            transport_slot,
            authority_digest,
            ticket_digest,
            run_digest,
            policy_digest,
            global_capability_set_digest,
            role_capability_semantic_digest,
            role_capability_raw_handle_digest,
            private_control_capability,
            frame_binding_digest,
        } = parse_child_bootstrap(bytes, expected_role, expected_slot)?;
        Ok(Self {
            role,
            transport_slot,
            authority_digest,
            ticket_digest,
            run_digest,
            policy_digest,
            global_capability_set_digest,
            role_capability_semantic_digest,
            role_capability_raw_handle_digest,
            private_control_capability,
            frame_binding_digest,
        })
    }

    pub fn validate(
        self,
        expectations: &ChildBootstrapExpectations,
    ) -> Result<ValidatedChildBootstrap, ChildProtocolError> {
        if expectations.role != self.role {
            return Err(ChildProtocolError::new(
                "child_bootstrap_expectation_role_mismatch",
            ));
        }
        validate_expected_digest(
            self.authority_digest.as_bytes(),
            expectations.authority_digest.as_bytes(),
            "child_bootstrap_authority_binding_unexpected",
        )?;
        validate_expected_digest(
            self.ticket_digest.as_bytes(),
            expectations.ticket_digest.as_bytes(),
            "child_bootstrap_ticket_binding_unexpected",
        )?;
        validate_expected_digest(
            self.run_digest.as_bytes(),
            expectations.run_digest.as_bytes(),
            "child_bootstrap_run_binding_unexpected",
        )?;
        validate_expected_digest(
            self.policy_digest.as_bytes(),
            expectations.policy_digest.as_bytes(),
            "child_bootstrap_policy_binding_unexpected",
        )?;
        validate_expected_digest(
            self.global_capability_set_digest.as_bytes(),
            expectations.global_capability_set_digest.as_bytes(),
            "child_bootstrap_global_capability_set_unexpected",
        )?;
        validate_expected_digest(
            &self.role_capability_semantic_digest,
            expectations.role_capability_set.semantic_digest(),
            "child_bootstrap_role_capability_semantics_unexpected",
        )?;
        validate_expected_digest(
            &self.role_capability_raw_handle_digest,
            expectations.role_capability_set.raw_handle_list_digest(),
            "child_bootstrap_role_capability_handles_unexpected",
        )?;
        let observed_private_control_commitment = self.private_control_capability.commitment()?;
        validate_expected_digest(
            observed_private_control_commitment.encoded_field(),
            expectations
                .private_control_capability_commitment
                .encoded_field(),
            "child_bootstrap_private_control_commitment_unexpected",
        )?;

        let bindings = ChildBootstrapBindings::new(
            self.authority_digest,
            self.ticket_digest,
            self.run_digest,
            self.policy_digest,
            self.global_capability_set_digest,
            expectations.role_capability_set.clone(),
            self.private_control_capability,
        )?;
        Ok(ValidatedChildBootstrap {
            role: self.role,
            transport_slot: self.transport_slot,
            bindings,
            frame_binding_digest: self.frame_binding_digest,
        })
    }
}

struct ParsedChildBootstrap {
    role: ChildBootstrapRole,
    transport_slot: ChildBootstrapTransportSlot,
    authority_digest: AuthorityBindingDigest,
    ticket_digest: TicketBindingDigest,
    run_digest: RunBindingDigest,
    policy_digest: PolicyBindingDigest,
    global_capability_set_digest: GlobalCapabilitySetDigest,
    role_capability_semantic_digest: BootstrapDigest,
    role_capability_raw_handle_digest: BootstrapDigest,
    private_control_capability: PrivateControlCapability,
    frame_binding_digest: BootstrapDigest,
}

impl Drop for ChildBootstrapTransportFrame {
    fn drop(&mut self) {
        volatile_zero(&mut self.bytes);
        #[cfg(test)]
        record_zeroized_drop(TRANSPORT_FRAME_DROP_BIT, &self.bytes);
    }
}

/// Owning encoded frame. It deliberately does not expose an owning byte array;
/// callers borrow it for a bounded write and the buffer is erased on drop.
pub struct EncodedChildBootstrapFrame {
    bytes: [u8; CHILD_BOOTSTRAP_FRAME_LEN],
}

impl EncodedChildBootstrapFrame {
    #[cfg(test)]
    pub fn as_bytes(&self) -> &[u8; CHILD_BOOTSTRAP_FRAME_LEN] {
        &self.bytes
    }

    #[cfg(test)]
    fn as_mut_bytes_for_test(&mut self) -> &mut [u8; CHILD_BOOTSTRAP_FRAME_LEN] {
        &mut self.bytes
    }

    #[cfg(test)]
    fn zeroize_for_test(&mut self) {
        volatile_zero(&mut self.bytes);
    }

    fn write_complete_to<W: Write>(&self, writer: &mut W) -> Result<(), ChildProtocolError> {
        write_all_retry_interrupted(
            writer,
            &self.bytes,
            "child_bootstrap_transport_write_failed",
        )
    }
}

impl fmt::Debug for EncodedChildBootstrapFrame {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("EncodedChildBootstrapFrame(<redacted>)")
    }
}

impl Drop for EncodedChildBootstrapFrame {
    fn drop(&mut self) {
        volatile_zero(&mut self.bytes);
        #[cfg(test)]
        record_zeroized_drop(ENCODED_FRAME_DROP_BIT, &self.bytes);
    }
}

/// Parent-side owner of one exact bootstrap frame and its one-use ACK key.
/// The only production encoder is reached through this semantic preparation
/// step; raw frame constructors remain unavailable.
pub(crate) struct ParentPreparedChildBootstrapFrame {
    role: ChildBootstrapRole,
    ack_verifier_key: BootstrapAckKey,
    encoded: EncodedChildBootstrapFrame,
    frame_binding_digest: BootstrapDigest,
    protocol_field_projection_digest: ParentProtocolFieldProjectionDigest,
}

impl ParentPreparedChildBootstrapFrame {
    pub(crate) fn prepare(
        role: ChildBootstrapRole,
        bindings: ChildBootstrapBindings,
    ) -> Result<Self, ChildProtocolError> {
        if bindings.role_capability_set.role() != role {
            return Err(ChildProtocolError::new(
                "child_bootstrap_role_capability_set_mismatch",
            ));
        }
        let transport_slot = ChildBootstrapTransportSlot::fixed();
        let private_control_capability_commitment =
            bindings.private_control_capability.commitment()?;
        let protocol_field_projection_digest = derive_parent_protocol_field_projection_digest(
            role,
            &bindings.authority_digest,
            &bindings.ticket_digest,
            &bindings.run_digest,
            &bindings.policy_digest,
            &bindings.global_capability_set_digest,
            &bindings.role_capability_set,
            &private_control_capability_commitment,
        )?;
        let ack_verifier_key = BootstrapAckKey::derive(&bindings.private_control_capability)?;
        let prefix = encode_prefix(role, transport_slot, &bindings);
        let frame_binding_digest = frame_binding_digest(prefix.as_slice());
        if is_zero_digest(&frame_binding_digest) {
            return Err(ChildProtocolError::new("child_bootstrap_binding_invalid"));
        }
        let mut bytes = [0u8; CHILD_BOOTSTRAP_FRAME_LEN];
        bytes[..CHILD_BOOTSTRAP_BINDING_OFFSET].copy_from_slice(prefix.as_slice());
        bytes[CHILD_BOOTSTRAP_BINDING_OFFSET..].copy_from_slice(&frame_binding_digest);
        drop(bindings);
        Ok(Self {
            role,
            ack_verifier_key,
            encoded: EncodedChildBootstrapFrame { bytes },
            frame_binding_digest,
            protocol_field_projection_digest,
        })
    }

    pub(crate) fn role(&self) -> ChildBootstrapRole {
        self.role
    }

    pub(crate) fn frame_binding_digest(&self) -> &BootstrapDigest {
        &self.frame_binding_digest
    }

    pub(crate) fn protocol_field_projection_digest(&self) -> &ParentProtocolFieldProjectionDigest {
        &self.protocol_field_projection_digest
    }

    pub(crate) fn write_complete_to<W: Write>(
        self,
        writer: &mut W,
    ) -> Result<ParentAwaitingBootstrapAck, ChildProtocolError> {
        self.encoded.write_complete_to(writer)?;
        let Self {
            role,
            ack_verifier_key,
            encoded,
            frame_binding_digest,
            protocol_field_projection_digest: _,
        } = self;
        drop(encoded);
        Ok(ParentAwaitingBootstrapAck {
            role,
            ack_verifier_key,
            frame_binding_digest,
        })
    }

    #[cfg(test)]
    fn as_bytes_for_test(&self) -> &[u8; CHILD_BOOTSTRAP_FRAME_LEN] {
        self.encoded.as_bytes()
    }
}

impl fmt::Debug for ParentPreparedChildBootstrapFrame {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ParentPreparedChildBootstrapFrame")
            .field("role", &self.role)
            .field("frame", &"<redacted>")
            .finish()
    }
}

pub(crate) struct ParentAwaitingBootstrapAck {
    role: ChildBootstrapRole,
    ack_verifier_key: BootstrapAckKey,
    frame_binding_digest: BootstrapDigest,
}

impl ParentAwaitingBootstrapAck {
    pub(crate) fn verify_ack(
        self,
        expectation_sent: ParentExpectationSent,
        ack: ReceivedBootstrapAck,
    ) -> Result<(), ChildProtocolError> {
        let transcript = expectation_sent.transcript();
        if self.role != transcript.role
            || self.role != ack.role
            || !digests_equal(&self.frame_binding_digest, &transcript.frame_binding_digest)
        {
            return Err(ChildProtocolError::new(
                "child_bootstrap_ack_transcript_unexpected",
            ));
        }
        ack.verify(&self.ack_verifier_key, transcript)
    }
}

impl fmt::Debug for ParentAwaitingBootstrapAck {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ParentAwaitingBootstrapAck")
            .field("role", &self.role)
            .field("verification", &"<redacted>")
            .finish()
    }
}

/// A decoded frame whose magic, version, length, role, transport slot, typed
/// field semantics, non-zero values, and complete frame binding are verified.
pub struct ValidatedChildBootstrap {
    role: ChildBootstrapRole,
    transport_slot: ChildBootstrapTransportSlot,
    bindings: ChildBootstrapBindings,
    frame_binding_digest: BootstrapDigest,
}

impl fmt::Debug for ValidatedChildBootstrap {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ValidatedChildBootstrap")
            .field("role", &self.role)
            .field("transportSlot", &self.transport_slot)
            .field("bindings", &self.bindings)
            .field("frameBindingDigest", &"<redacted>")
            .finish()
    }
}

impl ValidatedChildBootstrap {
    #[cfg(test)]
    fn new_for_test(
        role: ChildBootstrapRole,
        bindings: ChildBootstrapBindings,
    ) -> Result<Self, ChildProtocolError> {
        if bindings.role_capability_set.role() != role {
            return Err(ChildProtocolError::new(
                "child_bootstrap_role_capability_set_mismatch",
            ));
        }
        let transport_slot = ChildBootstrapTransportSlot::fixed();
        let frame = encode_prefix(role, transport_slot, &bindings);
        let frame_binding_digest = frame_binding_digest(frame.as_slice());
        if is_zero_digest(&frame_binding_digest) {
            return Err(ChildProtocolError::new("child_bootstrap_binding_invalid"));
        }
        Ok(Self {
            role,
            transport_slot,
            bindings,
            frame_binding_digest,
        })
    }

    pub fn role(&self) -> ChildBootstrapRole {
        self.role
    }

    pub fn transport_slot(&self) -> ChildBootstrapTransportSlot {
        self.transport_slot
    }

    pub fn bindings(&self) -> &ChildBootstrapBindings {
        &self.bindings
    }

    pub fn frame_binding_digest(&self) -> &BootstrapDigest {
        &self.frame_binding_digest
    }

    #[cfg(test)]
    fn encode_for_test(&self) -> EncodedChildBootstrapFrame {
        let prefix = encode_prefix(self.role, self.transport_slot, &self.bindings);
        let mut frame = [0u8; CHILD_BOOTSTRAP_FRAME_LEN];
        frame[..CHILD_BOOTSTRAP_BINDING_OFFSET].copy_from_slice(prefix.as_slice());
        frame[CHILD_BOOTSTRAP_BINDING_OFFSET..].copy_from_slice(&self.frame_binding_digest);
        EncodedChildBootstrapFrame { bytes: frame }
    }

    #[cfg(test)]
    fn decode_for_role(
        bytes: &[u8],
        expected_role: ChildBootstrapRole,
        expectations: &ChildBootstrapExpectations,
    ) -> Result<Self, ChildProtocolError> {
        Self::decode_for_role_and_slot(
            bytes,
            expected_role,
            ChildBootstrapTransportSlot::fixed(),
            expectations,
        )
    }

    #[cfg(test)]
    fn decode_for_role_and_slot(
        bytes: &[u8],
        expected_role: ChildBootstrapRole,
        expected_slot: ChildBootstrapTransportSlot,
        expectations: &ChildBootstrapExpectations,
    ) -> Result<Self, ChildProtocolError> {
        if expectations.role != expected_role {
            return Err(ChildProtocolError::new(
                "child_bootstrap_expectation_role_mismatch",
            ));
        }
        PendingChildBootstrapValidation::parse_for_role_and_slot(
            bytes,
            expected_role,
            expected_slot,
        )?
        .validate(expectations)
    }
}

fn parse_child_bootstrap(
    bytes: &[u8],
    expected_role: ChildBootstrapRole,
    expected_slot: ChildBootstrapTransportSlot,
) -> Result<ParsedChildBootstrap, ChildProtocolError> {
    if bytes.len() != CHILD_BOOTSTRAP_FRAME_LEN {
        return Err(ChildProtocolError::new("child_bootstrap_length_invalid"));
    }
    if bytes[..CHILD_BOOTSTRAP_MAGIC.len()] != CHILD_BOOTSTRAP_MAGIC {
        return Err(ChildProtocolError::new("child_bootstrap_magic_invalid"));
    }
    if u16::from_be_bytes([bytes[8], bytes[9]]) != CHILD_BOOTSTRAP_VERSION {
        return Err(ChildProtocolError::new("child_bootstrap_version_invalid"));
    }
    if bytes[14] != 0 || bytes[15] != 0 {
        return Err(ChildProtocolError::new("child_bootstrap_reserved_invalid"));
    }
    if usize::from(u16::from_be_bytes([bytes[12], bytes[13]])) != CHILD_BOOTSTRAP_FRAME_LEN {
        return Err(ChildProtocolError::new("child_bootstrap_length_invalid"));
    }
    let role = ChildBootstrapRole::from_wire(bytes[10])?;
    if role != expected_role {
        return Err(ChildProtocolError::new("child_bootstrap_role_unexpected"));
    }
    let transport_slot = ChildBootstrapTransportSlot::try_from_id(bytes[11])?;
    if transport_slot != expected_slot {
        return Err(ChildProtocolError::new(
            "child_bootstrap_transport_slot_unexpected",
        ));
    }

    let mut observed_binding = [0u8; CHILD_BOOTSTRAP_DIGEST_LEN];
    observed_binding.copy_from_slice(&bytes[CHILD_BOOTSTRAP_BINDING_OFFSET..]);
    if is_zero_digest(&observed_binding) {
        return Err(ChildProtocolError::new("child_bootstrap_binding_invalid"));
    }
    let expected_binding = frame_binding_digest(&bytes[..CHILD_BOOTSTRAP_BINDING_OFFSET]);
    if !digests_equal(&observed_binding, &expected_binding) {
        return Err(ChildProtocolError::new("child_bootstrap_binding_invalid"));
    }

    let mut offset = CHILD_BOOTSTRAP_HEADER_LEN;
    let authority_digest = AuthorityBindingDigest::from_wire(take_field(
        bytes,
        &mut offset,
        ChildBootstrapFieldSemantic::AuthorityDigest,
    )?)?;
    let ticket_digest = TicketBindingDigest::from_wire(take_field(
        bytes,
        &mut offset,
        ChildBootstrapFieldSemantic::TicketDigest,
    )?)?;
    let run_digest = RunBindingDigest::from_wire(take_field(
        bytes,
        &mut offset,
        ChildBootstrapFieldSemantic::RunDigest,
    )?)?;
    let policy_digest = PolicyBindingDigest::from_wire(take_field(
        bytes,
        &mut offset,
        ChildBootstrapFieldSemantic::PolicyDigest,
    )?)?;
    let global_capability_set_digest = GlobalCapabilitySetDigest::from_wire(take_field(
        bytes,
        &mut offset,
        ChildBootstrapFieldSemantic::GlobalCapabilitySetDigest,
    )?)?;
    let role_capability_semantic_digest = take_field(
        bytes,
        &mut offset,
        ChildBootstrapFieldSemantic::RoleInheritedCapabilitySetDigest,
    )?;
    let role_capability_raw_handle_digest = take_field(
        bytes,
        &mut offset,
        ChildBootstrapFieldSemantic::RawHandleListDigest,
    )?;
    let mut private_control_bytes = take_field(
        bytes,
        &mut offset,
        ChildBootstrapFieldSemantic::PrivateControlCapability,
    )?;
    let private_control_capability =
        PrivateControlCapability::take_from_wire(&mut private_control_bytes)?;
    if offset != CHILD_BOOTSTRAP_BINDING_OFFSET {
        return Err(ChildProtocolError::new("child_bootstrap_length_invalid"));
    }

    let binding_values = [
        authority_digest.as_bytes(),
        ticket_digest.as_bytes(),
        run_digest.as_bytes(),
        policy_digest.as_bytes(),
        global_capability_set_digest.as_bytes(),
        &role_capability_semantic_digest,
        &role_capability_raw_handle_digest,
        private_control_capability.encoded_field(),
    ];
    if has_duplicate_digest_refs(&binding_values) {
        return Err(ChildProtocolError::new(
            "child_bootstrap_binding_value_reused",
        ));
    }

    Ok(ParsedChildBootstrap {
        role,
        transport_slot,
        authority_digest,
        ticket_digest,
        run_digest,
        policy_digest,
        global_capability_set_digest,
        role_capability_semantic_digest,
        role_capability_raw_handle_digest,
        private_control_capability,
        frame_binding_digest: observed_binding,
    })
}

impl Drop for ValidatedChildBootstrap {
    fn drop(&mut self) {
        volatile_zero(&mut self.frame_binding_digest);
    }
}

fn encode_prefix(
    role: ChildBootstrapRole,
    transport_slot: ChildBootstrapTransportSlot,
    bindings: &ChildBootstrapBindings,
) -> SensitiveBytes<CHILD_BOOTSTRAP_BINDING_OFFSET> {
    let mut bytes = SensitiveBytes::<CHILD_BOOTSTRAP_BINDING_OFFSET>::zeroed();
    bytes.as_mut_slice()[..8].copy_from_slice(&CHILD_BOOTSTRAP_MAGIC);
    bytes.as_mut_slice()[8..10].copy_from_slice(&CHILD_BOOTSTRAP_VERSION.to_be_bytes());
    bytes.as_mut_slice()[10] = role.wire_value();
    bytes.as_mut_slice()[11] = transport_slot.id();
    bytes.as_mut_slice()[12..14].copy_from_slice(&(CHILD_BOOTSTRAP_FRAME_LEN as u16).to_be_bytes());
    let mut offset = CHILD_BOOTSTRAP_HEADER_LEN;
    for (semantic, field) in bindings.ordered_fields() {
        write_field(bytes.as_mut_slice(), &mut offset, semantic, field);
    }
    debug_assert_eq!(offset, CHILD_BOOTSTRAP_BINDING_OFFSET);
    bytes
}

fn write_field(
    bytes: &mut [u8],
    offset: &mut usize,
    semantic: ChildBootstrapFieldSemantic,
    digest: &BootstrapDigest,
) {
    bytes[*offset..*offset + 2].copy_from_slice(&semantic.wire_value().to_be_bytes());
    bytes[*offset + 2..*offset + CHILD_BOOTSTRAP_FIELD_HEADER_LEN].fill(0);
    let digest_start = *offset + CHILD_BOOTSTRAP_FIELD_HEADER_LEN;
    bytes[digest_start..digest_start + CHILD_BOOTSTRAP_DIGEST_LEN].copy_from_slice(digest);
    *offset += CHILD_BOOTSTRAP_FIELD_WIRE_LEN;
}

fn take_field(
    bytes: &[u8],
    offset: &mut usize,
    expected_semantic: ChildBootstrapFieldSemantic,
) -> Result<BootstrapDigest, ChildProtocolError> {
    let observed_semantic = u16::from_be_bytes([bytes[*offset], bytes[*offset + 1]]);
    if observed_semantic != expected_semantic.wire_value() {
        return Err(ChildProtocolError::new(
            "child_bootstrap_field_semantics_invalid",
        ));
    }
    if bytes[*offset + 2] != 0 || bytes[*offset + 3] != 0 {
        return Err(ChildProtocolError::new(
            "child_bootstrap_field_reserved_invalid",
        ));
    }
    let digest_start = *offset + CHILD_BOOTSTRAP_FIELD_HEADER_LEN;
    let mut digest = [0u8; CHILD_BOOTSTRAP_DIGEST_LEN];
    digest.copy_from_slice(&bytes[digest_start..digest_start + CHILD_BOOTSTRAP_DIGEST_LEN]);
    *offset += CHILD_BOOTSTRAP_FIELD_WIRE_LEN;
    Ok(digest)
}

fn frame_binding_digest(prefix: &[u8]) -> BootstrapDigest {
    let mut hasher = Sha256::new();
    hasher.update(CHILD_BOOTSTRAP_BINDING_DOMAIN);
    hasher.update(prefix);
    hasher.finalize().into()
}

fn validate_expected_digest(
    observed: &BootstrapDigest,
    expected: &BootstrapDigest,
    error_code: &'static str,
) -> Result<(), ChildProtocolError> {
    if !digests_equal(observed, expected) {
        return Err(ChildProtocolError::new(error_code));
    }
    Ok(())
}

fn validate_distinct_nonzero_digests(
    values: &[BootstrapDigest],
    error_code: &'static str,
) -> Result<(), ChildProtocolError> {
    if values.iter().any(is_zero_digest) || has_duplicate_digests(values) {
        return Err(ChildProtocolError::new(error_code));
    }
    Ok(())
}

fn has_duplicate_digests(values: &[BootstrapDigest]) -> bool {
    values.iter().enumerate().any(|(index, value)| {
        values[..index]
            .iter()
            .any(|prior| digests_equal(value, prior))
    })
}

fn has_duplicate_digest_refs(values: &[&BootstrapDigest]) -> bool {
    values.iter().enumerate().any(|(index, value)| {
        values[..index]
            .iter()
            .any(|prior| digests_equal(value, prior))
    })
}

fn is_zero_digest(value: &BootstrapDigest) -> bool {
    value.iter().all(|byte| *byte == 0)
}

fn digests_equal(left: &BootstrapDigest, right: &BootstrapDigest) -> bool {
    left.iter()
        .zip(right)
        .fold(0u8, |difference, (left, right)| difference | (left ^ right))
        == 0
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Cursor, Read, Write};

    fn source_capabilities() -> [BootstrapDigest; GLOBAL_CAPABILITY_SOURCE_COUNT] {
        std::array::from_fn(|index| [0x50 + index as u8; CHILD_BOOTSTRAP_DIGEST_LEN])
    }

    fn role_slots(role: ChildBootstrapRole) -> Vec<TestRoleCapabilitySlotBinding> {
        child_role_capability_schema(role)
            .iter()
            .enumerate()
            .map(|(index, descriptor)| {
                TestRoleCapabilitySlotBinding::new(
                    descriptor.semantic(),
                    [0x81 + index as u8; CHILD_BOOTSTRAP_DIGEST_LEN],
                    0x101usize * (index + 1),
                )
            })
            .collect()
    }

    fn role_capability_set(role: ChildBootstrapRole) -> RoleCapabilitySetBinding {
        RoleCapabilitySetBinding::derive_for_test(role, &role_slots(role))
            .expect("fixed test-only role capability set")
    }

    fn authority() -> AuthorityBindingDigest {
        AuthorityBindingDigest::derive(&[0x11; 32]).expect("authority binding")
    }

    fn ticket() -> TicketBindingDigest {
        TicketBindingDigest::derive(&[0x22; 32]).expect("ticket binding")
    }

    fn run_binding() -> RunBindingDigest {
        RunBindingDigest::derive(&[0x33; 32]).expect("run binding")
    }

    fn policy() -> PolicyBindingDigest {
        PolicyBindingDigest::derive(&[0x44; 32]).expect("policy binding")
    }

    fn global() -> GlobalCapabilitySetDigest {
        GlobalCapabilitySetDigest::derive(&source_capabilities()).expect("global binding")
    }

    fn private_control() -> PrivateControlCapability {
        let mut source = [0x77; 32];
        let capability = PrivateControlCapability::take_for_parent(&mut source)
            .expect("private control capability");
        assert!(source.iter().all(|byte| *byte == 0));
        capability
    }

    fn private_control_commitment() -> PrivateControlCapabilityCommitment {
        let capability = private_control();
        PrivateControlCapabilityCommitment::from_parent_capability(&capability)
            .expect("private control commitment")
    }

    fn bindings(role: ChildBootstrapRole) -> ChildBootstrapBindings {
        ChildBootstrapBindings::new(
            authority(),
            ticket(),
            run_binding(),
            policy(),
            global(),
            role_capability_set(role),
            private_control(),
        )
        .expect("fixed bindings")
    }

    fn expectations(role: ChildBootstrapRole) -> ChildBootstrapExpectations {
        ChildBootstrapExpectations::new_for_test(
            role,
            authority(),
            ticket(),
            run_binding(),
            policy(),
            global(),
            role_capability_set(role),
            private_control_commitment(),
        )
        .expect("fixed expectations")
    }

    fn parent_expectations(
        role: ChildBootstrapRole,
        nonce_byte: u8,
    ) -> ParentChildBootstrapExpectations {
        ParentChildBootstrapExpectations::from_authority_projection(
            role,
            authority(),
            ticket(),
            run_binding(),
            policy(),
            global(),
            role_capability_set(role),
            private_control_commitment(),
            child_observation_context(nonce_byte.wrapping_add(0x30)),
            execution_context(),
        )
        .expect("fixed authority expectation projection")
    }

    fn execution_context() -> AuthorityChildExecutionContext {
        AuthorityChildExecutionContext::from_independent_measurements(
            FinalGenerationContextDigest::derive(&[0x91; 32]).unwrap(),
            ChildTransportContractContextDigest::derive(&[0x92; 32]).unwrap(),
            StartContractContextDigest::derive(&[0x93; 32]).unwrap(),
            JobMembershipEpochContextDigest::derive(&[0x94; 32]).unwrap(),
            RunnerTokenContextDigest::derive(&[0x95; 32]).unwrap(),
            ChildImageContextDigest::derive(&[0x96; 32]).unwrap(),
            MinimalEnvironmentContextDigest::derive(&[0x97; 32]).unwrap(),
            ControlServerIdentityContextDigest::derive(&[0x98; 32]).unwrap(),
        )
        .unwrap()
    }

    fn child_observation_context(nonce_byte: u8) -> ChildObservedLaunchContextDigest {
        ChildObservedLaunchContextDigest::derive(&[nonce_byte; 32]).unwrap()
    }

    fn raw_handle_list(role: ChildBootstrapRole) -> RoleRawHandleListDigest {
        let slots = role_slots(role);
        let raw = std::array::from_fn(|index| slots[index].raw_handle);
        RoleRawHandleListDigest::derive(role, &raw).expect("fixed raw handle list")
    }

    fn child_ready(role: ChildBootstrapRole, nonce_byte: u8) -> PreparedChildReady {
        PreparedChildReady::prepare(
            role,
            ChildHandshakeNonce::from_fresh_bytes([nonce_byte; 32]).unwrap(),
            &role_capability_set(role),
            raw_handle_list(role),
            child_observation_context(nonce_byte.wrapping_add(0x30)),
        )
        .expect("fixed child ready")
    }

    fn parent_frame(role: ChildBootstrapRole) -> ParentPreparedChildBootstrapFrame {
        ParentPreparedChildBootstrapFrame::prepare(role, bindings(role))
            .expect("fixed parent frame")
    }

    #[derive(Default)]
    struct ChunkedWriter {
        bytes: Vec<u8>,
        max_chunk: usize,
        interrupt_next: bool,
        writes: usize,
        flushes: usize,
    }

    impl ChunkedWriter {
        fn new(max_chunk: usize, interrupt_next: bool) -> Self {
            Self {
                max_chunk,
                interrupt_next,
                ..Self::default()
            }
        }
    }

    impl Write for ChunkedWriter {
        fn write(&mut self, source: &[u8]) -> io::Result<usize> {
            self.writes += 1;
            if self.interrupt_next {
                self.interrupt_next = false;
                return Err(io::Error::new(io::ErrorKind::Interrupted, "retry"));
            }
            let written = self.max_chunk.max(1).min(source.len());
            self.bytes.extend_from_slice(&source[..written]);
            Ok(written)
        }

        fn flush(&mut self) -> io::Result<()> {
            self.flushes += 1;
            Ok(())
        }
    }

    struct ChunkedReader {
        bytes: Vec<u8>,
        offset: usize,
        max_chunk: usize,
        interrupt_next: bool,
    }

    impl ChunkedReader {
        fn new(bytes: Vec<u8>, max_chunk: usize, interrupt_next: bool) -> Self {
            Self {
                bytes,
                offset: 0,
                max_chunk,
                interrupt_next,
            }
        }
    }

    impl Read for ChunkedReader {
        fn read(&mut self, target: &mut [u8]) -> io::Result<usize> {
            if self.interrupt_next {
                self.interrupt_next = false;
                return Err(io::Error::new(io::ErrorKind::Interrupted, "retry"));
            }
            if self.offset == self.bytes.len() {
                return Ok(0);
            }
            let read = self
                .max_chunk
                .max(1)
                .min(target.len())
                .min(self.bytes.len() - self.offset);
            target[..read].copy_from_slice(&self.bytes[self.offset..self.offset + read]);
            self.offset += read;
            Ok(read)
        }
    }

    fn pending_handshake(
        role: ChildBootstrapRole,
        nonce_byte: u8,
    ) -> (
        PendingExpectationEnvelope,
        ParentPreparedChildBootstrapFrame,
        ParentExpectationSent,
    ) {
        let parent_frame = parent_frame(role);
        let frame_binding = *parent_frame.frame_binding_digest();
        let mut ready_bytes = Vec::new();
        let child_waiting = child_ready(role, nonce_byte)
            .write_to(&mut ready_bytes)
            .unwrap();
        let received_ready = ReceivedChildReady::read_from(&mut Cursor::new(&ready_bytes)).unwrap();
        let envelope = PreparedExpectationEnvelope::prepare(
            &received_ready,
            AuthorityHandshakeNonce::from_fresh_bytes([nonce_byte.wrapping_add(0x50); 32]).unwrap(),
            &frame_binding,
            &parent_expectations(role, nonce_byte),
        )
        .unwrap();
        let mut envelope_bytes = Vec::new();
        let expectation_sent = envelope.write_to(&mut envelope_bytes).unwrap();
        let pending = child_waiting
            .read_expectation_from(&mut Cursor::new(&envelope_bytes))
            .unwrap();
        volatile_zero(&mut ready_bytes);
        volatile_zero(&mut envelope_bytes);
        (pending, parent_frame, expectation_sent)
    }

    fn valid_ack_wire(role: ChildBootstrapRole, nonce_byte: u8) -> Vec<u8> {
        let (pending, parent_frame, _sent) = pending_handshake(role, nonce_byte);
        let witness = pending.peer_witness_for_test();
        let authenticated = pending.authenticate(witness).unwrap();
        let mut frame_bytes = Vec::new();
        let _parent_awaiting = parent_frame.write_complete_to(&mut frame_bytes).unwrap();
        let child_validated = authenticated
            .read_and_validate_bootstrap(&mut Cursor::new(&frame_bytes))
            .unwrap();
        let prepared_ack = child_validated.prepare_ack().unwrap();
        let bytes = prepared_ack.as_bytes_for_test().to_vec();
        volatile_zero(&mut frame_bytes);
        bytes
    }

    fn ack_exchange(
        role: ChildBootstrapRole,
        nonce_byte: u8,
    ) -> (ParentAwaitingBootstrapAck, ParentExpectationSent, Vec<u8>) {
        let (pending, parent_frame, sent) = pending_handshake(role, nonce_byte);
        let witness = pending.peer_witness_for_test();
        let authenticated = pending.authenticate(witness).unwrap();
        let mut frame_bytes = Vec::new();
        let awaiting_ack = parent_frame.write_complete_to(&mut frame_bytes).unwrap();
        let child_validated = authenticated
            .read_and_validate_bootstrap(&mut Cursor::new(&frame_bytes))
            .unwrap();
        let mut ack_bytes = Vec::new();
        child_validated
            .prepare_ack()
            .unwrap()
            .write_to(&mut ack_bytes)
            .unwrap();
        volatile_zero(&mut frame_bytes);
        (awaiting_ack, sent, ack_bytes)
    }

    fn sensitive<const N: usize>(bytes: &[u8]) -> SensitiveBytes<N> {
        assert_eq!(bytes.len(), N);
        let mut value = SensitiveBytes::<N>::zeroed();
        value.as_mut_slice().copy_from_slice(bytes);
        value
    }

    #[test]
    fn canonical_raw_handle_list_binds_role_slot_order_and_exact_values() {
        assert_eq!(
            [
                ChildStandardHandlePurpose::BootstrapRead,
                ChildStandardHandlePurpose::PrivateControlDuplex,
                ChildStandardHandlePurpose::StructuredResultWrite,
            ]
            .map(ChildStandardHandlePurpose::access_contract)
            .map(|access| (
                access.readable(),
                access.writable(),
                access.metadata_readable(),
            )),
            [
                (true, false, false),
                (true, true, false),
                (false, true, true)
            ]
        );

        let handles = [0x101usize, 0x202, 0x303];
        let driver =
            RoleRawHandleListDigest::derive(ChildBootstrapRole::LifecycleDriver, &handles).unwrap();
        let bridge =
            RoleRawHandleListDigest::derive(ChildBootstrapRole::BridgeLauncher, &handles).unwrap();
        assert_ne!(driver.as_bytes(), bridge.as_bytes());

        let mut reordered = handles;
        reordered.swap(0, 1);
        assert_ne!(
            driver.as_bytes(),
            RoleRawHandleListDigest::derive(ChildBootstrapRole::LifecycleDriver, &reordered,)
                .unwrap()
                .as_bytes()
        );
        for invalid in [[0, 0x202, 0x303], [0x101, 0x101, 0x303]] {
            assert!(
                RoleRawHandleListDigest::derive(ChildBootstrapRole::LifecycleDriver, &invalid,)
                    .is_err()
            );
        }

        let slots = role_slots(ChildBootstrapRole::LifecycleDriver);
        let slot_handles = std::array::from_fn(|index| slots[index].raw_handle);
        let binding =
            RoleCapabilitySetBinding::derive_for_test(ChildBootstrapRole::LifecycleDriver, &slots)
                .unwrap();
        assert_eq!(
            binding.raw_handle_list_digest(),
            RoleRawHandleListDigest::derive(ChildBootstrapRole::LifecycleDriver, &slot_handles,)
                .unwrap()
                .as_bytes()
        );
    }

    fn frame(role: ChildBootstrapRole) -> EncodedChildBootstrapFrame {
        ValidatedChildBootstrap::new_for_test(role, bindings(role))
            .expect("fixed frame")
            .encode_for_test()
    }

    #[test]
    fn parent_rejects_unmatched_child_observation_before_encoding_expectations() {
        let role = ChildBootstrapRole::LifecycleDriver;
        let nonce_byte = 0x24;
        let mut ready_bytes = Vec::new();
        child_ready(role, nonce_byte)
            .write_to(&mut ready_bytes)
            .expect("ready write");
        let received_ready =
            ReceivedChildReady::read_from(&mut Cursor::new(&ready_bytes)).expect("ready parse");
        let parent_frame = parent_frame(role);
        let error = PreparedExpectationEnvelope::prepare(
            &received_ready,
            AuthorityHandshakeNonce::from_fresh_bytes([0x71; 32]).unwrap(),
            parent_frame.frame_binding_digest(),
            &parent_expectations(role, nonce_byte.wrapping_add(1)),
        )
        .expect_err("parent measurement mismatch must stop before an envelope exists");
        assert_eq!(error.code(), "child_expectation_ready_binding_invalid");
        volatile_zero(&mut ready_bytes);
    }

    #[test]
    fn authenticated_wire_round_trips_both_roles_with_chunked_io_and_no_flush() {
        ZEROIZED_DROP_MASK.store(0, Ordering::SeqCst);
        assert_eq!(CHILD_READY_MESSAGE_LEN, 176);
        assert_eq!(CHILD_EXPECTATION_ENVELOPE_LEN, 720);
        assert_eq!(CHILD_BOOTSTRAP_ACK_LEN, 240);
        assert_eq!(CHILD_HANDSHAKE_NONCE_LEN, 32);

        for (role, nonce_byte) in [
            (ChildBootstrapRole::LifecycleDriver, 0x21),
            (ChildBootstrapRole::BridgeLauncher, 0x31),
        ] {
            let prepared_frame = parent_frame(role);
            assert_eq!(prepared_frame.role(), role);
            assert_eq!(
                prepared_frame.as_bytes_for_test().len(),
                CHILD_BOOTSTRAP_FRAME_LEN
            );
            let frame_binding = *prepared_frame.frame_binding_digest();

            let mut ready_writer = ChunkedWriter::new(5, true);
            let prepared_ready = child_ready(role, nonce_byte);
            assert_eq!(
                prepared_ready.as_bytes_for_test().len(),
                CHILD_READY_MESSAGE_LEN
            );
            let child_waiting = prepared_ready
                .write_to(&mut ready_writer)
                .expect("ready write");
            assert_eq!(ready_writer.bytes.len(), CHILD_READY_MESSAGE_LEN);
            assert_eq!(ready_writer.flushes, 0);

            let mut ready_reader = ChunkedReader::new(ready_writer.bytes.clone(), 7, true);
            let received_ready =
                ReceivedChildReady::read_from(&mut ready_reader).expect("ready read");
            assert_eq!(received_ready.as_bytes_for_test(), ready_writer.bytes);
            let ready_challenge = received_ready.challenge();
            assert_eq!(ready_challenge.role(), role);
            assert_eq!(ready_challenge.child_nonce(), received_ready.child_nonce());
            assert_eq!(ready_challenge.raw_handle_list_digest().role(), role);
            assert_ne!(ready_challenge.role_semantic_digest(), &[0; 32]);
            assert_ne!(
                ready_challenge
                    .child_observation_context_digest()
                    .as_bytes(),
                &[0; 32]
            );
            assert_ne!(ready_challenge.ready_binding_digest(), &[0; 32]);

            let prepared_expectation = PreparedExpectationEnvelope::prepare(
                &received_ready,
                AuthorityHandshakeNonce::from_fresh_bytes([0x41 + role.wire_value(); 32]).unwrap(),
                &frame_binding,
                &parent_expectations(role, nonce_byte),
            )
            .expect("expectation prepare");
            assert_eq!(
                prepared_expectation.as_bytes_for_test().len(),
                CHILD_EXPECTATION_ENVELOPE_LEN
            );
            let mut expectation_writer = ChunkedWriter::new(11, true);
            let parent_expectation_sent = prepared_expectation
                .write_to(&mut expectation_writer)
                .expect("expectation write");
            assert_eq!(
                expectation_writer.bytes.len(),
                CHILD_EXPECTATION_ENVELOPE_LEN
            );
            assert_eq!(expectation_writer.flushes, 0);

            let mut expectation_reader =
                ChunkedReader::new(expectation_writer.bytes.clone(), 13, true);
            let pending = child_waiting
                .read_expectation_from(&mut expectation_reader)
                .expect("expectation parse remains pending");
            let peer_challenge = pending.peer_authentication_challenge();
            assert_eq!(peer_challenge.role(), role);
            assert_eq!(peer_challenge.child_nonce(), received_ready.child_nonce());
            assert_ne!(peer_challenge.authority_nonce().as_bytes(), &[0; 32]);
            assert_eq!(peer_challenge.raw_handle_list_digest().role(), role);
            assert_ne!(
                peer_challenge
                    .expected_child_observation_context()
                    .as_bytes(),
                &[0; 32]
            );
            assert_ne!(
                peer_challenge.final_generation_context().as_bytes(),
                &[0; 32]
            );
            assert_ne!(
                peer_challenge.child_transport_contract_context().as_bytes(),
                &[0; 32]
            );
            assert_ne!(peer_challenge.start_contract_context().as_bytes(), &[0; 32]);
            assert_ne!(
                peer_challenge.job_membership_epoch_context().as_bytes(),
                &[0; 32]
            );
            assert_ne!(peer_challenge.runner_token_context().as_bytes(), &[0; 32]);
            assert_ne!(peer_challenge.child_image_context().as_bytes(), &[0; 32]);
            assert_ne!(
                peer_challenge.minimal_environment_context().as_bytes(),
                &[0; 32]
            );
            assert_ne!(
                peer_challenge.control_server_identity_context().as_bytes(),
                &[0; 32]
            );
            assert_eq!(peer_challenge.frame_binding_digest(), &frame_binding);
            assert_ne!(peer_challenge.challenge_digest(), &[0; 32]);

            let witness = pending.peer_witness_for_test();
            let authenticated = pending
                .authenticate(witness)
                .expect("independent peer witness authenticates envelope");

            let mut bootstrap_writer = ChunkedWriter::new(17, true);
            let parent_awaiting_ack = prepared_frame
                .write_complete_to(&mut bootstrap_writer)
                .expect("one exact bootstrap write");
            assert_eq!(bootstrap_writer.bytes.len(), CHILD_BOOTSTRAP_FRAME_LEN);
            assert_eq!(bootstrap_writer.flushes, 0);
            let mut bootstrap_reader = ChunkedReader::new(bootstrap_writer.bytes.clone(), 19, true);
            let child_validated = authenticated
                .read_and_validate_bootstrap(&mut bootstrap_reader)
                .expect("authenticated expectation then exact frame and EOF");
            let prepared_ack = child_validated.prepare_ack().expect("prepare ACK");
            assert_eq!(
                prepared_ack.as_bytes_for_test().len(),
                CHILD_BOOTSTRAP_ACK_LEN
            );

            let mut ack_writer = ChunkedWriter::new(23, true);
            let validated = prepared_ack.write_to(&mut ack_writer).expect("ACK write");
            assert_eq!(ack_writer.bytes.len(), CHILD_BOOTSTRAP_ACK_LEN);
            assert_eq!(ack_writer.flushes, 0);
            assert!(validated
                .bindings()
                .private_control_capability_for_test()
                .bytes_for_test()
                .iter()
                .all(|byte| *byte == 0));

            let mut ack_reader = ChunkedReader::new(ack_writer.bytes.clone(), 29, true);
            let received_ack = ReceivedBootstrapAck::read_from(&mut ack_reader).expect("ACK read");
            parent_awaiting_ack
                .verify_ack(parent_expectation_sent, received_ack)
                .expect("one-use ACK verification");

            for bytes in [
                &mut ready_writer.bytes,
                &mut expectation_writer.bytes,
                &mut bootstrap_writer.bytes,
                &mut ack_writer.bytes,
            ] {
                volatile_zero(bytes);
            }
        }
        let expected_drop_mask = READY_MESSAGE_DROP_BIT
            | EXPECTATION_MESSAGE_DROP_BIT
            | ACK_MESSAGE_DROP_BIT
            | ACK_KEY_DROP_BIT;
        assert_eq!(
            ZEROIZED_DROP_MASK.load(Ordering::SeqCst) & expected_drop_mask,
            expected_drop_mask
        );
    }

    #[test]
    fn every_control_header_and_structural_binding_is_fixed_and_fail_closed() {
        let role = ChildBootstrapRole::LifecycleDriver;
        let nonce_byte = 0x24;
        let ready_template = child_ready(role, nonce_byte).as_bytes_for_test().to_vec();
        for (offset, replacement, error) in [
            (0usize, 0u8, "child_ready_magic_invalid"),
            (9, 1, "child_ready_version_invalid"),
            (10, 0, "child_ready_role_invalid"),
            (11, 1, "child_ready_reserved_invalid"),
            (13, 0, "child_ready_length_invalid"),
            (14, 1, "child_ready_reserved_invalid"),
        ] {
            let mut hostile = ready_template.clone();
            hostile[offset] = replacement;
            assert_eq!(
                ReceivedChildReady::read_from(&mut Cursor::new(&hostile))
                    .expect_err("ready header drift rejected")
                    .code(),
                error
            );
            volatile_zero(&mut hostile);
        }
        let mut ready_binding_tamper = ready_template.clone();
        ready_binding_tamper[CHILD_READY_BINDING_OFFSET] ^= 0x80;
        assert_eq!(
            ReceivedChildReady::read_from(&mut Cursor::new(&ready_binding_tamper))
                .unwrap_err()
                .code(),
            "child_ready_binding_invalid"
        );

        let received_ready =
            ReceivedChildReady::read_from(&mut Cursor::new(&ready_template)).unwrap();
        let parent_frame = parent_frame(role);
        let envelope = PreparedExpectationEnvelope::prepare(
            &received_ready,
            AuthorityHandshakeNonce::from_fresh_bytes([0x74; 32]).unwrap(),
            parent_frame.frame_binding_digest(),
            &parent_expectations(role, nonce_byte),
        )
        .unwrap();
        let envelope_template = envelope.as_bytes_for_test().to_vec();
        drop(envelope);
        for (offset, replacement, error) in [
            (0usize, 0u8, "child_expectation_magic_invalid"),
            (9, 1, "child_expectation_version_invalid"),
            (10, 0, "child_expectation_role_invalid"),
            (11, 1, "child_expectation_reserved_invalid"),
            (13, 0, "child_expectation_length_invalid"),
            (15, 1, "child_expectation_reserved_invalid"),
        ] {
            let mut hostile = envelope_template.clone();
            hostile[offset] = replacement;
            assert_eq!(
                parse_expectation_envelope(
                    child_ready(role, nonce_byte),
                    sensitive::<CHILD_EXPECTATION_ENVELOPE_LEN>(&hostile),
                )
                .expect_err("expectation header drift rejected")
                .code(),
                error
            );
            volatile_zero(&mut hostile);
        }
        let mut envelope_binding_tamper = envelope_template.clone();
        envelope_binding_tamper[CHILD_EXPECTATION_BINDING_OFFSET] ^= 0x80;
        assert_eq!(
            parse_expectation_envelope(
                child_ready(role, nonce_byte),
                sensitive::<CHILD_EXPECTATION_ENVELOPE_LEN>(&envelope_binding_tamper),
            )
            .unwrap_err()
            .code(),
            "child_expectation_binding_invalid"
        );

        let ack_template = valid_ack_wire(role, nonce_byte);
        for (offset, replacement, error) in [
            (0usize, 0u8, "child_bootstrap_ack_magic_invalid"),
            (9, 1, "child_bootstrap_ack_version_invalid"),
            (10, 0, "child_bootstrap_ack_role_invalid"),
            (11, 1, "child_bootstrap_ack_reserved_invalid"),
            (13, 0, "child_bootstrap_ack_length_invalid"),
            (14, 1, "child_bootstrap_ack_reserved_invalid"),
        ] {
            let mut hostile = ack_template.clone();
            hostile[offset] = replacement;
            assert_eq!(
                ReceivedBootstrapAck::read_from(&mut Cursor::new(&hostile))
                    .expect_err("ACK header drift rejected")
                    .code(),
                error
            );
            volatile_zero(&mut hostile);
        }

        for (short, expected) in [
            (
                &ready_template[..CHILD_READY_MESSAGE_LEN - 1],
                "child_ready_length_invalid",
            ),
            (
                &ack_template[..CHILD_BOOTSTRAP_ACK_LEN - 1],
                "child_bootstrap_ack_length_invalid",
            ),
        ] {
            let observed = if short.len() == CHILD_READY_MESSAGE_LEN - 1 {
                ReceivedChildReady::read_from(&mut Cursor::new(short)).unwrap_err()
            } else {
                ReceivedBootstrapAck::read_from(&mut Cursor::new(short)).unwrap_err()
            };
            assert_eq!(observed.code(), expected);
        }
        let short_envelope = &envelope_template[..CHILD_EXPECTATION_ENVELOPE_LEN - 1];
        let mut sink = Vec::new();
        let waiting = child_ready(role, nonce_byte).write_to(&mut sink).unwrap();
        assert_eq!(
            waiting
                .read_expectation_from(&mut Cursor::new(short_envelope))
                .unwrap_err()
                .code(),
            "child_expectation_length_invalid"
        );

        for mut bytes in [
            ready_template,
            ready_binding_tamper,
            envelope_template,
            envelope_binding_tamper,
            ack_template,
        ] {
            volatile_zero(&mut bytes);
        }
    }

    #[test]
    fn ack_rejects_nonce_transcript_context_mac_cross_role_and_replay_tamper() {
        let role = ChildBootstrapRole::LifecycleDriver;

        let (valid_waiting, valid_sent, mut valid_bytes) = ack_exchange(role, 0x25);
        let valid_ack = ReceivedBootstrapAck::read_from(&mut Cursor::new(&valid_bytes)).unwrap();
        valid_waiting.verify_ack(valid_sent, valid_ack).unwrap();

        for (field_index, expected_error) in [
            (0usize, "child_bootstrap_ack_transcript_unexpected"),
            (4, "child_bootstrap_ack_transcript_unexpected"),
            (5, "child_bootstrap_ack_transcript_unexpected"),
        ] {
            let (waiting, sent, mut bytes) = ack_exchange(role, 0x26 + field_index as u8);
            bytes[CHILD_CONTROL_MESSAGE_HEADER_LEN + field_index * CHILD_BOOTSTRAP_DIGEST_LEN] ^=
                0x80;
            let ack = ReceivedBootstrapAck::read_from(&mut Cursor::new(&bytes)).unwrap();
            assert_eq!(
                waiting
                    .verify_ack(sent, ack)
                    .expect_err("bound ACK field tamper rejected")
                    .code(),
                expected_error
            );
            volatile_zero(&mut bytes);
        }

        let (mac_waiting, mac_sent, mut mac_bytes) = ack_exchange(role, 0x35);
        mac_bytes[CHILD_BOOTSTRAP_ACK_MAC_OFFSET] ^= 0x80;
        let mac_ack = ReceivedBootstrapAck::read_from(&mut Cursor::new(&mac_bytes)).unwrap();
        assert_eq!(
            mac_waiting
                .verify_ack(mac_sent, mac_ack)
                .expect_err("ACK MAC tamper rejected")
                .code(),
            "child_bootstrap_ack_mac_invalid"
        );

        let (driver_waiting, _driver_sent, mut driver_bytes) = ack_exchange(role, 0x36);
        let (_bridge_waiting, bridge_sent, mut bridge_bytes) =
            ack_exchange(ChildBootstrapRole::BridgeLauncher, 0x36);
        let bridge_ack = ReceivedBootstrapAck::read_from(&mut Cursor::new(&bridge_bytes)).unwrap();
        assert_eq!(
            driver_waiting
                .verify_ack(bridge_sent, bridge_ack)
                .expect_err("cross-role ACK rejected")
                .code(),
            "child_bootstrap_ack_transcript_unexpected"
        );

        let (_old_waiting, _old_sent, mut old_bytes) = ack_exchange(role, 0x37);
        let (fresh_waiting, fresh_sent, mut fresh_bytes) = ack_exchange(role, 0x38);
        let old_ack = ReceivedBootstrapAck::read_from(&mut Cursor::new(&old_bytes)).unwrap();
        assert_eq!(
            fresh_waiting
                .verify_ack(fresh_sent, old_ack)
                .expect_err("prior transcript ACK replay rejected")
                .code(),
            "child_bootstrap_ack_transcript_unexpected"
        );

        for bytes in [
            &mut valid_bytes,
            &mut mac_bytes,
            &mut driver_bytes,
            &mut bridge_bytes,
            &mut old_bytes,
            &mut fresh_bytes,
        ] {
            volatile_zero(bytes);
        }
    }

    #[test]
    fn expectation_authentication_requires_the_complete_external_challenge() {
        let role = ChildBootstrapRole::LifecycleDriver;
        let (pending, _frame, _sent) = pending_handshake(role, 0x28);
        assert!(format!("{pending:?}").contains("unauthenticated"));
        let canonical = pending.peer_challenge_fields_for_test();
        let canonical_refs: [&BootstrapDigest; 22] = std::array::from_fn(|index| &canonical[index]);
        assert_eq!(
            derive_peer_authentication_challenge(role, &canonical_refs).unwrap(),
            *pending.peer_authentication_challenge().challenge_digest()
        );

        for omitted_index in 0..canonical.len() {
            let mut omitted = canonical;
            omitted[omitted_index].fill(0);
            let omitted_refs: [&BootstrapDigest; 22] = std::array::from_fn(|index| &omitted[index]);
            assert_eq!(
                derive_peer_authentication_challenge(role, &omitted_refs)
                    .expect_err("omitting any challenge field is invalid")
                    .code(),
                "child_expectation_peer_challenge_invalid"
            );
            for value in &mut omitted {
                volatile_zero(value);
            }
        }

        let mut substituted = canonical;
        substituted[7][0] ^= 0x80;
        let substituted_refs: [&BootstrapDigest; 22] =
            std::array::from_fn(|index| &substituted[index]);
        let hostile_witness = AuthenticatedControlPeerWitness {
            role,
            challenge_digest: derive_peer_authentication_challenge(role, &substituted_refs)
                .unwrap(),
            _private: (),
        };
        assert_eq!(
            pending
                .authenticate(hostile_witness)
                .expect_err("partial or substituted peer challenge cannot self-authorize")
                .code(),
            "child_expectation_peer_witness_unexpected"
        );

        let (old_pending, _old_frame, _old_sent) = pending_handshake(role, 0x29);
        let stale_witness = old_pending.peer_witness_for_test();
        let (fresh_pending, _fresh_frame, _fresh_sent) = pending_handshake(role, 0x2A);
        assert_eq!(
            fresh_pending
                .authenticate(stale_witness)
                .expect_err("fresh ready nonce rejects prior envelope witness")
                .code(),
            "child_expectation_peer_witness_unexpected"
        );

        let (driver_pending, _driver_frame, _driver_sent) = pending_handshake(role, 0x2B);
        let driver_witness = driver_pending.peer_witness_for_test();
        let (bridge_pending, _bridge_frame, _bridge_sent) =
            pending_handshake(ChildBootstrapRole::BridgeLauncher, 0x2B);
        assert_eq!(
            bridge_pending
                .authenticate(driver_witness)
                .expect_err("cross-role expectation witness rejected")
                .code(),
            "child_expectation_peer_witness_unexpected"
        );

        let mut canonical = canonical;
        let mut substituted = substituted;
        for value in &mut canonical {
            volatile_zero(value);
        }
        for value in &mut substituted {
            volatile_zero(value);
        }
    }

    fn resign(frame: &mut EncodedChildBootstrapFrame) {
        let digest = frame_binding_digest(&frame.as_bytes()[..CHILD_BOOTSTRAP_BINDING_OFFSET]);
        frame.as_mut_bytes_for_test()[CHILD_BOOTSTRAP_BINDING_OFFSET..].copy_from_slice(&digest);
    }

    fn field_start(index: usize) -> usize {
        CHILD_BOOTSTRAP_HEADER_LEN + index * CHILD_BOOTSTRAP_FIELD_WIRE_LEN
    }

    fn field_digest_start(index: usize) -> usize {
        field_start(index) + CHILD_BOOTSTRAP_FIELD_HEADER_LEN
    }

    #[test]
    fn exact_frame_round_trips_all_typed_bindings_for_each_role() {
        for role in [
            ChildBootstrapRole::LifecycleDriver,
            ChildBootstrapRole::BridgeLauncher,
        ] {
            let expected = expectations(role);
            let bytes = frame(role);
            assert_eq!(bytes.as_bytes().len(), CHILD_BOOTSTRAP_FRAME_LEN);
            let decoded =
                ValidatedChildBootstrap::decode_for_role(bytes.as_bytes(), role, &expected)
                    .expect("valid frame");
            assert_eq!(decoded.role(), role);
            assert_eq!(
                decoded.transport_slot(),
                ChildBootstrapTransportSlot::fixed()
            );
            assert_eq!(decoded.bindings().authority_digest(), &authority());
            assert_eq!(decoded.bindings().ticket_digest(), &ticket());
            assert_eq!(decoded.bindings().run_digest(), &run_binding());
            assert_eq!(decoded.bindings().policy_digest(), &policy());
            assert_eq!(decoded.bindings().global_capability_set_digest(), &global());
            assert_eq!(
                decoded.bindings().role_capability_set(),
                &role_capability_set(role)
            );
            let mut expected_private_source = [0x77; 32];
            let expected_private =
                PrivateControlCapability::take_from(&mut expected_private_source).unwrap();
            assert_eq!(
                decoded
                    .bindings()
                    .private_control_capability_for_test()
                    .bytes_for_test(),
                expected_private.bytes_for_test()
            );
            assert_ne!(decoded.frame_binding_digest(), &[0; 32]);
            let reencoded = decoded.encode_for_test();
            assert_eq!(reencoded.as_bytes(), bytes.as_bytes());
        }
    }

    #[test]
    fn pending_validation_exposes_only_the_authenticated_lookup_material() {
        let encoded = frame(ChildBootstrapRole::LifecycleDriver);
        let mut reader = Cursor::new(encoded.as_bytes().as_slice());
        let transport = ChildBootstrapTransportFrame::read_complete_from(
            ChildBootstrapTransportSlot::fixed(),
            &mut reader,
        )
        .expect("complete transport");
        let pending = transport
            .begin_validation(ChildBootstrapRole::LifecycleDriver)
            .expect("structurally valid pending frame");

        assert_eq!(pending.role(), ChildBootstrapRole::LifecycleDriver);
        assert_eq!(
            pending.transport_slot(),
            ChildBootstrapTransportSlot::fixed()
        );
        assert_ne!(pending.frame_binding_digest(), &[0; 32]);
        assert_eq!(
            pending
                .private_control_capability_for_test()
                .bytes_for_test(),
            &[0x77; 32]
        );
        let debug = format!("{pending:?}");
        assert!(debug.contains("<redacted>"));
        assert!(!debug.contains("119"));

        let validated = pending
            .validate(&expectations(ChildBootstrapRole::LifecycleDriver))
            .expect("independent expectations complete validation");
        assert_eq!(validated.role(), ChildBootstrapRole::LifecycleDriver);
    }

    #[test]
    fn independently_committed_private_control_rejects_a_resigned_secret_substitution() {
        let role = ChildBootstrapRole::LifecycleDriver;
        let mut hostile = frame(role);
        hostile.as_mut_bytes_for_test()
            [field_digest_start(7)..field_digest_start(7) + CHILD_BOOTSTRAP_DIGEST_LEN]
            .copy_from_slice(&[0x78; CHILD_BOOTSTRAP_DIGEST_LEN]);
        resign(&mut hostile);

        let mut reader = Cursor::new(hostile.as_bytes().as_slice());
        let transport = ChildBootstrapTransportFrame::read_complete_from(
            ChildBootstrapTransportSlot::fixed(),
            &mut reader,
        )
        .expect("complete hostile transport");
        let pending = transport
            .begin_validation(role)
            .expect("self-consistent frame remains pending only");
        assert_eq!(
            pending
                .private_control_capability_for_test()
                .bytes_for_test(),
            &[0x78; 32]
        );
        assert_eq!(
            pending
                .validate(&expectations(role))
                .expect_err("service commitment must bind the exact bearer secret")
                .code(),
            "child_bootstrap_private_control_commitment_unexpected"
        );
    }

    #[test]
    fn binding_derivation_domains_and_roles_are_distinct() {
        let source = [0x61; 32];
        let mut private_source = source;
        let values = [
            *AuthorityBindingDigest::derive(&source).unwrap().as_bytes(),
            *TicketBindingDigest::derive(&source).unwrap().as_bytes(),
            *RunBindingDigest::derive(&source).unwrap().as_bytes(),
            *PolicyBindingDigest::derive(&source).unwrap().as_bytes(),
            *PrivateControlCapability::take_from(&mut private_source)
                .unwrap()
                .bytes_for_test(),
        ];
        validate_distinct_nonzero_digests(&values, "unexpected").unwrap();

        let driver = role_capability_set(ChildBootstrapRole::LifecycleDriver);
        let bridge = role_capability_set(ChildBootstrapRole::BridgeLauncher);
        assert_ne!(driver.semantic_digest(), bridge.semantic_digest());
        assert_ne!(
            driver.raw_handle_list_digest(),
            bridge.raw_handle_list_digest()
        );
        assert_eq!(driver.slot_count(), 3);
        assert_eq!(bridge.slot_count(), 3);
    }

    #[test]
    fn fixed_role_schema_rejects_count_mapping_and_handle_mismatch() {
        let role = ChildBootstrapRole::LifecycleDriver;
        let slots = role_slots(role);
        assert_eq!(
            RoleCapabilitySetBinding::derive_for_test(role, &slots[..2])
                .expect_err("short schema rejected")
                .code(),
            "child_bootstrap_role_capability_count_invalid"
        );

        let mut reordered = slots.clone();
        reordered.swap(0, 1);
        assert_eq!(
            RoleCapabilitySetBinding::derive_for_test(role, &reordered)
                .expect_err("mapping permutation rejected")
                .code(),
            "child_bootstrap_role_capability_mapping_invalid"
        );

        let mut wrong_role = slots.clone();
        wrong_role[0] = role_slots(ChildBootstrapRole::BridgeLauncher)[0];
        assert_eq!(
            RoleCapabilitySetBinding::derive_for_test(role, &wrong_role)
                .expect_err("cross-role mapping rejected")
                .code(),
            "child_bootstrap_role_capability_mapping_invalid"
        );

        let mut duplicate_digest = slots.clone();
        duplicate_digest[2].capability_digest = duplicate_digest[0].capability_digest;
        assert_eq!(
            RoleCapabilitySetBinding::derive_for_test(role, &duplicate_digest)
                .expect_err("duplicate semantic capability rejected")
                .code(),
            "child_bootstrap_role_capability_slot_invalid"
        );

        let mut duplicate_handle = slots.clone();
        duplicate_handle[2].raw_handle = duplicate_handle[0].raw_handle;
        assert!(RoleCapabilitySetBinding::derive_for_test(role, &duplicate_handle).is_err());

        let mut invalid_handle = slots;
        invalid_handle[1].raw_handle = usize::MAX;
        assert!(RoleCapabilitySetBinding::derive_for_test(role, &invalid_handle).is_err());
    }

    #[test]
    fn transport_owns_eof_proof_and_rejects_337th_byte() {
        let encoded = frame(ChildBootstrapRole::LifecycleDriver);
        let mut exact = Cursor::new(encoded.as_bytes().as_slice());
        let transport = ChildBootstrapTransportFrame::read_complete_from(
            ChildBootstrapTransportSlot::fixed(),
            &mut exact,
        )
        .expect("exact frame plus EOF");
        assert_eq!(transport.slot(), ChildBootstrapTransportSlot::fixed());
        assert!(transport
            .decode_for_role(
                ChildBootstrapRole::LifecycleDriver,
                &expectations(ChildBootstrapRole::LifecycleDriver),
            )
            .is_ok());

        let mut short = Cursor::new(&encoded.as_bytes()[..CHILD_BOOTSTRAP_FRAME_LEN - 1]);
        assert_eq!(
            ChildBootstrapTransportFrame::read_complete_from(
                ChildBootstrapTransportSlot::fixed(),
                &mut short,
            )
            .expect_err("short transport rejected")
            .code(),
            "child_bootstrap_transport_length_invalid"
        );

        let mut trailing = encoded.as_bytes().to_vec();
        trailing.push(0);
        let mut trailing_reader = Cursor::new(&trailing);
        assert_eq!(
            ChildBootstrapTransportFrame::read_complete_from(
                ChildBootstrapTransportSlot::fixed(),
                &mut trailing_reader,
            )
            .expect_err("337th byte rejected")
            .code(),
            "child_bootstrap_transport_length_invalid"
        );
        volatile_zero(&mut trailing);
    }

    struct ErrorInsteadOfEof {
        bytes: Vec<u8>,
        offset: usize,
    }

    impl Read for ErrorInsteadOfEof {
        fn read(&mut self, target: &mut [u8]) -> io::Result<usize> {
            if self.offset == self.bytes.len() {
                return Err(io::Error::new(io::ErrorKind::Other, "no EOF witness"));
            }
            let read = target.len().min(7).min(self.bytes.len() - self.offset);
            target[..read].copy_from_slice(&self.bytes[self.offset..self.offset + read]);
            self.offset += read;
            Ok(read)
        }
    }

    #[test]
    fn transport_requires_observed_eof_after_fragmented_frame() {
        let encoded = frame(ChildBootstrapRole::BridgeLauncher);
        let mut reader = ErrorInsteadOfEof {
            bytes: encoded.as_bytes().to_vec(),
            offset: 0,
        };
        assert_eq!(
            ChildBootstrapTransportFrame::read_complete_from(
                ChildBootstrapTransportSlot::fixed(),
                &mut reader,
            )
            .expect_err("read error is not EOF")
            .code(),
            "child_bootstrap_transport_read_failed"
        );
        volatile_zero(&mut reader.bytes);
    }

    #[test]
    fn cross_field_value_exchange_is_rejected_even_when_resigned() {
        let role = ChildBootstrapRole::LifecycleDriver;
        let mut hostile = frame(role);
        let authority = hostile.as_bytes()
            [field_digest_start(0)..field_digest_start(0) + CHILD_BOOTSTRAP_DIGEST_LEN]
            .to_vec();
        let ticket = hostile.as_bytes()
            [field_digest_start(1)..field_digest_start(1) + CHILD_BOOTSTRAP_DIGEST_LEN]
            .to_vec();
        hostile.as_mut_bytes_for_test()
            [field_digest_start(0)..field_digest_start(0) + CHILD_BOOTSTRAP_DIGEST_LEN]
            .copy_from_slice(&ticket);
        hostile.as_mut_bytes_for_test()
            [field_digest_start(1)..field_digest_start(1) + CHILD_BOOTSTRAP_DIGEST_LEN]
            .copy_from_slice(&authority);
        resign(&mut hostile);
        assert_eq!(
            ValidatedChildBootstrap::decode_for_role(
                hostile.as_bytes(),
                role,
                &expectations(role),
            )
            .expect_err("cross-field values rejected")
            .code(),
            "child_bootstrap_authority_binding_unexpected"
        );
    }

    #[test]
    fn cross_role_capability_transplant_is_rejected_even_when_resigned() {
        let mut hostile = frame(ChildBootstrapRole::LifecycleDriver);
        hostile.as_mut_bytes_for_test()[10] = ChildBootstrapRole::BridgeLauncher.wire_value();
        resign(&mut hostile);
        assert_eq!(
            ValidatedChildBootstrap::decode_for_role(
                hostile.as_bytes(),
                ChildBootstrapRole::BridgeLauncher,
                &expectations(ChildBootstrapRole::BridgeLauncher),
            )
            .expect_err("driver capability aggregate cannot become bridge aggregate")
            .code(),
            "child_bootstrap_role_capability_semantics_unexpected"
        );
    }

    #[test]
    fn frame_rejects_header_semantic_reserved_and_binding_tamper() {
        let role = ChildBootstrapRole::LifecycleDriver;
        let baseline = frame(role);
        let cases = [
            (0usize, 0u8, "child_bootstrap_magic_invalid"),
            (9, 1, "child_bootstrap_version_invalid"),
            (11, 0, "child_bootstrap_transport_slot_invalid"),
            (13, 0, "child_bootstrap_length_invalid"),
            (14, 1, "child_bootstrap_reserved_invalid"),
            (15, 1, "child_bootstrap_reserved_invalid"),
        ];
        for (offset, replacement, expected_error) in cases {
            let mut hostile = frame(role);
            hostile.as_mut_bytes_for_test()[offset] = replacement;
            assert_eq!(
                ValidatedChildBootstrap::decode_for_role(
                    hostile.as_bytes(),
                    role,
                    &expectations(role),
                )
                .expect_err("header drift rejected")
                .code(),
                expected_error
            );
        }

        let mut semantic = frame(role);
        semantic.as_mut_bytes_for_test()[field_start(5) + 1] = 0;
        resign(&mut semantic);
        assert_eq!(
            ValidatedChildBootstrap::decode_for_role(
                semantic.as_bytes(),
                role,
                &expectations(role),
            )
            .expect_err("semantic tag drift rejected")
            .code(),
            "child_bootstrap_field_semantics_invalid"
        );

        let mut reserved = frame(role);
        reserved.as_mut_bytes_for_test()[field_start(3) + 2] = 1;
        resign(&mut reserved);
        assert_eq!(
            ValidatedChildBootstrap::decode_for_role(
                reserved.as_bytes(),
                role,
                &expectations(role),
            )
            .expect_err("field reserved byte rejected")
            .code(),
            "child_bootstrap_field_reserved_invalid"
        );

        let mut tampered = frame(role);
        tampered.as_mut_bytes_for_test()[field_digest_start(3)] ^= 0x80;
        assert_eq!(
            ValidatedChildBootstrap::decode_for_role(
                tampered.as_bytes(),
                role,
                &expectations(role),
            )
            .expect_err("unresigned field tamper rejected")
            .code(),
            "child_bootstrap_binding_invalid"
        );
        assert_eq!(baseline.as_bytes().len(), CHILD_BOOTSTRAP_FRAME_LEN);
    }

    #[test]
    fn constructors_reject_zero_wrong_role_and_reused_values() {
        assert!(AuthorityBindingDigest::derive(&[0; 32]).is_err());
        assert!(TicketBindingDigest::derive(&[0; 32]).is_err());
        assert!(RunBindingDigest::derive(&[0; 32]).is_err());
        assert!(PolicyBindingDigest::derive(&[0; 32]).is_err());
        let mut zero_private = [0; 32];
        assert!(PrivateControlCapability::take_from(&mut zero_private).is_err());
        assert!(zero_private.iter().all(|byte| *byte == 0));

        assert_eq!(
            ValidatedChildBootstrap::new_for_test(
                ChildBootstrapRole::LifecycleDriver,
                bindings(ChildBootstrapRole::BridgeLauncher),
            )
            .expect_err("wrong role set rejected")
            .code(),
            "child_bootstrap_role_capability_set_mismatch"
        );

        let authority = authority();
        let reused_ticket = TicketBindingDigest::from_wire(*authority.as_bytes()).unwrap();
        assert_eq!(
            ChildBootstrapBindings::new(
                authority,
                reused_ticket,
                run_binding(),
                policy(),
                global(),
                role_capability_set(ChildBootstrapRole::LifecycleDriver),
                private_control(),
            )
            .expect_err("cross-field value reuse rejected")
            .code(),
            "child_bootstrap_binding_value_reused"
        );
    }

    #[test]
    fn wrong_expectation_role_fails_before_frame_use() {
        let driver = frame(ChildBootstrapRole::LifecycleDriver);
        assert_eq!(
            ValidatedChildBootstrap::decode_for_role(
                driver.as_bytes(),
                ChildBootstrapRole::LifecycleDriver,
                &expectations(ChildBootstrapRole::BridgeLauncher),
            )
            .expect_err("expectation role mismatch rejected")
            .code(),
            "child_bootstrap_expectation_role_mismatch"
        );
    }

    #[test]
    fn secret_and_frame_zeroization_paths_clear_bytes() {
        assert!(mem::needs_drop::<PrivateControlCapability>());
        assert!(mem::needs_drop::<EncodedChildBootstrapFrame>());
        assert!(mem::needs_drop::<ChildBootstrapTransportFrame>());

        ZEROIZED_DROP_MASK.store(0, Ordering::SeqCst);
        drop(private_control());
        drop(frame(ChildBootstrapRole::LifecycleDriver));
        let drop_frame = frame(ChildBootstrapRole::LifecycleDriver);
        let mut drop_reader = Cursor::new(drop_frame.as_bytes().as_slice());
        let drop_transport = ChildBootstrapTransportFrame::read_complete_from(
            ChildBootstrapTransportSlot::fixed(),
            &mut drop_reader,
        )
        .unwrap();
        drop(drop_transport);
        assert_eq!(ZEROIZED_DROP_MASK.load(Ordering::SeqCst) & 0b111, 0b111);

        let mut secret = private_control();
        assert!(secret.bytes_for_test().iter().any(|byte| *byte != 0));
        secret.zeroize_for_test();
        assert!(secret.bytes_for_test().iter().all(|byte| *byte == 0));

        let mut encoded = frame(ChildBootstrapRole::LifecycleDriver);
        encoded.zeroize_for_test();
        assert!(encoded.as_bytes().iter().all(|byte| *byte == 0));

        let fresh = frame(ChildBootstrapRole::LifecycleDriver);
        let mut cursor = Cursor::new(fresh.as_bytes().as_slice());
        let mut transport = ChildBootstrapTransportFrame::read_complete_from(
            ChildBootstrapTransportSlot::fixed(),
            &mut cursor,
        )
        .unwrap();
        transport.zeroize_for_test();
        assert!(transport.bytes.iter().all(|byte| *byte == 0));

        let mut scratch = [0xA5; 64];
        volatile_zero(&mut scratch);
        assert!(scratch.iter().all(|byte| *byte == 0));

        let mut ack_wire = BootstrapAckWire::zeroed();
        ack_wire.as_mut_slice().fill(0xA5);
        ack_wire.zeroize_for_test();
        assert!(ack_wire.as_slice().iter().all(|byte| *byte == 0));
    }

    #[test]
    fn debug_output_redacts_every_frame_and_secret_value() {
        let role = ChildBootstrapRole::LifecycleDriver;
        let encoded = frame(role);
        let decoded =
            ValidatedChildBootstrap::decode_for_role(encoded.as_bytes(), role, &expectations(role))
                .expect("valid fixed frame");
        let debug = format!("{decoded:?}");
        assert!(debug.contains("<redacted>"));
        assert!(!debug.contains("119"));

        let mut cursor = Cursor::new(encoded.as_bytes().as_slice());
        let transport = ChildBootstrapTransportFrame::read_complete_from(
            ChildBootstrapTransportSlot::fixed(),
            &mut cursor,
        )
        .expect("transport frame");
        let transport_debug = format!("{transport:?}");
        assert!(transport_debug.contains("<redacted>"));
        assert!(!transport_debug.contains("119"));

        let encoded_debug = format!("{encoded:?}");
        assert_eq!(encoded_debug, "EncodedChildBootstrapFrame(<redacted>)");
    }
}
