//! Closed production policy construction for the fixed protected runtime.
//!
//! The public service layer supplies capabilities and held handles. It cannot
//! supply paths, process identifiers, digests, role maps, or serialized policy
//! bytes. This module performs every projection into `SupervisorPolicy`.

use super::{
    canonical_supervisor_policy_digest, derive_run_binding_digest, role_index, validate_policy,
    ArtifactDirection, ArtifactExpectation, Digest, HelperProcessPolicy, PreparedRun, ProcessKey,
    ProcessRole, SocketEndpointMode, SocketPolicy, SocketRole, SupervisorError, SupervisorPolicy,
    VerifiedReadinessProof, APP_LOOPBACK_PORT, PROCESS_ROLES,
};
use crate::primitive_basis_protected_evidence_bundle::{
    FixedAuthorityBinding, FixedPackageBinding, PreparedProtectedEvidenceSource,
    PROTECTED_EVIDENCE_POLICY_ID,
};
use crate::primitive_evidence_authority_install::bootstrap::{
    AuthenticatedFinalCommitPolicyBinding, AuthenticatedGenerationBindingReadback,
    AuthenticatedRuntimeSourceCapability,
};
use crate::primitive_evidence_authority_runtime::{AuthorityRuntimeIdentity, RuntimeTicketRef};
use crate::primitive_evidence_child_protocol::{
    child_role_capability_schema, ChildBootstrapRole, CHILD_STANDARD_HANDLE_SLOT_COUNT,
    GLOBAL_CAPABILITY_SOURCE_COUNT, GLOBAL_CAPABILITY_SOURCE_ROLES,
};
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};
use sha2::{Digest as Sha2Digest, Sha256};
use std::{
    collections::BTreeSet,
    fmt,
    fs::File,
    os::windows::{
        fs::FileExt,
        io::{AsRawHandle, BorrowedHandle},
    },
    time::{SystemTime, UNIX_EPOCH},
};
use windows_sys::Win32::{
    Foundation::FILETIME,
    Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION, FILE_ATTRIBUTE_DIRECTORY,
    },
    System::Threading::{GetProcessId, GetProcessTimes},
};

const RUNTIME_SOURCE_SCHEMA: &str = "vrcforge.protected_runtime_source.v2";
const DEPENDENCY_SET_SCHEMA: &str = "vrcforge.protected_runtime_dependency_set.v2";
const BRIDGE_TARGET_RUNTIME_SCHEMA: &str = "vrcforge.bridge_target_runtime.v1";
const TREE_SOURCE_SCHEMA: &str = "vrcforge.protected_runtime_tree_source.v1";
const BRIDGE_TARGET_RUNTIME_ROOT: &str = "bridge_target";
const BRIDGE_TARGET_EXECUTABLE_PATH: &str = "bridge_target/vrcforge_bridge_target.exe";
const BRIDGE_TARGET_MANIFEST_PATH: &str = "bridge-target-manifest.json";
const FIXTURE_SCHEMA: &str = "vrcforge.primitive_basis_model_part_fixture.v1";
const FIXTURE_BASELINE_SCHEMA: &str = "vrcforge.primitive_basis_baseline.v1";
const SCENARIO_ID: &str = "model_part_composition";
const REQUIRED_PRIMITIVE: &str = "non_destructive_part_composition";
const FIXTURE_UNITY_VERSION: &str = "2022.3.22f1";
const FIXTURE_UNITY_REVISION: &str = "887be4894c44";
const FIXTURE_SCENE_ASSET_PATH: &str =
    "Assets/VRCForge/PrimitiveBasis/model_part_composition/ModelPartComposition.unity";
const FIXTURE_SCENE_GUID: &str = "285dbe12f5ede174cbcd075983e1410f";
const FIXTURE_AVATAR_PATH: &str = "FixtureAvatar";
const FIXTURE_BASE_ARMATURE_PATH: &str = "FixtureAvatar/Armature";
const FIXTURE_PART_ROOT_PATH: &str = "FixtureAvatar/Part";
const FIXTURE_COMPONENT_HOST_PATH: &str = "FixtureAvatar/Part/Armature";
const FIXTURE_MERGE_TARGET_PATH: &str = "FixtureAvatar/Armature";
const FIXTURE_RENDERER_PATH: &str = "FixtureAvatar/Part/RendererProbe";
const FIXTURE_BOOTSTRAP_TYPE: &str =
    "VRCForge.PrimitiveBasisFixtures.ModelPartCompositionFixtureBootstrap";
const FIXTURE_RUN_ID_ENVIRONMENT: &str = "VRCFORGE_PRIMITIVE_BASIS_RUN_ID";
const FIXTURE_READY_MARKER: &str = "Library/VRCForge/primitive-basis-model-part-ready.json";
const FIXTURE_TREE_DIGEST_HEX: &str =
    "eb6b2aa1b96e86723047d04d3e5059491d6fc5dae8534f8334ff55940676b622";
const FIXTURE_PROJECT_FILES: [(&str, &str); 3] = [
    (
        "Packages/manifest.json",
        "98b0f74110da34cfa5439cd6a99790f943be64fa378c8fcf9132720c0b2e5991",
    ),
    (
        "Packages/packages-lock.json",
        "b6b54dee94864c04bdf7223710576394b952182a2a3b2ab22dd4dd6489db2b92",
    ),
    (
        "ProjectSettings/ProjectVersion.txt",
        "0599d0a5e5f574ce3aaf02f6f98a477bd75b97ddd6a1cf9a9466c603c3d7b6ca",
    ),
];
const FIXTURE_REQUIRED_PACKAGES: [(&str, &str); 4] = [
    ("com.vrchat.avatars", "3.10.4"),
    ("com.vrchat.base", "3.10.4"),
    ("nadena.dev.modular-avatar", "1.17.1"),
    ("nadena.dev.ndmf", "1.13.1"),
];
const FIXTURE_PACKAGE_PROVISIONING: &str = "exact_artifact";
const FIXTURE_BASELINE_FILES: [(&str, u64, &str); 8] = [
    (
        "Editor.meta",
        169,
        "8216f68927f70e8c33485ac8bf8b70d1bdaa3c9a58dadeb90f3d598f13ea4af1",
    ),
    (
        "Editor/ModelPartCompositionFixtureBootstrap.cs",
        6694,
        "3b5f31ca7793d2fb33ea87da5341c8f95d5cea3024c1dacb8a70e10aaab330c3",
    ),
    (
        "Editor/ModelPartCompositionFixtureBootstrap.cs.meta",
        240,
        "515154b54ac206072df84989feab618b4c856face126979b4c5c8b2beb288722",
    ),
    (
        "ModelPartComposition.unity",
        11_348,
        "59de4a023cd1912acbbe9215c722886cc700879f43d8d4d477145f24b58aa97f",
    ),
    (
        "ModelPartComposition.unity.meta",
        152,
        "046beb2316cc5cc422e3b9cf46a59379481221b7947ec9487904b55de2a0c63b",
    ),
    (
        "baseline.json.meta",
        155,
        "189a2c7f9e34e005816591ac29b87e5f06d267bad8d7a907089e452aa7c3b3d6",
    ),
    (
        "fixture-contract.json",
        1904,
        "43c687eb885d3cce9f2549979585b41da2445e04c68a14d1f902f10b47ed00fc",
    ),
    (
        "fixture-contract.json.meta",
        155,
        "aaebf8b9e7693389c7ea292db3827af2011a51f5d6ff81160f5aed6129e5f4cd",
    ),
];
const STRICT_BUILD_MODE: &str = "strict-evidence";
const POLICY_LIFETIME_SECONDS: u64 = 15 * 60;
const MAX_RUNTIME_SOURCE_BYTES: u64 = 2 * 1024 * 1024;
const MAX_EXECUTABLE_BYTES: u64 = 512 * 1024 * 1024;
const MAX_SOURCE_BYTES: u64 = 16 * 1024 * 1024;
const MAX_ARCHIVE_BYTES: u64 = 16 * 1024 * 1024 * 1024;
const MAX_POLICY_TREE_BYTES: u64 = 16 * 1024 * 1024 * 1024;
const MAX_POLICY_TREE_ENTRIES: u64 = 200_000;
const FILE_ATTRIBUTE_REPARSE_POINT_VALUE: u32 = 0x0000_0400;
const PRIVATE_PORT_BASE: u16 = 49_152;
const PRIVATE_PORT_SPAN: u16 = 16_384;

const FINAL_COMMIT_BINDING_DOMAIN: &[u8] = b"vrcforge-protected-policy-final-commit-binding-v1\0";
const HELD_FILE_IDENTITY_DOMAIN: &[u8] = b"vrcforge-protected-policy-held-file-identity-v1\0";
const HELD_PROCESS_BINDING_DOMAIN: &[u8] = b"vrcforge-protected-policy-held-process-binding-v1\0";
const RUNTIME_SOURCE_BINDING_DOMAIN: &[u8] =
    b"vrcforge-protected-policy-runtime-source-binding-v1\0";
const SCENARIO_BINDING_DOMAIN: &[u8] = b"vrcforge-protected-policy-scenario-binding-v1\0";
const RUNNER_IDENTITY_DOMAIN: &[u8] = b"vrcforge-protected-policy-runner-identity-v1\0";
const RUNNER_ACCOUNT_DOMAIN: &[u8] = b"vrcforge-protected-policy-runner-account-v1\0";
const RUNNER_PROFILE_DOMAIN: &[u8] = b"vrcforge-protected-policy-runner-profile-v1\0";
const CHILD_TRANSPORT_CONTRACT_DOMAIN: &[u8] =
    b"vrcforge-protected-policy-child-transport-contract-v1\0";
const JOB_NAME_DOMAIN: &[u8] = b"vrcforge-protected-policy-job-name-v1\0";
const JOB_OBJECT_DOMAIN: &[u8] = b"vrcforge-protected-policy-job-object-v1\0";
const PRIVATE_ROOT_DOMAIN: &[u8] = b"vrcforge-protected-policy-private-root-v1\0";
const ARTIFACT_BINDING_DOMAIN: &[u8] = b"vrcforge-protected-policy-artifact-v1\0";
const OUTPUT_BINDING_DOMAIN: &[u8] = b"vrcforge-protected-policy-output-v1\0";
const ENDPOINT_BINDING_DOMAIN: &[u8] = b"vrcforge-protected-policy-endpoint-v1\0";
const READINESS_BINDING_DOMAIN: &[u8] = b"vrcforge-protected-policy-readiness-v1\0";

const ROLE_NAMES: [&str; 7] = [
    "authority_service",
    "driver",
    "desktop",
    "backend",
    "unity",
    "bridge_launcher",
    "bridge_listener",
];
const BRIDGE_LISTENER_ROLE_INDEX: usize = ROLE_NAMES.len() - 1;
const SOURCE_NAMES: [&str; 2] = ["runtime_contract", "fixture_baseline"];
const RELEASE_ARTIFACTS: [(&str, u64); 3] = [
    ("strictManifest", MAX_SOURCE_BYTES),
    ("portableArchive", MAX_ARCHIVE_BYTES),
    ("unityPackage", MAX_ARCHIVE_BYTES),
];
const PACKAGE_TREE_NAMES: [&str; 3] = ["backend", "vrcforgeCore", "server"];
const FIXTURE_SCENARIOS: [&str; 4] = [
    "component_feature_application",
    "parameter_optimization",
    "cross_avatar_accessory_copy",
    SCENARIO_ID,
];

#[derive(Debug, Clone, PartialEq, Eq)]
struct HeldFileReadback {
    content_digest: Digest,
    byte_length: u64,
    identity_digest: Digest,
}

impl HeldFileReadback {
    #[cfg(test)]
    fn for_test(content_digest: Digest, tag: u8, byte_length: u64) -> Self {
        let mut hasher = Sha256::new();
        hasher.update(HELD_FILE_IDENTITY_DOMAIN);
        hasher.update([tag]);
        hasher.update(byte_length.to_be_bytes());
        Self {
            content_digest,
            byte_length,
            identity_digest: hasher.finalize().into(),
        }
    }

    fn valid(&self) -> bool {
        !is_zero(&self.content_digest) && self.byte_length != 0 && !is_zero(&self.identity_digest)
    }
}

/// Readback of the exact install-independent runtime source held open by the
/// service.
/// Its fields are private so downstream callers cannot substitute role or
/// source digests after validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ProtectedRuntimeSourceReadback {
    version: String,
    manifest_digest: Digest,
    manifest_identity_digest: Digest,
    source_commit_digest: Digest,
    bridge_target_manifest_digest: Digest,
    bridge_target_tree_digest: Digest,
    dependency_set: DependencySetBinding,
    evidence: ProtectedEvidenceManifestProjection,
    binding_digest: Digest,
    role_digests: [Digest; PROCESS_ROLES.len()],
    role_byte_counts: [u64; PROCESS_ROLES.len()],
    source_digests: [Digest; SOURCE_NAMES.len()],
    source_byte_counts: [u64; SOURCE_NAMES.len()],
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ProtectedEvidenceManifestProjection {
    strict_manifest_digest: Digest,
    portable_digest: Digest,
    unity_package_digest: Digest,
    backend_tree_digest: Digest,
    vrcforge_core_tree_digest: Digest,
    server_tree_digest: Digest,
    fixture_set_descriptor_digest: Digest,
    fixture_set_digest: Digest,
    fixture_descriptor_digest: Digest,
    fixture_digest: Digest,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct DependencySetBinding {
    descriptor_digest: Digest,
    set_digest: Digest,
    byte_count: u64,
    binding_digest: Digest,
}

impl ProtectedRuntimeSourceReadback {
    pub(crate) fn read_from_capability(
        capability: &mut AuthenticatedRuntimeSourceCapability,
    ) -> Result<Self, SupervisorError> {
        let readback = capability
            .read_verified()
            .map_err(|error| SupervisorError::new(error.code()))?;
        let (descriptor, identity_digest, bytes) = readback.into_parts();
        if descriptor.byte_length() > MAX_RUNTIME_SOURCE_BYTES {
            return Err(SupervisorError::new(
                "authority_policy_manifest_handle_invalid",
            ));
        }
        let held = HeldFileReadback {
            content_digest: *descriptor.sha256(),
            byte_length: descriptor.byte_length(),
            identity_digest,
        };
        protected_runtime_source_readback_from_bytes(capability.binding(), held, &bytes)
    }
}

/// Positional handle set for the only registered production scenario. There
/// is no role-name or path input and therefore no extension point for an extra
/// process or source role.
pub(crate) struct FixedModelPartHandles<'a> {
    driver: &'a File,
    desktop: &'a File,
    backend: &'a File,
    unity: &'a File,
    bridge_launcher: &'a File,
    bridge_listener: &'a File,
    fixture_contract: &'a File,
    fixture_baseline: &'a File,
}

impl<'a> FixedModelPartHandles<'a> {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        driver: &'a File,
        desktop: &'a File,
        backend: &'a File,
        unity: &'a File,
        bridge_launcher: &'a File,
        bridge_listener: &'a File,
        fixture_contract: &'a File,
        fixture_baseline: &'a File,
    ) -> Self {
        Self {
            driver,
            desktop,
            backend,
            unity,
            bridge_launcher,
            bridge_listener,
            fixture_contract,
            fixture_baseline,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct FixedModelPartHeldReadback {
    candidate_executables: [HeldFileReadback; 6],
    fixture_contract: HeldFileReadback,
    fixture_baseline: HeldFileReadback,
    expected_tree_digest: Digest,
    scenario_binding_digest: Digest,
}

impl FixedModelPartHeldReadback {
    pub(crate) fn read_from_held_handles(
        package: &ProtectedRuntimeSourceReadback,
        handles: FixedModelPartHandles<'_>,
    ) -> Result<Self, SupervisorError> {
        let candidate_executables = [
            read_held_file(handles.driver, MAX_EXECUTABLE_BYTES)?.0,
            read_held_file(handles.desktop, MAX_EXECUTABLE_BYTES)?.0,
            read_held_file(handles.backend, MAX_EXECUTABLE_BYTES)?.0,
            read_held_file(handles.unity, MAX_EXECUTABLE_BYTES)?.0,
            read_held_file(handles.bridge_launcher, MAX_EXECUTABLE_BYTES)?.0,
            read_held_file(handles.bridge_listener, MAX_EXECUTABLE_BYTES)?.0,
        ];
        let (fixture_contract, contract_bytes) =
            read_held_file(handles.fixture_contract, MAX_SOURCE_BYTES)?;
        let (fixture_baseline, baseline_bytes) =
            read_held_file(handles.fixture_baseline, MAX_SOURCE_BYTES)?;
        fixed_model_part_readback_from_parts(
            package,
            candidate_executables,
            fixture_contract,
            &contract_bytes,
            fixture_baseline,
            &baseline_bytes,
        )
    }
}

/// Service and parent epochs read from handles that remain owned by the
/// service. Process identifiers are output-only and cannot be supplied by the
/// policy caller.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct HeldAuthorityProcessReadback {
    service: ProcessKey,
    parent: ProcessKey,
    binding_digest: Digest,
}

impl HeldAuthorityProcessReadback {
    pub(crate) fn read_from_held_handles(
        final_commit: &AuthenticatedFinalCommitPolicyBinding,
        service: BorrowedHandle<'_>,
        parent: BorrowedHandle<'_>,
    ) -> Result<Self, SupervisorError> {
        let service = process_key_from_handle(service)?;
        let parent = process_key_from_handle(parent)?;
        let readback = Self::from_keys(service, parent)?;
        readback.verify_final_commit(final_commit)?;
        Ok(readback)
    }

    fn from_keys(service: ProcessKey, parent: ProcessKey) -> Result<Self, SupervisorError> {
        if service.pid == 0
            || service.creation_time == 0
            || parent.pid == 0
            || parent.creation_time == 0
            || service == parent
            || parent.creation_time >= service.creation_time
        {
            return Err(SupervisorError::new(
                "authority_policy_process_readback_invalid",
            ));
        }
        let binding_digest = held_process_binding(&service, &parent);
        Ok(Self {
            service,
            parent,
            binding_digest,
        })
    }

    fn verify_final_commit(
        &self,
        final_commit: &AuthenticatedFinalCommitPolicyBinding,
    ) -> Result<(), SupervisorError> {
        if self.service.pid != final_commit.service_process_id()
            || self.service.creation_time != final_commit.service_process_creation_time()
            || self.binding_digest != held_process_binding(&self.service, &self.parent)
        {
            return Err(SupervisorError::new(
                "authority_policy_service_process_drift",
            ));
        }
        Ok(())
    }

    #[cfg(test)]
    fn for_test(
        final_commit: &AuthenticatedFinalCommitPolicyBinding,
    ) -> Result<Self, SupervisorError> {
        Self::from_keys(
            ProcessKey {
                pid: final_commit.service_process_id(),
                creation_time: final_commit.service_process_creation_time(),
            },
            ProcessKey {
                pid: final_commit.service_process_id().saturating_sub(1),
                creation_time: final_commit.service_process_creation_time() - 1,
            },
        )
    }
}

/// Opaque result of the native Job security module's descriptor construction
/// and exact readback checks. Only supervisor-internal native code can create
/// this token in production; callers cannot pass a raw digest.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct VerifiedJobSecurityBinding {
    authority_identity_digest: Digest,
    binding_digest: Digest,
}

