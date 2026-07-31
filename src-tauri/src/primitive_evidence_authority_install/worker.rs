use super::{
    bootstrap_activation::{
        candidate_credential_file_name, CandidateCredentialPhase, CandidateCredentialRecord,
        CandidateProcessEvidence,
    },
    *,
};

pub(super) const MAINTENANCE_WORKER_CAPSULE_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_capsule.v2";
pub(super) const MAINTENANCE_WORKER_SERVICE_NAME: &str = "VRCForgePrimitiveEvidenceMaintenance";
pub(super) const MAINTENANCE_WORKER_DISPLAY_NAME: &str = "VRCForge Primitive Evidence Maintenance";
const WORKER_CAPSULE_DOMAIN: &[u8] = b"vrcforge-authority-worker-capsule-v2\0";
const WORKER_LAUNCH_DOMAIN: &[u8] = b"vrcforge-authority-worker-launch-v2\0";
const WORKER_BOOTSTRAP_INTENT_DOMAIN: &[u8] = b"vrcforge-authority-worker-bootstrap-intent-v2\0";
const WORKER_BOOTSTRAP_STAGING_DOMAIN: &[u8] = b"vrcforge-authority-worker-bootstrap-staging-v2\0";
const WORKER_BOOTSTRAP_FILE_READBACK_DOMAIN: &[u8] =
    b"vrcforge-authority-worker-bootstrap-file-readback-v2\0";
const WORKER_JOURNAL_DOMAIN: &[u8] = b"vrcforge-authority-worker-journal-v2\0";
const WORKER_HANDOFF_DOMAIN: &[u8] = b"vrcforge-authority-worker-handoff-v2\0";
const WORKER_SOURCE_STAGING_INTENT_DOMAIN: &[u8] =
    b"vrcforge-authority-worker-source-staging-intent-v2\0";
const WORKER_PARTIAL_STAGING_CLEANUP_DOMAIN: &[u8] =
    b"vrcforge-authority-worker-partial-staging-cleanup-v2\0";
const WORKER_STAGING_DOMAIN: &[u8] = b"vrcforge-authority-worker-staging-v2\0";
const WORKER_STAGING_FILE_READBACK_DOMAIN: &[u8] =
    b"vrcforge-authority-worker-staging-file-readback-v2\0";
const WORKER_STAGING_CLEANUP_DOMAIN: &[u8] = b"vrcforge-authority-worker-staging-cleanup-v2\0";
const WORKER_PIPE_PREPARED_DOMAIN: &[u8] = b"vrcforge-authority-worker-pipe-prepared-v2\0";
const WORKER_PIPE_RECOVERY_DOMAIN: &[u8] = b"vrcforge-authority-worker-pipe-recovery-v2\0";
const WORKER_SERVICE_CREATED_DOMAIN: &[u8] = b"vrcforge-authority-worker-service-created-v2\0";
const WORKER_INVOCATION_CLAIM_DOMAIN: &[u8] = b"vrcforge-authority-worker-invocation-claim-v2\0";
const WORKER_STARTED_DOMAIN: &[u8] = b"vrcforge-authority-worker-started-v2\0";
const WORKER_NONCE_CONSUMPTION_DOMAIN: &[u8] = b"vrcforge-authority-worker-nonce-consumption-v2\0";
const WORKER_TRANSACTION_STARTED_DOMAIN: &[u8] =
    b"vrcforge-authority-worker-transaction-started-v2\0";
const WORKER_CANDIDATE_CREDENTIAL_ARMED_DOMAIN: &[u8] =
    b"vrcforge-authority-worker-candidate-credential-armed-v2\0";
const WORKER_TRANSACTION_COMMITTED_DOMAIN: &[u8] =
    b"vrcforge-authority-worker-transaction-committed-v2\0";
const WORKER_TRANSACTION_CONTAINED_DOMAIN: &[u8] =
    b"vrcforge-authority-worker-transaction-contained-v2\0";
const WORKER_EXIT_READY_DOMAIN: &[u8] = b"vrcforge-authority-worker-exit-ready-v2\0";
const WORKER_SERVICE_DELETE_INTENT_DOMAIN: &[u8] =
    b"vrcforge-authority-worker-service-delete-intent-v2\0";
const WORKER_SERVICE_DELETE_PENDING_DOMAIN: &[u8] =
    b"vrcforge-authority-worker-service-delete-pending-v2\0";
const WORKER_FINALIZER_HANDLES_CLOSED_DOMAIN: &[u8] =
    b"vrcforge-authority-worker-finalizer-handles-closed-v2\0";
const WORKER_SERVICE_ABSENT_DOMAIN: &[u8] = b"vrcforge-authority-worker-service-absent-v2\0";
const MAX_WORKER_RECEIPT_BYTES: usize = 64 * 1024;
const MAX_WORKER_JOURNAL_BYTES: usize = 64 * 1024;
const MAX_CONSENT_LEASE_MILLIS: u64 = 10 * 60 * 1_000;
pub(super) const ACTION_TIME_CONSENT_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_action_consent.v2";
pub(super) const WORKER_HANDLE_HANDOFF_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_handle_handoff.v2";
pub(super) const WORKER_BOOTSTRAP_STAGING_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_bootstrap_staging.v2";
pub(super) const WORKER_BOOTSTRAP_INTENT_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_bootstrap_intent.v2";
pub(super) const WORKER_DURABLE_STAGING_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_durable_staging.v2";
pub(super) const WORKER_SOURCE_STAGING_INTENT_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_source_staging_intent.v2";
pub(super) const WORKER_PARTIAL_STAGING_CLEANUP_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_partial_staging_cleanup.v2";
pub(super) const WORKER_SOURCE_IDENTITY_LEDGER_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_source_identity_ledger.v2";
pub(super) const WORKER_STAGING_CLEANUP_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_staging_cleanup.v2";
pub(super) const WORKER_PIPE_PREPARED_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_pipe_prepared.v2";
pub(super) const WORKER_PIPE_RECOVERY_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_pipe_recovery.v2";
pub(super) const WORKER_SERVICE_CREATED_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_service_created.v2";
pub(super) const WORKER_INVOCATION_CLAIM_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_invocation_claim.v2";
pub(super) const WORKER_STARTED_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_started.v2";
pub(super) const WORKER_NONCE_CONSUMPTION_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_nonce_consumption.v2";
pub(super) const WORKER_TRANSACTION_STARTED_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_transaction_started.v2";
pub(super) const WORKER_CANDIDATE_CREDENTIAL_ARMED_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_candidate_credential_armed.v2";
pub(super) const WORKER_TRANSACTION_COMMITTED_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_transaction_committed.v2";
pub(super) const WORKER_TRANSACTION_CONTAINED_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_transaction_contained.v2";
pub(super) const WORKER_EXIT_READY_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_exit_ready.v2";
pub(super) const WORKER_SERVICE_DELETE_INTENT_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_service_delete_intent.v2";
pub(super) const WORKER_SERVICE_DELETE_PENDING_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_service_delete_pending.v2";
pub(super) const WORKER_FINALIZER_HANDLES_CLOSED_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_finalizer_handles_closed.v2";
pub(super) const WORKER_SERVICE_ABSENT_SCHEMA: &str =
    "vrcforge.primitive_evidence_authority_worker_service_absent.v2";
const WORKER_HANDOFF_PIPE_PREFIX: &str = r"\\.\pipe\VRCForge.PrimitiveEvidence.Maintenance.v2.";

macro_rules! impl_worker_receipt_digest {
    ($receipt:ty, $domain:expr, $error:expr) => {
        impl $receipt {
            fn seal(&mut self) -> Result<(), AuthorityMaintenanceError> {
                self.receipt_sha256 = hex_lower(&self.compute_digest()?);
                Ok(())
            }

            pub(super) fn digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
                let value = decode_hex_32(&self.receipt_sha256)?;
                if value.iter().all(|byte| *byte == 0) || value != self.compute_digest()? {
                    return Err(AuthorityMaintenanceError($error));
                }
                Ok(value)
            }

            pub(super) fn sealed_canonical_bytes(
                &self,
            ) -> Result<Vec<u8>, AuthorityMaintenanceError> {
                self.digest()?;
                serde_json::to_vec(self).map_err(|_| AuthorityMaintenanceError($error))
            }

            pub(super) fn parse_sealed_canonical(
                bytes: &[u8],
            ) -> Result<Self, AuthorityMaintenanceError> {
                if bytes.is_empty() || bytes.len() > MAX_WORKER_RECEIPT_BYTES {
                    return Err(AuthorityMaintenanceError($error));
                }
                let value: Self =
                    serde_json::from_slice(bytes).map_err(|_| AuthorityMaintenanceError($error))?;
                value.digest()?;
                if value.sealed_canonical_bytes()? != bytes {
                    return Err(AuthorityMaintenanceError($error));
                }
                Ok(value)
            }

            fn compute_digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
                let mut unsigned = self.clone();
                unsigned.receipt_sha256.clear();
                let canonical =
                    serde_json::to_vec(&unsigned).map_err(|_| AuthorityMaintenanceError($error))?;
                let mut digest = Sha256::new();
                digest.update($domain);
                digest.update(canonical);
                Ok(digest.finalize().into())
            }
        }
    };
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct AuthorityActionTimeConsent {
    schema: String,
    operation: AuthorityMaintenanceOperation,
    plan_sha256: String,
    generation: String,
    service_sha256: String,
    controller_sha256: String,
    install_helper_sha256: String,
    lifecycle_driver_sha256: String,
    bridge_launcher_sha256: String,
    runtime_source_manifest_sha256: String,
    transaction_nonce_sha256: String,
    created_unix_millis: u64,
    expires_unix_millis: u64,
    approved: bool,
    local_only: bool,
    single_use: bool,
}

impl AuthorityActionTimeConsent {
    pub(super) fn validate_for_operation_binding(
        &self,
        preview: &AuthorityMaintenancePreview,
        content: &AuthorityInstallContent,
        now_unix_millis: u64,
    ) -> Result<ValidatedActionTimeConsent, AuthorityMaintenanceError> {
        let transaction_nonce_sha256 =
            self.validate_for_operation(preview, content, now_unix_millis)?;
        Ok(ValidatedActionTimeConsent {
            transaction_nonce_sha256,
            created_unix_millis: self.created_unix_millis,
            expires_unix_millis: self.expires_unix_millis,
        })
    }

    pub(super) fn validate_for_operation(
        &self,
        preview: &AuthorityMaintenancePreview,
        content: &AuthorityInstallContent,
        now_unix_millis: u64,
    ) -> Result<[u8; 32], AuthorityMaintenanceError> {
        if self.schema != ACTION_TIME_CONSENT_SCHEMA
            || self.operation != preview.operation
            || self.plan_sha256 != preview.plan_sha256
            || self.generation != preview.generation
            || self.service_sha256 != hex_lower(content.service.sha256())
            || self.controller_sha256 != hex_lower(content.controller.sha256())
            || self.install_helper_sha256 != hex_lower(content.install_helper.sha256())
            || self.lifecycle_driver_sha256 != hex_lower(content.lifecycle_driver.sha256())
            || self.bridge_launcher_sha256 != hex_lower(content.bridge_launcher.sha256())
            || self.runtime_source_manifest_sha256
                != hex_lower(content.runtime_source_manifest.sha256())
            || !self.approved
            || !self.local_only
            || !self.single_use
            || self.created_unix_millis == 0
            || self.expires_unix_millis <= self.created_unix_millis
            || self.expires_unix_millis - self.created_unix_millis > MAX_CONSENT_LEASE_MILLIS
            || now_unix_millis < self.created_unix_millis
            || now_unix_millis > self.expires_unix_millis
        {
            return Err(AuthorityMaintenanceError(
                "authority_action_time_consent_invalid",
            ));
        }
        decode_nonzero_hex_32(&self.transaction_nonce_sha256).ok_or(AuthorityMaintenanceError(
            "authority_action_time_consent_invalid",
        ))
    }

    pub(super) fn validate_for_install_binding(
        &self,
        preview: &AuthorityMaintenancePreview,
        content: &AuthorityInstallContent,
        now_unix_millis: u64,
    ) -> Result<ValidatedActionTimeConsent, AuthorityMaintenanceError> {
        if preview.operation != AuthorityMaintenanceOperation::Install {
            return Err(AuthorityMaintenanceError(
                "authority_action_time_consent_invalid",
            ));
        }
        self.validate_for_operation_binding(preview, content, now_unix_millis)
    }

    pub(super) fn validate_for_install(
        &self,
        preview: &AuthorityMaintenancePreview,
        content: &AuthorityInstallContent,
        now_unix_millis: u64,
    ) -> Result<[u8; 32], AuthorityMaintenanceError> {
        if preview.operation != AuthorityMaintenanceOperation::Install {
            return Err(AuthorityMaintenanceError(
                "authority_action_time_consent_invalid",
            ));
        }
        self.validate_for_operation(preview, content, now_unix_millis)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct ValidatedActionTimeConsent {
    pub(super) transaction_nonce_sha256: [u8; 32],
    pub(super) created_unix_millis: u64,
    pub(super) expires_unix_millis: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerPayloadBinding {
    sha256: String,
    byte_length: u64,
    volume_serial: u64,
    file_id: String,
    open_policy_receipt_sha256: String,
    full_readback_receipt_sha256: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct WorkerPayloadSourceExpectation {
    pub(super) descriptor: AuthorityPayloadDigest,
    pub(super) volume_serial: u64,
    pub(super) file_id: [u8; 16],
    pub(super) full_readback_receipt_sha256: [u8; 32],
}

impl WorkerPayloadBinding {
    fn from_verified(value: &VerifiedPayloadHandle) -> Self {
        Self {
            sha256: hex_lower(value.descriptor.sha256()),
            byte_length: value.descriptor.byte_length(),
            volume_serial: value.volume_serial,
            file_id: hex_lower(&value.file_id),
            open_policy_receipt_sha256: hex_lower(&value.open_policy_receipt_sha256),
            full_readback_receipt_sha256: hex_lower(&value.full_readback_receipt_sha256),
        }
    }

    fn is_valid(&self) -> bool {
        decode_nonzero_hex_32(&self.sha256).is_some()
            && self.byte_length > 0
            && self.byte_length <= MAX_AUTHORITY_BINARY_BYTES
            && self.volume_serial != 0
            && decode_hex_16(&self.file_id).is_some()
            && decode_nonzero_hex_32(&self.open_policy_receipt_sha256).is_some()
            && decode_nonzero_hex_32(&self.full_readback_receipt_sha256).is_some()
    }

    fn source_expectation(
        &self,
    ) -> Result<WorkerPayloadSourceExpectation, AuthorityMaintenanceError> {
        Ok(WorkerPayloadSourceExpectation {
            descriptor: AuthorityPayloadDigest::new(
                decode_hex_32(&self.sha256)?,
                self.byte_length,
            )?,
            volume_serial: self.volume_serial,
            file_id: decode_hex_16(&self.file_id).ok_or(AuthorityMaintenanceError(
                "authority_worker_capsule_binding_invalid",
            ))?,
            full_readback_receipt_sha256: decode_hex_32(&self.full_readback_receipt_sha256)?,
        })
    }
}

pub(super) fn worker_bootstrap_file_readback_receipt(
    payload: &str,
    sha256: &[u8; 32],
    byte_length: u64,
    volume_serial: u64,
    file_id: &[u8; 16],
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(WORKER_BOOTSTRAP_FILE_READBACK_DOMAIN);
    digest.update((payload.len() as u64).to_be_bytes());
    digest.update(payload.as_bytes());
    digest.update(sha256);
    digest.update(byte_length.to_be_bytes());
    digest.update(volume_serial.to_be_bytes());
    digest.update(file_id);
    digest.update(1u32.to_be_bytes());
    digest.finalize().into()
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct MaintenanceWorkerCapsule {
    schema: String,
    operation: AuthorityMaintenanceOperation,
    plan_sha256: String,
    generation: String,
    transaction_sha256: String,
    transaction_nonce_sha256: String,
    consent_sha256: String,
    consent_created_unix_millis: u64,
    consent_expires_unix_millis: u64,
    bootstrap_helper_process_id: u32,
    bootstrap_helper_process_creation_time: u64,
    bootstrap_helper_image_volume_serial: u64,
    bootstrap_helper_image_file_id: String,
    bootstrap_helper_binding_sha256: String,
    bootstrap_helper_elevated: bool,
    bootstrap_helper_high_integrity: bool,
    bootstrap_helper_local_system: bool,
    bootstrap_helper_session_id: u32,
    worker_pipe_nonce: String,
    service: WorkerPayloadBinding,
    controller: WorkerPayloadBinding,
    install_helper: WorkerPayloadBinding,
    lifecycle_driver: WorkerPayloadBinding,
    bridge_launcher: WorkerPayloadBinding,
    runtime_source_manifest: WorkerPayloadBinding,
    payload_set_binding_sha256: String,
    worker_service_name: String,
    pub(super) worker_service_account: String,
    worker_start_type: String,
    worker_stop_wait_delete_required: bool,
    source_handle_transfer_required: bool,
    source_paths_persisted: bool,
    private_key_material_persisted: bool,
}

impl MaintenanceWorkerCapsule {
    pub(super) fn for_operation(
        preview: &AuthorityMaintenancePreview,
        lease: &VerifiedMaintenanceLease,
        transaction_nonce_sha256: [u8; 32],
        consent_sha256: [u8; 32],
        consent_created_unix_millis: u64,
        consent_expires_unix_millis: u64,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if !lease.is_live()
            || lease.plan_sha256 != preview.plan_sha256()?
            || lease.generation != preview.generation_sha256()?
            || transaction_nonce_sha256.iter().all(|value| *value == 0)
            || consent_sha256.iter().all(|value| *value == 0)
            || consent_created_unix_millis == 0
            || consent_expires_unix_millis <= consent_created_unix_millis
            || consent_expires_unix_millis - consent_created_unix_millis > MAX_CONSENT_LEASE_MILLIS
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_capsule_binding_invalid",
            ));
        }
        let mut worker_pipe_nonce = [0u8; 32];
        getrandom::fill(&mut worker_pipe_nonce).map_err(|_| {
            AuthorityMaintenanceError("authority_worker_pipe_nonce_generation_failed")
        })?;
        if worker_pipe_nonce.iter().all(|value| *value == 0) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_pipe_nonce_generation_failed",
            ));
        }
        let helper = &lease.bootstrap_helper;
        let capsule = Self {
            schema: MAINTENANCE_WORKER_CAPSULE_SCHEMA.to_string(),
            operation: preview.operation,
            plan_sha256: preview.plan_sha256.clone(),
            generation: preview.generation.clone(),
            transaction_sha256: preview.transaction_sha256.clone(),
            transaction_nonce_sha256: hex_lower(&transaction_nonce_sha256),
            consent_sha256: hex_lower(&consent_sha256),
            consent_created_unix_millis,
            consent_expires_unix_millis,
            bootstrap_helper_process_id: helper.process_id,
            bootstrap_helper_process_creation_time: helper.process_creation_time,
            bootstrap_helper_image_volume_serial: helper.image_volume_serial,
            bootstrap_helper_image_file_id: hex_lower(&helper.image_file_id),
            bootstrap_helper_binding_sha256: hex_lower(&helper.binding_sha256),
            bootstrap_helper_elevated: helper.elevated_token,
            bootstrap_helper_high_integrity: helper.high_integrity,
            bootstrap_helper_local_system: helper.local_system,
            bootstrap_helper_session_id: helper.session_id,
            worker_pipe_nonce: hex_lower(&worker_pipe_nonce),
            service: WorkerPayloadBinding::from_verified(&lease.payloads.service),
            controller: WorkerPayloadBinding::from_verified(&lease.payloads.controller),
            install_helper: WorkerPayloadBinding::from_verified(&lease.payloads.install_helper),
            lifecycle_driver: WorkerPayloadBinding::from_verified(&lease.payloads.lifecycle_driver),
            bridge_launcher: WorkerPayloadBinding::from_verified(&lease.payloads.bridge_launcher),
            runtime_source_manifest: WorkerPayloadBinding::from_verified(
                &lease.payloads.runtime_source_manifest,
            ),
            payload_set_binding_sha256: hex_lower(&lease.payloads.binding_sha256),
            worker_service_name: MAINTENANCE_WORKER_SERVICE_NAME.to_string(),
            worker_service_account: "LocalSystem".to_string(),
            worker_start_type: "demand".to_string(),
            worker_stop_wait_delete_required: true,
            source_handle_transfer_required: true,
            source_paths_persisted: false,
            private_key_material_persisted: false,
        };
        capsule.validate()?;
        Ok(capsule)
    }

    pub(super) fn for_install(
        preview: &AuthorityMaintenancePreview,
        lease: &VerifiedMaintenanceLease,
        transaction_nonce_sha256: [u8; 32],
        consent_sha256: [u8; 32],
        consent_created_unix_millis: u64,
        consent_expires_unix_millis: u64,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if preview.operation != AuthorityMaintenanceOperation::Install {
            return Err(AuthorityMaintenanceError(
                "authority_worker_capsule_binding_invalid",
            ));
        }
        Self::for_operation(
            preview,
            lease,
            transaction_nonce_sha256,
            consent_sha256,
            consent_created_unix_millis,
            consent_expires_unix_millis,
        )
    }

    pub(super) fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if self.schema != MAINTENANCE_WORKER_CAPSULE_SCHEMA
            || decode_nonzero_hex_32(&self.plan_sha256).is_none()
            || decode_nonzero_hex_32(&self.generation).is_none()
            || decode_nonzero_hex_32(&self.transaction_sha256).is_none()
            || decode_nonzero_hex_32(&self.transaction_nonce_sha256).is_none()
            || decode_nonzero_hex_32(&self.consent_sha256).is_none()
            || self.consent_created_unix_millis == 0
            || self.consent_expires_unix_millis <= self.consent_created_unix_millis
            || self.consent_expires_unix_millis - self.consent_created_unix_millis
                > MAX_CONSENT_LEASE_MILLIS
            || self.bootstrap_helper_process_id == 0
            || self.bootstrap_helper_process_creation_time == 0
            || self.bootstrap_helper_image_volume_serial == 0
            || decode_hex_16(&self.bootstrap_helper_image_file_id).is_none()
            || decode_nonzero_hex_32(&self.bootstrap_helper_binding_sha256).is_none()
            || !self.bootstrap_helper_elevated
            || !self.bootstrap_helper_high_integrity
            || self.bootstrap_helper_local_system
            || self.bootstrap_helper_session_id == 0
            || decode_nonzero_hex_32(&self.worker_pipe_nonce).is_none()
            || !self.service.is_valid()
            || !self.controller.is_valid()
            || !self.install_helper.is_valid()
            || !self.lifecycle_driver.is_valid()
            || !self.bridge_launcher.is_valid()
            || !self.runtime_source_manifest.is_valid()
            || self.runtime_source_manifest.byte_length > MAX_RUNTIME_SOURCE_MANIFEST_BYTES
            || decode_nonzero_hex_32(&self.payload_set_binding_sha256).is_none()
            || self.worker_service_name != MAINTENANCE_WORKER_SERVICE_NAME
            || self.worker_service_account != "LocalSystem"
            || self.worker_start_type != "demand"
            || !self.worker_stop_wait_delete_required
            || !self.source_handle_transfer_required
            || self.source_paths_persisted
            || self.private_key_material_persisted
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_capsule_binding_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn canonical_bytes(&self) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        self.validate()?;
        serde_json::to_vec(self)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_capsule_encode_failed"))
    }

    pub(super) fn parse_canonical(
        bytes: &[u8],
        expected_digest: &[u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        if bytes.is_empty() || bytes.len() > MAX_WORKER_RECEIPT_BYTES {
            return Err(AuthorityMaintenanceError(
                "authority_worker_capsule_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_capsule_invalid"))?;
        value.validate()?;
        if value.canonical_bytes()? != bytes || &value.digest()? != expected_digest {
            return Err(AuthorityMaintenanceError(
                "authority_worker_capsule_not_canonical",
            ));
        }
        Ok(value)
    }

    pub(super) fn digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        let mut digest = Sha256::new();
        digest.update(WORKER_CAPSULE_DOMAIN);
        digest.update(self.canonical_bytes()?);
        Ok(digest.finalize().into())
    }

    pub(super) fn generation(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.generation)
    }

    pub(super) fn operation(&self) -> AuthorityMaintenanceOperation {
        self.operation
    }

    pub(super) fn plan_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.plan_sha256)
    }

    pub(super) fn transaction_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.transaction_sha256)
    }

    pub(super) fn install_helper_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.install_helper.sha256)
    }

    pub(super) fn install_helper_byte_length(&self) -> u64 {
        self.install_helper.byte_length
    }

    pub(super) fn validate_consent_at(
        &self,
        now_unix_millis: u64,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.validate()?;
        if now_unix_millis < self.consent_created_unix_millis
            || now_unix_millis > self.consent_expires_unix_millis
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_action_time_consent_expired",
            ));
        }
        Ok(())
    }

    pub(super) fn transaction_nonce_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.transaction_nonce_sha256)
    }

    pub(super) fn consent_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.consent_sha256)
    }

    pub(super) fn consent_expires_unix_millis(&self) -> u64 {
        self.consent_expires_unix_millis
    }

    pub(super) fn worker_pipe_nonce(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.worker_pipe_nonce)
    }

    fn helper_process_matches(&self, helper: &WorkerProcessBinding) -> bool {
        helper.process_id == self.bootstrap_helper_process_id
            && helper.process_creation_time == self.bootstrap_helper_process_creation_time
            && helper.image_sha256 == self.install_helper.sha256
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn validate_live_helper_identity(
        &self,
        helper: &WorkerProcessBinding,
        image_byte_length: u64,
        image_volume_serial: u64,
        image_file_id: [u8; 16],
        elevated: bool,
        high_integrity: bool,
        local_system: bool,
        session_id: u32,
    ) -> Result<(), AuthorityMaintenanceError> {
        if !self.helper_process_matches(helper)
            || image_byte_length != self.install_helper.byte_length
            || image_volume_serial != self.bootstrap_helper_image_volume_serial
            || Some(image_file_id) != decode_hex_16(&self.bootstrap_helper_image_file_id)
            || !elevated
            || !high_integrity
            || local_system
            || session_id == 0
            || session_id != self.bootstrap_helper_session_id
        {
            return Err(AuthorityMaintenanceError(
                "authority_action_time_helper_identity_mismatch",
            ));
        }
        Ok(())
    }

    pub(super) fn payload_source_expectation(
        &self,
        kind: StagedPayloadKind,
    ) -> Result<WorkerPayloadSourceExpectation, AuthorityMaintenanceError> {
        kind.source(self).source_expectation()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerBootstrapIntentReceipt {
    schema: String,
    capsule_sha256: String,
    launch_contract_sha256: String,
    state_directory_create_new: bool,
    state_directory_never_reused: bool,
    journal_create_new: bool,
    journal_write_through_required: bool,
    journal_file_flush_required: bool,
    journal_parent_flush_required: bool,
    binary_mutation_started: bool,
    receipt_sha256: String,
}

impl WorkerBootstrapIntentReceipt {
    pub(super) fn new(
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let mut value = Self {
            schema: WORKER_BOOTSTRAP_INTENT_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            launch_contract_sha256: hex_lower(&launch.digest()?),
            state_directory_create_new: true,
            state_directory_never_reused: true,
            journal_create_new: true,
            journal_write_through_required: true,
            journal_file_flush_required: true,
            journal_parent_flush_required: true,
            binary_mutation_started: false,
            receipt_sha256: String::new(),
        };
        value.receipt_sha256 = hex_lower(&value.compute_digest()?);
        value.validate(capsule, launch)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.schema != WORKER_BOOTSTRAP_INTENT_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.launch_contract_sha256)? != launch.digest()?
            || launch.capsule_sha256()? != capsule.digest()?
            || !self.state_directory_create_new
            || !self.state_directory_never_reused
            || !self.journal_create_new
            || !self.journal_write_through_required
            || !self.journal_file_flush_required
            || !self.journal_parent_flush_required
            || self.binary_mutation_started
            || decode_hex_32(&self.receipt_sha256)? != self.compute_digest()?
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_bootstrap_intent_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn canonical_bytes(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
    ) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        self.validate(capsule, launch)?;
        serde_json::to_vec(self).map_err(|_| {
            AuthorityMaintenanceError("authority_worker_bootstrap_intent_encode_failed")
        })
    }

    pub(super) fn parse_canonical(
        bytes: &[u8],
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if bytes.is_empty() || bytes.len() > MAX_WORKER_RECEIPT_BYTES {
            return Err(AuthorityMaintenanceError(
                "authority_worker_bootstrap_intent_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_bootstrap_intent_invalid"))?;
        value.validate(capsule, launch)?;
        if value.canonical_bytes(capsule, launch)? != bytes {
            return Err(AuthorityMaintenanceError(
                "authority_worker_bootstrap_intent_not_canonical",
            ));
        }
        Ok(value)
    }

    pub(super) fn digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        let value = decode_hex_32(&self.receipt_sha256)?;
        if value.iter().all(|byte| *byte == 0) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_bootstrap_intent_invalid",
            ));
        }
        Ok(value)
    }

    fn compute_digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        let mut unsigned = self.clone();
        unsigned.receipt_sha256.clear();
        let canonical = serde_json::to_vec(&unsigned).map_err(|_| {
            AuthorityMaintenanceError("authority_worker_bootstrap_intent_encode_failed")
        })?;
        let mut digest = Sha256::new();
        digest.update(WORKER_BOOTSTRAP_INTENT_DOMAIN);
        digest.update(canonical);
        Ok(digest.finalize().into())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerBootstrapStagedFileBinding {
    payload: String,
    relative_name: String,
    sha256: String,
    byte_length: u64,
    volume_serial: u64,
    file_id: String,
    full_readback_receipt_sha256: String,
    create_new: bool,
    never_reuse: bool,
    local_volume: bool,
    reparse_rejected: bool,
    single_link: bool,
    exact_owner_and_acl: bool,
    file_flushed_before_readback: bool,
    stable_identity_reverified: bool,
}

