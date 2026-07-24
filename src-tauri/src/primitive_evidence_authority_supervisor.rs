use sha2::{Digest as Sha2Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
};

pub const APP_LOOPBACK_PORT: u16 = 8757;
pub const BRIDGE_LOOPBACK_PORT: u16 = 8080;
const MAX_CANONICAL_RESULT_BYTES: usize = 64 * 1024;
const CLEANUP_RECEIPT_DOMAIN: &[u8] = b"vrcforge-authority-cleanup-receipt-v1\0";
const READINESS_PROOF_DOMAIN: &[u8] = b"vrcforge-authority-readiness-proof-v2\0";
const SUPERVISOR_POLICY_DOMAIN: &[u8] = b"vrcforge-authority-supervisor-policy-v2\0";
const RUNTIME_RUN_BINDING_DOMAIN: &[u8] = b"vrcforge-authority-runtime-run-binding-v2\0";
const PREPARED_RECEIPT_DOMAIN: &[u8] = b"vrcforge-authority-prepared-receipt-v1\0";
const ARMED_RECEIPT_DOMAIN: &[u8] = b"vrcforge-authority-armed-receipt-v1\0";
const ARTIFACT_POLICY_DOMAIN: &[u8] = b"vrcforge-authority-artifact-policy-v1\0";
const ENDPOINT_POLICY_DOMAIN: &[u8] = b"vrcforge-authority-endpoint-policy-v1\0";
const HELPER_POLICY_DOMAIN: &[u8] = b"vrcforge-authority-helper-policy-v1\0";
const PREPARED_RECEIPT_MAGIC: &[u8; 8] = b"VRCPRP01";
const ARMED_RECEIPT_MAGIC: &[u8; 8] = b"VRCARM01";
const POLICY_SNAPSHOT_MAGIC: &[u8; 8] = b"VRCPOL01";
const MAX_POLICY_ARTIFACTS: usize = 64;
const MAX_POLICY_ENDPOINTS: usize = 8;
const MAX_POLICY_HELPERS: usize = 64;
const MAX_HELPER_PARENT_DIGESTS: usize = 32;
const MAX_HELPER_EXIT_CODES: usize = 32;
const TEST_CLEANUP_RECEIPT_DOMAIN: &[u8] = b"vrcforge-authority-test-cleanup-receipt-v1\0";

const PRODUCTION_BLOCKERS: [&str; 6] = [
    "isolated_runner_identity_not_provisioned",
    "protected_process_launch_not_implemented",
    "service_owned_job_supervision_not_implemented",
    "observed_endpoint_supervision_not_implemented",
    "private_finalization_not_implemented",
    "supervised_cleanup_not_implemented",
];

const PROCESS_ROLES: [ProcessRole; 7] = [
    ProcessRole::AuthorityService,
    ProcessRole::Driver,
    ProcessRole::Desktop,
    ProcessRole::Backend,
    ProcessRole::Unity,
    ProcessRole::BridgeLauncher,
    ProcessRole::BridgeListener,
];

const CANDIDATE_ROLES: [ProcessRole; 6] = [
    ProcessRole::Driver,
    ProcessRole::Desktop,
    ProcessRole::Backend,
    ProcessRole::Unity,
    ProcessRole::BridgeLauncher,
    ProcessRole::BridgeListener,
];

const ROOT_ROLES: [ProcessRole; 1] = [ProcessRole::Driver];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SupervisorReadiness {
    trusted_boundary_ready: bool,
    blockers: &'static [&'static str],
}

impl SupervisorReadiness {
    pub fn trusted_boundary_ready(&self) -> bool {
        self.trusted_boundary_ready
    }

    pub fn blockers(&self) -> &'static [&'static str] {
        self.blockers
    }
}

pub fn production_readiness() -> SupervisorReadiness {
    SupervisorReadiness {
        trusted_boundary_ready: false,
        blockers: &PRODUCTION_BLOCKERS,
    }
}

// Readiness proves only the live authority instance. Per-run policy is sealed later, after the
// request ticket, deadline, private artifacts, and endpoint choices exist.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct VerifiedReadinessProof {
    authority_identity_digest: Digest,
    service_instance_digest: Digest,
    seal_digest: Digest,
}

impl VerifiedReadinessProof {
    #[cfg(test)]
    pub(crate) fn for_runtime_test(
        authority_identity_digest: Digest,
        service_instance_digest: Digest,
    ) -> Self {
        let seal_digest = readiness_seal(&authority_identity_digest, &service_instance_digest);
        Self {
            authority_identity_digest,
            service_instance_digest,
            seal_digest,
        }
    }

    pub(crate) fn verifies_for(&self, authority_identity_digest: &Digest) -> bool {
        self.authority_identity_digest == *authority_identity_digest
            && !is_zero_digest(&self.service_instance_digest)
            && self.seal_digest
                == readiness_seal(
                    &self.authority_identity_digest,
                    &self.service_instance_digest,
                )
    }

    pub(crate) fn service_instance_digest(&self) -> &Digest {
        &self.service_instance_digest
    }
}

