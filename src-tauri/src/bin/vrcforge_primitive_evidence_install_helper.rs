#[allow(dead_code)]
#[path = "../primitive_evidence_authority_windows.rs"]
mod primitive_evidence_authority_windows;

use primitive_evidence_authority_windows::{
    build_install_plan, inspect_installed_authority, AuthorityLayout,
};

fn main() {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    let layout = match AuthorityLayout::installed() {
        Ok(value) => value,
        Err(error) => exit_error(error.code()),
    };
    let value = match arguments.as_slice() {
        [command] if command == "--plan" => serde_json::to_value(build_install_plan(&layout))
            .unwrap_or_else(|_| exit_error("authority_plan_serialization_failed")),
        [command] if command == "--readback" => {
            let readback = inspect_installed_authority(&layout)
                .unwrap_or_else(|error| exit_error(error.code()));
            serde_json::to_value(readback)
                .unwrap_or_else(|_| exit_error("authority_readback_serialization_failed"))
        }
        _ => exit_error("authority_install_helper_command_rejected"),
    };
    println!("{}", value);
}

fn exit_error(code: &str) -> ! {
    println!(
        "{}",
        serde_json::json!({
            "schema": "vrcforge.primitive_evidence_authority_helper_error.v1",
            "ok": false,
            "error": {"code": code},
        })
    );
    std::process::exit(2)
}
