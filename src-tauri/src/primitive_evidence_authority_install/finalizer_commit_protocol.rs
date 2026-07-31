//! Durable, actor-separated finalizer-owned commit protocol.
//!
//! This module is intentionally side-effect free. It defines canonical durable
//! receipts and capability-separated transition APIs, but performs no service,
//! process, security-descriptor, registry, or filesystem operations. Production
//! connection remains disabled until the native adapters persist and read back
//! every receipt through the protected authority store.

#![cfg_attr(not(test), allow(dead_code))]

#[cfg(test)]
use super::candidate_activation_orchestrator::CandidateActivationSealReadyProjection;
#[cfg(test)]
use super::runner_policy::RunnerPolicyStateDescriptor;
use super::{
    bootstrap_activation::CandidateProcessEvidence,
    candidate_activation_orchestrator::candidate_exact_service_identity_digest,
    security_policy::{
        RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS, RUNTIME_BLOB_FILE_AUTHORITY_ACCESS,
        RUNTIME_BLOB_FILE_CLEANUP_ACCESS, RUNTIME_BLOB_FILE_READ_ACCESS,
    },
    AuthorityMaintenanceError, AuthorityMaintenanceOperation, GENERATION_SEAL_OBJECT_COUNT,
    GENERATION_SEAL_TERMINAL_SEQUENCE,
};
#[cfg(windows)]
use super::{
    finalizer_generation_seal::GenerationSealTerminalAuthorization,
    finalizer_security_windows::{expected_security_digests, FinalizerSealTarget},
};
use crate::primitive_evidence_authority_windows::AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use sha2::{Digest as _, Sha256};

type RawDigest = [u8; 32];

pub(super) const FINALIZER_COMMIT_PROTOCOL_SCHEMA: &str =
    "vrcforge.authority.finalizer-commit-protocol.v4";
pub(super) const FINALIZER_COMMIT_RECEIPT_SCHEMA: &str =
    "vrcforge.authority.finalizer-commit-receipt.v4";
pub(super) const FINALIZER_COMMIT_STORE_SCHEMA: &str =
    "vrcforge.authority.finalizer-commit-store.v4";
pub(super) const FINAL_COMMIT_RECEIPT_LEAF: &str = "05-final-commit.receipt.json";
pub(super) const FINALIZER_COMMIT_PROTOCOL_PRODUCTION_ENABLED: bool = false;

const PROTOCOL_GENESIS_DOMAIN: &[u8] = b"vrcforge-authority-finalizer-commit-protocol-genesis-v4\0";
const PROTOCOL_STATE_DOMAIN: &[u8] = b"vrcforge-authority-finalizer-commit-protocol-state-v4\0";
const SERVICE_PROCESS_IDENTITY_DOMAIN: &[u8] =
    b"vrcforge-authority-finalizer-service-process-identity-v1\0";
const COMMIT_BINDING_IDENTITY_DOMAIN: &[u8] =
    b"vrcforge-authority-finalizer-commit-binding-identity-v4\0";
const NONCE_ARTIFACT_PAIR_IDENTITY_DOMAIN: &[u8] =
    b"vrcforge-authority-finalizer-nonce-artifact-pair-identity-v1\0";
const CANDIDATE_ACTIVATION_PROOF_DOMAIN: &[u8] =
    b"vrcforge-authority-finalizer-candidate-activation-proof-v1\0";
const PRECOMMIT_DORMANT_RUNTIME_DOMAIN: &[u8] =
    b"vrcforge-authority-finalizer-precommit-dormant-runtime-v1\0";
const FINAL_COMMIT_GATE_PROJECTION_DOMAIN: &[u8] =
    b"vrcforge-authority-finalizer-final-commit-gate-projection-v4\0";
const FINAL_COMMIT_GATE_DOMAIN: &[u8] = b"vrcforge-authority-finalizer-final-commit-gate-v4\0";
const FINAL_COMMIT_VALID_PREDICATE: &[u8] = b"canonical-valid-final-commit-envelope-and-chain";
const WORKER_ACTIVATION_PROOF_DOMAIN: &[u8] =
    b"vrcforge-authority-finalizer-worker-activation-proof-v1\0";
const ACTIVE_HEAD_CAS_PROJECTION_DOMAIN: &[u8] =
    b"vrcforge-authority-finalizer-active-head-cas-projection-v1\0";
const OPERATION_RESIDUE_PLAN_DOMAIN: &[u8] = b"vrcforge-authority-operation-residue-plan-v1\0";
const OPERATION_ZERO_RESIDUE_READBACK_DOMAIN: &[u8] =
    b"vrcforge-authority-operation-zero-residue-readback-v1\0";
const POSTCOMMIT_SERVING_READBACK_DOMAIN: &[u8] =
    b"vrcforge-authority-postcommit-serving-readback-v1\0";
const RECOVERED_POSTCOMMIT_SERVING_READBACK_DOMAIN: &[u8] =
    b"vrcforge-authority-recovered-postcommit-serving-readback-v1\0";
const PREVIOUS_RUNTIME_ABSENCE_READBACK_DOMAIN: &[u8] =
    b"vrcforge-authority-previous-runtime-absence-readback-v1\0";
const SERVICE_STATE_STOPPED: u32 = 1;
const MAX_EXPECTED_RUNNER_POLICY_STATE_BYTES: u64 = 64 * 1024;
const TRANSACTION_STARTED_RECEIPT_DOMAIN: &[u8] =
    b"vrcforge-authority-finalizer-transaction-started-receipt-v4\0";
const APPLY_READY_RECEIPT_DOMAIN: &[u8] = b"vrcforge-authority-finalizer-apply-ready-receipt-v4\0";
const SEAL_READY_RECEIPT_DOMAIN: &[u8] = b"vrcforge-authority-finalizer-seal-ready-receipt-v4\0";
const EXIT_READY_RECEIPT_DOMAIN: &[u8] = b"vrcforge-authority-finalizer-exit-ready-receipt-v4\0";
const SEAL_COMPLETE_RECEIPT_DOMAIN: &[u8] =
    b"vrcforge-authority-finalizer-seal-complete-receipt-v4\0";
const FINAL_COMMIT_RECEIPT_DOMAIN: &[u8] =
    b"vrcforge-authority-finalizer-final-commit-receipt-v4\0";
const PROTECTED_BLOB_NAMESPACE_SEAL_DOMAIN: &[u8] =
    b"vrcforge-authority-protected-blob-namespace-seal-v1\0";
const PROTECTED_BLOB_NAMESPACE_EMPTY_INVENTORY_DOMAIN: &[u8] =
    b"vrcforge-authority-protected-blob-namespace-empty-inventory-v1\0";
const PROTECTED_BLOB_NAMESPACE_OPEN_DISPOSITION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub(super) struct Digest32(RawDigest);

impl Digest32 {
    fn checked(value: RawDigest) -> Result<Self, AuthorityMaintenanceError> {
        if is_zero(&value) {
            return Err(identity_invalid());
        }
        Ok(Self(value))
    }

    fn into_inner(self) -> RawDigest {
        self.0
    }
}

impl Serialize for Digest32 {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(&hex_lower(&self.0))
    }
}

impl<'de> Deserialize<'de> for Digest32 {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        decode_lower_hex_exact::<32>(&value)
            .map(Self)
            .map_err(serde::de::Error::custom)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
struct FileId16([u8; 16]);

impl Serialize for FileId16 {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(&hex_lower(&self.0))
    }
}

impl<'de> Deserialize<'de> for FileId16 {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        decode_lower_hex_exact::<16>(&value)
            .map(Self)
            .map_err(serde::de::Error::custom)
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct RunnerPolicySealedIdentity {
    volume_serial: u64,
    file_id: FileId16,
    link_count: u32,
    attributes: u32,
}

impl RunnerPolicySealedIdentity {
    pub(super) fn new(
        volume_serial: u64,
        file_id: [u8; 16],
        link_count: u32,
        attributes: u32,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            volume_serial,
            file_id: FileId16(file_id),
            link_count,
            attributes,
        };
        value.validate()?;
        Ok(value)
    }

