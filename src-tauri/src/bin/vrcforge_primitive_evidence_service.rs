#[path = "../primitive_evidence_authority_contract.rs"]
mod primitive_evidence_authority_contract;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_key.rs"]
mod primitive_evidence_authority_key;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_ledger.rs"]
mod primitive_evidence_authority_ledger;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_pipe.rs"]
mod primitive_evidence_authority_pipe;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_runtime.rs"]
mod primitive_evidence_authority_runtime;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_supervisor.rs"]
mod primitive_evidence_authority_supervisor;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_windows.rs"]
mod primitive_evidence_authority_windows;

fn main() {
    let arguments: Vec<std::ffi::OsString> = std::env::args_os().collect();
    if !self_test_stdio_requested(&arguments) {
        std::process::exit(2);
    }

    #[cfg(windows)]
    {
        use primitive_evidence_authority_contract::run_read_only_protocol;
        use std::io::{stdin, stdout};

        let mut input = stdin().lock();
        let mut output = stdout().lock();
        if run_read_only_protocol(&mut input, &mut output).is_err() {
            std::process::exit(2);
        }
    }

    #[cfg(not(windows))]
    std::process::exit(2);
}

fn self_test_stdio_requested(arguments: &[std::ffi::OsString]) -> bool {
    arguments.len() == 2 && arguments[1] == "--self-test-stdio"
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;

    fn arguments(values: &[&str]) -> Vec<OsString> {
        values.iter().map(OsString::from).collect()
    }

    #[test]
    fn only_explicit_self_test_enables_stdio_protocol() {
        assert!(self_test_stdio_requested(&arguments(&[
            "service.exe",
            "--self-test-stdio"
        ])));
        for rejected in [
            arguments(&["service.exe"]),
            arguments(&["service.exe", "--service"]),
            arguments(&["service.exe", "--self-test-stdio", "extra"]),
            arguments(&["service.exe", "--status"]),
        ] {
            assert!(!self_test_stdio_requested(&rejected));
        }
    }
}
