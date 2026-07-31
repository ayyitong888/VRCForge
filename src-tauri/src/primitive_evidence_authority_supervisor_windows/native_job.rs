use super::{Digest, NativeJobReceipt, ProcessKey, SupervisorError, SupervisorPolicy};
use crate::{
    primitive_evidence_authority_install::authority_service_sid,
    primitive_evidence_authority_runtime::AuthorityRuntimeIdentity,
    primitive_evidence_authority_supervisor::policy_source::VerifiedJobSecurityBinding,
    primitive_evidence_child_protocol::{
        windows_child_handshake::job_observation_digest_from_parts, ChildBootstrapRole,
        RoleRawHandleListDigest, CHILD_STANDARD_HANDLE_SLOT_COUNT,
    },
};
use hmac::{Hmac, Mac};
use sha2::{Digest as Sha2Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::c_void,
    fmt,
    marker::PhantomData,
    mem::{size_of, zeroed},
    os::windows::io::{AsRawHandle, BorrowedHandle, RawHandle},
    ptr::{null_mut, write_volatile},
    sync::atomic::{compiler_fence, Ordering},
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::{
        CloseHandle, CompareObjectHandles, DuplicateHandle, GetHandleInformation, GetLastError,
        LocalFree, SetLastError, DUPLICATE_SAME_ACCESS, ERROR_ALREADY_EXISTS,
        ERROR_INSUFFICIENT_BUFFER, ERROR_SUCCESS, FILETIME, GENERIC_ALL, GENERIC_EXECUTE,
        GENERIC_READ, GENERIC_WRITE, HANDLE, HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE,
        STILL_ACTIVE, WAIT_FAILED, WAIT_OBJECT_0, WAIT_TIMEOUT,
    },
    Security::{
        Authorization::{
            ConvertStringSecurityDescriptorToSecurityDescriptorW, ConvertStringSidToSidW,
            GetSecurityInfo, SDDL_REVISION_1, SE_KERNEL_OBJECT,
        },
        EqualSid, GetAce, GetLengthSid, GetSecurityDescriptorControl, GetSecurityDescriptorDacl,
        GetSecurityDescriptorOwner, IsValidSid, ACCESS_ALLOWED_ACE, ACL, ACL_REVISION,
        DACL_SECURITY_INFORMATION, OWNER_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR, PSID,
        SECURITY_ATTRIBUTES, SE_DACL_PROTECTED,
    },
    System::{
        JobObjects::{
            CreateJobObjectW, IsProcessInJob, JobObjectAssociateCompletionPortInformation,
            JobObjectBasicAccountingInformation, JobObjectBasicProcessIdList,
            JobObjectExtendedLimitInformation, QueryInformationJobObject, SetInformationJobObject,
            TerminateJobObject, JOBOBJECT_ASSOCIATE_COMPLETION_PORT,
            JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, JOBOBJECT_BASIC_PROCESS_ID_LIST,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
            JOB_OBJECT_LIMIT_BREAKAWAY_OK, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK,
        },
        SystemServices::{
            ACCESS_ALLOWED_ACE_TYPE, JOB_OBJECT_ASSIGN_PROCESS,
            JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS, JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO,
            JOB_OBJECT_MSG_EXIT_PROCESS, JOB_OBJECT_MSG_NEW_PROCESS, JOB_OBJECT_QUERY,
            JOB_OBJECT_SET_ATTRIBUTES, JOB_OBJECT_TERMINATE,
        },
        Threading::{
            DeleteProcThreadAttributeList, GetCurrentProcess, GetExitCodeProcess, GetProcessId,
            GetProcessIdOfThread, GetProcessTimes, GetThreadId, InitializeProcThreadAttributeList,
            TerminateProcess, UpdateProcThreadAttribute, WaitForSingleObject,
            CREATE_BREAKAWAY_FROM_JOB, CREATE_NO_WINDOW, CREATE_SUSPENDED,
            CREATE_UNICODE_ENVIRONMENT, EXTENDED_STARTUPINFO_PRESENT, LPPROC_THREAD_ATTRIBUTE_LIST,
            PROC_THREAD_ATTRIBUTE_HANDLE_LIST, PROC_THREAD_ATTRIBUTE_JOB_LIST,
        },
        IO::{CreateIoCompletionPort, GetQueuedCompletionStatus, OVERLAPPED},
    },
};

#[cfg(test)]
use windows_sys::Win32::{
    Security::{
        Authorization::ConvertSidToStringSidW, GetTokenInformation, TokenUser, TOKEN_QUERY,
        TOKEN_USER,
    },
    System::{
        Threading::{
            CreateEventW, CreateProcessW, GetCurrentProcessId, GetCurrentThread,
            GetCurrentThreadId, OpenProcessToken, PROCESS_INFORMATION, STARTUPINFOEXW,
            STARTUPINFOW,
        },
        IO::PostQueuedCompletionStatus,
    },
};

const JOB_NAME_PREFIX: &str = "Local\\VRCForge.PrimitiveEvidence.Job.";
const JOB_COMPLETION_CONCURRENCY: u32 = 1;
const JOB_LAUNCH_ATTRIBUTE_COUNT: u32 = 2;
const JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS: u32 = 5_000;
const JOB_FAULT_CONTAINMENT_TIMEOUT_MILLIS: u32 = 5_000;
const FIXED_ROOT_PROCESS_COUNT: usize = 2;
const MIN_TERMINAL_ROOT_PROCESS_COUNT: usize = 1;
const JOB_TERMINATION_EXIT_CODE: u32 = 1;
const MAX_JOB_ROSTER_PROCESSES: usize = 64;
const MAX_JOB_COMPLETION_EVENTS: usize = 512;
const MAX_JOB_TERMINAL_POLL_MILLIS: u32 = 250;
const JOB_SECURITY_BINDING_DOMAIN: &[u8] = b"vrcforge-authority-native-job-security-v1\0";
const JOB_LAUNCH_ATTRIBUTE_BINDING_DOMAIN: &[u8] =
    b"vrcforge-authority-native-job-launch-attributes-v1\0";
const JOB_PRE_RESUME_MEMBERSHIP_DOMAIN: &[u8] =
    b"vrcforge-authority-native-job-pre-resume-membership-v1\0";
const JOB_CHILD_MEMBERSHIP_EPOCH_SOURCE_DOMAIN: &[u8] =
    b"vrcforge-authority-native-child-membership-epoch-source-v1\0";
const JOB_ACTIVE_ROSTER_DOMAIN: &[u8] = b"vrcforge-authority-native-job-active-roster-v1\0";
const JOB_ROOT_PROCESS_IDS_DOMAIN: &[u8] = b"vrcforge-authority-native-job-root-process-ids-v1\0";
const JOB_ROOT_PROCESS_EPOCH_DOMAIN: &[u8] =
    b"vrcforge-authority-native-job-root-process-epoch-v1\0";
const JOB_ROOT_PROCESS_EPOCHS_DOMAIN: &[u8] =
    b"vrcforge-authority-native-job-root-process-epochs-v1\0";
const JOB_ROOT_PROCESS_TERMINAL_READBACK_DOMAIN: &[u8] =
    b"vrcforge-authority-native-job-root-process-terminal-readback-v1\0";
const JOB_TERMINAL_DRAIN_DOMAIN: &[u8] = b"vrcforge-authority-native-job-terminal-drain-v1\0";
const JOB_TERMINAL_TRANSCRIPT_DOMAIN: &[u8] =
    b"vrcforge-authority-native-job-terminal-transcript-v1\0";
const JOB_TERMINAL_DRAIN_POLL_ATTEMPTS: usize = 20;
const LOCAL_SYSTEM_SID: &str = "S-1-5-18";
pub(super) const JOB_OBJECT_ALL_ACCESS_EXACT: u32 = 0x001f_001f;
pub(super) const SERVICE_JOB_ACCESS_EXACT: u32 = JOB_OBJECT_ASSIGN_PROCESS
    | JOB_OBJECT_SET_ATTRIBUTES
    | JOB_OBJECT_QUERY
    | JOB_OBJECT_TERMINATE
    | 0x0002_0000
    | 0x0010_0000;
const GENERIC_ACCESS_MASK: u32 = GENERIC_ALL | GENERIC_EXECUTE | GENERIC_READ | GENERIC_WRITE;
const MUTATING_CONTROL_ACCESS_MASK: u32 = 0x0001_0000 | 0x0004_0000 | 0x0008_0000;
const MAX_SID_BYTES: usize = 68;
pub(super) const FIXED_CHILD_CREATION_FLAGS: u32 =
    CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT;

type HmacSha256 = Hmac<Sha256>;

#[derive(Clone, PartialEq, Eq)]
pub(super) struct NativeJobSecuritySpec {
    authority_generation_digest: Digest,
    authority_identity_digest: Digest,
    owner_sid: String,
    service_sid: String,
    binding_digest: Digest,
    test_owner_override: bool,
}

impl fmt::Debug for NativeJobSecuritySpec {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NativeJobSecuritySpec")
            .field("descriptor", &"<bound-and-redacted>")
            .finish()
    }
}

impl NativeJobSecuritySpec {
    pub(super) fn from_runtime_identity(
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<Self, SupervisorError> {
        Self::from_parts(
            *identity.authority_generation_digest(),
            identity.binding_digest(),
            LOCAL_SYSTEM_SID.to_owned(),
            false,
        )
    }

    fn from_parts(
        authority_generation_digest: Digest,
        authority_identity_digest: Digest,
        owner_sid: String,
        test_owner_override: bool,
    ) -> Result<Self, SupervisorError> {
        let service_sid = authority_service_sid().to_owned();
        let mut spec = Self {
            authority_generation_digest,
            authority_identity_digest,
            owner_sid,
            service_sid,
            binding_digest: [0; 32],
            test_owner_override,
        };
        spec.binding_digest = spec.derive_binding_digest()?;
        spec.validate()?;
        Ok(spec)
    }

    #[cfg(test)]
    fn for_test_current_owner(
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<Self, SupervisorError> {
        Self::from_parts(
            *identity.authority_generation_digest(),
            identity.binding_digest(),
            current_process_user_sid_string()?,
            true,
        )
    }

    fn validate(&self) -> Result<(), SupervisorError> {
        if self
            .authority_generation_digest
            .iter()
            .all(|byte| *byte == 0)
            || self.authority_identity_digest.iter().all(|byte| *byte == 0)
            || self.service_sid != authority_service_sid()
            || (!self.test_owner_override && self.owner_sid != LOCAL_SYSTEM_SID)
            || self.binding_digest.iter().all(|byte| *byte == 0)
            || self.derive_binding_digest()? != self.binding_digest
        {
            return Err(SupervisorError::new(
                "authority_native_job_security_spec_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn binding_digest(&self) -> &Digest {
        &self.binding_digest
    }

    fn sddl(&self) -> String {
        format!(
            "O:{}D:P(A;;0x{JOB_OBJECT_ALL_ACCESS_EXACT:08x};;;SY)(A;;0x{SERVICE_JOB_ACCESS_EXACT:08x};;;{})",
            self.owner_sid, self.service_sid
        )
    }

    fn derive_binding_digest(&self) -> Result<Digest, SupervisorError> {
        let owner = OwnedSid::from_text(&self.owner_sid)?;
        let system = OwnedSid::from_text(LOCAL_SYSTEM_SID)?;
        let service = OwnedSid::from_text(&self.service_sid)?;
        security_binding_digest(
            &self.authority_generation_digest,
            &self.authority_identity_digest,
            owner.raw(),
            system.raw(),
            service.raw(),
            ACL_REVISION as u8,
            SE_DACL_PROTECTED,
            JOB_OBJECT_ALL_ACCESS_EXACT,
            SERVICE_JOB_ACCESS_EXACT,
        )
    }
}

/// Constructs the policy token only from the locally derived native security
/// specification. No caller can supply either digest independently.
pub(super) fn verified_policy_binding(
    identity: &AuthorityRuntimeIdentity,
) -> Result<VerifiedJobSecurityBinding, SupervisorError> {
    let spec = NativeJobSecuritySpec::from_runtime_identity(identity)?;
    spec.validate()?;
    VerifiedJobSecurityBinding::from_validated_native_spec(
        identity.binding_digest(),
        *spec.binding_digest(),
    )
}

struct OwnedSecurityDescriptor(PSECURITY_DESCRIPTOR);

impl OwnedSecurityDescriptor {
    fn from_sddl(value: &str) -> Result<Self, SupervisorError> {
        let encoded = value
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let mut descriptor = null_mut();
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                encoded.as_ptr(),
                SDDL_REVISION_1,
                &mut descriptor,
                null_mut(),
            )
        } == 0
            || descriptor.is_null()
        {
            if !descriptor.is_null() {
                unsafe {
                    LocalFree(descriptor);
                }
            }
            return Err(SupervisorError::new(
                "authority_native_job_security_descriptor_invalid",
            ));
        }
        Ok(Self(descriptor))
    }

    fn from_spec(spec: &NativeJobSecuritySpec) -> Result<Self, SupervisorError> {
        spec.validate()?;
        Self::from_sddl(&spec.sddl())
    }

    fn raw(&self) -> PSECURITY_DESCRIPTOR {
        self.0
    }
}

impl Drop for OwnedSecurityDescriptor {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                LocalFree(self.0);
            }
            self.0 = null_mut();
        }
    }
}

struct OwnedSid(PSID);

impl OwnedSid {
    fn from_text(value: &str) -> Result<Self, SupervisorError> {
        if value.is_empty() || value.contains('\0') {
            return Err(SupervisorError::new(
                "authority_native_job_security_sid_invalid",
            ));
        }
        let encoded = value
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let mut sid = null_mut();
        if unsafe { ConvertStringSidToSidW(encoded.as_ptr(), &mut sid) } == 0
            || sid.is_null()
            || unsafe { IsValidSid(sid) } == 0
        {
            if !sid.is_null() {
                unsafe {
                    LocalFree(sid);
                }
            }
            return Err(SupervisorError::new(
                "authority_native_job_security_sid_invalid",
            ));
        }
        let length = unsafe { GetLengthSid(sid) } as usize;
        if length == 0 || length > MAX_SID_BYTES {
            unsafe {
                LocalFree(sid);
            }
            return Err(SupervisorError::new(
                "authority_native_job_security_sid_invalid",
            ));
        }
        Ok(Self(sid))
    }

    fn raw(&self) -> PSID {
        self.0
    }
}

impl Drop for OwnedSid {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                LocalFree(self.0);
            }
            self.0 = null_mut();
        }
    }
}

fn security_binding_digest(
    authority_generation_digest: &Digest,
    authority_identity_digest: &Digest,
    owner_sid: PSID,
    system_sid: PSID,
    service_sid: PSID,
    acl_revision: u8,
    control: u16,
    system_mask: u32,
    service_mask: u32,
) -> Result<Digest, SupervisorError> {
    let mut hasher = Sha256::new();
    hasher.update(JOB_SECURITY_BINDING_DOMAIN);
    hasher.update(authority_generation_digest);
    hasher.update(authority_identity_digest);
    hasher.update([acl_revision]);
    hasher.update(control.to_be_bytes());
    hash_sid(&mut hasher, owner_sid)?;
    hasher.update(2u32.to_be_bytes());
    hasher.update(system_mask.to_be_bytes());
    hash_sid(&mut hasher, system_sid)?;
    hasher.update(service_mask.to_be_bytes());
    hash_sid(&mut hasher, service_sid)?;
    Ok(hasher.finalize().into())
}

fn hash_sid(hasher: &mut Sha256, sid: PSID) -> Result<(), SupervisorError> {
    if sid.is_null() || unsafe { IsValidSid(sid) } == 0 {
        return Err(SupervisorError::new(
            "authority_native_job_security_sid_invalid",
        ));
    }
    let length = unsafe { GetLengthSid(sid) } as usize;
    if length == 0 || length > MAX_SID_BYTES {
        return Err(SupervisorError::new(
            "authority_native_job_security_sid_invalid",
        ));
    }
    hasher.update((length as u32).to_be_bytes());
    hasher.update(unsafe { std::slice::from_raw_parts(sid.cast::<u8>(), length) });
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct NativeJobSpec {
    object_id: u64,
    deterministic_name_digest: Digest,
    run_binding_digest: Digest,
    security_binding_digest: Digest,
    created_at: u64,
}

impl NativeJobSpec {
    fn from_policy(
        policy: &SupervisorPolicy,
        security: &NativeJobSecuritySpec,
        created_at: u64,
    ) -> Result<Self, SupervisorError> {
        security.validate()?;
        if security.authority_identity_digest != policy.authority_identity_digest {
            return Err(SupervisorError::new(
                "authority_native_job_security_identity_mismatch",
            ));
        }
        if security.binding_digest != policy.job_security_binding_digest {
            return Err(SupervisorError::new(
                "authority_native_job_security_policy_mismatch",
            ));
        }
        let spec = Self {
            object_id: policy.job_object_id,
            deterministic_name_digest: policy.deterministic_job_name_digest,
            run_binding_digest: policy.run_binding_digest,
            security_binding_digest: security.binding_digest,
            created_at,
        };
        spec.validate()?;
        if created_at < policy.issued_at || created_at >= policy.deadline {
            return Err(SupervisorError::new(
                "authority_native_job_create_time_invalid",
            ));
        }
        Ok(spec)
    }

    fn validate(&self) -> Result<(), SupervisorError> {
        if self.object_id == 0
            || self.created_at == 0
            || self.deterministic_name_digest.iter().all(|byte| *byte == 0)
            || self.run_binding_digest.iter().all(|byte| *byte == 0)
            || self.security_binding_digest.iter().all(|byte| *byte == 0)
        {
            return Err(SupervisorError::new("authority_native_job_spec_invalid"));
        }
        Ok(())
    }

    fn deterministic_name(&self) -> Vec<u16> {
        let mut name = String::with_capacity(JOB_NAME_PREFIX.len() + 64);
        name.push_str(JOB_NAME_PREFIX);
        for byte in self.deterministic_name_digest {
            const HEX: &[u8; 16] = b"0123456789abcdef";
            name.push(HEX[(byte >> 4) as usize] as char);
            name.push(HEX[(byte & 0x0f) as usize] as char);
        }
        name.encode_utf16().chain(std::iter::once(0)).collect()
    }

    fn completion_key(&self) -> Result<usize, SupervisorError> {
        let key = usize::try_from(self.object_id)
            .map_err(|_| SupervisorError::new("authority_native_job_object_id_unsupported"))?;
        if key == 0 {
            return Err(SupervisorError::new(
                "authority_native_job_object_id_unsupported",
            ));
        }
        Ok(key)
    }
}

#[derive(Debug)]
struct OwnedKernelHandle(HANDLE);

impl OwnedKernelHandle {
    fn new(raw: HANDLE, failure_code: &'static str) -> Result<Self, SupervisorError> {
        if raw.is_null() || raw == INVALID_HANDLE_VALUE {
            return Err(SupervisorError::new(failure_code));
        }
        Ok(Self(raw))
    }

    fn raw(&self) -> HANDLE {
        self.0
    }

    fn borrowed(&self) -> BorrowedHandle<'_> {
        unsafe { BorrowedHandle::borrow_raw(self.0 as RawHandle) }
    }

    fn is_inheritable(&self) -> Result<bool, SupervisorError> {
        let mut flags = 0u32;
        if unsafe { GetHandleInformation(self.0, &mut flags) } == 0 {
            return Err(SupervisorError::new(
                "authority_native_job_handle_query_failed",
            ));
        }
        Ok(flags & HANDLE_FLAG_INHERIT != 0)
    }
}

impl Drop for OwnedKernelHandle {
    fn drop(&mut self) {
        if !self.0.is_null() && self.0 != INVALID_HANDLE_VALUE {
            unsafe {
                CloseHandle(self.0);
            }
            self.0 = null_mut();
        }
    }
}

fn duplicate_non_inheritable_handle(
    source: HANDLE,
    failure_code: &'static str,
) -> Result<OwnedKernelHandle, SupervisorError> {
    if source.is_null() || source == INVALID_HANDLE_VALUE {
        return Err(SupervisorError::new(failure_code));
    }
    let current_process = unsafe { GetCurrentProcess() };
    let mut duplicate = null_mut();
    if unsafe {
        DuplicateHandle(
            current_process,
            source,
            current_process,
            &mut duplicate,
            0,
            0,
            DUPLICATE_SAME_ACCESS,
        )
    } == 0
    {
        return Err(SupervisorError::new(failure_code));
    }
    let duplicate = OwnedKernelHandle::new(duplicate, failure_code)?;
    if duplicate.is_inheritable()? {
        return Err(SupervisorError::new(
            "authority_native_job_process_handle_duplicate_invalid",
        ));
    }
    Ok(duplicate)
}

fn process_creation_time(handle: HANDLE) -> Result<u64, SupervisorError> {
    let mut creation: FILETIME = unsafe { zeroed() };
    let mut exit: FILETIME = unsafe { zeroed() };
    let mut kernel: FILETIME = unsafe { zeroed() };
    let mut user: FILETIME = unsafe { zeroed() };
    if unsafe { GetProcessTimes(handle, &mut creation, &mut exit, &mut kernel, &mut user) } == 0 {
        return Err(SupervisorError::new(
            "authority_native_job_process_epoch_readback_failed",
        ));
    }
    let value = (u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime);
    if value == 0 {
        return Err(SupervisorError::new(
            "authority_native_job_process_epoch_invalid",
        ));
    }
    Ok(value)
}

fn process_epoch_digest(process_id: u32, creation_time: u64) -> Result<Digest, SupervisorError> {
    if process_id == 0 || creation_time == 0 {
        return Err(SupervisorError::new(
            "authority_native_job_process_epoch_invalid",
        ));
    }
    let mut hasher = Sha256::new();
    hasher.update(JOB_ROOT_PROCESS_EPOCH_DOMAIN);
    hasher.update(process_id.to_be_bytes());
    hasher.update(creation_time.to_be_bytes());
    Ok(hasher.finalize().into())
}

#[derive(Debug)]
struct HeldRootProcess {
    handle: OwnedKernelHandle,
    process_id: u32,
    creation_time: u64,
    epoch_digest: Digest,
}

impl HeldRootProcess {
    fn duplicate_from_admission(
        job: &OwnedKernelHandle,
        source: HANDLE,
        expected_process_id: u32,
    ) -> Result<Self, SupervisorError> {
        if expected_process_id == 0
            || unsafe { GetProcessId(source) } != expected_process_id
            || handle_is_inheritable(source)?
            || unsafe { WaitForSingleObject(source, 0) } != WAIT_TIMEOUT
        {
            return Err(SupervisorError::new(
                "authority_native_job_process_handle_duplicate_invalid",
            ));
        }
        let source_creation_time = process_creation_time(source)?;
        let handle = duplicate_non_inheritable_handle(
            source,
            "authority_native_job_process_handle_duplicate_failed",
        )?;
        if unsafe { CompareObjectHandles(source, handle.raw()) } == 0
            || unsafe { GetProcessId(handle.raw()) } != expected_process_id
            || process_creation_time(handle.raw())? != source_creation_time
        {
            return Err(SupervisorError::new(
                "authority_native_job_process_handle_duplicate_invalid",
            ));
        }
        let mut in_job = 0;
        if unsafe { IsProcessInJob(handle.raw(), job.raw(), &mut in_job) } == 0 || in_job == 0 {
            return Err(SupervisorError::new(
                "authority_native_job_process_handle_duplicate_invalid",
            ));
        }
        if unsafe { WaitForSingleObject(handle.raw(), 0) } != WAIT_TIMEOUT {
            return Err(SupervisorError::new(
                "authority_native_job_process_handle_duplicate_invalid",
            ));
        }
        Ok(Self {
            handle,
            process_id: expected_process_id,
            creation_time: source_creation_time,
            epoch_digest: process_epoch_digest(expected_process_id, source_creation_time)?,
        })
    }

    fn validate(&self, job: &OwnedKernelHandle) -> Result<(), SupervisorError> {
        if self.process_id == 0
            || self.creation_time == 0
            || self.epoch_digest != process_epoch_digest(self.process_id, self.creation_time)?
            || self.handle.is_inheritable()?
            || unsafe { GetProcessId(self.handle.raw()) } != self.process_id
            || process_creation_time(self.handle.raw())? != self.creation_time
        {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_process_identity_invalid",
            ));
        }
        let mut in_job = 0;
        if unsafe { IsProcessInJob(self.handle.raw(), job.raw(), &mut in_job) } == 0 || in_job == 0
        {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_process_identity_invalid",
            ));
        }
        Ok(())
    }

    fn is_signaled(&self) -> Result<bool, SupervisorError> {
        match unsafe { WaitForSingleObject(self.handle.raw(), 0) } {
            WAIT_OBJECT_0 => Ok(true),
            WAIT_TIMEOUT => Ok(false),
            WAIT_FAILED => Err(SupervisorError::new(
                "authority_native_job_terminal_process_wait_failed",
            )),
            _ => Err(SupervisorError::new(
                "authority_native_job_terminal_process_wait_invalid",
            )),
        }
    }

    fn terminal_exit_code(&self) -> Result<u32, SupervisorError> {
        if !self.is_signaled()? {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_process_not_signaled",
            ));
        }
        let mut exit_code = STILL_ACTIVE as u32;
        if unsafe { GetExitCodeProcess(self.handle.raw(), &mut exit_code) } == 0 {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_process_exit_code_unavailable",
            ));
        }
        if exit_code == STILL_ACTIVE as u32 {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_process_exit_code_invalid",
            ));
        }
        Ok(exit_code)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct NativeJobSecurityReadback {
    binding_digest: Digest,
    owner_exact: bool,
    owner_local_system: bool,
    dacl_present: bool,
    dacl_defaulted: bool,
    dacl_protected: bool,
    ace_count: u16,
    system_access_mask: u32,
    service_access_mask: u32,
}