impl VerifiedJobSecurityBinding {
    pub(super) fn from_validated_native_spec(
        authority_identity_digest: Digest,
        binding_digest: Digest,
    ) -> Result<Self, SupervisorError> {
        if is_zero(&authority_identity_digest) || is_zero(&binding_digest) {
            return Err(SupervisorError::new(
                "authority_policy_job_security_binding_invalid",
            ));
        }
        Ok(Self {
            authority_identity_digest,
            binding_digest,
        })
    }
}

/// Seals the live, authenticated service-generation readback into the only
/// readiness proof accepted by production policy construction. The caller can
/// neither provide a service-instance digest nor assemble the opaque readback.
pub(crate) fn build_verified_readiness(
    final_commit: &AuthenticatedFinalCommitPolicyBinding,
    runtime_identity: &AuthorityRuntimeIdentity,
    readback: &AuthenticatedGenerationBindingReadback,
) -> Result<VerifiedReadinessProof, SupervisorError> {
    validate_final_commit_binding(final_commit, runtime_identity)?;
    if readback.current_generation() != final_commit.generation()
        || readback.service_executable_sha256() != final_commit.service_binary_sha256()
        || readback.controller_executable_sha256() != final_commit.controller_binary_sha256()
        || readback.install_helper_executable_sha256()
            != final_commit.install_helper_binary_sha256()
        || readback.installed_layout_sha256() != final_commit.installed_layout_sha256()
        || readback.ledger_identity_sha256() != final_commit.ledger_identity()
        || readback.service_process_id() != final_commit.service_process_id()
        || readback.service_process_started_at() != final_commit.service_process_creation_time()
        || readback.signer_key_id() != final_commit.signer_key_id()
        || readback.final_commit_receipt_sha256() != final_commit.final_commit_receipt_sha256()
    {
        return Err(SupervisorError::new(
            "authority_policy_readiness_readback_mismatch",
        ));
    }

    let mut digest = Sha256::new();
    digest.update(READINESS_BINDING_DOMAIN);
    digest.update(runtime_identity.binding_digest());
    for value in [
        readback.current_generation(),
        readback.service_executable_sha256(),
        readback.controller_executable_sha256(),
        readback.install_helper_executable_sha256(),
        readback.installed_layout_sha256(),
        readback.ledger_identity_sha256(),
        readback.service_executable_path_sha256(),
        readback.service_executable_file_identity_sha256(),
        readback.protected_manifest_readback_sha256(),
        readback.protected_key_readback_sha256(),
        readback.signer_key_id(),
        readback.protected_ledger_readback_sha256(),
        readback.scm_readback_sha256(),
        readback.final_commit_receipt_sha256(),
    ] {
        digest.update(value);
    }
    digest.update(readback.service_process_id().to_be_bytes());
    digest.update(readback.service_process_started_at().to_be_bytes());
    let service_instance_digest = digest.finalize().into();
    Ok(VerifiedReadinessProof::from_authenticated_readback(
        runtime_identity.binding_digest(),
        service_instance_digest,
    ))
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn prepare_model_part_run(
    final_commit: &AuthenticatedFinalCommitPolicyBinding,
    runtime_identity: &AuthorityRuntimeIdentity,
    readiness: &VerifiedReadinessProof,
    ticket: &RuntimeTicketRef,
    package: &ProtectedRuntimeSourceReadback,
    scenario: &FixedModelPartHeldReadback,
    processes: &HeldAuthorityProcessReadback,
    job_security: &VerifiedJobSecurityBinding,
) -> Result<PreparedRun, SupervisorError> {
    let policy = build_model_part_supervisor_policy(
        final_commit,
        runtime_identity,
        readiness,
        ticket,
        package,
        scenario,
        processes,
        job_security,
    )?;
    Ok(PreparedRun::from_policy(&policy))
}

pub(crate) fn build_model_part_supervisor_policy(
    final_commit: &AuthenticatedFinalCommitPolicyBinding,
    runtime_identity: &AuthorityRuntimeIdentity,
    readiness: &VerifiedReadinessProof,
    ticket: &RuntimeTicketRef,
    package: &ProtectedRuntimeSourceReadback,
    scenario: &FixedModelPartHeldReadback,
    processes: &HeldAuthorityProcessReadback,
    job_security: &VerifiedJobSecurityBinding,
) -> Result<SupervisorPolicy, SupervisorError> {
    let issued_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| SupervisorError::new("authority_policy_clock_invalid"))?
        .as_secs();
    build_model_part_supervisor_policy_at(
        final_commit,
        runtime_identity,
        readiness,
        ticket,
        package,
        scenario,
        processes,
        job_security,
        issued_at,
    )
}

