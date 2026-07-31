//! Canonical machine runner-policy state.
//!
//! This is an action-time state object, not package content. Parsing proves
//! only canonical shape. It becomes authority only after the finalizer creates
//! it from fresh machine readback and the exact file is included in the
//! generation seal and authenticated FinalCommit boundary.

#![cfg(windows)]
#![cfg_attr(not(test), allow(dead_code))]

use serde::{Deserialize, Serialize};
use sha2::{Digest as Sha2Digest, Sha256};
use std::{
    fmt,
    path::{Component, Path, PathBuf, Prefix},
};

use super::{RUNNER_ACCOUNT_NAME, RUNNER_POLICY_STATE_SCHEMA};
use crate::primitive_evidence_authority_windows::AUTHORITY_RUNNER_POLICY_STATE_FILE_NAME;

pub(super) const RUNNER_POLICY_STATE_FILE_NAME: &str = AUTHORITY_RUNNER_POLICY_STATE_FILE_NAME;
const RUNNER_POLICY_MACHINE_READBACK_BLOCKER: &str =
    "authority_runner_policy_machine_readback_not_connected";
const RUNNER_ACCOUNT_BINDING_DOMAIN: &[u8] = b"vrcforge-runner-account-binding-v2\0";
const RUNNER_PROFILE_BINDING_DOMAIN: &[u8] = b"vrcforge-runner-profile-binding-v2\0";
const RUNNER_PROVISIONING_BINDING_DOMAIN: &[u8] = b"vrcforge-runner-provisioning-binding-v2\0";
const RUNNER_TOKEN_POLICY_DOMAIN: &[u8] = b"vrcforge-runner-token-policy-v1\0";
const RUNNER_POLICY_STATE_BINDING_DOMAIN: &[u8] = b"vrcforge-runner-policy-state-binding-v2\0";
const MAX_RUNNER_POLICY_STATE_BYTES: usize = 64 * 1024;

