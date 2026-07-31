use super::{
    receipt_windows::process_security,
    worker::{
        worker_bootstrap_file_readback_receipt, worker_handoff_pipe_name,
        DurableSourceStagingReceipt, MaintenanceWorkerCapsule, MaintenanceWorkerJournalRecord,
        MaintenanceWorkerLaunchContract, OneShotDuplicatedHandleValues, ServiceCreatedReceipt,
        WorkerBootstrapStagingReceipt, WorkerExitReadyReceipt, WorkerHandleHandoffReceipt,
        WorkerLiveReadback, WorkerPipePreparedReceipt, WorkerPipeRecoveryReceipt,
        WorkerProcessBinding, WorkerStagingCleanupReceipt, WorkerStartedReceipt,
    },
    worker_store_windows::NativePersistedPipePrepared,
    AuthorityMaintenanceError, MAINTENANCE_SERVICE_SID, PROTECTED_GENERATION_PAYLOAD_COUNT,
};
use sha2::{Digest, Sha256};
use std::{
    ffi::OsString,
    fs::{File, OpenOptions},
    io::{Read, Seek, SeekFrom},
    mem::{size_of, MaybeUninit},
    os::windows::{
        ffi::{OsStrExt, OsStringExt},
        fs::{MetadataExt, OpenOptionsExt},
        io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle},
    },
    path::Path,
    ptr,
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::{
        DuplicateHandle, GetLastError, LocalFree, DUPLICATE_CLOSE_SOURCE, DUPLICATE_SAME_ACCESS,
        ERROR_INSUFFICIENT_BUFFER, ERROR_IO_PENDING, ERROR_PIPE_CONNECTED,
        ERROR_SERVICE_DOES_NOT_EXIST, FILETIME, GENERIC_READ, GENERIC_WRITE, INVALID_HANDLE_VALUE,
        STILL_ACTIVE, WAIT_OBJECT_0, WAIT_TIMEOUT,
    },
    Security::{
        Authorization::{
            ConvertSecurityDescriptorToStringSecurityDescriptorW,
            ConvertStringSecurityDescriptorToSecurityDescriptorW, ConvertStringSidToSidW,
            SDDL_REVISION_1,
        },
        EqualSid, GetTokenInformation, IsValidSid, TokenRestrictedSids, DACL_SECURITY_INFORMATION,
        GROUP_SECURITY_INFORMATION, LABEL_SECURITY_INFORMATION, OWNER_SECURITY_INFORMATION,
        PSECURITY_DESCRIPTOR, PSID, SECURITY_ATTRIBUTES, SID_AND_ATTRIBUTES, TOKEN_GROUPS,
        TOKEN_QUERY,
    },
    Storage::FileSystem::{
        CreateFileW, GetFileInformationByHandle, ReadFile, WriteFile, BY_HANDLE_FILE_INFORMATION,
        DELETE, FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_FIRST_PIPE_INSTANCE,
        FILE_FLAG_OPEN_REPARSE_POINT, FILE_FLAG_OVERLAPPED, FILE_FLAG_SEQUENTIAL_SCAN,
        FILE_SHARE_READ, OPEN_EXISTING, PIPE_ACCESS_DUPLEX, READ_CONTROL, WRITE_DAC,
    },
    System::{
        Pipes::{
            ConnectNamedPipe, CreateNamedPipeW, GetNamedPipeClientProcessId,
            GetNamedPipeClientSessionId, GetNamedPipeServerProcessId, SetNamedPipeHandleState,
            WaitNamedPipeW, PIPE_READMODE_MESSAGE, PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_MESSAGE,
            PIPE_WAIT,
        },
        Services::{
            ChangeServiceConfig2W, CloseServiceHandle, ControlService, CreateServiceW,
            DeleteService, OpenSCManagerW, OpenServiceW, QueryServiceConfig2W, QueryServiceConfigW,
            QueryServiceObjectSecurity, QueryServiceStatusEx, SetServiceObjectSecurity,
            StartServiceW, QUERY_SERVICE_CONFIGW, SC_HANDLE, SC_MANAGER_CONNECT,
            SC_MANAGER_CREATE_SERVICE, SC_STATUS_PROCESS_INFO, SERVICE_CHANGE_CONFIG,
            SERVICE_CONFIG_REQUIRED_PRIVILEGES_INFO, SERVICE_CONFIG_SERVICE_SID_INFO,
            SERVICE_CONTROL_STOP, SERVICE_DEMAND_START, SERVICE_ERROR_NORMAL, SERVICE_QUERY_CONFIG,
            SERVICE_QUERY_STATUS, SERVICE_REQUIRED_PRIVILEGES_INFOW, SERVICE_RUNNING,
            SERVICE_SID_INFO, SERVICE_START, SERVICE_START_PENDING, SERVICE_STATUS,
            SERVICE_STATUS_PROCESS, SERVICE_STOP, SERVICE_STOPPED, SERVICE_STOP_PENDING,
            SERVICE_WIN32_OWN_PROCESS,
        },
        Threading::{
            CreateEventW, GetCurrentProcess, GetCurrentProcessId, GetExitCodeProcess, GetProcessId,
            GetProcessTimes, OpenProcess, OpenProcessToken, QueryFullProcessImageNameW,
            WaitForMultipleObjects, WaitForSingleObject, PROCESS_DUP_HANDLE,
            PROCESS_QUERY_LIMITED_INFORMATION,
        },
        IO::{CancelIoEx, GetOverlappedResult, OVERLAPPED},
    },
};

// The worker uses ordinary, handle-relative file rights. SACL verification is
// the only operation that needs a token privilege, and it is enabled only by a
// scoped guard around the relevant open.
const WORKER_REQUIRED_PRIVILEGES: [&str; 1] = ["SeSecurityPrivilege"];
const SERVICE_SID_TYPE_RESTRICTED: u32 = 3;
const SECURITY_INFORMATION: u32 = OWNER_SECURITY_INFORMATION
    | GROUP_SECURITY_INFORMATION
    | DACL_SECURITY_INFORMATION
    | LABEL_SECURITY_INFORMATION;
const WORKER_START_TIMEOUT: Duration = Duration::from_secs(20);
const WORKER_ABSENCE_TIMEOUT: Duration = Duration::from_secs(20);
const WORKER_NATURAL_EXIT_GRACE: Duration = Duration::from_secs(2);
const WORKER_START_PENDING_EXIT_GRACE: Duration = Duration::from_secs(35);
const WORKER_HANDOFF_TIMEOUT: Duration = Duration::from_secs(30);
const WORKER_HANDOFF_PIPE_BYTES: u32 = 64 * 1024;
const WORKER_HANDOFF_PIPE_SDDL: &str = concat!(
    "D:P(A;;GA;;;SY)(A;;GA;;;BA)",
    "(A;;GA;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)",
    "S:(ML;;NW;;;HI)"
);
const MAX_WORKER_RESTRICTED_SIDS: usize = 64;
const WORKER_STAGING_ACK_DOMAIN: &[u8] = b"vrcforge-authority-worker-staging-ack-v2\0";
const PROCESS_SYNCHRONIZE: u32 = 0x0010_0000;
const WORKER_SERVICE_LIFECYCLE_ACCESS: u32 = SERVICE_CHANGE_CONFIG
    | SERVICE_QUERY_CONFIG
    | SERVICE_QUERY_STATUS
    | SERVICE_START
    | SERVICE_STOP
    | DELETE
    | READ_CONTROL
    | WRITE_DAC;

pub(super) struct NativeWorkerServiceLease {
    manager: ServiceHandle,
    service: ServiceHandle,
    process: OwnedHandle,
    process_id: u32,
    process_binding: WorkerProcessBinding,
    delete_pending: bool,
}

impl NativeWorkerServiceLease {
    pub(super) fn process_id(&self) -> u32 {
        self.process_id
    }

    pub(super) fn delete_pending(&self) -> bool {
        self.delete_pending
    }

    pub(super) fn process_binding(&self) -> &WorkerProcessBinding {
        &self.process_binding
    }

    fn process_raw(&self) -> windows_sys::Win32::Foundation::HANDLE {
        self.process.as_raw_handle().cast()
    }

    pub(super) fn duplicate_source_handles(
        &self,
        source_handles: [windows_sys::Win32::Foundation::HANDLE;
            PROTECTED_GENERATION_PAYLOAD_COUNT],
    ) -> Result<NativeDuplicatedSourceHandles<'_>, AuthorityMaintenanceError> {
        NativeDuplicatedSourceHandles::duplicate(self, source_handles)
    }
}

pub(super) struct NativeDuplicatedSourceHandles<'a> {
    worker: &'a NativeWorkerServiceLease,
    target_handles: [windows_sys::Win32::Foundation::HANDLE; PROTECTED_GENERATION_PAYLOAD_COUNT],
    transferred: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct NativeWorkerServiceRemovalOutcome {
    pub(super) stop_was_required: bool,
    pub(super) delete_pending_readback_sha256: [u8; 32],
    pub(super) handles_closed_readback_sha256: [u8; 32],
    pub(super) service_absence_readback_sha256: [u8; 32],
}

pub(super) struct NativeWorkerServiceDeletePendingLease {
    lease: NativeWorkerServiceLease,
    stop_was_required: bool,
    delete_pending_readback_sha256: [u8; 32],
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct NativeWorkerFinalizerHandlesClosed {
    worker: WorkerProcessBinding,
    stop_was_required: bool,
    delete_pending_readback_sha256: [u8; 32],
    handles_closed_readback_sha256: [u8; 32],
}

impl NativeWorkerServiceDeletePendingLease {
    pub(super) fn stop_was_required(&self) -> bool {
        self.stop_was_required
    }

    pub(super) fn delete_pending_readback_sha256(&self) -> [u8; 32] {
        self.delete_pending_readback_sha256
    }
}

impl NativeWorkerFinalizerHandlesClosed {
    pub(super) fn stop_was_required(&self) -> bool {
        self.stop_was_required
    }

    pub(super) fn delete_pending_readback_sha256(&self) -> [u8; 32] {
        self.delete_pending_readback_sha256
    }

    pub(super) fn handles_closed_readback_sha256(&self) -> [u8; 32] {
        self.handles_closed_readback_sha256
    }
}

impl NativeDuplicatedSourceHandles<'_> {
    fn duplicate<'a>(
        worker: &'a NativeWorkerServiceLease,
        source_handles: [windows_sys::Win32::Foundation::HANDLE;
            PROTECTED_GENERATION_PAYLOAD_COUNT],
    ) -> Result<NativeDuplicatedSourceHandles<'a>, AuthorityMaintenanceError> {
        if size_of::<windows_sys::Win32::Foundation::HANDLE>() > size_of::<u64>()
            || source_handles
                .iter()
                .any(|handle| handle.is_null() || *handle == INVALID_HANDLE_VALUE)
            || source_handles
                .iter()
                .enumerate()
                .any(|(index, handle)| source_handles[..index].contains(handle))
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_source_handle_set_invalid",
            ));
        }
        let mut result = NativeDuplicatedSourceHandles {
            worker,
            target_handles: [ptr::null_mut(); PROTECTED_GENERATION_PAYLOAD_COUNT],
            transferred: false,
        };
        for (index, source) in source_handles.iter().copied().enumerate() {
            let mut target = ptr::null_mut();
            if unsafe {
                DuplicateHandle(
                    GetCurrentProcess(),
                    source,
                    worker.process_raw(),
                    &mut target,
                    0,
                    0,
                    DUPLICATE_SAME_ACCESS,
                )
            } == 0
                || target.is_null()
                || target == INVALID_HANDLE_VALUE
            {
                return Err(AuthorityMaintenanceError(
                    "authority_worker_source_handle_duplicate_failed",
                ));
            }
            result.target_handles[index] = target;
        }
        Ok(result)
    }

    pub(super) fn values(&self) -> [u64; PROTECTED_GENERATION_PAYLOAD_COUNT] {
        self.target_handles.map(|handle| handle as usize as u64)
    }

    pub(super) fn transfer(mut self) -> [u64; PROTECTED_GENERATION_PAYLOAD_COUNT] {
        self.transferred = true;
        self.values()
    }
}

