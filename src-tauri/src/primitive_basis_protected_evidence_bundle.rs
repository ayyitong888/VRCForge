//! Internal producer for protected primitive-basis evidence projections.
//!
//! This module accepts only typed, service-owned terminal facts.  It does not
//! expose arbitrary signing, finalization, trust-key, or caller-report inputs.
//! Native runtime/ledger adapters remain required before this source contract
//! can be enabled in production.

use serde::{de, Deserialize, Deserializer};
use serde_json::{Map, Number, Value};
use sha2::{Digest as _, Sha256};
use std::fmt;

#[cfg(windows)]
use crate::primitive_evidence_authority_supervisor::native_windows::NativeCompletedRunProof;

pub(crate) const PROJECTION_SCHEMA: &str =
    "vrcforge.primitive_basis_protected_evidence_projection.v1";
pub(crate) const AUTHORITY_BUNDLE_SCHEMA: &str =
    "vrcforge.primitive_basis_authority_evidence_bundle.v1";
pub(crate) const AUTHORITY_BINDING_SCHEMA: &str = "vrcforge.primitive_basis_authority_binding.v1";
pub(crate) const PACKAGE_BINDING_SCHEMA: &str = "vrcforge.primitive_basis_package_binding.v1";
pub(crate) const AUTHORITY_ROW_SCHEMA: &str = "vrcforge.primitive_basis_authority_row.v1";
pub(crate) const LEDGER_SNAPSHOT_SCHEMA: &str =
    "vrcforge.primitive_basis_authority_ledger_snapshot.v1";
pub(crate) const LEDGER_RECEIPT_SCHEMA_V1: &str =
    "vrcforge.primitive_basis_authority_completed_receipt.v1";
pub(crate) const LEDGER_RECEIPT_SCHEMA: &str =
    "vrcforge.primitive_basis_authority_completed_receipt.v2";
pub(crate) const BINARY_LEDGER_TERMINAL_SCHEMA: &str =
    "vrcforge.primitive_basis_binary_ledger_terminal.v1";
pub(crate) const BINARY_LEDGER_READBACK_SCHEMA: &str =
    "vrcforge.primitive_basis_binary_ledger_reopen_readback.v1";
pub(crate) const PROTECTED_EVIDENCE_POLICY_ID: &str = "vrcforge-primitive-origin-v1";

const PROOF_ALGORITHM: &str = "ecdsa-p256-sha256-raw-v1";
const ORIGIN_ENVELOPE_SCHEMA_V1: &str = "vrcforge.primitive_basis_live_origin.v1";
const ORIGIN_ENVELOPE_SCHEMA_V2: &str = "vrcforge.primitive_basis_live_origin.v2";
const MODEL_SCENARIO_ID: &str = "model_part_composition";
const MODEL_PRIMITIVE_ID: &str = "model_part_create";
const RUN_ADMISSION_DOMAIN: &[u8] = b"vrcforge-primitive-basis-run-admission-v1\0";
const BUNDLE_ID_DOMAIN: &[u8] = b"vrcforge-primitive-basis-authority-bundle-id-v1\0";
const PREPARED_BINDING_SOURCE_MAGIC: &[u8; 8] = b"VRCPEB01";
const PREPARED_BINDING_SOURCE_DOMAIN: &[u8] =
    b"vrcforge-primitive-basis-prepared-binding-source-v1\0";
const MAX_RESULT_BYTES: usize = 64 * 1024;
const MAX_ORIGIN_BYTES: usize = 512 * 1024;
const MAX_JSON_DEPTH: usize = 128;
const MAX_AUTHORITY_BUNDLE_BYTES: usize = 8 * 1024 * 1024;
const MAX_LEDGER_SNAPSHOT_BYTES: usize = 2 * 1024 * 1024;
pub const MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES: usize = 10 * 1024 * 1024 + 64 * 1024;

type Digest = [u8; 32];

const PACKAGE_DESKTOP_EXECUTABLE_DIGEST_INDEX: usize = 2;
const PACKAGE_BACKEND_EXECUTABLE_DIGEST_INDEX: usize = 3;
const PACKAGE_RUNNER_DIGEST_INDEX: usize = 5;
const PACKAGE_UNITY_PACKAGE_DIGEST_INDEX: usize = 6;
const PACKAGE_UNITY_EDITOR_DIGEST_INDEX: usize = 9;
const PACKAGE_RUNTIME_BINDING_DIGEST_INDEX: usize = 15;

