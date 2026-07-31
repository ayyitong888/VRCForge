#[path = "../primitive_evidence_authority_blob.rs"]
mod primitive_evidence_authority_blob;
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
#[cfg(windows)]
#[allow(dead_code)]
#[path = "../primitive_evidence_runtime_broker_transfer_windows.rs"]
mod primitive_evidence_runtime_broker_transfer_windows;
#[path = "../primitive_evidence_windows_service_host.rs"]
mod primitive_evidence_windows_service_host;

// Keep the typed FinalCommit handoff compiled in this binary without making it
// reachable from an install-helper command. Production use remains gated by
// the service runtime's explicit protected-evidence binding blocker.
const _: fn(
    primitive_evidence_authority_blob::AuthenticatedProtectedBlobNamespace,
) -> Result<
    primitive_evidence_authority_blob::ProtectedBlobAuthority,
    primitive_evidence_authority_blob::ProtectedBlobError,
> = primitive_evidence_authority_blob::AuthenticatedProtectedBlobNamespace::into_authority;

#[cfg(test)]
const _: fn(
    std::path::PathBuf,
    primitive_evidence_authority_blob::BlobDigest,
    primitive_evidence_authority_blob::BlobDigest,
) -> Result<
    primitive_evidence_authority_blob::AuthenticatedProtectedBlobNamespace,
    primitive_evidence_authority_blob::ProtectedBlobError,
> = primitive_evidence_authority_blob::AuthenticatedProtectedBlobNamespace::provision_unsecured_test;

use primitive_evidence_authority_windows::{
    build_install_plan, inspect_installed_authority, inspect_installed_authority_for_generation,
    AuthorityLayout,
};
use sha2::{Digest, Sha256};
use std::{
    ffi::{OsStr, OsString},
    fs::{File, OpenOptions},
    io::Read,
    path::Path,
};

const MAX_CONSENT_BYTES: u64 = 64 * 1024;
const MAX_AUTHORITY_BINARY_BYTES: u64 = 512 * 1024 * 1024;
static MAINTENANCE_WORKER_CAPSULE: std::sync::OnceLock<[u8; 32]> = std::sync::OnceLock::new();

struct HeldPayloadInput {
    _file: File,
    descriptor: primitive_evidence_authority_install::AuthorityPayloadDigest,
}

#[cfg(windows)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct HeldFileIdentity {
    volume_serial: u32,
    file_index: u64,
    link_count: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ActionMaintenanceOperation {
    Install,
    Update,
    Retire,
}