impl Drop for NativeDuplicatedSourceHandles<'_> {
    fn drop(&mut self) {
        if self.transferred {
            return;
        }
        for target in self.target_handles {
            if target.is_null() || target == INVALID_HANDLE_VALUE {
                continue;
            }
            let mut local_copy = ptr::null_mut();
            if unsafe {
                DuplicateHandle(
                    self.worker.process_raw(),
                    target,
                    GetCurrentProcess(),
                    &mut local_copy,
                    0,
                    0,
                    DUPLICATE_CLOSE_SOURCE | DUPLICATE_SAME_ACCESS,
                )
            } != 0
                && !local_copy.is_null()
                && local_copy != INVALID_HANDLE_VALUE
            {
                drop(unsafe { OwnedHandle::from_raw_handle(local_copy as RawHandle) });
            }
        }
    }
}

pub(super) fn current_helper_process_binding(
    capsule: &MaintenanceWorkerCapsule,
) -> Result<WorkerProcessBinding, AuthorityMaintenanceError> {
    let process = unsafe { GetCurrentProcess() };
    let binding = process_binding_from_handle(
        process,
        unsafe { GetCurrentProcessId() },
        &capsule.install_helper_sha256()?,
        None,
    )?;
    let image = inspect_stable_running_image(&process_image_path(process)?)?;
    let security = process_security(process)?;
    capsule.validate_live_helper_identity(
        &binding,
        image.byte_length,
        image.volume_serial,
        image.file_id,
        security.elevated,
        security.high_integrity,
        security.local_system,
        security.session_id,
    )?;
    Ok(binding)
}

pub(super) fn current_worker_process_binding(
    capsule: &MaintenanceWorkerCapsule,
    contract: &MaintenanceWorkerLaunchContract,
) -> Result<WorkerProcessBinding, AuthorityMaintenanceError> {
    if contract.worker_image_sha256()? != capsule.install_helper_sha256()? {
        return Err(AuthorityMaintenanceError(
            "authority_worker_image_binding_mismatch",
        ));
    }
    process_binding_from_handle(
        unsafe { GetCurrentProcess() },
        unsafe { GetCurrentProcessId() },
        &capsule.install_helper_sha256()?,
        Some(contract_executable_path(contract)?),
    )
}

pub(super) fn launched_worker_process_binding(
    capsule: &MaintenanceWorkerCapsule,
    contract: &MaintenanceWorkerLaunchContract,
    lease: &NativeWorkerServiceLease,
) -> Result<WorkerProcessBinding, AuthorityMaintenanceError> {
    if contract.worker_image_sha256()? != capsule.install_helper_sha256()? {
        return Err(AuthorityMaintenanceError(
            "authority_worker_image_binding_mismatch",
        ));
    }
    let expected_path = contract_executable_path(contract)?;
    let observed = process_binding_from_handle(
        lease.process_raw(),
        lease.process_id(),
        &capsule.install_helper_sha256()?,
        Some(expected_path),
    )?;
    if observed != lease.process_binding {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_identity_mismatch",
        ));
    }
    Ok(observed)
}

pub(super) fn wait_for_worker_transaction_ready(
    capsule: &MaintenanceWorkerCapsule,
    contract: &MaintenanceWorkerLaunchContract,
    lease: &NativeWorkerServiceLease,
) -> Result<WorkerProcessBinding, AuthorityMaintenanceError> {
    verify_exact_service(lease.service.0, contract)?;
    let process_id = wait_for_running(lease.service.0)?;
    if process_id != lease.process_id() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_identity_mismatch",
        ));
    }
    launched_worker_process_binding(capsule, contract, lease)
}

pub(super) fn live_worker_scm_readback(
    capsule: &MaintenanceWorkerCapsule,
    contract: &MaintenanceWorkerLaunchContract,
    started: &WorkerStartedReceipt,
    lease: &NativeWorkerServiceLease,
    pipe_server: &NativeWorkerHandoffServer,
) -> Result<WorkerLiveReadback, AuthorityMaintenanceError> {
    verify_exact_service(lease.service.0, contract)?;
    if pipe_server.expected_worker_process_id != Some(lease.process_id()) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_live_readback_missing",
        ));
    }
    observed_live_worker_scm_readback(capsule, contract, started, lease)
}

pub(super) fn observe_service_created_receipt(
    capsule: &MaintenanceWorkerCapsule,
    contract: &MaintenanceWorkerLaunchContract,
    bootstrap: &WorkerBootstrapStagingReceipt,
    pipe: &WorkerPipePreparedReceipt,
    lease: &NativeWorkerServiceLease,
) -> Result<ServiceCreatedReceipt, AuthorityMaintenanceError> {
    verify_exact_service(lease.service.0, contract)?;
    let (config, buffer) = query_primary_config(lease.service.0)?;
    let command = wide_string_in_buffer(config.lpBinaryPathName, &buffer)?;
    let account = wide_string_in_buffer(config.lpServiceStartName, &buffer)?;
    let sid =
        query_fixed_config::<SERVICE_SID_INFO>(lease.service.0, SERVICE_CONFIG_SERVICE_SID_INFO)?;
    let privileges = query_required_privileges(lease.service.0)?;
    let service_sddl = query_service_sddl(lease.service.0)?;
    let mut configuration = Sha256::new();
    configuration.update(b"vrcforge-authority-worker-service-config-readback-v1\0");
    configuration.update((command.len() as u64).to_be_bytes());
    configuration.update(command.as_bytes());
    configuration.update((account.len() as u64).to_be_bytes());
    configuration.update(account.as_bytes());
    configuration.update(config.dwServiceType.to_be_bytes());
    configuration.update(config.dwStartType.to_be_bytes());
    configuration.update(config.dwErrorControl.to_be_bytes());
    configuration.update(sid.dwServiceSidType.to_be_bytes());
    configuration.update((privileges.len() as u64).to_be_bytes());
    for privilege in privileges {
        configuration.update((privilege.len() as u64).to_be_bytes());
        configuration.update(privilege.as_bytes());
    }
    let configuration_sha256 = configuration.finalize().into();
    let mut security = Sha256::new();
    security.update(b"vrcforge-authority-worker-service-security-readback-v1\0");
    security.update((service_sddl.len() as u64).to_be_bytes());
    security.update(service_sddl.as_bytes());
    let security_sha256 = security.finalize().into();
    ServiceCreatedReceipt::from_observed(
        capsule,
        contract,
        bootstrap,
        pipe,
        configuration_sha256,
        security_sha256,
    )
}

fn observed_live_worker_scm_readback(
    capsule: &MaintenanceWorkerCapsule,
    contract: &MaintenanceWorkerLaunchContract,
    started: &WorkerStartedReceipt,
    lease: &NativeWorkerServiceLease,
) -> Result<WorkerLiveReadback, AuthorityMaintenanceError> {
    let binding = launched_worker_process_binding(capsule, contract, lease)?;
    let path = process_image_path(lease.process_raw())?;
    if !paths_equal_case_insensitive(&path, contract_executable_path(contract)?) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_image_path_mismatch",
        ));
    }
    let image = inspect_stable_running_image(&path)?;
    let security = process_security(lease.process_raw())?;
    WorkerLiveReadback::from_observed(
        started,
        binding.process_id(),
        binding.process_creation_time(),
        image.sha256,
        image.byte_length,
        image.volume_serial,
        image.file_id,
        image.full_readback_receipt_sha256,
        security.local_system,
        security.high_integrity,
        security.session_id,
        process_is_active(lease.process_raw()),
        started.pipe_instance_id()?,
    )
}

pub(super) fn recover_live_worker_scm_readback(
    capsule: &MaintenanceWorkerCapsule,
    contract: &MaintenanceWorkerLaunchContract,
    started: &WorkerStartedReceipt,
) -> Result<WorkerLiveReadback, AuthorityMaintenanceError> {
    let manager =
        ServiceHandle(unsafe { OpenSCManagerW(ptr::null(), ptr::null(), SC_MANAGER_CONNECT) });
    if manager.0.is_null() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_scm_unavailable",
        ));
    }
    let service_name = wide_null(contract.service_name);
    let service = ServiceHandle(unsafe {
        OpenServiceW(
            manager.0,
            service_name.as_ptr(),
            SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS | READ_CONTROL,
        )
    });
    if service.0.is_null() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_readback_denied",
        ));
    }
    verify_exact_service(service.0, contract)?;
    let process_id = query_running_worker_process_id(service.0)?;
    let process = unsafe {
        OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
            0,
            process_id,
        )
    };
    if process.is_null() || process == INVALID_HANDLE_VALUE {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_handle_unavailable",
        ));
    }
    let process = unsafe { OwnedHandle::from_raw_handle(process as RawHandle) };
    let process_binding = process_binding_from_handle(
        process.as_raw_handle().cast(),
        process_id,
        &capsule.install_helper_sha256()?,
        Some(contract_executable_path(contract)?),
    )?;
    let lease = NativeWorkerServiceLease {
        manager,
        service,
        process,
        process_id,
        process_binding,
        delete_pending: true,
    };
    observed_live_worker_scm_readback(capsule, contract, started, &lease)
}

fn contract_executable_path(
    contract: &MaintenanceWorkerLaunchContract,
) -> Result<&Path, AuthorityMaintenanceError> {
    let command = contract.binary_command();
    let closing_quote = command
        .get(1..)
        .and_then(|value| value.find('"'))
        .map(|index| index + 1)
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_launch_contract_invalid",
        ))?;
    let expected_path = command
        .get(1..closing_quote)
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_launch_contract_invalid",
        ))?;
    Ok(Path::new(expected_path))
}

fn process_binding_from_handle(
    process: windows_sys::Win32::Foundation::HANDLE,
    expected_process_id: u32,
    expected_image_sha256: &[u8; 32],
    expected_path: Option<&Path>,
) -> Result<WorkerProcessBinding, AuthorityMaintenanceError> {
    if process.is_null()
        || expected_process_id == 0
        || unsafe { GetProcessId(process) } != expected_process_id
        || expected_image_sha256.iter().all(|value| *value == 0)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_identity_invalid",
        ));
    }
    let creation_time = process_creation_time(process)?;
    let path_before = process_image_path(process)?;
    if expected_path.is_some_and(|expected| !paths_equal_case_insensitive(expected, &path_before)) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_image_path_mismatch",
        ));
    }
    let digest = hash_stable_running_image(&path_before)?;
    let path_after = process_image_path(process)?;
    if digest != *expected_image_sha256
        || !paths_equal_case_insensitive(&path_before, &path_after)
        || !process_is_active(process)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_image_binding_mismatch",
        ));
    }
    Ok(WorkerProcessBinding::new(
        expected_process_id,
        creation_time,
        digest,
    ))
}

