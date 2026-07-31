//! Exact actor and access-control lifecycle for protected authority objects.
//!
//! This module is intentionally pure policy. It performs no filesystem,
//! service-control, process, or key-store operation. Native adapters must use
//! these projections with CreateNew/held-handle/readback contracts before any
//! production mutation gate can be considered.

use serde::{Deserialize, Serialize};
use std::fmt;

pub(crate) const SECURITY_POLICY_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_security_policy.v2";
pub(crate) const LOCAL_SYSTEM_SID: &str = "S-1-5-18";
pub(crate) const BUILTIN_ADMINISTRATORS_SID: &str = "S-1-5-32-544";
pub(crate) const AUTHORITY_SERVICE_SID: &str =
    "S-1-5-80-627086344-872206109-3199044541-2745001037-75066892";
pub(crate) const MAINTENANCE_SERVICE_SID: &str =
    "S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439";

pub(crate) const DELETE_SELF_ACCESS: u32 = 0x0001_0000;
pub(crate) const READ_CONTROL_ACCESS: u32 = 0x0002_0000;
pub(crate) const WRITE_DAC_ACCESS: u32 = 0x0004_0000;
pub(crate) const WRITE_OWNER_ACCESS: u32 = 0x0008_0000;
pub(crate) const SYNCHRONIZE_ACCESS: u32 = 0x0010_0000;
pub(crate) const FILE_READ_DATA_ACCESS: u32 = 0x0000_0001;
pub(crate) const FILE_WRITE_DATA_ACCESS: u32 = 0x0000_0002;
pub(crate) const FILE_APPEND_DATA_ACCESS: u32 = 0x0000_0004;
pub(crate) const FILE_READ_EA_ACCESS: u32 = 0x0000_0008;
pub(crate) const FILE_WRITE_EA_ACCESS: u32 = 0x0000_0010;
pub(crate) const FILE_EXECUTE_ACCESS: u32 = 0x0000_0020;
pub(crate) const FILE_DELETE_CHILD_ACCESS: u32 = 0x0000_0040;
pub(crate) const FILE_READ_ATTRIBUTES_ACCESS: u32 = 0x0000_0080;
pub(crate) const FILE_WRITE_ATTRIBUTES_ACCESS: u32 = 0x0000_0100;

pub(crate) const DIRECTORY_READ_EXECUTE_ACCESS: u32 = 0x0012_00a9;
pub(crate) const DIRECTORY_CREATE_CHILD_ACCESS: u32 = 0x0012_00af;
pub(crate) const DIRECTORY_CREATE_FILE_ACCESS: u32 = 0x0012_00ab;
pub(crate) const DIRECTORY_WORKER_STAGING_ACCESS: u32 = 0x0013_00af;
pub(crate) const DIRECTORY_FINALIZER_STAGING_ACCESS: u32 = 0x0017_00af;
pub(crate) const DIRECTORY_RETIRE_ACCESS: u32 = 0x0013_00a9;
pub(crate) const FILE_READ_ACCESS: u32 = 0x0012_0089;
pub(crate) const FILE_READ_EXECUTE_ACCESS: u32 = 0x0012_00a9;
pub(crate) const FILE_WORKER_STAGING_ACCESS: u32 = 0x0013_008f;
pub(crate) const FILE_FINALIZER_STAGING_ACCESS: u32 = 0x0017_008f;
pub(crate) const FILE_RETIRE_READ_ACCESS: u32 = 0x0013_0089;
pub(crate) const FILE_RETIRE_READ_EXECUTE_ACCESS: u32 = 0x0013_00a9;
pub(crate) const LEDGER_FINAL_AUTHORITY_ACCESS: u32 = 0x0012_008f;
pub(crate) const RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS: u32 = 0x0012_00a3;
pub(crate) const RUNTIME_BLOB_FILE_AUTHORITY_ACCESS: u32 = 0x0013_0083;
pub(crate) const RUNTIME_BLOB_FILE_READ_ACCESS: u32 = 0x0012_0081;
pub(crate) const RUNTIME_BLOB_FILE_CLEANUP_ACCESS: u32 = 0x0013_0081;

pub(crate) const SERVICE_QUERY_CONFIG_ACCESS: u32 = 0x0000_0001;
pub(crate) const SERVICE_CHANGE_CONFIG_ACCESS: u32 = 0x0000_0002;
pub(crate) const SERVICE_QUERY_STATUS_ACCESS: u32 = 0x0000_0004;
pub(crate) const SERVICE_START_ACCESS: u32 = 0x0000_0010;
pub(crate) const SERVICE_STOP_ACCESS: u32 = 0x0000_0020;
pub(crate) const AUTHORITY_SERVICE_CANDIDATE_START_ACCESS: u32 = 0x0002_0015;
pub(crate) const AUTHORITY_SERVICE_ELEVATED_FINALIZER_ACCESS: u32 = 0x0007_0037;

const SYSTEM_FILE_ALL_ACCESS: u32 = 0x001f_01ff;
const SYSTEM_SERVICE_ALL_ACCESS: u32 = 0x000f_01ff;
const GENERIC_RIGHTS_ACCESS: u32 = 0xf000_0000;
const ACCESS_SYSTEM_SECURITY_ACCESS: u32 = 0x0100_0000;
const MAX_POLICY_BYTES: usize = 64 * 1024;

pub(crate) const STABLE_ROOT_SDDL: &str = "O:SYG:SYD:P(A;OICI;0x001f01ff;;;SY)(A;OICI;0x001200af;;;BA)(A;OICI;0x001200a9;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;OICI;0x001200a9;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;OICI;NW;;;HI)";
pub(crate) const GENERATION_STAGING_SDDL: &str = "O:SYG:SYD:P(A;OICI;0x001f01ff;;;SY)(A;OICI;0x001700af;;;BA)(A;OICI;0x001200a9;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;OICI;0x001300af;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;OICI;NW;;;HI)";
pub(crate) const GENERATION_SEALED_SDDL: &str = "O:SYG:SYD:P(A;OICI;0x001f01ff;;;SY)(A;OICI;0x001300a9;;;BA)(A;OICI;0x001200a9;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;OICI;0x001200a9;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;OICI;NW;;;HI)";
pub(crate) const BINARY_STAGING_SDDL: &str = "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x0017008f;;;BA)(A;;0x001200a9;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;;0x0013008f;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;;NW;;;HI)";
pub(crate) const BINARY_SEALED_SDDL: &str = "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x001300a9;;;BA)(A;;0x001200a9;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;;0x00120089;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;;NW;;;HI)";
pub(crate) const STATE_STAGING_SDDL: &str = "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x0017008f;;;BA)(A;;0x00120089;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;;0x0013008f;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;;NW;;;HI)";
pub(crate) const STATE_IMMUTABLE_SDDL: &str = "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x00130089;;;BA)(A;;0x00120089;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;;0x00120089;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;;NW;;;HI)";
pub(crate) const FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL: &str = "O:SYG:SYD:P(A;;0x001200ab;;;SY)(A;;0x001200ab;;;BA)(A;;0x001200a1;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;;0x001200a0;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;;NW;;;HI)";
pub(crate) const FINALIZER_PUBLISHING_PRIVATE_SEALED_SDDL: &str = "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x00170089;;;BA)(A;;0x00120089;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;;0x00120089;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;;NW;;;HI)";
pub(crate) const FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL: &str = "O:SYG:SYD:P(A;;0x00130089;;;SY)(A;;0x00120089;;;BA)(A;;0x00120089;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;;0x00120089;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;;NW;;;HI)";
pub(crate) const FINALIZER_COMMIT_TRANSACTION_RECEIPT_HANDLE_ACCESS: u32 = 0x0112_00a2;
pub(crate) const FINALIZER_COMMIT_TRANSACTION_PROGRESS_HANDLE_ACCESS: u32 = 0x0112_00ab;
pub(crate) const FINALIZER_COMMIT_TRANSACTION_READONLY_HANDLE_ACCESS: u32 = 0x0012_00a1;
pub(crate) const LEDGER_STAGING_SDDL: &str = "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x0017008f;;;BA)(A;;0x0013008f;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;;NW;;;HI)";
pub(crate) const LEDGER_FINAL_SDDL: &str = "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x00130089;;;BA)(A;;0x0012008f;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)S:(ML;;NW;;;HI)";
pub(crate) const RUNTIME_BLOB_DIRECTORY_STAGING_SDDL: &str =
    "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x001700af;;;BA)S:(ML;OICI;NW;;;HI)";
