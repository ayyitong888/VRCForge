//! One-use elevated runtime-broker admission and controller-source transfer.
//!
//! Production remains closed until the FinalCommit helper receipt, exact parent
//! lease, private pipe, service-death containment, durable store, external-six
//! owner, and native controller transfer adapters are connected. The state
//! machine deliberately owns live capabilities; serialized digests are only
//! transcript and recovery evidence.

#![cfg(windows)]
#![cfg_attr(not(test), allow(dead_code))]

use crate::primitive_evidence_authority_pipe::{
    AdmittedExternalModelPartHandles, AuthenticatedRuntimeBrokerCapability,
};
use sha2::{Digest, Sha256};
use std::{
    fmt,
    sync::atomic::{compiler_fence, AtomicBool, Ordering},
};

#[cfg(test)]
use std::sync::Arc;

const ZERO_DIGEST: [u8; 32] = [0; 32];
const INVITATION_MAGIC: &[u8; 8] = b"VRCBKR02";
const INVITATION_VERSION: u16 = 2;
const ENDPOINT_ID_BYTES: usize = 16;
const INVITATION_DIGEST_FIELD_COUNT: usize = 7;
const INVITATION_FRAME_BYTES: usize =
    INVITATION_MAGIC.len() + 2 + ENDPOINT_ID_BYTES + 32 + 32 * INVITATION_DIGEST_FIELD_COUNT;
const MAX_RECOVERABLE_ADMISSIONS: usize = 1;
const TICKET_DIGEST_DOMAIN: &[u8] = b"vrcforge-runtime-broker-ticket-v2\0";
const LIVE_BINDING_DOMAIN: &[u8] = b"vrcforge-runtime-broker-live-binding-v2\0";
const PARENT_BINDING_DOMAIN: &[u8] = b"vrcforge-runtime-broker-parent-binding-v2\0";
const BOOTSTRAP_RECEIPT_DOMAIN: &[u8] = b"vrcforge-runtime-broker-bootstrap-receipt-v2\0";
const SERVICE_EPOCH_DOMAIN: &[u8] = b"vrcforge-runtime-broker-service-epoch-v2\0";
const CONTAINMENT_DOMAIN: &[u8] = b"vrcforge-runtime-broker-containment-v2\0";
const EXPECTATION_DOMAIN: &[u8] = b"vrcforge-runtime-broker-private-expectation-v2\0";
const DURABLE_BINDING_DOMAIN: &[u8] = b"vrcforge-runtime-broker-durable-binding-v2\0";
const DURABLE_RECORD_DOMAIN: &[u8] = b"vrcforge-runtime-broker-durable-record-v2\0";

pub(crate) const BROKER_BOOTSTRAP_RECEIPT_BLOCKER: &str =
    "authority_runtime_broker_bootstrap_receipt_not_connected";
pub(crate) const BROKER_PARENT_LEASE_BLOCKER: &str =
    "authority_runtime_broker_parent_lease_not_connected";
pub(crate) const BROKER_EXTERNAL_SIX_BLOCKER: &str =
    "authority_runtime_broker_external_six_not_connected";
pub(crate) const BROKER_CONTAINMENT_BLOCKER: &str =
    "authority_runtime_broker_containment_not_connected";
pub(crate) const BROKER_DURABLE_STORE_BLOCKER: &str =
    "authority_runtime_broker_durable_store_not_connected";
pub(crate) const BROKER_PRIVATE_CHANNEL_BLOCKER: &str =
    "authority_runtime_broker_private_channel_not_connected";
pub(crate) const BROKER_CONTROLLER_TRANSFER_BLOCKER: &str =
    "authority_runtime_broker_controller_transfer_not_connected";

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct RuntimeBrokerTransferError {
    code: &'static str,
    fatal: bool,
}

impl RuntimeBrokerTransferError {
    const fn new(code: &'static str) -> Self {
        Self { code, fatal: false }
    }

    const fn fatal(code: &'static str) -> Self {
        Self { code, fatal: true }
    }

    const fn into_fatal(self) -> Self {
        Self {
            code: self.code,
            fatal: true,
        }
    }

    pub(crate) const fn code(&self) -> &'static str {
        self.code
    }

    pub(crate) const fn is_fatal(&self) -> bool {
        self.fatal
    }
}

impl fmt::Display for RuntimeBrokerTransferError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code)
    }
}

impl std::error::Error for RuntimeBrokerTransferError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct RuntimeBrokerLiveBinding {
    generation_sha256: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    source_binding_sha256: [u8; 32],
    service_process_id: u32,
    service_process_started_at: u64,
    broker_process_id: u32,
    broker_process_creation_time: u64,
    broker_session_id: u32,
    broker_image_sha256: [u8; 32],
    broker_image_object_sha256: [u8; 32],
    broker_identity_sha256: [u8; 32],
    binding_sha256: [u8; 32],
}

impl RuntimeBrokerLiveBinding {
    fn new(
        generation_sha256: [u8; 32],
        final_commit_receipt_sha256: [u8; 32],
        source_binding_sha256: [u8; 32],
        service_process_id: u32,
        service_process_started_at: u64,
        broker_process_id: u32,
        broker_process_creation_time: u64,
        broker_session_id: u32,
        broker_image_sha256: [u8; 32],
        broker_image_object_sha256: [u8; 32],
        broker_identity_sha256: [u8; 32],
    ) -> Result<Self, RuntimeBrokerTransferError> {
        let mut value = Self {
            generation_sha256,
            final_commit_receipt_sha256,
            source_binding_sha256,
            service_process_id,
            service_process_started_at,
            broker_process_id,
            broker_process_creation_time,
            broker_session_id,
            broker_image_sha256,
            broker_image_object_sha256,
            broker_identity_sha256,
            binding_sha256: ZERO_DIGEST,
        };
        value.binding_sha256 = value.canonical_digest();
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), RuntimeBrokerTransferError> {
        if self.service_process_id == 0
            || self.service_process_started_at == 0
            || self.broker_process_id == 0
            || self.broker_process_creation_time == 0
            || self.broker_session_id == 0
            || [
                self.generation_sha256,
                self.final_commit_receipt_sha256,
                self.source_binding_sha256,
                self.broker_image_sha256,
                self.broker_image_object_sha256,
                self.broker_identity_sha256,
                self.binding_sha256,
            ]
            .iter()
            .any(is_zero)
            || self.binding_sha256 != self.canonical_digest()
        {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_live_binding_invalid",
            ));
        }
        Ok(())
    }

    fn canonical_digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(LIVE_BINDING_DOMAIN);
        digest.update(self.generation_sha256);
        digest.update(self.final_commit_receipt_sha256);
        digest.update(self.source_binding_sha256);
        digest.update(self.service_process_id.to_be_bytes());
        digest.update(self.service_process_started_at.to_be_bytes());
        digest.update(self.broker_process_id.to_be_bytes());
        digest.update(self.broker_process_creation_time.to_be_bytes());
        digest.update(self.broker_session_id.to_be_bytes());
        digest.update(self.broker_image_sha256);
        digest.update(self.broker_image_object_sha256);
        digest.update(self.broker_identity_sha256);
        digest.finalize().into()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct RuntimeBrokerParentBinding {
    process_id: u32,
    process_creation_time: u64,
    process_object_sha256: [u8; 32],
    privileged_launch_receipt_sha256: [u8; 32],
    binding_sha256: [u8; 32],
}

impl RuntimeBrokerParentBinding {
    fn validate(&self) -> Result<(), RuntimeBrokerTransferError> {
        if self.process_id == 0
            || self.process_creation_time == 0
            || [
                self.process_object_sha256,
                self.privileged_launch_receipt_sha256,
                self.binding_sha256,
            ]
            .iter()
            .any(is_zero)
            || self.binding_sha256 != self.canonical_digest()
        {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_parent_binding_invalid",
            ));
        }
        Ok(())
    }

    fn canonical_digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(PARENT_BINDING_DOMAIN);
        digest.update(self.process_id.to_be_bytes());
        digest.update(self.process_creation_time.to_be_bytes());
        digest.update(self.process_object_sha256);
        digest.update(self.privileged_launch_receipt_sha256);
        digest.finalize().into()
    }

    #[cfg(test)]
    fn exact_test_fixture(seed: u8) -> Self {
        let mut value = Self {
            process_id: 3_000 + u32::from(seed),
            process_creation_time: 40_000 + u64::from(seed),
            process_object_sha256: test_digest(seed.wrapping_add(1)),
            privileged_launch_receipt_sha256: test_digest(seed.wrapping_add(2)),
            binding_sha256: ZERO_DIGEST,
        };
        value.binding_sha256 = value.canonical_digest();
        value
    }
}

pub(crate) enum RuntimeBrokerLiveCapability {
    Installed(AuthenticatedRuntimeBrokerCapability),
    #[cfg(test)]
    Test(TestRuntimeBrokerCapability),
}

impl fmt::Debug for RuntimeBrokerLiveCapability {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("RuntimeBrokerLiveCapability(<held-and-redacted>)")
    }
}

impl RuntimeBrokerLiveCapability {
    fn projection(&self) -> Result<RuntimeBrokerLiveBinding, RuntimeBrokerTransferError> {
        match self {
            Self::Installed(capability) => RuntimeBrokerLiveBinding::new(
                *capability.installed_generation(),
                *capability.final_commit_receipt_sha256(),
                *capability.source_binding_sha256(),
                capability.service_process_id(),
                capability.service_process_started_at(),
                capability.process_id(),
                capability.process_creation_time(),
                capability.session_id(),
                *capability.controller_sha256(),
                capability.controller_file_identity_digest(),
                *capability.broker_identity_sha256(),
            ),
            #[cfg(test)]
            Self::Test(capability) => {
                capability.verify()?;
                Ok(capability.binding)
            }
        }
    }

    fn revalidate(&self) -> Result<(), RuntimeBrokerTransferError> {
        match self {
            Self::Installed(capability) => capability
                .revalidate_connected_peer()
                .map_err(|error| RuntimeBrokerTransferError::new(error.code())),
            #[cfg(test)]
            Self::Test(capability) => capability.verify(),
        }
    }
}

#[cfg(test)]
pub(crate) struct TestRuntimeBrokerCapability {
    binding: RuntimeBrokerLiveBinding,
    live: Arc<AtomicBool>,
}

#[cfg(test)]
impl TestRuntimeBrokerCapability {
    fn exact_test_fixture(seed: u8, live: Arc<AtomicBool>) -> Self {
        Self {
            binding: RuntimeBrokerLiveBinding::new(
                test_digest(seed.wrapping_add(10)),
                test_digest(seed.wrapping_add(12)),
                test_digest(seed.wrapping_add(13)),
                7_000 + u32::from(seed),
                70_000 + u64::from(seed),
                4_000 + u32::from(seed),
                50_000 + u64::from(seed),
                2 + u32::from(seed),
                test_digest(seed.wrapping_add(14)),
                test_digest(seed.wrapping_add(15)),
                test_digest(seed.wrapping_add(16)),
            )
            .unwrap(),
            live,
        }
    }

    fn verify(&self) -> Result<(), RuntimeBrokerTransferError> {
        self.binding.validate()?;
        if !self.live.load(Ordering::Acquire) {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_live_peer_closed",
            ));
        }
        Ok(())
    }
}

enum HeldRuntimeBrokerParentObject {
    ProductionUnavailable,
    #[cfg(test)]
    Test(Arc<AtomicBool>),
}

pub(crate) struct HeldRuntimeBrokerParentLease {
    binding: RuntimeBrokerParentBinding,
    object: HeldRuntimeBrokerParentObject,
}

impl fmt::Debug for HeldRuntimeBrokerParentLease {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("HeldRuntimeBrokerParentLease(<held-and-redacted>)")
    }
}