impl WorkerBootstrapStagedFileBinding {
    pub(super) fn from_observed(
        payload: &str,
        relative_name: &str,
        sha256: [u8; 32],
        byte_length: u64,
        volume_serial: u64,
        file_id: [u8; 16],
        full_readback_receipt_sha256: [u8; 32],
    ) -> Self {
        Self {
            payload: payload.to_string(),
            relative_name: relative_name.to_string(),
            sha256: hex_lower(&sha256),
            byte_length,
            volume_serial,
            file_id: hex_lower(&file_id),
            full_readback_receipt_sha256: hex_lower(&full_readback_receipt_sha256),
            create_new: true,
            never_reuse: true,
            local_volume: true,
            reparse_rejected: true,
            single_link: true,
            exact_owner_and_acl: true,
            file_flushed_before_readback: true,
            stable_identity_reverified: true,
        }
    }

    fn validates_for(
        &self,
        payload: &str,
        relative_name: &str,
        sha256: &[u8; 32],
        byte_length: u64,
    ) -> bool {
        self.payload == payload
            && self.relative_name == relative_name
            && decode_nonzero_hex_32(&self.sha256).as_ref() == Some(sha256)
            && self.byte_length == byte_length
            && self.byte_length > 0
            && self.byte_length <= MAX_AUTHORITY_BINARY_BYTES
            && self.volume_serial != 0
            && decode_hex_16(&self.file_id).is_some()
            && decode_hex_32(&self.full_readback_receipt_sha256).ok()
                == Some(worker_bootstrap_file_readback_receipt(
                    payload,
                    sha256,
                    byte_length,
                    self.volume_serial,
                    &decode_hex_16(&self.file_id).unwrap_or_default(),
                ))
            && self.create_new
            && self.never_reuse
            && self.local_volume
            && self.reparse_rejected
            && self.single_link
            && self.exact_owner_and_acl
            && self.file_flushed_before_readback
            && self.stable_identity_reverified
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerBootstrapStagingReceipt {
    schema: String,
    capsule_sha256: String,
    launch_contract_sha256: String,
    binary_directory_volume_serial: u64,
    binary_directory_file_id: String,
    state_directory_volume_serial: u64,
    state_directory_file_id: String,
    worker: WorkerBootstrapStagedFileBinding,
    capsule: WorkerBootstrapStagedFileBinding,
    directories_create_new: bool,
    directories_never_reused: bool,
    directories_local_volume: bool,
    directories_reparse_rejected: bool,
    directories_exact_owner_and_acl: bool,
    directories_flushed_after_create: bool,
    receipt_sha256: String,
}

impl WorkerBootstrapStagingReceipt {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        binary_directory_volume_serial: u64,
        binary_directory_file_id: [u8; 16],
        state_directory_volume_serial: u64,
        state_directory_file_id: [u8; 16],
        worker: WorkerBootstrapStagedFileBinding,
        capsule_file: WorkerBootstrapStagedFileBinding,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let mut value = Self {
            schema: WORKER_BOOTSTRAP_STAGING_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            launch_contract_sha256: hex_lower(&launch.digest()?),
            binary_directory_volume_serial,
            binary_directory_file_id: hex_lower(&binary_directory_file_id),
            state_directory_volume_serial,
            state_directory_file_id: hex_lower(&state_directory_file_id),
            worker,
            capsule: capsule_file,
            directories_create_new: true,
            directories_never_reused: true,
            directories_local_volume: true,
            directories_reparse_rejected: true,
            directories_exact_owner_and_acl: true,
            directories_flushed_after_create: true,
            receipt_sha256: String::new(),
        };
        value.receipt_sha256 = hex_lower(&value.compute_digest()?);
        value.validate(capsule, launch)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
    ) -> Result<(), AuthorityMaintenanceError> {
        let capsule_bytes = capsule.canonical_bytes()?;
        let capsule_file_sha256: [u8; 32] = Sha256::digest(&capsule_bytes).into();
        if self.schema != WORKER_BOOTSTRAP_STAGING_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.launch_contract_sha256)? != launch.digest()?
            || launch.capsule_sha256()? != capsule.digest()?
            || self.binary_directory_volume_serial == 0
            || decode_hex_16(&self.binary_directory_file_id).is_none()
            || self.state_directory_volume_serial == 0
            || decode_hex_16(&self.state_directory_file_id).is_none()
            || !self.worker.validates_for(
                "install-helper",
                "vrcforge_primitive_evidence_install_helper.exe",
                &capsule.install_helper_sha256()?,
                capsule.install_helper.byte_length,
            )
            || !self.capsule.validates_for(
                "capsule",
                "capsule.json",
                &capsule_file_sha256,
                capsule_bytes.len() as u64,
            )
            || !self.directories_create_new
            || !self.directories_never_reused
            || !self.directories_local_volume
            || !self.directories_reparse_rejected
            || !self.directories_exact_owner_and_acl
            || !self.directories_flushed_after_create
            || decode_hex_32(&self.receipt_sha256)? != self.compute_digest()?
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_bootstrap_staging_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn canonical_bytes(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
    ) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        self.validate(capsule, launch)?;
        serde_json::to_vec(self).map_err(|_| {
            AuthorityMaintenanceError("authority_worker_bootstrap_staging_encode_failed")
        })
    }

    pub(super) fn parse_canonical(
        bytes: &[u8],
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if bytes.is_empty() || bytes.len() > MAX_WORKER_RECEIPT_BYTES {
            return Err(AuthorityMaintenanceError(
                "authority_worker_bootstrap_staging_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_bootstrap_staging_invalid"))?;
        value.validate(capsule, launch)?;
        if value.canonical_bytes(capsule, launch)? != bytes {
            return Err(AuthorityMaintenanceError(
                "authority_worker_bootstrap_staging_not_canonical",
            ));
        }
        Ok(value)
    }

    pub(super) fn digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        let value = decode_hex_32(&self.receipt_sha256)?;
        if value.iter().all(|byte| *byte == 0) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_bootstrap_staging_invalid",
            ));
        }
        Ok(value)
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn validate_reopened_files(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        binary_directory_volume_serial: u64,
        binary_directory_file_id: [u8; 16],
        state_directory_volume_serial: u64,
        state_directory_file_id: [u8; 16],
        worker: &WorkerBootstrapStagedFileBinding,
        capsule_file: &WorkerBootstrapStagedFileBinding,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.validate(capsule, launch)?;
        if self.binary_directory_volume_serial != binary_directory_volume_serial
            || decode_hex_16(&self.binary_directory_file_id) != Some(binary_directory_file_id)
            || self.state_directory_volume_serial != state_directory_volume_serial
            || decode_hex_16(&self.state_directory_file_id) != Some(state_directory_file_id)
            || &self.worker != worker
            || &self.capsule != capsule_file
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_bootstrap_staging_readback_mismatch",
            ));
        }
        Ok(())
    }

    fn compute_digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        let mut unsigned = self.clone();
        unsigned.receipt_sha256.clear();
        let canonical = serde_json::to_vec(&unsigned).map_err(|_| {
            AuthorityMaintenanceError("authority_worker_bootstrap_staging_encode_failed")
        })?;
        let mut digest = Sha256::new();
        digest.update(WORKER_BOOTSTRAP_STAGING_DOMAIN);
        digest.update(canonical);
        Ok(digest.finalize().into())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerProcessBinding {
    process_id: u32,
    process_creation_time: u64,
    image_sha256: String,
}

impl WorkerProcessBinding {
    pub(super) fn new(process_id: u32, process_creation_time: u64, image_sha256: [u8; 32]) -> Self {
        Self {
            process_id,
            process_creation_time,
            image_sha256: hex_lower(&image_sha256),
        }
    }

    fn is_valid_for_helper(&self, capsule: &MaintenanceWorkerCapsule) -> bool {
        self.process_id != 0
            && self.process_creation_time != 0
            && self.image_sha256 == capsule.install_helper.sha256
    }

    pub(super) fn process_id(&self) -> u32 {
        self.process_id
    }

    pub(super) fn process_creation_time(&self) -> u64 {
        self.process_creation_time
    }

    pub(super) fn image_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.image_sha256)
    }
}

#[derive(Debug, Default, PartialEq, Eq)]
pub(super) struct OneShotDuplicatedHandleValues {
    pending: Option<[u64; PROTECTED_GENERATION_PAYLOAD_COUNT]>,
    adopted: bool,
}

impl OneShotDuplicatedHandleValues {
    pub(super) fn arm(
        &mut self,
        values: [u64; PROTECTED_GENERATION_PAYLOAD_COUNT],
    ) -> Result<(), AuthorityMaintenanceError> {
        let distinct = values
            .iter()
            .enumerate()
            .all(|(index, value)| *value != 0 && !values[..index].contains(value));
        if !distinct || self.pending.is_some() || self.adopted {
            return Err(AuthorityMaintenanceError(
                "authority_worker_duplicated_handles_already_armed",
            ));
        }
        self.pending = Some(values);
        Ok(())
    }

    pub(super) fn take(
        &mut self,
    ) -> Result<[u64; PROTECTED_GENERATION_PAYLOAD_COUNT], AuthorityMaintenanceError> {
        let values = self.pending.take().ok_or(AuthorityMaintenanceError(
            "authority_worker_duplicated_handles_already_adopted",
        ))?;
        self.adopted = true;
        Ok(values)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DurableReceiptPersistence {
    create_new: bool,
    never_reuse: bool,
    protected_relative_handle_chain: bool,
    exact_owner_and_acl: bool,
    write_through: bool,
    file_flushed: bool,
    parent_flushed: bool,
    stable_readback: bool,
}

impl DurableReceiptPersistence {
    fn exact() -> Self {
        Self {
            create_new: true,
            never_reuse: true,
            protected_relative_handle_chain: true,
            exact_owner_and_acl: true,
            write_through: true,
            file_flushed: true,
            parent_flushed: true,
            stable_readback: true,
        }
    }

    fn is_exact(&self) -> bool {
        self == &Self::exact()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerPipePreparedReceipt {
    schema: String,
    capsule_sha256: String,
    launch_contract_sha256: String,
    pipe_name: String,
    pipe_nonce_sha256: String,
    pipe_instance_id: String,
    helper: WorkerProcessBinding,
    helper_image_volume_serial: u64,
    helper_image_file_id: String,
    helper_bootstrap_binding_sha256: String,
    helper_elevated: bool,
    helper_high_integrity: bool,
    helper_local_system: bool,
    helper_session_id: u32,
    first_pipe_instance: bool,
    reject_remote_clients: bool,
    server_handle_held: bool,
    server_created_before_service: bool,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl WorkerPipePreparedReceipt {
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        helper: WorkerProcessBinding,
        pipe_instance_id: [u8; 16],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let mut value = Self {
            schema: WORKER_PIPE_PREPARED_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            launch_contract_sha256: hex_lower(&launch.digest()?),
            pipe_name: worker_handoff_pipe_name(capsule)?,
            pipe_nonce_sha256: hex_lower(&capsule.worker_pipe_nonce()?),
            pipe_instance_id: hex_lower(&pipe_instance_id),
            helper,
            helper_image_volume_serial: capsule.bootstrap_helper_image_volume_serial,
            helper_image_file_id: capsule.bootstrap_helper_image_file_id.clone(),
            helper_bootstrap_binding_sha256: capsule.bootstrap_helper_binding_sha256.clone(),
            helper_elevated: capsule.bootstrap_helper_elevated,
            helper_high_integrity: capsule.bootstrap_helper_high_integrity,
            helper_local_system: capsule.bootstrap_helper_local_system,
            helper_session_id: capsule.bootstrap_helper_session_id,
            first_pipe_instance: true,
            reject_remote_clients: true,
            server_handle_held: true,
            server_created_before_service: true,
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(capsule, launch)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.schema != WORKER_PIPE_PREPARED_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.launch_contract_sha256)? != launch.digest()?
            || self.pipe_name != worker_handoff_pipe_name(capsule)?
            || decode_hex_32(&self.pipe_nonce_sha256)? != capsule.worker_pipe_nonce()?
            || decode_hex_16(&self.pipe_instance_id).is_none()
            || !capsule.helper_process_matches(&self.helper)
            || self.helper_image_volume_serial != capsule.bootstrap_helper_image_volume_serial
            || self.helper_image_file_id != capsule.bootstrap_helper_image_file_id
            || self.helper_bootstrap_binding_sha256 != capsule.bootstrap_helper_binding_sha256
            || !self.helper_elevated
            || !self.helper_high_integrity
            || self.helper_local_system
            || self.helper_session_id == 0
            || !self.first_pipe_instance
            || !self.reject_remote_clients
            || !self.server_handle_held
            || !self.server_created_before_service
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_pipe_prepared_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn pipe_instance_id(&self) -> Result<[u8; 16], AuthorityMaintenanceError> {
        decode_hex_16(&self.pipe_instance_id).ok_or(AuthorityMaintenanceError(
            "authority_worker_pipe_prepared_invalid",
        ))
    }

    pub(super) fn canonical_bytes(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
    ) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        self.validate(capsule, launch)?;
        serde_json::to_vec(self)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_pipe_prepared_invalid"))
    }
}

impl_worker_receipt_digest!(
    WorkerPipePreparedReceipt,
    WORKER_PIPE_PREPARED_DOMAIN,
    "authority_worker_pipe_prepared_invalid"
);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerPipeRecoveryReceipt {
    schema: String,
    capsule_sha256: String,
    launch_contract_sha256: String,
    durable_journal_head_sha256: String,
    prior_pipe_prepared_receipt_sha256: String,
    prior_pipe_instance_id: String,
    replacement_pipe: WorkerPipePreparedReceipt,
    service_absence_readback_sha256: String,
    prior_server_handle_closed: bool,
    service_was_never_created: bool,
    replacement_is_new_first_instance: bool,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl WorkerPipeRecoveryReceipt {
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        records: &[MaintenanceWorkerJournalRecord],
        prior: &WorkerPipePreparedReceipt,
        replacement: &WorkerPipePreparedReceipt,
        service_absence_readback_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let head = records.last().ok_or(AuthorityMaintenanceError(
            "authority_worker_pipe_recovery_phase_invalid",
        ))?;
        let mut value = Self {
            schema: WORKER_PIPE_RECOVERY_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            launch_contract_sha256: hex_lower(&launch.digest()?),
            durable_journal_head_sha256: head.record_sha256.clone(),
            prior_pipe_prepared_receipt_sha256: hex_lower(&prior.digest()?),
            prior_pipe_instance_id: hex_lower(&prior.pipe_instance_id()?),
            replacement_pipe: replacement.clone(),
            service_absence_readback_sha256: hex_lower(&service_absence_readback_sha256),
            prior_server_handle_closed: true,
            service_was_never_created: true,
            replacement_is_new_first_instance: true,
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(capsule, launch, records, prior)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        records: &[MaintenanceWorkerJournalRecord],
        prior: &WorkerPipePreparedReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        prior.validate(capsule, launch)?;
        self.replacement_pipe.validate(capsule, launch)?;
        validate_worker_journal(capsule.digest()?, records)?;
        let head = records
            .iter()
            .find(|record| record.record_sha256 == self.durable_journal_head_sha256)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_pipe_recovery_phase_invalid",
            ))?;
        if !matches!(
            head.phase,
            MaintenanceWorkerPhase::CapsuleStaged | MaintenanceWorkerPhase::PipePrepared
        ) || self.schema != WORKER_PIPE_RECOVERY_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.launch_contract_sha256)? != launch.digest()?
            || self.durable_journal_head_sha256 != head.record_sha256
            || decode_hex_32(&self.prior_pipe_prepared_receipt_sha256)? != prior.digest()?
            || decode_hex_16(&self.prior_pipe_instance_id) != Some(prior.pipe_instance_id()?)
            || self.replacement_pipe.pipe_instance_id()? == prior.pipe_instance_id()?
            || decode_nonzero_hex_32(&self.service_absence_readback_sha256).is_none()
            || !self.prior_server_handle_closed
            || !self.service_was_never_created
            || !self.replacement_is_new_first_instance
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_pipe_recovery_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn replacement_pipe(&self) -> &WorkerPipePreparedReceipt {
        &self.replacement_pipe
    }
}

impl_worker_receipt_digest!(
    WorkerPipeRecoveryReceipt,
    WORKER_PIPE_RECOVERY_DOMAIN,
    "authority_worker_pipe_recovery_invalid"
);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct ServiceCreatedReceipt {
    schema: String,
    capsule_sha256: String,
    launch_contract_sha256: String,
    bootstrap_staging_receipt_sha256: String,
    pipe_prepared_receipt_sha256: String,
    scm_configuration_readback_sha256: String,
    service_security_readback_sha256: String,
    pipe_server_preexisted_service: bool,
    exact_binary_command: bool,
    exact_local_system_account: bool,
    exact_demand_start: bool,
    exact_service_security: bool,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl ServiceCreatedReceipt {
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        bootstrap: &WorkerBootstrapStagingReceipt,
        pipe: &WorkerPipePreparedReceipt,
        scm_configuration_readback_sha256: [u8; 32],
        service_security_readback_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let mut value = Self {
            schema: WORKER_SERVICE_CREATED_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            launch_contract_sha256: hex_lower(&launch.digest()?),
            bootstrap_staging_receipt_sha256: hex_lower(&bootstrap.digest()?),
            pipe_prepared_receipt_sha256: hex_lower(&pipe.digest()?),
            scm_configuration_readback_sha256: hex_lower(&scm_configuration_readback_sha256),
            service_security_readback_sha256: hex_lower(&service_security_readback_sha256),
            pipe_server_preexisted_service: true,
            exact_binary_command: true,
            exact_local_system_account: true,
            exact_demand_start: true,
            exact_service_security: true,
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(capsule, launch, bootstrap, pipe)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        bootstrap: &WorkerBootstrapStagingReceipt,
        pipe: &WorkerPipePreparedReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        pipe.validate(capsule, launch)?;
        bootstrap.validate(capsule, launch)?;
        if self.schema != WORKER_SERVICE_CREATED_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.launch_contract_sha256)? != launch.digest()?
            || decode_hex_32(&self.bootstrap_staging_receipt_sha256)? != bootstrap.digest()?
            || decode_hex_32(&self.pipe_prepared_receipt_sha256)? != pipe.digest()?
            || decode_nonzero_hex_32(&self.scm_configuration_readback_sha256).is_none()
            || decode_nonzero_hex_32(&self.service_security_readback_sha256).is_none()
            || !self.pipe_server_preexisted_service
            || !self.exact_binary_command
            || !self.exact_local_system_account
            || !self.exact_demand_start
            || !self.exact_service_security
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_service_created_receipt_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn pipe_prepared_receipt_sha256(
        &self,
    ) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.pipe_prepared_receipt_sha256)
    }

    pub(super) fn scm_configuration_readback_sha256(
        &self,
    ) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.scm_configuration_readback_sha256)
    }

