//! Held-handle adapter for one candidate-validation service start.
//!
//! The persistent service command remains the fixed runtime `--service`
//! command. The five one-use locator values exist only in the transient SCM
//! start vector and this module never formats them for diagnostics.

use super::{
    bootstrap_activation::{
        CandidateActivationBinding, CandidateCredentialPhase, CandidateProcessEvidence,
        CandidateServiceStartLocator,
    },
    candidate_activation_orchestrator::{
        candidate_exact_service_identity_digest, CandidateActivationSealReadyProjection,
        CandidateStartedReadback,
    },
    candidate_client_windows::{HeldCandidateServer, HeldServerImage},
    preview::ExactTargetServicePlan,
    worker_store_windows::NativeCandidateCredentialLease,
    AuthorityMaintenanceError, AuthorityMaintenanceOperation, SERVICE_SID,
};
use crate::primitive_evidence_authority_windows::{
    AuthorityLayout, AUTHORITY_REQUIRED_PRIVILEGES, AUTHORITY_SERVICE_ACCOUNT,
    AUTHORITY_SERVICE_DISPLAY_NAME, AUTHORITY_SERVICE_NAME, AUTHORITY_SERVICE_SID_TYPE_RESTRICTED,
};
use sha2::{Digest, Sha256};
use std::{
    ffi::OsString,
    mem::{size_of, zeroed, MaybeUninit},
    os::windows::{
        ffi::OsStrExt,
        io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle},
    },
    path::Path,
    ptr,
    time::{Duration, Instant},
};
use windows_sys::{
    core::PCWSTR,
    Win32::{
        Foundation::{
            GetLastError, LocalFree, ERROR_INSUFFICIENT_BUFFER, HANDLE, INVALID_HANDLE_VALUE,
            STILL_ACTIVE, WAIT_OBJECT_0,
        },
        Security::{
            Authorization::{
                ConvertSecurityDescriptorToStringSecurityDescriptorW,
                ConvertStringSecurityDescriptorToSecurityDescriptorW, ConvertStringSidToSidW,
                SDDL_REVISION_1,
            },
            GetLengthSid, GetTokenInformation, IsTokenRestricted, IsValidSid, LookupPrivilegeNameW,
            TokenGroups, TokenIntegrityLevel, TokenMandatoryPolicy, TokenPrimary, TokenPrivileges,
            TokenRestrictedSids, TokenSessionId, TokenType, TokenUser, DACL_SECURITY_INFORMATION,
            GROUP_SECURITY_INFORMATION, LABEL_SECURITY_INFORMATION, LUID_AND_ATTRIBUTES,
            OWNER_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR, PSID, SE_PRIVILEGE_ENABLED,
            SE_PRIVILEGE_ENABLED_BY_DEFAULT, SE_PRIVILEGE_USED_FOR_ACCESS, SID_AND_ATTRIBUTES,
            TOKEN_GROUPS, TOKEN_MANDATORY_LABEL, TOKEN_MANDATORY_POLICY,
            TOKEN_MANDATORY_POLICY_NO_WRITE_UP, TOKEN_PRIVILEGES, TOKEN_QUERY, TOKEN_TYPE,
            TOKEN_USER,
        },
        Storage::FileSystem::{READ_CONTROL, SYNCHRONIZE},
        System::{
            Services::{
                CloseServiceHandle, OpenSCManagerW, OpenServiceW, QueryServiceConfig2W,
                QueryServiceConfigW, QueryServiceObjectSecurity, QueryServiceStatusEx,
                StartServiceW, QUERY_SERVICE_CONFIGW, SC_HANDLE, SC_MANAGER_CONNECT,
                SC_STATUS_PROCESS_INFO, SERVICE_CONFIG_REQUIRED_PRIVILEGES_INFO,
                SERVICE_CONFIG_SERVICE_SID_INFO, SERVICE_DEMAND_START, SERVICE_ERROR_NORMAL,
                SERVICE_QUERY_CONFIG, SERVICE_QUERY_STATUS, SERVICE_REQUIRED_PRIVILEGES_INFOW,
                SERVICE_SID_INFO, SERVICE_START, SERVICE_START_PENDING, SERVICE_STATUS_PROCESS,
                SERVICE_STOPPED, SERVICE_WIN32_OWN_PROCESS,
            },
            SystemServices::{
                SE_GROUP_ENABLED, SE_GROUP_ENABLED_BY_DEFAULT, SE_GROUP_INTEGRITY,
                SE_GROUP_INTEGRITY_ENABLED, SE_GROUP_LOGON_ID, SE_GROUP_MANDATORY, SE_GROUP_OWNER,
            },
            Threading::{
                GetExitCodeProcess, GetProcessId, GetProcessTimes, OpenProcess, OpenProcessToken,
                WaitForSingleObject, INFINITE, PROCESS_QUERY_LIMITED_INFORMATION,
            },
        },
    },
};

const CANDIDATE_SERVICE_START_ARGUMENT_COUNT: u32 = 5;
const CANDIDATE_SERVICE_START_TIMEOUT: Duration = Duration::from_secs(20);
const CANDIDATE_SERVICE_START_POLL_INTERVAL: Duration = Duration::from_millis(25);
const MAX_CANDIDATE_TOKEN_READBACK_BYTES: usize = 256 * 1024;
const CANDIDATE_RUNTIME_TOKEN_DOMAIN: &[u8] = b"vrcforge-authority-candidate-runtime-token-v2\0";
const CANDIDATE_RUNTIME_TOKEN_GROUP_SET_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-runtime-token-group-set-v2\0";
const CANDIDATE_START_CONTAINMENT_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-start-containment-v1\0";
const CANDIDATE_ABORT_CONTAINMENT_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-abort-containment-v1\0";
const CANDIDATE_START_TERMINAL_DOMAIN: &[u8] = b"vrcforge-authority-candidate-start-terminal-v1\0";
const CHANGE_NOTIFY_PRIVILEGE: &str = "SeChangeNotifyPrivilege";
const LOCAL_SYSTEM_SID: &str = "S-1-5-18";
const WORLD_SID: &str = "S-1-1-0";
const WRITE_RESTRICTED_SID: &str = "S-1-5-33";
const SYSTEM_INTEGRITY_SID: &str = "S-1-16-16384";
const SYSTEM_INTEGRITY_ATTRIBUTES: u32 = (SE_GROUP_INTEGRITY | SE_GROUP_INTEGRITY_ENABLED) as u32;
const SERVICE_SID_ENABLED_ATTRIBUTES: u32 =
    (SE_GROUP_ENABLED_BY_DEFAULT | SE_GROUP_ENABLED | SE_GROUP_OWNER) as u32;
const SERVICE_SID_RESTRICTING_ATTRIBUTES: u32 = 0;
const SERVICE_LOGON_SID_ENABLED_ATTRIBUTES: u32 =
    (SE_GROUP_MANDATORY | SE_GROUP_ENABLED_BY_DEFAULT | SE_GROUP_ENABLED | SE_GROUP_LOGON_ID)
        as u32;
const TOKEN_PRIVILEGE_ALLOWED_ATTRIBUTES: u32 =
    SE_PRIVILEGE_ENABLED_BY_DEFAULT | SE_PRIVILEGE_ENABLED | SE_PRIVILEGE_USED_FOR_ACCESS;
const CANDIDATE_SERVICE_ACCESS: u32 =
    SERVICE_START | SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS | READ_CONTROL;
const SERVICE_SECURITY_INFORMATION: u32 = OWNER_SECURITY_INFORMATION
    | GROUP_SECURITY_INFORMATION
    | DACL_SECURITY_INFORMATION
    | LABEL_SECURITY_INFORMATION;

#[derive(Clone, Copy, PartialEq, Eq)]
struct CandidateCredentialReadback {
    volume_serial: u64,
    file_id: [u8; 16],
    bytes_sha256: [u8; 32],
}

impl CandidateCredentialReadback {
    fn from_verified_identity(
        identity: (u64, [u8; 16], [u8; 32]),
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            volume_serial: identity.0,
            file_id: identity.1,
            bytes_sha256: identity.2,
        };
        if value.volume_serial == 0
            || value.file_id.iter().all(|byte| *byte == 0)
            || value.bytes_sha256.iter().all(|byte| *byte == 0)
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_credential_readback_invalid",
            ));
        }
        Ok(value)
    }
}

#[derive(Clone, PartialEq, Eq)]
struct ObservedTokenPrivilege {
    name: String,
    attributes: u32,
}

#[derive(Clone, PartialEq, Eq)]
struct ObservedTokenGroup {
    sid: Vec<u8>,
    attributes: u32,
}

#[derive(Clone, PartialEq, Eq)]
struct ObservedTokenPrincipal {
    user_sid: Vec<u8>,
    user_attributes: u32,
    token_type: TOKEN_TYPE,
    session_id: u32,
    integrity_sid: Vec<u8>,
    integrity_attributes: u32,
    mandatory_policy: u32,
}

fn validate_token_principal(
    principal: &ObservedTokenPrincipal,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let expected_user = sid_bytes(OwnedSid::from_text(LOCAL_SYSTEM_SID)?.0)?;
    let expected_integrity = sid_bytes(OwnedSid::from_text(SYSTEM_INTEGRITY_SID)?.0)?;
    if principal.user_sid != expected_user
        || principal.user_attributes != 0
        || principal.token_type != TokenPrimary
        || principal.session_id != 0
        || principal.integrity_sid != expected_integrity
        || principal.integrity_attributes != SYSTEM_INTEGRITY_ATTRIBUTES
        || principal.mandatory_policy != TOKEN_MANDATORY_POLICY_NO_WRITE_UP
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_principal_mismatch",
        ));
    }
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-candidate-runtime-token-principal-v1\0");
    digest.update((principal.user_sid.len() as u32).to_be_bytes());
    digest.update(&principal.user_sid);
    digest.update(principal.user_attributes.to_be_bytes());
    digest.update(principal.token_type.to_be_bytes());
    digest.update(principal.session_id.to_be_bytes());
    digest.update((principal.integrity_sid.len() as u32).to_be_bytes());
    digest.update(&principal.integrity_sid);
    digest.update(principal.integrity_attributes.to_be_bytes());
    digest.update(principal.mandatory_policy.to_be_bytes());
    Ok(digest.finalize().into())
}

fn normalized_token_group_set(
    mut groups: Vec<ObservedTokenGroup>,
) -> Result<Vec<ObservedTokenGroup>, AuthorityMaintenanceError> {
    if groups.is_empty()
        || groups.len() > 1024
        || groups
            .iter()
            .any(|group| group.sid.is_empty() || group.sid.len() > 68)
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_groups_invalid",
        ));
    }
    groups.sort_by(|left, right| {
        left.sid
            .cmp(&right.sid)
            .then_with(|| left.attributes.cmp(&right.attributes))
    });
    if groups.windows(2).any(|pair| pair[0].sid == pair[1].sid) {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_groups_invalid",
        ));
    }
    Ok(groups)
}

fn token_group_set_digest(role: u8, groups: &[ObservedTokenGroup]) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_RUNTIME_TOKEN_GROUP_SET_DOMAIN);
    digest.update([role]);
    digest.update((groups.len() as u32).to_be_bytes());
    for group in groups {
        digest.update((group.sid.len() as u32).to_be_bytes());
        digest.update(&group.sid);
        digest.update(group.attributes.to_be_bytes());
    }
    digest.finalize().into()
}

fn is_service_logon_sid(sid: &[u8]) -> bool {
    sid.len() == 20
        && sid[0] == 1
        && sid[1] == 3
        && sid[2..8] == [0, 0, 0, 0, 0, 5]
        && sid[8..12] == [5, 0, 0, 0]
}

fn service_logon_sid_digest(sid: &[u8]) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-candidate-service-logon-sid-v1\0");
    digest.update((sid.len() as u32).to_be_bytes());
    digest.update(sid);
    digest.finalize().into()
}

fn token_privilege_attributes_are_exact(privilege: &ObservedTokenPrivilege) -> bool {
    let attributes = privilege.attributes;
    let enabled = attributes & SE_PRIVILEGE_ENABLED != 0;
    let enabled_by_default = attributes & SE_PRIVILEGE_ENABLED_BY_DEFAULT != 0;
    let used_for_access = attributes & SE_PRIVILEGE_USED_FOR_ACCESS != 0;
    attributes & !TOKEN_PRIVILEGE_ALLOWED_ATTRIBUTES == 0
        && (!enabled_by_default || enabled)
        && (!used_for_access || enabled)
        && (privilege.name != CHANGE_NOTIFY_PRIVILEGE || enabled)
}

/// Native-only observation of the token SCM actually attached to the held
/// candidate process. It has no constructor from plan/expected values and
/// deliberately implements neither `Debug` nor serialization.
pub(super) struct CandidateRuntimeTokenObservation {
    process_id: u32,
    process_creation_time: u64,
    service_sid_enabled_attributes: u32,
    service_sid_restricting_attributes: u32,
    enabled_group_set_sha256: [u8; 32],
    restricting_group_set_sha256: [u8; 32],
    service_logon_sid_sha256: [u8; 32],
    principal_sha256: [u8; 32],
    privilege_set_sha256: [u8; 32],
    receipt_sha256: [u8; 32],
}