const P256_ORDER: Digest = [
    0xff, 0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xbc, 0xe6, 0xfa, 0xad, 0xa7, 0x17, 0x9e, 0x84, 0xf3, 0xb9, 0xca, 0xc2, 0xfc, 0x63, 0x25, 0x51,
];
const P256_HALF_ORDER: Digest = [
    0x7f, 0xff, 0xff, 0xff, 0x80, 0x00, 0x00, 0x00, 0x7f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xde, 0x73, 0x7d, 0x56, 0xd3, 0x8b, 0xcf, 0x42, 0x79, 0xdc, 0xe5, 0x61, 0x7e, 0x31, 0x92, 0xa8,
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ProtectedEvidenceBundleError(&'static str);

impl ProtectedEvidenceBundleError {
    pub(crate) fn new(code: &'static str) -> Self {
        Self(code)
    }

    pub(crate) fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for ProtectedEvidenceBundleError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for ProtectedEvidenceBundleError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct FixedAuthorityBinding {
    policy_id: String,
    authority_generation_digest: Digest,
    protected_manifest_digest: Digest,
    installed_layout_digest: Digest,
    service_executable_digest: Digest,
    controller_executable_digest: Digest,
    install_helper_executable_digest: Digest,
    ledger_identity_digest: Digest,
}

impl FixedAuthorityBinding {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        policy_id: &str,
        authority_generation_digest: Digest,
        protected_manifest_digest: Digest,
        installed_layout_digest: Digest,
        service_executable_digest: Digest,
        controller_executable_digest: Digest,
        install_helper_executable_digest: Digest,
        ledger_identity_digest: Digest,
    ) -> Result<Self, ProtectedEvidenceBundleError> {
        require_safe_id(policy_id, "protected_authority_binding_invalid")?;
        require_nonzero_digests(
            &[
                authority_generation_digest,
                protected_manifest_digest,
                installed_layout_digest,
                service_executable_digest,
                controller_executable_digest,
                install_helper_executable_digest,
                ledger_identity_digest,
            ],
            "protected_authority_binding_invalid",
        )?;
        Ok(Self {
            policy_id: policy_id.to_owned(),
            authority_generation_digest,
            protected_manifest_digest,
            installed_layout_digest,
            service_executable_digest,
            controller_executable_digest,
            install_helper_executable_digest,
            ledger_identity_digest,
        })
    }

    fn value(&self) -> Value {
        serde_json::json!({
            "schema": AUTHORITY_BINDING_SCHEMA,
            "policyId": self.policy_id,
            "authorityGenerationDigest": hex_lower(&self.authority_generation_digest),
            "protectedManifestDigest": hex_lower(&self.protected_manifest_digest),
            "installedLayoutDigest": hex_lower(&self.installed_layout_digest),
            "serviceExecutableDigest": hex_lower(&self.service_executable_digest),
            "controllerExecutableDigest": hex_lower(&self.controller_executable_digest),
            "installHelperExecutableDigest": hex_lower(&self.install_helper_executable_digest),
            "ledgerIdentityDigest": hex_lower(&self.ledger_identity_digest),
        })
    }

    fn digests(&self) -> [Digest; 7] {
        [
            self.authority_generation_digest,
            self.protected_manifest_digest,
            self.installed_layout_digest,
            self.service_executable_digest,
            self.controller_executable_digest,
            self.install_helper_executable_digest,
            self.ledger_identity_digest,
        ]
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct FixedPackageBinding {
    version: String,
    digests: [Digest; 16],
}

impl FixedPackageBinding {
    pub(crate) fn new(
        version: &str,
        digests: [Digest; 16],
    ) -> Result<Self, ProtectedEvidenceBundleError> {
        require_version(version)?;
        require_nonzero_digests(&digests, "protected_package_binding_invalid")?;
        Ok(Self {
            version: version.to_owned(),
            digests,
        })
    }

    fn value(&self) -> Value {
        let names = [
            "manifestDigest",
            "portableDigest",
            "desktopExecutableDigest",
            "backendExecutableDigest",
            "backendTreeDigest",
            "runnerDigest",
            "unityPackageDigest",
            "packagedUnityToolTreeDigest",
            "runtimeUnityToolTreeDigest",
            "unityEditorDigest",
            "bridgeLauncherExecutableDigest",
            "bridgeListenerExecutableDigest",
            "connectorDigest",
            "serverDigest",
            "dependencySetDigest",
            "runtimeBindingDigest",
        ];
        let mut value = Map::new();
        value.insert(
            "schema".to_owned(),
            Value::String(PACKAGE_BINDING_SCHEMA.to_owned()),
        );
        value.insert("version".to_owned(), Value::String(self.version.clone()));
        for (name, digest) in names.into_iter().zip(self.digests) {
            value.insert(name.to_owned(), Value::String(hex_lower(&digest)));
        }
        Value::Object(value)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct FixedModelEvidenceBinding {
    fixture_set_descriptor_digest: Digest,
    fixture_set_digest: Digest,
    fixture_descriptor_digest: Digest,
    fixture_digest: Digest,
    fixture_project_input_digest: Digest,
    project_binding_digest: Digest,
}

impl FixedModelEvidenceBinding {
    pub(crate) fn new(
        fixture_set_descriptor_digest: Digest,
        fixture_set_digest: Digest,
        fixture_descriptor_digest: Digest,
        fixture_digest: Digest,
        fixture_project_input_digest: Digest,
        project_binding_digest: Digest,
    ) -> Result<Self, ProtectedEvidenceBundleError> {
        require_nonzero_digests(
            &[
                fixture_set_descriptor_digest,
                fixture_set_digest,
                fixture_descriptor_digest,
                fixture_digest,
                fixture_project_input_digest,
                project_binding_digest,
            ],
            "protected_fixture_binding_invalid",
        )?;
        Ok(Self {
            fixture_set_descriptor_digest,
            fixture_set_digest,
            fixture_descriptor_digest,
            fixture_digest,
            fixture_project_input_digest,
            project_binding_digest,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct FixedProtectedEvidenceBindings {
    authority: FixedAuthorityBinding,
    package: FixedPackageBinding,
    model: FixedModelEvidenceBinding,
}

impl FixedProtectedEvidenceBindings {
    pub(crate) fn new(
        authority: FixedAuthorityBinding,
        package: FixedPackageBinding,
        model: FixedModelEvidenceBinding,
    ) -> Self {
        Self {
            authority,
            package,
            model,
        }
    }
}

/// Immutable, prepare-time evidence source sealed into the supervisor policy.
///
/// Authority and package facts are derived from the authenticated generation
/// source. Fixture-set facts and `held_scenario_binding_digest` are derived only
/// after the exact fixed handle set has been read and validated. The two
/// run-specific model facts are deliberately absent and must be recovered from
/// the service-owned finalization that is bound to the same durable ticket.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct PreparedProtectedEvidenceSource {
    authority: FixedAuthorityBinding,
    package: FixedPackageBinding,
    fixture_set_descriptor_digest: Digest,
    fixture_set_digest: Digest,
    fixture_descriptor_digest: Digest,
    fixture_digest: Digest,
    held_scenario_binding_digest: Digest,
}

impl PreparedProtectedEvidenceSource {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        authority: FixedAuthorityBinding,
        package: FixedPackageBinding,
        fixture_set_descriptor_digest: Digest,
        fixture_set_digest: Digest,
        fixture_descriptor_digest: Digest,
        fixture_digest: Digest,
        held_scenario_binding_digest: Digest,
    ) -> Result<Self, ProtectedEvidenceBundleError> {
        require_nonzero_digests(
            &[
                fixture_set_descriptor_digest,
                fixture_set_digest,
                fixture_descriptor_digest,
                fixture_digest,
                held_scenario_binding_digest,
            ],
            "protected_prepared_binding_source_invalid",
        )?;
        Ok(Self {
            authority,
            package,
            fixture_set_descriptor_digest,
            fixture_set_digest,
            fixture_descriptor_digest,
            fixture_digest,
            held_scenario_binding_digest,
        })
    }

    #[cfg(test)]
    pub(crate) fn for_policy_test(
        authority_generation_digest: Digest,
        process_executable_digests: [Digest; 7],
        seed: Digest,
    ) -> Self {
        Self::for_runtime_identity_test(
            authority_generation_digest,
            Self::test_digest(seed, 1),
            Self::test_digest(seed, 2),
            Self::test_digest(seed, 5),
            process_executable_digests,
            seed,
        )
    }

    #[cfg(test)]
    pub(crate) fn for_runtime_identity_test(
        authority_generation_digest: Digest,
        protected_manifest_digest: Digest,
        installed_layout_digest: Digest,
        ledger_identity_digest: Digest,
        process_executable_digests: [Digest; 7],
        seed: Digest,
    ) -> Self {
        let derive = |tag: u8| Self::test_digest(seed, tag);
        let authority = FixedAuthorityBinding::new(
            PROTECTED_EVIDENCE_POLICY_ID,
            authority_generation_digest,
            protected_manifest_digest,
            installed_layout_digest,
            process_executable_digests[0],
            derive(3),
            derive(4),
            ledger_identity_digest,
        )
        .expect("fixed test authority binding");
        let mut package_digests = std::array::from_fn(|index| derive(20 + index as u8));
        package_digests[PACKAGE_DESKTOP_EXECUTABLE_DIGEST_INDEX] = process_executable_digests[2];
        package_digests[PACKAGE_BACKEND_EXECUTABLE_DIGEST_INDEX] = process_executable_digests[3];
        package_digests[PACKAGE_RUNNER_DIGEST_INDEX] = process_executable_digests[1];
        package_digests[PACKAGE_UNITY_EDITOR_DIGEST_INDEX] = process_executable_digests[4];
        package_digests[10] = process_executable_digests[5];
        package_digests[11] = process_executable_digests[6];
        let package =
            FixedPackageBinding::new("1.4.0", package_digests).expect("fixed test package binding");
        Self::new(
            authority,
            package,
            derive(40),
            derive(41),
            derive(42),
            derive(43),
            derive(44),
        )
        .expect("fixed test prepared evidence source")
    }

    #[cfg(test)]
    fn test_digest(seed: Digest, tag: u8) -> Digest {
        let mut digest = Sha256::new();
        digest.update(b"vrcforge-prepared-evidence-policy-test-v1\0");
        digest.update(seed);
        digest.update([tag]);
        digest.finalize().into()
    }

    pub(crate) fn canonical_bytes(&self) -> Vec<u8> {
        let mut bytes = Vec::with_capacity(1024);
        bytes.extend_from_slice(PREPARED_BINDING_SOURCE_MAGIC);
        push_short_ascii(&mut bytes, &self.authority.policy_id);
        push_short_ascii(&mut bytes, &self.package.version);
        for digest in self.authority.digests() {
            bytes.extend_from_slice(&digest);
        }
        for digest in self.package.digests {
            bytes.extend_from_slice(&digest);
        }
        for digest in [
            self.fixture_set_descriptor_digest,
            self.fixture_set_digest,
            self.fixture_descriptor_digest,
            self.fixture_digest,
            self.held_scenario_binding_digest,
        ] {
            bytes.extend_from_slice(&digest);
        }
        bytes
    }

    pub(crate) fn decode(bytes: &[u8]) -> Result<Self, ProtectedEvidenceBundleError> {
        if bytes.len() < PREPARED_BINDING_SOURCE_MAGIC.len()
            || bytes.get(..8) != Some(PREPARED_BINDING_SOURCE_MAGIC)
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_prepared_binding_source_invalid",
            ));
        }
        let mut offset = PREPARED_BINDING_SOURCE_MAGIC.len();
        let policy_id = take_short_ascii(bytes, &mut offset)?;
        let version = take_short_ascii(bytes, &mut offset)?;
        let mut take_digest = || -> Result<Digest, ProtectedEvidenceBundleError> {
            let end = offset.checked_add(32).ok_or_else(|| {
                ProtectedEvidenceBundleError::new("protected_prepared_binding_source_invalid")
            })?;
            let digest = bytes
                .get(offset..end)
                .and_then(|value| value.try_into().ok())
                .ok_or_else(|| {
                    ProtectedEvidenceBundleError::new("protected_prepared_binding_source_invalid")
                })?;
            offset = end;
            Ok(digest)
        };
        let authority = FixedAuthorityBinding::new(
            &policy_id,
            take_digest()?,
            take_digest()?,
            take_digest()?,
            take_digest()?,
            take_digest()?,
            take_digest()?,
            take_digest()?,
        )?;
        let mut package_digests = [[0u8; 32]; 16];
        for digest in &mut package_digests {
            *digest = take_digest()?;
        }
        let package = FixedPackageBinding::new(&version, package_digests)?;
        let fixture_set_descriptor_digest = take_digest()?;
        let fixture_set_digest = take_digest()?;
        let fixture_descriptor_digest = take_digest()?;
        let fixture_digest = take_digest()?;
        let held_scenario_binding_digest = take_digest()?;
        if offset != bytes.len() {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_prepared_binding_source_invalid",
            ));
        }
        let source = Self::new(
            authority,
            package,
            fixture_set_descriptor_digest,
            fixture_set_digest,
            fixture_descriptor_digest,
            fixture_digest,
            held_scenario_binding_digest,
        )?;
        if source.canonical_bytes() != bytes {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_prepared_binding_source_invalid",
            ));
        }
        Ok(source)
    }

    pub(crate) fn digest(&self) -> Digest {
        let mut digest = Sha256::new();
        digest.update(PREPARED_BINDING_SOURCE_DOMAIN);
        digest.update(self.canonical_bytes());
        digest.finalize().into()
    }

    pub(crate) fn matches_runtime_identity(
        &self,
        authority_generation_digest: &Digest,
        protected_manifest_digest: &Digest,
        installed_layout_digest: &Digest,
        service_executable_digest: &Digest,
    ) -> bool {
        self.authority.authority_generation_digest == *authority_generation_digest
            && self.authority.protected_manifest_digest == *protected_manifest_digest
            && self.authority.installed_layout_digest == *installed_layout_digest
            && self.authority.service_executable_digest == *service_executable_digest
    }

    pub(crate) fn held_scenario_binding_digest(&self) -> &Digest {
        &self.held_scenario_binding_digest
    }

    pub(crate) fn policy_id(&self) -> &str {
        &self.authority.policy_id
    }

    pub(crate) fn version(&self) -> &str {
        &self.package.version
    }

    pub(crate) fn fixture_set_descriptor_digest(&self) -> &Digest {
        &self.fixture_set_descriptor_digest
    }

    pub(crate) fn fixture_set_digest(&self) -> &Digest {
        &self.fixture_set_digest
    }

    pub(crate) fn fixture_descriptor_digest(&self) -> &Digest {
        &self.fixture_descriptor_digest
    }

    pub(crate) fn fixture_digest(&self) -> &Digest {
        &self.fixture_digest
    }

    pub(crate) fn package_digest(&self, index: usize) -> Option<&Digest> {
        self.package.digests.get(index)
    }

    pub(crate) fn matches_policy_processes(
        &self,
        authority_generation_digest: &Digest,
        process_executable_digests: &[Digest; 7],
    ) -> bool {
        self.authority.authority_generation_digest == *authority_generation_digest
            && self.authority.service_executable_digest == process_executable_digests[0]
            && self.package.digests[PACKAGE_DESKTOP_EXECUTABLE_DIGEST_INDEX]
                == process_executable_digests[2]
            && self.package.digests[PACKAGE_BACKEND_EXECUTABLE_DIGEST_INDEX]
                == process_executable_digests[3]
            && self.package.digests[PACKAGE_RUNNER_DIGEST_INDEX] == process_executable_digests[1]
            && self.package.digests[PACKAGE_UNITY_EDITOR_DIGEST_INDEX]
                == process_executable_digests[4]
            && self.package.digests[10] == process_executable_digests[5]
            && self.package.digests[11] == process_executable_digests[6]
    }

    pub(crate) fn resolve_for_result(
        &self,
        result: &ServiceOwnedVerifiedRuntimeResult,
    ) -> Result<FixedProtectedEvidenceBindings, ProtectedEvidenceBundleError> {
        let ticket = result
            .origin_envelope
            .get("ticket")
            .and_then(Value::as_object)
            .ok_or_else(|| {
                ProtectedEvidenceBundleError::new("protected_result_binding_source_mismatch")
            })?;
        if ticket.get("runId").and_then(Value::as_str) != Some(result.run_id.as_str())
            || ticket.get("policyId").and_then(Value::as_str)
                != Some(self.authority.policy_id.as_str())
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_result_binding_source_mismatch",
            ));
        }
        let attestation = result
            .finalization
            .get("attestation")
            .and_then(Value::as_object)
            .ok_or_else(|| {
                ProtectedEvidenceBundleError::new("protected_result_binding_source_mismatch")
            })?;
        let required = |field: &str| {
            attestation
                .get(field)
                .and_then(Value::as_str)
                .and_then(decode_digest)
                .ok_or_else(|| {
                    ProtectedEvidenceBundleError::new("protected_result_binding_source_mismatch")
                })
        };
        if attestation.get("runId").and_then(Value::as_str) != Some(result.run_id.as_str())
            || required("fixtureSetDescriptorDigest")? != self.fixture_set_descriptor_digest
            || required("fixtureDescriptorDigest")? != self.fixture_descriptor_digest
            || required("fixtureDigest")? != self.fixture_digest
            || required("desktopExecutableDigest")?
                != self.package.digests[PACKAGE_DESKTOP_EXECUTABLE_DIGEST_INDEX]
            || required("backendExecutableDigest")?
                != self.package.digests[PACKAGE_BACKEND_EXECUTABLE_DIGEST_INDEX]
            || required("runnerDigest")? != self.package.digests[PACKAGE_RUNNER_DIGEST_INDEX]
            || required("unityPackageDigest")?
                != self.package.digests[PACKAGE_UNITY_PACKAGE_DIGEST_INDEX]
            || required("unityEditorDigest")?
                != self.package.digests[PACKAGE_UNITY_EDITOR_DIGEST_INDEX]
            || required("runtimeBindingDigest")?
                != self.package.digests[PACKAGE_RUNTIME_BINDING_DIGEST_INDEX]
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_result_binding_source_mismatch",
            ));
        }
        let model = FixedModelEvidenceBinding::new(
            self.fixture_set_descriptor_digest,
            self.fixture_set_digest,
            self.fixture_descriptor_digest,
            self.fixture_digest,
            required("fixtureProjectInputDigest")?,
            required("projectBindingDigest")?,
        )?;
        Ok(FixedProtectedEvidenceBindings::new(
            self.authority.clone(),
            self.package.clone(),
            model,
        ))
    }
}

