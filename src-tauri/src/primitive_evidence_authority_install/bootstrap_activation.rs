use super::bootstrap::AuthorityBootstrapError;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub(crate) const CANDIDATE_ACTIVATION_CREDENTIAL_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_candidate_activation.v1";
pub(crate) const MAX_CANDIDATE_CREDENTIAL_BYTES: u64 = 64 * 1024;
pub(crate) const MAX_CANDIDATE_CREDENTIAL_LIFETIME_MILLIS: u64 = 120_000;
pub(crate) const CANDIDATE_HANDSHAKE_REQUEST_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_candidate_handshake_request.v1";
pub(crate) const CANDIDATE_HANDSHAKE_RESPONSE_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_candidate_handshake_response.v1";
pub(crate) const MAX_CANDIDATE_HANDSHAKE_BYTES: usize = 4 * 1024;
pub(crate) const CANDIDATE_HANDSHAKE_WINDOW_MILLIS: u32 = 30_000;
pub(crate) const CANDIDATE_HANDSHAKE_CLOSEOUT_GRACE_MILLIS: u32 = 5_000;
pub(crate) const CANDIDATE_START_PENDING_HANDSHAKE_WAIT_HINT_MILLIS: u32 =
    CANDIDATE_HANDSHAKE_WINDOW_MILLIS + CANDIDATE_HANDSHAKE_CLOSEOUT_GRACE_MILLIS;
pub(crate) const CANDIDATE_SERVICE_START_MODE_TOKEN: &str = "--candidate-validation-v1";
const CANDIDATE_SERVICE_START_TRANSACTION_PREFIX: &str = "--transaction-sha256=";
const CANDIDATE_SERVICE_START_CAPSULE_PREFIX: &str = "--capsule-sha256=";
const CANDIDATE_SERVICE_START_NONCE_PREFIX: &str = "--candidate-nonce=";
const CANDIDATE_SERVICE_START_CREDENTIAL_PREFIX: &str = "--credential-sha256=";

const CANDIDATE_CREDENTIAL_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-activation-credential-v1\0";
const CANDIDATE_RECORD_DOMAIN: &[u8] = b"vrcforge-authority-candidate-activation-record-v1\0";
const CANDIDATE_HANDSHAKE_PIPE_DOMAIN: &[u8] = b"vrcforge-authority-candidate-activation-pipe-v1\0";
const CANDIDATE_HANDSHAKE_RECEIPT_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-activation-handshake-v1\0";
const CANDIDATE_HANDSHAKE_REQUEST_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-activation-request-v1\0";
const CANDIDATE_IMAGE_EVIDENCE_DOMAIN: &[u8] = b"vrcforge-authority-candidate-image-evidence-v1\0";
const CANDIDATE_PROCESS_EVIDENCE_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-process-evidence-v1\0";
const CANDIDATE_CONSUMPTION_RECEIPT_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-consumption-v1\0";
const MAX_CANDIDATE_IMAGE_BYTES: u64 = 512 * 1024 * 1024;
const FILE_ATTRIBUTE_DIRECTORY_VALUE: u32 = 0x10;
const FILE_ATTRIBUTE_REPARSE_POINT_VALUE: u32 = 0x400;

/// Exact held executable evidence that can be prepared before the candidate
/// service starts. It deliberately contains no process identifier.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct CandidateImageEvidence {
    image_sha256: [u8; 32],
    image_byte_length: u64,
    image_volume_serial: u64,
    image_file_id: [u8; 16],
    image_link_count: u32,
    image_attributes: u32,
    full_readback_receipt_sha256: [u8; 32],
}

impl CandidateImageEvidence {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn from_held_image(
        image_sha256: [u8; 32],
        image_byte_length: u64,
        image_volume_serial: u64,
        image_file_id: [u8; 16],
        image_link_count: u32,
        image_attributes: u32,
    ) -> Result<Self, AuthorityBootstrapError> {
        let mut value = Self {
            image_sha256,
            image_byte_length,
            image_volume_serial,
            image_file_id,
            image_link_count,
            image_attributes,
            full_readback_receipt_sha256: [0; 32],
        };
        value.full_readback_receipt_sha256 = candidate_image_evidence_digest(&value);
        value.validate()?;
        Ok(value)
    }

    pub(crate) fn validate(&self) -> Result<(), AuthorityBootstrapError> {
        if is_zero_digest(&self.image_sha256)
            || self.image_byte_length == 0
            || self.image_byte_length > MAX_CANDIDATE_IMAGE_BYTES
            || self.image_volume_serial == 0
            || self.image_file_id.iter().all(|byte| *byte == 0)
            || self.image_link_count != 1
            || self.image_attributes == 0
            || self.image_attributes
                & (FILE_ATTRIBUTE_DIRECTORY_VALUE | FILE_ATTRIBUTE_REPARSE_POINT_VALUE)
                != 0
            || is_zero_digest(&self.full_readback_receipt_sha256)
            || self.full_readback_receipt_sha256 != candidate_image_evidence_digest(self)
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_image_evidence_invalid",
            ));
        }
        Ok(())
    }

    pub(crate) fn image_sha256(&self) -> &[u8; 32] {
        &self.image_sha256
    }

    pub(crate) fn image_byte_length(&self) -> u64 {
        self.image_byte_length
    }

    pub(crate) fn image_volume_serial(&self) -> u64 {
        self.image_volume_serial
    }

    pub(crate) fn image_file_id(&self) -> &[u8; 16] {
        &self.image_file_id
    }

    pub(crate) fn image_link_count(&self) -> u32 {
        self.image_link_count
    }

    pub(crate) fn image_attributes(&self) -> u32 {
        self.image_attributes
    }

    pub(crate) fn full_readback_receipt_sha256(&self) -> &[u8; 32] {
        &self.full_readback_receipt_sha256
    }
}

/// START_PENDING process evidence generated only after the service starts.
/// The embedded image must equal the Prepared static image binding.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct CandidateProcessEvidence {
    process_id: u32,
    process_creation_time: u64,
    image: CandidateImageEvidence,
    full_readback_receipt_sha256: [u8; 32],
}

impl CandidateProcessEvidence {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn from_held_process(
        process_id: u32,
        process_creation_time: u64,
        image_sha256: [u8; 32],
        image_byte_length: u64,
        image_volume_serial: u64,
        image_file_id: [u8; 16],
        image_link_count: u32,
        image_attributes: u32,
    ) -> Result<Self, AuthorityBootstrapError> {
        Self::from_static_image(
            process_id,
            process_creation_time,
            CandidateImageEvidence::from_held_image(
                image_sha256,
                image_byte_length,
                image_volume_serial,
                image_file_id,
                image_link_count,
                image_attributes,
            )?,
        )
    }

    pub(super) fn from_static_image(
        process_id: u32,
        process_creation_time: u64,
        image: CandidateImageEvidence,
    ) -> Result<Self, AuthorityBootstrapError> {
        let mut value = Self {
            process_id,
            process_creation_time,
            image,
            full_readback_receipt_sha256: [0; 32],
        };
        value.full_readback_receipt_sha256 = candidate_process_evidence_digest(&value);
        value.validate()?;
        Ok(value)
    }