pub(crate) const RUNTIME_BLOB_DIRECTORY_FINAL_SDDL: &str = "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x001300a9;;;BA)(A;;0x001200a3;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)S:(ML;OICI;NW;;;HI)";
pub(crate) const RUNTIME_BLOB_FILE_SDDL: &str = "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x00130089;;;BA)(A;;0x00130083;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)S:(ML;;NW;;;HI)";
pub(crate) const WORKER_NONCE_NAMESPACE_SDDL: &str = "O:SYG:SYD:P(A;OICI;0x001f01ff;;;SY)(A;OICI;0x001200ab;;;BA)(A;OICI;0x001200a9;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;OICI;0x001200ab;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;OICI;NW;;;HI)";
pub(crate) const CANDIDATE_CONSUMPTION_NAMESPACE_SDDL: &str = "O:SYG:SYD:P(A;OICI;0x001f01ff;;;SY)(A;OICI;0x001200ab;;;BA)(A;OICI;0x001200ab;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;OICI;0x001200a9;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;OICI;NW;;;HI)";
pub(crate) const CANDIDATE_ACTIVATION_NAMESPACE_SDDL: &str = "O:SYG:SYD:P(A;OICI;0x001f01ff;;;SY)(A;OICI;0x001200ab;;;BA)(A;OICI;0x001200a9;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;OICI;0x001200ab;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;OICI;NW;;;HI)";
pub(crate) const WORKER_NONCE_STAGING_SDDL: &str = "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x0017008f;;;BA)(A;;0x00120089;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;;0x0013008f;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;;NW;;;HI)";
pub(crate) const CANDIDATE_CONSUMPTION_STAGING_SDDL: &str = "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x0017008f;;;BA)(A;;0x0013008f;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;;0x00120089;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;;NW;;;HI)";
pub(crate) const NONCE_SEALED_SDDL: &str = "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x00120089;;;BA)(A;;0x00120089;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;;0x00120089;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;;NW;;;HI)";
pub(crate) const AUTHORITY_SERVICE_CANDIDATE_START_SDDL: &str = "O:SYG:SYD:P(A;;0x000f01ff;;;SY)(A;;0x00070037;;;BA)(A;;0x00020015;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;;NW;;;HI)";

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct SecurityPolicyError(&'static str);

impl SecurityPolicyError {
    pub(crate) fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for SecurityPolicyError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for SecurityPolicyError {}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) enum AuthorityActor {
    ElevatedPreparation,
    RestrictedWorker,
    ElevatedFinalizer,
    /// The fixed authority service SID is also used by the pre-commit
    /// candidate process; lifecycle state, not a second SID, separates them.
    CommittedRuntime,
}

impl AuthorityActor {
    pub(crate) const ALL: [Self; 4] = [
        Self::ElevatedPreparation,
        Self::RestrictedWorker,
        Self::ElevatedFinalizer,
        Self::CommittedRuntime,
    ];

    pub(crate) const fn principal_sid(self) -> &'static str {
        match self {
            Self::ElevatedPreparation | Self::ElevatedFinalizer => BUILTIN_ADMINISTRATORS_SID,
            Self::RestrictedWorker => MAINTENANCE_SERVICE_SID,
            Self::CommittedRuntime => AUTHORITY_SERVICE_SID,
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) enum SecurityObjectKind {
    StableRoot,
    GenerationStaging,
    GenerationSealed,
    BinaryStaging,
    BinarySealed,
    StateStaging,
    StateImmutable,
    LedgerStaging,
    LedgerFinal,
    RuntimeBlobDirectoryStaging,
    RuntimeBlobDirectoryFinal,
    RuntimeBlobFile,
    WorkerNonceNamespace,
    CandidateConsumptionNamespace,
    CandidateActivationNamespace,
    WorkerNonceStaging,
    CandidateConsumptionStaging,
    NonceSealed,
    AuthorityServiceCandidateStart,
}

impl SecurityObjectKind {
    pub(crate) const ALL: [Self; 19] = [
        Self::StableRoot,
        Self::GenerationStaging,
        Self::GenerationSealed,
        Self::BinaryStaging,
        Self::BinarySealed,
        Self::StateStaging,
        Self::StateImmutable,
        Self::LedgerStaging,
        Self::LedgerFinal,
        Self::RuntimeBlobDirectoryStaging,
        Self::RuntimeBlobDirectoryFinal,
        Self::RuntimeBlobFile,
        Self::WorkerNonceNamespace,
        Self::CandidateConsumptionNamespace,
        Self::CandidateActivationNamespace,
        Self::WorkerNonceStaging,
        Self::CandidateConsumptionStaging,
        Self::NonceSealed,
        Self::AuthorityServiceCandidateStart,
    ];
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) enum SecurityPolicyPhase {
    Stable,
    Namespace,
    Staging,
    Sealed,
    Immutable,
    Final,
    CandidateStart,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) enum SecurityPolicyTransition {
    ProvisionStableRoot,
    StageGeneration,
    SealGeneration,
    StageBinary,
    SealBinary,
    StageState,
    SealState,
    StageLedger,
    SealLedger,
    ProvisionRuntimeBlobDirectory,
    SealRuntimeBlobDirectory,
    MaterializeRuntimeBlobFile,
    ProvisionWorkerNonceNamespace,
    ProvisionCandidateConsumptionNamespace,
    ProvisionCandidateActivationNamespace,
    StageWorkerNonce,
    SealWorkerNonce,
    StageCandidateConsumption,
    SealCandidateConsumption,
    ProvisionCandidateService,
    RetireGeneration,
}

impl SecurityPolicyTransition {
    pub(crate) const ALL: [Self; 21] = [
        Self::ProvisionStableRoot,
        Self::StageGeneration,
        Self::SealGeneration,
        Self::StageBinary,
        Self::SealBinary,
        Self::StageState,
        Self::SealState,
        Self::StageLedger,
        Self::SealLedger,
        Self::ProvisionRuntimeBlobDirectory,
        Self::SealRuntimeBlobDirectory,
        Self::MaterializeRuntimeBlobFile,
        Self::ProvisionWorkerNonceNamespace,
        Self::ProvisionCandidateConsumptionNamespace,
        Self::ProvisionCandidateActivationNamespace,
        Self::StageWorkerNonce,
        Self::SealWorkerNonce,
        Self::StageCandidateConsumption,
        Self::SealCandidateConsumption,
        Self::ProvisionCandidateService,
        Self::RetireGeneration,
    ];
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct ActorAccessProjection {
    actor: AuthorityActor,
    principal_sid: String,
    access_mask: u32,
}

impl ActorAccessProjection {
    pub(crate) fn actor(&self) -> AuthorityActor {
        self.actor
    }

    pub(crate) fn principal_sid(&self) -> &str {
        &self.principal_sid
    }

    pub(crate) fn access_mask(&self) -> u32 {
        self.access_mask
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct ObjectSecurityPolicyProjection {
    schema: String,
    object: SecurityObjectKind,
    phase: SecurityPolicyPhase,
    owner_sid: String,
    group_sid: String,
    owner_access_mask: u32,
    dacl_protected: bool,
    high_integrity_no_write_up: bool,
    sddl: String,
    actor_access: Vec<ActorAccessProjection>,
}

impl ObjectSecurityPolicyProjection {
    pub(crate) fn object(&self) -> SecurityObjectKind {
        self.object
    }

    pub(crate) fn phase(&self) -> SecurityPolicyPhase {
        self.phase
    }

    pub(crate) fn sddl(&self) -> &str {
        &self.sddl
    }

    pub(crate) fn access_for(&self, actor: AuthorityActor) -> u32 {
        self.actor_access
            .iter()
            .find(|grant| grant.actor == actor)
            .map_or(0, |grant| grant.access_mask)
    }

    pub(crate) fn actor_access(&self) -> &[ActorAccessProjection] {
        &self.actor_access
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct SecurityTransitionProjection {
    transition: SecurityPolicyTransition,
    actor: AuthorityActor,
    source: Option<SecurityObjectKind>,
    destination: Option<SecurityObjectKind>,
    create_new_only: bool,
    held_parent_required: bool,
    close_write_handles_before_readback: bool,
    exact_readback_required: bool,
}

impl SecurityTransitionProjection {
    pub(crate) fn transition(&self) -> SecurityPolicyTransition {
        self.transition
    }

    pub(crate) fn actor(&self) -> AuthorityActor {
        self.actor
    }

    pub(crate) fn source(&self) -> Option<SecurityObjectKind> {
        self.source
    }

    pub(crate) fn destination(&self) -> Option<SecurityObjectKind> {
        self.destination
    }

    pub(crate) fn permits(&self, actor: AuthorityActor) -> bool {
        self.actor == actor
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct SecurityPolicyBundle {
    schema: String,
    objects: Vec<ObjectSecurityPolicyProjection>,
    transitions: Vec<SecurityTransitionProjection>,
}

impl SecurityPolicyBundle {
    pub(crate) fn exact() -> Self {
        Self {
            schema: SECURITY_POLICY_SCHEMA.to_string(),
            objects: SecurityObjectKind::ALL
                .iter()
                .copied()
                .map(project_object_policy)
                .collect(),
            transitions: SecurityPolicyTransition::ALL
                .iter()
                .copied()
                .map(project_transition)
                .collect(),
        }
    }

    pub(crate) fn object_policy(
        &self,
        object: SecurityObjectKind,
    ) -> Option<&ObjectSecurityPolicyProjection> {
        self.objects.iter().find(|policy| policy.object == object)
    }

    pub(crate) fn transition_policy(
        &self,
        transition: SecurityPolicyTransition,
    ) -> Option<&SecurityTransitionProjection> {
        self.transitions
            .iter()
            .find(|policy| policy.transition == transition)
    }

    pub(crate) fn objects(&self) -> &[ObjectSecurityPolicyProjection] {
        &self.objects
    }

    pub(crate) fn transitions(&self) -> &[SecurityTransitionProjection] {
        &self.transitions
    }

    pub(crate) fn canonical_bytes(&self) -> Result<Vec<u8>, SecurityPolicyError> {
        self.validate()?;
        serde_json::to_vec(self)
            .map_err(|_| SecurityPolicyError("authority_security_policy_invalid"))
    }

    pub(crate) fn parse_canonical(bytes: &[u8]) -> Result<Self, SecurityPolicyError> {
        if bytes.is_empty() || bytes.len() > MAX_POLICY_BYTES {
            return Err(SecurityPolicyError(
                "authority_security_policy_size_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes)
            .map_err(|_| SecurityPolicyError("authority_security_policy_invalid"))?;
        if serde_json::to_vec(&value).ok().as_deref() != Some(bytes) {
            return Err(SecurityPolicyError(
                "authority_security_policy_noncanonical",
            ));
        }
        value.validate()?;
        Ok(value)
    }

    pub(crate) fn validate(&self) -> Result<(), SecurityPolicyError> {
        if self != &Self::exact() {
            return Err(SecurityPolicyError("authority_security_policy_invalid"));
        }
        validate_security_invariants(self)
    }
}

pub(crate) fn project_object_policy(object: SecurityObjectKind) -> ObjectSecurityPolicyProjection {
    ObjectSecurityPolicyProjection {
        schema: SECURITY_POLICY_SCHEMA.to_string(),
        object,
        phase: phase_for(object),
        owner_sid: LOCAL_SYSTEM_SID.to_string(),
        group_sid: LOCAL_SYSTEM_SID.to_string(),
        owner_access_mask: owner_access_for(object),
        dacl_protected: true,
        high_integrity_no_write_up: true,
        sddl: sddl_for(object).to_string(),
        actor_access: AuthorityActor::ALL
            .iter()
            .copied()
            .map(|actor| ActorAccessProjection {
                actor,
                principal_sid: actor.principal_sid().to_string(),
                access_mask: actor_access_for(object, actor),
            })
            .collect(),
    }
}

pub(crate) fn project_transition(
    transition: SecurityPolicyTransition,
) -> SecurityTransitionProjection {
    use AuthorityActor::{
        CommittedRuntime, ElevatedFinalizer, ElevatedPreparation, RestrictedWorker,
    };
    use SecurityObjectKind::{
        AuthorityServiceCandidateStart, BinarySealed, BinaryStaging, CandidateActivationNamespace,
        CandidateConsumptionNamespace, CandidateConsumptionStaging, GenerationSealed,
        GenerationStaging, LedgerFinal, LedgerStaging, NonceSealed, RuntimeBlobDirectoryFinal,
        RuntimeBlobDirectoryStaging, RuntimeBlobFile, StableRoot, StateImmutable, StateStaging,
        WorkerNonceNamespace, WorkerNonceStaging,
    };
    use SecurityPolicyTransition::{
        MaterializeRuntimeBlobFile, ProvisionCandidateActivationNamespace,
        ProvisionCandidateConsumptionNamespace, ProvisionCandidateService,
        ProvisionRuntimeBlobDirectory, ProvisionStableRoot, ProvisionWorkerNonceNamespace,
        RetireGeneration, SealBinary, SealCandidateConsumption, SealGeneration, SealLedger,
        SealRuntimeBlobDirectory, SealState, SealWorkerNonce, StageBinary,
        StageCandidateConsumption, StageGeneration, StageLedger, StageState, StageWorkerNonce,
    };

    let (actor, source, destination, create_new_only, held_parent_required) = match transition {
        ProvisionStableRoot => (ElevatedPreparation, None, Some(StableRoot), true, true),
        StageGeneration => (
            ElevatedPreparation,
            Some(StableRoot),
            Some(GenerationStaging),
            true,
            true,
        ),
        SealGeneration => (
            ElevatedFinalizer,
            Some(GenerationStaging),
            Some(GenerationSealed),
            false,
            true,
        ),
        StageBinary => (
            RestrictedWorker,
            Some(GenerationStaging),
            Some(BinaryStaging),
            true,
            true,
        ),
        SealBinary => (
            ElevatedFinalizer,
            Some(BinaryStaging),
            Some(BinarySealed),
            false,
            true,
        ),
        StageState => (
            RestrictedWorker,
            Some(GenerationStaging),
            Some(StateStaging),
            true,
            true,
        ),
        SealState => (
            ElevatedFinalizer,
            Some(StateStaging),
            Some(StateImmutable),
            false,
            true,
        ),
        StageLedger => (
            RestrictedWorker,
            Some(GenerationStaging),
            Some(LedgerStaging),
            true,
            true,
        ),
        SealLedger => (
            ElevatedFinalizer,
            Some(LedgerStaging),
            Some(LedgerFinal),
            false,
            true,
        ),
        ProvisionRuntimeBlobDirectory => (
            ElevatedPreparation,
            Some(GenerationStaging),
            Some(RuntimeBlobDirectoryStaging),
            true,
            true,
        ),
        SealRuntimeBlobDirectory => (
            ElevatedFinalizer,
            Some(RuntimeBlobDirectoryStaging),
            Some(RuntimeBlobDirectoryFinal),
            false,
            true,
        ),
        MaterializeRuntimeBlobFile => (
            CommittedRuntime,
            Some(RuntimeBlobDirectoryFinal),
            Some(RuntimeBlobFile),
            true,
            true,
        ),
        ProvisionWorkerNonceNamespace => (
            ElevatedPreparation,
            Some(StableRoot),
            Some(WorkerNonceNamespace),
            true,
            true,
        ),
        ProvisionCandidateConsumptionNamespace => (
            ElevatedPreparation,
            Some(StableRoot),
            Some(CandidateConsumptionNamespace),
            true,
            true,
        ),
        ProvisionCandidateActivationNamespace => (
            ElevatedPreparation,
            Some(StableRoot),
            Some(CandidateActivationNamespace),
            true,
            true,
        ),
        StageWorkerNonce => (
            RestrictedWorker,
            Some(WorkerNonceNamespace),
            Some(WorkerNonceStaging),
            true,
            true,
        ),
        SealWorkerNonce => (
            ElevatedFinalizer,
            Some(WorkerNonceStaging),
            Some(NonceSealed),
            false,
            true,
        ),
        StageCandidateConsumption => (
            CommittedRuntime,
            Some(CandidateConsumptionNamespace),
            Some(CandidateConsumptionStaging),
            true,
            true,
        ),
        SealCandidateConsumption => (
            ElevatedFinalizer,
            Some(CandidateConsumptionStaging),
            Some(NonceSealed),
            false,
            true,
        ),
        ProvisionCandidateService => (
            ElevatedPreparation,
            None,
            Some(AuthorityServiceCandidateStart),
            true,
            false,
        ),
        RetireGeneration => (ElevatedFinalizer, Some(GenerationSealed), None, false, true),
    };
    SecurityTransitionProjection {
        transition,
        actor,
        source,
        destination,
        create_new_only,
        held_parent_required,
        close_write_handles_before_readback: true,
        exact_readback_required: true,
    }
}

const fn phase_for(object: SecurityObjectKind) -> SecurityPolicyPhase {
    match object {
        SecurityObjectKind::StableRoot => SecurityPolicyPhase::Stable,
        SecurityObjectKind::WorkerNonceNamespace
        | SecurityObjectKind::CandidateConsumptionNamespace
        | SecurityObjectKind::CandidateActivationNamespace => SecurityPolicyPhase::Namespace,
        SecurityObjectKind::GenerationStaging
        | SecurityObjectKind::BinaryStaging
        | SecurityObjectKind::StateStaging
        | SecurityObjectKind::LedgerStaging
        | SecurityObjectKind::WorkerNonceStaging
        | SecurityObjectKind::CandidateConsumptionStaging
        | SecurityObjectKind::RuntimeBlobDirectoryStaging => SecurityPolicyPhase::Staging,
        SecurityObjectKind::GenerationSealed
        | SecurityObjectKind::BinarySealed
        | SecurityObjectKind::NonceSealed => SecurityPolicyPhase::Sealed,
        SecurityObjectKind::StateImmutable => SecurityPolicyPhase::Immutable,
        SecurityObjectKind::LedgerFinal
        | SecurityObjectKind::RuntimeBlobDirectoryFinal
        | SecurityObjectKind::RuntimeBlobFile => SecurityPolicyPhase::Final,
        SecurityObjectKind::AuthorityServiceCandidateStart => SecurityPolicyPhase::CandidateStart,
    }
}

const fn sddl_for(object: SecurityObjectKind) -> &'static str {
    match object {
        SecurityObjectKind::StableRoot => STABLE_ROOT_SDDL,
        SecurityObjectKind::GenerationStaging => GENERATION_STAGING_SDDL,
        SecurityObjectKind::GenerationSealed => GENERATION_SEALED_SDDL,
        SecurityObjectKind::BinaryStaging => BINARY_STAGING_SDDL,
        SecurityObjectKind::BinarySealed => BINARY_SEALED_SDDL,
        SecurityObjectKind::StateStaging => STATE_STAGING_SDDL,
        SecurityObjectKind::StateImmutable => STATE_IMMUTABLE_SDDL,
        SecurityObjectKind::LedgerStaging => LEDGER_STAGING_SDDL,
        SecurityObjectKind::LedgerFinal => LEDGER_FINAL_SDDL,
        SecurityObjectKind::RuntimeBlobDirectoryStaging => RUNTIME_BLOB_DIRECTORY_STAGING_SDDL,
        SecurityObjectKind::RuntimeBlobDirectoryFinal => RUNTIME_BLOB_DIRECTORY_FINAL_SDDL,
        SecurityObjectKind::RuntimeBlobFile => RUNTIME_BLOB_FILE_SDDL,
        SecurityObjectKind::WorkerNonceNamespace => WORKER_NONCE_NAMESPACE_SDDL,
        SecurityObjectKind::CandidateConsumptionNamespace => CANDIDATE_CONSUMPTION_NAMESPACE_SDDL,
        SecurityObjectKind::CandidateActivationNamespace => CANDIDATE_ACTIVATION_NAMESPACE_SDDL,
        SecurityObjectKind::WorkerNonceStaging => WORKER_NONCE_STAGING_SDDL,
        SecurityObjectKind::CandidateConsumptionStaging => CANDIDATE_CONSUMPTION_STAGING_SDDL,
        SecurityObjectKind::NonceSealed => NONCE_SEALED_SDDL,
        SecurityObjectKind::AuthorityServiceCandidateStart => {
            AUTHORITY_SERVICE_CANDIDATE_START_SDDL
        }
    }
}

const fn owner_access_for(object: SecurityObjectKind) -> u32 {
    match object {
        SecurityObjectKind::AuthorityServiceCandidateStart => SYSTEM_SERVICE_ALL_ACCESS,
        _ => SYSTEM_FILE_ALL_ACCESS,
    }
}

const fn actor_access_for(object: SecurityObjectKind, actor: AuthorityActor) -> u32 {
    use AuthorityActor::{
        CommittedRuntime, ElevatedFinalizer, ElevatedPreparation, RestrictedWorker,
    };
    use SecurityObjectKind::{
        AuthorityServiceCandidateStart, BinarySealed, BinaryStaging, CandidateActivationNamespace,
        CandidateConsumptionNamespace, CandidateConsumptionStaging, GenerationSealed,
        GenerationStaging, LedgerFinal, LedgerStaging, NonceSealed, RuntimeBlobDirectoryFinal,
        RuntimeBlobDirectoryStaging, RuntimeBlobFile, StableRoot, StateImmutable, StateStaging,
        WorkerNonceNamespace, WorkerNonceStaging,
    };

    match (object, actor) {
        (StableRoot, ElevatedPreparation | ElevatedFinalizer) => DIRECTORY_CREATE_CHILD_ACCESS,
        (StableRoot, RestrictedWorker | CommittedRuntime) => DIRECTORY_READ_EXECUTE_ACCESS,
        (GenerationStaging, ElevatedPreparation | ElevatedFinalizer) => {
            DIRECTORY_FINALIZER_STAGING_ACCESS
        }
        (GenerationStaging, RestrictedWorker) => DIRECTORY_WORKER_STAGING_ACCESS,
        (GenerationStaging, CommittedRuntime) => DIRECTORY_READ_EXECUTE_ACCESS,
        (GenerationSealed, ElevatedPreparation | ElevatedFinalizer) => DIRECTORY_RETIRE_ACCESS,
        (GenerationSealed, RestrictedWorker | CommittedRuntime) => DIRECTORY_READ_EXECUTE_ACCESS,
        (BinaryStaging, ElevatedPreparation | ElevatedFinalizer) => FILE_FINALIZER_STAGING_ACCESS,
        (BinaryStaging, RestrictedWorker) => FILE_WORKER_STAGING_ACCESS,
        (BinaryStaging, CommittedRuntime) => FILE_READ_EXECUTE_ACCESS,
        (BinarySealed, ElevatedPreparation | ElevatedFinalizer) => FILE_RETIRE_READ_EXECUTE_ACCESS,
        (BinarySealed, RestrictedWorker) => FILE_READ_ACCESS,
        (BinarySealed, CommittedRuntime) => FILE_READ_EXECUTE_ACCESS,
        (StateStaging, ElevatedPreparation | ElevatedFinalizer) => FILE_FINALIZER_STAGING_ACCESS,
        (StateStaging, RestrictedWorker) => FILE_WORKER_STAGING_ACCESS,
        (StateStaging, CommittedRuntime) => FILE_READ_ACCESS,
        (StateImmutable, ElevatedPreparation | ElevatedFinalizer) => FILE_RETIRE_READ_ACCESS,
        (StateImmutable, RestrictedWorker | CommittedRuntime) => FILE_READ_ACCESS,
        (LedgerStaging, ElevatedPreparation | ElevatedFinalizer) => FILE_FINALIZER_STAGING_ACCESS,
        (LedgerStaging, RestrictedWorker) => FILE_WORKER_STAGING_ACCESS,
        (LedgerStaging, CommittedRuntime) => 0,
        (LedgerFinal, ElevatedPreparation | ElevatedFinalizer) => FILE_RETIRE_READ_ACCESS,
        (LedgerFinal, RestrictedWorker) => 0,
        (LedgerFinal, CommittedRuntime) => LEDGER_FINAL_AUTHORITY_ACCESS,
        (RuntimeBlobDirectoryStaging, ElevatedPreparation | ElevatedFinalizer) => {
            DIRECTORY_FINALIZER_STAGING_ACCESS
        }
        (RuntimeBlobDirectoryStaging, RestrictedWorker | CommittedRuntime) => 0,
        (RuntimeBlobDirectoryFinal, ElevatedPreparation | ElevatedFinalizer) => {
            DIRECTORY_RETIRE_ACCESS
        }
        (RuntimeBlobDirectoryFinal, RestrictedWorker) => 0,
        (RuntimeBlobDirectoryFinal, CommittedRuntime) => RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS,
        (RuntimeBlobFile, ElevatedPreparation | ElevatedFinalizer) => FILE_RETIRE_READ_ACCESS,
        (RuntimeBlobFile, RestrictedWorker) => 0,
        (RuntimeBlobFile, CommittedRuntime) => RUNTIME_BLOB_FILE_AUTHORITY_ACCESS,
        (WorkerNonceNamespace, ElevatedPreparation | ElevatedFinalizer) => {
            DIRECTORY_CREATE_FILE_ACCESS
        }
        (WorkerNonceNamespace, RestrictedWorker) => DIRECTORY_CREATE_FILE_ACCESS,
        (WorkerNonceNamespace, CommittedRuntime) => DIRECTORY_READ_EXECUTE_ACCESS,
        (CandidateConsumptionNamespace, ElevatedPreparation | ElevatedFinalizer) => {
            DIRECTORY_CREATE_FILE_ACCESS
        }
        (CandidateConsumptionNamespace, RestrictedWorker) => DIRECTORY_READ_EXECUTE_ACCESS,
        (CandidateConsumptionNamespace, CommittedRuntime) => DIRECTORY_CREATE_FILE_ACCESS,
        (CandidateActivationNamespace, ElevatedPreparation | ElevatedFinalizer) => {
            DIRECTORY_CREATE_FILE_ACCESS
        }
        (CandidateActivationNamespace, RestrictedWorker) => DIRECTORY_CREATE_FILE_ACCESS,
        (CandidateActivationNamespace, CommittedRuntime) => DIRECTORY_READ_EXECUTE_ACCESS,
        (WorkerNonceStaging, ElevatedPreparation | ElevatedFinalizer) => {
            FILE_FINALIZER_STAGING_ACCESS
        }
        (WorkerNonceStaging, RestrictedWorker) => FILE_WORKER_STAGING_ACCESS,
        (WorkerNonceStaging, CommittedRuntime) => FILE_READ_ACCESS,
        (CandidateConsumptionStaging, ElevatedPreparation | ElevatedFinalizer) => {
            FILE_FINALIZER_STAGING_ACCESS
        }
        (CandidateConsumptionStaging, RestrictedWorker) => FILE_READ_ACCESS,
        (CandidateConsumptionStaging, CommittedRuntime) => FILE_WORKER_STAGING_ACCESS,
        (NonceSealed, ElevatedPreparation | ElevatedFinalizer) => FILE_READ_ACCESS,
        (NonceSealed, RestrictedWorker | CommittedRuntime) => FILE_READ_ACCESS,
        (AuthorityServiceCandidateStart, ElevatedPreparation | ElevatedFinalizer) => {
            AUTHORITY_SERVICE_ELEVATED_FINALIZER_ACCESS
        }
        (AuthorityServiceCandidateStart, RestrictedWorker) => {
            AUTHORITY_SERVICE_CANDIDATE_START_ACCESS
        }
        (AuthorityServiceCandidateStart, CommittedRuntime) => 0,
    }
}

fn validate_security_invariants(bundle: &SecurityPolicyBundle) -> Result<(), SecurityPolicyError> {
    let invalid = || SecurityPolicyError("authority_security_policy_invalid");
    if bundle.objects.len() != SecurityObjectKind::ALL.len()
        || bundle.transitions.len() != SecurityPolicyTransition::ALL.len()
    {
        return Err(invalid());
    }

    for policy in &bundle.objects {
        if policy.schema != SECURITY_POLICY_SCHEMA
            || policy.owner_sid != LOCAL_SYSTEM_SID
            || policy.group_sid != LOCAL_SYSTEM_SID
            || !policy.dacl_protected
            || !policy.high_integrity_no_write_up
            || policy.actor_access.len() != AuthorityActor::ALL.len()
            || !policy.sddl.starts_with("O:SYG:SYD:P")
            || !policy.sddl.ends_with("S:(ML;;NW;;;HI)")
                && !policy.sddl.ends_with("S:(ML;OICI;NW;;;HI)")
        {
            return Err(invalid());
        }
        for grant in &policy.actor_access {
            if grant.principal_sid != grant.actor.principal_sid()
                || grant.access_mask
                    & (GENERIC_RIGHTS_ACCESS | ACCESS_SYSTEM_SECURITY_ACCESS | WRITE_OWNER_ACCESS)
                    != 0
            {
                return Err(invalid());
            }
        }
    }

    let root = bundle
        .object_policy(SecurityObjectKind::StableRoot)
        .ok_or_else(invalid)?;
    if AuthorityActor::ALL
        .iter()
        .copied()
        .any(|actor| root.access_for(actor) & (FILE_DELETE_CHILD_ACCESS | WRITE_OWNER_ACCESS) != 0)
        || root.access_for(AuthorityActor::RestrictedWorker) != DIRECTORY_READ_EXECUTE_ACCESS
        || root.access_for(AuthorityActor::CommittedRuntime) != DIRECTORY_READ_EXECUTE_ACCESS
    {
        return Err(invalid());
    }

    for (object, writer, expected_writer, expected_finalizer) in [
        (
            SecurityObjectKind::GenerationStaging,
            AuthorityActor::RestrictedWorker,
            DIRECTORY_WORKER_STAGING_ACCESS,
            DIRECTORY_FINALIZER_STAGING_ACCESS,
        ),
        (
            SecurityObjectKind::BinaryStaging,
            AuthorityActor::RestrictedWorker,
            FILE_WORKER_STAGING_ACCESS,
            FILE_FINALIZER_STAGING_ACCESS,
        ),
        (
            SecurityObjectKind::StateStaging,
            AuthorityActor::RestrictedWorker,
            FILE_WORKER_STAGING_ACCESS,
            FILE_FINALIZER_STAGING_ACCESS,
        ),
        (
            SecurityObjectKind::LedgerStaging,
            AuthorityActor::RestrictedWorker,
            FILE_WORKER_STAGING_ACCESS,
            FILE_FINALIZER_STAGING_ACCESS,
        ),
        (
            SecurityObjectKind::WorkerNonceStaging,
            AuthorityActor::RestrictedWorker,
            FILE_WORKER_STAGING_ACCESS,
            FILE_FINALIZER_STAGING_ACCESS,
        ),
        (
            SecurityObjectKind::CandidateConsumptionStaging,
            AuthorityActor::CommittedRuntime,
            FILE_WORKER_STAGING_ACCESS,
            FILE_FINALIZER_STAGING_ACCESS,
        ),
    ] {
        let policy = bundle.object_policy(object).ok_or_else(invalid)?;
        let writer_access = policy.access_for(writer);
        let finalizer_access = policy.access_for(AuthorityActor::ElevatedFinalizer);
        if writer_access != expected_writer
            || writer_access & DELETE_SELF_ACCESS == 0
            || writer_access & (WRITE_DAC_ACCESS | WRITE_OWNER_ACCESS | FILE_DELETE_CHILD_ACCESS)
                != 0
            || finalizer_access != expected_finalizer
            || finalizer_access & (DELETE_SELF_ACCESS | WRITE_DAC_ACCESS)
                != DELETE_SELF_ACCESS | WRITE_DAC_ACCESS
            || finalizer_access & (WRITE_OWNER_ACCESS | FILE_DELETE_CHILD_ACCESS) != 0
        {
            return Err(invalid());
        }
    }

    let worker_nonce_namespace = bundle
        .object_policy(SecurityObjectKind::WorkerNonceNamespace)
        .ok_or_else(invalid)?;
    let candidate_consumption_namespace = bundle
        .object_policy(SecurityObjectKind::CandidateConsumptionNamespace)
        .ok_or_else(invalid)?;
    if worker_nonce_namespace.access_for(AuthorityActor::RestrictedWorker)
        != DIRECTORY_CREATE_FILE_ACCESS
        || worker_nonce_namespace.access_for(AuthorityActor::CommittedRuntime)
            != DIRECTORY_READ_EXECUTE_ACCESS
        || candidate_consumption_namespace.access_for(AuthorityActor::RestrictedWorker)
            != DIRECTORY_READ_EXECUTE_ACCESS
        || candidate_consumption_namespace.access_for(AuthorityActor::CommittedRuntime)
            != DIRECTORY_CREATE_FILE_ACCESS
        || DIRECTORY_CREATE_FILE_ACCESS
            & (FILE_APPEND_DATA_ACCESS
                | FILE_DELETE_CHILD_ACCESS
                | WRITE_DAC_ACCESS
                | WRITE_OWNER_ACCESS)
            != 0
        || bundle
            .object_policy(SecurityObjectKind::WorkerNonceStaging)
            .ok_or_else(invalid)?
            .access_for(AuthorityActor::RestrictedWorker)
            != FILE_WORKER_STAGING_ACCESS
        || bundle
            .object_policy(SecurityObjectKind::WorkerNonceStaging)
            .ok_or_else(invalid)?
            .access_for(AuthorityActor::CommittedRuntime)
            != FILE_READ_ACCESS
        || bundle
            .object_policy(SecurityObjectKind::CandidateConsumptionStaging)
            .ok_or_else(invalid)?
            .access_for(AuthorityActor::RestrictedWorker)
            != FILE_READ_ACCESS
        || bundle
            .object_policy(SecurityObjectKind::CandidateConsumptionStaging)
            .ok_or_else(invalid)?
            .access_for(AuthorityActor::CommittedRuntime)
            != FILE_WORKER_STAGING_ACCESS
    {
        return Err(invalid());
    }

    let candidate_namespace = bundle
        .object_policy(SecurityObjectKind::CandidateActivationNamespace)
        .ok_or_else(invalid)?;
    if candidate_namespace.access_for(AuthorityActor::RestrictedWorker)
        != DIRECTORY_CREATE_FILE_ACCESS
        || candidate_namespace.access_for(AuthorityActor::CommittedRuntime)
            != DIRECTORY_READ_EXECUTE_ACCESS
        || DIRECTORY_CREATE_FILE_ACCESS
            & (FILE_APPEND_DATA_ACCESS
                | FILE_DELETE_CHILD_ACCESS
                | WRITE_DAC_ACCESS
                | WRITE_OWNER_ACCESS)
            != 0
    {
        return Err(invalid());
    }

    let sealed_worker_forbidden = FILE_WRITE_DATA_ACCESS
        | FILE_APPEND_DATA_ACCESS
        | FILE_WRITE_EA_ACCESS
        | FILE_DELETE_CHILD_ACCESS
        | FILE_WRITE_ATTRIBUTES_ACCESS
        | DELETE_SELF_ACCESS
        | WRITE_DAC_ACCESS
        | WRITE_OWNER_ACCESS;
    for object in [
        SecurityObjectKind::GenerationSealed,
        SecurityObjectKind::BinarySealed,
        SecurityObjectKind::StateImmutable,
        SecurityObjectKind::LedgerFinal,
        SecurityObjectKind::RuntimeBlobDirectoryFinal,
        SecurityObjectKind::RuntimeBlobFile,
        SecurityObjectKind::NonceSealed,
    ] {
        if bundle
            .object_policy(object)
            .ok_or_else(invalid)?
            .access_for(AuthorityActor::RestrictedWorker)
            & sealed_worker_forbidden
            != 0
        {
            return Err(invalid());
        }
    }

    let ledger = bundle
        .object_policy(SecurityObjectKind::LedgerFinal)
        .ok_or_else(invalid)?;
    if ledger.access_for(AuthorityActor::CommittedRuntime) != LEDGER_FINAL_AUTHORITY_ACCESS
        || ledger.access_for(AuthorityActor::RestrictedWorker) != 0
        || ledger.access_for(AuthorityActor::CommittedRuntime)
            & (DELETE_SELF_ACCESS | WRITE_DAC_ACCESS | WRITE_OWNER_ACCESS)
            != 0
    {
        return Err(invalid());
    }

    let blob_staging = bundle
        .object_policy(SecurityObjectKind::RuntimeBlobDirectoryStaging)
        .ok_or_else(invalid)?;
    let blob_root = bundle
        .object_policy(SecurityObjectKind::RuntimeBlobDirectoryFinal)
        .ok_or_else(invalid)?;
    let blob_file = bundle
        .object_policy(SecurityObjectKind::RuntimeBlobFile)
        .ok_or_else(invalid)?;
    let blob_root_forbidden = FILE_APPEND_DATA_ACCESS
        | FILE_DELETE_CHILD_ACCESS
        | FILE_WRITE_EA_ACCESS
        | FILE_WRITE_ATTRIBUTES_ACCESS
        | DELETE_SELF_ACCESS
        | WRITE_DAC_ACCESS
        | WRITE_OWNER_ACCESS
        | ACCESS_SYSTEM_SECURITY_ACCESS
        | GENERIC_RIGHTS_ACCESS;
    let blob_file_forbidden = FILE_APPEND_DATA_ACCESS
        | FILE_WRITE_EA_ACCESS
        | FILE_WRITE_ATTRIBUTES_ACCESS
        | WRITE_DAC_ACCESS
        | WRITE_OWNER_ACCESS
        | ACCESS_SYSTEM_SECURITY_ACCESS
        | GENERIC_RIGHTS_ACCESS;
    if blob_staging.access_for(AuthorityActor::ElevatedPreparation)
        != DIRECTORY_FINALIZER_STAGING_ACCESS
        || blob_staging.access_for(AuthorityActor::ElevatedFinalizer)
            != DIRECTORY_FINALIZER_STAGING_ACCESS
        || blob_staging.access_for(AuthorityActor::RestrictedWorker) != 0
        || blob_staging.access_for(AuthorityActor::CommittedRuntime) != 0
        || blob_root.access_for(AuthorityActor::CommittedRuntime)
            != RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS
        || blob_root.access_for(AuthorityActor::RestrictedWorker) != 0
        || blob_root.access_for(AuthorityActor::CommittedRuntime) & blob_root_forbidden != 0
        || blob_file.access_for(AuthorityActor::CommittedRuntime)
            != RUNTIME_BLOB_FILE_AUTHORITY_ACCESS
        || blob_file.access_for(AuthorityActor::RestrictedWorker) != 0
        || blob_file.access_for(AuthorityActor::CommittedRuntime) & blob_file_forbidden != 0
        || blob_file.access_for(AuthorityActor::CommittedRuntime) & DELETE_SELF_ACCESS == 0
        || RUNTIME_BLOB_FILE_READ_ACCESS & blob_file_forbidden != 0
        || RUNTIME_BLOB_FILE_CLEANUP_ACCESS & DELETE_SELF_ACCESS == 0
    {
        return Err(invalid());
    }

    let nonce = bundle
        .object_policy(SecurityObjectKind::NonceSealed)
        .ok_or_else(invalid)?;
    if nonce.access_for(AuthorityActor::RestrictedWorker) != FILE_READ_ACCESS
        || nonce.access_for(AuthorityActor::CommittedRuntime) != FILE_READ_ACCESS
    {
        return Err(invalid());
    }

    let candidate = bundle
        .object_policy(SecurityObjectKind::AuthorityServiceCandidateStart)
        .ok_or_else(invalid)?;
    if candidate.access_for(AuthorityActor::RestrictedWorker)
        != AUTHORITY_SERVICE_CANDIDATE_START_ACCESS
        || AUTHORITY_SERVICE_CANDIDATE_START_ACCESS
            & (SERVICE_CHANGE_CONFIG_ACCESS
                | SERVICE_STOP_ACCESS
                | DELETE_SELF_ACCESS
                | WRITE_DAC_ACCESS
                | WRITE_OWNER_ACCESS)
            != 0
    {
        return Err(invalid());
    }

    for transition in &bundle.transitions {
        if !transition.close_write_handles_before_readback || !transition.exact_readback_required {
            return Err(invalid());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const SEALED_MUTATION_ACCESS: u32 = FILE_WRITE_DATA_ACCESS
        | FILE_APPEND_DATA_ACCESS
        | FILE_WRITE_EA_ACCESS
        | FILE_DELETE_CHILD_ACCESS
        | FILE_WRITE_ATTRIBUTES_ACCESS
        | DELETE_SELF_ACCESS
        | WRITE_DAC_ACCESS
        | WRITE_OWNER_ACCESS;

    fn policy(
        bundle: &SecurityPolicyBundle,
        object: SecurityObjectKind,
    ) -> &ObjectSecurityPolicyProjection {
        bundle.object_policy(object).unwrap()
    }

    #[test]
    fn exact_bundle_is_canonical_and_round_trips() {
        let bundle = SecurityPolicyBundle::exact();
        let bytes = bundle.canonical_bytes().unwrap();
        assert_eq!(
            SecurityPolicyBundle::parse_canonical(&bytes).unwrap(),
            bundle
        );
        assert!(SecurityPolicyBundle::parse_canonical(b"").is_err());
        assert_eq!(
            SecurityPolicyBundle::parse_canonical(&vec![b'x'; MAX_POLICY_BYTES + 1])
                .unwrap_err()
                .code(),
            "authority_security_policy_size_invalid"
        );
    }

    #[test]
    fn every_object_has_the_exact_sddl_phase_and_actor_projection() {
        let bundle = SecurityPolicyBundle::exact();
        let expected = [
            (
                SecurityObjectKind::StableRoot,
                SecurityPolicyPhase::Stable,
                STABLE_ROOT_SDDL,
            ),
            (
                SecurityObjectKind::GenerationStaging,
                SecurityPolicyPhase::Staging,
                GENERATION_STAGING_SDDL,
            ),
            (
                SecurityObjectKind::GenerationSealed,
                SecurityPolicyPhase::Sealed,
                GENERATION_SEALED_SDDL,
            ),
            (
                SecurityObjectKind::BinaryStaging,
                SecurityPolicyPhase::Staging,
                BINARY_STAGING_SDDL,
            ),
            (
                SecurityObjectKind::BinarySealed,
                SecurityPolicyPhase::Sealed,
                BINARY_SEALED_SDDL,
            ),
            (
                SecurityObjectKind::StateStaging,
                SecurityPolicyPhase::Staging,
                STATE_STAGING_SDDL,
            ),
            (
                SecurityObjectKind::StateImmutable,
                SecurityPolicyPhase::Immutable,
                STATE_IMMUTABLE_SDDL,
            ),
            (
                SecurityObjectKind::LedgerStaging,
                SecurityPolicyPhase::Staging,
                LEDGER_STAGING_SDDL,
            ),
            (
                SecurityObjectKind::LedgerFinal,
                SecurityPolicyPhase::Final,
                LEDGER_FINAL_SDDL,
            ),
            (
                SecurityObjectKind::RuntimeBlobDirectoryStaging,
                SecurityPolicyPhase::Staging,
                RUNTIME_BLOB_DIRECTORY_STAGING_SDDL,
            ),
            (
                SecurityObjectKind::RuntimeBlobDirectoryFinal,
                SecurityPolicyPhase::Final,
                RUNTIME_BLOB_DIRECTORY_FINAL_SDDL,
            ),
            (
                SecurityObjectKind::RuntimeBlobFile,
                SecurityPolicyPhase::Final,
                RUNTIME_BLOB_FILE_SDDL,
            ),
            (
                SecurityObjectKind::WorkerNonceNamespace,
                SecurityPolicyPhase::Namespace,
                WORKER_NONCE_NAMESPACE_SDDL,
            ),
            (
                SecurityObjectKind::CandidateConsumptionNamespace,
                SecurityPolicyPhase::Namespace,
                CANDIDATE_CONSUMPTION_NAMESPACE_SDDL,
            ),
            (
                SecurityObjectKind::CandidateActivationNamespace,
                SecurityPolicyPhase::Namespace,
                CANDIDATE_ACTIVATION_NAMESPACE_SDDL,
            ),
            (
                SecurityObjectKind::WorkerNonceStaging,
                SecurityPolicyPhase::Staging,
                WORKER_NONCE_STAGING_SDDL,
            ),
            (
                SecurityObjectKind::CandidateConsumptionStaging,
                SecurityPolicyPhase::Staging,
                CANDIDATE_CONSUMPTION_STAGING_SDDL,
            ),
            (
                SecurityObjectKind::NonceSealed,
                SecurityPolicyPhase::Sealed,
                NONCE_SEALED_SDDL,
            ),
            (
                SecurityObjectKind::AuthorityServiceCandidateStart,
                SecurityPolicyPhase::CandidateStart,
                AUTHORITY_SERVICE_CANDIDATE_START_SDDL,
            ),
        ];
        assert_eq!(bundle.objects().len(), expected.len());
        for (object, phase, sddl) in expected {
            let projected = policy(&bundle, object);
            assert_eq!(projected.object(), object);
            assert_eq!(projected.phase(), phase);
            assert_eq!(projected.sddl(), sddl);
            assert_eq!(projected.actor_access().len(), AuthorityActor::ALL.len());
            for actor in AuthorityActor::ALL {
                let grant = projected
                    .actor_access()
                    .iter()
                    .find(|grant| grant.actor() == actor)
                    .unwrap();
                assert_eq!(grant.principal_sid(), actor.principal_sid());
                assert_eq!(grant.access_mask(), projected.access_for(actor));
            }
        }
    }

    #[test]
    fn staging_writer_can_write_and_delete_self_but_only_finalizer_can_seal() {
        let bundle = SecurityPolicyBundle::exact();
        let expected = [
            (
                SecurityObjectKind::GenerationStaging,
                AuthorityActor::RestrictedWorker,
                DIRECTORY_WORKER_STAGING_ACCESS,
                DIRECTORY_FINALIZER_STAGING_ACCESS,
                FILE_WRITE_DATA_ACCESS | FILE_APPEND_DATA_ACCESS,
            ),
            (
                SecurityObjectKind::BinaryStaging,
                AuthorityActor::RestrictedWorker,
                FILE_WORKER_STAGING_ACCESS,
                FILE_FINALIZER_STAGING_ACCESS,
                FILE_WRITE_DATA_ACCESS | FILE_APPEND_DATA_ACCESS,
            ),
            (
                SecurityObjectKind::StateStaging,
                AuthorityActor::RestrictedWorker,
                FILE_WORKER_STAGING_ACCESS,
                FILE_FINALIZER_STAGING_ACCESS,
                FILE_WRITE_DATA_ACCESS | FILE_APPEND_DATA_ACCESS,
            ),
            (
                SecurityObjectKind::LedgerStaging,
                AuthorityActor::RestrictedWorker,
                FILE_WORKER_STAGING_ACCESS,
                FILE_FINALIZER_STAGING_ACCESS,
                FILE_WRITE_DATA_ACCESS | FILE_APPEND_DATA_ACCESS,
            ),
            (
                SecurityObjectKind::WorkerNonceStaging,
                AuthorityActor::RestrictedWorker,
                FILE_WORKER_STAGING_ACCESS,
                FILE_FINALIZER_STAGING_ACCESS,
                FILE_WRITE_DATA_ACCESS | FILE_APPEND_DATA_ACCESS,
            ),
            (
                SecurityObjectKind::CandidateConsumptionStaging,
                AuthorityActor::CommittedRuntime,
                FILE_WORKER_STAGING_ACCESS,
                FILE_FINALIZER_STAGING_ACCESS,
                FILE_WRITE_DATA_ACCESS | FILE_APPEND_DATA_ACCESS,
            ),
        ];
        for (object, writer, writer_exact, finalizer_exact, write_bits) in expected {
            let projected = policy(&bundle, object);
            let writer_access = projected.access_for(writer);
            let finalizer_access = projected.access_for(AuthorityActor::ElevatedFinalizer);
            assert_eq!(writer_access, writer_exact);
            assert_eq!(writer_access & write_bits, write_bits);
            assert_ne!(writer_access & DELETE_SELF_ACCESS, 0);
            assert_eq!(
                writer_access & (WRITE_DAC_ACCESS | WRITE_OWNER_ACCESS | FILE_DELETE_CHILD_ACCESS),
                0
            );
            assert_eq!(finalizer_access, finalizer_exact);
            assert_eq!(
                finalizer_access & (DELETE_SELF_ACCESS | WRITE_DAC_ACCESS),
                DELETE_SELF_ACCESS | WRITE_DAC_ACCESS
            );
            assert_eq!(
                finalizer_access & (WRITE_OWNER_ACCESS | FILE_DELETE_CHILD_ACCESS),
                0
            );
        }
    }

    #[test]
    fn stable_parent_never_grants_delete_child_or_acl_takeover() {
        let bundle = SecurityPolicyBundle::exact();
        let root = policy(&bundle, SecurityObjectKind::StableRoot);
        for actor in AuthorityActor::ALL {
            assert_eq!(
                root.access_for(actor)
                    & (FILE_DELETE_CHILD_ACCESS | WRITE_DAC_ACCESS | WRITE_OWNER_ACCESS),
                0
            );
        }
        assert_eq!(
            root.access_for(AuthorityActor::RestrictedWorker),
            DIRECTORY_READ_EXECUTE_ACCESS
        );
    }

    #[test]
    fn sealed_worker_access_is_read_only_and_cannot_rewrite_old_generations() {
        let bundle = SecurityPolicyBundle::exact();
        let expected = [
            (
                SecurityObjectKind::GenerationSealed,
                DIRECTORY_READ_EXECUTE_ACCESS,
            ),
            (SecurityObjectKind::BinarySealed, FILE_READ_ACCESS),
            (SecurityObjectKind::StateImmutable, FILE_READ_ACCESS),
            (SecurityObjectKind::LedgerFinal, 0),
            (SecurityObjectKind::NonceSealed, FILE_READ_ACCESS),
        ];
        for (object, exact) in expected {
            let access = policy(&bundle, object).access_for(AuthorityActor::RestrictedWorker);
            assert_eq!(access, exact);
            assert_eq!(access & SEALED_MUTATION_ACCESS, 0);
        }
    }

    #[test]
    fn final_ledger_has_one_narrow_writer_and_no_delete_or_acl_takeover() {
        let bundle = SecurityPolicyBundle::exact();
        let ledger = policy(&bundle, SecurityObjectKind::LedgerFinal);
        assert_eq!(
            ledger.access_for(AuthorityActor::CommittedRuntime),
            LEDGER_FINAL_AUTHORITY_ACCESS
        );
        assert_eq!(
            LEDGER_FINAL_AUTHORITY_ACCESS
                & (FILE_READ_DATA_ACCESS | FILE_WRITE_DATA_ACCESS | FILE_APPEND_DATA_ACCESS),
            FILE_READ_DATA_ACCESS | FILE_WRITE_DATA_ACCESS | FILE_APPEND_DATA_ACCESS
        );
        assert_eq!(
            LEDGER_FINAL_AUTHORITY_ACCESS
                & (DELETE_SELF_ACCESS | WRITE_DAC_ACCESS | WRITE_OWNER_ACCESS),
            0
        );
        assert_eq!(ledger.access_for(AuthorityActor::RestrictedWorker), 0);
        assert_eq!(
            policy(&bundle, SecurityObjectKind::GenerationSealed)
                .access_for(AuthorityActor::CommittedRuntime)
                & (FILE_WRITE_DATA_ACCESS | FILE_APPEND_DATA_ACCESS),
            0
        );
    }

    #[test]
    fn prebuilt_nonce_namespaces_allow_only_their_exact_writer_to_create_files() {
        let bundle = SecurityPolicyBundle::exact();
        let worker_namespace = policy(&bundle, SecurityObjectKind::WorkerNonceNamespace);
        assert_eq!(
            worker_namespace.access_for(AuthorityActor::RestrictedWorker),
            DIRECTORY_CREATE_FILE_ACCESS
        );
        assert_eq!(
            worker_namespace.access_for(AuthorityActor::CommittedRuntime),
            DIRECTORY_READ_EXECUTE_ACCESS
        );
        let candidate_namespace =
            policy(&bundle, SecurityObjectKind::CandidateConsumptionNamespace);
        assert_eq!(
            candidate_namespace.access_for(AuthorityActor::RestrictedWorker),
            DIRECTORY_READ_EXECUTE_ACCESS
        );
        assert_eq!(
            candidate_namespace.access_for(AuthorityActor::CommittedRuntime),
            DIRECTORY_CREATE_FILE_ACCESS
        );
        assert_ne!(DIRECTORY_CREATE_FILE_ACCESS & FILE_WRITE_DATA_ACCESS, 0);
        assert_eq!(
            DIRECTORY_CREATE_FILE_ACCESS
                & (FILE_APPEND_DATA_ACCESS
                    | FILE_DELETE_CHILD_ACCESS
                    | WRITE_DAC_ACCESS
                    | WRITE_OWNER_ACCESS),
            0
        );
    }

    #[test]
    fn prebuilt_candidate_namespace_separates_publisher_from_reader() {
        let bundle = SecurityPolicyBundle::exact();
        let namespace = policy(&bundle, SecurityObjectKind::CandidateActivationNamespace);
        assert_eq!(
            namespace.access_for(AuthorityActor::RestrictedWorker),
            DIRECTORY_CREATE_FILE_ACCESS
        );
        assert_eq!(
            namespace.access_for(AuthorityActor::CommittedRuntime),
            DIRECTORY_READ_EXECUTE_ACCESS
        );
        assert_eq!(
            DIRECTORY_CREATE_FILE_ACCESS
                & (FILE_APPEND_DATA_ACCESS
                    | FILE_DELETE_CHILD_ACCESS
                    | WRITE_DAC_ACCESS
                    | WRITE_OWNER_ACCESS),
            0
        );
    }

    #[test]
    fn sealed_nonce_is_read_only_for_both_service_identities() {
        let bundle = SecurityPolicyBundle::exact();
        let nonce = policy(&bundle, SecurityObjectKind::NonceSealed);
        assert_eq!(
            nonce.access_for(AuthorityActor::RestrictedWorker),
            FILE_READ_ACCESS
        );
        assert_eq!(
            nonce.access_for(AuthorityActor::CommittedRuntime),
            FILE_READ_ACCESS
        );
        assert_eq!(FILE_READ_ACCESS & SEALED_MUTATION_ACCESS, 0);
        assert!(nonce.sddl().contains(MAINTENANCE_SERVICE_SID));
        assert!(nonce.sddl().contains(AUTHORITY_SERVICE_SID));
    }

    #[test]
    fn candidate_service_grants_only_query_and_start_to_the_worker() {
        let bundle = SecurityPolicyBundle::exact();
        let service = policy(&bundle, SecurityObjectKind::AuthorityServiceCandidateStart);
        let access = service.access_for(AuthorityActor::RestrictedWorker);
        assert_eq!(access, AUTHORITY_SERVICE_CANDIDATE_START_ACCESS);
        assert_eq!(
            access,
            READ_CONTROL_ACCESS
                | SERVICE_QUERY_CONFIG_ACCESS
                | SERVICE_QUERY_STATUS_ACCESS
                | SERVICE_START_ACCESS
        );
        assert_eq!(
            access
                & (SERVICE_CHANGE_CONFIG_ACCESS
                    | SERVICE_STOP_ACCESS
                    | DELETE_SELF_ACCESS
                    | WRITE_DAC_ACCESS
                    | WRITE_OWNER_ACCESS),
            0
        );
    }

    #[test]
    fn transition_matrix_has_one_exact_actor_and_fails_closed_for_every_other_actor() {
        let bundle = SecurityPolicyBundle::exact();
        let expected = [
            (
                SecurityPolicyTransition::ProvisionStableRoot,
                AuthorityActor::ElevatedPreparation,
                None,
                Some(SecurityObjectKind::StableRoot),
            ),
            (
                SecurityPolicyTransition::StageGeneration,
                AuthorityActor::ElevatedPreparation,
                Some(SecurityObjectKind::StableRoot),
                Some(SecurityObjectKind::GenerationStaging),
            ),
            (
                SecurityPolicyTransition::SealGeneration,
                AuthorityActor::ElevatedFinalizer,
                Some(SecurityObjectKind::GenerationStaging),
                Some(SecurityObjectKind::GenerationSealed),
            ),
            (
                SecurityPolicyTransition::StageBinary,
                AuthorityActor::RestrictedWorker,
                Some(SecurityObjectKind::GenerationStaging),
                Some(SecurityObjectKind::BinaryStaging),
            ),
            (
                SecurityPolicyTransition::SealBinary,
                AuthorityActor::ElevatedFinalizer,
                Some(SecurityObjectKind::BinaryStaging),
                Some(SecurityObjectKind::BinarySealed),
            ),
            (
                SecurityPolicyTransition::StageState,
                AuthorityActor::RestrictedWorker,
                Some(SecurityObjectKind::GenerationStaging),
                Some(SecurityObjectKind::StateStaging),
            ),
            (
                SecurityPolicyTransition::SealState,
                AuthorityActor::ElevatedFinalizer,
                Some(SecurityObjectKind::StateStaging),
                Some(SecurityObjectKind::StateImmutable),
            ),
            (
                SecurityPolicyTransition::StageLedger,
                AuthorityActor::RestrictedWorker,
                Some(SecurityObjectKind::GenerationStaging),
                Some(SecurityObjectKind::LedgerStaging),
            ),
            (
                SecurityPolicyTransition::SealLedger,
                AuthorityActor::ElevatedFinalizer,
                Some(SecurityObjectKind::LedgerStaging),
                Some(SecurityObjectKind::LedgerFinal),
            ),
            (
                SecurityPolicyTransition::ProvisionRuntimeBlobDirectory,
                AuthorityActor::ElevatedPreparation,
                Some(SecurityObjectKind::GenerationStaging),
                Some(SecurityObjectKind::RuntimeBlobDirectoryStaging),
            ),
            (
                SecurityPolicyTransition::SealRuntimeBlobDirectory,
                AuthorityActor::ElevatedFinalizer,
                Some(SecurityObjectKind::RuntimeBlobDirectoryStaging),
                Some(SecurityObjectKind::RuntimeBlobDirectoryFinal),
            ),
            (
                SecurityPolicyTransition::MaterializeRuntimeBlobFile,
                AuthorityActor::CommittedRuntime,
                Some(SecurityObjectKind::RuntimeBlobDirectoryFinal),
                Some(SecurityObjectKind::RuntimeBlobFile),
            ),
            (
                SecurityPolicyTransition::ProvisionWorkerNonceNamespace,
                AuthorityActor::ElevatedPreparation,
                Some(SecurityObjectKind::StableRoot),
                Some(SecurityObjectKind::WorkerNonceNamespace),
            ),
            (
                SecurityPolicyTransition::ProvisionCandidateConsumptionNamespace,
                AuthorityActor::ElevatedPreparation,
                Some(SecurityObjectKind::StableRoot),
                Some(SecurityObjectKind::CandidateConsumptionNamespace),
            ),
            (
                SecurityPolicyTransition::ProvisionCandidateActivationNamespace,
                AuthorityActor::ElevatedPreparation,
                Some(SecurityObjectKind::StableRoot),
                Some(SecurityObjectKind::CandidateActivationNamespace),
            ),
            (
                SecurityPolicyTransition::StageWorkerNonce,
                AuthorityActor::RestrictedWorker,
                Some(SecurityObjectKind::WorkerNonceNamespace),
                Some(SecurityObjectKind::WorkerNonceStaging),
            ),
            (
                SecurityPolicyTransition::SealWorkerNonce,
                AuthorityActor::ElevatedFinalizer,
                Some(SecurityObjectKind::WorkerNonceStaging),
                Some(SecurityObjectKind::NonceSealed),
            ),
            (
                SecurityPolicyTransition::StageCandidateConsumption,
                AuthorityActor::CommittedRuntime,
                Some(SecurityObjectKind::CandidateConsumptionNamespace),
                Some(SecurityObjectKind::CandidateConsumptionStaging),
            ),
            (
                SecurityPolicyTransition::SealCandidateConsumption,
                AuthorityActor::ElevatedFinalizer,
                Some(SecurityObjectKind::CandidateConsumptionStaging),
                Some(SecurityObjectKind::NonceSealed),
            ),
            (
                SecurityPolicyTransition::ProvisionCandidateService,
                AuthorityActor::ElevatedPreparation,
                None,
                Some(SecurityObjectKind::AuthorityServiceCandidateStart),
            ),
            (
                SecurityPolicyTransition::RetireGeneration,
                AuthorityActor::ElevatedFinalizer,
                Some(SecurityObjectKind::GenerationSealed),
                None,
            ),
        ];
        assert_eq!(bundle.transitions().len(), expected.len());
        for (transition, actor, source, destination) in expected {
            let projected = bundle.transition_policy(transition).unwrap();
            assert_eq!(projected.transition(), transition);
            assert_eq!(projected.actor(), actor);
            assert_eq!(projected.source(), source);
            assert_eq!(projected.destination(), destination);
            assert_eq!(
                projected.create_new_only,
                matches!(
                    transition,
                    SecurityPolicyTransition::ProvisionStableRoot
                        | SecurityPolicyTransition::StageGeneration
                        | SecurityPolicyTransition::StageBinary
                        | SecurityPolicyTransition::StageState
                        | SecurityPolicyTransition::StageLedger
                        | SecurityPolicyTransition::ProvisionRuntimeBlobDirectory
                        | SecurityPolicyTransition::MaterializeRuntimeBlobFile
                        | SecurityPolicyTransition::ProvisionWorkerNonceNamespace
                        | SecurityPolicyTransition::ProvisionCandidateConsumptionNamespace
                        | SecurityPolicyTransition::ProvisionCandidateActivationNamespace
                        | SecurityPolicyTransition::StageWorkerNonce
                        | SecurityPolicyTransition::StageCandidateConsumption
                        | SecurityPolicyTransition::ProvisionCandidateService
                )
            );
            assert_eq!(
                projected.held_parent_required,
                transition != SecurityPolicyTransition::ProvisionCandidateService
            );
            assert!(projected.close_write_handles_before_readback);
            assert!(projected.exact_readback_required);
            for candidate in AuthorityActor::ALL {
                assert_eq!(projected.permits(candidate), candidate == actor);
            }
        }
    }

    #[test]
    fn every_actor_mask_is_expanded_and_contains_no_generic_or_owner_takeover_right() {
        let bundle = SecurityPolicyBundle::exact();
        for policy in bundle.objects() {
            for grant in policy.actor_access() {
                assert_eq!(grant.access_mask() & GENERIC_RIGHTS_ACCESS, 0);
                assert_eq!(grant.access_mask() & ACCESS_SYSTEM_SECURITY_ACCESS, 0);
                assert_eq!(grant.access_mask() & WRITE_OWNER_ACCESS, 0);
            }
            for broad_principal in [";;;WD)", ";;;AN)", ";;;AU)", ";;;BU)", ";;;AC)"] {
                assert!(!policy.sddl().contains(broad_principal));
            }
            for generic_right in [";;GA;;;", ";;GR;;;", ";;GW;;;", ";;GX;;;"] {
                assert!(!policy.sddl().contains(generic_right));
            }
        }
        assert_eq!(SYNCHRONIZE_ACCESS, 0x0010_0000);
        assert_eq!(FILE_READ_EA_ACCESS, 0x0000_0008);
        assert_eq!(FILE_EXECUTE_ACCESS, 0x0000_0020);
        assert_eq!(FILE_READ_ATTRIBUTES_ACCESS, 0x0000_0080);
    }

    #[test]
    fn exact_sddl_aces_match_the_typed_actor_masks() {
        let bundle = SecurityPolicyBundle::exact();
        for projected in bundle.objects() {
            let directory = matches!(
                projected.object(),
                SecurityObjectKind::StableRoot
                    | SecurityObjectKind::GenerationStaging
                    | SecurityObjectKind::GenerationSealed
                    | SecurityObjectKind::WorkerNonceNamespace
                    | SecurityObjectKind::CandidateConsumptionNamespace
                    | SecurityObjectKind::CandidateActivationNamespace
            );
            let flags = if directory { "OICI" } else { "" };
            let owner_mask =
                if projected.object() == SecurityObjectKind::AuthorityServiceCandidateStart {
                    SYSTEM_SERVICE_ALL_ACCESS
                } else {
                    SYSTEM_FILE_ALL_ACCESS
                };
            assert!(projected
                .sddl()
                .contains(&format!("(A;{flags};0x{owner_mask:08x};;;SY)")));

            for actor in [
                AuthorityActor::ElevatedPreparation,
                AuthorityActor::RestrictedWorker,
                AuthorityActor::CommittedRuntime,
            ] {
                let mask = projected.access_for(actor);
                let principal = match actor {
                    AuthorityActor::ElevatedPreparation => "BA",
                    _ => actor.principal_sid(),
                };
                let ace = format!("(A;{flags};0x{mask:08x};;;{principal})");
                if mask == 0 {
                    assert!(!projected.sddl().contains(&format!(";;;{principal})")));
                } else {
                    assert!(projected.sddl().contains(&ace), "missing {ace}");
                    assert_eq!(projected.sddl().matches(&ace).count(), 1);
                }
            }
        }
    }

    #[test]
    fn serialized_policy_tampering_is_rejected() {
        let bytes = SecurityPolicyBundle::exact().canonical_bytes().unwrap();
        let canonical = String::from_utf8(bytes).unwrap();
        let tampered = canonical
            .replacen("\"accessMask\":1179823", "\"accessMask\":4294967295", 1)
            .into_bytes();
        assert_eq!(
            SecurityPolicyBundle::parse_canonical(&tampered)
                .unwrap_err()
                .code(),
            "authority_security_policy_invalid"
        );
    }
}
