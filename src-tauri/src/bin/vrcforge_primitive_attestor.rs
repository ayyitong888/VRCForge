#[path = "../primitive_attestor_protocol.rs"]
mod primitive_attestor_protocol;

#[cfg(windows)]
fn main() {
    use primitive_attestor_protocol::{
        executable_sha256, run_protocol, AttestorSession, CngKeyStore,
    };
    use std::io::{stdin, stdout};

    let executable = match std::env::current_exe() {
        Ok(path) => path,
        Err(_) => std::process::exit(2),
    };
    let digest = match executable_sha256(&executable) {
        Ok(value) => value,
        Err(_) => std::process::exit(2),
    };
    let mut session = match AttestorSession::new(CngKeyStore::new(), digest) {
        Ok(value) => value,
        Err(_) => std::process::exit(2),
    };
    let mut input = stdin().lock();
    let mut output = stdout().lock();
    if run_protocol(&mut input, &mut output, &mut session).is_err() {
        std::process::exit(2);
    }
}

#[cfg(not(windows))]
fn main() {
    std::process::exit(2);
}