fn process_creation_time(
    process: windows_sys::Win32::Foundation::HANDLE,
) -> Result<u64, AuthorityMaintenanceError> {
    let mut creation = unsafe { std::mem::zeroed::<FILETIME>() };
    let mut exit = unsafe { std::mem::zeroed::<FILETIME>() };
    let mut kernel = unsafe { std::mem::zeroed::<FILETIME>() };
    let mut user = unsafe { std::mem::zeroed::<FILETIME>() };
    if unsafe { GetProcessTimes(process, &mut creation, &mut exit, &mut kernel, &mut user) } == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_times_unavailable",
        ));
    }
    let value = (u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime);
    if value == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_times_unavailable",
        ));
    }
    Ok(value)
}

fn process_image_path(
    process: windows_sys::Win32::Foundation::HANDLE,
) -> Result<std::path::PathBuf, AuthorityMaintenanceError> {
    let mut buffer = vec![0u16; 32_768];
    let mut length = buffer.len() as u32;
    if unsafe { QueryFullProcessImageNameW(process, 0, buffer.as_mut_ptr(), &mut length) } == 0
        || length == 0
        || length as usize >= buffer.len()
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_image_path_unavailable",
        ));
    }
    buffer.truncate(length as usize);
    if buffer.contains(&0) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_image_path_unavailable",
        ));
    }
    Ok(std::path::PathBuf::from(OsString::from_wide(&buffer)))
}

fn process_is_active(process: windows_sys::Win32::Foundation::HANDLE) -> bool {
    let mut exit_code = 0u32;
    unsafe { GetExitCodeProcess(process, &mut exit_code) != 0 && exit_code == STILL_ACTIVE as u32 }
}

fn hash_stable_running_image(path: &Path) -> Result<[u8; 32], AuthorityMaintenanceError> {
    Ok(inspect_stable_running_image(path)?.sha256)
}

struct StableRunningImageObservation {
    sha256: [u8; 32],
    byte_length: u64,
    volume_serial: u64,
    file_id: [u8; 16],
    full_readback_receipt_sha256: [u8; 32],
}

fn inspect_stable_running_image(
    path: &Path,
) -> Result<StableRunningImageObservation, AuthorityMaintenanceError> {
    let metadata = std::fs::symlink_metadata(path)
        .map_err(|_| AuthorityMaintenanceError("authority_worker_process_image_metadata_failed"))?;
    if !metadata.is_file()
        || metadata.file_type().is_symlink()
        || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || metadata.len() == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_image_metadata_invalid",
        ));
    }
    let mut file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN)
        .open(path)
        .map_err(|_| AuthorityMaintenanceError("authority_worker_process_image_open_failed"))?;
    let before = stable_file_identity(&file)?;
    if before.3 != 1 || before.2 == 0 || before.2 != metadata.len() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_image_identity_invalid",
        ));
    }
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    let mut length = 0u64;
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_process_image_read_failed"))?;
        if count == 0 {
            break;
        }
        length = length
            .checked_add(count as u64)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_process_image_size_invalid",
            ))?;
        digest.update(&buffer[..count]);
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| AuthorityMaintenanceError("authority_worker_process_image_read_failed"))?;
    if stable_file_identity(&file)? != before || length != metadata.len() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_image_identity_changed",
        ));
    }
    let sha256 = digest.finalize().into();
    let mut file_id = [0u8; 16];
    file_id[..8].copy_from_slice(&before.1.to_be_bytes());
    Ok(StableRunningImageObservation {
        sha256,
        byte_length: before.2,
        volume_serial: u64::from(before.0),
        file_id,
        full_readback_receipt_sha256: worker_bootstrap_file_readback_receipt(
            "install-helper",
            &sha256,
            before.2,
            u64::from(before.0),
            &file_id,
        ),
    })
}

fn stable_file_identity(file: &File) -> Result<(u32, u64, u64, u32), AuthorityMaintenanceError> {
    let mut information = unsafe { std::mem::zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle().cast(), &mut information) } == 0
        || information.dwVolumeSerialNumber == 0
        || (information.nFileIndexHigh == 0 && information.nFileIndexLow == 0)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_image_identity_unavailable",
        ));
    }
    Ok((
        information.dwVolumeSerialNumber,
        (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow),
        (u64::from(information.nFileSizeHigh) << 32) | u64::from(information.nFileSizeLow),
        information.nNumberOfLinks,
    ))
}

fn paths_equal_case_insensitive(left: &Path, right: &Path) -> bool {
    left.as_os_str()
        .to_string_lossy()
        .eq_ignore_ascii_case(&right.as_os_str().to_string_lossy())
}

struct ServiceHandle(SC_HANDLE);

impl Drop for ServiceHandle {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                CloseServiceHandle(self.0);
            }
        }
    }
}

struct SecurityDescriptor(PSECURITY_DESCRIPTOR);

impl SecurityDescriptor {
    fn from_sddl(value: &str) -> Result<Self, AuthorityMaintenanceError> {
        let encoded = wide_null(value);
        let mut descriptor = ptr::null_mut();
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                encoded.as_ptr(),
                SDDL_REVISION_1,
                &mut descriptor,
                ptr::null_mut(),
            )
        } == 0
            || descriptor.is_null()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_service_sddl_invalid",
            ));
        }
        Ok(Self(descriptor))
    }
}

impl Drop for SecurityDescriptor {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                LocalFree(self.0);
            }
        }
    }
}

pub(super) struct NativeWorkerHandoffServer {
    pipe: OwnedHandle,
    expected_worker_process_id: Option<u32>,
    prepared: WorkerPipePreparedReceipt,
}

impl NativeWorkerHandoffServer {
    pub(super) fn create(
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let helper = current_helper_process_binding(capsule)?;
        let descriptor = SecurityDescriptor::from_sddl(WORKER_HANDOFF_PIPE_SDDL)?;
        let mut attributes = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor.0,
            bInheritHandle: 0,
        };
        let pipe_name = wide_null(&worker_handoff_pipe_name(capsule)?);
        let handle = unsafe {
            CreateNamedPipeW(
                pipe_name.as_ptr(),
                PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE | FILE_FLAG_OVERLAPPED,
                PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                1,
                WORKER_HANDOFF_PIPE_BYTES + 4,
                WORKER_HANDOFF_PIPE_BYTES + 4,
                WORKER_HANDOFF_TIMEOUT.as_millis() as u32,
                &mut attributes,
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_pipe_create_failed",
            ));
        }
        let pipe = unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) };
        let mut pipe_instance_id = [0u8; 16];
        getrandom::fill(&mut pipe_instance_id).map_err(|_| {
            AuthorityMaintenanceError("authority_worker_pipe_nonce_generation_failed")
        })?;
        if pipe_instance_id.iter().all(|value| *value == 0) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_pipe_nonce_generation_failed",
            ));
        }
        let prepared =
            WorkerPipePreparedReceipt::from_observed(capsule, launch, helper, pipe_instance_id)?;
        Ok(Self {
            pipe,
            expected_worker_process_id: None,
            prepared,
        })
    }

    pub(super) fn prepared_receipt(&self) -> &WorkerPipePreparedReceipt {
        &self.prepared
    }

    pub(super) fn rebuild_before_service(
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        records: &[MaintenanceWorkerJournalRecord],
        prior: Self,
    ) -> Result<(Self, WorkerPipeRecoveryReceipt), AuthorityMaintenanceError> {
        if prior.expected_worker_process_id.is_some() {
            return Err(AuthorityMaintenanceError(
                "authority_worker_pipe_recovery_phase_invalid",
            ));
        }
        let prior_receipt = prior.prepared.clone();
        drop(prior);
        Self::rebuild_persisted_before_service(capsule, launch, records, &prior_receipt)
    }

    pub(super) fn rebuild_persisted_before_service(
        capsule: &MaintenanceWorkerCapsule,
        launch: &MaintenanceWorkerLaunchContract,
        records: &[MaintenanceWorkerJournalRecord],
        prior_receipt: &WorkerPipePreparedReceipt,
    ) -> Result<(Self, WorkerPipeRecoveryReceipt), AuthorityMaintenanceError> {
        prior_receipt.validate(capsule, launch)?;
        let service_absence_readback_sha256 = worker_service_absence_readback(launch)?;
        let replacement = Self::create(capsule, launch)?;
        let recovery = WorkerPipeRecoveryReceipt::from_observed(
            capsule,
            launch,
            records,
            prior_receipt,
            replacement.prepared_receipt(),
            service_absence_readback_sha256,
        )?;
        Ok((replacement, recovery))
    }

    pub(super) fn accept_exact_worker(
        &mut self,
        lease: &NativeWorkerServiceLease,
    ) -> Result<(), AuthorityMaintenanceError> {
        connect_overlapped(self.raw(), lease.process_raw())?;
        let mut process_id = 0u32;
        let mut session_id = u32::MAX;
        if unsafe { GetNamedPipeClientProcessId(self.raw(), &mut process_id) } == 0
            || unsafe { GetNamedPipeClientSessionId(self.raw(), &mut session_id) } == 0
            || process_id != lease.process_id()
            || session_id != 0
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_peer_mismatch",
            ));
        }
        require_restricted_worker_service_sid(lease.process_raw())?;
        self.expected_worker_process_id = Some(process_id);
        Ok(())
    }

    pub(super) fn send_handoff(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        handoff: &WorkerHandleHandoffReceipt,
        lease: &NativeWorkerServiceLease,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.expected_worker_process_id != Some(lease.process_id())
            || handoff.worker().process_id() != lease.process_id()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_peer_mismatch",
            ));
        }
        write_framed_overlapped(
            self.raw(),
            lease.process_raw(),
            &handoff.canonical_bytes(capsule)?,
        )
    }

    pub(super) fn receive_durable_staging(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
        lease: &NativeWorkerServiceLease,
    ) -> Result<DurableSourceStagingReceipt, AuthorityMaintenanceError> {
        if self.expected_worker_process_id != Some(lease.process_id()) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_peer_mismatch",
            ));
        }
        let bytes = read_framed_overlapped(self.raw(), lease.process_raw())?;
        DurableSourceStagingReceipt::parse_canonical(&bytes, capsule, worker_started, handoff)
    }

    pub(super) fn receive_durable_staging_frame(
        &self,
        lease: &NativeWorkerServiceLease,
    ) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        if self.expected_worker_process_id != Some(lease.process_id()) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_peer_mismatch",
            ));
        }
        read_framed_overlapped(self.raw(), lease.process_raw())
    }

    pub(super) fn acknowledge_durable_staging(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        staging: &DurableSourceStagingReceipt,
        lease: &NativeWorkerServiceLease,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.expected_worker_process_id != Some(lease.process_id()) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_peer_mismatch",
            ));
        }
        write_framed_overlapped(
            self.raw(),
            lease.process_raw(),
            &durable_staging_ack(capsule, staging)?,
        )
    }

    pub(super) fn receive_exit_ready(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        terminal: &MaintenanceWorkerJournalRecord,
        cleanup: &WorkerStagingCleanupReceipt,
        worker_started: &WorkerStartedReceipt,
        lease: &NativeWorkerServiceLease,
    ) -> Result<WorkerExitReadyReceipt, AuthorityMaintenanceError> {
        let bytes = self.receive_exit_ready_frame(lease)?;
        let receipt = WorkerExitReadyReceipt::parse_sealed_canonical(&bytes)?;
        receipt.validate(capsule, terminal, cleanup, worker_started)?;
        if receipt.worker() != &lease.process_binding {
            return Err(AuthorityMaintenanceError(
                "authority_worker_process_identity_mismatch",
            ));
        }
        Ok(receipt)
    }

    pub(super) fn receive_exit_ready_frame(
        &self,
        lease: &NativeWorkerServiceLease,
    ) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        if self.expected_worker_process_id != Some(lease.process_id()) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_peer_mismatch",
            ));
        }
        read_framed_overlapped(self.raw(), lease.process_raw())
    }

    fn raw(&self) -> windows_sys::Win32::Foundation::HANDLE {
        self.pipe.as_raw_handle().cast()
    }
}