    pub(super) fn service_security_readback_sha256(
        &self,
    ) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.service_security_readback_sha256)
    }
}

impl_worker_receipt_digest!(
    ServiceCreatedReceipt,
    WORKER_SERVICE_CREATED_DOMAIN,
    "authority_worker_service_created_receipt_invalid"
);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerInvocationClaimReceipt {
    schema: String,
    capsule_sha256: String,
    service_created_receipt_sha256: String,
    pipe_prepared_receipt_sha256: String,
    handoff_receipt_sha256: String,
    worker: WorkerProcessBinding,
    pipe_instance_id: String,
    first_invocation_only: bool,
    no_reopen_or_process_replacement: bool,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl WorkerInvocationClaimReceipt {
    pub(super) fn new(
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        bootstrap: &WorkerBootstrapStagingReceipt,
        service_created: &ServiceCreatedReceipt,
        pipe: &WorkerPipePreparedReceipt,
        handoff: &WorkerHandleHandoffReceipt,
        worker: &WorkerProcessBinding,
    ) -> Result<Self, AuthorityMaintenanceError> {
        service_created.validate(capsule, launch, bootstrap, pipe)?;
        handoff.validate_with_pipe(capsule, launch, pipe)?;
        if handoff.worker() != worker {
            return Err(AuthorityMaintenanceError(
                "authority_worker_invocation_claim_invalid",
            ));
        }
        let mut value = Self {
            schema: WORKER_INVOCATION_CLAIM_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            service_created_receipt_sha256: hex_lower(&service_created.digest()?),
            pipe_prepared_receipt_sha256: hex_lower(&pipe.digest()?),
            handoff_receipt_sha256: hex_lower(&handoff.digest()?),
            worker: worker.clone(),
            pipe_instance_id: hex_lower(&pipe.pipe_instance_id()?),
            first_invocation_only: true,
            no_reopen_or_process_replacement: true,
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(
            capsule,
            launch,
            bootstrap,
            service_created,
            pipe,
            handoff,
            worker,
        )?;
        Ok(value)
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        bootstrap: &WorkerBootstrapStagingReceipt,
        service_created: &ServiceCreatedReceipt,
        pipe: &WorkerPipePreparedReceipt,
        handoff: &WorkerHandleHandoffReceipt,
        worker: &WorkerProcessBinding,
    ) -> Result<(), AuthorityMaintenanceError> {
        service_created.validate(capsule, launch, bootstrap, pipe)?;
        handoff.validate_with_pipe(capsule, launch, pipe)?;
        if self.schema != WORKER_INVOCATION_CLAIM_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.service_created_receipt_sha256)? != service_created.digest()?
            || decode_hex_32(&self.pipe_prepared_receipt_sha256)? != pipe.digest()?
            || decode_hex_32(&self.handoff_receipt_sha256)? != handoff.digest()?
            || &self.worker != worker
            || handoff.worker() != worker
            || decode_hex_16(&self.pipe_instance_id) != Some(pipe.pipe_instance_id()?)
            || !self.first_invocation_only
            || !self.no_reopen_or_process_replacement
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_invocation_claim_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn worker(&self) -> &WorkerProcessBinding {
        &self.worker
    }
}

impl_worker_receipt_digest!(
    WorkerInvocationClaimReceipt,
    WORKER_INVOCATION_CLAIM_DOMAIN,
    "authority_worker_invocation_claim_invalid"
);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerStartedReceipt {
    schema: String,
    capsule_sha256: String,
    service_created_receipt_sha256: String,
    pipe_prepared_receipt_sha256: String,
    handoff_receipt_sha256: String,
    scm_worker_process_id: u32,
    worker_process_creation_time: u64,
    worker_image_sha256: String,
    worker_image_byte_length: u64,
    worker_image_volume_serial: u64,
    worker_image_file_id: String,
    worker_image_readback_receipt_sha256: String,
    worker_local_system: bool,
    worker_high_integrity: bool,
    worker_session_id: u32,
    worker_process_active: bool,
    worker_image_handle_held: bool,
    pipe_instance_id: String,
    pipe_first_instance: bool,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl WorkerStartedReceipt {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        bootstrap: &WorkerBootstrapStagingReceipt,
        service_created: &ServiceCreatedReceipt,
        pipe: &WorkerPipePreparedReceipt,
        handoff: &WorkerHandleHandoffReceipt,
        worker_local_system: bool,
        worker_high_integrity: bool,
        worker_session_id: u32,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let worker = handoff.worker();
        let mut value = Self {
            schema: WORKER_STARTED_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            service_created_receipt_sha256: hex_lower(&service_created.digest()?),
            pipe_prepared_receipt_sha256: hex_lower(&pipe.digest()?),
            handoff_receipt_sha256: hex_lower(&handoff.digest()?),
            scm_worker_process_id: worker.process_id(),
            worker_process_creation_time: worker.process_creation_time(),
            worker_image_sha256: hex_lower(&worker.image_sha256()?),
            worker_image_byte_length: bootstrap.worker.byte_length,
            worker_image_volume_serial: bootstrap.worker.volume_serial,
            worker_image_file_id: bootstrap.worker.file_id.clone(),
            worker_image_readback_receipt_sha256: bootstrap
                .worker
                .full_readback_receipt_sha256
                .clone(),
            worker_local_system,
            worker_high_integrity,
            worker_session_id,
            worker_process_active: true,
            worker_image_handle_held: true,
            pipe_instance_id: hex_lower(&pipe.pipe_instance_id()?),
            pipe_first_instance: true,
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(capsule, bootstrap, service_created, pipe, handoff)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        bootstrap: &WorkerBootstrapStagingReceipt,
        service_created: &ServiceCreatedReceipt,
        pipe: &WorkerPipePreparedReceipt,
        handoff: &WorkerHandleHandoffReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        let worker = handoff.worker();
        if self.schema != WORKER_STARTED_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.service_created_receipt_sha256)? != service_created.digest()?
            || decode_hex_32(&self.pipe_prepared_receipt_sha256)? != pipe.digest()?
            || decode_hex_32(&self.handoff_receipt_sha256)? != handoff.digest()?
            || self.scm_worker_process_id != worker.process_id()
            || self.worker_process_creation_time != worker.process_creation_time()
            || decode_hex_32(&self.worker_image_sha256)? != worker.image_sha256()?
            || self.worker_image_sha256 != bootstrap.worker.sha256
            || self.worker_image_byte_length != bootstrap.worker.byte_length
            || self.worker_image_volume_serial != bootstrap.worker.volume_serial
            || self.worker_image_file_id != bootstrap.worker.file_id
            || self.worker_image_readback_receipt_sha256
                != bootstrap.worker.full_readback_receipt_sha256
            || !self.worker_local_system
            || !self.worker_high_integrity
            || self.worker_session_id != 0
            || !self.worker_process_active
            || !self.worker_image_handle_held
            || decode_hex_16(&self.pipe_instance_id) != Some(pipe.pipe_instance_id()?)
            || !self.pipe_first_instance
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_started_receipt_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn pipe_instance_id(&self) -> Result<[u8; 16], AuthorityMaintenanceError> {
        decode_hex_16(&self.pipe_instance_id).ok_or(AuthorityMaintenanceError(
            "authority_worker_started_receipt_invalid",
        ))
    }

    pub(super) fn matches_candidate_process_evidence(
        &self,
        evidence: &CandidateProcessEvidence,
    ) -> Result<bool, AuthorityMaintenanceError> {
        evidence
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        Ok(evidence.process_id() == self.scm_worker_process_id
            && evidence.process_creation_time() == self.worker_process_creation_time
            && *evidence.image_sha256() == decode_hex_32(&self.worker_image_sha256)?
            && evidence.image_byte_length() == self.worker_image_byte_length
            && evidence.image_volume_serial() == self.worker_image_volume_serial
            && Some(*evidence.image_file_id()) == decode_hex_16(&self.worker_image_file_id))
    }
}

impl_worker_receipt_digest!(
    WorkerStartedReceipt,
    WORKER_STARTED_DOMAIN,
    "authority_worker_started_receipt_invalid"
);

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct WorkerLiveReadback {
    pub(super) process_id: u32,
    pub(super) process_creation_time: u64,
    pub(super) image_sha256: [u8; 32],
    pub(super) image_byte_length: u64,
    pub(super) image_volume_serial: u64,
    pub(super) image_file_id: [u8; 16],
    pub(super) image_readback_receipt_sha256: [u8; 32],
    pub(super) local_system: bool,
    pub(super) high_integrity: bool,
    pub(super) session_id: u32,
    pub(super) process_active: bool,
    pub(super) pipe_instance_id: [u8; 16],
}

impl WorkerLiveReadback {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn from_observed(
        started: &WorkerStartedReceipt,
        process_id: u32,
        process_creation_time: u64,
        image_sha256: [u8; 32],
        image_byte_length: u64,
        image_volume_serial: u64,
        image_file_id: [u8; 16],
        image_readback_receipt_sha256: [u8; 32],
        local_system: bool,
        high_integrity: bool,
        session_id: u32,
        process_active: bool,
        pipe_instance_id: [u8; 16],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            process_id,
            process_creation_time,
            image_sha256,
            image_byte_length,
            image_volume_serial,
            image_file_id,
            image_readback_receipt_sha256,
            local_system,
            high_integrity,
            session_id,
            process_active,
            pipe_instance_id,
        };
        value.validate(started)?;
        Ok(value)
    }

    #[cfg(test)]
    pub(super) fn from_started_for_test(
        started: &WorkerStartedReceipt,
    ) -> Result<Self, AuthorityMaintenanceError> {
        Ok(Self {
            process_id: started.scm_worker_process_id,
            process_creation_time: started.worker_process_creation_time,
            image_sha256: decode_hex_32(&started.worker_image_sha256)?,
            image_byte_length: started.worker_image_byte_length,
            image_volume_serial: started.worker_image_volume_serial,
            image_file_id: decode_hex_16(&started.worker_image_file_id).ok_or(
                AuthorityMaintenanceError("authority_worker_live_readback_invalid"),
            )?,
            image_readback_receipt_sha256: decode_hex_32(
                &started.worker_image_readback_receipt_sha256,
            )?,
            local_system: started.worker_local_system,
            high_integrity: started.worker_high_integrity,
            session_id: started.worker_session_id,
            process_active: started.worker_process_active,
            pipe_instance_id: decode_hex_16(&started.pipe_instance_id).ok_or(
                AuthorityMaintenanceError("authority_worker_live_readback_invalid"),
            )?,
        })
    }

    pub(super) fn validate(
        &self,
        started: &WorkerStartedReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.process_id != started.scm_worker_process_id
            || self.process_creation_time != started.worker_process_creation_time
            || self.image_sha256 != decode_hex_32(&started.worker_image_sha256)?
            || self.image_byte_length != started.worker_image_byte_length
            || self.image_volume_serial != started.worker_image_volume_serial
            || Some(self.image_file_id) != decode_hex_16(&started.worker_image_file_id)
            || self.image_readback_receipt_sha256
                != decode_hex_32(&started.worker_image_readback_receipt_sha256)?
            || !self.local_system
            || !self.high_integrity
            || self.session_id != 0
            || !self.process_active
            || Some(self.pipe_instance_id) != decode_hex_16(&started.pipe_instance_id)
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_live_readback_invalid",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerNonceConsumptionReceipt {
    schema: String,
    capsule_sha256: String,
    transaction_nonce_sha256: String,
    consent_sha256: String,
    consent_expires_unix_millis: u64,
    consumed_unix_millis: u64,
    relative_name: String,
    nonce_root_volume_serial: u64,
    nonce_root_file_id: String,
    receipt_file_volume_serial: u64,
    receipt_file_id: String,
    full_readback_receipt_sha256: String,
    global_nonce_namespace: bool,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl WorkerNonceConsumptionReceipt {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        consumed_unix_millis: u64,
        nonce_root_volume_serial: u64,
        nonce_root_file_id: [u8; 16],
        receipt_file_volume_serial: u64,
        receipt_file_id: [u8; 16],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let nonce = capsule.transaction_nonce_sha256()?;
        let full_readback_receipt_sha256 = worker_nonce_consumption_readback_receipt(
            &capsule.digest()?,
            &nonce,
            consumed_unix_millis,
            nonce_root_volume_serial,
            &nonce_root_file_id,
            receipt_file_volume_serial,
            &receipt_file_id,
        );
        let mut value = Self {
            schema: WORKER_NONCE_CONSUMPTION_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            transaction_nonce_sha256: hex_lower(&nonce),
            consent_sha256: hex_lower(&capsule.consent_sha256()?),
            consent_expires_unix_millis: capsule.consent_expires_unix_millis(),
            consumed_unix_millis,
            relative_name: format!("nonce.{}.consumed.json", hex_lower(&nonce)),
            nonce_root_volume_serial,
            nonce_root_file_id: hex_lower(&nonce_root_file_id),
            receipt_file_volume_serial,
            receipt_file_id: hex_lower(&receipt_file_id),
            full_readback_receipt_sha256: hex_lower(&full_readback_receipt_sha256),
            global_nonce_namespace: true,
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(capsule)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
    ) -> Result<(), AuthorityMaintenanceError> {
        let nonce = capsule.transaction_nonce_sha256()?;
        if self.schema != WORKER_NONCE_CONSUMPTION_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.transaction_nonce_sha256)? != nonce
            || decode_hex_32(&self.consent_sha256)? != capsule.consent_sha256()?
            || self.consent_expires_unix_millis != capsule.consent_expires_unix_millis()
            || self.consumed_unix_millis == 0
            || self.consumed_unix_millis < capsule.consent_created_unix_millis
            || self.consumed_unix_millis > self.consent_expires_unix_millis
            || self.relative_name != format!("nonce.{}.consumed.json", hex_lower(&nonce))
            || self.nonce_root_volume_serial == 0
            || decode_hex_16(&self.nonce_root_file_id).is_none()
            || self.receipt_file_volume_serial == 0
            || decode_hex_16(&self.receipt_file_id).is_none()
            || decode_nonzero_hex_32(&self.full_readback_receipt_sha256).is_none()
            || decode_hex_32(&self.full_readback_receipt_sha256)?
                != worker_nonce_consumption_readback_receipt(
                    &capsule.digest()?,
                    &nonce,
                    self.consumed_unix_millis,
                    self.nonce_root_volume_serial,
                    &decode_hex_16(&self.nonce_root_file_id).unwrap_or_default(),
                    self.receipt_file_volume_serial,
                    &decode_hex_16(&self.receipt_file_id).unwrap_or_default(),
                )
            || !self.global_nonce_namespace
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_nonce_consumption_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn canonical_bytes(
        &self,
        capsule: &MaintenanceWorkerCapsule,
    ) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        self.validate(capsule)?;
        serde_json::to_vec(self)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_nonce_consumption_invalid"))
    }

    pub(super) fn relative_name(&self) -> &str {
        &self.relative_name
    }

    pub(super) fn full_readback_receipt_sha256(
        &self,
    ) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_nonzero_hex_32(&self.full_readback_receipt_sha256).ok_or(AuthorityMaintenanceError(
            "authority_worker_nonce_consumption_invalid",
        ))
    }
}

impl_worker_receipt_digest!(
    WorkerNonceConsumptionReceipt,
    WORKER_NONCE_CONSUMPTION_DOMAIN,
    "authority_worker_nonce_consumption_invalid"
);

fn worker_nonce_consumption_readback_receipt(
    capsule_sha256: &[u8; 32],
    transaction_nonce_sha256: &[u8; 32],
    consumed_unix_millis: u64,
    nonce_root_volume_serial: u64,
    nonce_root_file_id: &[u8; 16],
    receipt_file_volume_serial: u64,
    receipt_file_id: &[u8; 16],
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(WORKER_NONCE_CONSUMPTION_DOMAIN);
    digest.update(b"full-readback\0");
    digest.update(capsule_sha256);
    digest.update(transaction_nonce_sha256);
    digest.update(consumed_unix_millis.to_be_bytes());
    digest.update(nonce_root_volume_serial.to_be_bytes());
    digest.update(nonce_root_file_id);
    digest.update(receipt_file_volume_serial.to_be_bytes());
    digest.update(receipt_file_id);
    digest.update([1u8; 8]);
    digest.finalize().into()
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct TransactionStartedReceipt {
    schema: String,
    capsule_sha256: String,
    source_handles_bound_record_sha256: String,
    source_staging_receipt_sha256: String,
    nonce_consumption_receipt_sha256: String,
    transaction_nonce_sha256: String,
    consent_sha256: String,
    started_unix_millis: u64,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl TransactionStartedReceipt {
    fn new(
        capsule: &MaintenanceWorkerCapsule,
        source_bound: &MaintenanceWorkerJournalRecord,
        staging: &DurableSourceStagingReceipt,
        nonce: &WorkerNonceConsumptionReceipt,
        now_unix_millis: u64,
    ) -> Result<Self, AuthorityMaintenanceError> {
        capsule.validate_consent_at(now_unix_millis)?;
        nonce.validate(capsule)?;
        if nonce.consumed_unix_millis != now_unix_millis {
            return Err(AuthorityMaintenanceError(
                "authority_worker_transaction_start_not_authorized",
            ));
        }
        let mut value = Self {
            schema: WORKER_TRANSACTION_STARTED_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            source_handles_bound_record_sha256: source_bound.record_sha256.clone(),
            source_staging_receipt_sha256: hex_lower(&staging.digest()?),
            nonce_consumption_receipt_sha256: hex_lower(&nonce.digest()?),
            transaction_nonce_sha256: hex_lower(&capsule.transaction_nonce_sha256()?),
            consent_sha256: hex_lower(&capsule.consent_sha256()?),
            started_unix_millis: now_unix_millis,
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(capsule, source_bound, staging, nonce)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        source_bound: &MaintenanceWorkerJournalRecord,
        staging: &DurableSourceStagingReceipt,
        nonce: &WorkerNonceConsumptionReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        nonce.validate(capsule)?;
        if self.schema != WORKER_TRANSACTION_STARTED_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || self.source_handles_bound_record_sha256 != source_bound.record_sha256
            || decode_hex_32(&self.source_staging_receipt_sha256)? != staging.digest()?
            || decode_hex_32(&self.nonce_consumption_receipt_sha256)? != nonce.digest()?
            || decode_hex_32(&self.transaction_nonce_sha256)?
                != capsule.transaction_nonce_sha256()?
            || decode_hex_32(&self.consent_sha256)? != capsule.consent_sha256()?
            || self.started_unix_millis != nonce.consumed_unix_millis
            || self.started_unix_millis > capsule.consent_expires_unix_millis()
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_transaction_started_receipt_invalid",
            ));
        }
        Ok(())
    }
}

impl_worker_receipt_digest!(
    TransactionStartedReceipt,
    WORKER_TRANSACTION_STARTED_DOMAIN,
    "authority_worker_transaction_started_receipt_invalid"
);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct CandidateCredentialArmedReceipt {
    schema: String,
    capsule_sha256: String,
    plan_sha256: String,
    generation_sha256: String,
    transaction_sha256: String,
    transaction_started_receipt_sha256: String,
    worker_started_receipt_sha256: String,
    maintenance_worker: CandidateProcessEvidence,
    nonce_consumption_receipt_sha256: String,
    nonce_consumption_full_readback_sha256: String,
    nonce_consumption_file_sha256: String,
    nonce_consumption_file_volume_serial: u64,
    nonce_consumption_file_id: String,
    prepared_credential_sha256: String,
    prepared_record_sha256: String,
    prepared_canonical_bytes_sha256: String,
    prepared_canonical_byte_length: u64,
    prepared_file_volume_serial: u64,
    prepared_file_id: String,
    candidate_nonce_sha256: String,
    issued_at_unix_millis: u64,
    expires_at_unix_millis: u64,
    candidate_service: CandidateProcessEvidence,
    armed_relative_name: String,
    prepared_create_new_and_held: bool,
    armed_record_must_bind_this_receipt: bool,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl CandidateCredentialArmedReceipt {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        transaction_started: &TransactionStartedReceipt,
        worker_started: &WorkerStartedReceipt,
        prepared: &CandidateCredentialRecord,
        prepared_file_volume_serial: u64,
        prepared_file_id: [u8; 16],
        maintenance_worker: CandidateProcessEvidence,
        candidate_service: CandidateProcessEvidence,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let binding = prepared
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let prepared_bytes = prepared
            .canonical_bytes()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let prepared_byte_length: u64 = prepared_bytes.len().try_into().map_err(|_| {
            AuthorityMaintenanceError("authority_candidate_credential_size_invalid")
        })?;
        let prepared_canonical_bytes_sha256: [u8; 32] = Sha256::digest(&prepared_bytes).into();
        let issuer = binding.issuer();
        let mut value = Self {
            schema: WORKER_CANDIDATE_CREDENTIAL_ARMED_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            plan_sha256: hex_lower(&capsule.plan_sha256()?),
            generation_sha256: hex_lower(&capsule.generation()?),
            transaction_sha256: hex_lower(&capsule.transaction_sha256()?),
            transaction_started_receipt_sha256: hex_lower(&transaction_started.digest()?),
            worker_started_receipt_sha256: hex_lower(&worker_started.digest()?),
            maintenance_worker,
            nonce_consumption_receipt_sha256: hex_lower(issuer.nonce_consumption_receipt_sha256()),
            nonce_consumption_full_readback_sha256: hex_lower(
                issuer.nonce_consumption_full_readback_sha256(),
            ),
            nonce_consumption_file_sha256: hex_lower(issuer.nonce_consumption_file_sha256()),
            nonce_consumption_file_volume_serial: issuer.nonce_consumption_file_volume_serial(),
            nonce_consumption_file_id: hex_lower(issuer.nonce_consumption_file_id()),
            prepared_credential_sha256: hex_lower(
                &prepared
                    .credential_sha256()
                    .map_err(|error| AuthorityMaintenanceError(error.code()))?,
            ),
            prepared_record_sha256: hex_lower(
                &prepared
                    .record_sha256()
                    .map_err(|error| AuthorityMaintenanceError(error.code()))?,
            ),
            prepared_canonical_bytes_sha256: hex_lower(&prepared_canonical_bytes_sha256),
            prepared_canonical_byte_length: prepared_byte_length,
            prepared_file_volume_serial,
            prepared_file_id: hex_lower(&prepared_file_id),
            candidate_nonce_sha256: hex_lower(binding.nonce()),
            issued_at_unix_millis: binding.issued_at_unix_millis(),
            expires_at_unix_millis: binding.expires_at_unix_millis(),
            candidate_service,
            armed_relative_name: candidate_credential_file_name(binding.transaction_sha256())
                .map_err(|error| AuthorityMaintenanceError(error.code()))?,
            prepared_create_new_and_held: true,
            armed_record_must_bind_this_receipt: true,
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(capsule, transaction_started, worker_started, prepared)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        transaction_started: &TransactionStartedReceipt,
        worker_started: &WorkerStartedReceipt,
        prepared: &CandidateCredentialRecord,
    ) -> Result<(), AuthorityMaintenanceError> {
        let binding = prepared
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let prepared_bytes = prepared
            .canonical_bytes()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        self.maintenance_worker
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        self.candidate_service
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let issuer = binding.issuer();
        if self.schema != WORKER_CANDIDATE_CREDENTIAL_ARMED_SCHEMA
            || prepared.phase() != CandidateCredentialPhase::Prepared
            || *binding.plan_sha256() != capsule.plan_sha256()?
            || *binding.generation() != capsule.generation()?
            || *binding.transaction_sha256() != capsule.transaction_sha256()?
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.plan_sha256)? != capsule.plan_sha256()?
            || decode_hex_32(&self.generation_sha256)? != capsule.generation()?
            || decode_hex_32(&self.transaction_sha256)? != capsule.transaction_sha256()?
            || decode_hex_32(&self.transaction_started_receipt_sha256)?
                != transaction_started.digest()?
            || decode_hex_32(&self.worker_started_receipt_sha256)? != worker_started.digest()?
            || *issuer.capsule_sha256() != capsule.digest()?
            || *issuer.transaction_started_receipt_sha256() != transaction_started.digest()?
            || *issuer.worker_started_receipt_sha256() != worker_started.digest()?
            || *issuer.maintenance_worker() != self.maintenance_worker
            || decode_hex_32(&self.nonce_consumption_receipt_sha256)?
                != *issuer.nonce_consumption_receipt_sha256()
            || decode_hex_32(&self.nonce_consumption_full_readback_sha256)?
                != *issuer.nonce_consumption_full_readback_sha256()
            || decode_hex_32(&self.nonce_consumption_file_sha256)?
                != *issuer.nonce_consumption_file_sha256()
            || self.nonce_consumption_file_volume_serial
                != issuer.nonce_consumption_file_volume_serial()
            || decode_hex_16(&self.nonce_consumption_file_id)
                != Some(*issuer.nonce_consumption_file_id())
            || self.maintenance_worker.process_id() != worker_started.scm_worker_process_id
            || self.maintenance_worker.process_creation_time()
                != worker_started.worker_process_creation_time
            || *self.maintenance_worker.image_sha256()
                != decode_hex_32(&worker_started.worker_image_sha256)?
            || self.maintenance_worker.image_byte_length()
                != worker_started.worker_image_byte_length
            || self.maintenance_worker.image_volume_serial()
                != worker_started.worker_image_volume_serial
            || Some(*self.maintenance_worker.image_file_id())
                != decode_hex_16(&worker_started.worker_image_file_id)
            || decode_hex_32(&self.prepared_credential_sha256)?
                != prepared
                    .credential_sha256()
                    .map_err(|error| AuthorityMaintenanceError(error.code()))?
            || decode_hex_32(&self.prepared_record_sha256)?
                != prepared
                    .record_sha256()
                    .map_err(|error| AuthorityMaintenanceError(error.code()))?
            || decode_hex_32(&self.prepared_canonical_bytes_sha256)?
                != <[u8; 32]>::from(Sha256::digest(&prepared_bytes))
            || self.prepared_canonical_byte_length != prepared_bytes.len() as u64
            || self.prepared_file_volume_serial == 0
            || decode_hex_16(&self.prepared_file_id).is_none()
            || decode_hex_32(&self.candidate_nonce_sha256)? != *binding.nonce()
            || self.issued_at_unix_millis != binding.issued_at_unix_millis()
            || self.expires_at_unix_millis != binding.expires_at_unix_millis()
            || self.candidate_service.image() != binding.target_service_image()
            || self.armed_relative_name
                != candidate_credential_file_name(binding.transaction_sha256())
                    .map_err(|error| AuthorityMaintenanceError(error.code()))?
            || !self.prepared_create_new_and_held
            || !self.armed_record_must_bind_this_receipt
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_credential_armed_receipt_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn prepared_credential_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.prepared_credential_sha256)
    }

    pub(super) fn prepared_record_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.prepared_record_sha256)
    }

    pub(super) fn prepared_canonical_bytes_sha256(
        &self,
    ) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.prepared_canonical_bytes_sha256)
    }

    pub(super) fn matches_prepared_persistence(
        &self,
        prepared: &CandidateCredentialRecord,
        file_volume_serial: u64,
        file_id: &[u8; 16],
        canonical_bytes_sha256: &[u8; 32],
    ) -> Result<bool, AuthorityMaintenanceError> {
        let bytes = prepared
            .canonical_bytes()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        Ok(self.prepared_file_volume_serial == file_volume_serial
            && decode_hex_16(&self.prepared_file_id) == Some(*file_id)
            && self.prepared_canonical_byte_length == bytes.len() as u64
            && self.prepared_canonical_bytes_sha256()? == *canonical_bytes_sha256
            && <[u8; 32]>::from(Sha256::digest(&bytes)) == *canonical_bytes_sha256)
    }

    pub(super) fn armed_relative_name(&self) -> &str {
        &self.armed_relative_name
    }

    pub(super) fn maintenance_worker(&self) -> &CandidateProcessEvidence {
        &self.maintenance_worker
    }

    pub(super) fn candidate_service(&self) -> &CandidateProcessEvidence {
        &self.candidate_service
    }
}

