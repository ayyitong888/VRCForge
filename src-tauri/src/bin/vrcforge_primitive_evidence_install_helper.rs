#[allow(dead_code)]
#[path = "../primitive_evidence_authority_install.rs"]
mod primitive_evidence_authority_install;
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
#[path = "../primitive_evidence_authority_windows.rs"]
mod primitive_evidence_authority_windows;

use primitive_evidence_authority_windows::{
    build_install_plan, inspect_installed_authority, inspect_installed_authority_for_generation,
    AuthorityLayout,
};
use sha2::{Digest, Sha256};
use std::{ffi::OsStr, fs::File, io::Read, path::Path};

fn main() {
    let arguments = std::env::args_os().skip(1).collect::<Vec<_>>();
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
        [command, generation] if command == "--readback-generation" => {
            let generation =
                decode_sha256_argument(generation).unwrap_or_else(|code| exit_error(code));
            let readback = inspect_installed_authority_for_generation(&layout, &generation)
                .unwrap_or_else(|error| exit_error(error.code()));
            serde_json::to_value(readback)
                .unwrap_or_else(|_| exit_error("authority_readback_serialization_failed"))
        }
        [command, service, controller, install_helper] if command == "--preview-install" => {
            let content = primitive_evidence_authority_install::AuthorityInstallContent::new(
                digest_payload(Path::new(service)),
                digest_payload(Path::new(controller)),
                digest_payload(Path::new(install_helper)),
            )
            .unwrap_or_else(|error| exit_error(error.code()));
            let preview = primitive_evidence_authority_install::preview_install(&layout, content)
                .unwrap_or_else(|error| exit_error(error.code()));
            serde_json::to_value(preview)
                .unwrap_or_else(|_| exit_error("authority_preview_serialization_failed"))
        }
        _ => exit_error("authority_install_helper_command_rejected"),
    };
    println!("{}", value);
}

fn decode_sha256_argument(value: &OsStr) -> Result<[u8; 32], &'static str> {
    let value = value
        .to_str()
        .ok_or("authority_generation_digest_invalid")?;
    if value.len() != 64
        || !value
            .as_bytes()
            .iter()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
    {
        return Err("authority_generation_digest_invalid");
    }
    let mut output = [0u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (hex_nibble(pair[0]) << 4) | hex_nibble(pair[1]);
    }
    if output.iter().all(|byte| *byte == 0) {
        return Err("authority_generation_digest_invalid");
    }
    Ok(output)
}

fn hex_nibble(value: u8) -> u8 {
    match value {
        b'0'..=b'9' => value - b'0',
        b'a'..=b'f' => value - b'a' + 10,
        _ => unreachable!("validated lowercase hexadecimal input"),
    }
}

fn digest_payload(path: &Path) -> primitive_evidence_authority_install::AuthorityPayloadDigest {
    let path_metadata = std::fs::symlink_metadata(path)
        .unwrap_or_else(|_| exit_error("authority_payload_metadata_failed"));
    if path_metadata.file_type().is_symlink() || metadata_is_reparse_point(&path_metadata) {
        exit_error("authority_payload_reparse_rejected");
    }
    let mut file = File::open(path).unwrap_or_else(|_| exit_error("authority_payload_open_failed"));
    let before = file
        .metadata()
        .unwrap_or_else(|_| exit_error("authority_payload_metadata_failed"));
    if !before.is_file() {
        exit_error("authority_payload_not_regular");
    }
    primitive_evidence_authority_install::AuthorityPayloadDigest::new([1; 32], before.len())
        .unwrap_or_else(|error| exit_error(error.code()));
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .unwrap_or_else(|_| exit_error("authority_payload_read_failed"));
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    let after = file
        .metadata()
        .unwrap_or_else(|_| exit_error("authority_payload_metadata_failed"));
    if after.len() != before.len() || !after.is_file() {
        exit_error("authority_payload_changed_during_hash");
    }
    primitive_evidence_authority_install::AuthorityPayloadDigest::new(
        hasher.finalize().into(),
        after.len(),
    )
    .unwrap_or_else(|error| exit_error(error.code()))
}

#[cfg(windows)]
fn metadata_is_reparse_point(metadata: &std::fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;
    metadata.file_attributes()
        & windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT
        != 0
}

#[cfg(not(windows))]
fn metadata_is_reparse_point(_metadata: &std::fs::Metadata) -> bool {
    false
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