impl HeldRuntimeBrokerParentLease {
    fn verify(&self) -> Result<(), RuntimeBrokerTransferError> {
        self.binding.validate()?;
        match &self.object {
            HeldRuntimeBrokerParentObject::ProductionUnavailable => {
                Err(RuntimeBrokerTransferError::new(BROKER_PARENT_LEASE_BLOCKER))
            }
            #[cfg(test)]
            HeldRuntimeBrokerParentObject::Test(live) if live.load(Ordering::Acquire) => Ok(()),
            #[cfg(test)]
            HeldRuntimeBrokerParentObject::Test(_) => Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_parent_closed",
            )),
        }
    }

    #[cfg(test)]
    fn exact_test_fixture(seed: u8, live: Arc<AtomicBool>) -> Self {
        Self {
            binding: RuntimeBrokerParentBinding::exact_test_fixture(seed),
            object: HeldRuntimeBrokerParentObject::Test(live),
        }
    }
}

pub(crate) struct RuntimeBrokerBootstrapReceipt {
    generation_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    maintenance_capsule_sha256: [u8; 32],
    maintenance_chain_sha256: [u8; 32],
    live_binding_sha256: [u8; 32],
    parent_binding_sha256: [u8; 32],
    receipt_sha256: [u8; 32],
}

impl fmt::Debug for RuntimeBrokerBootstrapReceipt {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RuntimeBrokerBootstrapReceipt")
            .field("generation_sha256", &hex_lower(&self.generation_sha256))
            .field("receipt_sha256", &hex_lower(&self.receipt_sha256))
            .finish_non_exhaustive()
    }
}

impl RuntimeBrokerBootstrapReceipt {
    fn validate(&self) -> Result<(), RuntimeBrokerTransferError> {
        if [
            self.generation_sha256,
            self.transaction_sha256,
            self.final_commit_receipt_sha256,
            self.maintenance_capsule_sha256,
            self.maintenance_chain_sha256,
            self.live_binding_sha256,
            self.parent_binding_sha256,
            self.receipt_sha256,
        ]
        .iter()
        .any(is_zero)
            || self.generation_sha256 == self.transaction_sha256
            || self.receipt_sha256 != self.canonical_digest()
        {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_bootstrap_receipt_invalid",
            ));
        }
        Ok(())
    }

    fn canonical_digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(BOOTSTRAP_RECEIPT_DOMAIN);
        digest.update(self.generation_sha256);
        digest.update(self.transaction_sha256);
        digest.update(self.final_commit_receipt_sha256);
        digest.update(self.maintenance_capsule_sha256);
        digest.update(self.maintenance_chain_sha256);
        digest.update(self.live_binding_sha256);
        digest.update(self.parent_binding_sha256);
        digest.finalize().into()
    }

    #[cfg(test)]
    fn exact_test_fixture(
        seed: u8,
        live: RuntimeBrokerLiveBinding,
        parent: RuntimeBrokerParentBinding,
    ) -> Self {
        let mut value = Self {
            generation_sha256: live.generation_sha256,
            transaction_sha256: test_digest(seed.wrapping_add(11)),
            final_commit_receipt_sha256: live.final_commit_receipt_sha256,
            maintenance_capsule_sha256: test_digest(seed.wrapping_add(17)),
            maintenance_chain_sha256: test_digest(seed.wrapping_add(18)),
            live_binding_sha256: live.binding_sha256,
            parent_binding_sha256: parent.binding_sha256,
            receipt_sha256: ZERO_DIGEST,
        };
        value.receipt_sha256 = value.canonical_digest();
        value
    }
}

pub(crate) struct AuthenticatedRuntimeBrokerBootstrapPeer {
    receipt: RuntimeBrokerBootstrapReceipt,
    live: RuntimeBrokerLiveCapability,
    parent: HeldRuntimeBrokerParentLease,
}

impl fmt::Debug for AuthenticatedRuntimeBrokerBootstrapPeer {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("AuthenticatedRuntimeBrokerBootstrapPeer(<held-and-redacted>)")
    }
}

impl AuthenticatedRuntimeBrokerBootstrapPeer {
    pub(crate) fn from_authenticated_capability(
        receipt: RuntimeBrokerBootstrapReceipt,
        live: AuthenticatedRuntimeBrokerCapability,
        parent: HeldRuntimeBrokerParentLease,
    ) -> Result<Self, RuntimeBrokerTransferError> {
        Self::from_parts(
            receipt,
            RuntimeBrokerLiveCapability::Installed(live),
            parent,
        )
    }

    fn from_parts(
        receipt: RuntimeBrokerBootstrapReceipt,
        live: RuntimeBrokerLiveCapability,
        parent: HeldRuntimeBrokerParentLease,
    ) -> Result<Self, RuntimeBrokerTransferError> {
        receipt.validate()?;
        live.revalidate()?;
        parent.verify()?;
        let projection = live.projection()?;
        if receipt.generation_sha256 != projection.generation_sha256
            || receipt.final_commit_receipt_sha256 != projection.final_commit_receipt_sha256
            || receipt.live_binding_sha256 != projection.binding_sha256
            || receipt.parent_binding_sha256 != parent.binding.binding_sha256
        {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_bootstrap_peer_mismatch",
            ));
        }
        Ok(Self {
            receipt,
            live,
            parent,
        })
    }

    fn revalidate(&self) -> Result<RuntimeBrokerLiveBinding, RuntimeBrokerTransferError> {
        self.receipt.validate()?;
        self.live.revalidate()?;
        self.parent.verify()?;
        let projection = self.live.projection()?;
        if projection.binding_sha256 != self.receipt.live_binding_sha256
            || self.parent.binding.binding_sha256 != self.receipt.parent_binding_sha256
        {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_bootstrap_peer_mismatch",
            ));
        }
        Ok(projection)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct RuntimeBrokerServiceEpoch {
    generation_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    service_process_id: u32,
    service_process_started_at: u64,
    source_binding_sha256: [u8; 32],
    binding_sha256: [u8; 32],
}

impl RuntimeBrokerServiceEpoch {
    fn from_authenticated(
        receipt: &RuntimeBrokerBootstrapReceipt,
        live: &RuntimeBrokerLiveBinding,
    ) -> Result<Self, RuntimeBrokerTransferError> {
        let mut value = Self {
            generation_sha256: receipt.generation_sha256,
            transaction_sha256: receipt.transaction_sha256,
            final_commit_receipt_sha256: receipt.final_commit_receipt_sha256,
            service_process_id: live.service_process_id,
            service_process_started_at: live.service_process_started_at,
            source_binding_sha256: live.source_binding_sha256,
            binding_sha256: ZERO_DIGEST,
        };
        value.binding_sha256 = value.canonical_digest();
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), RuntimeBrokerTransferError> {
        if self.service_process_id == 0
            || self.service_process_started_at == 0
            || [
                self.generation_sha256,
                self.transaction_sha256,
                self.final_commit_receipt_sha256,
                self.source_binding_sha256,
                self.binding_sha256,
            ]
            .iter()
            .any(is_zero)
            || self.generation_sha256 == self.transaction_sha256
            || self.binding_sha256 != self.canonical_digest()
        {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_service_epoch_invalid",
            ));
        }
        Ok(())
    }

    fn canonical_digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(SERVICE_EPOCH_DOMAIN);
        digest.update(self.generation_sha256);
        digest.update(self.transaction_sha256);
        digest.update(self.final_commit_receipt_sha256);
        digest.update(self.service_process_id.to_be_bytes());
        digest.update(self.service_process_started_at.to_be_bytes());
        digest.update(self.source_binding_sha256);
        digest.finalize().into()
    }
}

enum OwnedExternalSixObject {
    ProductionUnavailable,
    #[cfg(test)]
    Test(Arc<AtomicBool>),
}

pub(crate) struct OwnedExternalSixLaunchLease {
    admitted: AdmittedExternalModelPartHandles,
    object: OwnedExternalSixObject,
}

impl fmt::Debug for OwnedExternalSixLaunchLease {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("OwnedExternalSixLaunchLease(<six-held-objects>)")
    }
}

impl OwnedExternalSixLaunchLease {
    fn verify(&self, epoch: &RuntimeBrokerServiceEpoch) -> Result<(), RuntimeBrokerTransferError> {
        let context = self.admitted.binding().context();
        if context.generation_sha256() != &epoch.generation_sha256
            || context.transaction_sha256() != &epoch.transaction_sha256
            || is_zero(self.admitted.binding().binding_sha256())
        {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_external_six_binding_invalid",
            ));
        }
        match &self.object {
            OwnedExternalSixObject::ProductionUnavailable => {
                Err(RuntimeBrokerTransferError::new(BROKER_EXTERNAL_SIX_BLOCKER))
            }
            #[cfg(test)]
            OwnedExternalSixObject::Test(live) if live.load(Ordering::Acquire) => Ok(()),
            #[cfg(test)]
            OwnedExternalSixObject::Test(_) => Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_external_six_closed",
            )),
        }
    }

    #[cfg(test)]
    fn exact_test_fixture(
        epoch: &RuntimeBrokerServiceEpoch,
        seed: u8,
        live: Arc<AtomicBool>,
    ) -> Self {
        Self {
            admitted: AdmittedExternalModelPartHandles::exact_test_fixture(
                epoch.generation_sha256,
                epoch.transaction_sha256,
                seed,
            )
            .unwrap(),
            object: OwnedExternalSixObject::Test(live),
        }
    }
}

enum RuntimeBrokerContainmentObject {
    ProductionUnavailable,
    #[cfg(test)]
    Test(Arc<AtomicBool>),
}

pub(crate) struct RuntimeBrokerCrashContainmentLease {
    live_binding_sha256: [u8; 32],
    service_epoch_sha256: [u8; 32],
    job_binding_sha256: [u8; 32],
    kill_on_service_close: bool,
    exact_member_verified: bool,
    binding_sha256: [u8; 32],
    object: RuntimeBrokerContainmentObject,
}

impl fmt::Debug for RuntimeBrokerCrashContainmentLease {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("RuntimeBrokerCrashContainmentLease(<held-job>)")
    }
}

impl RuntimeBrokerCrashContainmentLease {
    fn verify(
        &self,
        live: &RuntimeBrokerLiveBinding,
        epoch: &RuntimeBrokerServiceEpoch,
    ) -> Result<(), RuntimeBrokerTransferError> {
        if self.live_binding_sha256 != live.binding_sha256
            || self.service_epoch_sha256 != epoch.binding_sha256
            || is_zero(&self.job_binding_sha256)
            || !self.kill_on_service_close
            || !self.exact_member_verified
            || self.binding_sha256 != self.canonical_digest()
        {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_containment_invalid",
            ));
        }
        match &self.object {
            RuntimeBrokerContainmentObject::ProductionUnavailable => {
                Err(RuntimeBrokerTransferError::new(BROKER_CONTAINMENT_BLOCKER))
            }
            #[cfg(test)]
            RuntimeBrokerContainmentObject::Test(live) if live.load(Ordering::Acquire) => Ok(()),
            #[cfg(test)]
            RuntimeBrokerContainmentObject::Test(_) => Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_containment_lost",
            )),
        }
    }

    fn canonical_digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(CONTAINMENT_DOMAIN);
        digest.update(self.live_binding_sha256);
        digest.update(self.service_epoch_sha256);
        digest.update(self.job_binding_sha256);
        digest.update([u8::from(self.kill_on_service_close)]);
        digest.update([u8::from(self.exact_member_verified)]);
        digest.finalize().into()
    }

    #[cfg(test)]
    fn exact_test_fixture(
        live: &RuntimeBrokerLiveBinding,
        epoch: &RuntimeBrokerServiceEpoch,
        object_live: Arc<AtomicBool>,
    ) -> Self {
        let mut value = Self {
            live_binding_sha256: live.binding_sha256,
            service_epoch_sha256: epoch.binding_sha256,
            job_binding_sha256: test_digest(0xd1),
            kill_on_service_close: true,
            exact_member_verified: true,
            binding_sha256: ZERO_DIGEST,
            object: RuntimeBrokerContainmentObject::Test(object_live),
        };
        value.binding_sha256 = value.canonical_digest();
        value
    }
}

struct RuntimeBrokerTicketSecret([u8; 32]);

impl RuntimeBrokerTicketSecret {
    fn generate() -> Result<Self, RuntimeBrokerTransferError> {
        let mut value = Self([0; 32]);
        getrandom::fill(&mut value.0).map_err(|_| {
            RuntimeBrokerTransferError::new("authority_runtime_broker_ticket_random_failed")
        })?;
        if is_zero(&value.0) {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_ticket_random_failed",
            ));
        }
        Ok(value)
    }

    fn digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(TICKET_DIGEST_DOMAIN);
        digest.update(&self.0);
        digest.finalize().into()
    }

    #[cfg(test)]
    fn exact_test_fixture(seed: u8) -> Self {
        Self(test_digest(seed))
    }
}