impl_worker_receipt_digest!(
    CandidateCredentialArmedReceipt,
    WORKER_CANDIDATE_CREDENTIAL_ARMED_DOMAIN,
    "authority_candidate_credential_armed_receipt_invalid"
);

macro_rules! define_transaction_terminal_receipt {
    ($name:ident, $schema:expr, $domain:expr, $invalid:expr, $verified_field:ident) => {
        #[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
        #[serde(rename_all = "camelCase", deny_unknown_fields)]
        pub(super) struct $name {
            schema: String,
            capsule_sha256: String,
            transaction_started_receipt_sha256: String,
            outcome_readback_sha256: String,
            $verified_field: bool,
            persistence: DurableReceiptPersistence,
            receipt_sha256: String,
        }

        impl $name {
            pub(super) fn from_observed(
                capsule: &MaintenanceWorkerCapsule,
                started: &TransactionStartedReceipt,
                outcome_readback_sha256: [u8; 32],
            ) -> Result<Self, AuthorityMaintenanceError> {
                let mut value = Self {
                    schema: $schema.to_string(),
                    capsule_sha256: hex_lower(&capsule.digest()?),
                    transaction_started_receipt_sha256: hex_lower(&started.digest()?),
                    outcome_readback_sha256: hex_lower(&outcome_readback_sha256),
                    $verified_field: true,
                    persistence: DurableReceiptPersistence::exact(),
                    receipt_sha256: String::new(),
                };
                value.seal()?;
                value.validate(capsule, started)?;
                Ok(value)
            }

            pub(super) fn validate(
                &self,
                capsule: &MaintenanceWorkerCapsule,
                started: &TransactionStartedReceipt,
            ) -> Result<(), AuthorityMaintenanceError> {
                if self.schema != $schema
                    || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
                    || decode_hex_32(&self.transaction_started_receipt_sha256)?
                        != started.digest()?
                    || decode_nonzero_hex_32(&self.outcome_readback_sha256).is_none()
                    || !self.$verified_field
                    || !self.persistence.is_exact()
                    || self.digest().is_err()
                {
                    return Err(AuthorityMaintenanceError($invalid));
                }
                Ok(())
            }
        }

        impl_worker_receipt_digest!($name, $domain, $invalid);
    };
}

define_transaction_terminal_receipt!(
    TransactionCommittedReceipt,
    WORKER_TRANSACTION_COMMITTED_SCHEMA,
    WORKER_TRANSACTION_COMMITTED_DOMAIN,
    "authority_worker_transaction_committed_receipt_invalid",
    committed_state_readback_verified
);
define_transaction_terminal_receipt!(
    TransactionContainedReceipt,
    WORKER_TRANSACTION_CONTAINED_SCHEMA,
    WORKER_TRANSACTION_CONTAINED_DOMAIN,
    "authority_worker_transaction_contained_receipt_invalid",
    containment_and_restore_verified
);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerExitReadyReceipt {
    schema: String,
    capsule_sha256: String,
    terminal_record_sha256: String,
    terminal_receipt_sha256: String,
    staging_cleanup_receipt_sha256: String,
    worker: WorkerProcessBinding,
    system_actor_complete: bool,
    worker_returns_before_finalization: bool,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl WorkerExitReadyReceipt {
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        terminal: &MaintenanceWorkerJournalRecord,
        cleanup: &WorkerStagingCleanupReceipt,
        worker: &WorkerStartedReceipt,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let mut value = Self {
            schema: WORKER_EXIT_READY_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            terminal_record_sha256: terminal.record_sha256.clone(),
            terminal_receipt_sha256: hex_lower(&terminal.phase_receipt_sha256()?),
            staging_cleanup_receipt_sha256: hex_lower(&cleanup.digest()?),
            worker: WorkerProcessBinding::new(
                worker.scm_worker_process_id,
                worker.worker_process_creation_time,
                decode_hex_32(&worker.worker_image_sha256)?,
            ),
            system_actor_complete: true,
            worker_returns_before_finalization: true,
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(capsule, terminal, cleanup, worker)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        terminal: &MaintenanceWorkerJournalRecord,
        cleanup: &WorkerStagingCleanupReceipt,
        worker: &WorkerStartedReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        let expected_worker = WorkerProcessBinding::new(
            worker.scm_worker_process_id,
            worker.worker_process_creation_time,
            decode_hex_32(&worker.worker_image_sha256)?,
        );
        if self.schema != WORKER_EXIT_READY_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || !terminal.phase.is_transaction_terminal()
            || self.terminal_record_sha256 != terminal.record_sha256
            || decode_hex_32(&self.terminal_receipt_sha256)? != terminal.phase_receipt_sha256()?
            || decode_hex_32(&self.staging_cleanup_receipt_sha256)? != cleanup.digest()?
            || self.worker != expected_worker
            || !self.system_actor_complete
            || !self.worker_returns_before_finalization
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_exit_ready_receipt_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn worker(&self) -> &WorkerProcessBinding {
        &self.worker
    }
}

impl_worker_receipt_digest!(
    WorkerExitReadyReceipt,
    WORKER_EXIT_READY_DOMAIN,
    "authority_worker_exit_ready_receipt_invalid"
);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct ServiceDeleteIntentReceipt {
    schema: String,
    capsule_sha256: String,
    exit_ready_receipt_sha256: String,
    launch_contract_sha256: String,
    service_created_receipt_sha256: String,
    scm_configuration_readback_sha256: String,
    service_security_readback_sha256: String,
    maintenance_service_name: String,
    generation_sha256: String,
    transaction_sha256: String,
    worker: WorkerProcessBinding,
    finalizer: WorkerProcessBinding,
    flush_completed_before_delete_call: bool,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl ServiceDeleteIntentReceipt {
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        service_created: &ServiceCreatedReceipt,
        exit_ready: &WorkerExitReadyReceipt,
        finalizer: WorkerProcessBinding,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let mut value = Self {
            schema: WORKER_SERVICE_DELETE_INTENT_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            exit_ready_receipt_sha256: hex_lower(&exit_ready.digest()?),
            launch_contract_sha256: hex_lower(&launch.digest()?),
            service_created_receipt_sha256: hex_lower(&service_created.digest()?),
            scm_configuration_readback_sha256: hex_lower(
                &service_created.scm_configuration_readback_sha256()?,
            ),
            service_security_readback_sha256: hex_lower(
                &service_created.service_security_readback_sha256()?,
            ),
            maintenance_service_name: launch.service_name.to_string(),
            generation_sha256: hex_lower(&capsule.generation()?),
            transaction_sha256: hex_lower(&capsule.transaction_sha256()?),
            worker: exit_ready.worker().clone(),
            finalizer,
            flush_completed_before_delete_call: true,
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(capsule, launch, service_created, exit_ready)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        service_created: &ServiceCreatedReceipt,
        exit_ready: &WorkerExitReadyReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.schema != WORKER_SERVICE_DELETE_INTENT_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.exit_ready_receipt_sha256)? != exit_ready.digest()?
            || decode_hex_32(&self.launch_contract_sha256)? != launch.digest()?
            || decode_hex_32(&self.service_created_receipt_sha256)? != service_created.digest()?
            || decode_hex_32(&self.scm_configuration_readback_sha256)?
                != service_created.scm_configuration_readback_sha256()?
            || decode_hex_32(&self.service_security_readback_sha256)?
                != service_created.service_security_readback_sha256()?
            || self.maintenance_service_name != MAINTENANCE_WORKER_SERVICE_NAME
            || self.maintenance_service_name != launch.service_name
            || decode_hex_32(&self.generation_sha256)? != capsule.generation()?
            || decode_hex_32(&self.transaction_sha256)? != capsule.transaction_sha256()?
            || self.worker != *exit_ready.worker()
            || !capsule.helper_process_matches(&self.finalizer)
            || self.finalizer.process_id == self.worker.process_id
            || self.finalizer.process_creation_time == self.worker.process_creation_time
            || !self.flush_completed_before_delete_call
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_service_delete_intent_receipt_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn finalizer(&self) -> &WorkerProcessBinding {
        &self.finalizer
    }
}

impl_worker_receipt_digest!(
    ServiceDeleteIntentReceipt,
    WORKER_SERVICE_DELETE_INTENT_DOMAIN,
    "authority_worker_service_delete_intent_receipt_invalid"
);

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(super) enum ServiceDeleteTransitionKind {
    DeleteCallCompleted,
    TargetStateObserved,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(super) enum ServiceDeleteTargetState {
    DeletePending,
    Absent,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct ServiceDeletePendingReceipt {
    schema: String,
    capsule_sha256: String,
    terminal_receipt_sha256: String,
    delete_intent_receipt_sha256: String,
    scm_delete_pending_readback_sha256: String,
    transition_kind: ServiceDeleteTransitionKind,
    target_state: ServiceDeleteTargetState,
    delete_call_completed: bool,
    target_state_observed: bool,
    no_new_service_open: bool,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl ServiceDeletePendingReceipt {
    pub(super) fn from_delete_call(
        capsule: &MaintenanceWorkerCapsule,
        terminal_receipt_sha256: [u8; 32],
        intent: &ServiceDeleteIntentReceipt,
        scm_delete_pending_readback_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        Self::from_transition(
            capsule,
            terminal_receipt_sha256,
            intent,
            scm_delete_pending_readback_sha256,
            ServiceDeleteTransitionKind::DeleteCallCompleted,
            ServiceDeleteTargetState::DeletePending,
        )
    }

    pub(super) fn from_recovered_target_state(
        capsule: &MaintenanceWorkerCapsule,
        terminal_receipt_sha256: [u8; 32],
        intent: &ServiceDeleteIntentReceipt,
        target_state: ServiceDeleteTargetState,
        scm_target_state_readback_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        Self::from_transition(
            capsule,
            terminal_receipt_sha256,
            intent,
            scm_target_state_readback_sha256,
            ServiceDeleteTransitionKind::TargetStateObserved,
            target_state,
        )
    }

    fn from_transition(
        capsule: &MaintenanceWorkerCapsule,
        terminal_receipt_sha256: [u8; 32],
        intent: &ServiceDeleteIntentReceipt,
        scm_delete_pending_readback_sha256: [u8; 32],
        transition_kind: ServiceDeleteTransitionKind,
        target_state: ServiceDeleteTargetState,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let mut value = Self {
            schema: WORKER_SERVICE_DELETE_PENDING_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            terminal_receipt_sha256: hex_lower(&terminal_receipt_sha256),
            delete_intent_receipt_sha256: hex_lower(&intent.digest()?),
            scm_delete_pending_readback_sha256: hex_lower(&scm_delete_pending_readback_sha256),
            transition_kind,
            target_state,
            delete_call_completed: transition_kind
                == ServiceDeleteTransitionKind::DeleteCallCompleted,
            target_state_observed: transition_kind
                == ServiceDeleteTransitionKind::TargetStateObserved,
            no_new_service_open: true,
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(capsule, terminal_receipt_sha256, intent)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        terminal_receipt_sha256: [u8; 32],
        intent: &ServiceDeleteIntentReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.schema != WORKER_SERVICE_DELETE_PENDING_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.terminal_receipt_sha256)? != terminal_receipt_sha256
            || decode_hex_32(&self.delete_intent_receipt_sha256)? != intent.digest()?
            || decode_nonzero_hex_32(&self.scm_delete_pending_readback_sha256).is_none()
            || !matches!(
                (self.transition_kind, self.target_state),
                (
                    ServiceDeleteTransitionKind::DeleteCallCompleted,
                    ServiceDeleteTargetState::DeletePending
                ) | (
                    ServiceDeleteTransitionKind::TargetStateObserved,
                    ServiceDeleteTargetState::DeletePending | ServiceDeleteTargetState::Absent
                )
            )
            || self.delete_call_completed
                != (self.transition_kind == ServiceDeleteTransitionKind::DeleteCallCompleted)
            || self.target_state_observed
                != (self.transition_kind == ServiceDeleteTransitionKind::TargetStateObserved)
            || !self.no_new_service_open
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_service_delete_pending_receipt_invalid",
            ));
        }
        Ok(())
    }
}

impl_worker_receipt_digest!(
    ServiceDeletePendingReceipt,
    WORKER_SERVICE_DELETE_PENDING_DOMAIN,
    "authority_worker_service_delete_pending_receipt_invalid"
);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct FinalizerHandlesClosedReceipt {
    schema: String,
    capsule_sha256: String,
    exit_ready_receipt_sha256: String,
    delete_pending_receipt_sha256: String,
    handles_closed_readback_sha256: String,
    service_handle_closed: bool,
    service_manager_handle_closed: bool,
    worker_process_handle_closed: bool,
    worker_pipe_handle_closed: bool,
    elevated_finalizer_actor: bool,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl FinalizerHandlesClosedReceipt {
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        exit_ready: &WorkerExitReadyReceipt,
        delete_pending: &ServiceDeletePendingReceipt,
        handles_closed_readback_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let mut value = Self {
            schema: WORKER_FINALIZER_HANDLES_CLOSED_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            exit_ready_receipt_sha256: hex_lower(&exit_ready.digest()?),
            delete_pending_receipt_sha256: hex_lower(&delete_pending.digest()?),
            handles_closed_readback_sha256: hex_lower(&handles_closed_readback_sha256),
            service_handle_closed: true,
            service_manager_handle_closed: true,
            worker_process_handle_closed: true,
            worker_pipe_handle_closed: true,
            elevated_finalizer_actor: true,
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(capsule, exit_ready, delete_pending)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        exit_ready: &WorkerExitReadyReceipt,
        delete_pending: &ServiceDeletePendingReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.schema != WORKER_FINALIZER_HANDLES_CLOSED_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.exit_ready_receipt_sha256)? != exit_ready.digest()?
            || decode_hex_32(&self.delete_pending_receipt_sha256)? != delete_pending.digest()?
            || decode_nonzero_hex_32(&self.handles_closed_readback_sha256).is_none()
            || !self.service_handle_closed
            || !self.service_manager_handle_closed
            || !self.worker_process_handle_closed
            || !self.worker_pipe_handle_closed
            || !self.elevated_finalizer_actor
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_finalizer_handles_closed_receipt_invalid",
            ));
        }
        Ok(())
    }
}

impl_worker_receipt_digest!(
    FinalizerHandlesClosedReceipt,
    WORKER_FINALIZER_HANDLES_CLOSED_DOMAIN,
    "authority_worker_finalizer_handles_closed_receipt_invalid"
);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct ServiceAbsentReceipt {
    schema: String,
    capsule_sha256: String,
    delete_pending_receipt_sha256: String,
    cleanup_receipt_sha256: String,
    scm_absence_readback_sha256: String,
    worker_process_absent: bool,
    pipe_instance_absent: bool,
    protected_staging_absent: bool,
    zero_residue: bool,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl ServiceAbsentReceipt {
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        delete_pending: &ServiceDeletePendingReceipt,
        cleanup: &WorkerStagingCleanupReceipt,
        scm_absence_readback_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        cleanup.validate_self_sealed()?;
        if !cleanup.proves_staging_absent_for_service_finalization() {
            return Err(AuthorityMaintenanceError(
                "authority_worker_service_absent_receipt_invalid",
            ));
        }
        let mut value = Self {
            schema: WORKER_SERVICE_ABSENT_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            delete_pending_receipt_sha256: hex_lower(&delete_pending.digest()?),
            cleanup_receipt_sha256: hex_lower(&cleanup.digest()?),
            scm_absence_readback_sha256: hex_lower(&scm_absence_readback_sha256),
            worker_process_absent: true,
            pipe_instance_absent: true,
            protected_staging_absent: true,
            zero_residue: true,
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(capsule, delete_pending, cleanup)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        delete_pending: &ServiceDeletePendingReceipt,
        cleanup: &WorkerStagingCleanupReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        cleanup.validate_self_sealed()?;
        if !cleanup.proves_staging_absent_for_service_finalization()
            || self.schema != WORKER_SERVICE_ABSENT_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.delete_pending_receipt_sha256)? != delete_pending.digest()?
            || decode_hex_32(&self.cleanup_receipt_sha256)? != cleanup.digest()?
            || decode_nonzero_hex_32(&self.scm_absence_readback_sha256).is_none()
            || !self.worker_process_absent
            || !self.pipe_instance_absent
            || !self.protected_staging_absent
            || !self.zero_residue
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_service_absent_receipt_invalid",
            ));
        }
        Ok(())
    }
}