pub(super) struct NativeWorkerHandoffClient {
    pipe: OwnedHandle,
    helper_process: OwnedHandle,
    helper_binding: WorkerProcessBinding,
    duplicated_handles: OneShotDuplicatedHandleValues,
}

impl NativeWorkerHandoffClient {
    pub(super) fn connect(
        capsule: &MaintenanceWorkerCapsule,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let pipe_name = wide_null(&worker_handoff_pipe_name(capsule)?);
        if unsafe {
            WaitNamedPipeW(
                pipe_name.as_ptr(),
                WORKER_HANDOFF_TIMEOUT.as_millis() as u32,
            )
        } == 0
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_pipe_timeout",
            ));
        }
        let handle = unsafe {
            CreateFileW(
                pipe_name.as_ptr(),
                GENERIC_READ | GENERIC_WRITE,
                0,
                ptr::null(),
                OPEN_EXISTING,
                FILE_FLAG_OVERLAPPED,
                ptr::null_mut(),
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_pipe_open_failed",
            ));
        }
        let pipe = unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) };
        let mut read_mode = PIPE_READMODE_MESSAGE;
        if unsafe {
            SetNamedPipeHandleState(
                pipe.as_raw_handle().cast(),
                &mut read_mode,
                ptr::null(),
                ptr::null(),
            )
        } == 0
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_pipe_mode_failed",
            ));
        }
        let mut helper_process_id = 0u32;
        if unsafe {
            GetNamedPipeServerProcessId(pipe.as_raw_handle().cast(), &mut helper_process_id)
        } == 0
            || helper_process_id == 0
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_server_identity_unavailable",
            ));
        }
        let helper_process = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
                0,
                helper_process_id,
            )
        };
        if helper_process.is_null() || helper_process == INVALID_HANDLE_VALUE {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_server_identity_unavailable",
            ));
        }
        let helper_process = unsafe { OwnedHandle::from_raw_handle(helper_process as RawHandle) };
        let helper_binding = process_binding_from_handle(
            helper_process.as_raw_handle().cast(),
            helper_process_id,
            &capsule.install_helper_sha256()?,
            None,
        )?;
        Ok(Self {
            pipe,
            helper_process,
            helper_binding,
            duplicated_handles: OneShotDuplicatedHandleValues::default(),
        })
    }

    pub(super) fn receive_handoff(
        &mut self,
        capsule: &MaintenanceWorkerCapsule,
        current_worker: &WorkerProcessBinding,
    ) -> Result<WorkerHandleHandoffReceipt, AuthorityMaintenanceError> {
        let bytes = read_framed_overlapped(self.raw(), self.helper_process_raw())?;
        let handoff = WorkerHandleHandoffReceipt::parse_canonical(&bytes, capsule)?;
        if handoff.helper().process_id() != self.helper_binding.process_id()
            || handoff.helper().process_creation_time()
                != self.helper_binding.process_creation_time()
            || handoff.helper().image_sha256()? != self.helper_binding.image_sha256()?
            || handoff.worker().process_id() != current_worker.process_id()
            || handoff.worker().process_creation_time() != current_worker.process_creation_time()
            || handoff.worker().image_sha256()? != current_worker.image_sha256()?
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_peer_mismatch",
            ));
        }
        self.duplicated_handles
            .arm(handoff.duplicated_target_handle_values())?;
        Ok(handoff)
    }

    pub(super) fn send_durable_staging(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
        staging: &DurableSourceStagingReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        write_framed_overlapped(
            self.raw(),
            self.helper_process_raw(),
            &staging.canonical_bytes(capsule, worker_started, handoff)?,
        )
    }

    pub(super) fn receive_durable_staging_ack(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        staging: &DurableSourceStagingReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        let bytes = read_framed_overlapped(self.raw(), self.helper_process_raw())?;
        if bytes.as_slice() != durable_staging_ack(capsule, staging)? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_staging_ack_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn adopt_duplicated_source_handles(
        &mut self,
        handoff: &WorkerHandleHandoffReceipt,
    ) -> Result<[OwnedHandle; PROTECTED_GENERATION_PAYLOAD_COUNT], AuthorityMaintenanceError> {
        let values = self.duplicated_handles.take()?;
        if values != handoff.duplicated_target_handle_values() {
            return Err(AuthorityMaintenanceError(
                "authority_worker_duplicated_handle_value_invalid",
            ));
        }
        let raw = values.map(|value| value as usize as RawHandle);
        if values
            .iter()
            .zip(raw.iter())
            .any(|(value, raw)| *value == 0 || *value != *raw as usize as u64)
            || raw
                .iter()
                .enumerate()
                .any(|(index, value)| raw[..index].contains(value))
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_duplicated_handle_value_invalid",
            ));
        }
        let [service, controller, install_helper, lifecycle_driver, bridge_launcher, runtime_source_manifest] =
            raw;
        Ok(unsafe {
            [
                OwnedHandle::from_raw_handle(service),
                OwnedHandle::from_raw_handle(controller),
                OwnedHandle::from_raw_handle(install_helper),
                OwnedHandle::from_raw_handle(lifecycle_driver),
                OwnedHandle::from_raw_handle(bridge_launcher),
                OwnedHandle::from_raw_handle(runtime_source_manifest),
            ]
        })
    }

    pub(super) fn send_exit_ready(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        terminal: &MaintenanceWorkerJournalRecord,
        cleanup: &WorkerStagingCleanupReceipt,
        worker_started: &WorkerStartedReceipt,
        receipt: &WorkerExitReadyReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        receipt.validate(capsule, terminal, cleanup, worker_started)?;
        if receipt.worker().process_id() == self.helper_binding.process_id() {
            return Err(AuthorityMaintenanceError(
                "authority_worker_self_lifecycle_rejected",
            ));
        }
        write_framed_overlapped(
            self.raw(),
            self.helper_process_raw(),
            &receipt.sealed_canonical_bytes()?,
        )
    }

    fn raw(&self) -> windows_sys::Win32::Foundation::HANDLE {
        self.pipe.as_raw_handle().cast()
    }

    fn helper_process_raw(&self) -> windows_sys::Win32::Foundation::HANDLE {
        self.helper_process.as_raw_handle().cast()
    }
}

fn durable_staging_ack(
    capsule: &MaintenanceWorkerCapsule,
    staging: &DurableSourceStagingReceipt,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let mut digest = Sha256::new();
    digest.update(WORKER_STAGING_ACK_DOMAIN);
    digest.update(capsule.digest()?);
    digest.update(staging.digest()?);
    Ok(digest.finalize().into())
}

struct OverlappedEvent {
    event: OwnedHandle,
    overlapped: OVERLAPPED,
}

impl OverlappedEvent {
    fn new() -> Result<Self, AuthorityMaintenanceError> {
        let event = unsafe { CreateEventW(ptr::null(), 1, 0, ptr::null()) };
        if event.is_null() {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_event_failed",
            ));
        }
        let event = unsafe { OwnedHandle::from_raw_handle(event as RawHandle) };
        let mut overlapped = unsafe { std::mem::zeroed::<OVERLAPPED>() };
        overlapped.hEvent = event.as_raw_handle().cast();
        Ok(Self { event, overlapped })
    }
}

fn connect_overlapped(
    pipe: windows_sys::Win32::Foundation::HANDLE,
    peer_process: windows_sys::Win32::Foundation::HANDLE,
) -> Result<(), AuthorityMaintenanceError> {
    let mut operation = OverlappedEvent::new()?;
    if unsafe { ConnectNamedPipe(pipe, &mut operation.overlapped) } != 0 {
        return Ok(());
    }
    match unsafe { GetLastError() } {
        ERROR_PIPE_CONNECTED => Ok(()),
        ERROR_IO_PENDING => {
            wait_overlapped(pipe, peer_process, &mut operation, "connect").map(|_| ())
        }
        _ => Err(AuthorityMaintenanceError(
            "authority_worker_handoff_pipe_connect_failed",
        )),
    }
}

fn write_framed_overlapped(
    pipe: windows_sys::Win32::Foundation::HANDLE,
    peer_process: windows_sys::Win32::Foundation::HANDLE,
    payload: &[u8],
) -> Result<(), AuthorityMaintenanceError> {
    if payload.is_empty() || payload.len() > WORKER_HANDOFF_PIPE_BYTES as usize {
        return Err(AuthorityMaintenanceError(
            "authority_worker_handoff_frame_invalid",
        ));
    }
    let mut frame = Vec::with_capacity(payload.len() + 4);
    frame.extend_from_slice(&(payload.len() as u32).to_be_bytes());
    frame.extend_from_slice(payload);
    let mut operation = OverlappedEvent::new()?;
    let mut written = 0u32;
    let started = unsafe {
        WriteFile(
            pipe,
            frame.as_ptr(),
            frame.len() as u32,
            &mut written,
            &mut operation.overlapped,
        )
    };
    if started == 0 {
        if unsafe { GetLastError() } != ERROR_IO_PENDING {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_write_failed",
            ));
        }
        written = wait_overlapped(pipe, peer_process, &mut operation, "write")?;
    }
    if written != frame.len() as u32 {
        return Err(AuthorityMaintenanceError(
            "authority_worker_handoff_write_incomplete",
        ));
    }
    Ok(())
}

fn read_framed_overlapped(
    pipe: windows_sys::Win32::Foundation::HANDLE,
    peer_process: windows_sys::Win32::Foundation::HANDLE,
) -> Result<Vec<u8>, AuthorityMaintenanceError> {
    let mut frame = vec![0u8; WORKER_HANDOFF_PIPE_BYTES as usize + 4];
    let mut operation = OverlappedEvent::new()?;
    let mut read = 0u32;
    let started = unsafe {
        ReadFile(
            pipe,
            frame.as_mut_ptr(),
            frame.len() as u32,
            &mut read,
            &mut operation.overlapped,
        )
    };
    if started == 0 {
        if unsafe { GetLastError() } != ERROR_IO_PENDING {
            return Err(AuthorityMaintenanceError(
                "authority_worker_handoff_read_failed",
            ));
        }
        read = wait_overlapped(pipe, peer_process, &mut operation, "read")?;
    }
    let read = read as usize;
    if read < 5 {
        return Err(AuthorityMaintenanceError(
            "authority_worker_handoff_frame_invalid",
        ));
    }
    let length = u32::from_be_bytes(frame[..4].try_into().unwrap_or_default()) as usize;
    if length == 0 || length > WORKER_HANDOFF_PIPE_BYTES as usize || read != length + 4 {
        return Err(AuthorityMaintenanceError(
            "authority_worker_handoff_frame_invalid",
        ));
    }
    frame.drain(..4);
    frame.truncate(length);
    Ok(frame)
}