    pub(crate) fn validate(&self) -> Result<(), AuthorityBootstrapError> {
        self.image.validate()?;
        if self.process_id == 0
            || self.process_creation_time == 0
            || is_zero_digest(&self.full_readback_receipt_sha256)
            || self.full_readback_receipt_sha256 != candidate_process_evidence_digest(self)
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_process_evidence_invalid",
            ));
        }
        Ok(())
    }

    pub(crate) fn process_id(&self) -> u32 {
        self.process_id
    }

    pub(crate) fn process_creation_time(&self) -> u64 {
        self.process_creation_time
    }

    pub(crate) fn image(&self) -> &CandidateImageEvidence {
        &self.image
    }

    pub(crate) fn image_sha256(&self) -> &[u8; 32] {
        self.image.image_sha256()
    }

    pub(crate) fn image_byte_length(&self) -> u64 {
        self.image.image_byte_length()
    }

    pub(crate) fn image_volume_serial(&self) -> u64 {
        self.image.image_volume_serial()
    }

    pub(crate) fn image_file_id(&self) -> &[u8; 16] {
        self.image.image_file_id()
    }

    pub(crate) fn image_link_count(&self) -> u32 {
        self.image.image_link_count()
    }

    pub(crate) fn image_attributes(&self) -> u32 {
        self.image.image_attributes()
    }

    pub(crate) fn full_readback_receipt_sha256(&self) -> &[u8; 32] {
        &self.full_readback_receipt_sha256
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct CandidateIssuerBinding {
    capsule_sha256: [u8; 32],
    transaction_started_receipt_sha256: [u8; 32],
    worker_started_receipt_sha256: [u8; 32],
    maintenance_worker: CandidateProcessEvidence,
    nonce_consumption_receipt_sha256: [u8; 32],
    nonce_consumption_full_readback_sha256: [u8; 32],
    nonce_consumption_file_sha256: [u8; 32],
    nonce_consumption_file_volume_serial: u64,
    nonce_consumption_file_id: [u8; 16],
}

impl CandidateIssuerBinding {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        capsule_sha256: [u8; 32],
        transaction_started_receipt_sha256: [u8; 32],
        worker_started_receipt_sha256: [u8; 32],
        maintenance_worker: CandidateProcessEvidence,
        nonce_consumption_receipt_sha256: [u8; 32],
        nonce_consumption_full_readback_sha256: [u8; 32],
        nonce_consumption_file_sha256: [u8; 32],
        nonce_consumption_file_volume_serial: u64,
        nonce_consumption_file_id: [u8; 16],
    ) -> Result<Self, AuthorityBootstrapError> {
        let value = Self {
            capsule_sha256,
            transaction_started_receipt_sha256,
            worker_started_receipt_sha256,
            maintenance_worker,
            nonce_consumption_receipt_sha256,
            nonce_consumption_full_readback_sha256,
            nonce_consumption_file_sha256,
            nonce_consumption_file_volume_serial,
            nonce_consumption_file_id,
        };
        value.validate()?;
        Ok(value)
    }

    pub(crate) fn capsule_sha256(&self) -> &[u8; 32] {
        &self.capsule_sha256
    }

    pub(crate) fn transaction_started_receipt_sha256(&self) -> &[u8; 32] {
        &self.transaction_started_receipt_sha256
    }

    pub(crate) fn worker_started_receipt_sha256(&self) -> &[u8; 32] {
        &self.worker_started_receipt_sha256
    }

    pub(crate) fn maintenance_worker(&self) -> &CandidateProcessEvidence {
        &self.maintenance_worker
    }

    pub(crate) fn nonce_consumption_receipt_sha256(&self) -> &[u8; 32] {
        &self.nonce_consumption_receipt_sha256
    }

    pub(crate) fn nonce_consumption_full_readback_sha256(&self) -> &[u8; 32] {
        &self.nonce_consumption_full_readback_sha256
    }

    pub(crate) fn nonce_consumption_file_sha256(&self) -> &[u8; 32] {
        &self.nonce_consumption_file_sha256
    }

    pub(crate) fn nonce_consumption_file_volume_serial(&self) -> u64 {
        self.nonce_consumption_file_volume_serial
    }

    pub(crate) fn nonce_consumption_file_id(&self) -> &[u8; 16] {
        &self.nonce_consumption_file_id
    }

    fn validate(&self) -> Result<(), AuthorityBootstrapError> {
        self.maintenance_worker.validate()?;
        if self.nonce_consumption_file_volume_serial == 0
            || self.nonce_consumption_file_id.iter().all(|byte| *byte == 0)
            || [
                self.capsule_sha256,
                self.transaction_started_receipt_sha256,
                self.worker_started_receipt_sha256,
                self.nonce_consumption_receipt_sha256,
                self.nonce_consumption_full_readback_sha256,
                self.nonce_consumption_file_sha256,
            ]
            .iter()
            .any(is_zero_digest)
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_issuer_binding_invalid",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct CandidateActivationObservation {
    generation: [u8; 32],
    plan_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
    activation_epoch: u64,
    active_head_sha256: [u8; 32],
    trust_manifest_sha256: [u8; 32],
    activation_manifest_sha256: [u8; 32],
    ledger_identity: [u8; 32],
    candidate_service: CandidateProcessEvidence,
    issuer: CandidateIssuerBinding,
}

impl CandidateActivationObservation {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new_with_issuer(
        generation: [u8; 32],
        plan_sha256: [u8; 32],
        transaction_sha256: [u8; 32],
        activation_epoch: u64,
        active_head_sha256: [u8; 32],
        trust_manifest_sha256: [u8; 32],
        activation_manifest_sha256: [u8; 32],
        ledger_identity: [u8; 32],
        candidate_service: CandidateProcessEvidence,
        issuer: CandidateIssuerBinding,
    ) -> Result<Self, AuthorityBootstrapError> {
        let value = Self {
            generation,
            plan_sha256,
            transaction_sha256,
            activation_epoch,
            active_head_sha256,
            trust_manifest_sha256,
            activation_manifest_sha256,
            ledger_identity,
            candidate_service,
            issuer,
        };
        value.validate()?;
        Ok(value)
    }

    pub(crate) fn transaction_sha256(&self) -> &[u8; 32] {
        &self.transaction_sha256
    }

    #[cfg(test)]
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        generation: [u8; 32],
        plan_sha256: [u8; 32],
        transaction_sha256: [u8; 32],
        activation_epoch: u64,
        active_head_sha256: [u8; 32],
        trust_manifest_sha256: [u8; 32],
        activation_manifest_sha256: [u8; 32],
        ledger_identity: [u8; 32],
        service_image_sha256: [u8; 32],
        service_process_id: u32,
        service_process_creation_time: u64,
    ) -> Result<Self, AuthorityBootstrapError> {
        let candidate_service = CandidateProcessEvidence::from_held_process(
            service_process_id,
            service_process_creation_time,
            service_image_sha256,
            0x2929,
            0x3030,
            [0x31; 16],
            1,
            0x20,
        )?;
        let maintenance_worker = CandidateProcessEvidence::from_held_process(
            0x2828,
            0x2929_3030,
            [0x2a; 32],
            0x2b2b,
            0x2c2c,
            [0x2d; 16],
            1,
            0x20,
        )?;
        Self::new_with_issuer(
            generation,
            plan_sha256,
            transaction_sha256,
            activation_epoch,
            active_head_sha256,
            trust_manifest_sha256,
            activation_manifest_sha256,
            ledger_identity,
            candidate_service,
            CandidateIssuerBinding::new(
                [0x21; 32],
                [0x22; 32],
                [0x23; 32],
                maintenance_worker,
                [0x24; 32],
                [0x25; 32],
                [0x26; 32],
                0x2727,
                [0x28; 16],
            )?,
        )
    }

    fn validate(&self) -> Result<(), AuthorityBootstrapError> {
        self.issuer.validate()?;
        self.candidate_service.validate()?;
        if self.activation_epoch == 0
            || [
                self.generation,
                self.plan_sha256,
                self.transaction_sha256,
                self.active_head_sha256,
                self.trust_manifest_sha256,
                self.activation_manifest_sha256,
                self.ledger_identity,
            ]
            .iter()
            .any(is_zero_digest)
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_observation_invalid",
            ));
        }
        Ok(())
    }

    fn matches(
        &self,
        binding: &CandidateActivationBinding,
        candidate_service: &CandidateProcessEvidence,
    ) -> bool {
        self.generation == binding.generation
            && self.plan_sha256 == binding.plan_sha256
            && self.transaction_sha256 == binding.transaction_sha256
            && self.activation_epoch == binding.activation_epoch
            && self.active_head_sha256 == binding.active_head_sha256
            && self.trust_manifest_sha256 == binding.trust_manifest_sha256
            && self.activation_manifest_sha256 == binding.activation_manifest_sha256
            && self.ledger_identity == binding.ledger_identity
            && self.candidate_service == *candidate_service
            && self.candidate_service.image == binding.target_service_image
            && self.issuer == binding.issuer
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct CandidateActivationBinding {
    generation: [u8; 32],
    plan_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
    activation_epoch: u64,
    active_head_sha256: [u8; 32],
    trust_manifest_sha256: [u8; 32],
    activation_manifest_sha256: [u8; 32],
    ledger_identity: [u8; 32],
    target_service_image: CandidateImageEvidence,
    issuer: CandidateIssuerBinding,
    nonce: [u8; 32],
    issued_at_unix_millis: u64,
    expires_at_unix_millis: u64,
}

impl CandidateActivationBinding {
    #[cfg(test)]
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        observation: CandidateActivationObservation,
        nonce: [u8; 32],
        issued_at_unix_millis: u64,
        expires_at_unix_millis: u64,
    ) -> Result<Self, AuthorityBootstrapError> {
        observation.validate()?;
        Self::new_static(
            observation.generation,
            observation.plan_sha256,
            observation.transaction_sha256,
            observation.activation_epoch,
            observation.active_head_sha256,
            observation.trust_manifest_sha256,
            observation.activation_manifest_sha256,
            observation.ledger_identity,
            *observation.candidate_service.image(),
            observation.issuer,
            nonce,
            issued_at_unix_millis,
            expires_at_unix_millis,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new_static(
        generation: [u8; 32],
        plan_sha256: [u8; 32],
        transaction_sha256: [u8; 32],
        activation_epoch: u64,
        active_head_sha256: [u8; 32],
        trust_manifest_sha256: [u8; 32],
        activation_manifest_sha256: [u8; 32],
        ledger_identity: [u8; 32],
        target_service_image: CandidateImageEvidence,
        issuer: CandidateIssuerBinding,
        nonce: [u8; 32],
        issued_at_unix_millis: u64,
        expires_at_unix_millis: u64,
    ) -> Result<Self, AuthorityBootstrapError> {
        let value = Self {
            generation,
            plan_sha256,
            transaction_sha256,
            activation_epoch,
            active_head_sha256,
            trust_manifest_sha256,
            activation_manifest_sha256,
            ledger_identity,
            target_service_image,
            issuer,
            nonce,
            issued_at_unix_millis,
            expires_at_unix_millis,
        };
        value.validate_shape()?;
        Ok(value)
    }

    pub(crate) fn generation(&self) -> &[u8; 32] {
        &self.generation
    }

    pub(crate) fn plan_sha256(&self) -> &[u8; 32] {
        &self.plan_sha256
    }

    pub(crate) fn transaction_sha256(&self) -> &[u8; 32] {
        &self.transaction_sha256
    }

    pub(crate) fn activation_epoch(&self) -> u64 {
        self.activation_epoch
    }

    pub(crate) fn active_head_sha256(&self) -> &[u8; 32] {
        &self.active_head_sha256
    }

    pub(crate) fn trust_manifest_sha256(&self) -> &[u8; 32] {
        &self.trust_manifest_sha256
    }

    pub(crate) fn activation_manifest_sha256(&self) -> &[u8; 32] {
        &self.activation_manifest_sha256
    }

    pub(crate) fn ledger_identity(&self) -> &[u8; 32] {
        &self.ledger_identity
    }

    pub(crate) fn service_image_sha256(&self) -> &[u8; 32] {
        self.target_service_image.image_sha256()
    }

    pub(crate) fn target_service_image(&self) -> &CandidateImageEvidence {
        &self.target_service_image
    }

    pub(crate) fn issuer(&self) -> CandidateIssuerBinding {
        self.issuer
    }

    pub(crate) fn nonce(&self) -> &[u8; 32] {
        &self.nonce
    }

    pub(crate) fn issued_at_unix_millis(&self) -> u64 {
        self.issued_at_unix_millis
    }

    pub(crate) fn expires_at_unix_millis(&self) -> u64 {
        self.expires_at_unix_millis
    }

    pub(crate) fn credential_sha256(&self) -> [u8; 32] {
        candidate_credential_digest(self)
    }

    pub(crate) fn pipe_instance_id(&self) -> [u8; 16] {
        candidate_pipe_instance_id(self)
    }

    fn validate_shape(&self) -> Result<(), AuthorityBootstrapError> {
        self.issuer.validate()?;
        self.target_service_image.validate()?;
        if self.activation_epoch == 0
            || [
                self.generation,
                self.plan_sha256,
                self.transaction_sha256,
                self.active_head_sha256,
                self.trust_manifest_sha256,
                self.activation_manifest_sha256,
                self.ledger_identity,
            ]
            .iter()
            .any(is_zero_digest)
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_static_binding_invalid",
            ));
        }
        let lifetime = self
            .expires_at_unix_millis
            .checked_sub(self.issued_at_unix_millis)
            .ok_or(AuthorityBootstrapError(
                "authority_candidate_credential_lifetime_invalid",
            ))?;
        if self.issued_at_unix_millis == 0
            || lifetime == 0
            || lifetime > MAX_CANDIDATE_CREDENTIAL_LIFETIME_MILLIS
            || is_zero_digest(&self.nonce)
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_lifetime_invalid",
            ));
        }
        Ok(())
    }

    fn validate_time(&self, now_unix_millis: u64) -> Result<(), AuthorityBootstrapError> {
        self.validate_shape()?;
        if now_unix_millis < self.issued_at_unix_millis {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_not_yet_valid",
            ));
        }
        if now_unix_millis > self.expires_at_unix_millis {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_expired",
            ));
        }
        Ok(())
    }
}

/// Ephemeral SCM start locator for one candidate-validation invocation. It is
/// never part of the persistent ImagePath and deliberately has no `Debug` or
/// serialization implementation because it contains the one-use nonce.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) struct CandidateServiceStartLocator {
    transaction_sha256: [u8; 32],
    capsule_sha256: [u8; 32],
    nonce: [u8; 32],
    credential_sha256: [u8; 32],
}

impl CandidateServiceStartLocator {
    pub(crate) fn from_binding(binding: CandidateActivationBinding) -> Self {
        Self {
            transaction_sha256: *binding.transaction_sha256(),
            capsule_sha256: *binding.issuer().capsule_sha256(),
            nonce: *binding.nonce(),
            credential_sha256: binding.credential_sha256(),
        }
    }

