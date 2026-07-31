use super::{receipt::*, *};
use std::{
    fs::{File, OpenOptions},
    io::{Read, Seek, SeekFrom},
    mem::size_of,
    os::windows::{
        fs::{MetadataExt, OpenOptionsExt},
        io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle},
    },
    path::{Path, PathBuf},
    ptr,
};
use windows_sys::Win32::{
    Foundation::{CloseHandle, FILETIME, HANDLE},
    Security::{
        CreateWellKnownSid, EqualSid, GetSidSubAuthority, GetSidSubAuthorityCount,
        GetTokenInformation, TokenElevation, TokenIntegrityLevel, TokenSessionId, TokenUser,
        WinLocalSystemSid, TOKEN_ELEVATION, TOKEN_INFORMATION_CLASS, TOKEN_MANDATORY_LABEL,
        TOKEN_QUERY, TOKEN_USER,
    },
    Storage::FileSystem::{
        GetDriveTypeW, GetFileInformationByHandle, GetVolumePathNameW, BY_HANDLE_FILE_INFORMATION,
        FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_OPEN_REPARSE_POINT, FILE_FLAG_SEQUENTIAL_SCAN,
        FILE_SHARE_READ,
    },
    System::{
        SystemServices::SECURITY_MANDATORY_HIGH_RID,
        Threading::{
            GetCurrentProcessId, GetProcessTimes, OpenProcess, OpenProcessToken,
            QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
        },
    },
};

const OPEN_POLICY_DOMAIN: &[u8] = b"vrcforge-authority-source-open-policy-v1\0";
const PROCESS_SYNCHRONIZE: u32 = 0x0010_0000;

pub(super) struct NativeSealedInstalledGenerationSource<'a> {
    layout: &'a AuthorityLayout,
}

impl<'a> NativeSealedInstalledGenerationSource<'a> {
    pub(super) fn new(layout: &'a AuthorityLayout) -> Self {
        Self { layout }
    }
}

impl SealedInstalledGenerationSource for NativeSealedInstalledGenerationSource<'_> {
    fn read_sealed_generation(
        &mut self,
        expected_generation: [u8; 32],
    ) -> Result<SealedInstalledGenerationReadback, AuthorityMaintenanceError> {
        let _ = self
            .layout
            .generation_state_root(&expected_generation)
            .map_err(|_| AuthorityMaintenanceError("authority_prior_generation_layout_invalid"))?;
        // A prior generation is accepted only after the running protected
        // service signs its exact generation/process/pipe instance and the
        // helper independently binds that frame to protected files, key,
        // ledger, SCM configuration, and manifest history. The service-issued
        // attestation transport is intentionally not substituted with the
        // diagnostic SCM projection or caller-provided JSON.
        Err(AuthorityMaintenanceError(
            "authority_prior_generation_service_attestation_not_connected",
        ))
    }
}

pub(super) fn prepare_native_install_sources(
    layout: &AuthorityLayout,
    service_path: &Path,
    controller_path: &Path,
    install_helper_path: &Path,
    lifecycle_driver_path: &Path,
    bridge_launcher_path: &Path,
    runtime_source_manifest_path: &Path,
) -> Result<NativeInstallPreparation, AuthorityMaintenanceError> {
    prepare_native_sources(
        layout,
        service_path,
        controller_path,
        install_helper_path,
        lifecycle_driver_path,
        bridge_launcher_path,
        runtime_source_manifest_path,
        |layout, content| preview_install(layout, content.clone()),
    )
}

pub(super) fn prepare_native_update_sources(
    layout: &AuthorityLayout,
    service_path: &Path,
    controller_path: &Path,
    install_helper_path: &Path,
    lifecycle_driver_path: &Path,
    bridge_launcher_path: &Path,
    runtime_source_manifest_path: &Path,
    prior: VerifiedInstalledGeneration,
) -> Result<NativeInstallPreparation, AuthorityMaintenanceError> {
    prepare_native_sources(
        layout,
        service_path,
        controller_path,
        install_helper_path,
        lifecycle_driver_path,
        bridge_launcher_path,
        runtime_source_manifest_path,
        move |layout, content| preview_update(layout, content.clone(), prior),
    )
}