fn wait_overlapped(
    handle: windows_sys::Win32::Foundation::HANDLE,
    peer_process: windows_sys::Win32::Foundation::HANDLE,
    operation: &mut OverlappedEvent,
    operation_name: &'static str,
) -> Result<u32, AuthorityMaintenanceError> {
    let handles = [operation.event.as_raw_handle().cast(), peer_process];
    let wait = unsafe {
        WaitForMultipleObjects(
            handles.len() as u32,
            handles.as_ptr(),
            0,
            WORKER_HANDOFF_TIMEOUT.as_millis() as u32,
        )
    };
    if wait != WAIT_OBJECT_0 {
        unsafe {
            CancelIoEx(handle, &operation.overlapped);
        }
        let mut cancelled_transfer = 0u32;
        unsafe {
            GetOverlappedResult(handle, &operation.overlapped, &mut cancelled_transfer, 1);
        }
        return Err(AuthorityMaintenanceError(if wait == WAIT_TIMEOUT {
            "authority_worker_handoff_timeout"
        } else if wait == WAIT_OBJECT_0 + 1 {
            "authority_worker_handoff_peer_exited"
        } else {
            "authority_worker_handoff_wait_failed"
        }));
    }
    let mut transferred = 0u32;
    if unsafe { GetOverlappedResult(handle, &operation.overlapped, &mut transferred, 0) } == 0 {
        return Err(AuthorityMaintenanceError(match operation_name {
            "connect" => "authority_worker_handoff_pipe_connect_failed",
            "write" => "authority_worker_handoff_write_failed",
            _ => "authority_worker_handoff_read_failed",
        }));
    }
    Ok(transferred)
}

struct AlignedBuffer {
    words: Vec<usize>,
    byte_len: usize,
}

impl AlignedBuffer {
    fn new(byte_len: u32) -> Result<Self, AuthorityMaintenanceError> {
        let byte_len = usize::try_from(byte_len)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_readback_too_large"))?;
        if byte_len == 0 || byte_len > 1024 * 1024 {
            return Err(AuthorityMaintenanceError(
                "authority_worker_readback_size_invalid",
            ));
        }
        let word_size = size_of::<usize>();
        let word_count = byte_len
            .checked_add(word_size - 1)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_readback_too_large",
            ))?
            / word_size;
        Ok(Self {
            words: vec![0; word_count],
            byte_len,
        })
    }

    fn as_mut_u8(&mut self) -> *mut u8 {
        self.words.as_mut_ptr().cast()
    }

    fn contains_wide_ptr(&self, value: *const u16) -> bool {
        let start = self.words.as_ptr() as usize;
        let end = start.saturating_add(self.byte_len);
        let pointer = value as usize;
        pointer >= start && pointer < end && pointer % std::mem::align_of::<u16>() == 0
    }
}

pub(super) fn inspect_worker_service_absent(
    contract: &MaintenanceWorkerLaunchContract,
) -> Result<bool, AuthorityMaintenanceError> {
    let manager =
        ServiceHandle(unsafe { OpenSCManagerW(ptr::null(), ptr::null(), SC_MANAGER_CONNECT) });
    if manager.0.is_null() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_scm_unavailable",
        ));
    }
    let service_name = wide_null(contract.service_name);
    let service = ServiceHandle(unsafe {
        OpenServiceW(
            manager.0,
            service_name.as_ptr(),
            SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS,
        )
    });
    if service.0.is_null() {
        return if unsafe { GetLastError() } == ERROR_SERVICE_DOES_NOT_EXIST {
            Ok(true)
        } else {
            Err(AuthorityMaintenanceError(
                "authority_worker_service_readback_denied",
            ))
        };
    }
    verify_exact_service(service.0, contract)?;
    Ok(false)
}

fn worker_service_absence_readback(
    contract: &MaintenanceWorkerLaunchContract,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    if !inspect_worker_service_absent(contract)? {
        return Err(AuthorityMaintenanceError(
            "authority_worker_pipe_recovery_service_present",
        ));
    }
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-worker-service-absence-v1\0");
    digest.update(contract.service_name.as_bytes());
    digest.update([0]);
    digest.update(contract.binary_command.as_bytes());
    Ok(digest.finalize().into())
}

pub(super) fn create_start_worker(
    capsule: &MaintenanceWorkerCapsule,
    contract: &MaintenanceWorkerLaunchContract,
    pipe_server: &NativeWorkerHandoffServer,
    persisted_pipe: &NativePersistedPipePrepared,
) -> Result<NativeWorkerServiceLease, AuthorityMaintenanceError> {
    pipe_server.prepared_receipt().validate(capsule, contract)?;
    persisted_pipe.receipt().validate(capsule, contract)?;
    if pipe_server.prepared_receipt().digest()? != persisted_pipe.receipt().digest()?
        || pipe_server.expected_worker_process_id.is_some()
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_pipe_not_prepared_before_service",
        ));
    }
    if !inspect_worker_service_absent(contract)? {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_residue",
        ));
    }
    let manager = ServiceHandle(unsafe {
        OpenSCManagerW(
            ptr::null(),
            ptr::null(),
            SC_MANAGER_CONNECT | SC_MANAGER_CREATE_SERVICE,
        )
    });
    if manager.0.is_null() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_scm_create_denied",
        ));
    }
    let service_name = wide_null(contract.service_name);
    let display_name = wide_null(contract.display_name);
    let command = wide_null(&contract.binary_command);
    let account = wide_null(contract.account);
    let service = ServiceHandle(unsafe {
        CreateServiceW(
            manager.0,
            service_name.as_ptr(),
            display_name.as_ptr(),
            WORKER_SERVICE_LIFECYCLE_ACCESS,
            SERVICE_WIN32_OWN_PROCESS,
            SERVICE_DEMAND_START,
            SERVICE_ERROR_NORMAL,
            command.as_ptr(),
            ptr::null(),
            ptr::null_mut(),
            ptr::null(),
            account.as_ptr(),
            ptr::null(),
        )
    });
    if service.0.is_null() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_create_failed",
        ));
    }
    let configured = configure_exact_service(service.0, contract)
        .and_then(|()| verify_exact_service(service.0, contract));
    if let Err(error) = configured {
        let _ = stop_wait_delete_service(service.0, None);
        return Err(error);
    }
    if unsafe { StartServiceW(service.0, 0, ptr::null()) } == 0 {
        let _ = stop_wait_delete_service(service.0, None);
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_start_failed",
        ));
    }
    let process_id = match wait_for_started_process(service.0) {
        Ok(value) => value,
        Err(error) => {
            let _ = stop_wait_delete_service(service.0, None);
            return Err(error);
        }
    };
    let process = unsafe {
        OpenProcess(
            PROCESS_DUP_HANDLE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
            0,
            process_id,
        )
    };
    if process.is_null() {
        let _ = stop_wait_delete_service(service.0, None);
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_handle_unavailable",
        ));
    }
    let process = unsafe { OwnedHandle::from_raw_handle(process as RawHandle) };
    let process_binding = process_binding_from_handle(
        process.as_raw_handle().cast(),
        process_id,
        &capsule.install_helper_sha256()?,
        Some(contract_executable_path(contract)?),
    )?;
    Ok(NativeWorkerServiceLease {
        manager,
        service,
        process,
        process_id,
        process_binding,
        delete_pending: false,
    })
}

pub(super) fn finish_worker_service_removal(
    lease: NativeWorkerServiceLease,
) -> Result<NativeWorkerServiceRemovalOutcome, AuthorityMaintenanceError> {
    let pending = mark_worker_service_delete_pending(lease)?;
    let closed = close_worker_finalizer_handles(pending)?;
    wait_worker_service_absent_after_handles_closed(&closed)
}

pub(super) fn mark_exit_ready_worker_service_delete_pending(
    capsule: &MaintenanceWorkerCapsule,
    terminal: &MaintenanceWorkerJournalRecord,
    cleanup: &WorkerStagingCleanupReceipt,
    worker_started: &WorkerStartedReceipt,
    exit_ready: &WorkerExitReadyReceipt,
    lease: NativeWorkerServiceLease,
) -> Result<NativeWorkerServiceDeletePendingLease, AuthorityMaintenanceError> {
    exit_ready.validate(capsule, terminal, cleanup, worker_started)?;
    if exit_ready.worker() != lease.process_binding() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_identity_mismatch",
        ));
    }
    mark_worker_service_delete_pending(lease)
}

fn mark_worker_service_delete_pending(
    mut lease: NativeWorkerServiceLease,
) -> Result<NativeWorkerServiceDeletePendingLease, AuthorityMaintenanceError> {
    reject_self_process_lifecycle_target(unsafe { GetCurrentProcessId() }, lease.process_id)?;
    let expected_binding = lease.process_binding.clone();
    let stop_was_required = run_stop_wait_delete(&mut WindowsServiceRemoval {
        service: lease.service.0,
        process: Some(&lease.process),
        expected_binding: Some(&expected_binding),
    })?;
    lease.delete_pending = true;
    let delete_pending_readback_sha256 = worker_service_delete_pending_readback(
        &expected_binding,
        stop_was_required,
        lease.delete_pending,
    )?;
    Ok(NativeWorkerServiceDeletePendingLease {
        lease,
        stop_was_required,
        delete_pending_readback_sha256,
    })
}

pub(super) fn close_worker_finalizer_handles(
    pending: NativeWorkerServiceDeletePendingLease,
) -> Result<NativeWorkerFinalizerHandlesClosed, AuthorityMaintenanceError> {
    if !pending.lease.delete_pending {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_delete_mark_failed",
        ));
    }
    let worker = pending.lease.process_binding.clone();
    let stop_was_required = pending.stop_was_required;
    let delete_pending_readback_sha256 = pending.delete_pending_readback_sha256;
    drop(pending.lease);
    let handles_closed_readback_sha256 =
        worker_finalizer_handles_closed_readback(&worker, delete_pending_readback_sha256)?;
    Ok(NativeWorkerFinalizerHandlesClosed {
        worker,
        stop_was_required,
        delete_pending_readback_sha256,
        handles_closed_readback_sha256,
    })
}

pub(super) fn wait_worker_service_absent_after_handles_closed(
    closed: &NativeWorkerFinalizerHandlesClosed,
) -> Result<NativeWorkerServiceRemovalOutcome, AuthorityMaintenanceError> {
    wait_worker_service_absent()?;
    let contract_digest = {
        let mut digest = Sha256::new();
        digest.update(b"vrcforge-authority-worker-service-absence-contract-v1\0");
        digest.update(super::worker::MAINTENANCE_WORKER_SERVICE_NAME.as_bytes());
        digest.update(closed.worker.process_id().to_be_bytes());
        digest.update(closed.worker.process_creation_time().to_be_bytes());
        digest.update(closed.worker.image_sha256()?);
        digest.update(closed.handles_closed_readback_sha256);
        digest.finalize().into()
    };
    Ok(NativeWorkerServiceRemovalOutcome {
        stop_was_required: closed.stop_was_required,
        delete_pending_readback_sha256: closed.delete_pending_readback_sha256,
        handles_closed_readback_sha256: closed.handles_closed_readback_sha256,
        service_absence_readback_sha256: contract_digest,
    })
}

fn stop_wait_delete_service(
    service: SC_HANDLE,
    process: Option<&OwnedHandle>,
) -> Result<(), AuthorityMaintenanceError> {
    if let Some(process) = process {
        let process_id = unsafe { GetProcessId(process.as_raw_handle().cast()) };
        reject_self_process_lifecycle_target(unsafe { GetCurrentProcessId() }, process_id)?;
    }
    run_stop_wait_delete(&mut WindowsServiceRemoval {
        service,
        process,
        expected_binding: None,
    })
    .map(|_| ())
}