type PolicyDigest = [u8; 32];

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
enum RunnerProvisioningProvenanceWire {
    CreatedByTransaction {
        transaction_sha256: String,
    },
    AdoptedFromAuthenticatedPrior {
        transaction_sha256: String,
        prior_generation_sha256: String,
        prior_final_commit_receipt_sha256: String,
        prior_runner_policy_binding_sha256: String,
        profile: RunnerAdoptedProfileProvenanceWire,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
enum RunnerAdoptedProfileProvenanceWire {
    ReusedFromAuthenticatedPrior,
    RecreatedByTransaction {
        durable_recreation_receipt_sha256: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum VerifiedRunnerProvisioningProvenance {
    CreatedByTransaction {
        transaction_sha256: PolicyDigest,
    },
    AdoptedFromAuthenticatedPrior {
        transaction_sha256: PolicyDigest,
        prior_generation_sha256: PolicyDigest,
        prior_final_commit_receipt_sha256: PolicyDigest,
        prior_runner_policy_binding_sha256: PolicyDigest,
        profile: VerifiedRunnerAdoptedProfileProvenance,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum VerifiedRunnerAdoptedProfileProvenance {
    ReusedFromAuthenticatedPrior,
    RecreatedByTransaction {
        durable_recreation_receipt_sha256: PolicyDigest,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct RunnerPolicyStateError(&'static str);

impl RunnerPolicyStateError {
    pub(super) const fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for RunnerPolicyStateError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for RunnerPolicyStateError {}

/// Fresh machine facts collected by the future privileged finalizer. There is
/// deliberately no production constructor before account/profile provisioning
/// and exact readback are implemented.
pub(super) struct VerifiedRunnerMachinePolicyReadback {
    account_sid: String,
    profile_root: PathBuf,
    profile_identity_sha256: PolicyDigest,
    profile_security_sha256: PolicyDigest,
    provisioning: VerifiedRunnerProvisioningProvenance,
    account_name_sid_round_trip_verified: bool,
    batch_logon_granted: bool,
    interactive_logon_denied: bool,
    network_logon_denied: bool,
    service_logon_denied: bool,
    administrator_member: bool,
    service_identity_member: bool,
    profile_local_volume: bool,
    profile_reparse_free_held_chain: bool,
    profile_exact_owner_and_acl: bool,
}

impl fmt::Debug for VerifiedRunnerMachinePolicyReadback {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("VerifiedRunnerMachinePolicyReadback(<held-and-redacted>)")
    }
}

impl VerifiedRunnerMachinePolicyReadback {
    pub(super) fn from_production_machine_readback() -> Result<Self, RunnerPolicyStateError> {
        Err(RunnerPolicyStateError(
            RUNNER_POLICY_MACHINE_READBACK_BLOCKER,
        ))
    }

    #[cfg(test)]
    fn for_test(account_sid: &str, profile_root: &str) -> Self {
        Self {
            account_sid: account_sid.to_owned(),
            profile_root: PathBuf::from(profile_root),
            profile_identity_sha256: [0x41; 32],
            profile_security_sha256: [0x42; 32],
            provisioning: VerifiedRunnerProvisioningProvenance::CreatedByTransaction {
                transaction_sha256: [0x11; 32],
            },
            account_name_sid_round_trip_verified: true,
            batch_logon_granted: true,
            interactive_logon_denied: true,
            network_logon_denied: true,
            service_logon_denied: true,
            administrator_member: false,
            service_identity_member: false,
            profile_local_volume: true,
            profile_reparse_free_held_chain: true,
            profile_exact_owner_and_acl: true,
        }
    }

    #[cfg(test)]
    fn for_revalidated_test(
        account_sid: &str,
        profile_root: &str,
        profile: VerifiedRunnerAdoptedProfileProvenance,
    ) -> Self {
        let mut value = Self::for_test(account_sid, profile_root);
        value.provisioning = VerifiedRunnerProvisioningProvenance::AdoptedFromAuthenticatedPrior {
            transaction_sha256: [0x11; 32],
            prior_generation_sha256: [0x21; 32],
            prior_final_commit_receipt_sha256: [0x23; 32],
            prior_runner_policy_binding_sha256: [0x22; 32],
            profile,
        };
        value
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RunnerPolicyStateWire {
    schema: String,
    generation_sha256: String,
    account_name: String,
    account_sid: String,
    profile_root: String,
    provisioning: RunnerProvisioningProvenanceWire,
    account_name_sid_round_trip_verified: bool,
    batch_logon_granted: bool,
    interactive_logon_denied: bool,
    network_logon_denied: bool,
    service_logon_denied: bool,
    administrator_member: bool,
    service_identity_member: bool,
    profile_local_volume: bool,
    profile_reparse_free_held_chain: bool,
    profile_exact_owner_and_acl: bool,
    profile_identity_sha256: String,
    profile_security_sha256: String,
    token_policy_sha256: String,
    provisioning_binding_sha256: String,
    account_binding_sha256: String,
    profile_binding_sha256: String,
    binding_sha256: String,
}

/// Canonical shape-only state. This type is intentionally Clone because it is
/// not an authenticated capability; the FinalCommit boundary must wrap a held
/// sealed file before any launcher can consume its contents.
#[derive(Clone, PartialEq, Eq)]
pub(super) struct CanonicalRunnerPolicyState {
    wire: RunnerPolicyStateWire,
    generation_sha256: PolicyDigest,
    transaction_sha256: PolicyDigest,
    profile_identity_sha256: PolicyDigest,
    profile_security_sha256: PolicyDigest,
    token_policy_sha256: PolicyDigest,
    provisioning_binding_sha256: PolicyDigest,
    account_binding_sha256: PolicyDigest,
    profile_binding_sha256: PolicyDigest,
    binding_sha256: PolicyDigest,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct RunnerPolicyStateDescriptor {
    generation_sha256: PolicyDigest,
    transaction_sha256: PolicyDigest,
    byte_length: u64,
    bytes_sha256: PolicyDigest,
    binding_sha256: PolicyDigest,
}

impl RunnerPolicyStateDescriptor {
    pub(super) const fn generation_sha256(&self) -> PolicyDigest {
        self.generation_sha256
    }

    pub(super) const fn transaction_sha256(&self) -> PolicyDigest {
        self.transaction_sha256
    }

    pub(super) const fn byte_length(&self) -> u64 {
        self.byte_length
    }

    pub(super) const fn bytes_sha256(&self) -> PolicyDigest {
        self.bytes_sha256
    }

    pub(super) const fn binding_sha256(&self) -> PolicyDigest {
        self.binding_sha256
    }

    #[cfg(test)]
    pub(super) fn exact_test_fixture(
        generation_sha256: PolicyDigest,
        transaction_sha256: PolicyDigest,
        byte_length: u64,
        bytes_sha256: PolicyDigest,
        binding_sha256: PolicyDigest,
    ) -> Self {
        assert!(!is_zero(&generation_sha256));
        assert!(!is_zero(&transaction_sha256));
        assert!(byte_length > 0);
        assert!(!is_zero(&bytes_sha256));
        assert!(!is_zero(&binding_sha256));
        Self {
            generation_sha256,
            transaction_sha256,
            byte_length,
            bytes_sha256,
            binding_sha256,
        }
    }
}

impl fmt::Debug for CanonicalRunnerPolicyState {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("CanonicalRunnerPolicyState(<redacted>)")
    }
}

impl CanonicalRunnerPolicyState {
    #[cfg(test)]
    pub(super) fn canonical_test_fixture(
        generation_sha256: PolicyDigest,
        transaction_sha256: PolicyDigest,
    ) -> Self {
        let mut readback = VerifiedRunnerMachinePolicyReadback::for_test(
            "S-1-5-21-111-222-333-1001",
            r"C:\Users\VRCForgeRunner",
        );
        readback.provisioning =
            VerifiedRunnerProvisioningProvenance::CreatedByTransaction { transaction_sha256 };
        Self::from_verified_machine_readback(generation_sha256, transaction_sha256, readback)
            .expect("canonical runner-policy test fixture must be valid")
    }

    pub(super) fn from_verified_machine_readback(
        generation_sha256: PolicyDigest,
        transaction_sha256: PolicyDigest,
        readback: VerifiedRunnerMachinePolicyReadback,
    ) -> Result<Self, RunnerPolicyStateError> {
        if is_zero(&generation_sha256) {
            return Err(RunnerPolicyStateError(
                "authority_runner_policy_generation_invalid",
            ));
        }
        if is_zero(&transaction_sha256) {
            return Err(RunnerPolicyStateError(
                "authority_runner_policy_transaction_invalid",
            ));
        }
        validate_machine_readback(&readback)?;
        let profile_root = canonical_profile_root(&readback.profile_root)?;
        validate_provisioning(
            &generation_sha256,
            &transaction_sha256,
            &readback.provisioning,
        )?;
        let provisioning_binding_sha256 =
            runner_provisioning_binding_digest(&generation_sha256, &readback.provisioning);
        let provisioning = provisioning_to_wire(&readback.provisioning);
        let token_policy_sha256 = runner_token_policy_digest();
        let account_binding_sha256 = runner_account_binding_digest(
            &generation_sha256,
            RUNNER_ACCOUNT_NAME,
            &readback.account_sid,
            &provisioning_binding_sha256,
        );
        let profile_binding_sha256 = runner_profile_binding_digest(
            &generation_sha256,
            &account_binding_sha256,
            &profile_root,
            &readback.profile_identity_sha256,
            &readback.profile_security_sha256,
            &provisioning_binding_sha256,
        );
        let binding_sha256 = runner_policy_state_binding_digest(
            &generation_sha256,
            &provisioning_binding_sha256,
            &account_binding_sha256,
            &profile_binding_sha256,
            &token_policy_sha256,
        );
        let wire = RunnerPolicyStateWire {
            schema: RUNNER_POLICY_STATE_SCHEMA.to_owned(),
            generation_sha256: hex_lower(&generation_sha256),
            account_name: RUNNER_ACCOUNT_NAME.to_owned(),
            account_sid: readback.account_sid,
            profile_root,
            provisioning,
            account_name_sid_round_trip_verified: readback.account_name_sid_round_trip_verified,
            batch_logon_granted: readback.batch_logon_granted,
            interactive_logon_denied: readback.interactive_logon_denied,
            network_logon_denied: readback.network_logon_denied,
            service_logon_denied: readback.service_logon_denied,
            administrator_member: readback.administrator_member,
            service_identity_member: readback.service_identity_member,
            profile_local_volume: readback.profile_local_volume,
            profile_reparse_free_held_chain: readback.profile_reparse_free_held_chain,
            profile_exact_owner_and_acl: readback.profile_exact_owner_and_acl,
            profile_identity_sha256: hex_lower(&readback.profile_identity_sha256),
            profile_security_sha256: hex_lower(&readback.profile_security_sha256),
            token_policy_sha256: hex_lower(&token_policy_sha256),
            provisioning_binding_sha256: hex_lower(&provisioning_binding_sha256),
            account_binding_sha256: hex_lower(&account_binding_sha256),
            profile_binding_sha256: hex_lower(&profile_binding_sha256),
            binding_sha256: hex_lower(&binding_sha256),
        };
        let value = Self {
            wire,
            generation_sha256,
            transaction_sha256,
            profile_identity_sha256: readback.profile_identity_sha256,
            profile_security_sha256: readback.profile_security_sha256,
            token_policy_sha256,
            provisioning_binding_sha256,
            account_binding_sha256,
            profile_binding_sha256,
            binding_sha256,
        };
        value.validate()?;
        Ok(value)
    }

    pub(super) fn parse_canonical(bytes: &[u8]) -> Result<Self, RunnerPolicyStateError> {
        if bytes.is_empty() || bytes.len() > MAX_RUNNER_POLICY_STATE_BYTES {
            return Err(RunnerPolicyStateError(
                "authority_runner_policy_state_size_invalid",
            ));
        }
        let wire: RunnerPolicyStateWire = serde_json::from_slice(bytes)
            .map_err(|_| RunnerPolicyStateError("authority_runner_policy_state_json_invalid"))?;
        if serde_json::to_vec(&wire).map_err(|_| {
            RunnerPolicyStateError("authority_runner_policy_state_serialization_failed")
        })? != bytes
        {
            return Err(RunnerPolicyStateError(
                "authority_runner_policy_state_noncanonical",
            ));
        }
        let provisioning = provisioning_from_wire(&wire.provisioning)?;
        let value = Self {
            generation_sha256: parse_digest(&wire.generation_sha256)?,
            transaction_sha256: provisioning_transaction_sha256(&provisioning),
            profile_identity_sha256: parse_digest(&wire.profile_identity_sha256)?,
            profile_security_sha256: parse_digest(&wire.profile_security_sha256)?,
            token_policy_sha256: parse_digest(&wire.token_policy_sha256)?,
            provisioning_binding_sha256: parse_digest(&wire.provisioning_binding_sha256)?,
            account_binding_sha256: parse_digest(&wire.account_binding_sha256)?,
            profile_binding_sha256: parse_digest(&wire.profile_binding_sha256)?,
            binding_sha256: parse_digest(&wire.binding_sha256)?,
            wire,
        };
        value.validate()?;
        Ok(value)
    }

    pub(super) fn canonical_bytes(&self) -> Result<Vec<u8>, RunnerPolicyStateError> {
        self.validate()?;
        serde_json::to_vec(&self.wire).map_err(|_| {
            RunnerPolicyStateError("authority_runner_policy_state_serialization_failed")
        })
    }

    pub(super) fn descriptor(&self) -> Result<RunnerPolicyStateDescriptor, RunnerPolicyStateError> {
        let bytes = self.canonical_bytes()?;
        let byte_length = u64::try_from(bytes.len())
            .map_err(|_| RunnerPolicyStateError("authority_runner_policy_state_size_invalid"))?;
        let bytes_sha256 = Sha256::digest(&bytes).into();
        Ok(RunnerPolicyStateDescriptor {
            generation_sha256: self.generation_sha256,
            transaction_sha256: self.transaction_sha256,
            byte_length,
            bytes_sha256,
            binding_sha256: self.binding_sha256,
        })
    }

    pub(super) const fn generation_sha256(&self) -> &PolicyDigest {
        &self.generation_sha256
    }

    pub(super) const fn transaction_sha256(&self) -> &PolicyDigest {
        &self.transaction_sha256
    }

    pub(super) const fn binding_sha256(&self) -> &PolicyDigest {
        &self.binding_sha256
    }

    pub(super) const fn profile_identity_sha256(&self) -> &PolicyDigest {
        &self.profile_identity_sha256
    }

    pub(super) const fn profile_security_sha256(&self) -> &PolicyDigest {
        &self.profile_security_sha256
    }

    pub(super) const fn account_binding_sha256(&self) -> &PolicyDigest {
        &self.account_binding_sha256
    }

    pub(super) const fn profile_binding_sha256(&self) -> &PolicyDigest {
        &self.profile_binding_sha256
    }

    pub(super) fn canonical_account_sid(&self) -> &str {
        &self.wire.account_sid
    }

    pub(super) fn profile_root(&self) -> &Path {
        Path::new(&self.wire.profile_root)
    }

    fn validate(&self) -> Result<(), RunnerPolicyStateError> {
        validate_account_sid(&self.wire.account_sid)?;
        let profile_root = canonical_profile_root(Path::new(&self.wire.profile_root))?;
        let provisioning = provisioning_from_wire(&self.wire.provisioning)?;
        validate_provisioning(
            &self.generation_sha256,
            &self.transaction_sha256,
            &provisioning,
        )?;
        if self.wire.schema != RUNNER_POLICY_STATE_SCHEMA
            || self.wire.account_name != RUNNER_ACCOUNT_NAME
            || self.wire.profile_root != profile_root
            || !self.wire.account_name_sid_round_trip_verified
            || !self.wire.batch_logon_granted
            || !self.wire.interactive_logon_denied
            || !self.wire.network_logon_denied
            || !self.wire.service_logon_denied
            || self.wire.administrator_member
            || self.wire.service_identity_member
            || !self.wire.profile_local_volume
            || !self.wire.profile_reparse_free_held_chain
            || !self.wire.profile_exact_owner_and_acl
            || is_zero(&self.generation_sha256)
            || is_zero(&self.transaction_sha256)
            || is_zero(&self.profile_identity_sha256)
            || is_zero(&self.profile_security_sha256)
            || self.token_policy_sha256 != runner_token_policy_digest()
            || self.provisioning_binding_sha256
                != runner_provisioning_binding_digest(&self.generation_sha256, &provisioning)
            || self.account_binding_sha256
                != runner_account_binding_digest(
                    &self.generation_sha256,
                    &self.wire.account_name,
                    &self.wire.account_sid,
                    &self.provisioning_binding_sha256,
                )
            || self.profile_binding_sha256
                != runner_profile_binding_digest(
                    &self.generation_sha256,
                    &self.account_binding_sha256,
                    &profile_root,
                    &self.profile_identity_sha256,
                    &self.profile_security_sha256,
                    &self.provisioning_binding_sha256,
                )
            || self.binding_sha256
                != runner_policy_state_binding_digest(
                    &self.generation_sha256,
                    &self.provisioning_binding_sha256,
                    &self.account_binding_sha256,
                    &self.profile_binding_sha256,
                    &self.token_policy_sha256,
                )
            || self.wire.generation_sha256 != hex_lower(&self.generation_sha256)
            || self.wire.profile_identity_sha256 != hex_lower(&self.profile_identity_sha256)
            || self.wire.profile_security_sha256 != hex_lower(&self.profile_security_sha256)
            || self.wire.token_policy_sha256 != hex_lower(&self.token_policy_sha256)
            || self.wire.provisioning_binding_sha256 != hex_lower(&self.provisioning_binding_sha256)
            || self.wire.account_binding_sha256 != hex_lower(&self.account_binding_sha256)
            || self.wire.profile_binding_sha256 != hex_lower(&self.profile_binding_sha256)
            || self.wire.binding_sha256 != hex_lower(&self.binding_sha256)
        {
            return Err(RunnerPolicyStateError(
                "authority_runner_policy_state_binding_invalid",
            ));
        }
        Ok(())
    }
}

fn validate_machine_readback(
    readback: &VerifiedRunnerMachinePolicyReadback,
) -> Result<(), RunnerPolicyStateError> {
    validate_account_sid(&readback.account_sid)?;
    canonical_profile_root(&readback.profile_root)?;
    if is_zero(&readback.profile_identity_sha256)
        || is_zero(&readback.profile_security_sha256)
        || !readback.account_name_sid_round_trip_verified
        || !readback.batch_logon_granted
        || !readback.interactive_logon_denied
        || !readback.network_logon_denied
        || !readback.service_logon_denied
        || readback.administrator_member
        || readback.service_identity_member
        || !readback.profile_local_volume
        || !readback.profile_reparse_free_held_chain
        || !readback.profile_exact_owner_and_acl
    {
        return Err(RunnerPolicyStateError(
            "authority_runner_policy_machine_readback_invalid",
        ));
    }
    Ok(())
}

fn validate_provisioning(
    generation_sha256: &PolicyDigest,
    expected_transaction_sha256: &PolicyDigest,
    provenance: &VerifiedRunnerProvisioningProvenance,
) -> Result<(), RunnerPolicyStateError> {
    let valid = match provenance {
        VerifiedRunnerProvisioningProvenance::CreatedByTransaction { transaction_sha256 } => {
            !is_zero(transaction_sha256) && transaction_sha256 == expected_transaction_sha256
        }
        VerifiedRunnerProvisioningProvenance::AdoptedFromAuthenticatedPrior {
            transaction_sha256,
            prior_generation_sha256,
            prior_final_commit_receipt_sha256,
            prior_runner_policy_binding_sha256,
            profile,
        } => {
            !is_zero(transaction_sha256)
                && transaction_sha256 == expected_transaction_sha256
                && !is_zero(prior_generation_sha256)
                && !is_zero(prior_final_commit_receipt_sha256)
                && !is_zero(prior_runner_policy_binding_sha256)
                && prior_generation_sha256 != generation_sha256
                && match profile {
                    VerifiedRunnerAdoptedProfileProvenance::ReusedFromAuthenticatedPrior => true,
                    VerifiedRunnerAdoptedProfileProvenance::RecreatedByTransaction {
                        durable_recreation_receipt_sha256,
                    } => !is_zero(durable_recreation_receipt_sha256),
                }
        }
    };
    if !valid {
        return Err(RunnerPolicyStateError(
            "authority_runner_policy_provisioning_provenance_invalid",
        ));
    }
    Ok(())
}

fn provisioning_to_wire(
    provenance: &VerifiedRunnerProvisioningProvenance,
) -> RunnerProvisioningProvenanceWire {
    match provenance {
        VerifiedRunnerProvisioningProvenance::CreatedByTransaction { transaction_sha256 } => {
            RunnerProvisioningProvenanceWire::CreatedByTransaction {
                transaction_sha256: hex_lower(transaction_sha256),
            }
        }
        VerifiedRunnerProvisioningProvenance::AdoptedFromAuthenticatedPrior {
            transaction_sha256,
            prior_generation_sha256,
            prior_final_commit_receipt_sha256,
            prior_runner_policy_binding_sha256,
            profile,
        } => RunnerProvisioningProvenanceWire::AdoptedFromAuthenticatedPrior {
            transaction_sha256: hex_lower(transaction_sha256),
            prior_generation_sha256: hex_lower(prior_generation_sha256),
            prior_final_commit_receipt_sha256: hex_lower(prior_final_commit_receipt_sha256),
            prior_runner_policy_binding_sha256: hex_lower(prior_runner_policy_binding_sha256),
            profile: adopted_profile_to_wire(profile),
        },
    }
}

fn adopted_profile_to_wire(
    profile: &VerifiedRunnerAdoptedProfileProvenance,
) -> RunnerAdoptedProfileProvenanceWire {
    match profile {
        VerifiedRunnerAdoptedProfileProvenance::ReusedFromAuthenticatedPrior => {
            RunnerAdoptedProfileProvenanceWire::ReusedFromAuthenticatedPrior
        }
        VerifiedRunnerAdoptedProfileProvenance::RecreatedByTransaction {
            durable_recreation_receipt_sha256,
        } => RunnerAdoptedProfileProvenanceWire::RecreatedByTransaction {
            durable_recreation_receipt_sha256: hex_lower(durable_recreation_receipt_sha256),
        },
    }
}

fn provisioning_from_wire(
    provenance: &RunnerProvisioningProvenanceWire,
) -> Result<VerifiedRunnerProvisioningProvenance, RunnerPolicyStateError> {
    match provenance {
        RunnerProvisioningProvenanceWire::CreatedByTransaction { transaction_sha256 } => {
            Ok(VerifiedRunnerProvisioningProvenance::CreatedByTransaction {
                transaction_sha256: parse_digest(transaction_sha256)?,
            })
        }
        RunnerProvisioningProvenanceWire::AdoptedFromAuthenticatedPrior {
            transaction_sha256,
            prior_generation_sha256,
            prior_final_commit_receipt_sha256,
            prior_runner_policy_binding_sha256,
            profile,
        } => Ok(
            VerifiedRunnerProvisioningProvenance::AdoptedFromAuthenticatedPrior {
                transaction_sha256: parse_digest(transaction_sha256)?,
                prior_generation_sha256: parse_digest(prior_generation_sha256)?,
                prior_final_commit_receipt_sha256: parse_digest(prior_final_commit_receipt_sha256)?,
                prior_runner_policy_binding_sha256: parse_digest(
                    prior_runner_policy_binding_sha256,
                )?,
                profile: adopted_profile_from_wire(profile)?,
            },
        ),
    }
}

fn adopted_profile_from_wire(
    profile: &RunnerAdoptedProfileProvenanceWire,
) -> Result<VerifiedRunnerAdoptedProfileProvenance, RunnerPolicyStateError> {
    match profile {
        RunnerAdoptedProfileProvenanceWire::ReusedFromAuthenticatedPrior => {
            Ok(VerifiedRunnerAdoptedProfileProvenance::ReusedFromAuthenticatedPrior)
        }
        RunnerAdoptedProfileProvenanceWire::RecreatedByTransaction {
            durable_recreation_receipt_sha256,
        } => Ok(
            VerifiedRunnerAdoptedProfileProvenance::RecreatedByTransaction {
                durable_recreation_receipt_sha256: parse_digest(durable_recreation_receipt_sha256)?,
            },
        ),
    }
}

fn provisioning_transaction_sha256(
    provenance: &VerifiedRunnerProvisioningProvenance,
) -> PolicyDigest {
    match provenance {
        VerifiedRunnerProvisioningProvenance::CreatedByTransaction { transaction_sha256 }
        | VerifiedRunnerProvisioningProvenance::AdoptedFromAuthenticatedPrior {
            transaction_sha256,
            ..
        } => *transaction_sha256,
    }
}

fn validate_account_sid(value: &str) -> Result<(), RunnerPolicyStateError> {
    let parts = value.split('-').collect::<Vec<_>>();
    let local_account_shape = parts.len() == 8
        && parts[0] == "S"
        && parts[1] == "1"
        && parts[2] == "5"
        && parts[3] == "21"
        && parts[4..]
            .iter()
            .all(|part| !part.is_empty() && (*part == "0" || !part.starts_with('0')))
        && parts[4..].iter().all(|part| part.parse::<u32>().is_ok())
        && parts[7]
            .parse::<u32>()
            .is_ok_and(|relative_id| relative_id >= 1_000);
    if !local_account_shape {
        return Err(RunnerPolicyStateError(
            "authority_runner_policy_account_sid_invalid",
        ));
    }
    Ok(())
}

fn canonical_profile_root(path: &Path) -> Result<String, RunnerPolicyStateError> {
    let text = path.to_str().ok_or(RunnerPolicyStateError(
        "authority_runner_policy_profile_path_invalid",
    ))?;
    let valid_drive = matches!(
        path.components().next(),
        Some(Component::Prefix(prefix)) if matches!(prefix.kind(), Prefix::Disk(_))
    );
    if !path.is_absolute()
        || !valid_drive
        || text.is_empty()
        || text.ends_with('\\')
        || text.ends_with('/')
        || text.contains('/')
        || text
            .chars()
            .any(|value| value == '\0' || value.is_control())
        || path
            .components()
            .any(|part| matches!(part, Component::CurDir | Component::ParentDir))
    {
        return Err(RunnerPolicyStateError(
            "authority_runner_policy_profile_path_invalid",
        ));
    }
    let bytes = text.as_bytes();
    if bytes.len() < 3 || bytes[1] != b':' || !bytes[0].is_ascii_uppercase() || bytes[2] != b'\\' {
        return Err(RunnerPolicyStateError(
            "authority_runner_policy_profile_path_invalid",
        ));
    }
    Ok(text.to_owned())
}

fn runner_token_policy_digest() -> PolicyDigest {
    let mut digest = Sha256::new();
    digest.update(RUNNER_TOKEN_POLICY_DOMAIN);
    for value in [
        "primary-token",
        "session-zero",
        "medium-integrity",
        "restricted-user-sid-exact",
        "change-notify-only",
        "no-elevation",
        "no-ui-access",
        "no-virtualization",
        "no-app-container",
        "non-inheritable-handle",
    ] {
        digest.update((value.len() as u16).to_be_bytes());
        digest.update(value.as_bytes());
    }
    digest.finalize().into()
}

fn runner_account_binding_digest(
    generation_sha256: &PolicyDigest,
    account_name: &str,
    account_sid: &str,
    provisioning_binding_sha256: &PolicyDigest,
) -> PolicyDigest {
    let mut digest = Sha256::new();
    digest.update(RUNNER_ACCOUNT_BINDING_DOMAIN);
    digest.update(generation_sha256);
    for value in [account_name, account_sid] {
        digest.update((value.len() as u16).to_be_bytes());
        digest.update(value.as_bytes());
    }
    digest.update(provisioning_binding_sha256);
    digest.update([1, 1, 1, 1, 1, 0, 0]);
    digest.finalize().into()
}

fn runner_profile_binding_digest(
    generation_sha256: &PolicyDigest,
    account_binding_sha256: &PolicyDigest,
    profile_root: &str,
    profile_identity_sha256: &PolicyDigest,
    profile_security_sha256: &PolicyDigest,
    provisioning_binding_sha256: &PolicyDigest,
) -> PolicyDigest {
    let mut digest = Sha256::new();
    digest.update(RUNNER_PROFILE_BINDING_DOMAIN);
    digest.update(generation_sha256);
    digest.update(account_binding_sha256);
    digest.update((profile_root.len() as u16).to_be_bytes());
    digest.update(profile_root.as_bytes());
    digest.update(profile_identity_sha256);
    digest.update(profile_security_sha256);
    digest.update(provisioning_binding_sha256);
    digest.update([1, 1, 1, 1]);
    digest.finalize().into()
}

fn runner_provisioning_binding_digest(
    generation_sha256: &PolicyDigest,
    provenance: &VerifiedRunnerProvisioningProvenance,
) -> PolicyDigest {
    let mut digest = Sha256::new();
    digest.update(RUNNER_PROVISIONING_BINDING_DOMAIN);
    digest.update(generation_sha256);
    match provenance {
        VerifiedRunnerProvisioningProvenance::CreatedByTransaction { transaction_sha256 } => {
            digest.update([1]);
            digest.update(transaction_sha256);
        }
        VerifiedRunnerProvisioningProvenance::AdoptedFromAuthenticatedPrior {
            transaction_sha256,
            prior_generation_sha256,
            prior_final_commit_receipt_sha256,
            prior_runner_policy_binding_sha256,
            profile,
        } => {
            digest.update([2]);
            digest.update(transaction_sha256);
            digest.update(prior_generation_sha256);
            digest.update(prior_final_commit_receipt_sha256);
            digest.update(prior_runner_policy_binding_sha256);
            match profile {
                VerifiedRunnerAdoptedProfileProvenance::ReusedFromAuthenticatedPrior => {
                    digest.update([1]);
                }
                VerifiedRunnerAdoptedProfileProvenance::RecreatedByTransaction {
                    durable_recreation_receipt_sha256,
                } => {
                    digest.update([2]);
                    digest.update(durable_recreation_receipt_sha256);
                }
            }
        }
    }
    digest.finalize().into()
}

fn runner_policy_state_binding_digest(
    generation_sha256: &PolicyDigest,
    provisioning_binding_sha256: &PolicyDigest,
    account_binding_sha256: &PolicyDigest,
    profile_binding_sha256: &PolicyDigest,
    token_policy_sha256: &PolicyDigest,
) -> PolicyDigest {
    let mut digest = Sha256::new();
    digest.update(RUNNER_POLICY_STATE_BINDING_DOMAIN);
    digest.update(generation_sha256);
    digest.update(provisioning_binding_sha256);
    digest.update(account_binding_sha256);
    digest.update(profile_binding_sha256);
    digest.update(token_policy_sha256);
    digest.finalize().into()
}

fn is_zero(value: &PolicyDigest) -> bool {
    value.iter().all(|byte| *byte == 0)
}

fn hex_lower(value: &PolicyDigest) -> String {
    value.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn parse_digest(value: &str) -> Result<PolicyDigest, RunnerPolicyStateError> {
    if value.len() != 64
        || value
            .as_bytes()
            .iter()
            .any(|byte| !byte.is_ascii_digit() && !(b'a'..=b'f').contains(byte))
    {
        return Err(RunnerPolicyStateError(
            "authority_runner_policy_digest_invalid",
        ));
    }
    let mut digest = [0u8; 32];
    for (index, target) in digest.iter_mut().enumerate() {
        *target = u8::from_str_radix(&value[index * 2..index * 2 + 2], 16)
            .map_err(|_| RunnerPolicyStateError("authority_runner_policy_digest_invalid"))?;
    }
    if is_zero(&digest) {
        return Err(RunnerPolicyStateError(
            "authority_runner_policy_digest_invalid",
        ));
    }
    Ok(digest)
}

#[cfg(test)]
mod tests {
    use super::*;

    const GENERATION: PolicyDigest = [0x31; 32];
    const TRANSACTION: PolicyDigest = [0x11; 32];

    fn created_readback(transaction_sha256: PolicyDigest) -> VerifiedRunnerMachinePolicyReadback {
        let mut readback = VerifiedRunnerMachinePolicyReadback::for_test(
            "S-1-5-21-111-222-333-1001",
            r"C:\Users\VRCForgeRunner",
        );
        readback.provisioning =
            VerifiedRunnerProvisioningProvenance::CreatedByTransaction { transaction_sha256 };
        readback
    }

    fn adopted_readback(
        profile: VerifiedRunnerAdoptedProfileProvenance,
    ) -> VerifiedRunnerMachinePolicyReadback {
        VerifiedRunnerMachinePolicyReadback::for_revalidated_test(
            "S-1-5-21-111-222-333-1001",
            r"C:\Users\VRCForgeRunner",
            profile,
        )
    }

    fn state_for(
        generation_sha256: PolicyDigest,
        transaction_sha256: PolicyDigest,
    ) -> CanonicalRunnerPolicyState {
        CanonicalRunnerPolicyState::from_verified_machine_readback(
            generation_sha256,
            transaction_sha256,
            created_readback(transaction_sha256),
        )
        .unwrap()
    }

    fn state() -> CanonicalRunnerPolicyState {
        state_for(GENERATION, TRANSACTION)
    }

    #[test]
    fn canonical_state_round_trips_and_keeps_product_machine_source_closed() {
        assert_eq!(
            VerifiedRunnerMachinePolicyReadback::from_production_machine_readback()
                .unwrap_err()
                .code(),
            RUNNER_POLICY_MACHINE_READBACK_BLOCKER
        );
        let original = state();
        let bytes = original.canonical_bytes().unwrap();
        let parsed = CanonicalRunnerPolicyState::parse_canonical(&bytes).unwrap();
        assert_eq!(parsed, original);
        let descriptor = parsed.descriptor().unwrap();
        let expected_bytes_sha256: PolicyDigest = Sha256::digest(&bytes).into();
        assert_eq!(descriptor.generation_sha256(), GENERATION);
        assert_eq!(descriptor.transaction_sha256(), TRANSACTION);
        assert_eq!(descriptor.byte_length(), bytes.len() as u64);
        assert_eq!(descriptor.bytes_sha256(), expected_bytes_sha256);
        assert_eq!(descriptor.binding_sha256(), *parsed.binding_sha256());
        assert_eq!(parsed.generation_sha256(), &GENERATION);
        assert_eq!(parsed.transaction_sha256(), &TRANSACTION);
        assert_eq!(parsed.profile_root(), Path::new(r"C:\Users\VRCForgeRunner"));
        assert_eq!(parsed.canonical_account_sid(), "S-1-5-21-111-222-333-1001");
        assert_eq!(
            format!("{parsed:?}"),
            "CanonicalRunnerPolicyState(<redacted>)"
        );
    }

    #[test]
    fn exact_transaction_is_required_and_valid_foreign_states_stay_distinct() {
        assert_eq!(
            CanonicalRunnerPolicyState::from_verified_machine_readback(
                GENERATION,
                [0; 32],
                created_readback(TRANSACTION),
            )
            .unwrap_err()
            .code(),
            "authority_runner_policy_transaction_invalid"
        );
        assert_eq!(
            CanonicalRunnerPolicyState::from_verified_machine_readback(
                GENERATION,
                [0x12; 32],
                created_readback(TRANSACTION),
            )
            .unwrap_err()
            .code(),
            "authority_runner_policy_provisioning_provenance_invalid"
        );

        let first = state_for(GENERATION, TRANSACTION);
        let foreign_transaction = state_for(GENERATION, [0x12; 32]);
        let foreign_generation = state_for([0x32; 32], TRANSACTION);
        let first_bytes = first.canonical_bytes().unwrap();
        let transaction_bytes = foreign_transaction.canonical_bytes().unwrap();
        let generation_bytes = foreign_generation.canonical_bytes().unwrap();
        assert_eq!(first_bytes.len(), transaction_bytes.len());
        assert_eq!(first_bytes.len(), generation_bytes.len());
        assert_ne!(first_bytes, transaction_bytes);
        assert_ne!(first_bytes, generation_bytes);
        assert_ne!(
            first.descriptor().unwrap(),
            foreign_transaction.descriptor().unwrap()
        );
        assert_ne!(
            first.descriptor().unwrap(),
            foreign_generation.descriptor().unwrap()
        );
    }

    #[test]
    fn canonical_state_rejects_cross_generation_and_every_security_claim_drift() {
        let bytes = state().canonical_bytes().unwrap();
        let original: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        for (field, replacement) in [
            (
                "generationSha256",
                serde_json::json!(hex_lower(&[0x32; 32])),
            ),
            ("accountName", serde_json::json!("VRCForgeRunner2")),
            ("accountSid", serde_json::json!("S-1-5-21-111-222-333-1002")),
            ("profileRoot", serde_json::json!(r"C:\Users\OtherRunner")),
            (
                "provisioning",
                serde_json::json!({
                    "kind": "createdByTransaction",
                    "transactionSha256": hex_lower(&[0x60; 32]),
                }),
            ),
            ("accountNameSidRoundTripVerified", serde_json::json!(false)),
            ("batchLogonGranted", serde_json::json!(false)),
            ("interactiveLogonDenied", serde_json::json!(false)),
            ("networkLogonDenied", serde_json::json!(false)),
            ("serviceLogonDenied", serde_json::json!(false)),
            ("administratorMember", serde_json::json!(true)),
            ("serviceIdentityMember", serde_json::json!(true)),
            ("profileLocalVolume", serde_json::json!(false)),
            ("profileReparseFreeHeldChain", serde_json::json!(false)),
            ("profileExactOwnerAndAcl", serde_json::json!(false)),
            (
                "profileIdentitySha256",
                serde_json::json!(hex_lower(&[0x51; 32])),
            ),
            (
                "profileSecuritySha256",
                serde_json::json!(hex_lower(&[0x52; 32])),
            ),
            (
                "tokenPolicySha256",
                serde_json::json!(hex_lower(&[0x53; 32])),
            ),
            (
                "provisioningBindingSha256",
                serde_json::json!(hex_lower(&[0x57; 32])),
            ),
            (
                "accountBindingSha256",
                serde_json::json!(hex_lower(&[0x54; 32])),
            ),
            (
                "profileBindingSha256",
                serde_json::json!(hex_lower(&[0x55; 32])),
            ),
            ("bindingSha256", serde_json::json!(hex_lower(&[0x56; 32]))),
        ] {
            let mut changed = original.clone();
            changed[field] = replacement;
            let changed = serde_json::to_vec(&changed).unwrap();
            assert!(
                CanonicalRunnerPolicyState::parse_canonical(&changed).is_err(),
                "{field} drift must fail"
            );
        }
    }

    #[test]
    fn adopted_profile_provenance_is_typed_and_domain_separated() {
        let profiles = [
            VerifiedRunnerAdoptedProfileProvenance::ReusedFromAuthenticatedPrior,
            VerifiedRunnerAdoptedProfileProvenance::RecreatedByTransaction {
                durable_recreation_receipt_sha256: [0x24; 32],
            },
        ];
        let mut bindings = Vec::new();
        for profile in profiles {
            let state = CanonicalRunnerPolicyState::from_verified_machine_readback(
                GENERATION,
                TRANSACTION,
                adopted_readback(profile),
            )
            .unwrap();
            bindings.push(*state.binding_sha256());
            assert_eq!(
                CanonicalRunnerPolicyState::parse_canonical(&state.canonical_bytes().unwrap())
                    .unwrap(),
                state
            );
        }
        assert_ne!(bindings[0], bindings[1]);
    }

    #[test]
    fn adopted_provenance_rejects_missing_stale_or_foreign_durable_bindings() {
        let assert_invalid = |readback| {
            assert_eq!(
                CanonicalRunnerPolicyState::from_verified_machine_readback(
                    GENERATION,
                    TRANSACTION,
                    readback,
                )
                .unwrap_err()
                .code(),
                "authority_runner_policy_provisioning_provenance_invalid"
            );
        };

        let mut zero_transaction =
            adopted_readback(VerifiedRunnerAdoptedProfileProvenance::ReusedFromAuthenticatedPrior);
        let mut zero_prior_generation =
            adopted_readback(VerifiedRunnerAdoptedProfileProvenance::ReusedFromAuthenticatedPrior);
        let mut zero_prior_receipt =
            adopted_readback(VerifiedRunnerAdoptedProfileProvenance::ReusedFromAuthenticatedPrior);
        let mut zero_prior_binding =
            adopted_readback(VerifiedRunnerAdoptedProfileProvenance::ReusedFromAuthenticatedPrior);
        let mut stale_generation =
            adopted_readback(VerifiedRunnerAdoptedProfileProvenance::ReusedFromAuthenticatedPrior);
        for (readback, field) in [
            (&mut zero_transaction, 0usize),
            (&mut zero_prior_generation, 1),
            (&mut zero_prior_receipt, 2),
            (&mut zero_prior_binding, 3),
            (&mut stale_generation, 4),
        ] {
            if let VerifiedRunnerProvisioningProvenance::AdoptedFromAuthenticatedPrior {
                transaction_sha256,
                prior_generation_sha256,
                prior_final_commit_receipt_sha256,
                prior_runner_policy_binding_sha256,
                ..
            } = &mut readback.provisioning
            {
                match field {
                    0 => *transaction_sha256 = [0; 32],
                    1 => *prior_generation_sha256 = [0; 32],
                    2 => *prior_final_commit_receipt_sha256 = [0; 32],
                    3 => *prior_runner_policy_binding_sha256 = [0; 32],
                    4 => *prior_generation_sha256 = GENERATION,
                    _ => unreachable!(),
                }
            }
        }
        for readback in [
            zero_transaction,
            zero_prior_generation,
            zero_prior_receipt,
            zero_prior_binding,
            stale_generation,
        ] {
            assert_invalid(readback);
        }

        let mut foreign_transaction =
            adopted_readback(VerifiedRunnerAdoptedProfileProvenance::ReusedFromAuthenticatedPrior);
        if let VerifiedRunnerProvisioningProvenance::AdoptedFromAuthenticatedPrior {
            transaction_sha256,
            ..
        } = &mut foreign_transaction.provisioning
        {
            *transaction_sha256 = [0x12; 32];
        }
        assert_invalid(foreign_transaction);

        let mut missing_recreation = adopted_readback(
            VerifiedRunnerAdoptedProfileProvenance::RecreatedByTransaction {
                durable_recreation_receipt_sha256: [0x24; 32],
            },
        );
        if let VerifiedRunnerProvisioningProvenance::AdoptedFromAuthenticatedPrior {
            profile:
                VerifiedRunnerAdoptedProfileProvenance::RecreatedByTransaction {
                    durable_recreation_receipt_sha256,
                },
            ..
        } = &mut missing_recreation.provisioning
        {
            *durable_recreation_receipt_sha256 = [0; 32];
        }
        assert_invalid(missing_recreation);

        let mut created_without_transaction = created_readback(TRANSACTION);
        created_without_transaction.provisioning =
            VerifiedRunnerProvisioningProvenance::CreatedByTransaction {
                transaction_sha256: [0; 32],
            };
        assert_invalid(created_without_transaction);
    }

    #[test]
    fn canonical_shape_rejects_legacy_missing_unknown_and_trailing_data() {
        let bytes = state().canonical_bytes().unwrap();
        let original: serde_json::Value = serde_json::from_slice(&bytes).unwrap();

        let mut legacy = original.clone();
        legacy["schema"] = serde_json::json!("vrcforge.primitive_evidence_runner_policy_state.v1");
        assert!(
            CanonicalRunnerPolicyState::parse_canonical(&serde_json::to_vec(&legacy).unwrap())
                .is_err()
        );

        let mut missing = original.clone();
        missing.as_object_mut().unwrap().remove("accountSid");
        assert!(CanonicalRunnerPolicyState::parse_canonical(
            &serde_json::to_vec(&missing).unwrap()
        )
        .is_err());

        let mut unknown = original;
        unknown
            .as_object_mut()
            .unwrap()
            .insert("unexpected".to_owned(), serde_json::json!(true));
        assert!(CanonicalRunnerPolicyState::parse_canonical(
            &serde_json::to_vec(&unknown).unwrap()
        )
        .is_err());

        let mut trailing = bytes;
        trailing.push(b'\n');
        assert_eq!(
            CanonicalRunnerPolicyState::parse_canonical(&trailing)
                .unwrap_err()
                .code(),
            "authority_runner_policy_state_noncanonical"
        );
    }

    #[test]
    fn state_rejects_noncanonical_sid_and_profile() {
        for sid in [
            "S-1-5-18",
            "S-1-5-21-111-222-333-500",
            "S-1-5-21-0111-222-333-1001",
            "s-1-5-21-111-222-333-1001",
        ] {
            assert!(CanonicalRunnerPolicyState::from_verified_machine_readback(
                GENERATION,
                TRANSACTION,
                VerifiedRunnerMachinePolicyReadback::for_test(sid, r"C:\Users\VRCForgeRunner"),
            )
            .is_err());
        }
        for path in [
            r"c:\Users\VRCForgeRunner",
            r"C:/Users/VRCForgeRunner",
            r"C:\Users\Runner\..\Other",
            r"\\server\share\Runner",
            r"relative\Runner",
        ] {
            assert!(CanonicalRunnerPolicyState::from_verified_machine_readback(
                GENERATION,
                TRANSACTION,
                VerifiedRunnerMachinePolicyReadback::for_test("S-1-5-21-111-222-333-1001", path,),
            )
            .is_err());
        }
    }
}