fn push_short_ascii(bytes: &mut Vec<u8>, value: &str) {
    debug_assert!(value.is_ascii() && !value.is_empty() && value.len() <= u8::MAX as usize);
    bytes.push(value.len() as u8);
    bytes.extend_from_slice(value.as_bytes());
}

fn take_short_ascii(
    bytes: &[u8],
    offset: &mut usize,
) -> Result<String, ProtectedEvidenceBundleError> {
    let length = usize::from(*bytes.get(*offset).ok_or_else(|| {
        ProtectedEvidenceBundleError::new("protected_prepared_binding_source_invalid")
    })?);
    *offset += 1;
    let end = offset.checked_add(length).ok_or_else(|| {
        ProtectedEvidenceBundleError::new("protected_prepared_binding_source_invalid")
    })?;
    let value = bytes.get(*offset..end).ok_or_else(|| {
        ProtectedEvidenceBundleError::new("protected_prepared_binding_source_invalid")
    })?;
    if length == 0 || !value.is_ascii() {
        return Err(ProtectedEvidenceBundleError::new(
            "protected_prepared_binding_source_invalid",
        ));
    }
    *offset = end;
    String::from_utf8(value.to_vec())
        .map_err(|_| ProtectedEvidenceBundleError::new("protected_prepared_binding_source_invalid"))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ServiceOwnedVerifiedRuntimeResult {
    finalization_bytes: Vec<u8>,
    finalization: Value,
    finalization_digest: Digest,
    origin_envelope_bytes: Vec<u8>,
    origin_envelope: Value,
    origin_envelope_digest: Digest,
    authority_ticket_digest: Digest,
    origin_ticket_digest: Digest,
    dual_ticket_binding_v2: bool,
    run_id: String,
    run_binding_digest: Digest,
    cleanup_digest: Digest,
    prepared_receipt_digest: Digest,
    armed_receipt_digest: Digest,
    policy_snapshot_digest: Digest,
    recovery_bundle_digest: Digest,
}

impl ServiceOwnedVerifiedRuntimeResult {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn from_verified_terminal(
        finalization_bytes: Vec<u8>,
        origin_envelope_bytes: Vec<u8>,
        ticket_digest: Digest,
        run_binding_digest: Digest,
        cleanup_digest: Digest,
        prepared_receipt_digest: Digest,
        armed_receipt_digest: Digest,
        policy_snapshot_digest: Digest,
        recovery_bundle_digest: Digest,
    ) -> Result<Self, ProtectedEvidenceBundleError> {
        require_nonzero_digests(
            &[
                ticket_digest,
                run_binding_digest,
                cleanup_digest,
                prepared_receipt_digest,
                armed_receipt_digest,
                policy_snapshot_digest,
                recovery_bundle_digest,
            ],
            "protected_runtime_result_invalid",
        )?;
        let finalization = parse_canonical_ascii_object(
            &finalization_bytes,
            MAX_RESULT_BYTES,
            "protected_finalization_invalid",
        )?;
        let origin_envelope = parse_canonical_ascii_object(
            &origin_envelope_bytes,
            MAX_ORIGIN_BYTES,
            "protected_origin_envelope_invalid",
        )?;
        let envelope_cleanup = digest_value(&origin_envelope, "cleanupDigest")?;
        let (run_id, origin_ticket_digest, dual_ticket_binding_v2) =
            validate_origin_ticket_binding(&origin_envelope, ticket_digest)?;
        if envelope_cleanup != cleanup_digest {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_runtime_origin_binding_mismatch",
            ));
        }
        Ok(Self {
            finalization_digest: sha256(&finalization_bytes),
            finalization_bytes,
            finalization,
            origin_envelope_digest: sha256(&origin_envelope_bytes),
            origin_envelope_bytes,
            origin_envelope,
            authority_ticket_digest: ticket_digest,
            origin_ticket_digest,
            dual_ticket_binding_v2,
            run_id,
            run_binding_digest,
            cleanup_digest,
            prepared_receipt_digest,
            armed_receipt_digest,
            policy_snapshot_digest,
            recovery_bundle_digest,
        })
    }

    #[cfg(windows)]
    pub(crate) fn from_native_completed(
        completed: &NativeCompletedRunProof,
    ) -> Result<Self, ProtectedEvidenceBundleError> {
        let terminal = completed.terminal();
        let admission = completed.admission();
        let result = Self::from_verified_terminal(
            completed.result_bytes().to_vec(),
            completed.canonical_origin_envelope_bytes().to_vec(),
            *terminal.ticket_digest(),
            *terminal.run_binding_digest(),
            *completed.cleanup_receipt_digest(),
            *admission.prepared_receipt_digest(),
            *admission.armed_receipt_digest(),
            *admission.policy_snapshot_digest(),
            *admission.recovery_bundle_digest(),
        )?;
        if !result.dual_ticket_binding_v2 {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_origin_envelope_v2_required",
            ));
        }
        if result.origin_ticket_digest != *completed.origin_ticket_digest()
            || result.authority_ticket_digest != *completed.authority_ticket_digest()
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_runtime_origin_binding_mismatch",
            ));
        }
        Ok(result)
    }

    pub(crate) fn finalization_bytes(&self) -> &[u8] {
        &self.finalization_bytes
    }

    pub(crate) fn finalization_digest(&self) -> &Digest {
        &self.finalization_digest
    }

    pub(crate) fn origin_envelope_bytes(&self) -> &[u8] {
        &self.origin_envelope_bytes
    }

    pub(crate) fn origin_envelope_digest(&self) -> &Digest {
        &self.origin_envelope_digest
    }

    pub(crate) fn ticket_digest(&self) -> &Digest {
        &self.authority_ticket_digest
    }

    pub(crate) fn authority_ticket_digest(&self) -> &Digest {
        &self.authority_ticket_digest
    }

    pub(crate) fn origin_ticket_digest(&self) -> &Digest {
        &self.origin_ticket_digest
    }

    pub(crate) fn run_binding_digest(&self) -> &Digest {
        &self.run_binding_digest
    }

    pub(crate) fn cleanup_digest(&self) -> &Digest {
        &self.cleanup_digest
    }

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

    pub(crate) fn durable_terminal_timestamps(
        &self,
    ) -> Result<(&str, &str, &str), ProtectedEvidenceBundleError> {
        let issued_at = self
            .origin_envelope
            .get("ticket")
            .and_then(|ticket| ticket.get("issuedAt"))
            .and_then(Value::as_str)
            .ok_or_else(|| {
                ProtectedEvidenceBundleError::new("protected_timestamp_binding_invalid")
            })?;
        let consumed_at = self
            .finalization
            .get("attestation")
            .and_then(|attestation| attestation.get("startedAt"))
            .and_then(Value::as_str)
            .ok_or_else(|| {
                ProtectedEvidenceBundleError::new("protected_timestamp_binding_invalid")
            })?;
        let completed_at = self
            .origin_envelope
            .get("signedAt")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                ProtectedEvidenceBundleError::new("protected_timestamp_binding_invalid")
            })?;
        require_timestamp(issued_at)?;
        require_timestamp(consumed_at)?;
        require_timestamp(completed_at)?;
        if issued_at > consumed_at || consumed_at > completed_at {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_timestamp_binding_invalid",
            ));
        }
        Ok((issued_at, consumed_at, completed_at))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ReopenedBinaryLedgerReadback {
    ledger_file_digest: Digest,
    anchor_file_digest: Digest,
    ledger_file_identity_digest: Digest,
    anchor_file_identity_digest: Digest,
    ledger_length: u64,
    anchor_length: u64,
    frame_count: u64,
    active_ticket_count: u64,
    latest_frame_digest: Digest,
    anchor_record_digest: Digest,
    terminal_sequence: u64,
    terminal_frame_digest: Digest,
    terminal_ticket_digest: Digest,
}