    pub(crate) fn parse_ordered(arguments: &[&str]) -> Result<Self, AuthorityBootstrapError> {
        let [mode, transaction, capsule, nonce, credential] = arguments else {
            return Err(AuthorityBootstrapError(
                "authority_candidate_start_locator_invalid",
            ));
        };
        if *mode != CANDIDATE_SERVICE_START_MODE_TOKEN {
            return Err(AuthorityBootstrapError(
                "authority_candidate_start_locator_invalid",
            ));
        }
        let value = Self {
            transaction_sha256: decode_start_locator_digest(
                transaction,
                CANDIDATE_SERVICE_START_TRANSACTION_PREFIX,
            )?,
            capsule_sha256: decode_start_locator_digest(
                capsule,
                CANDIDATE_SERVICE_START_CAPSULE_PREFIX,
            )?,
            nonce: decode_start_locator_digest(nonce, CANDIDATE_SERVICE_START_NONCE_PREFIX)?,
            credential_sha256: decode_start_locator_digest(
                credential,
                CANDIDATE_SERVICE_START_CREDENTIAL_PREFIX,
            )?,
        };
        Ok(value)
    }

    pub(crate) fn ordered_service_arguments(self) -> [String; 5] {
        [
            CANDIDATE_SERVICE_START_MODE_TOKEN.to_string(),
            format!(
                "{CANDIDATE_SERVICE_START_TRANSACTION_PREFIX}{}",
                hex_lower(&self.transaction_sha256)
            ),
            format!(
                "{CANDIDATE_SERVICE_START_CAPSULE_PREFIX}{}",
                hex_lower(&self.capsule_sha256)
            ),
            format!(
                "{CANDIDATE_SERVICE_START_NONCE_PREFIX}{}",
                hex_lower(&self.nonce)
            ),
            format!(
                "{CANDIDATE_SERVICE_START_CREDENTIAL_PREFIX}{}",
                hex_lower(&self.credential_sha256)
            ),
        ]
    }

    pub(crate) fn validate_binding(
        self,
        binding: CandidateActivationBinding,
    ) -> Result<(), AuthorityBootstrapError> {
        if self.transaction_sha256 != *binding.transaction_sha256()
            || self.capsule_sha256 != *binding.issuer().capsule_sha256()
            || self.nonce != *binding.nonce()
            || self.credential_sha256 != binding.credential_sha256()
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_start_locator_binding_mismatch",
            ));
        }
        Ok(())
    }

    pub(crate) fn transaction_sha256(self) -> [u8; 32] {
        self.transaction_sha256
    }

    pub(crate) fn capsule_sha256(self) -> [u8; 32] {
        self.capsule_sha256
    }

    pub(crate) fn credential_sha256(self) -> [u8; 32] {
        self.credential_sha256
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) enum CandidateCredentialPhase {
    Prepared,
    Armed,
    Consumed,
    Committed,
}

impl CandidateCredentialPhase {
    fn digest_tag(self) -> u8 {
        match self {
            Self::Prepared => 1,
            Self::Armed => 2,
            Self::Consumed => 3,
            Self::Committed => 4,
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct CandidateConsumptionEvidence {
    credential_sha256: [u8; 32],
    armed_receipt_sha256: [u8; 32],
    pipe_instance_id: [u8; 16],
    request_sha256: [u8; 32],
    client_peer: CandidateProcessEvidence,
    receipt_sha256: [u8; 32],
}

impl CandidateConsumptionEvidence {
    fn new(
        binding: &CandidateActivationBinding,
        armed_receipt_sha256: [u8; 32],
        request: &CandidateValidationRequest,
        client_peer: CandidateProcessEvidence,
    ) -> Result<Self, AuthorityBootstrapError> {
        if is_zero_digest(&armed_receipt_sha256) {
            return Err(AuthorityBootstrapError(
                "authority_candidate_armed_receipt_invalid",
            ));
        }
        client_peer.validate()?;
        if client_peer != *binding.issuer.maintenance_worker()
            || request.credential_sha256 != binding.credential_sha256()
            || request.nonce != binding.nonce
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_client_peer_binding_mismatch",
            ));
        }
        let mut value = Self {
            credential_sha256: binding.credential_sha256(),
            armed_receipt_sha256,
            pipe_instance_id: candidate_pipe_instance_id(binding),
            request_sha256: candidate_handshake_request_digest(request),
            client_peer,
            receipt_sha256: [0; 32],
        };
        value.receipt_sha256 = candidate_consumption_receipt_digest(&value);
        value.validate(binding, armed_receipt_sha256)?;
        Ok(value)
    }