impl NativeJobSecurityReadback {
    fn validate(&self, spec: &NativeJobSecuritySpec) -> Result<(), SupervisorError> {
        if self.binding_digest != spec.binding_digest
            || !self.owner_exact
            || !self.dacl_present
            || self.dacl_defaulted
            || !self.dacl_protected
            || self.ace_count != 2
            || self.system_access_mask != JOB_OBJECT_ALL_ACCESS_EXACT
            || self.service_access_mask != SERVICE_JOB_ACCESS_EXACT
            || self.system_access_mask & GENERIC_ACCESS_MASK != 0
            || self.service_access_mask & GENERIC_ACCESS_MASK != 0
        {
            return Err(SupervisorError::new(
                "authority_native_job_security_readback_invalid",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct NativeJobReadback {
    limit_flags: u32,
    active_process_limit: u32,
    total_processes: u32,
    active_processes: u32,
    job_handle_inheritable: bool,
    completion_port_handle_inheritable: bool,
    security: NativeJobSecurityReadback,
}

impl NativeJobReadback {
    fn validate_empty_strict_job(
        &self,
        security: &NativeJobSecuritySpec,
    ) -> Result<(), SupervisorError> {
        let forbidden_flags = JOB_OBJECT_LIMIT_BREAKAWAY_OK
            | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
        if self.limit_flags != JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            || self.limit_flags & forbidden_flags != 0
            || self.active_process_limit != 0
            || self.total_processes != 0
            || self.active_processes != 0
            || self.job_handle_inheritable
            || self.completion_port_handle_inheritable
        {
            return Err(SupervisorError::new(
                "authority_native_job_readback_invalid",
            ));
        }
        self.security.validate(security)?;
        Ok(())
    }

    fn validate_strict_live_job(
        &self,
        security: &NativeJobSecuritySpec,
        active_processes: u32,
    ) -> Result<(), SupervisorError> {
        let forbidden_flags = JOB_OBJECT_LIMIT_BREAKAWAY_OK
            | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
        if active_processes == 0
            || self.limit_flags != JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            || self.limit_flags & forbidden_flags != 0
            || self.active_process_limit != 0
            || self.total_processes != active_processes
            || self.active_processes != active_processes
            || self.job_handle_inheritable
            || self.completion_port_handle_inheritable
        {
            return Err(SupervisorError::new(
                "authority_native_job_readback_invalid",
            ));
        }
        self.security.validate(security)
    }

    fn validate_strict_terminating_job(
        &self,
        security: &NativeJobSecuritySpec,
        admitted_root_processes: u32,
    ) -> Result<(), SupervisorError> {
        let forbidden_flags = JOB_OBJECT_LIMIT_BREAKAWAY_OK
            | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
        if admitted_root_processes < MIN_TERMINAL_ROOT_PROCESS_COUNT as u32
            || admitted_root_processes > FIXED_ROOT_PROCESS_COUNT as u32
            || self.limit_flags != JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            || self.limit_flags & forbidden_flags != 0
            || self.active_process_limit != 0
            || self.total_processes < admitted_root_processes
            || self.active_processes > self.total_processes
            || self.total_processes > (MAX_JOB_COMPLETION_EVENTS / 2) as u32
            || self.job_handle_inheritable
            || self.completion_port_handle_inheritable
        {
            return Err(SupervisorError::new(
                "authority_native_job_readback_invalid",
            ));
        }
        self.security.validate(security)
    }

    fn validate_strict_terminal_job(
        &self,
        security: &NativeJobSecuritySpec,
    ) -> Result<(), SupervisorError> {
        let forbidden_flags = JOB_OBJECT_LIMIT_BREAKAWAY_OK
            | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
        if self.limit_flags != JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            || self.limit_flags & forbidden_flags != 0
            || self.active_process_limit != 0
            || self.active_processes != 0
            || self.total_processes == 0
            || self.job_handle_inheritable
            || self.completion_port_handle_inheritable
        {
            return Err(SupervisorError::new(
                "authority_native_job_readback_invalid",
            ));
        }
        self.security.validate(security)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct NativeJobRunnerLaunchBinding {
    pub(super) object_id: u64,
    pub(super) deterministic_name_digest: Digest,
    pub(super) authority_generation_digest: Digest,
    pub(super) run_binding_digest: Digest,
    pub(super) security_binding_digest: Digest,
}

impl NativeJobRunnerLaunchBinding {
    fn new(
        object_id: u64,
        deterministic_name_digest: Digest,
        authority_generation_digest: Digest,
        run_binding_digest: Digest,
        security_binding_digest: Digest,
    ) -> Result<Self, SupervisorError> {
        let value = Self {
            object_id,
            deterministic_name_digest,
            authority_generation_digest,
            run_binding_digest,
            security_binding_digest,
        };
        if value.object_id == 0
            || [
                &value.deterministic_name_digest,
                &value.authority_generation_digest,
                &value.run_binding_digest,
                &value.security_binding_digest,
            ]
            .iter()
            .any(|digest| digest.iter().all(|byte| *byte == 0))
        {
            return Err(SupervisorError::new(
                "authority_native_job_runner_launch_binding_invalid",
            ));
        }
        Ok(value)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct NativeJobLaunchAttributeBinding {
    pub(super) object_id: u64,
    pub(super) deterministic_name_digest: Digest,
    pub(super) authority_generation_digest: Digest,
    pub(super) run_binding_digest: Digest,
    pub(super) security_binding_digest: Digest,
    pub(super) raw_handle_list: RoleRawHandleListDigest,
    pub(super) creation_flags: u32,
    pub(super) inherit_handles: bool,
    pub(super) job_list_count: u32,
    pub(super) handle_list_count: u32,
    pub(super) job_handle_non_inheritable: bool,
    pub(super) completion_port_handle_non_inheritable: bool,
    pub(super) job_assigned_at_creation: bool,
    pub(super) job_list_attribute_applied: bool,
    pub(super) handle_list_attribute_applied: bool,
    pub(super) breakaway_requested: bool,
    pub(super) initial_assignment_call_performed: bool,
    pub(super) binding_digest: Digest,
}

impl NativeJobLaunchAttributeBinding {
    fn new(
        job: &WindowsNativeJob,
        role: ChildBootstrapRole,
        inherited_handles: &[HANDLE],
    ) -> Result<Self, SupervisorError> {
        let mut value = Self {
            object_id: job.receipt.object_id,
            deterministic_name_digest: job.receipt.deterministic_name_digest,
            authority_generation_digest: job.security.authority_generation_digest,
            run_binding_digest: job.run_binding_digest,
            security_binding_digest: job.security.binding_digest,
            raw_handle_list: raw_handle_list(role, inherited_handles)?,
            creation_flags: FIXED_CHILD_CREATION_FLAGS,
            inherit_handles: true,
            job_list_count: 1,
            handle_list_count: u32::try_from(inherited_handles.len()).map_err(|_| {
                SupervisorError::new("authority_native_job_raw_handle_list_invalid")
            })?,
            job_handle_non_inheritable: !job.job.is_inheritable()?,
            completion_port_handle_non_inheritable: !job.completion_port.is_inheritable()?,
            job_assigned_at_creation: true,
            job_list_attribute_applied: true,
            handle_list_attribute_applied: true,
            breakaway_requested: false,
            initial_assignment_call_performed: false,
            binding_digest: [0; 32],
        };
        value.binding_digest = value.derive_binding_digest();
        value.validate(job, role)?;
        Ok(value)
    }

    fn validate(
        &self,
        job: &WindowsNativeJob,
        expected_role: ChildBootstrapRole,
    ) -> Result<(), SupervisorError> {
        if self.raw_handle_list.role() != expected_role
            || self.object_id != job.receipt.object_id
            || self.deterministic_name_digest != job.receipt.deterministic_name_digest
            || self.authority_generation_digest != job.security.authority_generation_digest
            || self.run_binding_digest != job.run_binding_digest
            || self.security_binding_digest != job.security.binding_digest
            || self
                .raw_handle_list
                .as_bytes()
                .iter()
                .all(|byte| *byte == 0)
            || self.creation_flags != FIXED_CHILD_CREATION_FLAGS
            || self.creation_flags & CREATE_BREAKAWAY_FROM_JOB != 0
            || !self.inherit_handles
            || self.job_list_count != 1
            || self.handle_list_count != CHILD_STANDARD_HANDLE_SLOT_COUNT as u32
            || !self.job_handle_non_inheritable
            || !self.completion_port_handle_non_inheritable
            || !self.job_assigned_at_creation
            || !self.job_list_attribute_applied
            || !self.handle_list_attribute_applied
            || self.breakaway_requested
            || self.initial_assignment_call_performed
            || self.binding_digest.iter().all(|byte| *byte == 0)
            || self.binding_digest != self.derive_binding_digest()
        {
            return Err(SupervisorError::new(
                "authority_native_job_launch_attribute_binding_invalid",
            ));
        }
        Ok(())
    }

    fn derive_binding_digest(&self) -> Digest {
        let mut hasher = Sha256::new();
        hasher.update(JOB_LAUNCH_ATTRIBUTE_BINDING_DOMAIN);
        hasher.update(self.object_id.to_be_bytes());
        hasher.update(self.deterministic_name_digest);
        hasher.update(self.authority_generation_digest);
        hasher.update(self.run_binding_digest);
        hasher.update(self.security_binding_digest);
        hasher.update(self.raw_handle_list.as_bytes());
        hasher.update(self.creation_flags.to_be_bytes());
        hasher.update([u8::from(self.inherit_handles)]);
        hasher.update(self.job_list_count.to_be_bytes());
        hasher.update(self.handle_list_count.to_be_bytes());
        for value in [
            self.job_handle_non_inheritable,
            self.completion_port_handle_non_inheritable,
            self.job_assigned_at_creation,
            self.job_list_attribute_applied,
            self.handle_list_attribute_applied,
            self.breakaway_requested,
            self.initial_assignment_call_performed,
        ] {
            hasher.update([u8::from(value)]);
        }
        hasher.finalize().into()
    }

    pub(super) fn runner_launch_binding(
        &self,
    ) -> Result<NativeJobRunnerLaunchBinding, SupervisorError> {
        if self.binding_digest.iter().all(|byte| *byte == 0)
            || self.binding_digest != self.derive_binding_digest()
        {
            return Err(SupervisorError::new(
                "authority_native_job_launch_attribute_binding_invalid",
            ));
        }
        NativeJobRunnerLaunchBinding::new(
            self.object_id,
            self.deterministic_name_digest,
            self.authority_generation_digest,
            self.run_binding_digest,
            self.security_binding_digest,
        )
    }
}

pub(super) struct NativeJobLaunchAttributeList<'a> {
    storage: Vec<usize>,
    job_handles: Box<[HANDLE; 1]>,
    inherited_handles: Box<[HANDLE]>,
    binding: NativeJobLaunchAttributeBinding,
    initialized: bool,
    _job_lifetime: PhantomData<&'a WindowsNativeJob>,
}

impl fmt::Debug for NativeJobLaunchAttributeList<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NativeJobLaunchAttributeList")
            .field("binding", &self.binding)
            .field("attributeStorage", &"<held-and-redacted>")
            .finish()
    }
}

impl NativeJobLaunchAttributeList<'_> {
    pub(super) fn binding(&self) -> &NativeJobLaunchAttributeBinding {
        &self.binding
    }

    pub(super) fn raw_attribute_list(&mut self) -> LPPROC_THREAD_ATTRIBUTE_LIST {
        self.storage.as_mut_ptr().cast()
    }

    pub(super) fn creation_flags(&self) -> u32 {
        self.binding.creation_flags
    }

    pub(super) fn inherit_handles(&self) -> bool {
        self.binding.inherit_handles
    }

    fn validate_storage(&self) -> Result<(), SupervisorError> {
        if !self.initialized
            || self.storage.is_empty()
            || self.job_handles.len() != 1
            || self.inherited_handles.len() != CHILD_STANDARD_HANDLE_SLOT_COUNT
            || self.job_handles[0].is_null()
            || self.binding.handle_list_count as usize != self.inherited_handles.len()
        {
            return Err(SupervisorError::new(
                "authority_native_job_launch_attribute_list_invalid",
            ));
        }
        Ok(())
    }
}

impl Drop for NativeJobLaunchAttributeList<'_> {
    fn drop(&mut self) {
        if self.initialized && !self.storage.is_empty() {
            unsafe {
                DeleteProcThreadAttributeList(self.storage.as_mut_ptr().cast());
            }
            self.initialized = false;
        }
    }
}

fn raw_handle_list(
    role: ChildBootstrapRole,
    handles: &[HANDLE],
) -> Result<RoleRawHandleListDigest, SupervisorError> {
    if handles.len() != CHILD_STANDARD_HANDLE_SLOT_COUNT {
        return Err(SupervisorError::new(
            "authority_native_job_raw_handle_list_invalid",
        ));
    }
    let mut unique = BTreeSet::new();
    let mut raw_values = [0usize; CHILD_STANDARD_HANDLE_SLOT_COUNT];
    for (index, handle) in handles.iter().enumerate() {
        let value = *handle as usize;
        if handle.is_null()
            || *handle == INVALID_HANDLE_VALUE
            || !unique.insert(value)
            || !handle_is_inheritable(*handle)?
        {
            return Err(SupervisorError::new(
                "authority_native_job_raw_handle_list_invalid",
            ));
        }
        raw_values[index] = value;
    }
    RoleRawHandleListDigest::derive(role, &raw_values)
        .map_err(|_| SupervisorError::new("authority_native_job_raw_handle_list_invalid"))
}

fn handle_is_inheritable(handle: HANDLE) -> Result<bool, SupervisorError> {
    if handle.is_null() || handle == INVALID_HANDLE_VALUE {
        return Err(SupervisorError::new(
            "authority_native_job_process_handle_invalid",
        ));
    }
    let mut flags = 0u32;
    if unsafe { GetHandleInformation(handle, &mut flags) } == 0 {
        return Err(SupervisorError::new(
            "authority_native_job_handle_query_failed",
        ));
    }
    Ok(flags & HANDLE_FLAG_INHERIT != 0)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct NativeJobPreResumeMembershipReceipt {
    pub(super) role: ChildBootstrapRole,
    pub(super) object_id: u64,
    pub(super) authority_generation_digest: Digest,
    pub(super) run_binding_digest: Digest,
    pub(super) security_binding_digest: Digest,
    pub(super) launch_attribute_binding_digest: Digest,
    pub(super) process_id: u32,
    pub(super) primary_thread_id: u32,
    pub(super) process_handle_non_inheritable: bool,
    pub(super) primary_thread_handle_non_inheritable: bool,
    pub(super) job_assigned_at_creation: bool,
    pub(super) initial_assignment_call_performed: bool,
    pub(super) job_membership_revalidated: bool,
    pub(super) membership_readback_before_resume: bool,
    pub(super) exact_roster_readback: bool,
    pub(super) breakaway_allowed: bool,
    pub(super) active_roster_digest: Digest,
    pub(super) receipt_digest: Digest,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct NativeJobPreResumeReadback {
    process_id_from_handle: u32,
    process_id_from_thread_handle: u32,
    primary_thread_id_from_handle: u32,
    process_handle_non_inheritable: bool,
    primary_thread_handle_non_inheritable: bool,
    is_process_in_exact_job: bool,
    exact_process_roster: BTreeSet<u32>,
    strict_live_job_readback: bool,
}

impl NativeJobPreResumeMembershipReceipt {
    fn from_readback(
        job: &WindowsNativeJob,
        launch: &NativeJobLaunchAttributeBinding,
        expected_process_id: u32,
        expected_primary_thread_id: u32,
        readback: &NativeJobPreResumeReadback,
    ) -> Result<Self, SupervisorError> {
        Self::from_readback_for_expected_roster(
            job,
            launch,
            ChildBootstrapRole::BridgeLauncher,
            expected_process_id,
            expected_primary_thread_id,
            &BTreeSet::from([expected_process_id]),
            readback,
        )
    }

    fn from_readback_for_expected_roster(
        job: &WindowsNativeJob,
        launch: &NativeJobLaunchAttributeBinding,
        expected_role: ChildBootstrapRole,
        expected_process_id: u32,
        expected_primary_thread_id: u32,
        expected_roster: &BTreeSet<u32>,
        readback: &NativeJobPreResumeReadback,
    ) -> Result<Self, SupervisorError> {
        launch.validate(job, expected_role)?;
        if expected_process_id == 0
            || expected_primary_thread_id == 0
            || expected_roster.is_empty()
            || expected_roster.len() > MAX_JOB_ROSTER_PROCESSES
            || !expected_roster.contains(&expected_process_id)
            || expected_roster.iter().any(|process_id| *process_id == 0)
            || readback.process_id_from_handle != expected_process_id
            || readback.process_id_from_thread_handle != expected_process_id
            || readback.primary_thread_id_from_handle != expected_primary_thread_id
            || !readback.process_handle_non_inheritable
            || !readback.primary_thread_handle_non_inheritable
            || !readback.is_process_in_exact_job
            || &readback.exact_process_roster != expected_roster
            || !readback.strict_live_job_readback
        {
            return Err(SupervisorError::new(
                "authority_native_job_pre_resume_readback_invalid",
            ));
        }
        let mut receipt = Self {
            role: expected_role,
            object_id: job.receipt.object_id,
            authority_generation_digest: job.security.authority_generation_digest,
            run_binding_digest: job.run_binding_digest,
            security_binding_digest: job.security.binding_digest,
            launch_attribute_binding_digest: launch.binding_digest,
            process_id: expected_process_id,
            primary_thread_id: expected_primary_thread_id,
            process_handle_non_inheritable: true,
            primary_thread_handle_non_inheritable: true,
            job_assigned_at_creation: true,
            initial_assignment_call_performed: false,
            job_membership_revalidated: true,
            membership_readback_before_resume: true,
            exact_roster_readback: true,
            breakaway_allowed: false,
            active_roster_digest: active_roster_digest(expected_roster)?,
            receipt_digest: [0; 32],
        };
        receipt.receipt_digest = receipt.derive_digest();
        receipt.validate(job, launch, expected_role)?;
        Ok(receipt)
    }

    fn validate(
        &self,
        job: &WindowsNativeJob,
        launch: &NativeJobLaunchAttributeBinding,
        expected_role: ChildBootstrapRole,
    ) -> Result<(), SupervisorError> {
        if self.role != expected_role
            || self.object_id != job.receipt.object_id
            || self.authority_generation_digest != job.security.authority_generation_digest
            || self.run_binding_digest != job.run_binding_digest
            || self.security_binding_digest != job.security.binding_digest
            || self.launch_attribute_binding_digest != launch.binding_digest
            || self.process_id == 0
            || self.primary_thread_id == 0
            || !self.process_handle_non_inheritable
            || !self.primary_thread_handle_non_inheritable
            || !self.job_assigned_at_creation
            || self.initial_assignment_call_performed
            || !self.job_membership_revalidated
            || !self.membership_readback_before_resume
            || !self.exact_roster_readback
            || self.breakaway_allowed
            || self.active_roster_digest.iter().all(|byte| *byte == 0)
            || self.receipt_digest.iter().all(|byte| *byte == 0)
            || self.receipt_digest != self.derive_digest()
        {
            return Err(SupervisorError::new(
                "authority_native_job_pre_resume_membership_invalid",
            ));
        }
        launch.validate(job, expected_role)
    }

    fn derive_digest(&self) -> Digest {
        let mut hasher = Sha256::new();
        hasher.update(JOB_PRE_RESUME_MEMBERSHIP_DOMAIN);
        hasher.update([self.role.wire_value()]);
        hasher.update(self.object_id.to_be_bytes());
        hasher.update(self.authority_generation_digest);
        hasher.update(self.run_binding_digest);
        hasher.update(self.security_binding_digest);
        hasher.update(self.launch_attribute_binding_digest);
        hasher.update(self.process_id.to_be_bytes());
        hasher.update(self.primary_thread_id.to_be_bytes());
        for value in [
            self.process_handle_non_inheritable,
            self.primary_thread_handle_non_inheritable,
            self.job_assigned_at_creation,
            self.initial_assignment_call_performed,
            self.job_membership_revalidated,
            self.membership_readback_before_resume,
            self.exact_roster_readback,
            self.breakaway_allowed,
        ] {
            hasher.update([u8::from(value)]);
        }
        hasher.update(self.active_roster_digest);
        hasher.finalize().into()
    }
}

/// Opaque, one-use fresh Job observation for one exact child root. The caller
/// cannot supply a membership digest or roster: both are re-read from the
/// retained Job and process handles immediately before construction. This is
/// a snapshot, not a replacement for retaining and re-reading the active Job.
#[derive(PartialEq, Eq)]
pub(super) struct NativeChildJobObservation {
    role: ChildBootstrapRole,
    process_key: ProcessKey,
    primary_thread_id: u32,
    runner_launch_binding: NativeJobRunnerLaunchBinding,
    membership_epoch_source: Digest,
    observation_digest: Digest,
}

impl fmt::Debug for NativeChildJobObservation {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NativeChildJobObservation")
            .field("role", &self.role)
            .field("process", &"<held-and-redacted>")
            .field("bindings", &"<redacted>")
            .finish()
    }
}

impl NativeChildJobObservation {
    pub(super) const fn role(&self) -> ChildBootstrapRole {
        self.role
    }

    pub(super) const fn process_key(&self) -> ProcessKey {
        self.process_key
    }

    pub(super) const fn primary_thread_id(&self) -> u32 {
        self.primary_thread_id
    }

    pub(super) fn authority_generation_digest(&self) -> &Digest {
        &self.runner_launch_binding.authority_generation_digest
    }

    pub(super) fn run_binding_digest(&self) -> &Digest {
        &self.runner_launch_binding.run_binding_digest
    }

    pub(super) const fn runner_launch_binding(&self) -> NativeJobRunnerLaunchBinding {
        self.runner_launch_binding
    }

    pub(super) fn membership_epoch_source(&self) -> &Digest {
        &self.membership_epoch_source
    }

    pub(super) fn observation_digest(&self) -> &Digest {
        &self.observation_digest
    }
}

fn active_roster_digest(process_ids: &BTreeSet<u32>) -> Result<Digest, SupervisorError> {
    if process_ids.is_empty()
        || process_ids.len() > MAX_JOB_ROSTER_PROCESSES
        || process_ids.iter().any(|process_id| *process_id == 0)
    {
        return Err(SupervisorError::new("authority_native_job_roster_invalid"));
    }
    let mut hasher = Sha256::new();
    hasher.update(JOB_ACTIVE_ROSTER_DOMAIN);
    hasher.update((process_ids.len() as u32).to_be_bytes());
    for process_id in process_ids {
        hasher.update(process_id.to_be_bytes());
    }
    Ok(hasher.finalize().into())
}

fn root_process_ids_digest(process_ids: &BTreeSet<u32>) -> Result<Digest, SupervisorError> {
    if process_ids.len() < MIN_TERMINAL_ROOT_PROCESS_COUNT
        || process_ids.len() > FIXED_ROOT_PROCESS_COUNT
        || process_ids.iter().any(|process_id| *process_id == 0)
    {
        return Err(SupervisorError::new(
            "authority_native_job_root_process_ids_invalid",
        ));
    }
    let mut hasher = Sha256::new();
    hasher.update(JOB_ROOT_PROCESS_IDS_DOMAIN);
    hasher.update((process_ids.len() as u32).to_be_bytes());
    for process_id in process_ids {
        hasher.update(process_id.to_be_bytes());
    }
    Ok(hasher.finalize().into())
}

fn root_process_epochs_digest(
    root_processes: &BTreeMap<u32, HeldRootProcess>,
) -> Result<Digest, SupervisorError> {
    if root_processes.len() < MIN_TERMINAL_ROOT_PROCESS_COUNT
        || root_processes.len() > FIXED_ROOT_PROCESS_COUNT
    {
        return Err(SupervisorError::new(
            "authority_native_job_root_process_epochs_invalid",
        ));
    }
    let mut hasher = Sha256::new();
    hasher.update(JOB_ROOT_PROCESS_EPOCHS_DOMAIN);
    hasher.update((root_processes.len() as u32).to_be_bytes());
    for (process_id, process) in root_processes {
        if *process_id == 0
            || process.process_id != *process_id
            || process.creation_time == 0
            || process.epoch_digest != process_epoch_digest(*process_id, process.creation_time)?
        {
            return Err(SupervisorError::new(
                "authority_native_job_root_process_epochs_invalid",
            ));
        }
        hasher.update(process_id.to_be_bytes());
        hasher.update(process.creation_time.to_be_bytes());
        hasher.update(process.epoch_digest);
    }
    Ok(hasher.finalize().into())
}

fn root_process_terminal_readback_digest(
    root_processes: &BTreeMap<u32, HeldRootProcess>,
    memberships: &BTreeMap<u32, NativeJobPreResumeMembershipReceipt>,
) -> Result<Digest, SupervisorError> {
    if root_processes.len() < MIN_TERMINAL_ROOT_PROCESS_COUNT
        || root_processes.len() > FIXED_ROOT_PROCESS_COUNT
        || root_processes.len() != memberships.len()
        || root_processes.keys().ne(memberships.keys())
    {
        return Err(SupervisorError::new(
            "authority_native_job_terminal_process_readback_invalid",
        ));
    }
    let mut hasher = Sha256::new();
    hasher.update(JOB_ROOT_PROCESS_TERMINAL_READBACK_DOMAIN);
    hasher.update((root_processes.len() as u32).to_be_bytes());
    for (process_id, process) in root_processes {
        let membership = memberships.get(process_id).ok_or_else(|| {
            SupervisorError::new("authority_native_job_terminal_process_readback_invalid")
        })?;
        if *process_id == 0
            || process.process_id != *process_id
            || membership.process_id != *process_id
        {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_process_readback_invalid",
            ));
        }
        let exit_code = process.terminal_exit_code()?;
        hasher.update([membership.role.wire_value()]);
        hasher.update(process_id.to_be_bytes());
        hasher.update(process.creation_time.to_be_bytes());
        hasher.update(process.epoch_digest);
        hasher.update(exit_code.to_be_bytes());
    }
    Ok(hasher.finalize().into())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum NativeJobCompletionKind {
    NewProcess,
    ExitProcess,
    AbnormalExitProcess,
    ActiveProcessZero,
}

impl NativeJobCompletionKind {
    fn transcript_tag(self) -> u8 {
        match self {
            Self::NewProcess => 1,
            Self::ExitProcess => 2,
            Self::AbnormalExitProcess => 3,
            Self::ActiveProcessZero => 4,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct NativeJobCompletionMessage {
    kind: NativeJobCompletionKind,
    process_id: Option<u32>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct NativeJobRosterTracker {
    known_root_process_ids: BTreeSet<u32>,
    // This is the exact admitted kernel roster, not a completion-message reconstruction.
    active_process_ids: BTreeSet<u32>,
    observed_new_process_ids: BTreeSet<u32>,
    observed_terminal_process_ids: BTreeSet<u32>,
    observed_abnormal_process_ids: BTreeSet<u32>,
    new_process_events: u32,
    exit_process_events: u32,
    abnormal_exit_process_events: u32,
    completion_message_count: u32,
    completion_transcript_digest: Digest,
    active_process_zero_observed: bool,
}

impl NativeJobRosterTracker {
    fn from_pre_resume_root(process_id: u32) -> Result<Self, SupervisorError> {
        if process_id == 0 {
            return Err(SupervisorError::new("authority_native_job_roster_invalid"));
        }
        let known_root_process_ids = BTreeSet::from([process_id]);
        Ok(Self {
            known_root_process_ids: known_root_process_ids.clone(),
            active_process_ids: known_root_process_ids,
            observed_new_process_ids: BTreeSet::new(),
            observed_terminal_process_ids: BTreeSet::new(),
            observed_abnormal_process_ids: BTreeSet::new(),
            new_process_events: 0,
            exit_process_events: 0,
            abnormal_exit_process_events: 0,
            completion_message_count: 0,
            completion_transcript_digest: Sha256::digest(JOB_TERMINAL_TRANSCRIPT_DOMAIN).into(),
            active_process_zero_observed: false,
        })
    }

    fn observe_additional_pre_resume_root(
        &mut self,
        process_id: u32,
        exact_process_roster: &BTreeSet<u32>,
    ) -> Result<(), SupervisorError> {
        let mut expected = self.active_process_ids.clone();
        if process_id == 0 || !expected.insert(process_id) || &expected != exact_process_roster {
            return Err(SupervisorError::new(
                "authority_native_job_pre_resume_roster_invalid",
            ));
        }
        self.active_process_ids = expected;
        self.known_root_process_ids = self.active_process_ids.clone();
        Ok(())
    }

    fn observe_terminal_advisory(
        &mut self,
        message: NativeJobCompletionMessage,
    ) -> Result<(), SupervisorError> {
        // Ordinary Job completion messages are untrusted advisory evidence: Windows may
        // delay or omit any of them. Record every unique, well-formed message in the
        // transcript, but never let notifications mutate the admitted kernel roster or
        // become a prerequisite for the held-handle/accounting terminal proof.
        match (message.kind, message.process_id) {
            (NativeJobCompletionKind::NewProcess, Some(process_id))
                if process_id != 0
                    && self.known_root_process_ids.contains(&process_id)
                    && self.observed_new_process_ids.insert(process_id) =>
            {
                self.new_process_events =
                    self.new_process_events.checked_add(1).ok_or_else(|| {
                        SupervisorError::new("authority_native_job_completion_roster_invalid")
                    })?;
            }
            (NativeJobCompletionKind::ExitProcess, Some(process_id))
                if process_id != 0
                    && self.known_root_process_ids.contains(&process_id)
                    && self.observed_terminal_process_ids.insert(process_id) =>
            {
                self.exit_process_events =
                    self.exit_process_events.checked_add(1).ok_or_else(|| {
                        SupervisorError::new("authority_native_job_completion_roster_invalid")
                    })?;
            }
            (NativeJobCompletionKind::AbnormalExitProcess, Some(process_id))
                if process_id != 0
                    && self.known_root_process_ids.contains(&process_id)
                    && self.observed_abnormal_process_ids.insert(process_id) =>
            {
                self.abnormal_exit_process_events = self
                    .abnormal_exit_process_events
                    .checked_add(1)
                    .ok_or_else(|| {
                        SupervisorError::new("authority_native_job_completion_roster_invalid")
                    })?;
            }
            (NativeJobCompletionKind::ActiveProcessZero, None)
                if !self.active_process_zero_observed =>
            {
                self.active_process_zero_observed = true;
            }
            _ => {
                return Err(SupervisorError::new(
                    "authority_native_job_completion_roster_invalid",
                ));
            }
        }
        self.completion_message_count =
            self.completion_message_count
                .checked_add(1)
                .ok_or_else(|| {
                    SupervisorError::new("authority_native_job_completion_roster_invalid")
                })?;
        let mut transcript = Sha256::new();
        transcript.update(JOB_TERMINAL_TRANSCRIPT_DOMAIN);
        transcript.update(self.completion_transcript_digest);
        transcript.update(self.completion_message_count.to_be_bytes());
        transcript.update([message.kind.transcript_tag()]);
        transcript.update(message.process_id.unwrap_or_default().to_be_bytes());
        self.completion_transcript_digest = transcript.finalize().into();
        Ok(())
    }

    fn validate_terminal_transcript(
        &self,
        expected_root_process_ids: &BTreeSet<u32>,
    ) -> Result<(), SupervisorError> {
        let expected_messages = self
            .new_process_events
            .checked_add(self.exit_process_events)
            .and_then(|value| value.checked_add(self.abnormal_exit_process_events))
            .and_then(|value| value.checked_add(u32::from(self.active_process_zero_observed)))
            .ok_or_else(|| {
                SupervisorError::new("authority_native_job_completion_roster_invalid")
            })?;
        if expected_root_process_ids.len() < MIN_TERMINAL_ROOT_PROCESS_COUNT
            || expected_root_process_ids.len() > FIXED_ROOT_PROCESS_COUNT
            || &self.known_root_process_ids != expected_root_process_ids
            || &self.active_process_ids != expected_root_process_ids
            || !self
                .observed_new_process_ids
                .is_subset(expected_root_process_ids)
            || !self
                .observed_terminal_process_ids
                .is_subset(expected_root_process_ids)
            || !self
                .observed_abnormal_process_ids
                .is_subset(expected_root_process_ids)
            || self.new_process_events != self.observed_new_process_ids.len() as u32
            || self.exit_process_events != self.observed_terminal_process_ids.len() as u32
            || self.abnormal_exit_process_events != self.observed_abnormal_process_ids.len() as u32
            || self.completion_message_count != expected_messages
            || self.completion_message_count as usize > MAX_JOB_COMPLETION_EVENTS
            || self
                .completion_transcript_digest
                .iter()
                .all(|byte| *byte == 0)
        {
            return Err(SupervisorError::new(
                "authority_native_job_completion_roster_invalid",
            ));
        }
        Ok(())
    }
}

struct NativeJobTerminalProofKey(Digest);

fn volatile_zero_terminal_key(value: &mut Digest) {
    for byte in value {
        unsafe {
            write_volatile(byte, 0);
        }
    }
    compiler_fence(Ordering::SeqCst);
}

impl NativeJobTerminalProofKey {
    fn generate() -> Result<Self, SupervisorError> {
        let mut value = [0u8; 32];
        if getrandom::fill(&mut value).is_err() {
            volatile_zero_terminal_key(&mut value);
            return Err(SupervisorError::new(
                "authority_native_job_terminal_key_unavailable",
            ));
        }
        if value.iter().all(|byte| *byte == 0) {
            volatile_zero_terminal_key(&mut value);
            return Err(SupervisorError::new(
                "authority_native_job_terminal_key_unavailable",
            ));
        }
        Ok(Self(value))
    }

    fn new_mac(&self) -> HmacSha256 {
        <HmacSha256 as Mac>::new_from_slice(&self.0).expect("fixed-size native Job terminal key")
    }
}

impl Drop for NativeJobTerminalProofKey {
    fn drop(&mut self) {
        volatile_zero_terminal_key(&mut self.0);
    }
}

#[derive(Clone, PartialEq, Eq)]
struct NativeJobTerminalDrainReceipt {
    object_id: u64,
    deterministic_name_digest: Digest,
    created_at: u64,
    authority_generation_digest: Digest,
    run_binding_digest: Digest,
    security_binding_digest: Digest,
    root_process_count: u32,
    root_process_ids_digest: Digest,
    root_process_epochs_digest: Digest,
    root_process_terminal_readback_digest: Digest,
    total_processes: u32,
    new_process_events: u32,
    exit_process_events: u32,
    abnormal_exit_process_events: u32,
    completion_message_count: u32,
    completion_transcript_digest: Digest,
    active_process_zero_observed: bool,
    completion_port_drained: bool,
    exact_empty_roster_readback: bool,
    active_processes_zero: bool,
    accounting_snapshot_stable: bool,
    all_root_process_handles_signaled: bool,
    all_root_process_handles_non_inheritable: bool,
    receipt_mac: Digest,
}

impl fmt::Debug for NativeJobTerminalDrainReceipt {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("NativeJobTerminalDrainReceipt(<bound-and-redacted>)")
    }
}

impl NativeJobTerminalDrainReceipt {
    fn validate(&self, key: &NativeJobTerminalProofKey) -> Result<(), SupervisorError> {
        let expected_messages = self
            .new_process_events
            .checked_add(self.exit_process_events)
            .and_then(|value| value.checked_add(self.abnormal_exit_process_events))
            .and_then(|value| value.checked_add(u32::from(self.active_process_zero_observed)))
            .ok_or_else(|| SupervisorError::new("authority_native_job_terminal_drain_invalid"))?;
        if self.object_id == 0
            || self.deterministic_name_digest.iter().all(|byte| *byte == 0)
            || self.created_at == 0
            || self
                .authority_generation_digest
                .iter()
                .all(|byte| *byte == 0)
            || self.run_binding_digest.iter().all(|byte| *byte == 0)
            || self.security_binding_digest.iter().all(|byte| *byte == 0)
            || self.root_process_count < MIN_TERMINAL_ROOT_PROCESS_COUNT as u32
            || self.root_process_count > FIXED_ROOT_PROCESS_COUNT as u32
            || self.root_process_ids_digest.iter().all(|byte| *byte == 0)
            || self
                .root_process_epochs_digest
                .iter()
                .all(|byte| *byte == 0)
            || self
                .root_process_terminal_readback_digest
                .iter()
                .all(|byte| *byte == 0)
            || self.new_process_events > self.root_process_count
            || self.exit_process_events > self.root_process_count
            || self.abnormal_exit_process_events > self.root_process_count
            || self.completion_message_count != expected_messages
            || self.completion_message_count as usize > MAX_JOB_COMPLETION_EVENTS
            || self
                .completion_transcript_digest
                .iter()
                .all(|byte| *byte == 0)
            || self.total_processes != self.root_process_count
            || !self.completion_port_drained
            || !self.exact_empty_roster_readback
            || !self.active_processes_zero
            || !self.accounting_snapshot_stable
            || !self.all_root_process_handles_signaled
            || !self.all_root_process_handles_non_inheritable
            || self.receipt_mac.iter().all(|byte| *byte == 0)
        {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_drain_invalid",
            ));
        }
        let mut verifier = key.new_mac();
        self.update_mac(&mut verifier);
        verifier.verify_slice(&self.receipt_mac).map_err(|_| {
            SupervisorError::new("authority_native_job_terminal_authentication_invalid")
        })?;
        Ok(())
    }

    fn validate_for_job(&self, job: &WindowsNativeJob) -> Result<(), SupervisorError> {
        self.validate(&job.terminal_proof_key)?;
        if self.object_id != job.receipt.object_id
            || self.deterministic_name_digest != job.receipt.deterministic_name_digest
            || self.created_at != job.receipt.created_at
            || self.authority_generation_digest != job.security.authority_generation_digest
            || self.run_binding_digest != job.run_binding_digest
            || self.security_binding_digest != job.security.binding_digest
        {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_context_invalid",
            ));
        }
        Ok(())
    }

    fn derive_mac(&self, key: &NativeJobTerminalProofKey) -> Digest {
        let mut mac = key.new_mac();
        self.update_mac(&mut mac);
        mac.finalize().into_bytes().into()
    }

    fn update_mac(&self, mac: &mut HmacSha256) {
        mac.update(JOB_TERMINAL_DRAIN_DOMAIN);
        mac.update(&self.object_id.to_be_bytes());
        mac.update(&self.deterministic_name_digest);
        mac.update(&self.created_at.to_be_bytes());
        mac.update(&self.authority_generation_digest);
        mac.update(&self.run_binding_digest);
        mac.update(&self.security_binding_digest);
        mac.update(&self.root_process_count.to_be_bytes());
        mac.update(&self.root_process_ids_digest);
        mac.update(&self.root_process_epochs_digest);
        mac.update(&self.root_process_terminal_readback_digest);
        mac.update(&self.total_processes.to_be_bytes());
        mac.update(&self.new_process_events.to_be_bytes());
        mac.update(&self.exit_process_events.to_be_bytes());
        mac.update(&self.abnormal_exit_process_events.to_be_bytes());
        mac.update(&self.completion_message_count.to_be_bytes());
        mac.update(&self.completion_transcript_digest);
        for value in [
            self.active_process_zero_observed,
            self.completion_port_drained,
            self.exact_empty_roster_readback,
            self.active_processes_zero,
            self.accounting_snapshot_stable,
            self.all_root_process_handles_signaled,
            self.all_root_process_handles_non_inheritable,
        ] {
            mac.update(&[u8::from(value)]);
        }
    }
}

pub(super) struct NativeJobTerminalProof {
    receipt: NativeJobTerminalDrainReceipt,
    key: NativeJobTerminalProofKey,
}

impl fmt::Debug for NativeJobTerminalProof {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("NativeJobTerminalProof(<held-and-redacted>)")
    }
}

impl NativeJobTerminalProof {
    pub(super) fn consume_for_runner(
        self,
        expected_object_id: u64,
        expected_deterministic_name_digest: &Digest,
        expected_authority_generation_digest: &Digest,
        expected_run_binding_digest: &Digest,
        expected_security_binding_digest: &Digest,
    ) -> Result<NativeJobTerminalCompletion, SupervisorError> {
        self.receipt.validate(&self.key)?;
        if self.receipt.object_id != expected_object_id
            || &self.receipt.deterministic_name_digest != expected_deterministic_name_digest
            || &self.receipt.authority_generation_digest != expected_authority_generation_digest
            || &self.receipt.run_binding_digest != expected_run_binding_digest
            || &self.receipt.security_binding_digest != expected_security_binding_digest
        {
            return Err(SupervisorError::new(
                "authority_native_runner_terminal_job_binding_invalid",
            ));
        }
        Ok(NativeJobTerminalCompletion {
            receipt_mac: self.receipt.receipt_mac,
        })
    }
}

pub(super) struct NativeJobTerminalCompletion {
    receipt_mac: Digest,
}

impl fmt::Debug for NativeJobTerminalCompletion {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let _ = &self.receipt_mac;
        formatter.write_str("NativeJobTerminalCompletion(<verified-and-redacted>)")
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum NativeJobTerminalDrainStatus {
    Pending,
    Complete(NativeJobTerminalDrainReceipt),
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum NativeJobTerminalState {
    Open,
    Terminating {
        root_process_count: u32,
        root_process_ids_digest: Digest,
        root_process_epochs_digest: Digest,
        termination_requested: bool,
    },
    Complete(NativeJobTerminalDrainReceipt),
    FaultContaining,
    FaultHeld,
}

#[derive(Debug)]
pub(super) struct WindowsNativeActiveJob {
    job: WindowsNativeJob,
    root_memberships: BTreeMap<u32, NativeJobPreResumeMembershipReceipt>,
    held_root_processes: BTreeMap<u32, HeldRootProcess>,
    quarantined_root_processes: BTreeMap<u32, HeldRootProcess>,
    roster: NativeJobRosterTracker,
    admission_faulted: bool,
    terminal_state: NativeJobTerminalState,
}

pub(super) struct NativeJobTerminalDrainFailure {
    job: WindowsNativeActiveJob,
    error: SupervisorError,
}

impl fmt::Debug for NativeJobTerminalDrainFailure {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NativeJobTerminalDrainFailure")
            .field("job", &"<held-and-redacted>")
            .field("error", &self.error.code())
            .finish()
    }
}

impl NativeJobTerminalDrainFailure {
    pub(super) fn into_parts(self) -> (WindowsNativeActiveJob, SupervisorError) {
        (self.job, self.error)
    }
}

// This is an independently safe kernel primitive, not an enabled supervisor. It creates no
// process and remains unreachable from production while the service runtime's global native
// supervisor gate is closed. Keeping both handles alive is required before any future suspended
// child can be assigned and before its completion-port roster can be trusted.
pub(super) struct WindowsNativeJob {
    job: OwnedKernelHandle,
    completion_port: OwnedKernelHandle,
    security: NativeJobSecuritySpec,
    run_binding_digest: Digest,
    receipt: NativeJobReceipt,
    readback: NativeJobReadback,
    terminal_proof_key: NativeJobTerminalProofKey,
}

impl fmt::Debug for WindowsNativeJob {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("WindowsNativeJob")
            .field("job", &"<held-and-redacted>")
            .field("completion_port", &"<held-and-redacted>")
            .field("security", &self.security)
            .field("receipt", &self.receipt)
            .field("readback", &self.readback)
            .field("terminal_proof_key", &"<held-and-redacted>")
            .finish()
    }
}

pub(super) struct NativeJobExclusionHandles<'a> {
    job: BorrowedHandle<'a>,
    completion_port: BorrowedHandle<'a>,
}

pub(super) struct NativeInitialRootAdmissionFailure {
    job: WindowsNativeJob,
    error: SupervisorError,
}

impl NativeInitialRootAdmissionFailure {
    pub(super) fn into_parts(self) -> (WindowsNativeJob, SupervisorError) {
        (self.job, self.error)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct NativeUnadmittedRootContainmentReceipt {
    pub(super) process_id: u32,
    pub(super) exact_job_membership_proven: bool,
    pub(super) job_termination_requested: bool,
    pub(super) direct_process_termination_requested: bool,
    pub(super) process_signaled: bool,
    pub(super) exact_empty_terminal_job: bool,
}

impl fmt::Debug for NativeInitialRootAdmissionFailure {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NativeInitialRootAdmissionFailure")
            .field("job", &"<held-and-redacted>")
            .field("error", &self.error.code())
            .finish()
    }
}

impl NativeJobExclusionHandles<'_> {
    pub(super) fn job(&self) -> BorrowedHandle<'_> {
        self.job
    }