impl_worker_receipt_digest!(
    ServiceAbsentReceipt,
    WORKER_SERVICE_ABSENT_DOMAIN,
    "authority_worker_service_absent_receipt_invalid"
);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerHandleHandoffReceipt {
    schema: String,
    capsule_sha256: String,
    pipe_name: String,
    pipe_instance_id: String,
    pipe_nonce_sha256: String,
    pipe_prepared_receipt_sha256: String,
    helper: WorkerProcessBinding,
    helper_image_volume_serial: u64,
    helper_image_file_id: String,
    helper_bootstrap_binding_sha256: String,
    helper_elevated: bool,
    helper_high_integrity: bool,
    helper_local_system: bool,
    helper_session_id: u32,
    worker: WorkerProcessBinding,
    scm_worker_process_id: u32,
    pipe_server_process_id: u32,
    pipe_client_process_id: u32,
    duplicated_target_handle_values: [u64; PROTECTED_GENERATION_PAYLOAD_COUNT],
    pipe_first_instance: bool,
    duplicate_handle_only: bool,
    source_paths_transmitted: bool,
    helper_holds_sources_until_durable_staging_ack: bool,
    receipt_sha256: String,
}

impl WorkerHandleHandoffReceipt {
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        helper: WorkerProcessBinding,
        worker: WorkerProcessBinding,
        pipe: &WorkerPipePreparedReceipt,
        duplicated_target_handle_values: [u64; PROTECTED_GENERATION_PAYLOAD_COUNT],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let capsule_sha256 = capsule.digest()?;
        let pipe_name = worker_handoff_pipe_name(capsule)?;
        let helper_process_id = helper.process_id;
        let worker_process_id = worker.process_id;
        let mut value = Self {
            schema: WORKER_HANDLE_HANDOFF_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule_sha256),
            pipe_name,
            pipe_instance_id: hex_lower(&pipe.pipe_instance_id()?),
            pipe_nonce_sha256: hex_lower(&capsule.worker_pipe_nonce()?),
            pipe_prepared_receipt_sha256: hex_lower(&pipe.digest()?),
            helper,
            helper_image_volume_serial: capsule.bootstrap_helper_image_volume_serial,
            helper_image_file_id: capsule.bootstrap_helper_image_file_id.clone(),
            helper_bootstrap_binding_sha256: capsule.bootstrap_helper_binding_sha256.clone(),
            helper_elevated: capsule.bootstrap_helper_elevated,
            helper_high_integrity: capsule.bootstrap_helper_high_integrity,
            helper_local_system: capsule.bootstrap_helper_local_system,
            helper_session_id: capsule.bootstrap_helper_session_id,
            worker,
            scm_worker_process_id: worker_process_id,
            pipe_server_process_id: helper_process_id,
            pipe_client_process_id: worker_process_id,
            duplicated_target_handle_values,
            pipe_first_instance: true,
            duplicate_handle_only: true,
            source_paths_transmitted: false,
            helper_holds_sources_until_durable_staging_ack: true,
            receipt_sha256: String::new(),
        };
        value.receipt_sha256 = hex_lower(&value.compute_digest()?);
        value.validate(capsule)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
    ) -> Result<(), AuthorityMaintenanceError> {
        let expected_capsule = capsule.digest()?;
        let distinct_handles =
            self.duplicated_target_handle_values
                .iter()
                .enumerate()
                .all(|(index, value)| {
                    *value != 0 && !self.duplicated_target_handle_values[..index].contains(value)
                });
        if self.schema != WORKER_HANDLE_HANDOFF_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != expected_capsule
            || self.pipe_name != worker_handoff_pipe_name(capsule)?
            || decode_hex_16(&self.pipe_instance_id).is_none()
            || decode_hex_32(&self.pipe_nonce_sha256)? != capsule.worker_pipe_nonce()?
            || decode_nonzero_hex_32(&self.pipe_prepared_receipt_sha256).is_none()
            || !capsule.helper_process_matches(&self.helper)
            || self.helper_image_volume_serial != capsule.bootstrap_helper_image_volume_serial
            || self.helper_image_file_id != capsule.bootstrap_helper_image_file_id
            || self.helper_bootstrap_binding_sha256 != capsule.bootstrap_helper_binding_sha256
            || !self.helper_elevated
            || !self.helper_high_integrity
            || self.helper_local_system
            || self.helper_session_id == 0
            || !self.worker.is_valid_for_helper(capsule)
            || self.helper.process_id == self.worker.process_id
            || self.helper.process_creation_time == self.worker.process_creation_time
            || self.scm_worker_process_id != self.worker.process_id
            || self.pipe_server_process_id != self.helper.process_id
            || self.pipe_client_process_id != self.worker.process_id
            || !distinct_handles
            || !self.pipe_first_instance
            || !self.duplicate_handle_only
            || self.source_paths_transmitted
            || !self.helper_holds_sources_until_durable_staging_ack
            || decode_hex_32(&self.receipt_sha256)? != self.compute_digest()?
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handle_handoff_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn validate_with_pipe(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        pipe: &WorkerPipePreparedReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.validate(capsule)?;
        pipe.validate(capsule, launch)?;
        if decode_hex_32(&self.pipe_prepared_receipt_sha256)? != pipe.digest()?
            || decode_hex_16(&self.pipe_instance_id) != Some(pipe.pipe_instance_id()?)
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handle_handoff_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn canonical_bytes(
        &self,
        capsule: &MaintenanceWorkerCapsule,
    ) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        self.validate(capsule)?;
        serde_json::to_vec(self)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_handoff_encode_failed"))
    }

    pub(super) fn parse_canonical(
        bytes: &[u8],
        capsule: &MaintenanceWorkerCapsule,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if bytes.is_empty() || bytes.len() > MAX_WORKER_RECEIPT_BYTES {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handle_handoff_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_handle_handoff_invalid"))?;
        value.validate(capsule)?;
        if value.canonical_bytes(capsule)? != bytes {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handle_handoff_not_canonical",
            ));
        }
        Ok(value)
    }

    pub(super) fn helper(&self) -> &WorkerProcessBinding {
        &self.helper
    }

    pub(super) fn worker(&self) -> &WorkerProcessBinding {
        &self.worker
    }

    pub(super) fn duplicated_target_handle_values(
        &self,
    ) -> [u64; PROTECTED_GENERATION_PAYLOAD_COUNT] {
        self.duplicated_target_handle_values
    }

    pub(super) fn digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        let value = decode_hex_32(&self.receipt_sha256)?;
        if value.iter().all(|byte| *byte == 0) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handle_handoff_invalid",
            ));
        }
        Ok(value)
    }

    fn compute_digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        let mut unsigned = self.clone();
        unsigned.receipt_sha256.clear();
        let canonical = serde_json::to_vec(&unsigned)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_handoff_encode_failed"))?;
        let mut digest = Sha256::new();
        digest.update(WORKER_HANDOFF_DOMAIN);
        digest.update(canonical);
        Ok(digest.finalize().into())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerSourceStagingIntentReceipt {
    schema: String,
    capsule_sha256: String,
    worker_started_record_sha256: String,
    handle_handoff_receipt_sha256: String,
    staging_directory_relative_name: String,
    create_new_required: bool,
    source_handles_held_until_durable_completion: bool,
    partial_stage_requires_containment: bool,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl WorkerSourceStagingIntentReceipt {
    pub(super) fn new(
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let mut value = Self {
            schema: WORKER_SOURCE_STAGING_INTENT_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            worker_started_record_sha256: worker_started.record_sha256.clone(),
            handle_handoff_receipt_sha256: hex_lower(&handoff.digest()?),
            staging_directory_relative_name: format!("stage.{}", hex_lower(&capsule.digest()?)),
            create_new_required: true,
            source_handles_held_until_durable_completion: true,
            partial_stage_requires_containment: true,
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(capsule, worker_started, handoff)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        handoff.validate(capsule)?;
        if self.schema != WORKER_SOURCE_STAGING_INTENT_SCHEMA
            || worker_started.phase != MaintenanceWorkerPhase::WorkerStarted
            || decode_hex_32(&worker_started.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || self.worker_started_record_sha256 != worker_started.record_sha256
            || decode_hex_32(&self.handle_handoff_receipt_sha256)? != handoff.digest()?
            || self.staging_directory_relative_name
                != format!("stage.{}", hex_lower(&capsule.digest()?))
            || !self.create_new_required
            || !self.source_handles_held_until_durable_completion
            || !self.partial_stage_requires_containment
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_source_staging_intent_invalid",
            ));
        }
        Ok(())
    }
}

impl_worker_receipt_digest!(
    WorkerSourceStagingIntentReceipt,
    WORKER_SOURCE_STAGING_INTENT_DOMAIN,
    "authority_worker_source_staging_intent_invalid"
);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerPartialStagingCleanupReceipt {
    schema: String,
    capsule_sha256: String,
    staging_intent_receipt_sha256: String,
    staging_directory_relative_name: String,
    expected_entry_names_sha256: String,
    staging_path_absent: bool,
    zero_unexpected_entries: bool,
    all_staging_handles_closed: bool,
    cleanup_parent_flushed: bool,
    cleanup_readback_sha256: String,
    persistence: DurableReceiptPersistence,
    receipt_sha256: String,
}

impl WorkerPartialStagingCleanupReceipt {
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        intent: &WorkerSourceStagingIntentReceipt,
        cleanup_readback_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let mut value = Self {
            schema: WORKER_PARTIAL_STAGING_CLEANUP_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            staging_intent_receipt_sha256: hex_lower(&intent.digest()?),
            staging_directory_relative_name: format!("stage.{}", hex_lower(&capsule.digest()?)),
            expected_entry_names_sha256: hex_lower(&partial_staging_entry_names_digest(capsule)?),
            staging_path_absent: true,
            zero_unexpected_entries: true,
            all_staging_handles_closed: true,
            cleanup_parent_flushed: true,
            cleanup_readback_sha256: hex_lower(&cleanup_readback_sha256),
            persistence: DurableReceiptPersistence::exact(),
            receipt_sha256: String::new(),
        };
        value.seal()?;
        value.validate(capsule, intent)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        intent: &WorkerSourceStagingIntentReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.schema != WORKER_PARTIAL_STAGING_CLEANUP_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.staging_intent_receipt_sha256)? != intent.digest()?
            || self.staging_directory_relative_name
                != format!("stage.{}", hex_lower(&capsule.digest()?))
            || decode_hex_32(&self.expected_entry_names_sha256)?
                != partial_staging_entry_names_digest(capsule)?
            || !self.staging_path_absent
            || !self.zero_unexpected_entries
            || !self.all_staging_handles_closed
            || !self.cleanup_parent_flushed
            || decode_nonzero_hex_32(&self.cleanup_readback_sha256).is_none()
            || !self.persistence.is_exact()
            || self.digest().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_partial_staging_cleanup_invalid",
            ));
        }
        Ok(())
    }
}

impl_worker_receipt_digest!(
    WorkerPartialStagingCleanupReceipt,
    WORKER_PARTIAL_STAGING_CLEANUP_DOMAIN,
    "authority_worker_partial_staging_cleanup_invalid"
);

fn partial_staging_entry_names_digest(
    capsule: &MaintenanceWorkerCapsule,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let mut names = [
        StagedPayloadKind::Service.staging_relative_name(capsule),
        StagedPayloadKind::Controller.staging_relative_name(capsule),
        StagedPayloadKind::InstallHelper.staging_relative_name(capsule),
        StagedPayloadKind::LifecycleDriver.staging_relative_name(capsule),
        StagedPayloadKind::BridgeLauncher.staging_relative_name(capsule),
        StagedPayloadKind::RuntimeSourceManifest.staging_relative_name(capsule),
        "source-identities.json".to_string(),
    ];
    names.sort();
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-worker-partial-stage-entry-set-v1\0");
    for name in names {
        digest.update(name.as_bytes());
        digest.update([0]);
    }
    Ok(digest.finalize().into())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum StagedPayloadKind {
    Service,
    Controller,
    InstallHelper,
    LifecycleDriver,
    BridgeLauncher,
    RuntimeSourceManifest,
}

impl StagedPayloadKind {
    pub(super) fn as_str(self) -> &'static str {
        match self {
            Self::Service => "service",
            Self::Controller => "controller",
            Self::InstallHelper => "install-helper",
            Self::LifecycleDriver => "lifecycle-driver",
            Self::BridgeLauncher => "bridge-launcher",
            Self::RuntimeSourceManifest => "runtime-source-manifest",
        }
    }

    fn source<'a>(self, capsule: &'a MaintenanceWorkerCapsule) -> &'a WorkerPayloadBinding {
        match self {
            Self::Service => &capsule.service,
            Self::Controller => &capsule.controller,
            Self::InstallHelper => &capsule.install_helper,
            Self::LifecycleDriver => &capsule.lifecycle_driver,
            Self::BridgeLauncher => &capsule.bridge_launcher,
            Self::RuntimeSourceManifest => &capsule.runtime_source_manifest,
        }
    }

    pub(super) fn staging_relative_name(self, capsule: &MaintenanceWorkerCapsule) -> String {
        format!("{}.{}.stage", self.as_str(), self.source(capsule).sha256)
    }
}

pub(super) fn worker_durable_stage_file_readback_receipt(
    kind: StagedPayloadKind,
    capsule: &MaintenanceWorkerCapsule,
    volume_serial: u64,
    file_id: &[u8; 16],
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let source = kind.source(capsule);
    let source_sha256 = decode_nonzero_hex_32(&source.sha256).ok_or(AuthorityMaintenanceError(
        "authority_worker_durable_staging_invalid",
    ))?;
    if volume_serial == 0 || file_id.iter().all(|value| *value == 0) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_durable_staging_invalid",
        ));
    }
    let mut digest = Sha256::new();
    digest.update(WORKER_STAGING_FILE_READBACK_DOMAIN);
    digest.update(capsule.digest()?);
    digest.update(kind.as_str().as_bytes());
    digest.update([0]);
    digest.update(source_sha256);
    digest.update(source.byte_length.to_le_bytes());
    digest.update(volume_serial.to_le_bytes());
    digest.update(file_id);
    Ok(digest.finalize().into())
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct DurableStagedPayloadBinding {
    payload: String,
    staging_relative_name: String,
    sha256: String,
    byte_length: u64,
    source_volume_serial: u64,
    source_file_id: String,
    source_full_readback_receipt_sha256: String,
    volume_serial: u64,
    file_id: String,
    full_readback_receipt_sha256: String,
    create_new: bool,
    never_reuse: bool,
    local_volume: bool,
    reparse_rejected: bool,
    single_link: bool,
    exact_owner_and_acl: bool,
    file_flushed_before_readback: bool,
    stable_identity_reverified: bool,
}

impl DurableStagedPayloadBinding {
    pub(super) fn from_observed(
        kind: StagedPayloadKind,
        capsule: &MaintenanceWorkerCapsule,
        source_volume_serial: u64,
        source_file_id: [u8; 16],
        source_full_readback_receipt_sha256: [u8; 32],
        volume_serial: u64,
        file_id: [u8; 16],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let source = kind.source(capsule);
        let value = Self {
            payload: kind.as_str().to_string(),
            staging_relative_name: kind.staging_relative_name(capsule),
            sha256: source.sha256.clone(),
            byte_length: source.byte_length,
            source_volume_serial,
            source_file_id: hex_lower(&source_file_id),
            source_full_readback_receipt_sha256: hex_lower(&source_full_readback_receipt_sha256),
            volume_serial,
            file_id: hex_lower(&file_id),
            full_readback_receipt_sha256: hex_lower(&worker_durable_stage_file_readback_receipt(
                kind,
                capsule,
                volume_serial,
                &file_id,
            )?),
            create_new: true,
            never_reuse: true,
            local_volume: true,
            reparse_rejected: true,
            single_link: true,
            exact_owner_and_acl: true,
            file_flushed_before_readback: true,
            stable_identity_reverified: true,
        };
        if !value.validates_for(kind, capsule, volume_serial) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_durable_staging_invalid",
            ));
        }
        Ok(value)
    }

    fn validates_for(
        &self,
        kind: StagedPayloadKind,
        capsule: &MaintenanceWorkerCapsule,
        staging_volume_serial: u64,
    ) -> bool {
        let source = kind.source(capsule);
        self.payload == kind.as_str()
            && self.staging_relative_name == kind.staging_relative_name(capsule)
            && self.sha256 == source.sha256
            && self.byte_length == source.byte_length
            && self.source_volume_serial == source.volume_serial
            && self.source_file_id == source.file_id
            && self.source_full_readback_receipt_sha256 == source.full_readback_receipt_sha256
            && self.volume_serial == staging_volume_serial
            && self.volume_serial != 0
            && decode_hex_16(&self.file_id).is_some()
            && decode_hex_32(&self.full_readback_receipt_sha256).ok()
                == worker_durable_stage_file_readback_receipt(
                    kind,
                    capsule,
                    self.volume_serial,
                    &decode_hex_16(&self.file_id).unwrap_or([0; 16]),
                )
                .ok()
            && self.create_new
            && self.never_reuse
            && self.local_volume
            && self.reparse_rejected
            && self.single_link
            && self.exact_owner_and_acl
            && self.file_flushed_before_readback
            && self.stable_identity_reverified
    }

    pub(super) fn staging_relative_name(&self) -> &str {
        &self.staging_relative_name
    }

    pub(super) fn validate_reopened_file(
        &self,
        kind: StagedPayloadKind,
        capsule: &MaintenanceWorkerCapsule,
        volume_serial: u64,
        file_id: [u8; 16],
        byte_length: u64,
        sha256: [u8; 32],
    ) -> Result<(), AuthorityMaintenanceError> {
        if !self.validates_for(kind, capsule, volume_serial)
            || decode_hex_16(&self.file_id) != Some(file_id)
            || self.byte_length != byte_length
            || decode_hex_32(&self.sha256)? != sha256
            || decode_hex_32(&self.full_readback_receipt_sha256)?
                != worker_durable_stage_file_readback_receipt(
                    kind,
                    capsule,
                    volume_serial,
                    &file_id,
                )?
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_durable_staging_readback_mismatch",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerSourceIdentityLedger {
    schema: String,
    capsule_sha256: String,
    worker_started_record_sha256: String,
    handle_handoff_receipt_sha256: String,
    staging_volume_serial: u64,
    staging_directory_file_id: String,
    service: DurableStagedPayloadBinding,
    controller: DurableStagedPayloadBinding,
    install_helper: DurableStagedPayloadBinding,
    lifecycle_driver: DurableStagedPayloadBinding,
    bridge_launcher: DurableStagedPayloadBinding,
    runtime_source_manifest: DurableStagedPayloadBinding,
}