impl fmt::Debug for RuntimeBrokerTicketSecret {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("RuntimeBrokerTicketSecret(<redacted>)")
    }
}

impl Drop for RuntimeBrokerTicketSecret {
    fn drop(&mut self) {
        volatile_zero(&mut self.0);
    }
}

pub(crate) struct SensitiveRuntimeBrokerInvitationFrame {
    bytes: [u8; INVITATION_FRAME_BYTES],
}

impl SensitiveRuntimeBrokerInvitationFrame {
    fn build(
        endpoint_id: [u8; ENDPOINT_ID_BYTES],
        ticket: RuntimeBrokerTicketSecret,
        expectation: &RuntimeBrokerPrivateChannelExpectation,
    ) -> Result<Self, RuntimeBrokerTransferError> {
        expectation.validate()?;
        if is_zero_16(&endpoint_id) || ticket.digest() != expectation.ticket_digest_sha256 {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_invitation_binding_invalid",
            ));
        }
        let mut value = Self {
            bytes: [0; INVITATION_FRAME_BYTES],
        };
        let mut offset = 0usize;
        value.write_field(&mut offset, INVITATION_MAGIC)?;
        value.write_field(&mut offset, &INVITATION_VERSION.to_be_bytes())?;
        value.write_field(&mut offset, &endpoint_id)?;
        value.write_field(&mut offset, &ticket.0)?;
        for digest in [
            expectation.expectation_sha256,
            expectation.live_binding_sha256,
            expectation.generation_sha256,
            expectation.transaction_sha256,
            expectation.final_commit_receipt_sha256,
            expectation.external_six_binding_sha256,
            expectation.bootstrap_receipt_sha256,
        ] {
            value.write_field(&mut offset, &digest)?;
        }
        if offset != INVITATION_FRAME_BYTES {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_invitation_size_invalid",
            ));
        }
        drop(ticket);
        Ok(value)
    }

    fn write_field(
        &mut self,
        offset: &mut usize,
        field: &[u8],
    ) -> Result<(), RuntimeBrokerTransferError> {
        let end = offset.checked_add(field.len()).ok_or_else(|| {
            RuntimeBrokerTransferError::new("authority_runtime_broker_invitation_size_invalid")
        })?;
        let target = self.bytes.get_mut(*offset..end).ok_or_else(|| {
            RuntimeBrokerTransferError::new("authority_runtime_broker_invitation_size_invalid")
        })?;
        target.copy_from_slice(field);
        *offset = end;
        Ok(())
    }

    #[cfg(test)]
    fn as_bytes_for_test(&self) -> &[u8; INVITATION_FRAME_BYTES] {
        &self.bytes
    }

    #[cfg(test)]
    fn tamper_for_test(&mut self, offset: usize) {
        self.bytes[offset] ^= 1;
    }

    #[cfg(test)]
    fn parse_for_test(
        &self,
    ) -> Result<PresentedRuntimeBrokerInvitation, RuntimeBrokerTransferError> {
        if &self.bytes[..INVITATION_MAGIC.len()] != INVITATION_MAGIC
            || u16::from_be_bytes([
                self.bytes[INVITATION_MAGIC.len()],
                self.bytes[INVITATION_MAGIC.len() + 1],
            ]) != INVITATION_VERSION
        {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_invitation_invalid",
            ));
        }
        let mut offset = INVITATION_MAGIC.len() + 2;
        let endpoint_id = take_array::<ENDPOINT_ID_BYTES>(&self.bytes, &mut offset)?;
        let ticket = RuntimeBrokerTicketSecret(take_array::<32>(&self.bytes, &mut offset)?);
        let value = PresentedRuntimeBrokerInvitation {
            endpoint_id,
            ticket,
            expectation_sha256: take_array::<32>(&self.bytes, &mut offset)?,
            live_binding_sha256: take_array::<32>(&self.bytes, &mut offset)?,
            generation_sha256: take_array::<32>(&self.bytes, &mut offset)?,
            transaction_sha256: take_array::<32>(&self.bytes, &mut offset)?,
            final_commit_receipt_sha256: take_array::<32>(&self.bytes, &mut offset)?,
            external_six_binding_sha256: take_array::<32>(&self.bytes, &mut offset)?,
            bootstrap_receipt_sha256: take_array::<32>(&self.bytes, &mut offset)?,
        };
        if offset != self.bytes.len() {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_invitation_invalid",
            ));
        }
        value.validate()?;
        Ok(value)
    }
}

impl fmt::Debug for SensitiveRuntimeBrokerInvitationFrame {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SensitiveRuntimeBrokerInvitationFrame(<redacted>)")
    }
}

impl Drop for SensitiveRuntimeBrokerInvitationFrame {
    fn drop(&mut self) {
        volatile_zero(&mut self.bytes);
    }
}

struct PresentedRuntimeBrokerInvitation {
    endpoint_id: [u8; ENDPOINT_ID_BYTES],
    ticket: RuntimeBrokerTicketSecret,
    expectation_sha256: [u8; 32],
    live_binding_sha256: [u8; 32],
    generation_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    external_six_binding_sha256: [u8; 32],
    bootstrap_receipt_sha256: [u8; 32],
}

impl PresentedRuntimeBrokerInvitation {
    fn validate(&self) -> Result<(), RuntimeBrokerTransferError> {
        if is_zero_16(&self.endpoint_id)
            || [
                self.expectation_sha256,
                self.live_binding_sha256,
                self.generation_sha256,
                self.transaction_sha256,
                self.final_commit_receipt_sha256,
                self.external_six_binding_sha256,
                self.bootstrap_receipt_sha256,
            ]
            .iter()
            .any(is_zero)
        {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_invitation_invalid",
            ));
        }
        Ok(())
    }
}

impl fmt::Debug for PresentedRuntimeBrokerInvitation {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("PresentedRuntimeBrokerInvitation(<redacted>)")
    }
}

impl Drop for PresentedRuntimeBrokerInvitation {
    fn drop(&mut self) {
        volatile_zero(&mut self.endpoint_id);
        volatile_zero(&mut self.expectation_sha256);
        volatile_zero(&mut self.live_binding_sha256);
        volatile_zero(&mut self.generation_sha256);
        volatile_zero(&mut self.transaction_sha256);
        volatile_zero(&mut self.final_commit_receipt_sha256);
        volatile_zero(&mut self.external_six_binding_sha256);
        volatile_zero(&mut self.bootstrap_receipt_sha256);
    }
}

enum DedicatedRuntimeBrokerChannelObject {
    ProductionUnavailable,
    #[cfg(test)]
    Test(Arc<AtomicBool>),
}

pub(crate) struct DedicatedRuntimeBrokerChannelLease {
    endpoint_id: [u8; ENDPOINT_ID_BYTES],
    live_binding_sha256: [u8; 32],
    object: DedicatedRuntimeBrokerChannelObject,
}

impl fmt::Debug for DedicatedRuntimeBrokerChannelLease {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("DedicatedRuntimeBrokerChannelLease(<held-private-pipe>)")
    }
}

impl DedicatedRuntimeBrokerChannelLease {
    fn verify(
        &self,
        endpoint_id: &[u8; ENDPOINT_ID_BYTES],
        live_binding_sha256: &[u8; 32],
    ) -> Result<(), RuntimeBrokerTransferError> {
        if &self.endpoint_id != endpoint_id
            || &self.live_binding_sha256 != live_binding_sha256
            || is_zero_16(&self.endpoint_id)
        {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_private_channel_mismatch",
            ));
        }
        match &self.object {
            DedicatedRuntimeBrokerChannelObject::ProductionUnavailable => Err(
                RuntimeBrokerTransferError::new(BROKER_PRIVATE_CHANNEL_BLOCKER),
            ),
            #[cfg(test)]
            DedicatedRuntimeBrokerChannelObject::Test(live) if live.load(Ordering::Acquire) => {
                Ok(())
            }
            #[cfg(test)]
            DedicatedRuntimeBrokerChannelObject::Test(_) => Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_private_channel_closed",
            )),
        }
    }

    #[cfg(test)]
    fn exact_test_fixture(
        endpoint_id: [u8; ENDPOINT_ID_BYTES],
        live_binding_sha256: [u8; 32],
        live: Arc<AtomicBool>,
    ) -> Self {
        Self {
            endpoint_id,
            live_binding_sha256,
            object: DedicatedRuntimeBrokerChannelObject::Test(live),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub(crate) enum RuntimeBrokerDurableState {
    Issued = 1,
    Consuming = 2,
    Consumed = 3,
    Burned = 4,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub(crate) enum RuntimeBrokerBurnReason {
    Rejected = 1,
    Disconnected = 2,
    TransferFailed = 3,
    Dropped = 4,
    RestartRecovery = 5,
    ActionCompleted = 6,
    ActionFailed = 7,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct RuntimeBrokerDurableRecord {
    ticket_digest_sha256: [u8; 32],
    durable_binding_sha256: [u8; 32],
    endpoint_sha256: [u8; 32],
    service_epoch_sha256: [u8; 32],
    live_binding: RuntimeBrokerLiveBinding,
    parent_binding: RuntimeBrokerParentBinding,
    containment_binding_sha256: [u8; 32],
    external_six_binding_sha256: [u8; 32],
    state: RuntimeBrokerDurableState,
    burn_reason: Option<RuntimeBrokerBurnReason>,
    record_sha256: [u8; 32],
}

impl RuntimeBrokerDurableRecord {
    fn new(
        expectation: &RuntimeBrokerPrivateChannelExpectation,
        state: RuntimeBrokerDurableState,
        burn_reason: Option<RuntimeBrokerBurnReason>,
    ) -> Result<Self, RuntimeBrokerTransferError> {
        expectation.validate()?;
        if matches!(state, RuntimeBrokerDurableState::Burned) != burn_reason.is_some() {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_durable_transition_invalid",
            ));
        }
        let mut value = Self {
            ticket_digest_sha256: expectation.ticket_digest_sha256,
            durable_binding_sha256: expectation.durable_binding_sha256,
            endpoint_sha256: endpoint_digest(&expectation.endpoint_id),
            service_epoch_sha256: expectation.service_epoch.binding_sha256,
            live_binding: expectation.live_binding,
            parent_binding: expectation.parent_binding,
            containment_binding_sha256: expectation.containment_binding_sha256,
            external_six_binding_sha256: expectation.external_six_binding_sha256,
            state,
            burn_reason,
            record_sha256: ZERO_DIGEST,
        };
        value.record_sha256 = value.canonical_digest();
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), RuntimeBrokerTransferError> {
        self.live_binding.validate()?;
        self.parent_binding.validate()?;
        if [
            self.ticket_digest_sha256,
            self.durable_binding_sha256,
            self.endpoint_sha256,
            self.service_epoch_sha256,
            self.containment_binding_sha256,
            self.external_six_binding_sha256,
            self.record_sha256,
        ]
        .iter()
        .any(is_zero)
            || matches!(self.state, RuntimeBrokerDurableState::Burned) != self.burn_reason.is_some()
            || self.record_sha256 != self.canonical_digest()
        {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_durable_record_invalid",
            ));
        }
        Ok(())
    }

    fn canonical_digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(DURABLE_RECORD_DOMAIN);
        digest.update(self.ticket_digest_sha256);
        digest.update(self.durable_binding_sha256);
        digest.update(self.endpoint_sha256);
        digest.update(self.service_epoch_sha256);
        digest.update(self.live_binding.binding_sha256);
        digest.update(self.parent_binding.binding_sha256);
        digest.update(self.containment_binding_sha256);
        digest.update(self.external_six_binding_sha256);
        digest.update([self.state as u8]);
        digest.update([self.burn_reason.map_or(0, |reason| reason as u8)]);
        digest.finalize().into()
    }

    pub(crate) const fn state(&self) -> RuntimeBrokerDurableState {
        self.state
    }
}