impl CandidateRuntimeTokenObservation {
    fn from_native(
        process: HANDLE,
        candidate: CandidateProcessEvidence,
    ) -> Result<Self, AuthorityMaintenanceError> {
        candidate
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if unsafe { GetProcessId(process) } != candidate.process_id()
            || process_creation_time(process)? != candidate.process_creation_time()
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_runtime_token_process_mismatch",
            ));
        }
        let mut raw_token = ptr::null_mut();
        if unsafe { OpenProcessToken(process, TOKEN_QUERY, &mut raw_token) } == 0
            || raw_token.is_null()
            || raw_token == INVALID_HANDLE_VALUE
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_runtime_token_unavailable",
            ));
        }
        let token = unsafe { OwnedHandle::from_raw_handle(raw_token as RawHandle) };
        let token_handle = token.as_raw_handle().cast();
        let restricted = unsafe { IsTokenRestricted(token_handle) } != 0;
        let principal = query_token_principal(token_handle)?;
        let enabled_groups = query_token_group_set(token_handle, TokenGroups)?;
        let restricting_groups = query_token_group_set(token_handle, TokenRestrictedSids)?;
        let privileges = query_token_privileges(token_handle)?;
        Self::from_observed(
            candidate,
            restricted,
            principal,
            enabled_groups,
            restricting_groups,
            privileges,
        )
    }

    fn from_observed(
        candidate: CandidateProcessEvidence,
        token_is_restricted: bool,
        principal: ObservedTokenPrincipal,
        enabled_groups: Vec<ObservedTokenGroup>,
        restricting_groups: Vec<ObservedTokenGroup>,
        mut privileges: Vec<ObservedTokenPrivilege>,
    ) -> Result<Self, AuthorityMaintenanceError> {
        candidate
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let expected_service_sid = sid_bytes(OwnedSid::from_text(SERVICE_SID)?.0)?;
        let expected_world_sid = sid_bytes(OwnedSid::from_text(WORLD_SID)?.0)?;
        let expected_write_restricted_sid =
            sid_bytes(OwnedSid::from_text(WRITE_RESTRICTED_SID)?.0)?;
        let principal_sha256 = validate_token_principal(&principal)?;
        let enabled_groups = normalized_token_group_set(enabled_groups)?;
        let restricting_groups = normalized_token_group_set(restricting_groups)?;
        let service_enabled = enabled_groups
            .iter()
            .filter(|group| group.sid == expected_service_sid)
            .collect::<Vec<_>>();
        if !token_is_restricted
            || service_enabled.len() != 1
            || service_enabled[0].attributes != SERVICE_SID_ENABLED_ATTRIBUTES
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_runtime_token_service_sid_mismatch",
            ));
        }
        let service_logon = enabled_groups
            .iter()
            .filter(|group| is_service_logon_sid(&group.sid))
            .collect::<Vec<_>>();
        if service_logon.len() != 1
            || service_logon[0].attributes != SERVICE_LOGON_SID_ENABLED_ATTRIBUTES
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_runtime_token_service_logon_sid_mismatch",
            ));
        }
        let service_logon_sid = service_logon[0].sid.clone();
        let mut expected_restricting_groups = vec![
            ObservedTokenGroup {
                sid: expected_service_sid,
                attributes: SERVICE_SID_RESTRICTING_ATTRIBUTES,
            },
            ObservedTokenGroup {
                sid: expected_world_sid,
                attributes: 0,
            },
            ObservedTokenGroup {
                sid: service_logon_sid.clone(),
                attributes: 0,
            },
            ObservedTokenGroup {
                sid: expected_write_restricted_sid,
                attributes: 0,
            },
        ];
        expected_restricting_groups.sort_by(|left, right| left.sid.cmp(&right.sid));
        if restricting_groups != expected_restricting_groups {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_runtime_token_restricting_sid_set_mismatch",
            ));
        }
        let enabled_group_set_sha256 = token_group_set_digest(b'E', &enabled_groups);
        let restricting_group_set_sha256 = token_group_set_digest(b'R', &restricting_groups);
        let service_logon_sid_sha256 = service_logon_sid_digest(&service_logon_sid);
        privileges.sort_by(|left, right| left.name.cmp(&right.name));
        if privileges.is_empty()
            || privileges.len() > 256
            || privileges
                .windows(2)
                .any(|pair| pair[0].name == pair[1].name)
            || privileges
                .iter()
                .any(|privilege| !token_privilege_attributes_are_exact(privilege))
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_runtime_token_privileges_invalid",
            ));
        }
        let mut expected = AUTHORITY_REQUIRED_PRIVILEGES
            .iter()
            .map(|value| (*value).to_string())
            .chain(std::iter::once(CHANGE_NOTIFY_PRIVILEGE.to_string()))
            .collect::<Vec<_>>();
        expected.sort();
        if privileges
            .iter()
            .map(|value| &value.name)
            .ne(expected.iter())
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_runtime_token_privileges_mismatch",
            ));
        }
        let mut privilege_digest = Sha256::new();
        privilege_digest.update(b"vrcforge-authority-candidate-runtime-privileges-v1\0");
        privilege_digest.update((privileges.len() as u32).to_be_bytes());
        for privilege in &privileges {
            privilege_digest.update((privilege.name.len() as u32).to_be_bytes());
            privilege_digest.update(privilege.name.as_bytes());
            privilege_digest.update(privilege.attributes.to_be_bytes());
        }
        let privilege_set_sha256: [u8; 32] = privilege_digest.finalize().into();
        let mut receipt = Sha256::new();
        receipt.update(CANDIDATE_RUNTIME_TOKEN_DOMAIN);
        update_candidate_process_digest(&mut receipt, candidate);
        receipt.update([u8::from(token_is_restricted)]);
        receipt.update(principal_sha256);
        receipt.update(SERVICE_SID_ENABLED_ATTRIBUTES.to_be_bytes());
        receipt.update(SERVICE_SID_RESTRICTING_ATTRIBUTES.to_be_bytes());
        receipt.update(enabled_group_set_sha256);
        receipt.update(restricting_group_set_sha256);
        receipt.update(service_logon_sid_sha256);
        receipt.update(privilege_set_sha256);
        let receipt_sha256 = receipt.finalize().into();
        Ok(Self {
            process_id: candidate.process_id(),
            process_creation_time: candidate.process_creation_time(),
            service_sid_enabled_attributes: SERVICE_SID_ENABLED_ATTRIBUTES,
            service_sid_restricting_attributes: SERVICE_SID_RESTRICTING_ATTRIBUTES,
            enabled_group_set_sha256,
            restricting_group_set_sha256,
            service_logon_sid_sha256,
            principal_sha256,
            privilege_set_sha256,
            receipt_sha256,
        })
    }

    pub(super) fn receipt_sha256(&self) -> [u8; 32] {
        self.receipt_sha256
    }

    pub(super) fn privilege_set_sha256(&self) -> [u8; 32] {
        self.privilege_set_sha256
    }

    pub(super) fn service_sid_attributes(&self) -> (u32, u32) {
        (
            self.service_sid_enabled_attributes,
            self.service_sid_restricting_attributes,
        )
    }

    pub(super) fn group_set_sha256(&self) -> ([u8; 32], [u8; 32]) {
        (
            self.enabled_group_set_sha256,
            self.restricting_group_set_sha256,
        )
    }

    pub(super) fn principal_sha256(&self) -> [u8; 32] {
        self.principal_sha256
    }

    #[cfg(test)]
    fn service_logon_sid_sha256(&self) -> [u8; 32] {
        self.service_logon_sid_sha256
    }

    fn require_process(
        &self,
        candidate: CandidateProcessEvidence,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.process_id != candidate.process_id()
            || self.process_creation_time != candidate.process_creation_time()
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_runtime_token_process_mismatch",
            ));
        }
        Ok(())
    }
}

/// Exact native identity exported beside the protocol-level Started record.
/// Its constructor is private so callers cannot manufacture it by echoing the
/// plan digest: it is created only after full SCM, held image/process, and
/// native runtime-token readback all agree.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) struct CandidateExactServiceIdentityObservation {
    exact_service_configuration_sha256: [u8; 32],
    candidate_service: CandidateProcessEvidence,
    runtime_token_receipt_sha256: [u8; 32],
    exact_service_identity_sha256: [u8; 32],
}

impl CandidateExactServiceIdentityObservation {
    fn from_native_readbacks(
        exact_service_configuration_sha256: [u8; 32],
        candidate_service: CandidateProcessEvidence,
        runtime_token: &CandidateRuntimeTokenObservation,
    ) -> Result<Self, AuthorityMaintenanceError> {
        candidate_service
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        runtime_token.require_process(candidate_service)?;
        Self::from_receipts(
            exact_service_configuration_sha256,
            candidate_service,
            runtime_token.receipt_sha256(),
        )
    }

    fn from_receipts(
        exact_service_configuration_sha256: [u8; 32],
        candidate_service: CandidateProcessEvidence,
        runtime_token_receipt_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        if exact_service_configuration_sha256
            .iter()
            .all(|byte| *byte == 0)
            || runtime_token_receipt_sha256.iter().all(|byte| *byte == 0)
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_exact_service_identity_invalid",
            ));
        }
        let value = Self {
            exact_service_configuration_sha256,
            candidate_service,
            runtime_token_receipt_sha256,
            exact_service_identity_sha256: candidate_exact_service_identity_digest(
                exact_service_configuration_sha256,
                candidate_service,
                runtime_token_receipt_sha256,
            )?,
        };
        value.validate()?;
        Ok(value)
    }

    #[cfg(test)]
    pub(super) fn for_test(
        exact_service_configuration_sha256: [u8; 32],
        candidate_service: CandidateProcessEvidence,
        runtime_token_receipt_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        Self::from_receipts(
            exact_service_configuration_sha256,
            candidate_service,
            runtime_token_receipt_sha256,
        )
    }

    pub(super) fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.candidate_service
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if self
            .exact_service_configuration_sha256
            .iter()
            .all(|byte| *byte == 0)
            || self
                .runtime_token_receipt_sha256
                .iter()
                .all(|byte| *byte == 0)
            || self
                .exact_service_identity_sha256
                .iter()
                .all(|byte| *byte == 0)
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_exact_service_identity_invalid",
            ));
        }
        let expected = candidate_exact_service_identity_digest(
            self.exact_service_configuration_sha256,
            self.candidate_service,
            self.runtime_token_receipt_sha256,
        )?;
        if expected != self.exact_service_identity_sha256 {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_exact_service_identity_mismatch",
            ));
        }
        Ok(())
    }

    pub(super) fn exact_service_configuration_sha256(&self) -> [u8; 32] {
        self.exact_service_configuration_sha256
    }

    pub(super) fn candidate_service(&self) -> CandidateProcessEvidence {
        self.candidate_service
    }

    pub(super) fn runtime_token_receipt_sha256(&self) -> [u8; 32] {
        self.runtime_token_receipt_sha256
    }

    pub(super) fn exact_service_identity_sha256(&self) -> [u8; 32] {
        self.exact_service_identity_sha256
    }
}

/// Linear terminal receipt for the native start lease. Production callers can
/// construct it only by consuming a validated SealReady projection, which in
/// turn proves candidate exit, credential consumption, and writer/create
/// handle closure.
#[must_use = "the terminal readback must remain bound to finalizer authorization"]
pub(super) struct CandidateServiceStartTerminalReadback {
    exact_service_identity_sha256: [u8; 32],
    activation_readback_sha256: [u8; 32],
    prepared_record_sha256: [u8; 32],
    credential_file_volume_serial: u64,
    credential_file_id: [u8; 16],
    credential_file_sha256: [u8; 32],
    process_exit_code: u32,
    service_win32_exit_code: u32,
    service_specific_exit_code: u32,
    receipt_sha256: [u8; 32],
}

impl CandidateServiceStartTerminalReadback {
    fn from_verified_terminal(
        exact_service_identity: CandidateExactServiceIdentityObservation,
        start_binding: CandidateActivationBinding,
        prepared_record_sha256: [u8; 32],
        credential_readback: CandidateCredentialReadback,
        projection: &CandidateActivationSealReadyProjection,
        process_exit_code: u32,
        stopped: &SERVICE_STATUS_PROCESS,
    ) -> Result<Self, AuthorityMaintenanceError> {
        exact_service_identity.validate()?;
        projection.validate()?;
        let credential_readback = CandidateCredentialReadback::from_verified_identity((
            credential_readback.volume_serial,
            credential_readback.file_id,
            credential_readback.bytes_sha256,
        ))?;
        if projection.binding() != start_binding
            || projection.credential_sha256() != start_binding.credential_sha256()
            || projection.prepared_record_sha256() != prepared_record_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_terminal_binding_mismatch",
            ));
        }
        if process_exit_code != 0
            || !service_status_is_exact_stopped(stopped)
            || stopped.dwWin32ExitCode != 0
            || stopped.dwServiceSpecificExitCode != 0
            || projection.exact_service_configuration_readback_sha256()
                != exact_service_identity.exact_service_configuration_sha256()
            || projection.exact_service_identity_sha256()
                != exact_service_identity.exact_service_identity_sha256()
            || projection.runtime_token_receipt_sha256()
                != exact_service_identity.runtime_token_receipt_sha256()
            || projection.candidate_service() != exact_service_identity.candidate_service()
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_terminal_mismatch",
            ));
        }
        let activation_readback_sha256 = projection.activation_readback_sha256();
        if activation_readback_sha256.iter().all(|byte| *byte == 0) {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_terminal_invalid",
            ));
        }
        let mut digest = Sha256::new();
        digest.update(CANDIDATE_START_TERMINAL_DOMAIN);
        digest.update(exact_service_identity.exact_service_identity_sha256());
        digest.update(activation_readback_sha256);
        digest.update(prepared_record_sha256);
        digest.update(credential_readback.volume_serial.to_be_bytes());
        digest.update(credential_readback.file_id);
        digest.update(credential_readback.bytes_sha256);
        digest.update(process_exit_code.to_be_bytes());
        digest.update(stopped.dwWin32ExitCode.to_be_bytes());
        digest.update(stopped.dwServiceSpecificExitCode.to_be_bytes());
        let value = Self {
            exact_service_identity_sha256: exact_service_identity.exact_service_identity_sha256(),
            activation_readback_sha256,
            prepared_record_sha256,
            credential_file_volume_serial: credential_readback.volume_serial,
            credential_file_id: credential_readback.file_id,
            credential_file_sha256: credential_readback.bytes_sha256,
            process_exit_code,
            service_win32_exit_code: stopped.dwWin32ExitCode,
            service_specific_exit_code: stopped.dwServiceSpecificExitCode,
            receipt_sha256: digest.finalize().into(),
        };
        value.validate()?;
        Ok(value)
    }

    pub(super) fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        if self.process_exit_code != 0
            || self.service_win32_exit_code != 0
            || self.service_specific_exit_code != 0
            || self
                .exact_service_identity_sha256
                .iter()
                .all(|byte| *byte == 0)
            || self
                .activation_readback_sha256
                .iter()
                .all(|byte| *byte == 0)
            || self.prepared_record_sha256.iter().all(|byte| *byte == 0)
            || self.credential_file_volume_serial == 0
            || self.credential_file_id.iter().all(|byte| *byte == 0)
            || self.credential_file_sha256.iter().all(|byte| *byte == 0)
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_terminal_invalid",
            ));
        }
        let mut digest = Sha256::new();
        digest.update(CANDIDATE_START_TERMINAL_DOMAIN);
        digest.update(self.exact_service_identity_sha256);
        digest.update(self.activation_readback_sha256);
        digest.update(self.prepared_record_sha256);
        digest.update(self.credential_file_volume_serial.to_be_bytes());
        digest.update(self.credential_file_id);
        digest.update(self.credential_file_sha256);
        digest.update(self.process_exit_code.to_be_bytes());
        digest.update(self.service_win32_exit_code.to_be_bytes());
        digest.update(self.service_specific_exit_code.to_be_bytes());
        let expected: [u8; 32] = digest.finalize().into();
        if expected != self.receipt_sha256 {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_terminal_mismatch",
            ));
        }
        Ok(())
    }

    pub(super) fn receipt_sha256(&self) -> [u8; 32] {
        self.receipt_sha256
    }
}

/// Sealed one-use bridge between the native start owner and the commit
/// finalizer. Neither inner capability can be copied, cloned, or extracted.
/// A future bridge must consume this whole value and bind the terminal receipt
/// into its stopped/seal authorization before exposing any finalizer input.
#[must_use = "the completed candidate bundle must be transferred to the finalizer"]
pub(super) struct CandidateCompletedBundle {
    terminal: CandidateServiceStartTerminalReadback,
    finalizer_projection: CandidateActivationSealReadyProjection,
}

impl CandidateCompletedBundle {
    fn from_verified(
        terminal: CandidateServiceStartTerminalReadback,
        finalizer_projection: CandidateActivationSealReadyProjection,
    ) -> Result<Self, AuthorityMaintenanceError> {
        terminal.validate()?;
        finalizer_projection.validate()?;
        Ok(Self {
            terminal,
            finalizer_projection,
        })
    }

    pub(super) fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.terminal.validate()?;
        self.finalizer_projection.validate()
    }

    /// Consumes the complete native candidate terminal capability. Production
    /// code cannot mint the restricted precommit start authorization from a
    /// locator and a matching credential binding alone.
    pub(super) fn into_restricted_precommit_start_authorization(
        self,
    ) -> Result<RestrictedPrecommitStartAuthorization, AuthorityMaintenanceError> {
        self.validate()?;
        Ok(RestrictedPrecommitStartAuthorization {
            inner: RestrictedPrecommitStartAuthorizationInner::Completed(self),
        })
    }

    #[cfg(test)]
    fn terminal_receipt_sha256(&self) -> [u8; 32] {
        self.terminal.receipt_sha256()
    }
}

/// Linear handoff from the candidate start owner to the restricted service.
/// The production variant retains the whole terminal bundle until the store
/// consumes it, so phase-local locator values cannot be promoted into an
/// authorization after the fact.
#[must_use = "the precommit authorization must be consumed exactly once"]
pub(super) struct RestrictedPrecommitStartAuthorization {
    inner: RestrictedPrecommitStartAuthorizationInner,
}

enum RestrictedPrecommitStartAuthorizationInner {
    Completed(CandidateCompletedBundle),
    #[cfg(test)]
    Fixture {
        locator: CandidateServiceStartLocator,
        credential_binding: CandidateActivationBinding,
    },
}

impl RestrictedPrecommitStartAuthorization {
    #[cfg(test)]
    pub(super) fn for_test(
        locator: CandidateServiceStartLocator,
        credential_binding: CandidateActivationBinding,
    ) -> Result<Self, AuthorityMaintenanceError> {
        locator.validate_binding(credential_binding).map_err(|_| {
            AuthorityMaintenanceError("authority_finalizer_commit_precommit_locator_mismatch")
        })?;
        Ok(Self {
            inner: RestrictedPrecommitStartAuthorizationInner::Fixture {
                locator,
                credential_binding,
            },
        })
    }

    pub(super) fn into_parts(
        self,
    ) -> Result<(CandidateServiceStartLocator, CandidateActivationBinding), AuthorityMaintenanceError>
    {
        let (locator, credential_binding) = match self.inner {
            RestrictedPrecommitStartAuthorizationInner::Completed(bundle) => {
                bundle.validate()?;
                let credential_binding = bundle.finalizer_projection.binding();
                (
                    CandidateServiceStartLocator::from_binding(credential_binding),
                    credential_binding,
                )
            }
            #[cfg(test)]
            RestrictedPrecommitStartAuthorizationInner::Fixture {
                locator,
                credential_binding,
            } => (locator, credential_binding),
        };
        locator.validate_binding(credential_binding).map_err(|_| {
            AuthorityMaintenanceError("authority_finalizer_commit_precommit_locator_mismatch")
        })?;
        Ok((locator, credential_binding))
    }
}