impl ReopenedBinaryLedgerReadback {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn from_held_and_reopened_ledger(
        ledger_file_digest: Digest,
        anchor_file_digest: Digest,
        ledger_file_identity_digest: Digest,
        anchor_file_identity_digest: Digest,
        ledger_length: u64,
        anchor_length: u64,
        frame_count: u64,
        active_ticket_count: u64,
        latest_frame_digest: Digest,
        anchor_record_digest: Digest,
        terminal_sequence: u64,
        terminal_frame_digest: Digest,
        terminal_ticket_digest: Digest,
    ) -> Result<Self, ProtectedEvidenceBundleError> {
        require_nonzero_digests(
            &[
                ledger_file_digest,
                anchor_file_digest,
                ledger_file_identity_digest,
                anchor_file_identity_digest,
                latest_frame_digest,
                anchor_record_digest,
                terminal_frame_digest,
                terminal_ticket_digest,
            ],
            "protected_binary_readback_invalid",
        )?;
        if ledger_length == 0
            || anchor_length == 0
            || active_ticket_count != 0
            || terminal_sequence.checked_add(1) != Some(frame_count)
            || latest_frame_digest != terminal_frame_digest
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_binary_readback_invalid",
            ));
        }
        Ok(Self {
            ledger_file_digest,
            anchor_file_digest,
            ledger_file_identity_digest,
            anchor_file_identity_digest,
            ledger_length,
            anchor_length,
            frame_count,
            active_ticket_count,
            latest_frame_digest,
            anchor_record_digest,
            terminal_sequence,
            terminal_frame_digest,
            terminal_ticket_digest,
        })
    }

    fn value(&self, authority: &FixedAuthorityBinding) -> Value {
        serde_json::json!({
            "schema": BINARY_LEDGER_READBACK_SCHEMA,
            "readbackKind": "heldAndReopenedStable",
            "authorityGenerationDigest": hex_lower(&authority.authority_generation_digest),
            "ledgerIdentityDigest": hex_lower(&authority.ledger_identity_digest),
            "ledgerFileDigest": hex_lower(&self.ledger_file_digest),
            "anchorFileDigest": hex_lower(&self.anchor_file_digest),
            "ledgerFileIdentityDigest": hex_lower(&self.ledger_file_identity_digest),
            "anchorFileIdentityDigest": hex_lower(&self.anchor_file_identity_digest),
            "ledgerLength": self.ledger_length,
            "anchorLength": self.anchor_length,
            "frameCount": self.frame_count,
            "activeTicketCount": self.active_ticket_count,
            "latestFrameDigest": hex_lower(&self.latest_frame_digest),
            "anchorRecordDigest": hex_lower(&self.anchor_record_digest),
            "terminalSequence": self.terminal_sequence,
            "terminalFrameDigest": hex_lower(&self.terminal_frame_digest),
            "terminalTicketDigest": hex_lower(&self.terminal_ticket_digest),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct DurableBinaryLedgerTerminal {
    receipt_ordinal: u64,
    previous_receipt_digest: Digest,
    predecessor_sequence: u64,
    terminal_sequence: u64,
    predecessor_frame_digest: Digest,
    terminal_frame_digest: Digest,
    terminal_ticket_digest: Digest,
    terminal_result_digest: Digest,
    anchor_sequence: u64,
    anchor_frame_digest: Digest,
    anchor_ticket_digest: Digest,
    run_binding_digest: Digest,
    prepared_receipt_digest: Digest,
    armed_receipt_digest: Digest,
    policy_snapshot_digest: Digest,
    recovery_bundle_digest: Digest,
    origin_envelope_digest: Digest,
    cleanup_digest: Digest,
    anchor_record_digest: Digest,
    issued_at: String,
    consumed_at: String,
    completed_at: String,
    readback: ReopenedBinaryLedgerReadback,
}

impl DurableBinaryLedgerTerminal {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn from_reopened_result_commit(
        receipt_ordinal: u64,
        previous_receipt_digest: Digest,
        predecessor_sequence: u64,
        terminal_sequence: u64,
        predecessor_frame_digest: Digest,
        terminal_frame_digest: Digest,
        terminal_ticket_digest: Digest,
        terminal_result_digest: Digest,
        anchor_sequence: u64,
        anchor_frame_digest: Digest,
        anchor_ticket_digest: Digest,
        run_binding_digest: Digest,
        prepared_receipt_digest: Digest,
        armed_receipt_digest: Digest,
        policy_snapshot_digest: Digest,
        recovery_bundle_digest: Digest,
        origin_envelope_digest: Digest,
        cleanup_digest: Digest,
        anchor_record_digest: Digest,
        issued_at: &str,
        consumed_at: &str,
        completed_at: &str,
        readback: ReopenedBinaryLedgerReadback,
    ) -> Result<Self, ProtectedEvidenceBundleError> {
        require_nonzero_digests(
            &[
                previous_receipt_digest,
                predecessor_frame_digest,
                terminal_frame_digest,
                terminal_ticket_digest,
                terminal_result_digest,
                anchor_frame_digest,
                anchor_ticket_digest,
                run_binding_digest,
                prepared_receipt_digest,
                armed_receipt_digest,
                policy_snapshot_digest,
                recovery_bundle_digest,
                origin_envelope_digest,
                cleanup_digest,
                anchor_record_digest,
            ],
            "protected_binary_terminal_invalid",
        )?;
        require_timestamp(issued_at)?;
        require_timestamp(consumed_at)?;
        require_timestamp(completed_at)?;
        if receipt_ordinal == 0
            || predecessor_sequence >= terminal_sequence
            || anchor_sequence != terminal_sequence
            || anchor_frame_digest != terminal_frame_digest
            || anchor_ticket_digest != terminal_ticket_digest
            || readback.terminal_sequence != terminal_sequence
            || readback.terminal_frame_digest != terminal_frame_digest
            || readback.terminal_ticket_digest != terminal_ticket_digest
            || readback.anchor_record_digest != anchor_record_digest
            || issued_at > consumed_at
            || consumed_at > completed_at
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_binary_terminal_invalid",
            ));
        }
        Ok(Self {
            receipt_ordinal,
            previous_receipt_digest,
            predecessor_sequence,
            terminal_sequence,
            predecessor_frame_digest,
            terminal_frame_digest,
            terminal_ticket_digest,
            terminal_result_digest,
            anchor_sequence,
            anchor_frame_digest,
            anchor_ticket_digest,
            run_binding_digest,
            prepared_receipt_digest,
            armed_receipt_digest,
            policy_snapshot_digest,
            recovery_bundle_digest,
            origin_envelope_digest,
            cleanup_digest,
            anchor_record_digest,
            issued_at: issued_at.to_owned(),
            consumed_at: consumed_at.to_owned(),
            completed_at: completed_at.to_owned(),
            readback,
        })
    }

    fn run_admission_digest(&self) -> Digest {
        run_admission_digest(
            &self.run_binding_digest,
            &self.prepared_receipt_digest,
            &self.armed_receipt_digest,
            &self.policy_snapshot_digest,
            &self.recovery_bundle_digest,
        )
    }

    fn value(&self, authority: &FixedAuthorityBinding) -> Value {
        let readback = self.readback.value(authority);
        serde_json::json!({
            "schema": BINARY_LEDGER_TERMINAL_SCHEMA,
            "event": "resultCommit",
            "authorityGenerationDigest": hex_lower(&authority.authority_generation_digest),
            "ledgerIdentityDigest": hex_lower(&authority.ledger_identity_digest),
            "predecessorSequence": self.predecessor_sequence,
            "terminalSequence": self.terminal_sequence,
            "predecessorFrameDigest": hex_lower(&self.predecessor_frame_digest),
            "terminalFrameDigest": hex_lower(&self.terminal_frame_digest),
            "terminalTicketDigest": hex_lower(&self.terminal_ticket_digest),
            "terminalResultDigest": hex_lower(&self.terminal_result_digest),
            "anchorSequence": self.anchor_sequence,
            "anchorFrameDigest": hex_lower(&self.anchor_frame_digest),
            "anchorTicketDigest": hex_lower(&self.anchor_ticket_digest),
            "runBindingDigest": hex_lower(&self.run_binding_digest),
            "preparedReceiptDigest": hex_lower(&self.prepared_receipt_digest),
            "armedReceiptDigest": hex_lower(&self.armed_receipt_digest),
            "policySnapshotDigest": hex_lower(&self.policy_snapshot_digest),
            "recoveryBundleDigest": hex_lower(&self.recovery_bundle_digest),
            "runAdmissionDigest": hex_lower(&self.run_admission_digest()),
            "originEnvelopeDigest": hex_lower(&self.origin_envelope_digest),
            "cleanupDigest": hex_lower(&self.cleanup_digest),
            "anchorRecordDigest": hex_lower(&self.anchor_record_digest),
            "reopenReadback": readback,
            "reopenReadbackDigest": hex_lower(&digest_value_bytes(&readback)),
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ProtectedBundleSigningDigest(Digest);

impl ProtectedBundleSigningDigest {
    pub(crate) fn as_bytes(&self) -> &Digest {
        &self.0
    }
}

pub(crate) trait ProtectedEvidenceBundleSigner: Send {
    fn signer_key_id(&self) -> Digest;

    fn sign_protected_bundle(
        &mut self,
        digest: ProtectedBundleSigningDigest,
    ) -> Result<[u8; 64], ProtectedEvidenceBundleError>;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifiedAuthorityResultProjection {
    canonical_bytes: Vec<u8>,
    sha256: Digest,
    authority_generation_digest: Digest,
    ledger_identity_digest: Digest,
    finalization_bytes: Vec<u8>,
    finalization_digest: Digest,
    origin_envelope_bytes: Vec<u8>,
    origin_envelope_digest: Digest,
    ticket_digest: Digest,
    origin_ticket_digest: Digest,
    dual_ticket_binding_v2: bool,
    run_binding_digest: Digest,
    cleanup_digest: Digest,
    prepared_receipt_digest: Digest,
    armed_receipt_digest: Digest,
    policy_snapshot_digest: Digest,
    recovery_bundle_digest: Digest,
    binary_result_commit_frame_digest: Digest,
    anchor_record_digest: Digest,
    reopen_readback_digest: Digest,
}

impl VerifiedAuthorityResultProjection {
    pub fn canonical_bytes(&self) -> &[u8] {
        &self.canonical_bytes
    }

    pub fn sha256(&self) -> &Digest {
        &self.sha256
    }

    pub(crate) fn authority_generation_digest(&self) -> &Digest {
        &self.authority_generation_digest
    }

    pub(crate) fn ledger_identity_digest(&self) -> &Digest {
        &self.ledger_identity_digest
    }

    pub(crate) fn finalization_digest(&self) -> &Digest {
        &self.finalization_digest
    }

    pub(crate) fn origin_envelope_digest(&self) -> &Digest {
        &self.origin_envelope_digest
    }

    pub(crate) fn ticket_digest(&self) -> &Digest {
        &self.ticket_digest
    }

    pub(crate) fn origin_ticket_digest(&self) -> &Digest {
        &self.origin_ticket_digest
    }

    pub(crate) fn run_binding_digest(&self) -> &Digest {
        &self.run_binding_digest
    }

    #[cfg(test)]
    pub(crate) fn for_signed_receipt_contract_test(
        canonical_bytes: Vec<u8>,
        authority_generation_digest: Digest,
        ledger_identity_digest: Digest,
        ticket_digest: Digest,
        run_binding_digest: Digest,
    ) -> Result<Self, ProtectedEvidenceBundleError> {
        require_nonzero_digests(
            &[
                authority_generation_digest,
                ledger_identity_digest,
                ticket_digest,
                run_binding_digest,
            ],
            "protected_projection_test_binding_invalid",
        )?;
        if canonical_bytes.is_empty()
            || canonical_bytes.len() > MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_projection_test_bytes_invalid",
            ));
        }
        let sha256 = sha256(&canonical_bytes);
        Ok(Self {
            canonical_bytes,
            sha256,
            authority_generation_digest,
            ledger_identity_digest,
            finalization_bytes: b"{}".to_vec(),
            finalization_digest: [0xa1; 32],
            origin_envelope_bytes: b"{}".to_vec(),
            origin_envelope_digest: [0xa2; 32],
            ticket_digest,
            origin_ticket_digest: ticket_digest,
            dual_ticket_binding_v2: true,
            run_binding_digest,
            cleanup_digest: [0xa3; 32],
            prepared_receipt_digest: [0xa4; 32],
            armed_receipt_digest: [0xa5; 32],
            policy_snapshot_digest: [0xa6; 32],
            recovery_bundle_digest: [0xa7; 32],
            binary_result_commit_frame_digest: [0xa8; 32],
            anchor_record_digest: [0xa9; 32],
            reopen_readback_digest: [0xaa; 32],
        })
    }

    pub(crate) fn from_immutable_ledger_readback(
        canonical_bytes: Vec<u8>,
        expected_sha256: Digest,
    ) -> Result<Self, ProtectedEvidenceBundleError> {
        if canonical_bytes.is_empty()
            || canonical_bytes.len() > MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES
            || sha256(&canonical_bytes) != expected_sha256
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_projection_readback_mismatch",
            ));
        }
        let projection = parse_canonical_ascii_object(
            &canonical_bytes,
            MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES,
            "protected_projection_readback_invalid",
        )?;
        require_exact_keys(
            &projection,
            &[
                "schema",
                "authorityBundle",
                "authorityBundleDigest",
                "ledgerSnapshot",
                "ledgerSnapshotDigest",
            ],
            "protected_projection_shape_invalid",
        )?;
        if projection.get("schema").and_then(Value::as_str) != Some(PROJECTION_SCHEMA) {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_projection_schema_invalid",
            ));
        }
        let bundle = projection
            .get("authorityBundle")
            .filter(|value| value.is_object())
            .ok_or_else(|| {
                ProtectedEvidenceBundleError::new("protected_projection_shape_invalid")
            })?;
        let ledger = projection
            .get("ledgerSnapshot")
            .filter(|value| value.is_object())
            .ok_or_else(|| {
                ProtectedEvidenceBundleError::new("protected_projection_shape_invalid")
            })?;
        let bundle_bytes = canonical_ascii_json(bundle)?;
        let ledger_bytes = canonical_ascii_json(ledger)?;
        if bundle_bytes.len() > MAX_AUTHORITY_BUNDLE_BYTES
            || ledger_bytes.len() > MAX_LEDGER_SNAPSHOT_BYTES
            || digest_value(&projection, "authorityBundleDigest")? != sha256(&bundle_bytes)
            || digest_value(&projection, "ledgerSnapshotDigest")? != sha256(&ledger_bytes)
            || digest_value(bundle, "ledgerSnapshotDigest")? != sha256(&ledger_bytes)
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_projection_digest_mismatch",
            ));
        }
        require_exact_keys(
            bundle,
            &[
                "schema",
                "bundleId",
                "proofAlgorithm",
                "policyId",
                "signerKeyId",
                "authorityBinding",
                "authorityBindingDigest",
                "packageBinding",
                "packageBindingDigest",
                "fixtureSetDescriptorDigest",
                "fixtureSetDigest",
                "ledgerSnapshotDigest",
                "rows",
                "signedAt",
                "signature",
            ],
            "protected_bundle_shape_invalid",
        )?;
        if bundle.get("schema").and_then(Value::as_str) != Some(AUTHORITY_BUNDLE_SCHEMA)
            || bundle.get("proofAlgorithm").and_then(Value::as_str) != Some(PROOF_ALGORITHM)
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_bundle_schema_invalid",
            ));
        }
        let signature = bundle
            .get("signature")
            .and_then(Value::as_str)
            .and_then(base64url_decode_64)
            .filter(signature_is_canonical)
            .ok_or_else(|| {
                ProtectedEvidenceBundleError::new("protected_bundle_signature_invalid")
            })?;
        let _ = signature;
        let rows = bundle
            .get("rows")
            .and_then(Value::as_array)
            .filter(|rows| rows.len() == 1)
            .ok_or_else(|| ProtectedEvidenceBundleError::new("protected_row_set_invalid"))?;
        let row = &rows[0];
        require_exact_keys(
            row,
            &[
                "schema",
                "scenarioId",
                "primitiveId",
                "fixtureDescriptorDigest",
                "fixtureDigest",
                "fixtureProjectInputDigest",
                "projectBindingDigest",
                "finalization",
                "finalizationDigest",
                "originEnvelope",
                "originEnvelopeDigest",
            ],
            "protected_row_shape_invalid",
        )?;
        if row.get("schema").and_then(Value::as_str) != Some(AUTHORITY_ROW_SCHEMA)
            || row.get("scenarioId").and_then(Value::as_str) != Some(MODEL_SCENARIO_ID)
            || row.get("primitiveId").and_then(Value::as_str) != Some(MODEL_PRIMITIVE_ID)
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_row_schema_invalid",
            ));
        }
        let finalization = row
            .get("finalization")
            .filter(|value| value.is_object())
            .ok_or_else(|| ProtectedEvidenceBundleError::new("protected_finalization_invalid"))?;
        let origin = row
            .get("originEnvelope")
            .filter(|value| value.is_object())
            .ok_or_else(|| {
                ProtectedEvidenceBundleError::new("protected_origin_envelope_invalid")
            })?;
        let finalization_bytes = canonical_ascii_json(finalization)?;
        let origin_envelope_bytes = canonical_ascii_json(origin)?;
        let finalization_digest = digest_value(row, "finalizationDigest")?;
        let origin_envelope_digest = digest_value(row, "originEnvelopeDigest")?;
        if sha256(&finalization_bytes) != finalization_digest
            || sha256(&origin_envelope_bytes) != origin_envelope_digest
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_row_digest_mismatch",
            ));
        }
        require_exact_keys(
            ledger,
            &[
                "schema",
                "authorityGenerationDigest",
                "ledgerIdentityDigest",
                "firstReceiptOrdinal",
                "lastReceiptOrdinal",
                "initialReceiptDigest",
                "terminalReceiptDigest",
                "receipts",
            ],
            "protected_ledger_shape_invalid",
        )?;
        if ledger.get("schema").and_then(Value::as_str) != Some(LEDGER_SNAPSHOT_SCHEMA) {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_ledger_schema_invalid",
            ));
        }
        let receipts = ledger
            .get("receipts")
            .and_then(Value::as_array)
            .filter(|receipts| receipts.len() == 1)
            .ok_or_else(|| ProtectedEvidenceBundleError::new("protected_receipt_set_invalid"))?;
        let authority_generation_digest = digest_value(ledger, "authorityGenerationDigest")?;
        let ledger_identity_digest = digest_value(ledger, "ledgerIdentityDigest")?;
        let receipt = &receipts[0];
        let receipt_schema = receipt.get("schema").and_then(Value::as_str);
        let dual_ticket_binding_v2 = match receipt_schema {
            Some(LEDGER_RECEIPT_SCHEMA) => true,
            Some(LEDGER_RECEIPT_SCHEMA_V1) => false,
            _ => {
                return Err(ProtectedEvidenceBundleError::new(
                    "protected_receipt_binding_mismatch",
                ))
            }
        };
        let mut receipt_keys = vec![
            "schema",
            "ordinal",
            "previousReceiptDigest",
            "receiptDigest",
            "ticketDigest",
            "runId",
            "scenarioId",
            "primitiveId",
            "state",
            "resultDigest",
            "originEnvelopeDigest",
            "cleanupDigest",
            "issuedAt",
            "consumedAt",
            "completedAt",
            "binaryLedgerTerminal",
            "binaryLedgerTerminalDigest",
        ];
        if dual_ticket_binding_v2 {
            receipt_keys.push("originTicketDigest");
        }
        require_exact_keys(receipt, &receipt_keys, "protected_receipt_shape_invalid")?;
        if receipt.get("state").and_then(Value::as_str) != Some("completed")
            || digest_value(receipt, "resultDigest")? != finalization_digest
            || digest_value(receipt, "originEnvelopeDigest")? != origin_envelope_digest
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_receipt_binding_mismatch",
            ));
        }
        let terminal = receipt
            .get("binaryLedgerTerminal")
            .filter(|value| value.is_object())
            .ok_or_else(|| {
                ProtectedEvidenceBundleError::new("protected_binary_terminal_invalid")
            })?;
        if digest_value(receipt, "binaryLedgerTerminalDigest")? != digest_value_bytes(terminal)
            || terminal.get("schema").and_then(Value::as_str) != Some(BINARY_LEDGER_TERMINAL_SCHEMA)
            || terminal.get("event").and_then(Value::as_str) != Some("resultCommit")
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_binary_terminal_invalid",
            ));
        }
        let readback = terminal
            .get("reopenReadback")
            .filter(|value| value.is_object())
            .ok_or_else(|| {
                ProtectedEvidenceBundleError::new("protected_binary_readback_invalid")
            })?;
        if terminal
            .get("reopenReadbackDigest")
            .and_then(Value::as_str)
            .and_then(decode_digest)
            != Some(digest_value_bytes(readback))
            || readback.get("schema").and_then(Value::as_str) != Some(BINARY_LEDGER_READBACK_SCHEMA)
            || readback.get("readbackKind").and_then(Value::as_str) != Some("heldAndReopenedStable")
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_binary_readback_invalid",
            ));
        }
        let ticket_digest = digest_value(receipt, "ticketDigest")?;
        let receipt_cleanup_digest = digest_value(receipt, "cleanupDigest")?;
        if digest_value(terminal, "terminalTicketDigest")? != ticket_digest
            || digest_value(terminal, "anchorTicketDigest")? != ticket_digest
            || digest_value(readback, "terminalTicketDigest")? != ticket_digest
            || digest_value(terminal, "terminalResultDigest")? != finalization_digest
            || digest_value(terminal, "originEnvelopeDigest")? != origin_envelope_digest
            || digest_value(terminal, "cleanupDigest")? != receipt_cleanup_digest
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_receipt_terminal_binding_mismatch",
            ));
        }
        let origin_ticket_digest = if dual_ticket_binding_v2 {
            let (origin_run_id, origin_ticket_digest, origin_dual_ticket_binding_v2) =
                validate_origin_ticket_binding(origin, ticket_digest)?;
            if !origin_dual_ticket_binding_v2
                || receipt.get("runId").and_then(Value::as_str) != Some(origin_run_id.as_str())
                || digest_value(receipt, "originTicketDigest")? != origin_ticket_digest
            {
                return Err(ProtectedEvidenceBundleError::new(
                    "protected_receipt_ticket_binding_mismatch",
                ));
            }
            origin_ticket_digest
        } else {
            // Preserve the original v1 projection parser contract exactly. The old
            // completed receipt carried no separately bound source-ticket digest,
            // so it remains readable but cannot be promoted by the v2 producer.
            ticket_digest
        };
        let run_binding_digest = digest_value(terminal, "runBindingDigest")?;
        let cleanup_digest = receipt_cleanup_digest;
        let prepared_receipt_digest = digest_value(terminal, "preparedReceiptDigest")?;
        let armed_receipt_digest = digest_value(terminal, "armedReceiptDigest")?;
        let policy_snapshot_digest = digest_value(terminal, "policySnapshotDigest")?;
        let recovery_bundle_digest = digest_value(terminal, "recoveryBundleDigest")?;
        let binary_result_commit_frame_digest = digest_value(terminal, "terminalFrameDigest")?;
        let anchor_record_digest = digest_value(terminal, "anchorRecordDigest")?;
        let reopen_readback_digest = digest_value(terminal, "reopenReadbackDigest")?;
        Ok(Self {
            canonical_bytes,
            sha256: expected_sha256,
            authority_generation_digest,
            ledger_identity_digest,
            finalization_bytes,
            finalization_digest,
            origin_envelope_bytes,
            origin_envelope_digest,
            ticket_digest,
            origin_ticket_digest,
            dual_ticket_binding_v2,
            run_binding_digest,
            cleanup_digest,
            prepared_receipt_digest,
            armed_receipt_digest,
            policy_snapshot_digest,
            recovery_bundle_digest,
            binary_result_commit_frame_digest,
            anchor_record_digest,
            reopen_readback_digest,
        })
    }
}