pub(crate) trait RuntimeBrokerDurableStore {
    fn compare_and_append_flush(
        &mut self,
        expected_prior: Option<RuntimeBrokerDurableState>,
        record: &RuntimeBrokerDurableRecord,
    ) -> Result<(), RuntimeBrokerTransferError>;

    fn readback(
        &mut self,
        ticket_digest_sha256: &[u8; 32],
    ) -> Result<Option<RuntimeBrokerDurableRecord>, RuntimeBrokerTransferError>;

    fn nonterminal_records(
        &mut self,
        maximum: usize,
    ) -> Result<Vec<RuntimeBrokerDurableRecord>, RuntimeBrokerTransferError>;

    fn contain_restart_peer(
        &mut self,
        record: &RuntimeBrokerDurableRecord,
    ) -> Result<(), RuntimeBrokerTransferError>;

    fn latch_poisoned(&mut self);

    fn is_poisoned(&self) -> bool;
}

pub(crate) struct RuntimeBrokerFatalLatch {
    poisoned: AtomicBool,
}

impl RuntimeBrokerFatalLatch {
    pub(crate) const fn new() -> Self {
        Self {
            poisoned: AtomicBool::new(false),
        }
    }

    fn trip(&self) {
        self.poisoned.store(true, Ordering::Release);
    }

    pub(crate) fn is_poisoned(&self) -> bool {
        self.poisoned.load(Ordering::Acquire)
    }
}

pub(crate) trait RuntimeBrokerRemoteControllerSourceLease {
    fn verify_same_object(
        &self,
        prerequisite: &DurablyConsumedRuntimeBrokerTicket,
        peer: &RuntimeBrokerLiveCapability,
    ) -> Result<(), RuntimeBrokerTransferError>;
}

pub(crate) trait RuntimeBrokerControllerTransferBackend {
    type HeldSource;
    type TransferredSource: RuntimeBrokerRemoteControllerSourceLease;

    fn transfer_and_verify_same_object(
        &mut self,
        prerequisite: &DurablyConsumedRuntimeBrokerTicket,
        peer: &RuntimeBrokerLiveCapability,
        containment: &RuntimeBrokerCrashContainmentLease,
        held_source: &Self::HeldSource,
    ) -> Result<Self::TransferredSource, RuntimeBrokerTransferError>;
}

pub(crate) struct DurablyConsumedRuntimeBrokerTicket {
    ticket_digest_sha256: [u8; 32],
    durable_binding_sha256: [u8; 32],
    live_binding_sha256: [u8; 32],
    containment_binding_sha256: [u8; 32],
    external_six_binding_sha256: [u8; 32],
}

pub(crate) struct RuntimeBrokerTransferredControllerSource<
    T: RuntimeBrokerRemoteControllerSourceLease,
> {
    inner: T,
}

impl<T: RuntimeBrokerRemoteControllerSourceLease> fmt::Debug
    for RuntimeBrokerTransferredControllerSource<T>
{
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("RuntimeBrokerTransferredControllerSource(<opaque>)")
    }
}

pub(crate) struct RuntimeBrokerActionAuthority {
    _private: (),
}

impl fmt::Debug for RuntimeBrokerActionAuthority {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("RuntimeBrokerActionAuthority(<one-use>)")
    }
}

pub(crate) trait RuntimeBrokerOneShotActionBackend<T: RuntimeBrokerRemoteControllerSourceLease> {
    type Output;

    fn launch_once(
        &mut self,
        authority: &mut RuntimeBrokerActionAuthority,
        source: &RuntimeBrokerTransferredControllerSource<T>,
        external_six: &OwnedExternalSixLaunchLease,
        peer: &RuntimeBrokerLiveCapability,
        containment: &RuntimeBrokerCrashContainmentLease,
    ) -> Result<Self::Output, RuntimeBrokerTransferError>;
}

struct RuntimeBrokerPrivateChannelExpectation {
    endpoint_id: [u8; ENDPOINT_ID_BYTES],
    ticket_digest_sha256: [u8; 32],
    live_binding: RuntimeBrokerLiveBinding,
    live_binding_sha256: [u8; 32],
    parent_binding: RuntimeBrokerParentBinding,
    generation_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
    final_commit_receipt_sha256: [u8; 32],
    external_six_binding_sha256: [u8; 32],
    bootstrap_receipt_sha256: [u8; 32],
    containment_binding_sha256: [u8; 32],
    service_epoch: RuntimeBrokerServiceEpoch,
    expectation_sha256: [u8; 32],
    durable_binding_sha256: [u8; 32],
}

impl RuntimeBrokerPrivateChannelExpectation {
    fn new(
        endpoint_id: [u8; ENDPOINT_ID_BYTES],
        ticket_digest_sha256: [u8; 32],
        authenticated: &AuthenticatedRuntimeBrokerBootstrapPeer,
        external_six: &OwnedExternalSixLaunchLease,
        containment: &RuntimeBrokerCrashContainmentLease,
    ) -> Result<Self, RuntimeBrokerTransferError> {
        let live = authenticated.revalidate()?;
        let service_epoch =
            RuntimeBrokerServiceEpoch::from_authenticated(&authenticated.receipt, &live)?;
        external_six.verify(&service_epoch)?;
        containment.verify(&live, &service_epoch)?;
        let mut value = Self {
            endpoint_id,
            ticket_digest_sha256,
            live_binding: live,
            live_binding_sha256: live.binding_sha256,
            parent_binding: authenticated.parent.binding,
            generation_sha256: authenticated.receipt.generation_sha256,
            transaction_sha256: authenticated.receipt.transaction_sha256,
            final_commit_receipt_sha256: authenticated.receipt.final_commit_receipt_sha256,
            external_six_binding_sha256: *external_six.admitted.binding().binding_sha256(),
            bootstrap_receipt_sha256: authenticated.receipt.receipt_sha256,
            containment_binding_sha256: containment.binding_sha256,
            service_epoch,
            expectation_sha256: ZERO_DIGEST,
            durable_binding_sha256: ZERO_DIGEST,
        };
        value.expectation_sha256 = value.canonical_expectation_digest();
        value.durable_binding_sha256 = value.canonical_durable_binding();
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), RuntimeBrokerTransferError> {
        self.live_binding.validate()?;
        self.parent_binding.validate()?;
        self.service_epoch.validate()?;
        if is_zero_16(&self.endpoint_id)
            || [
                self.ticket_digest_sha256,
                self.live_binding_sha256,
                self.generation_sha256,
                self.transaction_sha256,
                self.final_commit_receipt_sha256,
                self.external_six_binding_sha256,
                self.bootstrap_receipt_sha256,
                self.containment_binding_sha256,
                self.expectation_sha256,
                self.durable_binding_sha256,
            ]
            .iter()
            .any(is_zero)
            || self.live_binding_sha256 != self.live_binding.binding_sha256
            || self.expectation_sha256 != self.canonical_expectation_digest()
            || self.durable_binding_sha256 != self.canonical_durable_binding()
        {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_expectation_invalid",
            ));
        }
        Ok(())
    }

    fn canonical_expectation_digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(EXPECTATION_DOMAIN);
        digest.update(self.endpoint_id);
        digest.update(self.ticket_digest_sha256);
        digest.update(self.live_binding_sha256);
        digest.update(self.parent_binding.binding_sha256);
        digest.update(self.generation_sha256);
        digest.update(self.transaction_sha256);
        digest.update(self.final_commit_receipt_sha256);
        digest.update(self.external_six_binding_sha256);
        digest.update(self.bootstrap_receipt_sha256);
        digest.update(self.containment_binding_sha256);
        digest.update(self.service_epoch.binding_sha256);
        digest.finalize().into()
    }

    fn canonical_durable_binding(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(DURABLE_BINDING_DOMAIN);
        digest.update(self.canonical_expectation_digest());
        digest.update(endpoint_digest(&self.endpoint_id));
        digest.update(self.live_binding.binding_sha256);
        digest.update(self.parent_binding.binding_sha256);
        digest.update(self.containment_binding_sha256);
        digest.update(self.service_epoch.binding_sha256);
        digest.finalize().into()
    }
}

struct RuntimeBrokerLiveCapabilities {
    authenticated: AuthenticatedRuntimeBrokerBootstrapPeer,
    external_six: OwnedExternalSixLaunchLease,
    containment: RuntimeBrokerCrashContainmentLease,
    private_channel: Option<DedicatedRuntimeBrokerChannelLease>,
}

impl RuntimeBrokerLiveCapabilities {
    fn verify(
        &self,
        expectation: &RuntimeBrokerPrivateChannelExpectation,
        require_channel: bool,
    ) -> Result<(), RuntimeBrokerTransferError> {
        let live = self.authenticated.revalidate()?;
        if live != expectation.live_binding {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_live_binding_changed",
            ));
        }
        self.external_six.verify(&expectation.service_epoch)?;
        self.containment.verify(&live, &expectation.service_epoch)?;
        if require_channel {
            self.private_channel
                .as_ref()
                .ok_or_else(|| {
                    RuntimeBrokerTransferError::new(
                        "authority_runtime_broker_private_channel_missing",
                    )
                })?
                .verify(&expectation.endpoint_id, &expectation.live_binding_sha256)?;
        }
        Ok(())
    }
}

pub(crate) struct RecoveredRuntimeBrokerLane<'a, S: RuntimeBrokerDurableStore> {
    store: &'a mut S,
    fatal: &'a RuntimeBrokerFatalLatch,
}

pub(crate) struct IssuedRuntimeBrokerAdmission<'a, S: RuntimeBrokerDurableStore> {
    store: Option<&'a mut S>,
    fatal: &'a RuntimeBrokerFatalLatch,
    expectation: Option<RuntimeBrokerPrivateChannelExpectation>,
    capabilities: Option<RuntimeBrokerLiveCapabilities>,
    state: RuntimeBrokerDurableState,
    active: bool,
}

pub(crate) struct ConsumingRuntimeBrokerAdmission<'a, S: RuntimeBrokerDurableStore> {
    store: Option<&'a mut S>,
    fatal: &'a RuntimeBrokerFatalLatch,
    expectation: Option<RuntimeBrokerPrivateChannelExpectation>,
    capabilities: Option<RuntimeBrokerLiveCapabilities>,
    state: RuntimeBrokerDurableState,
    active: bool,
}

pub(crate) struct ConsumedRuntimeBrokerAdmission<
    'a,
    S: RuntimeBrokerDurableStore,
    T: RuntimeBrokerRemoteControllerSourceLease,
> {
    store: Option<&'a mut S>,
    fatal: &'a RuntimeBrokerFatalLatch,
    expectation: Option<RuntimeBrokerPrivateChannelExpectation>,
    capabilities: Option<RuntimeBrokerLiveCapabilities>,
    transferred_source: Option<RuntimeBrokerTransferredControllerSource<T>>,
    action_authority: Option<RuntimeBrokerActionAuthority>,
    state: RuntimeBrokerDurableState,
    active: bool,
}

pub(crate) fn recover_runtime_broker_admissions_before_accept<'a, S: RuntimeBrokerDurableStore>(
    store: &'a mut S,
    fatal: &'a RuntimeBrokerFatalLatch,
) -> Result<RecoveredRuntimeBrokerLane<'a, S>, RuntimeBrokerTransferError> {
    if fatal.is_poisoned() || store.is_poisoned() {
        fatal.trip();
        return Err(RuntimeBrokerTransferError::fatal(
            "authority_runtime_broker_lane_poisoned",
        ));
    }
    let records = match store.nonterminal_records(MAX_RECOVERABLE_ADMISSIONS + 1) {
        Ok(records) if records.len() <= MAX_RECOVERABLE_ADMISSIONS => records,
        Ok(_) => {
            return Err(poison_lane(
                store,
                fatal,
                RuntimeBrokerTransferError::new(
                    "authority_runtime_broker_recovery_capacity_exceeded",
                ),
            ));
        }
        Err(error) => return Err(poison_lane(store, fatal, error)),
    };
    for record in records {
        if let Err(error) = record.validate() {
            return Err(poison_lane(store, fatal, error));
        }
        if let Err(error) = store.contain_restart_peer(&record) {
            return Err(poison_lane(store, fatal, error));
        }
        let replacement = RuntimeBrokerDurableRecord::new_from_record(
            &record,
            RuntimeBrokerDurableState::Burned,
            Some(RuntimeBrokerBurnReason::RestartRecovery),
        )?;
        persist_exact(store, fatal, Some(record.state), replacement)?;
    }
    match store.nonterminal_records(MAX_RECOVERABLE_ADMISSIONS + 1) {
        Ok(records) if records.is_empty() => Ok(RecoveredRuntimeBrokerLane { store, fatal }),
        Ok(_) => Err(poison_lane(
            store,
            fatal,
            RuntimeBrokerTransferError::new("authority_runtime_broker_recovery_incomplete"),
        )),
        Err(error) => Err(poison_lane(store, fatal, error)),
    }
}