    fn validate(
        &self,
        binding: &CandidateActivationBinding,
        armed_receipt_sha256: [u8; 32],
    ) -> Result<(), AuthorityBootstrapError> {
        self.client_peer.validate()?;
        if is_zero_digest(&armed_receipt_sha256)
            || self.credential_sha256 != binding.credential_sha256()
            || self.armed_receipt_sha256 != armed_receipt_sha256
            || self.pipe_instance_id != candidate_pipe_instance_id(binding)
            || is_zero_digest(&self.request_sha256)
            || self.client_peer != *binding.issuer.maintenance_worker()
            || is_zero_digest(&self.receipt_sha256)
            || self.receipt_sha256 != candidate_consumption_receipt_digest(self)
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_consumption_evidence_invalid",
            ));
        }
        Ok(())
    }

    pub(crate) fn request_sha256(&self) -> &[u8; 32] {
        &self.request_sha256
    }

    pub(crate) fn client_peer(&self) -> &CandidateProcessEvidence {
        &self.client_peer
    }

    pub(crate) fn receipt_sha256(&self) -> &[u8; 32] {
        &self.receipt_sha256
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct CandidateCredentialRecord {
    schema: String,
    phase: CandidateCredentialPhase,
    generation: String,
    plan_sha256: String,
    transaction_sha256: String,
    activation_epoch: u64,
    active_head_sha256: String,
    trust_manifest_sha256: String,
    activation_manifest_sha256: String,
    ledger_identity: String,
    target_service_image_sha256: String,
    target_service_image_byte_length: u64,
    target_service_image_volume_serial: u64,
    target_service_image_file_id: String,
    target_service_image_link_count: u32,
    target_service_image_attributes: u32,
    target_service_full_readback_receipt_sha256: String,
    candidate_service: Option<CandidateProcessEvidence>,
    capsule_sha256: String,
    transaction_started_receipt_sha256: String,
    worker_started_receipt_sha256: String,
    maintenance_worker_process_id: u32,
    maintenance_worker_process_creation_time: u64,
    maintenance_worker_image_sha256: String,
    maintenance_worker_image_byte_length: u64,
    maintenance_worker_image_volume_serial: u64,
    maintenance_worker_image_file_id: String,
    maintenance_worker_image_link_count: u32,
    maintenance_worker_image_attributes: u32,
    maintenance_worker_image_full_readback_receipt_sha256: String,
    maintenance_worker_full_readback_receipt_sha256: String,
    nonce_consumption_receipt_sha256: String,
    nonce_consumption_full_readback_sha256: String,
    nonce_consumption_file_sha256: String,
    nonce_consumption_file_volume_serial: u64,
    nonce_consumption_file_id: String,
    nonce: String,
    issued_at_unix_millis: u64,
    expires_at_unix_millis: u64,
    credential_sha256: String,
    armed_receipt_sha256: Option<String>,
    consumption: Option<CandidateConsumptionEvidence>,
    record_sha256: String,
}

impl CandidateCredentialRecord {
    pub(crate) fn prepared(
        binding: CandidateActivationBinding,
    ) -> Result<Self, AuthorityBootstrapError> {
        Self::from_binding(
            binding,
            CandidateCredentialPhase::Prepared,
            None,
            None,
            None,
        )
    }

    #[cfg(test)]
    pub(crate) fn arm(&self) -> Result<Self, AuthorityBootstrapError> {
        let binding = self.binding()?;
        self.arm_with_receipt(
            [0xa1; 32],
            CandidateProcessEvidence::from_static_image(
                919,
                42_424,
                *binding.target_service_image(),
            )?,
        )
    }

    pub(crate) fn arm_with_receipt(
        &self,
        armed_receipt_sha256: [u8; 32],
        candidate_service: CandidateProcessEvidence,
    ) -> Result<Self, AuthorityBootstrapError> {
        if self.phase != CandidateCredentialPhase::Prepared {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_transition_invalid",
            ));
        }
        if is_zero_digest(&armed_receipt_sha256) {
            return Err(AuthorityBootstrapError(
                "authority_candidate_armed_receipt_invalid",
            ));
        }
        candidate_service.validate()?;
        let binding = self.binding()?;
        if candidate_service.image() != binding.target_service_image() {
            return Err(AuthorityBootstrapError(
                "authority_candidate_service_image_binding_mismatch",
            ));
        }
        Self::from_binding(
            binding,
            CandidateCredentialPhase::Armed,
            Some(armed_receipt_sha256),
            Some(candidate_service),
            None,
        )
    }

    #[cfg(test)]
    pub(crate) fn consume(&self) -> Result<Self, AuthorityBootstrapError> {
        let binding = self.binding()?;
        let request =
            CandidateValidationRequest::new(binding.credential_sha256(), *binding.nonce())?;
        self.consume_with_peer(&request, *binding.issuer().maintenance_worker())
    }

    pub(crate) fn consume_with_peer(
        &self,
        request: &CandidateValidationRequest,
        client_peer: CandidateProcessEvidence,
    ) -> Result<Self, AuthorityBootstrapError> {
        if self.phase != CandidateCredentialPhase::Armed {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_transition_invalid",
            ));
        }
        let binding = self.binding()?;
        let armed_receipt_sha256 = self.armed_receipt_sha256()?;
        let consumption = CandidateConsumptionEvidence::new(
            &binding,
            armed_receipt_sha256,
            request,
            client_peer,
        )?;
        Self::from_binding(
            binding,
            CandidateCredentialPhase::Consumed,
            Some(armed_receipt_sha256),
            self.candidate_service,
            Some(consumption),
        )
    }

    pub(crate) fn commit(&self) -> Result<Self, AuthorityBootstrapError> {
        if self.phase != CandidateCredentialPhase::Consumed {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_transition_invalid",
            ));
        }
        Self::from_binding(
            self.binding()?,
            CandidateCredentialPhase::Committed,
            Some(self.armed_receipt_sha256()?),
            self.candidate_service,
            self.consumption,
        )
    }

    pub(crate) fn parse_canonical(bytes: &[u8]) -> Result<Self, AuthorityBootstrapError> {
        if bytes.is_empty() || bytes.len() as u64 > MAX_CANDIDATE_CREDENTIAL_BYTES {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_size_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes).map_err(|_| {
            AuthorityBootstrapError("authority_candidate_credential_serialization_invalid")
        })?;
        if serde_json::to_vec(&value).ok().as_deref() != Some(bytes) {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_noncanonical",
            ));
        }
        value.binding()?;
        Ok(value)
    }

    pub(crate) fn canonical_bytes(&self) -> Result<Vec<u8>, AuthorityBootstrapError> {
        self.binding()?;
        serde_json::to_vec(self).map_err(|_| {
            AuthorityBootstrapError("authority_candidate_credential_serialization_invalid")
        })
    }

    pub(crate) fn phase(&self) -> CandidateCredentialPhase {
        self.phase
    }

    pub(crate) fn credential_sha256(&self) -> Result<[u8; 32], AuthorityBootstrapError> {
        decode_digest(&self.credential_sha256)
    }

    pub(crate) fn record_sha256(&self) -> Result<[u8; 32], AuthorityBootstrapError> {
        decode_digest(&self.record_sha256)
    }

    pub(crate) fn armed_receipt_sha256(&self) -> Result<[u8; 32], AuthorityBootstrapError> {
        self.armed_receipt_sha256
            .as_deref()
            .ok_or(AuthorityBootstrapError(
                "authority_candidate_armed_receipt_missing",
            ))
            .and_then(decode_digest)
    }

    pub(crate) fn consumption(&self) -> Option<&CandidateConsumptionEvidence> {
        self.consumption.as_ref()
    }

    pub(crate) fn candidate_service(&self) -> Option<&CandidateProcessEvidence> {
        self.candidate_service.as_ref()
    }

    pub(crate) fn binding(&self) -> Result<CandidateActivationBinding, AuthorityBootstrapError> {
        if self.schema != CANDIDATE_ACTIVATION_CREDENTIAL_SCHEMA {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_schema_invalid",
            ));
        }
        let binding = CandidateActivationBinding {
            generation: decode_digest(&self.generation)?,
            plan_sha256: decode_digest(&self.plan_sha256)?,
            transaction_sha256: decode_digest(&self.transaction_sha256)?,
            activation_epoch: self.activation_epoch,
            active_head_sha256: decode_digest(&self.active_head_sha256)?,
            trust_manifest_sha256: decode_digest(&self.trust_manifest_sha256)?,
            activation_manifest_sha256: decode_digest(&self.activation_manifest_sha256)?,
            ledger_identity: decode_digest(&self.ledger_identity)?,
            target_service_image: CandidateImageEvidence {
                image_sha256: decode_digest(&self.target_service_image_sha256)?,
                image_byte_length: self.target_service_image_byte_length,
                image_volume_serial: self.target_service_image_volume_serial,
                image_file_id: decode_fixed_hex::<16>(&self.target_service_image_file_id)?,
                image_link_count: self.target_service_image_link_count,
                image_attributes: self.target_service_image_attributes,
                full_readback_receipt_sha256: decode_digest(
                    &self.target_service_full_readback_receipt_sha256,
                )?,
            },
            issuer: CandidateIssuerBinding::new(
                decode_digest(&self.capsule_sha256)?,
                decode_digest(&self.transaction_started_receipt_sha256)?,
                decode_digest(&self.worker_started_receipt_sha256)?,
                CandidateProcessEvidence {
                    process_id: self.maintenance_worker_process_id,
                    process_creation_time: self.maintenance_worker_process_creation_time,
                    image: CandidateImageEvidence {
                        image_sha256: decode_digest(&self.maintenance_worker_image_sha256)?,
                        image_byte_length: self.maintenance_worker_image_byte_length,
                        image_volume_serial: self.maintenance_worker_image_volume_serial,
                        image_file_id: decode_fixed_hex::<16>(
                            &self.maintenance_worker_image_file_id,
                        )?,
                        image_link_count: self.maintenance_worker_image_link_count,
                        image_attributes: self.maintenance_worker_image_attributes,
                        full_readback_receipt_sha256: decode_digest(
                            &self.maintenance_worker_image_full_readback_receipt_sha256,
                        )?,
                    },
                    full_readback_receipt_sha256: decode_digest(
                        &self.maintenance_worker_full_readback_receipt_sha256,
                    )?,
                },
                decode_digest(&self.nonce_consumption_receipt_sha256)?,
                decode_digest(&self.nonce_consumption_full_readback_sha256)?,
                decode_digest(&self.nonce_consumption_file_sha256)?,
                self.nonce_consumption_file_volume_serial,
                decode_fixed_hex::<16>(&self.nonce_consumption_file_id)?,
            )?,
            nonce: decode_digest(&self.nonce)?,
            issued_at_unix_millis: self.issued_at_unix_millis,
            expires_at_unix_millis: self.expires_at_unix_millis,
        };
        binding.validate_shape()?;
        let credential_sha256 = decode_digest(&self.credential_sha256)?;
        if credential_sha256 != binding.credential_sha256() {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_digest_mismatch",
            ));
        }
        let armed_receipt_sha256 = match self.phase {
            CandidateCredentialPhase::Prepared => {
                if self.armed_receipt_sha256.is_some()
                    || self.candidate_service.is_some()
                    || self.consumption.is_some()
                {
                    return Err(AuthorityBootstrapError(
                        "authority_candidate_record_phase_binding_invalid",
                    ));
                }
                None
            }
            CandidateCredentialPhase::Armed => {
                let candidate_service = self.candidate_service.ok_or(AuthorityBootstrapError(
                    "authority_candidate_service_process_missing",
                ))?;
                candidate_service.validate()?;
                if candidate_service.image() != binding.target_service_image()
                    || self.consumption.is_some()
                {
                    return Err(AuthorityBootstrapError(
                        "authority_candidate_record_phase_binding_invalid",
                    ));
                }
                Some(self.armed_receipt_sha256()?)
            }
            CandidateCredentialPhase::Consumed | CandidateCredentialPhase::Committed => {
                let armed = self.armed_receipt_sha256()?;
                let candidate_service = self.candidate_service.ok_or(AuthorityBootstrapError(
                    "authority_candidate_service_process_missing",
                ))?;
                candidate_service.validate()?;
                if candidate_service.image() != binding.target_service_image() {
                    return Err(AuthorityBootstrapError(
                        "authority_candidate_service_image_binding_mismatch",
                    ));
                }
                self.consumption
                    .as_ref()
                    .ok_or(AuthorityBootstrapError(
                        "authority_candidate_consumption_evidence_missing",
                    ))?
                    .validate(&binding, armed)?;
                Some(armed)
            }
        };
        let record_sha256 = decode_digest(&self.record_sha256)?;
        if record_sha256
            != candidate_record_digest(
                self.phase,
                &credential_sha256,
                armed_receipt_sha256.as_ref(),
                self.candidate_service.as_ref(),
                self.consumption.as_ref(),
            )
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_record_digest_mismatch",
            ));
        }
        Ok(binding)
    }

    fn from_binding(
        binding: CandidateActivationBinding,
        phase: CandidateCredentialPhase,
        armed_receipt_sha256: Option<[u8; 32]>,
        candidate_service: Option<CandidateProcessEvidence>,
        consumption: Option<CandidateConsumptionEvidence>,
    ) -> Result<Self, AuthorityBootstrapError> {
        binding.validate_shape()?;
        match phase {
            CandidateCredentialPhase::Prepared
                if armed_receipt_sha256.is_some()
                    || candidate_service.is_some()
                    || consumption.is_some() =>
            {
                return Err(AuthorityBootstrapError(
                    "authority_candidate_record_phase_binding_invalid",
                ))
            }
            CandidateCredentialPhase::Armed
                if armed_receipt_sha256.is_none()
                    || candidate_service.is_none()
                    || consumption.is_some() =>
            {
                return Err(AuthorityBootstrapError(
                    "authority_candidate_record_phase_binding_invalid",
                ))
            }
            CandidateCredentialPhase::Consumed | CandidateCredentialPhase::Committed => {
                let armed = armed_receipt_sha256.ok_or(AuthorityBootstrapError(
                    "authority_candidate_armed_receipt_missing",
                ))?;
                consumption
                    .as_ref()
                    .ok_or(AuthorityBootstrapError(
                        "authority_candidate_consumption_evidence_missing",
                    ))?
                    .validate(&binding, armed)?;
                let service = candidate_service.ok_or(AuthorityBootstrapError(
                    "authority_candidate_service_process_missing",
                ))?;
                service.validate()?;
                if service.image() != binding.target_service_image() {
                    return Err(AuthorityBootstrapError(
                        "authority_candidate_service_image_binding_mismatch",
                    ));
                }
            }
            _ => {}
        }
        let credential_sha256 = binding.credential_sha256();
        let record_sha256 = candidate_record_digest(
            phase,
            &credential_sha256,
            armed_receipt_sha256.as_ref(),
            candidate_service.as_ref(),
            consumption.as_ref(),
        );
        if let Some(service) = candidate_service {
            service.validate()?;
            if service.image() != binding.target_service_image() {
                return Err(AuthorityBootstrapError(
                    "authority_candidate_service_image_binding_mismatch",
                ));
            }
        }
        let target_service_image = binding.target_service_image;
        let maintenance_worker = binding.issuer.maintenance_worker;
        Ok(Self {
            schema: CANDIDATE_ACTIVATION_CREDENTIAL_SCHEMA.to_string(),
            phase,
            generation: hex_lower(&binding.generation),
            plan_sha256: hex_lower(&binding.plan_sha256),
            transaction_sha256: hex_lower(&binding.transaction_sha256),
            activation_epoch: binding.activation_epoch,
            active_head_sha256: hex_lower(&binding.active_head_sha256),
            trust_manifest_sha256: hex_lower(&binding.trust_manifest_sha256),
            activation_manifest_sha256: hex_lower(&binding.activation_manifest_sha256),
            ledger_identity: hex_lower(&binding.ledger_identity),
            target_service_image_sha256: hex_lower(target_service_image.image_sha256()),
            target_service_image_byte_length: target_service_image.image_byte_length(),
            target_service_image_volume_serial: target_service_image.image_volume_serial(),
            target_service_image_file_id: hex_lower(target_service_image.image_file_id()),
            target_service_image_link_count: target_service_image.image_link_count(),
            target_service_image_attributes: target_service_image.image_attributes(),
            target_service_full_readback_receipt_sha256: hex_lower(
                target_service_image.full_readback_receipt_sha256(),
            ),
            candidate_service,
            capsule_sha256: hex_lower(&binding.issuer.capsule_sha256),
            transaction_started_receipt_sha256: hex_lower(
                &binding.issuer.transaction_started_receipt_sha256,
            ),
            worker_started_receipt_sha256: hex_lower(&binding.issuer.worker_started_receipt_sha256),
            maintenance_worker_process_id: maintenance_worker.process_id(),
            maintenance_worker_process_creation_time: maintenance_worker.process_creation_time(),
            maintenance_worker_image_sha256: hex_lower(maintenance_worker.image_sha256()),
            maintenance_worker_image_byte_length: maintenance_worker.image_byte_length(),
            maintenance_worker_image_volume_serial: maintenance_worker.image_volume_serial(),
            maintenance_worker_image_file_id: hex_lower(maintenance_worker.image_file_id()),
            maintenance_worker_image_link_count: maintenance_worker.image_link_count(),
            maintenance_worker_image_attributes: maintenance_worker.image_attributes(),
            maintenance_worker_image_full_readback_receipt_sha256: hex_lower(
                maintenance_worker.image().full_readback_receipt_sha256(),
            ),
            maintenance_worker_full_readback_receipt_sha256: hex_lower(
                maintenance_worker.full_readback_receipt_sha256(),
            ),
            nonce_consumption_receipt_sha256: hex_lower(
                &binding.issuer.nonce_consumption_receipt_sha256,
            ),
            nonce_consumption_full_readback_sha256: hex_lower(
                &binding.issuer.nonce_consumption_full_readback_sha256,
            ),
            nonce_consumption_file_sha256: hex_lower(&binding.issuer.nonce_consumption_file_sha256),
            nonce_consumption_file_volume_serial: binding
                .issuer
                .nonce_consumption_file_volume_serial,
            nonce_consumption_file_id: hex_lower(&binding.issuer.nonce_consumption_file_id),
            nonce: hex_lower(&binding.nonce),
            issued_at_unix_millis: binding.issued_at_unix_millis,
            expires_at_unix_millis: binding.expires_at_unix_millis,
            credential_sha256: hex_lower(&credential_sha256),
            armed_receipt_sha256: armed_receipt_sha256.map(|digest| hex_lower(&digest)),
            consumption,
            record_sha256: hex_lower(&record_sha256),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum CandidateCredentialReadback {
    None,
    Record {
        record: CandidateCredentialRecord,
        issuer: CandidateIssuerBinding,
        armed_receipt_sha256: [u8; 32],
    },
}

pub(crate) trait CandidateCredentialConsumer {
    fn read_candidate(
        &mut self,
        transaction_sha256: &[u8; 32],
    ) -> Result<CandidateCredentialReadback, AuthorityBootstrapError>;

    /// Must atomically compare the full Armed record and persist its exact
    /// Consumed successor before returning success.
    fn consume_armed(
        &mut self,
        expected: &CandidateCredentialRecord,
        request: &CandidateValidationRequest,
        client_peer: CandidateProcessEvidence,
    ) -> Result<CandidateCredentialRecord, AuthorityBootstrapError>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct CandidateValidationHandshake {
    credential_sha256: [u8; 32],
    generation: [u8; 32],
    plan_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
    activation_epoch: u64,
    service_process_id: u32,
    service_process_creation_time: u64,
    pipe_instance_id: [u8; 16],
    request_sha256: [u8; 32],
    receipt_sha256: [u8; 32],
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CandidateValidationHandshakeWire {
    schema: String,
    credential_sha256: String,
    generation: String,
    plan_sha256: String,
    transaction_sha256: String,
    activation_epoch: u64,
    service_process_id: u32,
    service_process_creation_time: u64,
    pipe_instance_id: String,
    request_sha256: String,
    receipt_sha256: String,
}

/// Parsed response bytes remain explicitly untrusted until they are checked
/// against the locally held credential, actual request transcript, SCM
/// process identity, service image, and pipe instance.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct UntrustedCandidateValidationResponse {
    wire: CandidateValidationHandshakeWire,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct CandidateServicePeerObservation {
    candidate_service: CandidateProcessEvidence,
    pipe_instance_id: [u8; 16],
}

impl CandidateServicePeerObservation {
    pub(crate) fn new(
        candidate_service: CandidateProcessEvidence,
        pipe_instance_id: [u8; 16],
    ) -> Result<Self, AuthorityBootstrapError> {
        candidate_service.validate()?;
        if pipe_instance_id.iter().all(|byte| *byte == 0) {
            return Err(AuthorityBootstrapError(
                "authority_candidate_peer_observation_invalid",
            ));
        }
        Ok(Self {
            candidate_service,
            pipe_instance_id,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct CandidateResponseExpectation {
    binding: CandidateActivationBinding,
    request: CandidateValidationRequest,
    candidate_service: CandidateProcessEvidence,
    peer: CandidateServicePeerObservation,
}

impl CandidateResponseExpectation {
    pub(crate) fn new(
        binding: CandidateActivationBinding,
        request: CandidateValidationRequest,
        candidate_service: CandidateProcessEvidence,
        peer: CandidateServicePeerObservation,
    ) -> Result<Self, AuthorityBootstrapError> {
        binding.validate_shape()?;
        if request.credential_sha256 != binding.credential_sha256()
            || request.nonce != binding.nonce
            || candidate_service.image() != binding.target_service_image()
            || peer.candidate_service != candidate_service
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_response_expectation_mismatch",
            ));
        }
        Ok(Self {
            binding,
            request,
            candidate_service,
            peer,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct VerifiedCandidateValidationReceipt(CandidateValidationHandshake);

impl VerifiedCandidateValidationReceipt {
    pub(crate) fn receipt_sha256(&self) -> &[u8; 32] {
        self.0.receipt_sha256()
    }

    pub(crate) fn request_sha256(&self) -> &[u8; 32] {
        self.0.request_sha256()
    }

    pub(crate) fn credential_sha256(&self) -> &[u8; 32] {
        self.0.credential_sha256()
    }
}

impl UntrustedCandidateValidationResponse {
    pub(crate) fn parse_canonical(bytes: &[u8]) -> Result<Self, AuthorityBootstrapError> {
        if bytes.is_empty() || bytes.len() > MAX_CANDIDATE_HANDSHAKE_BYTES {
            return Err(AuthorityBootstrapError(
                "authority_candidate_handshake_response_size_invalid",
            ));
        }
        let wire: CandidateValidationHandshakeWire =
            serde_json::from_slice(bytes).map_err(|_| {
                AuthorityBootstrapError("authority_candidate_handshake_response_invalid")
            })?;
        if serde_json::to_vec(&wire).ok().as_deref() != Some(bytes)
            || wire.schema != CANDIDATE_HANDSHAKE_RESPONSE_SCHEMA
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_handshake_response_noncanonical",
            ));
        }
        Ok(Self { wire })
    }

    pub(crate) fn verify_against(
        self,
        expectation: &CandidateResponseExpectation,
    ) -> Result<VerifiedCandidateValidationReceipt, AuthorityBootstrapError> {
        let expected_pipe_instance_id = candidate_pipe_instance_id(&expectation.binding);
        if expectation.peer.candidate_service != expectation.candidate_service
            || expectation.candidate_service.image() != expectation.binding.target_service_image()
            || expectation.peer.pipe_instance_id != expected_pipe_instance_id
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_handshake_peer_binding_mismatch",
            ));
        }
        let observed = CandidateValidationHandshake {
            credential_sha256: decode_digest(&self.wire.credential_sha256)?,
            generation: decode_digest(&self.wire.generation)?,
            plan_sha256: decode_digest(&self.wire.plan_sha256)?,
            transaction_sha256: decode_digest(&self.wire.transaction_sha256)?,
            activation_epoch: self.wire.activation_epoch,
            service_process_id: self.wire.service_process_id,
            service_process_creation_time: self.wire.service_process_creation_time,
            pipe_instance_id: decode_fixed_hex::<16>(&self.wire.pipe_instance_id)?,
            request_sha256: decode_digest(&self.wire.request_sha256)?,
            receipt_sha256: decode_digest(&self.wire.receipt_sha256)?,
        };
        let expected = CandidateValidationHandshake::from_binding(
            &expectation.binding,
            &expectation.request,
            &expectation.candidate_service,
        );
        if observed != expected {
            return Err(AuthorityBootstrapError(
                "authority_candidate_handshake_response_binding_mismatch",
            ));
        }
        Ok(VerifiedCandidateValidationReceipt(observed))
    }
}

impl CandidateValidationHandshake {
    pub(crate) fn credential_sha256(&self) -> &[u8; 32] {
        &self.credential_sha256
    }

    pub(crate) fn generation(&self) -> &[u8; 32] {
        &self.generation
    }

    pub(crate) fn plan_sha256(&self) -> &[u8; 32] {
        &self.plan_sha256
    }

    pub(crate) fn transaction_sha256(&self) -> &[u8; 32] {
        &self.transaction_sha256
    }

    pub(crate) fn activation_epoch(&self) -> u64 {
        self.activation_epoch
    }

    pub(crate) fn service_process_id(&self) -> u32 {
        self.service_process_id
    }

    pub(crate) fn service_process_creation_time(&self) -> u64 {
        self.service_process_creation_time
    }

    pub(crate) fn pipe_instance_id(&self) -> &[u8; 16] {
        &self.pipe_instance_id
    }

    pub(crate) fn receipt_sha256(&self) -> &[u8; 32] {
        &self.receipt_sha256
    }

    pub(crate) fn request_sha256(&self) -> &[u8; 32] {
        &self.request_sha256
    }

    pub(crate) fn canonical_bytes(&self) -> Result<Vec<u8>, AuthorityBootstrapError> {
        serde_json::to_vec(&CandidateValidationHandshakeWire {
            schema: CANDIDATE_HANDSHAKE_RESPONSE_SCHEMA.to_string(),
            credential_sha256: hex_lower(&self.credential_sha256),
            generation: hex_lower(&self.generation),
            plan_sha256: hex_lower(&self.plan_sha256),
            transaction_sha256: hex_lower(&self.transaction_sha256),
            activation_epoch: self.activation_epoch,
            service_process_id: self.service_process_id,
            service_process_creation_time: self.service_process_creation_time,
            pipe_instance_id: hex_lower(&self.pipe_instance_id),
            request_sha256: hex_lower(&self.request_sha256),
            receipt_sha256: hex_lower(&self.receipt_sha256),
        })
        .map_err(|_| {
            AuthorityBootstrapError("authority_candidate_handshake_response_serialization_failed")
        })
    }

    fn from_binding(
        binding: &CandidateActivationBinding,
        request: &CandidateValidationRequest,
        candidate_service: &CandidateProcessEvidence,
    ) -> Self {
        let credential_sha256 = binding.credential_sha256();
        let pipe_instance_id = candidate_pipe_instance_id(binding);

        let mut value = Self {
            credential_sha256,
            generation: binding.generation,
            plan_sha256: binding.plan_sha256,
            transaction_sha256: binding.transaction_sha256,
            activation_epoch: binding.activation_epoch,
            service_process_id: candidate_service.process_id(),
            service_process_creation_time: candidate_service.process_creation_time(),
            pipe_instance_id,
            request_sha256: candidate_handshake_request_digest(request),
            receipt_sha256: [0; 32],
        };
        value.receipt_sha256 = candidate_handshake_receipt_digest(&value);
        value
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct CandidateValidationRequest {
    credential_sha256: [u8; 32],
    nonce: [u8; 32],
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CandidateValidationRequestWire {
    schema: String,
    credential_sha256: String,
    nonce: String,
    request_sha256: String,
}

impl CandidateValidationRequest {
    pub(crate) fn new(
        credential_sha256: [u8; 32],
        nonce: [u8; 32],
    ) -> Result<Self, AuthorityBootstrapError> {
        if is_zero_digest(&credential_sha256) || is_zero_digest(&nonce) {
            return Err(AuthorityBootstrapError(
                "authority_candidate_handshake_request_invalid",
            ));
        }
        Ok(Self {
            credential_sha256,
            nonce,
        })
    }

    pub(crate) fn canonical_bytes(&self) -> Result<Vec<u8>, AuthorityBootstrapError> {
        let request_sha256 = candidate_handshake_request_digest(self);
        serde_json::to_vec(&CandidateValidationRequestWire {
            schema: CANDIDATE_HANDSHAKE_REQUEST_SCHEMA.to_string(),
            credential_sha256: hex_lower(&self.credential_sha256),
            nonce: hex_lower(&self.nonce),
            request_sha256: hex_lower(&request_sha256),
        })
        .map_err(|_| {
            AuthorityBootstrapError("authority_candidate_handshake_request_serialization_failed")
        })
    }

    pub(crate) fn parse_canonical(bytes: &[u8]) -> Result<Self, AuthorityBootstrapError> {
        if bytes.is_empty() || bytes.len() > MAX_CANDIDATE_HANDSHAKE_BYTES {
            return Err(AuthorityBootstrapError(
                "authority_candidate_handshake_request_size_invalid",
            ));
        }
        let wire: CandidateValidationRequestWire = serde_json::from_slice(bytes).map_err(|_| {
            AuthorityBootstrapError("authority_candidate_handshake_request_invalid")
        })?;
        if serde_json::to_vec(&wire).ok().as_deref() != Some(bytes)
            || wire.schema != CANDIDATE_HANDSHAKE_REQUEST_SCHEMA
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_handshake_request_noncanonical",
            ));
        }
        let value = Self::new(
            decode_digest(&wire.credential_sha256)?,
            decode_digest(&wire.nonce)?,
        )?;
        if decode_digest(&wire.request_sha256)? != candidate_handshake_request_digest(&value) {
            return Err(AuthorityBootstrapError(
                "authority_candidate_handshake_request_digest_mismatch",
            ));
        }
        Ok(value)
    }
}

/// An Armed credential has been checked against current protected state, but
/// is intentionally not consumed until a peer presents the one fixed request.
/// This value has no runtime/controller conversion and completion consumes it.
#[derive(Debug)]
pub(crate) struct PreparedCandidateValidation {
    record: CandidateCredentialRecord,
    binding: CandidateActivationBinding,
}

impl PreparedCandidateValidation {
    pub(crate) fn credential_sha256(&self) -> [u8; 32] {
        self.binding.credential_sha256()
    }

    pub(crate) fn pipe_instance_id(&self) -> [u8; 16] {
        candidate_pipe_instance_id(&self.binding)
    }

    pub(super) fn complete_fixed_handshake<S: CandidateCredentialConsumer>(
        self,
        request: CandidateValidationRequest,
        client_peer: CandidateProcessEvidence,
        now_unix_millis: u64,
        store: &mut S,
    ) -> Result<CandidateValidationHandshake, AuthorityBootstrapError> {
        self.binding.validate_time(now_unix_millis)?;
        let expected_credential = self.record.credential_sha256()?;
        if request.credential_sha256 != expected_credential || request.nonce != self.binding.nonce {
            return Err(AuthorityBootstrapError(
                "authority_candidate_handshake_request_mismatch",
            ));
        }
        if client_peer != *self.binding.issuer.maintenance_worker() {
            return Err(AuthorityBootstrapError(
                "authority_candidate_client_peer_binding_mismatch",
            ));
        }
        let consumed = store.consume_armed(&self.record, &request, client_peer)?;
        if consumed.phase() != CandidateCredentialPhase::Consumed
            || consumed.credential_sha256()? != expected_credential
            || consumed.binding()? != self.binding
            || consumed.consumption().map(|value| value.client_peer()) != Some(&client_peer)
            || consumed.consumption().map(|value| value.request_sha256())
                != Some(&candidate_handshake_request_digest(&request))
        {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_consumption_not_verified",
            ));
        }
        let candidate_service = self
            .record
            .candidate_service()
            .ok_or(AuthorityBootstrapError(
                "authority_candidate_service_process_missing",
            ))?;
        Ok(CandidateValidationHandshake::from_binding(
            &self.binding,
            &request,
            candidate_service,
        ))
    }
}

pub(crate) fn prepare_candidate_activation<S: CandidateCredentialConsumer>(
    observation: &CandidateActivationObservation,
    now_unix_millis: u64,
    store: &mut S,
) -> Result<PreparedCandidateValidation, AuthorityBootstrapError> {
    observation.validate()?;
    let readback = store.read_candidate(observation.transaction_sha256())?;
    prepare_candidate_activation_from_readback(observation, now_unix_millis, readback)
}

pub(crate) fn prepare_candidate_activation_from_readback(
    observation: &CandidateActivationObservation,
    now_unix_millis: u64,
    readback: CandidateCredentialReadback,
) -> Result<PreparedCandidateValidation, AuthorityBootstrapError> {
    observation.validate()?;
    let (record, issuer, armed_receipt_sha256) = match readback {
        CandidateCredentialReadback::None => {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_missing",
            ))
        }
        CandidateCredentialReadback::Record {
            record,
            issuer,
            armed_receipt_sha256,
        } => (record, issuer, armed_receipt_sha256),
    };
    if issuer != observation.issuer {
        return Err(AuthorityBootstrapError(
            "authority_candidate_issuer_binding_mismatch",
        ));
    }
    match record.phase() {
        CandidateCredentialPhase::Prepared => {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_not_armed",
            ))
        }
        CandidateCredentialPhase::Armed => {}
        CandidateCredentialPhase::Consumed => {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_replayed",
            ))
        }
        CandidateCredentialPhase::Committed => {
            return Err(AuthorityBootstrapError(
                "authority_candidate_credential_committed_wrong_lane",
            ))
        }
    }
    if is_zero_digest(&armed_receipt_sha256)
        || record.armed_receipt_sha256().ok() != Some(armed_receipt_sha256)
    {
        return Err(AuthorityBootstrapError(
            "authority_candidate_armed_receipt_binding_mismatch",
        ));
    }
    let binding = record.binding()?;
    binding.validate_time(now_unix_millis)?;
    let candidate_service = record.candidate_service().ok_or(AuthorityBootstrapError(
        "authority_candidate_service_process_missing",
    ))?;
    if !observation.matches(&binding, candidate_service) {
        return Err(AuthorityBootstrapError(
            "authority_candidate_credential_binding_mismatch",
        ));
    }
    Ok(PreparedCandidateValidation { record, binding })
}

