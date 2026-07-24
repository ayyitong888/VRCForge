use super::*;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum CanonicalUnsignedManifestPayload {
    Trust {
        generation: [u8; 32],
        signer_key_id: [u8; 32],
        signer_public_key_sec1: [u8; 65],
        ledger_identity: [u8; 32],
        created_epoch: u64,
        valid: bool,
        revoked: bool,
    },
    Activation {
        generation: [u8; 32],
        trust_manifest_sha256: [u8; 32],
        signer_key_id: [u8; 32],
        activated_epoch: u64,
        previous_generation: Option<[u8; 32]>,
        previous_activation_sha256: Option<[u8; 32]>,
        previous_activation_epoch: Option<u64>,
        valid: bool,
        revoked: bool,
    },
    Retirement {
        generation: [u8; 32],
        prior_activation_sha256: [u8; 32],
        retired_epoch: u64,
        successor_generation: Option<[u8; 32]>,
        successor_activation_sha256: Option<[u8; 32]>,
        valid: bool,
        revoked: bool,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct VerifiedDetachedSignatureProof {
    pub(super) signer_key_id: [u8; 32],
    pub(super) unsigned_payload_sha256: [u8; 32],
    pub(super) receipt_sha256: [u8; 32],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct DetachedManifestReadback {
    pub(super) unsigned_payload: CanonicalUnsignedManifestPayload,
    pub(super) unsigned_payload_sha256: [u8; 32],
    pub(super) signature: VerifiedDetachedSignatureProof,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct VerifiedProtectedActivationHead {
    pub(super) generation: [u8; 32],
    pub(super) activation_manifest_sha256: [u8; 32],
    pub(super) activation_epoch: u64,
    pub(super) volume_serial: u64,
    pub(super) file_id: [u8; 16],
    pub(super) protected_head_receipt_sha256: [u8; 32],
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct RawManifestChainReadback {
    pub(super) trust: DetachedManifestReadback,
    pub(super) activation: DetachedManifestReadback,
    pub(super) retirement: Option<DetachedManifestReadback>,
    pub(super) protected_activation_history: Vec<DetachedManifestReadback>,
    pub(super) observed_heads: Vec<VerifiedProtectedActivationHead>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct VerifiedManifestChain {
    pub(super) signer_key_id: [u8; 32],
    pub(super) signer_public_key_sec1: [u8; 65],
    pub(super) trust_manifest_sha256: [u8; 32],
    pub(super) activation_manifest_sha256: [u8; 32],
    pub(super) activation_epoch: u64,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) enum ManifestKind {
    Trust,
    Activation,
    Retirement,
    Recovery,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub(crate) enum CanonicalUnsignedManifestTemplate {
    Trust {
        schema: &'static str,
        generation: String,
        signer_key_id_source: &'static str,
        signer_public_key_source: &'static str,
        ledger_identity_source: &'static str,
        created_epoch_source: &'static str,
        valid: bool,
        revoked: bool,
    },
    Activation {
        schema: &'static str,
        generation: String,
        trust_manifest_digest_source: &'static str,
        signer_key_id_source: &'static str,
        activated_epoch: u64,
        previous_generation: Option<String>,
        previous_activation_sha256: Option<String>,
        previous_activation_epoch: Option<u64>,
        valid: bool,
        revoked: bool,
    },
    Retirement {
        schema: &'static str,
        generation: String,
        prior_activation_sha256: String,
        retired_epoch: u64,
        successor_generation: Option<String>,
        successor_activation_digest_reference: Option<ProtectedActivationDigestReference>,
        valid: bool,
        revoked: bool,
    },
    Recovery {
        schema: &'static str,
        generation: String,
        plan_sha256: String,
        terminal_source: &'static str,
    },
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DetachedManifestContractProjection {
    schema: &'static str,
    manifest_kind: ManifestKind,
    unsigned_payload: CanonicalUnsignedManifestTemplate,
    digest_algorithm: &'static str,
    signature_algorithm: &'static str,
    digest_scope: &'static str,
    digest_excludes_digest_field: bool,
    digest_excludes_signature_field: bool,
    signer_generation_equality_required: bool,
    epoch_monotonicity_required: bool,
    unique_head_required: bool,
    fork_rejected: bool,
}

pub(super) fn detached_manifest_contract(
    manifest_kind: ManifestKind,
    unsigned_payload: CanonicalUnsignedManifestTemplate,
) -> DetachedManifestContractProjection {
    DetachedManifestContractProjection {
        schema: DETACHED_MANIFEST_SCHEMA,
        manifest_kind,
        unsigned_payload,
        digest_algorithm: "SHA256",
        signature_algorithm: "ECDSA_P256_SHA256",
        digest_scope: "canonicalUnsignedPayloadOnly",
        digest_excludes_digest_field: true,
        digest_excludes_signature_field: true,
        signer_generation_equality_required: true,
        epoch_monotonicity_required: true,
        unique_head_required: true,
        fork_rejected: true,
    }
}

pub(super) fn trust_manifest_contract(generation: &str) -> DetachedManifestContractProjection {
    detached_manifest_contract(
        ManifestKind::Trust,
        CanonicalUnsignedManifestTemplate::Trust {
            schema: TRUST_MANIFEST_SCHEMA,
            generation: generation.to_string(),
            signer_key_id_source: "protectedMachineKeyReadback",
            signer_public_key_source: "protectedMachineKeyReadback",
            ledger_identity_source: "protectedLedgerReadback",
            created_epoch_source: "protectedActivationChainEpoch",
            valid: true,
            revoked: false,
        },
    )
}

pub(super) fn activation_manifest_contract(
    generation: &str,
    prior: Option<&VerifiedInstalledGeneration>,
) -> DetachedManifestContractProjection {
    detached_manifest_contract(
        ManifestKind::Activation,
        CanonicalUnsignedManifestTemplate::Activation {
            schema: ACTIVE_GENERATION_SCHEMA,
            generation: generation.to_string(),
            trust_manifest_digest_source: "canonicalTrustUnsignedPayloadDigest",
            signer_key_id_source: "protectedMachineKeyReadback",
            activated_epoch: prior
                .map(|value| value.activation_epoch.saturating_add(1))
                .unwrap_or(1),
            previous_generation: prior.map(|value| hex_lower(&value.generation)),
            previous_activation_sha256: prior
                .map(|value| hex_lower(&value.activation_manifest_sha256)),
            previous_activation_epoch: prior.map(|value| value.activation_epoch),
            valid: true,
            revoked: false,
        },
    )
}

pub(super) fn retirement_manifest_contract(
    prior: &VerifiedInstalledGeneration,
    successor_generation: Option<String>,
    successor_activation_digest_reference: Option<ProtectedActivationDigestReference>,
) -> DetachedManifestContractProjection {
    detached_manifest_contract(
        ManifestKind::Retirement,
        CanonicalUnsignedManifestTemplate::Retirement {
            schema: RETIREMENT_MANIFEST_SCHEMA,
            generation: hex_lower(&prior.generation),
            prior_activation_sha256: hex_lower(&prior.activation_manifest_sha256),
            retired_epoch: prior.activation_epoch.saturating_add(1),
            successor_generation,
            successor_activation_digest_reference,
            valid: false,
            revoked: true,
        },
    )
}

pub(super) fn verify_manifest_chain(
    generation: [u8; 32],
    key: &VerifiedKeyProof,
    ledger: &VerifiedLedgerProof,
    chain: &RawManifestChainReadback,
) -> Result<VerifiedManifestChain, AuthorityMaintenanceError> {
    if key.signer_key_id.iter().all(|value| *value == 0)
        || key.signer_public_key_sec1[0] != 0x04
        || key.signer_public_key_sec1[1..]
            .iter()
            .all(|value| *value == 0)
        || Sha256::digest(key.signer_public_key_sec1).as_slice() != key.signer_key_id
        || ledger.ledger_identity.iter().all(|value| *value == 0)
    {
        return Err(AuthorityMaintenanceError(
            "authority_manifest_signer_or_ledger_invalid",
        ));
    }
    let trust_digest = verify_detached_manifest(&chain.trust)?;
    let activation = verify_activation_link(&chain.activation)?;
    let activation_digest = activation.digest;
    if chain.observed_heads.len() != 1 {
        return Err(AuthorityMaintenanceError(
            "authority_manifest_unique_head_not_verified",
        ));
    }
    let head = chain.observed_heads[0];
    if head.generation != generation
        || head.generation != activation.generation
        || head.activation_manifest_sha256 != activation_digest
        || head.activation_epoch != activation.epoch
        || head.volume_serial == 0
        || head.file_id.iter().all(|value| *value == 0)
        || head
            .protected_head_receipt_sha256
            .iter()
            .all(|value| *value == 0)
    {
        return Err(AuthorityMaintenanceError(
            "authority_manifest_unique_head_not_verified",
        ));
    }
    if chain.retirement.is_some() {
        return Err(AuthorityMaintenanceError(
            "authority_installed_generation_retired",
        ));
    }
    let (
        trust_generation,
        trust_key_id,
        trust_public_key,
        trust_ledger,
        trust_epoch,
        trust_valid,
        trust_revoked,
    ) = match chain.trust.unsigned_payload {
        CanonicalUnsignedManifestPayload::Trust {
            generation,
            signer_key_id,
            signer_public_key_sec1,
            ledger_identity,
            created_epoch,
            valid,
            revoked,
        } => (
            generation,
            signer_key_id,
            signer_public_key_sec1,
            ledger_identity,
            created_epoch,
            valid,
            revoked,
        ),
        _ => {
            return Err(AuthorityMaintenanceError(
                "authority_trust_manifest_kind_invalid",
            ))
        }
    };
    if trust_generation != generation
        || trust_key_id != key.signer_key_id
        || trust_public_key != key.signer_public_key_sec1
        || trust_ledger != ledger.ledger_identity
        || chain.trust.signature.signer_key_id != key.signer_key_id
        || trust_epoch == 0
        || !trust_valid
        || trust_revoked
    {
        return Err(AuthorityMaintenanceError(
            "authority_trust_manifest_binding_invalid",
        ));
    }
    let mut prior_link: Option<VerifiedActivationLink> = None;
    for historical_manifest in &chain.protected_activation_history {
        let historical = verify_activation_link(historical_manifest)?;
        if !activation_follows(historical, prior_link) {
            return Err(AuthorityMaintenanceError(
                "authority_manifest_predecessor_not_verified",
            ));
        }
        prior_link = Some(historical);
    }
    if !activation_follows(activation, prior_link) {
        return Err(AuthorityMaintenanceError(
            "authority_manifest_predecessor_not_verified",
        ));
    }
    if activation.generation != generation
        || activation.trust_manifest_sha256 != trust_digest
        || activation.signer_key_id != key.signer_key_id
        || chain.activation.signature.signer_key_id != key.signer_key_id
    {
        return Err(AuthorityMaintenanceError(
            "authority_activation_manifest_binding_invalid",
        ));
    }
    if activation.epoch != trust_epoch {
        return Err(AuthorityMaintenanceError(
            "authority_manifest_epoch_domain_mismatch",
        ));
    }
    Ok(VerifiedManifestChain {
        signer_key_id: key.signer_key_id,
        signer_public_key_sec1: key.signer_public_key_sec1,
        trust_manifest_sha256: trust_digest,
        activation_manifest_sha256: activation_digest,
        activation_epoch: activation.epoch,
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct VerifiedActivationLink {
    generation: [u8; 32],
    trust_manifest_sha256: [u8; 32],
    signer_key_id: [u8; 32],
    epoch: u64,
    previous_generation: Option<[u8; 32]>,
    previous_activation_sha256: Option<[u8; 32]>,
    previous_activation_epoch: Option<u64>,
    digest: [u8; 32],
}

fn verify_activation_link(
    manifest: &DetachedManifestReadback,
) -> Result<VerifiedActivationLink, AuthorityMaintenanceError> {
    let digest = verify_detached_manifest(manifest)?;
    let (
        generation,
        trust_manifest_sha256,
        signer_key_id,
        epoch,
        previous_generation,
        previous_activation_sha256,
        previous_activation_epoch,
        valid,
        revoked,
    ) = match manifest.unsigned_payload {
        CanonicalUnsignedManifestPayload::Activation {
            generation,
            trust_manifest_sha256,
            signer_key_id,
            activated_epoch,
            previous_generation,
            previous_activation_sha256,
            previous_activation_epoch,
            valid,
            revoked,
        } => (
            generation,
            trust_manifest_sha256,
            signer_key_id,
            activated_epoch,
            previous_generation,
            previous_activation_sha256,
            previous_activation_epoch,
            valid,
            revoked,
        ),
        _ => {
            return Err(AuthorityMaintenanceError(
                "authority_activation_manifest_kind_invalid",
            ))
        }
    };
    if generation.iter().all(|value| *value == 0)
        || trust_manifest_sha256.iter().all(|value| *value == 0)
        || signer_key_id.iter().all(|value| *value == 0)
        || manifest.signature.signer_key_id != signer_key_id
        || epoch == 0
        || !valid
        || revoked
    {
        return Err(AuthorityMaintenanceError(
            "authority_activation_manifest_binding_invalid",
        ));
    }
    Ok(VerifiedActivationLink {
        generation,
        trust_manifest_sha256,
        signer_key_id,
        epoch,
        previous_generation,
        previous_activation_sha256,
        previous_activation_epoch,
        digest,
    })
}

fn activation_follows(
    candidate: VerifiedActivationLink,
    predecessor: Option<VerifiedActivationLink>,
) -> bool {
    match predecessor {
        None => {
            candidate.epoch == 1
                && candidate.previous_generation.is_none()
                && candidate.previous_activation_sha256.is_none()
                && candidate.previous_activation_epoch.is_none()
        }
        Some(predecessor) => {
            candidate.previous_generation == Some(predecessor.generation)
                && candidate.previous_activation_sha256 == Some(predecessor.digest)
                && candidate.previous_activation_epoch == Some(predecessor.epoch)
                && predecessor.epoch.checked_add(1) == Some(candidate.epoch)
        }
    }
}

fn verify_detached_manifest(
    manifest: &DetachedManifestReadback,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let expected = canonical_unsigned_manifest_digest(&manifest.unsigned_payload);
    if manifest.unsigned_payload_sha256 != expected
        || manifest
            .signature
            .signer_key_id
            .iter()
            .all(|value| *value == 0)
        || manifest.signature.unsigned_payload_sha256 != expected
        || manifest
            .signature
            .receipt_sha256
            .iter()
            .all(|value| *value == 0)
    {
        return Err(AuthorityMaintenanceError(
            "authority_detached_manifest_not_verified",
        ));
    }
    Ok(expected)
}

pub(super) fn verify_retirement_link(
    manifest: &DetachedManifestReadback,
    prior_generation: [u8; 32],
    prior_activation_sha256: [u8; 32],
    prior_activation_epoch: u64,
    successor: Option<([u8; 32], [u8; 32], u64)>,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let digest = verify_detached_manifest(manifest)?;
    let (
        generation,
        prior_activation,
        retired_epoch,
        successor_generation,
        successor_activation,
        valid,
        revoked,
    ) = match manifest.unsigned_payload {
        CanonicalUnsignedManifestPayload::Retirement {
            generation,
            prior_activation_sha256,
            retired_epoch,
            successor_generation,
            successor_activation_sha256,
            valid,
            revoked,
        } => (
            generation,
            prior_activation_sha256,
            retired_epoch,
            successor_generation,
            successor_activation_sha256,
            valid,
            revoked,
        ),
        _ => {
            return Err(AuthorityMaintenanceError(
                "authority_retirement_manifest_kind_invalid",
            ))
        }
    };
    let successor_exact = match successor {
        None => successor_generation.is_none() && successor_activation.is_none(),
        Some((expected_generation, expected_activation, successor_epoch)) => {
            successor_generation == Some(expected_generation)
                && successor_activation == Some(expected_activation)
                && successor_epoch == retired_epoch
        }
    };
    if generation != prior_generation
        || prior_activation != prior_activation_sha256
        || retired_epoch != prior_activation_epoch.saturating_add(1)
        || !successor_exact
        || valid
        || !revoked
    {
        return Err(AuthorityMaintenanceError(
            "authority_retirement_manifest_link_invalid",
        ));
    }
    Ok(digest)
}

pub(super) fn canonical_unsigned_manifest_digest(
    payload: &CanonicalUnsignedManifestPayload,
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(UNSIGNED_MANIFEST_DOMAIN);
    match payload {
        CanonicalUnsignedManifestPayload::Trust {
            generation,
            signer_key_id,
            signer_public_key_sec1,
            ledger_identity,
            created_epoch,
            valid,
            revoked,
        } => {
            digest.update([1]);
            digest.update(generation);
            digest.update(signer_key_id);
            digest.update(signer_public_key_sec1);
            digest.update(ledger_identity);
            digest.update(created_epoch.to_be_bytes());
            digest.update([u8::from(*valid), u8::from(*revoked)]);
        }
        CanonicalUnsignedManifestPayload::Activation {
            generation,
            trust_manifest_sha256,
            signer_key_id,
            activated_epoch,
            previous_generation,
            previous_activation_sha256,
            previous_activation_epoch,
            valid,
            revoked,
        } => {
            digest.update([2]);
            digest.update(generation);
            digest.update(trust_manifest_sha256);
            digest.update(signer_key_id);
            digest.update(activated_epoch.to_be_bytes());
            digest_optional_32(&mut digest, previous_generation);
            digest_optional_32(&mut digest, previous_activation_sha256);
            match previous_activation_epoch {
                Some(value) => {
                    digest.update([1]);
                    digest.update(value.to_be_bytes());
                }
                None => digest.update([0]),
            }
            digest.update([u8::from(*valid), u8::from(*revoked)]);
        }
        CanonicalUnsignedManifestPayload::Retirement {
            generation,
            prior_activation_sha256,
            retired_epoch,
            successor_generation,
            successor_activation_sha256,
            valid,
            revoked,
        } => {
            digest.update([3]);
            digest.update(generation);
            digest.update(prior_activation_sha256);
            digest.update(retired_epoch.to_be_bytes());
            digest_optional_32(&mut digest, successor_generation);
            digest_optional_32(&mut digest, successor_activation_sha256);
            digest.update([u8::from(*valid), u8::from(*revoked)]);
        }
    }
    digest.finalize().into()
}

fn digest_optional_32(digest: &mut Sha256, value: &Option<[u8; 32]>) {
    match value {
        Some(value) => {
            digest.update([1]);
            digest.update(value);
        }
        None => digest.update([0]),
    }
}