impl RuntimeBrokerDurableRecord {
    fn new_from_record(
        prior: &Self,
        state: RuntimeBrokerDurableState,
        burn_reason: Option<RuntimeBrokerBurnReason>,
    ) -> Result<Self, RuntimeBrokerTransferError> {
        if state != RuntimeBrokerDurableState::Burned || burn_reason.is_none() {
            return Err(RuntimeBrokerTransferError::new(
                "authority_runtime_broker_durable_transition_invalid",
            ));
        }
        let mut value = prior.clone();
        value.state = state;
        value.burn_reason = burn_reason;
        value.record_sha256 = ZERO_DIGEST;
        value.record_sha256 = value.canonical_digest();
        value.validate()?;
        Ok(value)
    }
}

pub(crate) fn issue_runtime_broker_invitation<'a, 'store, S: RuntimeBrokerDurableStore>(
    lane: &'a mut RecoveredRuntimeBrokerLane<'store, S>,
    authenticated: AuthenticatedRuntimeBrokerBootstrapPeer,
    external_six: OwnedExternalSixLaunchLease,
    containment: RuntimeBrokerCrashContainmentLease,
) -> Result<
    (
        IssuedRuntimeBrokerAdmission<'a, S>,
        SensitiveRuntimeBrokerInvitationFrame,
    ),
    RuntimeBrokerTransferError,
> {
    if lane.fatal.is_poisoned() || lane.store.is_poisoned() {
        lane.fatal.trip();
        return Err(RuntimeBrokerTransferError::fatal(
            "authority_runtime_broker_lane_poisoned",
        ));
    }
    match lane.store.nonterminal_records(1) {
        Ok(records) if records.is_empty() => {}
        Ok(_) => {
            return Err(poison_lane(
                lane.store,
                lane.fatal,
                RuntimeBrokerTransferError::new("authority_runtime_broker_recovery_required"),
            ));
        }
        Err(error) => return Err(poison_lane(lane.store, lane.fatal, error)),
    }
    let ticket = RuntimeBrokerTicketSecret::generate()?;
    let mut endpoint_id = [0u8; ENDPOINT_ID_BYTES];
    getrandom::fill(&mut endpoint_id).map_err(|_| {
        RuntimeBrokerTransferError::new("authority_runtime_broker_endpoint_random_failed")
    })?;
    if is_zero_16(&endpoint_id) {
        return Err(RuntimeBrokerTransferError::new(
            "authority_runtime_broker_endpoint_random_failed",
        ));
    }
    let expectation = RuntimeBrokerPrivateChannelExpectation::new(
        endpoint_id,
        ticket.digest(),
        &authenticated,
        &external_six,
        &containment,
    )?;
    persist_exact(
        lane.store,
        lane.fatal,
        None,
        RuntimeBrokerDurableRecord::new(&expectation, RuntimeBrokerDurableState::Issued, None)?,
    )?;
    let admission = IssuedRuntimeBrokerAdmission {
        store: Some(&mut *lane.store),
        fatal: lane.fatal,
        expectation: Some(expectation),
        capabilities: Some(RuntimeBrokerLiveCapabilities {
            authenticated,
            external_six,
            containment,
            private_channel: None,
        }),
        state: RuntimeBrokerDurableState::Issued,
        active: true,
    };
    let expectation = admission.expectation.as_ref().ok_or_else(|| {
        RuntimeBrokerTransferError::fatal("authority_runtime_broker_expectation_missing")
    })?;
    let frame = SensitiveRuntimeBrokerInvitationFrame::build(endpoint_id, ticket, expectation)?;
    Ok((admission, frame))
}

impl<'a, S: RuntimeBrokerDurableStore> IssuedRuntimeBrokerAdmission<'a, S> {
    fn authenticate_dedicated_channel(
        mut self,
        presented: PresentedRuntimeBrokerInvitation,
        channel: DedicatedRuntimeBrokerChannelLease,
    ) -> Result<ConsumingRuntimeBrokerAdmission<'a, S>, RuntimeBrokerTransferError> {
        let result = (|| {
            let expectation = require_expectation(&self.expectation)?;
            let capabilities = require_capabilities(&self.capabilities)?;
            capabilities.verify(expectation, false)?;
            presented.validate()?;
            channel.verify(&expectation.endpoint_id, &expectation.live_binding_sha256)?;
            if !constant_time_eq(
                &presented.ticket.digest(),
                &expectation.ticket_digest_sha256,
            ) || presented.endpoint_id != expectation.endpoint_id
                || presented.expectation_sha256 != expectation.expectation_sha256
                || presented.live_binding_sha256 != expectation.live_binding_sha256
                || presented.generation_sha256 != expectation.generation_sha256
                || presented.transaction_sha256 != expectation.transaction_sha256
                || presented.final_commit_receipt_sha256 != expectation.final_commit_receipt_sha256
                || presented.external_six_binding_sha256 != expectation.external_six_binding_sha256
                || presented.bootstrap_receipt_sha256 != expectation.bootstrap_receipt_sha256
            {
                return Err(RuntimeBrokerTransferError::new(
                    "authority_runtime_broker_private_channel_mismatch",
                ));
            }
            persist_exact(
                require_store(&mut self.store)?,
                self.fatal,
                Some(RuntimeBrokerDurableState::Issued),
                RuntimeBrokerDurableRecord::new(
                    expectation,
                    RuntimeBrokerDurableState::Consuming,
                    None,
                )?,
            )?;
            Ok(())
        })();
        if let Err(error) = result {
            return Err(self.fail_and_burn(RuntimeBrokerBurnReason::Rejected, error));
        }
        self.state = RuntimeBrokerDurableState::Consuming;
        require_capabilities_mut(&mut self.capabilities)?.private_channel = Some(channel);
        require_capabilities(&self.capabilities)?
            .verify(require_expectation(&self.expectation)?, true)?;
        let store = self.store.take().ok_or_else(missing_store)?;
        let expectation = self.expectation.take().ok_or_else(missing_expectation)?;
        let capabilities = self.capabilities.take().ok_or_else(missing_capabilities)?;
        self.active = false;
        Ok(ConsumingRuntimeBrokerAdmission {
            store: Some(store),
            fatal: self.fatal,
            expectation: Some(expectation),
            capabilities: Some(capabilities),
            state: RuntimeBrokerDurableState::Consuming,
            active: true,
        })
    }

    fn fail_and_burn(
        &mut self,
        reason: RuntimeBrokerBurnReason,
        error: RuntimeBrokerTransferError,
    ) -> RuntimeBrokerTransferError {
        match self.burn(reason) {
            Ok(()) => error,
            Err(burn) => burn,
        }
    }

    fn burn(&mut self, reason: RuntimeBrokerBurnReason) -> Result<(), RuntimeBrokerTransferError> {
        let result = burn_owned(
            &mut self.store,
            self.fatal,
            self.expectation.as_ref(),
            self.state,
            reason,
        );
        self.active = false;
        result
    }
}

impl<S: RuntimeBrokerDurableStore> Drop for IssuedRuntimeBrokerAdmission<'_, S> {
    fn drop(&mut self) {
        if self.active {
            let _ = self.burn(RuntimeBrokerBurnReason::Dropped);
        }
    }
}

impl<'a, S: RuntimeBrokerDurableStore> ConsumingRuntimeBrokerAdmission<'a, S> {
    fn consume_and_transfer<B: RuntimeBrokerControllerTransferBackend>(
        mut self,
        backend: &mut B,
        held_source: &B::HeldSource,
    ) -> Result<
        ConsumedRuntimeBrokerAdmission<'a, S, B::TransferredSource>,
        RuntimeBrokerTransferError,
    > {
        let expectation = require_expectation(&self.expectation)?;
        require_capabilities(&self.capabilities)?.verify(expectation, true)?;
        if let Err(error) = persist_exact(
            require_store(&mut self.store)?,
            self.fatal,
            Some(RuntimeBrokerDurableState::Consuming),
            RuntimeBrokerDurableRecord::new(
                expectation,
                RuntimeBrokerDurableState::Consumed,
                None,
            )?,
        ) {
            self.active = false;
            return Err(error);
        }
        self.state = RuntimeBrokerDurableState::Consumed;
        let prerequisite = DurablyConsumedRuntimeBrokerTicket {
            ticket_digest_sha256: expectation.ticket_digest_sha256,
            durable_binding_sha256: expectation.durable_binding_sha256,
            live_binding_sha256: expectation.live_binding_sha256,
            containment_binding_sha256: expectation.containment_binding_sha256,
            external_six_binding_sha256: expectation.external_six_binding_sha256,
        };
        let capabilities = require_capabilities(&self.capabilities)?;
        let transferred = match backend.transfer_and_verify_same_object(
            &prerequisite,
            &capabilities.authenticated.live,
            &capabilities.containment,
            held_source,
        ) {
            Ok(value) => value,
            Err(error) => {
                return Err(self.fail_and_burn(RuntimeBrokerBurnReason::TransferFailed, error));
            }
        };
        if let Err(error) = transferred
            .verify_same_object(&prerequisite, &capabilities.authenticated.live)
            .and_then(|_| capabilities.verify(expectation, true))
        {
            drop(transferred);
            return Err(self.fail_and_burn(RuntimeBrokerBurnReason::TransferFailed, error));
        }
        let store = self.store.take().ok_or_else(missing_store)?;
        let expectation = self.expectation.take().ok_or_else(missing_expectation)?;
        let capabilities = self.capabilities.take().ok_or_else(missing_capabilities)?;
        self.active = false;
        Ok(ConsumedRuntimeBrokerAdmission {
            store: Some(store),
            fatal: self.fatal,
            expectation: Some(expectation),
            capabilities: Some(capabilities),
            transferred_source: Some(RuntimeBrokerTransferredControllerSource {
                inner: transferred,
            }),
            action_authority: Some(RuntimeBrokerActionAuthority { _private: () }),
            state: RuntimeBrokerDurableState::Consumed,
            active: true,
        })
    }

    fn fail_and_burn(
        &mut self,
        reason: RuntimeBrokerBurnReason,
        error: RuntimeBrokerTransferError,
    ) -> RuntimeBrokerTransferError {
        match self.burn(reason) {
            Ok(()) => error,
            Err(burn) => burn,
        }
    }

    fn burn(&mut self, reason: RuntimeBrokerBurnReason) -> Result<(), RuntimeBrokerTransferError> {
        let result = burn_owned(
            &mut self.store,
            self.fatal,
            self.expectation.as_ref(),
            self.state,
            reason,
        );
        self.active = false;
        result
    }
}

impl<S: RuntimeBrokerDurableStore> Drop for ConsumingRuntimeBrokerAdmission<'_, S> {
    fn drop(&mut self) {
        if self.active {
            let _ = self.burn(RuntimeBrokerBurnReason::Dropped);
        }
    }
}