pub(crate) fn candidate_credential_file_name(
    transaction_sha256: &[u8; 32],
) -> Result<String, AuthorityBootstrapError> {
    if is_zero_digest(transaction_sha256) {
        return Err(AuthorityBootstrapError(
            "authority_candidate_transaction_invalid",
        ));
    }
    Ok(format!(
        "VRCForgeEvidenceAuthority-candidate-{}.json",
        hex_lower(transaction_sha256)
    ))
}

pub(crate) fn candidate_pipe_name(
    pipe_instance_id: &[u8; 16],
) -> Result<String, AuthorityBootstrapError> {
    if pipe_instance_id.iter().all(|byte| *byte == 0) {
        return Err(AuthorityBootstrapError(
            "authority_candidate_pipe_instance_invalid",
        ));
    }
    Ok(format!(
        r"\\.\pipe\VRCForgeEvidenceAuthorityCandidate-{}",
        hex_lower(pipe_instance_id)
    ))
}

fn candidate_credential_digest(binding: &CandidateActivationBinding) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_CREDENTIAL_DOMAIN);
    digest.update(binding.generation);
    digest.update(binding.plan_sha256);
    digest.update(binding.transaction_sha256);
    digest.update(binding.activation_epoch.to_be_bytes());
    digest.update(binding.active_head_sha256);
    digest.update(binding.trust_manifest_sha256);
    digest.update(binding.activation_manifest_sha256);
    digest.update(binding.ledger_identity);
    update_candidate_image_evidence_digest(&mut digest, &binding.target_service_image);
    digest.update(binding.issuer.capsule_sha256);
    digest.update(binding.issuer.transaction_started_receipt_sha256);
    digest.update(binding.issuer.worker_started_receipt_sha256);
    update_candidate_process_evidence_digest(&mut digest, &binding.issuer.maintenance_worker);
    digest.update(binding.issuer.nonce_consumption_receipt_sha256);
    digest.update(binding.issuer.nonce_consumption_full_readback_sha256);
    digest.update(binding.issuer.nonce_consumption_file_sha256);
    digest.update(
        binding
            .issuer
            .nonce_consumption_file_volume_serial
            .to_be_bytes(),
    );
    digest.update(binding.issuer.nonce_consumption_file_id);
    digest.update(binding.nonce);
    digest.update(binding.issued_at_unix_millis.to_be_bytes());
    digest.update(binding.expires_at_unix_millis.to_be_bytes());
    digest.finalize().into()
}

