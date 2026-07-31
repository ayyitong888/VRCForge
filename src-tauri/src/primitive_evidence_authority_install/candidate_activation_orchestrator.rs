//! One ordered, restart-safe candidate activation path.
//!
//! This module owns no Windows or SCM handles. Native adapters must implement
//! every operation from held handles and durable readbacks. The SYSTEM worker
//! may return only a `SealReady` proof: the exact candidate exited
//! successfully, one-use consumption is durable, and every credential or
//! tombstone writer/create handle is closed before read-only reopen. Final
//! sealing and transaction commit remain elevated-finalizer responsibilities.

use super::{
    bootstrap_activation::{
        CandidateActivationBinding, CandidateCredentialPhase, CandidateCredentialRecord,
        CandidateProcessEvidence, VerifiedCandidateValidationReceipt,
    },
    candidate_service_start_windows::CandidateExactServiceIdentityObservation,
    AuthorityMaintenanceError,
};
use sha2::{Digest, Sha256};

type Sha256Digest = [u8; 32];

const CANDIDATE_STARTED_READBACK_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-started-readback-v1\0";
const CANDIDATE_VALIDATION_EVIDENCE_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-validation-evidence-v1\0";
const CANDIDATE_PROCESS_HANDLE_CLOSURE_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-process-handle-closure-v1\0";
const CANDIDATE_WRITERS_CLOSED_DOMAIN: &[u8] = b"vrcforge-authority-candidate-writers-closed-v1\0";
const CANDIDATE_SEAL_READY_DOMAIN: &[u8] = b"vrcforge-authority-candidate-seal-ready-v1\0";
const CANDIDATE_FINALIZER_BRIDGE_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-finalizer-bridge-v1\0";
const CANDIDATE_EXACT_SERVICE_IDENTITY_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-exact-service-identity-v1\0";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct CandidateStartedReadback {
    prepared_record_sha256: Sha256Digest,
    exact_service_configuration_readback_sha256: Sha256Digest,
    exact_service_identity_sha256: Sha256Digest,
    runtime_token_receipt_sha256: Sha256Digest,
    candidate_service: CandidateProcessEvidence,
    readback_sha256: Sha256Digest,
}

impl CandidateStartedReadback {
    pub(super) fn from_exact_observation(
        prepared: &CandidateCredentialRecord,
        exact_service_identity: CandidateExactServiceIdentityObservation,
    ) -> Result<Self, AuthorityMaintenanceError> {
        require_prepared(prepared)?;
        exact_service_identity.validate()?;
        let exact_service_configuration_readback_sha256 =
            exact_service_identity.exact_service_configuration_sha256();
        let exact_service_identity_sha256 = exact_service_identity.exact_service_identity_sha256();
        let runtime_token_receipt_sha256 = exact_service_identity.runtime_token_receipt_sha256();
        let candidate_service = exact_service_identity.candidate_service();
        candidate_service
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let binding = prepared
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if [
            exact_service_configuration_readback_sha256,
            exact_service_identity_sha256,
            runtime_token_receipt_sha256,
        ]
        .iter()
        .any(is_zero_digest)
            || candidate_service.image() != binding.target_service_image()
            || exact_service_identity_sha256
                != candidate_exact_service_identity_digest(
                    exact_service_configuration_readback_sha256,
                    candidate_service,
                    runtime_token_receipt_sha256,
                )?
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_started_readback_invalid",
            ));
        }
        let prepared_record_sha256 = record_sha256(prepared)?;
        let readback_sha256 = candidate_started_readback_digest(
            &prepared_record_sha256,
            &exact_service_configuration_readback_sha256,
            &exact_service_identity_sha256,
            &runtime_token_receipt_sha256,
            &candidate_service,
        );
        Ok(Self {
            prepared_record_sha256,
            exact_service_configuration_readback_sha256,
            exact_service_identity_sha256,
            runtime_token_receipt_sha256,
            candidate_service,
            readback_sha256,
        })
    }

    #[cfg(test)]
    fn from_test_observation(
        prepared: &CandidateCredentialRecord,
        exact_service_configuration_readback_sha256: Sha256Digest,
        runtime_token_receipt_sha256: Sha256Digest,
        candidate_service: CandidateProcessEvidence,
    ) -> Result<Self, AuthorityMaintenanceError> {
        require_prepared(prepared)?;
        candidate_service
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let binding = prepared
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if [
            exact_service_configuration_readback_sha256,
            runtime_token_receipt_sha256,
        ]
        .iter()
        .any(is_zero_digest)
            || candidate_service.image() != binding.target_service_image()
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_started_readback_invalid",
            ));
        }
        let exact_service_identity_sha256 = candidate_exact_service_identity_digest(
            exact_service_configuration_readback_sha256,
            candidate_service,
            runtime_token_receipt_sha256,
        )?;
        let prepared_record_sha256 = record_sha256(prepared)?;
        let readback_sha256 = candidate_started_readback_digest(
            &prepared_record_sha256,
            &exact_service_configuration_readback_sha256,
            &exact_service_identity_sha256,
            &runtime_token_receipt_sha256,
            &candidate_service,
        );
        Ok(Self {
            prepared_record_sha256,
            exact_service_configuration_readback_sha256,
            exact_service_identity_sha256,
            runtime_token_receipt_sha256,
            candidate_service,
            readback_sha256,
        })
    }

    pub(super) fn candidate_service(&self) -> CandidateProcessEvidence {
        self.candidate_service
    }

    pub(super) fn readback_sha256(&self) -> Sha256Digest {
        self.readback_sha256
    }

    fn validate_against(
        &self,
        prepared: &CandidateCredentialRecord,
    ) -> Result<(), AuthorityMaintenanceError> {
        require_prepared(prepared)?;
        self.candidate_service
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let binding = prepared
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if self.prepared_record_sha256 != record_sha256(prepared)?
            || [
                self.exact_service_configuration_readback_sha256,
                self.exact_service_identity_sha256,
                self.runtime_token_receipt_sha256,
            ]
            .iter()
            .any(is_zero_digest)
            || self.candidate_service.image() != binding.target_service_image()
            || self.exact_service_identity_sha256
                != candidate_exact_service_identity_digest(
                    self.exact_service_configuration_readback_sha256,
                    self.candidate_service,
                    self.runtime_token_receipt_sha256,
                )?
            || self.readback_sha256
                != candidate_started_readback_digest(
                    &self.prepared_record_sha256,
                    &self.exact_service_configuration_readback_sha256,
                    &self.exact_service_identity_sha256,
                    &self.runtime_token_receipt_sha256,
                    &self.candidate_service,
                )
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_started_readback_mismatch",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct CandidateValidationEvidence {
    credential_sha256: Sha256Digest,
    armed_record_sha256: Sha256Digest,
    armed_receipt_sha256: Sha256Digest,
    candidate_service: CandidateProcessEvidence,
    handshake_receipt_sha256: Sha256Digest,
    request_sha256: Sha256Digest,
    transcript_sha256: Sha256Digest,
    consumed_record_sha256: Sha256Digest,
    consumption_receipt_sha256: Sha256Digest,
    tombstone_file_sha256: Sha256Digest,
    tombstone_file_volume_serial: u64,
    tombstone_file_id: [u8; 16],
    tombstone_file_link_count: u32,
    stopped_readback_sha256: Sha256Digest,
    evidence_sha256: Sha256Digest,
}