#[allow(clippy::too_many_arguments)]
fn build_model_part_supervisor_policy_at(
    final_commit: &AuthenticatedFinalCommitPolicyBinding,
    runtime_identity: &AuthorityRuntimeIdentity,
    readiness: &VerifiedReadinessProof,
    ticket: &RuntimeTicketRef,
    package: &ProtectedRuntimeSourceReadback,
    scenario: &FixedModelPartHeldReadback,
    processes: &HeldAuthorityProcessReadback,
    job_security: &VerifiedJobSecurityBinding,
    issued_at: u64,
) -> Result<SupervisorPolicy, SupervisorError> {
    validate_final_commit_binding(final_commit, runtime_identity)?;
    let authority_identity_digest = runtime_identity.binding_digest();
    if !readiness.verifies_for(&authority_identity_digest) {
        return Err(SupervisorError::new(
            "authority_policy_readiness_binding_mismatch",
        ));
    }
    processes.verify_final_commit(final_commit)?;
    validate_package_binding(final_commit, package)?;
    validate_scenario_binding(package, scenario)?;
    if job_security.authority_identity_digest != authority_identity_digest
        || is_zero(&job_security.binding_digest)
    {
        return Err(SupervisorError::new(
            "authority_policy_job_security_binding_mismatch",
        ));
    }
    let ticket_digest = ticket.digest();
    if is_zero(&ticket_digest) || ticket.as_str() != hex_lower(&ticket_digest) || issued_at == 0 {
        return Err(SupervisorError::new("authority_policy_ticket_invalid"));
    }
    let deadline = issued_at
        .checked_add(POLICY_LIFETIME_SECONDS)
        .ok_or_else(|| SupervisorError::new("authority_policy_clock_invalid"))?;

    let runner_identity_digest = derive(
        RUNNER_IDENTITY_DOMAIN,
        &[
            &authority_identity_digest,
            &package.binding_digest,
            &scenario.scenario_binding_digest,
        ],
    );
    let runner_account_digest = derive(
        RUNNER_ACCOUNT_DOMAIN,
        &[final_commit.generation(), &runner_identity_digest],
    );
    let runner_profile_digest = derive(
        RUNNER_PROFILE_DOMAIN,
        &[
            final_commit.generation(),
            &ticket_digest,
            &runner_identity_digest,
        ],
    );
    let child_transport_projection = child_transport_contract_projection(package, scenario);
    let child_transport_contract_digest =
        child_transport_contract_digest_from_projection(&child_transport_projection);
    let deterministic_job_name_digest = derive(
        JOB_NAME_DOMAIN,
        &[
            final_commit.generation(),
            &authority_identity_digest,
            &ticket_digest,
            SCENARIO_ID.as_bytes(),
        ],
    );
    let job_object_digest = derive(
        JOB_OBJECT_DOMAIN,
        &[&deterministic_job_name_digest, &processes.binding_digest],
    );
    let job_object_id = u64::from_be_bytes(job_object_digest[..8].try_into().unwrap());
    if job_object_id == 0 {
        return Err(SupervisorError::new("authority_policy_job_id_invalid"));
    }
    let private_root_binding_digest = derive(
        PRIVATE_ROOT_DOMAIN,
        &[
            final_commit.generation(),
            &ticket_digest,
            &package.binding_digest,
            &scenario.scenario_binding_digest,
        ],
    );

    let mut process_executable_digests = [[0u8; 32]; PROCESS_ROLES.len()];
    process_executable_digests[0] = *final_commit.service_binary_sha256();
    for (target, held) in process_executable_digests[1..]
        .iter_mut()
        .zip(&scenario.candidate_executables)
    {
        *target = held.content_digest;
    }
    let authority_binding = FixedAuthorityBinding::new(
        PROTECTED_EVIDENCE_POLICY_ID,
        *runtime_identity.authority_generation_digest(),
        *final_commit.protected_manifest_sha256(),
        *final_commit.installed_layout_sha256(),
        *final_commit.service_binary_sha256(),
        *final_commit.controller_binary_sha256(),
        *final_commit.install_helper_binary_sha256(),
        *final_commit.ledger_identity(),
    )
    .map_err(|error| SupervisorError::new(error.code()))?;
    let package_binding = FixedPackageBinding::new(
        &package.version,
        [
            package.evidence.strict_manifest_digest,
            package.evidence.portable_digest,
            package.role_digests[2],
            package.role_digests[3],
            package.evidence.backend_tree_digest,
            package.role_digests[1],
            package.evidence.unity_package_digest,
            package.evidence.vrcforge_core_tree_digest,
            package.evidence.vrcforge_core_tree_digest,
            package.role_digests[4],
            package.role_digests[5],
            package.role_digests[6],
            package.evidence.vrcforge_core_tree_digest,
            package.evidence.server_tree_digest,
            package.dependency_set.set_digest,
            package.binding_digest,
        ],
    )
    .map_err(|error| SupervisorError::new(error.code()))?;
    let protected_evidence_source = PreparedProtectedEvidenceSource::new(
        authority_binding,
        package_binding,
        package.evidence.fixture_set_descriptor_digest,
        package.evidence.fixture_set_digest,
        package.evidence.fixture_descriptor_digest,
        package.evidence.fixture_digest,
        scenario.scenario_binding_digest,
    )
    .map_err(|error| SupervisorError::new(error.code()))?;

    let artifacts = vec![
        ArtifactExpectation {
            binding_digest: artifact_binding(b"fixture-contract", &scenario.fixture_contract),
            direction: ArtifactDirection::Input,
            expected_content_digest: Some(scenario.fixture_contract.content_digest),
        },
        ArtifactExpectation {
            binding_digest: artifact_binding(b"fixture-baseline", &scenario.fixture_baseline),
            direction: ArtifactDirection::Input,
            expected_content_digest: Some(scenario.fixture_baseline.content_digest),
        },
        ArtifactExpectation {
            binding_digest: derive(
                OUTPUT_BINDING_DOMAIN,
                &[
                    &private_root_binding_digest,
                    &scenario.expected_tree_digest,
                    SCENARIO_ID.as_bytes(),
                ],
            ),
            direction: ArtifactDirection::Output,
            expected_content_digest: None,
        },
    ];

    let app_binding = derive(
        ENDPOINT_BINDING_DOMAIN,
        &[&ticket_digest, b"app", &package.binding_digest],
    );
    let bridge_binding = derive(
        ENDPOINT_BINDING_DOMAIN,
        &[&ticket_digest, b"bridge", &scenario.scenario_binding_digest],
    );
    let bridge_port = PRIVATE_PORT_BASE
        + (u16::from_be_bytes([bridge_binding[0], bridge_binding[1]]) % PRIVATE_PORT_SPAN);
    let socket_policies = vec![
        SocketPolicy {
            role: SocketRole::App,
            mode: SocketEndpointMode::FixedFixture,
            local_port: APP_LOOPBACK_PORT,
            owner_role: ProcessRole::Backend,
            driver_binding_digest: app_binding,
        },
        SocketPolicy {
            role: SocketRole::Bridge,
            mode: SocketEndpointMode::ServiceSelectedPrivate,
            local_port: bridge_port,
            owner_role: ProcessRole::BridgeListener,
            driver_binding_digest: bridge_binding,
        },
    ];

    let mut policy = SupervisorPolicy {
        authority_identity_digest,
        authority_generation_digest: *runtime_identity.authority_generation_digest(),
        ticket_digest,
        run_binding_digest: [0; 32],
        service_instance_digest: *readiness.service_instance_digest(),
        runner_policy_digest: [0; 32],
        issued_at,
        deadline,
        authority_process: processes.service,
        authority_parent_process: processes.parent,
        process_executable_digests,
        bridge_target_manifest_digest: package.bridge_target_manifest_digest,
        bridge_target_tree_digest: package.bridge_target_tree_digest,
        runner_identity_digest,
        runner_account_digest,
        runner_profile_digest,
        child_transport_projection,
        child_transport_contract_digest,
        deterministic_job_name_digest,
        job_security_binding_digest: job_security.binding_digest,
        private_root_binding_digest,
        job_object_id,
        protected_evidence_source,
        artifacts,
        socket_policies,
        helper_policies: Vec::<HelperProcessPolicy>::new(),
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

fn protected_runtime_source_readback_from_bytes(
    final_commit: &AuthenticatedFinalCommitPolicyBinding,
    held: HeldFileReadback,
    bytes: &[u8],
) -> Result<ProtectedRuntimeSourceReadback, SupervisorError> {
    if !held.valid() || held.content_digest != sha256(bytes) {
        return Err(SupervisorError::new(
            "authority_policy_manifest_handle_invalid",
        ));
    }
    let value = parse_strict_json(bytes)?;
    if canonical_json_line(&value)? != bytes {
        return Err(SupervisorError::new(
            "authority_policy_manifest_noncanonical",
        ));
    }
    let root = value
        .as_object()
        .ok_or_else(|| SupervisorError::new("authority_policy_manifest_invalid"))?;
    require_exact_keys(
        root,
        &[
            "schema",
            "version",
            "sourceCommit",
            "scenarioId",
            "buildPolicy",
            "roles",
            "sources",
            "bridgeTargetRuntime",
            "releaseArtifacts",
            "packageTrees",
            "dependencySet",
            "fixtureSet",
            "modelFixture",
        ],
        "authority_policy_package_contract_invalid",
    )?;
    if required_string(root, "schema")? != RUNTIME_SOURCE_SCHEMA {
        return Err(SupervisorError::new(
            "authority_policy_package_contract_invalid",
        ));
    }
    let version = required_string(root, "version")?;
    if version != env!("CARGO_PKG_VERSION") {
        return Err(SupervisorError::new(
            "authority_policy_manifest_version_mismatch",
        ));
    }
    let commit = required_string(root, "sourceCommit")?;
    if !is_lower_hex(commit, 40) || commit.bytes().all(|byte| byte == b'0') {
        return Err(SupervisorError::new(
            "authority_policy_manifest_commit_invalid",
        ));
    }
    validate_strict_build_policy(required_object(root, "buildPolicy")?)?;
    if required_string(root, "scenarioId")? != SCENARIO_ID {
        return Err(SupervisorError::new(
            "authority_policy_package_identity_mismatch",
        ));
    }

    let role_values = required_array(root, "roles")?;
    if role_values.len() != ROLE_NAMES.len() {
        return Err(SupervisorError::new(
            "authority_policy_package_roles_invalid",
        ));
    }
    let mut role_digests = [[0u8; 32]; PROCESS_ROLES.len()];
    let mut role_byte_counts = [0u64; PROCESS_ROLES.len()];
    for (index, (expected_role, value)) in ROLE_NAMES.iter().zip(role_values).enumerate() {
        let role = value
            .as_object()
            .ok_or_else(|| SupervisorError::new("authority_policy_package_roles_invalid"))?;
        require_exact_keys(
            role,
            &["role", "sha256", "byteCount"],
            "authority_policy_package_roles_invalid",
        )?;
        if required_string(role, "role")? != *expected_role {
            return Err(SupervisorError::new(
                "authority_policy_package_roles_invalid",
            ));
        }
        role_digests[index] = required_digest(role, "sha256")?;
        role_byte_counts[index] = required_bounded_count(
            role,
            "byteCount",
            MAX_EXECUTABLE_BYTES,
            false,
            "authority_policy_package_roles_invalid",
        )?;
    }
    if role_digests[0] != *final_commit.service_binary_sha256() {
        return Err(SupervisorError::new(
            "authority_policy_package_service_image_mismatch",
        ));
    }

    let source_values = required_array(root, "sources")?;
    if source_values.len() != SOURCE_NAMES.len() {
        return Err(SupervisorError::new(
            "authority_policy_package_sources_invalid",
        ));
    }
    let mut source_digests = [[0u8; 32]; SOURCE_NAMES.len()];
    let mut source_byte_counts = [0u64; SOURCE_NAMES.len()];
    for (index, (expected_source, value)) in SOURCE_NAMES.iter().zip(source_values).enumerate() {
        let source = value
            .as_object()
            .ok_or_else(|| SupervisorError::new("authority_policy_package_sources_invalid"))?;
        require_exact_keys(
            source,
            &["source", "sha256", "byteCount"],
            "authority_policy_package_sources_invalid",
        )?;
        if required_string(source, "source")? != *expected_source {
            return Err(SupervisorError::new(
                "authority_policy_package_sources_invalid",
            ));
        }
        source_digests[index] = required_digest(source, "sha256")?;
        source_byte_counts[index] = required_bounded_count(
            source,
            "byteCount",
            MAX_SOURCE_BYTES,
            false,
            "authority_policy_package_sources_invalid",
        )?;
    }

    let source_commit_digest = sha256(commit.as_bytes());
    let bridge_target = required_object(root, "bridgeTargetRuntime")?;
    require_exact_keys(
        bridge_target,
        &[
            "schema",
            "runtimeRelativeRoot",
            "executableRelativePath",
            "executableSha256",
            "manifestRelativePath",
            "manifestSha256",
            "treeDigest",
            "directoryCount",
            "entryCount",
            "byteCount",
            "candidatePayloadIncluded",
            "strictSourceBound",
            "verifiedAfterBuild",
        ],
        "authority_policy_bridge_target_runtime_invalid",
    )?;
    let bridge_target_manifest_digest = required_digest(bridge_target, "manifestSha256")?;
    let bridge_target_tree_digest = required_digest(bridge_target, "treeDigest")?;
    let bridge_target_executable_digest = required_digest(bridge_target, "executableSha256")?;
    required_bounded_count(
        bridge_target,
        "directoryCount",
        MAX_POLICY_TREE_ENTRIES,
        false,
        "authority_policy_bridge_target_runtime_invalid",
    )?;
    required_bounded_count(
        bridge_target,
        "entryCount",
        MAX_POLICY_TREE_ENTRIES,
        false,
        "authority_policy_bridge_target_runtime_invalid",
    )?;
    required_bounded_count(
        bridge_target,
        "byteCount",
        MAX_POLICY_TREE_BYTES,
        false,
        "authority_policy_bridge_target_runtime_invalid",
    )?;
    if required_string(bridge_target, "schema")? != BRIDGE_TARGET_RUNTIME_SCHEMA
        || required_string(bridge_target, "runtimeRelativeRoot")? != BRIDGE_TARGET_RUNTIME_ROOT
        || required_string(bridge_target, "executableRelativePath")?
            != BRIDGE_TARGET_EXECUTABLE_PATH
        || PROCESS_ROLES[BRIDGE_LISTENER_ROLE_INDEX] != ProcessRole::BridgeListener
        || bridge_target_executable_digest != role_digests[BRIDGE_LISTENER_ROLE_INDEX]
        || required_string(bridge_target, "manifestRelativePath")? != BRIDGE_TARGET_MANIFEST_PATH
        || !required_bool(bridge_target, "candidatePayloadIncluded")?
        || !required_bool(bridge_target, "strictSourceBound")?
        || !required_bool(bridge_target, "verifiedAfterBuild")?
    {
        return Err(SupervisorError::new(
            "authority_policy_bridge_target_runtime_invalid",
        ));
    }
    let (dependency_set, evidence) = validate_v2_runtime_source_sections(root)?;
    let binding_digest = runtime_source_binding(
        final_commit,
        &held.content_digest,
        &held.identity_digest,
        &source_commit_digest,
        &bridge_target_manifest_digest,
        &bridge_target_tree_digest,
        &role_digests,
        &role_byte_counts,
        &source_digests,
        &source_byte_counts,
        &dependency_set,
    );
    Ok(ProtectedRuntimeSourceReadback {
        version: version.to_owned(),
        manifest_digest: held.content_digest,
        manifest_identity_digest: held.identity_digest,
        source_commit_digest,
        bridge_target_manifest_digest,
        bridge_target_tree_digest,
        dependency_set,
        evidence,
        binding_digest,
        role_digests,
        role_byte_counts,
        source_digests,
        source_byte_counts,
    })
}

fn validate_v2_runtime_source_sections(
    root: &Map<String, Value>,
) -> Result<(DependencySetBinding, ProtectedEvidenceManifestProjection), SupervisorError> {
    const CODE: &str = "authority_policy_package_contract_invalid";
    let validated: Result<
        (DependencySetBinding, ProtectedEvidenceManifestProjection),
        SupervisorError,
    > = (|| {
        let release_artifacts = required_object(root, "releaseArtifacts")?;
        require_exact_keys(
            release_artifacts,
            &RELEASE_ARTIFACTS
                .iter()
                .map(|(name, _)| *name)
                .collect::<Vec<_>>(),
            CODE,
        )?;
        let mut release_digests = [[0u8; 32]; RELEASE_ARTIFACTS.len()];
        for (index, (name, maximum)) in RELEASE_ARTIFACTS.into_iter().enumerate() {
            release_digests[index] = validate_file_record(
                release_artifacts
                    .get(name)
                    .ok_or_else(|| SupervisorError::new(CODE))?,
                maximum,
            )?;
        }

        let package_trees = required_object(root, "packageTrees")?;
        require_exact_keys(package_trees, &PACKAGE_TREE_NAMES, CODE)?;
        let mut package_tree_digests = [[0u8; 32]; PACKAGE_TREE_NAMES.len()];
        for (index, name) in PACKAGE_TREE_NAMES.into_iter().enumerate() {
            package_tree_digests[index] = validate_tree_source_record(
                package_trees
                    .get(name)
                    .ok_or_else(|| SupervisorError::new(CODE))?,
            )?;
        }

        let dependency_set = required_object(root, "dependencySet")?;
        require_exact_keys(
            dependency_set,
            &[
                "descriptorSchema",
                "setDigest",
                "descriptorSha256",
                "byteCount",
                "canonicalJson",
                "bindingDigest",
            ],
            CODE,
        )?;
        let descriptor_digest = required_digest(dependency_set, "descriptorSha256")?;
        let set_digest = required_digest(dependency_set, "setDigest")?;
        let byte_count =
            required_bounded_count(dependency_set, "byteCount", MAX_SOURCE_BYTES, false, CODE)?;
        let binding_digest = required_digest(dependency_set, "bindingDigest")?;
        if required_string(dependency_set, "descriptorSchema")? != DEPENDENCY_SET_SCHEMA
            || !required_bool(dependency_set, "canonicalJson")?
            || binding_digest
                != dependency_set_binding_digest(&descriptor_digest, &set_digest, byte_count)?
        {
            return Err(SupervisorError::new(CODE));
        }
        let dependency_binding = DependencySetBinding {
            descriptor_digest,
            set_digest,
            byte_count,
            binding_digest,
        };

        let fixture_set = required_object(root, "fixtureSet")?;
        require_exact_keys(
            fixture_set,
            &[
                "descriptorSetDigest",
                "fixtureSetDigest",
                "descriptors",
                "materializedRoots",
            ],
            CODE,
        )?;
        let descriptors = required_array(fixture_set, "descriptors")?;
        let materialized_roots = required_array(fixture_set, "materializedRoots")?;
        if descriptors.len() != FIXTURE_SCENARIOS.len()
            || materialized_roots.len() != FIXTURE_SCENARIOS.len()
        {
            return Err(SupervisorError::new(CODE));
        }

        let mut descriptor_digests = [[0u8; 32]; FIXTURE_SCENARIOS.len()];
        let mut fixture_digests = [[0u8; 32]; FIXTURE_SCENARIOS.len()];
        let mut descriptor_set_rows = Vec::with_capacity(FIXTURE_SCENARIOS.len());
        let mut fixture_set_rows = Vec::with_capacity(FIXTURE_SCENARIOS.len());
        for (index, expected_scenario) in FIXTURE_SCENARIOS.iter().enumerate() {
            let descriptor = descriptors[index]
                .as_object()
                .ok_or_else(|| SupervisorError::new(CODE))?;
            require_exact_keys(
                descriptor,
                &["scenarioId", "fileSha256", "descriptorDigest", "byteCount"],
                CODE,
            )?;
            if required_string(descriptor, "scenarioId")? != *expected_scenario {
                return Err(SupervisorError::new(CODE));
            }
            required_digest(descriptor, "fileSha256")?;
            let descriptor_digest = required_digest(descriptor, "descriptorDigest")?;
            required_bounded_count(descriptor, "byteCount", MAX_SOURCE_BYTES, false, CODE)?;

            let materialized_root = materialized_roots[index]
                .as_object()
                .ok_or_else(|| SupervisorError::new(CODE))?;
            require_exact_keys(
                materialized_root,
                &[
                    "scenarioId",
                    "fixtureDigest",
                    "baselineDigest",
                    "contentTreeDigest",
                    "sourceTree",
                ],
                CODE,
            )?;
            if required_string(materialized_root, "scenarioId")? != *expected_scenario {
                return Err(SupervisorError::new(CODE));
            }
            let fixture_digest = required_digest(materialized_root, "fixtureDigest")?;
            let baseline_digest = required_digest(materialized_root, "baselineDigest")?;
            let content_tree_digest = required_digest(materialized_root, "contentTreeDigest")?;
            let _ = validate_tree_source_record(
                materialized_root
                    .get("sourceTree")
                    .ok_or_else(|| SupervisorError::new(CODE))?,
            )?;
            let expected_fixture_digest = contract_json_digest(&serde_json::json!({
                "descriptorDigest": hex_lower(&descriptor_digest),
                "baselineDigest": hex_lower(&baseline_digest),
                "treeDigest": hex_lower(&content_tree_digest),
            }))?;
            if fixture_digest != expected_fixture_digest {
                return Err(SupervisorError::new(CODE));
            }

            descriptor_digests[index] = descriptor_digest;
            fixture_digests[index] = fixture_digest;
            descriptor_set_rows.push(serde_json::json!({
                "scenarioId": expected_scenario,
                "descriptorDigest": hex_lower(&descriptor_digest),
            }));
            fixture_set_rows.push(serde_json::json!({
                "scenarioId": expected_scenario,
                "digest": hex_lower(&fixture_digest),
            }));
        }

        let fixture_set_descriptor_digest = required_digest(fixture_set, "descriptorSetDigest")?;
        let fixture_set_digest = required_digest(fixture_set, "fixtureSetDigest")?;
        if fixture_set_descriptor_digest
            != contract_json_digest(&Value::Array(descriptor_set_rows))?
            || fixture_set_digest != contract_json_digest(&Value::Array(fixture_set_rows))?
        {
            return Err(SupervisorError::new(CODE));
        }

        let model_fixture = required_object(root, "modelFixture")?;
        require_exact_keys(
            model_fixture,
            &["scenarioId", "descriptorDigest", "fixtureDigest"],
            CODE,
        )?;
        let model_index = FIXTURE_SCENARIOS.len() - 1;
        let fixture_descriptor_digest = required_digest(model_fixture, "descriptorDigest")?;
        let fixture_digest = required_digest(model_fixture, "fixtureDigest")?;
        if required_string(model_fixture, "scenarioId")? != SCENARIO_ID
            || fixture_descriptor_digest != descriptor_digests[model_index]
            || fixture_digest != fixture_digests[model_index]
        {
            return Err(SupervisorError::new(CODE));
        }
        Ok((
            dependency_binding,
            ProtectedEvidenceManifestProjection {
                strict_manifest_digest: release_digests[0],
                portable_digest: release_digests[1],
                unity_package_digest: release_digests[2],
                backend_tree_digest: package_tree_digests[0],
                vrcforge_core_tree_digest: package_tree_digests[1],
                server_tree_digest: package_tree_digests[2],
                fixture_set_descriptor_digest,
                fixture_set_digest,
                fixture_descriptor_digest,
                fixture_digest,
            },
        ))
    })();
    validated.map_err(|_| SupervisorError::new(CODE))
}

fn validate_file_record(value: &Value, maximum: u64) -> Result<Digest, SupervisorError> {
    const CODE: &str = "authority_policy_package_contract_invalid";
    let record = value
        .as_object()
        .ok_or_else(|| SupervisorError::new(CODE))?;
    require_exact_keys(record, &["sha256", "byteCount"], CODE)?;
    let digest = required_digest(record, "sha256")?;
    required_bounded_count(record, "byteCount", maximum, false, CODE)?;
    Ok(digest)
}

fn validate_tree_source_record(value: &Value) -> Result<Digest, SupervisorError> {
    const CODE: &str = "authority_policy_package_contract_invalid";
    let record = value
        .as_object()
        .ok_or_else(|| SupervisorError::new(CODE))?;
    require_exact_keys(
        record,
        &[
            "schema",
            "treeDigest",
            "bindingDigest",
            "directoryCount",
            "entryCount",
            "byteCount",
        ],
        CODE,
    )?;
    if required_string(record, "schema")? != TREE_SOURCE_SCHEMA {
        return Err(SupervisorError::new(CODE));
    }
    let tree_digest = required_digest(record, "treeDigest")?;
    required_digest(record, "bindingDigest")?;
    required_bounded_count(
        record,
        "directoryCount",
        MAX_POLICY_TREE_ENTRIES,
        true,
        CODE,
    )?;
    required_bounded_count(record, "entryCount", MAX_POLICY_TREE_ENTRIES, false, CODE)?;
    required_bounded_count(record, "byteCount", MAX_POLICY_TREE_BYTES, false, CODE)?;
    Ok(tree_digest)
}

fn contract_json_digest(value: &Value) -> Result<Digest, SupervisorError> {
    let mut bytes = canonical_json_line(value)?;
    if bytes.pop() != Some(b'\n') || bytes.is_empty() {
        return Err(SupervisorError::new(
            "authority_policy_manifest_canonicalization_failed",
        ));
    }
    Ok(sha256(&bytes))
}

fn dependency_set_binding_digest(
    descriptor_digest: &Digest,
    set_digest: &Digest,
    byte_count: u64,
) -> Result<Digest, SupervisorError> {
    contract_json_digest(&serde_json::json!({
        "descriptorSchema": DEPENDENCY_SET_SCHEMA,
        "setDigest": hex_lower(set_digest),
        "descriptorSha256": hex_lower(descriptor_digest),
        "byteCount": byte_count,
        "canonicalJson": true,
    }))
}

fn fixed_model_part_readback_from_parts(
    package: &ProtectedRuntimeSourceReadback,
    candidate_executables: [HeldFileReadback; 6],
    fixture_contract: HeldFileReadback,
    contract_bytes: &[u8],
    fixture_baseline: HeldFileReadback,
    baseline_bytes: &[u8],
) -> Result<FixedModelPartHeldReadback, SupervisorError> {
    if candidate_executables.iter().any(|held| !held.valid())
        || !fixture_contract.valid()
        || !fixture_baseline.valid()
        || package.role_digests[1..]
            .iter()
            .zip(&package.role_byte_counts[1..])
            .zip(&candidate_executables)
            .any(|((expected_digest, expected_length), held)| {
                *expected_digest != held.content_digest || *expected_length != held.byte_length
            })
        || package.source_digests[0] != fixture_contract.content_digest
        || package.source_byte_counts[0] != fixture_contract.byte_length
        || package.source_digests[1] != fixture_baseline.content_digest
        || package.source_byte_counts[1] != fixture_baseline.byte_length
        || fixture_contract.content_digest != sha256(contract_bytes)
        || fixture_contract.byte_length != contract_bytes.len() as u64
        || fixture_baseline.content_digest != sha256(baseline_bytes)
        || fixture_baseline.byte_length != baseline_bytes.len() as u64
    {
        return Err(SupervisorError::new(
            "authority_policy_held_source_mismatch",
        ));
    }
    let mut identities = BTreeSet::new();
    if !identities.insert(package.manifest_identity_digest)
        || candidate_executables
            .iter()
            .chain([&fixture_contract, &fixture_baseline])
            .any(|held| !identities.insert(held.identity_digest))
    {
        return Err(SupervisorError::new(
            "authority_policy_held_source_identity_alias",
        ));
    }
    let expected_tree_digest = validate_fixed_model_part_fixture_documents(
        contract_bytes,
        baseline_bytes,
        &fixture_contract,
    )?;
    let scenario_binding_digest = scenario_binding(
        package,
        &candidate_executables,
        &fixture_contract,
        &fixture_baseline,
        &expected_tree_digest,
    );
    Ok(FixedModelPartHeldReadback {
        candidate_executables,
        fixture_contract,
        fixture_baseline,
        expected_tree_digest,
        scenario_binding_digest,
    })
}

fn validate_fixed_model_part_fixture_documents(
    contract_bytes: &[u8],
    baseline_bytes: &[u8],
    fixture_contract: &HeldFileReadback,
) -> Result<Digest, SupervisorError> {
    let contract = parse_strict_json(contract_bytes)
        .map_err(|_| SupervisorError::new("authority_policy_fixture_contract_invalid"))?;
    validate_fixed_model_part_fixture_contract(&contract)?;
    let baseline = parse_strict_json(baseline_bytes)
        .map_err(|_| SupervisorError::new("authority_policy_fixture_baseline_invalid"))?;
    validate_fixed_model_part_fixture_baseline(&baseline, fixture_contract)
}

fn validate_fixed_model_part_fixture_contract(value: &Value) -> Result<(), SupervisorError> {
    let validated: Result<(), SupervisorError> = (|| {
        let contract = value
            .as_object()
            .ok_or_else(|| SupervisorError::new("fixture_contract_not_object"))?;
        require_exact_keys(
            contract,
            &[
                "schema",
                "scenarioId",
                "primitiveId",
                "unity",
                "scene",
                "projectFiles",
                "requiredPackages",
                "runtime",
            ],
            "fixture_contract_fields_invalid",
        )?;
        if required_string(contract, "schema")? != FIXTURE_SCHEMA
            || required_string(contract, "scenarioId")? != SCENARIO_ID
            || required_string(contract, "primitiveId")? != REQUIRED_PRIMITIVE
        {
            return Err(SupervisorError::new("fixture_contract_identity_invalid"));
        }

        let unity = required_object(contract, "unity")?;
        require_exact_keys(
            unity,
            &["version", "revision"],
            "fixture_contract_unity_fields_invalid",
        )?;
        if required_string(unity, "version")? != FIXTURE_UNITY_VERSION
            || required_string(unity, "revision")? != FIXTURE_UNITY_REVISION
        {
            return Err(SupervisorError::new("fixture_contract_unity_invalid"));
        }

        let scene = required_object(contract, "scene")?;
        require_exact_keys(
            scene,
            &[
                "assetPath",
                "guid",
                "avatarPath",
                "baseArmaturePath",
                "partRootPath",
                "componentHostPath",
                "mergeTargetPath",
                "rendererPath",
                "baselineComponentCount",
            ],
            "fixture_contract_scene_fields_invalid",
        )?;
        if required_string(scene, "assetPath")? != FIXTURE_SCENE_ASSET_PATH
            || required_string(scene, "guid")? != FIXTURE_SCENE_GUID
            || required_string(scene, "avatarPath")? != FIXTURE_AVATAR_PATH
            || required_string(scene, "baseArmaturePath")? != FIXTURE_BASE_ARMATURE_PATH
            || required_string(scene, "partRootPath")? != FIXTURE_PART_ROOT_PATH
            || required_string(scene, "componentHostPath")? != FIXTURE_COMPONENT_HOST_PATH
            || required_string(scene, "mergeTargetPath")? != FIXTURE_MERGE_TARGET_PATH
            || required_string(scene, "rendererPath")? != FIXTURE_RENDERER_PATH
            || required_u64(scene, "baselineComponentCount")? != 0
        {
            return Err(SupervisorError::new("fixture_contract_scene_invalid"));
        }

        let project_files = required_array(contract, "projectFiles")?;
        if project_files.len() != FIXTURE_PROJECT_FILES.len() {
            return Err(SupervisorError::new(
                "fixture_contract_project_files_invalid",
            ));
        }
        for (entry, (expected_path, expected_sha256)) in
            project_files.iter().zip(FIXTURE_PROJECT_FILES)
        {
            let entry = entry
                .as_object()
                .ok_or_else(|| SupervisorError::new("fixture_project_file_not_object"))?;
            require_exact_keys(
                entry,
                &["path", "sha256"],
                "fixture_contract_project_file_fields_invalid",
            )?;
            if required_string(entry, "path")? != expected_path
                || required_string(entry, "sha256")? != expected_sha256
            {
                return Err(SupervisorError::new(
                    "fixture_contract_project_file_invalid",
                ));
            }
        }

        let required_packages = required_array(contract, "requiredPackages")?;
        if required_packages.len() != FIXTURE_REQUIRED_PACKAGES.len() {
            return Err(SupervisorError::new(
                "fixture_contract_required_packages_invalid",
            ));
        }
        for (entry, (expected_id, expected_version)) in
            required_packages.iter().zip(FIXTURE_REQUIRED_PACKAGES)
        {
            let entry = entry
                .as_object()
                .ok_or_else(|| SupervisorError::new("fixture_package_not_object"))?;
            require_exact_keys(
                entry,
                &["id", "version", "provisioning"],
                "fixture_contract_package_fields_invalid",
            )?;
            if required_string(entry, "id")? != expected_id
                || required_string(entry, "version")? != expected_version
                || required_string(entry, "provisioning")? != FIXTURE_PACKAGE_PROVISIONING
            {
                return Err(SupervisorError::new("fixture_contract_package_invalid"));
            }
        }

        let runtime = required_object(contract, "runtime")?;
        require_exact_keys(
            runtime,
            &["bootstrapType", "runIdEnvironment", "readyMarker"],
            "fixture_contract_runtime_fields_invalid",
        )?;
        if required_string(runtime, "bootstrapType")? != FIXTURE_BOOTSTRAP_TYPE
            || required_string(runtime, "runIdEnvironment")? != FIXTURE_RUN_ID_ENVIRONMENT
            || required_string(runtime, "readyMarker")? != FIXTURE_READY_MARKER
        {
            return Err(SupervisorError::new("fixture_contract_runtime_invalid"));
        }
        Ok(())
    })();
    validated.map_err(|_| SupervisorError::new("authority_policy_fixture_contract_invalid"))
}

fn validate_fixed_model_part_fixture_baseline(
    value: &Value,
    fixture_contract: &HeldFileReadback,
) -> Result<Digest, SupervisorError> {
    let validated: Result<Digest, SupervisorError> = (|| {
        let baseline = value
            .as_object()
            .ok_or_else(|| SupervisorError::new("fixture_baseline_not_object"))?;
        require_exact_keys(
            baseline,
            &["schema", "scenarioId", "files"],
            "fixture_baseline_fields_invalid",
        )?;
        if required_string(baseline, "schema")? != FIXTURE_BASELINE_SCHEMA
            || required_string(baseline, "scenarioId")? != SCENARIO_ID
        {
            return Err(SupervisorError::new("fixture_baseline_identity_invalid"));
        }
        let files = required_array(baseline, "files")?;
        if files.len() != FIXTURE_BASELINE_FILES.len() {
            return Err(SupervisorError::new("fixture_baseline_files_invalid"));
        }
        for (index, (entry, (expected_path, expected_size, expected_sha256))) in
            files.iter().zip(FIXTURE_BASELINE_FILES).enumerate()
        {
            let entry = entry
                .as_object()
                .ok_or_else(|| SupervisorError::new("fixture_baseline_file_not_object"))?;
            require_exact_keys(
                entry,
                &["path", "size", "sha256"],
                "fixture_baseline_file_fields_invalid",
            )?;
            if required_string(entry, "path")? != expected_path
                || required_u64(entry, "size")? != expected_size
                || required_string(entry, "sha256")? != expected_sha256
            {
                return Err(SupervisorError::new("fixture_baseline_file_invalid"));
            }
            if index == 6
                && (required_u64(entry, "size")? != fixture_contract.byte_length
                    || required_digest(entry, "sha256")? != fixture_contract.content_digest)
            {
                return Err(SupervisorError::new(
                    "fixture_baseline_contract_binding_invalid",
                ));
            }
        }
        if FIXTURE_SCENE_ASSET_PATH
            .strip_prefix("Assets/VRCForge/PrimitiveBasis/model_part_composition/")
            != Some(FIXTURE_BASELINE_FILES[3].0)
            || FIXTURE_BASELINE_FILES[1].0 != "Editor/ModelPartCompositionFixtureBootstrap.cs"
        {
            return Err(SupervisorError::new(
                "fixture_baseline_contract_binding_invalid",
            ));
        }
        let mut canonical_files = canonical_json_line(&Value::Array(files.clone()))?;
        if canonical_files.pop() != Some(b'\n') || canonical_files.is_empty() {
            return Err(SupervisorError::new(
                "fixture_baseline_canonicalization_invalid",
            ));
        }
        let tree_digest = sha256(&canonical_files);
        if decode_digest(FIXTURE_TREE_DIGEST_HEX) != Some(tree_digest) {
            return Err(SupervisorError::new("fixture_baseline_tree_invalid"));
        }
        Ok(tree_digest)
    })();
    validated.map_err(|_| SupervisorError::new("authority_policy_fixture_baseline_invalid"))
}

fn validate_final_commit_binding(
    final_commit: &AuthenticatedFinalCommitPolicyBinding,
    runtime_identity: &AuthorityRuntimeIdentity,
) -> Result<(), SupervisorError> {
    let required = [
        final_commit.generation(),
        final_commit.signer_key_id(),
        final_commit.protected_manifest_sha256(),
        final_commit.installed_layout_sha256(),
        final_commit.exact_service_configuration_sha256(),
        final_commit.service_binary_sha256(),
        final_commit.final_commit_receipt_sha256(),
    ];
    if required.into_iter().any(is_zero)
        || is_zero(&final_commit.published_runtime_binding_sha256())
        || is_zero(final_commit.runtime_source_manifest().sha256())
        || final_commit.runtime_source_manifest().byte_length() == 0
        || final_commit.service_process_id() == 0
        || final_commit.service_process_creation_time() == 0
        || runtime_identity.authority_generation_digest() != final_commit.generation()
        || runtime_identity.signer_key_id() != final_commit.signer_key_id()
        || runtime_identity.protected_manifest_digest() != final_commit.protected_manifest_sha256()
        || runtime_identity.installed_layout_digest() != final_commit.installed_layout_sha256()
        || runtime_identity.service_binary_digest() != final_commit.service_binary_sha256()
    {
        return Err(SupervisorError::new(
            "authority_policy_final_commit_identity_mismatch",
        ));
    }
    Ok(())
}

fn validate_package_binding(
    final_commit: &AuthenticatedFinalCommitPolicyBinding,
    package: &ProtectedRuntimeSourceReadback,
) -> Result<(), SupervisorError> {
    let expected_dependency_binding = dependency_set_binding_digest(
        &package.dependency_set.descriptor_digest,
        &package.dependency_set.set_digest,
        package.dependency_set.byte_count,
    )?;
    if is_zero(&package.manifest_digest)
        || is_zero(&package.manifest_identity_digest)
        || is_zero(&package.source_commit_digest)
        || is_zero(&package.bridge_target_manifest_digest)
        || is_zero(&package.bridge_target_tree_digest)
        || is_zero(&package.dependency_set.descriptor_digest)
        || is_zero(&package.dependency_set.set_digest)
        || package.dependency_set.byte_count == 0
        || package.dependency_set.byte_count > MAX_SOURCE_BYTES
        || package.dependency_set.binding_digest != expected_dependency_binding
        || package.role_digests.iter().any(is_zero)
        || package
            .role_byte_counts
            .iter()
            .any(|count| *count == 0 || *count > MAX_EXECUTABLE_BYTES)
        || package.source_digests.iter().any(is_zero)
        || package
            .source_byte_counts
            .iter()
            .any(|count| *count == 0 || *count > MAX_SOURCE_BYTES)
        || package.role_digests[0] != *final_commit.service_binary_sha256()
        || package.binding_digest
            != runtime_source_binding(
                final_commit,
                &package.manifest_digest,
                &package.manifest_identity_digest,
                &package.source_commit_digest,
                &package.bridge_target_manifest_digest,
                &package.bridge_target_tree_digest,
                &package.role_digests,
                &package.role_byte_counts,
                &package.source_digests,
                &package.source_byte_counts,
                &package.dependency_set,
            )
    {
        return Err(SupervisorError::new(
            "authority_policy_package_binding_invalid",
        ));
    }
    Ok(())
}

fn validate_scenario_binding(
    package: &ProtectedRuntimeSourceReadback,
    scenario: &FixedModelPartHeldReadback,
) -> Result<(), SupervisorError> {
    if scenario
        .candidate_executables
        .iter()
        .any(|held| !held.valid())
        || !scenario.fixture_contract.valid()
        || !scenario.fixture_baseline.valid()
        || is_zero(&scenario.expected_tree_digest)
        || scenario.scenario_binding_digest
            != scenario_binding(
                package,
                &scenario.candidate_executables,
                &scenario.fixture_contract,
                &scenario.fixture_baseline,
                &scenario.expected_tree_digest,
            )
    {
        return Err(SupervisorError::new(
            "authority_policy_scenario_binding_invalid",
        ));
    }
    Ok(())
}

fn validate_strict_build_policy(policy: &Map<String, Value>) -> Result<(), SupervisorError> {
    require_exact_keys(
        policy,
        &[
            "mode",
            "releaseEligible",
            "evidenceEligible",
            "allowDirty",
            "allowUnpushed",
            "allowVersionMismatch",
        ],
        "authority_policy_build_policy_invalid",
    )?;
    if required_string(policy, "mode")? != STRICT_BUILD_MODE
        || required_bool(policy, "releaseEligible")?
        || !required_bool(policy, "evidenceEligible")?
        || required_bool(policy, "allowDirty")?
        || required_bool(policy, "allowUnpushed")?
        || required_bool(policy, "allowVersionMismatch")?
    {
        return Err(SupervisorError::new(
            "authority_policy_build_policy_invalid",
        ));
    }
    Ok(())
}

fn read_held_file(
    file: &File,
    maximum: u64,
) -> Result<(HeldFileReadback, Vec<u8>), SupervisorError> {
    read_held_file_after_read(file, maximum, || {})
}

fn read_held_file_after_read<F>(
    file: &File,
    maximum: u64,
    after_read: F,
) -> Result<(HeldFileReadback, Vec<u8>), SupervisorError>
where
    F: FnOnce(),
{
    let information = query_held_file_information(file)?;
    let byte_length =
        (u64::from(information.nFileSizeHigh) << 32) | u64::from(information.nFileSizeLow);
    let volume_serial = information.dwVolumeSerialNumber;
    let file_index =
        (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow);
    if information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY != 0
        || byte_length == 0
        || byte_length > maximum
        || volume_serial == 0
        || file_index == 0
        || information.nNumberOfLinks != 1
        || information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT_VALUE != 0
    {
        return Err(SupervisorError::new("authority_policy_held_file_invalid"));
    }
    let capacity = usize::try_from(byte_length)
        .map_err(|_| SupervisorError::new("authority_policy_held_file_invalid"))?;
    let mut bytes = Vec::with_capacity(capacity);
    let mut offset = 0u64;
    let mut buffer = [0u8; 64 * 1024];
    while offset < byte_length {
        let remaining = usize::try_from((byte_length - offset).min(buffer.len() as u64)).unwrap();
        let count = file
            .seek_read(&mut buffer[..remaining], offset)
            .map_err(|_| SupervisorError::new("authority_policy_held_file_read_failed"))?;
        if count == 0 {
            return Err(SupervisorError::new(
                "authority_policy_held_file_read_failed",
            ));
        }
        bytes.extend_from_slice(&buffer[..count]);
        offset = offset
            .checked_add(count as u64)
            .ok_or_else(|| SupervisorError::new("authority_policy_held_file_read_failed"))?;
    }
    if offset != byte_length || bytes.len() != capacity {
        return Err(SupervisorError::new(
            "authority_policy_held_file_read_failed",
        ));
    }
    after_read();
    let post_read_information = query_held_file_information(file)?;
    if !held_file_information_matches(&information, &post_read_information) {
        return Err(SupervisorError::new("authority_policy_held_file_changed"));
    }
    let content_digest = sha256(&bytes);
    let identity_digest = derive(
        HELD_FILE_IDENTITY_DOMAIN,
        &[
            &volume_serial.to_be_bytes(),
            &file_index.to_be_bytes(),
            &filetime_value(information.ftCreationTime).to_be_bytes(),
            &filetime_value(information.ftLastWriteTime).to_be_bytes(),
            &byte_length.to_be_bytes(),
            &content_digest,
        ],
    );
    Ok((
        HeldFileReadback {
            content_digest,
            byte_length,
            identity_digest,
        },
        bytes,
    ))
}

fn query_held_file_information(file: &File) -> Result<BY_HANDLE_FILE_INFORMATION, SupervisorError> {
    let mut information: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle().cast(), &mut information) } == 0 {
        return Err(SupervisorError::new(
            "authority_policy_held_file_identity_unavailable",
        ));
    }
    Ok(information)
}