/// Typed fail-closed receipt for a started candidate that exited without
/// reaching the successful activation handoff. It deliberately implements
/// neither `Clone`, `Debug`, nor serialization.
#[must_use = "the containment readback must be persisted by recovery"]
pub(super) struct CandidateStartContainmentReadback {
    candidate: CandidateProcessEvidence,
    process_exit_code: u32,
    service_win32_exit_code: u32,
    service_specific_exit_code: u32,
    exact_service_configuration_sha256: [u8; 32],
    credential_readback: CandidateCredentialReadback,
    receipt_sha256: [u8; 32],
}

impl CandidateStartContainmentReadback {
    fn from_observed(
        candidate: CandidateProcessEvidence,
        process_exit_code: u32,
        stopped: &SERVICE_STATUS_PROCESS,
        exact_service_configuration_sha256: [u8; 32],
        credential_readback: CandidateCredentialReadback,
    ) -> Result<Self, AuthorityMaintenanceError> {
        candidate
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if process_exit_code == STILL_ACTIVE as u32
            || stopped.dwServiceType != SERVICE_WIN32_OWN_PROCESS
            || stopped.dwCurrentState != SERVICE_STOPPED
            || stopped.dwProcessId != 0
            || exact_service_configuration_sha256
                .iter()
                .all(|byte| *byte == 0)
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_containment_invalid",
            ));
        }
        let mut digest = Sha256::new();
        digest.update(CANDIDATE_START_CONTAINMENT_DOMAIN);
        update_candidate_process_digest(&mut digest, candidate);
        digest.update(process_exit_code.to_be_bytes());
        digest.update(stopped.dwWin32ExitCode.to_be_bytes());
        digest.update(stopped.dwServiceSpecificExitCode.to_be_bytes());
        digest.update(exact_service_configuration_sha256);
        digest.update(credential_readback.volume_serial.to_be_bytes());
        digest.update(credential_readback.file_id);
        digest.update(credential_readback.bytes_sha256);
        let receipt_sha256 = digest.finalize().into();
        Ok(Self {
            candidate,
            process_exit_code,
            service_win32_exit_code: stopped.dwWin32ExitCode,
            service_specific_exit_code: stopped.dwServiceSpecificExitCode,
            exact_service_configuration_sha256,
            credential_readback,
            receipt_sha256,
        })
    }

    pub(super) fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        let status = SERVICE_STATUS_PROCESS {
            dwServiceType: SERVICE_WIN32_OWN_PROCESS,
            dwCurrentState: SERVICE_STOPPED,
            dwControlsAccepted: 0,
            dwWin32ExitCode: self.service_win32_exit_code,
            dwServiceSpecificExitCode: self.service_specific_exit_code,
            dwCheckPoint: 0,
            dwWaitHint: 0,
            dwProcessId: 0,
            dwServiceFlags: 0,
        };
        let recomputed = Self::from_observed(
            self.candidate,
            self.process_exit_code,
            &status,
            self.exact_service_configuration_sha256,
            self.credential_readback,
        )?;
        if recomputed.receipt_sha256 != self.receipt_sha256 {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_containment_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn receipt_sha256(&self) -> [u8; 32] {
        self.receipt_sha256
    }
}

/// Recovery-facing abort receipt. It binds the generic stopped/configuration/
/// credential containment proof to the exact per-process runtime identity,
/// including the held process token receipt.
#[must_use = "the abort containment readback must be persisted by recovery"]
pub(super) struct CandidateAbortContainmentReadback {
    exact_service_identity_sha256: [u8; 32],
    containment: CandidateStartContainmentReadback,
    receipt_sha256: [u8; 32],
}

impl CandidateAbortContainmentReadback {
    fn from_verified(
        exact_service_identity: CandidateExactServiceIdentityObservation,
        containment: CandidateStartContainmentReadback,
    ) -> Result<Self, AuthorityMaintenanceError> {
        exact_service_identity.validate()?;
        containment.validate()?;
        if exact_service_identity.candidate_service() != containment.candidate {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_abort_identity_mismatch",
            ));
        }
        let exact_service_identity_sha256 = exact_service_identity.exact_service_identity_sha256();
        let mut digest = Sha256::new();
        digest.update(CANDIDATE_ABORT_CONTAINMENT_DOMAIN);
        digest.update(exact_service_identity_sha256);
        digest.update(containment.receipt_sha256());
        let value = Self {
            exact_service_identity_sha256,
            containment,
            receipt_sha256: digest.finalize().into(),
        };
        value.validate()?;
        Ok(value)
    }

    pub(super) fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.containment.validate()?;
        if self
            .exact_service_identity_sha256
            .iter()
            .all(|byte| *byte == 0)
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_abort_identity_invalid",
            ));
        }
        let mut digest = Sha256::new();
        digest.update(CANDIDATE_ABORT_CONTAINMENT_DOMAIN);
        digest.update(self.exact_service_identity_sha256);
        digest.update(self.containment.receipt_sha256());
        let expected: [u8; 32] = digest.finalize().into();
        if expected != self.receipt_sha256 {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_abort_identity_mismatch",
            ));
        }
        Ok(())
    }

    pub(super) fn receipt_sha256(&self) -> [u8; 32] {
        self.receipt_sha256
    }
}

fn verified_candidate_abort_readback(
    exact_service_identity: CandidateExactServiceIdentityObservation,
    candidate: CandidateProcessEvidence,
    process_exit_code: u32,
    stopped: &SERVICE_STATUS_PROCESS,
    expected_configuration_sha256: [u8; 32],
    observed_configuration_sha256: [u8; 32],
    expected_credential: CandidateCredentialReadback,
    observed_credential: CandidateCredentialReadback,
) -> Result<CandidateAbortContainmentReadback, AuthorityMaintenanceError> {
    if observed_configuration_sha256 != expected_configuration_sha256
        || observed_credential != expected_credential
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_abort_readback_changed",
        ));
    }
    let readback = CandidateStartContainmentReadback::from_observed(
        candidate,
        process_exit_code,
        stopped,
        observed_configuration_sha256,
        observed_credential,
    )?;
    readback.validate()?;
    CandidateAbortContainmentReadback::from_verified(exact_service_identity, readback)
}

struct SensitiveOrderedArguments([String; CANDIDATE_SERVICE_START_ARGUMENT_COUNT as usize]);

impl Drop for SensitiveOrderedArguments {
    fn drop(&mut self) {
        for argument in &mut self.0 {
            // Zero is valid UTF-8, so the String remains valid until it drops.
            unsafe {
                argument.as_mut_vec().fill(0);
            }
        }
    }
}

/// Opaque transient argument storage. It deliberately implements neither
/// `Debug` nor serialization and clears both UTF-8 and UTF-16 buffers on drop.
struct CandidateServiceStartArguments {
    locator: CandidateServiceStartLocator,
    wide: [Vec<u16>; CANDIDATE_SERVICE_START_ARGUMENT_COUNT as usize],
}

impl CandidateServiceStartArguments {
    fn from_binding(
        binding: CandidateActivationBinding,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let locator = CandidateServiceStartLocator::from_binding(binding);
        locator
            .validate_binding(binding)
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let ordered = SensitiveOrderedArguments(locator.ordered_service_arguments());
        let references = ordered.0.each_ref().map(String::as_str);
        validate_ordered_candidate_service_arguments(binding, &references)?;
        let wide = ordered.0.each_ref().map(|argument| wide_null(argument));
        if wide
            .iter()
            .any(|argument| argument.len() < 2 || argument.last() != Some(&0))
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_arguments_invalid",
            ));
        }
        Ok(Self { locator, wide })
    }

    fn validate_against(
        &self,
        binding: CandidateActivationBinding,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.locator
            .validate_binding(binding)
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if self
            .wide
            .iter()
            .any(|argument| argument.len() < 2 || argument.last() != Some(&0))
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_arguments_invalid",
            ));
        }
        Ok(())
    }

    fn pointers(&self) -> [PCWSTR; CANDIDATE_SERVICE_START_ARGUMENT_COUNT as usize] {
        self.wide.each_ref().map(|argument| argument.as_ptr())
    }
}

impl Drop for CandidateServiceStartArguments {
    fn drop(&mut self) {
        for argument in &mut self.wide {
            argument.fill(0);
        }
    }
}

fn validate_ordered_candidate_service_arguments(
    binding: CandidateActivationBinding,
    arguments: &[&str],
) -> Result<(), AuthorityMaintenanceError> {
    let parsed = CandidateServiceStartLocator::parse_ordered(arguments)
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    parsed
        .validate_binding(binding)
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    let expected = SensitiveOrderedArguments(
        CandidateServiceStartLocator::from_binding(binding).ordered_service_arguments(),
    );
    if arguments.len() != expected.0.len()
        || arguments
            .iter()
            .zip(expected.0.iter())
            .any(|(observed, expected)| *observed != expected)
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_arguments_invalid",
        ));
    }
    Ok(())
}

struct CandidateStartSequenceReadback {
    exact_service_configuration_sha256: [u8; 32],
    candidate_service: CandidateProcessEvidence,
    credential_readback: CandidateCredentialReadback,
}

trait CandidateServiceStartOperations {
    fn credential_readback(
        &mut self,
    ) -> Result<CandidateCredentialReadback, AuthorityMaintenanceError>;

    fn service_configuration_readback(&mut self) -> Result<[u8; 32], AuthorityMaintenanceError>;

    fn start_exact(
        &mut self,
        arguments: &CandidateServiceStartArguments,
    ) -> Result<(), AuthorityMaintenanceError>;

    fn bind_start_pending_process(
        &mut self,
    ) -> Result<CandidateProcessEvidence, AuthorityMaintenanceError>;

    fn revalidate_start_pending_process(
        &mut self,
        candidate_service: CandidateProcessEvidence,
    ) -> Result<(), AuthorityMaintenanceError>;

    fn start_was_issued(&self) -> bool;

    fn contain_started_failure(&mut self) -> Result<(), AuthorityMaintenanceError>;
}

fn resolve_started_result<O, T>(
    operations: &mut O,
    result: Result<T, AuthorityMaintenanceError>,
) -> Result<T, AuthorityMaintenanceError>
where
    O: CandidateServiceStartOperations,
{
    match result {
        Ok(value) => Ok(value),
        Err(error) if operations.start_was_issued() => {
            operations.contain_started_failure().map_err(|_| {
                AuthorityMaintenanceError("authority_candidate_start_containment_failed")
            })?;
            Err(error)
        }
        Err(error) => Err(error),
    }
}

fn execute_candidate_service_start<O: CandidateServiceStartOperations>(
    binding: CandidateActivationBinding,
    exact_service_configuration_sha256: [u8; 32],
    operations: &mut O,
) -> Result<CandidateStartSequenceReadback, AuthorityMaintenanceError> {
    if exact_service_configuration_sha256
        .iter()
        .all(|byte| *byte == 0)
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_configuration_invalid",
        ));
    }
    let result = (|| {
        let arguments = CandidateServiceStartArguments::from_binding(binding)?;
        let credential_before = operations.credential_readback()?;
        let configuration_before = operations.service_configuration_readback()?;
        if configuration_before != exact_service_configuration_sha256 {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_configuration_mismatch",
            ));
        }
        operations.start_exact(&arguments)?;
        let candidate_service = operations.bind_start_pending_process()?;
        candidate_service
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if candidate_service.image() != binding.target_service_image() {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_image_mismatch",
            ));
        }
        operations.revalidate_start_pending_process(candidate_service)?;
        let configuration_after = operations.service_configuration_readback()?;
        let credential_after = operations.credential_readback()?;
        if configuration_after != configuration_before
            || configuration_after != exact_service_configuration_sha256
            || credential_after != credential_before
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_readback_changed",
            ));
        }
        Ok(CandidateStartSequenceReadback {
            exact_service_configuration_sha256,
            candidate_service,
            credential_readback: credential_after,
        })
    })();
    resolve_started_result(operations, result)
}

/// Held native result for the candidate process. The service, process, and
/// pre-start image handles remain alive until the activation owner drops it.
#[must_use = "the live candidate lease must be completed with SealReady evidence or contained"]
pub(super) struct NativeCandidateServiceStartLease {
    _manager: ServiceHandle,
    _service: ServiceHandle,
    server: HeldCandidateServer,
    layout: AuthorityLayout,
    plan: ExactTargetServicePlan,
    prepared: NativeCandidateCredentialLease,
    binding: CandidateActivationBinding,
    prepared_record_sha256: [u8; 32],
    exact_service_identity: CandidateExactServiceIdentityObservation,
    credential_readback: CandidateCredentialReadback,
    runtime_token: CandidateRuntimeTokenObservation,
    terminal_resolved: bool,
}

impl NativeCandidateServiceStartLease {
    pub(super) fn candidate_service(
        &self,
    ) -> Result<CandidateProcessEvidence, AuthorityMaintenanceError> {
        self.server.evidence()
    }

    pub(super) fn revalidate_start_pending(&mut self) -> Result<(), AuthorityMaintenanceError> {
        self.server.revalidate(true)?;
        let candidate = self.server.evidence()?;
        let status = query_service_status(self._service.0)?;
        require_exact_start_pending_process(&status, candidate.process_id())?;
        let configuration = verify_exact_service_configuration(self._service.0, &self.plan)?;
        let runtime_token =
            CandidateRuntimeTokenObservation::from_native(self.server.raw_process(), candidate)?;
        let exact_service_identity =
            CandidateExactServiceIdentityObservation::from_native_readbacks(
                configuration,
                candidate,
                &runtime_token,
            )?;
        if exact_service_identity != self.exact_service_identity {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_exact_service_identity_changed",
            ));
        }
        self.runtime_token = runtime_token;
        Ok(())
    }

    pub(super) fn runtime_token_observation(&self) -> &CandidateRuntimeTokenObservation {
        &self.runtime_token
    }

    pub(super) fn exact_service_identity_observation(
        &self,
    ) -> CandidateExactServiceIdentityObservation {
        self.exact_service_identity
    }

    pub(super) fn complete(
        mut self,
        projection: CandidateActivationSealReadyProjection,
    ) -> Result<CandidateCompletedBundle, AuthorityMaintenanceError> {
        self.server.revalidate(false)?;
        let candidate = self.server.evidence()?;
        if candidate != self.exact_service_identity.candidate_service()
            || !candidate_process_exit_is_proven(unsafe {
                WaitForSingleObject(self.server.raw_process(), 0)
            })
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_terminal_process_active",
            ));
        }
        let mut process_exit_code = STILL_ACTIVE as u32;
        if unsafe { GetExitCodeProcess(self.server.raw_process(), &mut process_exit_code) } == 0 {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_terminal_process_unavailable",
            ));
        }
        let stopped = query_service_status(self._service.0)?;
        let configuration = verify_exact_service_configuration(self._service.0, &self.plan)?;
        if configuration
            != self
                .exact_service_identity
                .exact_service_configuration_sha256()
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_terminal_configuration_changed",
            ));
        }
        let readback = CandidateServiceStartTerminalReadback::from_verified_terminal(
            self.exact_service_identity,
            self.binding,
            self.prepared_record_sha256,
            self.credential_readback,
            &projection,
            process_exit_code,
            &stopped,
        )?;
        let bundle = CandidateCompletedBundle::from_verified(readback, projection)?;
        self.terminal_resolved = true;
        Ok(bundle)
    }

    /// Explicit fail-closed recovery path. This consumes the live lease, holds
    /// the exact process until exit, requires SCM STOPPED, and revalidates both
    /// the service configuration and the original Prepared credential before
    /// releasing the native handles with a typed receipt.
    pub(super) fn abort(
        mut self,
    ) -> Result<CandidateAbortContainmentReadback, AuthorityMaintenanceError> {
        self.server.revalidate(false)?;
        let candidate = self.server.evidence()?;
        if candidate != self.exact_service_identity.candidate_service() {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_abort_process_changed",
            ));
        }
        while !candidate_process_exit_is_proven(unsafe {
            WaitForSingleObject(self.server.raw_process(), INFINITE)
        }) {
            std::thread::sleep(CANDIDATE_SERVICE_START_POLL_INTERVAL);
        }
        self.server.revalidate(false)?;
        if self.server.evidence()? != candidate {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_abort_process_changed",
            ));
        }
        let mut process_exit_code = STILL_ACTIVE as u32;
        if unsafe { GetExitCodeProcess(self.server.raw_process(), &mut process_exit_code) } == 0
            || process_exit_code == STILL_ACTIVE as u32
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_abort_process_exit_invalid",
            ));
        }
        let stopped = wait_for_exact_stopped_service(self._service.0)?;
        let configuration = verify_exact_service_configuration(self._service.0, &self.plan)?;
        let credential = CandidateCredentialReadback::from_verified_identity(
            self.prepared.verify_prepared_readback(&self.layout)?,
        )?;
        let final_status = query_service_status(self._service.0)?;
        if final_status.dwServiceType != stopped.dwServiceType
            || final_status.dwCurrentState != stopped.dwCurrentState
            || final_status.dwProcessId != stopped.dwProcessId
            || final_status.dwWin32ExitCode != stopped.dwWin32ExitCode
            || final_status.dwServiceSpecificExitCode != stopped.dwServiceSpecificExitCode
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_abort_status_changed",
            ));
        }
        let readback = verified_candidate_abort_readback(
            self.exact_service_identity,
            candidate,
            process_exit_code,
            &final_status,
            self.exact_service_identity
                .exact_service_configuration_sha256(),
            configuration,
            self.credential_readback,
            credential,
        )?;
        self.terminal_resolved = true;
        Ok(readback)
    }
}