#[derive(Debug)]
struct ActionMaintenanceArguments<'a> {
    operation: ActionMaintenanceOperation,
    service: &'a OsStr,
    controller: &'a OsStr,
    install_helper: &'a OsStr,
    lifecycle_driver: &'a OsStr,
    bridge_launcher: &'a OsStr,
    runtime_source_manifest: &'a OsStr,
    prior_generation: Option<[u8; 32]>,
    plan_sha256: [u8; 32],
    generation: [u8; 32],
    service_sha256: [u8; 32],
    controller_sha256: [u8; 32],
    install_helper_sha256: [u8; 32],
    lifecycle_driver_sha256: [u8; 32],
    bridge_launcher_sha256: [u8; 32],
    runtime_source_manifest_sha256: [u8; 32],
    consent: &'a OsStr,
    consent_sha256: [u8; 32],
}

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
        [command, service, controller, install_helper, lifecycle_driver, bridge_launcher, runtime_source_manifest]
            if command == "--preview-install" =>
        {
            let content = primitive_evidence_authority_install::AuthorityInstallContent::new(
                digest_payload(Path::new(service)),
                digest_payload(Path::new(controller)),
                digest_payload(Path::new(install_helper)),
                digest_payload(Path::new(lifecycle_driver)),
                digest_payload(Path::new(bridge_launcher)),
                digest_runtime_source_manifest(Path::new(runtime_source_manifest)),
            )
            .unwrap_or_else(|error| exit_error(error.code()));
            let preview = primitive_evidence_authority_install::preview_install(&layout, content)
                .unwrap_or_else(|error| exit_error(error.code()));
            serde_json::to_value(preview)
                .unwrap_or_else(|_| exit_error("authority_preview_serialization_failed"))
        }
        [command, service, controller, install_helper, lifecycle_driver, bridge_launcher, runtime_source_manifest, prior_generation]
            if command == "--preview-update" || command == "--preview-retire" =>
        {
            let expected_prior = decode_sha256_argument_with_code(
                prior_generation,
                "authority_prior_generation_expected_invalid",
            )
            .unwrap_or_else(|code| exit_error(code));
            let prior =
                primitive_evidence_authority_install::read_native_verified_prior_generation(
                    &layout,
                    expected_prior,
                )
                .unwrap_or_else(|error| exit_error(error.code()));
            let content = primitive_evidence_authority_install::AuthorityInstallContent::new(
                digest_payload(Path::new(service)),
                digest_payload(Path::new(controller)),
                digest_payload(Path::new(install_helper)),
                digest_payload(Path::new(lifecycle_driver)),
                digest_payload(Path::new(bridge_launcher)),
                digest_runtime_source_manifest(Path::new(runtime_source_manifest)),
            )
            .unwrap_or_else(|error| exit_error(error.code()));
            let preview = if command == "--preview-update" {
                primitive_evidence_authority_install::preview_update(&layout, content, prior)
            } else {
                primitive_evidence_authority_install::preview_retire_with_content(
                    &layout, content, prior,
                )
            }
            .unwrap_or_else(|error| exit_error(error.code()));
            serde_json::to_value(preview)
                .unwrap_or_else(|_| exit_error("authority_preview_serialization_failed"))
        }
        values
            if values.first().is_some_and(|command| {
                command == "--execute-install"
                    || command == "--execute-update"
                    || command == "--execute-retire"
            }) =>
        {
            let request =
                parse_action_maintenance_arguments(values).unwrap_or_else(|code| exit_error(code));
            execute_action_time_maintenance(&layout, request)
        }
        [command, capsule_sha256] if command == "--maintenance-worker" => {
            let capsule_sha256 = decode_sha256_argument_with_code(
                capsule_sha256,
                "authority_worker_capsule_digest_invalid",
            )
            .unwrap_or_else(|code| exit_error(code));
            MAINTENANCE_WORKER_CAPSULE
                .set(capsule_sha256)
                .unwrap_or_else(|_| exit_error("authority_worker_capsule_state_invalid"));
            primitive_evidence_windows_service_host::run_service_dispatcher(
                primitive_evidence_authority_install::MAINTENANCE_WORKER_SERVICE_NAME,
                maintenance_worker_service_body,
            )
            .unwrap_or_else(|code| exit_error(code));
            return;
        }
        _ => exit_error("authority_install_helper_command_rejected"),
    };
    println!("{}", value);
}

fn decode_sha256_argument(value: &OsStr) -> Result<[u8; 32], &'static str> {
    decode_sha256_argument_with_code(value, "authority_generation_digest_invalid")
}

fn decode_sha256_argument_with_code(
    value: &OsStr,
    code: &'static str,
) -> Result<[u8; 32], &'static str> {
    let value = value.to_str().ok_or(code)?;
    if value.len() != 64
        || !value
            .as_bytes()
            .iter()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
    {
        return Err(code);
    }
    let mut output = [0u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (hex_nibble(pair[0]) << 4) | hex_nibble(pair[1]);
    }
    if output.iter().all(|byte| *byte == 0) {
        return Err(code);
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
    open_payload_with_limit(
        path,
        MAX_AUTHORITY_BINARY_BYTES,
        "authority_payload_metadata_invalid",
    )
    .descriptor
}