pub(super) fn prepare_native_retire_sources(
    layout: &AuthorityLayout,
    service_path: &Path,
    controller_path: &Path,
    install_helper_path: &Path,
    lifecycle_driver_path: &Path,
    bridge_launcher_path: &Path,
    runtime_source_manifest_path: &Path,
    prior: VerifiedInstalledGeneration,
) -> Result<NativeInstallPreparation, AuthorityMaintenanceError> {
    prepare_native_sources(
        layout,
        service_path,
        controller_path,
        install_helper_path,
        lifecycle_driver_path,
        bridge_launcher_path,
        runtime_source_manifest_path,
        move |layout, content| {
            let expected = AuthorityInstallContent::new(
                prior.service,
                prior.controller,
                prior.install_helper,
                prior.lifecycle_driver,
                prior.bridge_launcher,
                prior.runtime_source_manifest,
            )?;
            if content != &expected {
                return Err(AuthorityMaintenanceError(
                    "authority_retire_source_binding_mismatch",
                ));
            }
            preview_retire(layout, prior)
        },
    )
}

fn prepare_native_sources<F>(
    layout: &AuthorityLayout,
    service_path: &Path,
    controller_path: &Path,
    install_helper_path: &Path,
    lifecycle_driver_path: &Path,
    bridge_launcher_path: &Path,
    runtime_source_manifest_path: &Path,
    build_operation_preview: F,
) -> Result<NativeInstallPreparation, AuthorityMaintenanceError>
where
    F: FnOnce(
        &AuthorityLayout,
        &AuthorityInstallContent,
    ) -> Result<AuthorityMaintenancePreview, AuthorityMaintenanceError>,
{
    let service = open_verified_payload(service_path, MAX_AUTHORITY_BINARY_BYTES)?;
    let controller = open_verified_payload(controller_path, MAX_AUTHORITY_BINARY_BYTES)?;
    let install_helper = open_verified_payload(install_helper_path, MAX_AUTHORITY_BINARY_BYTES)?;
    let lifecycle_driver =
        open_verified_payload(lifecycle_driver_path, MAX_AUTHORITY_BINARY_BYTES)?;
    let bridge_launcher = open_verified_payload(bridge_launcher_path, MAX_AUTHORITY_BINARY_BYTES)?;
    let runtime_source_manifest = open_verified_payload(
        runtime_source_manifest_path,
        MAX_RUNTIME_SOURCE_MANIFEST_BYTES,
    )?;
    let content = AuthorityInstallContent::new(
        service.observation.descriptor,
        controller.observation.descriptor,
        install_helper.observation.descriptor,
        lifecycle_driver.observation.descriptor,
        bridge_launcher.observation.descriptor,
        runtime_source_manifest.observation.descriptor,
    )?;
    let preview = build_operation_preview(layout, &content)?;
    let payloads = VerifiedPayloadSet::from_held_observations(
        &content,
        service.observation,
        controller.observation,
        install_helper.observation,
        lifecycle_driver.observation,
        bridge_launcher.observation,
        runtime_source_manifest.observation,
    )?;

    let process_id = unsafe { GetCurrentProcessId() };
    let process = unsafe {
        OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
            0,
            process_id,
        )
    };
    if process.is_null() {
        return Err(AuthorityMaintenanceError(
            "authority_bootstrap_process_handle_unavailable",
        ));
    }
    let process = unsafe { OwnedHandle::from_raw_handle(process as RawHandle) };
    let process_creation_time = process_creation_time(process.as_raw_handle().cast())?;
    let image_path = process_image_path(process.as_raw_handle().cast())?;
    let running_image = open_verified_payload(&image_path, MAX_AUTHORITY_BINARY_BYTES)?;
    if running_image.observation.descriptor != install_helper.observation.descriptor
        || running_image.observation.volume_serial != install_helper.observation.volume_serial
        || running_image.observation.file_id != install_helper.observation.file_id
    {
        return Err(AuthorityMaintenanceError(
            "authority_action_time_helper_identity_mismatch",
        ));
    }
    let security = current_process_security()?;
    if !security.elevated
        || !security.high_integrity
        || security.local_system
        || security.session_id == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_action_time_elevation_required",
        ));
    }
    let bootstrap_helper = VerifiedBootstrapHelperIdentity::from_running_helper(
        content.install_helper(),
        RawBootstrapHelperObservation {
            process_id,
            process_creation_time,
            image_volume_serial: running_image.observation.volume_serial,
            image_file_id: running_image.observation.file_id,
            image_sha256: *running_image.observation.descriptor.sha256(),
            image_byte_length: running_image.observation.descriptor.byte_length(),
            image_handle_held: true,
            elevated_token: security.elevated,
            high_integrity: security.high_integrity,
            local_system: security.local_system,
            session_id: security.session_id,
        },
    )?;
    let lease = VerifiedMaintenanceLease {
        payloads,
        bootstrap_helper,
        held_payloads: HeldPayloadLease::Native(NativeHeldPayloadLease {
            _service: service.file.into(),
            _controller: controller.file.into(),
            _install_helper: install_helper.file.into(),
            _lifecycle_driver: lifecycle_driver.file.into(),
            _bridge_launcher: bridge_launcher.file.into(),
            _runtime_source_manifest: runtime_source_manifest.file.into(),
            _bootstrap_process: process,
            _bootstrap_running_image: running_image.file.into(),
        }),
        plan_sha256: preview.plan_sha256()?,
        generation: preview.generation_sha256()?,
    };
    Ok(NativeInstallPreparation {
        preview,
        content,
        lease,
    })
}