impl CandidateValidationEvidence {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn from_verified_observation(
        armed: &CandidateCredentialRecord,
        receipt: VerifiedCandidateValidationReceipt,
        transcript_sha256: Sha256Digest,
        consumed: &CandidateCredentialRecord,
        tombstone_file_sha256: Sha256Digest,
        tombstone_file_volume_serial: u64,
        tombstone_file_id: [u8; 16],
        tombstone_file_link_count: u32,
        stopped_readback_sha256: Sha256Digest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let credential_sha256 = credential_sha256(armed)?;
        if receipt.credential_sha256() != &credential_sha256 {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_validation_evidence_invalid",
            ));
        }
        Self::from_observed_digests(
            armed,
            *receipt.request_sha256(),
            *receipt.receipt_sha256(),
            transcript_sha256,
            consumed,
            tombstone_file_sha256,
            tombstone_file_volume_serial,
            tombstone_file_id,
            tombstone_file_link_count,
            stopped_readback_sha256,
        )
    }

    #[cfg(test)]
    #[allow(clippy::too_many_arguments)]
    fn from_test_observation(
        armed: &CandidateCredentialRecord,
        request_sha256: Sha256Digest,
        handshake_receipt_sha256: Sha256Digest,
        transcript_sha256: Sha256Digest,
        consumed: &CandidateCredentialRecord,
        tombstone_file_sha256: Sha256Digest,
        tombstone_file_volume_serial: u64,
        tombstone_file_id: [u8; 16],
        tombstone_file_link_count: u32,
        stopped_readback_sha256: Sha256Digest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        Self::from_observed_digests(
            armed,
            request_sha256,
            handshake_receipt_sha256,
            transcript_sha256,
            consumed,
            tombstone_file_sha256,
            tombstone_file_volume_serial,
            tombstone_file_id,
            tombstone_file_link_count,
            stopped_readback_sha256,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn from_observed_digests(
        armed: &CandidateCredentialRecord,
        request_sha256: Sha256Digest,
        handshake_receipt_sha256: Sha256Digest,
        transcript_sha256: Sha256Digest,
        consumed: &CandidateCredentialRecord,
        tombstone_file_sha256: Sha256Digest,
        tombstone_file_volume_serial: u64,
        tombstone_file_id: [u8; 16],
        tombstone_file_link_count: u32,
        stopped_readback_sha256: Sha256Digest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        require_armed(armed)?;
        if consumed.phase() != CandidateCredentialPhase::Consumed {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_consumed_readback_invalid",
            ));
        }
        let armed_binding = armed
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let consumed_binding = consumed
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let candidate_service = *armed.candidate_service().ok_or(AuthorityMaintenanceError(
            "authority_candidate_service_process_missing",
        ))?;
        let consumption = consumed.consumption().ok_or(AuthorityMaintenanceError(
            "authority_candidate_consumption_evidence_missing",
        ))?;
        let credential_sha256 = credential_sha256(armed)?;
        let armed_record_sha256 = record_sha256(armed)?;
        let armed_receipt_sha256 = armed
            .armed_receipt_sha256()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let consumed_record_sha256 = record_sha256(consumed)?;
        let consumed_bytes = consumed
            .canonical_bytes()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let expected_tombstone_file_sha256: Sha256Digest = Sha256::digest(&consumed_bytes).into();
        let consumption_receipt_sha256 = *consumption.receipt_sha256();
        if armed_binding != consumed_binding
            || consumed.credential_sha256().ok() != Some(credential_sha256)
            || consumed.candidate_service() != Some(&candidate_service)
            || consumption.request_sha256() != &request_sha256
            || consumption.client_peer() != armed_binding.issuer().maintenance_worker()
            || tombstone_file_sha256 != expected_tombstone_file_sha256
            || [
                armed_receipt_sha256,
                handshake_receipt_sha256,
                request_sha256,
                transcript_sha256,
                consumed_record_sha256,
                consumption_receipt_sha256,
                tombstone_file_sha256,
                stopped_readback_sha256,
            ]
            .iter()
            .any(is_zero_digest)
            || tombstone_file_volume_serial == 0
            || tombstone_file_id.iter().all(|byte| *byte == 0)
            || tombstone_file_link_count != 1
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_validation_evidence_invalid",
            ));
        }
        let evidence_sha256 = candidate_validation_evidence_digest(
            &credential_sha256,
            &armed_record_sha256,
            &armed_receipt_sha256,
            &candidate_service,
            &handshake_receipt_sha256,
            &request_sha256,
            &transcript_sha256,
            &consumed_record_sha256,
            &consumption_receipt_sha256,
            &tombstone_file_sha256,
            tombstone_file_volume_serial,
            &tombstone_file_id,
            tombstone_file_link_count,
            &stopped_readback_sha256,
        );
        Ok(Self {
            credential_sha256,
            armed_record_sha256,
            armed_receipt_sha256,
            candidate_service,
            handshake_receipt_sha256,
            request_sha256,
            transcript_sha256,
            consumed_record_sha256,
            consumption_receipt_sha256,
            tombstone_file_sha256,
            tombstone_file_volume_serial,
            tombstone_file_id,
            tombstone_file_link_count,
            stopped_readback_sha256,
            evidence_sha256,
        })
    }

    pub(super) fn evidence_sha256(&self) -> Sha256Digest {
        self.evidence_sha256
    }

    fn validate_against(
        &self,
        armed: &CandidateCredentialRecord,
        started: &CandidateStartedReadback,
    ) -> Result<(), AuthorityMaintenanceError> {
        require_armed(armed)?;
        let candidate_service = *armed.candidate_service().ok_or(AuthorityMaintenanceError(
            "authority_candidate_service_process_missing",
        ))?;
        if self.credential_sha256 != credential_sha256(armed)?
            || self.armed_record_sha256 != record_sha256(armed)?
            || self.armed_receipt_sha256
                != armed
                    .armed_receipt_sha256()
                    .map_err(|error| AuthorityMaintenanceError(error.code()))?
            || self.candidate_service != candidate_service
            || self.candidate_service != started.candidate_service
            || [
                self.handshake_receipt_sha256,
                self.request_sha256,
                self.transcript_sha256,
                self.consumed_record_sha256,
                self.consumption_receipt_sha256,
                self.tombstone_file_sha256,
                self.stopped_readback_sha256,
            ]
            .iter()
            .any(is_zero_digest)
            || self.tombstone_file_volume_serial == 0
            || self.tombstone_file_id.iter().all(|byte| *byte == 0)
            || self.tombstone_file_link_count != 1
            || self.evidence_sha256
                != candidate_validation_evidence_digest(
                    &self.credential_sha256,
                    &self.armed_record_sha256,
                    &self.armed_receipt_sha256,
                    &self.candidate_service,
                    &self.handshake_receipt_sha256,
                    &self.request_sha256,
                    &self.transcript_sha256,
                    &self.consumed_record_sha256,
                    &self.consumption_receipt_sha256,
                    &self.tombstone_file_sha256,
                    self.tombstone_file_volume_serial,
                    &self.tombstone_file_id,
                    self.tombstone_file_link_count,
                    &self.stopped_readback_sha256,
                )
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_validation_evidence_mismatch",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct CandidateProcessAndHandleClosureReadback {
    candidate_service: CandidateProcessEvidence,
    candidate_process_exited: bool,
    candidate_exit_readback_sha256: Sha256Digest,
    all_writer_handles_closed: bool,
    writer_handle_roster_readback_sha256: Sha256Digest,
    all_create_handles_closed: bool,
    create_handle_roster_readback_sha256: Sha256Digest,
    readback_sha256: Sha256Digest,
}