fn held_file_information_matches(
    before: &BY_HANDLE_FILE_INFORMATION,
    after: &BY_HANDLE_FILE_INFORMATION,
) -> bool {
    before.dwFileAttributes == after.dwFileAttributes
        && before.dwVolumeSerialNumber == after.dwVolumeSerialNumber
        && before.nFileSizeHigh == after.nFileSizeHigh
        && before.nFileSizeLow == after.nFileSizeLow
        && before.nNumberOfLinks == after.nNumberOfLinks
        && before.nFileIndexHigh == after.nFileIndexHigh
        && before.nFileIndexLow == after.nFileIndexLow
        && filetime_value(before.ftCreationTime) == filetime_value(after.ftCreationTime)
        && filetime_value(before.ftLastWriteTime) == filetime_value(after.ftLastWriteTime)
}

fn filetime_value(value: FILETIME) -> u64 {
    (u64::from(value.dwHighDateTime) << 32) | u64::from(value.dwLowDateTime)
}

fn process_key_from_handle(handle: BorrowedHandle<'_>) -> Result<ProcessKey, SupervisorError> {
    let raw = handle.as_raw_handle().cast();
    let pid = unsafe { GetProcessId(raw) };
    let mut creation = FILETIME {
        dwLowDateTime: 0,
        dwHighDateTime: 0,
    };
    let mut exit = creation;
    let mut kernel = creation;
    let mut user = creation;
    if pid == 0
        || unsafe { GetProcessTimes(raw, &mut creation, &mut exit, &mut kernel, &mut user) } == 0
    {
        return Err(SupervisorError::new(
            "authority_policy_process_readback_failed",
        ));
    }
    let creation_time =
        (u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime);
    if creation_time == 0 {
        return Err(SupervisorError::new(
            "authority_policy_process_readback_failed",
        ));
    }
    Ok(ProcessKey { pid, creation_time })
}

fn runtime_source_binding(
    final_commit: &AuthenticatedFinalCommitPolicyBinding,
    manifest_digest: &Digest,
    manifest_identity_digest: &Digest,
    source_commit_digest: &Digest,
    bridge_target_manifest_digest: &Digest,
    bridge_target_tree_digest: &Digest,
    role_digests: &[Digest; PROCESS_ROLES.len()],
    role_byte_counts: &[u64; PROCESS_ROLES.len()],
    source_digests: &[Digest; SOURCE_NAMES.len()],
    source_byte_counts: &[u64; SOURCE_NAMES.len()],
    dependency_set: &DependencySetBinding,
) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(RUNTIME_SOURCE_BINDING_DOMAIN);
    hasher.update(final_commit_binding(final_commit));
    hasher.update(manifest_digest);
    hasher.update(manifest_identity_digest);
    hasher.update(source_commit_digest);
    hasher.update(bridge_target_manifest_digest);
    hasher.update(bridge_target_tree_digest);
    for (digest, byte_count) in role_digests.iter().zip(role_byte_counts) {
        hasher.update(digest);
        hasher.update(byte_count.to_be_bytes());
    }
    for (digest, byte_count) in source_digests.iter().zip(source_byte_counts) {
        hasher.update(digest);
        hasher.update(byte_count.to_be_bytes());
    }
    hasher.update(DEPENDENCY_SET_SCHEMA.as_bytes());
    hasher.update(dependency_set.descriptor_digest);
    hasher.update(dependency_set.set_digest);
    hasher.update(dependency_set.byte_count.to_be_bytes());
    hasher.update(dependency_set.binding_digest);
    hasher.finalize().into()
}