fn digest_runtime_source_manifest(
    path: &Path,
) -> primitive_evidence_authority_install::AuthorityPayloadDigest {
    open_payload_with_limit(
        path,
        primitive_evidence_authority_install::MAX_RUNTIME_SOURCE_MANIFEST_BYTES,
        "authority_runtime_source_manifest_length_invalid",
    )
    .descriptor
}

fn open_payload_with_limit(
    path: &Path,
    maximum_bytes: u64,
    length_error: &'static str,
) -> HeldPayloadInput {
    if !path.is_absolute() {
        exit_error("authority_payload_path_invalid");
    }
    let path_metadata = std::fs::symlink_metadata(path)
        .unwrap_or_else(|_| exit_error("authority_payload_metadata_failed"));
    if path_metadata.file_type().is_symlink() || metadata_is_reparse_point(&path_metadata) {
        exit_error("authority_payload_reparse_rejected");
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::{
            FILE_FLAG_OPEN_REPARSE_POINT, FILE_FLAG_SEQUENTIAL_SCAN, FILE_SHARE_READ,
        };
        options
            .share_mode(FILE_SHARE_READ)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN);
    }
    let mut file = options
        .open(path)
        .unwrap_or_else(|_| exit_error("authority_payload_open_failed"));
    let before = file
        .metadata()
        .unwrap_or_else(|_| exit_error("authority_payload_metadata_failed"));
    if !before.is_file() {
        exit_error("authority_payload_not_regular");
    }
    if before.len() == 0 || before.len() > maximum_bytes {
        exit_error(length_error);
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
    let descriptor = primitive_evidence_authority_install::AuthorityPayloadDigest::new(
        hasher.finalize().into(),
        after.len(),
    )
    .unwrap_or_else(|error| exit_error(error.code()));
    #[cfg(windows)]
    let identity = held_file_identity(&file);
    #[cfg(windows)]
    {
        if identity.link_count != 1 || !path_is_local(path) {
            exit_error("authority_payload_link_or_network_rejected");
        }
    }
    HeldPayloadInput {
        _file: file,
        descriptor,
    }
}

