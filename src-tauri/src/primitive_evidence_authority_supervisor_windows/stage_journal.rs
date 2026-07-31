use sha2::{Digest as _, Sha256};
use std::fmt;

pub(crate) type StageDigest = [u8; 32];

const JOURNAL_MAGIC: &[u8; 8] = b"VRCSTG01";
const JOURNAL_VERSION: u16 = 2;
const HEADER_LEN: usize = 304;
const RECORD_MAGIC: &[u8; 4] = b"SGR1";
const RECORD_VERSION: u8 = 1;
const RECORD_LEN: usize = 236;
const RECORD_DIGEST_OFFSET: usize = 204;
const BINDING_DOMAIN: &[u8] = b"vrcforge-stage-journal-binding-v2\0";
const RECORD_DOMAIN: &[u8] = b"vrcforge-stage-journal-record-v1\0";
const STAGE_ACTION_INTENT_DOMAIN: &[u8] = b"vrcforge-native-stage-action-intent-v1\0";

pub(crate) const STAGE_JOURNAL_MAX_RECORDS: usize = 32;
pub(crate) const STAGE_JOURNAL_MAX_BYTES: usize =
    HEADER_LEN + (STAGE_JOURNAL_MAX_RECORDS * RECORD_LEN);

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct StageJournalBinding {
    authority_generation_digest: StageDigest,
    service_instance_digest: StageDigest,
    ticket_digest: StageDigest,
    run_binding_digest: StageDigest,
    prepared_receipt_digest: StageDigest,
    policy_snapshot_digest: StageDigest,
    recovery_bundle_digest: StageDigest,
    start_contract_digest: StageDigest,
}

impl StageJournalBinding {
    pub(crate) fn new(
        authority_generation_digest: StageDigest,
        service_instance_digest: StageDigest,
        ticket_digest: StageDigest,
        run_binding_digest: StageDigest,
        prepared_receipt_digest: StageDigest,
        policy_snapshot_digest: StageDigest,
        recovery_bundle_digest: StageDigest,
        start_contract_digest: StageDigest,
    ) -> Result<Self, StageJournalError> {
        let binding = Self {
            authority_generation_digest,
            service_instance_digest,
            ticket_digest,
            run_binding_digest,
            prepared_receipt_digest,
            policy_snapshot_digest,
            recovery_bundle_digest,
            start_contract_digest,
        };
        binding.validate()?;
        Ok(binding)
    }

    pub(crate) fn authority_generation_digest(&self) -> StageDigest {
        self.authority_generation_digest
    }

    pub(crate) fn service_instance_digest(&self) -> StageDigest {
        self.service_instance_digest
    }

    pub(crate) fn ticket_digest(&self) -> StageDigest {
        self.ticket_digest
    }

    pub(crate) fn run_binding_digest(&self) -> StageDigest {
        self.run_binding_digest
    }

    pub(crate) fn prepared_receipt_digest(&self) -> StageDigest {
        self.prepared_receipt_digest
    }

    pub(crate) fn policy_snapshot_digest(&self) -> StageDigest {
        self.policy_snapshot_digest
    }

    pub(crate) fn recovery_bundle_digest(&self) -> StageDigest {
        self.recovery_bundle_digest
    }

    pub(crate) fn start_contract_digest(&self) -> StageDigest {
        self.start_contract_digest
    }

    pub(crate) fn digest(&self) -> StageDigest {
        hash_parts(
            BINDING_DOMAIN,
            &[
                &self.authority_generation_digest,
                &self.service_instance_digest,
                &self.ticket_digest,
                &self.run_binding_digest,
                &self.prepared_receipt_digest,
                &self.policy_snapshot_digest,
                &self.recovery_bundle_digest,
                &self.start_contract_digest,
            ],
        )
    }