impl WorkerSourceIdentityLedger {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
        staging_volume_serial: u64,
        staging_directory_file_id: [u8; 16],
        service: DurableStagedPayloadBinding,
        controller: DurableStagedPayloadBinding,
        install_helper: DurableStagedPayloadBinding,
        lifecycle_driver: DurableStagedPayloadBinding,
        bridge_launcher: DurableStagedPayloadBinding,
        runtime_source_manifest: DurableStagedPayloadBinding,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            schema: WORKER_SOURCE_IDENTITY_LEDGER_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            worker_started_record_sha256: worker_started.record_sha256.clone(),
            handle_handoff_receipt_sha256: hex_lower(&handoff.digest()?),
            staging_volume_serial,
            staging_directory_file_id: hex_lower(&staging_directory_file_id),
            service,
            controller,
            install_helper,
            lifecycle_driver,
            bridge_launcher,
            runtime_source_manifest,
        };
        value.validate(capsule, worker_started, handoff)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        handoff.validate(capsule)?;
        let directory_id = decode_hex_16(&self.staging_directory_file_id);
        if self.schema != WORKER_SOURCE_IDENTITY_LEDGER_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || worker_started.phase != MaintenanceWorkerPhase::WorkerStarted
            || self.worker_started_record_sha256 != worker_started.record_sha256
            || decode_hex_32(&self.handle_handoff_receipt_sha256)? != handoff.digest()?
            || self.staging_volume_serial == 0
            || directory_id.is_none()
            || !self.service.validates_for(
                StagedPayloadKind::Service,
                capsule,
                self.staging_volume_serial,
            )
            || !self.controller.validates_for(
                StagedPayloadKind::Controller,
                capsule,
                self.staging_volume_serial,
            )
            || !self.install_helper.validates_for(
                StagedPayloadKind::InstallHelper,
                capsule,
                self.staging_volume_serial,
            )
            || !self.lifecycle_driver.validates_for(
                StagedPayloadKind::LifecycleDriver,
                capsule,
                self.staging_volume_serial,
            )
            || !self.bridge_launcher.validates_for(
                StagedPayloadKind::BridgeLauncher,
                capsule,
                self.staging_volume_serial,
            )
            || !self.runtime_source_manifest.validates_for(
                StagedPayloadKind::RuntimeSourceManifest,
                capsule,
                self.staging_volume_serial,
            )
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_source_identity_ledger_invalid",
            ));
        }
        let mut file_ids = [
            self.service.file_id.as_str(),
            self.controller.file_id.as_str(),
            self.install_helper.file_id.as_str(),
            self.lifecycle_driver.file_id.as_str(),
            self.bridge_launcher.file_id.as_str(),
            self.runtime_source_manifest.file_id.as_str(),
        ];
        file_ids.sort();
        if file_ids.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_source_identity_ledger_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn canonical_bytes(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
    ) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        self.validate(capsule, worker_started, handoff)?;
        serde_json::to_vec(self).map_err(|_| {
            AuthorityMaintenanceError("authority_worker_source_identity_ledger_encode_failed")
        })
    }

    pub(super) fn parse_canonical(
        bytes: &[u8],
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if bytes.is_empty() || bytes.len() > MAX_WORKER_RECEIPT_BYTES {
            return Err(AuthorityMaintenanceError(
                "authority_worker_source_identity_ledger_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes).map_err(|_| {
            AuthorityMaintenanceError("authority_worker_source_identity_ledger_invalid")
        })?;
        value.validate(capsule, worker_started, handoff)?;
        if value.canonical_bytes(capsule, worker_started, handoff)? != bytes {
            return Err(AuthorityMaintenanceError(
                "authority_worker_source_identity_ledger_not_canonical",
            ));
        }
        Ok(value)
    }

    pub(super) fn digest(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
    ) -> Result<[u8; 32], AuthorityMaintenanceError> {
        Ok(Sha256::digest(self.canonical_bytes(capsule, worker_started, handoff)?).into())
    }

    pub(super) fn staging_identity(&self) -> Result<(u64, [u8; 16]), AuthorityMaintenanceError> {
        Ok((
            self.staging_volume_serial,
            decode_hex_16(&self.staging_directory_file_id).ok_or(AuthorityMaintenanceError(
                "authority_worker_source_identity_ledger_invalid",
            ))?,
        ))
    }

    pub(super) fn payload(&self, kind: StagedPayloadKind) -> &DurableStagedPayloadBinding {
        match kind {
            StagedPayloadKind::Service => &self.service,
            StagedPayloadKind::Controller => &self.controller,
            StagedPayloadKind::InstallHelper => &self.install_helper,
            StagedPayloadKind::LifecycleDriver => &self.lifecycle_driver,
            StagedPayloadKind::BridgeLauncher => &self.bridge_launcher,
            StagedPayloadKind::RuntimeSourceManifest => &self.runtime_source_manifest,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct DurableSourceStagingReceipt {
    schema: String,
    capsule_sha256: String,
    generation: String,
    plan_sha256: String,
    transaction_sha256: String,
    worker_started_record_sha256: String,
    handle_handoff_receipt_sha256: String,
    staging_directory_relative_name: String,
    staging_volume_serial: u64,
    staging_directory_file_id: String,
    service: DurableStagedPayloadBinding,
    controller: DurableStagedPayloadBinding,
    install_helper: DurableStagedPayloadBinding,
    lifecycle_driver: DurableStagedPayloadBinding,
    bridge_launcher: DurableStagedPayloadBinding,
    runtime_source_manifest: DurableStagedPayloadBinding,
    identity_ledger_sha256: String,
    directory_flushed_after_files: bool,
    identity_ledger_flushed: bool,
    source_paths_persisted: bool,
    durable_before_helper_ack: bool,
    receipt_sha256: String,
}

impl DurableSourceStagingReceipt {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
        identity_ledger: &WorkerSourceIdentityLedger,
    ) -> Result<Self, AuthorityMaintenanceError> {
        identity_ledger.validate(capsule, worker_started, handoff)?;
        let capsule_sha256 = capsule.digest()?;
        let mut value = Self {
            schema: WORKER_DURABLE_STAGING_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule_sha256),
            generation: hex_lower(&capsule.generation()?),
            plan_sha256: hex_lower(&capsule.plan_sha256()?),
            transaction_sha256: hex_lower(&capsule.transaction_sha256()?),
            worker_started_record_sha256: worker_started.record_sha256.clone(),
            handle_handoff_receipt_sha256: hex_lower(&handoff.digest()?),
            staging_directory_relative_name: format!("stage.{}", hex_lower(&capsule_sha256)),
            staging_volume_serial: identity_ledger.staging_volume_serial,
            staging_directory_file_id: identity_ledger.staging_directory_file_id.clone(),
            service: identity_ledger.service.clone(),
            controller: identity_ledger.controller.clone(),
            install_helper: identity_ledger.install_helper.clone(),
            lifecycle_driver: identity_ledger.lifecycle_driver.clone(),
            bridge_launcher: identity_ledger.bridge_launcher.clone(),
            runtime_source_manifest: identity_ledger.runtime_source_manifest.clone(),
            identity_ledger_sha256: hex_lower(&identity_ledger.digest(
                capsule,
                worker_started,
                handoff,
            )?),
            directory_flushed_after_files: true,
            identity_ledger_flushed: true,
            source_paths_persisted: false,
            durable_before_helper_ack: true,
            receipt_sha256: String::new(),
        };
        value.receipt_sha256 = hex_lower(&value.compute_digest()?);
        value.validate(capsule, worker_started, handoff)?;
        Ok(value)
    }

    pub(super) fn validate_identity_ledger(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
        identity_ledger: &WorkerSourceIdentityLedger,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.validate(capsule, worker_started, handoff)?;
        identity_ledger.validate(capsule, worker_started, handoff)?;
        if decode_hex_32(&self.identity_ledger_sha256)?
            != identity_ledger.digest(capsule, worker_started, handoff)?
            || self.staging_volume_serial != identity_ledger.staging_volume_serial
            || self.staging_directory_file_id != identity_ledger.staging_directory_file_id
            || self.service != identity_ledger.service
            || self.controller != identity_ledger.controller
            || self.install_helper != identity_ledger.install_helper
            || self.lifecycle_driver != identity_ledger.lifecycle_driver
            || self.bridge_launcher != identity_ledger.bridge_launcher
            || self.runtime_source_manifest != identity_ledger.runtime_source_manifest
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_source_identity_ledger_mismatch",
            ));
        }
        Ok(())
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        let capsule_sha256 = capsule.digest()?;
        handoff.validate(capsule)?;
        if worker_started.phase != MaintenanceWorkerPhase::WorkerStarted
            || decode_hex_32(&worker_started.capsule_sha256)? != capsule_sha256
            || self.schema != WORKER_DURABLE_STAGING_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule_sha256
            || decode_hex_32(&self.generation)? != capsule.generation()?
            || decode_hex_32(&self.plan_sha256)? != capsule.plan_sha256()?
            || decode_hex_32(&self.transaction_sha256)? != capsule.transaction_sha256()?
            || self.worker_started_record_sha256 != worker_started.record_sha256
            || decode_hex_32(&self.handle_handoff_receipt_sha256)? != handoff.digest()?
            || self.staging_directory_relative_name
                != format!("stage.{}", hex_lower(&capsule_sha256))
            || self.staging_volume_serial == 0
            || decode_hex_16(&self.staging_directory_file_id).is_none()
            || !self.service.validates_for(
                StagedPayloadKind::Service,
                capsule,
                self.staging_volume_serial,
            )
            || !self.controller.validates_for(
                StagedPayloadKind::Controller,
                capsule,
                self.staging_volume_serial,
            )
            || !self.install_helper.validates_for(
                StagedPayloadKind::InstallHelper,
                capsule,
                self.staging_volume_serial,
            )
            || !self.lifecycle_driver.validates_for(
                StagedPayloadKind::LifecycleDriver,
                capsule,
                self.staging_volume_serial,
            )
            || !self.bridge_launcher.validates_for(
                StagedPayloadKind::BridgeLauncher,
                capsule,
                self.staging_volume_serial,
            )
            || !self.runtime_source_manifest.validates_for(
                StagedPayloadKind::RuntimeSourceManifest,
                capsule,
                self.staging_volume_serial,
            )
            || decode_nonzero_hex_32(&self.identity_ledger_sha256).is_none()
            || !self.directory_flushed_after_files
            || !self.identity_ledger_flushed
            || self.source_paths_persisted
            || !self.durable_before_helper_ack
            || decode_hex_32(&self.receipt_sha256)? != self.compute_digest()?
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_durable_staging_invalid",
            ));
        }
        let mut file_ids = [
            self.service.file_id.as_str(),
            self.controller.file_id.as_str(),
            self.install_helper.file_id.as_str(),
            self.lifecycle_driver.file_id.as_str(),
            self.bridge_launcher.file_id.as_str(),
            self.runtime_source_manifest.file_id.as_str(),
        ];
        file_ids.sort();
        if file_ids.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_durable_staging_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn canonical_bytes(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
    ) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        self.validate(capsule, worker_started, handoff)?;
        serde_json::to_vec(self)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_staging_encode_failed"))
    }

    pub(super) fn parse_canonical(
        bytes: &[u8],
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if bytes.is_empty() || bytes.len() > MAX_WORKER_RECEIPT_BYTES {
            return Err(AuthorityMaintenanceError(
                "authority_worker_durable_staging_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_durable_staging_invalid"))?;
        value.validate(capsule, worker_started, handoff)?;
        if value.canonical_bytes(capsule, worker_started, handoff)? != bytes {
            return Err(AuthorityMaintenanceError(
                "authority_worker_durable_staging_not_canonical",
            ));
        }
        Ok(value)
    }

    pub(super) fn digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.receipt_sha256)
    }

    fn compute_digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        let mut unsigned = self.clone();
        unsigned.receipt_sha256.clear();
        let canonical = serde_json::to_vec(&unsigned)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_staging_encode_failed"))?;
        let mut digest = Sha256::new();
        digest.update(WORKER_STAGING_DOMAIN);
        digest.update(canonical);
        Ok(digest.finalize().into())
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(super) enum WorkerStagingTerminalDisposition {
    RemovedAfterCommit,
    RemovedAfterRollback,
    SealedContained,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WorkerStagingCleanupReceipt {
    schema: String,
    capsule_sha256: String,
    durable_staging_receipt_sha256: String,
    transaction_terminal_record_sha256: String,
    disposition: WorkerStagingTerminalDisposition,
    staging_path_absent: bool,
    adopted_generation_readback_sha256: Option<String>,
    containment_seal_sha256: Option<String>,
    zero_unexpected_entries: bool,
    all_staging_handles_closed: bool,
    cleanup_parent_flushed: bool,
    cleanup_readback_sha256: String,
    receipt_sha256: String,
}

impl WorkerStagingCleanupReceipt {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn from_observed(
        capsule: &MaintenanceWorkerCapsule,
        staging: &DurableSourceStagingReceipt,
        terminal: &MaintenanceWorkerJournalRecord,
        disposition: WorkerStagingTerminalDisposition,
        staging_path_absent: bool,
        adopted_generation_readback_sha256: Option<[u8; 32]>,
        containment_seal_sha256: Option<[u8; 32]>,
        cleanup_readback_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let mut value = Self {
            schema: WORKER_STAGING_CLEANUP_SCHEMA.to_string(),
            capsule_sha256: hex_lower(&capsule.digest()?),
            durable_staging_receipt_sha256: hex_lower(&staging.digest()?),
            transaction_terminal_record_sha256: terminal.record_sha256.clone(),
            disposition,
            staging_path_absent,
            adopted_generation_readback_sha256: adopted_generation_readback_sha256
                .map(|value| hex_lower(&value)),
            containment_seal_sha256: containment_seal_sha256.map(|value| hex_lower(&value)),
            zero_unexpected_entries: true,
            all_staging_handles_closed: true,
            cleanup_parent_flushed: true,
            cleanup_readback_sha256: hex_lower(&cleanup_readback_sha256),
            receipt_sha256: String::new(),
        };
        value.receipt_sha256 = hex_lower(&value.compute_digest()?);
        value.validate(capsule, staging, terminal)?;
        Ok(value)
    }

    pub(super) fn validate(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        staging: &DurableSourceStagingReceipt,
        terminal: &MaintenanceWorkerJournalRecord,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.validate_self_sealed()?;
        let terminal_shape = match self.disposition {
            WorkerStagingTerminalDisposition::RemovedAfterCommit => {
                terminal.phase == MaintenanceWorkerPhase::TransactionCommitted
                    && self.staging_path_absent
                    && self
                        .adopted_generation_readback_sha256
                        .as_deref()
                        .and_then(decode_nonzero_hex_32)
                        .is_some()
                    && self.containment_seal_sha256.is_none()
            }
            WorkerStagingTerminalDisposition::RemovedAfterRollback => {
                terminal.phase == MaintenanceWorkerPhase::TransactionContained
                    && self.staging_path_absent
                    && self.adopted_generation_readback_sha256.is_none()
                    && self
                        .containment_seal_sha256
                        .as_deref()
                        .and_then(decode_nonzero_hex_32)
                        .is_some()
            }
            WorkerStagingTerminalDisposition::SealedContained => {
                terminal.phase == MaintenanceWorkerPhase::TransactionContained
                    && !self.staging_path_absent
                    && self.adopted_generation_readback_sha256.is_none()
                    && self
                        .containment_seal_sha256
                        .as_deref()
                        .and_then(decode_nonzero_hex_32)
                        .is_some()
            }
        };
        if self.schema != WORKER_STAGING_CLEANUP_SCHEMA
            || decode_hex_32(&self.capsule_sha256)? != capsule.digest()?
            || decode_hex_32(&self.durable_staging_receipt_sha256)? != staging.digest()?
            || self.transaction_terminal_record_sha256 != terminal.record_sha256
            || !terminal.phase.is_transaction_terminal()
            || !terminal_shape
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_staging_cleanup_invalid",
            ));
        }
        Ok(())
    }

    fn validate_self_sealed(&self) -> Result<(), AuthorityMaintenanceError> {
        if !self.zero_unexpected_entries
            || !self.all_staging_handles_closed
            || !self.cleanup_parent_flushed
            || decode_nonzero_hex_32(&self.cleanup_readback_sha256).is_none()
            || decode_hex_32(&self.receipt_sha256)? != self.compute_digest()?
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_staging_cleanup_invalid",
            ));
        }
        Ok(())
    }

    fn proves_staging_absent_for_service_finalization(&self) -> bool {
        self.staging_path_absent
            && matches!(
                self.disposition,
                WorkerStagingTerminalDisposition::RemovedAfterCommit
                    | WorkerStagingTerminalDisposition::RemovedAfterRollback
            )
    }

    pub(super) fn canonical_bytes(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        staging: &DurableSourceStagingReceipt,
        terminal: &MaintenanceWorkerJournalRecord,
    ) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        self.validate(capsule, staging, terminal)?;
        serde_json::to_vec(self)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_cleanup_encode_failed"))
    }

    pub(super) fn parse_canonical(
        bytes: &[u8],
        capsule: &MaintenanceWorkerCapsule,
        staging: &DurableSourceStagingReceipt,
        terminal: &MaintenanceWorkerJournalRecord,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if bytes.is_empty() || bytes.len() > MAX_WORKER_RECEIPT_BYTES {
            return Err(AuthorityMaintenanceError(
                "authority_worker_staging_cleanup_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_staging_cleanup_invalid"))?;
        value.validate(capsule, staging, terminal)?;
        if value.canonical_bytes(capsule, staging, terminal)? != bytes {
            return Err(AuthorityMaintenanceError(
                "authority_worker_staging_cleanup_not_canonical",
            ));
        }
        Ok(value)
    }

    pub(super) fn digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.receipt_sha256)
    }

    fn compute_digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        let mut unsigned = self.clone();
        unsigned.receipt_sha256.clear();
        let canonical = serde_json::to_vec(&unsigned)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_cleanup_encode_failed"))?;
        let mut digest = Sha256::new();
        digest.update(WORKER_STAGING_CLEANUP_DOMAIN);
        digest.update(canonical);
        Ok(digest.finalize().into())
    }
}

pub(super) fn worker_handoff_pipe_name(
    capsule: &MaintenanceWorkerCapsule,
) -> Result<String, AuthorityMaintenanceError> {
    let capsule_sha256 = capsule.digest()?;
    let pipe_nonce = capsule.worker_pipe_nonce()?;
    Ok(format!(
        "{WORKER_HANDOFF_PIPE_PREFIX}{}.{}",
        hex_lower(&capsule_sha256),
        hex_lower(&pipe_nonce)
    ))
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(super) struct MaintenanceWorkerLaunchContract {
    pub(super) service_name: &'static str,
    pub(super) display_name: &'static str,
    pub(super) account: &'static str,
    pub(super) start_type: &'static str,
    pub(super) service_type: &'static str,
    pub(super) error_control: &'static str,
    pub(super) binary_command: String,
    pub(super) capsule_sha256: String,
    worker_pipe_name: String,
    worker_pipe_server_precedes_service_create: bool,
    worker_image_sha256: String,
    worker_path_create_new: bool,
    worker_path_content_addressed: bool,
    worker_path_exact_acl: bool,
    worker_image_held_through_service_start: bool,
    pub(super) service_sddl: &'static str,
    pub(super) stop_wait_delete_after_transaction: bool,
}

impl MaintenanceWorkerLaunchContract {
    pub(super) fn new(
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let capsule_sha256 = capsule.digest()?;
        let worker_executable = layout
            .maintenance_worker_executable(&capsule_sha256)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?;
        let executable = worker_executable
            .to_str()
            .ok_or(AuthorityMaintenanceError("authority_worker_path_invalid"))?;
        if executable.is_empty()
            || executable.contains('"')
            || !worker_executable.is_absolute()
            || capsule_sha256.iter().all(|value| *value == 0)
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_launch_contract_invalid",
            ));
        }
        let capsule_hex = hex_lower(&capsule_sha256);
        Ok(Self {
            service_name: MAINTENANCE_WORKER_SERVICE_NAME,
            display_name: MAINTENANCE_WORKER_DISPLAY_NAME,
            account: "LocalSystem",
            start_type: "demand",
            service_type: "ownProcess",
            error_control: "normal",
            binary_command: format!("\"{executable}\" --maintenance-worker {capsule_hex}"),
            capsule_sha256: capsule_hex,
            worker_pipe_name: worker_handoff_pipe_name(capsule)?,
            worker_pipe_server_precedes_service_create: true,
            worker_image_sha256: capsule.install_helper.sha256.clone(),
            worker_path_create_new: true,
            worker_path_content_addressed: true,
            worker_path_exact_acl: true,
            worker_image_held_through_service_start: true,
            service_sddl: SERVICE_SECURITY_SDDL,
            stop_wait_delete_after_transaction: true,
        })
    }

    pub(super) fn binary_command(&self) -> &str {
        &self.binary_command
    }

    pub(super) fn capsule_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.capsule_sha256)
    }

    pub(super) fn worker_image_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.worker_image_sha256)
    }

    pub(super) fn digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        if !self.worker_staging_contract_exact()
            || self.capsule_sha256()?.iter().all(|value| *value == 0)
            || self.worker_image_sha256()?.iter().all(|value| *value == 0)
            || self.worker_pipe_name.is_empty()
            || !self.worker_pipe_server_precedes_service_create
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_launch_contract_invalid",
            ));
        }
        let canonical = serde_json::to_vec(self)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_launch_contract_invalid"))?;
        let mut digest = Sha256::new();
        digest.update(WORKER_LAUNCH_DOMAIN);
        digest.update(canonical);
        Ok(digest.finalize().into())
    }

    pub(super) fn worker_staging_contract_exact(&self) -> bool {
        self.worker_path_create_new
            && self.worker_path_content_addressed
            && self.worker_path_exact_acl
            && self.worker_image_held_through_service_start
            && self.worker_pipe_server_precedes_service_create
            && self.stop_wait_delete_after_transaction
    }
}

#[repr(u8)]
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "camelCase")]
pub(super) enum MaintenanceWorkerPhase {
    BootstrapIntent = 1,
    CapsuleStaged = 2,
    PipePrepared = 3,
    PipeRecovered = 4,
    ServiceCreated = 5,
    WorkerInvocationClaimed = 6,
    WorkerStarted = 7,
    SourceStagingIntent = 8,
    SourceStagingContained = 9,
    SourceHandlesBound = 10,
    TransactionStarted = 11,
    TransactionCommitted = 12,
    TransactionContained = 13,
    ServiceDeletePending = 14,
    ServiceAbsent = 15,
    SourceStageResolved = 16,
    ExitReady = 17,
    FinalizerHandlesClosed = 18,
    CandidateCredentialArmed = 19,
    ServiceDeleteIntent = 20,
}

impl MaintenanceWorkerPhase {
    pub(super) fn is_transaction_terminal(self) -> bool {
        matches!(
            self,
            Self::TransactionCommitted | Self::TransactionContained
        )
    }

    fn transition_allowed(self, next: Self) -> bool {
        matches!(
            (self, next),
            (Self::BootstrapIntent, Self::CapsuleStaged)
                | (Self::CapsuleStaged, Self::PipePrepared)
                | (Self::PipePrepared, Self::PipeRecovered)
                | (Self::PipePrepared, Self::ServiceCreated)
                | (Self::PipeRecovered, Self::ServiceCreated)
                // Retained for canonical v1 source-contract fixtures. The native
                // writer always persists a PipePrepared phase first.
                | (Self::CapsuleStaged, Self::ServiceCreated)
                | (Self::ServiceCreated, Self::WorkerInvocationClaimed)
                | (Self::WorkerInvocationClaimed, Self::WorkerStarted)
                // Retained for canonical v1 source-contract fixtures. The native
                // writer always persists a one-use invocation claim first.
                | (Self::ServiceCreated, Self::WorkerStarted)
                | (Self::WorkerStarted, Self::SourceStagingIntent)
                | (Self::SourceStagingIntent, Self::SourceStagingContained)
                | (Self::SourceStagingIntent, Self::SourceHandlesBound)
                | (Self::SourceHandlesBound, Self::SourceStagingContained)
                // Retained for canonical v1 source-contract fixtures. The native
                // writer always uses SourceStagingIntent before its first write.
                | (Self::WorkerStarted, Self::SourceHandlesBound)
                | (Self::SourceHandlesBound, Self::TransactionStarted)
                | (Self::TransactionStarted, Self::TransactionCommitted)
                | (Self::TransactionStarted, Self::TransactionContained)
                | (Self::TransactionStarted, Self::CandidateCredentialArmed)
                | (Self::CandidateCredentialArmed, Self::TransactionCommitted)
                | (Self::CandidateCredentialArmed, Self::TransactionContained)
                | (Self::TransactionCommitted, Self::SourceStageResolved)
                | (Self::TransactionContained, Self::SourceStageResolved)
                | (Self::SourceStageResolved, Self::ExitReady)
                | (Self::ExitReady, Self::ServiceDeleteIntent)
                | (Self::ServiceDeleteIntent, Self::ServiceDeletePending)
                | (Self::ServiceDeletePending, Self::FinalizerHandlesClosed)
                | (Self::FinalizerHandlesClosed, Self::ServiceAbsent)
        )
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct MaintenanceWorkerJournalRecord {
    sequence: u64,
    capsule_sha256: String,
    phase: MaintenanceWorkerPhase,
    previous_record_sha256: String,
    phase_receipt_sha256: String,
    pub(super) record_sha256: String,
}

impl MaintenanceWorkerJournalRecord {
    pub(super) fn first_intent(
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        intent: &WorkerBootstrapIntentReceipt,
    ) -> Result<Self, AuthorityMaintenanceError> {
        intent.validate(capsule, launch)?;
        Ok(Self::new(
            0,
            capsule.digest()?,
            MaintenanceWorkerPhase::BootstrapIntent,
            [0; 32],
            intent.digest()?,
        ))
    }

    pub(super) fn phase_receipt_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.phase_receipt_sha256)
    }

    pub(super) fn phase(&self) -> MaintenanceWorkerPhase {
        self.phase
    }

    pub(super) fn sequence(&self) -> u64 {
        self.sequence
    }

    pub(super) fn record_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        decode_hex_32(&self.record_sha256)
    }

    fn new(
        sequence: u64,
        capsule_sha256: [u8; 32],
        phase: MaintenanceWorkerPhase,
        previous_record_sha256: [u8; 32],
        phase_receipt_sha256: [u8; 32],
    ) -> Self {
        let record_sha256 = worker_journal_record_digest(
            sequence,
            &capsule_sha256,
            phase,
            &previous_record_sha256,
            &phase_receipt_sha256,
        );
        Self {
            sequence,
            capsule_sha256: hex_lower(&capsule_sha256),
            phase,
            previous_record_sha256: hex_lower(&previous_record_sha256),
            phase_receipt_sha256: hex_lower(&phase_receipt_sha256),
            record_sha256: hex_lower(&record_sha256),
        }
    }

    fn validates_after(&self, prior: Option<&Self>) -> bool {
        let Ok(capsule) = decode_hex_32(&self.capsule_sha256) else {
            return false;
        };
        let Ok(previous) = decode_hex_32(&self.previous_record_sha256) else {
            return false;
        };
        let Ok(record) = decode_hex_32(&self.record_sha256) else {
            return false;
        };
        let Ok(phase_receipt) = decode_hex_32(&self.phase_receipt_sha256) else {
            return false;
        };
        if phase_receipt.iter().all(|value| *value == 0)
            || record
                != worker_journal_record_digest(
                    self.sequence,
                    &capsule,
                    self.phase,
                    &previous,
                    &phase_receipt,
                )
        {
            return false;
        }
        match prior {
            None => {
                self.sequence == 0
                    && previous == [0; 32]
                    && self.phase == MaintenanceWorkerPhase::BootstrapIntent
            }
            Some(prior) => {
                prior.sequence.checked_add(1) == Some(self.sequence)
                    && self.capsule_sha256 == prior.capsule_sha256
                    && self.previous_record_sha256 == prior.record_sha256
                    && prior.phase.transition_allowed(self.phase)
            }
        }
    }
}