struct OpenedPayload {
    file: File,
    observation: RawHeldPayloadObservation,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct FileIdentity {
    volume_serial: u64,
    file_id: [u8; 16],
    byte_length: u64,
    link_count: u32,
}

fn open_verified_payload(
    path: &Path,
    maximum_bytes: u64,
) -> Result<OpenedPayload, AuthorityMaintenanceError> {
    if !path.is_absolute() || !path_is_local(path) {
        return Err(AuthorityMaintenanceError("authority_payload_path_invalid"));
    }
    let path_metadata = std::fs::symlink_metadata(path)
        .map_err(|_| AuthorityMaintenanceError("authority_payload_metadata_failed"))?;
    if !path_metadata.is_file()
        || path_metadata.file_type().is_symlink()
        || path_metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || path_metadata.len() == 0
        || path_metadata.len() > maximum_bytes
    {
        return Err(AuthorityMaintenanceError(
            "authority_payload_metadata_invalid",
        ));
    }
    let mut file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN)
        .open(path)
        .map_err(|_| AuthorityMaintenanceError("authority_payload_open_failed"))?;
    let before = file_identity(&file)?;
    if before.byte_length != path_metadata.len() || before.link_count != 1 {
        return Err(AuthorityMaintenanceError(
            "authority_payload_identity_invalid",
        ));
    }
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    let mut byte_length = 0u64;
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|_| AuthorityMaintenanceError("authority_payload_read_failed"))?;
        if count == 0 {
            break;
        }
        byte_length = byte_length
            .checked_add(count as u64)
            .ok_or(AuthorityMaintenanceError("authority_payload_size_invalid"))?;
        digest.update(&buffer[..count]);
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| AuthorityMaintenanceError("authority_payload_seek_failed"))?;
    let after = file_identity(&file)?;
    if before != after || byte_length != before.byte_length {
        return Err(AuthorityMaintenanceError(
            "authority_payload_changed_during_hash",
        ));
    }
    let descriptor = AuthorityPayloadDigest::new(digest.finalize().into(), byte_length)?;
    let handle_identity = file.as_raw_handle() as usize as u64;
    if handle_identity == 0 || handle_identity == u64::MAX {
        return Err(AuthorityMaintenanceError(
            "authority_payload_handle_not_verified",
        ));
    }
    let open_policy_receipt_sha256 = source_open_policy_receipt(&before, handle_identity);
    let full_readback_receipt_sha256 = source_full_readback_receipt(
        &descriptor,
        before.volume_serial,
        &before.file_id,
        before.link_count,
    );
    Ok(OpenedPayload {
        file,
        observation: RawHeldPayloadObservation {
            descriptor,
            volume_serial: before.volume_serial,
            file_id: before.file_id,
            post_read_descriptor: descriptor,
            post_read_volume_serial: after.volume_serial,
            post_read_file_id: after.file_id,
            handle_identity,
            regular_file: true,
            reparse_point: false,
            handle_held: true,
            write_sharing_denied: true,
            delete_sharing_denied: true,
            open_policy_receipt_sha256,
            full_readback_receipt_sha256,
        },
    })
}