    fn validate(&self) -> Result<(), StageJournalError> {
        if is_zero_digest(&self.authority_generation_digest)
            || is_zero_digest(&self.service_instance_digest)
            || is_zero_digest(&self.ticket_digest)
            || is_zero_digest(&self.run_binding_digest)
            || is_zero_digest(&self.prepared_receipt_digest)
            || is_zero_digest(&self.policy_snapshot_digest)
            || is_zero_digest(&self.recovery_bundle_digest)
            || is_zero_digest(&self.start_contract_digest)
        {
            return fail("stage_journal_binding_digest_zero");
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum StageAction {
    Prepare,
    BridgeCreate,
    BridgeResume,
    DriverCreate,
    DriverResume,
    Arm,
}

impl StageAction {
    pub(crate) const ORDERED: [Self; 6] = [
        Self::Prepare,
        Self::BridgeCreate,
        Self::BridgeResume,
        Self::DriverCreate,
        Self::DriverResume,
        Self::Arm,
    ];

    fn code(self) -> u8 {
        match self {
            Self::Prepare => 1,
            Self::BridgeCreate => 2,
            Self::BridgeResume => 3,
            Self::DriverCreate => 4,
            Self::DriverResume => 5,
            Self::Arm => 6,
        }
    }

    fn from_code(code: u8) -> Result<Option<Self>, StageJournalError> {
        match code {
            0 => Ok(None),
            1 => Ok(Some(Self::Prepare)),
            2 => Ok(Some(Self::BridgeCreate)),
            3 => Ok(Some(Self::BridgeResume)),
            4 => Ok(Some(Self::DriverCreate)),
            5 => Ok(Some(Self::DriverResume)),
            6 => Ok(Some(Self::Arm)),
            _ => fail("stage_journal_record_action_unknown"),
        }
    }

    fn next(self) -> Option<Self> {
        match self {
            Self::Prepare => Some(Self::BridgeCreate),
            Self::BridgeCreate => Some(Self::BridgeResume),
            Self::BridgeResume => Some(Self::DriverCreate),
            Self::DriverCreate => Some(Self::DriverResume),
            Self::DriverResume => Some(Self::Arm),
            Self::Arm => None,
        }
    }

    fn index(self) -> usize {
        usize::from(self.code() - 1)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum StageTerminationKind {
    Cancelled,
    TimedOut,
}

impl StageTerminationKind {
    fn code(self) -> u8 {
        match self {
            Self::Cancelled => 1,
            Self::TimedOut => 2,
        }
    }

    fn from_code(code: u8) -> Result<Option<Self>, StageJournalError> {
        match code {
            0 => Ok(None),
            1 => Ok(Some(Self::Cancelled)),
            2 => Ok(Some(Self::TimedOut)),
            _ => fail("stage_journal_record_termination_unknown"),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum StageJournalRecordKind {
    RunDeclared,
    ActionIntent,
    ActionObserved,
    TerminationIntent,
    Terminal,
    Cleanup,
}

impl StageJournalRecordKind {
    fn code(self) -> u8 {
        match self {
            Self::RunDeclared => 1,
            Self::ActionIntent => 2,
            Self::ActionObserved => 3,
            Self::TerminationIntent => 4,
            Self::Terminal => 5,
            Self::Cleanup => 6,
        }
    }

    fn from_code(code: u8) -> Result<Self, StageJournalError> {
        match code {
            1 => Ok(Self::RunDeclared),
            2 => Ok(Self::ActionIntent),
            3 => Ok(Self::ActionObserved),
            4 => Ok(Self::TerminationIntent),
            5 => Ok(Self::Terminal),
            6 => Ok(Self::Cleanup),
            _ => fail("stage_journal_record_kind_unknown"),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct StageJournalHead {
    pub(crate) sequence: u64,
    pub(crate) record_digest: StageDigest,
    pub(crate) kind: StageJournalRecordKind,
    pub(crate) action: Option<StageAction>,
    pub(crate) termination_kind: Option<StageTerminationKind>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum StageJournalReplayStage {
    Ready,
    Pending,
    Armed,
    Terminating,
    Terminal,
    Cleaned,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct StageActionReplayCommitment {
    action: StageAction,
    intent_sequence: u64,
    intent_parent_sequence: u64,
    intent_parent_digest: StageDigest,
    intent_payload_digest: StageDigest,
    intent_record_digest: StageDigest,
    observed_sequence: Option<u64>,
    observed_parent_sequence: Option<u64>,
    observed_parent_digest: Option<StageDigest>,
    observed_payload_digest: Option<StageDigest>,
    observed_record_digest: Option<StageDigest>,
}

impl StageActionReplayCommitment {
    pub(crate) fn action(self) -> StageAction {
        self.action
    }

    pub(crate) fn intent_sequence(self) -> u64 {
        self.intent_sequence
    }

    pub(crate) fn intent_parent_sequence(self) -> u64 {
        self.intent_parent_sequence
    }

    pub(crate) fn intent_parent_digest(self) -> StageDigest {
        self.intent_parent_digest
    }

    pub(crate) fn intent_payload_digest(self) -> StageDigest {
        self.intent_payload_digest
    }

    pub(crate) fn intent_record_digest(self) -> StageDigest {
        self.intent_record_digest
    }

    pub(crate) fn observed_sequence(self) -> Option<u64> {
        self.observed_sequence
    }

    pub(crate) fn observed_parent_sequence(self) -> Option<u64> {
        self.observed_parent_sequence
    }

    pub(crate) fn observed_parent_digest(self) -> Option<StageDigest> {
        self.observed_parent_digest
    }

    pub(crate) fn observed_payload_digest(self) -> Option<StageDigest> {
        self.observed_payload_digest
    }

    pub(crate) fn observed_record_digest(self) -> Option<StageDigest> {
        self.observed_record_digest
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct StageTerminationReplayCommitment {
    kind: StageTerminationKind,
    branch_head_sequence: u64,
    branch_head_digest: StageDigest,
    intent_sequence: u64,
    intent_record_digest: StageDigest,
    requested_at_unix_ms: u64,
    recorded_at_unix_ms: u64,
}

impl StageTerminationReplayCommitment {
    pub(crate) fn kind(self) -> StageTerminationKind {
        self.kind
    }

    pub(crate) fn branch_head_sequence(self) -> u64 {
        self.branch_head_sequence
    }

    pub(crate) fn branch_head_digest(self) -> StageDigest {
        self.branch_head_digest
    }

    pub(crate) fn intent_sequence(self) -> u64 {
        self.intent_sequence
    }

    pub(crate) fn intent_record_digest(self) -> StageDigest {
        self.intent_record_digest
    }

    pub(crate) fn requested_at_unix_ms(self) -> u64 {
        self.requested_at_unix_ms
    }

    pub(crate) fn recorded_at_unix_ms(self) -> u64 {
        self.recorded_at_unix_ms
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct VerifiedNormalTerminationMaterial {
    termination_kind: StageTerminationKind,
    branch_head_sequence: u64,
    branch_head_digest: StageDigest,
    intent_sequence: u64,
    intent_record_digest: StageDigest,
    requested_at_unix_ms: u64,
    recorded_at_unix_ms: u64,
    armed_receipt_digest: Option<StageDigest>,
    terminal_payload_digest: StageDigest,
    cleanup_payload_digest: StageDigest,
}

impl VerifiedNormalTerminationMaterial {
    pub(crate) fn termination_kind(self) -> StageTerminationKind {
        self.termination_kind
    }

    pub(crate) fn branch_head_sequence(self) -> u64 {
        self.branch_head_sequence
    }

    pub(crate) fn branch_head_digest(self) -> StageDigest {
        self.branch_head_digest
    }

    pub(crate) fn intent_sequence(self) -> u64 {
        self.intent_sequence
    }

    pub(crate) fn intent_record_digest(self) -> StageDigest {
        self.intent_record_digest
    }

    pub(crate) fn requested_at_unix_ms(self) -> u64 {
        self.requested_at_unix_ms
    }

    pub(crate) fn recorded_at_unix_ms(self) -> u64 {
        self.recorded_at_unix_ms
    }

    pub(crate) fn armed_receipt_digest(self) -> Option<StageDigest> {
        self.armed_receipt_digest
    }

    pub(crate) fn terminal_payload_digest(self) -> StageDigest {
        self.terminal_payload_digest
    }

    pub(crate) fn cleanup_payload_digest(self) -> StageDigest {
        self.cleanup_payload_digest
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum NormalTerminationPendingReason {
    NotTerminating,
    ObservationPending,
    TerminationIncomplete,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct NormalTerminationPending {
    stage: StageJournalReplayStage,
    reason: NormalTerminationPendingReason,
    pending_observation: Option<StageAction>,
}

impl NormalTerminationPending {
    pub(crate) fn stage(self) -> StageJournalReplayStage {
        self.stage
    }

    pub(crate) fn reason(self) -> NormalTerminationPendingReason {
        self.reason
    }

    pub(crate) fn pending_observation(self) -> Option<StageAction> {
        self.pending_observation
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum VerifiedNormalTerminationReplay {
    Complete(VerifiedNormalTerminationMaterial),
    Pending(NormalTerminationPending),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct VerifiedStageJournalReplay {
    stage: StageJournalReplayStage,
    next_action: Option<StageAction>,
    pending_observation: Option<StageAction>,
    termination_kind: Option<StageTerminationKind>,
    head_sequence: u64,
    head_digest: StageDigest,
    armed_receipt_digest: Option<StageDigest>,
    terminal_payload_digest: Option<StageDigest>,
    cleanup_payload_digest: Option<StageDigest>,
    declaration_digest: StageDigest,
    action_commitments: [Option<StageActionReplayCommitment>; StageAction::ORDERED.len()],
    termination_commitment: Option<StageTerminationReplayCommitment>,
}

impl VerifiedStageJournalReplay {
    pub(crate) fn stage(self) -> StageJournalReplayStage {
        self.stage
    }

    pub(crate) fn next_action(self) -> Option<StageAction> {
        self.next_action
    }

    pub(crate) fn pending_observation(self) -> Option<StageAction> {
        self.pending_observation
    }

    pub(crate) fn termination_kind(self) -> Option<StageTerminationKind> {
        self.termination_kind
    }

    pub(crate) fn head_sequence(self) -> u64 {
        self.head_sequence
    }

    pub(crate) fn head_digest(self) -> StageDigest {
        self.head_digest
    }

    pub(crate) fn armed_receipt_digest(self) -> Option<StageDigest> {
        self.armed_receipt_digest
    }

    pub(crate) fn terminal_payload_digest(self) -> Option<StageDigest> {
        self.terminal_payload_digest
    }

    pub(crate) fn cleanup_payload_digest(self) -> Option<StageDigest> {
        self.cleanup_payload_digest
    }

    pub(crate) fn declaration_digest(self) -> StageDigest {
        self.declaration_digest
    }

    pub(crate) fn action_commitment(
        self,
        action: StageAction,
    ) -> Option<StageActionReplayCommitment> {
        self.action_commitments[action.index()]
    }

    pub(crate) fn termination_commitment(self) -> Option<StageTerminationReplayCommitment> {
        self.termination_commitment
    }

    pub(crate) fn normal_termination_material(self) -> VerifiedNormalTerminationReplay {
        if self.pending_observation.is_some() {
            return VerifiedNormalTerminationReplay::Pending(NormalTerminationPending {
                stage: self.stage,
                reason: NormalTerminationPendingReason::ObservationPending,
                pending_observation: self.pending_observation,
            });
        }

        if self.stage != StageJournalReplayStage::Cleaned {
            let reason = if self.termination_commitment.is_some() {
                NormalTerminationPendingReason::TerminationIncomplete
            } else {
                NormalTerminationPendingReason::NotTerminating
            };
            return VerifiedNormalTerminationReplay::Pending(NormalTerminationPending {
                stage: self.stage,
                reason,
                pending_observation: None,
            });
        }

        let termination = self
            .termination_commitment
            .expect("a canonical cleaned replay has a termination commitment");
        VerifiedNormalTerminationReplay::Complete(VerifiedNormalTerminationMaterial {
            termination_kind: termination.kind,
            branch_head_sequence: termination.branch_head_sequence,
            branch_head_digest: termination.branch_head_digest,
            intent_sequence: termination.intent_sequence,
            intent_record_digest: termination.intent_record_digest,
            requested_at_unix_ms: termination.requested_at_unix_ms,
            recorded_at_unix_ms: termination.recorded_at_unix_ms,
            armed_receipt_digest: self.armed_receipt_digest,
            terminal_payload_digest: self
                .terminal_payload_digest
                .expect("a canonical cleaned replay has a terminal payload"),
            cleanup_payload_digest: self
                .cleanup_payload_digest
                .expect("a canonical cleaned replay has a cleanup payload"),
        })
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct StageJournalAppend {
    prior_byte_len: usize,
    prior_head_sequence: u64,
    prior_head_digest: StageDigest,
    record_bytes: [u8; RECORD_LEN],
}

impl StageJournalAppend {
    pub(crate) fn prior_byte_len(&self) -> usize {
        self.prior_byte_len
    }

    pub(crate) fn prior_head_sequence(&self) -> u64 {
        self.prior_head_sequence
    }

    pub(crate) fn prior_head_digest(&self) -> StageDigest {
        self.prior_head_digest
    }

    pub(crate) fn record_bytes(&self) -> &[u8] {
        &self.record_bytes
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct StageJournal {
    binding: StageJournalBinding,
    records: Vec<StageRecord>,
    bytes: Vec<u8>,
}

impl StageJournal {
    pub(crate) fn declare(
        binding: StageJournalBinding,
        declaration_digest: StageDigest,
    ) -> Result<Self, StageJournalError> {
        binding.validate()?;
        if is_zero_digest(&declaration_digest) {
            return fail("stage_journal_declaration_digest_zero");
        }

        let mut record = StageRecord::empty(StageJournalRecordKind::RunDeclared);
        record.payload_digest = declaration_digest;
        let (record, record_bytes) = seal_record(record, binding.digest());
        let records = vec![record];
        validate_record_chain(&records, binding.digest())?;

        let mut bytes = encode_header(&binding).to_vec();
        bytes.extend_from_slice(&record_bytes);
        Ok(Self {
            binding,
            records,
            bytes,
        })
    }

    pub(crate) fn reopen(
        bytes: &[u8],
        expected_binding: &StageJournalBinding,
    ) -> Result<Self, StageJournalError> {
        expected_binding.validate()?;
        if bytes.len() > STAGE_JOURNAL_MAX_BYTES {
            return fail("stage_journal_oversized");
        }
        if bytes.len() < HEADER_LEN {
            return fail("stage_journal_header_truncated");
        }

        let binding = decode_header(&bytes[..HEADER_LEN], Some(expected_binding))?;
        let records_bytes = &bytes[HEADER_LEN..];
        if records_bytes.is_empty() {
            return fail("stage_journal_run_declaration_missing");
        }
        if records_bytes.len() % RECORD_LEN != 0 {
            return fail("stage_journal_record_torn_tail");
        }
        let count = records_bytes.len() / RECORD_LEN;
        if count > STAGE_JOURNAL_MAX_RECORDS {
            return fail("stage_journal_record_limit_exceeded");
        }

        let binding_digest = binding.digest();
        let mut records = Vec::with_capacity(count);
        for raw in records_bytes.chunks_exact(RECORD_LEN) {
            records.push(decode_record(raw, binding_digest)?);
        }
        validate_record_chain(&records, binding_digest)?;

        let mut canonical = encode_header(&binding).to_vec();
        for record in &records {
            canonical.extend_from_slice(&encode_record(record));
        }
        if canonical != bytes {
            return fail("stage_journal_noncanonical_encoding");
        }

        Ok(Self {
            binding,
            records,
            bytes: bytes.to_vec(),
        })
    }

    /// Reads only a canonical v2 header binding. Callers must compare every
    /// independently known field before using the returned start-contract
    /// digest to reopen or replay the journal.
    pub(crate) fn persisted_binding(
        bytes: &[u8],
    ) -> Result<StageJournalBinding, StageJournalError> {
        if bytes.len() > STAGE_JOURNAL_MAX_BYTES {
            return fail("stage_journal_oversized");
        }
        if bytes.len() < HEADER_LEN {
            return fail("stage_journal_header_truncated");
        }
        decode_header(&bytes[..HEADER_LEN], None)
    }

    pub(crate) fn verified_replay_summary_from_bytes(
        bytes: &[u8],
        expected_binding: &StageJournalBinding,
        expected_declaration_digest: StageDigest,
    ) -> Result<VerifiedStageJournalReplay, StageJournalError> {
        let journal = Self::reopen(bytes, expected_binding)?;
        replay_record_chain(
            &journal.records,
            journal.binding.digest(),
            Some(expected_declaration_digest),
        )
    }

    pub(crate) fn verified_replay_summary(
        &self,
        expected_declaration_digest: StageDigest,
    ) -> Result<VerifiedStageJournalReplay, StageJournalError> {
        let reopened = Self::reopen(&self.bytes, &self.binding)?;
        if reopened.records != self.records {
            return fail("stage_journal_in_memory_state_mismatch");
        }
        replay_record_chain(
            &reopened.records,
            reopened.binding.digest(),
            Some(expected_declaration_digest),
        )
    }

    pub(crate) fn binding(&self) -> &StageJournalBinding {
        &self.binding
    }

    pub(crate) fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub(crate) fn record_count(&self) -> usize {
        self.records.len()
    }

    pub(crate) fn head(&self) -> StageJournalHead {
        let record = self
            .records
            .last()
            .expect("a verified stage journal always has RunDeclared");
        StageJournalHead {
            sequence: record.sequence,
            record_digest: record.record_digest,
            kind: record.kind,
            action: record.action,
            termination_kind: record.termination_kind,
        }
    }

    pub(crate) fn plan_action_intent(
        &self,
        action: StageAction,
        intent_digest: StageDigest,
    ) -> Result<StageJournalAppend, StageJournalError> {
        let mut record = StageRecord::empty(StageJournalRecordKind::ActionIntent);
        record.action = Some(action);
        record.payload_digest = intent_digest;
        self.plan_record(record)
    }

    pub(crate) fn canonical_action_intent_digest(&self, action: StageAction) -> StageDigest {
        let head = self.head();
        stage_action_intent_digest(
            self.binding.digest(),
            action,
            head.sequence,
            head.record_digest,
        )
    }

    pub(crate) fn plan_action_observed(
        &self,
        action: StageAction,
        observation_digest: StageDigest,
    ) -> Result<StageJournalAppend, StageJournalError> {
        let mut record = StageRecord::empty(StageJournalRecordKind::ActionObserved);
        record.action = Some(action);
        record.payload_digest = observation_digest;
        self.plan_record(record)
    }

    pub(crate) fn plan_termination_intent(
        &self,
        effective_kind: StageTerminationKind,
        requested_at_unix_ms: u64,
        recorded_at_unix_ms: u64,
        armed_receipt_digest: Option<StageDigest>,
    ) -> Result<StageJournalAppend, StageJournalError> {
        let head = self.head();
        let mut record = StageRecord::empty(StageJournalRecordKind::TerminationIntent);
        record.termination_kind = Some(effective_kind);
        record.requested_at_unix_ms = requested_at_unix_ms;
        record.recorded_at_unix_ms = recorded_at_unix_ms;
        record.stage_head_sequence = head.sequence;
        record.stage_head_digest = head.record_digest;
        record.armed_receipt_digest = armed_receipt_digest;
        self.plan_record(record)
    }

    pub(crate) fn plan_terminal(
        &self,
        effective_kind: StageTerminationKind,
        terminal_digest: StageDigest,
    ) -> Result<StageJournalAppend, StageJournalError> {
        let mut record = StageRecord::empty(StageJournalRecordKind::Terminal);
        record.termination_kind = Some(effective_kind);
        record.payload_digest = terminal_digest;
        self.plan_record(record)
    }

    pub(crate) fn plan_cleanup(
        &self,
        effective_kind: StageTerminationKind,
        cleanup_digest: StageDigest,
    ) -> Result<StageJournalAppend, StageJournalError> {
        let mut record = StageRecord::empty(StageJournalRecordKind::Cleanup);
        record.termination_kind = Some(effective_kind);
        record.payload_digest = cleanup_digest;
        self.plan_record(record)
    }

    pub(crate) fn verify_declared_reopen(
        &self,
        reopened_bytes: &[u8],
    ) -> Result<(), StageJournalError> {
        if self.records.len() != 1 || self.records[0].kind != StageJournalRecordKind::RunDeclared {
            return fail("stage_journal_declared_reopen_wrong_state");
        }
        let reopened = Self::reopen(reopened_bytes, &self.binding)?;
        if reopened.bytes != self.bytes {
            return fail("stage_journal_declared_reopen_mismatch");
        }
        Ok(())
    }

    pub(crate) fn verify_reopened_append(
        &self,
        append: &StageJournalAppend,
        reopened_bytes: &[u8],
    ) -> Result<Self, StageJournalError> {
        let head = self.head();
        if append.prior_byte_len != self.bytes.len()
            || append.prior_head_sequence != head.sequence
            || append.prior_head_digest != head.record_digest
        {
            return fail("stage_journal_append_stale_plan");
        }
        let expected_len = self
            .bytes
            .len()
            .checked_add(RECORD_LEN)
            .ok_or_else(|| StageJournalError::new("stage_journal_append_extent_invalid"))?;
        if reopened_bytes.len() != expected_len {
            return fail("stage_journal_append_extent_invalid");
        }
        if reopened_bytes[..self.bytes.len()] != self.bytes {
            return fail("stage_journal_append_prefix_changed");
        }
        if reopened_bytes[self.bytes.len()..] != append.record_bytes {
            return fail("stage_journal_append_record_mismatch");
        }

        let reopened = Self::reopen(reopened_bytes, &self.binding)?;
        if reopened.records.len() != self.records.len() + 1 {
            return fail("stage_journal_append_record_count_invalid");
        }
        Ok(reopened)
    }

    fn plan_record(
        &self,
        mut record: StageRecord,
    ) -> Result<StageJournalAppend, StageJournalError> {
        if self.records.len() >= STAGE_JOURNAL_MAX_RECORDS {
            return fail("stage_journal_record_limit_exceeded");
        }
        let head = self.head();
        record.sequence = self.records.len() as u64;
        record.previous_record_digest = head.record_digest;
        let (record, record_bytes) = seal_record(record, self.binding.digest());

        let mut candidate = self.records.clone();
        candidate.push(record);
        validate_record_chain(&candidate, self.binding.digest())?;
        Ok(StageJournalAppend {
            prior_byte_len: self.bytes.len(),
            prior_head_sequence: head.sequence,
            prior_head_digest: head.record_digest,
            record_bytes,
        })
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct StageRecord {
    kind: StageJournalRecordKind,
    action: Option<StageAction>,
    termination_kind: Option<StageTerminationKind>,
    sequence: u64,
    requested_at_unix_ms: u64,
    recorded_at_unix_ms: u64,
    stage_head_sequence: u64,
    binding_digest: StageDigest,
    previous_record_digest: StageDigest,
    stage_head_digest: StageDigest,
    armed_receipt_digest: Option<StageDigest>,
    payload_digest: StageDigest,
    record_digest: StageDigest,
}

impl StageRecord {
    fn empty(kind: StageJournalRecordKind) -> Self {
        Self {
            kind,
            action: None,
            termination_kind: None,
            sequence: 0,
            requested_at_unix_ms: 0,
            recorded_at_unix_ms: 0,
            stage_head_sequence: 0,
            binding_digest: [0; 32],
            previous_record_digest: [0; 32],
            stage_head_digest: [0; 32],
            armed_receipt_digest: None,
            payload_digest: [0; 32],
            record_digest: [0; 32],
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ReplayState {
    Ready(StageAction),
    Pending(StageAction),
    Armed(StageDigest),
    Terminating {
        kind: StageTerminationKind,
        pending_observation: Option<StageAction>,
        armed_receipt_digest: Option<StageDigest>,
    },
    Terminal {
        kind: StageTerminationKind,
        pending_observation: Option<StageAction>,
        armed_receipt_digest: Option<StageDigest>,
        terminal_payload_digest: StageDigest,
    },
    Cleaned {
        kind: StageTerminationKind,
        pending_observation: Option<StageAction>,
        armed_receipt_digest: Option<StageDigest>,
        terminal_payload_digest: StageDigest,
        cleanup_payload_digest: StageDigest,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct ReplayPhase {
    state: ReplayState,
    declaration_digest: StageDigest,
    action_commitments: [Option<StageActionReplayCommitment>; StageAction::ORDERED.len()],
    termination_commitment: Option<StageTerminationReplayCommitment>,
}

impl ReplayPhase {
    fn declared(record: &StageRecord) -> Self {
        Self {
            state: ReplayState::Ready(StageAction::Prepare),
            declaration_digest: record.payload_digest,
            action_commitments: [None; StageAction::ORDERED.len()],
            termination_commitment: None,
        }
    }

    fn record_action_intent(
        &mut self,
        previous: &StageRecord,
        record: &StageRecord,
        action: StageAction,
    ) -> Result<(), StageJournalError> {
        let slot = &mut self.action_commitments[action.index()];
        if slot.is_some() {
            return fail("stage_journal_action_intent_duplicate");
        }
        let expected_payload_digest = stage_action_intent_digest(
            record.binding_digest,
            action,
            previous.sequence,
            previous.record_digest,
        );
        if record.payload_digest != expected_payload_digest {
            return fail("stage_journal_action_intent_payload_invalid");
        }
        *slot = Some(StageActionReplayCommitment {
            action,
            intent_sequence: record.sequence,
            intent_parent_sequence: previous.sequence,
            intent_parent_digest: previous.record_digest,
            intent_payload_digest: record.payload_digest,
            intent_record_digest: record.record_digest,
            observed_sequence: None,
            observed_parent_sequence: None,
            observed_parent_digest: None,
            observed_payload_digest: None,
            observed_record_digest: None,
        });
        Ok(())
    }

    fn record_action_observed(
        &mut self,
        previous: &StageRecord,
        record: &StageRecord,
        action: StageAction,
    ) -> Result<(), StageJournalError> {
        let commitment = self.action_commitments[action.index()]
            .as_mut()
            .ok_or_else(|| {
                StageJournalError::new("stage_journal_action_observed_without_intent")
            })?;
        if commitment.observed_sequence.is_some()
            || commitment.observed_parent_sequence.is_some()
            || commitment.observed_parent_digest.is_some()
            || commitment.observed_payload_digest.is_some()
            || commitment.observed_record_digest.is_some()
        {
            return fail("stage_journal_action_observation_duplicate");
        }
        commitment.observed_sequence = Some(record.sequence);
        commitment.observed_parent_sequence = Some(previous.sequence);
        commitment.observed_parent_digest = Some(previous.record_digest);
        commitment.observed_payload_digest = Some(record.payload_digest);
        commitment.observed_record_digest = Some(record.record_digest);
        Ok(())
    }

    fn record_termination_intent(
        &mut self,
        previous: &StageRecord,
        record: &StageRecord,
    ) -> Result<(), StageJournalError> {
        if self.termination_commitment.is_some() {
            return fail("stage_journal_duplicate_termination_intent");
        }
        self.termination_commitment = Some(StageTerminationReplayCommitment {
            kind: record
                .termination_kind
                .expect("termination shape requires a kind"),
            branch_head_sequence: previous.sequence,
            branch_head_digest: previous.record_digest,
            intent_sequence: record.sequence,
            intent_record_digest: record.record_digest,
            requested_at_unix_ms: record.requested_at_unix_ms,
            recorded_at_unix_ms: record.recorded_at_unix_ms,
        });
        Ok(())
    }
}

fn validate_record_chain(
    records: &[StageRecord],
    binding_digest: StageDigest,
) -> Result<(), StageJournalError> {
    replay_record_chain(records, binding_digest, None).map(|_| ())
}

fn replay_record_chain(
    records: &[StageRecord],
    binding_digest: StageDigest,
    expected_declaration_digest: Option<StageDigest>,
) -> Result<VerifiedStageJournalReplay, StageJournalError> {
    if records.is_empty() {
        return fail("stage_journal_run_declaration_missing");
    }
    if records.len() > STAGE_JOURNAL_MAX_RECORDS {
        return fail("stage_journal_record_limit_exceeded");
    }

    let mut phase = None;
    for (index, record) in records.iter().enumerate() {
        validate_record_shape(record)?;
        validate_in_memory_record_digest(record)?;
        if record.sequence != index as u64 {
            return fail("stage_journal_sequence_invalid");
        }
        if record.binding_digest != binding_digest {
            return fail("stage_journal_record_binding_mismatch");
        }
        let expected_previous = index
            .checked_sub(1)
            .map(|previous| records[previous].record_digest)
            .unwrap_or([0; 32]);
        if record.previous_record_digest != expected_previous {
            return fail("stage_journal_previous_digest_mismatch");
        }

        if index == 0 {
            if record.kind != StageJournalRecordKind::RunDeclared {
                return fail("stage_journal_first_record_not_declaration");
            }
            if expected_declaration_digest.is_some_and(|expected| record.payload_digest != expected)
            {
                return fail("stage_journal_declaration_digest_mismatch");
            }
            phase = Some(ReplayPhase::declared(record));
            continue;
        }

        let previous = &records[index - 1];
        let current_phase = phase.expect("phase exists after the declaration record");
        phase = Some(apply_transition(current_phase, previous, record)?);
    }
    let head = records
        .last()
        .expect("a non-empty replay has a verified declaration record");
    verified_replay_from_phase(
        phase.expect("a non-empty replay has a phase"),
        head.sequence,
        head.record_digest,
    )
}

fn validate_in_memory_record_digest(record: &StageRecord) -> Result<(), StageJournalError> {
    let encoded = encode_record(record);
    let expected = hash_parts(RECORD_DOMAIN, &[&encoded[..RECORD_DIGEST_OFFSET]]);
    if record.record_digest != expected {
        return fail("stage_journal_record_digest_invalid");
    }
    Ok(())
}

fn verified_replay_from_phase(
    phase: ReplayPhase,
    head_sequence: u64,
    head_digest: StageDigest,
) -> Result<VerifiedStageJournalReplay, StageJournalError> {
    let mut summary = VerifiedStageJournalReplay {
        stage: StageJournalReplayStage::Ready,
        next_action: None,
        pending_observation: None,
        termination_kind: None,
        head_sequence,
        head_digest,
        armed_receipt_digest: None,
        terminal_payload_digest: None,
        cleanup_payload_digest: None,
        declaration_digest: phase.declaration_digest,
        action_commitments: phase.action_commitments,
        termination_commitment: phase.termination_commitment,
    };
    match phase.state {
        ReplayState::Ready(next_action) => {
            summary.stage = StageJournalReplayStage::Ready;
            summary.next_action = Some(next_action);
        }
        ReplayState::Pending(pending_observation) => {
            summary.stage = StageJournalReplayStage::Pending;
            summary.pending_observation = Some(pending_observation);
        }
        ReplayState::Armed(armed_receipt_digest) => {
            summary.stage = StageJournalReplayStage::Armed;
            summary.armed_receipt_digest = Some(armed_receipt_digest);
        }
        ReplayState::Terminating {
            kind,
            pending_observation,
            armed_receipt_digest,
        } => {
            summary.stage = StageJournalReplayStage::Terminating;
            summary.pending_observation = pending_observation;
            summary.termination_kind = Some(kind);
            summary.armed_receipt_digest = armed_receipt_digest;
        }
        ReplayState::Terminal {
            kind,
            pending_observation,
            armed_receipt_digest,
            terminal_payload_digest,
        } => {
            summary.stage = StageJournalReplayStage::Terminal;
            summary.pending_observation = pending_observation;
            summary.termination_kind = Some(kind);
            summary.armed_receipt_digest = armed_receipt_digest;
            summary.terminal_payload_digest = Some(terminal_payload_digest);
        }
        ReplayState::Cleaned {
            kind,
            pending_observation,
            armed_receipt_digest,
            terminal_payload_digest,
            cleanup_payload_digest,
        } => {
            summary.stage = StageJournalReplayStage::Cleaned;
            summary.pending_observation = pending_observation;
            summary.termination_kind = Some(kind);
            summary.armed_receipt_digest = armed_receipt_digest;
            summary.terminal_payload_digest = Some(terminal_payload_digest);
            summary.cleanup_payload_digest = Some(cleanup_payload_digest);
        }
    }
    validate_verified_replay_summary(&summary)?;
    Ok(summary)
}

fn validate_verified_replay_summary(
    summary: &VerifiedStageJournalReplay,
) -> Result<(), StageJournalError> {
    let canonical = match summary.stage {
        StageJournalReplayStage::Ready => {
            summary.next_action.is_some()
                && summary.pending_observation.is_none()
                && summary.termination_kind.is_none()
                && summary.armed_receipt_digest.is_none()
                && summary.terminal_payload_digest.is_none()
                && summary.cleanup_payload_digest.is_none()
        }
        StageJournalReplayStage::Pending => {
            summary.next_action.is_none()
                && summary.pending_observation.is_some()
                && summary.termination_kind.is_none()
                && summary.armed_receipt_digest.is_none()
                && summary.terminal_payload_digest.is_none()
                && summary.cleanup_payload_digest.is_none()
        }
        StageJournalReplayStage::Armed => {
            summary.next_action.is_none()
                && summary.pending_observation.is_none()
                && summary.termination_kind.is_none()
                && summary.armed_receipt_digest.is_some()
                && summary.terminal_payload_digest.is_none()
                && summary.cleanup_payload_digest.is_none()
        }
        StageJournalReplayStage::Terminating => {
            summary.next_action.is_none()
                && summary.termination_kind.is_some()
                && !(summary.pending_observation.is_some()
                    && summary.armed_receipt_digest.is_some())
                && summary.terminal_payload_digest.is_none()
                && summary.cleanup_payload_digest.is_none()
        }
        StageJournalReplayStage::Terminal => {
            summary.next_action.is_none()
                && summary.termination_kind.is_some()
                && !(summary.pending_observation.is_some()
                    && summary.armed_receipt_digest.is_some())
                && summary.terminal_payload_digest.is_some()
                && summary.cleanup_payload_digest.is_none()
        }
        StageJournalReplayStage::Cleaned => {
            summary.next_action.is_none()
                && summary.termination_kind.is_some()
                && !(summary.pending_observation.is_some()
                    && summary.armed_receipt_digest.is_some())
                && summary.terminal_payload_digest.is_some()
                && summary.cleanup_payload_digest.is_some()
        }
    };
    if !canonical
        || is_zero_digest(&summary.declaration_digest)
        || is_zero_digest(&summary.head_digest)
        || summary
            .armed_receipt_digest
            .is_some_and(|digest| is_zero_digest(&digest))
        || summary
            .terminal_payload_digest
            .is_some_and(|digest| is_zero_digest(&digest))
        || summary
            .cleanup_payload_digest
            .is_some_and(|digest| is_zero_digest(&digest))
    {
        return fail("stage_journal_replay_summary_noncanonical");
    }
    validate_action_replay_commitments(summary)?;
    validate_termination_replay_commitment(summary)?;
    Ok(())
}

fn validate_action_replay_commitments(
    summary: &VerifiedStageJournalReplay,
) -> Result<(), StageJournalError> {
    let mut first_missing = None;
    let mut pending_action = None;
    let mut previous_observed = None;
    let mut payload_digests = vec![summary.declaration_digest];

    for action in StageAction::ORDERED {
        let Some(commitment) = summary.action_commitments[action.index()] else {
            if first_missing.is_none() {
                first_missing = Some(action);
            }
            continue;
        };
        if first_missing.is_some()
            || commitment.action != action
            || commitment.intent_sequence == 0
            || commitment.intent_parent_sequence.checked_add(1) != Some(commitment.intent_sequence)
            || is_zero_digest(&commitment.intent_parent_digest)
            || is_zero_digest(&commitment.intent_payload_digest)
            || is_zero_digest(&commitment.intent_record_digest)
            || payload_digests.contains(&commitment.intent_payload_digest)
        {
            return fail("stage_journal_replay_action_commitments_noncanonical");
        }
        if let Some((sequence, digest)) = previous_observed {
            if commitment.intent_parent_sequence != sequence
                || commitment.intent_parent_digest != digest
            {
                return fail("stage_journal_replay_action_parent_mismatch");
            }
        } else if action != StageAction::Prepare || commitment.intent_sequence != 1 {
            return fail("stage_journal_replay_action_sequence_invalid");
        }
        payload_digests.push(commitment.intent_payload_digest);

        let observed_fields = [
            commitment.observed_sequence.is_some(),
            commitment.observed_parent_sequence.is_some(),
            commitment.observed_parent_digest.is_some(),
            commitment.observed_payload_digest.is_some(),
            commitment.observed_record_digest.is_some(),
        ];
        if observed_fields.iter().any(|present| *present)
            && observed_fields.iter().any(|present| !*present)
        {
            return fail("stage_journal_replay_action_observation_partial");
        }
        if let (
            Some(observed_sequence),
            Some(observed_parent_sequence),
            Some(observed_parent_digest),
            Some(observed_payload_digest),
            Some(observed_record_digest),
        ) = (
            commitment.observed_sequence,
            commitment.observed_parent_sequence,
            commitment.observed_parent_digest,
            commitment.observed_payload_digest,
            commitment.observed_record_digest,
        ) {
            if pending_action.is_some()
                || observed_parent_sequence.checked_add(1) != Some(observed_sequence)
                || is_zero_digest(&observed_parent_digest)
                || is_zero_digest(&observed_payload_digest)
                || is_zero_digest(&observed_record_digest)
                || payload_digests.contains(&observed_payload_digest)
            {
                return fail("stage_journal_replay_action_observation_noncanonical");
            }
            let direct_observation = observed_parent_sequence == commitment.intent_sequence
                && observed_parent_digest == commitment.intent_record_digest;
            let post_termination_observation =
                summary.termination_commitment.is_some_and(|termination| {
                    termination.branch_head_sequence == commitment.intent_sequence
                        && termination.branch_head_digest == commitment.intent_record_digest
                        && termination.intent_sequence == observed_parent_sequence
                        && termination.intent_record_digest == observed_parent_digest
                });
            if !direct_observation && !post_termination_observation {
                return fail("stage_journal_replay_action_observation_parent_mismatch");
            }
            payload_digests.push(observed_payload_digest);
            previous_observed = Some((observed_sequence, observed_record_digest));
        } else {
            pending_action = Some(action);
        }
    }

    if summary.pending_observation != pending_action {
        return fail("stage_journal_replay_pending_observation_mismatch");
    }
    match summary.stage {
        StageJournalReplayStage::Ready => {
            if pending_action.is_some() || summary.next_action != first_missing {
                return fail("stage_journal_replay_ready_action_mismatch");
            }
        }
        StageJournalReplayStage::Pending => {
            if pending_action.is_none() || summary.next_action.is_some() {
                return fail("stage_journal_replay_pending_action_mismatch");
            }
        }
        StageJournalReplayStage::Armed => {
            if pending_action.is_some() || first_missing.is_some() {
                return fail("stage_journal_replay_armed_action_mismatch");
            }
        }
        StageJournalReplayStage::Terminating
        | StageJournalReplayStage::Terminal
        | StageJournalReplayStage::Cleaned => {}
    }

    let observed_arm_digest = summary.action_commitments[StageAction::Arm.index()]
        .and_then(|commitment| commitment.observed_payload_digest);
    if summary.armed_receipt_digest != observed_arm_digest {
        return fail("stage_journal_replay_armed_digest_mismatch");
    }
    Ok(())
}

fn validate_termination_replay_commitment(
    summary: &VerifiedStageJournalReplay,
) -> Result<(), StageJournalError> {
    let termination_stage = matches!(
        summary.stage,
        StageJournalReplayStage::Terminating
            | StageJournalReplayStage::Terminal
            | StageJournalReplayStage::Cleaned
    );
    if termination_stage != summary.termination_commitment.is_some() {
        return fail("stage_journal_replay_termination_commitment_mismatch");
    }
    let Some(termination) = summary.termination_commitment else {
        return Ok(());
    };
    if summary.termination_kind != Some(termination.kind)
        || termination.branch_head_sequence.checked_add(1) != Some(termination.intent_sequence)
        || is_zero_digest(&termination.branch_head_digest)
        || is_zero_digest(&termination.intent_record_digest)
        || termination.requested_at_unix_ms == 0
        || termination.recorded_at_unix_ms < termination.requested_at_unix_ms
    {
        return fail("stage_journal_replay_termination_commitment_noncanonical");
    }

    let branch_matches_declaration = termination.branch_head_sequence == 0
        && summary.action_commitments.iter().all(Option::is_none);
    let branch_matches_action = summary
        .action_commitments
        .iter()
        .flatten()
        .filter(|commitment| {
            let intent_match = commitment.intent_sequence == termination.branch_head_sequence
                && commitment.intent_record_digest == termination.branch_head_digest
                && commitment
                    .observed_sequence
                    .is_none_or(|sequence| sequence > termination.intent_sequence);
            let observed_match = commitment.observed_sequence
                == Some(termination.branch_head_sequence)
                && commitment.observed_record_digest == Some(termination.branch_head_digest)
                && commitment
                    .observed_sequence
                    .is_some_and(|sequence| sequence < termination.intent_sequence);
            intent_match || observed_match
        })
        .count();
    if usize::from(branch_matches_declaration) + branch_matches_action != 1 {
        return fail("stage_journal_replay_termination_branch_ambiguous");
    }

    let post_termination_observations: Vec<_> = summary
        .action_commitments
        .iter()
        .flatten()
        .filter(|commitment| {
            commitment
                .observed_sequence
                .is_some_and(|sequence| sequence > termination.intent_sequence)
        })
        .collect();
    if post_termination_observations.len() > 1 {
        return fail("stage_journal_replay_post_termination_observation_ambiguous");
    }
    let post_termination_count = post_termination_observations.len() as u64;
    let expected_head_sequence = match summary.stage {
        StageJournalReplayStage::Terminating => termination
            .intent_sequence
            .checked_add(post_termination_count),
        StageJournalReplayStage::Terminal => termination
            .intent_sequence
            .checked_add(post_termination_count)
            .and_then(|sequence| sequence.checked_add(1)),
        StageJournalReplayStage::Cleaned => termination
            .intent_sequence
            .checked_add(post_termination_count)
            .and_then(|sequence| sequence.checked_add(2)),
        _ => None,
    };
    if expected_head_sequence != Some(summary.head_sequence) {
        return fail("stage_journal_replay_termination_sequence_invalid");
    }
    if summary.stage == StageJournalReplayStage::Terminating {
        let expected_head_digest = post_termination_observations
            .first()
            .and_then(|commitment| commitment.observed_record_digest)
            .unwrap_or(termination.intent_record_digest);
        if summary.head_digest != expected_head_digest {
            return fail("stage_journal_replay_termination_head_invalid");
        }
    }
    Ok(())
}

fn validate_record_shape(record: &StageRecord) -> Result<(), StageJournalError> {
    match record.kind {
        StageJournalRecordKind::RunDeclared => {
            require_no_action_or_termination(record)?;
            require_zero_timing_and_stage_head(record)?;
            if record.armed_receipt_digest.is_some() || is_zero_digest(&record.payload_digest) {
                return fail("stage_journal_declaration_noncanonical");
            }
        }
        StageJournalRecordKind::ActionIntent | StageJournalRecordKind::ActionObserved => {
            if record.action.is_none()
                || record.termination_kind.is_some()
                || record.armed_receipt_digest.is_some()
                || is_zero_digest(&record.payload_digest)
            {
                return fail("stage_journal_action_record_noncanonical");
            }
            require_zero_timing_and_stage_head(record)?;
        }
        StageJournalRecordKind::TerminationIntent => {
            if record.action.is_some()
                || record.termination_kind.is_none()
                || !is_zero_digest(&record.payload_digest)
                || record.requested_at_unix_ms == 0
                || record.recorded_at_unix_ms < record.requested_at_unix_ms
                || is_zero_digest(&record.stage_head_digest)
            {
                return fail("stage_journal_termination_intent_noncanonical");
            }
        }
        StageJournalRecordKind::Terminal | StageJournalRecordKind::Cleanup => {
            if record.action.is_some()
                || record.termination_kind.is_none()
                || record.armed_receipt_digest.is_some()
                || is_zero_digest(&record.payload_digest)
            {
                return fail("stage_journal_terminal_record_noncanonical");
            }
            require_zero_timing_and_stage_head(record)?;
        }
    }
    Ok(())
}

fn require_no_action_or_termination(record: &StageRecord) -> Result<(), StageJournalError> {
    if record.action.is_some() || record.termination_kind.is_some() {
        return fail("stage_journal_record_unexpected_discriminator");
    }
    Ok(())
}

fn require_zero_timing_and_stage_head(record: &StageRecord) -> Result<(), StageJournalError> {
    if record.requested_at_unix_ms != 0
        || record.recorded_at_unix_ms != 0
        || record.stage_head_sequence != 0
        || !is_zero_digest(&record.stage_head_digest)
    {
        return fail("stage_journal_record_unexpected_timing_or_head");
    }
    Ok(())
}

fn apply_transition(
    mut phase: ReplayPhase,
    previous: &StageRecord,
    record: &StageRecord,
) -> Result<ReplayPhase, StageJournalError> {
    if record.kind == StageJournalRecordKind::RunDeclared {
        return fail("stage_journal_duplicate_declaration");
    }

    match (phase.state, record.kind) {
        (ReplayState::Ready(expected), StageJournalRecordKind::ActionIntent) => {
            if record.action != Some(expected) {
                return fail("stage_journal_action_order_invalid");
            }
            phase.record_action_intent(previous, record, expected)?;
            phase.state = ReplayState::Pending(expected);
            Ok(phase)
        }
        (ReplayState::Pending(expected), StageJournalRecordKind::ActionObserved) => {
            if record.action != Some(expected) {
                return fail("stage_journal_action_pair_invalid");
            }
            phase.record_action_observed(previous, record, expected)?;
            phase.state = match expected.next() {
                Some(next) => ReplayState::Ready(next),
                None => ReplayState::Armed(record.payload_digest),
            };
            Ok(phase)
        }
        (ReplayState::Ready(_), StageJournalRecordKind::TerminationIntent) => {
            validate_termination_branch(previous, record, None)?;
            phase.record_termination_intent(previous, record)?;
            phase.state = ReplayState::Terminating {
                kind: record
                    .termination_kind
                    .expect("termination shape requires a kind"),
                pending_observation: None,
                armed_receipt_digest: None,
            };
            Ok(phase)
        }
        (ReplayState::Pending(pending), StageJournalRecordKind::TerminationIntent) => {
            validate_termination_branch(previous, record, None)?;
            phase.record_termination_intent(previous, record)?;
            phase.state = ReplayState::Terminating {
                kind: record
                    .termination_kind
                    .expect("termination shape requires a kind"),
                pending_observation: Some(pending),
                armed_receipt_digest: None,
            };
            Ok(phase)
        }
        (ReplayState::Armed(armed_digest), StageJournalRecordKind::TerminationIntent) => {
            validate_termination_branch(previous, record, Some(armed_digest))?;
            phase.record_termination_intent(previous, record)?;
            phase.state = ReplayState::Terminating {
                kind: record
                    .termination_kind
                    .expect("termination shape requires a kind"),
                pending_observation: None,
                armed_receipt_digest: Some(armed_digest),
            };
            Ok(phase)
        }
        (
            ReplayState::Terminating {
                kind,
                pending_observation: Some(pending),
                armed_receipt_digest,
            },
            StageJournalRecordKind::ActionObserved,
        ) => {
            if record.action != Some(pending) {
                return fail("stage_journal_post_termination_observation_invalid");
            }
            phase.record_action_observed(previous, record, pending)?;
            phase.state = ReplayState::Terminating {
                kind,
                pending_observation: None,
                armed_receipt_digest: if pending == StageAction::Arm {
                    Some(record.payload_digest)
                } else {
                    armed_receipt_digest
                },
            };
            Ok(phase)
        }
        (
            ReplayState::Terminating {
                kind,
                pending_observation,
                armed_receipt_digest,
            },
            StageJournalRecordKind::Terminal,
        ) => {
            if record.termination_kind != Some(kind) {
                return fail("stage_journal_terminal_kind_mismatch");
            }
            phase.state = ReplayState::Terminal {
                kind,
                pending_observation,
                armed_receipt_digest,
                terminal_payload_digest: record.payload_digest,
            };
            Ok(phase)
        }
        (
            ReplayState::Terminal {
                kind,
                pending_observation,
                armed_receipt_digest,
                terminal_payload_digest,
            },
            StageJournalRecordKind::Cleanup,
        ) => {
            if record.termination_kind != Some(kind) {
                return fail("stage_journal_cleanup_kind_mismatch");
            }
            phase.state = ReplayState::Cleaned {
                kind,
                pending_observation,
                armed_receipt_digest,
                terminal_payload_digest,
                cleanup_payload_digest: record.payload_digest,
            };
            Ok(phase)
        }
        (ReplayState::Terminating { .. }, StageJournalRecordKind::ActionIntent) => {
            fail("stage_journal_action_intent_after_termination")
        }
        (ReplayState::Terminating { .. }, StageJournalRecordKind::TerminationIntent) => {
            fail("stage_journal_duplicate_termination_intent")
        }
        (ReplayState::Terminal { .. }, _) | (ReplayState::Cleaned { .. }, _) => {
            fail("stage_journal_record_after_terminal_state")
        }
        _ => fail("stage_journal_transition_invalid"),
    }
}

fn validate_termination_branch(
    previous: &StageRecord,
    record: &StageRecord,
    expected_armed_digest: Option<StageDigest>,
) -> Result<(), StageJournalError> {
    if record.stage_head_sequence != previous.sequence
        || record.stage_head_digest != previous.record_digest
    {
        return fail("stage_journal_termination_head_mismatch");
    }
    if record.armed_receipt_digest != expected_armed_digest {
        return fail("stage_journal_termination_armed_digest_mismatch");
    }
    Ok(())
}

fn encode_header(binding: &StageJournalBinding) -> [u8; HEADER_LEN] {
    let mut bytes = [0u8; HEADER_LEN];
    bytes[..8].copy_from_slice(JOURNAL_MAGIC);
    bytes[8..10].copy_from_slice(&JOURNAL_VERSION.to_be_bytes());
    bytes[10..12].copy_from_slice(&(HEADER_LEN as u16).to_be_bytes());
    bytes[12..14].copy_from_slice(&(RECORD_LEN as u16).to_be_bytes());
    let mut offset = 16;
    for digest in [
        binding.authority_generation_digest,
        binding.service_instance_digest,
        binding.ticket_digest,
        binding.run_binding_digest,
        binding.prepared_receipt_digest,
        binding.policy_snapshot_digest,
        binding.recovery_bundle_digest,
        binding.start_contract_digest,
        binding.digest(),
    ] {
        bytes[offset..offset + 32].copy_from_slice(&digest);
        offset += 32;
    }
    bytes
}

fn decode_header(
    bytes: &[u8],
    expected_binding: Option<&StageJournalBinding>,
) -> Result<StageJournalBinding, StageJournalError> {
    if bytes.len() != HEADER_LEN {
        return fail("stage_journal_header_length_invalid");
    }
    if &bytes[..8] != JOURNAL_MAGIC {
        return fail("stage_journal_header_magic_invalid");
    }
    if read_u16(bytes, 8) != JOURNAL_VERSION {
        return fail("stage_journal_header_version_invalid");
    }
    if read_u16(bytes, 10) as usize != HEADER_LEN || read_u16(bytes, 12) as usize != RECORD_LEN {
        return fail("stage_journal_header_layout_invalid");
    }
    if bytes[14] != 0 || bytes[15] != 0 {
        return fail("stage_journal_header_reserved_nonzero");
    }

    let binding = StageJournalBinding::new(
        read_digest(bytes, 16),
        read_digest(bytes, 48),
        read_digest(bytes, 80),
        read_digest(bytes, 112),
        read_digest(bytes, 144),
        read_digest(bytes, 176),
        read_digest(bytes, 208),
        read_digest(bytes, 240),
    )?;
    if expected_binding.is_some_and(|expected| expected != &binding) {
        return fail("stage_journal_header_binding_mismatch");
    }
    if read_digest(bytes, 272) != binding.digest() {
        return fail("stage_journal_header_binding_digest_invalid");
    }
    if encode_header(&binding) != bytes {
        return fail("stage_journal_header_noncanonical");
    }
    Ok(binding)
}

fn seal_record(
    mut record: StageRecord,
    binding_digest: StageDigest,
) -> (StageRecord, [u8; RECORD_LEN]) {
    record.binding_digest = binding_digest;
    let mut bytes = encode_record(&record);
    record.record_digest = hash_parts(RECORD_DOMAIN, &[&bytes[..RECORD_DIGEST_OFFSET]]);
    bytes[RECORD_DIGEST_OFFSET..].copy_from_slice(&record.record_digest);
    (record, bytes)
}

fn encode_record(record: &StageRecord) -> [u8; RECORD_LEN] {
    let mut bytes = [0u8; RECORD_LEN];
    bytes[..4].copy_from_slice(RECORD_MAGIC);
    bytes[4] = RECORD_VERSION;
    bytes[5] = record.kind.code();
    bytes[6] = record.action.map(StageAction::code).unwrap_or(0);
    bytes[7] = record
        .termination_kind
        .map(StageTerminationKind::code)
        .unwrap_or(0);
    if record.armed_receipt_digest.is_some() {
        bytes[8] = 1;
    }
    bytes[12..20].copy_from_slice(&record.sequence.to_be_bytes());
    bytes[20..28].copy_from_slice(&record.requested_at_unix_ms.to_be_bytes());
    bytes[28..36].copy_from_slice(&record.recorded_at_unix_ms.to_be_bytes());
    bytes[36..44].copy_from_slice(&record.stage_head_sequence.to_be_bytes());
    bytes[44..76].copy_from_slice(&record.binding_digest);
    bytes[76..108].copy_from_slice(&record.previous_record_digest);
    bytes[108..140].copy_from_slice(&record.stage_head_digest);
    if let Some(armed_receipt_digest) = record.armed_receipt_digest {
        bytes[140..172].copy_from_slice(&armed_receipt_digest);
    }
    bytes[172..204].copy_from_slice(&record.payload_digest);
    bytes[204..236].copy_from_slice(&record.record_digest);
    bytes
}

fn decode_record(
    bytes: &[u8],
    expected_binding_digest: StageDigest,
) -> Result<StageRecord, StageJournalError> {
    if bytes.len() != RECORD_LEN {
        return fail("stage_journal_record_length_invalid");
    }
    if &bytes[..4] != RECORD_MAGIC {
        return fail("stage_journal_record_magic_invalid");
    }
    if bytes[4] != RECORD_VERSION {
        return fail("stage_journal_record_version_invalid");
    }
    if bytes[8] & !1 != 0 {
        return fail("stage_journal_record_flags_unknown");
    }
    if bytes[9..12] != [0, 0, 0] {
        return fail("stage_journal_record_reserved_nonzero");
    }

    let armed_bytes = read_digest(bytes, 140);
    let armed_receipt_digest = if bytes[8] == 1 {
        if is_zero_digest(&armed_bytes) {
            return fail("stage_journal_record_armed_digest_zero");
        }
        Some(armed_bytes)
    } else {
        if !is_zero_digest(&armed_bytes) {
            return fail("stage_journal_record_armed_digest_without_flag");
        }
        None
    };
    let record = StageRecord {
        kind: StageJournalRecordKind::from_code(bytes[5])?,
        action: StageAction::from_code(bytes[6])?,
        termination_kind: StageTerminationKind::from_code(bytes[7])?,
        sequence: read_u64(bytes, 12),
        requested_at_unix_ms: read_u64(bytes, 20),
        recorded_at_unix_ms: read_u64(bytes, 28),
        stage_head_sequence: read_u64(bytes, 36),
        binding_digest: read_digest(bytes, 44),
        previous_record_digest: read_digest(bytes, 76),
        stage_head_digest: read_digest(bytes, 108),
        armed_receipt_digest,
        payload_digest: read_digest(bytes, 172),
        record_digest: read_digest(bytes, 204),
    };
    if record.binding_digest != expected_binding_digest {
        return fail("stage_journal_record_binding_mismatch");
    }
    let expected_record_digest = hash_parts(RECORD_DOMAIN, &[&bytes[..RECORD_DIGEST_OFFSET]]);
    if record.record_digest != expected_record_digest {
        return fail("stage_journal_record_digest_invalid");
    }
    if encode_record(&record) != bytes {
        return fail("stage_journal_record_noncanonical");
    }
    Ok(record)
}

fn hash_parts(domain: &[u8], parts: &[&[u8]]) -> StageDigest {
    let mut hasher = Sha256::new();
    hasher.update((domain.len() as u64).to_be_bytes());
    hasher.update(domain);
    for part in parts {
        hasher.update((part.len() as u64).to_be_bytes());
        hasher.update(part);
    }
    hasher.finalize().into()
}

fn stage_action_intent_digest(
    binding_digest: StageDigest,
    action: StageAction,
    parent_sequence: u64,
    parent_digest: StageDigest,
) -> StageDigest {
    let mut hasher = Sha256::new();
    hasher.update(STAGE_ACTION_INTENT_DOMAIN);
    for part in [
        binding_digest.as_slice(),
        &[action.code()],
        &parent_sequence.to_be_bytes(),
        parent_digest.as_slice(),
    ] {
        hasher.update((part.len() as u64).to_be_bytes());
        hasher.update(part);
    }
    hasher.finalize().into()
}

fn read_u16(bytes: &[u8], offset: usize) -> u16 {
    u16::from_be_bytes(
        bytes[offset..offset + 2]
            .try_into()
            .expect("fixed-size u16 field"),
    )
}

fn read_u64(bytes: &[u8], offset: usize) -> u64 {
    u64::from_be_bytes(
        bytes[offset..offset + 8]
            .try_into()
            .expect("fixed-size u64 field"),
    )
}

fn read_digest(bytes: &[u8], offset: usize) -> StageDigest {
    bytes[offset..offset + 32]
        .try_into()
        .expect("fixed-size digest field")
}

fn is_zero_digest(digest: &StageDigest) -> bool {
    digest.iter().all(|byte| *byte == 0)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct StageJournalError {
    code: &'static str,
}

impl StageJournalError {
    const fn new(code: &'static str) -> Self {
        Self { code }
    }

    pub(crate) const fn code(self) -> &'static str {
        self.code
    }
}

impl fmt::Display for StageJournalError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code)
    }
}

impl std::error::Error for StageJournalError {}

fn fail<T>(code: &'static str) -> Result<T, StageJournalError> {
    Err(StageJournalError::new(code))
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIRST_RECORD_OFFSET: usize = HEADER_LEN;

    fn digest(value: u8) -> StageDigest {
        [value; 32]
    }

    fn binding() -> StageJournalBinding {
        StageJournalBinding::new(
            digest(1),
            digest(2),
            digest(3),
            digest(4),
            digest(5),
            digest(6),
            digest(7),
            digest(8),
        )
        .unwrap()
    }

    fn declared() -> StageJournal {
        StageJournal::declare(binding(), digest(8)).unwrap()
    }

    fn apply(journal: &StageJournal, append: &StageJournalAppend) -> StageJournal {
        let mut bytes = journal.bytes().to_vec();
        bytes.extend_from_slice(append.record_bytes());
        journal.verify_reopened_append(append, &bytes).unwrap()
    }

    fn replay_summary(journal: &StageJournal) -> VerifiedStageJournalReplay {
        StageJournal::verified_replay_summary_from_bytes(journal.bytes(), &binding(), digest(8))
            .unwrap()
    }

    fn action_pair(journal: &StageJournal, action: StageAction, seed: u8) -> StageJournal {
        let journal = apply(
            journal,
            &journal
                .plan_action_intent(action, journal.canonical_action_intent_digest(action))
                .unwrap(),
        );
        apply(
            &journal,
            &journal
                .plan_action_observed(action, digest(seed.wrapping_add(1)))
                .unwrap(),
        )
    }

    fn prefix_before(action: StageAction) -> StageJournal {
        let mut journal = declared();
        for (index, candidate) in StageAction::ORDERED.into_iter().enumerate() {
            if candidate == action {
                break;
            }
            journal = action_pair(&journal, candidate, 20 + (index as u8 * 2));
        }
        journal
    }

    fn assert_code<T: fmt::Debug>(result: Result<T, StageJournalError>, expected: &'static str) {
        assert_eq!(result.unwrap_err().code(), expected);
    }

    fn record_offset(index: usize) -> usize {
        FIRST_RECORD_OFFSET + (index * RECORD_LEN)
    }

    fn recompute_record_digest(bytes: &mut [u8], index: usize) {
        let start = record_offset(index);
        let digest = hash_parts(
            RECORD_DOMAIN,
            &[&bytes[start..start + RECORD_DIGEST_OFFSET]],
        );
        bytes[start + RECORD_DIGEST_OFFSET..start + RECORD_LEN].copy_from_slice(&digest);
    }

    fn assert_normal_pending(
        summary: VerifiedStageJournalReplay,
        stage: StageJournalReplayStage,
        reason: NormalTerminationPendingReason,
        pending_observation: Option<StageAction>,
    ) {
        let VerifiedNormalTerminationReplay::Pending(pending) =
            summary.normal_termination_material()
        else {
            panic!("partial replay unexpectedly produced complete termination material");
        };
        assert_eq!(pending.stage(), stage);
        assert_eq!(pending.reason(), reason);
        assert_eq!(pending.pending_observation(), pending_observation);
    }

    #[test]
    fn full_chain_is_canonical_and_reopens_exactly() {
        let mut journal = declared();
        journal.verify_declared_reopen(journal.bytes()).unwrap();
        assert_eq!(journal.binding().authority_generation_digest(), digest(1));
        assert_eq!(journal.binding().service_instance_digest(), digest(2));
        assert_eq!(journal.binding().ticket_digest(), digest(3));
        assert_eq!(journal.binding().run_binding_digest(), digest(4));
        assert_eq!(journal.binding().prepared_receipt_digest(), digest(5));
        assert_eq!(journal.binding().policy_snapshot_digest(), digest(6));
        assert_eq!(journal.binding().recovery_bundle_digest(), digest(7));
        for (index, action) in StageAction::ORDERED.into_iter().enumerate() {
            journal = action_pair(&journal, action, 20 + (index as u8 * 2));
        }
        let armed_digest = digest(31);
        assert_eq!(journal.records.last().unwrap().payload_digest, armed_digest);
        journal = apply(
            &journal,
            &journal
                .plan_termination_intent(
                    StageTerminationKind::Cancelled,
                    1_000,
                    1_005,
                    Some(armed_digest),
                )
                .unwrap(),
        );
        journal = apply(
            &journal,
            &journal
                .plan_terminal(StageTerminationKind::Cancelled, digest(40))
                .unwrap(),
        );
        journal = apply(
            &journal,
            &journal
                .plan_cleanup(StageTerminationKind::Cancelled, digest(41))
                .unwrap(),
        );

        assert_eq!(journal.record_count(), 16);
        assert_eq!(journal.head().kind, StageJournalRecordKind::Cleanup);
        assert_eq!(
            StageJournal::reopen(journal.bytes(), &binding()).unwrap(),
            journal
        );
        assert!(journal.bytes().len() <= STAGE_JOURNAL_MAX_BYTES);
    }

    #[test]
    fn verified_replay_summary_reports_every_canonical_stage() {
        let declared_journal = declared();
        let ready = replay_summary(&declared_journal);
        assert_eq!(ready.stage(), StageJournalReplayStage::Ready);
        assert_eq!(ready.next_action(), Some(StageAction::Prepare));
        assert_eq!(ready.pending_observation(), None);
        assert_eq!(ready.termination_kind(), None);
        assert_eq!(ready.head_sequence(), declared_journal.head().sequence);
        assert_eq!(ready.head_digest(), declared_journal.head().record_digest);
        assert_eq!(ready.armed_receipt_digest(), None);
        assert_eq!(ready.terminal_payload_digest(), None);
        assert_eq!(ready.cleanup_payload_digest(), None);
        assert_eq!(
            declared_journal.verified_replay_summary(digest(8)).unwrap(),
            ready
        );

        let prepare_pending = apply(
            &declared_journal,
            &declared_journal
                .plan_action_intent(
                    StageAction::Prepare,
                    declared_journal.canonical_action_intent_digest(StageAction::Prepare),
                )
                .unwrap(),
        );
        let pending = replay_summary(&prepare_pending);
        assert_eq!(pending.stage(), StageJournalReplayStage::Pending);
        assert_eq!(pending.next_action(), None);
        assert_eq!(pending.pending_observation(), Some(StageAction::Prepare));
        assert_eq!(pending.termination_kind(), None);

        let prepare_observed = apply(
            &prepare_pending,
            &prepare_pending
                .plan_action_observed(StageAction::Prepare, digest(161))
                .unwrap(),
        );
        let next_ready = replay_summary(&prepare_observed);
        assert_eq!(next_ready.stage(), StageJournalReplayStage::Ready);
        assert_eq!(next_ready.next_action(), Some(StageAction::BridgeCreate));

        let mut armed_journal = declared();
        for (index, action) in StageAction::ORDERED.into_iter().enumerate() {
            armed_journal = action_pair(&armed_journal, action, 170 + (index as u8 * 2));
        }
        let armed_digest = armed_journal.records.last().unwrap().payload_digest;
        let armed = replay_summary(&armed_journal);
        assert_eq!(armed.stage(), StageJournalReplayStage::Armed);
        assert_eq!(armed.armed_receipt_digest(), Some(armed_digest));
        assert_eq!(armed.termination_kind(), None);

        let terminating_journal = apply(
            &armed_journal,
            &armed_journal
                .plan_termination_intent(
                    StageTerminationKind::TimedOut,
                    2_000,
                    2_001,
                    Some(armed_digest),
                )
                .unwrap(),
        );
        let terminating = replay_summary(&terminating_journal);
        assert_eq!(terminating.stage(), StageJournalReplayStage::Terminating);
        assert_eq!(
            terminating.termination_kind(),
            Some(StageTerminationKind::TimedOut)
        );
        assert_eq!(terminating.pending_observation(), None);
        assert_eq!(terminating.armed_receipt_digest(), Some(armed_digest));
        assert_eq!(terminating.terminal_payload_digest(), None);

        let terminal_digest = digest(190);
        let terminal_journal = apply(
            &terminating_journal,
            &terminating_journal
                .plan_terminal(StageTerminationKind::TimedOut, terminal_digest)
                .unwrap(),
        );
        let terminal = replay_summary(&terminal_journal);
        assert_eq!(terminal.stage(), StageJournalReplayStage::Terminal);
        assert_eq!(terminal.terminal_payload_digest(), Some(terminal_digest));
        assert_eq!(terminal.cleanup_payload_digest(), None);
        assert_eq!(terminal.armed_receipt_digest(), Some(armed_digest));

        let cleanup_digest = digest(191);
        let cleaned_journal = apply(
            &terminal_journal,
            &terminal_journal
                .plan_cleanup(StageTerminationKind::TimedOut, cleanup_digest)
                .unwrap(),
        );
        let cleaned = replay_summary(&cleaned_journal);
        assert_eq!(cleaned.stage(), StageJournalReplayStage::Cleaned);
        assert_eq!(
            cleaned.termination_kind(),
            Some(StageTerminationKind::TimedOut)
        );
        assert_eq!(cleaned.terminal_payload_digest(), Some(terminal_digest));
        assert_eq!(cleaned.cleanup_payload_digest(), Some(cleanup_digest));
        assert_eq!(cleaned.head_sequence(), cleaned_journal.head().sequence);
        assert_eq!(cleaned.head_digest(), cleaned_journal.head().record_digest);
    }

    #[test]
    fn replay_commitments_reconstruct_complete_normal_termination_material() {
        let mut journal = declared();
        let mut expected_actions = Vec::new();
        for (index, action) in StageAction::ORDERED.into_iter().enumerate() {
            let parent = journal.head();
            let intent_payload = journal.canonical_action_intent_digest(action);
            journal = apply(
                &journal,
                &journal.plan_action_intent(action, intent_payload).unwrap(),
            );
            let intent = journal.head();
            let observed_payload = digest(180 + index as u8);
            journal = apply(
                &journal,
                &journal
                    .plan_action_observed(action, observed_payload)
                    .unwrap(),
            );
            let observed = journal.head();
            expected_actions.push((
                action,
                parent,
                intent_payload,
                intent,
                observed_payload,
                observed,
            ));
        }

        let armed_digest = expected_actions.last().unwrap().4;
        let branch = journal.head();
        journal = apply(
            &journal,
            &journal
                .plan_termination_intent(
                    StageTerminationKind::TimedOut,
                    8_000,
                    8_007,
                    Some(armed_digest),
                )
                .unwrap(),
        );
        let termination_intent = journal.head();
        let terminal_payload = digest(220);
        journal = apply(
            &journal,
            &journal
                .plan_terminal(StageTerminationKind::TimedOut, terminal_payload)
                .unwrap(),
        );
        let cleanup_payload = digest(221);
        journal = apply(
            &journal,
            &journal
                .plan_cleanup(StageTerminationKind::TimedOut, cleanup_payload)
                .unwrap(),
        );

        let summary = replay_summary(&journal);
        assert_eq!(summary.declaration_digest(), digest(8));
        for (action, parent, intent_payload, intent, observed_payload, observed) in expected_actions
        {
            let commitment = summary.action_commitment(action).unwrap();
            assert_eq!(commitment.action(), action);
            assert_eq!(commitment.intent_sequence(), intent.sequence);
            assert_eq!(commitment.intent_parent_sequence(), parent.sequence);
            assert_eq!(commitment.intent_parent_digest(), parent.record_digest);
            assert_eq!(commitment.intent_payload_digest(), intent_payload);
            assert_eq!(commitment.intent_record_digest(), intent.record_digest);
            assert_eq!(commitment.observed_sequence(), Some(observed.sequence));
            assert_eq!(commitment.observed_parent_sequence(), Some(intent.sequence));
            assert_eq!(
                commitment.observed_parent_digest(),
                Some(intent.record_digest)
            );
            assert_eq!(commitment.observed_payload_digest(), Some(observed_payload));
            assert_eq!(
                commitment.observed_record_digest(),
                Some(observed.record_digest)
            );
        }

        let termination = summary.termination_commitment().unwrap();
        assert_eq!(termination.kind(), StageTerminationKind::TimedOut);
        assert_eq!(termination.branch_head_sequence(), branch.sequence);
        assert_eq!(termination.branch_head_digest(), branch.record_digest);
        assert_eq!(termination.intent_sequence(), termination_intent.sequence);
        assert_eq!(
            termination.intent_record_digest(),
            termination_intent.record_digest
        );
        assert_eq!(termination.requested_at_unix_ms(), 8_000);
        assert_eq!(termination.recorded_at_unix_ms(), 8_007);

        let VerifiedNormalTerminationReplay::Complete(material) =
            summary.normal_termination_material()
        else {
            panic!("canonical cleaned replay did not produce termination material");
        };
        assert_eq!(material.termination_kind(), StageTerminationKind::TimedOut);
        assert_eq!(material.branch_head_sequence(), branch.sequence);
        assert_eq!(material.branch_head_digest(), branch.record_digest);
        assert_eq!(material.intent_sequence(), termination_intent.sequence);
        assert_eq!(
            material.intent_record_digest(),
            termination_intent.record_digest
        );
        assert_eq!(material.requested_at_unix_ms(), 8_000);
        assert_eq!(material.recorded_at_unix_ms(), 8_007);
        assert_eq!(material.armed_receipt_digest(), Some(armed_digest));
        assert_eq!(material.terminal_payload_digest(), terminal_payload);
        assert_eq!(material.cleanup_payload_digest(), cleanup_payload);
    }

    #[test]
    fn partial_and_unobserved_replays_return_typed_pending_material() {
        let declared = declared();
        assert_normal_pending(
            replay_summary(&declared),
            StageJournalReplayStage::Ready,
            NormalTerminationPendingReason::NotTerminating,
            None,
        );

        let prepare_intent = apply(
            &declared,
            &declared
                .plan_action_intent(
                    StageAction::Prepare,
                    declared.canonical_action_intent_digest(StageAction::Prepare),
                )
                .unwrap(),
        );
        assert_normal_pending(
            replay_summary(&prepare_intent),
            StageJournalReplayStage::Pending,
            NormalTerminationPendingReason::ObservationPending,
            Some(StageAction::Prepare),
        );
        let terminating_pending = apply(
            &prepare_intent,
            &prepare_intent
                .plan_termination_intent(StageTerminationKind::Cancelled, 9_000, 9_001, None)
                .unwrap(),
        );
        assert_normal_pending(
            replay_summary(&terminating_pending),
            StageJournalReplayStage::Terminating,
            NormalTerminationPendingReason::ObservationPending,
            Some(StageAction::Prepare),
        );
        let terminal_pending = apply(
            &terminating_pending,
            &terminating_pending
                .plan_terminal(StageTerminationKind::Cancelled, digest(222))
                .unwrap(),
        );
        let cleaned_pending = apply(
            &terminal_pending,
            &terminal_pending
                .plan_cleanup(StageTerminationKind::Cancelled, digest(223))
                .unwrap(),
        );
        assert_normal_pending(
            replay_summary(&cleaned_pending),
            StageJournalReplayStage::Cleaned,
            NormalTerminationPendingReason::ObservationPending,
            Some(StageAction::Prepare),
        );

        let terminating_complete = apply(
            &declared,
            &declared
                .plan_termination_intent(StageTerminationKind::TimedOut, 9_100, 9_102, None)
                .unwrap(),
        );
        assert_normal_pending(
            replay_summary(&terminating_complete),
            StageJournalReplayStage::Terminating,
            NormalTerminationPendingReason::TerminationIncomplete,
            None,
        );
        let terminal_complete = apply(
            &terminating_complete,
            &terminating_complete
                .plan_terminal(StageTerminationKind::TimedOut, digest(224))
                .unwrap(),
        );
        assert_normal_pending(
            replay_summary(&terminal_complete),
            StageJournalReplayStage::Terminal,
            NormalTerminationPendingReason::TerminationIncomplete,
            None,
        );
    }

    #[test]
    fn full_replay_requires_declaration_and_action_intent_domains() {
        let journal = declared();
        assert_code(
            StageJournal::verified_replay_summary_from_bytes(
                journal.bytes(),
                &binding(),
                digest(9),
            ),
            "stage_journal_declaration_digest_mismatch",
        );
        assert_code(
            journal.plan_action_intent(StageAction::Prepare, digest(225)),
            "stage_journal_action_intent_payload_invalid",
        );
    }

    #[test]
    fn post_termination_observation_retains_its_distinct_parent_commitment() {
        let declared = declared();
        let action_intent = apply(
            &declared,
            &declared
                .plan_action_intent(
                    StageAction::Prepare,
                    declared.canonical_action_intent_digest(StageAction::Prepare),
                )
                .unwrap(),
        );
        let branch = action_intent.head();
        let terminating = apply(
            &action_intent,
            &action_intent
                .plan_termination_intent(StageTerminationKind::Cancelled, 9_200, 9_203, None)
                .unwrap(),
        );
        let termination_intent = terminating.head();
        let observed_payload = digest(226);
        let observed = apply(
            &terminating,
            &terminating
                .plan_action_observed(StageAction::Prepare, observed_payload)
                .unwrap(),
        );
        let terminal = apply(
            &observed,
            &observed
                .plan_terminal(StageTerminationKind::Cancelled, digest(227))
                .unwrap(),
        );
        let cleaned = apply(
            &terminal,
            &terminal
                .plan_cleanup(StageTerminationKind::Cancelled, digest(228))
                .unwrap(),
        );

        let summary = replay_summary(&cleaned);
        let commitment = summary.action_commitment(StageAction::Prepare).unwrap();
        assert_eq!(commitment.intent_sequence(), branch.sequence);
        assert_eq!(commitment.intent_record_digest(), branch.record_digest);
        assert_eq!(
            commitment.observed_parent_sequence(),
            Some(termination_intent.sequence)
        );
        assert_eq!(
            commitment.observed_parent_digest(),
            Some(termination_intent.record_digest)
        );
        assert_eq!(commitment.observed_payload_digest(), Some(observed_payload));
        assert!(matches!(
            summary.normal_termination_material(),
            VerifiedNormalTerminationReplay::Complete(_)
        ));
    }

    #[test]
    fn replay_summary_tracks_pending_and_post_termination_arm_observation() {
        let declared = declared();
        let prepare_intent = apply(
            &declared,
            &declared
                .plan_action_intent(
                    StageAction::Prepare,
                    declared.canonical_action_intent_digest(StageAction::Prepare),
                )
                .unwrap(),
        );
        let prepare_terminating = apply(
            &prepare_intent,
            &prepare_intent
                .plan_termination_intent(StageTerminationKind::Cancelled, 3_000, 3_001, None)
                .unwrap(),
        );
        let pending = replay_summary(&prepare_terminating);
        assert_eq!(pending.stage(), StageJournalReplayStage::Terminating);
        assert_eq!(pending.pending_observation(), Some(StageAction::Prepare));
        assert_eq!(pending.armed_receipt_digest(), None);

        let terminal_with_unobserved_prepare = apply(
            &prepare_terminating,
            &prepare_terminating
                .plan_terminal(StageTerminationKind::Cancelled, digest(203))
                .unwrap(),
        );
        let terminal_pending = replay_summary(&terminal_with_unobserved_prepare);
        assert_eq!(terminal_pending.stage(), StageJournalReplayStage::Terminal);
        assert_eq!(
            terminal_pending.pending_observation(),
            Some(StageAction::Prepare)
        );
        let cleaned_with_unobserved_prepare = apply(
            &terminal_with_unobserved_prepare,
            &terminal_with_unobserved_prepare
                .plan_cleanup(StageTerminationKind::Cancelled, digest(204))
                .unwrap(),
        );
        assert_eq!(
            replay_summary(&cleaned_with_unobserved_prepare).pending_observation(),
            Some(StageAction::Prepare)
        );

        let before_arm = prefix_before(StageAction::Arm);
        let arm_intent = apply(
            &before_arm,
            &before_arm
                .plan_action_intent(
                    StageAction::Arm,
                    before_arm.canonical_action_intent_digest(StageAction::Arm),
                )
                .unwrap(),
        );
        let arm_terminating = apply(
            &arm_intent,
            &arm_intent
                .plan_termination_intent(StageTerminationKind::Cancelled, 4_000, 4_002, None)
                .unwrap(),
        );
        let pending_arm = replay_summary(&arm_terminating);
        assert_eq!(pending_arm.pending_observation(), Some(StageAction::Arm));
        assert_eq!(pending_arm.armed_receipt_digest(), None);

        let observed_arm_digest = digest(202);
        let observed_after_termination = apply(
            &arm_terminating,
            &arm_terminating
                .plan_action_observed(StageAction::Arm, observed_arm_digest)
                .unwrap(),
        );
        let observed = replay_summary(&observed_after_termination);
        assert_eq!(observed.stage(), StageJournalReplayStage::Terminating);
        assert_eq!(observed.pending_observation(), None);
        assert_eq!(observed.armed_receipt_digest(), Some(observed_arm_digest));
    }

    #[test]
    fn replay_summary_reopens_bytes_and_rejects_private_state_drift() {
        let journal = declared();
        let mut drifted = journal.clone();
        drifted.records[0].payload_digest = digest(230);
        assert_code(
            drifted.verified_replay_summary(digest(8)),
            "stage_journal_in_memory_state_mismatch",
        );

        let mut damaged_bytes = journal.bytes().to_vec();
        damaged_bytes[FIRST_RECORD_OFFSET + 172] ^= 1;
        assert_code(
            StageJournal::verified_replay_summary_from_bytes(&damaged_bytes, &binding(), digest(8)),
            "stage_journal_record_digest_invalid",
        );
    }

    #[test]
    fn termination_can_branch_from_every_action_head() {
        let declared = declared();
        let terminated = apply(
            &declared,
            &declared
                .plan_termination_intent(StageTerminationKind::TimedOut, 10, 11, None)
                .unwrap(),
        );
        assert_eq!(
            terminated.head().termination_kind,
            Some(StageTerminationKind::TimedOut)
        );

        for (index, action) in StageAction::ORDERED.into_iter().enumerate() {
            let before = prefix_before(action);
            let intent = apply(
                &before,
                &before
                    .plan_action_intent(action, before.canonical_action_intent_digest(action))
                    .unwrap(),
            );
            let terminating = apply(
                &intent,
                &intent
                    .plan_termination_intent(StageTerminationKind::Cancelled, 20, 20, None)
                    .unwrap(),
            );
            let observed = apply(
                &terminating,
                &terminating
                    .plan_action_observed(action, digest(90 + index as u8))
                    .unwrap(),
            );
            let terminal = apply(
                &observed,
                &observed
                    .plan_terminal(StageTerminationKind::Cancelled, digest(100))
                    .unwrap(),
            );
            assert_eq!(terminal.head().kind, StageJournalRecordKind::Terminal);

            let observed_head = action_pair(&before, action, 110 + (index as u8 * 2));
            let armed = if action == StageAction::Arm {
                Some(observed_head.records.last().unwrap().payload_digest)
            } else {
                None
            };
            let terminating = apply(
                &observed_head,
                &observed_head
                    .plan_termination_intent(StageTerminationKind::TimedOut, 30, 31, armed)
                    .unwrap(),
            );
            assert_eq!(
                terminating.records.last().unwrap().stage_head_sequence,
                observed_head.head().sequence
            );
        }
    }

    #[test]
    fn armed_digest_is_required_only_for_an_already_observed_arm() {
        let before_arm = prefix_before(StageAction::Arm);
        assert_code(
            before_arm.plan_termination_intent(
                StageTerminationKind::Cancelled,
                1,
                1,
                Some(digest(120)),
            ),
            "stage_journal_termination_armed_digest_mismatch",
        );
        let arm_intent = apply(
            &before_arm,
            &before_arm
                .plan_action_intent(
                    StageAction::Arm,
                    before_arm.canonical_action_intent_digest(StageAction::Arm),
                )
                .unwrap(),
        );
        assert_code(
            arm_intent.plan_termination_intent(
                StageTerminationKind::Cancelled,
                1,
                1,
                Some(digest(122)),
            ),
            "stage_journal_termination_armed_digest_mismatch",
        );
        let armed = apply(
            &arm_intent,
            &arm_intent
                .plan_action_observed(StageAction::Arm, digest(123))
                .unwrap(),
        );
        assert_code(
            armed.plan_termination_intent(StageTerminationKind::Cancelled, 1, 1, None),
            "stage_journal_termination_armed_digest_mismatch",
        );
        assert_code(
            armed.plan_termination_intent(StageTerminationKind::Cancelled, 1, 1, Some(digest(124))),
            "stage_journal_termination_armed_digest_mismatch",
        );
        assert!(armed
            .plan_termination_intent(StageTerminationKind::Cancelled, 1, 1, Some(digest(123)),)
            .is_ok());
    }

    #[test]
    fn termination_intent_closes_new_actions_but_allows_one_pending_observation() {
        let declared = declared();
        let intent = apply(
            &declared,
            &declared
                .plan_action_intent(
                    StageAction::Prepare,
                    declared.canonical_action_intent_digest(StageAction::Prepare),
                )
                .unwrap(),
        );
        let terminating = apply(
            &intent,
            &intent
                .plan_termination_intent(StageTerminationKind::Cancelled, 50, 55, None)
                .unwrap(),
        );
        assert_code(
            terminating.plan_action_intent(
                StageAction::Prepare,
                terminating.canonical_action_intent_digest(StageAction::Prepare),
            ),
            "stage_journal_action_intent_after_termination",
        );
        assert_code(
            terminating.plan_termination_intent(StageTerminationKind::Cancelled, 56, 57, None),
            "stage_journal_duplicate_termination_intent",
        );
        assert_code(
            terminating.plan_action_observed(StageAction::BridgeCreate, digest(32)),
            "stage_journal_post_termination_observation_invalid",
        );
        let observed = apply(
            &terminating,
            &terminating
                .plan_action_observed(StageAction::Prepare, digest(33))
                .unwrap(),
        );
        assert_code(
            observed.plan_action_observed(StageAction::Prepare, digest(34)),
            "stage_journal_transition_invalid",
        );
        assert!(observed
            .plan_terminal(StageTerminationKind::Cancelled, digest(35))
            .is_ok());
    }

    #[test]
    fn termination_can_become_terminal_without_pending_observation() {
        let journal = declared();
        let terminating = apply(
            &journal,
            &journal
                .plan_termination_intent(StageTerminationKind::TimedOut, 100, 101, None)
                .unwrap(),
        );
        let terminal = apply(
            &terminating,
            &terminating
                .plan_terminal(StageTerminationKind::TimedOut, digest(50))
                .unwrap(),
        );
        let cleaned = apply(
            &terminal,
            &terminal
                .plan_cleanup(StageTerminationKind::TimedOut, digest(51))
                .unwrap(),
        );
        assert_code(
            cleaned.plan_cleanup(StageTerminationKind::TimedOut, digest(52)),
            "stage_journal_record_after_terminal_state",
        );
    }

    #[test]
    fn append_verifier_rejects_missing_extra_changed_and_stale_prefixes() {
        let journal = declared();
        let append = journal
            .plan_action_intent(
                StageAction::Prepare,
                journal.canonical_action_intent_digest(StageAction::Prepare),
            )
            .unwrap();
        assert_eq!(append.prior_byte_len(), journal.bytes().len());
        assert_eq!(append.prior_head_sequence(), journal.head().sequence);
        assert_eq!(append.prior_head_digest(), journal.head().record_digest);
        assert_code(
            journal.verify_reopened_append(&append, journal.bytes()),
            "stage_journal_append_extent_invalid",
        );

        let mut expected = journal.bytes().to_vec();
        expected.extend_from_slice(append.record_bytes());
        let next = journal.verify_reopened_append(&append, &expected).unwrap();
        let mut extra = expected.clone();
        extra.extend_from_slice(append.record_bytes());
        assert_code(
            journal.verify_reopened_append(&append, &extra),
            "stage_journal_append_extent_invalid",
        );

        let mut changed_prefix = expected.clone();
        changed_prefix[16] ^= 1;
        assert_code(
            journal.verify_reopened_append(&append, &changed_prefix),
            "stage_journal_append_prefix_changed",
        );
        let mut changed_record = expected;
        *changed_record.last_mut().unwrap() ^= 1;
        assert_code(
            journal.verify_reopened_append(&append, &changed_record),
            "stage_journal_append_record_mismatch",
        );
        assert_code(
            next.verify_reopened_append(&append, next.bytes()),
            "stage_journal_append_stale_plan",
        );
    }

    #[test]
    fn every_torn_append_tail_is_rejected() {
        let journal = declared();
        let append = journal
            .plan_action_intent(
                StageAction::Prepare,
                journal.canonical_action_intent_digest(StageAction::Prepare),
            )
            .unwrap();
        for cut in 0..RECORD_LEN {
            let mut bytes = journal.bytes().to_vec();
            bytes.extend_from_slice(&append.record_bytes()[..cut]);
            assert!(journal.verify_reopened_append(&append, &bytes).is_err());
            if cut > 0 {
                assert_code(
                    StageJournal::reopen(&bytes, &binding()),
                    "stage_journal_record_torn_tail",
                );
            }
        }
    }

    #[test]
    fn decoder_rejects_unknown_discriminators_flags_and_reserved_bytes() {
        let journal = declared();
        for (relative_offset, value, expected) in [
            (5, 0xff, "stage_journal_record_kind_unknown"),
            (6, 0xff, "stage_journal_record_action_unknown"),
            (7, 0xff, "stage_journal_record_termination_unknown"),
            (8, 0x80, "stage_journal_record_flags_unknown"),
            (9, 1, "stage_journal_record_reserved_nonzero"),
        ] {
            let mut bytes = journal.bytes().to_vec();
            bytes[FIRST_RECORD_OFFSET + relative_offset] = value;
            assert_code(StageJournal::reopen(&bytes, &binding()), expected);
        }
        let mut header_reserved = journal.bytes().to_vec();
        header_reserved[14] = 1;
        assert_code(
            StageJournal::reopen(&header_reserved, &binding()),
            "stage_journal_header_reserved_nonzero",
        );

        let mut known_but_unexpected_action = journal.bytes().to_vec();
        known_but_unexpected_action[FIRST_RECORD_OFFSET + 6] = StageAction::Prepare.code();
        recompute_record_digest(&mut known_but_unexpected_action, 0);
        assert_code(
            StageJournal::reopen(&known_but_unexpected_action, &binding()),
            "stage_journal_record_unexpected_discriminator",
        );

        let mut noncanonical_armed_field = journal.bytes().to_vec();
        noncanonical_armed_field[FIRST_RECORD_OFFSET + 140] = 1;
        assert_code(
            StageJournal::reopen(&noncanonical_armed_field, &binding()),
            "stage_journal_record_armed_digest_without_flag",
        );
    }

    #[test]
    fn duplicate_swapped_sequence_and_previous_digest_records_fail_closed() {
        let journal = action_pair(&declared(), StageAction::Prepare, 70);
        let mut duplicate = journal.bytes().to_vec();
        let first = duplicate[record_offset(0)..record_offset(1)].to_vec();
        duplicate.extend_from_slice(&first);
        assert!(StageJournal::reopen(&duplicate, &binding()).is_err());

        let mut swapped = journal.bytes().to_vec();
        let first = swapped[record_offset(1)..record_offset(2)].to_vec();
        let second = swapped[record_offset(2)..record_offset(3)].to_vec();
        swapped[record_offset(1)..record_offset(2)].copy_from_slice(&second);
        swapped[record_offset(2)..record_offset(3)].copy_from_slice(&first);
        assert!(StageJournal::reopen(&swapped, &binding()).is_err());

        let mut sequence = journal.bytes().to_vec();
        sequence[record_offset(1) + 19] = 9;
        recompute_record_digest(&mut sequence, 1);
        assert_code(
            StageJournal::reopen(&sequence, &binding()),
            "stage_journal_sequence_invalid",
        );

        let mut previous = journal.bytes().to_vec();
        previous[record_offset(1) + 76] ^= 1;
        recompute_record_digest(&mut previous, 1);
        assert_code(
            StageJournal::reopen(&previous, &binding()),
            "stage_journal_previous_digest_mismatch",
        );
    }

    #[test]
    fn every_header_binding_is_required_and_every_record_rebind_is_rejected() {
        let journal = declared();
        assert_eq!(read_digest(journal.bytes(), 48), digest(2));
        let other_service_instance = StageJournalBinding::new(
            digest(1),
            digest(42),
            digest(3),
            digest(4),
            digest(5),
            digest(6),
            digest(7),
            digest(8),
        )
        .unwrap();
        assert_ne!(binding().digest(), other_service_instance.digest());
        for offset in [16, 48, 80, 112, 144, 176, 208, 240] {
            let mut bytes = journal.bytes().to_vec();
            bytes[offset] ^= 1;
            assert_code(
                StageJournal::reopen(&bytes, &binding()),
                "stage_journal_header_binding_mismatch",
            );
        }

        let mut binding_digest = journal.bytes().to_vec();
        binding_digest[272] ^= 1;
        assert_code(
            StageJournal::reopen(&binding_digest, &binding()),
            "stage_journal_header_binding_digest_invalid",
        );

        let mut record_binding = journal.bytes().to_vec();
        record_binding[FIRST_RECORD_OFFSET + 44] ^= 1;
        recompute_record_digest(&mut record_binding, 0);
        assert_code(
            StageJournal::reopen(&record_binding, &binding()),
            "stage_journal_record_binding_mismatch",
        );
    }

    #[test]
    fn termination_head_and_record_digest_tampering_fail_closed() {
        let journal = declared();
        let terminating = apply(
            &journal,
            &journal
                .plan_termination_intent(StageTerminationKind::Cancelled, 5, 6, None)
                .unwrap(),
        );
        let termination_index = terminating.record_count() - 1;

        let mut head_sequence = terminating.bytes().to_vec();
        head_sequence[record_offset(termination_index) + 43] ^= 1;
        recompute_record_digest(&mut head_sequence, termination_index);
        assert_code(
            StageJournal::reopen(&head_sequence, &binding()),
            "stage_journal_termination_head_mismatch",
        );

        let mut head_digest = terminating.bytes().to_vec();
        head_digest[record_offset(termination_index) + 108] ^= 1;
        recompute_record_digest(&mut head_digest, termination_index);
        assert_code(
            StageJournal::reopen(&head_digest, &binding()),
            "stage_journal_termination_head_mismatch",
        );

        let mut record_digest = terminating.bytes().to_vec();
        *record_digest.last_mut().unwrap() ^= 1;
        assert_code(
            StageJournal::reopen(&record_digest, &binding()),
            "stage_journal_record_digest_invalid",
        );
    }

    #[test]
    fn termination_timing_kind_and_cleanup_order_are_strict() {
        let journal = declared();
        assert_code(
            journal.plan_termination_intent(StageTerminationKind::Cancelled, 0, 1, None),
            "stage_journal_termination_intent_noncanonical",
        );
        assert_code(
            journal.plan_termination_intent(StageTerminationKind::Cancelled, 2, 1, None),
            "stage_journal_termination_intent_noncanonical",
        );
        assert_code(
            journal.plan_cleanup(StageTerminationKind::Cancelled, digest(90)),
            "stage_journal_transition_invalid",
        );

        let terminating = apply(
            &journal,
            &journal
                .plan_termination_intent(StageTerminationKind::Cancelled, 2, 2, None)
                .unwrap(),
        );
        assert_code(
            terminating.plan_terminal(StageTerminationKind::TimedOut, digest(91)),
            "stage_journal_terminal_kind_mismatch",
        );
        let terminal = apply(
            &terminating,
            &terminating
                .plan_terminal(StageTerminationKind::Cancelled, digest(92))
                .unwrap(),
        );
        assert_code(
            terminal.plan_cleanup(StageTerminationKind::TimedOut, digest(93)),
            "stage_journal_cleanup_kind_mismatch",
        );
    }

    #[test]
    fn missing_noncanonical_and_oversized_journals_fail_closed() {
        assert_code(
            StageJournal::reopen(&[], &binding()),
            "stage_journal_header_truncated",
        );
        assert_code(
            StageJournal::reopen(&encode_header(&binding()), &binding()),
            "stage_journal_run_declaration_missing",
        );
        assert_code(
            StageJournal::reopen(&vec![0; STAGE_JOURNAL_MAX_BYTES + 1], &binding()),
            "stage_journal_oversized",
        );

        let journal = declared();
        let mut wrong_magic = journal.bytes().to_vec();
        wrong_magic[0] ^= 1;
        assert_code(
            StageJournal::reopen(&wrong_magic, &binding()),
            "stage_journal_header_magic_invalid",
        );
        let mut wrong_layout = journal.bytes().to_vec();
        wrong_layout[13] ^= 1;
        assert_code(
            StageJournal::reopen(&wrong_layout, &binding()),
            "stage_journal_header_layout_invalid",
        );
        let mut downgraded = journal.bytes().to_vec();
        downgraded[8..10].copy_from_slice(&1u16.to_be_bytes());
        assert_code(
            StageJournal::reopen(&downgraded, &binding()),
            "stage_journal_header_version_invalid",
        );
        assert_code(
            StageJournal::persisted_binding(&journal.bytes()[..272]),
            "stage_journal_header_truncated",
        );
        assert_code(
            StageJournalBinding::new(
                [0; 32],
                digest(2),
                digest(3),
                digest(4),
                digest(5),
                digest(6),
                digest(7),
                digest(8),
            ),
            "stage_journal_binding_digest_zero",
        );
        assert_code(
            StageJournalBinding::new(
                digest(1),
                digest(2),
                digest(3),
                digest(4),
                digest(5),
                digest(6),
                digest(7),
                [0; 32],
            ),
            "stage_journal_binding_digest_zero",
        );
        assert_code(
            StageJournalBinding::new(
                digest(1),
                [0; 32],
                digest(3),
                digest(4),
                digest(5),
                digest(6),
                digest(7),
                digest(8),
            ),
            "stage_journal_binding_digest_zero",
        );
    }
}