fn parse_action_maintenance_arguments(
    values: &[OsString],
) -> Result<ActionMaintenanceArguments<'_>, &'static str> {
    let (
        operation,
        service,
        controller,
        install_helper,
        lifecycle_driver,
        bridge_launcher,
        runtime_source_manifest,
        prior_generation,
        plan,
        generation,
        service_sha256,
        controller_sha256,
        install_helper_sha256,
        lifecycle_driver_sha256,
        bridge_launcher_sha256,
        runtime_source_manifest_sha256,
        consent,
        consent_sha256,
    ) = match values {
        [command, service, controller, install_helper, lifecycle_driver, bridge_launcher, runtime_source_manifest, plan, generation, service_sha256, controller_sha256, install_helper_sha256, lifecycle_driver_sha256, bridge_launcher_sha256, runtime_source_manifest_sha256, consent, consent_sha256]
            if command == "--execute-install" =>
        {
            (
                ActionMaintenanceOperation::Install,
                service,
                controller,
                install_helper,
                lifecycle_driver,
                bridge_launcher,
                runtime_source_manifest,
                None,
                plan,
                generation,
                service_sha256,
                controller_sha256,
                install_helper_sha256,
                lifecycle_driver_sha256,
                bridge_launcher_sha256,
                runtime_source_manifest_sha256,
                consent,
                consent_sha256,
            )
        }
        [command, service, controller, install_helper, lifecycle_driver, bridge_launcher, runtime_source_manifest, prior_generation, plan, generation, service_sha256, controller_sha256, install_helper_sha256, lifecycle_driver_sha256, bridge_launcher_sha256, runtime_source_manifest_sha256, consent, consent_sha256]
            if command == "--execute-update" || command == "--execute-retire" =>
        {
            (
                if command == "--execute-update" {
                    ActionMaintenanceOperation::Update
                } else {
                    ActionMaintenanceOperation::Retire
                },
                service,
                controller,
                install_helper,
                lifecycle_driver,
                bridge_launcher,
                runtime_source_manifest,
                Some(decode_sha256_argument_with_code(
                    prior_generation,
                    "authority_prior_generation_expected_invalid",
                )?),
                plan,
                generation,
                service_sha256,
                controller_sha256,
                install_helper_sha256,
                lifecycle_driver_sha256,
                bridge_launcher_sha256,
                runtime_source_manifest_sha256,
                consent,
                consent_sha256,
            )
        }
        _ => return Err("authority_action_time_arguments_invalid"),
    };
    Ok(ActionMaintenanceArguments {
        operation,
        service,
        controller,
        install_helper,
        lifecycle_driver,
        bridge_launcher,
        runtime_source_manifest,
        prior_generation,
        plan_sha256: decode_sha256_argument_with_code(
            plan,
            "authority_action_time_plan_digest_invalid",
        )?,
        generation: decode_sha256_argument_with_code(
            generation,
            "authority_action_time_generation_invalid",
        )?,
        service_sha256: decode_sha256_argument_with_code(
            service_sha256,
            "authority_action_time_content_digest_invalid",
        )?,
        controller_sha256: decode_sha256_argument_with_code(
            controller_sha256,
            "authority_action_time_content_digest_invalid",
        )?,
        install_helper_sha256: decode_sha256_argument_with_code(
            install_helper_sha256,
            "authority_action_time_content_digest_invalid",
        )?,
        lifecycle_driver_sha256: decode_sha256_argument_with_code(
            lifecycle_driver_sha256,
            "authority_action_time_content_digest_invalid",
        )?,
        bridge_launcher_sha256: decode_sha256_argument_with_code(
            bridge_launcher_sha256,
            "authority_action_time_content_digest_invalid",
        )?,
        runtime_source_manifest_sha256: decode_sha256_argument_with_code(
            runtime_source_manifest_sha256,
            "authority_action_time_content_digest_invalid",
        )?,
        consent,
        consent_sha256: decode_sha256_argument_with_code(
            consent_sha256,
            "authority_action_time_consent_digest_invalid",
        )?,
    })
}

fn execute_action_time_maintenance(
    layout: &AuthorityLayout,
    request: ActionMaintenanceArguments<'_>,
) -> serde_json::Value {
    require_action_time_elevated_user();
    #[cfg(not(windows))]
    exit_error("authority_platform_unsupported");
    #[cfg(windows)]
    let preparation = match request.operation {
        ActionMaintenanceOperation::Install => {
            primitive_evidence_authority_install::prepare_native_install_sources(
                layout,
                Path::new(request.service),
                Path::new(request.controller),
                Path::new(request.install_helper),
                Path::new(request.lifecycle_driver),
                Path::new(request.bridge_launcher),
                Path::new(request.runtime_source_manifest),
            )
        }
        ActionMaintenanceOperation::Update | ActionMaintenanceOperation::Retire => {
            let expected_prior = request
                .prior_generation
                .unwrap_or_else(|| exit_error("authority_prior_generation_expected_invalid"));
            let prior =
                primitive_evidence_authority_install::read_native_verified_prior_generation(
                    layout,
                    expected_prior,
                )
                .unwrap_or_else(|error| exit_error(error.code()));
            if request.operation == ActionMaintenanceOperation::Update {
                primitive_evidence_authority_install::prepare_native_update_sources(
                    layout,
                    Path::new(request.service),
                    Path::new(request.controller),
                    Path::new(request.install_helper),
                    Path::new(request.lifecycle_driver),
                    Path::new(request.bridge_launcher),
                    Path::new(request.runtime_source_manifest),
                    prior,
                )
            } else {
                primitive_evidence_authority_install::prepare_native_retire_sources(
                    layout,
                    Path::new(request.service),
                    Path::new(request.controller),
                    Path::new(request.install_helper),
                    Path::new(request.lifecycle_driver),
                    Path::new(request.bridge_launcher),
                    Path::new(request.runtime_source_manifest),
                    prior,
                )
            }
        }
    }
    .unwrap_or_else(|error| exit_error(error.code()));
    #[cfg(windows)]
    preparation
        .validate_request_binding(
            request.plan_sha256,
            request.generation,
            request.service_sha256,
            request.controller_sha256,
            request.install_helper_sha256,
            request.lifecycle_driver_sha256,
            request.bridge_launcher_sha256,
            request.runtime_source_manifest_sha256,
        )
        .unwrap_or_else(|error| exit_error(error.code()));
    let consent = read_bounded_input(Path::new(request.consent), MAX_CONSENT_BYTES);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_else(|_| exit_error("authority_action_time_clock_invalid"))
        .as_millis()
        .try_into()
        .unwrap_or_else(|_| exit_error("authority_action_time_clock_invalid"));
    #[cfg(windows)]
    let prepared = preparation
        .seal_for_worker(&consent, request.consent_sha256, now)
        .unwrap_or_else(|error| exit_error(error.code()));
    #[cfg(windows)]
    let report = primitive_evidence_authority_install::execute_prepared_native_install(prepared)
        .unwrap_or_else(|error| exit_error(error.code()));
    #[cfg(windows)]
    serde_json::to_value(report)
        .unwrap_or_else(|_| exit_error("authority_execution_report_serialization_failed"))
}

