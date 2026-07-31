// This shared wire-contract module also contains parent-only projections. The
// fixed child entrypoint compiles only the child half; parent crates still
// compile the same source without this boundary exemption.
#[allow(dead_code)]
#[path = "../primitive_evidence_child_protocol.rs"]
mod primitive_evidence_child_protocol;
#[cfg(windows)]
#[path = "../primitive_evidence_child_transport_windows.rs"]
mod primitive_evidence_child_transport_windows;
#[cfg(windows)]
#[allow(dead_code)]
#[path = "../primitive_evidence_process_token_windows.rs"]
mod primitive_evidence_process_token_windows;

#[cfg(windows)]
use primitive_evidence_child_protocol::windows_child_handshake::{
    perform_authenticated_bootstrap, AuthenticatedChildStartup, ChildHandshakeError,
};
use primitive_evidence_child_protocol::ChildBootstrapRole;
#[cfg(windows)]
use primitive_evidence_child_transport_windows::{
    ChildStandardHandleError, ValidatedChildStandardHandleSet,
};
use std::{ffi::OsString, fmt};

const EXPECTED_ROLE: ChildBootstrapRole = ChildBootstrapRole::LifecycleDriver;
#[cfg(not(windows))]
const EXPECTATIONS_SOURCE_NOT_CONNECTED: &str =
    "lifecycle_driver_authenticated_expectations_source_not_connected";
#[cfg(windows)]
const RUNTIME_NOT_CONNECTED: &str = "lifecycle_driver_runtime_not_connected";

fn main() {
    let arguments = std::env::args_os().skip(1).collect::<Vec<_>>();
    if let Err(error) = run(&arguments) {
        let _ = error.code();
        std::process::exit(2);
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct DriverEntryError(&'static str);

impl DriverEntryError {
    fn new(code: &'static str) -> Self {
        Self(code)
    }

    fn code(&self) -> &'static str {
        self.0
    }
}

#[cfg(windows)]
impl From<ChildStandardHandleError> for DriverEntryError {
    fn from(error: ChildStandardHandleError) -> Self {
        Self::new(error.code())
    }
}

#[cfg(windows)]
impl From<ChildHandshakeError> for DriverEntryError {
    fn from(error: ChildHandshakeError) -> Self {
        Self::new(error.code())
    }
}

impl fmt::Display for DriverEntryError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for DriverEntryError {}

fn run(arguments: &[OsString]) -> Result<(), DriverEntryError> {
    run_with_standard_handle_gate(arguments, load_validated_standard_handles)
}

fn run_with_standard_handle_gate<F>(arguments: &[OsString], gate: F) -> Result<(), DriverEntryError>
where
    F: FnOnce() -> Result<(), DriverEntryError>,
{
    if !arguments.is_empty() {
        return Err(DriverEntryError::new("lifecycle_driver_argument_rejected"));
    }
    gate()
}

fn load_validated_standard_handles() -> Result<(), DriverEntryError> {
    #[cfg(windows)]
    {
        let validated_handles =
            ValidatedChildStandardHandleSet::validate_current_process(EXPECTED_ROLE)
                .map_err(DriverEntryError::from)?;
        let startup = perform_authenticated_bootstrap(EXPECTED_ROLE, validated_handles)
            .map_err(DriverEntryError::from)?;
        return hand_to_runtime(startup);
    }
    #[cfg(not(windows))]
    Err(DriverEntryError::new(EXPECTATIONS_SOURCE_NOT_CONNECTED))
}

#[cfg(windows)]
fn hand_to_runtime(startup: AuthenticatedChildStartup) -> Result<(), DriverEntryError> {
    let (_validated, transport) = startup.into_runtime_parts();
    debug_assert_eq!(transport.role(), EXPECTED_ROLE);
    let _runtime_endpoints = (
        transport.private_control_handle(),
        transport.structured_result_handle(),
    );
    Err(DriverEntryError::new(RUNTIME_NOT_CONNECTED))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    #[test]
    fn direct_entry_is_zero_argument_and_fail_closed_before_runtime() {
        let error = run(&[]).expect_err("expectations source must remain disconnected");
        assert_ne!(error.code(), RUNTIME_NOT_CONNECTED);
    }

    #[test]
    fn every_argument_is_rejected_before_standard_handle_loading() {
        for arguments in [
            vec![OsString::from("--help")],
            vec![OsString::from("--bootstrap"), OsString::from("payload")],
            vec![OsString::from("driver.exe")],
        ] {
            let gate_called = Cell::new(false);
            let error = run_with_standard_handle_gate(&arguments, || {
                gate_called.set(true);
                Err(DriverEntryError::new("test_gate_failure"))
            })
            .expect_err("arguments must be rejected");
            assert_eq!(error.code(), "lifecycle_driver_argument_rejected");
            assert!(!gate_called.get());
        }
    }

    #[test]
    fn failed_authenticated_startup_never_reaches_runtime() {
        let gate_called = Cell::new(false);
        assert_eq!(
            run_with_standard_handle_gate(&[], || {
                gate_called.set(true);
                Err(DriverEntryError::new("test_handshake_failed"))
            })
            .expect_err("failed handshake must stop the entry point")
            .code(),
            "test_handshake_failed"
        );
        assert!(gate_called.get());
    }
}