fn file_identity(file: &File) -> Result<FileIdentity, AuthorityMaintenanceError> {
    let mut information = unsafe { std::mem::zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle().cast(), &mut information) } == 0
        || information.dwVolumeSerialNumber == 0
        || (information.nFileIndexHigh == 0 && information.nFileIndexLow == 0)
    {
        return Err(AuthorityMaintenanceError(
            "authority_file_identity_unavailable",
        ));
    }
    let file_index =
        (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow);
    let mut file_id = [0u8; 16];
    file_id[..8].copy_from_slice(&file_index.to_be_bytes());
    Ok(FileIdentity {
        volume_serial: u64::from(information.dwVolumeSerialNumber),
        file_id,
        byte_length: (u64::from(information.nFileSizeHigh) << 32)
            | u64::from(information.nFileSizeLow),
        link_count: information.nNumberOfLinks,
    })
}

fn source_open_policy_receipt(identity: &FileIdentity, handle_identity: u64) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(OPEN_POLICY_DOMAIN);
    digest.update(identity.volume_serial.to_be_bytes());
    digest.update(identity.file_id);
    digest.update(identity.byte_length.to_be_bytes());
    digest.update(identity.link_count.to_be_bytes());
    digest.update(handle_identity.to_be_bytes());
    digest.update(FILE_SHARE_READ.to_be_bytes());
    digest.update((FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN).to_be_bytes());
    digest.finalize().into()
}

fn path_is_local(path: &Path) -> bool {
    use std::os::windows::ffi::OsStrExt;
    const DRIVE_REMOTE: u32 = 4;
    let encoded = path
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let mut root = [0u16; 32_768];
    unsafe {
        GetVolumePathNameW(encoded.as_ptr(), root.as_mut_ptr(), root.len() as u32) != 0
            && GetDriveTypeW(root.as_ptr()) != DRIVE_REMOTE
    }
}

fn process_creation_time(process: HANDLE) -> Result<u64, AuthorityMaintenanceError> {
    let mut creation = unsafe { std::mem::zeroed::<FILETIME>() };
    let mut exit = unsafe { std::mem::zeroed::<FILETIME>() };
    let mut kernel = unsafe { std::mem::zeroed::<FILETIME>() };
    let mut user = unsafe { std::mem::zeroed::<FILETIME>() };
    if unsafe { GetProcessTimes(process, &mut creation, &mut exit, &mut kernel, &mut user) } == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_bootstrap_process_times_unavailable",
        ));
    }
    let value = (u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime);
    if value == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_bootstrap_process_times_unavailable",
        ));
    }
    Ok(value)
}

fn process_image_path(process: HANDLE) -> Result<PathBuf, AuthorityMaintenanceError> {
    use std::os::windows::ffi::OsStringExt;
    let mut buffer = vec![0u16; 32_768];
    let mut length = buffer.len() as u32;
    if unsafe { QueryFullProcessImageNameW(process, 0, buffer.as_mut_ptr(), &mut length) } == 0
        || length == 0
        || length as usize >= buffer.len()
    {
        return Err(AuthorityMaintenanceError(
            "authority_bootstrap_process_image_path_unavailable",
        ));
    }
    buffer.truncate(length as usize);
    if buffer.contains(&0) {
        return Err(AuthorityMaintenanceError(
            "authority_bootstrap_process_image_path_unavailable",
        ));
    }
    Ok(PathBuf::from(std::ffi::OsString::from_wide(&buffer)))
}

#[derive(Debug, Clone, Copy)]
pub(super) struct ProcessSecurity {
    pub(super) elevated: bool,
    pub(super) high_integrity: bool,
    pub(super) local_system: bool,
    pub(super) session_id: u32,
}