impl CandidateProcessAndHandleClosureReadback {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn from_exact_kernel_readback(
        candidate_service: CandidateProcessEvidence,
        candidate_process_exited: bool,
        candidate_exit_readback_sha256: Sha256Digest,
        all_writer_handles_closed: bool,
        writer_handle_roster_readback_sha256: Sha256Digest,
        all_create_handles_closed: bool,
        create_handle_roster_readback_sha256: Sha256Digest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        candidate_service
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if !candidate_process_exited
            || !all_writer_handles_closed
            || !all_create_handles_closed
            || [
                candidate_exit_readback_sha256,
                writer_handle_roster_readback_sha256,
                create_handle_roster_readback_sha256,
            ]
            .iter()
            .any(is_zero_digest)
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_process_handle_closure_invalid",
            ));
        }
        let readback_sha256 = candidate_process_handle_closure_digest(
            &candidate_service,
            &candidate_exit_readback_sha256,
            &writer_handle_roster_readback_sha256,
            &create_handle_roster_readback_sha256,
        );
        Ok(Self {
            candidate_service,
            candidate_process_exited,
            candidate_exit_readback_sha256,
            all_writer_handles_closed,
            writer_handle_roster_readback_sha256,
            all_create_handles_closed,
            create_handle_roster_readback_sha256,
            readback_sha256,
        })
    }

    fn validate_against(
        &self,
        candidate_service: &CandidateProcessEvidence,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.candidate_service
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if &self.candidate_service != candidate_service
            || !self.candidate_process_exited
            || !self.all_writer_handles_closed
            || !self.all_create_handles_closed
            || [
                self.candidate_exit_readback_sha256,
                self.writer_handle_roster_readback_sha256,
                self.create_handle_roster_readback_sha256,
            ]
            .iter()
            .any(is_zero_digest)
            || self.readback_sha256
                != candidate_process_handle_closure_digest(
                    &self.candidate_service,
                    &self.candidate_exit_readback_sha256,
                    &self.writer_handle_roster_readback_sha256,
                    &self.create_handle_roster_readback_sha256,
                )
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_process_handle_closure_mismatch",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct CandidateWritersClosedReadback {
    prepared_credential_readonly_sha256: Sha256Digest,
    armed_credential_readonly_sha256: Sha256Digest,
    consumed_tombstone_readonly_sha256: Sha256Digest,
    prepared_publishing_absence_sha256: Sha256Digest,
    armed_publishing_absence_sha256: Sha256Digest,
    tombstone_publishing_absence_sha256: Sha256Digest,
    process_and_handles: CandidateProcessAndHandleClosureReadback,
    readback_sha256: Sha256Digest,
}

impl CandidateWritersClosedReadback {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn from_exact_readonly_reopen(
        prepared_credential_readonly_sha256: Sha256Digest,
        armed_credential_readonly_sha256: Sha256Digest,
        consumed_tombstone_readonly_sha256: Sha256Digest,
        prepared_publishing_absence_sha256: Sha256Digest,
        armed_publishing_absence_sha256: Sha256Digest,
        tombstone_publishing_absence_sha256: Sha256Digest,
        process_and_handles: CandidateProcessAndHandleClosureReadback,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let inputs = [
            prepared_credential_readonly_sha256,
            armed_credential_readonly_sha256,
            consumed_tombstone_readonly_sha256,
            prepared_publishing_absence_sha256,
            armed_publishing_absence_sha256,
            tombstone_publishing_absence_sha256,
        ];
        process_and_handles.validate_against(&process_and_handles.candidate_service)?;
        if inputs.iter().any(is_zero_digest) {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_writers_closed_readback_invalid",
            ));
        }
        let readback_sha256 =
            candidate_writers_closed_digest(&inputs, &process_and_handles.readback_sha256);
        Ok(Self {
            prepared_credential_readonly_sha256,
            armed_credential_readonly_sha256,
            consumed_tombstone_readonly_sha256,
            prepared_publishing_absence_sha256,
            armed_publishing_absence_sha256,
            tombstone_publishing_absence_sha256,
            process_and_handles,
            readback_sha256,
        })
    }

    pub(super) fn readback_sha256(&self) -> Sha256Digest {
        self.readback_sha256
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        let inputs = [
            self.prepared_credential_readonly_sha256,
            self.armed_credential_readonly_sha256,
            self.consumed_tombstone_readonly_sha256,
            self.prepared_publishing_absence_sha256,
            self.armed_publishing_absence_sha256,
            self.tombstone_publishing_absence_sha256,
        ];
        if inputs.iter().any(is_zero_digest)
            || self
                .process_and_handles
                .validate_against(&self.process_and_handles.candidate_service)
                .is_err()
            || self.readback_sha256
                != candidate_writers_closed_digest(
                    &inputs,
                    &self.process_and_handles.readback_sha256,
                )
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_writers_closed_readback_mismatch",
            ));
        }
        Ok(())
    }

    fn validate_against(
        &self,
        prepared: &CandidateCredentialRecord,
        armed: &CandidateCredentialRecord,
        validation: &CandidateValidationEvidence,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.validate()?;
        self.process_and_handles
            .validate_against(&validation.candidate_service)?;
        if self.prepared_credential_readonly_sha256 != canonical_file_sha256(prepared)?
            || self.armed_credential_readonly_sha256 != canonical_file_sha256(armed)?
            || self.consumed_tombstone_readonly_sha256 != validation.tombstone_file_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_writers_closed_binding_mismatch",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct CandidateActivationSealReadyReadback {
    prepared_record_sha256: Sha256Digest,
    started: CandidateStartedReadback,
    validation: CandidateValidationEvidence,
    writers_closed: CandidateWritersClosedReadback,
    seal_ready_receipt_sha256: Sha256Digest,
    seal_ready_full_readback_sha256: Sha256Digest,
    proof_sha256: Sha256Digest,
}

impl CandidateActivationSealReadyReadback {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn from_durable_readback(
        prepared: &CandidateCredentialRecord,
        started: CandidateStartedReadback,
        validation: CandidateValidationEvidence,
        writers_closed: CandidateWritersClosedReadback,
        seal_ready_receipt_sha256: Sha256Digest,
        seal_ready_full_readback_sha256: Sha256Digest,
    ) -> Result<Self, AuthorityMaintenanceError> {
        validate_seal_ready_inputs(prepared, &started, &validation, &writers_closed)?;
        if is_zero_digest(&seal_ready_receipt_sha256)
            || is_zero_digest(&seal_ready_full_readback_sha256)
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_seal_ready_readback_invalid",
            ));
        }
        let prepared_record_sha256 = record_sha256(prepared)?;
        let proof_sha256 = candidate_seal_ready_digest(
            &prepared_record_sha256,
            &started.readback_sha256,
            &validation.evidence_sha256,
            &writers_closed.readback_sha256,
            &seal_ready_receipt_sha256,
            &seal_ready_full_readback_sha256,
        );
        Ok(Self {
            prepared_record_sha256,
            started,
            validation,
            writers_closed,
            seal_ready_receipt_sha256,
            seal_ready_full_readback_sha256,
            proof_sha256,
        })
    }

    pub(super) fn proof_sha256(&self) -> Sha256Digest {
        self.proof_sha256
    }

    fn validate_against(
        &self,
        prepared: &CandidateCredentialRecord,
    ) -> Result<(), AuthorityMaintenanceError> {
        validate_seal_ready_inputs(
            prepared,
            &self.started,
            &self.validation,
            &self.writers_closed,
        )?;
        if self.prepared_record_sha256 != record_sha256(prepared)?
            || is_zero_digest(&self.seal_ready_receipt_sha256)
            || is_zero_digest(&self.seal_ready_full_readback_sha256)
            || self.proof_sha256
                != candidate_seal_ready_digest(
                    &self.prepared_record_sha256,
                    &self.started.readback_sha256,
                    &self.validation.evidence_sha256,
                    &self.writers_closed.readback_sha256,
                    &self.seal_ready_receipt_sha256,
                    &self.seal_ready_full_readback_sha256,
                )
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_seal_ready_readback_mismatch",
            ));
        }
        Ok(())
    }
}

/// Opaque SYSTEM-worker result. It is intentionally not `Clone`; the elevated
/// finalizer must consume this proof, re-open sealed state, and own any later
/// transaction commit decision.
#[derive(Debug)]
pub(super) struct VerifiedCandidateActivationSealReady {
    readback: CandidateActivationSealReadyReadback,
}

impl VerifiedCandidateActivationSealReady {
    pub(super) fn proof_sha256(&self) -> Sha256Digest {
        self.readback.proof_sha256()
    }

    pub(super) fn into_finalizer_projection(
        self,
        prepared: &CandidateCredentialRecord,
    ) -> Result<CandidateActivationSealReadyProjection, AuthorityMaintenanceError> {
        self.readback.validate_against(prepared)?;
        CandidateActivationSealReadyProjection::from_verified(prepared, self.readback)
    }
}