pub(crate) struct ProtectedEvidenceBundleProducer<S> {
    authority_generation_digest: Digest,
    protected_manifest_digest: Digest,
    installed_layout_digest: Digest,
    service_executable_digest: Digest,
    signer: S,
}

impl<S: ProtectedEvidenceBundleSigner> ProtectedEvidenceBundleProducer<S> {
    pub(crate) fn new(
        authority_generation_digest: Digest,
        protected_manifest_digest: Digest,
        installed_layout_digest: Digest,
        service_executable_digest: Digest,
        signer: S,
    ) -> Self {
        Self {
            authority_generation_digest,
            protected_manifest_digest,
            installed_layout_digest,
            service_executable_digest,
            signer,
        }
    }

    pub(crate) fn matches_runtime_identity(
        &self,
        authority_generation_digest: &Digest,
        signer_key_id: &Digest,
        protected_manifest_digest: &Digest,
        installed_layout_digest: &Digest,
        service_executable_digest: &Digest,
    ) -> bool {
        self.authority_generation_digest == *authority_generation_digest
            && self.signer.signer_key_id() == *signer_key_id
            && self.protected_manifest_digest == *protected_manifest_digest
            && self.installed_layout_digest == *installed_layout_digest
            && self.service_executable_digest == *service_executable_digest
    }