fn stop_request_race_is_safe(observed_state: u32) -> bool {
    matches!(observed_state, SERVICE_STOP_PENDING | SERVICE_STOPPED)
}

trait ServiceRemovalOperations {
    fn actor_process_id(&mut self) -> u32;
    fn worker_process_id(&mut self) -> Option<u32>;
    fn validate_worker_identity(&mut self) -> Result<(), AuthorityMaintenanceError>;
    fn wait_natural_exit(&mut self) -> Result<bool, AuthorityMaintenanceError>;
    fn current_state(&mut self) -> Result<u32, AuthorityMaintenanceError>;
    fn request_stop(&mut self) -> Result<bool, AuthorityMaintenanceError>;
    fn wait_stopped(&mut self) -> Result<(), AuthorityMaintenanceError>;
    fn wait_process(&mut self) -> Result<(), AuthorityMaintenanceError>;
    fn mark_delete(&mut self) -> Result<(), AuthorityMaintenanceError>;
}

fn run_stop_wait_delete<B: ServiceRemovalOperations>(
    backend: &mut B,
) -> Result<bool, AuthorityMaintenanceError> {
    if let Some(worker_process_id) = backend.worker_process_id() {
        reject_self_process_lifecycle_target(backend.actor_process_id(), worker_process_id)?;
    }
    backend.validate_worker_identity()?;
    let natural_exit = backend.wait_natural_exit()?;
    let mut stop_was_required = false;
    if !natural_exit && backend.current_state()? != SERVICE_STOPPED {
        if !backend.request_stop()? && !stop_request_race_is_safe(backend.current_state()?) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_service_stop_failed",
            ));
        }
        stop_was_required = true;
    }
    backend.wait_stopped()?;
    backend.wait_process()?;
    backend.mark_delete()?;
    Ok(stop_was_required)
}

struct WindowsServiceRemoval<'a> {
    service: SC_HANDLE,
    process: Option<&'a OwnedHandle>,
    expected_binding: Option<&'a WorkerProcessBinding>,
}

impl ServiceRemovalOperations for WindowsServiceRemoval<'_> {
    fn actor_process_id(&mut self) -> u32 {
        unsafe { GetCurrentProcessId() }
    }

    fn worker_process_id(&mut self) -> Option<u32> {
        self.process
            .map(|process| unsafe { GetProcessId(process.as_raw_handle().cast()) })
            .filter(|process_id| *process_id != 0)
    }

    fn validate_worker_identity(&mut self) -> Result<(), AuthorityMaintenanceError> {
        let (Some(process), Some(expected)) = (self.process, self.expected_binding) else {
            return Ok(());
        };
        let observed = process_binding_from_handle(
            process.as_raw_handle().cast(),
            expected.process_id(),
            &expected.image_sha256()?,
            None,
        )?;
        if observed != *expected {
            return Err(AuthorityMaintenanceError(
                "authority_worker_process_identity_mismatch",
            ));
        }
        Ok(())
    }

    fn wait_natural_exit(&mut self) -> Result<bool, AuthorityMaintenanceError> {
        let Some(process) = self.process else {
            return Ok(false);
        };
        let grace = natural_exit_grace_for_state(query_service_state(self.service)?);
        let wait = unsafe {
            WaitForSingleObject(process.as_raw_handle().cast(), grace.as_millis() as u32)
        };
        match wait {
            WAIT_OBJECT_0 => Ok(true),
            WAIT_TIMEOUT => Ok(false),
            _ => Err(AuthorityMaintenanceError(
                "authority_worker_process_natural_exit_wait_failed",
            )),
        }
    }

    fn current_state(&mut self) -> Result<u32, AuthorityMaintenanceError> {
        query_service_state(self.service)
    }

    fn request_stop(&mut self) -> Result<bool, AuthorityMaintenanceError> {
        let mut status = unsafe { std::mem::zeroed::<SERVICE_STATUS>() };
        Ok(unsafe { ControlService(self.service, SERVICE_CONTROL_STOP, &mut status) } != 0)
    }

    fn wait_stopped(&mut self) -> Result<(), AuthorityMaintenanceError> {
        wait_for_stopped(self.service)
    }

    fn wait_process(&mut self) -> Result<(), AuthorityMaintenanceError> {
        let Some(process) = self.process else {
            return Ok(());
        };
        let wait = unsafe {
            WaitForSingleObject(
                process.as_raw_handle().cast(),
                WORKER_ABSENCE_TIMEOUT.as_millis() as u32,
            )
        };
        if wait != WAIT_OBJECT_0 {
            return Err(AuthorityMaintenanceError(
                "authority_worker_process_stop_timeout",
            ));
        }
        Ok(())
    }

    fn mark_delete(&mut self) -> Result<(), AuthorityMaintenanceError> {
        if unsafe { DeleteService(self.service) } == 0 {
            return Err(AuthorityMaintenanceError(
                "authority_worker_service_delete_mark_failed",
            ));
        }
        Ok(())
    }
}

fn natural_exit_grace_for_state(current_state: u32) -> Duration {
    if current_state == SERVICE_START_PENDING {
        WORKER_START_PENDING_EXIT_GRACE
    } else {
        WORKER_NATURAL_EXIT_GRACE
    }
}

fn reject_self_process_lifecycle_target(
    actor_process_id: u32,
    worker_process_id: u32,
) -> Result<(), AuthorityMaintenanceError> {
    if actor_process_id == 0 || worker_process_id == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_worker_process_identity_invalid",
        ));
    }
    if actor_process_id == worker_process_id {
        return Err(AuthorityMaintenanceError(
            "authority_worker_self_lifecycle_rejected",
        ));
    }
    Ok(())
}

fn worker_service_delete_pending_readback(
    worker: &WorkerProcessBinding,
    stop_was_required: bool,
    delete_pending: bool,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    if !delete_pending {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_delete_mark_failed",
        ));
    }
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-worker-service-delete-pending-readback-v1\0");
    digest.update(super::worker::MAINTENANCE_WORKER_SERVICE_NAME.as_bytes());
    digest.update(worker.process_id().to_be_bytes());
    digest.update(worker.process_creation_time().to_be_bytes());
    digest.update(worker.image_sha256()?);
    digest.update([u8::from(stop_was_required), 1]);
    Ok(digest.finalize().into())
}

fn worker_finalizer_handles_closed_readback(
    worker: &WorkerProcessBinding,
    delete_pending_readback_sha256: [u8; 32],
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    if delete_pending_readback_sha256
        .iter()
        .all(|value| *value == 0)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_finalizer_handles_closed_invalid",
        ));
    }
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-worker-finalizer-handles-closed-readback-v1\0");
    digest.update(worker.process_id().to_be_bytes());
    digest.update(worker.process_creation_time().to_be_bytes());
    digest.update(worker.image_sha256()?);
    digest.update(delete_pending_readback_sha256);
    digest.update([1u8; 3]);
    Ok(digest.finalize().into())
}

fn wait_worker_service_absent() -> Result<(), AuthorityMaintenanceError> {
    let service_name = wide_null(super::worker::MAINTENANCE_WORKER_SERVICE_NAME);
    let manager =
        ServiceHandle(unsafe { OpenSCManagerW(ptr::null(), ptr::null(), SC_MANAGER_CONNECT) });
    if manager.0.is_null() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_scm_unavailable",
        ));
    }
    let deadline = Instant::now() + WORKER_ABSENCE_TIMEOUT;
    loop {
        let service = ServiceHandle(unsafe {
            OpenServiceW(manager.0, service_name.as_ptr(), SERVICE_QUERY_STATUS)
        });
        if service.0.is_null() && unsafe { GetLastError() } == ERROR_SERVICE_DOES_NOT_EXIST {
            return Ok(());
        }
        drop(service);
        if Instant::now() >= deadline {
            return Err(AuthorityMaintenanceError(
                "authority_worker_service_residue",
            ));
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn wait_for_stopped(service: SC_HANDLE) -> Result<(), AuthorityMaintenanceError> {
    let deadline = Instant::now() + WORKER_ABSENCE_TIMEOUT;
    loop {
        if query_service_state(service)? == SERVICE_STOPPED {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(AuthorityMaintenanceError(
                "authority_worker_service_stop_timeout",
            ));
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn query_service_state(service: SC_HANDLE) -> Result<u32, AuthorityMaintenanceError> {
    let mut status = MaybeUninit::<SERVICE_STATUS_PROCESS>::zeroed();
    let mut required = 0u32;
    if unsafe {
        QueryServiceStatusEx(
            service,
            SC_STATUS_PROCESS_INFO,
            status.as_mut_ptr().cast(),
            size_of::<SERVICE_STATUS_PROCESS>() as u32,
            &mut required,
        )
    } == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_status_failed",
        ));
    }
    Ok(unsafe { status.assume_init() }.dwCurrentState)
}

fn configure_exact_service(
    service: SC_HANDLE,
    contract: &MaintenanceWorkerLaunchContract,
) -> Result<(), AuthorityMaintenanceError> {
    let mut sid = SERVICE_SID_INFO {
        dwServiceSidType: SERVICE_SID_TYPE_RESTRICTED,
    };
    if unsafe {
        ChangeServiceConfig2W(
            service,
            SERVICE_CONFIG_SERVICE_SID_INFO,
            (&mut sid as *mut SERVICE_SID_INFO).cast(),
        )
    } == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_sid_config_failed",
        ));
    }
    let mut privilege_words = Vec::new();
    for privilege in WORKER_REQUIRED_PRIVILEGES {
        privilege_words.extend(privilege.encode_utf16());
        privilege_words.push(0);
    }
    privilege_words.push(0);
    let mut privileges = SERVICE_REQUIRED_PRIVILEGES_INFOW {
        pmszRequiredPrivileges: privilege_words.as_mut_ptr(),
    };
    if unsafe {
        ChangeServiceConfig2W(
            service,
            SERVICE_CONFIG_REQUIRED_PRIVILEGES_INFO,
            (&mut privileges as *mut SERVICE_REQUIRED_PRIVILEGES_INFOW).cast(),
        )
    } == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_privilege_config_failed",
        ));
    }
    let descriptor = SecurityDescriptor::from_sddl(contract.service_sddl)?;
    if unsafe { SetServiceObjectSecurity(service, SECURITY_INFORMATION, descriptor.0) } == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_security_config_failed",
        ));
    }
    Ok(())
}

fn verify_exact_service(
    service: SC_HANDLE,
    contract: &MaintenanceWorkerLaunchContract,
) -> Result<(), AuthorityMaintenanceError> {
    let (config, buffer) = query_primary_config(service)?;
    let command = wide_string_in_buffer(config.lpBinaryPathName, &buffer)?;
    let account = wide_string_in_buffer(config.lpServiceStartName, &buffer)?;
    if command != contract.binary_command
        || !account.eq_ignore_ascii_case(contract.account)
        || config.dwServiceType != SERVICE_WIN32_OWN_PROCESS
        || config.dwStartType != SERVICE_DEMAND_START
        || config.dwErrorControl != SERVICE_ERROR_NORMAL
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_configuration_mismatch",
        ));
    }
    let sid = query_fixed_config::<SERVICE_SID_INFO>(service, SERVICE_CONFIG_SERVICE_SID_INFO)?;
    if sid.dwServiceSidType != SERVICE_SID_TYPE_RESTRICTED {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_sid_mismatch",
        ));
    }
    let privileges = query_required_privileges(service)?;
    let mut expected = WORKER_REQUIRED_PRIVILEGES.map(str::to_string).to_vec();
    expected.sort();
    if privileges != expected {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_privilege_mismatch",
        ));
    }
    let actual_sddl = query_service_sddl(service)?;
    if actual_sddl != contract.service_sddl {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_security_mismatch",
        ));
    }
    Ok(())
}