/// Consumption-only bridge from the complete candidate activation state into
/// the finalizer commit protocol. Callers cannot reconstruct this projection
/// from a later process observation or a bag of digests: it is emitted only
/// from the validated Prepared/Armed/Consumed/SealReady chain above.
#[derive(Debug, PartialEq, Eq)]
pub(super) struct CandidateActivationSealReadyProjection {
    binding: CandidateActivationBinding,
    prepared_record_sha256: Sha256Digest,
    credential_sha256: Sha256Digest,
    armed_record_sha256: Sha256Digest,
    consumed_record_sha256: Sha256Digest,
    candidate_consumption_file_sha256: Sha256Digest,
    candidate_consumption_file_volume_serial: u64,
    candidate_consumption_file_id: [u8; 16],
    candidate_consumption_file_link_count: u32,
    exact_service_configuration_readback_sha256: Sha256Digest,
    exact_service_identity_sha256: Sha256Digest,
    runtime_token_receipt_sha256: Sha256Digest,
    candidate_service: CandidateProcessEvidence,
    validation_evidence_sha256: Sha256Digest,
    seal_ready_receipt_sha256: Sha256Digest,
    seal_ready_full_readback_sha256: Sha256Digest,
    seal_ready_proof_sha256: Sha256Digest,
    bridge_sha256: Sha256Digest,
}

impl CandidateActivationSealReadyProjection {
    fn from_verified(
        prepared: &CandidateCredentialRecord,
        readback: CandidateActivationSealReadyReadback,
    ) -> Result<Self, AuthorityMaintenanceError> {
        readback.validate_against(prepared)?;
        let binding = prepared
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let mut value = Self {
            binding,
            prepared_record_sha256: readback.prepared_record_sha256,
            credential_sha256: readback.validation.credential_sha256,
            armed_record_sha256: readback.validation.armed_record_sha256,
            consumed_record_sha256: readback.validation.consumed_record_sha256,
            candidate_consumption_file_sha256: readback.validation.tombstone_file_sha256,
            candidate_consumption_file_volume_serial: readback
                .validation
                .tombstone_file_volume_serial,
            candidate_consumption_file_id: readback.validation.tombstone_file_id,
            candidate_consumption_file_link_count: readback.validation.tombstone_file_link_count,
            exact_service_configuration_readback_sha256: readback
                .started
                .exact_service_configuration_readback_sha256,
            exact_service_identity_sha256: readback.started.exact_service_identity_sha256,
            runtime_token_receipt_sha256: readback.started.runtime_token_receipt_sha256,
            candidate_service: readback.started.candidate_service,
            validation_evidence_sha256: readback.validation.evidence_sha256,
            seal_ready_receipt_sha256: readback.seal_ready_receipt_sha256,
            seal_ready_full_readback_sha256: readback.seal_ready_full_readback_sha256,
            seal_ready_proof_sha256: readback.proof_sha256,
            bridge_sha256: [0; 32],
        };
        value.bridge_sha256 = candidate_finalizer_bridge_digest(&value);
        value.validate()?;
        Ok(value)
    }

    #[cfg(test)]
    pub(super) fn for_test(
        binding: CandidateActivationBinding,
        exact_service_configuration_readback_sha256: Sha256Digest,
        runtime_token_receipt_sha256: Sha256Digest,
        candidate_service: CandidateProcessEvidence,
        candidate_consumption_file_sha256: Sha256Digest,
        candidate_consumption_file_volume_serial: u64,
        candidate_consumption_file_id: [u8; 16],
        candidate_consumption_file_link_count: u32,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let exact_service_identity_sha256 = candidate_exact_service_identity_digest(
            exact_service_configuration_readback_sha256,
            candidate_service,
            runtime_token_receipt_sha256,
        )?;
        let mut value = Self {
            binding,
            prepared_record_sha256: [0x91; 32],
            credential_sha256: binding.credential_sha256(),
            armed_record_sha256: [0x92; 32],
            consumed_record_sha256: [0x93; 32],
            candidate_consumption_file_sha256,
            candidate_consumption_file_volume_serial,
            candidate_consumption_file_id,
            candidate_consumption_file_link_count,
            exact_service_configuration_readback_sha256,
            exact_service_identity_sha256,
            runtime_token_receipt_sha256,
            candidate_service,
            validation_evidence_sha256: [0x94; 32],
            seal_ready_receipt_sha256: [0x95; 32],
            seal_ready_full_readback_sha256: [0x96; 32],
            seal_ready_proof_sha256: [0x97; 32],
            bridge_sha256: [0; 32],
        };
        value.bridge_sha256 = candidate_finalizer_bridge_digest(&value);
        value.validate()?;
        Ok(value)
    }

    pub(super) fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.candidate_service
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if self.binding.credential_sha256() != self.credential_sha256
            || self.candidate_service.image() != self.binding.target_service_image()
            || [
                self.prepared_record_sha256,
                self.credential_sha256,
                self.armed_record_sha256,
                self.consumed_record_sha256,
                self.candidate_consumption_file_sha256,
                self.exact_service_configuration_readback_sha256,
                self.exact_service_identity_sha256,
                self.runtime_token_receipt_sha256,
                self.validation_evidence_sha256,
                self.seal_ready_receipt_sha256,
                self.seal_ready_full_readback_sha256,
                self.seal_ready_proof_sha256,
                self.bridge_sha256,
            ]
            .iter()
            .any(is_zero_digest)
            || self.exact_service_identity_sha256
                != candidate_exact_service_identity_digest(
                    self.exact_service_configuration_readback_sha256,
                    self.candidate_service,
                    self.runtime_token_receipt_sha256,
                )?
            || self.bridge_sha256 != candidate_finalizer_bridge_digest(self)
            || self.candidate_consumption_file_volume_serial == 0
            || self
                .candidate_consumption_file_id
                .iter()
                .all(|byte| *byte == 0)
            || self.candidate_consumption_file_link_count != 1
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_finalizer_bridge_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn binding(&self) -> CandidateActivationBinding {
        self.binding
    }

    pub(super) fn prepared_record_sha256(&self) -> Sha256Digest {
        self.prepared_record_sha256
    }

    pub(super) fn credential_sha256(&self) -> Sha256Digest {
        self.credential_sha256
    }

    pub(super) fn exact_service_configuration_readback_sha256(&self) -> Sha256Digest {
        self.exact_service_configuration_readback_sha256
    }

    pub(super) fn exact_service_identity_sha256(&self) -> Sha256Digest {
        self.exact_service_identity_sha256
    }

    pub(super) fn runtime_token_receipt_sha256(&self) -> Sha256Digest {
        self.runtime_token_receipt_sha256
    }

    pub(super) fn candidate_service(&self) -> CandidateProcessEvidence {
        self.candidate_service
    }

    pub(super) fn candidate_consumption_file_sha256(&self) -> Sha256Digest {
        self.candidate_consumption_file_sha256
    }

    pub(super) fn candidate_consumption_file_volume_serial(&self) -> u64 {
        self.candidate_consumption_file_volume_serial
    }

    pub(super) fn candidate_consumption_file_id(&self) -> [u8; 16] {
        self.candidate_consumption_file_id
    }

    pub(super) fn candidate_consumption_file_link_count(&self) -> u32 {
        self.candidate_consumption_file_link_count
    }

    pub(super) fn activation_readback_sha256(&self) -> Sha256Digest {
        self.bridge_sha256
    }
}

pub(super) trait CandidateActivationOperations {
    fn reopen_seal_ready(
        &mut self,
        prepared: &CandidateCredentialRecord,
    ) -> Result<Option<CandidateActivationSealReadyReadback>, AuthorityMaintenanceError>;

    fn persist_or_reopen_prepared(
        &mut self,
        prepared: CandidateCredentialRecord,
    ) -> Result<CandidateCredentialRecord, AuthorityMaintenanceError>;

    fn create_start_or_reopen_exact_candidate_service(
        &mut self,
        prepared: &CandidateCredentialRecord,
    ) -> Result<CandidateStartedReadback, AuthorityMaintenanceError>;

    fn arm_or_reopen(
        &mut self,
        prepared: &CandidateCredentialRecord,
        started: &CandidateStartedReadback,
    ) -> Result<CandidateCredentialRecord, AuthorityMaintenanceError>;

    fn validate_once_or_recover_consumed(
        &mut self,
        armed: &CandidateCredentialRecord,
        started: &CandidateStartedReadback,
    ) -> Result<CandidateValidationEvidence, AuthorityMaintenanceError>;