    pub(crate) fn produce(
        &mut self,
        source: &PreparedProtectedEvidenceSource,
        result: &ServiceOwnedVerifiedRuntimeResult,
        terminal: &DurableBinaryLedgerTerminal,
    ) -> Result<VerifiedAuthorityResultProjection, ProtectedEvidenceBundleError> {
        if !result.dual_ticket_binding_v2 {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_origin_envelope_v2_required",
            ));
        }
        if !source.matches_runtime_identity(
            &self.authority_generation_digest,
            &self.protected_manifest_digest,
            &self.installed_layout_digest,
            &self.service_executable_digest,
        ) {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_result_binding_source_mismatch",
            ));
        }
        let bindings = source.resolve_for_result(result)?;
        self.verify_terminal_bindings(result, terminal)?;
        let authority = bindings.authority.value();
        let package = bindings.package.value();
        let row = serde_json::json!({
            "schema": AUTHORITY_ROW_SCHEMA,
            "scenarioId": MODEL_SCENARIO_ID,
            "primitiveId": MODEL_PRIMITIVE_ID,
            "fixtureDescriptorDigest": hex_lower(&bindings.model.fixture_descriptor_digest),
            "fixtureDigest": hex_lower(&bindings.model.fixture_digest),
            "fixtureProjectInputDigest": hex_lower(&bindings.model.fixture_project_input_digest),
            "projectBindingDigest": hex_lower(&bindings.model.project_binding_digest),
            "finalization": result.finalization.clone(),
            "finalizationDigest": hex_lower(&result.finalization_digest),
            "originEnvelope": result.origin_envelope.clone(),
            "originEnvelopeDigest": hex_lower(&result.origin_envelope_digest),
        });
        let binary_terminal = terminal.value(&bindings.authority);
        let binary_terminal_digest = digest_value_bytes(&binary_terminal);
        let mut receipt = serde_json::json!({
            "schema": LEDGER_RECEIPT_SCHEMA,
            "ordinal": terminal.receipt_ordinal,
            "previousReceiptDigest": hex_lower(&terminal.previous_receipt_digest),
            "ticketDigest": hex_lower(&result.authority_ticket_digest),
            "originTicketDigest": hex_lower(&result.origin_ticket_digest),
            "runId": result.run_id.clone(),
            "scenarioId": MODEL_SCENARIO_ID,
            "primitiveId": MODEL_PRIMITIVE_ID,
            "state": "completed",
            "resultDigest": hex_lower(&result.finalization_digest),
            "originEnvelopeDigest": hex_lower(&result.origin_envelope_digest),
            "cleanupDigest": hex_lower(&result.cleanup_digest),
            "issuedAt": terminal.issued_at.clone(),
            "consumedAt": terminal.consumed_at.clone(),
            "completedAt": terminal.completed_at.clone(),
            "binaryLedgerTerminal": binary_terminal,
            "binaryLedgerTerminalDigest": hex_lower(&binary_terminal_digest),
        });
        let receipt_digest = digest_value_bytes(&receipt);
        receipt
            .as_object_mut()
            .expect("fixed receipt object")
            .insert(
                "receiptDigest".to_owned(),
                Value::String(hex_lower(&receipt_digest)),
            );
        let ledger = serde_json::json!({
            "schema": LEDGER_SNAPSHOT_SCHEMA,
            "authorityGenerationDigest": hex_lower(&bindings.authority.authority_generation_digest),
            "ledgerIdentityDigest": hex_lower(&bindings.authority.ledger_identity_digest),
            "firstReceiptOrdinal": terminal.receipt_ordinal,
            "lastReceiptOrdinal": terminal.receipt_ordinal,
            "initialReceiptDigest": hex_lower(&terminal.previous_receipt_digest),
            "terminalReceiptDigest": hex_lower(&receipt_digest),
            "receipts": [receipt],
        });
        let ledger_bytes = canonical_ascii_json(&ledger)?;
        if ledger_bytes.len() > MAX_LEDGER_SNAPSHOT_BYTES {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_ledger_snapshot_too_large",
            ));
        }
        let ledger_digest = sha256(&ledger_bytes);
        let authority_digest = digest_value_bytes(&authority);
        let package_digest = digest_value_bytes(&package);
        let mut bundle = serde_json::json!({
            "schema": AUTHORITY_BUNDLE_SCHEMA,
            "bundleId": derive_bundle_id(result, terminal),
            "proofAlgorithm": PROOF_ALGORITHM,
            "policyId": bindings.authority.policy_id.clone(),
            "signerKeyId": hex_lower(&self.signer.signer_key_id()),
            "authorityBinding": authority,
            "authorityBindingDigest": hex_lower(&authority_digest),
            "packageBinding": package,
            "packageBindingDigest": hex_lower(&package_digest),
            "fixtureSetDescriptorDigest": hex_lower(&bindings.model.fixture_set_descriptor_digest),
            "fixtureSetDigest": hex_lower(&bindings.model.fixture_set_digest),
            "ledgerSnapshotDigest": hex_lower(&ledger_digest),
            "rows": [row],
            "signedAt": terminal.completed_at.clone(),
        });
        let unsigned_bytes = canonical_ascii_json(&bundle)?;
        let signature = self
            .signer
            .sign_protected_bundle(ProtectedBundleSigningDigest(sha256(&unsigned_bytes)))?;
        if !signature_is_canonical(&signature) {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_bundle_signature_invalid",
            ));
        }
        bundle
            .as_object_mut()
            .expect("fixed bundle object")
            .insert("signature".to_owned(), Value::String(base64url(&signature)));
        let bundle_bytes = canonical_ascii_json(&bundle)?;
        if bundle_bytes.len() > MAX_AUTHORITY_BUNDLE_BYTES {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_authority_bundle_too_large",
            ));
        }
        let bundle_digest = sha256(&bundle_bytes);
        let projection = serde_json::json!({
            "schema": PROJECTION_SCHEMA,
            "authorityBundle": bundle,
            "authorityBundleDigest": hex_lower(&bundle_digest),
            "ledgerSnapshot": ledger,
            "ledgerSnapshotDigest": hex_lower(&ledger_digest),
        });
        let canonical_bytes = canonical_ascii_json(&projection)?;
        if canonical_bytes.len() > MAX_VERIFIED_AUTHORITY_RESULT_PROJECTION_BYTES {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_projection_too_large",
            ));
        }
        let sha256 = sha256(&canonical_bytes);
        Ok(VerifiedAuthorityResultProjection {
            canonical_bytes,
            sha256,
            authority_generation_digest: bindings.authority.authority_generation_digest,
            ledger_identity_digest: bindings.authority.ledger_identity_digest,
            finalization_bytes: result.finalization_bytes.clone(),
            finalization_digest: result.finalization_digest,
            origin_envelope_bytes: result.origin_envelope_bytes.clone(),
            origin_envelope_digest: result.origin_envelope_digest,
            ticket_digest: result.authority_ticket_digest,
            origin_ticket_digest: result.origin_ticket_digest,
            dual_ticket_binding_v2: true,
            run_binding_digest: result.run_binding_digest,
            cleanup_digest: result.cleanup_digest,
            prepared_receipt_digest: result.prepared_receipt_digest,
            armed_receipt_digest: result.armed_receipt_digest,
            policy_snapshot_digest: result.policy_snapshot_digest,
            recovery_bundle_digest: result.recovery_bundle_digest,
            binary_result_commit_frame_digest: terminal.terminal_frame_digest,
            anchor_record_digest: terminal.anchor_record_digest,
            reopen_readback_digest: digest_value_bytes(
                &terminal.readback.value(&bindings.authority),
            ),
        })
    }

    pub(crate) fn verify_existing_projection(
        &self,
        source: &PreparedProtectedEvidenceSource,
        result: &ServiceOwnedVerifiedRuntimeResult,
        terminal: &DurableBinaryLedgerTerminal,
        projection: &VerifiedAuthorityResultProjection,
    ) -> Result<(), ProtectedEvidenceBundleError> {
        if !result.dual_ticket_binding_v2 {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_origin_envelope_v2_required",
            ));
        }
        if !source.matches_runtime_identity(
            &self.authority_generation_digest,
            &self.protected_manifest_digest,
            &self.installed_layout_digest,
            &self.service_executable_digest,
        ) {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_result_binding_source_mismatch",
            ));
        }
        let bindings = source.resolve_for_result(result)?;
        self.verify_terminal_bindings(result, terminal)?;
        let reparsed = VerifiedAuthorityResultProjection::from_immutable_ledger_readback(
            projection.canonical_bytes.clone(),
            projection.sha256,
        )?;
        if reparsed != *projection
            || projection.authority_generation_digest
                != bindings.authority.authority_generation_digest
            || projection.ledger_identity_digest != bindings.authority.ledger_identity_digest
            || projection.finalization_bytes != result.finalization_bytes
            || projection.finalization_digest != result.finalization_digest
            || projection.origin_envelope_bytes != result.origin_envelope_bytes
            || projection.origin_envelope_digest != result.origin_envelope_digest
            || projection.ticket_digest != result.authority_ticket_digest
            || projection.origin_ticket_digest != result.origin_ticket_digest
            || !projection.dual_ticket_binding_v2
            || projection.run_binding_digest != result.run_binding_digest
            || projection.cleanup_digest != result.cleanup_digest
            || projection.prepared_receipt_digest != result.prepared_receipt_digest
            || projection.armed_receipt_digest != result.armed_receipt_digest
            || projection.policy_snapshot_digest != result.policy_snapshot_digest
            || projection.recovery_bundle_digest != result.recovery_bundle_digest
            || projection.binary_result_commit_frame_digest != terminal.terminal_frame_digest
            || projection.anchor_record_digest != terminal.anchor_record_digest
            || projection.reopen_readback_digest
                != digest_value_bytes(&terminal.readback.value(&bindings.authority))
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_projection_result_binding_mismatch",
            ));
        }
        Ok(())
    }

    fn verify_terminal_bindings(
        &self,
        result: &ServiceOwnedVerifiedRuntimeResult,
        terminal: &DurableBinaryLedgerTerminal,
    ) -> Result<(), ProtectedEvidenceBundleError> {
        if !result.dual_ticket_binding_v2
            || terminal.terminal_ticket_digest != result.authority_ticket_digest
            || terminal.terminal_result_digest != result.finalization_digest
            || terminal.run_binding_digest != result.run_binding_digest
            || terminal.prepared_receipt_digest != result.prepared_receipt_digest
            || terminal.armed_receipt_digest != result.armed_receipt_digest
            || terminal.policy_snapshot_digest != result.policy_snapshot_digest
            || terminal.recovery_bundle_digest != result.recovery_bundle_digest
            || terminal.origin_envelope_digest != result.origin_envelope_digest
            || terminal.cleanup_digest != result.cleanup_digest
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_terminal_result_binding_mismatch",
            ));
        }
        if sha256(&result.finalization_bytes) != result.finalization_digest
            || canonical_ascii_json(&result.finalization)? != result.finalization_bytes
            || sha256(&result.origin_envelope_bytes) != result.origin_envelope_digest
            || canonical_ascii_json(&result.origin_envelope)? != result.origin_envelope_bytes
        {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_runtime_result_changed",
            ));
        }
        Ok(())
    }
}