    pub(super) fn completion_port(&self) -> BorrowedHandle<'_> {
        self.completion_port
    }
}

impl WindowsNativeJob {
    pub(super) fn exclusion_handles(&self) -> NativeJobExclusionHandles<'_> {
        NativeJobExclusionHandles {
            job: self.job.borrowed(),
            completion_port: self.completion_port.borrowed(),
        }
    }

    pub(super) fn create(
        policy: &SupervisorPolicy,
        security: &NativeJobSecuritySpec,
        created_at: u64,
    ) -> Result<Self, SupervisorError> {
        Self::create_from_spec(
            NativeJobSpec::from_policy(policy, security, created_at)?,
            security,
        )
    }

    fn create_from_spec(
        spec: NativeJobSpec,
        security: &NativeJobSecuritySpec,
    ) -> Result<Self, SupervisorError> {
        spec.validate()?;
        security.validate()?;
        if spec.security_binding_digest != security.binding_digest {
            return Err(SupervisorError::new(
                "authority_native_job_security_binding_mismatch",
            ));
        }
        let descriptor = OwnedSecurityDescriptor::from_spec(security)?;
        verify_security_descriptor(descriptor.raw(), security)?;
        let attributes = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor.raw(),
            bInheritHandle: 0,
        };
        let name = spec.deterministic_name();
        unsafe {
            SetLastError(ERROR_SUCCESS);
        }
        let raw_job = unsafe { CreateJobObjectW(&attributes, name.as_ptr()) };
        let create_status = unsafe { GetLastError() };
        let job = OwnedKernelHandle::new(raw_job, "authority_native_job_create_failed")?;
        if create_status == ERROR_ALREADY_EXISTS {
            return Err(SupervisorError::new(
                "authority_native_job_name_already_exists",
            ));
        }
        if create_status != ERROR_SUCCESS {
            return Err(SupervisorError::new(
                "authority_native_job_create_status_invalid",
            ));
        }
        if job.is_inheritable()? {
            return Err(SupervisorError::new(
                "authority_native_job_handle_inheritable",
            ));
        }

        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if unsafe {
            SetInformationJobObject(
                job.raw(),
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const c_void,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        } == 0
        {
            return Err(SupervisorError::new(
                "authority_native_job_configure_failed",
            ));
        }

        let completion_port = OwnedKernelHandle::new(
            unsafe {
                CreateIoCompletionPort(
                    INVALID_HANDLE_VALUE,
                    null_mut(),
                    0,
                    JOB_COMPLETION_CONCURRENCY,
                )
            },
            "authority_native_job_completion_port_create_failed",
        )?;
        if completion_port.is_inheritable()? {
            return Err(SupervisorError::new(
                "authority_native_job_completion_port_inheritable",
            ));
        }

        let association = JOBOBJECT_ASSOCIATE_COMPLETION_PORT {
            CompletionKey: spec.completion_key()? as *mut c_void,
            CompletionPort: completion_port.raw(),
        };
        if unsafe {
            SetInformationJobObject(
                job.raw(),
                JobObjectAssociateCompletionPortInformation,
                &association as *const _ as *const c_void,
                size_of::<JOBOBJECT_ASSOCIATE_COMPLETION_PORT>() as u32,
            )
        } == 0
        {
            return Err(SupervisorError::new(
                "authority_native_job_completion_port_attach_failed",
            ));
        }

        let readback = query_readback(&job, &completion_port, security)?;
        readback.validate_empty_strict_job(security)?;
        let terminal_proof_key = NativeJobTerminalProofKey::generate()?;
        let receipt = NativeJobReceipt {
            object_id: spec.object_id,
            deterministic_name_digest: spec.deterministic_name_digest,
            security_binding_digest: readback.security.binding_digest,
            exact_security_readback: true,
            owner_local_system: readback.security.owner_local_system,
            dacl_present: readback.security.dacl_present,
            dacl_defaulted: readback.security.dacl_defaulted,
            dacl_protected: readback.security.dacl_protected,
            dacl_ace_count: readback.security.ace_count,
            system_access_mask: readback.security.system_access_mask,
            service_access_mask: readback.security.service_access_mask,
            created_at: spec.created_at,
            kill_on_job_close: true,
            breakaway_allowed: false,
            silent_breakaway_allowed: false,
            active_process_limit: 0,
            completion_port_attached: true,
            service_handle_held: true,
        };
        Ok(Self {
            job,
            completion_port,
            security: security.clone(),
            run_binding_digest: spec.run_binding_digest,
            receipt,
            readback,
            terminal_proof_key,
        })
    }

    pub(super) fn receipt(&self) -> &NativeJobReceipt {
        &self.receipt
    }

    pub(super) fn runner_launch_binding(
        &self,
    ) -> Result<NativeJobRunnerLaunchBinding, SupervisorError> {
        NativeJobRunnerLaunchBinding::new(
            self.receipt.object_id,
            self.receipt.deterministic_name_digest,
            self.security.authority_generation_digest,
            self.run_binding_digest,
            self.security.binding_digest,
        )
    }

    pub(super) fn revalidate_empty(&self) -> Result<(), SupervisorError> {
        query_readback(&self.job, &self.completion_port, &self.security)?
            .validate_empty_strict_job(&self.security)
    }

    pub(super) fn prepare_suspended_launch_attributes<'a>(
        &'a self,
        inherited_handles: &'a [BorrowedHandle<'a>],
    ) -> Result<NativeJobLaunchAttributeList<'a>, SupervisorError> {
        self.prepare_suspended_launch_attributes_for_active_roster(
            ChildBootstrapRole::BridgeLauncher,
            inherited_handles,
            0,
        )
    }

    fn prepare_suspended_launch_attributes_for_active_roster<'a>(
        &'a self,
        role: ChildBootstrapRole,
        inherited_handles: &'a [BorrowedHandle<'a>],
        expected_active_processes: u32,
    ) -> Result<NativeJobLaunchAttributeList<'a>, SupervisorError> {
        if expected_active_processes == 0 {
            self.revalidate_empty()?;
        } else {
            query_readback(&self.job, &self.completion_port, &self.security)?
                .validate_strict_live_job(&self.security, expected_active_processes)?;
        }
        self.build_suspended_launch_attributes(role, inherited_handles)
    }

    fn build_suspended_launch_attributes<'a>(
        &'a self,
        role: ChildBootstrapRole,
        inherited_handles: &'a [BorrowedHandle<'a>],
    ) -> Result<NativeJobLaunchAttributeList<'a>, SupervisorError> {
        let raw_handles = inherited_handles
            .iter()
            .map(|handle| handle.as_raw_handle() as HANDLE)
            .collect::<Vec<_>>();
        if raw_handles
            .iter()
            .any(|handle| *handle == self.job.raw() || *handle == self.completion_port.raw())
        {
            return Err(SupervisorError::new(
                "authority_native_job_raw_handle_list_invalid",
            ));
        }
        let binding = NativeJobLaunchAttributeBinding::new(self, role, &raw_handles)?;
        let mut required = 0usize;
        unsafe {
            SetLastError(ERROR_SUCCESS);
        }
        if unsafe {
            InitializeProcThreadAttributeList(
                null_mut(),
                JOB_LAUNCH_ATTRIBUTE_COUNT,
                0,
                &mut required,
            )
        } != 0
            || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER
            || required == 0
            || required > 1024 * 1024
        {
            return Err(SupervisorError::new(
                "authority_native_job_launch_attribute_size_invalid",
            ));
        }
        let word_size = size_of::<usize>();
        let word_count = required.checked_add(word_size - 1).ok_or_else(|| {
            SupervisorError::new("authority_native_job_launch_attribute_size_invalid")
        })? / word_size;
        let mut value = NativeJobLaunchAttributeList {
            storage: vec![0usize; word_count],
            job_handles: Box::new([self.job.raw()]),
            inherited_handles: raw_handles.into_boxed_slice(),
            binding,
            initialized: false,
            _job_lifetime: PhantomData,
        };
        if unsafe {
            InitializeProcThreadAttributeList(
                value.storage.as_mut_ptr().cast(),
                JOB_LAUNCH_ATTRIBUTE_COUNT,
                0,
                &mut required,
            )
        } == 0
        {
            return Err(SupervisorError::new(
                "authority_native_job_launch_attribute_init_failed",
            ));
        }
        value.initialized = true;
        if unsafe {
            UpdateProcThreadAttribute(
                value.storage.as_mut_ptr().cast(),
                0,
                PROC_THREAD_ATTRIBUTE_JOB_LIST as usize,
                value.job_handles.as_ptr().cast(),
                size_of::<HANDLE>(),
                null_mut(),
                std::ptr::null(),
            )
        } == 0
        {
            return Err(SupervisorError::new(
                "authority_native_job_job_list_attribute_failed",
            ));
        }
        let handle_list_bytes = value
            .inherited_handles
            .len()
            .checked_mul(size_of::<HANDLE>())
            .ok_or_else(|| SupervisorError::new("authority_native_job_raw_handle_list_invalid"))?;
        if unsafe {
            UpdateProcThreadAttribute(
                value.storage.as_mut_ptr().cast(),
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                value.inherited_handles.as_ptr().cast(),
                handle_list_bytes,
                null_mut(),
                std::ptr::null(),
            )
        } == 0
        {
            return Err(SupervisorError::new(
                "authority_native_job_handle_list_attribute_failed",
            ));
        }
        value.validate_storage()?;
        value.binding.validate(self, role)?;
        Ok(value)
    }

    pub(super) fn revalidate_created_root_before_resume(
        self,
        process: BorrowedHandle<'_>,
        primary_thread: BorrowedHandle<'_>,
        expected_process_id: u32,
        expected_primary_thread_id: u32,
        launch: &NativeJobLaunchAttributeBinding,
    ) -> Result<WindowsNativeActiveJob, SupervisorError> {
        self.revalidate_created_root_before_resume_preserving_job(
            process,
            primary_thread,
            expected_process_id,
            expected_primary_thread_id,
            launch,
        )
        .map_err(|failure| failure.into_parts().1)
    }

    pub(super) fn revalidate_created_root_before_resume_preserving_job(
        self,
        process: BorrowedHandle<'_>,
        primary_thread: BorrowedHandle<'_>,
        expected_process_id: u32,
        expected_primary_thread_id: u32,
        launch: &NativeJobLaunchAttributeBinding,
    ) -> Result<WindowsNativeActiveJob, NativeInitialRootAdmissionFailure> {
        let (membership, held_root, roster) = match self
            .revalidate_created_root_before_resume_inner(
                process,
                primary_thread,
                expected_process_id,
                expected_primary_thread_id,
                launch,
            ) {
            Ok(value) => value,
            Err(error) => {
                return Err(NativeInitialRootAdmissionFailure { job: self, error });
            }
        };
        Ok(WindowsNativeActiveJob {
            job: self,
            root_memberships: BTreeMap::from([(expected_process_id, membership)]),
            held_root_processes: BTreeMap::from([(expected_process_id, held_root)]),
            quarantined_root_processes: BTreeMap::new(),
            roster,
            admission_faulted: false,
            terminal_state: NativeJobTerminalState::Open,
        })
    }

    fn revalidate_created_root_before_resume_inner(
        &self,
        process: BorrowedHandle<'_>,
        primary_thread: BorrowedHandle<'_>,
        expected_process_id: u32,
        expected_primary_thread_id: u32,
        launch: &NativeJobLaunchAttributeBinding,
    ) -> Result<
        (
            NativeJobPreResumeMembershipReceipt,
            HeldRootProcess,
            NativeJobRosterTracker,
        ),
        SupervisorError,
    > {
        launch.validate(self, ChildBootstrapRole::BridgeLauncher)?;
        let process_handle = process.as_raw_handle() as HANDLE;
        let thread_handle = primary_thread.as_raw_handle() as HANDLE;
        let mut in_job = 0;
        if unsafe { IsProcessInJob(process_handle, self.job.raw(), &mut in_job) } == 0 {
            return Err(SupervisorError::new(
                "authority_native_job_membership_readback_invalid",
            ));
        }
        let held_root = HeldRootProcess::duplicate_from_admission(
            &self.job,
            process_handle,
            expected_process_id,
        )?;
        let readback_a = query_readback(&self.job, &self.completion_port, &self.security)?;
        let roster = query_process_roster(&self.job)?;
        let readback_b = query_readback(&self.job, &self.completion_port, &self.security)?;
        readback_a.validate_strict_live_job(&self.security, 1)?;
        readback_b.validate_strict_live_job(&self.security, 1)?;
        if readback_a != readback_b {
            return Err(SupervisorError::new(
                "authority_native_job_roster_readback_invalid",
            ));
        }
        // Completion-port delivery is advisory and may be delayed or omitted. Pre-resume
        // authority comes from the held process object, its creation epoch, exact job
        // membership, and a stable kernel accounting/roster snapshot.
        let readback = NativeJobPreResumeReadback {
            process_id_from_handle: unsafe { GetProcessId(process_handle) },
            process_id_from_thread_handle: unsafe { GetProcessIdOfThread(thread_handle) },
            primary_thread_id_from_handle: unsafe { GetThreadId(thread_handle) },
            process_handle_non_inheritable: !handle_is_inheritable(process_handle)?,
            primary_thread_handle_non_inheritable: !handle_is_inheritable(thread_handle)?,
            is_process_in_exact_job: in_job != 0,
            exact_process_roster: roster,
            strict_live_job_readback: true,
        };
        let membership = NativeJobPreResumeMembershipReceipt::from_readback(
            self,
            launch,
            expected_process_id,
            expected_primary_thread_id,
            &readback,
        )?;
        Ok((
            membership,
            held_root,
            NativeJobRosterTracker::from_pre_resume_root(expected_process_id)?,
        ))
    }

    pub(super) fn contain_unadmitted_created_root(
        &mut self,
        process: BorrowedHandle<'_>,
    ) -> Result<NativeUnadmittedRootContainmentReceipt, SupervisorError> {
        let process = process.as_raw_handle() as HANDLE;
        if process.is_null()
            || process == INVALID_HANDLE_VALUE
            || unsafe { GetProcessId(process) } == 0
        {
            return Err(SupervisorError::new(
                "authority_native_job_unadmitted_containment_handle_invalid",
            ));
        }
        let mut in_job = 0;
        let membership_proven =
            unsafe { IsProcessInJob(process, self.job.raw(), &mut in_job) } != 0 && in_job != 0;
        let job_termination_succeeded =
            unsafe { TerminateJobObject(self.job.raw(), JOB_TERMINATION_EXIT_CODE) } != 0;
        let direct_process_termination_requested = if !membership_proven
            || !job_termination_succeeded
        {
            terminate_unproven_suspended_process(process).map_err(|_| {
                SupervisorError::new("authority_native_job_unadmitted_containment_request_failed")
            })?
        } else {
            false
        };
        if unsafe { WaitForSingleObject(process, JOB_FAULT_CONTAINMENT_TIMEOUT_MILLIS) }
            != WAIT_OBJECT_0
        {
            return Err(SupervisorError::new(
                "authority_native_job_unadmitted_containment_timeout",
            ));
        }
        if !job_termination_succeeded {
            return Err(SupervisorError::new(
                "authority_native_job_unadmitted_job_termination_unproven",
            ));
        }
        let readback = query_readback(&self.job, &self.completion_port, &self.security)?;
        let roster = query_process_roster(&self.job)?;
        readback.validate_strict_terminal_job(&self.security)?;
        if !roster.is_empty() {
            return Err(SupervisorError::new(
                "authority_native_job_unadmitted_containment_incomplete",
            ));
        }
        Ok(NativeUnadmittedRootContainmentReceipt {
            process_id: unsafe { GetProcessId(process) },
            exact_job_membership_proven: membership_proven,
            job_termination_requested: true,
            direct_process_termination_requested,
            process_signaled: true,
            exact_empty_terminal_job: true,
        })
    }
}