fn wait_for_started_process(service: SC_HANDLE) -> Result<u32, AuthorityMaintenanceError> {
    let deadline = Instant::now() + WORKER_START_TIMEOUT;
    loop {
        let status = query_worker_service_status(service)?;
        match started_process_id_from_status(status.dwCurrentState, status.dwProcessId)? {
            Some(process_id) => return Ok(process_id),
            None => {}
        }
        if Instant::now() >= deadline {
            return Err(AuthorityMaintenanceError(
                "authority_worker_service_start_timeout",
            ));
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn wait_for_running(service: SC_HANDLE) -> Result<u32, AuthorityMaintenanceError> {
    let deadline = Instant::now() + WORKER_START_TIMEOUT;
    loop {
        match query_running_worker_process_id(service) {
            Ok(process_id) => return Ok(process_id),
            Err(error) if error.code() == "authority_worker_service_not_running" => {}
            Err(error) => return Err(error),
        }
        if Instant::now() >= deadline {
            return Err(AuthorityMaintenanceError(
                "authority_worker_service_readiness_timeout",
            ));
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn query_running_worker_process_id(service: SC_HANDLE) -> Result<u32, AuthorityMaintenanceError> {
    let status = query_worker_service_status(service)?;
    if status.dwCurrentState != SERVICE_RUNNING || status.dwProcessId == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_not_running",
        ));
    }
    Ok(status.dwProcessId)
}

fn query_worker_service_status(
    service: SC_HANDLE,
) -> Result<SERVICE_STATUS_PROCESS, AuthorityMaintenanceError> {
    let mut status = MaybeUninit::<SERVICE_STATUS_PROCESS>::zeroed();
    let mut required = 0u32;
    if unsafe {
        QueryServiceStatusEx(
            service,
            SC_STATUS_PROCESS_INFO,
            status.as_mut_ptr().cast(),
            size_of::<SERVICE_STATUS_PROCESS>() as u32,
            &mut required,
        )
    } == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_status_failed",
        ));
    }
    Ok(unsafe { status.assume_init() })
}

fn started_process_id_from_status(
    current_state: u32,
    process_id: u32,
) -> Result<Option<u32>, AuthorityMaintenanceError> {
    if matches!(current_state, SERVICE_START_PENDING | SERVICE_RUNNING) {
        return if process_id == 0 {
            Ok(None)
        } else {
            Ok(Some(process_id))
        };
    }
    if current_state == SERVICE_STOPPED || current_state == SERVICE_STOP_PENDING {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_exited_before_ready",
        ));
    }
    Ok(None)
}

fn query_primary_config(
    service: SC_HANDLE,
) -> Result<(QUERY_SERVICE_CONFIGW, AlignedBuffer), AuthorityMaintenanceError> {
    let mut required = 0u32;
    unsafe {
        QueryServiceConfigW(service, ptr::null_mut(), 0, &mut required);
    }
    if required == 0 || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_readback_failed",
        ));
    }
    let mut buffer = AlignedBuffer::new(required)?;
    if unsafe { QueryServiceConfigW(service, buffer.as_mut_u8().cast(), required, &mut required) }
        == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_readback_failed",
        ));
    }
    let value = unsafe { *(buffer.words.as_ptr().cast::<QUERY_SERVICE_CONFIGW>()) };
    Ok((value, buffer))
}

fn query_fixed_config<T: Copy>(
    service: SC_HANDLE,
    level: u32,
) -> Result<T, AuthorityMaintenanceError> {
    let mut value = MaybeUninit::<T>::zeroed();
    let mut required = 0u32;
    if unsafe {
        QueryServiceConfig2W(
            service,
            level,
            value.as_mut_ptr().cast(),
            size_of::<T>() as u32,
            &mut required,
        )
    } == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_readback_failed",
        ));
    }
    Ok(unsafe { value.assume_init() })
}

fn query_required_privileges(service: SC_HANDLE) -> Result<Vec<String>, AuthorityMaintenanceError> {
    let mut required = 0u32;
    unsafe {
        QueryServiceConfig2W(
            service,
            SERVICE_CONFIG_REQUIRED_PRIVILEGES_INFO,
            ptr::null_mut(),
            0,
            &mut required,
        );
    }
    if required == 0 || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_readback_failed",
        ));
    }
    let mut buffer = AlignedBuffer::new(required)?;
    if unsafe {
        QueryServiceConfig2W(
            service,
            SERVICE_CONFIG_REQUIRED_PRIVILEGES_INFO,
            buffer.as_mut_u8(),
            required,
            &mut required,
        )
    } == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_readback_failed",
        ));
    }
    let info = unsafe {
        *(buffer
            .words
            .as_ptr()
            .cast::<SERVICE_REQUIRED_PRIVILEGES_INFOW>())
    };
    let mut values = wide_multi_string_in_buffer(info.pmszRequiredPrivileges, &buffer)?;
    values.sort();
    Ok(values)
}

fn query_service_sddl(service: SC_HANDLE) -> Result<String, AuthorityMaintenanceError> {
    let mut required = 0u32;
    unsafe {
        QueryServiceObjectSecurity(
            service,
            SECURITY_INFORMATION,
            ptr::null_mut(),
            0,
            &mut required,
        );
    }
    if required == 0 || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_security_readback_failed",
        ));
    }
    let mut descriptor = vec![0u8; required as usize];
    if unsafe {
        QueryServiceObjectSecurity(
            service,
            SECURITY_INFORMATION,
            descriptor.as_mut_ptr().cast(),
            required,
            &mut required,
        )
    } == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_security_readback_failed",
        ));
    }
    let mut text = ptr::null_mut::<u16>();
    let mut text_length = 0u32;
    if unsafe {
        ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor.as_ptr().cast_mut().cast(),
            SDDL_REVISION_1,
            SECURITY_INFORMATION,
            &mut text,
            &mut text_length,
        )
    } == 0
        || text.is_null()
        || text_length == 0
    {
        if !text.is_null() {
            unsafe {
                LocalFree(text.cast());
            }
        }
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_security_readback_failed",
        ));
    }
    let mut words = unsafe { std::slice::from_raw_parts(text, text_length as usize) }.to_vec();
    if words.last() == Some(&0) {
        words.pop();
    }
    let result = if words.contains(&0) {
        Err(AuthorityMaintenanceError(
            "authority_worker_service_security_readback_failed",
        ))
    } else {
        String::from_utf16(&words).map_err(|_| {
            AuthorityMaintenanceError("authority_worker_service_security_readback_failed")
        })
    };
    unsafe {
        LocalFree(text.cast());
    }
    result
}

fn wide_string_in_buffer(
    pointer: *const u16,
    buffer: &AlignedBuffer,
) -> Result<String, AuthorityMaintenanceError> {
    if pointer.is_null() || !buffer.contains_wide_ptr(pointer) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_string_invalid",
        ));
    }
    let maximum = ((buffer.words.as_ptr() as usize + buffer.byte_len) - pointer as usize) / 2;
    let mut length = 0usize;
    while length < maximum && unsafe { *pointer.add(length) } != 0 {
        length += 1;
    }
    if length == maximum {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_string_invalid",
        ));
    }
    String::from_utf16(unsafe { std::slice::from_raw_parts(pointer, length) })
        .map_err(|_| AuthorityMaintenanceError("authority_worker_service_string_invalid"))
}

fn wide_multi_string_in_buffer(
    pointer: *const u16,
    buffer: &AlignedBuffer,
) -> Result<Vec<String>, AuthorityMaintenanceError> {
    if pointer.is_null() || !buffer.contains_wide_ptr(pointer) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_service_privileges_invalid",
        ));
    }
    let maximum = ((buffer.words.as_ptr() as usize + buffer.byte_len) - pointer as usize) / 2;
    let mut values = Vec::new();
    let mut offset = 0usize;
    loop {
        if offset >= maximum {
            return Err(AuthorityMaintenanceError(
                "authority_worker_service_privileges_invalid",
            ));
        }
        let start = offset;
        while offset < maximum && unsafe { *pointer.add(offset) } != 0 {
            offset += 1;
        }
        if offset == maximum {
            return Err(AuthorityMaintenanceError(
                "authority_worker_service_privileges_invalid",
            ));
        }
        if offset == start {
            break;
        }
        values.push(
            String::from_utf16(unsafe {
                std::slice::from_raw_parts(pointer.add(start), offset - start)
            })
            .map_err(|_| {
                AuthorityMaintenanceError("authority_worker_service_privileges_invalid")
            })?,
        );
        offset += 1;
    }
    Ok(values)
}

fn require_restricted_worker_service_sid(
    process: windows_sys::Win32::Foundation::HANDLE,
) -> Result<(), AuthorityMaintenanceError> {
    let mut token = ptr::null_mut();
    if unsafe { OpenProcessToken(process, TOKEN_QUERY, &mut token) } == 0 || token.is_null() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_restricted_identity_unavailable",
        ));
    }
    let token = unsafe { OwnedHandle::from_raw_handle(token as RawHandle) };

    let mut required_bytes = 0u32;
    let initial = unsafe {
        GetTokenInformation(
            token.as_raw_handle().cast(),
            TokenRestrictedSids,
            ptr::null_mut(),
            0,
            &mut required_bytes,
        )
    };
    if initial != 0
        || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER
        || required_bytes == 0
        || required_bytes as usize > WORKER_HANDOFF_PIPE_BYTES as usize
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_restricted_identity_unavailable",
        ));
    }

    let allocation_bytes = (required_bytes as usize).max(size_of::<TOKEN_GROUPS>());
    let words = allocation_bytes.div_ceil(size_of::<usize>());
    let mut aligned = vec![0usize; words];
    let mut returned_bytes = 0u32;
    if unsafe {
        GetTokenInformation(
            token.as_raw_handle().cast(),
            TokenRestrictedSids,
            aligned.as_mut_ptr().cast(),
            (aligned.len() * size_of::<usize>()) as u32,
            &mut returned_bytes,
        )
    } == 0
        || returned_bytes == 0
        || returned_bytes > required_bytes
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_restricted_identity_unavailable",
        ));
    }

    let groups = unsafe { &*(aligned.as_ptr().cast::<TOKEN_GROUPS>()) };
    let count = groups.GroupCount as usize;
    if count == 0 || count > MAX_WORKER_RESTRICTED_SIDS {
        return Err(AuthorityMaintenanceError(
            "authority_worker_restricted_service_sid_mismatch",
        ));
    }
    let minimum_bytes = size_of::<TOKEN_GROUPS>()
        .checked_add(
            count
                .saturating_sub(1)
                .checked_mul(size_of::<SID_AND_ATTRIBUTES>())
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_restricted_identity_unavailable",
                ))?,
        )
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_restricted_identity_unavailable",
        ))?;
    if minimum_bytes > returned_bytes as usize {
        return Err(AuthorityMaintenanceError(
            "authority_worker_restricted_identity_unavailable",
        ));
    }

    let mut expected_sid: PSID = ptr::null_mut();
    let expected_sid_text = wide_null(MAINTENANCE_SERVICE_SID);
    if unsafe { ConvertStringSidToSidW(expected_sid_text.as_ptr(), &mut expected_sid) } == 0
        || expected_sid.is_null()
        || unsafe { IsValidSid(expected_sid) } == 0
    {
        if !expected_sid.is_null() {
            unsafe {
                LocalFree(expected_sid);
            }
        }
        return Err(AuthorityMaintenanceError(
            "authority_worker_restricted_identity_unavailable",
        ));
    }
    struct LocalSid(PSID);
    impl Drop for LocalSid {
        fn drop(&mut self) {
            if !self.0.is_null() {
                unsafe {
                    LocalFree(self.0);
                }
            }
        }
    }
    let expected_sid = LocalSid(expected_sid);

    let restricted = unsafe { std::slice::from_raw_parts(groups.Groups.as_ptr(), count) };
    let mut matches = 0usize;
    for entry in restricted {
        if entry.Sid.is_null() || unsafe { IsValidSid(entry.Sid) } == 0 {
            return Err(AuthorityMaintenanceError(
                "authority_worker_restricted_identity_unavailable",
            ));
        }
        if unsafe { EqualSid(entry.Sid, expected_sid.0) } != 0 {
            matches += 1;
        }
    }
    if !restricted_service_sid_match_is_exact(matches) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_restricted_service_sid_mismatch",
        ));
    }
    Ok(())
}