    pub(super) fn validate(self) -> Result<(), AuthorityMaintenanceError> {
        const FILE_ATTRIBUTE_DIRECTORY_BIT: u32 = 0x10;
        const FILE_ATTRIBUTE_REPARSE_POINT_BIT: u32 = 0x400;
        if self.volume_serial == 0
            || is_zero(&self.file_id.0)
            || self.link_count != 1
            || self.attributes == 0
            || self.attributes & (FILE_ATTRIBUTE_DIRECTORY_BIT | FILE_ATTRIBUTE_REPARSE_POINT_BIT)
                != 0
        {
            return Err(AuthorityMaintenanceError(
                "authority_runner_policy_sealed_identity_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn volume_serial(self) -> u64 {
        self.volume_serial
    }

    pub(super) fn file_id(self) -> [u8; 16] {
        self.file_id.0
    }

    pub(super) fn link_count(self) -> u32 {
        self.link_count
    }

    pub(super) fn attributes(self) -> u32 {
        self.attributes
    }

    #[cfg(test)]
    pub(super) fn exact_test_fixture(seed: u8) -> Self {
        Self::new(
            u64::from(seed).saturating_add(1),
            [seed.max(1); 16],
            1,
            0x80,
        )
        .expect("runner-policy sealed identity fixture must be valid")
    }

    #[cfg(test)]
    pub(super) fn with_field_drift_for_test(mut self, index: usize) -> Self {
        match index {
            0 => self.volume_serial = self.volume_serial.saturating_add(1),
            1 => self.file_id.0[0] ^= 1,
            2 => self.link_count = self.link_count.saturating_add(1),
            3 => self.attributes ^= 1,
            _ => panic!("runner-policy sealed identity field index out of range"),
        }
        self
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct ProtectedBlobNamespacePersistenceProjection {
    generation_sha256: Digest32,
    volume_serial: u64,
    file_id: FileId16,
    link_count: u32,
    attributes: u32,
    byte_length: u64,
    canonical_path_sha256: Digest32,
    initial_empty_inventory_sha256: Digest32,
    final_security_sha256: Digest32,
    file_security_sha256: Digest32,
    runtime_access: u32,
    share_access: u32,
    open_disposition: u32,
    file_create_access: u32,
    file_read_access: u32,
    file_cleanup_access: u32,
    seal_sha256: Digest32,
}

impl ProtectedBlobNamespacePersistenceProjection {
    #[cfg(windows)]
    fn from_generation_seal(
        value: super::finalizer_generation_seal::ProtectedBlobNamespaceSealProjection,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let projected = Self {
            generation_sha256: Digest32::checked(value.generation_sha256())?,
            volume_serial: value.volume_serial(),
            file_id: FileId16(value.file_id()),
            link_count: value.link_count(),
            attributes: value.attributes(),
            byte_length: value.byte_length(),
            canonical_path_sha256: Digest32::checked(value.canonical_path_sha256())?,
            initial_empty_inventory_sha256: Digest32::checked(
                value.initial_empty_inventory_sha256(),
            )?,
            final_security_sha256: Digest32::checked(value.final_security_sha256())?,
            file_security_sha256: Digest32::checked(value.file_security_sha256())?,
            runtime_access: value.runtime_access(),
            share_access: value.share_access(),
            open_disposition: value.open_disposition(),
            file_create_access: value.file_create_access(),
            file_read_access: value.file_read_access(),
            file_cleanup_access: value.file_cleanup_access(),
            seal_sha256: Digest32::checked(value.seal_sha256())?,
        };
        projected.validate()?;
        Ok(projected)
    }

    fn compute_seal_sha256(self) -> RawDigest {
        let mut digest = Sha256::new();
        digest.update(PROTECTED_BLOB_NAMESPACE_SEAL_DOMAIN);
        digest.update(self.generation_sha256.0);
        digest
            .update((AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME.len() as u64).to_be_bytes());
        digest.update(AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME.as_bytes());
        digest.update(self.volume_serial.to_be_bytes());
        digest.update(self.file_id.0);
        digest.update(self.link_count.to_be_bytes());
        digest.update(self.attributes.to_be_bytes());
        digest.update(self.byte_length.to_be_bytes());
        digest.update(self.canonical_path_sha256.0);
        digest.update(self.initial_empty_inventory_sha256.0);
        digest.update(self.final_security_sha256.0);
        digest.update(self.file_security_sha256.0);
        digest.update(self.runtime_access.to_be_bytes());
        digest.update(self.share_access.to_be_bytes());
        digest.update(self.open_disposition.to_be_bytes());
        digest.update(self.file_create_access.to_be_bytes());
        digest.update(self.file_read_access.to_be_bytes());
        digest.update(self.file_cleanup_access.to_be_bytes());
        digest.finalize().into()
    }

    fn compute_initial_empty_inventory_sha256(self) -> RawDigest {
        let mut digest = Sha256::new();
        digest.update(PROTECTED_BLOB_NAMESPACE_EMPTY_INVENTORY_DOMAIN);
        digest.update(self.generation_sha256.0);
        digest.update(self.volume_serial.to_be_bytes());
        digest.update(self.file_id.0);
        digest.update(self.canonical_path_sha256.0);
        digest.update(0u64.to_be_bytes());
        digest.finalize().into()
    }

    pub(crate) fn validate(self) -> Result<(), AuthorityMaintenanceError> {
        const FILE_ATTRIBUTE_DIRECTORY_BIT: u32 = 0x10;
        const FILE_ATTRIBUTE_REPARSE_POINT_BIT: u32 = 0x400;
        #[cfg(windows)]
        let expected_final_security =
            expected_security_digests(FinalizerSealTarget::RuntimeBlobDirectory)
                .map_err(|_| seal_complete_authorization_invalid())?
                .1;
        #[cfg(windows)]
        let expected_file_security =
            expected_security_digests(FinalizerSealTarget::RuntimeBlobFile)
                .map_err(|_| seal_complete_authorization_invalid())?
                .1;
        if is_zero(&self.generation_sha256.0)
            || self.volume_serial == 0
            || is_zero(&self.file_id.0)
            || self.link_count != 1
            || self.attributes & FILE_ATTRIBUTE_DIRECTORY_BIT == 0
            || self.attributes & FILE_ATTRIBUTE_REPARSE_POINT_BIT != 0
            || self.byte_length != 0
            || is_zero(&self.canonical_path_sha256.0)
            || self.initial_empty_inventory_sha256.0
                != self.compute_initial_empty_inventory_sha256()
            || is_zero(&self.final_security_sha256.0)
            || is_zero(&self.file_security_sha256.0)
            || self.runtime_access != RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS
            || self.share_access != 0
            || self.open_disposition != PROTECTED_BLOB_NAMESPACE_OPEN_DISPOSITION
            || self.file_create_access != RUNTIME_BLOB_FILE_AUTHORITY_ACCESS
            || self.file_read_access != RUNTIME_BLOB_FILE_READ_ACCESS
            || self.file_cleanup_access != RUNTIME_BLOB_FILE_CLEANUP_ACCESS
            || self.seal_sha256.0 != self.compute_seal_sha256()
            || {
                #[cfg(windows)]
                {
                    self.final_security_sha256.0 != expected_final_security
                        || self.file_security_sha256.0 != expected_file_security
                }
                #[cfg(not(windows))]
                {
                    false
                }
            }
        {
            return Err(seal_complete_authorization_invalid());
        }
        Ok(())
    }

    pub(crate) fn generation_sha256(self) -> RawDigest {
        self.generation_sha256.0
    }

    pub(crate) fn volume_serial(self) -> u64 {
        self.volume_serial
    }

    pub(crate) fn file_id(self) -> [u8; 16] {
        self.file_id.0
    }

    pub(crate) fn link_count(self) -> u32 {
        self.link_count
    }

    pub(crate) fn attributes(self) -> u32 {
        self.attributes
    }

    pub(crate) fn byte_length(self) -> u64 {
        self.byte_length
    }

    pub(crate) fn final_security_sha256(self) -> RawDigest {
        self.final_security_sha256.0
    }

    pub(crate) fn canonical_path_sha256(self) -> RawDigest {
        self.canonical_path_sha256.0
    }

    pub(crate) fn initial_empty_inventory_sha256(self) -> RawDigest {
        self.initial_empty_inventory_sha256.0
    }

    pub(crate) fn file_security_sha256(self) -> RawDigest {
        self.file_security_sha256.0
    }

    pub(crate) fn runtime_access(self) -> u32 {
        self.runtime_access
    }

    pub(crate) fn share_access(self) -> u32 {
        self.share_access
    }

    pub(crate) fn open_disposition(self) -> u32 {
        self.open_disposition
    }

    pub(crate) fn file_create_access(self) -> u32 {
        self.file_create_access
    }

    pub(crate) fn file_read_access(self) -> u32 {
        self.file_read_access
    }

    pub(crate) fn file_cleanup_access(self) -> u32 {
        self.file_cleanup_access
    }

    pub(crate) fn seal_sha256(self) -> RawDigest {
        self.seal_sha256.0
    }

    #[cfg(all(test, windows))]
    pub(super) fn exact_test_fixture(generation_sha256: RawDigest, seed: u8) -> Self {
        Self::from_generation_seal(
            super::finalizer_generation_seal::ProtectedBlobNamespaceSealProjection::exact_test_fixture(
                generation_sha256,
                seed,
            ),
        )
        .expect("runtime blob seal fixture must project exactly")
    }

    #[cfg(all(test, not(windows)))]
    pub(super) fn exact_test_fixture(generation_sha256: RawDigest, seed: u8) -> Self {
        let final_security_sha256 = [seed.wrapping_add(1).max(1); 32];
        let file_security_sha256 = [seed.wrapping_add(2).max(1); 32];
        let mut value = Self {
            generation_sha256: Digest32(generation_sha256),
            volume_serial: u64::from(seed) + 1,
            file_id: FileId16([seed.max(1); 16]),
            link_count: 1,
            attributes: 0x10,
            byte_length: 0,
            canonical_path_sha256: Digest32([seed.wrapping_add(3).max(1); 32]),
            initial_empty_inventory_sha256: Digest32([0; 32]),
            final_security_sha256: Digest32(final_security_sha256),
            file_security_sha256: Digest32(file_security_sha256),
            runtime_access: RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS,
            share_access: 0,
            open_disposition: PROTECTED_BLOB_NAMESPACE_OPEN_DISPOSITION,
            file_create_access: RUNTIME_BLOB_FILE_AUTHORITY_ACCESS,
            file_read_access: RUNTIME_BLOB_FILE_READ_ACCESS,
            file_cleanup_access: RUNTIME_BLOB_FILE_CLEANUP_ACCESS,
            seal_sha256: Digest32([0; 32]),
        };
        value.initial_empty_inventory_sha256 =
            Digest32(value.compute_initial_empty_inventory_sha256());
        value.seal_sha256 = Digest32(value.compute_seal_sha256());
        value
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(super) enum ResidueDimension {
    MaintenanceService,
    TransientStaging,
    CandidateActivationCredential,
    MaintenancePipe,
    WorkerProcessAndState,
    WorkerNonce,
    CandidateConsumption,
    FinalizerReceiptPublishing,
    ActiveHead,
    RetirementStaging,
    RetirementAborted,
    RetirementFinal,
    FinalizerCommitStore,
}

const RESIDUE_DIMENSIONS: [ResidueDimension; 13] = [
    ResidueDimension::MaintenanceService,
    ResidueDimension::TransientStaging,
    ResidueDimension::CandidateActivationCredential,
    ResidueDimension::MaintenancePipe,
    ResidueDimension::WorkerProcessAndState,
    ResidueDimension::WorkerNonce,
    ResidueDimension::CandidateConsumption,
    ResidueDimension::FinalizerReceiptPublishing,
    ResidueDimension::ActiveHead,
    ResidueDimension::RetirementStaging,
    ResidueDimension::RetirementAborted,
    ResidueDimension::RetirementFinal,
    ResidueDimension::FinalizerCommitStore,
];

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
enum ResidueExpectation {
    Absent,
    PresentExact,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum ResidueExpectationKind {
    Absent,
    PresentExact,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct ResidueObjectExpectationView {
    dimension: ResidueDimension,
    expectation: ResidueExpectationKind,
    object_binding_sha256: RawDigest,
    expected_identity_sha256: Option<RawDigest>,
}

impl ResidueObjectExpectationView {
    pub(super) fn dimension(self) -> ResidueDimension {
        self.dimension
    }

    pub(super) fn expectation(self) -> ResidueExpectationKind {
        self.expectation
    }

    pub(super) fn object_binding_sha256(self) -> RawDigest {
        self.object_binding_sha256
    }

    pub(super) fn expected_identity_sha256(self) -> Option<RawDigest> {
        self.expected_identity_sha256
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct ResidueObjectPlan {
    dimension: ResidueDimension,
    object_binding_sha256: Digest32,
    expectation: ResidueExpectation,
    expected_identity_sha256: Option<Digest32>,
}

impl ResidueObjectPlan {
    #[cfg(test)]
    pub(super) fn absent(
        dimension: ResidueDimension,
        object_binding_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            dimension,
            object_binding_sha256: Digest32::checked(object_binding_sha256)?,
            expectation: ResidueExpectation::Absent,
            expected_identity_sha256: None,
        };
        value.validate()?;
        Ok(value)
    }

    #[cfg(test)]
    pub(super) fn present_exact(
        dimension: ResidueDimension,
        object_binding_sha256: RawDigest,
        expected_identity_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            dimension,
            object_binding_sha256: Digest32::checked(object_binding_sha256)?,
            expectation: ResidueExpectation::PresentExact,
            expected_identity_sha256: Some(Digest32::checked(expected_identity_sha256)?),
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if is_zero(&self.object_binding_sha256.0)
            || matches!(self.expectation, ResidueExpectation::Absent)
                != self.expected_identity_sha256.is_none()
        {
            return Err(operation_residue_plan_invalid());
        }
        Ok(())
    }

    fn expectation_view(self) -> ResidueObjectExpectationView {
        ResidueObjectExpectationView {
            dimension: self.dimension,
            expectation: match self.expectation {
                ResidueExpectation::Absent => ResidueExpectationKind::Absent,
                ResidueExpectation::PresentExact => ResidueExpectationKind::PresentExact,
            },
            object_binding_sha256: self.object_binding_sha256.0,
            expected_identity_sha256: self.expected_identity_sha256.map(Digest32::into_inner),
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct OperationResiduePlan {
    operation: AuthorityMaintenanceOperation,
    objects: [ResidueObjectPlan; 13],
}

impl OperationResiduePlan {
    #[cfg(test)]
    pub(super) fn new(
        operation: AuthorityMaintenanceOperation,
        objects: [ResidueObjectPlan; 13],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self { operation, objects };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if self.operation == AuthorityMaintenanceOperation::Retire {
            return Err(operation_residue_plan_invalid());
        }
        for (index, object) in self.objects.iter().enumerate() {
            object.validate()?;
            if object.dimension != RESIDUE_DIMENSIONS[index] {
                return Err(operation_residue_plan_invalid());
            }
            let present = matches!(object.expectation, ResidueExpectation::PresentExact);
            let expected_present = match object.dimension {
                ResidueDimension::WorkerNonce
                | ResidueDimension::CandidateConsumption
                | ResidueDimension::ActiveHead
                | ResidueDimension::FinalizerCommitStore => true,
                ResidueDimension::RetirementFinal => {
                    self.operation == AuthorityMaintenanceOperation::Update
                }
                ResidueDimension::MaintenanceService
                | ResidueDimension::TransientStaging
                | ResidueDimension::CandidateActivationCredential
                | ResidueDimension::MaintenancePipe
                | ResidueDimension::WorkerProcessAndState
                | ResidueDimension::FinalizerReceiptPublishing
                | ResidueDimension::RetirementStaging
                | ResidueDimension::RetirementAborted => false,
            };
            if present != expected_present {
                return Err(operation_residue_plan_invalid());
            }
        }
        Ok(())
    }

    pub(super) fn digest(&self) -> Result<RawDigest, AuthorityMaintenanceError> {
        self.validate()?;
        Ok(operation_residue_plan_digest_unchecked(self))
    }

    pub(super) fn operation(&self) -> AuthorityMaintenanceOperation {
        self.operation
    }

    pub(super) fn expectations(
        &self,
    ) -> Result<[ResidueObjectExpectationView; 13], AuthorityMaintenanceError> {
        self.validate()?;
        Ok(self.objects.map(ResidueObjectPlan::expectation_view))
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct ResidueObjectReadback {
    plan: ResidueObjectPlan,
    observed_identity_sha256: Option<Digest32>,
    kernel_readback_sha256: Digest32,
}

impl ResidueObjectReadback {
    pub(super) fn absent(
        plan: ResidueObjectPlan,
        kernel_readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            plan,
            observed_identity_sha256: None,
            kernel_readback_sha256: Digest32::checked(kernel_readback_sha256)?,
        };
        value.validate()?;
        Ok(value)
    }

    pub(super) fn present_exact(
        plan: ResidueObjectPlan,
        observed_identity_sha256: RawDigest,
        kernel_readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            plan,
            observed_identity_sha256: Some(Digest32::checked(observed_identity_sha256)?),
            kernel_readback_sha256: Digest32::checked(kernel_readback_sha256)?,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.plan.validate()?;
        if is_zero(&self.kernel_readback_sha256.0)
            || self.observed_identity_sha256 != self.plan.expected_identity_sha256
        {
            return Err(operation_zero_residue_invalid());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct OperationZeroResidueReadback {
    plan: OperationResiduePlan,
    objects: [ResidueObjectReadback; 13],
}

impl OperationZeroResidueReadback {
    pub(super) fn new(
        plan: OperationResiduePlan,
        objects: [ResidueObjectReadback; 13],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self { plan, objects };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.plan.validate()?;
        for (index, object) in self.objects.iter().enumerate() {
            object.validate()?;
            if object.plan != self.plan.objects[index] {
                return Err(operation_zero_residue_invalid());
            }
        }
        Ok(())
    }

    pub(super) fn digest(&self) -> Result<RawDigest, AuthorityMaintenanceError> {
        self.validate()?;
        Ok(operation_zero_residue_readback_digest_unchecked(self))
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct FinalizerCommitPlanBinding {
    operation: AuthorityMaintenanceOperation,
    expected_worker_service_identity_sha256: Digest32,
    expected_worker_image_sha256: Digest32,
    exact_service_configuration_sha256: Digest32,
    expected_service_image_sha256: Digest32,
    expected_active_head_prior_sha256: Digest32,
    expected_active_head_replacement_sha256: Digest32,
    expected_activation_manifest_sha256: Digest32,
    expected_activation_epoch: u64,
    generation_object_manifest_sha256: Digest32,
    expected_runner_policy_state_byte_length: u64,
    expected_runner_policy_state_bytes_sha256: Digest32,
    expected_runner_policy_state_binding_sha256: Digest32,
    residue_plan: OperationResiduePlan,
}

impl FinalizerCommitPlanBinding {
    #[allow(clippy::too_many_arguments)]
    #[cfg(test)]
    pub(super) fn new(
        operation: AuthorityMaintenanceOperation,
        expected_worker_service_identity_sha256: RawDigest,
        expected_worker_image_sha256: RawDigest,
        exact_service_configuration_sha256: RawDigest,
        expected_service_image_sha256: RawDigest,
        expected_active_head_prior_sha256: RawDigest,
        expected_active_head_replacement_sha256: RawDigest,
        expected_activation_manifest_sha256: RawDigest,
        expected_activation_epoch: u64,
        generation_object_manifest_sha256: RawDigest,
        residue_plan: OperationResiduePlan,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            operation,
            expected_worker_service_identity_sha256: Digest32::checked(
                expected_worker_service_identity_sha256,
            )?,
            expected_worker_image_sha256: Digest32::checked(expected_worker_image_sha256)?,
            exact_service_configuration_sha256: Digest32::checked(
                exact_service_configuration_sha256,
            )?,
            expected_service_image_sha256: Digest32::checked(expected_service_image_sha256)?,
            expected_active_head_prior_sha256: Digest32::checked(
                expected_active_head_prior_sha256,
            )?,
            expected_active_head_replacement_sha256: Digest32::checked(
                expected_active_head_replacement_sha256,
            )?,
            expected_activation_manifest_sha256: Digest32::checked(
                expected_activation_manifest_sha256,
            )?,
            expected_activation_epoch,
            generation_object_manifest_sha256: Digest32::checked(
                generation_object_manifest_sha256,
            )?,
            expected_runner_policy_state_byte_length: 512,
            expected_runner_policy_state_bytes_sha256: Digest32([0x59; 32]),
            expected_runner_policy_state_binding_sha256: Digest32([0x5a; 32]),
            residue_plan,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if self.operation == AuthorityMaintenanceOperation::Retire
            || self.expected_activation_epoch == 0
            || [
                self.exact_service_configuration_sha256.0,
                self.expected_worker_service_identity_sha256.0,
                self.expected_worker_image_sha256.0,
                self.expected_service_image_sha256.0,
                self.expected_active_head_prior_sha256.0,
                self.expected_active_head_replacement_sha256.0,
                self.expected_activation_manifest_sha256.0,
                self.generation_object_manifest_sha256.0,
                self.expected_runner_policy_state_bytes_sha256.0,
                self.expected_runner_policy_state_binding_sha256.0,
            ]
            .iter()
            .any(is_zero)
            || self.expected_runner_policy_state_byte_length == 0
            || self.expected_runner_policy_state_byte_length
                > MAX_EXPECTED_RUNNER_POLICY_STATE_BYTES
            || self.residue_plan.validate().is_err()
            || self.residue_plan.operation != self.operation
        {
            return Err(identity_invalid());
        }
        Ok(())
    }

    pub(super) fn operation(&self) -> AuthorityMaintenanceOperation {
        self.operation
    }

    pub(super) fn exact_service_configuration_sha256(&self) -> RawDigest {
        self.exact_service_configuration_sha256.0
    }

    pub(super) fn expected_worker_service_identity_sha256(&self) -> RawDigest {
        self.expected_worker_service_identity_sha256.0
    }

    pub(super) fn expected_worker_image_sha256(&self) -> RawDigest {
        self.expected_worker_image_sha256.0
    }

    pub(super) fn expected_service_image_sha256(&self) -> RawDigest {
        self.expected_service_image_sha256.0
    }

    pub(super) fn expected_active_head_prior_sha256(&self) -> RawDigest {
        self.expected_active_head_prior_sha256.0
    }

    pub(super) fn expected_active_head_replacement_sha256(&self) -> RawDigest {
        self.expected_active_head_replacement_sha256.0
    }

    pub(super) fn expected_activation_manifest_sha256(&self) -> RawDigest {
        self.expected_activation_manifest_sha256.0
    }

    pub(super) fn expected_activation_epoch(&self) -> u64 {
        self.expected_activation_epoch
    }

    pub(super) fn generation_object_manifest_sha256(&self) -> RawDigest {
        self.generation_object_manifest_sha256.0
    }

    pub(super) fn expected_runner_policy_state_byte_length(&self) -> u64 {
        self.expected_runner_policy_state_byte_length
    }

    pub(super) fn expected_runner_policy_state_bytes_sha256(&self) -> RawDigest {
        self.expected_runner_policy_state_bytes_sha256.0
    }

    pub(super) fn expected_runner_policy_state_binding_sha256(&self) -> RawDigest {
        self.expected_runner_policy_state_binding_sha256.0
    }

    #[cfg(test)]
    pub(super) fn with_runner_policy_state_descriptor(
        mut self,
        descriptor: RunnerPolicyStateDescriptor,
    ) -> Result<Self, AuthorityMaintenanceError> {
        self.expected_runner_policy_state_byte_length = descriptor.byte_length();
        self.expected_runner_policy_state_bytes_sha256 =
            Digest32::checked(descriptor.bytes_sha256())?;
        self.expected_runner_policy_state_binding_sha256 =
            Digest32::checked(descriptor.binding_sha256())?;
        self.validate()?;
        Ok(self)
    }

    pub(super) fn residue_plan(&self) -> OperationResiduePlan {
        self.residue_plan
    }

    pub(super) fn residue_plan_sha256(&self) -> Result<RawDigest, AuthorityMaintenanceError> {
        self.residue_plan.digest()
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct FinalizerCommitBinding {
    capsule_sha256: Digest32,
    plan_sha256: Digest32,
    generation_sha256: Digest32,
    transaction_sha256: Digest32,
    plan: FinalizerCommitPlanBinding,
    final_commit_store_root_identity_sha256: Digest32,
}

impl FinalizerCommitBinding {
    #[cfg(test)]
    pub(super) fn new(
        capsule_sha256: RawDigest,
        plan_sha256: RawDigest,
        generation_sha256: RawDigest,
        transaction_sha256: RawDigest,
        plan: FinalizerCommitPlanBinding,
        final_commit_store_root_identity_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            capsule_sha256: Digest32::checked(capsule_sha256)?,
            plan_sha256: Digest32::checked(plan_sha256)?,
            generation_sha256: Digest32::checked(generation_sha256)?,
            transaction_sha256: Digest32::checked(transaction_sha256)?,
            plan,
            final_commit_store_root_identity_sha256: Digest32::checked(
                final_commit_store_root_identity_sha256,
            )?,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if [
            self.capsule_sha256.0,
            self.plan_sha256.0,
            self.generation_sha256.0,
            self.transaction_sha256.0,
            self.final_commit_store_root_identity_sha256.0,
        ]
        .iter()
        .any(is_zero)
            || self.plan.validate().is_err()
        {
            return Err(identity_invalid());
        }
        Ok(())
    }

    pub(super) fn capsule_sha256(&self) -> RawDigest {
        self.capsule_sha256.into_inner()
    }

    pub(super) fn plan_sha256(&self) -> RawDigest {
        self.plan_sha256.into_inner()
    }

    pub(super) fn generation_sha256(&self) -> RawDigest {
        self.generation_sha256.into_inner()
    }

    pub(super) fn transaction_sha256(&self) -> RawDigest {
        self.transaction_sha256.into_inner()
    }

    pub(super) fn plan_binding(&self) -> FinalizerCommitPlanBinding {
        self.plan
    }

    pub(super) fn final_commit_gate_projection_sha256(&self) -> RawDigest {
        final_commit_gate_projection_sha256(self)
    }

    pub(super) fn final_commit_store_root_identity_sha256(&self) -> RawDigest {
        self.final_commit_store_root_identity_sha256.0
    }

    pub(super) fn expected_final_commit_gate_sha256(&self) -> RawDigest {
        expected_final_commit_gate_sha256(self)
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct DurableFileIdentity {
    volume_serial: u64,
    file_id: FileId16,
    link_count: u32,
    byte_length: u64,
    bytes_sha256: Digest32,
}

impl DurableFileIdentity {
    pub(super) fn new(
        volume_serial: u64,
        file_id: [u8; 16],
        link_count: u32,
        byte_length: u64,
        bytes_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            volume_serial,
            file_id: FileId16(file_id),
            link_count,
            byte_length,
            bytes_sha256: Digest32::checked(bytes_sha256)?,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if self.volume_serial == 0
            || is_zero(&self.file_id.0)
            || self.link_count != 1
            || self.byte_length == 0
            || is_zero(&self.bytes_sha256.0)
        {
            return Err(file_identity_invalid());
        }
        Ok(())
    }

    pub(super) fn volume_serial(&self) -> u64 {
        self.volume_serial
    }

    pub(super) fn file_id(&self) -> [u8; 16] {
        self.file_id.0
    }

    pub(super) fn link_count(&self) -> u32 {
        self.link_count
    }

    pub(super) fn byte_length(&self) -> u64 {
        self.byte_length
    }

    pub(super) fn bytes_sha256(&self) -> RawDigest {
        self.bytes_sha256.into_inner()
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct NonceArtifactPair {
    worker_nonce: DurableFileIdentity,
    candidate_consumption: DurableFileIdentity,
}

impl NonceArtifactPair {
    #[cfg(test)]
    pub(super) fn new(
        worker_nonce: DurableFileIdentity,
        candidate_consumption: DurableFileIdentity,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            worker_nonce,
            candidate_consumption,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.worker_nonce.validate()?;
        self.candidate_consumption.validate()?;
        if (self.worker_nonce.volume_serial, self.worker_nonce.file_id)
            == (
                self.candidate_consumption.volume_serial,
                self.candidate_consumption.file_id,
            )
        {
            return Err(file_identity_collision());
        }
        Ok(())
    }

    pub(super) fn worker_nonce(&self) -> DurableFileIdentity {
        self.worker_nonce
    }

    pub(super) fn candidate_consumption(&self) -> DurableFileIdentity {
        self.candidate_consumption
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct ExactServiceProcessIdentity {
    exact_service_identity_sha256: Digest32,
    process_id: u32,
    process_creation_time: u64,
    image_sha256: Digest32,
}

impl ExactServiceProcessIdentity {
    #[cfg(test)]
    pub(super) fn new(
        exact_service_identity_sha256: RawDigest,
        process_id: u32,
        process_creation_time: u64,
        image_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            exact_service_identity_sha256: Digest32::checked(exact_service_identity_sha256)?,
            process_id,
            process_creation_time,
            image_sha256: Digest32::checked(image_sha256)?,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if self.process_id == 0
            || self.process_creation_time == 0
            || is_zero(&self.exact_service_identity_sha256.0)
            || is_zero(&self.image_sha256.0)
        {
            return Err(evidence_invalid());
        }
        Ok(())
    }

    fn process_identity_sha256(&self) -> RawDigest {
        service_process_identity_sha256(
            self.exact_service_identity_sha256.0,
            self.process_id,
            self.process_creation_time,
            self.image_sha256.0,
        )
    }

    pub(super) fn exact_service_identity_sha256(self) -> RawDigest {
        self.exact_service_identity_sha256.0
    }

    pub(super) fn process_id(self) -> u32 {
        self.process_id
    }

    pub(super) fn process_creation_time(self) -> u64 {
        self.process_creation_time
    }

    pub(super) fn image_sha256(self) -> RawDigest {
        self.image_sha256.0
    }

    pub(super) fn exact_process_identity_sha256(self) -> RawDigest {
        self.process_identity_sha256()
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct TransactionStartedEvidence {
    commit_binding_sha256: Digest32,
    worker: ExactServiceProcessIdentity,
    durable_start_readback_sha256: Digest32,
    worker_activation_proof_sha256: Digest32,
}

impl TransactionStartedEvidence {
    #[cfg(test)]
    pub(super) fn new(
        binding: FinalizerCommitBinding,
        worker: ExactServiceProcessIdentity,
        durable_start_readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        binding.validate()?;
        worker.validate()?;
        let commit_binding_sha256 = Digest32::checked(commit_binding_identity_sha256(&binding))?;
        let durable_start_readback_sha256 = Digest32::checked(durable_start_readback_sha256)?;
        let worker_activation_proof_sha256 = Digest32::checked(worker_activation_proof_sha256(
            commit_binding_sha256.0,
            worker,
            durable_start_readback_sha256.0,
        ))?;
        let value = Self {
            commit_binding_sha256,
            worker,
            durable_start_readback_sha256,
            worker_activation_proof_sha256,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.worker.validate()?;
        if is_zero(&self.commit_binding_sha256.0)
            || is_zero(&self.durable_start_readback_sha256.0)
            || self.worker_activation_proof_sha256.0
                != worker_activation_proof_sha256(
                    self.commit_binding_sha256.0,
                    self.worker,
                    self.durable_start_readback_sha256.0,
                )
        {
            return Err(evidence_invalid());
        }
        Ok(())
    }

    pub(super) fn worker(&self) -> ExactServiceProcessIdentity {
        self.worker
    }

    pub(super) fn durable_start_readback_sha256(&self) -> RawDigest {
        self.durable_start_readback_sha256.0
    }

    pub(super) fn worker_activation_proof_sha256(&self) -> RawDigest {
        self.worker_activation_proof_sha256.0
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct ApplyReadyEvidence {
    exact_apply_readback_sha256: Digest32,
}

impl ApplyReadyEvidence {
    #[cfg(test)]
    pub(super) fn new(
        exact_apply_readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        Ok(Self {
            exact_apply_readback_sha256: Digest32::checked(exact_apply_readback_sha256)?,
        })
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        Digest32::checked(self.exact_apply_readback_sha256.0).map(|_| ())
    }

    pub(super) fn exact_apply_readback_sha256(&self) -> RawDigest {
        self.exact_apply_readback_sha256.0
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WriterHandlesClosedReadback {
    worker: ExactServiceProcessIdentity,
    worker_writer_handles_closed: bool,
    worker_writer_handles_closed_readback_sha256: Digest32,
    candidate_writer_handles_closed: bool,
    candidate_writer_handles_closed_readback_sha256: Digest32,
}

impl WriterHandlesClosedReadback {
    #[cfg(test)]
    pub(super) fn new(
        worker: ExactServiceProcessIdentity,
        worker_writer_handles_closed: bool,
        worker_writer_handles_closed_readback_sha256: RawDigest,
        candidate_writer_handles_closed: bool,
        candidate_writer_handles_closed_readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            worker,
            worker_writer_handles_closed,
            worker_writer_handles_closed_readback_sha256: Digest32::checked(
                worker_writer_handles_closed_readback_sha256,
            )?,
            candidate_writer_handles_closed,
            candidate_writer_handles_closed_readback_sha256: Digest32::checked(
                candidate_writer_handles_closed_readback_sha256,
            )?,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.worker.validate()?;
        if !self.worker_writer_handles_closed
            || is_zero(&self.worker_writer_handles_closed_readback_sha256.0)
            || !self.candidate_writer_handles_closed
            || is_zero(&self.candidate_writer_handles_closed_readback_sha256.0)
        {
            return Err(writer_handles_not_closed());
        }
        Ok(())
    }

    pub(super) fn worker_readback_sha256(&self) -> RawDigest {
        self.worker_writer_handles_closed_readback_sha256
            .into_inner()
    }

    pub(super) fn candidate_readback_sha256(&self) -> RawDigest {
        self.candidate_writer_handles_closed_readback_sha256
            .into_inner()
    }

    pub(super) fn worker(&self) -> ExactServiceProcessIdentity {
        self.worker
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct ExactServiceRuntimeIdentity {
    exact_service_configuration_sha256: Digest32,
    exact_runtime_instance_sha256: Digest32,
    runtime_token_receipt_sha256: Digest32,
    process: CandidateProcessEvidence,
}

impl ExactServiceRuntimeIdentity {
    #[cfg(test)]
    pub(super) fn from_observed(
        exact_service_configuration_sha256: RawDigest,
        runtime_token_receipt_sha256: RawDigest,
        process: CandidateProcessEvidence,
    ) -> Result<Self, AuthorityMaintenanceError> {
        Self::from_verified_parts(
            exact_service_configuration_sha256,
            runtime_token_receipt_sha256,
            process,
        )
    }

    fn from_verified_parts(
        exact_service_configuration_sha256: RawDigest,
        runtime_token_receipt_sha256: RawDigest,
        process: CandidateProcessEvidence,
    ) -> Result<Self, AuthorityMaintenanceError> {
        process
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let exact_service_configuration_sha256 =
            Digest32::checked(exact_service_configuration_sha256)?;
        let runtime_token_receipt_sha256 = Digest32::checked(runtime_token_receipt_sha256)?;
        let exact_runtime_instance_sha256 =
            Digest32::checked(candidate_exact_service_identity_digest(
                exact_service_configuration_sha256.0,
                process,
                runtime_token_receipt_sha256.0,
            )?)?;
        let value = Self {
            exact_service_configuration_sha256,
            exact_runtime_instance_sha256,
            runtime_token_receipt_sha256,
            process,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.process
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if is_zero(&self.exact_service_configuration_sha256.0)
            || is_zero(&self.runtime_token_receipt_sha256.0)
            || self.exact_runtime_instance_sha256.0
                != candidate_exact_service_identity_digest(
                    self.exact_service_configuration_sha256.0,
                    self.process,
                    self.runtime_token_receipt_sha256.0,
                )?
        {
            return Err(evidence_invalid());
        }
        Ok(())
    }

    pub(super) fn exact_service_configuration_sha256(self) -> RawDigest {
        self.exact_service_configuration_sha256.0
    }

    pub(super) fn exact_runtime_instance_sha256(self) -> RawDigest {
        self.exact_runtime_instance_sha256.0
    }

    pub(super) fn runtime_token_receipt_sha256(self) -> RawDigest {
        self.runtime_token_receipt_sha256.0
    }

    pub(super) fn process(self) -> CandidateProcessEvidence {
        self.process
    }

    fn process_identity_sha256(&self) -> RawDigest {
        service_process_identity_sha256(
            self.exact_runtime_instance_sha256.0,
            self.process.process_id(),
            self.process.process_creation_time(),
            *self.process.image_sha256(),
        )
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct CandidateActivationIdentity {
    commit_binding_sha256: Digest32,
    nonce_artifact_pair_sha256: Digest32,
    runtime: ExactServiceRuntimeIdentity,
    activation_readback_sha256: Digest32,
    activation_proof_sha256: Digest32,
}

impl CandidateActivationIdentity {
    #[allow(clippy::too_many_arguments)]
    #[cfg(test)]
    pub(super) fn new(
        binding: FinalizerCommitBinding,
        artifacts: NonceArtifactPair,
        exact_service_configuration_sha256: RawDigest,
        runtime_token_receipt_sha256: RawDigest,
        candidate_service: CandidateProcessEvidence,
        activation_readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        Self::from_observed(
            binding,
            artifacts,
            exact_service_configuration_sha256,
            runtime_token_receipt_sha256,
            candidate_service,
            activation_readback_sha256,
        )
    }

    #[cfg(test)]
    pub(super) fn from_verified_seal_ready(
        binding: FinalizerCommitBinding,
        artifacts: NonceArtifactPair,
        projection: CandidateActivationSealReadyProjection,
    ) -> Result<Self, AuthorityMaintenanceError> {
        binding.validate()?;
        artifacts.validate()?;
        projection.validate()?;
        let candidate_binding = projection.binding();
        let plan = binding.plan_binding();
        let candidate_process = projection.candidate_service();
        if candidate_binding.generation() != &binding.generation_sha256()
            || candidate_binding.plan_sha256() != &binding.plan_sha256()
            || candidate_binding.transaction_sha256() != &binding.transaction_sha256()
            || candidate_binding.activation_epoch() != plan.expected_activation_epoch()
            || candidate_binding.active_head_sha256() != &plan.expected_active_head_prior_sha256()
            || candidate_binding.activation_manifest_sha256()
                != &plan.expected_activation_manifest_sha256()
            || candidate_binding.service_image_sha256() != &plan.expected_service_image_sha256()
            || projection.exact_service_configuration_readback_sha256()
                != plan.exact_service_configuration_sha256()
            || artifacts.candidate_consumption().bytes_sha256()
                != projection.candidate_consumption_file_sha256()
            || artifacts.candidate_consumption().volume_serial()
                != projection.candidate_consumption_file_volume_serial()
            || artifacts.candidate_consumption().file_id()
                != projection.candidate_consumption_file_id()
            || artifacts.candidate_consumption().link_count()
                != projection.candidate_consumption_file_link_count()
        {
            return Err(evidence_invalid());
        }
        Self::from_observed(
            binding,
            artifacts,
            projection.exact_service_configuration_readback_sha256(),
            projection.runtime_token_receipt_sha256(),
            candidate_process,
            projection.activation_readback_sha256(),
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn from_observed(
        binding: FinalizerCommitBinding,
        artifacts: NonceArtifactPair,
        exact_service_configuration_sha256: RawDigest,
        runtime_token_receipt_sha256: RawDigest,
        candidate_service: CandidateProcessEvidence,
        activation_readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        binding.validate()?;
        artifacts.validate()?;
        candidate_service
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if exact_service_configuration_sha256
            != binding.plan_binding().exact_service_configuration_sha256()
            || candidate_service.image_sha256()
                != &binding.plan_binding().expected_service_image_sha256()
        {
            return Err(evidence_invalid());
        }
        let commit_binding_sha256 = Digest32::checked(commit_binding_identity_sha256(&binding))?;
        let nonce_artifact_pair_sha256 =
            Digest32::checked(nonce_artifact_pair_identity_sha256(&artifacts))?;
        let runtime = ExactServiceRuntimeIdentity::from_verified_parts(
            exact_service_configuration_sha256,
            runtime_token_receipt_sha256,
            candidate_service,
        )?;
        let activation_readback_sha256 = Digest32::checked(activation_readback_sha256)?;
        let activation_proof_sha256 = Digest32::checked(candidate_activation_proof_sha256(
            commit_binding_sha256.0,
            nonce_artifact_pair_sha256.0,
            runtime.exact_runtime_instance_sha256.0,
            runtime.process.process_id(),
            runtime.process.process_creation_time(),
            *runtime.process.image_sha256(),
            activation_readback_sha256.0,
        ))?;
        let value = Self {
            commit_binding_sha256,
            nonce_artifact_pair_sha256,
            runtime,
            activation_readback_sha256,
            activation_proof_sha256,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.runtime.validate()?;
        if [
            self.commit_binding_sha256.0,
            self.nonce_artifact_pair_sha256.0,
            self.activation_readback_sha256.0,
            self.activation_proof_sha256.0,
        ]
        .iter()
        .any(is_zero)
        {
            return Err(evidence_invalid());
        }
        if self.activation_proof_sha256.0
            != candidate_activation_proof_sha256(
                self.commit_binding_sha256.0,
                self.nonce_artifact_pair_sha256.0,
                self.runtime.exact_runtime_instance_sha256.0,
                self.runtime.process.process_id(),
                self.runtime.process.process_creation_time(),
                *self.runtime.process.image_sha256(),
                self.activation_readback_sha256.0,
            )
        {
            return Err(evidence_invalid());
        }
        Ok(())
    }

    fn process_identity_sha256(&self) -> RawDigest {
        self.runtime.process_identity_sha256()
    }

    pub(super) fn exact_service_configuration_sha256(self) -> RawDigest {
        self.runtime.exact_service_configuration_sha256.0
    }

    pub(super) fn exact_service_identity_sha256(self) -> RawDigest {
        self.runtime.exact_runtime_instance_sha256.0
    }

    pub(super) fn runtime(self) -> ExactServiceRuntimeIdentity {
        self.runtime
    }

    pub(super) fn process_id(self) -> u32 {
        self.runtime.process.process_id()
    }

    pub(super) fn process_creation_time(self) -> u64 {
        self.runtime.process.process_creation_time()
    }

    pub(super) fn image_sha256(self) -> RawDigest {
        *self.runtime.process.image_sha256()
    }

    pub(super) fn activation_readback_sha256(self) -> RawDigest {
        self.activation_readback_sha256.0
    }

    pub(super) fn exact_process_identity_sha256(self) -> RawDigest {
        self.process_identity_sha256()
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct SealReadyEvidence {
    artifacts: NonceArtifactPair,
    writer_handles: WriterHandlesClosedReadback,
    candidate: CandidateActivationIdentity,
}

impl SealReadyEvidence {
    #[cfg(test)]
    pub(super) fn new(
        artifacts: NonceArtifactPair,
        writer_handles: WriterHandlesClosedReadback,
        candidate: CandidateActivationIdentity,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            artifacts,
            writer_handles,
            candidate,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.artifacts.validate()?;
        self.writer_handles.validate()?;
        self.candidate.validate()?;
        Ok(())
    }

    pub(super) fn artifacts(&self) -> NonceArtifactPair {
        self.artifacts
    }

    pub(super) fn writer_handles(&self) -> WriterHandlesClosedReadback {
        self.writer_handles
    }

    pub(super) fn candidate(&self) -> CandidateActivationIdentity {
        self.candidate
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct ExitReadyEvidence {
    worker: ExactServiceProcessIdentity,
    worker_writer_handles_closed_readback_sha256: Digest32,
    durable_exit_readback_sha256: Digest32,
}

impl ExitReadyEvidence {
    #[cfg(test)]
    pub(super) fn new(
        worker: ExactServiceProcessIdentity,
        worker_writer_handles_closed_readback_sha256: RawDigest,
        durable_exit_readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            worker,
            worker_writer_handles_closed_readback_sha256: Digest32::checked(
                worker_writer_handles_closed_readback_sha256,
            )?,
            durable_exit_readback_sha256: Digest32::checked(durable_exit_readback_sha256)?,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.worker.validate()?;
        if is_zero(&self.worker_writer_handles_closed_readback_sha256.0)
            || is_zero(&self.durable_exit_readback_sha256.0)
        {
            return Err(evidence_invalid());
        }
        Ok(())
    }

    pub(super) fn worker(&self) -> ExactServiceProcessIdentity {
        self.worker
    }

    pub(super) fn worker_writer_handles_closed_readback_sha256(&self) -> RawDigest {
        self.worker_writer_handles_closed_readback_sha256.0
    }

    pub(super) fn durable_exit_readback_sha256(&self) -> RawDigest {
        self.durable_exit_readback_sha256.0
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct ExactSealedSecurityReadback {
    expected_sealed_descriptor_sha256: Digest32,
    worker_nonce_descriptor_sha256: Digest32,
    candidate_consumption_descriptor_sha256: Digest32,
    owner_exact: bool,
    protected_dacl_exact: bool,
    integrity_label_exact: bool,
    security_readback_sha256: Digest32,
}

impl ExactSealedSecurityReadback {
    #[allow(clippy::too_many_arguments)]
    #[cfg(test)]
    pub(super) fn new(
        expected_sealed_descriptor_sha256: RawDigest,
        worker_nonce_descriptor_sha256: RawDigest,
        candidate_consumption_descriptor_sha256: RawDigest,
        owner_exact: bool,
        protected_dacl_exact: bool,
        integrity_label_exact: bool,
        security_readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            expected_sealed_descriptor_sha256: Digest32::checked(
                expected_sealed_descriptor_sha256,
            )?,
            worker_nonce_descriptor_sha256: Digest32::checked(worker_nonce_descriptor_sha256)?,
            candidate_consumption_descriptor_sha256: Digest32::checked(
                candidate_consumption_descriptor_sha256,
            )?,
            owner_exact,
            protected_dacl_exact,
            integrity_label_exact,
            security_readback_sha256: Digest32::checked(security_readback_sha256)?,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if is_zero(&self.expected_sealed_descriptor_sha256.0)
            || is_zero(&self.worker_nonce_descriptor_sha256.0)
            || is_zero(&self.candidate_consumption_descriptor_sha256.0)
            || self.worker_nonce_descriptor_sha256 != self.expected_sealed_descriptor_sha256
            || self.candidate_consumption_descriptor_sha256
                != self.expected_sealed_descriptor_sha256
            || !self.owner_exact
            || !self.protected_dacl_exact
            || !self.integrity_label_exact
            || is_zero(&self.security_readback_sha256.0)
        {
            return Err(sealed_security_not_exact());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
enum CandidateServiceTerminalState {
    Stopped,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct CandidateStoppedReadback {
    exact_service_identity_sha256: Digest32,
    candidate_process_id: u32,
    candidate_process_creation_time: u64,
    candidate_image_sha256: Digest32,
    candidate_process_identity_sha256: Digest32,
    service_state: CandidateServiceTerminalState,
    scm_process_id: u32,
    win32_exit_code: u32,
    service_specific_exit_code: u32,
    writer_handles_closed: bool,
    seal_ready_writer_handles_closed_readback_sha256: Digest32,
    writer_handles_closed_readback_sha256: Digest32,
    scm_readback_sha256: Digest32,
}

impl CandidateStoppedReadback {
    #[cfg(test)]
    pub(super) fn exact_stopped(
        exact_service_identity_sha256: RawDigest,
        candidate_process_id: u32,
        candidate_process_creation_time: u64,
        candidate_image_sha256: RawDigest,
        seal_ready_writer_handles_closed_readback_sha256: RawDigest,
        writer_handles_closed_readback_sha256: RawDigest,
        scm_readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let exact_service_identity_sha256 = Digest32::checked(exact_service_identity_sha256)?;
        let candidate_image_sha256 = Digest32::checked(candidate_image_sha256)?;
        let value = Self {
            exact_service_identity_sha256,
            candidate_process_id,
            candidate_process_creation_time,
            candidate_image_sha256,
            candidate_process_identity_sha256: Digest32(service_process_identity_sha256(
                exact_service_identity_sha256.0,
                candidate_process_id,
                candidate_process_creation_time,
                candidate_image_sha256.0,
            )),
            service_state: CandidateServiceTerminalState::Stopped,
            scm_process_id: 0,
            win32_exit_code: 0,
            service_specific_exit_code: 0,
            writer_handles_closed: true,
            seal_ready_writer_handles_closed_readback_sha256: Digest32::checked(
                seal_ready_writer_handles_closed_readback_sha256,
            )?,
            writer_handles_closed_readback_sha256: Digest32::checked(
                writer_handles_closed_readback_sha256,
            )?,
            scm_readback_sha256: Digest32::checked(scm_readback_sha256)?,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if is_zero(&self.exact_service_identity_sha256.0)
            || self.candidate_process_id == 0
            || self.candidate_process_creation_time == 0
            || is_zero(&self.candidate_image_sha256.0)
            || self.candidate_process_identity_sha256.0
                != service_process_identity_sha256(
                    self.exact_service_identity_sha256.0,
                    self.candidate_process_id,
                    self.candidate_process_creation_time,
                    self.candidate_image_sha256.0,
                )
            || self.service_state != CandidateServiceTerminalState::Stopped
            || self.scm_process_id != 0
            || self.win32_exit_code != 0
            || self.service_specific_exit_code != 0
            || !self.writer_handles_closed
            || is_zero(&self.seal_ready_writer_handles_closed_readback_sha256.0)
            || is_zero(&self.writer_handles_closed_readback_sha256.0)
            || is_zero(&self.scm_readback_sha256.0)
        {
            return Err(candidate_not_exact_stopped());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct SealCompletePersistenceProjection {
    capsule_sha256: Digest32,
    plan_sha256: Digest32,
    generation_sha256: Digest32,
    transaction_sha256: Digest32,
    manifest_sha256: Digest32,
    generation_object_manifest_sha256: Digest32,
    runner_policy_sealed_identity: RunnerPolicySealedIdentity,
    protected_blob_namespace: ProtectedBlobNamespacePersistenceProjection,
    writer_closure_readback_sha256: Digest32,
    seal_receipt_sha256: Digest32,
    terminal_checkpoint_sha256: Digest32,
    final_commit_store_root_identity_sha256: Digest32,
    authenticated_progress_root_sha256: Digest32,
    restart_readback_sha256: Digest32,
    final_inventory_readback_sha256: Digest32,
    final_root_capabilities_sha256: Digest32,
    object_count: u32,
    terminal_sequence: u32,
    authorization_sha256: Digest32,
}

impl SealCompletePersistenceProjection {
    pub(super) fn manifest_sha256(&self) -> RawDigest {
        self.manifest_sha256.0
    }

    pub(super) fn terminal_sequence(&self) -> u32 {
        self.terminal_sequence
    }

    pub(super) fn runner_policy_sealed_identity(&self) -> RunnerPolicySealedIdentity {
        self.runner_policy_sealed_identity
    }

    pub(super) fn protected_blob_namespace(&self) -> ProtectedBlobNamespacePersistenceProjection {
        self.protected_blob_namespace
    }

    #[cfg(windows)]
    pub(super) fn from_authorization(
        authorization: &GenerationSealTerminalAuthorization,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let terminal = authorization.projection();
        let binding = terminal.binding();
        let sealed_runner = terminal.runner_policy_identity();
        let value = Self {
            capsule_sha256: Digest32::checked(binding.capsule_sha256())?,
            plan_sha256: Digest32::checked(binding.plan_sha256())?,
            generation_sha256: Digest32::checked(binding.generation_sha256())?,
            transaction_sha256: Digest32::checked(binding.transaction_sha256())?,
            manifest_sha256: Digest32::checked(terminal.manifest_sha256())?,
            generation_object_manifest_sha256: Digest32::checked(
                terminal.generation_object_manifest_sha256(),
            )?,
            runner_policy_sealed_identity: RunnerPolicySealedIdentity::new(
                sealed_runner.volume_serial(),
                sealed_runner.file_id(),
                sealed_runner.link_count(),
                sealed_runner.attributes(),
            )?,
            protected_blob_namespace:
                ProtectedBlobNamespacePersistenceProjection::from_generation_seal(
                    terminal.protected_blob_namespace(),
                )?,
            writer_closure_readback_sha256: Digest32::checked(
                terminal.writer_closure_readback_sha256(),
            )?,
            seal_receipt_sha256: Digest32::checked(terminal.seal_receipt_sha256())?,
            terminal_checkpoint_sha256: Digest32::checked(terminal.terminal_checkpoint_sha256())?,
            final_commit_store_root_identity_sha256: Digest32::checked(
                binding.final_commit_store_root_identity_sha256(),
            )?,
            authenticated_progress_root_sha256: Digest32::checked(
                terminal.authenticated_progress_root_sha256(),
            )?,
            restart_readback_sha256: Digest32::checked(terminal.restart_readback_sha256())?,
            final_inventory_readback_sha256: Digest32::checked(
                terminal.final_inventory_readback_sha256(),
            )?,
            final_root_capabilities_sha256: Digest32::checked(
                terminal.final_root_capabilities_sha256(),
            )?,
            object_count: terminal.object_count(),
            terminal_sequence: terminal.terminal_sequence(),
            authorization_sha256: Digest32::checked(terminal.authorization_sha256())?,
        };
        value.validate()?;
        Ok(value)
    }

    #[cfg(test)]
    pub(super) fn exact_test_fixture(binding: FinalizerCommitBinding) -> Self {
        Self {
            capsule_sha256: binding.capsule_sha256,
            plan_sha256: binding.plan_sha256,
            generation_sha256: binding.generation_sha256,
            transaction_sha256: binding.transaction_sha256,
            manifest_sha256: Digest32([0x5d; 32]),
            generation_object_manifest_sha256: binding.plan.generation_object_manifest_sha256,
            runner_policy_sealed_identity: RunnerPolicySealedIdentity::exact_test_fixture(0x6b),
            protected_blob_namespace:
                ProtectedBlobNamespacePersistenceProjection::exact_test_fixture(
                    binding.generation_sha256(),
                    0x6c,
                ),
            writer_closure_readback_sha256: Digest32([0x5e; 32]),
            seal_receipt_sha256: Digest32([0x5f; 32]),
            terminal_checkpoint_sha256: Digest32([0x60; 32]),
            final_commit_store_root_identity_sha256: binding
                .final_commit_store_root_identity_sha256,
            authenticated_progress_root_sha256: binding.final_commit_store_root_identity_sha256,
            restart_readback_sha256: Digest32([0x59; 32]),
            final_inventory_readback_sha256: Digest32([0x5c; 32]),
            final_root_capabilities_sha256: Digest32([0x5b; 32]),
            object_count: GENERATION_SEAL_OBJECT_COUNT as u32,
            terminal_sequence: GENERATION_SEAL_TERMINAL_SEQUENCE,
            authorization_sha256: Digest32([0x6a; 32]),
        }
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if [
            self.capsule_sha256.0,
            self.plan_sha256.0,
            self.generation_sha256.0,
            self.transaction_sha256.0,
            self.manifest_sha256.0,
            self.generation_object_manifest_sha256.0,
            self.writer_closure_readback_sha256.0,
            self.seal_receipt_sha256.0,
            self.terminal_checkpoint_sha256.0,
            self.final_commit_store_root_identity_sha256.0,
            self.authenticated_progress_root_sha256.0,
            self.restart_readback_sha256.0,
            self.final_inventory_readback_sha256.0,
            self.final_root_capabilities_sha256.0,
            self.authorization_sha256.0,
        ]
        .iter()
        .any(is_zero)
            || self.runner_policy_sealed_identity.validate().is_err()
            || self.protected_blob_namespace.validate().is_err()
            || self.protected_blob_namespace.generation_sha256() != self.generation_sha256.0
            || self.authenticated_progress_root_sha256
                != self.final_commit_store_root_identity_sha256
            || self.object_count != GENERATION_SEAL_OBJECT_COUNT as u32
            || self.terminal_sequence != GENERATION_SEAL_TERMINAL_SEQUENCE
        {
            return Err(seal_complete_authorization_invalid());
        }
        Ok(())
    }

    fn validate_against(
        &self,
        binding: FinalizerCommitBinding,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.validate()?;
        if self.capsule_sha256.0 != binding.capsule_sha256()
            || self.plan_sha256.0 != binding.plan_sha256()
            || self.generation_sha256.0 != binding.generation_sha256()
            || self.transaction_sha256.0 != binding.transaction_sha256()
            || self.generation_object_manifest_sha256.0
                != binding.plan.generation_object_manifest_sha256()
            || self.final_commit_store_root_identity_sha256.0
                != binding.final_commit_store_root_identity_sha256()
            || self.authenticated_progress_root_sha256.0
                != binding.final_commit_store_root_identity_sha256()
            || self.protected_blob_namespace.generation_sha256() != binding.generation_sha256()
        {
            return Err(seal_complete_authorization_invalid());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct SealCompleteEvidence {
    artifacts: NonceArtifactPair,
    sealed_security: ExactSealedSecurityReadback,
    candidate: CandidateStoppedReadback,
    generation_seal: SealCompletePersistenceProjection,
}

impl SealCompleteEvidence {
    #[cfg(windows)]
    fn from_generation_seal_authorization(
        authorization: &GenerationSealTerminalAuthorization,
        artifacts: NonceArtifactPair,
        sealed_security: ExactSealedSecurityReadback,
        candidate: CandidateStoppedReadback,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            artifacts,
            sealed_security,
            candidate,
            generation_seal: SealCompletePersistenceProjection::from_authorization(authorization)?,
        };
        value.validate()?;
        Ok(value)
    }

    #[cfg(test)]
    pub(super) fn exact_test_fixture(
        binding: FinalizerCommitBinding,
        artifacts: NonceArtifactPair,
        sealed_security: ExactSealedSecurityReadback,
        candidate: CandidateStoppedReadback,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            artifacts,
            sealed_security,
            candidate,
            generation_seal: SealCompletePersistenceProjection::exact_test_fixture(binding),
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.artifacts.validate()?;
        self.sealed_security.validate()?;
        self.candidate.validate()?;
        self.generation_seal.validate()?;
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(super) enum ActiveHeadCasDisposition {
    Applied,
    AlreadyIdentical,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", content = "details", rename_all = "camelCase")]
pub(super) enum ActiveHeadPriorReadback {
    Absent {
        expected_prior_sha256: Digest32,
        absence_readback_sha256: Digest32,
    },
    Present {
        expected_head_sha256: Digest32,
        observed_head_sha256: Digest32,
    },
}

impl ActiveHeadPriorReadback {
    pub(super) fn absent(
        expected_prior_sha256: RawDigest,
        absence_readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self::Absent {
            expected_prior_sha256: Digest32::checked(expected_prior_sha256)?,
            absence_readback_sha256: Digest32::checked(absence_readback_sha256)?,
        };
        value.validate()?;
        Ok(value)
    }

    pub(super) fn present(
        expected_head_sha256: RawDigest,
        observed_head_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self::Present {
            expected_head_sha256: Digest32::checked(expected_head_sha256)?,
            observed_head_sha256: Digest32::checked(observed_head_sha256)?,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        match self {
            Self::Absent {
                expected_prior_sha256,
                absence_readback_sha256,
            } if !is_zero(&expected_prior_sha256.0) && !is_zero(&absence_readback_sha256.0) => {
                Ok(())
            }
            Self::Present {
                expected_head_sha256,
                observed_head_sha256,
            } if expected_head_sha256 == observed_head_sha256
                && !is_zero(&expected_head_sha256.0) =>
            {
                Ok(())
            }
            _ => Err(active_head_cas_invalid()),
        }
    }

    fn expected_prior_sha256(&self) -> RawDigest {
        match self {
            Self::Absent {
                expected_prior_sha256,
                ..
            } => expected_prior_sha256.0,
            Self::Present {
                expected_head_sha256,
                ..
            } => expected_head_sha256.0,
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct ActiveHeadCasReadback {
    prior: ActiveHeadPriorReadback,
    replacement_head_sha256: Digest32,
    observed_head_sha256: Digest32,
    committed_generation_sha256: Digest32,
    activation_manifest_sha256: Digest32,
    activation_epoch: u64,
    disposition: ActiveHeadCasDisposition,
    readback_sha256: Digest32,
}

impl ActiveHeadCasReadback {
    #[allow(clippy::too_many_arguments)]
    #[cfg(test)]
    pub(super) fn new(
        prior: ActiveHeadPriorReadback,
        replacement_head_sha256: RawDigest,
        observed_head_sha256: RawDigest,
        committed_generation_sha256: RawDigest,
        activation_manifest_sha256: RawDigest,
        activation_epoch: u64,
        disposition: ActiveHeadCasDisposition,
        readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            prior,
            replacement_head_sha256: Digest32::checked(replacement_head_sha256)?,
            observed_head_sha256: Digest32::checked(observed_head_sha256)?,
            committed_generation_sha256: Digest32::checked(committed_generation_sha256)?,
            activation_manifest_sha256: Digest32::checked(activation_manifest_sha256)?,
            activation_epoch,
            disposition,
            readback_sha256: Digest32::checked(readback_sha256)?,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.prior.validate()?;
        if self.replacement_head_sha256 != self.observed_head_sha256
            || self.activation_epoch == 0
            || is_zero(&self.activation_manifest_sha256.0)
            || is_zero(&self.readback_sha256.0)
        {
            return Err(active_head_cas_invalid());
        }
        Ok(())
    }

    pub(super) fn observed_head_sha256(self) -> RawDigest {
        self.observed_head_sha256.0
    }

    pub(super) fn committed_generation_sha256(self) -> RawDigest {
        self.committed_generation_sha256.0
    }

    pub(super) fn activation_manifest_sha256(self) -> RawDigest {
        self.activation_manifest_sha256.0
    }

    pub(super) fn activation_epoch(self) -> u64 {
        self.activation_epoch
    }

    pub(super) fn readback_sha256(self) -> RawDigest {
        self.readback_sha256.0
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct CommittedRuntimeIdentity {
    generation_sha256: Digest32,
    active_head_cas_projection_sha256: Digest32,
    runtime: ExactServiceRuntimeIdentity,
    precommit_handshake_pipe_instance_id: FileId16,
    precommit_handshake_readback_sha256: Digest32,
    controller_pipe_absence_readback_sha256: Digest32,
    generation_writer_roster_readback_sha256: Digest32,
    final_commit_activation_gate_sha256: Digest32,
    precommit_dormant_readback_sha256: Digest32,
}

impl CommittedRuntimeIdentity {
    #[allow(clippy::too_many_arguments)]
    #[cfg(test)]
    pub(super) fn new(
        active_head: ActiveHeadCasReadback,
        runtime: ExactServiceRuntimeIdentity,
        precommit_handshake_pipe_instance_id: [u8; 16],
        precommit_handshake_readback_sha256: RawDigest,
        controller_pipe_absence_readback_sha256: RawDigest,
        generation_writer_roster_readback_sha256: RawDigest,
        final_commit_activation_gate_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        active_head.validate()?;
        runtime.validate()?;
        let generation_sha256 = active_head.committed_generation_sha256;
        let active_head_cas_projection_sha256 =
            Digest32::checked(active_head_cas_projection_sha256(&active_head))?;
        let precommit_handshake_readback_sha256 =
            Digest32::checked(precommit_handshake_readback_sha256)?;
        let controller_pipe_absence_readback_sha256 =
            Digest32::checked(controller_pipe_absence_readback_sha256)?;
        let generation_writer_roster_readback_sha256 =
            Digest32::checked(generation_writer_roster_readback_sha256)?;
        let final_commit_activation_gate_sha256 =
            Digest32::checked(final_commit_activation_gate_sha256)?;
        let precommit_dormant_readback_sha256 =
            Digest32::checked(precommit_dormant_runtime_sha256(
                generation_sha256.0,
                active_head_cas_projection_sha256.0,
                &runtime,
                precommit_handshake_pipe_instance_id,
                precommit_handshake_readback_sha256.0,
                controller_pipe_absence_readback_sha256.0,
                generation_writer_roster_readback_sha256.0,
                final_commit_activation_gate_sha256.0,
            ))?;
        let value = Self {
            generation_sha256,
            active_head_cas_projection_sha256,
            runtime,
            precommit_handshake_pipe_instance_id: FileId16(precommit_handshake_pipe_instance_id),
            precommit_handshake_readback_sha256,
            controller_pipe_absence_readback_sha256,
            generation_writer_roster_readback_sha256,
            final_commit_activation_gate_sha256,
            precommit_dormant_readback_sha256,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.runtime.validate()?;
        if is_zero(&self.generation_sha256.0)
            || is_zero(&self.precommit_handshake_pipe_instance_id.0)
            || is_zero(&self.precommit_handshake_readback_sha256.0)
            || is_zero(&self.controller_pipe_absence_readback_sha256.0)
            || is_zero(&self.generation_writer_roster_readback_sha256.0)
            || is_zero(&self.final_commit_activation_gate_sha256.0)
            || self.precommit_dormant_readback_sha256.0
                != precommit_dormant_runtime_sha256(
                    self.generation_sha256.0,
                    self.active_head_cas_projection_sha256.0,
                    &self.runtime,
                    self.precommit_handshake_pipe_instance_id.0,
                    self.precommit_handshake_readback_sha256.0,
                    self.controller_pipe_absence_readback_sha256.0,
                    self.generation_writer_roster_readback_sha256.0,
                    self.final_commit_activation_gate_sha256.0,
                )
        {
            return Err(committed_runtime_invalid());
        }
        Ok(())
    }

    fn process_identity_sha256(&self) -> RawDigest {
        self.runtime.process_identity_sha256()
    }

    pub(super) fn generation_sha256(self) -> RawDigest {
        self.generation_sha256.0
    }

    pub(super) fn exact_service_identity_sha256(self) -> RawDigest {
        self.runtime.exact_runtime_instance_sha256.0
    }

    pub(super) fn exact_service_configuration_sha256(self) -> RawDigest {
        self.runtime.exact_service_configuration_sha256.0
    }

    pub(super) fn runtime(self) -> ExactServiceRuntimeIdentity {
        self.runtime
    }

    pub(super) fn process_id(self) -> u32 {
        self.runtime.process.process_id()
    }

    pub(super) fn process_creation_time(self) -> u64 {
        self.runtime.process.process_creation_time()
    }

    pub(super) fn image_sha256(self) -> RawDigest {
        *self.runtime.process.image_sha256()
    }

    pub(super) fn precommit_handshake_pipe_instance_id(self) -> [u8; 16] {
        self.precommit_handshake_pipe_instance_id.0
    }

    pub(super) fn final_commit_activation_gate_sha256(self) -> RawDigest {
        self.final_commit_activation_gate_sha256.0
    }

    pub(super) fn precommit_dormant_readback_sha256(self) -> RawDigest {
        self.precommit_dormant_readback_sha256.0
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct FinalCommitEvidence {
    active_head: ActiveHeadCasReadback,
    committed_runtime: CommittedRuntimeIdentity,
    runner_policy_sealed_identity: RunnerPolicySealedIdentity,
    protected_blob_namespace: ProtectedBlobNamespacePersistenceProjection,
    zero_residue: OperationZeroResidueReadback,
}

impl FinalCommitEvidence {
    #[cfg(test)]
    pub(super) fn new(
        active_head: ActiveHeadCasReadback,
        committed_runtime: CommittedRuntimeIdentity,
        zero_residue: OperationZeroResidueReadback,
    ) -> Result<Self, AuthorityMaintenanceError> {
        Self::new_with_runner_policy_sealed_identity(
            active_head,
            committed_runtime,
            RunnerPolicySealedIdentity::exact_test_fixture(0x6b),
            zero_residue,
        )
    }

    #[cfg(test)]
    pub(super) fn new_with_runner_policy_sealed_identity(
        active_head: ActiveHeadCasReadback,
        committed_runtime: CommittedRuntimeIdentity,
        runner_policy_sealed_identity: RunnerPolicySealedIdentity,
        zero_residue: OperationZeroResidueReadback,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let protected_blob_namespace =
            ProtectedBlobNamespacePersistenceProjection::exact_test_fixture(
                committed_runtime.generation_sha256.0,
                0x6c,
            );
        Self::new_with_sealed_runtime_bindings(
            active_head,
            committed_runtime,
            runner_policy_sealed_identity,
            protected_blob_namespace,
            zero_residue,
        )
    }

    pub(super) fn new_with_sealed_runtime_bindings(
        active_head: ActiveHeadCasReadback,
        committed_runtime: CommittedRuntimeIdentity,
        runner_policy_sealed_identity: RunnerPolicySealedIdentity,
        protected_blob_namespace: ProtectedBlobNamespacePersistenceProjection,
        zero_residue: OperationZeroResidueReadback,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            active_head,
            committed_runtime,
            runner_policy_sealed_identity,
            protected_blob_namespace,
            zero_residue,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.active_head.validate()?;
        self.committed_runtime.validate()?;
        self.runner_policy_sealed_identity.validate()?;
        self.protected_blob_namespace.validate()?;
        if self.protected_blob_namespace.generation_sha256()
            != self.committed_runtime.generation_sha256.0
        {
            return Err(committed_generation_mismatch());
        }
        self.zero_residue.validate()?;
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(super) enum FinalizerCommitStage {
    TransactionStarted,
    ApplyReady,
    SealReady,
    ExitReady,
    SealComplete,
    FinalCommit,
}

impl FinalizerCommitStage {
    fn receipt_domain(self) -> &'static [u8] {
        match self {
            Self::TransactionStarted => TRANSACTION_STARTED_RECEIPT_DOMAIN,
            Self::ApplyReady => APPLY_READY_RECEIPT_DOMAIN,
            Self::SealReady => SEAL_READY_RECEIPT_DOMAIN,
            Self::ExitReady => EXIT_READY_RECEIPT_DOMAIN,
            Self::SealComplete => SEAL_COMPLETE_RECEIPT_DOMAIN,
            Self::FinalCommit => FINAL_COMMIT_RECEIPT_DOMAIN,
        }
    }

    fn predecessor(self) -> Option<Self> {
        match self {
            Self::TransactionStarted => None,
            Self::ApplyReady => Some(Self::TransactionStarted),
            Self::SealReady => Some(Self::ApplyReady),
            Self::ExitReady => Some(Self::SealReady),
            Self::SealComplete => Some(Self::ExitReady),
            Self::FinalCommit => Some(Self::SealComplete),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", content = "details", rename_all = "camelCase")]
enum FinalizerCommitEvidence {
    TransactionStarted(TransactionStartedEvidence),
    ApplyReady(ApplyReadyEvidence),
    SealReady(SealReadyEvidence),
    ExitReady(ExitReadyEvidence),
    SealComplete(SealCompleteEvidence),
    FinalCommit(FinalCommitEvidence),
}

impl FinalizerCommitEvidence {
    fn stage(&self) -> FinalizerCommitStage {
        match self {
            Self::TransactionStarted(_) => FinalizerCommitStage::TransactionStarted,
            Self::ApplyReady(_) => FinalizerCommitStage::ApplyReady,
            Self::SealReady(_) => FinalizerCommitStage::SealReady,
            Self::ExitReady(_) => FinalizerCommitStage::ExitReady,
            Self::SealComplete(_) => FinalizerCommitStage::SealComplete,
            Self::FinalCommit(_) => FinalizerCommitStage::FinalCommit,
        }
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        match self {
            Self::TransactionStarted(value) => value.validate(),
            Self::ApplyReady(value) => value.validate(),
            Self::SealReady(value) => value.validate(),
            Self::ExitReady(value) => value.validate(),
            Self::SealComplete(value) => value.validate(),
            Self::FinalCommit(value) => value.validate(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct FinalizerCommitReceipt {
    schema: String,
    stage: FinalizerCommitStage,
    binding: FinalizerCommitBinding,
    previous_receipt_sha256: Digest32,
    evidence: FinalizerCommitEvidence,
}

impl FinalizerCommitReceipt {
    fn new(
        stage: FinalizerCommitStage,
        binding: FinalizerCommitBinding,
        previous_receipt_sha256: RawDigest,
        evidence: FinalizerCommitEvidence,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            schema: FINALIZER_COMMIT_RECEIPT_SCHEMA.to_string(),
            stage,
            binding,
            previous_receipt_sha256: Digest32::checked(previous_receipt_sha256)?,
            evidence,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if self.schema != FINALIZER_COMMIT_RECEIPT_SCHEMA
            || self.stage != self.evidence.stage()
            || is_zero(&self.previous_receipt_sha256.0)
        {
            return Err(receipt_invalid());
        }
        self.binding.validate()?;
        self.evidence.validate()?;
        Ok(())
    }

    fn canonical_json(&self) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        self.validate()?;
        serde_json::to_vec(self).map_err(|_| canonical_invalid())
    }

    fn digest(&self) -> Result<RawDigest, AuthorityMaintenanceError> {
        let canonical = self.canonical_json()?;
        Ok(domain_separated_digest(
            self.stage.receipt_domain(),
            &canonical,
        ))
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct FinalizerCommitProtocolState {
    schema: String,
    transaction_started: FinalizerCommitReceipt,
    apply_ready: Option<FinalizerCommitReceipt>,
    seal_ready: Option<FinalizerCommitReceipt>,
    exit_ready: Option<FinalizerCommitReceipt>,
    seal_complete: Option<FinalizerCommitReceipt>,
    final_commit: Option<FinalizerCommitReceipt>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct FinalCommitPersistenceProjection {
    binding: FinalizerCommitBinding,
    operation: AuthorityMaintenanceOperation,
    active_head: ActiveHeadCasReadback,
    committed_runtime: CommittedRuntimeIdentity,
    runner_policy_sealed_identity: RunnerPolicySealedIdentity,
    protected_blob_namespace: ProtectedBlobNamespacePersistenceProjection,
    zero_residue: OperationZeroResidueReadback,
    expected_final_commit_gate_sha256: RawDigest,
    final_commit_receipt_sha256: RawDigest,
    protocol_state_sha256: RawDigest,
}

impl FinalCommitPersistenceProjection {
    pub(super) fn binding(self) -> FinalizerCommitBinding {
        self.binding
    }

    pub(super) fn operation(self) -> AuthorityMaintenanceOperation {
        self.operation
    }

    pub(super) fn active_head(self) -> ActiveHeadCasReadback {
        self.active_head
    }

    pub(super) fn committed_runtime(self) -> CommittedRuntimeIdentity {
        self.committed_runtime
    }

    pub(super) fn runner_policy_sealed_identity(self) -> RunnerPolicySealedIdentity {
        self.runner_policy_sealed_identity
    }

    pub(super) fn protected_blob_namespace(self) -> ProtectedBlobNamespacePersistenceProjection {
        self.protected_blob_namespace
    }

    pub(super) fn zero_residue(self) -> OperationZeroResidueReadback {
        self.zero_residue
    }

    pub(super) fn expected_final_commit_gate_sha256(self) -> RawDigest {
        self.expected_final_commit_gate_sha256
    }

    pub(super) fn final_commit_receipt_sha256(self) -> RawDigest {
        self.final_commit_receipt_sha256
    }

    pub(super) fn protocol_state_sha256(self) -> RawDigest {
        self.protocol_state_sha256
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct SealReadyRecoveryProjection {
    artifacts: NonceArtifactPair,
    writer_handles: WriterHandlesClosedReadback,
    candidate: CandidateActivationIdentity,
}

impl SealReadyRecoveryProjection {
    pub(super) fn artifacts(self) -> NonceArtifactPair {
        self.artifacts
    }

    pub(super) fn writer_handles(self) -> WriterHandlesClosedReadback {
        self.writer_handles
    }

    pub(super) fn candidate(self) -> CandidateActivationIdentity {
        self.candidate
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct ExitReadyRecoveryProjection {
    worker: ExactServiceProcessIdentity,
    worker_writer_handles_closed_readback_sha256: RawDigest,
    durable_exit_readback_sha256: RawDigest,
}

impl ExitReadyRecoveryProjection {
    pub(super) fn worker(self) -> ExactServiceProcessIdentity {
        self.worker
    }

    pub(super) fn worker_writer_handles_closed_readback_sha256(self) -> RawDigest {
        self.worker_writer_handles_closed_readback_sha256
    }

    pub(super) fn durable_exit_readback_sha256(self) -> RawDigest {
        self.durable_exit_readback_sha256
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct FinalizerCommitRecoveryProjection {
    binding: FinalizerCommitBinding,
    latest_stage: FinalizerCommitStage,
    transaction_worker: ExactServiceProcessIdentity,
    durable_start_readback_sha256: RawDigest,
    worker_activation_proof_sha256: RawDigest,
    exact_apply_readback_sha256: Option<RawDigest>,
    seal_ready: Option<SealReadyRecoveryProjection>,
    exit_ready: Option<ExitReadyRecoveryProjection>,
    seal_complete: Option<SealCompletePersistenceProjection>,
    final_commit: Option<FinalCommitPersistenceProjection>,
}

impl FinalizerCommitRecoveryProjection {
    pub(super) fn binding(self) -> FinalizerCommitBinding {
        self.binding
    }

    pub(super) fn latest_stage(self) -> FinalizerCommitStage {
        self.latest_stage
    }

    pub(super) fn transaction_worker(self) -> ExactServiceProcessIdentity {
        self.transaction_worker
    }

    pub(super) fn durable_start_readback_sha256(self) -> RawDigest {
        self.durable_start_readback_sha256
    }

    pub(super) fn worker_activation_proof_sha256(self) -> RawDigest {
        self.worker_activation_proof_sha256
    }

    pub(super) fn exact_apply_readback_sha256(self) -> Option<RawDigest> {
        self.exact_apply_readback_sha256
    }

    pub(super) fn seal_ready(self) -> Option<SealReadyRecoveryProjection> {
        self.seal_ready
    }

    pub(super) fn exit_ready(self) -> Option<ExitReadyRecoveryProjection> {
        self.exit_ready
    }

    pub(super) fn seal_complete(self) -> Option<SealCompletePersistenceProjection> {
        self.seal_complete
    }

    pub(super) fn final_commit(self) -> Option<FinalCommitPersistenceProjection> {
        self.final_commit
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct PostcommitServingRuntimeReadback {
    final_commit: FinalCommitPersistenceProjection,
    observed_runtime: ExactServiceRuntimeIdentity,
    observed_final_commit_receipt_sha256: RawDigest,
    controller_pipe_instance_id: [u8; 16],
    controller_pipe_handshake_readback_sha256: RawDigest,
    serving_state_readback_sha256: RawDigest,
    readback_sha256: RawDigest,
}

impl PostcommitServingRuntimeReadback {
    #[allow(clippy::too_many_arguments)]
    #[cfg(test)]
    pub(super) fn exact(
        final_commit: FinalCommitPersistenceProjection,
        observed_runtime: ExactServiceRuntimeIdentity,
        observed_final_commit_receipt_sha256: RawDigest,
        controller_pipe_instance_id: [u8; 16],
        controller_pipe_handshake_readback_sha256: RawDigest,
        serving_state_readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        observed_runtime.validate()?;
        let runtime = final_commit.committed_runtime;
        if observed_runtime != runtime.runtime
            || observed_final_commit_receipt_sha256 != final_commit.final_commit_receipt_sha256
            || runtime.final_commit_activation_gate_sha256.0
                != final_commit.expected_final_commit_gate_sha256
            || is_zero(&controller_pipe_instance_id)
            || controller_pipe_instance_id == runtime.precommit_handshake_pipe_instance_id.0
            || is_zero(&controller_pipe_handshake_readback_sha256)
            || is_zero(&serving_state_readback_sha256)
        {
            return Err(postcommit_serving_readback_invalid());
        }
        let readback_sha256 = postcommit_serving_readback_sha256(
            &final_commit,
            observed_runtime,
            observed_final_commit_receipt_sha256,
            controller_pipe_instance_id,
            controller_pipe_handshake_readback_sha256,
            serving_state_readback_sha256,
        );
        Ok(Self {
            final_commit,
            observed_runtime,
            observed_final_commit_receipt_sha256,
            controller_pipe_instance_id,
            controller_pipe_handshake_readback_sha256,
            serving_state_readback_sha256,
            readback_sha256,
        })
    }

    pub(super) fn readback_sha256(&self) -> RawDigest {
        self.readback_sha256
    }
}

/// Exact absence proof for the precommit process before a committed restart.
/// A caller-supplied digest is insufficient: the readback must bind the prior
/// runtime instance and process identity, a terminal process-handle
/// observation, an exact stopped service-manager state, and a separate proof
/// that the old numeric process identifier was not reused for the old creation
/// time.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct PreviousRuntimeAbsenceReadback {
    previous_runtime_instance_sha256: RawDigest,
    previous_process_id: u32,
    previous_process_creation_time: u64,
    observed_service_state: u32,
    observed_service_process_id: u32,
    observed_win32_exit_code: u32,
    observed_service_specific_exit_code: u32,
    prior_process_exit_readback_sha256: RawDigest,
    process_reuse_absence_readback_sha256: RawDigest,
    stopped_service_readback_sha256: RawDigest,
    readback_sha256: RawDigest,
}

impl PreviousRuntimeAbsenceReadback {
    #[allow(clippy::too_many_arguments)]
    #[cfg(test)]
    pub(super) fn from_observed(
        previous_runtime: CommittedRuntimeIdentity,
        previous_runtime_instance_sha256: RawDigest,
        previous_process_id: u32,
        previous_process_creation_time: u64,
        observed_service_state: u32,
        observed_service_process_id: u32,
        observed_win32_exit_code: u32,
        observed_service_specific_exit_code: u32,
        prior_process_exit_readback_sha256: RawDigest,
        process_reuse_absence_readback_sha256: RawDigest,
        stopped_service_readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            previous_runtime_instance_sha256,
            previous_process_id,
            previous_process_creation_time,
            observed_service_state,
            observed_service_process_id,
            observed_win32_exit_code,
            observed_service_specific_exit_code,
            prior_process_exit_readback_sha256,
            process_reuse_absence_readback_sha256,
            stopped_service_readback_sha256,
            readback_sha256: previous_runtime_absence_readback_sha256(
                previous_runtime_instance_sha256,
                previous_process_id,
                previous_process_creation_time,
                observed_service_state,
                observed_service_process_id,
                observed_win32_exit_code,
                observed_service_specific_exit_code,
                prior_process_exit_readback_sha256,
                process_reuse_absence_readback_sha256,
                stopped_service_readback_sha256,
            ),
        };
        value.validate_for(previous_runtime)?;
        Ok(value)
    }

    fn validate_for(
        &self,
        previous_runtime: CommittedRuntimeIdentity,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.previous_runtime_instance_sha256 != previous_runtime.exact_service_identity_sha256()
            || self.previous_process_id != previous_runtime.process_id()
            || self.previous_process_creation_time != previous_runtime.process_creation_time()
            || self.observed_service_state != SERVICE_STATE_STOPPED
            || self.observed_service_process_id != 0
            || self.observed_win32_exit_code != 0
            || self.observed_service_specific_exit_code != 0
            || self.previous_process_id == 0
            || self.previous_process_creation_time == 0
            || [
                self.prior_process_exit_readback_sha256,
                self.process_reuse_absence_readback_sha256,
                self.stopped_service_readback_sha256,
            ]
            .iter()
            .any(is_zero)
            || self.readback_sha256
                != previous_runtime_absence_readback_sha256(
                    self.previous_runtime_instance_sha256,
                    self.previous_process_id,
                    self.previous_process_creation_time,
                    self.observed_service_state,
                    self.observed_service_process_id,
                    self.observed_win32_exit_code,
                    self.observed_service_specific_exit_code,
                    self.prior_process_exit_readback_sha256,
                    self.process_reuse_absence_readback_sha256,
                    self.stopped_service_readback_sha256,
                )
        {
            return Err(postcommit_serving_readback_invalid());
        }
        Ok(())
    }

    pub(super) fn readback_sha256(self) -> RawDigest {
        self.readback_sha256
    }
}

/// Readback for the only restart allowed after the immutable final receipt is
/// durable. The ordinary path above still requires the precommit process to
/// remain identical. This path instead proves that the old process is gone,
/// the committed generation and active head did not change, the exact service
/// configuration and image still match the sealed plan, and a distinct
/// process activated from the already-existing final receipt without
/// rewriting it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct RecoveredPostcommitServingRuntimeReadback {
    final_commit: FinalCommitPersistenceProjection,
    previous_runtime_absence: PreviousRuntimeAbsenceReadback,
    observed_active_head_sha256: RawDigest,
    observed_runtime: ExactServiceRuntimeIdentity,
    observed_generation_sha256: RawDigest,
    observed_final_commit_receipt_sha256: RawDigest,
    observed_final_commit_gate_sha256: RawDigest,
    controller_pipe_instance_id: [u8; 16],
    generation_handshake_readback_sha256: RawDigest,
    controller_pipe_handshake_readback_sha256: RawDigest,
    serving_state_readback_sha256: RawDigest,
    readback_sha256: RawDigest,
}

impl RecoveredPostcommitServingRuntimeReadback {
    #[allow(clippy::too_many_arguments)]
    #[cfg(test)]
    pub(super) fn after_committed_restart(
        final_commit: FinalCommitPersistenceProjection,
        previous_runtime_absence: PreviousRuntimeAbsenceReadback,
        observed_active_head_sha256: RawDigest,
        observed_runtime: ExactServiceRuntimeIdentity,
        observed_generation_sha256: RawDigest,
        observed_final_commit_receipt_sha256: RawDigest,
        observed_final_commit_gate_sha256: RawDigest,
        controller_pipe_instance_id: [u8; 16],
        generation_handshake_readback_sha256: RawDigest,
        controller_pipe_handshake_readback_sha256: RawDigest,
        serving_state_readback_sha256: RawDigest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        final_commit.binding.validate()?;
        final_commit.active_head.validate()?;
        observed_runtime.validate()?;
        let committed_runtime = final_commit.committed_runtime;
        previous_runtime_absence.validate_for(committed_runtime)?;
        let plan = final_commit.binding.plan_binding();
        if observed_active_head_sha256 != final_commit.active_head.observed_head_sha256.0
            || observed_active_head_sha256 != plan.expected_active_head_replacement_sha256()
            || observed_runtime.exact_service_configuration_sha256.0
                != plan.exact_service_configuration_sha256.0
            || observed_runtime.exact_service_configuration_sha256
                != committed_runtime.runtime.exact_service_configuration_sha256
            || observed_runtime.process.image_sha256()
                != committed_runtime.runtime.process.image_sha256()
            || observed_runtime.process.image_sha256() != &plan.expected_service_image_sha256.0
            || observed_runtime.exact_runtime_instance_sha256
                == committed_runtime.runtime.exact_runtime_instance_sha256
            || observed_runtime.process_identity_sha256()
                == committed_runtime.process_identity_sha256()
            || observed_generation_sha256 != final_commit.binding.generation_sha256()
            || observed_generation_sha256 != committed_runtime.generation_sha256.0
            || observed_final_commit_receipt_sha256 != final_commit.final_commit_receipt_sha256
            || observed_final_commit_gate_sha256 != final_commit.expected_final_commit_gate_sha256
            || observed_final_commit_gate_sha256
                != committed_runtime.final_commit_activation_gate_sha256.0
            || is_zero(&controller_pipe_instance_id)
            || controller_pipe_instance_id
                == committed_runtime.precommit_handshake_pipe_instance_id.0
            || [
                generation_handshake_readback_sha256,
                controller_pipe_handshake_readback_sha256,
                serving_state_readback_sha256,
            ]
            .iter()
            .any(is_zero)
        {
            return Err(postcommit_serving_readback_invalid());
        }
        let readback_sha256 = recovered_postcommit_serving_readback_sha256(
            &final_commit,
            previous_runtime_absence,
            observed_active_head_sha256,
            observed_runtime,
            observed_generation_sha256,
            observed_final_commit_receipt_sha256,
            observed_final_commit_gate_sha256,
            controller_pipe_instance_id,
            generation_handshake_readback_sha256,
            controller_pipe_handshake_readback_sha256,
            serving_state_readback_sha256,
        );
        Ok(Self {
            final_commit,
            previous_runtime_absence,
            observed_active_head_sha256,
            observed_runtime,
            observed_generation_sha256,
            observed_final_commit_receipt_sha256,
            observed_final_commit_gate_sha256,
            controller_pipe_instance_id,
            generation_handshake_readback_sha256,
            controller_pipe_handshake_readback_sha256,
            serving_state_readback_sha256,
            readback_sha256,
        })
    }

    pub(super) fn readback_sha256(&self) -> RawDigest {
        self.readback_sha256
    }
}

impl FinalizerCommitProtocolState {
    pub(super) fn transaction_started(
        binding: FinalizerCommitBinding,
        evidence: TransactionStartedEvidence,
    ) -> Result<Self, AuthorityMaintenanceError> {
        binding.validate()?;
        evidence.validate()?;
        let previous = protocol_genesis_sha256(&binding);
        let transaction_started = FinalizerCommitReceipt::new(
            FinalizerCommitStage::TransactionStarted,
            binding,
            previous,
            FinalizerCommitEvidence::TransactionStarted(evidence),
        )?;
        let value = Self {
            schema: FINALIZER_COMMIT_PROTOCOL_SCHEMA.to_string(),
            transaction_started,
            apply_ready: None,
            seal_ready: None,
            exit_ready: None,
            seal_complete: None,
            final_commit: None,
        };
        value.validate()?;
        Ok(value)
    }

    pub(super) fn from_canonical_json(bytes: &[u8]) -> Result<Self, AuthorityMaintenanceError> {
        let value: Self = serde_json::from_slice(bytes).map_err(|_| canonical_invalid())?;
        value.validate()?;
        let canonical = serde_json::to_vec(&value).map_err(|_| canonical_invalid())?;
        if canonical != bytes {
            return Err(canonical_invalid());
        }
        Ok(value)
    }

    pub(super) fn to_canonical_json(&self) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        self.validate()?;
        serde_json::to_vec(self).map_err(|_| canonical_invalid())
    }

    pub(super) fn state_sha256(&self) -> Result<RawDigest, AuthorityMaintenanceError> {
        Ok(domain_separated_digest(
            PROTOCOL_STATE_DOMAIN,
            &self.to_canonical_json()?,
        ))
    }

    pub(super) fn binding(&self) -> FinalizerCommitBinding {
        self.transaction_started.binding
    }

    pub(super) fn recovery_projection(
        &self,
    ) -> Result<FinalizerCommitRecoveryProjection, AuthorityMaintenanceError> {
        self.validate()?;
        let transaction_started = match &self.transaction_started.evidence {
            FinalizerCommitEvidence::TransactionStarted(value) => *value,
            _ => return Err(protocol_chain_invalid()),
        };
        let exact_apply_readback_sha256 = self
            .apply_ready
            .as_ref()
            .map(|receipt| match &receipt.evidence {
                FinalizerCommitEvidence::ApplyReady(value) => {
                    Ok(value.exact_apply_readback_sha256())
                }
                _ => Err(protocol_chain_invalid()),
            })
            .transpose()?;
        let seal_ready = self
            .seal_ready
            .as_ref()
            .map(|receipt| match &receipt.evidence {
                FinalizerCommitEvidence::SealReady(value) => Ok(SealReadyRecoveryProjection {
                    artifacts: value.artifacts(),
                    writer_handles: value.writer_handles(),
                    candidate: value.candidate(),
                }),
                _ => Err(protocol_chain_invalid()),
            })
            .transpose()?;
        let exit_ready = self
            .exit_ready
            .as_ref()
            .map(|receipt| match &receipt.evidence {
                FinalizerCommitEvidence::ExitReady(value) => Ok(ExitReadyRecoveryProjection {
                    worker: value.worker(),
                    worker_writer_handles_closed_readback_sha256: value
                        .worker_writer_handles_closed_readback_sha256(),
                    durable_exit_readback_sha256: value.durable_exit_readback_sha256(),
                }),
                _ => Err(protocol_chain_invalid()),
            })
            .transpose()?;
        Ok(FinalizerCommitRecoveryProjection {
            binding: self.binding(),
            latest_stage: self.latest_stage(),
            transaction_worker: transaction_started.worker(),
            durable_start_readback_sha256: transaction_started.durable_start_readback_sha256(),
            worker_activation_proof_sha256: transaction_started.worker_activation_proof_sha256(),
            exact_apply_readback_sha256,
            seal_ready,
            exit_ready,
            seal_complete: self.seal_complete_persistence_projection()?,
            final_commit: self.final_commit_persistence_projection()?,
        })
    }

    pub(super) fn final_commit_persistence_projection(
        &self,
    ) -> Result<Option<FinalCommitPersistenceProjection>, AuthorityMaintenanceError> {
        self.validate()?;
        let Some(receipt) = self.final_commit.as_ref() else {
            return Ok(None);
        };
        let evidence = match receipt.evidence {
            FinalizerCommitEvidence::FinalCommit(value) => value,
            _ => return Err(protocol_chain_invalid()),
        };
        Ok(Some(FinalCommitPersistenceProjection {
            binding: self.binding(),
            operation: self.binding().plan.operation,
            active_head: evidence.active_head,
            committed_runtime: evidence.committed_runtime,
            runner_policy_sealed_identity: evidence.runner_policy_sealed_identity,
            protected_blob_namespace: evidence.protected_blob_namespace,
            zero_residue: evidence.zero_residue,
            expected_final_commit_gate_sha256: self.binding().expected_final_commit_gate_sha256(),
            final_commit_receipt_sha256: receipt.digest()?,
            protocol_state_sha256: self.state_sha256()?,
        }))
    }

    pub(super) fn seal_complete_persistence_projection(
        &self,
    ) -> Result<Option<SealCompletePersistenceProjection>, AuthorityMaintenanceError> {
        self.validate()?;
        let Some(receipt) = self.seal_complete.as_ref() else {
            return Ok(None);
        };
        let evidence = match receipt.evidence {
            FinalizerCommitEvidence::SealComplete(value) => value,
            _ => return Err(protocol_chain_invalid()),
        };
        Ok(Some(evidence.generation_seal))
    }

    pub(super) fn latest_stage(&self) -> FinalizerCommitStage {
        if self.final_commit.is_some() {
            FinalizerCommitStage::FinalCommit
        } else if self.seal_complete.is_some() {
            FinalizerCommitStage::SealComplete
        } else if self.exit_ready.is_some() {
            FinalizerCommitStage::ExitReady
        } else if self.seal_ready.is_some() {
            FinalizerCommitStage::SealReady
        } else if self.apply_ready.is_some() {
            FinalizerCommitStage::ApplyReady
        } else {
            FinalizerCommitStage::TransactionStarted
        }
    }

    pub(super) fn receipt_sha256(
        &self,
        stage: FinalizerCommitStage,
    ) -> Result<Option<RawDigest>, AuthorityMaintenanceError> {
        self.receipt(stage)
            .map(FinalizerCommitReceipt::digest)
            .transpose()
    }

    pub(super) fn system_actor(&mut self) -> SystemCommitProtocolActor<'_> {
        SystemCommitProtocolActor { state: self }
    }

    pub(super) fn elevated_finalizer(&mut self) -> ElevatedFinalizerCommitProtocolActor<'_> {
        ElevatedFinalizerCommitProtocolActor { state: self }
    }

    fn receipt(&self, stage: FinalizerCommitStage) -> Option<&FinalizerCommitReceipt> {
        match stage {
            FinalizerCommitStage::TransactionStarted => Some(&self.transaction_started),
            FinalizerCommitStage::ApplyReady => self.apply_ready.as_ref(),
            FinalizerCommitStage::SealReady => self.seal_ready.as_ref(),
            FinalizerCommitStage::ExitReady => self.exit_ready.as_ref(),
            FinalizerCommitStage::SealComplete => self.seal_complete.as_ref(),
            FinalizerCommitStage::FinalCommit => self.final_commit.as_ref(),
        }
    }

    fn set_receipt(&mut self, receipt: FinalizerCommitReceipt) {
        match receipt.stage {
            FinalizerCommitStage::TransactionStarted => self.transaction_started = receipt,
            FinalizerCommitStage::ApplyReady => self.apply_ready = Some(receipt),
            FinalizerCommitStage::SealReady => self.seal_ready = Some(receipt),
            FinalizerCommitStage::ExitReady => self.exit_ready = Some(receipt),
            FinalizerCommitStage::SealComplete => self.seal_complete = Some(receipt),
            FinalizerCommitStage::FinalCommit => self.final_commit = Some(receipt),
        }
    }

    fn record(
        &mut self,
        stage: FinalizerCommitStage,
        evidence: FinalizerCommitEvidence,
    ) -> Result<DurableReceiptWrite, AuthorityMaintenanceError> {
        self.validate()?;
        if stage == FinalizerCommitStage::TransactionStarted || evidence.stage() != stage {
            return Err(protocol_state_invalid());
        }
        let predecessor = stage.predecessor().ok_or_else(protocol_state_invalid)?;
        let predecessor_receipt = self.receipt(predecessor).ok_or_else(protocol_gap)?;
        let previous_receipt_sha256 = predecessor_receipt.digest()?;
        let candidate =
            FinalizerCommitReceipt::new(stage, self.binding(), previous_receipt_sha256, evidence)?;
        if let Some(existing) = self.receipt(stage) {
            if existing != &candidate {
                return Err(receipt_conflict());
            }
            return DurableReceiptWrite::from_receipt(
                existing,
                ProtocolWriteDisposition::AlreadyIdentical,
            );
        }
        if self.latest_stage() != predecessor {
            return Err(protocol_gap());
        }
        self.set_receipt(candidate.clone());
        if let Err(error) = self.validate() {
            match stage {
                FinalizerCommitStage::ApplyReady => self.apply_ready = None,
                FinalizerCommitStage::SealReady => self.seal_ready = None,
                FinalizerCommitStage::ExitReady => self.exit_ready = None,
                FinalizerCommitStage::SealComplete => self.seal_complete = None,
                FinalizerCommitStage::FinalCommit => self.final_commit = None,
                FinalizerCommitStage::TransactionStarted => {}
            }
            return Err(error);
        }
        DurableReceiptWrite::from_receipt(&candidate, ProtocolWriteDisposition::Created)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if self.schema != FINALIZER_COMMIT_PROTOCOL_SCHEMA {
            return Err(protocol_state_invalid());
        }
        self.transaction_started.validate()?;
        if self.transaction_started.stage != FinalizerCommitStage::TransactionStarted
            || self.transaction_started.binding.validate().is_err()
            || self.transaction_started.previous_receipt_sha256.0
                != protocol_genesis_sha256(&self.transaction_started.binding)
        {
            return Err(protocol_chain_invalid());
        }
        let expected_binding = self.transaction_started.binding;
        let ordered = [
            (FinalizerCommitStage::ApplyReady, self.apply_ready.as_ref()),
            (FinalizerCommitStage::SealReady, self.seal_ready.as_ref()),
            (FinalizerCommitStage::ExitReady, self.exit_ready.as_ref()),
            (
                FinalizerCommitStage::SealComplete,
                self.seal_complete.as_ref(),
            ),
            (
                FinalizerCommitStage::FinalCommit,
                self.final_commit.as_ref(),
            ),
        ];
        let mut previous = &self.transaction_started;
        let mut gap_seen = false;
        for (expected_stage, receipt) in ordered {
            match receipt {
                None => gap_seen = true,
                Some(value) => {
                    if gap_seen
                        || value.stage != expected_stage
                        || value.binding != expected_binding
                        || value.previous_receipt_sha256.0 != previous.digest()?
                    {
                        return Err(protocol_chain_invalid());
                    }
                    value.validate()?;
                    previous = value;
                }
            }
        }
        let transaction_started_evidence = match &self.transaction_started.evidence {
            FinalizerCommitEvidence::TransactionStarted(value) => value,
            _ => return Err(protocol_chain_invalid()),
        };
        if transaction_started_evidence.commit_binding_sha256.0
            != commit_binding_identity_sha256(&expected_binding)
            || transaction_started_evidence
                .worker
                .exact_service_identity_sha256
                != expected_binding
                    .plan
                    .expected_worker_service_identity_sha256
            || transaction_started_evidence.worker.image_sha256
                != expected_binding.plan.expected_worker_image_sha256
        {
            return Err(worker_identity_mismatch());
        }
        if let Some(seal_ready) = self.seal_ready.as_ref() {
            let ready_evidence = match &seal_ready.evidence {
                FinalizerCommitEvidence::SealReady(value) => value,
                _ => return Err(protocol_chain_invalid()),
            };
            if ready_evidence.writer_handles.worker != transaction_started_evidence.worker {
                return Err(worker_identity_mismatch());
            }
            if ready_evidence.candidate.commit_binding_sha256.0
                != commit_binding_identity_sha256(&expected_binding)
                || ready_evidence.candidate.nonce_artifact_pair_sha256.0
                    != nonce_artifact_pair_identity_sha256(&ready_evidence.artifacts)
            {
                return Err(candidate_activation_binding_mismatch());
            }
        }
        if let Some(exit_ready) = self.exit_ready.as_ref() {
            let exit_evidence = match &exit_ready.evidence {
                FinalizerCommitEvidence::ExitReady(value) => value,
                _ => return Err(protocol_chain_invalid()),
            };
            if exit_evidence.worker != transaction_started_evidence.worker {
                return Err(worker_identity_mismatch());
            }
            if let Some(seal_ready) = self.seal_ready.as_ref() {
                let ready_evidence = match &seal_ready.evidence {
                    FinalizerCommitEvidence::SealReady(value) => value,
                    _ => return Err(protocol_chain_invalid()),
                };
                if exit_evidence.worker != ready_evidence.writer_handles.worker {
                    return Err(worker_identity_mismatch());
                }
                if exit_evidence.worker_writer_handles_closed_readback_sha256
                    != ready_evidence
                        .writer_handles
                        .worker_writer_handles_closed_readback_sha256
                {
                    return Err(writer_handles_not_closed());
                }
            }
        }
        if let (Some(seal_ready), Some(seal_complete)) =
            (self.seal_ready.as_ref(), self.seal_complete.as_ref())
        {
            let ready_evidence = match &seal_ready.evidence {
                FinalizerCommitEvidence::SealReady(value) => value,
                _ => return Err(protocol_chain_invalid()),
            };
            let complete_evidence = match &seal_complete.evidence {
                FinalizerCommitEvidence::SealComplete(value) => value,
                _ => return Err(protocol_chain_invalid()),
            };
            if ready_evidence.artifacts != complete_evidence.artifacts {
                return Err(artifact_identity_drift());
            }
            if ready_evidence
                .candidate
                .runtime
                .exact_service_configuration_sha256
                != expected_binding.plan.exact_service_configuration_sha256
                || ready_evidence.candidate.runtime.process.image_sha256()
                    != &expected_binding.plan.expected_service_image_sha256.0
                || complete_evidence.candidate.exact_service_identity_sha256
                    != ready_evidence
                        .candidate
                        .runtime
                        .exact_runtime_instance_sha256
                || complete_evidence.candidate.candidate_process_id
                    != ready_evidence.candidate.runtime.process.process_id()
                || complete_evidence.candidate.candidate_process_creation_time
                    != ready_evidence
                        .candidate
                        .runtime
                        .process
                        .process_creation_time()
                || complete_evidence.candidate.candidate_image_sha256
                    != Digest32(*ready_evidence.candidate.runtime.process.image_sha256())
                || complete_evidence
                    .candidate
                    .candidate_process_identity_sha256
                    .0
                    != ready_evidence.candidate.process_identity_sha256()
            {
                return Err(committed_runtime_identity_mismatch());
            }
            complete_evidence
                .generation_seal
                .validate_against(expected_binding)?;
            if complete_evidence
                .candidate
                .seal_ready_writer_handles_closed_readback_sha256
                != ready_evidence
                    .writer_handles
                    .candidate_writer_handles_closed_readback_sha256
            {
                return Err(writer_handles_not_closed());
            }
        }
        if let Some(final_commit) = &self.final_commit {
            let evidence = match &final_commit.evidence {
                FinalizerCommitEvidence::FinalCommit(value) => value,
                _ => return Err(protocol_chain_invalid()),
            };
            let seal_complete_evidence = match &self
                .seal_complete
                .as_ref()
                .ok_or_else(protocol_chain_invalid)?
                .evidence
            {
                FinalizerCommitEvidence::SealComplete(value) => value,
                _ => return Err(protocol_chain_invalid()),
            };
            if evidence.active_head.committed_generation_sha256
                != expected_binding.generation_sha256
                || evidence.committed_runtime.generation_sha256
                    != expected_binding.generation_sha256
                || evidence
                    .committed_runtime
                    .active_head_cas_projection_sha256
                    .0
                    != active_head_cas_projection_sha256(&evidence.active_head)
            {
                return Err(committed_generation_mismatch());
            }
            if evidence.active_head.prior.expected_prior_sha256()
                != expected_binding.plan.expected_active_head_prior_sha256.0
                || evidence.active_head.replacement_head_sha256
                    != expected_binding
                        .plan
                        .expected_active_head_replacement_sha256
                || evidence.active_head.activation_manifest_sha256
                    != expected_binding.plan.expected_activation_manifest_sha256
                || evidence.active_head.activation_epoch
                    != expected_binding.plan.expected_activation_epoch
                || evidence
                    .committed_runtime
                    .runtime
                    .exact_service_configuration_sha256
                    != expected_binding.plan.exact_service_configuration_sha256
                || evidence.committed_runtime.runtime.process.image_sha256()
                    != &expected_binding.plan.expected_service_image_sha256.0
                || evidence
                    .committed_runtime
                    .final_commit_activation_gate_sha256
                    .0
                    != expected_binding.expected_final_commit_gate_sha256()
                || evidence.runner_policy_sealed_identity
                    != seal_complete_evidence
                        .generation_seal
                        .runner_policy_sealed_identity
                || evidence.protected_blob_namespace
                    != seal_complete_evidence
                        .generation_seal
                        .protected_blob_namespace
                || evidence.zero_residue.plan != expected_binding.plan.residue_plan
            {
                return Err(committed_plan_binding_mismatch());
            }
            if evidence
                .committed_runtime
                .runtime
                .exact_runtime_instance_sha256
                == seal_complete_evidence
                    .candidate
                    .exact_service_identity_sha256
                || evidence.committed_runtime.process_identity_sha256()
                    == seal_complete_evidence
                        .candidate
                        .candidate_process_identity_sha256
                        .0
            {
                return Err(committed_runtime_identity_mismatch());
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum ProtocolWriteDisposition {
    Created,
    AlreadyIdentical,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct DurableReceiptWrite {
    stage: FinalizerCommitStage,
    disposition: ProtocolWriteDisposition,
    receipt_sha256: RawDigest,
    canonical_json: Vec<u8>,
}

impl DurableReceiptWrite {
    fn from_receipt(
        receipt: &FinalizerCommitReceipt,
        disposition: ProtocolWriteDisposition,
    ) -> Result<Self, AuthorityMaintenanceError> {
        Ok(Self {
            stage: receipt.stage,
            disposition,
            receipt_sha256: receipt.digest()?,
            canonical_json: receipt.canonical_json()?,
        })
    }

    pub(super) fn stage(&self) -> FinalizerCommitStage {
        self.stage
    }

    pub(super) fn disposition(&self) -> ProtocolWriteDisposition {
        self.disposition
    }

    pub(super) fn receipt_sha256(&self) -> RawDigest {
        self.receipt_sha256
    }

    pub(super) fn canonical_json(&self) -> &[u8] {
        &self.canonical_json
    }
}

/// SYSTEM can persist only the three pre-finalizer stages. There are no seal
/// completion or final-commit methods on this capability.
pub(super) struct SystemCommitProtocolActor<'a> {
    state: &'a mut FinalizerCommitProtocolState,
}

impl SystemCommitProtocolActor<'_> {
    pub(super) fn record_apply_ready(
        &mut self,
        evidence: ApplyReadyEvidence,
    ) -> Result<DurableReceiptWrite, AuthorityMaintenanceError> {
        self.state.record(
            FinalizerCommitStage::ApplyReady,
            FinalizerCommitEvidence::ApplyReady(evidence),
        )
    }

    pub(super) fn record_seal_ready(
        &mut self,
        evidence: SealReadyEvidence,
    ) -> Result<DurableReceiptWrite, AuthorityMaintenanceError> {
        self.state.record(
            FinalizerCommitStage::SealReady,
            FinalizerCommitEvidence::SealReady(evidence),
        )
    }

    pub(super) fn record_exit_ready(
        &mut self,
        evidence: ExitReadyEvidence,
    ) -> Result<DurableReceiptWrite, AuthorityMaintenanceError> {
        self.state.record(
            FinalizerCommitStage::ExitReady,
            FinalizerCommitEvidence::ExitReady(evidence),
        )
    }
}

/// Elevated finalization begins only after durable `ExitReady`. This
/// capability cannot generate any SYSTEM-owned receipt.
pub(super) struct ElevatedFinalizerCommitProtocolActor<'a> {
    state: &'a mut FinalizerCommitProtocolState,
}

impl ElevatedFinalizerCommitProtocolActor<'_> {
    pub(super) fn record_seal_complete(
        &mut self,
        evidence: SealCompleteEvidence,
    ) -> Result<DurableReceiptWrite, AuthorityMaintenanceError> {
        self.state.record(
            FinalizerCommitStage::SealComplete,
            FinalizerCommitEvidence::SealComplete(evidence),
        )
    }

    #[cfg(windows)]
    pub(super) fn record_seal_complete_authorized(
        &mut self,
        authorization: &GenerationSealTerminalAuthorization,
        artifacts: NonceArtifactPair,
        sealed_security: ExactSealedSecurityReadback,
        candidate: CandidateStoppedReadback,
    ) -> Result<DurableReceiptWrite, AuthorityMaintenanceError> {
        let evidence = SealCompleteEvidence::from_generation_seal_authorization(
            authorization,
            artifacts,
            sealed_security,
            candidate,
        )?;
        self.state.record(
            FinalizerCommitStage::SealComplete,
            FinalizerCommitEvidence::SealComplete(evidence),
        )
    }

    pub(super) fn record_final_commit(
        &mut self,
        evidence: FinalCommitEvidence,
    ) -> Result<DurableReceiptWrite, AuthorityMaintenanceError> {
        self.state.record(
            FinalizerCommitStage::FinalCommit,
            FinalizerCommitEvidence::FinalCommit(evidence),
        )
    }
}

fn protocol_genesis_sha256(binding: &FinalizerCommitBinding) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(PROTOCOL_GENESIS_DOMAIN);
    digest.update(binding.capsule_sha256.0);
    digest.update(binding.plan_sha256.0);
    digest.update(binding.generation_sha256.0);
    digest.update(binding.transaction_sha256.0);
    digest.update(binding.final_commit_store_root_identity_sha256.0);
    digest.finalize().into()
}

fn service_process_identity_sha256(
    exact_service_identity_sha256: RawDigest,
    process_id: u32,
    process_creation_time: u64,
    image_sha256: RawDigest,
) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(SERVICE_PROCESS_IDENTITY_DOMAIN);
    digest.update(exact_service_identity_sha256);
    digest.update(process_id.to_be_bytes());
    digest.update(process_creation_time.to_be_bytes());
    digest.update(image_sha256);
    digest.finalize().into()
}

fn commit_binding_identity_sha256(binding: &FinalizerCommitBinding) -> RawDigest {
    let operation = operation_tag(binding.plan.operation);
    let mut digest = Sha256::new();
    digest.update(COMMIT_BINDING_IDENTITY_DOMAIN);
    digest.update(binding.capsule_sha256.0);
    digest.update(binding.plan_sha256.0);
    digest.update(binding.generation_sha256.0);
    digest.update(binding.transaction_sha256.0);
    digest.update([operation]);
    digest.update(binding.plan.expected_worker_service_identity_sha256.0);
    digest.update(binding.plan.expected_worker_image_sha256.0);
    digest.update(binding.plan.exact_service_configuration_sha256.0);
    digest.update(binding.plan.expected_service_image_sha256.0);
    digest.update(binding.plan.expected_active_head_prior_sha256.0);
    digest.update(binding.plan.expected_active_head_replacement_sha256.0);
    digest.update(binding.plan.expected_activation_manifest_sha256.0);
    digest.update(binding.plan.expected_activation_epoch.to_be_bytes());
    digest.update(binding.plan.generation_object_manifest_sha256.0);
    digest.update(
        binding
            .plan
            .expected_runner_policy_state_byte_length
            .to_be_bytes(),
    );
    digest.update(binding.plan.expected_runner_policy_state_bytes_sha256.0);
    digest.update(binding.plan.expected_runner_policy_state_binding_sha256.0);
    digest.update(operation_residue_plan_digest_unchecked(
        &binding.plan.residue_plan,
    ));
    digest.finalize().into()
}

fn final_commit_gate_projection_sha256(binding: &FinalizerCommitBinding) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(FINAL_COMMIT_GATE_PROJECTION_DOMAIN);
    digest.update(binding.capsule_sha256.0);
    digest.update(binding.plan_sha256.0);
    digest.update(binding.generation_sha256.0);
    digest.update(binding.transaction_sha256.0);
    digest.update(binding.final_commit_store_root_identity_sha256.0);
    digest.update([operation_tag(binding.plan.operation)]);
    digest.update(binding.plan.expected_worker_service_identity_sha256.0);
    digest.update(binding.plan.expected_worker_image_sha256.0);
    digest.update(binding.plan.exact_service_configuration_sha256.0);
    digest.update(binding.plan.expected_service_image_sha256.0);
    digest.update(binding.plan.expected_active_head_prior_sha256.0);
    digest.update(binding.plan.expected_active_head_replacement_sha256.0);
    digest.update(binding.plan.expected_activation_manifest_sha256.0);
    digest.update(binding.plan.expected_activation_epoch.to_be_bytes());
    digest.update(binding.plan.generation_object_manifest_sha256.0);
    digest.update(
        binding
            .plan
            .expected_runner_policy_state_byte_length
            .to_be_bytes(),
    );
    digest.update(binding.plan.expected_runner_policy_state_bytes_sha256.0);
    digest.update(binding.plan.expected_runner_policy_state_binding_sha256.0);
    digest.update(operation_residue_plan_digest_unchecked(
        &binding.plan.residue_plan,
    ));
    digest.finalize().into()
}

fn expected_final_commit_gate_sha256(binding: &FinalizerCommitBinding) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(FINAL_COMMIT_GATE_DOMAIN);
    digest.update(binding.final_commit_store_root_identity_sha256.0);
    update_length_prefixed(&mut digest, FINAL_COMMIT_RECEIPT_LEAF.as_bytes());
    update_length_prefixed(&mut digest, FINALIZER_COMMIT_STORE_SCHEMA.as_bytes());
    update_length_prefixed(&mut digest, FINALIZER_COMMIT_RECEIPT_SCHEMA.as_bytes());
    update_length_prefixed(&mut digest, FINALIZER_COMMIT_PROTOCOL_SCHEMA.as_bytes());
    update_length_prefixed(&mut digest, FINAL_COMMIT_VALID_PREDICATE);
    digest.update(final_commit_gate_projection_sha256(binding));
    digest.finalize().into()
}

fn operation_tag(operation: AuthorityMaintenanceOperation) -> u8 {
    match operation {
        AuthorityMaintenanceOperation::Install => 1,
        AuthorityMaintenanceOperation::Update => 2,
        AuthorityMaintenanceOperation::Retire => 3,
    }
}

fn residue_dimension_tag(dimension: ResidueDimension) -> u8 {
    match dimension {
        ResidueDimension::MaintenanceService => 1,
        ResidueDimension::TransientStaging => 2,
        ResidueDimension::CandidateActivationCredential => 3,
        ResidueDimension::MaintenancePipe => 4,
        ResidueDimension::WorkerProcessAndState => 5,
        ResidueDimension::WorkerNonce => 6,
        ResidueDimension::CandidateConsumption => 7,
        ResidueDimension::FinalizerReceiptPublishing => 8,
        ResidueDimension::ActiveHead => 9,
        ResidueDimension::RetirementStaging => 10,
        ResidueDimension::RetirementAborted => 11,
        ResidueDimension::RetirementFinal => 12,
        ResidueDimension::FinalizerCommitStore => 13,
    }
}

fn operation_residue_plan_digest_unchecked(plan: &OperationResiduePlan) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(OPERATION_RESIDUE_PLAN_DOMAIN);
    digest.update([operation_tag(plan.operation)]);
    for object in plan.objects {
        digest.update([residue_dimension_tag(object.dimension)]);
        digest.update(object.object_binding_sha256.0);
        digest.update([match object.expectation {
            ResidueExpectation::Absent => 0,
            ResidueExpectation::PresentExact => 1,
        }]);
        match object.expected_identity_sha256 {
            Some(value) => {
                digest.update([1]);
                digest.update(value.0);
            }
            None => digest.update([0]),
        }
    }
    digest.finalize().into()
}

fn operation_zero_residue_readback_digest_unchecked(
    readback: &OperationZeroResidueReadback,
) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(OPERATION_ZERO_RESIDUE_READBACK_DOMAIN);
    digest.update(operation_residue_plan_digest_unchecked(&readback.plan));
    for object in readback.objects {
        digest.update([residue_dimension_tag(object.plan.dimension)]);
        digest.update(object.plan.object_binding_sha256.0);
        match object.observed_identity_sha256 {
            Some(value) => {
                digest.update([1]);
                digest.update(value.0);
            }
            None => digest.update([0]),
        }
        digest.update(object.kernel_readback_sha256.0);
    }
    digest.finalize().into()
}

#[allow(clippy::too_many_arguments)]
fn postcommit_serving_readback_sha256(
    final_commit: &FinalCommitPersistenceProjection,
    observed_runtime: ExactServiceRuntimeIdentity,
    observed_final_commit_receipt_sha256: RawDigest,
    controller_pipe_instance_id: [u8; 16],
    controller_pipe_handshake_readback_sha256: RawDigest,
    serving_state_readback_sha256: RawDigest,
) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(POSTCOMMIT_SERVING_READBACK_DOMAIN);
    digest.update(final_commit.final_commit_receipt_sha256);
    digest.update(final_commit.protocol_state_sha256);
    digest.update(final_commit.expected_final_commit_gate_sha256);
    digest.update(active_head_cas_projection_sha256(&final_commit.active_head));
    digest.update(
        final_commit
            .committed_runtime
            .precommit_dormant_readback_sha256
            .0,
    );
    digest.update(operation_zero_residue_readback_digest_unchecked(
        &final_commit.zero_residue,
    ));
    update_exact_service_runtime_digest(&mut digest, observed_runtime);
    digest.update(observed_final_commit_receipt_sha256);
    digest.update(controller_pipe_instance_id);
    digest.update(controller_pipe_handshake_readback_sha256);
    digest.update(serving_state_readback_sha256);
    digest.finalize().into()
}

#[allow(clippy::too_many_arguments)]
fn recovered_postcommit_serving_readback_sha256(
    final_commit: &FinalCommitPersistenceProjection,
    previous_runtime_absence: PreviousRuntimeAbsenceReadback,
    observed_active_head_sha256: RawDigest,
    observed_runtime: ExactServiceRuntimeIdentity,
    observed_generation_sha256: RawDigest,
    observed_final_commit_receipt_sha256: RawDigest,
    observed_final_commit_gate_sha256: RawDigest,
    controller_pipe_instance_id: [u8; 16],
    generation_handshake_readback_sha256: RawDigest,
    controller_pipe_handshake_readback_sha256: RawDigest,
    serving_state_readback_sha256: RawDigest,
) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(RECOVERED_POSTCOMMIT_SERVING_READBACK_DOMAIN);
    digest.update(final_commit.final_commit_receipt_sha256);
    digest.update(final_commit.protocol_state_sha256);
    digest.update(final_commit.expected_final_commit_gate_sha256);
    digest.update(active_head_cas_projection_sha256(&final_commit.active_head));
    digest.update(operation_zero_residue_readback_digest_unchecked(
        &final_commit.zero_residue,
    ));
    digest.update(previous_runtime_absence.readback_sha256());
    digest.update(observed_active_head_sha256);
    update_exact_service_runtime_digest(&mut digest, observed_runtime);
    digest.update(observed_generation_sha256);
    digest.update(observed_final_commit_receipt_sha256);
    digest.update(observed_final_commit_gate_sha256);
    digest.update(controller_pipe_instance_id);
    digest.update(generation_handshake_readback_sha256);
    digest.update(controller_pipe_handshake_readback_sha256);
    digest.update(serving_state_readback_sha256);
    digest.finalize().into()
}

#[allow(clippy::too_many_arguments)]
fn previous_runtime_absence_readback_sha256(
    previous_runtime_instance_sha256: RawDigest,
    previous_process_id: u32,
    previous_process_creation_time: u64,
    observed_service_state: u32,
    observed_service_process_id: u32,
    observed_win32_exit_code: u32,
    observed_service_specific_exit_code: u32,
    prior_process_exit_readback_sha256: RawDigest,
    process_reuse_absence_readback_sha256: RawDigest,
    stopped_service_readback_sha256: RawDigest,
) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(PREVIOUS_RUNTIME_ABSENCE_READBACK_DOMAIN);
    digest.update(previous_runtime_instance_sha256);
    digest.update(previous_process_id.to_be_bytes());
    digest.update(previous_process_creation_time.to_be_bytes());
    digest.update(observed_service_state.to_be_bytes());
    digest.update(observed_service_process_id.to_be_bytes());
    digest.update(observed_win32_exit_code.to_be_bytes());
    digest.update(observed_service_specific_exit_code.to_be_bytes());
    digest.update(prior_process_exit_readback_sha256);
    digest.update(process_reuse_absence_readback_sha256);
    digest.update(stopped_service_readback_sha256);
    digest.finalize().into()
}

fn update_exact_service_runtime_digest(digest: &mut Sha256, runtime: ExactServiceRuntimeIdentity) {
    digest.update(runtime.exact_service_configuration_sha256.0);
    digest.update(runtime.exact_runtime_instance_sha256.0);
    digest.update(runtime.runtime_token_receipt_sha256.0);
    digest.update(runtime.process.full_readback_receipt_sha256());
}

fn worker_activation_proof_sha256(
    commit_binding_sha256: RawDigest,
    worker: ExactServiceProcessIdentity,
    durable_start_readback_sha256: RawDigest,
) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(WORKER_ACTIVATION_PROOF_DOMAIN);
    digest.update(commit_binding_sha256);
    digest.update(worker.exact_service_identity_sha256.0);
    digest.update(worker.process_id.to_be_bytes());
    digest.update(worker.process_creation_time.to_be_bytes());
    digest.update(worker.image_sha256.0);
    digest.update(durable_start_readback_sha256);
    digest.finalize().into()
}

fn nonce_artifact_pair_identity_sha256(artifacts: &NonceArtifactPair) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(NONCE_ARTIFACT_PAIR_IDENTITY_DOMAIN);
    for artifact in [artifacts.worker_nonce, artifacts.candidate_consumption] {
        digest.update(artifact.volume_serial.to_be_bytes());
        digest.update(artifact.file_id.0);
        digest.update(artifact.link_count.to_be_bytes());
        digest.update(artifact.byte_length.to_be_bytes());
        digest.update(artifact.bytes_sha256.0);
    }
    digest.finalize().into()
}

fn active_head_cas_projection_sha256(active_head: &ActiveHeadCasReadback) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(ACTIVE_HEAD_CAS_PROJECTION_DOMAIN);
    match active_head.prior {
        ActiveHeadPriorReadback::Absent {
            expected_prior_sha256,
            absence_readback_sha256,
        } => {
            digest.update([0]);
            digest.update(expected_prior_sha256.0);
            digest.update(absence_readback_sha256.0);
        }
        ActiveHeadPriorReadback::Present {
            expected_head_sha256,
            observed_head_sha256,
        } => {
            digest.update([1]);
            digest.update(expected_head_sha256.0);
            digest.update(observed_head_sha256.0);
        }
    }
    digest.update(active_head.replacement_head_sha256.0);
    digest.update(active_head.observed_head_sha256.0);
    digest.update(active_head.committed_generation_sha256.0);
    digest.update(active_head.activation_manifest_sha256.0);
    digest.update(active_head.activation_epoch.to_be_bytes());
    digest.update([match active_head.disposition {
        ActiveHeadCasDisposition::Applied => 1,
        ActiveHeadCasDisposition::AlreadyIdentical => 2,
    }]);
    digest.update(active_head.readback_sha256.0);
    digest.finalize().into()
}

#[allow(clippy::too_many_arguments)]
fn candidate_activation_proof_sha256(
    commit_binding_sha256: RawDigest,
    nonce_artifact_pair_sha256: RawDigest,
    exact_service_identity_sha256: RawDigest,
    process_id: u32,
    process_creation_time: u64,
    image_sha256: RawDigest,
    activation_readback_sha256: RawDigest,
) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_ACTIVATION_PROOF_DOMAIN);
    digest.update(commit_binding_sha256);
    digest.update(nonce_artifact_pair_sha256);
    digest.update(exact_service_identity_sha256);
    digest.update(process_id.to_be_bytes());
    digest.update(process_creation_time.to_be_bytes());
    digest.update(image_sha256);
    digest.update(activation_readback_sha256);
    digest.finalize().into()
}

#[allow(clippy::too_many_arguments)]
fn precommit_dormant_runtime_sha256(
    generation_sha256: RawDigest,
    active_head_cas_projection_sha256: RawDigest,
    runtime: &ExactServiceRuntimeIdentity,
    precommit_handshake_pipe_instance_id: [u8; 16],
    precommit_handshake_readback_sha256: RawDigest,
    controller_pipe_absence_readback_sha256: RawDigest,
    generation_writer_roster_readback_sha256: RawDigest,
    final_commit_activation_gate_sha256: RawDigest,
) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(PRECOMMIT_DORMANT_RUNTIME_DOMAIN);
    digest.update(generation_sha256);
    digest.update(active_head_cas_projection_sha256);
    digest.update(runtime.exact_service_configuration_sha256.0);
    digest.update(runtime.exact_runtime_instance_sha256.0);
    digest.update(runtime.runtime_token_receipt_sha256.0);
    digest.update(runtime.process.full_readback_receipt_sha256());
    digest.update(precommit_handshake_pipe_instance_id);
    digest.update(precommit_handshake_readback_sha256);
    digest.update(controller_pipe_absence_readback_sha256);
    digest.update(generation_writer_roster_readback_sha256);
    digest.update(final_commit_activation_gate_sha256);
    digest.finalize().into()
}

fn update_length_prefixed(digest: &mut Sha256, bytes: &[u8]) {
    digest.update((bytes.len() as u64).to_be_bytes());
    digest.update(bytes);
}

fn domain_separated_digest(domain: &[u8], bytes: &[u8]) -> RawDigest {
    let mut digest = Sha256::new();
    digest.update(domain);
    digest.update((bytes.len() as u64).to_be_bytes());
    digest.update(bytes);
    digest.finalize().into()
}

fn is_zero<const N: usize>(value: &[u8; N]) -> bool {
    value.iter().all(|byte| *byte == 0)
}

fn hex_lower<const N: usize>(value: &[u8; N]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(N * 2);
    for byte in value {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn decode_lower_hex_exact<const N: usize>(value: &str) -> Result<[u8; N], &'static str> {
    let bytes = value.as_bytes();
    if bytes.len() != N * 2 {
        return Err("authority_finalizer_commit_hex_length_invalid");
    }
    let mut output = [0u8; N];
    for (index, pair) in bytes.chunks_exact(2).enumerate() {
        let high = lower_hex_nibble(pair[0])?;
        let low = lower_hex_nibble(pair[1])?;
        output[index] = (high << 4) | low;
    }
    Ok(output)
}

fn lower_hex_nibble(value: u8) -> Result<u8, &'static str> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err("authority_finalizer_commit_hex_not_canonical"),
    }
}

fn identity_invalid() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_identity_invalid")
}

fn file_identity_invalid() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_file_identity_invalid")
}

fn file_identity_collision() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_file_identity_collision")
}

fn evidence_invalid() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_evidence_invalid")
}

fn writer_handles_not_closed() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_writer_handles_not_closed")
}

fn worker_identity_mismatch() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_worker_identity_mismatch")
}

fn candidate_activation_binding_mismatch() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_candidate_activation_binding_mismatch")
}

fn operation_residue_plan_invalid() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_residue_plan_invalid")
}

fn operation_zero_residue_invalid() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_zero_residue_invalid")
}

fn postcommit_serving_readback_invalid() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_postcommit_serving_readback_invalid")
}

fn seal_complete_authorization_invalid() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_seal_authorization_invalid")
}

fn sealed_security_not_exact() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_sealed_security_not_exact")
}

fn candidate_not_exact_stopped() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_candidate_not_exact_stopped")
}

fn active_head_cas_invalid() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_active_head_cas_invalid")
}

fn committed_runtime_invalid() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_runtime_identity_invalid")
}

fn canonical_invalid() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_canonical_json_invalid")
}

fn receipt_invalid() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_receipt_invalid")
}

fn receipt_conflict() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_receipt_conflict")
}

fn protocol_gap() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_protocol_gap")
}

fn protocol_state_invalid() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_protocol_state_invalid")
}

fn protocol_chain_invalid() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_protocol_chain_invalid")
}

fn artifact_identity_drift() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_artifact_identity_drift")
}

fn committed_generation_mismatch() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_generation_mismatch")
}

fn committed_plan_binding_mismatch() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_plan_binding_mismatch")
}

fn committed_runtime_identity_mismatch() -> AuthorityMaintenanceError {
    AuthorityMaintenanceError("authority_finalizer_commit_runtime_identity_mismatch")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::primitive_evidence_authority_install::bootstrap_activation::{
        CandidateActivationBinding, CandidateActivationObservation, CandidateProcessEvidence,
    };

    fn residue_plan_for(operation: AuthorityMaintenanceOperation) -> OperationResiduePlan {
        let objects = std::array::from_fn(|index| {
            let dimension = RESIDUE_DIMENSIONS[index];
            let binding_byte = 0x80_u8.saturating_add(index as u8);
            let present = matches!(
                dimension,
                ResidueDimension::WorkerNonce
                    | ResidueDimension::CandidateConsumption
                    | ResidueDimension::ActiveHead
                    | ResidueDimension::FinalizerCommitStore
            ) || (dimension == ResidueDimension::RetirementFinal
                && operation == AuthorityMaintenanceOperation::Update);
            if present {
                ResidueObjectPlan::present_exact(
                    dimension,
                    [binding_byte; 32],
                    [0xa0_u8.saturating_add(index as u8); 32],
                )
                .unwrap()
            } else {
                ResidueObjectPlan::absent(dimension, [binding_byte; 32]).unwrap()
            }
        });
        OperationResiduePlan::new(operation, objects).unwrap()
    }

    fn zero_residue_for(plan: OperationResiduePlan) -> OperationZeroResidueReadback {
        let objects = std::array::from_fn(|index| {
            let object = plan.objects[index];
            let kernel_readback = [0xc0_u8.saturating_add(index as u8); 32];
            match object.expected_identity_sha256 {
                Some(identity) => {
                    ResidueObjectReadback::present_exact(object, identity.0, kernel_readback)
                        .unwrap()
                }
                None => ResidueObjectReadback::absent(object, kernel_readback).unwrap(),
            }
        });
        OperationZeroResidueReadback::new(plan, objects).unwrap()
    }

    fn plan_binding() -> FinalizerCommitPlanBinding {
        FinalizerCommitPlanBinding::new(
            AuthorityMaintenanceOperation::Install,
            [0x70; 32],
            [0x71; 32],
            [0x54; 32],
            [0x5a; 32],
            [0x61; 32],
            [0x62; 32],
            [0x64; 32],
            4,
            [0x58; 32],
            residue_plan_for(AuthorityMaintenanceOperation::Install),
        )
        .unwrap()
        .with_runner_policy_state_descriptor(RunnerPolicyStateDescriptor::exact_test_fixture(
            [0x13; 32], [0x14; 32], 512, [0x59; 32], [0x5a; 32],
        ))
        .unwrap()
    }

    fn binding() -> FinalizerCommitBinding {
        FinalizerCommitBinding::new(
            [0x11; 32],
            [0x12; 32],
            [0x13; 32],
            [0x14; 32],
            plan_binding(),
            [0x15; 32],
        )
        .unwrap()
    }

    fn worker() -> ExactServiceProcessIdentity {
        ExactServiceProcessIdentity::new([0x70; 32], 2222, 555, [0x71; 32]).unwrap()
    }

    fn artifacts() -> NonceArtifactPair {
        NonceArtifactPair::new(
            DurableFileIdentity::new(0x101, [0x21; 16], 1, 121, [0x31; 32]).unwrap(),
            DurableFileIdentity::new(0x102, [0x22; 16], 1, 122, [0x32; 32]).unwrap(),
        )
        .unwrap()
    }

    fn candidate_process() -> CandidateProcessEvidence {
        CandidateProcessEvidence::from_held_process(
            3333, 666, [0x5a; 32], 0x2000, 0x3030, [0x31; 16], 1, 0x20,
        )
        .unwrap()
    }

    fn committed_process() -> CandidateProcessEvidence {
        CandidateProcessEvidence::from_held_process(
            4242, 777, [0x5a; 32], 0x2000, 0x3030, [0x31; 16], 1, 0x20,
        )
        .unwrap()
    }

    fn candidate_runtime() -> ExactServiceRuntimeIdentity {
        ExactServiceRuntimeIdentity::from_observed([0x54; 32], [0x55; 32], candidate_process())
            .unwrap()
    }

    fn committed_runtime() -> ExactServiceRuntimeIdentity {
        ExactServiceRuntimeIdentity::from_observed([0x54; 32], [0x56; 32], committed_process())
            .unwrap()
    }

    fn seal_ready() -> SealReadyEvidence {
        SealReadyEvidence::new(
            artifacts(),
            WriterHandlesClosedReadback::new(worker(), true, [0x41; 32], true, [0x42; 32]).unwrap(),
            CandidateActivationIdentity::new(
                binding(),
                artifacts(),
                [0x54; 32],
                [0x55; 32],
                candidate_process(),
                [0x5b; 32],
            )
            .unwrap(),
        )
        .unwrap()
    }

    fn seal_complete() -> SealCompleteEvidence {
        SealCompleteEvidence::exact_test_fixture(
            binding(),
            artifacts(),
            ExactSealedSecurityReadback::new(
                [0x51; 32], [0x51; 32], [0x51; 32], true, true, true, [0x53; 32],
            )
            .unwrap(),
            CandidateStoppedReadback::exact_stopped(
                candidate_runtime().exact_runtime_instance_sha256(),
                3333,
                666,
                [0x5a; 32],
                [0x42; 32],
                [0x56; 32],
                [0x57; 32],
            )
            .unwrap(),
        )
        .unwrap()
    }

    fn final_commit() -> FinalCommitEvidence {
        let active_head = ActiveHeadCasReadback::new(
            ActiveHeadPriorReadback::present([0x61; 32], [0x61; 32]).unwrap(),
            [0x62; 32],
            [0x62; 32],
            binding().generation_sha256(),
            [0x64; 32],
            4,
            ActiveHeadCasDisposition::Applied,
            [0x63; 32],
        )
        .unwrap();
        FinalCommitEvidence::new_with_runner_policy_sealed_identity(
            active_head,
            CommittedRuntimeIdentity::new(
                active_head,
                committed_runtime(),
                [0x66; 16],
                [0x67; 32],
                [0x75; 32],
                [0x76; 32],
                binding().expected_final_commit_gate_sha256(),
            )
            .unwrap(),
            RunnerPolicySealedIdentity::exact_test_fixture(0x6b),
            zero_residue_for(residue_plan_for(AuthorityMaintenanceOperation::Install)),
        )
        .unwrap()
    }

    fn started() -> FinalizerCommitProtocolState {
        FinalizerCommitProtocolState::transaction_started(
            binding(),
            TransactionStartedEvidence::new(binding(), worker(), [0x72; 32]).unwrap(),
        )
        .unwrap()
    }

    fn through_exit_ready() -> FinalizerCommitProtocolState {
        let mut state = started();
        let mut system = state.system_actor();
        system
            .record_apply_ready(ApplyReadyEvidence::new([0x73; 32]).unwrap())
            .unwrap();
        system.record_seal_ready(seal_ready()).unwrap();
        system
            .record_exit_ready(ExitReadyEvidence::new(worker(), [0x41; 32], [0x74; 32]).unwrap())
            .unwrap();
        state
    }

    fn completed() -> FinalizerCommitProtocolState {
        let mut state = through_exit_ready();
        let mut finalizer = state.elevated_finalizer();
        finalizer.record_seal_complete(seal_complete()).unwrap();
        finalizer.record_final_commit(final_commit()).unwrap();
        state
    }

    fn serialize_unchecked(state: &FinalizerCommitProtocolState) -> Vec<u8> {
        serde_json::to_vec(state).unwrap()
    }

    #[test]
    fn protocol_is_canonical_domain_separated_and_production_disabled() {
        let state = completed();
        let canonical = state.to_canonical_json().unwrap();
        assert_eq!(
            FinalizerCommitProtocolState::from_canonical_json(&canonical).unwrap(),
            state
        );
        assert!(!FINALIZER_COMMIT_PROTOCOL_PRODUCTION_ENABLED);
        assert_eq!(
            FINALIZER_COMMIT_PROTOCOL_SCHEMA,
            "vrcforge.authority.finalizer-commit-protocol.v4"
        );
        assert_eq!(
            FINALIZER_COMMIT_RECEIPT_SCHEMA,
            "vrcforge.authority.finalizer-commit-receipt.v4"
        );
        assert_eq!(
            FINALIZER_COMMIT_STORE_SCHEMA,
            "vrcforge.authority.finalizer-commit-store.v4"
        );
        assert_ne!(
            state.state_sha256().unwrap(),
            Sha256::digest(&canonical)[..]
        );
        let domains = [
            TRANSACTION_STARTED_RECEIPT_DOMAIN,
            APPLY_READY_RECEIPT_DOMAIN,
            SEAL_READY_RECEIPT_DOMAIN,
            EXIT_READY_RECEIPT_DOMAIN,
            SEAL_COMPLETE_RECEIPT_DOMAIN,
            FINAL_COMMIT_RECEIPT_DOMAIN,
        ];
        for (index, domain) in domains.iter().enumerate() {
            for other in domains.iter().skip(index + 1) {
                assert_ne!(domain, other);
            }
        }
        for stage in [
            FinalizerCommitStage::TransactionStarted,
            FinalizerCommitStage::ApplyReady,
            FinalizerCommitStage::SealReady,
            FinalizerCommitStage::ExitReady,
            FinalizerCommitStage::SealComplete,
            FinalizerCommitStage::FinalCommit,
        ] {
            assert!(state.receipt_sha256(stage).unwrap().is_some());
        }
    }

    #[test]
    fn runner_policy_descriptor_is_durable_and_complete_in_commit_gates() {
        let exact_plan = plan_binding();
        assert_eq!(exact_plan.expected_runner_policy_state_byte_length(), 512);
        assert_eq!(
            exact_plan.expected_runner_policy_state_bytes_sha256(),
            [0x59; 32]
        );
        assert_eq!(
            exact_plan.expected_runner_policy_state_binding_sha256(),
            [0x5a; 32]
        );
        let exact_binding = binding();
        let exact_identity = commit_binding_identity_sha256(&exact_binding);
        let exact_projection = exact_binding.final_commit_gate_projection_sha256();
        let exact_gate = exact_binding.expected_final_commit_gate_sha256();

        let descriptors = [
            RunnerPolicyStateDescriptor::exact_test_fixture(
                exact_binding.generation_sha256(),
                exact_binding.transaction_sha256(),
                513,
                [0x59; 32],
                [0x5a; 32],
            ),
            RunnerPolicyStateDescriptor::exact_test_fixture(
                exact_binding.generation_sha256(),
                exact_binding.transaction_sha256(),
                512,
                [0x5b; 32],
                [0x5a; 32],
            ),
            RunnerPolicyStateDescriptor::exact_test_fixture(
                exact_binding.generation_sha256(),
                exact_binding.transaction_sha256(),
                512,
                [0x59; 32],
                [0x5c; 32],
            ),
        ];
        for descriptor in descriptors {
            let drifted_plan = exact_plan
                .with_runner_policy_state_descriptor(descriptor)
                .unwrap();
            let drifted = FinalizerCommitBinding::new(
                exact_binding.capsule_sha256(),
                exact_binding.plan_sha256(),
                exact_binding.generation_sha256(),
                exact_binding.transaction_sha256(),
                drifted_plan,
                exact_binding.final_commit_store_root_identity_sha256(),
            )
            .unwrap();
            assert_ne!(commit_binding_identity_sha256(&drifted), exact_identity);
            assert_ne!(
                drifted.final_commit_gate_projection_sha256(),
                exact_projection
            );
            assert_ne!(drifted.expected_final_commit_gate_sha256(), exact_gate);
        }

        let serialized = serde_json::to_value(exact_plan).unwrap();
        let object = serialized.as_object().unwrap();
        for field in [
            "expectedRunnerPolicyStateByteLength",
            "expectedRunnerPolicyStateBytesSha256",
            "expectedRunnerPolicyStateBindingSha256",
        ] {
            assert!(object.contains_key(field));
            let mut missing = serialized.clone();
            missing.as_object_mut().unwrap().remove(field);
            assert!(serde_json::from_value::<FinalizerCommitPlanBinding>(missing).is_err());
        }
        let mut unknown = serialized;
        unknown
            .as_object_mut()
            .unwrap()
            .insert("runnerPolicyAlias".to_string(), serde_json::json!(true));
        assert!(serde_json::from_value::<FinalizerCommitPlanBinding>(unknown).is_err());
    }

    #[test]
    fn sealed_runner_identity_is_durable_in_both_terminal_receipts() {
        let state = completed();
        let value = serde_json::to_value(&state).unwrap();
        let seal_identity = &value["sealComplete"]["evidence"]["details"]["generationSeal"]
            ["runnerPolicySealedIdentity"];
        let final_identity =
            &value["finalCommit"]["evidence"]["details"]["runnerPolicySealedIdentity"];
        assert_eq!(seal_identity, final_identity);
        for field in ["volumeSerial", "fileId", "linkCount", "attributes"] {
            assert!(seal_identity.get(field).is_some(), "missing {field}");
        }

        for terminal in ["sealComplete", "finalCommit"] {
            let mut missing = value.clone();
            let identity = if terminal == "sealComplete" {
                &mut missing[terminal]["evidence"]["details"]["generationSeal"]
                    ["runnerPolicySealedIdentity"]
            } else {
                &mut missing[terminal]["evidence"]["details"]["runnerPolicySealedIdentity"]
            };
            identity.as_object_mut().unwrap().remove("volumeSerial");
            assert!(serde_json::from_value::<FinalizerCommitProtocolState>(missing).is_err());
        }

        let mut drifted = value.clone();
        drifted["finalCommit"]["evidence"]["details"]["runnerPolicySealedIdentity"]
            ["volumeSerial"] = serde_json::json!(999u64);
        let drifted: FinalizerCommitProtocolState = serde_json::from_value(drifted).unwrap();
        assert_eq!(
            drifted.validate().unwrap_err().code(),
            "authority_finalizer_commit_plan_binding_mismatch"
        );

        let mut legacy = value;
        legacy["schema"] = serde_json::json!("vrcforge.authority.finalizer-commit-protocol.v2");
        let legacy: FinalizerCommitProtocolState = serde_json::from_value(legacy).unwrap();
        assert!(legacy.validate().is_err());

        let mut legacy_receipt = serde_json::to_value(completed()).unwrap();
        legacy_receipt["finalCommit"]["schema"] =
            serde_json::json!("vrcforge.authority.finalizer-commit-receipt.v2");
        let legacy_receipt: FinalizerCommitProtocolState =
            serde_json::from_value(legacy_receipt).unwrap();
        assert!(legacy_receipt.validate().is_err());
    }

    #[test]
    fn actor_surfaces_enforce_exact_order_and_ownership() {
        let mut state = started();
        assert!(state
            .system_actor()
            .record_seal_ready(seal_ready())
            .is_err());
        assert!(state
            .elevated_finalizer()
            .record_seal_complete(seal_complete())
            .is_err());
        {
            let mut system = state.system_actor();
            assert_eq!(
                system
                    .record_apply_ready(ApplyReadyEvidence::new([0x72; 32]).unwrap())
                    .unwrap()
                    .stage(),
                FinalizerCommitStage::ApplyReady
            );
            system.record_seal_ready(seal_ready()).unwrap();
            system
                .record_exit_ready(
                    ExitReadyEvidence::new(worker(), [0x41; 32], [0x74; 32]).unwrap(),
                )
                .unwrap();
        }
        assert_eq!(state.latest_stage(), FinalizerCommitStage::ExitReady);
        {
            let mut finalizer = state.elevated_finalizer();
            finalizer.record_seal_complete(seal_complete()).unwrap();
            finalizer.record_final_commit(final_commit()).unwrap();
        }
        assert_eq!(state.latest_stage(), FinalizerCommitStage::FinalCommit);
    }

    #[test]
    fn every_durable_phase_restarts_and_continues() {
        let mut state = started();
        state =
            FinalizerCommitProtocolState::from_canonical_json(&state.to_canonical_json().unwrap())
                .unwrap();
        state
            .system_actor()
            .record_apply_ready(ApplyReadyEvidence::new([0x72; 32]).unwrap())
            .unwrap();
        state =
            FinalizerCommitProtocolState::from_canonical_json(&state.to_canonical_json().unwrap())
                .unwrap();
        state
            .system_actor()
            .record_seal_ready(seal_ready())
            .unwrap();
        state =
            FinalizerCommitProtocolState::from_canonical_json(&state.to_canonical_json().unwrap())
                .unwrap();
        state
            .system_actor()
            .record_exit_ready(ExitReadyEvidence::new(worker(), [0x41; 32], [0x74; 32]).unwrap())
            .unwrap();
        state =
            FinalizerCommitProtocolState::from_canonical_json(&state.to_canonical_json().unwrap())
                .unwrap();
        state
            .elevated_finalizer()
            .record_seal_complete(seal_complete())
            .unwrap();
        state =
            FinalizerCommitProtocolState::from_canonical_json(&state.to_canonical_json().unwrap())
                .unwrap();
        state
            .elevated_finalizer()
            .record_final_commit(final_commit())
            .unwrap();
        let final_bytes = state.to_canonical_json().unwrap();
        let recovered = FinalizerCommitProtocolState::from_canonical_json(&final_bytes).unwrap();
        assert_eq!(recovered.latest_stage(), FinalizerCommitStage::FinalCommit);
        assert_eq!(
            recovered.state_sha256().unwrap(),
            state.state_sha256().unwrap()
        );
    }

    #[test]
    fn identical_replays_are_idempotent_and_conflicts_fail_closed() {
        let mut state = started();
        let apply = ApplyReadyEvidence::new([0x72; 32]).unwrap();
        let first = state.system_actor().record_apply_ready(apply).unwrap();
        let replay = state.system_actor().record_apply_ready(apply).unwrap();
        assert_eq!(first.disposition(), ProtocolWriteDisposition::Created);
        assert_eq!(
            replay.disposition(),
            ProtocolWriteDisposition::AlreadyIdentical
        );
        assert_eq!(first.receipt_sha256(), replay.receipt_sha256());
        assert_eq!(first.canonical_json(), replay.canonical_json());
        assert!(state
            .system_actor()
            .record_apply_ready(ApplyReadyEvidence::new([0x75; 32]).unwrap())
            .is_err());

        state
            .system_actor()
            .record_seal_ready(seal_ready())
            .unwrap();
        state
            .system_actor()
            .record_exit_ready(ExitReadyEvidence::new(worker(), [0x41; 32], [0x74; 32]).unwrap())
            .unwrap();
        let seal = state
            .elevated_finalizer()
            .record_seal_complete(seal_complete())
            .unwrap();
        let seal_replay = state
            .elevated_finalizer()
            .record_seal_complete(seal_complete())
            .unwrap();
        assert_eq!(seal.receipt_sha256(), seal_replay.receipt_sha256());
        state
            .elevated_finalizer()
            .record_final_commit(final_commit())
            .unwrap();
        assert_eq!(
            state
                .elevated_finalizer()
                .record_final_commit(final_commit())
                .unwrap()
                .disposition(),
            ProtocolWriteDisposition::AlreadyIdentical
        );
    }

    #[test]
    fn gaps_binding_drift_and_predecessor_forgery_are_rejected() {
        let mut gap = through_exit_ready();
        gap.apply_ready = None;
        assert!(
            FinalizerCommitProtocolState::from_canonical_json(&serialize_unchecked(&gap)).is_err()
        );

        let mut binding_drift = through_exit_ready();
        binding_drift
            .exit_ready
            .as_mut()
            .unwrap()
            .binding
            .plan_sha256 = Digest32([0x91; 32]);
        assert!(
            FinalizerCommitProtocolState::from_canonical_json(&serialize_unchecked(&binding_drift))
                .is_err()
        );

        let mut forged_previous = through_exit_ready();
        forged_previous
            .exit_ready
            .as_mut()
            .unwrap()
            .previous_receipt_sha256 = Digest32([0x92; 32]);
        assert!(
            FinalizerCommitProtocolState::from_canonical_json(&serialize_unchecked(
                &forged_previous
            ))
            .is_err()
        );
    }

    #[test]
    fn seal_ready_requires_two_exact_files_and_closed_writers() {
        assert!(
            WriterHandlesClosedReadback::new(worker(), false, [0x41; 32], true, [0x42; 32])
                .is_err()
        );
        assert!(
            WriterHandlesClosedReadback::new(worker(), true, [0x41; 32], false, [0x42; 32])
                .is_err()
        );
        assert!(DurableFileIdentity::new(0, [0x21; 16], 1, 121, [0x31; 32]).is_err());
        assert!(DurableFileIdentity::new(0x101, [0; 16], 1, 121, [0x31; 32]).is_err());
        assert!(DurableFileIdentity::new(0x101, [0x21; 16], 0, 121, [0x31; 32]).is_err());
        assert!(DurableFileIdentity::new(0x101, [0x21; 16], 2, 121, [0x31; 32]).is_err());
        assert!(DurableFileIdentity::new(0x101, [0x21; 16], 1, 0, [0x31; 32]).is_err());
        let same = DurableFileIdentity::new(0x101, [0x21; 16], 1, 121, [0x31; 32]).unwrap();
        assert!(NonceArtifactPair::new(same, same).is_err());
        assert!(NonceArtifactPair::new(
            same,
            DurableFileIdentity::new(0x102, [0x22; 16], 1, 121, [0x31; 32]).unwrap(),
        )
        .is_ok());

        let mut forged = through_exit_ready();
        if let FinalizerCommitEvidence::SealReady(value) =
            &mut forged.seal_ready.as_mut().unwrap().evidence
        {
            value.writer_handles.candidate_writer_handles_closed = false;
        }
        assert!(
            FinalizerCommitProtocolState::from_canonical_json(&serialize_unchecked(&forged))
                .is_err()
        );
    }

    #[test]
    fn seal_complete_rejects_identity_drift_security_drift_and_live_candidate() {
        let mut identity_drift = through_exit_ready();
        identity_drift
            .elevated_finalizer()
            .record_seal_complete(seal_complete())
            .unwrap();
        if let FinalizerCommitEvidence::SealComplete(value) =
            &mut identity_drift.seal_complete.as_mut().unwrap().evidence
        {
            value.artifacts.worker_nonce.file_id = FileId16([0x99; 16]);
        }
        assert!(
            FinalizerCommitProtocolState::from_canonical_json(&serialize_unchecked(
                &identity_drift
            ))
            .is_err()
        );

        let mut security_drift = through_exit_ready();
        security_drift
            .elevated_finalizer()
            .record_seal_complete(seal_complete())
            .unwrap();
        if let FinalizerCommitEvidence::SealComplete(value) =
            &mut security_drift.seal_complete.as_mut().unwrap().evidence
        {
            value.sealed_security.protected_dacl_exact = false;
        }
        assert!(
            FinalizerCommitProtocolState::from_canonical_json(&serialize_unchecked(
                &security_drift
            ))
            .is_err()
        );

        let mut descriptor_drift = through_exit_ready();
        descriptor_drift
            .elevated_finalizer()
            .record_seal_complete(seal_complete())
            .unwrap();
        if let FinalizerCommitEvidence::SealComplete(value) =
            &mut descriptor_drift.seal_complete.as_mut().unwrap().evidence
        {
            value
                .sealed_security
                .candidate_consumption_descriptor_sha256 = Digest32([0x59; 32]);
        }
        assert!(
            FinalizerCommitProtocolState::from_canonical_json(&serialize_unchecked(
                &descriptor_drift
            ))
            .is_err()
        );

        let mut closure_binding_drift = through_exit_ready();
        closure_binding_drift
            .elevated_finalizer()
            .record_seal_complete(seal_complete())
            .unwrap();
        if let FinalizerCommitEvidence::SealComplete(value) = &mut closure_binding_drift
            .seal_complete
            .as_mut()
            .unwrap()
            .evidence
        {
            value
                .candidate
                .seal_ready_writer_handles_closed_readback_sha256 = Digest32([0x58; 32]);
        }
        assert!(
            FinalizerCommitProtocolState::from_canonical_json(&serialize_unchecked(
                &closure_binding_drift
            ))
            .is_err()
        );

        let mut empty_inventory_drift = through_exit_ready();
        empty_inventory_drift
            .elevated_finalizer()
            .record_seal_complete(seal_complete())
            .unwrap();
        if let FinalizerCommitEvidence::SealComplete(value) = &mut empty_inventory_drift
            .seal_complete
            .as_mut()
            .unwrap()
            .evidence
        {
            value
                .generation_seal
                .protected_blob_namespace
                .initial_empty_inventory_sha256 = Digest32([0x57; 32]);
            value.generation_seal.protected_blob_namespace.seal_sha256 = Digest32(
                value
                    .generation_seal
                    .protected_blob_namespace
                    .compute_seal_sha256(),
            );
        }
        assert!(
            FinalizerCommitProtocolState::from_canonical_json(&serialize_unchecked(
                &empty_inventory_drift
            ))
            .is_err()
        );

        let mut live_candidate = through_exit_ready();
        live_candidate
            .elevated_finalizer()
            .record_seal_complete(seal_complete())
            .unwrap();
        if let FinalizerCommitEvidence::SealComplete(value) =
            &mut live_candidate.seal_complete.as_mut().unwrap().evidence
        {
            value.candidate.scm_process_id = 4242;
            value.candidate.writer_handles_closed = false;
        }
        assert!(
            FinalizerCommitProtocolState::from_canonical_json(&serialize_unchecked(
                &live_candidate
            ))
            .is_err()
        );
    }

    #[test]
    fn final_commit_requires_exact_cas_readback_and_bound_runtime() {
        assert!(ActiveHeadPriorReadback::present([0x61; 32], [0x68; 32]).is_err());
        assert!(ActiveHeadCasReadback::new(
            ActiveHeadPriorReadback::absent([0x61; 32], [0x60; 32]).unwrap(),
            [0x62; 32],
            [0x62; 32],
            binding().generation_sha256(),
            [0x64; 32],
            4,
            ActiveHeadCasDisposition::AlreadyIdentical,
            [0x63; 32],
        )
        .is_ok());
        assert!(ActiveHeadCasReadback::new(
            ActiveHeadPriorReadback::absent([0x61; 32], [0x60; 32]).unwrap(),
            [0x62; 32],
            [0x68; 32],
            binding().generation_sha256(),
            [0x64; 32],
            4,
            ActiveHeadCasDisposition::Applied,
            [0x63; 32],
        )
        .is_err());
        assert!(CommittedRuntimeIdentity::new(
            final_commit().active_head,
            committed_runtime(),
            [0; 16],
            [0x67; 32],
            [0x75; 32],
            [0x76; 32],
            binding().expected_final_commit_gate_sha256(),
        )
        .is_err());

        let mut generation_drift = through_exit_ready();
        generation_drift
            .elevated_finalizer()
            .record_seal_complete(seal_complete())
            .unwrap();
        let mut drifted = final_commit();
        drifted.committed_runtime.generation_sha256 = Digest32([0x98; 32]);
        assert!(generation_drift
            .elevated_finalizer()
            .record_final_commit(drifted)
            .is_err());
        assert_eq!(
            generation_drift.latest_stage(),
            FinalizerCommitStage::SealComplete
        );

        let mut service_drift = through_exit_ready();
        service_drift
            .elevated_finalizer()
            .record_seal_complete(seal_complete())
            .unwrap();
        let mut drifted = final_commit();
        drifted
            .committed_runtime
            .runtime
            .exact_runtime_instance_sha256 = Digest32([0x99; 32]);
        assert!(service_drift
            .elevated_finalizer()
            .record_final_commit(drifted)
            .is_err());

        let mut process_reuse = through_exit_ready();
        process_reuse
            .elevated_finalizer()
            .record_seal_complete(seal_complete())
            .unwrap();
        let mut reused = final_commit();
        reused.committed_runtime.runtime = candidate_runtime();
        reused.committed_runtime.precommit_dormant_readback_sha256 =
            Digest32(precommit_dormant_runtime_sha256(
                reused.committed_runtime.generation_sha256.0,
                reused.committed_runtime.active_head_cas_projection_sha256.0,
                &reused.committed_runtime.runtime,
                reused
                    .committed_runtime
                    .precommit_handshake_pipe_instance_id
                    .0,
                reused
                    .committed_runtime
                    .precommit_handshake_readback_sha256
                    .0,
                reused
                    .committed_runtime
                    .controller_pipe_absence_readback_sha256
                    .0,
                reused
                    .committed_runtime
                    .generation_writer_roster_readback_sha256
                    .0,
                reused
                    .committed_runtime
                    .final_commit_activation_gate_sha256
                    .0,
            ));
        assert!(process_reuse
            .elevated_finalizer()
            .record_final_commit(reused)
            .is_err());
    }

    #[test]
    fn noncanonical_unknown_and_uppercase_encodings_are_rejected() {
        let state = completed();
        let canonical = state.to_canonical_json().unwrap();
        let mut whitespace = b" ".to_vec();
        whitespace.extend_from_slice(&canonical);
        assert!(FinalizerCommitProtocolState::from_canonical_json(&whitespace).is_err());

        let mut value: serde_json::Value = serde_json::from_slice(&canonical).unwrap();
        value["unexpected"] = serde_json::json!(true);
        assert!(FinalizerCommitProtocolState::from_canonical_json(
            &serde_json::to_vec(&value).unwrap()
        )
        .is_err());

        let lettered_binding = FinalizerCommitBinding::new(
            [0xab; 32],
            binding().plan_sha256(),
            binding().generation_sha256(),
            binding().transaction_sha256(),
            plan_binding(),
            [0x15; 32],
        )
        .unwrap();
        let lettered = FinalizerCommitProtocolState::transaction_started(
            lettered_binding,
            TransactionStartedEvidence::new(lettered_binding, worker(), [0x72; 32]).unwrap(),
        )
        .unwrap()
        .to_canonical_json()
        .unwrap();
        let upper = String::from_utf8(lettered).unwrap().replacen(
            &hex_lower(&[0xab; 32]),
            &hex_lower(&[0xab; 32]).to_uppercase(),
            1,
        );
        assert!(FinalizerCommitProtocolState::from_canonical_json(upper.as_bytes()).is_err());
    }

    #[test]
    fn operation_residue_plan_and_readback_are_exact_and_operation_aware() {
        let install = residue_plan_for(AuthorityMaintenanceOperation::Install);
        let update = residue_plan_for(AuthorityMaintenanceOperation::Update);
        assert_ne!(install.digest().unwrap(), update.digest().unwrap());
        assert_eq!(install.operation(), AuthorityMaintenanceOperation::Install);
        let install_expectations = install.expectations().unwrap();
        assert_eq!(
            install_expectations.map(ResidueObjectExpectationView::dimension),
            RESIDUE_DIMENSIONS
        );
        assert_eq!(
            install_expectations[0].expectation(),
            ResidueExpectationKind::Absent
        );
        assert_eq!(install_expectations[0].object_binding_sha256(), [0x80; 32]);
        assert_eq!(install_expectations[0].expected_identity_sha256(), None);
        assert_eq!(
            install_expectations[5].expectation(),
            ResidueExpectationKind::PresentExact
        );
        assert_eq!(
            install_expectations[5].expected_identity_sha256(),
            Some([0xa5; 32])
        );
        assert!(matches!(
            install.objects[11].expectation,
            ResidueExpectation::Absent
        ));
        assert!(matches!(
            update.objects[11].expectation,
            ResidueExpectation::PresentExact
        ));

        let mut reordered = install.objects;
        reordered.swap(0, 1);
        assert!(
            OperationResiduePlan::new(AuthorityMaintenanceOperation::Install, reordered).is_err()
        );
        assert!(
            OperationResiduePlan::new(AuthorityMaintenanceOperation::Retire, install.objects,)
                .is_err()
        );

        let readback = zero_residue_for(install);
        assert!(!is_zero(&readback.digest().unwrap()));
        let mut drifted = readback.objects;
        drifted[5].observed_identity_sha256 = Some(Digest32([0xee; 32]));
        assert!(OperationZeroResidueReadback::new(install, drifted).is_err());
    }

    #[test]
    fn recovery_projection_exposes_only_validated_typed_resume_evidence() {
        let exit_state = through_exit_ready();
        let canonical = exit_state.to_canonical_json().unwrap();
        let reopened = FinalizerCommitProtocolState::from_canonical_json(&canonical).unwrap();
        let recovery = reopened.recovery_projection().unwrap();
        assert_eq!(recovery.binding(), binding());
        assert_eq!(recovery.latest_stage(), FinalizerCommitStage::ExitReady);
        assert_eq!(recovery.transaction_worker().process_id(), 2222);
        assert_eq!(recovery.transaction_worker().process_creation_time(), 555);
        assert_eq!(recovery.durable_start_readback_sha256(), [0x72; 32]);
        assert!(!is_zero(&recovery.worker_activation_proof_sha256()));
        assert_eq!(recovery.exact_apply_readback_sha256(), Some([0x73; 32]));

        let seal_ready = recovery.seal_ready().unwrap();
        assert_eq!(seal_ready.artifacts(), artifacts());
        assert_eq!(seal_ready.writer_handles().worker().process_id(), 2222);
        assert_eq!(seal_ready.candidate().process_id(), 3333);
        assert_eq!(seal_ready.candidate().process_creation_time(), 666);
        assert_eq!(seal_ready.candidate().image_sha256(), [0x5a; 32]);
        assert_eq!(
            seal_ready.candidate().activation_readback_sha256(),
            [0x5b; 32]
        );
        let exit_ready = recovery.exit_ready().unwrap();
        assert_eq!(
            exit_ready.worker().exact_process_identity_sha256(),
            worker().exact_process_identity_sha256()
        );
        assert_eq!(
            exit_ready.worker_writer_handles_closed_readback_sha256(),
            [0x41; 32]
        );
        assert_eq!(exit_ready.durable_exit_readback_sha256(), [0x74; 32]);
        assert!(recovery.seal_complete().is_none());
        assert!(recovery.final_commit().is_none());

        let completed = completed().recovery_projection().unwrap();
        assert_eq!(completed.latest_stage(), FinalizerCommitStage::FinalCommit);
        assert!(completed.seal_complete().is_some());
        assert!(completed.final_commit().is_some());
    }

    #[test]
    fn final_commit_gate_is_root_bound_and_postcommit_readback_is_same_process() {
        let first = binding();
        let second = FinalizerCommitBinding::new(
            first.capsule_sha256(),
            first.plan_sha256(),
            first.generation_sha256(),
            first.transaction_sha256(),
            first.plan_binding(),
            [0x16; 32],
        )
        .unwrap();
        assert_ne!(
            first.expected_final_commit_gate_sha256(),
            second.expected_final_commit_gate_sha256()
        );

        let state = completed();
        let projection = state
            .final_commit_persistence_projection()
            .unwrap()
            .unwrap();
        let runtime = projection.committed_runtime;
        let serving = PostcommitServingRuntimeReadback::exact(
            projection,
            runtime.runtime(),
            projection.final_commit_receipt_sha256,
            [0x77; 16],
            [0x78; 32],
            [0x79; 32],
        )
        .unwrap();
        assert!(!is_zero(&serving.readback_sha256()));
        let drifted_process = CandidateProcessEvidence::from_held_process(
            runtime.process_id() + 1,
            runtime.process_creation_time(),
            runtime.image_sha256(),
            runtime.runtime().process().image_byte_length(),
            runtime.runtime().process().image_volume_serial(),
            *runtime.runtime().process().image_file_id(),
            runtime.runtime().process().image_link_count(),
            runtime.runtime().process().image_attributes(),
        )
        .unwrap();
        let drifted_runtime = ExactServiceRuntimeIdentity::from_observed(
            runtime.exact_service_configuration_sha256(),
            [0x57; 32],
            drifted_process,
        )
        .unwrap();
        assert!(PostcommitServingRuntimeReadback::exact(
            projection,
            drifted_runtime,
            projection.final_commit_receipt_sha256,
            [0x77; 16],
            [0x78; 32],
            [0x79; 32],
        )
        .is_err());
        assert!(PostcommitServingRuntimeReadback::exact(
            projection,
            runtime.runtime(),
            [0xfe; 32],
            [0x77; 16],
            [0x78; 32],
            [0x79; 32],
        )
        .is_err());
    }

    #[test]
    fn candidate_identity_consumes_the_exact_seal_ready_projection() {
        let observation = CandidateActivationObservation::new(
            [0x11; 32], [0x12; 32], [0x13; 32], 7, [0x14; 32], [0x15; 32], [0x16; 32], [0x17; 32],
            [0x41; 32], 919, 42_424,
        )
        .unwrap();
        let candidate_binding =
            CandidateActivationBinding::new(observation, [0x19; 32], 10_000, 20_000).unwrap();
        let candidate_process = CandidateProcessEvidence::from_static_image(
            1771,
            88_181,
            *candidate_binding.target_service_image(),
        )
        .unwrap();
        let projection_for_test = || {
            CandidateActivationSealReadyProjection::for_test(
                candidate_binding,
                [0x71; 32],
                [0x73; 32],
                candidate_process,
                artifacts().candidate_consumption().bytes_sha256(),
                artifacts().candidate_consumption().volume_serial(),
                artifacts().candidate_consumption().file_id(),
                artifacts().candidate_consumption().link_count(),
            )
            .unwrap()
        };
        let projection = projection_for_test();
        let expected_exact_service_identity = projection.exact_service_identity_sha256();
        let expected_activation_readback = projection.activation_readback_sha256();
        let plan = FinalizerCommitPlanBinding::new(
            AuthorityMaintenanceOperation::Install,
            [0x70; 32],
            [0x71; 32],
            [0x71; 32],
            [0x41; 32],
            [0x14; 32],
            [0x62; 32],
            [0x16; 32],
            7,
            [0x18; 32],
            residue_plan_for(AuthorityMaintenanceOperation::Install),
        )
        .unwrap();
        let commit_binding = FinalizerCommitBinding::new(
            [0x10; 32], [0x12; 32], [0x11; 32], [0x13; 32], plan, [0x1a; 32],
        )
        .unwrap();
        let identity = CandidateActivationIdentity::from_verified_seal_ready(
            commit_binding,
            artifacts(),
            projection,
        )
        .unwrap();
        assert_eq!(identity.process_id(), 1771);
        assert_eq!(identity.process_creation_time(), 88_181);
        assert_eq!(identity.image_sha256(), [0x41; 32]);
        assert_eq!(
            identity.exact_service_identity_sha256(),
            expected_exact_service_identity
        );
        assert_ne!(identity.exact_service_identity_sha256(), [0x71; 32]);
        assert_eq!(
            identity.activation_readback_sha256(),
            expected_activation_readback
        );

        let drifted_plan = FinalizerCommitPlanBinding::new(
            AuthorityMaintenanceOperation::Install,
            [0x70; 32],
            [0x71; 32],
            [0xee; 32],
            [0x41; 32],
            [0x14; 32],
            [0x62; 32],
            [0x16; 32],
            7,
            [0x18; 32],
            residue_plan_for(AuthorityMaintenanceOperation::Install),
        )
        .unwrap();
        let drifted_binding = FinalizerCommitBinding::new(
            [0x10; 32],
            [0x12; 32],
            [0x11; 32],
            [0x13; 32],
            drifted_plan,
            [0x1a; 32],
        )
        .unwrap();
        assert!(CandidateActivationIdentity::from_verified_seal_ready(
            drifted_binding,
            artifacts(),
            projection_for_test(),
        )
        .is_err());

        let exact = artifacts();
        let same_bytes_different_file = NonceArtifactPair::new(
            exact.worker_nonce(),
            DurableFileIdentity::new(
                exact.candidate_consumption().volume_serial() + 1,
                [0x2f; 16],
                1,
                exact.candidate_consumption().byte_length(),
                exact.candidate_consumption().bytes_sha256(),
            )
            .unwrap(),
        )
        .unwrap();
        assert!(CandidateActivationIdentity::from_verified_seal_ready(
            commit_binding,
            same_bytes_different_file,
            projection_for_test(),
        )
        .is_err());
    }

    #[test]
    fn committed_restart_readback_requires_exact_final_state_and_a_new_process() {
        let projection = completed()
            .final_commit_persistence_projection()
            .unwrap()
            .unwrap();
        let precommit = projection.committed_runtime;
        let prior_process = precommit.runtime().process();
        let recovered_process = CandidateProcessEvidence::from_held_process(
            precommit.process_id() + 1,
            precommit.process_creation_time() + 1,
            precommit.image_sha256(),
            prior_process.image_byte_length(),
            prior_process.image_volume_serial(),
            *prior_process.image_file_id(),
            prior_process.image_link_count(),
            prior_process.image_attributes(),
        )
        .unwrap();
        let recovered_runtime = ExactServiceRuntimeIdentity::from_observed(
            precommit.exact_service_configuration_sha256(),
            [0x58; 32],
            recovered_process,
        )
        .unwrap();
        let previous_absence = PreviousRuntimeAbsenceReadback::from_observed(
            precommit,
            precommit.exact_service_identity_sha256(),
            precommit.process_id(),
            precommit.process_creation_time(),
            SERVICE_STATE_STOPPED,
            0,
            0,
            0,
            [0x80; 32],
            [0x85; 32],
            [0x86; 32],
        )
        .unwrap();
        let recovered = RecoveredPostcommitServingRuntimeReadback::after_committed_restart(
            projection,
            previous_absence,
            projection.active_head.observed_head_sha256.0,
            recovered_runtime,
            projection.binding.generation_sha256(),
            projection.final_commit_receipt_sha256,
            projection.expected_final_commit_gate_sha256,
            [0x81; 16],
            [0x82; 32],
            [0x83; 32],
            [0x84; 32],
        )
        .unwrap();
        assert!(!is_zero(&recovered.readback_sha256()));

        let same_process = precommit.runtime();
        assert!(
            RecoveredPostcommitServingRuntimeReadback::after_committed_restart(
                projection,
                previous_absence,
                projection.active_head.observed_head_sha256.0,
                same_process,
                projection.binding.generation_sha256(),
                projection.final_commit_receipt_sha256,
                projection.expected_final_commit_gate_sha256,
                [0x81; 16],
                [0x82; 32],
                [0x83; 32],
                [0x84; 32],
            )
            .is_err()
        );
        let drifted_configuration =
            ExactServiceRuntimeIdentity::from_observed([0xee; 32], [0x58; 32], recovered_process)
                .unwrap();
        assert!(
            RecoveredPostcommitServingRuntimeReadback::after_committed_restart(
                projection,
                previous_absence,
                projection.active_head.observed_head_sha256.0,
                drifted_configuration,
                projection.binding.generation_sha256(),
                projection.final_commit_receipt_sha256,
                projection.expected_final_commit_gate_sha256,
                [0x81; 16],
                [0x82; 32],
                [0x83; 32],
                [0x84; 32],
            )
            .is_err()
        );
        assert!(
            RecoveredPostcommitServingRuntimeReadback::after_committed_restart(
                projection,
                previous_absence,
                projection.active_head.observed_head_sha256.0,
                recovered_runtime,
                projection.binding.generation_sha256(),
                [0xef; 32],
                projection.expected_final_commit_gate_sha256,
                [0x81; 16],
                [0x82; 32],
                [0x83; 32],
                [0x84; 32],
            )
            .is_err()
        );
        assert!(PreviousRuntimeAbsenceReadback::from_observed(
            precommit,
            precommit.exact_service_identity_sha256(),
            precommit.process_id() + 1,
            precommit.process_creation_time(),
            SERVICE_STATE_STOPPED,
            0,
            0,
            0,
            [0x80; 32],
            [0x85; 32],
            [0x86; 32],
        )
        .is_err());
        assert!(PreviousRuntimeAbsenceReadback::from_observed(
            precommit,
            precommit.exact_service_identity_sha256(),
            precommit.process_id(),
            precommit.process_creation_time(),
            4,
            precommit.process_id(),
            0,
            0,
            [0x80; 32],
            [0x85; 32],
            [0x86; 32],
        )
        .is_err());
    }

    #[test]
    fn worker_candidate_and_generation_terminal_are_cross_bound() {
        let mut worker_drift = through_exit_ready();
        if let FinalizerCommitEvidence::ExitReady(value) =
            &mut worker_drift.exit_ready.as_mut().unwrap().evidence
        {
            value.worker.image_sha256 = Digest32([0xee; 32]);
        }
        assert!(
            FinalizerCommitProtocolState::from_canonical_json(&serialize_unchecked(&worker_drift))
                .is_err()
        );

        let mut candidate_drift = through_exit_ready();
        if let FinalizerCommitEvidence::SealReady(value) =
            &mut candidate_drift.seal_ready.as_mut().unwrap().evidence
        {
            value.candidate.commit_binding_sha256 = Digest32([0xef; 32]);
        }
        assert!(
            FinalizerCommitProtocolState::from_canonical_json(&serialize_unchecked(
                &candidate_drift
            ))
            .is_err()
        );

        let mut terminal_drift = through_exit_ready();
        let mut seal = seal_complete();
        seal.generation_seal.object_count = 9;
        assert!(terminal_drift
            .elevated_finalizer()
            .record_seal_complete(seal)
            .is_err());
    }

    #[test]
    fn public_identity_getters_preserve_exact_values() {
        let binding = binding();
        assert_eq!(binding.capsule_sha256(), [0x11; 32]);
        assert_eq!(binding.plan_sha256(), [0x12; 32]);
        assert_eq!(binding.generation_sha256(), [0x13; 32]);
        assert_eq!(binding.transaction_sha256(), [0x14; 32]);
        let artifacts = artifacts();
        let worker_nonce = artifacts.worker_nonce();
        assert_eq!(worker_nonce.volume_serial(), 0x101);
        assert_eq!(worker_nonce.file_id(), [0x21; 16]);
        assert_eq!(worker_nonce.byte_length(), 121);
        assert_eq!(worker_nonce.bytes_sha256(), [0x31; 32]);
        assert_eq!(artifacts.candidate_consumption().bytes_sha256(), [0x32; 32]);
        let writers =
            WriterHandlesClosedReadback::new(worker(), true, [0x41; 32], true, [0x42; 32]).unwrap();
        assert_eq!(writers.worker_readback_sha256(), [0x41; 32]);
        assert_eq!(writers.candidate_readback_sha256(), [0x42; 32]);
        assert_eq!(started().binding(), binding);
    }
}