fn candidate_pipe_instance_id(binding: &CandidateActivationBinding) -> [u8; 16] {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_HANDSHAKE_PIPE_DOMAIN);
    digest.update(binding.credential_sha256());
    digest.update(binding.nonce);
    let digest: [u8; 32] = digest.finalize().into();
    let mut pipe_instance_id = [0u8; 16];
    pipe_instance_id.copy_from_slice(&digest[..16]);
    pipe_instance_id
}

fn candidate_handshake_request_digest(request: &CandidateValidationRequest) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_HANDSHAKE_REQUEST_DOMAIN);
    digest.update(request.credential_sha256);
    digest.update(request.nonce);
    digest.finalize().into()
}

fn candidate_handshake_receipt_digest(handshake: &CandidateValidationHandshake) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_HANDSHAKE_RECEIPT_DOMAIN);
    digest.update(handshake.credential_sha256);
    digest.update(handshake.generation);
    digest.update(handshake.plan_sha256);
    digest.update(handshake.transaction_sha256);
    digest.update(handshake.activation_epoch.to_be_bytes());
    digest.update(handshake.service_process_id.to_be_bytes());
    digest.update(handshake.service_process_creation_time.to_be_bytes());
    digest.update(handshake.pipe_instance_id);
    digest.update(handshake.request_sha256);
    digest.finalize().into()
}

fn candidate_record_digest(
    phase: CandidateCredentialPhase,
    credential_sha256: &[u8; 32],
    armed_receipt_sha256: Option<&[u8; 32]>,
    candidate_service: Option<&CandidateProcessEvidence>,
    consumption: Option<&CandidateConsumptionEvidence>,
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_RECORD_DOMAIN);
    digest.update([phase.digest_tag()]);
    digest.update(credential_sha256);
    match armed_receipt_sha256 {
        Some(value) => {
            digest.update([1]);
            digest.update(value);
        }
        None => digest.update([0]),
    }
    match consumption {
        Some(value) => {
            digest.update([1]);
            digest.update(value.receipt_sha256);
        }
        None => digest.update([0]),
    }
    match candidate_service {
        Some(value) => {
            digest.update([1]);
            digest.update(value.full_readback_receipt_sha256());
        }
        None => digest.update([0]),
    }
    digest.finalize().into()
}