fn final_commit_binding(final_commit: &AuthenticatedFinalCommitPolicyBinding) -> Digest {
    derive(
        FINAL_COMMIT_BINDING_DOMAIN,
        &[
            final_commit.generation(),
            final_commit.signer_key_id(),
            final_commit.protected_manifest_sha256(),
            final_commit.installed_layout_sha256(),
            final_commit.exact_service_configuration_sha256(),
            final_commit.service_binary_sha256(),
            &final_commit.service_process_id().to_be_bytes(),
            &final_commit.service_process_creation_time().to_be_bytes(),
            final_commit.final_commit_receipt_sha256(),
            &final_commit.published_runtime_binding_sha256(),
            final_commit.runtime_source_manifest().sha256(),
            &final_commit
                .runtime_source_manifest()
                .byte_length()
                .to_be_bytes(),
        ],
    )
}

fn scenario_binding(
    package: &ProtectedRuntimeSourceReadback,
    executables: &[HeldFileReadback; 6],
    fixture_contract: &HeldFileReadback,
    fixture_baseline: &HeldFileReadback,
    expected_tree_digest: &Digest,
) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(SCENARIO_BINDING_DOMAIN);
    hasher.update(package.binding_digest);
    hasher.update(SCENARIO_ID.as_bytes());
    for executable in executables {
        hasher.update(executable.content_digest);
        hasher.update(executable.byte_length.to_be_bytes());
        hasher.update(executable.identity_digest);
    }
    for source in [fixture_contract, fixture_baseline] {
        hasher.update(source.content_digest);
        hasher.update(source.byte_length.to_be_bytes());
        hasher.update(source.identity_digest);
    }
    hasher.update(expected_tree_digest);
    hasher.finalize().into()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ChildTransportSlotContractProjection {
    pub(super) ordinal: u16,
    pub(super) semantic: u16,
    pub(super) purpose: u8,
    pub(super) readable: bool,
    pub(super) writable: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ChildTransportRoleContractProjection {
    pub(super) role: ChildBootstrapRole,
    pub(super) executable_identity_digest: Digest,
    pub(super) executable_content_digest: Digest,
    pub(super) executable_byte_length: u64,
    pub(super) slots: [ChildTransportSlotContractProjection; CHILD_STANDARD_HANDLE_SLOT_COUNT],
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ChildTransportContractProjection {
    pub(super) manifest_identity_digest: Digest,
    pub(super) global_source_identities: [Digest; GLOBAL_CAPABILITY_SOURCE_COUNT],
    pub(super) roles: [ChildTransportRoleContractProjection; 2],
}

impl ChildTransportContractProjection {
    pub(crate) fn global_source_identities(&self) -> &[Digest; GLOBAL_CAPABILITY_SOURCE_COUNT] {
        &self.global_source_identities
    }

    pub(super) fn validates_fixed_contract(&self) -> bool {
        if is_zero(&self.manifest_identity_digest)
            || self.global_source_identities.iter().any(is_zero)
            || self
                .global_source_identities
                .iter()
                .enumerate()
                .any(|(index, value)| self.global_source_identities[..index].contains(value))
        {
            return false;
        }
        for (expected_role, role) in [
            ChildBootstrapRole::LifecycleDriver,
            ChildBootstrapRole::BridgeLauncher,
        ]
        .into_iter()
        .zip(&self.roles)
        {
            if role.role != expected_role
                || is_zero(&role.executable_identity_digest)
                || is_zero(&role.executable_content_digest)
                || role.executable_byte_length == 0
            {
                return false;
            }
            let schema = child_role_capability_schema(expected_role);
            for (index, (slot, descriptor)) in role.slots.iter().zip(schema).enumerate() {
                let access = descriptor.purpose().access_contract();
                if slot.ordinal != index as u16
                    || slot.semantic != descriptor.semantic().wire_value()
                    || slot.purpose != descriptor.purpose().wire_value()
                    || slot.readable != access.readable()
                    || slot.writable != access.writable()
                {
                    return false;
                }
            }
        }
        true
    }
}

fn child_transport_contract_digest(
    package: &ProtectedRuntimeSourceReadback,
    scenario: &FixedModelPartHeldReadback,
) -> Digest {
    child_transport_contract_digest_from_projection(&child_transport_contract_projection(
        package, scenario,
    ))
}

fn child_transport_contract_projection(
    package: &ProtectedRuntimeSourceReadback,
    scenario: &FixedModelPartHeldReadback,
) -> ChildTransportContractProjection {
    let global_source_identities = [
        scenario.candidate_executables[0].identity_digest,
        scenario.candidate_executables[1].identity_digest,
        scenario.candidate_executables[2].identity_digest,
        scenario.candidate_executables[3].identity_digest,
        scenario.candidate_executables[4].identity_digest,
        scenario.candidate_executables[5].identity_digest,
        scenario.fixture_contract.identity_digest,
        scenario.fixture_baseline.identity_digest,
    ];
    let roles = [
        child_transport_role_projection(
            ChildBootstrapRole::LifecycleDriver,
            &scenario.candidate_executables[role_index(ProcessRole::Driver) - 1],
        ),
        child_transport_role_projection(
            ChildBootstrapRole::BridgeLauncher,
            &scenario.candidate_executables[role_index(ProcessRole::BridgeLauncher) - 1],
        ),
    ];
    ChildTransportContractProjection {
        manifest_identity_digest: package.manifest_identity_digest,
        global_source_identities,
        roles,
    }
}

fn child_transport_role_projection(
    role: ChildBootstrapRole,
    executable: &HeldFileReadback,
) -> ChildTransportRoleContractProjection {
    let schema = child_role_capability_schema(role);
    let slots = std::array::from_fn(|index| {
        let descriptor = schema[index];
        let purpose = descriptor.purpose();
        let access = purpose.access_contract();
        ChildTransportSlotContractProjection {
            ordinal: index as u16,
            semantic: descriptor.semantic().wire_value(),
            purpose: purpose.wire_value(),
            readable: access.readable(),
            writable: access.writable(),
        }
    });
    ChildTransportRoleContractProjection {
        role,
        executable_identity_digest: executable.identity_digest,
        executable_content_digest: executable.content_digest,
        executable_byte_length: executable.byte_length,
        slots,
    }
}

pub(super) fn child_transport_contract_digest_from_projection(
    projection: &ChildTransportContractProjection,
) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(CHILD_TRANSPORT_CONTRACT_DOMAIN);
    hasher.update(projection.manifest_identity_digest);
    hasher.update((GLOBAL_CAPABILITY_SOURCE_COUNT as u16).to_be_bytes());
    for ((ordinal, role_name), identity_digest) in GLOBAL_CAPABILITY_SOURCE_ROLES
        .iter()
        .enumerate()
        .zip(&projection.global_source_identities)
    {
        hasher.update((ordinal as u16).to_be_bytes());
        hasher.update((role_name.len() as u16).to_be_bytes());
        hasher.update(role_name.as_bytes());
        hasher.update(identity_digest);
    }
    hasher.update((projection.roles.len() as u16).to_be_bytes());
    for role in &projection.roles {
        hasher.update([role.role.wire_value()]);
        hasher.update(role.executable_identity_digest);
        hasher.update(role.executable_content_digest);
        hasher.update(role.executable_byte_length.to_be_bytes());
        hasher.update((role.slots.len() as u16).to_be_bytes());
        for slot in role.slots {
            hasher.update(slot.ordinal.to_be_bytes());
            hasher.update(slot.semantic.to_be_bytes());
            hasher.update([slot.purpose]);
            hasher.update([u8::from(slot.readable), u8::from(slot.writable)]);
        }
    }
    hasher.finalize().into()
}

#[cfg(test)]
pub(super) fn child_transport_contract_projection_for_test(
    seed: Digest,
) -> ChildTransportContractProjection {
    let source = |tag: u8| {
        derive(
            b"vrcforge-child-transport-test-projection-v1\0",
            &[&seed, &[tag]],
        )
    };
    let role = |role: ChildBootstrapRole, tag: u8| ChildTransportRoleContractProjection {
        role,
        executable_identity_digest: source(tag),
        executable_content_digest: source(tag.wrapping_add(1)),
        executable_byte_length: 1_024 + u64::from(tag),
        slots: std::array::from_fn(|index| {
            let descriptor = child_role_capability_schema(role)[index];
            let access = descriptor.purpose().access_contract();
            ChildTransportSlotContractProjection {
                ordinal: index as u16,
                semantic: descriptor.semantic().wire_value(),
                purpose: descriptor.purpose().wire_value(),
                readable: access.readable(),
                writable: access.writable(),
            }
        }),
    };
    let projection = ChildTransportContractProjection {
        manifest_identity_digest: source(1),
        global_source_identities: std::array::from_fn(|index| source(10 + index as u8)),
        roles: [
            role(ChildBootstrapRole::LifecycleDriver, 30),
            role(ChildBootstrapRole::BridgeLauncher, 40),
        ],
    };
    assert!(projection.validates_fixed_contract());
    projection
}

fn held_process_binding(service: &ProcessKey, parent: &ProcessKey) -> Digest {
    derive(
        HELD_PROCESS_BINDING_DOMAIN,
        &[
            &service.pid.to_be_bytes(),
            &service.creation_time.to_be_bytes(),
            &parent.pid.to_be_bytes(),
            &parent.creation_time.to_be_bytes(),
        ],
    )
}

fn artifact_binding(label: &[u8], held: &HeldFileReadback) -> Digest {
    derive(
        ARTIFACT_BINDING_DOMAIN,
        &[
            label,
            &held.content_digest,
            &held.byte_length.to_be_bytes(),
            &held.identity_digest,
        ],
    )
}

fn derive(domain: &[u8], parts: &[&[u8]]) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(domain);
    for part in parts {
        hasher.update((part.len() as u64).to_be_bytes());
        hasher.update(part);
    }
    hasher.finalize().into()
}

fn sha256(bytes: &[u8]) -> Digest {
    Sha256::digest(bytes).into()
}

fn is_zero(value: &Digest) -> bool {
    value.iter().all(|byte| *byte == 0)
}

fn hex_lower(value: &Digest) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(64);
    for byte in value {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn decode_digest(value: &str) -> Option<Digest> {
    if !is_lower_hex(value, 64) || value.bytes().all(|byte| byte == b'0') {
        return None;
    }
    let mut output = [0u8; 32];
    for (index, chunk) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (hex_nibble(chunk[0])? << 4) | hex_nibble(chunk[1])?;
    }
    Some(output)
}

fn hex_nibble(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        _ => None,
    }
}

fn is_lower_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn required_object<'a>(
    object: &'a Map<String, Value>,
    key: &str,
) -> Result<&'a Map<String, Value>, SupervisorError> {
    object
        .get(key)
        .and_then(Value::as_object)
        .ok_or_else(|| SupervisorError::new("authority_policy_manifest_invalid"))
}

fn required_array<'a>(
    object: &'a Map<String, Value>,
    key: &str,
) -> Result<&'a Vec<Value>, SupervisorError> {
    object
        .get(key)
        .and_then(Value::as_array)
        .ok_or_else(|| SupervisorError::new("authority_policy_manifest_invalid"))
}

fn required_string<'a>(
    object: &'a Map<String, Value>,
    key: &str,
) -> Result<&'a str, SupervisorError> {
    object
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| SupervisorError::new("authority_policy_manifest_invalid"))
}

fn required_bool(object: &Map<String, Value>, key: &str) -> Result<bool, SupervisorError> {
    object
        .get(key)
        .and_then(Value::as_bool)
        .ok_or_else(|| SupervisorError::new("authority_policy_manifest_invalid"))
}

fn required_u64(object: &Map<String, Value>, key: &str) -> Result<u64, SupervisorError> {
    object
        .get(key)
        .and_then(Value::as_u64)
        .ok_or_else(|| SupervisorError::new("authority_policy_manifest_invalid"))
}

fn required_bounded_count(
    object: &Map<String, Value>,
    key: &str,
    maximum: u64,
    allow_zero: bool,
    code: &'static str,
) -> Result<u64, SupervisorError> {
    let value = required_u64(object, key).map_err(|_| SupervisorError::new(code))?;
    if value > maximum || (!allow_zero && value == 0) {
        return Err(SupervisorError::new(code));
    }
    Ok(value)
}

fn required_digest(object: &Map<String, Value>, key: &str) -> Result<Digest, SupervisorError> {
    required_string(object, key)
        .ok()
        .and_then(decode_digest)
        .ok_or_else(|| SupervisorError::new("authority_policy_digest_invalid"))
}

fn require_exact_keys(
    object: &Map<String, Value>,
    expected: &[&str],
    code: &'static str,
) -> Result<(), SupervisorError> {
    let actual = object.keys().map(String::as_str).collect::<BTreeSet<_>>();
    let expected = expected.iter().copied().collect::<BTreeSet<_>>();
    if actual != expected {
        return Err(SupervisorError::new(code));
    }
    Ok(())
}

struct StrictJsonValue(Value);

impl<'de> Deserialize<'de> for StrictJsonValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_any(StrictJsonVisitor)
    }
}

struct StrictJsonVisitor;

impl<'de> Visitor<'de> for StrictJsonVisitor {
    type Value = StrictJsonValue;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("strict JSON without duplicate keys or floating-point values")
    }

    fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E> {
        Ok(StrictJsonValue(Value::Bool(value)))
    }

    fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E> {
        Ok(StrictJsonValue(Value::Number(Number::from(value))))
    }

    fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E> {
        Ok(StrictJsonValue(Value::Number(Number::from(value))))
    }

    fn visit_f64<E>(self, _value: f64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Err(E::custom("floating-point values are not accepted"))
    }

    fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Ok(StrictJsonValue(Value::String(value.to_owned())))
    }

    fn visit_string<E>(self, value: String) -> Result<Self::Value, E> {
        Ok(StrictJsonValue(Value::String(value)))
    }

    fn visit_none<E>(self) -> Result<Self::Value, E> {
        Ok(StrictJsonValue(Value::Null))
    }

    fn visit_unit<E>(self) -> Result<Self::Value, E> {
        Ok(StrictJsonValue(Value::Null))
    }

    fn visit_seq<A>(self, mut sequence: A) -> Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let mut values = Vec::new();
        while let Some(value) = sequence.next_element::<StrictJsonValue>()? {
            values.push(value.0);
        }
        Ok(StrictJsonValue(Value::Array(values)))
    }

    fn visit_map<A>(self, mut map: A) -> Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut values = Map::new();
        while let Some((key, value)) = map.next_entry::<String, StrictJsonValue>()? {
            if values.insert(key, value.0).is_some() {
                return Err(de::Error::custom("duplicate object key"));
            }
        }
        Ok(StrictJsonValue(Value::Object(values)))
    }
}

fn parse_strict_json(bytes: &[u8]) -> Result<Value, SupervisorError> {
    let mut deserializer = serde_json::Deserializer::from_slice(bytes);
    let value = StrictJsonValue::deserialize(&mut deserializer)
        .map_err(|_| SupervisorError::new("authority_policy_strict_json_invalid"))?;
    deserializer
        .end()
        .map_err(|_| SupervisorError::new("authority_policy_strict_json_invalid"))?;
    Ok(value.0)
}

