#[allow(dead_code)]
#[path = "../primitive_evidence_authority_pipe.rs"]
mod primitive_evidence_authority_pipe;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_windows.rs"]
mod primitive_evidence_authority_windows;

use serde_json::json;

const CONTROLLER_STATUS_SCHEMA: &str = "vrcforge.primitive_evidence_controller_status.v1";

fn main() {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    match arguments.as_slice() {
        [argument] if argument == "--status" => {
            println!(
                "{}",
                json!({
                    "schema": CONTROLLER_STATUS_SCHEMA,
                    "trustedBoundaryReady": false,
                    "requestProcessingEnabled": false,
                    "pipePolicyCompiled": cfg!(windows),
                })
            );
        }
        [argument] if argument == "--self-test" => {
            match primitive_evidence_authority_pipe::run_non_mutating_self_test() {
                Ok(()) => println!(
                    "{}",
                    json!({
                        "schema": CONTROLLER_STATUS_SCHEMA,
                        "ok": true,
                        "trustedBoundaryReady": false,
                        "requestProcessingEnabled": false,
                    })
                ),
                Err(error) => {
                    eprintln!(
                        "{}",
                        json!({
                            "schema": CONTROLLER_STATUS_SCHEMA,
                            "ok": false,
                            "error": error.code(),
                            "win32": error.win32(),
                        })
                    );
                    std::process::exit(2);
                }
            }
        }
        _ => {
            eprintln!(
                "{}",
                json!({
                    "schema": CONTROLLER_STATUS_SCHEMA,
                    "ok": false,
                    "error": "controller_argument_rejected",
                })
            );
            std::process::exit(2);
        }
    }
}