fn current_process_security() -> Result<ProcessSecurity, AuthorityMaintenanceError> {
    process_security(unsafe_process_handle())
}

pub(super) fn process_security(
    process: HANDLE,
) -> Result<ProcessSecurity, AuthorityMaintenanceError> {
    let mut token = ptr::null_mut();
    if process.is_null()
        || unsafe { OpenProcessToken(process, TOKEN_QUERY, &mut token) } == 0
        || token.is_null()
    {
        return Err(AuthorityMaintenanceError(
            "authority_process_token_unavailable",
        ));
    }
    struct Token(HANDLE);
    impl Drop for Token {
        fn drop(&mut self) {
            if !self.0.is_null() {
                unsafe { CloseHandle(self.0) };
            }
        }
    }
    let token = Token(token);
    let elevation: TOKEN_ELEVATION = query_token_fixed(token.0, TokenElevation)?;
    let session_id: u32 = query_token_fixed(token.0, TokenSessionId)?;
    let integrity = query_token_buffer(token.0, TokenIntegrityLevel)?;
    let label = unsafe { &*(integrity.as_ptr().cast::<TOKEN_MANDATORY_LABEL>()) };
    let count = unsafe { *GetSidSubAuthorityCount(label.Label.Sid) } as u32;
    if count == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_process_integrity_invalid",
        ));
    }
    let rid = unsafe { *GetSidSubAuthority(label.Label.Sid, count - 1) };
    let user = query_token_buffer(token.0, TokenUser)?;
    let token_user = unsafe { &*(user.as_ptr().cast::<TOKEN_USER>()) };
    let mut system_sid = [0u8; 68];
    let mut system_sid_size = system_sid.len() as u32;
    if unsafe {
        CreateWellKnownSid(
            WinLocalSystemSid,
            ptr::null_mut(),
            system_sid.as_mut_ptr().cast(),
            &mut system_sid_size,
        )
    } == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_system_sid_unavailable",
        ));
    }
    Ok(ProcessSecurity {
        elevated: elevation.TokenIsElevated != 0,
        high_integrity: rid >= SECURITY_MANDATORY_HIGH_RID as u32,
        local_system: unsafe { EqualSid(token_user.User.Sid, system_sid.as_mut_ptr().cast()) != 0 },
        session_id,
    })
}

fn unsafe_process_handle() -> HANDLE {
    unsafe { windows_sys::Win32::System::Threading::GetCurrentProcess() }
}

fn query_token_fixed<T: Copy>(
    token: HANDLE,
    class: TOKEN_INFORMATION_CLASS,
) -> Result<T, AuthorityMaintenanceError> {
    let mut value = std::mem::MaybeUninit::<T>::zeroed();
    let mut returned = 0u32;
    if unsafe {
        GetTokenInformation(
            token,
            class,
            value.as_mut_ptr().cast(),
            size_of::<T>() as u32,
            &mut returned,
        )
    } == 0
        || returned as usize != size_of::<T>()
    {
        return Err(AuthorityMaintenanceError(
            "authority_process_token_query_failed",
        ));
    }
    Ok(unsafe { value.assume_init() })
}

fn query_token_buffer(
    token: HANDLE,
    class: TOKEN_INFORMATION_CLASS,
) -> Result<Vec<usize>, AuthorityMaintenanceError> {
    let mut required = 0u32;
    unsafe {
        GetTokenInformation(token, class, ptr::null_mut(), 0, &mut required);
    }
    if required == 0 || required > 64 * 1024 {
        return Err(AuthorityMaintenanceError(
            "authority_process_token_query_failed",
        ));
    }
    let word_count = (required as usize)
        .checked_add(size_of::<usize>() - 1)
        .ok_or(AuthorityMaintenanceError(
            "authority_process_token_query_failed",
        ))?
        / size_of::<usize>();
    let mut buffer = vec![0usize; word_count];
    let mut written = 0u32;
    if unsafe {
        GetTokenInformation(
            token,
            class,
            buffer.as_mut_ptr().cast(),
            required,
            &mut written,
        )
    } == 0
        || written != required
    {
        return Err(AuthorityMaintenanceError(
            "authority_process_token_query_failed",
        ));
    }
    Ok(buffer)
}