    fn close_writers_and_persist_or_reopen_seal_ready(
        &mut self,
        prepared: &CandidateCredentialRecord,
        started: &CandidateStartedReadback,
        armed: &CandidateCredentialRecord,
        validation: &CandidateValidationEvidence,
    ) -> Result<CandidateActivationSealReadyReadback, AuthorityMaintenanceError>;
}

pub(super) fn execute_candidate_activation<O>(
    prepared: CandidateCredentialRecord,
    operations: &mut O,
) -> Result<VerifiedCandidateActivationSealReady, AuthorityMaintenanceError>
where
    O: CandidateActivationOperations,
{
    require_prepared(&prepared)?;
    if let Some(readback) = operations.reopen_seal_ready(&prepared)? {
        readback.validate_against(&prepared)?;
        return Ok(VerifiedCandidateActivationSealReady { readback });
    }

    let durable_prepared = operations.persist_or_reopen_prepared(prepared.clone())?;
    if durable_prepared != prepared {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_prepared_readback_mismatch",
        ));
    }
    let started = operations.create_start_or_reopen_exact_candidate_service(&durable_prepared)?;
    started.validate_against(&durable_prepared)?;
    let armed = operations.arm_or_reopen(&durable_prepared, &started)?;
    validate_armed_transition(&durable_prepared, &started, &armed)?;
    let validation = operations.validate_once_or_recover_consumed(&armed, &started)?;
    validation.validate_against(&armed, &started)?;

    let readback = operations.close_writers_and_persist_or_reopen_seal_ready(
        &durable_prepared,
        &started,
        &armed,
        &validation,
    )?;
    readback.validate_against(&durable_prepared)?;
    if readback.started != started || readback.validation != validation {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_seal_ready_input_mismatch",
        ));
    }
    let reopened =
        operations
            .reopen_seal_ready(&durable_prepared)?
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_seal_ready_not_durable",
            ))?;
    reopened.validate_against(&durable_prepared)?;
    if reopened != readback {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_seal_ready_reopen_mismatch",
        ));
    }
    Ok(VerifiedCandidateActivationSealReady { readback: reopened })
}

fn validate_seal_ready_inputs(
    prepared: &CandidateCredentialRecord,
    started: &CandidateStartedReadback,
    validation: &CandidateValidationEvidence,
    writers_closed: &CandidateWritersClosedReadback,
) -> Result<(), AuthorityMaintenanceError> {
    require_prepared(prepared)?;
    started.validate_against(prepared)?;
    let armed = prepared
        .arm_with_receipt(validation.armed_receipt_sha256, started.candidate_service)
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    validation.validate_against(&armed, started)?;
    writers_closed.validate_against(prepared, &armed, validation)
}

fn require_prepared(prepared: &CandidateCredentialRecord) -> Result<(), AuthorityMaintenanceError> {
    if prepared.phase() != CandidateCredentialPhase::Prepared
        || prepared.credential_sha256().is_err()
        || prepared.record_sha256().is_err()
        || prepared.binding().is_err()
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_prepared_record_invalid",
        ));
    }
    Ok(())
}

fn require_armed(armed: &CandidateCredentialRecord) -> Result<(), AuthorityMaintenanceError> {
    if armed.phase() != CandidateCredentialPhase::Armed
        || armed.credential_sha256().is_err()
        || armed.record_sha256().is_err()
        || armed.armed_receipt_sha256().is_err()
        || armed.candidate_service().is_none()
        || armed.binding().is_err()
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_armed_record_invalid",
        ));
    }
    Ok(())
}

fn validate_armed_transition(
    prepared: &CandidateCredentialRecord,
    started: &CandidateStartedReadback,
    armed: &CandidateCredentialRecord,
) -> Result<(), AuthorityMaintenanceError> {
    require_prepared(prepared)?;
    require_armed(armed)?;
    if armed.binding().ok() != prepared.binding().ok()
        || armed.credential_sha256().ok() != prepared.credential_sha256().ok()
        || armed.candidate_service() != Some(&started.candidate_service)
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_armed_readback_mismatch",
        ));
    }
    Ok(())
}

fn credential_sha256(
    record: &CandidateCredentialRecord,
) -> Result<Sha256Digest, AuthorityMaintenanceError> {
    record
        .credential_sha256()
        .map_err(|error| AuthorityMaintenanceError(error.code()))
}

fn record_sha256(
    record: &CandidateCredentialRecord,
) -> Result<Sha256Digest, AuthorityMaintenanceError> {
    record
        .record_sha256()
        .map_err(|error| AuthorityMaintenanceError(error.code()))
}

fn canonical_file_sha256(
    record: &CandidateCredentialRecord,
) -> Result<Sha256Digest, AuthorityMaintenanceError> {
    let bytes = record
        .canonical_bytes()
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    Ok(Sha256::digest(bytes).into())
}

fn candidate_started_readback_digest(
    prepared_record_sha256: &Sha256Digest,
    exact_service_configuration_readback_sha256: &Sha256Digest,
    exact_service_identity_sha256: &Sha256Digest,
    runtime_token_receipt_sha256: &Sha256Digest,
    candidate_service: &CandidateProcessEvidence,
) -> Sha256Digest {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_STARTED_READBACK_DOMAIN);
    digest.update(prepared_record_sha256);
    digest.update(exact_service_configuration_readback_sha256);
    digest.update(exact_service_identity_sha256);
    digest.update(runtime_token_receipt_sha256);
    update_process_digest(&mut digest, candidate_service);
    digest.finalize().into()
}

#[allow(clippy::too_many_arguments)]
fn candidate_validation_evidence_digest(
    credential_sha256: &Sha256Digest,
    armed_record_sha256: &Sha256Digest,
    armed_receipt_sha256: &Sha256Digest,
    candidate_service: &CandidateProcessEvidence,
    handshake_receipt_sha256: &Sha256Digest,
    request_sha256: &Sha256Digest,
    transcript_sha256: &Sha256Digest,
    consumed_record_sha256: &Sha256Digest,
    consumption_receipt_sha256: &Sha256Digest,
    tombstone_file_sha256: &Sha256Digest,
    tombstone_file_volume_serial: u64,
    tombstone_file_id: &[u8; 16],
    tombstone_file_link_count: u32,
    stopped_readback_sha256: &Sha256Digest,
) -> Sha256Digest {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_VALIDATION_EVIDENCE_DOMAIN);
    digest.update(credential_sha256);
    digest.update(armed_record_sha256);
    digest.update(armed_receipt_sha256);
    update_process_digest(&mut digest, candidate_service);
    digest.update(handshake_receipt_sha256);
    digest.update(request_sha256);
    digest.update(transcript_sha256);
    digest.update(consumed_record_sha256);
    digest.update(consumption_receipt_sha256);
    digest.update(tombstone_file_sha256);
    digest.update(tombstone_file_volume_serial.to_be_bytes());
    digest.update(tombstone_file_id);
    digest.update(tombstone_file_link_count.to_be_bytes());
    digest.update(stopped_readback_sha256);
    digest.finalize().into()
}

fn candidate_process_handle_closure_digest(
    candidate_service: &CandidateProcessEvidence,
    candidate_exit_readback_sha256: &Sha256Digest,
    writer_handle_roster_readback_sha256: &Sha256Digest,
    create_handle_roster_readback_sha256: &Sha256Digest,
) -> Sha256Digest {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_PROCESS_HANDLE_CLOSURE_DOMAIN);
    update_process_digest(&mut digest, candidate_service);
    digest.update(candidate_exit_readback_sha256);
    digest.update(writer_handle_roster_readback_sha256);
    digest.update(create_handle_roster_readback_sha256);
    digest.update([1, 1, 1]);
    digest.finalize().into()
}

fn candidate_writers_closed_digest(
    inputs: &[Sha256Digest; 6],
    process_handle_closure_sha256: &Sha256Digest,
) -> Sha256Digest {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_WRITERS_CLOSED_DOMAIN);
    for value in inputs {
        digest.update(value);
    }
    digest.update(process_handle_closure_sha256);
    digest.finalize().into()
}