impl WindowsNativeActiveJob {
    pub(super) fn exclusion_handles(&self) -> NativeJobExclusionHandles<'_> {
        self.job.exclusion_handles()
    }

    pub(super) fn runner_launch_binding(
        &self,
    ) -> Result<NativeJobRunnerLaunchBinding, SupervisorError> {
        self.job.runner_launch_binding()
    }

    fn ensure_admission_open(&self) -> Result<(), SupervisorError> {
        match &self.terminal_state {
            NativeJobTerminalState::Open => Ok(()),
            NativeJobTerminalState::FaultContaining | NativeJobTerminalState::FaultHeld => Err(
                SupervisorError::new("authority_native_job_terminal_fault_held"),
            ),
            NativeJobTerminalState::Terminating { .. } | NativeJobTerminalState::Complete(_) => {
                Err(SupervisorError::new(
                    "authority_native_job_terminal_started",
                ))
            }
        }
    }

    pub(super) fn root_membership_receipts(
        &self,
    ) -> &BTreeMap<u32, NativeJobPreResumeMembershipReceipt> {
        &self.root_memberships
    }

    pub(super) fn child_job_observation_digest(
        &self,
        expected_process_id: u32,
    ) -> Result<Digest, SupervisorError> {
        self.ensure_admission_open()?;
        if expected_process_id == 0
            || self.admission_faulted
            || !self
                .roster
                .active_process_ids
                .contains(&expected_process_id)
        {
            return Err(SupervisorError::new(
                "authority_native_child_job_observation_invalid",
            ));
        }
        let first = query_child_job_observation(&self.job.job)?;
        let roster = query_process_roster(&self.job.job)?;
        let second = query_child_job_observation(&self.job.job)?;
        if first != second
            || roster != self.roster.active_process_ids
            || first.limit_flags != JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            || first.active_process_limit != 0
            || first.total_processes != first.active_processes
            || first.active_processes as usize != roster.len()
            || first.total_terminated_processes != 0
        {
            return Err(SupervisorError::new(
                "authority_native_child_job_observation_invalid",
            ));
        }
        job_observation_digest_from_parts(
            first.limit_flags,
            first.active_process_limit,
            first.total_processes,
            first.active_processes,
            first.total_terminated_processes,
            &roster,
        )
        .map_err(|error| SupervisorError::new(error.code()))
    }

    pub(super) fn observe_child_root(
        &self,
        role: ChildBootstrapRole,
        process_key: ProcessKey,
        primary_thread_id: u32,
    ) -> Result<NativeChildJobObservation, SupervisorError> {
        self.ensure_admission_open()?;
        let membership = self.root_memberships.get(&process_key.pid).ok_or_else(|| {
            SupervisorError::new("authority_native_child_job_observation_invalid")
        })?;
        let held_root = self
            .held_root_processes
            .get(&process_key.pid)
            .ok_or_else(|| {
                SupervisorError::new("authority_native_child_job_observation_invalid")
            })?;
        held_root.validate(&self.job.job)?;
        let current_roster_digest = active_roster_digest(&self.roster.active_process_ids)?;
        if self.admission_faulted
            || role != membership.role
            || process_key.pid == 0
            || process_key.creation_time == 0
            || primary_thread_id == 0
            || held_root.process_id != process_key.pid
            || held_root.creation_time != process_key.creation_time
            || membership.process_id != process_key.pid
            || membership.primary_thread_id != primary_thread_id
            || membership.authority_generation_digest
                != self.job.security.authority_generation_digest
            || membership.run_binding_digest != self.job.run_binding_digest
            || membership.security_binding_digest != self.job.security.binding_digest
            || membership.active_roster_digest != current_roster_digest
            || membership.receipt_digest != membership.derive_digest()
            || self
                .quarantined_root_processes
                .contains_key(&process_key.pid)
        {
            return Err(SupervisorError::new(
                "authority_native_child_job_observation_invalid",
            ));
        }
        let observation_digest = self.child_job_observation_digest(process_key.pid)?;
        let runner_launch_binding = self.job.runner_launch_binding()?;
        let mut source = Sha256::new();
        source.update(JOB_CHILD_MEMBERSHIP_EPOCH_SOURCE_DOMAIN);
        source.update([role.wire_value()]);
        source.update(process_key.pid.to_be_bytes());
        source.update(process_key.creation_time.to_be_bytes());
        source.update(primary_thread_id.to_be_bytes());
        source.update(membership.receipt_digest);
        source.update(held_root.epoch_digest);
        source.update(current_roster_digest);
        source.update(runner_launch_binding.object_id.to_be_bytes());
        source.update(runner_launch_binding.deterministic_name_digest);
        source.update(runner_launch_binding.authority_generation_digest);
        source.update(runner_launch_binding.run_binding_digest);
        source.update(runner_launch_binding.security_binding_digest);
        let membership_epoch_source: Digest = source.finalize().into();
        if membership_epoch_source.iter().all(|byte| *byte == 0)
            || membership_epoch_source == observation_digest
        {
            return Err(SupervisorError::new(
                "authority_native_child_job_observation_invalid",
            ));
        }
        Ok(NativeChildJobObservation {
            role,
            process_key,
            primary_thread_id,
            runner_launch_binding,
            membership_epoch_source,
            observation_digest,
        })
    }

    pub(super) fn prepare_additional_suspended_launch_attributes<'a>(
        &'a mut self,
        inherited_handles: &'a [BorrowedHandle<'a>],
    ) -> Result<NativeJobLaunchAttributeList<'a>, SupervisorError> {
        self.ensure_admission_open()?;
        if self.admission_faulted {
            return Err(SupervisorError::new(
                "authority_native_job_admission_fault_held",
            ));
        }
        if let Err(error) = self.validate_additional_admission_kernel_state() {
            return self.start_fault_containment(None, error);
        }
        match self.job.build_suspended_launch_attributes(
            ChildBootstrapRole::LifecycleDriver,
            inherited_handles,
        ) {
            Ok(attributes) => Ok(attributes),
            Err(error) => start_fault_containment_without_candidate(
                &self.job,
                &mut self.admission_faulted,
                &mut self.terminal_state,
                error,
            ),
        }
    }

    fn validate_additional_admission_kernel_state(&self) -> Result<(), SupervisorError> {
        let readback_a =
            query_readback(&self.job.job, &self.job.completion_port, &self.job.security)?;
        let observed_roster = query_process_roster(&self.job.job)?;
        let readback_b =
            query_readback(&self.job.job, &self.job.completion_port, &self.job.security)?;
        if observed_roster != self.roster.active_process_ids {
            return Err(SupervisorError::new(
                "authority_native_job_pre_resume_roster_invalid",
            ));
        }
        let active_processes = u32::try_from(observed_roster.len())
            .map_err(|_| SupervisorError::new("authority_native_job_roster_invalid"))?;
        if active_processes == 0 {
            return Err(SupervisorError::new("authority_native_job_roster_invalid"));
        }
        readback_a.validate_strict_live_job(&self.job.security, active_processes)?;
        readback_b.validate_strict_live_job(&self.job.security, active_processes)?;
        if readback_a != readback_b {
            return Err(SupervisorError::new(
                "authority_native_job_roster_readback_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn revalidate_additional_root_before_resume(
        &mut self,
        process: BorrowedHandle<'_>,
        primary_thread: BorrowedHandle<'_>,
        expected_process_id: u32,
        expected_primary_thread_id: u32,
        launch: &NativeJobLaunchAttributeBinding,
    ) -> Result<NativeJobPreResumeMembershipReceipt, SupervisorError> {
        self.ensure_admission_open()?;
        if self.admission_faulted {
            return Err(SupervisorError::new(
                "authority_native_job_admission_fault_held",
            ));
        }
        let process_handle = process.as_raw_handle() as HANDLE;
        let result = (|| {
            let mut in_job = 0;
            if unsafe { IsProcessInJob(process_handle, self.job.job.raw(), &mut in_job) } == 0
                || in_job == 0
            {
                let direct_error = terminate_unproven_suspended_process(process_handle).err();
                let original = direct_error.unwrap_or_else(|| {
                    SupervisorError::new("authority_native_job_membership_readback_invalid")
                });
                return self.start_fault_containment(None, original);
            }
            let held_candidate = Some(HeldRootProcess::duplicate_from_admission(
                &self.job.job,
                process_handle,
                expected_process_id,
            )?);
            match self.revalidate_additional_root_before_resume_inner(
                process,
                primary_thread,
                expected_process_id,
                expected_primary_thread_id,
                launch,
            ) {
                Ok(value) => Ok((value, held_candidate)),
                Err(error) => self.start_fault_containment(held_candidate, error),
            }
        })();
        let ((membership, next_roster), mut held_candidate) = result?;
        let held_root = match held_candidate.take() {
            Some(held_root) => held_root,
            None => {
                return self.start_fault_containment(
                    None,
                    SupervisorError::new("authority_native_job_process_handle_duplicate_invalid"),
                );
            }
        };
        self.roster = next_roster;
        self.root_memberships
            .insert(expected_process_id, membership.clone());
        self.held_root_processes
            .insert(expected_process_id, held_root);
        Ok(membership)
    }

    fn revalidate_additional_root_before_resume_inner(
        &mut self,
        process: BorrowedHandle<'_>,
        primary_thread: BorrowedHandle<'_>,
        expected_process_id: u32,
        expected_primary_thread_id: u32,
        launch: &NativeJobLaunchAttributeBinding,
    ) -> Result<(NativeJobPreResumeMembershipReceipt, NativeJobRosterTracker), SupervisorError>
    {
        launch.validate(&self.job, ChildBootstrapRole::LifecycleDriver)?;
        if self.root_memberships.len() != 1
            || self.held_root_processes.len() != 1
            || self.root_memberships.contains_key(&expected_process_id)
            || self.held_root_processes.contains_key(&expected_process_id)
            || self
                .root_memberships
                .keys()
                .ne(self.held_root_processes.keys())
            || self
                .roster
                .active_process_ids
                .contains(&expected_process_id)
        {
            return Err(SupervisorError::new(
                "authority_native_job_pre_resume_roster_invalid",
            ));
        }
        let process_handle = process.as_raw_handle() as HANDLE;
        let thread_handle = primary_thread.as_raw_handle() as HANDLE;
        let mut expected_roster = self.roster.active_process_ids.clone();
        if !expected_roster.insert(expected_process_id)
            || expected_roster.len() != FIXED_ROOT_PROCESS_COUNT
        {
            return Err(SupervisorError::new(
                "authority_native_job_pre_resume_roster_invalid",
            ));
        }
        let readback_a =
            query_readback(&self.job.job, &self.job.completion_port, &self.job.security)?;
        let roster = query_process_roster(&self.job.job)?;
        if roster != expected_roster {
            return Err(SupervisorError::new(
                "authority_native_job_pre_resume_roster_invalid",
            ));
        }
        let expected_active_processes = u32::try_from(expected_roster.len())
            .map_err(|_| SupervisorError::new("authority_native_job_roster_invalid"))?;
        let readback_b =
            query_readback(&self.job.job, &self.job.completion_port, &self.job.security)?;
        readback_a.validate_strict_live_job(&self.job.security, expected_active_processes)?;
        readback_b.validate_strict_live_job(&self.job.security, expected_active_processes)?;
        if readback_a != readback_b {
            return Err(SupervisorError::new(
                "authority_native_job_roster_readback_invalid",
            ));
        }
        let readback = NativeJobPreResumeReadback {
            process_id_from_handle: unsafe { GetProcessId(process_handle) },
            process_id_from_thread_handle: unsafe { GetProcessIdOfThread(thread_handle) },
            primary_thread_id_from_handle: unsafe { GetThreadId(thread_handle) },
            process_handle_non_inheritable: !handle_is_inheritable(process_handle)?,
            primary_thread_handle_non_inheritable: !handle_is_inheritable(thread_handle)?,
            is_process_in_exact_job: true,
            exact_process_roster: roster.clone(),
            strict_live_job_readback: true,
        };
        let membership = NativeJobPreResumeMembershipReceipt::from_readback_for_expected_roster(
            &self.job,
            launch,
            ChildBootstrapRole::LifecycleDriver,
            expected_process_id,
            expected_primary_thread_id,
            &expected_roster,
            &readback,
        )?;
        let mut next_roster = self.roster.clone();
        next_roster.observe_additional_pre_resume_root(expected_process_id, &expected_roster)?;
        Ok((membership, next_roster))
    }

    fn start_fault_containment<T>(
        &mut self,
        held_candidate: Option<HeldRootProcess>,
        original_error: SupervisorError,
    ) -> Result<T, SupervisorError> {
        if let Some(candidate) = held_candidate {
            let process_id = candidate.process_id;
            if !self.held_root_processes.contains_key(&process_id) {
                self.quarantined_root_processes
                    .entry(process_id)
                    .or_insert(candidate);
            }
        }
        let result = start_fault_containment_without_candidate(
            &self.job,
            &mut self.admission_faulted,
            &mut self.terminal_state,
            original_error,
        );
        if !matches!(self.terminal_state, NativeJobTerminalState::FaultContaining) {
            return result;
        }
        let deadline =
            Instant::now() + Duration::from_millis(JOB_FAULT_CONTAINMENT_TIMEOUT_MILLIS.into());
        match self.wait_for_fault_containment_processes_until(deadline) {
            Ok(true) => result,
            Ok(false) => Err(SupervisorError::new(
                "authority_native_job_fault_containment_timeout",
            )),
            Err(error) => self.latch_terminal_fault(error),
        }
    }

    fn begin_terminal_drain(&mut self) -> Result<(), SupervisorError> {
        let state = self.terminal_state.clone();
        match state.clone() {
            NativeJobTerminalState::FaultContaining => {
                return Err(SupervisorError::new(
                    "authority_native_job_fault_containment_in_progress",
                ));
            }
            NativeJobTerminalState::FaultHeld => {
                return Err(SupervisorError::new(
                    "authority_native_job_terminal_fault_held",
                ));
            }
            NativeJobTerminalState::Complete(receipt) => {
                return match self.validate_completed_kernel_state(&receipt) {
                    Ok(()) => Ok(()),
                    Err(error) => self.latch_terminal_fault(error),
                };
            }
            NativeJobTerminalState::Open | NativeJobTerminalState::Terminating { .. } => {}
        }
        let termination_requested = matches!(
            &state,
            NativeJobTerminalState::Terminating {
                termination_requested: true,
                ..
            }
        );
        let (root_process_count, root_process_ids_digest, root_process_epochs_digest) =
            match self.validate_held_root_processes() {
                Ok(binding) => binding,
                Err(error) if !termination_requested => {
                    return self.start_fault_containment(None, error);
                }
                Err(error) => return self.latch_terminal_fault(error),
            };
        match state {
            NativeJobTerminalState::Open => {
                if let Err(error) = self.validate_open_kernel_state(root_process_count) {
                    return self.start_fault_containment(None, error);
                }
                self.terminal_state = NativeJobTerminalState::Terminating {
                    root_process_count,
                    root_process_ids_digest,
                    root_process_epochs_digest,
                    termination_requested: false,
                };
                Ok(())
            }
            NativeJobTerminalState::Terminating {
                root_process_count: expected_count,
                root_process_ids_digest: expected_digest,
                root_process_epochs_digest: expected_epochs_digest,
                termination_requested: _,
            } if expected_count == root_process_count
                && expected_digest == root_process_ids_digest
                && expected_epochs_digest == root_process_epochs_digest =>
            {
                Ok(())
            }
            NativeJobTerminalState::Terminating {
                termination_requested: false,
                ..
            } => self.start_fault_containment(
                None,
                SupervisorError::new("authority_native_job_terminal_process_identity_invalid"),
            ),
            NativeJobTerminalState::Terminating {
                termination_requested: true,
                ..
            } => self.latch_terminal_fault(SupervisorError::new(
                "authority_native_job_terminal_process_identity_invalid",
            )),
            NativeJobTerminalState::Complete(_)
            | NativeJobTerminalState::FaultContaining
            | NativeJobTerminalState::FaultHeld => unreachable!(),
        }
    }

    fn request_termination(&mut self) -> Result<(), SupervisorError> {
        self.begin_terminal_drain()?;
        let termination_requested = match &self.terminal_state {
            NativeJobTerminalState::Terminating {
                termination_requested,
                ..
            } => *termination_requested,
            NativeJobTerminalState::Complete(receipt) => {
                let receipt = receipt.clone();
                let result = self.validate_completed_kernel_state(&receipt);
                return match result {
                    Ok(()) => Ok(()),
                    Err(error) => self.latch_terminal_fault(error),
                };
            }
            NativeJobTerminalState::Open
            | NativeJobTerminalState::FaultContaining
            | NativeJobTerminalState::FaultHeld => {
                return self.latch_terminal_fault(SupervisorError::new(
                    "authority_native_job_terminal_state_invalid",
                ));
            }
        };
        if !termination_requested
            && unsafe { TerminateJobObject(self.job.job.raw(), JOB_TERMINATION_EXIT_CODE) } == 0
        {
            return self.latch_terminal_fault(SupervisorError::new(
                "authority_native_job_termination_request_failed",
            ));
        }
        if let NativeJobTerminalState::Terminating {
            termination_requested,
            ..
        } = &mut self.terminal_state
        {
            *termination_requested = true;
            Ok(())
        } else {
            self.latch_terminal_fault(SupervisorError::new(
                "authority_native_job_terminal_state_invalid",
            ))
        }
    }

    fn poll_terminal_drain(
        &mut self,
        timeout_millis: u32,
    ) -> Result<NativeJobTerminalDrainStatus, SupervisorError> {
        if timeout_millis > MAX_JOB_TERMINAL_POLL_MILLIS {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_timeout_invalid",
            ));
        }
        if matches!(self.terminal_state, NativeJobTerminalState::FaultContaining) {
            return self.poll_admission_fault_containment(timeout_millis);
        }
        let (
            root_process_count,
            root_process_ids_digest,
            root_process_epochs_digest,
            termination_requested,
        ) = match &self.terminal_state {
            NativeJobTerminalState::Open => {
                return Err(SupervisorError::new(
                    "authority_native_job_terminal_not_started",
                ));
            }
            NativeJobTerminalState::FaultHeld => {
                return Err(SupervisorError::new(
                    "authority_native_job_terminal_fault_held",
                ));
            }
            NativeJobTerminalState::FaultContaining => unreachable!(),
            NativeJobTerminalState::Complete(receipt) => {
                let receipt = receipt.clone();
                if let Err(error) = self.validate_completed_kernel_state(&receipt) {
                    return self.latch_terminal_fault(error);
                }
                return Ok(NativeJobTerminalDrainStatus::Complete(receipt));
            }
            NativeJobTerminalState::Terminating {
                root_process_count,
                root_process_ids_digest,
                root_process_epochs_digest,
                termination_requested,
            } => (
                *root_process_count,
                *root_process_ids_digest,
                *root_process_epochs_digest,
                *termination_requested,
            ),
        };
        match self.validate_held_root_processes() {
            Ok((count, ids_digest, epochs_digest))
                if count == root_process_count
                    && ids_digest == root_process_ids_digest
                    && epochs_digest == root_process_epochs_digest => {}
            Ok(_) if !termination_requested => {
                return self.start_fault_containment(
                    None,
                    SupervisorError::new("authority_native_job_terminal_process_identity_invalid"),
                );
            }
            Ok(_) => {
                return self.latch_terminal_fault(SupervisorError::new(
                    "authority_native_job_terminal_process_identity_invalid",
                ));
            }
            Err(error) if !termination_requested => {
                return self.start_fault_containment(None, error);
            }
            Err(error) => return self.latch_terminal_fault(error),
        }
        if !termination_requested {
            return Ok(NativeJobTerminalDrainStatus::Pending);
        }

        if let Err(error) = self.drain_terminal_completion_messages() {
            return self.latch_terminal_fault(error);
        }
        let deadline = Instant::now() + Duration::from_millis(u64::from(timeout_millis));
        let all_roots_signaled = match self.wait_for_held_roots_until(deadline) {
            Ok(signaled) => signaled,
            Err(error) => return self.latch_terminal_fault(error),
        };
        let completion_port_drained = match self.drain_terminal_completion_messages() {
            Ok(drained) => drained,
            Err(error) => return self.latch_terminal_fault(error),
        };
        if !all_roots_signaled || !completion_port_drained {
            return Ok(NativeJobTerminalDrainStatus::Pending);
        }
        let first_terminal_process_readback = match root_process_terminal_readback_digest(
            &self.held_root_processes,
            &self.root_memberships,
        ) {
            Ok(digest) => digest,
            Err(error) => return self.latch_terminal_fault(error),
        };
        let first_readback = match self.query_stable_terminal_snapshot(root_process_count) {
            Ok(Some(readback)) => readback,
            Ok(None) => return Ok(NativeJobTerminalDrainStatus::Pending),
            Err(error) => return self.latch_terminal_fault(error),
        };
        let boundary_drained = match self.drain_terminal_completion_messages() {
            Ok(drained) => drained,
            Err(error) => return self.latch_terminal_fault(error),
        };
        if !boundary_drained {
            return Ok(NativeJobTerminalDrainStatus::Pending);
        }
        let second_readback = match self.query_stable_terminal_snapshot(root_process_count) {
            Ok(Some(readback)) => readback,
            Ok(None) => return Ok(NativeJobTerminalDrainStatus::Pending),
            Err(error) => return self.latch_terminal_fault(error),
        };
        if first_readback != second_readback {
            return self.latch_terminal_fault(SupervisorError::new(
                "authority_native_job_terminal_snapshot_drift",
            ));
        }
        let second_terminal_process_readback = match root_process_terminal_readback_digest(
            &self.held_root_processes,
            &self.root_memberships,
        ) {
            Ok(digest) => digest,
            Err(error) => return self.latch_terminal_fault(error),
        };
        if first_terminal_process_readback != second_terminal_process_readback {
            return self.latch_terminal_fault(SupervisorError::new(
                "authority_native_job_terminal_process_readback_drift",
            ));
        }
        let final_boundary_drained = match self.drain_terminal_completion_messages() {
            Ok(drained) => drained,
            Err(error) => return self.latch_terminal_fault(error),
        };
        if !final_boundary_drained {
            return Ok(NativeJobTerminalDrainStatus::Pending);
        }
        let final_terminal_process_readback = match root_process_terminal_readback_digest(
            &self.held_root_processes,
            &self.root_memberships,
        ) {
            Ok(digest) => digest,
            Err(error) => return self.latch_terminal_fault(error),
        };
        if second_terminal_process_readback != final_terminal_process_readback {
            return self.latch_terminal_fault(SupervisorError::new(
                "authority_native_job_terminal_process_readback_drift",
            ));
        }
        self.finish_terminal_drain(
            root_process_count,
            root_process_ids_digest,
            root_process_epochs_digest,
            final_terminal_process_readback,
            second_readback,
        )
    }

    pub(super) fn into_terminal_proof(
        mut self,
    ) -> Result<NativeJobTerminalProof, NativeJobTerminalDrainFailure> {
        if let Err(error) = self.request_termination() {
            return Err(NativeJobTerminalDrainFailure { job: self, error });
        }
        for _ in 0..JOB_TERMINAL_DRAIN_POLL_ATTEMPTS {
            match self.poll_terminal_drain(MAX_JOB_TERMINAL_POLL_MILLIS) {
                Ok(NativeJobTerminalDrainStatus::Pending) => continue,
                Ok(NativeJobTerminalDrainStatus::Complete(receipt)) => {
                    if let Err(error) = self.validate_completed_kernel_state(&receipt) {
                        self.terminal_state = NativeJobTerminalState::FaultHeld;
                        return Err(NativeJobTerminalDrainFailure { job: self, error });
                    }
                    let WindowsNativeActiveJob { job, .. } = self;
                    let WindowsNativeJob {
                        terminal_proof_key, ..
                    } = job;
                    return Ok(NativeJobTerminalProof {
                        receipt,
                        key: terminal_proof_key,
                    });
                }
                Err(error) => {
                    return Err(NativeJobTerminalDrainFailure { job: self, error });
                }
            }
        }
        self.terminal_state = NativeJobTerminalState::FaultHeld;
        Err(NativeJobTerminalDrainFailure {
            job: self,
            error: SupervisorError::new("authority_native_job_terminal_drain_timeout"),
        })
    }

    fn validate_held_root_processes(&self) -> Result<(u32, Digest, Digest), SupervisorError> {
        if self.root_memberships.len() < MIN_TERMINAL_ROOT_PROCESS_COUNT
            || self.root_memberships.len() > FIXED_ROOT_PROCESS_COUNT
            || self.held_root_processes.len() != self.root_memberships.len()
            || self
                .root_memberships
                .keys()
                .ne(self.held_root_processes.keys())
        {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_process_identity_invalid",
            ));
        }
        for process in self.held_root_processes.values() {
            process.validate(&self.job.job)?;
        }
        let bridge_count = self
            .root_memberships
            .values()
            .filter(|membership| membership.role == ChildBootstrapRole::BridgeLauncher)
            .count();
        let driver_count = self
            .root_memberships
            .values()
            .filter(|membership| membership.role == ChildBootstrapRole::LifecycleDriver)
            .count();
        if bridge_count != 1
            || bridge_count + driver_count != self.root_memberships.len()
            || driver_count > 1
        {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_process_identity_invalid",
            ));
        }
        let expected_root_process_ids = self
            .root_memberships
            .keys()
            .copied()
            .collect::<BTreeSet<_>>();
        let root_process_count = u32::try_from(expected_root_process_ids.len())
            .map_err(|_| SupervisorError::new("authority_native_job_roster_invalid"))?;
        Ok((
            root_process_count,
            root_process_ids_digest(&expected_root_process_ids)?,
            root_process_epochs_digest(&self.held_root_processes)?,
        ))
    }

    fn validate_open_kernel_state(&self, root_process_count: u32) -> Result<(), SupervisorError> {
        let readback_a =
            query_readback(&self.job.job, &self.job.completion_port, &self.job.security)?;
        let roster = query_process_roster(&self.job.job)?;
        let readback_b =
            query_readback(&self.job.job, &self.job.completion_port, &self.job.security)?;
        readback_a.validate_strict_live_job(&self.job.security, root_process_count)?;
        readback_b.validate_strict_live_job(&self.job.security, root_process_count)?;
        let expected_roster = self
            .held_root_processes
            .keys()
            .copied()
            .collect::<BTreeSet<_>>();
        if readback_a != readback_b || roster != expected_roster {
            return Err(SupervisorError::new(
                "authority_native_job_roster_readback_invalid",
            ));
        }
        Ok(())
    }

    fn validate_fault_containment_processes(&self) -> Result<u32, SupervisorError> {
        if !self.admission_faulted
            || self.root_memberships.len() < MIN_TERMINAL_ROOT_PROCESS_COUNT
            || self.root_memberships.len() > FIXED_ROOT_PROCESS_COUNT
            || self.held_root_processes.len() != self.root_memberships.len()
            || self
                .root_memberships
                .keys()
                .ne(self.held_root_processes.keys())
            || self.quarantined_root_processes.len() > 1
            || self
                .held_root_processes
                .keys()
                .any(|process_id| self.quarantined_root_processes.contains_key(process_id))
        {
            return Err(SupervisorError::new(
                "authority_native_job_fault_containment_invalid",
            ));
        }
        for (process_id, process) in self
            .held_root_processes
            .iter()
            .chain(self.quarantined_root_processes.iter())
        {
            if *process_id != process.process_id {
                return Err(SupervisorError::new(
                    "authority_native_job_fault_containment_invalid",
                ));
            }
            process.validate(&self.job.job)?;
        }
        let process_count = self
            .held_root_processes
            .len()
            .checked_add(self.quarantined_root_processes.len())
            .and_then(|count| u32::try_from(count).ok())
            .ok_or_else(|| {
                SupervisorError::new("authority_native_job_fault_containment_invalid")
            })?;
        if process_count < MIN_TERMINAL_ROOT_PROCESS_COUNT as u32
            || process_count > FIXED_ROOT_PROCESS_COUNT as u32
        {
            return Err(SupervisorError::new(
                "authority_native_job_fault_containment_invalid",
            ));
        }
        Ok(process_count)
    }

    fn poll_admission_fault_containment(
        &mut self,
        timeout_millis: u32,
    ) -> Result<NativeJobTerminalDrainStatus, SupervisorError> {
        let known_process_count = match self.validate_fault_containment_processes() {
            Ok(process_count) => process_count,
            Err(error) => return self.latch_terminal_fault(error),
        };
        let deadline = Instant::now() + Duration::from_millis(u64::from(timeout_millis));
        let all_known_processes_signaled =
            match self.wait_for_fault_containment_processes_until(deadline) {
                Ok(signaled) => signaled,
                Err(error) => return self.latch_terminal_fault(error),
            };
        if !all_known_processes_signaled {
            return Ok(NativeJobTerminalDrainStatus::Pending);
        }
        let readback_a =
            match query_readback(&self.job.job, &self.job.completion_port, &self.job.security) {
                Ok(readback) => readback,
                Err(error) => return self.latch_terminal_fault(error),
            };
        let roster = match query_process_roster(&self.job.job) {
            Ok(roster) => roster,
            Err(error) => return self.latch_terminal_fault(error),
        };
        let readback_b =
            match query_readback(&self.job.job, &self.job.completion_port, &self.job.security) {
                Ok(readback) => readback,
                Err(error) => return self.latch_terminal_fault(error),
            };
        match classify_fault_containment_snapshot(
            &self.job.security,
            known_process_count,
            readback_a,
            &roster,
            readback_b,
        ) {
            Ok(Some(_)) => {
                self.terminal_state = NativeJobTerminalState::FaultHeld;
                Err(SupervisorError::new("authority_native_job_fault_contained"))
            }
            Ok(None) => Ok(NativeJobTerminalDrainStatus::Pending),
            Err(error) => self.latch_terminal_fault(error),
        }
    }

    fn drain_terminal_completion_messages(&mut self) -> Result<bool, SupervisorError> {
        for _ in 0..MAX_JOB_COMPLETION_EVENTS {
            let Some(message) = dequeue_completion_message(
                &self.job.completion_port,
                self.job.receipt.object_id,
                0,
            )?
            else {
                return Ok(true);
            };
            self.roster.observe_terminal_advisory(message)?;
        }
        Ok(false)
    }

    fn wait_for_held_roots_until(&self, deadline: Instant) -> Result<bool, SupervisorError> {
        for process in self.held_root_processes.values() {
            if process.is_signaled()? {
                continue;
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            let wait_millis = u32::try_from(remaining.as_millis())
                .unwrap_or(MAX_JOB_TERMINAL_POLL_MILLIS)
                .min(MAX_JOB_TERMINAL_POLL_MILLIS);
            match unsafe { WaitForSingleObject(process.handle.raw(), wait_millis) } {
                WAIT_OBJECT_0 => {}
                WAIT_TIMEOUT => return Ok(false),
                WAIT_FAILED => {
                    return Err(SupervisorError::new(
                        "authority_native_job_terminal_process_wait_failed",
                    ));
                }
                _ => {
                    return Err(SupervisorError::new(
                        "authority_native_job_terminal_process_wait_invalid",
                    ));
                }
            }
        }
        Ok(true)
    }

    fn wait_for_fault_containment_processes_until(
        &self,
        deadline: Instant,
    ) -> Result<bool, SupervisorError> {
        for process in self
            .held_root_processes
            .values()
            .chain(self.quarantined_root_processes.values())
        {
            if process.is_signaled()? {
                continue;
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            let wait_millis = u32::try_from(remaining.as_millis())
                .unwrap_or(MAX_JOB_TERMINAL_POLL_MILLIS)
                .min(MAX_JOB_TERMINAL_POLL_MILLIS);
            match unsafe { WaitForSingleObject(process.handle.raw(), wait_millis) } {
                WAIT_OBJECT_0 => {}
                WAIT_TIMEOUT => return Ok(false),
                WAIT_FAILED => {
                    return Err(SupervisorError::new(
                        "authority_native_job_terminal_process_wait_failed",
                    ));
                }
                _ => {
                    return Err(SupervisorError::new(
                        "authority_native_job_terminal_process_wait_invalid",
                    ));
                }
            }
        }
        Ok(true)
    }

    fn all_held_roots_signaled(&self) -> Result<bool, SupervisorError> {
        for process in self.held_root_processes.values() {
            if !process.is_signaled()? {
                return Ok(false);
            }
        }
        Ok(true)
    }

    fn query_stable_terminal_snapshot(
        &self,
        root_process_count: u32,
    ) -> Result<Option<NativeJobReadback>, SupervisorError> {
        let readback_a =
            query_readback(&self.job.job, &self.job.completion_port, &self.job.security)?;
        let roster = query_process_roster(&self.job.job)?;
        let readback_b =
            query_readback(&self.job.job, &self.job.completion_port, &self.job.security)?;
        classify_terminal_snapshot(
            &self.job.security,
            root_process_count,
            readback_a,
            &roster,
            readback_b,
        )
    }

    fn validate_completed_kernel_state(
        &self,
        receipt: &NativeJobTerminalDrainReceipt,
    ) -> Result<(), SupervisorError> {
        receipt.validate_for_job(&self.job)?;
        let (root_process_count, root_process_ids_digest, root_process_epochs_digest) =
            self.validate_held_root_processes()?;
        let expected_root_process_ids = self
            .held_root_processes
            .keys()
            .copied()
            .collect::<BTreeSet<_>>();
        self.roster
            .validate_terminal_transcript(&expected_root_process_ids)?;
        let first_terminal_process_readback = root_process_terminal_readback_digest(
            &self.held_root_processes,
            &self.root_memberships,
        )?;
        if root_process_count != receipt.root_process_count
            || root_process_ids_digest != receipt.root_process_ids_digest
            || root_process_epochs_digest != receipt.root_process_epochs_digest
            || first_terminal_process_readback != receipt.root_process_terminal_readback_digest
            || self.roster.new_process_events != receipt.new_process_events
            || self.roster.exit_process_events != receipt.exit_process_events
            || self.roster.abnormal_exit_process_events != receipt.abnormal_exit_process_events
            || self.roster.completion_message_count != receipt.completion_message_count
            || self.roster.completion_transcript_digest != receipt.completion_transcript_digest
            || self.roster.active_process_zero_observed != receipt.active_process_zero_observed
            || !self.all_held_roots_signaled()?
        {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_process_identity_invalid",
            ));
        }
        let first_readback = self
            .query_stable_terminal_snapshot(root_process_count)?
            .ok_or_else(|| SupervisorError::new("authority_native_job_completed_state_unstable"))?;
        if dequeue_completion_message(&self.job.completion_port, self.job.receipt.object_id, 0)?
            .is_some()
        {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_message_boundary_invalid",
            ));
        }
        let second_readback = self
            .query_stable_terminal_snapshot(root_process_count)?
            .ok_or_else(|| SupervisorError::new("authority_native_job_completed_state_unstable"))?;
        let second_terminal_process_readback = root_process_terminal_readback_digest(
            &self.held_root_processes,
            &self.root_memberships,
        )?;
        if dequeue_completion_message(&self.job.completion_port, self.job.receipt.object_id, 0)?
            .is_some()
        {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_message_boundary_invalid",
            ));
        }
        if first_readback != second_readback
            || second_readback.total_processes != receipt.total_processes
            || first_terminal_process_readback != second_terminal_process_readback
        {
            return Err(SupervisorError::new(
                "authority_native_job_terminal_process_count_invalid",
            ));
        }
        Ok(())
    }

    fn finish_terminal_drain(
        &mut self,
        root_process_count: u32,
        root_process_ids_digest: Digest,
        root_process_epochs_digest: Digest,
        root_process_terminal_readback_digest: Digest,
        readback: NativeJobReadback,
    ) -> Result<NativeJobTerminalDrainStatus, SupervisorError> {
        let expected_root_process_ids = self
            .held_root_processes
            .keys()
            .copied()
            .collect::<BTreeSet<_>>();
        if let Err(error) = self
            .roster
            .validate_terminal_transcript(&expected_root_process_ids)
        {
            return self.latch_terminal_fault(error);
        }
        let mut receipt = NativeJobTerminalDrainReceipt {
            object_id: self.job.receipt.object_id,
            deterministic_name_digest: self.job.receipt.deterministic_name_digest,
            created_at: self.job.receipt.created_at,
            authority_generation_digest: self.job.security.authority_generation_digest,
            run_binding_digest: self.job.run_binding_digest,
            security_binding_digest: self.job.security.binding_digest,
            root_process_count,
            root_process_ids_digest,
            root_process_epochs_digest,
            root_process_terminal_readback_digest,
            total_processes: readback.total_processes,
            new_process_events: self.roster.new_process_events,
            exit_process_events: self.roster.exit_process_events,
            abnormal_exit_process_events: self.roster.abnormal_exit_process_events,
            completion_message_count: self.roster.completion_message_count,
            completion_transcript_digest: self.roster.completion_transcript_digest,
            active_process_zero_observed: self.roster.active_process_zero_observed,
            completion_port_drained: true,
            exact_empty_roster_readback: true,
            active_processes_zero: readback.active_processes == 0,
            accounting_snapshot_stable: true,
            all_root_process_handles_signaled: true,
            all_root_process_handles_non_inheritable: true,
            receipt_mac: [0; 32],
        };
        receipt.receipt_mac = receipt.derive_mac(&self.job.terminal_proof_key);
        if let Err(error) = receipt.validate_for_job(&self.job) {
            return self.latch_terminal_fault(error);
        }
        self.terminal_state = NativeJobTerminalState::Complete(receipt.clone());
        Ok(NativeJobTerminalDrainStatus::Complete(receipt))
    }

    fn latch_terminal_fault<T>(&mut self, error: SupervisorError) -> Result<T, SupervisorError> {
        self.terminal_state = NativeJobTerminalState::FaultHeld;
        Err(error)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ChildJobObservationReadback {
    limit_flags: u32,
    active_process_limit: u32,
    total_processes: u32,
    active_processes: u32,
    total_terminated_processes: u32,
}

fn query_child_job_observation(
    job: &OwnedKernelHandle,
) -> Result<ChildJobObservationReadback, SupervisorError> {
    let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
    let mut returned = 0u32;
    if unsafe {
        QueryInformationJobObject(
            job.raw(),
            JobObjectExtendedLimitInformation,
            (&mut limits as *mut JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            &mut returned,
        )
    } == 0
        || returned as usize != size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>()
    {
        return Err(SupervisorError::new(
            "authority_native_child_job_observation_unavailable",
        ));
    }
    let mut accounting: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = unsafe { zeroed() };
    returned = 0;
    if unsafe {
        QueryInformationJobObject(
            job.raw(),
            JobObjectBasicAccountingInformation,
            (&mut accounting as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
            size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
            &mut returned,
        )
    } == 0
        || returned as usize != size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>()
    {
        return Err(SupervisorError::new(
            "authority_native_child_job_observation_unavailable",
        ));
    }
    Ok(ChildJobObservationReadback {
        limit_flags: limits.BasicLimitInformation.LimitFlags,
        active_process_limit: limits.BasicLimitInformation.ActiveProcessLimit,
        total_processes: accounting.TotalProcesses,
        active_processes: accounting.ActiveProcesses,
        total_terminated_processes: accounting.TotalTerminatedProcesses,
    })
}

fn start_fault_containment_without_candidate<T>(
    job: &WindowsNativeJob,
    admission_faulted: &mut bool,
    terminal_state: &mut NativeJobTerminalState,
    original_error: SupervisorError,
) -> Result<T, SupervisorError> {
    *admission_faulted = true;
    if unsafe { TerminateJobObject(job.job.raw(), JOB_TERMINATION_EXIT_CODE) } == 0 {
        *terminal_state = NativeJobTerminalState::FaultHeld;
        return Err(SupervisorError::new(
            "authority_native_job_termination_request_failed",
        ));
    }
    *terminal_state = NativeJobTerminalState::FaultContaining;
    Err(original_error)
}

fn terminate_unproven_suspended_process(process: HANDLE) -> Result<bool, SupervisorError> {
    if process.is_null() || process == INVALID_HANDLE_VALUE || unsafe { GetProcessId(process) } == 0
    {
        return Err(SupervisorError::new(
            "authority_native_job_unproven_process_handle_invalid",
        ));
    }
    match unsafe { WaitForSingleObject(process, 0) } {
        WAIT_OBJECT_0 => return Ok(false),
        WAIT_TIMEOUT => {}
        _ => {
            return Err(SupervisorError::new(
                "authority_native_job_unproven_process_wait_invalid",
            ));
        }
    }
    if unsafe { TerminateProcess(process, JOB_TERMINATION_EXIT_CODE) } == 0 {
        return Err(SupervisorError::new(
            "authority_native_job_unproven_process_termination_failed",
        ));
    }
    if unsafe { WaitForSingleObject(process, JOB_FAULT_CONTAINMENT_TIMEOUT_MILLIS) }
        != WAIT_OBJECT_0
    {
        return Err(SupervisorError::new(
            "authority_native_job_unproven_process_containment_timeout",
        ));
    }
    Ok(true)
}

fn query_readback(
    job: &OwnedKernelHandle,
    completion_port: &OwnedKernelHandle,
    security: &NativeJobSecuritySpec,
) -> Result<NativeJobReadback, SupervisorError> {
    let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
    let mut returned = 0u32;
    if unsafe {
        QueryInformationJobObject(
            job.raw(),
            JobObjectExtendedLimitInformation,
            &mut limits as *mut _ as *mut c_void,
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            &mut returned,
        )
    } == 0
        || returned as usize != size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>()
    {
        return Err(SupervisorError::new(
            "authority_native_job_limit_readback_failed",
        ));
    }

    let mut accounting: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = unsafe { zeroed() };
    returned = 0;
    if unsafe {
        QueryInformationJobObject(
            job.raw(),
            JobObjectBasicAccountingInformation,
            &mut accounting as *mut _ as *mut c_void,
            size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
            &mut returned,
        )
    } == 0
        || returned as usize != size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>()
    {
        return Err(SupervisorError::new(
            "authority_native_job_accounting_readback_failed",
        ));
    }

    Ok(NativeJobReadback {
        limit_flags: limits.BasicLimitInformation.LimitFlags,
        active_process_limit: limits.BasicLimitInformation.ActiveProcessLimit,
        total_processes: accounting.TotalProcesses,
        active_processes: accounting.ActiveProcesses,
        job_handle_inheritable: job.is_inheritable()?,
        completion_port_handle_inheritable: completion_port.is_inheritable()?,
        security: query_security_readback(job, security)?,
    })
}

fn classify_terminal_snapshot(
    security: &NativeJobSecuritySpec,
    root_process_count: u32,
    readback_a: NativeJobReadback,
    roster: &BTreeSet<u32>,
    readback_b: NativeJobReadback,
) -> Result<Option<NativeJobReadback>, SupervisorError> {
    readback_a.validate_strict_terminating_job(security, root_process_count)?;
    readback_b.validate_strict_terminating_job(security, root_process_count)?;
    if readback_a.limit_flags != readback_b.limit_flags
        || readback_a.active_process_limit != readback_b.active_process_limit
        || readback_a.job_handle_inheritable != readback_b.job_handle_inheritable
        || readback_a.completion_port_handle_inheritable
            != readback_b.completion_port_handle_inheritable
        || readback_a.security != readback_b.security
    {
        return Err(SupervisorError::new(
            "authority_native_job_readback_invalid",
        ));
    }
    if readback_a.total_processes != readback_b.total_processes
        || readback_a.active_processes != readback_b.active_processes
        || roster.len() != readback_b.active_processes as usize
    {
        return Ok(None);
    }
    if readback_b.total_processes > root_process_count {
        return Err(SupervisorError::new(
            "authority_native_job_descendant_handle_registry_unavailable",
        ));
    }
    if readback_b.active_processes != 0 || !roster.is_empty() {
        return Ok(None);
    }
    readback_b.validate_strict_terminal_job(security)?;
    if readback_b.total_processes != root_process_count {
        return Err(SupervisorError::new(
            "authority_native_job_terminal_process_count_invalid",
        ));
    }
    Ok(Some(readback_b))
}

fn classify_fault_containment_snapshot(
    security: &NativeJobSecuritySpec,
    known_process_count: u32,
    readback_a: NativeJobReadback,
    roster: &BTreeSet<u32>,
    readback_b: NativeJobReadback,
) -> Result<Option<NativeJobReadback>, SupervisorError> {
    readback_a.validate_strict_terminating_job(security, known_process_count)?;
    readback_b.validate_strict_terminating_job(security, known_process_count)?;
    if readback_a.limit_flags != readback_b.limit_flags
        || readback_a.active_process_limit != readback_b.active_process_limit
        || readback_a.job_handle_inheritable != readback_b.job_handle_inheritable
        || readback_a.completion_port_handle_inheritable
            != readback_b.completion_port_handle_inheritable
        || readback_a.security != readback_b.security
    {
        return Err(SupervisorError::new(
            "authority_native_job_readback_invalid",
        ));
    }
    if readback_a.total_processes != readback_b.total_processes
        || readback_a.active_processes != readback_b.active_processes
        || roster.len() != readback_b.active_processes as usize
    {
        return Ok(None);
    }
    if readback_b.active_processes != 0 || !roster.is_empty() {
        return Ok(None);
    }
    readback_b.validate_strict_terminal_job(security)?;
    Ok(Some(readback_b))
}

fn query_process_roster(job: &OwnedKernelHandle) -> Result<BTreeSet<u32>, SupervisorError> {
    let header_length = size_of::<u32>()
        .checked_mul(2)
        .ok_or_else(|| SupervisorError::new("authority_native_job_roster_readback_invalid"))?;
    let byte_length = size_of::<JOBOBJECT_BASIC_PROCESS_ID_LIST>()
        .checked_add(
            (MAX_JOB_ROSTER_PROCESSES - 1)
                .checked_mul(size_of::<usize>())
                .ok_or_else(|| {
                    SupervisorError::new("authority_native_job_roster_readback_invalid")
                })?,
        )
        .ok_or_else(|| SupervisorError::new("authority_native_job_roster_readback_invalid"))?;
    let word_size = size_of::<usize>();
    let word_count = byte_length
        .checked_add(word_size - 1)
        .ok_or_else(|| SupervisorError::new("authority_native_job_roster_readback_invalid"))?
        / word_size;
    let mut buffer = vec![0usize; word_count];
    let mut returned = 0u32;
    if unsafe {
        QueryInformationJobObject(
            job.raw(),
            JobObjectBasicProcessIdList,
            buffer.as_mut_ptr().cast(),
            byte_length as u32,
            &mut returned,
        )
    } == 0
        || returned < header_length as u32
        || returned as usize > byte_length
    {
        return Err(SupervisorError::new(
            "authority_native_job_roster_readback_invalid",
        ));
    }
    let list = unsafe { &*buffer.as_ptr().cast::<JOBOBJECT_BASIC_PROCESS_ID_LIST>() };
    let count = list.NumberOfProcessIdsInList as usize;
    let assigned = list.NumberOfAssignedProcesses as usize;
    let returned_for_roster =
        header_length
            .checked_add(count.checked_mul(size_of::<usize>()).ok_or_else(|| {
                SupervisorError::new("authority_native_job_roster_readback_invalid")
            })?)
            .ok_or_else(|| SupervisorError::new("authority_native_job_roster_readback_invalid"))?;
    if count > MAX_JOB_ROSTER_PROCESSES
        || assigned != count
        || assigned > MAX_JOB_COMPLETION_EVENTS / 2
        || (returned as usize) < returned_for_roster
    {
        return Err(SupervisorError::new(
            "authority_native_job_roster_readback_invalid",
        ));
    }
    let process_ids = unsafe { std::slice::from_raw_parts(list.ProcessIdList.as_ptr(), count) };
    let mut roster = BTreeSet::new();
    for process_id in process_ids {
        let process_id = u32::try_from(*process_id)
            .map_err(|_| SupervisorError::new("authority_native_job_roster_readback_invalid"))?;
        if process_id == 0 || !roster.insert(process_id) {
            return Err(SupervisorError::new(
                "authority_native_job_roster_readback_invalid",
            ));
        }
    }
    Ok(roster)
}

fn dequeue_completion_message(
    completion_port: &OwnedKernelHandle,
    expected_completion_key: u64,
    timeout_millis: u32,
) -> Result<Option<NativeJobCompletionMessage>, SupervisorError> {
    if timeout_millis == u32::MAX {
        return Err(SupervisorError::new(
            "authority_native_job_completion_timeout_invalid",
        ));
    }
    let expected_completion_key = usize::try_from(expected_completion_key)
        .map_err(|_| SupervisorError::new("authority_native_job_object_id_unsupported"))?;
    let mut message_code = 0u32;
    let mut completion_key = 0usize;
    let mut overlapped = null_mut::<OVERLAPPED>();
    unsafe {
        SetLastError(ERROR_SUCCESS);
    }
    let success = unsafe {
        GetQueuedCompletionStatus(
            completion_port.raw(),
            &mut message_code,
            &mut completion_key,
            &mut overlapped,
            timeout_millis,
        )
    };
    if success == 0 {
        if overlapped.is_null() && unsafe { GetLastError() } == WAIT_TIMEOUT {
            return Ok(None);
        }
        return Err(SupervisorError::new(
            "authority_native_job_completion_readback_failed",
        ));
    }
    if completion_key != expected_completion_key {
        return Err(SupervisorError::new(
            "authority_native_job_completion_key_mismatch",
        ));
    }
    let kind = match message_code {
        JOB_OBJECT_MSG_NEW_PROCESS => NativeJobCompletionKind::NewProcess,
        JOB_OBJECT_MSG_EXIT_PROCESS => NativeJobCompletionKind::ExitProcess,
        JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS => NativeJobCompletionKind::AbnormalExitProcess,
        JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO => NativeJobCompletionKind::ActiveProcessZero,
        _ => {
            return Err(SupervisorError::new(
                "authority_native_job_completion_message_invalid",
            ));
        }
    };
    let process_id = if kind == NativeJobCompletionKind::ActiveProcessZero {
        if !overlapped.is_null() {
            return Err(SupervisorError::new(
                "authority_native_job_completion_message_invalid",
            ));
        }
        None
    } else {
        let process_id = u32::try_from(overlapped as usize)
            .map_err(|_| SupervisorError::new("authority_native_job_completion_message_invalid"))?;
        if process_id == 0 {
            return Err(SupervisorError::new(
                "authority_native_job_completion_message_invalid",
            ));
        }
        Some(process_id)
    };
    Ok(Some(NativeJobCompletionMessage { kind, process_id }))
}

fn query_security_readback(
    job: &OwnedKernelHandle,
    spec: &NativeJobSecuritySpec,
) -> Result<NativeJobSecurityReadback, SupervisorError> {
    let mut descriptor = null_mut();
    let status = unsafe {
        GetSecurityInfo(
            job.raw(),
            SE_KERNEL_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            null_mut(),
            null_mut(),
            null_mut(),
            null_mut(),
            &mut descriptor,
        )
    };
    if status != ERROR_SUCCESS || descriptor.is_null() {
        if !descriptor.is_null() {
            unsafe {
                LocalFree(descriptor);
            }
        }
        return Err(SupervisorError::new(
            "authority_native_job_security_readback_failed",
        ));
    }
    let descriptor = OwnedSecurityDescriptor(descriptor);
    verify_security_descriptor(descriptor.raw(), spec)
}

fn verify_security_descriptor(
    descriptor: PSECURITY_DESCRIPTOR,
    spec: &NativeJobSecuritySpec,
) -> Result<NativeJobSecurityReadback, SupervisorError> {
    spec.validate()?;
    if descriptor.is_null() {
        return Err(SupervisorError::new(
            "authority_native_job_security_readback_invalid",
        ));
    }

    let expected_owner = OwnedSid::from_text(&spec.owner_sid)?;
    let expected_system = OwnedSid::from_text(LOCAL_SYSTEM_SID)?;
    let expected_service = OwnedSid::from_text(&spec.service_sid)?;

    let mut owner = null_mut();
    let mut owner_defaulted = 0;
    if unsafe { GetSecurityDescriptorOwner(descriptor, &mut owner, &mut owner_defaulted) } == 0
        || owner.is_null()
        || owner_defaulted != 0
        || unsafe { IsValidSid(owner) } == 0
        || unsafe { EqualSid(owner, expected_owner.raw()) } == 0
    {
        return Err(SupervisorError::new(
            "authority_native_job_security_readback_invalid",
        ));
    }

    let mut control = 0u16;
    let mut descriptor_revision = 0u32;
    if unsafe { GetSecurityDescriptorControl(descriptor, &mut control, &mut descriptor_revision) }
        == 0
        || descriptor_revision != SDDL_REVISION_1
        || control & SE_DACL_PROTECTED == 0
    {
        return Err(SupervisorError::new(
            "authority_native_job_security_readback_invalid",
        ));
    }

    let mut dacl_present = 0;
    let mut dacl_defaulted = 0;
    let mut dacl = null_mut();
    if unsafe {
        GetSecurityDescriptorDacl(
            descriptor,
            &mut dacl_present,
            &mut dacl,
            &mut dacl_defaulted,
        )
    } == 0
        || dacl_present == 0
        || dacl_defaulted != 0
        || dacl.is_null()
    {
        return Err(SupervisorError::new(
            "authority_native_job_security_readback_invalid",
        ));
    }

    let header = unsafe { &*dacl };
    if header.AclRevision != ACL_REVISION as u8
        || header.Sbz1 != 0
        || header.AceCount != 2
        || header.Sbz2 != 0
    {
        return Err(SupervisorError::new(
            "authority_native_job_security_readback_invalid",
        ));
    }

    let system_ace =
        access_allowed_ace(dacl, 0, JOB_OBJECT_ALL_ACCESS_EXACT, expected_system.raw())?;
    let service_ace =
        access_allowed_ace(dacl, 1, SERVICE_JOB_ACCESS_EXACT, expected_service.raw())?;
    let expected_acl_size = size_of::<ACL>()
        .checked_add(system_ace.size as usize)
        .and_then(|value| value.checked_add(service_ace.size as usize))
        .ok_or_else(|| SupervisorError::new("authority_native_job_security_readback_invalid"))?;
    if header.AclSize as usize != expected_acl_size
        || system_ace.mask & GENERIC_ACCESS_MASK != 0
        || service_ace.mask & GENERIC_ACCESS_MASK != 0
        || service_ace.mask & MUTATING_CONTROL_ACCESS_MASK != 0
    {
        return Err(SupervisorError::new(
            "authority_native_job_security_readback_invalid",
        ));
    }

    let binding_digest = security_binding_digest(
        &spec.authority_generation_digest,
        &spec.authority_identity_digest,
        owner,
        system_ace.sid,
        service_ace.sid,
        header.AclRevision,
        control & SE_DACL_PROTECTED,
        system_ace.mask,
        service_ace.mask,
    )?;
    let readback = NativeJobSecurityReadback {
        binding_digest,
        owner_exact: true,
        owner_local_system: spec.owner_sid == LOCAL_SYSTEM_SID,
        dacl_present: true,
        dacl_defaulted: false,
        dacl_protected: true,
        ace_count: header.AceCount,
        system_access_mask: system_ace.mask,
        service_access_mask: service_ace.mask,
    };
    readback.validate(spec)?;
    Ok(readback)
}

#[derive(Clone, Copy)]
struct AccessAllowedAceReadback {
    mask: u32,
    size: u16,
    sid: PSID,
}

fn access_allowed_ace(
    dacl: *mut ACL,
    index: u32,
    expected_mask: u32,
    expected_sid: PSID,
) -> Result<AccessAllowedAceReadback, SupervisorError> {
    let mut raw_ace = null_mut();
    if unsafe { GetAce(dacl, index, &mut raw_ace) } == 0 || raw_ace.is_null() {
        return Err(SupervisorError::new(
            "authority_native_job_security_readback_invalid",
        ));
    }
    let ace = unsafe { &*raw_ace.cast::<ACCESS_ALLOWED_ACE>() };
    let sid = std::ptr::addr_of!(ace.SidStart).cast_mut().cast::<c_void>();
    if ace.Header.AceType != ACCESS_ALLOWED_ACE_TYPE as u8
        || ace.Header.AceFlags != 0
        || ace.Mask != expected_mask
        || sid.is_null()
        || unsafe { IsValidSid(sid) } == 0
        || unsafe { EqualSid(sid, expected_sid) } == 0
    {
        return Err(SupervisorError::new(
            "authority_native_job_security_readback_invalid",
        ));
    }
    let sid_length = unsafe { GetLengthSid(sid) } as usize;
    let expected_size = size_of::<ACCESS_ALLOWED_ACE>()
        .checked_sub(size_of::<u32>())
        .and_then(|value| value.checked_add(sid_length))
        .ok_or_else(|| SupervisorError::new("authority_native_job_security_readback_invalid"))?;
    if sid_length == 0 || sid_length > MAX_SID_BYTES || ace.Header.AceSize as usize != expected_size
    {
        return Err(SupervisorError::new(
            "authority_native_job_security_readback_invalid",
        ));
    }
    Ok(AccessAllowedAceReadback {
        mask: ace.Mask,
        size: ace.Header.AceSize,
        sid,
    })
}

#[cfg(test)]
fn current_process_user_sid_string() -> Result<String, SupervisorError> {
    let mut raw_token = null_mut();
    if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut raw_token) } == 0 {
        return Err(SupervisorError::new(
            "authority_native_job_test_token_open_failed",
        ));
    }
    let token = OwnedKernelHandle::new(raw_token, "authority_native_job_test_token_open_failed")?;
    let mut required = 0u32;
    unsafe {
        SetLastError(ERROR_SUCCESS);
    }
    if unsafe { GetTokenInformation(token.raw(), TokenUser, null_mut(), 0, &mut required) } != 0
        || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER
        || required < size_of::<TOKEN_USER>() as u32
        || required > 1024 * 1024
    {
        return Err(SupervisorError::new(
            "authority_native_job_test_token_query_failed",
        ));
    }
    let word_size = size_of::<usize>();
    let word_count = (required as usize)
        .checked_add(word_size - 1)
        .ok_or_else(|| SupervisorError::new("authority_native_job_test_token_query_failed"))?
        / word_size;
    let mut buffer = vec![0usize; word_count];
    if unsafe {
        GetTokenInformation(
            token.raw(),
            TokenUser,
            buffer.as_mut_ptr().cast(),
            required,
            &mut required,
        )
    } == 0
    {
        return Err(SupervisorError::new(
            "authority_native_job_test_token_query_failed",
        ));
    }
    let token_user = unsafe { &*buffer.as_ptr().cast::<TOKEN_USER>() };
    let sid = token_user.User.Sid;
    if sid.is_null() || unsafe { IsValidSid(sid) } == 0 {
        return Err(SupervisorError::new(
            "authority_native_job_test_token_query_failed",
        ));
    }
    let mut text = null_mut::<u16>();
    if unsafe { ConvertSidToStringSidW(sid, &mut text) } == 0 || text.is_null() {
        if !text.is_null() {
            unsafe {
                LocalFree(text.cast());
            }
        }
        return Err(SupervisorError::new(
            "authority_native_job_test_sid_convert_failed",
        ));
    }
    let Some(length) = (0..256).find(|offset| unsafe { *text.add(*offset) } == 0) else {
        unsafe {
            LocalFree(text.cast());
        }
        return Err(SupervisorError::new(
            "authority_native_job_test_sid_convert_failed",
        ));
    };
    let value = String::from_utf16(unsafe { std::slice::from_raw_parts(text, length) })
        .map_err(|_| SupervisorError::new("authority_native_job_test_sid_convert_failed"));
    unsafe {
        LocalFree(text.cast());
    }
    value
}

#[cfg(test)]
pub(super) mod tests {
    use super::*;
    use std::{
        os::windows::{ffi::OsStrExt, io::RawHandle},
        path::PathBuf,
        sync::atomic::{AtomicU64, Ordering},
        time::{SystemTime, UNIX_EPOCH},
    };

    static TEST_SEQUENCE: AtomicU64 = AtomicU64::new(1);

    fn test_identity(seed: u8) -> AuthorityRuntimeIdentity {
        AuthorityRuntimeIdentity::new(
            [seed; 32],
            [seed.wrapping_add(1); 32],
            [seed.wrapping_add(2); 32],
            [seed.wrapping_add(3); 32],
            [seed.wrapping_add(4); 32],
        )
        .expect("valid test runtime identity")
    }

    fn test_specs() -> (NativeJobSpec, NativeJobSecuritySpec) {
        let sequence = TEST_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock")
            .as_nanos();
        let mut hasher = Sha256::new();
        hasher.update(b"vrcforge-native-job-test-v1\0");
        hasher.update(std::process::id().to_be_bytes());
        hasher.update(sequence.to_be_bytes());
        hasher.update(nanos.to_be_bytes());
        let identity = test_identity((sequence as u8).wrapping_add(1));
        let security = NativeJobSecuritySpec::for_test_current_owner(&identity)
            .expect("ordinary-permission test security");
        (
            NativeJobSpec {
                object_id: sequence,
                deterministic_name_digest: hasher.finalize().into(),
                run_binding_digest: [(sequence as u8).wrapping_add(0x20); 32],
                security_binding_digest: security.binding_digest,
                created_at: sequence,
            },
            security,
        )
    }

    fn inheritable_test_event() -> OwnedKernelHandle {
        let attributes = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: null_mut(),
            bInheritHandle: 1,
        };
        let handle = unsafe { CreateEventW(&attributes, 1, 0, std::ptr::null()) };
        let handle =
            OwnedKernelHandle::new(handle, "authority_native_job_test_event_create_failed")
                .expect("inheritable test event");
        assert!(handle.is_inheritable().expect("event handle flags"));
        handle
    }

    fn noninheritable_test_event() -> OwnedKernelHandle {
        let attributes = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: null_mut(),
            bInheritHandle: 0,
        };
        let handle = unsafe { CreateEventW(&attributes, 1, 0, std::ptr::null()) };
        let handle =
            OwnedKernelHandle::new(handle, "authority_native_job_test_event_create_failed")
                .expect("noninheritable test event");
        assert!(!handle.is_inheritable().expect("event handle flags"));
        handle
    }

    fn inheritable_test_events<const N: usize>() -> [OwnedKernelHandle; N] {
        std::array::from_fn(|_| inheritable_test_event())
    }

    fn borrowed_test_handles(handles: &[OwnedKernelHandle]) -> Vec<BorrowedHandle<'static>> {
        handles
            .iter()
            .map(|handle| unsafe { borrowed_handle(handle.raw()) })
            .collect()
    }

    fn raw_test_handles(handles: &[OwnedKernelHandle]) -> Vec<HANDLE> {
        handles.iter().map(OwnedKernelHandle::raw).collect()
    }

    fn create_suspended_process_with_attributes(
        attributes: &mut NativeJobLaunchAttributeList<'_>,
    ) -> (
        OwnedKernelHandle,
        OwnedKernelHandle,
        u32,
        u32,
        NativeJobLaunchAttributeBinding,
    ) {
        let application = PathBuf::from(std::env::var_os("SystemRoot").expect("system root"))
            .join("System32")
            .join("ping.exe");
        let application = application
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let mut command_line = "ping.exe -n 30 127.0.0.1"
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let binding = attributes.binding().clone();
        let mut startup: STARTUPINFOEXW = unsafe { zeroed() };
        startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
        startup.lpAttributeList = attributes.raw_attribute_list();
        let mut process_information: PROCESS_INFORMATION = unsafe { zeroed() };
        assert_ne!(
            unsafe {
                CreateProcessW(
                    application.as_ptr(),
                    command_line.as_mut_ptr(),
                    std::ptr::null(),
                    std::ptr::null(),
                    i32::from(attributes.inherit_handles()),
                    attributes.creation_flags(),
                    std::ptr::null(),
                    std::ptr::null(),
                    &startup.StartupInfo,
                    &mut process_information,
                )
            },
            0,
            "create suspended root directly in the strict job"
        );
        let process = OwnedKernelHandle::new(
            process_information.hProcess,
            "authority_native_job_test_process_invalid",
        )
        .expect("held process handle");
        let primary_thread = OwnedKernelHandle::new(
            process_information.hThread,
            "authority_native_job_test_thread_invalid",
        )
        .expect("held primary-thread handle");
        (
            process,
            primary_thread,
            process_information.dwProcessId,
            process_information.dwThreadId,
            binding,
        )
    }

    fn create_suspended_process_outside_job() -> (OwnedKernelHandle, OwnedKernelHandle, u32, u32) {
        let application = PathBuf::from(std::env::var_os("SystemRoot").expect("system root"))
            .join("System32")
            .join("ping.exe");
        let application = application
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let mut command_line = "ping.exe -n 30 127.0.0.1"
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let mut startup: STARTUPINFOW = unsafe { zeroed() };
        startup.cb = size_of::<STARTUPINFOW>() as u32;
        let mut information: PROCESS_INFORMATION = unsafe { zeroed() };
        assert_ne!(
            unsafe {
                CreateProcessW(
                    application.as_ptr(),
                    command_line.as_mut_ptr(),
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                    CREATE_SUSPENDED | CREATE_NO_WINDOW,
                    std::ptr::null(),
                    std::ptr::null(),
                    &startup,
                    &mut information,
                )
            },
            0,
            "create suspended process outside the strict job"
        );
        let process = OwnedKernelHandle::new(
            information.hProcess,
            "authority_native_job_test_process_invalid",
        )
        .expect("held outside process");
        let thread = OwnedKernelHandle::new(
            information.hThread,
            "authority_native_job_test_thread_invalid",
        )
        .expect("held outside primary thread");
        (
            process,
            thread,
            information.dwProcessId,
            information.dwThreadId,
        )
    }

    fn create_suspended_test_root() -> (WindowsNativeActiveJob, OwnedKernelHandle, OwnedKernelHandle)
    {
        let (spec, security) = test_specs();
        let native =
            WindowsNativeJob::create_from_spec(spec, &security).expect("create strict job");
        let inherited_events = inheritable_test_events::<3>();
        let inherited = borrowed_test_handles(&inherited_events);
        let (process, primary_thread, process_id, primary_thread_id, binding) = {
            let mut attributes = native
                .prepare_suspended_launch_attributes(&inherited)
                .expect("prepare create-time job attributes");
            create_suspended_process_with_attributes(&mut attributes)
        };
        let active = native
            .revalidate_created_root_before_resume(
                unsafe { borrowed_handle(process.raw()) },
                unsafe { borrowed_handle(primary_thread.raw()) },
                process_id,
                primary_thread_id,
                &binding,
            )
            .expect("revalidate create-time root before resume");
        (active, process, primary_thread)
    }

    fn create_policy_bound_empty_job(policy: &mut SupervisorPolicy) -> WindowsNativeJob {
        let sequence = TEST_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock")
            .as_nanos();
        let seed = (sequence as u8).wrapping_add(0x40);
        let identity = AuthorityRuntimeIdentity::new(
            policy.authority_generation_digest,
            [seed; 32],
            [seed.wrapping_add(1); 32],
            [seed.wrapping_add(2); 32],
            [seed.wrapping_add(3); 32],
        )
        .expect("policy-bound test identity");
        let security = NativeJobSecuritySpec::for_test_current_owner(&identity)
            .expect("ordinary-permission test security");
        let mut name = Sha256::new();
        name.update(b"vrcforge-native-job-terminal-proof-test-v1\0");
        name.update(std::process::id().to_be_bytes());
        name.update(sequence.to_be_bytes());
        name.update(nanos.to_be_bytes());
        let deterministic_name_digest: Digest = name.finalize().into();
        policy.authority_identity_digest = identity.binding_digest();
        policy.job_object_id = sequence;
        policy.deterministic_job_name_digest = deterministic_name_digest;
        policy.job_security_binding_digest = security.binding_digest;
        WindowsNativeJob::create_from_spec(
            NativeJobSpec {
                object_id: policy.job_object_id,
                deterministic_name_digest,
                run_binding_digest: policy.run_binding_digest,
                security_binding_digest: security.binding_digest,
                created_at: policy.issued_at + 1,
            },
            &security,
        )
        .expect("create policy-bound strict Job")
    }

    fn create_policy_bound_suspended_test_root(
        policy: &mut SupervisorPolicy,
    ) -> (WindowsNativeActiveJob, OwnedKernelHandle, OwnedKernelHandle) {
        let native = create_policy_bound_empty_job(policy);
        let inherited_events = inheritable_test_events::<3>();
        let inherited = borrowed_test_handles(&inherited_events);
        let (process, primary_thread, process_id, primary_thread_id, binding) = {
            let mut attributes = native
                .prepare_suspended_launch_attributes(&inherited)
                .expect("prepare policy-bound root attributes");
            create_suspended_process_with_attributes(&mut attributes)
        };
        let active = native
            .revalidate_created_root_before_resume(
                unsafe { borrowed_handle(process.raw()) },
                unsafe { borrowed_handle(primary_thread.raw()) },
                process_id,
                primary_thread_id,
                &binding,
            )
            .expect("admit policy-bound held root");
        (active, process, primary_thread)
    }

    pub(in super::super) fn real_empty_job_for_policy(
        policy: &mut SupervisorPolicy,
    ) -> WindowsNativeJob {
        create_policy_bound_empty_job(policy)
    }

    pub(in super::super) fn real_launch_bindings_for_policy(
        policy: &mut SupervisorPolicy,
    ) -> (
        NativeJobLaunchAttributeBinding,
        NativeJobLaunchAttributeBinding,
    ) {
        let native = create_policy_bound_empty_job(policy);
        let inherited_events = inheritable_test_events::<6>();
        let raw_handles = raw_test_handles(&inherited_events);
        let bridge = NativeJobLaunchAttributeBinding::new(
            &native,
            ChildBootstrapRole::BridgeLauncher,
            &raw_handles[..3],
        )
        .expect("bridge launch binding");
        let driver = NativeJobLaunchAttributeBinding::new(
            &native,
            ChildBootstrapRole::LifecycleDriver,
            &raw_handles[3..],
        )
        .expect("driver launch binding");
        (bridge, driver)
    }

    pub(in super::super) fn real_terminal_proof_for_policy(
        policy: &mut SupervisorPolicy,
    ) -> NativeJobTerminalProof {
        let (active, process, primary_thread) = create_policy_bound_suspended_test_root(policy);
        let proof = active
            .into_terminal_proof()
            .expect("live held Job reaches an exact terminal proof");
        drop(primary_thread);
        drop(process);
        proof
    }

    pub(in super::super) fn real_active_job_for_policy(
        policy: &mut SupervisorPolicy,
    ) -> (WindowsNativeActiveJob, u32) {
        let (active, process, primary_thread) = create_policy_bound_suspended_test_root(policy);
        let process_id = unsafe { GetProcessId(process.raw()) };
        drop(primary_thread);
        drop(process);
        (active, process_id)
    }

    pub(in super::super) fn real_fault_held_live_job_for_policy(
        policy: &mut SupervisorPolicy,
    ) -> (WindowsNativeActiveJob, u32) {
        let (mut active, process, primary_thread) = create_policy_bound_suspended_test_root(policy);
        let process_id = unsafe { GetProcessId(process.raw()) };
        active.terminal_state = NativeJobTerminalState::FaultHeld;
        drop(primary_thread);
        drop(process);
        (active, process_id)
    }

    pub(in super::super) fn real_terminal_proof_without_notifications_for_policy(
        policy: &mut SupervisorPolicy,
    ) -> NativeJobTerminalProof {
        let (mut active, process, primary_thread) = create_policy_bound_suspended_test_root(policy);
        active
            .request_termination()
            .expect("terminate the exact held Job before stripping its advisory queue");
        assert_eq!(
            unsafe { WaitForSingleObject(process.raw(), JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS) },
            WAIT_OBJECT_0,
            "the held root must be terminal before the test strips all ordinary notifications"
        );

        let mut consecutive_quiet_boundaries = 0;
        for _ in 0..8 {
            match dequeue_completion_message(
                &active.job.completion_port,
                active.job.receipt.object_id,
                MAX_JOB_TERMINAL_POLL_MILLIS,
            )
            .expect("strip one real ordinary Job notification")
            {
                Some(_) => consecutive_quiet_boundaries = 0,
                None => {
                    consecutive_quiet_boundaries += 1;
                    if consecutive_quiet_boundaries == 2 {
                        break;
                    }
                }
            }
        }
        assert_eq!(
            consecutive_quiet_boundaries, 2,
            "the test must establish two drained completion-port boundaries"
        );

        let proof = active
            .into_terminal_proof()
            .expect("kernel terminal authority must survive complete notification loss");
        assert_eq!(
            proof.receipt.completion_message_count, 0,
            "ordinary notifications cannot be required to issue the terminal proof"
        );
        assert!(!proof.receipt.active_process_zero_observed);
        drop(primary_thread);
        drop(process);
        proof
    }

    fn create_suspended_test_root_with_consumed_notification(
    ) -> (WindowsNativeActiveJob, OwnedKernelHandle, OwnedKernelHandle) {
        let (spec, security) = test_specs();
        let native =
            WindowsNativeJob::create_from_spec(spec, &security).expect("create strict job");
        let inherited_events = inheritable_test_events::<3>();
        let inherited = borrowed_test_handles(&inherited_events);
        let (process, primary_thread, process_id, primary_thread_id, binding) = {
            let mut attributes = native
                .prepare_suspended_launch_attributes(&inherited)
                .expect("prepare create-time job attributes");
            create_suspended_process_with_attributes(&mut attributes)
        };
        assert_eq!(
            dequeue_completion_message(
                &native.completion_port,
                native.receipt.object_id,
                JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS,
            )
            .expect("read real root notification")
            .expect("real root notification must arrive"),
            NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::NewProcess,
                process_id: Some(process_id),
            }
        );
        let active = native
            .revalidate_created_root_before_resume(
                unsafe { borrowed_handle(process.raw()) },
                unsafe { borrowed_handle(primary_thread.raw()) },
                process_id,
                primary_thread_id,
                &binding,
            )
            .expect("kernel authority admits a root without a queued notification");
        (active, process, primary_thread)
    }

    fn create_unregistered_additional_test_root(
        active: &mut WindowsNativeActiveJob,
    ) -> (OwnedKernelHandle, OwnedKernelHandle) {
        let inherited_events = inheritable_test_events::<3>();
        let inherited = borrowed_test_handles(&inherited_events);
        let (process, primary_thread, _, _, _) = {
            let mut attributes = active
                .prepare_additional_suspended_launch_attributes(&inherited)
                .expect("prepare unregistered create-time root");
            create_suspended_process_with_attributes(&mut attributes)
        };
        (process, primary_thread)
    }

    fn poll_until_complete(active: &mut WindowsNativeActiveJob) -> NativeJobTerminalDrainReceipt {
        for _ in 0..20 {
            match active
                .poll_terminal_drain(MAX_JOB_TERMINAL_POLL_MILLIS)
                .expect("bounded terminal poll")
            {
                NativeJobTerminalDrainStatus::Pending => continue,
                NativeJobTerminalDrainStatus::Complete(receipt) => return receipt,
            }
        }
        panic!("held roots and stable empty job did not become terminal in bounded polls");
    }

    fn poll_until_fault_contained(active: &mut WindowsNativeActiveJob) {
        for _ in 0..20 {
            match active.poll_terminal_drain(MAX_JOB_TERMINAL_POLL_MILLIS) {
                Ok(NativeJobTerminalDrainStatus::Pending) => continue,
                Ok(NativeJobTerminalDrainStatus::Complete(_)) => {
                    panic!("fault containment must never issue a success receipt")
                }
                Err(error) if error.code() == "authority_native_job_fault_contained" => {
                    assert_eq!(active.terminal_state, NativeJobTerminalState::FaultHeld);
                    return;
                }
                Err(error) => panic!("unexpected containment error: {}", error.code()),
            }
        }
        panic!("fault containment did not reach stable empty state in bounded polls");
    }

    fn assert_resealed_terminal_context_mutation_faults(
        mutate: fn(&mut NativeJobTerminalDrainReceipt),
    ) {
        assert_resealed_terminal_receipt_mutation_faults(
            mutate,
            "authority_native_job_terminal_context_invalid",
        );
    }

    fn assert_resealed_terminal_receipt_mutation_faults(
        mutate: fn(&mut NativeJobTerminalDrainReceipt),
        expected_error: &str,
    ) {
        let (mut active, _process, _primary_thread) = create_suspended_test_root();
        active.request_termination().expect("request termination");
        let mut receipt = poll_until_complete(&mut active);
        mutate(&mut receipt);
        receipt.receipt_mac = receipt.derive_mac(&active.job.terminal_proof_key);
        receipt
            .validate(&active.job.terminal_proof_key)
            .expect("hostile receipt remains internally self-consistent");
        active.terminal_state = NativeJobTerminalState::Complete(receipt);
        assert_eq!(
            active.poll_terminal_drain(0).unwrap_err().code(),
            expected_error
        );
        assert_eq!(active.terminal_state, NativeJobTerminalState::FaultHeld);
        assert_eq!(
            active.poll_terminal_drain(0).unwrap_err().code(),
            "authority_native_job_terminal_fault_held"
        );
    }

    unsafe fn borrowed_handle(raw: HANDLE) -> BorrowedHandle<'static> {
        unsafe { BorrowedHandle::borrow_raw(raw as RawHandle) }
    }

    #[test]
    fn creates_exclusive_non_inheritable_empty_job_with_strict_readback() {
        let (spec, security) = test_specs();
        let native =
            WindowsNativeJob::create_from_spec(spec, &security).expect("create strict job");
        assert_eq!(native.receipt.object_id, spec.object_id);
        assert_eq!(
            native.receipt.deterministic_name_digest,
            spec.deterministic_name_digest
        );
        assert_eq!(native.receipt.created_at, spec.created_at);
        assert!(native.receipt.kill_on_job_close);
        assert!(!native.receipt.breakaway_allowed);
        assert!(!native.receipt.silent_breakaway_allowed);
        assert_eq!(native.receipt.active_process_limit, 0);
        assert!(native.receipt.completion_port_attached);
        assert!(native.receipt.service_handle_held);
        assert_eq!(
            native.readback.limit_flags,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        );
        assert_eq!(native.readback.total_processes, 0);
        assert_eq!(native.readback.active_processes, 0);
        assert_eq!(
            native.readback.security.binding_digest,
            security.binding_digest
        );
        assert!(native.readback.security.owner_exact);
        assert!(native.readback.security.dacl_protected);
        assert_eq!(native.readback.security.ace_count, 2);
        let exclusions = native.exclusion_handles();
        assert_ne!(
            exclusions.job().as_raw_handle(),
            exclusions.completion_port().as_raw_handle()
        );
        native.revalidate_empty().expect("stable kernel readback");
    }

    #[test]
    fn primary_launch_attributes_bind_bridge_role_and_exact_raw_handle_list() {
        let (spec, security) = test_specs();
        let native =
            WindowsNativeJob::create_from_spec(spec, &security).expect("create strict job");
        let events = inheritable_test_events::<3>();
        let inherited = borrowed_test_handles(&events);
        let mut attributes = native
            .prepare_suspended_launch_attributes(&inherited)
            .expect("prepare exact launch attributes");
        let binding = attributes.binding();
        assert_eq!(binding.object_id, spec.object_id);
        assert_eq!(
            binding.deterministic_name_digest,
            spec.deterministic_name_digest
        );
        assert_eq!(binding.run_binding_digest, spec.run_binding_digest);
        assert_eq!(binding.creation_flags, FIXED_CHILD_CREATION_FLAGS);
        assert_eq!(binding.creation_flags & CREATE_BREAKAWAY_FROM_JOB, 0);
        assert!(binding.job_assigned_at_creation);
        assert!(binding.job_list_attribute_applied);
        assert!(binding.handle_list_attribute_applied);
        assert!(!binding.initial_assignment_call_performed);
        assert!(!binding.breakaway_requested);
        assert_eq!(binding.job_list_count, 1);
        assert_eq!(binding.handle_list_count, 3);
        assert_eq!(
            binding.raw_handle_list.role(),
            ChildBootstrapRole::BridgeLauncher
        );
        assert_eq!(
            binding.raw_handle_list,
            raw_handle_list(
                ChildBootstrapRole::BridgeLauncher,
                &raw_test_handles(&events)
            )
            .expect("exact raw handle digest")
        );
        assert!(!attributes.raw_attribute_list().is_null());
        assert_eq!(attributes.creation_flags(), FIXED_CHILD_CREATION_FLAGS);
        assert!(attributes.inherit_handles());
        drop(attributes);
        native
            .revalidate_empty()
            .expect("attribute build is nonmutating");
    }

    #[test]
    fn launch_binding_rejects_resealed_breakaway_assignment_and_identity_drift() {
        let (spec, security) = test_specs();
        let native =
            WindowsNativeJob::create_from_spec(spec, &security).expect("create strict job");
        let events = inheritable_test_events::<3>();
        let baseline = NativeJobLaunchAttributeBinding::new(
            &native,
            ChildBootstrapRole::BridgeLauncher,
            &raw_test_handles(&events),
        )
        .expect("baseline binding");
        let mut mutations = Vec::new();

        let mut value = baseline.clone();
        value.creation_flags |= CREATE_BREAKAWAY_FROM_JOB;
        mutations.push(value);
        let mut value = baseline.clone();
        value.breakaway_requested = true;
        mutations.push(value);
        let mut value = baseline.clone();
        value.initial_assignment_call_performed = true;
        mutations.push(value);
        let mut value = baseline.clone();
        value.job_assigned_at_creation = false;
        mutations.push(value);
        let mut value = baseline.clone();
        value.job_list_attribute_applied = false;
        mutations.push(value);
        let mut value = baseline.clone();
        value.handle_list_attribute_applied = false;
        mutations.push(value);
        let mut value = baseline.clone();
        value.job_list_count = 0;
        mutations.push(value);
        let mut value = baseline.clone();
        value.handle_list_count = 0;
        mutations.push(value);
        let mut value = baseline.clone();
        value.job_handle_non_inheritable = false;
        mutations.push(value);
        let mut value = baseline.clone();
        value.completion_port_handle_non_inheritable = false;
        mutations.push(value);
        let mut value = baseline;
        value.authority_generation_digest = [0xf1; 32];
        mutations.push(value);

        for mut mutation in mutations {
            mutation.binding_digest = mutation.derive_binding_digest();
            assert_eq!(
                mutation
                    .validate(&native, ChildBootstrapRole::BridgeLauncher)
                    .unwrap_err()
                    .code(),
                "authority_native_job_launch_attribute_binding_invalid"
            );
        }
    }

    #[test]
    fn stale_run_launch_binding_cannot_validate_against_recreated_job() {
        let (spec, security) = test_specs();
        let first =
            WindowsNativeJob::create_from_spec(spec, &security).expect("create first strict Job");
        let events = inheritable_test_events::<3>();
        let stale = NativeJobLaunchAttributeBinding::new(
            &first,
            ChildBootstrapRole::BridgeLauncher,
            &raw_test_handles(&events),
        )
        .expect("first-run launch binding");
        stale
            .validate(&first, ChildBootstrapRole::BridgeLauncher)
            .expect("binding is valid only for its original run");
        drop(first);

        let rebound_spec = NativeJobSpec {
            run_binding_digest: [spec.run_binding_digest[0] ^ 0x80; 32],
            ..spec
        };
        let rebound = WindowsNativeJob::create_from_spec(rebound_spec, &security)
            .expect("recreate same named Job for a different run");
        assert_eq!(stale.object_id, rebound.receipt.object_id);
        assert_eq!(
            stale.deterministic_name_digest,
            rebound.receipt.deterministic_name_digest
        );
        assert_eq!(
            stale.authority_generation_digest,
            rebound.security.authority_generation_digest
        );
        assert_eq!(
            stale.security_binding_digest,
            rebound.security.binding_digest
        );
        assert_ne!(stale.run_binding_digest, rebound.run_binding_digest);
        assert_eq!(
            stale
                .validate(&rebound, ChildBootstrapRole::BridgeLauncher)
                .unwrap_err()
                .code(),
            "authority_native_job_launch_attribute_binding_invalid"
        );
    }

    #[test]
    fn source_contract_contains_no_post_create_initial_assignment_call() {
        let source = include_str!("native_job.rs");
        let forbidden = ["Assign", "Process", "To", "Job", "Object"].concat();
        assert!(!source.contains(&forbidden));
        assert!(source.contains("PROC_THREAD_ATTRIBUTE_JOB_LIST"));
        assert!(source.contains("initial_assignment_call_performed"));
    }

    #[test]
    fn pre_resume_readback_rejects_every_handle_membership_and_roster_drift() {
        let (spec, security) = test_specs();
        let native =
            WindowsNativeJob::create_from_spec(spec, &security).expect("create strict job");
        let events = inheritable_test_events::<3>();
        let launch = NativeJobLaunchAttributeBinding::new(
            &native,
            ChildBootstrapRole::BridgeLauncher,
            &raw_test_handles(&events),
        )
        .expect("launch binding");
        let process_id = 4_301;
        let thread_id = 4_302;
        let baseline = NativeJobPreResumeReadback {
            process_id_from_handle: process_id,
            process_id_from_thread_handle: process_id,
            primary_thread_id_from_handle: thread_id,
            process_handle_non_inheritable: true,
            primary_thread_handle_non_inheritable: true,
            is_process_in_exact_job: true,
            exact_process_roster: BTreeSet::from([process_id]),
            strict_live_job_readback: true,
        };
        let receipt = NativeJobPreResumeMembershipReceipt::from_readback(
            &native, &launch, process_id, thread_id, &baseline,
        )
        .expect("baseline pre-resume receipt");
        assert!(receipt.job_assigned_at_creation);
        assert_eq!(receipt.run_binding_digest, spec.run_binding_digest);
        assert!(!receipt.initial_assignment_call_performed);
        assert!(receipt.job_membership_revalidated);
        assert!(receipt.membership_readback_before_resume);

        let mut stale_run = receipt.clone();
        stale_run.run_binding_digest[0] ^= 0x40;
        stale_run.receipt_digest = stale_run.derive_digest();
        assert_eq!(
            stale_run
                .validate(&native, &launch, ChildBootstrapRole::BridgeLauncher)
                .unwrap_err()
                .code(),
            "authority_native_job_pre_resume_membership_invalid"
        );

        let mutations = [
            NativeJobPreResumeReadback {
                process_id_from_handle: process_id + 1,
                ..baseline.clone()
            },
            NativeJobPreResumeReadback {
                process_id_from_thread_handle: process_id + 1,
                ..baseline.clone()
            },
            NativeJobPreResumeReadback {
                primary_thread_id_from_handle: thread_id + 1,
                ..baseline.clone()
            },
            NativeJobPreResumeReadback {
                process_handle_non_inheritable: false,
                ..baseline.clone()
            },
            NativeJobPreResumeReadback {
                primary_thread_handle_non_inheritable: false,
                ..baseline.clone()
            },
            NativeJobPreResumeReadback {
                is_process_in_exact_job: false,
                ..baseline.clone()
            },
            NativeJobPreResumeReadback {
                exact_process_roster: BTreeSet::new(),
                ..baseline.clone()
            },
            NativeJobPreResumeReadback {
                exact_process_roster: BTreeSet::from([process_id, process_id + 1]),
                ..baseline.clone()
            },
            NativeJobPreResumeReadback {
                strict_live_job_readback: false,
                ..baseline.clone()
            },
        ];
        for mutation in mutations {
            assert_eq!(
                NativeJobPreResumeMembershipReceipt::from_readback(
                    &native, &launch, process_id, thread_id, &mutation,
                )
                .unwrap_err()
                .code(),
                "authority_native_job_pre_resume_readback_invalid"
            );
        }
    }

    #[test]
    fn additional_root_membership_binds_the_complete_prior_plus_new_roster() {
        let (spec, security) = test_specs();
        let native =
            WindowsNativeJob::create_from_spec(spec, &security).expect("create strict job");
        let events = inheritable_test_events::<3>();
        let launch = NativeJobLaunchAttributeBinding::new(
            &native,
            ChildBootstrapRole::LifecycleDriver,
            &raw_test_handles(&events),
        )
        .expect("launch binding");
        assert_eq!(
            launch.raw_handle_list.role(),
            ChildBootstrapRole::LifecycleDriver
        );
        let first_process_id = 4_311;
        let process_id = 4_312;
        let thread_id = 4_313;
        let expected_roster = BTreeSet::from([first_process_id, process_id]);
        let baseline = NativeJobPreResumeReadback {
            process_id_from_handle: process_id,
            process_id_from_thread_handle: process_id,
            primary_thread_id_from_handle: thread_id,
            process_handle_non_inheritable: true,
            primary_thread_handle_non_inheritable: true,
            is_process_in_exact_job: true,
            exact_process_roster: expected_roster.clone(),
            strict_live_job_readback: true,
        };
        let receipt = NativeJobPreResumeMembershipReceipt::from_readback_for_expected_roster(
            &native,
            &launch,
            ChildBootstrapRole::LifecycleDriver,
            process_id,
            thread_id,
            &expected_roster,
            &baseline,
        )
        .expect("second root membership");
        assert_eq!(
            receipt.active_roster_digest,
            active_roster_digest(&expected_roster).unwrap()
        );

        for hostile_roster in [
            BTreeSet::from([process_id]),
            BTreeSet::from([first_process_id, process_id, process_id + 1]),
        ] {
            assert_eq!(
                NativeJobPreResumeMembershipReceipt::from_readback_for_expected_roster(
                    &native,
                    &launch,
                    ChildBootstrapRole::LifecycleDriver,
                    process_id,
                    thread_id,
                    &expected_roster,
                    &NativeJobPreResumeReadback {
                        exact_process_roster: hostile_roster,
                        ..baseline.clone()
                    },
                )
                .unwrap_err()
                .code(),
                "authority_native_job_pre_resume_readback_invalid"
            );
        }
        assert_eq!(
            NativeJobPreResumeMembershipReceipt::from_readback_for_expected_roster(
                &native,
                &launch,
                ChildBootstrapRole::LifecycleDriver,
                process_id,
                thread_id,
                &BTreeSet::from([process_id]),
                &baseline,
            )
            .unwrap_err()
            .code(),
            "authority_native_job_pre_resume_readback_invalid"
        );
    }

    #[test]
    fn additional_root_prepare_requires_the_exact_live_roster_and_latches_failure() {
        let (mut active, process, _primary_thread) = create_suspended_test_root();
        assert_eq!(active.root_membership_receipts().len(), 1);
        active.roster.active_process_ids.clear();
        let events = inheritable_test_events::<3>();
        let inherited = borrowed_test_handles(&events);
        assert_eq!(
            active
                .prepare_additional_suspended_launch_attributes(&inherited)
                .unwrap_err()
                .code(),
            "authority_native_job_pre_resume_roster_invalid"
        );
        assert!(active.admission_faulted);
        assert_eq!(
            active.terminal_state,
            NativeJobTerminalState::FaultContaining
        );
        assert_eq!(
            active
                .prepare_additional_suspended_launch_attributes(&inherited)
                .unwrap_err()
                .code(),
            "authority_native_job_terminal_fault_held"
        );
        assert_eq!(
            unsafe { WaitForSingleObject(process.raw(), JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS) },
            WAIT_OBJECT_0,
            "roster drift requests whole-job termination before returning"
        );
        poll_until_fault_contained(&mut active);
    }

    #[test]
    fn additional_attribute_build_failure_starts_whole_job_containment() {
        let (mut active, process, _primary_thread) = create_suspended_test_root();
        let events = inheritable_test_events::<2>();
        let inherited = [
            unsafe { borrowed_handle(active.job.completion_port.raw()) },
            unsafe { borrowed_handle(events[0].raw()) },
            unsafe { borrowed_handle(events[1].raw()) },
        ];
        assert_eq!(
            active
                .prepare_additional_suspended_launch_attributes(&inherited)
                .unwrap_err()
                .code(),
            "authority_native_job_raw_handle_list_invalid"
        );
        assert!(active.admission_faulted);
        assert_eq!(
            active.terminal_state,
            NativeJobTerminalState::FaultContaining
        );
        assert_eq!(
            unsafe { WaitForSingleObject(process.raw(), JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS) },
            WAIT_OBJECT_0,
            "attribute-build failure terminates the existing root before returning"
        );
        poll_until_fault_contained(&mut active);
        assert_eq!(
            active.poll_terminal_drain(0).unwrap_err().code(),
            "authority_native_job_terminal_fault_held"
        );
    }

    #[test]
    fn additional_prepare_unregistered_kernel_process_starts_whole_job_containment() {
        let (mut active, first_process, _first_thread) = create_suspended_test_root();
        let (unexpected_process, _unexpected_thread) =
            create_unregistered_additional_test_root(&mut active);
        let retry_events = inheritable_test_events::<3>();
        let retry_handles = borrowed_test_handles(&retry_events);
        let error = active
            .prepare_additional_suspended_launch_attributes(&retry_handles)
            .unwrap_err();
        assert_eq!(
            error.code(),
            "authority_native_job_pre_resume_roster_invalid"
        );
        assert!(active.admission_faulted);
        assert_eq!(
            active.terminal_state,
            NativeJobTerminalState::FaultContaining
        );
        for process in [&first_process, &unexpected_process] {
            assert_eq!(
                unsafe {
                    WaitForSingleObject(process.raw(), JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS)
                },
                WAIT_OBJECT_0,
                "additional-admission drift terminates the complete job"
            );
        }
        poll_until_fault_contained(&mut active);
        assert_eq!(
            active.poll_terminal_drain(0).unwrap_err().code(),
            "authority_native_job_terminal_fault_held"
        );
    }

    #[test]
    fn open_terminal_preflight_drift_terminates_before_fault_hold() {
        let (mut active, first_process, _first_thread) = create_suspended_test_root();
        let (unexpected_process, _unexpected_thread) =
            create_unregistered_additional_test_root(&mut active);
        assert_eq!(
            active.request_termination().unwrap_err().code(),
            "authority_native_job_readback_invalid"
        );
        assert!(active.admission_faulted);
        assert_eq!(
            active.terminal_state,
            NativeJobTerminalState::FaultContaining
        );
        for process in [&first_process, &unexpected_process] {
            assert_eq!(
                unsafe {
                    WaitForSingleObject(process.raw(), JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS)
                },
                WAIT_OBJECT_0,
                "terminal preflight drift still requests whole-job termination"
            );
        }
        poll_until_fault_contained(&mut active);
        assert_eq!(
            active.poll_terminal_drain(0).unwrap_err().code(),
            "authority_native_job_terminal_fault_held"
        );
    }

    #[test]
    fn failed_additional_root_revalidation_terminates_and_quarantines_the_created_root() {
        let (mut active, first_process, _first_thread) = create_suspended_test_root();
        let inherited_events = inheritable_test_events::<3>();
        let inherited = borrowed_test_handles(&inherited_events);
        let (second_process, second_thread, second_process_id, second_thread_id, binding) = {
            let mut attributes = active
                .prepare_additional_suspended_launch_attributes(&inherited)
                .expect("prepare exact second-root launch attributes");
            create_suspended_process_with_attributes(&mut attributes)
        };
        let wrong_thread_id = if second_thread_id == u32::MAX {
            second_thread_id - 1
        } else {
            second_thread_id + 1
        };
        assert_eq!(
            active
                .revalidate_additional_root_before_resume(
                    unsafe { borrowed_handle(second_process.raw()) },
                    unsafe { borrowed_handle(second_thread.raw()) },
                    second_process_id,
                    wrong_thread_id,
                    &binding,
                )
                .unwrap_err()
                .code(),
            "authority_native_job_pre_resume_readback_invalid"
        );
        assert!(active.admission_faulted);
        assert_eq!(active.root_membership_receipts().len(), 1);
        let quarantined = active
            .quarantined_root_processes
            .get(&second_process_id)
            .expect("failed second root is retained for containment");
        assert_ne!(
            unsafe { CompareObjectHandles(second_process.raw(), quarantined.handle.raw()) },
            0,
            "quarantine owns the exact created process object"
        );
        assert_eq!(
            active
                .revalidate_additional_root_before_resume(
                    unsafe { borrowed_handle(second_process.raw()) },
                    unsafe { borrowed_handle(second_thread.raw()) },
                    second_process_id,
                    second_thread_id,
                    &binding,
                )
                .unwrap_err()
                .code(),
            "authority_native_job_terminal_fault_held"
        );
        for process in [&first_process, &second_process] {
            assert_eq!(
                unsafe {
                    WaitForSingleObject(process.raw(), JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS)
                },
                WAIT_OBJECT_0,
                "containment terminates every process in the job"
            );
        }
        poll_until_fault_contained(&mut active);
        assert_eq!(
            active.poll_terminal_drain(0).unwrap_err().code(),
            "authority_native_job_terminal_fault_held"
        );
    }

    #[test]
    fn raw_handle_list_rejects_wrong_counts_invalid_duplicates_and_noninheritable_handles() {
        let (spec, security) = test_specs();
        let native =
            WindowsNativeJob::create_from_spec(spec, &security).expect("create strict job");
        for count in [0usize, 1, 2, 4] {
            let events = (0..count)
                .map(|_| inheritable_test_event())
                .collect::<Vec<_>>();
            assert_eq!(
                native
                    .prepare_suspended_launch_attributes(&borrowed_test_handles(&events))
                    .unwrap_err()
                    .code(),
                "authority_native_job_raw_handle_list_invalid"
            );
        }

        let events = inheritable_test_events::<3>();
        let raw = raw_test_handles(&events);
        for invalid in [std::ptr::null_mut(), INVALID_HANDLE_VALUE] {
            let mut candidate = raw.clone();
            candidate[0] = invalid;
            assert_eq!(
                raw_handle_list(ChildBootstrapRole::BridgeLauncher, &candidate)
                    .unwrap_err()
                    .code(),
                "authority_native_job_raw_handle_list_invalid"
            );
        }

        let duplicate = [
            unsafe { borrowed_handle(events[0].raw()) },
            unsafe { borrowed_handle(events[0].raw()) },
            unsafe { borrowed_handle(events[1].raw()) },
        ];
        assert_eq!(
            native
                .prepare_suspended_launch_attributes(&duplicate)
                .unwrap_err()
                .code(),
            "authority_native_job_raw_handle_list_invalid"
        );

        let noninheritable = noninheritable_test_event();
        let candidate = [
            unsafe { borrowed_handle(noninheritable.raw()) },
            unsafe { borrowed_handle(events[0].raw()) },
            unsafe { borrowed_handle(events[1].raw()) },
        ];
        assert_eq!(
            native
                .prepare_suspended_launch_attributes(&candidate)
                .unwrap_err()
                .code(),
            "authority_native_job_raw_handle_list_invalid"
        );

        for forbidden in [native.job.raw(), native.completion_port.raw()] {
            let candidate = [
                unsafe { borrowed_handle(forbidden) },
                unsafe { borrowed_handle(events[0].raw()) },
                unsafe { borrowed_handle(events[1].raw()) },
            ];
            assert_eq!(
                native
                    .prepare_suspended_launch_attributes(&candidate)
                    .unwrap_err()
                    .code(),
                "authority_native_job_raw_handle_list_invalid"
            );
        }
        native
            .revalidate_empty()
            .expect("hostile inputs do not mutate job");
    }

    #[test]
    fn typed_raw_handle_list_binds_role_order_and_debug_redacts_raw_values() {
        let (spec, security) = test_specs();
        let native =
            WindowsNativeJob::create_from_spec(spec, &security).expect("create strict job");
        let events = inheritable_test_events::<3>();
        let raw = raw_test_handles(&events);
        let primary = raw_handle_list(ChildBootstrapRole::BridgeLauncher, &raw)
            .expect("primary raw handle digest");
        assert_eq!(primary.role(), ChildBootstrapRole::BridgeLauncher);
        let mut reordered = raw.clone();
        reordered.swap(0, 1);
        assert_ne!(
            raw_handle_list(ChildBootstrapRole::BridgeLauncher, &reordered)
                .expect("reordered raw handle digest"),
            primary
        );
        let additional = raw_handle_list(ChildBootstrapRole::LifecycleDriver, &raw)
            .expect("additional raw handle digest");
        assert_eq!(additional.role(), ChildBootstrapRole::LifecycleDriver);
        assert_ne!(additional, primary);

        let inherited = borrowed_test_handles(&events);
        let attributes = native
            .prepare_suspended_launch_attributes(&inherited)
            .expect("prepare exact raw handle list");
        assert_eq!(attributes.binding().raw_handle_list, primary);
        let mut cross_role = attributes.binding().clone();
        cross_role.raw_handle_list = additional;
        cross_role.binding_digest = cross_role.derive_binding_digest();
        assert_eq!(
            cross_role
                .validate(&native, ChildBootstrapRole::BridgeLauncher)
                .unwrap_err()
                .code(),
            "authority_native_job_launch_attribute_binding_invalid"
        );
        let rendered = format!("{attributes:?}");
        assert!(rendered.contains("<held-and-redacted>"));
        assert!(!rendered.contains("inherited_handles"));
        assert!(!rendered.contains(&format!("{raw:?}")));
        for handle in raw {
            assert!(!rendered.contains(&format!("{:#x}", handle as usize)));
        }
    }

    #[test]
    fn failed_post_create_revalidation_consumes_and_closes_the_job_for_containment() {
        let (spec, security) = test_specs();
        let native =
            WindowsNativeJob::create_from_spec(spec, &security).expect("create strict job");
        let events = inheritable_test_events::<3>();
        let launch = NativeJobLaunchAttributeBinding::new(
            &native,
            ChildBootstrapRole::BridgeLauncher,
            &raw_test_handles(&events),
        )
        .expect("launch binding");
        let process = unsafe { borrowed_handle(GetCurrentProcess()) };
        let thread = unsafe { borrowed_handle(GetCurrentThread()) };
        assert!(native
            .revalidate_created_root_before_resume(
                process,
                thread,
                unsafe { GetCurrentProcessId() },
                unsafe { GetCurrentThreadId() },
                &launch,
            )
            .is_err());
        let replacement = WindowsNativeJob::create_from_spec(spec, &security)
            .expect("failed revalidation closed the original job");
        replacement
            .revalidate_empty()
            .expect("replacement remains strict");
    }

    #[test]
    fn preserving_revalidation_failure_explicitly_terminates_and_reads_back_the_job() {
        let (spec, security) = test_specs();
        let native =
            WindowsNativeJob::create_from_spec(spec, &security).expect("create strict job");
        let inherited_events = inheritable_test_events::<3>();
        let inherited = borrowed_test_handles(&inherited_events);
        let (process, primary_thread, process_id, primary_thread_id, mut binding) = {
            let mut attributes = native
                .prepare_suspended_launch_attributes(&inherited)
                .expect("prepare create-time attributes");
            create_suspended_process_with_attributes(&mut attributes)
        };
        binding.binding_digest[0] ^= 0xff;
        let failure = native
            .revalidate_created_root_before_resume_preserving_job(
                unsafe { borrowed_handle(process.raw()) },
                unsafe { borrowed_handle(primary_thread.raw()) },
                process_id,
                primary_thread_id,
                &binding,
            )
            .expect_err("tampered launch binding must preserve the Job owner");
        let (mut job, error) = failure.into_parts();
        assert_eq!(
            error.code(),
            "authority_native_job_launch_attribute_binding_invalid"
        );
        let receipt = job
            .contain_unadmitted_created_root(unsafe { borrowed_handle(process.raw()) })
            .expect("explicit unadmitted containment");
        assert_eq!(receipt.process_id, process_id);
        assert!(receipt.exact_job_membership_proven);
        assert!(receipt.job_termination_requested);
        assert!(!receipt.direct_process_termination_requested);
        assert!(receipt.process_signaled);
        assert!(receipt.exact_empty_terminal_job);
        assert_eq!(
            unsafe { WaitForSingleObject(process.raw(), 0) },
            WAIT_OBJECT_0
        );
        drop(job);
        let replacement =
            WindowsNativeJob::create_from_spec(spec, &security).expect("contained job closed");
        replacement.revalidate_empty().expect("replacement strict");
    }

    #[test]
    fn completion_transcript_records_a_complete_lifecycle_without_mutating_the_roster() {
        let root = 4_401;
        let mut tracker = NativeJobRosterTracker::from_pre_resume_root(root).unwrap();
        tracker
            .observe_terminal_advisory(NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::NewProcess,
                process_id: Some(root),
            })
            .unwrap();
        tracker
            .observe_terminal_advisory(NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::AbnormalExitProcess,
                process_id: Some(root),
            })
            .unwrap();
        tracker
            .observe_terminal_advisory(NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::ExitProcess,
                process_id: Some(root),
            })
            .unwrap();
        tracker
            .observe_terminal_advisory(NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::ActiveProcessZero,
                process_id: None,
            })
            .unwrap();
        tracker
            .validate_terminal_transcript(&BTreeSet::from([root]))
            .unwrap();
        assert_eq!(tracker.active_process_ids, BTreeSet::from([root]));
        assert_eq!(tracker.new_process_events, 1);
        assert_eq!(tracker.exit_process_events, 1);
        assert_eq!(tracker.abnormal_exit_process_events, 1);
        assert_eq!(tracker.completion_message_count, 4);
    }

    #[test]
    fn terminal_transcript_accepts_no_messages_and_keeps_the_admitted_roster() {
        let root = 4_421;
        let expected = BTreeSet::from([root]);
        let tracker = NativeJobRosterTracker::from_pre_resume_root(root).unwrap();

        tracker
            .validate_terminal_transcript(&expected)
            .expect("stable held-handle and kernel evidence does not require notifications");
        assert_eq!(tracker.active_process_ids, expected);
        assert_eq!(tracker.completion_message_count, 0);
        assert!(!tracker.active_process_zero_observed);
        assert_ne!(tracker.completion_transcript_digest, [0; 32]);
    }

    #[test]
    fn terminal_transcript_accepts_partial_messages_and_keeps_the_admitted_roster() {
        let first = 4_431;
        let second = 4_432;
        let expected = BTreeSet::from([first, second]);
        let mut tracker = NativeJobRosterTracker::from_pre_resume_root(first).unwrap();
        tracker
            .observe_additional_pre_resume_root(second, &expected)
            .unwrap();
        tracker
            .observe_terminal_advisory(NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::ExitProcess,
                process_id: Some(second),
            })
            .unwrap();

        tracker
            .validate_terminal_transcript(&expected)
            .expect("a unique partial advisory transcript remains valid");
        assert_eq!(tracker.active_process_ids, expected);
        assert_eq!(tracker.exit_process_events, 1);
        assert_eq!(tracker.completion_message_count, 1);
        assert!(!tracker.active_process_zero_observed);
    }

    #[test]
    fn additional_pre_resume_root_requires_the_exact_authoritative_roster() {
        let first = 4_451;
        let second = 4_452;
        let exact = BTreeSet::from([first, second]);
        let mut tracker = NativeJobRosterTracker::from_pre_resume_root(first).unwrap();
        tracker
            .observe_additional_pre_resume_root(second, &exact)
            .expect("second root must extend the exact kernel roster once");
        assert_eq!(tracker.active_process_ids, exact);
        assert_eq!(tracker.new_process_events, 0);

        for (process_id, roster) in [
            (first, BTreeSet::from([first])),
            (second, BTreeSet::from([first, second, second + 1])),
        ] {
            let mut hostile = NativeJobRosterTracker::from_pre_resume_root(first).unwrap();
            assert_eq!(
                hostile
                    .observe_additional_pre_resume_root(process_id, &roster)
                    .unwrap_err()
                    .code(),
                "authority_native_job_pre_resume_roster_invalid"
            );
            assert_eq!(hostile.active_process_ids, BTreeSet::from([first]));
            assert_eq!(hostile.new_process_events, 0);
        }
    }

    #[test]
    fn additional_root_outside_job_is_directly_terminated_and_fault_held() {
        let (mut active, first_process, _first_thread) = create_suspended_test_root();
        let inherited_events = inheritable_test_events::<3>();
        let inherited = borrowed_test_handles(&inherited_events);
        let binding = active
            .prepare_additional_suspended_launch_attributes(&inherited)
            .expect("prepare exact additional-root contract")
            .binding()
            .clone();
        let (outside_process, outside_thread, process_id, thread_id) =
            create_suspended_process_outside_job();
        let mut in_job = 1;
        assert_ne!(
            unsafe { IsProcessInJob(outside_process.raw(), active.job.job.raw(), &mut in_job) },
            0
        );
        assert_eq!(in_job, 0, "fixture process must remain outside the Job");

        assert_eq!(
            active
                .revalidate_additional_root_before_resume(
                    unsafe { borrowed_handle(outside_process.raw()) },
                    unsafe { borrowed_handle(outside_thread.raw()) },
                    process_id,
                    thread_id,
                    &binding,
                )
                .unwrap_err()
                .code(),
            "authority_native_job_membership_readback_invalid"
        );
        assert_eq!(
            unsafe {
                WaitForSingleObject(outside_process.raw(), JOB_FAULT_CONTAINMENT_TIMEOUT_MILLIS)
            },
            WAIT_OBJECT_0,
            "the unassigned suspended root must be directly contained"
        );
        assert_eq!(
            unsafe {
                WaitForSingleObject(first_process.raw(), JOB_FAULT_CONTAINMENT_TIMEOUT_MILLIS)
            },
            WAIT_OBJECT_0,
            "the already admitted root must be contained with the Job"
        );
        poll_until_fault_contained(&mut active);
        assert_eq!(active.root_membership_receipts().len(), 1);
        assert!(active.quarantined_root_processes.is_empty());
    }

    #[test]
    fn completion_roster_rejects_duplicates_unknown_exits_and_malformed_zero() {
        let root = 4_501;
        let hostile = [
            NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::NewProcess,
                process_id: Some(root + 1),
            },
            NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::ExitProcess,
                process_id: Some(root + 1),
            },
            NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::AbnormalExitProcess,
                process_id: None,
            },
            NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::ActiveProcessZero,
                process_id: Some(root),
            },
        ];
        for message in hostile {
            let mut tracker = NativeJobRosterTracker::from_pre_resume_root(root).unwrap();
            assert_eq!(
                tracker
                    .observe_terminal_advisory(message)
                    .unwrap_err()
                    .code(),
                "authority_native_job_completion_roster_invalid"
            );
        }
        for kind in [
            NativeJobCompletionKind::NewProcess,
            NativeJobCompletionKind::ExitProcess,
            NativeJobCompletionKind::AbnormalExitProcess,
        ] {
            let mut tracker = NativeJobRosterTracker::from_pre_resume_root(root).unwrap();
            let message = NativeJobCompletionMessage {
                kind,
                process_id: Some(root),
            };
            tracker
                .observe_terminal_advisory(message)
                .expect("one well-formed known advisory is allowed");
            assert_eq!(
                tracker
                    .observe_terminal_advisory(message)
                    .unwrap_err()
                    .code(),
                "authority_native_job_completion_roster_invalid",
                "duplicate {kind:?} must fail closed"
            );
        }
        let mut tracker = NativeJobRosterTracker::from_pre_resume_root(root).unwrap();
        tracker
            .observe_terminal_advisory(NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::ActiveProcessZero,
                process_id: None,
            })
            .expect("one well-formed Job-wide zero advisory is allowed");
        assert_eq!(
            tracker
                .observe_terminal_advisory(NativeJobCompletionMessage {
                    kind: NativeJobCompletionKind::ActiveProcessZero,
                    process_id: None,
                })
                .unwrap_err()
                .code(),
            "authority_native_job_completion_roster_invalid"
        );
        assert_eq!(
            tracker
                .observe_terminal_advisory(NativeJobCompletionMessage {
                    kind: NativeJobCompletionKind::NewProcess,
                    process_id: Some(root + 1),
                })
                .unwrap_err()
                .code(),
            "authority_native_job_completion_roster_invalid"
        );
    }

    #[test]
    fn terminal_completion_transcript_accepts_out_of_order_known_messages() {
        let root = 4_551;
        let expected = BTreeSet::from([root]);
        let mut tracker = NativeJobRosterTracker::from_pre_resume_root(root).unwrap();
        tracker
            .observe_terminal_advisory(NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::ExitProcess,
                process_id: Some(root),
            })
            .unwrap();
        tracker
            .observe_terminal_advisory(NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::ActiveProcessZero,
                process_id: None,
            })
            .unwrap();
        tracker
            .observe_terminal_advisory(NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::NewProcess,
                process_id: Some(root),
            })
            .unwrap();
        tracker
            .observe_terminal_advisory(NativeJobCompletionMessage {
                kind: NativeJobCompletionKind::AbnormalExitProcess,
                process_id: Some(root),
            })
            .unwrap();
        tracker.validate_terminal_transcript(&expected).unwrap();
        assert_eq!(tracker.new_process_events, 1);
        assert_eq!(tracker.exit_process_events, 1);
        assert_eq!(tracker.abnormal_exit_process_events, 1);
        assert!(tracker.active_process_zero_observed);
        assert_eq!(tracker.active_process_ids, expected);
    }

    #[test]
    fn terminal_completion_transcript_digest_binds_exact_message_identity_and_order() {
        let first = 4_561;
        let second = 4_562;
        let expected = BTreeSet::from([first, second]);
        let build = |new_order: [u32; 2]| {
            let mut tracker = NativeJobRosterTracker::from_pre_resume_root(first).unwrap();
            tracker
                .observe_additional_pre_resume_root(second, &expected)
                .unwrap();
            for process_id in new_order {
                tracker
                    .observe_terminal_advisory(NativeJobCompletionMessage {
                        kind: NativeJobCompletionKind::NewProcess,
                        process_id: Some(process_id),
                    })
                    .unwrap();
            }
            for process_id in [first, second] {
                tracker
                    .observe_terminal_advisory(NativeJobCompletionMessage {
                        kind: NativeJobCompletionKind::ExitProcess,
                        process_id: Some(process_id),
                    })
                    .unwrap();
            }
            tracker
                .observe_terminal_advisory(NativeJobCompletionMessage {
                    kind: NativeJobCompletionKind::ActiveProcessZero,
                    process_id: None,
                })
                .unwrap();
            tracker.validate_terminal_transcript(&expected).unwrap();
            tracker
        };
        let first_order = build([first, second]);
        let second_order = build([second, first]);
        assert_eq!(
            first_order.completion_message_count,
            second_order.completion_message_count
        );
        assert_eq!(
            first_order.new_process_events,
            second_order.new_process_events
        );
        assert_eq!(
            first_order.exit_process_events,
            second_order.exit_process_events
        );
        assert_ne!(
            first_order.completion_transcript_digest, second_order.completion_transcript_digest,
            "equal event counts cannot erase exact completion-message identity or order"
        );
    }

    #[test]
    fn terminal_proof_key_uses_the_volatile_source_wipe() {
        let mut source = [0xa5; 32];
        volatile_zero_terminal_key(&mut source);
        assert_eq!(source, [0; 32]);
    }

    #[test]
    fn terminal_drain_receipt_rejects_resealed_count_and_zero_residue_drift() {
        let key = NativeJobTerminalProofKey::generate().unwrap();
        let mut baseline = NativeJobTerminalDrainReceipt {
            object_id: 9,
            deterministic_name_digest: [0x80; 32],
            created_at: 7,
            authority_generation_digest: [0x81; 32],
            run_binding_digest: [0x86; 32],
            security_binding_digest: [0x82; 32],
            root_process_count: FIXED_ROOT_PROCESS_COUNT as u32,
            root_process_ids_digest: root_process_ids_digest(&BTreeSet::from([4_601, 4_602]))
                .unwrap(),
            root_process_epochs_digest: [0x83; 32],
            root_process_terminal_readback_digest: [0x84; 32],
            total_processes: 2,
            new_process_events: 2,
            exit_process_events: 2,
            abnormal_exit_process_events: 1,
            completion_message_count: 6,
            completion_transcript_digest: [0x85; 32],
            active_process_zero_observed: true,
            completion_port_drained: true,
            exact_empty_roster_readback: true,
            active_processes_zero: true,
            accounting_snapshot_stable: true,
            all_root_process_handles_signaled: true,
            all_root_process_handles_non_inheritable: true,
            receipt_mac: [0; 32],
        };
        baseline.receipt_mac = baseline.derive_mac(&key);
        baseline.validate(&key).unwrap();
        let mut mutations = Vec::new();
        let mut value = baseline.clone();
        value.run_binding_digest = [0; 32];
        mutations.push(value);
        let mut value = baseline.clone();
        value.root_process_count = 0;
        mutations.push(value);
        let mut value = baseline.clone();
        value.root_process_count = (FIXED_ROOT_PROCESS_COUNT + 1) as u32;
        mutations.push(value);
        let mut value = baseline.clone();
        value.root_process_ids_digest = [0; 32];
        mutations.push(value);
        let mut value = baseline.clone();
        value.root_process_epochs_digest = [0; 32];
        mutations.push(value);
        let mut value = baseline.clone();
        value.root_process_terminal_readback_digest = [0; 32];
        mutations.push(value);
        let mut value = baseline.clone();
        value.total_processes = 3;
        mutations.push(value);
        let mut value = baseline.clone();
        value.completion_port_drained = false;
        mutations.push(value);
        let mut value = baseline.clone();
        value.exact_empty_roster_readback = false;
        mutations.push(value);
        let mut value = baseline.clone();
        value.active_processes_zero = false;
        mutations.push(value);
        let mut value = baseline.clone();
        value.accounting_snapshot_stable = false;
        mutations.push(value);
        let mut value = baseline.clone();
        value.all_root_process_handles_signaled = false;
        mutations.push(value);
        let mut value = baseline.clone();
        value.active_process_zero_observed = false;
        mutations.push(value);
        let mut value = baseline.clone();
        value.completion_message_count = value.completion_message_count.saturating_add(1);
        mutations.push(value);
        let mut value = baseline.clone();
        value.completion_transcript_digest = [0; 32];
        mutations.push(value);
        let mut value = baseline.clone();
        value.new_process_events = (MAX_JOB_COMPLETION_EVENTS / 2 + 1) as u32;
        value.exit_process_events = value.new_process_events;
        value.abnormal_exit_process_events = 0;
        mutations.push(value);
        let mut value = baseline.clone();
        value.new_process_events = u32::MAX;
        value.exit_process_events = u32::MAX;
        value.abnormal_exit_process_events = 1;
        mutations.push(value);
        let mut value = baseline;
        value.all_root_process_handles_non_inheritable = false;
        mutations.push(value);
        for mut mutation in mutations {
            mutation.receipt_mac = mutation.derive_mac(&key);
            assert_eq!(
                mutation.validate(&key).unwrap_err().code(),
                "authority_native_job_terminal_drain_invalid"
            );
        }
    }

    #[test]
    fn terminal_receipt_accepts_no_advisory_messages_for_exact_root_accounting() {
        let key = NativeJobTerminalProofKey::generate().unwrap();
        for root_process_ids in [BTreeSet::from([4_611]), BTreeSet::from([4_611, 4_612])] {
            let root_process_count = root_process_ids.len() as u32;
            let mut receipt = NativeJobTerminalDrainReceipt {
                object_id: 10,
                deterministic_name_digest: [0x90; 32],
                created_at: 8,
                authority_generation_digest: [0x91; 32],
                run_binding_digest: [0x95; 32],
                security_binding_digest: [0x92; 32],
                root_process_count,
                root_process_ids_digest: root_process_ids_digest(&root_process_ids).unwrap(),
                root_process_epochs_digest: [0x93; 32],
                root_process_terminal_readback_digest: [0x94; 32],
                total_processes: root_process_count,
                new_process_events: 0,
                exit_process_events: 0,
                abnormal_exit_process_events: 0,
                completion_message_count: 0,
                completion_transcript_digest: Sha256::digest(JOB_TERMINAL_TRANSCRIPT_DOMAIN).into(),
                active_process_zero_observed: false,
                completion_port_drained: true,
                exact_empty_roster_readback: true,
                active_processes_zero: true,
                accounting_snapshot_stable: true,
                all_root_process_handles_signaled: true,
                all_root_process_handles_non_inheritable: true,
                receipt_mac: [0; 32],
            };
            receipt.receipt_mac = receipt.derive_mac(&key);
            receipt
                .validate(&key)
                .expect("exact kernel evidence does not require an advisory transcript");

            let mut drift = receipt;
            drift.total_processes = drift.total_processes.saturating_add(1);
            drift.receipt_mac = drift.derive_mac(&key);
            assert_eq!(
                drift.validate(&key).unwrap_err().code(),
                "authority_native_job_terminal_drain_invalid"
            );
        }
    }

    #[test]
    fn accounting_roster_accounting_races_are_pending_and_descendants_fail_closed() {
        let (spec, security) = test_specs();
        let native =
            WindowsNativeJob::create_from_spec(spec, &security).expect("create strict job");
        let empty = query_readback(&native.job, &native.completion_port, &native.security)
            .expect("empty readback");
        let live = NativeJobReadback {
            total_processes: 1,
            active_processes: 1,
            ..empty
        };
        let terminal = NativeJobReadback {
            total_processes: 1,
            active_processes: 0,
            ..empty
        };
        assert_eq!(
            classify_terminal_snapshot(&security, 1, live, &BTreeSet::new(), terminal)
                .expect("accounting transition is a healthy race"),
            None
        );
        assert_eq!(
            classify_terminal_snapshot(&security, 1, terminal, &BTreeSet::new(), terminal)
                .expect("stable empty terminal snapshot"),
            Some(terminal)
        );
        let descendant = NativeJobReadback {
            total_processes: 2,
            ..terminal
        };
        assert_eq!(
            classify_terminal_snapshot(&security, 1, descendant, &BTreeSet::new(), descendant,)
                .unwrap_err()
                .code(),
            "authority_native_job_descendant_handle_registry_unavailable"
        );
        let active_descendant = NativeJobReadback {
            active_processes: 1,
            ..descendant
        };
        assert_eq!(
            classify_terminal_snapshot(
                &security,
                1,
                active_descendant,
                &BTreeSet::from([9_901]),
                active_descendant,
            )
            .unwrap_err()
            .code(),
            "authority_native_job_descendant_handle_registry_unavailable"
        );
    }

    #[test]
    fn oversized_terminal_poll_and_infinite_completion_wait_are_rejected_without_blocking() {
        let (mut active, _process, _primary_thread) = create_suspended_test_root();
        active.begin_terminal_drain().expect("begin terminal drain");
        let started = Instant::now();
        assert_eq!(
            active.poll_terminal_drain(u32::MAX).unwrap_err().code(),
            "authority_native_job_terminal_timeout_invalid"
        );
        assert!(started.elapsed() < Duration::from_secs(1));
        assert!(matches!(
            active.terminal_state,
            NativeJobTerminalState::Terminating {
                termination_requested: false,
                ..
            }
        ));
        assert_eq!(
            dequeue_completion_message(
                &active.job.completion_port,
                active.job.receipt.object_id,
                u32::MAX,
            )
            .unwrap_err()
            .code(),
            "authority_native_job_completion_timeout_invalid"
        );
        active.request_termination().expect("cleanup root job");
        poll_until_complete(&mut active);
    }

    #[test]
    fn caller_process_handle_can_close_after_admission_without_losing_the_root_epoch() {
        let (mut active, process, primary_thread) = create_suspended_test_root();
        let process_id = unsafe { GetProcessId(process.raw()) };
        let held_handle = active
            .held_root_processes
            .get(&process_id)
            .expect("internally held root")
            .handle
            .raw();
        assert_ne!(
            unsafe { CompareObjectHandles(process.raw(), held_handle) },
            0
        );
        drop(primary_thread);
        drop(process);

        active
            .request_termination()
            .expect("termination uses only the internally held root object");
        assert_eq!(
            unsafe { WaitForSingleObject(held_handle, JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS) },
            WAIT_OBJECT_0
        );
        let receipt = poll_until_complete(&mut active);
        assert_eq!(receipt.root_process_count, 1);
        assert_eq!(receipt.total_processes, 1);
        assert_eq!(
            receipt.root_process_epochs_digest,
            root_process_epochs_digest(&active.held_root_processes).unwrap()
        );
        assert_eq!(
            receipt.root_process_terminal_readback_digest,
            root_process_terminal_readback_digest(
                &active.held_root_processes,
                &active.root_memberships,
            )
            .unwrap(),
            "terminal receipt is derived from the exact internally held process handle"
        );
    }

    #[test]
    fn zero_timeout_terminal_poll_retains_job_handles_and_blocks_new_admission() {
        let (mut active, process, _primary_thread) = create_suspended_test_root();
        let process_id = unsafe { GetProcessId(process.raw()) };
        let internally_held = active
            .held_root_processes
            .get(&process_id)
            .expect("internally held root");
        assert_ne!(
            unsafe { CompareObjectHandles(process.raw(), internally_held.handle.raw()) },
            0,
            "admission retains an exact duplicate of the process object"
        );
        active
            .begin_terminal_drain()
            .expect("begin terminal drain without caller-supplied handles");

        assert_eq!(
            active
                .poll_terminal_drain(0)
                .expect("nonblocking terminal poll"),
            NativeJobTerminalDrainStatus::Pending
        );
        active
            .validate_held_root_processes()
            .expect("pending retained the exact process, job, and completion handles");
        assert_eq!(
            active
                .poll_terminal_drain(0)
                .expect("repeat nonblocking terminal poll"),
            NativeJobTerminalDrainStatus::Pending
        );
        let inherited_events = inheritable_test_events::<3>();
        let inherited = borrowed_test_handles(&inherited_events);
        assert_eq!(
            active
                .prepare_additional_suspended_launch_attributes(&inherited)
                .unwrap_err()
                .code(),
            "authority_native_job_terminal_started"
        );
        active.request_termination().expect("cleanup root job");
        poll_until_complete(&mut active);
    }

    #[test]
    fn pre_termination_poll_identity_fault_contains_and_signals_the_real_root() {
        let (mut active, process, _primary_thread) = create_suspended_test_root();
        active
            .begin_terminal_drain()
            .expect("begin terminal drain without requesting termination");
        let NativeJobTerminalState::Terminating {
            root_process_epochs_digest,
            termination_requested: false,
            ..
        } = &mut active.terminal_state
        else {
            panic!("terminal drain must remain in the pre-termination phase");
        };
        root_process_epochs_digest[0] ^= 0x80;

        assert_eq!(
            active.poll_terminal_drain(0).unwrap_err().code(),
            "authority_native_job_terminal_process_identity_invalid"
        );
        assert!(active.admission_faulted);
        assert_eq!(
            active.terminal_state,
            NativeJobTerminalState::FaultContaining
        );
        assert_eq!(
            unsafe { WaitForSingleObject(process.raw(), 0) },
            WAIT_OBJECT_0,
            "faulting poll must signal the held root before returning its error"
        );
        poll_until_fault_contained(&mut active);
    }

    #[test]
    fn malformed_terminal_completion_latches_fault_held() {
        let (mut active, process, _primary_thread) = create_suspended_test_root();
        let process_id = unsafe { GetProcessId(process.raw()) };
        active.request_termination().expect("request termination");
        assert_ne!(
            unsafe {
                PostQueuedCompletionStatus(
                    active.job.completion_port.raw(),
                    JOB_OBJECT_MSG_EXIT_PROCESS,
                    active.job.receipt.object_id as usize + 1,
                    process_id as usize as *mut OVERLAPPED,
                )
            },
            0
        );
        assert_eq!(
            active.poll_terminal_drain(0).unwrap_err().code(),
            "authority_native_job_completion_key_mismatch"
        );
        assert_eq!(active.terminal_state, NativeJobTerminalState::FaultHeld);
        assert_eq!(
            active.poll_terminal_drain(0).unwrap_err().code(),
            "authority_native_job_terminal_fault_held"
        );
    }

    #[test]
    fn injected_unknown_and_duplicate_real_completion_messages_latch_fault_held() {
        {
            let (mut active, process, _primary_thread) =
                create_suspended_test_root_with_consumed_notification();
            let process_id = unsafe { GetProcessId(process.raw()) };
            assert_ne!(
                unsafe {
                    PostQueuedCompletionStatus(
                        active.job.completion_port.raw(),
                        JOB_OBJECT_MSG_EXIT_PROCESS,
                        active.job.receipt.object_id as usize,
                        process_id.saturating_add(1) as usize as *mut OVERLAPPED,
                    )
                },
                0
            );
            active.request_termination().expect("terminate hostile Job");
            assert_eq!(
                active.poll_terminal_drain(0).unwrap_err().code(),
                "authority_native_job_completion_roster_invalid"
            );
            assert_eq!(active.terminal_state, NativeJobTerminalState::FaultHeld);
        }

        let (mut active, process, _primary_thread) =
            create_suspended_test_root_with_consumed_notification();
        let process_id = unsafe { GetProcessId(process.raw()) };
        for _ in 0..2 {
            assert_ne!(
                unsafe {
                    PostQueuedCompletionStatus(
                        active.job.completion_port.raw(),
                        JOB_OBJECT_MSG_NEW_PROCESS,
                        active.job.receipt.object_id as usize,
                        process_id as usize as *mut OVERLAPPED,
                    )
                },
                0
            );
        }
        active.request_termination().expect("terminate hostile Job");
        assert_eq!(
            active.poll_terminal_drain(0).unwrap_err().code(),
            "authority_native_job_completion_roster_invalid"
        );
        assert_eq!(active.terminal_state, NativeJobTerminalState::FaultHeld);
    }

    #[test]
    fn completed_poll_latches_internal_process_epoch_drift() {
        let (mut active, process, _primary_thread) = create_suspended_test_root();
        let process_id = unsafe { GetProcessId(process.raw()) };
        active.request_termination().expect("request termination");
        assert_eq!(
            unsafe { WaitForSingleObject(process.raw(), JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS) },
            WAIT_OBJECT_0
        );
        poll_until_complete(&mut active);
        active
            .held_root_processes
            .get_mut(&process_id)
            .expect("internally held root")
            .creation_time ^= 1;
        assert_eq!(
            active.poll_terminal_drain(0).unwrap_err().code(),
            "authority_native_job_terminal_process_identity_invalid"
        );
        assert_eq!(active.terminal_state, NativeJobTerminalState::FaultHeld);
        assert_eq!(
            active.poll_terminal_drain(0).unwrap_err().code(),
            "authority_native_job_terminal_fault_held"
        );
    }

    #[test]
    fn completed_poll_rejects_resealed_job_instance_generation_and_security_context_drift() {
        assert_resealed_terminal_context_mutation_faults(|receipt| {
            receipt.object_id = receipt.object_id.wrapping_add(1).max(1);
        });
        assert_resealed_terminal_context_mutation_faults(|receipt| {
            receipt.deterministic_name_digest[0] ^= 0x40;
        });
        assert_resealed_terminal_context_mutation_faults(|receipt| {
            receipt.created_at = receipt.created_at.saturating_add(1);
        });
        assert_resealed_terminal_context_mutation_faults(|receipt| {
            receipt.authority_generation_digest[0] ^= 0x80;
        });
        assert_resealed_terminal_context_mutation_faults(|receipt| {
            receipt.run_binding_digest[0] ^= 0x80;
        });
        assert_resealed_terminal_context_mutation_faults(|receipt| {
            receipt.security_binding_digest[0] ^= 0x80;
        });
    }

    #[test]
    fn completed_poll_rejects_resealed_process_readback_and_event_transcript_drift() {
        assert_resealed_terminal_receipt_mutation_faults(
            |receipt| receipt.root_process_terminal_readback_digest[0] ^= 0x40,
            "authority_native_job_terminal_process_identity_invalid",
        );
        assert_resealed_terminal_receipt_mutation_faults(
            |receipt| receipt.completion_transcript_digest[0] ^= 0x20,
            "authority_native_job_terminal_process_identity_invalid",
        );
    }

    #[test]
    fn termination_request_is_idempotent_and_preserves_strict_readback() {
        let (mut active, process, _primary_thread) = create_suspended_test_root();

        active
            .request_termination()
            .expect("request bounded job termination");
        active
            .request_termination()
            .expect("repeat request is readback-only and idempotent");
        assert_eq!(
            unsafe { WaitForSingleObject(process.raw(), JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS) },
            WAIT_OBJECT_0,
            "termination reaches the held root process"
        );
        active
            .request_termination()
            .expect("terminated readback remains strict");
        let readback = query_readback(
            &active.job.job,
            &active.job.completion_port,
            &active.job.security,
        )
        .expect("read back terminated job");
        readback
            .validate_strict_terminal_job(&active.job.security)
            .expect("terminated job keeps its exact policy and zero active processes");
        assert_eq!(readback.total_processes, 1);

        let receipt = poll_until_complete(&mut active);
        assert_eq!(receipt.root_process_count, 1);
        assert_eq!(receipt.total_processes, 1);
        assert!(receipt.all_root_process_handles_signaled);
        assert!(receipt.accounting_snapshot_stable);
    }

    #[test]
    fn consuming_terminal_transition_queries_and_terminates_the_live_held_job_once() {
        let (active, process, _primary_thread) = create_suspended_test_root();
        assert_eq!(
            unsafe { WaitForSingleObject(process.raw(), 0) },
            WAIT_TIMEOUT,
            "the consuming transition starts from a live held root"
        );
        let live_process_id = unsafe { GetProcessId(process.raw()) };
        assert_eq!(
            active
                .held_root_processes
                .get(&live_process_id)
                .expect("exact internally held root")
                .terminal_exit_code()
                .unwrap_err()
                .code(),
            "authority_native_job_terminal_process_not_signaled",
            "a live process can never be projected as terminal proof material"
        );
        let expected_object_id = active.job.receipt.object_id;
        let expected_name = active.job.receipt.deterministic_name_digest;
        let expected_generation = active.job.security.authority_generation_digest;
        let expected_run_binding = active.job.run_binding_digest;
        let expected_security = active.job.security.binding_digest;
        let proof = active
            .into_terminal_proof()
            .expect("held Job issues one opaque proof after live terminal queries");
        assert_eq!(
            unsafe { WaitForSingleObject(process.raw(), 0) },
            WAIT_OBJECT_0,
            "proof cannot be issued while the held root remains live"
        );
        assert!(proof.receipt.completion_port_drained);
        assert!(proof.receipt.exact_empty_roster_readback);
        assert!(proof.receipt.all_root_process_handles_signaled);
        assert!(proof.receipt.all_root_process_handles_non_inheritable);
        assert_ne!(
            proof.receipt.root_process_terminal_readback_digest, [0; 32],
            "proof binds the held root's exact non-STILL_ACTIVE terminal readback"
        );
        assert_ne!(
            proof.receipt.completion_transcript_digest, [0; 32],
            "proof binds exact completion-message identity and order"
        );
        let mut exact_exit_code = STILL_ACTIVE as u32;
        assert_ne!(
            unsafe { GetExitCodeProcess(process.raw(), &mut exact_exit_code) },
            0,
            "the caller's duplicate confirms the same process object's terminal exit code"
        );
        assert_ne!(exact_exit_code, STILL_ACTIVE as u32);
        assert_eq!(
            proof.receipt.completion_message_count,
            proof
                .receipt
                .new_process_events
                .checked_add(proof.receipt.exit_process_events)
                .and_then(|value| value.checked_add(proof.receipt.abnormal_exit_process_events))
                .and_then(|value| {
                    value.checked_add(u32::from(proof.receipt.active_process_zero_observed))
                })
                .unwrap()
        );
        let completion = proof
            .consume_for_runner(
                expected_object_id,
                &expected_name,
                &expected_generation,
                &expected_run_binding,
                &expected_security,
            )
            .expect("the exact runner binding consumes the proof once");
        assert!(format!("{completion:?}").contains("verified-and-redacted"));
    }

    #[test]
    fn terminal_proof_mac_rejects_field_drift_and_a_recreated_same_spec_job() {
        let (active, _process, _primary_thread) = create_suspended_test_root();
        let spec = NativeJobSpec {
            object_id: active.job.receipt.object_id,
            deterministic_name_digest: active.job.receipt.deterministic_name_digest,
            run_binding_digest: active.job.run_binding_digest,
            security_binding_digest: active.job.receipt.security_binding_digest,
            created_at: active.job.receipt.created_at,
        };
        let security = active.job.security.clone();
        let proof = active.into_terminal_proof().expect("first Job proof");
        let NativeJobTerminalProof {
            mut receipt,
            key: first_instance_key,
        } = proof;
        receipt.completion_message_count = receipt.completion_message_count.saturating_add(1);
        assert_eq!(
            receipt.validate(&first_instance_key).unwrap_err().code(),
            "authority_native_job_terminal_drain_invalid"
        );
        receipt.completion_message_count = receipt.completion_message_count.saturating_sub(1);
        receipt.root_process_epochs_digest[0] ^= 0x80;
        assert_eq!(
            receipt.validate(&first_instance_key).unwrap_err().code(),
            "authority_native_job_terminal_authentication_invalid"
        );
        receipt.root_process_epochs_digest[0] ^= 0x80;
        receipt.root_process_terminal_readback_digest[0] ^= 0x40;
        assert_eq!(
            receipt.validate(&first_instance_key).unwrap_err().code(),
            "authority_native_job_terminal_authentication_invalid"
        );
        receipt.root_process_terminal_readback_digest[0] ^= 0x40;
        receipt.completion_transcript_digest[0] ^= 0x20;
        assert_eq!(
            receipt.validate(&first_instance_key).unwrap_err().code(),
            "authority_native_job_terminal_authentication_invalid"
        );
        receipt.completion_transcript_digest[0] ^= 0x20;
        receipt
            .validate(&first_instance_key)
            .expect("original instance key validates the restored proof");

        let replacement = WindowsNativeJob::create_from_spec(spec, &security)
            .expect("same-spec Job can be recreated only after the first held Job is consumed");
        assert_eq!(
            receipt.validate_for_job(&replacement).unwrap_err().code(),
            "authority_native_job_terminal_authentication_invalid",
            "a fresh per-instance key rejects stale proof replay"
        );
    }

    #[test]
    fn first_root_admission_succeeds_when_the_notification_is_missing() {
        let (mut active, process, _primary_thread) =
            create_suspended_test_root_with_consumed_notification();
        assert_eq!(active.root_membership_receipts().len(), 1);
        active
            .request_termination()
            .expect("terminate root admitted from kernel authority");
        assert_eq!(
            unsafe { WaitForSingleObject(process.raw(), JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS) },
            WAIT_OBJECT_0
        );
        let receipt = poll_until_complete(&mut active);
        assert_eq!(receipt.new_process_events, 0);
    }

    #[test]
    fn child_job_observation_is_role_epoch_thread_and_live_roster_bound() {
        let (mut active, process, primary_thread) = create_suspended_test_root();
        let process_id = unsafe { GetProcessId(process.raw()) };
        let primary_thread_id = unsafe { GetThreadId(primary_thread.raw()) };
        let process_key = ProcessKey {
            pid: process_id,
            creation_time: process_creation_time(process.raw()).expect("held process epoch"),
        };
        let observation = active
            .observe_child_root(
                ChildBootstrapRole::BridgeLauncher,
                process_key,
                primary_thread_id,
            )
            .expect("typed held-Job child observation");
        assert_eq!(observation.role(), ChildBootstrapRole::BridgeLauncher);
        assert_eq!(observation.process_key(), process_key);
        assert_eq!(observation.primary_thread_id(), primary_thread_id);
        assert_eq!(
            observation.authority_generation_digest(),
            &active.job.security.authority_generation_digest
        );
        assert_eq!(
            observation.run_binding_digest(),
            &active.job.run_binding_digest
        );
        assert_eq!(
            observation.runner_launch_binding(),
            active.job.runner_launch_binding().unwrap()
        );
        assert_ne!(observation.membership_epoch_source(), &[0; 32]);
        assert_ne!(observation.observation_digest(), &[0; 32]);
        assert_ne!(
            observation.membership_epoch_source(),
            observation.observation_digest()
        );

        for result in [
            active.observe_child_root(
                ChildBootstrapRole::LifecycleDriver,
                process_key,
                primary_thread_id,
            ),
            active.observe_child_root(
                ChildBootstrapRole::BridgeLauncher,
                ProcessKey {
                    creation_time: process_key.creation_time ^ 1,
                    ..process_key
                },
                primary_thread_id,
            ),
            active.observe_child_root(
                ChildBootstrapRole::BridgeLauncher,
                process_key,
                primary_thread_id ^ 1,
            ),
        ] {
            assert_eq!(
                result.unwrap_err().code(),
                "authority_native_child_job_observation_invalid"
            );
        }

        active.request_termination().expect("terminate held root");
        assert_eq!(
            unsafe { WaitForSingleObject(process.raw(), JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS) },
            WAIT_OBJECT_0
        );
        assert_eq!(
            active
                .observe_child_root(
                    ChildBootstrapRole::BridgeLauncher,
                    process_key,
                    primary_thread_id,
                )
                .unwrap_err()
                .code(),
            "authority_native_job_terminal_started"
        );
        let _ = poll_until_complete(&mut active);
    }

    #[test]
    fn delayed_and_missing_notifications_do_not_control_second_root_admission() {
        let (mut active, first_process, _first_thread) =
            create_suspended_test_root_with_consumed_notification();
        let first_process_id = unsafe { GetProcessId(first_process.raw()) };
        assert_ne!(
            unsafe {
                PostQueuedCompletionStatus(
                    active.job.completion_port.raw(),
                    JOB_OBJECT_MSG_NEW_PROCESS,
                    active.job.receipt.object_id as usize,
                    first_process_id as usize as *mut OVERLAPPED,
                )
            },
            0,
            "post a delayed advisory notification for the already-admitted first root"
        );

        let inherited_events = inheritable_test_events::<3>();
        let inherited = borrowed_test_handles(&inherited_events);
        let (second_process, second_thread, second_process_id, second_thread_id, binding) = {
            let mut attributes = active
                .prepare_additional_suspended_launch_attributes(&inherited)
                .expect("a delayed first-root notification must not reject admission");
            create_suspended_process_with_attributes(&mut attributes)
        };
        let mut observed_new_processes = BTreeSet::new();
        for _ in 0..2 {
            let message = dequeue_completion_message(
                &active.job.completion_port,
                active.job.receipt.object_id,
                JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS,
            )
            .expect("read controlled admission notification")
            .expect("both controlled notifications must arrive");
            assert_eq!(message.kind, NativeJobCompletionKind::NewProcess);
            observed_new_processes.insert(message.process_id.expect("new-process id"));
        }
        assert_eq!(
            observed_new_processes,
            BTreeSet::from([first_process_id, second_process_id])
        );
        assert!(dequeue_completion_message(
            &active.job.completion_port,
            active.job.receipt.object_id,
            0,
        )
        .expect("verify controlled queue state")
        .is_none());

        active
            .revalidate_additional_root_before_resume(
                unsafe { borrowed_handle(second_process.raw()) },
                unsafe { borrowed_handle(second_thread.raw()) },
                second_process_id,
                second_thread_id,
                &binding,
            )
            .expect("kernel authority admits the second root without a queued notification");
        active
            .request_termination()
            .expect("terminate both admitted roots");
        for process in [&first_process, &second_process] {
            assert_eq!(
                unsafe {
                    WaitForSingleObject(process.raw(), JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS)
                },
                WAIT_OBJECT_0
            );
        }
        let receipt = poll_until_complete(&mut active);
        assert_eq!(receipt.root_process_count, 2);
        assert_eq!(receipt.new_process_events, 0);
    }

    #[test]
    fn two_suspended_roots_are_admitted_at_creation_and_retained_through_terminal_poll() {
        let (mut active, first_process, _first_thread) = create_suspended_test_root();
        let inherited_events = inheritable_test_events::<3>();
        let inherited = borrowed_test_handles(&inherited_events);
        let (second_process, second_thread, second_process_id, second_thread_id, binding) = {
            let mut attributes = active
                .prepare_additional_suspended_launch_attributes(&inherited)
                .expect("prepare exact second-root launch attributes");
            create_suspended_process_with_attributes(&mut attributes)
        };
        active
            .revalidate_additional_root_before_resume(
                unsafe { borrowed_handle(second_process.raw()) },
                unsafe { borrowed_handle(second_thread.raw()) },
                second_process_id,
                second_thread_id,
                &binding,
            )
            .expect("revalidate second create-time root before resume");

        let first_process_id = unsafe { GetProcessId(first_process.raw()) };
        let root_process_ids = BTreeSet::from([first_process_id, second_process_id]);
        assert_eq!(active.root_membership_receipts().len(), 2);
        assert_eq!(
            active
                .root_membership_receipts()
                .keys()
                .copied()
                .collect::<BTreeSet<_>>(),
            root_process_ids
        );
        assert_eq!(
            query_process_roster(&active.job.job).expect("exact two-root kernel roster"),
            root_process_ids
        );
        query_readback(
            &active.job.job,
            &active.job.completion_port,
            &active.job.security,
        )
        .expect("two-root readback")
        .validate_strict_live_job(&active.job.security, 2)
        .expect("two exact suspended roots are live");
        assert_ne!(
            root_process_ids_digest(&root_process_ids).expect("root-id binding"),
            active_roster_digest(&root_process_ids).expect("active-roster binding"),
            "root identities use a distinct digest domain"
        );

        active
            .request_termination()
            .expect("terminate both suspended roots");
        active
            .request_termination()
            .expect("two-root termination is idempotent");
        for process in [&first_process, &second_process] {
            assert_eq!(
                unsafe {
                    WaitForSingleObject(process.raw(), JOB_INITIAL_MEMBERSHIP_TIMEOUT_MILLIS)
                },
                WAIT_OBJECT_0,
                "held root reaches terminal state"
            );
        }
        active
            .request_termination()
            .expect("terminal kernel readback remains strict");
        let receipt = poll_until_complete(&mut active);
        assert_eq!(receipt.root_process_count, 2);
        assert_eq!(receipt.total_processes, 2);
        assert_eq!(
            receipt.root_process_ids_digest,
            root_process_ids_digest(&root_process_ids).unwrap()
        );
        assert_eq!(
            receipt.root_process_epochs_digest,
            root_process_epochs_digest(&active.held_root_processes).unwrap()
        );
        assert_eq!(
            receipt.root_process_terminal_readback_digest,
            root_process_terminal_readback_digest(
                &active.held_root_processes,
                &active.root_memberships,
            )
            .unwrap(),
            "both bridge and driver terminal exit codes are role and epoch bound"
        );
    }

    #[test]
    fn existing_deterministic_name_is_rejected_without_reconfiguring_owner() {
        let (spec, security) = test_specs();
        let owner = WindowsNativeJob::create_from_spec(spec, &security).expect("create owner");
        let error = WindowsNativeJob::create_from_spec(spec, &security).unwrap_err();
        assert!(matches!(
            error.code(),
            "authority_native_job_name_already_exists" | "authority_native_job_create_failed"
        ));
        owner.revalidate_empty().expect("owner remains unchanged");
    }

    #[test]
    fn closing_held_handles_removes_the_empty_named_job() {
        let (spec, security) = test_specs();
        let first = WindowsNativeJob::create_from_spec(spec, &security).expect("first create");
        drop(first);
        let second =
            WindowsNativeJob::create_from_spec(spec, &security).expect("recreate after close");
        second.revalidate_empty().expect("recreated job valid");
    }

    #[test]
    fn invalid_identity_is_rejected_before_any_kernel_create() {
        let (mut spec, security) = test_specs();
        spec.object_id = 0;
        assert_eq!(
            WindowsNativeJob::create_from_spec(spec, &security)
                .unwrap_err()
                .code(),
            "authority_native_job_spec_invalid"
        );
        (spec, _) = test_specs();
        spec.deterministic_name_digest = [0; 32];
        assert_eq!(
            WindowsNativeJob::create_from_spec(spec, &security)
                .unwrap_err()
                .code(),
            "authority_native_job_spec_invalid"
        );
        (spec, _) = test_specs();
        spec.run_binding_digest = [0; 32];
        assert_eq!(
            WindowsNativeJob::create_from_spec(spec, &security)
                .unwrap_err()
                .code(),
            "authority_native_job_spec_invalid"
        );
    }

    #[test]
    fn security_binding_mismatch_is_rejected_before_any_kernel_create() {
        let (mut spec, security) = test_specs();
        spec.security_binding_digest = [0x77; 32];
        assert_eq!(
            WindowsNativeJob::create_from_spec(spec, &security)
                .unwrap_err()
                .code(),
            "authority_native_job_security_binding_mismatch"
        );
    }

    #[test]
    fn production_security_spec_is_generation_bound_and_system_owned() {
        let first_identity =
            AuthorityRuntimeIdentity::new([21; 32], [22; 32], [23; 32], [24; 32], [25; 32])
                .expect("first identity");
        let second_identity =
            AuthorityRuntimeIdentity::new([31; 32], [22; 32], [23; 32], [24; 32], [25; 32])
                .expect("generation-only replacement identity");
        let first = NativeJobSecuritySpec::from_runtime_identity(&first_identity)
            .expect("first production security spec");
        let second = NativeJobSecuritySpec::from_runtime_identity(&second_identity)
            .expect("second production security spec");
        assert_eq!(first.owner_sid, LOCAL_SYSTEM_SID);
        assert_eq!(first.service_sid, authority_service_sid());
        assert!(!first.test_owner_override);
        assert_ne!(first.binding_digest, second.binding_digest);
        let descriptor =
            OwnedSecurityDescriptor::from_spec(&first).expect("production descriptor builds");
        let readback = verify_security_descriptor(descriptor.raw(), &first)
            .expect("production descriptor is exact");
        assert_eq!(readback.binding_digest, first.binding_digest);
        assert_eq!(readback.system_access_mask, JOB_OBJECT_ALL_ACCESS_EXACT);
        assert_eq!(readback.service_access_mask, SERVICE_JOB_ACCESS_EXACT);
    }

    #[test]
    fn policy_and_security_must_bind_the_same_runtime_identity() {
        let identity = test_identity(35);
        let security =
            NativeJobSecuritySpec::from_runtime_identity(&identity).expect("security spec");
        let mut policy = super::super::super::runtime_test_policy(
            identity.binding_digest(),
            [36; 32],
            [37; 32],
            [38; 32],
        );
        policy.job_security_binding_digest = security.binding_digest;
        policy.runner_policy_digest =
            super::super::super::canonical_supervisor_policy_digest(&policy);
        policy.run_binding_digest = super::super::super::derive_run_binding_digest(
            &policy.authority_identity_digest,
            &policy.ticket_digest,
            &policy.service_instance_digest,
            &policy.runner_policy_digest,
        );
        let spec = NativeJobSpec::from_policy(&policy, &security, 11)
            .expect("matching policy and security");
        assert_eq!(spec.security_binding_digest, security.binding_digest);

        let replacement = NativeJobSecuritySpec::from_runtime_identity(&test_identity(45))
            .expect("replacement security spec");
        assert_eq!(
            NativeJobSpec::from_policy(&policy, &replacement, 11)
                .unwrap_err()
                .code(),
            "authority_native_job_security_identity_mismatch"
        );
    }

    #[test]
    fn descriptor_readback_rejects_unprotected_extra_or_overpowered_aces() {
        let identity = test_identity(41);
        let spec =
            NativeJobSecuritySpec::for_test_current_owner(&identity).expect("test security spec");
        let system_ace = format!("(A;;0x{JOB_OBJECT_ALL_ACCESS_EXACT:08x};;;SY)");
        let service_ace = format!(
            "(A;;0x{SERVICE_JOB_ACCESS_EXACT:08x};;;{})",
            spec.service_sid
        );
        let invalid = [
            format!("O:{}D:{system_ace}{service_ace}", spec.owner_sid),
            format!(
                "O:{}D:P{system_ace}{service_ace}(A;;GA;;;BA)",
                spec.owner_sid
            ),
            format!(
                "O:{}D:P{system_ace}{service_ace}(A;;0x000c0000;;;{})",
                spec.owner_sid, spec.owner_sid
            ),
            format!(
                "O:{}D:P{system_ace}(A;;GA;;;{})",
                spec.owner_sid, spec.service_sid
            ),
            format!("O:{}D:P{service_ace}{system_ace}", spec.owner_sid),
            format!("O:{}D:P{system_ace}", spec.owner_sid),
        ];
        for value in invalid {
            let descriptor = OwnedSecurityDescriptor::from_sddl(&value)
                .expect("negative fixture descriptor builds");
            assert_eq!(
                verify_security_descriptor(descriptor.raw(), &spec)
                    .unwrap_err()
                    .code(),
                "authority_native_job_security_readback_invalid"
            );
        }
    }

    #[test]
    fn strict_readback_rejects_every_forbidden_or_unbound_state() {
        let (_, security) = test_specs();
        let descriptor =
            OwnedSecurityDescriptor::from_spec(&security).expect("baseline descriptor");
        let security_readback =
            verify_security_descriptor(descriptor.raw(), &security).expect("baseline security");
        let baseline = NativeJobReadback {
            limit_flags: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            active_process_limit: 0,
            total_processes: 0,
            active_processes: 0,
            job_handle_inheritable: false,
            completion_port_handle_inheritable: false,
            security: security_readback,
        };
        baseline
            .validate_empty_strict_job(&security)
            .expect("baseline");
        let mutations = [
            NativeJobReadback {
                limit_flags: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK,
                ..baseline
            },
            NativeJobReadback {
                limit_flags: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                    | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK,
                ..baseline
            },
            NativeJobReadback {
                limit_flags: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
                active_process_limit: 1,
                ..baseline
            },
            NativeJobReadback {
                total_processes: 1,
                ..baseline
            },
            NativeJobReadback {
                active_processes: 1,
                ..baseline
            },
            NativeJobReadback {
                job_handle_inheritable: true,
                ..baseline
            },
            NativeJobReadback {
                completion_port_handle_inheritable: true,
                ..baseline
            },
            NativeJobReadback {
                limit_flags: 0,
                ..baseline
            },
        ];
        for mutation in mutations {
            assert_eq!(
                mutation
                    .validate_empty_strict_job(&security)
                    .unwrap_err()
                    .code(),
                "authority_native_job_readback_invalid"
            );
        }

        let live = NativeJobReadback {
            total_processes: 1,
            active_processes: 1,
            ..baseline
        };
        live.validate_strict_live_job(&security, 1)
            .expect("one exact live process");
        assert_eq!(
            NativeJobReadback {
                total_processes: 2,
                ..live
            }
            .validate_strict_live_job(&security, 1)
            .unwrap_err()
            .code(),
            "authority_native_job_readback_invalid"
        );

        let terminating = NativeJobReadback {
            total_processes: 2,
            active_processes: 1,
            ..baseline
        };
        terminating
            .validate_strict_terminating_job(&security, 1)
            .expect("one admitted root with one completed descendant");
        terminating
            .validate_strict_terminating_job(&security, 2)
            .expect("two admitted roots");
        for (mutation, admitted_roots) in [
            (terminating, 0),
            (terminating, 3),
            (
                NativeJobReadback {
                    total_processes: 1,
                    ..terminating
                },
                2,
            ),
            (
                NativeJobReadback {
                    total_processes: 1,
                    active_processes: 2,
                    ..terminating
                },
                1,
            ),
        ] {
            assert_eq!(
                mutation
                    .validate_strict_terminating_job(&security, admitted_roots)
                    .unwrap_err()
                    .code(),
                "authority_native_job_readback_invalid"
            );
        }

        NativeJobReadback {
            total_processes: 2,
            active_processes: 0,
            ..baseline
        }
        .validate_strict_terminal_job(&security)
        .expect("terminal accounting preserves the cumulative process count");

        let security_mutations = [
            NativeJobSecurityReadback {
                binding_digest: [0x55; 32],
                ..security_readback
            },
            NativeJobSecurityReadback {
                owner_exact: false,
                ..security_readback
            },
            NativeJobSecurityReadback {
                dacl_present: false,
                ..security_readback
            },
            NativeJobSecurityReadback {
                dacl_defaulted: true,
                ..security_readback
            },
            NativeJobSecurityReadback {
                dacl_protected: false,
                ..security_readback
            },
            NativeJobSecurityReadback {
                ace_count: 3,
                ..security_readback
            },
            NativeJobSecurityReadback {
                service_access_mask: SERVICE_JOB_ACCESS_EXACT | 0x0004_0000,
                ..security_readback
            },
        ];
        for mutation in security_mutations {
            assert_eq!(
                mutation.validate(&security).unwrap_err().code(),
                "authority_native_job_security_readback_invalid"
            );
        }
    }

    #[test]
    fn deterministic_name_contains_only_fixed_prefix_and_lower_hex_digest() {
        let spec = NativeJobSpec {
            object_id: 7,
            deterministic_name_digest: [0xab; 32],
            run_binding_digest: [0xbc; 32],
            security_binding_digest: [0xcd; 32],
            created_at: 9,
        };
        let encoded = spec.deterministic_name();
        let name = String::from_utf16(&encoded[..encoded.len() - 1]).expect("valid UTF-16");
        assert_eq!(name, format!("{JOB_NAME_PREFIX}{}", "ab".repeat(32)));
        assert_eq!(encoded.last(), Some(&0));
    }
}