fn derive_bundle_id(
    result: &ServiceOwnedVerifiedRuntimeResult,
    terminal: &DurableBinaryLedgerTerminal,
) -> String {
    let mut digest = Sha256::new();
    digest.update(BUNDLE_ID_DOMAIN);
    digest.update(result.finalization_digest);
    digest.update(result.origin_envelope_digest);
    digest.update(terminal.terminal_frame_digest);
    digest.update(terminal.anchor_record_digest);
    let digest: Digest = digest.finalize().into();
    format!("authority-{}", hex_lower(&digest))
}

fn run_admission_digest(
    run_binding: &Digest,
    prepared: &Digest,
    armed: &Digest,
    policy: &Digest,
    recovery: &Digest,
) -> Digest {
    let mut digest = Sha256::new();
    digest.update(RUN_ADMISSION_DOMAIN);
    digest.update(run_binding);
    digest.update(prepared);
    digest.update(armed);
    digest.update(policy);
    digest.update(recovery);
    digest.finalize().into()
}

fn parse_canonical_ascii_object(
    raw: &[u8],
    maximum_size: usize,
    code: &'static str,
) -> Result<Value, ProtectedEvidenceBundleError> {
    if raw.is_empty() || raw.len() > maximum_size {
        return Err(ProtectedEvidenceBundleError::new(code));
    }
    let strict = serde_json::from_slice::<StrictJsonValue>(raw)
        .map_err(|_| ProtectedEvidenceBundleError::new(code))?
        .0;
    if !strict.is_object() || canonical_ascii_json(&strict)? != raw {
        return Err(ProtectedEvidenceBundleError::new(code));
    }
    Ok(strict)
}

fn validate_origin_ticket_binding(
    origin_envelope: &Value,
    authority_ticket_digest: Digest,
) -> Result<(String, Digest, bool), ProtectedEvidenceBundleError> {
    let ticket = origin_envelope
        .get("ticket")
        .filter(|value| value.is_object())
        .ok_or_else(|| ProtectedEvidenceBundleError::new("protected_origin_envelope_invalid"))?;
    let run_id = ticket
        .get("runId")
        .and_then(Value::as_str)
        .ok_or_else(|| ProtectedEvidenceBundleError::new("protected_origin_envelope_invalid"))?
        .to_owned();
    require_safe_id(&run_id, "protected_origin_envelope_invalid")?;
    let origin_ticket_digest = digest_value(origin_envelope, "ticketDigest")?;
    let dual_ticket_binding_v2 = match origin_envelope.get("schema").and_then(Value::as_str) {
        Some(ORIGIN_ENVELOPE_SCHEMA_V2) => true,
        Some(ORIGIN_ENVELOPE_SCHEMA_V1) => false,
        _ => {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_origin_envelope_schema_invalid",
            ))
        }
    };
    if dual_ticket_binding_v2 {
        let computed_origin_ticket_digest = sha256(&canonical_ascii_json(ticket)?);
        if origin_ticket_digest != computed_origin_ticket_digest {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_origin_ticket_digest_mismatch",
            ));
        }
        if digest_value(origin_envelope, "authorityTicketDigest")? != authority_ticket_digest {
            return Err(ProtectedEvidenceBundleError::new(
                "protected_authority_ticket_digest_mismatch",
            ));
        }
    } else if origin_ticket_digest != authority_ticket_digest {
        // Legacy v1 envelopes carried only one ticket digest. Preserve that exact
        // equality rule for compatibility, but never promote such a result through
        // the protected producer.
        return Err(ProtectedEvidenceBundleError::new(
            "protected_runtime_origin_binding_mismatch",
        ));
    }
    Ok((run_id, origin_ticket_digest, dual_ticket_binding_v2))
}