fn append_authorized_phase(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    expected_prior: MaintenanceWorkerPhase,
    phase: MaintenanceWorkerPhase,
    receipt_sha256: [u8; 32],
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    validate_worker_journal(capsule.digest()?, records)?;
    let prior = records.last().ok_or(AuthorityMaintenanceError(
        "authority_worker_journal_missing",
    ))?;
    if prior.phase != expected_prior
        || !expected_prior.transition_allowed(phase)
        || receipt_sha256.iter().all(|value| *value == 0)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_phase_transition_not_authorized",
        ));
    }
    Ok(MaintenanceWorkerJournalRecord::new(
        prior
            .sequence
            .checked_add(1)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_journal_sequence_exhausted",
            ))?,
        capsule.digest()?,
        phase,
        decode_hex_32(&prior.record_sha256)?,
        receipt_sha256,
    ))
}

pub(super) fn authorize_capsule_staged(
    capsule: &MaintenanceWorkerCapsule,
    launch: &MaintenanceWorkerLaunchContract,
    records: &[MaintenanceWorkerJournalRecord],
    intent: &WorkerBootstrapIntentReceipt,
    bootstrap: &WorkerBootstrapStagingReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    intent.validate(capsule, launch)?;
    bootstrap.validate(capsule, launch)?;
    if records.len() != 1
        || validate_worker_journal(capsule.digest()?, records)?
            != MaintenanceWorkerRecoveryDisposition::ResumeSameCapsuleBeforeTransaction
        || records[0].phase != MaintenanceWorkerPhase::BootstrapIntent
        || records[0].phase_receipt_sha256()? != intent.digest()?
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_bootstrap_staging_phase_invalid",
        ));
    }
    let prior = &records[0];
    Ok(MaintenanceWorkerJournalRecord::new(
        prior
            .sequence
            .checked_add(1)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_journal_sequence_exhausted",
            ))?,
        capsule.digest()?,
        MaintenanceWorkerPhase::CapsuleStaged,
        decode_hex_32(&prior.record_sha256)?,
        bootstrap.digest()?,
    ))
}

pub(super) fn authorize_service_created(
    capsule: &MaintenanceWorkerCapsule,
    launch: &MaintenanceWorkerLaunchContract,
    bootstrap: &WorkerBootstrapStagingReceipt,
    pipe: &WorkerPipePreparedReceipt,
    records: &[MaintenanceWorkerJournalRecord],
    receipt: &ServiceCreatedReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    receipt.validate(capsule, launch, bootstrap, pipe)?;
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::CapsuleStaged,
        MaintenanceWorkerPhase::ServiceCreated,
        receipt.digest()?,
    )
}

pub(super) fn authorize_pipe_prepared(
    capsule: &MaintenanceWorkerCapsule,
    launch: &MaintenanceWorkerLaunchContract,
    records: &[MaintenanceWorkerJournalRecord],
    pipe: &WorkerPipePreparedReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    pipe.validate(capsule, launch)?;
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::CapsuleStaged,
        MaintenanceWorkerPhase::PipePrepared,
        pipe.digest()?,
    )
}

pub(super) fn authorize_pipe_recovered(
    capsule: &MaintenanceWorkerCapsule,
    launch: &MaintenanceWorkerLaunchContract,
    records: &[MaintenanceWorkerJournalRecord],
    prior: &WorkerPipePreparedReceipt,
    recovery: &WorkerPipeRecoveryReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    recovery.validate(capsule, launch, records, prior)?;
    let prepared = records.last().ok_or(AuthorityMaintenanceError(
        "authority_worker_journal_missing",
    ))?;
    if prepared.phase != MaintenanceWorkerPhase::PipePrepared
        || prepared.phase_receipt_sha256()? != prior.digest()?
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_pipe_recovery_phase_invalid",
        ));
    }
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::PipePrepared,
        MaintenanceWorkerPhase::PipeRecovered,
        recovery.digest()?,
    )
}

#[allow(clippy::too_many_arguments)]
pub(super) fn authorize_service_created_after_pipe(
    capsule: &MaintenanceWorkerCapsule,
    launch: &MaintenanceWorkerLaunchContract,
    bootstrap: &WorkerBootstrapStagingReceipt,
    pipe: &WorkerPipePreparedReceipt,
    records: &[MaintenanceWorkerJournalRecord],
    pipe_phase_receipt_sha256: [u8; 32],
    receipt: &ServiceCreatedReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    receipt.validate(capsule, launch, bootstrap, pipe)?;
    let prior = records.last().ok_or(AuthorityMaintenanceError(
        "authority_worker_journal_missing",
    ))?;
    if !matches!(
        prior.phase,
        MaintenanceWorkerPhase::PipePrepared | MaintenanceWorkerPhase::PipeRecovered
    ) || prior.phase_receipt_sha256()? != pipe_phase_receipt_sha256
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_created_receipt_invalid",
        ));
    }
    append_authorized_phase(
        capsule,
        records,
        prior.phase,
        MaintenanceWorkerPhase::ServiceCreated,
        receipt.digest()?,
    )
}

#[allow(clippy::too_many_arguments)]
pub(super) fn authorize_worker_invocation_claimed(
    capsule: &MaintenanceWorkerCapsule,
    launch: &MaintenanceWorkerLaunchContract,
    bootstrap: &WorkerBootstrapStagingReceipt,
    service_created: &ServiceCreatedReceipt,
    pipe: &WorkerPipePreparedReceipt,
    handoff: &WorkerHandleHandoffReceipt,
    worker: &WorkerProcessBinding,
    records: &[MaintenanceWorkerJournalRecord],
    receipt: &WorkerInvocationClaimReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    receipt.validate(
        capsule,
        launch,
        bootstrap,
        service_created,
        pipe,
        handoff,
        worker,
    )?;
    if records
        .last()
        .and_then(|record| record.phase_receipt_sha256().ok())
        != Some(service_created.digest()?)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_invocation_claim_invalid",
        ));
    }
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::ServiceCreated,
        MaintenanceWorkerPhase::WorkerInvocationClaimed,
        receipt.digest()?,
    )
}

#[allow(clippy::too_many_arguments)]
pub(super) fn authorize_worker_started(
    capsule: &MaintenanceWorkerCapsule,
    bootstrap: &WorkerBootstrapStagingReceipt,
    service_created: &ServiceCreatedReceipt,
    pipe: &WorkerPipePreparedReceipt,
    handoff: &WorkerHandleHandoffReceipt,
    records: &[MaintenanceWorkerJournalRecord],
    receipt: &WorkerStartedReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    receipt.validate(capsule, bootstrap, service_created, pipe, handoff)?;
    if records
        .last()
        .and_then(|record| record.phase_receipt_sha256().ok())
        != Some(service_created.digest()?)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_started_receipt_invalid",
        ));
    }
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::ServiceCreated,
        MaintenanceWorkerPhase::WorkerStarted,
        receipt.digest()?,
    )
}

#[allow(clippy::too_many_arguments)]
pub(super) fn authorize_claimed_worker_started(
    capsule: &MaintenanceWorkerCapsule,
    launch: &MaintenanceWorkerLaunchContract,
    bootstrap: &WorkerBootstrapStagingReceipt,
    service_created: &ServiceCreatedReceipt,
    pipe: &WorkerPipePreparedReceipt,
    handoff: &WorkerHandleHandoffReceipt,
    claim: &WorkerInvocationClaimReceipt,
    records: &[MaintenanceWorkerJournalRecord],
    receipt: &WorkerStartedReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    receipt.validate(capsule, bootstrap, service_created, pipe, handoff)?;
    claim.validate(
        capsule,
        launch,
        bootstrap,
        service_created,
        pipe,
        handoff,
        handoff.worker(),
    )?;
    if records
        .last()
        .and_then(|record| record.phase_receipt_sha256().ok())
        != Some(claim.digest()?)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_started_receipt_invalid",
        ));
    }
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::WorkerInvocationClaimed,
        MaintenanceWorkerPhase::WorkerStarted,
        receipt.digest()?,
    )
}

pub(super) fn authorize_source_handles_bound(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    handoff: &WorkerHandleHandoffReceipt,
    staging: &DurableSourceStagingReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    if validate_worker_journal(capsule.digest()?, records)?
        != MaintenanceWorkerRecoveryDisposition::ResumeSameCapsuleBeforeTransaction
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_source_staging_phase_invalid",
        ));
    }
    let worker_started = records.last().ok_or(AuthorityMaintenanceError(
        "authority_worker_journal_missing",
    ))?;
    if worker_started.phase != MaintenanceWorkerPhase::WorkerStarted {
        return Err(AuthorityMaintenanceError(
            "authority_worker_source_staging_phase_invalid",
        ));
    }
    staging.validate(capsule, worker_started, handoff)?;
    let next_sequence = worker_started
        .sequence
        .checked_add(1)
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_journal_sequence_exhausted",
        ))?;
    Ok(MaintenanceWorkerJournalRecord::new(
        next_sequence,
        capsule.digest()?,
        MaintenanceWorkerPhase::SourceHandlesBound,
        decode_hex_32(&worker_started.record_sha256)?,
        staging.digest()?,
    ))
}

pub(super) fn authorize_source_staging_intent(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    handoff: &WorkerHandleHandoffReceipt,
    receipt: &WorkerSourceStagingIntentReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    let worker_started = records.last().ok_or(AuthorityMaintenanceError(
        "authority_worker_journal_missing",
    ))?;
    receipt.validate(capsule, worker_started, handoff)?;
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::WorkerStarted,
        MaintenanceWorkerPhase::SourceStagingIntent,
        receipt.digest()?,
    )
}

pub(super) fn authorize_source_handles_bound_after_intent(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    handoff: &WorkerHandleHandoffReceipt,
    intent: &WorkerSourceStagingIntentReceipt,
    staging: &DurableSourceStagingReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    let intent_record = records.last().ok_or(AuthorityMaintenanceError(
        "authority_worker_journal_missing",
    ))?;
    let worker_started = records
        .iter()
        .rev()
        .find(|record| record.phase == MaintenanceWorkerPhase::WorkerStarted)
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_source_staging_phase_invalid",
        ))?;
    intent.validate(capsule, worker_started, handoff)?;
    staging.validate(capsule, worker_started, handoff)?;
    if intent_record.phase != MaintenanceWorkerPhase::SourceStagingIntent
        || intent_record.phase_receipt_sha256()? != intent.digest()?
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_source_staging_phase_invalid",
        ));
    }
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::SourceStagingIntent,
        MaintenanceWorkerPhase::SourceHandlesBound,
        staging.digest()?,
    )
}

pub(super) fn authorize_partial_staging_contained(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    intent: &WorkerSourceStagingIntentReceipt,
    cleanup: &WorkerPartialStagingCleanupReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    cleanup.validate(capsule, intent)?;
    let intent_record = records.last().ok_or(AuthorityMaintenanceError(
        "authority_worker_journal_missing",
    ))?;
    let expected_prior_receipt = match intent_record.phase {
        MaintenanceWorkerPhase::SourceStagingIntent => intent.digest()?,
        MaintenanceWorkerPhase::SourceHandlesBound => intent_record.phase_receipt_sha256()?,
        _ => {
            return Err(AuthorityMaintenanceError(
                "authority_worker_partial_staging_cleanup_invalid",
            ))
        }
    };
    if intent_record.phase_receipt_sha256()? != expected_prior_receipt {
        return Err(AuthorityMaintenanceError(
            "authority_worker_partial_staging_cleanup_invalid",
        ));
    }
    append_authorized_phase(
        capsule,
        records,
        intent_record.phase,
        MaintenanceWorkerPhase::SourceStagingContained,
        cleanup.digest()?,
    )
}

pub(super) fn authorize_transaction_start(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    handoff: &WorkerHandleHandoffReceipt,
    staging: &DurableSourceStagingReceipt,
    nonce: &WorkerNonceConsumptionReceipt,
    now_unix_millis: u64,
) -> Result<(MaintenanceWorkerJournalRecord, TransactionStartedReceipt), AuthorityMaintenanceError>
{
    if validate_worker_journal(capsule.digest()?, records)?
        != MaintenanceWorkerRecoveryDisposition::ResumeSameCapsuleBeforeTransaction
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_transaction_start_not_authorized",
        ));
    }
    let source_bound = records.last().ok_or(AuthorityMaintenanceError(
        "authority_worker_journal_missing",
    ))?;
    let worker_started = records
        .iter()
        .rev()
        .find(|record| record.phase == MaintenanceWorkerPhase::WorkerStarted)
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_transaction_start_not_authorized",
        ))?;
    if source_bound.phase != MaintenanceWorkerPhase::SourceHandlesBound
        || worker_started.phase != MaintenanceWorkerPhase::WorkerStarted
        || decode_hex_32(&source_bound.phase_receipt_sha256)? != staging.digest()?
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_transaction_start_not_authorized",
        ));
    }
    staging.validate(capsule, worker_started, handoff)?;
    let receipt =
        TransactionStartedReceipt::new(capsule, source_bound, staging, nonce, now_unix_millis)?;
    let record = append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::SourceHandlesBound,
        MaintenanceWorkerPhase::TransactionStarted,
        receipt.digest()?,
    )?;
    Ok((record, receipt))
}

pub(super) fn authorize_transaction_committed(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    started: &TransactionStartedReceipt,
    receipt: &TransactionCommittedReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    receipt.validate(capsule, started)?;
    if records
        .last()
        .and_then(|record| record.phase_receipt_sha256().ok())
        != Some(started.digest()?)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_transaction_committed_receipt_invalid",
        ));
    }
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::TransactionStarted,
        MaintenanceWorkerPhase::TransactionCommitted,
        receipt.digest()?,
    )
}

pub(super) fn authorize_candidate_credential_armed(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    started: &TransactionStartedReceipt,
    worker_started: &WorkerStartedReceipt,
    prepared: &CandidateCredentialRecord,
    receipt: &CandidateCredentialArmedReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    receipt.validate(capsule, started, worker_started, prepared)?;
    if records.last().map(MaintenanceWorkerJournalRecord::phase)
        != Some(MaintenanceWorkerPhase::TransactionStarted)
        || records
            .last()
            .and_then(|record| record.phase_receipt_sha256().ok())
            != Some(started.digest()?)
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_credential_armed_not_authorized",
        ));
    }
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::TransactionStarted,
        MaintenanceWorkerPhase::CandidateCredentialArmed,
        receipt.digest()?,
    )
}

pub(super) fn authorize_transaction_committed_after_candidate_armed(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    started: &TransactionStartedReceipt,
    armed: &CandidateCredentialArmedReceipt,
    receipt: &TransactionCommittedReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    receipt.validate(capsule, started)?;
    if records.last().map(MaintenanceWorkerJournalRecord::phase)
        != Some(MaintenanceWorkerPhase::CandidateCredentialArmed)
        || records
            .last()
            .and_then(|record| record.phase_receipt_sha256().ok())
            != Some(armed.digest()?)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_transaction_committed_receipt_invalid",
        ));
    }
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::CandidateCredentialArmed,
        MaintenanceWorkerPhase::TransactionCommitted,
        receipt.digest()?,
    )
}

pub(super) fn authorize_transaction_contained(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    started: &TransactionStartedReceipt,
    receipt: &TransactionContainedReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    receipt.validate(capsule, started)?;
    if records
        .last()
        .and_then(|record| record.phase_receipt_sha256().ok())
        != Some(started.digest()?)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_transaction_contained_receipt_invalid",
        ));
    }
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::TransactionStarted,
        MaintenanceWorkerPhase::TransactionContained,
        receipt.digest()?,
    )
}

pub(super) fn authorize_transaction_contained_after_candidate_armed(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    started: &TransactionStartedReceipt,
    armed: &CandidateCredentialArmedReceipt,
    receipt: &TransactionContainedReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    receipt.validate(capsule, started)?;
    if records.last().map(MaintenanceWorkerJournalRecord::phase)
        != Some(MaintenanceWorkerPhase::CandidateCredentialArmed)
        || records
            .last()
            .and_then(|record| record.phase_receipt_sha256().ok())
            != Some(armed.digest()?)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_transaction_contained_receipt_invalid",
        ));
    }
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::CandidateCredentialArmed,
        MaintenanceWorkerPhase::TransactionContained,
        receipt.digest()?,
    )
}

pub(super) fn authorize_source_stage_resolved(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    staging: &DurableSourceStagingReceipt,
    cleanup: &WorkerStagingCleanupReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    let terminal = records.last().ok_or(AuthorityMaintenanceError(
        "authority_worker_transaction_terminal_missing",
    ))?;
    if !terminal.phase.is_transaction_terminal() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_source_stage_resolution_not_authorized",
        ));
    }
    cleanup.validate(capsule, staging, terminal)?;
    append_authorized_phase(
        capsule,
        records,
        terminal.phase,
        MaintenanceWorkerPhase::SourceStageResolved,
        cleanup.digest()?,
    )
}

pub(super) fn authorize_worker_exit_ready(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    cleanup: &WorkerStagingCleanupReceipt,
    worker_started: &WorkerStartedReceipt,
    receipt: &WorkerExitReadyReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    let source_resolved = records.last().ok_or(AuthorityMaintenanceError(
        "authority_worker_source_stage_resolution_missing",
    ))?;
    let terminal = records
        .iter()
        .rev()
        .find(|record| record.phase.is_transaction_terminal())
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_transaction_terminal_missing",
        ))?;
    if source_resolved.phase != MaintenanceWorkerPhase::SourceStageResolved
        || source_resolved.phase_receipt_sha256()? != cleanup.digest()?
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_exit_ready_not_authorized",
        ));
    }
    receipt.validate(capsule, terminal, cleanup, worker_started)?;
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::SourceStageResolved,
        MaintenanceWorkerPhase::ExitReady,
        receipt.digest()?,
    )
}

pub(super) fn authorize_service_delete_intent_after_exit_ready(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    exit_ready: &WorkerExitReadyReceipt,
    intent: &ServiceDeleteIntentReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    let ready = records.last().ok_or(AuthorityMaintenanceError(
        "authority_worker_exit_ready_missing",
    ))?;
    if ready.phase != MaintenanceWorkerPhase::ExitReady
        || ready.phase_receipt_sha256()? != exit_ready.digest()?
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_delete_pending_receipt_invalid",
        ));
    }
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::ExitReady,
        MaintenanceWorkerPhase::ServiceDeleteIntent,
        intent.digest()?,
    )
}

pub(super) fn authorize_service_delete_pending_after_intent(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    terminal_receipt_sha256: [u8; 32],
    intent: &ServiceDeleteIntentReceipt,
    receipt: &ServiceDeletePendingReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    receipt.validate(capsule, terminal_receipt_sha256, intent)?;
    let durable_intent = records.last().ok_or(AuthorityMaintenanceError(
        "authority_worker_service_delete_intent_receipt_missing",
    ))?;
    if durable_intent.phase != MaintenanceWorkerPhase::ServiceDeleteIntent
        || durable_intent.phase_receipt_sha256()? != intent.digest()?
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_delete_pending_receipt_invalid",
        ));
    }
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::ServiceDeleteIntent,
        MaintenanceWorkerPhase::ServiceDeletePending,
        receipt.digest()?,
    )
}

pub(super) fn authorize_finalizer_handles_closed(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    exit_ready: &WorkerExitReadyReceipt,
    delete_pending: &ServiceDeletePendingReceipt,
    receipt: &FinalizerHandlesClosedReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    let pending = records.last().ok_or(AuthorityMaintenanceError(
        "authority_worker_delete_receipt_not_persisted",
    ))?;
    if pending.phase != MaintenanceWorkerPhase::ServiceDeletePending
        || pending.phase_receipt_sha256()? != delete_pending.digest()?
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_finalizer_handles_closed_receipt_invalid",
        ));
    }
    receipt.validate(capsule, exit_ready, delete_pending)?;
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::ServiceDeletePending,
        MaintenanceWorkerPhase::FinalizerHandlesClosed,
        receipt.digest()?,
    )
}

#[cfg(test)]
pub(super) fn authorize_service_delete_pending(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    terminal_receipt_sha256: [u8; 32],
    intent: &ServiceDeleteIntentReceipt,
    receipt: &ServiceDeletePendingReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    authorize_service_delete_pending_after_intent(
        capsule,
        records,
        terminal_receipt_sha256,
        intent,
        receipt,
    )
}

pub(super) fn authorize_service_absent_after_handles_closed(
    capsule: &MaintenanceWorkerCapsule,
    records: &[MaintenanceWorkerJournalRecord],
    cleanup: &WorkerStagingCleanupReceipt,
    delete_pending: &ServiceDeletePendingReceipt,
    handles_closed: &FinalizerHandlesClosedReceipt,
    absent: &ServiceAbsentReceipt,
) -> Result<MaintenanceWorkerJournalRecord, AuthorityMaintenanceError> {
    let closed = records.last().ok_or(AuthorityMaintenanceError(
        "authority_worker_finalizer_handles_closed_receipt_missing",
    ))?;
    if closed.phase != MaintenanceWorkerPhase::FinalizerHandlesClosed
        || closed.phase_receipt_sha256()? != handles_closed.digest()?
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_absence_not_authorized",
        ));
    }
    cleanup.validate_self_sealed()?;
    let cleanup_sha256 = cleanup.digest()?;
    if !records.iter().any(|record| {
        record.phase == MaintenanceWorkerPhase::SourceStageResolved
            && record.phase_receipt_sha256().ok() == Some(cleanup_sha256)
    }) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_absence_not_authorized",
        ));
    }
    absent.validate(capsule, delete_pending, cleanup)?;
    append_authorized_phase(
        capsule,
        records,
        MaintenanceWorkerPhase::FinalizerHandlesClosed,
        MaintenanceWorkerPhase::ServiceAbsent,
        absent.digest()?,
    )
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum MaintenanceWorkerRecoveryDisposition {
    ResumeSameCapsuleBeforeTransaction,
    ContainSameCapsuleBeforeTransaction,
    RecoverSameTransaction,
    ContainInterruptedTransaction,
    FinishServiceRemoval,
    Complete,
}

