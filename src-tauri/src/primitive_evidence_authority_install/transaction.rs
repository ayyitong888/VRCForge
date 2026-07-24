use super::*;

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(super) struct JournalContractProjection {
    pub(super) schema: &'static str,
    pub(super) anchor_path: String,
    pub(super) anchor_source: &'static str,
    pub(super) anchor_handle_held: bool,
    pub(super) anchor_stable_object_identity_required: bool,
    pub(super) anchor_reparse_points_rejected: bool,
    pub(super) path: String,
    pub(super) transaction_sha256: String,
    pub(super) plan_sha256: String,
    pub(super) create_new: bool,
    pub(super) create_relative_to_anchor_handle: bool,
    pub(super) preexisting_path_rejected: bool,
    pub(super) exact_security_required: bool,
    pub(super) owner_sid: &'static str,
    pub(super) write_through: bool,
    pub(super) flush_file_after_every_transition: bool,
    pub(super) flush_parent_after_create: bool,
    pub(super) startup_recovery_required: bool,
    pub(super) terminal_states: [&'static str; 3],
    pub(super) identical_terminal_is_idempotent: bool,
    pub(super) conflicting_terminal_rejected: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum JournalTerminal {
    Committed,
    RolledBack,
    Contained,
}

impl JournalTerminal {
    fn as_str(self) -> &'static str {
        match self {
            Self::Committed => "committed",
            Self::RolledBack => "rolledBack",
            Self::Contained => "contained",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum StartupRecoveryDisposition {
    Clean,
    RecoveredRolledBack,
    RecoveredContained,
}

impl StartupRecoveryDisposition {
    fn as_report(self) -> Option<&'static str> {
        match self {
            Self::Clean => None,
            Self::RecoveredRolledBack => Some("rolledBack"),
            Self::RecoveredContained => Some("contained"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum IdempotentWriteDisposition {
    Created,
    AlreadyIdentical,
}

impl IdempotentWriteDisposition {
    fn as_str(self) -> &'static str {
        match self {
            Self::Created => "created",
            Self::AlreadyIdentical => "alreadyIdentical",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum JournalTransition {
    StepStarted,
    StepCompleted,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum MaintenanceApplyFailure {
    BeforeIrreversibleCommit,
    AfterIrreversibleCommit,
}

pub(super) trait MaintenanceExecutor {
    fn recover_startup(
        &mut self,
        journal: &JournalContractProjection,
    ) -> Result<StartupRecoveryDisposition, ()>;
    fn create_journal(&mut self, journal: &JournalContractProjection) -> Result<(), ()>;
    fn record_transition(
        &mut self,
        step: &AuthorityMaintenanceStep,
        transition: JournalTransition,
    ) -> Result<(), ()>;
    fn apply(
        &mut self,
        step: &AuthorityMaintenanceStep,
        lease: &VerifiedMaintenanceLease,
    ) -> Result<(), MaintenanceApplyFailure>;
    fn cleanup_failed_apply(&mut self, step: &AuthorityMaintenanceStep) -> Result<(), ()>;
    fn rollback_completed(&mut self, step: &AuthorityMaintenanceStep) -> Result<(), ()>;
    fn contain_post_commit(&mut self, failed_step: &AuthorityMaintenanceStep) -> Result<(), ()>;
    fn seal_recovery_once(
        &mut self,
        path: &str,
        content_sha256: [u8; 32],
    ) -> Result<IdempotentWriteDisposition, ()>;
    fn write_journal_terminal(
        &mut self,
        terminal: JournalTerminal,
    ) -> Result<IdempotentWriteDisposition, ()>;
}

#[cfg(test)]
pub(super) fn execute_with_test_executor<E: MaintenanceExecutor>(
    preview: &AuthorityMaintenancePreview,
    lease: &mut VerifiedMaintenanceLease,
    executor: &mut E,
) -> AuthorityMaintenanceExecutionReport {
    if !lease.is_live()
        || lease.plan_sha256 != preview.plan_sha256().unwrap_or([0; 32])
        || lease.generation != preview.generation_sha256().unwrap_or([0; 32])
    {
        return journal_failure_report(None, None, Vec::new());
    }
    let startup_recovery = match executor.recover_startup(&preview.journal) {
        Ok(value) => value,
        Err(()) => return journal_failure_report(None, None, Vec::new()),
    };
    if executor.create_journal(&preview.journal).is_err() {
        return journal_failure_report(startup_recovery.as_report(), None, Vec::new());
    }
    let mut completed = vec!["createDurableJournal"];
    for step in preview.steps.iter().skip(1) {
        if executor
            .record_transition(step, JournalTransition::StepStarted)
            .is_err()
        {
            return journal_failure_report(startup_recovery.as_report(), Some(step.id), completed);
        }
        if let Err(apply_failure) = executor.apply(step, lease) {
            let mut rollback_failures = Vec::new();
            let post_commit = crossed_irreversible_commit(preview, completed.len())
                || apply_failure == MaintenanceApplyFailure::AfterIrreversibleCommit;
            let failed_step_cleanup;
            let mut recovery_seal_required;
            if post_commit {
                recovery_seal_required = true;
                if executor.contain_post_commit(step).is_ok() {
                    failed_step_cleanup = "postCommitContained";
                } else {
                    failed_step_cleanup = "uncertain";
                    rollback_failures.push("postCommitContainment");
                }
            } else {
                failed_step_cleanup = if executor.cleanup_failed_apply(step).is_ok() {
                    rollback_resolution(&step.failed_apply_cleanup)
                } else {
                    rollback_failures.push(step.id);
                    "uncertain"
                };
                recovery_seal_required =
                    rollback_requires_recovery_seal(&step.failed_apply_cleanup);
                for completed_step in preview.steps[1..completed.len()].iter().rev() {
                    recovery_seal_required |=
                        rollback_requires_recovery_seal(&completed_step.rollback);
                    if executor.rollback_completed(completed_step).is_err() {
                        rollback_failures.push(completed_step.id);
                    }
                }
            }
            let mut recovery_seal = None;
            if recovery_seal_required || !rollback_failures.is_empty() {
                let seal_digest = recovery_seal_digest(preview, step.id, &rollback_failures);
                match executor.seal_recovery_once(&preview.layout.recovery_manifest, seal_digest) {
                    Ok(value) => recovery_seal = Some(value.as_str()),
                    Err(()) => rollback_failures.push("recoverySeal"),
                }
            }
            let terminal = if !rollback_failures.is_empty() {
                None
            } else if recovery_seal_required {
                Some(JournalTerminal::Contained)
            } else {
                Some(JournalTerminal::RolledBack)
            };
            let terminal_write = terminal
                .and_then(|value| executor.write_journal_terminal(value).ok().map(|_| value));
            if terminal.is_some() && terminal_write.is_none() {
                rollback_failures.push("journalTerminal");
            }
            let status = terminal_write
                .map(JournalTerminal::as_str)
                .unwrap_or("recoveryRequired");
            return AuthorityMaintenanceExecutionReport {
                status,
                startup_recovery: startup_recovery.as_report(),
                journal_terminal: terminal_write.map(JournalTerminal::as_str),
                recovery_seal,
                trusted_boundary_ready: false,
                completed_steps: completed,
                failed_step: Some(step.id),
                failed_step_cleanup: Some(failed_step_cleanup),
                failure_cleanup_verified: Some(rollback_failures.is_empty()),
                rollback_failures,
                blockers: if status == "recoveryRequired" {
                    vec![
                        "authority_maintenance_cleanup_uncertain",
                        "authority_protected_readback_required",
                    ]
                } else if post_commit {
                    vec!["authority_post_commit_protected_readback_required"]
                } else {
                    vec!["authority_protected_readback_required"]
                },
            };
        }
        if executor
            .record_transition(step, JournalTransition::StepCompleted)
            .is_err()
        {
            return journal_failure_report(startup_recovery.as_report(), Some(step.id), completed);
        }
        completed.push(step.id);
    }
    let terminal = match executor.write_journal_terminal(JournalTerminal::Committed) {
        Ok(_) => Some(JournalTerminal::Committed),
        Err(()) => None,
    };
    AuthorityMaintenanceExecutionReport {
        status: terminal
            .map(JournalTerminal::as_str)
            .unwrap_or("recoveryRequired"),
        startup_recovery: startup_recovery.as_report(),
        journal_terminal: terminal.map(JournalTerminal::as_str),
        recovery_seal: None,
        trusted_boundary_ready: false,
        completed_steps: completed,
        failed_step: None,
        failed_step_cleanup: None,
        failure_cleanup_verified: None,
        rollback_failures: if terminal.is_some() {
            Vec::new()
        } else {
            vec!["journalTerminal"]
        },
        blockers: if terminal.is_some() {
            vec!["authority_protected_readback_required"]
        } else {
            vec![
                "authority_maintenance_journal_uncertain",
                "authority_protected_readback_required",
            ]
        },
    }
}

#[cfg(test)]
fn crossed_irreversible_commit(
    preview: &AuthorityMaintenancePreview,
    completed_len: usize,
) -> bool {
    preview.steps[1..completed_len].iter().any(|completed| {
        matches!(
            &completed.action,
            AuthorityMaintenanceAction::FinalizeRetirementTombstoneAtomic {
                irreversible_commit: true,
                ..
            }
        )
    })
}

#[cfg(test)]
fn journal_failure_report(
    startup_recovery: Option<&'static str>,
    failed_step: Option<&'static str>,
    completed_steps: Vec<&'static str>,
) -> AuthorityMaintenanceExecutionReport {
    AuthorityMaintenanceExecutionReport {
        status: "recoveryRequired",
        startup_recovery,
        journal_terminal: None,
        recovery_seal: None,
        trusted_boundary_ready: false,
        completed_steps,
        failed_step,
        failed_step_cleanup: Some("uncertain"),
        failure_cleanup_verified: Some(false),
        rollback_failures: vec!["journalDurability"],
        blockers: vec![
            "authority_maintenance_journal_uncertain",
            "authority_protected_readback_required",
        ],
    }
}

pub(super) fn rollback_resolution(rollback: &AuthorityRollbackAction) -> &'static str {
    match rollback {
        AuthorityRollbackAction::None => "noMutation",
        AuthorityRollbackAction::SealGenerationConsumed { .. }
        | AuthorityRollbackAction::DiscardManifestAndSealGenerationConsumed { .. } => {
            "recoverySealed"
        }
        AuthorityRollbackAction::RestoreProtectedDirectoryState { .. }
        | AuthorityRollbackAction::RemoveNewServiceRegistration { .. }
        | AuthorityRollbackAction::RestorePriorServiceConfiguration { .. }
        | AuthorityRollbackAction::RestoreRetiredServiceConfiguration { .. }
        | AuthorityRollbackAction::DiscardCreatedManifest { .. }
        | AuthorityRollbackAction::MarkRetirementAbortedNoReuse { .. } => "rolledBack",
        AuthorityRollbackAction::RestoreActiveHeadAndSealGenerationConsumed { .. } => {
            "headRestoredAndRecoverySealed"
        }
    }
}

pub(super) fn rollback_requires_recovery_seal(rollback: &AuthorityRollbackAction) -> bool {
    matches!(
        rollback,
        AuthorityRollbackAction::SealGenerationConsumed { .. }
            | AuthorityRollbackAction::DiscardManifestAndSealGenerationConsumed { .. }
            | AuthorityRollbackAction::RestoreActiveHeadAndSealGenerationConsumed { .. }
    )
}

fn recovery_seal_digest(
    preview: &AuthorityMaintenancePreview,
    failed_step: &str,
    rollback_failures: &[&str],
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(RECOVERY_SEAL_DOMAIN);
    digest.update(preview.plan_sha256.as_bytes());
    digest.update(preview.generation.as_bytes());
    digest.update(failed_step.as_bytes());
    digest.update([0]);
    for failure in rollback_failures {
        digest.update(failure.as_bytes());
        digest.update([0]);
    }
    digest.finalize().into()
}