fn canonical_json_line(value: &Value) -> Result<Vec<u8>, SupervisorError> {
    fn write_value(value: &Value, output: &mut Vec<u8>) -> Result<(), SupervisorError> {
        match value {
            Value::Array(values) => {
                output.push(b'[');
                for (index, value) in values.iter().enumerate() {
                    if index != 0 {
                        output.push(b',');
                    }
                    write_value(value, output)?;
                }
                output.push(b']');
            }
            Value::Object(values) => {
                output.push(b'{');
                let mut keys = values.keys().collect::<Vec<_>>();
                keys.sort_unstable();
                for (index, key) in keys.into_iter().enumerate() {
                    if index != 0 {
                        output.push(b',');
                    }
                    serde_json::to_writer(&mut *output, key).map_err(|_| {
                        SupervisorError::new("authority_policy_manifest_canonicalization_failed")
                    })?;
                    output.push(b':');
                    write_value(&values[key], output)?;
                }
                output.push(b'}');
            }
            _ => serde_json::to_writer(&mut *output, value).map_err(|_| {
                SupervisorError::new("authority_policy_manifest_canonicalization_failed")
            })?,
        }
        Ok(())
    }

    let mut output = Vec::new();
    write_value(value, &mut output)?;
    output.push(b'\n');
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::primitive_evidence_child_protocol::GlobalCapabilitySetDigest;
    use serde_json::json;

    const REAL_FIXTURE_CONTRACT_BYTES: &[u8] = include_bytes!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../tests/fixtures/primitive_basis/projects/model_part_composition/Assets/VRCForge/PrimitiveBasis/model_part_composition/fixture-contract.json"
    ));
    const REAL_FIXTURE_BASELINE_BYTES: &[u8] = include_bytes!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../tests/fixtures/primitive_basis/projects/model_part_composition/Assets/VRCForge/PrimitiveBasis/model_part_composition/baseline.json"
    ));
    const SYNTHETIC_GENERIC_DESCRIPTOR_BYTES: &[u8] = include_bytes!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../tests/fixtures/primitive_basis/model_part_composition.json"
    ));

    struct Fixture {
        final_commit: AuthenticatedFinalCommitPolicyBinding,
        runtime_identity: AuthorityRuntimeIdentity,
        readiness: VerifiedReadinessProof,
        ticket: RuntimeTicketRef,
        package: ProtectedRuntimeSourceReadback,
        scenario: FixedModelPartHeldReadback,
        processes: HeldAuthorityProcessReadback,
        job_security: VerifiedJobSecurityBinding,
        manifest: Value,
        executable_readbacks: [HeldFileReadback; 6],
        contract_readback: HeldFileReadback,
        contract_bytes: Vec<u8>,
        baseline_readback: HeldFileReadback,
        baseline_bytes: Vec<u8>,
    }

    fn digest(tag: u8) -> Digest {
        [tag; 32]
    }

    fn manifest_bytes(value: &Value) -> Vec<u8> {
        canonical_json_line(value).expect("canonical manifest")
    }

    fn file_record(tag: u8, byte_count: u64) -> Value {
        json!({
            "sha256": hex_lower(&digest(tag)),
            "byteCount": byte_count,
        })
    }

    fn tree_record(tag: u8) -> Value {
        json!({
            "schema": TREE_SOURCE_SCHEMA,
            "treeDigest": hex_lower(&digest(tag)),
            "bindingDigest": hex_lower(&digest(tag + 1)),
            "directoryCount": 2,
            "entryCount": 3,
            "byteCount": 4096,
        })
    }

    fn manifest_value(
        role_digests: &[Digest; 7],
        role_byte_counts: &[u64; 7],
        source_digests: &[Digest; 2],
        source_byte_counts: &[u64; 2],
    ) -> Value {
        let descriptors = FIXTURE_SCENARIOS
            .iter()
            .enumerate()
            .map(|(index, scenario)| {
                json!({
                    "scenarioId": scenario,
                    "fileSha256": hex_lower(&digest(130 + index as u8)),
                    "descriptorDigest": hex_lower(&digest(140 + index as u8)),
                    "byteCount": 1024 + index as u64,
                })
            })
            .collect::<Vec<_>>();
        let materialized_roots = FIXTURE_SCENARIOS
            .iter()
            .enumerate()
            .map(|(index, scenario)| {
                let descriptor_digest = digest(140 + index as u8);
                let baseline_digest = digest(150 + index as u8);
                let content_tree_digest = digest(160 + index as u8);
                let fixture_digest = contract_json_digest(&json!({
                    "descriptorDigest": hex_lower(&descriptor_digest),
                    "baselineDigest": hex_lower(&baseline_digest),
                    "treeDigest": hex_lower(&content_tree_digest),
                }))
                .unwrap();
                json!({
                    "scenarioId": scenario,
                    "fixtureDigest": hex_lower(&fixture_digest),
                    "baselineDigest": hex_lower(&baseline_digest),
                    "contentTreeDigest": hex_lower(&content_tree_digest),
                    "sourceTree": tree_record(170 + (index as u8 * 2)),
                })
            })
            .collect::<Vec<_>>();
        let descriptor_set_digest = contract_json_digest(&Value::Array(
            descriptors
                .iter()
                .map(|record| {
                    json!({
                        "scenarioId": record["scenarioId"],
                        "descriptorDigest": record["descriptorDigest"],
                    })
                })
                .collect(),
        ))
        .unwrap();
        let fixture_set_digest = contract_json_digest(&Value::Array(
            materialized_roots
                .iter()
                .map(|record| {
                    json!({
                        "scenarioId": record["scenarioId"],
                        "digest": record["fixtureDigest"],
                    })
                })
                .collect(),
        ))
        .unwrap();
        let model_descriptor_digest = descriptors.last().unwrap()["descriptorDigest"].clone();
        let model_fixture_digest = materialized_roots.last().unwrap()["fixtureDigest"].clone();
        let dependency_descriptor_digest = digest(213);
        let dependency_set_digest = digest(214);
        let dependency_byte_count = 1024;
        let dependency_binding_digest = dependency_set_binding_digest(
            &dependency_descriptor_digest,
            &dependency_set_digest,
            dependency_byte_count,
        )
        .unwrap();
        json!({
            "schema": RUNTIME_SOURCE_SCHEMA,
            "version": env!("CARGO_PKG_VERSION"),
            "sourceCommit": "1234567890abcdef1234567890abcdef12345678",
            "scenarioId": SCENARIO_ID,
            "buildPolicy": {
                "mode": "strict-evidence",
                "releaseEligible": false,
                "evidenceEligible": true,
                "allowDirty": false,
                "allowUnpushed": false,
                "allowVersionMismatch": false
            },
            "roles": ROLE_NAMES.iter().zip(role_digests).zip(role_byte_counts).map(
                |((role, digest), byte_count)| json!({
                    "role": role,
                    "sha256": hex_lower(digest),
                    "byteCount": byte_count,
                })
            ).collect::<Vec<_>>(),
            "sources": SOURCE_NAMES.iter().zip(source_digests).zip(source_byte_counts).map(
                |((source, digest), byte_count)| json!({
                    "source": source,
                    "sha256": hex_lower(digest),
                    "byteCount": byte_count,
                })
            ).collect::<Vec<_>>(),
            "bridgeTargetRuntime": {
                "schema": BRIDGE_TARGET_RUNTIME_SCHEMA,
                "runtimeRelativeRoot": BRIDGE_TARGET_RUNTIME_ROOT,
                "executableRelativePath": BRIDGE_TARGET_EXECUTABLE_PATH,
                "executableSha256": hex_lower(&role_digests[BRIDGE_LISTENER_ROLE_INDEX]),
                "manifestRelativePath": BRIDGE_TARGET_MANIFEST_PATH,
                "manifestSha256": hex_lower(&digest(200)),
                "treeDigest": hex_lower(&digest(201)),
                "directoryCount": 2,
                "entryCount": 3,
                "byteCount": 4096,
                "candidatePayloadIncluded": true,
                "strictSourceBound": true,
                "verifiedAfterBuild": true
            },
            "releaseArtifacts": {
                "strictManifest": file_record(202, 2048),
                "portableArchive": file_record(203, 8192),
                "unityPackage": file_record(204, 4096),
            },
            "packageTrees": {
                "backend": tree_record(205),
                "vrcforgeCore": tree_record(207),
                "server": tree_record(209),
            },
            "dependencySet": {
                "descriptorSchema": DEPENDENCY_SET_SCHEMA,
                "setDigest": hex_lower(&dependency_set_digest),
                "descriptorSha256": hex_lower(&dependency_descriptor_digest),
                "byteCount": dependency_byte_count,
                "canonicalJson": true,
                "bindingDigest": hex_lower(&dependency_binding_digest),
            },
            "fixtureSet": {
                "descriptorSetDigest": hex_lower(&descriptor_set_digest),
                "fixtureSetDigest": hex_lower(&fixture_set_digest),
                "descriptors": descriptors,
                "materializedRoots": materialized_roots,
            },
            "modelFixture": {
                "scenarioId": SCENARIO_ID,
                "descriptorDigest": model_descriptor_digest,
                "fixtureDigest": model_fixture_digest,
            },
        })
    }

    fn fixture() -> Fixture {
        let final_commit = AuthenticatedFinalCommitPolicyBinding::for_policy_source_test(10);
        let runtime_identity = AuthorityRuntimeIdentity::new(
            *final_commit.generation(),
            *final_commit.signer_key_id(),
            *final_commit.protected_manifest_sha256(),
            *final_commit.installed_layout_sha256(),
            *final_commit.service_binary_sha256(),
        )
        .unwrap();
        let authority_identity = runtime_identity.binding_digest();
        let readiness = VerifiedReadinessProof::for_runtime_test(authority_identity, digest(70));
        let ticket = RuntimeTicketRef::from_persisted(&hex_lower(&digest(71))).unwrap();
        let baseline_bytes = REAL_FIXTURE_BASELINE_BYTES.to_vec();
        let baseline_readback =
            HeldFileReadback::for_test(sha256(&baseline_bytes), 80, baseline_bytes.len() as u64);
        let contract_bytes = REAL_FIXTURE_CONTRACT_BYTES.to_vec();
        let contract_readback =
            HeldFileReadback::for_test(sha256(&contract_bytes), 82, contract_bytes.len() as u64);
        let executable_readbacks = std::array::from_fn(|index| {
            HeldFileReadback::for_test(digest(90 + index as u8), 100 + index as u8, 1_000)
        });
        let mut role_digests = [[0u8; 32]; 7];
        role_digests[0] = *final_commit.service_binary_sha256();
        for (target, source) in role_digests[1..].iter_mut().zip(&executable_readbacks) {
            *target = source.content_digest;
        }
        let mut role_byte_counts = [1_000u64; 7];
        for (target, source) in role_byte_counts[1..].iter_mut().zip(&executable_readbacks) {
            *target = source.byte_length;
        }
        let source_digests = [
            contract_readback.content_digest,
            baseline_readback.content_digest,
        ];
        let source_byte_counts = [contract_readback.byte_length, baseline_readback.byte_length];
        let manifest = manifest_value(
            &role_digests,
            &role_byte_counts,
            &source_digests,
            &source_byte_counts,
        );
        let manifest_bytes = manifest_bytes(&manifest);
        let manifest_held =
            HeldFileReadback::for_test(sha256(&manifest_bytes), 120, manifest_bytes.len() as u64);
        let package = protected_runtime_source_readback_from_bytes(
            &final_commit,
            manifest_held,
            &manifest_bytes,
        )
        .unwrap();
        let scenario = fixed_model_part_readback_from_parts(
            &package,
            executable_readbacks.clone(),
            contract_readback.clone(),
            &contract_bytes,
            baseline_readback.clone(),
            &baseline_bytes,
        )
        .unwrap();
        let processes = HeldAuthorityProcessReadback::for_test(&final_commit).unwrap();
        let job_security =
            VerifiedJobSecurityBinding::from_validated_native_spec(authority_identity, digest(121))
                .unwrap();
        Fixture {
            final_commit,
            runtime_identity,
            readiness,
            ticket,
            package,
            scenario,
            processes,
            job_security,
            manifest,
            executable_readbacks,
            contract_readback,
            contract_bytes,
            baseline_readback,
            baseline_bytes,
        }
    }

    fn readback_with_fixture_documents(
        value: &Fixture,
        contract_bytes: &[u8],
        baseline_bytes: &[u8],
    ) -> Result<FixedModelPartHeldReadback, SupervisorError> {
        let contract_readback =
            HeldFileReadback::for_test(sha256(contract_bytes), 82, contract_bytes.len() as u64);
        let baseline_readback =
            HeldFileReadback::for_test(sha256(baseline_bytes), 80, baseline_bytes.len() as u64);
        let source_digests = [
            contract_readback.content_digest,
            baseline_readback.content_digest,
        ];
        let source_byte_counts = [contract_readback.byte_length, baseline_readback.byte_length];
        let manifest = manifest_value(
            &value.package.role_digests,
            &value.package.role_byte_counts,
            &source_digests,
            &source_byte_counts,
        );
        let manifest_bytes = manifest_bytes(&manifest);
        let manifest_held =
            HeldFileReadback::for_test(sha256(&manifest_bytes), 123, manifest_bytes.len() as u64);
        let package = protected_runtime_source_readback_from_bytes(
            &value.final_commit,
            manifest_held,
            &manifest_bytes,
        )?;
        fixed_model_part_readback_from_parts(
            &package,
            value.executable_readbacks.clone(),
            contract_readback,
            contract_bytes,
            baseline_readback,
            baseline_bytes,
        )
    }

    fn build(value: &Fixture) -> Result<SupervisorPolicy, SupervisorError> {
        build_model_part_supervisor_policy_at(
            &value.final_commit,
            &value.runtime_identity,
            &value.readiness,
            &value.ticket,
            &value.package,
            &value.scenario,
            &value.processes,
            &value.job_security,
            1_000,
        )
    }

    fn parse_manifest(
        value: &Fixture,
        manifest: &Value,
    ) -> Result<ProtectedRuntimeSourceReadback, SupervisorError> {
        let bytes = manifest_bytes(manifest);
        let held = HeldFileReadback::for_test(sha256(&bytes), 124, bytes.len() as u64);
        protected_runtime_source_readback_from_bytes(&value.final_commit, held, &bytes)
    }

    #[test]
    fn repository_fixture_documents_are_the_only_fixed_model_part_contract() {
        let value = fixture();
        assert_eq!(
            value.scenario.expected_tree_digest,
            decode_digest(FIXTURE_TREE_DIGEST_HEX).unwrap()
        );
        assert_eq!(
            value.contract_readback.content_digest,
            decode_digest(FIXTURE_BASELINE_FILES[6].2).unwrap()
        );
        assert_eq!(
            value.contract_readback.byte_length,
            FIXTURE_BASELINE_FILES[6].1
        );

        assert_eq!(
            readback_with_fixture_documents(
                &value,
                SYNTHETIC_GENERIC_DESCRIPTOR_BYTES,
                REAL_FIXTURE_BASELINE_BYTES,
            )
            .unwrap_err()
            .code(),
            "authority_policy_fixture_contract_invalid"
        );

        let contract = parse_strict_json(REAL_FIXTURE_CONTRACT_BYTES).unwrap();
        let reformatted = serde_json::to_vec(&contract).unwrap();
        assert_ne!(reformatted, REAL_FIXTURE_CONTRACT_BYTES);
        assert_eq!(
            readback_with_fixture_documents(&value, &reformatted, REAL_FIXTURE_BASELINE_BYTES,)
                .unwrap_err()
                .code(),
            "authority_policy_fixture_baseline_invalid"
        );
    }

    #[test]
    fn fixture_contract_rejects_every_fixed_field_and_shape_drift() {
        let value = fixture();
        for mutation in 0..42 {
            let mut contract = parse_strict_json(REAL_FIXTURE_CONTRACT_BYTES).unwrap();
            match mutation {
                0 => contract["schema"] = Value::String("drift".to_owned()),
                1 => contract["scenarioId"] = Value::String("drift".to_owned()),
                2 => contract["primitiveId"] = Value::String("drift".to_owned()),
                3 => contract["unity"]["version"] = Value::String("drift".to_owned()),
                4 => contract["unity"]["revision"] = Value::String("drift".to_owned()),
                5..=12 => {
                    let field = [
                        "assetPath",
                        "guid",
                        "avatarPath",
                        "baseArmaturePath",
                        "partRootPath",
                        "componentHostPath",
                        "mergeTargetPath",
                        "rendererPath",
                    ][mutation - 5];
                    contract["scene"][field] = Value::String("drift".to_owned());
                }
                13 => contract["scene"]["baselineComponentCount"] = Value::from(1),
                14..=19 => {
                    let index = (mutation - 14) / 2;
                    let field = if (mutation - 14) % 2 == 0 {
                        "path"
                    } else {
                        "sha256"
                    };
                    contract["projectFiles"][index][field] = Value::String("drift".to_owned());
                }
                20..=31 => {
                    let index = (mutation - 20) / 3;
                    let field = ["id", "version", "provisioning"][(mutation - 20) % 3];
                    contract["requiredPackages"][index][field] = Value::String("drift".to_owned());
                }
                32 => contract["runtime"]["bootstrapType"] = Value::String("drift".to_owned()),
                33 => contract["runtime"]["runIdEnvironment"] = Value::String("drift".to_owned()),
                34 => contract["runtime"]["readyMarker"] = Value::String("drift".to_owned()),
                35 => {
                    contract.as_object_mut().unwrap().remove("primitiveId");
                }
                36 => {
                    contract["unexpected"] = Value::Bool(true);
                }
                37 => contract["projectFiles"].as_array_mut().unwrap().swap(0, 1),
                38 => contract["requiredPackages"]
                    .as_array_mut()
                    .unwrap()
                    .swap(0, 1),
                39 => {
                    contract["requiredPackages"].as_array_mut().unwrap().pop();
                }
                40 => {
                    let duplicate = contract["requiredPackages"][0].clone();
                    contract["requiredPackages"]
                        .as_array_mut()
                        .unwrap()
                        .push(duplicate);
                }
                41 => {
                    contract["requiredPackages"][0]
                        .as_object_mut()
                        .unwrap()
                        .insert("unexpected".to_owned(), Value::Bool(true));
                }
                _ => unreachable!(),
            }
            let bytes = serde_json::to_vec(&contract).unwrap();
            assert_eq!(
                readback_with_fixture_documents(&value, &bytes, REAL_FIXTURE_BASELINE_BYTES,)
                    .unwrap_err()
                    .code(),
                "authority_policy_fixture_contract_invalid",
                "contract mutation {mutation}"
            );
        }
    }

    #[test]
    fn fixture_baseline_rejects_every_fixed_field_and_shape_drift() {
        let value = fixture();
        for mutation in 0..30 {
            let mut baseline = parse_strict_json(REAL_FIXTURE_BASELINE_BYTES).unwrap();
            match mutation {
                0 => baseline["schema"] = Value::String("drift".to_owned()),
                1 => baseline["scenarioId"] = Value::String("drift".to_owned()),
                2..=25 => {
                    let index = (mutation - 2) / 3;
                    match (mutation - 2) % 3 {
                        0 => baseline["files"][index]["path"] = Value::String("drift".to_owned()),
                        1 => {
                            let size = baseline["files"][index]["size"].as_u64().unwrap();
                            baseline["files"][index]["size"] = Value::from(size + 1);
                        }
                        2 => {
                            baseline["files"][index]["sha256"] = Value::String("00".repeat(32));
                        }
                        _ => unreachable!(),
                    }
                }
                26 => {
                    baseline["files"].as_array_mut().unwrap().pop();
                }
                27 => baseline["files"].as_array_mut().unwrap().swap(0, 1),
                28 => baseline["unexpected"] = Value::Bool(true),
                29 => baseline["files"][0]["unexpected"] = Value::Bool(true),
                _ => unreachable!(),
            }
            let bytes = serde_json::to_vec(&baseline).unwrap();
            assert_eq!(
                readback_with_fixture_documents(&value, REAL_FIXTURE_CONTRACT_BYTES, &bytes,)
                    .unwrap_err()
                    .code(),
                "authority_policy_fixture_baseline_invalid",
                "baseline mutation {mutation}"
            );
        }
    }

    #[test]
    fn policy_source_builds_the_exact_fixed_model_part_policy() {
        let value = fixture();
        let policy = build(&value).unwrap();
        validate_policy(&policy).unwrap();
        assert_eq!(policy.authority_process, value.processes.service);
        assert_eq!(policy.authority_parent_process, value.processes.parent);
        assert_eq!(
            policy.process_executable_digests[0],
            *value.final_commit.service_binary_sha256()
        );
        assert_eq!(
            policy.process_executable_digests[1..],
            value.package.role_digests[1..]
        );
        assert_eq!(policy.issued_at, 1_000);
        assert_eq!(policy.deadline, 1_000 + POLICY_LIFETIME_SECONDS);
        assert_eq!(policy.artifacts.len(), 3);
        assert_eq!(policy.socket_policies.len(), 2);
        assert_eq!(policy.socket_policies[0].local_port, APP_LOOPBACK_PORT);
        assert!((49_152..=65_535).contains(&policy.socket_policies[1].local_port));
        assert!(policy.helper_policies.is_empty());
        assert_eq!(
            policy.job_security_binding_digest,
            value.job_security.binding_digest
        );
        assert_eq!(
            policy.bridge_target_manifest_digest,
            value.package.bridge_target_manifest_digest
        );
        assert_eq!(
            policy.bridge_target_tree_digest,
            value.package.bridge_target_tree_digest
        );
        assert_eq!(
            policy.child_transport_contract_digest,
            child_transport_contract_digest(&value.package, &value.scenario)
        );
        GlobalCapabilitySetDigest::derive(
            policy
                .child_transport_projection()
                .global_source_identities(),
        )
        .expect("the persisted exact source projection reconstructs the protocol capability set");
    }

    #[test]
    fn child_transport_contract_binds_fixed_roles_slots_access_and_executables() {
        let value = fixture();
        let baseline = child_transport_contract_projection(&value.package, &value.scenario);
        let baseline_digest = child_transport_contract_digest_from_projection(&baseline);
        assert_eq!(baseline.roles[0].role, ChildBootstrapRole::LifecycleDriver);
        assert_eq!(baseline.roles[1].role, ChildBootstrapRole::BridgeLauncher);
        assert_eq!(
            baseline.roles[0]
                .slots
                .map(|slot| (slot.readable, slot.writable)),
            [(true, false), (true, true), (false, true)]
        );

        let mut mutations = Vec::new();
        let mut changed = baseline.clone();
        changed.roles.swap(0, 1);
        mutations.push(changed);
        let mut changed = baseline.clone();
        changed.roles[0].slots.swap(0, 1);
        mutations.push(changed);
        let mut changed = baseline.clone();
        changed.roles[0].slots[0].semantic ^= 1;
        mutations.push(changed);
        let mut changed = baseline.clone();
        changed.roles[0].slots[0].purpose ^= 1;
        mutations.push(changed);
        let mut changed = baseline.clone();
        changed.roles[0].slots[0].readable = false;
        mutations.push(changed);
        let mut changed = baseline.clone();
        changed.roles[0].slots[0].writable = true;
        mutations.push(changed);
        let mut changed = baseline.clone();
        changed.roles[0].executable_identity_digest[0] ^= 0xff;
        mutations.push(changed);
        let mut changed = baseline.clone();
        changed.roles[1].executable_content_digest[0] ^= 0xff;
        mutations.push(changed);
        let mut changed = baseline.clone();
        changed.roles[1].executable_byte_length += 1;
        mutations.push(changed);
        let mut changed = baseline.clone();
        changed.global_source_identities.swap(0, 4);
        mutations.push(changed);

        for mutation in mutations {
            assert_ne!(
                child_transport_contract_digest_from_projection(&mutation),
                baseline_digest
            );
        }
    }

    #[test]
    fn authenticated_live_readback_is_the_only_readiness_and_preparation_source() {
        let value = fixture();
        let live =
            AuthenticatedGenerationBindingReadback::for_policy_source_test(&value.final_commit);
        let readiness =
            build_verified_readiness(&value.final_commit, &value.runtime_identity, &live).unwrap();
        assert!(readiness.verifies_for(&value.runtime_identity.binding_digest()));
        assert_ne!(readiness.service_instance_digest(), &[0; 32]);

        let prepared = prepare_model_part_run(
            &value.final_commit,
            &value.runtime_identity,
            &readiness,
            &value.ticket,
            &value.package,
            &value.scenario,
            &value.processes,
            &value.job_security,
        )
        .unwrap();
        assert!(prepared.verifies_for(
            &value.runtime_identity.binding_digest(),
            &value.ticket.digest(),
            readiness.service_instance_digest(),
        ));
        assert!(prepared
            .receipt()
            .verifies_policy_snapshot(prepared.policy_snapshot()));

        for field in 0..10 {
            let drifted = live.with_boundary_drift_for_policy_source_test(field);
            assert_eq!(
                build_verified_readiness(&value.final_commit, &value.runtime_identity, &drifted,)
                    .unwrap_err()
                    .code(),
                "authority_policy_readiness_readback_mismatch",
                "readback field {field}"
            );
        }
    }

    #[test]
    fn policy_source_is_deterministic_and_ticket_bound() {
        let first = fixture();
        let policy = build(&first).unwrap();
        assert_eq!(policy, build(&first).unwrap());

        let mut second = fixture();
        second.ticket = RuntimeTicketRef::from_persisted(&hex_lower(&digest(72))).unwrap();
        let changed = build(&second).unwrap();
        assert_ne!(policy.runner_policy_digest, changed.runner_policy_digest);
        assert_ne!(policy.run_binding_digest, changed.run_binding_digest);
        assert_ne!(
            policy.deterministic_job_name_digest,
            changed.deterministic_job_name_digest
        );
        assert_ne!(
            policy.private_root_binding_digest,
            changed.private_root_binding_digest
        );
        assert_ne!(
            policy.socket_policies[1].local_port,
            changed.socket_policies[1].local_port
        );
    }

    #[test]
    fn policy_source_rejects_final_commit_process_readiness_and_job_drift() {
        let mut value = fixture();
        value.runtime_identity = AuthorityRuntimeIdentity::new(
            digest(200),
            *value.final_commit.signer_key_id(),
            *value.final_commit.protected_manifest_sha256(),
            *value.final_commit.installed_layout_sha256(),
            *value.final_commit.service_binary_sha256(),
        )
        .unwrap();
        assert_eq!(
            build(&value).unwrap_err().code(),
            "authority_policy_final_commit_identity_mismatch"
        );

        let mut value = fixture();
        value.processes.service.pid += 1;
        assert_eq!(
            build(&value).unwrap_err().code(),
            "authority_policy_service_process_drift"
        );

        let mut value = fixture();
        value.readiness = VerifiedReadinessProof::for_runtime_test(digest(200), digest(70));
        assert_eq!(
            build(&value).unwrap_err().code(),
            "authority_policy_readiness_binding_mismatch"
        );

        let mut value = fixture();
        value.job_security.authority_identity_digest[0] ^= 1;
        assert_eq!(
            build(&value).unwrap_err().code(),
            "authority_policy_job_security_binding_mismatch"
        );

        assert_eq!(
            VerifiedJobSecurityBinding::from_validated_native_spec(digest(1), [0; 32])
                .unwrap_err()
                .code(),
            "authority_policy_job_security_binding_invalid"
        );
    }

    #[test]
    fn policy_source_rejects_missing_extra_reordered_and_invalid_roles() {
        for mutation in 0..7 {
            let value = fixture();
            let mut manifest = value.manifest.clone();
            let roles = manifest["roles"].as_array_mut().unwrap();
            match mutation {
                0 => {
                    roles.pop();
                }
                1 => roles.push(json!({
                    "role":"unexpected",
                    "sha256":hex_lower(&digest(220)),
                    "byteCount": 1,
                })),
                2 => roles.swap(1, 2),
                3 => roles[3]["sha256"] = Value::String("00".repeat(32)),
                4 => roles[3]["byteCount"] = Value::from(0),
                5 => roles[3]["byteCount"] = Value::from(MAX_EXECUTABLE_BYTES + 1),
                6 => {
                    roles[3].as_object_mut().unwrap().remove("byteCount");
                }
                _ => unreachable!(),
            }
            let bytes = manifest_bytes(&manifest);
            let held = HeldFileReadback::for_test(sha256(&bytes), 120, bytes.len() as u64);
            let error =
                protected_runtime_source_readback_from_bytes(&value.final_commit, held, &bytes)
                    .unwrap_err();
            assert!(matches!(
                error.code(),
                "authority_policy_package_roles_invalid" | "authority_policy_digest_invalid"
            ));
        }
    }

    #[test]
    fn policy_source_rejects_invalid_source_order_shape_and_byte_counts() {
        for mutation in 0..8 {
            let value = fixture();
            let mut manifest = value.manifest.clone();
            let sources = manifest["sources"].as_array_mut().unwrap();
            match mutation {
                0 => {
                    sources.pop();
                }
                1 => sources.push(json!({
                    "source": "unexpected",
                    "sha256": hex_lower(&digest(221)),
                    "byteCount": 1,
                })),
                2 => sources.swap(0, 1),
                3 => sources[0]["source"] = Value::String("fixture_contract".to_owned()),
                4 => sources[0]["sha256"] = Value::String("00".repeat(32)),
                5 => sources[0]["byteCount"] = Value::from(0),
                6 => sources[0]["byteCount"] = Value::from(MAX_SOURCE_BYTES + 1),
                7 => {
                    sources[0].as_object_mut().unwrap().remove("byteCount");
                }
                _ => unreachable!(),
            }
            assert!(
                parse_manifest(&value, &manifest).is_err(),
                "mutation {mutation}"
            );
        }
    }

    #[test]
    fn policy_source_rejects_relaxed_manifest_identity_and_scenario_drift() {
        for mutation in 0..4 {
            let value = fixture();
            let mut manifest = value.manifest.clone();
            match mutation {
                0 => manifest["buildPolicy"]["allowDirty"] = Value::Bool(true),
                1 => manifest["schema"] = Value::String("invalid".to_owned()),
                2 => manifest["scenarioId"] = Value::String("unregistered".to_owned()),
                3 => {
                    manifest["unexpected"] = Value::Bool(true);
                }
                _ => unreachable!(),
            }
            let bytes = manifest_bytes(&manifest);
            let held = HeldFileReadback::for_test(sha256(&bytes), 120, bytes.len() as u64);
            assert!(protected_runtime_source_readback_from_bytes(
                &value.final_commit,
                held,
                &bytes,
            )
            .is_err());
        }
    }

    #[test]
    fn policy_source_accepts_only_the_complete_v2_root_contract() {
        let value = fixture();
        assert_eq!(value.manifest["schema"], RUNTIME_SOURCE_SCHEMA);
        assert_eq!(value.manifest["sources"][0]["source"], SOURCE_NAMES[0]);
        assert_eq!(
            value.package.role_byte_counts[1..],
            value
                .executable_readbacks
                .iter()
                .map(|held| held.byte_length)
                .collect::<Vec<_>>()
        );
        assert_eq!(
            value.package.source_byte_counts,
            [
                value.contract_readback.byte_length,
                value.baseline_readback.byte_length,
            ]
        );

        let mut legacy = value.manifest.clone();
        legacy["schema"] = Value::String("vrcforge.protected_runtime_source.v1".to_owned());
        assert_eq!(
            parse_manifest(&value, &legacy).unwrap_err().code(),
            "authority_policy_package_contract_invalid"
        );

        for missing in [
            "releaseArtifacts",
            "packageTrees",
            "dependencySet",
            "fixtureSet",
            "modelFixture",
        ] {
            let mut manifest = value.manifest.clone();
            manifest.as_object_mut().unwrap().remove(missing);
            assert_eq!(
                parse_manifest(&value, &manifest).unwrap_err().code(),
                "authority_policy_package_contract_invalid",
                "missing {missing}"
            );
        }
    }

    #[test]
    fn policy_source_rejects_legacy_or_drifted_dependency_set_summaries() {
        for mutation in 0..6 {
            let value = fixture();
            let mut manifest = value.manifest.clone();
            match mutation {
                0 => manifest["dependencySet"] = json!({}),
                1 => {
                    manifest["dependencySet"]["descriptorSchema"] =
                        Value::String("vrcforge.protected_runtime_dependency_set.v1".to_owned())
                }
                2 => {
                    manifest["dependencySet"]["setDigest"] = Value::String(hex_lower(&digest(230)))
                }
                3 => {
                    manifest["dependencySet"]["descriptorSha256"] =
                        Value::String(hex_lower(&digest(231)))
                }
                4 => manifest["dependencySet"]["byteCount"] = Value::from(1025),
                5 => {
                    manifest["dependencySet"]["bindingDigest"] =
                        Value::String(hex_lower(&digest(232)))
                }
                _ => unreachable!(),
            }
            assert_eq!(
                parse_manifest(&value, &manifest).unwrap_err().code(),
                "authority_policy_package_contract_invalid",
                "mutation {mutation}"
            );
        }
    }

    #[test]
    fn dependency_set_semantics_flow_into_the_runtime_and_run_bindings() {
        let mut value = fixture();
        let original_policy = build(&value).unwrap();
        value.package.dependency_set.descriptor_digest = digest(230);
        value.package.dependency_set.set_digest = digest(231);
        value.package.dependency_set.byte_count += 7;
        value.package.dependency_set.binding_digest = dependency_set_binding_digest(
            &value.package.dependency_set.descriptor_digest,
            &value.package.dependency_set.set_digest,
            value.package.dependency_set.byte_count,
        )
        .unwrap();
        value.package.binding_digest = runtime_source_binding(
            &value.final_commit,
            &value.package.manifest_digest,
            &value.package.manifest_identity_digest,
            &value.package.source_commit_digest,
            &value.package.bridge_target_manifest_digest,
            &value.package.bridge_target_tree_digest,
            &value.package.role_digests,
            &value.package.role_byte_counts,
            &value.package.source_digests,
            &value.package.source_byte_counts,
            &value.package.dependency_set,
        );
        value.scenario = fixed_model_part_readback_from_parts(
            &value.package,
            value.executable_readbacks.clone(),
            value.contract_readback.clone(),
            &value.contract_bytes,
            value.baseline_readback.clone(),
            &value.baseline_bytes,
        )
        .unwrap();
        let changed_policy = build(&value).unwrap();

        assert_ne!(
            original_policy.runner_policy_digest,
            changed_policy.runner_policy_digest
        );
        assert_ne!(
            original_policy.run_binding_digest,
            changed_policy.run_binding_digest
        );
    }

    #[test]
    fn policy_source_contract_digests_match_the_producer_canonical_vectors() {
        assert_eq!(
            hex_lower(
                &contract_json_digest(&json!({
                    "descriptorDigest": hex_lower(&digest(1)),
                    "baselineDigest": hex_lower(&digest(2)),
                    "treeDigest": hex_lower(&digest(3)),
                }))
                .unwrap()
            ),
            "dae0eca31ac1ab9d85457be50cf0037e99c03f25da712df2a7ab3e018073e154"
        );
        let descriptor_rows = FIXTURE_SCENARIOS
            .iter()
            .enumerate()
            .map(|(index, scenario)| {
                json!({
                    "scenarioId": scenario,
                    "descriptorDigest": hex_lower(&digest(10 + index as u8)),
                })
            })
            .collect::<Vec<_>>();
        assert_eq!(
            hex_lower(&contract_json_digest(&Value::Array(descriptor_rows)).unwrap()),
            "412fefdf958fbfd0483aeb91e09e0d41f1d3bf480c09c8b2fb8aca34ec6341fe"
        );
        let fixture_rows = FIXTURE_SCENARIOS
            .iter()
            .enumerate()
            .map(|(index, scenario)| {
                json!({
                    "scenarioId": scenario,
                    "digest": hex_lower(&digest(20 + index as u8)),
                })
            })
            .collect::<Vec<_>>();
        assert_eq!(
            hex_lower(&contract_json_digest(&Value::Array(fixture_rows)).unwrap()),
            "d391f6c05e8a5e84fa1afe58232d8bb3d93e8adc27a152147dc0e78db2b7b6e9"
        );
    }

    #[test]
    fn policy_source_rejects_every_v2_release_tree_dependency_and_fixture_drift() {
        for mutation in 0..55 {
            let value = fixture();
            let mut manifest = value.manifest.clone();
            match mutation {
                0 => {
                    manifest["releaseArtifacts"]
                        .as_object_mut()
                        .unwrap()
                        .remove("unityPackage");
                }
                1 => {
                    manifest["releaseArtifacts"]
                        .as_object_mut()
                        .unwrap()
                        .insert("unexpected".to_owned(), file_record(240, 1));
                }
                2 => {
                    manifest["releaseArtifacts"]["strictManifest"]["sha256"] =
                        Value::String("00".repeat(32))
                }
                3 => manifest["releaseArtifacts"]["strictManifest"]["byteCount"] = Value::from(0),
                4 => {
                    manifest["releaseArtifacts"]["portableArchive"]["byteCount"] =
                        Value::from(MAX_ARCHIVE_BYTES + 1)
                }
                5 => {
                    manifest["releaseArtifacts"]["strictManifest"]
                        .as_object_mut()
                        .unwrap()
                        .insert("unexpected".to_owned(), Value::Bool(true));
                }
                6 => {
                    manifest["packageTrees"]
                        .as_object_mut()
                        .unwrap()
                        .remove("server");
                }
                7 => {
                    manifest["packageTrees"]
                        .as_object_mut()
                        .unwrap()
                        .insert("unexpected".to_owned(), tree_record(240));
                }
                8 => {
                    manifest["packageTrees"]["backend"]["schema"] =
                        Value::String("invalid".to_owned())
                }
                9 => {
                    manifest["packageTrees"]["backend"]["treeDigest"] =
                        Value::String("00".repeat(32))
                }
                10 => {
                    manifest["packageTrees"]["backend"]["bindingDigest"] =
                        Value::String("00".repeat(32))
                }
                11 => {
                    manifest["packageTrees"]["backend"]["directoryCount"] =
                        Value::from(MAX_POLICY_TREE_ENTRIES + 1)
                }
                12 => manifest["packageTrees"]["backend"]["entryCount"] = Value::from(0),
                13 => {
                    manifest["packageTrees"]["backend"]["byteCount"] =
                        Value::from(MAX_POLICY_TREE_BYTES + 1)
                }
                14 => {
                    manifest["packageTrees"]["backend"]
                        .as_object_mut()
                        .unwrap()
                        .insert("unexpected".to_owned(), Value::Bool(true));
                }
                15 => {
                    manifest["dependencySet"]["descriptorSha256"] = Value::String("00".repeat(32))
                }
                16 => manifest["dependencySet"]["byteCount"] = Value::from(0),
                17 => manifest["dependencySet"]["canonicalJson"] = Value::Bool(false),
                18 => {
                    manifest["dependencySet"]
                        .as_object_mut()
                        .unwrap()
                        .insert("unexpected".to_owned(), Value::Bool(true));
                }
                19 => {
                    manifest["fixtureSet"]["descriptors"]
                        .as_array_mut()
                        .unwrap()
                        .pop();
                }
                20 => {
                    manifest["fixtureSet"]["materializedRoots"]
                        .as_array_mut()
                        .unwrap()
                        .pop();
                }
                21 => manifest["fixtureSet"]["descriptors"]
                    .as_array_mut()
                    .unwrap()
                    .swap(0, 1),
                22 => manifest["fixtureSet"]["materializedRoots"]
                    .as_array_mut()
                    .unwrap()
                    .swap(0, 1),
                23 => {
                    manifest["fixtureSet"]["descriptors"][0]["fileSha256"] =
                        Value::String("00".repeat(32))
                }
                24 => {
                    manifest["fixtureSet"]["descriptors"][0]["descriptorDigest"] =
                        Value::String("00".repeat(32))
                }
                25 => {
                    manifest["fixtureSet"]["descriptors"][0]["byteCount"] =
                        Value::from(MAX_SOURCE_BYTES + 1)
                }
                26 => {
                    manifest["fixtureSet"]["descriptors"][0]
                        .as_object_mut()
                        .unwrap()
                        .insert("unexpected".to_owned(), Value::Bool(true));
                }
                27 => {
                    manifest["fixtureSet"]["materializedRoots"][0]["baselineDigest"] =
                        Value::String("00".repeat(32))
                }
                28 => {
                    manifest["fixtureSet"]["materializedRoots"][0]["contentTreeDigest"] =
                        Value::String("00".repeat(32))
                }
                29 => {
                    manifest["fixtureSet"]["materializedRoots"][0]["sourceTree"]["schema"] =
                        Value::String("invalid".to_owned())
                }
                30 => {
                    manifest["fixtureSet"]["materializedRoots"][0]["sourceTree"]["entryCount"] =
                        Value::from(0)
                }
                31 => {
                    manifest["fixtureSet"]["materializedRoots"][0]["fixtureDigest"] =
                        Value::String(hex_lower(&digest(241)))
                }
                32 => {
                    manifest["fixtureSet"]["descriptorSetDigest"] =
                        Value::String(hex_lower(&digest(242)))
                }
                33 => {
                    manifest["fixtureSet"]["fixtureSetDigest"] =
                        Value::String(hex_lower(&digest(243)))
                }
                34 => {
                    manifest["fixtureSet"]
                        .as_object_mut()
                        .unwrap()
                        .insert("unexpected".to_owned(), Value::Bool(true));
                }
                35 => {
                    manifest["modelFixture"]["scenarioId"] =
                        Value::String(FIXTURE_SCENARIOS[0].to_owned())
                }
                36 => {
                    manifest["modelFixture"]["descriptorDigest"] =
                        Value::String(hex_lower(&digest(244)))
                }
                37 => {
                    manifest["modelFixture"]["fixtureDigest"] =
                        Value::String(hex_lower(&digest(245)))
                }
                38 => {
                    manifest["modelFixture"]
                        .as_object_mut()
                        .unwrap()
                        .insert("unexpected".to_owned(), Value::Bool(true));
                }
                39 => {
                    manifest["fixtureSet"]["materializedRoots"][0]
                        .as_object_mut()
                        .unwrap()
                        .insert("unexpected".to_owned(), Value::Bool(true));
                }
                40 => {
                    manifest["packageTrees"]
                        .as_object_mut()
                        .unwrap()
                        .remove("vrcforgeCore");
                }
                41 => {
                    manifest["packageTrees"]["vrcforgeCore"]["schema"] =
                        Value::String("invalid".to_owned())
                }
                42 => {
                    manifest["packageTrees"]["vrcforgeCore"]["treeDigest"] =
                        Value::String("00".repeat(32))
                }
                43 => {
                    manifest["packageTrees"]["vrcforgeCore"]["bindingDigest"] =
                        Value::String("00".repeat(32))
                }
                44 => {
                    manifest["packageTrees"]["vrcforgeCore"]["directoryCount"] =
                        Value::from(MAX_POLICY_TREE_ENTRIES + 1)
                }
                45 => manifest["packageTrees"]["vrcforgeCore"]["entryCount"] = Value::from(0),
                46 => {
                    manifest["packageTrees"]["vrcforgeCore"]["byteCount"] =
                        Value::from(MAX_POLICY_TREE_BYTES + 1)
                }
                47 => {
                    manifest["packageTrees"]["vrcforgeCore"]
                        .as_object_mut()
                        .unwrap()
                        .insert("unexpected".to_owned(), Value::Bool(true));
                }
                48 => {
                    manifest["packageTrees"]["server"]["schema"] =
                        Value::String("invalid".to_owned())
                }
                49 => {
                    manifest["packageTrees"]["server"]["treeDigest"] =
                        Value::String("00".repeat(32))
                }
                50 => {
                    manifest["packageTrees"]["server"]["bindingDigest"] =
                        Value::String("00".repeat(32))
                }
                51 => {
                    manifest["packageTrees"]["server"]["directoryCount"] =
                        Value::from(MAX_POLICY_TREE_ENTRIES + 1)
                }
                52 => manifest["packageTrees"]["server"]["entryCount"] = Value::from(0),
                53 => {
                    manifest["packageTrees"]["server"]["byteCount"] =
                        Value::from(MAX_POLICY_TREE_BYTES + 1)
                }
                54 => {
                    manifest["packageTrees"]["server"]
                        .as_object_mut()
                        .unwrap()
                        .insert("unexpected".to_owned(), Value::Bool(true));
                }
                _ => unreachable!(),
            }
            assert_eq!(
                parse_manifest(&value, &manifest).unwrap_err().code(),
                "authority_policy_package_contract_invalid",
                "mutation {mutation}"
            );
        }
    }

    #[test]
    fn policy_source_allows_zero_directory_count_only_for_tree_records() {
        let value = fixture();
        let mut manifest = value.manifest.clone();
        manifest["packageTrees"]["backend"]["directoryCount"] = Value::from(0);
        manifest["fixtureSet"]["materializedRoots"][0]["sourceTree"]["directoryCount"] =
            Value::from(0);
        parse_manifest(&value, &manifest).unwrap();
    }

    #[test]
    fn policy_source_manifest_is_install_independent_and_rejects_final_commit_fields() {
        let value = fixture();
        let root = value.manifest.as_object().unwrap();
        for forbidden in [
            "authorityGenerationSha256",
            "authorityFinalCommitReceiptSha256",
            "protectedManifestSha256",
            "installedLayoutSha256",
            "serviceConfigurationSha256",
        ] {
            assert!(!root.contains_key(forbidden));

            let mut manifest = value.manifest.clone();
            manifest[forbidden] = Value::String(hex_lower(&digest(230)));
            let bytes = manifest_bytes(&manifest);
            let held = HeldFileReadback::for_test(sha256(&bytes), 120, bytes.len() as u64);
            assert_eq!(
                protected_runtime_source_readback_from_bytes(&value.final_commit, held, &bytes)
                    .unwrap_err()
                    .code(),
                "authority_policy_package_contract_invalid"
            );
        }
    }

    #[test]
    fn policy_source_requires_canonical_compact_json_with_one_lf() {
        let value = fixture();
        let canonical = manifest_bytes(&value.manifest);
        let mut missing_lf = canonical.clone();
        assert_eq!(missing_lf.pop(), Some(b'\n'));
        let mut crlf = missing_lf.clone();
        crlf.extend_from_slice(b"\r\n");
        let mut pretty = serde_json::to_vec_pretty(&value.manifest).unwrap();
        pretty.push(b'\n');

        let root = value.manifest.as_object().unwrap();
        let reordered_keys = [
            "schema",
            "version",
            "sourceCommit",
            "scenarioId",
            "buildPolicy",
            "roles",
            "sources",
            "bridgeTargetRuntime",
            "releaseArtifacts",
            "packageTrees",
            "dependencySet",
            "fixtureSet",
            "modelFixture",
        ];
        let mut reordered = Vec::from(&b"{"[..]);
        for (index, key) in reordered_keys.iter().enumerate() {
            if index != 0 {
                reordered.push(b',');
            }
            serde_json::to_writer(&mut reordered, key).unwrap();
            reordered.push(b':');
            serde_json::to_writer(&mut reordered, &root[*key]).unwrap();
        }
        reordered.extend_from_slice(b"}\n");
        assert_ne!(reordered, canonical);

        for bytes in [missing_lf, crlf, pretty, reordered] {
            let held = HeldFileReadback::for_test(sha256(&bytes), 120, bytes.len() as u64);
            assert_eq!(
                protected_runtime_source_readback_from_bytes(&value.final_commit, held, &bytes)
                    .unwrap_err()
                    .code(),
                "authority_policy_manifest_noncanonical"
            );
        }
    }

    #[test]
    fn policy_source_requires_the_exact_bridge_target_runtime_binding() {
        for mutation in 0..17 {
            let value = fixture();
            let mut manifest = value.manifest.clone();
            let runtime = manifest["bridgeTargetRuntime"].as_object_mut().unwrap();
            match mutation {
                0 => runtime["schema"] = Value::String("invalid".to_owned()),
                1 => runtime["runtimeRelativeRoot"] = Value::String("other".to_owned()),
                2 => runtime["executableRelativePath"] = Value::String("other.exe".to_owned()),
                3 => runtime["executableSha256"] = Value::String("11".repeat(32)),
                4 => runtime["manifestRelativePath"] = Value::String("other.json".to_owned()),
                5 => runtime["manifestSha256"] = Value::String("00".repeat(32)),
                6 => runtime["treeDigest"] = Value::String("00".repeat(32)),
                7 => runtime["directoryCount"] = Value::from(0),
                8 => runtime["entryCount"] = Value::from(0),
                9 => runtime["byteCount"] = Value::from(0),
                10 => runtime["candidatePayloadIncluded"] = Value::Bool(false),
                11 => runtime["strictSourceBound"] = Value::Bool(false),
                12 => runtime["verifiedAfterBuild"] = Value::Bool(false),
                13 => {
                    runtime.insert("unexpected".to_owned(), Value::Bool(true));
                }
                14 => runtime["directoryCount"] = Value::from(MAX_POLICY_TREE_ENTRIES + 1),
                15 => runtime["entryCount"] = Value::from(MAX_POLICY_TREE_ENTRIES + 1),
                16 => runtime["byteCount"] = Value::from(MAX_POLICY_TREE_BYTES + 1),
                _ => unreachable!(),
            }
            let bytes = manifest_bytes(&manifest);
            let held = HeldFileReadback::for_test(sha256(&bytes), 120, bytes.len() as u64);
            assert!(protected_runtime_source_readback_from_bytes(
                &value.final_commit,
                held,
                &bytes,
            )
            .is_err());
        }
    }

    #[test]
    fn policy_source_rejects_held_executable_source_and_policy_binding_drift() {
        let value = fixture();
        let mut executables = value.executable_readbacks.clone();
        executables[2].content_digest[0] ^= 1;
        assert_eq!(
            fixed_model_part_readback_from_parts(
                &value.package,
                executables,
                value.contract_readback.clone(),
                &value.contract_bytes,
                value.baseline_readback.clone(),
                &value.baseline_bytes,
            )
            .unwrap_err()
            .code(),
            "authority_policy_held_source_mismatch"
        );

        let mut executables = value.executable_readbacks.clone();
        executables[2].byte_length += 1;
        assert_eq!(
            fixed_model_part_readback_from_parts(
                &value.package,
                executables,
                value.contract_readback.clone(),
                &value.contract_bytes,
                value.baseline_readback.clone(),
                &value.baseline_bytes,
            )
            .unwrap_err()
            .code(),
            "authority_policy_held_source_mismatch"
        );

        let mut baseline = value.baseline_readback.clone();
        baseline.content_digest[0] ^= 1;
        assert_eq!(
            fixed_model_part_readback_from_parts(
                &value.package,
                value.executable_readbacks.clone(),
                value.contract_readback.clone(),
                &value.contract_bytes,
                baseline,
                &value.baseline_bytes,
            )
            .unwrap_err()
            .code(),
            "authority_policy_held_source_mismatch"
        );

        let mut manifest = value.manifest.clone();
        manifest["sources"][0]["byteCount"] = Value::from(value.contract_readback.byte_length + 1);
        let count_drifted_package = parse_manifest(&value, &manifest).unwrap();
        assert_eq!(
            fixed_model_part_readback_from_parts(
                &count_drifted_package,
                value.executable_readbacks.clone(),
                value.contract_readback.clone(),
                &value.contract_bytes,
                value.baseline_readback.clone(),
                &value.baseline_bytes,
            )
            .unwrap_err()
            .code(),
            "authority_policy_held_source_mismatch"
        );

        let mut value = fixture();
        value.package.binding_digest[0] ^= 1;
        assert_eq!(
            build(&value).unwrap_err().code(),
            "authority_policy_package_binding_invalid"
        );

        let mut value = fixture();
        value.package.role_byte_counts[2] += 1;
        assert_eq!(
            build(&value).unwrap_err().code(),
            "authority_policy_package_binding_invalid"
        );

        let mut value = fixture();
        value.scenario.scenario_binding_digest[0] ^= 1;
        assert_eq!(
            build(&value).unwrap_err().code(),
            "authority_policy_scenario_binding_invalid"
        );
    }

    #[test]
    fn policy_source_rejects_role_and_runtime_source_identity_aliases() {
        let value = fixture();
        let mut role_digests = value.package.role_digests;
        role_digests[2] = role_digests[1];
        let manifest = manifest_value(
            &role_digests,
            &value.package.role_byte_counts,
            &value.package.source_digests,
            &value.package.source_byte_counts,
        );
        let bytes = manifest_bytes(&manifest);
        let held = HeldFileReadback::for_test(sha256(&bytes), 122, bytes.len() as u64);
        let package =
            protected_runtime_source_readback_from_bytes(&value.final_commit, held, &bytes)
                .unwrap();
        let mut executables = value.executable_readbacks.clone();
        executables[1] = executables[0].clone();
        assert_eq!(
            fixed_model_part_readback_from_parts(
                &package,
                executables,
                value.contract_readback.clone(),
                &value.contract_bytes,
                value.baseline_readback.clone(),
                &value.baseline_bytes,
            )
            .unwrap_err()
            .code(),
            "authority_policy_held_source_identity_alias"
        );

        let value = fixture();
        let mut executables = value.executable_readbacks.clone();
        executables[0].identity_digest = value.package.manifest_identity_digest;
        assert_eq!(
            fixed_model_part_readback_from_parts(
                &value.package,
                executables,
                value.contract_readback.clone(),
                &value.contract_bytes,
                value.baseline_readback.clone(),
                &value.baseline_bytes,
            )
            .unwrap_err()
            .code(),
            "authority_policy_held_source_identity_alias"
        );
    }

    #[test]
    fn policy_source_strict_json_rejects_duplicate_keys_floats_and_trailing_data() {
        let value = fixture();
        let ordinary = serde_json::to_string(&value.manifest).unwrap();
        let duplicate = format!("{{\"version\":\"duplicate\",{}", &ordinary[1..]);
        let floating = format!("{{\"floating\":1.5,{}", &ordinary[1..]);
        let trailing = format!("{ordinary} true");
        for bytes in [
            duplicate.as_bytes(),
            floating.as_bytes(),
            trailing.as_bytes(),
        ] {
            let held = HeldFileReadback::for_test(sha256(bytes), 120, bytes.len() as u64);
            assert_eq!(
                protected_runtime_source_readback_from_bytes(&value.final_commit, held, bytes)
                    .unwrap_err()
                    .code(),
                "authority_policy_strict_json_invalid"
            );
        }
    }

    #[test]
    fn held_file_readback_rejects_same_length_post_read_mutation() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "vrcforge-policy-held-file-{}-{unique}",
            std::process::id()
        ));
        std::fs::create_dir(&root).unwrap();
        let path = root.join("held.bin");
        let original = vec![0x41; 128 * 1024];
        let replacement = vec![0x42; original.len()];
        std::fs::write(&path, &original).unwrap();
        let reader = File::open(&path).unwrap();
        let writer = std::fs::OpenOptions::new().write(true).open(&path).unwrap();
        let result = read_held_file_after_read(&reader, original.len() as u64, move || {
            std::thread::sleep(std::time::Duration::from_millis(20));
            assert_eq!(
                writer.seek_write(&replacement, 0).unwrap(),
                replacement.len()
            );
            writer.sync_all().unwrap();
        });
        drop(reader);
        std::fs::remove_file(&path).unwrap();
        std::fs::remove_dir(&root).unwrap();
        assert_eq!(
            result.unwrap_err().code(),
            "authority_policy_held_file_changed"
        );
    }

    #[test]
    fn policy_source_process_epoch_requires_exact_parent_order_and_final_commit_service() {
        let value = fixture();
        for (service, parent) in [
            (
                ProcessKey {
                    pid: 0,
                    creation_time: 2,
                },
                ProcessKey {
                    pid: 1,
                    creation_time: 1,
                },
            ),
            (
                ProcessKey {
                    pid: 2,
                    creation_time: 2,
                },
                ProcessKey {
                    pid: 1,
                    creation_time: 2,
                },
            ),
            (
                ProcessKey {
                    pid: 2,
                    creation_time: 2,
                },
                ProcessKey {
                    pid: 2,
                    creation_time: 2,
                },
            ),
        ] {
            assert_eq!(
                HeldAuthorityProcessReadback::from_keys(service, parent)
                    .unwrap_err()
                    .code(),
                "authority_policy_process_readback_invalid"
            );
        }
        let wrong = HeldAuthorityProcessReadback::from_keys(
            ProcessKey {
                pid: value.final_commit.service_process_id() + 1,
                creation_time: value.final_commit.service_process_creation_time(),
            },
            ProcessKey {
                pid: value.final_commit.service_process_id(),
                creation_time: value.final_commit.service_process_creation_time() - 1,
            },
        )
        .unwrap();
        assert_eq!(
            wrong
                .verify_final_commit(&value.final_commit)
                .unwrap_err()
                .code(),
            "authority_policy_service_process_drift"
        );
    }

    #[test]
    fn runtime_source_entry_requires_the_typed_authenticated_capability() {
        let source = include_str!("policy_source.rs");
        assert!(source.contains("read_from_capability("));
        assert!(source.contains("&mut AuthenticatedRuntimeSourceCapability"));
        let raw_handle_entry = ["read_from_held_", "handle("].concat();
        assert!(!source.contains(&raw_handle_entry));
        assert!(!source.contains(
            "final_commit: &AuthenticatedFinalCommitPolicyBinding,\n        manifest: &File"
        ));
    }
}