fn candidate_seal_ready_digest(
    prepared_record_sha256: &Sha256Digest,
    started_readback_sha256: &Sha256Digest,
    validation_sha256: &Sha256Digest,
    writers_closed_sha256: &Sha256Digest,
    seal_ready_receipt_sha256: &Sha256Digest,
    seal_ready_full_readback_sha256: &Sha256Digest,
) -> Sha256Digest {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_SEAL_READY_DOMAIN);
    digest.update(prepared_record_sha256);
    digest.update(started_readback_sha256);
    digest.update(validation_sha256);
    digest.update(writers_closed_sha256);
    digest.update(seal_ready_receipt_sha256);
    digest.update(seal_ready_full_readback_sha256);
    digest.finalize().into()
}

fn candidate_finalizer_bridge_digest(
    projection: &CandidateActivationSealReadyProjection,
) -> Sha256Digest {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_FINALIZER_BRIDGE_DOMAIN);
    digest.update(projection.binding.credential_sha256());
    digest.update(projection.prepared_record_sha256);
    digest.update(projection.armed_record_sha256);
    digest.update(projection.consumed_record_sha256);
    digest.update(projection.candidate_consumption_file_sha256);
    digest.update(
        projection
            .candidate_consumption_file_volume_serial
            .to_be_bytes(),
    );
    digest.update(projection.candidate_consumption_file_id);
    digest.update(
        projection
            .candidate_consumption_file_link_count
            .to_be_bytes(),
    );
    digest.update(projection.exact_service_configuration_readback_sha256);
    digest.update(projection.exact_service_identity_sha256);
    digest.update(projection.runtime_token_receipt_sha256);
    update_process_digest(&mut digest, &projection.candidate_service);
    digest.update(projection.validation_evidence_sha256);
    digest.update(projection.seal_ready_receipt_sha256);
    digest.update(projection.seal_ready_full_readback_sha256);
    digest.update(projection.seal_ready_proof_sha256);
    digest.finalize().into()
}

fn update_process_digest(digest: &mut Sha256, process: &CandidateProcessEvidence) {
    digest.update(process.process_id().to_be_bytes());
    digest.update(process.process_creation_time().to_be_bytes());
    digest.update(process.full_readback_receipt_sha256());
}

pub(super) fn candidate_exact_service_identity_digest(
    exact_service_configuration_sha256: Sha256Digest,
    candidate: CandidateProcessEvidence,
    runtime_token_receipt_sha256: Sha256Digest,
) -> Result<Sha256Digest, AuthorityMaintenanceError> {
    candidate
        .validate()
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    if is_zero_digest(&exact_service_configuration_sha256)
        || is_zero_digest(&runtime_token_receipt_sha256)
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_exact_service_identity_invalid",
        ));
    }
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_EXACT_SERVICE_IDENTITY_DOMAIN);
    digest.update(exact_service_configuration_sha256);
    digest.update(candidate.process_id().to_be_bytes());
    digest.update(candidate.process_creation_time().to_be_bytes());
    digest.update(candidate.image_sha256());
    digest.update(candidate.image_byte_length().to_be_bytes());
    digest.update(candidate.image_volume_serial().to_be_bytes());
    digest.update(candidate.image_file_id());
    digest.update(candidate.image_link_count().to_be_bytes());
    digest.update(candidate.image_attributes().to_be_bytes());
    digest.update(candidate.full_readback_receipt_sha256());
    digest.update(runtime_token_receipt_sha256);
    Ok(digest.finalize().into())
}