impl<'a, S, T> ConsumedRuntimeBrokerAdmission<'a, S, T>
where
    S: RuntimeBrokerDurableStore,
    T: RuntimeBrokerRemoteControllerSourceLease,
{
    pub(crate) fn launch_once<B: RuntimeBrokerOneShotActionBackend<T>>(
        mut self,
        backend: &mut B,
    ) -> Result<B::Output, RuntimeBrokerTransferError> {
        let expectation = require_expectation(&self.expectation)?;
        let capabilities = require_capabilities(&self.capabilities)?;
        capabilities.verify(expectation, true)?;
        let prerequisite = DurablyConsumedRuntimeBrokerTicket {
            ticket_digest_sha256: expectation.ticket_digest_sha256,
            durable_binding_sha256: expectation.durable_binding_sha256,
            live_binding_sha256: expectation.live_binding_sha256,
            containment_binding_sha256: expectation.containment_binding_sha256,
            external_six_binding_sha256: expectation.external_six_binding_sha256,
        };
        let source = self.transferred_source.as_ref().ok_or_else(|| {
            RuntimeBrokerTransferError::fatal("authority_runtime_broker_transferred_source_missing")
        })?;
        source
            .inner
            .verify_same_object(&prerequisite, &capabilities.authenticated.live)?;
        let result = backend.launch_once(
            self.action_authority.as_mut().ok_or_else(|| {
                RuntimeBrokerTransferError::fatal(
                    "authority_runtime_broker_action_authority_missing",
                )
            })?,
            source,
            &capabilities.external_six,
            &capabilities.authenticated.live,
            &capabilities.containment,
        );
        let reason = if result.is_ok() {
            RuntimeBrokerBurnReason::ActionCompleted
        } else {
            RuntimeBrokerBurnReason::ActionFailed
        };
        if let Err(error) = self.burn(reason) {
            return Err(error);
        }
        result
    }

    fn burn(&mut self, reason: RuntimeBrokerBurnReason) -> Result<(), RuntimeBrokerTransferError> {
        let result = burn_owned(
            &mut self.store,
            self.fatal,
            self.expectation.as_ref(),
            self.state,
            reason,
        );
        self.active = false;
        result
    }
}

impl<S, T> Drop for ConsumedRuntimeBrokerAdmission<'_, S, T>
where
    S: RuntimeBrokerDurableStore,
    T: RuntimeBrokerRemoteControllerSourceLease,
{
    fn drop(&mut self) {
        if self.active {
            let _ = self.burn(RuntimeBrokerBurnReason::Dropped);
        }
    }
}

fn persist_exact<S: RuntimeBrokerDurableStore>(
    store: &mut S,
    fatal: &RuntimeBrokerFatalLatch,
    expected_prior: Option<RuntimeBrokerDurableState>,
    record: RuntimeBrokerDurableRecord,
) -> Result<(), RuntimeBrokerTransferError> {
    record.validate()?;
    if fatal.is_poisoned() || store.is_poisoned() {
        fatal.trip();
        return Err(RuntimeBrokerTransferError::fatal(
            "authority_runtime_broker_lane_poisoned",
        ));
    }
    if let Err(error) = store.compare_and_append_flush(expected_prior, &record) {
        return Err(poison_lane(store, fatal, error));
    }
    match store.readback(&record.ticket_digest_sha256) {
        Ok(Some(observed)) if observed == record => Ok(()),
        Ok(_) => Err(poison_lane(
            store,
            fatal,
            RuntimeBrokerTransferError::new("authority_runtime_broker_durable_readback_mismatch"),
        )),
        Err(error) => Err(poison_lane(store, fatal, error)),
    }
}

fn burn_owned<S: RuntimeBrokerDurableStore>(
    store: &mut Option<&mut S>,
    fatal: &RuntimeBrokerFatalLatch,
    expectation: Option<&RuntimeBrokerPrivateChannelExpectation>,
    state: RuntimeBrokerDurableState,
    reason: RuntimeBrokerBurnReason,
) -> Result<(), RuntimeBrokerTransferError> {
    let store = store.as_deref_mut().ok_or_else(|| {
        fatal.trip();
        RuntimeBrokerTransferError::fatal("authority_runtime_broker_store_missing")
    })?;
    let expectation = expectation.ok_or_else(|| {
        fatal.trip();
        RuntimeBrokerTransferError::fatal("authority_runtime_broker_expectation_missing")
    })?;
    persist_exact(
        store,
        fatal,
        Some(state),
        RuntimeBrokerDurableRecord::new(
            expectation,
            RuntimeBrokerDurableState::Burned,
            Some(reason),
        )?,
    )
}

fn poison_lane<S: RuntimeBrokerDurableStore>(
    store: &mut S,
    fatal: &RuntimeBrokerFatalLatch,
    error: RuntimeBrokerTransferError,
) -> RuntimeBrokerTransferError {
    fatal.trip();
    store.latch_poisoned();
    error.into_fatal()
}

fn require_store<'a, S: RuntimeBrokerDurableStore>(
    store: &'a mut Option<&mut S>,
) -> Result<&'a mut S, RuntimeBrokerTransferError> {
    store.as_deref_mut().ok_or_else(missing_store)
}

fn require_expectation(
    expectation: &Option<RuntimeBrokerPrivateChannelExpectation>,
) -> Result<&RuntimeBrokerPrivateChannelExpectation, RuntimeBrokerTransferError> {
    expectation.as_ref().ok_or_else(missing_expectation)
}

fn require_capabilities(
    capabilities: &Option<RuntimeBrokerLiveCapabilities>,
) -> Result<&RuntimeBrokerLiveCapabilities, RuntimeBrokerTransferError> {
    capabilities.as_ref().ok_or_else(missing_capabilities)
}

fn require_capabilities_mut(
    capabilities: &mut Option<RuntimeBrokerLiveCapabilities>,
) -> Result<&mut RuntimeBrokerLiveCapabilities, RuntimeBrokerTransferError> {
    capabilities.as_mut().ok_or_else(missing_capabilities)
}

fn missing_store() -> RuntimeBrokerTransferError {
    RuntimeBrokerTransferError::fatal("authority_runtime_broker_store_missing")
}

fn missing_expectation() -> RuntimeBrokerTransferError {
    RuntimeBrokerTransferError::fatal("authority_runtime_broker_expectation_missing")
}

fn missing_capabilities() -> RuntimeBrokerTransferError {
    RuntimeBrokerTransferError::fatal("authority_runtime_broker_live_capability_missing")
}

pub(crate) fn require_production_runtime_broker_bootstrap_receipt(
) -> Result<RuntimeBrokerBootstrapReceipt, RuntimeBrokerTransferError> {
    Err(RuntimeBrokerTransferError::new(
        BROKER_BOOTSTRAP_RECEIPT_BLOCKER,
    ))
}

pub(crate) fn require_production_runtime_broker_parent_lease(
) -> Result<HeldRuntimeBrokerParentLease, RuntimeBrokerTransferError> {
    Err(RuntimeBrokerTransferError::new(BROKER_PARENT_LEASE_BLOCKER))
}

pub(crate) fn require_production_runtime_broker_external_six(
) -> Result<OwnedExternalSixLaunchLease, RuntimeBrokerTransferError> {
    Err(RuntimeBrokerTransferError::new(BROKER_EXTERNAL_SIX_BLOCKER))
}

pub(crate) fn require_production_runtime_broker_containment(
) -> Result<RuntimeBrokerCrashContainmentLease, RuntimeBrokerTransferError> {
    Err(RuntimeBrokerTransferError::new(BROKER_CONTAINMENT_BLOCKER))
}

pub(crate) fn require_production_runtime_broker_durable_store(
) -> Result<(), RuntimeBrokerTransferError> {
    Err(RuntimeBrokerTransferError::new(
        BROKER_DURABLE_STORE_BLOCKER,
    ))
}

pub(crate) fn require_production_runtime_broker_private_channel(
) -> Result<(), RuntimeBrokerTransferError> {
    Err(RuntimeBrokerTransferError::new(
        BROKER_PRIVATE_CHANNEL_BLOCKER,
    ))
}

pub(crate) fn require_production_runtime_broker_controller_transfer(
) -> Result<(), RuntimeBrokerTransferError> {
    Err(RuntimeBrokerTransferError::new(
        BROKER_CONTROLLER_TRANSFER_BLOCKER,
    ))
}

fn endpoint_digest(endpoint_id: &[u8; ENDPOINT_ID_BYTES]) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-runtime-broker-endpoint-v2\0");
    digest.update(endpoint_id);
    digest.finalize().into()
}

fn constant_time_eq(left: &[u8; 32], right: &[u8; 32]) -> bool {
    let mut difference = 0u8;
    for (left, right) in left.iter().zip(right.iter()) {
        difference |= left ^ right;
    }
    difference == 0
}

fn take_array<const N: usize>(
    bytes: &[u8],
    offset: &mut usize,
) -> Result<[u8; N], RuntimeBrokerTransferError> {
    let end = offset.checked_add(N).ok_or_else(|| {
        RuntimeBrokerTransferError::new("authority_runtime_broker_invitation_invalid")
    })?;
    let slice = bytes.get(*offset..end).ok_or_else(|| {
        RuntimeBrokerTransferError::new("authority_runtime_broker_invitation_invalid")
    })?;
    let mut value = [0u8; N];
    value.copy_from_slice(slice);
    *offset = end;
    Ok(value)
}

fn volatile_zero(bytes: &mut [u8]) {
    for byte in bytes {
        unsafe {
            std::ptr::write_volatile(byte, 0);
        }
    }
    compiler_fence(Ordering::SeqCst);
}

fn is_zero(value: &[u8; 32]) -> bool {
    value.iter().all(|byte| *byte == 0)
}