fn candidate_image_evidence_digest(evidence: &CandidateImageEvidence) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_IMAGE_EVIDENCE_DOMAIN);
    digest.update(evidence.image_sha256);
    digest.update(evidence.image_byte_length.to_be_bytes());
    digest.update(evidence.image_volume_serial.to_be_bytes());
    digest.update(evidence.image_file_id);
    digest.update(evidence.image_link_count.to_be_bytes());
    digest.update(evidence.image_attributes.to_be_bytes());
    digest.finalize().into()
}

fn candidate_process_evidence_digest(evidence: &CandidateProcessEvidence) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_PROCESS_EVIDENCE_DOMAIN);
    digest.update(evidence.process_id.to_be_bytes());
    digest.update(evidence.process_creation_time.to_be_bytes());
    digest.update(evidence.image.full_readback_receipt_sha256);
    digest.finalize().into()
}

fn update_candidate_image_evidence_digest(digest: &mut Sha256, evidence: &CandidateImageEvidence) {
    digest.update(evidence.image_sha256);
    digest.update(evidence.image_byte_length.to_be_bytes());
    digest.update(evidence.image_volume_serial.to_be_bytes());
    digest.update(evidence.image_file_id);
    digest.update(evidence.image_link_count.to_be_bytes());
    digest.update(evidence.image_attributes.to_be_bytes());
    digest.update(evidence.full_readback_receipt_sha256);
}

fn update_candidate_process_evidence_digest(
    digest: &mut Sha256,
    evidence: &CandidateProcessEvidence,
) {
    digest.update(evidence.process_id.to_be_bytes());
    digest.update(evidence.process_creation_time.to_be_bytes());
    update_candidate_image_evidence_digest(digest, &evidence.image);
    digest.update(evidence.full_readback_receipt_sha256);
}

fn candidate_consumption_receipt_digest(evidence: &CandidateConsumptionEvidence) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_CONSUMPTION_RECEIPT_DOMAIN);
    digest.update(evidence.credential_sha256);
    digest.update(evidence.armed_receipt_sha256);
    digest.update(evidence.pipe_instance_id);
    digest.update(evidence.request_sha256);
    update_candidate_process_evidence_digest(&mut digest, &evidence.client_peer);
    digest.finalize().into()
}