fn is_zero_digest(value: &Sha256Digest) -> bool {
    value.iter().all(|byte| *byte == 0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::primitive_evidence_authority_install::bootstrap_activation::{
        CandidateActivationBinding, CandidateActivationObservation, CandidateValidationRequest,
    };

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    enum Event {
        ReopenSealReady,
        Prepare,
        CreateStart,
        Arm,
        Validate,
        CloseWritersSealReady,
    }

    #[derive(Clone)]
    enum DurableState {
        Empty,
        Prepared {
            prepared: CandidateCredentialRecord,
        },
        Started {
            prepared: CandidateCredentialRecord,
            started: CandidateStartedReadback,
        },
        Armed {
            prepared: CandidateCredentialRecord,
            started: CandidateStartedReadback,
            armed: CandidateCredentialRecord,
        },
        Consumed {
            prepared: CandidateCredentialRecord,
            started: CandidateStartedReadback,
            armed: CandidateCredentialRecord,
            validation: CandidateValidationEvidence,
        },
        SealReady {
            readback: CandidateActivationSealReadyReadback,
        },
    }

    struct FakeOperations {
        state: DurableState,
        events: Vec<Event>,
        fail_at: Option<Event>,
        fail_reopen_occurrence: Option<usize>,
        reopen_count: usize,
        mutations: usize,
    }

    impl FakeOperations {
        fn new(state: DurableState) -> Self {
            Self {
                state,
                events: Vec::new(),
                fail_at: None,
                fail_reopen_occurrence: None,
                reopen_count: 0,
                mutations: 0,
            }
        }

        fn enter(&mut self, event: Event) -> Result<(), AuthorityMaintenanceError> {
            self.events.push(event);
            if event == Event::ReopenSealReady {
                self.reopen_count += 1;
                if self.fail_reopen_occurrence == Some(self.reopen_count) {
                    return Err(AuthorityMaintenanceError(
                        "authority_candidate_test_injected_failure",
                    ));
                }
            }
            if self.fail_at == Some(event) {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_test_injected_failure",
                ));
            }
            Ok(())
        }
    }

    impl CandidateActivationOperations for FakeOperations {
        fn reopen_seal_ready(
            &mut self,
            _prepared: &CandidateCredentialRecord,
        ) -> Result<Option<CandidateActivationSealReadyReadback>, AuthorityMaintenanceError>
        {
            self.enter(Event::ReopenSealReady)?;
            Ok(match &self.state {
                DurableState::SealReady { readback } => Some(readback.clone()),
                _ => None,
            })
        }

        fn persist_or_reopen_prepared(
            &mut self,
            prepared: CandidateCredentialRecord,
        ) -> Result<CandidateCredentialRecord, AuthorityMaintenanceError> {
            self.enter(Event::Prepare)?;
            match &self.state {
                DurableState::Empty => {
                    self.mutations += 1;
                    self.state = DurableState::Prepared {
                        prepared: prepared.clone(),
                    };
                    Ok(prepared)
                }
                DurableState::Prepared { prepared: existing }
                | DurableState::Started {
                    prepared: existing, ..
                }
                | DurableState::Armed {
                    prepared: existing, ..
                }
                | DurableState::Consumed {
                    prepared: existing, ..
                } if *existing == prepared => Ok(existing.clone()),
                _ => Err(AuthorityMaintenanceError(
                    "authority_candidate_test_prepared_conflict",
                )),
            }
        }

        fn create_start_or_reopen_exact_candidate_service(
            &mut self,
            prepared: &CandidateCredentialRecord,
        ) -> Result<CandidateStartedReadback, AuthorityMaintenanceError> {
            self.enter(Event::CreateStart)?;
            match &self.state {
                DurableState::Prepared { prepared: existing } if existing == prepared => {
                    let started = started(prepared);
                    self.mutations += 1;
                    self.state = DurableState::Started {
                        prepared: prepared.clone(),
                        started,
                    };
                    Ok(started)
                }
                DurableState::Started {
                    prepared: existing,
                    started,
                }
                | DurableState::Armed {
                    prepared: existing,
                    started,
                    ..
                }
                | DurableState::Consumed {
                    prepared: existing,
                    started,
                    ..
                } if existing == prepared => Ok(*started),
                _ => Err(AuthorityMaintenanceError(
                    "authority_candidate_test_start_conflict",
                )),
            }
        }

        fn arm_or_reopen(
            &mut self,
            prepared: &CandidateCredentialRecord,
            started: &CandidateStartedReadback,
        ) -> Result<CandidateCredentialRecord, AuthorityMaintenanceError> {
            self.enter(Event::Arm)?;
            match &self.state {
                DurableState::Started {
                    prepared: existing,
                    started: existing_started,
                } if existing == prepared && existing_started == started => {
                    let armed = armed(prepared, started.candidate_service);
                    self.mutations += 1;
                    self.state = DurableState::Armed {
                        prepared: prepared.clone(),
                        started: *started,
                        armed: armed.clone(),
                    };
                    Ok(armed)
                }
                DurableState::Armed {
                    prepared: existing,
                    started: existing_started,
                    armed,
                }
                | DurableState::Consumed {
                    prepared: existing,
                    started: existing_started,
                    armed,
                    ..
                } if existing == prepared && existing_started == started => Ok(armed.clone()),
                _ => Err(AuthorityMaintenanceError(
                    "authority_candidate_test_arm_conflict",
                )),
            }
        }

        fn validate_once_or_recover_consumed(
            &mut self,
            armed: &CandidateCredentialRecord,
            started: &CandidateStartedReadback,
        ) -> Result<CandidateValidationEvidence, AuthorityMaintenanceError> {
            self.enter(Event::Validate)?;
            match &self.state {
                DurableState::Armed {
                    prepared,
                    started: existing_started,
                    armed: existing,
                } if existing == armed && existing_started == started => {
                    let validation = validation(armed);
                    self.mutations += 1;
                    self.state = DurableState::Consumed {
                        prepared: prepared.clone(),
                        started: *started,
                        armed: armed.clone(),
                        validation: validation.clone(),
                    };
                    Ok(validation)
                }
                DurableState::Consumed {
                    started: existing_started,
                    armed: existing,
                    validation,
                    ..
                } if existing == armed && existing_started == started => Ok(validation.clone()),
                _ => Err(AuthorityMaintenanceError(
                    "authority_candidate_test_validation_conflict",
                )),
            }
        }

        fn close_writers_and_persist_or_reopen_seal_ready(
            &mut self,
            prepared: &CandidateCredentialRecord,
            started: &CandidateStartedReadback,
            armed: &CandidateCredentialRecord,
            validation: &CandidateValidationEvidence,
        ) -> Result<CandidateActivationSealReadyReadback, AuthorityMaintenanceError> {
            self.enter(Event::CloseWritersSealReady)?;
            match &self.state {
                DurableState::Consumed {
                    prepared: existing_prepared,
                    started: existing_started,
                    armed: existing_armed,
                    validation: existing_validation,
                } if existing_prepared == prepared
                    && existing_started == started
                    && existing_armed == armed
                    && existing_validation == validation =>
                {
                    let readback = seal_ready(prepared, *started, validation.clone());
                    self.mutations += 1;
                    self.state = DurableState::SealReady {
                        readback: readback.clone(),
                    };
                    Ok(readback)
                }
                DurableState::SealReady { readback } => Ok(readback.clone()),
                _ => Err(AuthorityMaintenanceError(
                    "authority_candidate_test_seal_ready_conflict",
                )),
            }
        }
    }

    fn process(process_id: u32, creation_time: u64, image: u8) -> CandidateProcessEvidence {
        CandidateProcessEvidence::from_held_process(
            process_id,
            creation_time,
            [image; 32],
            4096,
            31,
            [image.wrapping_add(1); 16],
            1,
            0x20,
        )
        .unwrap()
    }

    fn prepared() -> CandidateCredentialRecord {
        let observation = CandidateActivationObservation::new(
            [0x11; 32], [0x12; 32], [0x13; 32], 7, [0x14; 32], [0x15; 32], [0x16; 32], [0x17; 32],
            [0x41; 32], 919, 42_424,
        )
        .unwrap();
        let binding =
            CandidateActivationBinding::new(observation, [0x19; 32], 10_000, 20_000).unwrap();
        CandidateCredentialRecord::prepared(binding).unwrap()
    }

    fn started(prepared: &CandidateCredentialRecord) -> CandidateStartedReadback {
        let binding = prepared.binding().unwrap();
        let candidate = CandidateProcessEvidence::from_static_image(
            1771,
            88_181,
            *binding.target_service_image(),
        )
        .unwrap();
        CandidateStartedReadback::from_test_observation(prepared, [0x71; 32], [0x73; 32], candidate)
            .unwrap()
    }

    fn armed(
        prepared: &CandidateCredentialRecord,
        candidate: CandidateProcessEvidence,
    ) -> CandidateCredentialRecord {
        prepared.arm_with_receipt([0x72; 32], candidate).unwrap()
    }

    fn validation(armed: &CandidateCredentialRecord) -> CandidateValidationEvidence {
        let binding = armed.binding().unwrap();
        let request =
            CandidateValidationRequest::new(binding.credential_sha256(), *binding.nonce()).unwrap();
        let consumed = armed
            .consume_with_peer(&request, *binding.issuer().maintenance_worker())
            .unwrap();
        let tombstone_file_sha256: Sha256Digest =
            Sha256::digest(consumed.canonical_bytes().unwrap()).into();
        CandidateValidationEvidence::from_test_observation(
            armed,
            *consumed.consumption().unwrap().request_sha256(),
            [0x70; 32],
            [0x73; 32],
            &consumed,
            tombstone_file_sha256,
            75,
            [0x76; 16],
            1,
            [0x77; 32],
        )
        .unwrap()
    }

    fn writers_closed(
        prepared: &CandidateCredentialRecord,
        armed: &CandidateCredentialRecord,
        validation: &CandidateValidationEvidence,
    ) -> CandidateWritersClosedReadback {
        let process_and_handles =
            CandidateProcessAndHandleClosureReadback::from_exact_kernel_readback(
                validation.candidate_service,
                true,
                [0x78; 32],
                true,
                [0x79; 32],
                true,
                [0x7a; 32],
            )
            .unwrap();
        CandidateWritersClosedReadback::from_exact_readonly_reopen(
            canonical_file_sha256(prepared).unwrap(),
            canonical_file_sha256(armed).unwrap(),
            validation.tombstone_file_sha256,
            [0x84; 32],
            [0x85; 32],
            [0x86; 32],
            process_and_handles,
        )
        .unwrap()
    }

    fn seal_ready(
        prepared: &CandidateCredentialRecord,
        started: CandidateStartedReadback,
        validation: CandidateValidationEvidence,
    ) -> CandidateActivationSealReadyReadback {
        let armed = prepared
            .arm_with_receipt(validation.armed_receipt_sha256, started.candidate_service)
            .unwrap();
        CandidateActivationSealReadyReadback::from_durable_readback(
            prepared,
            started,
            validation.clone(),
            writers_closed(prepared, &armed, &validation),
            [0x87; 32],
            [0x88; 32],
        )
        .unwrap()
    }

    fn state_at(event: Event) -> DurableState {
        let prepared = prepared();
        let started = started(&prepared);
        let armed = armed(&prepared, started.candidate_service);
        let validation = validation(&armed);
        match event {
            Event::Prepare => DurableState::Prepared { prepared },
            Event::CreateStart => DurableState::Started { prepared, started },
            Event::Arm => DurableState::Armed {
                prepared,
                started,
                armed,
            },
            Event::Validate | Event::CloseWritersSealReady | Event::ReopenSealReady => {
                DurableState::Consumed {
                    prepared,
                    started,
                    armed,
                    validation,
                }
            }
        }
    }

    #[test]
    fn exact_order_returns_only_after_writer_close_and_seal_ready_reopen() {
        let prepared = prepared();
        let mut operations = FakeOperations::new(DurableState::Empty);
        let proof = execute_candidate_activation(prepared.clone(), &mut operations).unwrap();
        assert_ne!(proof.proof_sha256(), [0; 32]);
        let projection = proof.into_finalizer_projection(&prepared).unwrap();
        assert_eq!(projection.binding(), prepared.binding().unwrap());
        assert_eq!(
            projection.exact_service_configuration_readback_sha256(),
            [0x71; 32]
        );
        assert_eq!(
            projection.exact_service_identity_sha256(),
            candidate_exact_service_identity_digest(
                [0x71; 32],
                projection.candidate_service(),
                [0x73; 32],
            )
            .unwrap()
        );
        assert_eq!(projection.runtime_token_receipt_sha256(), [0x73; 32]);
        assert_eq!(projection.candidate_service().process_id(), 1771);
        assert_eq!(
            projection.candidate_consumption_file_sha256(),
            validation(&armed(&prepared, started(&prepared).candidate_service))
                .tombstone_file_sha256
        );
        assert_ne!(projection.activation_readback_sha256(), [0; 32]);
        assert_eq!(
            operations.events,
            [
                Event::ReopenSealReady,
                Event::Prepare,
                Event::CreateStart,
                Event::Arm,
                Event::Validate,
                Event::CloseWritersSealReady,
                Event::ReopenSealReady,
            ]
        );
        assert_eq!(operations.mutations, 5);
    }

    #[test]
    fn every_failure_boundary_stops_before_the_next_operation() {
        for failure in [
            Event::ReopenSealReady,
            Event::Prepare,
            Event::CreateStart,
            Event::Arm,
            Event::Validate,
            Event::CloseWritersSealReady,
        ] {
            let mut operations = FakeOperations::new(DurableState::Empty);
            operations.fail_at = Some(failure);
            assert_eq!(
                execute_candidate_activation(prepared(), &mut operations)
                    .unwrap_err()
                    .code(),
                "authority_candidate_test_injected_failure"
            );
            assert_eq!(operations.events.last(), Some(&failure));
        }

        let mut final_reopen = FakeOperations::new(DurableState::Empty);
        final_reopen.fail_reopen_occurrence = Some(2);
        assert_eq!(
            execute_candidate_activation(prepared(), &mut final_reopen)
                .unwrap_err()
                .code(),
            "authority_candidate_test_injected_failure"
        );
        assert_eq!(final_reopen.events.last(), Some(&Event::ReopenSealReady));
    }

    #[test]
    fn restart_reopens_each_durable_prefix_without_repeating_mutations() {
        for (state, expected_mutations) in [
            (state_at(Event::Prepare), 4),
            (state_at(Event::CreateStart), 3),
            (state_at(Event::Arm), 2),
            (state_at(Event::Validate), 1),
        ] {
            let prepared = match &state {
                DurableState::Prepared { prepared }
                | DurableState::Started { prepared, .. }
                | DurableState::Armed { prepared, .. }
                | DurableState::Consumed { prepared, .. } => prepared.clone(),
                _ => unreachable!(),
            };
            let mut operations = FakeOperations::new(state);
            execute_candidate_activation(prepared, &mut operations).unwrap();
            assert_eq!(operations.mutations, expected_mutations);
        }
    }

    #[test]
    fn repeated_seal_ready_call_is_read_only_and_returns_same_proof() {
        let prepared = prepared();
        let mut first = FakeOperations::new(DurableState::Empty);
        let expected = execute_candidate_activation(prepared.clone(), &mut first)
            .unwrap()
            .proof_sha256();
        let readback = match first.state {
            DurableState::SealReady { readback } => readback,
            _ => panic!("seal-ready readback missing"),
        };
        let mut repeated = FakeOperations::new(DurableState::SealReady { readback });
        let actual = execute_candidate_activation(prepared, &mut repeated)
            .unwrap()
            .proof_sha256();
        assert_eq!(actual, expected);
        assert_eq!(repeated.events, [Event::ReopenSealReady]);
        assert_eq!(repeated.mutations, 0);
    }

    #[test]
    fn writer_close_requires_exit_all_handle_closure_and_readonly_evidence() {
        let prepared = prepared();
        let started = started(&prepared);
        for flags in [
            [false, true, true],
            [true, false, true],
            [true, true, false],
        ] {
            assert_eq!(
                CandidateProcessAndHandleClosureReadback::from_exact_kernel_readback(
                    started.candidate_service,
                    flags[0],
                    [0x78; 32],
                    flags[1],
                    [0x79; 32],
                    flags[2],
                    [0x7a; 32],
                )
                .unwrap_err()
                .code(),
                "authority_candidate_process_handle_closure_invalid"
            );
        }
        let process_and_handles =
            CandidateProcessAndHandleClosureReadback::from_exact_kernel_readback(
                started.candidate_service,
                true,
                [0x78; 32],
                true,
                [0x79; 32],
                true,
                [0x7a; 32],
            )
            .unwrap();
        assert_eq!(
            CandidateWritersClosedReadback::from_exact_readonly_reopen(
                [0; 32],
                [0x82; 32],
                [0x83; 32],
                [0x84; 32],
                [0x85; 32],
                [0x86; 32],
                process_and_handles,
            )
            .unwrap_err()
            .code(),
            "authority_candidate_writers_closed_readback_invalid"
        );
    }

    #[test]
    fn validation_requires_the_exact_consumed_tombstone_bytes_hash() {
        let prepared = prepared();
        let started = started(&prepared);
        let armed = armed(&prepared, started.candidate_service);
        let binding = armed.binding().unwrap();
        let request =
            CandidateValidationRequest::new(binding.credential_sha256(), *binding.nonce()).unwrap();
        let consumed = armed
            .consume_with_peer(&request, *binding.issuer().maintenance_worker())
            .unwrap();

        assert_eq!(
            CandidateValidationEvidence::from_test_observation(
                &armed,
                *consumed.consumption().unwrap().request_sha256(),
                [0x70; 32],
                [0x73; 32],
                &consumed,
                [0x74; 32],
                75,
                [0x76; 16],
                1,
                [0x77; 32],
            )
            .unwrap_err()
            .code(),
            "authority_candidate_validation_evidence_invalid"
        );
    }

    #[test]
    fn seal_ready_rejects_readonly_reopens_not_bound_to_exact_records() {
        let prepared = prepared();
        let started = started(&prepared);
        let armed = armed(&prepared, started.candidate_service);
        let validation = validation(&armed);
        let exact = [
            canonical_file_sha256(&prepared).unwrap(),
            canonical_file_sha256(&armed).unwrap(),
            validation.tombstone_file_sha256,
        ];

        for index in 0..exact.len() {
            let mut drift = exact;
            drift[index][0] ^= 1;
            let process_and_handles =
                CandidateProcessAndHandleClosureReadback::from_exact_kernel_readback(
                    started.candidate_service,
                    true,
                    [0x78; 32],
                    true,
                    [0x79; 32],
                    true,
                    [0x7a; 32],
                )
                .unwrap();
            let writers = CandidateWritersClosedReadback::from_exact_readonly_reopen(
                drift[0],
                drift[1],
                drift[2],
                [0x84; 32],
                [0x85; 32],
                [0x86; 32],
                process_and_handles,
            )
            .unwrap();
            assert_eq!(
                CandidateActivationSealReadyReadback::from_durable_readback(
                    &prepared,
                    started,
                    validation.clone(),
                    writers,
                    [0x87; 32],
                    [0x88; 32],
                )
                .unwrap_err()
                .code(),
                "authority_candidate_writers_closed_binding_mismatch"
            );
        }
    }

    #[test]
    fn conflicting_prepared_or_tampered_seal_ready_fails_closed() {
        let expected = prepared();
        let other = CandidateCredentialRecord::prepared(
            CandidateActivationBinding::new(
                CandidateActivationObservation::new(
                    [0x21; 32], [0x22; 32], [0x23; 32], 8, [0x24; 32], [0x25; 32], [0x26; 32],
                    [0x27; 32], [0x51; 32], 929, 52_525,
                )
                .unwrap(),
                [0x29; 32],
                11_000,
                21_000,
            )
            .unwrap(),
        )
        .unwrap();
        let mut conflict = FakeOperations::new(DurableState::Prepared { prepared: other });
        assert_eq!(
            execute_candidate_activation(expected.clone(), &mut conflict)
                .unwrap_err()
                .code(),
            "authority_candidate_test_prepared_conflict"
        );

        let mut complete = FakeOperations::new(DurableState::Empty);
        execute_candidate_activation(expected.clone(), &mut complete).unwrap();
        let mut readback = match complete.state {
            DurableState::SealReady { readback } => readback,
            _ => unreachable!(),
        };
        readback.proof_sha256[0] ^= 1;
        let mut tampered = FakeOperations::new(DurableState::SealReady { readback });
        assert_eq!(
            execute_candidate_activation(expected, &mut tampered)
                .unwrap_err()
                .code(),
            "authority_candidate_seal_ready_readback_mismatch"
        );
    }
}