fn restricted_service_sid_match_is_exact(matches: usize) -> bool {
    matches == 1
}

fn wide_null(value: &str) -> Vec<u16> {
    OsString::from(value)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    enum RemovalEvent {
        ValidateWorkerIdentity,
        WaitNaturalExit,
        CurrentState,
        RequestStop,
        WaitStopped,
        WaitProcess,
        MarkDelete,
    }

    struct MockRemoval {
        actor_process_id: u32,
        worker_process_id: Option<u32>,
        natural_exit: bool,
        states: VecDeque<u32>,
        request_accepted: bool,
        fail_at: Option<RemovalEvent>,
        events: Vec<RemovalEvent>,
    }

    impl MockRemoval {
        fn running() -> Self {
            Self {
                actor_process_id: 100,
                worker_process_id: Some(200),
                natural_exit: false,
                states: VecDeque::from([SERVICE_RUNNING]),
                request_accepted: true,
                fail_at: None,
                events: Vec::new(),
            }
        }

        fn record(&mut self, event: RemovalEvent) -> Result<(), AuthorityMaintenanceError> {
            self.events.push(event);
            if self.fail_at == Some(event) {
                Err(AuthorityMaintenanceError(
                    "authority_worker_test_injected_removal_failure",
                ))
            } else {
                Ok(())
            }
        }
    }

    impl ServiceRemovalOperations for MockRemoval {
        fn actor_process_id(&mut self) -> u32 {
            self.actor_process_id
        }

        fn worker_process_id(&mut self) -> Option<u32> {
            self.worker_process_id
        }

        fn validate_worker_identity(&mut self) -> Result<(), AuthorityMaintenanceError> {
            self.record(RemovalEvent::ValidateWorkerIdentity)
        }

        fn wait_natural_exit(&mut self) -> Result<bool, AuthorityMaintenanceError> {
            self.record(RemovalEvent::WaitNaturalExit)?;
            Ok(self.natural_exit)
        }

        fn current_state(&mut self) -> Result<u32, AuthorityMaintenanceError> {
            self.record(RemovalEvent::CurrentState)?;
            self.states.pop_front().ok_or(AuthorityMaintenanceError(
                "authority_worker_test_state_missing",
            ))
        }

        fn request_stop(&mut self) -> Result<bool, AuthorityMaintenanceError> {
            self.record(RemovalEvent::RequestStop)?;
            Ok(self.request_accepted)
        }

        fn wait_stopped(&mut self) -> Result<(), AuthorityMaintenanceError> {
            self.record(RemovalEvent::WaitStopped)
        }

        fn wait_process(&mut self) -> Result<(), AuthorityMaintenanceError> {
            self.record(RemovalEvent::WaitProcess)
        }

        fn mark_delete(&mut self) -> Result<(), AuthorityMaintenanceError> {
            self.record(RemovalEvent::MarkDelete)
        }
    }

    #[test]
    fn worker_service_privileges_are_exactly_scoped_to_sacl_readback() {
        assert_eq!(WORKER_REQUIRED_PRIVILEGES, ["SeSecurityPrivilege"]);
        assert_eq!(
            WORKER_SERVICE_LIFECYCLE_ACCESS,
            SERVICE_CHANGE_CONFIG
                | SERVICE_QUERY_CONFIG
                | SERVICE_QUERY_STATUS
                | SERVICE_START
                | SERVICE_STOP
                | DELETE
                | READ_CONTROL
                | WRITE_DAC
        );
    }

    #[test]
    fn worker_pipe_and_peer_require_the_exact_restricted_service_sid() {
        let expected_ace = format!("(A;;GA;;;{MAINTENANCE_SERVICE_SID})");
        assert!(WORKER_HANDOFF_PIPE_SDDL.contains(&expected_ace));
        assert_eq!(WORKER_HANDOFF_PIPE_SDDL.matches(&expected_ace).count(), 1);
        let descriptor = SecurityDescriptor::from_sddl(WORKER_HANDOFF_PIPE_SDDL).unwrap();
        assert!(!descriptor.0.is_null());
        assert!(!restricted_service_sid_match_is_exact(0));
        assert!(restricted_service_sid_match_is_exact(1));
        assert!(!restricted_service_sid_match_is_exact(2));
    }

    #[test]
    fn failed_stop_request_is_safe_only_after_stopped_or_stop_pending_readback() {
        assert!(stop_request_race_is_safe(SERVICE_STOPPED));
        assert!(stop_request_race_is_safe(SERVICE_STOP_PENDING));
        assert!(!stop_request_race_is_safe(SERVICE_RUNNING));
        assert!(!stop_request_race_is_safe(0));
    }

    #[test]
    fn worker_start_waits_for_a_start_pending_pid_without_waiting_for_running() {
        assert_eq!(
            started_process_id_from_status(SERVICE_START_PENDING, 4242).unwrap(),
            Some(4242)
        );
        assert_eq!(
            started_process_id_from_status(SERVICE_RUNNING, 4242).unwrap(),
            Some(4242)
        );
        assert_eq!(
            started_process_id_from_status(SERVICE_START_PENDING, 0).unwrap(),
            None
        );
        assert_eq!(
            started_process_id_from_status(SERVICE_STOP_PENDING, 4242)
                .unwrap_err()
                .code(),
            "authority_worker_service_exited_before_ready"
        );
        assert_eq!(
            started_process_id_from_status(SERVICE_STOPPED, 0)
                .unwrap_err()
                .code(),
            "authority_worker_service_exited_before_ready"
        );
        assert_eq!(
            natural_exit_grace_for_state(SERVICE_START_PENDING),
            WORKER_START_PENDING_EXIT_GRACE
        );
        assert!(WORKER_START_PENDING_EXIT_GRACE > WORKER_HANDOFF_TIMEOUT);
        assert_eq!(
            natural_exit_grace_for_state(SERVICE_RUNNING),
            WORKER_NATURAL_EXIT_GRACE
        );
    }

    #[test]
    fn service_removal_orders_stop_wait_process_and_delete() {
        let mut running = MockRemoval::running();
        assert!(run_stop_wait_delete(&mut running).unwrap());
        assert_eq!(
            running.events,
            vec![
                RemovalEvent::ValidateWorkerIdentity,
                RemovalEvent::WaitNaturalExit,
                RemovalEvent::CurrentState,
                RemovalEvent::RequestStop,
                RemovalEvent::WaitStopped,
                RemovalEvent::WaitProcess,
                RemovalEvent::MarkDelete,
            ]
        );

        let mut already_stopped = MockRemoval {
            actor_process_id: 100,
            worker_process_id: Some(200),
            natural_exit: true,
            states: VecDeque::from([SERVICE_STOPPED]),
            request_accepted: false,
            fail_at: None,
            events: Vec::new(),
        };
        assert!(!run_stop_wait_delete(&mut already_stopped).unwrap());
        assert_eq!(
            already_stopped.events,
            vec![
                RemovalEvent::ValidateWorkerIdentity,
                RemovalEvent::WaitNaturalExit,
                RemovalEvent::WaitStopped,
                RemovalEvent::WaitProcess,
                RemovalEvent::MarkDelete,
            ]
        );
    }

    #[test]
    fn service_removal_stops_at_each_injected_failure() {
        for failed in [
            RemovalEvent::ValidateWorkerIdentity,
            RemovalEvent::WaitNaturalExit,
            RemovalEvent::CurrentState,
            RemovalEvent::RequestStop,
            RemovalEvent::WaitStopped,
            RemovalEvent::WaitProcess,
            RemovalEvent::MarkDelete,
        ] {
            let mut backend = MockRemoval {
                fail_at: Some(failed),
                ..MockRemoval::running()
            };
            assert_eq!(
                run_stop_wait_delete(&mut backend).unwrap_err().code(),
                "authority_worker_test_injected_removal_failure"
            );
            assert_eq!(backend.events.last(), Some(&failed));
        }
    }

    #[test]
    fn service_stop_race_requires_fresh_stopping_or_stopped_state() {
        let mut stopping = MockRemoval {
            actor_process_id: 100,
            worker_process_id: Some(200),
            natural_exit: false,
            states: VecDeque::from([SERVICE_RUNNING, SERVICE_STOP_PENDING]),
            request_accepted: false,
            fail_at: None,
            events: Vec::new(),
        };
        run_stop_wait_delete(&mut stopping).unwrap();
        assert_eq!(stopping.events[2], RemovalEvent::CurrentState);

        let mut still_running = MockRemoval {
            actor_process_id: 100,
            worker_process_id: Some(200),
            natural_exit: false,
            states: VecDeque::from([SERVICE_RUNNING, SERVICE_RUNNING]),
            request_accepted: false,
            fail_at: None,
            events: Vec::new(),
        };
        assert_eq!(
            run_stop_wait_delete(&mut still_running).unwrap_err().code(),
            "authority_worker_service_stop_failed"
        );
        assert_eq!(
            still_running.events,
            vec![
                RemovalEvent::ValidateWorkerIdentity,
                RemovalEvent::WaitNaturalExit,
                RemovalEvent::CurrentState,
                RemovalEvent::RequestStop,
                RemovalEvent::CurrentState,
            ]
        );
    }

    #[test]
    fn service_removal_rejects_self_pid_before_identity_wait_stop_or_delete() {
        let mut backend = MockRemoval {
            actor_process_id: 200,
            worker_process_id: Some(200),
            ..MockRemoval::running()
        };
        assert_eq!(
            run_stop_wait_delete(&mut backend).unwrap_err().code(),
            "authority_worker_self_lifecycle_rejected"
        );
        assert!(backend.events.is_empty());
    }

    #[test]
    fn natural_worker_exit_never_sends_stop() {
        let mut backend = MockRemoval {
            natural_exit: true,
            states: VecDeque::new(),
            ..MockRemoval::running()
        };
        assert!(!run_stop_wait_delete(&mut backend).unwrap());
        assert_eq!(
            backend.events,
            vec![
                RemovalEvent::ValidateWorkerIdentity,
                RemovalEvent::WaitNaturalExit,
                RemovalEvent::WaitStopped,
                RemovalEvent::WaitProcess,
                RemovalEvent::MarkDelete,
            ]
        );
    }
}