impl Drop for NativeCandidateServiceStartLease {
    fn drop(&mut self) {
        if self.terminal_resolved {
            return;
        }
        while !candidate_process_exit_is_proven(unsafe {
            WaitForSingleObject(self.server.raw_process(), INFINITE)
        }) {
            std::thread::sleep(CANDIDATE_SERVICE_START_POLL_INTERVAL);
        }
        loop {
            if query_service_status(self._service.0)
                .map(|status| service_status_is_exact_stopped(&status))
                .unwrap_or(false)
            {
                break;
            }
            std::thread::sleep(CANDIDATE_SERVICE_START_POLL_INTERVAL);
        }
    }
}

/// Starts the already-configured authority service in its one-use candidate
/// mode. This adapter is intentionally not connected to a production mutation
/// gate yet; callers must explicitly own that later integration decision.
pub(super) fn start_exact_candidate_service(
    layout: &AuthorityLayout,
    plan: &ExactTargetServicePlan,
    prepared: NativeCandidateCredentialLease,
) -> Result<
    (
        CandidateStartedReadback,
        CandidateExactServiceIdentityObservation,
        NativeCandidateServiceStartLease,
    ),
    AuthorityMaintenanceError,
> {
    if prepared.record().phase() != CandidateCredentialPhase::Prepared {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_credential_not_prepared",
        ));
    }
    let binding = prepared
        .record()
        .binding()
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    validate_plan_binding(layout, plan, binding)?;
    let exact_service_configuration_sha256 = plan.exact_service_configuration_sha256();
    let mut operations =
        WindowsCandidateServiceStartOperations::open(layout, plan, prepared, binding)?;
    let sequence = execute_candidate_service_start(
        binding,
        exact_service_configuration_sha256,
        &mut operations,
    )?;
    let exact_service_identity = match operations.exact_service_identity(
        sequence.exact_service_configuration_sha256,
        sequence.candidate_service,
    ) {
        Ok(value) => value,
        Err(error) => {
            operations.contain_started_failure().map_err(|_| {
                AuthorityMaintenanceError("authority_candidate_start_containment_failed")
            })?;
            return Err(error);
        }
    };
    let started = match CandidateStartedReadback::from_exact_observation(
        operations.prepared.record(),
        exact_service_identity,
    ) {
        Ok(value) => value,
        Err(error) => {
            operations.contain_started_failure().map_err(|_| {
                AuthorityMaintenanceError("authority_candidate_start_containment_failed")
            })?;
            return Err(error);
        }
    };
    let lease = operations.into_lease(exact_service_identity, sequence.credential_readback)?;
    Ok((started, exact_service_identity, lease))
}

struct HeldStartedProcess {
    process: OwnedHandle,
    process_id: u32,
    process_creation_time: u64,
}

impl HeldStartedProcess {
    fn open(process_id: u32) -> Result<Self, AuthorityMaintenanceError> {
        if process_id == 0 {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_process_invalid",
            ));
        }
        let raw = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
                0,
                process_id,
            )
        };
        if raw.is_null() || raw == INVALID_HANDLE_VALUE {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_process_unavailable",
            ));
        }
        let process = unsafe { OwnedHandle::from_raw_handle(raw as RawHandle) };
        let value = Self {
            process_creation_time: process_creation_time(raw)?,
            process,
            process_id,
        };
        value.revalidate_identity()?;
        Ok(value)
    }

    fn raw(&self) -> HANDLE {
        self.process.as_raw_handle().cast()
    }

    fn revalidate_identity(&self) -> Result<(), AuthorityMaintenanceError> {
        if unsafe { GetProcessId(self.raw()) } != self.process_id
            || process_creation_time(self.raw())? != self.process_creation_time
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_process_changed",
            ));
        }
        Ok(())
    }
}

struct WindowsCandidateServiceStartOperations<'a> {
    layout: &'a AuthorityLayout,
    plan: &'a ExactTargetServicePlan,
    prepared: NativeCandidateCredentialLease,
    binding: CandidateActivationBinding,
    manager: ServiceHandle,
    service: ServiceHandle,
    pre_start_image: Option<HeldServerImage>,
    containment_image: Option<HeldServerImage>,
    validation_process: Option<OwnedHandle>,
    started_process: Option<HeldStartedProcess>,
    server: Option<HeldCandidateServer>,
    runtime_token: Option<CandidateRuntimeTokenObservation>,
    configuration_before: Option<[u8; 32]>,
    credential_before: Option<CandidateCredentialReadback>,
    containment_readback: Option<CandidateStartContainmentReadback>,
    start_issued: bool,
    lifecycle_resolved: bool,
}

impl<'a> WindowsCandidateServiceStartOperations<'a> {
    fn open(
        layout: &'a AuthorityLayout,
        plan: &'a ExactTargetServicePlan,
        prepared: NativeCandidateCredentialLease,
        binding: CandidateActivationBinding,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let expected_path = expected_service_path(layout, binding)?;
        let pre_start_image = HeldServerImage::open_path(&expected_path)?;
        pre_start_image.require_static_expectation(binding.target_service_image())?;
        let containment_image = HeldServerImage::open_path(&expected_path)?;
        containment_image.require_static_expectation(binding.target_service_image())?;
        let manager =
            ServiceHandle(unsafe { OpenSCManagerW(ptr::null(), ptr::null(), SC_MANAGER_CONNECT) });
        if manager.0.is_null() {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_scm_unavailable",
            ));
        }
        let service_name = wide_null(AUTHORITY_SERVICE_NAME);
        let service = ServiceHandle(unsafe {
            OpenServiceW(manager.0, service_name.as_ptr(), CANDIDATE_SERVICE_ACCESS)
        });
        if service.0.is_null() {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_service_unavailable",
            ));
        }
        Ok(Self {
            layout,
            plan,
            prepared,
            binding,
            manager,
            service,
            pre_start_image: Some(pre_start_image),
            containment_image: Some(containment_image),
            validation_process: None,
            started_process: None,
            server: None,
            runtime_token: None,
            configuration_before: None,
            credential_before: None,
            containment_readback: None,
            start_issued: false,
            lifecycle_resolved: false,
        })
    }

    fn into_lease(
        mut self,
        exact_service_identity: CandidateExactServiceIdentityObservation,
        credential_readback: CandidateCredentialReadback,
    ) -> Result<NativeCandidateServiceStartLease, AuthorityMaintenanceError> {
        let readiness = (|| {
            let server = self.server.as_ref().ok_or(AuthorityMaintenanceError(
                "authority_candidate_start_process_not_held",
            ))?;
            let runtime_token = self
                .runtime_token
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_candidate_runtime_token_unavailable",
                ))?;
            let candidate = server.evidence()?;
            runtime_token.require_process(candidate)?;
            exact_service_identity.validate()?;
            if exact_service_identity.candidate_service() != candidate
                || exact_service_identity.exact_service_configuration_sha256()
                    != self.plan.exact_service_configuration_sha256()
                || exact_service_identity.runtime_token_receipt_sha256()
                    != runtime_token.receipt_sha256()
            {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_exact_service_identity_mismatch",
                ));
            }
            let prepared_record_sha256 = self
                .prepared
                .record()
                .record_sha256()
                .map_err(|error| AuthorityMaintenanceError(error.code()))?;
            if prepared_record_sha256.iter().all(|byte| *byte == 0) {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_start_credential_readback_invalid",
                ));
            }
            Ok(prepared_record_sha256)
        })();
        let prepared_record_sha256 = resolve_started_result(&mut self, readiness)?;
        let server = self.server.take().ok_or(AuthorityMaintenanceError(
            "authority_candidate_start_process_not_held",
        ))?;
        let runtime_token = self.runtime_token.take().ok_or(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_unavailable",
        ))?;
        self.lifecycle_resolved = true;
        Ok(NativeCandidateServiceStartLease {
            _manager: self.manager,
            _service: self.service,
            server,
            layout: self.layout.clone(),
            plan: self.plan.clone(),
            prepared: self.prepared,
            binding: self.binding,
            prepared_record_sha256,
            exact_service_identity,
            credential_readback,
            runtime_token,
            terminal_resolved: false,
        })
    }

    fn exact_service_identity(
        &self,
        exact_service_configuration_sha256: [u8; 32],
        candidate_service: CandidateProcessEvidence,
    ) -> Result<CandidateExactServiceIdentityObservation, AuthorityMaintenanceError> {
        let runtime_token = self
            .runtime_token
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_runtime_token_unavailable",
            ))?;
        CandidateExactServiceIdentityObservation::from_native_readbacks(
            exact_service_configuration_sha256,
            candidate_service,
            runtime_token,
        )
    }
}

impl CandidateServiceStartOperations for WindowsCandidateServiceStartOperations<'_> {
    fn credential_readback(
        &mut self,
    ) -> Result<CandidateCredentialReadback, AuthorityMaintenanceError> {
        let value = CandidateCredentialReadback::from_verified_identity(
            self.prepared.verify_prepared_readback(self.layout)?,
        )?;
        if !self.start_issued && self.credential_before.is_none() {
            self.credential_before = Some(value);
        }
        Ok(value)
    }

    fn service_configuration_readback(&mut self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        let value = verify_exact_service_configuration(self.service.0, self.plan)?;
        if !self.start_issued && self.configuration_before.is_none() {
            self.configuration_before = Some(value);
        }
        Ok(value)
    }

    fn start_exact(
        &mut self,
        arguments: &CandidateServiceStartArguments,
    ) -> Result<(), AuthorityMaintenanceError> {
        arguments.validate_against(self.binding)?;
        require_exact_stopped_service(&query_service_status(self.service.0)?)?;
        let pointers = arguments.pointers();
        if unsafe {
            StartServiceW(
                self.service.0,
                CANDIDATE_SERVICE_START_ARGUMENT_COUNT,
                pointers.as_ptr(),
            )
        } == 0
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_service_failed",
            ));
        }
        self.start_issued = true;
        let process_id = wait_for_exact_start_pending_process(self.service.0)?;
        self.started_process = Some(HeldStartedProcess::open(process_id)?);
        let started_process = self
            .started_process
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_start_process_not_held",
            ))?;
        self.containment_image
            .as_mut()
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_start_image_not_held",
            ))?
            .revalidate_process(started_process.raw())?;
        let validation_process = HeldStartedProcess::open(process_id)?;
        if validation_process.process_creation_time != started_process.process_creation_time {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_process_changed",
            ));
        }
        self.validation_process = Some(validation_process.process);
        let candidate = CandidateProcessEvidence::from_static_image(
            process_id,
            started_process.process_creation_time,
            *self.binding.target_service_image(),
        )
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_start_process_invalid"))?;
        let runtime_token =
            CandidateRuntimeTokenObservation::from_native(started_process.raw(), candidate)?;
        self.runtime_token = Some(runtime_token);
        Ok(())
    }

    fn bind_start_pending_process(
        &mut self,
    ) -> Result<CandidateProcessEvidence, AuthorityMaintenanceError> {
        if self.server.is_some() {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_process_already_bound",
            ));
        }
        let started = self
            .started_process
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_start_process_not_held",
            ))?;
        started.revalidate_identity()?;
        let process_id = started.process_id;
        let process = self
            .validation_process
            .take()
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_start_process_not_held",
            ))?;
        let image = self
            .pre_start_image
            .take()
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_start_image_not_held",
            ))?;
        let server = HeldCandidateServer::open_started(
            process,
            process_id,
            *self.binding.target_service_image(),
            image,
        )?;
        let evidence = server.evidence()?;
        if evidence.process_creation_time() != started.process_creation_time {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_process_changed",
            ));
        }
        self.runtime_token
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_runtime_token_unavailable",
            ))?
            .require_process(evidence)?;
        self.server = Some(server);
        Ok(evidence)
    }

    fn revalidate_start_pending_process(
        &mut self,
        candidate_service: CandidateProcessEvidence,
    ) -> Result<(), AuthorityMaintenanceError> {
        let server = self.server.as_mut().ok_or(AuthorityMaintenanceError(
            "authority_candidate_start_process_not_held",
        ))?;
        server.revalidate(true)?;
        if server.evidence()? != candidate_service {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_process_changed",
            ));
        }
        let status = query_service_status(self.service.0)?;
        require_exact_start_pending_process(&status, candidate_service.process_id())
    }

    fn start_was_issued(&self) -> bool {
        self.start_issued
    }

    fn contain_started_failure(&mut self) -> Result<(), AuthorityMaintenanceError> {
        if self.lifecycle_resolved {
            return self
                .containment_readback
                .as_ref()
                .map(CandidateStartContainmentReadback::validate)
                .unwrap_or(Ok(()));
        }
        if !self.start_issued {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_containment_not_armed",
            ));
        }
        if self.started_process.is_none() {
            let process_id = wait_for_exact_start_pending_process(self.service.0)?;
            self.started_process = Some(HeldStartedProcess::open(process_id)?);
        }
        let process = self
            .started_process
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_start_process_not_held",
            ))?;
        process.revalidate_identity()?;
        let candidate = CandidateProcessEvidence::from_static_image(
            process.process_id,
            process.process_creation_time,
            *self.binding.target_service_image(),
        )
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_start_process_invalid"))?;
        if let Some(server) = self.server.as_mut() {
            server.revalidate(false)?;
            if server.evidence()? != candidate {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_start_process_changed",
                ));
            }
        }
        self.containment_image
            .as_mut()
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_start_image_not_held",
            ))?
            .revalidate_process(process.raw())?;
        // No timeout is allowed here. Until a supervisor owns a terminate/stop
        // capability, returning while this exact process is still live would
        // drop the only containment lease and make rollback unsafe. A broken
        // candidate may therefore deny maintenance progress, but it cannot
        // escape the held service/process/image constraints.
        while !candidate_process_exit_is_proven(unsafe {
            WaitForSingleObject(process.raw(), INFINITE)
        }) {
            std::thread::sleep(CANDIDATE_SERVICE_START_POLL_INTERVAL);
        }
        process.revalidate_identity()?;
        let mut process_exit_code = STILL_ACTIVE as u32;
        if unsafe { GetExitCodeProcess(process.raw(), &mut process_exit_code) } == 0
            || process_exit_code == STILL_ACTIVE as u32
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_containment_process_exit_invalid",
            ));
        }
        self.containment_image
            .as_mut()
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_start_image_not_held",
            ))?
            .revalidate_held()?;
        let stopped = wait_for_exact_stopped_service(self.service.0)?;
        let configuration = verify_exact_service_configuration(self.service.0, self.plan)?;
        let credential = CandidateCredentialReadback::from_verified_identity(
            self.prepared.verify_prepared_readback(self.layout)?,
        )?;
        let final_status = query_service_status(self.service.0)?;
        if Some(configuration) != self.configuration_before
            || Some(credential) != self.credential_before
            || final_status.dwServiceType != stopped.dwServiceType
            || final_status.dwCurrentState != stopped.dwCurrentState
            || final_status.dwProcessId != stopped.dwProcessId
            || final_status.dwWin32ExitCode != stopped.dwWin32ExitCode
            || final_status.dwServiceSpecificExitCode != stopped.dwServiceSpecificExitCode
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_containment_readback_changed",
            ));
        }
        require_exact_stopped_service(&final_status)?;
        let readback = CandidateStartContainmentReadback::from_observed(
            candidate,
            process_exit_code,
            &stopped,
            configuration,
            credential,
        )?;
        readback.validate()?;
        self.containment_readback = Some(readback);
        self.lifecycle_resolved = true;
        Ok(())
    }
}