fn is_zero_digest(value: &[u8; 32]) -> bool {
    value.iter().all(|byte| *byte == 0)
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

fn decode_digest(value: &str) -> Result<[u8; 32], AuthorityBootstrapError> {
    let output = decode_fixed_hex::<32>(value)?;
    if is_zero_digest(&output) {
        return Err(AuthorityBootstrapError(
            "authority_candidate_credential_digest_invalid",
        ));
    }
    Ok(output)
}

fn decode_start_locator_digest(
    value: &str,
    prefix: &str,
) -> Result<[u8; 32], AuthorityBootstrapError> {
    value
        .strip_prefix(prefix)
        .ok_or(AuthorityBootstrapError(
            "authority_candidate_start_locator_invalid",
        ))
        .and_then(|digest| {
            decode_digest(digest)
                .map_err(|_| AuthorityBootstrapError("authority_candidate_start_locator_invalid"))
        })
}

fn decode_fixed_hex<const N: usize>(value: &str) -> Result<[u8; N], AuthorityBootstrapError> {
    if value.len() != N * 2
        || value
            .as_bytes()
            .iter()
            .any(|byte| !matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
    {
        return Err(AuthorityBootstrapError(
            "authority_candidate_credential_digest_invalid",
        ));
    }
    let mut output = [0u8; N];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (hex_nibble(pair[0]) << 4) | hex_nibble(pair[1]);
    }
    Ok(output)
}

fn hex_nibble(value: u8) -> u8 {
    match value {
        b'0'..=b'9' => value - b'0',
        b'a'..=b'f' => value - b'a' + 10,
        _ => unreachable!("validated lowercase hexadecimal input"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Clone)]
    struct MemoryCredentialStore {
        record: Option<CandidateCredentialRecord>,
        wrong_consume: bool,
    }

    impl CandidateCredentialConsumer for MemoryCredentialStore {
        fn read_candidate(
            &mut self,
            transaction_sha256: &[u8; 32],
        ) -> Result<CandidateCredentialReadback, AuthorityBootstrapError> {
            match self.record.clone() {
                None => Ok(CandidateCredentialReadback::None),
                Some(record) if record.binding()?.transaction_sha256() == transaction_sha256 => {
                    let issuer = record.binding()?.issuer();
                    let armed_receipt_sha256 = record.armed_receipt_sha256().unwrap_or([0; 32]);
                    Ok(CandidateCredentialReadback::Record {
                        record,
                        issuer,
                        armed_receipt_sha256,
                    })
                }
                Some(_) => Err(AuthorityBootstrapError(
                    "authority_candidate_credential_binding_mismatch",
                )),
            }
        }

        fn consume_armed(
            &mut self,
            expected: &CandidateCredentialRecord,
            request: &CandidateValidationRequest,
            client_peer: CandidateProcessEvidence,
        ) -> Result<CandidateCredentialRecord, AuthorityBootstrapError> {
            let current = self.record.as_ref().ok_or(AuthorityBootstrapError(
                "authority_candidate_credential_missing",
            ))?;
            if current != expected || current.phase() != CandidateCredentialPhase::Armed {
                return Err(AuthorityBootstrapError(
                    "authority_candidate_credential_compare_exchange_failed",
                ));
            }
            let mut consumed = current.consume_with_peer(request, client_peer)?;
            if self.wrong_consume {
                consumed.credential_sha256 = "ab".repeat(32);
            }
            self.record = Some(consumed.clone());
            Ok(consumed)
        }
    }

    fn observation() -> CandidateActivationObservation {
        CandidateActivationObservation::new(
            [0x11; 32], [0x12; 32], [0x13; 32], 7, [0x14; 32], [0x15; 32], [0x16; 32], [0x17; 32],
            [0x18; 32], 919, 42_424,
        )
        .unwrap()
    }

    fn binding() -> CandidateActivationBinding {
        CandidateActivationBinding::new(observation(), [0x19; 32], 10_000, 20_000).unwrap()
    }

    fn client_peer() -> CandidateProcessEvidence {
        *binding().issuer().maintenance_worker()
    }

    fn reseal_process(value: &mut CandidateProcessEvidence) {
        value.image.full_readback_receipt_sha256 = candidate_image_evidence_digest(&value.image);
        value.full_readback_receipt_sha256 = candidate_process_evidence_digest(value);
    }

    fn store(record: Option<CandidateCredentialRecord>) -> MemoryCredentialStore {
        MemoryCredentialStore {
            record,
            wrong_consume: false,
        }
    }

    fn complete_candidate_activation(
        observation: &CandidateActivationObservation,
        now_unix_millis: u64,
        store: &mut MemoryCredentialStore,
    ) -> Result<CandidateValidationHandshake, AuthorityBootstrapError> {
        let prepared = prepare_candidate_activation(observation, now_unix_millis, store)?;
        let binding = store
            .record
            .as_ref()
            .ok_or(AuthorityBootstrapError(
                "authority_candidate_credential_missing",
            ))?
            .binding()?;
        let request =
            CandidateValidationRequest::new(binding.credential_sha256(), *binding.nonce())?;
        let client_peer = *binding.issuer().maintenance_worker();
        prepared.complete_fixed_handshake(request, client_peer, now_unix_millis, store)
    }

    #[test]
    fn credential_record_round_trip_is_canonical_and_phase_bound() {
        let prepared = CandidateCredentialRecord::prepared(binding()).unwrap();
        let armed = prepared.arm().unwrap();
        assert_ne!(
            prepared.record_sha256().unwrap(),
            armed.record_sha256().unwrap()
        );
        assert_eq!(
            CandidateCredentialRecord::parse_canonical(&armed.canonical_bytes().unwrap()).unwrap(),
            armed
        );
        let mut noncanonical = armed.canonical_bytes().unwrap();
        noncanonical.push(b'\n');
        assert_eq!(
            CandidateCredentialRecord::parse_canonical(&noncanonical)
                .unwrap_err()
                .code(),
            "authority_candidate_credential_noncanonical"
        );
    }

    #[test]
    fn fixed_request_is_canonical_and_only_exact_request_consumes() {
        let armed = CandidateCredentialRecord::prepared(binding())
            .unwrap()
            .arm()
            .unwrap();
        let mut candidate_store = store(Some(armed));
        let prepared =
            prepare_candidate_activation(&observation(), 15_000, &mut candidate_store).unwrap();
        assert_eq!(
            candidate_store.record.as_ref().unwrap().phase(),
            CandidateCredentialPhase::Armed
        );
        let exact =
            CandidateValidationRequest::new(binding().credential_sha256(), [0x19; 32]).unwrap();
        assert_eq!(
            CandidateValidationRequest::parse_canonical(&exact.canonical_bytes().unwrap()).unwrap(),
            exact
        );
        let wrong =
            CandidateValidationRequest::new(binding().credential_sha256(), [0x1a; 32]).unwrap();
        assert_eq!(
            prepared
                .complete_fixed_handshake(wrong, client_peer(), 15_000, &mut candidate_store)
                .unwrap_err()
                .code(),
            "authority_candidate_handshake_request_mismatch"
        );
        assert_eq!(
            candidate_store.record.unwrap().phase(),
            CandidateCredentialPhase::Armed
        );
        let armed = CandidateCredentialRecord::prepared(binding())
            .unwrap()
            .arm()
            .unwrap();
        let mut exact_store = store(Some(armed));
        let prepared =
            prepare_candidate_activation(&observation(), 15_000, &mut exact_store).unwrap();
        prepared
            .complete_fixed_handshake(exact, client_peer(), 15_000, &mut exact_store)
            .unwrap();
        assert_eq!(
            exact_store.record.unwrap().phase(),
            CandidateCredentialPhase::Consumed
        );
    }

    #[test]
    fn none_prepared_armed_committed_and_consumed_are_distinct_lanes() {
        let prepared = CandidateCredentialRecord::prepared(binding()).unwrap();
        let armed = prepared.arm().unwrap();
        let consumed = armed.consume().unwrap();
        let committed = consumed.commit().unwrap();

        let mut absent = store(None);
        assert_eq!(
            complete_candidate_activation(&observation(), 15_000, &mut absent)
                .unwrap_err()
                .code(),
            "authority_candidate_credential_missing"
        );
        let mut prepared_store = store(Some(prepared));
        assert_eq!(
            complete_candidate_activation(&observation(), 15_000, &mut prepared_store)
                .unwrap_err()
                .code(),
            "authority_candidate_credential_not_armed"
        );
        let mut armed_store = store(Some(armed));
        let handshake =
            complete_candidate_activation(&observation(), 15_000, &mut armed_store).unwrap();
        assert_eq!(handshake.generation(), &[0x11; 32]);
        assert_eq!(handshake.plan_sha256(), &[0x12; 32]);
        assert_eq!(handshake.transaction_sha256(), &[0x13; 32]);
        assert_eq!(handshake.activation_epoch(), 7);
        assert_eq!(handshake.service_process_id(), 919);
        assert_eq!(handshake.service_process_creation_time(), 42_424);
        assert!(handshake.pipe_instance_id().iter().any(|byte| *byte != 0));
        assert!(handshake.request_sha256().iter().any(|byte| *byte != 0));
        assert!(handshake.receipt_sha256().iter().any(|byte| *byte != 0));
        assert!(handshake.canonical_bytes().unwrap().len() <= MAX_CANDIDATE_HANDSHAKE_BYTES);

        let mut committed_store = store(Some(committed));
        assert_eq!(
            complete_candidate_activation(&observation(), 15_000, &mut committed_store)
                .unwrap_err()
                .code(),
            "authority_candidate_credential_committed_wrong_lane"
        );
        assert_eq!(
            complete_candidate_activation(&observation(), 15_000, &mut armed_store)
                .unwrap_err()
                .code(),
            "authority_candidate_credential_replayed"
        );
    }

    #[test]
    fn every_observed_binding_is_exact() {
        let baseline = observation();
        let armed = CandidateCredentialRecord::prepared(binding())
            .unwrap()
            .arm()
            .unwrap();
        let mut variants = Vec::new();
        let mut value = baseline;
        value.generation[0] ^= 1;
        variants.push(value);
        let mut value = baseline;
        value.plan_sha256[0] ^= 1;
        variants.push(value);
        let mut value = baseline;
        value.transaction_sha256[0] ^= 1;
        variants.push(value);
        let mut value = baseline;
        value.activation_epoch += 1;
        variants.push(value);
        let mut value = baseline;
        value.active_head_sha256[0] ^= 1;
        variants.push(value);
        let mut value = baseline;
        value.trust_manifest_sha256[0] ^= 1;
        variants.push(value);
        let mut value = baseline;
        value.activation_manifest_sha256[0] ^= 1;
        variants.push(value);
        let mut value = baseline;
        value.ledger_identity[0] ^= 1;
        variants.push(value);
        let mut value = baseline;
        value.candidate_service.image.image_sha256[0] ^= 1;
        reseal_process(&mut value.candidate_service);
        variants.push(value);
        let mut value = baseline;
        value.candidate_service.process_id += 1;
        reseal_process(&mut value.candidate_service);
        variants.push(value);
        let mut value = baseline;
        value.candidate_service.process_creation_time += 1;
        reseal_process(&mut value.candidate_service);
        variants.push(value);

        for variant in variants {
            let mut candidate_store = store(Some(armed.clone()));
            assert_eq!(
                complete_candidate_activation(&variant, 15_000, &mut candidate_store)
                    .unwrap_err()
                    .code(),
                "authority_candidate_credential_binding_mismatch"
            );
            assert_eq!(
                candidate_store.record.unwrap().phase(),
                CandidateCredentialPhase::Armed
            );
        }

        let mut issuer_variants = Vec::new();
        let mut value = baseline;
        value.issuer.capsule_sha256[0] ^= 1;
        issuer_variants.push(value);
        let mut value = baseline;
        value.issuer.transaction_started_receipt_sha256[0] ^= 1;
        issuer_variants.push(value);
        let mut value = baseline;
        value.issuer.worker_started_receipt_sha256[0] ^= 1;
        issuer_variants.push(value);
        let mut value = baseline;
        value.issuer.maintenance_worker.process_id += 1;
        reseal_process(&mut value.issuer.maintenance_worker);
        issuer_variants.push(value);
        let mut value = baseline;
        value.issuer.nonce_consumption_receipt_sha256[0] ^= 1;
        issuer_variants.push(value);
        let mut value = baseline;
        value.issuer.nonce_consumption_full_readback_sha256[0] ^= 1;
        issuer_variants.push(value);
        let mut value = baseline;
        value.issuer.nonce_consumption_file_sha256[0] ^= 1;
        issuer_variants.push(value);
        let mut value = baseline;
        value.issuer.nonce_consumption_file_volume_serial += 1;
        issuer_variants.push(value);
        let mut value = baseline;
        value.issuer.nonce_consumption_file_id[0] ^= 1;
        issuer_variants.push(value);

        for variant in issuer_variants {
            let mut candidate_store = store(Some(armed.clone()));
            assert_eq!(
                complete_candidate_activation(&variant, 15_000, &mut candidate_store)
                    .unwrap_err()
                    .code(),
                "authority_candidate_issuer_binding_mismatch"
            );
            assert_eq!(
                candidate_store.record.unwrap().phase(),
                CandidateCredentialPhase::Armed
            );
        }
    }

    #[test]
    fn response_bytes_are_untrusted_until_exact_local_evidence_matches() {
        let binding = binding();
        let request =
            CandidateValidationRequest::new(binding.credential_sha256(), *binding.nonce()).unwrap();
        let pipe_instance_id = candidate_pipe_instance_id(&binding);
        let candidate_service = observation().candidate_service;
        let peer =
            CandidateServicePeerObservation::new(candidate_service, pipe_instance_id).unwrap();
        let expectation =
            CandidateResponseExpectation::new(binding, request, candidate_service, peer).unwrap();
        let handshake =
            CandidateValidationHandshake::from_binding(&binding, &request, &candidate_service);
        let bytes = handshake.canonical_bytes().unwrap();
        let verified = UntrustedCandidateValidationResponse::parse_canonical(&bytes)
            .unwrap()
            .verify_against(&expectation)
            .unwrap();
        assert_eq!(verified.credential_sha256(), &binding.credential_sha256());
        assert_eq!(
            verified.request_sha256(),
            &candidate_handshake_request_digest(&request)
        );
        assert_eq!(verified.receipt_sha256(), handshake.receipt_sha256());

        let mut forged = handshake;
        forged.service_process_id += 1;
        forged.receipt_sha256 = candidate_handshake_receipt_digest(&forged);
        assert_eq!(
            UntrustedCandidateValidationResponse::parse_canonical(
                &forged.canonical_bytes().unwrap()
            )
            .unwrap()
            .verify_against(&expectation)
            .unwrap_err()
            .code(),
            "authority_candidate_handshake_response_binding_mismatch"
        );

        let drifted_service = CandidateProcessEvidence::from_held_process(
            candidate_service.process_id() + 1,
            candidate_service.process_creation_time(),
            *binding.service_image_sha256(),
            binding.target_service_image().image_byte_length(),
            binding.target_service_image().image_volume_serial(),
            *binding.target_service_image().image_file_id(),
            binding.target_service_image().image_link_count(),
            binding.target_service_image().image_attributes(),
        )
        .unwrap();
        let drifted_peer =
            CandidateServicePeerObservation::new(drifted_service, pipe_instance_id).unwrap();
        assert_eq!(
            CandidateResponseExpectation::new(binding, request, candidate_service, drifted_peer)
                .unwrap_err()
                .code(),
            "authority_candidate_response_expectation_mismatch"
        );

        let mut noncanonical = bytes;
        noncanonical.push(b'\n');
        assert_eq!(
            UntrustedCandidateValidationResponse::parse_canonical(&noncanonical)
                .unwrap_err()
                .code(),
            "authority_candidate_handshake_response_noncanonical"
        );
        assert_eq!(
            UntrustedCandidateValidationResponse::parse_canonical(&vec![
                b'x';
                MAX_CANDIDATE_HANDSHAKE_BYTES
                    + 1
            ])
            .unwrap_err()
            .code(),
            "authority_candidate_handshake_response_size_invalid"
        );
    }

    #[test]
    fn credential_time_window_is_short_and_exact() {
        assert_eq!(
            CandidateActivationBinding::new(
                observation(),
                [0x19; 32],
                10_000,
                10_000 + MAX_CANDIDATE_CREDENTIAL_LIFETIME_MILLIS + 1,
            )
            .unwrap_err()
            .code(),
            "authority_candidate_credential_lifetime_invalid"
        );
        let armed = CandidateCredentialRecord::prepared(binding())
            .unwrap()
            .arm()
            .unwrap();
        let mut early = store(Some(armed.clone()));
        assert_eq!(
            complete_candidate_activation(&observation(), 9_999, &mut early)
                .unwrap_err()
                .code(),
            "authority_candidate_credential_not_yet_valid"
        );
        let mut expired = store(Some(armed));
        assert_eq!(
            complete_candidate_activation(&observation(), 20_001, &mut expired)
                .unwrap_err()
                .code(),
            "authority_candidate_credential_expired"
        );

        let armed = CandidateCredentialRecord::prepared(binding())
            .unwrap()
            .arm()
            .unwrap();
        let mut expires_while_waiting = store(Some(armed));
        let prepared =
            prepare_candidate_activation(&observation(), 15_000, &mut expires_while_waiting)
                .unwrap();
        let request =
            CandidateValidationRequest::new(binding().credential_sha256(), [0x19; 32]).unwrap();
        assert_eq!(
            prepared
                .complete_fixed_handshake(
                    request,
                    client_peer(),
                    20_001,
                    &mut expires_while_waiting,
                )
                .unwrap_err()
                .code(),
            "authority_candidate_credential_expired"
        );
        assert_eq!(
            expires_while_waiting.record.unwrap().phase(),
            CandidateCredentialPhase::Armed
        );
    }

    #[test]
    fn consume_must_return_the_exact_durable_successor() {
        let armed = CandidateCredentialRecord::prepared(binding())
            .unwrap()
            .arm()
            .unwrap();
        let mut candidate_store = store(Some(armed));
        candidate_store.wrong_consume = true;
        assert_eq!(
            complete_candidate_activation(&observation(), 15_000, &mut candidate_store)
                .unwrap_err()
                .code(),
            "authority_candidate_credential_consumption_not_verified"
        );
    }

    #[test]
    fn file_name_is_transaction_bound() {
        assert!(candidate_credential_file_name(&[0; 32]).is_err());
        let name = candidate_credential_file_name(&[0x5a; 32]).unwrap();
        assert_eq!(
            name,
            format!(
                "VRCForgeEvidenceAuthority-candidate-{}.json",
                "5a".repeat(32)
            )
        );
        assert!(candidate_pipe_name(&[0; 16]).is_err());
        assert_eq!(
            candidate_pipe_name(&[0x6b; 16]).unwrap(),
            format!(
                r"\\.\pipe\VRCForgeEvidenceAuthorityCandidate-{}",
                "6b".repeat(16)
            )
        );
    }

    #[test]
    fn candidate_types_have_no_runtime_or_controller_pipe_composition_path() {
        let runtime_source = include_str!("../primitive_evidence_authority_service_runtime.rs");
        assert!(runtime_source.contains("bootstrap: AuthenticatedFinalCommitBootstrap"));
        assert!(!runtime_source.contains("ValidatedAuthorityBootstrap"));
        let service_source = include_str!("../bin/vrcforge_primitive_evidence_service.rs");
        assert!(!service_source.contains("into_validated()"));
        let candidate_pipe_source =
            include_str!("../primitive_evidence_authority_candidate_pipe.rs");
        assert!(!candidate_pipe_source.contains("AuthorityPipe::"));
        assert!(!candidate_pipe_source.contains("compose_production_runtime"));
        assert!(!candidate_pipe_source.contains("AuthorityRuntime"));
    }
}
