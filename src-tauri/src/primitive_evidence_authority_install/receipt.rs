use super::*;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct RawHeldPayloadObservation {
    pub(super) descriptor: AuthorityPayloadDigest,
    pub(super) volume_serial: u64,
    pub(super) file_id: [u8; 16],
    pub(super) post_read_descriptor: AuthorityPayloadDigest,
    pub(super) post_read_volume_serial: u64,
    pub(super) post_read_file_id: [u8; 16],
    pub(super) handle_identity: u64,
    pub(super) regular_file: bool,
    pub(super) reparse_point: bool,
    pub(super) handle_held: bool,
    pub(super) write_sharing_denied: bool,
    pub(super) delete_sharing_denied: bool,
    pub(super) open_policy_receipt_sha256: [u8; 32],
    pub(super) full_readback_receipt_sha256: [u8; 32],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct VerifiedPayloadHandle {
    pub(super) descriptor: AuthorityPayloadDigest,
    pub(super) volume_serial: u64,
    pub(super) file_id: [u8; 16],
    pub(super) handle_identity: u64,
    pub(super) open_policy_receipt_sha256: [u8; 32],
    pub(super) full_readback_receipt_sha256: [u8; 32],
}

impl VerifiedPayloadHandle {
    pub(super) fn from_observation(
        expected: AuthorityPayloadDigest,
        observed: RawHeldPayloadObservation,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if observed.descriptor != expected
            || observed.post_read_descriptor != expected
            || observed.volume_serial == 0
            || observed.file_id.iter().all(|value| *value == 0)
            || observed.post_read_volume_serial != observed.volume_serial
            || observed.post_read_file_id != observed.file_id
            || observed.handle_identity == 0
            || !observed.regular_file
            || observed.reparse_point
            || !observed.handle_held
            || !observed.write_sharing_denied
            || !observed.delete_sharing_denied
            || observed
                .open_policy_receipt_sha256
                .iter()
                .all(|value| *value == 0)
            || observed
                .full_readback_receipt_sha256
                .iter()
                .all(|value| *value == 0)
        {
            return Err(AuthorityMaintenanceError(
                "authority_payload_handle_not_verified",
            ));
        }
        Ok(Self {
            descriptor: observed.descriptor,
            volume_serial: observed.volume_serial,
            file_id: observed.file_id,
            handle_identity: observed.handle_identity,
            open_policy_receipt_sha256: observed.open_policy_receipt_sha256,
            full_readback_receipt_sha256: observed.full_readback_receipt_sha256,
        })
    }
}

/// A sealed set of already-open source files. The preview-only helper has no
/// constructor for this type, so path strings and preview digests can never be
/// promoted into an executable maintenance capability.
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct VerifiedPayloadSet {
    pub(super) service: VerifiedPayloadHandle,
    pub(super) controller: VerifiedPayloadHandle,
    pub(super) install_helper: VerifiedPayloadHandle,
    pub(super) binding_sha256: [u8; 32],
}

#[cfg(all(windows, not(test)))]
pub(super) struct NativeHeldPayloadLease {
    pub(super) _service: std::os::windows::io::OwnedHandle,
    pub(super) _controller: std::os::windows::io::OwnedHandle,
    pub(super) _install_helper: std::os::windows::io::OwnedHandle,
    pub(super) _bootstrap_process: std::os::windows::io::OwnedHandle,
    pub(super) _bootstrap_running_image: std::os::windows::io::OwnedHandle,
}

#[cfg(test)]
pub(super) struct TestHeldPayloadLease {
    pub(super) source_handles_live: bool,
    pub(super) bootstrap_process_handle_live: bool,
    pub(super) bootstrap_image_handle_live: bool,
}

pub(super) enum HeldPayloadLease {
    #[cfg(all(windows, not(test)))]
    Native(NativeHeldPayloadLease),
    #[cfg(test)]
    Test(TestHeldPayloadLease),
    #[cfg(all(not(windows), not(test)))]
    Unsupported,
}

/// Owns the source-file handles for the whole transaction. It is intentionally
/// non-Clone; dropping it is the only release path. Numeric identities remain
/// receipts, while the child backend must place its RAII handles in
/// `NativeHeldPayloadLease` before native mutation can ever be enabled.
pub(crate) struct VerifiedMaintenanceLease {
    pub(super) payloads: VerifiedPayloadSet,
    pub(super) bootstrap_helper: VerifiedBootstrapHelperIdentity,
    pub(super) held_payloads: HeldPayloadLease,
    pub(super) plan_sha256: [u8; 32],
    pub(super) generation: [u8; 32],
}

impl VerifiedMaintenanceLease {
    pub(super) fn is_live(&self) -> bool {
        match &self.held_payloads {
            #[cfg(all(windows, not(test)))]
            HeldPayloadLease::Native(_) => true,
            #[cfg(test)]
            HeldPayloadLease::Test(value) => {
                value.source_handles_live
                    && value.bootstrap_process_handle_live
                    && value.bootstrap_image_handle_live
            }
            #[cfg(all(not(windows), not(test)))]
            HeldPayloadLease::Unsupported => false,
        }
    }

    #[cfg(test)]
    pub(super) fn for_test(
        preview: &AuthorityMaintenancePreview,
        expected: &AuthorityInstallContent,
        bootstrap_helper: VerifiedBootstrapHelperIdentity,
        service: RawHeldPayloadObservation,
        controller: RawHeldPayloadObservation,
        install_helper: RawHeldPayloadObservation,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let payloads = VerifiedPayloadSet::from_held_observations(
            expected,
            service,
            controller,
            install_helper,
        )?;
        if bootstrap_helper.image != expected.install_helper {
            return Err(AuthorityMaintenanceError(
                "authority_maintenance_bootstrap_binding_mismatch",
            ));
        }
        Ok(Self {
            payloads,
            bootstrap_helper,
            held_payloads: HeldPayloadLease::Test(TestHeldPayloadLease {
                source_handles_live: true,
                bootstrap_process_handle_live: true,
                bootstrap_image_handle_live: true,
            }),
            plan_sha256: preview.plan_sha256()?,
            generation: preview.generation_sha256()?,
        })
    }
}

impl VerifiedPayloadSet {
    pub(super) fn from_held_observations(
        expected: &AuthorityInstallContent,
        service: RawHeldPayloadObservation,
        controller: RawHeldPayloadObservation,
        install_helper: RawHeldPayloadObservation,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let service = VerifiedPayloadHandle::from_observation(expected.service, service)?;
        let controller = VerifiedPayloadHandle::from_observation(expected.controller, controller)?;
        let install_helper =
            VerifiedPayloadHandle::from_observation(expected.install_helper, install_helper)?;
        let identities = [
            (service.volume_serial, service.file_id),
            (controller.volume_serial, controller.file_id),
            (install_helper.volume_serial, install_helper.file_id),
        ];
        if identities[0] == identities[1]
            || identities[0] == identities[2]
            || identities[1] == identities[2]
            || service.handle_identity == controller.handle_identity
            || service.handle_identity == install_helper.handle_identity
            || controller.handle_identity == install_helper.handle_identity
        {
            return Err(AuthorityMaintenanceError(
                "authority_payload_handle_identity_collision",
            ));
        }
        let mut digest = Sha256::new();
        digest.update(PAYLOAD_SET_DOMAIN);
        for payload in [service, controller, install_helper] {
            digest.update(payload.descriptor.sha256);
            digest.update(payload.descriptor.byte_length.to_be_bytes());
            digest.update(payload.volume_serial.to_be_bytes());
            digest.update(payload.file_id);
            digest.update(payload.handle_identity.to_be_bytes());
            digest.update(payload.open_policy_receipt_sha256);
            digest.update(payload.full_readback_receipt_sha256);
        }
        Ok(Self {
            service,
            controller,
            install_helper,
            binding_sha256: digest.finalize().into(),
        })
    }

    pub(super) fn content_matches(&self, content: &AuthorityInstallContent) -> bool {
        self.service.descriptor == content.service
            && self.controller.descriptor == content.controller
            && self.install_helper.descriptor == content.install_helper
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct RawBootstrapHelperObservation {
    pub(super) process_id: u32,
    pub(super) process_creation_time: u64,
    pub(super) image_volume_serial: u64,
    pub(super) image_file_id: [u8; 16],
    pub(super) image_sha256: [u8; 32],
    pub(super) image_byte_length: u64,
    pub(super) image_handle_held: bool,
    pub(super) elevated_token: bool,
    pub(super) high_integrity: bool,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) struct VerifiedBootstrapHelperIdentity {
    pub(super) process_id: u32,
    pub(super) process_creation_time: u64,
    pub(super) image: AuthorityPayloadDigest,
    pub(super) binding_sha256: [u8; 32],
}

impl VerifiedBootstrapHelperIdentity {
    pub(super) fn from_running_helper(
        expected: AuthorityPayloadDigest,
        observed: RawBootstrapHelperObservation,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if observed.process_id == 0
            || observed.process_creation_time == 0
            || observed.image_volume_serial == 0
            || observed.image_file_id.iter().all(|value| *value == 0)
            || observed.image_sha256 != expected.sha256
            || observed.image_byte_length != expected.byte_length
            || !observed.image_handle_held
            || !observed.elevated_token
            || !observed.high_integrity
        {
            return Err(AuthorityMaintenanceError(
                "authority_bootstrap_helper_identity_not_verified",
            ));
        }
        let mut digest = Sha256::new();
        digest.update(BOOTSTRAP_HELPER_DOMAIN);
        digest.update(observed.process_id.to_be_bytes());
        digest.update(observed.process_creation_time.to_be_bytes());
        digest.update(observed.image_volume_serial.to_be_bytes());
        digest.update(observed.image_file_id);
        digest.update(observed.image_sha256);
        digest.update(observed.image_byte_length.to_be_bytes());
        Ok(Self {
            process_id: observed.process_id,
            process_creation_time: observed.process_creation_time,
            image: expected,
            binding_sha256: digest.finalize().into(),
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct VerifiedScmConfigurationProof {
    pub(super) generation: [u8; 32],
    pub(super) service_image_sha256: [u8; 32],
    pub(super) receipt_sha256: [u8; 32],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct VerifiedServiceSecurityProof {
    pub(super) receipt_sha256: [u8; 32],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct VerifiedServiceProcessProof {
    pub(super) process_id: u32,
    pub(super) process_creation_time: u64,
    pub(super) image_sha256: [u8; 32],
    pub(super) pipe_instance_id: [u8; 16],
    pub(super) held_image_receipt_sha256: [u8; 32],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct VerifiedGenerationHandshakeProof {
    pub(super) generation: [u8; 32],
    pub(super) pipe_instance_id: [u8; 16],
    pub(super) receipt_sha256: [u8; 32],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct SealedServiceGenerationReadback {
    pub(super) scm: VerifiedScmConfigurationProof,
    pub(super) security: VerifiedServiceSecurityProof,
    pub(super) process: VerifiedServiceProcessProof,
    pub(super) handshake: VerifiedGenerationHandshakeProof,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct VerifiedServiceRuntimeProof {
    pub(super) process_id: u32,
    pub(super) process_creation_time: u64,
    pub(super) image_sha256: [u8; 32],
    pub(super) pipe_instance_id: [u8; 16],
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct VerifiedPayloadFilesProof {
    pub(super) service: AuthorityPayloadDigest,
    pub(super) controller: AuthorityPayloadDigest,
    pub(super) install_helper: AuthorityPayloadDigest,
    pub(super) receipt_sha256: [u8; 32],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct VerifiedKeyProof {
    pub(super) signer_key_id: [u8; 32],
    pub(super) signer_public_key_sec1: [u8; 65],
    pub(super) receipt_sha256: [u8; 32],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct VerifiedLedgerProof {
    pub(super) ledger_identity: [u8; 32],
    pub(super) receipt_sha256: [u8; 32],
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct SealedInstalledGenerationReadback {
    pub(super) generation: [u8; 32],
    pub(super) payload_files: VerifiedPayloadFilesProof,
    pub(super) key: VerifiedKeyProof,
    pub(super) ledger: VerifiedLedgerProof,
    pub(super) service_runtime: SealedServiceGenerationReadback,
    pub(super) manifests: RawManifestChainReadback,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct VerifiedInstalledGeneration {
    pub(super) generation: [u8; 32],
    pub(super) service: AuthorityPayloadDigest,
    pub(super) controller: AuthorityPayloadDigest,
    pub(super) install_helper: AuthorityPayloadDigest,
    pub(super) signer_key_id: [u8; 32],
    pub(super) signer_public_key_sec1: [u8; 65],
    pub(super) trust_manifest_sha256: [u8; 32],
    pub(super) activation_manifest_sha256: [u8; 32],
    pub(super) activation_epoch: u64,
    pub(super) service_runtime: VerifiedServiceRuntimeProof,
}

impl VerifiedInstalledGeneration {
    pub(super) fn from_sealed_readback(
        readback: SealedInstalledGenerationReadback,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if readback.generation.iter().all(|value| *value == 0)
            || readback
                .payload_files
                .receipt_sha256
                .iter()
                .all(|value| *value == 0)
            || readback.key.receipt_sha256.iter().all(|value| *value == 0)
            || readback
                .ledger
                .receipt_sha256
                .iter()
                .all(|value| *value == 0)
        {
            return Err(AuthorityMaintenanceError(
                "authority_installed_generation_not_verified",
            ));
        }
        let service_runtime = verify_service_generation_readback(
            readback.generation,
            readback.payload_files.service,
            readback.service_runtime,
        )?;
        let manifests = verify_manifest_chain(
            readback.generation,
            &readback.key,
            &readback.ledger,
            &readback.manifests,
        )?;
        Ok(Self {
            generation: readback.generation,
            service: readback.payload_files.service,
            controller: readback.payload_files.controller,
            install_helper: readback.payload_files.install_helper,
            signer_key_id: manifests.signer_key_id,
            signer_public_key_sec1: manifests.signer_public_key_sec1,
            trust_manifest_sha256: manifests.trust_manifest_sha256,
            activation_manifest_sha256: manifests.activation_manifest_sha256,
            activation_epoch: manifests.activation_epoch,
            service_runtime,
        })
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(super) struct PriorGenerationProjection {
    pub(super) generation: String,
    pub(super) signer_key_id: String,
    pub(super) signer_public_key_sec1: String,
    pub(super) trust_manifest_sha256: String,
    pub(super) activation_manifest_sha256: String,
    pub(super) activation_epoch: u64,
    pub(super) service_process_id: u32,
    pub(super) service_process_creation_time: u64,
    pub(super) service_image_sha256: String,
    pub(super) service_pipe_instance_id: String,
    pub(super) manifest_version: u32,
    pub(super) valid: bool,
    pub(super) revoked: bool,
}

impl From<&VerifiedInstalledGeneration> for PriorGenerationProjection {
    fn from(value: &VerifiedInstalledGeneration) -> Self {
        Self {
            generation: hex_lower(&value.generation),
            signer_key_id: hex_lower(&value.signer_key_id),
            signer_public_key_sec1: hex_lower(&value.signer_public_key_sec1),
            trust_manifest_sha256: hex_lower(&value.trust_manifest_sha256),
            activation_manifest_sha256: hex_lower(&value.activation_manifest_sha256),
            activation_epoch: value.activation_epoch,
            service_process_id: value.service_runtime.process_id,
            service_process_creation_time: value.service_runtime.process_creation_time,
            service_image_sha256: hex_lower(&value.service_runtime.image_sha256),
            service_pipe_instance_id: hex_lower(&value.service_runtime.pipe_instance_id),
            manifest_version: 1,
            valid: true,
            revoked: false,
        }
    }
}

pub(super) fn verify_service_generation_readback(
    generation: [u8; 32],
    service: AuthorityPayloadDigest,
    readback: SealedServiceGenerationReadback,
) -> Result<VerifiedServiceRuntimeProof, AuthorityMaintenanceError> {
    if readback.scm.generation != generation
        || readback.scm.service_image_sha256 != service.sha256
        || readback.scm.receipt_sha256.iter().all(|value| *value == 0)
        || readback
            .security
            .receipt_sha256
            .iter()
            .all(|value| *value == 0)
        || readback.process.process_id == 0
        || readback.process.process_creation_time == 0
        || readback.process.image_sha256 != service.sha256
        || readback
            .process
            .pipe_instance_id
            .iter()
            .all(|value| *value == 0)
        || readback
            .process
            .held_image_receipt_sha256
            .iter()
            .all(|value| *value == 0)
        || readback.handshake.generation != generation
        || readback.handshake.pipe_instance_id != readback.process.pipe_instance_id
        || readback
            .handshake
            .receipt_sha256
            .iter()
            .all(|value| *value == 0)
    {
        return Err(AuthorityMaintenanceError(
            "authority_service_generation_readback_not_verified",
        ));
    }
    Ok(VerifiedServiceRuntimeProof {
        process_id: readback.process.process_id,
        process_creation_time: readback.process.process_creation_time,
        image_sha256: readback.process.image_sha256,
        pipe_instance_id: readback.process.pipe_instance_id,
    })
}