fn candidate_process_exit_is_proven(wait_result: u32) -> bool {
    wait_result == WAIT_OBJECT_0
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

fn process_creation_time(process: HANDLE) -> Result<u64, AuthorityMaintenanceError> {
    let mut creation = unsafe { zeroed() };
    let mut exit = unsafe { zeroed() };
    let mut kernel = unsafe { zeroed() };
    let mut user = unsafe { zeroed() };
    if unsafe { GetProcessTimes(process, &mut creation, &mut exit, &mut kernel, &mut user) } == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_process_times_unavailable",
        ));
    }
    let value = (u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime);
    if value == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_process_times_invalid",
        ));
    }
    Ok(value)
}

fn update_candidate_process_digest(digest: &mut Sha256, candidate: CandidateProcessEvidence) {
    digest.update(candidate.process_id().to_be_bytes());
    digest.update(candidate.process_creation_time().to_be_bytes());
    digest.update(candidate.image_sha256());
    digest.update(candidate.image_byte_length().to_be_bytes());
    digest.update(candidate.image_volume_serial().to_be_bytes());
    digest.update(candidate.image_file_id());
    digest.update(candidate.image_link_count().to_be_bytes());
    digest.update(candidate.image_attributes().to_be_bytes());
    digest.update(candidate.full_readback_receipt_sha256());
}

fn wait_for_exact_stopped_service(
    service: SC_HANDLE,
) -> Result<SERVICE_STATUS_PROCESS, AuthorityMaintenanceError> {
    loop {
        let status = query_service_status(service)?;
        if status.dwServiceType == SERVICE_WIN32_OWN_PROCESS
            && status.dwCurrentState == SERVICE_STOPPED
            && status.dwProcessId == 0
        {
            return Ok(status);
        }
        if status.dwServiceType != SERVICE_WIN32_OWN_PROCESS
            || status.dwCurrentState != SERVICE_START_PENDING
            || status.dwProcessId == 0
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_containment_scm_mismatch",
            ));
        }
        std::thread::sleep(CANDIDATE_SERVICE_START_POLL_INTERVAL);
    }
}

fn query_token_buffer(
    token: HANDLE,
    class: i32,
) -> Result<AlignedBuffer, AuthorityMaintenanceError> {
    let mut required = 0u32;
    unsafe {
        GetTokenInformation(token, class, ptr::null_mut(), 0, &mut required);
    }
    if required == 0
        || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER
        || required as usize > MAX_CANDIDATE_TOKEN_READBACK_BYTES
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_unavailable",
        ));
    }
    let mut buffer = AlignedBuffer::new(required)?;
    let mut returned = 0u32;
    if unsafe {
        GetTokenInformation(
            token,
            class,
            buffer.as_mut_u8().cast(),
            required,
            &mut returned,
        )
    } == 0
        || returned == 0
        || returned > required
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_unavailable",
        ));
    }
    buffer.byte_len = returned as usize;
    Ok(buffer)
}

fn sid_bytes(sid: PSID) -> Result<Vec<u8>, AuthorityMaintenanceError> {
    if sid.is_null() || unsafe { IsValidSid(sid) } == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_groups_invalid",
        ));
    }
    let length = unsafe { GetLengthSid(sid) } as usize;
    if length == 0 || length > 68 {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_groups_invalid",
        ));
    }
    Ok(unsafe { std::slice::from_raw_parts(sid.cast::<u8>(), length) }.to_vec())
}

fn sid_bytes_in_buffer(
    sid: PSID,
    buffer: &AlignedBuffer,
) -> Result<Vec<u8>, AuthorityMaintenanceError> {
    let bytes = sid_bytes(sid)?;
    if !buffer.contains_bytes(sid.cast(), bytes.len()) {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_groups_invalid",
        ));
    }
    Ok(bytes)
}

fn query_fixed_token_value<T: Copy>(
    token: HANDLE,
    class: i32,
) -> Result<T, AuthorityMaintenanceError> {
    let buffer = query_token_buffer(token, class)?;
    if buffer.byte_len != size_of::<T>() {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_principal_invalid",
        ));
    }
    Ok(unsafe { *(buffer.words.as_ptr().cast::<T>()) })
}

fn query_token_principal(
    token: HANDLE,
) -> Result<ObservedTokenPrincipal, AuthorityMaintenanceError> {
    let user_buffer = query_token_buffer(token, TokenUser)?;
    if user_buffer.byte_len < size_of::<TOKEN_USER>() {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_principal_invalid",
        ));
    }
    let user = unsafe { &*(user_buffer.words.as_ptr().cast::<TOKEN_USER>()) };
    let user_sid = sid_bytes_in_buffer(user.User.Sid, &user_buffer)?;

    let integrity_buffer = query_token_buffer(token, TokenIntegrityLevel)?;
    if integrity_buffer.byte_len < size_of::<TOKEN_MANDATORY_LABEL>() {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_principal_invalid",
        ));
    }
    let integrity = unsafe {
        &*(integrity_buffer
            .words
            .as_ptr()
            .cast::<TOKEN_MANDATORY_LABEL>())
    };
    let integrity_sid = sid_bytes_in_buffer(integrity.Label.Sid, &integrity_buffer)?;
    let token_type: TOKEN_TYPE = query_fixed_token_value(token, TokenType)?;
    let session_id: u32 = query_fixed_token_value(token, TokenSessionId)?;
    let mandatory_policy: TOKEN_MANDATORY_POLICY =
        query_fixed_token_value(token, TokenMandatoryPolicy)?;
    Ok(ObservedTokenPrincipal {
        user_sid,
        user_attributes: user.User.Attributes,
        token_type,
        session_id,
        integrity_sid,
        integrity_attributes: integrity.Label.Attributes,
        mandatory_policy: mandatory_policy.Policy,
    })
}

fn query_token_group_set(
    token: HANDLE,
    class: i32,
) -> Result<Vec<ObservedTokenGroup>, AuthorityMaintenanceError> {
    let buffer = query_token_buffer(token, class)?;
    if buffer.byte_len < size_of::<TOKEN_GROUPS>() {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_groups_invalid",
        ));
    }
    let groups = unsafe { &*(buffer.words.as_ptr().cast::<TOKEN_GROUPS>()) };
    let count = groups.GroupCount as usize;
    if count == 0 || count > 1024 {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_groups_invalid",
        ));
    }
    let offset = (ptr::addr_of!(groups.Groups) as usize)
        .checked_sub(groups as *const TOKEN_GROUPS as usize)
        .ok_or(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_groups_invalid",
        ))?;
    let required = offset
        .checked_add(count.checked_mul(size_of::<SID_AND_ATTRIBUTES>()).ok_or(
            AuthorityMaintenanceError("authority_candidate_runtime_token_groups_invalid"),
        )?)
        .ok_or(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_groups_invalid",
        ))?;
    if required > buffer.byte_len {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_groups_invalid",
        ));
    }
    let entries = unsafe { std::slice::from_raw_parts(groups.Groups.as_ptr(), count) };
    let mut observed = Vec::with_capacity(count);
    for entry in entries {
        observed.push(ObservedTokenGroup {
            sid: sid_bytes_in_buffer(entry.Sid, &buffer)?,
            attributes: entry.Attributes,
        });
    }
    normalized_token_group_set(observed)
}

fn query_token_privileges(
    token: HANDLE,
) -> Result<Vec<ObservedTokenPrivilege>, AuthorityMaintenanceError> {
    let buffer = query_token_buffer(token, TokenPrivileges)?;
    if buffer.byte_len < size_of::<TOKEN_PRIVILEGES>() {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_privileges_invalid",
        ));
    }
    let privileges = unsafe { &*(buffer.words.as_ptr().cast::<TOKEN_PRIVILEGES>()) };
    let count = privileges.PrivilegeCount as usize;
    if count == 0 || count > 256 {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_privileges_invalid",
        ));
    }
    let offset = (ptr::addr_of!(privileges.Privileges) as usize)
        .checked_sub(privileges as *const TOKEN_PRIVILEGES as usize)
        .ok_or(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_privileges_invalid",
        ))?;
    let required = offset
        .checked_add(count.checked_mul(size_of::<LUID_AND_ATTRIBUTES>()).ok_or(
            AuthorityMaintenanceError("authority_candidate_runtime_token_privileges_invalid"),
        )?)
        .ok_or(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_privileges_invalid",
        ))?;
    if required > buffer.byte_len {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_privileges_invalid",
        ));
    }
    unsafe { std::slice::from_raw_parts(privileges.Privileges.as_ptr(), count) }
        .iter()
        .map(|privilege| {
            Ok(ObservedTokenPrivilege {
                name: lookup_privilege_name(&privilege.Luid)?,
                attributes: privilege.Attributes,
            })
        })
        .collect()
}

fn lookup_privilege_name(
    luid: &windows_sys::Win32::Foundation::LUID,
) -> Result<String, AuthorityMaintenanceError> {
    let mut required = 0u32;
    unsafe {
        LookupPrivilegeNameW(ptr::null(), luid, ptr::null_mut(), &mut required);
    }
    if required == 0 || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_privilege_name_unavailable",
        ));
    }
    let capacity = required.checked_add(1).ok_or(AuthorityMaintenanceError(
        "authority_candidate_runtime_token_privilege_name_invalid",
    ))?;
    let mut words = vec![0u16; capacity as usize];
    let mut length = capacity;
    if unsafe { LookupPrivilegeNameW(ptr::null(), luid, words.as_mut_ptr(), &mut length) } == 0
        || length == 0
        || length >= capacity
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_privilege_name_unavailable",
        ));
    }
    words.truncate(length as usize);
    if words.contains(&0) {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_runtime_token_privilege_name_invalid",
        ));
    }
    String::from_utf16(&words).map_err(|_| {
        AuthorityMaintenanceError("authority_candidate_runtime_token_privilege_name_invalid")
    })
}

struct OwnedSid(PSID);

impl OwnedSid {
    fn from_text(value: &str) -> Result<Self, AuthorityMaintenanceError> {
        let words = wide_null(value);
        let mut sid = ptr::null_mut();
        if unsafe { ConvertStringSidToSidW(words.as_ptr(), &mut sid) } == 0
            || sid.is_null()
            || unsafe { IsValidSid(sid) } == 0
        {
            if !sid.is_null() {
                unsafe {
                    LocalFree(sid.cast());
                }
            }
            return Err(AuthorityMaintenanceError(
                "authority_candidate_runtime_token_service_sid_invalid",
            ));
        }
        Ok(Self(sid))
    }
}

impl Drop for OwnedSid {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                LocalFree(self.0.cast());
            }
        }
    }
}

struct AlignedBuffer {
    words: Vec<usize>,
    byte_len: usize,
}

impl AlignedBuffer {
    fn new(byte_len: u32) -> Result<Self, AuthorityMaintenanceError> {
        let byte_len = usize::try_from(byte_len).map_err(|_| {
            AuthorityMaintenanceError("authority_candidate_start_readback_too_large")
        })?;
        if byte_len == 0 || byte_len > 1024 * 1024 {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_readback_size_invalid",
            ));
        }
        let words = byte_len
            .checked_add(size_of::<usize>() - 1)
            .map(|value| value / size_of::<usize>())
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_start_readback_too_large",
            ))?;
        Ok(Self {
            words: vec![0; words],
            byte_len,
        })
    }

    fn as_mut_u8(&mut self) -> *mut u8 {
        self.words.as_mut_ptr().cast()
    }

    fn contains_wide_ptr(&self, pointer: *const u16) -> bool {
        let start = self.words.as_ptr() as usize;
        let end = start.saturating_add(self.byte_len);
        let pointer = pointer as usize;
        pointer >= start && pointer < end && pointer % std::mem::align_of::<u16>() == 0
    }

    fn contains_bytes(&self, pointer: *const u8, length: usize) -> bool {
        let start = self.words.as_ptr() as usize;
        let end = match start.checked_add(self.byte_len) {
            Some(value) => value,
            None => return false,
        };
        let pointer = pointer as usize;
        pointer >= start
            && pointer < end
            && length > 0
            && pointer
                .checked_add(length)
                .map(|value| value <= end)
                .unwrap_or(false)
    }
}

fn validate_plan_binding(
    layout: &AuthorityLayout,
    plan: &ExactTargetServicePlan,
    binding: CandidateActivationBinding,
) -> Result<(), AuthorityMaintenanceError> {
    let configuration = plan.configuration();
    let expected_path = expected_service_path(layout, binding)?;
    if !matches!(
        plan.operation(),
        AuthorityMaintenanceOperation::Install | AuthorityMaintenanceOperation::Update
    ) || plan.plan_sha256() != *binding.plan_sha256()
        || plan.transaction_sha256() != *binding.transaction_sha256()
        || plan.generation_sha256() != *binding.generation()
        || plan.expected_service_image_sha256() != *binding.service_image_sha256()
        || plan
            .exact_service_configuration_sha256()
            .iter()
            .all(|byte| *byte == 0)
        || configuration.name != AUTHORITY_SERVICE_NAME
        || configuration.display_name != AUTHORITY_SERVICE_DISPLAY_NAME
        || !configuration
            .account
            .eq_ignore_ascii_case(AUTHORITY_SERVICE_ACCOUNT)
        || configuration.service_type != "ownProcess"
        || configuration.start != "demand"
        || configuration.error_control != "normal"
        || configuration.sid_type != "restricted"
        || configuration.service_sid != SERVICE_SID
        || configuration.required_privileges != AUTHORITY_REQUIRED_PRIVILEGES
        || !persistent_service_command_is_exact(&expected_path, &configuration.binary_command)
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_plan_mismatch",
        ));
    }
    Ok(())
}

fn expected_service_path(
    layout: &AuthorityLayout,
    binding: CandidateActivationBinding,
) -> Result<std::path::PathBuf, AuthorityMaintenanceError> {
    layout
        .service_executable_for_generation(binding.generation())
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_start_path_invalid"))
}

fn persistent_service_command_is_exact(expected_path: &Path, command: &str) -> bool {
    let path = expected_path.to_string_lossy();
    !path.is_empty() && !path.contains('"') && command == format!("\"{path}\" --service")
}

fn verify_exact_service_configuration(
    service: SC_HANDLE,
    plan: &ExactTargetServicePlan,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let expected = plan.configuration();
    let (config, buffer) = query_primary_config(service)?;
    let binary_command = wide_string_in_buffer(config.lpBinaryPathName, &buffer)?;
    let display_name = wide_string_in_buffer(config.lpDisplayName, &buffer)?;
    let account = wide_string_in_buffer(config.lpServiceStartName, &buffer)?;
    let load_order_group = wide_optional_string_in_buffer(config.lpLoadOrderGroup, &buffer)?;
    let dependencies = wide_optional_multi_string_in_buffer(config.lpDependencies, &buffer)?;
    if binary_command != expected.binary_command
        || display_name != expected.display_name
        || !account.eq_ignore_ascii_case(expected.account)
        || config.dwServiceType != SERVICE_WIN32_OWN_PROCESS
        || config.dwStartType != SERVICE_DEMAND_START
        || config.dwErrorControl != SERVICE_ERROR_NORMAL
        || config.dwTagId != 0
        || !load_order_group.is_empty()
        || !dependencies.is_empty()
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_configuration_mismatch",
        ));
    }
    let sid = query_fixed_config::<SERVICE_SID_INFO>(service, SERVICE_CONFIG_SERVICE_SID_INFO)?;
    if sid.dwServiceSidType != AUTHORITY_SERVICE_SID_TYPE_RESTRICTED {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_sid_mismatch",
        ));
    }
    let mut observed_privileges = query_required_privileges(service)?;
    let mut expected_privileges = expected
        .required_privileges
        .iter()
        .map(|value| (*value).to_string())
        .collect::<Vec<_>>();
    observed_privileges.sort();
    expected_privileges.sort();
    if observed_privileges != expected_privileges {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_privileges_mismatch",
        ));
    }
    let observed_sddl = query_service_sddl(service)?;
    let expected_sddl = project_security_sddl(expected.security_sddl)?;
    if observed_sddl != expected_sddl {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_security_mismatch",
        ));
    }
    Ok(plan.exact_service_configuration_sha256())
}

fn query_service_status(
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
            "authority_candidate_start_status_unavailable",
        ));
    }
    Ok(unsafe { status.assume_init() })
}