fn readiness_seal(authority_identity_digest: &Digest, service_instance_digest: &Digest) -> Digest {
    let mut digest = Sha256::new();
    digest.update(READINESS_PROOF_DOMAIN);
    digest.update(authority_identity_digest);
    digest.update(service_instance_digest);
    digest.finalize().into()
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SupervisorError(&'static str);

impl SupervisorError {
    fn new(code: &'static str) -> Self {
        Self(code)
    }

    pub fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for SupervisorError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for SupervisorError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum ProcessRole {
    AuthorityService,
    Driver,
    Desktop,
    Backend,
    Unity,
    BridgeLauncher,
    BridgeListener,
}

impl ProcessRole {
    fn expected_parent(self) -> Option<Self> {
        match self {
            Self::AuthorityService => None,
            Self::Driver => Some(Self::AuthorityService),
            Self::Desktop | Self::Unity => Some(Self::Driver),
            Self::Backend => Some(Self::Desktop),
            Self::BridgeLauncher => Some(Self::Unity),
            Self::BridgeListener => Some(Self::BridgeLauncher),
        }
    }

    fn expected_supervisor(self) -> Option<Self> {
        self.expected_parent()
    }

    fn is_candidate(self) -> bool {
        self != Self::AuthorityService
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum SocketRole {
    App,
    Bridge,
}

impl SocketRole {
    fn expected_owner(self) -> ProcessRole {
        match self {
            Self::App => ProcessRole::Backend,
            Self::Bridge => ProcessRole::BridgeListener,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum ArtifactDirection {
    Input,
    Output,
}

pub(crate) type Digest = [u8; 32];

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct ProcessKey {
    pid: u32,
    creation_time: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct FileIdentity {
    volume_serial: u64,
    file_id: [u8; 16],
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct PreparedRecoveryReceipt {
    authority_identity_digest: Digest,
    ticket_digest: Digest,
    service_instance_digest: Digest,
    runner_policy_digest: Digest,
    deterministic_job_name_digest: Digest,
    private_root_binding_digest: Digest,
    artifact_policy_digest: Digest,
    endpoint_policy_digest: Digest,
    helper_policy_digest: Digest,
    inherited_handle_allowlist_digest: Digest,
    issued_at: u64,
    deadline: u64,
    job_object_id: u64,
    seal_digest: Digest,
}

impl PreparedRecoveryReceipt {
    fn from_policy(policy: &SupervisorPolicy) -> Self {
        let mut receipt = Self {
            authority_identity_digest: policy.authority_identity_digest,
            ticket_digest: policy.ticket_digest,
            service_instance_digest: policy.service_instance_digest,
            runner_policy_digest: policy.runner_policy_digest,
            deterministic_job_name_digest: policy.deterministic_job_name_digest,
            private_root_binding_digest: policy.private_root_binding_digest,
            artifact_policy_digest: artifact_policy_digest(&policy.artifacts),
            endpoint_policy_digest: endpoint_policy_digest(&policy.socket_policies),
            helper_policy_digest: helper_policy_digest(&policy.helper_policies),
            inherited_handle_allowlist_digest: policy.inherited_handle_allowlist_digest,
            issued_at: policy.issued_at,
            deadline: policy.deadline,
            job_object_id: policy.job_object_id,
            seal_digest: [0; 32],
        };
        receipt.seal_digest = prepared_receipt_seal(&receipt);
        receipt
    }

    pub(crate) fn decode(bytes: &[u8]) -> Result<Self, SupervisorError> {
        const DIGEST_COUNT: usize = 11;
        const ENCODED_LEN: usize = 8 + DIGEST_COUNT * 32 + 3 * 8;
        if bytes.len() != ENCODED_LEN || &bytes[..8] != PREPARED_RECEIPT_MAGIC {
            return Err(SupervisorError::new("authority_prepared_receipt_invalid"));
        }
        let mut offset = 8usize;
        let authority_identity_digest = take_digest(bytes, &mut offset)?;
        let ticket_digest = take_digest(bytes, &mut offset)?;
        let service_instance_digest = take_digest(bytes, &mut offset)?;
        let runner_policy_digest = take_digest(bytes, &mut offset)?;
        let deterministic_job_name_digest = take_digest(bytes, &mut offset)?;
        let private_root_binding_digest = take_digest(bytes, &mut offset)?;
        let artifact_policy_digest = take_digest(bytes, &mut offset)?;
        let endpoint_policy_digest = take_digest(bytes, &mut offset)?;
        let helper_policy_digest = take_digest(bytes, &mut offset)?;
        let inherited_handle_allowlist_digest = take_digest(bytes, &mut offset)?;
        let issued_at = take_u64(bytes, &mut offset)?;
        let deadline = take_u64(bytes, &mut offset)?;
        let job_object_id = take_u64(bytes, &mut offset)?;
        let seal_digest = take_digest(bytes, &mut offset)?;
        let receipt = Self {
            authority_identity_digest,
            ticket_digest,
            service_instance_digest,
            runner_policy_digest,
            deterministic_job_name_digest,
            private_root_binding_digest,
            artifact_policy_digest,
            endpoint_policy_digest,
            helper_policy_digest,
            inherited_handle_allowlist_digest,
            issued_at,
            deadline,
            job_object_id,
            seal_digest,
        };
        if offset != bytes.len() || !receipt.is_self_consistent() {
            return Err(SupervisorError::new("authority_prepared_receipt_invalid"));
        }
        Ok(receipt)
    }

    pub(crate) fn encode(&self) -> Vec<u8> {
        let mut bytes = Vec::with_capacity(384);
        bytes.extend_from_slice(PREPARED_RECEIPT_MAGIC);
        for value in [
            self.authority_identity_digest,
            self.ticket_digest,
            self.service_instance_digest,
            self.runner_policy_digest,
            self.deterministic_job_name_digest,
            self.private_root_binding_digest,
            self.artifact_policy_digest,
            self.endpoint_policy_digest,
            self.helper_policy_digest,
            self.inherited_handle_allowlist_digest,
        ] {
            bytes.extend_from_slice(&value);
        }
        bytes.extend_from_slice(&self.issued_at.to_be_bytes());
        bytes.extend_from_slice(&self.deadline.to_be_bytes());
        bytes.extend_from_slice(&self.job_object_id.to_be_bytes());
        bytes.extend_from_slice(&self.seal_digest);
        bytes
    }

    pub(crate) fn verifies_for(
        &self,
        authority_identity_digest: &Digest,
        ticket_digest: &Digest,
        service_instance_digest: &Digest,
    ) -> bool {
        self.authority_identity_digest == *authority_identity_digest
            && self.ticket_digest == *ticket_digest
            && self.service_instance_digest == *service_instance_digest
            && self.is_self_consistent()
    }

    fn verifies_policy(&self, policy: &SupervisorPolicy) -> bool {
        self.verifies_for(
            &policy.authority_identity_digest,
            &policy.ticket_digest,
            &policy.service_instance_digest,
        ) && self.runner_policy_digest == policy.runner_policy_digest
            && self.deterministic_job_name_digest == policy.deterministic_job_name_digest
            && self.private_root_binding_digest == policy.private_root_binding_digest
            && self.artifact_policy_digest == artifact_policy_digest(&policy.artifacts)
            && self.endpoint_policy_digest == endpoint_policy_digest(&policy.socket_policies)
            && self.helper_policy_digest == helper_policy_digest(&policy.helper_policies)
            && self.inherited_handle_allowlist_digest == policy.inherited_handle_allowlist_digest
            && self.issued_at == policy.issued_at
            && self.deadline == policy.deadline
            && self.job_object_id == policy.job_object_id
    }

    pub(crate) fn verifies_policy_snapshot(&self, bytes: &[u8]) -> bool {
        decode_supervisor_policy_snapshot(bytes).is_ok_and(|policy| self.verifies_policy(&policy))
    }

    fn is_self_consistent(&self) -> bool {
        ![
            self.authority_identity_digest,
            self.ticket_digest,
            self.service_instance_digest,
            self.runner_policy_digest,
            self.deterministic_job_name_digest,
            self.private_root_binding_digest,
            self.artifact_policy_digest,
            self.endpoint_policy_digest,
            self.helper_policy_digest,
            self.inherited_handle_allowlist_digest,
        ]
        .iter()
        .any(is_zero_digest)
            && self.issued_at != 0
            && self.deadline > self.issued_at
            && self.job_object_id != 0
            && self.seal_digest == prepared_receipt_seal(self)
    }

    pub(crate) fn runner_policy_digest(&self) -> &Digest {
        &self.runner_policy_digest
    }

    pub(crate) fn service_instance_digest(&self) -> &Digest {
        &self.service_instance_digest
    }

    pub(crate) fn digest(&self) -> Digest {
        Sha256::digest(self.encode()).into()
    }
}

// This capability is deliberately non-Clone. The supervisor must consume the exact preparation
// that was persisted before it can arm a root process.
#[derive(Debug)]
pub(crate) struct PreparedRun {
    receipt: PreparedRecoveryReceipt,
    policy_snapshot: Vec<u8>,
}

impl PreparedRun {
    fn from_policy(policy: &SupervisorPolicy) -> Self {
        Self {
            receipt: PreparedRecoveryReceipt::from_policy(policy),
            policy_snapshot: canonical_supervisor_policy_snapshot(policy),
        }
    }

    #[cfg(test)]
    pub(crate) fn for_runtime_test(
        authority_identity_digest: Digest,
        ticket_digest: Digest,
        service_instance_digest: Digest,
        runner_policy_digest: Digest,
    ) -> Self {
        let policy = runtime_test_policy(
            authority_identity_digest,
            ticket_digest,
            service_instance_digest,
            runner_policy_digest,
        );
        Self::from_policy(&policy)
    }

    pub(crate) fn receipt(&self) -> &PreparedRecoveryReceipt {
        &self.receipt
    }

    pub(crate) fn policy_snapshot(&self) -> &[u8] {
        &self.policy_snapshot
    }

    pub(crate) fn verifies_for(
        &self,
        authority_identity_digest: &Digest,
        ticket_digest: &Digest,
        service_instance_digest: &Digest,
    ) -> bool {
        self.receipt.verifies_for(
            authority_identity_digest,
            ticket_digest,
            service_instance_digest,
        )
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ArmedRecoveryReceipt {
    prepared_receipt_digest: Digest,
    authority_identity_digest: Digest,
    ticket_digest: Digest,
    run_binding_digest: Digest,
    runner_policy_digest: Digest,
    deterministic_job_name_digest: Digest,
    private_root_binding_digest: Digest,
    artifact_policy_digest: Digest,
    endpoint_policy_digest: Digest,
    helper_policy_digest: Digest,
    root_process: ProcessKey,
    root_executable_digest: Digest,
    root_image_identity: FileIdentity,
    job_object_id: u64,
    created_suspended_at: u64,
    assigned_to_job_at: u64,
    resumed_at: u64,
    seal_digest: Digest,
}

impl ArmedRecoveryReceipt {
    fn from_armed_launch(
        policy: &SupervisorPolicy,
        prepared: &PreparedRecoveryReceipt,
        root_process: &ProcessObservation,
        launch: &RootLaunchObservation,
    ) -> Self {
        let mut receipt = Self {
            prepared_receipt_digest: prepared.digest(),
            authority_identity_digest: policy.authority_identity_digest,
            ticket_digest: policy.ticket_digest,
            run_binding_digest: policy.run_binding_digest,
            runner_policy_digest: policy.runner_policy_digest,
            deterministic_job_name_digest: policy.deterministic_job_name_digest,
            private_root_binding_digest: policy.private_root_binding_digest,
            artifact_policy_digest: artifact_policy_digest(&policy.artifacts),
            endpoint_policy_digest: endpoint_policy_digest(&policy.socket_policies),
            helper_policy_digest: helper_policy_digest(&policy.helper_policies),
            root_process: root_process.key,
            root_executable_digest: root_process.executable_digest,
            root_image_identity: root_process.image_handle_identity,
            job_object_id: policy.job_object_id,
            created_suspended_at: launch.created_suspended_at,
            assigned_to_job_at: launch.assigned_to_job_at,
            resumed_at: launch.resumed_at,
            seal_digest: [0; 32],
        };
        receipt.seal_digest = armed_receipt_seal(&receipt);
        receipt
    }

    #[cfg(test)]
    pub(crate) fn for_runtime_test(
        prepared: &PreparedRecoveryReceipt,
        run_binding_digest: Digest,
    ) -> Self {
        let mut receipt = Self {
            prepared_receipt_digest: prepared.digest(),
            authority_identity_digest: prepared.authority_identity_digest,
            ticket_digest: prepared.ticket_digest,
            run_binding_digest,
            runner_policy_digest: prepared.runner_policy_digest,
            deterministic_job_name_digest: prepared.deterministic_job_name_digest,
            private_root_binding_digest: prepared.private_root_binding_digest,
            artifact_policy_digest: prepared.artifact_policy_digest,
            endpoint_policy_digest: prepared.endpoint_policy_digest,
            helper_policy_digest: prepared.helper_policy_digest,
            root_process: ProcessKey {
                pid: 101,
                creation_time: 1_001,
            },
            root_executable_digest: [0x11; 32],
            root_image_identity: FileIdentity {
                volume_serial: 7,
                file_id: [8; 16],
            },
            job_object_id: prepared.job_object_id,
            created_suspended_at: 30,
            assigned_to_job_at: 31,
            resumed_at: 32,
            seal_digest: [0; 32],
        };
        receipt.seal_digest = armed_receipt_seal(&receipt);
        receipt
    }

    pub(crate) fn decode(bytes: &[u8]) -> Result<Self, SupervisorError> {
        const DIGEST_COUNT: usize = 12;
        const ENCODED_LEN: usize = 8 + DIGEST_COUNT * 32 + 4 + 8 + 8 + 16 + 4 * 8;
        if bytes.len() != ENCODED_LEN || &bytes[..8] != ARMED_RECEIPT_MAGIC {
            return Err(SupervisorError::new("authority_armed_receipt_invalid"));
        }
        let mut offset = 8usize;
        let prepared_receipt_digest = take_digest(bytes, &mut offset)?;
        let authority_identity_digest = take_digest(bytes, &mut offset)?;
        let ticket_digest = take_digest(bytes, &mut offset)?;
        let run_binding_digest = take_digest(bytes, &mut offset)?;
        let runner_policy_digest = take_digest(bytes, &mut offset)?;
        let deterministic_job_name_digest = take_digest(bytes, &mut offset)?;
        let private_root_binding_digest = take_digest(bytes, &mut offset)?;
        let artifact_policy_digest = take_digest(bytes, &mut offset)?;
        let endpoint_policy_digest = take_digest(bytes, &mut offset)?;
        let helper_policy_digest = take_digest(bytes, &mut offset)?;
        let root_executable_digest = take_digest(bytes, &mut offset)?;
        let pid = take_u32(bytes, &mut offset)?;
        let creation_time = take_u64(bytes, &mut offset)?;
        let volume_serial = take_u64(bytes, &mut offset)?;
        let file_id = take_array_16(bytes, &mut offset)?;
        let job_object_id = take_u64(bytes, &mut offset)?;
        let created_suspended_at = take_u64(bytes, &mut offset)?;
        let assigned_to_job_at = take_u64(bytes, &mut offset)?;
        let resumed_at = take_u64(bytes, &mut offset)?;
        let seal_digest = take_digest(bytes, &mut offset)?;
        let receipt = Self {
            prepared_receipt_digest,
            authority_identity_digest,
            ticket_digest,
            run_binding_digest,
            runner_policy_digest,
            deterministic_job_name_digest,
            private_root_binding_digest,
            artifact_policy_digest,
            endpoint_policy_digest,
            helper_policy_digest,
            root_process: ProcessKey { pid, creation_time },
            root_executable_digest,
            root_image_identity: FileIdentity {
                volume_serial,
                file_id,
            },
            job_object_id,
            created_suspended_at,
            assigned_to_job_at,
            resumed_at,
            seal_digest,
        };
        if offset != bytes.len() || !receipt.is_self_consistent() {
            return Err(SupervisorError::new("authority_armed_receipt_invalid"));
        }
        Ok(receipt)
    }

    pub(crate) fn encode(&self) -> Vec<u8> {
        let mut bytes = Vec::with_capacity(480);
        bytes.extend_from_slice(ARMED_RECEIPT_MAGIC);
        for value in [
            self.prepared_receipt_digest,
            self.authority_identity_digest,
            self.ticket_digest,
            self.run_binding_digest,
            self.runner_policy_digest,
            self.deterministic_job_name_digest,
            self.private_root_binding_digest,
            self.artifact_policy_digest,
            self.endpoint_policy_digest,
            self.helper_policy_digest,
            self.root_executable_digest,
        ] {
            bytes.extend_from_slice(&value);
        }
        bytes.extend_from_slice(&self.root_process.pid.to_be_bytes());
        bytes.extend_from_slice(&self.root_process.creation_time.to_be_bytes());
        bytes.extend_from_slice(&self.root_image_identity.volume_serial.to_be_bytes());
        bytes.extend_from_slice(&self.root_image_identity.file_id);
        bytes.extend_from_slice(&self.job_object_id.to_be_bytes());
        bytes.extend_from_slice(&self.created_suspended_at.to_be_bytes());
        bytes.extend_from_slice(&self.assigned_to_job_at.to_be_bytes());
        bytes.extend_from_slice(&self.resumed_at.to_be_bytes());
        bytes.extend_from_slice(&self.seal_digest);
        bytes
    }

    pub(crate) fn verifies_for(
        &self,
        prepared: &PreparedRecoveryReceipt,
        run_binding_digest: &Digest,
    ) -> bool {
        self.prepared_receipt_digest == prepared.digest()
            && self.authority_identity_digest == prepared.authority_identity_digest
            && self.ticket_digest == prepared.ticket_digest
            && self.runner_policy_digest == prepared.runner_policy_digest
            && self.deterministic_job_name_digest == prepared.deterministic_job_name_digest
            && self.private_root_binding_digest == prepared.private_root_binding_digest
            && self.artifact_policy_digest == prepared.artifact_policy_digest
            && self.endpoint_policy_digest == prepared.endpoint_policy_digest
            && self.helper_policy_digest == prepared.helper_policy_digest
            && self.job_object_id == prepared.job_object_id
            && self.run_binding_digest == *run_binding_digest
            && self.is_self_consistent()
    }

    fn is_self_consistent(&self) -> bool {
        ![
            self.prepared_receipt_digest,
            self.authority_identity_digest,
            self.ticket_digest,
            self.run_binding_digest,
            self.runner_policy_digest,
            self.deterministic_job_name_digest,
            self.private_root_binding_digest,
            self.artifact_policy_digest,
            self.endpoint_policy_digest,
            self.helper_policy_digest,
            self.root_executable_digest,
        ]
        .iter()
        .any(is_zero_digest)
            && self.root_process.pid != 0
            && self.root_process.creation_time != 0
            && self.root_image_identity.volume_serial != 0
            && self
                .root_image_identity
                .file_id
                .iter()
                .any(|byte| *byte != 0)
            && self.job_object_id != 0
            && self.created_suspended_at < self.assigned_to_job_at
            && self.assigned_to_job_at < self.resumed_at
            && self.seal_digest == armed_receipt_seal(self)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ArtifactExpectation {
    binding_digest: Digest,
    direction: ArtifactDirection,
    expected_content_digest: Option<Digest>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SocketEndpointMode {
    FixedFixture,
    ServiceSelectedPrivate,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct SocketPolicy {
    role: SocketRole,
    mode: SocketEndpointMode,
    local_port: u16,
    owner_role: ProcessRole,
    driver_binding_digest: Digest,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct HelperProcessPolicy {
    binding_digest: Digest,
    executable_digest: Digest,
    allowed_parent_executable_digests: Vec<Digest>,
    max_instances: u32,
    allowed_exit_codes: Vec<u32>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct SupervisorPolicy {
    authority_identity_digest: Digest,
    ticket_digest: Digest,
    run_binding_digest: Digest,
    service_instance_digest: Digest,
    runner_policy_digest: Digest,
    issued_at: u64,
    deadline: u64,
    authority_process: ProcessKey,
    authority_parent_process: ProcessKey,
    process_executable_digests: [Digest; PROCESS_ROLES.len()],
    runner_identity_digest: Digest,
    runner_account_digest: Digest,
    runner_profile_digest: Digest,
    inherited_handle_allowlist_digest: Digest,
    deterministic_job_name_digest: Digest,
    private_root_binding_digest: Digest,
    job_object_id: u64,
    artifacts: Vec<ArtifactExpectation>,
    socket_policies: Vec<SocketPolicy>,
    helper_policies: Vec<HelperProcessPolicy>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RunnerIdentityObservation {
    identity_digest: Digest,
    account_digest: Digest,
    profile_digest: Digest,
    validated_at: u64,
    dedicated_account: bool,
    restricted_token: bool,
    batch_logon: bool,
    administrator_member: bool,
    elevated: bool,
    interactive_user_identity: bool,
    service_identity: bool,
    network_credentials_present: bool,
    service_owned_profile: bool,
    profile_is_reparse_point: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct StableArtifactObservation {
    binding_digest: Digest,
    direction: ArtifactDirection,
    created_at: u64,
    service_handle_id: u64,
    candidate_handle_id: u64,
    source_identity: Option<FileIdentity>,
    private_identity: FileIdentity,
    candidate_handle_identity: FileIdentity,
    path_identity_at_terminal: FileIdentity,
    content_digest: Digest,
    content_length: u64,
    content_digest_read_from_service_handle: bool,
    created_new_private_copy: bool,
    service_owned_parent: bool,
    parent_is_reparse_point: bool,
    candidate_handle_explicitly_inherited: bool,
    service_handle_held_through_terminal: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RootLaunchObservation {
    role: ProcessRole,
    created_suspended_at: u64,
    assigned_to_job_at: u64,
    resumed_at: u64,
    job_object_id: u64,
    runner_identity_digest: Digest,
    inherited_handle_allowlist_digest: Digest,
    all_other_handles_non_inheritable: bool,
    breakaway_requested: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ProcessObservation {
    role: ProcessRole,
    key: ProcessKey,
    parent_pid: u32,
    parent_creation_time: u64,
    supervisor_pid: u32,
    started_at: u64,
    executable_digest: Digest,
    executable_digest_read_from_image_handle: bool,
    image_handle_identity: FileIdentity,
    image_path_identity_at_terminal: FileIdentity,
    runner_identity_digest: Option<Digest>,
    job_object_id: Option<u64>,
    job_member: bool,
    breakaway_allowed: bool,
    image_handle_held_through_terminal: bool,
    process_handle_held_through_cleanup_begin: bool,
    alive_at_finalization: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct JobObservation {
    object_id: u64,
    kill_on_job_close: bool,
    breakaway_allowed: bool,
    silent_breakaway_allowed: bool,
    active_process_limit: u32,
    completion_port_supervised: bool,
    assignment_history_complete: bool,
    assigned_processes: Vec<ProcessKey>,
    handle_held_through_cleanup_begin: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct HelperProcessObservation {
    policy_binding_digest: Digest,
    key: ProcessKey,
    parent: ProcessKey,
    supervisor_pid: u32,
    started_at: u64,
    executable_digest: Digest,
    executable_digest_read_from_image_handle: bool,
    image_handle_identity: FileIdentity,
    image_path_identity_at_terminal: FileIdentity,
    runner_identity_digest: Digest,
    job_object_id: u64,
    job_member: bool,
    breakaway_allowed: bool,
    image_handle_held_through_terminal: bool,
    process_handle_held_through_cleanup_begin: bool,
    alive_at_finalization: bool,
    exited_at: u64,
    exit_code: Option<u32>,
    completion_port_exit_observed: bool,
    terminated_by_job: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SocketVerificationPhase {
    ListenerBound,
    WorkflowReady,
    BeforeProtectedAction,
    TerminalIntent,
    CleanupBegin,
}

const SOCKET_VERIFICATION_PHASES: [SocketVerificationPhase; 5] = [
    SocketVerificationPhase::ListenerBound,
    SocketVerificationPhase::WorkflowReady,
    SocketVerificationPhase::BeforeProtectedAction,
    SocketVerificationPhase::TerminalIntent,
    SocketVerificationPhase::CleanupBegin,
];

#[derive(Debug, Clone, PartialEq, Eq)]
struct SocketVerificationObservation {
    phase: SocketVerificationPhase,
    observed_at: u64,
    owner: ProcessKey,
    owner_job_object_id: u64,
    owner_executable_digest: Digest,
    owner_image_identity: FileIdentity,
    listening: bool,
    exclusive_address_use: bool,
    address_reuse_disabled: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct SocketObservation {
    role: SocketRole,
    local_port: u16,
    prelaunch_idle_observed_at: u64,
    prelaunch_competing_owner: Option<ProcessKey>,
    listener_ready_at: u64,
    listener_socket_id: u64,
    owner: ProcessKey,
    owner_job_object_id: u64,
    owner_executable_digest: Digest,
    owner_image_identity: FileIdentity,
    driver_binding_digest: Digest,
    loopback_v4_only: bool,
    exclusive_address_use: bool,
    address_reuse_disabled: bool,
    ownership_verifications: Vec<SocketVerificationObservation>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FinalizationSource {
    AuthorityHeldOutputHandles,
    CallerSuppliedReport,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct FinalizationObservation {
    source: FinalizationSource,
    ticket_digest: Digest,
    run_binding_digest: Digest,
    finalized_at: u64,
    output_binding_digests: Vec<Digest>,
    canonical_result_binding_digest: Digest,
    canonical_result_digest: Digest,
    canonical_result_bytes: Vec<u8>,
    read_directly_from_held_handles: bool,
    retained_until_cleanup_complete: bool,
    caller_report_present: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TerminalKind {
    Completed,
    Cancelled,
    TimedOut,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TerminalIntent {
    Unresolved,
    CommitResult,
    Burn,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct TerminalObservation {
    kind: TerminalKind,
    observed_at: u64,
    intent: TerminalIntent,
    intent_recorded_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct SocketCleanupObservation {
    role: SocketRole,
    local_port: u16,
    closed_listener_socket_id: Option<u64>,
    listener_exit_observed_at: u64,
    exclusive_rebind_observed_at: u64,
    exclusive_rebind_succeeded: bool,
    rebound_socket_object_id: u64,
    competing_owner: Option<ProcessKey>,
    rebound_handle_closed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct CleanupObservation {
    observed_at: u64,
    exited_processes: Vec<ProcessKey>,
    deleted_private_artifacts: Vec<Digest>,
    sockets: Vec<SocketCleanupObservation>,
    job_terminated: bool,
    job_handle_closed: bool,
    no_live_descendants: bool,
    all_process_handles_closed: bool,
    all_file_handles_closed: bool,
    private_root_removed: bool,
    disposable_project_removed: bool,
    runner_profile_removed: bool,
    final_result_persisted: bool,
    unknown_processes: Vec<ProcessKey>,
    unknown_artifacts: Vec<Digest>,
    unknown_listeners: Vec<u16>,
}

// The fields intentionally stay private. A request handler cannot turn caller JSON into this
// type. The eventual platform backend must construct it from service-owned handles and kernel
// observations inside this module.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AuthorityOwnedRunObservation {
    ticket_consumed_at: u64,
    runner: RunnerIdentityObservation,
    artifacts: Vec<StableArtifactObservation>,
    launches: Vec<RootLaunchObservation>,
    processes: Vec<ProcessObservation>,
    helpers: Vec<HelperProcessObservation>,
    job: JobObservation,
    sockets: Vec<SocketObservation>,
    finalization: Option<FinalizationObservation>,
    terminal: TerminalObservation,
    cleanup: CleanupObservation,
}

// Abort observations are deliberately stage-aware: they record exactly what the protected
// service managed to create before a failed/uncertain launch. Missing objects are allowed; any
// object that did exist must be policy-bound, job-contained, and present in the cleanup sets.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AuthorityOwnedAbortObservation {
    ticket_consumed_at: u64,
    runner: Option<RunnerIdentityObservation>,
    artifacts: Vec<StableArtifactObservation>,
    launches: Vec<RootLaunchObservation>,
    processes: Vec<ProcessObservation>,
    helpers: Vec<HelperProcessObservation>,
    job: Option<JobObservation>,
    sockets: Vec<SocketObservation>,
    terminal: TerminalObservation,
    cleanup: CleanupObservation,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct CompletedRunProof {
    authority_identity_digest: Digest,
    ticket_digest: Digest,
    run_binding_digest: Digest,
    result_digest: Digest,
    result_bytes: Vec<u8>,
    cleanup_receipt_digest: Digest,
    finalized_at: u64,
    cleanup_observed_at: u64,
}

impl CompletedRunProof {
    #[cfg(test)]
    pub(crate) fn for_runtime_test(
        authority_identity_digest: Digest,
        ticket_digest: Digest,
        run_binding_digest: Digest,
        result_bytes: Vec<u8>,
    ) -> Self {
        let result_digest = Sha256::digest(&result_bytes).into();
        let cleanup_receipt_digest = test_cleanup_receipt(
            &authority_identity_digest,
            &ticket_digest,
            &run_binding_digest,
            &result_digest,
            1,
        );
        Self {
            authority_identity_digest,
            ticket_digest,
            run_binding_digest,
            result_digest,
            result_bytes,
            cleanup_receipt_digest,
            finalized_at: 100,
            cleanup_observed_at: 120,
        }
    }

    pub(crate) fn authority_identity_digest(&self) -> &Digest {
        &self.authority_identity_digest
    }

    pub(crate) fn ticket_digest(&self) -> &Digest {
        &self.ticket_digest
    }

    pub(crate) fn run_binding_digest(&self) -> &Digest {
        &self.run_binding_digest
    }

    pub(crate) fn result_digest(&self) -> &Digest {
        &self.result_digest
    }

    pub(crate) fn result_bytes(&self) -> &[u8] {
        &self.result_bytes
    }

    pub(crate) fn cleanup_receipt_digest(&self) -> &Digest {
        &self.cleanup_receipt_digest
    }

    pub(crate) fn finalized_at(&self) -> u64 {
        self.finalized_at
    }

    pub(crate) fn cleanup_observed_at(&self) -> u64 {
        self.cleanup_observed_at
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum BurnReason {
    Cancelled,
    TimedOut,
    Failed,
    RestartRecovery,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct BurnedRunProof {
    authority_identity_digest: Digest,
    ticket_digest: Digest,
    run_binding_digest: Digest,
    reason: BurnReason,
    cleanup_receipt_digest: Digest,
    terminal_ready_at: u64,
    cleanup_observed_at: u64,
}

impl BurnedRunProof {
    #[cfg(test)]
    pub(crate) fn for_runtime_test(
        authority_identity_digest: Digest,
        ticket_digest: Digest,
        run_binding_digest: Digest,
        reason: BurnReason,
    ) -> Self {
        let cleanup_receipt_digest = test_cleanup_receipt(
            &authority_identity_digest,
            &ticket_digest,
            &run_binding_digest,
            &[0; 32],
            burn_reason_code(reason),
        );
        Self {
            authority_identity_digest,
            ticket_digest,
            run_binding_digest,
            reason,
            cleanup_receipt_digest,
            terminal_ready_at: 100,
            cleanup_observed_at: 120,
        }
    }

    pub(crate) fn authority_identity_digest(&self) -> &Digest {
        &self.authority_identity_digest
    }

    pub(crate) fn ticket_digest(&self) -> &Digest {
        &self.ticket_digest
    }

    pub(crate) fn run_binding_digest(&self) -> &Digest {
        &self.run_binding_digest
    }

    pub(crate) fn reason(&self) -> BurnReason {
        self.reason
    }

    pub(crate) fn cleanup_receipt_digest(&self) -> &Digest {
        &self.cleanup_receipt_digest
    }

    pub(crate) fn terminal_ready_at(&self) -> u64 {
        self.terminal_ready_at
    }

    pub(crate) fn cleanup_observed_at(&self) -> u64 {
        self.cleanup_observed_at
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum ValidatedTerminalRun {
    Completed(CompletedRunProof),
    Burned(BurnedRunProof),
}

pub(crate) fn derive_run_binding_digest(
    authority_identity_digest: &Digest,
    ticket_digest: &Digest,
    service_instance_digest: &Digest,
    runner_policy_digest: &Digest,
) -> Digest {
    let mut digest = Sha256::new();
    digest.update(RUNTIME_RUN_BINDING_DOMAIN);
    digest.update(authority_identity_digest);
    digest.update(ticket_digest);
    digest.update(service_instance_digest);
    digest.update(runner_policy_digest);
    digest.finalize().into()
}

fn canonical_supervisor_policy_digest(policy: &SupervisorPolicy) -> Digest {
    let mut digest = Sha256::new();
    digest.update(SUPERVISOR_POLICY_DOMAIN);
    digest.update(canonical_supervisor_policy_snapshot(policy));
    digest.finalize().into()
}

fn canonical_supervisor_policy_snapshot(policy: &SupervisorPolicy) -> Vec<u8> {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(POLICY_SNAPSHOT_MAGIC);
    for value in [
        policy.authority_identity_digest,
        policy.ticket_digest,
        policy.service_instance_digest,
    ] {
        bytes.extend_from_slice(&value);
    }
    bytes.extend_from_slice(&policy.issued_at.to_be_bytes());
    bytes.extend_from_slice(&policy.deadline.to_be_bytes());
    for process in [policy.authority_process, policy.authority_parent_process] {
        bytes.extend_from_slice(&process.pid.to_be_bytes());
        bytes.extend_from_slice(&process.creation_time.to_be_bytes());
    }
    for executable in policy.process_executable_digests {
        bytes.extend_from_slice(&executable);
    }
    for value in [
        policy.runner_identity_digest,
        policy.runner_account_digest,
        policy.runner_profile_digest,
        policy.inherited_handle_allowlist_digest,
        policy.deterministic_job_name_digest,
        policy.private_root_binding_digest,
    ] {
        bytes.extend_from_slice(&value);
    }
    bytes.extend_from_slice(&policy.job_object_id.to_be_bytes());
    bytes.extend_from_slice(&(policy.artifacts.len() as u32).to_be_bytes());
    for artifact in &policy.artifacts {
        bytes.extend_from_slice(&artifact.binding_digest);
        bytes.push(match artifact.direction {
            ArtifactDirection::Input => 1,
            ArtifactDirection::Output => 2,
        });
        bytes.push(u8::from(artifact.expected_content_digest.is_some()));
        bytes.extend_from_slice(&artifact.expected_content_digest.unwrap_or([0; 32]));
    }
    bytes.extend_from_slice(&(policy.socket_policies.len() as u32).to_be_bytes());
    for endpoint in &policy.socket_policies {
        bytes.push(socket_role_code(endpoint.role));
        bytes.push(match endpoint.mode {
            SocketEndpointMode::FixedFixture => 1,
            SocketEndpointMode::ServiceSelectedPrivate => 2,
        });
        bytes.extend_from_slice(&endpoint.local_port.to_be_bytes());
        bytes.push(process_role_code(endpoint.owner_role));
        bytes.extend_from_slice(&endpoint.driver_binding_digest);
    }
    bytes.extend_from_slice(&(policy.helper_policies.len() as u32).to_be_bytes());
    for helper in &policy.helper_policies {
        bytes.extend_from_slice(&helper.binding_digest);
        bytes.extend_from_slice(&helper.executable_digest);
        bytes.extend_from_slice(&helper.max_instances.to_be_bytes());
        bytes.extend_from_slice(
            &(helper.allowed_parent_executable_digests.len() as u32).to_be_bytes(),
        );
        for parent in &helper.allowed_parent_executable_digests {
            bytes.extend_from_slice(parent);
        }
        bytes.extend_from_slice(&(helper.allowed_exit_codes.len() as u32).to_be_bytes());
        for code in &helper.allowed_exit_codes {
            bytes.extend_from_slice(&code.to_be_bytes());
        }
    }
    bytes
}

fn decode_supervisor_policy_snapshot(bytes: &[u8]) -> Result<SupervisorPolicy, SupervisorError> {
    if bytes.len() < POLICY_SNAPSHOT_MAGIC.len() || &bytes[..8] != POLICY_SNAPSHOT_MAGIC {
        return Err(SupervisorError::new("authority_policy_snapshot_invalid"));
    }
    let mut offset = 8usize;
    let authority_identity_digest = take_digest(bytes, &mut offset)?;
    let ticket_digest = take_digest(bytes, &mut offset)?;
    let service_instance_digest = take_digest(bytes, &mut offset)?;
    let issued_at = take_u64(bytes, &mut offset)?;
    let deadline = take_u64(bytes, &mut offset)?;
    let authority_process = ProcessKey {
        pid: take_u32(bytes, &mut offset)?,
        creation_time: take_u64(bytes, &mut offset)?,
    };
    let authority_parent_process = ProcessKey {
        pid: take_u32(bytes, &mut offset)?,
        creation_time: take_u64(bytes, &mut offset)?,
    };
    let mut process_executable_digests = [[0u8; 32]; PROCESS_ROLES.len()];
    for digest in &mut process_executable_digests {
        *digest = take_digest(bytes, &mut offset)?;
    }
    let runner_identity_digest = take_digest(bytes, &mut offset)?;
    let runner_account_digest = take_digest(bytes, &mut offset)?;
    let runner_profile_digest = take_digest(bytes, &mut offset)?;
    let inherited_handle_allowlist_digest = take_digest(bytes, &mut offset)?;
    let deterministic_job_name_digest = take_digest(bytes, &mut offset)?;
    let private_root_binding_digest = take_digest(bytes, &mut offset)?;
    let job_object_id = take_u64(bytes, &mut offset)?;
    let artifact_count = take_count(bytes, &mut offset, MAX_POLICY_ARTIFACTS)?;
    let mut artifacts = Vec::with_capacity(artifact_count);
    for _ in 0..artifact_count {
        let binding_digest = take_digest(bytes, &mut offset)?;
        let direction = match take_u8(bytes, &mut offset)? {
            1 => ArtifactDirection::Input,
            2 => ArtifactDirection::Output,
            _ => return Err(SupervisorError::new("authority_policy_snapshot_invalid")),
        };
        let has_expected = take_u8(bytes, &mut offset)?;
        let expected = take_digest(bytes, &mut offset)?;
        let expected_content_digest = match has_expected {
            0 if is_zero_digest(&expected) => None,
            1 if !is_zero_digest(&expected) => Some(expected),
            _ => return Err(SupervisorError::new("authority_policy_snapshot_invalid")),
        };
        artifacts.push(ArtifactExpectation {
            binding_digest,
            direction,
            expected_content_digest,
        });
    }
    let endpoint_count = take_count(bytes, &mut offset, MAX_POLICY_ENDPOINTS)?;
    let mut socket_policies = Vec::with_capacity(endpoint_count);
    for _ in 0..endpoint_count {
        let role = decode_socket_role(take_u8(bytes, &mut offset)?)?;
        let mode = match take_u8(bytes, &mut offset)? {
            1 => SocketEndpointMode::FixedFixture,
            2 => SocketEndpointMode::ServiceSelectedPrivate,
            _ => return Err(SupervisorError::new("authority_policy_snapshot_invalid")),
        };
        let local_port = take_u16(bytes, &mut offset)?;
        let owner_role = decode_process_role(take_u8(bytes, &mut offset)?)?;
        let driver_binding_digest = take_digest(bytes, &mut offset)?;
        socket_policies.push(SocketPolicy {
            role,
            mode,
            local_port,
            owner_role,
            driver_binding_digest,
        });
    }
    let helper_count = take_count(bytes, &mut offset, MAX_POLICY_HELPERS)?;
    let mut helper_policies = Vec::with_capacity(helper_count);
    for _ in 0..helper_count {
        let binding_digest = take_digest(bytes, &mut offset)?;
        let executable_digest = take_digest(bytes, &mut offset)?;
        let max_instances = take_u32(bytes, &mut offset)?;
        let parent_count = take_count(bytes, &mut offset, MAX_HELPER_PARENT_DIGESTS)?;
        let mut allowed_parent_executable_digests = Vec::with_capacity(parent_count);
        for _ in 0..parent_count {
            allowed_parent_executable_digests.push(take_digest(bytes, &mut offset)?);
        }
        let exit_count = take_count(bytes, &mut offset, MAX_HELPER_EXIT_CODES)?;
        let mut allowed_exit_codes = Vec::with_capacity(exit_count);
        for _ in 0..exit_count {
            allowed_exit_codes.push(take_u32(bytes, &mut offset)?);
        }
        helper_policies.push(HelperProcessPolicy {
            binding_digest,
            executable_digest,
            allowed_parent_executable_digests,
            max_instances,
            allowed_exit_codes,
        });
    }
    if offset != bytes.len() {
        return Err(SupervisorError::new("authority_policy_snapshot_invalid"));
    }
    let mut policy = SupervisorPolicy {
        authority_identity_digest,
        ticket_digest,
        run_binding_digest: [0; 32],
        service_instance_digest,
        runner_policy_digest: [0; 32],
        issued_at,
        deadline,
        authority_process,
        authority_parent_process,
        process_executable_digests,
        runner_identity_digest,
        runner_account_digest,
        runner_profile_digest,
        inherited_handle_allowlist_digest,
        deterministic_job_name_digest,
        private_root_binding_digest,
        job_object_id,
        artifacts,
        socket_policies,
        helper_policies,
    };
    policy.runner_policy_digest = canonical_supervisor_policy_digest(&policy);
    policy.run_binding_digest = derive_run_binding_digest(
        &policy.authority_identity_digest,
        &policy.ticket_digest,
        &policy.service_instance_digest,
        &policy.runner_policy_digest,
    );
    validate_policy(&policy)?;
    Ok(policy)
}

#[cfg(test)]
fn runtime_test_policy(
    authority_identity_digest: Digest,
    ticket_digest: Digest,
    service_instance_digest: Digest,
    policy_seed: Digest,
) -> SupervisorPolicy {
    let derive = |tag: u8| {
        let mut digest = Sha256::new();
        digest.update(b"vrcforge-authority-runtime-test-policy-v1\0");
        digest.update(policy_seed);
        digest.update([tag]);
        <[u8; 32]>::from(digest.finalize())
    };
    let mut policy = SupervisorPolicy {
        authority_identity_digest,
        ticket_digest,
        run_binding_digest: [0; 32],
        service_instance_digest,
        runner_policy_digest: [0; 32],
        issued_at: 10,
        deadline: 1_000,
        authority_process: ProcessKey {
            pid: 100,
            creation_time: 1_000,
        },
        authority_parent_process: ProcessKey {
            pid: 90,
            creation_time: 900,
        },
        process_executable_digests: [
            derive(1),
            derive(2),
            derive(3),
            derive(4),
            derive(5),
            derive(6),
            derive(7),
        ],
        runner_identity_digest: derive(8),
        runner_account_digest: derive(9),
        runner_profile_digest: derive(10),
        inherited_handle_allowlist_digest: derive(11),
        deterministic_job_name_digest: derive(12),
        private_root_binding_digest: derive(13),
        job_object_id: 500,
        artifacts: vec![
            ArtifactExpectation {
                binding_digest: derive(14),
                direction: ArtifactDirection::Input,
                expected_content_digest: Some(derive(15)),
            },
            ArtifactExpectation {
                binding_digest: derive(16),
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
                driver_binding_digest: derive(17),
            },
            SocketPolicy {
                role: SocketRole::Bridge,
                mode: SocketEndpointMode::ServiceSelectedPrivate,
                local_port: 55_080,
                owner_role: ProcessRole::BridgeListener,
                driver_binding_digest: derive(18),
            },
        ],
        helper_policies: Vec::new(),
    };
    policy.runner_policy_digest = canonical_supervisor_policy_digest(&policy);
    policy.run_binding_digest = derive_run_binding_digest(
        &policy.authority_identity_digest,
        &policy.ticket_digest,
        &policy.service_instance_digest,
        &policy.runner_policy_digest,
    );
    policy
}

fn artifact_policy_digest(artifacts: &[ArtifactExpectation]) -> Digest {
    let mut digest = Sha256::new();
    digest.update(ARTIFACT_POLICY_DOMAIN);
    digest.update((artifacts.len() as u64).to_be_bytes());
    for artifact in artifacts {
        digest.update(artifact.binding_digest);
        digest.update([match artifact.direction {
            ArtifactDirection::Input => 1,
            ArtifactDirection::Output => 2,
        }]);
        digest.update(artifact.expected_content_digest.unwrap_or([0; 32]));
    }
    digest.finalize().into()
}

fn endpoint_policy_digest(endpoints: &[SocketPolicy]) -> Digest {
    let mut digest = Sha256::new();
    digest.update(ENDPOINT_POLICY_DOMAIN);
    digest.update((endpoints.len() as u64).to_be_bytes());
    for endpoint in endpoints {
        digest.update([socket_role_code(endpoint.role)]);
        digest.update([match endpoint.mode {
            SocketEndpointMode::FixedFixture => 1,
            SocketEndpointMode::ServiceSelectedPrivate => 2,
        }]);
        digest.update(endpoint.local_port.to_be_bytes());
        digest.update([process_role_code(endpoint.owner_role)]);
        digest.update(endpoint.driver_binding_digest);
    }
    digest.finalize().into()
}

fn helper_policy_digest(helpers: &[HelperProcessPolicy]) -> Digest {
    let mut digest = Sha256::new();
    digest.update(HELPER_POLICY_DOMAIN);
    digest.update((helpers.len() as u64).to_be_bytes());
    for helper in helpers {
        digest.update(helper.binding_digest);
        digest.update(helper.executable_digest);
        digest.update(helper.max_instances.to_be_bytes());
        digest.update((helper.allowed_parent_executable_digests.len() as u64).to_be_bytes());
        for parent in &helper.allowed_parent_executable_digests {
            digest.update(parent);
        }
        digest.update((helper.allowed_exit_codes.len() as u64).to_be_bytes());
        for code in &helper.allowed_exit_codes {
            digest.update(code.to_be_bytes());
        }
    }
    digest.finalize().into()
}

fn prepared_receipt_seal(receipt: &PreparedRecoveryReceipt) -> Digest {
    let mut digest = Sha256::new();
    digest.update(PREPARED_RECEIPT_DOMAIN);
    for value in [
        receipt.authority_identity_digest,
        receipt.ticket_digest,
        receipt.service_instance_digest,
        receipt.runner_policy_digest,
        receipt.deterministic_job_name_digest,
        receipt.private_root_binding_digest,
        receipt.artifact_policy_digest,
        receipt.endpoint_policy_digest,
        receipt.helper_policy_digest,
        receipt.inherited_handle_allowlist_digest,
    ] {
        digest.update(value);
    }
    digest.update(receipt.issued_at.to_be_bytes());
    digest.update(receipt.deadline.to_be_bytes());
    digest.update(receipt.job_object_id.to_be_bytes());
    digest.finalize().into()
}

fn armed_receipt_seal(receipt: &ArmedRecoveryReceipt) -> Digest {
    let mut digest = Sha256::new();
    digest.update(ARMED_RECEIPT_DOMAIN);
    for value in [
        receipt.prepared_receipt_digest,
        receipt.authority_identity_digest,
        receipt.ticket_digest,
        receipt.run_binding_digest,
        receipt.runner_policy_digest,
        receipt.deterministic_job_name_digest,
        receipt.private_root_binding_digest,
        receipt.artifact_policy_digest,
        receipt.endpoint_policy_digest,
        receipt.helper_policy_digest,
        receipt.root_executable_digest,
    ] {
        digest.update(value);
    }
    digest.update(receipt.root_process.pid.to_be_bytes());
    digest.update(receipt.root_process.creation_time.to_be_bytes());
    digest.update(receipt.root_image_identity.volume_serial.to_be_bytes());
    digest.update(receipt.root_image_identity.file_id);
    digest.update(receipt.job_object_id.to_be_bytes());
    digest.update(receipt.created_suspended_at.to_be_bytes());
    digest.update(receipt.assigned_to_job_at.to_be_bytes());
    digest.update(receipt.resumed_at.to_be_bytes());
    digest.finalize().into()
}

pub(crate) fn validate_authority_owned_run(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedRunObservation,
) -> Result<ValidatedTerminalRun, SupervisorError> {
    validate_policy(policy)?;
    if observation.ticket_consumed_at < policy.issued_at
        || observation.ticket_consumed_at >= policy.deadline
    {
        return Err(SupervisorError::new(
            "authority_ticket_consume_time_invalid",
        ));
    }

    validate_runner(policy, observation)?;
    validate_artifacts(policy, observation)?;
    let process_graph = validate_processes(policy, observation)?;
    validate_launches(policy, observation, &process_graph.core)?;
    validate_job(policy, observation, &process_graph.all_candidates)?;
    validate_sockets(policy, observation, &process_graph.core)?;
    validate_terminal(policy, observation)?;
    validate_cleanup(policy, observation, &process_graph.all_candidates)?;
    let cleanup_receipt_digest = derive_cleanup_receipt(policy, observation);

    match observation.terminal.kind {
        TerminalKind::Completed => {
            let finalization = observation
                .finalization
                .as_ref()
                .ok_or_else(|| SupervisorError::new("authority_finalization_missing"))?;
            Ok(ValidatedTerminalRun::Completed(CompletedRunProof {
                authority_identity_digest: policy.authority_identity_digest,
                ticket_digest: policy.ticket_digest,
                run_binding_digest: policy.run_binding_digest,
                result_digest: finalization.canonical_result_digest,
                result_bytes: finalization.canonical_result_bytes.clone(),
                cleanup_receipt_digest,
                finalized_at: finalization.finalized_at,
                cleanup_observed_at: observation.cleanup.observed_at,
            }))
        }
        TerminalKind::Cancelled | TerminalKind::TimedOut | TerminalKind::Failed => {
            let reason = match observation.terminal.kind {
                TerminalKind::Cancelled => BurnReason::Cancelled,
                TerminalKind::TimedOut => BurnReason::TimedOut,
                TerminalKind::Failed => BurnReason::Failed,
                TerminalKind::Completed => unreachable!(),
            };
            Ok(ValidatedTerminalRun::Burned(BurnedRunProof {
                authority_identity_digest: policy.authority_identity_digest,
                ticket_digest: policy.ticket_digest,
                run_binding_digest: policy.run_binding_digest,
                reason,
                cleanup_receipt_digest,
                terminal_ready_at: observation.terminal.intent_recorded_at,
                cleanup_observed_at: observation.cleanup.observed_at,
            }))
        }
    }
}

pub(crate) fn validate_authority_owned_abort(
    policy: &SupervisorPolicy,
    prepared: &PreparedRecoveryReceipt,
    armed: Option<&ArmedRecoveryReceipt>,
    observation: &AuthorityOwnedAbortObservation,
    reason: BurnReason,
) -> Result<BurnedRunProof, SupervisorError> {
    if !matches!(reason, BurnReason::Failed | BurnReason::RestartRecovery) {
        return Err(SupervisorError::new("authority_abort_reason_invalid"));
    }
    validate_policy(policy)?;
    if !prepared.verifies_policy(policy) {
        return Err(SupervisorError::new("authority_prepared_receipt_mismatch"));
    }
    if let Some(armed) = armed {
        if !armed.verifies_for(prepared, &policy.run_binding_digest) {
            return Err(SupervisorError::new("authority_armed_receipt_mismatch"));
        }
    }
    if observation.ticket_consumed_at < policy.issued_at
        || observation.ticket_consumed_at >= policy.deadline
        || observation.terminal.kind != TerminalKind::Failed
        || observation.terminal.intent != TerminalIntent::Burn
        || observation.terminal.observed_at < observation.ticket_consumed_at
        || observation.terminal.intent_recorded_at < observation.terminal.observed_at
        || observation.terminal.intent_recorded_at > observation.cleanup.observed_at
    {
        return Err(SupervisorError::new("authority_abort_terminal_invalid"));
    }
    match &observation.runner {
        Some(runner) => {
            validate_runner_observation(policy, observation.ticket_consumed_at, runner)?
        }
        None if observation.processes.len() == 1
            && observation.helpers.is_empty()
            && observation.launches.is_empty()
            && observation.sockets.is_empty() => {}
        None => return Err(SupervisorError::new("authority_abort_runner_missing")),
    }
    validate_abort_artifacts(policy, observation)?;
    let process_graph = validate_abort_processes(policy, observation)?;
    validate_abort_launch(policy, prepared, armed, observation, &process_graph)?;
    validate_abort_job(policy, observation, &process_graph.all_candidates)?;
    validate_abort_sockets(policy, observation, &process_graph.core)?;
    validate_abort_cleanup(policy, observation, &process_graph.all_candidates)?;
    let cleanup_receipt_digest = derive_abort_cleanup_receipt(policy, observation, reason);
    Ok(BurnedRunProof {
        authority_identity_digest: policy.authority_identity_digest,
        ticket_digest: policy.ticket_digest,
        run_binding_digest: policy.run_binding_digest,
        reason,
        cleanup_receipt_digest,
        terminal_ready_at: observation.terminal.intent_recorded_at,
        cleanup_observed_at: observation.cleanup.observed_at,
    })
}

fn validate_abort_artifacts(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedAbortObservation,
) -> Result<(), SupervisorError> {
    let cutoff = observation
        .launches
        .first()
        .map(|launch| launch.created_suspended_at)
        .unwrap_or(observation.terminal.observed_at);
    let policy_order = policy
        .artifacts
        .iter()
        .enumerate()
        .map(|(index, item)| (item.binding_digest, (index, item)))
        .collect::<BTreeMap<_, _>>();
    let mut prior_index = None;
    let mut service_handles = BTreeSet::new();
    let mut candidate_handles = BTreeSet::new();
    let mut identities = BTreeSet::new();
    for artifact in &observation.artifacts {
        let (index, expected) = policy_order
            .get(&artifact.binding_digest)
            .ok_or_else(|| SupervisorError::new("authority_abort_artifact_unexpected"))?;
        if prior_index.is_some_and(|prior| *index <= prior)
            || artifact.direction != expected.direction
            || artifact.created_at < observation.ticket_consumed_at
            || artifact.created_at >= cutoff
            || artifact.service_handle_id == 0
            || artifact.candidate_handle_id == 0
            || !service_handles.insert(artifact.service_handle_id)
            || !candidate_handles.insert(artifact.candidate_handle_id)
            || !identities.insert(artifact.private_identity)
            || !artifact.created_new_private_copy
            || !artifact.service_owned_parent
            || artifact.parent_is_reparse_point
            || !artifact.candidate_handle_explicitly_inherited
            || !artifact.service_handle_held_through_terminal
            || !artifact.content_digest_read_from_service_handle
            || is_zero_digest(&artifact.content_digest)
            || artifact.private_identity != artifact.candidate_handle_identity
            || artifact.private_identity != artifact.path_identity_at_terminal
        {
            return Err(SupervisorError::new("authority_abort_artifact_invalid"));
        }
        if artifact.direction == ArtifactDirection::Input
            && (artifact.source_identity.is_none()
                || artifact.source_identity == Some(artifact.private_identity)
                || expected.expected_content_digest != Some(artifact.content_digest)
                || artifact.content_length == 0)
        {
            return Err(SupervisorError::new("authority_abort_artifact_invalid"));
        }
        if artifact.direction == ArtifactDirection::Output && artifact.source_identity.is_some() {
            return Err(SupervisorError::new("authority_abort_artifact_invalid"));
        }
        prior_index = Some(*index);
    }
    Ok(())
}

struct AbortProcessGraph {
    core: [Option<ProcessKey>; PROCESS_ROLES.len()],
    all_candidates: BTreeSet<ProcessKey>,
}

fn validate_abort_processes(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedAbortObservation,
) -> Result<AbortProcessGraph, SupervisorError> {
    if observation.processes.is_empty()
        || observation.processes[0].role != ProcessRole::AuthorityService
    {
        return Err(SupervisorError::new("authority_abort_service_missing"));
    }
    let mut core: [Option<ProcessKey>; PROCESS_ROLES.len()] = [None; PROCESS_ROLES.len()];
    let mut keys = BTreeSet::new();
    let mut pids = BTreeSet::new();
    let mut prior_role_index = None;
    for process in &observation.processes {
        let index = role_index(process.role);
        if prior_role_index.is_some_and(|prior| index <= prior)
            || process.key.pid == 0
            || process.key.creation_time == 0
            || !keys.insert(process.key)
            || !pids.insert(process.key.pid)
            || process.executable_digest != policy.process_executable_digests[index]
            || !process.executable_digest_read_from_image_handle
            || process.image_handle_identity != process.image_path_identity_at_terminal
            || !process.image_handle_held_through_terminal
            || !process.process_handle_held_through_cleanup_begin
        {
            return Err(SupervisorError::new("authority_abort_process_invalid"));
        }
        if process.role == ProcessRole::AuthorityService {
            if process.key != policy.authority_process
                || process.parent_pid != policy.authority_parent_process.pid
                || process.parent_creation_time != policy.authority_parent_process.creation_time
                || process.supervisor_pid != 0
                || process.runner_identity_digest.is_some()
                || process.job_object_id.is_some()
                || process.job_member
                || process.breakaway_allowed
                || process.started_at > policy.issued_at
            {
                return Err(SupervisorError::new("authority_abort_service_invalid"));
            }
        } else {
            let parent_role = process
                .role
                .expected_parent()
                .ok_or_else(|| SupervisorError::new("authority_abort_parent_missing"))?;
            let parent = core[role_index(parent_role)]
                .ok_or_else(|| SupervisorError::new("authority_abort_parent_missing"))?;
            if process.parent_pid != parent.pid
                || process.parent_creation_time != parent.creation_time
                || process.supervisor_pid != parent.pid
                || process.started_at < observation.ticket_consumed_at
                || process.started_at >= policy.deadline
                || process.runner_identity_digest != Some(policy.runner_identity_digest)
                || process.job_object_id != Some(policy.job_object_id)
                || !process.job_member
                || process.breakaway_allowed
            {
                return Err(SupervisorError::new("authority_abort_process_invalid"));
            }
        }
        core[index] = Some(process.key);
        prior_role_index = Some(index);
    }
    let mut executable_by_process = observation
        .processes
        .iter()
        .map(|process| (process.key, process.executable_digest))
        .collect::<BTreeMap<_, _>>();
    let helper_policies = policy
        .helper_policies
        .iter()
        .map(|item| (item.binding_digest, item))
        .collect::<BTreeMap<_, _>>();
    let mut counts = BTreeMap::<Digest, u32>::new();
    let mut helper_keys = BTreeSet::new();
    for helper in &observation.helpers {
        let helper_policy = helper_policies
            .get(&helper.policy_binding_digest)
            .ok_or_else(|| SupervisorError::new("authority_helper_process_unexpected"))?;
        let count = counts.entry(helper.policy_binding_digest).or_insert(0);
        *count = count.saturating_add(1);
        let parent_executable = executable_by_process
            .get(&helper.parent)
            .ok_or_else(|| SupervisorError::new("authority_helper_parent_unknown"))?;
        if *count > helper_policy.max_instances
            || !helper_policy
                .allowed_parent_executable_digests
                .contains(parent_executable)
            || helper.executable_digest != helper_policy.executable_digest
            || helper.supervisor_pid != helper.parent.pid
            || helper.runner_identity_digest != policy.runner_identity_digest
            || helper.job_object_id != policy.job_object_id
            || !helper.job_member
            || helper.breakaway_allowed
            || !helper.executable_digest_read_from_image_handle
            || helper.image_handle_identity != helper.image_path_identity_at_terminal
            || !helper.image_handle_held_through_terminal
            || !helper.process_handle_held_through_cleanup_begin
            || helper.started_at < observation.ticket_consumed_at
            || helper.started_at >= policy.deadline
            || helper.key.pid == 0
            || helper.key.creation_time == 0
            || !keys.insert(helper.key)
            || !pids.insert(helper.key.pid)
            || !helper.completion_port_exit_observed
            || helper.exited_at < helper.started_at
            || helper.exited_at > observation.cleanup.observed_at
        {
            return Err(SupervisorError::new("authority_helper_process_invalid"));
        }
        if helper.terminated_by_job {
            if helper.exit_code.is_some()
                || helper.exited_at < observation.terminal.intent_recorded_at
            {
                return Err(SupervisorError::new("authority_helper_exit_invalid"));
            }
        } else if !helper
            .exit_code
            .is_some_and(|code| helper_policy.allowed_exit_codes.contains(&code))
        {
            return Err(SupervisorError::new("authority_helper_exit_invalid"));
        }
        executable_by_process.insert(helper.key, helper.executable_digest);
        helper_keys.insert(helper.key);
    }
    let mut all_candidates = observation
        .processes
        .iter()
        .filter(|process| process.role.is_candidate())
        .map(|process| process.key)
        .collect::<BTreeSet<_>>();
    all_candidates.extend(helper_keys);
    Ok(AbortProcessGraph {
        core,
        all_candidates,
    })
}

fn validate_abort_launch(
    policy: &SupervisorPolicy,
    prepared: &PreparedRecoveryReceipt,
    armed: Option<&ArmedRecoveryReceipt>,
    observation: &AuthorityOwnedAbortObservation,
    graph: &AbortProcessGraph,
) -> Result<(), SupervisorError> {
    let driver = graph.core[role_index(ProcessRole::Driver)];
    match (driver, observation.launches.as_slice()) {
        (None, []) => {
            if armed.is_some() {
                return Err(SupervisorError::new("authority_armed_without_root"));
            }
        }
        (Some(driver_key), [launch]) => {
            if launch.role != ProcessRole::Driver
                || launch.created_suspended_at
                    < observation
                        .runner
                        .as_ref()
                        .ok_or_else(|| SupervisorError::new("authority_abort_runner_missing"))?
                        .validated_at
                || launch.created_suspended_at >= launch.assigned_to_job_at
                || launch.assigned_to_job_at >= launch.resumed_at
                || launch.resumed_at >= policy.deadline
                || launch.job_object_id != policy.job_object_id
                || launch.runner_identity_digest != policy.runner_identity_digest
                || launch.inherited_handle_allowlist_digest
                    != policy.inherited_handle_allowlist_digest
                || !launch.all_other_handles_non_inheritable
                || launch.breakaway_requested
            {
                return Err(SupervisorError::new("authority_abort_launch_invalid"));
            }
            if let Some(armed) = armed {
                let driver_process = observation
                    .processes
                    .iter()
                    .find(|process| process.role == ProcessRole::Driver)
                    .ok_or_else(|| SupervisorError::new("authority_abort_driver_missing"))?;
                if armed.root_process != driver_key
                    || armed.root_executable_digest != driver_process.executable_digest
                    || armed.root_image_identity != driver_process.image_handle_identity
                    || armed.created_suspended_at != launch.created_suspended_at
                    || armed.assigned_to_job_at != launch.assigned_to_job_at
                    || armed.resumed_at != launch.resumed_at
                    || !armed.verifies_for(prepared, &policy.run_binding_digest)
                {
                    return Err(SupervisorError::new("authority_armed_root_mismatch"));
                }
            }
        }
        _ => return Err(SupervisorError::new("authority_abort_root_set_invalid")),
    }
    Ok(())
}

fn validate_abort_job(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedAbortObservation,
    expected_processes: &BTreeSet<ProcessKey>,
) -> Result<(), SupervisorError> {
    let job = observation
        .job
        .as_ref()
        .ok_or_else(|| SupervisorError::new("authority_abort_job_missing"))?;
    let assigned = job
        .assigned_processes
        .iter()
        .copied()
        .collect::<BTreeSet<_>>();
    if job.object_id != policy.job_object_id
        || !job.kill_on_job_close
        || job.breakaway_allowed
        || job.silent_breakaway_allowed
        || job.active_process_limit != 0
        || !job.completion_port_supervised
        || !job.assignment_history_complete
        || !job.handle_held_through_cleanup_begin
        || assigned != *expected_processes
        || job.assigned_processes.len() != expected_processes.len()
    {
        return Err(SupervisorError::new("authority_abort_job_invalid"));
    }
    Ok(())
}

fn validate_abort_sockets(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedAbortObservation,
    core: &[Option<ProcessKey>; PROCESS_ROLES.len()],
) -> Result<(), SupervisorError> {
    let policy_by_role = policy
        .socket_policies
        .iter()
        .enumerate()
        .map(|(index, endpoint)| (endpoint.role, (index, endpoint)))
        .collect::<BTreeMap<_, _>>();
    let first_launch = observation
        .launches
        .first()
        .map(|launch| launch.created_suspended_at)
        .unwrap_or(observation.terminal.observed_at);
    let mut prior_index = None;
    let mut listener_ids = BTreeSet::new();
    for socket in &observation.sockets {
        let (index, endpoint) = policy_by_role
            .get(&socket.role)
            .ok_or_else(|| SupervisorError::new("authority_abort_socket_unexpected"))?;
        let owner = core[role_index(endpoint.owner_role)]
            .ok_or_else(|| SupervisorError::new("authority_abort_socket_owner_missing"))?;
        let owner_process = observation
            .processes
            .iter()
            .find(|process| process.key == owner)
            .ok_or_else(|| SupervisorError::new("authority_abort_socket_owner_missing"))?;
        if prior_index.is_some_and(|prior| *index <= prior)
            || socket.local_port != endpoint.local_port
            || socket.prelaunch_idle_observed_at < observation.ticket_consumed_at
            || socket.prelaunch_idle_observed_at >= first_launch
            || socket.prelaunch_competing_owner.is_some()
            || socket.listener_socket_id == 0
            || !listener_ids.insert(socket.listener_socket_id)
            || socket.owner != owner
            || socket.owner_job_object_id != policy.job_object_id
            || socket.owner_executable_digest != owner_process.executable_digest
            || socket.owner_image_identity != owner_process.image_handle_identity
            || socket.driver_binding_digest != endpoint.driver_binding_digest
            || !socket.loopback_v4_only
            || !socket.exclusive_address_use
            || !socket.address_reuse_disabled
            || socket.listener_ready_at < owner_process.started_at
            || socket.listener_ready_at >= policy.deadline
            || socket.ownership_verifications.is_empty()
            || socket.ownership_verifications.len() > SOCKET_VERIFICATION_PHASES.len()
        {
            return Err(SupervisorError::new("authority_abort_socket_invalid"));
        }
        let mut prior_time = socket.listener_ready_at.saturating_sub(1);
        for (expected_phase, verification) in SOCKET_VERIFICATION_PHASES
            .iter()
            .zip(&socket.ownership_verifications)
        {
            if verification.phase != *expected_phase
                || verification.observed_at <= prior_time
                || verification.observed_at > observation.cleanup.observed_at
                || verification.owner != socket.owner
                || verification.owner_job_object_id != policy.job_object_id
                || verification.owner_executable_digest != socket.owner_executable_digest
                || verification.owner_image_identity != socket.owner_image_identity
                || !verification.listening
                || !verification.exclusive_address_use
                || !verification.address_reuse_disabled
            {
                return Err(SupervisorError::new("authority_abort_socket_owner_drift"));
            }
            prior_time = verification.observed_at;
        }
        prior_index = Some(*index);
    }
    Ok(())
}

fn validate_abort_cleanup(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedAbortObservation,
    expected_processes: &BTreeSet<ProcessKey>,
) -> Result<(), SupervisorError> {
    let cleanup = &observation.cleanup;
    if cleanup.observed_at < observation.terminal.intent_recorded_at
        || !cleanup.job_terminated
        || !cleanup.job_handle_closed
        || !cleanup.no_live_descendants
        || !cleanup.all_process_handles_closed
        || !cleanup.all_file_handles_closed
        || !cleanup.private_root_removed
        || !cleanup.disposable_project_removed
        || !cleanup.runner_profile_removed
        || cleanup.final_result_persisted
        || !cleanup.unknown_processes.is_empty()
        || !cleanup.unknown_artifacts.is_empty()
        || !cleanup.unknown_listeners.is_empty()
    {
        return Err(SupervisorError::new("authority_abort_cleanup_residue"));
    }
    let exited = cleanup
        .exited_processes
        .iter()
        .copied()
        .collect::<BTreeSet<_>>();
    if exited != *expected_processes || cleanup.exited_processes.len() != expected_processes.len() {
        return Err(SupervisorError::new(
            "authority_abort_cleanup_process_mismatch",
        ));
    }
    let created_artifacts = observation
        .artifacts
        .iter()
        .map(|artifact| artifact.binding_digest)
        .collect::<BTreeSet<_>>();
    let deleted_artifacts = cleanup
        .deleted_private_artifacts
        .iter()
        .copied()
        .collect::<BTreeSet<_>>();
    if created_artifacts != deleted_artifacts
        || cleanup.deleted_private_artifacts.len() != created_artifacts.len()
    {
        return Err(SupervisorError::new(
            "authority_abort_cleanup_artifact_mismatch",
        ));
    }
    if cleanup.sockets.len() != policy.socket_policies.len() {
        return Err(SupervisorError::new(
            "authority_abort_cleanup_socket_mismatch",
        ));
    }
    for (endpoint, cleanup_socket) in policy.socket_policies.iter().zip(&cleanup.sockets) {
        let original = observation
            .sockets
            .iter()
            .find(|socket| socket.role == endpoint.role);
        if cleanup_socket.role != endpoint.role
            || cleanup_socket.local_port != endpoint.local_port
            || cleanup_socket.closed_listener_socket_id
                != original.map(|socket| socket.listener_socket_id)
            || cleanup_socket.listener_exit_observed_at < observation.terminal.intent_recorded_at
            || cleanup_socket.exclusive_rebind_observed_at
                < cleanup_socket.listener_exit_observed_at
            || cleanup_socket.exclusive_rebind_observed_at > cleanup.observed_at
            || !cleanup_socket.exclusive_rebind_succeeded
            || cleanup_socket.rebound_socket_object_id == 0
            || cleanup_socket
                .closed_listener_socket_id
                .is_some_and(|closed| closed == cleanup_socket.rebound_socket_object_id)
            || cleanup_socket.competing_owner.is_some()
            || !cleanup_socket.rebound_handle_closed
        {
            return Err(SupervisorError::new(
                "authority_abort_cleanup_socket_mismatch",
            ));
        }
    }
    Ok(())
}

fn derive_abort_cleanup_receipt(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedAbortObservation,
    reason: BurnReason,
) -> Digest {
    let mut digest = Sha256::new();
    digest.update(CLEANUP_RECEIPT_DOMAIN);
    digest.update(policy.authority_identity_digest);
    digest.update(policy.ticket_digest);
    digest.update(policy.run_binding_digest);
    digest.update([burn_reason_code(reason)]);
    digest.update(observation.terminal.intent_recorded_at.to_be_bytes());
    digest.update(observation.cleanup.observed_at.to_be_bytes());
    let mut exited = observation.cleanup.exited_processes.clone();
    exited.sort_unstable();
    for process in exited {
        digest.update(process.pid.to_be_bytes());
        digest.update(process.creation_time.to_be_bytes());
    }
    for artifact in &observation.cleanup.deleted_private_artifacts {
        digest.update(artifact);
    }
    for socket in &observation.cleanup.sockets {
        digest.update([socket_role_code(socket.role)]);
        digest.update(socket.local_port.to_be_bytes());
        digest.update(socket.rebound_socket_object_id.to_be_bytes());
    }
    digest.finalize().into()
}

fn validate_policy(policy: &SupervisorPolicy) -> Result<(), SupervisorError> {
    if is_zero_digest(&policy.authority_identity_digest)
        || is_zero_digest(&policy.ticket_digest)
        || is_zero_digest(&policy.run_binding_digest)
        || is_zero_digest(&policy.service_instance_digest)
        || is_zero_digest(&policy.runner_policy_digest)
        || is_zero_digest(&policy.runner_identity_digest)
        || is_zero_digest(&policy.runner_account_digest)
        || is_zero_digest(&policy.runner_profile_digest)
        || is_zero_digest(&policy.inherited_handle_allowlist_digest)
        || is_zero_digest(&policy.deterministic_job_name_digest)
        || is_zero_digest(&policy.private_root_binding_digest)
        || policy.issued_at == 0
        || policy.deadline <= policy.issued_at
        || policy.authority_process.pid == 0
        || policy.authority_process.creation_time == 0
        || policy.authority_parent_process.pid == 0
        || policy.authority_parent_process.creation_time == 0
        || policy.authority_parent_process == policy.authority_process
        || policy.authority_parent_process.creation_time >= policy.authority_process.creation_time
        || policy.job_object_id == 0
        || policy.process_executable_digests.iter().any(is_zero_digest)
    {
        return Err(SupervisorError::new("authority_supervisor_policy_invalid"));
    }
    if policy.artifacts.is_empty() {
        return Err(SupervisorError::new("authority_artifact_policy_empty"));
    }
    let mut bindings = BTreeSet::new();
    let mut input_count = 0usize;
    let mut output_count = 0usize;
    for artifact in &policy.artifacts {
        if is_zero_digest(&artifact.binding_digest)
            || !bindings.insert(artifact.binding_digest)
            || artifact
                .expected_content_digest
                .as_ref()
                .is_some_and(is_zero_digest)
        {
            return Err(SupervisorError::new("authority_artifact_policy_invalid"));
        }
        match artifact.direction {
            ArtifactDirection::Input => {
                input_count += 1;
                if artifact.expected_content_digest.is_none() {
                    return Err(SupervisorError::new("authority_input_digest_missing"));
                }
            }
            ArtifactDirection::Output => {
                output_count += 1;
                if artifact.expected_content_digest.is_some() {
                    return Err(SupervisorError::new("authority_output_digest_predeclared"));
                }
            }
        }
    }
    if input_count == 0 || output_count == 0 {
        return Err(SupervisorError::new(
            "authority_artifact_direction_incomplete",
        ));
    }
    let socket_roles = [SocketRole::App, SocketRole::Bridge];
    if policy.socket_policies.len() != socket_roles.len() {
        return Err(SupervisorError::new("authority_endpoint_policy_incomplete"));
    }
    let mut ports = BTreeSet::new();
    for (expected_role, endpoint) in socket_roles.iter().zip(&policy.socket_policies) {
        if endpoint.role != *expected_role
            || endpoint.owner_role != expected_role.expected_owner()
            || is_zero_digest(&endpoint.driver_binding_digest)
            || endpoint.local_port == 0
            || !ports.insert(endpoint.local_port)
        {
            return Err(SupervisorError::new("authority_endpoint_policy_invalid"));
        }
        match (endpoint.role, endpoint.mode) {
            (SocketRole::App, SocketEndpointMode::FixedFixture)
                if endpoint.local_port == APP_LOOPBACK_PORT => {}
            (SocketRole::Bridge, SocketEndpointMode::FixedFixture)
                if endpoint.local_port == BRIDGE_LOOPBACK_PORT => {}
            (SocketRole::Bridge, SocketEndpointMode::ServiceSelectedPrivate)
                if (49_152..=65_535).contains(&endpoint.local_port) => {}
            _ => return Err(SupervisorError::new("authority_endpoint_policy_invalid")),
        }
    }
    let mut helper_bindings = BTreeSet::new();
    for helper in &policy.helper_policies {
        if is_zero_digest(&helper.binding_digest)
            || is_zero_digest(&helper.executable_digest)
            || !helper_bindings.insert(helper.binding_digest)
            || helper.max_instances == 0
            || helper.allowed_exit_codes.is_empty()
            || helper.allowed_parent_executable_digests.is_empty()
            || helper
                .allowed_parent_executable_digests
                .iter()
                .any(is_zero_digest)
        {
            return Err(SupervisorError::new("authority_helper_policy_invalid"));
        }
        let unique_parents = helper
            .allowed_parent_executable_digests
            .iter()
            .copied()
            .collect::<BTreeSet<_>>();
        if unique_parents.len() != helper.allowed_parent_executable_digests.len() {
            return Err(SupervisorError::new("authority_helper_policy_invalid"));
        }
        if helper
            .allowed_exit_codes
            .iter()
            .copied()
            .collect::<BTreeSet<_>>()
            .len()
            != helper.allowed_exit_codes.len()
        {
            return Err(SupervisorError::new("authority_helper_policy_invalid"));
        }
    }
    let recomputed = canonical_supervisor_policy_digest(policy);
    if recomputed != policy.runner_policy_digest {
        return Err(SupervisorError::new(
            "authority_runner_policy_digest_mismatch",
        ));
    }
    if derive_run_binding_digest(
        &policy.authority_identity_digest,
        &policy.ticket_digest,
        &policy.service_instance_digest,
        &policy.runner_policy_digest,
    ) != policy.run_binding_digest
    {
        return Err(SupervisorError::new("authority_run_binding_mismatch"));
    }
    Ok(())
}

fn validate_runner(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedRunObservation,
) -> Result<(), SupervisorError> {
    validate_runner_observation(policy, observation.ticket_consumed_at, &observation.runner)
}

fn validate_runner_observation(
    policy: &SupervisorPolicy,
    ticket_consumed_at: u64,
    runner: &RunnerIdentityObservation,
) -> Result<(), SupervisorError> {
    if runner.validated_at < ticket_consumed_at || runner.validated_at >= policy.deadline {
        return Err(SupervisorError::new(
            "authority_runner_validation_time_invalid",
        ));
    }
    if runner.identity_digest != policy.runner_identity_digest
        || runner.account_digest != policy.runner_account_digest
        || runner.profile_digest != policy.runner_profile_digest
    {
        return Err(SupervisorError::new("authority_runner_identity_mismatch"));
    }
    if !runner.dedicated_account
        || !runner.restricted_token
        || !runner.batch_logon
        || runner.administrator_member
        || runner.elevated
        || runner.interactive_user_identity
        || runner.service_identity
        || runner.network_credentials_present
        || !runner.service_owned_profile
        || runner.profile_is_reparse_point
    {
        return Err(SupervisorError::new("authority_runner_not_isolated"));
    }
    Ok(())
}

fn validate_artifacts(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedRunObservation,
) -> Result<(), SupervisorError> {
    if observation.artifacts.len() != policy.artifacts.len() {
        return Err(SupervisorError::new("authority_artifact_set_mismatch"));
    }
    let first_launch = observation
        .launches
        .iter()
        .map(|item| item.created_suspended_at)
        .min()
        .ok_or_else(|| SupervisorError::new("authority_root_launch_missing"))?;
    let mut service_handles = BTreeSet::new();
    let mut candidate_handles = BTreeSet::new();
    let mut private_files = BTreeSet::new();
    for (expected, actual) in policy.artifacts.iter().zip(&observation.artifacts) {
        if actual.binding_digest != expected.binding_digest
            || actual.direction != expected.direction
        {
            return Err(SupervisorError::new("authority_artifact_order_mismatch"));
        }
        if actual.created_at < observation.ticket_consumed_at || actual.created_at >= first_launch {
            return Err(SupervisorError::new(
                "authority_artifact_creation_time_invalid",
            ));
        }
        if actual.service_handle_id == 0
            || actual.candidate_handle_id == 0
            || !service_handles.insert(actual.service_handle_id)
            || !candidate_handles.insert(actual.candidate_handle_id)
            || !private_files.insert(actual.private_identity)
            || is_zero_digest(&actual.content_digest)
        {
            return Err(SupervisorError::new("authority_artifact_handle_invalid"));
        }
        if !actual.created_new_private_copy
            || !actual.service_owned_parent
            || actual.parent_is_reparse_point
            || !actual.candidate_handle_explicitly_inherited
            || !actual.service_handle_held_through_terminal
            || !actual.content_digest_read_from_service_handle
        {
            return Err(SupervisorError::new("authority_private_copy_not_protected"));
        }
        if actual.private_identity != actual.candidate_handle_identity
            || actual.private_identity != actual.path_identity_at_terminal
        {
            return Err(SupervisorError::new("authority_private_copy_replaced"));
        }
        match actual.direction {
            ArtifactDirection::Input => {
                let source_identity = actual
                    .source_identity
                    .ok_or_else(|| SupervisorError::new("authority_input_source_missing"))?;
                if source_identity == actual.private_identity {
                    return Err(SupervisorError::new("authority_input_not_privately_copied"));
                }
                if expected.expected_content_digest != Some(actual.content_digest) {
                    return Err(SupervisorError::new("authority_input_digest_mismatch"));
                }
                if actual.content_length == 0 {
                    return Err(SupervisorError::new("authority_input_empty"));
                }
            }
            ArtifactDirection::Output => {
                if actual.source_identity.is_some() {
                    return Err(SupervisorError::new("authority_output_source_unexpected"));
                }
                if observation.terminal.kind == TerminalKind::Completed
                    && actual.content_length == 0
                {
                    return Err(SupervisorError::new("authority_output_empty"));
                }
            }
        }
    }
    Ok(())
}

struct ValidatedProcessGraph {
    core: [ProcessKey; PROCESS_ROLES.len()],
    all_candidates: BTreeSet<ProcessKey>,
}

fn validate_processes(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedRunObservation,
) -> Result<ValidatedProcessGraph, SupervisorError> {
    if observation.processes.len() != PROCESS_ROLES.len() {
        return Err(SupervisorError::new("authority_process_graph_incomplete"));
    }
    let mut keys = BTreeSet::new();
    let mut pids = BTreeSet::new();
    let mut by_role = [ProcessKey {
        pid: 0,
        creation_time: 0,
    }; PROCESS_ROLES.len()];
    for (index, actual) in observation.processes.iter().enumerate() {
        let role = PROCESS_ROLES[index];
        if actual.role != role {
            return Err(SupervisorError::new("authority_process_role_order_invalid"));
        }
        if actual.key.pid == 0
            || actual.key.creation_time == 0
            || !keys.insert(actual.key)
            || !pids.insert(actual.key.pid)
            || actual.started_at == 0
            || actual.executable_digest != policy.process_executable_digests[index]
            || !actual.executable_digest_read_from_image_handle
        {
            return Err(SupervisorError::new("authority_process_identity_invalid"));
        }
        if actual.image_handle_identity != actual.image_path_identity_at_terminal {
            return Err(SupervisorError::new("authority_process_image_replaced"));
        }
        if !actual.image_handle_held_through_terminal
            || !actual.process_handle_held_through_cleanup_begin
        {
            return Err(SupervisorError::new("authority_process_handle_not_held"));
        }
        if role == ProcessRole::AuthorityService {
            if actual.key != policy.authority_process
                || actual.parent_pid != policy.authority_parent_process.pid
                || actual.parent_creation_time != policy.authority_parent_process.creation_time
                || actual.supervisor_pid != 0
                || actual.runner_identity_digest.is_some()
                || actual.job_object_id.is_some()
                || actual.job_member
                || actual.breakaway_allowed
                || actual.started_at > policy.issued_at
            {
                return Err(SupervisorError::new("authority_service_identity_invalid"));
            }
        } else {
            if actual.started_at < observation.ticket_consumed_at
                || actual.started_at >= policy.deadline
                || actual.runner_identity_digest != Some(policy.runner_identity_digest)
                || actual.job_object_id != Some(policy.job_object_id)
                || !actual.job_member
                || actual.breakaway_allowed
            {
                return Err(SupervisorError::new("authority_candidate_process_invalid"));
            }
            if observation.terminal.kind == TerminalKind::Completed && !actual.alive_at_finalization
            {
                return Err(SupervisorError::new(
                    "authority_process_exited_before_finalization",
                ));
            }
        }
        by_role[index] = actual.key;
    }
    for (index, actual) in observation.processes.iter().enumerate() {
        let role = PROCESS_ROLES[index];
        match role.expected_parent() {
            Some(parent) => {
                let parent_index = role_index(parent);
                if actual.parent_pid != by_role[parent_index].pid
                    || actual.parent_creation_time != by_role[parent_index].creation_time
                    || actual.started_at < observation.processes[parent_index].started_at
                {
                    return Err(SupervisorError::new("authority_process_parent_mismatch"));
                }
            }
            None if role == ProcessRole::AuthorityService => {}
            None => return Err(SupervisorError::new("authority_process_parent_mismatch")),
        }
        match role.expected_supervisor() {
            Some(supervisor) if actual.supervisor_pid != by_role[role_index(supervisor)].pid => {
                return Err(SupervisorError::new(
                    "authority_process_supervisor_mismatch",
                ));
            }
            None if actual.supervisor_pid != 0 => {
                return Err(SupervisorError::new(
                    "authority_process_supervisor_mismatch",
                ));
            }
            _ => {}
        }
    }
    let mut executable_by_process = BTreeMap::new();
    for (index, key) in by_role.iter().enumerate() {
        executable_by_process.insert(*key, policy.process_executable_digests[index]);
    }
    let policies = policy
        .helper_policies
        .iter()
        .map(|item| (item.binding_digest, item))
        .collect::<BTreeMap<_, _>>();
    let mut helper_counts = BTreeMap::<Digest, u32>::new();
    let mut helper_keys = BTreeSet::new();
    for helper in &observation.helpers {
        let helper_policy = policies
            .get(&helper.policy_binding_digest)
            .ok_or_else(|| SupervisorError::new("authority_helper_process_unexpected"))?;
        let count = helper_counts
            .entry(helper.policy_binding_digest)
            .or_insert(0);
        *count = count.saturating_add(1);
        let parent_executable = executable_by_process
            .get(&helper.parent)
            .ok_or_else(|| SupervisorError::new("authority_helper_parent_unknown"))?;
        if *count > helper_policy.max_instances
            || !helper_policy
                .allowed_parent_executable_digests
                .contains(parent_executable)
            || helper.key.pid == 0
            || helper.key.creation_time == 0
            || !keys.insert(helper.key)
            || !pids.insert(helper.key.pid)
            || helper.supervisor_pid != helper.parent.pid
            || helper.started_at < observation.ticket_consumed_at
            || helper.started_at >= policy.deadline
            || helper.executable_digest != helper_policy.executable_digest
            || !helper.executable_digest_read_from_image_handle
            || helper.image_handle_identity != helper.image_path_identity_at_terminal
            || helper.runner_identity_digest != policy.runner_identity_digest
            || helper.job_object_id != policy.job_object_id
            || !helper.job_member
            || helper.breakaway_allowed
            || !helper.image_handle_held_through_terminal
            || !helper.process_handle_held_through_cleanup_begin
            || !helper.completion_port_exit_observed
            || helper.exited_at < helper.started_at
            || helper.exited_at > observation.cleanup.observed_at
        {
            return Err(SupervisorError::new("authority_helper_process_invalid"));
        }
        if helper.terminated_by_job {
            if helper.exit_code.is_some()
                || helper.exited_at < observation.terminal.intent_recorded_at
            {
                return Err(SupervisorError::new("authority_helper_exit_invalid"));
            }
        } else if !helper
            .exit_code
            .is_some_and(|code| helper_policy.allowed_exit_codes.contains(&code))
        {
            return Err(SupervisorError::new("authority_helper_exit_invalid"));
        }
        if observation.terminal.kind == TerminalKind::Completed {
            let finalized_at = observation
                .finalization
                .as_ref()
                .ok_or_else(|| SupervisorError::new("authority_finalization_missing"))?
                .finalized_at;
            if helper.alive_at_finalization != (helper.exited_at >= finalized_at) {
                return Err(SupervisorError::new("authority_helper_liveness_invalid"));
            }
        }
        executable_by_process.insert(helper.key, helper.executable_digest);
        helper_keys.insert(helper.key);
    }
    let mut all_candidates = CANDIDATE_ROLES
        .iter()
        .map(|role| by_role[role_index(*role)])
        .collect::<BTreeSet<_>>();
    all_candidates.extend(helper_keys);
    Ok(ValidatedProcessGraph {
        core: by_role,
        all_candidates,
    })
}

fn validate_launches(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedRunObservation,
    process_by_role: &[ProcessKey; PROCESS_ROLES.len()],
) -> Result<(), SupervisorError> {
    if observation.launches.len() != ROOT_ROLES.len() {
        return Err(SupervisorError::new("authority_root_launch_set_invalid"));
    }
    for (expected_role, launch) in ROOT_ROLES.iter().zip(&observation.launches) {
        if launch.role != *expected_role {
            return Err(SupervisorError::new("authority_root_launch_order_invalid"));
        }
        if launch.created_suspended_at < observation.runner.validated_at
            || launch.created_suspended_at >= launch.assigned_to_job_at
            || launch.assigned_to_job_at >= launch.resumed_at
            || launch.resumed_at >= policy.deadline
        {
            return Err(SupervisorError::new("authority_launch_sequence_invalid"));
        }
        if launch.job_object_id != policy.job_object_id
            || launch.runner_identity_digest != policy.runner_identity_digest
            || launch.inherited_handle_allowlist_digest != policy.inherited_handle_allowlist_digest
            || !launch.all_other_handles_non_inheritable
            || launch.breakaway_requested
        {
            return Err(SupervisorError::new("authority_launch_policy_mismatch"));
        }
        let process = &observation.processes[role_index(*expected_role)];
        if process.key != process_by_role[role_index(*expected_role)]
            || process.started_at < launch.resumed_at
        {
            return Err(SupervisorError::new(
                "authority_root_launch_identity_mismatch",
            ));
        }
    }
    Ok(())
}

fn validate_job(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedRunObservation,
    expected_processes: &BTreeSet<ProcessKey>,
) -> Result<(), SupervisorError> {
    let job = &observation.job;
    if job.object_id != policy.job_object_id
        || !job.kill_on_job_close
        || job.breakaway_allowed
        || job.silent_breakaway_allowed
        || job.active_process_limit != 0
        || !job.completion_port_supervised
        || !job.assignment_history_complete
        || !job.handle_held_through_cleanup_begin
    {
        return Err(SupervisorError::new("authority_job_policy_mismatch"));
    }
    let observed = job
        .assigned_processes
        .iter()
        .copied()
        .collect::<BTreeSet<_>>();
    if job.assigned_processes.len() != expected_processes.len() || observed != *expected_processes {
        return Err(SupervisorError::new("authority_job_unexpected_process"));
    }
    Ok(())
}

fn validate_sockets(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedRunObservation,
    process_by_role: &[ProcessKey; PROCESS_ROLES.len()],
) -> Result<(), SupervisorError> {
    if observation.sockets.len() != policy.socket_policies.len() {
        return Err(SupervisorError::new("authority_socket_set_incomplete"));
    }
    let first_launch = observation
        .launches
        .iter()
        .map(|item| item.created_suspended_at)
        .min()
        .ok_or_else(|| SupervisorError::new("authority_root_launch_missing"))?;
    let mut socket_ids = BTreeSet::new();
    for (endpoint, socket) in policy.socket_policies.iter().zip(&observation.sockets) {
        if socket.role != endpoint.role
            || socket.local_port != endpoint.local_port
            || socket.prelaunch_idle_observed_at < observation.ticket_consumed_at
            || socket.prelaunch_idle_observed_at >= first_launch
            || socket.prelaunch_competing_owner.is_some()
            || socket.listener_ready_at >= policy.deadline
            || socket.listener_socket_id == 0
            || !socket_ids.insert(socket.listener_socket_id)
        {
            return Err(SupervisorError::new("authority_socket_identity_invalid"));
        }
        let owner_role = endpoint.owner_role;
        let owner = process_by_role[role_index(owner_role)];
        let owner_process = &observation.processes[role_index(owner_role)];
        if socket.owner != owner
            || socket.owner_job_object_id != policy.job_object_id
            || socket.owner_executable_digest
                != policy.process_executable_digests[role_index(owner_role)]
            || socket.owner_image_identity != owner_process.image_handle_identity
            || socket.listener_ready_at < owner_process.started_at
        {
            return Err(SupervisorError::new("authority_socket_owner_mismatch"));
        }
        if socket.driver_binding_digest != endpoint.driver_binding_digest
            || !socket.loopback_v4_only
            || !socket.exclusive_address_use
            || !socket.address_reuse_disabled
        {
            return Err(SupervisorError::new("authority_socket_not_protected"));
        }
        if socket.ownership_verifications.len() != SOCKET_VERIFICATION_PHASES.len() {
            return Err(SupervisorError::new(
                "authority_socket_verification_incomplete",
            ));
        }
        let mut prior_time = socket.listener_ready_at.saturating_sub(1);
        for (expected_phase, verification) in SOCKET_VERIFICATION_PHASES
            .iter()
            .zip(&socket.ownership_verifications)
        {
            if verification.phase != *expected_phase
                || verification.observed_at <= prior_time
                || verification.observed_at > observation.cleanup.observed_at
                || verification.owner != socket.owner
                || verification.owner_job_object_id != policy.job_object_id
                || verification.owner_executable_digest != socket.owner_executable_digest
                || verification.owner_image_identity != socket.owner_image_identity
                || !verification.listening
                || !verification.exclusive_address_use
                || !verification.address_reuse_disabled
            {
                return Err(SupervisorError::new("authority_socket_owner_drift"));
            }
            prior_time = verification.observed_at;
        }
        if socket.ownership_verifications[0].observed_at < socket.listener_ready_at
            || socket.ownership_verifications[3].observed_at
                < observation.terminal.intent_recorded_at
            || socket.ownership_verifications[4].observed_at > observation.cleanup.observed_at
        {
            return Err(SupervisorError::new(
                "authority_socket_verification_time_invalid",
            ));
        }
    }
    Ok(())
}

fn validate_terminal(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedRunObservation,
) -> Result<(), SupervisorError> {
    let terminal = &observation.terminal;
    if terminal.observed_at < observation.ticket_consumed_at
        || terminal.intent_recorded_at < terminal.observed_at
        || terminal.intent_recorded_at > observation.cleanup.observed_at
    {
        return Err(SupervisorError::new("authority_terminal_time_invalid"));
    }
    match terminal.kind {
        TerminalKind::Completed => {
            if terminal.observed_at > policy.deadline
                || terminal.intent != TerminalIntent::CommitResult
            {
                return Err(SupervisorError::new("authority_completed_intent_invalid"));
            }
            let finalization = observation
                .finalization
                .as_ref()
                .ok_or_else(|| SupervisorError::new("authority_finalization_missing"))?;
            if finalization.source != FinalizationSource::AuthorityHeldOutputHandles
                || finalization.caller_report_present
                || !finalization.read_directly_from_held_handles
                || !finalization.retained_until_cleanup_complete
            {
                return Err(SupervisorError::new(
                    "authority_finalization_source_untrusted",
                ));
            }
            if finalization.ticket_digest != policy.ticket_digest
                || finalization.run_binding_digest != policy.run_binding_digest
            {
                return Err(SupervisorError::new(
                    "authority_finalization_binding_mismatch",
                ));
            }
            let ready_at = observation
                .sockets
                .iter()
                .map(|item| item.listener_ready_at)
                .max()
                .ok_or_else(|| SupervisorError::new("authority_socket_set_incomplete"))?;
            if finalization.finalized_at < ready_at
                || finalization.finalized_at > terminal.observed_at
                || finalization.finalized_at > policy.deadline
            {
                return Err(SupervisorError::new("authority_finalization_time_invalid"));
            }
            let expected_outputs = policy
                .artifacts
                .iter()
                .filter(|item| item.direction == ArtifactDirection::Output)
                .map(|item| item.binding_digest)
                .collect::<Vec<_>>();
            if finalization.output_binding_digests != expected_outputs {
                return Err(SupervisorError::new(
                    "authority_finalization_output_mismatch",
                ));
            }
            let result_artifact = observation
                .artifacts
                .iter()
                .find(|item| {
                    item.direction == ArtifactDirection::Output
                        && item.binding_digest == finalization.canonical_result_binding_digest
                })
                .ok_or_else(|| SupervisorError::new("authority_canonical_result_unbound"))?;
            let computed_result_digest: Digest =
                Sha256::digest(&finalization.canonical_result_bytes).into();
            if finalization.canonical_result_bytes.is_empty()
                || finalization.canonical_result_bytes.len() > MAX_CANONICAL_RESULT_BYTES
                || computed_result_digest != finalization.canonical_result_digest
                || result_artifact.content_digest != finalization.canonical_result_digest
                || result_artifact.content_length
                    != u64::try_from(finalization.canonical_result_bytes.len()).unwrap_or(u64::MAX)
            {
                return Err(SupervisorError::new("authority_canonical_result_mismatch"));
            }
        }
        TerminalKind::Cancelled => {
            if terminal.observed_at >= policy.deadline
                || terminal.intent != TerminalIntent::Burn
                || observation.finalization.is_some()
            {
                return Err(SupervisorError::new("authority_cancel_intent_invalid"));
            }
        }
        TerminalKind::TimedOut => {
            if terminal.observed_at < policy.deadline
                || terminal.intent != TerminalIntent::Burn
                || observation.finalization.is_some()
            {
                return Err(SupervisorError::new("authority_timeout_intent_invalid"));
            }
        }
        TerminalKind::Failed => {
            if terminal.observed_at > policy.deadline
                || terminal.intent != TerminalIntent::Burn
                || observation.finalization.is_some()
            {
                return Err(SupervisorError::new("authority_failed_intent_invalid"));
            }
        }
    }
    Ok(())
}

fn validate_cleanup(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedRunObservation,
    expected_processes: &BTreeSet<ProcessKey>,
) -> Result<(), SupervisorError> {
    let cleanup = &observation.cleanup;
    if cleanup.observed_at < observation.terminal.intent_recorded_at {
        return Err(SupervisorError::new("authority_cleanup_time_invalid"));
    }
    if !cleanup.job_terminated
        || !cleanup.job_handle_closed
        || !cleanup.no_live_descendants
        || !cleanup.all_process_handles_closed
        || !cleanup.all_file_handles_closed
        || !cleanup.private_root_removed
        || !cleanup.disposable_project_removed
        || !cleanup.runner_profile_removed
        || !cleanup.unknown_processes.is_empty()
        || !cleanup.unknown_artifacts.is_empty()
        || !cleanup.unknown_listeners.is_empty()
    {
        return Err(SupervisorError::new("authority_cleanup_residue"));
    }
    if observation.terminal.kind == TerminalKind::Completed {
        if !cleanup.final_result_persisted {
            return Err(SupervisorError::new("authority_result_not_persisted"));
        }
    } else if cleanup.final_result_persisted {
        return Err(SupervisorError::new("authority_burned_result_unexpected"));
    }
    let exited = cleanup
        .exited_processes
        .iter()
        .copied()
        .collect::<BTreeSet<_>>();
    if cleanup.exited_processes.len() != expected_processes.len() || exited != *expected_processes {
        return Err(SupervisorError::new("authority_cleanup_process_mismatch"));
    }
    let expected_artifacts = policy
        .artifacts
        .iter()
        .map(|item| item.binding_digest)
        .collect::<BTreeSet<_>>();
    let deleted_artifacts = cleanup
        .deleted_private_artifacts
        .iter()
        .copied()
        .collect::<BTreeSet<_>>();
    if cleanup.deleted_private_artifacts.len() != expected_artifacts.len()
        || deleted_artifacts != expected_artifacts
    {
        return Err(SupervisorError::new("authority_cleanup_artifact_mismatch"));
    }
    if cleanup.sockets.len() != policy.socket_policies.len() {
        return Err(SupervisorError::new("authority_cleanup_socket_mismatch"));
    }
    for ((endpoint, actual), original) in policy
        .socket_policies
        .iter()
        .zip(&cleanup.sockets)
        .zip(&observation.sockets)
    {
        if actual.role != endpoint.role
            || actual.local_port != endpoint.local_port
            || original.role != endpoint.role
            || original.local_port != endpoint.local_port
            || actual.closed_listener_socket_id != Some(original.listener_socket_id)
            || actual.listener_exit_observed_at < observation.terminal.intent_recorded_at
            || actual.exclusive_rebind_observed_at < actual.listener_exit_observed_at
            || actual.exclusive_rebind_observed_at > cleanup.observed_at
            || !actual.exclusive_rebind_succeeded
            || actual.rebound_socket_object_id == 0
            || actual.rebound_socket_object_id == original.listener_socket_id
            || actual.competing_owner.is_some()
            || !actual.rebound_handle_closed
        {
            return Err(SupervisorError::new("authority_cleanup_socket_mismatch"));
        }
    }
    Ok(())
}

fn derive_cleanup_receipt(
    policy: &SupervisorPolicy,
    observation: &AuthorityOwnedRunObservation,
) -> Digest {
    let mut digest = Sha256::new();
    digest.update(CLEANUP_RECEIPT_DOMAIN);
    digest.update(policy.authority_identity_digest);
    digest.update(policy.ticket_digest);
    digest.update(policy.run_binding_digest);
    digest.update([match observation.terminal.kind {
        TerminalKind::Completed => 1,
        TerminalKind::Cancelled => 2,
        TerminalKind::TimedOut => 3,
        TerminalKind::Failed => 4,
    }]);
    digest.update(observation.terminal.observed_at.to_be_bytes());
    digest.update(observation.terminal.intent_recorded_at.to_be_bytes());
    digest.update(observation.cleanup.observed_at.to_be_bytes());
    digest.update(
        observation
            .finalization
            .as_ref()
            .map(|value| value.canonical_result_digest)
            .unwrap_or([0; 32]),
    );

    let mut exited = observation.cleanup.exited_processes.clone();
    exited.sort_unstable();
    digest.update((exited.len() as u64).to_be_bytes());
    for process in exited {
        digest.update(process.pid.to_be_bytes());
        digest.update(process.creation_time.to_be_bytes());
    }

    let mut artifacts = observation.cleanup.deleted_private_artifacts.clone();
    artifacts.sort_unstable();
    digest.update((artifacts.len() as u64).to_be_bytes());
    for artifact in artifacts {
        digest.update(artifact);
    }

    digest.update((observation.cleanup.sockets.len() as u64).to_be_bytes());
    for socket in &observation.cleanup.sockets {
        digest.update([match socket.role {
            SocketRole::App => 1,
            SocketRole::Bridge => 2,
        }]);
        digest.update(socket.local_port.to_be_bytes());
        digest.update(socket.closed_listener_socket_id.unwrap_or(0).to_be_bytes());
        digest.update(socket.listener_exit_observed_at.to_be_bytes());
        digest.update(socket.exclusive_rebind_observed_at.to_be_bytes());
        digest.update(socket.rebound_socket_object_id.to_be_bytes());
        digest.update([u8::from(socket.exclusive_rebind_succeeded)]);
        digest.update([u8::from(socket.competing_owner.is_none())]);
        digest.update([u8::from(socket.rebound_handle_closed)]);
    }
    for value in [
        observation.cleanup.job_terminated,
        observation.cleanup.job_handle_closed,
        observation.cleanup.no_live_descendants,
        observation.cleanup.all_process_handles_closed,
        observation.cleanup.all_file_handles_closed,
        observation.cleanup.private_root_removed,
        observation.cleanup.disposable_project_removed,
        observation.cleanup.runner_profile_removed,
        observation.cleanup.final_result_persisted,
    ] {
        digest.update([u8::from(value)]);
    }
    digest.finalize().into()
}

fn burn_reason_code(reason: BurnReason) -> u8 {
    match reason {
        BurnReason::Cancelled => 2,
        BurnReason::TimedOut => 3,
        BurnReason::Failed => 4,
        BurnReason::RestartRecovery => 5,
    }
}

#[cfg(test)]
fn test_cleanup_receipt(
    authority_identity_digest: &Digest,
    ticket_digest: &Digest,
    run_binding_digest: &Digest,
    result_digest: &Digest,
    result_or_reason: u8,
) -> Digest {
    let mut digest = Sha256::new();
    digest.update(TEST_CLEANUP_RECEIPT_DOMAIN);
    digest.update(authority_identity_digest);
    digest.update(ticket_digest);
    digest.update(run_binding_digest);
    digest.update(result_digest);
    digest.update([result_or_reason]);
    digest.finalize().into()
}

fn socket_role_code(role: SocketRole) -> u8 {
    match role {
        SocketRole::App => 1,
        SocketRole::Bridge => 2,
    }
}

fn process_role_code(role: ProcessRole) -> u8 {
    (role_index(role) + 1) as u8
}

fn decode_socket_role(value: u8) -> Result<SocketRole, SupervisorError> {
    match value {
        1 => Ok(SocketRole::App),
        2 => Ok(SocketRole::Bridge),
        _ => Err(SupervisorError::new("authority_policy_snapshot_invalid")),
    }
}

fn decode_process_role(value: u8) -> Result<ProcessRole, SupervisorError> {
    PROCESS_ROLES
        .get(usize::from(value).saturating_sub(1))
        .copied()
        .filter(|_| value != 0)
        .ok_or_else(|| SupervisorError::new("authority_policy_snapshot_invalid"))
}

fn take_u8(bytes: &[u8], offset: &mut usize) -> Result<u8, SupervisorError> {
    let value = *bytes
        .get(*offset)
        .ok_or_else(|| SupervisorError::new("authority_receipt_truncated"))?;
    *offset = (*offset).saturating_add(1);
    Ok(value)
}

fn take_u16(bytes: &[u8], offset: &mut usize) -> Result<u16, SupervisorError> {
    let end = offset.saturating_add(2);
    let slice = bytes
        .get(*offset..end)
        .ok_or_else(|| SupervisorError::new("authority_receipt_truncated"))?;
    let mut output = [0u8; 2];
    output.copy_from_slice(slice);
    *offset = end;
    Ok(u16::from_be_bytes(output))
}

fn take_count(bytes: &[u8], offset: &mut usize, maximum: usize) -> Result<usize, SupervisorError> {
    let value = usize::try_from(take_u32(bytes, offset)?)
        .map_err(|_| SupervisorError::new("authority_policy_snapshot_invalid"))?;
    if value > maximum {
        return Err(SupervisorError::new("authority_policy_snapshot_invalid"));
    }
    Ok(value)
}

fn take_digest(bytes: &[u8], offset: &mut usize) -> Result<Digest, SupervisorError> {
    let end = offset.saturating_add(32);
    let slice = bytes
        .get(*offset..end)
        .ok_or_else(|| SupervisorError::new("authority_receipt_truncated"))?;
    let mut output = [0u8; 32];
    output.copy_from_slice(slice);
    *offset = end;
    Ok(output)
}

fn take_array_16(bytes: &[u8], offset: &mut usize) -> Result<[u8; 16], SupervisorError> {
    let end = offset.saturating_add(16);
    let slice = bytes
        .get(*offset..end)
        .ok_or_else(|| SupervisorError::new("authority_receipt_truncated"))?;
    let mut output = [0u8; 16];
    output.copy_from_slice(slice);
    *offset = end;
    Ok(output)
}

fn take_u32(bytes: &[u8], offset: &mut usize) -> Result<u32, SupervisorError> {
    let end = offset.saturating_add(4);
    let slice = bytes
        .get(*offset..end)
        .ok_or_else(|| SupervisorError::new("authority_receipt_truncated"))?;
    let mut output = [0u8; 4];
    output.copy_from_slice(slice);
    *offset = end;
    Ok(u32::from_be_bytes(output))
}

fn take_u64(bytes: &[u8], offset: &mut usize) -> Result<u64, SupervisorError> {
    let end = offset.saturating_add(8);
    let slice = bytes
        .get(*offset..end)
        .ok_or_else(|| SupervisorError::new("authority_receipt_truncated"))?;
    let mut output = [0u8; 8];
    output.copy_from_slice(slice);
    *offset = end;
    Ok(u64::from_be_bytes(output))
}

fn role_index(role: ProcessRole) -> usize {
    match role {
        ProcessRole::AuthorityService => 0,
        ProcessRole::Driver => 1,
        ProcessRole::Desktop => 2,
        ProcessRole::Backend => 3,
        ProcessRole::Unity => 4,
        ProcessRole::BridgeLauncher => 5,
        ProcessRole::BridgeListener => 6,
    }
}

fn is_zero_digest(value: &Digest) -> bool {
    value.iter().all(|byte| *byte == 0)
}

#[cfg(test)]
#[path = "primitive_evidence_authority_supervisor/tests.rs"]
pub(crate) mod tests;