fn read_bounded_input(path: &Path, maximum: u64) -> Vec<u8> {
    let held = open_read_only_input(path, maximum);
    let mut file = held.0;
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)
        .unwrap_or_else(|_| exit_error("authority_action_time_consent_read_failed"));
    if bytes.is_empty() || bytes.len() as u64 > maximum {
        exit_error("authority_action_time_consent_size_invalid");
    }
    bytes
}

fn open_read_only_input(path: &Path, maximum: u64) -> (File, u64) {
    if !path.is_absolute() {
        exit_error("authority_action_time_consent_path_invalid");
    }
    let metadata = std::fs::symlink_metadata(path)
        .unwrap_or_else(|_| exit_error("authority_action_time_consent_metadata_failed"));
    if !metadata.is_file()
        || metadata.file_type().is_symlink()
        || metadata_is_reparse_point(&metadata)
        || metadata.len() == 0
        || metadata.len() > maximum
    {
        exit_error("authority_action_time_consent_metadata_invalid");
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::{
            FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_READ,
        };
        options
            .share_mode(FILE_SHARE_READ)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }
    let file = options
        .open(path)
        .unwrap_or_else(|_| exit_error("authority_action_time_consent_open_failed"));
    #[cfg(windows)]
    {
        let identity = held_file_identity(&file);
        if identity.link_count != 1 || !path_is_local(path) {
            exit_error("authority_action_time_consent_link_or_network_rejected");
        }
    }
    (file, metadata.len())
}

#[cfg(windows)]
fn held_file_identity(file: &File) -> HeldFileIdentity {
    use std::{mem::zeroed, os::windows::io::AsRawHandle};
    use windows_sys::Win32::Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
    };
    let mut information: BY_HANDLE_FILE_INFORMATION = unsafe { zeroed() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle().cast(), &mut information) } == 0
        || information.dwVolumeSerialNumber == 0
        || (information.nFileIndexHigh == 0 && information.nFileIndexLow == 0)
    {
        exit_error("authority_file_identity_unavailable");
    }
    HeldFileIdentity {
        volume_serial: information.dwVolumeSerialNumber,
        file_index: (u64::from(information.nFileIndexHigh) << 32)
            | u64::from(information.nFileIndexLow),
        link_count: information.nNumberOfLinks,
    }
}