fn require_exact_stopped_service(
    status: &SERVICE_STATUS_PROCESS,
) -> Result<(), AuthorityMaintenanceError> {
    // Exit codes describe the previous run after SCM reaches STOPPED. They are
    // evidence for that run, not readiness state for the next one. Requiring
    // zero here would permanently wedge a safely contained failed candidate.
    if !service_status_is_exact_stopped(status) {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_service_not_stopped",
        ));
    }
    Ok(())
}

fn service_status_is_exact_stopped(status: &SERVICE_STATUS_PROCESS) -> bool {
    status.dwServiceType == SERVICE_WIN32_OWN_PROCESS
        && status.dwCurrentState == SERVICE_STOPPED
        && status.dwProcessId == 0
}

fn start_pending_process_id(
    status: &SERVICE_STATUS_PROCESS,
) -> Result<Option<u32>, AuthorityMaintenanceError> {
    if status.dwServiceType != SERVICE_WIN32_OWN_PROCESS
        || status.dwCurrentState != SERVICE_START_PENDING
        || status.dwWin32ExitCode != 0
        || status.dwServiceSpecificExitCode != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_pending_mismatch",
        ));
    }
    Ok((status.dwProcessId != 0).then_some(status.dwProcessId))
}

fn wait_for_exact_start_pending_process(
    service: SC_HANDLE,
) -> Result<u32, AuthorityMaintenanceError> {
    let deadline = Instant::now()
        .checked_add(CANDIDATE_SERVICE_START_TIMEOUT)
        .ok_or(AuthorityMaintenanceError(
            "authority_candidate_start_deadline_invalid",
        ))?;
    loop {
        let status = query_service_status(service)?;
        if let Some(process_id) = start_pending_process_id(&status)? {
            return Ok(process_id);
        }
        if Instant::now() >= deadline {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_pending_timeout",
            ));
        }
        std::thread::sleep(CANDIDATE_SERVICE_START_POLL_INTERVAL);
    }
}

fn require_exact_start_pending_process(
    status: &SERVICE_STATUS_PROCESS,
    expected_process_id: u32,
) -> Result<(), AuthorityMaintenanceError> {
    if expected_process_id == 0 || start_pending_process_id(status)? != Some(expected_process_id) {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_pending_process_mismatch",
        ));
    }
    Ok(())
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
            "authority_candidate_start_configuration_unavailable",
        ));
    }
    let mut buffer = AlignedBuffer::new(required)?;
    if unsafe { QueryServiceConfigW(service, buffer.as_mut_u8().cast(), required, &mut required) }
        == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_configuration_unavailable",
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
            "authority_candidate_start_configuration_unavailable",
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
            "authority_candidate_start_privileges_unavailable",
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
            "authority_candidate_start_privileges_unavailable",
        ));
    }
    let info = unsafe {
        *(buffer
            .words
            .as_ptr()
            .cast::<SERVICE_REQUIRED_PRIVILEGES_INFOW>())
    };
    wide_optional_multi_string_in_buffer(info.pmszRequiredPrivileges, &buffer)
}

fn query_service_sddl(service: SC_HANDLE) -> Result<String, AuthorityMaintenanceError> {
    let mut required = 0u32;
    unsafe {
        QueryServiceObjectSecurity(
            service,
            SERVICE_SECURITY_INFORMATION,
            ptr::null_mut(),
            0,
            &mut required,
        );
    }
    if required == 0 || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_security_unavailable",
        ));
    }
    let mut buffer = AlignedBuffer::new(required)?;
    if unsafe {
        QueryServiceObjectSecurity(
            service,
            SERVICE_SECURITY_INFORMATION,
            buffer.as_mut_u8().cast(),
            required,
            &mut required,
        )
    } == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_security_unavailable",
        ));
    }
    descriptor_sddl(buffer.as_mut_u8().cast())
}

fn project_security_sddl(value: &str) -> Result<String, AuthorityMaintenanceError> {
    let words = wide_null(value);
    let mut descriptor = ptr::null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            words.as_ptr(),
            SDDL_REVISION_1,
            &mut descriptor,
            ptr::null_mut(),
        )
    } == 0
        || descriptor.is_null()
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_security_invalid",
        ));
    }
    let descriptor = OwnedSecurityDescriptor(descriptor);
    descriptor_sddl(descriptor.0)
}

struct OwnedSecurityDescriptor(PSECURITY_DESCRIPTOR);

impl Drop for OwnedSecurityDescriptor {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                LocalFree(self.0.cast());
            }
        }
    }
}

fn descriptor_sddl(descriptor: PSECURITY_DESCRIPTOR) -> Result<String, AuthorityMaintenanceError> {
    let mut text = ptr::null_mut::<u16>();
    let mut length = 0u32;
    if unsafe {
        ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            SDDL_REVISION_1,
            SERVICE_SECURITY_INFORMATION,
            &mut text,
            &mut length,
        )
    } == 0
        || text.is_null()
        || length == 0
    {
        if !text.is_null() {
            unsafe {
                LocalFree(text.cast());
            }
        }
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_security_unavailable",
        ));
    }
    let mut words = unsafe { std::slice::from_raw_parts(text, length as usize) }.to_vec();
    unsafe {
        LocalFree(text.cast());
    }
    let terminator = words
        .iter()
        .position(|word| *word == 0)
        .ok_or(AuthorityMaintenanceError(
            "authority_candidate_start_security_unavailable",
        ))?;
    if terminator == 0 || words[terminator..].iter().any(|word| *word != 0) {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_security_unavailable",
        ));
    }
    words.truncate(terminator);
    String::from_utf16(&words)
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_start_security_unavailable"))
}

fn wide_string_in_buffer(
    pointer: *const u16,
    buffer: &AlignedBuffer,
) -> Result<String, AuthorityMaintenanceError> {
    if pointer.is_null() || !buffer.contains_wide_ptr(pointer) {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_string_invalid",
        ));
    }
    wide_string_at(pointer, buffer)
}

fn wide_optional_string_in_buffer(
    pointer: *const u16,
    buffer: &AlignedBuffer,
) -> Result<String, AuthorityMaintenanceError> {
    if pointer.is_null() {
        return Ok(String::new());
    }
    if !buffer.contains_wide_ptr(pointer) {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_string_invalid",
        ));
    }
    wide_string_at(pointer, buffer)
}

fn wide_string_at(
    pointer: *const u16,
    buffer: &AlignedBuffer,
) -> Result<String, AuthorityMaintenanceError> {
    let maximum = ((buffer.words.as_ptr() as usize + buffer.byte_len) - pointer as usize) / 2;
    let mut length = 0usize;
    while length < maximum && unsafe { *pointer.add(length) } != 0 {
        length += 1;
    }
    if length == maximum {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_string_invalid",
        ));
    }
    String::from_utf16(unsafe { std::slice::from_raw_parts(pointer, length) })
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_start_string_invalid"))
}