pub(super) fn validate_worker_journal(
    expected_capsule_sha256: [u8; 32],
    records: &[MaintenanceWorkerJournalRecord],
) -> Result<MaintenanceWorkerRecoveryDisposition, AuthorityMaintenanceError> {
    if records.is_empty() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_journal_missing",
        ));
    }
    for (index, record) in records.iter().enumerate() {
        if !record.validates_after(index.checked_sub(1).map(|prior| &records[prior]))
            || decode_hex_32(&record.capsule_sha256)? != expected_capsule_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_journal_integrity_failed",
            ));
        }
    }
    let phase = records
        .last()
        .map(|record| record.phase)
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_journal_integrity_failed",
        ))?;
    Ok(match phase {
        MaintenanceWorkerPhase::BootstrapIntent
        | MaintenanceWorkerPhase::CapsuleStaged
        | MaintenanceWorkerPhase::PipePrepared
        | MaintenanceWorkerPhase::ServiceCreated
        | MaintenanceWorkerPhase::WorkerStarted
        | MaintenanceWorkerPhase::SourceHandlesBound => {
            MaintenanceWorkerRecoveryDisposition::ResumeSameCapsuleBeforeTransaction
        }
        MaintenanceWorkerPhase::SourceStagingIntent => {
            MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
        }
        MaintenanceWorkerPhase::SourceStagingContained => {
            MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
        }
        MaintenanceWorkerPhase::WorkerInvocationClaimed => {
            MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
        }
        MaintenanceWorkerPhase::PipeRecovered => {
            MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
        }
        MaintenanceWorkerPhase::TransactionStarted
        | MaintenanceWorkerPhase::CandidateCredentialArmed => {
            MaintenanceWorkerRecoveryDisposition::RecoverSameTransaction
        }
        phase
            if phase.is_transaction_terminal()
                || phase == MaintenanceWorkerPhase::SourceStageResolved
                || phase == MaintenanceWorkerPhase::ExitReady
                || phase == MaintenanceWorkerPhase::ServiceDeleteIntent
                || phase == MaintenanceWorkerPhase::ServiceDeletePending
                || phase == MaintenanceWorkerPhase::FinalizerHandlesClosed =>
        {
            MaintenanceWorkerRecoveryDisposition::FinishServiceRemoval
        }
        MaintenanceWorkerPhase::ServiceAbsent => MaintenanceWorkerRecoveryDisposition::Complete,
        _ => {
            return Err(AuthorityMaintenanceError(
                "authority_worker_journal_integrity_failed",
            ))
        }
    })
}

pub(super) fn encode_worker_journal(
    capsule_sha256: [u8; 32],
    records: &[MaintenanceWorkerJournalRecord],
) -> Result<Vec<u8>, AuthorityMaintenanceError> {
    validate_worker_journal(capsule_sha256, records)?;
    let mut bytes = Vec::new();
    for record in records {
        let encoded = serde_json::to_vec(record)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_journal_encode_failed"))?;
        if encoded.is_empty()
            || bytes
                .len()
                .checked_add(encoded.len() + 1)
                .map_or(true, |length| length > MAX_WORKER_JOURNAL_BYTES)
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_journal_size_invalid",
            ));
        }
        bytes.extend_from_slice(&encoded);
        bytes.push(b'\n');
    }
    Ok(bytes)
}

pub(super) fn encode_worker_journal_append(
    capsule_sha256: [u8; 32],
    durable_records: &[MaintenanceWorkerJournalRecord],
    next_record: &MaintenanceWorkerJournalRecord,
) -> Result<Vec<u8>, AuthorityMaintenanceError> {
    let durable = encode_worker_journal(capsule_sha256, durable_records)?;
    let mut next = durable_records.to_vec();
    next.push(next_record.clone());
    let complete = encode_worker_journal(capsule_sha256, &next)?;
    if !complete.starts_with(&durable) || complete.len() <= durable.len() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_journal_append_invalid",
        ));
    }
    Ok(complete[durable.len()..].to_vec())
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct WorkerJournalRecovery {
    records: Vec<MaintenanceWorkerJournalRecord>,
    durable_byte_length: usize,
    torn_tail: bool,
}

impl WorkerJournalRecovery {
    pub(super) fn records(&self) -> &[MaintenanceWorkerJournalRecord] {
        &self.records
    }

    pub(super) fn durable_byte_length(&self) -> usize {
        self.durable_byte_length
    }

    pub(super) fn torn_tail(&self) -> bool {
        self.torn_tail
    }
}

fn parse_complete_worker_journal(
    bytes: &[u8],
    capsule_sha256: [u8; 32],
) -> Result<Vec<MaintenanceWorkerJournalRecord>, AuthorityMaintenanceError> {
    if bytes.is_empty()
        || bytes.len() > MAX_WORKER_JOURNAL_BYTES
        || bytes.last() != Some(&b'\n')
        || bytes.contains(&b'\r')
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_journal_encoding_invalid",
        ));
    }
    let mut records = Vec::new();
    for line in bytes[..bytes.len() - 1].split(|byte| *byte == b'\n') {
        if line.is_empty() {
            return Err(AuthorityMaintenanceError(
                "authority_worker_journal_encoding_invalid",
            ));
        }
        let record: MaintenanceWorkerJournalRecord = serde_json::from_slice(line)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_journal_encoding_invalid"))?;
        if serde_json::to_vec(&record)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_journal_encode_failed"))?
            != line
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_journal_not_canonical",
            ));
        }
        records.push(record);
    }
    validate_worker_journal(capsule_sha256, &records)?;
    if encode_worker_journal(capsule_sha256, &records)? != bytes {
        return Err(AuthorityMaintenanceError(
            "authority_worker_journal_not_canonical",
        ));
    }
    Ok(records)
}

pub(super) fn parse_worker_journal_recovery(
    bytes: &[u8],
    capsule_sha256: [u8; 32],
) -> Result<WorkerJournalRecovery, AuthorityMaintenanceError> {
    if bytes.is_empty() || bytes.len() > MAX_WORKER_JOURNAL_BYTES || bytes.contains(&b'\r') {
        return Err(AuthorityMaintenanceError(
            "authority_worker_journal_encoding_invalid",
        ));
    }
    let durable_byte_length = bytes
        .iter()
        .rposition(|value| *value == b'\n')
        .and_then(|index| index.checked_add(1))
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_journal_durable_prefix_missing",
        ))?;
    let records = parse_complete_worker_journal(&bytes[..durable_byte_length], capsule_sha256)?;
    Ok(WorkerJournalRecovery {
        records,
        durable_byte_length,
        torn_tail: durable_byte_length != bytes.len(),
    })
}

pub(super) fn parse_worker_journal(
    bytes: &[u8],
    capsule_sha256: [u8; 32],
) -> Result<Vec<MaintenanceWorkerJournalRecord>, AuthorityMaintenanceError> {
    let recovered = parse_worker_journal_recovery(bytes, capsule_sha256)?;
    if recovered.torn_tail {
        return Err(AuthorityMaintenanceError(
            "authority_worker_journal_encoding_invalid",
        ));
    }
    Ok(recovered.records)
}

#[derive(Default)]
pub(super) struct WorkerRecoveryEvidence<'a> {
    pub(super) intent: Option<&'a WorkerBootstrapIntentReceipt>,
    pub(super) bootstrap: Option<&'a WorkerBootstrapStagingReceipt>,
    pub(super) original_pipe: Option<&'a WorkerPipePreparedReceipt>,
    pub(super) pipe: Option<&'a WorkerPipePreparedReceipt>,
    pub(super) pipe_recovery: Option<&'a WorkerPipeRecoveryReceipt>,
    pub(super) service_created: Option<&'a ServiceCreatedReceipt>,
    pub(super) handoff: Option<&'a WorkerHandleHandoffReceipt>,
    pub(super) invocation_claim: Option<&'a WorkerInvocationClaimReceipt>,
    pub(super) worker_started: Option<&'a WorkerStartedReceipt>,
    pub(super) live_worker: Option<&'a WorkerLiveReadback>,
    pub(super) staging_intent: Option<&'a WorkerSourceStagingIntentReceipt>,
    pub(super) partial_staging_cleanup: Option<&'a WorkerPartialStagingCleanupReceipt>,
    pub(super) staging: Option<&'a DurableSourceStagingReceipt>,
    pub(super) nonce_consumption: Option<&'a WorkerNonceConsumptionReceipt>,
    pub(super) transaction_started: Option<&'a TransactionStartedReceipt>,
    pub(super) candidate_prepared: Option<&'a CandidateCredentialRecord>,
    pub(super) candidate_credential_armed: Option<&'a CandidateCredentialArmedReceipt>,
    pub(super) candidate_armed: Option<&'a CandidateCredentialRecord>,
    pub(super) transaction_committed: Option<&'a TransactionCommittedReceipt>,
    pub(super) transaction_contained: Option<&'a TransactionContainedReceipt>,
    pub(super) exit_ready: Option<&'a WorkerExitReadyReceipt>,
    pub(super) delete_intent: Option<&'a ServiceDeleteIntentReceipt>,
    pub(super) delete_pending: Option<&'a ServiceDeletePendingReceipt>,
    pub(super) handles_closed: Option<&'a FinalizerHandlesClosedReceipt>,
    pub(super) cleanup: Option<&'a WorkerStagingCleanupReceipt>,
    pub(super) service_absent: Option<&'a ServiceAbsentReceipt>,
}

pub(super) fn validate_worker_recovery_bundle(
    capsule: &MaintenanceWorkerCapsule,
    launch: &MaintenanceWorkerLaunchContract,
    records: &[MaintenanceWorkerJournalRecord],
    now_unix_millis: u64,
    evidence: WorkerRecoveryEvidence<'_>,
) -> Result<MaintenanceWorkerRecoveryDisposition, AuthorityMaintenanceError> {
    validate_worker_recovery_bundle_inner(
        capsule,
        launch,
        records,
        now_unix_millis,
        false,
        false,
        evidence,
    )
}

pub(super) fn validate_worker_recovery_bundle_with_containment(
    capsule: &MaintenanceWorkerCapsule,
    launch: &MaintenanceWorkerLaunchContract,
    records: &[MaintenanceWorkerJournalRecord],
    now_unix_millis: u64,
    journal_torn_tail: bool,
    evidence: WorkerRecoveryEvidence<'_>,
) -> Result<MaintenanceWorkerRecoveryDisposition, AuthorityMaintenanceError> {
    validate_worker_recovery_bundle_inner(
        capsule,
        launch,
        records,
        now_unix_millis,
        journal_torn_tail,
        true,
        evidence,
    )
}

fn validate_worker_recovery_bundle_inner(
    capsule: &MaintenanceWorkerCapsule,
    launch: &MaintenanceWorkerLaunchContract,
    records: &[MaintenanceWorkerJournalRecord],
    now_unix_millis: u64,
    journal_torn_tail: bool,
    contain_unsafe_state: bool,
    evidence: WorkerRecoveryEvidence<'_>,
) -> Result<MaintenanceWorkerRecoveryDisposition, AuthorityMaintenanceError> {
    let journal_disposition = validate_worker_journal(capsule.digest()?, records)?;
    let transaction_record = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::TransactionStarted);
    let reopened_after_service_creation = transaction_record.is_none()
        && records.last().is_some_and(|record| {
            matches!(
                record.phase,
                MaintenanceWorkerPhase::ServiceCreated
                    | MaintenanceWorkerPhase::WorkerInvocationClaimed
                    | MaintenanceWorkerPhase::WorkerStarted
                    | MaintenanceWorkerPhase::SourceStagingIntent
                    | MaintenanceWorkerPhase::SourceStagingContained
                    | MaintenanceWorkerPhase::SourceHandlesBound
            )
        });
    let consent_invalid =
        transaction_record.is_none() && capsule.validate_consent_at(now_unix_millis).is_err();
    let disposition = if contain_unsafe_state
        && transaction_record.is_none()
        && (journal_torn_tail || consent_invalid || reopened_after_service_creation)
    {
        MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
    } else if contain_unsafe_state
        && journal_torn_tail
        && journal_disposition == MaintenanceWorkerRecoveryDisposition::RecoverSameTransaction
    {
        MaintenanceWorkerRecoveryDisposition::ContainInterruptedTransaction
    } else {
        journal_disposition
    };
    if transaction_record.is_none() && !contain_unsafe_state {
        capsule.validate_consent_at(now_unix_millis)?;
    }

    let intent = evidence.intent.ok_or(AuthorityMaintenanceError(
        "authority_worker_recovery_evidence_missing",
    ))?;
    intent.validate(capsule, launch)?;
    if records
        .first()
        .and_then(|record| record.phase_receipt_sha256().ok())
        != Some(intent.digest()?)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_mismatch",
        ));
    }

    let Some(capsule_staged) = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::CapsuleStaged)
    else {
        return Ok(disposition);
    };
    let bootstrap = evidence.bootstrap.ok_or(AuthorityMaintenanceError(
        "authority_worker_recovery_evidence_missing",
    ))?;
    bootstrap.validate(capsule, launch)?;
    if capsule_staged.phase_receipt_sha256()? != bootstrap.digest()? {
        return Err(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_mismatch",
        ));
    }

    if let Some(pipe_prepared_record) = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::PipePrepared)
    {
        let original_pipe =
            evidence
                .original_pipe
                .or(evidence.pipe)
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?;
        original_pipe.validate(capsule, launch)?;
        if pipe_prepared_record.phase_receipt_sha256()? != original_pipe.digest()? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_mismatch",
            ));
        }
        if let Some(pipe_recovered_record) = records
            .iter()
            .find(|record| record.phase == MaintenanceWorkerPhase::PipeRecovered)
        {
            let recovery = evidence.pipe_recovery.ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
            recovery.validate(capsule, launch, records, original_pipe)?;
            if pipe_recovered_record.phase_receipt_sha256()? != recovery.digest()?
                || evidence.pipe != Some(recovery.replacement_pipe())
            {
                return Err(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_mismatch",
                ));
            }
        } else if evidence.pipe != Some(original_pipe) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_mismatch",
            ));
        }
    }

    let Some(service_created_record) = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::ServiceCreated)
    else {
        return Ok(disposition);
    };
    let pipe = evidence.pipe.ok_or(AuthorityMaintenanceError(
        "authority_worker_recovery_evidence_missing",
    ))?;
    let service_created = evidence.service_created.ok_or(AuthorityMaintenanceError(
        "authority_worker_recovery_evidence_missing",
    ))?;
    service_created.validate(capsule, launch, bootstrap, pipe)?;
    if service_created_record.phase_receipt_sha256()? != service_created.digest()? {
        return Err(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_mismatch",
        ));
    }

    let invocation_claim_record = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::WorkerInvocationClaimed);
    let worker_started_record = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::WorkerStarted);
    if invocation_claim_record.is_none() && worker_started_record.is_none() {
        return Ok(disposition);
    }
    let handoff = evidence.handoff.ok_or(AuthorityMaintenanceError(
        "authority_worker_recovery_evidence_missing",
    ))?;
    handoff.validate_with_pipe(capsule, launch, pipe)?;
    if let Some(claim_record) = invocation_claim_record {
        let claim = evidence.invocation_claim.ok_or(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_missing",
        ))?;
        claim.validate(
            capsule,
            launch,
            bootstrap,
            service_created,
            pipe,
            handoff,
            handoff.worker(),
        )?;
        if claim_record.phase_receipt_sha256()? != claim.digest()? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_mismatch",
            ));
        }
    }
    let Some(worker_started_record) = worker_started_record else {
        return Ok(disposition);
    };
    let worker_started = evidence.worker_started.ok_or(AuthorityMaintenanceError(
        "authority_worker_recovery_evidence_missing",
    ))?;
    worker_started.validate(capsule, bootstrap, service_created, pipe, handoff)?;
    if worker_started_record.phase_receipt_sha256()? != worker_started.digest()? {
        return Err(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_mismatch",
        ));
    }
    if transaction_record.is_none()
        && disposition != MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
    {
        evidence
            .live_worker
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_live_readback_missing",
            ))?
            .validate(worker_started)?;
    }

    if let Some(staging_intent_record) = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::SourceStagingIntent)
    {
        let staging_intent = evidence.staging_intent.ok_or(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_missing",
        ))?;
        staging_intent.validate(capsule, worker_started_record, handoff)?;
        if staging_intent_record.phase_receipt_sha256()? != staging_intent.digest()? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_mismatch",
            ));
        }
    }

    if let Some(partial_cleanup_record) = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::SourceStagingContained)
    {
        let staging_intent = evidence.staging_intent.ok_or(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_missing",
        ))?;
        let cleanup = evidence
            .partial_staging_cleanup
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        cleanup.validate(capsule, staging_intent)?;
        if partial_cleanup_record.phase_receipt_sha256()? != cleanup.digest()? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_mismatch",
            ));
        }
    }

    let Some(source_bound) = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::SourceHandlesBound)
    else {
        return Ok(disposition);
    };
    let staging = evidence.staging.ok_or(AuthorityMaintenanceError(
        "authority_worker_recovery_evidence_missing",
    ))?;
    staging.validate(capsule, worker_started_record, handoff)?;
    if source_bound.phase_receipt_sha256()? != staging.digest()? {
        return Err(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_mismatch",
        ));
    }

    let Some(transaction_record) = transaction_record else {
        return Ok(disposition);
    };
    let nonce = evidence.nonce_consumption.ok_or(AuthorityMaintenanceError(
        "authority_worker_recovery_evidence_missing",
    ))?;
    let started = evidence
        .transaction_started
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_missing",
        ))?;
    started.validate(capsule, source_bound, staging, nonce)?;
    if transaction_record.phase_receipt_sha256()? != started.digest()? {
        return Err(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_mismatch",
        ));
    }

    if let Some(candidate_armed_record) = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::CandidateCredentialArmed)
    {
        let prepared = evidence
            .candidate_prepared
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let candidate_armed =
            evidence
                .candidate_credential_armed
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?;
        candidate_armed.validate(capsule, started, worker_started, prepared)?;
        let armed = evidence.candidate_armed.ok_or(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_missing",
        ))?;
        let prepared_binding = prepared
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let armed_binding = armed
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if candidate_armed_record.phase_receipt_sha256()? != candidate_armed.digest()?
            || armed.phase() != CandidateCredentialPhase::Armed
            || prepared_binding != armed_binding
            || armed
                .credential_sha256()
                .map_err(|error| AuthorityMaintenanceError(error.code()))?
                != prepared
                    .credential_sha256()
                    .map_err(|error| AuthorityMaintenanceError(error.code()))?
            || armed
                .armed_receipt_sha256()
                .map_err(|error| AuthorityMaintenanceError(error.code()))?
                != candidate_armed.digest()?
            || armed.candidate_service() != Some(candidate_armed.candidate_service())
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_mismatch",
            ));
        }
    }

    let terminal = records
        .iter()
        .find(|record| record.phase.is_transaction_terminal());
    let Some(terminal) = terminal else {
        return Ok(disposition);
    };
    let terminal_receipt_sha256 = match terminal.phase {
        MaintenanceWorkerPhase::TransactionCommitted => {
            let receipt = evidence
                .transaction_committed
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?;
            receipt.validate(capsule, started)?;
            receipt.digest()?
        }
        MaintenanceWorkerPhase::TransactionContained => {
            let receipt = evidence
                .transaction_contained
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?;
            receipt.validate(capsule, started)?;
            receipt.digest()?
        }
        _ => {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_mismatch",
            ))
        }
    };
    if terminal.phase_receipt_sha256()? != terminal_receipt_sha256 {
        return Err(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_mismatch",
        ));
    }

    let source_resolved_record = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::SourceStageResolved);
    if let Some(source_resolved_record) = source_resolved_record {
        let cleanup = evidence.cleanup.ok_or(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_missing",
        ))?;
        cleanup.validate(capsule, staging, terminal)?;
        if source_resolved_record.phase_receipt_sha256()? != cleanup.digest()? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_mismatch",
            ));
        }
    }

    let exit_ready_record = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::ExitReady);
    if let Some(exit_ready_record) = exit_ready_record {
        let cleanup = evidence.cleanup.ok_or(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_missing",
        ))?;
        let worker_started = evidence.worker_started.ok_or(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_missing",
        ))?;
        let exit_ready = evidence.exit_ready.ok_or(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_missing",
        ))?;
        exit_ready.validate(capsule, terminal, cleanup, worker_started)?;
        if source_resolved_record.is_none()
            || exit_ready_record.phase_receipt_sha256()? != exit_ready.digest()?
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_mismatch",
            ));
        }
    }

    let delete_intent_record = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::ServiceDeleteIntent);
    let delete_intent = match delete_intent_record {
        Some(intent_record) => {
            let exit_ready = evidence.exit_ready.ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
            let intent = evidence.delete_intent.ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
            intent.validate(capsule, launch, service_created, exit_ready)?;
            if exit_ready_record.is_none()
                || intent_record.phase_receipt_sha256()? != intent.digest()?
            {
                return Err(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_mismatch",
                ));
            }
            Some(intent)
        }
        None => None,
    };

    let Some(delete_record) = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::ServiceDeletePending)
    else {
        return Ok(disposition);
    };
    let delete_pending = evidence.delete_pending.ok_or(AuthorityMaintenanceError(
        "authority_worker_recovery_evidence_missing",
    ))?;
    let delete_intent = delete_intent.ok_or(AuthorityMaintenanceError(
        "authority_worker_service_delete_intent_receipt_missing",
    ))?;
    delete_pending.validate(capsule, terminal_receipt_sha256, delete_intent)?;
    if delete_record.phase_receipt_sha256()? != delete_pending.digest()? {
        return Err(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_mismatch",
        ));
    }

    let handles_closed_record = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::FinalizerHandlesClosed);
    if let Some(handles_closed_record) = handles_closed_record {
        let exit_ready = evidence.exit_ready.ok_or(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_missing",
        ))?;
        let handles_closed = evidence.handles_closed.ok_or(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_missing",
        ))?;
        handles_closed.validate(capsule, exit_ready, delete_pending)?;
        if handles_closed_record.phase_receipt_sha256()? != handles_closed.digest()? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_mismatch",
            ));
        }
    }

    let Some(absent_record) = records
        .iter()
        .find(|record| record.phase == MaintenanceWorkerPhase::ServiceAbsent)
    else {
        return Ok(disposition);
    };
    let cleanup = evidence.cleanup.ok_or(AuthorityMaintenanceError(
        "authority_worker_recovery_evidence_missing",
    ))?;
    cleanup.validate(capsule, staging, terminal)?;
    let absent = evidence.service_absent.ok_or(AuthorityMaintenanceError(
        "authority_worker_recovery_evidence_missing",
    ))?;
    absent.validate(capsule, delete_pending, cleanup)?;
    if records
        .iter()
        .any(|record| record.phase == MaintenanceWorkerPhase::ExitReady)
        && handles_closed_record.is_none()
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_mismatch",
        ));
    }
    if absent_record.phase_receipt_sha256()? != absent.digest()? {
        return Err(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_mismatch",
        ));
    }
    Ok(disposition)
}

fn worker_journal_record_digest(
    sequence: u64,
    capsule_sha256: &[u8; 32],
    phase: MaintenanceWorkerPhase,
    previous_record_sha256: &[u8; 32],
    phase_receipt_sha256: &[u8; 32],
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(WORKER_JOURNAL_DOMAIN);
    digest.update(sequence.to_be_bytes());
    digest.update(capsule_sha256);
    digest.update([phase as u8]);
    digest.update(previous_record_sha256);
    digest.update(phase_receipt_sha256);
    digest.finalize().into()
}

fn decode_hex_16(value: &str) -> Option<[u8; 16]> {
    if value.len() != 32 {
        return None;
    }
    let mut output = [0u8; 16];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (worker_hex_nibble(pair[0])? << 4) | worker_hex_nibble(pair[1])?;
    }
    if output.iter().all(|value| *value == 0) {
        None
    } else {
        Some(output)
    }
}

fn decode_nonzero_hex_32(value: &str) -> Option<[u8; 32]> {
    let decoded = decode_hex_32(value).ok()?;
    (!decoded.iter().all(|value| *value == 0)).then_some(decoded)
}

fn worker_hex_nibble(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        _ => None,
    }
}
