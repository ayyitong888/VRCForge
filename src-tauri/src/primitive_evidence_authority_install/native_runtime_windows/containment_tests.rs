use super::*;

#[derive(Default)]
struct FakeContainment {
    failures: Vec<NativeBeforeTransactionContainmentPhase>,
    events: Vec<&'static str>,
}

impl FakeContainment {
    fn result(
        &self,
        phase: NativeBeforeTransactionContainmentPhase,
        code: &'static str,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.failures.contains(&phase) {
            Err(AuthorityMaintenanceError(code))
        } else {
            Ok(())
        }
    }
}

impl NativeBeforeTransactionContainmentOperations for FakeContainment {
    fn recover_store(&mut self) -> Result<(), AuthorityMaintenanceError> {
        self.events.push("recoverStore");
        self.result(
            NativeBeforeTransactionContainmentPhase::RecoverStore,
            "authority_worker_test_recovery_failed",
        )
    }

    fn close_pipe(&mut self) -> Result<(), AuthorityMaintenanceError> {
        self.events.push("closePipe");
        self.result(
            NativeBeforeTransactionContainmentPhase::ClosePipe,
            "authority_worker_test_pipe_cleanup_failed",
        )
    }

    fn stop_wait_delete_worker(&mut self) -> Result<(), AuthorityMaintenanceError> {
        self.events.push("stopWaitDeleteWorker");
        self.result(
            NativeBeforeTransactionContainmentPhase::StopWaitDeleteWorker,
            "authority_worker_test_service_cleanup_failed",
        )
    }

    fn contain_partial_staging(&mut self) -> Result<(), AuthorityMaintenanceError> {
        self.events.push("containPartialStaging");
        self.result(
            NativeBeforeTransactionContainmentPhase::ContainPartialStaging,
            "authority_worker_test_staging_cleanup_failed",
        )
    }

    fn verify_zero_residue(&mut self) -> Result<(), AuthorityMaintenanceError> {
        self.events.push("verifyZeroResidue");
        self.result(
            NativeBeforeTransactionContainmentPhase::VerifyZeroResidue,
            "authority_worker_test_zero_residue_failed",
        )
    }
}

#[test]
fn store_recovery_failure_cannot_skip_worker_cleanup() {
    let mut containment = FakeContainment {
        failures: vec![NativeBeforeTransactionContainmentPhase::RecoverStore],
        ..Default::default()
    };

    let failure = run_before_transaction_containment(&mut containment).unwrap_err();
    assert_eq!(
        failure.primary,
        Some("authority_worker_test_recovery_failed")
    );
    assert!(failure.cleanup.is_empty());
    assert_eq!(
        containment.events,
        vec![
            "recoverStore",
            "closePipe",
            "stopWaitDeleteWorker",
            "containPartialStaging",
            "verifyZeroResidue",
        ]
    );
}

#[test]
fn legitimate_cleanup_preserves_exact_phase_order() {
    let mut containment = FakeContainment::default();
    run_before_transaction_containment(&mut containment).unwrap();
    assert_eq!(
        containment.events,
        vec![
            "recoverStore",
            "closePipe",
            "stopWaitDeleteWorker",
            "containPartialStaging",
            "verifyZeroResidue",
        ]
    );
}

#[test]
fn primary_and_cleanup_failures_are_preserved_by_phase() {
    let mut containment = FakeContainment {
        failures: vec![
            NativeBeforeTransactionContainmentPhase::RecoverStore,
            NativeBeforeTransactionContainmentPhase::StopWaitDeleteWorker,
            NativeBeforeTransactionContainmentPhase::ContainPartialStaging,
            NativeBeforeTransactionContainmentPhase::VerifyZeroResidue,
        ],
        ..Default::default()
    };

    let failure = run_before_transaction_containment(&mut containment).unwrap_err();
    assert_eq!(
        failure.primary,
        Some("authority_worker_test_recovery_failed")
    );
    assert_eq!(
        failure.cleanup,
        vec![
            NativeBeforeTransactionCleanupFailure {
                phase: NativeBeforeTransactionContainmentPhase::StopWaitDeleteWorker,
                code: "authority_worker_test_service_cleanup_failed",
            },
            NativeBeforeTransactionCleanupFailure {
                phase: NativeBeforeTransactionContainmentPhase::ContainPartialStaging,
                code: "authority_worker_test_staging_cleanup_failed",
            },
            NativeBeforeTransactionCleanupFailure {
                phase: NativeBeforeTransactionContainmentPhase::VerifyZeroResidue,
                code: "authority_worker_test_zero_residue_failed",
            },
        ]
    );
    assert_eq!(
        failure.into_authority_error().code(),
        "authority_native_worker_recovery_and_cleanup_failed"
    );
}

#[test]
fn every_cleanup_fault_still_runs_later_cleanup_phases() {
    for failed_phase in NATIVE_BEFORE_TRANSACTION_CONTAINMENT_PHASES {
        let mut containment = FakeContainment {
            failures: vec![failed_phase],
            ..Default::default()
        };
        assert!(run_before_transaction_containment(&mut containment).is_err());
        assert_eq!(
            containment.events,
            vec![
                "recoverStore",
                "closePipe",
                "stopWaitDeleteWorker",
                "containPartialStaging",
                "verifyZeroResidue",
            ],
            "failed phase {failed_phase:?}"
        );
    }
}