fn is_zero_16(value: &[u8; 16]) -> bool {
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

#[cfg(test)]
fn test_digest(seed: u8) -> [u8; 32] {
    [seed.max(1); 32]
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{collections::BTreeMap, sync::Mutex};

    #[derive(Debug, Default)]
    struct RecordingStoreState {
        records: BTreeMap<[u8; 32], RuntimeBrokerDurableRecord>,
        trace: Vec<String>,
        poisoned: bool,
        fail_append: bool,
        fail_readback: bool,
        fail_containment: bool,
        remote_leases: Vec<Arc<AtomicBool>>,
    }

    #[derive(Debug, Clone, Default)]
    struct RecordingStore {
        shared: Arc<Mutex<RecordingStoreState>>,
    }

    impl RecordingStore {
        fn latest(&self) -> RuntimeBrokerDurableRecord {
            self.shared
                .lock()
                .unwrap()
                .records
                .values()
                .next()
                .unwrap()
                .clone()
        }

        fn trace(&self) -> Vec<String> {
            self.shared.lock().unwrap().trace.clone()
        }

        fn fail_next_append(&self) {
            self.shared.lock().unwrap().fail_append = true;
        }

        fn fail_next_readback(&self) {
            self.shared.lock().unwrap().fail_readback = true;
        }

        fn fail_next_containment(&self) {
            self.shared.lock().unwrap().fail_containment = true;
        }

        fn register_remote(&self, live: Arc<AtomicBool>) {
            self.shared.lock().unwrap().remote_leases.push(live);
        }

        fn insert_second_nonterminal_for_test(&self) {
            let mut state = self.shared.lock().unwrap();
            let first = state.records.values().next().unwrap().clone();
            let mut second = first;
            second.ticket_digest_sha256[0] ^= 0x55;
            second.record_sha256 = ZERO_DIGEST;
            second.record_sha256 = second.canonical_digest();
            state.records.insert(second.ticket_digest_sha256, second);
        }
    }

    impl RuntimeBrokerDurableStore for RecordingStore {
        fn compare_and_append_flush(
            &mut self,
            expected_prior: Option<RuntimeBrokerDurableState>,
            record: &RuntimeBrokerDurableRecord,
        ) -> Result<(), RuntimeBrokerTransferError> {
            record.validate()?;
            let mut state = self.shared.lock().unwrap();
            if state.poisoned {
                return Err(RuntimeBrokerTransferError::new(
                    "authority_runtime_broker_store_poisoned",
                ));
            }
            if state.fail_append {
                state.fail_append = false;
                return Err(RuntimeBrokerTransferError::new(
                    "authority_runtime_broker_test_append_failed",
                ));
            }
            let observed_prior = state
                .records
                .get(&record.ticket_digest_sha256)
                .map(|value| value.state);
            if observed_prior != expected_prior
                || (expected_prior.is_none() && record.state != RuntimeBrokerDurableState::Issued)
                || (expected_prior.is_some()
                    && record.state != RuntimeBrokerDurableState::Burned
                    && !matches!(
                        (expected_prior, record.state),
                        (
                            Some(RuntimeBrokerDurableState::Issued),
                            RuntimeBrokerDurableState::Consuming
                        ) | (
                            Some(RuntimeBrokerDurableState::Consuming),
                            RuntimeBrokerDurableState::Consumed
                        )
                    ))
            {
                return Err(RuntimeBrokerTransferError::new(
                    "authority_runtime_broker_durable_transition_invalid",
                ));
            }
            state.trace.push(format!("append:{:?}", record.state));
            state
                .records
                .insert(record.ticket_digest_sha256, record.clone());
            state.trace.push(format!("flush:{:?}", record.state));
            Ok(())
        }

        fn readback(
            &mut self,
            ticket_digest_sha256: &[u8; 32],
        ) -> Result<Option<RuntimeBrokerDurableRecord>, RuntimeBrokerTransferError> {
            let mut state = self.shared.lock().unwrap();
            if state.fail_readback {
                state.fail_readback = false;
                return Err(RuntimeBrokerTransferError::new(
                    "authority_runtime_broker_test_readback_failed",
                ));
            }
            let value = state.records.get(ticket_digest_sha256).cloned();
            if let Some(record) = value.as_ref() {
                state.trace.push(format!("readback:{:?}", record.state));
            }
            Ok(value)
        }

        fn nonterminal_records(
            &mut self,
            maximum: usize,
        ) -> Result<Vec<RuntimeBrokerDurableRecord>, RuntimeBrokerTransferError> {
            let state = self.shared.lock().unwrap();
            if state.poisoned {
                return Err(RuntimeBrokerTransferError::new(
                    "authority_runtime_broker_store_poisoned",
                ));
            }
            let values = state
                .records
                .values()
                .filter(|record| record.state != RuntimeBrokerDurableState::Burned)
                .cloned()
                .collect::<Vec<_>>();
            if values.len() > maximum {
                return Err(RuntimeBrokerTransferError::new(
                    "authority_runtime_broker_recovery_capacity_exceeded",
                ));
            }
            Ok(values)
        }

        fn contain_restart_peer(
            &mut self,
            record: &RuntimeBrokerDurableRecord,
        ) -> Result<(), RuntimeBrokerTransferError> {
            record.validate()?;
            let mut state = self.shared.lock().unwrap();
            if state.fail_containment {
                state.fail_containment = false;
                return Err(RuntimeBrokerTransferError::new(
                    "authority_runtime_broker_test_containment_failed",
                ));
            }
            for remote in &state.remote_leases {
                remote.store(false, Ordering::Release);
            }
            state.trace.push("contain:peer-terminal".to_string());
            Ok(())
        }

        fn latch_poisoned(&mut self) {
            self.shared.lock().unwrap().poisoned = true;
        }

        fn is_poisoned(&self) -> bool {
            self.shared.lock().unwrap().poisoned
        }
    }

    struct TestControls {
        peer: Arc<AtomicBool>,
        parent: Arc<AtomicBool>,
        external_six: Arc<AtomicBool>,
        containment: Arc<AtomicBool>,
        channel: Arc<AtomicBool>,
    }

    fn test_inputs(
        seed: u8,
    ) -> (
        AuthenticatedRuntimeBrokerBootstrapPeer,
        OwnedExternalSixLaunchLease,
        RuntimeBrokerCrashContainmentLease,
        TestControls,
    ) {
        let controls = TestControls {
            peer: Arc::new(AtomicBool::new(true)),
            parent: Arc::new(AtomicBool::new(true)),
            external_six: Arc::new(AtomicBool::new(true)),
            containment: Arc::new(AtomicBool::new(true)),
            channel: Arc::new(AtomicBool::new(true)),
        };
        let test_capability =
            TestRuntimeBrokerCapability::exact_test_fixture(seed, Arc::clone(&controls.peer));
        let live = test_capability.binding;
        let parent =
            HeldRuntimeBrokerParentLease::exact_test_fixture(seed, Arc::clone(&controls.parent));
        let receipt = RuntimeBrokerBootstrapReceipt::exact_test_fixture(seed, live, parent.binding);
        let epoch = RuntimeBrokerServiceEpoch::from_authenticated(&receipt, &live).unwrap();
        let external_six = OwnedExternalSixLaunchLease::exact_test_fixture(
            &epoch,
            seed,
            Arc::clone(&controls.external_six),
        );
        let containment = RuntimeBrokerCrashContainmentLease::exact_test_fixture(
            &live,
            &epoch,
            Arc::clone(&controls.containment),
        );
        let authenticated = AuthenticatedRuntimeBrokerBootstrapPeer::from_parts(
            receipt,
            RuntimeBrokerLiveCapability::Test(test_capability),
            parent,
        )
        .unwrap();
        (authenticated, external_six, containment, controls)
    }

    macro_rules! issued {
        ($seed:expr, $store:ident, $observer:ident, $fatal:ident, $lane:ident, $admission:ident, $frame:ident, $controls:ident) => {
            let mut $store = RecordingStore::default();
            let $observer = $store.clone();
            let $fatal = RuntimeBrokerFatalLatch::new();
            let mut $lane =
                recover_runtime_broker_admissions_before_accept(&mut $store, &$fatal).unwrap();
            let (authenticated, external_six, containment, $controls) = test_inputs($seed);
            let ($admission, $frame) = issue_runtime_broker_invitation(
                &mut $lane,
                authenticated,
                external_six,
                containment,
            )
            .unwrap();
        };
    }

    fn channel_for(
        presented: &PresentedRuntimeBrokerInvitation,
        controls: &TestControls,
    ) -> DedicatedRuntimeBrokerChannelLease {
        DedicatedRuntimeBrokerChannelLease::exact_test_fixture(
            presented.endpoint_id,
            presented.live_binding_sha256,
            Arc::clone(&controls.channel),
        )
    }

    struct RecordingRemoteSource {
        value: u64,
        live: Arc<AtomicBool>,
        durable_binding_sha256: [u8; 32],
        peer_binding_sha256: [u8; 32],
    }

    impl Drop for RecordingRemoteSource {
        fn drop(&mut self) {
            self.live.store(false, Ordering::Release);
        }
    }

    impl RuntimeBrokerRemoteControllerSourceLease for RecordingRemoteSource {
        fn verify_same_object(
            &self,
            prerequisite: &DurablyConsumedRuntimeBrokerTicket,
            peer: &RuntimeBrokerLiveCapability,
        ) -> Result<(), RuntimeBrokerTransferError> {
            if !self.live.load(Ordering::Acquire)
                || self.durable_binding_sha256 != prerequisite.durable_binding_sha256
                || self.peer_binding_sha256 != prerequisite.live_binding_sha256
                || peer.projection()?.binding_sha256 != self.peer_binding_sha256
            {
                return Err(RuntimeBrokerTransferError::new(
                    "authority_runtime_broker_remote_source_invalid",
                ));
            }
            Ok(())
        }
    }

    struct RecordingTransferBackend {
        store: RecordingStore,
        fail_after_duplicate: bool,
        calls: usize,
        last_remote: Option<Arc<AtomicBool>>,
    }

    impl RuntimeBrokerControllerTransferBackend for RecordingTransferBackend {
        type HeldSource = u64;
        type TransferredSource = RecordingRemoteSource;

        fn transfer_and_verify_same_object(
            &mut self,
            prerequisite: &DurablyConsumedRuntimeBrokerTicket,
            peer: &RuntimeBrokerLiveCapability,
            containment: &RuntimeBrokerCrashContainmentLease,
            held_source: &Self::HeldSource,
        ) -> Result<Self::TransferredSource, RuntimeBrokerTransferError> {
            self.calls += 1;
            let latest = self.store.latest();
            let projection = peer.projection()?;
            if latest.state != RuntimeBrokerDurableState::Consumed
                || latest.ticket_digest_sha256 != prerequisite.ticket_digest_sha256
                || latest.durable_binding_sha256 != prerequisite.durable_binding_sha256
                || latest.live_binding.binding_sha256 != prerequisite.live_binding_sha256
                || latest.containment_binding_sha256 != prerequisite.containment_binding_sha256
                || latest.external_six_binding_sha256 != prerequisite.external_six_binding_sha256
                || containment.binding_sha256 != prerequisite.containment_binding_sha256
            {
                return Err(RuntimeBrokerTransferError::new(
                    "authority_runtime_broker_transfer_before_durable_consume",
                ));
            }
            let live = Arc::new(AtomicBool::new(true));
            self.store.register_remote(Arc::clone(&live));
            self.last_remote = Some(Arc::clone(&live));
            if self.fail_after_duplicate {
                live.store(false, Ordering::Release);
                return Err(RuntimeBrokerTransferError::new(
                    "authority_runtime_broker_test_transfer_failed",
                ));
            }
            Ok(RecordingRemoteSource {
                value: held_source + 1,
                live,
                durable_binding_sha256: prerequisite.durable_binding_sha256,
                peer_binding_sha256: projection.binding_sha256,
            })
        }
    }

    #[derive(Default)]
    struct RecordingActionBackend {
        calls: usize,
        fail: bool,
    }

    impl RuntimeBrokerOneShotActionBackend<RecordingRemoteSource> for RecordingActionBackend {
        type Output = u64;

        fn launch_once(
            &mut self,
            _authority: &mut RuntimeBrokerActionAuthority,
            source: &RuntimeBrokerTransferredControllerSource<RecordingRemoteSource>,
            external_six: &OwnedExternalSixLaunchLease,
            peer: &RuntimeBrokerLiveCapability,
            containment: &RuntimeBrokerCrashContainmentLease,
        ) -> Result<Self::Output, RuntimeBrokerTransferError> {
            self.calls += 1;
            peer.revalidate()?;
            if !source.inner.live.load(Ordering::Acquire)
                || is_zero(external_six.admitted.binding().binding_sha256())
                || is_zero(&containment.binding_sha256)
            {
                return Err(RuntimeBrokerTransferError::new(
                    "authority_runtime_broker_test_action_invalid",
                ));
            }
            if self.fail {
                return Err(RuntimeBrokerTransferError::new(
                    "authority_runtime_broker_test_action_failed",
                ));
            }
            Ok(source.inner.value)
        }
    }

    #[test]
    fn production_dependencies_remain_explicitly_closed() {
        assert_eq!(
            require_production_runtime_broker_bootstrap_receipt()
                .unwrap_err()
                .code(),
            BROKER_BOOTSTRAP_RECEIPT_BLOCKER
        );
        assert_eq!(
            require_production_runtime_broker_parent_lease()
                .err()
                .unwrap()
                .code(),
            BROKER_PARENT_LEASE_BLOCKER
        );
        assert_eq!(
            require_production_runtime_broker_external_six()
                .err()
                .unwrap()
                .code(),
            BROKER_EXTERNAL_SIX_BLOCKER
        );
        assert_eq!(
            require_production_runtime_broker_containment()
                .err()
                .unwrap()
                .code(),
            BROKER_CONTAINMENT_BLOCKER
        );
        assert_eq!(
            require_production_runtime_broker_durable_store()
                .unwrap_err()
                .code(),
            BROKER_DURABLE_STORE_BLOCKER
        );
        assert_eq!(
            require_production_runtime_broker_private_channel()
                .unwrap_err()
                .code(),
            BROKER_PRIVATE_CHANNEL_BLOCKER
        );
        assert_eq!(
            require_production_runtime_broker_controller_transfer()
                .unwrap_err()
                .code(),
            BROKER_CONTROLLER_TRANSFER_BLOCKER
        );
    }

    #[test]
    fn sensitive_values_are_redacted_and_use_volatile_zeroing() {
        let secret = RuntimeBrokerTicketSecret::exact_test_fixture(7);
        assert_eq!(
            format!("{secret:?}"),
            "RuntimeBrokerTicketSecret(<redacted>)"
        );
        issued!(8, _store, _observer, _fatal, _lane, admission, frame, _controls);
        assert_eq!(
            format!("{frame:?}"),
            "SensitiveRuntimeBrokerInvitationFrame(<redacted>)"
        );
        assert_eq!(frame.as_bytes_for_test().len(), INVITATION_FRAME_BYTES);
        let source = include_str!("primitive_evidence_runtime_broker_transfer_windows.rs");
        let production = source.split("#[cfg(test)]\nmod tests {").next().unwrap();
        assert!(production.contains("std::ptr::write_volatile"));
        assert!(!production.contains("pub(crate) fn as_bytes"));
        drop(admission);
    }

    #[test]
    fn issued_is_cas_flushed_and_read_back_before_return() {
        issued!(9, _store, observer, _fatal, _lane, admission, _frame, _controls);
        assert_eq!(observer.latest().state, RuntimeBrokerDurableState::Issued);
        assert_eq!(
            observer.trace(),
            vec!["append:Issued", "flush:Issued", "readback:Issued"]
        );
        drop(admission);
    }

    #[test]
    fn dedicated_channel_revalidates_all_live_owners() {
        issued!(10, _store, observer, _fatal, _lane, admission, frame, controls);
        let presented = frame.parse_for_test().unwrap();
        let channel = channel_for(&presented, &controls);
        let consuming = admission
            .authenticate_dedicated_channel(presented, channel)
            .unwrap();
        assert_eq!(
            observer.latest().state,
            RuntimeBrokerDurableState::Consuming
        );
        drop(consuming);
        assert_eq!(observer.latest().state, RuntimeBrokerDurableState::Burned);
    }

    fn assert_tamper_burns(seed: u8, offset: usize) {
        issued!(seed, _store, observer, _fatal, _lane, admission, frame, controls);
        let mut frame = frame;
        frame.tamper_for_test(offset);
        match frame.parse_for_test() {
            Ok(presented) => {
                let channel = channel_for(&presented, &controls);
                assert!(admission
                    .authenticate_dedicated_channel(presented, channel)
                    .is_err());
            }
            Err(_) => drop(admission),
        }
        assert_eq!(observer.latest().state, RuntimeBrokerDurableState::Burned);
    }

    #[test]
    fn every_invitation_binding_field_tamper_burns_the_ticket() {
        let header = INVITATION_MAGIC.len() + 2;
        let ticket = header + ENDPOINT_ID_BYTES;
        let digests = ticket + 32;
        for (index, offset) in std::iter::once(header)
            .chain(std::iter::once(ticket))
            .chain((0..INVITATION_DIGEST_FIELD_COUNT).map(|index| digests + index * 32))
            .enumerate()
        {
            assert_tamper_burns(20 + index as u8, offset);
        }
    }

    #[test]
    fn live_owner_loss_fails_closed_before_transfer() {
        for selector in 0..5 {
            issued!(
                40 + selector,
                _store,
                observer,
                _fatal,
                _lane,
                admission,
                frame,
                controls
            );
            match selector {
                0 => controls.peer.store(false, Ordering::Release),
                1 => controls.parent.store(false, Ordering::Release),
                2 => controls.external_six.store(false, Ordering::Release),
                3 => controls.containment.store(false, Ordering::Release),
                _ => controls.channel.store(false, Ordering::Release),
            }
            let presented = frame.parse_for_test().unwrap();
            let channel = channel_for(&presented, &controls);
            assert!(admission
                .authenticate_dedicated_channel(presented, channel)
                .is_err());
            assert_eq!(observer.latest().state, RuntimeBrokerDurableState::Burned);
        }
    }

    #[test]
    fn consumed_precedes_transfer_and_launch_once_burns_completed() {
        issued!(51, _store, observer, _fatal, _lane, admission, frame, controls);
        let presented = frame.parse_for_test().unwrap();
        let channel = channel_for(&presented, &controls);
        let consuming = admission
            .authenticate_dedicated_channel(presented, channel)
            .unwrap();
        let mut transfer = RecordingTransferBackend {
            store: observer.clone(),
            fail_after_duplicate: false,
            calls: 0,
            last_remote: None,
        };
        let consumed = consuming.consume_and_transfer(&mut transfer, &41).unwrap();
        assert_eq!(transfer.calls, 1);
        assert_eq!(observer.latest().state, RuntimeBrokerDurableState::Consumed);
        let mut action = RecordingActionBackend::default();
        assert_eq!(consumed.launch_once(&mut action).unwrap(), 42);
        assert_eq!(action.calls, 1);
        let latest = observer.latest();
        assert_eq!(latest.state, RuntimeBrokerDurableState::Burned);
        assert_eq!(
            latest.burn_reason,
            Some(RuntimeBrokerBurnReason::ActionCompleted)
        );
        let source = include_str!("primitive_evidence_runtime_broker_transfer_windows.rs");
        let production = source.split("#[cfg(test)]\nmod tests {").next().unwrap();
        assert!(!production.contains("pub(crate) fn action_authority(&self)"));
        assert!(!production.contains("pub(crate) fn transferred_source(&self)"));
    }

    #[test]
    fn duplicate_failure_closes_remote_and_burns() {
        issued!(52, _store, observer, _fatal, _lane, admission, frame, controls);
        let presented = frame.parse_for_test().unwrap();
        let channel = channel_for(&presented, &controls);
        let consuming = admission
            .authenticate_dedicated_channel(presented, channel)
            .unwrap();
        let mut transfer = RecordingTransferBackend {
            store: observer.clone(),
            fail_after_duplicate: true,
            calls: 0,
            last_remote: None,
        };
        assert!(consuming.consume_and_transfer(&mut transfer, &41).is_err());
        assert!(!transfer
            .last_remote
            .as_ref()
            .unwrap()
            .load(Ordering::Acquire));
        assert_eq!(observer.latest().state, RuntimeBrokerDurableState::Burned);
    }

    #[test]
    fn peer_exit_after_private_channel_burns_before_transfer_backend() {
        issued!(61, _store, observer, _fatal, _lane, admission, frame, controls);
        let presented = frame.parse_for_test().unwrap();
        let channel = channel_for(&presented, &controls);
        let consuming = admission
            .authenticate_dedicated_channel(presented, channel)
            .unwrap();
        controls.peer.store(false, Ordering::Release);
        let mut transfer = RecordingTransferBackend {
            store: observer.clone(),
            fail_after_duplicate: false,
            calls: 0,
            last_remote: None,
        };
        assert!(consuming.consume_and_transfer(&mut transfer, &41).is_err());
        assert_eq!(transfer.calls, 0);
        assert_eq!(observer.latest().state, RuntimeBrokerDurableState::Burned);
    }

    #[test]
    fn failed_one_shot_action_burns_and_drops_remote_lease() {
        issued!(62, _store, observer, _fatal, _lane, admission, frame, controls);
        let presented = frame.parse_for_test().unwrap();
        let channel = channel_for(&presented, &controls);
        let consuming = admission
            .authenticate_dedicated_channel(presented, channel)
            .unwrap();
        let mut transfer = RecordingTransferBackend {
            store: observer.clone(),
            fail_after_duplicate: false,
            calls: 0,
            last_remote: None,
        };
        let consumed = consuming.consume_and_transfer(&mut transfer, &41).unwrap();
        let remote = Arc::clone(transfer.last_remote.as_ref().unwrap());
        let mut action = RecordingActionBackend {
            calls: 0,
            fail: true,
        };
        assert!(consumed.launch_once(&mut action).is_err());
        assert_eq!(action.calls, 1);
        assert!(!remote.load(Ordering::Acquire));
        let latest = observer.latest();
        assert_eq!(latest.state, RuntimeBrokerDurableState::Burned);
        assert_eq!(
            latest.burn_reason,
            Some(RuntimeBrokerBurnReason::ActionFailed)
        );
    }

    #[test]
    fn restart_contains_remote_before_burning_consumed() {
        issued!(53, store, observer, fatal, lane, admission, frame, controls);
        let presented = frame.parse_for_test().unwrap();
        let channel = channel_for(&presented, &controls);
        let consuming = admission
            .authenticate_dedicated_channel(presented, channel)
            .unwrap();
        let mut transfer = RecordingTransferBackend {
            store: observer.clone(),
            fail_after_duplicate: false,
            calls: 0,
            last_remote: None,
        };
        let consumed = consuming.consume_and_transfer(&mut transfer, &41).unwrap();
        let remote = Arc::clone(transfer.last_remote.as_ref().unwrap());
        std::mem::forget(consumed);
        drop(lane);
        assert!(remote.load(Ordering::Acquire));
        let recovered =
            recover_runtime_broker_admissions_before_accept(&mut store, &fatal).unwrap();
        assert!(!remote.load(Ordering::Acquire));
        let latest = observer.latest();
        assert_eq!(latest.state, RuntimeBrokerDurableState::Burned);
        assert_eq!(
            latest.burn_reason,
            Some(RuntimeBrokerBurnReason::RestartRecovery)
        );
        let trace = observer.trace();
        let containment_index = trace
            .iter()
            .position(|event| event == "contain:peer-terminal")
            .unwrap();
        let burned_index = trace
            .iter()
            .position(|event| event == "append:Burned")
            .unwrap();
        assert!(containment_index < burned_index);
        drop(recovered);
    }

    #[test]
    fn restart_burns_issued_and_consuming_not_only_one_stage() {
        issued!(54, store, observer, fatal, lane, admission, _frame, _controls);
        std::mem::forget(admission);
        drop(lane);
        drop(recover_runtime_broker_admissions_before_accept(&mut store, &fatal).unwrap());
        let issued_latest = observer.latest();
        assert_eq!(issued_latest.state, RuntimeBrokerDurableState::Burned);
        assert_eq!(
            issued_latest.burn_reason,
            Some(RuntimeBrokerBurnReason::RestartRecovery)
        );

        issued!(55, store2, observer2, fatal2, lane2, admission2, frame2, controls2);
        let presented = frame2.parse_for_test().unwrap();
        let channel = channel_for(&presented, &controls2);
        let consuming = admission2
            .authenticate_dedicated_channel(presented, channel)
            .unwrap();
        std::mem::forget(consuming);
        drop(lane2);
        drop(recover_runtime_broker_admissions_before_accept(&mut store2, &fatal2).unwrap());
        let consuming_latest = observer2.latest();
        assert_eq!(consuming_latest.state, RuntimeBrokerDurableState::Burned);
        assert_eq!(
            consuming_latest.burn_reason,
            Some(RuntimeBrokerBurnReason::RestartRecovery)
        );
    }

    #[test]
    fn store_and_containment_failures_trip_global_fatal_latch() {
        let mut store = RecordingStore::default();
        let observer = store.clone();
        observer.fail_next_append();
        let fatal = RuntimeBrokerFatalLatch::new();
        let mut lane = recover_runtime_broker_admissions_before_accept(&mut store, &fatal).unwrap();
        let (authenticated, external, containment, _controls) = test_inputs(56);
        let error =
            issue_runtime_broker_invitation(&mut lane, authenticated, external, containment)
                .err()
                .unwrap();
        assert!(error.is_fatal());
        assert!(fatal.is_poisoned());
        assert!(observer.is_poisoned());

        issued!(57, store2, observer2, fatal2, lane2, admission2, _frame2, _controls2);
        std::mem::forget(admission2);
        drop(lane2);
        observer2.fail_next_containment();
        assert!(recover_runtime_broker_admissions_before_accept(&mut store2, &fatal2).is_err());
        assert!(fatal2.is_poisoned());
        assert!(observer2.is_poisoned());
    }

    #[test]
    fn recovery_capacity_is_bounded_and_poisoned() {
        issued!(58, store, observer, fatal, lane, admission, _frame, _controls);
        std::mem::forget(admission);
        drop(lane);
        observer.insert_second_nonterminal_for_test();
        let error = recover_runtime_broker_admissions_before_accept(&mut store, &fatal)
            .err()
            .unwrap();
        assert!(error.is_fatal());
        assert!(fatal.is_poisoned());
        assert!(observer.is_poisoned());
    }

    #[test]
    fn readback_and_burn_failures_are_fatal_not_silently_dropped() {
        let mut store = RecordingStore::default();
        let observer = store.clone();
        observer.fail_next_readback();
        let fatal = RuntimeBrokerFatalLatch::new();
        let mut lane = recover_runtime_broker_admissions_before_accept(&mut store, &fatal).unwrap();
        let (authenticated, external, containment, _controls) = test_inputs(59);
        assert!(
            issue_runtime_broker_invitation(&mut lane, authenticated, external, containment)
                .is_err()
        );
        assert!(fatal.is_poisoned());

        issued!(60, _store2, observer2, fatal2, _lane2, admission2, _frame2, _controls2);
        observer2.fail_next_append();
        drop(admission2);
        assert!(fatal2.is_poisoned());
        assert!(observer2.is_poisoned());
    }
}