#[cfg(windows)]
fn path_is_local(path: &Path) -> bool {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{GetDriveTypeW, GetVolumePathNameW};
    const DRIVE_REMOTE_VALUE: u32 = 4;
    let path = path
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let mut root = [0u16; 32_768];
    if unsafe { GetVolumePathNameW(path.as_ptr(), root.as_mut_ptr(), root.len() as u32) } == 0 {
        return false;
    }
    (unsafe { GetDriveTypeW(root.as_ptr()) }) != DRIVE_REMOTE_VALUE
}

#[cfg(windows)]
fn require_action_time_elevated_user() {
    let security = current_process_security();
    if !security.elevated || !security.high_integrity || security.local_system {
        exit_error("authority_action_time_elevation_required");
    }
}

#[cfg(not(windows))]
fn require_action_time_elevated_user() {
    exit_error("authority_platform_unsupported")
}

fn maintenance_worker_service_body() -> u32 {
    if primitive_evidence_windows_service_host::stop_requested() {
        return 0;
    }
    let Some(capsule_sha256) = MAINTENANCE_WORKER_CAPSULE.get().copied() else {
        return 2;
    };
    if execute_system_worker(capsule_sha256).is_ok() {
        0
    } else {
        2
    }
}

fn execute_system_worker(capsule_sha256: [u8; 32]) -> Result<(), &'static str> {
    #[cfg(windows)]
    {
        let security = current_process_security();
        if !security.local_system || security.session_id != 0 || !security.high_integrity {
            return Err("authority_worker_system_identity_required");
        }
        let layout = AuthorityLayout::installed().map_err(|error| error.code())?;
        primitive_evidence_authority_install::execute_native_system_worker(
            &layout,
            capsule_sha256,
            primitive_evidence_windows_service_host::report_running,
        )
        .map_err(|error| error.code())
    }
    #[cfg(not(windows))]
    Err("authority_platform_unsupported")
}

#[cfg(windows)]
struct ProcessSecurity {
    elevated: bool,
    high_integrity: bool,
    local_system: bool,
    session_id: u32,
}

#[cfg(windows)]
fn current_process_security() -> ProcessSecurity {
    use std::ptr;
    use windows_sys::Win32::{
        Foundation::{CloseHandle, HANDLE},
        Security::{
            CreateWellKnownSid, EqualSid, GetSidSubAuthority, GetSidSubAuthorityCount, IsValidSid,
            TokenElevation, TokenIntegrityLevel, TokenSessionId, TokenUser, WinLocalSystemSid,
            TOKEN_ELEVATION, TOKEN_MANDATORY_LABEL, TOKEN_QUERY, TOKEN_USER,
        },
        System::{
            SystemServices::SECURITY_MANDATORY_HIGH_RID,
            Threading::{GetCurrentProcess, OpenProcessToken},
        },
    };
    let mut token: HANDLE = ptr::null_mut();
    if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0
        || token.is_null()
    {
        exit_error("authority_process_token_unavailable");
    }
    struct Token(HANDLE);
    impl Drop for Token {
        fn drop(&mut self) {
            if !self.0.is_null() {
                unsafe {
                    CloseHandle(self.0);
                }
            }
        }
    }
    let token = Token(token);
    let elevation: TOKEN_ELEVATION = query_token_fixed(token.0, TokenElevation);
    let session_id: u32 = query_token_fixed(token.0, TokenSessionId);
    let integrity = query_token_buffer(token.0, TokenIntegrityLevel);
    let label = unsafe { &*(integrity.as_ptr().cast::<TOKEN_MANDATORY_LABEL>()) };
    if label.Label.Sid.is_null() || unsafe { IsValidSid(label.Label.Sid) } == 0 {
        exit_error("authority_process_integrity_invalid");
    }
    let count = unsafe { *GetSidSubAuthorityCount(label.Label.Sid) } as u32;
    if count == 0 {
        exit_error("authority_process_integrity_invalid");
    }
    let rid = unsafe { *GetSidSubAuthority(label.Label.Sid, count - 1) };
    let user = query_token_buffer(token.0, TokenUser);
    let token_user = unsafe { &*(user.as_ptr().cast::<TOKEN_USER>()) };
    if token_user.User.Sid.is_null() || unsafe { IsValidSid(token_user.User.Sid) } == 0 {
        exit_error("authority_process_token_query_failed");
    }
    let mut system_sid = [0usize; 9];
    let mut system_sid_size = std::mem::size_of_val(&system_sid) as u32;
    if unsafe {
        CreateWellKnownSid(
            WinLocalSystemSid,
            ptr::null_mut(),
            system_sid.as_mut_ptr().cast(),
            &mut system_sid_size,
        )
    } == 0
    {
        exit_error("authority_system_sid_unavailable");
    }
    if unsafe { IsValidSid(system_sid.as_ptr().cast_mut().cast()) } == 0 {
        exit_error("authority_system_sid_unavailable");
    }
    ProcessSecurity {
        elevated: elevation.TokenIsElevated != 0,
        high_integrity: rid >= SECURITY_MANDATORY_HIGH_RID as u32,
        local_system: unsafe {
            EqualSid(token_user.User.Sid, system_sid.as_ptr().cast_mut().cast())
        } != 0,
        session_id,
    }
}