fn canonical_ascii_json(value: &Value) -> Result<Vec<u8>, ProtectedEvidenceBundleError> {
    validate_public_ascii(value, 0)?;
    serde_json::to_vec(value)
        .map_err(|_| ProtectedEvidenceBundleError::new("protected_json_invalid"))
}

fn validate_public_ascii(value: &Value, depth: usize) -> Result<(), ProtectedEvidenceBundleError> {
    if depth > MAX_JSON_DEPTH {
        return Err(ProtectedEvidenceBundleError::new(
            "protected_json_nesting_invalid",
        ));
    }
    match value {
        Value::Null | Value::Bool(_) => Ok(()),
        Value::Number(number) if number.is_i64() || number.is_u64() => Ok(()),
        Value::Number(_) => Err(ProtectedEvidenceBundleError::new(
            "protected_json_number_invalid",
        )),
        Value::String(text) => validate_public_ascii_string(text, false),
        Value::Array(values) => {
            for item in values {
                validate_public_ascii(item, depth + 1)?;
            }
            Ok(())
        }
        Value::Object(values) => {
            for (key, item) in values {
                validate_public_ascii_string(key, true)?;
                validate_public_ascii(item, depth + 1)?;
            }
            Ok(())
        }
    }
}

fn validate_public_ascii_string(
    value: &str,
    is_key: bool,
) -> Result<(), ProtectedEvidenceBundleError> {
    if value.is_empty()
        || !value.bytes().all(|byte| (0x20..=0x7e).contains(&byte))
        || contains_private_material(value, is_key)
    {
        return Err(ProtectedEvidenceBundleError::new(
            "protected_private_or_non_ascii_value",
        ));
    }
    Ok(())
}

fn contains_private_material(value: &str, is_key: bool) -> bool {
    let lower = value.to_ascii_lowercase();
    if lower.contains("-----begin") && lower.contains("private key-----") {
        return true;
    }
    if lower.contains("sk-") && lower.len() > 8 {
        return true;
    }
    if !is_key {
        return false;
    }
    let compact: String = lower
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .collect();
    [
        "privatekey",
        "secret",
        "password",
        "authorization",
        "cookie",
        "accesstoken",
        "refreshtoken",
        "apikey",
        "credential",
        "sessiontoken",
    ]
    .iter()
    .any(|blocked| compact.contains(blocked))
}

fn digest_value(value: &Value, field: &str) -> Result<Digest, ProtectedEvidenceBundleError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .and_then(decode_digest)
        .ok_or_else(|| ProtectedEvidenceBundleError::new("protected_origin_envelope_invalid"))
}

fn require_exact_keys(
    value: &Value,
    expected: &[&str],
    code: &'static str,
) -> Result<(), ProtectedEvidenceBundleError> {
    let object = value
        .as_object()
        .ok_or_else(|| ProtectedEvidenceBundleError::new(code))?;
    if object.len() != expected.len() || expected.iter().any(|field| !object.contains_key(*field)) {
        return Err(ProtectedEvidenceBundleError::new(code));
    }
    Ok(())
}

fn digest_value_bytes(value: &Value) -> Digest {
    let bytes = canonical_ascii_json(value).expect("internally constructed public JSON");
    sha256(&bytes)
}

fn sha256(value: &[u8]) -> Digest {
    Sha256::digest(value).into()
}

fn require_nonzero_digests(
    values: &[Digest],
    code: &'static str,
) -> Result<(), ProtectedEvidenceBundleError> {
    if values
        .iter()
        .any(|value| value.iter().all(|byte| *byte == 0))
    {
        Err(ProtectedEvidenceBundleError::new(code))
    } else {
        Ok(())
    }
}

fn require_safe_id(value: &str, code: &'static str) -> Result<(), ProtectedEvidenceBundleError> {
    let mut characters = value.chars();
    if value.len() > 128
        || !characters
            .next()
            .is_some_and(|character| character.is_ascii_alphanumeric())
        || !characters.all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.')
        })
    {
        return Err(ProtectedEvidenceBundleError::new(code));
    }
    Ok(())
}

fn require_version(value: &str) -> Result<(), ProtectedEvidenceBundleError> {
    let mut parts = value.split('-');
    let core = parts.next().unwrap_or_default();
    if value.len() > 64
        || !value.is_ascii()
        || parts.clone().count() > 1
        || core.split('.').count() != 3
        || !core
            .split('.')
            .all(|part| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_digit()))
        || !parts.next().is_none_or(|suffix| {
            !suffix.is_empty()
                && suffix
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-'))
        })
    {
        return Err(ProtectedEvidenceBundleError::new(
            "protected_package_binding_invalid",
        ));
    }
    Ok(())
}

fn require_timestamp(value: &str) -> Result<(), ProtectedEvidenceBundleError> {
    let bytes = value.as_bytes();
    let punctuation = [
        (4, b'-'),
        (7, b'-'),
        (10, b'T'),
        (13, b':'),
        (16, b':'),
        (19, b'.'),
    ];
    if bytes.len() != 27
        || bytes[26] != b'Z'
        || punctuation
            .iter()
            .any(|(index, expected)| bytes[*index] != *expected)
        || bytes
            .iter()
            .enumerate()
            .filter(|(index, _)| !matches!(*index, 4 | 7 | 10 | 13 | 16 | 19 | 26))
            .any(|(_, byte)| !byte.is_ascii_digit())
    {
        return Err(ProtectedEvidenceBundleError::new(
            "protected_timestamp_invalid",
        ));
    }
    let component = |start: usize, end: usize| -> u32 {
        bytes[start..end]
            .iter()
            .fold(0, |value, byte| value * 10 + u32::from(*byte - b'0'))
    };
    let year = component(0, 4);
    let month = component(5, 7);
    let day = component(8, 10);
    let hour = component(11, 13);
    let minute = component(14, 16);
    let second = component(17, 19);
    let leap_year = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let maximum_day = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if leap_year => 29,
        2 => 28,
        _ => 0,
    };
    if year == 0 || day == 0 || day > maximum_day || hour > 23 || minute > 59 || second > 59 {
        return Err(ProtectedEvidenceBundleError::new(
            "protected_timestamp_invalid",
        ));
    }
    Ok(())
}

fn signature_is_canonical(signature: &[u8; 64]) -> bool {
    let r: Digest = signature[..32].try_into().expect("fixed signature");
    let s: Digest = signature[32..].try_into().expect("fixed signature");
    r.iter().any(|byte| *byte != 0)
        && r < P256_ORDER
        && s.iter().any(|byte| *byte != 0)
        && s <= P256_HALF_ORDER
}

fn base64url(value: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut output = String::with_capacity((value.len() * 4 + 2) / 3);
    for chunk in value.chunks(3) {
        let first = chunk[0];
        let second = chunk.get(1).copied().unwrap_or(0);
        let third = chunk.get(2).copied().unwrap_or(0);
        output.push(ALPHABET[(first >> 2) as usize] as char);
        output.push(ALPHABET[(((first & 0x03) << 4) | (second >> 4)) as usize] as char);
        if chunk.len() > 1 {
            output.push(ALPHABET[(((second & 0x0f) << 2) | (third >> 6)) as usize] as char);
        }
        if chunk.len() > 2 {
            output.push(ALPHABET[(third & 0x3f) as usize] as char);
        }
    }
    output
}

fn base64url_decode_64(value: &str) -> Option<[u8; 64]> {
    if value.len() != 86
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return None;
    }
    let mut output = [0u8; 64];
    let mut written = 0usize;
    for chunk in value.as_bytes().chunks(4) {
        let first = base64url_nibble(chunk[0])?;
        let second = base64url_nibble(chunk[1])?;
        output[written] = (first << 2) | (second >> 4);
        written += 1;
        if chunk.len() > 2 {
            let third = base64url_nibble(chunk[2])?;
            output[written] = (second << 4) | (third >> 2);
            written += 1;
            if chunk.len() > 3 {
                let fourth = base64url_nibble(chunk[3])?;
                output[written] = (third << 6) | fourth;
                written += 1;
            }
        }
    }
    (written == output.len() && base64url(&output) == value).then_some(output)
}

fn base64url_nibble(value: u8) -> Option<u8> {
    match value {
        b'A'..=b'Z' => Some(value - b'A'),
        b'a'..=b'z' => Some(value - b'a' + 26),
        b'0'..=b'9' => Some(value - b'0' + 52),
        b'-' => Some(62),
        b'_' => Some(63),
        _ => None,
    }
}

fn hex_lower(value: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn decode_digest(value: &str) -> Option<Digest> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
    {
        return None;
    }
    let mut output = [0u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (hex_nibble(pair[0]) << 4) | hex_nibble(pair[1]);
    }
    output.iter().any(|byte| *byte != 0).then_some(output)
}

fn hex_nibble(value: u8) -> u8 {
    match value {
        b'0'..=b'9' => value - b'0',
        b'a'..=b'f' => value - b'a' + 10,
        _ => 0,
    }
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
        formatter.write_str("canonical JSON without duplicate keys or floats")
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

#[cfg(test)]
#[path = "primitive_basis_protected_evidence_bundle/tests.rs"]
mod tests;