fn wide_optional_multi_string_in_buffer(
    pointer: *const u16,
    buffer: &AlignedBuffer,
) -> Result<Vec<String>, AuthorityMaintenanceError> {
    if pointer.is_null() {
        return Ok(Vec::new());
    }
    if !buffer.contains_wide_ptr(pointer) {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_start_multi_string_invalid",
        ));
    }
    let maximum = ((buffer.words.as_ptr() as usize + buffer.byte_len) - pointer as usize) / 2;
    let mut values = Vec::new();
    let mut offset = 0usize;
    loop {
        if offset >= maximum {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_multi_string_invalid",
            ));
        }
        let start = offset;
        while offset < maximum && unsafe { *pointer.add(offset) } != 0 {
            offset += 1;
        }
        if offset == maximum {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_multi_string_invalid",
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
                AuthorityMaintenanceError("authority_candidate_start_multi_string_invalid")
            })?,
        );
        offset += 1;
    }
    Ok(values)
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
    use crate::primitive_evidence_authority_install::bootstrap_activation::{
        CandidateActivationObservation, CandidateImageEvidence,
    };

    const TEST_SERVICE_LOGON_SID: &str = "S-1-5-5-100-200";

    macro_rules! assert_not_impl {
        ($type:ty: $trait:path) => {
            const _: fn() = || {
                trait AmbiguousIfImpl<A> {
                    fn check() {}
                }
                impl<T: ?Sized> AmbiguousIfImpl<()> for T {}
                impl<T: ?Sized + $trait> AmbiguousIfImpl<u8> for T {}
                let _ = <$type as AmbiguousIfImpl<_>>::check;
            };
        };
    }

    assert_not_impl!(CandidateActivationSealReadyProjection: Clone);
    assert_not_impl!(CandidateCompletedBundle: Clone);
    assert_not_impl!(CandidateCompletedBundle: Copy);
    assert_not_impl!(RestrictedPrecommitStartAuthorization: Clone);
    assert_not_impl!(RestrictedPrecommitStartAuthorization: Copy);

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    enum Event {
        CredentialBefore,
        ConfigurationBefore,
        Start,
        BindProcess,
        RevalidateProcess,
        ConfigurationAfter,
        CredentialAfter,
        Contain,
    }

    struct FakeOperations {
        binding: CandidateActivationBinding,
        candidate_service: CandidateProcessEvidence,
        configuration: [u8; 32],
        credential: CandidateCredentialReadback,
        events: Vec<Event>,
        configuration_drift: bool,
        credential_drift: bool,
        containment_failure: bool,
        bind_failure: bool,
        revalidate_failure: bool,
    }

    impl CandidateServiceStartOperations for FakeOperations {
        fn credential_readback(
            &mut self,
        ) -> Result<CandidateCredentialReadback, AuthorityMaintenanceError> {
            let after = self.events.contains(&Event::Start);
            self.events.push(if after {
                Event::CredentialAfter
            } else {
                Event::CredentialBefore
            });
            let mut value = self.credential;
            if after && self.credential_drift {
                value.bytes_sha256[0] ^= 1;
            }
            Ok(value)
        }

        fn service_configuration_readback(
            &mut self,
        ) -> Result<[u8; 32], AuthorityMaintenanceError> {
            let after = self.events.contains(&Event::Start);
            self.events.push(if after {
                Event::ConfigurationAfter
            } else {
                Event::ConfigurationBefore
            });
            let mut value = self.configuration;
            if after && self.configuration_drift {
                value[0] ^= 1;
            }
            Ok(value)
        }

        fn start_exact(
            &mut self,
            arguments: &CandidateServiceStartArguments,
        ) -> Result<(), AuthorityMaintenanceError> {
            arguments.validate_against(self.binding)?;
            self.events.push(Event::Start);
            Ok(())
        }

        fn bind_start_pending_process(
            &mut self,
        ) -> Result<CandidateProcessEvidence, AuthorityMaintenanceError> {
            self.events.push(Event::BindProcess);
            if self.bind_failure {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_start_test_bind_failed",
                ));
            }
            Ok(self.candidate_service)
        }

        fn revalidate_start_pending_process(
            &mut self,
            candidate_service: CandidateProcessEvidence,
        ) -> Result<(), AuthorityMaintenanceError> {
            self.events.push(Event::RevalidateProcess);
            if self.revalidate_failure || candidate_service != self.candidate_service {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_start_process_changed",
                ));
            }
            Ok(())
        }

        fn start_was_issued(&self) -> bool {
            self.events.contains(&Event::Start)
        }

        fn contain_started_failure(&mut self) -> Result<(), AuthorityMaintenanceError> {
            self.events.push(Event::Contain);
            if self.containment_failure {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_start_test_containment_failed",
                ));
            }
            Ok(())
        }
    }

    fn binding_variant(
        generation: [u8; 32],
        transaction_sha256: [u8; 32],
        nonce: [u8; 32],
        expires_at_unix_millis: u64,
    ) -> CandidateActivationBinding {
        let observation = CandidateActivationObservation::new(
            generation,
            [0x12; 32],
            transaction_sha256,
            7,
            [0x14; 32],
            [0x15; 32],
            [0x16; 32],
            [0x17; 32],
            [0x41; 32],
            919,
            42_424,
        )
        .unwrap();
        CandidateActivationBinding::new(observation, nonce, 10_000, expires_at_unix_millis).unwrap()
    }

    fn binding() -> CandidateActivationBinding {
        binding_variant([0x11; 32], [0x13; 32], [0x19; 32], 20_000)
    }

    fn fake_operations(binding: CandidateActivationBinding) -> FakeOperations {
        FakeOperations {
            binding,
            candidate_service: CandidateProcessEvidence::from_static_image(
                1771,
                88_181,
                *binding.target_service_image(),
            )
            .unwrap(),
            configuration: [0x71; 32],
            credential: CandidateCredentialReadback {
                volume_serial: 72,
                file_id: [0x73; 16],
                bytes_sha256: [0x74; 32],
            },
            events: Vec::new(),
            configuration_drift: false,
            credential_drift: false,
            containment_failure: false,
            bind_failure: false,
            revalidate_failure: false,
        }
    }

    fn expected_token_privileges() -> Vec<ObservedTokenPrivilege> {
        AUTHORITY_REQUIRED_PRIVILEGES
            .iter()
            .copied()
            .chain(std::iter::once(CHANGE_NOTIFY_PRIVILEGE))
            .map(|name| ObservedTokenPrivilege {
                name: name.to_string(),
                attributes: if name == CHANGE_NOTIFY_PRIVILEGE {
                    SE_PRIVILEGE_ENABLED_BY_DEFAULT | SE_PRIVILEGE_ENABLED
                } else {
                    0
                },
            })
            .collect()
    }

    fn sid_for_test(value: &str) -> Vec<u8> {
        sid_bytes(OwnedSid::from_text(value).unwrap().0).unwrap()
    }

    fn expected_token_principal() -> ObservedTokenPrincipal {
        ObservedTokenPrincipal {
            user_sid: sid_for_test(LOCAL_SYSTEM_SID),
            user_attributes: 0,
            token_type: TokenPrimary,
            session_id: 0,
            integrity_sid: sid_for_test(SYSTEM_INTEGRITY_SID),
            integrity_attributes: SYSTEM_INTEGRITY_ATTRIBUTES,
            mandatory_policy: TOKEN_MANDATORY_POLICY_NO_WRITE_UP,
        }
    }

    fn expected_enabled_groups() -> Vec<ObservedTokenGroup> {
        vec![
            ObservedTokenGroup {
                sid: sid_for_test(SERVICE_SID),
                attributes: SERVICE_SID_ENABLED_ATTRIBUTES,
            },
            ObservedTokenGroup {
                sid: sid_for_test(TEST_SERVICE_LOGON_SID),
                attributes: SERVICE_LOGON_SID_ENABLED_ATTRIBUTES,
            },
        ]
    }

    fn expected_restricting_groups() -> Vec<ObservedTokenGroup> {
        [
            SERVICE_SID,
            WORLD_SID,
            TEST_SERVICE_LOGON_SID,
            WRITE_RESTRICTED_SID,
        ]
        .into_iter()
        .map(|sid| ObservedTokenGroup {
            sid: sid_for_test(sid),
            attributes: SERVICE_SID_RESTRICTING_ATTRIBUTES,
        })
        .collect()
    }

    #[test]
    fn exact_five_argument_vector_is_accepted_and_shape_locked() {
        let binding = binding();
        let ordered = SensitiveOrderedArguments(
            CandidateServiceStartLocator::from_binding(binding).ordered_service_arguments(),
        );
        let references = ordered.0.each_ref().map(String::as_str);
        validate_ordered_candidate_service_arguments(binding, &references).unwrap();

        assert_eq!(
            validate_ordered_candidate_service_arguments(binding, &[])
                .unwrap_err()
                .code(),
            "authority_candidate_start_locator_invalid"
        );
        assert_eq!(
            validate_ordered_candidate_service_arguments(binding, &references[..4])
                .unwrap_err()
                .code(),
            "authority_candidate_start_locator_invalid"
        );
        let mut extra = references.to_vec();
        extra.push("--unexpected");
        assert_eq!(
            validate_ordered_candidate_service_arguments(binding, &extra)
                .unwrap_err()
                .code(),
            "authority_candidate_start_locator_invalid"
        );
        let mut reordered = references;
        reordered.swap(1, 2);
        assert_eq!(
            validate_ordered_candidate_service_arguments(binding, &reordered)
                .unwrap_err()
                .code(),
            "authority_candidate_start_locator_invalid"
        );
    }

    #[test]
    fn start_sequence_brackets_start_with_held_readbacks() {
        let binding = binding();
        let mut operations = fake_operations(binding);
        let result = execute_candidate_service_start(binding, [0x71; 32], &mut operations).unwrap();
        assert_eq!(result.candidate_service, operations.candidate_service);
        assert!(result.credential_readback == operations.credential);
        assert!(
            operations.events
                == [
                    Event::CredentialBefore,
                    Event::ConfigurationBefore,
                    Event::Start,
                    Event::BindProcess,
                    Event::RevalidateProcess,
                    Event::ConfigurationAfter,
                    Event::CredentialAfter,
                ]
        );
    }

    #[test]
    fn pre_start_failure_does_not_claim_a_started_process_or_containment() {
        let binding = binding();
        let mut operations = fake_operations(binding);
        assert_eq!(
            execute_candidate_service_start(binding, [0x72; 32], &mut operations)
                .err()
                .unwrap()
                .code(),
            "authority_candidate_start_configuration_mismatch"
        );
        assert!(!operations.events.contains(&Event::Start));
        assert!(!operations.events.contains(&Event::Contain));
    }

    #[test]
    fn post_start_configuration_or_credential_drift_fails_closed() {
        let binding = binding();
        let mut configuration_drift = fake_operations(binding);
        configuration_drift.configuration_drift = true;
        assert_eq!(
            execute_candidate_service_start(binding, [0x71; 32], &mut configuration_drift)
                .err()
                .unwrap()
                .code(),
            "authority_candidate_start_readback_changed"
        );
        assert_eq!(configuration_drift.events.last(), Some(&Event::Contain));

        let mut credential_drift = fake_operations(binding);
        credential_drift.credential_drift = true;
        assert_eq!(
            execute_candidate_service_start(binding, [0x71; 32], &mut credential_drift)
                .err()
                .unwrap()
                .code(),
            "authority_candidate_start_readback_changed"
        );
        assert_eq!(credential_drift.events.last(), Some(&Event::Contain));
    }

    #[test]
    fn post_start_failure_without_proven_containment_is_not_returned_as_safe() {
        let binding = binding();
        let mut operations = fake_operations(binding);
        operations.configuration_drift = true;
        operations.containment_failure = true;
        assert_eq!(
            execute_candidate_service_start(binding, [0x71; 32], &mut operations)
                .err()
                .unwrap()
                .code(),
            "authority_candidate_start_containment_failed"
        );
        assert_eq!(operations.events.last(), Some(&Event::Contain));
    }

    #[test]
    fn lease_transfer_precheck_failure_after_start_runs_containment() {
        let binding = binding();
        let mut operations = fake_operations(binding);
        operations.events.push(Event::Start);
        let error = resolve_started_result::<_, ()>(
            &mut operations,
            Err(AuthorityMaintenanceError(
                "authority_candidate_start_credential_readback_invalid",
            )),
        )
        .unwrap_err();
        assert_eq!(
            error.code(),
            "authority_candidate_start_credential_readback_invalid"
        );
        assert_eq!(operations.events.last(), Some(&Event::Contain));

        let mut failed_containment = fake_operations(binding);
        failed_containment.events.push(Event::Start);
        failed_containment.containment_failure = true;
        let error = resolve_started_result::<_, ()>(
            &mut failed_containment,
            Err(AuthorityMaintenanceError(
                "authority_candidate_start_credential_readback_invalid",
            )),
        )
        .unwrap_err();
        assert_eq!(error.code(), "authority_candidate_start_containment_failed");
        assert_eq!(failed_containment.events.last(), Some(&Event::Contain));
    }

    #[test]
    fn every_process_binding_failure_after_start_runs_containment() {
        let binding = binding();
        let mut bind_failure = fake_operations(binding);
        bind_failure.bind_failure = true;
        assert_eq!(
            execute_candidate_service_start(binding, [0x71; 32], &mut bind_failure)
                .err()
                .unwrap()
                .code(),
            "authority_candidate_start_test_bind_failed"
        );
        assert_eq!(bind_failure.events.last(), Some(&Event::Contain));

        let mut revalidate_failure = fake_operations(binding);
        revalidate_failure.revalidate_failure = true;
        assert_eq!(
            execute_candidate_service_start(binding, [0x71; 32], &mut revalidate_failure)
                .err()
                .unwrap()
                .code(),
            "authority_candidate_start_process_changed"
        );
        assert_eq!(revalidate_failure.events.last(), Some(&Event::Contain));
    }

    #[test]
    fn only_persistent_runtime_command_is_accepted() {
        let path = Path::new(r"C:\Program Files\VRCForgeEvidenceAuthority\service.exe");
        assert!(persistent_service_command_is_exact(
            path,
            r#""C:\Program Files\VRCForgeEvidenceAuthority\service.exe" --service"#,
        ));
        assert!(!persistent_service_command_is_exact(
            path,
            r#""C:\Program Files\VRCForgeEvidenceAuthority\service.exe""#,
        ));
        assert!(!persistent_service_command_is_exact(
            path,
            r#""C:\Program Files\VRCForgeEvidenceAuthority\service.exe" --service --candidate-validation-v1"#,
        ));
    }

    #[test]
    fn start_pending_readback_requires_exact_state_pid_and_zero_exit() {
        let baseline = SERVICE_STATUS_PROCESS {
            dwServiceType: SERVICE_WIN32_OWN_PROCESS,
            dwCurrentState: SERVICE_START_PENDING,
            dwControlsAccepted: 0,
            dwWin32ExitCode: 0,
            dwServiceSpecificExitCode: 0,
            dwCheckPoint: 1,
            dwWaitHint: 30_000,
            dwProcessId: 4242,
            dwServiceFlags: 0,
        };
        assert_eq!(start_pending_process_id(&baseline).unwrap(), Some(4242));
        let mut zero_pid = baseline;
        zero_pid.dwProcessId = 0;
        assert_eq!(start_pending_process_id(&zero_pid).unwrap(), None);
        let mut wrong_state = baseline;
        wrong_state.dwCurrentState = SERVICE_STOPPED;
        assert_eq!(
            start_pending_process_id(&wrong_state).unwrap_err().code(),
            "authority_candidate_start_pending_mismatch"
        );
        let mut failed = baseline;
        failed.dwWin32ExitCode = 1;
        assert_eq!(
            start_pending_process_id(&failed).unwrap_err().code(),
            "authority_candidate_start_pending_mismatch"
        );
    }

    #[test]
    fn stopped_readiness_ignores_prior_exit_outcome_but_requires_pid_zero() {
        let baseline = SERVICE_STATUS_PROCESS {
            dwServiceType: SERVICE_WIN32_OWN_PROCESS,
            dwCurrentState: SERVICE_STOPPED,
            dwControlsAccepted: 0,
            dwWin32ExitCode: 1066,
            dwServiceSpecificExitCode: 73,
            dwCheckPoint: 0,
            dwWaitHint: 0,
            dwProcessId: 0,
            dwServiceFlags: 0,
        };
        require_exact_stopped_service(&baseline).unwrap();
        let mut stale_pid = baseline;
        stale_pid.dwProcessId = 4242;
        assert_eq!(
            require_exact_stopped_service(&stale_pid)
                .unwrap_err()
                .code(),
            "authority_candidate_start_service_not_stopped"
        );
    }

    #[test]
    fn containment_receipt_binds_failed_exit_and_exact_stopped_state() {
        let binding = binding();
        let candidate = CandidateProcessEvidence::from_static_image(
            1771,
            88_181,
            *binding.target_service_image(),
        )
        .unwrap();
        let stopped = SERVICE_STATUS_PROCESS {
            dwServiceType: SERVICE_WIN32_OWN_PROCESS,
            dwCurrentState: SERVICE_STOPPED,
            dwControlsAccepted: 0,
            dwWin32ExitCode: 1066,
            dwServiceSpecificExitCode: 73,
            dwCheckPoint: 0,
            dwWaitHint: 0,
            dwProcessId: 0,
            dwServiceFlags: 0,
        };
        let credential = CandidateCredentialReadback {
            volume_serial: 72,
            file_id: [0x73; 16],
            bytes_sha256: [0x74; 32],
        };
        let readback = CandidateStartContainmentReadback::from_observed(
            candidate, 2, &stopped, [0x71; 32], credential,
        )
        .unwrap();
        readback.validate().unwrap();
        assert_ne!(readback.receipt_sha256, [0; 32]);

        let mut still_running = stopped;
        still_running.dwCurrentState = SERVICE_START_PENDING;
        still_running.dwProcessId = 1771;
        assert_eq!(
            CandidateStartContainmentReadback::from_observed(
                candidate,
                2,
                &still_running,
                [0x71; 32],
                credential,
            )
            .err()
            .unwrap()
            .code(),
            "authority_candidate_start_containment_invalid"
        );
    }

    #[test]
    fn containment_keeps_ownership_until_process_exit_is_proven() {
        assert_eq!(INFINITE, u32::MAX);
        assert!(std::mem::needs_drop::<NativeCandidateServiceStartLease>());
        assert!(candidate_process_exit_is_proven(WAIT_OBJECT_0));
        assert!(!candidate_process_exit_is_proven(
            windows_sys::Win32::Foundation::WAIT_TIMEOUT
        ));
        assert!(!candidate_process_exit_is_proven(
            windows_sys::Win32::Foundation::WAIT_FAILED
        ));
    }

    #[test]
    fn explicit_abort_receipt_rejects_configuration_or_credential_drift() {
        let binding = binding();
        let candidate = CandidateProcessEvidence::from_static_image(
            1771,
            88_181,
            *binding.target_service_image(),
        )
        .unwrap();
        let stopped = SERVICE_STATUS_PROCESS {
            dwServiceType: SERVICE_WIN32_OWN_PROCESS,
            dwCurrentState: SERVICE_STOPPED,
            dwControlsAccepted: 0,
            dwWin32ExitCode: 7,
            dwServiceSpecificExitCode: 8,
            dwCheckPoint: 0,
            dwWaitHint: 0,
            dwProcessId: 0,
            dwServiceFlags: 0,
        };
        let credential = CandidateCredentialReadback {
            volume_serial: 72,
            file_id: [0x73; 16],
            bytes_sha256: [0x74; 32],
        };
        let exact_service_identity =
            CandidateExactServiceIdentityObservation::for_test([0x71; 32], candidate, [0x72; 32])
                .unwrap();
        let readback = verified_candidate_abort_readback(
            exact_service_identity,
            candidate,
            2,
            &stopped,
            [0x71; 32],
            [0x71; 32],
            credential,
            credential,
        )
        .unwrap();
        readback.validate().unwrap();
        assert_ne!(readback.receipt_sha256(), [0; 32]);

        assert_eq!(
            verified_candidate_abort_readback(
                exact_service_identity,
                candidate,
                2,
                &stopped,
                [0x71; 32],
                [0x72; 32],
                credential,
                credential,
            )
            .err()
            .unwrap()
            .code(),
            "authority_candidate_start_abort_readback_changed"
        );
        let mut drifted_credential = credential;
        drifted_credential.bytes_sha256[0] ^= 1;
        assert_eq!(
            verified_candidate_abort_readback(
                exact_service_identity,
                candidate,
                2,
                &stopped,
                [0x71; 32],
                [0x71; 32],
                credential,
                drifted_credential,
            )
            .err()
            .unwrap()
            .code(),
            "authority_candidate_start_abort_readback_changed"
        );
    }

    #[test]
    fn successful_terminal_requires_exact_seal_ready_projection_and_zero_exit() {
        let binding = binding();
        let candidate = CandidateProcessEvidence::from_static_image(
            1771,
            88_181,
            *binding.target_service_image(),
        )
        .unwrap();
        let identity =
            CandidateExactServiceIdentityObservation::for_test([0x71; 32], candidate, [0x72; 32])
                .unwrap();
        let projection = CandidateActivationSealReadyProjection::for_test(
            binding, [0x71; 32], [0x72; 32], candidate, [0x73; 32], 74, [0x75; 16], 1,
        )
        .unwrap();
        let credential_readback = CandidateCredentialReadback {
            volume_serial: 72,
            file_id: [0x73; 16],
            bytes_sha256: [0x74; 32],
        };
        let stopped = SERVICE_STATUS_PROCESS {
            dwServiceType: SERVICE_WIN32_OWN_PROCESS,
            dwCurrentState: SERVICE_STOPPED,
            dwControlsAccepted: 0,
            dwWin32ExitCode: 0,
            dwServiceSpecificExitCode: 0,
            dwCheckPoint: 0,
            dwWaitHint: 0,
            dwProcessId: 0,
            dwServiceFlags: 0,
        };
        let terminal = CandidateServiceStartTerminalReadback::from_verified_terminal(
            identity,
            binding,
            [0x91; 32],
            credential_readback,
            &projection,
            0,
            &stopped,
        )
        .unwrap();
        terminal.validate().unwrap();
        assert_ne!(terminal.receipt_sha256(), [0; 32]);
        assert_eq!(
            CandidateServiceStartTerminalReadback::from_verified_terminal(
                identity,
                binding,
                [0x91; 32],
                credential_readback,
                &projection,
                1,
                &stopped,
            )
            .err()
            .unwrap()
            .code(),
            "authority_candidate_start_terminal_mismatch"
        );
    }

    #[test]
    fn completed_bundle_is_the_only_production_precommit_authorization_source() {
        let binding = binding();
        let candidate = CandidateProcessEvidence::from_static_image(
            1771,
            88_181,
            *binding.target_service_image(),
        )
        .unwrap();
        let identity =
            CandidateExactServiceIdentityObservation::for_test([0x71; 32], candidate, [0x72; 32])
                .unwrap();
        let projection = CandidateActivationSealReadyProjection::for_test(
            binding, [0x71; 32], [0x72; 32], candidate, [0x73; 32], 74, [0x75; 16], 1,
        )
        .unwrap();
        let credential_readback = CandidateCredentialReadback {
            volume_serial: 72,
            file_id: [0x73; 16],
            bytes_sha256: [0x74; 32],
        };
        let stopped = SERVICE_STATUS_PROCESS {
            dwServiceType: SERVICE_WIN32_OWN_PROCESS,
            dwCurrentState: SERVICE_STOPPED,
            dwControlsAccepted: 0,
            dwWin32ExitCode: 0,
            dwServiceSpecificExitCode: 0,
            dwCheckPoint: 0,
            dwWaitHint: 0,
            dwProcessId: 0,
            dwServiceFlags: 0,
        };
        let terminal = CandidateServiceStartTerminalReadback::from_verified_terminal(
            identity,
            binding,
            [0x91; 32],
            credential_readback,
            &projection,
            0,
            &stopped,
        )
        .unwrap();
        let bundle = CandidateCompletedBundle::from_verified(terminal, projection).unwrap();
        bundle.validate().unwrap();
        assert_ne!(bundle.terminal_receipt_sha256(), [0; 32]);
        let authorization = bundle
            .into_restricted_precommit_start_authorization()
            .unwrap();
        let (locator, transferred_binding) = authorization.into_parts().unwrap();
        assert_eq!(transferred_binding, binding);
        locator.validate_binding(binding).unwrap();
    }

    #[test]
    fn successful_terminal_rejects_cross_activation_binding_or_prepared_record() {
        let start_binding = binding();
        let candidate = CandidateProcessEvidence::from_static_image(
            1771,
            88_181,
            *start_binding.target_service_image(),
        )
        .unwrap();
        let identity =
            CandidateExactServiceIdentityObservation::for_test([0x71; 32], candidate, [0x72; 32])
                .unwrap();
        let credential_readback = CandidateCredentialReadback {
            volume_serial: 72,
            file_id: [0x73; 16],
            bytes_sha256: [0x74; 32],
        };
        let stopped = SERVICE_STATUS_PROCESS {
            dwServiceType: SERVICE_WIN32_OWN_PROCESS,
            dwCurrentState: SERVICE_STOPPED,
            dwControlsAccepted: 0,
            dwWin32ExitCode: 0,
            dwServiceSpecificExitCode: 0,
            dwCheckPoint: 0,
            dwWaitHint: 0,
            dwProcessId: 0,
            dwServiceFlags: 0,
        };

        for drifted_binding in [
            binding_variant([0x1a; 32], [0x13; 32], [0x19; 32], 20_000),
            binding_variant([0x11; 32], [0x1b; 32], [0x19; 32], 20_000),
            binding_variant([0x11; 32], [0x13; 32], [0x1c; 32], 20_000),
            binding_variant([0x11; 32], [0x13; 32], [0x19; 32], 20_001),
        ] {
            assert_eq!(
                drifted_binding.target_service_image(),
                start_binding.target_service_image()
            );
            let projection = CandidateActivationSealReadyProjection::for_test(
                drifted_binding,
                [0x71; 32],
                [0x72; 32],
                candidate,
                [0x73; 32],
                74,
                [0x75; 16],
                1,
            )
            .unwrap();
            assert_eq!(
                CandidateServiceStartTerminalReadback::from_verified_terminal(
                    identity,
                    start_binding,
                    [0x91; 32],
                    credential_readback,
                    &projection,
                    0,
                    &stopped,
                )
                .err()
                .unwrap()
                .code(),
                "authority_candidate_start_terminal_binding_mismatch"
            );
        }

        let projection = CandidateActivationSealReadyProjection::for_test(
            start_binding,
            [0x71; 32],
            [0x72; 32],
            candidate,
            [0x73; 32],
            74,
            [0x75; 16],
            1,
        )
        .unwrap();
        assert_eq!(
            CandidateServiceStartTerminalReadback::from_verified_terminal(
                identity,
                start_binding,
                [0x90; 32],
                credential_readback,
                &projection,
                0,
                &stopped,
            )
            .err()
            .unwrap()
            .code(),
            "authority_candidate_start_terminal_binding_mismatch"
        );
    }

    #[test]
    fn full_service_security_projection_rejects_label_drift_or_omission() {
        let service_sddl =
            crate::primitive_evidence_authority_windows::AUTHORITY_SERVICE_SECURITY_SDDL;
        let expected = project_security_sddl(service_sddl).unwrap();
        assert!(expected.contains("S:(ML;;NW;;;HI)"));
        let medium =
            project_security_sddl(&service_sddl.replace("S:(ML;;NW;;;HI)", "S:(ML;;NW;;;ME)"))
                .unwrap();
        let without_label =
            project_security_sddl(service_sddl.split("S:(ML;;NW;;;HI)").next().unwrap()).unwrap();
        assert_ne!(medium, expected);
        assert_ne!(without_label, expected);
    }

    #[test]
    fn label_readback_does_not_request_audit_sacl_privilege() {
        assert_ne!(SERVICE_SECURITY_INFORMATION & LABEL_SECURITY_INFORMATION, 0);
        assert_eq!(
            SERVICE_SECURITY_INFORMATION & windows_sys::Win32::Security::SACL_SECURITY_INFORMATION,
            0
        );
        assert_eq!(
            CANDIDATE_SERVICE_ACCESS
                & windows_sys::Win32::System::SystemServices::ACCESS_SYSTEM_SECURITY,
            0
        );
        assert_ne!(CANDIDATE_SERVICE_ACCESS & READ_CONTROL, 0);
    }

    #[test]
    fn runtime_token_observation_requires_exact_principal_sid_sets_and_privileges() {
        let binding = binding();
        let candidate = CandidateProcessEvidence::from_static_image(
            1771,
            88_181,
            *binding.target_service_image(),
        )
        .unwrap();
        let baseline = CandidateRuntimeTokenObservation::from_observed(
            candidate,
            true,
            expected_token_principal(),
            expected_enabled_groups(),
            expected_restricting_groups(),
            expected_token_privileges(),
        )
        .unwrap();
        assert_ne!(baseline.receipt_sha256(), [0; 32]);
        assert_ne!(baseline.privilege_set_sha256(), [0; 32]);
        assert_ne!(baseline.principal_sha256(), [0; 32]);
        assert_ne!(baseline.service_logon_sid_sha256(), [0; 32]);
        let group_digests = baseline.group_set_sha256();
        assert_ne!(group_digests.0, [0; 32]);
        assert_ne!(group_digests.1, [0; 32]);
        assert_eq!(
            baseline.service_sid_attributes(),
            (
                SERVICE_SID_ENABLED_ATTRIBUTES,
                SERVICE_SID_RESTRICTING_ATTRIBUTES,
            )
        );

        let mut missing_enabled = expected_enabled_groups();
        missing_enabled.clear();
        let mut enabled_attribute_drift = expected_enabled_groups();
        enabled_attribute_drift[0].attributes = SE_GROUP_ENABLED as u32;
        let mut missing_restricting = expected_restricting_groups();
        missing_restricting.clear();
        let mut restricting_attribute_drift = expected_restricting_groups();
        restricting_attribute_drift[0].attributes = SE_GROUP_ENABLED as u32;
        for (restricted, enabled_groups, restricting_groups, expected_code) in [
            (
                false,
                expected_enabled_groups(),
                expected_restricting_groups(),
                "authority_candidate_runtime_token_service_sid_mismatch",
            ),
            (
                true,
                missing_enabled,
                expected_restricting_groups(),
                "authority_candidate_runtime_token_groups_invalid",
            ),
            (
                true,
                enabled_attribute_drift,
                expected_restricting_groups(),
                "authority_candidate_runtime_token_service_sid_mismatch",
            ),
            (
                true,
                expected_enabled_groups(),
                missing_restricting,
                "authority_candidate_runtime_token_groups_invalid",
            ),
            (
                true,
                expected_enabled_groups(),
                restricting_attribute_drift,
                "authority_candidate_runtime_token_restricting_sid_set_mismatch",
            ),
        ] {
            assert_eq!(
                CandidateRuntimeTokenObservation::from_observed(
                    candidate,
                    restricted,
                    expected_token_principal(),
                    enabled_groups,
                    restricting_groups,
                    expected_token_privileges(),
                )
                .err()
                .unwrap()
                .code(),
                expected_code
            );
        }

        let extra_sid = sid_for_test(LOCAL_SYSTEM_SID);
        let mut extra_restricting = expected_restricting_groups();
        extra_restricting.push(ObservedTokenGroup {
            sid: extra_sid.clone(),
            attributes: 0,
        });
        assert_eq!(
            CandidateRuntimeTokenObservation::from_observed(
                candidate,
                true,
                expected_token_principal(),
                expected_enabled_groups(),
                extra_restricting,
                expected_token_privileges(),
            )
            .err()
            .unwrap()
            .code(),
            "authority_candidate_runtime_token_restricting_sid_set_mismatch"
        );
        let service_group = expected_restricting_groups().pop().unwrap();
        assert_eq!(
            CandidateRuntimeTokenObservation::from_observed(
                candidate,
                true,
                expected_token_principal(),
                expected_enabled_groups(),
                vec![service_group.clone(), service_group],
                expected_token_privileges(),
            )
            .err()
            .unwrap()
            .code(),
            "authority_candidate_runtime_token_groups_invalid"
        );

        let mut enabled_with_extra = expected_enabled_groups();
        enabled_with_extra.push(ObservedTokenGroup {
            sid: extra_sid,
            attributes: SE_GROUP_ENABLED as u32,
        });
        let enabled_extra = CandidateRuntimeTokenObservation::from_observed(
            candidate,
            true,
            expected_token_principal(),
            enabled_with_extra,
            expected_restricting_groups(),
            expected_token_privileges(),
        )
        .unwrap();
        assert_ne!(
            baseline.group_set_sha256().0,
            enabled_extra.group_set_sha256().0
        );
        assert_ne!(baseline.receipt_sha256(), enabled_extra.receipt_sha256());

        let mut logon_attribute_drift = expected_enabled_groups();
        logon_attribute_drift
            .iter_mut()
            .find(|group| is_service_logon_sid(&group.sid))
            .unwrap()
            .attributes = SE_GROUP_ENABLED as u32;
        assert_eq!(
            CandidateRuntimeTokenObservation::from_observed(
                candidate,
                true,
                expected_token_principal(),
                logon_attribute_drift,
                expected_restricting_groups(),
                expected_token_privileges(),
            )
            .err()
            .unwrap()
            .code(),
            "authority_candidate_runtime_token_service_logon_sid_mismatch"
        );

        let mut duplicate_logon = expected_enabled_groups();
        duplicate_logon.push(ObservedTokenGroup {
            sid: sid_for_test("S-1-5-5-101-201"),
            attributes: SERVICE_LOGON_SID_ENABLED_ATTRIBUTES,
        });
        assert_eq!(
            CandidateRuntimeTokenObservation::from_observed(
                candidate,
                true,
                expected_token_principal(),
                duplicate_logon,
                expected_restricting_groups(),
                expected_token_privileges(),
            )
            .err()
            .unwrap()
            .code(),
            "authority_candidate_runtime_token_service_logon_sid_mismatch"
        );

        for missing_index in 0..expected_restricting_groups().len() {
            let mut missing = expected_restricting_groups();
            missing.remove(missing_index);
            assert_eq!(
                CandidateRuntimeTokenObservation::from_observed(
                    candidate,
                    true,
                    expected_token_principal(),
                    expected_enabled_groups(),
                    missing,
                    expected_token_privileges(),
                )
                .err()
                .unwrap()
                .code(),
                "authority_candidate_runtime_token_restricting_sid_set_mismatch",
                "missing restricting SID {missing_index}"
            );
        }

        for drift_principal in [
            {
                let mut value = expected_token_principal();
                value.user_sid = sid_for_test("S-1-5-19");
                value
            },
            {
                let mut value = expected_token_principal();
                value.user_attributes = SE_GROUP_ENABLED as u32;
                value
            },
            {
                let mut value = expected_token_principal();
                value.token_type = 2;
                value
            },
            {
                let mut value = expected_token_principal();
                value.session_id = 1;
                value
            },
            {
                let mut value = expected_token_principal();
                value.integrity_sid = sid_for_test("S-1-16-12288");
                value
            },
            {
                let mut value = expected_token_principal();
                value.integrity_attributes = SE_GROUP_INTEGRITY as u32;
                value
            },
            {
                let mut value = expected_token_principal();
                value.mandatory_policy = 0;
                value
            },
        ] {
            assert_eq!(
                CandidateRuntimeTokenObservation::from_observed(
                    candidate,
                    true,
                    drift_principal,
                    expected_enabled_groups(),
                    expected_restricting_groups(),
                    expected_token_privileges(),
                )
                .err()
                .unwrap()
                .code(),
                "authority_candidate_runtime_token_principal_mismatch"
            );
        }

        for (name, attributes) in [
            (AUTHORITY_REQUIRED_PRIVILEGES[0], 0x0000_0004),
            (
                AUTHORITY_REQUIRED_PRIVILEGES[0],
                SE_PRIVILEGE_ENABLED_BY_DEFAULT,
            ),
            (
                AUTHORITY_REQUIRED_PRIVILEGES[0],
                SE_PRIVILEGE_USED_FOR_ACCESS,
            ),
            (CHANGE_NOTIFY_PRIVILEGE, 0),
        ] {
            let mut privileges = expected_token_privileges();
            privileges
                .iter_mut()
                .find(|privilege| privilege.name == name)
                .unwrap()
                .attributes = attributes;
            assert_eq!(
                CandidateRuntimeTokenObservation::from_observed(
                    candidate,
                    true,
                    expected_token_principal(),
                    expected_enabled_groups(),
                    expected_restricting_groups(),
                    privileges,
                )
                .err()
                .unwrap()
                .code(),
                "authority_candidate_runtime_token_privileges_invalid"
            );
        }

        let mut extra = expected_token_privileges();
        extra.push(ObservedTokenPrivilege {
            name: "SeDebugPrivilege".to_string(),
            attributes: 0,
        });
        assert_eq!(
            CandidateRuntimeTokenObservation::from_observed(
                candidate,
                true,
                expected_token_principal(),
                expected_enabled_groups(),
                expected_restricting_groups(),
                extra,
            )
            .err()
            .unwrap()
            .code(),
            "authority_candidate_runtime_token_privileges_mismatch"
        );
        let mut missing = expected_token_privileges();
        missing.pop();
        assert_eq!(
            CandidateRuntimeTokenObservation::from_observed(
                candidate,
                true,
                expected_token_principal(),
                expected_enabled_groups(),
                expected_restricting_groups(),
                missing,
            )
            .err()
            .unwrap()
            .code(),
            "authority_candidate_runtime_token_privileges_mismatch"
        );
    }

    #[test]
    fn runtime_token_observation_is_bound_to_exact_process_generation() {
        let binding = binding();
        let candidate = CandidateProcessEvidence::from_static_image(
            1771,
            88_181,
            *binding.target_service_image(),
        )
        .unwrap();
        let drifted = CandidateProcessEvidence::from_static_image(
            1771,
            88_182,
            *binding.target_service_image(),
        )
        .unwrap();
        let baseline = CandidateRuntimeTokenObservation::from_observed(
            candidate,
            true,
            expected_token_principal(),
            expected_enabled_groups(),
            expected_restricting_groups(),
            expected_token_privileges(),
        )
        .unwrap();
        let other = CandidateRuntimeTokenObservation::from_observed(
            drifted,
            true,
            expected_token_principal(),
            expected_enabled_groups(),
            expected_restricting_groups(),
            expected_token_privileges(),
        )
        .unwrap();
        assert_ne!(baseline.receipt_sha256(), other.receipt_sha256());
        assert_eq!(
            baseline.require_process(drifted).unwrap_err().code(),
            "authority_candidate_runtime_token_process_mismatch"
        );
    }

    #[test]
    fn exact_service_identity_binds_configuration_process_image_and_native_token() {
        let binding = binding();
        let candidate = CandidateProcessEvidence::from_static_image(
            1771,
            88_181,
            *binding.target_service_image(),
        )
        .unwrap();
        let token = CandidateRuntimeTokenObservation::from_observed(
            candidate,
            true,
            expected_token_principal(),
            expected_enabled_groups(),
            expected_restricting_groups(),
            expected_token_privileges(),
        )
        .unwrap();
        let baseline = CandidateExactServiceIdentityObservation::from_native_readbacks(
            [0x71; 32], candidate, &token,
        )
        .unwrap();
        baseline.validate().unwrap();
        assert_eq!(baseline.exact_service_configuration_sha256(), [0x71; 32]);
        assert_eq!(baseline.candidate_service(), candidate);
        assert_eq!(
            baseline.runtime_token_receipt_sha256(),
            token.receipt_sha256()
        );
        assert_ne!(baseline.exact_service_identity_sha256(), [0; 32]);
        assert!(
            CandidateExactServiceIdentityObservation::for_test(
                [0x71; 32],
                candidate,
                token.receipt_sha256(),
            )
            .unwrap()
                == baseline
        );
        assert_eq!(
            CandidateExactServiceIdentityObservation::for_test(
                [0; 32],
                candidate,
                token.receipt_sha256(),
            )
            .err()
            .unwrap()
            .code(),
            "authority_candidate_exact_service_identity_invalid"
        );

        let configuration_drift = CandidateExactServiceIdentityObservation::from_native_readbacks(
            [0x72; 32], candidate, &token,
        )
        .unwrap();
        assert_ne!(
            baseline.exact_service_identity_sha256(),
            configuration_drift.exact_service_identity_sha256()
        );

        let token_drift = CandidateRuntimeTokenObservation::from_observed(
            candidate,
            true,
            expected_token_principal(),
            expected_enabled_groups(),
            expected_restricting_groups(),
            expected_token_privileges()
                .into_iter()
                .map(|mut privilege| {
                    if privilege.name != CHANGE_NOTIFY_PRIVILEGE {
                        privilege.attributes |= SE_PRIVILEGE_ENABLED;
                    }
                    privilege
                })
                .collect(),
        )
        .unwrap();
        let token_drift = CandidateExactServiceIdentityObservation::from_native_readbacks(
            [0x71; 32],
            candidate,
            &token_drift,
        )
        .unwrap();
        assert_ne!(
            baseline.exact_service_identity_sha256(),
            token_drift.exact_service_identity_sha256()
        );

        let mut tampered = baseline;
        tampered.exact_service_identity_sha256[0] ^= 1;
        assert_eq!(
            tampered.validate().unwrap_err().code(),
            "authority_candidate_exact_service_identity_mismatch"
        );
    }

    #[test]
    fn credential_readback_rejects_unbound_identity() {
        assert_eq!(
            CandidateCredentialReadback::from_verified_identity((0, [0x11; 16], [0x12; 32]))
                .err()
                .unwrap()
                .code(),
            "authority_candidate_start_credential_readback_invalid"
        );
        assert_eq!(
            CandidateCredentialReadback::from_verified_identity((1, [0; 16], [0x12; 32]))
                .err()
                .unwrap()
                .code(),
            "authority_candidate_start_credential_readback_invalid"
        );
        assert_eq!(
            CandidateCredentialReadback::from_verified_identity((1, [0x11; 16], [0; 32]))
                .err()
                .unwrap()
                .code(),
            "authority_candidate_start_credential_readback_invalid"
        );
    }

    #[test]
    fn static_image_type_remains_nonzero_and_exact() {
        let binding = binding();
        let image: CandidateImageEvidence = *binding.target_service_image();
        image.validate().unwrap();
        assert_ne!(image.image_sha256(), &[0; 32]);
    }
}