#[cfg(windows)]
fn query_token_fixed<T: Copy>(
    token: windows_sys::Win32::Foundation::HANDLE,
    class: windows_sys::Win32::Security::TOKEN_INFORMATION_CLASS,
) -> T {
    use std::mem::MaybeUninit;
    use windows_sys::Win32::Security::GetTokenInformation;
    let mut value = MaybeUninit::<T>::zeroed();
    let mut returned = 0u32;
    if unsafe {
        GetTokenInformation(
            token,
            class,
            value.as_mut_ptr().cast(),
            std::mem::size_of::<T>() as u32,
            &mut returned,
        )
    } == 0
        || returned != std::mem::size_of::<T>() as u32
    {
        exit_error("authority_process_token_query_failed");
    }
    unsafe { value.assume_init() }
}

#[cfg(windows)]
fn query_token_buffer(
    token: windows_sys::Win32::Foundation::HANDLE,
    class: windows_sys::Win32::Security::TOKEN_INFORMATION_CLASS,
) -> Vec<usize> {
    use std::ptr;
    use windows_sys::Win32::{
        Foundation::{GetLastError, ERROR_INSUFFICIENT_BUFFER},
        Security::GetTokenInformation,
    };
    let mut required = 0u32;
    unsafe {
        GetTokenInformation(token, class, ptr::null_mut(), 0, &mut required);
    }
    if required == 0 || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER {
        exit_error("authority_process_token_query_failed");
    }
    let word_size = std::mem::size_of::<usize>();
    let word_count = (required as usize)
        .checked_add(word_size - 1)
        .unwrap_or_else(|| exit_error("authority_process_token_query_failed"))
        / word_size;
    let mut words = vec![0usize; word_count];
    let mut returned = 0u32;
    if unsafe {
        GetTokenInformation(
            token,
            class,
            words.as_mut_ptr().cast(),
            required,
            &mut returned,
        )
    } == 0
        || returned != required
    {
        exit_error("authority_process_token_query_failed");
    }
    words
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

#[cfg(test)]
mod helper_tests {
    use super::*;

    fn install_action_arguments() -> Vec<OsString> {
        vec![
            OsString::from("--execute-install"),
            OsString::from(r"C:\bundle\service.exe"),
            OsString::from(r"C:\bundle\controller.exe"),
            OsString::from(r"C:\bundle\helper.exe"),
            OsString::from(r"C:\bundle\lifecycle-driver.exe"),
            OsString::from(r"C:\bundle\bridge-launcher.exe"),
            OsString::from(r"C:\bundle\runtime-source-manifest.json"),
            OsString::from("11".repeat(32)),
            OsString::from("22".repeat(32)),
            OsString::from("33".repeat(32)),
            OsString::from("44".repeat(32)),
            OsString::from("55".repeat(32)),
            OsString::from("99".repeat(32)),
            OsString::from("aa".repeat(32)),
            OsString::from("88".repeat(32)),
            OsString::from(r"C:\bundle\consent.json"),
            OsString::from("66".repeat(32)),
        ]
    }

    fn successor_action_arguments(command: &str) -> Vec<OsString> {
        let mut values = install_action_arguments();
        values[0] = OsString::from(command);
        values.insert(7, OsString::from("77".repeat(32)));
        values
    }

    #[test]
    fn action_time_commands_have_exact_operation_shapes_and_explicit_hashes() {
        let arguments = install_action_arguments();
        let parsed = parse_action_maintenance_arguments(&arguments).unwrap();
        assert_eq!(parsed.operation, ActionMaintenanceOperation::Install);
        assert_eq!(parsed.prior_generation, None);
        assert_eq!(parsed.plan_sha256, [0x11; 32]);
        assert_eq!(parsed.generation, [0x22; 32]);
        assert_eq!(parsed.service_sha256, [0x33; 32]);
        assert_eq!(parsed.controller_sha256, [0x44; 32]);
        assert_eq!(parsed.install_helper_sha256, [0x55; 32]);
        assert_eq!(parsed.lifecycle_driver_sha256, [0x99; 32]);
        assert_eq!(parsed.bridge_launcher_sha256, [0xaa; 32]);
        assert_eq!(parsed.runtime_source_manifest_sha256, [0x88; 32]);
        assert_eq!(parsed.consent_sha256, [0x66; 32]);

        for (command, operation) in [
            ("--execute-update", ActionMaintenanceOperation::Update),
            ("--execute-retire", ActionMaintenanceOperation::Retire),
        ] {
            let arguments = successor_action_arguments(command);
            let parsed = parse_action_maintenance_arguments(&arguments).unwrap();
            assert_eq!(parsed.operation, operation);
            assert_eq!(parsed.prior_generation, Some([0x77; 32]));
            assert_eq!(parsed.plan_sha256, [0x11; 32]);
            assert_eq!(parsed.generation, [0x22; 32]);
            assert_eq!(parsed.consent_sha256, [0x66; 32]);
        }

        let mut extra = install_action_arguments();
        extra.push(OsString::from("extra"));
        assert_eq!(
            parse_action_maintenance_arguments(&extra).unwrap_err(),
            "authority_action_time_arguments_invalid"
        );
        let mut missing = successor_action_arguments("--execute-update");
        missing.pop();
        assert_eq!(
            parse_action_maintenance_arguments(&missing).unwrap_err(),
            "authority_action_time_arguments_invalid"
        );
        let mut wrong_command = install_action_arguments();
        wrong_command[0] = OsString::from("--provision");
        assert_eq!(
            parse_action_maintenance_arguments(&wrong_command).unwrap_err(),
            "authority_action_time_arguments_invalid"
        );

        let mut install_with_prior = install_action_arguments();
        install_with_prior.insert(7, OsString::from("77".repeat(32)));
        assert_eq!(
            parse_action_maintenance_arguments(&install_with_prior).unwrap_err(),
            "authority_action_time_arguments_invalid"
        );
    }

    #[test]
    fn action_time_command_rejects_zero_uppercase_and_malformed_hashes() {
        for invalid in ["00".repeat(32), "AA".repeat(32), "1".repeat(63)] {
            let mut arguments = install_action_arguments();
            arguments[7] = OsString::from(invalid.clone());
            assert_eq!(
                parse_action_maintenance_arguments(&arguments).unwrap_err(),
                "authority_action_time_plan_digest_invalid"
            );

            let mut successor = successor_action_arguments("--execute-update");
            successor[7] = OsString::from(invalid);
            assert_eq!(
                parse_action_maintenance_arguments(&successor).unwrap_err(),
                "authority_prior_generation_expected_invalid"
            );
        }
    }
}
